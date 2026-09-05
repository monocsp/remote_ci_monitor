"""gitops — 임시 bare 레포로 resolve · mirror · fetch · checkout · ref 이동 · 강제 push · 타임아웃.

모든 URL 은 tmp 안의 bare 레포 경로다. 원격을 부르지 않는다. `GitError` 문구에는 경로·URL 이
없어야 한다(잡 summary 에 실린다) — 자세한 stderr 는 `log` 콜백으로만 나간다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from gitrepo import HELLO, RemoteRepo, build_remote, git, install_hanging_git, isolate_git_env
from remote_ci_monitor.gitops import (
    GitError,
    checkout,
    ensure_mirror,
    fetch_ref,
    has_commit,
    resolve_ref,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

SHA = "0123456789abcdef0123456789abcdef01234567"
NEVER = "d" * 40  # 어떤 레포에도 없는 커밋
TIMEOUT = 20.0


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RemoteRepo:
    isolate_git_env(tmp_path, monkeypatch)
    return build_remote(tmp_path)


@pytest.fixture
def mirror(tmp_path: Path) -> Path:
    # 부모(`mirrors/`)가 없는 경로 — ensure_mirror 가 만들어야 한다
    return tmp_path / "data" / "mirrors" / "app"


def assert_clean_message(exc: BaseException, *secrets: str) -> None:
    """summary 에 실릴 문구다 — 경로·URL·스택이 없고 한 줄이며 짧다."""
    msg = str(exc)
    assert msg and "\n" not in msg and len(msg) <= 200
    assert "Traceback" not in msg
    for s in secrets:
        assert s and s not in msg


def head_of(ws: Path) -> str:
    return git("rev-parse", "HEAD", cwd=ws)


def is_detached(ws: Path) -> bool:
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=ws) == "HEAD"


# ── resolve_ref ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("ref", "attr"), [("main", "main"), ("dev", "dev"), ("lw", "lw")])
def test_resolve_ref_branches_and_lightweight_tag(remote: RemoteRepo, ref: str, attr: str) -> None:
    assert resolve_ref(remote.url, ref, timeout=TIMEOUT) == getattr(remote, attr)


def test_resolve_ref_annotated_tag_gives_the_commit_not_the_tag_object(remote: RemoteRepo) -> None:
    sha = resolve_ref(remote.url, "v1.0.0", timeout=TIMEOUT)
    assert sha == remote.v1_commit == git("rev-parse", "v1.0.0^{commit}", cwd=remote.work)
    assert sha != remote.v1_tag


def test_resolve_ref_full_refname(remote: RemoteRepo) -> None:
    assert resolve_ref(remote.url, "refs/heads/dev", timeout=TIMEOUT) == remote.dev
    assert resolve_ref(remote.url, "refs/tags/v1.0.0", timeout=TIMEOUT) == remote.v1_commit


def test_resolve_ref_full_sha_does_not_call_git(tmp_path: Path) -> None:
    calls: list[Any] = []

    def never(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise RuntimeError("git must not be called for a full sha")

    url = str(tmp_path / "does-not-exist.git")
    assert resolve_ref(url, SHA, timeout=TIMEOUT, run=never) == SHA
    assert calls == []


def test_resolve_ref_unknown_ref_raises_git_error_without_paths(
    remote: RemoteRepo, tmp_path: Path
) -> None:
    with pytest.raises(GitError) as e:
        resolve_ref(remote.url, "no-such-branch", timeout=TIMEOUT)
    assert_clean_message(e.value, str(tmp_path), remote.url)


def test_resolve_ref_missing_remote_raises_git_error_without_paths(tmp_path: Path) -> None:
    url = str(tmp_path / "missing.git")
    with pytest.raises(GitError) as e:
        resolve_ref(url, "main", timeout=TIMEOUT)
    assert_clean_message(e.value, str(tmp_path), url, "missing.git")


def test_resolve_ref_timeout_becomes_git_error(tmp_path: Path) -> None:
    def slow(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "git", timeout=kwargs["timeout"])

    url = str(tmp_path / "slow.git")
    with pytest.raises(GitError) as e:
        resolve_ref(url, "main", timeout=20, run=slow)
    assert "timed out" in str(e.value)
    assert_clean_message(e.value, str(tmp_path), url)


def test_resolve_ref_argv_has_double_dash_before_url_and_no_prompt_env(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    url = str(tmp_path / "fake.git")

    def capture(*args: Any, **kwargs: Any) -> Any:
        argv = list(args[0] if args else kwargs["args"])
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        out: str | bytes = f"{SHA}\trefs/heads/main\n"
        if not (kwargs.get("text") or kwargs.get("encoding") or kwargs.get("universal_newlines")):
            out = out.encode()
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr=type(out)())

    assert resolve_ref(url, "main", timeout=20, run=capture) == SHA
    argv = seen["argv"]
    assert Path(argv[0]).name == "git" and "ls-remote" in argv
    assert argv.index("ls-remote") < argv.index("--")
    tail = argv[argv.index("--") + 1 :]
    # `--` 바로 뒤에 url, 그 뒤엔 ref 패턴만(peeled 줄을 받으려는 `main^{}` 는 허용)
    assert tail[0] == url and tail[1:] and all(p in ("main", "main^{}") for p in tail[1:])
    kw = seen["kwargs"]
    assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and kw["env"].get("LC_ALL") == "C"
    assert "PATH" in kw["env"]
    assert kw.get("stdin") is subprocess.DEVNULL
    assert kw.get("timeout") == 20
    assert kw.get("start_new_session") is True
    assert not kw.get("shell")


# ── ensure_mirror · fetch_ref · has_commit ───────────────────────────────────


def test_ensure_mirror_creates_bare_repo_and_is_idempotent(
    remote: RemoteRepo, mirror: Path
) -> None:
    lines: list[str] = []
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    assert (mirror / "HEAD").is_file()
    assert git("--git-dir", str(mirror), "rev-parse", "--is-bare-repository") == "true"
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    assert has_commit(mirror, remote.main)
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)  # 두 번째는 그대로
    assert has_commit(mirror, remote.main)  # 받아 둔 객체가 사라지지 않았다


def test_fetch_ref_makes_the_commit_available(remote: RemoteRepo, mirror: Path) -> None:
    lines: list[str] = []
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    assert not has_commit(mirror, remote.main)  # fetch 전엔 없다
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    assert has_commit(mirror, remote.main)
    assert git("--git-dir", str(mirror), "rev-parse", "refs/heads/main") == remote.main
    assert not has_commit(mirror, NEVER)
    fetch_ref(mirror, remote.url, "v1.0.0", timeout=TIMEOUT, log=lines.append)
    assert has_commit(mirror, remote.v1_commit)


def test_fetch_ref_targets_one_ref_then_falls_back_to_the_whole_mirror(
    remote: RemoteRepo, mirror: Path
) -> None:
    """리뷰 반영 설계: ref 하나만 먼저 받고, 원하는 sha 가 안 오면 heads · tags 전체로 폴백."""
    lines: list[str] = []
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append, want_sha=remote.main)
    assert has_commit(mirror, remote.main)
    assert not has_commit(mirror, remote.dev)  # dev 는 아직 안 받았다 — 전체 fetch 가 아니다
    # want_sha 가 그 ref 에 없으면 전체 fetch 로 떨어져 dev · 태그까지 온다
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append, want_sha=remote.dev)
    assert has_commit(mirror, remote.dev) and has_commit(mirror, remote.lw)
    assert git("--git-dir", str(mirror), "rev-parse", "refs/heads/dev") == remote.dev
    assert git("--git-dir", str(mirror), "rev-parse", "v1.0.0^{commit}") == remote.v1_commit


def test_fetch_ref_leaves_a_trace_in_the_log(remote: RemoteRepo, mirror: Path) -> None:
    # 잡 로그가 유일한 상세 기록이다 — 성공한 fetch 도 무엇을 받았는지 흔적을 남긴다
    lines: list[str] = []
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    assert any("main" in line for line in lines)


def test_fetch_ref_missing_remote_raises_git_fetch_failed_without_paths(
    remote: RemoteRepo, mirror: Path, tmp_path: Path
) -> None:
    lines: list[str] = []
    url = str(tmp_path / "missing.git")
    ensure_mirror(mirror, url, timeout=TIMEOUT, log=lines.append)
    with pytest.raises(GitError) as e:
        fetch_ref(mirror, url, "main", timeout=TIMEOUT, log=lines.append)
    assert str(e.value).startswith("git fetch failed")
    assert_clean_message(e.value, str(tmp_path), url, "missing.git")
    assert any("fatal" in line for line in lines)  # git 의 stderr 는 로그로만 나간다


def test_fetch_ref_timeout_kills_git_and_raises(
    remote: RemoteRepo, mirror: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    calls = install_hanging_git(tmp_path, monkeypatch, hang_on="fetch")
    t0 = time.monotonic()
    with pytest.raises(GitError) as e:
        fetch_ref(mirror, remote.url, "main", timeout=0.5, log=lines.append)
    assert time.monotonic() - t0 < 5
    assert "timed out" in str(e.value)
    assert_clean_message(e.value, str(tmp_path), remote.url)
    # 상한은 상한이다 — 타임아웃을 다른 refspec 으로 다시 시도하면 실제 상한이 N 배가 된다
    fetches = [c for c in calls.read_text().splitlines() if " fetch " in f" {c} "]
    assert len(fetches) == 1


# ── checkout ─────────────────────────────────────────────────────────────────


def test_checkout_detached_at_sha_with_files_of_that_commit(
    remote: RemoteRepo, mirror: Path, tmp_path: Path
) -> None:
    lines: list[str] = []
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    ws_main = tmp_path / "data" / "workspaces" / "1"
    checkout(mirror, ws_main, remote.main, timeout=TIMEOUT, log=lines.append)
    assert (ws_main / "hello.txt").read_text() == HELLO
    assert (ws_main / "third.txt").is_file() and not (ws_main / "dev.txt").exists()
    assert head_of(ws_main) == remote.main and is_detached(ws_main)
    assert (ws_main / ".git").exists()  # 진짜 체크아웃 — 프리셋이 git 명령을 쓸 수 있다
    ws_lw = tmp_path / "data" / "workspaces" / "2"
    checkout(mirror, ws_lw, remote.lw, timeout=TIMEOUT, log=lines.append)
    assert (ws_lw / "second.txt").is_file() and not (ws_lw / "third.txt").exists()
    assert head_of(ws_lw) == remote.lw and is_detached(ws_lw)


def test_checkout_unknown_sha_raises_git_error_without_paths(
    remote: RemoteRepo, mirror: Path, tmp_path: Path
) -> None:
    lines: list[str] = []
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    ws = tmp_path / "data" / "workspaces" / "3"
    with pytest.raises(GitError) as e:
        checkout(mirror, ws, NEVER, timeout=TIMEOUT, log=lines.append)
    assert_clean_message(e.value, str(tmp_path), remote.url)
    assert not (ws / "hello.txt").exists()


# ── ref 이동 · 강제 push ─────────────────────────────────────────────────────


def test_old_sha_still_checks_out_after_the_ref_moved(
    remote: RemoteRepo, mirror: Path, tmp_path: Path
) -> None:
    lines: list[str] = []
    old = resolve_ref(remote.url, "main", timeout=TIMEOUT)
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    new = remote.push_commit("new.txt", "new\n", "fourth")
    assert new != old and resolve_ref(remote.url, "main", timeout=TIMEOUT) == new
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    assert has_commit(mirror, old) and has_commit(mirror, new)
    ws = tmp_path / "data" / "workspaces" / "4"
    checkout(mirror, ws, old, timeout=TIMEOUT, log=lines.append)  # 제출 시점의 sha 를 돈다
    assert head_of(ws) == old and not (ws / "new.txt").exists()


def test_force_push_orphans_old_commit_and_unknown_sha_fails(
    remote: RemoteRepo, mirror: Path, tmp_path: Path
) -> None:
    lines: list[str] = []
    old = resolve_ref(remote.url, "main", timeout=TIMEOUT)
    remote.rewind_main(1)
    new = remote.push_commit("rewrite.txt", "rewritten\n", "rewrite", force=True)
    ensure_mirror(mirror, remote.url, timeout=TIMEOUT, log=lines.append)
    fetch_ref(mirror, remote.url, "main", timeout=TIMEOUT, log=lines.append)
    assert resolve_ref(remote.url, "main", timeout=TIMEOUT) == new != old
    assert has_commit(mirror, new)
    # 옛 커밋은 미러에 남아 있을 수도(객체 보존) 없을 수도 있다 — 단정하지 않는다.
    # 애초에 없던 sha 는 반드시 실패하고, 문구에 경로가 없다.
    ws = tmp_path / "data" / "workspaces" / "5"
    with pytest.raises(GitError) as e:
        checkout(mirror, ws, NEVER, timeout=TIMEOUT, log=lines.append)
    assert_clean_message(e.value, str(tmp_path), remote.url)


def test_git_error_messages_never_leak_the_environment(tmp_path: Path) -> None:
    # 여기까지의 오류 문구 규칙을 한 번 더: URL 도 경로도 HOME 도 없다
    url = str(tmp_path / "missing.git")
    with pytest.raises(GitError) as e:
        resolve_ref(url, "main", timeout=TIMEOUT)
    assert_clean_message(e.value, str(tmp_path), url, os.environ.get("HOME", "~"))
