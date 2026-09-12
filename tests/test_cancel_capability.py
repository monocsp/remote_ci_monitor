"""제출 참여자별 취소 capability(M5j G5 · docs/gate-optimization-workplan.md §2 G5 · 결정 87).

오늘의 `cancel()` 은 「토큰 이름 == 요청자 이름」으로 내 잡을 가린다 — 같은 토큰을 나눠 쓰는 다른
세션이 남의 잡을 지울 수 있고, joiner 표 PK `(job_id, name)` 은 같은 이름의 세션들을 못 가른다.
이제 `POST /jobs` 시도마다(합류 포함) `submission_id` 와 서로 다른 비밀을 한 번 발급한다.
요청자 capability = `cancel_job`, 합류자 = `leave_submission`. 서버는 해시만 둔다.

잠그는 것(Codex 리뷰 11~13 · 대안 G5):
- v17 `submissions` 표 — v16 백업이 DDL 보다 먼저, 백업 실패면 표도 없다(결정 74 경계).
- 서버 키 `cancel_requires_submission_token`(기본 off). off 면 오늘 그대로 + capability 도 받는다.
  on 이면 capability 또는 admin 만 — 비-admin `--force` 는 없다.
- Ctrl-C 계약: 요청자는 detach(취소 없음), 합류자는 자기 `leave_submission` 만 보낸다.
- 비밀은 상태 문서 · 잡 문서 · health · 오류 · 로그 어디에도 없다. 클라이언트 상태 파일은 0600.

구현보다 먼저 썼다(test-first) — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import store as store_mod
from remote_ci_monitor.cli import _version_key, main
from remote_ci_monitor.clientwheel import CANCEL_MIN_CLIENT_VERSION, MIN_CLIENT_VERSION
from remote_ci_monitor.config import ConfigError, load_server_config
from remote_ci_monitor.store import (
    DB_VERSION,
    ROLE_CANCEL_JOB,
    ROLE_LEAVE_SUBMISSION,
    Store,
    StoreError,
)
from test_server import Server
from test_store import at, enqueue, finished_job

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
OTHER_TREE = "ab" * 32
NEEDS_TOKEN = "not your submission"
STATE_FILE = "submissions.json"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def raw(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


def names(path: Path) -> set[str]:
    return {r[0] for r in raw(path, "SELECT name FROM sqlite_master")}


def set_version(path: Path, version: int) -> None:
    c = sqlite3.connect(path)
    try:
        c.execute(f"PRAGMA user_version={version}")
        c.commit()
    finally:
        c.close()


def v16_database(tmp_path: Path) -> Path:
    """v17 로 만든 DB 에서 submissions 표를 떼고 v16 으로 표시한다 — 진짜 v16 DB 의 모양."""
    path = tmp_path / "data" / "rcm.sqlite3"
    store = Store(path)
    enqueue(store, tree="aaaa", now=NOW)
    store.close()
    c = sqlite3.connect(path)
    try:
        c.execute("DROP INDEX IF EXISTS submissions_job")
        c.execute("DROP TABLE IF EXISTS submissions")
        c.execute("PRAGMA user_version=16")
        c.commit()
    finally:
        c.close()
    assert "submissions" not in names(path)
    return path


def cancel(srv: Server, jid: int, token: str, cancel_token: str | None = None):
    body = {} if cancel_token is None else {"cancel_token": cancel_token}
    return srv.req("POST", f"/jobs/{jid}/cancel", token=token, json_body=body)


def submission_rows(srv: Server, jid: int) -> list[tuple]:
    return raw(
        srv.cfg.data_dir / "rcm.sqlite3",
        "SELECT submission_id, role FROM submissions WHERE job_id=? ORDER BY created_at",
        (jid,),
    )


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def strict(tmp_path):
    """키가 켜진 서버 — capability 또는 admin 만 취소한다."""
    s = Server(tmp_path, workers=False, cancel_requires_submission_token=True)
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """`use(srv, token)` — 서버·토큰을 환경변수로. HOME 과 XDG_STATE_HOME 은 임시 디렉터리다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def tree(tmp_path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    return root


@pytest.fixture
def state_file(tmp_path) -> Path:
    return tmp_path / "state" / "rcm" / STATE_FILE


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def last_json(out: str) -> dict:
    return json.loads(out.strip().splitlines()[-1])


# ── 저장소: v17 ──────────────────────────────────────────────────────────────


def test_a_fresh_database_is_v17_with_a_submissions_table_and_its_job_index(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    assert s.user_version() == DB_VERSION == 17
    s.close()
    assert {"submissions", "submissions_job"} <= names(path)
    cols = {r[1] for r in raw(path, "PRAGMA table_info(submissions)")}
    assert cols == {
        "job_id",
        "submission_id",
        "role",
        "capability_hash",
        "created_at",
        "participant",
    }


def test_v16_to_v17_takes_the_boundary_backup_first_and_a_failed_backup_adds_no_table(
    tmp_path, monkeypatch
):
    """결정 74 의 경계: `backup/rcm.sqlite3.v16.bak` 이 DDL 보다 먼저다. 백업을 못 만들면 표를
    더하지 않고 버전도 16 그대로 — 되돌릴 길 없는 일방통행을 시작하지 않는다(Codex 13)."""
    path = v16_database(tmp_path)
    bak = path.parent / "backup" / "rcm.sqlite3.v16.bak"

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store_mod, "_copy_database", refuse)
    with pytest.raises(StoreError, match="backup"):
        Store(path)
    assert raw(path, "PRAGMA user_version") == [(16,)]
    assert "submissions" not in names(path)
    assert not bak.exists()

    monkeypatch.undo()
    s = Store(path)
    assert s.user_version() == 17
    s.close()
    assert "submissions" in names(path)
    assert bak.is_file()
    assert raw(bak, "PRAGMA user_version") == [(16,)]
    assert "submissions" not in names(bak), "the backup was taken after the DDL"
    assert raw(bak, "PRAGMA integrity_check") == [("ok",)]


def test_a_submission_issues_a_secret_the_store_keeps_only_as_a_hash(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    j = enqueue(s, now=NOW)
    sid, tok = s.add_submission(j.id, ROLE_CANCEL_JOB, NOW, participant="alice-laptop")
    sid2, tok2 = s.add_submission(j.id, ROLE_LEAVE_SUBMISSION, at(1), participant="bob-desk")
    assert sid != sid2 and tok != tok2 and len(tok) >= 32
    assert s.submission_role(j.id, tok) == (sid, ROLE_CANCEL_JOB, "alice-laptop")
    assert s.submission_role(j.id, tok2) == (sid2, ROLE_LEAVE_SUBMISSION, "bob-desk")
    assert s.submission_role(j.id, "nope") is None
    assert s.submission_role(j.id + 1, tok) is None  # 다른 잡의 비밀은 이 잡을 못 건드린다
    assert s.remove_submission(sid2) is True
    assert s.submission_role(j.id, tok2) is None
    assert s.remove_submission(sid2) is False
    s.close()
    rows = raw(path, "SELECT submission_id, role, capability_hash FROM submissions")
    assert rows == [(sid, ROLE_CANCEL_JOB, hashlib.sha256(tok.encode()).hexdigest())]
    on_disk = b"".join(
        p.read_bytes() for p in path.parent.iterdir() if p.name.startswith("rcm.sqlite3")
    )
    assert tok.encode() not in on_disk and tok2.encode() not in on_disk


def test_a_cancel_token_never_starts_with_a_dash_so_the_flag_can_carry_it(tmp_path, monkeypatch):
    """`secrets.token_urlsafe` 는 `-` 로 시작할 수 있다(64분의 1). 그러면
    `rcm cancel N --cancel-token <비밀>` 에서 argparse 가 비밀을 옵션으로 읽어 exit 2 다 —
    CI 의 `test_rcm_cancel_sends_the_saved_token_or_the_flag…` 가 그렇게 한 번 빨갛게 됐다."""
    dashed = "-" + "A" * 42
    plain = "B" * 43
    served = iter([dashed, dashed, plain])
    monkeypatch.setattr(store_mod.secrets, "token_urlsafe", lambda n: next(served))
    s = Store(tmp_path / "rcm.sqlite3")
    j = enqueue(s, now=NOW)
    _sid, tok = s.add_submission(j.id, ROLE_CANCEL_JOB, NOW, participant="a")
    s.close()
    assert tok == plain, "a token starting with '-' must be drawn again"


def test_metadata_expiry_deletes_the_submissions_in_the_same_transaction_as_the_job(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    old = finished_job(s, finished=at(10))
    s.add_submission(old.id, ROLE_CANCEL_JOB, at(0), participant="a")
    s.add_submission(old.id, ROLE_LEAVE_SUBMISSION, at(1), participant="b")
    keep = enqueue(s, now=at(30), tree="a")
    s.add_submission(keep.id, ROLE_CANCEL_JOB, at(30), participant="a")
    s.mark_artifacts_purged([old.id], at(2000))
    assert s.delete_old_jobs(at(500)) == 1
    assert s.get_job(old.id) is None
    assert raw(path, "SELECT count(*) FROM submissions WHERE job_id=?", (old.id,)) == [(0,)]
    assert raw(path, "SELECT count(*) FROM submissions WHERE job_id=?", (keep.id,)) == [(1,)]
    s.close()


def test_a_failed_metadata_delete_rolls_back_the_job_and_its_submissions_together(tmp_path):
    """같은 트랜잭션의 반대쪽(검증 시나리오 G5.21 ②): 삭제 중간에 실패하면 잡 행도 제출 행도
    그대로다 — 제출 행만 먼저 지워진 채 잡이 남으면 옛 잡처럼 보여 강제 모드의 권한이 바뀐다."""
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    old = finished_job(s, finished=at(10))
    s.add_submission(old.id, ROLE_CANCEL_JOB, at(0), participant="a")
    s.add_submission(old.id, ROLE_LEAVE_SUBMISSION, at(1), participant="b")
    s.mark_artifacts_purged([old.id], at(2000))
    # 제출 행 DELETE 뒤에 오는 문장(jobs 행 삭제)을 실패시킨다 — 디스크 오류와 같은 자리
    with sqlite3.connect(path) as raw_conn:
        raw_conn.execute(
            "CREATE TRIGGER refuse_job_delete BEFORE DELETE ON jobs "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
    with pytest.raises(sqlite3.DatabaseError, match="disk I/O error"):
        s.delete_old_jobs(at(500))
    assert s.get_job(old.id) is not None, "the job row must survive a failed delete"
    assert raw(path, "SELECT count(*) FROM submissions WHERE job_id=?", (old.id,)) == [(2,)], (
        "the submissions were deleted outside the job's transaction"
    )
    s.close()


# ── 서버: 제출마다 capability ────────────────────────────────────────────────


def test_every_post_jobs_attempt_answers_with_its_own_submission(srv):
    """같은 토큰 이름의 두 세션도 서로 다른 참여자다(Codex 12) — 합류 응답에도 새 비밀이 온다."""
    status, first = srv.submit()
    assert status == 201 and first["joined"] is False
    sub = first["submission"]
    assert set(sub) == {"id", "cancel_token"} and len(sub["cancel_token"]) >= 32
    status, again = srv.submit()  # alice 의 두 번째 세션 — 같은 트리라 합류
    assert status == 200 and again["joined"] is True and again["job_id"] == first["job_id"]
    status, bobs = srv.submit(token="bob")
    assert status == 200 and bobs["joined"] is True
    ids = {first["submission"]["id"], again["submission"]["id"], bobs["submission"]["id"]}
    toks = {s["submission"]["cancel_token"] for s in (first, again, bobs)}
    assert len(ids) == 3 and len(toks) == 3
    assert [r[1] for r in submission_rows(srv, first["job_id"])] == [
        ROLE_CANCEL_JOB,
        ROLE_LEAVE_SUBMISSION,
        ROLE_LEAVE_SUBMISSION,
    ]
    jid2 = srv.submit(token="bob", tree_hash=OTHER_TREE)[1]
    assert jid2["submission"]["id"] not in ids


def test_with_the_key_off_the_old_cancel_works_and_a_capability_is_honoured_by_its_role(srv):
    """기본(off) — 0.2.x 클라이언트의 취소는 오늘 그대로. capability 를 보내면 그 역할대로."""
    jid = srv.submit()[1]["job_id"]
    second = srv.submit()[1]["submission"]  # alice 의 다른 세션: leave_submission
    bobs = srv.submit(token="bob")[1]["submission"]
    assert [r[1] for r in submission_rows(srv, jid)][1:] == [ROLE_LEAVE_SUBMISSION] * 2
    # alice 의 두 번째 세션이 자기 비밀로 나간다 — 잡은 그대로, 요청자도 그대로
    status, body = cancel(srv, jid, "alice", second["cancel_token"])
    assert status == 200 and body == {"left": True, "job_id": jid, "job_state": "uploading"}
    assert srv.store.get_job(jid).state == "uploading"
    assert len(submission_rows(srv, jid)) == 2
    # bob 의 옛 클라이언트(비밀 없음) — 오늘처럼 합류만 빠진다
    status, body = cancel(srv, jid, "bob")
    assert status == 200 and body["left"] is True
    assert srv.store.get_job(jid).joiners == ()
    # bob 이 이름 행으로는 이미 나갔어도 비밀은 자기 역할(나가기)만 한다 — 잡은 못 건드린다
    status, body = cancel(srv, jid, "bob", bobs["cancel_token"])
    assert status == 200 and body["left"] is True
    assert srv.store.get_job(jid).state == "uploading"
    assert [r[1] for r in submission_rows(srv, jid)] == [ROLE_CANCEL_JOB]
    # 옛 요청자 취소
    status, body = cancel(srv, jid, "alice")
    assert status == 200 and body == {"job_id": jid, "state": "cancelled"}


def test_with_the_key_on_only_a_capability_or_an_admin_may_cancel(strict):
    """켜면: cancel_job → 취소 · leave_submission → 그 참여자만 나감 · 그 외 403. 비-admin
    강제 우회는 없다 — 옛 공유 토큰 권한을 되살리지 않는다(Codex 규칙 위반 표 · fail-open 금지)."""
    srv = strict
    status, first = srv.submit()
    jid, mine = first["job_id"], first["submission"]["cancel_token"]
    bobs = srv.submit(token="bob")[1]["submission"]["cancel_token"]
    assert [j.name for j in srv.store.get_job(jid).joiners] == ["bob-desk"]
    # 요청자 이름이 같아도 비밀이 없으면 403 — 문구가 다음 행동(--cancel-token)을 말한다
    status, body = cancel(srv, jid, "alice")
    assert status == 403 and NEEDS_TOKEN in body["error"] and "--cancel-token" in body["error"]
    assert srv.store.get_job(jid).state == "uploading"
    # 합류자도 비밀 없이는 못 나간다(옛 클라이언트의 Ctrl-C 는 조용히 detach 로 끝난다)
    assert cancel(srv, jid, "bob")[0] == 403
    assert [j.name for j in srv.store.get_job(jid).joiners] == ["bob-desk"]
    # 엉뚱한 비밀 · 남의 잡의 비밀 · 잘못된 타입
    bad = "x" * 43
    status, body = cancel(srv, jid, "alice", bad)
    assert status == 403 and bad not in json.dumps(body)
    other = srv.submit(token="bob", tree_hash=OTHER_TREE)[1]
    assert cancel(srv, jid, "bob", other["submission"]["cancel_token"])[0] == 403
    assert (
        srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={"cancel_token": 7})[0]
        == 400
    )
    # bob 의 leave_submission — 자기 참여만 빠지고 잡은 계속. 비밀은 한 번만 통한다
    status, body = cancel(srv, jid, "bob", bobs)
    assert status == 200 and body == {"left": True, "job_id": jid, "job_state": "uploading"}
    assert srv.store.get_job(jid).joiners == () and srv.store.get_job(jid).state == "uploading"
    assert cancel(srv, jid, "bob", bobs)[0] == 403
    # 요청자의 cancel_job — 취소. 어느 bearer 토큰으로 보내든 capability 가 권한이다
    status, body = cancel(srv, jid, "bob", mine)
    assert status == 200 and body == {"job_id": jid, "state": "cancelled"}
    # 비-admin 의 강제 우회는 없다 — 본문의 `force` 는 무시되고 403 그대로(G5.11)
    third = srv.submit(token="bob", tree_hash="cd" * 32)[1]
    status, body = srv.req(
        "POST", f"/jobs/{third['job_id']}/cancel", token="bob", json_body={"force": True}
    )
    assert status == 403 and srv.store.get_job(third["job_id"]).state == "uploading"
    # admin 은 비밀 없이 — 오늘 그대로
    status, body = cancel(srv, other["job_id"], "admin")
    assert status == 200 and body["state"] == "cancelled"
    assert srv.store.get_job(other["job_id"]).cancelled_by == "macmini-admin"
    # v16 에서 올라온 옛 잡(제출 행 없음 · G5.19): 조회는 그대로, 요청자는 403, admin 만 취소
    srv.store._conn().execute("DELETE FROM submissions WHERE job_id=?", (third["job_id"],))
    assert srv.req("GET", f"/jobs/{third['job_id']}", token="bob")[0] == 200
    assert cancel(srv, third["job_id"], "bob")[0] == 403
    assert cancel(srv, third["job_id"], "admin")[1]["state"] == "cancelled"


def test_the_server_key_is_a_plain_boolean_and_a_bad_value_names_it(tmp_path):
    """G5.23 — 로더의 bool 규칙 그대로(`true`/`false` · 환경변수용 문자열). 그 밖의 값은 키 이름을
    말하며 거부한다(`rcm serve --config` 는 그것을 exit 2 로 옮긴다)."""
    cfg = tmp_path / "server.toml"

    def write(raw: str) -> None:
        cfg.write_text(
            f'[server]\ndata_dir = "{tmp_path}"\ncancel_requires_submission_token = {raw}\n'
        )

    for raw, want in (("true", True), ("false", False)):
        write(raw)
        assert load_server_config(cfg).server.cancel_requires_submission_token is want
    write('"maybe"')
    with pytest.raises(ConfigError, match="cancel_requires_submission_token"):
        load_server_config(cfg)


def test_health_and_status_announce_the_mode_and_the_cancel_floor(srv, tmp_path):
    h = srv.req("GET", "/api/health")[1]
    assert h["cancel_min_client_version"] is None
    assert srv.req("GET", "/api/status")[1]["server"]["cancel_requires_submission_token"] is False
    strict = Server(tmp_path / "strict", workers=False, cancel_requires_submission_token=True)
    try:
        h = strict.req("GET", "/api/health")[1]
        assert h["cancel_min_client_version"] == CANCEL_MIN_CLIENT_VERSION == "0.2.7"
        # 설정의 **유효** 최소 버전(명세 §2 G5 · Codex 규칙표 「옛 클라이언트」): 키를 켜면 그
        # 전 클라이언트의 취소가 깨지므로 `min_client_version` 자체가 올라간다 — 옛 클라이언트의
        # `rcm check` 가 자기 코드로 FAIL 을 그릴 수 있는 유일한 키다. 제출은 그대로 받는다.
        assert h["min_client_version"] == "0.2.7"
        assert _version_key(MIN_CLIENT_VERSION) < _version_key(CANCEL_MIN_CLIENT_VERSION)
        doc = strict.req("GET", "/api/status")[1]
        assert doc["server"]["cancel_requires_submission_token"] is True
    finally:
        strict.close()


def test_capabilities_never_appear_in_status_job_views_health_errors_or_the_server_log(
    strict, monkeypatch, capsys
):
    """비밀은 취소 권한이다 — 토큰과 같은 취급(Codex 규칙 위반 표 「비밀·경로 노출 금지」)."""
    srv = strict
    logged: list[str] = []
    monkeypatch.setattr(srv.app, "log", lambda msg: logged.append(msg))
    srv.app.debug = True  # 요청 로그까지 켜서 본다
    status, first = srv.submit()
    jid = first["job_id"]
    secrets = [first["submission"]["cancel_token"], first["submission"]["id"]]
    secrets.append(srv.submit(token="bob")[1]["submission"]["cancel_token"])
    hashes = [hashlib.sha256(s.encode()).hexdigest() for s in secrets]
    views = [
        srv.req("GET", "/api/status")[1],
        srv.req("GET", f"/jobs/{jid}?tail=5", token="alice")[1],
        srv.req("GET", "/api/health")[1],
        cancel(srv, jid, "alice")[1],
        cancel(srv, jid, "alice", "wrong-" + secrets[0])[1],
        srv.req("GET", "/api/whoami", token="alice")[1],
    ]
    text = json.dumps(views) + "\n".join(logged) + capsys.readouterr().err
    for secret in [*secrets, *hashes]:
        assert secret not in text, secret[:8]
    assert '"cancel_token"' not in text and "capability_hash" not in text
    assert '"submission"' not in json.dumps(views[0]) + json.dumps(views[1])


# ── CLI: 상태 파일 · rcm cancel · Ctrl-C 계약 · rcm check ────────────────────


def test_rcm_run_no_wait_carries_the_submission_and_saves_it_in_a_0600_state_file(
    srv, env, tree, state_file, capsys
):
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    body = last_json(out)
    sub = body["submission"]
    assert set(sub) == {"id", "cancel_token"}
    assert list(body)[-2:] == ["submission", "url"]
    assert sub["cancel_token"] not in err  # stderr 한 줄에는 비밀이 없다
    assert state_file.is_file()
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_file.parent.stat().st_mode) == 0o700
    entries = json.loads(state_file.read_text())
    assert entries == [
        {
            "server": f"http://127.0.0.1:{srv.port}",
            "job_id": body["job_id"],
            "submission_id": sub["id"],
            "cancel_token": sub["cancel_token"],
            "role": ROLE_CANCEL_JOB,
            "token_fingerprint": hashlib.sha256(srv.tokens["alice"].encode()).hexdigest()[:16],
        }
    ]
    assert srv.tokens["alice"] not in state_file.read_text()  # 지문이지 토큰이 아니다
    # 합류한 세션도 자기 비밀을 남긴다 — 나중의 `rcm cancel` 이 자기 참여만 빼게. 다른 토큰의
    # 세션이라 항목이 하나 더 생긴다(같은 잡이어도 요청자의 것을 덮지 않는다)
    env(srv, "bob")
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0 and last_json(out)["joined"] is True
    entries = json.loads(state_file.read_text())
    assert len(entries) == 2 and entries[1]["role"] == ROLE_LEAVE_SUBMISSION
    assert entries[1]["cancel_token"] == last_json(out)["submission"]["cancel_token"]


def test_rcm_cancel_sends_the_saved_token_or_the_flag_and_says_when_it_has_neither(
    strict, env, tree, state_file, capsys
):
    srv = strict
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    jid = last_json(out)["job_id"]
    tok = last_json(out)["submission"]["cancel_token"]
    code, out, err = run(capsys, ["cancel", str(jid)])
    assert code == 0, err
    assert last_json(out) == {"job_id": jid, "state": "cancelled"}
    assert tok not in out + err
    # 다른 셸(상태 파일 없음)에서 — 어느 쪽이 없는지 말하고, 서버의 403 문구를 그대로 전한다
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    jid2 = last_json(out)["job_id"]
    tok2 = last_json(out)["submission"]["cancel_token"]
    state_file.unlink()
    code, out, err = run(capsys, ["cancel", str(jid2)])
    assert code == 2, err
    assert "rcm state file" in err and "--cancel-token" in err and NEEDS_TOKEN in err
    assert str(state_file.parent) not in err  # 경로는 안 찍는다(M5l S10)
    assert srv.store.get_job(jid2).state == "queued"
    with pytest.raises(SystemExit) as no_force:  # 비-admin `--force` 는 없다(G5.11)
        run(capsys, ["cancel", str(jid2), "--force"])
    assert no_force.value.code == 2
    code, out, err = run(capsys, ["cancel", str(jid2), "--cancel-token", tok2])
    assert code == 0, err
    assert last_json(out)["state"] == "cancelled" and tok2 not in out + err


def test_rcm_cancel_of_a_joiner_leaves_and_an_admin_needs_no_token(
    strict, env, tree, state_file, capsys
):
    srv = strict
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    jid = last_json(out)["job_id"]
    env(srv, "bob")
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert last_json(out)["joined"] is True
    code, out, err = run(capsys, ["cancel", str(jid)])
    assert code == 0 and last_json(out)["left"] is True, err
    assert "left the join list" in err
    assert srv.store.get_job(jid).state == "queued" and srv.store.get_job(jid).joiners == ()
    env(srv, "admin")
    state_file.unlink()
    code, out, err = run(capsys, ["cancel", str(jid)])
    assert code == 0 and last_json(out)["state"] == "cancelled", err


def test_ctrl_c_detaches_the_requester_and_only_leaves_for_a_joiner(
    strict, env, tree, monkeypatch, capsys
):
    """PLAN 「잡 모델과 생명주기」의 Ctrl-C 계약은 그대로다(Codex 11): 요청자는 detach,
    합류자는 자기 capability 로 나간다. 요청자의 취소는 명시적 `rcm cancel` 뿐."""
    srv = strict

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "wait_for_job", interrupted)
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--dir", str(tree)])
    assert code == 3 and "detached from job" in err, err
    body = last_json(out)
    jid = body["job_id"]
    assert body["detached"] is True and body["left"] is False
    j = srv.store.get_job(jid)
    assert j.state == "queued" and j.cancel is None  # 취소를 보내지 않았다
    env(srv, "bob")
    code, out, err = run(capsys, ["run", "ok", "--dir", str(tree)])
    assert code == 3 and "(left the join list)" in err, err
    assert last_json(out)["left"] is True
    j = srv.store.get_job(jid)
    assert j.state == "queued" and j.joiners == ()
    assert [r[1] for r in submission_rows(srv, jid)] == [ROLE_CANCEL_JOB]


def test_a_waiting_run_never_prints_the_cancel_token(tmp_path, env, tree, state_file, capsys):
    live = Server(tmp_path / "live", workers=True)
    try:
        env(live)
        code, out, err = run(capsys, ["run", "ok", "--dir", str(tree)])
        assert code == 0, err
        entries = json.loads(state_file.read_text())
        assert len(entries) == 1
        tok = entries[0]["cancel_token"]
        assert tok not in out and tok not in err
        assert "submission" not in last_json(out)
    finally:
        live.close()


def test_rcm_check_mentions_the_cancel_floor_only_when_the_key_is_on(
    srv, env, tmp_path, capsys, monkeypatch
):
    # 이 클라이언트는 비밀을 보내지만 버전 번호는 릴리스 전까지 0.2.6 이라 strict 서버의 바닥
    # (0.2.7)에 걸린다 — 릴리스된 모양(0.2.7)으로 본다. 옛 클라이언트의 FAIL 은 M5l S10 테스트.
    monkeypatch.setattr(cli_mod, "__version__", CANCEL_MIN_CLIENT_VERSION)
    env(srv)
    code, out, err = run(capsys, ["check"])
    assert code == 0, err
    assert not [ln for ln in out.splitlines() if ln.split()[1:2] == ["cancel"]], out
    strict = Server(tmp_path / "strict", workers=False, cancel_requires_submission_token=True)
    try:
        env(strict)
        code, out, err = run(capsys, ["check"])
    finally:
        strict.close()
    assert code == 0, out + err
    rows = [ln for ln in out.splitlines() if ln.split()[1:2] == ["cancel"]]
    assert len(rows) == 1 and rows[0].startswith("ok ") and "0.2.7" in rows[0], out
    assert "cancel token" in rows[0]


def test_the_state_file_is_written_atomically_and_prunes_only_finished_jobs(tmp_path, monkeypatch):
    from remote_ci_monitor import submissions

    path = tmp_path / "state" / "rcm" / STATE_FILE
    for i in range(submissions.MAX_ENTRIES + 5):
        submissions.remember(
            "http://h:1", i, f"s{i}", f"t{i}", role=ROLE_CANCEL_JOB, path=path, finished=_done
        )
    entries = json.loads(path.read_text())
    assert len(entries) == submissions.MAX_ENTRIES
    assert entries[-1]["job_id"] == submissions.MAX_ENTRIES + 4
    assert submissions.lookup("http://h:1", 3, path=path) is None  # 끝난 잡이라 잘려 나갔다
    assert submissions.lookup("http://h:1", submissions.MAX_ENTRIES + 4, path=path) == (
        f"t{submissions.MAX_ENTRIES + 4}"
    )
    # 같은 제출을 다시 기억하면 최근 것 · 다른 서버의 같은 번호는 남이다
    submissions.remember("http://h:1", 7, "s7", "newer", role=ROLE_CANCEL_JOB, path=path)
    assert submissions.lookup("http://h:1", 7, path=path) == "newer"
    assert submissions.lookup("http://other:1", 7, path=path) is None
    # 한 머신의 두 세션이 한 잡에 얽히면(M5l S5): 그 잡의 가장 최근 제출이 간다 — 합류한
    # 세션의 `rcm cancel` 이 요청자의 비밀로 잡을 죽이지 않는다. 특정 제출은 `submission_id` 로.
    submissions.remember(
        "http://h:1", 7, "a2", "alice-leave", role=ROLE_LEAVE_SUBMISSION, path=path
    )
    assert submissions.lookup("http://h:1", 7, path=path) == "alice-leave"
    assert submissions.lookup("http://h:1", 7, submission_id="s7", path=path) == "newer"
    names = sorted(p.name for p in path.parent.iterdir())
    assert names == [STATE_FILE, STATE_FILE + ".lock"]  # 임시 파일은 안 남는다
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # 깨진 파일은 「없음」이지 예외가 아니다 — 취소는 서버가 최종 판정한다
    path.write_text("{not json")
    assert submissions.lookup("http://h:1", 7, path=path) is None
    # 기본 경로: $XDG_STATE_HOME/rcm, 없으면 ~/.local/state/rcm
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert submissions.state_path() == tmp_path / "xdg" / "rcm" / STATE_FILE
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert submissions.state_path() == tmp_path / "home" / ".local" / "state" / "rcm" / STATE_FILE
    assert os.environ.get("XDG_STATE_HOME") is None


def _done(server: str, job_id: int) -> bool:
    return True
