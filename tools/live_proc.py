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

운영 가드는 띄우기 전에 본다(거부는 종료 4, 아무것도 만들지 않는다): `--dir` 가 진짜 홈의
`~/.local/share/rcm` · `~/.config/rcm` 아래이거나 `/tmp` 아래, 또는 운영 설치의 뿌리 안이면
거부한다. 안쪽 명령의 `--config` 가 운영 설정이거나, 포트 8787(설정의 기본값이기도 하다)을
쓰거나, 운영 data_dir 를 열면 거부한다. `rcm serve|worker|token` 은 `--config` 를 꼭 받고,
클라이언트 명령은 서버 주소(`RCM_SERVER` · `--server`)를 꼭 받는다 — 없으면 발견으로 운영을
찾는다. 그리고 운영 가드 훅의 판정(`guard_production.decide`)을 안쪽 명령에 그대로 묻는다 —
이 도구가 훅을 돌아가는 길이 되지 않게. 운영 설치는 훅과 같은 함수(`find_production`)로 찾되
홈은 `HOME` 이 아니라 계정의 진짜 홈이다 — 이 도구는 `HOME=<data_dir>/home` 아래에서 불린다.

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


def _options(argv: Sequence[str], names: tuple[str, ...]) -> list[str]:
    """`--x v` · `--x=v` 꼴의 값을 모두."""
    values = []
    for index, token in enumerate(argv):
        if token in names and index + 1 < len(argv):
            values.append(argv[index + 1])
        for name in names:
            if token.startswith(name + "="):
                values.append(token.split("=", 1)[1])
    return values


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
        else:
            port = None
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
    if environ.get("RCM_SERVER_PORT", "").strip() == str(PRODUCTION_PORT):
        return f"RCM_SERVER_PORT={PRODUCTION_PORT} is production's port"
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


def _ps(pid: int, column: str) -> str:
    """`LC_ALL=C TZ=UTC ps -p <pid> -o <column>=` 의 출력 그대로. 없는 프로세스면 빈 글."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", f"{column}="],
            env={**os.environ, **PS_ENV},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout


def _running_start(pid: int) -> str:
    """살아 있는 프로세스의 시작 시각(앞뒤 공백 없이). 끝났거나 좀비면 빈 글 — 좀비는 신호를
    받아도 아무 일이 없고, 거둘 부모를 기다리는 중일 뿐이다."""
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

    @property
    def gone(self) -> bool:
        return self.pid is not None and self.pid > 1 and not self.current


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _holds(command: str, path: str) -> bool:
    """명령줄에 경로가 낱말로 있나(`x.toml.bak` 은 `x.toml` 이 아니다)."""
    padded = f" {command} "
    return f" {path} " in padded or f" --config={path} " in padded


def identity(directory: Path, name: str, expect_config: str | None = None) -> Identity:
    pid_path, start_path = directory / f"{name}.pid", directory / f"{name}.start"
    recorded = (_read(start_path) or "").strip()
    text = _read(pid_path)
    if text is None:
        return Identity(name, None, recorded, "", "", f"no pid file {pid_path}")
    if not PID_RE.fullmatch(text.strip()):
        return Identity(name, None, recorded, "", "", f"{pid_path} holds {text!r}, not a pid")
    pid = int(text.strip())
    if pid <= 1:
        return Identity(name, pid, recorded, "", "", f"pid {pid} is not above 1")
    current = _running_start(pid)
    command = _ps(pid, "command").strip() if current else ""
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


def _default_sigint() -> None:
    # 작업 제어 없는 셸이 뒤로 보낸 프로세스는 SIGINT 를 무시한 채 시작하고, 파이썬은 그때
    # ctrl-C 처리기를 달지 않는다. exec 전에 기본 처분으로 돌려 놓으면 파이썬이 처리기를 단다.
    signal.signal(signal.SIGINT, signal.SIG_DFL)


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
) -> subprocess.Popen[bytes] | int:
    """띄운 `Popen`, 아니면 종료 코드. 시험은 `Popen` 으로 자식을 거둔다."""
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
    if (target / f"{name}.pid").exists():
        earlier = identity(target, name)
        if earlier.problem is None:
            print(
                f"REFUSED: {name} is still running as pid {earlier.pid}; stop it first.",
                file=out,
            )
            return EXIT_REFUSED
    preexec: Callable[[], None] | None = _default_sigint if interactive_sigint else None
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
                preexec_fn=preexec,  # noqa: PLW1509 — 신호 처분 하나만 바꾼다
            )
        except OSError as error:
            print(f"FAILED: cannot start {argv[0]}: {error}", file=out)
            return EXIT_FAILED
    _write_atomically(target / f"{name}.pid", f"{proc.pid}\n")
    started = _ps(proc.pid, "lstart")
    _write_atomically(target / f"{name}.start", started)
    if started.strip():
        print(f"started {name}: pid {proc.pid}, start {started.strip()}, log {log_path}", file=out)
    else:
        print(f"started {name}: pid {proc.pid} ended at once; read {log_path}", file=out)
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


def _still(ident: Identity) -> bool:
    return ident.pid is not None and _running_start(ident.pid) == ident.recorded


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
        waiting = [ident for ident in waiting if _still(ident)]
    for ident in pending:
        if ident in waiting and _still(ident):
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
            "1 failed to start, 2 usage, 3 BLOCKED (identity not confirmed, nothing sent), "
            "4 REFUSED (a production path or port, nothing started)."
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
            places=places, out=out,
        )  # fmt: skip
        if isinstance(result, int):
            return result
        recorded = _read(_abs(args.dir, Path.cwd()) / f"{args.name}.start") or ""
        return EXIT_OK if recorded.strip() else EXIT_FAILED
    if command:
        print("only start takes a command after --", file=out)
        return EXIT_USAGE
    if args.action == "signal":
        return send(args.dir, args.name, args.sig, expect_config=args.expect_config, out=out)
    return stop_all(args.dir, grace=args.grace, out=out)


if __name__ == "__main__":
    raise SystemExit(main())
