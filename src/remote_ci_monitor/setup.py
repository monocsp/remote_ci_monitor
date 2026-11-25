"""`rcm setup` — 이 머신의 역할을 고르고 설정 파일과 서비스 파일을 쓴다(M5t-a 결정 239~242,
docs/m5t-workplan.md §4.1).

묻고 · 쓰고 · 조회하는 쪽이다. 역할별 결정과 파일 내용은 `core/setup.py` 에 있다.

- **기존 `init` 을 조립한다.** 템플릿과 쓰는 함수는 cli 의 것(`read_template` ·
  `client_template` · `worker_template` · `write_private` · `worker_plist` · `worker_unit` ·
  `_this_rcm`)을 그대로 부른다.
- **덮지 않는다.** 있는 파일은 `--force config|service`(또는 `--force` 로 둘 다)를 준 것만 덮는다.
  쓸 파일을 모두 확인한 뒤에 쓰고, 중간에 실패하면 이미 쓴 파일을 되돌린다 — 반쪽 설정은 없다.
- **등록하지 않는다(⛔ Q9).** 서비스 파일은 `~/Library/LaunchAgents/` 가 아니라 설정 폴더 아래
  `service/` 에 쓰고, 복사 · 시작 명령만 찍는다. 같은 라벨이 로드돼 있는지는 읽기 전용
  조회(`launchctl print`, `systemctl --user is-active` · `is-enabled`)로만 본다.
- **Store 를 열지 않고 토큰을 만들지도 받지도 않는다.** 다른 빌드가 DB 를 열면 그 자리에서
  마이그레이션한다. `--reuse-data-dir` 의 DB 판은 헤더만 읽는다(6-B-V3).
- **묻는 것은** stdin 과 stderr 가 모두 TTY 이고 `--yes` 가 없을 때뿐이다(결정 240). 물을 사람이
  없으면 역할마다 꼭 줘야 하는 인자가 빠졌을 때 사용법 오류(종료 2)다.

종료 코드: 설정을 썼으면 0, 쓰지 못했으면 1, 사용법 오류 2, Ctrl-C 130.

cli 는 `register(subparsers)` 로 `rcm setup` 을 달고, 인자 없는 `rcm` 에서 `first_run()` 을
부른다(첫 실행 질문). `setup` 은 버전 따라가기를 하지 않는다(`follow.NO_FOLLOW_COMMANDS`).
"""

from __future__ import annotations

import argparse
import errno
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from remote_ci_monitor.core import doctor as dc
from remote_ci_monitor.core import setup as core

#: sqlite 파일의 첫 16바이트 — 헤더만 읽고 연결하지 않는다
SQLITE_MAGIC = b"SQLite format 3\x00"
DB_FILE = "rcm.sqlite3"
#: 서비스 관리자 조회의 시한(초)
QUERY_TIMEOUT = 5.0
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130

ROLE_HELP = {
    dc.ROLE_CLIENT: "sends jobs and reads their results",
    dc.ROLE_COORDINATOR: "queues jobs and hands them to other machines; runs none itself",
    dc.ROLE_WORKER: "runs the jobs a coordinator hands it",
    dc.ROLE_COORDINATOR_WORKER: "a coordinator that also runs jobs on this machine",
}


class SetupUsage(Exception):
    """사용법 오류 — 종료 2. 아무것도 쓰지 않았다."""


class SetupFailed(Exception):
    """설정을 쓰지 못했다 — 종료 1. 쓴 것은 되돌렸다."""


# ── 바깥 세계 ────────────────────────────────────────────────────────────────


def find_rcm() -> tuple[Path | None, bool]:
    """(서비스가 띄울 `rcm`, PATH 에서 찾았나). `cli._this_rcm()` 을 그대로 부른다 — 지금 도는
    실행 파일, 그다음 이 Python 옆의 `rcm`, 마지막이 PATH 다. 마지막으로 떨어졌으면 setup 을 돌린
    빌드가 아닐 수 있어 그렇다고 알린다(6-B-V6)."""
    from remote_ci_monitor import cli

    found = cli._this_rcm()
    if found is None:
        return None, False
    own: list[Path] = []
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.name == "rcm":
        own.append(argv0 if argv0.is_absolute() else Path.cwd() / argv0)
    own.append(Path(sys.executable).parent / "rcm")
    return found, found not in own


def addresses() -> list[str]:
    """이 머신의 비루프백 IPv4 — mDNS 광고와 같은 목록이다."""
    from remote_ci_monitor import mdns

    return mdns.local_ipv4s()


def is_local_address(address: str) -> bool:
    """이 머신의 인터페이스에 있는 주소인가 — 그 주소에 묶어 본다. `0.0.0.0` 은 모든 주소다."""
    if address in (core.ALL_ADDRESS, "::"):
        return True
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.bind((address, 0))
    except (OSError, UnicodeError):
        return False
    return True


def port_in_use(host: str, port: int) -> bool | None:
    """고른 주소와 포트에 묶어 본다(6-B-V9). 연결하지 않는다 — 다른 서버에 요청이 가지 않는다.
    `SO_REUSEADDR` 없이 묶으므로 다른 프로세스가 `0.0.0.0` 이든 그 주소든 그 포트를 잡고 있으면
    EADDRINUSE 다. 다른 이유로 못 묶으면 모른다(None)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.bind((host, port))
    except OSError as e:
        return True if e.errno == errno.EADDRINUSE else None
    return False


def _query(argv: list[str]) -> int | None:
    try:
        done = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=QUERY_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode


def service_loaded(manager: str, label: str) -> bool | None:
    """같은 라벨이 이미 로드돼 있나 — 읽기 전용 조회만 한다(TR36). 모르면 None."""
    if manager == core.LAUNCHD:
        exe = shutil.which("launchctl")
        if exe is None:
            return None
        code = _query([exe, "print", f"gui/{os.getuid()}/{label}"])
        return None if code is None else code == 0
    exe = shutil.which("systemctl")
    if exe is None:
        return None
    unit = core.unit_name(label)
    answers = [_query([exe, "--user", word, unit]) for word in ("is-active", "is-enabled")]
    if any(code == 0 for code in answers):
        return True
    return None if all(code is None for code in answers) else False


def host_facts() -> dict[str, Any]:
    from remote_ci_monitor import hostsample

    return hostsample.host_facts()


@dataclass
class World:
    """바깥 세계 — `cmd_setup` 이 진짜를 채우고 시험이 바꿔 끼운다."""

    platform: str = sys.platform
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    #: 물어도 되나 — stdin 과 stderr 가 모두 TTY 일 때(결정 240, doctor 와 같은 함수)
    interactive: Callable[[], bool] = field(default_factory=lambda: _is_tty)
    addresses: Callable[[], list[str]] = addresses
    is_local_address: Callable[[str], bool] = is_local_address
    port_in_use: Callable[[str, int], bool | None] = port_in_use
    service_loaded: Callable[[str, str], bool | None] = service_loaded
    host_facts: Callable[[], dict[str, Any]] = host_facts
    find_rcm: Callable[[], tuple[Path | None, bool]] = find_rcm


def _is_tty() -> bool:
    from remote_ci_monitor import doctor

    return doctor.is_tty()


# ── 명령줄 ───────────────────────────────────────────────────────────────────


def _add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--role", choices=dc.ROLES, help="the role this machine takes")
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="ask nothing; flags the role needs must be given",
    )
    p.add_argument("--server", metavar="URL", help="the coordinator's URL (client, worker)")
    p.add_argument("--by", metavar="LABEL", help="requester label in client.toml (client)")
    p.add_argument("--config", metavar="PATH", help="where to write the config file")
    p.add_argument("--pool", metavar="NAME", help="the worker's pool (worker)")
    p.add_argument("--lanes", type=int, metavar="N", help="parallel job lanes, 1-64 (worker)")
    p.add_argument(
        "--label",
        metavar="LABEL",
        help="service label (default: com.remote-ci-monitor.server or .worker)",
    )
    p.add_argument(
        "--service-file",
        dest="service_file",
        metavar="PATH",
        help="where to write the service file (default: <config dir>/service/)",
    )
    p.add_argument("--port", type=int, metavar="N", help="server port (coordinator roles)")
    p.add_argument(
        "--data-dir", dest="data_dir", metavar="PATH", help="server data_dir (coordinator roles)"
    )
    p.add_argument(
        "--advertise",
        dest="advertise",
        action="store_true",
        default=None,
        help="advertise the server over mDNS",
    )
    p.add_argument("--no-advertise", dest="advertise", action="store_false")
    p.add_argument(
        "--bind",
        metavar="ADDRESS",
        help="address the server listens on — an address of this machine or 0.0.0.0 "
        "(coordinator roles; required with --yes)",
    )
    p.add_argument(
        "--force",
        action="append",
        nargs="?",
        const="all",
        choices=("config", "service", "all"),
        help="replace an existing config file, service file, or both (no value)",
    )
    p.add_argument(
        "--reuse-data-dir",
        dest="reuse_data_dir",
        action="store_true",
        help="use a data_dir that already holds rcm.sqlite3",
    )


#: 줄인 옵션(`--conf` · `--data`)을 받지 않는다. guard(tools/guard_production.py)는 정확한 이름만
#: 읽는다 — argparse 가 줄인 이름을 받으면 guard 가 못 본 경로에 쓴다(P3 v1-1 · v2-2)
PARSER_OPTIONS: dict[str, Any] = {"allow_abbrev": False}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rcm setup",
        description="Set this machine up for one role: config and service file.",
        **PARSER_OPTIONS,
    )
    _add_arguments(p)
    return p


def register(subparsers: Any) -> argparse.ArgumentParser:
    """cli 의 하위 명령 목록에 `setup` 을 단다(`cli.build_parser` 가 부른다)."""
    p = subparsers.add_parser(
        "setup", help="set this machine up as a client, worker or coordinator", **PARSER_OPTIONS
    )
    _add_arguments(p)
    p.set_defaults(func=cmd_setup)
    return p


# ── 역할 읽어 내기 ───────────────────────────────────────────────────────────


def existing_roles(environ: Mapping[str, str]) -> list[str]:
    """있는 설정에서 역할을 읽어 낸다(결정 239) — doctor 와 같은 함수다. `$RCM_CONFIG` 가 없는
    파일을 가리키면 사용법 오류다."""
    from remote_ci_monitor import doctor

    try:
        return doctor.infer_roles(environ)
    except doctor.DoctorUsage as e:
        raise SetupUsage(str(e)) from e


def db_header_version(db: Path) -> int | None:
    """sqlite 헤더의 user_version(오프셋 60, 4바이트 big-endian). 연결하지 않는다 — 연결은 그
    자체로 `-wal` · `-shm` 을 만든다. 없거나 sqlite 가 아니거나 `-wal` 에 새 판이 있을 수 있으면
    None."""
    try:
        with db.open("rb") as fh:
            header = fh.read(100)
    except OSError:
        return None
    if len(header) < 64 or not header.startswith(SQLITE_MAGIC):
        return None
    try:
        if db.with_name(db.name + "-wal").stat().st_size > 0:
            return None
    except OSError:
        pass
    return int.from_bytes(header[60:64], "big")


# ── 묻기 ─────────────────────────────────────────────────────────────────────


class _Ask:
    """공용 질문 — 묻는 말은 stderr, 답은 stdin 한 줄. 빈 답은 기본값, 답이 끝나면(EOF) 사용법
    오류다."""

    def __init__(self, world: World):
        self.inp, self.err = world.stdin, world.stderr

    def line(self, question: str, default: str | None = None) -> str:
        shown = f" [{default}]" if default else ""
        self.err.write(f"{question}{shown}: ")
        self.err.flush()
        raw = self.inp.readline()
        if raw == "":
            self.err.write("\n")
            raise SetupUsage("no answer to read — pass the values as flags with --yes")
        return raw.strip() or (default or "")

    def yes_no(self, question: str, default: bool) -> bool:
        while True:
            answer = self.line(f"{question} (y/n)", "y" if default else "n").lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("answer y or n")

    def number(self, question: str, default: int, low: int, high: int) -> int:
        while True:
            answer = self.line(question, str(default))
            if answer.isdigit() and low <= int(answer) <= high:
                return int(answer)
            self.say(f"a number from {low} to {high}")

    def say(self, text: str) -> None:
        self.err.write(f"  {text}\n")
        self.err.flush()


def _ask_role(ask: _Ask) -> str:
    ask.say("Which role does this machine take?")
    for i, role in enumerate(dc.ROLES, 1):
        ask.say(f"{i}) {role} — {ROLE_HELP[role]}")
    while True:
        answer = ask.line("role")
        if answer.isdigit() and 1 <= int(answer) <= len(dc.ROLES):
            return dc.ROLES[int(answer) - 1]
        if answer in dc.ROLES:
            return answer
        ask.say(f"one of 1-{len(dc.ROLES)} or a role name")


def _ask_bind(ask: _Ask, world: World) -> str:
    choices = core.bind_choices(world.addresses())
    ask.say("Which address should the server listen on?")
    for i, (address, kind) in enumerate(choices, 1):
        tag = " (recommended)" if kind == core.TAILSCALE else ""
        ask.say(f"{i}) {address} — {kind}{tag}")
    ask.say("or type an address of this machine")
    while True:
        answer = ask.line("bind", choices[0][0] if choices else None)
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1][0]
        if answer and world.is_local_address(answer):
            return answer
        ask.say(f"{answer or '(empty)'} is not an address of this machine")


# ── 계획 ─────────────────────────────────────────────────────────────────────


@dataclass
class Planned:
    path: Path
    data: bytes
    mode: int
    kind: str  # "config" | "service"


@dataclass
class Plan:
    role: str
    config: Path
    files: list[Planned] = field(default_factory=list)
    manager: str | None = None
    service: Path | None = None
    installed: Path | None = None
    label: str | None = None
    log: Path | None = None
    rcm: Path | None = None
    warnings: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)


def _manager(platform: str) -> str | None:
    if platform.startswith("darwin"):
        return core.LAUNCHD
    if platform.startswith("linux"):
        return core.SYSTEMD
    return None


def _url(raw: str) -> str:
    """코디네이터 URL — `init client` · `init worker` 와 같은 규칙. setup 은 `auto` 를 쓰지 않는다
    (§9: 같은 네트워크에서 답하는 아무 서버나 집는다)."""
    url = raw.strip().rstrip("/")
    if url.lower() == "auto":
        raise SetupUsage(
            '--server must be the coordinator\'s URL — setup does not write server = "auto"'
        )
    if not url.startswith(("http://", "https://")):
        raise SetupUsage("--server must be a URL starting with http:// or https://")
    if any(c in '"\\' or c.isspace() for c in url):
        raise SetupUsage("--server must not contain quotes, backslashes or whitespace")
    return url


def _abs(raw: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(raw)))


def _plan(args: argparse.Namespace, world: World) -> Plan:
    from remote_ci_monitor.config import user_config_dir

    roles_here = existing_roles(os.environ)
    asking = not args.yes and world.interactive()
    ask = _Ask(world)
    role = args.role
    if role is None:
        if not asking:
            raise SetupUsage(f"pass --role ROLE ({', '.join(dc.ROLES)}) — there is no one to ask")
        role = _ask_role(ask)
    if not asking:
        given = {"server": args.server, "pool": args.pool, "lanes": args.lanes, "bind": args.bind}
        missing = core.missing_for_yes(role, given)
        if missing:
            raise SetupUsage(f"--role {role} without questions needs {' '.join(missing)}")
    if args.port is not None and not 1 <= args.port <= 65535:
        raise SetupUsage(f"--port must be 1-65535 (got {args.port})")
    if args.lanes is not None and not 1 <= args.lanes <= 64:
        raise SetupUsage(f"--lanes must be 1-64 (got {args.lanes})")
    if args.label is not None and (problem := core.label_problem(args.label)):
        raise SetupUsage(problem)
    config_dir = user_config_dir().absolute()
    if role == dc.ROLE_CLIENT:
        return _client(args, ask if asking else None, config_dir)
    if role == dc.ROLE_WORKER:
        plan = _worker(args, ask if asking else None, config_dir)
    else:
        plan = _server(args, ask if asking else None, world, role, config_dir, roles_here)
    _service(args, ask if asking else None, world, plan)
    return plan


def _label(args: argparse.Namespace, ask: _Ask | None, role: str) -> str:
    default = core.default_label(role) or ""
    if args.label is not None:
        return args.label
    if ask is None:
        return default
    while True:
        label = ask.line("service label", default)
        problem = core.label_problem(label)
        if problem is None:
            return label
        ask.say(problem)


def _client(args: argparse.Namespace, ask: _Ask | None, config_dir: Path) -> Plan:
    from remote_ci_monitor import cli

    raw = args.server if args.server is not None else ask.line("coordinator URL") if ask else ""
    url = _url(raw)
    by = args.by
    if by is None and ask is not None:
        by = ask.line("your name in the queue (empty: user@host)", "")
    if by and any(c in "\r\n" for c in by):
        raise SetupUsage("--by must not contain line breaks")
    text = cli.client_template(url).decode("utf-8")
    if by:
        text = core.set_value(text, "label", core.toml_str(by))
    path = _abs(args.config) if args.config else config_dir / "client.toml"
    plan = Plan(role=dc.ROLE_CLIENT, config=path)
    # 토큰이 들어갈 수 있는 파일이라 600 이다(`init client` 와 같다)
    plan.files.append(Planned(path, text.encode("utf-8"), 0o600, "config"))
    plan.tokens = [
        "on the coordinator, make a client token: rcm token add CLIENT_NAME",
        f'then: export RCM_TOKEN=… with that token (or token = "…" in {path}, mode 600)',
    ]
    return plan


def _worker(args: argparse.Namespace, ask: _Ask | None, config_dir: Path) -> Plan:
    from remote_ci_monitor import cli

    raw = args.server if args.server is not None else ask.line("coordinator URL") if ask else ""
    url = _url(raw)
    pool = args.pool
    if pool is None and ask is not None:
        pool = ask.line("pool", cli._short_hostname())
    pool = pool or cli._short_hostname()
    if not cli._WORKER_NAME_RE.match(pool):
        raise SetupUsage(f"--pool must be letters, digits, '.', '_' or '-' (got {pool!r})")
    lanes = args.lanes
    if lanes is None:
        lanes = ask.number("lanes", 1, 1, 64) if ask is not None else 1
    path = _abs(args.config) if args.config else config_dir / "worker.toml"
    plan = Plan(role=dc.ROLE_WORKER, config=path)
    plan.files.append(Planned(path, cli.worker_template(url, pool, lanes, None), 0o600, "config"))
    plan.tokens = [
        "on the coordinator, make a worker token: rcm token add WORKER_NAME --worker",
        f'put it in {path} as token = "…" (mode 600) — the service does not see '
        "RCM_WORKER_TOKEN from your shell",
    ]
    return plan


def _server(
    args: argparse.Namespace,
    ask: _Ask | None,
    world: World,
    role: str,
    config_dir: Path,
    roles_here: list[str],
) -> Plan:
    from remote_ci_monitor import cli
    from remote_ci_monitor.store import DB_VERSION

    template = cli.read_template("server.toml").decode("utf-8")
    if args.bind is not None:
        bind = args.bind.strip()
        if not world.is_local_address(bind):
            raise SetupUsage(
                f"--bind {bind} is not an address of this machine — use one of its interface "
                "addresses or 0.0.0.0"
            )
    elif ask is not None:
        bind = _ask_bind(ask, world)
    else:  # missing_for_yes 가 이미 막았다
        raise SetupUsage(f"--role {role} needs --bind")
    kind = core.bind_kind(bind)
    default_port = int(core.template_value(template, "port") or 8787)
    port = args.port
    if port is None:
        port = ask.number("port", default_port, 1, 65535) if ask is not None else default_port
    default_data = str(_abs(core.template_value(template, "data_dir") or "~/.local/share/rcm"))
    raw = args.data_dir
    if raw is None:
        raw = ask.line("data_dir", default_data) if ask is not None else default_data
    data_dir = _abs(raw)
    warnings: list[str] = []
    advertise: bool | None
    if kind == core.TAILSCALE:
        advertise = False
        if args.advertise:
            warnings.append("--advertise is ignored on a Tailscale address (advertise = false)")
    elif args.advertise is not None:
        advertise = args.advertise
    elif ask is not None:
        advertise = ask.yes_no(
            "advertise this server over mDNS so machines on this network find it",
            core.advertise_default(kind),
        )
    else:
        advertise = None  # 템플릿의 기본 규칙(루프백이 아니면 켬)
    label = _label(args, ask, role)
    lanes = 0 if role == dc.ROLE_COORDINATOR else int(core.template_value(template, "lanes") or 1)
    path = _abs(args.config) if args.config else config_dir / "server.toml"
    text = core.server_toml(
        template, bind=bind, port=port, data_dir=str(data_dir), lanes=lanes, advertise=advertise
    )
    plan = Plan(role=role, config=path, label=label, warnings=warnings)
    plan.files.append(Planned(path, text.encode("utf-8"), 0o644, "config"))
    db = data_dir / DB_FILE
    if db.exists():
        if not args.reuse_data_dir:
            raise SetupFailed(
                f"{data_dir} already holds an rcm database ({DB_FILE}) — choose a new data_dir, "
                "or pass --reuse-data-dir to keep using it"
            )
        note = core.db_version_note(db_header_version(db), DB_VERSION)
        if note:
            warnings.append(note)
    held = world.port_in_use(core.ALL_ADDRESS if kind == core.ALL else bind, port)
    if held:
        warnings.append(
            f"port {port} is in use on this machine — the server does not start while "
            "something else holds it"
        )
    warnings.extend(core.bind_warnings(kind))
    if role == dc.ROLE_COORDINATOR_WORKER and dc.ROLE_WORKER in roles_here:
        warnings.append(
            "this machine already has worker.toml — an rcm worker next to a coordinator-worker "
            "runs two sets of lanes on one host; that is only needed for a second worker"
        )
    return plan


def _service(args: argparse.Namespace, ask: _Ask | None, world: World, plan: Plan) -> None:
    """서비스 파일 하나를 계획에 더한다 — 설정 폴더 아래 `service/` 에, 등록하지 않는다."""
    from remote_ci_monitor import cli

    if plan.label is None:
        plan.label = _label(args, ask, plan.role)
    manager = _manager(world.platform)
    if manager is None:
        plan.warnings.append(
            f"no launchd or systemd on {world.platform} — no service file; start "
            f"`rcm {'serve' if plan.role in core.SERVER_ROLES else 'worker'} --config "
            f"{plan.config}` yourself"
        )
        return
    rcm, via_path = world.find_rcm()
    if rcm is None:
        raise SetupFailed(
            "cannot find the rcm executable this command runs from — the service file would "
            "have nothing to start"
        )
    if via_path:
        plan.warnings.append(
            f"the service file runs {rcm}, the rcm found on PATH — check that it is the build "
            "you want the service to run"
        )
    home = Path.home()
    label = plan.label
    name = core.service_file_name(label, manager)
    staged = _abs(args.service_file) if args.service_file else plan.config.parent / "service" / name
    log = core.log_dir(manager, home) / core.log_file_name(label)
    if plan.role in core.SERVER_ROLES:
        render = core.server_plist if manager == core.LAUNCHD else core.server_unit
        data = render(
            rcm=rcm,
            config=plan.config,
            home=home,
            label=label,
            log=log,
            path=cli.WORKER_SERVICE_PATH,
        )
        plan.tokens = core.token_commands(rcm, plan.config)
    elif manager == core.LAUNCHD:
        doc = plistlib.loads(cli.worker_plist(rcm, plan.config, home))
        doc["Label"] = label
        doc["StandardOutPath"] = doc["StandardErrorPath"] = str(log)
        data = plistlib.dumps(doc)
    else:
        data = cli.worker_unit(rcm, plan.config, home)
    plan.files.append(Planned(staged, data, 0o644, "service"))
    plan.manager, plan.service, plan.rcm, plan.log = manager, staged, rcm, log
    plan.installed = core.install_path(label, manager, home)
    if world.service_loaded(manager, label):
        plan.warnings.append(
            f"a service labelled {label} is already loaded — setup leaves it alone; the copy "
            "below does not replace it (pass --label to set up another one)"
        )


def _check_free(args: argparse.Namespace, plan: Plan) -> None:
    forced = set(args.force or ())
    if "all" in forced:
        forced |= {"config", "service"}
    blocked = [f for f in plan.files if f.path.exists() and f.kind not in forced]
    if blocked:
        raise SetupFailed(
            "; ".join(f"{f.path} exists — pass --force {f.kind} to replace it" for f in blocked)
            + " (nothing was written)"
        )


def _write(plan: Plan) -> None:
    """모두 쓰거나 아무것도 쓰지 않는다. 중간에 실패하면 이미 쓴 파일을 되돌린다 — 새로 만든
    파일은 지우고 덮은 파일은 옛 내용과 권한으로 돌린다(TR7)."""
    from remote_ci_monitor import cli

    done: list[tuple[Planned, bytes | None, int]] = []
    current: Planned | None = None
    try:
        for current in plan.files:
            old = current.path.read_bytes() if current.path.is_file() else None
            mode = current.path.stat().st_mode & 0o777 if old is not None else current.mode
            cli.write_private(current.path, current.data, current.mode)
            done.append((current, old, mode))
    except BaseException as e:
        # Ctrl-C 도 되돌린다 — 반쪽 설정을 남기지 않는다
        for written, old, mode in reversed(done):
            try:
                if old is None:
                    written.path.unlink(missing_ok=True)
                else:
                    cli.write_private(written.path, old, mode)
            except OSError:
                pass
        if not isinstance(e, OSError):
            raise
        where = current.path if current is not None else "?"
        raise SetupFailed(f"cannot write {where}: {e.strerror or e} — nothing was written") from e


# ── 마지막: 서버 없이 도는 진단 ──────────────────────────────────────────────


def _config_row(plan: Plan) -> dict[str, Any]:
    from remote_ci_monitor import doctor
    from remote_ci_monitor.config import (
        ConfigError,
        load_client_config,
        load_server_config,
        load_worker_config,
    )

    try:
        if plan.role in core.SERVER_ROLES:
            load_server_config(plan.config, environ={}, check_tools=False)
        elif plan.role == dc.ROLE_WORKER:
            load_worker_config(plan.config, environ={})
        else:
            load_client_config(plan.config, environ={})
    except ConfigError as e:
        return doctor.requirement("config", "fail", str(e), "this role")
    return doctor.requirement("config", "ok", f"{plan.config} reads", "this role")


def advice(plan: Plan, world: World) -> list[str]:
    """rcm 자신의 요구 · 호스트 사실 · 설정 파일 검증 — 서버에 묻지 않고 DB 도 열지 않는다. 조언
    일 뿐 종료 코드에 들지 않는다(§4.1 「마지막」)."""
    from remote_ci_monitor import doctor

    facts = world.host_facts()
    platform = {
        "platform": world.platform,
        "package_manager": facts.get("package_manager"),
        "root": bool(facts.get("root")),
    }
    rows = [
        doctor.python_requirement(
            doctor.python_version(),
            hasattr(tarfile, "data_filter"),
            who="the Python that ran rcm setup",
            **platform,
        )
    ]
    if plan.role in core.SERVER_ROLES:
        rows.append(doctor.sqlite_requirement())
        sf = None
        if plan.service is not None:
            try:
                user_home = str(Path.home()) if plan.manager == core.SYSTEMD else None
                sf = doctor.read_service_file(plan.service, user_home=user_home)
            except doctor.ServiceFileUnreadable:
                sf = None
        rows.append(doctor.fd_requirement(sf))
    rows.append(_config_row(plan))
    host = " · ".join(
        str(facts[k]) for k in ("os", "os_release", "arch", "package_manager") if facts.get(k)
    )
    lines = [f"  host: {host or '—'}"]
    for r in rows:
        mark = dc.REQ_MARK.get(r["state"], r["state"])
        hint = f" — {r['install']}" if r.get("install") else ""
        lines.append(f"  {mark:<4} {r['name']}: {r['detail']}{hint}")
    return lines


def _report(plan: Plan, world: World) -> None:
    out, err = world.stdout, world.stderr
    for f in plan.files:
        out.write(f"{f.path}\n")
    out.flush()
    for w in plan.warnings:
        err.write(f"warning: {w}\n")
    if plan.service is not None and plan.installed is not None and plan.manager is not None:
        err.write("next — install and start the service yourself (setup registers nothing):\n")
        for line in core.install_commands(
            plan.manager,
            staged=plan.service,
            installed=plan.installed,
            label=plan.label or "",
            log=plan.log or plan.service,
        ):
            err.write(f"  {line}\n")
        err.write(
            "  (cp -n keeps a file already there; the start command runs only when the "
            "installed file is the one setup wrote)\n"
        )
        if plan.manager == core.SYSTEMD:
            err.write("  to keep it running after you log out: sudo loginctl enable-linger $USER\n")
    if plan.tokens:
        if plan.role in core.SERVER_ROLES:
            err.write("tokens — once the server runs, with the same build as the service:\n")
        else:
            err.write("token:\n")
        for line in plan.tokens:
            err.write(f"  {line}\n")
    err.write("advice — checks that need no server (they do not change the exit code):\n")
    for line in advice(plan, world):
        err.write(f"{line}\n")
    if plan.role == dc.ROLE_CLIENT:
        err.write(f"then: add your token and run `{core.doctor_command(plan.role)}`\n")
    else:
        err.write(
            f"then: start the service, add a token, and run `{core.doctor_command(plan.role)}`\n"
        )
    err.flush()


# ── 진입점 ───────────────────────────────────────────────────────────────────


def run(args: argparse.Namespace, world: World) -> int:
    try:
        plan = _plan(args, world)
        _check_free(args, plan)
        _write(plan)
    except SetupUsage as e:
        world.stderr.write(f"rcm: {e}\n")
        return EXIT_USAGE
    except SetupFailed as e:
        world.stderr.write(f"rcm: {e}\n")
        return EXIT_FAILED
    except KeyboardInterrupt:
        world.stderr.write("\nrcm: interrupted — nothing was written\n")
        return EXIT_INTERRUPTED
    _report(plan, world)
    return EXIT_OK


#: 인자 없는 `rcm` 이 설정 없는 터미널에서 묻는 말(결정 240)
FIRST_RUN_QUESTION = "No rcm config on this machine yet. Set it up now?"


def first_run(world: World | None = None, environ: Mapping[str, str] | None = None) -> int | None:
    """인자 없는 `rcm`(결정 240, §4.1 「첫 실행」). stdin 과 stderr 가 모두 TTY 이고 역할을 읽어 낼
    설정이 하나도 없을 때만 설정을 시작할지 묻는다. 예면 대화형 setup 을 돌려 그 종료 코드를
    돌려준다. None 이면 지금처럼 사용법 오류(종료 2)로 간다 — 물을 사람이 없을 때, 설정이 있을
    때(환경변수만 쓰는 클라이언트 · 워커 포함), `$RCM_CONFIG` 가 없는 파일을 가리킬 때, 아니오나
    답이 없을 때다.

    설정은 새로 찾지 않고 `existing_roles`(doctor 와 같은 `infer_roles` → `find_server_config` ·
    `find_client_config` · `user_config_dir()/worker.toml`)로 본다. TTY 가 아니면 설정 파일도 읽지
    않는다."""
    world = World() if world is None else world
    environ = os.environ if environ is None else environ
    if not world.interactive():
        return None
    try:
        roles = existing_roles(environ)
    except SetupUsage as e:
        world.stderr.write(f"rcm: {e}\n")
        return None
    if roles:
        return None
    ask = _Ask(world)
    try:
        wanted = ask.yes_no(FIRST_RUN_QUESTION, True)
    except SetupUsage:
        return None
    except KeyboardInterrupt:
        world.stderr.write("\nrcm: interrupted\n")
        return EXIT_INTERRUPTED
    if not wanted:
        return None
    return run(build_parser().parse_args([]), world)


def cmd_setup(args: argparse.Namespace) -> int:
    """`rcm setup` — 진짜 바깥 세계로 돈다."""
    return run(args, World())


def main(argv: list[str] | None = None) -> int:
    return cmd_setup(build_parser().parse_args(argv))
