"""설정 로딩 — 우선순위(플래그 > env > 파일 > 기본값) · 프리셋 오류 메시지 · 시간대."""

import os
import shutil
from pathlib import Path

import pytest

from remote_ci_monitor.config import (
    ConfigError,
    ServerConfig,
    load_client_config,
    load_server_config,
    parse_preset,
)
from remote_ci_monitor.core.status import preset_json

GOOD = """
[server]
port = 9000
lanes = 2

[estimate]
default_seconds = 300

[display]
timezone = "Asia/Seoul"

[[presets]]
name = "gate"
description = "Full local gate"
argv = ["bash", "scripts/gate.sh"]
timeout_seconds = 1200
expected_seconds = 480
duration_key_inputs = ["scope"]
[presets.env]
CI = "1"
[[presets.inputs]]
name = "scope"
type = "choice"
choices = ["full", "commit", "fast"]
default = "full"
"""


def write(tmp_path: Path, text: str, name: str = "rcm.toml") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_file_values_override_defaults(tmp_path):
    cfg = load_server_config(write(tmp_path, GOOD), environ={})
    assert cfg.server.port == 9000
    assert cfg.server.lanes == 2
    assert cfg.server.bind == "127.0.0.1"
    assert cfg.estimate.default_seconds == 300
    assert cfg.display.timezone == "Asia/Seoul"
    assert cfg.preset("gate").env == {"CI": "1"}
    assert cfg.preset("gate").inputs[0].choices == ("full", "commit", "fast")


def test_env_overrides_file_and_flag_overrides_env(tmp_path):
    p = write(tmp_path, GOOD)
    cfg = load_server_config(p, environ={"RCM_SERVER_PORT": "9100", "RCM_SERVER_LANES": "3"})
    assert cfg.server.port == 9100 and cfg.server.lanes == 3
    cfg = load_server_config(
        p,
        environ={"RCM_SERVER_PORT": "9100"},
        overrides={"server": {"port": 9200, "bind": None}},
    )
    assert cfg.server.port == 9200
    assert cfg.server.bind == "127.0.0.1"  # None 플래그는 덮어쓰지 않는다


def test_env_type_error_names_the_key(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, GOOD), environ={"RCM_SERVER_PORT": "abc"})
    assert "[server] port" in str(e.value)


def test_unknown_key_fails_with_section_and_key(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, "[server]\nprot = 1\n"), environ={})
    assert "[server] unknown key 'prot'" in str(e.value)


def test_unknown_section_fails(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, "[sever]\nport = 1\n"), environ={})
    assert "unknown section(s): sever" in str(e.value)


def test_missing_config_gives_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("remote_ci_monitor.config.SERVER_CONFIG_CANDIDATES", ("./nope.toml",))
    cfg = load_server_config(None, environ={})
    assert cfg.path is None and cfg.server.port == 8787 and cfg.presets == ()


def test_explicit_missing_path_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_server_config(tmp_path / "missing.toml", environ={})


def test_bad_timezone_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, '[display]\ntimezone = "Mars/Olympus"\n'), environ={})
    assert "[display] timezone" in str(e.value)


def test_host_interval_floor(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, "[host]\ninterval_seconds = 1\n"), environ={})
    assert "[host] interval_seconds" in str(e.value)


def test_preset_errors_name_preset_and_key():
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "gate", "argv": []})
    assert "preset 'gate' argv" in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "gate", "argv": ["x"], "inputs": [{"name": "s", "type": "choice"}]})
    assert "preset 'gate' input 's'" in str(e.value) and "requires 'choices'" in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "gate", "argv": ["x"], "cmd": "rm -rf"})
    assert "preset 'gate': unknown key(s): cmd" in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "gate", "argv": ["x"], "duration_key_inputs": ["zzz"]})
    assert "unknown input 'zzz'" in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "gate", "argv": ["x"], "source_modes": ["ftp"]})
    assert "source_modes" in str(e.value)


def test_git_ref_preset_needs_a_repo(tmp_path):
    text = '[[presets]]\nname = "deploy"\nargv = ["x"]\nsource_modes = ["git_ref"]\n'
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, text), environ={})
    assert "no [[repos]]" in str(e.value)


def test_duplicate_preset_names(tmp_path):
    text = '[[presets]]\nname = "a"\nargv = ["x"]\n[[presets]]\nname = "a"\nargv = ["y"]\n'
    with pytest.raises(ConfigError) as e:
        load_server_config(write(tmp_path, text), environ={})
    assert "duplicate preset name" in str(e.value)


def test_client_config_priority(tmp_path, monkeypatch):
    p = write(
        tmp_path,
        'server = "http://mini:8787/"\ntoken_env = "MY_TOK"\nlabel = "me@here"\n',
        "client.toml",
    )
    cfg = load_client_config(p, environ={"MY_TOK": "secret"})
    assert cfg.server == "http://mini:8787" and cfg.token == "secret" and cfg.label == "me@here"
    cfg = load_client_config(
        p, environ={"MY_TOK": "secret", "RCM_TOKEN": "env-tok"}, server="http://x"
    )
    assert cfg.server == "http://x" and cfg.token == "env-tok"


def test_client_token_in_file_requires_600(tmp_path):
    p = write(tmp_path, 'server = "http://mini:8787"\ntoken = "abc"\n', "client.toml")
    os.chmod(p, 0o644)
    with pytest.raises(ConfigError) as e:
        load_client_config(p, environ={})
    assert "chmod 600" in str(e.value)
    os.chmod(p, 0o600)
    assert load_client_config(p, environ={}).token == "abc"


def test_client_config_remembers_token_env_name(tmp_path):
    p = write(tmp_path, 'server = "http://mini:8787"\ntoken_env = "MY_TOK"\n', "client.toml")
    cfg = load_client_config(p, environ={})
    assert cfg.token == "" and cfg.token_env == "MY_TOK"  # `rcm check` 안내가 이 이름을 말한다


# ── M3: [[repos]] · git_ref 프리셋의 repo · 새 [server] 키 (명세 §1.1 · §2.2) ──────────────
# 성공 로딩이 필요한 케이스는 git 이 PATH 에 있어야 한다(`[[repos]]` 가 있으면 시작 시 확인).

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

REPO_APP = """
[[repos]]
name = "app"
url = "git@example.com:org/app.git"
"""

REPO_LIB = """
[[repos]]
name = "lib"
url = "https://example.com/org/lib.git"
"""

DEPLOY = """
[[presets]]
name = "deploy"
argv = ["bash", "scripts/deploy.sh"]
source_modes = ["git_ref"]
"""

GATE_TREE = """
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
source_modes = ["tree"]
"""


def load(tmp_path: Path, text: str, environ: dict[str, str] | None = None) -> ServerConfig:
    return load_server_config(write(tmp_path, text), environ=environ or {})


@needs_git
def test_git_ref_preset_with_explicit_repo(tmp_path):
    cfg = load(tmp_path, REPO_APP + REPO_LIB + DEPLOY + 'repo = "app"\n')
    assert cfg.preset("deploy").repo == "app"
    assert [r.name for r in cfg.repos] == ["app", "lib"]
    assert cfg.repo("app").url == "git@example.com:org/app.git"  # 워커가 §1.4 에서 쓴다
    assert cfg.repo("nope") is None


@needs_git
def test_git_ref_preset_autofills_repo_when_exactly_one_repo(tmp_path):
    cfg = load(tmp_path, REPO_APP + DEPLOY)
    assert cfg.preset("deploy").repo == "app"


@needs_git
def test_git_ref_preset_without_repo_is_ambiguous_with_two_repos(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, REPO_APP + REPO_LIB + DEPLOY)
    assert "preset 'deploy'" in str(e.value) and "repo" in str(e.value)


@needs_git
def test_git_ref_preset_unknown_repo(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, REPO_APP + DEPLOY + 'repo = "nope"\n')
    assert "preset 'deploy'" in str(e.value) and "'nope'" in str(e.value)


@needs_git
def test_tree_only_preset_rejects_repo(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, REPO_APP + GATE_TREE + 'repo = "app"\n')
    assert "preset 'gate'" in str(e.value) and "only valid with" in str(e.value)


def test_tree_only_preset_rejects_repo_even_without_repos(tmp_path):
    # repos 가 없어도 같은 오류 — repo 키 자체가 git_ref 전용이다
    with pytest.raises(ConfigError) as e:
        load(tmp_path, GATE_TREE + 'repo = "app"\n')
    assert "only valid with" in str(e.value)


@needs_git
def test_mixed_modes_preset_accepts_repo(tmp_path):
    text = REPO_APP + GATE_TREE.replace('["tree"]', '["tree", "git_ref"]') + 'repo = "app"\n'
    assert load(tmp_path, text).preset("gate").repo == "app"


def test_tree_preset_has_no_repo(tmp_path):
    assert not load(tmp_path, GATE_TREE).preset("gate").repo


def test_parse_preset_repo_must_be_a_string():
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "deploy", "argv": ["x"], "source_modes": ["git_ref"], "repo": 5})
    assert "preset 'deploy'" in str(e.value) and "repo" in str(e.value)


# [[repos]] 자체의 검증 — url 비어 있음 · `-` 로 시작(옵션 주입) · 이름 중복 · 이름 규칙


@needs_git
def test_repo_url_must_not_be_empty(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, '[[repos]]\nname = "app"\nurl = ""\n')
    assert "url" in str(e.value)


@needs_git
def test_repo_url_must_not_start_with_dash(tmp_path):
    # git 호출은 `--` 뒤에 url 을 두지만, 설정 단계에서도 막는다
    with pytest.raises(ConfigError) as e:
        load(tmp_path, '[[repos]]\nname = "app"\nurl = "--upload-pack=evil"\n')
    assert "url" in str(e.value)


@needs_git
def test_duplicate_repo_names(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, REPO_APP + REPO_APP)
    assert "duplicate" in str(e.value).lower() and "app" in str(e.value)


@needs_git
def test_repo_name_must_be_an_identifier(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, '[[repos]]\nname = "bad name"\nurl = "https://example.com/x.git"\n')
    assert "name" in str(e.value)


def test_repos_require_git_on_path(tmp_path, monkeypatch):
    # 빈 디렉터리만 PATH 에 두면 git 을 못 찾는다. environ 에도 같은 PATH 를 줘야
    # (env 단계에서 os.environ 을 통째로 바꾸므로) os.defpath 의 /usr/bin 으로 새지 않는다
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ConfigError) as e:
        load(tmp_path, REPO_APP, environ={"PATH": str(tmp_path)})
    assert "git" in str(e.value)


def test_no_repos_needs_no_git(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    cfg = load(tmp_path, GATE_TREE, environ={"PATH": str(tmp_path)})
    assert cfg.preset("gate") is not None and cfg.repos == ()


# 새 [server] 키 — git 타임아웃 둘 · janitor 주기(하한 60) · 보존 일수는 음수 금지


def test_m3_server_keys_have_defaults(tmp_path):
    cfg = load(tmp_path, GOOD)
    assert cfg.server.git_resolve_timeout_seconds == 20
    assert cfg.server.git_fetch_timeout_seconds == 600
    assert cfg.server.retention_sweep_interval_seconds == 3600


def test_m3_server_keys_from_file(tmp_path):
    text = (
        "[server]\ngit_resolve_timeout_seconds = 5\ngit_fetch_timeout_seconds = 30\n"
        "retention_sweep_interval_seconds = 60\n"
    )
    cfg = load(tmp_path, text)
    assert cfg.server.git_resolve_timeout_seconds == 5
    assert cfg.server.git_fetch_timeout_seconds == 30
    assert cfg.server.retention_sweep_interval_seconds == 60


def test_sweep_interval_floor_is_60(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, "[server]\nretention_sweep_interval_seconds = 59\n")
    assert "retention_sweep_interval_seconds" in str(e.value)


@pytest.mark.parametrize("key", ["retention_days_success", "retention_days_failure"])
def test_retention_days_must_not_be_negative(tmp_path, key):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, f"[server]\n{key} = -1\n")
    assert key in str(e.value)
    # 0 은 「끝나자마자 다음 sweep 에」(명세 §2.1) — 허용
    assert getattr(load(tmp_path, f"[server]\n{key} = 0\n").server, key) == 0


@pytest.mark.parametrize("key", ["git_resolve_timeout_seconds", "git_fetch_timeout_seconds"])
def test_git_timeouts_must_be_positive(tmp_path, key):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, f"[server]\n{key} = 0\n")
    assert key in str(e.value)


def test_env_overrides_git_fetch_timeout(tmp_path):
    p = write(tmp_path, GOOD)
    cfg = load_server_config(p, environ={"RCM_SERVER_GIT_FETCH_TIMEOUT_SECONDS": "30"})
    assert cfg.server.git_fetch_timeout_seconds == 30
    with pytest.raises(ConfigError) as e:
        load_server_config(p, environ={"RCM_SERVER_GIT_FETCH_TIMEOUT_SECONDS": "soon"})
    assert "[server] git_fetch_timeout_seconds" in str(e.value)


# 프리셋 JSON(`/api/status.presets[]` · `rcm presets`)에 repo 가 실린다 — 없으면 null


@needs_git
def test_preset_json_carries_repo(tmp_path):
    cfg = load(tmp_path, REPO_APP + DEPLOY + GATE_TREE)
    assert preset_json(cfg.preset("deploy"))["repo"] == "app"
    assert preset_json(cfg.preset("gate"))["repo"] is None


# ── 탐색 순서: ./rcm.toml → $XDG_CONFIG_HOME/rcm → ~/.config/rcm (M4 XDG 도입의 회귀 방지) ──


@pytest.fixture
def search_home(tmp_path, monkeypatch):
    """HOME 을 tmp 로, cwd 는 빈 tmp/cwd 로, XDG_CONFIG_HOME · RCM_CONFIG 는 없이."""
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("RCM_CONFIG", raising=False)
    monkeypatch.chdir(cwd)
    return home, cwd


def _put(path: Path, port: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[server]\nport = {port}\n")
    return path


def test_legacy_user_config_is_found_without_xdg(search_home):
    home, _ = search_home
    p = _put(home / ".config" / "rcm" / "server.toml", 9001)
    cfg = load_server_config(None, environ={})
    assert cfg.path is not None and cfg.path.resolve() == p.resolve()
    assert cfg.server.port == 9001


def test_legacy_user_config_is_found_when_xdg_dir_has_no_rcm(search_home, tmp_path, monkeypatch):
    home, _ = search_home
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    p = _put(home / ".config" / "rcm" / "server.toml", 9002)
    cfg = load_server_config(None, environ={})
    assert cfg.path is not None and cfg.path.resolve() == p.resolve()
    assert cfg.server.port == 9002


def test_xdg_config_wins_over_legacy_user_config(search_home, tmp_path, monkeypatch):
    home, _ = search_home
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    _put(home / ".config" / "rcm" / "server.toml", 9003)
    p = _put(xdg / "rcm" / "server.toml", 9004)
    cfg = load_server_config(None, environ={})
    assert cfg.path is not None and cfg.path.resolve() == p.resolve()
    assert cfg.server.port == 9004


def test_local_rcm_toml_wins_over_user_configs(search_home, tmp_path, monkeypatch):
    """`./rcm.toml` 이 사용자 설정(XDG · ~/.config)보다 앞선다 — PLAN 「설정」의 탐색 순서."""
    home, cwd = search_home
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    _put(home / ".config" / "rcm" / "server.toml", 9005)
    _put(xdg / "rcm" / "server.toml", 9006)
    p = _put(cwd / "rcm.toml", 9007)
    cfg = load_server_config(None, environ={})
    assert cfg.path is not None and cfg.path.resolve() == p.resolve()
    assert cfg.server.port == 9007


def test_legacy_client_config_is_found_when_xdg_dir_has_no_rcm(search_home, tmp_path, monkeypatch):
    home, _ = search_home
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    p = home / ".config" / "rcm" / "client.toml"
    p.parent.mkdir(parents=True)
    p.write_text('server = "http://legacy:8787"\n')
    cfg = load_client_config(None, environ={})
    assert cfg.path is not None and cfg.path.resolve() == p.resolve()
    assert cfg.server == "http://legacy:8787"
