"""머신의 능력 — 재고, 묶고, 판정한다(M5n · `docs/m5n-workplan.md` §4).

여기는 순수 계층이다. 프로브를 **돌리는** 일(`hostsample.run_probes`)과 도구를 **찾는** 일
(`runner.measure_tools`)은 I/O 쪽에 있고, 이 모듈은 그 결과를 받아

  ① 프로브 출력 → 정규화된 한 줄(`normalize_probe_output`)
  ② 프로브 묶음 → 지문과 그 해시(`settle_fingerprint` · `fingerprint_hash`)
  ③ 점검 잡 기록 → 점검 상태(`check_state`)
  ④ (프리셋, 보고) → 판정(`verdict` — §4.3 의 표를 위에서부터)

만 한다. 시계는 인자다.

증인은 2026-11-06 의 Xcode 사고다(§1.3). 그날 바뀐 것은 머신이었지 코드가 아니었는데, rcm 은
「도구가 있다」만 보고 열흘 동안 깨진 머신으로 iOS QA 를 보냈다. 지문이 바뀌면 그 머신은
점검을 **현재 지문으로** 다시 통과하기 전까지 `checks` 가 붙은 일을 받지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from remote_ci_monitor.core.model import Preset

LOCAL_WORKER = "@local"

PROBE_OK = "ok"
PROBE_ERROR = "error"
PROBE_MISSING = "missing"
PROBE_TIMEOUT = "timeout"

ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
UNKNOWN = "unknown"

CHECK_PASSED = "passed"
CHECK_RUNNING = "running"
CHECK_FAILED = "failed"
CHECK_NEEDED = "needed"

CODE_REPORT_MISSING = "report_missing"
CODE_REPORT_STALE = "report_stale"
CODE_TOOL_MISSING = "tool_missing"
CODE_WORKSPACE_TOOL = "workspace_tool"
CODE_CHECK_FAILED = "check_failed"
CODE_CHECK_RUNNING = "check_running"
CODE_CHECK_NEEDED = "check_needed"
CODE_FINGERPRINT_UNKNOWN = "fingerprint_unknown"
CODE_OK = "ok"

#: 정규화한 값의 상한(§4.1). 화면 한 줄에 들어가야 하고, 출력이 길수록 매번 흔들린다.
PROBE_VALUE_MAX = 120
#: 앞에서 몇 줄을 쓰나. `xcodebuild -version` 은 두 줄(버전 · 빌드)이다.
PROBE_LINES = 2

# C0 제어문자(탭·줄바꿈 포함) · DEL · C1. ANSI 색의 ESC 도 여기서 빠진다.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

_ACTIVE_JOB_STATES = frozenset({"uploading", "queued", "running", "cancelling"})
_FAILED_JOB_STATES = frozenset({"failed", "timed_out"})


@dataclass(frozen=True)
class ProbeResult:
    state: str  # PROBE_*
    #: ok → 정규화 문자열 · error → "exit N" · missing/timeout → None
    value: str | None
    #: timeout 이라 앞 값을 이어 쓴 것(결정 90)
    stale: bool = False


@dataclass(frozen=True)
class Fingerprint:
    #: timeout 인 프로브는 앞 값(stale=True)으로 채워진다. 앞 값이 없으면 timeout 그대로.
    probes: Mapping[str, ProbeResult]
    #: 앞 값 없는 timeout 이 하나라도 있으면 None(모름)
    hash: str | None
    stale: bool


@dataclass(frozen=True)
class PresetFacts:
    missing_tools: tuple[str, ...] = ()
    #: PATH 의 상대 항목에만 있을 수 있는 도구 — 작업 공간이 와야 안다(결정 93)
    workspace_tools: tuple[str, ...] = ()
    missing_passthrough: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityReport:
    worker: str
    generation: str
    host_key: str
    accept_any: bool
    fingerprint: Fingerprint
    presets: Mapping[str, PresetFacts]
    measured_at: datetime


@dataclass(frozen=True)
class CheckRecord:
    preset: str
    job_id: int
    job_state: str  # 잡 상태 그대로
    fingerprint_hash: str | None


@dataclass(frozen=True)
class Verdict:
    state: str  # ELIGIBLE · INELIGIBLE · UNKNOWN
    code: str
    detail: Mapping[str, Any] = field(default_factory=dict)


# ── ① 프로브 출력 ─────────────────────────────────────────────────────────────


def normalize_probe_output(
    output: bytes | str | None,
    returncode: int | None,
    *,
    missing: bool = False,
    timed_out: bool = False,
) -> ProbeResult:
    """프로브 한 번의 결과를 지문에 넣을 모양으로(§4.1).

    실패(exit ≠ 0)의 출력은 **버린다** — 실패 문구에는 경로가 섞이기 쉽고 값이 매번 흔들려
    지문을 출렁이게 한다. 코드만 남긴다.
    """
    if missing:
        return ProbeResult(PROBE_MISSING, None)
    if timed_out:
        return ProbeResult(PROBE_TIMEOUT, None)
    if returncode is None or returncode != 0:
        return ProbeResult(PROBE_ERROR, f"exit {returncode}")
    if output is None:
        return ProbeResult(PROBE_OK, "")
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    lines: list[str] = []
    for raw in text.splitlines():
        line = _CONTROL.sub("", raw).strip()
        if line:
            lines.append(line)
        if len(lines) >= PROBE_LINES:
            break
    return ProbeResult(PROBE_OK, " · ".join(lines)[:PROBE_VALUE_MAX])


# ── ② 지문 ───────────────────────────────────────────────────────────────────


def fingerprint_hash(probes: Mapping[str, ProbeResult]) -> str:
    """(이름, 상태, 값) 의 정렬된 JSON 의 sha256 앞 16자. timeout 은 안 들어간다(결정 90).

    `stale` 은 안 들어간다 — 이어 쓴 값은 같은 값이다. 경계가 있는 JSON 으로 묶으므로
    `ab`+`c` 와 `a`+`bc` 가 같아지지 않는다.
    """
    items = sorted(
        [name, p.state, p.value] for name, p in probes.items() if p.state != PROBE_TIMEOUT
    )
    text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def settle_fingerprint(
    current: Mapping[str, ProbeResult], previous: Mapping[str, ProbeResult] | None
) -> Fingerprint:
    """이번 측정을 지문으로 굳힌다. timeout 은 앞 값을 잇고 `stale`, 앞 값이 없으면 모른다.

    앞 보고에만 있고 이번에 안 잰 프로브(설정에서 뺀 것)는 되살리지 않는다.
    """
    settled: dict[str, ProbeResult] = {}
    unknown = False
    stale = False
    for name, result in current.items():
        if result.state != PROBE_TIMEOUT:
            settled[name] = result
            continue
        before = (previous or {}).get(name)
        if before is None or before.state == PROBE_TIMEOUT:
            settled[name] = result
            unknown = True
            continue
        settled[name] = ProbeResult(before.state, before.value, stale=True)
        stale = True
    return Fingerprint(
        probes=settled,
        hash=None if unknown else fingerprint_hash(settled),
        stale=stale or unknown,
    )


def changed_probes(
    old: Mapping[str, ProbeResult] | None, new: Mapping[str, ProbeResult]
) -> dict[str, tuple[str | None, str | None]]:
    """바뀐 프로브 → (앞 값, 새 값). 첫 보고(앞이 없음)는 비교할 것이 없다 — 빈 dict.

    상태가 바뀐 것도(ok → missing) 바뀐 것이다. 이어 쓴(stale) 값은 같은 값이다.
    """
    if old is None:
        return {}
    out: dict[str, tuple[str | None, str | None]] = {}
    for name in [*old, *(n for n in new if n not in old)]:
        a = old.get(name)
        b = new.get(name)
        if a is not None and b is not None and (a.state, a.value) == (b.state, b.value):
            continue
        if a is None and b is None:
            continue
        out[name] = (a.value if a is not None else None, b.value if b is not None else None)
    return out


# ── ③ 점검 상태 ───────────────────────────────────────────────────────────────


def check_state(
    records: Sequence[CheckRecord], fingerprint_hash: str | None
) -> tuple[str, CheckRecord | None]:
    """현재 지문으로 돈 가장 최근(가장 큰 job_id) 점검 잡 → CHECK_*.

    다른 지문의 기록은 아무것도 증명하지 않는다(Xcode 26.5 의 통과는 27.2 에서 무효).
    지문을 모르면(None) 무엇과도 맞추지 않는다.
    """
    if fingerprint_hash is None:
        return CHECK_NEEDED, None
    mine = [r for r in records if r.fingerprint_hash == fingerprint_hash]
    if not mine:
        return CHECK_NEEDED, None
    latest = max(mine, key=lambda r: r.job_id)
    if latest.job_state == "succeeded":
        return CHECK_PASSED, latest
    if latest.job_state in _ACTIVE_JOB_STATES:
        return CHECK_RUNNING, latest
    if latest.job_state in _FAILED_JOB_STATES:
        return CHECK_FAILED, latest
    # cancelled · lost — 아무것도 증명하지 않았다
    return CHECK_NEEDED, latest


# ── ④ 판정 ───────────────────────────────────────────────────────────────────


def verdict(
    preset: Preset,
    report: CapabilityReport | None,
    *,
    generation: str,
    now: datetime,
    max_age_seconds: float,
    check_records: Sequence[CheckRecord] = (),
) -> Verdict:
    """(프리셋, 그 워커의 최신 보고) → 판정. §4.3 의 표를 위에서부터, 첫 번째로 걸리는 것.

    `max_age_seconds` 는 호출자가 이미 `3 × probe_interval_seconds` 로 곱해서 넘긴다.
    `missing_passthrough` 는 판정에 안 들어간다(결정 92 — 빈 값이 의도일 수 있다).
    """
    if report is None:
        return Verdict(UNKNOWN, CODE_REPORT_MISSING)
    if report.generation != generation:
        return Verdict(UNKNOWN, CODE_REPORT_STALE, {"reason": "generation"})
    age = (now - report.measured_at).total_seconds()
    if age > max_age_seconds:
        return Verdict(UNKNOWN, CODE_REPORT_STALE, {"reason": "age", "age_seconds": int(age)})
    facts = report.presets.get(preset.name)
    if facts is None:
        return Verdict(UNKNOWN, CODE_REPORT_MISSING)
    if facts.missing_tools:
        return Verdict(INELIGIBLE, CODE_TOOL_MISSING, {"tools": list(facts.missing_tools)})
    if facts.workspace_tools:
        return Verdict(UNKNOWN, CODE_WORKSPACE_TOOL, {"tools": list(facts.workspace_tools)})
    checks = tuple(preset.checks)
    if not checks:
        return Verdict(ELIGIBLE, CODE_OK)
    fp = report.fingerprint.hash
    if fp is None:
        return Verdict(UNKNOWN, CODE_FINGERPRINT_UNKNOWN)
    states: list[tuple[str, str, CheckRecord | None]] = []
    for name in checks:
        state, record = check_state([r for r in check_records if r.preset == name], fp)
        states.append((name, state, record))
    for wanted, verdict_state, code in (
        (CHECK_FAILED, INELIGIBLE, CODE_CHECK_FAILED),
        (CHECK_RUNNING, UNKNOWN, CODE_CHECK_RUNNING),
        (CHECK_NEEDED, UNKNOWN, CODE_CHECK_NEEDED),
    ):
        for name, state, record in states:
            if state == wanted:
                detail: dict[str, Any] = {"check": name}
                if record is not None:
                    detail["job_id"] = record.job_id
                return Verdict(verdict_state, code, detail)
    return Verdict(ELIGIBLE, CODE_OK)


# ── 호스트 ───────────────────────────────────────────────────────────────────


def host_key_from(raw_hardware_id: str | None, worker: str) -> str:
    """하드웨어 UUID 의 sha256 앞 16자. 못 읽었으면 `worker:<w>`(그 워커 혼자 한 호스트).

    원문은 어디에도 남기지 않는다 — 해시만 나간다.
    """
    raw = (raw_hardware_id or "").strip()
    if not raw:
        return f"worker:{worker}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "CHECK_FAILED",
    "CHECK_NEEDED",
    "CHECK_PASSED",
    "CHECK_RUNNING",
    "ELIGIBLE",
    "INELIGIBLE",
    "LOCAL_WORKER",
    "PROBE_ERROR",
    "PROBE_MISSING",
    "PROBE_OK",
    "PROBE_TIMEOUT",
    "UNKNOWN",
    "CapabilityReport",
    "CheckRecord",
    "Fingerprint",
    "PresetFacts",
    "ProbeResult",
    "Verdict",
    "changed_probes",
    "check_state",
    "fingerprint_hash",
    "host_key_from",
    "normalize_probe_output",
    "settle_fingerprint",
    "verdict",
]
