"""원격 워커 프로토콜(M5b-2) — `/worker/register · claim · heartbeat` 와
`/worker/jobs/{id}/tree|phase|log|finish`.

서버 쪽만이다(워커 프로세스 `rcm worker` 는 M5b-3). `App` 이 이 믹스인을 상속해 규칙을 갖고,
HTTP 핸들러는 얇게 여기를 부른다.

규칙(docs/m5b2-workplan.md):
- 워커 토큰(`kind = worker`)만 `/worker/*` 를 쓴다. 워커 이름 = 토큰 이름.
- 워커 상태는 **서버가 받은 시각** `last_seen_at` 로만: `now − last_seen_at` 이
  `worker_timeout_seconds` 를 넘으면 down 이고 그 워커의 running·cancelling 잡은 lost.
  워커 payload 의 시각은 어디에도 안 쓴다.
- 레인 상태의 정본은 DB(그 워커의 running 잡 `lane`). heartbeat 의 `jobs` 목록은 조정용 —
  서버가 아는 running 잡이 목록에 없으면 워커가 잊은 잡이라 lost.
- `register` 는 새 프로세스라는 뜻 — 그 이름의 활성 잡을 먼저 lost 로 닫는다.
- 워커는 자기가 claim 한 잡만 보고한다(`jobs.worker_name`). 종료된 잡에 대한 늦은 보고는 409 로
  거절하고 아무것도 바꾸지 않는다.
- 취소: 워커가 heartbeat 은 계속 보내면서 `kill_at + 2 × heartbeat` 가 지나도 finish 를 안 하면
  서버가 cancelled 로 닫는다. 워커가 닿지 않으면 lost 가 우선한다.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import sqlite3
import tarfile
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from remote_ci_monitor.collect import CollectResult
from remote_ci_monitor.config import _NAME_RE, disconnect_grace
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.artifacts import BundleFile
from remote_ci_monitor.core.hostparse import sample_from_json
from remote_ci_monitor.core.model import (
    BUSY_STATES,
    CANCELLED,
    CANCELLING,
    DEFAULT_POOL,
    FAILED,
    LOST,
    MODE_GIT_REF,
    PHASE_EXECUTING,
    PHASE_MATERIALIZING,
    RETRIED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    TOKEN_WORKER,
    WORKER_BUSY,
    WORKER_DOWN,
    WORKER_HELD,
    WORKER_IDLE,
    HostSample,
    Job,
    WorkerInfo,
)
from remote_ci_monitor.core.progress import parse_marker
from remote_ci_monitor.core.status import iso, source_json
from remote_ci_monitor.materialize import MaterializeError, assemble_tar_from_manifest
from remote_ci_monitor.release_secrets import mask_bytes
from remote_ci_monitor.store import (
    RELEASE_WORKER,
    LaneBusy,
    SessionFenced,
    TokenInfo,
    WorkerRow,
)

if TYPE_CHECKING:
    from remote_ci_monitor.config import ServerConfig
    from remote_ci_monitor.store import Store

CLAIM_WAIT_SLOTS = 8  # 동시에 long-poll 로 기다리는 claim 수. 넘치면 바로 204
CLAIM_POLL_SECONDS = 0.5
MAX_WORKER_LOG_BODY = 4 * 1024 * 1024
MAX_WORKER_LANES = 64
WORKER_PHASES = (PHASE_MATERIALIZING, PHASE_EXECUTING)
WORKER_OUTCOMES = (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, LOST)
#: 문장은 `core.outcome` 표가 만든다(결정 37). 이 이름들은 옛 호출부와 테스트를 위해 남긴다.
SUMMARY_RESTARTED = "worker {name} restarted without the job"
SUMMARY_UNREACHABLE = "worker {name} unreachable for {seconds}s"
SUMMARY_CANCEL_UNCONFIRMED = "worker did not confirm the cancel"

#: 워커 프로토콜 판(M5t-b §4.7 · 결정 258 · 270). 1 은 세션이 없는 옛 판이다
WORKER_PROTOCOL = 2
#: 409 `protocol_required` 의 문구. 「not registered」를 담지 않는다 — 옛 워커는 claim 409 의
#: `error` 에 그 부분 문자열이 있으면 재등록 루프를 돈다(결정 329 · F40)
PROTOCOL_REQUIRED = "worker protocol 2 required: upgrade rcm on this worker"
SESSION_FENCED = "this worker process was replaced by a newer one with the same token"
SESSION_FOREIGN = "session belongs to another worker"
#: 본문이 없는 워커 라우트(log · tree · artifacts)의 세션 헤더(§4.10)
SESSION_HEADER = "X-RCM-Worker-Session"
#: 세션 id 의 모양 — `fullmatch` 로만 쓴다. `$` 는 끝 줄바꿈 앞에서도 맞는다(P6 GAP7)
_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
#: heartbeat `seq` · `claims_settled` · claim `claim_seq` 의 상한(JSON 정수가 잃지 않는 범위)
MAX_SEQ = 2**53 - 1
#: janitor 한 틱에 유예로 닫는 잡의 상한(결정 339-g). 늦게 닫는 것은 안전한 쪽이라 판단이고 재지
#: 않았다 — 서버 재시작 뒤 한꺼번에 넘어도 다른 janitor 일을 막지 않는다
GRACE_CLOSES_PER_TICK = 20
#: 세션마다 기억하는 「조용했다가 다시 들림」 기록 수의 상한(T4) — 메모리 상한일 뿐이다. 기록은
#: `_prune_resumed` 가 열린 취소의 가장 이른 요청보다 오래된 것부터 지운다. 개수로 자르면 요청을
#: 걸친 침묵을 끝낸 기록(창의 시작)이 그 뒤의 짧은 침묵 기록들에 밀려 지워졌다(P3 3라운드 LOW).
#: 이 상한에 닿으려면 취소 하나가 열린 동안 2H 넘는 침묵이 이만큼 있어야 한다
RESUMED_KEEP = 1024


def _api_error(status: int, message: str, **extra: Any) -> Exception:
    from remote_ci_monitor.server import ApiError  # 순환 import 를 피한다

    return ApiError(status, message, **extra)


#: SQLite 의 「지금 바쁘다」. 이것만 일시 오류(503)로 본다 — `no such table` 같은 영구 결함은
#: 그대로 500 으로 올린다.
_BUSY_MARKERS = ("locked", "busy")


def _is_busy_error(e: sqlite3.OperationalError) -> bool:
    return any(m in str(e).lower() for m in _BUSY_MARKERS)


#: 오프셋 헤더(M5t-b §4.5 · 결정 329) — 조각의 첫 바이트가 그 잡 스트림(가리기 전)의 몇째 바이트인가
LOG_OFFSET_HEADER = "X-RCM-Log-Offset"
_OFFSET_RE = re.compile(r"[0-9]{1,18}")


def log_offset_arg(raw: str | None) -> int | None:
    """`X-RCM-Log-Offset` 의 값. 없으면 None, 0 이상의 정수가 아니면 400(`error` 만)."""
    if raw is None:
        return None
    if not _OFFSET_RE.fullmatch(raw):
        raise _api_error(400, f"{LOG_OFFSET_HEADER} must be a non-negative integer")
    return int(raw)


def _write_at(fh: Any, pos: int, data: bytes) -> None:
    """로그 쓰기 절차 step 7 — 커밋된 길이 자리에 쓰고 그 뒤를 자른다. 덧붙이지 않는다: 앞 요청이
    남긴 미커밋 꼬리나 부분 쓰기가 남지 않는다(V1-3)."""
    fh.seek(pos)  # mutcheck: log-append-not-positioned
    fh.write(data)
    fh.truncate(pos + len(data))
    fh.flush()


def _open_line(fh: Any, pos: int) -> bytes:
    """파일의 `pos` 앞에서 줄 끝 없이 열려 있는 마지막 줄(많아야 4 KiB). 마커 파싱에만 쓴다."""
    start = max(0, pos - 4096)
    fh.seek(start)
    head = fh.read(pos - start)
    cut = head.rfind(b"\n")
    return head[cut + 1 :] if cut >= 0 else (head if start == 0 else b"")


def _truncate_quietly(fh: Any, pos: int) -> None:
    try:
        fh.truncate(pos)
        fh.flush()
    except OSError:
        pass


def _api_error_type() -> type[Exception]:
    from remote_ci_monitor.server import ApiError

    return ApiError


def _int_field(body: dict[str, Any], key: str, lo: int, hi: int, *, default: int | None = None):
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise _api_error(400, f"{key} must be an integer")
    if not lo <= v <= hi:
        raise _api_error(400, f"{key} must be between {lo} and {hi}")
    return v


def _finish_outcome(code: str, **args: Any) -> dict[str, Any]:
    """`store.finish` 에 바로 넣을 요약 세 값(결정 37)."""
    text, code, clean = outcome.summary(code, **args)
    return {"summary": text, "summary_code": code, "summary_args": clean}


#: 워커가 finish 에 실어 보낼 수 있는 구조화된 요약 코드 — 프로세스가 뜨기 **전**의 실패뿐이다
#: (M5j G4 · F2b). 나머지 코드는 서버가 마커·종료 코드로 스스로 만든다. 목록은 `core/outcome` 이
#: 정본이다: 워커와 서버가 각자 목록을 들면 원격 잡의 요약만 조용히 달라진다.
#:
#: fail-closed 다 — 모르는 코드는 400 이지 「그냥 저장」이 아니다(검증 G4.15). **이 코드가 강제하는
#: 것은 여기까지다**: 등록 뒤의 요청은 워커 버전을 다시 보지 않으므로(`_registered` 는 행의 존재만
#: 본다), 서버만 새 버전으로 재기동하면 구 워커가 그대로 heartbeat·claim·finish 를 계속한다.
#: 그러니 이 목록에서 코드를 **빼는** 변경은 그 자리에서 배포 사고다: 구 워커의 finish 가 400 을
#: 받고 잡이 안 닫혀 heartbeat 시한으로 `lost` 가 된다. 더하는 것은 안전하다(구 워커는 안 보낸다).
WORKER_PREFLIGHT_CODES: tuple[str, ...] = outcome.PREFLIGHT_CODES


def _preflight_summary(
    body: dict[str, Any], reported: Any, rc: Any
) -> tuple[str, str, dict[str, Any]] | None:
    """finish 의 `summary_code`/`summary_args` 를 검증해 `(문장, 코드, 인자)` 로. 키가 없으면
    None(옛 워커). 아는 코드만, `failed` + `exit_code null` 일 때만 받고, 인자는 **코드마다 정한
    키와 모양**만 지난다(`outcome.PREFLIGHT_ARGS`) — 워커가 PATH·경로·자유 문구를 실어 보내도
    공개 상태에 남지 않는다(PLAN 「보안」).

    워커는 토큰이 있을 뿐 신뢰 경계 안이 아니다: 잡 요약을 짜낼 권한까지 준 적은 없다. 그래서
    `tool` 은 워커가 이미 basename 을 떴어도 서버가 **다시** 뜨고, 멤버 이름도 다시 이름만 남긴다.
    문장은 워커가 보낸 것이 아니라 이 자리에서 코드로 그린 것이다.
    """
    code = body.get("summary_code")
    if code is None:
        return None
    if code not in WORKER_PREFLIGHT_CODES:
        raise _api_error(400, f"summary_code must be one of {', '.join(WORKER_PREFLIGHT_CODES)}")
    if reported != FAILED or rc is not None:
        raise _api_error(400, f"summary_code {code} needs outcome failed and exit_code null")
    args = body.get("summary_args")
    if not isinstance(args, dict):
        raise _api_error(400, "summary_args must be an object")
    try:
        clean = outcome.clean_args(code, args, strict=True)
        need = outcome.PREFLIGHT_REQUIRED.get(code)
        if need is not None and need not in clean:
            raise outcome.OutcomeError(f"{code} needs a {need}")
    except outcome.OutcomeError as e:
        # 조용히 버리지 않고 400 을 준다: 워커가 인자를 잘못 보냈다는 것은 그 자체로 버그이고,
        # 반쪽짜리 요약을 저장하면 로컬 레인이 남기는 행과 달라진다(어느 레인이 집었나가 요약을
        # 바꾼다). 워커는 이 400 을 보고 잡을 자기 문장으로 닫는다.
        raise _api_error(400, f"summary_args: {e}") from e
    return outcome.summary(code, **clean)


def _lost_summary(
    body: dict[str, Any], reported: Any, name: str
) -> tuple[str, str, dict[str, Any]] | None:
    """finish 의 `lost` 코드(`outcome.LOST_CODES`, M5t-b 결정 278). `outcome = lost` 일 때만 받고
    인자는 코드마다 정한 키만 지난다. `worker_replaced` 의 이름은 서버가 토큰 이름으로 채운다."""
    code = body.get("summary_code")
    if code not in outcome.LOST_CODES:
        return None
    if reported != LOST:
        raise _api_error(400, f"summary_code {code} needs outcome lost")
    args = body.get("summary_args", {})
    if not isinstance(args, dict):
        raise _api_error(400, "summary_args must be an object")
    try:
        clean = outcome.clean_args(code, args, strict=True)
    except outcome.OutcomeError as e:
        raise _api_error(400, f"summary_args: {e}") from e
    if code == "worker_replaced":
        clean = {"name": name}
    return outcome.summary(code, **clean)


def _group_gone(body: dict[str, Any]) -> bool | None:
    """finish 의 `group_gone`(결정 330): 러너가 돌아온 뒤 그 잡의 프로세스 그룹이 사라졌다는
    확인. 없으면 None(옛 워커)이고 확인이 아니다."""
    v = body.get("group_gone")
    if v is not None and not isinstance(v, bool):
        raise _api_error(400, "group_gone must be true or false")
    return v


def _stream_to_file(stream: Any, dest: Path, length: int) -> str:
    """`length` 바이트를 그대로 파일에 흘려 넣고 sha256 을 돌려준다. 메모리에 안 올린다."""
    digest = hashlib.sha256()
    left = length
    with dest.open("wb") as fh:
        while left > 0:
            chunk = stream.read(min(1 << 16, left))
            if not chunk:
                raise _api_error(400, "artifact upload ended early")
            digest.update(chunk)
            fh.write(chunk)
            left -= len(chunk)
    return digest.hexdigest()


def _manifest_from_tar(path: Path, max_files: int) -> list[BundleFile] | None:
    """올라온 tar 에서 매니페스트를 만든다 — 목록은 서버가 **본 것**이지 워커가 말한 것이 아니다.

    finish 본문으로 받지 않는 이유: 파일 만 개짜리 목록은 JSON 본문 상한을 훌쩍 넘는다.

    읽을 수 없는 묶음이면 `None` — 「목록을 모른다」다. 바이트와 해시는 그대로 검증하고
    저장한다. 못 본 파일을 봤다고 말하지 않는다(§3). 안전하지 않은 경로는 그때도 거절한다.
    """
    files: list[BundleFile] = []
    try:
        tar = tarfile.open(path, "r:")
    except (tarfile.TarError, OSError):
        return None
    with tar:
        for member in tar:
            if not member.isfile():
                continue
            if len(files) >= max_files:
                raise _api_error(413, f"artifact bundle has more than {max_files} files")
            try:
                art.check_path(member.name)
            except art.PolicyError as e:
                raise _api_error(400, f"artifact bundle has an unsafe path: {e}") from e
            fh = tar.extractfile(member)
            digest = hashlib.sha256()
            if fh is not None:
                while True:
                    chunk = fh.read(1 << 16)
                    if not chunk:
                        break
                    digest.update(chunk)
            files.append(
                BundleFile(
                    path=member.name,
                    size=member.size,
                    sha256=digest.hexdigest(),
                    mode=0o755 if member.mode & 0o111 else 0o644,
                )
            )
    return files


def _log_bytes(body: dict[str, Any]) -> int | None:
    """finish 의 `log_bytes`(M5t-b §4.6) — 그 잡이 낸 스트림 전체 바이트. 없으면 None(레거시)."""
    if body.get("log_bytes") is None:
        return None
    return _int_field(body, "log_bytes", 0, MAX_SEQ)


def _clamp(at: datetime, started: datetime | None, received: datetime) -> datetime:
    """워커가 보낸 끝난 시각을 `[started_at, 수신 시각]` 안으로(결정 277) — `job_seconds` 가 음수가
    되지 않는다."""
    if at > received:  # mutcheck: finished-at-unclamped
        return received
    if started is not None and at < started:
        return started
    return at


def _parse_iso(raw: Any) -> datetime | None:
    """워커가 보낸 ISO Z 시각. 이상하면 None — 조용히 수신 시각으로 물러선다."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class RemoteWorkersMixin:
    """`App` 에 섞이는 원격 워커 규칙. 아래 속성은 `App` 이 준다."""

    store: Store
    config: ServerConfig
    version: str
    wake: threading.Event
    stop: threading.Event

    # App 이 정의하는 메서드(타입 힌트용)
    def now_fn(self) -> datetime: ...  # type: ignore[empty-body]
    def job_dir(self, job_id: int) -> Path: ...  # type: ignore[empty-body]
    def log_path(self, job_id: int) -> Path: ...  # type: ignore[empty-body]
    def log(self, msg: str) -> None: ...
    def _publish_job(self, job: Job | None, job_id: int) -> None: ...
    def _publish_server(self) -> None: ...
    def _mark_dirty(self) -> None: ...
    def _on_marker(self, job_id: int, kind: str, value: str) -> None: ...
    def mask_for_preset(self, preset_name: str) -> tuple[bytes, ...]: ...  # type: ignore[empty-body]

    if TYPE_CHECKING:
        # `CapabilitiesMixin` 이 정의한다 — MRO 에서 이 믹스인이 앞이라 여기 몸통을 두면 가린다
        def _has_local_lanes(self) -> bool: ...

    def _remote_init(self) -> None:
        self._claim_slots = threading.BoundedSemaphore(CLAIM_WAIT_SLOTS)
        self._worker_samples: dict[str, HostSample] = {}
        self._log_partial: dict[int, bytes] = {}
        self._remote_lock = threading.Lock()
        # M5t-b(결정 328 · 339-a · 339-g): 세션마다 서버가 마지막으로 들은 단조 시각 `heard` 와
        # 받아들인 heartbeat `seq`. 메모리에만 두고 바닥은 기동 단조 시각이다(결정 266). 레거시
        # 요청은 `legacy:<이름>` 키를 쓴다. 판정 잠금은 세션마다 하나(`_session_locks`)이고
        # `_heard_lock` 은 그 사전에 접근하는 짧은 메모리 잠금이다 — 그 안에서 DB 를 만지지 않는다.
        self._heard_lock = threading.Lock()
        self._heard: dict[str, float] = {}
        self._hb_seq: dict[str, int] = {}
        # 조용했다가 다시 들린 때(벽시계)와 그 전에 조용했던 초 — 취소 미확인 창은 조용했던 시간을
        # 세지 않고 이때부터 새로 잰다(T4). 2H 를 넘긴 침묵만 적고 세션마다 최근 몇 개만 둔다
        self._resumed: dict[str, list[tuple[datetime, float]]] = {}
        self._session_locks: dict[str, threading.Lock] = {}
        self._synthetic_said: set[str] = set()  # 합성 그룹 로그는 프리셋마다 한 번(결정 330)
        # 잡별 로그 잠금(§4.5 step 3) — 사전 접근은 `_remote_lock` 아래, 잡이 끝나면 지운다
        self._job_log_locks: dict[int, threading.Lock] = {}

    def _sync_irreversible(self) -> None:
        """설정의 표식 프리셋 표를 저장소에 넣는다(M5t-b 결정 257 · 330). 기동과 reload 마다 부른다.

        claim 이 이 표로 `jobs.irreversible` 을 얼리고 그룹 NULL 인 옛 대기 잡의 유효 그룹을 정한다.
        레거시 잡의 표식 판정은 「설정에 있는 프리셋 이름」도 본다 — 없으면 모름이고 닫힌 쪽이다."""
        presets = self.config.presets
        self.store.irreversible = {
            p.name: (p.concurrency_group or f"irreversible:{p.name}", p.concurrency_scope)
            for p in presets
            if p.irreversible
        }
        self.store.known_presets = frozenset(p.name for p in presets)
        for p in presets:
            if p.irreversible and p.group_synthetic and p.name not in self._synthetic_said:
                self._synthetic_said.add(p.name)
                self.log(
                    f"preset {p.name}: irreversible without concurrency_group — using group "
                    f"{p.concurrency_group} ({p.concurrency_scope})"
                )

    # ── 세션 (M5t-b §4.2) ────────────────────────────────────────────────────

    def _session_lock(self, key: str) -> threading.Lock:
        with self._heard_lock:
            lock = self._session_locks.get(key)
            if lock is None:
                lock = self._session_locks[key] = threading.Lock()
            return lock

    def _hear(self, key: str) -> None:
        """서버가 그 세션(또는 레거시 이름)의 인증된 요청을 들었다 — 세션별 잠금 안에서 올린다."""
        now = self.mono_fn()  # type: ignore[attr-defined]
        missed = 2 * self.config.server.worker_heartbeat_seconds
        with self._session_lock(key):
            last = self.heard_at(key)
            with self._heard_lock:
                gap = now - last
                if key not in self._heard:  # mutcheck: restart-not-quiet
                    # 기동 뒤 처음 듣는다 — 서버가 꺼져 있던 동안도 그 세션이 조용했던 시간이다.
                    # 얼마나 조용했는지 모른다(바닥이 기동 시각이다) — 창은 늘 새로 잰다
                    gap = math.inf
                if gap > missed:  # mutcheck: t4-quiet-time-counted
                    records = self._resumed.setdefault(key, [])
                    records.append((self.now_fn(), gap))
                    del records[:-RESUMED_KEEP]
                self._heard[key] = max(self._heard.get(key, now), now)

    def heard_at(self, key: str) -> float:
        """`max(heard(S), 기동)` — 유예의 시작점(결정 266). 들은 적이 없으면 기동 단조 시각."""
        with self._heard_lock:
            heard = self._heard.get(key)
        floor = self.started_mono  # type: ignore[attr-defined]
        return floor if heard is None else max(heard, floor)

    @staticmethod
    def _session_arg(raw: Any, where: str = "worker_session_id") -> str | None:
        """세션 id 의 모양(32자 소문자 hex). 없으면 None — 레거시(옛 워커)다. 모양이 틀리면 400."""
        if raw is None:
            return None
        if not isinstance(raw, str) or not _SESSION_RE.fullmatch(raw):  # mutcheck: session-re-tail
            raise _api_error(400, f"{where} must be 32 lowercase hex characters")
        return raw

    @staticmethod
    def _session_list(body: dict[str, Any], key: str) -> list[str]:
        raw = body.get(key, [])
        if not isinstance(raw, list) or len(raw) > 64:
            raise _api_error(400, f"{key} must be a list of session ids")
        out: list[str] = []
        for x in raw:
            if not isinstance(x, str) or not _SESSION_RE.fullmatch(x):  # mutcheck: list-tail
                raise _api_error(400, f"{key} must be a list of session ids")
            out.append(x)
        return out

    @staticmethod
    def _protocol_required() -> Exception:
        return _api_error(
            409, PROTOCOL_REQUIRED, code="protocol_required", worker_protocol=WORKER_PROTOCOL
        )

    def _current_session(self, token: TokenInfo, session_id: str) -> None:
        """세션 판정(§4.2 · T8 · T10 · T19). 현재 세션이면 `heard` 를 올린다.

        표에 없으면 409 `not_registered`(워커는 같은 id 로 재등록한다), 다른 이름의 세션이면 403
        (`heard` 를 남의 이름으로 올려 표식 잡이 닫히지 않게 하는 길을 막는다), 펜싱됐으면 409
        `session_fenced`. 세션 행은 잠금 밖에서 읽는다 — WAL 이라 읽기는 쓰기를 기다리지 않는다."""
        row = self.store.get_session(session_id)
        if row is None:
            raise _api_error(409, f"worker {token.name} is not registered", code="not_registered")
        if row.worker_name != token.name:  # mutcheck: session-name-unchecked
            raise _api_error(403, SESSION_FOREIGN)
        if row.fenced_at is not None:
            raise _api_error(409, SESSION_FENCED, code="session_fenced")
        self._hear(session_id)

    def _legacy_gate(self, token: TokenInfo) -> None:
        """세션 없는 요청(옛 워커, 결정 262). 그 이름에 레거시 busy 잡이 있을 때만 받는다."""
        if not self.store.has_legacy_busy(token.name):
            raise self._protocol_required()
        self._hear(f"legacy:{token.name}")

    def _gate(self, token: TokenInfo, session_id: str | None) -> None:
        if session_id is None:
            self._legacy_gate(token)
        else:
            self._current_session(token, session_id)

    def _forget_session_memory(self, session_ids: list[str]) -> None:
        """펜싱된 세션의 받아들인 seq 는 더 쓰지 않는다(§4.2). `heard` 는 그 세션의 busy 잡이 닫힐
        때까지 남긴다 — janitor 가 `heard + G` 로 그 잡을 닫아야 한다."""
        with self._heard_lock:
            for sid in session_ids:
                self._hb_seq.pop(sid, None)
                self._resumed.pop(sid, None)

    # ── 인증 ────────────────────────────────────────────────────────────────

    def require_worker_token(self, token: TokenInfo | None) -> TokenInfo:
        if token is None:
            raise _api_error(401, "a valid worker token is required")
        if token.kind != TOKEN_WORKER:
            raise _api_error(403, "worker token required")
        return token

    # ── 워커 상태 (DB 로 계산) ───────────────────────────────────────────────

    def worker_alive(self, row: WorkerRow, now: datetime) -> bool:
        timeout = self.config.server.worker_timeout_seconds
        return (now - row.last_seen_at).total_seconds() <= timeout

    def _workers(self) -> list[WorkerRow]:
        try:
            return self.store.list_workers()
        except Exception:  # noqa: BLE001 — 표가 없거나 DB 오류면 원격 워커는 없는 셈
            return []

    def remote_worker_infos(self, pool: str | None, now: datetime) -> list[WorkerInfo]:
        """그 풀(None 이면 전부)의 원격 레인. busy 는 DB 의 running·cancelling 잡, 나머지 idle,
        heartbeat 이 오래됐으면 전부 down. 워커 이름순 · 레인순."""
        infos: list[WorkerInfo] = []
        rows = [r for r in self._workers() if pool is None or r.pool == pool]
        # 워커마다 묻지 않고 **한 문장**으로 읽는다.
        # 실패를 삼키지 않는다: 빈 map 으로 물러서면 도는 레인이 `idle` 로, `since` 까지 등록
        # 시각으로 바뀌어 「그 레인은 등록 이후 계속 놀았다」는 **없는 사실**을 지어낸다.
        # 모르는 것은 모르는 대로 올린다(fail-open 금지).
        lanes_busy = self.store.active_worker_lanes() if rows else {}
        for row in rows:
            alive = self.worker_alive(row, now)
            host = self.host_of(row.name)  # type: ignore[attr-defined]
            for lane in range(1, row.lanes + 1):
                job = lanes_busy.get((row.name, lane)) if alive else None
                if not alive:
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_DOWN,
                            error="no heartbeat",
                            since=row.last_seen_at,
                            worker=row.name,
                            pool=row.pool,
                            host_key=host,
                        )
                    )
                elif job is not None:
                    job_id, started_at = job
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_BUSY,
                            job_id=job_id,
                            since=started_at,
                            worker=row.name,
                            pool=row.pool,
                            host_key=host,
                        )
                    )
                else:
                    # 상태 경로는 **다시 판정하지 않는다** — claim 경로가 남긴 것을 읽는다.
                    # 두 번 부르면 화면과 실제가 어긋난다(§4.5).
                    hold, held_since = self.hold_of(lane, row.name, now)
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_HELD if hold else WORKER_IDLE,
                            since=held_since or row.registered_at,
                            worker=row.name,
                            pool=row.pool,
                            host_key=host,
                            hold_code=hold.code if hold else None,
                            hold_detail=dict(hold.detail) if hold and hold.detail else None,
                            held_since=held_since,
                        )
                    )
        return infos

    def remote_lanes(self, pool: str, now: datetime) -> int:
        """살아 있는 워커의 레인 합."""
        return sum(r.lanes for r in self._workers() if r.pool == pool and self.worker_alive(r, now))

    def remote_hosts(self, pool: str, now: datetime) -> tuple[HostSample, ...]:
        """살아 있는 워커의 마지막 heartbeat 표본(이름순). 없으면 `()` — 「표본 없음」이지 실패가
        아니다."""
        out: list[HostSample] = []
        with self._remote_lock:
            samples = dict(self._worker_samples)
        for row in self._workers():
            if row.pool != pool or not self.worker_alive(row, now):
                continue
            sample = samples.get(row.name)
            if sample is not None:
                out.append(sample)
        return tuple(out)

    def pools_without_workers(self, now: datetime) -> list[str]:
        """등록된 워커가 있는데 전부 down 인 풀. 기본 풀은 로컬 레인이 있을 때만 뺀다 — 그때는
        원격 워커가 다 죽어도 로컬 레인이 받는다. 분배 전용(`lanes = 0`)이면 기본 풀도 원격
        워커뿐이라 다른 풀처럼 센다(M5t-a §3 C12, TL13)."""
        local_lanes = self._has_local_lanes()
        seen: dict[str, bool] = {}
        for row in self._workers():
            if row.pool == DEFAULT_POOL and local_lanes:
                continue
            seen[row.pool] = seen.get(row.pool, False) or self.worker_alive(row, now)
        return sorted(name for name, alive in seen.items() if not alive)

    # ── 등록 ────────────────────────────────────────────────────────────────

    def worker_register(self, token: TokenInfo, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        pool = body.get("pool", DEFAULT_POOL)
        if not isinstance(pool, str) or not _NAME_RE.match(pool):
            raise _api_error(400, "pool must be a name (letters, digits, . _ -)")
        lanes = _int_field(body, "lanes", 1, MAX_WORKER_LANES, default=1)
        host_name = body.get("host_name")
        if host_name is not None and not isinstance(host_name, str):
            raise _api_error(400, "host_name must be a string")
        version = body.get("version")
        if version is not None and not isinstance(version, str):
            raise _api_error(400, "version must be a string")
        session = self._session_arg(body.get("worker_session_id"))
        previous = self._session_list(body, "previous_sessions")
        unverified = self._session_list(body, "unverified_sessions")
        # mutcheck: register-session-before-version
        if version != self.version:
            # 이 검사는 아래의 lost 처리보다 먼저다 — 따라가기 전의 409 가 도는 잡을 닫으면 안 된다.
            # `server_version` 은 M5q 의 워커가 따라갈 버전이다(키 추가뿐 — 옛 워커는 무시한다)
            raise _api_error(
                409,
                f"worker version {version or '?'}, server {self.version} — "
                "install the same release",
                server_version=self.version,
            )
        if session is None:
            # 버전 검사 **뒤**다(RL13). 옛 워커(프로토콜 1)는 새 서버에 등록하지 못한다(결정 262)
            raise self._protocol_required()
        # ── M5t-c 의 `pair_proof` 검사 자리(M5t-c 결정 287 · F52): 버전 · 프로토콜 검사 뒤, 아래의
        # 어떤 부작용(세션 넣기 · 펜싱 · 능력 지우기 · 호스트 사실 교체 · 잡 닫기)보다도 앞 ──
        now = self.now_fn()
        host_name = host_name[:200] if host_name else None
        existing = self.store.get_session(session)
        if existing is not None:
            if existing.worker_name != token.name:  # mutcheck: session-name-unchecked
                raise _api_error(403, SESSION_FOREIGN)
            if existing.fenced_at is not None:
                # 펜싱된 프로세스가 재등록해 새 세션을 거꾸로 펜싱하는 핑퐁을 막는다(§4.2 의 5)
                raise _api_error(409, SESSION_FENCED, code="session_fenced")
            # 같은 세션의 재등록(레인이 `not_registered` 409 를 받음, T10)은 멱등이다 — 잡을 닫지
            # 않고 능력 보고도 지우지 않는다. 같은 프로세스라 호스트 사실도 같다
            row = self.store.register_worker(  # mutcheck: reregister-closes-own-jobs
                token.name, pool=pool, lanes=lanes, host_name=host_name, version=version, now=now
            )
            self._hear(session)
            self.remember_host(token.name, body.get("host"), replace=True)  # type: ignore[attr-defined]
            self._mark_dirty()
            self._publish_server()
            return self._register_answer(row)
        names = self.store.session_names([*previous, *unverified])
        if any(n != token.name for n in names.values()):
            # 남의 이름에 걸린 잡과 홀드를 풀 수 없게(T19 · V1-8)
            raise _api_error(403, SESSION_FOREIGN)
        try:
            opened = self.store.open_session(
                token.name,
                session,
                previous=previous,
                unverified=unverified,
                now=now,
                pool=pool,
                lanes=lanes,
                host_name=host_name,
                version=version,
            )
        except sqlite3.IntegrityError:
            # 같은 새 세션 id 의 등록이 겹쳐 다른 요청이 먼저 세션 행을 넣었다(같은 프로세스의
            # 레인 둘). 이 요청은 그 세션의 재등록이다 — 위의 멱등 길로 다시 답한다
            if self.store.get_session(session) is None:  # mutcheck: register-race-500
                raise
            return self.worker_register(token, body)
        replaced = self._live_replaced(opened.fenced, {*previous, *unverified})
        self._forget_session_memory(opened.fenced)
        self._hear(session)
        if replaced:
            self.log(f"worker {token.name} session {session[:8]}: replaced {replaced} live")
        for job_id in opened.closed:
            self._publish_job(None, job_id)
        if opened.closed:
            self._drop_log_partial(opened.closed)
            self.log(
                f"worker {token.name} registered session {session[:8]}: "
                f"lost={opened.closed}" + (f" legacy={opened.legacy}" if opened.legacy else "")
            )
        if opened.released:
            self.log(f"worker {token.name}: hold released by the new session: {opened.released}")
            for job_id in opened.released:
                self._publish_job(None, job_id)
        for job_id in opened.legacy:
            if self.store.has_open_hold(job_id):
                self._hold_created(job_id)
        row = self.store.get_worker(token.name)
        assert row is not None
        # 새 세션 = 새 프로세스. 옛 프로세스의 능력 보고는 이 프로세스의 것이 아니다 — 새로 잴
        # 때까지 이 워커는 「보고 없음」이고 any-capable 잡을 안 받는다(M5n 결정 91, fail-closed)
        self.forget_report(token.name)  # type: ignore[attr-defined]
        # 호스트 사실(M5t-a §4.5) — 메모리에만 둔다. 안 보낸 옛 워커면 「모름」으로 바꾼다
        self.remember_host(token.name, body.get("host"), replace=True)  # type: ignore[attr-defined]
        self._mark_dirty()
        self._publish_server()
        if opened.closed or opened.released:
            self.wake.set()
        return self._register_answer(row, fenced=replaced)

    def _live_replaced(self, fenced: list[str], listed: set[str]) -> int | None:
        """이 등록이 펜싱한 다른 세션 가운데 살아 있던 것으로 보이는 수(P6 GAP8, 명세 §9). 워커가
        죽었다고 넘긴 세션은 빼고, 서버가 `worker_timeout_seconds` 안에 들은 세션만 센다. 펜싱된
        프로세스는 종료 0 이라 서비스면 다시 뜨지 않는다 — 새 프로세스가 그것을 로그로 말한다.

        들은 시각은 메모리에만 있다(결정 266). 기동 뒤 `worker_timeout_seconds` 가 지나기 전에는
        안 들린 세션이 조용했는지 서버가 꺼져 있어 못 들었는지 모른다 — 그런 세션이 하나라도
        있으면 None(모름)이다(P6-2 #2)."""
        now = self.mono_fn()  # type: ignore[attr-defined]
        timeout = self.config.server.worker_timeout_seconds
        with self._heard_lock:
            others = [
                sid
                for sid in fenced
                if sid not in listed  # mutcheck: replaced-counts-listed
            ]
            heard = {sid: self._heard[sid] for sid in others if sid in self._heard}
        recent = now - self.started_mono <= timeout  # type: ignore[attr-defined]
        if recent and len(heard) < len(others):  # mutcheck: replaced-unknown-as-zero
            return None
        return sum(1 for at in heard.values() if now - at <= timeout)

    def _register_answer(self, row: WorkerRow, *, fenced: int | None = 0) -> dict[str, Any]:
        s = self.config.server
        return {
            # 이 등록이 대신한 살아 있던 프로세스 수(P6 GAP8) — 같은 세션의 재등록은 0. 재시작한
            # 서버가 아직 모르면 null(P6-2 #2)
            "fenced_sessions": fenced,
            "generation": self.generation,  # type: ignore[attr-defined]
            "name": row.name,
            "pool": row.pool,
            "lanes": row.lanes,
            "heartbeat_seconds": s.worker_heartbeat_seconds,
            "worker_timeout_seconds": s.worker_timeout_seconds,
            "claim_wait_seconds": s.worker_claim_wait_seconds,
            "worker_protocol": WORKER_PROTOCOL,
        }

    def _hold_created(self, job_id: int) -> None:
        """홀드를 만든 커밋 뒤 · 잠금 밖에서 — 서버 로그 한 줄과 버스 사건(§4.12).

        알림 오류는 삼키고 로그에 남긴다 — 뒤에 올 다른 홀드의 알림 · 발행 · 깨우기를 막지 않게
        (로컬 레인의 `Worker._hold_created` 와 같다, P3 3라운드 LOW). 홀드 자체는 이미 커밋됐다."""
        try:
            self.log(f"job #{job_id}: irreversible group held until rcm release-hold {job_id}")
            on_hold = getattr(self, "_on_hold_created", None)
            if on_hold is not None:
                on_hold(job_id)
        except Exception as e:  # noqa: BLE001  # mutcheck: hold-announce-error-escapes
            try:
                self.log(f"job #{job_id}: hold announcement failed: {type(e).__name__}")
            except Exception:  # noqa: BLE001 — 로그도 못 쓰면 더 할 것이 없다
                pass

    def _registered(self, token: TokenInfo) -> WorkerRow:
        row = self.store.get_worker(token.name)
        if row is None:
            raise _api_error(409, f"worker {token.name} is not registered", code="not_registered")
        return row

    # ── claim ───────────────────────────────────────────────────────────────

    def _claim_payload(self, job: Job) -> dict[str, Any]:
        preset = self.config.preset(job.preset)
        preset_doc: dict[str, Any] | None = None
        if preset is not None:
            preset_doc = {
                "name": preset.name,
                "argv": list(preset.argv),
                "timeout_seconds": preset.timeout_seconds,
                "env": dict(preset.env),
                "env_passthrough": list(preset.env_passthrough),
                # 워커가 자기 최종 환경에서 같은 규칙으로 검사한다(M5j G4). 옛 워커는 무시한다.
                "requires": list(preset.requires),
                "source_modes": list(preset.source_modes),
                "repo": preset.repo or None,
            }
        # ⚠️ **얼린 산출물 정책을 반드시 실어야 한다.** 안 실으면 워커의 `_policy_from_claim` 이
        # None 을 돌려주고 **원격 풀에서는 산출물이 하나도 안 모인다** — 잡은 성공하고 화면도
        # 아무 말을 안 한다. 이 구멍이 M5e 내내 열려 있었다(M5g §13 D). 정책은 업로드 검증에
        # 쓰는 것과 **같은 값**이어야 한다(`job_artifact_policy`).
        policy = self.job_artifact_policy(job)
        artifacts_doc = (
            {
                "globs": list(policy.globs),
                "max_bytes": policy.max_bytes,
                "max_files": policy.max_files,
                "timeout_seconds": policy.timeout_seconds,
                "cancel_timeout_seconds": policy.cancel_timeout_seconds,
                "collect_on": policy.collect_on,
            }
            if policy.enabled()
            else None
        )
        return {
            "artifacts": artifacts_doc,
            "job": {
                "id": job.id,
                "preset": job.preset,
                "pool": job.pool,
                "priority": job.priority,
                "state": job.state,
                "lane": job.lane,
                "source": source_json(job.source),
                "inputs": dict(job.inputs),
                "requester": {"name": job.requester.name, "label": job.requester.label},
                "requested_by": job.requester.label,
                "concurrency_group": job.concurrency_group,
                "group": job.concurrency_group,
                "timeout_seconds": job.timeout_seconds,
                "started_at": iso(job.started_at),
                # claim 때 얼린 값(결정 257) — 워커는 이 값만 쓴다. 최상위 키는 늘리지 않는다(F58)
                "irreversible": bool(job.irreversible),
                "disconnect_grace_seconds": job.disconnect_grace_seconds,
            },
            "tree_url": f"/worker/jobs/{job.id}/tree",
            "preset": preset_doc,
        }

    def worker_claim(self, token: TokenInfo, body: Any) -> dict[str, Any] | None:
        """잡이 있으면 payload, 없으면 None(204). 기다림은 `claim_wait_seconds` 상한."""
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        session = self._session_arg(body.get("worker_session_id"))
        if session is None:
            raise self._protocol_required()  # 옛 워커의 새 claim 은 받지 않는다(결정 262)
        claim_seq = _int_field(body, "claim_seq", 1, MAX_SEQ)
        self._current_session(token, session)
        row = self._registered(token)
        lane = _int_field(body, "lane", 1, row.lanes, default=1)
        s = self.config.server
        wait = _int_field(body, "wait_seconds", 0, 60, default=s.worker_claim_wait_seconds)
        wait = min(wait, s.worker_claim_wait_seconds)
        now = self.now_fn()
        if not self.store.touch_worker(token.name, now):
            # 이제 워커 행이 사라질 수 있다(은퇴 정리) — 그 뒤에 claim 하면 아무도 못 거두는
            # 잡이 된다. 다시 등록하라고 말한다.
            raise _api_error(409, "worker is not registered", code="not_registered")
        who = (session, claim_seq)
        job, hold = self._try_claim(token.name, row.pool, lane, now, who)
        if job is not None:
            return self._claim_payload(job)
        # 보류된 레인은 **슬롯을 안 잡는다**. 8개뿐인 long-poll 슬롯을 보류 레인이 먹으면
        # 정작 열린 레인이 즉시 204 를 받고 1초 폴링으로 떨어진다(실측: 0.003초 → 0.394초).
        if hold is not None:
            return None
        if wait <= 0 or not self._claim_slots.acquire(blocking=False):
            return None
        try:
            deadline = time.monotonic() + wait  # 주입 시계(now_fn)가 아니라 실제 경과 시간
            while not self.stop.is_set():
                woke = self.wake.wait(CLAIM_POLL_SECONDS)
                job, _hold = self._try_claim(token.name, row.pool, lane, self.now_fn(), who)
                if job is not None:
                    return self._claim_payload(job)
                if time.monotonic() >= deadline:
                    break
                if woke:
                    # `wake` 는 로컬 워커 스레드가 지우는 이벤트라 로컬 레인이 바쁘면 세워진 채로
                    # 남는다 — 그대로 돌면 매 반복 DB 를 두드리므로 한 주기 쉬고 다시 본다
                    self.stop.wait(CLAIM_POLL_SECONDS)
        finally:
            self._claim_slots.release()
        return None

    def _try_claim(self, name: str, pool: str, lane: int, now: datetime, who: tuple[str, int]):
        """(잡, 보류) — 둘 다 None 이면 큐가 빈 것이다. `who` 는 (세션, claim_seq)."""
        session, claim_seq = who
        try:
            if self.store.get_paused() is not None:
                return None, None
            claimant = self.claimant_for(name, pool, now)  # type: ignore[attr-defined]

            def claim():
                return self.store.claim(
                    lane,
                    now,
                    pool=pool,
                    worker_name=name,
                    claimant=claimant,
                    now_fn=self.now_fn,  # 시작 마감은 트랜잭션 안의 시각으로(M5s)
                    session_id=session,
                    claim_seq=claim_seq,
                    grace_seconds=disconnect_grace(self.config.server),
                )

            job, hold = self.admit(lane, name, now, claim)
        except LaneBusy as e:
            # 펜싱된 세션(또는 레거시)의 잡이 그 레인을 잡고 있다 — 확인이나 유예 만료까지 기다릴
            # 일이라 409 를 쏟지 않고 「잡 없음」으로 답한다(결정 261 · F10). 자기 세션의 잡이면
            # 오늘처럼 409 다
            taken = [j for j in self.store.jobs_of_worker(name) if j.lane == lane]
            if taken and all(j.worker_session_id != session for j in taken):
                return None, None
            raise _api_error(409, str(e)) from e
        except SessionFenced as e:
            # 긴 대기 중에 같은 이름의 새 프로세스가 등록했다 — 시작 때의 세션 판정과 같은 답
            raise _api_error(409, SESSION_FENCED, code="session_fenced") from e
        except sqlite3.OperationalError as e:
            # 잠금·바쁨만 일시 오류다. `no such table` 같은 영구 결함을 「다시 해 보라」고 하면
            # 워커가 영원히 재시도한다. 워커는 503 을 이미 일시 오류로 처리한다.
            if not _is_busy_error(e):
                raise
            raise _api_error(
                503,
                "database is busy",
                headers={"Retry-After": "1"},
                code="database_busy",
                retry_after=1,
            ) from e
        if job is not None:
            self._publish_job(job, job.id)
            self._publish_server()
        return job, hold

    # ── 잡 보고 ─────────────────────────────────────────────────────────────

    def _drop_log_partial(self, job_ids: list[int]) -> None:
        """종료된 잡의 잘린 마지막 줄 조각을 버린다 — 늦은 보고는 409 라 다시 쓰일 일이 없고, 두면
        lost 로 닫힌 잡마다 조각이 남는다."""
        with self._remote_lock:
            for job_id in job_ids:
                self._log_partial.pop(job_id, None)
                self._job_log_locks.pop(job_id, None)

    def _job_log_lock(self, job_id: int) -> threading.Lock:
        with self._remote_lock:
            lock = self._job_log_locks.get(job_id)
            if lock is None:
                lock = self._job_log_locks[job_id] = threading.Lock()
            return lock

    def _before_log_commit(self, job_id: int) -> None:
        """시험 훅 — 로그 조각을 파일에 쓴 뒤 · 커밋 전(§7.5 「쓰기와 커밋 사이 멈춤」)."""

    def check_logs_on_start(self) -> None:
        """기동 정리(§4.5 · 결정 338). busy 원격 잡의 `log.txt` 가 커밋된 길이보다 길면 그 길이로
        자른다 — 쓰고 커밋하기 전에 서버가 죽어 남은 조각이다. 짧으면 `log_complete = 0` 을 고정하고
        한 줄. 끝난 원격 잡(완전하다고 적힘)은 짧을 때만 0 으로 고친다 — 파일이 없으면(gc) 그대로,
        길면 자르지 않는다(끝난 잡의 파일에는 커밋 뒤 쓰기가 없다)."""
        for row in self.store.remote_log_rows():
            job_id, committed = int(row["id"]), int(row["log_file_bytes"])
            busy = row["state"] in BUSY_STATES
            if not busy and row["log_complete"] != 1:  # mutcheck: finished-log-not-checked
                continue
            path = self.log_path(job_id)
            try:
                size = path.stat().st_size
            except OSError:
                if busy and committed > 0:
                    self.store.pin_log_incomplete(job_id)
                    self.log(f"log gap: job #{job_id} log.txt is missing, committed {committed}")
                continue
            if size < committed:
                self.store.pin_log_incomplete(job_id)
                self.log(f"log gap: job #{job_id} log.txt is {size} bytes, committed {committed}")
            elif size > committed and busy:
                try:
                    with path.open("r+b") as fh:
                        fh.truncate(committed)
                except OSError as e:
                    self.log(f"job #{job_id} log.txt: cannot trim ({type(e).__name__})")

    def _session_job(self, token: TokenInfo, job_id: int, session: str | None) -> Job:
        """세션 판정 뒤 이 세션이 claim 한 잡(끝났어도). 남의 잡 · 다른 세션의 잡은 403."""
        self._gate(token, session)
        job = self.store.get_job(job_id)
        if job is None:
            raise _api_error(404, "no such job")
        if job.worker_name != token.name or job.worker_session_id != session:
            raise _api_error(403, "not your job")
        return job

    def _owned_active(self, token: TokenInfo, job_id: int, session: str | None) -> Job:
        """이 세션이 claim 한 활성 잡. 남의 잡 403 · 종료 잡 409 `job_closed`(늦은 보고는 무시)."""
        job = self._session_job(token, job_id, session)
        if job.state not in BUSY_STATES:
            raise _api_error(
                409, f"job #{job.id} is {job.state}", state=job.state, code="job_closed"
            )
        return job

    def worker_tree_path(self, token: TokenInfo, job_id: int, session: str | None) -> Path:
        job = self._owned_active(token, job_id, session)
        if job.source.mode == MODE_GIT_REF:
            raise _api_error(404, "git_ref jobs are fetched by the worker")
        job_dir = self.job_dir(job.id)
        tar_path = job_dir / "tree.tar.gz"
        if tar_path.is_file():
            return tar_path
        manifest = job_dir / "manifest.json"
        if manifest.is_file():
            try:
                assemble_tar_from_manifest(manifest, self.config.data_dir / "blobs", tar_path)
            except MaterializeError as e:
                raise _api_error(409, str(e)) from e
            return tar_path
        raise _api_error(404, "snapshot file is missing")

    def worker_phase(self, token: TokenInfo, job_id: int, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        session = self._session_arg(body.get("worker_session_id"))
        job = self._owned_active(token, job_id, session)
        phase = body.get("phase")
        if phase not in WORKER_PHASES:
            raise _api_error(400, f"phase must be one of {', '.join(WORKER_PHASES)}")
        now = self.now_fn()
        self.store.set_phase(job.id, phase)
        if phase == PHASE_EXECUTING:
            self.store.set_last_output(job.id, now)
        self._publish_job(None, job.id)
        return {"job_id": job.id, "phase": phase}

    def worker_log(
        self,
        token: TokenInfo,
        job_id: int,
        data: bytes,
        session: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """로그 조각 하나(§4.5 의 서버 로그 쓰기 절차 1~9, 결정 323 · 338). 세션이 없으면 오늘의
        덧붙이는 레거시 길이다(T11)."""
        if session is None:
            return self._legacy_log(token, job_id, data)
        if offset is None:
            raise _api_error(400, f"{LOG_OFFSET_HEADER} is required with {SESSION_HEADER}")
        job = self._owned_active(token, job_id, session)  # step 2
        with self._job_log_lock(job.id):  # step 3 · mutcheck: log-write-unlocked
            answer, parsed = self._write_log(job, session, offset, data)
        # step 9 — 커밋 뒤 잠금을 놓고 마커를 발행한다
        for kind, value in parsed:
            self._on_marker(job.id, kind, value)
        if answer["bytes"]:
            self._mark_dirty()
        return answer

    def _write_log(
        self, job: Job, session: str, offset: int, data: bytes
    ) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        """잡별 로그 잠금 안의 step 4~8. 실패하면 파일을 커밋된 길이로 되자른다."""
        row = self.store.log_row(job.id)  # step 4
        if row is None:
            raise _api_error(404, "no such job")
        if row["state"] not in BUSY_STATES:
            raise _api_error(
                409, f"job #{job.id} is {row['state']}", state=row["state"], code="job_closed"
            )
        committed = int(row["log_offset"] or 0)
        file_bytes = int(row["log_file_bytes"] or 0)
        if offset > committed:  # step 5 · mutcheck: log-offset-ignored
            raise _api_error(
                409,
                f"log gap: the server has {committed} bytes of job #{job.id}",
                code="log_gap",
                expected_offset=committed,
            )
        rest = data[committed - offset :]
        if not rest:
            return {"job_id": job.id, "bytes": 0, "markers": 0, "offset": committed}, []
        # step 6 — 원격 워커의 stdout 도 서버의 릴리스 비밀 규칙으로 가린다(F14)
        masked = mask_bytes(rest, self.mask_for_preset(job.preset))
        path = self.log_path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = path.open("r+b")
        except FileNotFoundError:
            fh = path.open("w+b")
        with fh:
            size = fh.seek(0, 2)
            pos = file_bytes
            if size < file_bytes:
                # OS 충돌로 fsync 되지 않은 꼬리를 잃었다(T16) — 빈 바이트 구멍을 만들지 않는다
                self.store.pin_log_incomplete(job.id)
                self.log(f"log gap: job #{job.id} log.txt is {size} bytes, committed {file_bytes}")
                pos = size
            try:
                _write_at(fh, pos, masked)  # step 7
            except OSError as e:
                _truncate_quietly(fh, pos)
                raise _api_error(
                    503, f"cannot write the log ({type(e).__name__})", code="log_write_failed"
                ) from e
            # 마커는 끝난 줄에서만 찾는다. 앞 조각이 줄 끝 없이 끝났으면(러너가 64 KiB 마다 끊은
            # 줄 · 옛 시험) 파일의 그 꼬리를 앞에 붙인다 — 메모리 조각(`_log_partial`)이 아니라
            # 커밋된 파일에서 읽으므로 재시작을 넘어도 같다
            lines = (_open_line(fh, pos) + masked).split(b"\n")[:-1]
            parsed = [
                p for raw in lines if (p := parse_marker(raw.decode("utf-8", errors="replace")))
            ]
            self._before_log_commit(job.id)
            try:  # step 8 — 「세션이 아직 현재」도 같은 UPDATE 가 본다(결정 338)
                ok = self.store.commit_log(
                    job.id,
                    session=session,
                    expect_offset=committed,
                    new_offset=committed + len(rest),
                    file_bytes=pos + len(masked),
                    markers=parsed,
                    at=self.now_fn(),
                )
            except sqlite3.OperationalError as e:
                _truncate_quietly(fh, pos)
                if not _is_busy_error(e):
                    raise
                raise _api_error(
                    503, "database is busy", code="database_busy", retry_after=1
                ) from e
            except BaseException:
                _truncate_quietly(fh, pos)
                raise
            if not ok:
                _truncate_quietly(fh, pos)
                raise self._log_refused(job.id, session)
        answer = {
            "job_id": job.id,
            "bytes": len(rest),
            "markers": len(parsed),
            "offset": committed + len(rest),
        }
        return answer, parsed

    def _log_refused(self, job_id: int, session: str) -> Exception:
        """커밋이 0행이었다 — 펜싱됐으면 `session_fenced`, 닫혔으면 `job_closed`."""
        srow = self.store.get_session(session)
        if srow is None or srow.fenced_at is not None:
            return _api_error(409, SESSION_FENCED, code="session_fenced")
        current = self.store.get_job(job_id)
        st = current.state if current is not None else "unknown"
        return _api_error(409, f"job #{job_id} is {st}", state=st, code="job_closed")

    def _legacy_log(self, token: TokenInfo, job_id: int, data: bytes) -> dict[str, Any]:
        """옛 워커(프로토콜 1)의 잡 — raw 바이트를 `log.txt` 에 붙이고 줄 단위로 마커를 파싱한다.
        잘린 마지막 줄은 다음 요청과 이어 붙인다. 오프셋은 없다(`log_offset` NULL = 모름)."""
        session = None
        job = self._owned_active(token, job_id, session)
        now = self.now_fn()
        # 릴리스 비밀은 서버에 있다 — 원격 워커의 stdout 도 같은 규칙으로 지운다. 요청 하나가
        # 한 배치라, 두 요청에 걸쳐 잘린 값은 못 지운다(러너가 줄 단위로 flush 하므로 드물다).
        data = mask_bytes(data, self.mask_for_preset(job.preset))
        path = self.log_path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as fh:
            fh.write(data)
        with self._remote_lock:
            buf = self._log_partial.pop(job.id, b"") + data
            *lines, rest = buf.split(b"\n")
            if rest:
                self._log_partial[job.id] = rest[-4096:]
        # 마커는 **한 트랜잭션**으로 쓰고, 발행은 커밋 뒤에 한다. 줄마다 트랜잭션을 열면 다른
        # 레인의 claim 이 밀린다(256 KB flush 하나에 0.03 ms → 275.9 ms).
        parsed_markers = [
            p for raw in lines if (p := parse_marker(raw.decode("utf-8", errors="replace")))
        ]
        self.store.add_markers(job.id, parsed_markers, now)
        for kind, value in parsed_markers:
            self._on_marker(job.id, kind, value)
        markers = len(parsed_markers)
        if data:
            self.store.set_last_output(job.id, now)
            self._mark_dirty()
        return {"job_id": job.id, "bytes": len(data), "markers": markers}

    # ── 잡 산출물 (M5e) ─────────────────────────────────────────────────────

    def worker_artifacts_status(
        self, token: TokenInfo, job_id: int, session: str | None = None
    ) -> dict[str, Any]:
        """업로드 처리 상태와 잡 상태. 내용은 주지 않는다(§6). **종료됐어도 답한다** — 모호한
        finish 를 푸는 것이 이 라우트다."""
        job = self._session_job(token, job_id, session)
        row = self.store.get_bundle(job.id)
        return {
            "job_id": job.id,
            "state": row["state"] if row else None,
            "bundle_sha256": row["bundle_sha256"] if row else None,
            "job_state": job.state,
        }

    def job_artifact_policy(self, job: Job) -> Any:
        """그 잡에 얼린 정책. 프리셋의 글롭 + 서버의 잡당 상한(§9)."""
        s = self.config.server
        preset = self.config.preset(job.preset)
        return art.ArtifactPolicy(
            globs=tuple(preset.artifacts) if preset is not None else (),
            max_bytes=s.max_artifact_bytes,
            max_files=s.max_artifact_files,
            timeout_seconds=s.artifact_timeout_seconds,
            cancel_timeout_seconds=s.artifact_cancel_timeout_seconds,
            collect_on=preset.artifacts_on if preset is not None else art.COLLECT_ALWAYS,
        )

    def worker_receive_artifacts(
        self, token: TokenInfo, job_id: int, stream: Any, length: int, session: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        """워커가 올린 묶음을 받는다. (상태 코드, 본문). 같은 해시의 재시도는 멱등이다(§6)."""
        # 종료된 잡은 409 — 죽은 잡을 되살리지 않는다
        job = self._owned_active(token, job_id, session)
        policy = self.job_artifact_policy(job)
        allowance = art.archive_allowance(policy.max_bytes, policy.max_files)
        if length > allowance:
            raise _api_error(413, f"artifact bundle larger than {allowance} bytes")
        now = self.now_fn()
        limit = self.config.server.artifact_storage_max_bytes
        if not self.store.reserve_bundle_bytes(job.id, length, limit, now):
            raise _api_error(503, "artifact storage is full")
        root = self.config.data_dir / "artifacts"
        staging = root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        part = staging / f"{job.id}.{secrets.token_hex(8)}.part"
        try:
            digest = _stream_to_file(stream, part, length)
            # 받는 동안 세션이 펜싱됐거나 잡이 닫혔을 수 있다 — 발행 전에 다시 본다(P6 GAP6)
            self._owned_active(token, job_id, session)  # mutcheck: artifact-stream-not-rechecked
            row = self.store.get_bundle(job.id)
            stored = row["bundle_sha256"] if row else None
            if stored and stored != digest:
                # 묶음은 불변이다 — 한 번 받은 내용은 바뀌지 않는다(§3)
                raise _api_error(409, "a different bundle is already stored for this job")
            if stored == digest:
                return 200, {"job_id": job.id, "bundle_sha256": digest, "bytes": length}
            dest = root / str(job.id)
            dest.mkdir(parents=True, exist_ok=True)
            files = _manifest_from_tar(part, policy.max_files) or []
            part.replace(dest / "bundle.tar")
        except _api_error_type():
            part.unlink(missing_ok=True)
            self.store.release_bundle_bytes(job.id)
            raise
        except OSError as e:
            part.unlink(missing_ok=True)
            self.store.release_bundle_bytes(job.id)
            raise _api_error(503, f"cannot store the bundle ({type(e).__name__})") from e
        result = CollectResult(
            state=art.UPLOADING,
            files=tuple(files),
            total_bytes=sum(f.size for f in files),
            bundle_bytes=length,
            bundle_sha256=digest,
        )
        self.store.publish_bundle(
            job.id,
            result,
            now=now,
            # 영수증만 남고 finish 가 안 오면 sweep 이 가져간다 — 만료 없는 행을 안 만든다(§9 ⑤)
            expires_at=art.expires_at(now, self.config.server.artifact_retention_hours),
        )
        self.store.set_bundle_state(job.id, art.UPLOADING)
        self.publish_artifacts(job.id, art.UPLOADING)
        return 201, {"job_id": job.id, "bundle_sha256": digest, "bytes": length}

    def worker_finish(self, token: TokenInfo, job_id: int, body: Any) -> dict[str, Any]:
        from remote_ci_monitor.worker import outcome_for

        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        session = self._session_arg(body.get("worker_session_id"))
        gone = _group_gone(body)
        if session is not None:
            srow = self.store.get_session(session)
            if srow is not None and srow.worker_name == token.name and srow.fenced_at is not None:
                return self._narrow_confirm(token, job_id, session, body, gone)
        job = self._session_job(token, job_id, session)
        if job.state not in BUSY_STATES:
            raise self._closed_answer(token, job, session, gone, body)
        reported = body.get("outcome")
        if reported not in WORKER_OUTCOMES:
            raise _api_error(400, f"outcome must be one of {', '.join(WORKER_OUTCOMES)}")
        rc = body.get("exit_code")
        if rc is not None and (isinstance(rc, bool) or not isinstance(rc, int)):
            raise _api_error(400, "exit_code must be an integer or null")
        given = body.get("summary")
        if given is not None and not isinstance(given, str):
            raise _api_error(400, "summary must be a string")
        lost_coded = _lost_summary(body, reported, token.name)
        preflight = None if lost_coded is not None else _preflight_summary(body, reported, rc)
        log_bytes = _log_bytes(body)
        now = self.now_fn()
        # 실행 종료 시각(선택). 수집·업로드가 끼어도 v1 의 `finished_at`·`job_seconds` 가
        # 밀리지 않게 워커가 실어 보낸다(§10). 이상하면 조용히 수신 시각으로 물러선다. 워커 시계가
        # 어긋났으면 `[started_at, 수신 시각]` 의 가까운 끝으로 자른다(M5t-b 결정 277)
        ended = _clamp(_parse_iso(body.get("finished_at")) or now, job.started_at, now)
        disposition = body.get("artifacts")
        if disposition is not None and not isinstance(disposition, dict):
            raise _api_error(400, "artifacts must be an object")
        markers = self.store.markers(job.id)
        lost_text, lost_code, lost_args = outcome.summary("worker_stopped_while_running")
        if lost_coded is not None:
            lost_text, lost_code, lost_args = lost_coded
        elif given:  # 워커가 자기 문장을 보냈으면 그대로 쓴다 — 코드는 붙이지 않는다
            lost_text, lost_code, lost_args = given[:200], None, {}
        # 표식 잡을 닫는 보고에 프로세스 그룹이 사라졌다는 확인이 없으면 같은 트랜잭션에서 홀드를
        # 만든다(결정 330). 결과는 그대로이고 확인만 없는 것이다 — 옛 워커 · 레거시 잡이 그렇다
        hold_cause = None if gone is True else "group_not_gone"

        def _fields(now_job: Job, reported: str, preflight: Any) -> tuple[str, dict[str, Any]]:
            """이 보고를 `store.finish` 인자로. 취소가 끼어들면 같은 함수를 다시 부른다."""
            oc = outcome_for(
                now_job,
                markers,
                started=now_job.started_at or ended,
                finished=ended,
                rc=rc,
                cancelled=reported == CANCELLED,
                timed_out=reported == TIMED_OUT,
                lost=reported == LOST,
                lost_summary=lost_text,
                lost_code=lost_code,
                lost_args=lost_args,
            )
            state, summary, failed_step = oc.state, oc.summary, oc.failed_step
            code, args = oc.code, oc.args
            if preflight is not None:
                # 프로세스가 뜨기 전의 실패 — 서버가 만든 코드이지 스크립트의 선언이 아니다.
                # 라벨도 대장 행도 없이 로컬 워커와 같은 모양으로 닫는다(M5h 불변식 · 결정 85).
                summary, code, args = preflight
                oc = replace(
                    oc, failed_step=None, last_step=None, fail_names=(), fail_truncated=False
                )
                failed_step = None
            elif state in (FAILED, SUCCEEDED) and not summary:
                if given:
                    summary, code, args = given[:200], None, {}
                elif state == FAILED:
                    summary, code, args = outcome.summary("worker_failed")
            return state, {
                "state": state,
                "exit_code": rc,
                "summary": summary,
                "summary_code": code,
                "summary_args": args,
                "failed_step": failed_step,
                "last_step": oc.last_step,
                "fail_names": oc.fail_names,
                "fail_truncated": oc.fail_truncated,
            }

        # 프로세스가 뜨기 전의 실패가 사용자의 취소를 덮어서는 안 된다 — 워커가 취소를 아직 못
        # 들었어도 잡이 `cancelling` 이면 `cancelled` 로 닫는다(M5l S4). 코드가 늘어도 같다:
        # 자재화 실패든 시작 실패든 프로세스는 뜨지 않았고 사용자는 취소했다(F2b).
        if preflight is not None and job.state == CANCELLING:
            preflight = None
            reported = CANCELLED
        self._drop_log_partial([job.id])
        if disposition is not None:
            self._record_disposition(job.id, disposition, now)
        state, fields = _fields(job, reported, preflight)
        # 위의 `job` 은 트랜잭션 **밖에서** 읽은 스냅샷이다. 그것만 보면 취소가 이 두 줄 사이에
        # 착륙했을 때 `cancelling` 을 `failed` 로 덮는다 — 사용자가 멈춘 잡이 실패로 남는다.
        # 로컬 레인은 `only_from=(RUNNING,)` 으로 이 틈을 닫았다(M5l S4); 원격도 같아야 한다.
        # 시작 전 실패일 때만 건다: 실행까지 간 잡의 보고는 `cancelling` 에서도 그대로 받는다.
        only_from = (RUNNING,) if preflight is not None else None
        # 반복(M5s 결정 225)의 `retried` 는 `store.finish` 가 스스로 `running` 에서만 받는다 — 그
        # 사이 취소가 들어왔으면 거절되고, 아래 대체 경로가 `cancelled` 로 닫는다(다음 시도 없음)
        if not self.store.finish(
            job.id,
            now=ended,
            only_from=only_from,
            hold_cause=hold_cause,
            log_bytes=log_bytes,
            **fields,
        ):
            current = self.store.get_job(job.id)
            st = current.state if current else "unknown"
            if current is not None and st not in BUSY_STATES:
                # 확인과 닫기 사이에 janitor 가 닫았다 — 늦은 보고와 같은 답(P3 1라운드 LOW)
                # mutcheck: race-closed
                raise self._closed_answer(token, current, session, gone, body)
            racing = preflight is not None or state == RETRIED
            if not racing or current is None or st != CANCELLING:
                raise _api_error(409, f"job #{job.id} is {st}", state=st)
            # 취소가 이겼다 — 프로세스는 없었으니 여느 취소처럼 요청자 이름으로 닫는다. 이
            # 대체 경로가 없으면 잡은 종료 상태에 못 가고 heartbeat 시한까지 `running` 이다.
            state, fields = _fields(current, CANCELLED, None)
            if not self.store.finish(
                job.id,
                now=ended,
                only_from=(CANCELLING,),
                hold_cause=hold_cause,
                log_bytes=log_bytes,
                **fields,
            ):
                current = self.store.get_job(job.id)
                st = current.state if current else "unknown"
                if current is not None and st not in BUSY_STATES:
                    # 재시도 직전에 janitor 가 닫았다 — 위와 같은 답 · mutcheck: race-closed-retry
                    raise self._closed_answer(token, current, session, gone, body)
                raise _api_error(409, f"job #{job.id} is {st}", state=st)
        if hold_cause is not None and self.store.has_open_hold(job.id):
            self._hold_created(job.id)
        self._publish_job(None, job.id)
        self._publish_server()
        self.wake.set()
        return {"job_id": job.id, "state": state}

    def _closed_answer(
        self,
        token: TokenInfo,
        job: Job,
        session: str | None,
        gone: bool | None,
        body: dict[str, Any] | None = None,
    ) -> Exception:
        """이미 닫힌 잡에 온 finish(T14)의 답 — 409 `code: job_closed`. 같은 세션의 `group_gone:
        true` 는 그 잡의 열린 홀드를 푼다 — 확인은 뒤에 와도 참이다(§4.12 표의 1). 잡이 `lost` 면
        늦은 결과를 한 번 남긴다(§4.6 · 결정 256 · 274) — 기록과 홀드 풀기는 한 트랜잭션이다. 앞에서
        닫혀 있었든 확인과 닫기 사이에 닫혔든 같은 답이다."""
        late = None
        if job.state == LOST and session is not None and body is not None:
            late = self._record_late(token, job, session, gone, body)
        if late is not None:
            recorded, released = late
            if released:
                self.log(f"job #{job.id}: hold released — the worker confirmed the group is gone")
                self._publish_job(None, job.id)
                self.wake.set()
            return _api_error(
                409,
                f"job #{job.id} is {job.state}",
                state=job.state,
                code="job_closed",
                late_result_recorded=recorded,
                hold_released=released,
            )
        released = (
            gone is True  # mutcheck: hold-released-without-group-gone
            and session is not None
            and self.store.release_hold(
                job.id, kind=RELEASE_WORKER, by=token.name, reason=None, now=self.now_fn()
            )
            is not None
        )
        if released:
            self.log(f"job #{job.id}: hold released — the worker confirmed the group is gone")
            self._publish_job(None, job.id)
            self.wake.set()
        return _api_error(
            409,
            f"job #{job.id} is {job.state}",
            state=job.state,
            code="job_closed",
            late_result_recorded=False,
            hold_released=released,
        )

    def _record_late(
        self, token: TokenInfo, job: Job, session: str, gone: bool | None, body: dict[str, Any]
    ) -> tuple[bool, bool] | None:
        """늦은 finish 의 본문을 검사하고 `late_result` 를 남긴다. 펜싱된 세션은 None(2차 Q-B)."""
        reported = body.get("outcome")
        if reported not in WORKER_OUTCOMES:
            raise _api_error(400, f"outcome must be one of {', '.join(WORKER_OUTCOMES)}")
        rc = body.get("exit_code")
        if rc is not None and (isinstance(rc, bool) or not isinstance(rc, int)):
            raise _api_error(400, "exit_code must be an integer or null")
        log_bytes = _log_bytes(body)
        now = self.now_fn()
        ended = _clamp(_parse_iso(body.get("finished_at")) or now, job.started_at, now)
        payload = {
            "outcome": reported,
            "exit_code": rc,
            "finished_at": iso(ended),
            "received_at": iso(now),
        }
        return self.store.record_late_result(
            job.id,
            session=session,
            payload=payload,
            log_bytes=log_bytes,
            gone=gone,
            by=token.name,
            now=now,
        )

    def _narrow_confirm(
        self, token: TokenInfo, job_id: int, session: str, body: dict[str, Any], gone: bool | None
    ) -> dict[str, Any]:
        """펜싱된 세션에 허용하는 단 하나의 요청(결정 261): `lost` + `worker_replaced` finish.

        잡이 busy 면 조건부로 닫는다. `group_gone` 이 참이 아니면 같은 트랜잭션에서 홀드다. 잡이
        이미 끝났고 열린 홀드가 있으면 `group_gone: true` 일 때만 푼다. 그 밖의 모든 요청은 409
        `session_fenced` 이고 아무것도 반영하지 않는다 — 늦은 finish 도 `late_result` 로 남기지
        않는다(2차 Q-B). `heard` 는 올리지 않는다."""
        fenced = _api_error(409, SESSION_FENCED, code="session_fenced")
        if body.get("outcome") != LOST or body.get("summary_code") != "worker_replaced":
            raise fenced
        job = self.store.get_job(job_id)
        if job is None or job.worker_name != token.name or job.worker_session_id != session:
            raise fenced
        now = self.now_fn()
        if job.state in BUSY_STATES:
            text, code, args = outcome.summary("worker_replaced", name=token.name)
            cause = None if gone is True else "group_not_gone"
            if self.store.finish(
                job.id,
                LOST,
                now=now,
                summary=text,
                summary_code=code,
                summary_args=args,
                only_from=BUSY_STATES,
                hold_cause=cause,
            ):
                self._drop_log_partial([job.id])
                if cause is not None and self.store.has_open_hold(job.id):
                    self._hold_created(job.id)
                self._publish_job(None, job.id)
                self._publish_server()
                self.wake.set()
                return {"job_id": job.id, "state": LOST}
            job = self.store.get_job(job_id) or job
        if gone is True and self.store.release_hold(
            job.id, kind=RELEASE_WORKER, by=token.name, reason=None, now=now
        ):
            self._publish_job(None, job.id)
            self.wake.set()
            return {"job_id": job.id, "state": job.state, "hold_released": True}
        raise _api_error(409, f"job #{job.id} is {job.state}", state=job.state, code="job_closed")

    def _record_disposition(self, job_id: int, reported: dict[str, Any], now: datetime) -> None:
        """워커가 보고한 산출물 처분을 쓴다(§5).

        필드가 통째로 없으면 이 함수를 부르지 않는다 — 그게 `unknown` 이고 `empty` 와 다르다.
        """
        state = reported.get("state")
        if state not in art.ARTIFACT_STATES:
            raise _api_error(400, "artifacts.state is not a known state")
        row = self.store.get_bundle(job_id)
        if state == art.READY:
            # `ready` 는 묶음이 서버에 실제로 있을 때만이다. 행 · 해시 · 파일을 모두 본다(M5t-b
            # 결정 275 · F63). 업로드 없이 온 `ready` 를 적으면 내려받기가 없는 파일을 가리킨다
            stored = (row or {}).get("bundle_sha256")
            on_disk = (self.config.data_dir / "artifacts" / str(job_id) / "bundle.tar").is_file()
            if not stored or not on_disk:  # mutcheck: artifact-ready-without-bundle
                self.store.set_bundle_failed(job_id, art.FAILED, "upload_failed", None, now)
                return
            if reported.get("bundle_sha256") != stored:
                self.store.set_bundle_failed(job_id, art.FAILED, "hash_mismatch", None, now)
                return
            # 무엇을 모았는지는 **워커가** 안다. 서버는 자기가 받은 바이트를 안다 — 올라온 행이
            # 있으면 그 해시·바이트를 쓰고, 없으면 워커 말을 그대로 적는다. 파일이 실제로 없으면
            # 내려받기가 503 을 내고 재시작 화해가 `unavailable` 로 고친다(§3 · §9).
            files = tuple(
                BundleFile(path=f["path"], size=f["size"], sha256=f["sha256"], mode=f["mode"])
                for f in ((row or {}).get("files") or [])
            )
            digest = (row or {}).get("bundle_sha256") or reported.get("bundle_sha256")
            self.store.publish_bundle(
                job_id,
                CollectResult(
                    state=art.READY,
                    files=files,
                    total_bytes=int(reported.get("total_bytes") or 0),
                    bundle_bytes=int((row or {}).get("bundle_bytes") or 0),
                    skipped_count=int(reported.get("skipped_count") or 0),
                    bundle_sha256=digest if isinstance(digest, str) else None,
                ),
                now=now,
                expires_at=art.expires_at(now, self.config.server.artifact_retention_hours),
            )
            if reported.get("file_count") is not None and not files:
                # 매니페스트는 못 봤지만 워커가 센 수는 있다 — 지어내지 말고 그대로 적는다
                self.store.set_bundle_counts(job_id, int(reported["file_count"]))
            return
        reason = reported.get("reason_code")
        if reason is not None and reason not in art.REASONS:
            raise _api_error(400, "artifacts.reason_code is not a known code")
        args = reported.get("reason_args")
        if args is not None and not isinstance(args, dict):
            raise _api_error(400, "artifacts.reason_args must be an object")
        if state == art.EMPTY:
            self.store.publish_bundle(
                job_id,
                CollectResult(
                    state=art.EMPTY,
                    total_bytes=int(reported.get("total_bytes") or 0),
                    skipped_count=int(reported.get("skipped_count") or 0),
                    reason_code=reason,
                    reason_args=args,
                ),
                now=now,
                expires_at=art.expires_at(now, self.config.server.artifact_retention_hours),
            )
            return
        self.store.set_bundle_failed(job_id, state, reason or "collect_failed", args, now)

    # ── heartbeat ───────────────────────────────────────────────────────────

    def worker_heartbeat(self, token: TokenInfo, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        session = self._session_arg(body.get("worker_session_id"))
        known = body.get("jobs")
        if known is not None and (
            not isinstance(known, list)
            or any(isinstance(x, bool) or not isinstance(x, int) for x in known)
        ):
            raise _api_error(400, "jobs must be a list of job ids")
        if session is None:
            self._legacy_gate(token)
            self._registered(token)
            now = self.now_fn()
            self.store.touch_worker(token.name, now)
            active = self._legacy_forgotten(token, known, now)
            closed: list[dict[str, Any]] = []
        else:
            seq = _int_field(body, "seq", 1, MAX_SEQ)
            settled = _int_field(body, "claims_settled", 0, MAX_SEQ)
            self._current_session(token, session)
            self._registered(token)
            now = self.now_fn()
            self.store.touch_worker(token.name, now)
            active = self._session_forgotten(token, session, known, seq, settled, now)
            # 그 세션의 이미 닫힌 잡(결정 321 · 322). 닫힌 상태는 되돌아오지 않으므로 늦게 온
            # heartbeat 의 목록으로 만들어도 맞다
            closed = self.store.closed_jobs_of(
                session, known or []
            )  # mutcheck: closed-jobs-omitted
        sample = body.get("host_sample")
        if sample is not None:
            try:
                parsed = sample_from_json(sample, name=token.name, source="worker", sampled_at=now)
            except ValueError:
                parsed = None  # 표본만 버린다 — heartbeat 자체는 유효하다
            if parsed is not None:
                with self._remote_lock:
                    self._worker_samples[token.name] = parsed
        cancel = [j.id for j in active if j.state == CANCELLING]
        cancel_held: list[int] = []
        try:
            self._close_unconfirmed_cancels(active, now, cancel_held)
        finally:
            for job_id in cancel_held:  # 중간에 터져도 커밋된 홀드는 알린다
                self._hold_created(job_id)
        self._mark_dirty()
        return {
            "cancel": cancel,
            "closed_jobs": closed,
            "paused": self.store.get_paused() is not None,
            "timeout_seconds": self.config.server.worker_timeout_seconds,
            # 세대가 바뀌었으면 워커는 `/worker/spec` 을 다시 받고 다시 잰다(M5n §4.2)
            "generation": self.generation,  # type: ignore[attr-defined]
            # 워커가 서버 버전이 바뀐 것을 보고 드레인한다(M5q ③ · PR B2). 옛 워커는 무시한다
            "server_version": self.version,
        }

    def _legacy_forgotten(
        self, token: TokenInfo, known: list[int] | None, now: datetime
    ) -> list[Job]:
        """옛 워커의 「잊힌 잡」 — 그 이름의 레거시 잡에만(T11).
        옛 워커는 heartbeat 을 한 스레드에서 차례로 보낸다."""
        active = self.store.legacy_jobs_of_worker(token.name)
        if known is None:
            return active
        forgotten = [j for j in active if j.id not in set(known)]
        for job in forgotten:
            text, code, args = outcome.summary("worker_restarted_without_job", name=token.name)
            closed = self.store.finish(
                job.id,
                LOST,
                now=now,
                summary=text,
                summary_code=code,
                summary_args=args,
                only_from=BUSY_STATES,
                hold_cause="worker_restarted_without_job",
            )
            self._publish_job(None, job.id)
            if closed and self.store.has_open_hold(job.id):  # mutcheck: legacy-hold-silent
                self._hold_created(job.id)
        if forgotten:
            self._drop_log_partial([f.id for f in forgotten])
        return [j for j in active if j.id not in {f.id for f in forgotten}]

    def _session_forgotten(
        self,
        token: TokenInfo,
        session: str,
        known: list[int] | None,
        seq: int,
        settled: int,
        now: datetime,
    ) -> list[Job]:
        """T5(결정 339-a · 339-b): 그 세션의 heartbeat 가 받아들인 것보다 큰 `seq` 를 싣고, 그 잡을
        돌려준 claim 이 워커에서 끝난 뒤(`claims_settled ≥ claim_seq`) 찍은 목록에 잡이 없으면
        워커는 그 잡을 받지 않았고 앞으로도 받을 수 없다 — `lost` 로 닫는다.

        그래도 「목록에 없다」는 지금 상태라서 확인이 아니다. 표식 잡이면 같은 트랜잭션에서 홀드를
        건다. 판정과 닫기는 그 세션의 잠금 안이다 — 같은 세션의 다른 heartbeat 와 줄을 선다."""
        closed: list[int] = []
        held: list[int] = []
        try:
            with self._session_lock(session):
                with self._heard_lock:
                    accepted = self._hb_seq.get(session, 0)
                    fresh = seq > accepted  # mutcheck: hb-stale-seq-judged
                    if fresh:
                        self._hb_seq[session] = seq
                active = self.store.jobs_of_session(session)
                if fresh and known is not None:
                    listed = set(known)
                    forgotten = [
                        j
                        for j in active
                        if j.id not in listed
                        and j.claim_seq is not None
                        and j.claim_seq <= settled  # mutcheck: t5-ignores-claims-settled
                    ]
                    text, code, args = outcome.summary(
                        "worker_restarted_without_job", name=token.name
                    )
                    for job in forgotten:
                        if self.store.finish(
                            job.id,
                            LOST,
                            now=now,
                            summary=text,
                            summary_code=code,
                            summary_args=args,
                            only_from=BUSY_STATES,
                            # mutcheck: t5-confirms-irreversible
                            hold_cause="worker_restarted_without_job",
                        ):
                            closed.append(job.id)
                            if self.store.has_open_hold(job.id):
                                held.append(job.id)
        finally:
            # 알림 · 발행은 잠금을 놓은 뒤에(§4.2). 중간에 터져도 이미 커밋한 닫힘은 알린다
            self._announce_closed(closed, held)
        return [j for j in active if j.id not in set(closed)]

    def _announce_closed(self, closed: list[int], held: list[int]) -> None:
        """커밋한 닫힘의 발행 · 홀드 알림 · 깨우기(잠금 밖, §4.2). 닫기 한 무리가 중간에 예외로
        끊겨도 `finally` 에서 부른다 — 커밋된 홀드의 알림이 빠지지 않게(P3 3라운드 LOW)."""
        for job_id in closed:
            self._publish_job(None, job_id)
        for job_id in held:
            self._hold_created(job_id)
        if closed:
            self._drop_log_partial(closed)
            self.wake.set()

    def _cancel_deadline(self, job: Job) -> datetime | None:
        """취소 미확인 닫기의 기준 `kill_at`. None 이면 아직 닫지 않는다.

        T4 「조용해지면 `kill_at` 규칙 멈춤」: 조용했던 시간은 세지 않는다(P2-6 의 답). 워커는
        heartbeat 의 답으로 취소를 듣는다. 그래서 창은 워커가 취소를 들을 수 있었던 때부터 잰다.

        - 요청 뒤로 그 세션을 아직 못 들었으면 None 이다. 워커는 취소를 모른다(분단 중의 janitor).
          `worker_timeout` 이 지나면 유예 판정이 맡는다.
        - 요청을 걸친 침묵(2H 넘게)이 끝나고 다시 들렸으면 창을 그때부터 새로 잰다 —
          `다시 들린 때 + (kill_at − 요청)`. 돌아온 첫 heartbeat 는 닫지 않고 `cancel` 을 전한다.
          서버가 꺼져 있던 동안도 침묵이다(기동 뒤 처음 들음).
        - 워커가 취소를 들은 뒤의 침묵은 창을 늘리지 않는다."""
        cancel = job.cancel
        if cancel is None or cancel.kill_at is None:
            return None
        key = job.worker_session_id or f"legacy:{job.worker_name}"
        silent = self.mono_fn() - self.heard_at(key)  # type: ignore[attr-defined]
        last_heard = self.now_fn() - timedelta(seconds=max(0.0, silent))
        if last_heard <= cancel.requested_at:  # mutcheck: t4-unheard-since-cancel-closed
            return None
        window = cancel.kill_at - cancel.requested_at
        with self._heard_lock:
            records = list(self._resumed.get(key, ()))
        deadline = cancel.kill_at
        for at, gap in records:
            began = None if gap == math.inf else at - timedelta(seconds=gap)
            # mutcheck: t4-short-quiet-counted
            spans = began is None or began <= cancel.requested_at
            if at > cancel.requested_at and spans:
                deadline = max(deadline, at + window)
        return deadline

    def _prune_resumed(self, jobs: list[Job], now: datetime) -> None:
        """창이 볼 일 없는 「다시 들림」 기록을 지운다(T4 · P3 3라운드 LOW). `jobs` 는 그 세션의
        busy 잡 전부이고 `now` 는 그 목록을 읽기 전 시각이다.

        창은 요청 뒤의 기록만 본다(`_cancel_deadline`). 그래서 열린 취소 가운데 가장 이른 요청
        이전의 기록은 쓰이지 않는다. 열린 취소가 없으면 기준은 `now` 다 — 목록을 읽은 뒤에 온
        취소의 요청은 `now` 뒤라 그 전의 기록을 보지 않는다."""
        floors: dict[str, datetime] = {}
        for job in jobs:
            key = job.worker_session_id or f"legacy:{job.worker_name}"
            floor = floors.get(key, now)
            if job.state == CANCELLING and job.cancel is not None:
                floor = min(floor, job.cancel.requested_at)  # mutcheck: resumed-pruned-past-request
            floors[key] = floor
        with self._heard_lock:
            for key, floor in floors.items():
                records = self._resumed.get(key)
                if records:
                    records[:] = [r for r in records if r[0] > floor]

    def _close_unconfirmed_cancels(self, jobs: list[Job], now: datetime, held: list[int]) -> None:
        """살아 있는 워커가 취소를 확인하지 않으면 `kill_at + 2 × heartbeat` 뒤 서버가 닫는다.
        `kill_at` 은 `_cancel_deadline` 이 조용했던 시간만큼 민다. 홀드가 걸린 잡 id 를 커밋마다
        `held` 에 더한다 — 호출자가 세션 잠금을 놓은 뒤(중간에 터져도 `finally` 에서)
        `_hold_created` 를 부른다(§4.2 · §4.12 · P3 3라운드 LOW)."""
        # 순환 import 를 피한다(위 finish 의 `outcome_for` 와 같다)
        from remote_ci_monitor.worker import cancel_outcome

        self._prune_resumed(jobs, now)
        slack = timedelta(seconds=2 * self.config.server.worker_heartbeat_seconds)
        for job in jobs:
            if job.state != CANCELLING:
                continue
            deadline = self._cancel_deadline(job)
            if deadline is not None and now >= deadline + slack:
                # 새 잡에 밀려난 잡은 그 표시를 지킨다(M5o) — N 을 기억하는 자리가 요약뿐이다
                text, code, args = cancel_outcome(job)
                closing = (
                    {"summary": text, "summary_code": code, "summary_args": args}
                    if code == "superseded"
                    else _finish_outcome("cancel_unconfirmed")
                )
                if self.store.finish(
                    job.id,
                    CANCELLED,
                    now=now,
                    **closing,
                    only_from=(CANCELLING,),
                    # 들리는 워커가 취소를 확인하지 않았다 — 프로세스가 아직 돌 수 있다(결정 330)
                    hold_cause="cancel_unconfirmed",
                ):
                    self._drop_log_partial([job.id])
                    self._publish_job(None, job.id)
                    if self.store.has_open_hold(job.id):  # mutcheck: cancel-unconfirmed-hold-silent
                        held.append(job.id)

    # ── down · lost 판정 (janitor 루프) ─────────────────────────────────────

    def mark_lost_workers(self, now: datetime) -> list[int]:
        """janitor(5초) — 끊김 유예 판정(M5t-b §4.3)과 살아 있는 세션의 취소 미확인 닫기.

        레인 down(벽시계 `last_seen_at`, F1)은 잡을 닫지 않는다. 잡은 그 세션을 서버가 마지막으로
        들은 단조 시각(바닥 = 기동)에서 claim 때 얼린 유예 G 가 지나야 `lost`(`worker_unreachable`,
        인자 `seconds` = 단조 경과)다. 후보마다 그 세션의 잠금 안에서 `heard` 를 다시 보고 아직
        넘었으면 조건부로 닫는다(RL4). 표식 잡은 같은 트랜잭션에서 그룹 홀드다(결정 330). 한 틱에
        많아야 `GRACE_CLOSES_PER_TICK` 개, 닫기가 DB 바쁨이면 이 틱을 멈춘다(결정 339-g)."""
        lost: list[int] = []
        held: list[int] = []
        cancel_held: list[int] = []
        try:
            mono = self.mono_fn()  # type: ignore[attr-defined]
            timeout = self.config.server.worker_timeout_seconds
            for row in self._workers():
                if not self.worker_alive(row, now):
                    with self._remote_lock:
                        self._worker_samples.pop(row.name, None)
                    continue
                # 취소 미확인 닫기는 그 이름의 **현재 · 들리는** 세션의 잡에만(결정 266 · RL33).
                # 펜싱된 세션의 취소 중 잡을 살아 있는 새 세션 때문에 닫으면 그룹 자리가 풀린다
                current = self.store.current_session(row.name)
                if current is None:  # mutcheck: cancel-unconfirmed-any-session
                    continue
                with self._heard_lock:
                    unheard = current not in self._heard
                if unheard:  # mutcheck: restart-unheard-cancel-closed
                    continue  # 기동 뒤 아직 안 들렸다 — 들리는 세션이 아니다
                if mono - self.heard_at(current) > timeout:
                    continue
                with self._session_lock(current):
                    jobs = self.store.jobs_of_session(current)
                    self._close_unconfirmed_cancels(jobs, now, cancel_held)
            default_grace = disconnect_grace(self.config.server)
            budget = GRACE_CLOSES_PER_TICK
            for job in self.store.busy_remote_jobs():
                key = job.worker_session_id or f"legacy:{job.worker_name}"
                grace = job.disconnect_grace_seconds or default_grace
                if mono - self.heard_at(key) <= grace:  # mutcheck: grace-wall-clock
                    continue
                if budget <= 0:
                    break
                with self._session_lock(key):
                    gap = self.mono_fn() - self.heard_at(key)  # type: ignore[attr-defined]
                    if gap <= grace:
                        continue  # 판정 뒤에 들었다 — 닫지 않는다
                    code = "worker_unreachable"
                    if job.worker_session_id is not None and job.irreversible:
                        srow = self.store.get_session(job.worker_session_id)
                        if srow is not None and srow.orphan_unverified:
                            # 다음 프로세스가 고아를 확인하지 못했다고 알렸다 — 「그 머신에서 아직
                            # 돌 수 있다」를 말한다(결정 327). 그룹은 홀드다(결정 330)
                            code = "orphan_unverified"  # mutcheck: orphan-unverified-code-dropped
                    text, code, args = outcome.summary(code, name=job.worker_name, seconds=int(gap))
                    try:
                        closed = self.store.finish(
                            job.id,
                            LOST,
                            now=now,
                            summary=text,
                            summary_code=code,
                            summary_args=args,
                            only_from=BUSY_STATES,
                            hold_cause=code,
                        )
                    except sqlite3.OperationalError as e:
                        if not _is_busy_error(e):
                            raise
                        break  # 바쁘다 — 다음 틱에 다시 한다. 늦게 닫는 것은 안전한 쪽이다
                budget -= 1
                if closed:
                    lost.append(job.id)
                    if job.irreversible is not False and self.store.has_open_hold(job.id):
                        held.append(job.id)
        finally:
            # 알림 · 발행 · 로그는 잠금을 놓은 뒤에(§4.2). 틱이 중간에 예외로 끊겨도 이미 커밋한
            # 닫힘은 알린다 — 모아 두었다 끝에서만 알리면 그 홀드의 알림이 빠진다(P3 3라운드 LOW)
            for job_id in cancel_held:
                self._hold_created(job_id)
            if lost:
                self.log(f"disconnect grace over: lost={lost}")
                self._drop_log_partial(lost)
                for job_id in lost:
                    self._publish_job(None, job_id)
                for job_id in held:
                    self._hold_created(job_id)
                self._publish_server()
                self.wake.set()
        self._forget_closed_sessions()
        return lost

    def _forget_closed_sessions(self) -> None:
        """펜싱된 세션의 busy 잡이 모두 닫히면 그 세션의 메모리 값을 지운다(§4.2). 그 전에는
        남긴다 — janitor 가 `heard + G` 로 그 잡들을 닫아야 한다. 레거시 키는 레거시 busy 잡이 없을
        때 지운다."""
        with self._heard_lock:
            keys = list(self._heard)
        drop: list[str] = []
        for key in keys:
            if key.startswith("legacy:"):
                if not self.store.has_legacy_busy(key.removeprefix("legacy:")):
                    drop.append(key)
                continue
            row = self.store.get_session(key)
            if row is None or (row.fenced_at is not None and not self.store.jobs_of_session(key)):
                drop.append(key)
        if drop:
            with self._heard_lock:
                for key in drop:
                    self._heard.pop(key, None)
                    self._hb_seq.pop(key, None)
                    self._resumed.pop(key, None)
                    self._session_locks.pop(key, None)


__all__ = [
    "CLAIM_WAIT_SLOTS",
    "MAX_WORKER_LOG_BODY",
    "RemoteWorkersMixin",
    "SUMMARY_CANCEL_UNCONFIRMED",
    "SUMMARY_RESTARTED",
    "SUMMARY_UNREACHABLE",
]
