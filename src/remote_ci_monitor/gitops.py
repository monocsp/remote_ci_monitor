"""git_ref 소스 모드의 I/O — ls-remote 로 sha 확정 · 로컬 미러 fetch · 워크스페이스 체크아웃.

- 모든 호출은 argv 배열, 셸 없음, 인자 앞에 `--`(sha 는 40 hex 로 검증돼 예외).
- `GIT_TERMINAL_PROMPT=0` 으로 자격 프롬프트에 걸려 멈추지 않는다. 자격은 빌드 머신의
  ssh 키·credential helper 가 준다(서버 프로세스의 HOME · SSH_AUTH_SOCK 을 넘긴다).
- `GitError` 메시지는 잡 summary 에 실리므로 URL·경로·stderr 를 담지 않는다. 전체 stderr 는
  `log` 콜백(잡 로그 — 토큰이 있어야 본다)으로만 나간다.
- 같은 미러를 두 레인이 동시에 fetch 하면 ref 락 충돌이 나므로 프로세스 안에서 미러별로 직렬화한다.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from remote_ci_monitor.core.gitref import is_full_sha, pick_sha, short_sha

LogFn = Callable[[str], None]
RunFn = Callable[..., "subprocess.CompletedProcess[str]"]
STDERR_TAIL_LINES = 20
_PASSTHROUGH_ENV = ("PATH", "HOME", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND", "XDG_CONFIG_HOME", "TMPDIR")
_mirror_locks: dict[str, threading.Lock] = {}
_mirror_locks_guard = threading.Lock()


class GitError(Exception):
    """git 호출 실패. 메시지는 짧고 경로·URL 이 없다(잡 summary 에 실린다)."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


class GitTimeout(GitError):
    """상한을 넘겼다. 서버는 이걸로 504 를, fetch 루프는 재시도 중단을 판단한다."""


def _fmt_seconds(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600 and s % 3600 == 0:
        return f"{s // 3600}h"
    if s >= 60 and s % 60 == 0:
        return f"{s // 60}m"
    return f"{s}s"


def git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """git 자식 프로세스 env. 프롬프트 금지 · 영어 메시지 · 자격 관련 변수만 통과."""
    src = os.environ if base is None else base
    env = {k: src[k] for k in _PASSTHROUGH_ENV if k in src}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def _mirror_lock(mirror: Path) -> threading.Lock:
    # 키는 미러가 생기기 전후로 같아야 한다 — `resolve()` 는 생긴 뒤에만 심볼릭 링크
    # (/var → /private/var)를 풀어 다른 키를 주므로 존재 여부와 무관한 절대 경로를 쓴다
    key = os.path.abspath(mirror)
    with _mirror_locks_guard:
        lock = _mirror_locks.get(key)
        if lock is None:
            lock = _mirror_locks[key] = threading.Lock()
        return lock


def _run_process(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """`subprocess.run` 과 같되, 타임아웃이면 프로세스 **그룹**을 죽인다.

    `subprocess.run` 은 직접 자식(git)만 죽여서 ssh · credential helper · fetch-pack 같은
    손자가 멈춘 원격을 붙들고 남는다. `start_new_session` 으로 git 이 세션 리더이므로 그
    pid 의 그룹을 SIGKILL 한다(세션을 안 만든 호출은 우리 그룹일 수 있어 자식만 죽인다).
    """
    timeout = kwargs.pop("timeout", None)
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    own_group = bool(kwargs.get("start_new_session")) and hasattr(os, "killpg")
    with subprocess.Popen(argv, **kwargs) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if own_group:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
            raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _run_git(
    argv: list[str],
    *,
    what: str,
    timeout: float,
    run: RunFn = _run_process,
    cwd: Path | None = None,
    log: LogFn | None = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd is not None else None,
        "env": git_env(),
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "start_new_session": True,
    }
    try:
        proc = run(["git", *argv], **kwargs)
    except subprocess.TimeoutExpired as e:
        raise GitTimeout(f"{what} timed out after {_fmt_seconds(timeout)}") from e
    except FileNotFoundError as e:
        raise GitError("git is not installed on the build machine") from e
    except OSError as e:
        raise GitError(f"{what} could not start git: {type(e).__name__}") from e
    stderr = getattr(proc, "stderr", "") or ""
    if log is not None and stderr.strip():
        for line in stderr.strip().splitlines()[-STDERR_TAIL_LINES:]:
            log(f"[git] {line}")
    if proc.returncode != 0:
        # 「잡 로그를 보라」는 stderr 가 실제로 그리로 갔을 때만 — 제출 시점(ls-remote)엔 잡이 없다
        where = " — see the job log" if log is not None else ""
        raise GitError(f"{what} failed (exit {proc.returncode}){where}", stderr)
    return proc


def resolve_ref(url: str, ref: str, *, timeout: float, run: RunFn = subprocess.run) -> str:
    """원격에서 ref 가 가리키는 커밋 sha. 40 hex 는 원격을 부르지 않고 그대로."""
    if is_full_sha(ref):
        return ref.lower()
    # 패턴을 주면 ls-remote 가 annotated 태그의 peeled 줄(`^{}`)을 안 찍는다 — 두 번째 패턴으로 요청
    proc = _run_git(
        ["ls-remote", "--", url, ref, f"{ref}^{{}}"],
        what="git ls-remote",
        timeout=timeout,
        run=run,
    )
    sha = pick_sha(proc.stdout or "", ref)
    if sha is None:
        raise GitError(f"ref '{ref}' not found in the remote")
    return sha


def ensure_mirror(mirror: Path, url: str, *, timeout: float, log: LogFn | None = None) -> None:
    """미러(bare)가 없으면 만든다. 있으면 그대로. 자동 gc 는 끈다(공유 객체를 지우지 않게).

    두 레인이 같은 레포의 첫 잡을 동시에 받으면 `git init` 이 겹치므로 fetch 와 같은 락 아래서 한다.
    """
    with _mirror_lock(mirror):
        if (mirror / "HEAD").is_file():
            return
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _run_git(
            ["init", "--bare", "-q", "--", str(mirror)],
            what="git init",
            timeout=timeout,
            log=log,
        )
        _run_git(
            ["--git-dir", str(mirror), "config", "gc.auto", "0"],
            what="git config",
            timeout=timeout,
            log=log,
        )


_FULL_REFSPECS = ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")


def _fetch(mirror: Path, url: str, refspecs: tuple[str, ...], *, prune: bool, timeout, log):
    # `-q` 없이: 어떤 ref 가 어떻게 움직였는지(forced update 포함)가 잡 로그에 남아야 한다
    argv = ["--git-dir", str(mirror), "fetch", "--no-recurse-submodules"]
    if prune:
        argv.append("--prune")
    else:
        argv.append("--no-tags")
    _run_git([*argv, "--", url, *refspecs], what="git fetch", timeout=timeout, log=log)


def _targeted_refspecs(ref: str) -> list[tuple[str, ...]]:
    """ref 하나만 받는 refspec 후보. 완전한 refname 이면 그것, 아니면 heads → tags 순."""
    if ref.startswith("refs/"):
        return [(f"+{ref}:{ref}",)]
    return [(f"+refs/heads/{ref}:refs/heads/{ref}",), (f"+refs/tags/{ref}:refs/tags/{ref}",)]


def fetch_ref(
    mirror: Path,
    url: str,
    ref: str,
    *,
    timeout: float,
    log: LogFn | None = None,
    want_sha: str | None = None,
) -> None:
    """ref 하나만 먼저 받고, 그것으로 `want_sha` 가 안 오면 미러 전체(heads · tags)를 맞춘다.

    대형 레포에서 매 잡 전체 fetch 를 피하려는 것. 40 hex ref 나 후보 refspec 이 전부 실패한
    경우는 전체 fetch 로 떨어진다. 같은 미러는 프로세스 안에서 직렬화한다.
    """
    with _mirror_lock(mirror):
        if not is_full_sha(ref):
            for refspecs in _targeted_refspecs(ref):
                try:
                    # 후보가 원격에 없을 때의 「couldn't find remote ref」 는 정상 — 로그 생략
                    _fetch(mirror, url, refspecs, prune=False, timeout=timeout, log=None)
                except GitTimeout:
                    raise  # 멈춘 원격을 후보마다 다시 기다리지 않는다
                except GitError:
                    continue
                if log is not None:
                    log(f"[rcm] fetched {refspecs[0].split(':')[0].lstrip('+')}")
                if want_sha is None or has_commit(mirror, want_sha):
                    return
        _fetch(mirror, url, _FULL_REFSPECS, prune=True, timeout=timeout, log=log)


def has_commit(mirror: Path, sha: str) -> bool:
    """미러에 그 커밋이 있는가. sha 는 40 hex 여야 한다."""
    if not is_full_sha(sha) or not (mirror / "HEAD").is_file():
        return False
    try:
        subprocess.run(
            ["git", "--git-dir", str(mirror), "cat-file", "-e", f"{sha.lower()}^{{commit}}"],
            env=git_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def checkout(
    mirror: Path, workspace: Path, sha: str, *, timeout: float, log: LogFn | None = None
) -> None:
    """미러에서 로컬 clone(객체는 하드링크 — 미러가 gc 해도 워크스페이스는 안 깨진다)을
    만들고 sha 를 detached 로 체크아웃한다. `.git` 이 남아 `git describe` 가 된다."""
    if not is_full_sha(sha):
        raise GitError("checkout needs a full commit sha")
    if not has_commit(mirror, sha):
        raise GitError(f"commit {short_sha(sha)} is not in the mirror")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        ["clone", "-q", "--no-checkout", "--", str(mirror), str(workspace)],
        what="git clone",
        timeout=timeout,
        log=log,
    )
    _run_git(
        ["checkout", "-q", "--detach", sha.lower()],
        what="git checkout",
        timeout=timeout,
        cwd=workspace,
        log=log,
    )
