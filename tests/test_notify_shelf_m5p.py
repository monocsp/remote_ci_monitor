"""알림의 보관함 링크(M5p P3) — env `RCM_SHELF_URL` · 웹훅 맨 위 `shelf_url`.

명세는 docs/m5p-workplan.md §4.8 · §15.6(결정 142 · 144), 시나리오는 docs/m5p-test-scenarios-c.md 의
notify 줄. **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

- keep 인 잡: `RCM_SHELF_URL` = 잡 링크와 같은 앞부분 + `/shelf/<id>/`. 보관이 아직 안
  끝났어도 같다.
- keep 이 아닌 잡: env 키는 있고 값은 `""`, 웹훅에는 `shelf_url` 키가 없다.
- 알림 시점에 보관이 `none` · `failed` 로 확정이면 빈 값 · 키 없음(§15.6).
"""

from __future__ import annotations

import itertools
import json
import socket
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from remote_ci_monitor import config as config_mod
from remote_ci_monitor.config import ServerSection, server_base_url
from remote_ci_monitor.core.model import FAILED, QUEUED, SUCCEEDED, Requester, Source
from remote_ci_monitor.core.notify import notify_env, shelf_url
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.core.shelf import host_allowed
from remote_ci_monitor.events import EventBus
from remote_ci_monitor.store import Store
from test_notify import NOW, FakeOpener, FakeRun, at, notifier_for, rule
from test_worker import make_config

ALICE = Requester(name="alice-laptop", label="alice@laptop")
_seq = itertools.count(1)


def keep_job(store: Store, *, keep: bool, state: str = SUCCEEDED, preset: str = "gold") -> Any:
    tree = f"tree-{next(_seq)}"
    created = at(0) - timedelta(seconds=90)
    j = store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", base_sha="abc", dirty=False, tree_hash=tree),
        requester=ALICE,
        timeout_seconds=60,
        join_key=join_key(preset, {}, tree),
        now=created,
        state=QUEUED,
        keep=keep,
    )
    claimed = store.claim(1, created + timedelta(seconds=10))
    assert claimed is not None and claimed.id == j.id
    store.finish(j.id, state, now=at(0), exit_code=0 if state == SUCCEEDED else 1)
    return store.get_job(j.id)


def put_item(store: Store, job_id: int, state: str, reason: str | None = None) -> None:
    store._conn().execute(
        "INSERT INTO shelf_items (job_id, state, preset, requested_at, reason_code) "
        "VALUES (?,?,?,?,?)",
        (job_id, state, "gold", NOW.timestamp(), reason),
    )


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, public_url="http://build.example:8787")
    cfg.presets = tuple(
        config_mod.parse_preset(p)
        for p in (
            {"name": "gold", "argv": ["true"], "artifacts": ["out/*"], "keep": True},
            {
                "name": "onfail",
                "argv": ["true"],
                "artifacts": ["out/*"],
                "artifacts_on": "failure",
                "keep": True,
            },
            {"name": "plain", "argv": ["true"]},
        )
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def env_of(store: Store, cfg: Any, job: Any) -> dict[str, str]:
    cfg.notify = (rule("hook", argv=("true",)),)
    run = FakeRun()
    n, _ = notifier_for(store, cfg, EventBus(), run=run)
    assert n.deliver(job) == 1
    return run.envs()[0]


def webhook_of(store: Store, cfg: Any, job: Any) -> dict[str, Any]:
    cfg.notify = (rule("web", url="https://hooks.example/x"),)
    opener = FakeOpener()
    n, _ = notifier_for(store, cfg, EventBus(), run=FakeRun(), opener=opener)
    assert n.deliver(job) == 1
    return json.loads(opener.requests[0].data)


# ── 순수 함수 ────────────────────────────────────────────────────────────────


def test_notify_env_always_has_the_key_and_takes_the_value():
    row = {"id": 7, "state": "failed", "preset": "gate", "url": "http://x/#/jobs/7"}
    assert notify_env(row, "hook")["RCM_SHELF_URL"] == ""
    got = notify_env(row, "hook", shelf_url="http://x/shelf/7/")
    assert got["RCM_SHELF_URL"] == "http://x/shelf/7/"


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        ({"keep": False, "item_state": None, "bundle_state": "ready"}, ""),
        ({"keep": True, "item_state": "pending", "bundle_state": "ready"}, "URL"),
        ({"keep": True, "item_state": "copying", "bundle_state": "ready"}, "URL"),
        ({"keep": True, "item_state": "active", "bundle_state": "purged"}, "URL"),
        ({"keep": True, "item_state": "stale", "bundle_state": None}, "URL"),
        ({"keep": True, "item_state": "failed", "bundle_state": "ready"}, ""),
        ({"keep": True, "item_state": "deleted", "bundle_state": None}, ""),
        # 행이 아직 없다 — 원격 묶음이 올라오는 중이면 곧 생긴다(waiting)
        ({"keep": True, "item_state": None, "bundle_state": "uploading"}, "URL"),
        ({"keep": True, "item_state": None, "bundle_state": "collecting"}, "URL"),
        ({"keep": True, "item_state": None, "bundle_state": "pending"}, "URL"),
        # 묶음이 종결인데 ready 가 아니다 — none
        ({"keep": True, "item_state": None, "bundle_state": "empty"}, ""),
        ({"keep": True, "item_state": None, "bundle_state": "dropped"}, ""),
        ({"keep": True, "item_state": None, "bundle_state": "failed"}, ""),
        # 보관 행도 묶음 행도 없는 끝난 잡 — 시작 전에 취소 · 정책상 안 모음(P3 격리 검증 D1)
        ({"keep": True, "item_state": None, "bundle_state": None}, ""),
    ],
)
def test_shelf_url_table(kw, want):
    base = "http://mac.local:8787"
    got = shelf_url(base, 7, **kw)
    assert got == ("http://mac.local:8787/shelf/7/" if want == "URL" else "")


def test_cg27_a_trailing_slash_in_the_base_does_not_double():
    got = shelf_url("http://mac.local:8787/", 7, keep=True, item_state="active", bundle_state=None)
    assert got == "http://mac.local:8787/shelf/7/"


# ── Notifier (CB12 · CB13 · CG25 · CG26 · CG28) ──────────────────────────────


def test_cb12_a_keep_job_gets_the_link_with_the_same_base_as_rcm_url(env):
    store, cfg = env
    job = keep_job(store, keep=True)
    put_item(store, job.id, "pending")  # 보관이 아직 안 끝났어도 같다
    e = env_of(store, cfg, job)
    assert e["RCM_SHELF_URL"] == f"http://build.example:8787/shelf/{job.id}/"
    assert e["RCM_URL"].startswith("http://build.example:8787/")


def uploading(store: Store, job_id: int) -> None:
    """원격 잡처럼 묶음 행을 `uploading` 으로 둔다 — 보관 행은 묶음이 ready 가 될 때 선다."""
    policy = SimpleNamespace(
        globs=("out/*",), max_bytes=1, max_files=1, timeout_seconds=1, cancel_timeout_seconds=1
    )
    store.start_collect(job_id, policy, at(1))
    store.set_bundle_state(job_id, "uploading")


def test_cb12b_no_row_yet_still_gets_the_link(env):
    store, cfg = env
    job = keep_job(store, keep=True)
    uploading(store, job.id)
    assert env_of(store, cfg, job)["RCM_SHELF_URL"].endswith(f"/shelf/{job.id}/")


def test_no_shelf_row_and_no_bundle_row_gives_no_link(env):
    store, cfg = env
    job = keep_job(store, keep=True)
    assert env_of(store, cfg, job)["RCM_SHELF_URL"] == ""


def test_cb13_the_webhook_carries_shelf_url_at_the_top(env):
    store, cfg = env
    job = keep_job(store, keep=True)
    put_item(store, job.id, "active")
    body = webhook_of(store, cfg, job)
    assert body["shelf_url"] == f"http://build.example:8787/shelf/{job.id}/"


def test_cg25_a_job_that_is_not_kept_has_an_empty_value_and_no_webhook_key(env):
    store, cfg = env
    job = keep_job(store, keep=False, preset="plain")
    e = env_of(store, cfg, job)
    assert "RCM_SHELF_URL" in e and e["RCM_SHELF_URL"] == ""
    assert "shelf_url" not in webhook_of(store, cfg, job)


def test_a_failed_copy_at_notify_time_gives_no_link(env):
    store, cfg = env
    job = keep_job(store, keep=True)
    put_item(store, job.id, "failed", "shelf_full")
    assert env_of(store, cfg, job)["RCM_SHELF_URL"] == ""
    assert "shelf_url" not in webhook_of(store, cfg, job)


def test_a_green_job_that_only_collects_on_failure_gives_no_link(env):
    store, cfg = env
    job = keep_job(store, keep=True, preset="onfail")
    assert env_of(store, cfg, job)["RCM_SHELF_URL"] == ""


def test_a_red_job_that_collects_on_failure_gets_the_link(env):
    store, cfg = env
    job = keep_job(store, keep=True, preset="onfail", state=FAILED)
    put_item(store, job.id, "pending")  # 실패한 잡은 모았다 — 보관 행이 선다
    assert env_of(store, cfg, job)["RCM_SHELF_URL"].endswith(f"/shelf/{job.id}/")


def test_cg26_no_any_address_in_the_link(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "macmini")
    cfg = make_config(tmp_path, bind="0.0.0.0", port=8787, public_url="", advertise=False)
    cfg.presets = (
        config_mod.parse_preset({"name": "gold", "argv": ["true"], "artifacts": ["o/*"]}),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        job = keep_job(store, keep=True)
        put_item(store, job.id, "pending")
        e = env_of(store, cfg, job)
        assert e["RCM_SHELF_URL"] == f"http://macmini:8787/shelf/{job.id}/"
        assert "0.0.0.0" not in e["RCM_SHELF_URL"]
    finally:
        store.close()


def test_cg27_public_url_with_a_trailing_slash(tmp_path):
    cfg = make_config(tmp_path, public_url="http://mac.local:8787/")
    cfg.presets = (
        config_mod.parse_preset({"name": "gold", "argv": ["true"], "artifacts": ["o/*"]}),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        job = keep_job(store, keep=True)
        put_item(store, job.id, "pending")
        assert env_of(store, cfg, job)["RCM_SHELF_URL"] == f"http://mac.local:8787/shelf/{job.id}/"
    finally:
        store.close()


def test_cg28_the_startup_scan_carries_the_same_link(env):
    store, cfg = env
    job = keep_job(store, keep=True)
    put_item(store, job.id, "pending")
    cfg.notify = (rule("hook", argv=("true",)),)
    run = FakeRun()
    n, _ = notifier_for(store, cfg, EventBus(), run=run, now=NOW + timedelta(minutes=5))
    assert n.scan() == 1
    assert run.envs()[0]["RCM_SHELF_URL"] == f"http://build.example:8787/shelf/{job.id}/"


@pytest.mark.parametrize(
    "server",
    [
        {"public_url": "http://macmini.tail1234.ts.net:8787"},
        {"bind": "192.168.0.10", "public_url": ""},
        {"bind": "0.0.0.0", "public_url": "", "advertise": True},
        {"bind": "0.0.0.0", "public_url": "", "advertise": False},
    ],
)
def test_cg40_every_link_host_passes_the_shelf_host_allowlist(server):
    section = ServerSection(**server)
    base = server_base_url(section, hostname="macmini")
    link = shelf_url(base, 7, keep=True, item_state="active", bundle_state=None)
    host = link.split("/")[2]
    public = section.public_url.split("/")[2] if section.public_url else None
    assert host_allowed(host, public_host=public, hostname="macmini"), link
