"""`rcm check --config` 의 `local preset tools` 행(M5j G4) — **이 셸**의 검사다.

명세 `docs/gate-optimization-workplan.md` §2 G4 · Codex 리뷰 G4: 셸에서 돌린 `rcm check` 가 launchd
서비스의 환경도 정상이라고 보이게 하면 fail-open 이다. 그래서 행 이름이 `local` 이고 상세가 「이
셸」이라고 말한다. 정본은 잡 시작 전 검사(`tests/test_requires.py`).

`test_cli_m4` 의 픽스처(서버 · env · server_toml)를 그대로 쓴다.
"""

from __future__ import annotations

import os

import test_cli_m4 as m4
from test_cli_m4 import TREE_SERVER_TOML, row_status, run

MISSING = "definitely-missing-tool-rcm"

#: test_cli_m4 의 픽스처를 이름 그대로 다시 묶는다(F811 없이 인자 이름을 쓰기 위해).
srv, env, home, server_toml = m4.srv, m4.env, m4.home, m4.server_toml

REQUIRES_SERVER_TOML = """\
[server]
data_dir = "{data_dir}"

[[presets]]
name = "gate"
argv = ["sh", "-c", "true"]
requires = ["sh", "%s"]

[[presets]]
name = "plain"
argv = ["sh", "-c", "true"]

[[presets]]
name = "deploy"
argv = ["sh", "-c", "true"]
requires = ["sh"]
"""


def test_check_row_local_preset_tools_names_each_tool_and_its_verdict(
    srv, env, server_toml, capsys
):
    """행 이름은 `local preset tools` — 이 셸의 검사다. 프리셋마다 이름과 판정, 없는 게 하나라도
    있으면 FAIL. `requires` 가 없는 프리셋은 안 나온다. PATH 값은 찍지 않는다."""
    env(srv)
    cfg = server_toml(REQUIRES_SERVER_TOML % MISSING)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    assert row_status(out, "local preset tools") == "FAIL", out
    line = next(ln for ln in out.splitlines() if "local preset tools" in ln)
    assert f"gate (sh ok · {MISSING} missing)" in line, line
    assert "deploy (sh ok)" in line and "plain" not in line, line
    assert "shell" in line, line  # 정본이 아님을 행이 말한다
    assert os.environ["PATH"] not in line
    assert row_status(out, "server") == "ok" and row_status(out, "local data dir") == "ok"


def test_check_row_local_preset_tools_is_ok_when_everything_is_found(srv, env, server_toml, capsys):
    env(srv)
    cfg = server_toml(REQUIRES_SERVER_TOML % "/bin/sh")
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    assert row_status(out, "local preset tools") == "ok", out
    line = next(ln for ln in out.splitlines() if "local preset tools" in ln)
    assert "gate (sh ok · /bin/sh ok)" in line and "deploy (sh ok)" in line, line


def test_check_has_no_local_preset_tools_row_without_requires(srv, env, server_toml, capsys):
    env(srv)
    cfg = server_toml(TREE_SERVER_TOML)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    assert row_status(out, "local preset tools") is None, out


def test_check_has_no_local_preset_tools_row_without_a_config(srv, env, capsys):
    env(srv)
    code, out, err = run(capsys, ["check"])
    assert code == 0, out + err
    assert row_status(out, "local preset tools") is None, out
