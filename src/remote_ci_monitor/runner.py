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
- 종료 코드로 예외를 내지 않는다. 시작 실패는 `RunnerError`("cannot start …").
"""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

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
    """프로세스를 띄우지 못했다(argv[0] 없음 · 로그 파일 못 엶). 문구에 경로 없음."""


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


class RunObserver(Protocol):
    """관찰자 — 로컬은 DB 에 직접, 원격은 HTTP 로 서버에."""

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


def run_job(
    spec: RunSpec,
    observer: RunObserver,
    *,
    now_fn: Callable[[], datetime] = _utcnow,
    environ: Mapping[str, str] | None = None,
    materialize: Callable[[RunSpec], None] | None = None,
) -> RunResult:
    """자재화(있으면) → `phase executing` → Popen → 펌프 → 결과. 종료 규칙은 호출자가
    `worker.outcome_for` 로 정한다. `MaterializeError` 는 그대로 올린다(호출자가 failed 로)."""
    if materialize is not None:
        materialize(spec)
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    env = build_env(spec, os.environ if environ is None else environ)
    try:
        log = spec.log_path.open("ab")
    except OSError as e:
        raise RunnerError(f"cannot open log file: {safe_error(e)}") from e
    with log:
        started = now_fn()
        observer.phase(PHASE_EXECUTING)
        try:
            proc = subprocess.Popen(
                list(spec.argv),
                cwd=str(spec.workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as e:
            raise RunnerError(f"cannot start {spec.argv[0]!r}: {safe_error(e)}") from e
        result = _pump(spec, proc, log, observer, started, now_fn)
    return result


def _pump(
    spec: RunSpec,
    proc: subprocess.Popen,
    log: Any,
    observer: RunObserver,
    started: datetime,
    now_fn: Callable[[], datetime],
) -> RunResult:
    """stdout 을 파일과 관찰자로 흘리며 취소·타임아웃·정지를 본다. 프로세스가 끝날 때까지."""
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
    eof = False
    last_check = started
    grace = timedelta(seconds=spec.grace_seconds)
    timeout = timedelta(seconds=spec.timeout_seconds) if spec.timeout_seconds else None
    while not eof:
        try:
            data = chunks.get(timeout=POLL_SECONDS)
        except queue.Empty:
            data = b""
        now = now_fn()
        if data is None:
            eof = True
        elif data:
            buf += data
            *lines, buf = buf.split(b"\n")
            if len(buf) > MAX_LINE_BYTES:  # 개행 없는 긴 출력은 잘라서 흘린다
                lines.append(buf)
                buf = b""
            if lines:
                batch = b"".join(raw + b"\n" for raw in lines)
                log.write(batch)
                log.flush()
                observer.output(batch)
        # ── 취소 · 타임아웃 · 정지 (1초마다) ──
        if (now - last_check).total_seconds() >= POLL_SECONDS or eof:
            last_check = now
            forced = result.cancelled or result.timed_out or result.lost
            if not forced and observer.should_stop():
                result.lost = True
                signal_group(proc, signal.SIGTERM)
                result.term_sent_at = now
            elif not forced and observer.should_cancel():
                result.cancelled = True
                signal_group(proc, signal.SIGTERM)
                result.term_sent_at = now
            elif not forced and timeout is not None and now - started > timeout:
                result.timed_out = True
                signal_group(proc, signal.SIGTERM)
                result.term_sent_at = now
            if result.term_sent_at is not None and not result.kill_sent:
                if now - result.term_sent_at >= grace:
                    signal_group(proc, signal.SIGKILL)
                    result.kill_sent = True
        if not eof and result.kill_sent and proc.poll() is not None:
            break  # 손자가 파이프를 잡고 있어도 더 기다리지 않는다
    if buf:
        log.write(buf + b"\n")
        log.flush()
        observer.output(buf + b"\n")
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
    result.finished = now_fn()
    return result


__all__ = [
    "MAX_LINE_BYTES",
    "POLL_SECONDS",
    "READ_CHUNK",
    "RunObserver",
    "RunResult",
    "RunSpec",
    "RunnerError",
    "build_env",
    "run_job",
    "safe_error",
    "signal_group",
]
