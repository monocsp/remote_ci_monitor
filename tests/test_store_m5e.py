"""저장소(M5e) — 스키마 7 · `join_count` · 번들 예약과 회계 · 발행 · ack 규칙 · 만료 ·
재시작 화해 · 메타데이터 삭제의 새 조건.

명세는 docs/m5e-workplan.md §7(삭제 규칙) · §8(상한·회계·M3 경계) · §9(스키마 7) ·
§18(잠근 API 표면). **구현 전이라 빨간 것이 정상이다.**

- 시각은 고정 `NOW` 기준으로 넘긴다 — sleep 도 벽시계도 없다.
- 파일 배치는 진짜와 같게 둔다: `Store(<data_dir>/rcm.sqlite3)` 이므로 번들은
  `<data_dir>/artifacts/<job_id>/` 고 스테이징은 `<data_dir>/artifacts/.staging/` 다(§3).
  `reconcile_bundles_on_start` 은 인자가 없으므로(§18) DB 파일 옆에서 이 배치를 찾는다.
- 아직 없는 모듈(`core/artifacts.py` · `collect.py`)은 **테스트 안에서** 늦게 부른다 —
  파일 하나가 통째로 수집 오류가 되는 대신 테스트마다 따로 빨개진다.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import _MIGRATIONS, _SCHEMA_V1, DB_VERSION, Store

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
TTL_HOURS = 24
ALICE = Requester(name="alice-laptop", label="alice@laptop")

#: §9 가 못 박은 `job_artifacts` 열. 이름이 바뀌면 청소기·회계·재생 판정이 전부 어긋난다.
BUNDLE_COLUMNS = {
    "job_id",
    "state",
    "policy_json",
    "manifest_json",
    "bundle_sha256",
    "file_count",
    "total_bytes",
    "bundle_bytes",
    "reserved_bytes",
    "skipped_count",
    "collected_at",
    "ready_at",
    "expires_at",
    "acked_at",
    "purged_at",
    "reason_code",
    "reason_args",
    "detail_json",
}


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


# ── 아직 없는 모듈 (§18) ──────────────────────────────────────────────────────


def artifacts_core() -> Any:
    """`remote_ci_monitor.core.artifacts` — 순수 규칙(§18). 구현 전에는 여기서 빨개진다."""
    import remote_ci_monitor.core.artifacts as mod

    return mod


def policy(**kw: Any) -> Any:
    """잡마다 어는 정책(§9). 글롭 하나는 언제나 있다 — 없으면 기능이 꺼진 잡이다."""
    fields: dict[str, Any] = {"globs": ("out/*.png",), "max_bytes": 1024, "max_files": 10}
    fields.update(kw)
    return artifacts_core().ArtifactPolicy(**fields)


def bundle_file(path: str, data: bytes, mode: int = 0o644) -> Any:
    core = artifacts_core()
    return core.BundleFile(
        path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=mode
    )


def collect_result(**kw: Any) -> Any:
    """`collect.CollectResult`(§18). `state` 는 반드시 준다."""
    import remote_ci_monitor.collect as mod

    return mod.CollectResult(**kw)


# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def store(data_dir: Path):
    s = Store(data_dir / "rcm.sqlite3")
    yield s
    s.close()


def enqueue(
    store: Store,
    *,
    preset: str = "goldens",
    tree: str = "9f8e",
    now: datetime = NOW,
    state: str = QUEUED,
    requester: Requester = ALICE,
):
    src = Source(mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash=tree)
    return store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=None,
        source=src,
        requester=requester,
        timeout_seconds=1200,
        join_key=join_key(preset, {}, tree),
        now=now,
        state=state,
    )


def finished_job(store: Store, *, state: str = SUCCEEDED, finished: datetime, tree: str = "9f8e"):
    """enqueue → claim → finish. 다른 queued 잡이 없을 때 부른다(FIFO claim)."""
    j = enqueue(store, tree=tree)
    claimed = store.claim(1, at(1))
    assert claimed is not None and claimed.id == j.id
    assert store.finish(j.id, state, now=finished, exit_code=0 if state == SUCCEEDED else 1)
    return store.get_job(j.id)


def raw(store: Store) -> sqlite3.Connection:
    """DB 를 직접 읽는다 — `join_count` 는 공개 모델에 없는 내부 열이다(§7)."""
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    return conn


def join_count(store: Store, job_id: int) -> int:
    with raw(store) as conn:
        row = conn.execute("SELECT join_count FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row is not None, job_id
    return int(row["join_count"])


def bundle_row_count(store: Store, job_id: int) -> int:
    with raw(store) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM job_artifacts WHERE job_id=?", (job_id,)
        ).fetchone()
    return int(row["n"])


def bundle_dir(store: Store, job_id: int) -> Path:
    return store.path.parent / "artifacts" / str(job_id)


def install_files(store: Store, job_id: int, payload: bytes) -> str:
    """`artifacts/<id>/bundle.tar` + `manifest.json` 을 실제로 놓고 tar 해시를 돌려준다."""
    d = bundle_dir(store, job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.tar").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (d / "manifest.json").write_text(f'{{"bundle_sha256": "{digest}", "files": []}}')
    return digest


def ready_bundle(
    store: Store,
    job_id: int,
    *,
    payload: bytes = b"tar bytes",
    total_bytes: int | None = None,
    ready_at: datetime = NOW,
    ttl_hours: int = TTL_HOURS,
    files: tuple[Any, ...] | None = None,
) -> str:
    """진짜 순서대로 — `start_collect` 로 행을 열고 파일을 놓고 `publish_bundle` 로 발행한다.
    `bundle_sha256` 을 돌려준다."""
    core = artifacts_core()
    digest = install_files(store, job_id, payload)
    entries = files if files is not None else (bundle_file("out/a.png", b"png"),)
    store.start_collect(job_id, policy(), ready_at)
    result = collect_result(
        state=core.READY,
        files=entries,
        total_bytes=sum(f.size for f in entries) if total_bytes is None else total_bytes,
        bundle_bytes=len(payload),
        skipped_count=0,
        bundle_sha256=digest,
        bundle_path=bundle_dir(store, job_id) / "bundle.tar",
    )
    store.publish_bundle(
        job_id, result, now=ready_at, expires_at=core.expires_at(ready_at, ttl_hours)
    )
    return digest


# ══ 스키마 7 (§9) ════════════════════════════════════════════════════════════


def test_fresh_database_is_schema_7_with_join_count_and_job_artifacts(store, data_dir):
    """새 DB 는 마이그레이션을 건너뛰고 `_SCHEMA_V1` 만 실행한다(`store.py:294`) — 마이그레이션만
    고치면 새 DB 에 표가 없다. 그래서 6→7 만 보는 검사로는 이 버그를 못 잡는다."""
    assert DB_VERSION >= 7  # M5e 는 7, M5f 가 claim 인덱스로 8 을 더한다
    assert store.user_version() == DB_VERSION
    with raw(store) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION
        job_cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(jobs)")}
        bundle_cols = {r["name"] for r in conn.execute("PRAGMA table_info(job_artifacts)")}
        indexes = {r["name"] for r in conn.execute("PRAGMA index_list(job_artifacts)")}
    assert "join_count" in job_cols
    assert int(job_cols["join_count"]["notnull"]) == 1
    assert str(job_cols["join_count"]["dflt_value"]) == "0"
    assert BUNDLE_COLUMNS <= bundle_cols, sorted(BUNDLE_COLUMNS - bundle_cols)
    assert "job_artifacts_expiry" in indexes
    # 문면으로도 잠근다 — `_MIGRATIONS[7]` 만 고치면 새 DB 가 조용히 비어 있다
    assert "join_count" in _SCHEMA_V1 and "job_artifacts" in _SCHEMA_V1


def test_migration_7_is_one_statement_per_tuple_entry():
    """`_MIGRATIONS` 는 문장 하나씩의 튜플이다(`store.py:173`) — executescript 가 아니다."""
    assert 7 in _MIGRATIONS
    stmts = _MIGRATIONS[7]
    assert isinstance(stmts, tuple) and stmts
    assert all(isinstance(s, str) for s in stmts)
    blob = " ".join(stmts)
    assert "join_count" in blob and "job_artifacts" in blob


def test_migration_from_6_to_7_keeps_rows_and_defaults_join_count_to_zero(data_dir):
    """6 에서 만든 DB 를 열면 기존 행이 살아남고 새 열은 0 · 옛 잡은 번들 행이 없다(= unknown)."""
    path = data_dir / "rcm.sqlite3"
    s = Store(path)
    kept = enqueue(s)
    joined = enqueue(s, tree="other")
    assert s.add_joiner(joined.id, "bob-desk", "bob@desk", at(1)) is True
    s.close()
    # v6 데이터베이스를 흉내 낸다: M5e 가 더한 것만 떼고 user_version 을 6 으로
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP INDEX IF EXISTS job_artifacts_expiry")
        conn.execute("DROP TABLE IF EXISTS job_artifacts")
        conn.execute("ALTER TABLE jobs DROP COLUMN join_count")
        conn.execute("PRAGMA user_version=6")
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        assert "join_count" not in cols
    finally:
        conn.close()
    s2 = Store(path)  # 6 → 7 이 여기서 돈다
    try:
        assert s2.user_version() == DB_VERSION
        got = s2.get_job(kept.id)
        assert got is not None and got.state == QUEUED and got.created_at == NOW
        assert [j.name for j in s2.get_job(joined.id).joiners] == ["bob-desk"]
        # `DEFAULT 0` 은 과거의 합류를 복원하지 못한다 — 옛 잡은 아무도 안 붙은 것으로 읽힌다(§9)
        assert join_count(s2, kept.id) == 0
        assert join_count(s2, joined.id) == 0
        # M5e 이전 잡은 번들 행이 없다 → 공개 상태는 `unknown` 이다(§3, 서버 쪽에서 잠근다)
        assert s2.get_bundle(kept.id) is None
        assert s2.bundles_due(NOW + 400 * HOUR) == []
    finally:
        s2.close()


def test_reopening_twice_changes_nothing(data_dir):
    path = data_dir / "rcm.sqlite3"
    first = Store(path)
    job = enqueue(first)
    sha = ready_bundle(first, job.id)
    first.close()
    second = Store(path)
    assert second.user_version() == DB_VERSION
    assert second.get_bundle(job.id)["bundle_sha256"] == sha
    second.close()
    third = Store(path)
    try:
        assert third.user_version() == DB_VERSION
        assert third.get_job(job.id).key == "goldens"
        assert third.get_bundle(job.id)["bundle_sha256"] == sha
    finally:
        third.close()


def test_m3_artifacts_purged_at_and_the_bundle_purge_are_different_things(store):
    """`jobs.artifacts_purged_at`(M3: 로그·워크스페이스) 과 `job_artifacts.purged_at`(M5e) 은
    뜻이 다르다(§1). 하나를 찍어도 다른 하나는 움직이지 않는다."""
    job = finished_job(store, finished=at(10))
    ready_bundle(store, job.id)
    store.mark_artifacts_purged([job.id], at(20))
    assert store.get_job(job.id).artifacts_purged_at == at(20)
    assert store.get_bundle(job.id)["purged_at"] is None
    assert store.get_bundle(job.id)["state"] == artifacts_core().READY


# ══ join_count (§7) ══════════════════════════════════════════════════════════


def test_join_count_starts_at_zero_and_add_joiner_alone_does_not_bump_it(store):
    """`add_joiner` 에는 상태 검사도 카운트도 없다(`store.py:782`) — 불변식은 `join_or_bump`
    경로에 걸린다. 픽스처가 합류자만 꽂으면 `join_count == 0` 인 잡을 만들 수 있다(§7)."""
    job = enqueue(store)
    assert join_count(store, job.id) == 0
    assert store.add_joiner(job.id, "bob-desk", "bob@desk", at(1)) is True
    assert join_count(store, job.id) == 0
    assert [j.name for j in store.get_job(job.id).joiners] == ["bob-desk"]


def test_a_different_token_joining_bumps_the_count_and_adds_the_row(store):
    job = enqueue(store)
    key = join_key("goldens", {}, "9f8e")
    got = store.join_or_bump(key, "bob-desk", "bob@desk", 0, at(1))
    assert got is not None and got.id == job.id
    assert [j.name for j in got.joiners] == ["bob-desk"]
    assert join_count(store, job.id) == 1


def test_the_requester_resubmitting_bumps_the_count_without_a_joiner_row(store):
    """요청자 본인은 합류자 줄이 아예 안 생긴다(`store.py:619`) — 그래도 세어야 한다(§7)."""
    job = enqueue(store)
    key = join_key("goldens", {}, "9f8e")
    got = store.join_or_bump(key, ALICE.name, ALICE.label, 0, at(1))
    assert got is not None and got.id == job.id
    assert got.joiners == ()
    assert join_count(store, job.id) == 1
    store.join_or_bump(key, ALICE.name, ALICE.label, 0, at(2))
    assert store.get_job(job.id).joiners == ()
    assert join_count(store, job.id) == 2


def test_the_same_joiner_resubmitting_bumps_the_count_without_a_second_row(store):
    """`INSERT OR IGNORE` 는 줄을 안 늘린다 — 카운트는 는다(§7)."""
    job = enqueue(store)
    key = join_key("goldens", {}, "9f8e")
    for i in range(3):
        assert store.join_or_bump(key, "bob-desk", "bob@desk", 0, at(i + 1)) is not None
    assert [j.name for j in store.get_job(job.id).joiners] == ["bob-desk"]
    assert join_count(store, job.id) == 3


def test_a_job_nobody_joined_keeps_join_count_zero(store):
    job = enqueue(store)
    assert (
        store.join_or_bump(join_key("goldens", {}, "different-tree"), "bob-desk", "b", 0, at(1))
        is None
    )
    assert join_count(store, job.id) == 0


def test_join_or_bump_on_a_terminal_job_does_not_bump(store):
    """합류는 `ACTIVE_STATES` 에서만 일어난다 — 끝난 잡은 후보가 아니다(§2)."""
    job = finished_job(store, finished=at(10))
    assert store.join_or_bump(join_key("goldens", {}, "9f8e"), "bob-desk", "b", 0, at(11)) is None
    assert join_count(store, job.id) == 0


def test_a_joiner_leaving_never_gives_the_count_back(store):
    """되돌리면 남은 사람이 못 받는다(§7). 이탈은 종료 뒤에도 일어난다(`server.py:1304`)."""
    job = enqueue(store)
    key = join_key("goldens", {}, "9f8e")
    store.join_or_bump(key, "bob-desk", "bob@desk", 0, at(1))
    assert join_count(store, job.id) == 1
    assert store.remove_joiner(job.id, "bob-desk") is True
    assert store.get_job(job.id).joiners == ()
    assert join_count(store, job.id) == 1


def test_concurrent_joins_are_all_counted_exactly_once(store):
    """판정·기록·카운트가 한 `BEGIN IMMEDIATE` 안이면 갱신이 사라지지 않는다(`store.py:606`)."""
    job = enqueue(store)
    key = join_key("goldens", {}, "9f8e")
    names = [f"joiner-{i:02d}" for i in range(8)]
    barrier = threading.Barrier(len(names))
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            barrier.wait(timeout=10)
            store.join_or_bump(key, name, f"{name}@host", 0, at(1))
        except BaseException as e:  # noqa: BLE001 — 스레드 예외를 삼키지 않는다
            errors.append(e)
        finally:
            store.close()

    threads = [threading.Thread(target=run, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert errors == []
    assert sorted(j.name for j in store.get_job(job.id).joiners) == names
    assert join_count(store, job.id) == len(names)


# ══ 예약 · 회계 (§8) ═════════════════════════════════════════════════════════


def test_reserve_and_release_move_the_totals(store):
    job = enqueue(store)
    assert store.bundle_storage_totals() == (0, 0)
    assert store.reserve_bundle_bytes(job.id, 100, 1000, NOW) is True
    assert store.bundle_storage_totals() == (0, 100)
    store.release_bundle_bytes(job.id)
    assert store.bundle_storage_totals() == (0, 0)
    store.release_bundle_bytes(job.id)  # 두 번째 반납은 조용하다
    assert store.bundle_storage_totals() == (0, 0)


def test_a_refused_reservation_changes_nothing(store):
    """전체 상한을 넘으면 새 묶음을 받지 않는다 — 남의 묶음을 쫓아내지 않는다(§8)."""
    first = enqueue(store)
    second = enqueue(store, tree="second")
    assert store.reserve_bundle_bytes(first.id, 900, 1000, NOW) is True
    assert store.reserve_bundle_bytes(second.id, 200, 1000, NOW) is False
    assert store.bundle_storage_totals() == (0, 900)
    assert (
        bundle_row_count(store, second.id) == 0
        or store.get_bundle(second.id)["reserved_bytes"] == 0
    )
    # 잡당 예약은 하나다 — 같은 잡이 다시 잡으면 더하지 않고 바꾼다(두 번 세면 회계가 무너진다)
    assert store.reserve_bundle_bytes(first.id, 900, 1000, NOW) is True
    assert store.bundle_storage_totals() == (0, 900)


def test_concurrent_reservations_never_oversubscribe_the_limit(store):
    """8명이 같이 400 씩 집어도 상한 1000 안에서는 둘만 성공한다."""
    jobs = [enqueue(store, tree=f"t{i}").id for i in range(8)]
    barrier = threading.Barrier(len(jobs))
    granted: list[int] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def run(job_id: int) -> None:
        try:
            barrier.wait(timeout=10)
            if store.reserve_bundle_bytes(job_id, 400, 1000, NOW):
                with lock:
                    granted.append(job_id)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
        finally:
            store.close()

    threads = [threading.Thread(target=run, args=(j,)) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert errors == []
    assert len(granted) == 2, granted
    stored, reserved = store.bundle_storage_totals()
    assert stored == 0 and reserved == 800 and reserved <= 1000


def test_publishing_turns_the_reservation_into_measured_stored_bytes(store):
    """상한은 받기 전에 예약으로 걸고 발행 때 실측으로 바꾼다(§8)."""
    job = enqueue(store)
    assert store.reserve_bundle_bytes(job.id, 4096, 10_000, NOW) is True
    assert store.bundle_storage_totals() == (0, 4096)
    payload = b"x" * 2048
    ready_bundle(store, job.id, payload=payload, total_bytes=2000)
    stored, reserved = store.bundle_storage_totals()
    assert stored == len(payload) and reserved == 0


def test_stored_and_reserved_are_counted_separately(store):
    published = enqueue(store)
    pending = enqueue(store, tree="pending")
    ready_bundle(store, published.id, payload=b"y" * 300)
    assert store.reserve_bundle_bytes(pending.id, 700, 10_000, NOW) is True
    assert store.bundle_storage_totals() == (300, 700)


# ══ 발행 · 실패 · 읽기 (§18) ══════════════════════════════════════════════════


def test_publish_bundle_records_the_hash_counts_and_expiry(store):
    core = artifacts_core()
    job = enqueue(store)
    files = (bundle_file("out/a.png", b"aa"), bundle_file("out/b.png", b"bbb", mode=0o755))
    sha = ready_bundle(store, job.id, payload=b"tar!", total_bytes=5, files=files)
    b = store.get_bundle(job.id)
    assert b["state"] == core.READY
    assert b["bundle_sha256"] == sha
    assert b["file_count"] == 2 and b["total_bytes"] == 5 and b["bundle_bytes"] == 4
    assert b["skipped_count"] == 0
    assert b["ready_at"] == NOW
    assert b["expires_at"] == NOW + TTL_HOURS * HOUR  # 발행 시각부터 잰다(§8)
    assert b["acked_at"] is None and b["purged_at"] is None
    assert b["reason_code"] is None and b["reason_args"] is None


def test_reading_a_bundle_does_not_extend_its_expiry(store):
    """TTL 은 `ready` 가 된 시각부터다. 읽어도 연장되지 않는다(§8)."""
    job = enqueue(store)
    ready_bundle(store, job.id)
    first = store.get_bundle(job.id)["expires_at"]
    for _ in range(3):
        assert store.get_bundle(job.id)["expires_at"] == first


def test_set_bundle_failed_records_the_state_and_reason_without_a_hash(store):
    core = artifacts_core()
    job = enqueue(store)
    store.start_collect(job.id, policy(), NOW)
    assert store.get_bundle(job.id)["state"] == core.COLLECTING
    store.set_bundle_failed(
        job.id, core.DROPPED, "over_bytes", {"limit": 1024, "seen": 2048}, at(5)
    )
    b = store.get_bundle(job.id)
    assert b["state"] == core.DROPPED
    assert b["reason_code"] == "over_bytes"
    assert b["reason_args"] == {"limit": 1024, "seen": 2048}
    assert b["bundle_sha256"] is None and b["ready_at"] is None
    # 잡의 요약은 건드리지 않는다 — 「테스트가 깨졌다」와 「산출물을 못 올렸다」는 다른 사실이다(§3)
    assert store.get_job(job.id).summary is None


def test_reason_args_never_carry_a_path(store):
    """거부된 파일 이름은 그 자체로 남의 트리 구조다 — 수치만 공개한다(§3)."""
    core = artifacts_core()
    job = enqueue(store)
    store.start_collect(job.id, policy(), NOW)
    store.set_bundle_failed(job.id, core.DROPPED, "over_files", {"limit": 10, "seen": 64}, at(5))
    args = store.get_bundle(job.id)["reason_args"] or {}
    assert all("/" not in str(v) for v in args.values()), args


def test_get_bundle_returns_none_for_a_job_that_never_had_one(store):
    job = enqueue(store)
    assert store.get_bundle(job.id) is None
    assert store.get_bundle(999_999) is None


# ══ ack (§7) ═════════════════════════════════════════════════════════════════


def test_ack_of_an_unjoined_job_purges_immediately(store):
    core = artifacts_core()
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    assert join_count(store, job.id) == 0
    decision = store.ack_bundle(job.id, sha, owner=True, now=at(60))
    assert (decision.status, decision.purge, decision.record) == (200, True, True)
    assert decision.reason_code == "acked"
    b = store.get_bundle(job.id)
    assert b["acked_at"] == at(60)
    # 「지웠다」는 물리적으로 지운 뒤에 말한다 — 여기서는 삭제 자격만 준다(§7)
    assert b["state"] in (core.READY, core.PURGED)


def test_ack_of_a_joined_job_records_but_does_not_purge(store):
    """한 번이라도 합류가 있었으면 확인이 와도 TTL 까지 유지한다(결정 40)."""
    job = enqueue(store)
    store.join_or_bump(join_key("goldens", {}, "9f8e"), "bob-desk", "bob@desk", 0, at(1))
    sha = ready_bundle(store, job.id)
    decision = store.ack_bundle(job.id, sha, owner=True, now=at(60))
    assert (decision.status, decision.purge, decision.record) == (200, False, True)
    b = store.get_bundle(job.id)
    assert b["acked_at"] == at(60)
    assert b["state"] == artifacts_core().READY
    assert b["expires_at"] == NOW + TTL_HOURS * HOUR


def test_an_admin_who_is_neither_requester_nor_joiner_acks_as_a_no_op(store):
    """`owner=False` 는 `acked_at` 을 쓰지 않고 아무것도 지우지 않는다(§7)."""
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    decision = store.ack_bundle(job.id, sha, owner=False, now=at(60))
    assert (decision.status, decision.purge, decision.record) == (200, False, False)
    b = store.get_bundle(job.id)
    assert b["acked_at"] is None
    assert b["state"] == artifacts_core().READY


def test_ack_with_a_different_hash_is_409_and_changes_nothing(store):
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    decision = store.ack_bundle(job.id, "ff" * 32, owner=True, now=at(60))
    assert decision.status == 409 and decision.purge is False and decision.record is False
    b = store.get_bundle(job.id)
    assert b["acked_at"] is None and b["state"] == artifacts_core().READY
    assert b["bundle_sha256"] == sha


def test_replaying_an_ack_on_a_purged_bundle_with_the_same_hash_is_200(store):
    """응답을 잃은 클라이언트가 409 를 받지 않게 해시와 `acked_at` 을 묘비로 남긴다(§7)."""
    core = artifacts_core()
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    first = store.ack_bundle(job.id, sha, owner=True, now=at(60))
    assert first.purge is True
    assert store.mark_bundles_purged([job.id], core.PURGED, at(61)) == 1
    replay = store.ack_bundle(job.id, sha, owner=True, now=at(120))
    assert replay.status == 200
    assert replay.purge is False  # 이미 없다 — 두 번 지우지 않는다
    b = store.get_bundle(job.id)
    assert b["state"] == core.PURGED
    assert b["bundle_sha256"] == sha  # 묘비는 남는다
    assert b["acked_at"] == at(60)  # 첫 확인 시각을 덮어쓰지 않는다


def test_replaying_a_purged_bundle_with_a_different_hash_is_409(store):
    core = artifacts_core()
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    store.ack_bundle(job.id, sha, owner=True, now=at(60))
    store.mark_bundles_purged([job.id], core.PURGED, at(61))
    assert store.ack_bundle(job.id, "ab" * 32, owner=True, now=at(120)).status == 409


def test_ack_after_the_expiry_moment_is_410(store):
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    late = NOW + TTL_HOURS * HOUR + timedelta(seconds=1)
    decision = store.ack_bundle(job.id, sha, owner=True, now=late)
    assert decision.status == 410
    assert decision.purge is False and decision.record is False
    assert store.get_bundle(job.id)["acked_at"] is None


def test_ack_of_an_expired_bundle_is_410(store):
    core = artifacts_core()
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    store.mark_bundles_purged([job.id], core.EXPIRED, NOW + TTL_HOURS * HOUR)
    decision = store.ack_bundle(job.id, sha, owner=True, now=NOW + 25 * HOUR)
    assert decision.status == 410 and decision.purge is False


def test_ack_of_a_bundle_that_is_neither_ready_nor_purged_is_409(store):
    core = artifacts_core()
    for state, tree in ((core.COLLECTING, "a"), (core.FAILED, "b"), (core.EMPTY, "c")):
        job = enqueue(store, tree=tree)
        store.start_collect(job.id, policy(), NOW)
        if state != core.COLLECTING:
            store.set_bundle_failed(job.id, state, "collect_failed", None, at(5))
        decision = store.ack_bundle(job.id, "cd" * 32, owner=True, now=at(60))
        assert decision.status == 409, state
        assert decision.purge is False and decision.record is False


def test_ack_of_a_job_without_a_bundle_is_409(store):
    job = enqueue(store)
    assert store.ack_bundle(job.id, "ef" * 32, owner=True, now=at(60)).status == 409


def test_two_concurrent_final_acks_schedule_exactly_one_deletion(store):
    """둘 다 200 이지만 지우라는 답은 하나뿐이다 — 두 번 지우면 회계가 두 번 줄어든다."""
    job = enqueue(store)
    sha = ready_bundle(store, job.id)
    barrier = threading.Barrier(2)
    results: list[Any] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait(timeout=10)
            decision = store.ack_bundle(job.id, sha, owner=True, now=at(60))
            with lock:
                results.append(decision)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
        finally:
            store.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert errors == []
    assert [d.status for d in results] == [200, 200]
    assert sum(1 for d in results if d.purge) == 1, [d.purge for d in results]


# ══ 만료 (§8) ════════════════════════════════════════════════════════════════


def test_bundles_due_returns_only_rows_whose_expiry_has_passed(store):
    core = artifacts_core()
    early = enqueue(store, tree="early")
    late = enqueue(store, tree="late")
    ready_bundle(store, early.id, ready_at=NOW - 25 * HOUR, payload=b"e" * 11)
    ready_bundle(store, late.id, ready_at=NOW, payload=b"l" * 22)
    due = store.bundles_due(NOW)
    assert [b.job_id for b in due] == [early.id]
    assert due[0].state == core.READY and due[0].bytes == 11
    assert due[0].expires_at == NOW - HOUR
    assert [b.job_id for b in store.bundles_due(NOW + TTL_HOURS * HOUR)] == [early.id, late.id]


def test_the_expiry_boundary_is_inclusive(store):
    job = enqueue(store)
    ready_bundle(store, job.id)
    expiry = NOW + TTL_HOURS * HOUR
    assert store.bundles_due(expiry - timedelta(seconds=1)) == []
    assert [b.job_id for b in store.bundles_due(expiry)] == [job.id]


def test_a_row_with_no_expiry_is_never_due(store):
    """만료 없는 행은 절대 대상이 아니다(§18 `bundles_to_expire`)."""
    core = artifacts_core()
    job = enqueue(store)
    store.start_collect(job.id, policy(), NOW)
    store.set_bundle_failed(job.id, core.FAILED, "collect_failed", None, at(5))
    assert store.get_bundle(job.id)["expires_at"] is None
    assert store.bundles_due(NOW + 10_000 * HOUR) == []


def test_bundles_due_skips_rows_that_are_already_gone(store):
    core = artifacts_core()
    job = enqueue(store)
    ready_bundle(store, job.id, ready_at=NOW - 25 * HOUR)
    assert [b.job_id for b in store.bundles_due(NOW)] == [job.id]
    store.mark_bundles_purged([job.id], core.EXPIRED, NOW)
    assert store.bundles_due(NOW) == []


def test_bundles_due_honours_the_limit_and_orders_by_job_id(store):
    ids = []
    for i in range(4):
        job = enqueue(store, tree=f"t{i}")
        ready_bundle(store, job.id, ready_at=NOW - (30 - i) * HOUR)
        ids.append(job.id)
    assert [b.job_id for b in store.bundles_due(NOW)] == sorted(ids)
    assert [b.job_id for b in store.bundles_due(NOW, limit=2)] == sorted(ids)[:2]


def test_mark_bundles_purged_sets_the_state_and_time_and_releases_the_bytes(store):
    core = artifacts_core()
    job = enqueue(store)
    ready_bundle(store, job.id, payload=b"z" * 64, ready_at=NOW - 25 * HOUR)
    assert store.bundle_storage_totals() == (64, 0)
    assert store.mark_bundles_purged([job.id], core.EXPIRED, NOW) == 1
    b = store.get_bundle(job.id)
    assert b["state"] == core.EXPIRED and b["purged_at"] == NOW
    assert store.bundle_storage_totals() == (0, 0)
    assert store.mark_bundles_purged([job.id], core.EXPIRED, at(1)) == 0  # idempotent
    assert store.get_bundle(job.id)["purged_at"] == NOW
    assert store.mark_bundles_purged([], core.EXPIRED, NOW) == 0


# ══ 재시작 화해 (§9) ═════════════════════════════════════════════════════════


def test_reconcile_removes_orphan_staging_directories(store):
    staging = store.path.parent / "artifacts" / ".staging" / "abc123"
    staging.mkdir(parents=True)
    (staging / "bundle.tar.part").write_bytes(b"half a tar")
    installed = enqueue(store)
    ready_bundle(store, installed.id)
    store.reconcile_bundles_on_start(NOW)
    assert not staging.exists()
    assert not (store.path.parent / "artifacts" / ".staging").exists() or not list(
        (store.path.parent / "artifacts" / ".staging").iterdir()
    )
    assert (bundle_dir(store, installed.id) / "bundle.tar").exists()  # 설치된 것은 그대로


def test_reconcile_closes_collecting_and_uploading_rows_and_releases_reservations(store):
    core = artifacts_core()
    collecting = enqueue(store, tree="c")
    uploading = enqueue(store, tree="u")
    for job in (collecting, uploading):
        store.start_collect(job.id, policy(), NOW)
        assert store.reserve_bundle_bytes(job.id, 500, 10_000, NOW) is True
    with raw(store) as conn:  # 업로드 중 상태는 워커가 만든다 — 여기서는 손으로 놓는다
        conn.execute(
            "UPDATE job_artifacts SET state=? WHERE job_id=?", (core.UPLOADING, uploading.id)
        )
        conn.commit()
    interrupted, unavailable = store.reconcile_bundles_on_start(at(5))
    assert sorted(interrupted) == sorted([collecting.id, uploading.id])
    assert unavailable == []
    for job in (collecting, uploading):
        b = store.get_bundle(job.id)
        assert b["state"] == core.FAILED
        assert b["reason_code"] == "interrupted"
        assert b["reserved_bytes"] == 0
    assert store.bundle_storage_totals() == (0, 0)


def test_reconcile_marks_ready_rows_with_missing_files_unavailable(store):
    """메타는 있는데 파일이 없다 — 조용히 `empty` 로 만들지 않는다(§3)."""
    core = artifacts_core()
    gone = enqueue(store, tree="gone")
    kept = enqueue(store, tree="kept")
    ready_bundle(store, gone.id)
    ready_bundle(store, kept.id)
    (bundle_dir(store, gone.id) / "bundle.tar").unlink()
    interrupted, unavailable = store.reconcile_bundles_on_start(at(5))
    assert interrupted == []
    assert unavailable == [gone.id]
    assert store.get_bundle(gone.id)["state"] == core.UNAVAILABLE
    assert store.get_bundle(kept.id)["state"] == core.READY


def test_reconcile_removes_an_installed_directory_that_has_no_row(store):
    """커밋 전에 죽으면 행 없는 `artifacts/<id>/` 가 남는다 — 지운다(§9 ④)."""
    orphan = store.path.parent / "artifacts" / "4242"
    orphan.mkdir(parents=True)
    (orphan / "bundle.tar").write_bytes(b"nobody owns me")
    kept = enqueue(store)
    ready_bundle(store, kept.id)
    store.reconcile_bundles_on_start(NOW)
    assert not orphan.exists()
    assert (bundle_dir(store, kept.id) / "bundle.tar").exists()


def test_a_bundle_never_turns_a_lost_job_into_a_success(store):
    """영수증이 먼저 도착하고 워커가 죽었다 — 묶음이 있어도 잡은 `lost` 그대로다(§5 · §9)."""
    core = artifacts_core()
    job = enqueue(store)
    assert store.claim(1, at(1)).id == job.id
    ready_bundle(store, job.id, ready_at=at(2))
    assert store.finish(job.id, LOST, now=at(3), summary="worker stopped while running")
    store.reconcile_bundles_on_start(at(10))
    assert store.get_job(job.id).state == LOST
    assert store.get_job(job.id).exit_code is None
    assert store.get_bundle(job.id)["state"] == core.READY


def test_a_receipt_without_a_finish_already_has_an_expiry_so_the_sweep_takes_it(store):
    """만료 없는 행을 만들지 않는다(§9 ⑤) — 영수증 시각 + TTL 이 이미 박혀 있다."""
    job = enqueue(store)
    assert store.claim(1, at(1)).id == job.id
    ready_bundle(store, job.id, ready_at=NOW)
    assert store.get_job(job.id).state == RUNNING  # finish 가 아직 안 왔다
    assert store.get_bundle(job.id)["expires_at"] == NOW + TTL_HOURS * HOUR
    assert [b.job_id for b in store.bundles_due(NOW + TTL_HOURS * HOUR)] == [job.id]


def test_reconcile_is_idempotent(store):
    core = artifacts_core()
    job = enqueue(store)
    store.start_collect(job.id, policy(), NOW)
    assert store.reconcile_bundles_on_start(at(5)) == ([job.id], [])
    assert store.reconcile_bundles_on_start(at(6)) == ([], [])
    assert store.get_bundle(job.id)["state"] == core.FAILED


# ══ finish 와 묶음을 한 트랜잭션으로 (§5) ════════════════════════════════════


def test_finish_commits_the_terminal_state_and_the_bundle_together(store):
    core = artifacts_core()
    job = enqueue(store)
    assert store.claim(1, at(1)).id == job.id
    digest = install_files(store, job.id, b"tar bytes")
    result = collect_result(
        state=core.READY,
        files=(bundle_file("out/a.png", b"png"),),
        total_bytes=3,
        bundle_bytes=9,
        bundle_sha256=digest,
        bundle_path=bundle_dir(store, job.id) / "bundle.tar",
    )
    assert store.finish(job.id, SUCCEEDED, now=at(2), exit_code=0, bundle=result) is True
    assert store.get_job(job.id).state == SUCCEEDED
    b = store.get_bundle(job.id)
    assert b["state"] == core.READY and b["bundle_sha256"] == digest
    assert b["expires_at"] is not None  # 만료 없는 행을 만들지 않는다


def test_finish_records_a_dropped_bundle_next_to_a_successful_job(store):
    """상한을 넘으면 잡은 그대로 성공하고 산출물만 버려진다(결정 41)."""
    core = artifacts_core()
    job = enqueue(store)
    assert store.claim(1, at(1)).id == job.id
    dropped = collect_result(
        state=core.DROPPED, reason_code="over_bytes", reason_args={"limit": 1024, "seen": 4096}
    )
    assert store.finish(job.id, SUCCEEDED, now=at(2), exit_code=0, bundle=dropped) is True
    assert store.get_job(job.id).state == SUCCEEDED
    assert store.get_job(job.id).summary_code is None  # 잡의 요약은 건드리지 않는다(§3)
    b = store.get_bundle(job.id)
    assert b["state"] == core.DROPPED and b["reason_code"] == "over_bytes"


def test_a_cancellation_accepted_during_collection_wins(store):
    """수집 중에 수용된 취소가 이긴다(§5). 거절된 finish 는 묶음도 안 남긴다 — 한 트랜잭션이다."""
    job = enqueue(store)
    assert store.claim(1, at(1)).id == job.id
    assert store.request_cancel(job.id, "alice-laptop", at(2), 10) == CANCELLING
    core = artifacts_core()
    digest = install_files(store, job.id, b"collected while cancelling")
    result = collect_result(
        state=core.READY,
        files=(bundle_file("out/a.png", b"png"),),
        total_bytes=3,
        bundle_bytes=26,
        bundle_sha256=digest,
        bundle_path=bundle_dir(store, job.id) / "bundle.tar",
    )
    refused = store.finish(
        job.id, SUCCEEDED, now=at(3), exit_code=0, bundle=result, only_from=(RUNNING,)
    )
    assert refused is False
    assert store.get_job(job.id).state == CANCELLING
    assert store.get_bundle(job.id) is None  # 원자성: 거절된 finish 는 아무것도 안 쓴다
    # 다시 판정한 종료 상태와 함께라면 묶음도 같이 커밋된다 — 모은 것은 버리지 않는다
    assert store.finish(job.id, CANCELLED, now=at(4), bundle=result) is True
    assert store.get_job(job.id).state == CANCELLED
    assert store.get_bundle(job.id)["bundle_sha256"] == digest


def test_finish_without_a_bundle_leaves_no_row(store):
    """`bundle` 은 선택이다 — 안 주면 M5e 이전과 똑같이 행동한다."""
    job = enqueue(store)
    assert store.claim(1, at(1)).id == job.id
    assert store.finish(job.id, SUCCEEDED, now=at(2), exit_code=0) is True
    assert store.get_job(job.id).state == SUCCEEDED
    assert store.get_bundle(job.id) is None
    assert bundle_row_count(store, job.id) == 0


def test_a_second_finish_neither_changes_the_state_nor_the_bundle(store):
    core = artifacts_core()
    job = enqueue(store)
    assert store.claim(1, at(1)).id == job.id
    digest = install_files(store, job.id, b"first")
    first = collect_result(
        state=core.READY,
        files=(bundle_file("out/a.png", b"png"),),
        total_bytes=3,
        bundle_bytes=5,
        bundle_sha256=digest,
        bundle_path=bundle_dir(store, job.id) / "bundle.tar",
    )
    assert store.finish(job.id, SUCCEEDED, now=at(2), exit_code=0, bundle=first) is True
    second = collect_result(state=core.EMPTY)
    assert store.finish(job.id, SUCCEEDED, now=at(3), exit_code=0, bundle=second) is False
    b = store.get_bundle(job.id)
    assert b["state"] == core.READY and b["bundle_sha256"] == digest  # 묶음은 불변이다(§3)


# ══ 메타데이터 삭제의 새 조건 (§8) ═══════════════════════════════════════════


def test_delete_old_jobs_keeps_a_job_whose_bundle_files_still_exist(store):
    """지금은 `jobs.artifacts_purged_at` 만 본다(`store.py:475`) — 삭제에 실패한 번들이
    소유 기록과 회계를 잃으면 안 된다(§8)."""
    job = finished_job(store, finished=at(10))
    ready_bundle(store, job.id, ready_at=at(11))
    store.mark_artifacts_purged([job.id], at(20))
    assert store.delete_old_jobs(at(1000)) == 0
    assert store.get_job(job.id) is not None
    assert store.get_bundle(job.id) is not None


def test_delete_old_jobs_keeps_a_job_whose_bundle_still_holds_a_reservation(store):
    job = finished_job(store, finished=at(10))
    store.start_collect(job.id, policy(), at(11))
    assert store.reserve_bundle_bytes(job.id, 128, 10_000, at(11)) is True
    store.mark_artifacts_purged([job.id], at(20))
    assert store.delete_old_jobs(at(1000)) == 0
    assert store.get_job(job.id) is not None


def test_delete_old_jobs_removes_the_job_and_its_bundle_row_once_both_are_gone(store):
    core = artifacts_core()
    job = finished_job(store, finished=at(10))
    ready_bundle(store, job.id, ready_at=at(11))
    store.mark_artifacts_purged([job.id], at(20))
    # 번들이 실제로 사라진 뒤에야 메타데이터가 지워진다
    store.mark_bundles_purged([job.id], core.EXPIRED, at(30))
    for name in ("bundle.tar", "manifest.json"):
        (bundle_dir(store, job.id) / name).unlink()
    bundle_dir(store, job.id).rmdir()
    assert store.delete_old_jobs(at(1000)) == 1
    assert store.get_job(job.id) is None
    assert bundle_row_count(store, job.id) == 0
    assert store.get_bundle(job.id) is None


def test_delete_old_jobs_still_removes_a_job_that_never_had_a_bundle(store):
    """M5e 이전 잡(번들 행 없음)은 예전 규칙 그대로 지워진다 — 조건이 세지기만 하면 안 된다."""
    job = finished_job(store, finished=at(10))
    store.mark_artifacts_purged([job.id], at(20))
    assert store.delete_old_jobs(at(1000)) == 1
    assert store.get_job(job.id) is None
