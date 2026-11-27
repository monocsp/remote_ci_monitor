"""시험 서버의 프로세스를 띄우고, 띄울 때 적은 신원에만 신호를 보낸다.

`.claude/skills/_shared/test-server.md` 「Signals」 가 정본이고 이 파일은 그 절차를 실행한다.
셸 블록으로 띄우던 꼴(`{ … & echo $! > x.pid; } && ps … > x.start`)은 빌드 머신의
`tools/guard_production.py` 훅이 「rcm setup 에 닿을 수 있다」로 읽고 막았다(2026-11-27 batch-4).
훅을 좁히거나 훅의 문장 나누기를 비켜 가는 꼴 대신 이 도구가 띄우기와 신호를 맡는다.

- `start`: 셸을 거치지 않고 argv 그대로 띄운다. 띄운 직후 `<dir>/<name>.pid` 와
  `<dir>/<name>.start`(`LC_ALL=C TZ=UTC ps -p <pid> -o lstart=` 가 찍는 글 그대로 — 워커의
  `remote_worker.ps_start` 와 같은 환경)를 차례로, 파일마다 원자적으로 쓴다. 출력은
  `<dir>/<name>.log` 에 이어 쓴다. 새 세션으로 띄워 부른 셸이 끝나도 살아 있다.
- `signal`: 두 파일을 다시 읽어 pid 가 1 보다 크고 지금 `ps` 의 시작 시각이 적은 것과 같을 때만
  (`--expect-config` 면 명령줄에 그 경로가 낱말로 있을 때만) 보낸다. 아니면 적은 시각 · 지금
  시각 · 명령줄을 한 줄씩 찍고 `BLOCKED`, 종료 3. 이름으로 찾는 길은 없다.
- `stop-all`: 그 폴더에 적힌 것만 같은 확인 뒤 SIGTERM → 기다림 → 아직 같은 신원이면 SIGKILL.
- 모름은 「끝남」이 아니다(2026-11-27 batch-6): `ps` 를 못 돌리거나(없음 · 시간 초과 · 이상한
  종료) 기록을 못 읽으면(디렉터리 · 권한 · UTF-8 아님) 그 이름은 BLOCKED 이고 stop-all 은
  나머지를 계속한다. 곧바로 죽은 자식(CLI 는 1초 지켜본다)과 기록하지 못한 자식은 종료 1 이고,
  기록하지 못한 자식은 거둔다. 자식의 신호 마스크는 exec 전에 비운다(#267).

운영 가드는 띄우기 전에 본다(거부는 종료 4, 아무것도 만들지 않는다): `--dir` 가 진짜 홈의
`~/.local/share/rcm` · `~/.config/rcm` 아래이거나 `/tmp` 아래, 또는 운영 설치의 뿌리 안이면
거부한다. 안쪽 명령의 `--config` 가 운영 설정이거나, 포트 8787(설정의 기본값이기도 하다 —
`[server]` 가 없거나 `port` 가 없으면 8787 이다)을 쓰거나, 운영 data_dir 를 열면 거부한다.
설정 위에 얹히는 `--port` · `--bind` · `--server` 의 8787 도, 그리고 rcm 의 argparse 가 받는
줄임말(`--por` · `--conf` · `--data-d`)도 그 옵션으로 읽는다. `rcm serve|worker|token` 은
`--config` 를 꼭 받고, 클라이언트 명령은 서버 주소(`RCM_SERVER` · `--server`)를 꼭 받는다
— 없으면 발견으로 운영을 찾는다. 그리고 운영 가드 훅의 판정(`guard_production.decide`)을
안쪽 명령에 그대로 묻는다 — 이 도구가 훅을 돌아가는 길이 되지 않게. 운영 설치는 훅과 같은
함수(`find_production`)로 찾되 홈은 `HOME` 이 아니라 계정의 진짜 홈이다 — 이 도구는
`HOME=<data_dir>/home` 아래에서 불린다.

표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pwd
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import IO, Any
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # 3.10 이하 — 설정을 못 읽으니 --config 가 든 명령은 거부한다
    tomllib = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_REFUSED = 4

PRODUCTION_PORT = 8787
SIGNALS = ("TERM", "KILL", "INT")
#: `rcm` 의 하위 명령 가운데 DB 를 여는 것 — `--config` 를 꼭 받는다
NEEDS_CONFIG = frozenset({"serve", "worker", "token"})
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
PID_RE = re.compile(r"[0-9]+")
#: `ps` 는 워커의 읽기 코드(`remote_worker.ps_start`)와 같은 로캘 · 시간대로 부른다
PS_ENV = {"LC_ALL": "C", "TZ": "UTC"}
#: 부를 `ps` 와 그 시간 상한. 시험이 없는 경로 · 오래 걸리는 가짜로 바꿔 실패 갈래를 본다
PS_COMMAND = "ps"
PS_TIMEOUT = 5.0
#: `start` 의 CLI 가 띄운 뒤 자식이 살아 있는지 지켜보는 초. 곧바로 죽은 자식은 종료 1 이다
SETTLE_SECONDS = 1.0
LOG_TAIL_LINES = 20
SKILL = ".claude/skills/_shared/test-server.md"


def _load_guard() -> ModuleType:
    """같은 폴더의 운영 가드 훅을 들여온다. 이미 들여온 것이 있으면 그것을 쓴다."""
    loaded = sys.modules.get("guard_production")
    if loaded is not None:
        return loaded
    path = Path(__file__).resolve().with_name("guard_production.py")
    spec = importlib.util.spec_from_file_location("guard_production", path)
    if spec is None or spec.loader is None:  # pragma: no cover — 레포가 깨진 것
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gp = _load_guard()


# ── 어디가 운영인가 ──────────────────────────────────────────────────────────


def _real_home() -> Path:
    """계정의 진짜 홈. `HOME` 은 시험 서버의 `<data_dir>/home` 일 수 있어 보지 않는다."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@dataclass(frozen=True)
class Places:
    """운영으로 보는 자리. 시험은 가짜 홈과 빈 `Production` 을 준다."""

    home: Path
    prod: Any  # guard_production.Production
    tmp_roots: tuple[Path, ...] = field(default=(Path("/tmp"), Path("/private/tmp")))

    @classmethod
    def of_this_machine(cls) -> Places:
        home = _real_home()
        saved = os.environ.get("HOME")
        os.environ["HOME"] = str(home)  # find_production 은 `~` 를 HOME 으로 푼다
        try:
            prod = gp.find_production()
        finally:
            if saved is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved
        return cls(home=home, prod=prod)

    def folders(self) -> tuple[tuple[str, Path], ...]:
        """(이름, 뿌리) — 서비스의 data_dir 와 설정 폴더의 문서상 자리."""
        return (
            ("the production data directory", self.home / ".local" / "share" / "rcm"),
            ("the production config directory", self.home / ".config" / "rcm"),
        )


def _abs(value: str | Path, cwd: Path) -> Path:
    path = Path(os.path.expanduser(str(value)))
    return Path(os.path.normpath(path if path.is_absolute() else cwd / path))


def _forms(path: Path) -> tuple[Path, ...]:
    """글자 그대로의 경로와 심볼릭 링크를 푼 경로(`/tmp` → `/private/tmp`)."""
    return (path, Path(os.path.realpath(path)))


def _production_place(path: Path, places: Places, *, tmp: bool = True) -> str | None:
    """`path` 가 운영 자리 안이면 그 이름, 아니면 None."""
    for form in _forms(path):
        if tmp:
            for root in places.tmp_roots:
                if any(gp.inside(form, r) for r in _forms(root)):
                    return f"{root} (the system may clear it mid-run)"
        for name, root in places.folders():
            if any(gp.inside(form, r) for r in _forms(root)):
                return f"{name} {root}"
        found = gp.production_root(form, places.prod)
        if found is not None:
            return f"{found[0]} {found[1]}"
    return None


def _names_option(word: str, names: tuple[str, ...]) -> bool:
    """`word` 가 `names` 가운데 하나인가. rcm 의 argparse 는 `allow_abbrev` 를 끄지 않아
    `--por` 도 `--port` 다 — 그래서 `--` 낱말이 지키는 긴 옵션의 앞부분이면 그 옵션으로 본다.
    줄임말이 모호하면 rcm 이 거부하지만, 여기서는 그 값을 보는 쪽(더 막는 쪽)으로 읽는다."""
    if word in names:
        return True
    return (
        word.startswith("--")
        and len(word) > 2
        and any(name.startswith("--") and name.startswith(word) for name in names)
    )


def _options(argv: Sequence[str], names: tuple[str, ...]) -> list[str]:
    """`--x v` · `--x=v` 꼴의 값을 모두. 긴 옵션의 줄임말(`--conf v`)도."""
    values = []
    for index, token in enumerate(argv):
        if not token.startswith("-"):
            continue
        word, equals, value = token.partition("=")
        if not _names_option(word, names):
            continue
        if equals:
            values.append(value)
        elif index + 1 < len(argv):
            values.append(argv[index + 1])
    return values


def _int_or_none(value: str) -> int | None:
    """argparse 의 `type=int` 와 같은 읽기(`int()` 그대로 — 앞의 0 · 공백 · `_` 도 받는다)."""
    try:
        return int(value)
    except ValueError:
        return None


def _bind_port(value: str) -> int | None:
    """`--bind` 값에 포트가 붙어 있으면(`127.0.0.1:8787`) 그 포트."""
    host, colon, tail = value.rpartition(":")
    return _int_or_none(tail) if colon and host else None


def _url_port(url: str) -> int | None:
    parts = urlsplit(url if "://" in url else f"http://{url}")
    port = parts.port  # 틀린 포트는 ValueError — 부르는 쪽이 거부로 바꾼다
    if port is None:
        return 443 if parts.scheme == "https" else 80
    return port


def _config_refusal(path: Path, places: Places) -> str | None:
    """`--config` 파일 하나를 본다: 운영 설정 · 포트 8787 · 운영 data_dir 를 거부한다."""
    place = _production_place(path, places, tmp=False)
    if place is not None:
        return f"--config {path} is inside {place}"
    if tomllib is None:
        return f"cannot read --config {path} on this Python (no tomllib)"
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        return f"cannot read --config {path} ({error}); it is unknown what it would open"
    section = raw.get("server")
    try:
        if isinstance(section, dict):  # server.toml — 포트가 없으면 기본값 8787 이다
            port = section.get("port", PRODUCTION_PORT)
        elif isinstance(section, str):  # worker.toml — 붙을 서버의 주소
            port = _url_port(section)
        else:  # `[server]` 가 아예 없다 — `ServerConfig.port` 의 기본값 8787 로 뜬다
            port = PRODUCTION_PORT
    except ValueError as error:
        return f"cannot read the port in --config {path} ({error})"
    if port == PRODUCTION_PORT:
        return f"--config {path} uses port {PRODUCTION_PORT}, production's (or leaves it unset)"
    data_dir = gp._config_data_dir(path)
    if data_dir is None:
        return f"cannot tell the data_dir of --config {path}"
    place = _production_place(Path(data_dir), places)
    if place is not None:
        return f"--config {path} opens data_dir {data_dir}, inside {place}"
    return None


def refusal(
    directory: str | Path,
    argv: Sequence[str],
    places: Places,
    *,
    cwd: str | Path,
    environ: Mapping[str, str],
) -> str | None:
    """띄우면 안 되는 이유. 없으면 None."""
    cwd = Path(cwd)
    target = _abs(directory, cwd)
    place = _production_place(target, places)
    if place is not None:
        return f"--dir {target} is inside {place}"
    subcommand = gp._rcm_subcommand(list(argv))
    # `-c` 는 rcm 의 짧은 꼴일 때만 설정이다 — `python -c …` 의 `-c` 는 프로그램이다.
    configs = _options(argv, ("--config", "-c") if subcommand else ("--config",))
    if not configs and environ.get("RCM_CONFIG"):
        configs = [environ["RCM_CONFIG"]]
    if subcommand in NEEDS_CONFIG and not configs:
        return (
            f"`rcm {subcommand}` needs an explicit --config (without one it can find production's)"
        )
    for config in configs:
        reason = _config_refusal(_abs(config, cwd), places)
        if reason is not None:
            return reason
    data_dirs = _options(argv, ("--data-dir", "--data"))
    if environ.get("RCM_SERVER_DATA_DIR") is not None:
        data_dirs.append(environ["RCM_SERVER_DATA_DIR"])
    for value in data_dirs:
        place = _production_place(_abs(value, cwd), places)
        if place is not None:
            return f"data_dir {value} is inside {place}"
    if _int_or_none(environ.get("RCM_SERVER_PORT", "")) == PRODUCTION_PORT:
        return f"RCM_SERVER_PORT={PRODUCTION_PORT} is production's port"
    # CLI 의 `--port` · `--bind` 는 설정 위에 얹힌다(`cli._server_config`) — 설정이 8790 이어도.
    for value in _options(argv, ("--port",)):
        if _int_or_none(value) == PRODUCTION_PORT:
            return f"--port {value} is production's port {PRODUCTION_PORT}"
    binds = _options(argv, ("--bind",))
    if environ.get("RCM_SERVER_BIND"):
        binds.append(environ["RCM_SERVER_BIND"])
    for value in binds:
        if _bind_port(value) == PRODUCTION_PORT:
            return f"bind address {value} is on production's port {PRODUCTION_PORT}"
    servers = _options(argv, ("--server",))
    if environ.get("RCM_SERVER"):
        servers.append(environ["RCM_SERVER"])
    if subcommand is not None and subcommand not in NEEDS_CONFIG and not servers:
        return (
            f"`rcm {subcommand}` needs RCM_SERVER or --server "
            "(without one the client may discover production)"
        )
    for server in servers:
        try:
            port = _url_port(server)
        except ValueError as error:
            return f"cannot read the port of server {server!r} ({error})"
        if port == PRODUCTION_PORT:
            return f"server {server} is on port {PRODUCTION_PORT}, production's"
    # 운영 가드 훅이 이 명령을 Bash 로 받았다면 내릴 판정 — 그대로 따른다.
    verdict = gp.decide(
        "Bash",
        {"command": shlex.join(argv)},
        places.prod,
        cwd,
        local_config=(cwd / "rcm.toml").exists(),
        environ=environ,
    )
    if verdict is not None:
        return f"tools/guard_production.py would {verdict.decision} it: {verdict.reason}"
    return None


# ── 신원 ─────────────────────────────────────────────────────────────────────


class PsFailed(Exception):
    """`ps` 를 못 돌렸다 — 그 프로세스가 살았는지 끝났는지 모른다. 「끝남」으로 읽지 않는다."""


class Unreadable(Exception):
    """pid · start 기록을 읽지 못했다(디렉터리 · 권한 · UTF-8 아님)."""


def _ps(pid: int, column: str) -> str:
    """`LC_ALL=C TZ=UTC ps -p <pid> -o <column>=` 의 출력 그대로. 없는 프로세스면 빈 글
    (`ps` 는 그때 아무것도 찍지 않고 1 로 끝난다). 못 돌렸으면 `PsFailed`."""
    try:
        out = subprocess.run(
            [PS_COMMAND, "-p", str(pid), "-o", f"{column}="],
            env={**os.environ, **PS_ENV},
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PsFailed(f"cannot run {PS_COMMAND} for pid {pid} ({error})") from error
    if out.returncode not in (0, 1) or (out.returncode == 1 and out.stderr.strip()):
        said = out.stderr.strip()[:200]
        raise PsFailed(f"{PS_COMMAND} for pid {pid} ended with exit {out.returncode} ({said})")
    return out.stdout


def _running_start(pid: int) -> str:
    """살아 있는 프로세스의 시작 시각(앞뒤 공백 없이). 끝났거나 좀비면 빈 글 — 좀비는 신호를
    받아도 아무 일이 없고, 거둘 부모를 기다리는 중일 뿐이다. `ps` 를 못 돌리면 `PsFailed`."""
    stat = _ps(pid, "stat").strip()
    if not stat or stat.startswith("Z"):
        return ""
    return _ps(pid, "lstart").strip()


@dataclass(frozen=True)
class Identity:
    """적힌 신원과 지금의 모습. `problem` 이 None 이면 그 프로세스는 아직 우리 것이다."""

    name: str
    pid: int | None
    recorded: str
    current: str
    command: str
    problem: str | None
    #: 기록을 못 읽었거나 `ps` 를 못 돌렸다 — 끝났다고도 살았다고도 말하지 않는다
    unknown: bool = False

    @property
    def gone(self) -> bool:
        return self.pid is not None and self.pid > 1 and not self.current and not self.unknown


def _read(path: Path) -> str | None:
    """기록 하나. 없으면 None, 있는데 못 읽으면 `Unreadable`."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise Unreadable(f"cannot read {path} ({error})") from error


def _holds(command: str, path: str) -> bool:
    """명령줄에 경로가 낱말로 있나(`x.toml.bak` 은 `x.toml` 이 아니다)."""
    padded = f" {command} "
    return f" {path} " in padded or f" --config={path} " in padded


def identity(directory: Path, name: str, expect_config: str | None = None) -> Identity:
    pid_path, start_path = directory / f"{name}.pid", directory / f"{name}.start"
    try:
        recorded = (_read(start_path) or "").strip()
        text = _read(pid_path)
    except Unreadable as error:
        return Identity(name, None, "", "", "", str(error), unknown=True)
    if text is None:
        return Identity(name, None, recorded, "", "", f"no pid file {pid_path}")
    if not PID_RE.fullmatch(text.strip()):
        return Identity(name, None, recorded, "", "", f"{pid_path} holds {text!r}, not a pid")
    pid = int(text.strip())
    if pid <= 1:
        return Identity(name, pid, recorded, "", "", f"pid {pid} is not above 1")
    try:
        current = _running_start(pid)
        command = _ps(pid, "command").strip() if current else ""
    except PsFailed as error:
        problem = f"{error}: whether pid {pid} still runs is unknown"
        return Identity(name, pid, recorded, "", "", problem, unknown=True)
    problem = None
    if not recorded:
        problem = f"{start_path} is missing or empty"
    elif not current:
        problem = f"pid {pid} is not running"
    elif current != recorded:
        problem = f"pid {pid} started at another time: it is another process now"
    elif expect_config is not None and not _holds(command, expect_config):
        problem = f"the command line of pid {pid} does not hold {expect_config}"
    return Identity(name, pid, recorded, current, command, problem)


def _print_blocked(ident: Identity, out: IO[str], *, prefix: str = "") -> None:
    print(f"{prefix}reason: {ident.problem}", file=out)
    print(f"{prefix}recorded start: {ident.recorded}", file=out)
    print(f"{prefix}current start: {ident.current}", file=out)
    print(f"{prefix}command: {ident.command}", file=out)


# ── start · signal · stop-all ───────────────────────────────────────────────


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _child_setup(interactive_sigint: bool) -> Callable[[], None]:
    """exec 직전 자식에서 돌 함수. 신호 마스크는 늘 비운다 — 부모가 막아 둔 신호(#267 의 막힌
    SIGTERM)는 exec 를 지나 자식에게 그대로 가서, 이 도구의 SIGTERM · SIGINT 가 닿지 않는다."""

    def prepare() -> None:
        signal.pthread_sigmask(signal.SIG_SETMASK, [])  # mutcheck: live-proc-sigmask-kept
        if interactive_sigint:
            # 작업 제어 없는 셸이 뒤로 보낸 프로세스는 SIGINT 를 무시한 채 시작하고, 파이썬은
            # 그때 ctrl-C 처리기를 달지 않는다. 기본 처분으로 돌려 놓으면 파이썬이 처리기를 단다.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    return prepare


def _reap(proc: subprocess.Popen[bytes]) -> None:
    """기록하지 못한 자식을 거둔다 — 신원 없이 살아 남으면 아무도 그것에 신호를 못 보낸다."""
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover — SIGKILL 뒤 10초는 커널이 멈춘 것
        pass


def _forget(*paths: Path) -> None:
    """반쪽 기록을 지운다. 못 지우면(디렉터리 · 권한) 그대로 둔다 — 그 이름은 다음에 BLOCKED 다."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _log_tail(path: Path, lines: int = LOG_TAIL_LINES) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return [f"(cannot read {path}: {error})"]
    return text.splitlines()[-lines:]


def start(
    directory: str | Path,
    name: str,
    argv: Sequence[str],
    *,
    interactive_sigint: bool = False,
    places: Places | None = None,
    out: IO[str] = sys.stdout,
    cwd: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    settle: float | None = None,
) -> subprocess.Popen[bytes] | int:
    """띄운 `Popen`, 아니면 종료 코드. 시험은 `Popen` 으로 자식을 거둔다.

    `settle` 초가 주어지면(CLI) 그동안 자식이 살아 있는지 본다. 곧바로 죽었으면 `FAILED` 와
    로그 꼬리를 찍는다 — 돌려주는 `Popen` 은 끝나 있고 CLI 는 종료 1 로 바꾼다."""
    here = Path.cwd() if cwd is None else Path(cwd)
    session = os.environ if environ is None else environ
    if not NAME_RE.fullmatch(name):
        print(f"REFUSED: --name {name!r} is not a plain name (letters, digits, - and _)", file=out)
        return EXIT_USAGE
    if not argv:
        print("REFUSED: no command after --", file=out)
        return EXIT_USAGE
    target = _abs(directory, here)
    reason = refusal(target, argv, places or Places.of_this_machine(), cwd=here, environ=session)
    if reason is not None:
        print(f"REFUSED: {reason}. Nothing was started. See {SKILL}.", file=out)
        return EXIT_REFUSED
    if not target.is_dir():
        print(f"REFUSED: {target} does not exist; create it first. See {SKILL}.", file=out)
        return EXIT_REFUSED
    pid_path, start_path = target / f"{name}.pid", target / f"{name}.start"
    if pid_path.exists():
        earlier = identity(target, name)
        if earlier.problem is None:
            print(
                f"REFUSED: {name} is still running as pid {earlier.pid}; stop it first.",
                file=out,
            )
            return EXIT_REFUSED
        if earlier.unknown:  # 그 기록을 덮으면 아직 살아 있을지 모르는 것의 신원을 잃는다
            print(
                f"REFUSED: the earlier {name} cannot be confirmed gone: {earlier.problem}", file=out
            )
            return EXIT_REFUSED
    log_path = target / f"{name}.log"
    with log_path.open("ab") as log:
        try:
            proc = subprocess.Popen(  # noqa: S603 — argv 그대로, 셸 없이
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=here,
                env=dict(session),
                start_new_session=True,
                preexec_fn=_child_setup(interactive_sigint),  # noqa: PLW1509 — 신호만 바꾼다
            )
        except OSError as error:
            print(f"FAILED: cannot start {argv[0]}: {error}", file=out)
            return EXIT_FAILED
    # pid · 시작 시각을 못 적으면 그 자식은 신원 없이 산다 — 거두고 반쪽 기록도 지운다.
    try:
        _write_atomically(pid_path, f"{proc.pid}\n")
        started = _ps(proc.pid, "lstart")
        if not started.strip():
            raise PsFailed(f"{PS_COMMAND} printed no start time for pid {proc.pid}")
        _write_atomically(start_path, started)
    except (OSError, PsFailed) as error:  # mutcheck: live-proc-unrecorded-child-kept
        _reap(proc)
        _forget(pid_path, start_path)
        print(f"FAILED: {name} (pid {proc.pid}) was not recorded ({error}); stopped it", file=out)
        return EXIT_FAILED
    if settle is not None:
        deadline = time.monotonic() + settle
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if proc.poll() is not None:  # mutcheck: live-proc-dead-child-started
            print(
                f"FAILED: {name} (pid {proc.pid}) ended at once with exit {proc.returncode}; "
                f"the end of {log_path}:",
                file=out,
            )
            for line in _log_tail(log_path):
                print(f"  {line}", file=out)
            return proc
    print(f"started {name}: pid {proc.pid}, start {started.strip()}, log {log_path}", file=out)
    return proc


def send(
    directory: str | Path,
    name: str,
    sig: str,
    *,
    expect_config: str | None = None,
    out: IO[str] = sys.stdout,
) -> int:
    if sig not in SIGNALS:
        print(f"--sig must be one of {', '.join(SIGNALS)}", file=out)
        return EXIT_USAGE
    ident = identity(Path(directory), name, expect_config)
    if ident.problem is None and ident.pid is not None:
        try:
            os.kill(ident.pid, getattr(signal, f"SIG{sig}"))
        except ProcessLookupError:
            ident = Identity(name, ident.pid, ident.recorded, "", "", "it ended before the signal")
        else:
            print(f"sent SIG{sig} to {name} (pid {ident.pid})", file=out)
            return EXIT_OK
    _print_blocked(ident, out)
    print("BLOCKED", file=out)
    return EXIT_BLOCKED


def _still(ident: Identity) -> bool | None:
    """아직 같은 신원으로 살아 있나. `ps` 를 못 돌리면 None — 모름은 「끝남」이 아니다."""
    if ident.pid is None:
        return False
    try:
        return _running_start(ident.pid) == ident.recorded
    except PsFailed:
        return None  # mutcheck: live-proc-stop-all-ps-failure-stopped


def stop_all(directory: str | Path, *, grace: float = 10.0, out: IO[str] = sys.stdout) -> int:
    """그 폴더에 적힌 것만: 확인 → SIGTERM → `grace` 초 기다림 → 아직 같으면 SIGKILL."""
    folder = Path(directory)
    blocked = False
    pending: list[Identity] = []
    for name in sorted(p.stem for p in folder.glob("*.pid")):
        ident = identity(folder, name)
        if ident.problem is None and ident.pid is not None:
            try:
                os.kill(ident.pid, signal.SIGTERM)
            except ProcessLookupError:
                print(f"{name}: not running (pid {ident.pid})", file=out)
                continue
            pending.append(ident)
        elif ident.gone and ident.recorded:
            print(f"{name}: not running (pid {ident.pid})", file=out)
        else:
            _print_blocked(ident, out, prefix=f"{name}: ")
            print(f"{name}: BLOCKED", file=out)
            blocked = True
    deadline = time.monotonic() + grace
    waiting = list(pending)
    while waiting and time.monotonic() < deadline:
        time.sleep(0.1)
        # 확인 못 한 것(None)은 끝난 것으로 빼지 않고 기다리는 쪽에 남긴다.
        waiting = [ident for ident in waiting if _still(ident) is not False]
    for ident in pending:
        state = _still(ident) if ident in waiting else False
        if state is None:
            print(
                f"{ident.name}: reason: {PS_COMMAND} failed after SIGTERM, so whether pid "
                f"{ident.pid} ended is unknown; no SIGKILL sent",
                file=out,
            )
            print(f"{ident.name}: BLOCKED", file=out)
            blocked = True
        elif state:
            try:
                os.kill(ident.pid or 0, signal.SIGKILL)
            except ProcessLookupError:
                print(f"{ident.name}: stopped by SIGTERM (pid {ident.pid})", file=out)
                continue
            print(f"{ident.name}: SIGKILL after {grace:g} s (pid {ident.pid})", file=out)
        else:
            print(f"{ident.name}: stopped by SIGTERM (pid {ident.pid})", file=out)
    return EXIT_BLOCKED if blocked else EXIT_OK


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="live_proc.py",
        description=(
            "Start test-server processes and signal only the pid and start time recorded at "
            f"start. There is no lookup by name. Rules: {SKILL} (Signals). Exit codes: 0 done, "
            "1 failed to start, ended within a second (the log's end is printed) or could not "
            "be recorded (then it is stopped), 2 usage, 3 BLOCKED (identity not confirmed, "
            "ps failed or a record unreadable, nothing sent), 4 REFUSED (a production path or "
            "port, nothing started)."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    begin = sub.add_parser(
        "start",
        usage="live_proc.py start --dir DIR --name NAME [--interactive-sigint] -- ARGV...",
        help="start ARGV (no shell), write NAME.pid, NAME.start and NAME.log in DIR",
    )
    begin.add_argument("--dir", required=True, help="the test server's data_dir")
    begin.add_argument("--name", required=True, help="server, worker or client")
    begin.add_argument(
        "--interactive-sigint",
        action="store_true",
        help="give the child SIGINT's default disposition, so a Python child ends on SIGINT",
    )
    send_p = sub.add_parser("signal", help="signal NAME only if its recorded identity still holds")
    send_p.add_argument("--dir", required=True)
    send_p.add_argument("--name", required=True)
    send_p.add_argument("--sig", required=True, choices=SIGNALS)
    send_p.add_argument(
        "--expect-config", help="the command line must also hold this path (server, worker)"
    )
    stop_p = sub.add_parser(
        "stop-all", help="SIGTERM, wait, then SIGKILL every process recorded in DIR"
    )
    stop_p.add_argument("--dir", required=True)
    stop_p.add_argument("--grace", type=float, default=10.0, help="seconds before SIGKILL")
    return parser


def main(
    argv: Sequence[str] | None = None, *, places: Places | None = None, out: IO[str] = sys.stdout
) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    command: list[str] = []
    if "--" in args_list:
        cut = args_list.index("--")
        args_list, command = args_list[:cut], args_list[cut + 1 :]
    args = _parser().parse_args(args_list)
    if args.action == "start":
        result = start(
            args.dir, args.name, command, interactive_sigint=args.interactive_sigint,
            places=places, out=out, settle=SETTLE_SECONDS,
        )  # fmt: skip
        if isinstance(result, int):
            return result
        return EXIT_OK if result.poll() is None else EXIT_FAILED
    if command:
        print("only start takes a command after --", file=out)
        return EXIT_USAGE
    if args.action == "signal":
        return send(args.dir, args.name, args.sig, expect_config=args.expect_config, out=out)
    return stop_all(args.dir, grace=args.grace, out=out)


if __name__ == "__main__":
    raise SystemExit(main())
