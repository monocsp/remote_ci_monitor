"""워커 — git_ref 잡 자재화(미러 → fetch → 체크아웃) · env · 로그 순서 · 실패 문구 · phase.

`tests/test_worker.py` 의 `make_config`·`run_one` 패턴을 git_ref 용으로 옮겼다.
원격은 tmp 의 bare 레포다(네트워크 없음).
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gitrepo import HELLO, RemoteRepo, build_remote, git, install_hanging_git, isolate_git_env
from remote_ci_monitor.config import RepoConfig, ServerConfig
from remote_ci_monitor.core.model import FAILED, QUEUED, SUCCEEDED, Preset, Requester, Source
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import Worker

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

ALICE = Requester(name="alice-laptop", label="alice@laptop")
SCRIPT = (
    "cat hello.txt; echo RCM_REF=$RCM_REF; echo RCM_BASE_SHA=$RCM_BASE_SHA; "
    "echo RCM_DIRTY=$RCM_DIRTY; echo RCM_SOURCE_MODE=$RCM_SOURCE_MODE; "
    "echo HEAD=$(git rev-parse HEAD); echo FILES=$(ls | tr '\\n' ' ')"
)
NEVER = "0123456789abcdef0123456789abcdef01234567"


def make_config(tmp_path: Path, url: str, **server: object) -> ServerConfig:
    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    cfg.server.grace_seconds = 1
    cfg.server.git_fetch_timeout_seconds = 30
    for k, v in server.items():
        setattr(cfg.server, k, v)
    cfg.repos = (RepoConfig(name="app", url=url),)
    cfg.presets = (
        Preset(
            name="deploy",
            argv=("sh", "-c", SCRIPT),
            timeout_seconds=30,
            source_modes=("git_ref",),
            repo="app",
        ),
    )
    return cfg


def enqueue_ref(store: Store, ref: str, sha: str) -> int:
    """서버의 git_ref 분기가 만드는 모양 그대로 — 트리 업로드 없이 바로 queued."""
    job = store.create_job(
        preset="deploy",
        inputs={},
        key="deploy",
        concurrency_group=None,
        source=Source(mode="git_ref", repo="app", ref=ref, sha=sha, base_sha=sha, dirty=False),
        requester=ALICE,
        timeout_seconds=30,
        join_key=join_key("deploy", {}, sha),
        now=datetime.now(UTC),
        state=QUEUED,
    )
    return job.id


def run_one(
    store: Store,
    cfg: ServerConfig,
    job_id: int,
    *,
    timeout: float = 20.0,
    on_change: Callable[[int], None] | None = None,
) -> None:
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop, on_change=on_change)
    w.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = store.get_job(job_id)
        if j is not None and j.is_terminal:
            break
        time.sleep(0.05)
    stop.set()
    w.wake.set()
    w.join(timeout=5)


def log_of(cfg: ServerConfig, job_id: int) -> str:
    return (cfg.data_dir / "jobs" / str(job_id) / "log.txt").read_text()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Store, ServerConfig, RemoteRepo]:
    isolate_git_env(tmp_path, monkeypatch)
    remote = build_remote(tmp_path)
    cfg = make_config(tmp_path, remote.url)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg, remote
    store.close()


# ── 성공 ─────────────────────────────────────────────────────────────────────


def test_git_ref_job_checks_out_the_sha_and_passes_env(env) -> None:
    store, cfg, remote = env
    jid = enqueue_ref(store, "main", remote.main)
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == SUCCEEDED and j.exit_code == 0
    assert j.phase is None and j.lane is None
    assert [t.state for t in j.transitions] == ["queued", "running", "succeeded"]
    log = log_of(cfg, jid)
    assert HELLO in log
    assert "RCM_REF=main\n" in log
    assert f"RCM_BASE_SHA={remote.main}\n" in log
    assert "RCM_DIRTY=0\n" in log and "RCM_SOURCE_MODE=git_ref\n" in log
    assert f"HEAD={remote.main}\n" in log
    # 자재화 로그가 스크립트 출력보다 앞에 있다: fetching → checked out → 출력
    fetching = log.index("[rcm] fetching main from app")
    checked = log.index(f"[rcm] checked out {remote.main[:7]}")
    assert fetching < checked < log.index(HELLO)
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()
    mirror = cfg.data_dir / "mirrors" / "app"
    assert mirror.is_dir()
    assert git("--git-dir", str(mirror), "rev-parse", "--is-bare-repository") == "true"


def test_tag_ref_checks_out_the_tagged_commit(env) -> None:
    store, cfg, remote = env
    jid = enqueue_ref(store, "lw", remote.lw)  # 둘째 커밋 — third.txt 가 아직 없다
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == SUCCEEDED
    log = log_of(cfg, jid)
    assert f"HEAD={remote.lw}\n" in log
    assert "FILES=hello.txt second.txt\n" in log  # third.txt · dev.txt 없음
    assert "[rcm] fetching lw from app" in log


def test_second_job_reuses_the_mirror(env) -> None:
    store, cfg, remote = env
    first = enqueue_ref(store, "main", remote.main)
    run_one(store, cfg, first)
    mirror = cfg.data_dir / "mirrors" / "app"
    before = sorted(p.name for p in (mirror / "refs").rglob("*"))
    second = enqueue_ref(store, "dev", remote.dev)
    run_one(store, cfg, second)
    assert store.get_job(first).state == SUCCEEDED
    assert store.get_job(second).state == SUCCEEDED
    assert f"HEAD={remote.dev}\n" in log_of(cfg, second)
    assert mirror.is_dir() and before  # 미러는 남고(지우지 않는다) 두 잡이 같은 미러를 쓴다
    assert not (cfg.data_dir / "workspaces" / str(second)).exists()


def test_phase_is_materializing_while_fetching_then_executing(env) -> None:
    store, cfg, remote = env
    jid = enqueue_ref(store, "main", remote.main)
    seen: list[tuple[str, str | None]] = []

    def snapshot(job_id: int) -> None:
        j = store.get_job(job_id)
        seen.append((j.state, j.phase))

    run_one(store, cfg, jid, on_change=snapshot)
    assert store.get_job(jid).state == SUCCEEDED
    assert ("running", "materializing") in seen and ("running", "executing") in seen
    assert seen.index(("running", "materializing")) < seen.index(("running", "executing"))
    assert seen[-1] == ("succeeded", None)


# ── 실패 — exit_code null · summary 에 경로 없음 ─────────────────────────────


def test_repo_removed_from_config_fails_the_job(env) -> None:
    store, cfg, remote = env
    jid = enqueue_ref(store, "main", remote.main)
    cfg.repos = ()
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary == "repo 'app' is no longer configured"
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()


def test_sha_missing_in_remote_fails_without_paths(env, tmp_path: Path) -> None:
    store, cfg, remote = env
    jid = enqueue_ref(store, "main", NEVER)
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert "not found after fetch" in j.summary
    assert NEVER[:7] in j.summary
    assert str(tmp_path) not in j.summary and remote.url not in j.summary
    assert "[rcm] fetching main from app" in log_of(cfg, jid)
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()


def test_unreachable_url_fails_with_git_fetch_failed(tmp_path: Path, monkeypatch) -> None:
    isolate_git_env(tmp_path, monkeypatch)
    remote = build_remote(tmp_path)
    url = str(tmp_path / "gone.git")
    cfg = make_config(tmp_path, url)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jid = enqueue_ref(store, "main", remote.main)
        run_one(store, cfg, jid)
        j = store.get_job(jid)
        assert j.state == FAILED and j.exit_code is None
        assert j.summary.startswith("git fetch failed")
        assert url not in j.summary and str(tmp_path) not in j.summary
        assert "gone.git" not in j.summary
        log = log_of(cfg, jid)
        assert "[rcm] fetching main from app" in log
        assert "fatal" in log  # stderr 는 로그에만(토큰이 있어야 보는 곳)
    finally:
        store.close()


def test_fetch_timeout_fails_with_git_fetch_timed_out(env, tmp_path: Path, monkeypatch) -> None:
    store, cfg, remote = env
    cfg.server.git_fetch_timeout_seconds = 1
    install_hanging_git(tmp_path, monkeypatch, hang_on="fetch")
    jid = enqueue_ref(store, "main", remote.main)
    t0 = time.monotonic()
    run_one(store, cfg, jid)
    assert time.monotonic() - t0 < 10
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary == "git fetch timed out after 1s"
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()
