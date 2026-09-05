"""테스트 공용 — 임시 bare 「원격」 레포. 네트워크를 부르지 않는다(url 은 tmp 안의 파일 경로).

모양(`build_remote`):

    main : c1(hello.txt) → c2(second.txt) → c3(third.txt)      ← annotated 태그 v1.0.0 은 c3
    dev  : c3 → c4(dev.txt)                                     ← lightweight 태그 lw 는 c2

`work` 는 원격에 push 할 수 있는 작업 클론(origin → bare). ref 이동·강제 push 시나리오에 쓴다.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

HELLO = "hello from main\n"


def git(*args: str, cwd: Path | None = None) -> str:
    """전역·시스템 git 설정을 무시하고 돌려 stdout 을 돌려준다. 실패는 `CalledProcessError`."""
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    argv = [
        "git",
        "-c",
        "user.name=rcm-test",
        "-c",
        "user.email=rcm-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
        "-c",
        "init.defaultBranch=main",
        *args,
    ]
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def commit(work: Path, name: str, content: str, message: str) -> str:
    """파일 하나를 쓰고 커밋한다. 새 HEAD sha."""
    (work / name).write_text(content)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", message, cwd=work)
    return git("rev-parse", "HEAD", cwd=work)


@dataclass(frozen=True)
class RemoteRepo:
    url: str  # bare 레포 경로 — 파일 경로다. 네트워크 URL 이 아니다
    bare: Path
    work: Path
    main: str  # c3
    dev: str  # c4
    lw: str  # c2 — lightweight 태그가 가리키는 커밋
    v1_commit: str  # annotated 태그 v1.0.0 이 가리키는 커밋(== main)
    v1_tag: str  # 태그 객체 자신의 sha(커밋이 아니다)

    def push_commit(self, name: str, content: str, message: str, *, force: bool = False) -> str:
        """work 의 main 에 커밋 하나를 더해 원격 main 으로 push 한다. 새 main sha."""
        sha = commit(self.work, name, content, message)
        flags = ["--force"] if force else []
        git("push", "-q", *flags, "origin", "main", cwd=self.work)
        return sha

    def rewind_main(self, n: int = 1) -> None:
        """work 의 main 을 n 커밋 되돌린다(강제 push 준비)."""
        git("reset", "-q", "--hard", f"HEAD~{n}", cwd=self.work)


def build_remote(root: Path) -> RemoteRepo:
    work = root / "work"
    work.mkdir()
    git("init", "-q", cwd=work)
    commit(work, "hello.txt", HELLO, "first")
    lw = commit(work, "second.txt", "second\n", "second")
    git("tag", "lw", cwd=work)
    main = commit(work, "third.txt", "third\n", "third")
    git("tag", "-a", "-m", "release 1.0.0", "v1.0.0", cwd=work)
    v1_tag = git("rev-parse", "v1.0.0", cwd=work)
    v1_commit = git("rev-parse", "v1.0.0^{commit}", cwd=work)
    git("checkout", "-q", "-b", "dev", cwd=work)
    dev = commit(work, "dev.txt", "dev only\n", "dev")
    git("checkout", "-q", "main", cwd=work)
    bare = root / "remote.git"
    git("clone", "-q", "--bare", str(work), str(bare))
    git("remote", "add", "origin", str(bare), cwd=work)
    assert v1_tag != v1_commit and v1_commit == main
    return RemoteRepo(
        url=str(bare),
        bare=bare,
        work=work,
        main=main,
        dev=dev,
        lw=lw,
        v1_commit=v1_commit,
        v1_tag=v1_tag,
    )


def isolate_git_env(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """gitops 가 넘기는 HOME 을 빈 디렉터리로 바꿔 개발자의 ~/.gitconfig 가 끼어들지 않게 한다."""
    home = root / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))


def install_hanging_git(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, hang_on: str, grandchild: bool = False
) -> Path:
    """PATH 맨 앞에 가짜 `git` 을 둔다 — 부명령 `hang_on` 이면 잠들고, 나머지는 진짜 git 으로.

    호출마다 인자 한 줄을 돌려주는 경로(`calls.log`)에 적는다 — 재시도 횟수를 셀 수 있다.
    gitops 가 argv[0] 을 `git` 으로 두고 PATH 로 찾는다는 전제다(스펙 §1.3: `PATH` 전달).
    `grandchild=True` 면 잠들 때 `sleep` 을 손자로 띄우고 `<자기 pid> <손자 pid>` 를
    `fakebin/pids` 에 적는다 — 프로세스 그룹 kill 을 확인하는 데 쓴다.
    """
    real = shutil.which("git")
    assert real is not None
    bin_dir = root / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    calls = bin_dir / "calls.log"
    pids = bin_dir / "pids"
    script = bin_dir / "git"
    if grandchild:
        hang = f'sleep 30 & echo "$$ $!" > "{pids}"; wait; exit 0'
    else:
        hang = "exec sleep 30"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f'for a in "$@"; do [ "$a" = {hang_on} ] && {{ {hang}; }}; done\n'
        f'exec "{real}" "$@"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls
