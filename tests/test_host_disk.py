"""호스트 디스크(§4.6 (가)) + 원격 워커 표본 파서의 버전 내성(§4.6 (나)).

명세는 docs/m5d-workplan.md §4.6. 구현 전이라 빨간 것이 정상이다.

- (가) 잡이 쓰는 파일 시스템의 사용량을 표본에 싣는다. 루트 파티션이 아니라 데이터 디렉터리다.
  못 읽으면 그 칸만 `None` 이고 표본의 나머지는 산다.
- (나) `sample_from_json` 은 **모르는 최상위 키를 조용히 버린다**. M5d-0 이 `gpu_note_code` 를
  더하면서 파서를 안 고쳐 표본이 통째로 버려졌다 — 워커·서버 버전이 어긋나도 호스트 칸이 살아
  있으려면 이 규칙이 필요하다. 값 검사 강도는 그대로다(중첩 키는 아는 것만).
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from remote_ci_monitor.core.hostparse import sample_from_json
from remote_ci_monitor.core.model import HostSample
from remote_ci_monitor.core.status import host_json, iso
from remote_ci_monitor.hostsample import _disk_usage
from test_hostsample import LINUX_GPU, MAC_CPU, MAC_GPU, MAC_MEMORY, NOW, make

DISK_KEYS = frozenset({"used_bytes", "free_bytes", "total_bytes", "path"})
DISK: dict[str, Any] = {
    "used_bytes": 120 * 10**9,
    "free_bytes": 340 * 10**9,
    "total_bytes": 460 * 10**9,
    "path": "/var/lib/rcm",
}
#: 서버가 정하는 키 — 워커 payload 의 값은 쓰지 않는다.
SERVER_KEYS = frozenset({"name", "source", "sampled_at", "age_seconds", "stale"})


class FakeDisk:
    """주입한 값을 그대로 주고 어떤 경로로 불렸는지 적어 둔다(실제 파일 시스템을 안 본다)."""

    def __init__(self, value: dict[str, Any] | None):
        self.value = value
        self.calls: list[str | None] = []

    def __call__(self, path: str | None) -> dict[str, Any] | None:
        self.calls.append(path)
        return self.value


# ── (가) 표본의 디스크 칸 ─────────────────────────────────────────────────────


def test_host_sample_disk_defaults_to_none():
    """옛 호출부는 `disk` 를 모른다 — 인자를 안 줘도 표본이 만들어진다."""
    sample = HostSample(name="macmini", source="local", sampled_at=NOW, interval_seconds=5)
    assert sample.disk is None


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_the_sampler_carries_the_disk_reading_it_was_given(platform):
    """수집기를 주입해 표본의 `disk` 가 그 값이 되는지 본다 — macOS·Linux 경로 둘 다."""
    fake = FakeDisk(DISK)
    sampler = make(platform, disk_path=DISK["path"], disk_usage=fake, sleep=lambda _s: None)
    sample = sampler.sample_once()
    assert sample is not None
    assert sample.disk == DISK
    assert fake.calls == [DISK["path"]]  # 설정한 경로 그대로 — 루트가 아니다
    assert sample.cpu is not None and sample.memory is not None and sample.load is not None


@pytest.mark.parametrize(("platform", "gpu"), [("darwin", MAC_GPU), ("linux", LINUX_GPU)])
def test_without_a_disk_path_the_rest_of_the_sample_is_untouched(platform, gpu):
    """`disk_path=None` 이면 디스크 칸만 비고 cpu·memory·gpu 는 그대로 산다."""
    sample = make(platform, sleep=lambda _s: None).sample_once()
    assert sample is not None
    assert sample.disk is None
    assert sample.cpu is not None and sample.cpu["busy"] is not None
    assert sample.memory is not None and sample.memory["used_bytes"] is not None
    assert sample.gpu == gpu


def test_a_disk_reading_that_failed_blanks_only_that_field():
    """못 읽으면 그 칸만 `None` — 부분 실패는 표본 전체를 죽이지 않는다."""
    fake = FakeDisk(None)
    sampler = make(disk_path="/var/lib/rcm", disk_usage=fake)
    sample = sampler.sample_once()
    assert sample is not None
    assert sample.disk is None
    assert fake.calls == ["/var/lib/rcm"]
    assert sample.cpu == MAC_CPU and sample.memory == MAC_MEMORY
    assert sampler.latest()[1] is None  # hosts_error 가 아니다


# ── (가) 실제 파일 시스템을 보는 헬퍼 ────────────────────────────────────────


def test_disk_usage_reads_a_real_directory(tmp_path):
    """예약 블록 때문에 used + free 는 total 과 안 맞는다 — 합이 아니라 부호만 본다."""
    usage = _disk_usage(str(tmp_path))
    assert usage is not None
    assert set(usage) == DISK_KEYS
    for key in ("used_bytes", "free_bytes", "total_bytes"):
        assert isinstance(usage[key], int) and not isinstance(usage[key], bool), key
    assert isinstance(usage["path"], str)
    assert usage["total_bytes"] > 0
    assert usage["free_bytes"] >= 0
    assert usage["used_bytes"] >= 0
    assert usage["path"] == str(tmp_path)


@pytest.mark.parametrize("path", [None, "", "/no/such/path/for/rcm"])
def test_disk_usage_of_a_path_it_cannot_read_is_none_not_an_exception(path):
    assert _disk_usage(path) is None


# ── (나) M5d-0 회귀 — 새 키가 표본 전체를 버리게 하지 않는다 ─────────────────

#: 워커가 heartbeat 에 싣는 문서 — `host_json` 에서 서버가 정하는 키를 뺀 모양.
WORKER_SAMPLE: dict[str, Any] = {
    "interval_seconds": 5,
    "os": "linux",
    "cores": 8,
    "load": [1.5, 1.0, 0.5],
    "cpu": {"user": 10.0, "sys": 2.5, "idle": 87.5, "busy": 12.5},
    "memory": {
        "total_bytes": 16_000_000_000,
        "used_bytes": 4_000_000_000,
        "compressed_bytes": None,
    },
    "gpu": None,
    "gpu_note": "nvidia-smi not found",
    "top": [{"comm": "cc1", "cpu": 90.0, "rss_mb": 512}],
    "history": [
        {
            "at": "2026-09-05T01:01:46Z",
            "cpu_busy": 12.5,
            "mem_used_bytes": 4_000_000_000,
            "gpu_util_pct": None,
        }
    ],
}


def parse(doc: Any) -> HostSample:
    """서버가 하는 그대로 — 이름·source·시각은 서버 값으로 덮는다."""
    return sample_from_json(doc, name="build-02", source="worker", sampled_at=NOW)


def test_gpu_note_code_no_longer_throws_the_whole_sample_away():
    """M5d-0 회귀: 이 키 하나 때문에 `hosts[]` 가 조용히 비었다."""
    sample = parse({**WORKER_SAMPLE, "gpu_note_code": "no_sampler"})
    assert sample.gpu_note_code == "no_sampler"
    assert sample.gpu_note == "nvidia-smi not found"
    assert sample.cores == 8 and sample.cpu is not None and sample.cpu["busy"] == 12.5


def test_disk_arrives_from_the_worker_intact():
    sample = parse({**WORKER_SAMPLE, "disk": DISK})
    assert sample.disk == DISK


def test_a_worker_without_a_disk_reading_leaves_the_field_none():
    assert parse(WORKER_SAMPLE).disk is None
    assert parse({**WORKER_SAMPLE, "disk": None}).disk is None


@pytest.mark.parametrize(
    "disk",
    [
        {**DISK, "inodes_free": 1234},  # 모르는 중첩 키 — 값이 멀쩡한 숫자라도 거부한다
        {**DISK, "mount_point": "/"},
        {"used_bytes": True},  # bool 은 숫자가 아니다
        {"free_bytes": float("nan")},
        {"total_bytes": float("inf")},
        {"used_bytes": "120 GB"},  # 숫자 자리의 문자열
        "not a dict",
        [1, 2],
    ],
)
def test_a_malformed_disk_is_still_a_value_error(disk):
    """모르는 **최상위** 키만 버린다 — 중첩 dict 의 검사 강도는 그대로다."""
    with pytest.raises(ValueError):
        parse({**WORKER_SAMPLE, "disk": disk})


def test_an_unknown_top_level_key_is_dropped_and_the_sample_lives():
    """더 새로운 워커가 보낸 모르는 키 — 버리고 나머지는 그대로 읽는다."""
    newer = {
        **WORKER_SAMPLE,
        "brand_new_key_from_a_newer_worker": 1,
        "npu": {"util_pct": 3},
        "battery": None,
    }
    sample = parse(newer)
    assert sample.os == "linux" and sample.cores == 8
    assert sample.cpu is not None and sample.cpu["busy"] == 12.5
    assert sample.memory is not None and sample.memory["used_bytes"] == 4_000_000_000
    assert not hasattr(sample, "brand_new_key_from_a_newer_worker")
    assert not hasattr(sample, "npu")


def test_one_known_key_is_enough_to_keep_a_sample():
    sample = parse({"cores": 8, "wholly_unknown": {"a": 1}})
    assert sample.cores == 8
    assert sample.name == "build-02" and sample.source == "worker"


@pytest.mark.parametrize(
    "doc",
    [
        {"foo": 1},  # 아는 키가 하나도 없다
        {"foo": 1, "bar": [2]},
        {},
        "not a dict",
        [1, 2],
        None,
        7,
    ],
)
def test_a_document_with_no_known_key_is_still_rejected(doc):
    with pytest.raises(ValueError):
        parse(doc)


@pytest.mark.parametrize(
    "doc",
    [
        {**WORKER_SAMPLE, "cores": True},  # bool 은 숫자가 아니다
        {**WORKER_SAMPLE, "cpu": {**WORKER_SAMPLE["cpu"], "busy": float("nan")}},
        {**WORKER_SAMPLE, "cpu": {**WORKER_SAMPLE["cpu"], "busy": float("inf")}},
        {**WORKER_SAMPLE, "cpu": {**WORKER_SAMPLE["cpu"], "busy": "NaN"}},
        {**WORKER_SAMPLE, "load": [float("inf"), 1.0, 0.5]},
        {**WORKER_SAMPLE, "load": [1.0, 0.5]},
        {**WORKER_SAMPLE, "memory": {"foo": 1}},  # 중첩 dict 는 아는 키만
        {**WORKER_SAMPLE, "gpu": {"util_pct": 1, "fan_rpm": 900}},
        {**WORKER_SAMPLE, "top": [{"comm": "cc1", "nice": 0}]},
        {**WORKER_SAMPLE, "history": [{"at": "2026-09-05T01:01:46Z", "swap": 1}]},
        {**WORKER_SAMPLE, "os": 3},  # 문자열 자리의 숫자
    ],
)
def test_the_value_checks_are_as_strict_as_before(doc):
    with pytest.raises(ValueError):
        parse(doc)


# ── (나) 왕복 — `host_json` 이 낸 문서를 파서가 그대로 받는다 ────────────────

#: 왕복 시험용. 빠지는 칸을 잡으려고 **모든 칸을 일부러 채웠다**(gpu 와 note 가 함께 온다).
FULL = HostSample(
    name="macmini",
    source="local",
    sampled_at=NOW,
    interval_seconds=5.0,
    os="linux",
    cores=8,
    load=(1.5, 1.0, 0.5),
    cpu={"user": 10.0, "sys": 2.5, "idle": 87.5, "busy": 12.5},
    memory={"total_bytes": 16_000_000_000, "used_bytes": 4_000_000_000, "compressed_bytes": None},
    gpu={
        "util_pct": 13,
        "mem_used_bytes": 622_854_144,
        "mem_total_bytes": 8_589_934_592,
        "source": "nvidia-smi",
    },
    disk=DISK,
    gpu_note="nvidia-smi not found",
    gpu_note_code="no_sampler",
    top=({"comm": "cc1", "cpu": 90.0, "rss_mb": 512},),
    history=(
        {"at": iso(NOW), "cpu_busy": 12.5, "mem_used_bytes": 4_000_000_000, "gpu_util_pct": 13},
    ),
)


def test_every_key_host_json_emits_survives_the_parser():
    """`remote_worker.RemoteWorker._host_sample` 이 하는 그대로 — 서버 키만 빼고 되돌린다.

    `host_json` 에 칸이 늘 때마다 파서를 같이 고쳐야 하는지가 여기서 바로 드러난다.
    """
    doc = host_json(FULL, now=NOW)
    payload = {k: v for k, v in doc.items() if k not in SERVER_KEYS}
    carried = {f.name for f in fields(HostSample)} - SERVER_KEYS
    assert carried <= set(payload)  # 문서가 빠뜨린 칸이 없다
    back = parse(payload)
    for key in sorted(carried):
        assert getattr(back, key) == getattr(FULL, key), key
    assert (back.name, back.source, back.sampled_at) == ("build-02", "worker", NOW)


def test_the_round_trip_holds_for_a_host_with_nothing_but_the_required_fields():
    """칸이 거의 다 비어도 왕복한다 — `None` 이 파서를 막지 않는다."""
    bare = HostSample(name="lin-01", source="local", sampled_at=NOW, interval_seconds=5.0)
    payload = {k: v for k, v in host_json(bare, now=NOW).items() if k not in SERVER_KEYS}
    back = parse(payload)
    assert back.cpu is None and back.memory is None and back.gpu is None and back.disk is None
    assert back.top == () and back.history == ()
    assert back.interval_seconds == 5.0
