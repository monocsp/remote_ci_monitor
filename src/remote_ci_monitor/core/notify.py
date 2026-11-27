"""알림의 순수 규칙 — 어떤 규칙이 어떤 잡에 해당하는가 · argv 에 줄 env · 문자열 정화.

I/O 가 없다. 실제 실행(argv · url POST)은 `notify.py`. 사용자 문자열(summary · failed_step ·
requester)은 NUL·제어문자를 지우고 4 KB 로 자른다 — 알림 스크립트의 env 로 들어가는 값이다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import NOTIFY_DEFAULT_STATES
from remote_ci_monitor.core.progress import clean_line
from remote_ci_monitor.core.queue import hold_note

ENV_TEXT_LIMIT = 4096
#: 훅에 넘기는 실패 이름의 최대 개수(M5o 결정 119). 전체는 `GET /jobs/<id>` 의 `failures[]` 에 있다.
MAX_NOTIFY_FAILURES = 20
_KEEP = {"\n", "\t"}
#: 알림의 사건(`RCM_EVENT` · URL 본문 `event`, M5t-b 결정 339-e). 잡 끝 알림과 opt-in 홀드 알림
EVENT_JOB_FINISHED = "job_finished"
EVENT_IRREVERSIBLE_HOLD = "irreversible_hold"
#: `[[notify]] on` 에 적을 수 있는 종료 상태 밖의 값 — 홀드마다 한 번. 기본에는 들지 않는다
NOTIFY_ON_HOLD = EVENT_IRREVERSIBLE_HOLD
#: opt-in 홀드 알림의 중복 막기 행 이름 앞머리 — 규칙 이름에는 `:` 가 없어 잡 끝 행과 겹치지 않는다
HOLD_ROW_PREFIX = "hold:"


@dataclass(frozen=True)
class NotifyRule:
    """`[[notify]]` 하나. argv 와 url 중 정확히 하나."""

    name: str
    on: frozenset[str] = frozenset(NOTIFY_DEFAULT_STATES)
    presets: frozenset[str] | None = None
    argv: tuple[str, ...] | None = None
    url: str | None = None
    timeout_seconds: int = 30


def hold_rules_for(preset: str, rules: Iterable[NotifyRule]) -> list[NotifyRule]:
    """홀드 알림을 받을 규칙 — `on` 에 `irreversible_hold` 를 적었고 `presets` 가 맞는 것(§4.12)."""
    return [
        r for r in rules if NOTIFY_ON_HOLD in r.on and (r.presets is None or preset in r.presets)
    ]


def hold_summary(hold: Mapping[str, Any], job_id: int, preset: str) -> str:
    """opt-in 홀드 알림의 `RCM_SUMMARY` — 영어 한 줄이고 경로가 없다(§4.12)."""
    worker = hold.get("worker")
    where = worker if worker else "the server's local lane"
    check = worker if worker else "the server"
    return (
        f"group {hold.get('group')} is held: job #{int(job_id)} ({preset}) on {where} closed "
        f"without a stop confirmation — check {check}, then rcm release-hold {int(job_id)}"
    )


def hold_body(hold: Mapping[str, Any] | None, job_id: int) -> dict[str, Any]:
    """모든 알림의 URL 본문 `hold` — `{held, group, note}`. 홀드가 없으면 거짓과 빈 값이다."""
    if hold is None:
        return {"held": False, "group": "", "note": ""}
    return {"held": True, "group": str(hold.get("group") or ""), "note": hold_note(job_id)}


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
    """실패 이름 → 훅에 줄 줄 목록. 한 줄 이름이라 개행까지 지우고(`progress.clean_line` —
    `clean_name` 의 규칙에 bidi · 보이지 않는 문자를 더한 것으로, 「사람이 할 일」 note 와 같은
    함수다), 빈 것은 빼고, 앞에서부터 `MAX_NOTIFY_FAILURES` 개."""
    out: list[str] = []
    for name in names:
        line = clean_line(str(name))
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
) -> str:
    """알림에 싣는 보관함 링크(M5p 결정 142 · 144). 없으면 빈 문자열.

    keep 인 잡이면 보관이 아직 안 끝났어도 정해진 URL(`<base>/shelf/<id>/`)을 싣는다 — 링크는 잡
    번호로 정해지고, 훅이 도는 동안 보관이 끝난다. 다만 알림 시점에 이미 **확정적으로 없는** 경우는
    빈 값이다(§15.6): 보관 행이 `failed` · `deleted`, 또는 보관 행이 없는데 묶음 행도 없거나(시작
    전에 취소 · 정책상 안 모음) 묶음이 종결이면서 ready 가 아닌(`none`) 경우다.
    앞부분은 잡 링크(`RCM_URL`)와 같은 `base_url` 이다(결정 120 · 142).
    """
    if not keep or item_state in _SHELF_SETTLED_EMPTY:
        return ""
    if item_state is None:
        # 끝난 잡에 보관 행도 묶음 행도 없으면 확정적으로 없다 — 시작 전에 취소됐거나(P3 격리
        # 검증 D1) 정책상 안 모았다. 원격 잡은 끝날 때 묶음 행이 `uploading` 으로 이미 있다
        if bundle_state is None:
            return ""
        if bundle_state != art.READY and bundle_state not in _BUNDLE_ON_THE_WAY:
            return ""
    return f"{base_url.rstrip('/')}/shelf/{int(job_id)}/"


def notify_env(
    job_row: dict[str, Any],
    rule_name: str,
    *,
    failures: Sequence[str] = (),
    failures_truncated: bool = False,
    shelf_url: str = "",
    event: str = EVENT_JOB_FINISHED,
    hold: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """알림 argv 에 줄 env. 값은 전부 문자열, 없는 값은 빈 문자열.

    `failures` 는 그 잡의 실패 이름 대장(`job_failures`, 잡이 찍은 순서 — M5o 결정 119).
    `shelf_url` 은 keep 인 잡의 보관함 링크(`shelf_url()` — 아니면 빈 값, M5p 결정 144).
    `hold` 는 알림 행을 만들 때 **한 번** 읽은 그 잡의 열린 홀드(`store.hold_of`, M5t-b 339-e)다.
    없으면 None 이고 `RCM_HOLD=0` 과 빈 그룹 · 빈 한 줄이 된다 — 키 집합은 늘 같다."""
    held = hold is not None  # mutcheck: job-end-hold-env-missing
    requester = job_row.get("requester") or {}
    if isinstance(requester, dict):
        requester_label = requester.get("label") or requester.get("name") or ""
    else:
        requester_label = str(requester)
    exit_code = job_row.get("exit_code")
    seconds = job_row.get("job_seconds")
    source = job_row.get("source") if isinstance(job_row.get("source"), dict) else {}
    action = job_row.get("action") if isinstance(job_row.get("action"), dict) else {}
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
        # 「사람이 할 일」 표식(M5s 결정 213): 잡이 찍은 마지막 것. 없으면 둘 다 빈 값이다
        "RCM_ACTION_CODE": sanitize_text(action.get("code")),
        "RCM_ACTION_NOTE": sanitize_text(action.get("note")),
        # 잡이 돈 머신(M5t-a 결정 248 f) — 잡 행의 `ran_on`. 시작하지 못했거나 모르면 빈 값이다
        "RCM_MACHINE": sanitize_text(job_row.get("ran_on")),
        # 그룹 홀드(M5t-b 결정 339-e): 사건 이름과 그 잡의 열린 홀드. 홀드가 없으면 0 과 빈 값이다
        "RCM_EVENT": sanitize_text(event),
        "RCM_HOLD": "1" if held else "0",
        "RCM_HOLD_GROUP": sanitize_text(hold.get("group")) if held else "",  # type: ignore[union-attr]
        "RCM_HOLD_NOTE": hold_note(int(job_row.get("id") or 0)) if held else "",
    }


def hold_env(
    job_row: dict[str, Any], rule_name: str, hold: Mapping[str, Any], **kw: Any
) -> dict[str, str]:
    """opt-in 홀드 알림의 env — 잡 알림 env(`RCM_EVENT=irreversible_hold`)에 `RCM_HOLD_CAUSE` ·
    `RCM_HOLD_WORKER` 를 더하고 `RCM_SUMMARY` 를 홀드 한 줄로 바꾼다(§4.12). 워커는 로컬 레인이면
    빈 값이다."""
    env = notify_env(job_row, rule_name, event=EVENT_IRREVERSIBLE_HOLD, hold=hold, **kw)
    # 쓰는 키다 — `env[...] =` 꼴은 `tests/test_docs_m5t.py` 가 「셸에서 읽는 이름」으로 센다
    summary = hold_summary(hold, int(job_row.get("id") or 0), str(job_row.get("preset") or ""))
    env.update(
        {
            "RCM_SUMMARY": sanitize_text(summary),
            "RCM_HOLD_CAUSE": sanitize_text(hold.get("cause")),
            "RCM_HOLD_WORKER": sanitize_text(hold.get("worker")),
        }
    )
    return env
