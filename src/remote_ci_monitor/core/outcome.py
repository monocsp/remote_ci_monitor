"""잡 요약을 **코드**로 말한다 — 오너 결정 37(2026-09-08), 명세 `docs/m5d-workplan.md` §4.5.

서버는 「무슨 일이 있었는지」를 코드와 **원시 인자**로 남기고, 보여 주는 쪽(웹 화면 · CLI ·
알림 훅)이 각자의 말로 그린다. 서버가 `48 MB` 같은 문자열로 굳혀 보내면 화면이 다시 쓸 수
없기 때문에, 인자는 바이트 수 · 초 · 종료 코드 · 이름 같은 값 그대로 담는다.

잡이 `::rcm::summary::` 로 찍은 문장에는 **코드가 없다**. 팀이 쓴 문장이라 번역 대상이 아니고,
그대로 보여 준다. 그래서 `summary_code` 가 없는 요약이 정상이다.

영어 문장은 여기서 만든다. 지금까지 서버 곳곳에 흩어져 있던 문장을 한 표로 모은 것이라, 이 모듈이
바뀌면 화면 · CLI · 알림이 함께 바뀐다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

#: 코드 → 영어 문장을 만드는 함수. 인자는 `summary_args` 의 원시 값이다.
#: 여기 없는 코드는 「모르는 코드」이고, 보여 주는 쪽은 저장된 `summary` 문장으로 물러선다.
Renderer = Callable[[dict[str, Any]], str]


def _mb(n: Any) -> str:
    """바이트 수를 사람이 읽는 크기로. 서버의 기존 표기와 글자 하나까지 같다."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "?"
    return f"{v / 1e6:.0f} MB" if v >= 1e6 else f"{v / 1e3:.0f} KB"


def _secs(n: Any) -> str:
    """초를 분/초 표기로. 60초 미만은 초, 그 위는 분(내림) — 기존 표기와 같다."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "?"
    return f"{v // 60}m" if v >= 60 else f"{v}s"


def _limit(seconds: Any) -> str:
    """시간 초과 문구. `worker.format_limit` 과 글자까지 같다."""
    if seconds is None:
        return "limit"
    try:
        v = int(seconds)
    except (TypeError, ValueError):
        return "limit"
    if v % 3600 == 0:
        return f"limit {v // 3600}h"
    if v % 60 == 0:
        return f"limit {v // 60}m"
    return f"limit {v}s"


def _s(args: dict[str, Any], key: str, default: str = "?") -> str:
    v = args.get(key)
    return default if v is None else str(v)


#: 서버가 만드는 요약 전부. 새 요약을 추가할 때는 여기 코드부터 만든다.
CODES: dict[str, Renderer] = {
    # ── 취소 · 재시작 ──────────────────────────────────────────────────────
    "cancelled_before_start": lambda a: "cancelled before start",
    "cancelled_by": lambda a: f"cancelled by {_s(a, 'by')}",
    "server_restarted": lambda a: f"server restarted {_s(a, 'at')}",
    "server_restarted_during_upload": lambda a: "server restarted during upload",
    "server_stopped_while_running": lambda a: "server stopped while running",
    # ── 업로드 · 스냅샷 ────────────────────────────────────────────────────
    "upload_abandoned": lambda a: f"upload abandoned after {_secs(a.get('seconds'))}",
    "upload_interrupted": lambda a: f"upload interrupted after {_mb(a.get('bytes'))}",
    "snapshot_too_big": lambda a: f"snapshot {_mb(a.get('bytes'))} exceeds {_mb(a.get('limit'))}",
    "snapshot_rejected": lambda a: f"snapshot rejected: {_s(a, 'kind')}",
    "snapshot_blobs_missing": lambda a: (
        f"snapshot rejected: {_s(a, 'count')} blob(s) missing in upload"
    ),
    "workspace_failed": lambda a: f"workspace failed: {_s(a, 'detail')}",
    # ── 실행 결과 ──────────────────────────────────────────────────────────
    "exit_code": lambda a: f"exit {_s(a, 'code')}",
    "timed_out": lambda a: _limit(a.get("seconds")),
    # ── 워커 ───────────────────────────────────────────────────────────────
    "worker_error": lambda a: f"worker error: {_s(a, 'detail')}",
    "worker_failed": lambda a: "failed on the worker",
    "worker_stopped_while_running": lambda a: "worker stopped while running",
    "worker_restarted_without_job": lambda a: f"worker {_s(a, 'name')} restarted without the job",
    "worker_unreachable": lambda a: f"worker {_s(a, 'name')} unreachable for {_s(a, 'seconds')}s",
    "cancel_unconfirmed": lambda a: "worker did not confirm the cancel",
}

#: 오류 필드의 코드. 파이썬 예외 문자열은 닫힌 집합이 아니라서, 종류만 코드로 말하고
#: 원문은 상세로 남긴다.
ERROR_CODES: dict[str, str] = {
    "internal_error": "internal error",
    "database_unavailable": "database unavailable",
    "sampler_failed": "sampler failed",
}

#: GPU 표본이 없는 이유. 화면이 「GPU — 없음」을 자기 말로 쓸 수 있게 한다.
GPU_NOTE_CODES: dict[str, str] = {
    "no_sampler": "no GPU sampler",
    "no_gpu": "no GPU",
    "sampler_failed": "GPU sampler failed",
}

#: 요약 한 줄의 길이 상한. 저장된 문장이 이보다 길면 자른다(기존 동작과 같다).
MAX_SUMMARY = 200


class OutcomeError(ValueError):
    """알 수 없는 코드로 요약을 만들려 했다. 서버 안에서만 난다(오타 방지)."""


def render(code: str | None, args: dict[str, Any] | None = None) -> str | None:
    """코드를 영어 문장으로. 코드가 없거나 모르는 코드면 `None` — 부르는 쪽이 물러설 수 있게."""
    if not code:
        return None
    fn = CODES.get(code)
    if fn is None:
        return None
    try:
        return fn(args or {})[:MAX_SUMMARY]
    except Exception:  # noqa: BLE001 — 인자가 이상해도 요약 때문에 잡이 깨지면 안 된다
        return None


def summary(code: str, /, **args: Any) -> tuple[str, str, dict[str, Any]]:
    """`(문장, 코드, 인자)`. 서버가 잡을 끝낼 때 세 값을 함께 남긴다."""
    if code not in CODES:
        raise OutcomeError(f"unknown outcome code: {code}")
    clean = {k: v for k, v in args.items() if v is not None}
    text = render(code, clean)
    assert text is not None  # CODES 에 있으므로 None 이 나올 수 없다
    return text, code, clean


def dump_args(args: dict[str, Any] | None) -> str | None:
    """인자를 DB 에 넣을 JSON 으로. 비어 있으면 `None`."""
    if not args:
        return None
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None


def load_args(raw: Any) -> dict[str, Any]:
    """DB 의 JSON 을 인자로. 깨져 있으면 빈 사전 — 요약 때문에 화면이 죽지 않는다."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}
