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
        copies = [ln for ln in code if value in ln and not ln.startswith(f'{name}="')]
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
        "hold_released_after_return": "1",
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
    "RL18": {
        "wait_rc": "0", "masked_seen": "200", "in_log": "0", "in_rcm_logs": "0",
        "in_web_log": "0", "in_job_doc": "0", "in_sse": "0", "sse_bytes": "5000",
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
    "RL29-web": {**WEB_NO_BAND, "needles": "worker_unreachable", "found_worker_unreachable": "1"},
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
    "RL1": [{"state_at_stop": "lost"}, {"summary_code": "cancelled"}, {"death_epoch": "1040"},
            {"wait_rc": "1"}, {"hold_released_after_return": "0"}],
    "RL2": [{"x_lost_events": "2"}, {"y_state_after_start": "lost"}],
    "RL5": [{"late_count": "2"}, {"state_after_late": "succeeded"}, {"alive_after_close": "0"}],
    "RL8": [{"x_code": "cancel_unconfirmed"}, {"y_final": "cancelled"}, {"y_late_count": "2"}],
    "RL10": [{"registers": "5"}, {"a_exit": "1"}, {"x_code": "worker_unreachable"}],
    "RL15": [{"leader_hold": "0"}, {"z_after_orphan": "0"}, {"ship_hold": "1"}],
    "RL18": [{"in_sse": "1"}, {"in_log": "2"}, {"log_equals_masked": "0"}],
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
    "RL29-web": [{"found_worker_unreachable": "0"}],
    "RL34": [{"d_stop_after": "95"}, {"b_code": "cancelled"}, {"b_lowered": "0"}],
    "RL40": [{"final_state": "lost"}, {"log_sha_match": "0"}],
    "RL42": [{"same_group_started": "2026-11-27T00:00:00Z"}, {"plain_hold_rows": "1"}],
    "RL43": [{"a_detect": "1.5"}, {"b_detect": "0.4"}, {"e_alive": "0"}, {"d_exit": "0"}],
    "RL45": [{"client_release_rc": "0"}, {"wait_action_rc": "3"}, {"health_ok_same": "0"},
             {"owner_line": "0"}, {"eta_no_ahead": "0"}, {"eta_held": "0"},
             {"top_empty_held": "0"}],
    "RL45-web": [{"en_more_n": "0"}, {"en_nothing_else": "1"}, {"en_stuck_n": "4"},
                 {"found_hold_b": "0"}, {"en_more_n": "0", "en_stuck_n": "2"}],
    "RL45-empty": [{"found_empty_held": "0"}, {"en_empty_hint": "1"}],
    "RL50": [{"x_state": "lost"}, {"second_state": "running"}],
    "RL51": [{"b1_gone": "6.5"}, {"b2_gone": "5"}],
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
        if key in ("codes", "needles", "admin_name", "identity", "web"):
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
