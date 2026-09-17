"""`::rcm::progress::` — 현재 스텝 **안**의 세부 진행(`docs/release-contract.md` §3 · 화면 명세 44).

문법: `::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]`. 잠그는 것:
- 파서: 정수 분모(`total ≥ 1`, `0 ≤ done ≤ total`) · 아는 state 만 · 빈 unit 은 마커가 아니다 ·
  unit 은 스텝 이름 규칙(제어문자 제거 · 120자) · note 는 요약 규칙(200자).
- 모임: `sub` 는 마지막 마커, `units[]` 는 단위별 **마지막** 상태(처음 본 순서) · 500개 상한을
  넘으면 `units_truncated` 이되 `done/total` 은 계속 센다 · 새 `::rcm::step::` 이 비운다 ·
  첫 스텝 전에 온 것도 센다.
- 문서: `progress.sub`·`units`·`units_truncated` 가 큐 행에 실린다(키만 더한다, schema_version 1).
  문서 문면은 `tests/test_docs_progress_sub.py` 가 잠근다(mutcheck 의 복사본에는 문서가 없다).
- 진짜 생산자 → 진짜 소비자: 워커가 실행한 스크립트의 줄이 저장소를 지나 같은 값으로 나온다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from jobfactory import CFG, MEDIANS, NOW, PRESETS, ago, default_workers, job
from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core.model import RUNNING, SUCCEEDED
from remote_ci_monitor.core.progress import (
    MAX_PROGRESS_UNITS,
    MAX_STEP_NAME,
    MAX_SUMMARY,
    PROGRESS_STATES,
    Marker,
    ProgressMark,
    parse_marker,
    parse_progress_value,
    progress_for_job,
    progress_from_markers,
)
from remote_ci_monitor.core.queue import compute_queue
from remote_ci_monitor.core.status import iso, progress_json, queue_row_json
from remote_ci_monitor.store import Store
from test_worker import enqueue, make_config, run_one, sh

# ── 파서 ─────────────────────────────────────────────────────────────────────


def test_parse_progress_marker_valid_forms():
    assert parse_marker("::rcm::progress::41/68::inquiry_photo/android::run") == (
        "progress",
        "41/68::inquiry_photo/android::run",
    )
    assert parse_marker("::rcm::progress::0/1::lock devices::wait::rank 2 · 12m to start\n") == (
        "progress",
        "0/1::lock devices::wait::rank 2 · 12m to start",
    )
    # 경계: done == total, 앞뒤 공백은 정리된다
    assert parse_marker("::rcm::progress:: 3/3 :: test :: ok ") == ("progress", "3/3::test::ok")
    # note 안의 `::` 는 note 의 일부다
    assert parse_marker("::rcm::progress::1/2::a::fail::x::y") == ("progress", "1/2::a::fail::x::y")


def test_parse_progress_marker_rejects_bad_denominators_states_and_units():
    bad = [
        "::rcm::progress::2/1::a::run",  # done > total
        "::rcm::progress::0/0::a::run",  # total < 1
        "::rcm::progress::-1/3::a::run",
        "::rcm::progress::1.5/3::a::run",
        "::rcm::progress::1/x::a::run",
        "::rcm::progress::²/3::a::run",  # 유니코드 숫자 — isdigit 은 참이지만 int 는 못 읽는다
        "::rcm::progress::1/٣::a::run",
        "::rcm::progress::1::a::run",  # 슬래시 없음
        "::rcm::progress::1/3::a::running",  # 모르는 state
        "::rcm::progress::1/3::a",  # state 없음
        "::rcm::progress::1/3::::run",  # 빈 unit
        "::rcm::progress::1/3::\x1b\x07 \t::run",  # 제어문자·공백뿐인 unit
        "::rcm::progress::",
        "::rcm::progress::1/3",
        " ::rcm::progress::1/3::a::run",  # 줄 머리가 아니다
    ]
    for line in bad:
        assert parse_marker(line) is None, line


def test_parse_progress_marker_cleans_and_caps_unit_and_note():
    kind, value = parse_marker(
        "::rcm::progress::1/3::" + "\x1bu\rnit\x07" + "x" * 300 + "::env::" + "n\x00ote" + "y" * 300
    )
    assert kind == "progress"
    mark = parse_progress_value(value)
    assert mark is not None
    assert mark.unit == ("unit" + "x" * 300)[:MAX_STEP_NAME]
    assert mark.note == ("note" + "y" * 300)[:MAX_SUMMARY]
    assert mark.state == "env"
    # 정규형은 되읽어도 같다(저장소 왕복)
    assert parse_progress_value(mark.value) == mark


def test_parse_progress_value_note_of_only_control_chars_is_no_note():
    mark = parse_progress_value("1/3::a::ok::\x07\x1b")
    assert mark == ProgressMark(done=1, total=3, unit="a", state="ok", note=None)
    assert mark.value == "1/3::a::ok"


def test_progress_states_are_the_contract_vocabulary():
    assert PROGRESS_STATES == ("run", "ok", "fail", "skip", "env", "review", "blocked", "wait")
    assert MAX_PROGRESS_UNITS == 500


# ── 모임 ─────────────────────────────────────────────────────────────────────

START = ago(minutes=5)


def at(i: int) -> datetime:
    return START + timedelta(seconds=i)


def pm(i: int, value: str) -> Marker:
    return Marker(at(i), "progress", value)


def test_sub_is_the_last_marker_and_units_keep_the_last_state_in_first_seen_order():
    markers = [
        Marker(at(1), "step", "scenarios"),
        pm(2, "0/4::a/ios::run"),
        pm(3, "1/4::a/ios::ok::+12 ~0 -0"),
        pm(4, "1/4::a/android::run"),
        pm(5, "2/4::a/android::fail::+11 ~0 -1"),
        pm(6, "2/4::b/ios::run"),
    ]
    p = progress_from_markers(markers, started_at=START, finished_at=None, now=NOW, exit_code=None)
    assert p.sub is not None
    assert (p.sub.done, p.sub.total, p.sub.unit, p.sub.state, p.sub.note) == (
        2,
        4,
        "b/ios",
        "run",
        None,
    )
    assert p.sub.at == at(6)
    assert [(u.unit, u.state, u.note) for u in p.units] == [
        ("a/ios", "ok", "+12 ~0 -0"),
        ("a/android", "fail", "+11 ~0 -1"),
        ("b/ios", "run", None),
    ]
    assert p.units[0].at == at(3)  # 마지막으로 본 시각
    assert p.units_truncated is False
    # 스텝 자체는 그대로다 — sub 는 스텝 수를 바꾸지 않는다
    assert p.steps_done == 0 and p.current_name == "scenarios"


def test_a_new_step_resets_the_sub_progress():
    markers = [
        Marker(at(1), "step", "one"),
        pm(2, "3/3::x::ok"),
        Marker(at(3), "step", "two"),
    ]
    p = progress_from_markers(markers, started_at=START, finished_at=None, now=NOW, exit_code=None)
    assert p.sub is None and p.units == () and p.units_truncated is False
    # 다음 스텝에서 다시 찍으면 그 스텝의 것만 보인다
    markers.append(pm(4, "1/9::y::run"))
    p = progress_from_markers(markers, started_at=START, finished_at=None, now=NOW, exit_code=None)
    assert p.sub is not None and (p.sub.done, p.sub.total, p.sub.unit) == (1, 9, "y")
    assert [u.unit for u in p.units] == ["y"]


def test_progress_before_the_first_step_still_counts():
    p = progress_from_markers(
        [pm(1, "2/5::child::run")], started_at=START, finished_at=None, now=NOW, exit_code=None
    )
    assert p.steps == () and p.steps_total is None
    assert p.sub is not None and p.sub.done == 2 and p.sub.total == 5
    assert [u.unit for u in p.units] == ["child"]


def test_unit_cap_keeps_counting_but_stops_adding_cells():
    markers = [Marker(at(0), "step", "s")]
    n = MAX_PROGRESS_UNITS + 7
    for i in range(n):
        markers.append(pm(i + 1, f"{i + 1}/{n}::u{i}::ok"))
    # 이미 격자에 있는 단위는 상한을 넘겨도 계속 갱신된다
    markers.append(pm(n + 1, f"{n}/{n}::u0::fail"))
    p = progress_from_markers(markers, started_at=START, finished_at=None, now=NOW, exit_code=None)
    assert p.units_truncated is True
    assert len(p.units) == MAX_PROGRESS_UNITS
    assert p.sub is not None and (p.sub.done, p.sub.total, p.sub.unit) == (n, n, "u0")
    assert p.units[0].state == "fail"
    assert p.units[-1].unit == f"u{MAX_PROGRESS_UNITS - 1}"


def test_a_stored_row_that_does_not_parse_is_skipped_not_fatal():
    markers = [pm(1, "garbage"), pm(2, "1/2::a::ok")]
    p = progress_from_markers(markers, started_at=START, finished_at=None, now=NOW, exit_code=None)
    assert p.sub is not None and p.sub.unit == "a"


# ── 문서 ─────────────────────────────────────────────────────────────────────


def test_progress_json_carries_sub_units_and_the_truncation_flag():
    p = progress_from_markers(
        [Marker(at(1), "step", "s"), pm(2, "1/3::a::ok::fine"), pm(3, "1/3::b::run")],
        started_at=START,
        finished_at=None,
        now=NOW,
        exit_code=None,
    )
    doc = progress_json(p)
    assert doc is not None
    assert doc["sub"] == {
        "done": 1,
        "total": 3,
        "unit": "b",
        "state": "run",
        "note": None,
        "at": iso(at(3)),
    }
    assert doc["units"] == [
        {"unit": "a", "state": "ok", "note": "fine", "at": iso(at(2))},
        {"unit": "b", "state": "run", "note": None, "at": iso(at(3))},
    ]
    assert doc["units_truncated"] is False


def test_progress_json_without_progress_markers_has_null_sub_and_empty_units():
    doc = progress_json(
        progress_from_markers(
            [Marker(at(1), "step", "s")],
            started_at=START,
            finished_at=None,
            now=NOW,
            exit_code=None,
        )
    )
    assert doc is not None
    assert doc["sub"] is None and doc["units"] == [] and doc["units_truncated"] is False


def test_sub_progress_travels_in_the_queue_row_the_screen_reads():
    j = job(412, state=RUNNING, created_min=2, started_min=1)
    markers = [Marker(at(1), "step", "scenarios"), pm(2, "41/68::inquiry_photo/android::run")]
    p = progress_for_job(j, markers, NOW)
    rows = compute_queue(
        [j],
        workers=default_workers([412]),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
        progress={j.id: p},
    )
    doc = queue_row_json(rows[0])["progress"]
    assert doc is not None
    assert doc["sub"]["done"] == 41 and doc["sub"]["total"] == 68
    assert doc["sub"]["unit"] == "inquiry_photo/android" and doc["sub"]["state"] == "run"
    assert [u["unit"] for u in doc["units"]] == ["inquiry_photo/android"]


# ── 진짜 생산자 → 진짜 소비자 ────────────────────────────────────────────────


def test_a_real_script_line_reaches_the_store_and_comes_back_as_sub(tmp_path):
    cfg = make_config(tmp_path)
    cfg.presets = (
        parse_preset(
            sh(
                "chunks",
                "echo '::rcm::step::scenarios'; "
                "echo '::rcm::progress::0/2::a/ios::run'; "
                "echo '::rcm::progress::1/2::a/ios::ok::+3 ~0 -0'; "
                "echo '::rcm::progress::2/1::broken::run'; "  # 문법 위반 — 그냥 로그 줄이다
                "echo '::rcm::progress::1/2::a/android::run'; exit 0",
            )
        ),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jid = enqueue(store, cfg, "chunks")
        run_one(store, cfg, jid)
        j = store.get_job(jid)
        assert j.state == SUCCEEDED
        kinds = [(m.kind, m.value) for m in store.markers(jid)]
        assert kinds == [
            ("step", "scenarios"),
            ("progress", "0/2::a/ios::run"),
            ("progress", "1/2::a/ios::ok::+3 ~0 -0"),
            ("progress", "1/2::a/android::run"),
        ]
        log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
        assert "::rcm::progress::2/1::broken::run\n" in log  # 로그에는 그대로 남는다
        p = progress_from_markers(
            store.markers(jid),
            started_at=j.started_at,
            finished_at=j.finished_at,
            now=j.finished_at,
            exit_code=0,
        )
        assert p.sub is not None and (p.sub.done, p.sub.total, p.sub.unit, p.sub.state) == (
            1,
            2,
            "a/android",
            "run",
        )
        assert [(u.unit, u.state) for u in p.units] == [("a/ios", "ok"), ("a/android", "run")]
    finally:
        store.close()
