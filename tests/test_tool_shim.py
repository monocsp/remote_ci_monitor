"""macOS Command Line Tools shim 판별(M5t-a 결정 246, docs/m5t-workplan.md §4.5 「macOS shim」).

CLT 가 없는 macOS 에서 `/usr/bin/git` 같은 shim 을 실행하면 설치 대화상자가 뜬다. 헤드리스 서비스
에서는 그 창이 프로브 주기마다 되풀이된다. 그래서 `/usr/bin/` 바로 아래이고 바이트에 `libxcselect`
가 든 실행 파일은 shim 으로 보고, `xcode-select -p` 가 실패하면 실행하지 않고 「없음」으로 센다.

판별 함수는 하나다(`runner.tool_blocked`). 서버 판정(`measure_tools`), 잡 직전 검사
(`missing_tools`), 서버의 `preset_env` 측정, `rcm check` 의 도구 행, 워커의 프로브가 모두 그 함수를
거친다. 진짜
`/usr/bin` 과 진짜 `xcode-select` 는 쓰지 않는다 — 가짜 폴더와 가짜 스크립트를 모듈 값으로 끼운다.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_ci_monitor import cli, hostsample, runner
from remote_ci_monitor.config import Probe
from remote_ci_monitor.core.model import Preset
from remote_ci_monitor.server import App


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def shimdir(tmp_path, monkeypatch) -> Path:
    """가짜 `/usr/bin/` — 그 아래 `git` 은 shim(바이트에 `libxcselect`), `real-tool` 은 보통 도구.

    shim 을 실행하면 표지 파일을 남긴다 — 「실행하지 않는다」를 그 파일로 본다.
    """
    d = tmp_path / "usr-bin"
    d.mkdir()
    marker = tmp_path / "shim-ran"
    script(d / "git", f"# links against libxcselect.dylib\ntouch {marker}\necho 'git 2.0'")
    script(d / "xcode-select", "# libxcselect\necho /Library/Developer/CommandLineTools")
    script(d / "real-tool", "echo real")
    monkeypatch.setattr(runner, "SHIM_DIR", str(d) + "/")
    monkeypatch.setattr(runner, "SHIM_PLATFORM", "darwin")
    return d


def no_clt(tmp_path: Path, monkeypatch) -> None:
    """`xcode-select -p` 가 실패한다 — CLT 가 깔리지 않은 macOS."""
    fake = script(tmp_path / "fake-xcode-select", "exit 2")
    monkeypatch.setattr(runner, "XCODE_SELECT", str(fake))


def with_clt(tmp_path: Path, monkeypatch) -> None:
    fake = script(tmp_path / "fake-xcode-select-ok", "echo /Library/Developer/CommandLineTools")
    monkeypatch.setattr(runner, "XCODE_SELECT", str(fake))


def test_td1_shim_without_clt_is_missing_and_never_runs(shimdir, tmp_path, monkeypatch):
    no_clt(tmp_path, monkeypatch)
    env = {"PATH": str(shimdir)}
    assert runner.missing_tools(("git",), env) == ["git"]
    assert runner.missing_note("git", env) == "missing (needs Command Line Tools)"
    probe = Probe(name="git", argv=("git", "--version"), timeout_seconds=5)
    result = hostsample.run_probes([probe], env)["git"]
    assert result.state == "missing", result
    assert not (tmp_path / "shim-ran").exists(), "the shim was executed"


def test_td1_shim_with_clt_installed_is_a_tool(shimdir, tmp_path, monkeypatch):
    with_clt(tmp_path, monkeypatch)
    env = {"PATH": str(shimdir)}
    assert runner.missing_tools(("git",), env) == []
    assert runner.missing_note("git", env) is None


def test_td33_shim_is_judged_by_libxcselect_bytes_not_by_name(shimdir, tmp_path, monkeypatch):
    no_clt(tmp_path, monkeypatch)
    # 이름이 shim 목록에 있을 법한 `make` 라도 바이트에 표지가 없으면 보통 도구다
    script(shimdir / "make", "echo make")
    # 이름이 낯선 도구라도 바이트에 표지가 있으면 shim 이다
    script(shimdir / "rcm-td-odd", "# libxcselect\necho odd")
    env = {"PATH": str(shimdir)}
    assert runner.missing_tools(("make", "rcm-td-odd", "real-tool"), env) == ["rcm-td-odd"]
    # `/usr/bin/` **바로 아래**만 본다 — 하위 폴더의 같은 바이트는 shim 이 아니다
    sub = shimdir / "sub"
    sub.mkdir()
    script(sub / "rcm-td-deep", "# libxcselect\necho deep")
    assert runner.missing_tools(("rcm-td-deep",), {"PATH": str(sub)}) == []


def test_td33_the_worker_probe_follows_the_same_rule(shimdir, tmp_path, monkeypatch):
    no_clt(tmp_path, monkeypatch)
    env = {"PATH": str(shimdir)}
    probes = [
        Probe(name="git", argv=("git", "--version"), timeout_seconds=5),
        Probe(name="abs", argv=(str(shimdir / "git"), "--version"), timeout_seconds=5),
        Probe(name="real", argv=("real-tool",), timeout_seconds=5),
    ]
    got = hostsample.run_probes(probes, env)
    assert got["git"].state == "missing" and got["abs"].state == "missing", got
    assert got["real"].state == "ok" and got["real"].value == "real", got
    assert not (tmp_path / "shim-ran").exists()


def test_td33_other_platforms_never_judge_shims(shimdir, tmp_path, monkeypatch):
    no_clt(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "SHIM_PLATFORM", "linux")
    assert runner.missing_tools(("git",), {"PATH": str(shimdir)}) == []


def test_td43_measure_tools_applies_the_shim_rule(shimdir, tmp_path, monkeypatch):
    """서버의 `requires` 판정(`measure_tools`)도 같은 규칙 — doctor 와 서버 판정이 갈리지 않는다."""
    no_clt(tmp_path, monkeypatch)
    env = {"PATH": str(shimdir)}
    assert runner.measure_tools(("git", "real-tool"), env) == (["git"], [])
    # 절대경로로 선언해도 같다
    assert runner.measure_tools((str(shimdir / "git"),), env) == ([str(shimdir / "git")], [])
    # PATH 에 상대 항목이 있으면 막힌 shim 은 「못 찾음」과 같다 — 워크스페이스의 도구가 앞설
    # 수 있으니 「작업 공간에 있을지 모른다」(결정 93)로 남는다. shim 을 「있음」으로 세지 않는다
    rel = {"PATH": f"{shimdir}{os.pathsep}tools"}
    assert runner.measure_tools(("git",), rel) == ([], ["git"])


def test_td52_four_paths_give_the_same_answer(shimdir, tmp_path, monkeypatch):
    no_clt(tmp_path, monkeypatch)
    env = {"PATH": str(shimdir)}
    preset = Preset(name="lint", argv=("true",), requires=("git", "real-tool"), env=dict(env))
    # ① 서버 판정
    assert runner.measure_tools(preset.requires, env)[0] == ["git"]
    # ② 잡 직전
    assert runner.missing_tools(preset.requires, env) == ["git"]
    # ③ 서버의 preset_env 측정(`/api/health` 의 service preset tools)
    fake_app = SimpleNamespace(config=SimpleNamespace(presets=[preset]))
    App.measure_preset_env(fake_app, environ={})
    assert fake_app.preset_env["lint"]["missing_tools"] == ["git"], fake_app.preset_env
    # ④ `rcm check` 의 local preset tools 행
    monkeypatch.setenv("PATH", str(shimdir))
    row = cli._local_preset_tools_row(SimpleNamespace(presets=[preset]))
    assert row is not None and row[1] is False, row
    assert "git missing" in row[2] and "real-tool ok" in row[2], row


def test_td52_xcode_select_itself_is_never_a_shim(shimdir, tmp_path, monkeypatch):
    no_clt(tmp_path, monkeypatch)
    env = {"PATH": str(shimdir)}
    assert runner.missing_tools(("xcode-select",), env) == []
    assert runner.measure_tools(("xcode-select",), env) == ([], [])
