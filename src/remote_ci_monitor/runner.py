"""잡 실행기(M5b-3) — 자재화 뒤 Popen(argv) · 로그 펌프 · 취소/타임아웃/정지 신호.

로컬 워커(`worker.py`, DB 에 직접 기록)와 원격 워커(`remote_worker.py`, HTTP 로 보고)가 **같은
함수** `run_job` 을 쓴다. 다른 것은 「관찰자」뿐이다 — 관찰자가 phase 를 남기고 raw 출력을 받아
마커를 파싱한다(실행기는 마커를 모른다).

규칙(오늘의 로컬 워커 그대로):
- 프리셋의 `argv` 만, 셸 없이, `cwd = workspace`, `start_new_session=True`(프로세스 그룹).
- 1초 폴링으로 취소(`should_cancel`) · 타임아웃 · 정지(`should_stop`)를 보고 SIGTERM →
  `grace_seconds` → SIGKILL(손자까지 `killpg`).
- 출력은 줄 단위로 `log_path` 에 flush 하고 같은 바이트를 관찰자 `output` 에 준다. 개행 없는
  긴 줄은 `MAX_LINE_BYTES` 에서 잘라 흘린다. EOF 뒤 남은 조각도 준다.
- 종료 코드로 예외를 내지 않는다. 시작 실패는 `RunnerError` — 문구가 아니라 코드다.
"""

from __future__ import annotations

import errno
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.inputs import env_for_inputs
from remote_ci_monitor.core.model import PHASE_EXECUTING, Source

READ_CHUNK = 65536
POLL_SECONDS = 1.0
MAX_LINE_BYTES = 64 * 1024
_PATH_RE = re.compile(r"/[^\s'\"]+")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def safe_error(e: BaseException) -> str:
    """오류 문구에서 절대 경로를 지운다(상태 JSON 에 실린다)."""
    text = getattr(e, "strerror", None) or str(e)
    text = _PATH_RE.sub("<path>", text)[:160]
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


class RunnerError(Exception):
    """프로세스를 띄우지 못했다(argv[0] 없음 · 로그 파일 못 엶).

    `MaterializeError` 와 같은 규칙이다: **문구가 아니라 코드**(`outcome.PREFLIGHT_CODES`)를 들고,
    원문은 `log` 로 보호된 잡 로그에만 간다. `argv[0]` 이 공개 요약에 들어가면 안 되는 이유가
    바로 여기다 — 프리셋의 명령은 비공개 SDK 의 절대 경로이거나 토큰이 박힌 러너 이름일 수 있고,
    `/api/status` 는 기본 설정에서 토큰 없이 읽힌다(PLAN 「보안」).
    """

    def __init__(self, code: str, /, *, log: str = "", **args: Any) -> None:
        self.code = code
        self.args_public = args
        self.log = log
        super().__init__(outcome.render(code, args) or code)


def _launch_error(e: OSError, argv0: str) -> RunnerError:
    """프로세스를 못 띄운 `OSError` → 코드. 고칠 곳이 다르다: 없는 명령은 프리셋의 `argv` 를,
    권한은 파일 모드를 고치라는 말이다. 나머지는 클래스 이름만 인자로 남긴다(`E2BIG` · FD 고갈).

    코드를 표에서 꺼내지 않고 그 자리에 적는 이유: 예외 자리를 정적으로 훑어 「표에 없는 코드로
    잡을 닫으려는 자리」를 잡는 시험이 있다. 오타 하나가 레인을 `down` 으로 만든다.
    """
    # 원문에는 `argv[0]` 을 그대로 둔다 — 잡 로그는 토큰이 있어야 읽고, 이게 없으면 「명령을 못
    # 찾았다」만 남아 어느 명령인지 모른 채로 프리셋을 뒤져야 한다.
    log = f"cannot start {argv0!r}: {safe_error(e)}"
    if e.errno == errno.ENOENT:
        return RunnerError("launch_executable_missing", log=log)
    if e.errno in (errno.EACCES, errno.EPERM):
        return RunnerError("launch_permission_denied", log=log)
    return RunnerError("launch_failed", error=type(e).__name__, log=log)


class RequiredToolMissing(Exception):
    """프리셋 `requires` 의 도구가 잡의 최종 환경에 없다 — 프로세스는 뜨지 않았다(M5j G4).

    `tool` 은 선언된 그대로(절대경로일 수 있다), `public_name` 은 상태·요약·로그에 싣는 이름 —
    언제나 basename 이다(설정이 basename 없는 경로를 거절한다, M5l S2). 선언한 경로도 PATH 값도
    어디에도 들지 않는다(M5l S3).
    """

    def __init__(self, tool: str):
        super().__init__(f"required tool {public_tool_name(tool)} is missing")
        self.tool = tool
        self.public_name = public_tool_name(tool)


def public_tool_name(tool: str) -> str:
    """선언 항목의 공개 이름 — 이름이면 그대로, 절대경로면 basename. 설정 검증이 basename 이
    비는 선언을 거절하므로 여기서 경로로 물러나지 않는다(M5l S2)."""
    return os.path.basename(tool)


@dataclass(frozen=True)
class RunSpec:
    """실행에 필요한 것 전부 — 서버 DB 의 잡이든 claim 응답이든 여기로 정규화한다."""

    job_id: int
    preset_name: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    env_passthrough: tuple[str, ...]
    timeout_seconds: int | None
    inputs: Mapping[str, Any]
    requester_label: str
    source: Source
    workspace: Path
    log_path: Path
    grace_seconds: int = 10
    #: 시작 전에 최종 환경에서 찾아야 하는 도구(이름 또는 절대경로). 비어 있으면 검사 없음.
    requires: tuple[str, ...] = ()
    #: stdout 을 로그 파일과 관찰자에게 주기 **전에** 거치는 마스킹(릴리스 비밀 → `****`).
    #: None 이면 그대로. 줄 단위 배치에 적용되므로 한 값이 줄을 넘지 않는 한 조각이 안 남는다.
    mask: Callable[[bytes], bytes] | None = None
    #: claim 이 얼린 표식(M5t-b 결정 257 · 331). 참이면 감독자 아래에서 돌고 끝에 그룹을 거둔다
    irreversible: bool = False
    #: claim 이 얼린 끊김 유예 G_J(원격 잡만). 로컬 잡은 None
    disconnect_grace_seconds: int | None = None


# ── macOS Command Line Tools shim (M5t-a 결정 246) ─────────────────────────────
#: shim 이 사는 폴더(바로 아래만 본다). 시험은 이 값과 아래 둘을 가짜로 바꾼다
SHIM_DIR = "/usr/bin/"
#: shim 을 판별하는 플랫폼 — macOS 에서만 뜻이 있다
SHIM_PLATFORM = sys.platform
#: CLT 가 깔렸는지 묻는 명령(`xcode-select -p`). 실패하면 shim 은 「없음」이다
XCODE_SELECT = "/usr/bin/xcode-select"
#: shim 실행 파일이 링크하는 라이브러리 이름 — 도구 이름 목록 대신 바이트로 판별한다
SHIM_MARKER = b"libxcselect"
#: `xcode-select -p` 답을 이만큼 다시 쓴다. CLT 를 깐 뒤 서버를 재시작하지 않아도 1분 안에 바뀐다
CLT_CACHE_SECONDS = 60.0
CLT_NOTE = "missing (needs Command Line Tools)"
_CLT_CACHE: dict[str, tuple[float, bool]] = {}
_CLT_LOCK = threading.Lock()


def is_clt_shim(path: str) -> bool:
    """`/usr/bin/` 바로 아래이고 바이트에 `libxcselect` 가 든 실행 파일인가(macOS 만).

    이름 목록으로 판별하지 않는다. Apple Silicon Mac 한 대의 실측에서 `git` · `make` · `xcrun`
    에는 이 바이트가 있었고 `/usr/bin/java` · `ruby` · `perl` 에는 없었다 — Java 스텁은 이 방법으로
    판별되지 않는다(명세 §4.5). `xcode-select` 자신은 CLT 를 묻는 데 쓰므로 판별에서 뺀다.
    """
    if not SHIM_PLATFORM.startswith("darwin"):
        return False
    if os.path.basename(path) == "xcode-select":
        return False
    folder = SHIM_DIR.rstrip("/") or "/"
    if os.path.dirname(os.path.abspath(path)) != folder:
        return False
    try:
        with open(path, "rb") as fh:
            tail = b""
            while chunk := fh.read(READ_CHUNK):
                if SHIM_MARKER in tail + chunk:
                    return True
                tail = chunk[-len(SHIM_MARKER) :]
    except OSError:
        return False
    return False


def clt_installed() -> bool:
    """`xcode-select -p` 가 성공하나 — 1분 동안 같은 답을 쓴다. 명령이 없거나 멈추면 False."""
    now = time.monotonic()
    with _CLT_LOCK:
        cached = _CLT_CACHE.get(XCODE_SELECT)
        if cached is not None and now - cached[0] < CLT_CACHE_SECONDS:
            return cached[1]
    try:
        proc = subprocess.run(
            [XCODE_SELECT, "-p"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            env={"PATH": "/usr/bin:/bin"},
        )
        ok = proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    with _CLT_LOCK:
        _CLT_CACHE[XCODE_SELECT] = (now, ok)
    return ok


def tool_blocked(path: str) -> bool:
    """실행하면 CLT 설치 대화상자를 띄우는 shim 인가 — 판별 함수는 이것 하나다.

    `measure_tools` · `missing_tools`(그래서 서버의 `preset_env` 와 `rcm check` 의 도구 행도) ·
    워커 프로브(`hostsample.run_probes`) · `rcm doctor` 가 모두 이 함수를 거친다. 헤드리스
    서비스에서 그 창이 프로브 주기마다 되풀이되지 않게 실행하지 않고 「없음」으로 센다.
    """
    return is_clt_shim(path) and not clt_installed()


def find_tool(name: str, path: str) -> str | None:
    """`shutil.which` 에 shim 규칙을 더한 것 — 막힌 shim 이면 None."""
    found = shutil.which(name, path=path)
    if found is None or tool_blocked(found):
        return None
    return found


def missing_note(name: str, env: Mapping[str, str]) -> str | None:
    """`name` 이 못 쓰는 shim 이라 없는 것이면 그 사연, 아니면 None(사람용 표의 한 칸)."""
    raw = env.get("PATH", "")
    found = shutil.which(name, path=raw) if raw or os.path.isabs(name) else None
    if found is not None and tool_blocked(found):
        return CLT_NOTE
    return None


def missing_tools(
    requires: tuple[str, ...], env: Mapping[str, str], cwd: str | os.PathLike[str] | None = None
) -> list[str]:
    """`requires` 중 `env` 의 PATH 에서 못 찾은 것 — 선언 순서대로.

    **잡의 PATH 가 정본이다.** PATH 가 없으면 빈 PATH 로 본다(`shutil.which` 는 `path=None`
    이면 검사 프로세스의 `os.environ["PATH"]` 로 물러난다 — 그게 launchd 서비스에서 「셸에선
    되는데 잡에선 안 되는」 사고를 숨긴다). 절대경로는 PATH 와 무관하게 그 자리에서 본다.

    PATH 의 상대 항목(`tools`·`.`·빈 항목)은 `cwd` — 잡이라면 **워크스페이스** — 기준으로 푼다.
    프로세스는 `cwd=spec.workspace` 로 뜨므로 그 셸이 보는 곳이 거기다. 검사 프로세스의 cwd 로
    보면 서버 폴더에만 있는 도구로 통과하고(fail-open), 워크스페이스 안의 도구는 못 찾는다
    (M5l S1 · PR #109 리뷰 B). `cwd` 가 None 이면 이 프로세스의 cwd — `rcm check` 의 셸 검사.
    """
    base = os.fspath(cwd) if cwd is not None else os.getcwd()
    raw = env.get("PATH", "")
    entries = [os.path.join(base, entry) for entry in raw.split(os.pathsep)] if raw else []
    path = os.pathsep.join(entries)
    return [name for name in requires if find_tool(name, path) is None]


def measure_tools(requires: tuple[str, ...], env: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """능력 보고용 측정(M5n §4.3 5번) — (없는 것, 작업 공간에만 있을 수 있는 것), 선언 순서대로.

    잡이 오기 **전에** 잰다. 그래서 PATH 의 **절대 항목**과 절대경로 선언만 본다. 상대
    항목(`tools`·`.`·빈 항목)은 잡의 워크스페이스 기준이라 지금은 알 수 없다 — 못 찾았는데
    PATH 에 상대 항목이 있으면 「없다」가 아니라 「작업 공간에 있을지 모른다」(결정 93).
    측정 프로세스의 cwd 로 풀지 않는다(그건 서버 폴더에만 있는 도구로 통과하는 fail-open 이다).
    PATH 가 없으면 빈 PATH 다 — 이 프로세스의 PATH 로 물러나지 않는다(`missing_tools` 와 같다).
    """
    raw = env.get("PATH", "")
    entries = raw.split(os.pathsep) if raw else []
    absolute = [e for e in entries if e and os.path.isabs(e)]
    has_relative = any(not (e and os.path.isabs(e)) for e in entries)
    path = os.pathsep.join(absolute)
    missing: list[str] = []
    workspace: list[str] = []
    for name in requires:
        if os.path.isabs(name):
            if not (os.path.isfile(name) and os.access(name, os.X_OK)) or tool_blocked(name):
                missing.append(name)
            continue
        if find_tool(name, path) is not None:
            continue
        (workspace if has_relative else missing).append(name)
    return missing, workspace


def _preflight_lines(requires: tuple[str, ...], missing: list[str]) -> list[str]:
    """잡 로그에 남길 판정 줄 — 공개 이름(basename)과 ok/missing 만. 선언한 절대경로도 PATH 값도
    찍지 않는다(명세 §2 G4 · M5l S3)."""
    if missing:
        return [f"[rcm] required tool {public_tool_name(name)}: missing" for name in missing]
    verdicts = " · ".join(f"{public_tool_name(name)} ok" for name in requires)
    return [f"[rcm] required tools: {verdicts}"]


class RunObserver(Protocol):
    """관찰자 — 로컬은 DB 에 직접, 원격은 HTTP 로 서버에.

    M5t-b(결정 333): `spawning()` 은 Popen 직전 확인을 지난 뒤 · Popen 바로 앞, `spawned(pid)` 는
    Popen 직후에 부른다(원격 워커가 `proc.json` 을 두 번 쓴다). 없는 관찰자는 건너뛴다."""

    def phase(self, phase: str) -> None: ...
    def output(self, data: bytes) -> None: ...
    def should_cancel(self) -> bool: ...
    def should_stop(self) -> bool: ...


@dataclass
class RunResult:
    rc: int | None
    started: datetime
    finished: datetime
    cancelled: bool = False
    timed_out: bool = False
    lost: bool = False  # should_stop 으로 끊었다
    term_sent_at: datetime | None = field(default=None, repr=False)
    kill_sent: bool = field(default=False, repr=False)
    #: 표식 잡의 프로세스 그룹이 사라졌다는 확인(S7 의 ESRCH, M5t-b 결정 330). 프로세스를 띄우지
    #: 않았으면 참, 일반 잡은 None(모름 — 볼 필요가 없다)
    group_gone: bool | None = None


def build_env(spec: RunSpec, environ: Mapping[str, str]) -> dict[str, str]:
    """프리셋 env_passthrough → preset.env → RCM_* → 입력(`RCM_INPUT_*`) 순으로 덮어쓴다."""
    env: dict[str, str] = {}
    for key in spec.env_passthrough:
        if key in environ:
            env[key] = environ[key]
    env.update(spec.env)
    env.update(
        {
            "RCM_JOB_ID": str(spec.job_id),
            "RCM_PRESET": spec.preset_name,
            "RCM_REQUESTER": spec.requester_label,
            "RCM_SOURCE_MODE": spec.source.mode,
            "RCM_REF": spec.source.ref or "",
            "RCM_BASE_SHA": spec.source.base_sha or "",
            "RCM_DIRTY": "1" if spec.source.dirty else "0",
            "RCM_WORKSPACE": str(spec.workspace),
            "RCM_LOG_FILE": str(spec.log_path),
        }
    )
    env.update(env_for_inputs(dict(spec.inputs)))
    return env


def signal_group(proc: subprocess.Popen, sig: int) -> None:
    """프로세스 그룹 전체에(손자까지). 그룹이 없으면 프로세스에만."""
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


#: 감독자 파일(M5t-b §4.13). `-m` 이 아니라 파일 경로로 띄운다 — `-I` 가 `PYTHONPATH` 를 버리기 때문
SUPERVISOR_PATH = Path(__file__).resolve().with_name("job_supervisor.py")
#: 감독자가 명령을 띄우지 못했을 때의 종료와 표지(감독자 파일의 같은 이름과 같다 — 시험이 잠근다)
SUPERVISOR_NOT_STARTED_EXIT = 125
SUPERVISOR_MARKER = b"[rcm] supervisor: not started: "
#: S7 — 리더가 끝난 뒤 그룹 소멸(ESRCH)을 확인하는 창
GROUP_GONE_WAIT_SECONDS = 2.0


def supervised_argv(spec: RunSpec) -> list[str]:
    """표식 잡의 argv — 감독자로 감싼다(결정 331 · 339-c (4)). 파일이 없으면 깨진 설치다."""
    if not SUPERVISOR_PATH.is_file():
        raise RunnerError(
            "launch_failed", error="SupervisorMissing", log=f"missing {SUPERVISOR_PATH.name}"
        )
    return [
        sys.executable,
        "-I",  # mutcheck: supervisor-not-isolated
        str(SUPERVISOR_PATH),
        "--parent",
        str(os.getpid()),
        "--grace",
        str(spec.grace_seconds),
        "--",
        *spec.argv,
    ]


def _not_started(result: RunResult, head: bytes) -> RunnerError | None:
    """리더가 125 로 끝났고 **파이프 첫 바이트부터** 표지가 나왔을 때만 감독자의 미시작으로 읽는다.
    러너 플래그가 있으면 그 결과(None), `launch_*` 면 그 코드, 아니면 `SupervisorNotStarted`."""
    if result.rc != SUPERVISOR_NOT_STARTED_EXIT or not head.startswith(SUPERVISOR_MARKER):
        return None
    reason = head[len(SUPERVISOR_MARKER) :].split(b"\n", 1)[0].decode("ascii", "replace")
    log = "the supervisor could not start the command"
    if reason == "launch_executable_missing":
        return RunnerError("launch_executable_missing", log=log)
    if reason == "launch_permission_denied":
        return RunnerError("launch_permission_denied", log=log)
    if reason == "launch_failed":
        return RunnerError("launch_failed", error="SupervisorLaunch", log=reason)
    if result.lost or result.cancelled or result.timed_out:
        return None
    return RunnerError("launch_failed", error="SupervisorNotStarted", log=reason)


def run_job(
    spec: RunSpec,
    observer: RunObserver,
    *,
    now_fn: Callable[[], datetime] = _utcnow,
    environ: Mapping[str, str] | None = None,
    materialize: Callable[[RunSpec], None] | None = None,
    mono_fn: Callable[[], float] = time.monotonic,
) -> RunResult:
    """자재화(있으면) → `requires` 검사 → `phase executing` → Popen → 펌프 → 결과. 종료 규칙은
    호출자가 `worker.outcome_for` 로 정한다. `MaterializeError` 는 그대로 올린다(호출자가 failed
    로). 도구가 없으면 `RequiredToolMissing` — 프로세스를 띄우지 않는다(M5j G4)."""
    if materialize is not None:
        materialize(spec)
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    env = build_env(spec, os.environ if environ is None else environ)
    try:
        log = spec.log_path.open("ab")
    except OSError as e:
        raise RunnerError(
            "log_unavailable", error=type(e).__name__, log=f"cannot open log file: {safe_error(e)}"
        ) from e
    with log:
        if spec.requires:
            # 자재화가 길었다면 그사이 취소·종료 요청이 와 있을 수 있다 — 그게 이긴다. 프로세스도
            # 판정 줄도 없이 돌려주고, 호출자가 여느 취소·유실처럼 닫는다(M5l S4 · 리뷰 B P2).
            # 없는 도구가 사용자의 취소를 `tool_missing` 으로 덮어서는 안 된다. 프로세스를 띄우지
            # 않았으니 그룹은 없다(`group_gone` 참) — 아래 Popen 직전 확인과 같다. 빠지면 표식 잡의
            # finish 가 `group_gone` 없이 가서 서버가 그룹을 홀드한다(RL41 (c))
            if observer.should_stop():
                at = now_fn()
                # mutcheck: requires-early-stop-group-unknown
                return RunResult(rc=None, started=at, finished=at, lost=True, group_gone=True)
            if observer.should_cancel():
                at = now_fn()
                return RunResult(rc=None, started=at, finished=at, cancelled=True, group_gone=True)
            # Popen 직전, **최종 환경**에서 — 프로세스가 볼 PATH 그대로, 상대 항목은 워크스페이스
            # 기준. 같은 줄을 관찰자에게도 주어 원격 워커의 서버 로그에도 남는다(마커는 아니다).
            missing = missing_tools(spec.requires, env, spec.workspace)
            batch = "".join(f"{ln}\n" for ln in _preflight_lines(spec.requires, missing))
            data = batch.encode("utf-8", errors="replace")
            log.write(data)
            log.flush()
            observer.output(data)
            if missing:
                raise RequiredToolMissing(missing[0])
        started = now_fn()
        observer.phase(PHASE_EXECUTING)
        # Popen 직전 확인 — requires 와 상관없이(M5t-b 결정 333). 준비가 길었다면 그사이 자기 멈춤
        # · 취소가 왔을 수 있다. 확인과 Popen 사이에는 네트워크가 없다
        if observer.should_stop():  # mutcheck: popen-check-only-with-requires
            return RunResult(rc=None, started=started, finished=started, lost=True, group_gone=True)
        if observer.should_cancel():
            return RunResult(
                rc=None, started=started, finished=started, cancelled=True, group_gone=True
            )
        argv = supervised_argv(spec) if spec.irreversible else list(spec.argv)
        spawning = getattr(observer, "spawning", None)
        if callable(spawning):
            spawning()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(spec.workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as e:
            raise _launch_error(e, spec.argv[0]) from e
        spawned = getattr(observer, "spawned", None)
        if callable(spawned):
            spawned(proc.pid)
        result, head = _pump(spec, proc, log, observer, started, now_fn, mono_fn)
    if spec.irreversible:
        failure = _not_started(result, head)
        if failure is not None:
            failure.group_gone = result.group_gone  # type: ignore[attr-defined]
            raise failure
    return result


def _pump(
    spec: RunSpec,
    proc: subprocess.Popen,
    log: Any,
    observer: RunObserver,
    started: datetime,
    now_fn: Callable[[], datetime],
    mono_fn: Callable[[], float] = time.monotonic,
) -> tuple[RunResult, bytes]:
    """stdout 을 파일과 관찰자로 흘리며 취소·타임아웃·정지를 본다. 프로세스가 끝날 때까지.

    점검 박자와 SIGTERM→SIGKILL 은 단조 시계로, `started` · `finished` · 잡 시한은 벽시계로 잰다
    (결정 333 — 잡 시한까지 단조로 바꾸면 macOS 잠자기가 시한에서 빠진다). 첫 점검은 Popen 직후
    곧바로 한다. 표식 잡은 리더가 끝나면 남은 그룹을 거두고(S7) `group_gone` 을 정한다. 두 번째
    값은 파이프의 첫 바이트들이다(감독자 표지를 읽는다)."""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    chunks: queue.Queue[bytes | None] = queue.Queue()

    def reader() -> None:
        try:
            while True:
                data = os.read(fd, READ_CHUNK)
                if not data:
                    break
                chunks.put(data)
        except OSError:
            pass
        finally:
            chunks.put(None)

    threading.Thread(target=reader, name=f"rcm-reader-{spec.job_id}", daemon=True).start()
    result = RunResult(rc=None, started=started, finished=started)
    buf = b""
    head = b""
    eof = False
    grace = float(spec.grace_seconds)
    timeout = timedelta(seconds=spec.timeout_seconds) if spec.timeout_seconds else None
    term_mono: list[float] = []  # 러너가 보낸 첫 SIGTERM 의 단조 시각

    def term(now: datetime) -> None:
        signal_group(proc, signal.SIGTERM)
        result.term_sent_at = now
        term_mono.append(mono_fn())  # mutcheck: runner-grace-wall-clock

    def check(now: datetime) -> None:
        forced = result.cancelled or result.timed_out or result.lost
        if not forced and observer.should_stop():
            result.lost = True
            term(now)
        elif not forced and observer.should_cancel():
            result.cancelled = True
            term(now)
        elif not forced and timeout is not None and now - started > timeout:
            result.timed_out = True
            term(now)
        if spec.irreversible and not term_mono and proc.poll() is not None:
            # S7: 리더(감독자)가 끝났는데 파이프를 쥔 멤버가 남았다 — 지금 SIGTERM
            term(now)
        if term_mono and not result.kill_sent and mono_fn() - term_mono[0] >= grace:
            signal_group(proc, signal.SIGKILL)
            result.kill_sent = True

    check(now_fn())  # 첫 점검은 곧바로(결정 333 — 1초 기다리지 않는다)
    last_check = mono_fn()
    while not eof:
        try:
            data = chunks.get(timeout=POLL_SECONDS)
        except queue.Empty:
            data = b""
        now = now_fn()
        if data is None:
            eof = True
        elif data:
            if len(head) < len(SUPERVISOR_MARKER) + 64:
                head += data[: len(SUPERVISOR_MARKER) + 64 - len(head)]
            buf += data
            *lines, buf = buf.split(b"\n")
            if len(buf) > MAX_LINE_BYTES:  # 개행 없는 긴 출력은 잘라서 흘린다
                lines.append(buf)
                buf = b""
            if lines:
                batch = b"".join(raw + b"\n" for raw in lines)
                if spec.mask is not None:
                    batch = spec.mask(batch)  # 파일에도 관찰자에게도 마스킹된 바이트만 간다
                log.write(batch)
                log.flush()
                observer.output(batch)
        # ── 취소 · 타임아웃 · 정지 (1초마다, 단조) ──
        if mono_fn() - last_check >= POLL_SECONDS or eof:
            last_check = mono_fn()
            check(now)
        if not eof and result.kill_sent and proc.poll() is not None:
            break  # 손자가 파이프를 잡고 있어도 더 기다리지 않는다
    if buf:
        tail = buf + b"\n"
        if spec.mask is not None:
            tail = spec.mask(tail)
        log.write(tail)
        log.flush()
        observer.output(tail)
    try:
        proc.wait(timeout=spec.grace_seconds + 5)
    except subprocess.TimeoutExpired:
        signal_group(proc, signal.SIGKILL)
        proc.wait()
    try:
        proc.stdout.close()
    except OSError:
        pass
    result.rc = proc.returncode
    if spec.irreversible:
        result.group_gone = _reap_group(proc, result, term_mono, grace, mono_fn)
    result.finished = now_fn()
    return result, head


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_group(
    proc: subprocess.Popen,
    result: RunResult,
    term_mono: list[float],
    grace: float,
    mono_fn: Callable[[], float],
) -> bool:
    """S7(결정 339-c (2)): 리더가 끝난 뒤 남은 그룹 멤버를 거둔다. 러너가 이미 SIGTERM 을 보냈으면
    다시 보내지 않고 **그 첫 SIGTERM** + grace 에 SIGKILL, 안 보냈으면 지금 SIGTERM. 그 뒤 2초 안에
    ESRCH 면 그룹이 사라졌다(참). 리더는 이미 거뒀다 — 좀비 리더는 그룹에 남기 때문이다."""
    pgid = proc.pid
    leader_end = mono_fn()
    if not _group_alive(pgid):
        return True
    if not term_mono:
        signal_group(proc, signal.SIGTERM)
        term_mono.append(mono_fn())
    # 시한은 첫 SIGTERM 부터다. 리더 끝부터 재면 2·grace 가 나온다(3판 꼴, RL51)
    kill_at = term_mono[0] + grace  # mutcheck: reap-grace-from-leader-end
    del leader_end  # 위 변이가 쓰는 이름 — 시한의 기준이 아니다
    while _group_alive(pgid):
        if not result.kill_sent and mono_fn() >= kill_at:
            signal_group(proc, signal.SIGKILL)
            result.kill_sent = True
        if result.kill_sent and mono_fn() >= kill_at + GROUP_GONE_WAIT_SECONDS:
            return False
        time.sleep(0.05)
    return True


__all__ = [
    "MAX_LINE_BYTES",
    "POLL_SECONDS",
    "READ_CHUNK",
    "RequiredToolMissing",
    "RunObserver",
    "RunResult",
    "RunSpec",
    "RunnerError",
    "build_env",
    "missing_tools",
    "public_tool_name",
    "run_job",
    "safe_error",
    "signal_group",
]
