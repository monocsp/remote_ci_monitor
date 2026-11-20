"""`scripts/mutcheck.py -j N` — 변이를 동시에 돌린다(2026-11-20, 맥미니 CI 15.5분 가운데 12.7분).

병렬은 빨라지기만 해야 한다. 판정 · 출력 · 종료 코드는 직렬과 같다. 출력은 끝난 순서가 아니라
`MUTANTS` 순서다. 한 변이가 timeout 이어도 나머지는 끝난다. 변이마다 자기 `TMPDIR` 을 쓰고(시스템
임시 폴더를 세는 시험 · pytest 번호 폴더 정리), Chrome 변이는 겹치지 않는다.

진짜 변이 114개 대신 작은 가짜 프로젝트(`ROOT` 를 바꿔 끼운다)와 가짜 변이를 쓴다.
"""

import importlib.util
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "mutcheck.py"
    spec = importlib.util.spec_from_file_location("mutcheck_under_test", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mc = _load()


def fake_project(base: Path) -> Path:
    """`src/mod.py` 하나와 그것을 보는 시험 셋. `SLOW` 를 켜면 시험이 60초를 잔다."""
    root = base / "proj"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "tools").mkdir()
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    (root / "src" / "mod.py").write_text("VALUE = 1  # the answer\nSLOW = False\nBROKEN = False\n")
    (root / "tests" / "test_value.py").write_text(
        "import mod\n\n\ndef test_value():\n    assert mod.VALUE == 1\n"
    )
    (root / "tests" / "test_slow.py").write_text(
        "import time\n\nimport mod\n\n\ndef test_slow():\n"
        "    if mod.SLOW:\n        time.sleep(60)\n"
    )
    (root / "tests" / "test_env.py").write_text(
        "import os\nimport tempfile\n\nimport mod\n\n\ndef test_env():\n"
        "    assert tempfile.gettempdir() == os.environ['TMPDIR'].rstrip('/')\n"
        "    assert not mod.BROKEN\n"
    )
    return root


def M(name, old, new, tests=("tests/test_value.py",), **kw):
    return mc.Mutant(name=name, path="src/mod.py", old=old, new=new, tests=tests, **kw)


FAKE = (
    M("caught", "VALUE = 1", "VALUE = 2"),
    M("survives", "# the answer", "# another answer"),
    M("gone", "VALUE = 3", "VALUE = 4"),
    M("times-out", "SLOW = False", "SLOW = True", tests=("tests/test_slow.py",)),
    M("own-tmpdir", "BROKEN = False", "BROKEN = True", tests=("tests/test_env.py",)),
)


def run(argv, capsys):
    code = mc.main(argv)
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("[")]
    # 시간만 다르다
    return code, [re.sub(r"\d+\.\d+s", "Ns", line) for line in lines]


@pytest.mark.slow
def test_parallel_and_serial_report_the_same_and_a_timeout_does_not_hold_the_rest(
    tmp_path, monkeypatch, capsys
):
    # mutcheck 안에서 돌 때는 바깥 mutcheck 가 준 값이 남는다 — 이 mutcheck 가 스스로 끄는지 본다
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.setattr(mc, "ROOT", fake_project(tmp_path))
    monkeypatch.setattr(mc, "MUTANTS", FAKE)
    monkeypatch.setattr(mc, "PYTEST_TIMEOUT_SECONDS", 4)
    serial = run(["-j", "1"], capsys)
    started = time.monotonic()
    parallel = run(["-j", str(len(FAKE))], capsys)
    took = time.monotonic() - started
    assert parallel == serial
    code, lines = parallel
    assert code == 1
    assert [line.split(":", 1)[0] for line in lines] == [
        "[OK ] caught",
        "[FAIL] survives",
        "[FAIL] gone",
        "[FAIL] times-out",
        "[OK ] own-tmpdir",
    ]
    assert "tests stayed GREEN" in lines[1]
    assert "pattern not found" in lines[2]
    assert "mutant run timed out" in lines[3]
    # 대조군이 초록이었다 = 그 변이의 pytest 가 자기 `TMPDIR` 을 썼다. `caught` 는 대조군이 1초
    # 안에 끝나고 변이가 같은 길이라 `.pyc` 를 다시 읽으면 「초록」이 된다(바이트코드 캐시)
    assert "control green" in lines[4] and "control green" in lines[0]
    # timeout(4초) 하나를 기다리는 동안 나머지가 끝났다 — 직렬이면 그 뒤에 줄을 섰다
    assert took < mc.PYTEST_TIMEOUT_SECONDS + 15, took


def test_the_report_follows_the_list_not_the_finish_order(monkeypatch, capsys):
    """먼저 시작한 변이가 가장 늦게 끝나도 출력은 `MUTANTS` 순서다."""
    mutants = tuple(M(f"m{i}", "VALUE = 1", "VALUE = 2") for i in range(5))
    delays = {"m0": 0.4, "m1": 0.3, "m2": 0.0, "m3": 0.2, "m4": 0.0}

    def fake(m, keep):
        time.sleep(delays[m.name])
        return (m.name != "m3"), f"done {m.name}"

    monkeypatch.setattr(mc, "MUTANTS", mutants)
    monkeypatch.setattr(mc, "run_mutant", fake)
    code, lines = run(["-j", "5"], capsys)
    assert lines == [
        "[OK ] m0: done m0",
        "[OK ] m1: done m1",
        "[OK ] m2: done m2",
        "[FAIL] m3: done m3",
        "[OK ] m4: done m4",
    ]
    assert code == 1


def test_a_mutant_that_crashes_is_a_failure_and_the_rest_still_report(monkeypatch, capsys):
    mutants = (M("a", "x", "y"), M("boom", "x", "y"), M("c", "x", "y"))

    def fake(m, keep):
        if m.name == "boom":
            raise RuntimeError("copy failed")
        return True, "fine"

    monkeypatch.setattr(mc, "MUTANTS", mutants)
    monkeypatch.setattr(mc, "run_mutant", fake)
    code, lines = run(["-j", "3"], capsys)
    assert code == 1
    assert [line.split(":", 1)[0] for line in lines] == ["[OK ] a", "[FAIL] boom", "[OK ] c"]
    assert "copy failed" in lines[1]


def test_chrome_mutants_never_run_at_the_same_time(monkeypatch, capsys):
    mutants = tuple(M(f"c{i}", "x", "y", needs_chrome=True) for i in range(3)) + (
        M("plain", "x", "y"),
    )
    running = []
    peak = []
    lock = threading.Lock()

    def fake(m, keep):
        with lock:
            running.append(m.name)
            peak.append(sum(1 for n in running if n.startswith("c")))
        time.sleep(0.1)
        with lock:
            running.remove(m.name)
        return True, "ok"

    monkeypatch.setattr(mc, "MUTANTS", mutants)
    monkeypatch.setattr(mc, "run_mutant", fake)
    code, _ = run(["-j", "4"], capsys)
    assert code == 0
    assert max(peak) == 1, peak


def test_each_mutant_gets_its_own_tmpdir_and_it_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "ROOT", fake_project(tmp_path))
    seen = []

    def fake_pytest(cmd, cwd, env=None):
        assert env is not None
        # 바이트코드를 안 쓴다 — 옛 `.pyc` 가 같은 길이의 변이를 가린다(같은 초에 쓰였을 때만이라
        # 실제 실행으로는 들쭉날쭉해서 값을 직접 본다)
        assert env.get("PYTHONDONTWRITEBYTECODE") == "1"
        seen.append(env["TMPDIR"])
        assert Path(env["TMPDIR"]).is_dir()
        return 1, 0.1, ""  # 대조군이 빨갛다 — 여기서 멈춘다

    monkeypatch.setattr(mc, "_pytest", fake_pytest)
    mc.run_mutant(FAKE[0], keep=False)
    mc.run_mutant(FAKE[0], keep=False)
    assert len(set(seen)) == 2 and seen[0] != os.environ.get("TMPDIR"), seen
    assert not any(Path(p).exists() for p in seen)


def test_keep_reports_the_copy_in_the_result_not_on_its_own_line(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "ROOT", fake_project(tmp_path))
    monkeypatch.setattr(mc, "_pytest", lambda cmd, cwd, env=None: (1, 0.1, ""))
    ok, info = mc.run_mutant(FAKE[0], keep=True)
    kept = Path(info.rsplit("kept copy: ", 1)[1].strip())
    try:
        assert ok is False and (kept / "src" / "mod.py").is_file()
    finally:
        import shutil

        shutil.rmtree(kept, ignore_errors=True)


@pytest.mark.parametrize("cpus, want", [(10, 5), (1, 1), (None, 1), (3, 1)])
def test_jobs_zero_is_half_the_cpus(monkeypatch, cpus, want):
    monkeypatch.setattr(mc.os, "cpu_count", lambda: cpus)
    assert mc.resolve_jobs(0) == want
    assert mc.resolve_jobs(3) == 3


def test_negative_jobs_is_a_usage_error(capsys):
    assert mc.main(["-j", "-1"]) == 2
    assert "--jobs" in capsys.readouterr().err


def test_no_mutant_runs_the_test_that_shares_a_fixed_port():
    """`tests/test_mdns_loopback.py` 는 15353 을 `SO_REUSEPORT` 로 나눠 듣는다 — 두 변이가 동시에
    돌리면 서로의 응답을 센다. 그 파일을 쓰는 변이가 필요하면 포트를 먼저 변이마다 나눈다."""
    for m in mc.MUTANTS:
        assert not any("test_mdns_loopback" in t for t in m.tests), m.name
