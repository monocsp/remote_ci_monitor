"""종료 잡의 `step_timeline`(M5j G1) — 저장된 마커로 스텝별 시간을 다시 낸다.

명세는 `docs/gate-optimization-workplan.md` §2 G1 · §4 결정 84, Codex 리뷰의 G1 충돌 1~4
(`docs/reviews/2026-09-10-codex-gate-optimization-design.md`). **구현보다 먼저 썼다 — 빨간 것이
정상이다.**

- 서버는 `tests/test_worker_api.py` 의 `WorkerServer` — in-process HTTP + **주입한 시계**. 마커는
  원격 워커 경로(`POST /worker/jobs/{id}/log`)로 넣어 수신 시각이 시계 값이 되게 한다. 그래서
  「돌던 중의 `progress`」와 「끝난 뒤의 `step_timeline`」을 같은 잡에서 초 단위로 견줄 수 있다.
- `rcm run`·`rcm wait` 은 진짜 로컬 워커가 있는 `test_server.Server(workers=True)` 에 진짜 git
  체크아웃을 올린다 — 최종 JSON 이 문서를 그대로 복사하는지는 실제 경로로만 알 수 있다.
- 문서 잠금은 `tests/test_docs_step_timeline.py` 에 따로 있다 — `scripts/mutcheck.py` 의 사본에는
  `docs/` 가 없어서 여기 두면 대조군부터 빨개진다.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from gitrepo import commit, git
from remote_ci_monitor import cli
from remote_ci_monitor.core.model import CANCELLED, FAILED
from remote_ci_monitor.core.progress import MAX_FAIL_NAMES
from test_server import Server
from test_worker_api import T0, WorkerServer

#: 타임라인 객체와 스텝 하나의 키 집합 — 결정 84 의 모양 그대로. 재구성한 `failed_step` 은 없다.
TIMELINE_KEYS = {"timing", "steps_total", "steps_total_partial", "steps"}
STEP_KEYS = {"index", "name", "started_at", "ended_at", "seconds", "ok"}
#: 마커가 없거나 시작 전에 끝난 잡의 타임라인. `null` 이 아니라 **조회에 성공한 빈 목록**이다.
EMPTY = {"timing": "as_received", "steps_total": None, "steps_total_partial": True, "steps": []}

WORKER = "build-02"


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    s.registered(WORKER)
    yield s
    s.close()


def tree(n: int) -> str:
    """합류를 피하려고 잡마다 다른 트리 해시를 준다(64 hex)."""
    return f"{n:064x}"


def running(srv: WorkerServer, n: int, *lines: tuple[float, bytes]) -> int:
    """claim 한 잡에 (몇 초 뒤, 로그 바이트) 를 차례로 흘린다. 시작 시각은 claim 시각이다."""
    jid = srv.queued_job(tree_hash=tree(n))
    assert srv.claimed(WORKER) == jid
    for seconds, data in lines:
        srv.clock.advance(seconds)
        status, body = srv.log(WORKER, jid, data)
        assert status == 200, body
    return jid


def finish(srv: WorkerServer, jid: int, outcome: str, *, after: float, exit_code: Any) -> None:
    srv.clock.advance(after)
    status, body = srv.finish(WORKER, jid, outcome, exit_code=exit_code)
    assert status == 200, body


def view(srv: WorkerServer, jid: int) -> dict[str, Any]:
    return srv.view(jid, token="alice")


# ── 모양 · 값 ────────────────────────────────────────────────────────────────


def test_a_finished_job_keeps_the_step_seconds_it_showed_while_running(srv):
    """G1 의 요청 그대로 — 끝난 뒤에도 돌던 중의 스텝별 초가 그대로 있다.

    닫힌 스텝은 `progress.steps[]` 와 **같은 값**이고, 열려 있던 마지막 스텝은 종료 시각에
    닫힌다(`ended_at == finished_at`). `progress` 자체는 종료 잡에 붙지 않는다(지금과 같다).
    """
    jid = running(
        srv,
        1,
        (5, b"::rcm::steps::3\n::rcm::step::analyze\n"),
        (30, b"::rcm::step::test\n"),
        (7, b"::rcm::step::package\n"),
    )
    srv.clock.advance(2)
    live = view(srv, jid)["progress"]
    assert [s["seconds"] for s in live["steps"]] == [30, 7, 2]
    finish(srv, jid, "succeeded", after=11, exit_code=0)
    doc = view(srv, jid)
    assert doc["state"] == "succeeded" and "progress" not in doc
    tl = doc["step_timeline"]
    assert set(tl) == TIMELINE_KEYS, sorted(set(tl) ^ TIMELINE_KEYS)
    assert tl["timing"] == "as_received"
    assert tl["steps_total"] == 3 and tl["steps_total_partial"] is False
    closed = [{k: s[k] for k in STEP_KEYS} for s in live["steps"][:2]]
    assert tl["steps"][:2] == closed
    last = tl["steps"][2]
    assert set(last) == STEP_KEYS
    assert last["name"] == "package" and last["index"] == 3
    assert last["ended_at"] == doc["finished_at"] and last["seconds"] == 13
    assert last["ok"] is True  # exit 0 — 여기까지 무사히 왔다
    assert doc["job_seconds"] == 55


def test_a_job_without_markers_has_an_empty_timeline_not_null(srv):
    """마커 조회는 됐고 마커가 없다 — `steps: []`, `steps_total: null`(0 이 아니다)."""
    jid = running(srv, 2, (3, b"plain output, no markers\n"))
    finish(srv, jid, "succeeded", after=4, exit_code=0)
    doc = view(srv, jid)
    assert doc["step_timeline"] == EMPTY
    assert "step_timeline_error_code" not in doc


def test_a_job_that_failed_before_it_started_has_an_empty_timeline(srv):
    """`started_at` 이 없는 종료 잡(프리셋 소멸·업로드 포기) — 조회에 성공한 빈 타임라인이다."""
    jid = srv.new_job(tree_hash=tree(3))
    ok = srv.store.finish(jid, FAILED, now=T0 + timedelta(seconds=10), summary="preset gone")
    assert ok is True
    srv.app._mark_dirty()
    doc = view(srv, jid)
    assert doc["state"] == FAILED and doc["started_at"] is None
    assert doc["step_timeline"] == EMPTY
    assert "step_timeline_error_code" not in doc


def test_a_store_error_reading_markers_is_null_plus_a_code(srv, monkeypatch):
    """fail-open 금지 — 못 읽은 것을 빈 목록으로 뭉개지 않는다. 잡 문서 자체는 산다."""
    jid = running(srv, 4, (5, b"::rcm::step::a\n"))
    finish(srv, jid, "failed", after=5, exit_code=1)

    def boom(*a: Any, **kw: Any):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(srv.store, "markers", boom)
    doc = view(srv, jid)
    assert doc["state"] == FAILED and doc["id"] == jid
    assert doc["step_timeline"] is None
    assert doc["step_timeline_error_code"] == "database_unavailable"
    assert doc["last_step"] == "a"  # 잡 행의 칸은 그대로 온다
    assert doc["failures"] == []  # 대장 질의는 별개다 — 같이 죽지 않는다


def test_the_stored_failed_step_and_last_step_are_never_reconstructed(srv):
    """Codex 충돌 4 · M5h 결정 63/64 · v16 불변식 — 타임라인은 시간과 `ok` 만 말한다.

    마커는 `a` 를 실패로 선언했지만 잡 행은 v16 이 옮긴 모양(`failed_step` 없음, `last_step` 만)
    이다. 문서는 **저장된 값**을 그대로 싣고, 타임라인 안에도 실패 라벨 키는 없다.
    """
    jid = running(
        srv,
        5,
        (5, b"::rcm::step::a\n"),
        (10, b"::rcm::step::b\n::rcm::fail::a\n"),
    )
    finish(srv, jid, "failed", after=5, exit_code=1)
    assert view(srv, jid)["failed_step"] == "a"  # 선언된 것이라 워커 경로는 적는다
    # v16 이 옛 추론 라벨을 옮긴 잡의 모양을 만든다 — 대장 행 없이 last_step 만 남는다
    conn = srv.store._conn()
    conn.execute("UPDATE jobs SET failed_step=NULL, last_step='b' WHERE id=?", (jid,))
    conn.execute("DELETE FROM job_failures WHERE job_id=?", (jid,))
    srv.app._mark_dirty()
    doc = view(srv, jid)
    assert doc["failed_step"] is None and doc["last_step"] == "b"
    tl = doc["step_timeline"]
    assert [(s["name"], s["ok"]) for s in tl["steps"]] == [("a", False), ("b", None)]
    assert "failed_step" not in tl and "last_step" not in tl


def test_a_following_step_closes_the_previous_one_as_ok_and_failure_is_never_inferred(srv):
    """Codex 충돌 2 — `ok` 는 `progress_for_job` 이 오늘 내는 값 그대로다.

    다음 `step` 이 앞 스텝을 `true` 로 닫고, 실패한 잡의 마지막 열린 스텝은 `null`(모른다)이다.
    """
    jid = running(srv, 6, (5, b"::rcm::step::a\n"), (10, b"::rcm::step::b\n"))
    finish(srv, jid, "failed", after=5, exit_code=2)
    doc = view(srv, jid)
    assert doc["failed_step"] is None and doc["last_step"] == "b"
    assert [(s["name"], s["ok"], s["seconds"]) for s in doc["step_timeline"]["steps"]] == [
        ("a", True, 10),
        ("b", None, 5),
    ]


def test_a_cancelled_job_closes_its_last_step_at_finished_at_without_a_verdict(srv):
    """취소는 사람이 세운 것이다 — 마지막 스텝은 종료 시각에 닫히되 `ok` 가 False 가 아니다.

    결정 64 대로 `failed_step`·`last_step` 은 둘 다 없다.
    """
    jid = running(srv, 7, (5, b"::rcm::step::a\n"), (10, b"::rcm::step::b\n"))
    status, _ = srv.cancel(jid)
    assert status == 200
    finish(srv, jid, "cancelled", after=3, exit_code=None)
    doc = view(srv, jid)
    assert doc["state"] == CANCELLED
    assert doc["failed_step"] is None and doc["last_step"] is None
    last = doc["step_timeline"]["steps"][-1]
    assert last["name"] == "b" and last["ended_at"] == doc["finished_at"]
    assert last["seconds"] == 3 and last["ok"] is not False


def test_a_running_job_keeps_progress_and_has_no_timeline(srv):
    """활성 잡은 오늘과 같다 — `progress` 는 있고 `step_timeline` 은 없다."""
    jid = running(srv, 8, (5, b"::rcm::step::a\n"))
    doc = view(srv, jid)
    assert doc["state"] == "running" and doc["progress"]["steps"]
    assert "step_timeline" not in doc and "step_timeline_error_code" not in doc


def test_api_status_recent_rows_carry_no_timeline_and_the_schema_version_stays(srv):
    """`/api/status` 는 건드리지 않는다 — 가장 뜨거운 요청에 마커 질의를 얹지 않는다."""
    jid = running(srv, 9, (5, b"::rcm::step::a\n"))
    finish(srv, jid, "succeeded", after=5, exit_code=0)
    status, doc = srv.req("GET", "/api/status")
    assert status == 200
    recents = [row for pool in doc["pools"] for row in (pool["recent"] or [])]
    assert [r["id"] for r in recents] == [jid]
    assert "step_timeline" not in recents[0]
    assert doc["schema_version"] == 1  # 키를 더하되 값을 안 바꾼다


# ── `failures[].step` — 타임라인의 모든 스텝 이름 ─────────────────────────────


def test_failures_step_matches_a_middle_step_name(srv):
    """`b` 는 `failed_step`(a) 도 `last_step`(c) 도 아니지만 스텝이다 — 이제 `step: true`."""
    jid = running(
        srv,
        10,
        (5, b"::rcm::step::a\n::rcm::step-end::fail\n"),
        (10, b"::rcm::step::b\n"),
        (10, b"::rcm::step::c\n::rcm::fail::b\n::rcm::fail::port_test.dart\n"),
    )
    finish(srv, jid, "failed", after=5, exit_code=1)
    doc = view(srv, jid)
    assert doc["failed_step"] == "a" and doc["last_step"] == "c"
    assert [(f["name"], f["step"]) for f in doc["failures"]] == [
        ("b", True),
        ("port_test.dart", False),
        ("a", True),
    ]
    assert [s["name"] for s in doc["step_timeline"]["steps"]] == ["a", "b", "c"]


def test_the_fail_name_cap_does_not_shorten_the_timeline(srv):
    """실패 이름 상한(100)은 대장의 상한이지 스텝의 상한이 아니다 — 스텝은 전부 남는다."""
    names = "".join(f"::rcm::fail::unit{i:03d}\n" for i in range(MAX_FAIL_NAMES))
    jid = running(
        srv,
        11,
        (5, b"::rcm::step::a\n"),
        (5, b"::rcm::step::b\n" + names.encode()),
        (5, b"::rcm::step::c\n::rcm::fail::b\n"),  # 상한 뒤의 선언 — 대장에는 못 들어간다
    )
    finish(srv, jid, "failed", after=5, exit_code=1)
    doc = view(srv, jid)
    assert doc["failures_truncated"] is True and len(doc["failures"]) == MAX_FAIL_NAMES
    assert all(f["step"] is False for f in doc["failures"])
    assert [s["name"] for s in doc["step_timeline"]["steps"]] == ["a", "b", "c"]


# ── `rcm run` · `rcm wait` — 최종 JSON 이 문서를 그대로 복사한다(계약) ─────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def last_json(out: str) -> Any:
    import json

    lines = [ln for ln in out.strip().splitlines() if ln[:1] == "{"]
    assert lines, f"no JSON line in stdout: {out!r}"
    return json.loads(lines[-1])


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)

    def use(server: Server, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def live(tmp_path):
    """진짜 로컬 워커 — `ok` 프리셋은 `::rcm::step::a` 하나를 찍고 0 으로 끝난다."""
    s = Server(tmp_path, workers=True)
    yield s
    s.close()


@pytest.fixture
def checkout(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", cwd=root)
    commit(root, "hello.txt", "hello\n", "first")
    return root


def test_rcm_run_and_rcm_wait_json_carry_the_step_timeline(live, env, checkout, capsys):
    """Codex 충돌 3 — wait 은 문서를 복사하므로 키가 실린다. 그것을 **계약**으로 잠근다."""
    env(live)
    code, out, err = run(capsys, ["run", "ok", "--dir", str(checkout)])
    assert code == 0, err
    doc = last_json(out)
    assert doc["state"] == "succeeded" and doc["wait_exit_code"] == 0
    tl = doc["step_timeline"]
    assert set(tl) == TIMELINE_KEYS
    assert [s["name"] for s in tl["steps"]] == ["a"]
    assert tl["steps"][0]["ended_at"] == doc["finished_at"]
    assert tl["steps"][0]["ok"] is True and tl["steps"][0]["seconds"] >= 0
    code, out, _err = run(capsys, ["wait", "--job", str(doc["id"])])
    assert code == 0
    assert last_json(out)["step_timeline"] == tl


def test_rcm_wait_exit_code_follows_the_state_when_the_timeline_cannot_be_read(
    live, env, checkout, capsys, monkeypatch
):
    """M5j 검증 G1.5 — 타임라인은 부가 정보다. 마커 조회가 깨져도 `rcm wait` 는 3(모른다)이 아니라
    상태대로 끝나고(`bad` 는 exit 2 로 실패 → 1), JSON 은 `null` + 코드를 그대로 싣는다.
    """
    env(live)
    code, out, err = run(capsys, ["run", "bad", "--dir", str(checkout)])
    assert code == 1, err
    jid = last_json(out)["id"]

    def boom(*a: Any, **kw: Any):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(live.store, "markers", boom)
    code, out, _err = run(capsys, ["wait", "--job", str(jid)])
    assert code == 1
    doc = last_json(out)
    assert doc["state"] == FAILED and doc["wait_exit_code"] == 1
    assert doc["step_timeline"] is None
    assert doc["step_timeline_error_code"] == "database_unavailable"
    assert doc["last_step"] == "t"
# ── M5l S11 (구현 리뷰 pr-108 B-1 · B-2) ─────────────────────────────────────


def test_a_broken_marker_row_is_null_plus_a_code_not_a_500(srv):
    """`Store.markers()` 는 행마다 payload 를 JSON 으로 푼다 — 깨진 행은 `sqlite3.Error` 가 아니라
    `ValueError`/`KeyError` 다. 그것도 「못 읽었다」이지 500 이 아니다(형제 경로와 같은 폭)."""
    jid = running(srv, 5, (5, b"::rcm::step::a\n"))
    finish(srv, jid, "failed", after=5, exit_code=1)
    raw = sqlite3.connect(srv.store.path)
    try:
        raw.execute("UPDATE events SET payload='not json' WHERE job_id=? AND kind='marker'", (jid,))
        raw.commit()
    finally:
        raw.close()
    srv.store.close()  # 스레드 로컬 연결이 옛 페이지를 보지 않게
    doc = view(srv, jid)
    assert doc["state"] == FAILED and doc["id"] == jid
    assert doc["step_timeline"] is None
    assert doc["step_timeline_error_code"]  # 종류 코드만 — 문장·SQL 없음
    assert "not json" not in str(doc)


def test_a_finished_job_without_finished_at_is_unknown_not_a_moving_timeline(srv):
    """옛 DB 의 흔적(`finished_at` NULL)에 지금 시각으로 물러서면 요청마다 초가 자란다 —
    「모른다」로 낸다."""
    jid = running(srv, 6, (5, b"::rcm::step::a\n"))
    finish(srv, jid, "failed", after=5, exit_code=1)
    raw = sqlite3.connect(srv.store.path)
    try:
        raw.execute("UPDATE jobs SET finished_at=NULL WHERE id=?", (jid,))
        raw.commit()
    finally:
        raw.close()
    srv.store.close()
    first, second = view(srv, jid), view(srv, jid)
    assert first["step_timeline"] is None and first["step_timeline_error_code"] == "no_finished_at"
    assert second == first


def test_the_finished_document_carries_concurrent_at_start(srv):
    """두 레인 실험(G3 · #106)의 측정 재료 — v10 열을 종료 문서에 그대로(모르면 null)."""
    jid = running(srv, 7, (5, b"::rcm::step::a\n"))
    finish(srv, jid, "succeeded", after=5, exit_code=0)
    doc = view(srv, jid)
    assert "concurrent_at_start" in doc
    assert doc["concurrent_at_start"] is None or doc["concurrent_at_start"] >= 1
