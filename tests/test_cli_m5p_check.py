"""`rcm check` 의 보관함 행(M5p P3) · `--config` 의 `local shelf` 행 · 오프라인 `rcm gc --dry-run`
의 보관함 한 줄.

명세는 docs/m5p-workplan.md §4.8 · §5.3 · §15.6, 시나리오는 docs/m5p-test-scenarios-c.md 의
check 줄.
**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

행 모양은 오늘의 `rcm check` 를 따른다: `ok   shelf         …`(등급 4칸 · 두 칸 · 이름 13칸).
바이트는 `render_text._bytes` 의 십진 눈금이다(§15.6 — 50 GiB 상한은 `53.7 GB` 로 보인다).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__
from remote_ci_monitor.cli import main
from remote_ci_monitor.store import Store

URL = "http://127.0.0.1:8799/shelf/"


def doc(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "enabled": True,
        "stored_bytes": 12_300_000_000,
        "limit_bytes": 53_687_091_200,
        "items": {"active": 3, "stale": 1, "pending": 0, "failed": 0},
        "last_sweep_at": "2026-11-19T10:00:00Z",
        "error_code": None,
    }
    base.update(over)
    return base


def shelf_row(d: dict[str, Any] | None):
    from remote_ci_monitor.core.render_text import shelf_row as fn

    return fn(d, url=URL)


# ── 순수 함수 `render_text.shelf_row` ────────────────────────────────────────


def test_cb11_ok_row_counts_usage_and_link():
    assert shelf_row(doc()) == (
        "shelf",
        True,
        f"3 active · 1 stale · 12.3 GB / 53.7 GB · {URL}",
    )


def test_pending_and_failed_counts_show_only_when_there_are_some():
    _, ok, detail = shelf_row(doc(items={"active": 1, "stale": 0, "pending": 2, "failed": 1}))
    assert ok is True
    assert detail.startswith("1 active · 0 stale · 2 copying · 1 failed · "), detail


def test_ce18_over_90_percent_is_a_warning():
    name, ok, detail = shelf_row(doc(stored_bytes=901, limit_bytes=1000))
    assert name == "shelf" and ok is None
    assert "901 B / 1.0 KB (90%) · new keeps fail at the limit" in detail, detail
    assert detail.endswith(URL)


def test_ce18b_exactly_90_percent_is_still_ok():
    assert shelf_row(doc(stored_bytes=900, limit_bytes=1000))[1] is True


@pytest.mark.parametrize(
    ("code", "text"),
    [
        (
            "shelf_dir_missing",
            "unavailable (shelf_dir_missing) — the shelf folder has no .rcm-shelf marker; "
            "is its disk mounted?",
        ),
        ("copier_dead", "the copy thread stopped (copier_dead) — restart the server"),
        (
            "copier_stuck",
            "the copy thread keeps failing (copier_stuck) — see the server log",
        ),
        (
            "shelf_dir_unwritable",
            "cannot write to the shelf folder (shelf_dir_unwritable) — check its owner and "
            "permissions",
        ),
    ],
)
def test_ce16_ce17_errors_fail(code, text):
    assert shelf_row(doc(error_code=code)) == ("shelf", False, text)


def test_an_unknown_error_code_still_fails_with_the_code():
    name, ok, detail = shelf_row(doc(error_code="something_new"))
    assert ok is False and "something_new" in detail


def test_cg20_off_is_not_a_failure_and_says_what_stays():
    assert shelf_row(doc(enabled=False)) == (
        "shelf",
        True,
        "off (shelf_max_bytes = 0) · 4 kept items stay until their clock runs out",
    )


def test_off_with_nothing_left():
    d = doc(enabled=False, items={"active": 0, "stale": 0, "pending": 0, "failed": 0})
    assert shelf_row(d) == ("shelf", True, "off (shelf_max_bytes = 0)")


def test_cg21_no_document_no_row():
    assert shelf_row(None) is None


def test_cg37_the_same_scale_as_the_rest_of_rcm():
    detail = shelf_row(doc(stored_bytes=12_300_000_000))[2]
    assert "12.3 GB / 53.7 GB" in detail


# ── `rcm check` 에 이어 붙이기 ───────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def row_of(out: str, name: str) -> tuple[str, str] | None:
    m = re.search(rf"^(ok |warn|FAIL)  {re.escape(name)}\s+(.*)$", out, re.M)
    return (m.group(1).strip(), m.group(2)) if m else None


@pytest.fixture
def fake(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    for var in ("XDG_CONFIG_HOME", "RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    f = FakeRcm()
    # 주소를 늘 준다 — 비우면 rcm check 가 같은 네트워크를 뒤져 이 머신의 운영 서버를 찾아간다
    monkeypatch.setenv("RCM_SERVER", f.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    f.route("GET", "/api/whoami", {"name": "alice", "admin": False})
    f.route("GET", "/api/health", {"ok": True, "version": __version__, "shelf": doc()})
    f.route(
        "GET",
        "/api/status",
        {
            "presets": [{"name": "gate", "source_modes": ["tree"], "inputs": []}],
            "display_timezone": "Asia/Seoul",
            "server": {"shelf_storage": doc()},
            "pools": [],
        },
    )
    yield f
    f.close()


def test_check_prints_the_shelf_row_with_the_client_server_link(fake, capsys):
    code, out, _err = run(capsys, ["check"])
    row = row_of(out, "shelf")
    assert row == ("ok", f"3 active · 1 stale · 12.3 GB / 53.7 GB · {fake.url}/shelf/"), out


def test_check_fails_when_the_copier_died(fake, capsys):
    fake.route(
        "GET",
        "/api/status",
        {
            "presets": [{"name": "gate", "source_modes": ["tree"], "inputs": []}],
            "server": {"shelf_storage": doc(error_code="copier_dead")},
            "pools": [],
        },
    )
    code, out, _err = run(capsys, ["check"])
    assert row_of(out, "shelf") == (
        "FAIL",
        "the copy thread stopped (copier_dead) — restart the server",
    ), out
    assert code == 1


def test_cg21_check_against_an_old_server_has_no_shelf_row(fake, capsys):
    fake.route("GET", "/api/health", {"ok": True, "version": "0.6.1"})
    fake.route(
        "GET",
        "/api/status",
        {"presets": [{"name": "gate", "source_modes": ["tree"]}], "server": {}, "pools": []},
    )
    _code, out, _err = run(capsys, ["check"])
    assert row_of(out, "shelf") is None, out


def test_check_falls_back_to_the_health_summary(fake, capsys):
    fake.route(
        "GET",
        "/api/status",
        {"presets": [{"name": "gate", "source_modes": ["tree"]}], "server": {}, "pools": []},
    )
    _code, out, _err = run(capsys, ["check"])
    assert row_of(out, "shelf") is not None, out


# ── `--config` 의 `local shelf` (CG22 · CG23 · CG24) ─────────────────────────

SERVER_TOML = """\
[server]
data_dir = "{data_dir}"
{extra}

[[presets]]
name = "gate"
argv = ["true"]
"""

ONFAIL_PRESET = """
[[presets]]
name = "shelfonfail"
argv = ["true"]
artifacts = ["out/*"]
artifacts_on = "failure"
keep = true
"""


def write_config(tmp_path: Path, extra: str = "", presets: str = "") -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    p = tmp_path / "server.toml"
    p.write_text(SERVER_TOML.format(data_dir=data, extra=extra) + presets)
    return p


def v24_db(data: Path, job_ids: list[int]) -> Path:
    db = data / "rcm.sqlite3"
    store = Store(db)
    conn = store._conn()
    for jid in job_ids:
        conn.execute(
            "INSERT INTO shelf_items (job_id, state, preset, requested_at) VALUES (?,?,?,?)",
            (jid, "active", "gate", 1.0),
        )
    store.close()
    return db


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cg22_local_shelf_counts_folders_without_a_row(fake, tmp_path, capsys):
    cfg = write_config(tmp_path)
    data = tmp_path / "data"
    db = v24_db(data, [412])
    shelf = data / "shelf"
    for name in ("412", "77", "78", ".staging", "notes"):
        (shelf / name).mkdir(parents=True)
    (shelf / ".rcm-shelf").write_text("")
    before = digest(db)
    _code, out, _err = run(capsys, ["check", "--config", str(cfg)])
    row = row_of(out, "local shelf")
    assert row is not None, out
    grade, detail = row
    assert grade == "warn", row
    assert detail.startswith(f"{shelf} · orphans 2"), detail
    assert digest(db) == before
    assert not (data / "backup").exists()


def test_local_shelf_with_no_orphans_is_ok(fake, tmp_path, capsys):
    cfg = write_config(tmp_path)
    data = tmp_path / "data"
    v24_db(data, [412])
    (data / "shelf" / "412").mkdir(parents=True)
    _code, out, _err = run(capsys, ["check", "--config", str(cfg)])
    assert row_of(out, "local shelf") == ("ok", f"{data / 'shelf'} · orphans 0"), out


def test_cg23_an_older_database_is_not_migrated_and_says_why(fake, tmp_path, capsys):
    cfg = write_config(tmp_path)
    data = tmp_path / "data"
    db = data / "rcm.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 23")
    conn.commit()
    conn.close()
    (data / "shelf" / "77").mkdir(parents=True)
    before = digest(db)
    _code, out, _err = run(capsys, ["check", "--config", str(cfg)])
    row = row_of(out, "local shelf")
    assert row is not None, out
    assert "orphans ? (the database is schema v23, before the shelf)" in row[1], row
    assert digest(db) == before
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 23
    conn.close()
    assert not (data / "backup").exists()


def test_local_shelf_before_the_server_ever_ran(fake, tmp_path, capsys):
    cfg = write_config(tmp_path)
    _code, out, _err = run(capsys, ["check", "--config", str(cfg)])
    row = row_of(out, "local shelf")
    assert row is not None, out
    assert row[0] == "ok" and "not created yet" in row[1], row
    assert not (tmp_path / "data" / "rcm.sqlite3").exists()


def test_local_shelf_off(fake, tmp_path, capsys):
    cfg = write_config(tmp_path, extra="shelf_max_bytes = 0")
    _code, out, _err = run(capsys, ["check", "--config", str(cfg)])
    row = row_of(out, "local shelf")
    assert row is not None and row[1].startswith("off (shelf_max_bytes = 0)"), out


def test_cg24_a_keep_preset_that_only_collects_on_failure_is_named(fake, tmp_path, capsys):
    cfg = write_config(tmp_path, presets=ONFAIL_PRESET)
    _code, out, _err = run(capsys, ["check", "--config", str(cfg)])
    row = row_of(out, "local shelf")
    assert row is not None, out
    assert 'shelfonfail keeps failed jobs only (artifacts_on = "failure")' in row[1], row


# ── 오프라인 `rcm gc --dry-run --config` 의 보관함 한 줄 ─────────────────────


def test_offline_gc_dry_run_prints_the_shelf_line_and_leaves_the_db(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RCM_SERVER", raising=False)
    cfg = write_config(tmp_path)
    data = tmp_path / "data"
    db = data / "rcm.sqlite3"
    store = Store(db)
    conn = store._conn()
    # 오래 안 쓴 항목 둘: 하나는 정리 대기로, 하나는 삭제로 갈 때다
    conn.execute(
        "INSERT INTO shelf_items (job_id, state, preset, requested_at, last_used_at) "
        "VALUES (1, 'active', 'gate', 1.0, 1.0)"
    )
    conn.execute(
        "INSERT INTO shelf_items (job_id, state, preset, requested_at, last_used_at) "
        "VALUES (2, 'active', 'gate', ?, ?)",
        (time.time() - 200 * 86400, time.time() - 200 * 86400),
    )
    store.close()
    before = digest(db)
    code, out, err = run(capsys, ["gc", "--dry-run", "--config", str(cfg)])
    assert code == 0, err
    assert out.strip().splitlines()[-1] == (
        "shelf: the next sweep would mark 1 cleanup pending and remove 1 "
        "(rcm gc itself does not touch the shelf)"
    ), out
    assert digest(db) == before
    code, out, _err = run(capsys, ["gc", "--dry-run", "--config", str(cfg), "--json"])
    body = json.loads(out.strip().splitlines()[-1])
    assert body["shelf"] == {"to_stale": 1, "to_delete": 1}
