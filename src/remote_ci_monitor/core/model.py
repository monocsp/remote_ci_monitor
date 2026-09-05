"""도메인 모델 — Job · Preset · Progress · Estimate · StatusModel.

순수 데이터다. 시각은 전부 UTC aware `datetime`. JSON 으로 바꾸는 규칙은 `core/status.py`.
잡 상태와 생명주기는 PLAN.md 「잡 모델과 생명주기」를 따른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ── 잡 상태 ──────────────────────────────────────────────────────────────────
UPLOADING = "uploading"
QUEUED = "queued"
RUNNING = "running"
CANCELLING = "cancelling"
SUCCEEDED = "succeeded"
FAILED = "failed"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"
LOST = "lost"

#: 대기 잡 — position 은 이 상태에만 1부터 매긴다.
WAITING_STATES = frozenset({UPLOADING, QUEUED})
#: 실행 중(취소 진행 포함). position 은 null.
BUSY_STATES = frozenset({RUNNING, CANCELLING})
#: 큐에 보이는 잡 전부. 합류 판정도 이 집합에서 한다.
ACTIVE_STATES = WAITING_STATES | BUSY_STATES
#: 종료 상태 — 최근 완료에 남는다. 큐에서 조용히 사라지는 잡은 없다.
TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, LOST})
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES

#: `rcm wait` 종료 코드. 3 은 「모른다」이지 「실패」가 아니다(fail-open 금지).
EXIT_CODE_BY_STATE = {
    SUCCEEDED: 0,
    FAILED: 1,
    CANCELLED: 2,
    TIMED_OUT: 2,
    LOST: 3,
}
EXIT_UNKNOWN = 3

# ── 소스 모드 ────────────────────────────────────────────────────────────────
MODE_TREE = "tree"
MODE_GIT_REF = "git_ref"
SOURCE_MODES = (MODE_TREE, MODE_GIT_REF)

# ── 입력 타입 ────────────────────────────────────────────────────────────────
INPUT_TYPES = ("string", "choice", "bool", "int")
MAX_INPUT_LENGTH = 256

# ── 진행 단계 ────────────────────────────────────────────────────────────────
PHASE_MATERIALIZING = "materializing"
PHASE_EXECUTING = "executing"

# ── 워커 상태 ────────────────────────────────────────────────────────────────
WORKER_IDLE = "idle"
WORKER_BUSY = "busy"
WORKER_DOWN = "down"

# ── reason 열거값 (PLAN.md 「큐 규칙」) ─────────────────────────────────────
REASON_RUNNING = "running"
REASON_WAITING_FOR_LANE = "waiting_for_lane"
REASON_BLOCKED_BY_GROUP = "blocked_by_group"
REASON_UPLOADING = "uploading"
REASON_UPLOAD_STALLED = "upload_stalled"
REASON_MATERIALIZING = "materializing"
REASON_OVERDUE = "overdue"
REASON_STUCK = "stuck"
REASON_CANCELLING = "cancelling"
REASON_PAUSED = "paused"
REASON_NOT_SCHEDULED = "not_scheduled"
REASON_WORKER_DOWN = "worker_down"

#: 「Not moving」 요약에 오르는 행동 가능한 이유. 순서가 우선순위다.
ACTIONABLE_REASONS = (
    REASON_WORKER_DOWN,
    REASON_STUCK,
    REASON_UPLOAD_STALLED,
    REASON_NOT_SCHEDULED,
    REASON_BLOCKED_BY_GROUP,
    REASON_OVERDUE,
    REASON_PAUSED,
)


@dataclass(frozen=True)
class InputSpec:
    """프리셋 입력 하나의 스키마. 검증 규칙은 `core/inputs.py`."""

    name: str
    type: str = "string"
    choices: tuple[str, ...] = ()
    default: str | bool | int | None = None
    pattern: str | None = None
    description: str = ""


@dataclass(frozen=True)
class Preset:
    """서버 설정의 `[[presets]]` 하나. 세션은 이름과 입력값만 보낸다."""

    name: str
    argv: tuple[str, ...]
    description: str = ""
    timeout_seconds: int = 1200
    source_modes: tuple[str, ...] = (MODE_TREE,)
    concurrency_group: str | None = None
    expected_seconds: int | None = None
    duration_key_inputs: tuple[str, ...] = ()
    env_passthrough: tuple[str, ...] = ("PATH", "HOME", "LANG")
    env: dict[str, str] = field(default_factory=dict)
    inputs: tuple[InputSpec, ...] = ()

    def input_spec(self, name: str) -> InputSpec | None:
        for spec in self.inputs:
            if spec.name == name:
                return spec
        return None


@dataclass(frozen=True)
class Source:
    """잡의 코드 출처. `tree` 는 작업 트리 스냅샷, `git_ref` 는 원격 ref."""

    mode: str
    repo: str | None = None
    base_sha: str | None = None
    dirty: bool | None = None
    tree_hash: str | None = None
    bytes: int | None = None
    received_bytes: int | None = None
    last_received_at: datetime | None = None
    ref: str | None = None
    sha: str | None = None

    @property
    def identity(self) -> str | None:
        """합류 판정에 쓰는 소스 신원. tree 면 tree_hash, git_ref 면 sha."""
        return self.tree_hash if self.mode == MODE_TREE else self.sha


@dataclass(frozen=True)
class Requester:
    name: str
    label: str


@dataclass(frozen=True)
class Joiner:
    name: str
    label: str
    joined_at: datetime


@dataclass(frozen=True)
class CancelInfo:
    requested_at: datetime
    by: str
    kill_at: datetime | None


@dataclass(frozen=True)
class Transition:
    state: str
    at: datetime


@dataclass(frozen=True)
class Job:
    """서버 DB 의 잡 한 행. 계산에 필요한 것만 담고 로그 본문은 담지 않는다."""

    id: int
    preset: str
    inputs: dict[str, Any]
    key: str
    concurrency_group: str | None
    source: Source
    requester: Requester
    state: str
    created_at: datetime
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    summary: str | None = None
    failed_step: str | None = None
    lane: int | None = None
    timeout_seconds: int | None = None
    cancel: CancelInfo | None = None
    cancelled_by: str | None = None
    phase: str | None = None
    last_output_at: datetime | None = None
    joiners: tuple[Joiner, ...] = ()
    transitions: tuple[Transition, ...] = ()

    @property
    def is_waiting(self) -> bool:
        return self.state in WAITING_STATES

    @property
    def is_busy(self) -> bool:
        return self.state in BUSY_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def owned_by(self, token_name: str) -> bool:
        """요청자이거나 합류자면 「내 잡」이다(오너 결정 16)."""
        if self.requester.name == token_name:
            return True
        return any(j.name == token_name for j in self.joiners)


@dataclass(frozen=True)
class Step:
    index: int
    name: str
    state: str  # "done" | "running"
    ok: bool | None
    seconds: float | None
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(frozen=True)
class Progress:
    """스텝 마커로 만든 진행 상태. `timing` 은 항상 `as_received`."""

    phase: str
    steps: tuple[Step, ...] = ()
    steps_total: int | None = None
    steps_total_partial: bool = True
    steps_done: int = 0
    current_index: int | None = None
    current_name: str | None = None
    current_seconds: float | None = None
    job_seconds: float | None = None
    failed_step: str | None = None
    summary: str | None = None
    last_output_at: datetime | None = None
    timing: str = "as_received"


@dataclass(frozen=True)
class BlockedBy:
    job_id: int
    group: str
    remaining_seconds: float | None


@dataclass(frozen=True)
class Estimate:
    expected_seconds: float
    source: str  # "measured" | "preset" | "default"
    sample_count: int
    elapsed_seconds: float | None
    waited_seconds: float | None
    remaining_seconds: float | None
    wait_seconds: float | None
    overdue: bool
    stuck: bool
    finish_at: datetime | None


@dataclass(frozen=True)
class QueueRow:
    """큐 표의 한 행 = 잡 + 서버가 계산한 순번·이유·추정."""

    job: Job
    position: int | None
    reason: str
    lane: int | None
    ahead_job_id: int | None
    blocked_by: BlockedBy | None
    estimate: Estimate
    progress: Progress | None


@dataclass(frozen=True)
class Median:
    seconds: float
    wait_seconds: float | None
    sample_count: int


@dataclass(frozen=True)
class WorkerInfo:
    lane: int
    state: str  # idle | busy | down
    job_id: int | None = None
    error: str | None = None
    since: datetime | None = None


@dataclass(frozen=True)
class Paused:
    by: str
    at: datetime


@dataclass(frozen=True)
class ServerInfo:
    version: str
    uptime_seconds: float
    lanes: int
    paused: Paused | None
    last_error: str | None
    workers: tuple[WorkerInfo, ...]
    sse_connections: int = 0


@dataclass(frozen=True)
class HostSample:
    """호스트 자원 표본. 수집은 M1(`hostsample.py`) — 모델만 미리 둔다."""

    name: str
    source: str
    sampled_at: datetime
    interval_seconds: float
    os: str | None = None
    cores: int | None = None
    load: tuple[float, float, float] | None = None
    cpu: dict[str, float | None] | None = None
    memory: dict[str, int | None] | None = None
    gpu: dict[str, Any] | None = None
    gpu_note: str | None = None
    top: tuple[dict[str, Any], ...] = ()
    history: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Pool:
    """조회·수집 실패 섹션은 `None` + `*_error`. 빈 목록과 실패는 다른 값이다."""

    name: str
    lanes: int
    queue: tuple[QueueRow, ...] | None
    queue_error: str | None
    recent: tuple[Job, ...] | None
    recent_error: str | None
    recent_count: int
    medians: dict[str, Median] | None
    medians_error: str | None
    hosts: tuple[HostSample, ...] | None
    hosts_error: str | None


@dataclass(frozen=True)
class StatusModel:
    generated_at: datetime
    display_timezone: str | None
    server: ServerInfo
    presets: tuple[Preset, ...]
    pools: tuple[Pool, ...]
    base_url: str | None = None
