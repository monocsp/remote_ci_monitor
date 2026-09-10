"""`rcm gc` 와 `rcm check` 의 storage 행 (M5g §5.3·§5.5). **구현보다 먼저 썼다.**

여기서 지키는 것:
- `--dry-run` 은 **서버 없이도** 돈다(`--config` 로 설정과 데이터 디렉터리만 읽는다). 업그레이드
  안전 게이트가 이것 위에 서 있다 — `POST /gc` 는 새 서버에만 있고 새 서버는 뜨자마자 sweep 한다.
- 타임아웃은 실패가 아니라 **3(모른다)** 이다. 서버는 계속 지우고 있을 수 있다.
- `rcm check` 의 등급은 「다음 sweep 이 고칠 수 있나」로 가른다 — 고칠 수 있으면 warn, 사람이
  와야 하면 FAIL.
"""

from __future__ import annotations

import json

from remote_ci_monitor.cli import main
from remote_ci_monitor.core.render_text import render_gc, storage_row
from remote_ci_monitor.store import Store

GB = 1024**3


def base(**over) -> dict:
    doc = {
        "volume_bytes": 30 * GB,
        "workspace_bytes": 29 * GB,
        "snapshot_bytes": GB,
        "evictable_bytes": 20 * GB,
        "non_evictable_bytes": 10 * GB,
        "orphan_bytes": 0,
        "limit_bytes": 100 * GB,
        "min_free_bytes": 10 * GB,
        "free_bytes": 600 * GB,
        "over_budget_bytes": 0,
        "projected_short_free_bytes": 0,
        "budget_unreachable": False,
        "no_progress": False,
        "measured_at": "2026-09-09T05:00:03Z",
        "last_sweep_at": "2026-09-09T05:00:03Z",
        "next_sweep_at": "2026-09-09T06:00:03Z",
        "error_code": None,
    }
    doc.update(over)
    return doc


# ── `rcm check` 의 storage 행 ────────────────────────────────────────────────


def test_ok_when_inside_the_budget_and_above_the_floor() -> None:
    name, ok, detail = storage_row(base(), now="2026-09-09T05:18:03Z")
    assert name == "storage" and ok is True
    assert "30.0 GB" in detail or "32.2 GB" in detail  # 눈금은 렌더가 정한다
    assert "next sweep" in detail


def test_warn_when_over_budget_because_the_next_sweep_will_fix_it() -> None:
    doc = base(volume_bytes=118 * GB, over_budget_bytes=0, evictable_bytes=110 * GB)
    _, ok, detail = storage_row(doc, now=None)
    assert ok is None and "over" in detail and "budget" in detail


def test_warn_when_a_size_could_not_be_measured() -> None:
    doc = base(volume_bytes=None, evictable_bytes=None, error_code="scan_EACCES")
    _, ok, detail = storage_row(doc, now=None)
    assert ok is None and "measure" in detail.lower()


def test_fail_when_the_budget_cannot_be_reached_and_says_why() -> None:
    """지울 수 없는 바이트가 원인이면 사람이 와야 한다 — 다음 sweep 은 못 고친다."""
    doc = base(volume_bytes=118 * GB, budget_unreachable=True, non_evictable_bytes=118 * GB)
    _, ok, detail = storage_row(doc, now=None)
    assert ok is False and ("running" in detail or "orphan" in detail)


def test_fail_when_under_the_floor_with_nothing_left_to_delete() -> None:
    doc = base(free_bytes=4 * GB, evictable_bytes=0, projected_short_free_bytes=6 * GB)
    _, ok, detail = storage_row(doc, now=None)
    assert ok is False and "floor" in detail


def test_fail_when_deleting_stopped_helping() -> None:
    """삭제가 효과 없던 상황을 warn 으로 숨기지 않는다(코덱스 2차 리뷰 2번)."""
    doc = base(free_bytes=4 * GB, no_progress=True)
    _, ok, detail = storage_row(doc, now=None)
    assert ok is False and ("not move" in detail or "did not" in detail)


def test_unknown_numbers_never_render_as_zero() -> None:
    doc = base(volume_bytes=None, free_bytes=None, limit_bytes=None, min_free_bytes=None)
    _, _, detail = storage_row(doc, now=None)
    assert "0 GB" not in detail and "0.0 GB" not in detail


def test_a_small_number_is_not_rounded_away_to_zero_gigabytes() -> None:
    """50 KB 짜리 스냅샷이 `0.0 GB` 로 보이면 「없다」로 읽힌다.

    회수 목록은 1.6 GB 워크스페이스와 50 KB tar 이 한 표에 섞이는 자리다."""
    from remote_ci_monitor.core.render_text import _bytes

    assert _bytes(0) == "0 B"  # 없는 것은 없는 것이다
    assert _bytes(50_000) == "50.0 KB"
    assert _bytes(720_000_000) == "720.0 MB"
    assert _bytes(30 * GB).endswith("GB")


# ── `rcm gc` 사람용 표 ───────────────────────────────────────────────────────


def test_the_dry_run_table_names_each_job_its_bytes_and_the_rule() -> None:
    body = {
        "dry_run": True,
        "planned": [
            {"job_id": 118, "workspace_bytes": 1717986918, "snapshot_bytes": None, "reason": "age"},
            {"job_id": 131, "workspace_bytes": 755, "snapshot_bytes": 3379809, "reason": "budget"},
        ],
        "deleted": [],
        "failed": [],
        "freed_bytes": 0,
        "storage_before": base(),
        "storage_after": None,
    }
    text = render_gc(body)
    assert "#118" in text and "#131" in text
    assert "age" in text and "budget" in text
    assert "would free" in text and "would remain" in text  # 지운 게 아니라 지울 것이다
    assert "—" in text  # 모르는 바이트는 대시


def test_the_real_run_reports_deleted_and_failed_apart() -> None:
    body = {
        "dry_run": False,
        "planned": [
            {"job_id": 1, "workspace_bytes": 10, "snapshot_bytes": 0, "reason": "age"},
            {"job_id": 2, "workspace_bytes": 20, "snapshot_bytes": 0, "reason": "age"},
        ],
        "deleted": [{"job_id": 1, "workspace_bytes": 10, "snapshot_bytes": 0}],
        "failed": [{"job_id": 2, "error_code": "EACCES"}],
        "freed_bytes": 10,
        "storage_before": base(),
        "storage_after": base(),
    }
    text = render_gc(body)
    assert "freed" in text and "1 failed" in text and "EACCES" in text


# ── 서버 없이 도는 dry-run (결정 61) ─────────────────────────────────────────


def test_dry_run_works_without_a_server(tmp_path, capsys, monkeypatch):
    """업그레이드 게이트가 이것 위에 서 있다 — 서버를 올리기 **전에** 무엇이 지워질지 본다."""
    cfg_path = tmp_path / "server.toml"
    data = tmp_path / "data"
    cfg_path.write_text(
        f'[server]\ndata_dir = "{data}"\nworkspace_retention_days = 0\n'
        '[[presets]]\nname = "ok"\nargv = ["true"]\n'
    )
    (data / "workspaces" / "7").mkdir(parents=True)
    (data / "workspaces" / "7" / "big").write_bytes(b"x" * 4096)
    Store(data / "rcm.sqlite3").close()  # 서버가 한 번은 돌았다 — DB 가 없으면 「모른다」(3)다
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:1")  # 닿지 않는 주소 — 안 부른다
    rc = main(["gc", "--dry-run", "--config", str(cfg_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert (data / "workspaces" / "7").exists()  # 아무것도 안 지운다
    assert "would free" in out


def test_the_offline_dry_run_is_json_too(tmp_path, capsys):
    cfg_path = tmp_path / "server.toml"
    data = tmp_path / "data"
    cfg_path.write_text(
        f'[server]\ndata_dir = "{data}"\n[[presets]]\nname = "ok"\nargv = ["true"]\n'
    )
    Store(data / "rcm.sqlite3").close()
    rc = main(["gc", "--dry-run", "--config", str(cfg_path), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["dry_run"] is True and doc["planned"] == []
    assert doc["offline"]["copy"] is True


# ── 타임아웃은 실패가 아니다 ─────────────────────────────────────────────────


def test_a_timeout_exits_three_and_says_the_server_may_still_be_deleting(capsys, monkeypatch):
    """「타임아웃 = 실패」로 찍지 않는다 — `rcm wait` 의 종료 코드 3 과 같은 규칙이다."""
    import remote_ci_monitor.cli as cli

    class Boom(cli.ClientError):
        def __init__(self):
            super().__init__(0, "timed out")

    monkeypatch.setattr(cli.Client, "gc", lambda self, **kw: (_ for _ in ()).throw(Boom()))
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:1")
    monkeypatch.setenv("RCM_TOKEN", "t")
    rc = main(["gc"])
    err = capsys.readouterr().err
    assert rc == 3 and "may still" in err.lower()


def test_no_row_at_all_before_the_first_measurement() -> None:
    """「못 쟀다」와 「아직 안 쟀다」는 다른 사실이다 — 갓 뜬 서버를 노랗게 그리지 않는다."""
    doc = base(measured_at=None, volume_bytes=None, error_code=None)
    assert storage_row(doc, now=None) is None


def test_a_measurement_failure_still_warns() -> None:
    doc = base(measured_at=None, volume_bytes=None, error_code="scan_EACCES")
    row = storage_row(doc, now=None)
    assert row is not None and row[1] is None
