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
- **운영을 스스로 알아본다(결정 352).** 계획을 마친 뒤와 쓰기 직전에 이 머신의 rcm 서비스
  (`scan_services`)를 찾아, 쓸 자리가 그 서비스의 설정 · data_dir · venv · 체크아웃이거나 그
  안이면, 또는 라벨이 같으면 거절한다(`core.production_refusal`, 종료 1, 우회 플래그 없음).
  훅(`tools/guard_production.py`)은 셸 글을 판정하는 1차 방어라 바깥이 남는다 — 그 바깥을 여기서
  막는다.
- **Store 를 열지 않고 토큰을 만들지도 받지도 않는다.** 다른 빌드가 DB 를 열면 그 자리에서
  마이그레이션한다. `--reuse-data-dir` 의 DB 판은 헤더만 읽는다(6-B-V3).
- **묻는 것은** stdin 과 stderr 가 모두 TTY 이고 `--yes` 가 없을 때뿐이다(결정 240). 물을 사람이
  없으면 역할마다 꼭 줘야 하는 인자가 빠졌을 때 사용법 오류(종료 2)다.

종료 코드: 설정을 썼으면 0(다 쓴 뒤 보고 중의 Ctrl-C 도), 쓰지 못했으면 1, 사용법 오류 2, 쓰기 전 ·
쓰는 중의 Ctrl-C 130.

cli 는 `register(subparsers)` 로 `rcm setup` 을 달고, 인자 없는 `rcm` 에서 `first_run()` 을
부른다(첫 실행 질문). `setup` 은 버전 따라가기를 하지 않는다(`follow.NO_FOLLOW_COMMANDS`).
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tomllib
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from remote_ci_monitor.core import doctor as dc
from remote_ci_monitor.core import setup as core

#: sqlite 파일의 첫 16바이트 — 헤더만 읽고 연결하지 않는다
SQLITE_MAGIC = b"SQLite format 3\x00"
DB_FILE = "rcm.sqlite3"
#: systemd 사용자 서비스의 선행 조건 — 로그인 없이도 user manager 가 돌게 한다
LINGER_LINE = (
    "first, if no user manager runs for you here (a headless machine, or once you log out): "
    "sudo loginctl enable-linger $USER"
)
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


def _query(argv: list[str]) -> tuple[int, str] | None:
    """(종료 코드, stdout + stderr). 실행하지 못했거나 시한을 넘기면 None."""
    try:
        done = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=QUERY_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode, (done.stdout or "") + (done.stderr or "")


#: `launchctl print` 이 그 라벨이 없다고 답하는 말 — 이 Mac(macOS 26)에서 잰 값은 종료 113 과
#: `Could not find service "<label>" in domain for user gui: <uid>` 다. 도메인이 없으면 112 와
#: `Could not find domain …`, 권한이 없으면 1 과 `Could not print domain …` 이다 — 그쪽은 모른다
LAUNCHD_NO_SERVICE = "Could not find service"
#: `systemctl is-active` 의 「active 아님」 — systemctl(1) 「Exit status」 표의 3(LSB 의 「program
#: is not running」). 버스에 닿지 못하면(`Failed to connect to bus`) 1 이다 — 그쪽은 모른다
SYSTEMD_INACTIVE = 3
#: 3 과 함께 stdout 이 이 낱말일 때만 「없음」이다. `activating` · `reloading` · `deactivating`
#: 은 그 유닛이 있다는 뜻이라 모른다(P6 재확인 4 · v1-2). 없는 유닛은 `inactive` 다
SYSTEMD_ABSENT_STATES = frozenset({"inactive", "unknown"})
#: 3 과 함께 이 낱말이면 유닛이 있다 — 실패한 유닛은 `reset-failed` 전까지 올라가 있다(P6 재확인
#: v1-7). 없는 유닛은 `failed` 가 아니라 `inactive` 다
SYSTEMD_FAILED = "failed"


def service_loaded(manager: str, label: str) -> bool | None:
    """같은 라벨이 이미 로드돼 있나 — 읽기 전용 조회만 한다(TR36). 0 아닌 종료가 모두
    「없음」은 아니다(P5 A-E2): 서비스 관리자가 「그런 서비스는 없다」고 답한 것만 False 이고,
    도메인 · 버스에 닿지 못한 것은 모른다(None)."""
    if manager == core.LAUNCHD:
        exe = shutil.which("launchctl")
        if exe is None:
            return None
        answer = _query([exe, "print", f"gui/{os.getuid()}/{label}"])
        if answer is None:
            return None
        code, text = answer
        if code == 0:
            return True
        return False if LAUNCHD_NO_SERVICE in text else None
    exe = shutil.which("systemctl")
    if exe is None:
        return None
    unit = core.unit_name(label)
    answers = [_query([exe, "--user", word, unit]) for word in ("is-active", "is-enabled")]
    if any(answer is not None and answer[0] == 0 for answer in answers):
        return True
    active = answers[0]
    if active is None or active[0] != SYSTEMD_INACTIVE:
        return None
    state = active[1].strip().split("\n", 1)[0].strip()
    if state == SYSTEMD_FAILED:
        return True
    return False if state in SYSTEMD_ABSENT_STATES else None


# ── 운영 알아보기(결정 352) ──────────────────────────────────────────────────

#: setup 이 rcm 서비스를 찾는 시스템 자리 — launchd 의 모든 사용자 자리와 데몬 자리, systemd 의
#: 시스템 유닛과 모든 사용자의 사용자 유닛. 시험이 바꿔 끼운다
SYSTEM_SERVICE_PLACES: tuple[Path, ...] = (
    Path("/Library/LaunchAgents"),
    Path("/Library/LaunchDaemons"),
    Path("/etc/systemd/system"),
    Path("/etc/systemd/user"),
)
#: root 가 읽고 `User=` · `UserName` 으로 띄우는 자리 — 그 유닛의 `%h` · `~` 는 이 사용자의 홈이
#: 아니다
_ROOT_PLACES = ("LaunchDaemons", "system")
#: 읽지 못한 서비스 파일이 rcm 의 것일 수 있나 — 바이트에 rcm 실행 파일 · 패키지 이름이 있다
_RCM_BYTES = re.compile(rb"(?<![\w.-])rcm(?![\w.-])|remote_ci_monitor|remote-ci-monitor")
#: 서비스 하위 명령마다 data_dir 을 읽는 절과 그 환경 변수 · 기본값(config.py 와 같다)
_DATA_DIRS = {
    "serve": ("server", "RCM_SERVER_DATA_DIR", "~/.local/share/rcm"),
    "worker": ("worker", "RCM_WORKER_DATA_DIR", "~/.local/share/rcm-worker"),
}


def account_home() -> Path | None:
    """이 사용자의 passwd 홈 — `$HOME` 이 다른 곳을 가리켜도 그 계정의 서비스가 있는 자리다
    (batch 10 H3). passwd 를 읽지 못하면 None. 시험이 바꿔 끼운다."""
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return None


def real_path(path: Path) -> Path:
    """심볼릭 링크를 푼 경로 — 있는 조상까지 `realpath` 로 풀고 없는 꼬리는 그대로 붙인다."""
    head = Path(os.path.abspath(path))
    tail: list[str] = []
    while not os.path.lexists(head) and head != head.parent:
        tail.append(head.name)
        head = head.parent
    return Path(os.path.realpath(head)).joinpath(*reversed(tail))


def service_places(home: Path, environ: Mapping[str, str]) -> list[tuple[Path, bool]]:
    """(자리, root 자리인가). 사용자 자리(`~/Library/LaunchAgents`, `~/.config/systemd/user`,
    `$XDG_CONFIG_HOME/systemd/user`)와 시스템 자리(`SYSTEM_SERVICE_PLACES`)다."""
    user = [home / "Library" / "LaunchAgents", home / ".config" / "systemd" / "user"]
    xdg = environ.get("XDG_CONFIG_HOME")
    if xdg and os.path.isabs(xdg):
        user.append(Path(xdg) / "systemd" / "user")
    out = [(place, False) for place in user]
    out += [(place, place.name in _ROOT_PLACES) for place in SYSTEM_SERVICE_PLACES]
    return out


def _editable_checkout(venv: Path) -> Path | None:
    """venv 가 editable 로 설치한 rcm 소스 폴더(`direct_url.json`). 없거나 못 읽으면 None."""
    for info in venv.glob("lib/python*/site-packages/remote_ci_monitor-*.dist-info"):
        try:
            record = json.loads((info / "direct_url.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not (record.get("dir_info") or {}).get("editable"):
            continue
        url = str(record.get("url", ""))
        if url.startswith("file://"):
            return real_path(Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)))
    return None


def _venv_of(program: str | None, env: Mapping[str, str]) -> Path | None:
    """서비스가 실행하는 파일의 venv — 심볼릭 링크(`~/.local/bin/rcm`)를 푼 `…/bin/x` 의 위에
    `pyvenv.cfg` 가 있으면 그 폴더. 이름만 적혀 있으면 서비스의 PATH 로 찾는다."""
    if not program:
        return None
    found = program if os.path.isabs(program) else shutil.which(program, path=env.get("PATH"))
    if not found:
        return None
    venv = Path(os.path.realpath(found)).parent.parent
    return venv if (venv / "pyvenv.cfg").is_file() else None


def _service_path(raw: str, *, home: str | None, workdir: str) -> Path | None:
    """서비스가 받은 경로 글자 하나를 그 서비스의 눈으로 푼다 — `~` 는 그 서비스의 홈, 상대
    경로는 작업 폴더 기준. 풀 수 없으면(홈을 모르는 `~`, `~user`) None."""
    if raw == "~" or raw.startswith("~/"):
        if not home:
            return None
        raw = home.rstrip("/") + raw[1:]
    elif raw.startswith("~"):
        return None
    return real_path(Path(os.path.join(workdir, raw)))


def _rcm_service(path: Path, *, home: Path, root: bool) -> core.RcmService | None:
    """서비스 파일 하나 → rcm 서비스의 사실. rcm 의 서비스가 아님이 확실하면 None."""
    from remote_ci_monitor import doctor

    file = real_path(path)
    stem = path.name.rsplit(".", 1)[0]
    data: bytes | None = None
    try:
        data = path.read_bytes()
        sf = doctor.read_service_file(path, user_home=None if root else str(home), system=root)
    except (OSError, doctor.ServiceFileUnreadable) as e:
        named = doctor.looks_like_rcm(doctor.ServiceFile(path=path, label=stem))
        if named or (data is not None and _RCM_BYTES.search(data)):
            if isinstance(e, doctor.ServiceFileUnreadable):
                reason = e.reason
            else:
                reason = e.strerror or type(e).__name__
            return core.RcmService(stem, file, (stem,), problem=f"setup cannot read it ({reason})")
        return None
    name = sf.label if path.suffix == ".plist" and sf.label else stem
    names = tuple(dict.fromkeys(n for n in (sf.label, stem) if n))

    def problem(why: str) -> core.RcmService:
        return core.RcmService(name, file, names, data=data, problem=why)

    if sf.launch is None:
        if not doctor.looks_like_rcm(sf):
            return None
        return problem("setup cannot tell which rcm command it starts")
    if sf.launch_unexpanded:
        return problem("its command line has a systemd specifier setup does not expand")
    service_home = sf.env.get("HOME") or (None if root else str(home))
    if sf.workdir is not None and os.path.isabs(sf.workdir):
        workdir = sf.workdir
    elif sf.workdir_unknown:
        return problem("setup cannot tell its working directory")
    else:  # launchd 와 시스템 유닛은 `/`, systemd 사용자 유닛은 그 사용자의 홈에서 돈다
        workdir = "/" if path.suffix == ".plist" or root else (service_home or "/")
    venv = _venv_of(sf.program, sf.env)
    checkout = _editable_checkout(venv) if venv is not None else None
    command = sf.launch.get("command")
    raw_config = sf.launch.get("config") or sf.env.get("RCM_CONFIG")
    config = None
    if raw_config:
        config = _service_path(str(raw_config), home=service_home, workdir=workdir)
        if config is None:
            return problem(f"setup cannot resolve its config path {raw_config}")
    data_dir = None
    if command in _DATA_DIRS:
        if config is None:
            return problem("it names no --config, so setup cannot tell which config it reads")
        section, env_name, default = _DATA_DIRS[command]
        try:
            with config.open("rb") as fh:
                doc = tomllib.load(fh)
        except (OSError, ValueError) as e:
            why = getattr(e, "strerror", None) or type(e).__name__
            return problem(f"setup cannot read its config {config} ({why})")
        table = doc.get(section)
        declared = table.get("data_dir") if isinstance(table, dict) else None
        raw_data = sf.env.get(env_name) or (declared if isinstance(declared, str) else default)
        data_dir = _service_path(raw_data, home=service_home, workdir=workdir)
        if data_dir is None:
            return problem(f"setup cannot resolve its data_dir {raw_data}")
    return core.RcmService(name, file, names, config, data_dir, venv, checkout, data)


def scan_services(
    home: Path | None = None, environ: Mapping[str, str] | None = None
) -> list[core.RcmService]:
    """이 머신에서 찾을 수 있는 rcm 서비스 — 지원하는 자리의 `*.plist` · `*.service` 만 본다
    (결정 352). 설정은 TOML 로 읽기만 하고 DB 는 열지 않는다. 읽지 못하는 자리(권한)도 닫는
    쪽이다 — 그 안에 rcm 서비스가 있는지 모른다."""
    home = Path.home() if home is None else home
    environ = os.environ if environ is None else environ
    out: list[core.RcmService] = []
    for place, root in service_places(home, environ):
        try:
            entries = sorted(place.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as e:
            reason = f"setup cannot list that folder ({e.strerror or type(e).__name__})"
            out.append(core.RcmService(place.name, real_path(place), problem=reason))
            continue
        for path in entries:
            if path.suffix not in (".plist", ".service") or not path.is_file():
                continue
            found = _rcm_service(path, home=home, root=root)
            if found is not None:
                out.append(found)
    return out


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
    services: Callable[[], list[core.RcmService]] = scan_services


def _is_tty() -> bool:
    from remote_ci_monitor import doctor

    return doctor.is_tty()


# ── 명령줄 ───────────────────────────────────────────────────────────────────


#: 줄인 옵션을 받지 않는다 — 정의는 core 에 있다(훅도 같은 정의로 읽는다)
PARSER_OPTIONS = core.PARSER_OPTIONS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rcm setup",
        description="Set this machine up for one role: config and service file.",
        **PARSER_OPTIONS,
    )
    core.add_arguments(p)
    return p


def register(subparsers: Any) -> argparse.ArgumentParser:
    """cli 의 하위 명령 목록에 `setup` 을 단다(`cli.build_parser` 가 부른다)."""
    p = subparsers.add_parser(
        "setup", help="set this machine up as a client, worker or coordinator", **PARSER_OPTIONS
    )
    core.add_arguments(p)
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


def scan_roles(environ: Mapping[str, str]) -> tuple[list[str], str | None]:
    """setup 이 쓰는 역할 스캔 — (역할들, 경고). 있는 서버 설정이 TOML · UTF-8 이 아니어서 읽지
    못해도 setup 을 막지 않는다(P5 A-E5): 그 파일은 `--force config` 로 고칠 수 있고, 덮는 것은
    여전히 `--force config` 일 때만이다(`_check_free`). 그때는 워커 · 클라이언트 설정만 본다.
    `$RCM_CONFIG` 가 없는 파일을 가리키는 것은 그대로 사용법 오류다(TR38)."""
    try:
        return existing_roles(environ), None
    except SetupUsage as e:
        from remote_ci_monitor.config import (
            ConfigError,
            find_client_config,
            find_server_config,
            user_config_dir,
        )

        named = environ.get("RCM_CONFIG")
        if named and not Path(named).expanduser().is_file():
            raise
        try:
            server = find_server_config(None)
        except ConfigError:
            raise e from None
        roles = []
        if (user_config_dir() / "worker.toml").is_file() or environ.get("RCM_WORKER_TOKEN"):
            roles.append(dc.ROLE_WORKER)
        # 클라이언트도 doctor.infer_roles 와 같은 규칙으로 찾는다(P6 재확인 5 · v1-3)
        try:
            client = find_client_config(None)
        except ConfigError:
            client = None
        if client is not None or environ.get("RCM_SERVER") or environ.get("RCM_TOKEN"):
            roles.append(dc.ROLE_CLIENT)
        reason = str(e).removeprefix(f"{server}: ") if server is not None else str(e)
        where = server if server is not None else "the existing config"
        return roles, (
            f"could not read {where} ({reason}) — its role is not known; setup goes on, and "
            "--force config replaces that file"
        )


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
            value = _whole_number(self.line(question, str(default)))
            if value is not None and low <= value <= high:
                return value
            self.say(f"a number from {low} to {high}")

    def say(self, text: str) -> None:
        self.err.write(f"  {text}\n")
        self.err.flush()


def _whole_number(answer: str) -> int | None:
    """ASCII 숫자만으로 된 답의 값. `"²".isdigit()` 은 참인데 `int("²")` 는 ValueError 이고,
    `int("١")` 은 1 이다 — 사람이 친 숫자는 0-9 만 받는다(P3 v2-3)."""
    if answer.isascii() and answer.isdecimal():
        return int(answer)
    return None


def _ask_role(ask: _Ask) -> str:
    ask.say("Which role does this machine take?")
    for i, role in enumerate(dc.ROLES, 1):
        ask.say(f"{i}) {role} — {ROLE_HELP[role]}")
    while True:
        answer = ask.line("role")
        index = _whole_number(answer)
        if index is not None and 1 <= index <= len(dc.ROLES):
            return dc.ROLES[index - 1]
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
        index = _whole_number(answer)
        if index is not None and 1 <= index <= len(choices):
            return choices[index - 1][0]
        problem = core.address_problem(answer) if answer else None
        if problem is not None:
            ask.say(f"{answer}: {problem}")
            continue
        answer = core.normal_address(answer) if answer else answer
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
    #: 쓰다 멈춘 뒤 되돌리지 못한 파일
    unrestored: list[Path] = field(default_factory=list)
    #: 올라간 서비스를 바꿔 끼우는 순서를 찍나 — 같은 라벨이 올라가 있거나 `--force service` 로
    #: 있던 서비스 파일을 다시 쓸 때(P5 B-E2)
    replacing: bool = False
    #: 같은 라벨이 setup 전에 이미 올라가 있었나 — 제거 줄이 그 서비스는 setup 이 쓴 것이 아닐 수
    #: 있다고 말한다(P6 재확인 3)
    preloaded: bool = False
    #: 서버 역할의 data_dir — 운영 알아보기(결정 352)가 견준다
    data_dir: Path | None = None
    #: 마지막 `rcm doctor` 줄에 붙일 경로 — 사용자가 `--config` · `--service-file` 을 줬을 때만
    doctor_config: Path | None = None
    doctor_service: Path | None = None


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
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        host = None
    if not host:  # `http://` · `http:///x` · `http://:8787` — 갈 곳이 없다(P3 v2-12)
        raise SetupUsage(f"--server must name the coordinator's host (got {url!r})")
    return url


def _where(args: argparse.Namespace, world: World, role: str, **given: Any) -> core.Where:
    """setup 이 어디에 쓸지 — `core.where` 하나로 정한다. 훅(tools/guard_production.py)도 같은
    함수를 부르므로 둘이 다른 자리를 볼 수 없다(P3 2라운드). `given` 은 물어서 받은 값(라벨 ·
    data_dir)과 템플릿의 기본 data_dir 이다."""
    values: dict[str, Any] = {
        "config": args.config,
        "label": args.label,
        "service_file": args.service_file,
        "data_dir": args.data_dir,
    }
    values.update(given)
    home = Path.home()
    fold = core.folds_case(world.platform)
    try:
        found = core.where(
            role,
            environ=os.environ,
            cwd=Path.cwd(),
            home=home,
            manager=core.manager_of(world.platform),
            fold_case=fold,
            expand_user=os.path.expanduser,
            **values,
        )
    except core.SetupValueError as e:
        raise SetupUsage(str(e)) from None
    place = core.install_place_of(found.config, home, fold_case=fold)
    if place is not None:
        # 설정 파일도 서비스 파일과 같은 규칙이다 — 서비스 관리자가 읽는 자리에 쓰지 않는다
        # (P3 3라운드 v1-1). 그 자리의 운영 서비스 파일을 `--config --force config` 로 덮을 수
        # 있었다
        raise SetupUsage(
            f"--config {found.config} is inside {place}, an install place the service manager "
            "reads — setup writes no config there; pick a config path outside it"
        )
    return found


#: 글자로 받는 옵션 — 설정 · 서비스 파일에 UTF-8 로 들어간다
_TEXT_OPTIONS = (
    "role",
    "server",
    "by",
    "config",
    "pool",
    "label",
    "service_file",
    "data_dir",
    "bind",
)


def _check_text(args: argparse.Namespace) -> None:
    """UTF-8 이 아닌 인자(파이썬이 surrogate 로 받은 바이트)는 파일에 쓸 수 없다 — 쓰기 전에
    사용법 오류로 멈춘다(P3 v2-10)."""
    for name in _TEXT_OPTIONS:
        value = getattr(args, name, None)
        if not isinstance(value, str):
            continue
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise SetupUsage(
                f"--{name.replace('_', '-')} is not valid UTF-8 text — setup writes its files "
                "in UTF-8"
            ) from None


#: 역할에 따라 쓰이지 않을 수 있는 플래그 — `core.unused_flags` 가 본다
_FLAG_NAMES = (
    "server",
    "by",
    "pool",
    "lanes",
    "label",
    "service_file",
    "port",
    "data_dir",
    "advertise",
    "bind",
    "reuse_data_dir",
)


def _plan(args: argparse.Namespace, world: World) -> Plan:
    _check_text(args)
    blank = core.blank_flags(vars(args))
    if blank:  # 빈 `--config` 는 기본 자리로 떨어졌다 — 묻기 전에 멈춘다(P3 2라운드 v2-1)
        raise SetupUsage(f"{' '.join(blank)} must not be empty")
    roles_here, unread = scan_roles(os.environ)
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
    if role == dc.ROLE_CLIENT:
        plan = _client(args, ask if asking else None, world)
    elif role == dc.ROLE_WORKER:
        plan = _worker(args, ask if asking else None, world)
    else:
        plan = _server(args, ask if asking else None, world, role, roles_here)
    if role != dc.ROLE_CLIENT:
        _service(args, ask if asking else None, world, plan)
    given = {name: getattr(args, name, None) for name in _FLAG_NAMES}
    unused = core.unused_flags(role, given)
    if unused:
        plan.warnings.insert(0, f"{' '.join(unused)}: not used by --role {role} (ignored)")
    if unread:
        plan.warnings.insert(0, unread)
    if plan.rcm is None:  # 서비스 파일이 없는 역할도 마지막 doctor 줄은 이 빌드로 찍는다(P5 B-E7)
        plan.rcm, _ = world.find_rcm()
    plan.doctor_config = plan.config if args.config is not None else None
    plan.doctor_service = plan.service if args.service_file is not None else None
    _check_production(args, plan, world)
    return plan


def _check_production(args: argparse.Namespace, plan: Plan, world: World) -> None:
    """쓸 자리가 이 머신의 rcm 서비스가 쓰는 것이면 거절한다(결정 352) — 종료 1, 쓰지 않는다,
    우회 플래그는 없다. 계획을 마친 뒤와 쓰기 직전에 한 번씩 부른다(스캔과 쓰기 사이에 생긴
    유닛). 경로는 심볼릭 링크를 풀어 견주고 darwin 은 대소문자를 접는다."""
    targets = {"--config": plan.config}
    if plan.service is not None:
        targets["--service-file"] = plan.service
    if plan.data_dir is not None:
        targets["--data-dir"] = plan.data_dir
    written = [f.data for f in plan.files if f.kind == "service"]
    if plan.service is not None:
        try:
            written.append(plan.service.read_bytes())
        except OSError:
            pass
    reason = core.production_refusal(
        world.services(),
        {flag: real_path(path) for flag, path in targets.items()},
        label=plan.label if plan.service is not None else None,
        written=written,
        force_service=bool(set(args.force or ()) & {"service", "all"}),
        fold_case=core.folds_case(world.platform),
    )
    if reason is not None:
        raise SetupFailed(reason)


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


def _client(args: argparse.Namespace, ask: _Ask | None, world: World) -> Plan:
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
    path = _where(args, world, dc.ROLE_CLIENT).config
    plan = Plan(role=dc.ROLE_CLIENT, config=path)
    # 토큰이 들어갈 수 있는 파일이라 600 이다(`init client` 와 같다)
    plan.files.append(Planned(path, text.encode("utf-8"), 0o600, "config"))
    plan.tokens = [
        "on the coordinator, make a client token: rcm token add CLIENT_NAME",
        f'then: export RCM_TOKEN=… with that token (or token = "…" in {path}, mode 600)',
    ]
    return plan


def _worker(args: argparse.Namespace, ask: _Ask | None, world: World) -> Plan:
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
    path = _where(args, world, dc.ROLE_WORKER).config
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
    roles_here: list[str],
) -> Plan:
    from remote_ci_monitor import cli
    from remote_ci_monitor.store import DB_VERSION

    template = cli.read_template("server.toml").decode("utf-8")
    if args.bind is not None:
        bind = args.bind.strip()
        problem = core.address_problem(bind)  # 이름은 풀지 않는다 — DNS · mDNS 를 묻지 않는다
        if problem == core.NAME_NOT_ADDRESS:
            raise SetupUsage(f"--bind {bind}: {problem}, such as 0.0.0.0")
        if problem is not None:  # 괄호를 친 주소(P6 재확인 6)
            raise SetupUsage(f"--bind {bind}: {problem}")
        bind = core.normal_address(bind)
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
    template_data = core.template_value(template, "data_dir") or core.DEFAULT_DATA_DIR
    raw = args.data_dir
    if raw is None and ask is not None:
        shown = _where(args, world, role, default_data_dir=template_data).data_dir
        raw = ask.line("data_dir", str(shown))
    data_dir = _where(args, world, role, data_dir=raw, default_data_dir=template_data).data_dir
    assert data_dir is not None  # 서버 역할은 늘 data_dir 이 있다
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
    path = _where(args, world, role).config
    text = core.server_toml(
        template, bind=bind, port=port, data_dir=str(data_dir), lanes=lanes, advertise=advertise
    )
    plan = Plan(role=role, config=path, label=label, warnings=warnings, data_dir=data_dir)
    plan.files.append(Planned(path, text.encode("utf-8"), 0o644, "config"))
    db = data_dir / DB_FILE
    try:
        present = db.exists()
    except OSError as e:  # 읽을 수 없는 폴더 — 있는지 모르면 쓰지 않는다(P3 v1-10)
        raise SetupFailed(
            f"cannot look into {data_dir}: {e.strerror or e} — choose a data_dir this user can "
            "read (nothing was written)"
        ) from e
    if present:
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
    elif held is None:  # 모름은 모름이라고 쓴다(P3 v1-5)
        warnings.append(
            f"could not tell whether port {port} is free on {bind} — check it before you start "
            "the service"
        )
    warnings.extend(core.bind_warnings(kind))
    if role == dc.ROLE_COORDINATOR_WORKER and dc.ROLE_WORKER in roles_here:
        warnings.append(
            "this machine already has worker.toml — an rcm worker next to a coordinator-worker "
            "runs two sets of lanes on one host; that is only needed for a second worker"
        )
    return plan


def _render_service(
    plan: Plan, manager: str, *, rcm: Path, home: Path, label: str, log: Path
) -> bytes:
    from remote_ci_monitor import cli

    if plan.role in core.SERVER_ROLES:
        render = core.server_plist if manager == core.LAUNCHD else core.server_unit
        return render(
            rcm=rcm,
            config=plan.config,
            home=home,
            label=label,
            log=log,
            path=cli.WORKER_SERVICE_PATH,
        )
    if manager == core.LAUNCHD:
        doc = plistlib.loads(cli.worker_plist(rcm, plan.config, home))
        doc["Label"] = label
        doc["StandardOutPath"] = doc["StandardErrorPath"] = str(log)
        return plistlib.dumps(doc)
    return core.worker_unit(
        rcm=rcm, config=plan.config, home=home, label=label, log=log, path=cli.WORKER_SERVICE_PATH
    )


def _service(args: argparse.Namespace, ask: _Ask | None, world: World, plan: Plan) -> None:
    """서비스 파일 하나를 계획에 더한다 — 설정 폴더 아래 `service/` 에, 등록하지 않는다."""

    if plan.label is None:
        plan.label = _label(args, ask, plan.role)
    manager = core.manager_of(world.platform)
    if manager is None:
        # 서비스 파일은 없어도 토큰 명령(TR21 · TR27)은 찍는다 — 서버를 띄우는 빌드로(P5 A-E3)
        rcm, _ = world.find_rcm()
        plan.rcm = rcm
        if plan.role in core.SERVER_ROLES:
            plan.tokens = core.token_commands(rcm or Path("rcm"), plan.config)
        plan.warnings.append(
            f"no launchd or systemd on {world.platform} — no service file; start "
            f"`rcm {'serve' if plan.role in core.SERVER_ROLES else 'worker'} --config "
            f"{shlex.quote(str(plan.config))}` yourself"
        )
        return
    home = Path.home()
    label = plan.label
    found = _where(args, world, plan.role, label=label)
    staged, place = found.service_file, found.install_place
    assert staged is not None  # 서비스가 있는 역할 · 서비스 관리자가 있는 플랫폼
    if place is not None:
        # 설치 자리에 쓰지 않는다(§4.1, ⛔ Q9) — 그 자리의 파일은 서비스 관리자가 읽는 것이고,
        # 운영 서비스의 파일도 거기 있다(P3 v1-2)
        raise SetupUsage(
            f"--service-file {staged} is inside {place}, an install place the service manager "
            "reads — setup does not install; let it write the file elsewhere and copy it with "
            "the printed command"
        )
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
    log = core.log_dir(manager, home) / core.log_file_name(label)
    try:
        data = _render_service(plan, manager, rcm=rcm, home=home, label=label, log=log)
    except ValueError as e:
        # 줄바꿈 · 제어 문자가 든 경로는 유닛 · plist 에 쓸 수 없다 — 아무것도 쓰지 않는다(P3 v2-4)
        kind = "systemd unit" if manager == core.SYSTEMD else "launchd plist"
        raise SetupUsage(f"cannot write the {kind}: {e}") from None
    if plan.role in core.SERVER_ROLES:
        plan.tokens = core.token_commands(rcm, plan.config)
    plan.files.append(Planned(staged, data, 0o644, "service"))
    plan.manager, plan.service, plan.rcm, plan.log = manager, staged, rcm, log
    plan.installed = core.install_path(label, manager, home)
    loaded = world.service_loaded(manager, label)
    forced = set(args.force or ()) & {"service", "all"}
    plan.replacing = bool(loaded) or (bool(forced) and staged.exists())
    plan.preloaded = bool(loaded)
    if loaded:
        plan.warnings.append(
            f"a service labelled {label} is already loaded — setup leaves it alone; the copy "
            "below does not replace it (the lines under 'to replace' do, or pass --label to set "
            "up another one)"
        )
    elif loaded is None:  # 서비스 관리자가 답하지 않았다(P3 v1-5 · P5 A-E2)
        tool = "launchctl" if manager == core.LAUNCHD else "systemctl --user"
        plan.warnings.append(
            f"could not tell whether a service labelled {label} is already loaded — {tool} "
            "did not answer for this user; the start command printed below may fail on this "
            "machine (no user session or service manager to reach)"
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
        # Ctrl-C 도 되돌린다 — 반쪽 설정을 남기지 않는다. 되돌리지 못한 파일은 삼키지 않고
        # 이름을 댄다(P3 v1-6)
        for written, old, mode in reversed(done):
            try:
                if old is None:
                    written.path.unlink(missing_ok=True)
                else:
                    cli.write_private(written.path, old, mode)
            except OSError:
                plan.unrestored.append(written.path)
        if not isinstance(e, OSError):
            raise
        where = current.path if current is not None else "?"
        raise SetupFailed(f"cannot write {where}: {e.strerror or e} — {_left(plan)}") from e


def _left(plan: Plan) -> str:
    """되돌린 뒤 디스크에 남은 것."""
    if not plan.unrestored:
        return "nothing was written"
    return "could not restore " + ", ".join(str(p) for p in plan.unrestored)


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
    # 워커는 토큰을 넣은 뒤에 시작한다 — 토큰 없이 시작한 워커는 거절당하며 재시작을 되풀이한다
    # (P5 B-E9). 코디네이터의 토큰은 서버가 돈 뒤에 만든다
    token_first = plan.role == dc.ROLE_WORKER
    if plan.tokens and token_first:
        _tokens(plan, err)
    if plan.service is not None and plan.installed is not None and plan.manager is not None:
        err.write("next — install and start the service yourself (setup registers nothing):\n")
        if plan.manager == core.SYSTEMD:
            # 헤드리스 머신에는 로그인 세션이 없어 user manager 도 없다 — 시작 명령보다 먼저다
            # (P5 B-E5)
            err.write(f"  {LINGER_LINE}\n")
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
        if plan.replacing:
            err.write("to replace the service already installed or loaded, in this order:\n")
            for line in core.replace_commands(
                plan.manager, staged=plan.service, installed=plan.installed, label=plan.label or ""
            ):
                err.write(f"  {line}\n")
    if plan.tokens and not token_first:
        _tokens(plan, err)
    err.write("advice — checks that need no server (they do not change the exit code):\n")
    for line in advice(plan, world):
        err.write(f"{line}\n")
    removal = core.remove_line(
        plan.manager,
        installed=plan.installed,
        label=plan.label,
        files=[f.path for f in plan.files],
        staged=plan.service,
        preloaded=plan.preloaded,
    )
    err.write(f"{removal}\n")
    doctor = core.doctor_command(
        plan.role, rcm=plan.rcm, config=plan.doctor_config, service_file=plan.doctor_service
    )
    if plan.role == dc.ROLE_CLIENT:
        err.write(f"then: add your token and run `{doctor}`\n")
    elif token_first:
        err.write(f"then: put the worker token in, start the service, and run `{doctor}`\n")
    else:
        err.write(f"then: start the service, add a token, and run `{doctor}`\n")
    err.flush()


def _tokens(plan: Plan, err: TextIO) -> None:
    if plan.role in core.SERVER_ROLES:
        err.write("tokens — once the server runs, with the same build as the service:\n")
    else:
        err.write("token:\n")
    for line in plan.tokens:
        err.write(f"  {line}\n")


# ── 진입점 ───────────────────────────────────────────────────────────────────


def _say(world: World, line: str) -> None:
    """경계의 한 줄. 이 줄을 쓰다 또 터지면(닫힌 파이프) 조용히 넘어간다 — 종료 코드가 남는다."""
    try:
        world.stderr.write(f"{line}\n")
        world.stderr.flush()
    except (OSError, ValueError):
        pass


def _after(plan: Plan | None, written: bool) -> str:
    """경계에서 멈췄을 때 디스크에 남은 것."""
    if plan is None:
        return "nothing was written"
    if not written:
        return _left(plan)
    return "these files were written: " + ", ".join(str(f.path) for f in plan.files)


def _one_line(e: BaseException) -> str:
    """예외 하나를 한 줄로 — 이름과 첫 줄(제어 문자를 걷어 낸 것)."""
    first = dc.clean_text((str(e).splitlines() or [""])[0], 200)
    return f"{type(e).__name__}: {first}" if first else type(e).__name__


def run(args: argparse.Namespace, world: World) -> int:
    """setup 의 맨 바깥 경계. 예상 못 한 예외도 traceback 이 아니라 한 줄이고, 그 한 줄은 디스크에
    남은 것을 말한다(P3 v2-3 · v2-11).

    종료 코드는 파일을 썼는지로 정한다(§4.1 「설정을 썼으면 0」). 쓰기 전 · 쓰는 중의 예외는
    종료 1, Ctrl-C 는 130 이다. 다 쓴 뒤 보고(`_report`)에서 난 예외와 Ctrl-C 는 종료 0 에 그 한
    줄을 붙인다(P3 2 · 3라운드)."""
    plan: Plan | None = None
    try:
        plan = _plan(args, world)
        _check_free(args, plan)
        _check_production(args, plan, world)  # 쓰기 직전에 한 번 더(결정 352)
        _write(plan)
    except SetupUsage as e:
        _say(world, f"rcm: {e}")
        return EXIT_USAGE
    except SetupFailed as e:
        _say(world, f"rcm: {e}")
        return EXIT_FAILED
    except KeyboardInterrupt:
        _say(world, f"\nrcm: interrupted — {_after(plan, False)}")
        return EXIT_INTERRUPTED
    except Exception as e:  # noqa: BLE001 — setup 의 맨 바깥 경계
        _say(
            world,
            f"rcm: setup stopped on an unexpected error ({_one_line(e)}) — {_after(plan, False)}",
        )
        return EXIT_FAILED
    try:
        _report(plan, world)
    except KeyboardInterrupt:  # 파일은 이미 다 썼다 — 설정을 썼으면 0 이다(P3 3라운드 v2-2)
        _say(world, f"\nrcm: files written; report interrupted — {_after(plan, True)}")
        return EXIT_OK
    except Exception as e:  # noqa: BLE001 — 파일은 이미 다 썼다
        _say(world, f"rcm: files written; the report failed: {_one_line(e)}")
        return EXIT_OK
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
