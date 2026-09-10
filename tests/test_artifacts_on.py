"""프리셋 `artifacts_on` 과 claim 정책 배선 (M5g §6 · 결정 59 · §13 D).

**구현보다 먼저 썼다(test-first).**

여기서 잠그는 것 둘:

- **§13 D — 원격 워커가 산출물을 하나도 안 모으던 기존 버그.** 서버의 claim 응답에 얼린 정책이
  아예 안 실려서 워커의 `_policy_from_claim` 이 언제나 `None` 을 돌려줬다. `artifacts` 를 선언한
  프리셋이 원격 풀에서 돌면 아무것도 안 모이는데 잡은 성공하고 화면도 아무 말을 안 했다.
  시험이 **손으로 만든 claim payload** 를 써서(`tests/test_worker_m5e.py:287`) 이 구멍을 못 잡았다 —
  그래서 여기서는 **서버가 실제로 만드는** payload 를 쓴다.
- **결정 59 — `artifacts_on`.** 초록 잡마다 무거운 로그를 모아 24시간 들고 있을 이유가 없다.
  기본값 `"always"` 는 오늘의 동작 그대로다.
"""

from __future__ import annotations

import pytest

from remote_ci_monitor.config import ConfigError, parse_preset
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import CANCELLED, FAILED, LOST, SUCCEEDED, TIMED_OUT
from remote_ci_monitor.remote_worker import _policy_from_claim

GOLDENS = {
    "name": "goldens",
    "argv": ["flutter", "test"],
    "artifacts": ["test/**/goldens/*.png"],
}


# ── 설정 ─────────────────────────────────────────────────────────────────────


def test_the_default_is_always_which_is_todays_behaviour() -> None:
    assert parse_preset(GOLDENS).artifacts_on == "always"


@pytest.mark.parametrize("value", ["always", "failure"])
def test_both_values_parse(value: str) -> None:
    assert parse_preset({**GOLDENS, "artifacts_on": value}).artifacts_on == value


def test_an_unknown_value_names_the_preset_and_the_key() -> None:
    with pytest.raises(ConfigError) as e:
        parse_preset({**GOLDENS, "artifacts_on": "sometimes"})
    assert "goldens" in str(e.value) and "artifacts_on" in str(e.value)


def test_it_is_pointless_without_globs_and_says_so() -> None:
    """글롭이 없으면 아무것도 안 모은다 — 조건만 적어 둔 설정은 오해다."""
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "x", "argv": ["true"], "artifacts_on": "failure"})
    assert "artifacts_on" in str(e.value) and "artifacts" in str(e.value)


# ── 순수 규칙: 언제 모으나 ───────────────────────────────────────────────────


@pytest.mark.parametrize("state", [FAILED, TIMED_OUT, CANCELLED])
def test_failure_only_collects_for_every_non_success_terminal_state(state: str) -> None:
    policy = art.ArtifactPolicy(globs=("out/*",), collect_on="failure")
    assert policy.collects_for(state) is True


def test_failure_only_skips_a_green_job(policy_off=None) -> None:
    policy = art.ArtifactPolicy(globs=("out/*",), collect_on="failure")
    assert policy.collects_for(SUCCEEDED) is False


def test_always_collects_for_a_green_job() -> None:
    policy = art.ArtifactPolicy(globs=("out/*",), collect_on="always")
    assert policy.collects_for(SUCCEEDED) is True


def test_a_lost_job_is_never_collected_under_either_setting() -> None:
    """죽은 잡을 산출물로 되살리지 않는다(M5e §5) — 새 키가 그 규칙을 흔들지 않는다."""
    for on in ("always", "failure"):
        assert art.ArtifactPolicy(globs=("out/*",), collect_on=on).collects_for(LOST) is False


def test_a_preset_without_globs_never_collects() -> None:
    assert art.ArtifactPolicy(collect_on="always").collects_for(FAILED) is False


# ── §13 D: 서버가 만드는 claim payload 가 정책을 싣는다 ──────────────────────


def test_the_servers_own_claim_payload_carries_the_policy(tmp_path):
    """**손으로 만든 payload 가 아니라** 서버가 만드는 것을 워커의 파서에 그대로 넣는다."""
    from test_server import Server

    srv = Server(tmp_path, workers=False)
    try:
        preset = parse_preset({**GOLDENS, "artifacts_on": "failure"})
        srv.cfg.presets = (*srv.cfg.presets, preset)
        job = _a_job(srv, "goldens")
        payload = srv.app._claim_payload(job)
        policy = _policy_from_claim(payload)
        assert policy is not None, "원격 워커가 정책을 못 찾는다 — 아무것도 안 모은다"
        assert policy.globs == ("test/**/goldens/*.png",)
        assert policy.collect_on == "failure"
        assert policy.max_bytes == srv.cfg.server.max_artifact_bytes
    finally:
        srv.close()


def test_a_preset_without_artifacts_sends_no_policy(tmp_path):
    from test_server import Server

    srv = Server(tmp_path, workers=False)
    try:
        job = _a_job(srv, "ok")
        assert _policy_from_claim(srv.app._claim_payload(job)) is None
    finally:
        srv.close()


def test_an_old_server_that_sends_no_policy_means_always(tmp_path):
    """필드가 없는 옛 서버에서는 **옛 동작**이 기본이다 — 조용히 안 모으게 만들지 않는다."""
    claimed = {"job": {"id": 7}, "preset": {"name": "goldens", "artifacts": ["out/*"]}}
    policy = _policy_from_claim(claimed)
    assert policy is not None and policy.collect_on == "always"


def _a_job(srv, preset: str):
    """그 프리셋의 잡 하나를 Store 에 직접 만든다(HTTP 왕복 없이)."""
    from datetime import UTC, datetime

    from remote_ci_monitor.core.model import Requester, Source
    from remote_ci_monitor.core.queue import join_key

    now = datetime.now(UTC)
    return srv.store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=None,
        source=Source(mode="tree", repo="o/a", base_sha="a", dirty=False, tree_hash=preset),
        requester=Requester(name="alice-laptop", label="alice@l"),
        timeout_seconds=60,
        join_key=join_key(preset, {}, preset),
        now=now,
        state="queued",
    )


# ── 중복의 안전장치: 예정 상태가 실제 종료 규칙과 어긋나지 않는다 ────────────


@pytest.mark.parametrize(
    ("rc", "cancelled", "timed_out", "lost"),
    [
        (0, False, False, False),
        (1, False, False, False),
        (None, False, False, False),
        (0, True, False, False),
        (1, True, False, False),
        (0, False, True, False),
        (0, False, False, True),
        (1, True, True, False),
    ],
)
def test_the_prospective_state_agrees_with_the_real_outcome_rule(rc, cancelled, timed_out, lost):
    """수집은 종료를 커밋하기 **전에** 일어나므로 예정 상태로 판정한다.

    그 판정이 `outcome_for` 와 어긋나면 「초록인 줄 알고 안 모았는데 실패였다」가 된다.
    이 시험이 그 중복을 잡는다.
    """
    from datetime import UTC, datetime

    from jobfactory import job as make_job
    from remote_ci_monitor.worker import outcome_for

    now = datetime.now(UTC)
    real = outcome_for(
        make_job(1),
        [],
        started=now,
        finished=now,
        rc=rc,
        cancelled=cancelled,
        timed_out=timed_out,
        lost=lost,
    )
    guess = art.prospective_state(rc, cancelled=cancelled, timed_out=timed_out, lost=lost)
    assert guess == real.state


# ── 두 워커 경로가 실제로 안 모은다 ──────────────────────────────────────────


def test_the_local_worker_skips_collection_for_a_green_job(tmp_path):
    """초록 잡마다 무거운 로그를 모아 24시간 들고 있을 이유가 없다(결정 59)."""
    from remote_ci_monitor.store import Store
    from test_worker import enqueue, make_config, run_one

    cfg = make_config(tmp_path)
    cfg.presets = (
        *cfg.presets,
        parse_preset(
            {
                "name": "gold",
                "argv": ["sh", "-c", "mkdir -p out && echo hi > out/a.txt"],
                "artifacts": ["out/*"],
                "artifacts_on": "failure",
            }
        ),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        job_id = enqueue(store, cfg, "gold")
        run_one(store, cfg, job_id)
        job = store.get_job(job_id)
        assert job.state == SUCCEEDED
        assert store.get_bundle(job_id) is None  # 행조차 안 만든다
    finally:
        store.close()


def test_the_local_worker_still_collects_when_the_same_preset_fails(tmp_path):
    from remote_ci_monitor.store import Store
    from test_worker import enqueue, make_config, run_one

    cfg = make_config(tmp_path)
    cfg.presets = (
        *cfg.presets,
        parse_preset(
            {
                "name": "gold",
                "argv": ["sh", "-c", "mkdir -p out && echo why > out/a.txt && exit 1"],
                "artifacts": ["out/*"],
                "artifacts_on": "failure",
            }
        ),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        job_id = enqueue(store, cfg, "gold")
        run_one(store, cfg, job_id)
        assert store.get_job(job_id).state == FAILED
        row = store.get_bundle(job_id)
        assert row is not None and row["file_count"] == 1
    finally:
        store.close()


def test_always_keeps_collecting_a_green_job(tmp_path):
    """기본값은 오늘의 동작 그대로다 — 업그레이드가 조용히 산출물을 끊지 않는다."""
    from remote_ci_monitor.store import Store
    from test_worker import enqueue, make_config, run_one

    cfg = make_config(tmp_path)
    cfg.presets = (
        *cfg.presets,
        parse_preset(
            {
                "name": "gold",
                "argv": ["sh", "-c", "mkdir -p out && echo hi > out/a.txt"],
                "artifacts": ["out/*"],
            }
        ),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        job_id = enqueue(store, cfg, "gold")
        run_one(store, cfg, job_id)
        assert store.get_job(job_id).state == SUCCEEDED
        assert store.get_bundle(job_id) is not None
    finally:
        store.close()
