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
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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
    return ProbeResult(state, value)


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
    return tuple(x[:200] for x in raw[:64])


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
    if not isinstance(host_key, str) or not host_key or len(host_key) > 80:
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
        self._check_errors: dict[tuple[str, str], str] = {}
        self._cap_thread: threading.Thread | None = None

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
            "presets": [
                {
                    "name": p.name,
                    "requires": list(p.requires),
                    "env_passthrough": list(p.env_passthrough),
                    "env": dict(p.env),
                }
                for p in self.config.presets
            ],
        }

    # ── 보고 ────────────────────────────────────────────────────────────────

    def local_host_key(self) -> str:
        if self._local_host_key is None:
            from remote_ci_monitor.hostsample import hardware_id

            self._local_host_key = cap.host_key_from(hardware_id(), LOCAL)
        return self._local_host_key

    def measure_capabilities(self, environ: Mapping[str, str] | None = None) -> None:
        """로컬 워커(`@local`)를 **이 프로세스의 환경**으로 잰다 — 기동 · reload · 주기(§4.2)."""
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
                missing_passthrough=jobenv.missing_passthrough(p.env_passthrough, env),
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

    def verdicts_for(self, worker: str, now: datetime) -> dict[str, Verdict]:
        report = self.report_of(worker)
        checks = self.check_preset_names()
        records = self.store.check_records(worker, checks) if checks else []
        return {
            p.name: cap.verdict(
                p,
                report,
                generation=self.generation,
                now=now,
                max_age_seconds=self._max_age(),
                check_records=records,
            )
            for p in self.config.presets
        }

    def claimant_for(self, worker: str, pool: str, now: datetime) -> Claimant:
        """claim 직전의 신원과 받을 수 있는 것(§5.2). 보고가 없으면 capable 은 비어 있다."""
        report = self.report_of(worker)
        verdicts = self.verdicts_for(worker, now)
        presets = {p.name: p for p in self.config.presets}
        capable = tuple(
            name
            for name, v in verdicts.items()
            if presets[name].placement == PLACEMENT_CAPABLE and v.state == cap.ELIGIBLE
        )
        gated = tuple(
            name
            for name, v in verdicts.items()
            if presets[name].checks and v.state != cap.ELIGIBLE and v.code not in _TOOL_CODES
        )
        host = report.host_key if report else (self.local_host_key() if worker == LOCAL else None)
        return Claimant(
            worker=worker,
            pool=pool,
            host_key=host,
            fingerprint_hash=report.fingerprint.hash if report else None,
            capable=capable,
            gated=gated,
            accept_any=report.accept_any if report else False,
        )

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
            out.append(
                {
                    "preset": name,
                    "state": state,
                    "job_id": record.job_id if record is not None else None,
                    # 지금 보이는 그 점검 잡을 rcm 이 스스로 걸었나(사람이 다시 걸었으면 False)
                    "auto_submitted": bool(
                        record is not None and self.store.check_was_automatic(record.job_id)
                    ),
                    "error_code": self._check_errors.get((view.id, name)),
                    # 「점검 필요」인데 스스로 안 걸린다 — 사람이 `rcm recheck` 해야 풀린다
                    "recheck_needed": state == cap.CHECK_NEEDED
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
        checks = self.check_preset_names()
        records = self.store.check_records(view.id, checks) if checks else []
        verdicts = {
            p.name: cap.verdict(
                p,
                report,
                generation=self.generation,
                now=now,
                max_age_seconds=self._max_age(),
                check_records=records,
            )
            for p in self.config.presets
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
        if not self.config.server.auto_checks:
            return False
        fp = view.report.fingerprint.hash if view.report is not None else None
        if fp is None:
            return True
        return not self.store.auto_check_done(view.id, check, fp)

    def _flag_recheck(self, view: _MachineView, v: Verdict) -> Verdict:
        """스스로 안 풀리는 `check_needed` 판정에 `detail.recheck = True` 를 단다(코드는 그대로)."""
        if v.code != cap.CODE_CHECK_NEEDED:
            return v
        check = v.detail.get("check")
        if not isinstance(check, str) or self.check_comes_by_itself(view, check):
            return v
        return Verdict(v.state, v.code, {**dict(v.detail), "recheck": True})

    def _placement_notes(
        self, preset: Preset, views: list[_MachineView], now: datetime
    ) -> list[tuple[_MachineView, Verdict]]:
        out = []
        checks = self.check_preset_names()
        for view in views:
            records = (
                self.store.check_records(view.id, checks) if (checks and preset.checks) else []
            )
            v = cap.verdict(
                preset,
                view.report,
                generation=self.generation,
                now=now,
                max_age_seconds=self._max_age(),
                check_records=records,
            )
            out.append((view, self._flag_recheck(view, v)))
        return out

    @staticmethod
    def _note(view: _MachineView, v: Verdict, *, accepts: bool = True) -> PlacementNote:
        if not view.alive:
            return PlacementNote(view.id, view.display_name, "down", "worker_down")
        if not accepts:
            return PlacementNote(view.id, view.display_name, cap.INELIGIBLE, "not_accepting_any")
        return PlacementNote(view.id, view.display_name, v.state, v.code, dict(v.detail))

    def placement_for(self, job: Job, now: datetime, views: list[_MachineView]) -> Placement:
        """대기 잡 하나가 들어갈 수 있는 워커(§7.2). 로컬은 `None` 이다(WorkerInfo 와 같은 키)."""

        def key(view: _MachineView) -> str | None:
            return None if view.id == LOCAL else view.id

        if job.target_worker is not None:
            target = None if job.target_worker == LOCAL else job.target_worker
            return Placement(workers=frozenset({target}))
        preset = self.config.preset(job.preset)
        if job.placement == PLACEMENT_CAPABLE and preset is not None:
            judged = self._placement_notes(preset, views, now)
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
                reason=None if workers else self._why_none(notes),
                notes=tuple(notes),
            )
        # pool 배치 — 그 풀의 워커. checks 로 막힌 워커는 빠진다(도구 문제는 막지 않는다)
        in_pool = [v for v in views if v.pool == job.pool]
        if preset is None or not preset.checks:
            return Placement(workers=frozenset(key(v) for v in in_pool))
        judged = self._placement_notes(preset, in_pool, now)
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
            workers=frozenset(), reason=self._why_none(gated_notes), notes=tuple(gated_notes)
        )

    @staticmethod
    def _why_none(notes: list[PlacementNote]) -> str:
        live = [n for n in notes if n.state != "down"]

        # 사람이 손대야 풀리는 점검 — 실패했거나, 판정 없이 끝나 다시 안 걸리는 것(`recheck`)
        def blocked(n: PlacementNote) -> bool:
            return n.code == cap.CODE_CHECK_FAILED or (
                n.code == cap.CODE_CHECK_NEEDED and bool(n.detail.get("recheck"))
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
            for v in self.verdicts_for(view.id, now).values():
                if v.code == cap.CODE_CHECK_NEEDED:
                    name = str(v.detail.get("check"))
                    if name not in wanted:
                        wanted.append(name)
            for name in wanted:
                if self.store.auto_check_done(view.id, name, fp):
                    continue
                job_id = self._submit_check(view, name, fp, auto=True)
                if job_id is not None:
                    submitted.append(job_id)
        if submitted:
            self.log(f"machine checks submitted: {submitted}")
        return submitted

    def recheck(self, worker: str, names: list[str] | None) -> list[dict[str, Any]]:
        """`POST /machines/{id}/recheck` — 사람이 다시 건다(한 번 제한과 무관). 없는 머신은
        `KeyError`, 점검 프리셋이 아닌 이름은 `ValueError`."""
        now = self.now_fn()
        views = {v.id: v for v in self.machine_views(now)}
        view = views.get(worker)
        if view is None:
            raise KeyError(worker)
        checks = self.check_preset_names()
        targets = list(checks) if names is None else list(names)
        bad = [n for n in targets if n not in checks]
        if bad:
            raise ValueError(f"not a check preset: {', '.join(bad)}")
        fp = view.report.fingerprint.hash if view.report else None
        out = []
        for name in targets:
            job_id = self._submit_check(view, name, fp, auto=False)
            out.append({"preset": name, "job_id": job_id})
        return out

    def _submit_check(
        self, view: _MachineView, name: str, fp: str | None, *, auto: bool
    ) -> int | None:
        preset = self.config.preset(name)
        if preset is None:
            return None
        try:
            job = self.submit_internal_check(preset, view, fingerprint_hash=fp)
        except Exception as e:  # noqa: BLE001 — 해석 실패 등은 그 회차만 건너뛴다
            # 원인이 무엇이든 화면이 그릴 코드는 하나다 — git 의 종료 코드(128)를 싣지 않는다
            self._check_errors[(view.id, name)] = "ref_unresolved"
            self.log(f"machine check {name} on {view.id}: not submitted ({type(e).__name__})")
            return None
        self._check_errors.pop((view.id, name), None)
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
                self.record_error(f"capabilities: {type(e).__name__}: {e}"[:200])


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
    "CHECK_LABEL",
    "CHECK_REQUESTER",
    "CapabilitiesMixin",
    "CapabilityError",
    "Fingerprint",
    "probes_from_json",
    "probes_json",
    "report_from_json",
]
