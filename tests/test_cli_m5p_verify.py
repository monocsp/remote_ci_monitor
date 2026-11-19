"""P3 격리 검증이 찾은 결함과 개선(2026-11-19) — 고치기 전에 쓴 시험이다(빨간 것이 정상이다).

- D1: 시작 전에 취소된 keep 잡에 404 링크를 알리던 것 · 그 잡의 보관 줄은 「the job never ran」.
- D2: 서버 경로의 `rcm gc --dry-run` 에 보관함 줄이 없던 것. `POST /gc` dry-run 이 `shelf` 를
  싣는다.
- 개선 3: `rcm keep` 의 `gone` · `retried` 이유. 개선 5: 보관함 unavailable(503)은 종료 3.
- 개선 6: `rcm shelf get` 이 실패하면 새로 만든 DIR 을 지우고, zip · 풀린 크기를 목록과 대조한다.
- 개선 7: 살아남은 변이 셋(zip 같은 이름, 남의 서버의 리다이렉트, `local shelf` 의 `mode=ro`).
"""

from __future__ import annotations

import io
import json
import sqlite3
import time
import zipfile
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, RawReply
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import config as config_mod
from remote_ci_monitor.core.model import CANCELLED, QUEUED, Requester, Source
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.events import EventBus
from remote_ci_monitor.store import Store
from test_cli_m5p import SUMMARY, Shelf, human, run
from test_cli_m5p_get import FILES, serve, sha, zip_of
from test_notify import FakeOpener, FakeRun, at, notifier_for, rule
from test_server import Server
from test_worker import make_config

JOB = 412


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli_mod, "SHELF_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(cli_mod, "SHELF_POLL_SECONDS", 0.02)


@pytest.fixture
def srv(env, monkeypatch):
    fake = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", fake.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    yield Shelf(fake)
    fake.close()


# ── D1: 시작 전에 취소된 keep 잡 ─────────────────────────────────────────────


def cancelled_keep_job(store: Store) -> Any:
    j = store.create_job(
        preset="gold",
        inputs={},
        key="gold",
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", base_sha="abc", dirty=False, tree_hash="t1"),
        requester=Requester(name="alice-laptop", label="alice@laptop"),
        timeout_seconds=60,
        join_key=join_key("gold", {}, "t1"),
        now=at(0) - timedelta(seconds=30),
        state=QUEUED,
        keep=True,
    )
    store.request_cancel(j.id, "alice-laptop", at(0), 1.0)
    job = store.get_job(j.id)
    assert job.state == CANCELLED and job.started_at is None
    return job


@pytest.fixture
def nstore(tmp_path):
    cfg = make_config(tmp_path, public_url="http://build.example:8787")
    cfg.presets = (
        config_mod.parse_preset(
            {"name": "gold", "argv": ["true"], "artifacts": ["out/*"], "keep": True}
        ),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def test_d1_a_keep_job_cancelled_before_it_ran_gets_no_link(nstore):
    store, cfg = nstore
    job = cancelled_keep_job(store)
    cfg.notify = (rule("hook", argv=("true",)),)
    run_ = FakeRun()
    n, _ = notifier_for(store, cfg, EventBus(), run=run_)
    assert n.deliver(job) == 1
    assert run_.envs()[0]["RCM_SHELF_URL"] == ""
    cfg.notify = (rule("web", url="https://hooks.example/x"),)
    opener = FakeOpener()
    n2, _ = notifier_for(store, cfg, EventBus(), run=FakeRun(), opener=opener)
    store._conn().execute("DELETE FROM notifications")
    assert n2.deliver(job) == 1
    assert "shelf_url" not in json.loads(opener.requests[0].data)


def test_d1_a_remote_job_whose_bundle_is_still_uploading_keeps_its_link(nstore):
    """원격 잡은 끝날 때 묶음 행이 `uploading` 으로 있다 — 그 링크는 곧 산다."""
    store, cfg = nstore
    job = cancelled_keep_job(store)
    policy = SimpleNamespace(
        globs=("out/*",), max_bytes=1, max_files=1, timeout_seconds=1, cancel_timeout_seconds=1
    )
    store.start_collect(job.id, policy, at(1))
    store.set_bundle_state(job.id, "uploading")
    cfg.notify = (rule("hook", argv=("true",)),)
    run_ = FakeRun()
    n, _ = notifier_for(store, cfg, EventBus(), run=run_)
    n.deliver(job)
    assert run_.envs()[0]["RCM_SHELF_URL"] == f"http://build.example:8787/shelf/{job.id}/"


def test_d1_the_shelf_line_of_a_job_that_never_ran(srv, capsys):
    srv.docs = [
        srv.job("none", state="cancelled", reason="unknown", started_at=None, exit_code=None)
    ]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB)])
    assert code == 2, err
    assert human(err)[-1] == "shelf: nothing to keep — the job never ran", err


def test_d1_a_superseded_job_that_never_ran_still_says_moved(srv, capsys):
    srv.docs = [
        srv.job("none", state="cancelled", reason="unknown", started_at=None, superseded_by=57)
    ]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB)])
    assert human(err)[-1] == "shelf: moved to #57", err


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("interrupted", "the job ended while its artifacts were still uploading"),
        ("job_lost", "the job was lost before its artifacts were collected"),
    ],
)
def test_new_none_reasons_from_p2_read_as_sentences(srv, capsys, reason, text):
    srv.docs = [srv.job("none", reason=reason, state="lost", started_at="2026-11-19T09:00:00Z")]
    _code, _out, err = run(capsys, ["wait", "--job", str(JOB)])
    assert human(err)[-1] == f"shelf: nothing to keep — {text}", err


# ── D2: 서버 경로의 `rcm gc --dry-run` ───────────────────────────────────────


def test_d2_post_gc_dry_run_carries_the_shelf_plan(tmp_path):
    s = Server(tmp_path, workers=True)
    try:
        # 기동 직후의 첫 청소 회차가 끝난 뒤에 넣는다 — 그 회차가 행을 먼저 치우지 않게
        deadline = time.monotonic() + 10
        while s.app.retention.last_sweep_at is None and time.monotonic() < deadline:
            time.sleep(0.05)
        conn = s.store._conn()
        conn.execute(
            "INSERT INTO shelf_items (job_id, state, preset, requested_at, last_used_at) "
            "VALUES (9001, 'active', 'ok', 1.0, 1.0)"
        )
        status, body = s.req("POST", "/gc", token="admin", json_body={"dry_run": True})
        assert status == 200, body
        assert body["shelf"] == {"to_stale": 0, "to_delete": 1}
    finally:
        s.close()


def test_d2_rcm_gc_dry_run_against_the_server_prints_the_shelf_line(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    s = Server(tmp_path / "srv", workers=True)
    try:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{s.port}")
        monkeypatch.setenv("RCM_TOKEN", s.tokens["admin"])
        code, out, err = run(capsys, ["gc", "--dry-run"])
        assert code == 0, err
        assert (
            out.strip()
            .splitlines()[-1]
            .startswith("shelf: the next sweep would mark 0 cleanup pending and remove 0")
        ), out
    finally:
        s.close()


# ── 개선 3: `rcm keep` 의 이유 ───────────────────────────────────────────────


def keep_route(srv: Shelf, result: str, status: int, **body: Any) -> None:
    srv.fake.route(
        "POST", f"/jobs/{JOB}/keep", {"job": JOB, "result": result, **body}, status=status
    )


def test_gone_without_a_reason_names_the_bundle_state(srv, capsys):
    keep_route(srv, "gone", 410, shelf={"state": "none", "url": srv.shelf_url, "reason_code": None})
    srv.docs = [srv.job("none", artifacts={"state": "expired"})]
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 1, err
    assert "its artifacts are gone (expired)" in err, err


def test_gone_prefers_the_servers_reason(srv, capsys):
    keep_route(srv, "gone", 410, reason_code="purged", shelf=None)
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert "its artifacts are gone (purged)" in err, err


def test_retried_names_the_previous_reason_from_the_server(srv, capsys):
    keep_route(
        srv,
        "retried",
        202,
        previous_reason_code="shelf_full",
        shelf={"state": "pending", "url": srv.shelf_url, "reason_code": None},
    )
    code, out, _err = run(capsys, ["keep", str(JOB)])
    assert code == 0
    assert out.strip() == f"#412: the last copy failed (shelf_full); trying again · {srv.shelf_url}"


def test_retried_without_a_previous_reason_leaves_it_out(srv, capsys):
    keep_route(
        srv, "retried", 202, shelf={"state": "pending", "url": srv.shelf_url, "reason_code": None}
    )
    code, out, _err = run(capsys, ["keep", str(JOB)])
    assert out.strip() == f"#412: the last copy failed; trying again · {srv.shelf_url}"


def test_the_real_server_says_why_a_retry_and_a_gone(tmp_path):
    s = Server(tmp_path, workers=False)
    try:
        s.cfg.presets = tuple(
            config_mod.parse_preset(p)
            for p in ({"name": "gold", "argv": ["true"], "artifacts": ["out/*"]},)
        )
        app = s.app
        prev = SimpleNamespace(reason_code="shelf_full")
        body = app.keep_body_extra("retried", prev, None)
        assert body == {"previous_reason_code": "shelf_full"}
        assert app.keep_body_extra("gone", None, "purged") == {"reason_code": "purged"}
        assert app.keep_body_extra("requested", prev, None) == {}
    finally:
        s.close()


# ── 개선 5: 보관함 unavailable(503)은 종료 3 ─────────────────────────────────


def test_keep_on_an_unavailable_shelf_exits_3(srv, capsys):
    keep_route(srv, "disabled", 503, shelf=None, error="the shelf is off on this server")
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 3, err
    assert "unavailable" in err


def test_keep_on_a_shelf_turned_off_still_exits_1(srv, capsys):
    keep_route(srv, "disabled", 409, shelf=None, error="the shelf is off on this server")
    assert run(capsys, ["keep", str(JOB)])[0] == 1


def test_shelf_list_on_an_unavailable_shelf_exits_3(srv, capsys):
    srv.fake.route("GET", "/api/shelf", {"error": "the shelf folder is unavailable"}, status=503)
    code, _out, err = run(capsys, ["shelf"])
    assert code == 3, err


def test_shelf_rm_on_an_unavailable_shelf_exits_3(srv, monkeypatch, capsys):
    srv.fake.route("DELETE", f"/shelf/{JOB}", {"error": "unavailable"}, status=503)
    code, _out, err = run(capsys, ["shelf", "rm", str(JOB)])
    assert code == 3, err


def test_shelf_get_on_an_unavailable_shelf_exits_3(env, monkeypatch, tmp_path, capsys):
    f = FakeRcm()
    try:
        monkeypatch.setenv("RCM_SERVER", f.url)
        serve(f)
        f.route("GET", f"/api/shelf/{JOB}", {"error": "unavailable"}, status=503)
        code, _out, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
        assert code == 3, err
    finally:
        f.close()


# ── 개선 6: 실패하면 새 DIR 을 지운다 · 크기 상한 ────────────────────────────


@pytest.fixture
def gfake(env, monkeypatch):
    f = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", f.url)
    serve(f)
    yield f
    f.close()


def test_a_failed_get_removes_the_dir_it_created(gfake, tmp_path, capsys):
    bad = dict(FILES)
    bad["build/app_map/run.log"] = b"no\n"  # 같은 크기 — 해시만 다르다
    gfake.route("GET", f"/shelf/{JOB}.zip", RawReply(zip_of(bad)))
    out = tmp_path / "new" / "o"
    code, _o, _e = run(capsys, ["shelf", "get", "412", "--output", str(out)])
    assert code == 3
    assert not out.exists()
    assert not (tmp_path / "new").exists()


def test_a_failed_get_leaves_an_existing_dir_alone(gfake, tmp_path, capsys):
    out = tmp_path / "o"
    out.mkdir()
    (out / "mine.txt").write_text("keep me")
    bad = dict(FILES)
    bad["build/app_map/run.log"] = b"no\n"  # 같은 크기 — 해시만 다르다
    gfake.route("GET", f"/shelf/{JOB}.zip", RawReply(zip_of(bad)))
    assert run(capsys, ["shelf", "get", "412", "--output", str(out)])[0] == 3
    assert sorted(p.name for p in out.iterdir()) == ["mine.txt"]


def test_a_zip_far_bigger_than_the_list_is_stopped(gfake, tmp_path, capsys):
    huge = zip_of({**FILES, "build/app_map/run.log": b"x" * 2_000_000})
    gfake.route("GET", f"/shelf/{JOB}.zip", RawReply(huge))
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
    assert code == 3, err
    assert "bigger than" in err, err


def test_a_member_bigger_than_its_listed_size_is_refused(gfake, tmp_path, capsys):
    """zip 의 선언 크기는 거짓말할 수 있다 — 풀면서 목록의 크기를 넘는 순간 멈춘다."""
    buf = io.BytesIO()
    big = b"y" * 4096
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p, b in FILES.items():
            z.writestr(p, big if p.endswith("run.log") else b)
    gfake.route("GET", f"/shelf/{JOB}.zip", RawReply(buf.getvalue()))
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
    assert code == 3, err
    assert "build/app_map/run.log" in err


# ── 개선 7: 살아남은 변이 셋 ─────────────────────────────────────────────────


def test_a_zip_with_the_same_name_twice_is_refused(gfake, tmp_path, capsys):
    dup = zip_of(FILES, [("build/app_map/run.log", FILES["build/app_map/run.log"])])
    gfake.route("GET", f"/shelf/{JOB}.zip", RawReply(dup))
    out = tmp_path / "o"
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(out)])
    assert code == 3, err
    assert "unsafe" in err
    assert not out.exists()


def test_a_redirect_from_another_server_is_not_followed(env, monkeypatch, tmp_path, capsys):
    other, target = FakeRcm(), FakeRcm()
    try:
        serve(target)
        other.route("GET", "/api/health", {"ok": True, "version": __version__, "shelf": SUMMARY})
        other.route(
            "GET",
            f"/api/shelf/{JOB}",
            RawReply(b"", {"Location": f"{target.url}/api/shelf/{JOB}"}),
            status=302,
        )
        url = f"{other.url}/shelf/{JOB}/"
        code, _o, err = run(capsys, ["shelf", "get", url, "--output", str(tmp_path / "o")])
        assert code == 3, err
        assert "redirect" in err
        assert target.requests == []
    finally:
        other.close()
        target.close()


def test_local_shelf_opens_the_database_read_only(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    Store(data / "rcm.sqlite3").close()
    seen: list[tuple[Any, dict]] = []
    real = sqlite3.connect

    def spy(*args, **kw):
        seen.append((args, kw))
        return real(*args, **kw)

    monkeypatch.setattr(cli_mod.sqlite3, "connect", spy)
    known, why = cli_mod._shelf_rows_ro(data / "rcm.sqlite3")
    assert known == set() and why is None
    assert seen, "the database was not opened"
    target, kw = seen[0]
    assert kw.get("uri") is True and str(target[0]).endswith("?mode=ro"), seen


def test_a_successful_get_into_a_new_nested_dir(gfake, tmp_path, capsys):
    out = tmp_path / "new" / "o"
    code, stdout, err = run(capsys, ["shelf", "get", "412", "--output", str(out)])
    assert code == 0, err
    assert {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} == set(FILES)
    assert sha(FILES["build/app_map/run.log"]) == sha((out / "build/app_map/run.log").read_bytes())
    assert "wrote 4 files" in stdout
