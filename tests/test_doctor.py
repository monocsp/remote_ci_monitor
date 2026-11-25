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
    kw.setdefault("unreachable", {})  # 새 서버처럼 「닿지 않는 점검 없음」(TO18 의 칸)
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
    env = dc.clean_env("systemd", SHELL_ENV, {"PATH": "/svc/bin"})
    assert set(env) == {"HOME", "USER", "LOGNAME", "SHELL", "XDG_RUNTIME_DIR", "PATH"}
    assert env["PATH"] == "/svc/bin"
    # 서비스 파일에 PATH 가 없으면 셸의 PATH 도 지어낸 기본값도 아니다 — 모른다(위임 결정 5)
    assert "PATH" not in dc.clean_env("systemd", SHELL_ENV, {})
    assert dc.clean_env("launchd", SHELL_ENV, {})["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"


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


def test_to18_unreachable_checks_decide_before_tools_and_keep_their_code():
    """서버가 적은 점검은 도구보다 먼저 「오지 않음」이다. 이유 코드가 `check_local_only` 가 아니면
    그 코드를 그대로 두고 「로컬 전용」이라 부르지 않는다. 칸이 없으면(None) 「모름」이다."""
    gated = entry(checks=("sim-check",))
    row = classify(gated, facts(missing=("t",)), unreachable={"sim-check": "check_local_only"})
    assert (row.applies, row.code, row.problem) == ("not_coming", "check_local_only", False), row
    row = classify(gated, facts(), unreachable={"sim-check": "check_other_pool"})
    assert row.code == "check_other_pool" and "local-only" not in (row.note or ""), row
    row = classify(gated, facts(), unreachable=None)
    assert (row.state, row.code) == ("unknown", "check_unknown"), row
    assert classify(entry(), facts(), unreachable=None).state == "ready"


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
        ("sysctl", "-n", "hw.optional.arm64"): "0\n",  # Intel Mac 의 답
    }
    intel, _ = mac_facts(native)
    assert intel["arch"] == "x86_64" and intel["rosetta"] is False, intel


def test_rosetta_is_unknown_when_both_sysctl_answers_fail():
    """`sysctl` 두 질문이 모두 실패하면(서비스 PATH 에 `/usr/sbin` 이 없다) Rosetta 를 모른다 —
    거짓으로 적지 않는다(P3 1라운드 검증자 1 #5). `uname -m` 이 arm64 면 번역 중일 수 없다."""
    unknown, _ = mac_facts({("uname", "-m"): "x86_64\n"})
    assert unknown["rosetta"] is None and unknown["arch"] == "x86_64", unknown
    native, _ = mac_facts({("uname", "-m"): "arm64\n"})
    assert native["rosetta"] is False and native["arch"] == "arm64", native


def linux_facts(outputs: dict[tuple[str, ...], str], *, marker: bool = False) -> dict[str, Any]:
    return hostsample.host_facts(
        runner=FakeRunner(outputs),
        platform="linux",
        exists=lambda p: marker and p == "/.dockerenv",
        read_file=lambda _p: "ID=debian\nVERSION_ID=12\n",
        euid=1000,
        which=lambda _n: None,
        python="3.12.3",
    )


def test_container_is_unknown_without_markers_or_an_answer():
    """표지 파일이 없고 `systemd-detect-virt` 도 답하지 않으면 컨테이너인지 모른다(null).
    `none` 이라는 답은 「컨테이너 아님」이다(P3 1라운드 검증자 1 #5)."""
    virt = ("systemd-detect-virt", "-c")
    assert linux_facts({})["container"] is None
    assert linux_facts({virt: "none\n"})["container"] is False
    assert linux_facts({virt: "docker\n"})["container"] is True
    assert linux_facts({}, marker=True)["container"] is True


def test_systemd_detect_virt_says_none_with_exit_1_and_that_is_an_answer(tmp_path: Path):
    """`systemd-detect-virt -c` 는 컨테이너가 아니면 `none` 을 찍고 1 로 끝난다. 그 답을 버리면
    보통 리눅스 머신이 모두 「모름」이 된다."""
    d = tmp_path / "bin"
    d.mkdir()
    tool = d / "systemd-detect-virt"
    tool.write_text("#!/bin/sh\necho none\nexit 1\n")
    tool.chmod(0o755)
    other = d / "uname"
    other.write_text("#!/bin/sh\necho nope\nexit 1\n")
    other.chmod(0o755)
    env = {"PATH": str(d)}
    assert hostsample._fact_command(["systemd-detect-virt", "-c"], env) == "none\n"
    assert hostsample._fact_command(["uname", "-m"], env) is None


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


# ── 모름은 모름으로 (P3 3라운드 근본 원인) ─────────────────────────────────────


def test_clean_text_of_unknown_is_empty_not_the_word_none():
    assert dc.clean_text(None) == ""


def test_no_jobs_reason_does_not_fire_while_a_preset_may_come():
    """오는지 모르는 프리셋이 있으면 「아무것도 오지 않는다」(종료 1)로 단정하지 않는다."""
    unknown = dc.PresetRow("build", "unknown", "unknown")
    assert dc.no_jobs_reason([unknown], "mbp") is None


@pytest.mark.parametrize("item", [{"code": "check_local_only"}, {"name": "qa"}, {}])
def test_an_unreachable_checks_item_without_a_name_or_code_is_unknown_not_dropped(item):
    """이름이나 이유 코드가 없는 항목을 조용히 버리면 그 점검이 「닿는다」로 읽힌다. 서버 답을 읽는
    자리는 하나다(`doctor.reach` 가 쓰는 모양 검사) — 그 자리가 그 칸을 말하고 답 전체를
    모름으로 둔다. 같은 일을 하던 `core.doctor.unreachable_checks` 는 쓰는 곳이 없어
    지웠다(P3 4라운드 LOW)."""
    from remote_ci_monitor import doctor

    entry = {"name": "app", "requires": [], "env_passthrough": [], "env": {}}
    answer = {"presets": [{**entry, "unreachable_checks": [item]}]}
    problem = doctor.shape_problem(answer, doctor.ANSWER_SHAPES["/worker/spec"])
    assert problem is not None and "presets[0].unreachable_checks[0]" in problem, problem
    assert not hasattr(dc, "unreachable_checks")


@pytest.mark.parametrize("name", [" rcm-td-fvm", "rcm-td-fvm ", "x" * 65])
def test_a_tool_name_doctor_would_change_refuses_to_start(tmp_path, name):
    """doctor 는 서버가 준 도구 이름을 strip 하고 64자로 자른다. 그러면 `requires` 의 이름과 맞지
    않아 안내가 사라진다 — 설정을 읽을 때 거절한다(P3 3라운드 LOW)."""
    from remote_ci_monitor.config import ConfigError, load_server_config

    with pytest.raises(ConfigError) as e:
        load_server_config(
            _server_toml(tmp_path, f'[[tools]]\nname = "{name}"\n'), check_tools=False
        )
    assert "[[tools]]" in str(e.value) and "name" in str(e.value), e.value


# ── P6 GAP X-bidi: 정리 함수 하나(`core/progress.clean_line` 규칙) ───────────────────

BIDI_AND_SEPARATORS = "  ‪‮⁦⁩​﻿"


def test_x_bidi_server_text_loses_line_separators_and_bidi_controls():
    """서버가 보낸 문자열(`dc.clean_text`)도 줄 · 문단 구분자와 bidi 제어 · 폭 없는 글자를 지운다 —
    같은 PR 의 `capabilities.clean_line` 과 같은 규칙이다(P6 GAP X-bidi)."""
    assert dc.clean_text(f"brew{BIDI_AND_SEPARATORS} install\x1b[2J td") == "brew install[2J td"


@pytest.mark.parametrize("char", list(BIDI_AND_SEPARATORS))
def test_x_bidi_tools_refuse_line_separators_and_bidi_controls(tmp_path, char):
    """`[[tools]]` 설정 검증도 같은 글자를 거절한다 — 워커가 그대로 터미널에 찍는 글이다."""
    from remote_ci_monitor.config import ConfigError, load_server_config

    escaped = f"\\u{ord(char):04x}"
    tools = f'[[tools]]\nname = "rcm-td-fvm"\n[tools.install]\ndarwin = "brew {escaped}x"\n'
    with pytest.raises(ConfigError) as e:
        load_server_config(_server_toml(tmp_path, tools), check_tools=False)
    assert "[[tools]]" in str(e.value) and "control" in str(e.value), e.value


def test_x_python_the_service_interpreter_is_read_from_the_rcm_script(tmp_path):
    """rcm 콘솔 스크립트의 `#!` 줄 · pip 의 sh 트램펄린 · `env python3` 에서 서비스의 Python 을
    읽는다. 읽을 수 없으면 None(다른지 모른다)이다(P6 GAP X-python)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    py = bindir / "python3.12"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)

    def rcm(body: str) -> str:
        p = tmp_path / "rcm"
        p.write_text(body)
        return str(p)

    assert doctor.service_interpreter(rcm(f"#!{py}\n"), None) == str(py)
    trampoline = f"#!/bin/sh\n'''exec' \"{py}\" \"$0\" \"$@\"\n' '''\n"
    assert doctor.service_interpreter(rcm(trampoline), None) == str(py)
    assert doctor.service_interpreter(rcm("#!/usr/bin/env python3.12\n"), str(bindir)) == str(py)
    assert doctor.service_interpreter(rcm("#!/usr/bin/env python3.12\n"), None) is None
    assert doctor.service_interpreter(rcm("no shebang\n"), None) is None
    assert doctor.service_interpreter(str(tmp_path / "missing" / "rcm"), None) is None
    assert doctor.service_interpreter(str(py), None) == str(py)
    assert doctor.service_interpreter("/usr/bin/env", None) is None
