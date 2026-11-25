"""`rcm doctor` — 이 머신이 무엇을 빌드하고 시험할 수 있는지, 무엇을 더 깔아야 하는지(M5t-a 결정
246 · 247, docs/m5t-workplan.md §4.5).

재는 쪽이다. 판정과 출력 모양은 `core/doctor.py` 에 있다.

- **측정 환경.** 셸의 환경이 아니라 깨끗한 서비스 환경에서 잰다: `env -i` 에 서비스 관리자의
  기본 변수와 서비스 파일의 환경 변수만. 셸의 `JAVA_HOME` 이 새지 않는다.
- **서버.** 워커는 `/api/health` · `/api/whoami` · `/worker/spec` 을, coordinator 계열은 로컬
  `server.toml` 의 bind · port 로 `/api/health` · `/api/machines` 를 GET 으로만 읽는다. DB 는 열지
  않고 서버에는 읽기 요청만 보낸다 — 등록도 능력 보고도 없다(조언일 뿐이다, 위임 결정 E2).
- **mDNS 로 찾은 서버**에는 사람이 확인하기 전에 어떤 요청도 보내지 않는다. 비TTY 면 종료 3.
- **출력**에는 홈 경로 · 프리셋 env 값 · 서비스 파일의 환경 변수 값이 없다.
"""

from __future__ import annotations

import json
import math
import os
import plistlib
import re
import shlex
import sqlite3
import sys
import tarfile
import xml.parsers.expat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from remote_ci_monitor import __version__, hostsample, runner
from remote_ci_monitor.core import doctor as dc
from remote_ci_monitor.core import jobenv

#: 플랫폼 — 시험이 바꿔 끼운다(Local Network 안내 · 설치 문구 · 서비스 관리자)
PLATFORM = sys.platform
MIN_PYTHON = (3, 11, 4)
MIN_GIT_TOKEN_FILE = (2, 31)
#: `git --version` 의 시한(초) — 시험이 줄인다
GIT_VERSION_TIMEOUT = 10.0
MIN_SQLITE = (3, 24, 0)
FD_WANTED = 4096
#: 표준 라벨의 설치 자리(launchd · systemd 사용자 유닛)
LAUNCH_AGENTS_GLOB = "com.remote-ci-monitor.*.plist"
SYSTEMD_GLOB = "rcm-*.service"
#: 시스템 자리 — 서비스 관리자가 root 로 읽는다. 문서의 Linux 서버 설치는 `examples/systemd/
#: rcm-server.service` 를 `/etc/systemd/system/` 에 넣는다(`User=rcm`). 시험이 바꿔 끼운다
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
LAUNCH_DAEMONS_DIR = Path("/Library/LaunchDaemons")
#: 서비스의 환경을 바꾸지만 rcm 이 따라가지 않는 systemd 줄(P3 2라운드 MED). `EnvironmentFile=` 의
#: 값은 `Environment=` 를 덮으므로 PATH 도 모른다
SYSTEMD_UNFOLLOWED = ("EnvironmentFile", "PassEnvironment", "UnsetEnvironment")
#: 라벨(plist 의 `Label` · 파일 이름)이 이 이름으로 끝나면 rcm 의 서비스처럼 보인다
#: (`com.remote-ci-monitor.worker` · `rcm-server.service`)
RCM_LABEL_ROLES = ("worker", "server")
#: launchd 는 `WorkingDirectory` 가 없으면 `/` 에서 띄운다(launchd.plist(5) WorkingDirectory)
LAUNCHD_DEFAULT_WORKDIR = "/"
#: 역할마다 서비스 파일이 띄워야 하는 `rcm` 하위 명령 — 후보를 이것으로 거른다(6-B-V2)
ROLE_COMMANDS = {
    dc.ROLE_WORKER: "worker",
    dc.ROLE_COORDINATOR: "serve",
    dc.ROLE_COORDINATOR_WORKER: "serve",
}
REQUEST_TIMEOUT = 10.0
WORKER_TOKEN_HINT = "a worker token is needed (rcm token add NAME --worker on the server)"
CLIENT_TOKEN_HINT = "a client token is needed (rcm token add NAME on the server)"
DISCOVER_SECONDS = 1.5

#: rcm 자신의 의존성에 한한 OS 별 일반 설치 안내(결정 246 · Q7). 프리셋 도구의 안내는 `[[tools]]`
INSTALL: dict[str, dict[str, str]] = {
    "python": {
        "darwin": "brew install python@3.13",
        "apt-get": "sudo apt-get install python3.12 python3.12-venv",
        "dnf": "sudo dnf install python3.12",
        "pacman": "sudo pacman -S python",
        "any": "install Python 3.12 or newer (https://www.python.org/downloads/)",
    },
    "git": {
        "darwin": "xcode-select --install",
        "apt-get": "sudo apt-get install git",
        "dnf": "sudo dnf install git",
        "pacman": "sudo pacman -S git",
        "any": "install git with the system's package manager",
    },
}


class DoctorUsage(Exception):
    """사용법 오류 — 종료 2."""


class ConfigUnknown(Exception):
    """서비스가 어느 설정으로 도는지 모른다 — 사용법 오류가 아니라 「판정 못 함」(종료 3)이다.
    서비스 파일을 못 읽었거나, 그 파일이 가리키는 설정을 doctor 가 못 읽었다(P3 4라운드 HIGH)."""


class ServiceFileUnreadable(Exception):
    """서비스 파일을 읽지 못했다 — 사용법 오류가 아니라 「판정 못 함」(종료 3)이다. `reason` 에는
    파일의 값이 들지 않는다(TD55)."""

    def __init__(self, path: Path, reason: str):
        super().__init__(f"{path.name}: {reason}")
        self.path, self.reason = path, reason


def is_tty() -> bool:
    """사람에게 물어도 되나 — stdin 과 stderr 가 모두 TTY 일 때만(결정 240 과 같은 조건)."""
    return sys.stdin.isatty() and sys.stderr.isatty()


def ask(question: str) -> bool:
    sys.stderr.write(f"{question} [y/N] ")
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def choose(paths: list[Path], home: str) -> Path | None:
    sys.stderr.write("several service files match:\n")
    for i, p in enumerate(paths, 1):
        sys.stderr.write(f"  {i}) {dc.mask_home(str(p), home)}\n")
    sys.stderr.write("which one? ")
    sys.stderr.flush()
    answer = sys.stdin.readline().strip()
    if answer.isdigit() and 1 <= int(answer) <= len(paths):
        return paths[int(answer) - 1]
    return None


def lookup_user(name: str) -> tuple[str, str, str] | None:
    """(이름, 홈, 셸) — 시스템 서비스가 도는 사용자(`User=` · `UserName`). 이 머신이 모르는
    사용자면 None. 숫자는 uid 다. 시험이 바꿔 끼운다."""
    import pwd

    try:
        pw = pwd.getpwuid(int(name)) if name.isdigit() else pwd.getpwnam(name)
    except (KeyError, ValueError, OverflowError):
        return None
    return pw.pw_name, pw.pw_dir, pw.pw_shell


def python_version() -> tuple[int, ...]:
    return tuple(sys.version_info[:3])


# ── rcm 자신의 요구 ──────────────────────────────────────────────────────────


def install_hint(what: str, *, platform: str, package_manager: str | None, root: bool) -> str:
    """rcm 자신의 의존성 설치 문구. root 면 sudo 를 뺀다(컨테이너, TD7)."""
    table = INSTALL[what]
    if platform.startswith("darwin"):
        text = table["darwin"]
    else:
        text = table.get(package_manager or "", table["any"])
    return dc.without_sudo(text) if root else text


def requirement(
    name: str, state: str, detail: str, needed_for: str, install: str = ""
) -> dict[str, Any]:
    return {
        "name": name,
        "state": state,
        "detail": detail,
        "needed_for": needed_for,
        "install": install,
    }


def python_requirement(
    version: tuple[int, ...],
    has_filter: bool,
    *,
    platform: str,
    package_manager: str | None,
    root: bool = False,
) -> dict[str, Any]:
    """Python 3.11.4+ 와 `tarfile.data_filter`(산출물 · 트리를 안전하게 푼다). 미달이면 FAIL."""
    shown = ".".join(str(x) for x in version[:3])
    ok = tuple(version[:3]) >= MIN_PYTHON and has_filter
    if ok:
        return requirement("python", "ok", f"{shown} · tarfile data filter", "every role")
    detail = f"{shown} — rcm needs Python 3.11.4+ with the tarfile data filter; use 3.12+"
    hint = install_hint("python", platform=platform, package_manager=package_manager, root=root)
    return requirement("python", "fail", detail, "every role", hint)


def _git_version(git: str, env: Mapping[str, str]) -> tuple[int, int] | None:
    """서비스 환경으로 돈다 — 셸의 환경(`GIT_EXEC_PATH` 같은 것)이 다른 답을 주지 않게. 자기
    프로세스 그룹에서 돌고 시한을 넘기면 그룹째 거둔다 — 감싼 git 의 손자가 doctor 뒤에 남지
    않게(P6 GAP E2)."""
    import subprocess

    from remote_ci_monitor.gitops import _run_process

    try:
        proc = _run_process(
            [git, "--version"],
            capture_output=True,
            text=True,
            timeout=GIT_VERSION_TIMEOUT,
            stdin=subprocess.DEVNULL,
            env=dict(env),
            start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.(\d+)", proc.stdout or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def git_requirement(
    env: Mapping[str, str],
    *,
    token_file: bool,
    platform: str,
    package_manager: str | None,
    root: bool,
) -> dict[str, Any]:
    """`[[repos]]` 나 `git_ref` 를 쓰면 git 이 필수, `token_file` 을 쓰면 2.31 이상."""
    path = env.get("PATH", "")
    git = runner.find_tool("git", path)
    needed = "[[repos]] and git_ref presets"
    hint = install_hint("git", platform=platform, package_manager=package_manager, root=root)
    if git is None:
        note = runner.missing_note("git", env) or "missing"
        return requirement("git", "fail", f"{note} on the service PATH", needed, hint)
    if token_file:
        version = _git_version(git, env)
        if version is None:
            # 버전을 읽지 못한 것은 「낮다」가 아니다 — 모름이다(P3 3라운드)
            detail = "cannot read its version — token_file needs git 2.31+"
            return requirement("git", "unknown", detail, needed)
        if version < MIN_GIT_TOKEN_FILE:
            shown = ".".join(map(str, version))
            return requirement("git", "fail", f"{shown} — token_file needs git 2.31+", needed, hint)
    return requirement("git", "ok", "on the service PATH", needed)


def sampler_requirement(env: Mapping[str, str], platform: str) -> dict[str, Any]:
    """호스트 샘플러가 부르는 명령(`hostsample` 의 상수). 없으면 부하 표본과 게이트가 null 이다."""
    if platform.startswith("darwin"):
        names = [
            hostsample.MAC_VM_STAT[0],
            hostsample.MAC_MEMSIZE[0],
            hostsample.MAC_TOP[0],
            hostsample.MAC_PS[0],
            hostsample.MAC_IOREG[0],
        ]
    else:
        names = [hostsample.LINUX_PS[0]]
    lost = runner.missing_tools(tuple(names), env, cwd="/")
    if not lost:
        return requirement("host sampler", "ok", " · ".join(names), "host load and admission")
    detail = f"{', '.join(lost)} not on the service PATH (it needs /usr/sbin)"
    return requirement("host sampler", "warn", detail, "host load and admission")


def local_network_requirement() -> dict[str, Any]:
    """macOS 15+ 의 Local Network 권한 — rcm 은 판정할 수 없다. 안내만 한다(TD50)."""
    return requirement(
        "local network",
        "info",
        "macOS asks once for Local Network permission when the service first talks on the "
        "network; rcm cannot check it (docs/operating.md)",
        'advertising and server = "auto"',
    )


def sleep_requirement() -> dict[str, Any]:
    """잠자는 Mac 은 잡을 받지 않는다 — rcm 은 전원 설정을 재지 않고 안내만 한다(위임 결정 6)."""
    return requirement(
        "sleep",
        "info",
        "keep the machine awake (pmset -a sleep 0); rcm does not check it",
        "jobs that run while nobody is at the machine",
    )


def unknown_path_requirement(name: str, needed_for: str) -> dict[str, Any]:
    """서비스 PATH 를 모르면 PATH 로 찾는 도구는 재지 않는다 — 지어낸 PATH 로 「빠짐」을 내지
    않는다(위임 결정 5)."""
    return requirement(name, "info", "not checked — the service PATH is unknown", needed_for)


def fd_requirement(service: ServiceFile | None) -> dict[str, Any]:
    needed = "the coordinator service"
    if service is None:
        return requirement("open files", "warn", "no service file to read the limit from", needed)
    if service.nofile is None or service.nofile < FD_WANTED:
        shown = "not set" if service.nofile is None else str(service.nofile)
        detail = f"{shown} in the service file — set {FD_WANTED} (docs/operating.md)"
        return requirement("open files", "warn", detail, needed)
    return requirement("open files", "ok", str(service.nofile), needed)


def sqlite_requirement() -> dict[str, Any]:
    version = sqlite3.sqlite_version_info
    shown = sqlite3.sqlite_version
    if tuple(version) < MIN_SQLITE:
        return requirement("sqlite", "fail", f"{shown} — needs 3.24+ (UPSERT)", "the coordinator")
    return requirement("sqlite", "ok", shown, "the coordinator")


def linger_requirement(user: str | None) -> dict[str, Any] | None:
    """Linux 사용자 유닛은 로그아웃 뒤에도 돌려면 linger 가 켜져 있어야 한다."""
    if not user:
        return None
    if Path(f"/var/lib/systemd/linger/{user}").exists():
        return requirement("linger", "ok", "enabled", "a systemd user service")
    return requirement(
        "linger",
        "warn",
        "off — the service stops when you log out",
        "a systemd user service",
        f"sudo loginctl enable-linger {user}",
    )


# ── 서비스 파일 ──────────────────────────────────────────────────────────────


@dataclass
class ServiceFile:
    path: Path | None = None
    manager: str = ""
    command: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    nofile: int | None = None
    program: str | None = None  # 띄우는 실행 파일(argv[0]) — venv 의 bin 이 PATH 에 있는지 본다
    #: 읽지 못한 줄(systemd) — `line 3 (Environment=)` 처럼 줄 번호와 키만. 값은 담지 않는다
    unreadable_lines: list[str] = field(default_factory=list)
    #: 읽었지만 rcm 이 따라가지 않는 줄(`EnvironmentFile=` · 풀지 않는 지정자 `%h`) — 줄과 이유
    unfollowed: list[str] = field(default_factory=list)
    #: 그 줄 때문에 서비스의 PATH 를 모른다
    path_unknown: bool = False
    #: 서비스가 rcm 을 띄우는 인자를 rcm 자신의 파서로 읽은 것(`command` · `config` · `port` …).
    #: rcm 을 띄우는지, 어느 하위 명령인지 풀지 못했으면 None
    launch: dict[str, Any] | None = None
    #: 띄우는 명령 줄에 rcm 이 풀지 않는 지정자가 있다(`%S` · 사용자 유닛인지 모르는 파일의 `%h`) —
    #: 명령(역할)은 알지만 그 인자의 값(`--config` …)은 모른다
    launch_unexpanded: bool = False
    #: plist 의 `Label`, systemd 는 파일 이름 — rcm 의 서비스처럼 보이는지 가린다
    label: str | None = None
    #: 띄우는 인자 전부(실행 파일 포함) — 풀지 못한 명령이 rcm 을 띄우는 것처럼 보이는지 본다
    argv: list[str] = field(default_factory=list)
    #: 서비스의 작업 폴더(`WorkingDirectory`) — 적혀 있지 않으면 None
    workdir: str | None = None
    #: 작업 폴더가 적혀 있지만 rcm 이 풀지 못한다(지정자 · 상대 경로)
    workdir_unknown: bool = False
    #: 사용자 유닛인 것을 안다(`~/.config/systemd/user/` · setup 자리) — `%h` 가 그 홈이다
    user_unit: bool = False
    #: 시스템 자리(`/etc/systemd/system` · `/Library/LaunchDaemons`)의 파일 — 관리자가 root 로
    #: 읽고 `run_user`(없으면 root)로 띄운다
    system: bool = False
    #: `User=` · `UserName` 의 값(적혀 있지 않으면 None)
    run_user: str | None = None

    @property
    def env_names(self) -> list[str]:
        """환경 변수 **이름**만 — 값은 어디에도 찍지 않는다(TD55)."""
        return sorted(self.env)


def rcm_launch(program: str | None, argv: list[str]) -> dict[str, Any] | None:
    """서비스가 띄우는 명령 → rcm 이 그 인자를 읽은 결과(`{"command": "worker", "config": …}`).

    실행 파일이 `rcm`(또는 `python -m remote_ci_monitor.cli`)이고 rcm 자신의 파서가 그 인자를
    받을 때만 푼다 — `--config=X` · 줄여 쓴 옵션도 서비스가 뜰 때와 똑같이 읽는다. `env rcm …` ·
    `sh -c` 처럼 rcm 을 바로 띄우지 않거나 rcm 이 받지 않을 인자면 None(풀지 못함)이다."""
    import contextlib
    import io

    if not program:
        return None
    exe = os.path.basename(program)
    if exe == "rcm":
        rest = argv[1:]
    elif exe.startswith("python") and argv[1:3] == ["-m", "remote_ci_monitor.cli"]:
        rest = argv[3:]
    else:
        return None
    from remote_ci_monitor import cli

    try:
        # 파서는 틀린 인자에 사용법을 찍고 SystemExit 을 낸다 — doctor 의 출력에 섞이지 않게 막는다
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            args = cli.build_parser().parse_args(rest)
    except SystemExit:
        return None
    return {k: v for k, v in vars(args).items() if k != "func"}


def looks_like_rcm(sf: ServiceFile) -> bool:
    """띄우는 명령을 rcm 의 하위 명령으로 풀지 못한 파일이 rcm 의 서비스일 수 있나.

    실행 파일과 인자 어디에도 rcm(`rcm` 실행 파일, `remote_ci_monitor` 모듈)이 없고 라벨도
    worker · server 가 아니면 rcm 의 서비스가 아님이 확실하다. 그런 파일은 후보가 아니다 —
    rcm-ci-connect 스킬의 `com.remote-ci-monitor.ci-watch.plist`(`python3 rcm_ci_watch.py`)가
    그렇다(검증 사이클 2 의 1라운드 HIGH 1). `sh -c "exec rcm worker"` 처럼 한 인자에 든 명령은
    낱말로 나눠 본다."""
    for arg in [sf.program or "", *sf.argv]:
        for word in arg.split():
            if os.path.basename(word) == "rcm":
                return True
            if word == "remote_ci_monitor" or word.startswith("remote_ci_monitor."):
                return True
    names = [sf.label or "", sf.path.stem if sf.path is not None else ""]
    for name in names:
        tail = name.removeprefix("com.remote-ci-monitor.").removeprefix("rcm-")
        if tail in RCM_LABEL_ROLES:
            return True
    return False


def expand_specifiers(value: str, home: str | None) -> str | None:
    """systemd 지정자를 푼다 — `%%` 는 `%`, `%h` 는 사용자 유닛의 홈(`home`, 모르면 None).
    다른 지정자는 rcm 이 풀지 않는다: None(모름)."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        spec = value[i + 1 : i + 2]
        if spec == "%":
            out.append("%")
        elif spec == "h" and home:
            out.append(home.rstrip("/") or "/")
        else:
            return None
        i += 2
    return "".join(out)


def _plist_reason(e: Exception) -> str:
    """plist 를 못 읽은 이유 — 형식 이름과 위치만. 메시지에는 파일의 값이 섞일 수 있다."""
    if isinstance(e, xml.parsers.expat.ExpatError):
        return f"not valid XML (line {e.lineno}, column {e.offset})"
    return f"not a valid plist ({type(e).__name__})"


def _read_plist(p: Path) -> ServiceFile:
    try:
        doc = plistlib.loads(p.read_bytes())
    except OSError as e:
        raise ServiceFileUnreadable(p, type(e).__name__) from e
    except (ValueError, plistlib.InvalidFileException, xml.parsers.expat.ExpatError) as e:
        # ExpatError(잘린 XML)는 ValueError 가 아니다 — 따로 잡지 않으면 traceback 이 난다
        raise ServiceFileUnreadable(p, _plist_reason(e)) from e
    if not isinstance(doc, dict):
        raise ServiceFileUnreadable(p, f"the top level is a {type(doc).__name__}, not a dict")
    raw_args = doc.get("ProgramArguments") or []
    raw_env = doc.get("EnvironmentVariables") or {}
    limits = doc.get("SoftResourceLimits") or {}
    program = doc.get("Program")
    if not isinstance(raw_args, list):
        raise ServiceFileUnreadable(p, "ProgramArguments is not an array")
    if not isinstance(raw_env, dict):
        raise ServiceFileUnreadable(p, "EnvironmentVariables is not a dict")
    if not isinstance(limits, dict):
        raise ServiceFileUnreadable(p, "SoftResourceLimits is not a dict")
    nofile = limits.get("NumberOfFiles")
    if nofile is not None and (isinstance(nofile, bool) or not isinstance(nofile, int)):
        raise ServiceFileUnreadable(p, "SoftResourceLimits.NumberOfFiles is not an integer")
    if program is not None and not isinstance(program, str):
        raise ServiceFileUnreadable(p, "Program is not a string")
    workdir = doc.get("WorkingDirectory")
    if workdir is not None and not isinstance(workdir, str):
        raise ServiceFileUnreadable(p, "WorkingDirectory is not a string")
    label = doc.get("Label")
    user = doc.get("UserName")
    if user is not None and not isinstance(user, str):
        raise ServiceFileUnreadable(p, "UserName is not a string")
    # launchd 는 인자와 환경 변수 값을 문자열로만 받는다 — `str()` 로 바꿔 「PATH 가 7」로 재지
    # 않는다(P3 4라운드 LOW). 이유에는 이름만 넣고 값은 넣지 않는다
    if not all(isinstance(a, str) for a in raw_args):
        raise ServiceFileUnreadable(p, "ProgramArguments has an item that is not a string")
    for k, v in raw_env.items():
        if not isinstance(v, str):
            raise ServiceFileUnreadable(
                p, f"EnvironmentVariables.{dc.clean_text(k, 64)} is not a string"
            )
    args = list(raw_args)
    env = dict(raw_env)
    exe = str(program or (args[0] if args else "")) or None
    launch = rcm_launch(exe, args)
    return ServiceFile(
        p,
        dc.LAUNCHD,
        None if launch is None else launch["command"],
        env,
        nofile or None,
        exe,
        launch=launch,
        label=label if isinstance(label, str) else None,
        argv=args,
        workdir=workdir or None,
        # launchd 는 `~` 를 풀지 않는다 — 절대 경로가 아니면 그 폴더를 모른다
        workdir_unknown=bool(workdir) and not os.path.isabs(str(workdir)),
        run_user=user or None,
    )


def read_service_file(
    path: str | os.PathLike[str], *, user_home: str | None = None, system: bool = False
) -> ServiceFile:
    """launchd plist 나 systemd 유닛을 읽는다. 파일을 못 읽으면 `ServiceFileUnreadable`.

    `user_home` 은 그 유닛이 사용자 유닛인 것을 알 때(`~/.config/systemd/user/` · setup 자리)의
    홈이다. 그때 `%h` 는 그 홈으로 푼다 — systemd 가 사용자 유닛의 `%h` 를 그 사용자의 홈으로 풀기
    때문이다(systemd.unit 「Specifiers」). 시스템 유닛의 `%h` 는 `/root` 라서, 어느 쪽인지 모르는
    파일에서는 풀지 않는다(검증 사이클 2 의 1라운드 MED 4).

    systemd 유닛의 줄 하나를 못 읽으면(닫히지 않은 따옴표 · 숫자가 아닌 한도) 그 줄을 건너뛰되
    `unreadable_lines` 에 남긴다 — 조용히 버리면 「PATH 를 주지 않았다」 같은 틀린 안내가 나간다.

    rcm 이 따라가지 않는 줄(`SYSTEMD_UNFOLLOWED`)과 rcm 이 풀지 않는 지정자가 든 `Environment=` ·
    `ExecStart=` 값은 `unfollowed` 에 남긴다. 지정자가 든 값은 환경에 넣지 않는다 — 글자 그대로
    재면 없는 「빠짐」(종료 1)을 지어낸다. PATH 에 닿으면 `path_unknown` 이다. `$` 는 남긴다:
    systemd 는 `Environment=` 에서 변수를 펼치지 않으므로(systemd.exec 「Environment=」) 글자
    그대로가 서비스가 실제로 받는 값이다.
    """
    p = Path(path)
    if p.suffix == ".plist":
        return replace(_read_plist(p), system=system)
    home = None if system else (user_home or None)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ServiceFileUnreadable(p, type(e).__name__) from e
    except ValueError as e:  # UnicodeDecodeError
        raise ServiceFileUnreadable(p, "not UTF-8") from e
    env: dict[str, str] = {}
    command: str | None = None
    program: str | None = None
    launch: dict[str, Any] | None = None
    nofile_unit: int | None = None
    unreadable: list[str] = []
    unfollowed: list[str] = []
    path_unknown = False
    argv: list[str] = []
    unexpanded = False
    workdir: str | None = None
    workdir_unknown = False
    user: str | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        key, _, value = line.partition("=")
        try:
            if key in SYSTEMD_UNFOLLOWED:
                unfollowed.append(f"line {number} ({key}=): rcm does not follow {key}=")
                if key == "EnvironmentFile" or "PATH" in value.split():
                    path_unknown = True
            elif key == "Environment":
                for item in shlex.split(value):
                    k, sep, v = item.partition("=")
                    if not sep:
                        continue
                    expanded = expand_specifiers(v, home)
                    if expanded is None:
                        unfollowed.append(
                            f"line {number} ({key}=): rcm does not expand systemd specifiers "
                            f"such as %h in {k}"
                        )
                        path_unknown = path_unknown or k == "PATH"
                        env.pop(k, None)
                        continue
                    env[k] = expanded
            elif key == "ExecStart":
                argv = shlex.split(value)
                expanded_argv = [expand_specifiers(a, home) for a in argv]
                unexpanded = any(a is None for a in expanded_argv)
                if unexpanded:
                    # 명령(역할)은 글자 그대로 읽되 인자의 값은 모른다 — `%h/…` 를 상대 경로로
                    # 읽어 「상대 --config」라고 말하지 않는다
                    unfollowed.append(
                        f"line {number} ({key}=): rcm does not expand this systemd specifier "
                        "in ExecStart"
                    )
                else:
                    argv = [a for a in expanded_argv if a is not None]
                program = argv[0].lstrip("@-:+!") if argv else None
                launch = rcm_launch(program, argv)
                command = None if launch is None else launch["command"]
            elif key == "WorkingDirectory":
                # `-` 앞머리는 「없어도 된다」, `~` 는 사용자의 홈이다
                # (systemd.exec 「WorkingDirectory=」)
                wd = value.strip().removeprefix("-")
                if wd == "~":
                    wd = home or ""
                resolved = expand_specifiers(wd, home) if wd else None
                workdir = resolved
                workdir_unknown = resolved is None or not os.path.isabs(resolved)
            elif key == "User":
                user = value.strip() or None
            elif key == "LimitNOFILE":
                first = value.split(":", 1)[0].strip()
                nofile_unit = 1 << 20 if first == "infinity" else int(first)
        except ValueError:
            unreadable.append(f"line {number} ({key}=)")
    return ServiceFile(
        p,
        dc.SYSTEMD,
        command,
        env,
        nofile_unit,
        program,
        unreadable,
        unfollowed,
        path_unknown,
        launch,
        launch_unexpanded=unexpanded,
        label=p.stem,
        argv=argv,
        workdir=None if workdir_unknown else workdir,
        workdir_unknown=workdir_unknown,
        user_unit=home is not None,
        system=system,
        run_user=user,
    )


def system_place(p: Path) -> bool:
    """시스템 자리의 파일인가 — 관리자가 root 로 읽고 `User=` 로 띄운다."""
    return p.absolute().parent in (SYSTEM_UNIT_DIR, LAUNCH_DAEMONS_DIR)


def _matching(
    paths: list[Path],
    role: str,
    unreadable: list[ServiceFileUnreadable],
    user_home: str | None = None,
) -> list[ServiceFile]:
    """그 역할의 `rcm` 하위 명령을 띄우는 파일만. 못 읽은 파일은 버리지 않고 `unreadable` 에
    모은다 — 그 파일이 이 역할의 것이었는지 모르기 때문이다. rcm 의 서비스가 아님이 확실한
    파일(`looks_like_rcm`)은 후보도 못 읽은 파일도 아니다."""
    want = ROLE_COMMANDS.get(role)
    out: list[ServiceFile] = []
    for p in paths:
        try:
            sf = read_service_file(p, user_home=user_home, system=system_place(p))
        except ServiceFileUnreadable as e:
            unreadable.append(e)
            continue
        if sf.command is None and any("ExecStart" in u for u in sf.unreadable_lines):
            # 띄우는 명령 줄을 못 읽었다 — 이 역할의 파일인지 모른다
            unreadable.append(ServiceFileUnreadable(p, f"cannot read {sf.unreadable_lines[0]}"))
            continue
        if sf.launch is None and not looks_like_rcm(sf):
            # rcm 의 서비스가 아님이 확실하다(rcm-ci-connect 의 ci-watch plist). 이 파일을 「못
            # 읽음」으로 적으면 모든 입력을 읽은 실행이 종료 3 이 된다(검증 사이클 2 의 1라운드
            # HIGH 1)
            continue
        if sf.launch is None:
            # rcm 의 표준 자리에 있는데 띄우는 명령을 rcm 의 하위 명령으로 풀지 못했다(`env rcm …` ·
            # `sh -c` · rcm 이 받지 않을 인자) — 조용히 빼면 이 역할의 서비스를 놓친다
            # (P3 4라운드 LOW)
            unreadable.append(
                ServiceFileUnreadable(
                    p,
                    "cannot tell which rcm command it starts — start the rcm executable itself "
                    "with arguments rcm accepts",
                )
            )
            continue
        if want is None or sf.command == want:
            out.append(sf)
    return out


def pick_service_file(
    explicit: str | None,
    role: str,
    *,
    home: Path,
    config_dir: Path,
    unreadable: list[ServiceFileUnreadable] | None = None,
) -> ServiceFile | None:
    """찾는 순서: `--service-file`, 표준 라벨의 설치 자리(사용자 자리와 시스템 자리
    `/etc/systemd/system` · `/Library/LaunchDaemons`), setup 의 자리 `<config dir>/service/*`.

    설치 자리를 setup 자리보다 먼저 본다 — 서비스 관리자가 실제로 읽는 파일이다(6-B-V2). 후보는
    그 역할의 `rcm` 하위 명령을 띄우는 파일로 거른다. 같은 자리에 둘 이상이면 사람이 고르고,
    비TTY 면 사용법 오류다. 없으면 None(「서비스 파일 없음」).

    읽지 못한 파일은 `unreadable` 에 모은다. 자동으로 찾은 후보면 빼고 같은 자리의 나머지로
    찾지만, 그 자리에서 고른 파일이 없으면 다음 자리로 넘어가지 않고 None 이다 — 설치 자리의 못
    읽은 파일이 서비스 관리자가 읽는 파일일 수 있어서, setup 자리의 사본으로 재면 짐작이다(P3
    3라운드 HIGH). `--service-file` 로 준 파일이면 `ServiceFileUnreadable` 를 그대로 던진다.
    """
    skipped: list[ServiceFileUnreadable] = [] if unreadable is None else unreadable
    user_dir = home / ".config" / "systemd" / "user"
    staged_dir = config_dir / "service"
    # 사용자 유닛인 것을 아는 자리 — 그 유닛의 `%h` 는 이 홈이다. setup 의 자리도 사용자 유닛을 쓴다
    # (`rcm init worker --service-file` 의 안내: `~/.config/systemd/user/` 에 넣는다)
    user_places = (user_dir, staged_dir)
    user_home = str(home) if str(home) not in ("", ".") else None
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise DoctorUsage(f"service file not found: {p.name}")
        known_user = p.absolute().parent in user_places
        return read_service_file(
            p, user_home=user_home if known_user else None, system=system_place(p)
        )
    launch_agents = sorted((home / "Library" / "LaunchAgents").glob(LAUNCH_AGENTS_GLOB))
    user_units = sorted(user_dir.glob(SYSTEMD_GLOB))
    # 시스템 자리도 설치 자리다 — 문서의 Linux 서버 설치가 `/etc/systemd/system/rcm-server.service`
    # 다(P3 사이클 2 r2 검증자 1 #1)
    system_files = sorted(LAUNCH_DAEMONS_DIR.glob(LAUNCH_AGENTS_GLOB)) + sorted(
        SYSTEM_UNIT_DIR.glob(SYSTEMD_GLOB)
    )
    staged = sorted(
        p for p in staged_dir.glob("*") if p.suffix in (".plist", ".service") and p.is_file()
    )
    for stage in (launch_agents + user_units + system_files, staged):
        before = len(skipped)
        found = _matching(stage, role, skipped, user_home)
        if len(skipped) > before:
            # 못 읽은 파일이 이 역할의 서비스였는지 모른다 — 같은 자리의 읽히는 파일로도 재지 않고
            # 다음 자리로도 넘어가지 않는다(P3 3라운드 HIGH · 4라운드 LOW)
            return None
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = ", ".join(dc.mask_home(str(sf.path), str(home)) for sf in found)
            if is_tty():
                picked = choose([sf.path for sf in found if sf.path], str(home))
                if picked is not None:
                    return next(sf for sf in found if sf.path == picked)
            raise DoctorUsage(f"several service files match: {names} — pass --service-file PATH")
    return None


# ── 역할 읽기 ────────────────────────────────────────────────────────────────


def infer_roles(environ: Mapping[str, str]) -> list[str]:
    """있는 설정에서 역할을 읽어 낸다(결정 239). 환경변수만 쓰는 워커 · 클라이언트도
    「설정 있음」이다(TD49)."""
    from remote_ci_monitor.config import (
        ConfigError,
        _read_toml,
        find_client_config,
        find_server_config,
        user_config_dir,
    )

    roles: list[str] = []
    try:
        server = find_server_config(None)
        # `rcm serve` 와 같은 읽기 — UTF-8 이 아니거나 TOML 이 아니면 사용법 오류다
        raw = _read_toml(server) if server is not None else {}
    except ConfigError as e:
        raise DoctorUsage(str(e)) from e
    if server is not None:
        section = raw.get("server", {})
        if not isinstance(section, dict):
            raise DoctorUsage(f"{server}: [server] must be a table")
        lanes = section.get("lanes", 1)
        coordinator_only = isinstance(lanes, int) and lanes == 0
        roles.append(dc.ROLE_COORDINATOR if coordinator_only else dc.ROLE_COORDINATOR_WORKER)
    if (user_config_dir() / "worker.toml").is_file() or environ.get("RCM_WORKER_TOKEN"):
        roles.append(dc.ROLE_WORKER)
    try:
        client = find_client_config(None)
    except ConfigError:
        client = None
    if client is not None or environ.get("RCM_SERVER") or environ.get("RCM_TOKEN"):
        roles.append(dc.ROLE_CLIENT)
    return roles


def git_ref_presets(
    presets: tuple[SpecPreset, ...], rows: list[dc.PresetRow]
) -> tuple[dict[str, str], list[str]]:
    """이 머신에 오는 git_ref 프리셋 → 저장소 이름, 그리고 모르는 것의 줄. 오는 프리셋의 `checks`
    에 이름이 오른 점검 프리셋도 든다 — 점검은 그 머신을 지정한 git_ref 잡이다(결정 94).

    서버가 `source_modes` 를 말하지 않은 프리셋, git_ref 인데 `repo` 를 말하지 않은 프리셋은
    「git_ref 아님」 · 「저장소 없음」으로 읽지 않고 모름으로 둔다(P3 3라운드 (g))."""
    by_name = {dc.clean_text(p.name, 64): p for p in presets}
    coming = {r.name for r in rows if r.counts}
    names = set(coming)
    for r in rows:
        if r.name in coming:
            names.update(c for c in r.checks if c in by_name)
    known: dict[str, str] = {}
    unknown: list[str] = []
    for name in sorted(names):
        p = by_name.get(name)
        if p is None:
            continue
        if p.source_modes is None:
            unknown.append(
                f"{name}: the server does not say whether it takes git_ref jobs (older rcm) — "
                "cannot check git and [[repos]] for it"
            )
        elif "git_ref" in p.source_modes:
            if p.repo is None:
                unknown.append(
                    f"{name}: the server does not say which repository its git_ref jobs use — "
                    "cannot check [[repos]] for it"
                )
            else:
                known[name] = dc.clean_text(p.repo, 64)
    return known, unknown


# ── 서버 답의 모양과 파싱 ────────────────────────────────────────────────────
#
# 서버 답이 사실이 되는 자리는 여기 한 곳이다(P3 3라운드 근본 원인). 답은 먼저 모양(`Shape`)을
# 보고 — 필수 칸이 없거나 null 이면 rcm 서버의 답이 아니다 — 그다음 타입이 정해진 사실(`Health` ·
# `Spec` · `Machines`)로 바꾼다. 뒤에서 판정하는 자리는 이 사실만 받는다. 사실 칸의 `None` 은
# 「서버가 말하지 않았다 = 모름」이고, 확정값으로 바꾸지 않는다 — 기본값을 넣는 것은 그 칸이
# 판정에 들지 않을 때뿐이고 그 자리에 이유를 적는다.


@dataclass(frozen=True)
class Shape:
    """서버 답에서 doctor 가 쓰는 칸의 모양. `required` 칸이 없거나 null 이면 그 답 전체가 rcm
    서버의 답이 아니다(남의 서비스의 `{"status":"ok"}`, `{}`). 필수가 아닌 칸이 없거나 null 이면
    모름이다 — 파싱이 `None` 으로 옮기고, 판정이 그 `None` 을 모름으로 다룬다. 있는데 타입이
    틀리면 답 전체를 알아볼 수 없는 답으로 둔다 — 쓰다가 터지거나, 문자열을 목록처럼 한 글자씩
    돌거나, `"yes"` 를 참으로 읽지 않게(P3 2라운드 HIGH)."""

    types: tuple[type, ...]
    keys: Mapping[str, Shape] = field(default_factory=dict)
    items: Shape | None = None  # 배열의 항목
    values: Shape | None = None  # 객체의 값(이름이 서버가 정한 것일 때)
    required: bool = False
    #: 이름 · 버전 · 종류처럼 비면 없는 것과 같은 문자열 — rcm 서버는 빈 문자열을 보내지 않는다
    #: (P3 4라운드 LOW)
    nonempty: bool = False


def _req(shape: Shape) -> Shape:
    return replace(shape, required=True)


_STR = Shape((str,))
_NAME = Shape((str,), nonempty=True)
_NUMBER = Shape((int, float))
_BOOL = Shape((bool,))
_STRS = Shape((list,), items=_STR)
_NAMES = Shape((list,), items=_NAME)
_GENERATION = Shape((str, int))
_PRESET = Shape(
    (dict,),
    {
        "name": _req(_NAME),
        "requires": _req(_NAMES),
        "env_passthrough": _req(_NAMES),
        "env": _req(Shape((dict,), values=_STR)),  # 환경 변수 값은 비어도 된다
        "pool": _NAME,
        "pools": _NAMES,
        "placement": _NAME,
        "checks": _NAMES,
        "source_modes": _NAMES,
        "repo": _NAME,
        "unreachable_checks": Shape(
            (list,), items=Shape((dict,), {"name": _req(_NAME), "code": _req(_NAME)})
        ),
    },
)
# argv 의 뒤 인자는 빈 문자열일 수 있다 — 실행 파일(첫 항목)만 `parse_spec` 이 본다
_PROBE = Shape((dict,), {"name": _req(_NAME), "argv": _req(_STRS), "timeout_seconds": _NUMBER})
_TOOL = Shape((dict,), {"name": _req(_NAME), "docs": _STR, "install": Shape((dict,), values=_STR)})
_MACHINE = Shape(
    (dict,),
    {
        "id": _req(_NAME),
        "display_name": _STR,
        "kind": _req(_NAME),
        "pool": _NAME,
        "alive": _BOOL,
        "presets": Shape((dict,), values=Shape((dict,), {"state": _STR, "code": _STR})),
    },
)
#: 라우트 → 그 답의 모양. `reach` 가 답을 넘기기 전에 한 번 본다
ANSWER_SHAPES: dict[str, Shape] = {
    "/api/health": Shape(
        (dict,),
        {
            "ok": _req(_BOOL),
            "version": _req(_NAME),
            "error": _STR,
            "preset_env": Shape(
                (dict,),
                values=Shape(
                    (dict,),
                    {
                        "missing_tools": _req(_STRS),
                        "missing_passthrough": _req(_STRS),
                        # 어느 정의를 쟀는지 — 없으면(이 키를 모르는 서버) 그 프리셋은 모름이다
                        "digest": _STR,
                    },
                ),
            ),
        },
    ),
    "/api/whoami": Shape((dict,), {"name": _STR, "kind": _req(_NAME), "admin": _BOOL}),
    "/worker/spec": Shape(
        (dict,),
        {
            "generation": _GENERATION,
            "probes": Shape((list,), items=_PROBE),
            "presets": _req(Shape((list,), items=_PRESET)),
            "tools": Shape((list,), items=_TOOL),
        },
    ),
    "/api/machines": Shape(
        (dict,),
        {"generation": _GENERATION, "machines": _req(Shape((list,), items=_MACHINE))},
    ),
}
_JSON_KIND = {dict: "an object", list: "an array", str: "a string", bool: "a boolean"}


def _json_kind(t: type) -> str:
    return _JSON_KIND.get(t, "a number")


def _fits(value: Any, types: tuple[type, ...]) -> bool:
    if isinstance(value, bool):  # bool 은 int 의 하위 형이다 — 숫자 칸에 참거짓을 받지 않는다
        return bool in types
    if isinstance(value, float) and not math.isfinite(value):  # JSON 의 NaN · Infinity
        return False
    return isinstance(value, types)


def shape_problem(value: Any, shape: Shape, where: str = "") -> str | None:
    """`value` 가 `shape` 에 맞지 않으면 어디가 어떻게 틀렸는지 한 줄(`presets[0].env is an
    array, not an object` · `version is missing`). 값은 넣지 않는다."""
    if not _fits(value, shape.types):
        got = "null or empty" if value is None else _json_kind(type(value))
        want = " or ".join(dict.fromkeys(_json_kind(t) for t in shape.types))
        return f"{where or 'the answer'} is {got}, not {want}"
    if shape.nonempty and isinstance(value, str) and not value.strip():
        return f"{where or 'the answer'} is empty"
    dot = f"{where}." if where else ""
    if isinstance(value, dict):
        for key, sub in shape.keys.items():
            if value.get(key) is None:
                if sub.required:
                    return f"{dot}{key} is missing"
                continue
            problem = shape_problem(value[key], sub, f"{dot}{key}")
            if problem:
                return problem
        if shape.values is not None:
            for key, item in value.items():
                problem = shape_problem(item, shape.values, f"{dot}{dc.clean_text(key, 64)}")
                if problem:
                    return problem
    if isinstance(value, list) and shape.items is not None:
        for i, item in enumerate(value):
            problem = shape_problem(item, shape.items, f"{where or 'the answer'}[{i}]")
            if problem:
                return problem
    return None


class AnswerUnreadable(Exception):
    """모양은 맞는데 값이 rcm 서버가 보낼 수 없는 것이다(빈 argv · 범위 밖 시한) — 답 전체를
    알아볼 수 없는 답으로 둔다. `ValueError` 가 아니다: `reach` 는 그것을 「JSON 아님」으로
    읽는다."""


@dataclass(frozen=True)
class Health:
    """`/api/health` 의 사실. `preset_env` 가 None 이면 서버가 자기 측정을 싣지 않았다(옛 서버)."""

    ok: bool
    version: str
    error: str | None
    preset_env: dict[str, PresetEnv] | None


@dataclass(frozen=True)
class PresetEnv:
    missing_tools: tuple[str, ...]
    missing_passthrough: tuple[str, ...]
    #: 서버가 잰 프리셋 정의의 지문(`dc.preset_env_digest`). None 이면 서버가 말하지 않았다
    digest: str | None = None


@dataclass(frozen=True)
class Whoami:
    kind: str


@dataclass(frozen=True)
class SpecPreset:
    """`/worker/spec` 프리셋 하나. 판정에 쓰는 칸의 `None` 은 「서버가 말하지 않았다」이다 —
    `placement` 가 None 이면 옛 서버(TD5)라 `pool` 로 짐작하지 않는다."""

    name: str
    requires: tuple[str, ...]
    env_passthrough: tuple[str, ...]
    env: dict[str, str]
    placement: str | None
    pool: str | None
    pools: tuple[str, ...] | None
    checks: tuple[str, ...] | None
    unreachable: dict[str, str] | None
    source_modes: tuple[str, ...] | None
    repo: str | None

    def entry(self) -> dict[str, Any]:
        """`dc.classify_preset` 가 읽는 칸 — 모르는 칸은 null 그대로다."""
        return {
            "name": self.name,
            "requires": list(self.requires),
            "placement": self.placement,
            "pool": self.pool,
            "pools": None if self.pools is None else list(self.pools),
            "checks": None if self.checks is None else list(self.checks),
        }


@dataclass(frozen=True)
class Spec:
    generation: str | None
    probes: tuple[Any, ...]  # config.Probe
    presets: tuple[SpecPreset, ...]
    hints: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Machine:
    """`/api/machines` 의 머신 하나. `pool` · `alive` · `presets` 의 None 은 모름(표시만 한다)."""

    id: str
    name: str
    kind: str
    pool: str | None
    alive: bool | None
    presets: dict[str, dict[str, str | None]] | None


@dataclass(frozen=True)
class Machines:
    generation: str | None
    machines: tuple[Machine, ...]


def _strs(value: list[Any] | None) -> tuple[str, ...] | None:
    return None if value is None else tuple(value)


def _generation(value: str | int | None) -> str | None:
    return None if value is None else dc.clean_text(value, 64) or None


def parse_health(answer: dict[str, Any]) -> Health:
    raw = answer.get("preset_env")
    preset_env = (
        None
        if raw is None
        else {
            name: PresetEnv(
                tuple(v["missing_tools"]), tuple(v["missing_passthrough"]), v.get("digest")
            )
            for name, v in raw.items()
        }
    )
    return Health(answer["ok"], answer["version"], answer.get("error"), preset_env)


def parse_whoami(answer: dict[str, Any]) -> Whoami:
    return Whoami(answer["kind"])


def parse_spec(answer: dict[str, Any]) -> Spec:
    from remote_ci_monitor.config import PROBE_TIMEOUT_MAX, Probe

    probes = []
    # 프로브는 판정에 들지 않는다(시한 초과만 모름이다) — 칸이 없으면 잴 것이 없다
    raw_probes = answer.get("probes")
    for i, p in enumerate([] if raw_probes is None else raw_probes):
        timeout = p.get("timeout_seconds")
        timeout = 10 if timeout is None else timeout  # config.Probe 의 기본값
        if isinstance(timeout, float) or not 1 <= timeout <= PROBE_TIMEOUT_MAX:
            raise AnswerUnreadable(
                f"probes[{i}].timeout_seconds is not an integer from 1 to {PROBE_TIMEOUT_MAX}"
            )
        if not p["argv"] or not p["argv"][0].strip():
            raise AnswerUnreadable(f"probes[{i}].argv is empty")
        probes.append(Probe(name=p["name"], argv=tuple(p["argv"]), timeout_seconds=timeout))
    presets = []
    for p in answer["presets"]:
        far = p.get("unreachable_checks")
        presets.append(
            SpecPreset(
                name=p["name"],
                requires=tuple(p["requires"]),
                env_passthrough=tuple(p["env_passthrough"]),
                env=dict(p["env"]),
                placement=p.get("placement"),
                pool=p.get("pool"),
                pools=_strs(p.get("pools")),
                checks=_strs(p.get("checks")),
                unreachable=None
                if far is None
                else {dc.clean_text(c["name"], 64): dc.clean_text(c["code"], 64) for c in far},
                source_modes=_strs(p.get("source_modes")),
                repo=p.get("repo"),
            )
        )
    tools = answer.get("tools")
    return Spec(
        generation=_generation(answer.get("generation")),
        probes=tuple(probes),
        presets=tuple(presets),
        # 설치 안내는 표시만 한다 — 칸이 없으면 안내가 없을 뿐이다
        hints=dc.tool_hints([] if tools is None else tools),
    )


def parse_machines(answer: dict[str, Any]) -> Machines:
    out = []
    for m in answer["machines"]:
        raw = m.get("presets")
        verdicts = (
            None
            if raw is None
            else {name: {"state": v.get("state"), "code": v.get("code")} for name, v in raw.items()}
        )
        shown = m.get("display_name")
        out.append(
            Machine(
                id=m["id"],
                name=shown if shown is not None else m["id"],
                kind=m["kind"],
                pool=m.get("pool"),
                alive=m.get("alive"),
                presets=verdicts,
            )
        )
    return Machines(_generation(answer.get("generation")), tuple(out))


#: 라우트 → 모양이 맞은 답을 사실로 바꾸는 함수
ANSWER_PARSERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "/api/health": parse_health,
    "/api/whoami": parse_whoami,
    "/worker/spec": parse_spec,
    "/api/machines": parse_machines,
}


# ── 실행 ─────────────────────────────────────────────────────────────────────


@dataclass
class Options:
    role: str | None = None
    server: str | None = None
    config: str | None = None
    json: bool = False
    offline: bool = False
    shell_path: bool = False
    service_file: str | None = None


@dataclass
class Context:
    """바깥 세계 — CLI 가 채운다. 시험은 `cli.discover` 와 `cli._ls_remote_problem` 을 바꾼다."""

    environ: Mapping[str, str]
    discover: Callable[[float], list[Any]]
    #: (저장소 URL, git 실행 파일, 환경) → 문제 사유 또는 None — 서비스 PATH 의 git, 서비스 환경
    ls_remote: Callable[[str, str, Mapping[str, str]], str | None]
    worker_service_path: str
    out: Callable[[str], None] = print
    err: Callable[[str], None] = lambda m: print(m, file=sys.stderr)


class _Run:
    """한 번의 진단 — 줄을 모으고 마지막에 문서 하나로 낸다."""

    def __init__(self, opts: Options, ctx: Context, role: str, home: str):
        self.opts, self.ctx, self.role = opts, ctx, role
        self.home = home  # 모르면 "" — 가리지 못할 뿐이다(`run` 이 그때는 재지 않는다)
        self.problems: list[str] = []
        self.undecided: list[str] = []
        self.warnings: list[str] = []
        self.requirements: list[dict[str, Any]] = []
        self.rows: list[dc.PresetRow] = []
        self.probes: dict[str, dict[str, Any]] = {}
        self.server: dict[str, Any] | None = None
        self.service: dict[str, Any] | None = None
        self.shell: dict[str, Any] | None = None
        self.remote: list[dict[str, Any]] | None = None
        self.measured_in = "none"
        self.host: dict[str, Any] = {}
        self.judged = False  # 프리셋 판정까지 갔나 — 못 갔으면 「올 프리셋 0개」 규칙을 걸지 않는다
        self.pool = dc.DEFAULT_POOL
        self._service_file: ServiceFile | None = None
        #: 서비스 파일 후보나 사람이 준 서비스 파일을 못 읽었다 — 서비스가 어느 설정으로 도는지
        #: 모른다
        self.service_unread = False
        self.path_known = True  # 서비스 PATH 를 아나 — 모르면 PATH 로 찾는 것은 재지 않는다
        #: 이 머신에 오는 git_ref 프리셋 → 저장소 이름(명세를 받았을 때만 안다)
        self.git_presets: dict[str, str] | None = None
        #: 판정에 쓰는 사실 가운데 서버가 말하지 않은 것 — `document` 가 「판정 못 함」 줄로 옮긴다.
        #: 행 · 프로브 · rcm 자신의 요구의 모름은 각자의 상태에서 따로 끌어낸다
        self.unknown: list[str] = []
        #: 모름(unknown)인 프로브 → 그 이유
        self.probe_why: dict[str, str] = {}
        #: 어느 프리셋이 git_ref 인지 서버가 다 말하지 않았다(옛 서버 · 모르는 배치)
        self.unsure_git = False
        #: 시스템 서비스의 `User=` 를 이 머신이 모른다 — 그 이유 줄(모르면 None)
        self.service_user_unknown: str | None = None

    # ── 공통 ──

    def mask(self, text: str) -> str:
        return dc.mask_home(text, self.home)

    def host_facts(self, path: str | None) -> None:
        self.host = {**hostsample.host_facts(path=path), "rcm": __version__}

    def base_requirements(self) -> None:
        self.requirements.append(
            python_requirement(
                python_version(),
                hasattr(tarfile, "data_filter"),
                platform=PLATFORM,
                package_manager=self.host.get("package_manager"),
                root=bool(self.host.get("root")),
            )
        )

    @staticmethod
    def found_where(found: Any) -> str:
        """mDNS 로 찾은 서버의 이름과 주소 — 한 줄로 정리한다. 이름은 LAN 의 TXT 레코드에서 온다:
        ESC · 줄바꿈 · bidi 가 남으면 R1 을 지키는 질문 위에서 줄을 지우거나 가짜 질문 줄을 만든다
        (P6 GAP X-mdns)."""
        return f"{dc.clean_text(found.name, 64)} ({dc.clean_text(found.address, 200)})"

    def confirm(self, found: Any) -> bool:
        """mDNS 로 찾은 서버 — 사람이 확인하기 전에는 어떤 요청도 보내지 않는다(TD10 · TD28)."""
        where = self.found_where(found)
        if not is_tty():
            self.undecided.append(
                f"server {where} was found on this network and nobody confirmed it — "
                "pass --server URL or run rcm doctor in a terminal"
            )
            return False
        if not ask(f"Use the rcm server {where} found on this network?"):
            self.undecided.append(f"server {where} was not confirmed")
            return False
        return True

    def discover(self, server_name: str = "") -> Any | None:
        found = list(self.ctx.discover(DISCOVER_SECONDS))
        want = server_name.strip()
        picks = [f for f in found if f.name == want] if want else found
        if not picks:
            self.undecided.append("no rcm server found on this network — pass --server URL")
            return None
        if len(picks) > 1:
            names = ", ".join(self.found_where(f) for f in picks)
            raise DoctorUsage(f"several rcm servers on this network: {names} — pass --server URL")
        return picks[0] if self.confirm(picks[0]) else None

    def check_url(self, url: str, origin: Path | None) -> None:
        """설정이나 플래그로 받은 서버 주소는 http(s) 여야 한다 — 아니면 urllib 이 요청을 만들다
        터진다. 어디서 온 값인지(파일 이름 · 플래그)를 보이는 사용법 오류다."""
        if not url or url.startswith(("http://", "https://")):
            return
        where = "--server" if self.opts.server else (self.mask(str(origin)) if origin else "")
        where = where or "the environment"
        raise DoctorUsage(f"{where}: server must start with http:// or https://")

    def server_doc(self, url: str, found_by: str, state: str) -> dict[str, Any]:
        self.server = {
            "url": url,
            "found_by": found_by,
            "state": state,
            "generation": None,
            "version": None,
        }
        return self.server

    def answered(self, health: Health) -> None:
        """`/api/health` 가 왔다. 503 `ok: false`(janitor 멈춤 · 워커 down)에도 본문은 오지만
        서버가 성한 것은 아니다 — `state` 를 ok 로 적지 않고 이유를 경고로 보인다. 이 머신의
        판정은 그대로다."""
        assert self.server is not None
        version = dc.clean_text(health.version, 64) or None
        if not health.ok:
            self.server.update(state="not ok", version=version)
            why = "no reason given" if health.error is None else dc.clean_text(health.error, 120)
            self.warnings.append(f"the server answers but reports a problem: {why}")
        else:
            self.server.update(state="ok", version=version)

    def reach(self, call: Callable[[], Any], route: str, token_hint: str) -> tuple[bool, Any]:
        """GET 하나. 닿지 않거나 401 · 옛 서버면 판정 못 함(종료 3)으로 적고 (False, None).

        답이 JSON 이 아니거나(틀린 포트의 HTML · 빈 본문) 그 라우트의 모양(`ANSWER_SHAPES`)에
        맞지 않으면 — 필수 칸이 없거나 null 이어도 — 판정 못 함이다. 맞으면 그 라우트의 파서
        (`ANSWER_PARSERS`)가 타입이 정해진 사실로 바꿔 돌려준다. 서버 답이 사실이 되는 자리는
        여기 한 곳이다 — 뒤에서 판정하는 자리는 날 dict 를 읽지 않는다."""
        from remote_ci_monitor.client import ClientError

        assert self.server is not None
        try:
            answer = call()
        except ValueError:  # JSON 이 아니다(UnicodeDecodeError 도 ValueError 다)
            why: str | None = "not JSON"
        except ClientError as e:
            if e.status == 0:
                self.undecided.append(f"cannot reach {self.server['url']}")
                self.server["state"] = "unreachable"
            elif e.status in (401, 403):
                self.undecided.append(f"the server refused the token ({e.status}) — {token_hint}")
                self.server["state"] = "token refused"
            elif e.status == 404:
                self.undecided.append(f"the server has no {route} (older rcm)")
            else:
                self.undecided.append(f"the server answered {e.status}: {dc.clean_text(e.message)}")
            return False, None
        else:
            why = shape_problem(answer, ANSWER_SHAPES[route])
            if why is None:
                try:
                    return True, ANSWER_PARSERS[route](answer)
                except AnswerUnreadable as e:
                    why = str(e)
        self.undecided.append(
            f"{self.server['url']} gave {route} an answer rcm cannot read ({why}) — check that "
            "the URL and port are the rcm server's"
        )
        self.server["state"] = "unreadable answer"
        return False, None

    def document(self) -> dict[str, Any]:
        """종료 코드는 사실에서 끌어낸다. 「판정 못 함」은 손으로 적은 줄(닿지 않음 · 토큰 ·
        서비스 파일)에 더해, 모름인 행 · 프로브 · rcm 자신의 요구와 서버가 말하지 않은 사실에서
        나온다 — 읽는 자리마다 종료 3 을 기억해 넣지 않아도 된다. 「문제」는 확정된 사실(빠진 도구 ·
        FAIL 요구 · 아무 프리셋도 오지 않음)에서만 나온다."""
        rows = self.rows
        if self.judged and self.role in (dc.ROLE_WORKER, dc.ROLE_COORDINATOR_WORKER):
            reason = dc.no_jobs_reason(rows, self.pool)
            if reason:
                self.problems.append(reason)
        undecided = list(self.undecided)
        undecided += dc.unknown_rows(rows)
        undecided += [f"probe {name}: {why}" for name, why in self.probe_why.items()]
        undecided += [
            f"{r['name']}: {r['detail']}" for r in self.requirements if r["state"] == "unknown"
        ]
        undecided += self.unknown
        undecided = list(dict.fromkeys(undecided))
        problems = dc.preset_problems(rows) + self.problems
        problems += [
            f"{r['name']}: {r['detail']}" for r in self.requirements if r["state"] == "fail"
        ]
        warnings = list(self.warnings)
        warnings += [f"{r.name}: {r.warn}" for r in rows if r.warn]
        warnings += [
            f"{r['name']}: {r['detail']}" for r in self.requirements if r["state"] == "warn"
        ]
        code = dc.decide_exit(problems, undecided)
        # 키는 늘 있다 — 모르는 값은 키를 빼지 않고 null 이다(위임 결정 J). 읽는 쪽은 모르는 키를
        # 무시한다(docs/operating.md)
        doc: dict[str, Any] = {
            "doctor_version": dc.DOCTOR_VERSION,
            "role": self.role,
            "measured_in": self.measured_in,
            "host": self.host,
            "server": self.server,
            "service": self.service,
            "shell": self.shell,
            "rcm_requirements": self.requirements,
            "presets": [r.to_json() for r in rows],
            "probes": self.probes,
            "remote_machines": self.remote,
            "summary": dc.summary(rows),
            "problems": problems,
            "undecided": undecided,
            "warnings": warnings,
            "exit": code,
        }
        return doc

    # ── 서비스 환경 ──

    def service_env(self) -> dict[str, str]:
        from remote_ci_monitor.config import user_config_dir

        skipped: list[ServiceFileUnreadable] = []
        broken: ServiceFileUnreadable | None = None
        try:
            sf = pick_service_file(
                self.opts.service_file,
                self.role,
                home=Path(self.home),
                config_dir=user_config_dir(),
                unreadable=skipped,
            )
        except ServiceFileUnreadable as e:
            sf, broken = None, e
        for e in [*skipped, *([broken] if broken else [])]:
            # 못 읽은 파일은 「판정 못 함」이다 — traceback 도 조용한 무시도 아니다(P3 HIGH)
            self.undecided.append(
                f"cannot read the service file {self.mask(str(e.path))}: {e.reason} — fix it "
                "or pass --service-file PATH"
            )
        #: 사람이 준 파일인데 띄우는 명령을 rcm 의 하위 명령으로 풀지 못했다(`env PATH=… rcm` ·
        #: `sh -c`) — 그 실행이 받는 PATH 와 읽는 설정을 모른다(검증 사이클 2 의 1라운드 MED 3)
        unresolved = bool(self.opts.service_file) and sf is not None and sf.launch is None
        if sf is not None:
            for where in sf.unreadable_lines:
                self.undecided.append(
                    f"cannot read {where} of the service file "
                    f"{self.mask(str(sf.path))} — fix that line"
                )
            for where in sf.unfollowed:
                # 그 줄이 바꾸는 값을 모른다 — 판정 못 함이다(P3 2라운드 MED)
                advice = (
                    "write the paths out in full"
                    if "(ExecStart=)" in where
                    else "write the values out in Environment= lines"
                )
                self.undecided.append(
                    f"cannot read {where} (service file {self.mask(str(sf.path))}) — {advice}"
                )
            if unresolved:
                self.undecided.append(
                    f"cannot tell which rcm command the service file {self.mask(str(sf.path))} "
                    "starts, so its PATH and config are unknown — start the rcm executable "
                    "itself with arguments rcm accepts"
                )
        manager = sf.manager if sf else (dc.LAUNCHD if PLATFORM == "darwin" else dc.SYSTEMD)
        #: 서버 역할인데 서비스 파일이 하나도 없다 — 서버가 어느 PATH 로 도는지 모른다
        no_server_file = (
            sf is None
            and broken is None
            and not skipped
            and self.role in (dc.ROLE_COORDINATOR, dc.ROLE_COORDINATOR_WORKER)
        )
        if broken is not None:
            # 사람이 준 파일을 못 읽었다 — 그 PATH 를 모르므로 기본 PATH 로 재지 않는다
            manager = dc.LAUNCHD if broken.path.suffix == ".plist" else dc.SYSTEMD
            env = dc.clean_env(manager, self.ctx.environ, {})
            env.pop("PATH", None)
        elif sf is not None:
            env = dc.clean_env(manager, self.run_user_env(sf), sf.env)
            if sf.path_unknown or unresolved:
                env.pop("PATH", None)
        elif skipped:
            # 후보를 못 읽어 고른 파일이 없다 — 그 파일의 PATH 를 모른다. 기본 PATH 를 짐작하는
            # 것은 후보가 하나도 없을 때뿐이다(P3 3라운드 HIGH)
            env = dc.clean_env(manager, self.ctx.environ, {})
            env.pop("PATH", None)
        elif no_server_file:
            # 워커의 서비스 PATH(`WORKER_SERVICE_PATH`)는 `rcm init worker` 가 쓰는 워커의 것이다 —
            # 서버의 PATH 로 짐작하지 않는다(P3 사이클 2 r2 검증자 1 #1)
            env = dc.clean_env(manager, self.ctx.environ, {})
            env.pop("PATH", None)
        else:
            env = dc.clean_env(
                manager, self.ctx.environ, {}, default_path=self.ctx.worker_service_path
            )
        self.path_known = "PATH" in env
        self.service_unread = bool(skipped) or broken is not None
        shown = sf.path if sf is not None else (broken.path if broken is not None else None)
        self.service = {
            "file": self.mask(str(shown)) if shown else None,
            "manager": manager,
            "path": self.mask(env["PATH"]) if self.path_known else None,
            "env_names": sf.env_names if sf else [],
        }
        self._service_file = sf
        if self.service_user_unknown is not None:
            self.undecided.append(self.service_user_unknown)
        if no_server_file:
            self.undecided.append(
                "no service file for the rcm server was found (the user and system places for "
                "launchd and systemd) — rcm cannot tell the PATH the server runs with; pass "
                "--service-file PATH"
            )
        elif (
            not self.path_known
            and broken is None
            and not skipped
            and not (sf and sf.path_unknown)
            and not unresolved
        ):
            # systemd 의 기본 PATH 는 배포판마다 다르다 — 추정해서 재면 없는 「빠짐」을 지어낸다
            self.undecided.append(
                "the service file sets no PATH and systemd's default differs between "
                "distributions — add Environment=PATH=… to the service file"
            )
        elif self.path_known and sf is not None and sf.program and os.path.isabs(sf.program):
            own_bin = os.path.dirname(sf.program)
            if own_bin in env["PATH"].split(":"):
                self.warnings.append(
                    f"{self.mask(own_bin)} (where the service's rcm lives) is on the service "
                    "PATH — a preset's python3 becomes that venv's; keep the absolute path to "
                    "rcm in the service file and drop it from PATH (docs/operating.md)"
                )
        want = ROLE_COMMANDS.get(self.role)
        if (
            self.opts.service_file
            and sf is not None
            and want
            and sf.command != want
            and not unresolved
        ):
            # 사람이 고른 파일이라 거르지 않는다 — 그 파일로 재되 다른 역할의 파일이라고 알린다(E1)
            self.warnings.append(
                f"{self.service['file']} starts rcm {sf.command or '(no command)'}, not rcm "
                f"{want} — measured with it as given"
            )
        if self.opts.shell_path:
            self.shell = {"path": self.mask(self.ctx.environ.get("PATH", ""))}
        return env

    def run_user_env(self, sf: ServiceFile) -> Mapping[str, str]:
        """서비스 관리자가 그 프로세스에 주는 사용자 변수(HOME · USER · LOGNAME · SHELL)의 출처.
        사용자 자리의 파일이면 이 사용자(doctor 의 환경)다. 시스템 자리의 파일이면 `User=` ·
        `UserName`(없으면 root)의 것이다 — 이 머신이 그 사용자를 모르면 그 변수는 없고 홈을
        모른다(P3 사이클 2 r2 검증자 1 #1)."""
        if not sf.system:
            return self.ctx.environ
        name = sf.run_user or "root"
        found = None if "%" in name else lookup_user(name)
        if found is None:
            self.service_user_unknown = (
                f"the service file {self.mask(str(sf.path))} runs rcm as user "
                f"{dc.clean_text(name, 64)}, whom this machine does not know — rcm cannot tell "
                "its home folder"
            )
            return {}
        user, home, shell = found
        return {"HOME": home, "USER": user, "LOGNAME": user, "SHELL": shell}

    # ── 설정: 서비스가 읽는 것 ──

    def service_launch(self) -> dict[str, Any] | None:
        """서비스가 rcm 을 띄우는 인자(`rcm_launch`). 서비스 파일이 하나도 없으면 None — 표준 자리의
        설정을 쓴다. 후보나 사람이 준 파일을 못 읽어 고른 파일이 없으면 그 서비스가 어느 설정으로
        도는지 모른다(`ConfigUnknown`). 다른 역할의 파일을 사람이 줬으면(E1) 그 인자는 쓰지 않는다.
        """
        sf = self._service_file
        if sf is None:
            if self.service_unread:
                raise ConfigUnknown(
                    "cannot tell which config the service starts with — its service file could "
                    "not be read (pass --config PATH)"
                )
            return None
        if sf.launch is None or sf.command != ROLE_COMMANDS.get(self.role):
            if self.opts.service_file and sf.launch is None:
                # 사람이 준 파일의 명령을 풀지 못했다 — 표준 자리의 설정으로 재지 않는다(MED 3)
                raise ConfigUnknown(
                    "cannot tell which config the service starts with — rcm cannot read its "
                    "command (pass --config PATH)"
                )
            return None
        if sf.launch_unexpanded:
            raise ConfigUnknown(
                "cannot tell which config the service starts with — its ExecStart= line has a "
                "systemd specifier rcm does not expand (see above)"
            )
        return sf.launch

    def service_config_path(self, raw: str) -> Path:
        """서비스 인자의 `--config` — rcm 은 `~` 를 그 서비스의 홈으로 푼다. 상대 경로는 서비스의
        작업 폴더에 달렸으므로 모른다."""
        p = Path(raw).expanduser()
        if not p.is_absolute():
            raise ConfigUnknown(
                f"the service starts rcm with a relative --config ({self.mask(raw)}) — rcm cannot "
                "tell its working directory (write the absolute path in the service file)"
            )
        return p

    def config_unknown(self, error: Exception) -> ConfigUnknown:
        return ConfigUnknown(
            f"cannot read the config the service starts with: {dc.hide_home(str(error), self.home)}"
            " — fix it or pass --config PATH"
        )

    def compare_config(self) -> None:
        """사람이 준 `--config` 로 잰다. 서비스가 다른 파일로 뜨면 알린다 — 그 서비스가 읽는 것은
        그 파일이다."""
        try:
            launch = self.service_launch()
        except ConfigUnknown:
            return
        theirs = None if launch is None else launch.get("config")
        if not theirs or self.opts.config is None:
            return
        mine = Path(self.opts.config).expanduser()
        if not Path(theirs).expanduser().is_absolute():
            # 서비스의 상대 경로는 서비스의 작업 폴더에 달렸다 — doctor 가 선 폴더로 풀어 「같은
            # 파일」이나 「다른 파일」이라고 말하지 않는다(검증 사이클 2 의 1라운드 LOW)
            self.warnings.append(
                f"the service starts rcm with a relative --config ({self.mask(theirs)}) — rcm "
                f"cannot tell whether that is {self.mask(str(mine))}; measured with the file given"
            )
            return
        if Path(theirs).expanduser().resolve() != mine.resolve():
            self.warnings.append(
                f"the service starts rcm with --config {self.mask(theirs)}, not "
                f"{self.mask(str(mine))} — measured with the file given"
            )

    def config_dirs(self, env: Mapping[str, str]) -> list[Path]:
        """서비스가 설정을 찾는 폴더(`rcm` 의 `user_config_dir` 와 같은 규칙). 서비스 파일이 있으면
        그 서비스 환경의 `XDG_CONFIG_HOME`, 없으면 서비스 홈의 `.config` 다 — doctor 셸의
        `XDG_CONFIG_HOME` 이 아니다(검증 사이클 2 의 1라운드 HIGH 2). 서비스 파일이 없으면 `rcm
        init` 이 쓰는 자리(`user_config_dir`)다. 둘째 항목은 서버의 옛 자리 `~/.config/rcm` 이다."""
        from remote_ci_monitor.config import user_config_dir

        if self._service_file is None:
            first = user_config_dir()
            legacy = Path(self.home) / ".config" / "rcm"
        else:
            home = env.get("HOME") or ""
            if not home:
                if self._service_file.system:
                    # 시스템 서비스가 도는 사용자의 홈을 모른다 — doctor 의 홈으로 찾지 않는다
                    raise ConfigUnknown(
                        "cannot tell where the service looks for its config — rcm does not know "
                        "the home folder of the user it runs as (pass --config PATH)"
                    )
                home = self.home
            xdg = env.get("XDG_CONFIG_HOME") or ""
            if xdg.startswith("~"):
                xdg = home + xdg[1:]
            legacy = Path(home) / ".config" / "rcm"
            first = Path(xdg) / "rcm" if xdg else legacy
            if not first.is_absolute():
                raise ConfigUnknown(
                    "the service's XDG_CONFIG_HOME is not an absolute path — rcm cannot tell "
                    "where it looks for its config (pass --config PATH)"
                )
        return [first] if first == legacy else [first, legacy]

    def service_workdirs(self) -> list[str]:
        """`rcm serve` 가 `./rcm.toml` 을 찾는 폴더 — 서비스의 작업 폴더. 적혀 있지 않으면 관리자의
        기본값이다: launchd 는 `/`, systemd 는 사용자 유닛이면 홈 · 시스템 유닛이면 `/` 라서 어느
        쪽인지 모르는 유닛은 둘 다다. 적혀 있는데 풀지 못하면 모른다(`ConfigUnknown`)."""
        sf = self._service_file
        assert sf is not None
        if sf.workdir_unknown:
            raise ConfigUnknown(
                "cannot tell the service's working directory, where rcm serve looks for "
                "rcm.toml — write an absolute WorkingDirectory or --config in the service file"
            )
        if sf.workdir:
            return [sf.workdir]
        if sf.manager == dc.LAUNCHD or sf.system:
            # launchd 와 시스템 유닛의 기본 작업 폴더는 `/` 다(systemd.exec 「WorkingDirectory=」)
            return [LAUNCHD_DEFAULT_WORKDIR]
        home = self.home
        return [home] if sf.user_unit else ["/", home]

    def service_server_config(
        self, launch: dict[str, Any], env: Mapping[str, str]
    ) -> tuple[Path | None, bool]:
        """서비스의 `rcm serve` 가 고르는 설정과, 그것을 서비스가 가리켰는지 — `--config` → 서비스
        환경의 `RCM_CONFIG` → 서비스 작업 폴더의 `rcm.toml` → 표준 자리(`find_server_config` 의
        순서). doctor 셸의 `RCM_CONFIG` 와 doctor 가 선 폴더는 보지 않는다(검증 사이클 2 의
        1라운드 HIGH 2)."""
        raw = launch.get("config") or env.get("RCM_CONFIG")
        if raw:
            return self.service_config_path(raw), True
        dirs = self.service_workdirs()
        found = [Path(d) / "rcm.toml" for d in dirs if (Path(d) / "rcm.toml").is_file()]
        if found and len(dirs) > 1:
            raise ConfigUnknown(
                f"{self.mask(str(found[0]))} may be the server's config — rcm cannot tell whether "
                "the unit runs from that folder (write WorkingDirectory= or --config in the "
                "service file)"
            )
        if found:
            return found[0], True
        return self.standard_server_config(env), False

    def standard_server_config(self, env: Mapping[str, str]) -> Path | None:
        """표준 자리의 `server.toml` — 서비스의 설정 폴더(`config_dirs`) 순서대로 처음 있는 것."""
        return next(
            (d / "server.toml" for d in self.config_dirs(env) if (d / "server.toml").is_file()),
            None,
        )

    def worker_config(self, env: Mapping[str, str]) -> Any:
        """워커의 설정 — `rcm worker` 가 고르는 것과 같은 것(`cli._worker_config`). `--config` 가
        먼저, 없으면 서비스 인자의 `--config` · `--server` · `--pool` · `--lanes`, 그것도 없으면
        서비스의 설정 폴더(`config_dirs`)의 `worker.toml`. 서비스가 가리키는 파일을 못 읽은 것은
        사용법 오류가 아니라 모름이다."""
        from remote_ci_monitor.config import ConfigError, load_worker_config

        path: Path | str | None = self.opts.config
        overrides: dict[str, Any] = {}
        from_service = False
        if path is None:
            launch = self.service_launch()
            default = self.config_dirs(env)[0] / "worker.toml"
            if launch is not None:
                overrides = {k: launch.get(k) for k in ("server", "pool", "lanes")}
                if launch.get("config"):
                    path, from_service = self.service_config_path(launch["config"]), True
                elif launch.get("server") is None and default.is_file():
                    path = default  # `rcm worker` 도 `--server` 없이 뜨면 이 파일을 읽는다
            elif default.is_file():
                path = default
        else:
            self.compare_config()
        if self.opts.server:
            overrides["server"] = self.opts.server
        try:
            return load_worker_config(path, overrides=overrides)
        except ConfigError as e:
            if from_service:
                raise self.config_unknown(e) from e
            raise DoctorUsage(str(e)) from e

    def server_config(self, env: Mapping[str, str]) -> Any:
        """서버의 설정 — `rcm serve` 가 읽는 것. `--config` 가 먼저, 없으면 서비스 인자의
        `--config` · `--bind` · `--port` 와 서비스 환경(`RCM_CONFIG` · `RCM_SERVER_*`) · 작업 폴더,
        그것도 없으면 표준 자리. 환경 덮어쓰기(`RCM_SERVER_*`)는 언제나 서비스 환경의 것이다 —
        doctor 셸의 것이 아니다. 어디에도 설정 파일이 없으면 doctor 는 무엇을 잴지(프리셋 · 포트)
        모른다 — 기본값(프리셋 없음 · 8787)으로 그 포트의 아무 서버에 묻지 않고 사용법 오류다
        (P3 4라운드 HIGH)."""
        from remote_ci_monitor.config import ConfigError, load_server_config

        path: Path | str | None = self.opts.config
        overrides: dict[str, dict[str, Any]] = {}
        from_service = False
        if path is None:
            launch = self.service_launch()
            sf = self._service_file
            if launch is not None and sf is not None:
                if sf.unfollowed or sf.unreadable_lines:
                    # 따라가지 않는 줄(`EnvironmentFile=`)이 `RCM_CONFIG` · `RCM_SERVER_PORT` 를 줄
                    # 수 있다 — 그 서버가 어느 설정 · 포트로 도는지 모른다
                    raise ConfigUnknown(
                        "cannot tell which config and port the server's service uses — its service "
                        "file has lines rcm does not read (see above)"
                    )
                overrides = {"server": {"bind": launch.get("bind"), "port": launch.get("port")}}
                path, from_service = self.service_server_config(launch, env)
            else:
                path = self.standard_server_config(env)
        else:
            self.compare_config()
        if path is None:  # 서버 설정 파일이 어디에도 없다
            # `load_server_config(None)` 은 이 프로세스(doctor 셸)의 `RCM_CONFIG` 와 작업 폴더를
            # 본다 — 부르지 않는다
            raise DoctorUsage(
                "no server config found — pass --config PATH (without one, rcm serve starts with "
                "no presets and doctor would only be guessing the port)"
            )
        try:
            # 서비스가 받는 환경 — 셸의 `RCM_SERVER_PORT` 가 아니다
            cfg = load_server_config(
                path, check_tools=False, overrides=overrides, environ=dict(env)
            )
        except ConfigError as e:
            if from_service:
                raise self.config_unknown(e) from e
            raise DoctorUsage(str(e)) from e
        return cfg

    # ── 역할: worker ──

    def worker(self) -> None:
        env = self.service_env()
        self.measured_in = "clean_service_env"
        self.host_facts(env.get("PATH"))
        self.base_requirements()
        try:
            cfg = self.worker_config(env)
        except ConfigUnknown as e:
            # 설정을 모르면 그 설정에 달린 것(서버 · 토큰 · 풀 · 저장소)은 재지 않는다
            self.undecided.append(str(e))
            return
        self.pool = cfg.pool
        self.worker_requirements(cfg, env)
        self.worker_server(cfg, env)
        # 명세를 받았든 못 받았든 git 행은 그 뒤에 정한다(오는 git_ref 프리셋을 알아야 한다)
        self.git_requirements(cfg, env)

    def worker_server(self, cfg: Any, env: dict[str, str]) -> None:
        from remote_ci_monitor.client import WorkerClient

        if cfg.wants_discovery and cfg.path is not None and not self.opts.server:
            self.warnings.append(
                'server = "auto" in worker.toml: the worker takes whichever rcm server answers '
                "on this network — write the server's URL instead"
            )
        url, found_by = cfg.server, "flag" if self.opts.server else "config"
        if not cfg.wants_discovery:
            self.check_url(url, cfg.path)
        if cfg.wants_discovery:
            found = self.discover(cfg.server_name)
            if found is None:
                self.server_doc("—", "mdns", "not confirmed")
                return
            url, found_by = found.address, "mdns"
        self.server_doc(url, found_by, "—")
        if not cfg.token:
            self.undecided.append(
                "a worker token is needed: set RCM_WORKER_TOKEN or token in worker.toml "
                "(create one on the server with rcm token add NAME --worker)"
            )
            return
        client = WorkerClient(url, cfg.token, timeout=REQUEST_TIMEOUT)
        ok, health = self.reach(client.health, "/api/health", WORKER_TOKEN_HINT)
        if not ok:
            return
        self.answered(health)
        ok, me = self.reach(client.whoami, "/api/whoami", WORKER_TOKEN_HINT)
        if not ok:
            return
        if me.kind != "worker":
            self.undecided.append(
                f"a worker token is needed — this one is a {dc.clean_text(me.kind, 64)} "
                "token (rcm token add NAME --worker)"
            )
            return
        ok, spec = self.reach(client.spec_answer, "/worker/spec", WORKER_TOKEN_HINT)
        if not ok:
            return
        self.server["generation"] = spec.generation
        self.measure(spec, env, pool=cfg.pool, accept_any=bool(cfg.accept_any))

    def git_requirements(self, cfg: Any, env: Mapping[str, str]) -> None:
        """git 은 이 머신에 오는 git_ref 프리셋이 있으면 잰다(위임 결정 6). 명세가 `source_modes`
        를 싣지 않으면(옛 서버, 명세를 못 받음) 지금처럼 `[[repos]]` 가 있을 때 잰다. 오는
        git_ref 프리셋의 저장소가 이 워커의 `[[repos]]` 에 없으면 `repo_missing` 이다(E4, §3.6
        D8) — 넣어야 할 이름을 알려 줄 뿐 채우지 않는다."""
        if self.git_presets is None or self.unsure_git:
            # 명세를 못 받았거나 서버가 어느 프리셋이 git_ref 인지 다 말하지 않았다 — 지금처럼
            # `[[repos]]` 가 있으면 잰다
            needed = bool(cfg.repos) or bool(self.git_presets)
        else:
            needed = bool(self.git_presets)
        if needed:
            self.requirements.append(self.git_row(env, cfg.repos))
        have = {r.name for r in cfg.repos}
        for name, repo in sorted((self.git_presets or {}).items()):
            if repo and repo not in have:
                self.problems.append(
                    f"{name}: repository {repo!r} is not in this worker's [[repos]] — its "
                    "git_ref jobs fail with repo_missing (add it to worker.toml)"
                )

    def git_row(self, env: Mapping[str, str], repos: Any) -> dict[str, Any]:
        if not self.path_known:
            return unknown_path_requirement("git", "git_ref presets")
        return git_requirement(
            env,
            token_file=any(r.token_file for r in repos),
            platform=PLATFORM,
            package_manager=self.host.get("package_manager"),
            root=bool(self.host.get("root")),
        )

    def worker_requirements(self, cfg: Any, env: Mapping[str, str]) -> None:
        if cfg.repos:
            if self.opts.offline:
                self.requirements.append(
                    requirement("repos", "info", "not checked (--offline)", "git_ref presets")
                )
            else:
                # 닿지 못하는 저장소의 git_ref 일은 받을 수 없다 — FAIL, 종료 1(위임 결정 4).
                # 서비스 PATH 의 git 을 서비스 환경으로 돌린다. 막힌 shim 이면 돌리지 않는다
                # (실행하면 CLT 설치 창이 뜬다) — git 행이 그 사연을 보인다
                git = runner.find_tool("git", env["PATH"]) if self.path_known else None
                details: list[str] = []
                bad = unsure = False
                for r in cfg.repos:
                    problem, doubt = self.repo_reach(r, git, env)
                    bad = bad or problem is not None
                    unsure = unsure or doubt is not None
                    details.append(f"{r.name} ({problem or doubt or 'ok'})")
                state = "fail" if bad else "unknown" if unsure else "ok"
                self.requirements.append(
                    requirement("repos", state, " · ".join(details), "git_ref presets")
                )
        if self.path_known:
            self.requirements.append(sampler_requirement(env, PLATFORM))
        else:
            self.requirements.append(
                unknown_path_requirement("host sampler", "host load and admission")
            )
        if PLATFORM.startswith("darwin"):
            self.requirements.append(local_network_requirement())
            self.requirements.append(sleep_requirement())
        elif self._service_file is not None and self._service_file.manager == dc.SYSTEMD:
            linger = linger_requirement(self.ctx.environ.get("USER"))
            if linger is not None:
                self.requirements.append(linger)

    def repo_reach(
        self, repo: Any, git: str | None, env: Mapping[str, str]
    ) -> tuple[str | None, str | None]:
        """(문제, 모름의 이유) — 둘 다 None 이면 닿는다. 모르는 것을 「닿지 않음」으로 적지 않는다.

        `token_file` 저장소는 워커가 git 을 부를 때처럼(`gitops._run_git`) 그 파일을 읽는 자격
        헬퍼 하나(`gitops.git_env`)로 부른다 — URL 만 넘겨 전역 자격으로 재면 워커가 닿는 저장소를
        「닿지 않음」으로 단정한다(P3 3라운드 MED). 그 헬퍼는 git 2.31+ 에서만 먹는다."""
        from remote_ci_monitor import gitops

        if not self.path_known:
            return None, "not checked — the service PATH is unknown"
        if git is None:
            return "git missing", None
        credential = repo.credential
        if credential is None:
            return _reached(self.ctx.ls_remote(repo.url, git, env))
        if not os.access(credential, os.R_OK):
            return None, (
                "not checked — this user cannot read its token_file (run rcm doctor as the "
                "service's user)"
            )
        version = _git_version(git, env)
        if version is None or version < MIN_GIT_TOKEN_FILE:
            return None, "not checked — token_file needs git 2.31+ (see the git row)"
        return _reached(
            self.ctx.ls_remote(repo.url, git, gitops.git_env(env, credential=credential))
        )

    def measure(self, spec: Spec, env: dict[str, str], *, pool: str, accept_any: bool) -> None:
        """명세대로 깨끗한 환경에서 잰다 — 잰 것을 서버에 보내지 않는다(TD12)."""
        if not self.path_known:
            # 서비스 PATH 를 모르면 프로브도 돌리지 않는다 — 빈 PATH 로 돌리면 「missing」을
            # 지어낸다. 프리셋도 재지 않는다. 판정 못 함은 service_env 가 이미 적었다
            for probe in spec.probes:
                name = dc.clean_text(probe.name, 64)
                self.probes[name] = {"state": dc.TOOL_UNKNOWN, "value": None}
                self.probe_why[name] = "not run — the service PATH is unknown"
            return
        for raw_name, r in hostsample.run_probes(list(spec.probes), env).items():
            name = dc.clean_text(raw_name, 64)  # 서버가 보낸 이름 — 찍기 전에 정리한다
            state = {
                "ok": dc.TOOL_OK,
                "missing": dc.TOOL_MISSING,
                "error": dc.TOOL_BROKEN,
                "timeout": dc.TOOL_UNKNOWN,
            }.get(r.state, dc.TOOL_UNKNOWN)
            # 프로브 값에 이 머신의 경로가 섞일 수 있다 — 홈은 가린다
            value = None if r.value is None else dc.hide_home(dc.clean_text(r.value), self.home)
            self.probes[name] = {"state": state, "value": value}
            if state == dc.TOOL_UNKNOWN:
                self.probe_why[name] = "timed out" if r.state == "timeout" else "no answer"
        os_name = "darwin" if PLATFORM.startswith("darwin") else "linux"
        for p in spec.presets:
            job_env = jobenv.job_env(p.env_passthrough, p.env, env)
            missing, workspace = runner.measure_tools(p.requires, job_env)
            unset = [
                dc.clean_text(u, 64)
                for u in jobenv.missing_passthrough(p.env_passthrough, env, preset_env=p.env)
            ]
            # 이 머신에서 돌 수 없는 점검은 서버가 `unreachable_checks` 에 적는다(TO18, 위임 결정
            # 2). 칸이 없으면(None) 그 점검은 「모름」이다 — 목록에 무엇이 빠졌는지로 짐작하지
            # 않는다. `placement` 가 없으면(옛 서버) 그 프리셋은 모름이다 — `pool` 로 짐작하지
            # 않는다(TD5)
            row = dc.classify_preset(
                p.entry(),
                {"missing_tools": missing, "workspace_tools": workspace},
                pool=pool,
                accept_any=accept_any,
                hints=spec.hints,
                os_name=os_name,
                unreachable=p.unreachable,
            )
            for m in row.missing:
                note = runner.missing_note(m["tool"], job_env)
                if note:
                    m["note"] = note
            if unset and row.applies != dc.APPLIES_OTHER_POOL:
                self.warnings.append(
                    f"{row.name}: {', '.join(unset)} not set in the service environment "
                    "(env_passthrough)"
                )
            if self.opts.shell_path and row.applies != dc.APPLIES_OTHER_POOL:
                shell_env = jobenv.job_env(p.env_passthrough, p.env, self.ctx.environ)
                row.shell_differs = runner.measure_tools(p.requires, shell_env) != (
                    missing,
                    workspace,
                )
            self.rows.append(row)
        self.judged = True
        self.git_presets, unsure = git_ref_presets(spec.presets, self.rows)
        self.unknown += unsure
        self.unsure_git = bool(unsure) or any(r.applies == dc.APPLIES_UNKNOWN for r in self.rows)

    # ── 역할: coordinator · coordinator-worker ──

    def coordinator(self) -> None:
        from remote_ci_monitor.client import Client
        from remote_ci_monitor.config import ConfigError, advertise_enabled, load_client_config

        env = self.service_env()
        # 서버의 측정을 실제로 읽었을 때만 `server_service_env` 다(`local_lane`)
        self.measured_in = "none"
        self.host_facts(env.get("PATH"))
        self.base_requirements()
        self.requirements.append(sqlite_requirement())
        self.requirements.append(fd_requirement(self._service_file))
        try:
            cfg = self.server_config(env)
        except ConfigUnknown as e:
            # 설정을 모르면 프리셋 · 포트를 모른다 — 서버에 묻지 않고 프리셋도 판정하지 않는다
            self.undecided.append(str(e))
            return
        # 서버는 git_ref 제출의 ref 를 풀고 점검을 건다 — git_ref 프리셋이 있으면 git 을 잰다(위임
        # 결정 6). 프리셋의 저장소가 `[[repos]]` 에 있는지는 설정 검증이 이미 본다
        if any("git_ref" in p.source_modes for p in cfg.presets):
            self.requirements.append(self.git_row(env, cfg.repos))
        if self.path_known:
            self.requirements.append(sampler_requirement(env, PLATFORM))
        else:
            self.requirements.append(
                unknown_path_requirement("host sampler", "host load and admission")
            )
        if PLATFORM.startswith("darwin") and advertise_enabled(cfg.server):
            self.requirements.append(local_network_requirement())
        if PLATFORM.startswith("darwin"):
            self.requirements.append(sleep_requirement())
        if self.role == dc.ROLE_COORDINATOR:
            self.local_lane_only(cfg)
        bind = cfg.server.bind
        host = "127.0.0.1" if bind in ("0.0.0.0", "::", "") else bind
        host = f"[{host}]" if ":" in host else host
        self.check_url(self.opts.server or "", None)
        url = self.opts.server or f"http://{host}:{cfg.server.port}"
        self.server_doc(url.rstrip("/"), "flag" if self.opts.server else "config", "—")
        try:
            token = load_client_config(None).token
        except ConfigError:
            token = ""
        if not token:
            self.undecided.append(
                "a client token is needed to read the server's measurements: set RCM_TOKEN or "
                "token in client.toml (rcm token add NAME on the server)"
            )
            return
        client = Client(self.server["url"], token, timeout=REQUEST_TIMEOUT)
        ok, health = self.reach(client.health, "/api/health", CLIENT_TOKEN_HINT)
        if not ok:
            return
        ok, _me = self.reach(client.whoami, "/api/whoami", CLIENT_TOKEN_HINT)
        if not ok:
            return
        self.answered(health)
        ok, machines = self.reach(
            lambda: client.get_json("/api/machines"), "/api/machines", CLIENT_TOKEN_HINT
        )
        if not ok:
            return
        self.server["generation"] = machines.generation
        # 원격 머신 목록은 표시만 한다(종료 코드에 들지 않는다). 서버가 말하지 않은 칸은 null 이다 —
        # `pool` 이 없는 머신을 "None" 풀로, `alive` 가 없는 머신을 down 으로 적지 않는다
        self.remote = [
            {
                "name": dc.clean_text(m.name, 64),
                "pool": None if m.pool is None else dc.clean_text(m.pool, 64),
                "alive": m.alive,
                "eligible": None
                if m.presets is None
                else sum(1 for v in m.presets.values() if v["state"] == "eligible"),
                "presets": None if m.presets is None else len(m.presets),
            }
            for m in machines.machines
            if m.kind != "local"
        ]
        if self.role == dc.ROLE_COORDINATOR_WORKER:
            local = next((m for m in machines.machines if m.kind == "local"), None)
            self.local_lane(cfg, health, local)

    def local_lane_only(self, cfg: Any) -> None:
        """원격 머신이 후보가 아닌 프리셋을 「로컬 레인 필요」로 보인다(TO6). 판정은 서버의 후보
        함수(`capabilities.remote_can_take`)가 한다 — 같은 규칙을 여기 다시 쓰지 않는다(6-A-U1).
        설정만 보므로 서버에 닿지 못해도 보인다."""
        from remote_ci_monitor.capabilities import remote_can_take

        for p in cfg.presets:
            if remote_can_take(cfg, p):
                continue
            self.rows.append(
                dc.PresetRow(
                    p.name,
                    dc.APPLIES_LOCAL_LANE,
                    None,
                    checks=list(p.checks),
                    code="local_only",
                    tools=[os.path.basename(t) for t in p.requires],
                    note=dc.LOCAL_LANE_NOTE,
                )
            )

    def local_lane(self, cfg: Any, health: Health, local: Machine | None) -> None:
        """로컬 레인은 서버가 자기 서비스 환경에서 잰 값으로 본다(`preset_env`, G7 · TD30).

        서버가 그 프리셋을 재지 않았으면(키가 없는 옛 서버, 항목이 없음) `requires` 가 있는
        프리셋은 「모름」이다 — 빈 「빠진 도구」로 읽어 ready 라 부르지 않는다. 서버는 `requires` 와
        `env_passthrough` 가 둘 다 없는 프리셋은 재지 않으므로 그 프리셋은 항목이 없어도 된다.

        프리셋은 디스크의 설정에서, 측정은 서버의 메모리에서 온다. 둘은 이름으로만 이어지므로
        서버가 아직 그 설정을 reload 하지 않았으면 옛 정의의 측정을 읽게 된다. 그래서 항목의
        `digest` 를 디스크의 정의로 만든 지문과 견준다. 다르거나 항목이 지문을 싣지 않았으면 그
        프리셋은 모름이다(P6 GAP E4).
        """
        measured = health.preset_env
        if measured is not None:
            self.measured_in = "server_service_env"
        # 서버의 판정 코드는 표시만 한다 — 없으면 doctor 가 정한 코드를 둔다
        verdicts = {} if local is None or local.presets is None else local.presets
        os_name = "darwin" if PLATFORM.startswith("darwin") else "linux"
        # 로컬 레인 행도 워커와 같은 `[[tools]]` 안내를 쓴다(위임 결정 E3)
        hints = dc.tool_hints(
            [{"name": t.name, "docs": t.docs, "install": dict(t.install)} for t in cfg.tools]
        )
        for p in cfg.presets:
            got = None if measured is None else measured.get(p.name)
            mine = dc.preset_env_digest(p.requires, p.env_passthrough, p.env)
            stale = got is not None and got.digest != mine
            entry = {
                "name": p.name,
                "requires": list(p.requires),
                "pool": p.pool,
                "pools": list(p.pools),
                "placement": "any-capable" if p.placement != "pool" else "pool",
                "checks": list(p.checks),
            }
            row = dc.classify_preset(
                entry,
                {
                    "missing_tools": [] if got is None else list(got.missing_tools),
                    # 서버는 워크스페이스 도구를 따로 세지 않는다 — 빠진 도구에 든다
                    "workspace_tools": [],
                },
                pool=dc.DEFAULT_POOL,
                accept_any=bool(cfg.server.local_accept_any),
                hints=hints,
                os_name=os_name,
                unreachable={},  # 서버의 로컬 레인은 모든 점검을 돌린다(local_only 도)
            )
            verdict = verdicts.get(p.name)
            if verdict is not None and verdict["code"] is not None:
                row.code = dc.clean_text(verdict["code"], 64)
            if got is None and p.requires and row.counts:
                why = "older rcm" if measured is None else "no preset_env entry"
                row.state, row.code = dc.UNKNOWN, "tools_unknown"
                row.note = f"the server did not report its own tool measurements ({why})"
            if stale and row.counts:
                # 서버의 측정이 이 정의의 것이 아니다 — 빠진 도구도 판정 코드도 읽지 않는다
                row.state, row.code = dc.UNKNOWN, "not_reloaded"
                row.note = (
                    "the server measured another definition of this preset — it has not "
                    "reloaded this config yet (rcm reload)"
                    if got is not None and got.digest is not None
                    else "the server does not say which definition it measured (older rcm)"
                )
                row.missing = []
            unset = (
                []
                if got is None or stale
                else [dc.clean_text(v, 64) for v in got.missing_passthrough]
            )
            if unset and row.applies != dc.APPLIES_OTHER_POOL:
                self.warnings.append(
                    f"{p.name}: {', '.join(unset)} not set in the server's service environment"
                )
            self.rows.append(row)
        self.judged = True

    # ── 역할: client ──

    def client(self) -> None:
        from remote_ci_monitor.client import Client
        from remote_ci_monitor.config import ConfigError, load_client_config

        try:
            cfg = load_client_config(self.opts.config, server=self.opts.server)
        except ConfigError as e:
            raise DoctorUsage(str(e)) from e
        self.host_facts(None)
        self.base_requirements()
        url, found_by = cfg.server, "flag" if self.opts.server else "config"
        if not cfg.wants_discovery:
            self.check_url(url, cfg.path)
        if cfg.wants_discovery:
            found = self.discover()
            if found is None:
                self.server_doc("—", "mdns", "not confirmed")
                return
            url, found_by = found.address, "mdns"
        self.server_doc(url or "—", found_by, "—")
        if not url:
            self.undecided.append("no server configured — pass --server URL or write client.toml")
            return
        client = Client(url, cfg.token or None, timeout=REQUEST_TIMEOUT)
        ok, health = self.reach(client.health, "/api/health", CLIENT_TOKEN_HINT)
        if not ok:
            return
        self.answered(health)
        version = self.server["version"]
        if version is not None and version != __version__:
            self.warnings.append(
                f"this client runs v{__version__} and the server v{version} — rcm follows the "
                "server's version on the next command (docs/operating.md)"
            )
        if not cfg.token:
            self.undecided.append(
                f"a client token is needed: set {cfg.token_env} or token in client.toml"
            )
            return
        self.reach(client.whoami, "/api/whoami", CLIENT_TOKEN_HINT)


def _reached(result: str | None) -> tuple[str | None, str | None]:
    """`ls_remote` 의 답 → (문제, 모름의 이유). 시한 초과 · 실행 실패(`dc.Unsure`)는 저장소에
    닿지 않는다는 사실이 아니다 — 모름이다(P3 4라운드 MED)."""
    if isinstance(result, dc.Unsure):
        return None, f"not checked — {result}"
    return result, None


def known_home(environ: Mapping[str, str]) -> str | None:
    """홈 폴더. HOME 이 없고 이 uid 에 passwd 항목도 없으면 None — `Path.home()` 의
    `RuntimeError` 가 경계 밖에서 traceback 이 되지 않게(P3 4라운드 MED)."""
    home = environ.get("HOME")
    if home:
        return home
    try:
        return str(Path.home())
    except (RuntimeError, KeyError, OSError):
        return None


#: 홈 폴더를 모를 때의 판정 못 함 줄
NO_HOME = (
    "HOME is not set and this user has no home folder — rcm cannot find its config and service "
    "files (set HOME)"
)


def _emit(opts: Options, ctx: Context, doc: dict[str, Any]) -> None:
    """문서를 낸다. 글자를 못 찍는 stdout(UTF-8 이 아닌 로캘)이면 JSON 은 `\\uXXXX` 로 다시 적고
    글은 못 찍는 글자만 바꾼다 — 판정을 잃지 않는다(P6 GAP E3)."""
    if opts.json:
        try:
            ctx.out(json.dumps(doc, ensure_ascii=False, indent=2))
        except UnicodeEncodeError:
            ctx.out(json.dumps(doc, ensure_ascii=True, indent=2))
        return
    for line in dc.render(doc):
        try:
            ctx.out(line)
        except UnicodeEncodeError as e:
            ctx.out(line.encode(e.encoding, "replace").decode(e.encoding))


def _silence_stdout() -> None:
    """읽는 쪽이 닫은 파이프(`rcm doctor | head`) — 남은 출력은 버린다. 파이썬이 끝날 때 다시
    flush 하다 `Exception ignored … BrokenPipeError` 와 종료 120 을 내지 않게 stdout 을 devnull 로
    돌린다(파이썬 문서 「Note on SIGPIPE」의 방법)."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (OSError, ValueError, AttributeError):  # 파일이 아닌 stdout(시험의 capsys)
        pass


def _deliver(opts: Options, ctx: Context, doc: dict[str, Any]) -> None:
    """출력도 경계 안이다 — 닫힌 파이프는 traceback 없이 조용히, 종료 코드는 판정 그대로(P6 GAP
    E3). stdout 은 여기서 flush 한다: 블록 버퍼라 파이프가 닫힌 것을 종료 때가 아니라 여기서
    안다."""
    try:
        _emit(opts, ctx, doc)
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_stdout()


def run(opts: Options, ctx: Context) -> tuple[int, dict[str, Any] | None]:
    """(종료 코드, 문서). 사용법 오류면 (2, None) — 이유는 ctx.err 로 이미 나갔다.

    맨 바깥 경계: 읽는 자리가 모르는 예외는 traceback 이 아니라 「판정 못 함」(종료 3)이다. 모름을
    성공으로 적지 않고 모름으로 적을 뿐이다. 경계는 마지막 그물이다 — 읽는 자리마다 모양을 먼저
    보고, 망가진 입력 표 시험(tests/test_doctor_cli.py)은 이 줄이 보이면 빨개진다."""
    home = known_home(ctx.environ)
    role = opts.role
    if home is None:
        # 설정과 서비스 파일의 표준 자리가 모두 홈 아래다 — 재지 않고 판정 못 함이다
        ctx.err(f"rcm: {NO_HOME}")
        blind = _Run(opts, ctx, role or "unknown", "")
        blind.undecided.append(NO_HOME)
        doc = blind.document()
        _deliver(opts, ctx, doc)
        return dc.EXIT_UNKNOWN, doc
    try:
        if role is None:
            roles = infer_roles(ctx.environ)
            if not roles:
                raise DoctorUsage(
                    "no rcm config found on this machine — pass --role, or run rcm init first"
                )
            if len(roles) > 1:
                raise DoctorUsage(
                    f"this machine has several roles ({', '.join(roles)}) — pass --role ROLE"
                )
            role = roles[0]
        r = _Run(opts, ctx, role, home)
        if role == dc.ROLE_WORKER:
            r.worker()
        elif role in (dc.ROLE_COORDINATOR, dc.ROLE_COORDINATOR_WORKER):
            r.coordinator()
        else:
            r.client()
        doc = r.document()
    except DoctorUsage as e:
        # 설정 파일의 절대 경로가 문장에 든다 — 홈 폴더는 가린다(검증자 1 #5)
        ctx.err(f"rcm: {dc.hide_home(str(e), home)}")
        return dc.EXIT_USAGE, None
    except Exception as e:  # noqa: BLE001 — doctor 의 맨 바깥 경계
        line = dc.could_not_decide(e, home)
        ctx.err(line)
        crashed = _Run(opts, ctx, role or "unknown", home)
        crashed.undecided.append(line)
        doc = crashed.document()
        if opts.json:
            _deliver(opts, ctx, doc)
        return dc.EXIT_UNKNOWN, doc
    _deliver(opts, ctx, doc)
    return int(doc["exit"]), doc


def main(args: Any, ctx: Context) -> int:
    opts = Options(
        role=getattr(args, "role", None),
        server=getattr(args, "server", None),
        config=getattr(args, "config", None),
        json=bool(getattr(args, "json", False)),
        offline=bool(getattr(args, "offline", False)),
        shell_path=bool(getattr(args, "shell_path", False)),
        service_file=getattr(args, "service_file", None),
    )
    return run(opts, ctx)[0]
