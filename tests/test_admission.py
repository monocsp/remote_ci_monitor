"""부하 게이트의 순수 판정(M5f §4.3) — 시계도 I/O 도 없다. `now` 는 인자다.

시나리오는 `docs/m5f-test-scenarios-a.md`. 뮤테이션 ⑪(fail-open)·⑫(연속 표본)이 여기서 빨개진다.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from remote_ci_monitor.core.admission import AdmissionConfig, Hold, decide
from remote_ci_monitor.core.model import HostSample

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
CFG = AdmissionConfig()
LOAD = (1.0, 1.0, 1.0)
MEMORY = {"total_bytes": 25769803776, "used_bytes": 17550622720, "compressed_bytes": 0}


def stamp(dt: datetime) -> str:
    """`core.status.iso` 는 microsecond 를 버린다 — 0.1초 epsilon 을 재려면 이게 필요하다."""
    return dt.isoformat().replace("+00:00", "Z")


def entry(busy: float | None, at_: datetime, mem: int = 1) -> dict[str, Any]:
    """로컬 샘플러가 만드는 모양(`hostsample.py`) — 네 키를 다 넣는다."""
    return {"at": stamp(at_), "cpu_busy": busy, "mem_used_bytes": mem, "gpu_util_pct": None}


def history(*busy: float | None, step: float = 5.0, end: datetime = NOW) -> tuple[dict, ...]:
    """오래된 것 → 새것. 마지막 항목이 `end`, 그 앞은 step 초씩 뒤로."""
    n = len(busy)
    return tuple(entry(b, end - timedelta(seconds=step * (n - 1 - i))) for i, b in enumerate(busy))


def sample(
    *,
    hist: tuple[dict, ...] | None = None,
    age: float = 0.0,
    interval: float = 5.0,
    cpu: dict | None = ...,  # type: ignore[assignment]
    memory: dict | None = MEMORY,
    load: tuple[float, float, float] | None = LOAD,
) -> HostSample:
    sampled_at = NOW - timedelta(seconds=age)
    return HostSample(
        name="macmini",
        source="local",
        sampled_at=sampled_at,
        interval_seconds=interval,
        os="darwin",
        cores=10,
        load=load,
        cpu={"user": 5.0, "sys": 5.0, "idle": 90.0, "busy": 10.0} if cpu is ... else cpu,
        memory=memory,
        history=history(10.0, 10.0, 10.0, end=sampled_at) if hist is None else hist,
    )


def d(**kw: Any) -> Hold | None:
    return decide(
        lane=kw.pop("lane", 2),
        cfg=kw.pop("cfg", CFG),
        now=kw.pop("now", NOW),
        sample=kw.pop("sample", sample()),
        last_admit_at=kw.pop("last_admit_at", None),
    )


#: 레인 ≥ 2 · policy "load" 에서 **반드시 닫히는** 아홉 가지. A·B·H 가 같이 쓴다.
HOLD_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("no_sample", {"sample": None}, "no_sample"),
    ("stale", {"sample": sample(age=60.0)}, "no_sample"),
    ("cpu_none", {"sample": sample(cpu=None)}, "no_sample"),
    ("cpu_busy_none", {"sample": sample(cpu={"busy": None})}, "no_sample"),
    ("cooldown", {"last_admit_at": NOW - timedelta(seconds=1)}, "cooldown"),
    ("short_window", {"sample": sample(hist=history(10.0, 10.0))}, "no_sample"),
    ("none_in_window", {"sample": sample(hist=history(10.0, None, 10.0))}, "no_sample"),
    ("broken_window", {"sample": sample(hist=history(10.0, 10.0, 10.0, step=99.0))}, "no_sample"),
    ("over_cap", {"sample": sample(hist=history(10.0, 10.0, 95.0))}, "cpu_busy"),
]
IDS = [c[0] for c in HOLD_CASES]


# ── A. 레인 1 은 게이트를 안 지난다 (결정 40·41) ─────────────────────────────


@pytest.mark.parametrize(("_id", "kw", "_code"), HOLD_CASES, ids=IDS)
def test_lane_one_is_open_for_every_hold_case(_id, kw, _code):
    """레인 1 이 닫히면 큐 전체가 선다. 기동 직후 표본이 없어도 잡은 돌아야 한다."""
    assert d(lane=1, **kw) is None


@pytest.mark.parametrize(("_id", "kw", "code"), HOLD_CASES, ids=IDS)
def test_every_hold_case_actually_holds_for_lane_two(_id, kw, code):
    """A1·B1 의 **대조군** — 없으면 「전부 열림」 버그가 그 둘을 통과한다."""
    hold = d(lane=2, **kw)
    assert isinstance(hold, Hold) and hold.code == code


def test_lane_one_is_open_when_every_hold_condition_is_true_at_once():
    """`lane == 1` 검사가 첫 줄이 아닌 구현을 잡는다."""
    assert d(lane=1, sample=None, last_admit_at=NOW) is None


def test_lane_zero_or_negative_is_open_too():
    """레인은 1부터지만 방어로 `<= 1` 이다 — 0 이 들어와도 잠기지 않는다."""
    assert d(lane=0, sample=None) is None and d(lane=-1, sample=None) is None


# ── B. policy = "always" 는 오늘의 동작 ──────────────────────────────────────


@pytest.mark.parametrize(("_id", "kw", "_code"), HOLD_CASES, ids=IDS)
def test_always_policy_is_open_for_every_hold_case(_id, kw, _code):
    assert d(cfg=AdmissionConfig(policy="always"), **kw) is None


def test_default_policy_is_load():
    assert AdmissionConfig().policy == "load"


# ── C. 표본 없음 · 낡음 (fail-open 금지) ────────────────────────────────────


def test_stale_boundary_is_exclusive():
    """`age > stale_seconds` — 정확히 경계면 아직 신선하다(`hostparse.stale` 과 같은 규칙)."""
    limit = CFG.stale_seconds
    assert d(sample=sample(age=limit - 0.1)) is None
    assert d(sample=sample(age=limit)) is None
    assert d(sample=sample(age=limit + 0.1)).code == "no_sample"


def test_stale_uses_the_callers_limit_not_three_times_interval():
    """원격 표본은 서버가 heartbeat 까지 셈해 넣는다 — 표본의 interval 로 계산하면 안 된다."""
    cfg = AdmissionConfig(stale_seconds=60.0)
    assert d(cfg=cfg, sample=sample(age=30.0, interval=5.0)) is None


def test_a_sample_stamped_in_the_future_is_not_stale():
    assert d(sample=sample(age=-5.0)) is None


# ── D. CPU 를 모르는 표본 (TypeError 가 아니라 hold) ────────────────────────


def test_a_sample_can_exist_with_unknown_cpu():
    """표본은 cpu·memory·load 중 하나만 읽혀도 만들어진다(`hostsample.py`)."""
    for cpu in (None, {}, {"busy": None}, {"user": 1.0}):
        hold = d(sample=sample(cpu=cpu))
        assert isinstance(hold, Hold) and hold.code == "no_sample", cpu


# ── E. 쿨다운 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("elapsed", "expect"), [(29.0, "cooldown"), (30.0, None), (31.0, None)])
def test_cooldown_boundary_is_exclusive(elapsed, expect):
    """`now - last_admit < cooldown` 이 보류다 — 정확히 30초면 열린다."""
    hold = d(last_admit_at=NOW - timedelta(seconds=elapsed))
    assert (hold.code if hold else None) == expect


def test_zero_cooldown_never_holds():
    cfg = AdmissionConfig(cooldown_seconds=0.0)
    assert d(cfg=cfg, last_admit_at=NOW) is None


def test_cooldown_is_decided_before_the_window_is_walked():
    """판정 순서는 규범이다 — 화면이 「왜 막혔나」를 한 가지로만 말해야 한다."""
    hold = d(last_admit_at=NOW, sample=sample(hist=history(10.0, 10.0, 95.0)))
    assert hold.code == "cooldown"


# ── F. 창(window) — 개수 · None · 연속성 ────────────────────────────────────


def test_window_needs_exactly_as_many_entries_as_configured():
    assert d(sample=sample(hist=history(10.0, 10.0))).code == "no_sample"
    assert d(sample=sample(hist=history(10.0, 10.0, 10.0))) is None


def test_a_none_outside_the_window_does_not_matter():
    """창은 **마지막 N개**다. 그 앞의 결측은 이미 지나간 일이다."""
    assert d(sample=sample(hist=history(None, 10.0, 10.0, 10.0))) is None


def test_contiguity_boundary_is_two_intervals():
    ok = sample(hist=history(10.0, 10.0, 10.0, step=10.0))  # 2 × interval 정각
    bad = sample(hist=history(10.0, 10.0, 10.0, step=10.1))
    assert d(sample=ok) is None
    assert d(sample=bad).code == "no_sample"


def test_a_sampler_outage_leaves_no_gap_in_the_list_so_the_window_must_check_time():
    """2초 간격 셋 + 600초 공백 + 하나 — 리스트에서는 붙어 있지만 벽시계로는 604초다."""
    hist = (
        entry(10.0, NOW - timedelta(seconds=604)),
        entry(10.0, NOW - timedelta(seconds=602)),
        entry(10.0, NOW - timedelta(seconds=600)),
        entry(10.0, NOW),
    )
    assert d(sample=sample(hist=hist, interval=2.0)).code == "no_sample"


def test_contiguity_compares_entries_to_each_other_not_to_now():
    """`history[].at` 은 표본을 만든 머신의 시계, `sampled_at` 은 서버 시계다 — 섞으면 안 된다."""
    old = NOW - timedelta(hours=3)  # 워커 시계가 3시간 어긋나 있다
    hist = history(10.0, 10.0, 10.0, end=old)
    assert d(sample=sample(hist=hist)) is None  # 항목끼리는 5초 간격이라 멀쩡하다


def test_a_broken_or_missing_timestamp_holds():
    for bad in ("", "not-a-time", None):
        hist = (
            entry(10.0, NOW - timedelta(seconds=10)),
            {"at": bad, "cpu_busy": 10.0},
            entry(10.0, NOW),
        )
        assert d(sample=sample(hist=hist)).code == "no_sample", bad


def test_an_empty_history_entry_holds():
    """원격 표본의 항목은 `{}` 일 수 있다(`hostparse.sample_from_json`)."""
    hist = (entry(10.0, NOW - timedelta(seconds=10)), {}, entry(10.0, NOW))
    assert d(sample=sample(hist=hist)).code == "no_sample"


def test_a_window_of_one_needs_no_contiguity():
    cfg = AdmissionConfig(samples=1)
    assert d(cfg=cfg, sample=sample(hist=history(10.0, 10.0, 10.0, step=99.0))) is None


def test_a_non_positive_interval_does_not_lock_the_machine_forever():
    """`interval_seconds` 가 0 이면 모든 창이 「끊김」이 된다 — 하한을 둔다."""
    assert d(sample=sample(hist=history(10.0, 10.0, 10.0, step=1.0), interval=0.0)) is None


# ── G. CPU 상한 ─────────────────────────────────────────────────────────────


def test_exactly_at_the_cap_is_open_and_a_hair_over_holds():
    """뮤테이션 ⑫ 의 짝 — `<=` 를 `<` 로 바꾸면 앞이, `all` 을 `any` 로 바꾸면 뒤가 빨개진다."""
    assert d(sample=sample(hist=history(80.0, 80.0, 80.0))) is None
    assert d(sample=sample(hist=history(80.0, 80.0, 80.1))).code == "cpu_busy"


@pytest.mark.parametrize(
    ("window", "open_"),
    [((70.0, 70.0, 85.0), False), ((70.0, 70.0, 79.0), True), ((85.0, 70.0, 70.0), False)],
)
def test_every_entry_in_the_window_must_be_under_the_cap(window, open_):
    """하나라도 넘으면 닫는다 — 한 번 꺼진 표본에 속아 여는 버그를 막는다."""
    assert (d(sample=sample(hist=history(*window))) is None) is open_


def test_hold_detail_carries_the_highest_value_in_the_window():
    """창이 `[85, 70, 70]` 인데 화면이 `held (cpu 70%)` 라고 쓰면 거짓말이다."""
    hold = d(sample=sample(hist=history(85.0, 70.0, 70.0)))
    assert hold.code == "cpu_busy" and hold.detail == {"cpu_busy": 85.0}


def test_no_sample_and_cooldown_carry_no_detail():
    """얼마나 오래 막혔는지는 `held_since` 가 말한다."""
    assert d(sample=None).detail is None
    assert d(last_admit_at=NOW).detail is None


def test_the_cap_is_read_from_the_config():
    cfg = AdmissionConfig(cpu_max_percent=50.0)
    assert d(cfg=cfg, sample=sample(hist=history(60.0, 60.0, 60.0))).code == "cpu_busy"
    assert d(cfg=cfg, sample=sample(hist=history(40.0, 40.0, 40.0))) is None


# ── H. 판정 순서는 규범이다 ─────────────────────────────────────────────────


def test_stale_beats_cpu_busy():
    """낡은 표본은 상한을 넘었어도 `no_sample` 이다 — 그 숫자를 못 믿기 때문이다."""
    hold = d(sample=sample(age=60.0, hist=history(10.0, 10.0, 95.0)))
    assert hold.code == "no_sample"


def test_stale_beats_cooldown():
    hold = d(sample=sample(age=60.0), last_admit_at=NOW)
    assert hold.code == "no_sample"


# ── I. 순수성 · 계약 ────────────────────────────────────────────────────────


def test_decide_is_deterministic():
    args = {"sample": sample(hist=history(10.0, 10.0, 10.0))}
    assert d(**args) == d(**args)


def test_decide_never_reads_the_clock_or_the_filesystem():
    """`core/` 는 I/O 도 시계도 안 본다 — `now` 는 인자다."""
    src = sys.modules["remote_ci_monitor.core.admission"].__file__
    text = open(src, encoding="utf-8").read()  # noqa: SIM115
    for banned in ("datetime.now", "time.time", "utcnow", "open(", "Path("):
        assert banned not in text, banned


def test_admission_config_does_not_import_the_config_module():
    """순수 계층은 `config.py` 를 모른다 — `core/queue.QueueConfig` 와 같은 방식."""
    text = open(sys.modules["remote_ci_monitor.core.admission"].__file__, encoding="utf-8").read()  # noqa: SIM115
    assert "remote_ci_monitor.config" not in text


def test_config_and_hold_are_frozen():
    with pytest.raises(FrozenInstanceError):
        CFG.cpu_max_percent = 1.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        Hold("cpu_busy").code = "x"  # type: ignore[misc]


def test_decide_does_not_mutate_its_arguments():
    s = sample(hist=history(10.0, 10.0, 10.0))
    before = (s.history, s.cpu, s.sampled_at)
    d(sample=s)
    assert (s.history, s.cpu, s.sampled_at) == before


def test_decide_never_raises_on_a_shape_it_cannot_read():
    """읽을 수 없는 모양은 전부 `no_sample` 이다. 통짜 try/except 는 금지 — 진짜 버그가 숨는다."""
    weird = sample(hist=({"at": 12345, "cpu_busy": "hot"},) * 3)
    hold = d(sample=weird)
    assert isinstance(hold, Hold) and hold.code == "no_sample"
