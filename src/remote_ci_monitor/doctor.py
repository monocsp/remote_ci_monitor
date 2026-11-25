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
import os
import plistlib
import re
import shlex
import sqlite3
import sys
import tarfile
import tomllib
import xml.parsers.expat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remote_ci_monitor import __version__, hostsample, runner
from remote_ci_monitor.core import doctor as dc
from remote_ci_monitor.core import jobenv

#: 플랫폼 — 시험이 바꿔 끼운다(Local Network 안내 · 설치 문구 · 서비스 관리자)
PLATFORM = sys.platform
MIN_PYTHON = (3, 11, 4)
MIN_GIT_TOKEN_FILE = (2, 31)
MIN_SQLITE = (3, 24, 0)
FD_WANTED = 4096
#: 표준 라벨의 설치 자리(launchd · systemd 사용자 유닛)
LAUNCH_AGENTS_GLOB = "com.remote-ci-monitor.*.plist"
SYSTEMD_GLOB = "rcm-*.service"
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


def _git_version(git: str) -> tuple[int, int] | None:
    import subprocess

    try:
        proc = subprocess.run(
            [git, "--version"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL
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
        version = _git_version(git)
        if version is None or version < MIN_GIT_TOKEN_FILE:
            shown = ".".join(map(str, version)) if version else "unknown version"
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

    @property
    def env_names(self) -> list[str]:
        """환경 변수 **이름**만 — 값은 어디에도 찍지 않는다(TD55)."""
        return sorted(self.env)


def _rcm_command(argv: list[str]) -> str | None:
    """`rcm worker --config …` → `worker`. 첫 인자(실행 파일) 뒤의 첫 비옵션 인자."""
    for arg in argv[1:]:
        if not arg.startswith("-"):
            return arg
    return None


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
    args = [str(a) for a in raw_args]
    env = {str(k): str(v) for k, v in raw_env.items()}
    return ServiceFile(
        p,
        dc.LAUNCHD,
        _rcm_command(args),
        env,
        nofile or None,
        str(program or (args[0] if args else "")) or None,
    )


def read_service_file(path: str | os.PathLike[str]) -> ServiceFile:
    """launchd plist 나 systemd 유닛을 읽는다. 파일을 못 읽으면 `ServiceFileUnreadable`.

    systemd 유닛의 줄 하나를 못 읽으면(닫히지 않은 따옴표 · 숫자가 아닌 한도) 그 줄을 건너뛰되
    `unreadable_lines` 에 남긴다 — 조용히 버리면 「PATH 를 주지 않았다」 같은 틀린 안내가 나간다.
    """
    p = Path(path)
    if p.suffix == ".plist":
        return _read_plist(p)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ServiceFileUnreadable(p, type(e).__name__) from e
    except ValueError as e:  # UnicodeDecodeError
        raise ServiceFileUnreadable(p, "not UTF-8") from e
    env: dict[str, str] = {}
    command: str | None = None
    program: str | None = None
    nofile_unit: int | None = None
    unreadable: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        key, _, value = line.partition("=")
        try:
            if key == "Environment":
                for item in shlex.split(value):
                    k, sep, v = item.partition("=")
                    if sep:
                        env[k] = v
            elif key == "ExecStart":
                argv = shlex.split(value)
                command = _rcm_command(argv)
                program = argv[0].lstrip("@-:+!") if argv else None
            elif key == "LimitNOFILE":
                first = value.split(":", 1)[0].strip()
                nofile_unit = 1 << 20 if first == "infinity" else int(first)
        except ValueError:
            unreadable.append(f"line {number} ({key}=)")
    return ServiceFile(p, dc.SYSTEMD, command, env, nofile_unit, program, unreadable)


def _matching(
    paths: list[Path], role: str, unreadable: list[ServiceFileUnreadable]
) -> list[ServiceFile]:
    """그 역할의 `rcm` 하위 명령을 띄우는 파일만. 못 읽은 파일은 버리지 않고 `unreadable` 에
    모은다 — 그 파일이 이 역할의 것이었는지 모르기 때문이다."""
    want = ROLE_COMMANDS.get(role)
    out: list[ServiceFile] = []
    for p in paths:
        try:
            sf = read_service_file(p)
        except ServiceFileUnreadable as e:
            unreadable.append(e)
            continue
        if sf.command is None and any("ExecStart" in u for u in sf.unreadable_lines):
            # 띄우는 명령 줄을 못 읽었다 — 이 역할의 파일인지 모른다
            unreadable.append(ServiceFileUnreadable(p, f"cannot read {sf.unreadable_lines[0]}"))
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
    """찾는 순서: `--service-file`, 표준 라벨의 설치 자리, setup 의 자리 `<config dir>/service/*`.

    설치 자리를 setup 자리보다 먼저 본다 — 서비스 관리자가 실제로 읽는 파일이다(6-B-V2). 후보는
    그 역할의 `rcm` 하위 명령을 띄우는 파일로 거른다. 같은 자리에 둘 이상이면 사람이 고르고,
    비TTY 면 사용법 오류다. 없으면 None(「서비스 파일 없음」).

    읽지 못한 파일은 `unreadable` 에 모은다. 자동으로 찾은 후보면 빼고 나머지로 계속 찾는다.
    `--service-file` 로 준 파일이면 `ServiceFileUnreadable` 를 그대로 던진다.
    """
    skipped: list[ServiceFileUnreadable] = [] if unreadable is None else unreadable
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise DoctorUsage(f"service file not found: {p.name}")
        return read_service_file(p)
    installed = sorted((home / "Library" / "LaunchAgents").glob(LAUNCH_AGENTS_GLOB))
    installed += sorted((home / ".config" / "systemd" / "user").glob(SYSTEMD_GLOB))
    staged_dir = config_dir / "service"
    staged = sorted(
        p for p in staged_dir.glob("*") if p.suffix in (".plist", ".service") and p.is_file()
    )
    for stage in (installed, staged):
        found = _matching(stage, role, skipped)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = ", ".join(dc.mask_home(str(sf.path), str(home)) for sf in found)
            if is_tty():
                picked = choose([sf.path for sf in found if sf.path], str(home))
                if picked is not None:
                    return read_service_file(picked)
            raise DoctorUsage(f"several service files match: {names} — pass --service-file PATH")
    return None


# ── 역할 읽기 ────────────────────────────────────────────────────────────────


def infer_roles(environ: Mapping[str, str]) -> list[str]:
    """있는 설정에서 역할을 읽어 낸다(결정 239). 환경변수만 쓰는 워커 · 클라이언트도
    「설정 있음」이다(TD49)."""
    from remote_ci_monitor.config import (
        ConfigError,
        find_client_config,
        find_server_config,
        user_config_dir,
    )

    roles: list[str] = []
    try:
        server = find_server_config(None)
    except ConfigError as e:
        raise DoctorUsage(str(e)) from e
    if server is not None:
        lanes: Any = 1
        try:
            with server.open("rb") as fh:
                lanes = (tomllib.load(fh).get("server") or {}).get("lanes", 1)
        except (OSError, tomllib.TOMLDecodeError):
            pass
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


def git_ref_presets(entries: list[Mapping[str, Any]], rows: list[dc.PresetRow]) -> dict[str, str]:
    """이 머신에 오는 git_ref 프리셋 → 저장소 이름. 오는 프리셋의 `checks` 에 이름이 오른 점검
    프리셋도 든다 — 점검은 그 머신을 지정한 git_ref 잡이다(결정 94)."""
    by_name = {str(p["name"]): p for p in entries}
    coming = {r.name for r in rows if r.counts}
    names = set(coming)
    for r in rows:
        if r.name in coming:
            names.update(c for c in r.checks if c in by_name)
    out: dict[str, str] = {}
    for name in names:
        p = by_name.get(name) or {}
        if "git_ref" in [str(m) for m in p.get("source_modes") or ()]:
            out[name] = dc.clean_text(p.get("repo") or "", 64)
    return out


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
    ls_remote: Callable[[str], str | None]
    worker_service_path: str
    out: Callable[[str], None] = print
    err: Callable[[str], None] = lambda m: print(m, file=sys.stderr)


class _Run:
    """한 번의 진단 — 줄을 모으고 마지막에 문서 하나로 낸다."""

    def __init__(self, opts: Options, ctx: Context, role: str):
        self.opts, self.ctx, self.role = opts, ctx, role
        self.home = str(ctx.environ.get("HOME") or Path.home())
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
        self.path_known = True  # 서비스 PATH 를 아나 — 모르면 PATH 로 찾는 것은 재지 않는다
        #: 이 머신에 오는 git_ref 프리셋 → 저장소 이름(명세가 `source_modes` 를 실을 때만 안다)
        self.git_presets: dict[str, str] | None = None

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

    def confirm(self, found: Any) -> bool:
        """mDNS 로 찾은 서버 — 사람이 확인하기 전에는 어떤 요청도 보내지 않는다(TD10 · TD28)."""
        where = f"{found.name} ({found.address})"
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
            names = ", ".join(f"{f.name} ({f.address})" for f in picks)
            raise DoctorUsage(f"several rcm servers on this network: {names} — pass --server URL")
        return picks[0] if self.confirm(picks[0]) else None

    def server_doc(self, url: str, found_by: str, state: str) -> dict[str, Any]:
        self.server = {
            "url": url,
            "found_by": found_by,
            "state": state,
            "generation": None,
            "version": None,
        }
        return self.server

    def reach(self, call: Callable[[], Any], route: str, token_hint: str) -> tuple[bool, Any]:
        """GET 하나. 닿지 않거나 401 · 옛 서버면 판정 못 함(종료 3)으로 적고 (False, None)."""
        from remote_ci_monitor.client import ClientError

        assert self.server is not None
        try:
            return True, call()
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

    def document(self) -> dict[str, Any]:
        rows = self.rows
        if self.judged and self.role in (dc.ROLE_WORKER, dc.ROLE_COORDINATOR_WORKER):
            reason = dc.no_jobs_reason(rows, self.pool)
            if reason:
                self.problems.append(reason)
        problems = dc.preset_problems(rows) + self.problems
        problems += [
            f"{r['name']}: {r['detail']}" for r in self.requirements if r["state"] == "fail"
        ]
        warnings = list(self.warnings)
        warnings += [f"{r.name}: {r.warn}" for r in rows if r.warn]
        warnings += [
            f"{r['name']}: {r['detail']}" for r in self.requirements if r["state"] == "warn"
        ]
        code = dc.decide_exit(problems, self.undecided)
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
            "undecided": list(self.undecided),
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
        if sf is not None:
            for where in sf.unreadable_lines:
                self.undecided.append(
                    f"cannot read {where} of the service file "
                    f"{self.mask(str(sf.path))} — fix that line"
                )
        manager = sf.manager if sf else (dc.LAUNCHD if PLATFORM == "darwin" else dc.SYSTEMD)
        if broken is not None:
            # 사람이 준 파일을 못 읽었다 — 그 PATH 를 모르므로 기본 PATH 로 재지 않는다
            manager = dc.LAUNCHD if broken.path.suffix == ".plist" else dc.SYSTEMD
            env = dc.clean_env(manager, self.ctx.environ, {})
            env.pop("PATH", None)
        elif sf is not None:
            env = dc.clean_env(manager, self.ctx.environ, sf.env)
        else:
            env = dc.clean_env(
                manager, self.ctx.environ, {}, default_path=self.ctx.worker_service_path
            )
        self.path_known = "PATH" in env
        shown = sf.path if sf is not None else (broken.path if broken is not None else None)
        self.service = {
            "file": self.mask(str(shown)) if shown else None,
            "manager": manager,
            "path": self.mask(env["PATH"]) if self.path_known else None,
            "env_names": sf.env_names if sf else [],
        }
        self._service_file = sf
        if not self.path_known and broken is None:
            # systemd 의 기본 PATH 는 배포판마다 다르다 — 추정해서 재면 없는 「빠짐」을 지어낸다
            self.undecided.append(
                "the service file sets no PATH and systemd's default differs between "
                "distributions — add Environment=PATH=… to the service file"
            )
        elif sf is not None and sf.program and os.path.isabs(sf.program):
            own_bin = os.path.dirname(sf.program)
            if own_bin in env["PATH"].split(":"):
                self.warnings.append(
                    f"{self.mask(own_bin)} (where the service's rcm lives) is on the service "
                    "PATH — a preset's python3 becomes that venv's; keep the absolute path to "
                    "rcm in the service file and drop it from PATH (docs/operating.md)"
                )
        want = ROLE_COMMANDS.get(self.role)
        if self.opts.service_file and sf is not None and want and sf.command != want:
            # 사람이 고른 파일이라 거르지 않는다 — 그 파일로 재되 다른 역할의 파일이라고 알린다(E1)
            self.warnings.append(
                f"{self.service['file']} starts rcm {sf.command or '(no command)'}, not rcm "
                f"{want} — measured with it as given"
            )
        if self.opts.shell_path:
            self.shell = {"path": self.mask(self.ctx.environ.get("PATH", ""))}
        return env

    # ── 역할: worker ──

    def worker(self) -> None:
        from remote_ci_monitor.config import ConfigError, load_worker_config, user_config_dir

        path: Path | str | None = self.opts.config
        if path is None:
            default = user_config_dir() / "worker.toml"
            path = default if default.is_file() else None
        try:
            cfg = load_worker_config(path, overrides={"server": self.opts.server})
        except ConfigError as e:
            raise DoctorUsage(str(e)) from e
        self.pool = cfg.pool
        env = self.service_env()
        self.measured_in = "clean_service_env"
        self.host_facts(env.get("PATH"))
        self.base_requirements()
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
        self.server.update(state="ok", version=dc.clean_text(health.get("version"), 64))
        ok, me = self.reach(client.whoami, "/api/whoami", WORKER_TOKEN_HINT)
        if not ok:
            return
        if (me.get("kind") or "client") != "worker":
            self.undecided.append(
                f"a worker token is needed — this one is a {dc.clean_text(me.get('kind'))} "
                "token (rcm token add NAME --worker)"
            )
            return
        ok, spec = self.reach(client.spec, "/worker/spec", WORKER_TOKEN_HINT)
        if not ok or not isinstance(spec, dict):
            return
        self.server["generation"] = dc.clean_text(spec.get("generation"), 64) or None
        self.measure(spec, env, pool=cfg.pool, accept_any=bool(cfg.accept_any))

    def git_requirements(self, cfg: Any, env: Mapping[str, str]) -> None:
        """git 은 이 머신에 오는 git_ref 프리셋이 있으면 잰다(위임 결정 6). 명세가 `source_modes`
        를 싣지 않으면(옛 서버, 명세를 못 받음) 지금처럼 `[[repos]]` 가 있을 때 잰다. 오는
        git_ref 프리셋의 저장소가 이 워커의 `[[repos]]` 에 없으면 `repo_missing` 이다(E4, §3.6
        D8) — 넣어야 할 이름을 알려 줄 뿐 채우지 않는다."""
        needed = bool(cfg.repos) if self.git_presets is None else bool(self.git_presets)
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
                # 닿지 못하는 저장소의 git_ref 일은 받을 수 없다 — FAIL, 종료 1(위임 결정 4)
                details, bad = [], False
                for r in cfg.repos:
                    problem = self.ctx.ls_remote(r.url)
                    bad = bad or problem is not None
                    details.append(f"{r.name} ({'ok' if problem is None else problem})")
                state = "fail" if bad else "ok"
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

    def measure(
        self, spec: Mapping[str, Any], env: dict[str, str], *, pool: str, accept_any: bool
    ) -> None:
        """명세대로 깨끗한 환경에서 잰다 — 잰 것을 서버에 보내지 않는다(TD12)."""
        from remote_ci_monitor.config import Probe

        probes = []
        for p in spec.get("probes") or []:
            if isinstance(p, dict) and p.get("argv") and isinstance(p.get("name"), str):
                timeout = int(p.get("timeout_seconds") or 10)
                argv = tuple(str(a) for a in p["argv"])
                probes.append(Probe(name=p["name"], argv=argv, timeout_seconds=timeout))
        for name, r in hostsample.run_probes(probes, env).items():
            state = {
                "ok": dc.TOOL_OK,
                "missing": dc.TOOL_MISSING,
                "error": dc.TOOL_BROKEN,
                "timeout": dc.TOOL_UNKNOWN,
            }.get(r.state, dc.TOOL_UNKNOWN)
            self.probes[name] = {"state": state, "value": r.value}
            if state == dc.TOOL_UNKNOWN:
                self.undecided.append(f"probe {name} timed out")
        hints = dc.tool_hints(spec.get("tools"))
        entries = [p for p in spec.get("presets") or [] if isinstance(p, dict) and p.get("name")]
        if not self.path_known:
            return  # 모르는 PATH 로 재지 않는다 — 판정 못 함은 service_env 가 이미 적었다
        if entries and not all("placement" in p for p in entries):
            self.undecided.append(
                "the server does not say which pool each preset runs in (older rcm) — presets "
                "for other pools cannot be counted"
            )
        os_name = "darwin" if PLATFORM.startswith("darwin") else "linux"
        # 이 머신에서 돌 수 없는 점검은 서버가 `unreachable_checks` 에 적는다(TO18, 위임 결정 2).
        # 명세 목록에 무엇이 빠졌는지로 짐작하지 않는다. 칸이 없으면 그 점검은 「모름」이다
        blind: list[str] = []
        for p in entries:
            far = dc.unreachable_checks(p)
            if far is None and p.get("checks"):
                blind.append(dc.clean_text(p["name"], 64))
            passthrough = tuple(str(x) for x in p.get("env_passthrough") or ())
            fixed = {str(k): str(v) for k, v in (p.get("env") or {}).items()}
            requires = tuple(str(x) for x in p.get("requires") or ())
            job_env = jobenv.job_env(passthrough, fixed, env)
            missing, workspace = runner.measure_tools(requires, job_env)
            unset = jobenv.missing_passthrough(passthrough, env, preset_env=fixed)
            entry = dict(p) if "placement" in p else {**p, "pool": pool}
            row = dc.classify_preset(
                entry,
                {"missing_tools": missing, "workspace_tools": workspace},
                pool=pool,
                accept_any=accept_any,
                hints=hints,
                os_name=os_name,
                unreachable=far,
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
                shell_env = jobenv.job_env(passthrough, fixed, self.ctx.environ)
                row.shell_differs = runner.measure_tools(requires, shell_env) != (
                    missing,
                    workspace,
                )
            self.rows.append(row)
        self.judged = True
        if blind:
            self.undecided.append(
                "the server does not say which checks can run on this machine (older rcm) — "
                f"cannot judge {', '.join(blind)}"
            )
        if any("source_modes" in p for p in entries):
            self.git_presets = git_ref_presets(entries, self.rows)

    # ── 역할: coordinator · coordinator-worker ──

    def coordinator(self) -> None:
        from remote_ci_monitor.client import Client
        from remote_ci_monitor.config import (
            ConfigError,
            advertise_enabled,
            load_client_config,
            load_server_config,
        )

        try:
            cfg = load_server_config(self.opts.config, check_tools=False)
        except ConfigError as e:
            raise DoctorUsage(str(e)) from e
        env = self.service_env()
        self.measured_in = (
            "server_service_env" if self.role == dc.ROLE_COORDINATOR_WORKER else "none"
        )
        self.host_facts(env.get("PATH"))
        self.base_requirements()
        self.requirements.append(sqlite_requirement())
        self.requirements.append(fd_requirement(self._service_file))
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
        self.server.update(state="ok", version=dc.clean_text(health.get("version"), 64))
        ok, machines = self.reach(
            lambda: client.get_json("/api/machines"), "/api/machines", CLIENT_TOKEN_HINT
        )
        if not ok or not isinstance(machines, dict):
            return
        self.server["generation"] = dc.clean_text(machines.get("generation"), 64) or None
        listed = [m for m in machines.get("machines") or [] if isinstance(m, dict)]
        self.remote = [
            {
                "name": dc.clean_text(m.get("display_name") or m.get("id"), 64),
                "pool": dc.clean_text(m.get("pool"), 64),
                "alive": bool(m.get("alive")),
                "eligible": sum(
                    1
                    for v in (m.get("presets") or {}).values()
                    if isinstance(v, dict) and v.get("state") == "eligible"
                ),
                "presets": len(m.get("presets") or {}),
            }
            for m in listed
            if m.get("kind") != "local"
        ]
        if self.role == dc.ROLE_COORDINATOR_WORKER:
            local = next((m for m in listed if m.get("kind") == "local"), {})
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

    def local_lane(self, cfg: Any, health: Mapping[str, Any], local: Mapping[str, Any]) -> None:
        """로컬 레인은 서버가 자기 서비스 환경에서 잰 값으로 본다(`preset_env`, G7 · TD30)."""
        measured = health.get("preset_env") if isinstance(health.get("preset_env"), dict) else {}
        verdicts = local.get("presets") if isinstance(local.get("presets"), dict) else {}
        os_name = "darwin" if PLATFORM.startswith("darwin") else "linux"
        # 로컬 레인 행도 워커와 같은 `[[tools]]` 안내를 쓴다(위임 결정 E3)
        hints = dc.tool_hints(
            [{"name": t.name, "docs": t.docs, "install": dict(t.install)} for t in cfg.tools]
        )
        for p in cfg.presets:
            facts = measured.get(p.name) if isinstance(measured.get(p.name), dict) else {}
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
                {"missing_tools": facts.get("missing_tools") or []},
                pool=dc.DEFAULT_POOL,
                accept_any=bool(cfg.server.local_accept_any),
                hints=hints,
                os_name=os_name,
                unreachable={},  # 서버의 로컬 레인은 모든 점검을 돌린다(local_only 도)
            )
            verdict = verdicts.get(p.name)
            if isinstance(verdict, dict) and isinstance(verdict.get("code"), str):
                row.code = dc.clean_text(verdict["code"], 64)
            unset = [str(v) for v in facts.get("missing_passthrough") or []]
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
        version = dc.clean_text(health.get("version"), 64)
        self.server.update(state="ok", version=version)
        if version and version != __version__:
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


def run(opts: Options, ctx: Context) -> tuple[int, dict[str, Any] | None]:
    """(종료 코드, 문서). 사용법 오류면 (2, None) — 이유는 ctx.err 로 이미 나갔다."""
    try:
        role = opts.role
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
        r = _Run(opts, ctx, role)
        if role == dc.ROLE_WORKER:
            r.worker()
        elif role in (dc.ROLE_COORDINATOR, dc.ROLE_COORDINATOR_WORKER):
            r.coordinator()
        else:
            r.client()
        doc = r.document()
    except DoctorUsage as e:
        ctx.err(f"rcm: {e}")
        return dc.EXIT_USAGE, None
    if opts.json:
        ctx.out(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        for line in dc.render(doc):
            ctx.out(line)
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
