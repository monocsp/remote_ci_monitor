"""서버(M3) — `git_ref` 제출(400·502·504·합류·409 tree·`presets[].repo`) · `read_auth = basic` 의
진짜 HTTP Basic. 명세는 docs/m3-workplan.md §1.4 · §5.

git 은 tmp 안의 bare 레포만 부른다(실제 원격 없음). git 이 PATH 에 없으면 git 테스트는 skip.
`make_server` · `build_bare_repo` · `submit_ref` 는 test_cli_m3 · test_e2e_m3 도 쓴다.
"""

import base64
import http.client
import importlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import RepoConfig, parse_preset
from test_server import Server

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


def sh(name: str, script: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "argv": ["sh", "-c", script], "timeout_seconds": 60, **extra}


# deploy 는 git_ref 만 받고 `app` 레포를 가리킨다. gate 는 tree 만(기본) 받는다.
GIT_PRESETS = [
    sh(
        "deploy",
        "cat README.md && echo ref=$RCM_REF && echo ok",
        source_modes=["git_ref"],
        repo="app",
        expected_seconds=5,
    ),
    sh("gate", "echo gate"),
]


# ── 도우미 ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BareRepo:
    path: Path
    main_sha: str  # `main` 과 annotated 태그 `v1` 이 가리키는 커밋


def git_env(home: Path) -> dict[str, str]:
    """사용자 gitconfig·프롬프트가 섞이지 않는 git 환경."""
    return {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "rcm-test",
        "GIT_AUTHOR_EMAIL": "rcm@test.invalid",
        "GIT_COMMITTER_NAME": "rcm-test",
        "GIT_COMMITTER_EMAIL": "rcm@test.invalid",
    }


def build_bare_repo(tmp_path: Path) -> BareRepo:
    """커밋 2 · 브랜치 `main` · annotated 태그 `v1`(= main) 인 bare 레포를 tmp 에 만든다."""
    home = tmp_path / "githome"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    env = git_env(home)

    def git(*args: str, cwd: Path = work) -> str:
        out = subprocess.run(
            ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=60
        )
        return out.stdout.strip()

    git("init", "-q")
    git("symbolic-ref", "HEAD", "refs/heads/main")
    (work / "README.md").write_text("app repo\n")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    (work / "scripts").mkdir()
    (work / "scripts" / "deploy.sh").write_text("#!/bin/sh\necho deployed\n")
    git("add", "-A")
    git("commit", "-q", "-m", "second")
    git("tag", "-a", "v1", "-m", "release v1")  # annotated: refs/tags/v1^{} 가 커밋이다
    main_sha = git("rev-parse", "main")
    bare = tmp_path / "remote.git"
    git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    assert git("rev-parse", "main", cwd=bare) == main_sha
    assert git("rev-parse", "v1^{commit}", cwd=bare) == main_sha
    return BareRepo(path=bare, main_sha=main_sha)


def make_server(
    tmp_path: Path,
    presets: list[dict[str, Any]],
    *,
    workers: bool,
    repos: tuple[RepoConfig, ...] = (),
    **overrides: Any,
) -> Server:
    """test_server.Server 에 M3 프리셋·[[repos]] 를 끼운다. workers=True 면 그 뒤 App 을 띄운다."""
    s = Server(tmp_path, workers=False, **overrides)
    try:
        s.cfg.presets = tuple(parse_preset(p) for p in presets)
        s.cfg.repos = tuple(repos)
        if workers:
            s.app.start()
    except BaseException:
        s.close()
        raise
    return s


def git_server(
    tmp_path: Path, bare: BareRepo, *, workers: bool = False, **overrides: Any
) -> Server:
    return make_server(
        tmp_path,
        GIT_PRESETS,
        workers=workers,
        repos=(RepoConfig(name="app", url=str(bare.path)),),
        **overrides,
    )


def submit_ref(
    srv: Server,
    ref: str | None,
    *,
    token: str = "alice",
    preset: str = "deploy",
    join: bool | None = None,
    source: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    body: dict[str, Any] = {
        "preset": preset,
        "inputs": {},
        "source": source if source is not None else {"mode": "git_ref", "ref": ref},
        "requester_label": f"{token}@host",
    }
    if join is not None:
        body["join"] = join
    return srv.req("POST", "/jobs", token=token, json_body=body)


def basic(user: str, password: str) -> dict[str, str]:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def sse_first_event(srv: Server, headers: dict[str, str]) -> tuple[int, str | None]:
    """`GET /events` 를 열어 (상태, 첫 프레임의 event 이름) 을 돌려주고 닫는다."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    conn.connect()
    sock = conn.sock  # getresponse 뒤에는 conn.sock 이 None 이 된다
    conn.request("GET", "/events", headers={"Accept": "text/event-stream", **headers})
    resp = conn.getresponse()
    try:
        if resp.status != 200:
            resp.read()
            return resp.status, None
        event = None
        while True:
            line = resp.readline()
            if not line:
                return 200, event
            text = line.decode("utf-8").rstrip("\r\n")
            if text.startswith("event:"):
                event = text.partition(":")[2].strip()
            elif text == "" and event:
                return 200, event
    finally:
        try:
            sock.close()
        finally:
            conn.close()


def queue_ids(srv: Server) -> list[int]:
    return [r["id"] for r in srv.req("GET", "/api/status", token="alice")[1]["pools"][0]["queue"]]


@pytest.fixture
def bare(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    return build_bare_repo(tmp_path)


@pytest.fixture
def git_srv(tmp_path, bare):
    s = git_server(tmp_path, bare)
    yield s
    s.close()


@pytest.fixture
def basic_srv(tmp_path):
    s = Server(tmp_path, workers=False, read_auth="basic")
    yield s
    s.close()


# ── git_ref 제출 ─────────────────────────────────────────────────────────────


@needs_git
def test_git_ref_submit_creates_a_queued_job_with_the_resolved_sha(git_srv, bare):
    status, body = submit_ref(git_srv, "main")
    # 명세 §1.4-5 는 「응답 200」 이지만 tree 경로의 새 잡은 201 Created 다 — 같은 규약으로 잠근다
    assert status == 201, body
    assert body["joined"] is False and body["state"] == "queued"
    assert body["sha"] == bare.main_sha and "upload" not in body
    jid = body["job_id"]
    assert body["url"].endswith(f"/#/jobs/{jid}")
    status, view = git_srv.req("GET", f"/jobs/{jid}")
    assert status == 200 and view["state"] == "queued" and view["position"] == 1
    assert view["source"] == {"mode": "git_ref", "repo": "app", "ref": "main", "sha": bare.main_sha}
    assert view["queued_at"] is not None and view["queued_at"] == view["created_at"]
    assert view["reason"] == "waiting_for_lane"
    assert view["requester"] == {"name": "alice-laptop", "label": "alice@host"}
    j = git_srv.store.get_job(jid)
    assert j.source.identity == bare.main_sha and j.source.ref == "main"
    assert not (git_srv.app.job_dir(jid) / "tree.tar.gz").exists()  # 트리 업로드는 없다
    assert [t["state"] for t in git_srv.req("GET", "/api/status")[1]["pools"][0]["queue"]] == [
        "queued"
    ]


@needs_git
def test_same_commit_joins_whatever_the_ref_is_called(git_srv, bare):
    first = submit_ref(git_srv, "main")[1]
    status, again = submit_ref(git_srv, "main", token="bob")
    assert status == 200 and again["joined"] is True and again["job_id"] == first["job_id"]
    assert again["sha"] == bare.main_sha and again["state"] == "queued"
    by_tag = submit_ref(git_srv, "v1", token="admin")[1]  # annotated 태그 → 같은 커밋 → 합류
    assert by_tag["joined"] is True and by_tag["job_id"] == first["job_id"]
    by_sha = submit_ref(git_srv, bare.main_sha, token="alice")[1]  # 40 hex 는 원격 없이 그대로
    assert by_sha["joined"] is True and by_sha["job_id"] == first["job_id"]
    view = git_srv.req("GET", f"/jobs/{first['job_id']}")[1]
    assert [j["name"] for j in view["joiners"]] == ["bob-desk", "macmini-admin"]
    nojoin = submit_ref(git_srv, "main", token="bob", join=False)[1]
    assert nojoin["joined"] is False and nojoin["job_id"] != first["job_id"]
    assert nojoin["sha"] == bare.main_sha
    assert queue_ids(git_srv) == [first["job_id"], nojoin["job_id"]]


@needs_git
@pytest.mark.parametrize(
    "ref",
    ["--upload-pack=x", "-x", "a..b", "", "main.lock", "a b", "x^", "y:z", "~1", "a" * 201],
)
def test_bad_refs_are_400_naming_the_field(git_srv, ref):
    status, body = submit_ref(git_srv, ref)
    assert status == 400 and "source.ref" in body["error"], body
    assert queue_ids(git_srv) == []


@needs_git
def test_missing_or_non_string_ref_is_400(git_srv):
    for source in (
        {"mode": "git_ref"},
        {"mode": "git_ref", "ref": 123},
        {"mode": "git_ref", "ref": None},
        {"mode": "git_ref", "ref": ["main"]},
    ):
        status, body = submit_ref(git_srv, None, source=source)
        assert status == 400 and "source.ref" in body["error"], (source, body)
    assert queue_ids(git_srv) == []


@needs_git
def test_unknown_ref_is_502_without_leaking_the_repo_location(git_srv, bare):
    status, body = submit_ref(git_srv, "nope")
    assert status == 502, body
    assert "cannot resolve" in body["error"] and "'nope'" in body["error"]
    assert "'app'" in body["error"]
    assert str(bare.path) not in body["error"] and "remote.git" not in body["error"]
    assert queue_ids(git_srv) == []  # 잡을 만들지 않았다
    doc = git_srv.req("GET", "/api/status")[1]
    assert doc["server"]["last_error"] is None  # 사용자 오류(502)는 서버 오류가 아니다


@needs_git
def test_source_mode_must_match_the_preset(git_srv):
    tree = {
        "mode": "tree",
        "repo": "org/app",
        "base_sha": "abc123f",
        "dirty": False,
        "tree_hash": "9f" * 32,
        "bytes": 10,
    }
    status, body = submit_ref(git_srv, None, source=tree)
    assert status == 400 and "accepts source modes" in body["error"]
    assert "git_ref" in body["error"]
    status, body = submit_ref(git_srv, "main", preset="gate")
    assert status == 400 and "accepts source modes" in body["error"] and "tree" in body["error"]
    status, body = submit_ref(git_srv, None, source={"mode": "svn", "ref": "main"})
    assert status == 400
    assert queue_ids(git_srv) == []


@needs_git
def test_tree_upload_to_a_git_ref_job_is_409(git_srv):
    jid = submit_ref(git_srv, "main")[1]["job_id"]
    status, body = git_srv.upload(jid)
    assert status == 409, body
    assert git_srv.store.get_job(jid).state == "queued"
    assert not (git_srv.app.job_dir(jid) / "tree.tar.gz").exists()


@needs_git
def test_status_presets_carry_repo(git_srv):
    doc = git_srv.req("GET", "/api/status")[1]
    by_name = {p["name"]: p for p in doc["presets"]}
    assert by_name["deploy"]["repo"] == "app"
    assert by_name["deploy"]["source_modes"] == ["git_ref"]
    assert by_name["gate"]["repo"] is None and by_name["gate"]["source_modes"] == ["tree"]


@needs_git
def test_resolve_timeout_is_504_with_the_ref_and_seconds(git_srv, bare, monkeypatch):
    gitops = importlib.import_module("remote_ci_monitor.gitops")
    server_mod = importlib.import_module("remote_ci_monitor.server")
    timeout_cls = next(
        (getattr(gitops, n) for n in ("GitTimeout", "GitTimeoutError") if hasattr(gitops, n)),
        gitops.GitError,
    )
    calls: list[tuple[Any, ...]] = []

    def slow_resolve(url, ref, **kwargs):
        calls.append((url, ref, kwargs))
        raise timeout_cls("git ls-remote timed out after 20s")

    monkeypatch.setattr(gitops, "resolve_ref", slow_resolve)
    monkeypatch.setattr(server_mod, "resolve_ref", slow_resolve, raising=False)
    status, body = submit_ref(git_srv, "main")
    assert status == 504, body
    assert "resolving 'main' timed out after" in body["error"]
    assert str(bare.path) not in body["error"]
    assert calls and calls[0][0] == str(bare.path) and calls[0][1] == "main"
    assert queue_ids(git_srv) == []


# ── read_auth = basic ────────────────────────────────────────────────────────


def test_basic_mode_challenges_anonymous_reads_with_a_basic_realm(basic_srv):
    for path in ("/", "/api/status", "/static/app.js", "/events"):
        status, headers, body = basic_srv.req("GET", path, raw=True)
        assert status == 401, path
        challenge = headers.get("WWW-Authenticate", "")
        assert challenge.startswith('Basic realm="rcm"'), (path, challenge)
        assert 'charset="UTF-8"' in challenge
        assert b"<html" not in body.lower()  # JSON 한 줄 — 브라우저 프롬프트는 헤더가 만든다
    assert basic_srv.req("POST", "/api/eta", json_body={"preset": "gate"})[0] == 401
    jid = basic_srv.submit()[1]["job_id"]
    status, headers, _ = basic_srv.req("GET", f"/jobs/{jid}", raw=True)
    assert status == 401 and headers.get("WWW-Authenticate", "").startswith("Basic")


def test_basic_credentials_are_accepted_for_reads_events_log_and_whoami(basic_srv):
    alice = basic("alice-laptop", basic_srv.tokens["alice"])
    assert basic_srv.req("GET", "/api/status", headers=alice)[0] == 200
    assert basic_srv.req("GET", "/", headers=alice)[0] == 200
    assert basic_srv.req("GET", "/static/app.js", headers=alice)[0] == 200
    assert basic_srv.req("POST", "/api/eta", json_body={"preset": "gate"}, headers=alice)[0] == 200
    status, body = basic_srv.req("GET", "/api/whoami", headers=alice)
    assert status == 200 and body == {"name": "alice-laptop", "admin": False}
    admin = basic("macmini-admin", basic_srv.tokens["admin"])
    assert basic_srv.req("GET", "/api/whoami", headers=admin)[1]["admin"] is True
    assert sse_first_event(basic_srv, alice) == (200, "hello")
    jid = basic_srv.submit()[1]["job_id"]  # alice 의 잡(Bearer 로 제출)
    view = basic_srv.req("GET", f"/jobs/{jid}", headers=alice)[1]
    assert view["state"] == "uploading" and view["requester"]["name"] == "alice-laptop"
    # 로그도 읽기 라우트 — 그 잡의 토큰이면 Basic 으로 된다(남의 잡은 여전히 403)
    assert basic_srv.req("GET", f"/jobs/{jid}/log", headers=alice, raw=True)[0] == 200
    assert basic_srv.req("GET", f"/jobs/{jid}/log", headers=admin, raw=True)[0] == 200
    bob = basic("bob-desk", basic_srv.tokens["bob"])
    assert basic_srv.req("GET", f"/jobs/{jid}/log", headers=bob)[0] == 403


def test_writes_take_bearer_only_even_in_basic_mode(basic_srv):
    """(리뷰 반영) 브라우저가 Basic 을 자동으로 붙이므로 쓰기에 허용하면 내부망 CSRF 가 된다."""
    alice = basic("alice-laptop", basic_srv.tokens["alice"])
    admin = basic("macmini-admin", basic_srv.tokens["admin"])
    jid = basic_srv.submit()[1]["job_id"]  # Bearer 로는 된다
    submit = {
        "preset": "ok",
        "inputs": {},
        "source": {"mode": "tree", "tree_hash": "ab" * 32, "bytes": 10},
    }
    for method, path, hdr in (
        ("POST", "/jobs", alice),
        ("PUT", f"/jobs/{jid}/tree", alice),
        ("POST", f"/jobs/{jid}/cancel", alice),
        ("POST", "/pause", admin),
        ("POST", "/resume", admin),
    ):
        body = b"x" if method == "PUT" else None
        payload = submit if path == "/jobs" else None
        status, headers, _ = basic_srv.req(
            method, path, headers=hdr, body=body, json_body=payload, raw=True
        )
        assert status == 401, (method, path, status)
        assert headers.get("WWW-Authenticate", "").startswith('Bearer realm="rcm"'), (path, headers)
    assert basic_srv.store.get_job(jid).state == "uploading"  # 아무것도 바뀌지 않았다
    assert basic_srv.store.get_paused() is None
    assert basic_srv.req("POST", "/pause", token="admin", json_body={})[0] == 200
    assert basic_srv.req("POST", "/resume", token="admin", json_body={})[0] == 200
    assert (
        basic_srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})[1]["state"]
        == "cancelled"
    )


def test_basic_rejects_wrong_password_wrong_username_and_revoked_tokens(basic_srv):
    tok = basic_srv.tokens["alice"]
    for hdr in (
        basic("alice-laptop", "not-the-token"),
        basic("bob-desk", tok),  # 남의 토큰에 내 이름
        basic("", tok),
        basic("alice-laptop", ""),
    ):
        status, headers, _ = basic_srv.req("GET", "/api/status", headers=hdr, raw=True)
        assert status == 401, hdr
        assert headers.get("WWW-Authenticate", "").startswith('Basic realm="rcm"')
    assert basic_srv.req("GET", "/api/whoami", headers=basic("bob-desk", tok))[0] == 401
    assert basic_srv.store.revoke_token("alice-laptop", datetime.now(UTC)) is True
    assert basic_srv.req("GET", "/api/status", headers=basic("alice-laptop", tok))[0] == 401
    assert basic_srv.req("GET", "/api/status", token="alice")[0] == 401  # Bearer 도 폐기됐다
    assert sse_first_event(basic_srv, basic("alice-laptop", tok))[0] == 401
    assert (
        basic_srv.req("GET", "/api/status", headers=basic("bob-desk", basic_srv.tokens["bob"]))[0]
        == 200
    )


def test_bearer_still_works_and_challenges_depend_on_the_route(basic_srv):
    assert basic_srv.req("GET", "/api/status", token="alice")[0] == 200
    assert basic_srv.req("GET", "/api/whoami", token="admin")[1] == {
        "name": "macmini-admin",
        "admin": True,
    }
    bearer = {"Authorization": f"Bearer {basic_srv.tokens['alice']}"}
    assert sse_first_event(basic_srv, bearer) == (200, "hello")
    # 읽기 라우트의 401 은 basic 모드에서 언제나 Basic 챌린지(브라우저 프롬프트) — 틀린 Bearer 도
    status, headers, _ = basic_srv.req("GET", "/api/status", token="garbage", raw=True)
    assert status == 401 and headers.get("WWW-Authenticate", "").startswith('Basic realm="rcm"')
    status, headers, _ = basic_srv.req("GET", "/api/whoami", raw=True)
    assert status == 401 and headers.get("WWW-Authenticate", "").startswith('Basic realm="rcm"')
    # 쓰기 라우트의 401 은 기존처럼 Bearer 챌린지
    status, headers, _ = basic_srv.req("POST", "/jobs", json_body={"preset": "ok"}, raw=True)
    assert status == 401 and headers.get("WWW-Authenticate", "").startswith('Bearer realm="rcm"')
    status, headers, _ = basic_srv.req(
        "POST", "/jobs", token="garbage", json_body={"preset": "ok"}, raw=True
    )
    assert status == 401 and headers.get("WWW-Authenticate", "").startswith('Bearer realm="rcm"')


@pytest.mark.parametrize(
    "value",
    [
        "Basic",
        "Basic ",
        "Basic not-base64!!",
        "Basic " + base64.b64encode(b"alice-laptop").decode(),  # 콜론 없음
        "Basic " + base64.b64encode(b"\xff\xfe:zz").decode(),  # UTF-8 아님
        "Basic YWxpY2U6 YWxpY2U6",
        "basic",
        "Digest username=alice",
    ],
)
def test_malformed_credentials_are_401_not_500(basic_srv, value):
    status, headers, body = basic_srv.req(
        "GET", "/api/status", headers={"Authorization": value}, raw=True
    )
    assert status == 401, (value, status, body)
    assert headers.get("WWW-Authenticate", "").startswith('Basic realm="rcm"')
    doc = basic_srv.req("GET", "/api/status", token="alice")[1]  # 서버는 멀쩡하고 오류도 없다
    assert doc["schema_version"] == 1 and doc["server"]["last_error"] is None


def test_health_stays_open_without_credentials_in_basic_mode(basic_srv):
    status, body = basic_srv.req("GET", "/api/health")
    assert status == 200 and body["ok"] is True and body["db"] is True


def test_basic_header_is_ignored_when_read_auth_is_none(tmp_path):
    s = Server(tmp_path, workers=False)  # read_auth = none (기본)
    try:
        wrong = basic("alice-laptop", "wrong")
        status, body = s.req("GET", "/api/status", headers=wrong)
        assert status == 200 and body["schema_version"] == 1  # 401 이 아니라 익명 읽기
        right = basic("alice-laptop", s.tokens["alice"])
        assert s.req("GET", "/api/status", headers=right)[0] == 200
        assert s.req("GET", "/", headers=wrong)[0] == 200
        assert sse_first_event(s, wrong) == (200, "hello")
        # Basic 은 basic 모드에서만 자격이다 — none 모드에서는 맞는 값도 무시된다
        status, headers, _ = s.req("GET", "/api/whoami", headers=right, raw=True)
        assert status == 401 and headers.get("WWW-Authenticate", "").startswith("Bearer")
        assert s.req("POST", "/jobs", json_body={"preset": "ok"}, headers=right)[0] == 401
    finally:
        s.close()
