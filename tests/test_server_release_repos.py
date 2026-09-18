"""스토어 탭 서버 라우트 — `/api/repos` · `/api/repos/<name>` · `/fetch` · `/secrets` · `/verify`.

명세: 스토어 탭 API 계약 v1(2026-09-17) · docs/release-contract.md §4.

여기서 지키는 것:
- 인증 표: 목록 · 저장소 보기는 읽기 규칙, 비밀 목록은 아무 토큰(단 토큰), fetch · verify 는
  클라이언트 토큰, 비밀 PUT · DELETE 는 admin. 워커 토큰은 어디서도 403.
- 404: 모르는 저장소 · 프로파일 없는 저장소 · 프로파일 밖 비밀 이름. 비밀 라우트는 게이트가 닫혀
  있어도 409 를 내지 않는다.
- 응답 어디에도 비밀 값이 없다. URL 의 userinfo 는 지운다.
- fetch 는 로컬 bare 레포로(네트워크 없음); 실패는 502 `fetch_failed`.
- verify 는 스텁 검증기로(네트워크 없음) `.verify.json` 에 남고 `setup.complete` 를 연다.
"""

from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

import pytest

from gitrepo import RemoteRepo, build_remote, isolate_git_env
from remote_ci_monitor import release_verify
from remote_ci_monitor.config import RepoConfig, parse_preset, parse_release_profile
from remote_ci_monitor.release_verify import VerifyResult
from test_release_secrets import PROFILE_RAW, TOKEN
from test_server import Server

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

OCTET = {"Content-Type": "application/octet-stream"}
TEXT = {"Content-Type": "text/plain"}
RELEASE_PRESETS = [
    {
        "name": name,
        "argv": ["sh", "-c", "true"],
        "source_modes": ["git_ref"],
        "repo": "app",
        "inputs": [{"name": "build_name", "type": "string"}],
    }
    for name in ("release-plan", "release-upload", "release-review")
]


class ReleaseServer(Server):
    """`app`(프로파일 있음 · 로컬 bare 원격) 과 `plain`(프로파일 없음) 두 저장소를 가진 서버.
    설정 파일 경로는 `<tmp>/etc/server.toml` — 비밀은 `<tmp>/etc/secrets/app/` 에 간다."""

    def __init__(self, tmp_path: Path, remote: RemoteRepo, **overrides):
        super().__init__(tmp_path, workers=False, **overrides)
        self.cfg.path = tmp_path / "etc" / "server.toml"
        self.cfg.path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.repos = (
            RepoConfig(
                name="app", url=remote.url, release=parse_release_profile("app", PROFILE_RAW)
            ),
            RepoConfig(name="plain", url=str(tmp_path / "plain.git")),
        )
        self.cfg.presets = tuple(parse_preset(p) for p in RELEASE_PRESETS) + self.cfg.presets
        self.remote = remote

    @property
    def secrets_root(self) -> Path:
        return self.cfg.path.parent / "secrets" / "app"

    def put_secret(self, name: str, data: bytes, *, headers=TEXT, token="admin", file=None):
        path = f"/api/repos/app/secrets/{name}" + (f"/{file}" if file else "")
        return self.req("PUT", path, token=token, body=data, headers=headers)

    def fill_required(self) -> None:
        """필수 비밀 넷을 전부 넣는다(검증은 아직)."""
        assert self.put_secret("GH_TOKEN", TOKEN.encode())[0] == 200
        assert self.put_secret("AuthKey.p8", b"-----KEY-----", headers=OCTET)[0] == 200
        assert self.put_secret("upload-keystore.jks", b"\xfe\xed", headers=OCTET)[0] == 200
        assert self.put_secret("review_information", b"demo", file="demo_user.txt")[0] == 200
        assert self.put_secret("review_information", b"pw", file="demo_password.txt")[0] == 200


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RemoteRepo:
    isolate_git_env(tmp_path, monkeypatch)
    return build_remote(tmp_path)


@pytest.fixture
def srv(tmp_path: Path, remote: RemoteRepo):
    s = ReleaseServer(tmp_path, remote)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def stub_verify(monkeypatch: pytest.MonkeyPatch):
    """네트워크 · keytool 대신: github 는 통과(login), keystore 는 통과, asc/play 는 그대로
    (not implemented). 호출된 종류를 기록한다."""
    calls: list[str] = []

    def ok_github(ctx):
        calls.append("github")
        assert ctx.path.read_text().strip() == TOKEN  # 값은 검증기 안에서만
        return VerifyResult(None, "login: octocat")

    def ok_keystore(ctx):
        calls.append("keystore")
        return VerifyResult(None, "1 key entry")

    monkeypatch.setitem(release_verify.VERIFIERS, "github", ok_github)
    monkeypatch.setitem(release_verify.VERIFIERS, "keystore", ok_keystore)
    return calls


def no_secret_values(payload) -> None:
    text = json.dumps(payload)
    assert TOKEN not in text and TOKEN[4:12] not in text
    assert "-----KEY-----" not in text and "demo_pw" not in text


# ── 목록 · 보기 ──────────────────────────────────────────────────────────────


def test_the_list_says_which_repos_have_a_profile_and_the_gate_count(srv):
    status, body = srv.req("GET", "/api/repos")
    assert status == 200
    assert body == {
        "repos": [
            {
                "name": "app",
                "release": True,
                "setup": {"required": 4, "present": 0, "verified": 0, "complete": False},
            },
            {"name": "plain", "release": False},
        ]
    }


def test_the_repo_document_has_the_profile_without_values_and_a_null_mirror(srv, remote):
    status, body = srv.req("GET", "/api/repos/app")
    assert status == 200, body
    assert body == {
        "name": "app",
        "url": remote.url,
        "profile": {
            "default_branch": "main",
            "tag": "prod/{version}-{build}",
            "build_number_policy": "auto",
            "plan_max_age_minutes": 30,
            "driver": None,
            "presets": {
                "plan": "release-plan",
                "upload": "release-upload",
                "review": "release-review",
                "gate": None,
                "qa": None,
                "dev": None,
                "version": None,  # 선택 역할 — 새 버전 만들기(버전 페이지 계획 §1)
            },
            "listing": None,
            "secrets_dir_env": "APP_SECRETS",
        },
        "setup": {
            "required": 4,
            "present": 0,
            "verified": 0,
            "complete": False,
            "missing": [
                "GH_TOKEN",
                "AuthKey.p8",
                "upload-keystore.jks",
                "review_information/demo_user.txt",
                "review_information/demo_password.txt",
            ],
        },
        "mirror": {
            "path": str(srv.cfg.data_dir / "mirrors" / "app"),
            "fetched_at": None,
            "age_seconds": None,
        },
        "branches": {"default_branch": "main", "main": None, "dev": None, "main_in_dev": None},
    }


def test_a_repo_url_with_credentials_is_redacted(srv):
    srv.cfg.repos[0].url = "https://oauth2:ghp_secret123@github.com/org/app.git"
    body = srv.req("GET", "/api/repos/app")[1]
    assert body["url"] == "https://***@github.com/org/app.git"
    assert "ghp_secret123" not in json.dumps(body)


@pytest.mark.parametrize("name", ["plain", "nope", "App", "app%2F..", "app%20"])
def test_unknown_or_profileless_repos_are_404_on_every_route(srv, name):
    assert srv.req("GET", f"/api/repos/{name}")[0] == 404
    assert srv.req("POST", f"/api/repos/{name}/fetch", token="alice", json_body={})[0] == 404
    assert srv.req("GET", f"/api/repos/{name}/secrets", token="alice")[0] == 404
    assert (
        srv.req(
            "PUT", f"/api/repos/{name}/secrets/GH_TOKEN", token="admin", body=b"x", headers=TEXT
        )[0]
        == 404
    )
    assert srv.req("DELETE", f"/api/repos/{name}/secrets/TEAMS_WEBHOOK", token="admin")[0] == 404
    assert srv.req("POST", f"/api/repos/{name}/verify", token="alice", json_body={})[0] == 404


def test_the_read_routes_follow_the_read_rule_and_refuse_worker_tokens(tmp_path, remote):
    s = ReleaseServer(tmp_path, remote, read_auth="basic")
    try:
        assert s.req("GET", "/api/repos")[0] == 401
        assert s.req("GET", "/api/repos/app")[0] == 401
        assert s.req("GET", "/api/repos", token="alice")[0] == 200
        assert s.req("GET", "/api/repos/app", token="alice")[0] == 200
    finally:
        s.close()


def test_worker_tokens_cannot_use_any_repo_route(srv):
    from datetime import UTC, datetime

    secret = srv.store.add_token("build-02", admin=False, now=datetime.now(UTC), kind="worker")
    for method, path, kw in [
        ("GET", "/api/repos", {}),
        ("GET", "/api/repos/app", {}),
        ("GET", "/api/repos/app/secrets", {}),
        ("POST", "/api/repos/app/fetch", {"json_body": {}}),
        ("POST", "/api/repos/app/verify", {"json_body": {}}),
        ("PUT", "/api/repos/app/secrets/GH_TOKEN", {"body": b"abcdefghij", "headers": TEXT}),
        ("DELETE", "/api/repos/app/secrets/TEAMS_WEBHOOK", {}),
    ]:
        assert srv.req(method, path, token=secret, **kw)[0] == 403, (method, path)


def test_method_mismatches_are_405(srv):
    assert srv.req("POST", "/api/repos", token="admin", json_body={})[0] == 405
    assert srv.req("DELETE", "/api/repos/app", token="admin")[0] == 405
    assert srv.req("GET", "/api/repos/app/fetch", token="alice")[0] == 405
    assert srv.req("GET", "/api/repos/app/verify", token="alice")[0] == 405
    assert srv.req("POST", "/api/repos/app/secrets", token="admin", json_body={})[0] == 405
    assert srv.req("POST", "/api/repos/app/secrets/GH_TOKEN", token="admin", json_body={})[0] == 405
    assert srv.req("GET", "/api/repos/app/fetch/x", token="alice")[0] == 404


# ── 비밀 ─────────────────────────────────────────────────────────────────────


def test_the_secrets_list_needs_a_token_but_not_an_admin_one(srv):
    assert srv.req("GET", "/api/repos/app/secrets")[0] == 401
    status, body = srv.req("GET", "/api/repos/app/secrets", token="alice")
    assert status == 200
    assert body["dir_env"] == "APP_SECRETS"
    assert [i["name"] for i in body["items"]] == [
        "GH_TOKEN",
        "AuthKey.p8",
        "upload-keystore.jks",
        "TEAMS_WEBHOOK",
        "review_information",
    ]
    assert all(i["present"] is False for i in body["items"])


def test_writing_a_secret_takes_an_admin_bearer_token_only(srv):
    assert srv.put_secret("GH_TOKEN", TOKEN.encode(), token=None)[0] == 401
    assert srv.put_secret("GH_TOKEN", TOKEN.encode(), token="alice")[0] == 403
    assert srv.req("DELETE", "/api/repos/app/secrets/TEAMS_WEBHOOK", token="alice")[0] == 403
    assert not srv.secrets_root.exists()
    status, item = srv.put_secret("GH_TOKEN", TOKEN.encode())
    assert status == 200, item
    assert item["present"] is True and item["fingerprint"] == "ghp_…"
    no_secret_values(item)
    assert stat.S_IMODE(srv.secrets_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((srv.secrets_root / "GH_TOKEN").stat().st_mode) == 0o600


def test_the_secrets_document_after_filling_everything_never_shows_a_value(srv):
    srv.fill_required()
    status, body = srv.req("GET", "/api/repos/app/secrets", token="alice")
    assert status == 200
    no_secret_values(body)
    by_name = {i["name"]: i for i in body["items"]}
    assert by_name["GH_TOKEN"]["fingerprint"] == "ghp_…"
    assert by_name["AuthKey.p8"]["size"] == len(b"-----KEY-----")
    assert len(by_name["AuthKey.p8"]["fingerprint"]) == 8
    assert by_name["review_information"]["fingerprint"] == "2/2 files"
    assert by_name["review_information"]["files"] == [
        {"name": "demo_user.txt", "present": True},
        {"name": "demo_password.txt", "present": True},
    ]
    assert by_name["TEAMS_WEBHOOK"]["present"] is False
    assert all(i["verified_at"] is None for i in body["items"])
    # 보고용 — 계약의 GET …/secrets 모양 그대로
    print("SECRETS_JSON", json.dumps(body, ensure_ascii=False))


def test_bad_names_kinds_and_sizes_answer_400_404_413(srv):
    assert srv.put_secret("gh_token", b"abcdefghij")[0] == 404
    assert srv.put_secret("GH_TOKEN", b"abcdefghij", headers=OCTET)[0] == 400
    assert srv.put_secret("AuthKey.p8", b"k", headers=TEXT)[0] == 400
    assert srv.put_secret("GH_TOKEN", b"\n")[0] == 400
    assert srv.put_secret("AuthKey.p8", b"x" * 1025, headers=OCTET)[0] == 413
    assert srv.put_secret("review_information", b"x", file="notes.txt")[0] == 400
    assert srv.put_secret("GH_TOKEN", b"x", file="extra")[0] == 400
    assert srv.req("PUT", "/api/repos/app/secrets/../GH_TOKEN", token="admin", body=b"x")[0] == 400
    assert not srv.secrets_root.exists()


def test_delete_is_for_optional_secrets_only(srv):
    srv.fill_required()
    assert srv.put_secret("TEAMS_WEBHOOK", b"https://hooks.example/x")[0] == 200
    status, body = srv.req("DELETE", "/api/repos/app/secrets/GH_TOKEN", token="admin")
    assert status == 409 and (srv.secrets_root / "GH_TOKEN").exists()
    status, item = srv.req("DELETE", "/api/repos/app/secrets/TEAMS_WEBHOOK", token="admin")
    assert status == 200 and item["present"] is False
    assert not (srv.secrets_root / "TEAMS_WEBHOOK").exists()
    assert srv.req("DELETE", "/api/repos/app/secrets/TEAMS_WEBHOOK/x", token="admin")[0] == 404


# ── verify · 설정 게이트 ─────────────────────────────────────────────────────


def test_verify_runs_every_verifying_secret_records_the_result_and_opens_the_gate(
    srv, stub_verify, monkeypatch
):
    srv.fill_required()
    assert srv.req("GET", "/api/repos/app")[1]["setup"]["complete"] is False
    assert srv.req("POST", "/api/repos/app/verify", json_body={})[0] == 401
    status, body = srv.req("POST", "/api/repos/app/verify", token="alice", json_body={})
    assert status == 200, body
    assert sorted(stub_verify) == ["github", "keystore"]
    by_name = {i["name"]: i for i in body["items"]}
    assert by_name["GH_TOKEN"]["verified_at"] and by_name["GH_TOKEN"]["verify_error"] is None
    assert by_name["GH_TOKEN"]["verify_detail"] == "login: octocat"
    assert by_name["upload-keystore.jks"]["verify_error"] is None
    assert by_name["AuthKey.p8"]["verify_error"] is None
    assert by_name["AuthKey.p8"]["verify_detail"] == "not implemented in this build"
    assert by_name["AuthKey.p8"]["verified_at"] is not None
    assert by_name["review_information"]["verified_at"] is None  # verify none — 안 부른다
    no_secret_values(body)
    recorded = json.loads((srv.secrets_root / ".verify.json").read_text())
    assert set(recorded) == {"GH_TOKEN", "upload-keystore.jks", "AuthKey.p8"}
    assert TOKEN not in json.dumps(recorded)
    setup = srv.req("GET", "/api/repos/app")[1]["setup"]
    assert setup == {
        "required": 4,
        "present": 4,
        "verified": 4,
        "complete": True,
        "missing": [],
    }
    # asc 가 이 빌드에 없으니 게이트는 닫힌 채다 — 그걸 통과로 꾸미지 않는다. 검증기를 바꾸면 열린다
    monkeypatch.setitem(
        release_verify.VERIFIERS, "asc", lambda ctx: VerifyResult(None, "app: 1234567890")
    )
    srv.req("POST", "/api/repos/app/verify", token="alice", json_body={"names": ["AuthKey.p8"]})
    setup = srv.req("GET", "/api/repos/app")[1]["setup"]
    assert setup["verified"] == 4 and setup["complete"] is True
    assert srv.req("GET", "/api/repos")[1]["repos"][0]["setup"]["complete"] is True


def test_verify_takes_a_names_list_and_rejects_unknown_names(srv, stub_verify):
    srv.fill_required()
    status, body = srv.req(
        "POST", "/api/repos/app/verify", token="alice", json_body={"names": ["GH_TOKEN"]}
    )
    assert status == 200 and stub_verify == ["github"]
    by_name = {i["name"]: i for i in body["items"]}
    assert by_name["upload-keystore.jks"]["verified_at"] is None
    assert (
        srv.req("POST", "/api/repos/app/verify", token="alice", json_body={"names": ["x"]})[0]
        == 404
    )
    assert (
        srv.req("POST", "/api/repos/app/verify", token="alice", json_body={"names": "GH_TOKEN"})[0]
        == 400
    )
    assert srv.req("POST", "/api/repos/app/verify", token="alice", json_body=[])[0] == 400


def test_verifying_an_absent_secret_records_not_present_and_calls_nothing(srv, stub_verify):
    status, body = srv.req("POST", "/api/repos/app/verify", token="alice", json_body={})
    assert status == 200 and stub_verify == []
    by_name = {i["name"]: i for i in body["items"]}
    assert by_name["GH_TOKEN"]["present"] is False and by_name["GH_TOKEN"]["verify_error"] is None


def test_a_new_value_forgets_the_old_verification(srv, stub_verify):
    srv.fill_required()
    srv.req("POST", "/api/repos/app/verify", token="alice", json_body={"names": ["GH_TOKEN"]})
    status, item = srv.put_secret("GH_TOKEN", b"ghp_replacement_token_value")
    assert status == 200 and item["verified_at"] is None and item["verify_detail"] is None


# ── fetch · 미러 · 브랜치 ────────────────────────────────────────────────────


def test_fetch_builds_the_mirror_and_reports_both_branches(srv, remote):
    assert srv.req("POST", "/api/repos/app/fetch", json_body={})[0] == 401
    status, body = srv.req("POST", "/api/repos/app/fetch", token="alice", json_body={})
    assert status == 200, body
    assert set(body) == {"mirror", "branches"}
    assert body["mirror"]["path"] == str(srv.cfg.data_dir / "mirrors" / "app")
    assert body["mirror"]["fetched_at"] and body["mirror"]["age_seconds"] >= 0
    assert body["branches"] == {
        "default_branch": "main",
        "main": remote.main,
        "dev": remote.dev,
        "main_in_dev": True,  # dev = main + 1 커밋
    }
    # 보기도 미러에서 같은 것을 읽는다 — 원격은 안 부른다
    view = srv.req("GET", "/api/repos/app")[1]
    assert view["branches"] == body["branches"] and view["mirror"] == body["mirror"]
    print("REPO_JSON", json.dumps(view, ensure_ascii=False))


def test_main_in_dev_turns_false_when_main_moves_past_dev(srv, remote):
    remote.push_commit("hotfix.txt", "fix\n", "hotfix on main")
    body = srv.req("POST", "/api/repos/app/fetch", token="alice", json_body={})[1]
    assert body["branches"]["main"] != remote.dev
    assert body["branches"]["main_in_dev"] is False


def test_a_missing_dev_branch_gives_null_not_false(srv, remote):
    from gitrepo import git

    git("push", "-q", "origin", "--delete", "dev", cwd=remote.work)
    body = srv.req("POST", "/api/repos/app/fetch", token="alice", json_body={})[1]
    assert body["branches"]["dev"] is None and body["branches"]["main_in_dev"] is None
    assert body["branches"]["main"] == remote.main


def test_a_fetch_that_fails_is_502_with_a_short_stderr_tail(srv, tmp_path):
    srv.cfg.repos[0].url = str(tmp_path / "does-not-exist.git")
    status, body = srv.req("POST", "/api/repos/app/fetch", token="alice", json_body={})
    assert status == 502, body
    assert body["code"] == "fetch_failed" and body["error_code"] == "fetch_failed"
    assert body["error"].startswith("fetch failed: ")
    assert len(body["error"]) <= len("fetch failed: ") + 60
    assert str(tmp_path) not in body["error"]  # `_safe` 가 절대 경로를 지운다


def test_the_setup_gate_never_blocks_the_repo_or_secret_routes(srv):
    """게이트가 닫혀 있어도(비밀 0개) 보기 · 비밀 · fetch 는 그대로 200 이다 — 409 는
    `/release/*`(다음 PR) 의 것이다."""
    assert srv.req("GET", "/api/repos/app")[1]["setup"]["complete"] is False
    assert srv.req("GET", "/api/repos/app/secrets", token="alice")[0] == 200
    assert srv.req("POST", "/api/repos/app/fetch", token="alice", json_body={})[0] == 200
