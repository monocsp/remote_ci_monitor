"""추측한 실패 스텝은 추측이라고 말한다 — 스키마 v10 · finish 왕복 · 상태 문서 · 알림 env.

2026-09-08~09 운영(게이트 145잡) 추적에서 나왔다. 실패 55건 중 16건의 `failed_step` 이
`build web` 이었는데 그 스텝은 로그에서 `ok: build/web` 으로 **성공**했다. 게이트가 스텝을
병렬로 돌린 뒤 마커를 정해진 순서로 몰아 찍고 `::rcm::step-end::fail` 은 하나도 안 찍어서,
`core/progress.py` 의 「종료 코드가 0 이 아니면 마지막 스텝이 범인」 폴백이 무죄인 스텝을
지목한 것이다.

마커만 보고 진짜 범인을 골라낼 방법은 없다. 그러니 추측을 더 똑똑하게 만들지 않고,
**추측이라고 밝힌다** — AGENTS.md 「Never invent a number. Unknown prints as `—`」.

키는 **더하기만** 한다(`/api/status` 스키마 v1 그대로). 저장은 필요하다: 끝난 잡의
`failed_step` 은 마커를 다시 파는 게 아니라 `jobs` 행에서 오기 때문이다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    LOST,
    QUEUED,
    SUCCEEDED,
    TIMED_OUT,
    Job,
    Requester,
    Source,
)
from remote_ci_monitor.core.notify import notify_env
from remote_ci_monitor.core.progress import Marker, progress_from_markers
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.core.render_text import render, render_queue_row
from remote_ci_monitor.core.status import progress_json, recent_json, status_json
from remote_ci_monitor.store import DB_VERSION, Store
from remote_ci_monitor.worker import outcome_for

NOW = datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")

#: 운영에서 본 모양 — 병렬로 돌리고 마커를 몰아 찍는다. `step-end` 는 하나도 없다.
PARALLEL_NAMES = ("test", "gitleaks", "build web")


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


def parallel_markers(*, fail_marker: bool = False) -> list[Marker]:
    out: list[Marker] = []
    for i, name in enumerate(PARALLEL_NAMES):
        out.append(Marker(at(i + 1), "step", name))
        if fail_marker and name == "test":
            out.append(Marker(at(i + 1.5), "step-end", "fail"))
    return out


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def enqueue(store: Store, *, tree: str = "9f8e", now: datetime = NOW) -> Job:
    inputs = {"scope": "full"}
    src = Source(
        mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash=tree, bytes=None
    )
    return store.create_job(
        preset="gate",
        inputs=inputs,
        key="gate:full",
        concurrency_group=None,
        source=src,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key("gate", inputs, tree),
        now=now,
        state=QUEUED,
    )


def running(store: Store, *, tree: str = "9f8e", now: datetime = NOW) -> Job:
    j = enqueue(store, tree=tree, now=now)
    claimed = store.claim(1, now + timedelta(seconds=1))
    assert claimed is not None and claimed.id == j.id
    return claimed


def job_columns(path: Path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(jobs)")}


# ── 종료 규칙(worker.outcome_for) ─────────────────────────────────────────────


def test_outcome_carries_the_guess_flag_for_a_parallel_script():
    oc = outcome_for(_job(), parallel_markers(), started=NOW, finished=at(60), rc=1)
    assert oc.state == FAILED
    assert oc.failed_step == "build web" and oc.failed_step_guessed is True


def test_outcome_is_certain_when_the_script_says_which_step_failed():
    oc = outcome_for(_job(), parallel_markers(fail_marker=True), started=NOW, finished=at(60), rc=1)
    assert oc.failed_step == "test" and oc.failed_step_guessed is False


def test_a_succeeded_job_has_no_failed_step_and_no_guess():
    oc = outcome_for(_job(), parallel_markers(), started=NOW, finished=at(60), rc=0)
    assert oc.state == SUCCEEDED
    assert oc.failed_step is None and oc.failed_step_guessed is False


@pytest.mark.parametrize(
    ("kw", "state"),
    [({"cancelled": True}, CANCELLED), ({"timed_out": True}, TIMED_OUT), ({"lost": True}, LOST)],
)
def test_a_killed_job_never_claims_a_step_as_a_fact(kw, state):
    """취소·타임아웃·유실은 **어떤 마커가 있든** 실패 스텝을 확정이라고 말하지 않는다.

    이 잡은 스텝이 실패해서 끝난 게 아니라 죽임을 당해서 끝났다. 종료 코드를 1 로 쳐서 폴백을
    태우므로 마커가 없으면 마지막 스텝을 고르고(추측), 스크립트가 앞 스텝에 `step-end::fail` 을
    찍어 뒀으면 그 이름을 고른다 — **그것도 「이 잡이 왜 끝났나」의 답은 아니다.**

    실제로 겪는 모양: 게이트가 `test` 실패를 찍고도 계속 돌다가 `build web` 에서 타임아웃으로
    죽는다. 그때 `test` 를 확정 실패로 단언하면 사람이 엉뚱한 데를 판다.
    """
    guessed_only = outcome_for(
        _job(), parallel_markers(), started=NOW, finished=at(60), rc=None, **kw
    )
    assert guessed_only.state == state
    assert guessed_only.failed_step == "build web" and guessed_only.failed_step_guessed is True

    confirmed_earlier = outcome_for(
        _job(), parallel_markers(fail_marker=True), started=NOW, finished=at(60), rc=None, **kw
    )
    assert confirmed_earlier.state == state
    assert confirmed_earlier.failed_step == "test"
    assert confirmed_earlier.failed_step_guessed is True  # 스텝은 정말 깨졌지만 사인은 아니다


def test_a_job_that_really_failed_still_reports_a_confirmed_step():
    """죽임당한 잡과 달리, 종료 코드로 실패한 잡의 확정 표시는 그대로다(회귀 금지)."""
    oc = outcome_for(_job(), parallel_markers(fail_marker=True), started=NOW, finished=at(60), rc=1)
    assert oc.state == FAILED
    assert oc.failed_step == "test" and oc.failed_step_guessed is False


def test_outcome_still_unpacks_as_the_old_three_tuple():
    """옛 호출부(`state, summary, failed_step = outcome_for(...)`)를 깨지 않는다."""
    state, summary, failed_step = outcome_for(
        _job(), parallel_markers(), started=NOW, finished=at(60), rc=1
    )
    assert state == FAILED and failed_step == "build web" and summary == "exit 1"


def _job() -> Job:
    return Job(
        id=412,
        preset="gate",
        inputs={"scope": "full"},
        key="gate:full",
        concurrency_group=None,
        source=Source(
            mode="tree",
            repo="org/app",
            base_sha="abc123f",
            dirty=True,
            tree_hash="9f8e",
            bytes=None,
        ),
        requester=ALICE,
        state="running",
        created_at=NOW,
        started_at=NOW,
        timeout_seconds=1200,
    )


# ── 스키마 v10 · 왕복 ──────────────────────────────────────────────────────────


def test_fresh_db_is_schema_v10_with_the_guess_column(store, tmp_path):
    assert store.user_version() == DB_VERSION and DB_VERSION >= 10
    assert "failed_step_guessed" in job_columns(tmp_path / "rcm.sqlite3")


def test_finish_persists_the_guess_and_reads_it_back(store):
    guessed = running(store, tree="a")
    assert store.finish(
        guessed.id,
        FAILED,
        now=at(10),
        exit_code=1,
        summary="exit 1",
        failed_step="build web",
        failed_step_guessed=True,
    )
    got = store.get_job(guessed.id)
    assert got.failed_step == "build web" and got.failed_step_guessed is True

    certain = running(store, tree="b", now=at(20))
    assert store.finish(
        certain.id,
        FAILED,
        now=at(30),
        exit_code=1,
        summary="2 tests failed",
        failed_step="test",
        failed_step_guessed=False,
    )
    assert store.get_job(certain.id).failed_step_guessed is False

    # 플래그를 안 주면 「모름」이다 — 안 준 것을 「확정」으로 읽지 않는다
    silent = running(store, tree="c", now=at(40))
    assert store.finish(silent.id, FAILED, now=at(50), exit_code=1, failed_step="test")
    assert store.get_job(silent.id).failed_step_guessed is None


def test_recent_rows_keep_each_job_s_own_answer(store):
    for tree, step, guess in (("a", "build web", True), ("b", "test", False)):
        j = running(store, tree=tree, now=at(len(tree)))
        store.finish(
            j.id,
            FAILED,
            now=at(50),
            exit_code=1,
            failed_step=step,
            failed_step_guessed=guess,
        )
    recent = store.list_recent(5)
    assert {(j.failed_step, j.failed_step_guessed) for j in recent} == {
        ("build web", True),
        ("test", False),
    }


def test_migration_v9_to_v10_leaves_old_rows_saying_unknown(tmp_path):
    """옛 행은 「추측이었는지 모른다」다 — 0 으로 채워 확정이라고 우기지 않는다."""
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    done = enqueue(s, tree="d")
    assert s.claim(1, at(1)).id == done.id
    s.finish(done.id, FAILED, now=at(2), exit_code=1, failed_step="build web")
    live = enqueue(s, tree="q", now=at(3))
    s.close()
    # v9 데이터베이스를 흉내 낸다: 새 열을 떼고 user_version 을 9 로 되돌린다
    c = sqlite3.connect(path)
    try:
        c.execute("ALTER TABLE jobs DROP COLUMN failed_step_guessed")
        c.execute("PRAGMA user_version=9")
        c.commit()
    finally:
        c.close()
    assert "failed_step_guessed" not in job_columns(path)
    s2 = Store(path)  # 9 → 10 마이그레이션이 여기서 돈다
    try:
        assert s2.user_version() == DB_VERSION and s2.healthy()
        assert "failed_step_guessed" in job_columns(path)
        old = s2.get_job(done.id)
        assert old.failed_step == "build web"  # 옛 이름은 그대로 읽힌다
        assert old.failed_step_guessed is None  # 모름 — True 도 False 도 아니다
        # 마이그레이션 뒤에 닫은 잡은 답을 갖는다
        assert s2.finish(
            live.id, FAILED, now=at(4), exit_code=1, failed_step="a", failed_step_guessed=True
        )
        assert s2.get_job(live.id).failed_step_guessed is True
    finally:
        s2.close()


# ── 공개 문서(`/api/status`) — 키 추가만 ──────────────────────────────────────


def test_progress_json_carries_the_flag_for_a_running_job():
    p = progress_from_markers(
        parallel_markers(), started_at=NOW, finished_at=at(60), now=at(60), exit_code=1
    )
    doc = progress_json(p)
    assert doc["failed_step"] == "build web"
    assert doc["failed_step_guessed"] is True


def test_recent_json_carries_the_flag_and_nothing_else_new(store):
    j = running(store)
    store.finish(
        j.id, FAILED, now=at(10), exit_code=1, failed_step="build web", failed_step_guessed=True
    )
    doc = recent_json(store.get_job(j.id), base_url="http://x")
    assert doc["failed_step"] == "build web" and doc["failed_step_guessed"] is True


def test_an_unknown_guess_stays_null_in_the_document(store):
    """옛 행(마이그레이션 전 완료)은 `null` 로 나간다 — `false` 는 「확정」이라는 거짓말이다."""
    j = running(store)
    store.finish(j.id, FAILED, now=at(10), exit_code=1, failed_step="build web")
    row = store.get_job(j.id)
    object.__setattr__(row, "failed_step_guessed", None)
    assert recent_json(row, base_url="http://x")["failed_step_guessed"] is None


# ── 알림 훅 ───────────────────────────────────────────────────────────────────


def test_notify_hook_learns_the_blame_is_a_guess():
    """훅이 슬랙에 「실패 스텝: build web」을 올리면 잘못된 지목이 그대로 퍼진다."""
    env = notify_env({"failed_step": "build web", "failed_step_guessed": True}, "slack")
    assert env["RCM_FAILED_STEP"] == "build web"
    assert env["RCM_FAILED_STEP_GUESSED"] == "1"

    certain = notify_env({"failed_step": "test", "failed_step_guessed": False}, "slack")
    assert certain["RCM_FAILED_STEP_GUESSED"] == "0"

    unknown = notify_env({"failed_step": "test"}, "slack")
    assert unknown["RCM_FAILED_STEP_GUESSED"] == ""  # 모르는 값은 빈 문자열(값 규칙)


# ── 터미널(`rcm top`) — 추측은 추측으로 그린다 ────────────────────────────────


def _running_row(*, guessed: bool) -> dict:
    """도는 잡의 큐 행 dict — 진행 칸에 실패 스텝이 실려 있다."""
    from test_render_m5 import doc

    row = doc()["pools"][0]["queue"][0]
    assert row["state"] == "running"
    markers = parallel_markers(fail_marker=not guessed)
    prog = progress_from_markers(
        markers, started_at=NOW, finished_at=at(60), now=at(60), exit_code=1
    )
    row["progress"] = progress_json(prog)
    assert row["progress"]["failed_step_guessed"] is guessed
    return row


def test_terminal_marks_a_guessed_step_and_leaves_a_certain_one_alone():
    """큐 행의 진행 칸 계약 — 문서에 추측이 실려 오면 렌더가 추측으로 그린다.

    오늘 도는 잡은 종료 코드가 없어 폴백을 안 타므로 큐 행에는 추측이 안 실린다. 그래도
    `progress` 는 `failed_step_guessed` 를 실어 보내는 모양이라, 렌더가 그 값을 무시하지
    않는다는 것을 여기서 못 박는다. 사람이 실제로 보는 자리는 아래 최근 완료 줄이다.
    """
    from jobfactory import NOW as FIXTURE_NOW

    NOW = FIXTURE_NOW  # noqa: N806 — 픽스처 문서의 시계로 그린다
    guessed = "\n".join(render_queue_row(_running_row(guessed=True), UTC, NOW))
    certain = "\n".join(render_queue_row(_running_row(guessed=False), UTC, NOW))
    assert "✘ build web, guessed" in guessed  # 이름은 대되 짐작이라고 쓴다
    assert "✘ test" in certain and "guessed" not in certain  # 확정된 표시는 지금 그대로


def test_terminal_recent_line_says_when_the_step_is_a_guess(store):
    from test_status_schema import model

    def line_for(guess: bool | None) -> str:
        j = running(store, tree=f"t{guess}", now=at(len(str(guess))))
        store.finish(
            j.id,
            FAILED,
            now=at(50),
            exit_code=1,
            summary="exit 1",
            failed_step="build web",
            failed_step_guessed=guess,
        )
        doc = status_json(model(recent=[store.get_job(j.id)]))
        out = render(doc, tz=UTC)
        return next(ln for ln in out.splitlines() if "build web" in ln)

    assert "guessed" in line_for(True)
    assert "guessed" not in line_for(False)
    # 모르는 옛 잡은 아무 말도 덧붙이지 않는다 — 「확정」이라고도 「추측」이라고도 안 한다
    assert "guessed" not in line_for(None)


# ── 마이그레이션 번호를 옮겼을 때 ─────────────────────────────────────────────


def test_adding_a_column_that_is_already_there_is_not_fatal(tmp_path):
    """이 열은 처음에 v9 였다가 `dev` 가 v9 를 먼저 가져가서 v10 으로 밀렸다.

    그 사이 옛 빌드로 한 번이라도 연 데이터베이스는 `user_version = 9` 인데 **열은 이미 있다**.
    그대로 두면 v10 의 `ADD COLUMN` 이 「duplicate column name」으로 죽고, 버전이 안 올라가니
    **다음에도 똑같이 죽는다** — 서버가 영영 안 뜬다. 열을 더하는 마이그레이션은 이미 있으면
    건너뛴다(더하려는 것이 이미 있으니 할 일이 없다).
    """
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    j = enqueue(s, tree="d")
    assert s.claim(1, at(1)).id == j.id
    s.finish(
        j.id, FAILED, now=at(2), exit_code=1, failed_step="build web", failed_step_guessed=True
    )
    s.close()
    # 리베이스 전 빌드가 남긴 모양: 열은 있는데 버전은 9
    c = sqlite3.connect(path)
    try:
        c.execute("PRAGMA user_version=9")
        c.commit()
    finally:
        c.close()
    assert "failed_step_guessed" in job_columns(path)

    s2 = Store(path)  # 여기서 죽으면 안 된다
    try:
        assert s2.user_version() == DB_VERSION and s2.healthy()
        got = s2.get_job(j.id)
        assert got.failed_step == "build web" and got.failed_step_guessed is True  # 값도 그대로
        # 다시 열어도 조용하다 — 버전이 올라갔으니 두 번째부터는 마이그레이션 자체를 안 탄다
        s3 = Store(path)
        assert s3.user_version() == DB_VERSION
        s3.close()
    finally:
        s2.close()


def test_a_blame_free_finish_leaves_the_flag_unknown(store):
    """지목할 스텝이 없는 종료(취소·유실·업로드 포기)는 플래그도 「모름」이다.

    기본값이 `False` 면 「확정」이라는 **적극적 주장**을 아무 근거 없이 쓰게 된다 — 훅이
    `RCM_FAILED_STEP=""` 와 `RCM_FAILED_STEP_GUESSED="0"` 을 같이 받는 꼴이다.
    """
    j = running(store)
    assert store.finish(j.id, CANCELLED, now=at(10), cancelled_by="alice-laptop")
    got = store.get_job(j.id)
    assert got.failed_step is None and got.failed_step_guessed is None
    assert (
        notify_env({"failed_step": None, "failed_step_guessed": None}, "x")[
            "RCM_FAILED_STEP_GUESSED"
        ]
        == ""
    )
