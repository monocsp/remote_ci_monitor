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

COMMAND_TIMEOUT = 8.0
PROC_STAT_DELTA_SECONDS = 1.0
EVENT_HOST_SAMPLE = "host_sample"

MAC_VM_STAT = ["vm_stat"]
MAC_MEMSIZE = ["sysctl", "-n", "hw.memsize"]
MAC_TOP = ["top", "-l", "2", "-n", "0", "-s", "1"]
MAC_PS = ["ps", "-Aro", "%cpu=,rss=,comm="]
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


def _read_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


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
        if self.config.gpu == "off":
            note = "disabled"
        else:
            gpu, note = parse_ioreg_gpu(self.runner(MAC_IOREG) or "")
        return {
            "cpu": cpu,
            "memory": mac_memory(vm, total),
            "load": load,
            "top": top,
            "gpu": gpu,
            "gpu_note": note,
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
        if self.config.gpu == "off":
            note = "disabled"
        else:
            out = self.runner(LINUX_NVIDIA)
            if out is None:
                note = "nvidia-smi not found"
            else:
                gpu = parse_nvidia_smi(out)
                if gpu is None:
                    note = "nvidia-smi returned no usable data"
        return {
            "cpu": cpu,
            "memory": memory,
            "load": load,
            "top": top,
            "gpu": gpu,
            "gpu_note": note,
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
                gpu_note=raw["gpu_note"],
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
