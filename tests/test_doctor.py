"""`rcm doctor` 의 순수 판정과 측정 부품(M5t-a 결정 246 · 247, docs/m5t-workplan.md §4.5).

여기는 서버 없이 도는 것만 본다. 종료 코드의 우선순위, 깨끗한 환경, 홈 가리기, 서버가 보낸 문구
정리, 프리셋 판정, rcm 자신의 요구, 호스트 사실, 서비스 파일 읽기다. 명령 전체(가짜 서버 · 가짜
서비스 파일)는 `test_doctor_cli.py` 가 본다. 진짜 HOME · 운영 설정 · 운영 데이터는 쓰지 않는다.
"""

from __future__ import annotations

import plistlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import doctor, hostsample
from remote_ci_monitor.config import Probe
from remote_ci_monitor.core import capability as cap
from remote_ci_monitor.core import doctor as dc
from remote_ci_monitor.core.model import Preset


def facts(missing=(), workspace=(), passthrough=()) -> dict[str, Any]:
    return {
        "missing_tools": list(missing),
        "workspace_tools": list(workspace),
        "missing_passthrough": list(passthrough),
    }


def entry(name="build", *, placement="pool", pool="mbp", pools=(), checks=(), requires=("t",)):
    return {
        "name": name,
        "requires": list(requires),
        "env_passthrough": ["PATH"],
        "env": {},
        "pool": pool,
        "pools": list(pools),
        "placement": placement,
        "checks": list(checks),
    }


def classify(e: dict[str, Any], f: dict[str, Any], **kw: Any) -> dc.PresetRow:
    kw.setdefault("pool", "mbp")
    kw.setdefault("accept_any", True)
    kw.setdefault("hints", {})
    kw.setdefault("os_name", "darwin")
    return dc.classify_preset(e, f, **kw)


# ── 종료 코드 ────────────────────────────────────────────────────────────────


def test_td34_exit_priority_is_problem_then_unknown_then_ok():
    assert dc.decide_exit([], []) == 0
    assert dc.decide_exit(["fvm missing"], []) == 1
    assert dc.decide_exit([], ["server unreachable"]) == 3
    assert dc.decide_exit(["fvm missing"], ["server unreachable"]) == 1


# ── 깨끗한 환경 ──────────────────────────────────────────────────────────────

SHELL_ENV = {
    "HOME": "/h",
    "USER": "u",
    "LOGNAME": "u",
    "SHELL": "/bin/zsh",
    "TMPDIR": "/t/",
    "XDG_RUNTIME_DIR": "/run/user/501",
    "PATH": "/shell/bin:/usr/bin",
    "JAVA_HOME": "/jdk",
    "LANG": "ko_KR.UTF-8",
}


def test_td37_launchd_clean_env_has_only_its_defaults_and_the_service_env():
    env = dc.clean_env("launchd", SHELL_ENV, {"PATH": "/svc/bin", "FOO": "1"})
    assert env == {
        "HOME": "/h",
        "USER": "u",
        "LOGNAME": "u",
        "SHELL": "/bin/zsh",
        "TMPDIR": "/t/",
        "PATH": "/svc/bin",
        "FOO": "1",
    }


def test_td37_systemd_clean_env_has_xdg_runtime_dir_and_no_tmpdir():
    env = dc.clean_env("systemd", SHELL_ENV, {})
    assert set(env) == {"HOME", "USER", "LOGNAME", "SHELL", "XDG_RUNTIME_DIR", "PATH"}
    # 서비스 파일에 PATH 가 없으면 셸의 PATH 가 아니라 관리자의 기본 PATH 다
    assert env["PATH"] == dc.DEFAULT_PATHS["systemd"] != SHELL_ENV["PATH"]


def test_td29_clean_env_never_carries_the_shells_java_home():
    for manager in ("launchd", "systemd"):
        assert "JAVA_HOME" not in dc.clean_env(manager, SHELL_ENV, {"PATH": "/svc"})
        assert "LANG" not in dc.clean_env(manager, SHELL_ENV, {"PATH": "/svc"})


def test_td46_paths_hide_the_home_folder():
    assert dc.mask_home("/Users/x/bin:/usr/bin:/Users/x", "/Users/x") == "~/bin:/usr/bin:~"
    # 접두가 같을 뿐인 다른 폴더는 가리지 않는다
    assert dc.mask_home("/Users/xy/bin", "/Users/x") == "/Users/xy/bin"


def test_td32_server_text_loses_control_characters():
    assert dc.clean_text("brew\x1b[31m install\x07 fvm\n") == "brew[31m install fvm"
    assert len(dc.clean_text("x" * 500)) == 300


# ── 프리셋 판정 ──────────────────────────────────────────────────────────────


def test_td22_a_preset_without_checks_is_ready_once_its_tools_are_there():
    row = classify(entry(), facts())
    assert (row.applies, row.state) == (dc.APPLIES_POOL, dc.READY)
    # 서버 판정도 지금처럼 eligible 이다(결정 247 · 91~93)
    report = cap.CapabilityReport(
        worker="mbp",
        generation="1:0",
        measured_at=datetime.now(UTC),
        host_key="k",
        accept_any=True,
        fingerprint=cap.Fingerprint(probes={}, hash=None, stale=False),
        presets={"build": cap.PresetFacts(missing_tools=(), workspace_tools=())},
    )
    v = cap.verdict(
        Preset(name="build", argv=("true",), requires=("t",)),
        report,
        generation="1:0",
        now=datetime.now(UTC),
        max_age_seconds=1800,
    )
    assert v.state == cap.ELIGIBLE


def test_td11_tools_present_with_checks_left_is_needs_check_not_ok():
    row = classify(entry(checks=("sim-check",)), facts())
    assert row.state == dc.NEEDS_CHECK and row.checks == ["sim-check"]
    assert row.state != dc.READY


def test_td16_a_preset_for_another_pool_is_not_this_workers_target():
    row = classify(entry(pool="linux"), facts(missing=("t",)))
    assert row.applies == dc.APPLIES_OTHER_POOL
    assert not row.counts
    # `pools` 에 이 워커의 풀이 있으면 대상이다(`--pool mbp` 로 낸 잡이 온다)
    assert classify(entry(pool="linux", pools=("mbp",)), facts()).applies == dc.APPLIES_POOL


def test_td27_workspace_tool_is_a_warning_for_pool_and_not_coming_for_any_capable():
    pool_row = classify(entry(), facts(workspace=("t",)))
    assert pool_row.state == dc.READY and pool_row.warn, pool_row
    capable = classify(entry(placement="any-capable"), facts(workspace=("t",)))
    assert capable.applies == dc.APPLIES_NOT_COMING and not capable.counts


def test_td40_any_capable_with_a_missing_tool_is_not_coming_with_the_install_hint():
    hints = {"t": {"install": {"darwin": "brew install t", "any": "see docs"}, "docs": "https://x"}}
    row = classify(entry(placement="any-capable"), facts(missing=("t",)), hints=hints)
    assert row.applies == dc.APPLIES_NOT_COMING and row.warn
    assert row.missing == [{"tool": "t", "install": "brew install t", "docs": "https://x"}]
    assert not row.problem  # 종료 코드에 넣지 않는다
    # 이 머신이 든 풀의 pool 프리셋이면 문제다
    assert classify(entry(), facts(missing=("t",)), hints=hints).problem


# ── rcm 자신의 요구 ──────────────────────────────────────────────────────────


def test_td14_python_before_3_11_4_fails_the_requirement():
    for version in ((3, 11, 0), (3, 11, 3)):
        row = doctor.python_requirement(version, True, platform="darwin", package_manager="brew")
        assert row["state"] == "fail", (version, row)
    ok = doctor.python_requirement((3, 11, 4), True, platform="darwin", package_manager="brew")
    assert ok["state"] == "ok", ok
    # data filter 가 없는 빌드도 FAIL 이다
    bad = doctor.python_requirement((3, 12, 0), False, platform="darwin", package_manager="brew")
    assert bad["state"] == "fail", bad


def test_td7_root_drops_sudo_from_rcms_own_hints_only():
    user = doctor.install_hint("git", platform="linux", package_manager="apt-get", root=False)
    root = doctor.install_hint("git", platform="linux", package_manager="apt-get", root=True)
    assert user.startswith("sudo ") and "sudo" not in root, (user, root)
    # 코디네이터 관리자가 쓴 [[tools]] 문구는 그대로다
    hints = {"t": {"install": {"linux": "sudo apt-get install t"}}}
    row = classify(entry(), facts(missing=("t",)), hints=hints, os_name="linux")
    assert row.missing[0]["install"] == "sudo apt-get install t"


# ── 호스트 사실 ──────────────────────────────────────────────────────────────


class FakeRunner:
    def __init__(self, outputs: dict[tuple[str, ...], str]):
        self.outputs = outputs
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def __call__(self, argv: list[str], env: dict[str, str]) -> str | None:
        self.calls.append((tuple(argv), dict(env)))
        return self.outputs.get(tuple(argv))


MAC_ROSETTA = {
    ("sw_vers", "-productVersion"): "15.6\n",
    ("uname", "-m"): "x86_64\n",
    ("sysctl", "-n", "hw.optional.arm64"): "1\n",
    ("sysctl", "-n", "sysctl.proc_translated"): "1\n",
}


def mac_facts(outputs: dict[tuple[str, ...], str]) -> tuple[dict[str, Any], FakeRunner]:
    run = FakeRunner(outputs)
    got = hostsample.host_facts(
        runner=run,
        platform="darwin",
        exists=lambda _p: False,
        read_file=lambda _p: None,
        euid=501,
        which=lambda n: "/opt/homebrew/bin/brew" if n == "brew" else None,
        python="3.12.1",
    )
    return got, run


def test_td6_rosetta_reports_the_real_arch():
    got, _ = mac_facts(MAC_ROSETTA)
    assert got["arch"] == "arm64" and got["rosetta"] is True, got
    assert got["os"] == "darwin" and got["os_release"] == "15.6"
    native = {
        ("sw_vers", "-productVersion"): "15.6\n",
        ("uname", "-m"): "x86_64\n",
    }
    intel, _ = mac_facts(native)
    assert intel["arch"] == "x86_64" and intel["rosetta"] is False, intel


def test_td54_host_fact_commands_run_with_lang_c():
    _, run = mac_facts(MAC_ROSETTA)
    assert run.calls, "no host fact command ran"
    assert all(env.get("LANG") == "C" for _argv, env in run.calls), run.calls
    # 부르는 명령은 한 곳의 허용 목록에서만 온다
    allowed = {tuple(v) for v in hostsample.HOST_FACT_COMMANDS.values()}
    assert {argv for argv, _env in run.calls} <= allowed


def test_td54_probes_do_not_get_lang_c(tmp_path):
    probe_bin = tmp_path / "show-lang"
    probe_bin.write_text('#!/bin/sh\necho "lang=$LANG"\n')
    probe_bin.chmod(0o755)
    probe = Probe(name="lang", argv=(str(probe_bin),), timeout_seconds=5)
    got = hostsample.run_probes([probe], {"PATH": "/usr/bin:/bin"})["lang"]
    assert (got.state, got.value) == ("ok", "lang="), got


def test_host_facts_on_a_linux_container_as_root():
    os_release = 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu"\n'
    run = FakeRunner({("uname", "-m"): "aarch64\n"})
    got = hostsample.host_facts(
        runner=run,
        platform="linux",
        exists=lambda p: p == "/.dockerenv",
        read_file=lambda p: os_release if p == "/etc/os-release" else None,
        euid=0,
        which=lambda n: "/usr/bin/apt-get" if n == "apt-get" else None,
        python="3.12.3",
    )
    assert got["os_release"] == "ubuntu 24.04" and got["os_like"] == "debian", got
    assert got["container"] is True and got["root"] is True, got
    assert got["package_manager"] == "apt-get" and got["arch"] == "aarch64", got


# ── 서비스 파일 ──────────────────────────────────────────────────────────────


def test_td55_service_file_env_is_read_with_names_and_values_kept_apart(tmp_path: Path):
    plist = tmp_path / "w.plist"
    plist.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.remote-ci-monitor.worker",
                "ProgramArguments": ["/x/rcm", "worker", "--config", "/x/worker.toml"],
                "EnvironmentVariables": {"PATH": "/svc/bin", "SECRET_TOKEN": "td55-value"},
                "SoftResourceLimits": {"NumberOfFiles": 4096},
            }
        )
    )
    sf = doctor.read_service_file(plist)
    assert (sf.manager, sf.command, sf.nofile) == ("launchd", "worker", 4096)
    assert sf.env == {"PATH": "/svc/bin", "SECRET_TOKEN": "td55-value"}
    assert sf.env_names == ["PATH", "SECRET_TOKEN"]
    unit = tmp_path / "rcm-worker.service"
    unit.write_text(
        "[Service]\nExecStart=/x/rcm worker --config /x/w.toml\n"
        'Environment=PATH=/svc/bin "SECRET_TOKEN=td55 value"\nLimitNOFILE=1024\n'
    )
    su = doctor.read_service_file(unit)
    assert (su.manager, su.command, su.nofile) == ("systemd", "worker", 1024)
    assert su.env == {"PATH": "/svc/bin", "SECRET_TOKEN": "td55 value"}


# ── 설치 안내 `[[tools]]` (TD9) ──────────────────────────────────────────────


def _server_toml(tmp_path: Path, tools: str, *, probes: str = "") -> Path:
    p = tmp_path / "server.toml"
    p.write_text(
        f"""[server]
data_dir = "{tmp_path / "data"}"

[[presets]]
name = "app"
argv = ["true"]
requires = ["/opt/td/bin/rcm-td-fvm", "rcm-td-node"]
{probes}
{tools}"""
    )
    return p


def test_td9_tools_are_read_with_their_os_hints(tmp_path):
    from remote_ci_monitor.config import load_server_config

    cfg = load_server_config(
        _server_toml(
            tmp_path,
            """[[tools]]
name = "rcm-td-fvm"
docs = "https://example.invalid/fvm"
[tools.install]
darwin = "brew install td-fvm"
any = "see the docs"
""",
        ),
        check_tools=False,
    )
    (hint,) = cfg.tools
    assert (hint.name, hint.docs) == ("rcm-td-fvm", "https://example.invalid/fvm")
    assert hint.install == {"darwin": "brew install td-fvm", "any": "see the docs"}


@pytest.mark.parametrize(
    "tools,needle",
    [
        ('[[tools]]\nname = "rcm-td-fvm"\ndocs = "a\\u0007b"\n', "control"),
        ('[[tools]]\nname = "rcm-td-fvm"\n[tools.install]\ndarwin = "a\\nb"\n', "control"),
        (f'[[tools]]\nname = "rcm-td-fvm"\ndocs = "{"x" * 301}"\n', "300"),
        ('[[tools]]\nname = "rcm-td-fvm"\n[tools.install]\nwindows = "choco"\n', "windows"),
        ('[[tools]]\nname = "rcm-td-fvm"\n\n[[tools]]\nname = "rcm-td-fvm"\n', "twice"),
        ('[[tools]]\nname = "rcm-td-fvm"\nrun = "x"\n', "run"),
    ],
)
def test_td9_bad_tools_refuse_to_start(tmp_path, tools, needle):
    from remote_ci_monitor.config import ConfigError, load_server_config

    with pytest.raises(ConfigError) as e:
        load_server_config(_server_toml(tmp_path, tools), check_tools=False)
    assert "[[tools]]" in str(e.value) and needle in str(e.value), e.value


def test_td9_a_tool_nobody_uses_is_a_warning_but_a_probe_argv0_is_not(tmp_path):
    from remote_ci_monitor import config

    probes = '[[probes]]\nname = "flutter"\nargv = ["/opt/td/rcm-td-flutter", "--version"]\n'
    tools = "".join(
        f'[[tools]]\nname = "{n}"\n'
        for n in ("rcm-td-fvm", "rcm-td-node", "rcm-td-flutter", "rcm-td-orphan")
    )
    cfg = config.load_server_config(_server_toml(tmp_path, tools, probes=probes), check_tools=False)
    warnings = config.tool_warnings(cfg)
    assert len(warnings) == 1 and "rcm-td-orphan" in warnings[0], warnings
