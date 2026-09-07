"""클라이언트 — 스냅샷(git 추적·수정·미추적·무시·삭제·심링크·.rcmignore·--exclude) ·
제출→업로드→wait 종료 코드 매핑 · 서버 끊김 → 3 · --timeout → 3 · describe 한 줄."""

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from remote_ci_monitor.cli import describe
from remote_ci_monitor.client import (
    Client,
    ClientError,
    exit_code_for,
    make_snapshot,
    preset_from_json,
    wait_for_job,
)
from test_server import Server


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "tracked.txt").write_text("v1\n")
    (root / "deleted.txt").write_text("gone\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "gate.sh").write_text("#!/bin/sh\necho hi\n")
    os.chmod(root / "scripts" / "gate.sh", 0o755)
    (root / ".gitignore").write_text("*.log\nbuild/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    # 수정 · 미추적 · 무시 · 삭제 · 심링크
    (root / "tracked.txt").write_text("v2\n")
    (root / "untracked.txt").write_text("new\n")
    (root / "noise.log").write_text("ignored by git\n")
    (root / "build").mkdir()
    (root / "build" / "app.bin").write_bytes(b"\0" * 10)
    (root / "deleted.txt").unlink()
    os.symlink("tracked.txt", root / "link.txt")
    (root / ".rcmignore").write_text("secrets/\n")
    (root / "secrets").mkdir()
    (root / "secrets" / "key.pem").write_text("nope\n")
    return root


def tar_names(path: Path) -> list[str]:
    with tarfile.open(path) as tf:
        return sorted(tf.getnames())


def test_snapshot_selects_git_files_and_applies_rcmignore(repo, tmp_path):
    snap = make_snapshot(repo, tar_dir=tmp_path)
    try:
        assert snap.files == (
            ".gitignore",
            ".rcmignore",
            "link.txt",
            "scripts/gate.sh",
            "tracked.txt",
            "untracked.txt",
        )
        assert snap.dirty is True and len(snap.base_sha) == 40 and snap.repo is None
        assert len(snap.tree_hash) == 64
        assert tar_names(snap.tar_path) == list(snap.files)
        with tarfile.open(snap.tar_path) as tf:
            link = tf.getmember("link.txt")
            assert link.issym() and link.linkname == "tracked.txt"
            assert tf.getmember("scripts/gate.sh").mode & 0o111
            assert tf.getmember("tracked.txt").uname == "" and tf.getmember("tracked.txt").uid == 0
            assert tf.extractfile("tracked.txt").read() == b"v2\n"  # 미커밋 내용 그대로
    finally:
        snap.tar_path.unlink()


def test_snapshot_exclude_and_tree_hash_changes_with_content(repo, tmp_path):
    a = make_snapshot(repo, tar_dir=tmp_path)
    b = make_snapshot(repo, excludes=["untracked.txt"], tar_dir=tmp_path)
    (repo / "tracked.txt").write_text("v3\n")
    c = make_snapshot(repo, tar_dir=tmp_path)
    try:
        assert "untracked.txt" not in b.files and a.tree_hash != b.tree_hash
        assert a.tree_hash != c.tree_hash
        again = make_snapshot(repo, tar_dir=tmp_path)
        assert again.tree_hash == c.tree_hash  # 같은 트리는 같은 해시
        again.tar_path.unlink()
    finally:
        for s in (a, b, c):
            s.tar_path.unlink()


def test_snapshot_clean_checkout_is_not_dirty(repo, tmp_path):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "wip")
    snap = make_snapshot(repo, tar_dir=tmp_path)
    try:
        assert snap.dirty is False
    finally:
        snap.tar_path.unlink()


def test_snapshot_without_git_walks_the_tree(tmp_path):
    root = tmp_path / "plain"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("a")
    (root / "sub" / "b.txt").write_text("b")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref")
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        assert snap.files == ("a.txt", "sub/b.txt")
        assert snap.base_sha is None and snap.dirty is None
    finally:
        snap.tar_path.unlink()


def test_snapshot_skips_symlinks_that_point_outside_and_names_them(tmp_path):
    """사용자 검사 U2.1: venv 의 절대 경로 링크로 서버가 통째로 거부하지 않게 — 미리 뺀다."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.txt").write_text("a")
    os.symlink("/usr/bin/env", root / "env-abs")
    os.symlink("../outside", root / "escape")
    os.symlink("a.txt", root / "inside")
    seen: list[str] = []
    snap = make_snapshot(root, tar_dir=tmp_path, progress=seen.append)
    try:
        assert snap.files == ("a.txt", "inside")
        warn = [m for m in seen if "skipping symlink" in m]
        assert any("env-abs -> /usr/bin/env (absolute target)" in m for m in warn)
        assert any("escape -> ../outside (points outside the tree)" in m for m in warn)
    finally:
        snap.tar_path.unlink()


# ── 서버와 함께 ───────────────────────────────────────────────────────────────


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True)
    yield s
    s.close()


def submit_and_upload(live, client, repo, preset="ok", tmp_path=None):
    snap = make_snapshot(repo, tar_dir=tmp_path)
    try:
        source = {
            "mode": "tree",
            "repo": snap.repo,
            "base_sha": snap.base_sha,
            "dirty": snap.dirty,
            "tree_hash": snap.tree_hash,
            "bytes": snap.bytes,
        }
        resp = client.submit(preset, {}, source, requester_label="alice@laptop")
        if not resp["joined"]:
            client.upload(resp["job_id"], snap.tar_path)
        return resp
    finally:
        snap.tar_path.unlink()


def test_run_flow_success_and_failure_exit_codes(live, tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    client = Client(f"http://127.0.0.1:{live.port}", live.tokens["alice"])
    assert client.whoami() == {"name": "alice-laptop", "admin": False, "kind": "client"}
    assert set(client.presets()) == {"ok", "bad", "slow", "gate"}
    ok = submit_and_upload(live, client, root, "ok", tmp_path)
    code, job, reason = wait_for_job(client, ok["job_id"], poll_seconds=0.1)
    assert (code, reason) == (0, None) and job["state"] == "succeeded" and job["summary"] == "green"
    bad = submit_and_upload(live, client, root, "bad", tmp_path)
    code, job, _ = wait_for_job(client, bad["job_id"], poll_seconds=0.1)
    assert code == 1 and job["exit_code"] == 2 and job["failed_step"] == "t"


def test_cancel_gives_2_and_timeout_gives_3(live, tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    client = Client(f"http://127.0.0.1:{live.port}", live.tokens["alice"])
    slow = submit_and_upload(live, client, root, "slow", tmp_path)
    jid = slow["job_id"]
    code, job, reason = wait_for_job(client, jid, timeout=0.5, poll_seconds=0.1)
    assert code == 3 and "--timeout" in reason and job["state"] in ("queued", "running")
    live.wait_state(jid, "running")
    assert client.cancel(jid)["state"] == "cancelling"
    code, job, reason = wait_for_job(client, jid, poll_seconds=0.1)
    assert code == 2 and job["state"] == "cancelled" and reason is None


def test_joiner_cancel_leaves_and_wait_keeps_going(live, tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    alice = Client(f"http://127.0.0.1:{live.port}", live.tokens["alice"])
    bob = Client(f"http://127.0.0.1:{live.port}", live.tokens["bob"])
    first = submit_and_upload(live, alice, root, "slow", tmp_path)
    second = submit_and_upload(live, bob, root, "slow", tmp_path)
    assert second["joined"] is True and second["job_id"] == first["job_id"]
    assert bob.cancel(first["job_id"]) == {
        "left": True,
        "job_id": first["job_id"],
        "job_state": live.store.get_job(first["job_id"]).state,
    }
    assert alice.cancel(first["job_id"])["state"] in ("cancelled", "cancelling")
    code, job, _ = wait_for_job(alice, first["job_id"], poll_seconds=0.1)
    assert code == 2


def test_timeout_is_honoured_while_server_is_unreachable(tmp_path):
    """수용 검사 J3: 서버 연결이 안 돼도 --timeout 이 60초 grace 보다 먼저 온다."""
    from remote_ci_monitor.client import Client, wait_for_job

    client = Client("http://127.0.0.1:9", None)  # 아무도 안 듣는 포트 → 즉시 연결 실패
    clock = [0.0]

    def fake_sleep(s: float) -> None:
        clock[0] += s

    code, job, reason = wait_for_job(
        client, 1, timeout=3, sleep=fake_sleep, clock=lambda: clock[0], use_sse=False
    )
    assert code == 3 and job is None
    assert reason and "--timeout 3s" in reason
    assert clock[0] < 60


def test_unreachable_server_is_3_not_1(tmp_path):
    client = Client("http://127.0.0.1:1", "tok", timeout=0.5)
    with pytest.raises(ClientError) as e:
        client.health()
    assert e.value.status == 0
    clock = iter([0.0, 0.0, 30.0, 61.0, 62.0, 63.0])
    code, job, reason = wait_for_job(
        client, 1, poll_seconds=0, sleep=lambda s: None, clock=lambda: next(clock)
    )
    assert code == 3 and job is None and "lost contact" in reason


def test_missing_job_and_bad_token_are_3(live):
    client = Client(f"http://127.0.0.1:{live.port}", "wrong-token")
    code, _, reason = wait_for_job(client, 999, poll_seconds=0.1)
    assert code == 3 and "not found" in reason
    with pytest.raises(ClientError) as e:
        client.whoami()
    assert e.value.status == 401 and "token" in e.value.message


def test_exit_code_mapping_and_preset_from_json():
    assert exit_code_for({"state": "succeeded"}) == 0
    assert exit_code_for({"state": "failed"}) == 1
    assert exit_code_for({"state": "cancelled"}) == 2
    assert exit_code_for({"state": "timed_out"}) == 2
    assert exit_code_for({"state": "lost"}) == 3
    assert exit_code_for(None) == 3 and exit_code_for({"state": "running"}) == 3
    p = preset_from_json(
        {
            "name": "gate",
            "source_modes": ["tree"],
            "inputs": [{"name": "scope", "type": "choice", "choices": ["a", "b"], "default": "a"}],
        }
    )
    assert p.inputs[0].choices == ("a", "b") and p.source_modes == ("tree",)


def test_describe_lines():
    queued = {
        "id": 413,
        "state": "queued",
        "position": 2,
        "reason": "blocked_by_group",
        "estimate": {"wait_seconds": 160, "finish_at": "2026-09-04T01:03:52Z"},
    }
    text = describe(queued)
    assert text.startswith("#413 queued · 2nd in line · blocked by group · wait 2m 40s · eta ")
    running = {
        "id": 412,
        "state": "running",
        "position": None,
        "estimate": {"elapsed_seconds": 59, "finish_at": None, "overdue": True},
        "progress": {
            "phase": "executing",
            "steps": [{}],
            "steps_total": 8,
            "steps_total_partial": False,
            "current_index": 5,
            "current_name": "test",
        },
    }
    assert describe(running) == "#412 running · step 5/8 test · elapsed 59s · overdue"
    done = {"id": 1, "state": "failed", "summary": "2 tests failed", "estimate": {}}
    assert describe(done) == "#1 failed · 2 tests failed"
