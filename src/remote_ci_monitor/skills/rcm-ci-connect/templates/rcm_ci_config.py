"""rcm-ci-connect: `.rcm/ci.toml` 파서 · 모드 결정 · 커밋 상태 문장 · 종료 대응표 · GitHub 요청.

GitHub 러너의 대기 잡(`rcm_ci_wait.py`), 빌드 머신의 감시 스크립트(`rcm_ci_watch.py`)와 알림
훅(`rcm_ci_status.py`)이 같은 폴더에서 이 모듈을 import 한다. 셋이 같은 함수로 모드를 정하고
같은 표로 상태를 올려야 서로 엇갈리지 않는다. 표준 라이브러리만 쓴다(Python 3.11+, `tomllib`).

계약은 remote_ci_monitor 의 docs/m5r-workplan.md §2.2 ~ §2.4 · §2.7 · §5.2 다.
"""

from __future__ import annotations

import email.utils
import http.client
import json
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MODES = ("github", "rcm", "both")
FALLBACKS = ("github", "none")
WHERE = ("github", "rcm", "rcm-only")
WORDS = ("queued", "running", "passed", "failed", "unavailable", "superseded", "taken-over")
DEFAULT_CONTEXT = "rcm/ci"
DESCRIPTION_LIMIT = 140
CONFIG_PATH = ".rcm/ci.toml"
EX_TEMPFAIL = 75  # 잡 스크립트의 「환경 탓」 종료 코드
BOT_AUTHORS = ("dependabot[bot]",)
#: `taken-over` 는 이 계정이 올린 것도 믿는다(대기 잡의 GITHUB_TOKEN)
ACTIONS_BOT = "github-actions[bot]"
#: GitHub 요청 하나의 시한(초). 시험이 줄일 수 있게 부를 때마다 모듈 값을 읽는다
HTTP_TIMEOUT: float = 15

PRESET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
CONTEXT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,99}$")
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")
#: core/gitref.py 와 같은 보수적 부분집합 — 옵션처럼 보이는 값 · `..` · 끝의 `/` · `.lock` 을 막는다
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
SECONDS = {
    "pickup_seconds": (60, 3600, 300),
    "queue_seconds": (60, 86400, 1800),
    "run_seconds": (60, 86400, 3600),
    "poll_seconds": (10, 300, 30),
}
TOP_KEYS = ("version", "mode", "fallback", "rcm", "platforms")
RCM_KEYS = ("preset", "context", "priority", "push", "creators", *SECONDS)
PRIORITIES = ("low", "normal", "high")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]|\x1b\[[0-9;]*[A-Za-z]")


class CiConfigError(Exception):
    """`.rcm/ci.toml` 이 틀렸다. 문장에 표와 키 이름이 들어간다."""


@dataclass(frozen=True)
class RcmSide:
    preset: str
    context: str = DEFAULT_CONTEXT
    priority: str | None = None
    push: tuple[str, ...] = ()
    creators: tuple[str, ...] = ()
    pickup_seconds: int = 300
    queue_seconds: int = 1800
    run_seconds: int = 3600
    poll_seconds: int = 30


@dataclass(frozen=True)
class CiConfig:
    mode: str
    fallback: str
    rcm: RcmSide | None
    platforms: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Route:
    mode: str
    why: str  # schedule · fork · override · no_file · bad_file · push_not_listed · default
    now: tuple[str, ...]
    late: tuple[str, ...]
    wait: bool
    fallback: str
    context: str | None
    error: str | None = None  # bad_file 일 때 CiConfigError 문장


# ── 파서 ──────────────────────────────────────────────────────────────────────


def _unknown(table: str, raw: dict[str, Any], allowed: Sequence[str]) -> None:
    extra = sorted(k for k in raw if k not in allowed)
    if extra:
        raise CiConfigError(f"unknown key(s) in {table}: {', '.join(extra)}")


def _choice(table: str, key: str, value: Any, allowed: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise CiConfigError(f"{table} {key} = {value!r}: expected one of {', '.join(allowed)}")
    return value


def _strings(table: str, key: str, value: Any, pattern: re.Pattern[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise CiConfigError(f"{table} {key} must be a list of strings")
    for v in value:
        bad = not pattern.match(v) or ".." in v or "//" in v or v.endswith(("/", ".lock", "."))
        if bad:
            raise CiConfigError(f"{table} {key}: {v!r} is not an allowed name")
    return tuple(value)


def _rcm_side(raw: Any) -> RcmSide:
    t = "[rcm]"
    if not isinstance(raw, dict):
        raise CiConfigError(f"{t} must be a table")
    _unknown(t, raw, RCM_KEYS)
    preset = raw.get("preset")
    if not isinstance(preset, str) or not PRESET_NAME.match(preset):
        raise CiConfigError(f"{t} preset is required: a preset name like rcm-ci (64 chars max)")
    context = raw.get("context", DEFAULT_CONTEXT)
    if not isinstance(context, str) or not CONTEXT_NAME.match(context):
        raise CiConfigError(f"{t} context = {context!r}: letters, digits, _ . / - (100 max)")
    priority = raw.get("priority")
    if priority is not None:
        priority = _choice(t, "priority", priority, PRIORITIES)
    seconds: dict[str, int] = {}
    for key, (lo, hi, default) in SECONDS.items():
        v = raw.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise CiConfigError(f"{t} {key} = {v!r}: expected an integer {lo}..{hi}")
        seconds[key] = v
    return RcmSide(
        preset=preset,
        context=context,
        priority=priority,
        push=_strings(t, "push", raw.get("push", []), BRANCH_NAME),
        creators=_strings(t, "creators", raw.get("creators", []), LOGIN),
        **seconds,
    )


def parse(text: str, *, legs: Sequence[str] | None) -> CiConfig:
    """`.rcm/ci.toml` 의 글을 읽는다. `legs` 는 워크플로가 넘긴 갈래 이름이다.

    `legs` 가 None 이면(갈래를 모르는 감시 스크립트) 갈래 이름 검사와 대체 갈래 검사를 건너뛴다.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise CiConfigError(f"not valid TOML: {e}") from None
    _unknown("the top level", raw, TOP_KEYS)
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise CiConfigError("version is required: version = 1")
    if version > SCHEMA_VERSION:
        raise CiConfigError(
            f"version = {version} is newer than this template (it knows {SCHEMA_VERSION}); "
            "update the rcm-ci-connect scripts"
        )
    if version != SCHEMA_VERSION:
        raise CiConfigError(f"version = {version}: expected {SCHEMA_VERSION}")
    mode = _choice("the top level", "mode", raw.get("mode", "github"), MODES)
    fallback = _choice("the top level", "fallback", raw.get("fallback", "github"), FALLBACKS)
    side = _rcm_side(raw["rcm"]) if "rcm" in raw else None
    if side is None and mode != "github":
        raise CiConfigError(f'[rcm] is required when mode = "{mode}" (at least preset = "...")')
    plats_raw = raw.get("platforms", {})
    if not isinstance(plats_raw, dict):
        raise CiConfigError("[platforms] must be a table")
    platforms: dict[str, str] = {}
    for leg, where in plats_raw.items():
        if legs is not None and leg not in legs:
            raise CiConfigError(
                f"[platforms] {leg} is not a leg of this workflow ({', '.join(legs)})"
            )
        platforms[leg] = _choice("[platforms]", leg, where, WHERE)
    if legs is not None and mode == "rcm" and fallback == "github":
        if not any(platforms.get(leg, "rcm") == "rcm" for leg in legs):
            raise CiConfigError(
                '[platforms] no leg is "rcm", so GitHub has nothing to run when rcm cannot '
                'answer: write fallback = "none" to say so'
            )
    return CiConfig(mode=mode, fallback=fallback, rcm=side, platforms=platforms)


def load(path: Path, *, legs: Sequence[str] | None) -> CiConfig | None:
    """파일이 없으면 None. 틀렸으면 CiConfigError."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return parse(text, legs=legs)


# ── 모드 결정 ─────────────────────────────────────────────────────────────────


def is_fork(base_repo: str, head_repo: str | None, author: str | None) -> bool:
    """남의 코드다: 다른 레포 · 지워진 포크(head 레포 없음) · 봇이 연 PR."""
    if not head_repo or head_repo != base_repo:
        return True
    return author in BOT_AUTHORS


def resolve(
    cfg: CiConfig | None,
    *,
    event: str,
    base: str,
    fork: bool,
    override: str | None,
    legs: Sequence[str],
    error: str | None = None,
) -> Route:
    """위에서부터 처음 걸리는 것이 이긴다(명세 §2.3). `override` 의 모르는 값은 없는 것으로 본다."""
    legs = tuple(legs)
    if event == "schedule":
        return _github(legs, "schedule", cfg)
    if fork:
        return _github(legs, "fork", cfg)
    if override in MODES:
        side = cfg.rcm if cfg is not None and cfg.rcm is not None else None
        base_cfg = cfg or CiConfig(mode=override, fallback="github", rcm=side, platforms={})
        return _route(base_cfg, override, "override", legs)
    if error is not None:
        return Route("github", "bad_file", legs, (), False, "github", None, error)
    if cfg is None:
        return _github(legs, "no_file", cfg)
    if event == "push" and (cfg.rcm is None or base not in cfg.rcm.push):
        return _github(legs, "push_not_listed", cfg)
    return _route(cfg, cfg.mode, "default", legs)


def _github(legs: tuple[str, ...], why: str, cfg: CiConfig | None) -> Route:
    return Route("github", why, legs, (), False, "github", None)


def _route(cfg: CiConfig, mode: str, why: str, legs: tuple[str, ...]) -> Route:
    context = cfg.rcm.context if cfg.rcm is not None else DEFAULT_CONTEXT
    if mode == "github":
        return Route("github", why, legs, (), False, cfg.fallback, None)
    if mode == "both":
        return Route("both", why, legs, (), True, cfg.fallback, context)

    def where(leg: str) -> str:
        return cfg.platforms.get(leg, "rcm")

    now = tuple(leg for leg in legs if where(leg) == "github")
    late = tuple(leg for leg in legs if where(leg) == "rcm") if cfg.fallback == "github" else ()
    return Route("rcm", why, now, late, True, cfg.fallback, context)


# ── 상태 문장 ─────────────────────────────────────────────────────────────────


def clean(text: Any) -> str:
    """제어 문자를 지우고 공백을 한 칸으로."""
    return " ".join(_CONTROL.sub(" ", str(text or "")).split())


def annotation(text: Any) -> str:
    """워크플로 명령(`::warning::` 등)에 실을 문장. 줄바꿈과 `::` 를 없애 명령을 못 끼운다."""
    return clean(text).replace("::", ": ")


def describe(word: str, job: int | None, text: str) -> str:
    """`<낱말> · #<잡> · <문장>` — 잡 번호가 없으면 가운데 칸을 뺀다. 140자에서 자른다."""
    parts = [word]
    if job is not None:
        parts.append(f"#{job}")
    body = clean(text)
    if body:
        parts.append(body)
    out = " · ".join(parts)
    if len(out) > DESCRIPTION_LIMIT:
        out = out[: DESCRIPTION_LIMIT - 1] + "…"
    return out


def read_word(description: str | None) -> tuple[str | None, int | None]:
    """설명의 첫 낱말과 잡 번호. 없으면 (None, None)."""
    parts = [p.strip() for p in (description or "").split(" · ")]
    if not parts or not parts[0]:
        return None, None
    job = None
    if len(parts) > 1 and re.fullmatch(r"#\d+", parts[1]):
        job = int(parts[1][1:])
    return parts[0], job


def terminal_status(
    state: str,
    exit_code: int | None,
    cancelled_by: str | None,
    *,
    job: int,
    summary: str,
    step: str | None,
    successor: int | None,
) -> tuple[str, str] | None:
    """끝난 잡 → (커밋 상태, 설명). 끝나지 않았으면 None(명세 §2.7 의 표)."""
    if state == "succeeded":
        return "success", describe("passed", job, summary)
    if state == "failed":
        if exit_code == EX_TEMPFAIL:
            return "error", describe("unavailable", job, f"infra at {step}" if step else "infra")
        if exit_code is None:
            return "error", describe("unavailable", job, "could not prepare")
        return "failure", describe("failed", job, step or summary or f"exit {exit_code}")
    if state == "timed_out":
        return "error", describe("unavailable", job, "timed out")
    if state == "lost":
        return "error", describe("unavailable", job, "lost")
    if state == "cancelled":
        if cancelled_by == "superseded":
            text = f"by #{successor}" if successor is not None else "by a newer commit"
            return "error", describe("superseded", job, text)
        return "error", describe("unavailable", job, "cancelled")
    return None


def created_at(status: dict[str, Any]) -> float | None:
    raw = status.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def latest(
    statuses: Sequence[dict[str, Any]] | None,
    context: str,
    creators: Sequence[str] = (),
    *,
    skip_taken_over: bool = False,
) -> dict[str, Any] | None:
    """그 context 의 가장 새 상태 하나(목록은 새것부터다). `creators` 를 정했으면 그 계정의 것만,
    `taken-over` 는 Actions 봇이 올린 것도 본다."""
    for s in statuses or ():
        if not isinstance(s, dict) or s.get("context") != context:
            continue
        word, _ = read_word(s.get("description"))
        login = (
            ((s.get("creator") or {}).get("login")) if isinstance(s.get("creator"), dict) else None
        )
        if word == "taken-over":
            if skip_taken_over:
                continue
            if creators and login not in creators and login != ACTIONS_BOT:
                continue
        elif creators and login not in creators:
            continue
        return s
    return None


# ── GitHub 요청 ───────────────────────────────────────────────────────────────


@dataclass
class Reply:
    status: int  # 0 = 닿지 못했다(시간 초과 포함)
    data: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def date(self) -> float | None:
        """GitHub 응답의 Date 헤더(epoch 초). 러너의 시계가 아니라 이것으로 경과를 잰다."""
        raw = self.headers.get("date")
        if not raw:
            return None
        try:
            return email.utils.parsedate_to_datetime(raw).timestamp()
        except (TypeError, ValueError):
            return None


class GitHubApi:
    """레포 하나에 말하는 작은 REST 클라이언트. 토큰은 요청 헤더에만 싣고 어디에도 찍지 않는다."""

    def __init__(self, api_url: str, repo: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.repo = repo
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        etag: str | None = None,
        timeout: float | None = None,
    ) -> Reply:
        url = f"{self.api_url}/repos/{self.repo}{path}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "rcm-ci-connect")
        req.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if etag:
            req.add_header("If-None-Match", etag)
        limit = HTTP_TIMEOUT if timeout is None else min(timeout, HTTP_TIMEOUT)
        try:
            with urllib.request.urlopen(req, timeout=max(limit, 0.05)) as resp:
                raw = resp.read()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return Reply(resp.status, _json(raw), headers)
        except urllib.error.HTTPError as e:
            headers = {k.lower(): v for k, v in (e.headers or {}).items()}
            if e.code == 304:
                return Reply(304, None, headers)
            try:
                raw = e.read()
            except OSError:
                raw = b""
            msg = _json(raw)
            text = msg.get("message", "") if isinstance(msg, dict) else ""
            return Reply(e.code, msg, headers, f"HTTP {e.code} {clean(text)[:80]}".strip())
        except (TimeoutError, urllib.error.URLError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", e)
            return Reply(0, None, {}, f"unreachable: {clean(type(reason).__name__)}")

    def statuses(self, sha: str, *, etag: str | None = None, timeout: float | None = None):
        return self.request(
            "GET", f"/commits/{sha}/statuses?per_page=100", etag=etag, timeout=timeout
        )

    def post_status(
        self,
        sha: str,
        state: str,
        context: str,
        description: str,
        *,
        target_url: str | None = None,
        timeout: float | None = None,
    ) -> Reply:
        body: dict[str, Any] = {"state": state, "context": context, "description": description}
        if target_url:
            body["target_url"] = target_url
        return self.request("POST", f"/statuses/{sha}", body, timeout=timeout)

    def contents(self, path: str, *, ref: str | None, timeout: float | None = None) -> Reply:
        q = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
        return self.request("GET", f"/contents/{path}{q}", timeout=timeout)

    def variable(self, name: str, *, timeout: float | None = None) -> Reply:
        return self.request("GET", f"/actions/variables/{name}", timeout=timeout)


def _json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def read_token(path: str | Path) -> str:
    """토큰 파일의 첫 줄. 비어 있으면 ValueError(값은 문장에 넣지 않는다)."""
    text = Path(path).expanduser().read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"token file {path} is empty")
    return text.splitlines()[0].strip()
