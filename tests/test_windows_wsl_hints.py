"""Windows · WSL 에서 처음 막히는 두 자리의 안내 (dev-lite 카드 WW, 2026-11-26 격리 검사).

- WW1 네이티브 Windows: `submissions.py` 맨 위의 `import fcntl` 때문에 `rcm version` 부터
  모든 명령이 `ModuleNotFoundError` traceback 으로 죽었다. 네이티브 지원은 범위 밖이다
  (PLAN.md 「범위 밖」, README 「Windows is out of scope」). 대신 traceback 없이 WSL 로 가라는
  한 줄과 종료 2(사용 오류)를 낸다.
- WW2 서버를 못 찾았을 때: 안내가 「같은 Wi-Fi?」 만 말했다. 주소를 `--server` 로 주는 길을 같이
  말한다(`rcm discover` · 클라이언트 명령 · 워커 셋 다).
- WW3 WSL 안에서 못 찾았을 때: WSL2 의 기본 NAT 망은 멀티캐스트를 넘기지 않는다. `/proc/version` 에
  `microsoft` 가 있으면 그 말을 더한다. 그 밖의 Linux 에서는 더하지 않는다.

문구는 이 파일이 글자 그대로 잠근다(`tests/test_cli_discover.py` 가 옛 문장을 잠그는 강도와 같다).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor.config import ConfigError

ROOT = Path(__file__).resolve().parents[1]

OLD_NONE_FOUND = "no rcm server found on this network (is the server's advertise on? same Wi-Fi?)"
ADDRESS_HINT = "give its address with --server http://<address>:8787"
WSL_HINT = (
    "under WSL2 the default NAT network does not pass multicast, so use --server "
    "or WSL's mirrored networking"
)
NATIVE_WINDOWS_LINE = (
    "rcm: native Windows is not supported — install and run rcm inside WSL "
    "(see docs/usage.md «Windows (WSL)»)"
)

LINUX_PROC_VERSION = "Linux version 6.8.0-49-generic (buildd@lcy02-amd64-103) #49-Ubuntu SMP\n"
WSL_PROC_VERSION = (
    "Linux version 5.15.167.4-microsoft-standard-WSL2 (root@f9c826d3017f) "
    "(gcc (GCC) 11.2.0) #1 SMP Tue Nov 5 00:21:55 UTC 2024\n"
)


# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """HOME 을 tmp 로, XDG_CONFIG_HOME · RCM_* 은 없이. cwd 도 빈 tmp 로(`./rcm.toml` 방지)."""
    h = tmp_path / "home"
    h.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    return h


@pytest.fixture
def nothing_found(monkeypatch):
    """mDNS 가 아무 서버도 찾지 못한다."""
    monkeypatch.setattr(cli, "discover", lambda *a, **k: [])


def proc_version(monkeypatch, tmp_path: Path, text: str | None) -> None:
    """`/proc/version` 대역. `None` 이면 파일이 없다(macOS)."""
    p = tmp_path / "proc-version"
    if text is None:
        p.unlink(missing_ok=True)
    else:
        p.write_text(text, encoding="utf-8")
    monkeypatch.setattr(cli, "PROC_VERSION", p, raising=False)


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# ── WW1 네이티브 Windows ──────────────────────────────────────────────────────


def test_ww1_native_windows_says_one_line_and_exits_2_without_a_traceback():
    """`sys.platform` 을 win32 로 두고 `fcntl` 을 막은 채 콘솔 스크립트가 하는 import 를 한다.
    변이를 넣은 사본에서도 같은 코드가 돌게 `PYTHONPATH` 로 이 트리의 `src` 를 앞에 둔다.

    표준 모듈 몇은 플랫폼을 바꾸기 **전에** 들인다 — 바꾼 뒤에 들이면 macOS 의 표준 모듈이 win32 용
    내부 모듈을 찾다 먼저 죽는다(`zoneinfo` → `sysconfig`). 그래야 고치기 전의 실패가 Windows 에서
    본 것과 같은 `import fcntl` 의 `ModuleNotFoundError` 다."""
    prog = (
        "import email, http.server, mimetypes, shutil, socket, ssl, subprocess\n"
        "import sysconfig, tempfile, urllib.request, zoneinfo\n"
        "import sys\n"
        "sys.platform = 'win32'\n"
        "sys.modules['fcntl'] = None\n"
        "from remote_ci_monitor.cli import main\n"
        "sys.exit(main(['version']))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("RCM_")}
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [sys.executable, "-c", prog],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stderr == NATIVE_WINDOWS_LINE + "\n", r.stderr
    assert "Traceback" not in r.stderr
    assert r.stdout == "", r.stdout


def test_ww1_the_refusal_line_is_the_code_constant():
    """문서 잠금(`tests/test_docs_windows_wsl.py`)이 읽는 상수와 실제로 찍히는 줄이 같다."""
    assert getattr(cli, "NATIVE_WINDOWS_HINT", None) == NATIVE_WINDOWS_LINE


# ── WW2 주소를 주는 길 ────────────────────────────────────────────────────────


def test_ww2_discover_with_none_found_names_the_server_flag(
    home, nothing_found, monkeypatch, tmp_path, capsys
):
    proc_version(monkeypatch, tmp_path, LINUX_PROC_VERSION)
    code, out, err = run(capsys, ["discover"])
    assert code == 1, (out, err)
    assert out == "", out
    assert err == f"rcm: {OLD_NONE_FOUND} — {ADDRESS_HINT}\n", err


def test_ww2_a_client_command_with_none_found_names_the_server_flag(
    home, nothing_found, monkeypatch, tmp_path, capsys
):
    proc_version(monkeypatch, tmp_path, None)
    code, out, err = run(capsys, ["presets"])
    assert code == 2, (out, err)
    assert f"{cli.NO_SERVER_HINT} — {OLD_NONE_FOUND} — {ADDRESS_HINT}" in err, err
    assert "WSL" not in err, err


def test_ww2_a_worker_with_none_found_names_the_server_flag(nothing_found, monkeypatch, tmp_path):
    proc_version(monkeypatch, tmp_path, LINUX_PROC_VERSION)
    with pytest.raises(ConfigError) as e:
        cli._pick_worker_server(SimpleNamespace(server_name=""))
    assert str(e.value) == f"worker needs a server — {OLD_NONE_FOUND} — {ADDRESS_HINT}"


# ── WW3 WSL 의 NAT 망 ─────────────────────────────────────────────────────────


def test_ww3_discover_inside_wsl_says_the_nat_network_drops_multicast(
    home, nothing_found, monkeypatch, tmp_path, capsys
):
    proc_version(monkeypatch, tmp_path, WSL_PROC_VERSION)
    code, out, err = run(capsys, ["discover"])
    assert code == 1, (out, err)
    assert err == f"rcm: {OLD_NONE_FOUND} — {ADDRESS_HINT} — {WSL_HINT}\n", err


def test_ww3_a_client_command_inside_wsl_says_it_too(
    home, nothing_found, monkeypatch, tmp_path, capsys
):
    proc_version(monkeypatch, tmp_path, WSL_PROC_VERSION.upper())  # 대소문자는 가리지 않는다
    code, out, err = run(capsys, ["presets"])
    assert code == 2, (out, err)
    assert f"{OLD_NONE_FOUND} — {ADDRESS_HINT} — {WSL_HINT}" in err, err


def test_ww3_a_worker_inside_wsl_says_it_too(nothing_found, monkeypatch, tmp_path):
    proc_version(monkeypatch, tmp_path, WSL_PROC_VERSION)
    with pytest.raises(ConfigError) as e:
        cli._pick_worker_server(SimpleNamespace(server_name=""))
    assert str(e.value).endswith(f"{ADDRESS_HINT} — {WSL_HINT}"), str(e.value)


def test_ww3_plain_linux_and_a_missing_proc_version_say_nothing_about_wsl(
    nothing_found, monkeypatch, tmp_path
):
    for text in (LINUX_PROC_VERSION, None, ""):
        proc_version(monkeypatch, tmp_path, text)
        with pytest.raises(ConfigError) as e:
            cli._pick_worker_server(SimpleNamespace(server_name=""))
        assert "WSL" not in str(e.value), (text, str(e.value))
        assert str(e.value).endswith(ADDRESS_HINT), (text, str(e.value))
