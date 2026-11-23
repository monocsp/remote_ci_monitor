"""M5s D — 종료코드 반복: 설정 · 종료 규칙 · 사슬 저장 · 원격 finish · 취소 · health
(`docs/m5s-workplan.md` §4.2 · §7 S7 S13 S20 S26 S27 · 결정 210 211 225 228 229 230 231).

저장소 시험은 진짜 SQLite 다. 서버 시험은 `test_worker_api.WorkerServer`(주입 시계 · 로컬 워커
스레드 없음)에 원격 워커 프로토콜(`/worker/claim` · `/worker/jobs/N/finish`)로 결과를 보고한다 —
원격 finish 와 로컬 finish 가 같은 판정(`outcome_for`)과 같은 `store.finish` 를 지난다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from remote_ci_monitor.config import ConfigError, parse_notify, parse_preset
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    EXHAUSTED,
    EXIT_CODE_BY_STATE,
    FAILED,
    NOTIFY_DEFAULT_STATES,
    QUEUED,
    RETRIED,
    RUNNING,
    SCHEDULED,
    SUCCEEDED,
    TERMINAL_STATES,
    JobLink,
    Requester,
    RetryPolicy,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import outcome_for
from test_server import sh
from test_worker_api import GIT_SHA, WorkerServer

T = datetime(2026, 11, 23, 6, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")
POLICY = RetryPolicy(on_exit=75, interval_seconds=600, max_attempts=3)


def at(seconds: float) -> datetime:
    return T + timedelta(seconds=seconds)


def git_src(sha: str = "a" * 40) -> Source:
    return Source(mode="git_ref", repo="app", ref="main", sha=sha, base_sha=sha, dirty=False)


# ── 설정 (결정 210 · 211) ────────────────────────────────────────────────────


def retry_preset(**retry: Any) -> dict[str, Any]:
    return sh("golive", "exit 75", source_modes=["git_ref"], repo="app", retry=retry)


def test_the_retry_table_takes_the_requested_defaults():
    p = parse_preset(retry_preset())
    assert p.retry == RetryPolicy(on_exit=75, interval_seconds=600, max_attempts=12)


def test_the_retry_table_takes_its_own_values():
    p = parse_preset(retry_preset(on_exit=75, interval_seconds=60, max_attempts=4))
    assert p.retry == RetryPolicy(on_exit=75, interval_seconds=60, max_attempts=4)


def test_a_preset_without_the_table_does_not_retry():
    assert parse_preset(sh("rcm-ci", "exit 75", source_modes=["git_ref"], repo="app")).retry is None


@pytest.mark.parametrize("modes", [["tree"], ["tree", "git_ref"]])
def test_retry_is_refused_unless_the_preset_takes_git_ref_only(modes):
    raw = sh("golive", "exit 75", source_modes=modes, retry={})
    if "git_ref" in modes:
        raw["repo"] = "app"
    with pytest.raises(ConfigError, match="retry needs source_modes"):
        parse_preset(raw)


@pytest.mark.parametrize(
    "retry",
    [
        {"on_exit": 0},
        {"on_exit": 256},
        {"on_exit": True},
        {"interval_seconds": 0},
        {"max_attempts": 1},
        {"max_attempts": 1000},
        {"max_attempts": "12"},
        {"every": 5},
    ],
)
def test_bad_retry_values_are_refused(retry):
    with pytest.raises(ConfigError, match="retry"):
        parse_preset(retry_preset(**retry))


def test_retry_must_be_a_table():
    raw = sh("golive", "exit 75", source_modes=["git_ref"], repo="app", retry=True)
    with pytest.raises(ConfigError, match="retry must be a table"):
        parse_preset(raw)


# ── 상태 집합 · 종료코드 · 알림 기본값 ───────────────────────────────────────


def test_retried_and_exhausted_are_terminal_and_only_exhausted_ends_a_wait():
    assert {RETRIED, EXHAUSTED} <= TERMINAL_STATES
    assert EXIT_CODE_BY_STATE[EXHAUSTED] == 1  # 결정 212
    assert RETRIED not in EXIT_CODE_BY_STATE  # 기다림은 끝나지 않는다


def test_retried_is_out_of_the_default_notify_set_and_exhausted_is_in():
    assert RETRIED not in NOTIFY_DEFAULT_STATES
    assert EXHAUSTED in NOTIFY_DEFAULT_STATES
    rule = parse_notify({"name": "hook", "argv": ["true"]})
    assert RETRIED not in rule.on and EXHAUSTED in rule.on
    chosen = parse_notify({"name": "hook", "argv": ["true"], "on": ["retried", "exhausted"]})
    assert chosen.on == frozenset({RETRIED, EXHAUSTED})


# ── 저장소 ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def make(store: Store, *, retry: RetryPolicy | None = POLICY, **kw: Any):
    src = kw.pop("source", git_src())
    inputs = kw.pop("inputs", {"build_name": "1.2.0"})
    return store.create_job(
        preset=kw.pop("preset", "golive"),
        inputs=inputs,
        key="golive",
        concurrency_group=kw.pop("group", "chrome"),
        source=src,
        requester=ALICE,
        timeout_seconds=1800,
        join_key=join_key("golive", inputs, src.identity),
        now=kw.pop("now", T),
        state=kw.pop("state", QUEUED),
        retry=retry,
        **kw,
    )


def run(store: Store, job_id: int, now: datetime = T):
    claimed = store.claim(1, now)
    assert claimed is not None and claimed.id == job_id, claimed
    return claimed


def retry_once(store: Store, job_id: int, now: datetime) -> int:
    assert store.finish(job_id, RETRIED, now=now, exit_code=75)
    nxt = store.get_job(job_id).retried_as
    assert nxt is not None
    return nxt


def test_submission_freezes_the_policy_and_the_root_is_its_own_chain(store):
    j = make(store)
    assert j.chain_id == j.id and j.attempt == 1 and j.retry_of is None
    assert (j.retry_on_exit, j.retry_interval_seconds, j.retry_max_attempts) == (75, 600, 3)
    assert j.retry_disabled is False
    off = make(store, inputs={"build_name": "1.2.1"}, retry_disabled=True)
    assert off.retry_on_exit == 75 and off.retry_disabled is True
    plain = make(store, inputs={"build_name": "1.2.2"}, retry=None)
    assert plain.chain_id is None and plain.retry_on_exit is None


def test_a_retried_attempt_makes_the_next_one_in_the_same_finish(store):
    link = JobLink(url="https://example.invalid/pr/1", label="PR 1")
    j = make(store, link=link, keep=True, no_supersede=True, priority=1)
    store.add_joiner(j.id, "bob-desk", "bob@desk", T)
    run(store, j.id)
    nxt_id = retry_once(store, j.id, at(30))
    old, nxt = store.get_job(j.id), store.get_job(nxt_id)
    assert old.state == RETRIED and old.retried_as == nxt_id and old.exit_code == 75
    assert old.summary_code == "retrying"
    assert old.summary_args == {
        "code": 75,
        "attempt": 1,
        "max": 3,
        "by": nxt_id,
        "at": "2026-11-23 06:10Z",
    }
    assert nxt.state == SCHEDULED
    assert nxt.not_before == at(630) and nxt.start_deadline is None  # 결정 231
    assert (nxt.attempt, nxt.retry_of, nxt.chain_id) == (2, j.id, j.id)
    # 넘기는 열(결정 228)
    assert nxt.inputs == j.inputs and nxt.source.sha == j.source.sha
    assert nxt.priority == 1 and nxt.link == link and nxt.keep is True
    assert nxt.no_supersede is True and nxt.concurrency_group == "chrome"
    assert (nxt.retry_on_exit, nxt.retry_interval_seconds, nxt.retry_max_attempts) == (75, 600, 3)
    # 합류자는 다음 시도로 옮긴다
    assert [x.name for x in nxt.joiners] == ["bob-desk"] and old.joiners == ()
    assert nxt.started_at is None and nxt.exit_code is None and nxt.summary is None


def test_retried_only_leaves_running_and_a_cancel_in_between_makes_no_next_attempt(store):
    """결정 225 — 75 로 끝난 순간 취소가 먼저 들어왔으면 반복이 사람의 취소를 덮지 않는다."""
    j = make(store)
    run(store, j.id)
    assert store.request_cancel(j.id, "alice-laptop", at(10), 5) == CANCELLING
    assert store.finish(j.id, RETRIED, now=at(11), exit_code=75) is False
    # 부르는 쪽이 `only_from` 을 안 줘도(또는 다른 것을 줘도) 같다
    assert store.finish(j.id, RETRIED, now=at(11), exit_code=75, only_from=None) is False
    assert store.get_job(j.id).state == CANCELLING
    assert store.chain_jobs(j.id) == [store.get_job(j.id)]
    assert store.list_scheduled() == []


def test_the_last_attempt_cannot_be_retried(store):
    j = make(store, retry=RetryPolicy(75, 60, 2))
    run(store, j.id)
    nxt = retry_once(store, j.id, at(5))
    store.promote_due(at(70))
    run(store, nxt, at(70))
    with pytest.raises(Exception, match="nothing to retry"):
        store.finish(nxt, RETRIED, now=at(80), exit_code=75)
    assert store.get_job(nxt).state == RUNNING  # 거절된 finish 는 아무것도 바꾸지 않는다


def test_a_job_without_a_policy_cannot_be_retried(store):
    j = make(store, retry=None)
    run(store, j.id)
    with pytest.raises(Exception, match="no retry policy"):
        store.finish(j.id, RETRIED, now=at(5), exit_code=75)
    assert store.get_job(j.id).state == RUNNING


def test_s27_a_retry_attempt_has_no_start_deadline_and_never_expires(store):
    """결정 231 — 사람이 로그인하는 동안 그룹이 막혀도 다음 시도는 `expired` 가 되지 않는다."""
    j = make(store)
    run(store, j.id)
    nxt = retry_once(store, j.id, at(0))
    assert store.expire_overdue(at(10 * 3600)) == []
    assert store.promote_due(at(10 * 3600)) == [nxt]
    assert store.expire_overdue(at(30 * 3600)) == []
    assert store.get_job(nxt).state == QUEUED


def test_nothing_joins_a_promoted_retry_attempt(store):
    """넘어온 P2 — 승격된 「마감 없는 예약」(반복 시도)에 즉시 실행이 붙지 않는다. 합류 필터는
    `not_before IS NULL` 이다(예약에서 온 잡 전체를 뺀다)."""
    j = make(store)
    run(store, j.id)
    nxt = retry_once(store, j.id, at(0))
    store.promote_due(at(700))
    got = store.get_job(nxt)
    assert got.state == QUEUED and got.not_before is not None and got.start_deadline is None
    key = join_key(got.preset, got.inputs, got.source.identity)
    assert store.find_joinable(key) is None
    assert store.join_or_bump(key, "bob-desk", "bob@desk", 0, at(701)) is None


def test_recent_and_samples_leave_out_retried_attempts(store):
    j = make(store, retry=RetryPolicy(75, 60, 2))
    run(store, j.id)
    nxt = retry_once(store, j.id, at(30))
    store.promote_due(at(100))
    run(store, nxt, at(100))
    assert store.finish(nxt, SUCCEEDED, now=at(160), exit_code=0)
    assert [x.id for x in store.list_recent(8)] == [nxt]
    assert [r.state for r in store.list_sample_rows(T - timedelta(days=1))] == [SUCCEEDED]
    assert [x.id for x in store.list_samples(T - timedelta(days=1))] == [nxt]


def test_the_chain_head_is_the_last_attempt(store):
    j = make(store)
    run(store, j.id)
    second = retry_once(store, j.id, at(0))
    assert store.chain_head(store.get_job(j.id)).id == second
    assert store.chain_head(store.get_job(second)).id == second
    assert [x.id for x in store.chain_jobs(j.id)] == [j.id, second]
    plain = make(store, retry=None, inputs={"build_name": "9"})
    assert store.chain_head(plain).id == plain.id


def test_a_retried_attempt_does_not_go_on_the_shelf(store):
    """보관은 사슬의 마지막 시도만(결정 228) — 원격 잡이 끝을 알리기 전에 세운 보관 요청을
    접는다."""
    store.shelf_enabled = True
    j = make(store, keep=True)
    run(store, j.id)
    conn = store._conn()
    conn.execute(
        "INSERT INTO shelf_items (job_id, state, preset, inputs_json, requested_at) "
        "VALUES (?, 'pending', 'golive', '{}', ?)",
        (j.id, T.timestamp()),
    )
    nxt = retry_once(store, j.id, at(5))
    left = conn.execute("SELECT count(*) FROM shelf_items WHERE job_id=?", (j.id,)).fetchone()
    assert left[0] == 0
    assert store.get_job(nxt).keep is True


def test_a_root_whose_chain_is_alive_is_not_deleted(store):
    """뿌리에 취소 capability 가 있다(결정 228) — 사슬이 살아 있으면 오래돼도 지우지 않는다."""
    j = make(store)
    run(store, j.id)
    retry_once(store, j.id, at(0))
    store.mark_artifacts_purged([j.id], at(1))
    assert store.delete_old_jobs(at(400 * 86400)) == 0
    assert store.get_job(j.id) is not None


# ── 종료 규칙 (`outcome_for`) ────────────────────────────────────────────────


def frozen(store: Store, **kw: Any):
    j = make(store, **kw)
    run(store, j.id)
    return store.get_job(j.id)


def decide(job, rc):
    return outcome_for(job, [], started=T, finished=at(5), rc=rc)


def test_s7_exit_75_without_a_retry_declaration_is_a_plain_failure(store):
    """rcm-ci 의 75 는 인프라 실패다(§3 #1) — 선언이 없으면 지금처럼 `failed`."""
    job = frozen(store, retry=None)
    oc = decide(job, 75)
    assert oc.state == FAILED and oc.code == "exit_code"


def test_exit_75_with_a_declaration_retries_until_the_last_attempt(store):
    job = frozen(store)
    assert decide(job, 75).state == RETRIED
    assert decide(job, 1).state == FAILED
    assert decide(job, 0).state == SUCCEEDED
    last = job.__class__(**{**job.__dict__, "attempt": 3})
    oc = decide(last, 75)
    assert oc.state == EXHAUSTED
    assert oc.code == "retries_exhausted" and oc.args == {"code": 75, "attempts": 3}


def test_a_submission_that_turned_retry_off_fails_on_75(store):
    job = frozen(store, retry_disabled=True)
    assert decide(job, 75).state == FAILED


def test_a_cancelling_job_that_exits_75_is_cancelled_not_retried(store):
    job = frozen(store)
    store.request_cancel(job.id, "alice-laptop", at(1), 5)
    oc = decide(store.get_job(job.id), 75)
    assert oc.state == CANCELLED


# ── 서버 (원격 finish · 제출 · 취소 · health) ────────────────────────────────


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    s.cfg.presets = (
        *s.cfg.presets,
        parse_preset(
            sh(
                "golive",
                "exit 75",
                source_modes=["git_ref"],
                repo="app",
                retry={"on_exit": 75, "interval_seconds": 60, "max_attempts": 3},
            )
        ),
    )
    yield s
    s.close()


def submit(srv, token: str = "alice", **extra: Any) -> tuple[int, dict[str, Any]]:
    body = {
        "preset": "golive",
        "inputs": {},
        "source": {"mode": "git_ref", "ref": GIT_SHA},
        "requester_label": f"{token}@host",
        **extra,
    }
    return srv.req("POST", "/jobs", token=token, json_body=body)


def remote_attempt(srv, jid: int, rc: int, outcome: str = "failed") -> int:
    srv.registered("build-02")
    assert srv.claimed("build-02") == jid
    status, body = srv.finish("build-02", jid, outcome, exit_code=rc)
    assert status == 200, body
    return status


def test_submission_freezes_the_preset_policy_and_retry_false_turns_it_off(srv):
    status, resp = submit(srv)
    assert status == 201, resp
    job = srv.store.get_job(resp["job_id"])
    assert (job.retry_on_exit, job.retry_interval_seconds, job.retry_max_attempts) == (75, 60, 3)
    assert job.chain_id == job.id and job.retry_disabled is False
    status, resp = submit(srv, retry=False, join=False)
    assert status == 201 and srv.store.get_job(resp["job_id"]).retry_disabled is True
    status, resp = submit(srv, retry="no")
    assert status == 400 and "retry must be true or false" in resp["error"]


def test_a_remote_75_is_retried_and_the_job_document_names_the_next_attempt(srv):
    _, resp = submit(srv)
    root = resp["job_id"]
    remote_attempt(srv, root, 75)
    status, doc = srv.req("GET", f"/jobs/{root}", token="alice")
    assert status == 200
    assert doc["state"] == RETRIED and doc["exit_code"] == 75
    nxt = doc["retried_as"]
    assert isinstance(nxt, int) and doc["chain_id"] == root and doc["attempt"] == 1
    assert doc["max_attempts"] == 3
    status, nd = srv.req("GET", f"/jobs/{nxt}", token="alice")
    assert nd["state"] == SCHEDULED and nd["attempt"] == 2 and nd["retry_of"] == root
    assert nd["chain_id"] == root and nd["start_deadline"] is None


def test_s20_a_remote_75_after_a_cancel_closes_cancelled_with_no_next_attempt(srv):
    _, resp = submit(srv)
    root = resp["job_id"]
    srv.registered("build-02")
    assert srv.claimed("build-02") == root
    status, _ = srv.req("POST", f"/jobs/{root}/cancel", token="alice", json_body={})
    assert status == 200 and srv.store.get_job(root).state == CANCELLING
    status, body = srv.finish("build-02", root, "failed", exit_code=75)
    assert status == 200, body
    assert srv.store.get_job(root).state == CANCELLED
    assert srv.store.list_scheduled() == []


def test_s26_cancelling_the_first_id_stops_the_live_head(srv):
    _, resp = submit(srv)
    root = resp["job_id"]
    remote_attempt(srv, root, 75)
    head = srv.store.get_job(root).retried_as
    status, body = srv.req("POST", f"/jobs/{root}/cancel", token="alice", json_body={})
    assert status == 200, body
    assert body["job_id"] == head and body["state"] == CANCELLED
    assert srv.store.get_job(head).state == CANCELLED
    assert srv.store.get_job(root).state == RETRIED  # 끝난 시도는 그대로


def test_s26_the_submission_cancel_token_of_the_root_reaches_the_head(tmp_path):
    s = WorkerServer(tmp_path, cancel_requires_submission_token=True)
    try:
        s.cfg.presets = (
            *s.cfg.presets,
            parse_preset(sh("golive", "exit 75", source_modes=["git_ref"], repo="app", retry={})),
        )
        _, resp = submit(s)
        root, token = resp["job_id"], resp["submission"]["cancel_token"]
        remote_attempt(s, root, 75)
        head = s.store.get_job(root).retried_as
        # 뿌리 id 와 뿌리의 비밀로 머리를 멈춘다(비밀이 없으면 이 서버는 403 이다)
        status, body = s.req("POST", f"/jobs/{root}/cancel", token="alice", json_body={})
        assert status == 403, body
        status, body = s.req(
            "POST", f"/jobs/{root}/cancel", token="alice", json_body={"cancel_token": token}
        )
        assert status == 200, body
        assert body["job_id"] == head
        assert s.store.get_job(head).state == CANCELLED
    finally:
        s.close()


def test_recent_leaves_out_retried_attempts_in_the_status_document(srv):
    _, resp = submit(srv)
    root = resp["job_id"]
    remote_attempt(srv, root, 75)
    status, doc = srv.req("GET", "/api/status", token="alice")
    recent = [r["id"] for p in doc["pools"] for r in p["recent"]]
    assert root not in recent
    scheduled = [r for p in doc["pools"] for r in p["scheduled"]]
    assert [r["attempt"] for r in scheduled] == [2]
    golive = next(p for p in doc["presets"] if p["name"] == "golive")
    assert golive["retry"] == {"on_exit": 75, "interval_seconds": 60, "max_attempts": 3}


def test_health_raises_the_client_floor_when_a_preset_retries(srv):
    """결정 230 — 옛 클라이언트의 `rcm wait` 는 `retried` 를 몰라 끝없이 기다린다."""
    status, h = srv.req("GET", "/api/health")
    assert h["min_client_version"] == "0.10.0"
    assert h["retry_min_client_version"] == "0.10.0"
    srv.cfg.presets = tuple(p for p in srv.cfg.presets if p.retry is None)
    _, h = srv.req("GET", "/api/health")
    assert h["min_client_version"] == "0.2.0" and h["retry_min_client_version"] is None


def test_a_retry_false_submission_does_not_join_a_retrying_job(srv):
    _, first = submit(srv)
    status, second = submit(srv, token="bob", retry=False)
    assert status == 201 and second["job_id"] != first["job_id"]
    _, third = submit(srv, token="bob")
    assert third["joined"] is True and third["job_id"] == first["job_id"]


# ── 사슬 기준 표식 (결정 233) ────────────────────────────────────────────────

LOGIN = "login::Sign in on the build machine"


def test_chain_action_takes_the_last_mark_of_the_most_recent_attempt_that_ran():
    from remote_ci_monitor.core.model import Action
    from remote_ci_monitor.core.progress import chain_action

    a1, a2 = Action("login", "first", T), Action("login", "second", at(5))
    # 돈 시도의 표식 → 다음 시도가 예약으로 기다리는 동안에도 남는다
    assert chain_action([(1, True, True), (2, False, False)], {1: a1}) == a1
    # 다음 시도가 돌기 시작했지만 아직 표식이 없다 → 앞 시도의 표식이 남는다
    assert chain_action([(1, True, True), (2, True, False)], {1: a1}) == a1
    # 다음 시도가 자기 표식을 찍었다 → 그것
    assert chain_action([(1, True, True), (2, True, False)], {1: a1, 2: a2}) == a2
    # 표식 없이 끝까지 돈 시도가 나왔다 → 지운다
    assert chain_action([(1, True, True), (2, True, True), (3, False, False)], {1: a1}) is None
    # 돌지 않고 끝난 시도(시작 전 취소)는 건너뛴다
    assert chain_action([(1, True, True), (2, False, True)], {1: a1}) == a1
    assert chain_action([], {}) is None


def test_the_store_gives_the_chain_mark_to_the_waiting_and_running_attempts(store):
    j = make(store)
    run(store, j.id)
    store.add_marker(j.id, "action", LOGIN, at(3))
    second = retry_once(store, j.id, at(5))
    got = store.chain_actions([store.get_job(second)])
    assert got[second].code == "login" and got[second].note == "Sign in on the build machine"
    store.promote_due(at(700))
    run(store, second, at(700))
    assert store.chain_actions([store.get_job(second)])[second].code == "login"
    # 둘째 시도가 표식 없이 75 로 끝났다 → 셋째 시도에는 표식이 없다
    third = retry_once(store, second, at(760))
    assert store.chain_actions([store.get_job(third)]) == {}
    plain = make(store, retry=None, inputs={"build_name": "9"})
    assert store.chain_actions([plain]) == {}


def test_the_status_document_keeps_needs_action_between_attempts(srv):
    _, resp = submit(srv)
    root = resp["job_id"]
    srv.registered("build-02")
    assert srv.claimed("build-02") == root
    srv.store.add_marker(root, "action", LOGIN, srv.clock())
    status, body = srv.finish("build-02", root, "failed", exit_code=75)
    assert status == 200, body
    second = srv.store.get_job(root).retried_as
    status, doc = srv.req("GET", "/api/status", token="alice")
    row = next(r for p in doc["pools"] for r in p["scheduled"] if r["id"] == second)
    assert row["reason"] == "needs_action"
    assert row["action"]["code"] == "login"
    _, one = srv.req("GET", f"/jobs/{second}", token="alice")
    assert one["reason"] == "needs_action" and one["action"]["code"] == "login"
    # 승격된 뒤 큐에서 기다리는 행에도 남는다
    srv.clock.advance(61)
    srv.app.tick_schedule()
    srv.app._mark_dirty()
    _, doc = srv.req("GET", "/api/status", token="alice")
    row = next(r for p in doc["pools"] for r in p["queue"] if r["id"] == second)
    assert row["reason"] == "needs_action" and row["action"]["code"] == "login"


def test_a_scheduled_row_without_a_chain_mark_has_null_reason_and_action(srv):
    _, resp = submit(srv)
    root = resp["job_id"]
    remote_attempt(srv, root, 75)
    second = srv.store.get_job(root).retried_as
    _, doc = srv.req("GET", "/api/status", token="alice")
    row = next(r for p in doc["pools"] for r in p["scheduled"] if r["id"] == second)
    assert row["reason"] is None and row["action"] is None
