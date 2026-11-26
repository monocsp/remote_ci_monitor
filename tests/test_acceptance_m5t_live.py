"""M5t-a 실기 하네스 `scripts/acceptance/m5t_live.sh` 의 순수 부분(명세 §7 · TR16 · TD24 · TN8).

하네스 자체는 운영 머신에서 진짜 launchd · 서버 · 워커를 띄우므로 pytest 가 부르지 않는다. 여기서는
그 하네스를 `source` 해서 검사 함수만 bash 로 부른다 — 시험 값이 아닌 것(운영 라벨 · 8787 · 운영
경로)을 bootstrap 전에 거절하는지, doctor 의 사람용 표와 `--json` 을 견주는 함수가 어긋남을 잡는지,
판정 함수가 모르는 것을 PASS 로 적지 않는지다. 프로세스를 띄우지 않고 네트워크도 쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "m5t_live.sh"


def sh(fn: str, *args: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
    """하네스를 source 하고 함수 하나를 부른다. 파이썬은 이 시험의 해석기(`M5T_PY`)다."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "M5T_PY": sys.executable,
        "HOME": str(home) if home is not None else os.environ.get("HOME", "/"),
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
    "role": "worker",
    "service": {"path": "/usr/bin:/bin:/usr/sbin"},
    "rcm_requirements": [
        {"name": "python", "state": "ok", "detail": "3.11"},
        {"name": "git", "state": "warn", "detail": "old"},
    ],
    "presets": [
        {"name": "m5t-echo", "state": "ready", "applies": "pool"},
        {"name": "far", "state": None, "applies": "other_pool"},
    ],
    "probes": {"cpu": {"state": "ok", "value": "8"}},
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
