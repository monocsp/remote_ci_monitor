"""서버·클라이언트 설정 로딩. 우선순위 플래그 > 환경변수(`RCM_<섹션>_<키>`) > 파일 > 기본값.

오류는 시작 시 **섹션·키 이름과 함께** `ConfigError` 로 실패한다. 조용히 기본값으로
떨어지지 않는다(PLAN.md 「설정」). 프리셋 검증도 여기서 한다 — 모르는 키, 빈 argv,
choices 없는 choice 입력은 프리셋 이름과 키 이름을 찍고 실패한다.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
import zoneinfo
from collections.abc import Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from remote_ci_monitor.core.artifacts import (
    COLLECT_ALWAYS,
    COLLECT_ON,
    PolicyError,
    validate_globs,
)
from remote_ci_monitor.core.gitref import validate_repo_url
from remote_ci_monitor.core.model import (
    DEFAULT_POOL,
    INPUT_TYPES,
    MODE_GIT_REF,
    NOTIFY_DEFAULT_STATES,
    PLACEMENT_CAPABLE,
    PLACEMENT_POOL,
    PRIORITY_NAMES,
    SCOPE_POOL,
    SCOPES,
    SOURCE_MODES,
    TERMINAL_STATES,
    InputSpec,
    Preset,
    RetryPolicy,
)
from remote_ci_monitor.core.notify import NotifyRule
from remote_ci_monitor.core.supersede import SUPERSEDE_MODES, SUPERSEDE_OFF

ENV_PREFIX = "RCM"
DEFAULT_DATA_DIR = "~/.local/share/rcm"
SERVER_CONFIG_CANDIDATES = ("./rcm.toml", "~/.config/rcm/server.toml")
CLIENT_CONFIG_CANDIDATES = ("~/.config/rcm/client.toml",)
LOOPBACK_BINDS = ("127.0.0.1", "localhost", "::1")  # 여기 묶이면 다른 머신은 못 붙는다


def user_config_dir() -> Path:
    """`$XDG_CONFIG_HOME/rcm` 또는 `~/.config/rcm` — `rcm init` 이 쓰고 탐색이 먼저 본다."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "rcm"


def _candidates(kind: str, static: tuple[str, ...]) -> list[Path]:
    """고정 후보 순서는 그대로, `~/.config/rcm` 바로 앞에 `$XDG_CONFIG_HOME/rcm` 을 끼운다.

    `./rcm.toml` 이 사용자 설정보다 앞서는 기존 순서(PLAN 「설정」)는 바뀌지 않는다. XDG 가 없거나
    `~/.config` 이면 두 경로가 같아 한 번만 본다.
    """
    legacy = Path("~/.config/rcm").expanduser() / f"{kind}.toml"
    xdg = user_config_dir() / f"{kind}.toml"
    out: list[Path] = []
    for cand in static:
        p = Path(cand).expanduser()
        if p == legacy and xdg not in out:
            out.append(xdg)
        if p not in out:
            out.append(p)
    return out


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ConfigError(ValueError):
    """설정이 잘못됐다. 메시지에 섹션·키 이름이 들어간다."""


# ── 섹션 dataclass. 기본값이 곧 스키마(타입)다 ───────────────────────────────


@dataclass
class ServerSection:
    bind: str = "127.0.0.1"
    port: int = 8787
    data_dir: str = DEFAULT_DATA_DIR
    lanes: int = 1
    # 부하 게이트(M5f) — 레인 2 부터는 호스트가 한가할 때만 잡는다. 레인 1 은 안 지난다.
    admission: str = "load"  # "load" | "always"("always" 는 M5f 이전 동작)
    cpu_max_percent: float = 80.0  # 백분율은 소수가 될 수 있다 — `cpu.busy` 도 float 다
    admission_samples: int = 3  # 연속으로 이만큼의 표본이 전부 기준 아래여야 연다
    admission_cooldown_seconds: int = 30  # 한 머신에서 게이트를 지나 잡으면 이만큼 쉰다
    read_auth: str = "none"
    max_snapshot_bytes: int = 536_870_912
    max_concurrent_requests: int = 32
    join_duplicates: bool = True
    grace_seconds: int = 10
    retention_days_success: int = 14
    retention_days_failure: int = 30
    keep_workspace_on_failure: bool = True
    # 부피(워크스페이스 + 그 잡의 스냅샷 tar)는 증거(로그)와 **다른 시계로 잔다**(M5g).
    # 실패 잡 하나가 로그 50 KB · 워크스페이스 720 MB 를 남긴다 — 같은 기간을 줄 이유가 없다.
    workspace_retention_days: int = 1  # <= min(retention_days_success, retention_days_failure)
    workspace_storage_max_bytes: int = 107_374_182_400  # 100 GiB. 0 = 무제한
    min_free_bytes: int = 10_737_418_240  # 파일 시스템 여유 바닥 10 GiB. 0 = 안 본다
    recent_count: int = 8
    # 실패 이름의 최근 이력(M5h). 창은 같은 key 의 **이 잡까지** 최근 종료 잡 수다.
    failure_window_jobs: int = 20
    failure_min_jobs: int = 3  # 창이 이보다 얕으면 판정하지 않는다(unknown)
    upload_stall_seconds: int = 60
    upload_abandon_seconds: int = 300
    sse_max_connections: int = 16
    sse_keepalive_seconds: int = 15
    public_url: str = ""
    git_resolve_timeout_seconds: int = 20  # 제출 시 ls-remote 상한
    git_fetch_timeout_seconds: int = 600  # 자재화(fetch · clone) 상한
    retention_sweep_interval_seconds: int = 3600  # janitor 주기(하한 60)
    metadata_retention_days: int = (
        180  # 잡 행·이벤트 삭제. sample_days · retention_days_failure 이상
    )
    snapshot_cache: bool = True  # 내용 주소 스냅샷 캐시 (M5)
    snapshot_cache_days: int = 30  # 이만큼 안 쓰인 blob 은 지운다
    snapshot_cache_max_bytes: int = 4 * 1024**3  # 넘으면 오래된 blob 부터
    snapshot_cache_scope: str = "global"  # "global" | "token" — token 이면 토큰별로 blob 을 나눈다
    worker_timeout_seconds: int = 60  # heartbeat 이 이만큼 없으면 워커 down · 잡 lost (M5b-2)
    worker_heartbeat_seconds: int = 5  # 워커에게 알려 주는 heartbeat 주기
    worker_claim_wait_seconds: int = 20  # `/worker/claim` long-poll 상한
    # 잡 산출물(M5e). 전부 설정 키다 — 기본값만 결정 41 이고 운영자가 바꾼다.
    artifact_retention_hours: int = 24  # TTL. `ready` 가 된 시각부터 잰다
    max_artifact_bytes: int = 1_073_741_824  # 잡당 원본 바이트 상한 (1 GiB)
    max_artifact_files: int = 10_000  # 잡당 파일 수(바이트와 따로 건다)
    artifact_storage_max_bytes: int = 10 * 1024**3  # 서버 전체(발행 + 예약 + 스테이징)
    artifact_timeout_seconds: int = 60  # 수집·검증·설치 예산
    artifact_cancel_timeout_seconds: int = 5  # 취소·타임아웃 뒤 예산. 2 × heartbeat 미만
    artifact_transfer_timeout_seconds: int = 300  # 업로드·다운로드 한 건의 시한
    max_concurrent_artifact_transfers: int = 2  # 동시 전송 슬롯. 기다리지 않고 503
    advertise: bool | None = None  # mDNS 광고(M5c). None = bind 가 루프백이 아니면 켠다
    advertise_name: str = ""  # 발견 응답의 이름. 비면 짧은 호스트명
    # 취소에 제출 capability 를 요구한다(M5j G5 · 결정 87). 켜면 `POST /jobs` 가 준 cancel token
    # 또는 admin 토큰만 취소한다 — 0.2.7 전 클라이언트의 `rcm cancel` 은 403 이다. 기본은 off.
    cancel_requires_submission_token: bool = False
    # M5n — 머신의 능력(§4 · §11.2)
    probe_interval_seconds: int = 600  # 지문을 다시 재는 주기(하한 60). 보고 신선도는 이 값의 3배
    auto_checks: bool = True  # 지문이 바뀐 워커에 점검 프리셋을 rcm 이 스스로 한 번 건다
    local_accept_any: bool = True  # 서버의 로컬 레인이 `any-capable` 잡을 집나
    check_sweep_seconds: int = 30  # 자동 점검 스위퍼 주기
    # M5p — 보관함(§3.1). 재시작해야 바뀐다(`[server]` 는 reload 되지 않는다)
    shelf_dir: str = ""  # 비면 `<data_dir>/shelf`. 절대 경로 · `~` 를 받고 data_dir 밖이어도 된다
    shelf_max_bytes: int = 50 * 1024**3  # 보관함 전체 상한(50 GiB). 넘으면 새 보관을 거절. 0 = 꺼짐
    shelf_stale_days: int = 182  # 마지막으로 쓴 지 이만큼이면 「정리 대기」
    shelf_delete_days: int = 365  # 마지막으로 쓴 지 이만큼이면 삭제. stale 보다 커야 한다
    # M5s — 예약 실행(§4.1 · 결정 207 · 208). 재시작해야 바뀐다(`[server]` 는 reload 되지 않는다)
    schedule_start_deadline_seconds: int = 900  # 정한 시각 + 이만큼 안에 못 집히면 expired
    schedule_max_ahead_days: int = 14  # 이보다 먼 시각은 400 — 잊힌 예약이 쌓이지 않게


@dataclass(frozen=True)
class Probe:
    """`[[probes]]` 하나 — 각 워커가 자기 서비스 환경에서 돌려 지문을 만든다(M5n §4.1)."""

    name: str
    argv: tuple[str, ...]
    timeout_seconds: int = 10


@dataclass(frozen=True)
class ToolHint:
    """`[[tools]]` 하나 — 도구 이름에 묶인 설치 안내(M5t-a 결정 246). 표시 전용이라 rcm 은 이
    문자열을 실행하지 않고 sudo 로 설치하지도 않는다. `install` 의 키는 `TOOL_OS_KEYS` 다."""

    name: str
    docs: str = ""
    install: dict[str, str] = field(default_factory=dict)


@dataclass
class EstimateSection:
    sample_days: int = 45
    min_samples: int = 2
    min_job_seconds: int = 30
    sample_policy: str = "success"
    default_seconds: int = 600
    floor_remaining_seconds: int = 30
    stuck_multiplier: float = 3.0
    no_output_seconds: int = 240
    # 현재 단계가 자기 실측 중앙값의 몇 배를 넘으면 「응답 없음」인가. 판정은 단계 실측이
    # 먼저고, 단계 이야기를 안 하는 잡만 `no_output_seconds` 로 떨어진다.
    step_stuck_multiplier: float = 3.0
    # 단계 중앙값을 믿기 시작하는 실행 수. 단계 소요는 잡 소요보다 시끄러워 `min_samples`(2)
    # 보다 높다 — 3 이면 중앙값이 한 번의 이상치에 끌려가지 않는다.
    step_min_samples: int = 3


@dataclass
class HostSection:
    interval_seconds: int = 5
    gpu: str = "auto"
    top_processes: int = 5
    history_samples: int = 60


@dataclass
class DisplaySection:
    timezone: str = ""


# ── 릴리스 프로파일(스토어 탭) — docs/release-contract.md §1 ─────────────────

#: 역할 이름. 앞 셋만 필수이고 나머지는 선택이다. `status`(두 스토어의 심사 상태)와 `golive`
#: (사람이 요청한 출시)는 M5s §4.4 · §4.5 에서 더했다.
RELEASE_ROLES = ("plan", "upload", "review", "gate", "qa", "dev", "version", "status", "golive")
RELEASE_REQUIRED_ROLES = ("plan", "upload", "review")
RELEASE_SECRET_KINDS = ("value", "file", "dir")
RELEASE_VERIFY_KINDS = ("asc", "play", "github", "keystore", "none")
RELEASE_BUILD_NUMBER_POLICIES = ("auto", "manual")
_SECRETS_DIR_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_RELEASE_KEYS = {
    "default_branch",
    "tag",
    "build_number_policy",
    "plan_max_age_minutes",
    "version_ttl_hours",
    "driver",
    "secrets_dir_env",
    "presets",
    "secrets",
    "listing",
}
_RELEASE_SECRET_KEYS = {"name", "kind", "optional", "verify", "files", "max_kb"}
_RELEASE_LISTING_KEYS = {"preview", "diff", "validate", "screenshots", "release_notes", "ref"}


@dataclass(frozen=True)
class ReleaseSecret:
    """`[[repos.<name>.release.secrets]]` 하나 — 값이 아니라 **이름과 모양**만. 값은 설정에 없다."""

    name: str
    kind: str = "value"  # "value" | "file" | "dir"
    optional: bool = False
    verify: str = "none"  # "asc" | "play" | "github" | "keystore" | "none"
    files: tuple[str, ...] = ()  # kind = "dir" 일 때 안에 있어야 하는 파일 이름
    max_kb: int = 512  # kind = "file" 일 때 상한


@dataclass(frozen=True)
class ReleaseListing:
    """`[repos.<name>.release.listing]` — 스토어 문안을 미리 보는 명령과 글롭. 전부 선택."""

    preview: tuple[str, ...] = ()
    diff: tuple[str, ...] = ()
    validate: tuple[str, ...] = ()
    screenshots: tuple[str, ...] = ()
    release_notes: str = ""
    # 소개 자료를 읽을 브랜치 — 없으면 default_branch. dev → main 으로 머지해 내보내는 프로젝트는
    # 릴리스에 실릴 문안이 dev 에 있으므로 "dev" 로 둔다.
    ref: str | None = None


@dataclass(frozen=True)
class ReleaseProfile:
    """`[repos.<name>.release]` — 어느 프리셋이 어느 역할인지, 어떤 비밀이 있는지.

    여기서는 **모양**(타입 · 허용값 · 모르는 키)만 본다. 프리셋이 실제로 있는지, 되돌릴 수 없는
    모드가 기본값은 아닌지 같은 교차 검사는 `rcm check` 의 `release <repo>` 행이 한다 — 빠진
    역할은 페이지를 흐리게 할 뿐 서버를 못 뜨게 하지 않는다(계약 「Anything missing degrades
    the page, never the server」).
    """

    presets: dict[str, str] = field(default_factory=dict)  # 역할 → 프리셋 이름
    default_branch: str = "main"
    tag: str = "prod/{version}-{build}"
    build_number_policy: str = "auto"  # "auto" | "manual"
    plan_max_age_minutes: int = 30
    # 새 버전 드래프트가 편집 없이 살아 있는 시간 — 지나면 청소기가 버린다(버전 페이지 계획 Q1).
    version_ttl_hours: int = 24
    driver: str | None = None  # 저장소 안 상대경로
    secrets_dir_env: str | None = None
    secrets: tuple[ReleaseSecret, ...] = ()
    listing: ReleaseListing | None = None

    def preset_for(self, role: str) -> str | None:
        return self.presets.get(role) or None


@dataclass
class RepoConfig:
    name: str
    url: str
    release: ReleaseProfile | None = None  # 스토어 탭 프로파일. None = 이 저장소엔 탭이 없다
    # 이 저장소만의 https 자격(토큰 한 줄이 든 파일). 없으면 머신의 git 자격(전역 헬퍼)을 쓴다.
    # 쓴 대로(`~` 포함) 둔다 — 이 설정을 읽는 머신의 HOME 으로 푼다(원격 워커는 자기 머신 것).
    token_file: str | None = None

    @property
    def credential(self) -> Path | None:
        """git 에 넘길 토큰 파일 경로(`~` 를 푼). `token_file` 이 없으면 None."""
        return Path(self.token_file).expanduser() if self.token_file else None


@dataclass
class ServerConfig:
    server: ServerSection = field(default_factory=ServerSection)
    estimate: EstimateSection = field(default_factory=EstimateSection)
    host: HostSection = field(default_factory=HostSection)
    display: DisplaySection = field(default_factory=DisplaySection)
    repos: tuple[RepoConfig, ...] = ()
    presets: tuple[Preset, ...] = ()
    notify: tuple[NotifyRule, ...] = ()
    probes: tuple[Probe, ...] = ()
    #: `[[tools]]` — doctor 가 빠진 도구 옆에 찍는 설치 안내. `/worker/spec` 으로만 나간다
    tools: tuple[ToolHint, ...] = ()
    path: Path | None = None
    #: `include = [...]` 로 들인 파일들, 적힌 순서대로(M5o 결정 121). 경로는 펼친 절대 경로
    includes: tuple[Path, ...] = ()
    #: 프리셋 이름 → 그것을 정의한 파일. `Preset` 에 넣지 않는 이유: reload 는 프리셋을 `!=` 로
    #: 비교한다 — 같은 정의를 다른 파일로 옮긴 것이 「바뀜」으로 보이면 안 된다
    preset_sources: dict[str, Path] = field(default_factory=dict, compare=False)

    @property
    def data_dir(self) -> Path:
        return Path(self.server.data_dir).expanduser()

    @property
    def shelf_dir(self) -> Path:
        """보관함 폴더(M5p §3.1). 비워 두면 `data_dir` 을 따라간다(환경 변수로 바꾼 것까지)."""
        if self.server.shelf_dir:
            return Path(self.server.shelf_dir).expanduser()
        return self.data_dir / "shelf"

    @property
    def shelf_max_bytes(self) -> int:
        return self.server.shelf_max_bytes

    @property
    def shelf_stale_days(self) -> int:
        return self.server.shelf_stale_days

    @property
    def shelf_delete_days(self) -> int:
        return self.server.shelf_delete_days

    @property
    def shelf_enabled(self) -> bool:
        """새 보관을 받나. `shelf_max_bytes = 0` 이면 꺼진다.

        꺼져도 기존 항목은 그대로 보이고 받을 수 있으며 시계대로 정리된다(§3.1)."""
        return self.server.shelf_max_bytes > 0

    def preset(self, name: str) -> Preset | None:
        for p in self.presets:
            if p.name == name:
                return p
        return None

    def repo(self, name: str | None) -> RepoConfig | None:
        for r in self.repos:
            if r.name == name:
                return r
        return None


@dataclass
class ClientConfig:
    server: str = ""
    token: str = ""
    label: str = ""
    token_env: str = "RCM_TOKEN"  # 토큰을 찾은/찾을 환경변수 이름 — 안내 문구에 쓴다
    path: Path | None = None

    @property
    def wants_discovery(self) -> bool:
        """서버 주소가 없거나 `"auto"` 면 같은 네트워크에서 찾는다(M5c)."""
        return self.server.strip().lower() in ("", "auto")


def advertise_enabled(section: ServerSection) -> bool:
    """mDNS 광고를 켤까 — 명시값이 있으면 그것, 없으면 bind 가 루프백이 아닐 때만."""
    if section.advertise is not None:
        return bool(section.advertise)
    return section.bind not in LOOPBACK_BINDS


def advertise_warning(section: ServerSection) -> str | None:
    """`advertise = true` 인데 bind 가 루프백이면 서버 로그 한 줄 — 명시값은 존중해 광고하지만
    다른 머신은 발견만 되고 못 붙는다(실기: `rcm discover` 에 뜨고 connection refused).
    아니면 None."""
    if advertise_enabled(section) and section.bind in LOOPBACK_BINDS:
        return (
            f'warning: advertise is on but bind = "{section.bind}" — other machines will find '
            'this server but cannot connect (set bind = "0.0.0.0" or a LAN/Tailscale IP)'
        )
    return None


#: 「아무 주소에나 묶어라」는 bind 값. 듣기에는 맞지만 링크에 넣으면 다른 기기에서 안 열린다.
ANY_BINDS = ("0.0.0.0", "::", "")


def server_base_url(section: ServerSection, *, hostname: str) -> str:
    """잡 링크의 앞부분 — 알림(`notify.py`)과 서버(`server.py`)가 같이 쓴다(M5o 결정 120).

    1. `public_url` 이 있으면 그것.
    2. `bind` 가 특정 주소면 `http://<bind>:<port>`(IPv6 는 대괄호).
    3. `bind` 가 `0.0.0.0` · `::` · 빈 값이고 mDNS 광고가 켜져 있으면
       `http://<짧은 호스트>.local:<port>` — 서버의 Responder 가 그 이름의 A 레코드에 답한다
       (`server.py` 의 `f"{host}.local."`).
    4. 그 밖이면 `http://<hostname>:<port>`.

    `0.0.0.0` · `::` 은 **어느 갈래에서도** 나오지 않는다 — 운영 알림 3,721건이 그 주소를
    실어 보냈다.
    호스트 이름을 모르면 `localhost` 다(틀릴 수는 있어도 「아무 주소」보다는 열린다).
    """
    if section.public_url:
        return section.public_url.rstrip("/")
    bind = (section.bind or "").strip()
    port = section.port
    if bind not in ANY_BINDS:
        host = f"[{bind}]" if ":" in bind and not bind.startswith("[") else bind
        return f"http://{host}:{port}"
    name = (hostname or "").strip().rstrip(".")
    if advertise_enabled(section):
        short = name.split(".")[0]
        return f"http://{short}.local:{port}" if short else f"http://localhost:{port}"
    return f"http://{name or 'localhost'}:{port}"


def shorter_log_retention(section: ServerSection) -> tuple[str, int]:
    """부피가 넘을 수 없는 천장 — 두 로그 보존 기간 중 **짧은 쪽**(키 이름과 값)."""
    keys = ("retention_days_success", "retention_days_failure")
    key = min(keys, key=lambda k: getattr(section, k))
    return key, getattr(section, key)


def effective_workspace_retention_days(section: ServerSection) -> int:
    """실제로 지켜지는 부피 보존 일수. 로그보다 길게 줘도 잡 디렉터리 청소가 함께 가져간다."""
    return min(section.workspace_retention_days, shorter_log_retention(section)[1])


def retention_warning(section: ServerSection) -> str | None:
    """부피 보존이 로그 보존보다 길면 서버 로그 한 줄. **오류가 아니다.**

    오류로 만들면 `retention_days_success = 0` 을 쓰던 설치가 **업그레이드만으로 안 뜬다** —
    그 사람은 새 키를 만진 적이 없는데 기본값(1) 때문에 걸린다(M5f 가 같은 함정을 이미 겪었다).
    대신 실제 동작을 `effective_workspace_retention_days` 로 낮추고 그 사실을 말한다.
    """
    key, ceiling = shorter_log_retention(section)
    if section.workspace_retention_days <= ceiling:
        return None
    return (
        f"warning: [server] workspace_retention_days ({section.workspace_retention_days}) is more "
        f"than {key} ({ceiling}) — a job's whole directory goes at {ceiling}d, so the workspace "
        f"cannot outlive it; using {ceiling}d"
    )


def supersede_skipped_presets(cfg: ServerConfig) -> list[str]:
    """`supersede` 를 켰지만 서버가 건너뛸 프리셋 이름(M5o §10).

    `cancel_requires_submission_token = true` 인 서버에서는 세션이 남의 잡을 그 잡의 취소
    비밀 없이 못 취소한다. supersede 는 토큰 **이름**만 보고 취소하므로 그 약속을 우회한다 —
    그래서 그 설정이 켜져 있으면 supersede 는 아무것도 취소하지 않는다. 설정 오류는 아니다
    (경고): 업그레이드만으로 서버가 안 뜨면 안 된다.
    """
    if not cfg.server.cancel_requires_submission_token:
        return []
    return [p.name for p in cfg.presets if p.supersede != SUPERSEDE_OFF]


def supersede_warnings(cfg: ServerConfig) -> list[str]:
    names = supersede_skipped_presets(cfg)
    if not names:
        return []
    return [
        f"warning: preset(s) {', '.join(names)} set supersede but [server] "
        "cancel_requires_submission_token = true — supersede is skipped (it would cancel "
        "another session's job without its cancel token)"
    ]


def admission_warnings(server: ServerSection, host: HostSection) -> list[str]:
    """게이트 상수가 서로 안 맞을 때의 **경고**(M5f §4.1). 오류가 아니다.

    오류로 만들면 **업그레이드만으로 돌던 서버가 안 뜬다** — `[host] history_samples = 2` 나
    `interval_seconds = 60` 을 쓰던 사람은 admission 키를 만진 적이 없는데 기본값 때문에 걸린다.
    게이트는 런타임에 이미 옳게 닫히므로(`no_sample`) 경고로 충분하다.
    """
    if server.admission != "load" or server.lanes < 2:
        return []
    out: list[str] = []
    if server.admission_samples > host.history_samples:
        out.append(
            f"warning: [server] admission_samples ({server.admission_samples}) is more than "
            f"[host] history_samples ({host.history_samples}) — lanes 2+ will never open"
        )
    window = server.admission_samples * host.interval_seconds
    if window > server.admission_cooldown_seconds:
        out.append(
            f"warning: [server] admission_samples * [host] interval_seconds ({window}s) exceeds "
            f"admission_cooldown_seconds ({server.admission_cooldown_seconds}s) — the two "
            "constants no longer relate"
        )
    return out


@dataclass
class WorkerConfig:
    """`rcm worker` 설정(M5b-3). 토큰은 env `RCM_WORKER_TOKEN` 또는 파일 `token` 으로만."""

    server: str = ""
    token: str = ""
    pool: str = DEFAULT_POOL
    lanes: int = 1
    name: str = ""  # 표시용. 서버가 아는 이름은 토큰 이름이다
    data_dir: str = "~/.local/share/rcm-worker"
    grace_seconds: int = 10
    keep_workspace_on_failure: bool = True
    git_fetch_timeout_seconds: int = 600
    #: 같은 망에서 여러 서버가 보일 때 고를 광고 이름(M5n §9). 비면 하나뿐일 때만 자동으로 고른다
    server_name: str = ""
    #: `any-capable` 잡을 받나(M5n §5.1). 장치 실험실처럼 전용으로 둘 머신은 false
    accept_any: bool = True
    host: HostSection = field(default_factory=HostSection)
    repos: tuple[RepoConfig, ...] = ()
    path: Path | None = None

    @property
    def wants_discovery(self) -> bool:
        """서버 주소가 없거나 `"auto"` 면 같은 망에서 찾는다(M5n §9 — 클라이언트의 M5c 와 같다)."""
        return self.server.strip().lower() in ("", "auto")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    def repo(self, name: str | None) -> RepoConfig | None:
        for r in self.repos:
            if r.name == name:
                return r
        return None


# ── 도우미 ───────────────────────────────────────────────────────────────────


def _coerce_scalar(where: str, value: Any, kind: type) -> Any:
    """섹션 dataclass 의 기본값 타입으로 값을 맞춘다. 환경변수는 문자열이라 파싱이 필요하다."""
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
            return False
        raise ConfigError(f"{where}: expected true/false, got {value!r}")
    if kind is int:
        if isinstance(value, bool):
            raise ConfigError(f"{where}: expected an integer, got {value!r}")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError as e:
                raise ConfigError(f"{where}: expected an integer, got {value!r}") from e
        raise ConfigError(f"{where}: expected an integer, got {value!r}")
    if kind is float:
        if isinstance(value, bool):
            raise ConfigError(f"{where}: expected a number, got {value!r}")
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError as e:
                raise ConfigError(f"{where}: expected a number, got {value!r}") from e
        raise ConfigError(f"{where}: expected a number, got {value!r}")
    if kind is str:
        if isinstance(value, str):
            return value
        raise ConfigError(f"{where}: expected a string, got {type(value).__name__}")
    raise ConfigError(f"{where}: unsupported type {kind.__name__}")


def _apply_section(section: Any, name: str, values: dict[str, Any], origin: str) -> None:
    kinds = {f.name: type(getattr(section, f.name)) for f in fields(section)}
    for key, kind in list(kinds.items()):
        if kind is type(None):  # `bool | None`(advertise) — 값이 오면 bool 로 읽는다
            kinds[key] = bool
    for key, value in values.items():
        if key not in kinds:
            raise ConfigError(f"[{name}] unknown key '{key}' ({origin})")
        setattr(section, key, _coerce_scalar(f"[{name}] {key} ({origin})", value, kinds[key]))


def _env_overrides(name: str, section: Any) -> dict[str, str]:
    prefix = f"{ENV_PREFIX}_{name.upper()}_"
    out: dict[str, str] = {}
    for f in fields(section):
        env_key = prefix + f.name.upper()
        if env_key in os.environ:
            out[f.name] = os.environ[env_key]
    return out


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except UnicodeDecodeError as e:
        # `tomllib.load` 은 먼저 UTF-8 로 푼다 — 그 오류는 TOMLDecodeError 가 아니다
        raise ConfigError(f"{path}: not UTF-8 (byte {e.start})") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from e
    except OSError as e:
        raise ConfigError(f"{path}: cannot read: {e.strerror}") from e


def find_server_config(explicit: str | os.PathLike[str] | None) -> Path | None:
    """탐색 순서: `--config` → `$RCM_CONFIG` → `./rcm.toml` → `$XDG_CONFIG_HOME/rcm/server.toml`
    → `~/.config/rcm/server.toml`."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        return p
    env = os.environ.get("RCM_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise ConfigError(f"$RCM_CONFIG points to a missing file: {p}")
        return p
    for p in _candidates("server", SERVER_CONFIG_CANDIDATES):
        if p.is_file():
            return p
    return None


# ── 프리셋 ───────────────────────────────────────────────────────────────────

_PRESET_KEYS = {
    "name",
    "description",
    "argv",
    "timeout_seconds",
    "source_modes",
    "repo",
    "priority",
    "pool",
    "pools",
    "concurrency_group",
    "expected_seconds",
    "duration_key_inputs",
    "env_passthrough",
    "env",
    "requires",
    "inputs",
    "artifacts",
    "artifacts_on",
    "placement",
    "concurrency_scope",
    "checks",
    "check_ref",
    "sample",
    "supersede",
    "keep",
    "start_deadline_seconds",
    "retry",
    "local_only",
}
#: `[presets.retry]` 의 키(M5s 결정 210). 값을 안 적으면 요청서의 기본값이다(75 · 600 · 12)
_RETRY_KEYS = {"on_exit", "interval_seconds", "max_attempts"}
#: 시도 수 상한 — 실수로 큰 수를 적으면 사슬이 며칠을 돈다
MAX_RETRY_ATTEMPTS = 100
#: 설정 문자열 → DB 값(M5n 결정 97). DB 값 `"capable"` 을 설정에 쓰면 오타로 본다.
_PLACEMENT_VALUES = {"pool": PLACEMENT_POOL, "any-capable": PLACEMENT_CAPABLE}
_INPUT_KEYS = {"name", "type", "choices", "default", "pattern", "description"}


def _parse_requires(where: str, value: Any) -> tuple[str, ...]:
    """`requires` — 도구 이름 또는 절대경로만(M5j G4). 상대경로는 어느 cwd 에서 찾을지 정해져
    있지 않고, 빈 값·중복은 오타다. 절대경로는 도구 이름으로 끝나야 한다(`/opt/bin/`·`/` 처럼
    basename 이 비면 공개 이름이 경로로 물러난다 — M5l S2). 오류에 프리셋과 키 이름을 넣는다."""
    names = _str_list(where, value, allow_empty=True)
    seen: set[str] = set()
    for name in names:
        if not name.strip():
            raise ConfigError(f"{where}: entries must not be empty")
        if "/" in name and not name.startswith("/"):
            raise ConfigError(
                f"{where}: entries must be a tool name or an absolute path, got {name!r}"
            )
        if "/" in name and os.path.basename(name) in ("", ".", ".."):
            raise ConfigError(f"{where}: an absolute path must end in a tool name, got {name!r}")
        if name in seen:
            raise ConfigError(f"{where}: duplicate entry {name!r}")
        seen.add(name)
    return names


def _str_list(where: str, value: Any, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: expected a list of strings")
    if not allow_empty and not value:
        raise ConfigError(f"{where}: must not be empty")
    return tuple(value)


def _parse_input(preset_name: str, raw: Any) -> InputSpec:
    where = f"preset '{preset_name}' inputs"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each input must be a table")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(f"{where}: input 'name' must be a short identifier, got {name!r}")
    where = f"preset '{preset_name}' input '{name}'"
    unknown = sorted(set(raw) - _INPUT_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    kind = raw.get("type", "string")
    if kind not in INPUT_TYPES:
        raise ConfigError(f"{where}: type must be one of {', '.join(INPUT_TYPES)}, got {kind!r}")
    choices: tuple[str, ...] = ()
    if kind == "choice":
        if "choices" not in raw:
            raise ConfigError(f"{where}: type 'choice' requires 'choices'")
        choices = _str_list(f"{where} choices", raw["choices"], allow_empty=False)
    elif "choices" in raw:
        raise ConfigError(f"{where}: 'choices' is only valid for type 'choice'")
    pattern = raw.get("pattern")
    if pattern is not None:
        if kind != "string":
            raise ConfigError(f"{where}: 'pattern' is only valid for type 'string'")
        if not isinstance(pattern, str):
            raise ConfigError(f"{where}: 'pattern' must be a string")
        try:
            re.compile(pattern)
        except re.error as e:
            raise ConfigError(f"{where}: invalid pattern: {e}") from e
    default = raw.get("default")
    if default is not None:
        expected = {"string": str, "choice": str, "bool": bool, "int": int}[kind]
        if isinstance(default, bool) and expected is not bool:
            raise ConfigError(f"{where}: default must be a {expected.__name__}")
        if not isinstance(default, expected):
            raise ConfigError(f"{where}: default must be a {expected.__name__}")
        if kind == "choice" and default not in choices:
            raise ConfigError(f"{where}: default {default!r} is not in choices")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ConfigError(f"{where}: description must be a string")
    return InputSpec(
        name=name,
        type=kind,
        choices=choices,
        default=default,
        pattern=pattern,
        description=description,
    )


def parse_priority(value: Any, where: str) -> int:
    """`"high"|"normal"|"low"` → 1·0·-1. 숫자·대문자·다른 말은 키 이름을 찍고 실패."""
    if isinstance(value, str) and value in PRIORITY_NAMES:
        return PRIORITY_NAMES[value]
    raise ConfigError(f'{where}: priority must be "low", "normal" or "high", got {value!r}')


_NOTIFY_KEYS = {"name", "on", "presets", "argv", "url", "timeout_seconds"}


def parse_notify(raw: Any) -> NotifyRule:
    """`[[notify]]` 하나. argv 와 url 중 정확히 하나. 오류에 규칙 이름을 넣는다."""
    if not isinstance(raw, dict):
        raise ConfigError("[[notify]]: each rule must be a table")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(f"[[notify]]: 'name' must be a short identifier, got {name!r}")
    where = f"notify '{name}'"
    unknown = sorted(set(raw) - _NOTIFY_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    on_raw = raw.get("on")
    if on_raw is None:
        on = frozenset(NOTIFY_DEFAULT_STATES)
    else:
        on_list = _str_list(f"{where} on", on_raw, allow_empty=False)
        bad = [st for st in on_list if st not in TERMINAL_STATES]
        if bad:
            raise ConfigError(f"{where}: on must be terminal states, got {bad}")
        on = frozenset(on_list)
    presets_raw = raw.get("presets")
    presets = (
        None
        if presets_raw is None
        else frozenset(_str_list(f"{where} presets", presets_raw, allow_empty=False))
    )
    argv_raw = raw.get("argv")
    url = raw.get("url")
    if (argv_raw is None) == (url is None):
        raise ConfigError(f"{where}: exactly one of argv or url is required")
    argv = None if argv_raw is None else _str_list(f"{where} argv", argv_raw, allow_empty=False)
    if url is not None and (
        not isinstance(url, str) or not url.startswith(("http://", "https://"))
    ):
        raise ConfigError(f"{where}: url must start with http:// or https://")
    timeout = raw.get("timeout_seconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise ConfigError(f"{where}: timeout_seconds must be an integer >= 1")
    return NotifyRule(
        name=name,
        on=on,
        presets=presets,
        argv=None if argv is None else tuple(argv),
        url=url,
        timeout_seconds=timeout,
    )


def parse_preset(raw: Any) -> Preset:
    """`[[presets]]` 테이블 하나를 검증해 Preset 으로. 오류에 프리셋·키 이름을 넣는다."""
    if not isinstance(raw, dict):
        raise ConfigError("[[presets]]: each preset must be a table")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(f"[[presets]]: 'name' must be a short identifier, got {name!r}")
    where = f"preset '{name}'"
    unknown = sorted(set(raw) - _PRESET_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    if "argv" not in raw:
        raise ConfigError(f"{where}: 'argv' is required")
    argv = _str_list(f"{where} argv", raw["argv"], allow_empty=False)
    defaults = Preset(name=name, argv=argv)
    timeout = raw.get("timeout_seconds", defaults.timeout_seconds)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ConfigError(f"{where}: timeout_seconds must be a positive integer")
    modes = tuple(raw.get("source_modes", list(defaults.source_modes)))
    modes = _str_list(f"{where} source_modes", list(modes), allow_empty=False)
    bad = [m for m in modes if m not in SOURCE_MODES]
    if bad:
        raise ConfigError(f"{where}: source_modes must be from {SOURCE_MODES}, got {bad}")
    repo = raw.get("repo", "")
    if not isinstance(repo, str):
        raise ConfigError(f"{where}: repo must be a string")
    if repo and "git_ref" not in modes:
        raise ConfigError(f"{where}: repo is only valid with source_modes git_ref")
    group = raw.get("concurrency_group", "")
    if not isinstance(group, str):
        raise ConfigError(f"{where}: concurrency_group must be a string")
    priority = parse_priority(raw.get("priority", "normal"), where)
    pool = raw.get("pool", "default")
    if not isinstance(pool, str) or not _NAME_RE.match(pool):
        raise ConfigError(f"{where}: pool must be a short identifier, got {pool!r}")
    pools = _str_list(f"{where} pools", raw.get("pools", []), allow_empty=True)
    for extra in pools:
        if not _NAME_RE.match(extra):
            raise ConfigError(f"{where}: pools entries must be short identifiers, got {extra!r}")
    expected = raw.get("expected_seconds")
    if expected is not None and (
        isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0
    ):
        raise ConfigError(f"{where}: expected_seconds must be a positive integer")
    inputs = tuple(_parse_input(name, i) for i in raw.get("inputs", []))
    names = [i.name for i in inputs]
    if len(names) != len(set(names)):
        raise ConfigError(f"{where}: duplicate input names")
    dki = _str_list(
        f"{where} duration_key_inputs", raw.get("duration_key_inputs", []), allow_empty=True
    )
    for k in dki:
        if k not in names:
            raise ConfigError(f"{where}: duration_key_inputs refers to unknown input '{k}'")
    passthrough = _str_list(
        f"{where} env_passthrough",
        raw.get("env_passthrough", list(defaults.env_passthrough)),
        allow_empty=True,
    )
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ConfigError(f"{where}: env must be a table of string values")
    requires = _parse_requires(f"{where} requires", raw.get("requires", []))
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ConfigError(f"{where}: description must be a string")
    try:
        globs = validate_globs(raw.get("artifacts", []))
    except PolicyError as e:
        raise ConfigError(f"{where}: artifacts: {e}") from e
    except TypeError as e:
        raise ConfigError(f"{where}: artifacts must be a list of glob strings") from e
    collect_on = raw.get("artifacts_on", COLLECT_ALWAYS)
    if collect_on not in COLLECT_ON:
        raise ConfigError(f"{where}: artifacts_on must be 'always' or 'failure'")
    if collect_on != COLLECT_ALWAYS and not globs:
        # 글롭이 없으면 아무것도 안 모은다 — 조건만 적어 둔 설정은 「모으고 있다」는 오해다
        raise ConfigError(f"{where}: artifacts_on needs artifacts globs to collect")
    placement_raw = raw.get("placement", "pool")
    if not isinstance(placement_raw, str) or placement_raw not in _PLACEMENT_VALUES:
        raise ConfigError(
            f'{where}: placement must be "pool" or "any-capable", got {placement_raw!r}'
        )
    scope = raw.get("concurrency_scope", SCOPE_POOL)
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ConfigError(
            f"{where}: concurrency_scope must be one of {', '.join(SCOPES)}, got {scope!r}"
        )
    checks = _str_list(f"{where} checks", raw.get("checks", []), allow_empty=True)
    if len(checks) != len(set(checks)):
        dupes = sorted({c for c in checks if checks.count(c) > 1})
        raise ConfigError(f"{where}: checks has a duplicate entry: {', '.join(dupes)}")
    check_ref = raw.get("check_ref", "HEAD")
    if not isinstance(check_ref, str) or not check_ref.strip():
        raise ConfigError(f"{where}: check_ref must be a non-empty string (a ref or HEAD)")
    sample = raw.get("sample")
    if sample is not None and not isinstance(sample, bool):
        raise ConfigError(f"{where}: sample must be true or false")
    keep = raw.get("keep", False)
    if not isinstance(keep, bool):
        raise ConfigError(f"{where}: keep must be true or false")
    if keep and not globs:
        # 보관할 묶음이 없다 — 「보관하고 있다」는 오해만 남는다(M5p §3.2)
        raise ConfigError(f"{where}: keep needs artifacts (declare the artifacts globs to keep)")
    supersede = raw.get("supersede", SUPERSEDE_OFF)
    if not isinstance(supersede, str) or supersede not in SUPERSEDE_MODES:
        raise ConfigError(
            f'{where}: supersede must be "off", "waiting" or "all", got {supersede!r}'
        )
    start_deadline = raw.get("start_deadline_seconds")
    if start_deadline is not None and (
        isinstance(start_deadline, bool)
        or not isinstance(start_deadline, int)
        or start_deadline < 1
    ):
        raise ConfigError(f"{where}: start_deadline_seconds must be a positive integer")
    retry = _parse_retry(where, raw.get("retry"), modes)
    local_only = raw.get("local_only", False)
    if not isinstance(local_only, bool):
        raise ConfigError(f"{where}: local_only must be true or false")
    if local_only and _PLACEMENT_VALUES[placement_raw] == PLACEMENT_POOL and pool != DEFAULT_POOL:
        # 로컬 레인은 기본 풀만 집는다 — 다른 풀에 묶인 로컬 전용 프리셋은 아무도 못 돌린다
        # (M5t-a 결정 254). `pools` 의 다른 풀은 막지 않는다 — 그 풀로 낸 제출이 409 다
        raise ConfigError(
            f'{where}: local_only needs pool = "{DEFAULT_POOL}" — the server\'s local lanes take '
            f"only the {DEFAULT_POOL} pool, not {pool!r}"
        )
    return Preset(
        name=name,
        argv=argv,
        description=description,
        timeout_seconds=timeout,
        source_modes=modes,
        repo=repo,
        priority=priority,
        pool=pool,
        pools=tuple(pools),
        concurrency_group=group or None,
        expected_seconds=expected,
        duration_key_inputs=dki,
        env_passthrough=passthrough,
        artifacts=globs,
        artifacts_on=collect_on,
        env=dict(env),
        requires=requires,
        inputs=inputs,
        placement=_PLACEMENT_VALUES[placement_raw],
        concurrency_scope=scope,
        checks=checks,
        check_ref=check_ref,
        sample=sample,
        supersede=supersede,
        keep=keep,
        start_deadline_seconds=start_deadline,
        retry=retry,
        local_only=local_only,
    )


def _parse_retry(where: str, raw: Any, modes: Sequence[str]) -> RetryPolicy | None:
    """`[presets.retry]` → 얼릴 반복 정책(M5s 결정 210 · 211). 표가 없으면 None — 반복하지 않는다.

    git_ref 만 받는 프리셋에만 허용한다. tree 스냅샷을 시도마다 복사하지 않으려는 것이다(결정
    211) — 다음 시도는 같은 sha 를 다시 받는다."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: retry must be a table ([presets.retry])")
    unknown = sorted(set(raw) - _RETRY_KEYS)
    if unknown:
        raise ConfigError(f"{where}: retry: unknown key(s): {', '.join(unknown)}")
    if list(modes) != [MODE_GIT_REF]:
        raise ConfigError(
            f'{where}: retry needs source_modes = ["git_ref"] — a retried attempt fetches the '
            "same commit again; a tree snapshot is not kept for it"
        )
    defaults = RetryPolicy()

    def whole(key: str, low: int, high: int) -> int:
        value = raw.get(key, getattr(defaults, key))
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigError(f"{where}: retry.{key} must be an integer from {low} to {high}")
        return value

    return RetryPolicy(
        on_exit=whole("on_exit", 1, 255),
        interval_seconds=whole("interval_seconds", 1, 7 * 24 * 3600),
        max_attempts=whole("max_attempts", 2, MAX_RETRY_ATTEMPTS),
    )


# ── 프로브 (M5n §4.1) ────────────────────────────────────────────────────────

_PROBE_KEYS = {"name", "argv", "timeout_seconds"}
_PROBE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_PROBES = 64
PROBE_TIMEOUT_MAX = 60


def parse_probes(raw: Any, origin: str = "config") -> tuple[Probe, ...]:
    """`[[probes]]` 배열 → Probe 들. 오류에 프로브 이름과 키 이름을 넣는다."""
    if not isinstance(raw, list):
        raise ConfigError(f"{origin}: [[probes]] must be an array of tables")
    if len(raw) > MAX_PROBES:
        raise ConfigError(f"[[probes]]: at most {MAX_PROBES} probes, got {len(raw)}")
    out: list[Probe] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError("[[probes]]: each probe must be a table")
        name = item.get("name")
        if not isinstance(name, str) or not _PROBE_NAME_RE.match(name):
            raise ConfigError(
                "[[probes]]: 'name' must be lowercase letters, digits, - or _ "
                f"(32 characters at most), got {name!r}"
            )
        where = f"probe '{name}'"
        unknown = sorted(set(item) - _PROBE_KEYS)
        if unknown:
            raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
        if "argv" not in item:
            raise ConfigError(f"{where}: 'argv' is required")
        argv = _str_list(f"{where} argv", item["argv"], allow_empty=False)
        timeout = item.get("timeout_seconds", 10)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= PROBE_TIMEOUT_MAX
        ):
            raise ConfigError(
                f"{where}: timeout_seconds must be an integer from 1 to {PROBE_TIMEOUT_MAX}"
            )
        out.append(Probe(name=name, argv=argv, timeout_seconds=timeout))
    names = [p.name for p in out]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ConfigError(f"[[probes]]: duplicate probe name(s): {', '.join(dupes)}")
    return tuple(out)


#: `[tools.install]` 의 키 — OS 계열 둘과 대체값. 패키지 관리자별 키는 아직 두지 않는다(§4.5)
TOOL_OS_KEYS = ("darwin", "linux", "any")
_TOOL_KEYS = frozenset({"name", "docs", "install"})
TOOL_TEXT_MAX = 300
#: 도구 이름의 길이 상한 — doctor 가 서버가 준 이름을 자르는 길이와 같다
TOOL_NAME_MAX = 64


def _tool_text(where: str, value: Any) -> str:
    """한 줄 문자열, 300자 이하, 제어문자 없음 — 워커가 그대로 터미널에 찍는 글이다. 거절하는
    글자는 doctor 가 찍기 전에 지우는 글자와 같다(`core/progress.clean_line`: C0 · C1 제어문자, 줄 ·
    문단 구분자, bidi 제어, 폭 없는 글자 — P6 GAP X-bidi). 위치는 가리지 않는다. `clean_line` 은
    끝에서 `str.strip()` 을 하는데 그것은 `\r` · `\n` · `\t` · U+2028 같은 제어 글자도 지운다 —
    그래서 값의 앞뒤에 보통 글자를 하나씩 붙여 견준다. 그러면 앞뒤의 제어 글자도 가운데처럼
    걸리고, 앞뒤의 공백은 전처럼 받아들인다(batch 10 재확인 X-bidi)."""
    from remote_ci_monitor.core.progress import clean_line

    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string")
    fenced = f"|{value}|"
    if clean_line(fenced) != fenced:
        raise ConfigError(
            f"{where} has a control character (one line of text, no escapes or bidi controls)"
        )
    if len(value) > TOOL_TEXT_MAX:
        raise ConfigError(f"{where} is longer than {TOOL_TEXT_MAX} characters")
    return value


def parse_tools(raw: Any, origin: str = "config") -> tuple[ToolHint, ...]:
    """`[[tools]]` 배열 → ToolHint 들. 같은 이름 두 번 · 모르는 OS 키 · 제어문자 · 300자 초과는
    기동 실패다(M5t-a §4.5 「설치 안내」 · TD9)."""
    if not isinstance(raw, list):
        raise ConfigError(f"{origin}: [[tools]] must be an array of tables")
    out: list[ToolHint] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError("[[tools]]: each entry must be a table")
        name = item.get("name")
        # 앞뒤 공백 · 64자 초과는 거절한다 — doctor 는 서버가 준 이름을 strip 하고 64자로 잘라
        # `requires` 의 이름과 맞추므로, 그런 이름의 안내는 어디에도 나오지 않는다
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > TOOL_NAME_MAX
            or "/" in name
        ):
            raise ConfigError(
                f"[[tools]]: 'name' must be a tool name (the basename in requires or a probe's "
                f"argv[0], no surrounding spaces, at most {TOOL_NAME_MAX} characters), "
                f"got {name!r}"
            )
        where = f"[[tools]] '{name}'"
        _tool_text(f"{where} name", name)
        unknown = sorted(set(item) - _TOOL_KEYS)
        if unknown:
            raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
        docs = _tool_text(f"{where} docs", item.get("docs", ""))
        install = item.get("install", {})
        if not isinstance(install, dict):
            raise ConfigError(f"{where}: install must be a table of {', '.join(TOOL_OS_KEYS)}")
        bad = sorted(set(install) - set(TOOL_OS_KEYS))
        if bad:
            raise ConfigError(
                f"{where}: unknown install key(s): {', '.join(bad)} (use {', '.join(TOOL_OS_KEYS)})"
            )
        texts = {k: _tool_text(f"{where} install.{k}", v) for k, v in install.items()}
        out.append(ToolHint(name=name, docs=docs, install=texts))
    names = [t.name for t in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(f"[[tools]]: {', '.join(dupes)} named twice")
    return tuple(out)


def tool_warnings(cfg: ServerConfig) -> list[str]:
    """`requires` 항목의 basename 에도 프로브 argv[0] 의 basename 에도 없는 `[[tools]]` 이름 —
    그 안내는 아무 데도 안 나온다. 기동 · reload 의 warning 줄이다(TD9)."""
    used = {os.path.basename(r) for p in cfg.presets for r in p.requires}
    used |= {os.path.basename(p.argv[0]) for p in cfg.probes if p.argv}
    return [
        f"[[tools]] '{t.name}': no preset requires it and no probe runs it — its hint is never "
        "shown"
        for t in cfg.tools
        if t.name not in used
    ]


def _validate_checks(presets: tuple[Preset, ...]) -> tuple[Preset, ...]:
    """`checks` 의 이름이 있고 git_ref 를 받고 순환이 없는지(결정 94). `sample` 기본값을 정한다.

    점검 프리셋 = 다른 프리셋의 `checks` 에 이름이 오른 것(§3). 점검 프리셋은 자기 `checks` 를
    가질 수 없다 — 점검의 점검은 없다(A→B→A 도 A→B→C 도 막는다).
    """
    by_name = {p.name: p for p in presets}
    check_names: set[str] = set()
    for p in presets:
        for c in p.checks:
            target = by_name.get(c)
            if target is None:
                raise ConfigError(f"preset '{p.name}': checks refers to unknown preset '{c}'")
            if "git_ref" not in target.source_modes:
                raise ConfigError(
                    f"preset '{p.name}': checks names '{c}', which must accept git_ref "
                    "(rcm runs a check by itself from the repository)"
                )
            check_names.add(c)
    for name in sorted(check_names):
        if by_name[name].checks:
            raise ConfigError(
                f"preset '{name}': a check preset cannot have checks of its own "
                f"(it is named in another preset's checks)"
            )
    return tuple(
        p if p.sample is not None else replace(p, sample=p.name not in check_names) for p in presets
    )


# ── 서버 설정 ────────────────────────────────────────────────────────────────

_SECTIONS = ("server", "estimate", "host", "display")
_TOP_KEYS = set(_SECTIONS) | {"repos", "presets", "notify", "probes", "include", "tools"}
#: 들인 파일이 가질 수 있는 최상위 키(M5o 결정 121). 저장소 토큰 · 비밀 · 바인드 주소는
#: server.toml 에만 있다 — 들인 파일을 쓸 수 있는 사람이 그것까지 정하면 안 된다
_INCLUDE_KEYS = frozenset({"presets"})


def _include_paths(found: Path, raw: dict[str, Any]) -> tuple[Path, ...]:
    """server.toml 의 `include = [...]` → 펼친 절대 경로들. 상대 경로는 **server.toml 의 폴더**
    기준이다(서비스의 cwd 는 아무도 모른다). 자기 자신 · 같은 파일 두 번은 거절한다."""
    for name, value in raw.items():
        # TOML 에서 `[server]` 아래에 쓴 `include` 는 `[server] include` 가 된다. 「모르는 키」
        # 보다 자리를 알려 주는 쪽이 고치는 사람에게 맞는 답이다
        tables = value if isinstance(value, list) else [value]
        if name != "include" and any(isinstance(t, dict) and "include" in t for t in tables):
            raise ConfigError(
                f"{found}: include must be at the top of the file, before the first [section] "
                f"(it was read as part of [{name}])"
            )
    value = raw.get("include", [])
    if not isinstance(value, list):
        raise ConfigError(
            f"{found}: include must be a list of file paths — include = [{value!r}]"
            if isinstance(value, str)
            else f"{found}: include must be a list of file paths"
        )
    own = os.path.realpath(found)
    seen: dict[str, str] = {}
    out: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{found}: include entries must be file paths, got {item!r}")
        p = Path(item).expanduser()
        if not p.is_absolute():
            p = found.parent / p
        p = Path(os.path.normpath(p))
        real = os.path.realpath(p)
        if real == own:
            raise ConfigError(f"{found}: include lists the config file itself ({item})")
        if real in seen:
            raise ConfigError(f"{found}: include lists the same file twice ({seen[real]}, {item})")
        seen[real] = item
        out.append(p)
    return tuple(out)


def _check_include_stat(path: Path, st: os.stat_result) -> None:
    """들인 파일은 서버를 돌리는 사용자 소유이고 그룹 · 남이 쓸 수 없어야 한다(결정 122).
    프리셋은 서버가 실행할 argv 다 — 아무나 쓸 수 있는 파일이 그것을 정하면 「프리셋만 실행」이라는
    약속이 무너진다. server.toml 자신에게는 이 규칙을 붙이지 않는다(기존 설치를 깨지 않는다)."""
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError(f"{path}: an included file must be a regular file")
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and st.st_uid != geteuid():
        raise ConfigError(
            f"{path}: an included file must be owned by the user running rcm "
            f"(uid {geteuid()}), it is owned by uid {st.st_uid}"
        )
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise ConfigError(
            f"{path}: an included file must not be writable by group or others — chmod go-w {path}"
        )


def _load_include_toml(path: Path) -> dict[str, Any]:
    """들인 파일을 **한 번** 열고, 그 fd 의 `fstat` 로 권한을 본 뒤 **같은 핸들에서** 읽는다.
    이름으로 `stat` 하고 다시 이름으로 열면 그 사이에 파일이 바뀔 수 있다(TOCTOU) — 검사를 지난
    파일과 읽은 파일이 같아야 검사가 뜻이 있다. `O_NONBLOCK` 은 FIFO 에서 열기가 멈추지 않게 한다
    (일반 파일에는 아무 효과가 없고, FIFO 는 아래 regular file 검사가 거절한다)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise ConfigError(f"included file not found: {path}") from None
    except OSError as e:
        raise ConfigError(f"{path}: cannot read: {e.strerror}") from e
    with os.fdopen(fd, "rb") as fh:
        _check_include_stat(path, os.fstat(fh.fileno()))
        try:
            return tomllib.load(fh)
        except UnicodeDecodeError as e:
            raise ConfigError(f"{path}: not UTF-8 (byte {e.start})") from e
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: invalid TOML: {e}") from e
        except OSError as e:
            raise ConfigError(f"{path}: cannot read: {e.strerror}") from e


def _read_include(path: Path, owner: Path) -> tuple[Preset, ...]:
    """들인 파일 하나 → 그 안의 프리셋. 오류마다 그 파일의 경로를 붙인다."""
    raw = _load_include_toml(path)
    other = sorted(set(raw) - _INCLUDE_KEYS)
    if other:
        raise ConfigError(
            f"{path}: an included file may only hold [[presets]], found "
            f"{', '.join(other)} — [server], [[repos]], [[notify]], [[probes]] and include "
            f"belong in {owner.name}"
        )
    presets = raw.get("presets", [])
    if not isinstance(presets, list):
        raise ConfigError(f"{path}: [[presets]] must be an array of tables")
    try:
        return tuple(parse_preset(p) for p in presets)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e


def _merge_presets(groups: list[tuple[Path, tuple[Preset, ...]]]) -> dict[str, Path]:
    """파일별 프리셋을 합치며 이름이 **파일 사이에서** 겹치면 두 파일을 댄다. **들인 파일 하나
    안의** 겹침은 그 파일 이름으로 말한다. server.toml 안의 겹침은 오늘처럼 `_validate_server`
    의 「duplicate preset name(s)」이 말한다(첫 묶음이 server.toml 이다). 이름이 같은 두 파일은
    경로로 가른다."""
    basenames = [path.name for path, _ in groups]
    label = {
        path: path.name if basenames.count(path.name) == 1 else str(path) for path, _ in groups
    }
    sources: dict[str, Path] = {}
    for path, presets in groups:
        for p in presets:
            first = sources.get(p.name)
            if first is not None and first != path:
                raise ConfigError(
                    f"preset '{p.name}' is defined in both {label[first]} and {label[path]}"
                )
            if first is not None and path != groups[0][0]:
                raise ConfigError(
                    f"{label[path]}: [[presets]] duplicate preset name '{p.name}' "
                    "(defined twice in this file)"
                )
            sources.setdefault(p.name, path)
    return sources


def _validate_repos(repos: tuple[RepoConfig, ...]) -> None:
    """`[[repos]]` 규칙 — 서버와 워커(M5b-3)가 같이 쓴다: 이름 중복 없음 · 짧은 식별자 · 안전한 URL
    (`-` 로 시작하는 URL 은 git 옵션이 된다)."""
    repo_names = [r.name for r in repos]
    if len(repo_names) != len(set(repo_names)):
        dupes = sorted({n for n in repo_names if repo_names.count(n) > 1})
        raise ConfigError(f"[[repos]] duplicate repo name(s): {', '.join(dupes)}")
    for r in repos:
        if not _NAME_RE.match(r.name):
            raise ConfigError(f"[[repos]] name must be a short identifier, got {r.name!r}")
        problem = validate_repo_url(r.url)
        if problem is not None:
            raise ConfigError(f"[[repos]] '{r.name}': {problem}")


#: 이 폴더들의 청소기는 숫자 이름 폴더를 지운다 — 보관함이 그 안에 있으면 남의 것을 지운다(§3.1).
SHELF_FORBIDDEN_PARENTS = ("artifacts", "jobs", "workspaces", "blobs", "mirrors")


def _validate_shelf(cfg: ServerConfig) -> None:
    """보관함 키 넷(M5p §3.1 · §15.3). 경로는 `resolve()` 뒤에 비교한다 — `..` 와 이미 있는
    심볼릭 링크로 금지 폴더에 들어가는 길을 막는다(경로가 아직 없어도 된다)."""
    s = cfg.server
    if s.shelf_max_bytes < 0:
        raise ConfigError("[server] shelf_max_bytes must be >= 0 (0 turns the shelf off)")
    if s.shelf_stale_days < 1:
        raise ConfigError("[server] shelf_stale_days must be >= 1")
    if s.shelf_delete_days <= s.shelf_stale_days:
        raise ConfigError("[server] shelf_delete_days must be greater than shelf_stale_days")
    shelf = cfg.shelf_dir.resolve()
    data = cfg.data_dir.resolve()
    if shelf == data or shelf in data.parents:
        raise ConfigError("[server] shelf_dir must not be data_dir or a folder that holds it")
    for name in SHELF_FORBIDDEN_PARENTS:
        sweeper = (data / name).resolve()
        if shelf == sweeper or sweeper in shelf.parents:
            raise ConfigError(
                f"[server] shelf_dir must not be inside data_dir/{name} "
                "(that sweeper deletes numbered folders)"
            )


def _validate_server(cfg: ServerConfig, *, check_tools: bool = True) -> None:
    s = cfg.server
    if s.lanes < 1:
        raise ConfigError("[server] lanes must be >= 1")
    if s.admission not in ("load", "always"):
        raise ConfigError("[server] admission must be 'load' or 'always'")
    if not 0 < s.cpu_max_percent <= 100:
        raise ConfigError("[server] cpu_max_percent must be between 1 and 100")
    if s.admission_samples < 1:
        raise ConfigError("[server] admission_samples must be >= 1")
    if s.admission_cooldown_seconds < 0:
        raise ConfigError("[server] admission_cooldown_seconds must be >= 0")
    if not (1 <= s.port <= 65535):
        raise ConfigError("[server] port must be between 1 and 65535")
    if s.read_auth not in ("none", "basic"):
        raise ConfigError("[server] read_auth must be 'none' or 'basic'")
    for key in (
        "max_snapshot_bytes",
        "max_concurrent_requests",
        "grace_seconds",
        "recent_count",
        "upload_stall_seconds",
        "upload_abandon_seconds",
    ):
        if getattr(s, key) < 1:
            raise ConfigError(f"[server] {key} must be >= 1")
    if not 1 <= s.failure_window_jobs <= 500:
        raise ConfigError("[server] failure_window_jobs must be between 1 and 500")
    if s.failure_min_jobs < 1:
        raise ConfigError("[server] failure_min_jobs must be >= 1")
    if s.failure_min_jobs > s.failure_window_jobs:
        raise ConfigError("[server] failure_min_jobs must be <= failure_window_jobs")
    if s.sse_max_connections < 0:
        raise ConfigError("[server] sse_max_connections must be >= 0")
    if s.sse_keepalive_seconds < 1:
        raise ConfigError("[server] sse_keepalive_seconds must be >= 1")
    for key in ("retention_days_success", "retention_days_failure", "workspace_retention_days"):
        if getattr(s, key) < 0:
            raise ConfigError(f"[server] {key} must be >= 0")
    if s.min_free_bytes < 0:
        raise ConfigError("[server] min_free_bytes must be >= 0")
    if s.workspace_storage_max_bytes != 0 and s.workspace_storage_max_bytes < 1024**3:
        # 게이트 잡 하나가 720 MB 다. 그보다 작은 예산은 「끝나는 족족 지운다」와 같고,
        # 그건 workspace_retention_days = 0 이 이미 표현한다.
        raise ConfigError(
            "[server] workspace_storage_max_bytes must be 0 (no limit) or at least 1 GiB"
        )

    for key in ("git_resolve_timeout_seconds", "git_fetch_timeout_seconds"):
        if getattr(s, key) < 1:
            raise ConfigError(f"[server] {key} must be >= 1")
    if s.retention_sweep_interval_seconds < 60:
        raise ConfigError("[server] retention_sweep_interval_seconds must be at least 60")
    if s.snapshot_cache_days < 1:
        raise ConfigError("[server] snapshot_cache_days must be >= 1")
    if s.snapshot_cache_max_bytes < 1024 * 1024:
        raise ConfigError("[server] snapshot_cache_max_bytes must be at least 1 MiB")
    if s.snapshot_cache_scope not in ("global", "token"):
        raise ConfigError("[server] snapshot_cache_scope must be 'global' or 'token'")
    floor = max(cfg.estimate.sample_days, s.retention_days_failure, s.retention_days_success)
    if s.metadata_retention_days < floor:
        raise ConfigError(
            f"[server] metadata_retention_days must be >= {floor} "
            "(max of estimate.sample_days and retention_days_*)"
        )
    if s.upload_abandon_seconds < s.upload_stall_seconds:
        raise ConfigError("[server] upload_abandon_seconds must be >= upload_stall_seconds")
    if s.worker_timeout_seconds < 10:
        raise ConfigError("[server] worker_timeout_seconds must be >= 10")
    if s.worker_heartbeat_seconds < 1:
        raise ConfigError("[server] worker_heartbeat_seconds must be >= 1")
    if s.worker_heartbeat_seconds >= s.worker_timeout_seconds:
        raise ConfigError("[server] worker_heartbeat_seconds must be < worker_timeout_seconds")
    for key in (
        "artifact_retention_hours",
        "max_artifact_bytes",
        "max_artifact_files",
        "artifact_storage_max_bytes",
        "artifact_timeout_seconds",
        "artifact_cancel_timeout_seconds",
        "artifact_transfer_timeout_seconds",
        "max_concurrent_artifact_transfers",
    ):
        if getattr(s, key) < 1:
            raise ConfigError(f"[server] {key} must be >= 1")
    if any(p.artifacts for p in cfg.presets) and (
        s.artifact_cancel_timeout_seconds >= 2 * s.worker_heartbeat_seconds
    ):
        # 서버는 미확인 취소를 `kill_at + 2 × heartbeat` 에 닫는다(`remote_workers.py`). 수집이
        # 그보다 길면 워커의 finish 가 409 를 받고 모은 것이 버려진다(명세 §5). 산출물을 쓰는
        # 프리셋이 하나도 없으면 이 짝은 의미가 없으므로 보지 않는다.
        raise ConfigError(
            "[server] artifact_cancel_timeout_seconds must be < 2 × worker_heartbeat_seconds"
        )
    _validate_shelf(cfg)
    if not (0 <= s.worker_claim_wait_seconds <= 60):
        raise ConfigError("[server] worker_claim_wait_seconds must be between 0 and 60")
    if s.probe_interval_seconds < 60:
        raise ConfigError("[server] probe_interval_seconds must be at least 60")
    if s.check_sweep_seconds < 1:
        raise ConfigError("[server] check_sweep_seconds must be >= 1")
    if s.schedule_start_deadline_seconds < 1:
        raise ConfigError("[server] schedule_start_deadline_seconds must be >= 1")
    if not 1 <= s.schedule_max_ahead_days <= 366:
        raise ConfigError("[server] schedule_max_ahead_days must be between 1 and 366")
    if s.advertise_name and not _NAME_RE.match(s.advertise_name):
        raise ConfigError(
            "[server] advertise_name must be a short identifier (letters, digits, . _ -)"
        )
    e = cfg.estimate
    if e.sample_policy not in ("success", "completed"):
        raise ConfigError("[estimate] sample_policy must be 'success' or 'completed'")
    if e.min_samples < 1:
        raise ConfigError("[estimate] min_samples must be >= 1")
    if e.default_seconds < 1 or e.floor_remaining_seconds < 0:
        raise ConfigError("[estimate] default_seconds must be >= 1 and floor >= 0")
    if e.stuck_multiplier <= 1:
        raise ConfigError("[estimate] stuck_multiplier must be > 1")
    if e.no_output_seconds < 1:
        raise ConfigError("[estimate] no_output_seconds must be >= 1")
    if e.step_stuck_multiplier <= 1:
        raise ConfigError("[estimate] step_stuck_multiplier must be > 1")
    if e.step_min_samples < 2:
        raise ConfigError("[estimate] step_min_samples must be >= 2")
    h = cfg.host
    if h.interval_seconds < 2:
        raise ConfigError("[host] interval_seconds must be >= 2")
    if h.gpu not in ("auto", "off"):
        raise ConfigError("[host] gpu must be 'auto' or 'off'")
    if h.history_samples < 1:
        raise ConfigError("[host] history_samples must be >= 1")
    tz = cfg.display.timezone
    if tz:
        try:
            zoneinfo.ZoneInfo(tz)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as ex:
            raise ConfigError(f"[display] timezone {tz!r} is not a known IANA zone") from ex
    names = [p.name for p in cfg.presets]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ConfigError(f"[[presets]] duplicate preset name(s): {', '.join(dupes)}")
    _validate_repos(cfg.repos)
    if check_tools and cfg.repos:
        # CLT 없는 Mac 의 `/usr/bin/git` shim 은 있음이 아니다 — 실행하면 설치 창이 뜬다. 판별은
        # `runner.find_tool` 하나다(M5t-a 결정 246, P6 GAP R2). 늦게 들여 순환을 피한다
        from remote_ci_monitor import runner

        path = os.environ.get("PATH", "")
        if runner.find_tool("git", path) is None:
            note = runner.missing_note("git", {"PATH": path})
            where = f"git is {note}" if note else "git is not on PATH"
            raise ConfigError(f"[[repos]] configured but {where}")
    resolved: list[Preset] = []
    for p in cfg.presets:
        if "git_ref" in p.source_modes:
            if not cfg.repos:
                raise ConfigError(
                    f"preset '{p.name}': source_modes includes git_ref but no [[repos]]"
                )
            if not p.repo:
                if len(cfg.repos) != 1:
                    raise ConfigError(
                        f"preset '{p.name}': repo is required when more than one [[repos]] "
                        "is configured"
                    )
                p = replace(p, repo=cfg.repos[0].name)
            elif cfg.repo(p.repo) is None:
                raise ConfigError(f"preset '{p.name}': repo '{p.repo}' is not in [[repos]]")
        resolved.append(p)
    cfg.presets = _validate_checks(tuple(resolved))
    rule_names = [r.name for r in cfg.notify]
    if len(rule_names) != len(set(rule_names)):
        dupes = sorted({n for n in rule_names if rule_names.count(n) > 1})
        raise ConfigError(f"[[notify]] duplicate rule name(s): {', '.join(dupes)}")
    preset_names = {p.name for p in cfg.presets}
    for r in cfg.notify:
        for name in sorted(r.presets or ()):
            if name not in preset_names:
                raise ConfigError(f"notify '{r.name}': presets refers to unknown preset '{name}'")


def load_server_config(
    path: str | os.PathLike[str] | None = None,
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
    environ: dict[str, str] | None = None,
    check_tools: bool = True,
) -> ServerConfig:
    """서버 설정을 만든다. `overrides` 는 플래그({"server": {"port": 1}}), 최우선.

    `check_tools=False` 면 외부 도구(git) 유무는 검사하지 않는다 — `rcm check` 가 행으로 보여준다.
    """
    cfg = ServerConfig()
    found = find_server_config(path)
    if found is not None:
        raw = _read_toml(found)
        unknown = sorted(set(raw) - _TOP_KEYS)
        if unknown:
            raise ConfigError(f"{found}: unknown section(s): {', '.join(unknown)}")
        cfg.includes = _include_paths(found, raw)
        for name in _SECTIONS:
            values = raw.get(name, {})
            if not isinstance(values, dict):
                raise ConfigError(f"{found}: [{name}] must be a table")
            _apply_section(getattr(cfg, name), name, values, str(found))
        cfg.repos = _parse_repos(raw.get("repos", []), str(found))
        presets = raw.get("presets", [])
        if not isinstance(presets, list):
            raise ConfigError(f"{found}: [[presets]] must be an array of tables")
        # server.toml 의 것 먼저, 그다음 include 에 적힌 순서(M5o 결정 121). 검증(`checks` ·
        # notify 의 presets · 이름 겹침)은 아래 `_validate_server` 가 **합친 목록** 위에서 한다
        groups = [(found, tuple(parse_preset(p) for p in presets))]
        groups += [(path, _read_include(path, found)) for path in cfg.includes]
        cfg.preset_sources = _merge_presets(groups)
        cfg.presets = tuple(p for _, group in groups for p in group)
        notify = raw.get("notify", [])
        if not isinstance(notify, list):
            raise ConfigError(f"{found}: [[notify]] must be an array of tables")
        cfg.notify = tuple(parse_notify(n) for n in notify)
        cfg.probes = parse_probes(raw.get("probes", []), str(found))
        cfg.tools = parse_tools(raw.get("tools", []), str(found))
        cfg.path = found
    saved = None
    if environ is not None:
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(environ)
    try:
        for name in _SECTIONS:
            _apply_section(
                getattr(cfg, name), name, _env_overrides(name, getattr(cfg, name)), "env"
            )
    finally:
        if saved is not None:
            os.environ.clear()
            os.environ.update(saved)
    for name, values in (overrides or {}).items():
        if name not in _SECTIONS:
            raise ConfigError(f"unknown section '{name}' in overrides")
        clean = {k: v for k, v in values.items() if v is not None}
        _apply_section(getattr(cfg, name), name, clean, "flag")
    _validate_server(cfg, check_tools=check_tools)
    return cfg


# ── 클라이언트 설정 ──────────────────────────────────────────────────────────

_CLIENT_KEYS = {"server", "token_env", "token", "label"}


def find_client_config(explicit: str | os.PathLike[str] | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError(f"client config not found: {p}")
        return p
    for p in _candidates("client", CLIENT_CONFIG_CANDIDATES):
        if p.is_file():
            return p
    return None


def _check_private(path: Path) -> None:
    """토큰을 직접 담은 파일은 소유자만 읽을 수 있어야 한다(600)."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{path}: contains a token but is readable by others — chmod 600 {path}")


def load_client_config(
    path: str | os.PathLike[str] | None = None,
    *,
    server: str | None = None,
    token: str | None = None,
    label: str | None = None,
    environ: dict[str, str] | None = None,
) -> ClientConfig:
    """클라이언트 설정. 우선순위 플래그 > `RCM_SERVER`/`RCM_TOKEN` > 파일 > 없음."""
    env = os.environ if environ is None else environ
    cfg = ClientConfig()
    found = find_client_config(path)
    if found is not None:
        raw = _read_toml(found)
        unknown = sorted(set(raw) - _CLIENT_KEYS)
        if unknown:
            raise ConfigError(f"{found}: unknown key(s): {', '.join(unknown)}")
        for key in _CLIENT_KEYS:
            if key in raw and not isinstance(raw[key], str):
                raise ConfigError(f"{found}: '{key}' must be a string")
        cfg.server = raw.get("server", "")
        cfg.label = raw.get("label", "")
        if raw.get("token"):
            _check_private(found)
            cfg.token = raw["token"]
        token_env = raw.get("token_env", "RCM_TOKEN")
        cfg.token_env = token_env or "RCM_TOKEN"
        if not cfg.token and token_env and env.get(token_env):
            cfg.token = env[token_env]
        cfg.path = found
    if env.get("RCM_SERVER"):
        cfg.server = env["RCM_SERVER"]
    if env.get("RCM_TOKEN"):
        cfg.token = env["RCM_TOKEN"]
    if env.get("RCM_LABEL"):
        cfg.label = env["RCM_LABEL"]
    if server:
        cfg.server = server
    if token:
        cfg.token = token
    if label:
        cfg.label = label
    cfg.server = cfg.server.rstrip("/")
    return cfg


# ── 워커 설정 (M5b-3) ────────────────────────────────────────────────────────

_WORKER_KEYS = {
    "server",
    "token",
    "pool",
    "lanes",
    "name",
    "data_dir",
    "grace_seconds",
    "keep_workspace_on_failure",
    "git_fetch_timeout_seconds",
    "server_name",
    "accept_any",
}
_WORKER_OVERRIDE_KEYS = {"server", "pool", "lanes", "name", "data_dir"}


def _parse_release_secret(where: str, raw: Any) -> ReleaseSecret:
    """`[[repos.<name>.release.secrets]]` 하나. 이름은 파일 이름이 되므로 경로 조각은 거부한다."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each secret must be a table")
    name = raw.get("name")
    if (
        not isinstance(name, str)
        or not name.strip()
        or "/" in name
        or "\\" in name
        or name in (".", "..")
    ):
        raise ConfigError(f"{where}: secret 'name' must be a file or env name, got {name!r}")
    where = f"{where} '{name}'"
    unknown = sorted(set(raw) - _RELEASE_SECRET_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    kind = raw.get("kind", "value")
    if kind not in RELEASE_SECRET_KINDS:
        raise ConfigError(
            f"{where}: kind must be one of {', '.join(RELEASE_SECRET_KINDS)}, got {kind!r}"
        )
    optional = raw.get("optional", False)
    if not isinstance(optional, bool):
        raise ConfigError(f"{where}: optional must be true or false")
    verify = raw.get("verify", "none")
    if verify not in RELEASE_VERIFY_KINDS:
        raise ConfigError(
            f"{where}: verify must be one of {', '.join(RELEASE_VERIFY_KINDS)}, got {verify!r}"
        )
    files: tuple[str, ...] = ()
    if "files" in raw:
        if kind != "dir":
            raise ConfigError(f"{where}: 'files' is only valid for kind = \"dir\"")
        files = _str_list(f"{where} files", raw["files"], allow_empty=False)
        if any(not f.strip() or "/" in f for f in files):
            raise ConfigError(f"{where}: files entries must be plain file names")
    max_kb = raw.get("max_kb", 512)
    if "max_kb" in raw and kind != "file":
        raise ConfigError(f"{where}: 'max_kb' is only valid for kind = \"file\"")
    if isinstance(max_kb, bool) or not isinstance(max_kb, int) or max_kb < 1:
        raise ConfigError(f"{where}: max_kb must be a positive integer")
    return ReleaseSecret(
        name=name, kind=kind, optional=optional, verify=verify, files=files, max_kb=max_kb
    )


def _parse_release_listing(where: str, raw: Any) -> ReleaseListing:
    """`[repos.<name>.release.listing]` — 명령은 빈 argv 를 받지 않는다."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a table")
    unknown = sorted(set(raw) - _RELEASE_LISTING_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    argvs: dict[str, tuple[str, ...]] = {}
    for key in ("preview", "diff", "validate"):
        argvs[key] = _str_list(f"{where} {key}", raw[key], allow_empty=False) if key in raw else ()
    screenshots = (
        _str_list(f"{where} screenshots", raw["screenshots"], allow_empty=False)
        if "screenshots" in raw
        else ()
    )
    notes = raw.get("release_notes", "")
    if not isinstance(notes, str):
        raise ConfigError(f"{where}: release_notes must be a string")
    ref = raw.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref.strip() or " " in ref):
        raise ConfigError(f"{where}: ref must be a branch name")
    return ReleaseListing(
        ref=ref,
        preview=argvs["preview"],
        diff=argvs["diff"],
        validate=argvs["validate"],
        screenshots=screenshots,
        release_notes=notes,
    )


def parse_release_profile(repo_name: str, raw: Any) -> ReleaseProfile:
    """`[repos.<name>.release]` 를 검증해 ReleaseProfile 로. 오류에 섹션과 키 이름을 넣는다."""
    where = f"[repos.{repo_name}.release]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a table")
    unknown = sorted(set(raw) - _RELEASE_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    defaults = ReleaseProfile()
    branch = raw.get("default_branch", defaults.default_branch)
    if not isinstance(branch, str) or not branch.strip():
        raise ConfigError(f"{where}: default_branch must be a non-empty string")
    tag = raw.get("tag", defaults.tag)
    if not isinstance(tag, str) or "{version}" not in tag or "{build}" not in tag:
        raise ConfigError(f"{where}: tag must be a string containing {{version}} and {{build}}")
    policy = raw.get("build_number_policy", defaults.build_number_policy)
    if policy not in RELEASE_BUILD_NUMBER_POLICIES:
        raise ConfigError(
            f'{where}: build_number_policy must be "auto" or "manual", got {policy!r}'
        )
    max_age = raw.get("plan_max_age_minutes", defaults.plan_max_age_minutes)
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 1:
        raise ConfigError(f"{where}: plan_max_age_minutes must be a positive integer")
    ttl = raw.get("version_ttl_hours", defaults.version_ttl_hours)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
        raise ConfigError(f"{where}: version_ttl_hours must be a positive integer")
    driver = raw.get("driver")
    if driver is not None:
        if (
            not isinstance(driver, str)
            or not driver.strip()
            or driver.startswith("/")
            or ".." in Path(driver).parts
        ):
            raise ConfigError(
                f"{where}: driver must be a relative path inside the repository, got {driver!r}"
            )
    env_name = raw.get("secrets_dir_env")
    if env_name is not None and (
        not isinstance(env_name, str) or not _SECRETS_DIR_ENV_RE.match(env_name)
    ):
        raise ConfigError(
            f"{where}: secrets_dir_env must match ^[A-Z][A-Z0-9_]*$, got {env_name!r}"
        )
    presets_raw = raw.get("presets", {})
    presets_where = f"[repos.{repo_name}.release.presets]"
    if not isinstance(presets_raw, dict):
        raise ConfigError(f"{presets_where}: must be a table")
    unknown = sorted(set(presets_raw) - set(RELEASE_ROLES))
    if unknown:
        raise ConfigError(
            f"{presets_where}: unknown key(s): {', '.join(unknown)} "
            f"(roles are {', '.join(RELEASE_ROLES)})"
        )
    presets: dict[str, str] = {}
    for role in RELEASE_ROLES:
        if role not in presets_raw:
            continue
        value = presets_raw[role]
        if not isinstance(value, str) or not _NAME_RE.match(value):
            raise ConfigError(f"{presets_where}: {role} must be a preset name, got {value!r}")
        presets[role] = value
    secrets_raw = raw.get("secrets", [])
    secrets_where = f"[[repos.{repo_name}.release.secrets]]"
    if not isinstance(secrets_raw, list):
        raise ConfigError(f"{secrets_where}: must be an array of tables")
    secrets = tuple(_parse_release_secret(secrets_where, s) for s in secrets_raw)
    listing = None
    if "listing" in raw:
        listing = _parse_release_listing(f"[repos.{repo_name}.release.listing]", raw["listing"])
    return ReleaseProfile(
        presets=presets,
        default_branch=branch,
        tag=tag,
        build_number_policy=policy,
        plan_max_age_minutes=max_age,
        version_ttl_hours=ttl,
        driver=driver,
        secrets_dir_env=env_name,
        secrets=secrets,
        listing=listing,
    )


def _parse_repos(raw: Any, where: str) -> tuple[RepoConfig, ...]:
    """`[[repos]]` — `name`·`url` 만, 거기에 선택으로 릴리스 프로파일 하나.

    TOML 에서 `[repos.app.release]` 는 **마지막** `[[repos]]` 원소 안의 `app.release` 다 — 그래서
    원소에 자기 이름과 같은 키가 하나 더 생기고 그 안에 `release` 만 있어야 한다. `[repos.release]`
    라고 써서 `release` 키가 바로 오는 꼴도 같은 뜻으로 받는다.
    """
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: [[repos]] must be an array of tables")
    out: list[RepoConfig] = []
    for r in raw:
        if not isinstance(r, dict) or not {"name", "url"} <= set(r):
            raise ConfigError(f"{where}: each [[repos]] needs exactly 'name' and 'url'")
        if not isinstance(r["name"], str) or not isinstance(r["url"], str):
            raise ConfigError(f"{where}: [[repos]] name and url must be strings")
        name = r["name"]
        token_file = _repo_token_file(r, name, where)
        extra = set(r) - {"name", "url", "token_file"}
        release_raw: Any = None
        if extra == {"release"}:
            release_raw = r["release"]
        elif extra == {name} and isinstance(r[name], dict) and set(r[name]) == {"release"}:
            release_raw = r[name]["release"]
        elif extra:
            raise ConfigError(
                f"{where}: each [[repos]] needs exactly 'name' and 'url' (plus an optional "
                f"'token_file' and [repos.{name}.release] table), got extra key(s): "
                f"{', '.join(sorted(extra))}"
            )
        release = None if release_raw is None else parse_release_profile(name, release_raw)
        out.append(RepoConfig(name=name, url=r["url"], release=release, token_file=token_file))
    return tuple(out)


def _repo_token_file(r: dict[str, Any], name: str, where: str) -> str | None:
    """`[[repos]] token_file` — 비지 않은 문자열이고, 저장소 url 이 https 여야 한다.

    토큰은 https 자격이다. ssh 나 scp 형 url 에 붙이면 git 이 쓰지 않아 「설정했는데 안 먹는」
    꼴이 되므로 읽을 때 거절한다. 파일이 있는지는 여기서 보지 않는다 — 토큰을 바꿔 끼우는 동안
    서버가 못 뜨면 안 된다. 못 읽으면 그 저장소의 git 호출이 `no_credential` 로 실패한다.
    """
    if "token_file" not in r:
        return None
    value = r["token_file"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"{where}: [[repos]] {name!r}: token_file must be a path to a file holding a token"
        )
    if not r["url"].startswith("https://"):
        raise ConfigError(
            f"{where}: [[repos]] {name!r}: token_file needs an https:// url — a token is an "
            "https credential and git ignores it for ssh"
        )
    return value


def load_worker_config(
    path: str | os.PathLike[str] | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> WorkerConfig:
    """`worker.toml`(선택) + env `RCM_WORKER_TOKEN` + 플래그(`overrides`, 파일보다 우선).

    토큰은 플래그로 줄 수 없다(`overrides` 에 `token` 이 있으면 `ConfigError` — 프로세스 목록에
    노출된다). `server` 는 꼭 있어야 한다.
    """
    env = os.environ if environ is None else environ
    cfg = WorkerConfig()
    if path is not None:
        found = Path(path).expanduser()
        raw = _read_toml(found)
        unknown = sorted(set(raw) - _WORKER_KEYS - {"host", "repos"})
        if unknown:
            raise ConfigError(f"{found}: unknown key(s): {', '.join(unknown)}")
        scalars = {k: v for k, v in raw.items() if k in _WORKER_KEYS}
        _apply_section(cfg, "worker", scalars, str(found))
        if cfg.token:
            _check_private(found)
        host = raw.get("host", {})
        if not isinstance(host, dict):
            raise ConfigError(f"{found}: [host] must be a table")
        _apply_section(cfg.host, "host", host, str(found))
        cfg.repos = _parse_repos(raw.get("repos", []), str(found))
        cfg.path = found
    if env.get("RCM_WORKER_TOKEN"):
        cfg.token = env["RCM_WORKER_TOKEN"]
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key == "token":
            raise ConfigError("the worker token cannot be passed as a flag — use RCM_WORKER_TOKEN")
        if key not in _WORKER_OVERRIDE_KEYS:
            raise ConfigError(f"unknown worker override '{key}'")
        setattr(
            cfg, key, _coerce_scalar(f"--{key.replace('_', '-')}", value, type(getattr(cfg, key)))
        )
    cfg.server = cfg.server.rstrip("/")
    # 비었거나 "auto" 면 호출자(`rcm worker`)가 같은 망에서 찾는다 — 여기서는 막지 않는다(M5n §9)
    if not _NAME_RE.match(cfg.pool):
        raise ConfigError("worker pool must be a name (letters, digits, . _ -)")
    if not (1 <= cfg.lanes <= 64):
        raise ConfigError("worker lanes must be between 1 and 64")
    if cfg.grace_seconds < 1:
        raise ConfigError("worker grace_seconds must be >= 1")
    _validate_repos(cfg.repos)
    if cfg.git_fetch_timeout_seconds < 1:
        raise ConfigError("worker git_fetch_timeout_seconds must be >= 1")
    h = cfg.host
    if h.interval_seconds < 2:
        raise ConfigError("[host] interval_seconds must be >= 2")
    if h.gpu not in ("auto", "off"):
        raise ConfigError("[host] gpu must be 'auto' or 'off'")
    if h.history_samples < 1:
        raise ConfigError("[host] history_samples must be >= 1")
    return cfg
