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

from remote_ci_monitor.core.gitref import validate_repo_url
from remote_ci_monitor.core.model import (
    INPUT_TYPES,
    SOURCE_MODES,
    InputSpec,
    Preset,
)

ENV_PREFIX = "RCM"
DEFAULT_DATA_DIR = "~/.local/share/rcm"
SERVER_CONFIG_CANDIDATES = ("./rcm.toml", "~/.config/rcm/server.toml")
CLIENT_CONFIG_CANDIDATES = ("~/.config/rcm/client.toml",)


def user_config_dir() -> Path:
    """`$XDG_CONFIG_HOME/rcm` 또는 `~/.config/rcm` — `rcm init` 이 쓰고 탐색이 먼저 본다."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "rcm"


def _candidates(kind: str, static: tuple[str, ...]) -> list[Path]:
    """XDG 경로를 먼저, 그다음 고정 후보. 같은 경로는 한 번만."""
    out: list[Path] = [user_config_dir() / f"{kind}.toml"]
    for cand in static:
        p = Path(cand).expanduser()
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
    read_auth: str = "none"
    max_snapshot_bytes: int = 536_870_912
    max_concurrent_requests: int = 32
    join_duplicates: bool = True
    grace_seconds: int = 10
    retention_days_success: int = 14
    retention_days_failure: int = 30
    keep_workspace_on_failure: bool = True
    recent_count: int = 8
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
    """탐색 순서: `--config` → `$RCM_CONFIG` → `./rcm.toml` → `~/.config/rcm/server.toml`."""
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
    "concurrency_group",
    "expected_seconds",
    "duration_key_inputs",
    "env_passthrough",
    "env",
    "inputs",
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
    return Preset(
        name=name,
        argv=argv,
        description=description,
        timeout_seconds=timeout,
        source_modes=modes,
        repo=repo,
        concurrency_group=group or None,
        expected_seconds=expected,
        duration_key_inputs=dki,
        env_passthrough=passthrough,
        env=dict(env),
        inputs=inputs,
    )


# ── 서버 설정 ────────────────────────────────────────────────────────────────

_SECTIONS = ("server", "estimate", "host", "display")
_TOP_KEYS = set(_SECTIONS) | {"repos", "presets"}


def _validate_server(cfg: ServerConfig, *, check_tools: bool = True) -> None:
    s = cfg.server
    if s.lanes < 1:
        raise ConfigError("[server] lanes must be >= 1")
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
    if s.sse_max_connections < 0:
        raise ConfigError("[server] sse_max_connections must be >= 0")
    if s.sse_keepalive_seconds < 1:
        raise ConfigError("[server] sse_keepalive_seconds must be >= 1")
    for key in ("retention_days_success", "retention_days_failure"):
        if getattr(s, key) < 0:
            raise ConfigError(f"[server] {key} must be >= 0")
    for key in ("git_resolve_timeout_seconds", "git_fetch_timeout_seconds"):
        if getattr(s, key) < 1:
            raise ConfigError(f"[server] {key} must be >= 1")
    if s.retention_sweep_interval_seconds < 60:
        raise ConfigError("[server] retention_sweep_interval_seconds must be at least 60")
    floor = max(cfg.estimate.sample_days, s.retention_days_failure, s.retention_days_success)
    if s.metadata_retention_days < floor:
        raise ConfigError(
            f"[server] metadata_retention_days must be >= {floor} "
            "(max of estimate.sample_days and retention_days_*)"
        )
    if s.upload_abandon_seconds < s.upload_stall_seconds:
        raise ConfigError("[server] upload_abandon_seconds must be >= upload_stall_seconds")
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
    repo_names = [r.name for r in cfg.repos]
    if len(repo_names) != len(set(repo_names)):
        dupes = sorted({n for n in repo_names if repo_names.count(n) > 1})
        raise ConfigError(f"[[repos]] duplicate repo name(s): {', '.join(dupes)}")
    for r in cfg.repos:
        if not _NAME_RE.match(r.name):
            raise ConfigError(f"[[repos]] name must be a short identifier, got {r.name!r}")
        problem = validate_repo_url(r.url)
        if problem is not None:
            raise ConfigError(f"[[repos]] '{r.name}': {problem}")
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
