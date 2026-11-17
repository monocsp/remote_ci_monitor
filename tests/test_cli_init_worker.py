"""워커 붙이기(M5n 역할 C · 명세 §9) — `rcm init worker` · `--service-file` · 패키지 템플릿 잠금 ·
`rcm token add NAME --worker` 의 다음 줄 안내.

**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.** 오늘은 `init worker` 가 모르는 부명령이라
argparse 가 2 로 끝난다. 그래서 시험마다 종료 코드 말고도 파일·문구를 본다.

- HOME 은 tmp 로 옮긴다. `~/.config/rcm/worker.toml` 은 그 아래에 생긴다(운영 설정을 안 본다).
- 호스트명은 `socket.gethostname` 과 `platform.node` 를 **둘 다** 갈아 끼운다(가정 C14 — 구현이
  어느 쪽을 읽어도 같은 답).
- `--service-file` 은 `sys.platform` 을 갈아 끼워 darwin · linux 를 둘 다 잰다. `launchctl` ·
  `systemctl` 을 부르면 실패다 — `subprocess.Popen` · `os.system` 을 감시한다.
"""

from __future__ import annotations

import configparser
import os
import platform
import plistlib
import re
import socket
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor.config import load_worker_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "worker.toml"
TEMPLATE = ROOT / "src" / "remote_ci_monitor" / "templates" / "worker.toml"
EXAMPLE_PLIST = ROOT / "examples" / "launchd" / "com.remote-ci-monitor.worker.plist"
EXAMPLE_UNIT = ROOT / "examples" / "systemd" / "rcm-worker.service"
SERVICE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LABEL = "com.remote-ci-monitor.worker"


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def parsed(err: str) -> None:
    """argparse 가 명령·플래그를 알아봤다 — 「모르는 부명령이라 2」로 초록이 되지 않게."""
    assert "invalid choice" not in err and "unrecognized arguments" not in err, err


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN", "RCM_WORKER_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "mbp.local")
    monkeypatch.setattr(platform, "node", lambda: "mbp.local")
    return h


def worker_toml(home: Path) -> Path:
    return home / ".config" / "rcm" / "worker.toml"


@pytest.fixture
def no_service_manager(monkeypatch) -> list[list[str]]:
    """`launchctl`·`systemctl` 을 부르는지 감시한다 — 파일만 쓰고 등록은 사람이 한다(§9)."""
    calls: list[list[str]] = []
    real_popen = subprocess.Popen

    class Watch(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, args, *a, **kw):
            argv = [str(x) for x in (args if isinstance(args, list | tuple) else [args])]
            calls.append(argv)
            if any(Path(x).name in ("launchctl", "systemctl") for x in argv):
                raise AssertionError(f"rcm init worker ran a service manager: {argv}")
            super().__init__(args, *a, **kw)

    def system(cmd: str) -> int:
        calls.append([cmd])
        raise AssertionError(f"os.system({cmd!r})")

    monkeypatch.setattr(subprocess, "Popen", Watch)
    monkeypatch.setattr(os, "system", system)
    return calls


# ═══ G. 패키지 템플릿 · 예시 잠금 ════════════════════════════════════════════


def test_the_packaged_worker_template_is_byte_identical_to_the_example():
    assert TEMPLATE.is_file(), f"missing {TEMPLATE.relative_to(ROOT)}"
    assert TEMPLATE.read_bytes() == EXAMPLE.read_bytes()


def test_the_worker_example_defaults_to_discovery():
    text = EXAMPLE.read_text()
    assert len(re.findall(r'^server = "auto"', text, re.M)) == 1, text
    assert tomllib.loads(text)["server"] == "auto"


def test_the_worker_example_documents_server_name_and_accept_any():
    text = EXAMPLE.read_text()
    assert re.search(r"^#? ?server_name\s*=", text, re.M), text
    assert re.search(r"^#? ?accept_any\s*=", text, re.M), text


def test_the_worker_example_loads_and_wants_discovery(monkeypatch):
    monkeypatch.delenv("RCM_WORKER_TOKEN", raising=False)
    cfg = load_worker_config(EXAMPLE)
    assert cfg.wants_discovery is True
    assert cfg.server_name == ""
    assert cfg.accept_any is True


def test_the_worker_example_has_no_token_value():
    assert not re.search(r"^token\s*=", EXAMPLE.read_text(), re.M)


# ═══ H. rcm init worker ══════════════════════════════════════════════════════


def test_init_worker_writes_a_private_worker_toml_with_auto_and_the_short_hostname(home, capsys):
    code, out, err = run(capsys, ["init", "worker"])
    assert code == 0, err
    path = worker_toml(home)
    assert out.strip() == str(path), out
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = tomllib.loads(path.read_text())
    assert data["server"] == "auto"
    assert data["pool"] == "mbp"
    assert data["lanes"] == 1


def test_init_worker_output_is_the_template_with_only_the_chosen_lines_changed(home, capsys):
    code, _, err = run(capsys, ["init", "worker"])
    assert code == 0, err
    written = worker_toml(home).read_text().splitlines()
    template = EXAMPLE.read_text().splitlines()
    assert len(written) == len(template)
    changed = [(a, b) for a, b in zip(template, written, strict=True) if a != b]
    assert all(re.match(r"^(pool|lanes|name|server)\s*=", b) for _, b in changed), changed


def test_init_worker_file_loads_as_a_worker_config(home, capsys):
    run(capsys, ["init", "worker"])
    cfg = load_worker_config(worker_toml(home))
    assert cfg.pool == "mbp" and cfg.lanes == 1 and cfg.wants_discovery is True


def test_init_worker_flags_fill_server_pool_lanes_and_name(home, capsys):
    argv = ["init", "worker", "--server", "http://10.0.0.5:8787", "--pool", "ios"]
    code, _, err = run(capsys, [*argv, "--lanes", "2", "--name", "mbp-office"])
    assert code == 0, err
    data = tomllib.loads(worker_toml(home).read_text())
    assert data["server"] == "http://10.0.0.5:8787"
    assert (data["pool"], data["lanes"], data["name"]) == ("ios", 2, "mbp-office")


def test_init_worker_accepts_server_auto_explicitly(home, capsys):
    code, _, err = run(capsys, ["init", "worker", "--server", "auto"])
    assert code == 0, err
    assert tomllib.loads(worker_toml(home).read_text())["server"] == "auto"


@pytest.mark.parametrize(
    "argv",
    [
        ["--server", "macmini:8787"],
        ["--server", 'http://x"y'],
        ["--pool", "bad pool"],
        ["--lanes", "0"],
        ["--lanes", "65"],
    ],
)
def test_init_worker_rejects_bad_values_before_writing(home, capsys, argv):
    code, _, err = run(capsys, ["init", "worker", *argv])
    parsed(err)
    assert code == 2, err
    assert not worker_toml(home).exists()


def test_init_worker_refuses_to_overwrite_without_force(home, capsys):
    path = worker_toml(home)
    path.parent.mkdir(parents=True)
    path.write_text('server = "http://mine:8787"\n')
    code, _, err = run(capsys, ["init", "worker"])
    parsed(err)
    assert code == 2
    assert "refusing to overwrite" in err and "--force" in err, err
    assert path.read_text() == 'server = "http://mine:8787"\n'


def test_init_worker_force_overwrites(home, capsys):
    path = worker_toml(home)
    path.parent.mkdir(parents=True)
    path.write_text('server = "http://mine:8787"\n')
    code, _, err = run(capsys, ["init", "worker", "--force"])
    assert code == 0, err
    assert tomllib.loads(path.read_text())["server"] == "auto"


def test_init_worker_prints_the_next_step(home, capsys):
    code, _, err = run(capsys, ["init", "worker"])
    assert code == 0
    assert "RCM_WORKER_TOKEN" in err and "rcm worker --check" in err, err


def test_init_worker_falls_back_to_default_pool_when_the_hostname_is_not_a_name(
    home, monkeypatch, capsys
):
    monkeypatch.setattr(socket, "gethostname", lambda: "Bob's MacBook")
    monkeypatch.setattr(platform, "node", lambda: "Bob's MacBook")
    code, _, err = run(capsys, ["init", "worker"])
    assert code == 0, err
    assert tomllib.loads(worker_toml(home).read_text())["pool"] == "default"


def test_init_help_lists_worker(capsys):
    code, out, _ = run(capsys, ["init", "--help"])
    assert code == 0
    assert "worker" in out, out


# ═══ I. --service-file ═══════════════════════════════════════════════════════


def service_file(home: Path, name: str) -> Path:
    return home / "svc" / name


def test_service_file_on_macos_is_a_launchd_plist_with_the_service_path(
    home, monkeypatch, capsys, no_service_manager
):
    monkeypatch.setattr(sys, "platform", "darwin")
    target = service_file(home, f"{LABEL}.plist")
    code, _, err = run(capsys, ["init", "worker", "--service-file", str(target)])
    assert code == 0, err
    plist = plistlib.loads(target.read_bytes())
    assert plist["Label"] == LABEL
    args = plist["ProgramArguments"]
    assert Path(args[0]).is_absolute() and Path(args[0]).name == "rcm", args
    assert Path(args[0]).is_file(), args  # 지금 이 rcm — 없는 경로를 지어내지 않는다
    assert args[1] == "worker", args
    assert args[args.index("--config") + 1] == str(worker_toml(home)), args
    assert plist["EnvironmentVariables"]["PATH"] == SERVICE_PATH
    assert plist["EnvironmentVariables"]["HOME"] == str(home)
    assert plist["RunAtLoad"] is True
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert Path(plist[key]).is_absolute() and plist[key].endswith("worker.log"), plist[key]
    assert "launchctl bootstrap gui/" in err and str(target) in err, err


def test_service_file_path_does_not_start_with_the_venv_bin(home, monkeypatch, capsys):
    """운영 함정(docs/operating.md): PATH 앞의 venv bin 은 프리셋의 python 을 바꿔 친다."""
    monkeypatch.setattr(sys, "platform", "darwin")
    target = service_file(home, f"{LABEL}.plist")
    run(capsys, ["init", "worker", "--service-file", str(target)])
    plist = plistlib.loads(target.read_bytes())
    rcm_dir = str(Path(plist["ProgramArguments"][0]).parent)
    assert not plist["EnvironmentVariables"]["PATH"].startswith(rcm_dir)


def test_service_file_on_linux_is_a_systemd_user_unit(
    home, monkeypatch, capsys, no_service_manager
):
    monkeypatch.setattr(sys, "platform", "linux")
    target = service_file(home, "rcm-worker.service")
    code, _, err = run(capsys, ["init", "worker", "--service-file", str(target)])
    assert code == 0, err
    text = target.read_text()
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read_string(text)
    exec_start = cp["Service"]["ExecStart"].split()
    assert Path(exec_start[0]).is_absolute() and Path(exec_start[0]).name == "rcm", exec_start
    assert exec_start[1] == "worker", exec_start
    assert exec_start[exec_start.index("--config") + 1] == str(worker_toml(home)), exec_start
    assert re.search(rf'^Environment="?PATH={re.escape(SERVICE_PATH)}"?$', text, re.M), text
    assert "systemctl --user enable --now" in err, err


def test_service_file_never_carries_the_worker_token(home, monkeypatch, capsys):
    """토큰은 사람이 옮긴다(§13) — 셸에 있던 비밀이 서비스 파일로 새면 안 된다."""
    secret = "wk_" + "s3cr3t" * 4
    monkeypatch.setenv("RCM_WORKER_TOKEN", secret)
    for plat, name in (("darwin", f"{LABEL}.plist"), ("linux", "rcm-worker.service")):
        monkeypatch.setattr(sys, "platform", plat)
        target = service_file(home, name)
        code, out, err = run(capsys, ["init", "worker", "--service-file", str(target)])
        assert code == 0, err
        assert secret.encode() not in target.read_bytes()
        assert secret not in out + err


def test_service_file_does_not_touch_an_existing_worker_toml(home, monkeypatch, capsys):
    """S1 의 순서 — `init worker` 다음에 `--service-file` 을 따로 친다. 덮어쓰기 거절이 아니다."""
    monkeypatch.setattr(sys, "platform", "darwin")
    path = worker_toml(home)
    path.parent.mkdir(parents=True)
    path.write_text('server = "http://mine:8787"\n')
    target = service_file(home, f"{LABEL}.plist")
    code, _, err = run(capsys, ["init", "worker", "--service-file", str(target)])
    assert code == 0, err
    assert path.read_text() == 'server = "http://mine:8787"\n'
    assert target.is_file()


def test_service_file_refuses_to_overwrite_without_force(home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    target = service_file(home, f"{LABEL}.plist")
    target.parent.mkdir(parents=True)
    target.write_text("mine")
    code, _, err = run(capsys, ["init", "worker", "--service-file", str(target)])
    parsed(err)
    assert code == 2 and "refusing to overwrite" in err, err
    assert target.read_text() == "mine"


def test_service_file_on_another_platform_is_a_usage_error(home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    target = service_file(home, "worker.xml")
    code, _, err = run(capsys, ["init", "worker", "--service-file", str(target)])
    parsed(err)
    assert code == 2, err
    assert "launchd" in err and "systemd" in err, err
    assert not target.exists()


# ── 예시 서비스 파일 ─────────────────────────────────────────────────────────


def test_the_example_worker_plist_runs_rcm_worker_with_the_service_path():
    assert EXAMPLE_PLIST.is_file(), f"missing {EXAMPLE_PLIST.relative_to(ROOT)}"
    plist = plistlib.loads(EXAMPLE_PLIST.read_bytes())
    assert plist["Label"] == LABEL
    args = plist["ProgramArguments"]
    assert args[0].startswith("/") and args[0].endswith("rcm") and args[1] == "worker", args
    assert plist["EnvironmentVariables"]["PATH"] == SERVICE_PATH
    assert "RCM_WORKER_TOKEN" not in plist["EnvironmentVariables"]


def test_the_example_worker_unit_runs_rcm_worker_with_the_service_path():
    assert EXAMPLE_UNIT.is_file(), f"missing {EXAMPLE_UNIT.relative_to(ROOT)}"
    text = EXAMPLE_UNIT.read_text()
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read_string(text)
    exec_start = cp["Service"]["ExecStart"].split()
    assert exec_start[0].startswith("/") and exec_start[1] == "worker", exec_start
    assert re.search(rf'^Environment="?PATH={re.escape(SERVICE_PATH)}"?$', text, re.M), text


# ═══ J. rcm token add --worker 의 다음 줄 ════════════════════════════════════

FOLLOW_UP = "RCM_WORKER_TOKEN=<token above> rcm worker --check"


@pytest.fixture
def server_toml(home, tmp_path) -> Path:
    data = tmp_path / "data"
    cfg = tmp_path / "server.toml"
    cfg.write_text(f'[server]\ndata_dir = "{data}"\n')
    return cfg


def test_token_add_worker_prints_the_worker_side_command_after_the_secret(server_toml, capsys):
    code, out, err = run(capsys, ["token", "--config", str(server_toml), "add", "mbp", "--worker"])
    assert code == 0, err
    lines = out.splitlines()
    assert len(lines) == 1 and lines[0].strip(), out  # stdout 은 여전히 비밀 한 줄뿐
    secret = lines[0].strip()
    assert [ln for ln in err.splitlines() if ln.endswith(FOLLOW_UP)], err
    assert secret not in err, "the secret must be printed once"


def test_token_add_worker_names_the_worker_variable_not_rcm_token(server_toml, capsys):
    """실측(2026-11-16): 머리줄이 「store it as RCM_TOKEN on the worker machine」이라 바로 아래
    `RCM_WORKER_TOKEN=…` 줄과 어긋났다. 워커는 `RCM_TOKEN` 을 안 읽는다."""
    code, _out, err = run(capsys, ["token", "--config", str(server_toml), "add", "mbp", "--worker"])
    assert code == 0, err
    head = err.splitlines()[0]
    assert "RCM_WORKER_TOKEN" in head and "RCM_TOKEN " not in head, head


def test_token_add_for_a_client_has_no_worker_line(server_toml, capsys):
    code, out, err = run(capsys, ["token", "--config", str(server_toml), "add", "alice"])
    assert code == 0, err
    assert "rcm worker --check" not in err and len(out.splitlines()) == 1
