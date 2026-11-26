"""온보딩 시험이 찾은 것(OB1~OB8) — 운영 rcm 이 도는 머신에서 README 의 시험 설치 길을 따라 한
격리 에이전트 둘이 똑같이 걸린 곳이다(2026-11-26).

OB1 · OB2 · OB3 은 출력이다. 시험 설치의 setup · doctor 가 운영 설치의 `worker.toml` 과 서비스를
제 것처럼 말하지 않고, setup 이 찍는 명령에 셸 치환(`$(id -u)`)이 없다. OB4~OB8 은 사용 안내 두
거울의 문면이다.

운영 보호: 모든 시험이 임시 HOME · XDG 와 가짜 LaunchAgents 폴더를 쓴다. 진짜 `launchctl` 은
부르지 않고, 운영 설정(`~/.config/rcm`)과 운영 데이터는 입력으로 쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__, cli, doctor, setup
from remote_ci_monitor.core import doctor as doctor_core
from test_docs_m5t import USAGE, USAGE_KO, flat, getting_started, trial_install
from test_setup import Fake, coordinator_args, go

USAGE_GUIDES = [pytest.param(USAGE, id="en"), pytest.param(USAGE_KO, id="ko")]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """임시 HOME · XDG, 셸의 `RCM_*` 없음, 작업 폴더도 임시 폴더. setup 의 스캔이 진짜 시스템
    자리와 passwd 의 홈을 읽지 않게 바꿔 끼운다(tests/test_setup.py 의 `no_system_places` 와
    같다 — CI 맥미니에는 rcm 서비스가 있다)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    for name in (
        "RCM_CONFIG",
        "RCM_SERVER",
        "RCM_TOKEN",
        "RCM_WORKER_TOKEN",
        "RCM_SERVER_DATA_DIR",
        "RCM_WORKER_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    places = tuple(tmp_path / "sys" / name for name in ("LaunchAgents", "LaunchDaemons", "system"))
    monkeypatch.setattr(setup, "SYSTEM_SERVICE_PLACES", places)
    monkeypatch.setattr(setup, "account_home", lambda: tmp_path / "account-home")
    monkeypatch.setattr(doctor, "SYSTEM_UNIT_DIR", tmp_path / "sys" / "system-units")
    monkeypatch.setattr(doctor, "LAUNCH_DAEMONS_DIR", tmp_path / "sys" / "launch-daemons")
    monkeypatch.setattr(doctor, "is_tty", lambda: False)
    return h


@pytest.fixture
def cfg(home: Path, tmp_path: Path) -> Path:
    """기본 설정 폴더(`user_config_dir()`) — 운영 설치가 쓰는 자리의 가짜다."""
    return tmp_path / "xdg-config" / "rcm"


# ── OB1: setup 의 worker.toml 경고 ─────────────────────────────────────────────

#: 같은 폴더의 worker.toml 일 때의 경고(그대로 둔다)
SAME_PLACE_WORKER = "this machine already has worker.toml"


def _warnings(fake: Fake) -> list[str]:
    return [ln for ln in fake.err.getvalue().splitlines() if ln.startswith("warning: ")]


def test_ob1_a_worker_toml_outside_this_configs_folder_is_another_installs(cfg, tmp_path):
    """# OB1 — 시험 설치(`--config` 가 기본 자리 밖)가 기본 자리의 worker.toml 을 「이 머신에 이미
    있다」고 제 설치의 것처럼 말했다. 그 파일은 운영 설치의 것이다: 경로를 대고 이 설정과 별개라고
    말한다. 둘이 다 돌면 한 호스트에 레인이 두 벌이라는 정보는 남긴다."""
    cfg.mkdir(parents=True)
    worker = cfg / "worker.toml"
    worker.write_text('server = "http://192.0.2.10:8787"\n', encoding="utf-8")
    trial = tmp_path / "rcm-test" / "server.toml"
    fake = Fake()
    argv = coordinator_args(tmp_path / "rcm-test" / "data", "--config", str(trial))
    assert go(fake, *argv) == 0, fake.text
    said = [w for w in _warnings(fake) if "worker.toml" in w]
    assert len(said) == 1, _warnings(fake)
    assert SAME_PLACE_WORKER not in said[0], said[0]
    assert f"another rcm install's worker.toml is at {worker}" in said[0], said[0]
    assert f"separate from this config ({trial})" in said[0], said[0]
    assert "two sets of lanes" in said[0], said[0]


def test_ob1_a_worker_toml_next_to_this_config_keeps_the_two_lanes_warning(cfg, tmp_path):
    """# OB1 — 같은 설정 폴더의 worker.toml 이면 이 설치의 워커다: 지금 경고 그대로."""
    cfg.mkdir(parents=True)
    (cfg / "worker.toml").write_text('server = "http://192.0.2.10:8787"\n', encoding="utf-8")
    for extra in ([], ["--force", "--config", str(cfg / "server.toml")]):
        fake = Fake()
        assert go(fake, *coordinator_args(tmp_path / "data", *extra)) == 0, fake.text
        said = [w for w in _warnings(fake) if "worker.toml" in w]
        assert len(said) == 1 and SAME_PLACE_WORKER in said[0], (extra, _warnings(fake))
        assert "another rcm install" not in said[0], said[0]


# ── OB2: doctor 의 서비스 줄 ───────────────────────────────────────────────────

PROD_LABEL = "com.remote-ci-monitor.server"


def _server_toml(path: Path, port: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[server]\nbind = "127.0.0.1"\nport = {port}\ndata_dir = "{path.parent / "data"}"\n\n'
        '[[presets]]\nname = "unit"\nargv = ["true"]\nrequires = ["sh"]\n'
        'env_passthrough = ["PATH"]\n',
        encoding="utf-8",
    )
    return path


def _server_plist(home: Path, config: Path) -> Path:
    """표준 라벨의 설치 자리에 있는 서버 서비스 — `serve --config <config>` 로 rcm 을 띄운다."""
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    p = agents / f"{PROD_LABEL}.plist"
    p.write_bytes(
        plistlib.dumps(
            {
                "Label": PROD_LABEL,
                "ProgramArguments": ["/x/rcm", "serve", "--config", str(config)],
                "EnvironmentVariables": {"PATH": "/usr/bin:/bin"},
            }
        )
    )
    return p


@pytest.fixture
def trial_server(home: Path, monkeypatch) -> Iterator[FakeRcm]:
    """시험 서버(가짜 HTTP). 클라이언트 토큰을 셸에 둔다."""
    s = FakeRcm()
    digest = doctor_core.preset_env_digest(("sh",), ("PATH",), {})
    health = {
        "ok": True,
        "version": __version__,
        "preset_env": {"unit": {"missing_tools": [], "missing_passthrough": [], "digest": digest}},
    }
    s.route("GET", "/api/health", health)
    s.route("GET", "/api/whoami", {"name": "tester", "admin": False, "kind": "client"})
    s.route("GET", "/api/machines", {"generation": "1:0", "machines": []})
    monkeypatch.setenv("RCM_TOKEN", "rcm-ob-client-token")
    yield s
    s.close()


def _doctor(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(["doctor", *argv])
    return code, capsys.readouterr().out


def test_ob2_doctor_says_the_service_it_found_runs_another_config(
    home, trial_server, tmp_path, capsys
):
    """# OB2 — `rcm doctor --config <시험>/server.toml` 이 운영 서비스를 찾아 `service …` 줄에 찍고
    「the service starts rcm with --config …, not …」 만 말했다. 사용자는 그 서비스가 자기 것인지
    알 수 없었다. 이 설정을 쓰는 서비스가 없다는 것, 찾은 서비스(라벨)는 다른 설치의 설정을
    쓴다는 것, 이 설정은 준 파일로 쟀다는 것을 한 줄로 말한다. 판정과 `--json` 의 키는 그대로다."""
    prod_config = home / ".config" / "rcm" / "server.toml"
    _server_plist(home, prod_config)
    trial = _server_toml(tmp_path / "rcm-test" / "server.toml", trial_server.port)
    argv = ("--role", "coordinator-worker", "--config", str(trial))
    code, out = _doctor(capsys, *argv)
    warn = [ln for ln in out.splitlines() if ln.startswith("warn  ") and "--config" in ln]
    assert len(warn) == 1, out
    line = warn[0]
    assert f"no service starts rcm with --config {trial}" in line, line
    assert f"{PROD_LABEL} (~/Library/LaunchAgents/{PROD_LABEL}.plist)" in line, line
    assert "--config ~/.config/rcm/server.toml" in line and "another install's config" in line
    assert f"{trial} was measured as given" in line, line
    # --json: 키는 그대로고 그 말은 warnings 에만 있다(판정을 바꾸지 않는다)
    code_json, raw = _doctor(capsys, *argv, "--json")
    doc = json.loads(raw)
    assert code_json == code == doc["exit"], (code, code_json, doc["exit"])
    assert set(doc["service"]) == {"file", "manager", "path", "env_names"}, doc["service"]
    assert sum(line.removeprefix("warn  ") == w for w in doc["warnings"]) == 1, doc["warnings"]
    assert not any("no service starts" in t for t in doc["problems"] + doc["undecided"]), doc


def test_ob2_no_such_line_when_the_service_runs_this_config(home, trial_server, tmp_path, capsys):
    """# OB2 — 찾은 서비스가 준 설정으로 뜨면 그 줄은 없다(남의 서비스라고 하지 않는다)."""
    trial = _server_toml(tmp_path / "rcm-test" / "server.toml", trial_server.port)
    _server_plist(home, trial)
    _code, out = _doctor(capsys, "--role", "coordinator-worker", "--config", str(trial))
    assert "no service starts rcm" not in out and "another install" not in out, out


TRIAL_LABEL = "test.rcm.server"  # coordinator_args 의 --label


def _trial_install(tmp_path: Path, port: int) -> tuple[Path, Path]:
    """진짜 `rcm setup` 이 시험 설치를 만든다(`<시험>/service/<라벨>.plist` 를 그 규칙대로 둔다).
    설정은 가짜 시험 서버의 포트로 다시 쓴다 — 서비스 파일은 setup 이 쓴 그대로다."""
    trial = tmp_path / "rcm-test" / "server.toml"
    fake = Fake()
    argv = coordinator_args(tmp_path / "rcm-test" / "data", "--config", str(trial))
    assert go(fake, *argv) == 0, fake.text
    staged = trial.parent / "service" / f"{TRIAL_LABEL}.plist"
    assert staged.is_file(), fake.text
    _server_toml(trial, port)
    return trial, staged


def _staged_path(staged: Path) -> str:
    return plistlib.loads(staged.read_bytes())["EnvironmentVariables"]["PATH"]


def test_ob2_doctor_measures_the_service_file_setup_put_next_to_this_config(
    home, trial_server, tmp_path, capsys
):
    """# OB2 — 근본 원인: doctor 는 setup 이 둔 서비스 파일을 기본 설정 폴더 아래에서만 찾아,
    시험 설치의 `<시험>/service/<라벨>.plist` 를 못 보고 도는 설치의 서비스로 떨어졌다. `--config`
    를 주면 그 폴더 아래 `service/` 에서 setup 의 파일을 먼저 찾고 그 파일로 잰다 — 서비스 줄 ·
    PATH · `--config` 일치 판정 모두."""
    trial, staged = _trial_install(tmp_path, trial_server.port)
    # 도는 설치의 서비스(다른 설정). setup 의 운영 스캔이 거절하지 않게 시험 설치 뒤에 둔다
    _server_plist(home, home / ".config" / "rcm" / "server.toml")
    argv = ("--role", "coordinator-worker", "--config", str(trial))
    _code, out = _doctor(capsys, *argv)
    assert "--config" not in "\n".join(ln for ln in out.splitlines() if ln.startswith("warn  "))
    assert "no service starts rcm" not in out and PROD_LABEL not in out, out
    _code, raw = _doctor(capsys, *argv, "--json")
    doc = json.loads(raw)
    assert doc["service"]["file"] == str(staged), doc["service"]
    assert doc["service"]["path"] == _staged_path(staged), doc["service"]
    assert set(doc["service"]) == {"file", "manager", "path", "env_names"}, doc["service"]


def test_ob2_doctor_prefers_the_installed_copy_of_this_configs_service_file(
    home, trial_server, tmp_path, capsys
):
    """# OB2 — setup 이 찍은 복사 명령으로 설치한 뒤면(같은 내용) 서비스 관리자가 읽는 설치 자리의
    그 파일을 잰다(6-B-V2) — 도는 설치의 표준 라벨 서비스가 아니다."""
    trial, staged = _trial_install(tmp_path, trial_server.port)
    _server_plist(home, home / ".config" / "rcm" / "server.toml")
    installed = home / "Library" / "LaunchAgents" / f"{TRIAL_LABEL}.plist"
    installed.write_bytes(staged.read_bytes())
    _code, raw = _doctor(capsys, "--role", "coordinator-worker", "--config", str(trial), "--json")
    doc = json.loads(raw)
    assert doc["service"]["file"] == f"~/Library/LaunchAgents/{TRIAL_LABEL}.plist", doc["service"]
    assert not any("--config" in w for w in doc["warnings"]), doc["warnings"]


# ── OB3: setup 이 찍는 명령의 uid ──────────────────────────────────────────────


def _err_lines(fake: Fake) -> list[str]:
    return [line.strip() for line in fake.err.getvalue().splitlines()]


def test_ob3_setup_prints_the_numeric_uid_not_a_command_substitution(cfg, home, tmp_path):
    """# OB3 — `next —` · 바꿔 끼우는 순서 · `to remove:` 줄이 `gui/$(id -u)` 였다. 사람이 복사해
    붙이는 줄이고, 이 머신의 안전 훅은 `$(` 를 담은 rcm 근처 명령을 막는다. 숫자 uid 를 그대로
    찍는다."""
    fake = Fake(loaded=True)  # 이미 올라간 라벨 — 바꿔 끼우는 순서도 찍는다
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0, fake.text
    err = fake.err.getvalue()
    assert "$(" not in err, err
    uid = os.getuid()
    installed = home / "Library" / "LaunchAgents" / "test.rcm.server.plist"
    lines = _err_lines(fake)
    start = next(ln for ln in lines if ln.startswith("cmp -s ") and "bootstrap" in ln)
    assert f"&& launchctl bootstrap gui/{uid} {installed} ||" in start, start
    assert f"launchctl bootout gui/{uid}/test.rcm.server" in lines, lines
    assert f"launchctl bootstrap gui/{uid} {installed}" in lines, lines
    remove = next(ln for ln in lines if ln.startswith("to remove: "))
    assert f"launchctl bootout gui/{uid}/test.rcm.server;" in remove, remove


def test_ob3_the_linux_lines_hold_no_command_substitution(cfg, home, tmp_path):
    """# OB3 — systemd 줄에도 `$(` 가 없다(고치기 전에도 없었다 — 같은 꼴이 생기지 않게)."""
    argv = coordinator_args(tmp_path / "data")
    assert go(Fake(platform="linux"), *argv) == 0
    fake = Fake(platform="linux")
    assert go(fake, *argv, "--force") == 0, fake.text
    assert "$(" not in fake.err.getvalue(), fake.err.getvalue()


def test_ob3_core_commands_take_the_uid_they_are_given():
    """# OB3 — 순수 함수는 받은 uid 를 쓴다(시험 · 다른 계정의 안내)."""
    import inspect

    from remote_ci_monitor.core import setup as core

    for fn in (core.install_commands, core.replace_commands, core.remove_line):
        assert "uid" in inspect.signature(fn).parameters, fn.__name__
    staged, installed = Path("/c/service/s.plist"), Path("/h/Library/LaunchAgents/s.plist")
    kw: dict[str, Any] = {"staged": staged, "installed": installed, "label": "s"}
    start = core.install_commands(core.LAUNCHD, log=Path("/h/s.log"), uid=501, **kw)[-1]
    assert f"launchctl bootstrap gui/501 {installed}" in start, start
    swap = core.replace_commands(core.LAUNCHD, uid=501, **kw)
    assert swap[0] == "launchctl bootout gui/501/s" and swap[-1].endswith(f"gui/501 {installed}")
    remove = core.remove_line(core.LAUNCHD, files=[staged], uid=501, **kw)
    assert "launchctl bootout gui/501/s;" in remove, remove


# ── OB4~OB8: 사용 안내 두 거울 ────────────────────────────────────────────────


def lang(path: Path) -> str:
    return "ko" if path.name.endswith(".ko.md") else "en"


def paragraphs(sec: str) -> list[str]:
    return [flat(p) for p in re.split(r"\n\s*\n", sec) if p.strip()]


def trial_code(path: Path) -> list[str]:
    sec = trial_install(path)
    return "\n".join(re.findall(r"```sh\n(.*?)```", sec, re.S)).splitlines()


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ob4_test_install_says_what_shows_the_running_install(path: Path):
    """# OB4 — 시험 설치에서도 `rcm version` · `rcm doctor` · `rcm setup` 은 도는 설치의 기본
    설정 · 서비스 · 캐시를 보여 줄 수 있다. 시험 클라이언트를 시험 서버에 묶는 것은 `RCM_SERVER`
    · `RCM_TOKEN`(또는 `--server`)이다 — 한 단락에."""
    para = next((p for p in paragraphs(trial_install(path)) if "`rcm version`" in p), "")
    for word in ("`rcm doctor`", "`rcm setup`", "`RCM_SERVER`", "`RCM_TOKEN`", "`--server`"):
        assert word in para, f"{path.name}: the rcm version paragraph lacks {word}: {para!r}"
    what = {
        "en": r"running install's default config, service (and|or) cache",
        "ko": r"도는 설치의 기본 설정 · 서비스 · 캐시",
    }[lang(path)]
    assert re.search(what, para), f"{path.name}: {para!r}"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ob5_test_install_runs_the_job_from_a_small_empty_folder(path: Path):
    """# OB5 — `rcm run` 은 지금 폴더를 올린다. 시험 설치 블록은 작은 빈 폴더를 만들고 거기서
    (또는 `--dir`) 돌린다 — 홈 폴더에서 치면 홈을 통째로 올린다."""
    code = trial_code(path)
    run = next(i for i, ln in enumerate(code) if re.search(r"\brcm run ok\b", ln))
    made = [m.group(1) for ln in code[:run] if (m := re.match(r"mkdir -p (\S+)", ln))]
    assert made, f"{path.name}: no mkdir -p before rcm run: {code}"
    there = any(re.search(rf"\bcd {re.escape(d)}(\s|$)", ln) for ln in code[:run] for d in made)
    assert there or any(f"--dir {d}" in code[run] for d in made), (path.name, code)
    said = {"en": r"`rcm run` uploads the current folder", "ko": r"`rcm run` 은 지금 폴더를 올린다"}
    assert re.search(said[lang(path)], flat(trial_install(path))), path.name


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ob6_run_and_wait_print_different_end_lines(path: Path):
    """# OB6 — `rcm run` 이 기다릴 때는 짧은 줄(`#1 succeeded`)과 JSON 을 찍고, `on <머신> ·
    <시간>` 끝 줄은 `rcm wait` 가 찍는다(cli `_wait(end_summary=…)` 는 wait 만 켠다)."""
    sents = re.split(r"(?<=\.)\s+", flat(getting_started(path)))
    end = [s for s in sents if "`#1 succeeded on macmini · 1s`" in s]
    assert end and all("`rcm wait`" in s for s in end), (path.name, end)
    short = [s for s in sents if "`#1 succeeded`" in s]
    assert short and all("`rcm run" in s and "JSON" in s for s in short), (path.name, short)


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ob7_notify_prints_nothing_and_the_ok_job_is_too_quick_to_see(path: Path):
    """# OB7 — `--notify` 는 성공하면 아무것도 찍지 않고 OS 알림만 뜬다(실패면 경고 한 줄,
    cli.os_notify). `ok` 잡은 1초 안에 끝나므로 `--in 30s` 로 늦춘 잡을 기다려 본다."""
    sec = flat(getting_started(path))
    said = {
        "en": [r"`--notify` prints nothing", r"one warning line", r"rcm run ok --in 30s"],
        "ko": [r"`--notify` 는 [^.]*아무것도 찍지 않", r"경고 한 줄", r"rcm run ok --in 30s"],
    }
    for pattern in said[lang(path)]:
        assert re.search(pattern, sec), f"{path.name} lacks /{pattern}/"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ob8_shell_variables_last_only_in_one_terminal(path: Path):
    """# OB8 — `export RCM_TOKEN` · `unset RCM_TOKEN` 은 그 셸 안에서만 남는다. 블록의 명령은 같은
    터미널에서 이어 친다(`rcm serve` 만 따로 연 터미널)."""
    sec = flat(trial_install(path))
    said = {
        "en": r"`export RCM_TOKEN`[^.]*only in the shell[^.]*same terminal",
        "ko": r"`export RCM_TOKEN`[^.]*그 셸 안에서만[^.]*같은 터미널",
    }
    assert re.search(said[lang(path)], sec), f"{path.name}: no one-terminal line"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ob3_usage_shows_the_numeric_uid_setup_prints(path: Path):
    """# OB3 — 사용 안내가 setup 이 찍는 시작 줄을 `gui/$(id -u)` 로 보이지 않는다."""
    sec = getting_started(path)
    assert "$(id -u)" not in sec, path.name
    assert re.search(r"launchctl bootstrap gui/501 ", flat(sec)), path.name


def test_ob3_operating_setup_section_shows_the_numeric_uid():
    """# OB3 — operating.md 「Set a machine up」 의 출력 예도 숫자 uid 다."""
    from test_docs_m5t import OPERATING, h2, read

    sec = h2(read(OPERATING), "Set a machine up")
    assert "$(id -u)" not in sec
    assert "gui/<uid>" in sec and "gui/501" in sec
