"""클라이언트 적용(M5e) — `remote_ci_monitor.apply` 의 `plan` · `apply` · 저널.

명세는 docs/m5e-workplan.md §11(기준선 · 여섯 줄 분류표 · 절차 · 저널)과 §18 「잠근 API 표면」의
`apply.py` 블록. **구현보다 먼저 썼다(test-first) — `apply.py` 가 아직 없으니 빨간 것이 정상이다.**

- 모듈은 시험 안에서 늦게 부른다(`mod`·`core` 픽스처). 파일 맨 위에서 import 하면 수집이 통째로
  깨져 「몇 개가 무엇 때문에 빨간지」를 알 수 없다(test_worker_client 의 요령).
- 스테이징은 받은 파일을 **같은 상대 경로**로 담은 디렉터리다(`staging/<path>`) — `apply` 가
  `staging` 을 디렉터리로 받는다는 §18 서명의 자연스러운 읽기.
- 비교는 전부 바이트다. 벽시계 sleep 없음 · 모든 경로는 `tmp_path` 아래다.
- 「아무것도 안 썼다」는 뿌리 전체를 찍어 통째로 비교한다(`tree_of`) — 임시 파일이 남아도 걸린다.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

BASE = b"baseline bytes\n"
INCOMING = b"incoming bytes\n"
EDITED = b"the human edited this\n"
OTHER = b"another local file\n"

#: 저널이 「적용됨」으로 적을 법한 상태 낱말. 낱말 자체는 명세에 없다(문서 「애매했던 것」).
APPLIED_STATES = frozenset({"written", "wrote", "applied"})


class Crash(BaseException):
    """적용 도중 프로세스가 죽는 것을 흉내 낸다 — `except Exception` 에 잡히지 않는다."""


# ── 늦은 import ──────────────────────────────────────────────────────────────


def _load(name: str):
    """구현 전에는 여기서 ImportError 가 난다 — 수집이 아니라 시험이 빨갛다."""
    return importlib.import_module(name)


@pytest.fixture
def mod():
    return _load("remote_ci_monitor.apply")


@pytest.fixture
def core():
    """`BundleFile` 은 `core/artifacts.py` 에 있다(§18)."""
    return _load("remote_ci_monitor.core.artifacts")


# ── 도우미 ───────────────────────────────────────────────────────────────────


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(root: Path, rel: str, data: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def bundle_file(core, rel: str, data: bytes):
    return core.BundleFile(path=rel, size=len(data), sha256=sha(data), mode=0o644)


Case = dict[str, tuple[bytes | None, bytes | None, bytes]]


def build(core, root: Path, staging: Path, spec: Case):
    """`spec` 은 경로 → (기준선 · 로컬 · 받은 것). None 은 「없다」.

    반환: (`plan` 에 줄 BundleFile 튜플, 기준선 매핑 `{path: sha}`).
    """
    files = []
    baseline: dict[str, str] = {}
    for rel, (base, local, incoming) in sorted(spec.items()):
        if base is not None:
            baseline[rel] = sha(base)
        if local is not None:
            put(root, rel, local)
        put(staging, rel, incoming)
        files.append(bundle_file(core, rel, incoming))
    return tuple(files), baseline


def tree_of(root: Path) -> dict[str, str]:
    """뿌리 아래 파일 전체의 내용(심링크는 `-><target>`). 「아무것도 안 썼다」의 단일 비교값."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            out[rel] = "->" + os.readlink(p) if p.is_symlink() else p.read_bytes().hex()
    return out


def verdict_of(plan, path: str) -> str:
    found = [e for e in plan.entries if e.path == path]
    assert len(found) == 1, [e.path for e in plan.entries]
    return found[0].verdict


def entry_of(plan, path: str):
    found = [e for e in plan.entries if e.path == path]
    assert len(found) == 1, [e.path for e in plan.entries]
    return found[0]


def unsafe_plan(mod, files, baseline, root: Path):
    """안전하지 않은 목적지 — 계획이 `safe() is False` 이거나 계획 자체가 거부돼야 한다.

    둘 다 「아무것도 쓰지 않는다」를 지킨다. 거부한 경우 None 을 돌려준다.
    """
    try:
        plan = mod.plan(files, baseline, root)
    except Exception:  # noqa: BLE001 — 계획 단계 거부도 안전하다
        return None
    assert not plan.safe(), [(e.path, e.verdict, e.reason) for e in plan.entries]
    return plan


def applied_in(doc: dict[str, Any]) -> set[str]:
    """저널이 「적용됨」으로 적은 경로. 컨테이너 모양이 명세에 없어 dict/list 둘 다 받는다."""
    files = doc.get("files")
    if isinstance(files, dict):
        return {p for p, state in files.items() if str(state) in APPLIED_STATES}
    if isinstance(files, list):
        return {
            str(f.get("path"))
            for f in files
            if str(f.get("state") or f.get("verdict")) in APPLIED_STATES
        }
    raise AssertionError(f"journal has no per-file state: {doc!r}")


@pytest.fixture
def root(tmp_path) -> Path:
    p = tmp_path / "tree"
    p.mkdir()
    return p


@pytest.fixture
def staging(tmp_path) -> Path:
    p = tmp_path / "staging"
    p.mkdir()
    return p


#: §11 분류표 여섯 줄 — (기준선, 로컬, 받은 것) → 기대 verdict.
TABLE = {
    "out/new.png": ((None, None, INCOMING), "new"),
    "out/no_baseline.png": ((None, EDITED, INCOMING), "conflicted"),
    "out/same.png": ((INCOMING, INCOMING, INCOMING), "unchanged"),
    "out/changed.png": ((BASE, BASE, INCOMING), "changed"),
    "out/touched.png": ((BASE, EDITED, INCOMING), "conflicted"),
    "out/deleted.png": ((BASE, None, INCOMING), "conflicted"),
}


def table_case(core, root: Path, staging: Path):
    return build(core, root, staging, {k: v[0] for k, v in TABLE.items()})


# ── 분류표 여섯 줄 (§11) ─────────────────────────────────────────────────────


def test_a_path_that_was_not_in_the_submission_and_is_not_on_disk_is_new(mod, core, root, staging):
    """1행: 기준선 없음 · 로컬 없음 → `new`(쓴다)."""
    files, baseline = build(core, root, staging, {"out/a.png": (None, None, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert plan.safe() is True
    assert verdict_of(plan, "out/a.png") == "new"


@pytest.mark.parametrize(
    ("local", "verdict", "why"),
    [(EDITED, "conflicted", "differs"), (INCOMING, "unchanged", "identical")],
    ids=["differing", "identical"],
)
def test_an_existing_local_file_without_a_baseline_is_judged_by_content(
    mod, core, root, staging, local, verdict, why
):
    """2·3행: 기준선 없음 · 로컬 있음 → 내용으로 가른다.

    다르면 `conflicted` — **기준선이 없다는 것은 덮어쓸 자격이 아니다.** 같으면 `unchanged` — 쓰든
    말든 잃을 게 없다. 후자를 충돌로 두면 gitignore 되는 실패 diff 처럼 기준선 없는 경로가 흔한
    골든 흐름에서 ack 가 영영 안 나가고 결정 40 이 죽는다(명세 §11).
    """
    files, baseline = build(core, root, staging, {"out/a.png": (None, local, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == verdict, why


def test_a_local_file_equal_to_the_incoming_bytes_is_unchanged(mod, core, root, staging):
    """3행: 기준선 있음 · 로컬 = 받은 것 → `unchanged`(안 쓴다)."""
    files, baseline = build(core, root, staging, {"out/a.png": (INCOMING, INCOMING, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == "unchanged"


def test_an_untouched_local_file_that_differs_is_changed(mod, core, root, staging):
    """4행: 기준선 있음 · 로컬 = 기준선 ≠ 받은 것 → `changed`(**쓴다** — 골든 갱신의 본체)."""
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, BASE, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == "changed"


def test_a_locally_edited_file_is_conflicted(mod, core, root, staging):
    """5행: 기준선 있음 · 로컬 ≠ 기준선(손댔다) → `conflicted`(`--force` 여야 쓴다)."""
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, EDITED, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == "conflicted"


def test_a_file_the_user_deleted_while_waiting_is_conflicted_not_new(mod, core, root, staging):
    """6행: 기준선 있음 · **로컬이 지워졌다** → `conflicted`.

    초고가 틀렸던 곳이다 — 지운 것도 사람이 한 변경이라 조용히 되살리지 않는다.
    """
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, None, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == "conflicted"
    assert not (root / "out/a.png").exists()  # 계획은 아무것도 만들지 않는다


def test_local_bytes_equal_to_the_incoming_win_over_a_touched_baseline(mod, core, root, staging):
    """3행이 5행보다 앞선다 — 손댔더라도 **결과 바이트가 같으면** 쓸 것이 없다."""
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, INCOMING, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == "unchanged"


def test_counts_cover_every_row_of_the_table(mod, core, root, staging):
    """여섯 줄을 한 계획에 넣으면 `counts()` 가 new 1 · unchanged 1 · changed 1 · conflicted 3."""
    files, baseline = table_case(core, root, staging)
    plan = mod.plan(files, baseline, root)
    assert plan.safe() is True
    for path, (_spec, expected) in TABLE.items():
        assert verdict_of(plan, path) == expected, path
    counts = plan.counts()
    assert set(counts) >= {"new", "unchanged", "changed", "conflicted"}, counts
    assert counts["new"] == 1 and counts["unchanged"] == 1, counts
    assert counts["changed"] == 1 and counts["conflicted"] == 3, counts


def test_plan_entries_are_sorted_by_path(mod, core, root, staging):
    """순서가 결정적이어야 「41개까지 적용됐다」가 말이 된다."""
    files, baseline = table_case(core, root, staging)
    plan = mod.plan(files, baseline, root)
    paths = [e.path for e in plan.entries]
    assert paths == sorted(paths), paths
    assert set(paths) == set(TABLE)


def test_entries_carry_the_three_hashes_the_verdict_was_made_from(mod, core, root, staging):
    """§18 `Entry(path, verdict, incoming_sha, baseline_sha, local_sha, reason)` — 판정 근거가
    남아야 쓰기 직전 재확인(절차 3)이 가능하다."""
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, EDITED, INCOMING)})
    e = entry_of(mod.plan(files, baseline, root), "out/a.png")
    assert e.incoming_sha == sha(INCOMING)
    assert e.baseline_sha == sha(BASE)
    assert e.local_sha == sha(EDITED)


# ── 안전 검사 (§11 절차 2 · §15.1) ───────────────────────────────────────────


def test_a_symlink_in_the_parent_chain_makes_the_plan_unsafe(mod, core, root, staging):
    """조각 어디에도 심링크가 없어야 한다 — `out/` 이 심링크면 그 아래는 남의 트리다."""
    (root / "elsewhere").mkdir()
    (root / "out").symlink_to("elsewhere", target_is_directory=True)
    files = (bundle_file(core, "out/a.png", INCOMING),)
    put(staging, "out/a.png", INCOMING)
    before = tree_of(root)
    plan = unsafe_plan(mod, files, {}, root)
    if plan is not None:
        result = mod.apply(plan, staging, root)
        assert result.wrote == 0 and result.complete is False
    assert tree_of(root) == before


def test_a_destination_that_is_itself_a_symlink_makes_the_plan_unsafe(mod, core, root, staging):
    """마지막 조각이 심링크여도 안 된다 — 따라가면 트리 밖에 쓴다."""
    (root / "out").mkdir()
    (root / "outside.png").write_bytes(OTHER)
    (root / "out" / "a.png").symlink_to("../outside.png")
    files = (bundle_file(core, "out/a.png", INCOMING),)
    put(staging, "out/a.png", INCOMING)
    before = tree_of(root)
    plan = unsafe_plan(mod, files, {"out/a.png": sha(BASE)}, root)
    if plan is not None:
        assert mod.apply(plan, staging, root).wrote == 0
    assert tree_of(root) == before
    assert (root / "outside.png").read_bytes() == OTHER


@pytest.mark.parametrize(
    "path",
    ["../evil.png", "out/../../evil.png", "/etc/evil.png", "out/./../../evil.png"],
    ids=["parent", "escape-inside", "absolute", "dotted"],
)
def test_a_path_that_escapes_the_root_makes_the_plan_unsafe(mod, core, root, staging, path):
    """트리 밖으로 나가는 경로는 계획 단계에서 죽는다 — 자재화의 `data` 필터를 믿지 않는다(§2)."""
    files = (bundle_file(core, path, INCOMING),)
    before = tree_of(root)
    plan = unsafe_plan(mod, files, {}, root)
    if plan is not None:
        assert mod.apply(plan, staging, root).wrote == 0
    assert tree_of(root) == before
    assert not (root.parent / "evil.png").exists()


#: 같은 이름의 NFC 형(é 한 글자)과 NFD 형(e + 결합 악센트) — NFC 정규화 뒤 겹친다.
NFC_CAFE = "out/caf\u00e9.png"
NFD_CAFE = "out/cafe\u0301.png"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("out/Golden.png", "out/golden.png"),
        (NFC_CAFE, NFD_CAFE),
        ("out/A", "out/a/b.png"),
    ],
    ids=["casefold", "nfc-nfd", "directory-prefix"],
)
def test_paths_that_collide_after_normalisation_make_the_plan_unsafe(
    mod, core, root, staging, a, b
):
    """NFC → casefold 로 겹치면 묶음 전체가 안전하지 않다. **디렉터리 접두도 충돌이다**(§4).

    대소문자를 구분하지 않는 파일 시스템이 흔해서 두는 이식성 정책이다 — 지금 파일 시스템의
    성질과 무관하게 규칙으로 판정해야 한다.
    """
    files = (bundle_file(core, a, INCOMING), bundle_file(core, b, OTHER))
    before = tree_of(root)
    plan = unsafe_plan(mod, files, {}, root)
    if plan is not None:
        assert mod.apply(plan, staging, root).wrote == 0
    assert tree_of(root) == before


def test_an_unsafe_plan_writes_nothing_at_all_not_even_the_safe_entries(mod, core, root, staging):
    """안전하지 않으면 **아무것도 쓰지 않는다** — 안전한 파일만 골라 쓰지 않는다(§15.1)."""
    (root / "elsewhere").mkdir()
    (root / "out").symlink_to("elsewhere", target_is_directory=True)
    files = (
        bundle_file(core, "out/a.png", INCOMING),  # 안전하지 않다
        bundle_file(core, "safe/b.png", INCOMING),  # 이것만 보면 new
    )
    put(staging, "out/a.png", INCOMING)
    put(staging, "safe/b.png", INCOMING)
    before = tree_of(root)
    plan = unsafe_plan(mod, files, {}, root)
    if plan is not None:
        result = mod.apply(plan, staging, root, force=True)
        assert result.wrote == 0 and result.complete is False
    assert tree_of(root) == before
    assert not (root / "safe" / "b.png").exists()


# ── 적용 (§11 절차 3~4) ──────────────────────────────────────────────────────


def test_apply_writes_new_and_changed_only(mod, core, root, staging):
    """기본은 `new` · `changed` 뿐 — `unchanged` 는 건드리지 않고 `conflicted` 는 남긴다."""
    files, baseline = table_case(core, root, staging)
    plan = mod.plan(files, baseline, root)
    result = mod.apply(plan, staging, root)
    assert result.wrote == 2, result
    assert result.conflicted == 3, result
    assert result.complete is False, result
    assert (root / "out/new.png").read_bytes() == INCOMING
    assert (root / "out/changed.png").read_bytes() == INCOMING
    assert (root / "out/same.png").read_bytes() == INCOMING  # 원래 같았다
    assert (root / "out/no_baseline.png").read_bytes() == EDITED
    assert (root / "out/touched.png").read_bytes() == EDITED
    assert not (root / "out/deleted.png").exists()  # 지운 것은 되살리지 않는다


def test_force_also_writes_the_conflicted_files(mod, core, root, staging):
    """`--force` 는 `conflicted` 까지 덮는다 — 남은 충돌이 없으니 ack 자격(`complete`)이 선다."""
    files, baseline = table_case(core, root, staging)
    plan = mod.plan(files, baseline, root)
    result = mod.apply(plan, staging, root, force=True)
    assert result.wrote == 5, result
    assert result.conflicted == 0, result
    assert result.complete is True, result
    for path in TABLE:
        assert (root / path).read_bytes() == INCOMING, path


def test_a_file_changed_between_planning_and_writing_is_skipped_and_counted_conflicted(
    mod, core, root, staging
):
    """절차 3: 비교와 교체 **사이**에 사람이 고쳤으면 그 파일은 건너뛴다."""
    files, baseline = build(
        core,
        root,
        staging,
        {"out/a.png": (BASE, BASE, INCOMING), "out/b.png": (BASE, BASE, INCOMING)},
    )
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == "changed"
    put(root, "out/a.png", EDITED)  # 계획 뒤, 쓰기 전
    result = mod.apply(plan, staging, root)
    assert (root / "out/a.png").read_bytes() == EDITED, "방금 바뀐 파일을 덮어썼다"
    assert (root / "out/b.png").read_bytes() == INCOMING
    assert result.wrote == 1 and result.conflicted == 1, result
    assert result.complete is False, result


def test_force_still_skips_a_file_that_changed_after_planning(mod, core, root, staging):
    """`--force` 여도 「방금 바뀐 것」은 건너뛴다 — 보여 준 표에 없던 변경이다."""
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, EDITED, INCOMING)})
    plan = mod.plan(files, baseline, root)
    assert verdict_of(plan, "out/a.png") == "conflicted"
    put(root, "out/a.png", OTHER)  # 계획이 본 EDITED 와 또 다르다
    result = mod.apply(plan, staging, root, force=True)
    assert (root / "out/a.png").read_bytes() == OTHER
    assert result.wrote == 0 and result.conflicted == 1, result
    assert result.complete is False, result


def test_writes_go_through_a_temp_file_and_os_replace_in_the_same_directory(
    mod, core, root, staging, monkeypatch
):
    """절차 4: 같은 디렉터리에 임시 파일을 만들고 `os.replace`. 부분적으로 쓰인 골든은 없다."""
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, BASE, INCOMING)})
    calls: list[tuple[str, str]] = []
    real = os.replace

    def spy(src, dst, **kw):
        calls.append((str(src), str(dst)))
        return real(src, dst, **kw)

    monkeypatch.setattr(os, "replace", spy)
    result = mod.apply(mod.plan(files, baseline, root), staging, root)
    assert result.wrote == 1, result
    into_tree = [(s, d) for s, d in calls if Path(d).resolve() == (root / "out/a.png").resolve()]
    assert len(into_tree) == 1, calls
    src, dst = into_tree[0]
    assert Path(src).parent.resolve() == Path(dst).parent.resolve(), (src, dst)
    assert Path(src).name != Path(dst).name, (src, dst)


def test_no_temporary_files_are_left_in_the_tree(mod, core, root, staging):
    """뿌리에 남는 것은 매니페스트의 경로뿐 — `.part` 나 임시 이름이 남으면 안 된다."""
    files, baseline = table_case(core, root, staging)
    mod.apply(mod.plan(files, baseline, root), staging, root, force=True)
    assert sorted(tree_of(root)) == sorted(TABLE)


def test_local_files_missing_from_the_manifest_are_left_alone(mod, core, root, staging):
    """§17: 매니페스트에 없는 로컬 파일은 지우지 않는다."""
    put(root, "out/mine.png", OTHER)
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, BASE, INCOMING)})
    mod.apply(mod.plan(files, baseline, root), staging, root, force=True)
    assert (root / "out/mine.png").read_bytes() == OTHER


def test_complete_is_true_only_when_no_conflict_remains(mod, core, root, staging):
    """`ApplyResult.complete` 가 ack 의 문지기다(§7) — 충돌이 하나라도 남으면 False."""
    clean, clean_baseline = build(
        core, root, staging, {"out/a.png": (BASE, BASE, INCOMING), "out/b.png": (None, None, OTHER)}
    )
    assert mod.apply(mod.plan(clean, clean_baseline, root), staging, root).complete is True
    files, baseline = build(core, root, staging, {"out/c.png": (BASE, EDITED, INCOMING)})
    assert mod.apply(mod.plan(files, baseline, root), staging, root).complete is False


def test_complete_is_false_when_a_write_fails(mod, core, root, staging, monkeypatch):
    """쓰다 실패한 것도 「전부 적용」이 아니다 — 실패를 성공으로 포장하지 않는다."""
    files, baseline = build(core, root, staging, {"out/a.png": (BASE, BASE, INCOMING)})
    real = os.replace

    def spy(src, dst, **kw):
        if Path(dst).is_relative_to(root):
            raise OSError(28, "No space left on device")
        return real(src, dst, **kw)

    monkeypatch.setattr(os, "replace", spy)
    result = mod.apply(mod.plan(files, baseline, root), staging, root)
    assert result.wrote == 0 and result.failed >= 1, result
    assert result.complete is False, result
    assert (root / "out/a.png").read_bytes() == BASE  # 원본은 그대로다


# ── 저널 (§11 절차 5) ───────────────────────────────────────────────────────


def test_write_and_read_journal_round_trip(mod, tmp_path):
    """서버 URL · 잡 번호 · `bundle_sha256` · 목적지 뿌리 · 기준선 · 파일별 상태를 실어 나른다."""
    path = tmp_path / "journal.json"
    doc = {
        "server": "http://build:8787",
        "job_id": 412,
        "bundle_sha256": sha(INCOMING),
        "root": str(tmp_path / "tree"),
        "baseline": {"out/a.png": sha(BASE)},
        "files": {"out/a.png": "written"},
    }
    mod.write_journal(path, doc)
    assert mod.read_journal(path) == doc
    assert json.loads(path.read_text(encoding="utf-8")) == doc  # 사람이 읽을 수 있는 JSON


def test_read_journal_is_none_for_a_missing_or_corrupt_file(mod, tmp_path):
    """없거나 깨진 저널은 None — 반쯤 읽은 저널로 이어 쓰지 않는다."""
    assert mod.read_journal(tmp_path / "nope.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert mod.read_journal(broken) is None


def test_a_crash_partway_leaves_the_journal_and_the_files_written_so_far(
    mod, core, root, staging, tmp_path, monkeypatch
):
    """중간에 죽으면 저널이 **정직하게** 「몇 개까지 적용됐다」를 말한다.

    저널이 말하는 것과 디스크에 실제로 들어간 것이 어긋나면 재개가 거짓말이 된다.
    """
    spec = {f"out/{n}.png": (BASE, BASE, INCOMING) for n in ("a", "b", "c", "d")}
    files, baseline = build(core, root, staging, spec)
    journal = tmp_path / "journal.json"  # 뿌리 밖 — 계수에 섞이지 않는다
    real = os.replace
    into_tree: list[str] = []

    def spy(src, dst, **kw):
        if Path(dst).is_relative_to(root):
            into_tree.append(str(dst))
            if len(into_tree) == 3:
                raise Crash("simulated crash between two files")
        return real(src, dst, **kw)

    monkeypatch.setattr(os, "replace", spy)
    with pytest.raises(Crash):
        mod.apply(mod.plan(files, baseline, root), staging, root, journal=journal)
    written = {p for p in spec if (root / p).read_bytes() == INCOMING}
    assert len(written) == 2, sorted(written)
    doc = mod.read_journal(journal)
    assert doc is not None, "저널이 없다 — 이어 할 수가 없다"
    assert applied_in(doc) == written, (applied_in(doc), written)


def test_a_resumed_apply_finishes_the_rest(mod, core, root, staging, tmp_path, monkeypatch):
    """`--resume` 의 알맹이 — 남은 파일만 적용하고 그제야 `complete` 가 선다."""
    spec = {f"out/{n}.png": (BASE, BASE, INCOMING) for n in ("a", "b", "c", "d")}
    files, baseline = build(core, root, staging, spec)
    journal = tmp_path / "journal.json"
    real = os.replace
    into_tree: list[str] = []

    def spy(src, dst, **kw):
        if Path(dst).is_relative_to(root):
            into_tree.append(str(dst))
            if len(into_tree) == 3:
                raise Crash("simulated crash")
        return real(src, dst, **kw)

    monkeypatch.setattr(os, "replace", spy)
    with pytest.raises(Crash):
        mod.apply(mod.plan(files, baseline, root), staging, root, journal=journal)
    monkeypatch.undo()
    assert mod.read_journal(journal) is not None
    result = mod.apply(mod.plan(files, baseline, root), staging, root, journal=journal)
    assert result.complete is True, result
    for path in spec:
        assert (root / path).read_bytes() == INCOMING, path
    assert sorted(tree_of(root)) == sorted(spec)  # 임시 파일이 남지 않았다
