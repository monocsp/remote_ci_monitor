"""서버가 제출 경로에서만 클라이언트 버전을 한 줄 남긴다 (M5q PR A · 결정 168 · §2.10 · §14.10).

`docs/m5q-test-scenarios-c.md` 의 serverlog 줄(CB7~CB12 · CE1~CE5 · CG1~CG7)을 옮긴다. 잠그는 것:

- 진짜 서버의 health 를 진짜 `follow.parse_health` 가 받는다(CB7 · CB8) — 생산자와 소비자를 잇는다.
- 받아들여진 `POST /jobs` 뒤에만 `client <토큰 이름> runs v<X>` 를 (토큰 이름, 버전) 쌍마다
  프로세스당 한 번 쓴다. 인증하지 않은 요청 · 401 · 403 · 400 · 다른 경로는 줄을 남기지 않는다.
- `User-Agent` 는 전체가 `rcm/<버전>` 이고 그 버전이 `VERSION_RE` 를 fullmatch 로 통과할 때만 본다.
- 쌍의 기억은 256개(`server.CLIENT_VERSION_PAIRS_MAX`)에서 멈추고 찼다는 줄을 한 번 쓴다.
- DB 와 health 의 키는 바뀌지 않는다(CB12 · CG7).

서버의 `log_line` 은 부를 때마다 `sys.stderr` 를 찾으므로 서버 스레드가 쓴 줄도 capsys 로 받힌다.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
import sqlite3
import threading
import time
from datetime import UTC, datetime

import pytest

import remote_ci_monitor.server as server_module
from remote_ci_monitor import SCHEMA_VERSION, __version__, follow
from remote_ci_monitor.cli import main
from remote_ci_monitor.clientwheel import WheelBuildError, wheel_filename
from test_server import TAR, TREE_HASH, Server

LINE_RE = re.compile(r"^\[rcm\] client (\S+) runs v(\S+)$", re.M)

BODY = {
    "preset": "ok",
    "inputs": {},
    "source": {
        "mode": "tree",
        "repo": "org/app",
        "base_sha": "abc123f",
        "dirty": True,
        "tree_hash": TREE_HASH,
        "bytes": len(TAR),
    },
    "requester_label": "someone@host",
}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


def add_token(srv: Server, name: str, **kw) -> str:
    """이름이 정해진 토큰 하나 — `srv.req(token=…)` 에 그대로 넘긴다."""
    return srv.store.add_token(name, now=datetime.now(UTC), **kw)


def submit_as(srv: Server, token: str | None, ua: str | None, body=None, tree_hash=None):
    """손으로 정한 `User-Agent` 로 제출한다. `ua=None` 이면 헤더를 싣지 않는다."""
    b = copy.deepcopy(body or BODY)
    if tree_hash is not None:
        b["source"]["tree_hash"] = tree_hash
    headers = {"User-Agent": ua} if ua is not None else {}
    return srv.req("POST", "/jobs", token=token, json_body=b, headers=headers)


def lines(text: str) -> list[tuple[str, str]]:
    return LINE_RE.findall(text)


#: 줄이 없어야 하는 시험이 지켜보는 시간. 서버는 응답을 보내기 전에 줄을 쓰므로(`POST /jobs`
#: 처리기) 응답을 받은 뒤에는 짧게 봐도 된다. 응답 뒤에 쓰는 회귀가 생겨도 스레드 하나만큼 늦다
QUIET = 0.1


def settle(capsys, want: int, timeout: float = 3.0) -> str:
    """줄이 `want` 개 모일 때까지(또는 시간이 다 될 때까지) 서버 stderr 를 모은다.

    줄이 응답 뒤에 쓰여도 놓치지 않고, 더 쓰이는 줄이 있으면 잡히게 조금 더 기다린다."""
    seen = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen += capsys.readouterr().err
        if len(lines(seen)) >= want:
            break
        time.sleep(0.02)
    time.sleep(0.05)
    return seen + capsys.readouterr().err


def uniq_hash(i: int) -> str:
    return hashlib.sha256(f"tree-{i}".encode()).hexdigest()


# ── 베스트 ──────────────────────────────────────────────────────────────────


def test_cb7_real_health_passes_the_real_parse_health(srv):
    """CB7: 진짜 health 를 진짜 `parse_health` 가 예외 없이 받고 값이 실제 wheel 과 맞는다."""
    status, body = srv.req("GET", "/api/health")
    assert status == 200, body
    server = f"http://127.0.0.1:{srv.port}"
    info = follow.parse_health(server, body, now=time.time())
    status, data = srv.req("GET", body["client_wheel"]["path"], raw=True)[0::2]
    assert status == 200
    assert info.version == __version__
    assert info.wheel_path == "/client/" + wheel_filename(__version__)
    assert info.sha256 == hashlib.sha256(data).hexdigest()
    assert info.bytes == len(data) <= follow.MAX_WHEEL_BYTES
    assert info.wheel_error is None


def test_cb8_health_503_still_parses(srv):
    """CB8: janitor stale 로 503 이어도 본문은 계약을 지킨다."""
    from test_client_wheel import _make_health_503

    _make_health_503(srv)
    status, body = srv.req("GET", "/api/health")
    assert status == 503
    info = follow.parse_health(f"http://127.0.0.1:{srv.port}", body, now=time.time())
    assert info.version == __version__ and info.wheel_path is not None


def test_cb8_wheel_build_failure_is_no_wheel_not_bad_health(tmp_path, monkeypatch):
    """CB8: 조립 실패 서버의 본문은 `wheel_path None` · `wheel_error metadata_missing` 이다."""

    def boom(version):
        raise WheelBuildError("metadata_missing", "METADATA")

    monkeypatch.setattr(server_module, "build_wheel", boom)
    s = Server(tmp_path, workers=False)
    try:
        status, body = s.req("GET", "/api/health")
        assert body["client_wheel"] is None, body
        info = follow.parse_health(f"http://127.0.0.1:{s.port}", body, now=time.time())
        assert info.wheel_path is None and info.sha256 is None
        assert info.wheel_error == "metadata_missing"
    finally:
        s.close()


def test_cb9_same_pair_once_new_version_again(srv, capsys):
    """CB9: 같은 쌍은 한 번, 새 버전은 또 한 번 — 순서대로 정확히 두 줄."""
    tok = add_token(srv, "macbook")
    capsys.readouterr()
    for i in range(3):
        status, body = submit_as(srv, tok, "rcm/0.8.1", tree_hash=uniq_hash(i))
        assert status in (200, 201), body
    status, body = submit_as(srv, tok, "rcm/0.8.2", tree_hash=uniq_hash(9))
    assert status in (200, 201), body
    err = settle(capsys, 2)
    assert lines(err) == [("macbook", "0.8.1"), ("macbook", "0.8.2")], err
    assert "[rcm] client macbook runs v0.8.1" in err.splitlines()
    assert "[rcm] client macbook runs v0.8.2" in err.splitlines()


def test_cb10_same_version_other_token(srv, capsys):
    """CB10: 같은 버전이라도 토큰마다 한 줄."""
    a, b = add_token(srv, "macbook"), add_token(srv, "macstudio")
    capsys.readouterr()
    assert submit_as(srv, a, "rcm/0.8.1", tree_hash=uniq_hash(1))[0] in (200, 201)
    assert submit_as(srv, b, "rcm/0.8.1", tree_hash=uniq_hash(2))[0] in (200, 201)
    err = settle(capsys, 2)
    assert sorted(lines(err)) == [("macbook", "0.8.1"), ("macstudio", "0.8.1")], err


def test_cb11_the_real_cli_submission_logs_its_own_header(srv, capsys, monkeypatch, tmp_path):
    """CB11: `Client` 가 실제로 붙인 헤더로 한 줄 — 손으로 흉내 낸 헤더만 보지 않는다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{srv.port}")
    monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
    monkeypatch.delenv("RCM_LABEL", raising=False)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "hello.txt").write_text("hello\n")
    capsys.readouterr()
    code = main(["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0
    err = settle(capsys, 1)
    assert lines(err) == [("alice-laptop", __version__)], err


def test_cb12_the_log_line_changes_no_database(srv, capsys):
    """CB12: DB 스키마 · 열 목록 · `last_error` 가 그대로다(로그 한 줄은 오류가 아니다)."""
    db = srv.cfg.data_dir / "rcm.sqlite3"

    def shape():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            uv = con.execute("PRAGMA user_version").fetchone()[0]
            cols = {
                t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
                for t in ("jobs", "submissions")
            }
        finally:
            con.close()
        return uv, cols

    before = shape()
    last_before = srv.req("GET", "/api/status")[1]["server"].get("last_error")
    tok = add_token(srv, "macbook")
    assert submit_as(srv, tok, "rcm/0.8.1")[0] in (200, 201)
    settle(capsys, 1)
    assert shape() == before
    assert srv.req("GET", "/api/status")[1]["server"].get("last_error") == last_before


# ── 에러 ────────────────────────────────────────────────────────────────────


def test_ce1_other_routes_never_log(srv, capsys):
    """CE1: 제출이 아닌 경로는 같은 헤더와 유효한 토큰이 있어도 줄을 남기지 않는다."""
    status, body = submit_as(srv, "alice", None)  # 헤더 없는 제출 — 줄 없음
    assert status in (200, 201), body
    job = body["job_id"]
    ua = {"User-Agent": "rcm/0.8.1"}
    capsys.readouterr()
    srv.req("GET", "/api/status", token="alice", headers=ua)
    srv.req("GET", "/api/health", token="alice", headers=ua)
    srv.req("GET", "/client/" + wheel_filename(__version__), token="alice", headers=ua, raw=True)
    srv.req("GET", f"/jobs/{job}", token="alice", headers=ua)
    srv.req("POST", f"/jobs/{job}/cancel", token="alice", json_body={}, headers=ua)
    err = settle(capsys, 1, timeout=QUIET)
    assert lines(err) == [], err


def test_ce2_no_token_or_wrong_token_is_401_and_silent(srv, capsys):
    """CE2: 인증하지 않은 헤더로 로그를 채울 수 없다."""
    capsys.readouterr()
    assert submit_as(srv, None, "rcm/0.8.1")[0] == 401
    assert submit_as(srv, "not-a-real-token", "rcm/0.8.1")[0] == 401
    assert lines(settle(capsys, 1, timeout=QUIET)) == []


def test_ce3_worker_token_is_403_and_silent(srv, capsys):
    """CE3: 워커 토큰의 제출은 403 이고 줄이 없다."""
    wtok = add_token(srv, "w1", kind="worker")
    capsys.readouterr()
    status, body = submit_as(srv, wtok, "rcm/0.8.1")
    assert status == 403 and "worker tokens cannot use the client API" in str(body)
    assert lines(settle(capsys, 1, timeout=QUIET)) == []


def test_ce4_rejected_submission_is_silent(srv, capsys):
    """CE4: 인증은 됐지만 400 으로 끝난 제출은 줄을 쓰지 않는다(§14.10 — 받아들여진 뒤에만)."""
    capsys.readouterr()
    body = copy.deepcopy(BODY)
    body["preset"] = "no-such-preset"
    assert submit_as(srv, "alice", "rcm/0.8.1", body=body)[0] == 400
    assert lines(settle(capsys, 1, timeout=QUIET)) == []


@pytest.mark.parametrize(
    "ua",
    [
        "rcm/",
        "rcm/0.8.1 extra",
        "rcm/\x1b[2J",
        "rcm/" + "1." * 32 + "1",  # 65자 — 정규식은 맞지만 너무 길다
        "rcm/0.8.1\t",
        "curl/8.4.0",
        "xrcm/0.8.1",
        None,
    ],
)
def test_ce5_odd_user_agents_write_no_line(srv, capsys, ua):
    """CE5: 형식에 안 맞는 User-Agent 는 줄을 쓰지 않고 ESC 바이트도 남기지 않는다."""
    capsys.readouterr()
    status, body = submit_as(srv, "alice", ua)
    assert status in (200, 201), body
    err = settle(capsys, 1, timeout=QUIET)
    assert lines(err) == [], err
    assert "\x1b" not in err
    assert ("1." * 32 + "1") not in err


# ── 엣지 ────────────────────────────────────────────────────────────────────


def test_cg1_memory_is_per_process(tmp_path, capsys):
    """CG1: 쌍의 기억은 프로세스(App) 하나다 — 새로 띄운 서버는 같은 쌍을 다시 쓴다."""
    for sub in ("one", "two"):
        (tmp_path / sub).mkdir()
        s = Server(tmp_path / sub, workers=False)
        try:
            capsys.readouterr()
            assert submit_as(s, "alice", "rcm/0.8.1")[0] in (200, 201)
            assert lines(settle(capsys, 1)) == [("alice-laptop", "0.8.1")]
        finally:
            s.close()


def test_cg2_concurrent_first_pair_logs_once(srv, capsys):
    """CG2: 같은 쌍이 동시에 처음 와도 줄은 정확히 하나."""
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def go(i):
        barrier.wait()
        codes.append(submit_as(srv, "alice", "rcm/0.8.1", tree_hash=uniq_hash(100 + i))[0])

    capsys.readouterr()
    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert all(c in (200, 201) for c in codes), codes
    assert lines(settle(capsys, 1)) == [("alice-laptop", "0.8.1")]


def test_cg3_a_joined_submission_adds_no_line_for_the_same_pair(srv, capsys):
    """CG3: 합류한 제출도 같은 쌍이면 더 쓰지 않고, 다른 토큰의 합류면 그 토큰의 줄이 하나."""
    capsys.readouterr()
    s1, b1 = submit_as(srv, "alice", "rcm/0.8.1")
    s2, b2 = submit_as(srv, "alice", "rcm/0.8.1")
    s3, b3 = submit_as(srv, "bob", "rcm/0.8.1")
    assert {s1, s2, s3} <= {200, 201}, (b1, b2, b3)
    assert b2.get("joined") is True and b3.get("joined") is True, (b2, b3)
    err = settle(capsys, 2)
    assert sorted(lines(err)) == [("alice-laptop", "0.8.1"), ("bob-desk", "0.8.1")], err


def test_cg4_admin_token_is_a_client_too(srv, capsys):
    """CG4: admin 도 클라이언트 종류다."""
    capsys.readouterr()
    assert submit_as(srv, "admin", "rcm/0.8.1")[0] in (200, 201)
    assert lines(settle(capsys, 1)) == [("macmini-admin", "0.8.1")]


def test_cg5_dev_version_string(srv, capsys):
    """CG5: 개발 버전 문자열도 그대로 — 실기 L9 가 이 줄을 본다."""
    capsys.readouterr()
    assert submit_as(srv, "alice", "rcm/0.7.0.dev0")[0] in (200, 201)
    err = settle(capsys, 1)
    assert "[rcm] client alice-laptop runs v0.7.0.dev0" in err.splitlines(), err


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_cg6_git_ref_submission_logs_too(tmp_path, capsys):
    """CG6: git_ref 제출도 같은 `POST /jobs` 라 한 줄."""
    from test_server_m3 import build_bare_repo, git_server

    bare = build_bare_repo(tmp_path)
    s = git_server(tmp_path, bare)
    try:
        capsys.readouterr()
        body = {
            "preset": "deploy",
            "inputs": {},
            "source": {"mode": "git_ref", "ref": "main"},
            "requester_label": "alice@host",
        }
        status, resp = s.req(
            "POST", "/jobs", token="alice", json_body=body, headers={"User-Agent": "rcm/0.8.1"}
        )
        assert status in (200, 201), resp
        assert lines(settle(capsys, 1)) == [("alice-laptop", "0.8.1")]
    finally:
        s.close()


def test_cg7_health_gains_no_follow_keys(srv):
    """CG7: health 에 `requires_python` 도 `follow*` 키도 없다(§5.3 · P2-10)."""
    status, body = srv.req("GET", "/api/health")
    assert status == 200
    assert "requires_python" not in body
    assert not [k for k in body if k.startswith("follow")], body
    assert body.get("schema_version", SCHEMA_VERSION) == SCHEMA_VERSION


def test_pair_memory_is_capped_and_says_so_once(srv, capsys, monkeypatch):
    """§14.10: 쌍이 상한에 차면 새 쌍은 쓰지 않고 찼다는 줄을 한 번만 쓴다."""
    assert server_module.CLIENT_VERSION_PAIRS_MAX == 256
    monkeypatch.setattr(server_module, "CLIENT_VERSION_PAIRS_MAX", 3)
    capsys.readouterr()
    for i in range(6):
        status, _ = submit_as(srv, "alice", f"rcm/0.8.{i}", tree_hash=uniq_hash(200 + i))
        assert status in (200, 201)
    err = settle(capsys, 3)
    assert lines(err) == [("alice-laptop", f"0.8.{i}") for i in range(3)], err
    full = "[rcm] client versions: 3 token/version pairs seen — not logging new ones"
    assert err.splitlines().count(full) == 1, err
