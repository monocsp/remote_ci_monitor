"""도메인 모델 — Job · Preset · Progress · Estimate · StatusModel.

순수 데이터다. 시각은 전부 UTC aware `datetime`. JSON 으로 바꾸는 규칙은 `core/status.py`.
잡 상태와 생명주기는 PLAN.md 「잡 모델과 생명주기」를 따른다.
"""

from __future__ import annotations

from collections.abc import Mapping
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
#: 정한 시각을 기다리는 예약 잡(M5s 결정 206). 프로세스도 레인도 없이 DB 행만 있다.
SCHEDULED = "scheduled"
#: 시작 마감 안에 레인이 집지 못한 예약 잡(M5s 결정 208). 돌지 않았다 — `cancelled` 와 같은 줄이다.
EXPIRED = "expired"
#: 반복을 선언한 프리셋(`[presets.retry]`)의 시도가 그 종료코드(기본 75)로 끝났고 다음 시도를 걸었다
#: (M5s 결정 211). **종료 상태지만 실패가 아니다** — 기다림은 `retried_as` 를 따라 계속된다.
RETRIED = "retried"
#: 반복의 마지막 시도도 그 종료코드로 끝났다(M5s 결정 212). 끝내 확인하지 못했다 — `rcm wait` 1.
EXHAUSTED = "exhausted"

#: 대기 잡 — position 은 이 상태에만 1부터 매긴다.
WAITING_STATES = frozenset({UPLOADING, QUEUED})
#: 실행 중(취소 진행 포함). position 은 null.
BUSY_STATES = frozenset({RUNNING, CANCELLING})
#: 큐에 보이는 잡 전부. 합류 판정도 이 집합에서 한다.
ACTIVE_STATES = WAITING_STATES | BUSY_STATES
#: 예약 잡(M5s 결정 206). **활성 집합 밖**이다 — 넣으면 같은 입력의 즉시 실행이 예약에 합류하고
#: (`store.JOINABLE_STATES`), 큐 계산이 대기도 실행도 아닌 행을 말없이 빠뜨린다. 대신 blob 보호 ·
#: 취소 · 잡 문서 · `rcm wait` · `rcm jobs` · 워커 잊기가 이 집합을 **명시적으로** 본다.
SCHEDULED_STATES = frozenset({SCHEDULED})
#: 종료 상태 — 최근 완료에 남는다. 큐에서 조용히 사라지는 잡은 없다.
TERMINAL_STATES = frozenset(
    {SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, LOST, EXPIRED, RETRIED, EXHAUSTED}
)
ALL_STATES = ACTIVE_STATES | SCHEDULED_STATES | TERMINAL_STATES
#: `[[notify]]` 에 `on` 이 없을 때 울리는 상태(`config.parse_notify` · `core/notify.NotifyRule`
#: 두 곳이 이것을 쓴다). 종료 상태에서 `retried` 를 뺀 것이다(M5s 결정 211) — 시도마다 울리면
#: 2시간짜리 사슬이 알림 12개가 된다. `on = ["retried"]` 로 적으면 받는다.
NOTIFY_DEFAULT_STATES = TERMINAL_STATES - {RETRIED}
#: 사슬의 끝이 아닌 종료 — 최근 목록 · 소요 표본 · 실패 통계에 넣지 않는다(M5s §4.2 「목록」).
#: 사슬은 마지막 시도 한 행으로 보인다.
CHAIN_LINK_STATES = frozenset({RETRIED})

#: `rcm wait` 종료 코드. 3 은 「모른다」이지 「실패」가 아니다(fail-open 금지).
EXIT_CODE_BY_STATE = {
    SUCCEEDED: 0,
    FAILED: 1,
    CANCELLED: 2,
    TIMED_OUT: 2,
    EXPIRED: 2,  # M5s 결정 212 — 돌지 않았다
    EXHAUSTED: 1,  # M5s 결정 212 — 끝내 확인하지 못했다. 사람이 봐야 한다
    LOST: 3,
    # `retried` 는 없다 — 기다림이 끝나지 않고 `retried_as` 를 따라간다(결정 211). 그 상태로
    # 끝난 대기는 「모른다」(3)다(`retried_as` 가 없는 사슬, 결정 225)
}
EXIT_UNKNOWN = 3

# ── 우선순위 (M5) — 세 단계면 충분하다. 숫자 우선순위는 기아를 만들고 설명이 어렵다 ─────
PRIORITY_LOW = -1
PRIORITY_NORMAL = 0
PRIORITY_HIGH = 1
PRIORITY_NAMES = {"low": PRIORITY_LOW, "normal": PRIORITY_NORMAL, "high": PRIORITY_HIGH}
PRIORITY_LABELS = {PRIORITY_LOW: "low", PRIORITY_NORMAL: "normal", PRIORITY_HIGH: "high"}

# ── 풀 (M5b) — 로컬 워커는 "default", 원격 워커는 자기 풀 이름으로 claim 한다 ──────────
DEFAULT_POOL = "default"

#: 토큰 종류(M5b-2). client = 세션 · admin = 관리 · worker = 원격 워커(`/worker/*` 만).
TOKEN_CLIENT = "client"
TOKEN_ADMIN = "admin"
TOKEN_WORKER = "worker"
TOKEN_KINDS = (TOKEN_CLIENT, TOKEN_ADMIN, TOKEN_WORKER)

# ── 소스 모드 ────────────────────────────────────────────────────────────────
MODE_TREE = "tree"
MODE_GIT_REF = "git_ref"
SOURCE_MODES = (MODE_TREE, MODE_GIT_REF)

# ── 입력 타입 ────────────────────────────────────────────────────────────────
INPUT_TYPES = ("string", "choice", "bool", "int")
MAX_INPUT_LENGTH = 256
#: 긴 값을 받는 입력 — 스토어 문안 편집본(`listing_json`, 버전 페이지 Q4)은 JSON 한 덩어리라
#: 256자로는 안 된다. 이름으로 고른다: 계약이 그 이름을 고정했고 다른 입력은 그대로 짧다.
LONG_INPUTS = frozenset({"listing_json"})
MAX_LONG_INPUT_LENGTH = 64 * 1024

# ── 진행 단계 ────────────────────────────────────────────────────────────────
PHASE_MATERIALIZING = "materializing"
PHASE_EXECUTING = "executing"

# ── 워커 상태 ────────────────────────────────────────────────────────────────
WORKER_IDLE = "idle"
WORKER_BUSY = "busy"
WORKER_HELD = "held"  # 부하 게이트가 막고 있다 — 살아 있지만 지금은 못 집는다 (M5f)
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
#: 집었을 레인이 호스트 부하로 보류 중이다(M5f). **`ACTIONABLE_REASONS` 에는 안 올린다** —
#: 의도된·자가 치유되는 상태라 `paused` 와 같은 종류다(오너 결정 45). 오래 닫혀 있으면
#: `rcm check` 가 경고한다.
REASON_HELD_BY_LOAD = "held_by_load"
#: 출력이 조용하다 — 아직 죽었다고 말할 근거는 없다. **`ACTIONABLE_REASONS` 에는 안 올린다.**
#: `held_by_load` 와 같은 종류다: 의도되지 않았지만 **경보가 아니다**. 「확인이 필요한 작업」
#: 패널에 올리면 오늘의 빨간 소음이 이름만 바꿔 그대로 남는다 — 그것이 고치려던 사고다
#: (gate 프리셋은 마지막 단계에서 도구 셋을 병렬로 돌리고, 그것들은 끝날 때 출력을 몰아 뱉어
#: **정상 실행마다** 4분 침묵 조건에 걸렸다). 진짜로 죽은 잡은 `stuck` 과 프리셋의
#: `timeout_seconds` 가 잡는다.
REASON_QUIET = "quiet"
#: `any-capable` 잡인데 살아 있는 후보가 전부 ineligible · unknown 이다(M5n §7.3). 행동 가능.
REASON_NO_CAPABLE_MACHINE = "no_capable_machine"
#: 후보 워커가 전부 점검 실패로 막혔다(pool 잡 포함). 행동 가능 — 사람이 머신을 고쳐야 한다.
REASON_CHECK_FAILED = "check_failed"
#: 후보 중 점검이 돌고 있거나 기다리는 것뿐이다. 스스로 풀린다 — `held_by_load` 와 같은 종류.
REASON_CHECK_PENDING = "check_pending"
#: 도는 잡이 「사람이 할 일」 표식(`::rcm::action::`)을 찍었다(M5s 결정 213). 행동 가능 — 잡이
#: 직접 무엇을 해 달라고 말했다. 사람을 기다리는 잡은 대개 조용해서 `stuck` 보다 앞에 둔다.
REASON_NEEDS_ACTION = "needs_action"
#: 열린 그룹 홀드에 막힌 대기 잡의 `action.code`(M5t-b §4.8 · 결정 330). 사유는 새 값이 아니라
#: `needs_action` 이다 — 홀드는 사람(admin)이 그 머신을 보고 `rcm release-hold` 로만 풀린다.
ACTION_IRREVERSIBLE_HOLD = "irreversible_hold"

#: M5n 배치 · 그룹 범위 (DB 에 그대로 들어가는 값)
PLACEMENT_POOL = "pool"
PLACEMENT_CAPABLE = "capable"
PLACEMENTS = (PLACEMENT_POOL, PLACEMENT_CAPABLE)
SCOPE_POOL = "pool"
SCOPE_HOST = "host"
SCOPE_GLOBAL = "global"
SCOPES = (SCOPE_POOL, SCOPE_HOST, SCOPE_GLOBAL)
#: 기대 시간의 출처 — 그 호스트에는 표본이 모자라 **다른 호스트들**의 중앙값을 썼다(신뢰도 low)
SOURCE_MEASURED_OTHER = "measured_other"

#: `Estimate.stuck_code` — 「왜 죽었다고 보나」. 화면·CLI 가 근거별로 다른 문장을 그린다.
STUCK_ELAPSED = "over_elapsed"  # 경과가 예상의 `stuck_multiplier` 배를 넘었다
STUCK_STEP = "over_step"  # 현재 단계가 자기 실측 중앙값의 배수를 넘었다
STUCK_NO_OUTPUT = "no_output"  # 단계 이야기를 안 하는 잡이 `no_output_seconds` 동안 조용하다

#: 「Not moving」 요약에 오르는 행동 가능한 이유. 순서가 우선순위다.
#:
#: `needs_action` 이 맨 앞이다(M5s). 다른 이유는 rcm 이 관측해 짐작한 것이고, 이것만 잡이 스스로
#: 「사람이 이것을 해 달라」고 말한 것이다. 무엇을 할지(`action.note`)까지 정해져 있고, 하지
#: 않으면 잡이 나아가지 못한다(로그인이 풀린 출시 잡).
ACTIONABLE_REASONS = (
    REASON_NEEDS_ACTION,
    REASON_WORKER_DOWN,
    REASON_NO_CAPABLE_MACHINE,
    REASON_CHECK_FAILED,
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
    repo: str = ""  # git_ref 를 받는 프리셋이 가리키는 `[[repos]].name`
    priority: int = PRIORITY_NORMAL  # 이 프리셋 잡의 기본값이자 비-admin 의 상한 (M5)
    pool: str = DEFAULT_POOL  # 이 프리셋 잡의 기본 풀 (M5b)
    pools: tuple[str, ...] = ()  # 세션이 --pool 로 고를 수 있는 추가 풀(자기 pool 은 언제나 허용)
    concurrency_group: str | None = None
    expected_seconds: int | None = None
    duration_key_inputs: tuple[str, ...] = ()
    env_passthrough: tuple[str, ...] = ("PATH", "HOME", "LANG")
    env: dict[str, str] = field(default_factory=dict)
    #: 잡 시작 전에 최종 환경에서 찾아야 하는 도구 — 이름 또는 절대경로. 없으면 프로세스를
    #: 띄우지 않고 `tool_missing` 으로 실패한다 (M5j G4 · 결정 85)
    requires: tuple[str, ...] = ()
    inputs: tuple[InputSpec, ...] = ()
    artifacts: tuple[str, ...] = ()  # 잡이 만든 파일 중 돌려줄 것의 글롭 (M5e)
    artifacts_on: str = "always"  # "always"(오늘의 동작) | "failure" — 언제 모으나 (M5g)
    #: 어디서 도나(M5n 결정 97). `pool`(기본, 오늘 그대로) · `capable`(판정이 eligible 인 워커
    #: 아무 데나). 설정 문자열은 `"any-capable"` 이고 여기엔 DB 와 같은 값이 들어간다.
    placement: str = "pool"
    #: 같은 `concurrency_group` 을 막는 범위(M5n 결정 100) — `pool` · `host` · `global`
    concurrency_scope: str = "pool"
    #: 이 워커에서 현재 지문으로 통과해야 하는 점검 프리셋들(M5n §4.4)
    checks: tuple[str, ...] = ()
    #: rcm 이 점검을 스스로 걸 때 쓰는 ref(점검 프리셋에만 뜻이 있다). `HEAD` = 기본 브랜치
    check_ref: str = "HEAD"
    #: 추정 표본에 넣나(M5n 결정 103). None 이면 설정을 읽을 때 정한다 — 점검 프리셋은 False
    sample: bool | None = None
    #: 새 잡이 큐에 들어올 때 같은 브랜치의 옛 잡을 치우나(M5o 결정 109·110) —
    #: `off`(기본, 오늘 그대로) · `waiting`(기다리는 잡만) · `all`(도는 잡도). `core/supersede.py`
    supersede: str = "off"
    #: 끝나면 산출물을 보관함에 넣나(M5p 결정 126). 제출 때 잡에 얼린다 — `artifacts` 가 있어야 한다
    keep: bool = False
    #: 예약 잡의 시작 마감(초, M5s 결정 208). None 이면 서버의 `schedule_start_deadline_seconds`
    start_deadline_seconds: int | None = None
    #: 종료코드 반복(M5s 결정 210). `[presets.retry]` 를 선언한 프리셋만 — None 이면 그 종료코드도
    #: 여느 실패다(rcm-ci 의 75 = 인프라 실패를 지킨다). 제출할 때 잡 열로 얼린다(결정 229)
    retry: RetryPolicy | None = None
    #: 원격 금지(M5t-a 결정 254). 참이면 이 프리셋에게 서버의 머신 목록은 `@local` 하나뿐이다 —
    #: 원격 워커는 집지도, 후보로 보이지도 않는다. 비밀을 쓰는 release · deploy 프리셋에 단다
    local_only: bool = False
    #: 되돌릴 수 없음(M5t-b 결정 257). 참이면 claim 때 잡에 얼리고, 감독자 아래에서 돌고(331),
    #: 프로세스 죽음 확인 없이 닫히면 그룹을 홀드한다(330). 그룹이 없으면 합성 그룹을 쓴다
    irreversible: bool = False
    #: `concurrency_group` 을 설정이 아니라 rcm 이 채웠다(`irreversible:<이름>`, 결정 330)
    group_synthetic: bool = False

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
    uploaded_bytes: int | None = None  # M5 캐시: 이번에 실제로 받은 바이트
    cached_bytes: int | None = None  # M5 캐시: 캐시 히트 바이트
    #: tree 잡의 브랜치 이름(표시용, M5h). 신원은 `tree_hash` 이고 여기는 안 들어간다
    branch: str | None = None

    @property
    def identity(self) -> str | None:
        """합류 판정에 쓰는 소스 신원. tree 면 tree_hash, git_ref 면 sha."""
        return self.tree_hash if self.mode == MODE_TREE else self.sha


@dataclass(frozen=True)
class Requester:
    name: str
    label: str


@dataclass(frozen=True)
class JobLink:
    """호출자가 잡에 붙인 링크 하나(예: 그 커밋의 PR). rcm 은 무엇을 가리키는지 모른다 — 결정 30.

    `url` 은 검증된 http(s) 절대 주소(`core/joblink.parse_link`), `label` 은 짧은 글이거나 None
    (None 이면 화면이 주소에서 줄여 보인다). 합류자의 링크는 받지 않는다 — 처음 세운 제출의 것.
    """

    url: str
    label: str | None = None


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
class RetryPolicy:
    """`[presets.retry]` — 이 종료코드로 끝나면 `interval_seconds` 뒤에 다시 돈다(M5s §4.2).

    `max_attempts` 는 첫 시도를 포함한 시도 수다. 마지막 시도도 그 코드면 `exhausted` 다."""

    on_exit: int = 75
    interval_seconds: int = 600
    max_attempts: int = 12


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
    #: 서버가 만든 요약이면 코드와 원시 인자가 함께 온다(결정 37). 잡이 `::rcm::summary::` 로
    #: 찍은 문장에는 코드가 없다 — 팀이 쓴 문장이라 그대로 보여 준다.
    summary_code: str | None = None
    summary_args: dict[str, Any] = field(default_factory=dict)
    failed_step: str | None = None
    #: 마지막으로 시작한 스텝 — 인과를 주장하지 않는다. 취소·유실 잡에는 없다 (M5h)
    last_step: str | None = None
    #: 실패 이름이 상한을 넘어 버려진 것이 있다. 공개 JSON 은 `failures_truncated` (M5h)
    fail_truncated: bool = False
    lane: int | None = None
    timeout_seconds: int | None = None
    cancel: CancelInfo | None = None
    cancelled_by: str | None = None
    phase: str | None = None
    last_output_at: datetime | None = None
    joiners: tuple[Joiner, ...] = ()
    transitions: tuple[Transition, ...] = ()
    artifacts_purged_at: datetime | None = None  # 보존 정리로 로그·스냅샷·워크스페이스를 지운 시각
    priority: int = PRIORITY_NORMAL  # -1 low · 0 normal · 1 high (M5)
    pool: str = DEFAULT_POOL  # 어느 풀의 워커가 돌리는가 (M5b)
    worker_name: str | None = None  # 원격 워커가 claim 했으면 그 이름, 로컬 레인은 None (M5b-2)
    link: JobLink | None = None  # 호출자가 붙인 링크(v22). 옛 잡 · 링크 없이 낸 잡은 None
    #: M5n(v23) — 어디서 돌 수 있나와 어디서 돌았나
    placement: str = "pool"  # "pool" | "capable"
    display_pool: str | None = None  # capable 잡이 기다리는 동안 보이는 풀(None = pool)
    target_worker: str | None = None  # 그 워커만 집는다(점검 잡 · --machine)
    concurrency_scope: str = "pool"  # "pool" | "host" | "global" — 제출 때 박는다
    host_key: str | None = None  # 집은 워커의 호스트(claim 때 박는다)
    fingerprint_hash: str | None = None  # 집은 워커의 그때 지문
    sample: bool = True  # 추정 표본에 넣나(점검 · 예열 잡은 False)
    #: M5p(v24) — 끝나면 산출물을 보관함에 넣나. 제출 때 얼린다(프리셋 keep OR 제출 keep).
    #: reload · 재시작에 바뀌지 않고, 합류와 supersede 로 켜질 수만 있다(결정 127 · 128)
    keep: bool = False
    #: M5s(v25) — 예약. `not_before` 는 승격할 시각, `start_deadline` 은 그때까지 레인이 집지 못하면
    #: `expired` 로 닫는 시각이다. 즉시 실행한 잡은 둘 다 None 이다. 제출 때 정하고 바꾸지 않는다
    not_before: datetime | None = None
    start_deadline: datetime | None = None
    #: 제출의 `"supersede": false` 를 얼린 값(결정 237). 재시작해도 잊지 않는다
    no_supersede: bool = False
    #: M5s(v25) — 반복 사슬(결정 211 · 228). `chain_id` 는 뿌리 잡 id 이고 반복을 켠 잡에만 있다.
    #: `retry_of` 는 바로 앞 시도, `attempt` 는 1부터. `retried_as` 는 `retried` 로 끝난 시도의 다음
    #: 시도 id(저장소가 읽을 때 채운다 — 열이 아니다)
    chain_id: int | None = None
    retry_of: int | None = None
    attempt: int = 1
    retried_as: int | None = None
    #: 제출할 때 얼린 반복 정책(결정 229). `retry_on_exit` 가 None 이면 반복하지 않는다.
    #: `retry_disabled` 는 제출의 `"retry": false` 다
    retry_on_exit: int | None = None
    retry_interval_seconds: int | None = None
    retry_max_attempts: int | None = None
    retry_disabled: bool = False
    #: M5t-b(v26) — claim 한 워커 세션(결정 265). 로컬 잡과 v26 이전 레거시 잡은 None
    worker_session_id: str | None = None
    #: claim 때 얼린 「되돌릴 수 없음」(결정 257). None 은 claim 전 · 레거시다
    irreversible: bool | None = None
    #: claim 때 얼린 끊김 유예(초). 원격 잡에만 있다 — 로컬 잡은 None
    disconnect_grace_seconds: int | None = None
    #: 그 잡을 돌려준 claim 요청의 워커 순번(결정 339-a). 로컬 · 레거시는 None
    claim_seq: int | None = None

    @property
    def chain_root(self) -> int:
        """사슬의 뿌리 잡 id — 사슬이 없으면 자기 id 다."""
        return self.chain_id if self.chain_id is not None else self.id

    def retries_on(self, rc: int | None) -> bool:
        """이 종료코드가 얼린 반복 정책에 걸리나. 선언이 없거나 제출이 껐으면 언제나 False."""
        return (
            rc is not None
            and self.retry_on_exit is not None
            and not self.retry_disabled
            and rc == self.retry_on_exit
        )

    @property
    def wait_origin(self) -> datetime:
        """대기를 재는 기준 시각. 예약 잡은 승격한 시각(`queued_at`)이고 나머지는 만든 시각이다
        (M5s §3 #3) — 예약까지 기다린 시간은 「큐가 밀렸다」가 아니다."""
        if self.not_before is not None and self.queued_at is not None:
            return self.queued_at
        return self.created_at

    @property
    def is_scheduled(self) -> bool:
        return self.state in SCHEDULED_STATES

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
class ShelfItem:
    """보관함 항목 하나 — `shelf_items` 표의 한 행 그대로(M5p §5.1 · §15.3).

    잡 행보다 오래 산다(잡 행은 180일에 지워진다). 그래서 페이지에 필요한 잡의 열을 **복사해
    들고 있다**. 시각은 전부 epoch 초(float)다 — 순수 규칙(`core/shelf.py`)이 그대로 받는다.
    `sha` 는 git_ref 면 커밋, tree 면 트리 해시다(`source_mode` 로 가른다). `requester` 는
    사람이 읽는 이름(`requester_label`)이다.
    """

    job_id: int
    state: str  # pending | copying | active | stale | failed | deleted
    preset: str
    inputs_json: str | None
    repo: str | None
    branch: str | None
    sha: str | None
    source_mode: str | None
    requester: str | None
    job_state: str | None  # 잡이 끝났을 때만 채운다(원격 잡은 묶음이 끝나기 전에 행이 선다)
    job_finished_at: float | None
    requested_at: float
    shelved_at: float | None
    last_used_at: float | None  # 시계 — 이 값 하나로 정리 대기와 삭제를 잰다(결정 131)
    stale_since: float | None
    deleted_at: float | None
    file_count: int | None
    total_bytes: int | None
    reason_code: str | None
    reason_args: dict[str, Any] | None
    error_code: str | None
    failed_at: float | None  # 실패 행의 30일은 여기서 잰다(§15.3)


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
class SubProgress:
    """현재 스텝 **안**의 세부 진행 — 마지막 `::rcm::progress::` 마커 하나(release-contract §3).

    `done/total` 은 스크립트가 **아는** 분모만 찍는다(모르면 안 찍는다 — 추정은 화면의 몫).
    `unit` 은 「지금 말하는 것 하나」(청크/플랫폼 · 자식 프로세스 · 락), `state` 는
    `PROGRESS_STATES` 중 하나, `note` 는 자유 문장. `at` 은 서버 수신 시각이다.
    """

    done: int
    total: int
    unit: str
    state: str
    note: str | None
    at: datetime | None = None


@dataclass(frozen=True)
class ProgressUnit:
    """현재 스텝 안에서 본 단위 하나의 **마지막** 상태 — 격자용. 처음 본 순서로 쌓인다."""

    unit: str
    state: str
    note: str | None = None
    at: datetime | None = None


@dataclass(frozen=True)
class Action:
    """「사람이 할 일」 표식 하나 — 마지막 `::rcm::action::<code>::<note>` 마커(M5s 결정 213).

    `code` 는 `[a-z0-9_-]{1,32}`, `note` 는 제어문자를 지운 200자까지의 안내(없으면 None).
    `at` 은 서버가 그 줄을 받은 시각이다. note 는 토큰 없이 읽히는 `/api/status` 와 알림 env 로
    흐르므로 스크립트는 개인정보를 넣지 않는다(템플릿 머리말의 규칙).
    """

    code: str
    note: str | None = None
    at: datetime | None = None


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
    #: 마지막으로 시작한 스텝. 「끝났을 때 어디였나」만 말하고 원인은 주장하지 않는다 (M5h)
    last_step: str | None = None
    #: 잡이 `::rcm::fail::` 로 지목한 이름. 잡이 찍은 순서, 서로 다른 이름만 (M5h)
    fail_names: tuple[str, ...] = ()
    #: MAX_FAIL_NAMES 를 넘겨 버린 이름이 있다 — 공개 JSON 에서는 `failures_truncated` (M5h)
    fail_truncated: bool = False
    summary: str | None = None
    last_output_at: datetime | None = None
    timing: str = "as_received"
    #: 잡이 시작한 시각. 화면이 「도는 잡의 초」를 스스로 세는 기준점이다(M5d-2 §4.6-다).
    started_at: datetime | None = None
    #: 현재 스텝 안의 세부 진행 — 마지막 `::rcm::progress::` 마커. 새 스텝이 시작하면 비운다
    sub: SubProgress | None = None
    #: 현재 스텝 안에서 본 단위들의 마지막 상태(처음 본 순서). MAX_PROGRESS_UNITS 까지만
    units: tuple[ProgressUnit, ...] = ()
    #: 단위 상한을 넘겨 격자에 못 올린 단위가 있다 — `done/total` 은 그래도 계속 센다
    units_truncated: bool = False
    #: 잡이 찍은 마지막 「사람이 할 일」 표식(M5s). 없으면 None
    action: Action | None = None


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
    #: 이 잡이 **지금** 같은 풀의 다른 잡과 한 머신을 나눠 쓰고 있나(M5f). 중앙값은 혼자 잰
    #: 것이라 그동안은 덜 확실하다 — 배수를 지어내지 않고 신뢰도만 한 칸 내린다.
    shared: bool = False
    #: 출력이 조용하다(`no_output_seconds` 초과). **관측이지 경보가 아니다** — `stuck` 과
    #: 절대 동시에 참이 아니고, 취소 중·워크스페이스 준비 중에는 나지 않는다.
    quiet: bool = False
    #: `stuck` 의 근거 — `STUCK_ELAPSED` · `STUCK_STEP` · `STUCK_NO_OUTPUT`. `stuck` 이 아니면 None.
    stuck_code: str | None = None
    #: 현재 단계가 평소 걸리는 시간(초). 그 단계의 실측 중앙값이 없으면 None.
    step_expected_seconds: float | None = None


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
    #: 대기 잡이 들어갈 수 있는 머신과 못 들어가는 이유(M5n §7.3) — 최대 8개
    placement_detail: tuple[PlacementNote, ...] = ()
    #: 이 행의 「사람이 할 일」 표식(M5s). 도는 행은 그 잡의 마지막 표식이다
    action: Action | None = None


@dataclass(frozen=True)
class PlacementNote:
    """대기 잡 하나에서 본 워커 하나의 판정(M5n §7.3 `placement_detail`)."""

    worker: str
    display_name: str
    state: str  # eligible · ineligible · unknown · down
    code: str
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Placement:
    """잡 하나가 들어갈 수 있는 워커들(M5n §7.2). `None` 은 로컬 레인이다.

    `workers` 가 비면 `reason` 이 왜인지 말한다(`no_capable_machine` · `check_failed` ·
    `check_pending`). 비었는데 `reason` 도 없으면 「돌릴 레인이 없다」(`worker_down`)다.
    """

    workers: frozenset[str | None]
    reason: str | None = None
    notes: tuple[PlacementNote, ...] = ()


@dataclass(frozen=True)
class Median:
    seconds: float
    wait_seconds: float | None
    sample_count: int


@dataclass(frozen=True)
class WorkerInfo:
    lane: int
    state: str  # idle | busy | held | down
    job_id: int | None = None
    error: str | None = None
    since: datetime | None = None
    worker: str | None = None  # 원격 워커 이름. 로컬 레인은 None (M5b-2)
    pool: str = DEFAULT_POOL  # 이 레인이 섬기는 풀 (M5b-4: rcm check 가 워커를 풀에 묶는다)
    host_key: str | None = None  # 이 레인이 도는 물리 컴퓨터(M5n §3). 모르면 None
    #: `held` 일 때 **왜**(결정 37) — `cpu_busy` · `no_sample` · `cooldown` (M5f).
    hold_code: str | None = None
    #: 화면이 숫자를 그릴 재료. `cpu_busy` 일 때만 `{"cpu_busy": 92.4}`, 아니면 None.
    hold_detail: dict[str, Any] | None = None
    #: 언제부터 막혔나. 「게이트가 제 일을 하는 중」과 「누가 두 시간째 잡고 있음」을 가른다.
    held_since: datetime | None = None

    @property
    def display_name(self) -> str | None:
        """원격 레인의 표시 이름 `<worker>/<lane>`. 로컬 레인은 None(화면이 `lane N` 으로)."""
        return f"{self.worker}/{self.lane}" if self.worker else None


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
    #: 실패의 **종류**(결정 37). 원문은 `last_error` 에 남는다.
    last_error_code: str | None = None
    sse_connections: int = 0
    snapshot_cache_blobs: int | None = None  # 캐시가 꺼져 있으면 None (M5)
    snapshot_cache_bytes: int | None = None
    notify_failures: int = 0
    #: 능력 보고의 세대(M5n §4.2)와 머신별 요약(JSON 그대로 — `server.machines[]`)
    generation: str | None = None
    machines: tuple[dict[str, Any], ...] = ()


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
    #: 잡이 쓰는 디스크(데이터 디렉터리가 앉은 파일 시스템).
    #: `{used_bytes, free_bytes, total_bytes, path}`. 못 읽으면 None.
    disk: dict[str, Any] | None = None
    gpu_note: str | None = None
    #: GPU 표본이 없는 **이유**(결정 37). `no_sampler` · `no_gpu` · `sampler_failed`.
    gpu_note_code: str | None = None
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
    #: 실패의 **종류**(결정 37). 원문은 위의 `*_error` 에 남는다 — 화면은 종류를 자기 말로 쓰고
    #: 원문은 접어서 보여 준다. 실패가 없으면 둘 다 None.
    queue_error_code: str | None = None
    recent_error_code: str | None = None
    medians_error_code: str | None = None
    hosts_error_code: str | None = None
    #: 예약 잡(M5s) — 승격 시각 순. 큐 표와 따로 간다. 실패는 None + `scheduled_error*`
    scheduled: tuple[Job, ...] | None = ()
    scheduled_error: str | None = None
    scheduled_error_code: str | None = None


@dataclass(frozen=True)
class LocalMachine:
    """이 서버 머신 — 로컬 레인 잡의 `ran_on` 을 계산할 때 쓴다(M5t-a 결정 248 f).

    `name` 은 이 머신의 표시 이름(`gethostname` 의 첫 조각)이고 모르면 None 이다. 지어낸 이름
    (`"host"`)을 넣지 않는다. `host_key` 는 로컬 레인이 claim 때 잡에 박는 값과 같은 것이다 —
    다른 물리 머신에서 돈 로컬 잡(데이터 폴더를 옮겼다)을 이 머신 이름으로 부르지 않으려고 쓴다.
    """

    name: str | None
    host_key: str | None = None


@dataclass(frozen=True)
class OpenHold:
    """열린 그룹 홀드 하나(`irreversible_holds` 행, M5t-b §4.12). 그룹 · 범위 · 풀 · 호스트는 닫는
    트랜잭션이 잡 행에서 복사한 것이다 — 지금 설정을 읽지 않는다(결정 339-d)."""

    job_id: int
    group: str
    scope: str
    pool: str
    host_key: str | None = None
    since: datetime | None = None
    preset: str | None = None
    worker: str | None = None
    cause: str = ""


@dataclass(frozen=True)
class StatusModel:
    generated_at: datetime
    display_timezone: str | None
    server: ServerInfo
    presets: tuple[Preset, ...]
    pools: tuple[Pool, ...]
    base_url: str | None = None
    #: 이 서버 머신(M5t-a). 없으면 로컬 레인 잡의 `ran_on` 은 null 이다
    local_machine: LocalMachine | None = None
    #: 열린 그룹 홀드(M5t-b §4.8) — `/api/status` 의 `irreversible_holds`. 없으면 빈 튜플,
    #: 못 읽었으면 None(모름)
    irreversible_holds: tuple[OpenHold, ...] | None = ()
