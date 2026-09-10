"""`tools/guard_production.py` 의 판정 — 순수 함수만 본다(발견은 I/O 라 뺀다).

막아야 할 것: 운영 체크아웃·설정·데이터를 고치는 것, 운영 설정으로 서버를 띄우는 것,
운영 체크아웃에서 브랜치를 바꾸는 것. 물어봐야 할 것: 배포(서비스 venv 설치 · 서비스 재시작).
막으면 안 되는 것: 읽기, 개발 워크트리에서 하는 모든 것, 운영 설치가 없는 컴퓨터.
"""

import importlib.util
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


def bash(command: str, cwd: Path = WORKTREE, prod=PROD, local_config: bool = False):
    return guard.decide("Bash", {"command": command}, prod, cwd, local_config=local_config)


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
        f"RCM_CONFIG={CONFIG}/server.toml rcm token list",
        f"env RCM_CONFIG={CONFIG}/server.toml rcm token list",
        f"{WORKTREE}/.venv/bin/rcm token --config {CONFIG}/server.toml list",
    ],
)
def test_a_token_command_on_the_production_data_from_another_build_is_denied(command):
    verdict = bash(command)
    assert verdict is not None and verdict.decision == "deny", command
    assert "migrat" in verdict.reason and str(DATA) in verdict.reason, verdict.reason


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
