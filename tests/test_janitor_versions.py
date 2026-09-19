"""만료된 스토어 버전 드래프트의 청소 — `Janitor.sweep_versions`.

명세: docs/version-page-workplan.md §2.3 · §7 AC-B10 · §8 E12 · E13 · E14, 그리고 §0 의 결정
Q1(드래프트 수명 = `version_ttl_hours`, 기본 24시간)과 **Q2**(편집한 드래프트는 자동으로 지우지
않는다 — 경고만 하고 사람이 «버리기» 로 지운다).

여기서 지키는 것:
- 손도 안 댄 만료 드래프트만 자동으로 버린다. 길은 `DELETE …/versions/<id>` 와 **같다**
  (`App.discard_version`) — ASC 버전 + `version` 프리셋이면 `mode=delete` 잡, 아니면 즉시
  `discarded`.
- 편집이 있으면 상태를 바꾸지 않는다. `expiry_warned` 만 켜고 로그 한 줄.
- 제출된 행 · 만들던 행 · 도는 중인 행은 건드리지 않는다.
- 두 번 돌아도 안전하다. 지우기 잡은 행마다 한 번만 낸다(실패해도 매 sweep 마다 다시 던지지
  않는다 — 사람이 `error` 를 보고 정한다).
- 서버 없이 도는 청소기(`versions=None`)는 대장을 아예 안 본다.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.core.model import FAILED
from remote_ci_monitor.janitor import Janitor
from test_server_release_routes import NOW
from test_server_release_versions import (
    CREATE_FILES,
    PROFILE_NO_VERSION,
    VERSION_DOC,
    VersionServer,
    editing_draft,
)
from test_server_release_versions import remote as _remote
from test_server_release_versions import vsrv as _vsrv

remote = _remote  # 픽스처를 이 모듈의 이름공간에 — pytest 는 이름으로 찾는다
vsrv = _vsrv

PAST = NOW - timedelta(hours=1)


class Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.errors: list[str] = []


def janitor(srv: VersionServer, rec: Recorder | None = None) -> Janitor:
    rec = rec or Recorder()
    return Janitor(
        srv.store,
        srv.cfg,
        now_fn=lambda: NOW,
        log=rec.lines.append,
        on_error=rec.errors.append,
        versions=srv.app,
    )


def expire(srv: VersionServer, vid: int) -> None:
    assert srv.store.update_version(vid, expires_at=PAST)


def state_of(srv: VersionServer, vid: int) -> str:
    return srv.store.get_version(vid)["state"]


# ── 손도 안 댄 드래프트 (AC-B10 · E12) ────────────────────────────────────────


def test_an_untouched_expired_draft_goes_down_the_discard_route_as_a_delete_job(vsrv):
    """E12: TTL 지남 · 미편집 · asc id 있음 · version 프리셋 있음 → `mode=delete` 잡."""
    vid = editing_draft(vsrv)
    expire(vsrv, vid)
    rec = Recorder()
    assert janitor(vsrv, rec).sweep_versions(NOW) == 1
    row = vsrv.store.get_version(vid)
    assert row["state"] == "editing"  # 잡이 0 으로 끝나야 버려진다
    job = vsrv.store.get_job(row["delete_job_id"])
    assert job.preset == "release-version"
    assert job.inputs["mode"] == "delete"
    assert job.inputs["asc_version_id"] == "abc123"
    assert job.requester.label == "store:janitor"  # 사람이 누른 것과 구분된다
    assert any("untouched for 24h" in line and "store delete job" in line for line in rec.lines)
    vsrv.finish_job(row["delete_job_id"], {"out/version.json": json.dumps(VERSION_DOC).encode()})
    assert state_of(vsrv, vid) == "discarded"


def test_the_sweep_never_submits_a_second_delete_job_for_the_same_draft(vsrv):
    """두 번 돌아도 안전하다 — 잡이 아직 도는 중이든 이미 실패했든 새 잡을 던지지 않는다."""
    vid = editing_draft(vsrv)
    expire(vsrv, vid)
    jan = janitor(vsrv)
    assert jan.sweep_versions(NOW) == 1
    first = vsrv.store.get_version(vid)["delete_job_id"]
    assert jan.sweep_versions(NOW) == 0  # 도는 중
    assert vsrv.store.get_version(vid)["delete_job_id"] == first
    vsrv.finish_job(first, None, state=FAILED, exit_code=4)
    row = vsrv.store.get_version(vid)
    assert row["state"] == "editing" and row["error"]  # E12 뒷부분: 행은 그대로 + error
    assert jan.sweep_versions(NOW) == 0  # 실패한 뒤에도 매 sweep 마다 다시 던지지 않는다
    assert vsrv.store.get_version(vid)["delete_job_id"] == first


def test_a_draft_with_no_store_version_is_discarded_straight_away(
    tmp_path: Path, remote, monkeypatch
):
    """version 프리셋이 없으면(E25) 만들 때부터 asc id 가 없다 — 잡 없이 바로 `discarded`."""
    s = VersionServer(tmp_path, remote, profile=PROFILE_NO_VERSION)
    try:
        s.open_gate(monkeypatch)
        status, body = s.create({"ios_version": "1.1.1"})
        assert status == 201, body
        vid = body["id"]
        expire(s, vid)
        before = s.store.count_versions()
        rec = Recorder()
        assert janitor(s, rec).sweep_versions(NOW) == 1
        assert state_of(s, vid) == "discarded"
        assert s.store.count_versions() == before  # 행은 남는다(대장이다)
        assert any("untouched for 24h — discarded" in line for line in rec.lines)
        assert janitor(s).sweep_versions(NOW) == 0
    finally:
        s.close()


def test_a_draft_that_has_not_expired_yet_is_left_alone(vsrv):
    vid = editing_draft(vsrv)
    rec = Recorder()
    assert janitor(vsrv, rec).sweep_versions(NOW) == 0
    assert state_of(vsrv, vid) == "editing"
    assert vsrv.store.get_version(vid)["delete_job_id"] is None
    assert rec.lines == [] and rec.errors == []


def test_a_refusal_leaves_the_row_alone_and_names_only_the_code(vsrv, monkeypatch):
    """관문이 닫히면 잡을 못 낸다 — 행은 그대로고 다음 sweep 이 다시 본다. 로그에는 코드만."""
    vid = editing_draft(vsrv)
    expire(vsrv, vid)
    rec = Recorder()
    monkeypatch.setattr(
        vsrv.app, "_release_gate", lambda name: (_ for _ in ()).throw(_setup_incomplete())
    )
    assert janitor(vsrv, rec).sweep_versions(NOW) == 0
    assert state_of(vsrv, vid) == "editing"
    assert rec.errors == [f"versions: draft {vid} not discarded: setup_incomplete"]


def _setup_incomplete():
    from remote_ci_monitor.server import ApiError

    return ApiError(409, "secrets are missing", code="setup_incomplete", error_code="x")


# ── 만들기가 실패한 드래프트 (워크플랜 §14-3) ─────────────────────────────────


def test_an_expired_failed_draft_is_discarded_without_ever_touching_the_store(vsrv):
    """§14-3 — 만들기가 실패한 행은 청소기의 두 조회 어디에도 안 걸려 영원히 남으면서 이름을
    붙잡고 있었다. 이제 만료되면 손도 안 댄 드래프트와 같은 길로 버려진다.

    **스토어 삭제 잡은 절대 나가지 않는다**: `asc_version_id` 는 만들기가 성공했을 때만 적히므로
    그 행에는 없다. rcm 이 만들지도 않은 스토어 버전을 지우는 것이 여기서 일어날 수 있는 가장
    나쁜 일이라 잡 대장 자체를 확인한다."""
    status, body = vsrv.create({"ios_version": "1.1.1", "android_version": "1.0.1"})
    assert status == 202, body
    vid = body["id"]
    vsrv.finish_job(body["job_id"], None, state=FAILED, exit_code=3)
    row = vsrv.store.get_version(vid)
    assert row["state"] == "failed" and row["asc_version_id"] is None
    before = [j.id for j in vsrv.store.list_jobs_by_preset(["release-version"], 20)]
    expire(vsrv, vid)
    rec = Recorder()
    assert janitor(vsrv, rec).sweep_versions(NOW) == 1
    row = vsrv.store.get_version(vid)
    assert row["state"] == "discarded" and row["delete_job_id"] is None
    jobs = vsrv.store.list_jobs_by_preset(["release-version"], 20)
    assert [j.id for j in jobs] == before  # 새 잡이 없다
    assert [j.inputs["mode"] for j in jobs] == ["create"]  # delete 는 한 번도 나가지 않았다
    assert any("untouched for 24h — discarded" in line and f"#{vid}" in line for line in rec.lines)
    assert janitor(vsrv).sweep_versions(NOW) == 0


def test_a_failed_draft_that_was_edited_is_only_warned(vsrv):
    """편집이 있으면 `failed` 라도 자동으로 지우지 않는다(Q2) — 사람이 쓴 문안이 거기 있다."""
    status, body = vsrv.create({"ios_version": "1.1.1"})
    assert status == 202, body
    vid = body["id"]
    vsrv.finish_job(body["job_id"], None, state=FAILED, exit_code=1)
    assert vsrv.put_listing(vid, {"ios": {"subtitle": "typed by a person"}})[0] == 200
    expire(vsrv, vid)
    assert janitor(vsrv).sweep_versions(NOW) == 0
    row = vsrv.store.get_version(vid)
    assert row["state"] == "failed" and row["expiry_warned"] is True


# ── 편집한 드래프트 (AC-B10 · E13) ────────────────────────────────────────────


def test_an_edited_expired_draft_is_only_warned_and_keeps_its_state(vsrv):
    """Q2 · E13: 편집이 있으면 자동으로 지우지 않는다. `expiry_warned` 만 켜고 상태는 그대로."""
    vid = editing_draft(vsrv)
    assert vsrv.put_listing(vid, {"ios": {"subtitle": "new"}})[0] == 200
    expire(vsrv, vid)
    rec = Recorder()
    jan = janitor(vsrv, rec)
    assert jan.sweep_versions(NOW) == 0
    row = vsrv.store.get_version(vid)
    assert row["state"] == "editing" and row["expiry_warned"] is True
    assert row["delete_job_id"] is None and row["edited_json"]
    assert any("expired with edits" in line and f"#{vid}" in line for line in rec.lines)
    rec.lines.clear()
    assert jan.sweep_versions(NOW) == 0  # 경고는 한 번뿐이다
    assert rec.lines == []


def test_a_running_round_is_warned_but_never_touched(vsrv):
    """회차가 도는 중인 행은 상태가 안 바뀐다 — 경고 표시만 남는다."""
    vid = editing_draft(vsrv)
    assert vsrv.put_listing(vid, {"android": {"title": "X"}})[0] == 200
    assert vsrv.store.update_version(vid, state="running")
    expire(vsrv, vid)
    assert janitor(vsrv).sweep_versions(NOW) == 0
    row = vsrv.store.get_version(vid)
    assert row["state"] == "running" and row["expiry_warned"] is True


def test_a_creating_draft_is_neither_discarded_nor_warned(vsrv):
    status, body = vsrv.create({"ios_version": "1.2.0"})
    assert status == 202, body
    expire(vsrv, body["id"])
    rec = Recorder()
    assert janitor(vsrv, rec).sweep_versions(NOW) == 0
    row = vsrv.store.get_version(body["id"])
    assert row["state"] == "creating" and row["expiry_warned"] is False
    assert rec.lines == [] and rec.errors == []


# ── 제출된 버전 (E14) ─────────────────────────────────────────────────────────


def test_a_submitted_version_is_skipped_by_the_sweep(vsrv):
    """E14: 제출된 버전은 웹·CLI 가 409 로 막고 청소기는 아예 건너뛴다."""
    vid = editing_draft(vsrv)
    assert vsrv.put_listing(vid, {"ios": {"subtitle": "new"}})[0] == 200
    assert vsrv.store.update_version(vid, state="submitted")
    expire(vsrv, vid)
    rec = Recorder()
    assert janitor(vsrv, rec).sweep_versions(NOW) == 0
    row = vsrv.store.get_version(vid)
    assert row["state"] == "submitted" and row["expiry_warned"] is False
    assert rec.lines == [] and rec.errors == []
    assert vsrv.discard(vid)[0] == 409


def test_an_already_discarded_draft_is_not_seen_again(vsrv):
    vid = editing_draft(vsrv, ios="1.1.2", android="")
    expire(vsrv, vid)
    assert vsrv.store.update_version(vid, state="discarded")
    assert janitor(vsrv).sweep_versions(NOW) == 0


# ── 주기 · 기동 · 서버 없는 청소기 ────────────────────────────────────────────


def test_the_existing_sweep_cycle_runs_the_version_sweep(vsrv):
    """`sweep_once` — 보존 정리와 같은 주기에 얹혀 돈다(워크플랜 §2.3)."""
    vid = editing_draft(vsrv, ios="1.1.3", android="")
    expire(vsrv, vid)
    janitor(vsrv).sweep_once(NOW)
    assert vsrv.store.get_version(vid)["delete_job_id"] is not None


def test_a_broken_version_sweep_never_stops_the_retention_sweep(vsrv, monkeypatch):
    """버전 대장의 사고로 보존 정리 스레드가 죽으면 산출물이 영영 안 지워진다."""
    rec = Recorder()
    jan = janitor(vsrv, rec)
    monkeypatch.setattr(
        jan, "sweep_versions", lambda now: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert jan.sweep_once(NOW) == 0
    assert rec.errors == ["versions: sweep failed: RuntimeError"]
    assert jan.last_sweep_at == NOW  # 보존 정리는 끝까지 돌았다


def test_the_janitor_thread_sweeps_versions_as_soon_as_it_starts(vsrv):
    """기동 때 한 번 — `_loop` 은 기다리기 전에 먼저 쓸고, 서버는 기동 단계에서 그 스레드를 켠다."""
    vid = editing_draft(vsrv, ios="1.1.4", android="")
    expire(vsrv, vid)
    jan = janitor(vsrv)
    jan.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if vsrv.store.get_version(vid)["delete_job_id"] is not None:
                break
            time.sleep(0.05)
    finally:
        jan.stop()
    assert vsrv.store.get_version(vid)["delete_job_id"] is not None


def test_the_server_hands_its_own_discard_route_to_the_janitor(vsrv):
    """서버가 청소기에 넘기는 것은 라우트가 쓰는 바로 그 객체다(지름길이 생기지 않게)."""
    vsrv.app.start()
    try:
        assert vsrv.app.retention._versions is vsrv.app
    finally:
        vsrv.app.shutdown()


def test_a_janitor_without_a_server_never_touches_the_ledger(vsrv):
    """`rcm gc` 처럼 서버 없이 도는 청소기는 드래프트를 조용히 버리지 않는다."""
    vid = editing_draft(vsrv, ios="1.1.5", android="")
    expire(vsrv, vid)
    lonely = Janitor(vsrv.store, vsrv.cfg, now_fn=lambda: NOW, log=lambda _m: None)
    assert lonely.sweep_versions(NOW) == 0
    assert lonely.sweep_once(NOW) == 0
    row = vsrv.store.get_version(vid)
    assert row["state"] == "editing" and row["delete_job_id"] is None


def test_a_draft_of_a_repo_that_left_the_config_is_left_for_a_human(vsrv):
    vid = editing_draft(vsrv, ios="1.1.6", android="")
    expire(vsrv, vid)
    vsrv.cfg.repos = ()
    rec = Recorder()
    assert janitor(vsrv, rec).sweep_versions(NOW) == 0
    assert state_of(vsrv, vid) == "editing"
    assert rec.lines == [] and rec.errors == []


@pytest.mark.parametrize("hours", [1, 48])
def test_the_log_line_names_the_profile_ttl(tmp_path: Path, remote, monkeypatch, hours):
    from test_server_release_versions import PROFILE_V

    s = VersionServer(tmp_path, remote, profile={**PROFILE_V, "version_ttl_hours": hours})
    try:
        s.open_gate(monkeypatch)
        status, body = s.create({"ios_version": "1.1.1"})
        assert status == 202, body
        s.finish_job(body["job_id"], CREATE_FILES)
        expire(s, body["id"])
        rec = Recorder()
        assert janitor(s, rec).sweep_versions(NOW) == 1
        assert any(f"untouched for {hours}h" in line for line in rec.lines)
    finally:
        s.close()
