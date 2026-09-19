"""`rcm release` — 스토어 버전 드래프트를 CLI 에서 만들고 · 보고 · 버리고 · 주소를 얻는다.

명세: docs/version-page-workplan.md §2.3 · §7 AC-B12. 네 하위 명령은 서버 API 를 부르는 얇은
껍데기다 — 이름을 검증하는 것도, 힌트를 내는 것도, 스토어를 부르는 것도 서버다.

여기서 지키는 것:
- `new` 는 인자가 없으면 **버전 이름만** 묻는다. 엔터 = 서버가 준 힌트, `-` = 그 스토어는 안
  만든다, `--yes` = 묻지 않고 힌트 둘 다. 프롬프트는 `input` 을 몽키패치해 잠근다.
- 종료 코드는 집안 규칙 그대로: 0 · 사용/설정/서버 거절 2 · 서버에 못 닿음 3 · Ctrl-C 130.
- `open` 은 주소만 찍는다(브라우저를 열지 않는다).
- `delete` 는 제출된 버전을 **서버의 문구 그대로** 거절한다.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from remote_ci_monitor.config import RepoConfig, parse_release_profile
from test_cli_m4 import run
from test_server_release_versions import (
    LIVE_PLAN,
    PROFILE_NO_VERSION,
    PROFILE_V,
    VersionServer,
    editing_draft,
)
from test_server_release_versions import remote as _remote

remote = _remote  # 픽스처를 이 모듈의 이름공간에 — pytest 는 이름으로 찾는다


@pytest.fixture
def cli_home(monkeypatch, tmp_path) -> Path:
    """HOME · cwd 를 tmp 로, `RCM_*` 은 없이 — 진짜 client.toml 을 절대 읽지 않는다.
    `remote` 픽스처의 `isolate_git_env` 가 같은 폴더를 먼저 만들 수 있다(순서는 상관없다)."""
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    return h


def use(monkeypatch, srv: VersionServer, token: str = "admin") -> None:
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{srv.port}")
    monkeypatch.setenv("RCM_TOKEN", srv.tokens[token])


@pytest.fixture
def rsrv(tmp_path: Path, remote, monkeypatch, cli_home):
    """관문이 열리고 라이브 버전을 아는 `app` 하나 + admin 토큰이 걸린 환경."""
    s = VersionServer(tmp_path / "srv", remote)
    try:
        s.open_gate(monkeypatch)
        s.plan_job(LIVE_PLAN)
        use(monkeypatch, s)
        yield s
    finally:
        s.close()


class Prompt:
    """`input` 대역 — 미리 정한 답을 차례로 주고, 떨어지면 EOF. 물어본 **문구**는 stderr 에
    있다(프롬프트는 stdout 을 더럽히지 않는다)."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, text: str = "") -> str:
        self.calls += 1
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def ask(monkeypatch, *answers: str) -> Prompt:
    p = Prompt(*answers)
    monkeypatch.setattr(builtins, "input", p)
    return p


# ── new (AC-B12) ─────────────────────────────────────────────────────────────


def test_new_with_yes_takes_both_hints_and_prints_the_id_state_and_address(rsrv, capsys):
    """AC-B12 — `--yes` 는 묻지 않고 힌트 그대로 보낸다."""
    code, out, err = run(capsys, ["release", "new", "--repo", "app", "--yes"])
    assert code == 0, out + err
    row = rsrv.store.get_version(1)
    assert (row["ios_version"], row["android_version"]) == ("1.1.1", "1.0.1")
    assert row["state"] == "creating"
    lines = out.strip().splitlines()
    assert lines[0] == f"version #1 1.1.1 creating · create job #{row['create_job_id']}"
    assert lines[1] == f"http://127.0.0.1:{rsrv.port}/#/store/app/v/1"


def test_new_asks_only_for_the_version_names_and_enter_takes_the_hint(rsrv, monkeypatch, capsys):
    """인자가 없으면 «새 버전을 만듭니다» 뒤에 두 줄만 묻는다 — 엔터는 힌트다."""
    prompt = ask(monkeypatch, "", "")
    code, out, err = run(capsys, ["release", "new", "--repo", "app"])
    assert code == 0, out + err
    assert prompt.calls == 2
    assert err == (
        "Creating a new version. Enter keeps the hint, '-' skips that store.\n"
        "iOS [1.1.1]: Android [1.0.1]: "
    )
    assert out.splitlines()[0].startswith("version #1 1.1.1 creating")  # stdout 은 결과만
    row = rsrv.store.get_version(1)
    assert (row["ios_version"], row["android_version"]) == ("1.1.1", "1.0.1")


def test_a_dash_skips_that_store_and_a_typed_name_wins_over_the_hint(rsrv, monkeypatch, capsys):
    ask(monkeypatch, "1.2.0", "-")
    code, out, err = run(capsys, ["release", "new", "--repo", "app"])
    assert code == 0, out + err
    row = rsrv.store.get_version(1)
    assert (row["ios_version"], row["android_version"]) == ("1.2.0", None)
    assert out.startswith("version #1 1.2.0 creating")


def test_the_prompt_has_no_default_when_the_server_has_no_hint(
    tmp_path, remote, monkeypatch, cli_home, capsys
):
    """E1 — 플랜이 없으면 힌트가 없다. 그래도 이름은 물어보고, 그냥 엔터면 만들 것이 없다."""
    s = VersionServer(tmp_path / "srv", remote)
    try:
        s.open_gate(monkeypatch)
        use(monkeypatch, s)
        prompt = ask(monkeypatch, "", "")
        code, out, err = run(capsys, ["release", "new", "--repo", "app"])
        assert prompt.calls == 2 and "iOS: Android: " in err  # 힌트가 없으면 기본값도 없다
        assert code == 2 and "no version name to create" in err
        assert s.store.count_versions() == 0
    finally:
        s.close()


def test_explicit_names_skip_the_prompt_entirely(rsrv, monkeypatch, capsys):
    ask(monkeypatch)  # 물으면 EOFError 로 터진다
    code, out, err = run(capsys, ["release", "new", "--repo", "app", "--ios", "9.9.9"])
    assert code == 0, out + err
    row = rsrv.store.get_version(1)
    assert (row["ios_version"], row["android_version"]) == ("9.9.9", None)


def test_a_dash_on_the_command_line_skips_that_store_too(rsrv, capsys):
    code, out, err = run(
        capsys, ["release", "new", "--repo", "app", "--ios", "-", "--android", "2.0.0"]
    )
    assert code == 0, out + err
    row = rsrv.store.get_version(1)
    assert (row["ios_version"], row["android_version"]) == (None, "2.0.0")


def test_nothing_to_read_the_answers_from_is_a_usage_error(rsrv, monkeypatch, capsys):
    ask(monkeypatch)
    code, out, err = run(capsys, ["release", "new", "--repo", "app"])
    assert code == 2 and "pass --ios/--android or --yes" in err


def test_the_server_refusal_is_printed_as_it_is(rsrv, capsys):
    """이름 검증은 서버가 한다 — CLI 는 그 문구를 그대로 낸다(400 `not_greater`)."""
    code, out, err = run(capsys, ["release", "new", "--repo", "app", "--ios", "1.0.9"])
    assert code == 2
    assert "release new failed" in err and "greater than the live version 1.1.0" in err


def test_a_client_token_cannot_create_a_version(rsrv, monkeypatch, capsys):
    use(monkeypatch, rsrv, "alice")
    code, out, err = run(capsys, ["release", "new", "--repo", "app", "--yes"])
    assert code == 2 and "admin token" in err


def test_new_json_prints_the_server_answer(rsrv, capsys):
    code, out, err = run(capsys, ["release", "new", "--repo", "app", "--yes", "--json"])
    assert code == 0, out + err
    doc = json.loads(out)
    assert doc["id"] == 1 and doc["state"] == "creating" and doc["build_name"] == "1.1.1"


def test_a_version_preset_less_server_answers_201_and_editing(
    tmp_path, remote, monkeypatch, cli_home, capsys
):
    """E25 — `version` 프리셋이 없으면 만들자마자 `editing` 이고 잡이 없다."""
    s = VersionServer(tmp_path / "srv", remote, profile=PROFILE_NO_VERSION)
    try:
        s.open_gate(monkeypatch)
        use(monkeypatch, s)
        code, out, err = run(capsys, ["release", "new", "--repo", "app", "--ios", "1.1.1"])
        assert code == 0, out + err
        assert out.splitlines()[0] == "version #1 1.1.1 editing"
    finally:
        s.close()


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_shows_the_live_names_the_hint_and_every_open_draft(rsrv, capsys):
    vid = editing_draft(rsrv)
    assert rsrv.put_listing(vid, {"ios": {"subtitle": "new"}})[0] == 200
    code, out, err = run(capsys, ["release", "list", "--repo", "app"])
    assert code == 0, out + err
    lines = out.splitlines()
    assert lines[0] == "live   iOS 1.1.0 · Android 1.0.0"
    assert lines[1] == "next   iOS 1.1.1 · Android 1.0.1  (hint)"
    assert lines[2].startswith("id     state")
    assert lines[3].startswith(f"#{vid}")
    assert "editing" in lines[3] and "iOS 1.1.1 · Android 1.0.1" in lines[3]
    assert "expires" in lines[3]


def test_list_says_so_when_there_is_nothing_yet(rsrv, capsys):
    code, out, err = run(capsys, ["release", "list", "--repo", "app"])
    assert code == 0, out + err
    assert out.splitlines()[-1] == "no versions yet"


def test_list_shows_submitted_versions_under_the_drafts(rsrv, capsys):
    vid = editing_draft(rsrv)
    assert rsrv.store.update_version(vid, state="submitted")
    code, out, err = run(capsys, ["release", "list", "--repo", "app"])
    assert code == 0, out + err
    assert [ln for ln in out.splitlines() if "submitted" in ln]


def test_list_json_is_the_route_document(rsrv, capsys):
    editing_draft(rsrv)
    code, out, err = run(capsys, ["release", "list", "--repo", "app", "--json"])
    assert code == 0, out + err
    doc = json.loads(out)
    assert set(doc) == {"live", "hints", "ttl_hours", "drafts", "history"}
    assert doc["ttl_hours"] == 24 and len(doc["drafts"]) == 1


# ── delete · open ────────────────────────────────────────────────────────────


def test_delete_takes_the_discard_route_and_reports_the_store_job(rsrv, capsys):
    vid = editing_draft(rsrv)
    code, out, err = run(capsys, ["release", "delete", str(vid), "--repo", "app"])
    assert code == 0, out + err
    job = rsrv.store.get_version(vid)["delete_job_id"]
    assert out.strip() == f"version #{vid} editing · store delete job #{job}"


def test_delete_of_a_version_with_no_store_draft_is_immediate(
    tmp_path, remote, monkeypatch, cli_home, capsys
):
    s = VersionServer(tmp_path / "srv", remote, profile=PROFILE_NO_VERSION)
    try:
        s.open_gate(monkeypatch)
        use(monkeypatch, s)
        status, body = s.create({"ios_version": "1.1.1"})
        assert status == 201, body
        code, out, err = run(capsys, ["release", "delete", str(body["id"]), "--repo", "app"])
        assert code == 0, out + err
        assert out.strip() == f"version #{body['id']} discarded"
    finally:
        s.close()


def test_delete_refuses_a_submitted_version_with_the_servers_own_message(rsrv, capsys):
    """E14 — 제출된 버전은 지울 수 없다. 문구는 서버가 쓴 것 그대로."""
    vid = editing_draft(rsrv)
    assert rsrv.store.update_version(vid, state="submitted")
    code, out, err = run(capsys, ["release", "delete", str(vid), "--repo", "app"])
    assert code == 2
    assert "was submitted for review — it cannot be discarded" in err
    assert rsrv.store.get_version(vid)["state"] == "submitted"


def test_delete_of_an_unknown_id_is_a_clean_refusal(rsrv, capsys):
    code, out, err = run(capsys, ["release", "delete", "99", "--repo", "app"])
    assert code == 2 and "no version #99" in err


def test_open_prints_the_address_and_nothing_else(rsrv, capsys):
    code, out, err = run(capsys, ["release", "open", "7", "--repo", "app"])
    assert code == 0, out + err
    assert out == f"http://127.0.0.1:{rsrv.port}/#/store/app/v/7\n"
    code, out, err = run(capsys, ["release", "open", "7", "--repo", "app", "--json"])
    assert code == 0, out + err
    assert json.loads(out) == {"id": 7, "url": f"http://127.0.0.1:{rsrv.port}/#/store/app/v/7"}


# ── --repo 를 고르는 규칙 ────────────────────────────────────────────────────


def test_the_only_repo_with_a_profile_is_the_default(rsrv, capsys):
    code, out, err = run(capsys, ["release", "list"])
    assert code == 0, out + err
    assert out.startswith("live   ")


def test_two_repos_with_a_profile_ask_for_repo_and_name_both(rsrv, capsys):
    rsrv.cfg.repos = (
        *rsrv.cfg.repos,
        RepoConfig(
            name="other", url=rsrv.remote.url, release=parse_release_profile("other", PROFILE_V)
        ),
    )
    code, out, err = run(capsys, ["release", "list"])
    assert code == 2
    assert "--repo is required" in err and "app, other" in err


def test_a_repo_name_that_could_bend_the_url_is_refused_before_the_request(rsrv, capsys):
    """`--repo` 는 주소에 그대로 들어간다 — 설정의 이름 규칙을 클라이언트에서도 본다."""
    code, out, err = run(capsys, ["release", "list", "--repo", "../app/release"])
    assert code == 2 and "is not a repository name" in err


def test_a_server_with_no_release_profile_says_so(rsrv, capsys):
    rsrv.cfg.repos = ()
    code, out, err = run(capsys, ["release", "list"])
    assert code == 2 and "no repository on this server has a release profile" in err


def test_an_unreachable_server_exits_three(monkeypatch, cli_home, capsys):
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:1")
    monkeypatch.setenv("RCM_TOKEN", "t")
    code, out, err = run(capsys, ["release", "list", "--repo", "app"])
    assert code == 3 and "cannot reach" in err
