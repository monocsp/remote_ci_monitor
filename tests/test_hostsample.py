"""호스트 샘플러 — 가짜 runner/read_file 로 macOS·Linux 경로 · 부분/전부 실패 · gpu off · history.

실제 subprocess 는 안 부른다. 예외는 맨 아래 Linux 스모크 하나(CI 의 ubuntu 러너에서만 돈다).
Linux 경로는 /proc/stat 을 1초 간격으로 두 번 읽으므로 그 테스트들은 각각 1초쯤 걸린다.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.config import HostSection
from remote_ci_monitor.core.model import HostSample
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.hostsample import HostSampler

FIXTURES = Path(__file__).parent / "fixtures" / "host"
NOW = datetime(2026, 9, 5, 1, 1, 46, tzinfo=UTC)
LOAD = (5.42, 5.06, 3.45)
MAC_CPU = {"user": 39.82, "sys": 14.33, "idle": 45.84, "busy": 54.16}
MAC_MEMORY = {"total_bytes": 25769803776, "used_bytes": 17550622720, "compressed_bytes": 7695122432}
MAC_GPU = {"util_pct": 1, "mem_used_bytes": 27443200, "mem_total_bytes": None, "source": "ioreg"}
LINUX_MEMORY = {"total_bytes": 16672210944, "used_bytes": 6601457664, "compressed_bytes": None}
LINUX_GPU = {
    "util_pct": 13,
    "mem_used_bytes": 594 * 2**20,
    "mem_total_bytes": 8192 * 2**20,
    "source": "nvidia-smi",
}
MAC_COMMANDS = {
    "vm_stat": "macos/vm_stat.txt",
    "sysctl": "macos/sysctl_hw_memsize.txt",
    "top": "macos/top_l2.txt",
    "ps": "macos/ps_Aro.txt",
    "ioreg": "macos/ioreg_IOAccelerator.txt",
}
LINUX_COMMANDS = {"ps": "linux/ps_eo.txt", "nvidia-smi": "linux/nvidia_smi.txt"}
LINUX_FILES = {
    "/proc/loadavg": ["linux/proc_loadavg.txt"],
    "/proc/meminfo": ["linux/proc_meminfo.txt"],
    "/proc/stat": ["linux/proc_stat_1.txt", "linux/proc_stat_2.txt"],
}


class FakeRunner:
    """argv[0] 의 basename 으로 픽스처를 고른다. `fail` 에 들었거나 표에 없으면 None(실행 실패)."""

    def __init__(self, table: dict[str, str], fail: set[str] | None = None):
        self.table = table
        self.fail = set(fail or ())
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str | None:
        self.calls.append(list(argv))
        cmd = Path(argv[0]).name
        if cmd in self.fail or cmd not in self.table:
            return None
        return (FIXTURES / self.table[cmd]).read_text()

    def commands(self) -> list[str]:
        return [Path(a[0]).name for a in self.calls]


class FakeFiles:
    """경로 → 픽스처 목록. 거듭 읽으면 순서대로 주고 마지막 것을 반복한다(/proc/stat 두 표본)."""

    def __init__(self, table: dict[str, list[str]], fail: set[str] | None = None):
        self.table = {k: list(v) for k, v in table.items()}
        self.fail = set(fail or ())
        self.calls: list[str] = []

    def __call__(self, path: str) -> str | None:
        key = str(path)
        self.calls.append(key)
        seq = self.table.get(key)
        if key in self.fail or not seq:
            return None
        name = seq.pop(0) if len(seq) > 1 else seq[0]
        return (FIXTURES / name).read_text()


class Clock:
    def __init__(self, start: datetime = NOW):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FlakyLoad:
    """`fail` 이면 None — runner 와 같은 「실패는 None」 규약(예외는 샘플러 기본 래퍼가 삼킨다)."""

    def __init__(self):
        self.fail = False

    def __call__(self) -> tuple[float, float, float] | None:
        return None if self.fail else LOAD


def section(**kw) -> HostSection:
    base = {"interval_seconds": 2, "gpu": "auto", "top_processes": 5, "history_samples": 3}
    return HostSection(**{**base, **kw})


def make(platform: str = "darwin", *, config=None, runner=None, files=None, clock=None, **kw):
    if runner is None:
        runner = FakeRunner(MAC_COMMANDS if platform == "darwin" else LINUX_COMMANDS)
    if files is None:
        files = FakeFiles(LINUX_FILES if platform == "linux" else {})
    args = {
        "name": "macmini",
        "publish": None,
        "stop": threading.Event(),
        "now_fn": clock or Clock(),
        "runner": runner,
        "read_file": files,
        "platform": platform,
        "cpu_count": 10 if platform == "darwin" else 8,
        "loadavg": (lambda: LOAD) if platform == "darwin" else None,
    }
    return HostSampler(config or section(), **{**args, **kw})


# ── macOS 경로 ────────────────────────────────────────────────────────────────


def test_macos_sample_once_fills_every_field_from_fixtures():
    runner = FakeRunner(MAC_COMMANDS)
    s = make(runner=runner)
    sample = s.sample_once()
    assert isinstance(sample, HostSample)
    assert sample.name == "macmini" and sample.source == "local"
    assert sample.os == "darwin" and sample.cores == 10
    assert sample.sampled_at == NOW and sample.interval_seconds == 2
    assert sample.load == LOAD
    assert sample.cpu == MAC_CPU
    assert sample.memory == MAC_MEMORY
    assert sample.gpu == MAC_GPU and sample.gpu_note is None
    assert len(sample.top) == 5  # top_processes
    assert sample.top[0] == {"comm": "dartaotruntime", "cpu": 74.6, "rss_mb": 2881}
    hosts, err = s.latest()
    assert list(hosts) == [sample] and err is None
    assert set(runner.commands()) == {"vm_stat", "sysctl", "top", "ps", "ioreg"}


def test_macos_ioreg_failure_blanks_gpu_only():
    s = make(runner=FakeRunner(MAC_COMMANDS, fail={"ioreg"}))
    sample = s.sample_once()
    assert sample is not None
    assert sample.gpu is None
    assert isinstance(sample.gpu_note, str) and sample.gpu_note
    assert sample.cpu == MAC_CPU and sample.memory == MAC_MEMORY and sample.load == LOAD
    assert s.latest()[1] is None  # 부분 실패는 hosts_error 가 아니다


def test_macos_top_failure_blanks_cpu_only():
    sample = make(runner=FakeRunner(MAC_COMMANDS, fail={"top"})).sample_once()
    assert sample is not None and sample.cpu is None
    assert sample.memory == MAC_MEMORY and sample.load == LOAD and sample.gpu == MAC_GPU


def test_macos_vm_stat_failure_keeps_total_bytes():
    sample = make(runner=FakeRunner(MAC_COMMANDS, fail={"vm_stat"})).sample_once()
    assert sample is not None
    assert sample.memory == {
        "total_bytes": 25769803776,
        "used_bytes": None,
        "compressed_bytes": None,
    }
    assert sample.cpu == MAC_CPU


def test_macos_ps_failure_gives_empty_top():
    sample = make(runner=FakeRunner(MAC_COMMANDS, fail={"ps"})).sample_once()
    assert sample is not None and tuple(sample.top) == ()
    assert sample.cpu == MAC_CPU


def test_loadavg_failure_does_not_kill_the_sample():
    load = FlakyLoad()
    load.fail = True
    sample = make(loadavg=load).sample_once()
    assert sample is not None
    assert sample.load is None or sample.load == LOAD  # top 의 "Load Avg" 로 폴백해도 된다
    assert sample.cpu == MAC_CPU and sample.memory == MAC_MEMORY


def test_all_collectors_failed_gives_no_sample_and_hosts_error():
    s = make(runner=FakeRunner({}), loadavg=None)
    assert s.sample_once() is None
    assert isinstance(s.error, str) and s.error
    hosts, err = s.latest()
    assert list(hosts) == []
    assert err is not None and err.startswith("sampler: all collectors failed")


@pytest.mark.parametrize("platform, probe", [("darwin", "ioreg"), ("linux", "nvidia-smi")])
def test_gpu_off_skips_probe_and_notes_disabled(platform, probe):
    runner = FakeRunner(MAC_COMMANDS if platform == "darwin" else LINUX_COMMANDS)
    sample = make(platform, config=section(gpu="off"), runner=runner).sample_once()
    assert sample is not None
    assert sample.gpu is None and sample.gpu_note == "disabled"
    assert probe not in runner.commands()
    assert sample.cpu is not None and sample.cpu["busy"] is not None


# ── Linux 경로 ────────────────────────────────────────────────────────────────


def test_linux_sample_once_reads_proc_twice_and_nvidia_smi():
    runner = FakeRunner(LINUX_COMMANDS)
    files = FakeFiles(LINUX_FILES)
    sample = make("linux", runner=runner, files=files).sample_once()
    assert sample is not None
    assert sample.os == "linux" and sample.cores == 8
    assert sample.load == (0.58, 0.71, 0.83)  # loadavg=None 이므로 /proc/loadavg 에서만 온다
    assert sample.cpu["user"] == pytest.approx(41.0)
    assert sample.cpu["sys"] == pytest.approx(17.0)
    assert sample.cpu["idle"] == pytest.approx(42.0)
    assert sample.cpu["busy"] == pytest.approx(58.0)
    assert sample.memory == LINUX_MEMORY
    assert sample.gpu == LINUX_GPU and sample.gpu_note is None
    assert [t["comm"] for t in sample.top] == ["dart", "flutter_tester", "java", "node", "gradle"]
    assert files.calls.count("/proc/stat") == 2
    assert "/proc/meminfo" in files.calls and "/proc/loadavg" in files.calls
    cmds = runner.commands()
    assert "ps" in cmds and "nvidia-smi" in cmds
    assert not {"vm_stat", "sysctl", "top", "ioreg"} & set(cmds)


def test_linux_nvidia_smi_missing_notes_not_found():
    sample = make("linux", runner=FakeRunner({"ps": "linux/ps_eo.txt"})).sample_once()
    assert sample is not None
    assert sample.gpu is None and sample.gpu_note == "nvidia-smi not found"
    assert sample.cpu is not None and sample.memory == LINUX_MEMORY


def test_linux_nvidia_smi_error_text_is_not_a_gpu():
    runner = FakeRunner({"ps": "linux/ps_eo.txt", "nvidia-smi": "linux/nvidia_smi_error.txt"})
    sample = make("linux", runner=runner).sample_once()
    assert sample is not None
    assert sample.gpu is None
    assert isinstance(sample.gpu_note, str) and sample.gpu_note


def test_linux_meminfo_failure_blanks_memory_only():
    files = FakeFiles(LINUX_FILES, fail={"/proc/meminfo"})
    sample = make("linux", files=files).sample_once()
    assert sample is not None
    assert sample.memory is None or all(v is None for v in sample.memory.values())
    assert sample.load == (0.58, 0.71, 0.83) and sample.cpu["busy"] == pytest.approx(58.0)


# ── history · publish · run 루프 ──────────────────────────────────────────────


def test_history_first_sample_has_one_entry_of_itself():
    sample = make().sample_once()
    assert sample is not None
    assert tuple(sample.history) == (
        {"at": iso(NOW), "cpu_busy": 54.16, "mem_used_bytes": 17550622720, "gpu_util_pct": 1},
    )


def test_history_is_capped_and_failed_cycles_add_nothing():
    clock = Clock()
    runner = FakeRunner(MAC_COMMANDS)
    load = FlakyLoad()
    s = make(runner=runner, clock=clock, loadavg=load)
    stamps = []
    for _ in range(4):  # history_samples = 3
        assert s.sample_once() is not None
        stamps.append(iso(clock.now))
        clock.advance(2)
    sample = s.latest()[0][0]
    assert [h["at"] for h in sample.history] == stamps[1:]  # 가장 오래된 것이 밀려났다
    assert all(
        set(h) == {"at", "cpu_busy", "mem_used_bytes", "gpu_util_pct"} for h in sample.history
    )
    # 전부 실패한 주기 — 표본도 항목도 없다
    runner.fail = set(MAC_COMMANDS)
    load.fail = True
    failed_at = iso(clock.now)
    assert s.sample_once() is None
    clock.advance(2)
    runner.fail = set()
    load.fail = False
    assert s.sample_once() is not None
    ats = [h["at"] for h in s.latest()[0][0].history]
    assert ats == [stamps[2], stamps[3], iso(clock.now)]
    assert failed_at not in ats


def test_history_entries_carry_none_for_missing_fields():
    sample = make(runner=FakeRunner(MAC_COMMANDS, fail={"ioreg", "top"})).sample_once()
    assert sample is not None
    entry = sample.history[-1]
    assert entry["cpu_busy"] is None and entry["gpu_util_pct"] is None
    assert entry["mem_used_bytes"] == 17550622720


def test_run_loop_samples_publishes_and_stops():
    stop = threading.Event()
    events: list[tuple[str, dict]] = []
    lock = threading.Lock()

    def publish(kind: str, data: dict) -> None:
        with lock:
            events.append((kind, dict(data)))

    s = make(publish=publish, stop=stop)
    s.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with lock:
                if events and s.latest()[0]:
                    break
            time.sleep(0.05)
    finally:
        stop.set()
        s.join(timeout=5)
    assert not s.is_alive()
    hosts, err = s.latest()
    assert len(hosts) == 1 and err is None
    kind, data = events[0]
    assert kind == "host_sample"
    assert data["name"] == "macmini"
    assert data["sampled_at"] in (iso(NOW), NOW)  # JSON 으로 나가는 값이라 iso 가 맞다


# ── 스모크 (CI 의 ubuntu 러너) ────────────────────────────────────────────────


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads the real /proc")
def test_linux_smoke_reads_real_proc():
    s = HostSampler(section(), name="ci", publish=None, stop=threading.Event())
    sample = s.sample_once()
    assert sample is not None
    assert sample.os == "linux"
    assert sample.load is not None and len(sample.load) == 3
    assert sample.memory is not None and sample.memory["total_bytes"] > 0
    assert sample.cpu is not None and 0 <= sample.cpu["busy"] <= 100


def test_safe_loadavg_rounds_to_two_decimals(monkeypatch):
    """macOS 의 os.getloadavg() 는 6.60693359375 같은 이진 소수를 준다 — 그대로 싣지 않는다."""
    import os

    from remote_ci_monitor.hostsample import _safe_loadavg

    monkeypatch.setattr(os, "getloadavg", lambda: (6.60693359375, 5.7744140625, 3.357421875))
    assert _safe_loadavg() == (6.61, 5.77, 3.36)

    def boom():
        raise OSError("no loadavg")

    monkeypatch.setattr(os, "getloadavg", boom)
    assert _safe_loadavg() is None
