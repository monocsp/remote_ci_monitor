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
def test_the_marker_gives_the_real_lookups_back(monkeypatch):
    """마커가 있으면 막힌 자리가 진짜 함수로 넘어간다. 진짜 조회는 하지 않는다 — macOS 러너에서
    `127.0.0.1` 의 역방향 조회 하나가 35초 걸렸다(이 PR 의 첫 CI)."""
    stubs = socket.gethostbyaddr.__globals__  # conftest 의 전역 — 막는 함수가 여기서 진짜를 찾는다
    calls: list[str] = []
    monkeypatch.setitem(stubs, "_REAL_GETHOSTBYADDR", lambda a: calls.append(a) or ("h", [], [a]))
    monkeypatch.setitem(stubs, "_REAL_GETFQDN", lambda n="": calls.append(n) or "h.example")
    assert socket.gethostbyaddr("192.0.2.1") == ("h", [], ["192.0.2.1"])
    assert socket.getfqdn("192.0.2.1") == "h.example"
    assert calls == ["192.0.2.1", "192.0.2.1"]


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


def _pytest(project: Path, *args: str, slow: str = "0.3") -> subprocess.CompletedProcess:
    """안쪽 pytest. 예산은 `slow` 초다 — 위의 예산 시험은 0.3초를 쓰고, 예산을 보지 않는 시험은
    넉넉히 준다(부하 속 CI 에서 0.2초짜리 가짜 시험이 0.3초를 넘었다, rcm-ci 잡 #3865)."""
    env = dict(os.environ, RCM_TEST_SLOW_SECONDS=slow)
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


# ── 병렬(xdist)에서는 예산이 다르다 ──────────────────────────────────────────
#
# rcm-ci 잡 #3849(`pytest -n auto`, 워커 10)에서 직렬 5초짜리 Chrome 시험이 29.6초로 상한에 걸렸다.
# 직렬 시간을 기준으로 잡은 20초는 병렬 부하에서 맞지 않는다 — 워커가 둘 이상이면 60초.


def _conftest():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rcm_conftest_copy", ROOT / "tests" / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "workers, environ, budget",
    [
        (0, {}, 20.0),  # 직렬
        (1, {}, 20.0),
        (2, {}, 60.0),
        (10, {}, 60.0),
        (10, {"RCM_TEST_SLOW_SECONDS": "5"}, 5.0),  # 명시한 값이 이긴다
        (0, {"RCM_TEST_SLOW_SECONDS": "0.3"}, 0.3),
        (0, {"RCM_TEST_SLOW_SECONDS": ""}, 20.0),
    ],
)
def test_the_budget_follows_the_worker_count(workers, environ, budget):
    assert _conftest().slow_budget(workers, environ) == budget


class _Option:
    def __init__(self, numprocesses):
        self.numprocesses = numprocesses


class _Config:
    def __init__(self, numprocesses):
        self.option = _Option(numprocesses)


@pytest.mark.parametrize(
    "numprocesses, environ, workers",
    [
        (None, {}, 0),  # xdist 가 없거나 -n 을 안 줬다
        (0, {}, 0),
        (4, {}, 4),
        ("4", {}, 4),
        ("auto", {}, os.cpu_count() or 1),
        (None, {"PYTEST_XDIST_WORKER_COUNT": "10"}, 10),  # 워커 프로세스 쪽
    ],
)
def test_the_worker_count_is_read_from_the_option_or_the_worker_env(numprocesses, environ, workers):
    assert _conftest().worker_count(_Config(numprocesses), environ) == workers


# ── Chrome 시험은 한 워커에서 (rcm-ci 잡 #3861) ──────────────────────────────

CHROME_HARNESS = """
import os
import time
from pathlib import Path


class Chrome:  # 진짜 하네스 자리 — 이름과 모듈만 같다
    pass


def record(name):
    time.sleep(0.2)
    with open(Path(__file__).parent / "seen.txt", "a") as f:
        f.write(f"{name} {os.environ.get('PYTEST_XDIST_WORKER', '-')}\\n")
"""

# 줄머리에 두지 않는다 — 이 파일까지 Chrome 파일로 읽힌다
CHROME_IMPORT = "from test_web_browser import Chrome, record  # noqa: F401\n"
CHROME_USER = (
    CHROME_IMPORT
    + """

def test_{i}_a():
    record("chrome")


def test_{i}_b():
    record("chrome")
"""
)

PLAIN = """
from test_web_browser import record


def test_{i}_a():
    record("plain")


def test_{i}_b():
    record("plain")
"""


def _addopts() -> str:
    import tomllib

    ini = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    return ini["addopts"]


def test_chrome_modules_run_on_one_xdist_worker(tmp_path):
    """`test_web_browser.Chrome` 을 쓰는 모듈의 시험은 `-n` 에서도 모두 한 워커로 간다 — 여러
    워커가 동시에 Chrome 을 띄우면 CDP 가 마감을 넘긴다. `--dist` 를 주지 않아도 conftest 가
    loadgroup 으로 정한다(여기서는 `pyproject.toml` 의 addopts 를 그대로 쓴다)."""
    addopts = _addopts()
    shutil.copy(ROOT / "tests" / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_web_browser.py").write_text(CHROME_HARNESS + CHROME_USER.format(i="wb"))
    for i in range(3):
        (tmp_path / f"test_layout_{i}.py").write_text(CHROME_USER.format(i=i))
        (tmp_path / f"test_plain_{i}.py").write_text(PLAIN.format(i=i))
    (tmp_path / "pytest.ini").write_text(f"[pytest]\naddopts = -p no:cacheprovider {addopts}\n")
    proc = _pytest(tmp_path, "-n", "3", slow="60")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = [line.split() for line in (tmp_path / "seen.txt").read_text().splitlines()]
    chrome = {worker for kind, worker in seen if kind == "chrome"}
    plain = {worker for kind, worker in seen if kind == "plain"}
    assert len([1 for kind, _ in seen if kind == "chrome"]) == 8, seen
    assert len(chrome) == 1 and chrome != {"-"}, seen
    assert len(plain | chrome) > 1, seen  # 나머지는 퍼진다 — 묶은 것이 Chrome 뿐이다


def test_pytest_starts_without_xdist(tmp_path):
    """pytest-xdist 가 없는 환경에서도 레포의 addopts 로 시작한다. addopts 에 `--dist loadgroup` 이
    있던 때는 `unrecognized arguments: --dist` 로 시험 하나 돌기 전에 끝났다(#245 격리 검증)."""
    addopts = _addopts()
    shutil.copy(ROOT / "tests" / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_web_browser.py").write_text(CHROME_HARNESS + CHROME_USER.format(i="wb"))
    (tmp_path / "test_plain_0.py").write_text(PLAIN.format(i=0))
    (tmp_path / "pytest.ini").write_text(f"[pytest]\naddopts = -p no:cacheprovider {addopts}\n")
    # xdist_group 표시는 xdist 없이도 안다 — 모르는 표시 경고를 실패로 만들어 본다
    proc = _pytest(
        tmp_path, "-p", "no:xdist", "-W", "error::pytest.PytestUnknownMarkWarning", slow="60"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = (tmp_path / "seen.txt").read_text().splitlines()
    assert sorted(seen) == ["chrome -"] * 2 + ["plain -"] * 2, seen


def test_the_chrome_files_are_the_ones_that_bring_in_the_harness():
    """진짜 tests/ 에서 Chrome 으로 묶이는 파일 — 하네스와 그 `Chrome` 을 들여오는 모듈 전부. 새
    Chrome 모듈은 이 목록에 더한다(목록이 틀리면 묶이지 않는 것이 생긴다)."""
    uses_chrome = _conftest().uses_chrome
    found = sorted(p.name for p in (ROOT / "tests").glob("test_*.py") if uses_chrome(str(p)))
    assert found == [
        "test_web_browser.py",
        "test_web_browser_shelf.py",
        "test_web_layout.py",
        "test_web_layout_host_edges.py",
        "test_web_layout_m5n.py",
        "test_web_layout_queue_edges.py",
        "test_web_layout_store_edges.py",
        "test_web_readability_r1.py",
        "test_web_readability_r2.py",
    ]
