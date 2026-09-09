"""서버(M5h) — `GET /jobs/{id}` 의 실패 이력 · 설정 두 키 · 404 의 길 안내.

명세는 `docs/m5h-implementation.md` §2.3(`_with_failures`) · §2.4(설정) · §3(404 `hint`).
시나리오는 `docs/m5h-test-scenarios-b.md`. **구현 전이라 빨간 것이 정상이다.**

- 서버는 `tests/test_worker_api.py` 의 `WorkerServer` — in-process HTTP + **주입한 시계**다.
  로컬 워커 스레드는 안 띄운다. sleep 도 벽시계 대기도 없다.
- 종료 잡은 HTTP 로 만들고(`new_job`) 저장소에 직접 끝낸다(`store.finish`) — 잡을 진짜로
  돌리지 않고도 「끝난 잡의 문서」를 볼 수 있다.
- `core/failures.py` 는 역할 A 의 파일이다. 여기서는 import 하지 않고 **서버가 낸 JSON** 의
  키·값만 본다.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import ConfigError, ServerSection, load_server_config
from remote_ci_monitor.core.model import CANCELLED, FAILED, LOST, SUCCEEDED, TIMED_OUT
from test_worker_api import T0, WorkerServer

#: #162 의 마지막 머리말. 「끝났을 때 어디였나」만 말하고 인과를 주장하지 않는다.
STEP_162 = "build web (release — 셰이더 impellerc 컴파일 회귀 포함)"

#: `failures[]` 항목 하나의 키 집합(§2.2). 화면 셋이 전부 이것만 보고 그린다.
FAILURE_KEYS = {
    "name",
    "step",
    "seen",
    "window",
    "window_unnamed",
    "first_seen_job_id",
    "last_seen_job_id",
    "verdict",
}

#: §3 이 못 박은 두 문구. 글자 하나까지 계약이다.
HINT_JOB = (
    "job #162 is GET /jobs/162 · its log is GET /jobs/162/log "
    "with that job's token (try: rcm logs 162)"
)
HINT_ROUTES = (
    "routes: GET /api/status · GET /api/health · GET /jobs/<id> · GET /jobs/<id>/log · POST /jobs"
)


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    yield s
    s.close()


def tree(n: int) -> str:
    """합류를 피하려고 잡마다 다른 트리 해시를 준다(64 hex)."""
    return f"{n:064x}"


def terminal(
    srv: WorkerServer,
    *,
    n: int,
    state: str = FAILED,
    names: tuple[str, ...] = (),
    preset: str = "ok",
    failed_step: str | None = None,
    last_step: str | None = None,
    truncated: bool = False,
) -> int:
    """종료 잡 하나. `n` 이 클수록 나중에 끝난 잡이다(창 순서를 이걸로 만든다)."""
    jid = srv.new_job(preset=preset, tree_hash=tree(n))
    ok = srv.store.finish(
        jid,
        state,
        now=T0 + timedelta(seconds=60 * n),
        exit_code=0 if state == SUCCEEDED else 1,
        failed_step=failed_step,
        last_step=last_step,
        fail_names=list(names),
        fail_truncated=truncated,
    )
    assert ok is True
    srv.app._mark_dirty()  # 저장소를 직접 고쳤다 — 상태 캐시가 모른다
    return jid


def series(srv: WorkerServer, *specs: tuple[str, tuple[str, ...]], preset: str = "ok") -> int:
    """`(state, names)` 를 오래된 것부터 종료 잡으로. **마지막** 잡의 id 를 돌려준다."""
    last = 0
    for n, (state, names) in enumerate(specs, start=1):
        last = terminal(srv, n=n, state=state, names=names, preset=preset)
    return last


def view(srv: WorkerServer, job_id: int) -> dict[str, Any]:
    status, doc = srv.req("GET", f"/jobs/{job_id}", token="alice")
    assert status == 200, doc
    return doc


def only(doc: dict[str, Any]) -> dict[str, Any]:
    assert len(doc["failures"]) == 1, doc["failures"]
    return doc["failures"][0]


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "rcm.toml"
    p.write_text(text)
    return p


# ══ E. `GET /jobs/{id}` — 종료한 실패 잡에만 (§2.3) ═══════════════════════════


def test_a_failed_job_carries_failures_and_failures_truncated(srv):
    """종료 잡 갈래에만 붙는다. 항목 하나의 키 집합은 여덟 개로 고정이다(§2.2)."""
    jid = series(srv, (FAILED, ("test",)), (FAILED, ("test",)), (FAILED, ("test", "lint")))
    doc = view(srv, jid)
    assert doc["state"] == FAILED
    assert doc["failures_truncated"] is False
    assert [f["name"] for f in doc["failures"]] == ["test", "lint"]
    for f in doc["failures"]:
        assert set(f) == FAILURE_KEYS, sorted(set(f) ^ FAILURE_KEYS)
    first = doc["failures"][0]
    assert (first["seen"], first["window"], first["window_unnamed"]) == (3, 3, 0)
    assert doc["artifacts"] is not None  # 기존 칸은 손대지 않는다(스키마 v1)


def test_a_timed_out_job_carries_failures(srv):
    """시한에 걸린 잡도 판정을 냈다 — 이름이 있으면 이력이 붙는다."""
    jid = series(srv, (TIMED_OUT, ("test",)), (TIMED_OUT, ("test",)))
    doc = view(srv, jid)
    assert doc["state"] == TIMED_OUT
    assert only(doc)["name"] == "test"
    assert doc["failures_truncated"] is False


def test_a_succeeded_job_has_no_failures_key(srv):
    """성공한 잡에는 대장이 없다 — 빈 배열도 아니고 **키가 없다**."""
    jid = series(srv, (FAILED, ("test",)), (SUCCEEDED, ()))
    doc = view(srv, jid)
    assert doc["state"] == SUCCEEDED
    assert "failures" not in doc and "failures_truncated" not in doc


def test_a_cancelled_job_has_no_failures_key(srv):
    """#176 의 서버 쪽 — 취소된 잡은 아무 판정도 안 냈다."""
    jid = series(srv, (FAILED, ("test",)), (CANCELLED, ()))
    doc = view(srv, jid)
    assert doc["state"] == CANCELLED
    assert "failures" not in doc and "failures_truncated" not in doc


def test_a_lost_job_has_no_failures_key(srv):
    jid = series(srv, (FAILED, ("test",)), (LOST, ()))
    doc = view(srv, jid)
    assert doc["state"] == LOST
    assert "failures" not in doc and "failures_truncated" not in doc


def test_a_job_that_is_still_going_has_no_failures_key(srv):
    """도는 잡·기다리는 잡은 이력을 안 싣는다 — 2초마다 폴링되는 쪽이다(결정 67)."""
    queued = srv.queued_job(tree_hash=tree(50))
    doc = view(srv, queued)
    assert doc["state"] == "queued"
    assert "failures" not in doc and "failures_truncated" not in doc
    uploading = srv.new_job(tree_hash=tree(51))
    doc = view(srv, uploading)
    assert doc["state"] == "uploading"
    assert "failures" not in doc and "failures_truncated" not in doc


def test_step_is_true_only_for_names_that_match_failed_step_or_last_step(srv):
    """종료 잡에는 스텝 목록이 없다 — 그 두 칸으로만 「스텝」과 「단위」를 가른다(§2.3)."""
    jid = terminal(
        srv,
        n=1,
        names=("test", "build web", "port_test.dart"),
        failed_step="test",
        last_step="build web",
    )
    doc = view(srv, jid)
    assert [(f["name"], f["step"]) for f in doc["failures"]] == [
        ("test", True),
        ("build web", True),
        ("port_test.dart", False),
    ]


def test_failures_keeps_the_order_the_job_declared(srv):
    """이름 순이 아니라 잡이 찍은 순서다(§2.2) — 스크립트가 정한 순서가 곧 읽는 순서다."""
    jid = terminal(srv, n=1, names=("zeta", "alpha", "mid"))
    assert [f["name"] for f in view(srv, jid)["failures"]] == ["zeta", "alpha", "mid"]


def test_failures_truncated_reports_the_job_row_flag(srv):
    """`doc["failures_truncated"] = job.fail_truncated` — 분자가 잘렸다는 사실을 숨기지 않는다."""
    jid = terminal(srv, n=1, names=("a",), truncated=True)
    doc = view(srv, jid)
    assert doc["failures_truncated"] is True
    assert [f["name"] for f in doc["failures"]] == ["a"]


def test_the_window_comes_from_failure_window_jobs(tmp_path):
    """창 크기는 설정이 정한다(§2.4) — 20개를 세는 자리에 2를 주면 2를 센다."""
    srv = WorkerServer(tmp_path, failure_window_jobs=2, failure_min_jobs=1)
    try:
        jid = series(srv, *[(FAILED, ("test",))] * 5)
        row = only(view(srv, jid))
        assert (row["seen"], row["window"]) == (2, 2)
    finally:
        srv.close()


def test_a_window_shallower_than_failure_min_jobs_is_unknown(tmp_path):
    """표본이 얕으면 아무 말도 안 한다 — 숫자는 그대로 싣되 판정은 `unknown` 이다."""
    srv = WorkerServer(tmp_path, failure_window_jobs=20, failure_min_jobs=3)
    try:
        jid = series(srv, (FAILED, ("test",)), (FAILED, ("test",)))
        row = only(view(srv, jid))
        assert (row["seen"], row["window"], row["verdict"]) == (2, 2, "unknown")
    finally:
        srv.close()


def test_a_name_seen_once_in_a_deep_window_is_first_seen(srv):
    """같은 key 8회 중 1회 — 판정표(§2.2)는 `seen == 1` 을 `first_seen` 으로 부른다."""
    jid = series(srv, *[(SUCCEEDED, ())] * 7, (FAILED, ("port_test.dart",)))
    row = only(view(srv, jid))
    assert (row["seen"], row["window"], row["window_unnamed"]) == (1, 8, 0)
    assert row["verdict"] == "first_seen"


def test_a_name_in_every_window_job_is_persistent(srv):
    """창의 모든 잡에서 봤으면 간헐이 아니다 — 계속 빨갛다."""
    jid = series(srv, *[(FAILED, ("test",))] * 4)
    row = only(view(srv, jid))
    assert (row["seen"], row["window"], row["verdict"]) == (4, 4, "persistent")


def test_a_name_seen_some_of_the_time_is_intermittent(srv):
    """1 < 본 횟수 < 창의 잡 수 — 왔다 갔다 한다."""
    jid = series(srv, (SUCCEEDED, ()), (FAILED, ("test",)), (SUCCEEDED, ()), (FAILED, ("test",)))
    row = only(view(srv, jid))
    assert (row["seen"], row["window"], row["verdict"]) == (2, 4, "intermittent")


def test_window_unnamed_reaches_the_document(srv):
    """분모의 품질(결정 68) — 이름을 안 찍은 실패 잡 수가 항목마다 실린다."""
    jid = series(srv, (FAILED, ()), (FAILED, ()), (FAILED, ("test",)))
    row = only(view(srv, jid))
    assert (row["seen"], row["window"], row["window_unnamed"]) == (1, 3, 2)


def test_a_failed_job_that_named_nothing_still_carries_an_empty_list(srv):
    """이름이 없으면 `failures` 는 **빈 배열**이다 — 「대장이 없다」와는 다른 말이다."""
    jid = series(srv, (FAILED, ("test",)), (FAILED, ()))
    doc = view(srv, jid)
    assert doc["failures"] == []
    assert doc["failures_truncated"] is False


def test_a_store_error_drops_the_failures_key_instead_of_the_job(srv, monkeypatch):
    """조회 실패는 잡 문서를 죽이지 않는다(§2.3). 빈 배열은 「없다」는 뜻이라 다르다."""
    jid = terminal(srv, n=1, names=("test",), last_step=STEP_162)

    def boom(*a: Any, **kw: Any):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(srv.store, "failure_stats", boom)
    doc = view(srv, jid)
    assert doc["state"] == FAILED and doc["id"] == jid
    assert doc["last_step"] == STEP_162  # 잡 행의 칸은 그대로 온다
    assert "failures" not in doc
    # §2.3 — 둘 다 안 싣는다. 하나만 남으면 「이름이 없는 잡」처럼 읽힌다
    assert "failures_truncated" not in doc


def test_api_status_recent_rows_have_last_step_but_never_failures(srv):
    """`/api/status` 는 건드리지 않는다(결정 67). 최근 행은 `last_step` 만 갖는다."""
    terminal(srv, n=1, names=("test",), last_step=STEP_162)
    status, doc = srv.req("GET", "/api/status")
    assert status == 200
    recents = [row for pool in doc["pools"] for row in (pool["recent"] or [])]
    assert len(recents) == 1
    for row in recents:
        assert "failures" not in row and "failures_truncated" not in row
        assert row["last_step"] == STEP_162
    assert doc["schema_version"] == 1  # 키를 더하되 값을 안 바꾼다


def test_api_status_never_asks_for_the_failure_history(srv, monkeypatch):
    """가장 뜨거운 요청 위에 이력 질의를 얹지 않는다 — 부르면 여기서 500 이 된다."""
    series(srv, (FAILED, ("test",)), (FAILED, ("test",)))

    def boom(*a: Any, **kw: Any):
        raise AssertionError("failure_stats must not run on the /api/status path")

    monkeypatch.setattr(srv.store, "failure_stats", boom)
    status, doc = srv.req("GET", "/api/status")
    assert status == 200, doc


# ══ F. 설정 두 키 (§2.4) ═════════════════════════════════════════════════════


def test_the_two_failure_keys_have_their_documented_defaults():
    s = ServerSection()
    assert s.failure_window_jobs == 20
    assert s.failure_min_jobs == 3


def test_the_two_failure_keys_load_from_a_file(tmp_path):
    text = "[server]\nfailure_window_jobs = 50\nfailure_min_jobs = 5\n"
    cfg = load_server_config(write(tmp_path, text), environ={})
    assert cfg.server.failure_window_jobs == 50 and cfg.server.failure_min_jobs == 5


@pytest.mark.parametrize("value", [0, -1, 501])
def test_a_window_outside_one_to_five_hundred_is_rejected(tmp_path, value):
    """1 이상 500 이하. 0 이면 창이 없고, 500 을 넘으면 종료 잡 하나가 대장을 훑는다."""
    text = f"[server]\nfailure_window_jobs = {value}\n"
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, text), environ={})
    assert "[server] failure_window_jobs must be" in str(e.value)


@pytest.mark.parametrize("value", [1, 500])
def test_the_edges_of_the_window_are_accepted(tmp_path, value):
    text = f"[server]\nfailure_window_jobs = {value}\nfailure_min_jobs = 1\n"
    cfg = load_server_config(write(tmp_path, text), environ={})
    assert cfg.server.failure_window_jobs == value


def test_a_min_jobs_below_one_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, "[server]\nfailure_min_jobs = 0\n"), environ={})
    assert "[server] failure_min_jobs must be" in str(e.value)


def test_a_min_jobs_larger_than_the_window_is_rejected(tmp_path):
    """`min_jobs > window` 면 판정이 영원히 `unknown` 이다 — 조용히 꺼지는 대신 거절한다."""
    text = "[server]\nfailure_window_jobs = 5\nfailure_min_jobs = 6\n"
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, text), environ={})
    assert "[server] failure_min_jobs must be" in str(e.value)
    same = "[server]\nfailure_window_jobs = 5\nfailure_min_jobs = 5\n"
    cfg = load_server_config(write(tmp_path, same), environ={})
    assert cfg.server.failure_min_jobs == 5  # 같은 값은 통과한다


# ══ G. 404 가 길을 알려 준다 (§3) ════════════════════════════════════════════


@pytest.mark.parametrize("path", ["/api/jobs/162", "/logs/162", "/job/162/log"])
def test_a_path_with_a_job_number_names_the_real_route(srv, path):
    """`/api/jobs/…` 는 자연스러운 오추측이다 — 오늘은 그 오추측에 아무 말도 안 한다."""
    status, body = srv.req("GET", path)
    assert status == 404
    assert body["error"] == "not found"
    assert body["hint"] == HINT_JOB


def test_a_path_without_a_number_lists_the_routes(srv):
    status, body = srv.req("GET", "/nope")
    assert status == 404
    assert body["error"] == "not found"
    assert body["hint"] == HINT_ROUTES


def test_the_hint_names_the_number_in_the_path(srv):
    """번호는 경로에서 온다 — 162 를 물었는데 다른 번호를 말하면 길 안내가 아니다."""
    body = srv.req("GET", "/api/jobs/9001")[1]
    assert "job #9001 is GET /jobs/9001" in body["hint"]
    assert "9001" in body["hint"] and "162" not in body["hint"]


def test_a_real_route_is_untouched(srv):
    """별칭은 만들지 않는다(결정 69) — 진짜 경로는 그대로 돌고 `hint` 도 안 붙는다."""
    jid = srv.queued_job(tree_hash=tree(60))
    status, doc = srv.req("GET", f"/jobs/{jid}", token="alice")
    assert status == 200 and "hint" not in doc
    assert srv.req("GET", "/api/status")[0] == 200
    status, body = srv.req("GET", "/jobs/999999", token="alice")
    assert status == 404 and body["error"] == "no such job"


# ── 검증 라운드가 뮤테이션으로 증명한 구멍 ────────────────────────────────────


def test_a_running_jobs_progress_carries_the_last_step_value(srv):
    """§1.4 — 큐 행의 `progress.last_step` 은 **값**이다. 키만 있고 늘 null 이면 소용없다."""
    srv.registered("build-02")
    jid = srv.queued_job(tree_hash=tree(60))
    assert srv.claimed("build-02") == jid
    srv.log("build-02", jid, b"::rcm::step::build\n::rcm::step::test\n")
    doc = view(srv, jid)
    assert doc["state"] == "running"
    assert doc["progress"]["last_step"] == "test"
    assert doc["progress"]["failed_step"] is None


@pytest.mark.parametrize(
    "path,number",
    [("/v1/jobs/162", "162"), ("/api/jobs/9/log", "9"), ("/2/jobs/70", "70")],
)
def test_the_hint_reads_the_job_number_not_the_first_number_in_the_path(
    srv, path: str, number: str
):
    """§3 — `/v1/jobs/162` 의 잡은 #1 이 아니다. 앞의 숫자를 집으면 **틀린 길**을 가르친다."""
    status, body = srv.req("GET", path)
    assert status == 404
    assert f"job #{number} is GET /jobs/{number}" in body["hint"]
    assert f"rcm logs {number}" in body["hint"]
