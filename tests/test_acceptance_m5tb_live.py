"""M5t-b 실기 하네스 `scripts/acceptance/m5tb_live.sh` 의 정적 잠금.

명세는 docs/m5tb-workplan.md §5 PR4 행 · §7 이다.

하네스는 진짜 서버 · 워커 · 중계를 띄우고 G 만료를 실제로 기다리므로 pytest 가 부르지 않는다.
여기서는 그 하네스를 `source` 해서 순수 함수와 판정만 부른다.

- 가드: 운영 포트(8787 · 8788) · 운영 경로 · /tmp · 이미 있는 폴더를 아무것도 만들기 전에 거절하고,
  운영 launchd 라벨을 알아보고, 이 하네스가 띄우지 않은 프로세스에는 신호를 보내지 않는다.
- 단계 표가 §7.1 의 행 ID 를 모두 가진다(명세 표를 읽어 견준다). 실기 대상 행은 live 다.
- 하네스가 기대하는 키 · 코드 · 칸 · 로그 줄 · CLI 옵션 · 중계 명령이 진짜 생산자(코드)에서
  나온다 — 손으로 베낀 집합과 견주지 않는다. PR2(#286) · PR3(#285)의 키는 그 PR 이 이 트리에
  있을 때만 견준다.
- 판정은 모르는 것을 PASS 로 적지 않는다(PLAN.md 「fail-open 금지」).

프로세스는 bash · `sleep` 대역과 localhost 중계 하나만 띄우고 네트워크는 쓰지 않는다.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "m5tb_live.sh"
WORKPLAN = ROOT / "docs" / "m5tb-workplan.md"
SRC = ROOT / "src" / "remote_ci_monitor"

#: in 파일(batch-1)이 실기 확인 대상으로 꼽은 행 — §5 PR4 행의 우선 목록 + PR2 · PR3 검증이
#: 넘긴 것 + F78
LIVE_REQUIRED = {
    "RL1", "RL2", "RL10", "RL15", "RL34", "RL40", "RL42", "RL43", "RL50", "RL51",
    "RL5", "RL8", "RL18", "RL28", "RL29", "RL45", "F78",
}  # fmt: skip


def sh(fn: str, *args: str, home: Path | None = None, extra: dict[str, str] | None = None):
    """하네스를 source 하고 함수 하나를 부른다. 파이썬은 이 시험의 해석기(`M5TB_PY`)다."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "M5TB_PY": sys.executable,
        "HOME": str(home) if home is not None else os.environ.get("HOME", "/"),
        **(extra or {}),
    }
    r = subprocess.run(
        ["bash", "-c", 'source "$1" && shift && "$@"', "bash", str(SCRIPT), fn, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    # 거절(종료 1)을 기대하는 시험이 하네스 · 함수가 없어서 초록이 되지 않게
    for missing in ("No such file or directory", "command not found"):
        assert missing not in r.stderr, r.stderr
    return r


def script_text() -> str:
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    return SCRIPT.read_text(encoding="utf-8")


def var(name: str) -> str:
    """하네스의 `NAME="…"` 한 줄의 값."""
    m = re.search(rf'^{name}="([^"]*)"$', script_text(), re.M)
    assert m, f"the harness has no {name} line"
    return m.group(1)


def steps() -> list[tuple[str, str, str, str]]:
    r = sh("steps_table")
    assert r.returncode == 0, r.stderr
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split("|", 3)
        assert len(parts) == 4, line
        rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


def workplan_rows() -> dict[str, str]:
    """§7.1 시나리오 표의 행 ID → 「어디서」 칸."""
    text = WORKPLAN.read_text(encoding="utf-8")
    start = text.index("<!-- scenario-prefix: RL -->")
    end = text.index("### 7.2", start)
    rows: dict[str, str] = {}
    for line in text[start:end].splitlines():
        m = re.match(r"^\| (RL\d+) \| ", line)
        if m:
            cells = line.strip().strip("|").split(" | ")
            rows[m.group(1)] = cells[-2]
    return rows


def helper(tmp_path: Path) -> ModuleType:
    """하네스가 시험 폴더에 쓰는 도우미를 써서 import 한다."""
    d = tmp_path / "helper"
    r = sh("write_helper", str(d))
    assert r.returncode == 0, r.stderr
    path = d / "m5tb_helper.py"
    assert path.is_file(), "write_helper wrote no m5tb_helper.py"
    spec = importlib.util.spec_from_file_location(f"m5tb_helper_{id(tmp_path)}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


# ── 문법 · source · 운영과의 거리 ────────────────────────────────────────────


def test_pr4_harness_parses_and_sourcing_it_runs_nothing(tmp_path: Path) -> None:
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    syntax = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert syntax.returncode == 0, syntax.stderr
    r = subprocess.run(
        ["bash", "-c", 'source "$1" && echo sourced', "bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "M5TB_PY": sys.executable},
        timeout=15,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "sourced"
    assert list(tmp_path.iterdir()) == []  # 폴더 하나 만들지 않았다


def test_pr4_harness_never_drives_launchd_or_deletes_a_folder() -> None:
    lines = script_text().splitlines()
    code = [x for x in lines if x.strip() and not x.lstrip().startswith("#")]
    body = "\n".join(code)
    assert not re.search(r"\blaunchctl\b", body), "the M5t-b harness must not drive launchd"
    assert "LaunchAgents/" not in body.replace('"$h/Library/LaunchAgents"', "")
    assert not re.search(r"\brm\s+-[a-zA-Z]*r", body), "no rm -r — a new name instead"
    assert "8787" not in body  # 운영 포트를 쓰는 자리가 없다(거절은 범위 검사가 한다)


@pytest.mark.parametrize("port", ["8787", "8788", "80", "8789", "8900", "abc", "8797x", ""])
def test_pr4_port_check_refuses_production_and_out_of_range_ports(port: str) -> None:
    assert sh("is_test_port", port).returncode == 1, port


@pytest.mark.parametrize("port", ["8790", "8797", "8899"])
def test_pr4_port_check_takes_a_test_port(port: str) -> None:
    assert sh("is_test_port", port).returncode == 0, port


@pytest.mark.parametrize(
    "label",
    ["com.remote-ci-monitor", "com.remote-ci-monitor.server", "COM.Remote-CI-Monitor.Worker"],
)
def test_pr4_production_launchd_labels_are_recognised(label: str) -> None:
    assert sh("is_prod_label", label).returncode == 0, label


@pytest.mark.parametrize(
    "label", ["com.example.rcm-m5t-live-ab12cd34", "com.remote-ci-monitorx", "org.example", ""]
)
def test_pr4_other_labels_are_not_production(label: str) -> None:
    assert sh("is_prod_label", label).returncode == 1, label


def test_pr4_base_check_takes_only_the_m5tb_devtest_folder(tmp_path: Path) -> None:
    home = tmp_path / "h"
    share = home / ".local" / "share"
    good = [share / "rcm-devtest-m5tb" / "live-ab12cd34", share / "rcm-devtest-m5tb" / "x"]
    bad = [
        share / "rcm",
        share / "rcm" / "x",
        share / "rcm-devtest-m5tb",
        share / "rcm-devtest-m5tb" / "a" / "b",
        share / "rcm-devtest" / "x",  # M5t-a 하네스의 자리 — 섞지 않는다
        home / ".config" / "rcm",
        home,
        Path("/tmp/rcm-devtest-m5tb/x"),
        Path("/private/tmp/x"),
    ]
    for b in good:
        assert sh("is_test_base", str(b), str(home)).returncode == 0, b
    for b in bad:
        assert sh("is_test_base", str(b), str(home)).returncode == 1, b
    dotted = f"{share}/rcm-devtest-m5tb/x/../../rcm"
    assert sh("is_test_base", dotted, str(home)).returncode == 1
    assert sh("is_test_base", "relative/rcm-devtest-m5tb/x", str(home)).returncode == 1
    assert sh("is_test_base", str(share / "rcm-devtest-m5tb" / "x"), "/").returncode == 1


def test_pr4_path_check_refuses_production_places(tmp_path: Path) -> None:
    home = tmp_path / "h"
    base = home / ".local" / "share" / "rcm-devtest-m5tb" / "live-ab12cd34"
    inside = [base / "data" / "rcm.sqlite3", base / "w" / "rl1a-1.toml", base]
    outside = [
        home / ".config" / "rcm" / "server.toml",
        home / ".local" / "share" / "rcm" / "rcm.sqlite3",
        home / "Library" / "LaunchAgents" / "com.remote-ci-monitor.server.plist",
        home / "Library" / "Logs" / "rcm" / "server.log",
        Path(f"{base}/../../rcm/rcm.sqlite3"),
        Path(f"{base}-other/data"),
    ]
    for p in inside:
        assert sh("is_test_path", str(p), str(base), str(home)).returncode == 0, p
    for p in outside:
        assert sh("is_test_path", str(p), str(base), str(home)).returncode == 1, p


def _server_toml(
    path: Path, *, port: int, data: Path, advertise: str, bind: str, hook: str = ""
) -> None:
    text = f'[server]\nbind = "{bind}"\nport = {port}\ndata_dir = "{data}"\n'
    text += f"advertise = {advertise}\n"
    if hook:
        text += f'\n[[notify]]\nname = "x"\non = ["lost"]\nargv = ["sh", "{hook}"]\n'
    path.write_text(text, encoding="utf-8")


def test_pr4_server_toml_check_takes_test_values_only(tmp_path: Path) -> None:
    home = tmp_path / "h"
    base = home / ".local" / "share" / "rcm-devtest-m5tb" / "live-ab12cd34"
    base.mkdir(parents=True)
    conf, data = base / "server.toml", base / "data"
    args = (str(conf), "8797", str(data), str(base), str(home))
    _server_toml(conf, port=8797, data=data, advertise="false", bind="127.0.0.1",
                 hook=str(base / "bin" / "notify.sh"))  # fmt: skip
    r = sh("check_server_toml", *args)
    assert r.returncode == 0, r.stdout + r.stderr
    cases = [
        dict(port=8787, data=data, advertise="false", bind="127.0.0.1"),
        dict(port=8797, data=data, advertise="true", bind="127.0.0.1"),
        dict(port=8797, data=home / ".local/share/rcm", advertise="false", bind="127.0.0.1"),
        dict(port=8797, data=data, advertise="false", bind="0.0.0.0"),
        dict(port=8797, data=data, advertise="false", bind="127.0.0.1", hook="/usr/local/bin/x.sh"),
    ]
    for case in cases:
        _server_toml(conf, **case)  # type: ignore[arg-type]
        r = sh("check_server_toml", *args)
        assert r.returncode == 1, case
        assert r.stdout.strip(), case  # 무엇이 틀렸는지 한 줄


@pytest.mark.parametrize(
    "over,says",
    [
        ({"M5TB_PORT": "8787"}, "not a test port"),
        ({"M5TB_PORT": "8788"}, "not a test port"),
        ({"M5TB_PORT": "8898", "M5TB_BASE": "{home}/.local/share/rcm"}, "is not"),
        ({"M5TB_PORT": "8898", "M5TB_BASE": "{home}/.config/rcm"}, "is not"),
        ({"M5TB_PORT": "8898", "M5TB_BASE": "{base}"}, "exists"),
        (
            {
                "M5TB_PORT": "8898",
                "RCM_SERVER_DATA_DIR": "{home}/.local/share/rcm",
                "M5TB_BASE": "{home}/.local/share/rcm",
            },
            "is not",
        ),
    ],  # fmt: skip
    ids=["8787", "8788", "production-data-dir", "production-config", "existing-base", "env"],
)
def test_pr4_main_stops_before_anything_on_a_production_port_or_folder(
    tmp_path: Path, over: dict[str, str], says: str
) -> None:
    home = tmp_path / "h"
    base = home / ".local" / "share" / "rcm-devtest-m5tb" / "live-ab12cd34"
    base.mkdir(parents=True)
    before = _tree(home)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "M5TB_PY": sys.executable,
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


def test_pr4_main_refuses_a_new_base_under_tmp() -> None:
    # 가짜 홈을 /tmp 아래에 둔다 — 없는 폴더라도 /tmp 아래면 거절한다
    home = Path(tempfile.mkdtemp(prefix="m5tb-live-tmpcheck-", dir="/tmp"))
    try:
        base = home / ".local" / "share" / "rcm-devtest-m5tb" / "live-ab12cd34"
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "M5TB_PY": sys.executable,
            "M5TB_PORT": "8898",
            "M5TB_BASE": str(base),
        }
        r = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=15, check=False
        )
        if "is in use" in r.stderr:
            pytest.skip(r.stderr)
        assert r.returncode == 2, r.stderr
        assert "is under /tmp" in r.stderr
        assert _tree(home) == []
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_pr4_only_names_live_steps_and_list_creates_nothing(tmp_path: Path) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "M5TB_PY": sys.executable,
           "M5TB_PORT": "8898"}  # fmt: skip
    r = subprocess.run(["bash", str(SCRIPT), "--list"], capture_output=True, text=True, env=env,
                       timeout=15, check=False)  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0].startswith("RL1|live|")
    r = subprocess.run(["bash", str(SCRIPT), "--only", "RL4"], capture_output=True, text=True,
                       env=env, timeout=15, check=False)  # fmt: skip
    if "is in use" in r.stderr:
        pytest.skip(r.stderr)
    assert r.returncode == 2 and "not a live step" in r.stderr, r.stderr
    assert list(tmp_path.iterdir()) == []


def test_pr4_isolate_env_drops_rcm_and_points_home_and_xdg_into_the_base() -> None:
    r = subprocess.run(
        ["bash", "-c", 'source "$1" && isolate_env /b/m5tb && env', "bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": "/real",
            "RCM_TOKEN": "x",
            "RCM_SERVER": "http://127.0.0.1:8787",
            "RCM_SERVER_DATA_DIR": "/real/.local/share/rcm",
            "RCM_WORKER_TOKEN": "y",
            "XDG_CONFIG_HOME": "/real/.config",
        },
        timeout=15,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    env = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    assert sorted(k for k in env if k.startswith("RCM_")) == []
    assert env["HOME"] == "/b/m5tb/home"
    for k, v in (("CONFIG", "config"), ("DATA", "data"), ("STATE", "state"), ("CACHE", "cache")):
        assert env[f"XDG_{k}_HOME"] == f"/b/m5tb/xdg/{v}"


def test_pr4_main_isolates_before_the_first_rcm_command() -> None:
    text = script_text()
    start = text.index("\nmain() {")
    body = text[start : text.index("\n}\n", start)]
    assert body.index('mkdir "$B"') < body.index('isolate_env "$B"') < body.index("server_start")
    assert body.index("check_server_toml") < body.index("server_start")
    assert not re.search(r'^\s*"\$RCM"\s', body, re.M)  # main 은 rcm 을 직접 부르지 않는다


def _sleeper(*argv: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", *argv],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_pr4_signals_go_only_to_processes_this_harness_started(tmp_path: Path) -> None:
    base = str(tmp_path / "h" / ".local" / "share" / "rcm-devtest-m5tb" / "live-ab12cd34")
    ours, other = _sleeper(base + "/server.toml"), _sleeper("/elsewhere/server.toml")
    try:
        time.sleep(0.2)
        start = sh("proc_start", str(ours.pid)).stdout.strip()
        assert start, "ps gave no start time"
        assert sh("is_our_proc", str(ours.pid), base, start).returncode == 0
        # 명령줄에 시험 폴더가 없다 — 운영 rcm serve 같은 것
        other_start = sh("proc_start", str(other.pid)).stdout.strip()
        assert sh("is_our_proc", str(other.pid), base, other_start).returncode == 1
        # 시작 시각이 적은 것과 다르다 — pid 가 재사용됐다
        assert sh("is_our_proc", str(ours.pid), base, "Thu Jan  1 00:00:00 1970").returncode == 1
        assert sh("is_our_proc", str(ours.pid), base, "").returncode == 1
        assert sh("is_our_proc", "1", base, start).returncode == 1
        # 잡 pid: 시작 시각이 같을 때만
        assert sh("is_our_job_pid", str(ours.pid), start).returncode == 0
        assert sh("is_our_job_pid", str(ours.pid), "Thu Jan  1 00:00:00 1970").returncode == 1
        assert ours.poll() is None and other.poll() is None
    finally:
        for p in (ours, other):
            p.kill()
            p.wait(timeout=10)


def test_pr4_kill_job_group_leaves_a_reused_pid_alone(tmp_path: Path) -> None:
    p = _sleeper()
    try:
        time.sleep(0.2)
        rec = tmp_path / "1.start"
        rec.write_text("Thu Jan  1 00:00:00 1970\n", encoding="utf-8")
        assert sh("kill_job_group", str(p.pid), str(rec)).returncode == 0
        time.sleep(0.3)
        assert p.poll() is None  # 신원이 다르면 건드리지 않는다
        rec.write_text(sh("proc_start", str(p.pid)).stdout, encoding="utf-8")
        assert sh("kill_job_group", str(p.pid), str(rec)).returncode == 0
        p.wait(timeout=10)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)


# ── 단계 표 ↔ 명세 §7.1 ──────────────────────────────────────────────────────


def test_pr4_steps_table_has_every_row_id_of_section_7() -> None:
    rows = workplan_rows()
    assert len(rows) >= 56, sorted(rows)
    ids = [s[0] for s in steps()]
    assert len(ids) == len(set(ids)), "an ID is listed twice"
    assert set(ids) - {"F78"} == set(rows), (
        sorted(set(rows) - set(ids)),
        sorted(set(ids) - set(rows) - {"F78"}),
    )
    assert "F78" in ids


def test_pr4_e2e_rows_are_live_or_named_pytest_e2e_and_the_asked_rows_are_live() -> None:
    rows = workplan_rows()
    table = {s[0]: s for s in steps()}
    for rid, where in rows.items():
        kind = table[rid][1]
        assert kind in ("live", "e2e", "unit"), (rid, kind)
        if "e2e" in where or "실기" in where:
            assert kind in ("live", "e2e"), f"{rid} has e2e in §7.1 but the harness says {kind}"
        if kind == "e2e":
            assert "tests/" in table[rid][3], f"{rid}: name the pytest file that covers it"
    for rid in LIVE_REQUIRED:
        assert table[rid][1] == "live", f"{rid} must be a live step"


def test_pr4_every_live_step_names_runners_that_exist() -> None:
    text = script_text()
    defined = set(re.findall(r"^([a-z_0-9]+)\(\) \{", text, re.M))
    order = var("ALL_RUNNERS").split()
    for rid, kind, runners, method in steps():
        if kind != "live":
            assert runners == "-", rid
            continue
        assert method.strip(), rid
        for r in runners.split("+"):
            assert r in defined, f"{rid}: {r} is not defined"
            assert r in order, f"{rid}: {r} is not in ALL_RUNNERS"
    for r in order:
        assert r in defined, r


# ── 하네스가 기대하는 것 ↔ 진짜 생산자 ──────────────────────────────────────────


def _dict_keys(path: Path, func: str) -> set[str]:
    """`func` 가 돌려주는 dict 리터럴의 문자열 키(가장 큰 것)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    best: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    keys = {k.value for k in sub.keys if isinstance(k, ast.Constant)}
                    if len(keys) > len(best):
                        best = {str(k) for k in keys}
    assert best, f"{path.name}:{func} returns no dict any more"
    return best


def test_pr4_job_queue_and_hold_keys_come_from_the_status_producer() -> None:
    status = SRC / "core" / "status.py"
    assert set(var("JOB_KEYS").split()) <= _dict_keys(status, "recent_json")
    assert set(var("QUEUE_KEYS").split()) <= _dict_keys(status, "queue_row_json")
    assert set(var("HOLD_ITEM_KEYS").split()) <= _dict_keys(status, "hold_item_json")
    from remote_ci_monitor.core.status import hold_doc_json

    doc = hold_doc_json({"cause": "worker_unreachable", "history": []})  # 진짜 생산자
    assert doc is not None
    assert set(var("HOLD_DOC_KEYS").split()) <= set(doc)
    # 판정이 읽는 /api/health 의 칸 — 서버가 만드는 본문 사전
    health = _dict_keys(SRC / "server.py", "health")
    assert {"ok", "workers_down", "irreversible_holds"} <= health, sorted(health)


def test_pr4_lost_codes_are_rendered_by_the_outcome_producer() -> None:
    from remote_ci_monitor.core.outcome import CODES, render

    codes = var("LOST_CODES").split()
    assert len(codes) == 5
    for code in codes:
        assert code in CODES, code
        text = render(code, {"name": "w1", "seconds": 90})
        assert text and code not in text, (code, text)


def test_pr4_db_columns_exist_in_a_fresh_store(tmp_path: Path) -> None:
    import sqlite3

    from remote_ci_monitor.store import Store

    s = Store(tmp_path / "rcm.sqlite3")
    s.open_holds()  # 스키마를 만든다
    s.close()
    conn = sqlite3.connect(f"file:{tmp_path / 'rcm.sqlite3'}?mode=ro", uri=True)
    try:
        for item in var("DB_COLUMNS").split():
            table, col = item.split(".")
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert col in cols, item
    finally:
        conn.close()
    from remote_ci_monitor.store import EVENT_HOLD, EVENT_STATE

    text = script_text()
    assert f"kind='{EVENT_HOLD}'" in text and f"kind='{EVENT_STATE}'" in text


def test_pr4_notify_env_and_worker_lines_come_from_the_code() -> None:
    notify = (SRC / "core" / "notify.py").read_text(encoding="utf-8")
    for name in var("NOTIFY_ENV").split():
        assert f'"{name}"' in notify, name
    hook = script_text()
    for name in var("NOTIFY_ENV").split():
        assert f"${name}" in hook, f"the fake hook does not record {name}"
    worker = (SRC / "remote_worker.py").read_text(encoding="utf-8")
    for line in var("WORKER_LINES").split("|"):
        assert line in worker, f"remote_worker.py no longer logs '{line}'"
    for line in (
        "orphan #{job_dir.name}: stopped process group",
        "orphan #{job_dir.name}: leader gone but its group is alive",
        "no answer from the server for {int(wgap)}s",
        "grace_seconds {conf} lowered to {grace}",
    ):
        assert line in worker, line


def test_pr4_cli_options_the_harness_uses_exist() -> None:
    from remote_ci_monitor.cli import build_parser

    ap = build_parser()
    assert "--no-follow" in {o for a in ap._actions for o in a.option_strings}
    subs = next(a for a in ap._actions if isinstance(a, __import__("argparse")._SubParsersAction))
    for item in var("CLI_USES").split():
        name, _, opts = item.partition(":")
        assert name in subs.choices, name
        have = {o for a in subs.choices[name]._actions for o in a.option_strings}
        for o in filter(None, opts.split(",")):
            assert o in have, f"rcm {name} has no {o}"


def test_pr4_relay_commands_drive_the_real_tcp_relay(tmp_path: Path) -> None:
    from tcp_relay import TcpRelay

    mod = helper(tmp_path)
    r = TcpRelay(1)  # 위로 붙지 않는다 — 명령이 메서드에 닿는지만 본다
    try:
        for op in var("RELAY_OPS").split():
            words = {"hold": ["hold", "/worker/heartbeat", "refuse"], "slow": ["slow", "/x", "1"]}
            assert mod.apply_relay(r, words.get(op, [op])) == "ok", op
        assert mod.apply_relay(r, ["nope"]).startswith("error")
        for attr in ("held_count", "refused", "slow_count", "accepted"):
            assert isinstance(getattr(r, attr), int), attr
    finally:
        r.close()


def test_pr4_web_helper_uses_the_cdp_helper_it_names() -> None:
    import test_web_browser as wb

    for name in ("viewport", "open", "frames", "eval", "page_errors", "screenshot"):
        assert callable(getattr(wb.Chrome, name, None)), name
    # 도우미가 재는 요소 — 띠 · 「확인이 필요한 작업」 칸(그리기 전 글은 `—`) · 홀드 칩
    app = (SRC / "web" / "app.js").read_text(encoding="utf-8")
    assert '$("#banner-note")' in app and '$("[data-stuck]")' in app
    index = (SRC / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="banner-note"' in index and "data-stuck>—</span>" in index
    helper_js = script_text()
    assert "t !== '—'" in helper_js and "[data-hold-chip]" in helper_js


_JS_PARSE = """
const s = JSON.parse(process.argv[1]);
const bad = [];
for (const [k, v] of Object.entries(s)) {
  try { new Function("return " + v); } catch (e) { bad.push(k + ": " + e.message); }
}
process.stdout.write(JSON.stringify(bad));
"""


def test_pr4_web_js_the_helper_sends_to_chrome_parses(tmp_path: Path) -> None:
    """도우미가 CDP 로 보내는 식은 JS 로 읽혀야 한다. 문법이 틀리면 Chrome 이 예외를 던지고 웹
    단계가 통째로 UNKNOWN 이 된다(f-string 의 `}}` 가 일반 글 조각에 남은 일이 있었다)."""
    import json

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    mod = helper(tmp_path)
    snippets = {**mod.WEB_JS, "READY_JS": mod.READY_JS, "row_js": mod.row_js("12")}
    r = subprocess.run([node, "-e", _JS_PARSE, json.dumps(snippets)], capture_output=True,
                       text=True, timeout=20, check=True)  # fmt: skip
    assert json.loads(r.stdout) == [], r.stdout


def test_pr4_supervisor_and_secret_entry_points_exist(tmp_path: Path) -> None:
    mod = helper(tmp_path)
    import remote_ci_monitor
    from remote_ci_monitor.config import parse_release_profile
    from remote_ci_monitor.release_secrets import SecretStore, secrets_dir

    assert (Path(remote_ci_monitor.__file__).parent / "job_supervisor.py").is_file()
    conf = tmp_path / "server.toml"
    conf.write_text("", encoding="utf-8")
    value = tmp_path / "v.txt"
    value.write_text("rl18-test-value", encoding="utf-8")
    assert mod.cmd_putsecret(str(conf), "app", "GH_TOKEN", str(value)) == 0
    profile = parse_release_profile(
        "app",
        {"secrets_dir_env": "APP_SECRETS", "secrets": [{"name": "GH_TOKEN", "kind": "value"}],
         "presets": {"plan": "rl18-plan", "upload": "rl18-upload", "review": "rl18-review"}},
    )  # fmt: skip
    store = SecretStore(secrets_dir(conf, "app"), profile)
    assert b"rl18-test-value" in store.mask_values()


def test_pr4_pr2_keys_match_the_producer_once_pr2_is_here() -> None:
    store = (SRC / "store.py").read_text(encoding="utf-8")
    if "def record_late_result" not in store:
        pytest.skip("PR2 (#286) is not on this tree — its keys were checked on its branch")
    workers = SRC / "remote_workers.py"
    tree = ast.parse(workers.read_text(encoding="utf-8"))
    payload: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {str(k.value) for k in node.keys if isinstance(k, ast.Constant)}
            if {"outcome", "received_at"} <= keys:
                payload = keys
    assert payload, "remote_workers.py has no late_result payload"
    assert set(var("PR2_LATE_KEYS").split()) - {"log_complete"} <= payload
    server = (SRC / "server.py").read_text(encoding="utf-8")
    for key in var("PR2_JOB_KEYS").split():
        assert f'"{key}"' in server, key


def _need_pr3() -> None:
    render = (SRC / "core" / "render_text.py").read_text(encoding="utf-8")
    if "def held_group" not in render:
        pytest.skip("PR3 (#285) is not on this tree — its lines were checked on its branch")


def _produced_holds() -> list[dict[str, object]]:
    """`/api/status` · `/api/health` 의 `irreversible_holds` 항목 — 진짜 생산자 `hold_item_json`."""
    from datetime import UTC, datetime

    from remote_ci_monitor.core.model import OpenHold
    from remote_ci_monitor.core.status import hold_item_json

    since = datetime(2026, 11, 27, 8, 50, tzinfo=UTC)
    hold = OpenHold(job_id=5, group="irreversible:ship", scope="global", pool="m5tb",
                    since=since, preset="rl43-ship", worker="rl43a",
                    cause="worker_unreachable")  # fmt: skip
    return [hold_item_json(hold)]


def _produced_cli_lines() -> dict[str, tuple[str, ...]]:
    """하네스의 줄 변수 이름 → 그 줄을 쓰는 진짜 생산자가 만든 글(하나 이상). 손으로 베끼지 않고
    `rcm wait` · `rcm top` · `rcm eta` · `rcm check` · `rcm doctor` 의 생산 함수를 부른다."""
    from remote_ci_monitor import cli
    from remote_ci_monitor.core import doctor as core_doctor
    from remote_ci_monitor.core import render_text as rt
    from remote_ci_monitor.core.model import REASON_WORKER_UNREACHABLE_GRACE as GRACE
    from remote_ci_monitor.doctor import parse_holds

    run = {"id": 7, "state": "running", "reason": GRACE, "ran_on": "rl28"}
    # rcm wait 이 기다리는 동안: 끊김 → (취소 요청) → 돌아옴
    w = cli._GraceWatch(7)
    lost = w.observe(run)
    corrected = w.observe({**run, "state": "cancelling"})
    back_cancel = w.observe({**run, "state": "cancelling", "reason": "cancelling"})
    w = cli._GraceWatch(7)
    w.observe(run)
    back_on = w.observe({**run, "reason": "running"})
    # 취소 뒤에 새로 기다리는 rcm wait 의 첫 줄
    fresh_cancel = cli._GraceWatch(7).observe({**run, "state": "cancelling"})
    held = {
        "id": 9, "state": "queued", "position": 1, "reason": "needs_action",
        "blocked_by": {"job_id": 5, "group": "irreversible:ship", "remaining_seconds": None},
        "action": {"code": "irreversible_hold", "note": "x", "at": "2026-11-27T08:50:00Z"},
        "estimate": {"waited_seconds": 3.0, "wait_seconds": None, "finish_at": None},
    }  # fmt: skip
    grace_row = {
        "id": 7, "state": "running", "position": None, "reason": GRACE, "ran_on": "rl28",
        "estimate": {"elapsed_seconds": 20.0, "finish_at": None, "source": "preset"},
    }  # fmt: skip
    holds = _produced_holds()
    health = {"irreversible_holds": holds}
    top_empty = rt.render(
        {"schema_version": 1, "generated_at": "2026-11-27T09:00:00Z", "display_timezone": "UTC",
         "server": {"lanes": 0, "workers": []}, "irreversible_holds": holds,
         "pools": [{"name": "default", "queue": [], "recent": [], "medians": {}, "hosts": []}]}
    )  # fmt: skip
    got: dict[str, tuple[str, ...]] = {
        "LINE_LOST_CONTACT": (str(lost), str(fresh_cancel), rt.unreachable_text("rl28")),
        "LINE_GRACE_GOES_ON": (str(lost), rt.unreachable_text("rl28")),
        "LINE_GRACE_CANCEL": (str(fresh_cancel), str(corrected),
                              rt.unreachable_text("rl28", cancelling=True)),
        "LINE_CANCEL_REQUESTED": (str(corrected),),
        "LINE_BACK_GOES_ON": (str(back_on),),
        "LINE_BACK_CANCEL": (str(back_cancel),),
        "LINE_LATE": (str(rt.late_result_line(
            {"late_result": {"outcome": "succeeded", "exit_code": 0}}, job_id=7)),),
        "LINE_HELD_WAIT": (str(cli.action_line(9, held["action"], held["blocked_by"])),),
        "LINE_HOLD_OWNER": (str(rt.hold_owner_line(
            {"hold": {"released_at": None}, "concurrency_group": "irreversible:ship"}, job_id=5)),),
        "LINE_CHECK_HOLD": tuple(row[0] for row in cli._hold_rows(health)),
        "LINE_DOCTOR_HOLD": tuple(core_doctor.hold_line(h) for h in parse_holds(holds) or ()),
        "LINE_ETA_HELD": (cli._fmt_eta_row(held, None),),
        "LINE_ETA_AHEAD": (cli._fmt_eta_row(held, 2),),
        "LINE_TOP_EMPTY_HELD": (top_empty,),
    }  # fmt: skip
    # 반대쪽 — 이 글이 나오면 안 되는 자리(판정이 0 을 기대한다)
    assert var("LINE_BACK_GOES_ON") not in str(back_cancel), back_cancel
    assert var("LINE_GRACE_GOES_ON") not in str(fresh_cancel), fresh_cancel
    assert var("LINE_ETA_AHEAD") not in cli._fmt_eta_row(held, None)
    assert var("LINE_LOST_CONTACT") in cli._fmt_eta_row(grace_row, None)
    assert var("LINE_GRACE_CANCEL") in cli._fmt_eta_row({**grace_row, "state": "cancelling"}, None)
    return got


#: 하네스가 실기에서 찾는 사람 줄(CLI) — 이름마다 진짜 생산자의 글이 있다
LINE_VARS = (
    "LINE_LOST_CONTACT", "LINE_GRACE_GOES_ON", "LINE_GRACE_CANCEL", "LINE_CANCEL_REQUESTED",
    "LINE_BACK_GOES_ON", "LINE_BACK_CANCEL", "LINE_LATE", "LINE_HELD_WAIT", "LINE_HOLD_OWNER",
    "LINE_CHECK_HOLD", "LINE_DOCTOR_HOLD", "LINE_ETA_HELD", "LINE_ETA_AHEAD",
    "LINE_TOP_EMPTY_HELD",
)  # fmt: skip
#: 웹 화면에서 찾는 글과 값 — app.js · i18n.js 를 node 로 불러 견준다
WEB_VARS = (
    "WEB_GRACE_GOES_ON", "WEB_GRACE_CANCEL", "WEB_STUCK_MAX", "WEB_AND_MORE_PRE",
    "WEB_AND_MORE_POST", "WEB_NOTHING_ELSE", "WEB_EMPTY_HELD", "WEB_EMPTY_HINT",
    "WEB_LANE_UNHEARD", "WEB_LANE_LIVE",
)  # fmt: skip


def test_pr4_pr3_lines_match_the_producer_once_pr3_is_here() -> None:
    _need_pr3()
    from remote_ci_monitor.core.model import REASON_WORKER_UNREACHABLE_GRACE

    assert var("PR3_REASON") == REASON_WORKER_UNREACHABLE_GRACE
    produced = _produced_cli_lines()
    assert set(produced) == set(LINE_VARS)
    for name, texts in produced.items():
        want = var(name)
        assert want and texts, name
        for text in texts:
            assert want in text, f"{name}='{want}' is not in what the producer writes: '{text}'"


def test_pr4_the_harness_greps_through_the_line_variables_not_copies() -> None:
    """줄 변수는 한 자리에서만 정한다. grep 과 판정은 그 변수를 쓴다(값이 바뀌면 시험이 본다)."""
    text = script_text()
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    for name in (*LINE_VARS, *WEB_VARS):
        value = var(name)
        uses = [ln for ln in code if f"${name}" in ln or f"${{{name}" in ln]
        assert uses, f"the harness defines {name} but never uses it"
        if len(value) < 12:
            continue  # 짧은 값(「 more」 · 「run」)은 다른 낱말에도 들어 있다 — 쓰임만 본다
        # 다른 변수의 정의(더 긴 웹 문장이 CLI 조각을 품는다)는 베낀 것이 아니다
        copies = [ln for ln in code if value in ln and not re.match(r'[A-Z][A-Z0-9_]*="', ln)]
        assert not copies, f"'{value}' is copied instead of using ${name}: {copies}"
    assert "PR3_LINES" not in text  # 옛 묶음 — 손으로 베낀 줄 목록이었다


_WEB_PROBE = """
globalThis.window = globalThis;
const rcm = require(process.argv[1]);
const I = require(process.argv[2]);
const holds = JSON.parse(process.argv[3]);
const G = "worker_unreachable_grace";
const st = { schema_version: 1, generated_at: "2026-11-27T09:00:00Z",
  server: { lanes: 0, workers: [] }, pools: [{ name: "default", queue: [], recent: [] }],
  irreversible_holds: holds };
const row = (p) => Object.assign({ id: 7, state: "running", reason: G, ran_on: "rl28",
  started_at: "2026-11-27T08:59:30Z", lane: 1,
  estimate: { elapsed_seconds: 30, expected_seconds: 600, source: "preset", finish_at: null }
}, p || {});
const line = (i) => ({ jobId: i, reason: "needs_action", text: "x" + i, rank: 0 });
const now = Date.parse("2026-11-27T09:00:00Z");
const out = {
  grace: rcm.reasonText(row(), st, now, "en").text,
  cancel: rcm.reasonText(row({ state: "cancelling" }), st, now, "en").text,
  lane_unheard: rcm.laneClass(row()),
  lane_cancel: rcm.laneClass(row({ state: "cancelling" })),
  lane_live: rcm.laneClass(row({ reason: "running" })),
  bar_unheard: rcm.progressBarHtml(row(), "en", true),
  bar_live: rcm.progressBarHtml(row({ reason: "running" }), "en", true),
  band_over: rcm.stuckBand({ kind: "list", lines: [1, 2, 3, 4, 5].map(line) }, "en"),
  band_all: rcm.stuckBand({ kind: "list", lines: [line(1), line(2)] }, "en"),
  empty_held: I.t("en", rcm.queueEmptyKey(st) || "queue.empty"),
  empty_hint: I.t("en", "queue.empty_hint"),
  empty_free: rcm.queueEmptyKey(Object.assign({}, st, { irreversible_holds: [] })),
};
process.stdout.write(JSON.stringify(out));
"""


def test_pr4_web_strings_and_the_needs_a_look_band_come_from_app_js() -> None:
    """웹에서 찾는 글 · 레인 · 「확인이 필요한 작업」 칸의 줄 수와 꼬리를 app.js 함수에서 읽는다."""
    _need_pr3()
    import json

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed — the web half runs in tests/web (node --test)")
    r = subprocess.run(
        [node, "-e", _WEB_PROBE, str(SRC / "web" / "app.js"), str(SRC / "web" / "i18n.js"),
         json.dumps(_produced_holds())],
        capture_output=True, text=True, timeout=20, check=True,
    )  # fmt: skip
    got = json.loads(r.stdout)
    assert var("WEB_GRACE_GOES_ON") in got["grace"], got["grace"]
    assert var("WEB_GRACE_CANCEL") in got["cancel"], got["cancel"]
    assert var("WEB_GRACE_GOES_ON") not in got["cancel"], got["cancel"]
    assert got["lane_unheard"] == got["lane_cancel"] == var("WEB_LANE_UNHEARD"), got
    assert got["lane_live"] == var("WEB_LANE_LIVE"), got
    # 도우미의 행 JS 가 읽는 표지 — 듣는 행은 시간 눈금 + 틱, 끊긴 행은 막대만(눈금 · 틱 없음)
    assert 'data-basis="time"' in got["bar_live"] and 'data-tick="progress"' in got["bar_live"]
    assert 'class="pwrap"' in got["bar_unheard"], got["bar_unheard"]
    assert "data-tick" not in got["bar_unheard"] and 'data-basis="time"' not in got["bar_unheard"]
    cap = int(var("WEB_STUCK_MAX"))
    over = got["band_over"]
    assert len(over["lines"]) == cap, over
    assert over["tail"] == [var("WEB_AND_MORE_PRE") + str(5 - cap) + var("WEB_AND_MORE_POST")], over
    assert got["band_all"]["tail"] == [var("WEB_NOTHING_ELSE")], got["band_all"]
    assert var("WEB_EMPTY_HELD") in got["empty_held"], got["empty_held"]
    assert got["empty_hint"] == var("WEB_EMPTY_HINT") and got["empty_free"] is None, got
    helper_js = script_text()
    for needle in ("getElementById('job-", ".pwrap[data-tick]", "data-basis", "td.elapsed",
                   "[data-stuck] .sl.muted"):  # fmt: skip
        assert needle in helper_js, needle


# ── 판정: 모르는 것을 PASS 로 적지 않는다 ─────────────────────────────────────

COMMON = {"G": "90", "T": "10", "HB": "1", "grace_j": "3", "pr2": "1", "pr3": "1"}
#: 띠 높이(80px 이하)는 RL28 화면만 본다 — 다른 화면의 모범 사실에는 넣지 않는다
WEB_NO_BAND = {
    "web": "ok",
    **{f"{lang}_{k}": v for lang in ("en", "ko")
       for k, v in (("width", "390"), ("errors", "0"), ("raw", "0"))},
}  # fmt: skip
WEB_OK = {**WEB_NO_BAND, "en_band_h": "52", "ko_band_h": "64"}
GOOD: dict[str, dict[str, str]] = {
    "RL1": {
        "state_at_stop": "running", "self_stop_after": "63.2", "death_epoch": "1000.0",
        "finished_epoch": "1030.0", "final_state": "lost", "summary_code": "worker_unreachable",
        "failed_step": "null", "last_step": "null", "wait_rc": "3", "hold_at_close": "1",
        "hold_released_after_return": "1", "lost_events": "1",
    },
    "RL2": {
        "x_dead_while_down": "1", "y_alive_while_down": "1", "y_state_after_start": "running",
        "x_final": "lost", "x_code": "worker_self_stopped", "x_lost_events": "1",
        "x_wait_rc": "3", "x_failed_step": "null", "x_hold": "0", "y_alive_end": "1",
    },
    "RL5": {
        "final_state": "lost", "summary_code": "worker_unreachable", "wait_rc": "3",
        "alive_after_close": "1", "local_log_grew": "1", "hold": "0", "alive_after_return": "1",
        "late_count": "1", "late_outcome": "succeeded", "state_after_late": "lost",
        "wait_rc_after_late": "3", "late_line": "1",
    },
    "RL8": {
        "cancel_rc_x": "0", "cancel_rc_y": "0", "x_cancelling": "cancelling",
        "y_cancelling": "cancelling", "x_dead_after": "62", "x_final": "lost",
        "x_code": "worker_unreachable", "x_wait_rc": "3", "y_final": "lost",
        "y_code": "worker_unreachable", "y_wait_rc": "3", "y_dead_after_return": "1",
        "x_hold_at_close": "1", "x_hold_released": "1", "y_late_count": "1",
        "y_state_end": "lost", "x_state_end": "lost",
    },
    "RL10": {
        "a_exit": "0", "x_dead": "1", "x_final": "lost", "x_code": "worker_replaced",
        "x_wait_rc": "3", "x_hold": "0", "b_registered": "1", "registers": "2",
    },
    "RL15": {
        "orphan_alive_after_kill": "1", "orphan_dead_after_restart": "1", "orphan_line": "1",
        "z_after_orphan": "1", "plain_final": "lost", "ship_cmd_dead": "1", "ship_final": "lost",
        "ship_hold": "0", "leader_alive": "1", "leader_line": "1", "leader_final": "lost",
        "leader_hold": "1", "identity": "ps",
    },
    # RL18 — 다섯 곳마다 「읽었다」는 증거(종료 0 · HTTP 200 · 가린 값이나 알려진 글 하나 이상)
    "RL18": {
        "wait_rc": "0", "masked_seen": "200", "in_log": "0", "in_rcm_logs": "0",
        "in_web_log": "0", "in_job_doc": "0", "in_sse": "0",
        "rcm_logs_rc": "0", "rcm_logs_seen": "200", "web_log_status": "200",
        "web_log_seen": "200", "job_doc_status": "200", "job_doc_seen": "1", "sse_seen": "3",
        "log_equals_masked": "1", "resend_seen": "1", "log_complete": "true",
    },
    "RL28": {
        "state_during": "running", "finish_at": "null", "health_stable": "1",
        "wait_timeout_rc": "3", "wait_timeout": "8", "wait_timeout_secs": "8.4",
        "reason": "worker_unreachable_grace", "top_text": "1", "top_raw": "0",
        "lost_line_delay": "12.0", "back_line_delay": "2.0", "eta_text": "1",
    },
    "RL28-web": {
        **WEB_OK, "found_grace_said": "1", "found_cancel_said": "1",
        **{f"{lang}_{role}_{k}": v for lang in ("en", "ko") for role in ("grace", "cancel")
           for k, v in (("found", "1"), ("lane", "1"), ("ticks", "0"), ("bars", "1"),
                        ("bar_ticks", "0"), ("scaled", "0"), ("el_same", "1"))},
        **{f"{lang}_live_{k}": v for lang in ("en", "ko")
           for k, v in (("found", "1"), ("lane", "1"), ("ticks", "2"), ("bars", "1"),
                        ("bar_ticks", "1"), ("scaled", "1"), ("el_same", "0"))},
    },
    "RL28-cancel": {
        "cancel_rc": "0", "state_after_cancel": "cancelling",
        "reason_after_cancel": "worker_unreachable_grace", "top_cancel": "1", "eta_cancel": "1",
        "fresh_line": "1", "fresh_goes_on": "0", "bg_cancel_line": "1", "bg_back_goes_on": "0",
        "bg_back_cancel": "1", "final": "cancelled",
    },
    "RL29": {
        "codes": "worker_unreachable worker_self_stopped",
        **{f"{c}_{k}": v for c in ("worker_unreachable", "worker_self_stopped")
           for k, v in (("job", "5"), ("rc", "3"), ("failed_step", "null"), ("notify", "1"),
                        ("line", "1"))},
    },  # fmt: skip
    # RL29-web — 끝마다 두 언어의 문장(i18n.js 가 그린 것)을 그 언어 화면에서 찾는다
    "RL29-web": {
        **WEB_NO_BAND, "codes": "worker_unreachable", "needles": "worker_unreachable",
        "needle_en_worker_unreachable": "worker rl1b unreachable for 90s",
        "needle_ko_worker_unreachable": "워커 rl1b가 90초 동안 응답 없음",
        "found_en_worker_unreachable": "1", "found_ko_worker_unreachable": "1",
    },
    "RL34": {
        **{f"{m}_{k}": v for m, gj, thr, stop in (("d", "3", "62", "62.6"), ("b", "13", "52", "53"))
           for k, v in (("state_at_stop", "running"), ("grace_j", gj), ("threshold", thr),
                        ("stop_after", stop), ("code", "worker_self_stopped"), ("wait_rc", "3"),
                        ("hold", "0"))},
        "b_lowered": "1",
    },  # fmt: skip
    "RL40": {
        "refused_secs": "38", "state_while_refused": "running", "wait_rc": "0",
        "final_state": "succeeded", "log_sha_match": "1", "hold": "0", "log_complete": "true",
    },
    "RL42": {
        "hold_rows": "1", "hold_cause": "worker_unreachable", "hold_events": "1",
        "same_group_state": "queued", "same_group_reason": "needs_action",
        "same_group_started": "null", "after_restart_hold": "1",
        "after_restart_same_group_state": "queued", "other_group_ran": "1",
        "plain_hold_rows": "0", "released_after_return": "1",
        "same_group_started_after_release": "1",
    },
    "RL43": {
        "a_detect": "0.27", "b_detect": "3.2", "d_dead": "3.5", "e_alive": "1",
        "a_final": "lost", "a_code": "worker_unreachable", "a_hold": "1", "b_hold": "1",
        "e_hold": "0", "d_state": "failed", "d_exit": "-9", "d_hold": "0",
        "d_state_after_cancel": "failed", "x7_exit": "7", "x7_wait_rc": "1", "k_exit": "-9",
        "release_rc": "0", "z_started": "1",
    },
    "RL45": {
        "z_state": "queued", "z_reason": "needs_action", "z_action": "irreversible_hold",
        "z_blocked_by_ok": "1", "z_remaining": "null", "z_finish_at": "null",
        "status_holds_ok": "1", "health_holds_ok": "1", "health_ok_same": "1",
        "wait_action_rc": "4", "wait_timeout_rc": "3", "check_rc_same": "1",
        "doctor_rc_same": "1", "client_release_rc": "2", "admin_release_rc": "0",
        "admin_name": "acceptance-admin", "released_by": "acceptance-admin",
        "release_reason_ok": "1", "nohold_release_rc": "1", "wait_line": "1",
        "check_lines": "2", "doctor_lines": "1", "doctor_json_holds": "1",
        "owner_line": "1", "eta_held": "1", "eta_no_ahead": "1", "top_empty_held": "1",
    },
    # 홀드 둘 + 막힌 대기 잡 둘 = 4줄 이상 → 띠는 세 줄 + 「and 1 more」, 「nothing else」는 없다
    "RL45-web": {
        **WEB_NO_BAND, "found_hold_a": "1", "found_hold_b": "1", "en_chips": "1",
        "stuck_min": "4", "stuck_max": "3", "en_stuck_n": "3", "en_more_n": "1",
        "en_nothing_else": "0",
    },
    "RL45-empty": {
        **WEB_NO_BAND, "found_empty_held": "1", "en_empty_hint": "0", "queue_empty": "1",
        "hold_open": "1",
    },
    "RL50": {
        "held": "1", "refused": "1", "x_state": "running", "x_alive": "1", "x_hold": "0",
        "second_state": "queued",
    },
    "RL51": {
        "b1_gone": "3.6", "b2_gone": "3.3", "c_gone": "4.0", "b1_state": "cancelled",
        "c_state": "cancelled",
    },
    "F78": {
        "a_detect": "0.26", "b_detect": "3.27", "sup_cpu_30s": "0.02", "python_I_start": "0.03",
        "ps_cost": "0.013", "true_direct": "0.002", "true_supervised": "0.035",
    },
}  # fmt: skip
#: 한 사실을 틀리게 바꾸면 FAIL 이어야 한다(그 판정이 그 사실을 정말 본다)
BAD: dict[str, list[dict[str, str]]] = {
    # RL1 — 명령이 죽은 시각은 DB finished_at 보다 늦지 않다(명세: 차이 ≥ 0, 허용치 없음)
    "RL1": [{"state_at_stop": "lost"}, {"summary_code": "cancelled"}, {"death_epoch": "1040"},
            {"wait_rc": "1"}, {"hold_released_after_return": "0"}, {"lost_events": "2"},
            {"self_stop_after": "90"}, {"finished_epoch": "999.9"}],
    "RL2": [{"x_lost_events": "2"}, {"y_state_after_start": "lost"}],
    "RL5": [{"late_count": "2"}, {"state_after_late": "succeeded"}, {"alive_after_close": "0"}],
    "RL8": [{"x_code": "cancel_unconfirmed"}, {"y_final": "cancelled"}, {"y_late_count": "2"}],
    "RL10": [{"registers": "5"}, {"a_exit": "1"}, {"x_code": "worker_unreachable"}],
    "RL15": [{"leader_hold": "0"}, {"z_after_orphan": "0"}, {"ship_hold": "1"}],
    "RL18": [{"in_sse": "1"}, {"in_log": "2"}, {"log_equals_masked": "0"},
             {"in_rcm_logs": "1", "rcm_logs_rc": "1"}],
    "RL28": [{"finish_at": "2026-11-27T00:00:00Z"}, {"top_raw": "1"}, {"wait_timeout_secs": "30"},
             {"eta_text": "0"}],
    "RL28-web": [{"ko_band_h": "96"}, {"en_errors": "1"}, {"ko_width": "500"},
                 {"en_grace_ticks": "1"}, {"ko_cancel_lane": "0"}, {"en_grace_el_same": "0"},
                 {"en_grace_bar_ticks": "1"}, {"ko_grace_scaled": "1"}, {"found_cancel_said": "0"},
                 {"en_grace_found": "0"}, {"en_grace_bars": "0"}],
    "RL28-cancel": [{"top_cancel": "0"}, {"fresh_goes_on": "1"}, {"bg_back_goes_on": "1"},
                    {"final": "succeeded"}, {"bg_cancel_line": "0"}, {"eta_cancel": "0"},
                    {"reason_after_cancel": "cancelling"}],
    "RL29": [{"worker_unreachable_notify": "2"}, {"worker_self_stopped_rc": "1"}],
    "RL29-web": [{"found_en_worker_unreachable": "0"}, {"found_ko_worker_unreachable": "0"}],
    # RL34 — 워커가 적은 값은 int(wgap) 이고 wgap ≥ 문턱이다: 문턱 아래면 일찍 멈춘 것이다
    "RL34": [{"d_stop_after": "95"}, {"b_code": "cancelled"}, {"b_lowered": "0"},
             {"d_threshold": "61"}],
    "RL40": [{"final_state": "lost"}, {"log_sha_match": "0"}],
    "RL42": [{"same_group_started": "2026-11-27T00:00:00Z"}, {"plain_hold_rows": "1"}],
    "RL43": [{"a_detect": "1.5"}, {"b_detect": "0.4"}, {"e_alive": "0"}, {"d_exit": "0"},
             {"a_hold": "0"}, {"b_hold": "0"}, {"d_hold": "1"}, {"x7_exit": "0"},
             {"x7_wait_rc": "0"}, {"d_dead": "5.2"}],
    "RL45": [{"client_release_rc": "0"}, {"wait_action_rc": "3"}, {"health_ok_same": "0"},
             {"owner_line": "0"}, {"eta_no_ahead": "0"}, {"eta_held": "0"},
             {"top_empty_held": "0"}],
    "RL45-web": [{"en_more_n": "0"}, {"en_nothing_else": "1"}, {"en_stuck_n": "4"},
                 {"found_hold_b": "0"}, {"en_more_n": "0", "en_stuck_n": "2"}],
    "RL45-empty": [{"found_empty_held": "0"}, {"en_empty_hint": "1"}],
    "RL50": [{"x_state": "lost"}, {"second_state": "running"}],
    # RL51 — (b) 워커 kill -9 는 첫 SIGTERM(감지 ≤ 0.31) + grace_J + 0.5, (c) 도 (b) 와 같은 경계
    "RL51": [{"b1_gone": "6.5"}, {"b2_gone": "5"}, {"b2_gone": "3.9"}, {"c_gone": "5.5"}],
    "F78": [{"a_detect": "0.6"}, {"true_supervised": "0.5"}],
}  # fmt: skip


def test_pr4_every_live_step_has_a_judge_that_is_never_pass_on_nothing(tmp_path: Path) -> None:
    mod = helper(tmp_path)
    live = {s[0] for s in steps() if s[1] == "live"}
    judged = set(mod.JUDGES)
    assert live <= judged, sorted(live - judged)
    for step in sorted(judged):
        assert mod.judge(step, {}).startswith("UNKNOWN "), step
        assert mod.judge(step, dict(COMMON)).startswith("UNKNOWN "), step


@pytest.mark.parametrize("step", sorted(GOOD))
def test_pr4_judge_passes_good_facts_and_fails_each_bad_one(tmp_path: Path, step: str) -> None:
    mod = helper(tmp_path)
    good = {**COMMON, **GOOD[step]}
    got = mod.judge(step, good)
    assert got.startswith("PASS "), got
    for change in BAD[step]:
        got = mod.judge(step, {**good, **change})
        assert got.startswith("FAIL "), (change, got)
    # 사실 하나가 빠지면 PASS 가 아니다(UNKNOWN)
    for key in GOOD[step]:
        if key in ("admin_name", "identity", "web"):
            continue
        got = mod.judge(step, {k: v for k, v in good.items() if k != key})
        assert not got.startswith("PASS "), (key, got)


def test_pr4_judge_without_pr2_or_pr3_is_unknown_not_pass(tmp_path: Path) -> None:
    mod = helper(tmp_path)
    for step, flag in (("RL5", "pr2"), ("RL18", "pr2"), ("RL40", "pr2"), ("RL28", "pr3"),
                       ("RL45", "pr3"), ("RL29", "pr3"), ("RL28-cancel", "pr3"),
                       ("RL45-empty", "pr3")):  # fmt: skip
        got = mod.judge(step, {**COMMON, **GOOD[step], flag: "0"})
        assert got.startswith("UNKNOWN ") and "this build has no" in got, (step, got)


def test_pr4_web_judge_says_unknown_without_chrome(tmp_path: Path) -> None:
    mod = helper(tmp_path)
    for step in ("RL28-web", "RL29-web", "RL45-web", "RL45-empty"):
        for web in ("no-chrome", "failed RuntimeError: x", ""):
            got = mod.judge(step, {**COMMON, **GOOD[step], "web": web})
            assert got.startswith("UNKNOWN "), (step, web, got)


def test_pr4_needs_a_look_with_hidden_lines_says_so(tmp_path: Path) -> None:
    # 「확인이 필요한 작업」 4줄 이상 — 띠는 세 줄 + 「and N more」(PR3 P6). 세 줄에 「nothing else
    # needs a look」이면 FAIL(숨긴 줄을 없다고 말한다), 「and 1 more」면 PASS 다. 셋 이하면
    # 이 경우를 만들지 못한 것이라 UNKNOWN 이다
    mod = helper(tmp_path)
    base = {**COMMON, **GOOD["RL45-web"]}
    shown = "#4 group … · #5 group … · #6 needs action · nothing else needs a look"
    got = mod.judge("RL45-web", {**base, "en_more_n": "0", "en_nothing_else": "1",
                                 "en_stuck_text": shown})  # fmt: skip
    assert got.startswith("FAIL ") and "nothing else needs a look" in got, got
    assert mod.judge("RL45-web", base).startswith("PASS ")
    got = mod.judge("RL45-web", {**base, "stuck_min": "3"})
    assert got.startswith("UNKNOWN "), got
    # 줄이 다섯이면 「and 2 more」 — 세 줄 + 2 가 다섯을 다 센다
    assert mod.judge("RL45-web", {**base, "stuck_min": "5", "en_more_n": "2"}).startswith("PASS ")
    got = mod.judge("RL45-web", {**base, "stuck_min": "5", "en_more_n": "1"})
    assert got.startswith("FAIL "), got


def test_pr4_unreachable_row_without_a_ticking_control_row_is_unknown(tmp_path: Path) -> None:
    # 듣는 행(대조군)도 안 센다면 「끊긴 행은 안 센다」는 아무것도 말하지 않는다 — PASS 가 아니다
    mod = helper(tmp_path)
    base = {**COMMON, **GOOD["RL28-web"]}
    for change in ({"en_live_ticks": "0"}, {"ko_live_el_same": "1"}, {"en_live_bar_ticks": "0"},
                   {"en_live_scaled": "0"}, {"ko_live_lane": "0"}):  # fmt: skip
        got = mod.judge("RL28-web", {**base, **change})
        assert got.startswith("UNKNOWN ") and "control" in got, (change, got)
    got = mod.judge("RL28-web", {**base, "en_live_found": "0"})
    assert got.startswith("FAIL "), got


def test_pr4_new_pr3_facts_are_recorded_by_a_runner(tmp_path: Path) -> None:
    """판정이 읽는 사실마다 그 사실을 적는 줄이 하네스에 있다 — 판정만 있고 아무도 안 적으면
    실기는 늘 UNKNOWN 이다. 웹 행 사실은 도우미의 ROW_KEYS 가 적는다."""
    text = script_text()
    mod = helper(tmp_path)
    web = set(WEB_NO_BAND) | set(WEB_OK)
    asked = {
        "RL28": ("eta_text",),
        "RL28-web": ("found_grace_said", "found_cancel_said"),
        "RL28-cancel": tuple(GOOD["RL28-cancel"]),
        "RL45": ("owner_line", "eta_held", "eta_no_ahead", "top_empty_held"),
        "RL45-web": ("stuck_min", "stuck_max", "en_more_n", "en_nothing_else"),
        "RL45-empty": tuple(k for k in GOOD["RL45-empty"] if k not in web),
    }
    for step, keys in asked.items():
        for key in keys:
            found = re.search(rf"\bfact {re.escape(step)} {key}\b", text)
            assert found, f"no runner writes {step} {key}"
    rows = ("en_grace_", "en_live_")
    row_keys = {k.split("_", 2)[2] for k in GOOD["RL28-web"] if k.startswith(rows)}
    assert row_keys - {"lane"} <= set(mod.ROW_KEYS), sorted(row_keys - set(mod.ROW_KEYS))
    assert re.search(r'fact RL28-web "\$\{lang\}_\$\{role\}_lane"', text)
    assert re.search(r'web rl28 "grace=\$y,cancel=\$y2,live=\$y3"', text)


def test_pr4_facts_file_and_absent_values_read_as_not_observed(tmp_path: Path) -> None:
    mod = helper(tmp_path)
    f = tmp_path / "RL10.txt"
    lines = "x_code=worker_replaced\nx_code=<absent>\nregisters=2\nweird line\n"
    f.write_text(lines, encoding="utf-8")
    facts = mod.read_facts(str(f))
    assert facts["x_code"] == "<absent>" and facts["registers"] == "2"
    got = mod.judge("RL10", {**COMMON, **GOOD["RL10"], "x_code": "<absent>"})
    assert got.startswith("UNKNOWN ") and "summary_code: not observed" in got, got
    got = mod.judge("RL10", {**COMMON, **GOOD["RL10"], "x_dead": "timeout"})
    assert got.startswith("UNKNOWN "), got


# ── batch-3: 첫 실기(2026-11-27)의 RL15 → RL43 줄줄이 UNKNOWN · RL29-web FAIL ─────────────


def _written_server_toml(tmp_path: Path) -> Path:
    """하네스가 실기에서 쓰는 server.toml(self-test 가 아닌 쪽)을 시험 폴더에 쓴다."""
    base = tmp_path / "base"
    for d in ("bin", "marks", "data"):
        (base / d).mkdir(parents=True, exist_ok=True)
    conf = base / "server.toml"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "M5TB_PY": sys.executable,
        "HOME": str(tmp_path),
        "CONF": str(conf),
        "P": "8797",
        "B": str(base),
        "DATA": str(base / "data"),
        "MARKS": str(base / "marks"),
    }
    code = 'source "$1" && write_server_toml 0 && test -s "$CONF"'
    r = subprocess.run(
        ["bash", "-c", code, "bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return conf


def test_rl15_two_lane_worker_is_not_held_by_the_load_gate(tmp_path: Path) -> None:
    # RL15 — 레인 둘인 워커의 둘째 레인은 서버 부하 게이트(server.py admit · core/admission.py
    # decide: 30초 쿨다운 · CPU 창)를 지난다. 맥미니가 바쁘면(load 12, 다른 게이트) 둘째 잡이 30초
    # 안에 시작하지 않아 RL15 가 「jobs did not start」였다. 하네스는 게이트를 시험하지 않는다
    from remote_ci_monitor.config import load_server_config

    cfg = load_server_config(_written_server_toml(tmp_path), check_tools=False)
    assert cfg.server.admission == "always", cfg.server.admission


def test_rl29_recent_list_holds_every_job_a_full_run_ends(tmp_path: Path) -> None:
    # RL29-web — 웹은 최근 잡을 `recent_count`(기본 8)개까지만 받고 다섯을 보인다. 전체 실기는
    # 잡을 서른 개 남짓 끝내므로 RL10 · RL34 · claim-lost 의 lost 잡이 화면에 없어 0 을 셌다
    from remote_ci_monitor.config import load_server_config

    cfg = load_server_config(_written_server_toml(tmp_path), check_tools=False)
    submits = len(re.findall(r"\$\(submit ", script_text()))
    assert cfg.server.recent_count >= 2 * submits, (cfg.server.recent_count, submits)


def test_rl29_web_page_text_is_read_with_every_recent_row_shown(tmp_path: Path) -> None:
    # RL29-web — 「show N more」를 펴고 글을 읽는다. 단추와 그 동작은 app.js 의 것이다
    import json

    app = (SRC / "web" / "app.js").read_text(encoding="utf-8")
    assert "data-more-recent" in app and "state.showAllRecent = !state.showAllRecent" in app
    mod = helper(tmp_path)
    js = getattr(mod, "EXPAND_RECENT_JS", None)
    assert js is not None, "the web helper never expands the recent list"
    assert "[data-more-recent]" in js and "click()" in js, js
    body = SCRIPT.read_text(encoding="utf-8")
    start = body.index("def cmd_web(")
    web = body[start : body.index("\ndef ", start + 1)]
    assert web.index("EXPAND_RECENT_JS") < web.index("document.body.innerText"), web
    node = shutil.which("node")
    if node is not None:
        r = subprocess.run([node, "-e", _JS_PARSE, json.dumps({"expand": js})],
                           capture_output=True, text=True, timeout=20, check=True)  # fmt: skip
        assert json.loads(r.stdout) == [], r.stdout


#: reset_world 를 대역으로 부른다 — 서버 · 워커 · 중계 없이 무엇을 부르는지만 적는다
_RESET_STUB = r"""
source "$1"
LOG=$2
OUT=$3
MARKS=$3/marks
URL=http://127.0.0.1:1
CTOK=-
ATOK=-
relay_do() { :; }
stop_ours() { echo "stop $1" >>"$LOG"; }
client() { echo "client $*" >>"$LOG"; }
admin() { echo "admin $*" >>"$LOG"; }
h() {
  case "$1" in
    openjobs) echo "${OPEN:-}" ;;
    holds) echo "" ;;
    waitstate) echo "cancelled" ;;
    job) echo "rl15-ship" ;;
    *) echo "h $*" >>"$LOG" ;;
  esac
}
WORKER_PIDS=(111)
reset_world
"""


def test_rl15_reset_between_steps_cancels_jobs_left_in_the_queue(tmp_path: Path) -> None:
    # RL15 → RL1 · RL8 · RL2 · RL43 — 한 단계가 남긴 대기 잡(#19 rl15-ship)을 다음 단계의 첫
    # 워커(rl1a)가 집어 그 뒤 단계마다 잡이 하나씩 밀렸다. reset_world 는 아무것도 취소하지 않았다
    log, out = tmp_path / "calls.log", tmp_path / "out"
    (out / "marks").mkdir(parents=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "M5TB_PY": sys.executable,
           "HOME": str(tmp_path), "OPEN": "19 21"}  # fmt: skip
    argv = ["bash", "-c", _RESET_STUB, "bash", str(SCRIPT), str(log), str(out)]
    r = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=15, check=False)
    assert r.returncode == 0, r.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    cancels = [c for c in calls if re.match(r"^(client|admin) cancel ", c)]
    assert any(" 19" in c for c in cancels) and any(" 21" in c for c in cancels), calls
    # 워커를 먼저 멈춘다 — 멈추기 전에 취소하면 도는 잡이 cancelled 로 끝나 단계의 lost 가 바뀐다
    assert calls.index("stop 111") < calls.index(cancels[0]), calls
    reset_log = out / "reset.log"
    said = reset_log.read_text(encoding="utf-8") if reset_log.exists() else ""
    assert "#19" in said and "#21" in said, said


def test_rl15_open_jobs_come_from_the_status_pools(tmp_path: Path) -> None:
    mod = helper(tmp_path)
    fn = getattr(mod, "open_job_ids", None)
    assert fn is not None, "the helper cannot list the jobs a step left open"
    pool_keys = _dict_keys(SRC / "core" / "status.py", "pool_json")
    assert {"queue", "scheduled"} <= pool_keys, sorted(pool_keys)
    assert "pools" in _dict_keys(SRC / "core" / "status.py", "status_json")
    doc = {"pools": [
        {"name": "m5tb", "queue": [{"id": 19, "state": "queued"}, {"id": 18, "state": "running"}],
         "scheduled": [{"id": 30}]},
        {"name": "default", "queue": [], "scheduled": None},
    ]}  # fmt: skip
    assert fn(doc) == [18, 19, 30]
    assert fn({"pools": [{"queue": None}]}) is None  # 못 읽은 큐는 빈 큐가 아니다
    assert fn({}) is None


def test_rl15_a_step_whose_jobs_did_not_start_says_why(tmp_path: Path) -> None:
    # RL15 · RL1 · RL8 · RL2 · RL43 — 「jobs did not start」만으로는 하네스 탓인지 제품 탓인지
    # 모른다. 그 잡이 어디서 돌았나 · 대기 사유 · 그 단계 워커 레인의 막힘을 한 줄로 말한다
    status_py = (SRC / "core" / "status.py").read_text(encoding="utf-8")
    assert '"hold_code": w.hold_code' in status_py and '"reason": row.reason' in status_py
    assert {"ran_on", "state"} <= set(var("JOB_KEYS").split())
    mod = helper(tmp_path)
    fn = getattr(mod, "why_not_started", None)
    assert fn is not None, "the helper has no why_not_started"
    status = {
        "server": {"workers": [
            {"worker": "rl15", "lane": 1, "state": "busy", "job_id": 18, "hold_code": None},
            {"worker": "rl15", "lane": 2, "state": "held", "job_id": None, "hold_code": "cpu_busy"},
        ]},
        "pools": [{"queue": [{"id": 19, "state": "queued", "reason": "lane_held"}]}],
    }  # fmt: skip
    queued = {"id": 19, "preset": "rl15-ship", "state": "queued", "ran_on": None}
    level, text = fn([queued], status, ["rl15"])
    assert level == "FAIL" and text.startswith("harness: "), (level, text)
    assert "#19 rl15-ship queued" in text and "lane_held" in text, text
    assert "rl15 lane 2 held (cpu_busy)" in text, text
    # 앞 단계의 남은 잡이 이 단계 워커를 가져갔다 — 하네스 탓
    stolen = {"id": 19, "preset": "rl15-ship", "state": "running", "ran_on": "rl1a"}
    level, text = fn([stolen], {"server": {"workers": []}, "pools": []}, ["rl15"])
    assert level == "FAIL" and "#19 rl15-ship running on rl1a, not on rl15" in text, text
    # 이유를 모르면 UNKNOWN 이고 아는 만큼 말한다
    level, text = fn([queued], {"server": {"workers": []}, "pools": []}, ["rl15"])
    assert level == "UNKNOWN" and "#19 rl15-ship queued" in text, text
    assert "rl15: not registered" in text, text
    level, text = fn([{"id": 19}], None, ["rl15"])
    assert level == "UNKNOWN" and "status unreadable" in text, text


def test_rl15_runners_report_unstarted_jobs_through_not_started() -> None:
    text = script_text()
    assert 'UNKNOWN "jobs did not start"' not in text
    for step, workers in (("RL15", "rl15"), ("RL1", "rl1a,rl1b"), ("RL8", "rl8a,rl8b"),
                          ("RL2", "rl2a,rl2b"), ("RL43", "rl43a,rl43b,rl43e")):  # fmt: skip
        assert re.search(rf"not_started {step} {workers} ", text), step


def test_rl29_every_runner_can_be_rerun_with_only() -> None:
    # RL29 — claim-lost(worker_restarted_without_job 을 만드는 단계)는 어느 행에도 없어서
    # `--only` 로 실패한 단계만 다시 돌리면 RL29 가 그 끝을 볼 수 없었다
    reachable: set[str] = set()
    for _id, kind, runners, _what in steps():
        if kind == "live":
            reachable |= set(runners.split("+"))
    every = var("ALL_RUNNERS").split()
    assert set(every) <= reachable, sorted(set(every) - reachable)
    r = sh("runners_for", "RL29")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["run_claim_lost", "run_lost_ends"], r.stdout


def test_rl15_orphan_death_is_timed_by_the_worker_line_not_by_the_harness_poll(
    tmp_path: Path,
) -> None:
    # RL15 — 재실기(2026-11-27 01:24)에서 새 워커는 641.949 에 고아 줄
    # 「orphan #8: stopped process group」을 적고 641.992 에 다음 잡을 집었다(제품은 맞다).
    # 하네스는 worker_start 가 등록을 확인한 뒤에야 waitdead 를 시작해 그 끝난 시각(now)을
    # 고아가 죽은 시각으로 써서 z_after_orphan 이 0 이었다
    text = script_text()
    start = text.index("run_orphan() {")
    body = text[start : text.index("\n}\n", start)]
    m = re.search(r't_dead=\$\(h firstline "\$W_LOG" "orphan #\$y: stopped process group"\)', body)
    assert m, "run_orphan does not take the orphan's end from the worker's own log line"
    mod = helper(tmp_path)
    log = tmp_path / "w.log"
    log.write_text(
        "1795742641.971 [rcm worker] rcm worker rl15 · pool m5tb\n"
        "1795742641.949 [rcm worker] orphan #8: stopped process group 59099\n",
        encoding="utf-8",
    )
    r = subprocess.run([sys.executable, "-I", mod.__file__, "firstline", str(log),
                        "orphan #8: stopped process group"],
                       capture_output=True, text=True, timeout=15, check=True)  # fmt: skip
    assert r.stdout.strip() == "1795742641.949", r.stdout


def test_rl45_web_facts_never_put_a_case_pattern_inside_command_substitution() -> None:
    # RL45-web — macOS 의 /bin/bash 3.2 는 `$(case … in pat) …)` 에서 패턴의 `)` 를 치환의 끝으로
    # 읽는다. 재실기에서 found_hold_a · found_hold_b · en_nothing_else 가 문법 오류 글이 되어
    # RL45-web 이 FAIL 이었다. `bash -n` 은 치환 속을 늦게 읽어 이것을 못 잡는다
    bad = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(script_text().splitlines(), 1)
        if re.search(r"\$\(\s*case\b", line)
    ]
    assert not bad, bad


def test_f78_local_measurements_run_when_only_picks_rl43() -> None:
    # F78 — `--only` 의 runners_for 는 함수를 줄마다 내는데 main 은 `" $runners "` 에서 공백으로
    # 둘러싼 이름을 찾았다. 그래서 `--only RL43` 이면 F78 의 이 머신 값을 재지 않아 UNKNOWN 이었다
    picked = sh("runners_for", "RL43")
    assert picked.returncode == 0, picked.stderr
    r = sh("has_runner", picked.stdout, "run_kill_worker")
    assert r.returncode == 0, (picked.stdout, r.stderr)
    assert sh("has_runner", picked.stdout, "run_orphan").returncode == 1
    text = script_text()
    main = text[text.index("main() {") :]
    assert re.search(r'has_runner "\$runners" run_kill_worker && run_f78_local', main), main[:200]


# ── 보고서 docs/acceptance/reports/2026-11-27-m5tb-acceptance.md (§5 PR4 행 · §6 의 10 · 19) ──
REPORT = ROOT / "docs" / "acceptance" / "reports" / "2026-11-27-m5tb-acceptance.md"
VERDICTS = ("PASS", "FAIL", "UNKNOWN", "SKIP")


def report_text() -> str:
    assert REPORT.is_file(), f"{REPORT.relative_to(ROOT)} is missing"
    return REPORT.read_text(encoding="utf-8")


def report_rows() -> dict[str, list[str]]:
    """보고서 표에서 첫 칸이 판정 ID 인 행 → 나머지 칸(ID 는 백틱 없이 쓴다)."""
    rows: dict[str, list[str]] = {}
    for line in report_text().splitlines():
        m = re.match(r"^\| ((?:RL\d+|F78)(?:-[a-z0-9]+)?) \| (.*) \|$", line)
        if m:
            rows.setdefault(m.group(1), []).append(m.group(2))
    return rows


def skip_ids() -> set[str]:
    """실기 단계가 SKIP 으로 내는 판정 ID — 하네스의 `verdict <ID> SKIP` 줄."""
    return set(re.findall(r"verdict (\S+) SKIP ", script_text()))


def test_pr4_report_gives_every_verdict_the_harness_prints_a_row(tmp_path: Path) -> None:
    # 판정 ID 는 하네스(판정 표 JUDGES · SKIP 줄 · 단계 표의 live 행)에서 읽는다
    # — 손으로 베끼지 않는다
    mod = helper(tmp_path)
    want = set(mod.JUDGES) | skip_ids() | {s[0] for s in steps() if s[1] == "live"}
    rows = report_rows()
    missing = sorted(want - set(rows))
    assert missing == [], f"the report has no row for {missing}"
    no_verdict = sorted(
        i for i in want if not any(re.search(rf"\b{v}\b", c) for c in rows[i] for v in VERDICTS)
    )
    assert no_verdict == [], f"rows without a verdict word: {no_verdict}"


def test_pr4_report_skip_rows_name_tests_that_exist() -> None:
    rows = report_rows()
    for sid in sorted(skip_ids()):
        cells = " ".join(rows.get(sid, []))
        assert "SKIP" in cells, (sid, cells)
        assert re.search(r"test_\w+", cells), f"{sid}: the SKIP row names no covering test"
    defs: set[str] = set()
    for p in (ROOT / "tests").glob("test_*.py"):
        defs |= set(re.findall(r"^def (test_\w+)", p.read_text(encoding="utf-8"), re.M))
    unknown = []
    for m in re.finditer(r"(test_[A-Za-z0-9_]+)(\.py|\*)?", report_text()):
        name, tail = m.group(1), m.group(2)
        if tail == ".py":
            ok = (ROOT / "tests" / f"{name}.py").is_file()
        elif tail == "*":
            ok = any(d.startswith(name) for d in defs)
        else:
            ok = name in defs
        if not ok:
            unknown.append(m.group(0))
    assert unknown == [], f"the report names tests that do not exist: {sorted(set(unknown))}"


def test_pr4_report_has_no_personal_paths_or_private_addresses() -> None:
    text = report_text()
    assert "<HOME>" in text
    bad: list[str] = []
    for pat in (
        r"/Users/",
        r"/home/[a-z]",
        r"~/",
        r"[\w.+-]+@[\w-]+\.[\w.]+",
        r"\b10\.\d+\.\d+\.\d+\b",
        r"\b192\.168\.\d+\.\d+\b",
        r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b",
        r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b",
        r"\b[\w-]+\.local\b",
    ):
        bad += [m.group(0) for m in re.finditer(pat, text)]
    assert bad == [], bad


def test_pr4_report_evidence_hashes_are_sha256_of_relative_names() -> None:
    lines = re.findall(r"^([0-9a-f]{64})  (\S+)$", report_text(), re.M)
    assert len(lines) >= 20, "the report lists fewer than 20 evidence files with their sha256"
    for _, name in lines:
        assert not name.startswith(("/", "~", "<")), name
    names = {n for _, n in lines}
    # 판정을 낸 요약 · 시간표 · 잡 목록과 사실 파일 · 화면은 모두 있어야 한다
    for need in ("summary.txt", "timeline.txt", "jobs.json", "lost.tsv"):
        assert need in names, need
    assert any(n.startswith("facts/") for n in names)
    assert any(n.startswith("web/") and n.endswith(".png") for n in names)


def test_pr4_changelog_unreleased_names_the_live_harness() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [Unreleased]")
    end = text.index("\n## [", start + 1)
    assert "scripts/acceptance/m5tb_live.sh" in text[start:end]


# ── batch-5: 판정이 「못 읽은 것」을 「없음」으로 읽지 않는다(PLAN.md 「fail-open 금지」) ─────────


def _body(fn: str) -> str:
    """하네스 함수 하나의 본문."""
    text = script_text()
    start = text.index(f"\n{fn}() {{") + 1
    return text[start : text.index("\n}\n", start)]


def test_rl18_a_place_with_no_sign_it_was_read_is_unknown_not_clean(tmp_path: Path) -> None:
    # RL18 — `rcm logs` 가 실패해도 파일은 쓰이고(`|| true`), getraw · jobdoc 은 오류 본문을
    # 파일에 쓴다. 그 파일에서 비밀을 0 번 세면 「비밀 없음」이 아니라 모른다
    mod = helper(tmp_path)
    good = {**COMMON, **GOOD["RL18"]}
    for change in ({"rcm_logs_rc": "1"}, {"rcm_logs_seen": "0"}, {"web_log_status": "404"},
                   {"web_log_seen": "0"}, {"job_doc_status": "500"}, {"job_doc_seen": "0"},
                   {"sse_seen": "0"}, {"masked_seen": "0"}, {"web_log_status": ""}):  # fmt: skip
        got = mod.judge("RL18", {**good, **change})
        assert got.startswith("UNKNOWN ") and "no sign it was read" in got, (change, got)
    # 읽은 증거가 없어도 원문이 보이면 FAIL 이다
    got = mod.judge("RL18", {**good, "in_web_log": "1", "web_log_status": "404"})
    assert got.startswith("FAIL "), got


def test_rl18_runner_records_the_read_evidence_of_each_place() -> None:
    body = _body("run_secret")
    for key in ("rcm_logs_rc", "rcm_logs_seen", "web_log_status", "web_log_seen",
                "job_doc_status", "job_doc_seen", "sse_seen", "masked_seen"):  # fmt: skip
        assert re.search(rf"\bfact RL18 {key}\b", body), key
    assert not re.search(r"client logs [^\n]*\|\| true", body), "an rcm logs failure is dropped"
    assert not re.search(r"h getraw [^\n]*>/dev/null", body), "the web log status is dropped"


def test_rl29_web_without_a_sentence_to_look_for_is_unknown(tmp_path: Path) -> None:
    # RL29-web — 끝난 잡이 없으면 needles 가 비어 판정이 폭 · 오류 · 원문 코드만으로 PASS 였다.
    # 빈 문장은 `grep -qF ""` 로 아무 페이지에나 맞는다
    mod = helper(tmp_path)
    good = {**COMMON, **GOOD["RL29-web"]}
    for change in ({"needles": ""}, {"needle_en_worker_unreachable": ""},
                   {"needle_ko_worker_unreachable": "<absent>"},
                   {"codes": "worker_unreachable worker_replaced"}):  # fmt: skip
        got = mod.judge("RL29-web", {**good, **change})
        assert got.startswith("UNKNOWN "), (change, got)
    got = mod.judge("RL29-web", {**COMMON, **WEB_NO_BAND, "codes": var("LOST_CODES")})
    assert got.startswith("UNKNOWN "), got


def test_rl29_web_sentences_come_from_i18n_in_both_languages(tmp_path: Path) -> None:
    # RL29-web — 보고서는 「두 언어」라 적었지만 영어 화면만 찾았다. 한국어 화면의 문장은
    # core/outcome.py 가 아니라 i18n.js 가 그리므로 그 생산자로 바늘을 만든다
    mod = helper(tmp_path)
    fn = getattr(mod, "web_sentence", None)
    assert fn is not None, "the helper cannot draw a lost ending the way the page does"
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    args = {"name": "rl1b", "seconds": 90}
    assert fn("en", "worker_unreachable", args) == "worker rl1b unreachable for 90s"
    ko = fn("ko", "worker_unreachable", args)
    assert ko.startswith("워커 rl1b") and "90초" in ko, ko
    assert fn("ko", "no_such_code", args) == ""


def test_rl29_web_runner_looks_in_both_languages_and_never_for_an_empty_needle() -> None:
    body = _body("run_lost_ends")
    assert 'fact RL29-web codes "$LOST_CODES"' in body, body
    assert re.search(r'fact RL29-web "needle_\$\{lang\}_\$code"', body), body
    assert re.search(r'fact RL29-web "found_\$\{lang\}_\$code"', body), body
    assert 'grep -qF "$(' not in body  # 빈 글이 아무 페이지에나 맞던 꼴


def test_rl1_counts_its_lost_events_and_reads_finished_at_from_the_db() -> None:
    # RL1 — PASS 문장이 「lost 한 번」이라 말했지만 그 잡의 lost 사건 수를 재지 않았다(RL2 는
    # x_lost_events 로 잰다). 잡 문서의 finished_at 은 초 단위로 잘리므로(core/status.py iso)
    # 1.0초를 봐주는 대신 DB 의 REAL 값과 차이 ≥ 0 으로 견준다
    body = _body("run_partition_past_g")
    assert re.search(r"fact RL1 lost_events \"\$\(db \"SELECT count\(\*\) FROM events", body), body
    assert 'fact RL1 finished_epoch "$(db "SELECT finished_at FROM jobs WHERE id=?" "$x")"' in body
    assert "jobs.finished_at" in var("DB_COLUMNS").split()


def test_rl15_next_job_is_ordered_after_the_orphan_by_the_worker_log_not_a_clock(
    tmp_path: Path,
) -> None:
    # RL15 — z_after_orphan 은 잡 문서의 started_at(초 단위로 잘림)과 줄 시각을 −1.0초 봐주고
    # 견줬다. 같은 워커 로그(stderr 한 줄기, 줄마다 flush)에서 고아 줄이 「claimed #z」 줄보다
    # 앞인지 본다 — 시계도 허용치도 없다
    mod = helper(tmp_path)
    fn = getattr(mod, "lines_in_order", None)
    assert fn is not None, "the helper cannot tell which of two log lines came first"
    log = tmp_path / "w.log"
    log.write_text(
        "1.0 [rcm worker] orphan #18: stopped process group 1\n"
        "1.1 [rcm worker] lane 2: claimed #20 rl15-plain\n"
        "1.2 [rcm worker] lane 1: claimed #2 rl15-ship\n",
        encoding="utf-8",
    )
    p = str(log)
    assert fn(p, "orphan #18: stopped process group", "claimed #20 ") == "1"
    assert fn(p, "claimed #2 ", "orphan #18: stopped process group") == "0"
    assert fn(p, "orphan #18: stopped process group", "claimed #21 ") == mod.ABSENT
    assert fn(str(tmp_path / "none.log"), "a", "b") == mod.ABSENT
    body = _body("run_orphan")
    want = (
        'fact RL15 z_after_orphan "$(h inorder "$W_LOG" '
        '"orphan #$y: stopped process group" "claimed #$z ")"'
    )
    assert want in body, body
    assert "-1.0" not in body
    assert "claimed #" in var("WORKER_LINES").split("|")


def test_rl34_judge_bounds_come_from_the_worker_and_supervisor_constants(tmp_path: Path) -> None:
    # RL34 · RL43 · RL51 · F78 — 판정이 15 · 10 · 0.31 을 손으로 적었다. 도우미의 이름 붙은
    # 상수가 remote_worker.py · job_supervisor.py 의 값과 같고, 판정은 그 상수를 읽는다
    from remote_ci_monitor import job_supervisor, remote_worker

    mod = helper(tmp_path)
    for name, want in (("HEARTBEAT_DEADLINE", remote_worker.HEARTBEAT_DEADLINE_SECONDS),
                       ("SELF_STOP_SLACK", remote_worker.SELF_STOP_SLACK_SECONDS),
                       ("WATCHDOG_TICK", remote_worker.WATCHDOG_TICK_SECONDS),
                       ("SUPERVISOR_POLL", job_supervisor.SUPERVISOR_POLL_SECONDS)):  # fmt: skip
        assert getattr(mod, name, None) == want, name
    good = {**COMMON, **GOOD["RL34"]}
    assert mod.judge("RL34", good).startswith("PASS ")
    mod.HEARTBEAT_DEADLINE += 5  # 문턱이 57 이 되면 워커가 적은 62 는 늦다
    got = mod.judge("RL34", good)
    mod.HEARTBEAT_DEADLINE -= 5
    assert got.startswith("FAIL "), got
    mod.SUPERVISOR_POLL += 1  # 감지 상한이 1초 늘면 1.2초도 PASS 다
    got = mod.judge("RL43", {**COMMON, **GOOD["RL43"], "a_detect": "1.2"})
    mod.SUPERVISOR_POLL -= 1
    assert got.startswith("PASS "), got


def test_pr4_a_judge_that_crashes_is_unknown(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # 판정 함수가 traceback 으로 끝나면 출력이 비어 판정 낱말이 FAIL 도 UNKNOWN 도 아니었다
    mod = helper(tmp_path)

    def boom(_f: dict[str, str]) -> str:
        raise KeyError("x_code")

    mod.JUDGES["RL10"] = boom
    facts = tmp_path / "RL10.txt"
    facts.write_text("x_code=worker_replaced\n", encoding="utf-8")
    try:
        mod.cmd_judge("RL10", str(facts))
        out = capsys.readouterr().out
    except Exception as e:  # noqa: BLE001 — 고치기 전에는 여기로 온다
        out = f"raised {e!r}"
    assert out.startswith("UNKNOWN ") and "KeyError" in out, out


def test_rl43_killtime_refuses_pid_zero_and_one(tmp_path: Path) -> None:
    # RL43 (d) — `ps` 가 빈 줄을 주면 `h killtime KILL "${sup:-0}"` 가 os.kill(0, SIGKILL) 로
    # 하네스 자신의 프로세스 그룹을 EXIT 트랩 없이 죽였다. 새 세션에서 돌려 고치기 전에도 이
    # 시험의 자식만 죽는다
    mod = helper(tmp_path)
    for targets, watch in (("0", ""), ("1", ""), ("", "0")):
        r = subprocess.run(
            [sys.executable, "-I", mod.__file__, "killtime", "TERM", targets, watch, "1"],
            capture_output=True,
            text=True,
            timeout=15,
            start_new_session=True,
            check=False,
        )
        assert r.returncode == 2 and "refused" in r.stdout, (targets, watch, r.returncode, r.stdout)


def test_rl43_is_child_of_reads_the_parent_from_ps() -> None:
    p = _sleeper()
    try:
        time.sleep(0.2)
        assert sh("is_child_of", str(p.pid), str(os.getpid())).returncode == 0
        assert sh("is_child_of", str(p.pid), "1").returncode == 1
        for bad in ("", "0", "1", "x1"):
            assert sh("is_child_of", bad, str(os.getpid())).returncode == 1, bad
    finally:
        p.kill()
        p.wait(timeout=10)


def test_rl43_the_supervisor_is_checked_as_the_worker_child_before_any_signal() -> None:
    # RL43 (d) · RL15 — 감독자 pid 는 명령의 pgid 에서 읽는다. 그 pid 가 기록한 워커의 자식인지
    # (`ps -o ppid`) 보고 나서야 CPU 를 재고 신호를 보낸다
    for fn in ("run_supervisor_only", "run_orphan"):
        body = _body(fn)
        check = body.find('is_child_of "$sup" "$W_PID"')
        assert 0 <= check < body.index("h killtime"), fn
        assert "${sup:-0}" not in body, fn


def _sourced(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    """하네스를 source 하고 OUT 을 시험 폴더로 둔 채 bash 조각 하나를 돌린다."""
    out = tmp_path / "out"
    (out / "facts").mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "M5TB_PY": sys.executable,
           "HOME": str(tmp_path), "OUT": str(out), "REAL_HOME": "/nonexistent"}  # fmt: skip
    r = subprocess.run(
        ["bash", "-c", 'source "$1" && FAILS=0 && eval "$2"', "bash", str(SCRIPT), code],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    for missing in ("No such file or directory", "command not found"):
        assert missing not in r.stderr, r.stderr
    return r


def _summary(tmp_path: Path) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in (tmp_path / "out" / "summary.txt").read_text(encoding="utf-8").splitlines():
        word = [*line.split(), ""]  # 낱말이 빈 줄도 읽는다(빈 판정 낱말)
        rows.setdefault(word[0], []).append(word[1])
    return rows


def test_pr4_a_step_that_ends_without_a_verdict_gets_an_unknown_line(tmp_path: Path) -> None:
    # 여러 단계가 `worker_start … || return 0` 꼴로 판정 줄 없이 돌아왔고 main 은 종료 0 이었다.
    # 끝에 단계 표의 실기 ID(와 그 하위 판정)마다 판정 줄이 있는지 보고 빠진 것은 UNKNOWN 이다
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "summary.txt").write_text(
        "RL15      PASS    orphan stopped\nRL28      PASS    grace\n", encoding="utf-8"
    )
    r = _sourced(tmp_path, 'finish_summary "run_orphan run_grace_view run_kill_worker"')
    assert r.returncode == 0, r.stderr
    rows = _summary(tmp_path)
    assert rows["RL15"] == ["PASS"] and rows["RL28"] == ["PASS"], rows
    for sid in ("RL28-web", "RL28-cancel", "RL43", "RL45", "RL45-web", "RL45-empty", "F78"):
        assert rows.get(sid) == ["UNKNOWN"], (sid, rows)
    assert "RL1" not in rows and "RL29" not in rows  # 돌리지 않은 단계는 묻지 않는다
    text = (tmp_path / "out" / "summary.txt").read_text(encoding="utf-8")
    assert "no verdict" in text, text


def test_pr4_sub_verdicts_are_the_judges_with_a_tail(tmp_path: Path) -> None:
    mod = helper(tmp_path)
    assert set(var("SUB_VERDICTS").split()) == {k for k in mod.JUDGES if "-" in k}


@pytest.mark.parametrize(
    ("lines", "rc"),
    [
        ("RL1       PASS    a\nRL2       SKIP    b\n", "0"),
        ("RL1       PASS    a\nRL2       UNKNOWN b\n", "3"),
        ("RL1       FAIL    a\nRL2       UNKNOWN b\n", "1"),
        ("", "3"),
    ],
)
def test_pr4_exit_code_is_not_zero_with_an_unknown_row(tmp_path: Path, lines: str, rc: str) -> None:
    # 빠진 행 · UNKNOWN 이 하나라도 있으면 0 이 아니다(FAIL 1 이 먼저, 그다음 3). 판정 줄이 하나도
    # 없으면 아무것도 확인하지 않은 것이다
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "summary.txt").write_text(lines, encoding="utf-8")
    r = _sourced(tmp_path, 'summary_rc "$OUT/summary.txt"')
    assert r.stdout.strip() == rc, (r.stdout, r.stderr)


def test_pr4_main_ends_with_the_verdict_check_and_its_exit_code() -> None:
    main = _body("main")
    assert 'finish_summary "$runners"' in main, main[-600:]
    assert main.count('summary_rc "$OUT/summary.txt"') >= 2, "self-test and the full run"
    assert '[ "$FAILS" = 0 ]' not in main
    head = script_text().split("set -euo pipefail", 1)[0]
    assert re.search(r"UNKNOWN[^\n]*3", head), "the header does not name exit code 3"


def test_pr4_a_verdict_line_without_a_verdict_word_is_unknown(tmp_path: Path) -> None:
    # 도우미가 traceback 으로 끝나 판정 줄이 비면 낱말이 비어 FAIL 도 UNKNOWN 도 아니었다
    r = _sourced(
        tmp_path, 'verdict_line RL10 "" && verdict_line RL2 "Traceback (most recent call last):"'
    )
    assert r.returncode == 0, r.stderr
    rows = _summary(tmp_path)
    assert rows == {"RL10": ["UNKNOWN"], "RL2": ["UNKNOWN"]}, rows


def _child_marks(tmp_path: Path) -> tuple[subprocess.Popen[bytes], Path]:
    """RL51 의 표지 — <잡>.child 에 자식 pid, 그 시작 시각은 <잡>.child.start(run_sweep 이 쓰는
    자리), <잡>.start 에는 잡 자신의(다른) 시작 시각."""
    p = _sleeper()
    time.sleep(0.2)
    marks = tmp_path / "out" / "marks"
    marks.mkdir(parents=True, exist_ok=True)
    (marks / "7.child").write_text(f"{p.pid}\n", encoding="utf-8")
    (marks / "7.child.start").write_text(sh("proc_start", str(p.pid)).stdout, encoding="utf-8")
    (marks / "7.start").write_text("Thu Jan  1 00:00:00 1970\n", encoding="utf-8")
    return p, marks


@pytest.mark.parametrize("where", ["reset_world", "cleanup"])
def test_rl51_a_term_ignoring_child_mark_is_reaped_with_its_own_start(
    tmp_path: Path, where: str
) -> None:
    # RL51 — reset_world · cleanup 은 `${f%.*}.start` 를 찾아 <잡>.child 에 <잡>.start(잡 자신의
    # 시작 시각)를 견줬다. run_sweep 은 자식의 시작 시각을 <잡>.child.start 에 쓰므로 자식은
    # 신원이 맞지 않아 남았다(sleep 400)
    p, _ = _child_marks(tmp_path)
    try:
        if where == "reset_world":
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "M5TB_PY": sys.executable,
                   "HOME": str(tmp_path), "OPEN": ""}  # fmt: skip
            argv = ["bash", "-c", _RESET_STUB, "bash", str(SCRIPT), str(tmp_path / "calls.log"),
                    str(tmp_path / "out")]  # fmt: skip
            r = subprocess.run(
                argv, capture_output=True, text=True, env=env, timeout=15, check=False
            )
        else:
            code = (
                'B="$OUT/b"; MARKS="$OUT/marks"; WORKER_PIDS=(); LAUNCHERS=(); '
                "stop_ours() { :; }; cleanup"
            )
            r = _sourced(tmp_path, code)
        assert r.returncode == 0, r.stderr
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{where} left the RL51 child {p.pid} running")
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)


def test_rl51_run_sweep_records_every_child_start_as_it_reads_the_child() -> None:
    # 셋째 경우는 워커 신원을 확인한 뒤에야 .child.start 를 썼다 — 확인이 틀리면 자식을 못 거둔다
    assert _body("run_sweep").count('child=$(child_of "$j")') == 3
    reader = _body("child_of")
    assert '"$MARKS/$1.child.start"' in reader, reader


def test_pr4_a_busy_port_is_found_without_lsof(tmp_path: Path) -> None:
    # main 은 `lsof` 로만 「포트 사용 중」을 봤다 — lsof 가 없으면 조용히 통과했다
    import socket

    path = "/bin:/usr/bin"
    assert subprocess.run(["sh", "-c", "command -v lsof"], env={"PATH": path},
                          capture_output=True, check=False).returncode != 0  # fmt: skip
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = str(busy.getsockname()[1])
    try:
        assert sh("port_free", port, extra={"PATH": path}).returncode == 1, "a listener"
    finally:
        busy.close()
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = str(free.getsockname()[1])
    free.close()
    assert sh("port_free", port, extra={"PATH": path}).returncode == 0, "a free port"
    # 검사기가 돌지 못하면(파이썬이 없음) 비었다고 하지 않는다
    assert sh("port_free", port, extra={"PATH": path, "M5TB_PY": "/usr/bin/false"}).returncode == 1
    main = _body("main")
    assert re.search(r'port_free "\$P" \|\| die', main), main[:1200]


def test_rl51_a_is_a_skip_line_that_names_its_unit_test() -> None:
    # RL51 (a) 는 단계 표에서 「단위(주입 훅)」인데 하네스도 보고서도 SKIP 줄이 없었다
    body = _body("run_sweep")
    m = re.search(r'verdict RL51-a SKIP "([^"]*)"', body)
    assert m, body[-400:]
    assert "test_rl51_a_sigterm_in_the_popen_window_reaches_the_command" in m.group(1), m.group(1)


def test_pr4_changelog_claims_only_guards_the_harness_runs() -> None:
    # CHANGELOG 은 「launchd 라벨을 거절한다」고 했지만 is_prod_label 은 정의만 있고 불리지 않는다
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [Unreleased]")
    bullet = next(
        b for b in text[start : text.index("\n## [", start + 1)].split("\n- ") if "m5tb_live" in b
    )
    calls = [x for x in script_text().splitlines() if "is_prod_label " in x and "() {" not in x]
    if not calls:
        assert "launchd label" not in bullet, bullet
    assert "launchd" in bullet, "say that the harness does not use launchd"
    assert re.search(r"\b3\b", bullet) and "UNKNOWN" in bullet, "the bullet names no exit code 3"


def test_pr4_report_and_header_agree_on_run_time_and_exit_codes() -> None:
    head = script_text().split("set -euo pipefail", 1)[0]
    h = re.search(r"전부\(약 (\d+)분", head)
    r = re.search(r"전체는 (\d+)분쯤", report_text())
    assert h and r and h.group(1) == r.group(1), (h and h.group(0), r and r.group(0))
    assert re.search(r"UNKNOWN[^\n|]*3", report_text()), "the report does not give exit code 3"


def test_pr4_report_cites_functions_not_line_numbers() -> None:
    # 줄 번호는 다음 커밋에서 틀린다 — 판정 · 단계 함수 이름으로 가리킨다
    bad = re.findall(r"\d+~\d+행|\d+행\)", report_text())
    assert bad == [], bad


def test_pr4_report_states_the_bounds_the_judges_now_use() -> None:
    # RL1 은 허용치 없이 DB finished_at 과, RL15 는 워커 로그의 줄 순서로, RL51 (b) 경계는 명세의
    # 「첫 SIGTERM + grace_J + 0.5초」에서 왜 그만큼인지 — 보고서가 판정과 같은 말을 한다
    rows = report_rows()
    rl1, rl15, rl51 = (" ".join(rows[i]) for i in ("RL1", "RL15", "RL51"))
    assert "finished_at" in rl1 and "허용치" in rl1, rl1
    assert "claimed #" in rl15 and "줄 순서" in rl15, rl15
    assert "첫 SIGTERM" in rl51 and "heartbeat" in rl51, rl51
    assert "4.01" not in rl51, rl51  # 옛 상한 grace_J + 0.31 + 0.7
    assert "RL51-a" in rows, sorted(rows)
