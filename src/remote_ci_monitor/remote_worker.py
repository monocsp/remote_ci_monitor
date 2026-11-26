"""원격 워커 프로세스(M5b-3) — `rcm worker --server URL --pool NAME --lanes N`.

루프:
- 등록(`/worker/register`; 서버에 못 닿으면 5초마다 재시도, 409 는 그대로 출력하고 종료 2).
  버전 불일치 409 의 본문이 다른 `server_version` 을 실으면 `WorkerFollow` 를 던진다 —
  `cmd_worker` 가 그 버전으로 따라간다(M5q ②). 레인의 재등록이 막히면 `run()` 은 종료 2 다.
- 드레인(M5q ③ · ④ · PR B2): heartbeat 의 `server_version` 이 다르거나 레인의 재등록이 버전
  불일치 409 면 `draining` 을 켠다. 레인은 새 claim 을 하지 않고 기다리되 받은 잡은 끝까지 돌려
  보고한다. 모든 레인이 잡 없이 기다리면 `run()` 이 `stopping` 을 켜고 `restart_for` 를 남긴다 —
  `cmd_worker` 가 설치본 부트로더로 같은 PID 에서 다시 실행한다. 도는 잡은 하나도 잃지 않는다.
- heartbeat 스레드: `heartbeat_seconds` 마다 `{jobs: [도는 잡…], host_sample}`. 응답 `cancel` 은
  레인에 전달, `paused` 면 claim 을 쉰다. 연속 실패는 경고 한 줄 — 잡은 계속 돈다(서버가 timeout
  뒤 lost 로 닫으면 finish 가 409 를 받고 워크스페이스만 정리한다).
- 레인 스레드 × lanes: claim(long-poll) → 스냅샷 받기(`extract_tree`) 또는 git_ref fetch →
  `runner.run_job`(관찰자가 phase·raw 로그를 HTTP 로) → finish. 보고 실패는 1·2·4초 재시도.
- SIGTERM/SIGINT: 레인의 `should_stop` → 도는 잡 SIGTERM → grace → KILL → `finish lost`
  (summary `worker stopped`) → 종료 0. 두 번째 신호는 즉시 종료.

서버에서 워커로 오는 연결은 없다(NAT 뒤에서도 된다). 로그·표본은 데이터로만 보낸다.
"""

from __future__ import annotations

import copy
import dataclasses
import fcntl
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor import __version__, follow
from remote_ci_monitor.client import ClientError, WorkerClient
from remote_ci_monitor.collect import CollectResult, collect
from remote_ci_monitor.config import WorkerConfig
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import (
    CANCELLED,
    EXHAUSTED,
    FAILED,
    LOST,
    MODE_GIT_REF,
    MODE_TREE,
    PHASE_MATERIALIZING,
    RETRIED,
    SUCCEEDED,
    TIMED_OUT,
    Job,
    Requester,
    Source,
)
from remote_ci_monitor.core.outcome import summary as outcome_summary
from remote_ci_monitor.hostsample import HostSampler
from remote_ci_monitor.materialize import MaterializeError, extract_tree, prepare_git_ref
from remote_ci_monitor.runner import RequiredToolMissing, RunnerError, RunSpec, run_job
from remote_ci_monitor.worker import format_limit

REGISTER_RETRY_SECONDS = 5.0
CLAIM_MIN_INTERVAL = 1.0  # 빈 204 가 이보다 빨리 오면 이만큼 쉰다
RETRY_WAIT_SECONDS = 2.0  # 503 · 연결 실패 뒤 다시 claim 하기까지
LOG_FLUSH_SECONDS = 1.0
LOG_BATCH_BYTES = 256 * 1024
WORKSPACE_KEEP_DAYS = 7
STOP_SUMMARY = "worker stopped"
#: 연결 실패가 이만큼 **연속**이면 같은 망에서 서버를 다시 찾는다(`server = "auto"` 일 때만, M5n §9)
REDISCOVER_AFTER_FAILURES = 5
#: 등록 · 능력 보고에 싣는 호스트 사실의 키(서버의 `capabilities.HOST_FACT_KEYS` 와 같다)
HOST_KEYS = (
    "os",
    "os_release",
    "os_like",
    "arch",
    "rosetta",
    "container",
    "root",
    "package_manager",
    "python",
)
#: 드레인 중인 레인이 제 잡의 보고 큐가 비기를 기다리는 상한(M5q §14 B질문11 · M5t-b §4.4). 넘으면
#: 오늘처럼 exec 한다 — 큐는 디스크에 남아 새 이미지가 등록 전에 죽은 세션의 큐로 보낸다
DRAIN_REPORT_SECONDS = 60
#: 드레인 중인 레인이 claim 없이 한 번 기다리는 시간(§14 B질문8). 드레인이 취소되면 다시 claim 한다
DRAIN_WAIT_SECONDS = 1.0
DRAINING = "draining: no new claims, restarting when running jobs finish"
#: 서버가 이 워커 프로토콜 판을 받아야 등록한다(M5t-b 결정 270)
WORKER_PROTOCOL = 2
#: 프로토콜 불일치의 종료 코드 — 감독자가 되살린다(결정 258). 펜싱은 0(되살리지 않는다)
EXIT_PROTOCOL = 3
PROTOCOL_OLD_SERVER = (
    "the server speaks worker protocol 1 — upgrade rcm on the server (this worker needs protocol 2)"
)
FENCED = "replaced by a newer worker process with the same token — stopping"
# ── M5t-b 자기 멈춤(§4.3 · 결정 320 · 333 · 335) ──
#: 감시 스레드의 틱. heartbeat 루프와 따로 돈다 — 잠에서 깬 첫 틱이 판정한다(RL26)
WATCHDOG_TICK_SECONDS = 1
#: heartbeat 요청 하나의 전체 기한 T_req. config 의 같은 이름과 같아야 한다(시험이 잠근다)
HEARTBEAT_DEADLINE_SECONDS = 15
#: 동시에 매달린 heartbeat 도우미의 상한 — 넘으면 그 회차를 보내지 않고 실패로 센다
HEARTBEAT_INFLIGHT_MAX = 4
#: m = T_req + grace_J + 이 여유(결정 320). 기본 grace 10 이면 m = 35 = config 의 하한 여유
SELF_STOP_SLACK_SECONDS = 10
#: W4 표 첫 행 — 이 세션의 finish 가 서버에 이미 기록된 끝 상태(2xx 만 잃었다)
FINISH_RECORDED = frozenset({SUCCEEDED, FAILED, TIMED_OUT, RETRIED, EXHAUSTED})
#: W4 표 마지막 행 — 원격 claim 된 잡이 될 수 없는 상태(서버 버그 신호)
_NOT_CLAIMED_STATES = frozenset({"expired", "scheduled", "queued", "uploading"})


class SnapshotUnreachable(MaterializeError):
    """스냅샷을 받는 중 rcm 서버와 끊겼다(status 0 · 503) — 확정 결함이 아니라 `lost` +
    `snapshot_unreachable` 이다(M5t-b 결정 259). 코드는 오늘의 다운로드 실패 그대로 들고 간다."""

    def __init__(self, *, log: str = "") -> None:
        super().__init__("snapshot_download_failed", log=log, status=0)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkerExit(Exception):
    """치명적 — 메시지 그대로 출력하고 종료 코드로."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class WorkerFollow(WorkerExit):
    """기동 등록이 버전 불일치 409 이고 서버가 다른 `server_version` 을 알려 왔다(M5q ② · §5.4).

    `WorkerExit` 의 하위 클래스라 이것을 모르는 자리(B2 이전 빌드의 레인)도 `WorkerExit` 로 잡아
    종료 2 로 끝난다 — 레인 스레드가 예외로 죽지 않는다(§14 B질문2). `.version` 은 형식 검사를
    지난 서버 버전이다."""

    def __init__(self, version: str, message: str):
        super().__init__(2, message)
        self.version = version


def _fmt_seconds(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


#: 잡이 끝난 뒤 로그 · 산출물이 HTTP 5xx 로 막혀 있으면 이만큼 지나 finish 를 먼저 보낸다(결정 324)
FINISH_HOLD_SECONDS = 300
#: 잡이 끝난 뒤 로그 · 산출물을 다시 보내는 창(결정 264). 지나면 finish 만 보내고, 그것도 못
#: 보내면 버린다
RETRANSMIT_SECONDS = 24 * 3600
#: 죽은 세션 D 의 큐가 서버 오류(HTTP 응답)를 받는 시간이 이만큼이면 큐를 디스크에 둔 채 등록한다
#: (결정 337 · §4.4 의 7). 연결 실패 시간은 세지 않는다
DEAD_SESSION_WAIT_SECONDS = 300
#: 잡마다 보내는 스레드의 백오프(§4.5) — 1초에서 두 배씩 30초까지. 도는 잡에는 횟수 상한이 없다
BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
#: 멈추는 중(신호 · 펜싱)이면 보내는 스레드가 이만큼 더 해 보고 나머지는 디스크에 남긴다 — 다음
#: 프로세스가 죽은 세션의 큐로 보낸다(§4.4 의 7). 옛 `REPORT_RETRIES` 의 합(1 + 2 + 4)과 같다
STOP_REPORT_SECONDS = 7.0
STREAM_FILE = "stream.log"
OUTBOX_FILE = "outbox.json"


class JobOutbox:
    """한 잡의 보고 큐(M5t-b §4.4 · §4.5, 결정 271 · 337). 세션 폴더의 `jobs/<id>/` 에 둔다.

    `stream.log` 는 서버로 가는 스트림 바이트 그대로다(가리기 전, F14). 그 길이가 그 잡 스트림의
    길이다. `outbox.json` 은 보낼 phase(마지막 하나), `acked`(서버가 2xx 로 확인한 오프셋 — 하한),
    finish 기록, 끝난 시각, 산출물 묶음, `done` 을 담는다. 폴더가 없으면(세션 폴더 없이 도는 시험)
    메모리에만 둔다. 쓰기에 실패하면 「스풀 깨짐」이고 그 뒤 바이트는 세기만 한다(§4.6
    `log_bytes`)."""

    def __init__(self, folder: Path | None):
        self.folder = folder
        self._mem = bytearray()
        self._fh: Any = None
        self._lock = threading.Lock()
        self.length = 0  # 그 잡이 낸 스트림 바이트(깨진 뒤에도 센다)
        self.written = 0  # 스풀에 실제로 있는 바이트
        self.broken = False
        self.acked = 0
        self.phase: str | None = None
        self.finish: dict[str, Any] | None = None
        self.ended_at: float | None = None
        self.bundle: dict[str, Any] | None = None
        self.log_stopped = False
        self.done = False

    # ── 디스크 ──

    @classmethod
    def load(cls, folder: Path) -> JobOutbox | None:
        """죽은 세션 · exec 전 이미지가 남긴 큐. `outbox.json` 이 없거나 깨졌으면 None."""
        data = _read_json(folder / OUTBOX_FILE)
        if data is None:
            return None
        ob = cls(folder)
        try:
            size = (folder / STREAM_FILE).stat().st_size
        except OSError:
            size = 0
        ob.broken = data.get("broken") is True
        ob.written = size
        counted = data.get("length")
        ob.length = max(size, counted) if isinstance(counted, int) and ob.broken else size
        acked = data.get("acked")
        ob.acked = acked if isinstance(acked, int) and 0 <= acked <= ob.length else 0
        phase = data.get("phase")
        ob.phase = phase if isinstance(phase, str) else None
        finish = data.get("finish")
        ob.finish = finish if isinstance(finish, dict) else None
        ended = data.get("ended_at")
        ob.ended_at = float(ended) if isinstance(ended, (int, float)) else None
        bundle = data.get("bundle")
        ob.bundle = bundle if isinstance(bundle, dict) else None
        ob.log_stopped = data.get("log_stopped") is True
        ob.done = data.get("done") is True
        return ob

    def save(self) -> None:
        if self.folder is None:
            return
        with self._lock:
            data = {
                "acked": self.acked,
                "phase": self.phase,
                "finish": self.finish,
                "ended_at": self.ended_at,
                "bundle": self.bundle,
                "log_stopped": self.log_stopped,
                "done": self.done,
                "broken": self.broken,
                "length": self.length,
            }
        try:
            _write_json(self.folder / OUTBOX_FILE, data)
        except OSError:
            pass  # 다음 저장이 다시 쓴다 — 디스크의 `acked` 는 하한일 뿐이다

    # ── 스트림 ──

    def append(self, data: bytes) -> None:
        """관찰자의 `output` — 펌프를 막지 않는다(네트워크는 보내는 스레드가)."""
        if not data:
            return
        with self._lock:
            self.length += len(data)
            if self.broken:
                return
            if self.folder is None:
                self._mem.extend(data)
                self.written += len(data)
                return
            try:
                if self._fh is None:
                    self.folder.mkdir(parents=True, exist_ok=True)
                    self._fh = (self.folder / STREAM_FILE).open("ab")
                self._fh.write(data)
                self._fh.flush()
                self.written += len(data)
            except OSError:
                self.broken = True  # 그 뒤 바이트는 세기만 한다 — 서버는 log_complete 거짓
        if self.broken:
            self.save()

    def read(self, start: int, limit: int) -> bytes:
        with self._lock:
            end = min(self.written, start + limit)
            if start >= end:
                return b""
            if self.folder is None:
                return bytes(self._mem[start:end])
        try:
            with (self.folder / STREAM_FILE).open("rb") as fh:
                fh.seek(start)
                return fh.read(end - start)
        except OSError:
            return b""

    def pending(self) -> int:
        with self._lock:
            return max(0, self.written - self.acked)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


class _JobSender:
    """잡 하나의 보고를 순서대로 보내는 스레드(결정 337 · V1-10). phase → 로그(`acked` 부터 256 KiB
    이하, 마지막 줄 끝에서 자름) → 산출물 → finish. 잡끼리는 서로 기다리지 않는다.

    `dead` 가 있으면 죽은 세션 D 의 큐다(§4.4 의 7): D 의 세션 id 로 보내고, 응답 표가 다르며(W5 를
    부르지 않고 D 로 재등록하지 않는다), 2xx 가 새 세션의 `ok_at` 을 올리지 않는다(V2-8)."""

    def __init__(
        self,
        proc: RemoteWorker,
        job_id: int,
        outbox: JobOutbox,
        *,
        client: Any = None,
        dead: DeadSession | None = None,
        resumed: bool = False,
    ):
        self.proc = proc
        self.job_id = job_id
        self.ob = outbox
        self._client = client
        self.dead = dead
        self.wake = threading.Event()
        self.halt = threading.Event()
        # 이 프로세스가 처음 보내는 로그가 디스크의 `acked` 에서 시작하면 먼저 묻는다(V2-13)
        self.query_first = resumed
        self.delay = 0.0
        self.last_5xx = False  # 지금 막힌 항목의 마지막 시도가 HTTP 5xx 였다(결정 324)
        self.failures = 0
        # 펜싱 — 이 프로세스의 큐는 프로세스의 펜싱(W5)을, D 의 큐는 그 큐가 받은 409 를 본다
        self.fenced = proc.fenced if dead is None else threading.Event()
        self.narrow_sent = False
        self.gave_up = False
        self._stop_deadline: float | None = None
        self._last_log = time.monotonic()
        self._abandon_said = False
        self.thread = threading.Thread(target=self.run, name=f"rcm-report-{job_id}", daemon=True)

    @property
    def client(self) -> Any:
        return self._client if self._client is not None else self.proc.client

    def start(self) -> _JobSender:
        self.thread.start()
        return self

    def alive(self) -> bool:
        return self.thread.is_alive()

    def finished(self) -> bool:
        """큐가 끝났다 — 보낼 것이 남지 않았다(`done`) 또는 끝나지 않은 잡의 로그를 다 보냈다(D)."""
        return self.ob.done

    # ── 고르기 ──

    def _log_due(self) -> bool:
        ob = self.ob
        if ob.log_stopped:
            return False
        if self.query_first:
            return True
        pending = ob.pending()
        if pending <= 0:
            return False
        if ob.finish is not None or self.dead is not None:
            return True
        due = pending >= LOG_BATCH_BYTES or time.monotonic() - self._last_log >= LOG_FLUSH_SECONDS
        return due and self._chunk() != b""

    def _art_due(self) -> bool:
        b = self.ob.bundle
        return self.ob.finish is not None and b is not None and b.get("state") == "pending"

    def _finish_first(self) -> bool:
        """finish 를 앞당길 때(결정 324 · 264). 잡이 끝났고 로그나 산출물이 앞에 막혔을 때만
        본다."""
        ob = self.ob
        if ob.finish is None or ob.ended_at is None:
            return False
        blocked = (not ob.log_stopped and ob.pending() > 0) or self._art_due()
        if not blocked:
            return False
        age = self.proc._epoch() - ob.ended_at
        if age >= RETRANSMIT_SECONDS:
            if not self._abandon_said:
                self._abandon_said = True
                self.proc.log(
                    f"#{self.job_id} report: log and artifacts not delivered in 24h — sending the"
                    " finish without them"
                )
            return True
        # 5xx 는 서버에 닿았다는 뜻이다. 연결 실패면 finish 도 갈 수 없으니 순서를 지킨다
        return age >= FINISH_HOLD_SECONDS and self.last_5xx  # mutcheck: finish-waits-forever

    def next_step(self) -> str | None:
        ob = self.ob
        if ob.done:
            return None
        if ob.finish is not None and self._finish_first():
            return "finish"
        if ob.phase is not None and not self.fenced.is_set():
            return "phase"
        if self._log_due():
            return "query" if self.query_first else "log"
        if ob.finish is None:
            return None
        if self._art_due():
            return "artifacts"
        return "finish"

    def _chunk(self) -> bytes:
        """`acked` 부터 256 KiB 이하, 마지막 `\\n` 에서 자른 조각 — 한 줄이 두 요청에 걸치지 않는다
        (F34 · F35). 줄 끝이 없으면 잡이 끝났을 때만 그대로 보낸다(러너는 줄 끝을 붙인다)."""
        ob = self.ob
        data = ob.read(ob.acked, LOG_BATCH_BYTES)
        cut = data.rfind(b"\n")
        if cut >= 0:
            return data[: cut + 1]
        if len(data) >= LOG_BATCH_BYTES or ob.finish is not None or self.dead is not None:
            return data
        return b""

    # ── 보내기 ──

    def run(self) -> None:
        try:
            while not self.ob.done and not self.halt.is_set():
                if self._stopping_past_deadline():
                    self.gave_up = True
                    break
                step = self.next_step()
                if step is None:
                    if self.dead is not None and self.ob.finish is None:
                        break  # D 의 끝나지 않은 잡 — 로그를 다 보냈다
                    self.wake.wait(LOG_FLUSH_SECONDS)
                    self.wake.clear()
                    continue
                if self.attempt(step):
                    self.delay = 0.0
                    continue
                self.delay = min(BACKOFF_MAX_SECONDS, max(BACKOFF_START_SECONDS, self.delay * 2))
                self.halt.wait(self.delay)
        except Exception as e:  # noqa: BLE001 — 보내는 스레드가 죽으면 레인이 묶인다
            self.proc.log(f"#{self.job_id} report: {type(e).__name__}")
            self.gave_up = True
        finally:
            self.ob.close()

    def _stopping_past_deadline(self) -> bool:
        if self.dead is not None or not self.proc.stopping.is_set():
            return False
        now = time.monotonic()
        if self._stop_deadline is None:
            self._stop_deadline = now + STOP_REPORT_SECONDS
        return now >= self._stop_deadline

    def attempt(self, step: str) -> bool:
        """한 번 보낸다. 진행했으면(받았거나 그 항목을 정리했으면) 참, 다시 해 볼 일이면 거짓."""
        ob = self.ob
        sent = self.proc._sent_at()
        try:
            if step == "phase":
                phase = ob.phase
                self.client.phase(self.job_id, phase)
                if ob.phase == phase:
                    ob.phase = None
            elif step in ("query", "log"):
                chunk = b"" if step == "query" else self._chunk()
                out = self.client.log(self.job_id, chunk, offset=ob.acked)
                got = out.get("offset") if isinstance(out, dict) else None
                if isinstance(got, int) and not isinstance(got, bool):
                    ob.acked = min(max(got, 0), ob.written)  # 2xx 의 `offset` 으로만 오른다
                else:
                    ob.acked += len(chunk)
                self.query_first = False
                self._last_log = time.monotonic()
            elif step == "artifacts":
                assert ob.bundle is not None
                receipt = self.client.upload_bundle(self.job_id, Path(ob.bundle["path"]))
                ob.bundle = {
                    **ob.bundle,
                    "state": "uploaded",
                    "sha256": (receipt or {}).get("bundle_sha256"),
                }
            else:
                fenced = self.fenced.is_set()  # mutcheck: fenced-finish-keeps-its-outcome
                self._send_finish(fenced)
                ob.done = True
        except ClientError as e:
            done = self._refused(step, e)
            ob.save()
            return done
        ob.save()
        if self.dead is None:
            self.proc.mark_ok(*sent)
        self.last_5xx = False
        self.failures = 0
        return True

    def _send_finish(self, fenced: bool) -> None:
        rec = dict(self.ob.finish or {})
        gone = rec.get("group_gone")
        if fenced:
            # 펜싱된 세션은 결과를 보낼 수 없다 — 좁은 확인만(결정 261 · 2차 Q-B)
            if self.dead is not None and self.dead.verdicts.get(self.job_id) != CONFIRMED:
                return  # 「모름」은 보내지 않는다 — 재확인(§4.4 의 9)에 맡긴다
            narrow: dict[str, Any] = {"summary_code": "worker_replaced", "summary_args": {}}
            if gone is not None:
                narrow["group_gone"] = gone
            self.client.finish(self.job_id, LOST, None, None, **narrow)
            self.narrow_sent = True
            return
        extra: dict[str, Any] = {}
        for key in ("group_gone", "finished_at", "summary_code", "summary_args", "log_bytes"):
            if rec.get(key) is not None:
                extra[key] = rec[key]
        disposition = rec.get("artifacts")
        bundle = self.ob.bundle
        if bundle is not None and isinstance(disposition, dict):
            if bundle.get("state") == "uploaded":
                disposition = {**disposition, "bundle_sha256": bundle.get("sha256")}
            else:
                # 처분은 보내기 직전에 정한다 — 올리지 못한 묶음은 `ready` 가 아니다(결정 275)
                disposition = {
                    "state": art.FAILED,
                    "bundle_sha256": None,
                    "file_count": disposition.get("file_count"),
                    "total_bytes": disposition.get("total_bytes"),
                    "skipped_count": disposition.get("skipped_count"),
                    "reason_code": "upload_failed",
                    "reason_args": None,
                }
        if disposition is not None:
            extra["artifacts"] = disposition
        self.client.finish(
            self.job_id, rec.get("outcome"), rec.get("exit_code"), rec.get("summary"), **extra
        )

    def _drop_reports(self) -> None:
        """phase · 로그 · 산출물을 버린다(finish 는 남는다)."""
        ob = self.ob
        ob.phase = None
        ob.log_stopped = True
        if ob.bundle is not None and ob.bundle.get("state") == "pending":
            ob.bundle = {**ob.bundle, "state": "skipped"}

    def _drop_item(self, step: str, *, failed: bool) -> None:
        ob = self.ob
        if step == "phase":
            ob.phase = None
        elif step in ("query", "log"):
            ob.log_stopped = True
        elif step == "artifacts":
            if ob.bundle is not None:
                ob.bundle = {**ob.bundle, "state": "failed" if failed else "skipped"}
        else:
            ob.done = True

    def _refused(self, step: str, e: ClientError) -> bool:
        """응답 표(§4.5 · 살아 있는 프로세스, §4.4 · 죽은 세션 D). 참이면 진행, 거짓이면 백오프."""
        proc, jid, dead = self.proc, self.job_id, self.dead
        code = e.body.get("code")
        what = f"#{jid} {step}" if dead is None else f"dead session {dead.session_id[:8]} #{jid}"
        if proc._is_fenced(e):
            if dead is None:
                proc._on_fenced(what)  # W5 — 모든 레인이 멈춘다. finish 는 좁은 확인으로 간다
            else:
                self.fenced.set()  # D 는 다른 살아 있는 프로세스가 펜싱했다 — W5 를 부르지 않는다
            self._drop_reports()
            if step == "finish" and self.narrow_sent:
                self.ob.done = True  # 좁은 확인까지 거절됐다 — 보낼 것이 없다
            elif dead is not None and (
                self.ob.finish is None or dead.verdicts.get(jid) != CONFIRMED
            ):
                # D 의 원래 결과는 남지 않는다(2차 Q-B). 「모름」은 재확인(§4.4 의 9)에 맡긴다
                self.ob.done = True
            return True
        if e.status == 409 and code == "log_gap" and step in ("query", "log"):
            expected = e.body.get("expected_offset")
            if isinstance(expected, int) and not isinstance(expected, bool):
                self.ob.acked = max(0, min(expected, self.ob.written))
            return True  # 그 오프셋부터 다시 — 프로세스는 죽이지 않는다
        if e.status == 409 and code == "not_registered":
            if dead is not None:
                # 서버가 D 를 모른다 — D 의 큐를 지운다. D 의 id 로 재등록하지 않는다(결정 326)
                proc._drop_dead_queue(dead, e.message)
                return True
            proc._reregister(what)
            return False
        if e.status == 409 and code == "protocol_required":
            proc.log(f"{what}: {e.message} — keeping the report")
            self.halt.set()
            return True
        if e.status == 409:
            # job_closed(또는 옛 서버의 코드 없는 409) — 서버가 이미 닫았다. 로그는 멈추고(스풀은
            # 남긴다) 산출물은 건너뛰며 finish 는 지운다(`late_result` 여부는 서버가 정했다)
            proc.log(f"{what}: server says {e.message}")
            if code == "job_closed" and dead is None:
                proc._closed_by_409(jid, e)
            self._drop_item(step, failed=False)
            if step == "finish":
                self.ob.done = True
            return True
        if dead is not None and e.status in (401, 403):
            # 남의 큐일 리 없지만 진짜 결과를 지우지 않는다(V1-5) — 파일 그대로, 보내기만 멈춘다
            proc.log(f"{what}: {e.message} — keeping the queue on disk")
            self.halt.set()
            return True
        if e.status and e.status < 500:
            proc.log(f"{what}: {e.message} — dropped")
            self._drop_item(step, failed=True)
            return True
        # 5xx · 연결 실패 · 시간 초과 — 같은 항목을 백오프로 다시
        self.last_5xx = bool(e.status)
        if dead is not None:
            proc._dead_answered = bool(e.status)
        self.failures += 1
        n = self.failures
        if n in (1, 10) or n % 60 == 0:
            proc.log(f"{what}: {e.message} — retrying ({n})")
        return False


class _RemoteObserver:
    """실행기 관찰자 — phase 와 로그를 그 잡의 보고 큐(`JobOutbox`)에 넣는다. 네트워크는 잡마다
    보내는 스레드(`_JobSender`)가 맡는다(M5t-b §4.5). 마커는 서버가 파싱한다.

    멈춤의 길은 이름으로 나뉜다(M5t-b 결정 334). 자기 멈춤 `self_stop` 은 `should_stop` → `lost`
    + `worker_self_stopped` 이고, 서버가 닫은 잡의 멈춤 `closed_stop`(W4 표)은 `should_cancel`
    길이다. `closed_by_server` 는 로그 전송만 멈춘다 — 일반 `lost` 잡은 그 409 로 죽지 않는다."""

    def __init__(
        self,
        proc: RemoteWorker,
        job_id: int,
        *,
        irreversible: bool = False,
        grace_seconds: int | None = None,
        grace_j: int = 10,
        outbox: JobOutbox | None = None,
        sender: _JobSender | None = None,
    ):
        self.proc = proc
        self.job_id = job_id
        self.outbox = outbox if outbox is not None else JobOutbox(None)
        if sender is None:
            sender = _JobSender(proc, job_id, self.outbox).start()
        self.sender = sender
        self.closed_by_server = False  # 409: 서버가 이미 닫았다(lost 판정 등) — 로그만 멈춘다
        self.irreversible = irreversible
        self.grace_seconds = grace_seconds  # claim 에 얼린 G_J(결정 257)
        self.grace_j = grace_j  # 러너의 SIGTERM→SIGKILL(표식 잡은 상한, §4.3)
        self.self_stop = threading.Event()  # 한 방향 — 되돌리지 않는다
        self.self_stop_seconds = 0
        self.closed_stop = threading.Event()
        self.closed_state: str | None = None  # 서버가 알린 끝 상태(W4)
        self.finish_sent = False  # `_finish` 를 거쳤다(큐에 넣었다)
        self._last_wake = time.monotonic()
        # 바깥 안전망(`run_claimed`)이 읽는다: 러너가 Popen 직전까지 갔나, 결과를 냈나
        self.launching = False
        self.result: Any = None

    def phase(self, phase: str) -> None:
        self.outbox.phase = phase  # 마지막 값 하나만 보낸다
        self.outbox.save()
        self.sender.wake.set()

    def output(self, data: bytes) -> None:
        """스풀에 덧붙이고 돌아온다 — 펌프(취소 · 시간초과 · 자기 멈춤 점검)를 네트워크로 막지
        않는다(RL5)."""
        self.outbox.append(data)
        due = (
            self.outbox.pending() >= LOG_BATCH_BYTES
            or time.monotonic() - self._last_wake >= LOG_FLUSH_SECONDS
        )
        if due:
            self._send_later()

    def _send_later(self) -> None:
        """보내는 스레드를 깨운다 — 펌프 스레드는 네트워크를 기다리지 않는다."""
        self._last_wake = time.monotonic()
        self.sender.wake.set()

    def flush(self) -> None:
        """지금 이 스레드에서 로그 한 조각을 보낸다(진단 · 시험용). 펌프는 부르지 않는다."""
        if self.sender.next_step() in ("query", "log"):
            self.sender.attempt("query" if self.sender.query_first else "log")

    def final_flush(self, wait: float | None = None) -> None:
        """끝 — 보내는 스레드를 깨운다. `wait` 를 주면 로그를 다 보낼 때까지 그만큼
        기다린다(시험)."""
        self.sender.wake.set()
        if wait is None:
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.outbox.pending() <= 0 or self.outbox.log_stopped or not self.sender.alive():
                return
            time.sleep(0.02)

    def should_cancel(self) -> bool:
        # mutcheck: closed-by-server-cancels
        return self.job_id in self.proc.cancel_requested or self.closed_stop.is_set()

    def should_stop(self) -> bool:
        return self.proc.stopping.is_set() or self.self_stop.is_set()

    def spawning(self) -> None:
        """Popen 직전 확인을 지났다 — `proc.json` 에 「띄우는 중」(결정 333).

        이 기록이 없으면 다음 프로세스는 「띄운 적 없음」(CONFIRMED)으로 읽는다. 그래서 표식 잡은
        기록하지 못하면 띄우지 않는다 — 도는 그룹을 사라졌다고 판정받는 것보다 안 띄우는 것이
        안전하다. 일반 잡은 홀드가 없으니 로그 한 줄 뒤 띄운다(P3 1라운드 HIGH)."""
        self.launching = True
        try:
            self.proc._write_proc(self.job_id, None)
        except OSError as e:
            self.proc.log(f"#{self.job_id} session folder: {type(e).__name__}")
            if self.irreversible:  # mutcheck: spawning-record-missing-still-spawns
                raise RunnerError(
                    "launch_failed",
                    error=type(e).__name__,
                    log=f"cannot record the process in the worker session folder: {e.strerror}",
                ) from e

    def spawned(self, pid: int) -> None:
        """띄운 뒤 `{pgid, start}`. 실패해도 잡은 멈추지 않는다 — 「띄우는 중」 기록이 남아 다음
        프로세스는 그 그룹을 모름(UNKNOWN)으로 읽는다."""
        try:
            self.proc._write_proc(self.job_id, pid)
        except OSError as e:  # mutcheck: spawned-record-error-escapes
            self.proc.log(f"#{self.job_id} session folder: {type(e).__name__}")

    def server_closed(self, state: str, had_cancel: bool) -> None:
        """W4 표(§4.1.2, V2-6). 서버 상태는 이미 정해졌고 워커의 finish 는 확인으로만 쓰인다."""
        self.closed_state = state
        self.closed_by_server = True
        if state in FINISH_RECORDED or state == CANCELLED:
            self.closed_stop.set()  # 아직 돌면 멈춘다
        elif state == LOST:
            # 표식 잡 · 취소 요청이 있던 잡만 멈춘다. 일반 잡은 계속 돈다(결정 253 · 322)
            # mutcheck: job-closed-kills-running · lost-cancel-keeps-running
            if self.irreversible or had_cancel:
                self.closed_stop.set()


# ── 워커 세션 폴더 · 고아 정리(M5t-b §4.4 · 결정 268 · 325 · 327 · 333) ─────────────────────────
#
# `<data>/worker-sessions/<S>/` — `lock`(살아 있는 동안 flock), `owner.json`(주인), `ok_at.json`,
# `jobs/<id>/claim.json` · `proc.json`. 죽은 세션 D 의 폴더를 잠글 수 있으면 D 는 죽었다(F43).

SESSIONS_DIR = "worker-sessions"
#: 등록 본문의 `previous_sessions` · `unverified_sessions` 한 목록에 싣는 최대 수 — 서버의
#: `_session_list` 상한과 같다. 넘는 죽은 세션은 오래된 것부터 싣고 나머지는 다음 시작에 싣는다
SESSION_LIST_MAX = 64
#: 리눅스 프로세스 표 — 시험이 가짜 폴더로 바꾼다
PROC_ROOT = Path("/proc")
#: `LC_ALL=C TZ=UTC ps -o lstart=` 의 모양(F71). 앞뒤 공백을 지운 뒤 맞아야 쓴다
LSTART_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"[ 0-9][0-9] [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}$"
)
CONFIRMED = "confirmed"  # 그룹이 없다(띄운 적 없음 · 거둠 · 다른 프로세스)
UNKNOWN = "unknown"  # 신원을 확인할 수 없다 — 신호를 보내지 않는다


def linux_start(pid: int, proc_root: Path | None = None) -> str | None:
    """`/proc/<pid>/stat` 의 22번째 칸(starttime). 이름에 괄호 · 공백이 들 수 있어 마지막 `)` 뒤에서
    센다(F59) — 그 뒤 첫 칸이 3번째(state)다."""
    root = PROC_ROOT if proc_root is None else proc_root
    try:
        raw = (root / str(pid) / "stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 1 :].split()
    if len(fields) < 20 or not fields[19].isdigit():
        return None
    return f"linux:{fields[19]}"


def ps_start(pid: int) -> str | None:
    """`ps -o lstart=` 를 `LC_ALL=C TZ=UTC` 로 읽는다. 서비스와 손으로 띄운 셸이 같은 문자열을
    쓴다(F71)."""
    env = dict(os.environ, LC_ALL="C", TZ="UTC")  # mutcheck: ps-lstart-locale
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    if out.returncode != 0 or not LSTART_RE.match(value):
        return None
    return f"ps:{value}"


def process_start(pid: int) -> str | None:
    """프로세스 신원 — 리눅스는 `/proc`, 그 밖은 `ps`. 못 읽으면 None(「모름」으로 간다)."""
    if (PROC_ROOT / "self" / "stat").exists():
        return linux_start(pid)
    return ps_start(pid)


def _start_valid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value.startswith("linux:"):
        return value[6:].isdigit()
    return value.startswith("ps:") and bool(LSTART_RE.match(value[3:]))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """임시 파일에 쓴 뒤 rename — 읽는 쪽이 반쯤 쓴 파일을 보지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def owner_of(config: WorkerConfig, token_name: str) -> dict[str, str]:
    """`owner.json` — 설정의 `server`(auto 면 `auto`)와 광고 이름, 토큰 이름. 토큰 값은 적지 않는다
    (결정 325). 해석한 주소로 해시하면 auto 가 주소를 바꿀 때 제 폴더를 남의 것으로 본다."""
    server = "auto" if getattr(config, "discovered_server", False) else config.server
    key = f"{server.strip().rstrip('/')}\n{config.server_name}"
    return {"server_sha256": hashlib.sha256(key.encode()).hexdigest(), "token_name": token_name}


def try_lock(path: Path) -> int | None:
    """`LOCK_EX | LOCK_NB` 로 잠가 본다. 잠그면 fd, 누가 쥐고 있거나 판정할 수 없으면 None."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # EWOULDBLOCK 은 살아 있는 세션이다. 그 밖의 OSError(네트워크 파일 시스템 등)는 판정할 수
        # 없다. 둘 다 건너뛴다
        os.close(fd)
        return None
    return fd


def judge_orphan(job_dir: Path, grace_seconds: float, log: Callable[[str], None]) -> str:
    """죽은 세션의 잡 하나를 판정한다(§4.4 step 6). 같은 그룹일 때만 신호를 보낸다."""
    try:
        text = (job_dir / "proc.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return CONFIRMED  # Popen 직전 확인 전에 죽었다 — 띄운 적 없음(결정 333)
    except OSError as e:
        # 있는데 못 읽는다 — 띄웠는지 모른다. 없음(띄운 적 없음)과 섞으면 도는 그룹이 사라졌다고
        # 판정받는다(P3 2라운드 LOW)
        log(f"orphan #{job_dir.name}: cannot read proc.json ({type(e).__name__}) — not touching it")
        return UNKNOWN  # mutcheck: orphan-unreadable-proc-confirmed
    try:
        proc = json.loads(text)
    except ValueError:
        proc = None
    if not isinstance(proc, dict):  # mutcheck: orphan-broken-proc-confirmed
        log(f"orphan #{job_dir.name}: proc.json is not a record — not touching it")
        return UNKNOWN
    pgid = proc.get("pgid")
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 1:
        return UNKNOWN  # 띄우는 순간 죽었을 수 있다
    recorded = proc.get("start")
    if not _pid_alive(pgid):
        if _group_alive(pgid):
            log(f"orphan #{job_dir.name}: leader gone but its group is alive — not touching it")
            return UNKNOWN  # 리더만 죽고 멤버가 산다(F59 · W13)
        return CONFIRMED
    now = process_start(pgid)
    if not (_start_valid(recorded) and _start_valid(now)):
        log(f"orphan #{job_dir.name}: cannot verify process {pgid} — not touching it")
        return UNKNOWN
    if recorded != now:  # mutcheck: orphan-kill-no-lstart
        return CONFIRMED  # pid 가 재사용됐다 — 원래 그룹은 없다. 손대지 않는다
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return CONFIRMED
    except PermissionError:
        return UNKNOWN
    deadline = time.monotonic() + grace_seconds
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    gone_by = time.monotonic() + 2
    while _group_alive(pgid) and time.monotonic() < gone_by:
        time.sleep(0.05)
    alive = _group_alive(pgid)
    log(
        f"orphan #{job_dir.name}: stopped process group {pgid}"
        + (" (still there)" if alive else "")
    )
    return UNKNOWN if alive else CONFIRMED


@dataclasses.dataclass
class DeadSession:
    """잠근 죽은 세션 D — 잡마다 판정을 담는다."""

    session_id: str
    path: Path
    fd: int
    verdicts: dict[int, str]

    @property
    def verified(self) -> bool:
        return all(v == CONFIRMED for v in self.verdicts.values())

    def ok_at(self) -> float:
        """D 의 마지막 2xx 보낸 시각(벽). `ok_at.json` 이 없으면 `lock` 의 mtime(§4.4 step 9)."""
        data = _read_json(self.path / "ok_at.json") or {}
        wall = data.get("wall")
        if isinstance(wall, str):
            try:
                return datetime.fromisoformat(wall.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        try:
            return (self.path / "lock").stat().st_mtime
        except OSError:
            return time.time()

    def claim(self, job_id: int) -> dict[str, Any]:
        return _read_json(self.path / "jobs" / str(job_id) / "claim.json") or {}


def sweep_sessions(root: Path, keep_days: int = WORKSPACE_KEEP_DAYS) -> None:
    """시작 청소(F41 · 결정 325): 잠긴 폴더는 건너뛰고, 주인이 다른(없는) 폴더는 7일 지났고 잠글 수
    있을 때만 지운다. 제 폴더도 7일 지나고 잠글 수 있으면 지운다(큐는 24시간 창을 이미 넘었다)."""
    base = root / SESSIONS_DIR
    if not base.is_dir():
        return
    cutoff = time.time() - keep_days * 86400
    for entry in base.iterdir():
        try:
            if not entry.is_dir() or entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        fd = try_lock(entry / "lock")
        if fd is None:
            continue
        try:
            shutil.rmtree(entry, ignore_errors=True)
        finally:
            os.close(fd)


class _HeartbeatSkipped(Exception):
    """매달린 heartbeat 가 상한이라 이 회차를 보내지 않았다 — 실패로 셌다."""


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_exact(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _policy_from_claim(claimed: dict[str, Any]) -> art.ArtifactPolicy | None:
    """claim 응답에 실려 온 **얼린 정책**(§9). 서버가 안 보냈으면 산출물을 안 모은다."""
    raw = claimed.get("artifacts")
    if not isinstance(raw, dict):
        preset = claimed.get("preset")
        globs = preset.get("artifacts") if isinstance(preset, dict) else None
        if not isinstance(globs, list) or not globs:
            return None
        raw = {"globs": globs}
    globs = raw.get("globs")
    if not isinstance(globs, list) or not globs:
        return None
    defaults = art.ArtifactPolicy()
    return art.ArtifactPolicy(
        globs=tuple(str(g) for g in globs),
        max_bytes=int(raw.get("max_bytes") or defaults.max_bytes),
        max_files=int(raw.get("max_files") or defaults.max_files),
        timeout_seconds=int(raw.get("timeout_seconds") or defaults.timeout_seconds),
        cancel_timeout_seconds=int(
            raw.get("cancel_timeout_seconds") or defaults.cancel_timeout_seconds
        ),
        # 필드가 없는 옛 서버는 `"always"` — **옛 동작이 기본**이다. 조용히 안 모으게 만들지 않는다.
        collect_on=str(raw.get("collect_on") or defaults.collect_on),
    )


def _disposition(bundle: CollectResult | None) -> dict[str, Any] | None:
    """`finish` 에 싣는 구조화된 처분(§5). 해시 하나로는 빈 수집도 시한 초과도 말할 수 없다.

    `None` 이면 필드를 아예 안 보낸다 — 그게 `unknown` 이고 `empty` 와 다르다.
    """
    if bundle is None:
        return None
    return {
        "state": bundle.state,
        "bundle_sha256": bundle.bundle_sha256,
        "file_count": len(bundle.files) if bundle.files else 0,
        "total_bytes": bundle.total_bytes,
        "skipped_count": bundle.skipped_count,
        "reason_code": bundle.reason_code,
        "reason_args": bundle.reason_args,
    }


class RemoteWorker:
    """워커 프로세스 본체. `run()` 이 블로킹으로 돈다."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        client: WorkerClient | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
        mono_fn: Callable[[], float] = time.monotonic,
        rand_fn: Callable[[], float] = random.random,  # 지터 — 테스트가 주입한다
        log: Callable[[str], None] | None = None,
        environ: dict[str, str] | None = None,
        once: bool = False,
        rediscover: Callable[[], str | None] | None = None,
        follows: bool = False,
        follow_tail: str | None = None,
    ):
        self.config = config
        # DHCP 로 서버 주소가 바뀐 경우 — auto 일 때만 cmd_worker 가 넘긴다(M5n §9)
        self.rediscover = rediscover
        self._conn_failures = 0
        self._client = client or WorkerClient(config.server, config.token)
        self.now_fn = now_fn
        self.mono_fn = mono_fn  # 자기 멈춤의 단조 시계(M5t-b §4.1.3) — 시험이 벽과 따로 민다
        self.rand_fn = rand_fn
        self.log = log or (lambda msg: print(f"[rcm worker] {msg}", file=sys.stderr, flush=True))
        self.environ = environ
        self._host: dict[str, Any] | None = None  # 호스트 사실 — 처음 쓸 때 한 번 잰다
        self.once = once
        self.stopping = threading.Event()
        self.paused = False
        self.cancel_requested: set[int] = set()
        self.running: dict[int, int] = {}  # job_id → lane
        self._lock = threading.Lock()
        self.heartbeat_seconds = 5
        self.claim_wait_seconds = 20
        self.timeout_seconds = 60
        self.name = config.name or socket.gethostname().split(".")[0]
        self.sampler: HostSampler | None = None
        self._threads: list[threading.Thread] = []
        self._hb_failures = 0
        self.processed = 0
        # 레인이 끝낸 이유의 종료 코드(재등록이 막혔으면 2). 0 이면 평범한 멈춤이다
        self.exit_code = 0
        self._malformed_said = False
        # M5n — 능력 보고. 세대가 바뀌면 명세를 다시 받고 다시 잰다(§4.2)
        self.generation: str | None = None
        self.probe_interval_seconds = 600
        self._last_measured = 0.0
        self._measure_lock = threading.Lock()
        # M5q PR B2 — 서버 버전이 바뀌면 드레인한다(§3.3). `follows` 가 거짓이면 드레인하지 않고
        # ③ 에서 `follow_tail`(있으면)을 프로세스당 한 번 쓰며 ④ 의 종료 문장 뒤에 붙인다
        self.follows = follows
        self.follow_tail = follow_tail
        self.draining = threading.Event()
        self.follow_to: str | None = None
        self.restart_for: str | None = None
        self.signalled = False  # 첫 신호를 받았다 — `cmd_worker` 의 처리기가 켠다(§14 B질문18)
        self._drain_lock = threading.Lock()
        self._idle_lanes: set[int] = set()
        self._drain_done = False
        # 잡마다 보내는 스레드(M5t-b §4.5, 결정 337). 레인은 제 잡의 큐가 끝날 때까지 다음 claim 을
        # 하지 않고, heartbeat 의 `jobs` 목록에는 큐가 안 끝난 잡이 남는다(F10)
        self._senders: dict[int, _JobSender] = {}
        self._rereg_lock = threading.Lock()
        # 죽은 세션 D 의 큐가 마지막으로 받은 것이 HTTP 응답(5xx)이었나 — 등록 대기 300초는 이
        # 시간만 센다(결정 337)
        self._dead_answered = False
        self._dead_dropped: set[str] = set()
        self._tail_said = False
        self._window_said = False
        self._hb_unregistered = 0
        # 재실행 시각은 프로세스가 시작할 때 한 번 읽는다(§14 B질문9)
        self.restarted_at = follow.restarted_at(os.environ, self._epoch())
        # ── M5t-b 워커 세션(결정 260 · 339-a) ──
        # 프로세스마다 하나. 모든 레인이 같은 값을 쓰고 M5q exec 뒤에는 새 이미지가 새 세션이다
        self.session_id = secrets.token_hex(16)
        self._bind_session(self._client)
        self.fenced = threading.Event()  # 409 `session_fenced` 를 받았다 — 종료 0(결정 258)
        # claim 순번과 「끝난 claim 까지」 — heartbeat 의 「없음」 판정이 순서에 기대지 않게 한다
        self._claim_seq = 0
        self._open_claims: set[int] = set()
        self._hb_seq = 0
        self._hb_applied = 0  # 응답의 cancel · paused 를 적용한 마지막 heartbeat 의 seq
        # ── M5t-b 자기 멈춤(§4.3) ──
        # 도는 잡의 관찰자 — 감시 스레드 · 닫힌 잡 통보(W4)가 찾아간다
        self._observers: dict[int, _RemoteObserver] = {}
        # ok_at: 2xx 를 받은 요청을 **보낸** 시각의 최댓값(벽 epoch, 단조). 2xx 전에는 None
        self._ok_at: tuple[float, float] | None = None
        self._ok_lock = threading.Lock()
        self._hb_inflight = 0
        self._hb_skipped = 0
        # 세션 폴더(§4.4) — `prepare_sessions` 가 만든다. 없으면(시험 · 등록 전) 아무것도 안 쓴다
        self.session_dir: Path | None = None
        self._session_fd: int | None = None
        self._ok_written = 0.0
        self.previous_sessions: list[str] = []
        self.unverified_sessions: list[str] = []
        self.dead_sessions: list[DeadSession] = []
        self._rechecks: list[threading.Timer] = []
        # 그룹이 사라졌음을 확인하지 못하고 끝난 표식 잡 — 그 폴더(`proc.json`)는 다음 프로세스가
        # 다시 판정하도록 남긴다(서버는 그 잡에 홀드를 건다)
        self._group_unconfirmed: set[int] = set()

    @property
    def client(self) -> Any:
        return self._client

    @client.setter
    def client(self, client: Any) -> None:
        # 서버를 다시 찾아 클라이언트를 바꿔도 세션은 이 프로세스의 것 그대로다
        self._client = client
        self._bind_session(client)

    def _bind_session(self, client: Any) -> None:
        try:
            client.session_id = self.session_id
        except AttributeError:  # 시험의 가짜 클라이언트(slots 등)
            pass

    def claims_settled(self) -> int:
        """`self._lock` 안에서 부른다. 열린 claim 이 있으면 `min(열린 것) − 1`, 없으면 마지막
        번호다."""
        if self._open_claims:
            return min(self._open_claims) - 1
        return self._claim_seq

    def _on_fenced(self, what: str) -> None:
        """409 `session_fenced`(W5) — 같은 토큰의 새 프로세스가 이 세션을 펜싱했다. 모든 레인을
        멈추고(잡은 좁은 확인 finish 로 닫는다) 종료 0 으로 끝낸다. 감독자는 0 을 되살리지 않으므로
        펜싱 핑퐁이 없다(결정 255 · 258)."""
        if not self.fenced.is_set():
            self.fenced.set()
            self.log(f"{what}: {FENCED}")
        self.exit_code = 0
        self.stopping.set()

    @staticmethod
    def _is_fenced(e: ClientError) -> bool:
        return e.status == 409 and e.body.get("code") == "session_fenced"

    def _epoch(self) -> float:
        return self.now_fn().timestamp()

    # ── 세션 폴더 · 고아 정리 (M5t-b §4.4) ─────────────────────────────────

    def _token_name(self) -> str:
        """`GET /api/whoami` 의 `name`(F64). 연결 실패는 등록처럼 다시, 401 · 403 은 종료 2."""
        whoami = getattr(self.client, "whoami", None)
        if whoami is None:
            return ""
        while True:
            try:
                out = whoami() or {}
            except ClientError as e:
                if e.status in (401, 403):
                    raise WorkerExit(2, f"{e.message} — set RCM_WORKER_TOKEN") from e
                if e.status:
                    raise WorkerExit(2, e.message) from e
                self.log(f"whoami: {e.message} — retrying in {int(REGISTER_RETRY_SECONDS)}s")
                if self.stopping.wait(REGISTER_RETRY_SECONDS):
                    raise WorkerExit(0, "stopped before registering") from e
                continue
            name = out.get("name")
            return name if isinstance(name, str) else ""

    def prepare_sessions(self) -> None:
        """프로토콜 확인 뒤 · 등록 전(§4.4 step 3~6 · 8 · 9). 제 폴더를 만들어 잠그고, 같은 주인의
        죽은 세션 D 를 잠가 고아를 판정한다. 등록은 `previous_sessions` · `unverified_sessions` 를
        싣는다."""
        owner = owner_of(self.config, self._token_name())
        base = self.config.data_path / SESSIONS_DIR
        own = base / self.session_id
        own.mkdir(parents=True, exist_ok=True)
        fd = try_lock(own / "lock")
        if fd is None:
            raise WorkerExit(2, f"cannot lock the worker session folder {own}")
        self._session_fd = fd
        _write_json(own / "owner.json", owner)
        self.session_dir = own
        for entry in sorted(base.iterdir()):
            if entry == own or not entry.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", entry.name):
                continue
            if _read_json(entry / "owner.json") != owner:  # mutcheck: adopt-foreign-session
                continue  # 남의 폴더 · 주인 없는 폴더는 잠가 보지도 않는다(W9 · 결정 325)
            dfd = try_lock(entry / "lock")
            if dfd is None:
                continue  # 살아 있는 세션이거나 판정할 수 없다
            verdicts: dict[int, str] = {}
            jobs = entry / "jobs"
            for job_dir in sorted(jobs.iterdir()) if jobs.is_dir() else ():
                if job_dir.name.isdigit():
                    verdicts[int(job_dir.name)] = judge_orphan(
                        job_dir, float(self.config.grace_seconds), self.log
                    )
            dead = DeadSession(entry.name, entry, dfd, verdicts)
            self.dead_sessions.append(dead)
            if not dead.verified:
                self.log(
                    f"dead session {dead.session_id[:8]}: process identity unknown — keeping its"
                    " group held, checking again later"
                )
        # 오래된 것부터(P6 GAP1) — 서버의 목록 상한을 넘으면 앞의 것만 싣고 나머지는 다음 시작에.
        # 확인된 D 의 잠금은 등록이 받을 때까지 쥔다 — 받으면 그 폴더를 지운다(`_settle_reported`)
        for dead in sorted(self.dead_sessions, key=lambda d: (d.ok_at(), d.session_id)):
            if dead.verified:
                self.previous_sessions.append(dead.session_id)
            else:
                self.unverified_sessions.append(dead.session_id)

    def _drop_own_session(self) -> None:
        """등록하지 못한 시작의 제 폴더를 지운다(P6 GAP1). 잡을 받은 적이 없어 다음 프로세스가
        판정할 것이 없다 — 남기면 재시작 루프마다 잡 없는 죽은 세션이 하나씩 쌓여 등록 목록 상한을
        넘는다."""
        own, fd = self.session_dir, self._session_fd
        self.session_dir, self._session_fd = None, None
        if own is not None:
            shutil.rmtree(own, ignore_errors=True)  # mutcheck: failed-start-keeps-its-folder
        if fd is not None:
            os.close(fd)

    def _settle_reported(self, previous: list[str]) -> None:
        """등록이 받은 죽은 세션 정리(P6 GAP1). 확인된 D 는 서버가 그 busy 잡을 닫고 홀드를 풀고
        펜싱했다 — 다시 보고할 것이 없으니 잠금을 쥔 채 폴더를 지운다. 「모름」 D 는 재확인
        (step 9)이 쓰므로 남긴다. 싣지 못한 확인된 D 는 잠금만 놓고 다음 시작에 싣는다."""
        sent = set(previous)
        for dead in self.dead_sessions:
            if not dead.verified:
                continue
            if dead.session_id in sent:
                shutil.rmtree(dead.path, ignore_errors=True)  # mutcheck: reported-dead-folder-kept
            if dead.fd >= 0:
                os.close(dead.fd)
                dead.fd = -1

    def schedule_rechecks(self) -> None:
        """「모름」 잡이 있는 D 마다 `D.ok_at + G_J − m_J` 에 다시 본다(step 9). 지났으면 바로 한
        번 본다."""
        for dead in self.dead_sessions:
            for job_id, verdict in dead.verdicts.items():
                if verdict != UNKNOWN:
                    continue
                claim = dead.claim(job_id)
                grace = claim.get("disconnect_grace_seconds")
                if not isinstance(grace, int) or isinstance(grace, bool):
                    from remote_ci_monitor.config import DISCONNECT_GRACE_MARGIN_SECONDS

                    grace = self.connected_age_floor() + DISCONNECT_GRACE_MARGIN_SECONDS
                grace_j = self._grace_for(job_id, claim.get("irreversible") is True, grace)
                when = dead.ok_at() + self.self_stop_threshold(grace, grace_j)
                delay = max(0.0, when - self._epoch())
                t = threading.Timer(delay, self.recheck, args=(dead, job_id))
                t.daemon = True
                t.name = f"rcm-worker-recheck-{job_id}"
                self._rechecks.append(t)
                t.start()

    def recheck(self, dead: DeadSession, job_id: int) -> str:
        """D 의 「모름」 잡을 다시 판정한다. 확인되면 D 의 id 로 좁은 확인 finish 를 한 번 보낸다 —
        D 는 등록이 펜싱했으므로 이것이 D 에 허용되는 단 하나의 요청이다(§4.2)."""
        verdict = judge_orphan(
            dead.path / "jobs" / str(job_id), float(self.config.grace_seconds), self.log
        )
        if verdict != CONFIRMED:
            self.log(f"dead session {dead.session_id[:8]} job #{job_id}: still unknown")
            return verdict
        client = copy.copy(self.client)
        try:
            client.session_id = dead.session_id
            client.finish(
                job_id,
                LOST,
                None,
                None,
                summary_code="worker_replaced",
                summary_args={},
                group_gone=True,
            )
            self.log(f"dead session {dead.session_id[:8]} job #{job_id}: confirmed gone")
        except ClientError as e:
            self.log(f"dead session {dead.session_id[:8]} job #{job_id}: {e.message}")
        return verdict

    def _job_dir(self, job_id: int) -> Path | None:
        return None if self.session_dir is None else self.session_dir / "jobs" / str(job_id)

    def _write_claim(self, job_id: int, irreversible: bool, grace: int | None) -> None:
        d = self._job_dir(job_id)
        if d is not None:
            data = {"irreversible": irreversible, "disconnect_grace_seconds": grace}
            _write_json(d / "claim.json", data)

    def _write_proc(self, job_id: int, pid: int | None) -> None:
        """`proc.json` 두 번 쓰기(결정 333): 띄우기 직전 `{pgid: null}`, 띄운 뒤 `{pgid, start}`."""
        d = self._job_dir(job_id)
        if d is None:
            return
        start = process_start(pid) if pid is not None else None
        _write_json(d / "proc.json", {"pgid": pid, "start": start})

    def _forget_job_dir(self, job_id: int) -> None:
        if job_id in self._group_unconfirmed:
            return  # 다음 프로세스가 `proc.json` 으로 그룹을 다시 판정해 홀드를 푼다
        d = self._job_dir(job_id)
        if d is not None:
            shutil.rmtree(d, ignore_errors=True)

    # ── 자기 멈춤 (M5t-b §4.3 · 결정 320 · 335) ────────────────────────────

    def _sent_at(self) -> tuple[float, float]:
        """요청을 보내는 지금 — 2xx 가 오면 이 값이 `ok_at` 후보다(응답 시각이 아니다, F55)."""
        return self.now_fn().timestamp(), self.mono_fn()

    def mark_ok(self, wall: float, mono: float) -> None:
        """2xx 를 받은 요청의 보낸 시각으로 `ok_at` 을 올린다. 벽 · 단조 각각 최댓값이다. 세션
        폴더의 `ok_at.json` 은 H 마다 많아야 한 번 덮어쓴다(다음 프로세스가 D 를 다시 볼 때
        쓴다)."""
        with self._ok_lock:
            if self._ok_at is None:
                self._ok_at = (wall, mono)
            else:
                self._ok_at = (max(self._ok_at[0], wall), max(self._ok_at[1], mono))
            best = self._ok_at[0]
            due = self.session_dir is not None and mono - self._ok_written >= self.heartbeat_seconds
            if due:
                self._ok_written = mono
        if due and self.session_dir is not None:
            try:
                _write_json(self.session_dir / "ok_at.json", {"wall": _iso_exact(best)})
            except OSError as e:
                self.log(f"session folder: {type(e).__name__}")

    def ok_at(self) -> tuple[float, float] | None:
        with self._ok_lock:
            return self._ok_at

    def connected_age_floor(self) -> int:
        """L = max(T, 2H + 3·T_req + 5)(결정 335) — 연결된 워커의 `ok_at` 나이 상한(§4.1.5)."""
        t, h = self.timeout_seconds, self.heartbeat_seconds
        return max(
            t, 2 * h + 3 * HEARTBEAT_DEADLINE_SECONDS + 5
        )  # mutcheck: lower-bound-one-timeout

    @staticmethod
    def self_stop_threshold(grace_seconds: int, grace_j: int) -> int:
        """문턱 G_J − m_J, m_J = T_req + grace_J + 여유(결정 320)."""
        # mutcheck: self-stop-ignores-request-timeout
        return grace_seconds - (HEARTBEAT_DEADLINE_SECONDS + grace_j + SELF_STOP_SLACK_SECONDS)

    def _grace_for(self, job_id: int, irreversible: bool, grace_seconds: int | None) -> int:
        """러너의 `grace_J`. 표식 잡이면 `G_J − L − T_req − 여유` 로 자른다(§4.3). 기본 grace 10 은
        설정 검증(`G ≥ L + 35`) 덕에 줄지 않는다. 줄였으면 한 줄 남긴다."""
        conf = int(self.config.grace_seconds)
        if not irreversible or grace_seconds is None:
            return conf
        cap = grace_seconds - self.connected_age_floor() - HEARTBEAT_DEADLINE_SECONDS
        cap -= SELF_STOP_SLACK_SECONDS
        grace = max(1, min(conf, cap))
        if grace != conf:
            self.log(
                f"#{job_id}: grace_seconds {conf} lowered to {grace} for this job"
                " — fits disconnect_grace_seconds"
            )
        return grace

    def _job_grace(self, observer: _RemoteObserver) -> int:
        if observer.grace_seconds is not None:
            return int(observer.grace_seconds)
        # 얼린 G 가 없다(서버 버그) — 서버가 받을 수 있는 가장 짧은 G 로 본다. 일찍 멈추는 쪽이다
        from remote_ci_monitor.config import DISCONNECT_GRACE_MARGIN_SECONDS

        return self.connected_age_floor() + DISCONNECT_GRACE_MARGIN_SECONDS

    def watchdog_tick(self) -> None:
        """감시 한 틱(W3). 표식 잡마다 `wgap ≥ G_J − m_J` 면 그 관찰자의 `self_stop` 을 세운다.
        `wgap` 은 벽과 단조 가운데 큰 쪽이다 — 잠든 시간을 안 세는 단조 · 뒤로 가는 벽 어느 쪽이든
        하나는 실제 경과 이상이다(RL26)."""
        ok = self.ok_at()
        if ok is None:
            return
        wall, mono = self._sent_at()
        wgap = max(wall - ok[0], mono - ok[1])  # mutcheck: self-stop-monotonic-only
        with self._lock:
            observers = list(self._observers.values())
        for o in observers:
            if not o.irreversible or o.self_stop.is_set():  # mutcheck: self-stop-all-jobs
                continue
            if wgap >= self.self_stop_threshold(self._job_grace(o), o.grace_j):
                o.self_stop_seconds = int(wgap)
                o.self_stop.set()  # mutcheck: self-stop-reports-cancelled
                self.log(
                    f"#{o.job_id}: no answer from the server for {int(wgap)}s"
                    " — stopping this irreversible job before the server gives it up"
                )

    def _watchdog_loop(self) -> None:
        while not self.stopping.wait(WATCHDOG_TICK_SECONDS):
            try:
                self.watchdog_tick()
            except Exception as e:  # noqa: BLE001 — 감시가 죽으면 표식 잡이 멈추지 못한다
                self.log(f"watchdog: {type(e).__name__}")

    def start_watchdog(self) -> threading.Thread:
        t = threading.Thread(target=self._watchdog_loop, name="rcm-worker-watchdog", daemon=True)
        t.start()
        return t

    # ── 서버가 닫은 잡(W4) ──────────────────────────────────────────────────

    def _closed_by_409(self, job_id: int, e: ClientError) -> None:
        state = e.body.get("state")
        if e.body.get("code") == "job_closed" and isinstance(state, str):
            self._job_closed(job_id, state, had_cancel=False)

    def _job_closed(self, job_id: int, state: str, *, had_cancel: bool) -> None:
        """서버가 알린 끝 상태(heartbeat `closed_jobs` · 보고의 409 `job_closed`)를 W4 표로."""
        if state not in FINISH_RECORDED and state not in (CANCELLED, LOST):
            how = "a claimed job cannot be" if state in _NOT_CLAIMED_STATES else "unknown state"
            self.log(f"#{job_id}: server reports {state} ({how}) — ignoring")
            return
        with self._lock:
            observer = self._observers.get(job_id)
            sender = self._senders.get(job_id)
        if observer is not None:
            observer.server_closed(state, had_cancel)
        if state in FINISH_RECORDED and sender is not None and sender.ob.finish is not None:
            # 이 세션의 finish 가 기록됐고 2xx 만 잃었다 — 큐 항목을 전달됨으로 지운다
            self.log(f"#{job_id} finish: the server already has it ({state})")
            sender.ob.done = True
            sender.ob.save()
            sender.wake.set()

    def _apply_closed_jobs(self, closed: Any) -> None:
        if not isinstance(closed, list):
            return
        for item in closed:
            if not isinstance(item, dict):
                continue
            jid, state = item.get("id"), item.get("state")
            if isinstance(jid, int) and not isinstance(jid, bool) and isinstance(state, str):
                self._job_closed(jid, state, had_cancel=item.get("had_cancel") is True)

    def _stop_summary(self, observer: Any) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """러너의 `lost`(곧 `should_stop`) 보고 — 자기 멈춤이면 `worker_self_stopped`(결정 334)."""
        stop = getattr(observer, "self_stop", None)
        if stop is not None and stop.is_set():
            return outcome_summary("worker_self_stopped", seconds=observer.self_stop_seconds)
        return STOP_SUMMARY, None, None

    # ── 보고 큐 (M5t-b §4.4 · §4.5) ──────────────────────────────────────────

    def _reregister(self, what: str) -> None:
        """보고가 409 `not_registered` 를 받았다(T10) — 같은 세션 id 로 재등록한다. 멱등이다(결정
        260). 레인 · 보내는 스레드가 겹치지 않게 한 잠금 아래에서 한다."""
        with self._rereg_lock:
            self.log(f"{what}: worker not registered — registering again")
            try:
                self.register()
            except WorkerExit as x:
                self.log(f"{what}: {x.message}")

    def _drop_dead_queue(self, dead: DeadSession, message: str) -> None:
        """서버가 D 를 모른다(409 `not_registered`) — D 의 큐를 모두 지운다(결정 326). D 의 id 로
        재등록하면 죽은 세션을 되살려 살아 있는 세션을 펜싱한다."""
        if dead.session_id not in self._dead_dropped:
            self._dead_dropped.add(dead.session_id)
            self.log(f"dead session {dead.session_id[:8]}: {message} — dropping its queue")
        for sender in list(self._senders.values()):
            if sender.dead is dead:
                sender.ob.done = True
                sender.ob.save()
                sender.halt.set()

    def dead_queues(self) -> list[_JobSender]:
        """죽은 세션 D 마다 남은 보고 큐(§4.4 의 7). 보내는 스레드는 아직 띄우지 않는다."""
        out: list[_JobSender] = []
        for dead in self.dead_sessions:
            jobs = dead.path / "jobs"
            for job_dir in sorted(jobs.iterdir()) if jobs.is_dir() else ():
                if not job_dir.name.isdigit():
                    continue
                ob = JobOutbox.load(job_dir)
                if ob is None or ob.done:
                    continue
                client = copy.copy(self.client)
                try:
                    client.session_id = dead.session_id
                except AttributeError:
                    pass
                job_id = int(job_dir.name)
                out.append(
                    _JobSender(self, job_id, ob, client=client, dead=dead, resumed=ob.acked > 0)
                )
        return out

    def send_dead_queues(self) -> None:
        """등록 전에 D 의 큐를 D 의 세션 id 로 보낸다(§4.4 의 7 · 결정 326 · 337). 등록이 D 를
        펜싱하면
        D 의 finish 가 버려지기 때문이다. 서버가 응답하는(HTTP 상태가 오는) 시간으로 300초가 지나면
        남은 큐를 디스크에 둔 채 등록한다 — 연결 실패 시간은 세지 않는다."""
        senders = self.dead_queues()
        if not senders:
            return
        with self._lock:
            for s in senders:
                self._senders[s.job_id] = s  # 등록 전이라 이 프로세스의 잡과 겹치지 않는다
        for s in senders:
            s.start()
        answered = 0.0
        last = self._epoch()
        while any(s.alive() for s in senders):
            if self.stopping.wait(0.05):
                break
            now = self._epoch()
            if self._dead_answered:
                answered += max(0.0, now - last)
            last = now
            if answered >= DEAD_SESSION_WAIT_SECONDS:  # mutcheck: dead-session-wait-forever
                for s in senders:
                    if s.alive() and not s.ob.done:
                        assert s.dead is not None
                        self.log(
                            f"dead session {s.dead.session_id[:8]} queue not delivered after"
                            f" {DEAD_SESSION_WAIT_SECONDS}s of server errors — registering;"
                            f" job #{s.job_id} will close as restarted-without-job"
                        )
                    s.halt.set()
                break
        for s in senders:
            s.halt.set()
        for s in senders:
            s.thread.join(2)
        with self._lock:
            for s in senders:
                if self._senders.get(s.job_id) is s:
                    self._senders.pop(s.job_id, None)

    # ── 등록 · heartbeat ─────────────────────────────────────────────────────

    def check_protocol(self) -> None:
        """등록 **전에** 서버의 워커 프로토콜을 본다(M5t-b T13 · 결정 270). 서버에는 GET 만 보낸다.

        `/worker/spec` 이 `worker_protocol ≥ 2` 면 진행한다. 키가 없거나 404 면 서버 버전을 본다 —
        다르면 오늘의 등록 길로 가서 따라가거나(M5q) 종료 2 로 끝난다(서버의 버전 409 는 어떤
        부작용보다 먼저다). 같으면 등록 · claim 없이 종료 3 이다(감독자가 되살린다)."""
        while True:
            try:
                try:
                    spec = self.client.spec()
                except ClientError as e:
                    if e.status != 404:
                        raise
                    spec = {}
                proto = spec.get("worker_protocol") if isinstance(spec, dict) else None
                if isinstance(proto, int) and not isinstance(proto, bool) and proto >= 2:
                    return
                version = (self.client.health() or {}).get("version")
            except ClientError as e:
                if e.status == 403:
                    raise WorkerExit(
                        2, f"{e.message} (rcm token add NAME --worker on the server)"
                    ) from e
                if e.status == 401:
                    raise WorkerExit(2, f"{e.message} — set RCM_WORKER_TOKEN") from e
                if e.status:
                    raise WorkerExit(2, e.message) from e
                self._connection_failed()
                self.log(f"register: {e.message} — retrying in {int(REGISTER_RETRY_SECONDS)}s")
                if self.stopping.wait(REGISTER_RETRY_SECONDS):
                    raise WorkerExit(0, "stopped before registering") from e
                continue
            if version == __version__:
                raise WorkerExit(EXIT_PROTOCOL, PROTOCOL_OLD_SERVER)
            return  # 버전이 다르다 — 등록이 버전 409 를 받고 따라가거나 종료 2

    def register(self) -> dict[str, Any]:
        while True:
            sent = self._sent_at()
            try:
                sessions: dict[str, list[str]] = {}
                if self.previous_sessions or self.unverified_sessions:
                    sessions = {  # mutcheck: session-list-uncapped
                        "previous_sessions": self.previous_sessions[:SESSION_LIST_MAX],
                        "unverified_sessions": self.unverified_sessions[:SESSION_LIST_MAX],
                    }
                out = self.client.register(
                    pool=self.config.pool,
                    lanes=self.config.lanes,
                    host_name=self.name,
                    version=__version__,
                    host=self.host_facts(),
                    **sessions,
                )
            except ClientError as e:
                if self._is_fenced(e):
                    raise WorkerExit(0, FENCED) from e
                if e.status == 409 and e.body.get("code") == "protocol_required":
                    raise WorkerExit(EXIT_PROTOCOL, e.message) from e
                if e.status == 403:
                    raise WorkerExit(
                        2, f"{e.message} (rcm token add NAME --worker on the server)"
                    ) from e
                if e.status == 401:
                    raise WorkerExit(2, f"{e.message} — set RCM_WORKER_TOKEN") from e
                if e.status == 409:
                    self._raise_for_version(e)
                if e.status:
                    raise WorkerExit(2, e.message) from e
                self._connection_failed()
                self.log(f"register: {e.message} — retrying in {int(REGISTER_RETRY_SECONDS)}s")
                if self.stopping.wait(REGISTER_RETRY_SECONDS):
                    raise WorkerExit(0, "stopped before registering") from e
                continue
            self._conn_failures = 0
            self.mark_ok(*sent)
            # 첫 등록만 싣는다 — 받은 확인된 D 의 폴더는 지운다(P6 GAP1)
            self._settle_reported(sessions.get("previous_sessions", []))
            self.previous_sessions, self.unverified_sessions = [], []
            self.heartbeat_seconds = int(out.get("heartbeat_seconds") or 5)
            self.claim_wait_seconds = int(out.get("claim_wait_seconds") or 20)
            self.timeout_seconds = int(out.get("worker_timeout_seconds") or 60)
            self.name = str(out.get("name") or self.name)
            replaced = out.get("fenced_sessions")
            if "fenced_sessions" in out and replaced is None:
                # 재시작한 서버가 아직 모른다(P6-2 #2) — 대신했다고 단정하지 않고 모른다고 쓴다
                self.log(  # mutcheck: replaced-unknown-silent
                    "the server restarted recently and cannot tell whether this replaced another"
                    f" process of worker {self.name} — if a service worker with this token was"
                    " running, it has stopped and will not start again by itself"
                )
            if isinstance(replaced, int) and not isinstance(replaced, bool) and replaced > 0:
                # 펜싱된 프로세스는 종료 0 이라 서비스면 다시 뜨지 않는다(명세 §9, P6 GAP8)
                noun = "process" if replaced == 1 else "processes"
                self.log(  # mutcheck: replaced-service-silent
                    f"replaced {replaced} other {noun} of worker {self.name} — a service worker"
                    " with this token has stopped and will not start again by itself"
                )
            # 등록은 서버가 이 워커의 보고를 지우는 일이다(M5n §19-5). 등록 응답의 세대를 제 것으로
            # 삼으면 다음 heartbeat 이 「세대 같음」으로 보여 주기(기본 10분)까지 다시 재지 않는다 —
            # 그동안 any-capable 잡을 못 받는다. 아직 이 세대로 보고하지 않았다고 둔다
            self.generation = None
            return out

    def _raise_for_version(self, e: ClientError) -> None:
        """버전 불일치 409 의 `server_version` 이 형식 검사를 지나고 지금 도는 버전과 다르면
        `WorkerFollow`. 값이 없거나 틀렸으면 아무것도 하지 않는다 — 오늘의 종료 2 로 간다. 틀린 값은
        프로세스당 한 번 로그를 쓰고 값은 찍지 않는다(§14.1)."""
        if "server_version" not in e.body or e.body["server_version"] is None:
            return
        version = e.body["server_version"]
        if not follow.valid_version(version):
            if not self._malformed_said:
                self._malformed_said = True
                self.log("ignoring a malformed server_version from the server")
            return
        if version != __version__:
            raise WorkerFollow(version, e.message) from e

    def _host_sample(self) -> dict[str, Any] | None:
        if self.sampler is None:
            return None
        try:
            hosts, _error = self.sampler.latest()
        except Exception:  # noqa: BLE001
            return None
        if not hosts:
            return None
        from remote_ci_monitor.core.status import host_json

        doc = host_json(hosts[0], now=self.now_fn())
        # 낡은 표본은 **아예 안 보낸다**(M5f §3.1). 서버는 받은 시각으로 sampled_at 을 다시
        # 찍으므로, 샘플러가 죽어도 heartbeat 만 살아 있으면 굳은 표본을 영원히 「새것」으로
        # 본다 — 부하 게이트가 못 보면서 열리는 fail-open 구멍이다. 안 보내면 서버가 든
        # 표본이 늙어 게이트가 닫힌다.
        if doc.get("stale"):
            return None
        for key in ("name", "source", "sampled_at", "age_seconds", "stale"):
            doc.pop(key, None)
        return doc

    def heartbeat_once(self) -> bool:
        # 순번 · 목록 · 「끝난 claim 까지」는 한 잠금 구간에서 함께 찍는다(결정 339-a, R20)
        with self._lock:
            self._hb_seq += 1
            seq = self._hb_seq
            jobs = sorted(self.running)
            settled = self.claims_settled()
        try:
            out = self._heartbeat_call(jobs, seq, settled)
        except _HeartbeatSkipped:
            return False
        except ClientError as e:
            if self._is_fenced(e):
                self._on_fenced("heartbeat")
                return False
            if e.status == 409:
                # 서버에 워커 행이 없다(DB 를 되돌렸거나 잊었다) — 닿고 있으니 「못 닿음」이 아니다.
                # 연결 실패 줄과 같은 간격으로 쓴다(§14 B질문12)
                self._hb_unregistered += 1
                n = self._hb_unregistered
                if n in (3, 12) or n % 60 == 0:
                    self.log("heartbeat: worker not registered")
                return False
            self._hb_failures += 1
            if e.status:
                self._conn_failures = 0
            else:
                self._connection_failed()
            if self._hb_failures in (3, 12) or self._hb_failures % 60 == 0:
                gone = self._hb_failures * self.heartbeat_seconds
                self.log(f"heartbeat: server unreachable for {gone}s ({e.message})")
            return False
        if self._hb_failures >= 3:
            self.log("heartbeat: server reachable again")
        self._hb_failures = 0
        self._hb_unregistered = 0
        self._conn_failures = 0
        timeout = out.get("timeout_seconds")
        if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
            self.timeout_seconds = timeout  # L 의 T(F62)
        # 서버가 닫은 잡을 먼저 안다(W4) — 이미 기록된 finish 를 다시 보내지 않게
        self._apply_closed_jobs(out.get("closed_jobs"))  # mutcheck: closed-jobs-ignored
        # heartbeat 스레드는 보고를 다시 보내지 않는다 — 잡마다 보내는 스레드가 한다(§4.5)
        self._heard_server_version(out)
        cancel = out.get("cancel") or []
        with self._lock:
            # 늦게 온 응답이 새 취소를 지우지 않게 — 이미 적용한 것보다 큰 seq 의 응답만(결정 339-a)
            if seq > self._hb_applied:  # mutcheck: stale-hb-response-applied
                self._hb_applied = seq
                self.cancel_requested = {int(j) for j in cancel if isinstance(j, int)}
                self.paused = bool(out.get("paused"))
        gen = out.get("generation")
        due = time.monotonic() - self._last_measured >= self.probe_interval_seconds
        # 세대를 안 보내는 서버(M5n 이전)에는 보고하지 않는다
        if isinstance(gen, str) and (gen != self.generation or due):
            # 서버가 설정을 다시 읽었거나(세대) 다시 잴 때가 됐다 — 명세부터 다시 받는다.
            # **heartbeat 스레드에서 재지 않는다**: 프로브 묶음(최대 64 × 60초)이
            # `worker_timeout_seconds` 를 넘으면 서버가 이 워커를 down 으로 보고 도는 잡을
            # lost 로 만든다. 따로 띄우고, 재는 중이면 `_measure_lock` 이 겹치지 않게 한다
            threading.Thread(
                target=self._measure_quietly, name="rcm-worker-measure", daemon=True
            ).start()
        return True

    def _heartbeat_call(self, jobs: list[int], seq: int, settled: int) -> dict[str, Any]:
        """heartbeat 하나를 도우미 스레드에서 보내고 `HEARTBEAT_DEADLINE_SECONDS` 까지만 기다린다
        (결정 335). 넘으면 그 회차를 실패로 센다. 늦게 온 2xx 는 보낸 시각으로 `ok_at` 만 올린다 —
        응답의 취소 · 멈춤은 seq 가 막는다. 매달린 도우미가 `HEARTBEAT_INFLIGHT_MAX` 면 보내지
        않는다."""
        with self._ok_lock:
            if self._hb_inflight >= HEARTBEAT_INFLIGHT_MAX:
                self._hb_skipped += 1
                n = self._hb_skipped
                hung = True
            else:
                self._hb_inflight += 1
                hung = False
        if hung:
            self._hb_failures += 1
            if n in (1, 12) or n % 60 == 0:
                self.log(f"heartbeat: {HEARTBEAT_INFLIGHT_MAX} requests in flight — skipping one")
            raise _HeartbeatSkipped
        sent = self._sent_at()
        box: dict[str, Any] = {}
        done = threading.Event()
        host = self._host_sample()

        def call() -> None:
            try:
                box["out"] = self.client.heartbeat(jobs, host, seq=seq, claims_settled=settled)
                self.mark_ok(*sent)
            except BaseException as e:  # noqa: BLE001 — 부른 쪽이 다시 올린다
                box["err"] = e
            finally:
                with self._ok_lock:
                    self._hb_inflight -= 1
                done.set()

        threading.Thread(target=call, name="rcm-worker-heartbeat-call", daemon=True).start()
        if not done.wait(HEARTBEAT_DEADLINE_SECONDS):  # mutcheck: heartbeat-no-deadline
            raise ClientError(0, f"no answer in {HEARTBEAT_DEADLINE_SECONDS}s")
        if "err" in box:
            raise box["err"]
        return box.get("out") or {}

    # ── 드레인 (M5q §3.3 · §14.9) ───────────────────────────────────────────

    def _heard_server_version(self, out: dict[str, Any]) -> None:
        """heartbeat 응답의 `server_version`(③). 키가 없거나 `null` 이면 옛 서버다 — 아무것도 하지
        않는다. 형식이 틀린 값은 없는 것으로 보고 프로세스당 한 번 로그를 쓴다(§14.1)."""
        version = out.get("server_version")
        if version is None:
            return
        if not follow.valid_version(version):
            if not self._malformed_said:
                self._malformed_said = True
                self.log("ignoring a malformed server_version from the server")
            return
        if self.once:
            return  # `--once` 는 ③ 을 무시한다(§14 B질문4)
        if not self.follows:
            if version != __version__ and self.follow_tail and not self._tail_said:
                self._tail_said = True
                self.log(f"server v{follow.clean(version)} — {self.follow_tail}")
            return
        with self._drain_lock:
            if self._drain_done:
                return
            if version == __version__:
                if self.draining.is_set():
                    # 서버가 지금 도는 버전으로 돌아왔다 — 레인은 다시 claim 한다(§14 B질문8)
                    self.draining.clear()
                    self.follow_to = None
                    self.log(f"server runs v{follow.clean(version)} again — drain cancelled")
                return
            if self.draining.is_set():
                self._retarget(version)
                return
            ago = self._restarted_ago()
            if ago is not None:
                # 재실행한 지 300초 안이다 — 고리를 막는다. 창이 끝나면 다음 heartbeat 에서
                # 드레인한다
                if not self._window_said:
                    self._window_said = True
                    self.log(
                        f"server runs v{follow.clean(version)} — not draining: "
                        f"restarted {int(ago)}s ago"
                    )
                return
            self._begin_drain(version, None)

    def _drain_from_lane(self, lane: int, version: str) -> None:
        """레인의 재등록이 버전 불일치 409 다(④). 300초 창과 상관없이 드레인한다 — 등록이 막힌
        레인은 어차피 일을 못 한다(§3.3)."""
        with self._drain_lock:
            if self._drain_done:
                return
            if self.draining.is_set():
                self._retarget(version)
                return
            self._begin_drain(version, lane)

    def _begin_drain(self, version: str, lane: int | None) -> None:
        # `_drain_lock` 안에서 부른다
        self.follow_to = version
        self.draining.set()
        head = f"lane {lane}: " if lane is not None else ""
        self.log(f"{head}server runs v{follow.clean(version)} — {DRAINING}")

    def _retarget(self, version: str) -> None:
        # `_drain_lock` 안에서 부른다. 세 번째 버전이면 목표를 마지막 값으로 바꾼다(§14 B질문8)
        if version != self.follow_to:
            self.follow_to = version
            self.log(f"server now runs v{follow.clean(version)} — still draining")

    def _restarted_ago(self) -> float | None:
        """재실행한 지 `RESTART_WINDOW_SECONDS` 안이면 지난 초, 아니면 None."""
        if self.restarted_at is None:
            return None
        ago = self._epoch() - self.restarted_at
        if ago < follow.RESTART_WINDOW_SECONDS:
            return max(0.0, ago)
        return None

    def restart_window_open(self) -> bool:
        """이 프로세스가 300초 안에 드레인 재실행으로 시작했나 — 따라간 자식의 ② 가 본다."""
        return self._restarted_ago() is not None

    def _lane_waits(self, lane: int) -> bool:
        """드레인 중이면 이 레인을 「잡 없이 기다림」으로 적고 참. 아니면 그 표시를 지운다."""
        with self._drain_lock:
            if self.draining.is_set():
                self._idle_lanes.add(lane)
                return True
            self._idle_lanes.discard(lane)
            return False

    def _drained(self) -> bool:
        """드레인 중이고 살아 있는 레인이 모두 잡 없이 기다리면 드레인을 마친다(§3.3 의 4). 보고하지
        못한 잡은 heartbeat 가 도는 동안 다시 보고한다(§14 B질문11)."""
        with self._drain_lock:
            if not self.draining.is_set() or self._drain_done or self.stopping.is_set():
                return False  # 멈춤(신호 · `--once`)이 드레인을 이긴다 — 재실행하지 않는다
            alive = {i for i, t in enumerate(self._threads, 1) if t.is_alive()}
            if not alive <= self._idle_lanes:
                return False
            self._drain_done = True
        if not self.once:
            self.restart_for = self.follow_to
        return True

    def release_probes(self) -> None:
        """exec 직전 — 측정이 돌고 있으면 `grace_seconds` 까지 기다리고, 그래도 돌면 그 프로브
        프로세스를 끝낸다. exec 뒤에 부모가 바뀐 프로브가 남지 않게(§14 B질문13)."""
        from remote_ci_monitor import hostsample

        if self._measure_lock.acquire(timeout=max(0, self.config.grace_seconds)):
            self._measure_lock.release()
            return
        hostsample.kill_probes()

    def _measure_quietly(self) -> None:
        try:
            self.measure_and_report()
        except Exception as e:  # noqa: BLE001 — 재기가 죽어도 heartbeat 은 산다
            self.log(f"capabilities: {type(e).__name__}")

    def _connection_failed(self) -> None:
        """연결 실패(status 0) 하나. 연속 `REDISCOVER_AFTER_FAILURES` 번마다 서버를 다시 찾는다."""
        self._conn_failures += 1
        if self.rediscover is None or self._conn_failures % REDISCOVER_AFTER_FAILURES:
            return
        url = self.rediscover()
        if not url or url.rstrip("/") == self.client.server.rstrip("/"):
            return
        self.log(f"server moved: {self.client.server} → {url}")
        self.client = WorkerClient(url, self.config.token)

    # ── 능력 보고 (M5n §4.2) ────────────────────────────────────────────────

    def host_facts(self) -> dict[str, Any]:
        """OS 버전 · arch · Rosetta · 컨테이너 · root · 패키지 관리자 · Python(M5t-a §4.5). 프로세스
        하나에 한 번 잰다 — 등록과 능력 보고가 같은 값을 보낸다. 서버는 메모리에만 둔다."""
        if self._host is None:
            from remote_ci_monitor import hostsample

            facts = hostsample.host_facts()
            self._host = {k: facts.get(k) for k in HOST_KEYS}
        return dict(self._host)

    def measure(self, spec: dict[str, Any]) -> dict[str, Any]:
        """명세대로 **이 프로세스의 환경**에서 잰다 — 보고 본문. 서버에 보내지 않는다."""
        from remote_ci_monitor.config import Probe
        from remote_ci_monitor.core import jobenv
        from remote_ci_monitor.core.capability import host_key_from
        from remote_ci_monitor.hostsample import hardware_id, run_probes
        from remote_ci_monitor.runner import measure_tools

        env = dict(self.environ if self.environ is not None else os.environ)
        probes = [
            Probe(
                name=str(p.get("name")),
                argv=tuple(str(a) for a in p.get("argv") or ()),
                timeout_seconds=int(p.get("timeout_seconds") or 10),
            )
            for p in spec.get("probes") or []
            if isinstance(p, dict) and p.get("argv")
        ]
        fingerprint = {
            name: {"state": r.state, "value": r.value}
            for name, r in run_probes(probes, env).items()
        }
        presets: dict[str, Any] = {}
        for p in spec.get("presets") or []:
            if not isinstance(p, dict) or not isinstance(p.get("name"), str):
                continue
            passthrough = tuple(str(x) for x in p.get("env_passthrough") or ())
            fixed = {str(k): str(v) for k, v in (p.get("env") or {}).items()}
            job_env = jobenv.job_env(passthrough, fixed, env)
            missing, workspace = measure_tools(
                tuple(str(x) for x in p.get("requires") or ()), job_env
            )
            presets[p["name"]] = {
                "missing_tools": missing,
                "workspace_tools": workspace,
                "missing_passthrough": list(
                    jobenv.missing_passthrough(passthrough, env, preset_env=fixed)
                ),
            }
        return {
            "generation": spec.get("generation"),
            "host_key": host_key_from(hardware_id(), self.name),
            "accept_any": bool(getattr(self.config, "accept_any", True)),
            "fingerprint": fingerprint,
            "presets": presets,
            # 서버 재시작 뒤에도 다음 보고가 호스트 사실을 다시 채운다(TD42). 옛 서버는 무시한다
            "host": self.host_facts(),
        }

    def measure_and_report(self) -> bool:
        """명세를 받아 재고 보낸다. 세대가 그 사이 바뀌었으면(409) 한 번 더. 실패는 로그만 —
        보고가 없는 워커는 any-capable 잡을 안 받을 뿐 pool 잡은 그대로 돈다(fail-closed)."""
        if not self._measure_lock.acquire(blocking=False):
            return False  # 이미 재는 중
        try:
            for _attempt in range(2):
                try:
                    spec = self.client.spec()
                    self.probe_interval_seconds = int(spec.get("probe_interval_seconds") or 600)
                    body = self.measure(spec)
                    self.client.capabilities(body)
                except ClientError as e:
                    if e.status == 409 and _attempt == 0:
                        continue
                    if e.status == 404:
                        self.log("capabilities: server does not take reports (older rcm)")
                    else:
                        self.log(f"capabilities: {e.message}")
                    return False
                self.generation = str(spec.get("generation") or "") or None
                self._last_measured = time.monotonic()
                return True
            return False
        finally:
            self._measure_lock.release()

    def _heartbeat_loop(self) -> None:
        while not self.stopping.wait(self.heartbeat_seconds):
            try:
                self.heartbeat_once()
            except Exception as e:  # noqa: BLE001 — heartbeat 이 죽으면 서버가 lost 로 본다
                self.log(f"heartbeat: {type(e).__name__}")

    # ── 레인 ────────────────────────────────────────────────────────────────

    def _backoff(self, base: float) -> float:
        """`[base, 2 × base)` 의 대기. **하한을 낮추지 않는다** — `uniform(0, base)` 로 하면 뜨거운
        루프가 된다.

        지터가 없으면 빈 204 를 받은 레인들이 같은 1초 격자에 묶여 영영 안 흩어진다. 실측: 같은
        격자 32레인이면 요청 세마포어(`max_concurrent_requests`)가 정확히 가득 차고, 48레인에서
        첫 `/api/status` 503 이 난다(M5f §15-C4).
        """
        return base + self.rand_fn() * base

    def _lane_loop(self, lane: int) -> None:
        while not self.stopping.is_set():
            if self._lane_waits(lane):
                # 드레인 중 — claim 하지 않고 기다린다. 받은 잡은 이미 끝내고 보고했다(§3.3 의 2)
                self.stopping.wait(DRAIN_WAIT_SECONDS)
                continue
            if self.paused:
                if self.stopping.wait(1.0):
                    break
                continue
            asked_at = time.monotonic()
            with self._lock:
                self._claim_seq += 1
                claim_seq = self._claim_seq
                self._open_claims.add(claim_seq)
            claimed = None
            sent = self._sent_at()
            try:
                claimed = self.client.claim(lane, self.claim_wait_seconds, claim_seq=claim_seq)
                self.mark_ok(*sent)  # 잡 J 의 첫 ok_at 은 J 를 돌려준 claim 을 보낸 시각이다
            except ClientError as e:
                if self._is_fenced(e):
                    self._on_fenced(f"lane {lane}")
                    break
                if e.status == 409 and "not registered" in e.message:
                    self.log(f"lane {lane}: {e.message} — registering again")
                    # 같은 세션 id 의 재등록은 멱등이다(결정 260) — 다른 레인의 잡을 닫지 않는다.
                    # 보고 큐는 디스크에 있고 잡마다 보내는 스레드가 이어 보낸다
                    try:
                        with self._rereg_lock:
                            self.register()
                    except WorkerExit as x:
                        if isinstance(x, WorkerFollow) and self.follows:
                            # ④ — 다른 레인의 잡을 잃지 않고 드레인한다(§3.2 · §13 P1-5)
                            self._drain_from_lane(lane, x.version)
                            continue
                        # 등록이 막혀 이 워커는 잡을 받을 수 없다 — 종료 2 로 감독자에게 알린다.
                        # 종료 0 이면 launchd `SuccessfulExit = false` 와 systemd
                        # `Restart=on-failure` 가 다시 띄우지 않아 조용히 멈춘다(§14 B질문1).
                        # 따라가지 않는 워커는 문장 뒤에 이유를 붙인다(W12)
                        tail = self.follow_tail if isinstance(x, WorkerFollow) else None
                        self.log(f"lane {lane}: {x.message}" + (f" · {tail}" if tail else ""))
                        if x.code:
                            self.exit_code = x.code
                        self.stopping.set()
                        break
                    continue
                if e.status == 0 or e.status == 503:
                    self.stopping.wait(self._backoff(RETRY_WAIT_SECONDS))
                    continue
                self.log(f"lane {lane}: claim: {e.message}")
                self.stopping.wait(self._backoff(RETRY_WAIT_SECONDS))
                continue
            finally:
                # 응답 · 204 · 오류 · 시한 어느 경우든 이 claim 은 끝났다. 잡을 받았으면 `running`
                # 에 넣는 것과 같은 잠금 구간이다(R20) — 그 사이의 heartbeat 이 「없음」을 세지 않게
                with self._lock:  # mutcheck: claim-settled-before-running
                    if isinstance(claimed, dict):
                        job_id = (claimed.get("job") or {}).get("id")
                        if isinstance(job_id, int):
                            self.running[job_id] = lane
                    self._open_claims.discard(claim_seq)
            if claimed is None:
                # 서버가 기다리지 않고 204 를 줬다(wait 0 · long-poll 슬롯 소진) — 뜨거운 루프 금지
                if time.monotonic() - asked_at < CLAIM_MIN_INTERVAL:
                    self.stopping.wait(self._backoff(CLAIM_MIN_INTERVAL))
                continue
            try:
                self.run_claimed(lane, claimed)
            except Exception as e:  # noqa: BLE001 — 잡 하나의 오류가 레인을 죽이면 안 된다
                self.log(f"lane {lane}: {type(e).__name__}: {str(e)[:120]}")
            self.processed += 1
            if self.once:
                self.stopping.set()
                break

    def _spec_from_claim(self, claimed: dict[str, Any], lane: int) -> tuple[RunSpec, Job]:
        job = claimed.get("job") or {}
        preset = claimed.get("preset") or {}
        job_id = int(job["id"])
        src = job.get("source") or {}
        source = Source(
            mode=src.get("mode", MODE_TREE),
            repo=src.get("repo"),
            base_sha=src.get("base_sha"),
            dirty=src.get("dirty"),
            tree_hash=src.get("tree_hash"),
            bytes=src.get("bytes"),
            ref=src.get("ref"),
            sha=src.get("sha"),
        )
        requester = job.get("requester") or {}
        data = self.config.data_path
        irreversible = job.get("irreversible") is True
        frozen = job.get("disconnect_grace_seconds")
        grace = frozen if isinstance(frozen, int) and not isinstance(frozen, bool) else None
        spec = RunSpec(
            job_id=job_id,
            preset_name=str(job.get("preset") or preset.get("name") or ""),
            argv=tuple(str(a) for a in (preset.get("argv") or [])),
            env={str(k): str(v) for k, v in (preset.get("env") or {}).items()},
            env_passthrough=tuple(str(k) for k in (preset.get("env_passthrough") or [])),
            requires=tuple(str(k) for k in (preset.get("requires") or [])),
            timeout_seconds=job.get("timeout_seconds") or preset.get("timeout_seconds"),
            inputs=dict(job.get("inputs") or {}),
            requester_label=str(requester.get("label") or job.get("requested_by") or ""),
            source=source,
            workspace=data / "workspaces" / str(job_id),
            log_path=data / "jobs" / str(job_id) / "log.txt",
            grace_seconds=self._grace_for(job_id, irreversible, grace),
            irreversible=irreversible,
            disconnect_grace_seconds=grace,
        )
        model = Job(
            id=job_id,
            preset=spec.preset_name,
            inputs=spec.inputs,
            key="",
            concurrency_group=job.get("concurrency_group"),
            source=source,
            requester=Requester(name=str(requester.get("name") or ""), label=spec.requester_label),
            state=str(job.get("state") or "running"),
            created_at=self.now_fn(),
            timeout_seconds=spec.timeout_seconds,
            lane=lane,
        )
        return spec, model

    def _materialize(self, spec: RunSpec, job: Job) -> None:
        if spec.workspace.exists():
            shutil.rmtree(spec.workspace, ignore_errors=True)
        if job.source.mode == MODE_GIT_REF:
            repo = self.config.repo(job.source.repo)
            if repo is None:
                # 서버에는 있고 이 워커에는 없다 — 고칠 설정이 **워커 쪽**이라 `where` 로 말한다.
                raise MaterializeError("repo_missing", repo=job.source.repo or "", where="worker")
            spec.workspace.parent.mkdir(parents=True, exist_ok=True)
            spec.log_path.parent.mkdir(parents=True, exist_ok=True)

            def _log(line: str) -> None:
                try:
                    with spec.log_path.open("ab") as fh:
                        fh.write(line.encode("utf-8", errors="replace") + b"\n")
                except OSError:
                    pass

            prepare_git_ref(
                job,
                spec.workspace,
                repo_name=repo.name,
                repo_url=repo.url,
                credential=repo.credential,  # 이 워커 머신의 [[repos]] — 토큰은 선을 타지 않는다
                mirror=self.config.data_path / "mirrors" / repo.name,
                timeout=self.config.git_fetch_timeout_seconds,
                log=_log,
            )
            return
        tar_path = self.config.data_path / "jobs" / str(job.id) / "tree.tar.gz"
        try:
            self.client.download_tree(job.id, tar_path)
        except ClientError as e:
            if e.status in (0, 503):
                # rcm 서버와의 끊김이다 — 확정 결함(404 · 409)과 달리 `lost`(결정 259)
                raise SnapshotUnreachable(log=f"download failed: {e.message}") from e
            # `e.message` 에는 서버가 돌려준 문구와 경로가 섞인다 — 상태 코드만 공개로 남긴다.
            raise MaterializeError(
                "snapshot_download_failed", status=e.status, log=f"download failed: {e.message}"
            ) from e
        try:
            extract_tree(tar_path, spec.workspace)
        finally:
            tar_path.unlink(missing_ok=True)

    def run_claimed(self, lane: int, claimed: dict[str, Any]) -> None:
        spec, job = self._spec_from_claim(claimed, lane)
        with self._lock:
            self.running[job.id] = lane
        self.log(f"lane {lane}: claimed #{job.id} {job.preset}")
        # 그 잡의 보고 큐와 보내는 스레드(§4.5) — claim 뒤에 시작해 큐가 끝나면 끝난다
        outbox = JobOutbox(self._job_dir(job.id))
        sender = _JobSender(self, job.id, outbox)
        with self._lock:
            self._senders[job.id] = sender
        sender.start()
        observer = _RemoteObserver(
            self,
            job.id,
            irreversible=spec.irreversible,
            grace_seconds=spec.disconnect_grace_seconds,
            grace_j=spec.grace_seconds,
            outbox=outbox,
            sender=sender,
        )
        with self._lock:
            self._observers[job.id] = observer
        try:
            self._run_claimed(lane, claimed, spec, job, observer)
        except Exception as e:  # noqa: BLE001 — 예외가 레인 루프로 새면 잡이 `running` 에 멈춘다
            if observer.finish_sent:  # mutcheck: run-claimed-error-leaves-job-running
                raise
            self.log(f"lane {lane}: #{job.id} worker error: {type(e).__name__}")
            try:
                observer.final_flush()
            except Exception:  # noqa: BLE001
                pass
            result = observer.result
            if result is not None:  # mutcheck: safety-net-drops-the-result
                # 러너는 이미 결과를 냈다 — 그 결과와 그 `group_gone` 을 보낸다(P3 2라운드 LOW).
                # 버리면 끝난 잡이 실패로 남고 확인된 그룹도 홀드된다. 산출물은 없다
                outcome, summary, code, args = self._result_outcome(spec, observer, result)
                self._finish(
                    job.id,
                    outcome,
                    result.rc,
                    summary,
                    observer,
                    finished=result,
                    summary_code=code,
                    summary_args=args,
                    group_gone=getattr(result, "group_gone", None),
                )
                self._cleanup(spec, failed=outcome != SUCCEEDED)
                return
            # 띄우기 전이면 그룹은 없다. 띄운 뒤면 러너가 그룹을 끝내고 확인한 값이 실린다. 모르면
            # 싣지 않는다 — 서버는 표식 잡이면 홀드로 읽는다
            gone = getattr(e, "group_gone", None) if observer.launching else True
            self._finish(
                job.id,
                FAILED,
                None,
                f"worker error: {type(e).__name__}",
                observer,
                group_gone=gone,  # mutcheck: safety-net-unlaunched-group-unknown
            )
            self._cleanup(spec, failed=True)

    def _result_outcome(
        self, spec: RunSpec, observer: Any, result: Any
    ) -> tuple[str, str | None, str | None, dict[str, Any] | None]:
        """러너의 결과 → (outcome, summary, summary_code, summary_args)."""
        if result.lost:
            summary, code, args = self._stop_summary(observer)
            return LOST, summary, code, args
        if result.cancelled:
            return CANCELLED, None, None, None
        if result.timed_out:
            return TIMED_OUT, format_limit(spec.timeout_seconds), None, None
        return (SUCCEEDED if result.rc == 0 else FAILED), None, None, None

    def _run_claimed(
        self, lane: int, claimed: dict[str, Any], spec: RunSpec, job: Job, observer: Any
    ) -> None:
        try:
            self._write_claim(job.id, spec.irreversible, spec.disconnect_grace_seconds)
        except OSError as e:
            self.log(f"#{job.id} session folder: {type(e).__name__}")
        started = time.monotonic()
        outcome = FAILED
        rc: int | None = None
        summary: str | None = None
        try:
            if not spec.argv:
                # 잡이 큐에 있는 사이 서버 설정에서 프리셋이 사라졌다 — claim payload 의 `preset`
                # 이 `null` 로 온다. 로컬 레인이 같은 입력에 남기는 코드와 **같은 코드**여야
                # 한다: 원격에서만 다른 코드가 나오면 「어느 레인이 집었나」가 요약을 바꾼다.
                raise RunnerError("preset_missing", preset=spec.preset_name)
            observer.phase(PHASE_MATERIALIZING)
            result = run_job(
                spec,
                observer,
                now_fn=self.now_fn,
                environ=self.environ,
                materialize=lambda s: self._materialize(s, job),
                mono_fn=self.mono_fn,  # mutcheck: runner-own-monotonic-clock
            )
            observer.result = result
        except RequiredToolMissing as e:
            observer.final_flush()
            skipped = CollectResult(state=art.SKIPPED, reason_code="not_run")
            if observer.should_stop() or observer.should_cancel():
                # 검사와 보고 사이에 종료·취소가 왔다 — 그게 이긴다(M5l S4). 프로세스는 없었다.
                outcome = LOST if observer.should_stop() else CANCELLED
                summary, code, args = (
                    self._stop_summary(observer) if outcome == LOST else (None, None, None)
                )
                self._finish(
                    job.id,
                    outcome,
                    None,
                    summary,
                    observer,
                    artifacts=_disposition(skipped),
                    summary_code=code,
                    summary_args=args,
                    group_gone=True,  # 프로세스를 띄운 적이 없다
                )
                self.log(f"lane {lane}: #{job.id} {outcome} before start")
                self._cleanup(spec, failed=True)
                return
            # 최종 환경에 도구가 없다 — 프로세스는 뜨지 않았다. 구조화된 코드로 보고한다(M5j G4);
            # 서버가 로컬 워커와 같은 `tool_missing` 을 남긴다. PATH 는 어디에도 싣지 않는다.
            summary, code, args = outcome_summary("tool_missing", tool=e.public_name)
            self._finish(
                job.id,
                FAILED,
                None,
                summary,
                observer,
                artifacts=_disposition(skipped),
                summary_code=code,
                summary_args=args,
                group_gone=True,
            )
            self.log(f"lane {lane}: #{job.id} failed — {summary}")
            self._cleanup(spec, failed=True)
            return
        except (MaterializeError, RunnerError) as e:
            # 원문(경로·git stderr·`argv[0]`)은 **서버의 잡 로그**로 — 거긴 토큰이 있어야 읽는다.
            # 로컬 레인은 `_append_log` 로 같은 일을 한다. 이게 없으면 공개 요약만 남아서, 코드는
            # 「명령을 못 찾았다」고 말하는데 어느 명령인지 아무 데도 안 남는다(F2b · 검토 7).
            if e.log:
                observer.output(f"[rcm] {e.log}\n".encode())
            observer.final_flush()
            # 실행이 시작되지 못했다 — 조용히 비우지 않고 그 사실을 처분으로 남긴다(§5)
            skipped = CollectResult(state=art.SKIPPED, reason_code="not_run")
            if observer.should_stop() or observer.should_cancel():
                # 준비 중에 종료·취소가 왔다 — 그게 이긴다(M5l S4). 프로세스는 없었고, 취소가
                # 준비를 끊지 못했다는 이유로 사용자가 멈춘 잡이 실패로 남으면 안 된다.
                outcome = LOST if observer.should_stop() else CANCELLED
                summary, code, args = (
                    self._stop_summary(observer) if outcome == LOST else (None, None, None)
                )
                self._finish(
                    job.id,
                    outcome,
                    None,
                    summary,
                    observer,
                    artifacts=_disposition(skipped),
                    summary_code=code,
                    summary_args=args,
                    group_gone=getattr(e, "group_gone", None) is not False,
                )
                self.log(f"lane {lane}: #{job.id} {outcome} before start")
                self._cleanup(spec, failed=True)
                return
            # 예외가 **코드**를 들고 온다(`PREFLIGHT_CODES`) — 로컬 레인이 같은 입력에 남기는
            # 행과 같은 코드·같은 인자여야 화면이 「어느 레인이 집었나」에 흔들리지 않는다.
            # 원문은 위에서 **보호된 잡 로그**로 보냈다: 공개 요약에는 절대 싣지 않는다(F2b).
            summary, code, args = outcome_summary(e.code, **e.args_public)
            # 스냅샷을 받는 중 rcm 서버와 끊겼으면 `lost`(M5t-b 결정 259 · LOST_CODES)
            unreachable = isinstance(e, SnapshotUnreachable)
            if unreachable:
                summary, code, args = outcome_summary("snapshot_unreachable")
            self._finish(
                job.id,
                LOST if unreachable else FAILED,
                None,
                summary,
                observer,
                artifacts=_disposition(skipped),
                summary_code=code,
                summary_args=args,
                # 감독자가 떴다면 그룹 소멸은 S7 이 정했다(표지를 읽지 않는다, 결정 339-c (3))
                group_gone=getattr(e, "group_gone", None) is not False,
            )
            self.log(f"lane {lane}: #{job.id} failed — {summary}")
            self._cleanup(spec, failed=True)
            return
        observer.final_flush()
        rc = result.rc
        outcome, summary, code, args = self._result_outcome(spec, observer, result)
        # 워크스페이스를 지우기 전에 모으고, `self.running` 에서 빠지기 전에 올린다(§5)
        bundle = self._collect_artifacts(job.id, claimed, spec, result)
        self._finish(
            job.id,
            outcome,
            rc,
            summary,
            observer,
            artifacts=_disposition(bundle),
            finished=result,
            summary_code=code,
            summary_args=args,
            group_gone=getattr(result, "group_gone", None),
        )
        self.log(f"lane {lane}: #{job.id} {outcome} in {_fmt_seconds(time.monotonic() - started)}")
        self._cleanup(spec, failed=outcome != SUCCEEDED)

    def _collect_artifacts(
        self, job_id: int, claimed: dict[str, Any], spec: RunSpec, result: Any
    ) -> CollectResult | None:
        """모아서 서버에 올린다. 오류는 여기서 잡는다 — 산출물 때문에 잡이 죽지 않는다(§5)."""
        policy = _policy_from_claim(claimed)
        state = art.prospective_state(
            result.rc, cancelled=result.cancelled, timed_out=result.timed_out
        )
        if policy is None or not policy.collects_for(state):
            return None
        # 스풀은 **지우지 않는다.** 전송 결과가 불확실하면 다시 보낼 수 있어야 한다(§5).
        # 무한정 쌓이지 않게 시작할 때 `_sweep_workspaces` 가 나이로 쓸어 간다. 올리기는 보고
        # 큐에서 finish 바로 앞 단계다(M5t-b 결정 275) — 재시작 뒤에도 같은 스풀을 올린다
        staging = self.config.data_path / "artifacts" / f"{job_id}.{secrets.token_hex(8)}"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            forced = result.cancelled or result.timed_out
            out = collect(
                spec.workspace,
                policy,
                staging,
                now_fn=self.now_fn,
                budget_seconds=policy.cancel_timeout_seconds if forced else None,
            )
            if out.state != art.READY or out.bundle_path is None:
                return out
            with self._lock:
                sender = self._senders.get(job_id)
            if sender is None:  # 큐 없이 부른 시험 · 안전망 — 오늘처럼 바로 올린다
                receipt = self.client.upload_bundle(job_id, out.bundle_path)
                return dataclasses.replace(out, bundle_sha256=receipt.get("bundle_sha256"))
            sender.ob.bundle = {"path": str(out.bundle_path), "state": "pending", "sha256": None}
            sender.ob.save()
            return out
        except Exception as e:  # noqa: BLE001 — 수집·업로드 실패가 잡을 죽이지 않는다
            self.log(f"lane: #{job_id} artifacts: {type(e).__name__}")
            return CollectResult(state=art.FAILED, reason_code="upload_failed")

    def _finish(
        self,
        job_id: int,
        outcome: str,
        rc: int | None,
        summary: str | None,
        observer: Any,
        *,
        artifacts: dict[str, Any] | None = None,
        finished: Any = None,
        summary_code: str | None = None,
        summary_args: dict[str, Any] | None = None,
        group_gone: bool | None = None,
    ) -> None:
        """완료를 그 잡의 보고 큐에 적고 큐가 끝날 때까지 레인이 기다린다(M5t-b §4.5). 큐가 끝난
        **뒤에** heartbeat 목록에서 뺀다 — 먼저 빼면 서버가 그 잡을 「잊힌 잡」으로 닫는다(F10).

        finish 기록은 `outbox.json` 에 먼저 쓴다. 그래서 보내기 전에 프로세스가 죽어도 다음
        프로세스가 죽은 세션의 큐로 보낸다(RL14 a). `log_bytes` 는 그 잡이 낸 스트림 전체 바이트다
        (스풀이 깨졌으면 센 값)이다. 서버는 `log_offset < log_bytes` 면 `log_complete = false` 다.

        `group_gone` 은 그 잡의 프로세스 그룹이 사라졌다는 확인이다(M5t-b 결정 330). 프로세스를
        띄운 적이 없으면 참이다. 모르면 싣지 않는다 — 서버는 표식 잡이면 홀드로 읽는다.
        펜싱된 프로세스(W5)의 finish 는 결과가 무엇이든 보내는 순간에 좁은 확인(`lost` +
        `worker_replaced` + `group_gone`)으로 바뀐다(결정 261, P6 GAP2).
        """
        if observer is not None:
            observer.finish_sent = True  # 바깥 안전망(`run_claimed`)이 두 번 보내지 않게
        with self._lock:
            self.cancel_requested.discard(job_id)
            self._observers.pop(job_id, None)  # 프로세스는 끝났다 — 감시할 것이 없다
            sender = self._senders.get(job_id)
        if getattr(observer, "irreversible", False) and group_gone is not True:
            self._group_unconfirmed.add(job_id)  # mutcheck: unconfirmed-group-folder-dropped
        if sender is None:
            # 큐 없이 부른 경우(시험 · 안전망) — 메모리 큐 하나로 같은 길을 간다
            sender = _JobSender(self, job_id, getattr(observer, "outbox", None) or JobOutbox(None))
            with self._lock:
                self._senders[job_id] = sender
            sender.start()
        ob = sender.ob
        record: dict[str, Any] = {
            "outcome": outcome,
            "exit_code": rc,
            "summary": summary,
            "summary_code": summary_code,
            "summary_args": dict(summary_args or {}) if summary_code is not None else None,
            "group_gone": group_gone,
            "artifacts": artifacts,
            "log_bytes": ob.length,
            "finished_at": (
                _iso(finished.finished)
                if finished is not None and getattr(finished, "finished", None) is not None
                else None
            ),
        }
        if getattr(observer, "closed_state", None) in FINISH_RECORDED:
            # 서버가 이 세션의 finish 를 이미 기록했다(W4 표 첫 행) — 다시 보내지 않는다. 서버가
            # 닫은 다른 끝 상태(lost · cancelled)에는 보낸다 — 늦은 결과 · 확인 · 홀드 풀기로 쓰인다
            ob.done = True
        else:
            ob.finish = record
            ob.ended_at = self._epoch()
        ob.save()
        sender.wake.set()
        gave_up = not self._await_queue(job_id, sender)
        with self._lock:
            self.running.pop(job_id, None)
            if self._senders.get(job_id) is sender:
                self._senders.pop(job_id, None)
            # 못 보냈으면 폴더를 남긴다 — 다음 프로세스가 죽은 세션의 큐로 보낸다(§4.4 의 7).
            # 지우면 finish 와 그 확인이 사라진다(P6-2 #3)
            if gave_up:  # mutcheck: unsent-finish-folder-dropped
                return
        self._forget_job_dir(job_id)  # 보고가 끝났다 — 다음 프로세스가 판정할 것이 없다

    def _await_queue(self, job_id: int, sender: _JobSender) -> bool:
        """레인이 제 잡의 큐가 끝나기를 기다린다. 끝났으면(`done`) 참. 멈추는 중이면 보내는 스레드가
        `STOP_REPORT_SECONDS` 뒤 디스크에 남기고 끝난다. 드레인 중이면 `DRAIN_REPORT_SECONDS` 까지만
        기다린다 — 그 뒤는 오늘처럼 exec 하고 새 이미지가 등록 전에 보낸다(M5q · §4.4)."""
        waited_since: float | None = None
        while sender.alive():
            sender.thread.join(0.2)
            if self.draining.is_set() and not self.stopping.is_set():
                now = time.monotonic()
                waited_since = waited_since or now
                if now - waited_since >= DRAIN_REPORT_SECONDS and not sender.ob.done:
                    # 큐는 디스크에 남는다 — 새 이미지가 등록 전에 죽은 세션의 큐로 보낸다
                    self.log(f"job #{job_id} still not reported — restarting anyway")
                    sender.halt.set()
                    sender.thread.join(2)
                    return False
        return sender.ob.done

    def _cleanup(self, spec: RunSpec, *, failed: bool) -> None:
        keep = failed and self.config.keep_workspace_on_failure
        if not keep:
            shutil.rmtree(spec.workspace, ignore_errors=True)

    def _sweep_workspaces(self) -> None:
        """시작 시 `WORKSPACE_KEEP_DAYS` 넘은 워크스페이스·잡 디렉터리를 지운다."""
        cutoff = time.time() - WORKSPACE_KEEP_DAYS * 86400
        for base in (
            self.config.data_path / "workspaces",
            self.config.data_path / "jobs",
            self.config.data_path / "artifacts",  # 올리고 남긴 묶음 스풀(M5e)
        ):
            if not base.is_dir():
                continue
            for entry in base.iterdir():
                try:
                    if entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry, ignore_errors=True)
                except OSError:
                    continue

    # ── 수명 ────────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> int:
        """블로킹. 등록 → 스레드 → 정지까지. 종료 코드."""
        self.config.data_path.mkdir(parents=True, exist_ok=True)
        self._sweep_workspaces()
        sweep_sessions(self.config.data_path)
        # 등록 전에 프로토콜을 본다 — 옛 서버면 등록 · claim 없이 종료 3(M5t-b T13). 세션 폴더는 그
        # 뒤에 만든다 — 재시작 루프가 빈 폴더를 쌓지 않게(V2-12)
        self.check_protocol()
        self.prepare_sessions()
        # 죽은 세션 D 의 보고 큐를 D 의 id 로 먼저 보낸다(§4.4 의 7) — 등록은 그 뒤다
        self.send_dead_queues()
        try:
            out = self.register()
        except BaseException:
            # 등록하지 못한 시작(종료 2 · 3 · 0 · 따라가기)은 폴더를 남기지 않는다(P6 GAP1)
            self._drop_own_session()
            raise
        self.schedule_rechecks()
        self.log(
            f"rcm worker {self.name} · pool {out.get('pool')} · lanes {out.get('lanes')} · "
            f"server {self.client.server} · heartbeat {self.heartbeat_seconds}s"
        )
        self.sampler = HostSampler(
            self.config.host,
            name=self.name,
            publish=lambda kind, data: None,
            stop=self.stopping,
            now_fn=self.now_fn,
            disk_path=str(self.config.data_path),
        )
        self.sampler.start()
        # 레인이 뜨기 **전에** 재서 보낸다 — 첫 claim 부터 이 워커가 무엇을 할 수 있는지 서버가 안다
        self.measure_and_report()
        self.heartbeat_once()
        hb = threading.Thread(target=self._heartbeat_loop, name="rcm-worker-heartbeat", daemon=True)
        hb.start()
        dog = self.start_watchdog()
        self._threads = [
            threading.Thread(
                target=self._lane_loop, args=(lane,), name=f"rcm-worker-lane-{lane}", daemon=True
            )
            for lane in range(1, self.config.lanes + 1)
        ]
        for t in self._threads:
            t.start()
        try:
            while any(t.is_alive() for t in self._threads):
                if self._drained():
                    break
                self.stopping.wait(0.2)
        finally:
            # 드레인이 끝났으면 여기서 처음 켠다 — 레인은 이미 잡 없이 기다리고 있다(§3.3 의 4)
            self.stopping.set()
            if self.once and self.draining.is_set():
                self.exit_code = 2  # `--once` 의 ④ — 등록이 막혀 잡을 받지 못했다(§14 B질문4)
            for t in self._threads:
                t.join(timeout=self.config.grace_seconds + 10)
            hb.join(timeout=2)
            dog.join(timeout=2)
            if self.restart_for is None:
                self.log("stopped")  # 드레인 끝이면 `cmd_worker` 가 재실행 줄을 쓴다
        if self.fenced.is_set():
            return 0  # 펜싱 — 감독자가 되살리지 않는다(결정 258)
        return self.exit_code


__all__ = [
    "DRAIN_REPORT_SECONDS",
    "RemoteWorker",
    "WorkerExit",
    "WorkerFollow",
    "STOP_SUMMARY",
    "judge_orphan",
    "linux_start",
    "ps_start",
]
