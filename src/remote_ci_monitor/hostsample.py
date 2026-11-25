"""호스트 자원 샘플러 — 서버 프로세스 안의 스레드. 명령·파일을 읽어 `core/hostparse` 에 넘긴다.

- macOS: `vm_stat` · `sysctl -n hw.memsize` · `top -l 2 -n 0 -s 1`(두 번째 표본만) ·
  `ps -Aro %cpu=,rss=,comm=` · `ioreg -r -d 1 -w 0 -c IOAccelerator`(gpu = auto 일 때만).
- Linux: `/proc/loadavg` · `/proc/meminfo` · `/proc/stat` 두 번(1초 차분) ·
  `ps -eo %cpu=,rss=,comm= --sort=-%cpu` · `nvidia-smi …`(있을 때만).
- 부분 실패는 그 칸만 None. cpu·memory·load 가 전부 None 이면 표본을 만들지 않고 `hosts_error`.
- 마지막 표본 하나 + `history`(deque, `history_samples`) 를 메모리에 둔다. 재시작하면 비운다.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from remote_ci_monitor.config import HostSection
from remote_ci_monitor.core.hostparse import (
    mac_memory,
    parse_ioreg_gpu,
    parse_nvidia_smi,
    parse_proc_loadavg,
    parse_proc_meminfo,
    parse_proc_stat_cpu,
    parse_ps,
    parse_sysctl_int,
    parse_top_cpu,
    parse_top_load,
    parse_vm_stat,
)
from remote_ci_monitor.core.model import HostSample
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.runner import tool_blocked

COMMAND_TIMEOUT = 8.0
PROC_STAT_DELTA_SECONDS = 1.0
EVENT_HOST_SAMPLE = "host_sample"

MAC_VM_STAT = ["vm_stat"]
MAC_MEMSIZE = ["sysctl", "-n", "hw.memsize"]
MAC_TOP = ["top", "-l", "2", "-n", "0", "-s", "1"]
MAC_PS = ["ps", "-Aro", "%cpu=,rss=,comm="]
#: GPU 표본이 없는 이유(결정 37). 화면이 「GPU — 없음」을 자기 말로 쓴다.
GPU_NO_SAMPLER = "no_sampler"
GPU_NO_GPU = "no_gpu"
GPU_SAMPLER_FAILED = "sampler_failed"

MAC_IOREG = ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"]
LINUX_PS = ["ps", "-eo", "%cpu=,rss=,comm=", "--sort=-%cpu"]
LINUX_NVIDIA = [
    "nvidia-smi",
    "--query-gpu=utilization.gpu,memory.used,memory.total",
    "--format=csv,noheader,nounits",
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _run_command(argv: list[str]) -> str | None:
    """명령의 stdout. 없거나 실패하면 None(호출자가 그 칸을 None 으로 둔다)."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


# ── 머신의 신원 · 지문 (M5n §3 · §4.1) ──────────────────────────────────────

MAC_PLATFORM = ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]
LINUX_MACHINE_ID = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def hardware_id() -> str | None:
    """이 컴퓨터의 하드웨어 신원 원문(macOS `IOPlatformUUID` · Linux `machine-id`). 못 읽으면 None.

    원문은 이 프로세스 밖으로 나가지 않는다 — 호출자가 `capability.host_key_from` 으로 해시한다.
    """
    if sys.platform == "darwin":
        out = _run_command(MAC_PLATFORM)
        for line in (out or "").splitlines():
            if '"IOPlatformUUID"' in line and "=" in line:
                value = line.split("=", 1)[1].strip().strip('"')
                return value or None
        return None
    for path in LINUX_MACHINE_ID:
        text = _read_file(path)
        if text and text.strip():
            return text.strip()
    return None


HOST_FACT_COMMANDS: dict[str, list[str]] = {}


def host_facts(**_kw: Any) -> dict[str, Any]:
    keys = ("os", "os_release", "os_like", "arch", "machine", "rosetta", "container", "root")
    return dict.fromkeys(keys + ("package_manager", "python"))


def run_probes(probes: Any, env: dict[str, str]) -> dict[str, Any]:
    """`[[probes]]` 를 **잡과 같은 환경**(서비스의 PATH)에서 돌린다 → 이름 → ProbeResult(설정 순서).

    셸은 없다. 실행 파일은 `env` 의 PATH 로 찾는다 — 이 프로세스의 PATH 로 물러나지 않는다.

    **함께 돌린다.** 차례로 돌면 시간이 더해진다 — 64개 × 60초면 한 시간이고, 그동안 서버 기동 ·
    `rcm reload`(요청 스레드) · SIGHUP(받는 루프) 가 멈춘다. 함께 돌면 가장 긴 시한 하나(≤ 60초)다.
    """
    probes = list(probes)
    results: dict[str, Any] = {}
    threads = [
        threading.Thread(
            target=lambda p=probe: results.__setitem__(p.name, _run_probe(p, env)),
            name=f"rcm-probe-{probe.name}",
            daemon=True,
        )
        for probe in probes
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return {p.name: results[p.name] for p in probes}


#: 지금 도는 프로브 프로세스 — 워커가 exec 로 다시 실행하기 직전에 끝낸다(M5q §14 B질문13)
_LIVE_PROBES: set[subprocess.Popen[bytes]] = set()
_LIVE_LOCK = threading.Lock()
#: 참이면 새 프로브를 띄우지 않는다 — `kill_probes` 뒤에는 곧 exec 한다
_PROBES_CLOSED = False


def kill_probes() -> int:
    """도는 프로브를 그룹째 끝내고 그 뒤로는 새 프로브를 띄우지 않는다. 끝낸 수를 돌려준다.
    시작과 등록은 같은 잠금 안이라 그 사이에 끼인 프로브가 빠지지 않는다."""
    global _PROBES_CLOSED
    with _LIVE_LOCK:
        _PROBES_CLOSED = True
        procs = list(_LIVE_PROBES)
    for proc in procs:
        _kill_group(proc)
    return len(procs)


def _run_probe(probe: Any, env: dict[str, str]) -> Any:
    from remote_ci_monitor.core.capability import normalize_probe_output

    path = env.get("PATH", "")
    argv = list(probe.argv)
    exe = argv[0]
    if not os.path.isabs(exe) and shutil.which(exe, path=path) is None:
        return normalize_probe_output(None, None, missing=True)
    if os.path.isabs(exe) and not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        return normalize_probe_output(None, None, missing=True)
    if not os.path.isabs(exe):
        argv[0] = shutil.which(exe, path=path) or exe
    if tool_blocked(argv[0]):  # CLT 없는 macOS 의 shim — 띄우면 설치 대화상자가 뜬다(결정 246)
        return normalize_probe_output(None, None, missing=True)
    with _LIVE_LOCK:
        if _PROBES_CLOSED:  # 곧 exec 한다 — 띄우지 않는다
            return normalize_probe_output(None, None, timed_out=True)
        try:
            # 자기 프로세스 그룹 — 시한을 넘기면 손자까지 거둔다(`runner` 의 잡과 같은 규칙).
            # 감싸는 스크립트(`flutter` · `xcrun` 류)를 죽여도 그 자식은 남아 주기마다 쌓였다.
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError:
            return normalize_probe_output(None, None, missing=True)
        _LIVE_PROBES.add(proc)
    try:
        return _wait_probe(proc, probe)
    finally:
        with _LIVE_LOCK:
            _LIVE_PROBES.discard(proc)


def _wait_probe(proc: subprocess.Popen[bytes], probe: Any) -> Any:
    from remote_ci_monitor.core.capability import normalize_probe_output

    # 출력은 **앞부분만** 지닌다 — 지문에 드는 것은 앞 두 줄 · 120자다. communicate 로 다 담으면
    # `yes` 같은 프로브 하나가 3초에 10 GB 를 먹는다(엣지 검증 실측). 나머지는 읽어서 버린다 —
    # 안 읽으면 파이프가 차서 정상 프로브(큰 목록을 찍는 것)가 시한까지 멈춘다.
    timeout = float(probe.timeout_seconds)
    deadline = time.monotonic() + timeout
    kept = (bytearray(), bytearray())
    readers = [
        threading.Thread(target=_drain, args=(stream, buf), daemon=True)
        for stream, buf in zip((proc.stdout, proc.stderr), kept, strict=True)
    ]
    for r in readers:
        r.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.wait()
        for r in readers:
            r.join(1.0)
        return normalize_probe_output(None, None, timed_out=True)
    # 우두머리는 끝났다 — 그룹의 손자가 파이프를 쥐고 있으면 시한까지 기다린다(communicate 와 같다)
    for r in readers:
        r.join(max(0.0, deadline - time.monotonic()))
    if any(r.is_alive() for r in readers):
        _kill_group(proc)
        for r in readers:
            r.join(1.0)
        return normalize_probe_output(None, None, timed_out=True)
    stdout, stderr = bytes(kept[0]), bytes(kept[1])
    output = stdout if stdout.strip() else stderr
    return normalize_probe_output(output, proc.returncode)


#: 프로브 출력 중 지니는 앞부분(스트림마다). 지문은 앞 두 줄 · 120자만 쓴다.
PROBE_OUTPUT_KEEP = 64 * 1024


def _drain(stream: Any, keep: bytearray) -> None:
    """EOF 까지 읽되 앞 `PROBE_OUTPUT_KEEP` 바이트만 남긴다."""
    try:
        while True:
            chunk = stream.read1(65536)
            if not chunk:
                break
            room = PROBE_OUTPUT_KEEP - len(keep)
            if room > 0:
                keep += chunk[:room]
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _read_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _disk_usage(path: str | None) -> dict[str, Any] | None:
    """그 경로가 앉은 파일 시스템의 사용량. 잡이 쓰는 공간이라 데이터 디렉터리를 본다.

    못 읽으면 None — 표본의 다른 칸은 그대로 산다(부분 실패는 그 칸만 None).
    """
    if not path:
        return None
    try:
        u = shutil.disk_usage(path)
    except (OSError, ValueError):
        return None
    return {
        "used_bytes": int(u.used),
        "free_bytes": int(u.free),
        "total_bytes": int(u.total),
        "path": str(path),
    }


def _safe_loadavg() -> tuple[float, float, float] | None:
    """`os.getloadavg()` 를 두 자리로 — 이진 소수(6.60693359375)를 JSON 에 그대로 싣지 않는다."""
    try:
        a, b, c = os.getloadavg()
        return (round(a, 2), round(b, 2), round(c, 2))
    except (OSError, AttributeError):
        return None


class HostSampler(threading.Thread):
    """`interval_seconds` 마다 표본 하나. `latest()` 가 (hosts, hosts_error) 를 돌려준다."""

    def __init__(
        self,
        config: HostSection,
        *,
        name: str,
        publish: Callable[[str, dict[str, Any]], None] | None = None,
        stop: threading.Event | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
        runner: Callable[[list[str]], str | None] = _run_command,
        read_file: Callable[[str], str | None] = _read_file,
        platform: str = sys.platform,
        cpu_count: int | None = None,
        loadavg: Callable[[], tuple[float, float, float] | None] | None = _safe_loadavg,
        sleep: Callable[[float], None] | None = None,
        disk_path: str | None = None,
        disk_usage: Callable[[str | None], dict[str, Any] | None] = _disk_usage,
    ):
        super().__init__(name="rcm-hostsample", daemon=True)
        self.config = config
        self.host_name = name
        self.publish = publish
        self.stop_event = stop or threading.Event()
        self.now_fn = now_fn
        self.runner = runner
        self.read_file = read_file
        self.platform = platform
        self.cpu_count = cpu_count if cpu_count is not None else os.cpu_count()
        self.loadavg = loadavg
        # 잡이 쓰는 디스크. 데이터 디렉터리가 앉은 파일 시스템을 본다.
        self.disk_path = disk_path
        self.disk_usage = disk_usage
        # 기본은 stop 이벤트를 기다리는 sleep — Linux 1초 차분 중에도 shutdown 이 안 늦어진다
        self.sleep = sleep or (lambda seconds: self.stop_event.wait(seconds) and None)
        self._lock = threading.Lock()
        self._latest: HostSample | None = None
        self._history: deque[dict[str, Any]] = deque(maxlen=max(1, config.history_samples))
        self.error: str | None = None

    # ── 조회 ────────────────────────────────────────────────────────────────

    def latest(self) -> tuple[list[HostSample], str | None]:
        with self._lock:
            if self._latest is None:
                return [], self.error
            return [self._latest], None

    @property
    def os_name(self) -> str:
        if self.platform.startswith("darwin"):
            return "darwin"
        if self.platform.startswith("linux"):
            return "linux"
        return self.platform

    # ── 수집 ────────────────────────────────────────────────────────────────

    def _collect_mac(self) -> dict[str, Any]:
        vm = parse_vm_stat(self.runner(MAC_VM_STAT) or "")
        total = parse_sysctl_int(self.runner(MAC_MEMSIZE) or "")
        top_text = self.runner(MAC_TOP) or ""
        cpu = parse_top_cpu(top_text)
        load = (self.loadavg() if self.loadavg else None) or parse_top_load(top_text)
        top = parse_ps(self.runner(MAC_PS) or "", self.config.top_processes)
        gpu: dict[str, Any] | None = None
        note: str | None = None
        code: str | None = None
        if self.config.gpu == "off":
            note, code = "disabled", GPU_NO_GPU
        else:
            gpu, note = parse_ioreg_gpu(self.runner(MAC_IOREG) or "")
            code = None if gpu is not None else GPU_SAMPLER_FAILED
        return {
            "cpu": cpu,
            "memory": mac_memory(vm, total),
            "load": load,
            "top": top,
            "gpu": gpu,
            "gpu_note": note,
            "gpu_note_code": code,
        }

    def _collect_linux(self) -> dict[str, Any]:
        load_text = self.read_file("/proc/loadavg") or ""
        load = parse_proc_loadavg(load_text) or (self.loadavg() if self.loadavg else None)
        memory = parse_proc_meminfo(self.read_file("/proc/meminfo") or "")
        first = self.read_file("/proc/stat") or ""
        if first:
            self.sleep(PROC_STAT_DELTA_SECONDS)
        second = self.read_file("/proc/stat") or ""
        cpu = parse_proc_stat_cpu(first, second) if first and second else None
        top = parse_ps(self.runner(LINUX_PS) or "", self.config.top_processes)
        gpu: dict[str, Any] | None = None
        note: str | None = None
        code: str | None = None
        if self.config.gpu == "off":
            note, code = "disabled", GPU_NO_GPU
        else:
            out = self.runner(LINUX_NVIDIA)
            if out is None:
                note, code = "nvidia-smi not found", GPU_NO_SAMPLER
            else:
                gpu = parse_nvidia_smi(out)
                if gpu is None:
                    note, code = "nvidia-smi returned no usable data", GPU_SAMPLER_FAILED
        return {
            "cpu": cpu,
            "memory": memory,
            "load": load,
            "top": top,
            "gpu": gpu,
            "gpu_note": note,
            "gpu_note_code": code,
        }

    def sample_once(self) -> HostSample | None:
        """한 번 수집해 latest 와 history 를 갱신한다. 전부 실패면 None + `self.error`."""
        try:
            if self.os_name == "darwin":
                raw = self._collect_mac()
            elif self.os_name == "linux":
                raw = self._collect_linux()
            else:
                self._set_error(f"sampler: unsupported platform {self.platform}")
                return None
        except Exception as e:  # noqa: BLE001 — 수집기 예외는 표본 실패로만 남긴다
            self._set_error(f"sampler: {type(e).__name__}")
            return None
        memory = raw["memory"] or {}
        core_values = [raw["cpu"], memory.get("used_bytes"), raw["load"]]
        if all(v is None for v in core_values):
            self._set_error("sampler: all collectors failed (cpu, memory, load are unknown)")
            return None
        now = self.now_fn()
        # 잠금 밖에서 잰다 — 멈춘 마운트에서 syscall 이 걸려도 `latest()` 가 같이 서면 안 된다
        disk = self.disk_usage(self.disk_path)
        entry = {
            "at": iso(now),
            "cpu_busy": (raw["cpu"] or {}).get("busy"),
            "mem_used_bytes": memory.get("used_bytes"),
            "gpu_util_pct": (raw["gpu"] or {}).get("util_pct"),
        }
        with self._lock:
            self._history.append(entry)
            sample = HostSample(
                name=self.host_name,
                source="local",
                sampled_at=now,
                interval_seconds=float(self.config.interval_seconds),
                os=self.os_name,
                cores=self.cpu_count,
                load=raw["load"],
                cpu=raw["cpu"],
                memory=memory,
                gpu=raw["gpu"],
                disk=disk,
                gpu_note=raw["gpu_note"],
                gpu_note_code=raw.get("gpu_note_code"),
                top=tuple(raw["top"]),
                history=tuple(self._history),
            )
            self._latest = sample
            self.error = None
        if self.publish is not None:
            try:
                self.publish(EVENT_HOST_SAMPLE, {"name": self.host_name, "sampled_at": iso(now)})
            except Exception:  # noqa: BLE001 — 발행 실패가 샘플러를 죽이면 안 된다
                pass
        return sample

    def _set_error(self, msg: str) -> None:
        with self._lock:
            self.error = msg

    def run(self) -> None:
        interval = max(2.0, float(self.config.interval_seconds))
        while not self.stop_event.is_set():
            started = time.monotonic()
            self.sample_once()
            took = time.monotonic() - started
            if self.stop_event.wait(max(0.5, interval - took)):
                break
