"""설정 로딩 — 우선순위(플래그 > env > 파일 > 기본값) · 프리셋 오류 메시지 · 시간대."""

import os
from pathlib import Path

import pytest

from remote_ci_monitor.config import (
    ConfigError,
    load_client_config,
    load_server_config,
    parse_preset,
)

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
