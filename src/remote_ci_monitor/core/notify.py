"""알림의 순수 규칙 — 어떤 규칙이 어떤 잡에 해당하는가 · argv 에 줄 env · 문자열 정화.

I/O 가 없다. 실제 실행(argv · url POST)은 `notify.py`. 사용자 문자열(summary · failed_step ·
requester)은 NUL·제어문자를 지우고 4 KB 로 자른다 — 알림 스크립트의 env 로 들어가는 값이다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from remote_ci_monitor.core.model import TERMINAL_STATES

ENV_TEXT_LIMIT = 4096
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


def notify_env(job_row: dict[str, Any], rule_name: str) -> dict[str, str]:
    """알림 argv 에 줄 env. 값은 전부 문자열, 없는 값은 빈 문자열."""
    requester = job_row.get("requester") or {}
    if isinstance(requester, dict):
        requester_label = requester.get("label") or requester.get("name") or ""
    else:
        requester_label = str(requester)
    exit_code = job_row.get("exit_code")
    seconds = job_row.get("job_seconds")
    return {
        "RCM_JOB_ID": str(job_row.get("id") or ""),
        "RCM_STATE": sanitize_text(job_row.get("state")),
        "RCM_PRESET": sanitize_text(job_row.get("preset")),
        "RCM_KEY": sanitize_text(job_row.get("key")),
        "RCM_REQUESTER": sanitize_text(requester_label),
        "RCM_SUMMARY": sanitize_text(job_row.get("summary")),
        "RCM_FAILED_STEP": sanitize_text(job_row.get("failed_step")),
        "RCM_EXIT_CODE": "" if exit_code is None else str(exit_code),
        "RCM_JOB_SECONDS": "" if seconds is None else str(seconds),
        "RCM_URL": sanitize_text(job_row.get("url")),
        "RCM_NOTIFY": sanitize_text(rule_name),
    }
