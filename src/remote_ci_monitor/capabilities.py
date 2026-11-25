"""서버 쪽 능력 — 보고를 받고, 판정하고, 배치하고, 점검을 건다(M5n · `docs/m5n-workplan.md`).

`App` 에 섞는 믹스인이다(`RemoteWorkersMixin` 과 같은 모양). 규칙은 순수 계층
(`core/capability.py` · `core/queue.py`)에 있고, 여기는 그 규칙에 무엇을 넣을지만 정한다:

- 보고: 로컬은 서버 프로세스가 스스로 재고(`measure_capabilities`), 원격은
  `POST /worker/capabilities` 로 받는다. 세대(`generation`)가 다르면 거절한다(§4.2).
- 판정: (워커, 프리셋)마다 `capability.verdict` — 보고가 없거나 낡으면 unknown(fail-closed).
- claim: `Claimant`(eligible 인 any-capable 프리셋 · checks 로 막힌 pool 프리셋)를 만든다.
- 상태: 대기 잡마다 `Placement`(들어갈 수 있는 워커와 못 들어가는 이유).
- 점검: 지문이 바뀐 워커에 점검 프리셋을 (워커, 점검, 지문)마다 **한 번만** 스스로 건다.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from remote_ci_monitor.core import capability as cap
from remote_ci_monitor.core.capability import (
    CapabilityReport,
    Fingerprint,
    PresetFacts,
    ProbeResult,
    Verdict,
)
from remote_ci_monitor.core.model import (
    DEFAULT_POOL,
    PLACEMENT_CAPABLE,
    REASON_CHECK_FAILED,
    REASON_CHECK_PENDING,
    REASON_NO_CAPABLE_MACHINE,
    Job,
    Placement,
    PlacementNote,
    Preset,
)
from remote_ci_monitor.store import Claimant, MachineRow

LOCAL = cap.LOCAL_WORKER
#: 자동 점검의 요청자(토큰이 아니다 — 이 이름의 토큰은 없다)
CHECK_REQUESTER = "rcm:check"
CHECK_LABEL = "rcm (machine check)"
#: 보고 값 상한(§4.1) — 워커가 정규화했어도 서버가 다시 잰다
PROBE_VALUE_MAX = cap.PROBE_VALUE_MAX
#: 판정에서 「도구 문제」— pool 잡은 이것으로 막지 않는다(결정 98: 집어서 즉시 tool_missing)
_TOOL_CODES = frozenset({cap.CODE_TOOL_MISSING, cap.CODE_WORKSPACE_TOOL})
_CHECK_CODES_PENDING = frozenset({cap.CODE_CHECK_RUNNING, cap.CODE_CHECK_NEEDED})
#: 자동 점검의 하루 상한 — (워커, 점검)마다(결정 105). 워커는 자기 지문을 스스로 말하므로, 지문을
#: 매번 바꿔 보내는 워커가 점검을 끝없이 돌리게 할 수 없다. 사람의 다시 점검은 세지 않는다
AUTO_CHECKS_PER_DAY = 3
AUTO_CHECK_WINDOW = timedelta(days=1)
#: 원격이 보낸 host_key 가 가질 수 있는 꼴 — 해시(16 hex) 또는 `worker:<이름>`. 그 밖은 버리고
#: `worker:<이름>` 으로(화면 · 로그 · 잡 행에 실리는 값이다)
_HOST_KEY_RE = re.compile(r"[A-Za-z0-9:._-]{1,80}")
#: 원격 머신에서 본 `local_only` 프리셋의 판정 코드(M5t-a 결정 254) — 그 머신은 후보가 아니다
CODE_LOCAL_ONLY = "local_only"
#: `checks` 의 점검이 그 머신에서는 돌 수 없다(점검 프리셋이 `local_only`) — 점검 대기가 아니다
CODE_CHECK_LOCAL_ONLY = "check_local_only"


class MachineDown(Exception):
    """그 머신이 down 이다 — 거기에 점검을 걸어도 기다리기만 한다(결정 106)."""


class AmbiguousMachine(ValueError):
    """표시 이름이 여러 머신에 맞는다 — 워커 id 로 골라야 한다(결정 108)."""


class NotACandidate(Exception):
    """그 머신은 이 프리셋의 후보가 아니다(`local_only`, M5t-a 결정 254) — 409 로 거절한다."""


class CapabilityError(Exception):
    """보고 거절 — 상태 코드와 문장(경로·토큰 없이)."""

    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


@dataclass(frozen=True)
class _MachineView:
    """화면 · 판정이 보는 워커 하나."""

    id: str
    display_name: str
    kind: str  # "local" | "remote"
    pool: str
    alive: bool
    report: CapabilityReport | None
    row: MachineRow | None
    lanes: int | None = None  # 로컬은 `[server] lanes`, 원격은 등록한 레인 수(`rcm machines` 의 칸)


def _bare_view(worker: str, pool: str = DEFAULT_POOL) -> _MachineView:
    """이름만 아는 머신 — 후보 함수에 넘길 자리. 후보 함수는 종류(로컬 · 원격)와 id 만 본다."""
    kind = "local" if worker == LOCAL else "remote"
    return _MachineView(worker, worker, kind, pool, True, None, None)


def _probe_from_json(name: str, raw: Any) -> ProbeResult:
    if not isinstance(raw, dict):
        raise CapabilityError(400, f"fingerprint '{name}' must be an object")
    state = raw.get("state")
    if state not in (cap.PROBE_OK, cap.PROBE_ERROR, cap.PROBE_MISSING, cap.PROBE_TIMEOUT):
        raise CapabilityError(
            400, f"fingerprint '{name}': state must be ok, error, missing or timeout"
        )
    value = raw.get("value")
    if value is not None and not isinstance(value, str):
        raise CapabilityError(400, f"fingerprint '{name}': value must be a string or null")
    if value is not None and len(value) > PROBE_VALUE_MAX:
        raise CapabilityError(
            400, f"fingerprint '{name}': value longer than {PROBE_VALUE_MAX} characters"
        )
    # 워커가 정규화했어도 서버가 다시 씻는다 — 값은 서버 로그 줄(「environment changed」),
    # `rcm machines` 의 터미널, 웹에 그대로 나간다. 줄바꿈이면 로그 줄 위조, ESC 면 터미널 조작이다.
    return ProbeResult(state, cap.strip_control(value) if value is not None else None)


def probes_json(probes: Mapping[str, ProbeResult]) -> str:
    return json.dumps(
        {n: {"state": p.state, "value": p.value, "stale": p.stale} for n, p in probes.items()},
        sort_keys=True,
        ensure_ascii=False,
    )


def probes_from_json(text: str | None) -> dict[str, ProbeResult] | None:
    if not text:
        return None
    try:
        raw = json.loads(text)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    out: dict[str, ProbeResult] = {}
    for name, item in raw.items():
        if isinstance(item, dict):
            out[name] = ProbeResult(
                str(item.get("state")), item.get("value"), bool(item.get("stale", False))
            )
    return out


def _str_tuple(where: str, raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise CapabilityError(400, f"{where} must be a list of strings")
    return tuple(cap.strip_control(x)[:200] for x in raw[:64])


def report_from_json(
    worker: str,
    body: Any,
    *,
    generation: str,
    preset_names: set[str],
    probe_names: set[str],
    previous: Mapping[str, ProbeResult] | None,
    now: datetime,
) -> CapabilityReport:
    """`POST /worker/capabilities` 본문 → 보고. 지문은 서버가 앞 값으로 굳힌다(timeout 은 잇는다)"""
    if not isinstance(body, dict):
        raise CapabilityError(400, "body must be a JSON object")
    gen = body.get("generation")
    if gen != generation:
        raise CapabilityError(
            409,
            f"report generation {gen!r} is not the current {generation!r} — fetch /worker/spec",
            code="generation_mismatch",
        )
    host_key = body.get("host_key")
    if not isinstance(host_key, str) or not _HOST_KEY_RE.fullmatch(host_key):
        host_key = cap.host_key_from(None, worker)
    accept_any = body.get("accept_any", True)
    if not isinstance(accept_any, bool):
        raise CapabilityError(400, "accept_any must be true or false")
    raw_fp = body.get("fingerprint", {})
    if not isinstance(raw_fp, dict):
        raise CapabilityError(400, "fingerprint must be an object")
    unknown_probes = sorted(set(raw_fp) - probe_names)
    if unknown_probes:
        raise CapabilityError(400, f"unknown probe(s): {', '.join(unknown_probes)}")
    current = {name: _probe_from_json(name, raw_fp[name]) for name in sorted(raw_fp)}
    raw_presets = body.get("presets", {})
    if not isinstance(raw_presets, dict):
        raise CapabilityError(400, "presets must be an object")
    unknown_presets = sorted(set(raw_presets) - preset_names)
    if unknown_presets:
        raise CapabilityError(400, f"unknown preset(s): {', '.join(unknown_presets)}")
    facts: dict[str, PresetFacts] = {}
    for name, item in raw_presets.items():
        if not isinstance(item, dict):
            raise CapabilityError(400, f"presets '{name}' must be an object")
        facts[name] = PresetFacts(
            missing_tools=_str_tuple(f"presets '{name}' missing_tools", item.get("missing_tools")),
            workspace_tools=_str_tuple(
                f"presets '{name}' workspace_tools", item.get("workspace_tools")
            ),
            missing_passthrough=_str_tuple(
                f"presets '{name}' missing_passthrough", item.get("missing_passthrough")
            ),
        )
    return CapabilityReport(
        worker=worker,
        generation=generation,
        host_key=host_key,
        accept_any=accept_any,
        fingerprint=cap.settle_fingerprint(current, previous),
        presets=facts,
        measured_at=now,
    )


def verdict_json(v: Verdict) -> dict[str, Any]:
    return {"state": v.state, "code": v.code, "detail": dict(v.detail)}


class CapabilitiesMixin:
    """`App` 에 섞는다. `config` · `store` · `now_fn` · `log` · `stop` · 원격 워커 메서드를 쓴다."""

    # App 이 가진 것(타입 검사용 선언)
    config: Any
    store: Any
    now_fn: Any
    stop: threading.Event
    wake: threading.Event

    def _capabilities_init(self) -> None:
        self._cap_lock = threading.Lock()
        self._epoch = int(self.now_fn().timestamp())
        self._reloads = 0
        self._reports: dict[str, CapabilityReport] = {}
        self._local_host_key: str | None = None
        # 워커 → 마지막으로 보고한 호스트. `machines` 표의 사본이라 재시작 뒤에도 안다(처음 한 번
        # 읽는다 — 상태 요청마다 워커 수만큼 질의하지 않게)
        self._known_hosts: dict[str, str] | None = None
        self._check_errors: dict[tuple[str, str], str] = {}
        self._cap_thread: threading.Thread | None = None
        # 로컬 측정은 한 번에 하나 — 기동 · reload(뒤에서) · 주기가 겹쳐도 앞 지문을 같이 읽지 않게
        self._measure_lock = threading.Lock()

    # ── 세대 ────────────────────────────────────────────────────────────────

    @property
    def generation(self) -> str:
        return f"{self._epoch}:{self._reloads}"

    def bump_generation(self) -> None:
        """reload 가 프리셋 · 프로브를 바꿨다 — 옛 보고는 이제 `report_stale` 이다."""
        with self._cap_lock:
            self._reloads += 1

    def worker_spec(self) -> dict[str, Any]:
        """`GET /worker/spec` — 프로브와 프리셋 요구 사항. claim payload 와 같은 범위만 싣는다."""
        return {
            "generation": self.generation,
            "probe_interval_seconds": self.config.server.probe_interval_seconds,
            "probes": [
                {"name": p.name, "argv": list(p.argv), "timeout_seconds": p.timeout_seconds}
                for p in self.config.probes
            ],
            # 원격 워커가 받는 명세다 — 후보 함수가 원격을 빼는 프리셋(`local_only`)은 env 와 함께
            # 싣지 않는다(결정 254)
            "presets": [
                {
                    "name": p.name,
                    "requires": list(p.requires),
                    "env_passthrough": list(p.env_passthrough),
                    "env": dict(p.env),
                }
                for p in self.config.presets
                if self.machines_for(p, [_bare_view("")])
            ],
        }

    # ── 보고 ────────────────────────────────────────────────────────────────

    def local_host_key(self) -> str:
        if self._local_host_key is None:
            from remote_ci_monitor.hostsample import hardware_id

            self._local_host_key = cap.host_key_from(hardware_id(), LOCAL)
        return self._local_host_key

    def measure_capabilities_later(self) -> None:
        """reload 뒤의 다시 재기 — **뒤에서** 한다. reload 는 요청 스레드(SIGHUP 이면 연결을 받는
        루프)에서 돌고, 프로브는 60초까지 걸린다. 그동안 로컬의 보고는 옛 세대라 `report_stale`
        이다 — any-capable 을 안 받을 뿐이다(fail-closed, 결정 91)."""

        def run() -> None:
            try:
                self.measure_capabilities()
            except Exception as e:  # noqa: BLE001 — 스레드가 조용히 죽으면 로컬이 낡은 채로 남는다
                from remote_ci_monitor.server import _safe

                self.record_error(f"capabilities: {type(e).__name__}: {_safe(str(e))}")

        threading.Thread(target=run, name="rcm-measure", daemon=True).start()

    def measure_capabilities(self, environ: Mapping[str, str] | None = None) -> None:
        """로컬 워커(`@local`)를 **이 프로세스의 환경**으로 잰다 — 기동 · reload · 주기(§4.2)."""
        with self._measure_lock:
            self._measure_capabilities(environ)

    def _measure_capabilities(self, environ: Mapping[str, str] | None) -> None:
        from remote_ci_monitor.hostsample import run_probes
        from remote_ci_monitor.runner import measure_tools

        env = dict(os.environ if environ is None else environ)
        current = run_probes(self.config.probes, env)
        facts: dict[str, PresetFacts] = {}
        from remote_ci_monitor.core import jobenv

        for p in self.config.presets:
            job_env = jobenv.job_env(p.env_passthrough, p.env, env)
            missing, workspace = measure_tools(tuple(p.requires), job_env)
            facts[p.name] = PresetFacts(
                missing_tools=tuple(missing),
                workspace_tools=tuple(workspace),
                missing_passthrough=jobenv.missing_passthrough(
                    p.env_passthrough, env, preset_env=p.env
                ),
            )
        self._accept_report(
            LOCAL,
            lambda previous: CapabilityReport(
                worker=LOCAL,
                generation=self.generation,
                host_key=self.local_host_key(),
                accept_any=bool(self.config.server.local_accept_any),
                fingerprint=cap.settle_fingerprint(current, previous),
                presets=facts,
                measured_at=self.now_fn(),
            ),
            display_name=self.local_display_name(),
        )

    def receive_capabilities(self, worker: str, host_name: str | None, body: Any) -> None:
        """`POST /worker/capabilities` — 거절은 `CapabilityError`."""
        preset_names = {p.name for p in self.config.presets}
        probe_names = {p.name for p in self.config.probes}
        self._accept_report(
            worker,
            lambda previous: report_from_json(
                worker,
                body,
                generation=self.generation,
                preset_names=preset_names,
                probe_names=probe_names,
                previous=previous,
                now=self.now_fn(),
            ),
            display_name=host_name or worker,
        )

    def _accept_report(self, worker: str, build: Any, *, display_name: str) -> None:
        row = self.store.get_machine(worker)
        previous = probes_from_json(row.fingerprint_json) if row else None
        report: CapabilityReport = build(previous)
        fp = report.fingerprint
        fp_json = probes_json(fp.probes)
        saved = self.store.upsert_machine(
            worker,
            host_key=report.host_key,
            display_name=display_name,
            accept_any=report.accept_any,
            fingerprint_hash=fp.hash,
            fingerprint_json=fp_json,
            measured_at=report.measured_at,
            generation=report.generation,
            report_json=None,
        )
        if fp.hash is not None:
            self.store.record_fingerprint(fp.hash, fp_json, report.measured_at)
        with self._cap_lock:
            self._reports[worker] = report
            if self._known_hosts is not None:
                self._known_hosts[worker] = report.host_key
        if saved.changed:
            diff = cap.changed_probes(probes_from_json(saved.previous_json), fp.probes)
            what = " · ".join(f"{n} {old or '—'} → {new or '—'}" for n, (old, new) in diff.items())
            self.log(f"machine {worker}: environment changed — {what or 'fingerprint'}")
        self._mark_dirty()  # type: ignore[attr-defined]
        self.wake.set()

    def report_of(self, worker: str) -> CapabilityReport | None:
        with self._cap_lock:
            return self._reports.get(worker)

    def forget_report(self, worker: str) -> None:
        with self._cap_lock:
            self._reports.pop(worker, None)

    # ── 판정 ────────────────────────────────────────────────────────────────

    def check_preset_names(self) -> list[str]:
        seen: list[str] = []
        for p in self.config.presets:
            for c in p.checks:
                if c not in seen:
                    seen.append(c)
        return seen

    def _max_age(self) -> float:
        return 3.0 * float(self.config.server.probe_interval_seconds)

    def _records(self, worker: str, report: CapabilityReport | None) -> list[Any]:
        """판정에 드는 점검 기록 — **현재 지문의 것만**(`check_state` 가 보는 것이 그것뿐이다).
        지문을 모르면 읽을 것이 없다(판정은 `fingerprint_unknown` · 점검 칸은 `needed`)."""
        checks = self.check_preset_names()
        fp = report.fingerprint.hash if report is not None else None
        if not checks or fp is None:
            return []
        return self.store.check_records(worker, checks, fingerprint_hash=fp)

    def verdicts_for(self, worker: str, now: datetime) -> dict[str, Verdict]:
        report = self.report_of(worker)
        records = self._records(worker, report)
        view = _bare_view(worker)
        return {p.name: self._verdict(p, view, report, records, now) for p in self.config.presets}

    def _verdict(
        self,
        preset: Preset,
        view: _MachineView,
        report: CapabilityReport | None,
        records: list[Any],
        now: datetime,
    ) -> Verdict:
        """(프리셋, 머신) 판정 — `cap.verdict` 에 후보 함수의 답을 얹는다(결정 254).

        원격 머신이 그 프리셋의 후보가 아니면 `local_only` 다. 보고가 「다 있다」고 해도 그 머신은
        받지 않는다. 판정이 점검 대기(`check_needed`)인데 기다리는 점검이 그 머신에서는 돌 수
        없으면(점검 프리셋이 `local_only`) `check_local_only` 다. 영영 안 오는 점검을
        「점검 중」으로 부르면 잡이 조용히 기다린다."""
        if view.kind != "local" and not self.machines_for(preset, [view]):
            return Verdict(cap.INELIGIBLE, CODE_LOCAL_ONLY)
        v = cap.verdict(
            preset,
            report,
            generation=self.generation,
            now=now,
            max_age_seconds=self._max_age(),
            check_records=records,
        )
        if v.code != cap.CODE_CHECK_NEEDED or report is None:
            return v
        fp = report.fingerprint.hash
        for name in preset.checks:
            if not self.check_reachable(view, name):
                state, _record = cap.check_state([r for r in records if r.preset == name], fp)
                if state != cap.CHECK_PASSED:
                    return Verdict(cap.INELIGIBLE, CODE_CHECK_LOCAL_ONLY, {"check": name})
        return v

    # ── 후보 머신 (M5t-a 결정 254) ─────────────────────────────────────────

    def machines_for(
        self,
        preset: Preset | None,
        views: list[_MachineView],
        *,
        target: str | None = None,
        pool: str | None = None,
        placement: str | None = None,
    ) -> list[_MachineView]:
        """그 프리셋(그 잡)의 후보 머신. 배치 · ETA · 제출 · 점검 · `/worker/spec` · claim ·
        `/api/machines` 가 모두 이 결과를 쓰고 따로 거르지 않는다(docs/m5t-workplan.md §4.3).

        정적인 조건만 본다 — `local_only`, 대상 머신, 풀, `local_accept_any`. `[server] lanes`
        는 설정이 1 이상으로 막으므로 아직 보지 않는다(`lanes = 0` 은 M5t-a PR3 이 더한다).
        살아 있음과 도구 판정은 보지 않는다(그것은 지금처럼 갈래 안에서 본다). `local_only` 가
        아닌 프리셋은 받은 머신을 그대로 돌려준다. `target` · `pool` · `placement` 를 안 주면
        프리셋의 기본값이다(잡이 있으면 그 잡의 값을 준다)."""
        if preset is None or not preset.local_only:
            return list(views)
        if target is not None:
            views = [v for v in views if v.id == target]
        views = [v for v in views if v.kind == "local"]  # 원격 제외 — local_only 의 정의
        if self._local_gap(preset, target=target, pool=pool, placement=placement) is not None:
            return []
        return views

    def _local_gap(
        self,
        preset: Preset,
        *,
        target: str | None = None,
        pool: str | None = None,
        placement: str | None = None,
    ) -> str | None:
        """`local_only` 프리셋의 잡을 로컬 레인이 **설정 때문에** 못 받는 이유(사람용 문장).
        받을 수 있으면 None. 판정(도구 · 점검)과 살아 있음은 보지 않는다. 원격 워커를 지정한
        잡은 여기서 보지 않는다 — 원격 제외 한 줄(`machines_for`)이 그 후보를 비운다."""
        head = _local_only_head(preset)
        if target is not None:
            return None
        placement = preset.placement if placement is None else placement
        if placement == PLACEMENT_CAPABLE:
            if not self.config.server.local_accept_any:
                return f"{head} — they take no any-capable job (local_accept_any = false)"
            return None
        pool = preset.pool if pool is None else pool
        if pool != DEFAULT_POOL:
            return f"{head} — they take only the {DEFAULT_POOL} pool, not pool {pool!r}"
        return None

    def check_reachable(self, view: _MachineView, check: str) -> bool:
        """그 머신에서 점검 프리셋을 돌릴 수 있나 — 점검은 그 머신을 지정한 잡이다. 설정에 없는
        이름은 막지 않는다(지금처럼 걸려다 `preset` 없음으로 끝난다)."""
        preset = self.config.preset(check)
        if preset is None:
            return True
        return bool(self.machines_for(preset, [view], target=view.id))

    def local_only_refusal(
        self, preset: Preset, *, target: str | None, pool: str, placement: str
    ) -> tuple[str, str] | None:
        """`local_only` 제출을 거절할 이유 `(코드, 문장)` — 없으면 None(§4.3, TO16).

        정적인 셋(원격 지정 · `local_accept_any = false` 인데 any-capable · 기본 풀이 아닌 풀)과
        capable 갈래에서 `@local` 의 `tool_missing` 에만 409 다(넷째인 `lanes = 0` 은 PR3). 점검
        판정 때문에 비거나 `@local` 의 보고가 없거나 낡은 경우는 큐에 든다(`_why_none` 이 이유를
        보인다). `local_only` 가 아니면 언제나 None."""
        if not preset.local_only:
            return None
        now = self.now_fn()
        views = self.machine_views(now)
        found = self.machines_for(preset, views, target=target, pool=pool, placement=placement)
        if not found:
            if target is not None and target != LOCAL:
                why = f"{_local_only_head(preset)} — machine {target} cannot take it"
            else:
                gap = self._local_gap(preset, target=target, pool=pool, placement=placement)
                why = gap or f"{_local_only_head(preset)} — no local lane can take it"
            return REASON_NO_CAPABLE_MACHINE, why
        if placement != PLACEMENT_CAPABLE or target is not None:
            return None
        local = found[0]
        records = self._records(local.id, local.report) if preset.checks else []
        v = self._verdict(preset, local, local.report, records, now)
        if v.code == cap.CODE_TOOL_MISSING:
            tools = ", ".join(str(t) for t in v.detail.get("tools") or [])
            return (
                cap.CODE_TOOL_MISSING,
                f"{_local_only_head(preset)} — they miss the required tool(s): {tools}",
            )
        return None

    def claimant_for(self, worker: str, pool: str, now: datetime) -> Claimant:
        """claim 직전의 신원과 받을 수 있는 것(§5.2). 보고가 없으면 capable 은 비어 있다."""
        report = self.report_of(worker)
        verdicts = self.verdicts_for(worker, now)
        presets = {p.name: p for p in self.config.presets}
        # 지금 설정의 placement 로 거르지 않는다 — 잡은 제출 때의 placement 를 지니고(SQL 이
        # `placement = 'capable'` 로 고른다), reload 로 any-capable 을 뗀 프리셋의 대기 잡도 어딘가
        # 에서는 집혀야 한다. 거르면 그 잡은 어느 갈래에도 안 걸려 영원히 선다(검증 2026-11-16)
        capable = tuple(name for name, v in verdicts.items() if v.state == cap.ELIGIBLE)
        gated = tuple(
            name
            for name, v in verdicts.items()
            if presets[name].checks and v.state != cap.ELIGIBLE and v.code not in _TOOL_CODES
        )
        # 후보 함수가 이 워커를 뺀 프리셋 — 지정 · pool · capable · known 어느 갈래로도 안 집는다.
        # 로컬 레인은 빼지 않는다(로컬 레인의 규칙은 지금 그대로다, TO3)
        view = _bare_view(worker, pool)
        excluded = (
            ()
            if worker == LOCAL
            else tuple(name for name, p in presets.items() if not self.machines_for(p, [view]))
        )
        return Claimant(
            worker=worker,
            pool=pool,
            host_key=self.host_of(worker, report),
            fingerprint_hash=report.fingerprint.hash if report else None,
            capable=capable,
            gated=gated,
            accept_any=report.accept_any if report else False,
            known=tuple(presets),
            excluded=excluded,
        )

    def host_of(self, worker: str, report: CapabilityReport | None = None) -> str | None:
        """그 워커가 사는 호스트 — 보고 → `machines` 표가 기억하는 것 → (로컬이면) 이 컴퓨터.

        재등록 · 서버 재시작 뒤 첫 보고 전에도 어느 컴퓨터인지는 안다. 보고가 지워졌다고 호스트를
        모른다고 하면 `host` 범위 그룹이 그 틈에 막는 쪽으로만 돈다(claim 의 fail-closed). 기억도
        없으면 None — 지어내지 않는다."""
        report = report if report is not None else self.report_of(worker)
        if report is not None:
            return report.host_key
        if worker == LOCAL:
            return self.local_host_key()
        with self._cap_lock:
            known = self._known_hosts
        if known is None:
            known = {r.worker: r.host_key for r in self.store.list_machines() if r.host_key}
            with self._cap_lock:
                if self._known_hosts is None:
                    self._known_hosts = known
                known = self._known_hosts
        return known.get(worker)

    def local_claimant(self, now: datetime) -> Claimant:
        return self.claimant_for(LOCAL, DEFAULT_POOL, now)

    # ── 머신 목록 ───────────────────────────────────────────────────────────

    def local_display_name(self) -> str:
        return socket.gethostname().split(".")[0] or "host"

    def machine_views(self, now: datetime) -> list[_MachineView]:
        rows = {r.worker: r for r in self.store.list_machines()}
        views = [
            _MachineView(
                id=LOCAL,
                display_name=self.local_display_name(),
                kind="local",
                pool=DEFAULT_POOL,
                alive=True,
                report=self.report_of(LOCAL),
                row=rows.get(LOCAL),
                lanes=self.config.server.lanes,
            )
        ]
        for w in self._workers():
            views.append(
                _MachineView(
                    id=w.name,
                    display_name=w.host_name or w.name,
                    kind="remote",
                    pool=w.pool,
                    alive=self.worker_alive(w, now),
                    report=self.report_of(w.name),
                    row=rows.get(w.name),
                    lanes=w.lanes,
                )
            )
        return views

    @staticmethod
    def host_label(display_name: str, host_key: str | None) -> str:
        if not host_key or host_key.startswith("worker:"):
            return display_name
        return f"{display_name} ({host_key[:4]})"

    def _check_rows(self, view: _MachineView, records: list[Any]) -> list[dict[str, Any]]:
        fp = view.report.fingerprint.hash if view.report else None
        out = []
        for name in self.check_preset_names():
            state, record = cap.check_state([r for r in records if r.preset == name], fp)
            # 그 머신에서는 돌 수 없는 점검(`local_only`) — 스스로도, 다시 걸어도 안 온다(6-A-L5)
            reachable = self.check_reachable(view, name)
            out.append(
                {
                    "preset": name,
                    "state": state,
                    "job_id": record.job_id if record is not None else None,
                    # 지금 보이는 그 점검 잡을 rcm 이 스스로 걸었나(사람이 다시 걸었으면 False)
                    "auto_submitted": bool(
                        record is not None and self.store.check_was_automatic(record.job_id)
                    ),
                    "error_code": self._check_errors.get((view.id, name))
                    if reachable
                    else CODE_LOCAL_ONLY,
                    # 「점검 필요」인데 스스로 안 걸린다 — 사람이 `rcm recheck` 해야 풀린다.
                    # 돌 수 없는 점검은 다시 걸어도 409 라 사람에게 시키지 않는다
                    "recheck_needed": reachable
                    and state == cap.CHECK_NEEDED
                    and not self.check_comes_by_itself(view, name),
                }
            )
        return out

    def machine_json(self, view: _MachineView, now: datetime) -> dict[str, Any]:
        report = view.report
        row = view.row
        previous = probes_from_json(row.previous_json) if row else None
        fp: dict[str, Any]
        if report is None:
            fp = {
                "hash": None,
                "measured_at": None,
                "stale": False,
                "changed_at": None,
                "probes": {},
            }
        else:
            f = report.fingerprint
            fp = {
                "hash": f.hash,
                "measured_at": _iso(report.measured_at),
                "stale": f.stale,
                "changed_at": _iso(row.changed_at) if row else None,
                "probes": {
                    name: {
                        "state": p.state,
                        "value": p.value,
                        # 마지막으로 **바뀐** 프로브만 앞 값을 싣는다(§8.1). 안 바뀐 것까지 실으면
                        # 화면이 「바뀜」을 지어낸다
                        "previous": _previous_value(previous, name, p),
                    }
                    for name, p in f.probes.items()
                },
            }
        records = self._records(view.id, report)
        verdicts = {
            p.name: self._verdict(p, view, report, records, now) for p in self.config.presets
        }
        host_key = report.host_key if report else (row.host_key if row else None)
        if view.kind == "local":
            accept_any = bool(self.config.server.local_accept_any)
        else:
            accept_any = report.accept_any if report else (row.accept_any if row else True)
        return {
            "id": view.id,
            "display_name": view.display_name,
            "kind": view.kind,
            "pool": view.pool,
            "lanes": view.lanes,
            "alive": view.alive,
            "host_label": self.host_label(view.display_name, host_key),
            "host_key": host_key,
            "accept_any": accept_any,
            "fingerprint": fp,
            "presets": {
                name: verdict_json(self._flag_recheck(view, v)) for name, v in verdicts.items()
            },
            "checks": self._check_rows(view, records),
        }

    def machines_doc(self) -> dict[str, Any]:
        now = self.now_fn()
        return {
            "generation": self.generation,
            "machines": [self.machine_json(v, now) for v in self.machine_views(now)],
        }

    def machines_summary(self, now: datetime) -> list[dict[str, Any]]:
        out = []
        for view in self.machine_views(now):
            doc = self.machine_json(view, now)
            problems: list[dict[str, Any]] = []
            for c in doc["checks"]:
                if c["state"] == cap.CHECK_FAILED:
                    problems.append(
                        {"code": "check_failed", "check": c["preset"], "job_id": c["job_id"]}
                    )
            if doc["fingerprint"]["changed_at"] is not None:
                changed = {
                    n: [p["previous"], p["value"]]
                    for n, p in doc["fingerprint"]["probes"].items()
                    if p["previous"] is not None and p["previous"] != p["value"]
                }
                problems.append(
                    {
                        "code": "environment_changed",
                        "at": doc["fingerprint"]["changed_at"],
                        "probes": changed,
                    }
                )
            out.append(
                {
                    "id": doc["id"],
                    "display_name": doc["display_name"],
                    "kind": doc["kind"],
                    "pool": doc["pool"],
                    "host_label": doc["host_label"],
                    "alive": doc["alive"],
                    "accept_any": doc["accept_any"],
                    "fingerprint_hash": doc["fingerprint"]["hash"],
                    "fingerprint_changed_at": doc["fingerprint"]["changed_at"],
                    "eligible_count": sum(
                        1 for v in doc["presets"].values() if v["state"] == cap.ELIGIBLE
                    ),
                    "preset_count": len(doc["presets"]),
                    "problems": problems,
                }
            )
        return out

    # ── 배치 (상태 화면 · 제출 응답) ────────────────────────────────────────

    def check_comes_by_itself(self, view: _MachineView, check: str) -> bool:
        """`check_needed` 인 점검을 스위퍼가 스스로 거나 — 자동 점검이 켜져 있고 이 지문에 아직
        한 번도 스스로 건 적이 없을 때만. down 인 워커도 돌아오면 걸리므로 「스스로」다. 보고가
        없으면 지문을 모른다 — 보고가 오면 정해지므로 역시 「스스로」다.

        아니면(자동 점검이 꺼졌거나, 스스로 건 점검이 유실 · 취소로 판정 없이 끝났다 — 결정 95 로
        다시 안 건다) 사람이 다시 걸기 전까지 영영 안 풀린다. 그것을 「점검 중」(스스로 풀림)으로
        부르면 잡이 조용히 영원히 기다린다(실측 2026-11-16: 점검 도중 워커를 재시작했다).
        """
        if not self.config.server.auto_checks or not self.check_reachable(view, check):
            return False
        fp = view.report.fingerprint.hash if view.report is not None else None
        if fp is None:
            return True
        if self.store.auto_check_done(view.id, check, fp):
            return False
        # 하루 상한에 걸렸으면 스위퍼가 안 건다 — 사람이 다시 걸어야 풀린다(결정 105)
        return not self._auto_check_limited(view.id, check, self.now_fn())

    def _flag_recheck(self, view: _MachineView, v: Verdict) -> Verdict:
        """스스로 안 풀리는 `check_needed` 판정에 `detail.recheck = True` 를 단다(코드는 그대로)."""
        if v.code != cap.CODE_CHECK_NEEDED:
            return v
        check = v.detail.get("check")
        if not isinstance(check, str) or self.check_comes_by_itself(view, check):
            return v
        return Verdict(v.state, v.code, {**dict(v.detail), "recheck": True})

    def _placement_notes(
        self,
        preset: Preset,
        views: list[_MachineView],
        now: datetime,
        memo: dict[Any, Any] | None = None,
    ) -> list[tuple[_MachineView, Verdict]]:
        """(머신, 판정) 들. `memo` 를 주면 한 번의 계산 안에서 머신마다 점검 기록을 **한 번만**
        읽고 (프리셋, 머신) 판정을 다시 쓴다 — 큐 계산은 대기 잡마다 이걸 부른다(대기 잡 × 머신만큼
        질의하던 것, 엣지 검증)."""
        memo = {} if memo is None else memo
        out = []
        for view in views:
            vkey = ("verdict", preset.name, view.id)
            v = memo.get(vkey)
            if v is None:
                records: list[Any] = []
                if preset.checks:
                    rkey = ("records", view.id)
                    if rkey not in memo:
                        memo[rkey] = self._records(view.id, view.report)
                    records = memo[rkey]
                v = self._verdict(preset, view, view.report, records, now)
                # 스스로 안 풀리는 점검이면 표시를 단다 — (프리셋, 머신)마다 한 번(캐시 안에서)
                v = self._flag_recheck(view, v)
                memo[vkey] = v
            out.append((view, v))
        return out

    @staticmethod
    def _note(view: _MachineView, v: Verdict, *, accepts: bool = True) -> PlacementNote:
        if not view.alive:
            return PlacementNote(view.id, view.display_name, "down", "worker_down")
        if not accepts:
            return PlacementNote(view.id, view.display_name, cap.INELIGIBLE, "not_accepting_any")
        return PlacementNote(view.id, view.display_name, v.state, v.code, dict(v.detail))

    def placement_for(
        self,
        job: Job,
        now: datetime,
        views: list[_MachineView],
        memo: dict[Any, Any] | None = None,
    ) -> Placement:
        """대기 잡 하나가 들어갈 수 있는 워커(§7.2). 로컬은 `None` 이다(WorkerInfo 와 같은 키).

        `memo` 는 한 번의 큐 계산이 공유하는 판정 캐시다(`_placement_notes`)."""

        def key(view: _MachineView) -> str | None:
            return None if view.id == LOCAL else view.id

        preset = self.config.preset(job.preset)
        if preset is not None and preset.local_only:
            # 후보 함수가 머신을 뺐다 — 세 갈래 모두 「갈 곳 없음」이고 ETA 는 없다(§4.3 의 새
            # 분기). 이 분기는 `local_only` 프리셋에만 있다. 다른 프리셋의 빈 풀은 지금처럼
            # `worker_down` 이다(C3)
            views = self.machines_for(
                preset, views, target=job.target_worker, pool=job.pool, placement=job.placement
            )
            if not views:
                return Placement(workers=frozenset(), reason=REASON_NO_CAPABLE_MACHINE)
        if job.target_worker is not None:
            target = None if job.target_worker == LOCAL else job.target_worker
            return Placement(workers=frozenset({target}))
        if job.placement == PLACEMENT_CAPABLE and preset is None:
            # reload 로 프리셋이 사라졌다 — claim 의 capable 목록에 그 이름이 없으니 아무도
            # 안 집는다. pool 갈래로 떨어뜨리면 표시 풀의 레인에 시각을 지어낸다(fail-open)
            return Placement(workers=frozenset(), reason=REASON_NO_CAPABLE_MACHINE)
        if job.placement == PLACEMENT_CAPABLE and preset is not None:
            judged = self._placement_notes(preset, views, now, memo)
            stalled = self._stalled_checks(judged, memo)
            workers: set[str | None] = set()
            notes: list[PlacementNote] = []
            for view, v in judged:
                accepts = (
                    bool(self.config.server.local_accept_any)
                    if view.kind == "local"
                    else (view.report.accept_any if view.report else True)
                )
                if v.state == cap.ELIGIBLE and accepts:
                    workers.add(key(view))
                notes.append(self._note(view, v, accepts=accepts))
            return Placement(
                workers=frozenset(workers),
                reason=None if workers else self._why_none(notes, stalled),
                notes=tuple(notes),
            )
        # pool 배치 — 그 풀의 워커. checks 로 막힌 워커는 빠진다(도구 문제는 막지 않는다)
        in_pool = [v for v in views if v.pool == job.pool]
        if preset is None or not preset.checks:
            return Placement(workers=frozenset(key(v) for v in in_pool))
        judged = self._placement_notes(preset, in_pool, now, memo)
        stalled = self._stalled_checks(judged, memo)
        open_: set[str | None] = set()
        gated_notes: list[PlacementNote] = []
        for view, v in judged:
            if v.state == cap.ELIGIBLE or v.code in _TOOL_CODES:
                open_.add(key(view))
            else:
                gated_notes.append(self._note(view, v))
        if open_ or not in_pool:
            return Placement(workers=frozenset(open_), notes=tuple(gated_notes))
        return Placement(
            workers=frozenset(),
            reason=self._why_none(gated_notes, stalled),
            notes=tuple(gated_notes),
        )

    def _stalled_checks(
        self, judged: list[tuple[_MachineView, Verdict]], memo: dict[Any, Any] | None = None
    ) -> frozenset[str]:
        """`check_needed` 인데 **아무도 걸지 않을** 워커들 — 자동 점검이 꺼졌거나, 그 지문에 이미
        한 번 걸었다(실패가 아니라 취소 · 유실로 끝났다, 결정 95). 이것은 「스스로 풀린다」
        (check_pending)가 아니다 — 사람이 다시 점검해야 풀린다(검증 2026-11-16)."""
        out: set[str] = set()
        for view, v in judged:
            if v.code != cap.CODE_CHECK_NEEDED or not view.alive or view.report is None:
                continue
            fp = view.report.fingerprint.hash
            name = str(v.detail.get("check"))
            if not self.config.server.auto_checks or fp is None:
                out.add(view.id)
                continue
            # 대기 잡마다 부른다 — 한 번의 계산 안에서는 (머신, 점검, 지문)마다 한 번만 읽는다
            akey = ("auto_done", view.id, name, fp)
            if memo is None or akey not in memo:
                done = self.store.auto_check_done(view.id, name, fp)
                if memo is None:
                    if done:
                        out.add(view.id)
                    continue
                memo[akey] = done
            if memo[akey]:
                out.add(view.id)
        return frozenset(out)

    @staticmethod
    def _why_none(notes: list[PlacementNote], stalled: frozenset[str] = frozenset()) -> str:
        live = [n for n in notes if n.state != "down"]

        # 사람이 손대야 풀리는 점검 — 실패했거나, 판정 없이 끝나 다시 안 걸리는 것(`recheck`)
        def blocked(n: PlacementNote) -> bool:
            return n.code == cap.CODE_CHECK_FAILED or (
                n.code == cap.CODE_CHECK_NEEDED
                and (bool(n.detail.get("recheck")) or n.worker in stalled)
            )

        def pending(n: PlacementNote) -> bool:
            return n.code in _CHECK_CODES_PENDING and not blocked(n)

        if live and all(blocked(n) for n in live):
            return REASON_CHECK_FAILED
        if any(pending(n) for n in live) and not any(blocked(n) for n in live):
            return REASON_CHECK_PENDING
        if live and all(pending(n) or blocked(n) for n in live):
            return REASON_CHECK_PENDING
        return REASON_NO_CAPABLE_MACHINE

    def submission_placement(self, preset: Preset, job: Job | None) -> dict[str, Any]:
        """`POST /jobs` 응답의 `placement` — 지금 받을 수 있는 워커와 못 받는 워커의 이유."""
        now = self.now_fn()
        mode = "any-capable" if preset.placement == PLACEMENT_CAPABLE else "pool"
        views = self.machine_views(now)
        if job is not None and job.target_worker is not None:
            views = [v for v in views if v.id == job.target_worker]
        elif preset.placement != PLACEMENT_CAPABLE:
            pool = job.pool if job is not None else preset.pool
            views = [v for v in views if v.pool == pool]
        if job is not None:
            views = self.machines_for(
                preset, views, target=job.target_worker, pool=job.pool, placement=job.placement
            )
        else:
            views = self.machines_for(preset, views)
        eligible: list[str] = []
        blocked: list[dict[str, Any]] = []
        for view, v in self._placement_notes(preset, views, now):
            accepts = (
                bool(self.config.server.local_accept_any)
                if view.kind == "local"
                else (view.report.accept_any if view.report else True)
            )
            ok_now = view.alive and (
                (v.state == cap.ELIGIBLE and (accepts or preset.placement != PLACEMENT_CAPABLE))
                or (preset.placement != PLACEMENT_CAPABLE and v.code in _TOOL_CODES)
            )
            if ok_now:
                eligible.append(view.id)
                continue
            note = self._note(view, v, accepts=accepts)
            blocked.append({"worker": view.id, "code": note.code, "detail": dict(note.detail)})
        return {"mode": mode, "eligible_now": eligible, "blocked": blocked}

    # ── 자동 점검 (§4.4) ────────────────────────────────────────────────────

    def sweep_checks(self) -> list[int]:
        """한 회차 — `check_needed` 인 (워커, 점검)마다 그 워커를 지정한 점검 잡을 **한 번** 건다.

        살아 있는 워커만. 도구 판정을 지난(검사 단계까지 온) 프리셋이 있는 점검만. 같은 지문에
        이미 스스로 건 적이 있으면 다시 걸지 않는다(실패 · 취소 · 유실이어도 — 결정 95).
        """
        if not self.config.server.auto_checks:
            return []
        now = self.now_fn()
        submitted: list[int] = []
        for view in self.machine_views(now):
            if not view.alive or view.report is None:
                continue
            fp = view.report.fingerprint.hash
            if fp is None:
                continue
            wanted: list[str] = []
            presets = {p.name: p for p in self.config.presets}
            for preset_name, v in self.verdicts_for(view.id, now).items():
                preset = presets.get(preset_name)
                # 그 프리셋의 잡을 **받을 수 있는** 워커에만 건다 — 못 받는 워커에서 점검을 돌리면
                # 같은 컴퓨터의 둘째 워커가 시뮬레이터를 한 번 더 잡는다(계약 검증 N1)
                if preset is None or not self._could_receive(view, preset):
                    continue
                if v.code == cap.CODE_CHECK_NEEDED:
                    name = str(v.detail.get("check"))
                    if name not in wanted:
                        wanted.append(name)
            for name in wanted:
                if self.store.auto_check_done(view.id, name, fp):
                    continue
                if self._auto_check_limited(view.id, name, now):
                    if self._check_errors.get((view.id, name)) != "auto_check_limit":
                        self.log(
                            f"machine check {name} on {view.id}: not submitted — "
                            f"{AUTO_CHECKS_PER_DAY} automatic checks in 24h (fingerprint keeps "
                            "changing?) — rcm recheck runs it by hand"
                        )
                    self._check_errors[(view.id, name)] = "auto_check_limit"
                    continue
                job_id = self._submit_check(view, name, fp, auto=True)
                if job_id is not None:
                    submitted.append(job_id)
        if submitted:
            self.log(f"machine checks submitted: {submitted}")
        return submitted

    def resolve_machine(self, name: str, views: list[_MachineView]) -> _MachineView:
        """`--machine` · `recheck` 의 이름 → 머신(결정 108). 워커 id(`@local` 포함)가 먼저고,
        아니면 `rcm machines` 에 보이는 표시 이름. 없으면 `KeyError`, 여럿이면
        `AmbiguousMachine`(맞는 id 들을 댄다)."""
        for v in views:
            if v.id == name:
                return v
        hits = [v for v in views if v.display_name == name]
        if len(hits) == 1:
            return hits[0]
        if hits:
            ids = ", ".join(v.id for v in hits)
            raise AmbiguousMachine(
                f"machine name {name!r} matches several machines: {ids} — use the id"
            )
        raise KeyError(name)

    def _auto_check_limited(self, worker: str, check: str, now: datetime) -> bool:
        since = now - AUTO_CHECK_WINDOW
        return self.store.auto_checks_since(worker, check, since) >= AUTO_CHECKS_PER_DAY

    def _could_receive(self, view: _MachineView, preset: Preset) -> bool:
        """이 워커가 이 프리셋의 잡을 받을 수 있는 자리인가 — any-capable 이면 any-capable 을 받는
        워커, pool 이면 그 풀(또는 `--pool` 로 고를 수 있는 풀)의 워커. 판정은 따로다. 후보
        함수가 뺀 머신(`local_only`)은 받을 자리가 아니다."""
        if not self.machines_for(preset, [view]):
            return False
        if preset.placement == PLACEMENT_CAPABLE:
            if view.kind == "local":
                return bool(self.config.server.local_accept_any)
            return view.report.accept_any if view.report is not None else False
        return view.pool in (preset.pool, *preset.pools)

    def recheck(self, worker: str, names: list[str] | None) -> list[dict[str, Any]]:
        """`POST /machines/{id}/recheck` — 사람이 다시 건다(한 번 제한과 무관). 없는 머신은
        `KeyError`, 점검 프리셋이 아닌 이름은 `ValueError`."""
        now = self.now_fn()
        view = self.resolve_machine(worker, self.machine_views(now))  # KeyError · Ambiguous
        if not view.alive:
            raise MachineDown(view.id)
        checks = self.check_preset_names()
        targets = list(checks) if names is None else list(names)
        bad = [n for n in targets if n not in checks]
        if bad:
            raise ValueError(f"not a check preset: {', '.join(bad)}")
        if names is not None:
            # 이름을 댄 점검이 그 머신에서는 돌 수 없다(`local_only`) — 하나도 걸지 않고 409
            far = [n for n in targets if not self.check_reachable(view, n)]
            if far:
                raise NotACandidate(
                    f"check preset(s) {', '.join(far)} run only on this server's local lanes "
                    f"(local_only) — machine {view.id} cannot take them"
                )
        fp = view.report.fingerprint.hash if view.report else None
        out = []
        for name in targets:
            job_id = self._submit_check(view, name, fp, auto=False)
            item: dict[str, Any] = {"preset": name, "job_id": job_id}
            if job_id is None:
                # 못 걸었다 — 번호 없는 성공처럼 보이지 않게 이유를 싣는다(점검 칸과 같은 코드).
                # 돌 수 없는 점검(`local_only`)은 지금 설정으로 셈한다(`_check_rows` 와 같다)
                item["error_code"] = (
                    self._check_errors.get((view.id, name), "not_submitted")
                    if self.check_reachable(view, name)
                    else CODE_LOCAL_ONLY
                )
            out.append(item)
        return out

    def _submit_check(
        self, view: _MachineView, name: str, fp: str | None, *, auto: bool
    ) -> int | None:
        preset = self.config.preset(name)
        if preset is None:
            return None
        if not self.check_reachable(view, name):
            # 점검 잡을 만들기 전에 후보를 본다(6-A-L3) — 아무도 못 집는 지정 잡을 남기지 않는다.
            # `_check_errors` 에 적지 않는다: 설정에서 나온 사실이라 reload 로 표식을 떼면 바로
            # 사라져야 한다. 적어 두면 다음 성공까지 거짓 `local_only` 가 남고, 그 성공은
            # 「after ref_unresolved」로 틀린 원인을 로그에 남긴다(P3 1라운드)
            return None
        try:
            job = self.submit_internal_check(preset, view, fingerprint_hash=fp)
        except Exception as e:  # noqa: BLE001 — 해석 실패 등은 그 회차만 건너뛴다
            # 원인이 무엇이든 화면이 그릴 코드는 하나다 — git 의 종료 코드(128)를 싣지 않는다.
            # 로그는 처음 한 번만 — 회차(check_sweep_seconds)마다 워커마다 같은 줄이 쌓였다
            # (실측 2026-11-16: 15초에 20줄). 지금 상태는 `rcm machines` 의 `ref_unresolved`.
            first = (view.id, name) not in self._check_errors
            self._check_errors[(view.id, name)] = "ref_unresolved"
            if first:
                why = (str(e).strip().splitlines() or [""])[0][:160]
                self.log(
                    f"machine check {name} on {view.id}: not submitted "
                    f"({type(e).__name__}{': ' + why if why else ''}) — retried every sweep, "
                    "logged once"
                )
            return None
        if self._check_errors.pop((view.id, name), None) is not None:
            self.log(f"machine check {name} on {view.id}: submitted again after ref_unresolved")
        if fp is not None:
            self.store.record_machine_check(view.id, name, fp, job.id, self.now_fn(), auto=auto)
        return job.id

    def submit_internal_check(
        self, preset: Preset, view: _MachineView, *, fingerprint_hash: str | None
    ) -> Job:
        raise NotImplementedError  # App 이 구현한다(제출 경로를 공유한다)

    # ── 주기 스레드 ─────────────────────────────────────────────────────────

    def start_capabilities(self) -> None:
        self._cap_thread = threading.Thread(
            target=self._capabilities_loop, name="rcm-capabilities", daemon=True
        )
        self._cap_thread.start()

    def _capabilities_loop(self) -> None:
        last_probe = time.monotonic()
        while not self.stop.wait(max(1, int(self.config.server.check_sweep_seconds))):
            try:
                if time.monotonic() - last_probe >= self.config.server.probe_interval_seconds:
                    self.measure_capabilities()
                    last_probe = time.monotonic()
                self.sweep_checks()
            except Exception as e:  # noqa: BLE001 — 스레드가 죽으면 점검이 조용히 멈춘다
                # 공개면(`server.last_error`)이다 — 형제 루프처럼 경로를 지우고 한 줄로 접는다
                from remote_ci_monitor.server import _safe

                self.record_error(f"capabilities: {type(e).__name__}: {_safe(str(e))}")


def _local_only_head(preset: Preset) -> str:
    """`local_only` 거절 문장의 앞머리 — 409 의 사람용 문장이 모두 이것으로 시작한다."""
    return f"preset '{preset.name}' runs only on this server's local lanes (local_only)"


def _previous_value(
    previous: Mapping[str, ProbeResult] | None, name: str, now: ProbeResult
) -> str | None:
    """그 프로브가 앞 지문과 달랐으면 앞 값, 같거나 앞이 없으면 None."""
    before = (previous or {}).get(name)
    if before is None or (before.state, before.value) == (now.state, now.value):
        return None
    return before.value


def _iso(dt: datetime | None) -> str | None:
    from remote_ci_monitor.core.status import iso

    return iso(dt)


__all__ = [
    "CODE_CHECK_LOCAL_ONLY",
    "CODE_LOCAL_ONLY",
    "NotACandidate",
    "CHECK_LABEL",
    "CHECK_REQUESTER",
    "CapabilitiesMixin",
    "CapabilityError",
    "Fingerprint",
    "probes_from_json",
    "probes_json",
    "report_from_json",
]
