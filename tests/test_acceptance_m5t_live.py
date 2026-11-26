"""M5t-a 실기 하네스 `scripts/acceptance/m5t_live.sh` 의 순수 부분(명세 §7 · TR16 · TD24 · TN8).

하네스 자체는 운영 머신에서 진짜 launchd · 서버 · 워커를 띄우므로 pytest 가 부르지 않는다. 여기서는
그 하네스를 `source` 해서 검사 함수만 bash 로 부른다 — 시험 값이 아닌 것(운영 라벨 · 8787 · 운영
경로)을 bootstrap 전에 거절하는지, doctor 의 사람용 표와 `--json` 을 견주는 함수가 어긋남을 잡는지,
판정 함수가 모르는 것을 PASS 로 적지 않는지다. 프로세스를 띄우지 않고 네트워크도 쓰지 않는다.
"""

from __future__ import annotations

import ast
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "m5t_live.sh"


def sh(
    fn: str, *args: str, home: Path | None = None, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """하네스를 source 하고 함수 하나를 부른다. 파이썬은 이 시험의 해석기(`M5T_PY`)다.
    `extra` 는 함수가 읽는 전역(BOOTED · LABEL · OUT …)과 가짜 도구를 앞에 둔 PATH 다."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "M5T_PY": sys.executable,
        "HOME": str(home) if home is not None else os.environ.get("HOME", "/"),
        **(extra or {}),
    }
    return subprocess.run(
        ["bash", "-c", 'source "$1" && shift && "$@"', "bash", str(SCRIPT), fn, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )


@pytest.fixture
def place(tmp_path: Path) -> dict[str, Path]:
    """가짜 홈과 그 아래 시험 폴더 — 진짜 홈은 건드리지 않는다."""
    home = tmp_path / "h"
    base = home / ".local" / "share" / "rcm-devtest" / "m5t-live-ab12cd34"
    (base / "home").mkdir(parents=True)
    return {"home": home, "base": base}


def test_tr16_sourcing_the_harness_runs_nothing(tmp_path: Path) -> None:
    r = subprocess.run(
        ["bash", "-c", 'source "$1" && echo sourced', "bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "M5T_PY": sys.executable},
        timeout=15,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "sourced"
    assert list(tmp_path.iterdir()) == []  # 폴더 하나 만들지 않았다


@pytest.mark.parametrize(
    "label",
    [
        "com.remote-ci-monitor.server",
        "com.remote-ci-monitor.worker",
        "COM.Remote-CI-Monitor.Server",
        "com.remote-ci-monitor.server-m5t",
        "com.example.rcm-m5t-live-",
        "com.example.rcm-m5t-live-AB12",
        "com.example.rcm-m5t-live-ab12/../x",
        "com.example.other-ab12cd34",
        "",
    ],
)
def test_tr16_label_check_refuses_production_and_foreign_labels(label: str) -> None:
    r = sh("is_test_label", label)
    assert r.returncode == 1, (label, r.stdout, r.stderr)


def test_tr16_label_check_takes_the_test_label() -> None:
    r = sh("is_test_label", "com.example.rcm-m5t-live-ab12cd34")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("port", ["8787", "8788", "80", "8789", "8900", "abc", "8794x", ""])
def test_tr16_port_check_refuses_production_and_out_of_range_ports(port: str) -> None:
    assert sh("is_test_port", port).returncode == 1, port


@pytest.mark.parametrize("port", ["8790", "8794", "8899"])
def test_tr16_port_check_takes_a_test_port(port: str) -> None:
    assert sh("is_test_port", port).returncode == 0, port


def test_tr16_base_check_takes_only_the_devtest_folders(place: dict[str, Path]) -> None:
    home = place["home"]
    good = [
        place["base"],
        home / ".local" / "share" / "rcm-devtest-m5t",
    ]
    bad = [
        home / ".local" / "share" / "rcm",
        home / ".local" / "share" / "rcm" / "m5t",
        home / ".local" / "share" / "rcm-devtest",
        home / ".local" / "share" / "rcm-devtest" / "a" / "b",
        home / ".config" / "rcm",
        home,
        Path("/tmp/rcm-devtest-m5t"),
        Path("/private/tmp/m5t"),
    ]
    for b in good:
        assert sh("is_test_base", str(b), str(home)).returncode == 0, b
    for b in bad:
        assert sh("is_test_base", str(b), str(home)).returncode == 1, b
    dotted = f"{home}/.local/share/rcm-devtest/x/../../rcm"
    assert sh("is_test_base", dotted, str(home)).returncode == 1
    assert sh("is_test_base", "relative/rcm-devtest-x", str(home)).returncode == 1


def test_tr16_path_check_refuses_production_places(place: dict[str, Path]) -> None:
    home, base = place["home"], place["base"]
    inside = [base / "home" / "Library" / "LaunchAgents" / "x.plist", base / "data", base]
    production = [
        home / ".config" / "rcm" / "server.toml",
        home / ".local" / "share" / "rcm" / "rcm.sqlite3",
        home / "Library" / "LaunchAgents" / "com.example.rcm-m5t-live-ab12cd34.plist",
        home / "Library" / "Logs" / "rcm" / "server.log",
        Path("/tmp/x"),
        Path(f"{base}/../../rcm/rcm.sqlite3"),
        Path(f"{base}-other/data"),
    ]
    for p in inside:
        assert sh("is_test_path", str(p), str(base), str(home)).returncode == 0, p
    for p in production:
        assert sh("is_test_path", str(p), str(base), str(home)).returncode == 1, p


def _server_toml(path: Path, *, port: int, data_dir: Path, advertise: str, bind: str) -> None:
    path.write_text(
        "[server]\n"
        f'bind = "{bind}"\nport = {port}\ndata_dir = "{data_dir}"\nadvertise = {advertise}\n',
        encoding="utf-8",
    )


def test_tr16_server_toml_check_takes_test_values_only(place: dict[str, Path]) -> None:
    home, base = place["home"], place["base"]
    conf = base / "server.toml"
    data = base / "data"
    args = (str(conf), "8794", str(data), str(base), str(home))
    _server_toml(conf, port=8794, data_dir=data, advertise="false", bind="127.0.0.1")
    assert sh("check_server_toml", *args).returncode == 0
    cases = [
        dict(port=8787, data_dir=data, advertise="false", bind="127.0.0.1"),
        dict(port=8794, data_dir=data, advertise="true", bind="127.0.0.1"),
        dict(port=8794, data_dir=home / ".local/share/rcm", advertise="false", bind="127.0.0.1"),
        dict(port=8794, data_dir=data, advertise="false", bind="0.0.0.0"),
    ]
    for case in cases:
        _server_toml(conf, **case)  # type: ignore[arg-type]
        r = sh("check_server_toml", *args)
        assert r.returncode == 1, case
        assert r.stdout.strip(), case  # 무엇이 틀렸는지 한 줄
    conf.write_text("[server]\nport = 8794\n", encoding="utf-8")  # advertise · data_dir 없음
    assert sh("check_server_toml", *args).returncode == 1


def _plist(path: Path, **over: object) -> None:
    doc: dict[str, object] = {
        "Label": "com.example.rcm-m5t-live-ab12cd34",
        "ProgramArguments": ["/w/.venv/bin/rcm", "serve", "--config", str(over.pop("config"))],
        "WorkingDirectory": str(over.pop("home")),
        "StandardOutPath": str(over.pop("log")),
        "StandardErrorPath": str(over.pop("err")),
        "EnvironmentVariables": {"HOME": str(over.pop("env_home")), "PATH": "/usr/bin:/bin"},
    }
    doc.update(over)
    path.write_bytes(plistlib.dumps(doc))


def test_tr16_plist_check_takes_test_values_only(place: dict[str, Path]) -> None:
    home, base = place["home"], place["base"]
    plist = base / "svc.plist"
    conf = base / "xdg" / "config" / "rcm" / "server.toml"
    th = base / "home"
    log = th / "Library" / "Logs" / "rcm" / "x.log"
    label = "com.example.rcm-m5t-live-ab12cd34"
    args = (str(plist), label, str(conf), str(th), "/w/.venv/bin/rcm", str(base), str(home))
    good = dict(config=conf, home=th, log=log, err=log, env_home=th)
    _plist(plist, **good)
    assert sh("check_plist", *args).returncode == 0, sh("check_plist", *args).stdout
    real_logs = home / "Library" / "Logs" / "rcm" / "x.log"
    bad = [
        {**good, "Label": "com.remote-ci-monitor.server"},
        {**good, "config": home / ".config" / "rcm" / "server.toml"},
        {**good, "env_home": home},
        {**good, "log": real_logs},
        {**good, "err": real_logs},
        {**good, "home": home},
        {**good, "ProgramArguments": ["/other/rcm", "serve", "--config", str(conf)]},
        {**good, "ProgramArguments": ["/w/.venv/bin/rcm", "worker", "--config", str(conf)]},
    ]
    for case in bad:
        _plist(plist, **dict(case))
        r = sh("check_plist", *args)
        assert r.returncode == 1, case
        assert r.stdout.strip(), case
    # 파일 자체가 진짜 LaunchAgents 에 있으면 값이 맞아도 거절한다
    real = home / "Library" / "LaunchAgents" / f"{label}.plist"
    real.parent.mkdir(parents=True)
    _plist(real, **good)
    assert sh("check_plist", str(real), *args[1:]).returncode == 1


def _doctor_pair(tmp_path: Path, doc: dict[str, object], human: list[str]) -> tuple[str, str]:
    j, h = tmp_path / "d.json", tmp_path / "d.txt"
    j.write_text(json.dumps(doc), encoding="utf-8")
    h.write_text("\n".join(human) + "\n", encoding="utf-8")
    return str(h), str(j)


DOC = {
    # 키는 doctor 의 document() 가 늘 내는 그대로다(아래 키 잠금 시험이 견준다)
    "doctor_version": 1,
    "role": "worker",
    "measured_in": "service",
    "host": {"platform": "darwin"},
    "server": {"url": "http://127.0.0.1:8795", "state": "ok"},
    "service": {"file": None, "manager": "launchd", "path": "/usr/bin:/bin:/usr/sbin"},
    "shell": None,
    "rcm_requirements": [
        {"name": "python", "state": "ok", "detail": "3.11"},
        {"name": "git", "state": "warn", "detail": "old"},
    ],
    "presets": [
        {"name": "m5t-echo", "state": "ready", "applies": "pool"},
        {"name": "far", "state": None, "applies": "other_pool"},
    ],
    "probes": {"cpu": {"state": "ok", "value": "8"}},
    "remote_machines": None,
    "summary": {"ready": 1, "needs_check": 0, "missing": 0, "unknown": 0, "applies_unknown": 0},
    "problems": [],
    "undecided": [],
    "warnings": ["git: old"],
    "exit": 1,
}
HUMAN = [
    "rcm doctor · worker · measured in a clean service environment",
    "PATH     /usr/bin:/bin:/usr/sbin",
    "rcm's own needs",
    "  ok    python  3.11",
    "  warn  git     old",
    "presets",
    "  ready       m5t-echo  starts taking jobs once the service reports",
    "  other pool  far       runs in pool x",
    "probes",
    "  ok  cpu  8",
    "summary  1 ready · 0 needs check · 0 missing · 0 unknown",
    "warn  git: old",
    "exit 1",
]


def test_td24_doctor_compare_passes_when_every_row_agrees(tmp_path: Path) -> None:
    h, j = _doctor_pair(tmp_path, DOC, HUMAN)
    r = sh("doctor_same", h, j, "1", "1")
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize(
    "change",
    [
        ("  warn  git     old", "  ok    git     old"),
        ("  ready       m5t-echo  starts taking jobs once the service reports", ""),
        ("  other pool  far       runs in pool x", "  ready       far       x"),
        ("PATH     /usr/bin:/bin:/usr/sbin", "PATH     unknown"),
        ("  ok  cpu  8", "  fail  cpu  8"),
        ("exit 1", "exit 0"),
    ],
)
def test_td24_doctor_compare_catches_a_row_that_differs(
    tmp_path: Path, change: tuple[str, str]
) -> None:
    human = [change[1] if line == change[0] else line for line in HUMAN]
    h, j = _doctor_pair(tmp_path, DOC, human)
    r = sh("doctor_same", h, j, "1", "1")
    assert r.returncode == 1, change
    assert r.stdout.strip()


def test_td24_doctor_compare_needs_the_measured_path_and_matching_exits(tmp_path: Path) -> None:
    doc = {**DOC, "service": {"path": None}}
    human = [("PATH     unknown" if x.startswith("PATH") else x) for x in HUMAN]
    h, j = _doctor_pair(tmp_path, doc, human)
    assert sh("doctor_same", h, j, "1", "1").returncode == 1  # 측정 PATH 가 없다
    h, j = _doctor_pair(tmp_path, DOC, HUMAN)
    assert sh("doctor_same", h, j, "0", "1").returncode == 1  # 사람용 실행의 종료 코드가 다르다


def _verdict(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout.strip().split(" ", 1)[0]


def test_tr16_production_refusal_verdict(tmp_path: Path) -> None:
    err = tmp_path / "err.txt"
    err.write_text(
        "rcm setup: --config /h/.config/rcm/server.toml: this is com.x's config — refusing "
        "(nothing was written)\n",
        encoding="utf-8",
    )
    assert _verdict(sh("d352_verdict", "1", str(err), "100", "100", "0")) == "PASS"
    assert _verdict(sh("d352_verdict", "0", str(err), "100", "100", "0")) == "FAIL"
    assert _verdict(sh("d352_verdict", "1", str(err), "100", "101", "0")) == "FAIL"
    assert _verdict(sh("d352_verdict", "1", str(err), "100", "100", "1")) == "FAIL"
    err.write_text("server.toml exists — pass --force config to replace it\n", encoding="utf-8")
    assert _verdict(sh("d352_verdict", "1", str(err), "100", "100", "0")) == "FAIL"


def test_tn8_notify_verdict_never_claims_the_banner(tmp_path: Path) -> None:
    err, log = tmp_path / "err.txt", tmp_path / "osascript.log"
    err.write_text("#4 succeeded on w1 · 1s\n", encoding="utf-8")
    log.write_text("exit 0\n", encoding="utf-8")
    r = sh("tn8_verdict", "0", str(err), str(log))
    assert _verdict(r) == "UNKNOWN", r.stdout
    assert "banner" in r.stdout
    err.write_text("#4 succeeded\nwarning: --notify: osascript exited 1\n", encoding="utf-8")
    log.write_text("exit 1\n", encoding="utf-8")
    assert _verdict(sh("tn8_verdict", "0", str(err), str(log))) == "FAIL"
    err.write_text("#4 succeeded\n", encoding="utf-8")
    log.write_text("", encoding="utf-8")  # osascript 가 불리지 않았다
    assert _verdict(sh("tn8_verdict", "0", str(err), str(log))) == "FAIL"
    log.write_text("exit 0\n", encoding="utf-8")
    assert _verdict(sh("tn8_verdict", "1", str(err), str(log))) == "FAIL"


# ── PR #276 P3 뒤: 판정 줄 · 빈 절 · 운영 격리 자체 · 토큰 argv ─────────────────────────


def sh_code(code: str, *args: str, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """하네스를 source 한 뒤 `code` 를 돈다 — source 가 정하는 전역(RCM 등)을 바꿔 끼울 때."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "M5T_PY": sys.executable,
        "HOME": os.environ.get("HOME", "/"),
        **extra,
    }
    return subprocess.run(
        ["bash", "-c", f'source "$1" && shift && {code}', "bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )


def _fake(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    tool = bin_dir / name
    tool.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    tool.chmod(0o755)


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_td24_pass_text_says_what_was_measured(tmp_path: Path) -> None:
    # P3-1: 서비스 파일이 없으면 「기본 워커 PATH 를 쟀다」고 판정 줄이 말한다
    j = tmp_path / "d.json"
    j.write_text(json.dumps(DOC), encoding="utf-8")
    r = sh("td24_pass_text", str(j))
    assert r.stdout.startswith("PASS (no service file — default worker PATH measured) "), r
    assert "measured PATH /usr/bin:/bin:/usr/sbin" in r.stdout
    service = {"file": "/h/Library/LaunchAgents/w.plist", "path": "/usr/bin:/bin"}
    j.write_text(json.dumps({**DOC, "service": service}), encoding="utf-8")
    r = sh("td24_pass_text", str(j))
    assert r.stdout.startswith("PASS (service file /h/Library/LaunchAgents/w.plist measured) "), r


def _without(section: str) -> list[str]:
    """사람용 표에서 절 하나(제목과 그 아래 들여 쓴 줄)를 뺀다."""
    out, skip = [], False
    for line in HUMAN:
        if line == section:
            skip = True
            continue
        if skip and line.startswith("  "):
            continue
        skip = False
        out.append(line)
    return out


def _drop(key: str) -> dict[str, object]:
    return {k: v for k, v in DOC.items() if k != key}


@pytest.mark.parametrize(
    "doc,human",
    [
        ({**DOC, "rcm_requirements": []}, _without("rcm's own needs")),
        ({**DOC, "presets": []}, [x for x in _without("presets") if not x.startswith("summary")]),
        (_drop("summary"), HUMAN),
        (_drop("rcm_requirements"), _without("rcm's own needs")),
        (_drop("warnings"), HUMAN),
        (DOC, [x.replace("summary  1 ready", "summary  2 ready") for x in HUMAN]),
    ],
    ids=["no-needs", "no-presets", "no-summary-key", "no-needs-key", "no-warnings-key", "summary"],
)
def test_td24_doctor_compare_fails_on_an_empty_or_missing_section(
    tmp_path: Path, doc: dict[str, object], human: list[str]
) -> None:
    # P3-2: doctor 가 늘 내는 절이 비거나 없으면 견줄 것이 없는 것이지 일치가 아니다
    h, j = _doctor_pair(tmp_path, doc, human)
    r = sh("doctor_same", h, j, "1", "1")
    assert r.returncode == 1, r.stdout
    assert r.stdout.strip()


def test_td24_doctor_keys_are_the_keys_doctor_always_writes() -> None:
    # P3-2: 하네스가 요구하는 키 = doctor 의 document() 가 늘 내는 키(진짜 생산자에서 읽는다)
    src = (ROOT / "src" / "remote_ci_monitor" / "doctor.py").read_text(encoding="utf-8")
    keys: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            names = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if "doctor_version" in names and "rcm_requirements" in names:
                keys = [str(n) for n in names]
    assert keys, "doctor.py has no document() dict any more"
    m = re.search(r'^DOCTOR_KEYS="([^"]*)"$', SCRIPT.read_text(encoding="utf-8"), re.M)
    assert m, "the harness has no DOCTOR_KEYS line"
    assert m.group(1).split() == keys
    assert set(DOC) == set(keys)  # 이 파일의 모범 문서도 같은 키다


def test_tr16_isolate_env_drops_rcm_and_points_home_and_xdg_into_the_base() -> None:
    # P3-3: 운영 격리 자체 — RCM_* 는 하나도 남지 않고 HOME 과 XDG 넷은 시험 폴더다
    r = sh_code(
        'isolate_env "$1" && env',
        "/b/m5t",
        extra={
            "HOME": "/real",
            "RCM_TOKEN": "x",
            "RCM_SERVER": "http://127.0.0.1:8787",
            "RCM_SERVER_DATA_DIR": "/real/.local/share/rcm",
            "XDG_CONFIG_HOME": "/real/.config",
            "XDG_DATA_HOME": "/real/.local/share",
        },
    )
    assert r.returncode == 0, r.stderr
    env = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    assert sorted(k for k in env if k.startswith("RCM_")) == []
    assert env["HOME"] == "/b/m5t/home"
    assert env["XDG_CONFIG_HOME"] == "/b/m5t/xdg/config"
    assert env["XDG_DATA_HOME"] == "/b/m5t/xdg/data"
    assert env["XDG_STATE_HOME"] == "/b/m5t/xdg/state"
    assert env["XDG_CACHE_HOME"] == "/b/m5t/xdg/cache"


def test_tr16_main_isolates_before_the_first_rcm_command() -> None:
    # P3-3: main 이 격리를 세운 뒤에 첫 실행(run_d352)으로 간다
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("\nmain() {")
    body = text[start : text.index("\n}\n", start)]
    assert 'isolate_env "$B"' in body
    assert body.index('mkdir "$B"') < body.index('isolate_env "$B"') < body.index("run_d352")
    # main 자신은 rcm 을 부르지 않는다(`[ -x "$RCM" ]` 검사만 있다)
    assert not re.search(r'^\s*"\$RCM"\s', body, re.M)


def _test_base(tmp_path: Path) -> Path:
    return tmp_path / "h" / ".local" / "share" / "rcm-devtest" / "m5t-live-ab12cd34"


def _bootout_env(tmp_path: Path, label: str, installed: Path) -> dict[str, str]:
    base = _test_base(tmp_path)
    (base / "out").mkdir(parents=True, exist_ok=True)
    _fake(
        tmp_path / "bin",
        "launchctl",
        'echo "$*" >> "$LAUNCHCTL_LOG"\n[ "$1" = print ] && exit 1\nexit 0\n',
    )
    return {
        "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "LAUNCHCTL_LOG": str(tmp_path / "launchctl.log"),
        "BOOTED": "1",
        "LABEL": label,
        "U": "501",
        "OUT": str(base / "out"),
        "B": str(base),
        "REAL_HOME": str(tmp_path / "h"),
        "INSTALLED": str(installed),
    }


def _calls(tmp_path: Path) -> str:
    log = tmp_path / "launchctl.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_tr16_bootout_checks_the_label_again_and_removes_only_a_test_file(tmp_path: Path) -> None:
    # P3-3: bootout 직전의 라벨 재검사와 rm -f 앞의 시험 경로 검사
    inst = _test_base(tmp_path) / "home" / "Library" / "LaunchAgents" / "x.plist"
    inst.parent.mkdir(parents=True)
    inst.write_text("x", encoding="utf-8")
    env = _bootout_env(tmp_path, "com.remote-ci-monitor.server", inst)
    assert sh("bootout_test_label", extra=env).returncode == 0
    assert "bootout" not in _calls(tmp_path)  # 운영 라벨은 내리지 않는다
    assert inst.exists()

    label = "com.example.rcm-m5t-live-ab12cd34"
    assert sh("bootout_test_label", extra=_bootout_env(tmp_path, label, inst)).returncode == 0
    assert f"bootout gui/501/{label}" in _calls(tmp_path)
    assert not inst.exists()

    real = tmp_path / "h" / "Library" / "LaunchAgents" / f"{label}.plist"
    real.parent.mkdir(parents=True)
    real.write_text("x", encoding="utf-8")
    assert sh("bootout_test_label", extra=_bootout_env(tmp_path, label, real)).returncode == 0
    assert real.exists()  # 시험 폴더 밖의 파일은 지우지 않는다


def _d352_env(tmp_path: Path, prod: str) -> dict[str, str]:
    base = _test_base(tmp_path)
    (base / "out").mkdir(parents=True, exist_ok=True)
    _fake(
        tmp_path / "bin",
        "rcm",
        'echo "$*" >> "$RCM_LOG"\n'
        'echo "this is com.x\'s config — refusing (nothing was written)" >&2\n'
        "exit 1\n",
    )
    return {
        "RCM_LOG": str(tmp_path / "rcm.log"),
        "PROD_CONFIG": prod,
        "OUT": str(base / "out"),
        "B": str(base),
        "REAL_HOME": str(tmp_path / "h"),
        "P1": "8794",
        "LABEL": "com.example.rcm-m5t-live-ab12cd34",
        "FAILS": "0",
    }


D352_CODE = 'RCM=$1 && run_d352 && cat "$OUT/summary.txt"'


def test_tr16_d352_runs_only_on_an_existing_file_outside_the_test_folder(tmp_path: Path) -> None:
    # P3-3: run_d352 의 가드 — 운영 경로가 없거나 이상하면 시험 빌드를 부르지 않는다
    fake = str(tmp_path / "bin" / "rcm")
    inside = _test_base(tmp_path) / "server.toml"
    inside.parent.mkdir(parents=True)
    inside.write_text("", encoding="utf-8")
    odd = ["", "relative/server.toml", str(tmp_path), str(tmp_path / "none.toml"), str(inside)]
    for prod in odd:
        r = sh_code(D352_CODE, fake, extra=_d352_env(tmp_path, prod))
        assert r.returncode == 0, (prod, r.stderr)
        assert r.stdout.splitlines()[-1].startswith("D352  UNKNOWN"), (prod, r.stdout)
        assert not (tmp_path / "rcm.log").exists(), prod
    prod = tmp_path / "prod" / "server.toml"
    prod.parent.mkdir()
    prod.write_text("", encoding="utf-8")
    r = sh_code(D352_CODE, fake, extra=_d352_env(tmp_path, str(prod)))
    assert r.stdout.splitlines()[-1].startswith("D352  PASS"), r.stdout + r.stderr
    assert f"--config {prod}" in (tmp_path / "rcm.log").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "over,says",
    [
        ({"M5T_PORT": "8787"}, "not a test port"),
        ({"M5T_PORT": "8897", "M5T_PORT2": "8788"}, "not a test port"),
        (
            {"M5T_PORT": "8897", "M5T_PORT2": "8898", "M5T_BASE": "{home}/.local/share/rcm"},
            "is not",
        ),
        ({"M5T_PORT": "8897", "M5T_PORT2": "8898", "M5T_BASE": "{base}"}, "exists"),
    ],
    ids=["8787", "8788", "production-data-dir", "existing-base"],
)
def test_tr16_main_stops_before_anything_on_a_production_port_or_folder(
    place: dict[str, Path], over: dict[str, str], says: str
) -> None:
    # P3-3: main 의 포트 · 폴더 검사 — 종료 2, 아무것도 만들지 않는다
    home, base = place["home"], place["base"]
    before = _tree(home)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "M5T_PY": sys.executable,
        **{k: v.format(home=home, base=base) for k, v in over.items()},
    }
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=15, check=False
    )
    if "is in use" in r.stderr:
        pytest.skip(r.stderr)
    assert r.returncode == 2, r.stderr
    assert says in r.stderr
    assert _tree(home) == before


def test_tr16_main_refuses_a_new_base_under_tmp() -> None:
    # 없는 폴더라도 /tmp 아래면 거절한다 — 「이미 있다」를 먼저 보게 바꾼 뒤에도 이 거절이 남는다.
    # 가짜 홈을 직접 /tmp 아래에 두므로 pytest 임시 폴더가 어디든 같은 길을 탄다
    home = Path(tempfile.mkdtemp(prefix="m5t-live-tmpcheck-", dir="/tmp"))
    try:
        base = home / ".local" / "share" / "rcm-devtest" / "m5t-live-ab12cd34"
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "M5T_PY": sys.executable,
            "M5T_PORT": "8897",
            "M5T_PORT2": "8898",
            "M5T_BASE": str(base),
        }
        r = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=15, check=False
        )
        if "is in use" in r.stderr:
            pytest.skip(r.stderr)
        assert r.returncode == 2, r.stderr
        assert "is under /tmp" in r.stderr
        assert not base.exists()
        assert _tree(home) == []
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_tr16_job_lookup_keeps_the_token_out_of_argv(tmp_path: Path) -> None:
    # P3-7: 토큰은 curl 의 argv 가 아니라 600 헤더 파일로 간다(ps 에 보이지 않는다)
    tok = tmp_path / "client.token"
    tok.write_text("tok-not-in-argv\n", encoding="utf-8")
    tok.chmod(0o600)
    _fake(
        tmp_path / "bin",
        "curl",
        'for a in "$@"; do echo "$a" >> "$CURL_LOG"; done\n'
        'echo \'{"state": "succeeded", "worker": "w1"}\'\n',
    )
    extra = {
        "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "CURL_LOG": str(tmp_path / "curl.log"),
    }
    r = sh("jf", "http://127.0.0.1:8795", str(tok), "1", "state", "worker", extra=extra)
    assert r.stdout.strip() == "succeeded w1", r.stderr
    argv = (tmp_path / "curl.log").read_text(encoding="utf-8").splitlines()
    assert not [a for a in argv if "tok-not-in-argv" in a], argv
    header = [a for a in argv if a.startswith("@")]
    assert len(header) == 1, argv
    hdr = Path(header[0][1:])
    assert hdr.read_text(encoding="utf-8") == "Authorization: Bearer tok-not-in-argv\n"
    assert hdr.stat().st_mode & 0o777 == 0o600
