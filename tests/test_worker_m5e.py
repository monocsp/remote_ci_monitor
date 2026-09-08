"""워커 두 경로(M5e §5) — 언제 모으고 · 무엇을 보고하고 · 언제 `running` 에서 빠지는가.

명세는 docs/m5e-workplan.md §5(로컬·원격 수집 자리 · 종료 상태별 예산) · §8(설정 키) · §18.
**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.** 프리셋의 `artifacts` 키도 `collect.py` 도
아직 없어서, 픽스처는 `ConfigError` · 도우미는 `ModuleNotFoundError` 로 죽는다.

- 로컬은 `tests/test_worker.py` 의 도우미(`enqueue` · `run_one` · `sh`)로 진짜 `sh` 프로세스를
  돌린다. 순서는 `Store.finish` 를 감싸 그 시점의 상태를 찍어 확인한다 — 수집이 **끝난 뒤**
  finish 가 불리고, 그때까지 워크스페이스가 살아 있어야 한다.
- 원격은 `RemoteWorker` 에 가짜 `WorkerClient` 를 끼워 `run_claimed` 를 직접 부른다. 호출 순서와
  `finish` 순간의 `self.running` 을 그대로 적는다(§5.4 는 오늘 코드와 **반대**다 —
  `remote_worker.py:459` 는 보고 **전에** pop 한다).
- 서버 쪽 규칙(옛 워커의 `unknown` · `lost` 잡의 늦은 업로드 409)은 `test_worker_api.WorkerServer`
  (in-process HTTP + 주입 시계)로 본다. 벽시계 sleep 없음.
"""

from __future__ import annotations

import hashlib
import importlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.client import ClientError
from remote_ci_monitor.config import (
    ConfigError,
    ServerConfig,
    WorkerConfig,
    load_server_config,
    parse_preset,
)
from remote_ci_monitor.core.model import CANCELLED, FAILED, LOST, SUCCEEDED
from remote_ci_monitor.remote_worker import RemoteWorker
from remote_ci_monitor.store import Store
from test_server import TAR, make_tar, sh
from test_worker import enqueue, run_one
from test_worker_api import TIMEOUT, WORKER_PRESETS, WorkerServer

WORKER = "build-02"

#: 잡이 워크스페이스에 남기는 골든 두 장(각 3바이트).
GOLD_BODY = "mkdir -p out; printf 'v2\\n' > out/a.txt; printf 'v2\\n' > out/b.txt"
GOLD_ARGV = ["sh", "-c", f"{GOLD_BODY}; echo '::rcm::summary::goldens updated'; exit 0"]
GOLD_PATHS = {"out/a.txt", "out/b.txt"}

ARTIFACT_PRESETS = [
    sh("gold", f"{GOLD_BODY}; echo '::rcm::summary::goldens updated'; exit 0", artifacts=["out/*"]),
    sh(
        "goldfail",
        f"{GOLD_BODY}; echo '::rcm::summary::1 golden failed'; exit 3",
        artifacts=["out/*"],
    ),
    sh("plain", "echo nothing to collect; exit 0"),
]

SERVER_TOML = """\
[server]
data_dir = "{data}"
worker_heartbeat_seconds = {heartbeat}
artifact_cancel_timeout_seconds = {cancel}

[[presets]]
name = "gold"
argv = ["sh", "-c", "echo gold"]
artifacts = ["out/*.png"]
"""


# ── 도우미 ───────────────────────────────────────────────────────────────────


def make_config(tmp_path: Path, **server: Any) -> ServerConfig:
    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    cfg.server.grace_seconds = 1
    for k, v in server.items():
        setattr(cfg.server, k, v)
    cfg.presets = tuple(parse_preset(p) for p in ARTIFACT_PRESETS)
    return cfg


def collect_module():
    """`collect.py`(§18). 구현 전에는 여기서 ImportError — 수집이 아니라 시험이 빨갛다."""
    return importlib.import_module("remote_ci_monitor.collect")


def patch_collect(monkeypatch, fake) -> Any:
    """`collect` 를 부르는 자리를 전부 갈아 끼운다 — 어느 모듈이 어떻게 import 했든."""
    mod = collect_module()
    monkeypatch.setattr(mod, "collect", fake)
    for name in ("remote_ci_monitor.worker", "remote_ci_monitor.remote_worker"):
        other = importlib.import_module(name)
        if hasattr(other, "collect"):
            monkeypatch.setattr(other, "collect", fake)
    return mod


def watch_finish(monkeypatch, cfg: ServerConfig) -> dict[str, Any]:
    """`Store.finish` 를 감싸 「불린 순간」의 사실을 찍는다: 실린 묶음과 워크스페이스 유무."""
    seen: dict[str, Any] = {}
    real = Store.finish

    def spy(self, job_id, state, **kw):
        seen.setdefault("calls", []).append(state)
        seen["state"] = state
        seen["bundle"] = kw.get("bundle")
        seen["workspace"] = (cfg.data_dir / "workspaces" / str(job_id)).exists()
        return real(self, job_id, state, **kw)

    monkeypatch.setattr(Store, "finish", spy)
    return seen


def bundle_of(store: Store, job_id: int) -> dict[str, Any]:
    doc = store.get_bundle(job_id)
    assert doc is not None, f"job #{job_id} has no bundle row"
    return doc


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


# ── 로컬 워커 (§5 「로컬」) ─────────────────────────────────────────────────


def test_collection_is_committed_with_finish_while_the_workspace_still_exists(env, monkeypatch):
    """§5: `run_job` 반환과 `store.finish` **사이**에 모으고, 종료 상태와 **한 트랜잭션**으로
    커밋한다(`Store.finish(..., bundle=…)`). 정리는 그다음이다."""
    store, cfg = env
    seen = watch_finish(monkeypatch, cfg)
    jid = enqueue(store, cfg, "gold")
    run_one(store, cfg, jid)
    job = store.get_job(jid)
    assert job.state == SUCCEEDED and job.exit_code == 0, job
    assert seen["workspace"] is True, "수집 전에 워크스페이스를 지웠다"
    result = seen["bundle"]
    assert result is not None, "finish 에 묶음이 실리지 않았다"
    assert result.state == "ready", result
    assert {f.path for f in result.files} == GOLD_PATHS, result
    assert result.total_bytes == 6, result
    doc = bundle_of(store, jid)
    assert doc["state"] == "ready" and doc["file_count"] == 2, doc
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()  # 정리는 그 뒤


def test_a_failed_job_is_collected_too(env):
    """§5: 실패한 골든의 diff 이미지가 이 기능의 이유다 — 실패해도 모은다."""
    store, cfg = env
    jid = enqueue(store, cfg, "goldfail")
    run_one(store, cfg, jid)
    job = store.get_job(jid)
    assert job.state == FAILED and job.exit_code == 3, job
    doc = bundle_of(store, jid)
    assert doc["state"] == "ready" and doc["file_count"] == 2, doc


def test_a_collection_error_does_not_fail_the_job_or_take_the_lane_down(env, monkeypatch):
    """§5: 수집 오류는 자기 자리에서 잡는다. 바깥으로 새면 잡이 실패하고 레인이 `down` 이 된다."""
    store, cfg = env

    def boom(*args, **kwargs):
        raise OSError(5, "Input/output error")

    patch_collect(monkeypatch, boom)
    jid = enqueue(store, cfg, "gold")
    worker = run_one(store, cfg, jid)
    job = store.get_job(jid)
    assert job.state == SUCCEEDED and job.exit_code == 0, job
    info = worker.info()
    assert info.state != "down" and info.error is None, info
    doc = bundle_of(store, jid)
    assert doc["state"] == "failed" and doc["reason_code"] == "collect_failed", doc


def test_a_cancel_accepted_during_collection_wins_over_the_computed_outcome(env, monkeypatch):
    """§5.4: 커밋 트랜잭션 안에서 취소 상태를 **다시 읽는다** — 수집 중에 수용된 취소가 이긴다.

    `outcome_for` 도 `Store.finish` 도 이걸 자동으로 해 주지 않는다.
    """
    store, cfg = env
    jid = enqueue(store, cfg, "gold")
    mod = collect_module()

    def cancel_then_collect(*args, **kwargs):
        store.request_cancel(jid, "alice-laptop", datetime.now(UTC), cfg.server.grace_seconds)
        return mod.CollectResult(state="empty")

    patch_collect(monkeypatch, cancel_then_collect)
    run_one(store, cfg, jid)
    job = store.get_job(jid)
    assert job.state == CANCELLED, job
    assert job.cancelled_by == "alice-laptop", job


def test_a_collection_budget_overrun_drops_the_bundle_but_not_the_job(env, monkeypatch):
    """§5: 수집 예산 초과는 `dropped` + `timed_out` 이다.

    **잡을 `timed_out` 으로 바꾸지 않는다** — 테스트는 통과했는데 산출물만 못 모은 것이다.
    """
    store, cfg = env
    mod = collect_module()

    def over_budget(*args, **kwargs):
        return mod.CollectResult(
            state="dropped", reason_code="timed_out", reason_args={"limit": 60, "seen": 61}
        )

    patch_collect(monkeypatch, over_budget)
    jid = enqueue(store, cfg, "gold")
    run_one(store, cfg, jid)
    job = store.get_job(jid)
    assert job.state == SUCCEEDED and job.exit_code == 0, job
    assert job.summary == "goldens updated", job  # 잡이 찍은 문장은 그대로
    doc = bundle_of(store, jid)
    assert doc["state"] == "dropped" and doc["reason_code"] == "timed_out", doc


# ── 원격 워커 (§5 「원격」) ─────────────────────────────────────────────────


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeWorkerClient:
    """원격 워커가 서버에 하는 말을 순서대로 적는다. 재시도는 워커 루프의 정책이라 여기엔 없다."""

    def __init__(self, tar: bytes):
        self.server = "http://fake"
        self.token = "t"
        self.tar = tar
        self.calls: list[str] = []
        self.finish_kwargs: dict[str, Any] = {}
        self.running_at_finish: dict[int, int] | None = None
        self.worker: RemoteWorker | None = None
        self.download_error: ClientError | None = None
        self.uploads: list[Path] = []

    def download_tree(self, job_id: int, dest: Path) -> int:
        self.calls.append("download_tree")
        if self.download_error is not None:
            raise self.download_error
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.tar)
        return len(self.tar)

    def phase(self, job_id: int, phase: str) -> dict[str, Any]:
        self.calls.append(f"phase:{phase}")
        return {}

    def log(self, job_id: int, data: bytes) -> dict[str, Any]:
        self.calls.append("log")
        return {}

    def upload_bundle(self, job_id: int, tar_path: Path) -> dict[str, Any]:
        self.calls.append("upload_bundle")
        self.uploads.append(Path(tar_path))
        return {"bundle_sha256": sha_of(Path(tar_path)), "state": "uploaded"}

    def bundle_status(self, job_id: int) -> dict[str, Any]:
        self.calls.append("bundle_status")
        return {"state": "uploading"}

    def finish(
        self, job_id: int, outcome: str, exit_code: int | None, summary: str | None = None, **kw
    ) -> dict[str, Any]:
        self.calls.append("finish")
        self.running_at_finish = dict(self.worker.running) if self.worker else None
        self.finish_kwargs = {
            "outcome": outcome,
            "exit_code": exit_code,
            "summary": summary,
            **kw,
        }
        return {"state": outcome}


def claim_payload(job_id: int, *, argv: list[str], globs: tuple[str, ...] = ("out/*",)):
    """`/worker/claim` 응답. 언 정책(§9)은 프리셋 쪽·잡 쪽 어디로 오든 찾도록 둘 다 싣는다."""
    return {
        "job": {
            "id": job_id,
            "preset": "gold",
            "state": "running",
            "inputs": {},
            "timeout_seconds": 60,
            "source": {
                "mode": "tree",
                "repo": "org/app",
                "base_sha": "abc123f",
                "dirty": False,
                "tree_hash": "t",
                "bytes": len(TAR),
            },
            "requester": {"name": "alice-laptop", "label": "alice@laptop"},
        },
        "preset": {
            "name": "gold",
            "argv": list(argv),
            "env": {},
            "env_passthrough": ["PATH", "HOME", "LANG"],
            "timeout_seconds": 60,
            "artifacts": list(globs),
        },
        "artifacts": {
            "globs": list(globs),
            "max_bytes": 1_073_741_824,
            "max_files": 10_000,
            "timeout_seconds": 60,
            "cancel_timeout_seconds": 5,
        },
    }


@pytest.fixture
def remote(tmp_path):
    """`RemoteWorker` + 가짜 클라이언트. `run_claimed` 를 직접 부른다(루프는 안 돈다)."""
    fake = FakeWorkerClient(make_tar({"hello.txt": b"hello\n"}))
    cfg = WorkerConfig(
        server="http://fake",
        token="t",
        pool="default",
        lanes=1,
        data_dir=str(tmp_path / "wdata"),
        grace_seconds=1,
    )
    worker = RemoteWorker(cfg, client=fake, log=lambda msg: None, environ=dict(os.environ))
    fake.worker = worker
    return worker, fake


def test_the_remote_worker_uploads_before_finishing(remote):
    """§5: 모으고 → 올리고 → 보고한다. 순서가 뒤집히면 서버가 영수증 없는 처분을 받는다."""
    worker, fake = remote
    worker.run_claimed(1, claim_payload(7, argv=GOLD_ARGV))
    assert "upload_bundle" in fake.calls, fake.calls
    assert fake.calls.index("upload_bundle") < fake.calls.index("finish"), fake.calls


def test_the_remote_worker_leaves_running_only_after_the_finish_is_reported(remote):
    """§5.4: `self.running` 에서 빼는 것은 완료 보고가 **확정된 뒤**다.

    오늘은 `_finish` 가 보고 전에 pop 한다(`remote_worker.py:459`) — 업로드가 길어지면 heartbeat 에
    안 실린 잡을 서버가 `lost` 로 닫는다.
    """
    worker, fake = remote
    worker.run_claimed(1, claim_payload(7, argv=GOLD_ARGV))
    assert fake.running_at_finish is not None, fake.calls
    assert 7 in fake.running_at_finish, fake.running_at_finish
    assert worker.running == {}, worker.running  # 그리고 끝나면 빠진다


def test_the_remote_finish_carries_a_structured_disposition(remote):
    """§5: 해시만으로는 「빈 수집·시한 초과·건너뛴 수」를 말할 수 없다 — 구조화해서 보낸다."""
    worker, fake = remote
    worker.run_claimed(1, claim_payload(7, argv=GOLD_ARGV))
    art = fake.finish_kwargs.get("artifacts")
    assert isinstance(art, dict), fake.finish_kwargs
    assert set(art) >= {
        "state",
        "bundle_sha256",
        "file_count",
        "total_bytes",
        "skipped_count",
        "reason_code",
    }, art
    assert art["state"] == "ready", art
    assert art["file_count"] == 2 and art["total_bytes"] == 6, art
    assert art["skipped_count"] == 0 and art["reason_code"] is None, art
    assert art["bundle_sha256"] == sha_of(fake.uploads[0]), art


def test_a_materialisation_failure_still_leaves_a_disposition(remote):
    """§5: 오류로 빠지는 경로(`remote_worker.py:432~438`)도 처분을 남긴다 — 조용히 비우지 않는다."""
    worker, fake = remote
    fake.download_error = ClientError(404, "no snapshot for this job")
    worker.run_claimed(1, claim_payload(7, argv=GOLD_ARGV))
    assert fake.finish_kwargs["outcome"] == FAILED, fake.finish_kwargs
    art = fake.finish_kwargs.get("artifacts")
    assert isinstance(art, dict), fake.finish_kwargs
    assert art["state"] == "skipped" and art["reason_code"] == "not_run", art
    assert "upload_bundle" not in fake.calls, fake.calls


# ── 서버가 보는 처분 (§5 · §6) ─────────────────────────────────────────────


@pytest.fixture
def srv(tmp_path):
    """`/worker/*` 진짜 서버 + 산출물 글롭을 선언한 `gold` 프리셋."""
    s = WorkerServer(tmp_path)
    try:
        gold = sh("gold", "echo gold", artifacts=["out/*.txt"])
        s.cfg.presets = tuple(parse_preset(p) for p in [*WORKER_PRESETS, gold])
    except BaseException:
        s.close()
        raise
    yield s
    s.close()


def running_gold(srv: WorkerServer) -> int:
    jid = srv.queued_job(preset="gold")
    srv.registered(WORKER)
    assert srv.claimed(WORKER) == jid
    return jid


def test_a_finish_without_an_artifacts_field_reads_back_as_unknown(srv):
    """§5: 필드가 **통째로 없으면** 옛 워커다 → `unknown`. `empty` 로 소급하지 않는다."""
    jid = running_gold(srv)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token=WORKER,
        json_body={"outcome": SUCCEEDED, "exit_code": 0},
    )
    assert status == 200, body
    assert srv.store.get_job(jid).state == SUCCEEDED
    assert srv.view(jid)["artifacts"]["state"] == "unknown", srv.view(jid)["artifacts"]


def test_a_finish_that_says_empty_reads_back_as_empty(srv):
    """`{"state": "empty"}` 는 「모았는데 0개였다」 — `unknown` 과 반드시 구분된다."""
    jid = running_gold(srv)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token=WORKER,
        json_body={
            "outcome": SUCCEEDED,
            "exit_code": 0,
            "artifacts": {"state": "empty", "file_count": 0, "total_bytes": 0},
        },
    )
    assert status == 200, body
    doc = srv.view(jid)["artifacts"]
    assert doc["state"] == "empty" and doc["file_count"] == 0, doc


def test_a_late_upload_for_a_lost_job_is_refused_and_does_not_revive_it(srv):
    """§5: `lost` 잡의 늦은 업로드는 **409**. 죽은 잡을 산출물로 되살리지 않는다."""
    jid = running_gold(srv)
    srv.clock.advance(TIMEOUT + 1)
    srv.app.mark_lost_workers(srv.clock.now)
    assert srv.store.get_job(jid).state == LOST
    status, body = srv.req(
        "PUT",
        f"/worker/jobs/{jid}/artifacts",
        token=WORKER,
        body=TAR,
        headers={"Content-Type": "application/x-tar"},
    )
    assert status == 409, (status, body)
    assert srv.store.get_job(jid).state == LOST
    assert srv.view(jid)["artifacts"]["state"] != "ready", srv.view(jid)["artifacts"]


# ── 취소 예산 (§5 · §8) ────────────────────────────────────────────────────


def load_server(tmp_path: Path, *, heartbeat: int, cancel: int) -> ServerConfig:
    path = tmp_path / "rcm.toml"
    path.write_text(
        SERVER_TOML.format(data=str(tmp_path / "data"), heartbeat=heartbeat, cancel=cancel),
        encoding="utf-8",
    )
    return load_server_config(path, environ={})


def test_the_default_cancel_budget_fits_under_two_heartbeats(tmp_path):
    """§8 기본값 — `artifact_cancel_timeout_seconds` 5 는 `2 × 5` 아래다."""
    cfg = load_server(tmp_path, heartbeat=5, cancel=5)
    assert cfg.server.artifact_cancel_timeout_seconds == 5
    assert cfg.server.artifact_timeout_seconds == 60
    assert cfg.server.artifact_retention_hours == 24
    assert cfg.preset("gold").artifacts == ("out/*.png",)


@pytest.mark.parametrize("cancel", [10, 11], ids=["equal", "over"])
def test_a_cancel_budget_that_reaches_two_heartbeats_is_refused(tmp_path, cancel):
    """§5: 서버가 미확인 취소를 `kill_at + 2 × heartbeat` 에 닫는다(`remote_workers.py:536`) —
    그보다 오래 모으면 잡이 그 사이 닫힌다. 설정 검증이 **미만**을 강제한다."""
    with pytest.raises(ConfigError) as e:
        load_server(tmp_path, heartbeat=5, cancel=cancel)
    message = str(e.value)
    assert "artifact_cancel_timeout_seconds" in message, message
    assert "worker_heartbeat_seconds" in message, message
