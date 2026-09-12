"""`tools/guard_production.py` 의 판정 — 순수 함수만 본다(발견은 I/O 라 뺀다).

막아야 할 것: 운영 체크아웃·설정·데이터를 고치는 것, 운영 설정으로 서버를 띄우는 것,
운영 체크아웃에서 브랜치를 바꾸는 것. 물어봐야 할 것: 배포(서비스 venv 설치 · 서비스 재시작).
막으면 안 되는 것: 읽기, 개발 워크트리에서 하는 모든 것, 운영 설치가 없는 컴퓨터.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_guard():
    """훅은 패키지가 아니라 파일 하나다 — 경로로 읽어 들인다."""
    path = ROOT / "tools" / "guard_production.py"
    spec = importlib.util.spec_from_file_location("guard_production", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    # dataclass 가 문자열 애너테이션을 풀려면 모듈이 sys.modules 에 있어야 한다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()

CHECKOUT = Path("/srv/rcm/checkout")
VENV = Path("/srv/rcm/venv")
CONFIG = Path("/srv/rcm/config")
DATA = Path("/srv/rcm/data")
#: 이름이 앞부분만 겹치는 형제 폴더. 운영 밖이다.
WORKTREE = Path("/srv/rcm/checkout-dev")

PROD = guard.Production(checkout=CHECKOUT, venv=VENV, config_dir=CONFIG, data_dir=DATA)
NO_PROD = guard.Production()


def bash(
    command: str,
    cwd: Path = WORKTREE,
    prod=PROD,
    local_config: bool = False,
    environ: dict[str, str] | None = None,
):
    """`environ` 이 None 이면 판정은 프로세스 환경을 본다(훅과 같다)."""
    return guard.decide(
        "Bash", {"command": command}, prod, cwd, local_config=local_config, environ=environ
    )


def edit(file_path: str, cwd: Path = WORKTREE, prod=PROD):
    return guard.decide("Edit", {"file_path": file_path}, prod, cwd)


# ── 파일을 고치는 것 ─────────────────────────────────────────────────────────


def test_editing_the_production_checkout_is_denied():
    verdict = edit(f"{CHECKOUT}/src/remote_ci_monitor/server.py")
    assert verdict is not None and verdict.decision == "deny"
    assert "worktree" in verdict.reason and "pull request" in verdict.reason


@pytest.mark.parametrize(
    "path", [f"{CONFIG}/server.toml", f"{DATA}/rcm.sqlite3", f"{VENV}/bin/rcm"]
)
def test_editing_the_service_config_data_or_venv_is_denied(path):
    verdict = edit(path)
    assert verdict is not None and verdict.decision == "deny", path


def test_a_sibling_worktree_whose_name_starts_the_same_is_not_production():
    assert edit(f"{WORKTREE}/src/remote_ci_monitor/server.py") is None


def test_editing_anywhere_else_is_free():
    assert edit("/tmp/scratch/notes.md") is None


def test_a_relative_path_is_read_against_the_session_directory():
    assert edit("src/remote_ci_monitor/server.py", cwd=CHECKOUT) is not None
    assert edit("src/remote_ci_monitor/server.py", cwd=WORKTREE) is None


# ── 지우는 것 ────────────────────────────────────────────────────────────────


def test_removing_the_data_directory_is_denied():
    verdict = bash(f"rm -rf {DATA}")
    assert verdict is not None and verdict.decision == "deny"


def test_sudo_and_env_prefixes_do_not_hide_the_command():
    assert bash(f"sudo rm -rf {CONFIG}") is not None
    assert bash(f"RCM_TOKEN=x rm -rf {DATA}/jobs") is not None


def test_a_removal_somewhere_else_is_free():
    assert bash("rm -rf /tmp/scratch") is None


def test_each_part_of_a_compound_command_is_judged():
    assert bash(f"cd /tmp && rm -rf {DATA}") is not None


# ── 배포는 막지 않고 물어본다 ────────────────────────────────────────────────


def test_installing_into_the_service_venv_asks():
    verdict = bash(f"{VENV}/bin/pip install -e {CHECKOUT}")
    assert verdict is not None and verdict.decision == "ask"


def test_installing_into_the_worktree_venv_is_free():
    assert bash(".venv/bin/pip install -e '.[dev]'") is None


def test_restarting_the_service_asks():
    verdict = bash("launchctl kickstart -k gui/501/com.remote-ci-monitor.server")
    assert verdict is not None and verdict.decision == "ask"
    assert "lost" in verdict.reason
    assert bash("systemctl restart rcm-server").decision == "ask"


def test_looking_at_the_service_is_free():
    assert bash("launchctl list | grep remote-ci") is None
    assert bash("systemctl status rcm-server") is None


# ── 시험용 서버는 자기 설정·포트·데이터를 쓴다 ───────────────────────────────


def test_a_server_with_no_config_would_take_the_production_one():
    verdict = bash("rcm serve")
    assert verdict is not None and verdict.decision == "deny"
    assert "data_dir" in verdict.reason


@pytest.mark.parametrize(
    "command", ["rcm serve", "rcm worker", "python -m remote_ci_monitor.cli serve"]
)
def test_every_way_of_starting_a_server_is_seen(command):
    assert bash(command) is not None, command


def test_a_server_with_its_own_config_is_free():
    assert bash("rcm serve --config /tmp/test.toml --port 8788") is None


def test_a_worktree_that_has_its_own_rcm_toml_is_free():
    """설정 탐색은 `./rcm.toml` 에서 멈춘다 — 운영 설정까지 내려가지 않는다."""
    assert bash("rcm serve", local_config=True) is None
    assert bash("rcm serve") is not None  # 대조: 자기 설정이 없으면 여전히 막힌다


def test_a_local_config_does_not_excuse_naming_the_production_one():
    assert bash(f"rcm serve --config {CONFIG}/server.toml", local_config=True) is not None
    assert bash(f"rcm serve --data {DATA}", local_config=True) is not None


def test_a_server_pointed_at_the_production_config_or_data_is_denied():
    assert bash(f"rcm serve --config {CONFIG}/server.toml") is not None
    assert bash(f"rcm serve --config /tmp/test.toml --data {DATA}") is not None


def test_help_is_not_a_server():
    assert bash("rcm serve --help") is None


def test_reading_the_queue_is_never_blocked():
    for command in ("rcm top", "rcm jobs --json", "rcm wait --job 12", "rcm check", "rcm logs 3"):
        assert bash(command) is None, command


# ── 운영 체크아웃의 브랜치 ───────────────────────────────────────────────────


def test_changing_the_branch_of_the_production_checkout_is_denied():
    verdict = bash("git switch feat/x", cwd=CHECKOUT)
    assert verdict is not None and verdict.decision == "deny"
    assert "main" in verdict.reason


def test_going_back_to_main_and_fast_forwarding_stay_allowed():
    assert bash("git switch main", cwd=CHECKOUT) is None
    assert bash("git pull --ff-only", cwd=CHECKOUT) is None
    assert bash("git fetch --prune origin", cwd=CHECKOUT) is None
    assert bash("git worktree add ../rcm-dev dev", cwd=CHECKOUT) is None


def test_dash_c_reaches_into_the_production_checkout():
    verdict = bash(f"git -C {CHECKOUT} reset --hard origin/dev")
    assert verdict is not None and verdict.decision == "deny"


def test_the_same_git_command_in_a_worktree_is_free():
    assert bash("git switch -c feat/x", cwd=WORKTREE) is None
    assert bash("git commit -m 'x'", cwd=WORKTREE) is None


# ── 운영 설치가 없는 컴퓨터 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [f"rm -rf {DATA}", "rcm serve", f"{VENV}/bin/pip install -e {CHECKOUT}", "git switch feat/x"],
)
def test_without_a_production_install_the_hook_is_silent(command):
    assert bash(command, cwd=CHECKOUT, prod=NO_PROD) is None, command


def test_without_a_production_install_edits_are_free():
    assert edit(f"{CHECKOUT}/src/x.py", prod=NO_PROD) is None


# ── 다른 도구는 지나간다 ─────────────────────────────────────────────────────


def test_other_tools_are_not_judged():
    assert guard.decide("Read", {"file_path": f"{CHECKOUT}/PLAN.md"}, PROD, WORKTREE) is None
    assert guard.decide("Bash", {}, PROD, WORKTREE) is None


# ── 운영 데이터에 쓰는 명령 (M5i 결정 75) ────────────────────────────────────
#
# 2026-09-10: dev 워크트리의 `rcm gc --dry-run --config <운영 설정>` 이 운영 DB 를 마이그레이션했다.
# 훅은 `serve`·`worker` 만 봐서 못 막았다. 이제 `token`(list 도 Store 를 열어 마이그레이션한다 —
# 전부 쓰기)은 유효 `data_dir` 이 운영 데이터이고 실행 파일이 서비스 venv 밖이면 deny 다.
# `gc --dry-run --config` 는 PR 2 뒤 사본 위에서 돌므로 **명시 허용**이다.


def which_worktree(name: str) -> str | None:
    return f"{WORKTREE}/.venv/bin/{name}"


def which_production(name: str) -> str | None:
    return f"{VENV}/bin/{name}"


@pytest.fixture(autouse=True)
def _bare_rcm_is_a_worktree_build(monkeypatch):
    """맨 `rcm` 은 PATH 로 푼다 — 기본 픽스처에서는 워크트리 venv 의 것이다."""
    monkeypatch.setattr(guard, "_which", which_worktree, raising=False)


@pytest.mark.parametrize(
    "command",
    [
        f"rcm token --config {CONFIG}/server.toml list",
        f"rcm token --config {CONFIG}/server.toml add laptop",
        f"rcm token --config={CONFIG}/server.toml revoke laptop",
        f"rcm token --data-dir {DATA} add laptop",
        f"python -m remote_ci_monitor.cli token --config {CONFIG}/server.toml list",
        f"python -m remote_ci_monitor token --config {CONFIG}/server.toml list",
        f"RCM_CONFIG={CONFIG}/server.toml rcm token list",
        f"env RCM_CONFIG={CONFIG}/server.toml rcm token list",
        f"{WORKTREE}/.venv/bin/rcm token --config {CONFIG}/server.toml list",
    ],
)
def test_a_token_command_on_the_production_data_from_another_build_is_denied(command):
    verdict = bash(command)
    assert verdict is not None and verdict.decision == "deny", command
    assert "migrat" in verdict.reason and str(DATA) in verdict.reason, verdict.reason
    # 다음 행동을 읽을 수 있어야 한다 — 서비스 자신의 rcm 이 이름으로 나온다.
    assert f"{VENV}/bin/rcm" in verdict.reason, verdict.reason


def test_a_copy_of_the_production_config_still_points_at_the_production_data(tmp_path):
    """설정을 복사해도 `data_dir` 이 운영이면 같은 DB 다 — 훅이 TOML 을 읽어 유효 data_dir 을
    본다."""
    copy = tmp_path / "server-copy.toml"
    copy.write_text(f'[server]\nport = 8791\ndata_dir = "{DATA}"\n')
    verdict = bash(f"rcm token --config {copy} list")
    assert verdict is not None and verdict.decision == "deny"
    other = tmp_path / "test.toml"
    other.write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/data"\n')
    assert bash(f"rcm token --config {other} list") is None


@pytest.mark.parametrize(
    "prefix", ["RCM_SERVER_DATA_DIR={DATA} ", "env RCM_SERVER_DATA_DIR={DATA} "]
)
def test_the_data_dir_env_override_points_a_test_config_at_the_production_data(tmp_path, prefix):
    """`RCM_SERVER_DATA_DIR` 은 CLI 의 env 덮어쓰기(`RCM_<SECTION>_<KEY>`, config.py
    `_env_overrides`)라 설정 파일보다 우선한다 — 시험 설정을 줘도 그 변수가 운영을 가리키면 운영
    DB 가 열린다."""
    test = tmp_path / "test.toml"
    test.write_text(f'[server]\nport = 8795\ndata_dir = "{tmp_path}/data"\n')
    verdict = bash(prefix.format(DATA=DATA) + f"rcm token --config {test} add laptop")
    assert verdict is not None and verdict.decision == "deny", verdict


def test_the_data_dir_env_override_in_the_session_environment_is_seen(tmp_path, monkeypatch):
    """조각 앞이 아니라 세션 환경에 있어도 CLI 는 읽는다 — `RCM_CONFIG` 와 같은 규칙."""
    test = tmp_path / "test.toml"
    test.write_text(f'[server]\nport = 8795\ndata_dir = "{tmp_path}/data"\n')
    monkeypatch.setenv("RCM_SERVER_DATA_DIR", str(DATA))
    verdict = bash(f"rcm token --config {test} add laptop")
    assert verdict is not None and verdict.decision == "deny", verdict


def test_the_data_dir_env_override_away_from_production_is_free(monkeypatch):
    """반대로 변수가 시험 디렉터리를 가리키면 운영 설정을 줘도 운영 DB 는 안 열린다 — 그리고
    `--data-dir` 은 변수보다 앞선다(CLI 와 같은 순서)."""
    production_config = f"--config {CONFIG}/server.toml"
    assert bash(f"RCM_SERVER_DATA_DIR=/tmp/rcm-test rcm token {production_config} list") is None
    verdict = bash(f"RCM_SERVER_DATA_DIR=/tmp/rcm-test rcm token --data-dir {DATA} add laptop")
    assert verdict is not None and verdict.decision == "deny", verdict
    monkeypatch.setenv("RCM_SERVER_DATA_DIR", "/tmp/rcm-test")
    assert bash(f"rcm token {production_config} list") is None


def test_the_service_venvs_own_rcm_may_manage_its_tokens(monkeypatch):
    """운영 빌드가 운영 DB 를 여는 것은 정상 운영이다 — 마이그레이션이 없다."""
    assert bash(f"{VENV}/bin/rcm token --config {CONFIG}/server.toml add laptop") is None
    monkeypatch.setattr(guard, "_which", which_production)
    assert bash(f"rcm token --config {CONFIG}/server.toml add laptop") is None


def test_a_token_command_with_no_config_finds_the_production_one():
    assert bash("rcm token list") is not None
    assert bash("rcm token list", local_config=True) is None  # `./rcm.toml` 에서 멈춘다


def test_a_token_command_on_a_test_data_dir_is_free():
    assert bash("rcm token --data-dir /tmp/rcm-test add laptop") is None
    assert bash(f"rcm token --config {CONFIG}/server.toml --data-dir /tmp/rcm-test list") is None
    assert bash("rcm token --help") is None


def test_an_unreadable_config_is_not_a_reason_to_deny(tmp_path):
    """TOML 을 못 읽으면 CLI 도 먼저 실패한다 — 훅이 대신 막지 않는다."""
    broken = tmp_path / "broken.toml"
    broken.write_text("[server\nthis is not toml")
    assert bash(f"rcm token --config {broken} list") is None
    assert bash(f"rcm token --config {tmp_path}/missing.toml list") is None


def test_the_offline_dry_run_against_the_production_config_is_allowed():
    """PR 2 뒤 dry-run 은 임시 사본 위에서 돈다 — 결정 75 의 명시 허용."""
    assert bash(f"rcm gc --dry-run --config {CONFIG}/server.toml") is None
    assert bash(f"{WORKTREE}/.venv/bin/rcm gc --dry-run --config {CONFIG}/server.toml") is None


def test_without_a_production_install_token_commands_are_free():
    assert bash(f"rcm token --config {CONFIG}/server.toml list", prod=NO_PROD) is None


# ── 서비스 venv 의 정체 (M5l L4 · 리뷰 #89 B-1) ─────────────────────────────
#
# 2026-09-10 의 사고 모양: 워크트리 `.venv` 를 활성화한 셸에서는 PATH 의 `rcm` 이 워크트리 것이다.
# 「PATH 의 rcm 이 있는 venv」를 운영 venv 로 정의하면 발견이 워크트리를 운영으로 잡고, 면제가
# 그 빌드를 「서비스 자신」으로 봐서 운영 DB 를 여는 명령이 허용된다. 운영 venv 는 **editable
# 설치(`direct_url.json`)가 운영 체크아웃 — 링크된 워크트리가 아닌 본 체크아웃 — 을 가리키고
# 그 체크아웃 밖에 있는 venv** 다. 발견은 I/O 라 가짜 홈에서 본다.


def _fake_venv(root: Path, source: Path) -> Path:
    """`pyvenv.cfg` · `bin/rcm` · `source` 를 가리키는 editable `direct_url.json` 이 있는 venv."""
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n")
    launcher = root / "bin" / "rcm"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    info = root / "lib" / "python3.11" / "site-packages" / "remote_ci_monitor-0.2.6.dist-info"
    info.mkdir(parents=True)
    (info / "direct_url.json").write_text(
        '{"dir_info": {"editable": true}, "url": "file://' + str(source) + '"}'
    )
    return root


def _fake_home(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """운영 설치를 흉내 낸 홈: 본 체크아웃(`.git/` 디렉터리)과 링크된 워크트리(`.git` 파일),
    각각을 가리키는 venv. 서비스 venv 는 문서의 자리(`~/.local/share/rcm-venv`)에 둔다."""
    home = tmp_path / "home"
    checkout = home / "src" / "remote_ci_monitor"
    (checkout / ".git").mkdir(parents=True)
    worktree = home / "src" / "remote_ci_monitor-topic"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {checkout}/.git/worktrees/topic\n")
    service = _fake_venv(home / ".local" / "share" / "rcm-venv", checkout)
    local = _fake_venv(worktree / ".venv", worktree)
    (home / ".local" / "bin").mkdir()
    (home / ".local" / "bin" / "rcm").symlink_to(service / "bin" / "rcm")
    config_dir = home / ".config" / "rcm"
    config_dir.mkdir(parents=True)
    data = home / ".local" / "share" / "rcm"
    data.mkdir()
    (config_dir / "server.toml").write_text(f'[server]\nport = 8787\ndata_dir = "{data}"\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return {
        "home": home,
        "checkout": checkout,
        "worktree": worktree,
        "service": service,
        "local": local,
        "config_dir": config_dir,
        "data": data,
    }


def test_the_service_venv_is_the_editable_install_of_the_primary_checkout(tmp_path, monkeypatch):
    """활성화된 워크트리 venv 셸: PATH 의 `rcm` 은 워크트리 것이고 `~/.local/bin` 은 PATH 에 없다.
    그래도 발견은 문서의 자리에 있는 서비스 venv 와 본 체크아웃을 잡는다."""
    fake = _fake_home(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", f"{fake['local']}/bin:/usr/bin:/bin")
    prod = guard.find_production()
    assert prod.venv == fake["service"], prod
    assert prod.checkout == fake["checkout"], prod
    assert prod.config_dir == fake["config_dir"] and prod.data_dir == fake["data"], prod


def test_a_worktree_venv_on_path_is_never_taken_for_the_service(tmp_path, monkeypatch):
    """문서의 자리에 venv 가 없고 PATH 의 `rcm` 이 워크트리 것뿐이면 — 운영 venv 는 없다.
    (그 `direct_url.json` 은 링크된 워크트리를 가리키고 venv 는 그 안에 있다.)"""
    fake = _fake_home(tmp_path, monkeypatch)
    shutil.rmtree(fake["service"])
    monkeypatch.setenv("PATH", f"{fake['local']}/bin:/usr/bin:/bin")
    prod = guard.find_production()
    assert prod.venv is None and prod.checkout is None, prod


def test_a_venv_outside_the_worktree_that_installs_a_worktree_is_not_the_service(
    tmp_path, monkeypatch
):
    """venv 가 워크트리 밖에 있어도 원본이 링크된 워크트리(`.git` 파일)면 운영이 아니다."""
    fake = _fake_home(tmp_path, monkeypatch)
    shutil.rmtree(fake["service"])
    elsewhere = _fake_venv(fake["home"] / "venvs" / "dev", fake["worktree"])
    monkeypatch.setenv("PATH", f"{elsewhere}/bin:/usr/bin:/bin")
    assert guard.find_production().venv is None


@pytest.mark.parametrize(
    "unit, text",
    [
        (
            "Library/LaunchAgents/com.remote-ci-monitor.server.plist",
            "<array><string>{rcm}</string><string>serve</string></array>",
        ),
        ("~/.config/systemd/user/rcm-server.service", "ExecStart={rcm} serve --config x\n"),
    ],
)
def test_the_installed_service_unit_names_the_venv(tmp_path, monkeypatch, unit, text):
    """문서의 자리에 없으면 설치된 서비스 유닛이 적은 `rcm` 의 venv 가 후보다 — 서비스가 실제로
    실행하는 것이 서비스 venv 다."""
    fake = _fake_home(tmp_path, monkeypatch)
    shutil.rmtree(fake["service"])
    elsewhere = _fake_venv(fake["home"] / "opt" / "rcm", fake["checkout"])
    path = fake["home"] / unit.replace("~/", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.format(rcm=elsewhere / "bin" / "rcm"))
    monkeypatch.setenv("PATH", f"{fake['local']}/bin:/usr/bin:/bin")
    prod = guard.find_production()
    assert prod.venv == elsewhere and prod.checkout == fake["checkout"], prod


def test_a_path_rcm_that_installs_the_primary_checkout_is_the_service(tmp_path, monkeypatch):
    """자리도 유닛도 없는 기계: PATH 의 `rcm` 중 본 체크아웃을 editable 로 가리키는 것이
    운영이다."""
    fake = _fake_home(tmp_path, monkeypatch)
    shutil.rmtree(fake["service"])
    elsewhere = _fake_venv(fake["home"] / "opt" / "rcm", fake["checkout"])
    monkeypatch.setenv("PATH", f"{fake['local']}/bin:{elsewhere}/bin:/usr/bin:/bin")
    prod = guard.find_production()
    assert prod.venv == elsewhere and prod.checkout == fake["checkout"], prod


def test_an_activated_worktree_venv_does_not_excuse_opening_the_production_db(
    tmp_path, monkeypatch
):
    """발견 + 판정을 이어 붙인 2026-09-10 모양: PATH 의 `rcm` 은 워크트리 빌드 — deny 이고 사유의
    「own rcm」은 서비스 venv 의 것이다."""
    fake = _fake_home(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", f"{fake['local']}/bin:/usr/bin:/bin")
    monkeypatch.setattr(guard, "_which", lambda name: f"{fake['local']}/bin/{name}")
    prod = guard.find_production()
    verdict = bash(f"rcm token --config {fake['config_dir']}/server.toml add x", prod=prod)
    assert verdict is not None and verdict.decision == "deny", verdict
    assert f"{fake['service']}/bin/rcm" in verdict.reason, verdict.reason
    assert str(fake["local"]) not in verdict.reason, verdict.reason


# ── serve · worker 도 유효 data_dir 로 본다 (리뷰 #89 A-1 · B-2) ─────────────


def test_a_server_on_a_copy_of_the_production_config_is_denied(tmp_path):
    """설정을 복사해 `port` 만 바꿔도 `data_dir` 이 운영이면 두 서버가 한 SQLite 를 연다."""
    copy = tmp_path / "copy.toml"
    copy.write_text(f'[server]\nport = 8791\ndata_dir = "{DATA}"\n')
    for command in (f"rcm serve --config {copy}", f"rcm worker --config {copy}"):
        verdict = bash(command)
        assert verdict is not None and verdict.decision == "deny", command
        assert str(DATA) in verdict.reason, verdict.reason


def test_a_server_whose_env_override_points_at_the_production_data_is_denied(tmp_path):
    test = tmp_path / "test.toml"
    test.write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/data"\n')
    verdict = bash(f"RCM_SERVER_DATA_DIR={DATA} rcm serve --config {test}")
    assert verdict is not None and verdict.decision == "deny", verdict
    assert bash(f"rcm serve --config {test}") is None


def test_a_server_whose_env_override_points_away_from_production_is_free(tmp_path):
    """env 가 설정 파일을 이긴다 — CLI 와 같은 순서. 사본이 운영을 가리켜도 env 가 시험이면 열지
    않는다."""
    copy = tmp_path / "copy.toml"
    copy.write_text(f'[server]\nport = 8791\ndata_dir = "{DATA}"\n')
    assert bash(f"RCM_SERVER_DATA_DIR={tmp_path}/data rcm serve --config {copy}") is None


def test_a_worker_config_names_its_data_dir_at_the_top_level(tmp_path):
    """`worker.toml` 의 `data_dir` 은 최상위 키다."""
    worker = tmp_path / "worker.toml"
    worker.write_text(f'server = "http://x:1"\ndata_dir = "{DATA}"\n')
    assert bash(f"rcm worker --config {worker}") is not None
    worker.write_text(f'server = "http://x:1"\ndata_dir = "{tmp_path}/w"\n')
    assert bash(f"rcm worker --config {worker}") is None


# ── `env` 의 플래그 · 흔한 감싸기 (리뷰 #89 B-3 · B-4 · #93 B-3) ─────────────


@pytest.mark.parametrize(
    "command",
    [
        f"env -i PATH=/x rcm token --config {CONFIG}/server.toml list",
        f"env -u RCM_CONFIG rcm token --config {CONFIG}/server.toml list",
        f"env --unset=RCM_CONFIG rcm token --config {CONFIG}/server.toml list",
        f"env RCM_CONFIG={CONFIG}/server.toml -- rcm token list",
        f"env -i RCM_CONFIG={CONFIG}/server.toml rcm token list",
        f"(rcm token --config {CONFIG}/server.toml list)",
        f"{{ rcm token --config {CONFIG}/server.toml list; }}",
        f"(cd /tmp && rcm token --config {CONFIG}/server.toml list)",
        f"exec rcm token --config {CONFIG}/server.toml list",
        f"nohup rcm token --config {CONFIG}/server.toml list &",
        f"time rcm token --config {CONFIG}/server.toml list",
        f"command rcm token --config {CONFIG}/server.toml list",
    ],
)
def test_env_flags_and_wrappers_do_not_switch_the_verdict_off(command):
    verdict = bash(command)
    assert verdict is not None and verdict.decision == "deny", command


def test_env_u_drops_the_named_session_variable(tmp_path):
    """세션 환경이 시험을 가리켜도 `env -u` 로 벗기면 CLI 는 설정 파일(사본)로 돌아간다."""
    copy = tmp_path / "copy.toml"
    copy.write_text(f'[server]\nport = 8791\ndata_dir = "{DATA}"\n')
    session = {"RCM_SERVER_DATA_DIR": f"{tmp_path}/data"}
    assert bash(f"rcm token --config {copy} list", environ=session) is None
    verdict = bash(f"env -u RCM_SERVER_DATA_DIR rcm token --config {copy} list", environ=session)
    assert verdict is not None and verdict.decision == "deny", verdict
    # `-u RCM_CONFIG` 뒤의 이름은 명령이 아니다 — 그리고 벗기면 탐색이 운영 설정까지 내려간다.
    test = tmp_path / "test.toml"
    test.write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/data"\n')
    session = {"RCM_CONFIG": str(test)}
    assert bash("rcm token list", environ=session) is None
    assert bash("env -u RCM_CONFIG rcm token list", environ=session) is not None


def test_env_i_starts_from_an_empty_environment(tmp_path):
    test = tmp_path / "test.toml"
    test.write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/data"\n')
    session = {"RCM_SERVER_DATA_DIR": f"{tmp_path}/data", "RCM_CONFIG": str(test)}
    assert bash(f"rcm token --config {CONFIG}/server.toml list", environ=session) is None
    assert bash("env -i rcm token list", environ=session) is not None
    assert bash(f"env -i RCM_CONFIG={test} rcm token list", environ=session) is None


# ── `cd` 는 뒤 조각의 cwd 를 바꾼다 (리뷰 #89 B-6) ───────────────────────────


def test_cd_moves_the_rest_of_the_line(tmp_path):
    """CLI 는 그 폴더의 `./rcm.toml` 을 집는다 — 세션 cwd 가 아니라."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "rcm.toml").write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/wt-data"\n')
    assert bash(f"cd {worktree} && rcm token list") is None
    assert bash("cd wt && rcm serve", cwd=tmp_path) is None
    assert bash(f"cd {worktree}; rcm token list") is None
    # 반대: 세션 cwd 의 rcm.toml 은 `cd` 뒤에는 안 통한다 — 탐색이 운영 설정까지 내려간다.
    assert bash("cd /usr && rcm token list", cwd=worktree, local_config=True) is not None
    assert bash("cd /usr && rcm serve", cwd=worktree, local_config=True) is not None


def test_cd_into_the_production_checkout_reaches_git(tmp_path):
    verdict = bash(f"cd {CHECKOUT} && git switch feat/x")
    assert verdict is not None and verdict.decision == "deny"


# ── XDG 설정 자리 (리뷰 #89 B-7) ─────────────────────────────────────────────


def test_xdg_config_home_is_searched_before_the_production_config(tmp_path):
    """CLI 는 `./rcm.toml` 다음에 `$XDG_CONFIG_HOME/rcm/server.toml` 을 보고 그 다음에야
    `~/.config/rcm` 이다 — 거기 시험 설정이 있으면 운영 설정까지 내려가지 않는다."""
    xdg = tmp_path / "xdg"
    (xdg / "rcm").mkdir(parents=True)
    (xdg / "rcm" / "server.toml").write_text(f'[server]\ndata_dir = "{tmp_path}/xdg-data"\n')
    assert bash(f"XDG_CONFIG_HOME={xdg} rcm token list") is None
    assert bash("rcm token list", environ={"XDG_CONFIG_HOME": str(xdg)}) is None
    assert bash("rcm serve", environ={"XDG_CONFIG_HOME": str(xdg)}) is None
    # 그 자리에 파일이 없으면 여전히 내려간다.
    assert bash("rcm token list", environ={"XDG_CONFIG_HOME": f"{tmp_path}/empty"}) is not None


# ── 빈 `RCM_SERVER_DATA_DIR=` (리뷰 #93 B-1) ─────────────────────────────────


def test_an_empty_data_dir_override_means_the_current_directory(tmp_path):
    """CLI 의 `_env_overrides` 는 `in os.environ` 으로 보므로 빈 값도 적용된다 —
    `data_dir = ""` 은 현재 디렉터리다. 가드도 같은 뜻으로 읽는다."""
    copy = tmp_path / "copy.toml"
    copy.write_text(f'[server]\nport = 8791\ndata_dir = "{DATA}"\n')
    assert bash(f"RCM_SERVER_DATA_DIR= rcm token --config {copy} list") is None
    verdict = bash(f"RCM_SERVER_DATA_DIR= rcm token --config {copy} list", cwd=DATA / "sub")
    assert verdict is not None and verdict.decision == "deny", verdict
    assert bash("rcm token list", cwd=DATA, environ={"RCM_SERVER_DATA_DIR": ""}) is not None


# ── 리뷰 #89 §C 에서 살아남은 돌연변이 넷 ────────────────────────────────────


def test_a_local_rcm_toml_that_points_at_production_is_still_denied(tmp_path):
    """① `local_config` 라도 `./rcm.toml` 의 `data_dir` 이 운영이면 같은 DB 다."""
    (tmp_path / "rcm.toml").write_text(f'[server]\nport = 8791\ndata_dir = "{DATA}"\n')
    assert bash("rcm token list", cwd=tmp_path, local_config=True) is not None
    assert bash("rcm serve", cwd=tmp_path, local_config=True) is not None
    (tmp_path / "rcm.toml").write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/d"\n')
    assert bash("rcm token list", cwd=tmp_path, local_config=True) is None


def test_the_session_rcm_config_is_read_and_beats_the_local_file(tmp_path):
    """② 세션 환경의 `RCM_CONFIG` 는 조각 앞의 것과 같은 규칙 — 그리고 `./rcm.toml` 보다 앞이다."""
    test = tmp_path / "test.toml"
    test.write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/d"\n')
    assert bash("rcm token list", environ={"RCM_CONFIG": str(test)}) is None
    (tmp_path / "rcm.toml").write_text(f'[server]\nport = 8791\ndata_dir = "{tmp_path}/d"\n')
    session = {"RCM_CONFIG": f"{CONFIG}/server.toml"}
    verdict = bash("rcm token list", cwd=tmp_path, local_config=True, environ=session)
    assert verdict is not None and verdict.decision == "deny", verdict


DEFAULT_DATA = Path("~/.local/share/rcm").expanduser()
DEFAULT_PROD = guard.Production(
    checkout=CHECKOUT, venv=VENV, config_dir=CONFIG, data_dir=DEFAULT_DATA
)


def test_a_config_without_data_dir_means_the_default_data_dir(tmp_path):
    """③ `[server]` 에 `data_dir` 이 없으면 기본값이다 — 운영이 기본값을 쓰는 기계에선 그 사본도
    운영 DB 를 연다."""
    test = tmp_path / "test.toml"
    test.write_text("[server]\nport = 8791\n")
    verdict = bash(f"rcm token --config {test} list", prod=DEFAULT_PROD)
    assert verdict is not None and verdict.decision == "deny", verdict
    assert bash(f"rcm token --config {test} list") is None  # 운영 data_dir 이 다르면 아니다


def test_no_config_anywhere_still_means_the_default_data_dir():
    """④ 설정 파일이 어디에도 없는 세션의 `rcm token list` 는 기본 data_dir 을 연다."""
    prod = guard.Production(checkout=CHECKOUT, venv=VENV, config_dir=None, data_dir=DEFAULT_DATA)
    assert bash("rcm token list", prod=prod, environ={}) is not None
    other = guard.Production(checkout=CHECKOUT, venv=VENV, config_dir=None, data_dir=DATA)
    assert bash("rcm token list", prod=other, environ={}) is None
