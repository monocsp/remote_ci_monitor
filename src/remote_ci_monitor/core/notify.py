"""알림의 순수 규칙 — 어떤 규칙이 어떤 잡에 해당하는가 · argv 에 줄 env · 문자열 정화.

I/O 가 없다. 실제 실행(argv · url POST)은 `notify.py`. 사용자 문자열(summary · failed_step ·
requester)은 NUL·제어문자를 지우고 4 KB 로 자른다 — 알림 스크립트의 env 로 들어가는 값이다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import TERMINAL_STATES

ENV_TEXT_LIMIT = 4096
#: 훅에 넘기는 실패 이름의 최대 개수(M5o 결정 119). 전체는 `GET /jobs/<id>` 의 `failures[]` 에 있다.
MAX_NOTIFY_FAILURES = 20
_KEEP = {"\n", "\t"}


@dataclass(frozen=True)
class NotifyRule:
    """`[[notify]]` 하나. argv 와 url 중 정확히 하나."""

    name: str
    on: frozenset[str] = frozenset(TERMINAL_STATES)
    presets: frozenset[str] | None = None
    argv: tuple[str, ...] | None = None
    url: str | None = None
    timeout_seconds: int = 30


def rules_for(job_state: str, preset: str, rules: Iterable[NotifyRule]) -> list[NotifyRule]:
    """상태·프리셋에 해당하는 규칙을 설정 순서대로."""
    out: list[NotifyRule] = []
    for rule in rules:
        if job_state not in rule.on:
            continue
        if rule.presets is not None and preset not in rule.presets:
            continue
        out.append(rule)
    return out


def sanitize_text(value: Any, limit: int = ENV_TEXT_LIMIT) -> str:
    """None → "", 제어문자(개행·탭 제외) 제거, UTF-8 경계를 지켜 limit 바이트로 자른다."""
    if value is None:
        return ""
    text = str(value)
    # 제어문자(개행·탭 제외)와 홀로 남은 서로게이트(JSON "\udcff" — encode 가 죽는다)를 지운다
    cleaned = "".join(
        ch
        for ch in text
        if ch in _KEEP or (ord(ch) >= 0x20 and ord(ch) != 0x7F and not 0xD800 <= ord(ch) <= 0xDFFF)
    )
    data = cleaned.encode("utf-8")
    if len(data) <= limit:
        return cleaned
    return data[:limit].decode("utf-8", errors="ignore")


def failure_lines(names: Iterable[str]) -> list[str]:
    """실패 이름 → 훅에 줄 줄 목록. 한 줄 이름이라 개행까지 지우고(`progress.clean_name` 과 같은
    규칙에 bidi · 보이지 않는 문자를 더한다), 빈 것은 빼고, 앞에서부터 `MAX_NOTIFY_FAILURES` 개."""
    out: list[str] = []
    for name in names:
        line = "".join(
            ch
            for ch in str(name)
            if ord(ch) >= 0x20
            and not 0x7F <= ord(ch) <= 0x9F  # C1 — NEL 은 `splitlines()` 가 줄로 나눈다
            and ord(ch) != 0x061C
            and not 0xD800 <= ord(ch) <= 0xDFFF
            and not 0x200B <= ord(ch) <= 0x200F
            and not 0x2028 <= ord(ch) <= 0x202E  # 줄 · 문단 구분자와 bidi
            and not 0x2060 <= ord(ch) <= 0x2069
            and ord(ch) != 0xFEFF
        ).strip()
        if line:
            out.append(line)
        if len(out) >= MAX_NOTIFY_FAILURES:
            break
    return out


def failure_count(names: Sequence[str], truncated: bool) -> str:
    """`RCM_FAILURE_COUNT` — 대장 행의 수. 잘렸으면 `N+`(정확한 수를 주장하지 않는다). 없으면 ""."""
    if not names:
        return ""
    return f"{len(names)}+" if truncated else str(len(names))


def _joined_lines(lines: list[str], limit: int = ENV_TEXT_LIMIT) -> str:
    """줄을 개행으로 묶되 `limit` 바이트를 넘으면 **줄 단위로** 뒤에서 버린다 — 반쪽 이름은 없다."""
    kept = list(lines)
    while kept and len("\n".join(kept).encode("utf-8")) > limit:
        kept.pop()
    return "\n".join(kept)


#: 묶음이 아직 오는 중인 상태 — 보관 행이 곧 선다(공개 상태 `waiting`, M5p §15.1).
_BUNDLE_ON_THE_WAY = (art.PENDING, art.COLLECTING, art.UPLOADING)
#: 보관이 확정 실패인 행 — 링크가 404 · 410 이라 싣지 않는다(§15.6).
_SHELF_SETTLED_EMPTY = ("failed", "deleted")


def shelf_url(
    base_url: str,
    job_id: int,
    *,
    keep: bool,
    item_state: str | None,
    bundle_state: str | None,
    collected: bool = True,
) -> str:
    """알림에 싣는 보관함 링크(M5p 결정 142 · 144). 없으면 빈 문자열.

    keep 인 잡이면 보관이 아직 안 끝났어도 정해진 URL(`<base>/shelf/<id>/`)을 싣는다 — 링크는 잡
    번호로 정해지고, 훅이 도는 동안 보관이 끝난다. 다만 알림 시점에 이미 **확정적으로 없는** 경우는
    빈 값이다(§15.6): 보관 행이 `failed` · `deleted`, 또는 행이 없는데 묶음이 종결이면서 ready 가
    아니거나(`none`), 정책상 모으지 않은 잡(`collected=False` — `artifacts_on = "failure"` 인 성공).
    앞부분은 잡 링크(`RCM_URL`)와 같은 `base_url` 이다(결정 120 · 142).
    """
    if not keep or item_state in _SHELF_SETTLED_EMPTY:
        return ""
    if item_state is None:
        if bundle_state is None and not collected:
            return ""
        if bundle_state is not None and bundle_state != art.READY:
            if bundle_state not in _BUNDLE_ON_THE_WAY:
                return ""
    return f"{base_url.rstrip('/')}/shelf/{int(job_id)}/"


def notify_env(
    job_row: dict[str, Any],
    rule_name: str,
    *,
    failures: Sequence[str] = (),
    failures_truncated: bool = False,
    shelf_url: str = "",
) -> dict[str, str]:
    """알림 argv 에 줄 env. 값은 전부 문자열, 없는 값은 빈 문자열.

    `failures` 는 그 잡의 실패 이름 대장(`job_failures`, 잡이 찍은 순서 — M5o 결정 119).
    `shelf_url` 은 keep 인 잡의 보관함 링크(`shelf_url()` — 아니면 빈 값, M5p 결정 144)."""
    requester = job_row.get("requester") or {}
    if isinstance(requester, dict):
        requester_label = requester.get("label") or requester.get("name") or ""
    else:
        requester_label = str(requester)
    exit_code = job_row.get("exit_code")
    seconds = job_row.get("job_seconds")
    source = job_row.get("source") if isinstance(job_row.get("source"), dict) else {}
    return {
        "RCM_JOB_ID": str(job_row.get("id") or ""),
        "RCM_STATE": sanitize_text(job_row.get("state")),
        "RCM_PRESET": sanitize_text(job_row.get("preset")),
        "RCM_KEY": sanitize_text(job_row.get("key")),
        "RCM_REQUESTER": sanitize_text(requester_label),
        "RCM_SUMMARY": sanitize_text(job_row.get("summary")),
        "RCM_FAILED_STEP": sanitize_text(job_row.get("failed_step")),
        # 선언된 실패 스텝과 「끝났을 때 어디였나」는 다른 사실이다 — 훅이 둘을 따로 읽는다 (M5h)
        "RCM_LAST_STEP": sanitize_text(job_row.get("last_step")),
        "RCM_EXIT_CODE": "" if exit_code is None else str(exit_code),
        "RCM_JOB_SECONDS": "" if seconds is None else str(seconds),
        "RCM_URL": sanitize_text(job_row.get("url")),
        "RCM_NOTIFY": sanitize_text(rule_name),
        # 소스(M5 실배치에서 필요해짐): 훅이 커밋 status 를 남기려면 sha 가 있어야 한다.
        # tree 잡은 sha 가 없고 base_sha + dirty 로 말한다; git_ref 잡은 ref + sha.
        "RCM_SOURCE_MODE": sanitize_text(source.get("mode")),
        "RCM_SOURCE_REF": sanitize_text(source.get("ref")),
        "RCM_SOURCE_SHA": sanitize_text(source.get("sha")),
        "RCM_SOURCE_BASE_SHA": sanitize_text(source.get("base_sha")),
        "RCM_SOURCE_DIRTY": ""
        if source.get("dirty") is None
        else ("1" if source.get("dirty") else "0"),
        "RCM_SOURCE_REPO": sanitize_text(source.get("repo")),
        "RCM_INPUTS": sanitize_text(json.dumps(job_row.get("inputs") or {}, sort_keys=True)),
        # 실패 이름(M5o 결정 119): 줄바꿈으로 묶어 최대 20개, 수는 전부(잘렸으면 `N+`)
        "RCM_FAILURES": _joined_lines(failure_lines(failures)),
        "RCM_FAILURE_COUNT": failure_count(failures, failures_truncated),
        # 보관함(M5p 결정 144): keep 인 잡의 링크. 키는 늘 있고 keep 이 아니면 빈 값이다
        "RCM_SHELF_URL": sanitize_text(shelf_url),
    }
