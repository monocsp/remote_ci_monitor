"""`tests/conftest.py` 의 시험 위생 — 역방향 DNS 차단과 20초 상한(2026-11-20 사건 4).

macOS 의 `socket.getfqdn()` 역방향 조회가 변이마다 36초를 썼고, M5q 시험이 진짜 시간 제한(3·5·60초)
을 기다려 CI 가 25분 느려졌다. 그래서 모든 시험에서 역방향 DNS 를 막고(필요하면
`@pytest.mark.allow_reverse_dns`), 표시(`@pytest.mark.slow`) 없이 20초를 넘는 시험은 세션 끝에
실패로 만든다. 상한은 `RCM_TEST_SLOW_SECONDS` 로 바꿀 수 있다 — 아래 시험이 그것으로 0.3초를 쓴다.
"""

import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── 역방향 DNS ───────────────────────────────────────────────────────────────


def test_getfqdn_answers_without_asking_dns():
    started = time.monotonic()
    assert socket.getfqdn("192.0.2.1") == "192.0.2.1"
    assert socket.getfqdn("") == socket.gethostname()
    assert time.monotonic() - started < 0.5


def test_gethostbyaddr_refuses_and_names_the_marker():
    with pytest.raises(socket.herror, match="allow_reverse_dns"):
        socket.gethostbyaddr("192.0.2.1")


@pytest.mark.allow_reverse_dns
def test_the_marker_gives_the_real_lookups_back():
    name, _aliases, addresses = socket.gethostbyaddr("127.0.0.1")
    assert name and "127.0.0.1" in addresses


# ── 20초 상한 (시험 안의 시험) ───────────────────────────────────────────────

SAMPLE = """
import time
import pytest


def test_quick():
    pass


def test_dawdles():
    time.sleep(0.6)


@pytest.mark.slow
def test_marked_slow():
    time.sleep(0.6)
"""


def _project(tmp_path: Path, body: str) -> Path:
    shutil.copy(ROOT / "tests" / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_sample.py").write_text(textwrap.dedent(body))
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    return tmp_path


def _pytest(project: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, RCM_TEST_SLOW_SECONDS="0.3")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_XDIST_WORKER", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_an_unmarked_test_over_the_limit_fails_the_run(tmp_path):
    proc = _pytest(_project(tmp_path, SAMPLE))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "test_sample.py::test_dawdles" in out, out
    assert "test_marked_slow" not in out.split("pytest.mark.slow")[-1], out
    assert "@pytest.mark.slow" in out, out


def test_marked_or_quick_tests_pass(tmp_path):
    body = SAMPLE.replace(
        "def test_dawdles():\n    time.sleep(0.6)", "def test_dawdles():\n    pass"
    )
    proc = _pytest(_project(tmp_path, body))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_failing_run_keeps_its_own_exit_code(tmp_path):
    body = SAMPLE + "\n\ndef test_broken():\n    assert False\n"
    proc = _pytest(_project(tmp_path, body))
    assert proc.returncode == 1, proc.stdout


def test_the_limit_also_holds_under_xdist(tmp_path):
    pytest.importorskip("xdist")
    proc = _pytest(_project(tmp_path, SAMPLE), "-n", "2")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "test_sample.py::test_dawdles" in out, out
