"""취소 capability 의 리뷰 보완(M5l S5~S10 · docs/reviews/2026-09-13-impl-reviews/pr-110.md).

리뷰가 잡은 것 — 각 재현을 그대로 옮겼다:
- S5 상태 파일 조회가 같은 토큰의 다른 세션 `cancel_job` 을 집어 잡 전체를 취소했다. 이제
  같은 서버·잡·**내 토큰 지문**의 항목 중 **가장 최근 제출**(= 이 토큰으로 마지막에 낸 것)
  하나이고, 다른 지문으로 넘어가는 폴백도 `cancel_job` 우선 순위도 없다. `--submission-id` 가
  특정 제출을 고른다.
- S6 `leave_submission` 비밀이 참여자에 묶이지 않았고(Bob 비밀 + Charlie bearer 가 Charlie
  를 뺐다), 조회·삭제·joiner 삭제가 한 트랜잭션이 아니라 같은 비밀이 동시에 두 번 통했다.
- S7 상태 파일의 read-modify-write 가 잠금 없이 고정 `.tmp` 를 써서 동시 `rcm run` 이 항목을
  잃었다.
- S8 잡 생성/합류와 capability 삽입이 다른 트랜잭션이라 삽입 실패에도 잡·합류가 남았다.
- S9 200개 상한이 도는 잡의 유일한 취소 권한을 버렸고, 저장 실패는 조용했다.
- S10 모르는 role 이 `cancel_job` 으로 승격됐다 · stderr 에 절대경로 · `_client_row` 가
  `min_client_version` 보다 「same as server」를 먼저 봤다.

구현보다 먼저 썼다 — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import submissions
from remote_ci_monitor.cli import _client_row, main
from remote_ci_monitor.core.model import Requester, Source
from remote_ci_monitor.store import ROLE_CANCEL_JOB, ROLE_LEAVE_SUBMISSION, Store
from test_cancel_capability import cancel, raw, submission_rows
from test_server import Server
from test_store import enqueue

NOW = datetime(2026, 9, 13, 9, 0, tzinfo=UTC)
STATE_FILE = "submissions.json"
SRC = Source(mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash="9f8e")


@pytest.fixture
def strict(tmp_path):
    s = Server(tmp_path, workers=False, cancel_requires_submission_token=True)
    s.tokens["charlie"] = s.store.add_token("charlie-mac", admin=False, now=datetime.now(UTC))
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
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


def remember(path: Path, job_id: int, sid: str, tok: str, role: str = ROLE_CANCEL_JOB, **kw):
    submissions.remember("http://h:1", job_id, sid, tok, role=role, path=path, **kw)


# ── S5: 같은 토큰·같은 상태 파일의 두 세션 ──────────────────────────────────


def test_s5_a_second_session_of_the_same_token_cancels_its_own_join_not_the_job(
    strict, env, tree, state_file, capsys
):
    """리뷰 B P1 재현: 세션 A 가 제출, 같은 토큰·같은 XDG_STATE_HOME 의 세션 B 가 합류, B 가
    `rcm cancel N` → B 의 합류만 빠지고 잡은 돈다. (전에는 A 의 `cancel_job` 이 이겨 잡이
    죽었다 — 결정 87 의 정반대.)"""
    srv = strict
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    jid = last_json(out)["job_id"]
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0 and last_json(out)["joined"] is True, err
    second = last_json(out)["submission"]
    code, out, err = run(capsys, ["cancel", str(jid)])
    assert code == 0, err
    assert last_json(out) == {"left": True, "job_id": jid, "job_state": "queued"}
    assert srv.store.get_job(jid).state == "queued"
    assert [r[1] for r in submission_rows(srv, jid)] == [ROLE_CANCEL_JOB]
    # 쓴 비밀은 상태 파일에서 지운다 — 다시 `rcm cancel` 하면 남은 것(요청자 비밀)이 간다
    entries = json.loads(state_file.read_text())
    assert [e["submission_id"] for e in entries] != [second["id"]]
    assert all(e["submission_id"] != second["id"] for e in entries)
    code, out, err = run(capsys, ["cancel", str(jid)])
    assert code == 0 and last_json(out) == {"job_id": jid, "state": "cancelled"}, err
    assert json.loads(state_file.read_text()) == []


def test_s5_lookup_takes_the_newest_entry_of_my_token_and_never_another_sessions(tmp_path):
    """같은 서버·잡·내 지문의 항목 중 마지막 것 — `cancel_job` 을 먼저 집는 순위도, 다른 지문의
    항목으로 넘어가는 폴백도 없다. `submission_id` 를 주면 그것만(명시한 것이 이긴다)."""
    path = tmp_path / "state" / "rcm" / STATE_FILE
    A, B = submissions.fingerprint("alice-secret"), submissions.fingerprint("bob-secret")
    assert len(A) == 16 and A != B and submissions.fingerprint(None) == ""
    remember(path, 7, "a1", "alice-cancel", ROLE_CANCEL_JOB, token_fingerprint=A)
    remember(path, 7, "a2", "alice-leave", ROLE_LEAVE_SUBMISSION, token_fingerprint=A)
    look = lambda **kw: submissions.lookup("http://h:1", 7, path=path, **kw)  # noqa: E731
    assert look(token_fingerprint=A) == "alice-leave"  # 최근 것 — cancel_job 우선이 아니다
    assert look(token_fingerprint=A, submission_id="a1") == "alice-cancel"
    assert look(token_fingerprint=A, submission_id="zz") is None
    assert submissions.lookup("http://other:1", 7, submission_id="a1", path=path) is None
    # 다른 토큰(bob)의 세션은 alice 의 항목을 절대 집지 않는다 — 폴백 없음
    assert look(token_fingerprint=B) is None
    assert look() is None  # 토큰 없는 세션도 마찬가지
    remember(path, 7, "b1", "bob-leave", ROLE_LEAVE_SUBMISSION, token_fingerprint=B)
    assert look(token_fingerprint=B) == "bob-leave"
    assert look(token_fingerprint=A) == "alice-leave"  # bob 의 나중 항목은 alice 것이 아니다
    assert look(token_fingerprint=B, submission_id="a1") == "alice-cancel"  # 명시는 그대로
    entries = json.loads(path.read_text())
    assert [set(e) for e in entries] == [
        {"server", "job_id", "submission_id", "cancel_token", "role", "token_fingerprint"}
    ] * 3
    assert "alice-secret" not in path.read_text() and "bob-secret" not in path.read_text()
    submissions.forget("http://h:1", "b1", path=path)
    # 같은 제출을 다시 기억하면 하나로 — 잡당이 아니라 제출당 한 줄
    remember(path, 7, "a2", "alice-leave-again", ROLE_LEAVE_SUBMISSION, token_fingerprint=A)
    assert [e["submission_id"] for e in json.loads(path.read_text())] == ["a1", "a2"]
    assert look(token_fingerprint=A) == "alice-leave-again"
    assert submissions.forget("http://h:1", "a2", path=path) is True
    assert submissions.forget("http://h:1", "a2", path=path) is False
    assert look(token_fingerprint=A) == "alice-cancel"


def test_s5_rcm_cancel_submission_id_flag_selects_that_entry(strict, env, tree, capsys):
    srv = strict
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    jid, first = last_json(out)["job_id"], last_json(out)["submission"]["id"]
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert last_json(out)["joined"] is True
    code, out, err = run(capsys, ["cancel", str(jid), "--submission-id", first])
    assert code == 0 and last_json(out) == {"job_id": jid, "state": "cancelled"}, err
    code, out, err = run(capsys, ["cancel", str(jid), "--submission-id", "nope"])
    assert code == 2 and "submission nope" in err and "--cancel-token" in err


# ── S6: leave 비밀의 참여자 바인딩 · 원자적 소비 ────────────────────────────


def test_s6_a_leave_secret_only_works_for_the_bearer_it_was_issued_to(strict):
    """리뷰 B P1 재현: Bob 과 Charlie 가 합류한 잡에서 Bob 의 leave 비밀을 Charlie bearer 로
    보내면 403 — Bob 의 capability 도 Charlie 의 joiner 행도 그대로다."""
    srv = strict
    jid = srv.submit()[1]["job_id"]
    bobs = srv.submit(token="bob")[1]["submission"]["cancel_token"]
    srv.submit(token="charlie")
    assert [j.name for j in srv.store.get_job(jid).joiners] == ["bob-desk", "charlie-mac"]
    status, body = cancel(srv, jid, "charlie", bobs)
    assert status == 403 and "not your submission" in body["error"]
    assert [j.name for j in srv.store.get_job(jid).joiners] == ["bob-desk", "charlie-mac"]
    assert len(submission_rows(srv, jid)) == 3
    # 요청자 bearer 로도 안 된다 — 참여자가 다르다
    assert cancel(srv, jid, "alice", bobs)[0] == 403
    # 자기 bearer 로는 된다
    status, body = cancel(srv, jid, "bob", bobs)
    assert status == 200 and body["left"] is True
    assert [j.name for j in srv.store.get_job(jid).joiners] == ["charlie-mac"]
    rows = raw(
        srv.cfg.data_dir / "rcm.sqlite3",
        "SELECT role, participant FROM submissions WHERE job_id=? ORDER BY created_at",
        (jid,),
    )
    assert rows == [(ROLE_CANCEL_JOB, "alice-laptop"), (ROLE_LEAVE_SUBMISSION, "charlie-mac")]


def test_s6_the_same_leave_secret_sent_twice_at_once_succeeds_exactly_once(strict):
    srv = strict
    jid = srv.submit()[1]["job_id"]
    bobs = srv.submit(token="bob")[1]["submission"]["cancel_token"]
    n = 6
    barrier = threading.Barrier(n)
    results: list[int] = []
    lock = threading.Lock()

    def go():
        barrier.wait()
        status, _ = cancel(srv, jid, "bob", bobs)
        with lock:
            results.append(status)

    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200] + [403] * (n - 1)
    assert srv.store.get_job(jid).joiners == () and srv.store.get_job(jid).state == "uploading"
    assert [r[1] for r in submission_rows(srv, jid)] == [ROLE_CANCEL_JOB]


def test_s6_store_consume_leave_checks_the_row_and_removes_the_joiner_in_one_transaction(
    tmp_path,
):
    s = Store(tmp_path / "rcm.sqlite3")
    j = enqueue(s, now=NOW)
    assert s.add_joiner(j.id, "bob-desk", "bob", NOW)
    sid, tok = s.add_submission(j.id, ROLE_LEAVE_SUBMISSION, NOW, participant="bob-desk")
    assert s.submission_role(j.id, tok) == (sid, ROLE_LEAVE_SUBMISSION, "bob-desk")
    # 다른 참여자 이름 · cancel_job 행 · 없는 행은 소비되지 않는다
    assert s.consume_leave(j.id, sid, "charlie-mac", drop_joiner=True) is False
    assert s.consume_leave(j.id, "nope", "bob-desk", drop_joiner=True) is False
    assert [x.name for x in s.get_job(j.id).joiners] == ["bob-desk"]
    assert s.consume_leave(j.id, sid, "bob-desk", drop_joiner=True) is True
    assert s.get_job(j.id).joiners == () and s.submission_role(j.id, tok) is None
    assert s.consume_leave(j.id, sid, "bob-desk", drop_joiner=True) is False
    s.close()


# ── S7: 상태 파일의 다중 프로세스 쓰기 ──────────────────────────────────────


def test_s7_two_processes_writing_the_state_file_at_once_lose_nothing(tmp_path):
    """리뷰 B P1 재현: 같은 사용자의 `rcm run` 둘이 동시에 — 두 항목 다 남는다. 여기서는
    프로세스 넷이 각각 25 개를 겹쳐 쓴다; 하나라도 잃으면 잠금이 없는 것이다."""
    path = tmp_path / "state" / "rcm" / STATE_FILE
    code = (
        "import sys; from remote_ci_monitor import submissions\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); who = sys.argv[2]\n"
        "for i in range(25):\n"
        "    submissions.remember('http://h:1', int(who) * 100 + i, f'{who}-{i}', 't',\n"
        "        role='cancel_job', path=p)\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code, str(path), str(k)]) for k in range(4)]
    assert [p.wait() for p in procs] == [0] * 4
    entries = json.loads(path.read_text())
    assert sorted(e["submission_id"] for e in entries) == sorted(
        f"{k}-{i}" for k in range(4) for i in range(25)
    )
    leftovers = sorted(p.name for p in path.parent.iterdir())
    assert leftovers == [STATE_FILE, STATE_FILE + ".lock"], leftovers


def test_s7_remember_waits_for_the_lock_and_names_its_temp_file_by_pid(tmp_path):
    path = tmp_path / "state" / "rcm" / STATE_FILE
    remember(path, 1, "s1", "t1")
    lock_path = path.with_name(path.name + ".lock")
    assert lock_path.is_file()
    fd = os.open(lock_path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = threading.Event()
    t = threading.Thread(target=lambda: (remember(path, 2, "s2", "t2"), done.set()))
    t.start()
    assert not done.wait(0.5), "remember() wrote while another process held the lock"
    assert [e["submission_id"] for e in json.loads(path.read_text())] == ["s1"]
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    assert done.wait(5)
    t.join()
    assert [e["submission_id"] for e in json.loads(path.read_text())] == ["s1", "s2"]
    # 임시 파일 이름에 pid 가 들어간다 — 두 프로세스가 같은 `.tmp` 를 O_TRUNC 로 열지 않게
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(submissions.os, "replace", spy)
        remember(path, 3, "s3", "t3")
    assert seen and f".{os.getpid()}." in seen[0] and seen[0].endswith(".tmp"), seen


# ── S8: 잡/합류와 capability 가 한 트랜잭션 ──────────────────────────────────


def test_s8_a_failed_capability_insert_leaves_no_job_row_and_no_join(tmp_path, monkeypatch):
    """리뷰 B P2 재현: capability 삽입 실패(DB full · 충돌)에 잡이 남으면 `git_ref` 잡은 실행될
    수도 있다. 생성·합류 모두 삽입과 같은 트랜잭션이라 실패는 아무것도 남기지 않는다."""
    db = tmp_path / "rcm.sqlite3"
    s = Store(db)
    kw = dict(
        preset="ok",
        inputs={},
        key="ok",
        concurrency_group=None,
        source=SRC,
        requester=Requester(name="alice-laptop", label="alice"),
        timeout_seconds=60,
        join_key="jk",
        now=NOW,
        state="queued",
    )

    def boom(self, conn, *a, **k):
        raise sqlite3.OperationalError("database or disk is full")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Store, "_insert_submission", boom)
        with pytest.raises(sqlite3.OperationalError):
            s.create_job_with_submission(participant="alice-laptop", **kw)
    assert raw(db, "SELECT count(*) FROM jobs") == [(0,)]
    assert raw(db, "SELECT count(*) FROM events") == [(0,)]
    assert raw(db, "SELECT count(*) FROM submissions") == [(0,)]
    # 정상 경로: 잡 + cancel_job 행이 한 번에
    job, sid, tok = s.create_job_with_submission(participant="alice-laptop", **kw)
    assert s.submission_role(job.id, tok) == (sid, ROLE_CANCEL_JOB, "alice-laptop")
    assert raw(db, "SELECT join_count FROM jobs") == [(0,)]
    # 합류: 삽입 실패면 joiner 행도 join_count 도 그대로
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Store, "_insert_submission", boom)
        with pytest.raises(sqlite3.OperationalError):
            s.join_or_bump_with_submission("jk", "bob-desk", "bob", 0, NOW)
    assert s.get_job(job.id).joiners == ()
    assert raw(db, "SELECT join_count FROM jobs") == [(0,)]
    assert raw(db, "SELECT count(*) FROM submissions") == [(1,)]
    got = s.join_or_bump_with_submission("jk", "bob-desk", "bob", 0, NOW)
    assert got is not None
    joined, sid2, tok2 = got
    assert joined.id == job.id and [x.name for x in joined.joiners] == ["bob-desk"]
    assert s.submission_role(job.id, tok2) == (sid2, ROLE_LEAVE_SUBMISSION, "bob-desk")
    assert raw(db, "SELECT join_count FROM jobs") == [(1,)]
    assert s.join_or_bump_with_submission("other", "bob-desk", "bob", 0, NOW) is None
    s.close()


# ── S9: 상한은 끝난 잡만 · 저장 실패는 말한다 ──────────────────────────────


def test_s9_the_cap_prunes_only_finished_jobs_and_keeps_unknown_ones(tmp_path):
    path = tmp_path / "state" / "rcm" / STATE_FILE
    n = submissions.MAX_ENTRIES
    for i in range(n):
        remember(path, i, f"s{i}", f"t{i}")
    assert len(json.loads(path.read_text())) == n
    # 201 개, 전부 활성 → 전부 보존(리뷰 B P2: 도는 잡의 유일한 취소 권한을 버리지 않는다)
    remember(path, n, f"s{n}", f"t{n}", finished=lambda server, job_id: False)
    assert len(json.loads(path.read_text())) == n + 1
    # 서버가 대답을 못 하면(None) 보존
    asked: list[tuple[str, int]] = []

    def unknown(server, job_id):
        asked.append((server, job_id))
        return None

    remember(path, n + 1, f"s{n + 1}", f"t{n + 1}", finished=unknown)
    assert len(json.loads(path.read_text())) == n + 2
    assert asked and asked[0] == ("http://h:1", 0)
    # 물어볼 길이 없으면(기본) 아무것도 버리지 않는다
    remember(path, n + 2, f"s{n + 2}", f"t{n + 2}")
    assert len(json.loads(path.read_text())) == n + 3
    # 끝난 잡(짝수 번호)만 잘린다 — 오래된 것부터, 상한 아래로 내려갈 만큼만
    remember(path, n + 3, f"s{n + 3}", f"t{n + 3}", finished=lambda s, j: j % 2 == 0)
    entries = json.loads(path.read_text())
    assert len(entries) == n
    ids = [e["job_id"] for e in entries]
    assert all(j % 2 == 1 for j in ids[:4]) and ids[-1] == n + 3
    assert submissions.lookup("http://h:1", 1, path=path) == "t1"


def test_s9_rcm_run_asks_the_server_which_jobs_finished_before_pruning(
    strict, env, tree, state_file, capsys, monkeypatch
):
    srv = strict
    env(srv)
    monkeypatch.setattr(submissions, "MAX_ENTRIES", 2)
    state_file.parent.mkdir(parents=True)
    server = f"http://127.0.0.1:{srv.port}"
    # 이 서버의 끝난 잡 하나 · 도는 잡 하나 · 다른 서버의 잡 하나(모른다 → 보존)
    done = srv.submit(token="bob", tree_hash="ab" * 32)[1]
    cancel(srv, done["job_id"], "bob", done["submission"]["cancel_token"])
    live = srv.submit(token="bob", tree_hash="cd" * 32)[1]
    submissions.remember(server, done["job_id"], "d", "t", role=ROLE_CANCEL_JOB, path=state_file)
    submissions.remember(server, live["job_id"], "l", "t", role=ROLE_CANCEL_JOB, path=state_file)
    submissions.remember("http://elsewhere:1", 9, "e", "t", role=ROLE_CANCEL_JOB, path=state_file)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    ids = [e["submission_id"] for e in json.loads(state_file.read_text())]
    assert ids == ["l", "e", last_json(out)["submission"]["id"]]


def test_s9_a_failed_state_save_says_so_without_a_path_and_the_json_still_has_the_token(
    strict, env, tree, state_file, capsys, monkeypatch
):
    srv = strict
    env(srv)

    def refuse(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(submissions, "remember", refuse)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    body = last_json(out)
    assert body["submission"]["cancel_token"]
    line = [ln for ln in err.splitlines() if "not saved" in ln or "could not save" in ln]
    assert len(line) == 1, err
    assert "--cancel-token" in line[0] and "No space left" in line[0]
    assert str(state_file) not in err and "/" not in line[0].split("--cancel-token")[0]
    assert body["submission"]["cancel_token"] not in err


# ── S10: role 화이트리스트 · 경로 없는 stderr · check 의 바닥 비교 ─────────


def test_s10_an_unknown_role_in_the_table_is_refused_and_logged(strict, monkeypatch):
    """리뷰 B P2 재현: DB 손상·수동 복구·미래 role 값이 `cancel_job` 이 되면 안 된다."""
    srv = strict
    logged: list[str] = []
    monkeypatch.setattr(srv.app, "log", lambda msg: logged.append(msg))
    jid = srv.submit()[1]["job_id"]
    secret = "s" * 43
    from remote_ci_monitor.store import hash_token

    srv.store._conn().execute(
        "INSERT INTO submissions (job_id, submission_id, role, capability_hash, created_at, "
        "participant) VALUES (?, ?, ?, ?, ?, ?)",
        (jid, "weird", "wipe_everything", hash_token(secret), 0.0, "alice-laptop"),
    )
    status, body = cancel(srv, jid, "alice", secret)
    assert status == 403 and secret not in json.dumps(body)
    assert srv.store.get_job(jid).state == "uploading"
    assert [m for m in logged if "wipe_everything" in m and "weird" in m], logged
    assert not any(secret in m for m in logged)


def test_s10_stderr_never_prints_the_state_file_path(strict, env, tree, state_file, capsys):
    srv = strict
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    jid = last_json(out)["job_id"]
    state_file.unlink()
    code, out, err = run(capsys, ["cancel", str(jid)])
    assert code == 2
    assert "rcm state file" in err and "--cancel-token" in err
    assert str(state_file.parent) not in err and str(Path.home()) not in err


def test_s10_client_row_fails_below_the_floor_even_when_the_versions_are_equal(monkeypatch):
    """리뷰 B P2 재현: 0.2.6 클라이언트 · 0.2.6 strict 서버(`min_client_version` 0.2.7) —
    「same as server」로 OK 를 내면 안 된다. 실제 취소는 403 이다."""
    client = cli_mod.Client("http://h:1", token="t")
    monkeypatch.setattr(cli_mod, "__version__", "0.2.6")
    h = {"version": "0.2.6", "min_client_version": "0.2.7", "cancel_min_client_version": "0.2.7"}
    row = _client_row(client, h)
    assert row is not None and row[1] is False and "0.2.7" in row[2]
    assert "same as server" not in row[2]
    h["min_client_version"] = "0.2.0"
    assert _client_row(client, h)[1] is True
    monkeypatch.setattr(cli_mod, "__version__", "0.2.7")
    row = _client_row(client, {"version": "0.2.6", "min_client_version": "0.2.7"})
    assert row[1] is None and "newer" in row[2]
