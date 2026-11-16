"""M5n 순수 계층 — 능력: 프로브 정규화 · 지문 · 점검 상태 · 판정 표 · 호스트 키 · 도구 측정.

명세는 `docs/m5n-workplan.md` §4(지문 §4.1 · 판정 §4.3 · 점검 §4.4)와 §11.4 의 이름이다. 시나리오와
가정은 `docs/m5n-test-scenarios-a.md`. 여기 있는 것은 시계도 DB 도 소켓도 안 본다 —
`runner.measure_tools` 만 `shutil.which` 로 tmp 폴더를 본다.

증인은 2026-11-06 의 Xcode 사고다(§1.3): 지문이 `Xcode 26.5 → 27.2` 로 바뀐 날 그 머신의
`scenario-qa` 는 `sim-check` 를 **현재 지문으로** 다시 통과하기 전까지 받지 않는다.
"""

import hashlib
import os
import stat
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path

import pytest

from jobfactory import NOW
from remote_ci_monitor import runner
from remote_ci_monitor.core import capability as cap
from remote_ci_monitor.core.model import Preset

GEN = "1731740000:3"
MAX_AGE = 1800.0  # 3 × probe_interval_seconds(600) — 호출자가 곱해서 넘긴다

XCODE_265 = cap.ProbeResult(cap.PROBE_OK, "Xcode 26.5 · Build version 26F75")
XCODE_272 = cap.ProbeResult(cap.PROBE_OK, "Xcode 27.2 · Build version 27B52")
IDB_118 = cap.ProbeResult(cap.PROBE_OK, "idb_companion 1.1.8")


def probes_265():
    return {"xcode": XCODE_265, "idb": IDB_118}


def probes_272():
    return {"xcode": XCODE_272, "idb": IDB_118}


FP_265 = cap.fingerprint_hash(probes_265())
FP_272 = cap.fingerprint_hash(probes_272())


def preset(name="scenario-qa", *, checks=(), requires=()):
    return Preset(name=name, argv=("bash", "qa.sh"), checks=tuple(checks), requires=requires)


def report(
    facts=None,
    *,
    preset_name="scenario-qa",
    probes=None,
    fp_hash="auto",
    generation=GEN,
    measured_at=NOW,
    worker="mac-mini",
):
    """그 워커의 최신 보고. `facts` 는 `preset_name` 의 PresetFacts(기본: 아무것도 안 빠짐)."""
    probes = probes if probes is not None else probes_272()
    fp = cap.Fingerprint(
        probes=probes,
        hash=cap.fingerprint_hash(probes) if fp_hash == "auto" else fp_hash,
        stale=False,
    )
    return cap.CapabilityReport(
        worker=worker,
        generation=generation,
        host_key="a1f3c9d2e4b5a6f7",
        accept_any=True,
        fingerprint=fp,
        presets={preset_name: facts if facts is not None else cap.PresetFacts()},
        measured_at=measured_at,
    )


def rec(job_id, state, fp=FP_272, name="sim-check"):
    return cap.CheckRecord(preset=name, job_id=job_id, job_state=state, fingerprint_hash=fp)


def judge(p, r, records=(), *, now=NOW, generation=GEN, max_age=MAX_AGE):
    return cap.verdict(
        p, r, generation=generation, now=now, max_age_seconds=max_age, check_records=records
    )


# ── 상수 — 화면 · DB · API 가 이 문자열을 그대로 싣는다 ─────────────────────────────


def test_constants_are_the_wire_strings():
    assert cap.LOCAL_WORKER == "@local"
    assert (cap.PROBE_OK, cap.PROBE_ERROR, cap.PROBE_MISSING, cap.PROBE_TIMEOUT) == (
        "ok",
        "error",
        "missing",
        "timeout",
    )
    assert (cap.ELIGIBLE, cap.INELIGIBLE, cap.UNKNOWN) == ("eligible", "ineligible", "unknown")
    assert (cap.CHECK_PASSED, cap.CHECK_RUNNING, cap.CHECK_FAILED, cap.CHECK_NEEDED) == (
        "passed",
        "running",
        "failed",
        "needed",
    )


def test_probe_result_is_frozen_and_not_stale_by_default():
    r = cap.ProbeResult(cap.PROBE_OK, "v1")
    assert r.stale is False
    with pytest.raises(FrozenInstanceError):
        r.value = "v2"  # type: ignore[misc]


# ── normalize_probe_output (§4.1) ──────────────────────────────────────────────


def test_normalize_joins_the_first_two_nonempty_lines():
    out = b"Xcode 27.2\nBuild version 27B52\nthird line\n"
    assert cap.normalize_probe_output(out, 0) == cap.ProbeResult(
        "ok", "Xcode 27.2 · Build version 27B52"
    )


def test_normalize_skips_blank_and_whitespace_only_lines():
    out = "\n\n  Flutter 3.47.8 • channel stable  \n   \n\t\nDart 3.9.0\nDevTools 2.40\n"
    assert (
        cap.normalize_probe_output(out, 0).value == "Flutter 3.47.8 • channel stable · Dart 3.9.0"
    )


def test_normalize_accepts_str_and_bytes_alike():
    assert cap.normalize_probe_output("a\nb\n", 0) == cap.normalize_probe_output(b"a\nb\n", 0)


def test_normalize_strips_control_characters():
    """ANSI 색 · 벨 · NUL · DEL 이 지문을 흔들면 같은 버전이 「바뀜」으로 보인다."""
    out = b"Xco\x1bde\x00 27.2\x07\x7f\n"
    assert cap.normalize_probe_output(out, 0).value == "Xcode 27.2"


def test_normalize_cuts_at_120_characters():
    out = "v" * 200 + "\n"
    assert cap.normalize_probe_output(out, 0).value == "v" * 120


def test_normalize_cut_applies_after_joining():
    out = "a" * 100 + "\n" + "b" * 100 + "\n"
    value = cap.normalize_probe_output(out, 0).value
    assert value == ("a" * 100 + " · " + "b" * 100)[:120]
    assert len(value) == 120


def test_normalize_decodes_invalid_utf8_with_replacement():
    assert cap.normalize_probe_output(b"tool 1.0 \xff\n", 0).value == "tool 1.0 �"


def test_normalize_ok_with_no_output_is_an_empty_value():
    assert cap.normalize_probe_output(b"", 0) == cap.ProbeResult("ok", "")
    assert cap.normalize_probe_output(None, 0) == cap.ProbeResult("ok", "")


def test_normalize_nonzero_exit_is_error_with_exit_code_value():
    """출력은 버린다 — 실패 문구에는 경로가 섞이기 쉽고 값이 매번 흔들린다."""
    assert cap.normalize_probe_output(b"xcode-select: error: /Library/...\n", 1) == (
        cap.ProbeResult("error", "exit 1")
    )
    assert cap.normalize_probe_output(b"", 72) == cap.ProbeResult("error", "exit 72")


def test_normalize_signal_exit_keeps_the_negative_code():
    assert cap.normalize_probe_output(None, -9) == cap.ProbeResult("error", "exit -9")


def test_normalize_missing_executable():
    assert cap.normalize_probe_output(None, None, missing=True) == cap.ProbeResult("missing", None)


def test_normalize_timeout_has_no_value():
    assert cap.normalize_probe_output(b"partial\n", None, timed_out=True) == cap.ProbeResult(
        "timeout", None
    )


# ── fingerprint_hash (§3 · §4.1) ───────────────────────────────────────────────


def test_hash_is_16_lowercase_hex():
    h = cap.fingerprint_hash(probes_272())
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_ignores_mapping_order():
    a = {"xcode": XCODE_272, "idb": IDB_118}
    b = {"idb": IDB_118, "xcode": XCODE_272}
    assert cap.fingerprint_hash(a) == cap.fingerprint_hash(b)


def test_hash_changes_with_a_value():
    assert FP_265 != FP_272


def test_hash_changes_with_state_even_without_value():
    missing = {"idb": cap.ProbeResult("missing", None)}
    error = {"idb": cap.ProbeResult("error", "exit 1")}
    ok = {"idb": IDB_118}
    hashes = {cap.fingerprint_hash(missing), cap.fingerprint_hash(error), cap.fingerprint_hash(ok)}
    assert len(hashes) == 3


def test_hash_excludes_timeout_probes():
    """결정 90 — 한 번 느린 것만으로 지문이 출렁이면 점검이 다시 돈다(뮤테이션 ③)."""
    with_timeout = {**probes_272(), "java": cap.ProbeResult("timeout", None)}
    assert cap.fingerprint_hash(with_timeout) == FP_272


def test_hash_ignores_the_stale_flag():
    stale = {"xcode": replace(XCODE_272, stale=True), "idb": IDB_118}
    assert cap.fingerprint_hash(stale) == FP_272


def test_hash_of_no_probes_is_still_a_known_fingerprint():
    """`[[probes]]` 가 없는 설정도 점검을 쓸 수 있어야 한다 — 빈 지문은 「모름」이 아니다."""
    h = cap.fingerprint_hash({})
    assert len(h) == 16
    assert h == cap.fingerprint_hash({})


def test_hash_does_not_depend_on_probe_name_prefixes():
    """(이름, 상태, 값) 을 경계 없이 이어 붙이면 `a`+`bc` 와 `ab`+`c` 가 같아진다."""
    a = {"ab": cap.ProbeResult("ok", "c")}
    b = {"a": cap.ProbeResult("ok", "bc")}
    assert cap.fingerprint_hash(a) != cap.fingerprint_hash(b)


# ── settle_fingerprint (§4.1 · 결정 90 · 엣지 2) ───────────────────────────────


def test_settle_without_timeouts_is_the_measurement_itself():
    fp = cap.settle_fingerprint(probes_272(), probes_265())
    assert dict(fp.probes) == probes_272()
    assert fp.hash == FP_272
    assert fp.stale is False


def test_settle_timeout_keeps_previous_value_and_marks_stale():
    current = {"xcode": cap.ProbeResult("timeout", None), "idb": IDB_118}
    fp = cap.settle_fingerprint(current, probes_272())
    assert fp.probes["xcode"] == cap.ProbeResult("ok", XCODE_272.value, stale=True)
    assert fp.probes["idb"] == IDB_118
    assert fp.stale is True
    assert fp.hash == FP_272  # 지문은 그대로 — 점검이 다시 돌지 않는다


def test_settle_timeout_carries_a_previous_missing_state():
    previous = {"xcode": XCODE_272, "idb": cap.ProbeResult("missing", None)}
    current = {"xcode": XCODE_272, "idb": cap.ProbeResult("timeout", None)}
    fp = cap.settle_fingerprint(current, previous)
    assert fp.probes["idb"] == cap.ProbeResult("missing", None, stale=True)
    assert fp.hash == cap.fingerprint_hash(previous)


def test_settle_timeout_twice_in_a_row_keeps_carrying():
    """앞 회차가 이미 이어 쓴 값(stale)이어도 그 값을 다시 잇는다 — 지문은 계속 같다."""
    first = cap.settle_fingerprint(
        {"xcode": cap.ProbeResult("timeout", None), "idb": IDB_118}, probes_272()
    )
    second = cap.settle_fingerprint(
        {"xcode": cap.ProbeResult("timeout", None), "idb": IDB_118}, first.probes
    )
    assert second.probes["xcode"] == cap.ProbeResult("ok", XCODE_272.value, stale=True)
    assert second.hash == FP_272


def test_settle_first_round_timeout_makes_the_fingerprint_unknown():
    current = {"xcode": cap.ProbeResult("timeout", None), "idb": IDB_118}
    fp = cap.settle_fingerprint(current, None)
    assert fp.hash is None
    assert fp.probes["xcode"].state == "timeout"


def test_settle_timeout_of_a_newly_added_probe_is_unknown():
    """앞 보고에 그 프로브가 없었다(설정에 새로 더함) — 이을 앞 값이 없으니 모른다."""
    current = {**probes_272(), "java": cap.ProbeResult("timeout", None)}
    fp = cap.settle_fingerprint(current, probes_272())
    assert fp.hash is None


def test_settle_drops_probes_that_are_no_longer_measured():
    """설정에서 뺀 프로브의 앞 값을 지문에 되살리지 않는다."""
    fp = cap.settle_fingerprint({"xcode": XCODE_272}, probes_272())
    assert set(fp.probes) == {"xcode"}
    assert fp.hash == cap.fingerprint_hash({"xcode": XCODE_272})


# ── changed_probes ─────────────────────────────────────────────────────────────


def test_changed_probes_reports_old_and_new_values():
    assert cap.changed_probes(probes_265(), probes_272()) == {
        "xcode": (XCODE_265.value, XCODE_272.value)
    }


def test_changed_probes_is_empty_when_nothing_changed():
    assert cap.changed_probes(probes_272(), probes_272()) == {}


def test_changed_probes_without_previous_report_is_empty():
    """첫 보고는 비교할 것이 없다 — 「환경 바뀜」 칩을 지어내지 않는다."""
    assert cap.changed_probes(None, probes_272()) == {}


def test_changed_probes_added_and_removed():
    old = {"xcode": XCODE_272}
    new = {"idb": IDB_118}
    assert cap.changed_probes(old, new) == {
        "xcode": (XCODE_272.value, None),
        "idb": (None, IDB_118.value),
    }


def test_changed_probes_state_change_to_missing():
    new = {"xcode": XCODE_272, "idb": cap.ProbeResult("missing", None)}
    assert cap.changed_probes(probes_272(), new) == {"idb": (IDB_118.value, None)}


def test_changed_probes_error_code_change():
    old = {"idb": cap.ProbeResult("error", "exit 1")}
    new = {"idb": cap.ProbeResult("error", "exit 2")}
    assert cap.changed_probes(old, new) == {"idb": ("exit 1", "exit 2")}


def test_changed_probes_ignores_a_stale_carry():
    new = {"xcode": replace(XCODE_272, stale=True), "idb": IDB_118}
    assert cap.changed_probes(probes_272(), new) == {}


# ── check_state (§4.4) ─────────────────────────────────────────────────────────


def test_check_state_without_records_is_needed():
    assert cap.check_state([], FP_272) == (cap.CHECK_NEEDED, None)


def test_check_state_success_under_current_fingerprint_passes():
    r = rec(10, "succeeded")
    assert cap.check_state([r], FP_272) == (cap.CHECK_PASSED, r)


def test_check_state_success_under_old_fingerprint_does_not_count():
    """Xcode 재연의 핵심 — 26.5 로 통과한 기록은 27.2 에서 아무것도 증명하지 않는다."""
    assert cap.check_state([rec(10, "succeeded", fp=FP_265)], FP_272) == (cap.CHECK_NEEDED, None)


@pytest.mark.parametrize("state", ["uploading", "queued", "running", "cancelling"])
def test_check_state_active_job_is_running(state):
    r = rec(11, state)
    assert cap.check_state([r], FP_272) == (cap.CHECK_RUNNING, r)


@pytest.mark.parametrize("state", ["failed", "timed_out"])
def test_check_state_failure_is_failed(state):
    r = rec(3620, state)
    assert cap.check_state([r], FP_272) == (cap.CHECK_FAILED, r)


@pytest.mark.parametrize("state", ["cancelled", "lost"])
def test_check_state_cancelled_or_lost_is_needed_with_that_record(state):
    r = rec(12, state)
    assert cap.check_state([r], FP_272) == (cap.CHECK_NEEDED, r)


def test_check_state_uses_the_largest_job_id_not_list_order():
    older_ok = rec(10, "succeeded")
    newer_fail = rec(12, "failed")
    assert cap.check_state([newer_fail, older_ok], FP_272) == (cap.CHECK_FAILED, newer_fail)
    newest_ok = rec(15, "succeeded")
    assert cap.check_state([newer_fail, newest_ok, older_ok], FP_272) == (
        cap.CHECK_PASSED,
        newest_ok,
    )


def test_check_state_largest_id_is_taken_within_the_current_fingerprint_only():
    ok_now = rec(10, "succeeded")
    fail_old = rec(20, "failed", fp=FP_265)
    assert cap.check_state([ok_now, fail_old], FP_272) == (cap.CHECK_PASSED, ok_now)


def test_check_state_with_unknown_fingerprint_is_needed():
    """지문을 모르면 「현재 지문으로 통과」가 성립하지 않는다 — None 끼리 맞추지 않는다."""
    assert cap.check_state([rec(10, "succeeded", fp=None)], None) == (cap.CHECK_NEEDED, None)


# ── verdict — §4.3 표, 위에서 아래로 첫 번째로 걸리는 것 ───────────────────────────


def test_verdict_row1_no_report_is_unknown_report_missing():
    v = judge(preset(), None)
    assert (v.state, v.code) == (cap.UNKNOWN, "report_missing")


def test_verdict_row1_preset_absent_from_report_is_report_missing():
    """세대는 맞는데 그 프리셋의 사실이 없다 — 「없다」로도 「있다」로도 부르지 않는다."""
    r = report(preset_name="other")
    v = judge(preset(), r)
    assert (v.state, v.code) == (cap.UNKNOWN, "report_missing")


def test_verdict_row2_generation_mismatch_is_report_stale():
    v = judge(preset(), report(generation="1731740000:2"))
    assert (v.state, v.code) == (cap.UNKNOWN, "report_stale")


def test_verdict_row3_old_report_is_report_stale():
    old = report(measured_at=NOW - timedelta(seconds=MAX_AGE + 1))
    v = judge(preset(), old)
    assert (v.state, v.code) == (cap.UNKNOWN, "report_stale")


def test_verdict_row3_boundary_exactly_max_age_is_fresh():
    edge = report(measured_at=NOW - timedelta(seconds=MAX_AGE))
    v = judge(preset(), edge)
    assert (v.state, v.code) == (cap.ELIGIBLE, "ok")


def test_verdict_row4_missing_tool_is_ineligible_with_tool_names():
    r = report(cap.PresetFacts(missing_tools=("fvm", "gitleaks")))
    v = judge(preset(requires=("fvm", "gitleaks")), r)
    assert (v.state, v.code) == (cap.INELIGIBLE, "tool_missing")
    assert list(v.detail["tools"]) == ["fvm", "gitleaks"]


def test_verdict_row5_workspace_only_tool_is_unknown_not_ineligible():
    """엣지 6 — 작업 공간이 오기 전엔 모르는 것을 「없다」로 부르지 않는다(결정 93)."""
    r = report(cap.PresetFacts(workspace_tools=("gradlew",)))
    v = judge(preset(requires=("gradlew",)), r)
    assert (v.state, v.code) == (cap.UNKNOWN, "workspace_tool")
    assert list(v.detail["tools"]) == ["gradlew"]


def test_verdict_row6_failed_check_is_ineligible_with_check_and_job():
    v = judge(preset(checks=["sim-check"]), report(), [rec(3620, "failed")])
    assert (v.state, v.code) == (cap.INELIGIBLE, "check_failed")
    assert v.detail["check"] == "sim-check"
    assert v.detail["job_id"] == 3620


def test_verdict_row7_running_check_is_unknown_check_running():
    v = judge(preset(checks=["sim-check"]), report(), [rec(3621, "queued")])
    assert (v.state, v.code) == (cap.UNKNOWN, "check_running")
    assert v.detail["check"] == "sim-check"
    assert v.detail["job_id"] == 3621


def test_verdict_row8_no_pass_under_current_fingerprint_is_check_needed():
    """엣지 1(순수 부분) — 지문이 바뀐 날 앞 지문의 통과는 안 센다."""
    v = judge(preset(checks=["sim-check"]), report(), [rec(3500, "succeeded", fp=FP_265)])
    assert (v.state, v.code) == (cap.UNKNOWN, "check_needed")
    assert v.detail["check"] == "sim-check"


def test_verdict_row9_unknown_fingerprint_with_checks():
    """엣지 2 — 첫 회차 timeout 으로 지문이 None 이면 점검 기록과 맞출 수 없다."""
    r = report(fp_hash=None)
    v = judge(preset(checks=["sim-check"]), r, [rec(3500, "succeeded", fp=None)])
    assert (v.state, v.code) == (cap.UNKNOWN, "fingerprint_unknown")


def test_verdict_ok_when_every_check_passed():
    v = judge(preset(checks=["sim-check"]), report(), [rec(3643, "succeeded")])
    assert (v.state, v.code) == (cap.ELIGIBLE, "ok")
    assert dict(v.detail) == {}


def test_verdict_unknown_fingerprint_without_checks_is_ok():
    v = judge(preset(), report(fp_hash=None))
    assert (v.state, v.code) == (cap.ELIGIBLE, "ok")


def test_verdict_missing_passthrough_does_not_block():
    """결정 92 — 빈 값이 의도일 수 있다(M5m). 판정에 안 넣는다."""
    r = report(cap.PresetFacts(missing_passthrough=("JAVA_HOME",)))
    v = judge(preset(), r)
    assert (v.state, v.code) == (cap.ELIGIBLE, "ok")


def test_verdict_stale_fingerprint_probe_still_counts_as_the_same_fingerprint():
    """timeout 을 이어 쓴 지문(stale)의 해시는 그대로다 — 통과 기록이 계속 유효하다."""
    probes = {"xcode": replace(XCODE_272, stale=True), "idb": IDB_118}
    r = report(probes=probes)
    v = judge(preset(checks=["sim-check"]), r, [rec(3643, "succeeded")])
    assert (v.state, v.code) == (cap.ELIGIBLE, "ok")


def test_verdict_ignores_records_of_other_check_presets():
    records = [rec(40, "succeeded", name="other-check")]
    v = judge(preset(checks=["sim-check"]), report(), records)
    assert (v.state, v.code) == (cap.UNKNOWN, "check_needed")


# ── verdict — 표의 순서(첫 번째가 이긴다) ─────────────────────────────────────────


def test_precedence_generation_beats_missing_tool():
    r = report(cap.PresetFacts(missing_tools=("fvm",)), generation="old:1")
    v = judge(preset(requires=("fvm",)), r)
    assert v.code == "report_stale"


def test_precedence_missing_beats_workspace_tool():
    r = report(cap.PresetFacts(missing_tools=("fvm",), workspace_tools=("gradlew",)))
    v = judge(preset(requires=("fvm", "gradlew")), r)
    assert (v.state, v.code) == (cap.INELIGIBLE, "tool_missing")
    assert list(v.detail["tools"]) == ["fvm"]


def test_precedence_missing_tool_beats_failed_check():
    r = report(cap.PresetFacts(missing_tools=("fvm",)))
    v = judge(preset(checks=["sim-check"], requires=("fvm",)), r, [rec(3620, "failed")])
    assert v.code == "tool_missing"


def test_precedence_workspace_tool_beats_failed_check():
    r = report(cap.PresetFacts(workspace_tools=("gradlew",)))
    v = judge(preset(checks=["sim-check"], requires=("gradlew",)), r, [rec(3620, "failed")])
    assert v.code == "workspace_tool"


def test_precedence_failed_check_beats_running_check_across_checks():
    """`checks` 의 순서와 무관하게 「실패」가 「도는 중」보다 앞줄이다."""
    p = preset(checks=["a-check", "b-check"])
    records = [rec(50, "running", name="a-check"), rec(51, "failed", name="b-check")]
    v = judge(p, report(), records)
    assert (v.state, v.code) == (cap.INELIGIBLE, "check_failed")
    assert v.detail["check"] == "b-check"
    assert v.detail["job_id"] == 51


def test_precedence_running_check_beats_needed_check():
    p = preset(checks=["a-check", "b-check"])
    records = [rec(52, "running", name="b-check")]
    v = judge(p, report(), records)
    assert (v.state, v.code) == (cap.UNKNOWN, "check_running")
    assert v.detail["check"] == "b-check"


def test_precedence_needed_names_the_first_check_in_declared_order():
    p = preset(checks=["b-check", "a-check"])
    v = judge(p, report(), [])
    assert v.code == "check_needed"
    assert v.detail["check"] == "b-check"


def test_precedence_stale_report_beats_failed_check():
    old = report(measured_at=NOW - timedelta(seconds=MAX_AGE + 60))
    v = judge(preset(checks=["sim-check"]), old, [rec(3620, "failed")])
    assert v.code == "report_stale"


# ── host_key_from (§3) ─────────────────────────────────────────────────────────


def test_host_key_is_sha256_prefix_of_the_hardware_id():
    raw = "8F1C2D3E-4A5B-6C7D-8E9F-0A1B2C3D4E5F"
    assert cap.host_key_from(raw, "mbp") == hashlib.sha256(raw.encode()).hexdigest()[:16]


def test_host_key_ignores_surrounding_whitespace():
    """`/etc/machine-id` 는 줄바꿈으로 끝난다 — 같은 기계가 두 호스트가 되면 안 된다."""
    raw = "0123456789abcdef0123456789abcdef"
    assert cap.host_key_from(raw + "\n", "w") == cap.host_key_from(raw, "w")


def test_host_key_never_contains_the_raw_id():
    raw = "8F1C2D3E-4A5B-6C7D-8E9F-0A1B2C3D4E5F"
    assert raw.lower()[:8] not in cap.host_key_from(raw, "mbp")


@pytest.mark.parametrize("raw", [None, "", "   \n"])
def test_host_key_falls_back_to_the_worker(raw):
    assert cap.host_key_from(raw, "mbp") == "worker:mbp"
    assert cap.host_key_from(raw, cap.LOCAL_WORKER) == "worker:@local"


def test_same_hardware_gives_the_same_host_for_two_workers():
    """운영 모양 — Mac mini 의 로컬 레인과 mac2-worker 는 같은 호스트다(§1.1)."""
    raw = "SAME-UUID"
    assert cap.host_key_from(raw, cap.LOCAL_WORKER) == cap.host_key_from(raw, "mac2-worker")


# ── runner.measure_tools (§4.3 5번 · §11.4) ────────────────────────────────────


def _tool(folder: Path, name: str, *, executable: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text("#!/bin/sh\nexit 0\n")
    mode = stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if executable else 0)
    p.chmod(mode)
    return p


def _path(*entries) -> dict[str, str]:
    return {"PATH": os.pathsep.join(str(e) for e in entries)}


def test_measure_tools_finds_tools_on_absolute_entries(tmp_path):
    bin_dir = tmp_path / "bin"
    _tool(bin_dir, "fvm")
    assert runner.measure_tools(("fvm",), _path(bin_dir)) == ([], [])


def test_measure_tools_absent_with_only_absolute_entries_is_missing(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert runner.measure_tools(("fvm",), _path(bin_dir)) == (["fvm"], [])


@pytest.mark.parametrize("relative", ["tools", ".", "", "./node_modules/.bin"])
def test_measure_tools_absent_with_a_relative_entry_is_workspace_only(tmp_path, relative):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {"PATH": os.pathsep.join([str(bin_dir), relative])}
    assert runner.measure_tools(("gradlew",), env) == ([], ["gradlew"])


def test_measure_tools_does_not_resolve_relative_entries_against_its_own_cwd(tmp_path, monkeypatch):
    """fail-open 금지 — 측정 프로세스의 cwd 에 `tools/fvm` 이 있어도 잡의 워크스페이스는 다르다."""
    _tool(tmp_path / "tools", "fvm")
    monkeypatch.chdir(tmp_path)
    assert runner.measure_tools(("fvm",), {"PATH": "tools"}) == ([], ["fvm"])


def test_measure_tools_absolute_declaration_is_checked_in_place(tmp_path):
    present = _tool(tmp_path / "opt", "idb")
    absent = tmp_path / "opt" / "adb"
    env = {"PATH": "tools"}  # 상대 항목이 있어도 절대경로 선언은 PATH 와 무관하다
    assert runner.measure_tools((str(present), str(absent)), env) == ([str(absent)], [])


def test_measure_tools_without_path_does_not_fall_back_to_the_process_path():
    """launchd 사고 — 셸에선 되는데 잡에선 안 되는 것을 숨기지 않는다."""
    assert runner.measure_tools(("sh",), {}) == (["sh"], [])


def test_measure_tools_non_executable_file_is_missing(tmp_path):
    bin_dir = tmp_path / "bin"
    _tool(bin_dir, "fvm", executable=False)
    assert runner.measure_tools(("fvm",), _path(bin_dir)) == (["fvm"], [])


def test_measure_tools_keeps_declared_order_in_both_lists(tmp_path):
    bin_dir = tmp_path / "bin"
    _tool(bin_dir, "git")
    env = {"PATH": os.pathsep.join([str(bin_dir), "tools"])}
    absolute_only = _path(bin_dir)
    assert runner.measure_tools(("zz", "git", "aa"), env) == ([], ["zz", "aa"])
    assert runner.measure_tools(("zz", "git", "aa"), absolute_only) == (["zz", "aa"], [])


def test_measure_tools_nothing_required():
    assert runner.measure_tools((), {"PATH": "/usr/bin"}) == ([], [])
