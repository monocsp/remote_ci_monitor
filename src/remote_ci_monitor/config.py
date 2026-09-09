"""서버·클라이언트 설정 로딩. 우선순위 플래그 > 환경변수(`RCM_<섹션>_<키>`) > 파일 > 기본값.

오류는 시작 시 **섹션·키 이름과 함께** `ConfigError` 로 실패한다. 조용히 기본값으로
떨어지지 않는다(PLAN.md 「설정」). 프리셋 검증도 여기서 한다 — 모르는 키, 빈 argv,
choices 없는 choice 입력은 프리셋 이름과 키 이름을 찍고 실패한다.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tomllib
import zoneinfo
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
    PRIORITY_NAMES,
    SOURCE_MODES,
    TERMINAL_STATES,
    InputSpec,
    Preset,
)
from remote_ci_monitor.core.notify import NotifyRule

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


@dataclass
class HostSection:
    interval_seconds: int = 5
    gpu: str = "auto"
    top_processes: int = 5
    history_samples: int = 60


@dataclass
class DisplaySection:
    timezone: str = ""


@dataclass
class RepoConfig:
    name: str
    url: str


@dataclass
class ServerConfig:
    server: ServerSection = field(default_factory=ServerSection)
    estimate: EstimateSection = field(default_factory=EstimateSection)
    host: HostSection = field(default_factory=HostSection)
    display: DisplaySection = field(default_factory=DisplaySection)
    repos: tuple[RepoConfig, ...] = ()
    presets: tuple[Preset, ...] = ()
    notify: tuple[NotifyRule, ...] = ()
    path: Path | None = None

    @property
    def data_dir(self) -> Path:
        return Path(self.server.data_dir).expanduser()

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
    host: HostSection = field(default_factory=HostSection)
    repos: tuple[RepoConfig, ...] = ()
    path: Path | None = None

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
    "inputs",
    "artifacts",
    "artifacts_on",
}
_INPUT_KEYS = {"name", "type", "choices", "default", "pattern", "description"}


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
        on = frozenset(TERMINAL_STATES)
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
        inputs=inputs,
    )


# ── 서버 설정 ────────────────────────────────────────────────────────────────

_SECTIONS = ("server", "estimate", "host", "display")
_TOP_KEYS = set(_SECTIONS) | {"repos", "presets", "notify"}


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
    if not (0 <= s.worker_claim_wait_seconds <= 60):
        raise ConfigError("[server] worker_claim_wait_seconds must be between 0 and 60")
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
    if check_tools and cfg.repos and shutil.which("git") is None:
        raise ConfigError("[[repos]] configured but git is not on PATH")
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
    cfg.presets = tuple(resolved)
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
        for name in _SECTIONS:
            values = raw.get(name, {})
            if not isinstance(values, dict):
                raise ConfigError(f"{found}: [{name}] must be a table")
            _apply_section(getattr(cfg, name), name, values, str(found))
        repos = raw.get("repos", [])
        if not isinstance(repos, list):
            raise ConfigError(f"{found}: [[repos]] must be an array of tables")
        parsed_repos: list[RepoConfig] = []
        for r in repos:
            if not isinstance(r, dict) or set(r) != {"name", "url"}:
                raise ConfigError(f"{found}: each [[repos]] needs exactly 'name' and 'url'")
            if not isinstance(r["name"], str) or not isinstance(r["url"], str):
                raise ConfigError(f"{found}: [[repos]] name and url must be strings")
            parsed_repos.append(RepoConfig(name=r["name"], url=r["url"]))
        cfg.repos = tuple(parsed_repos)
        presets = raw.get("presets", [])
        if not isinstance(presets, list):
            raise ConfigError(f"{found}: [[presets]] must be an array of tables")
        cfg.presets = tuple(parse_preset(p) for p in presets)
        notify = raw.get("notify", [])
        if not isinstance(notify, list):
            raise ConfigError(f"{found}: [[notify]] must be an array of tables")
        cfg.notify = tuple(parse_notify(n) for n in notify)
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
}
_WORKER_OVERRIDE_KEYS = {"server", "pool", "lanes", "name", "data_dir"}


def _parse_repos(raw: Any, where: str) -> tuple[RepoConfig, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: [[repos]] must be an array of tables")
    out: list[RepoConfig] = []
    for r in raw:
        if not isinstance(r, dict) or set(r) != {"name", "url"}:
            raise ConfigError(f"{where}: each [[repos]] needs exactly 'name' and 'url'")
        if not isinstance(r["name"], str) or not isinstance(r["url"], str):
            raise ConfigError(f"{where}: [[repos]] name and url must be strings")
        out.append(RepoConfig(name=r["name"], url=r["url"]))
    return tuple(out)


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
    if not cfg.server:
        raise ConfigError('worker needs a server: --server URL or `server = "…"` in worker.toml')
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
