"""M5e 산출물의 순수 규칙 — 상태·이유 코드 · 글롭 검증/컴파일 · 경로·충돌 · 선택 ·
아카이브 허용치 · 적용 분류표 · ack 판정 · 만료 시각.
명세는 docs/m5e-workplan.md §3 · §4 · §7 · §8 · §11 · §18(잠근 API 표면).

`core/artifacts.py` 는 아직 없다 — **구현 전이라 빨간 것이 정상이다.**
모듈은 파일 맨 위가 아니라 **시험 안에서 늦게** 부른다(`artifacts_core()`). 위에서 import 하면
파일 하나가 통째로 수집 오류가 되어 나머지 스위트까지 안 돌고, 「몇 개가 무엇 때문에 빨간지」도
알 수 없다.

I/O 가 없다. 시각은 `jobfactory` 의 고정 `NOW` 만 쓴다 — 벽시계에 기대지 않는다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import re
import tarfile
import unicodedata
from datetime import timedelta
from typing import Any

import pytest

from jobfactory import NOW

# ── 아직 없는 모듈 (§18) ─────────────────────────────────────────────────────


def artifacts_core() -> Any:
    """`remote_ci_monitor.core.artifacts` — 순수 규칙(§18). 구현 전에는 여기서 빨개진다."""
    try:
        import remote_ci_monitor.core.artifacts as mod
    except ImportError as e:
        pytest.fail(
            f"remote_ci_monitor.core.artifacts is not implemented yet "
            f"(docs/m5e-workplan.md §18): {e}",
            pytrace=False,
        )
    return mod


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


SHA_BASE = sha("baseline")
SHA_LOCAL = sha("local edit")
SHA_IN = sha("incoming")
BUNDLE = sha("bundle")
OTHER_BUNDLE = sha("another bundle")

#: (상수 이름, 공개 문자열) — §3 의 상태 표 13줄. 이름을 늦게 부르려고 문자열로 적어 둔다.
STATE_CONSTANTS = (
    ("DISABLED", "disabled"),
    ("PENDING", "pending"),
    ("COLLECTING", "collecting"),
    ("UPLOADING", "uploading"),
    ("READY", "ready"),
    ("EMPTY", "empty"),
    ("DROPPED", "dropped"),
    ("FAILED", "failed"),
    ("SKIPPED", "skipped"),
    ("PURGED", "purged"),
    ("EXPIRED", "expired"),
    ("UNAVAILABLE", "unavailable"),
    ("UNKNOWN", "unknown"),
)
#: `ARTIFACT_STATES` 가 정확히 이 13개라는 것은 아래 테스트가 따로 잠근다.
STATE_NAMES = tuple(text for _, text in STATE_CONSTANTS)
NOT_READY_PURGED_OR_EXPIRED = sorted(
    s for s in STATE_NAMES if s not in ("ready", "purged", "expired")
)

REASON_CODES = (
    "over_bytes",
    "over_files",
    "no_match",
    "path_conflict",
    "unsafe_path",
    "collect_failed",
    "upload_failed",
    "storage_full",
    "timed_out",
    "cancelled",
    "not_run",
    "interrupted",
    "acked",
    "expired",
    "hash_mismatch",
    "not_ready",
)


# ── 상수 · 상태 집합 · 예외 ──────────────────────────────────────────────────


def test_state_constants_are_the_public_strings() -> None:
    core = artifacts_core()
    assert [getattr(core, attr) for attr, _ in STATE_CONSTANTS] == [
        text for _, text in STATE_CONSTANTS
    ]


def test_artifact_states_holds_the_thirteen_values() -> None:
    core = artifacts_core()
    assert core.ARTIFACT_STATES == frozenset(text for _, text in STATE_CONSTANTS)
    assert len(core.ARTIFACT_STATES) == 13
    assert isinstance(core.ARTIFACT_STATES, frozenset)


def test_routed_state_subsets_are_disjoint_and_inside_artifact_states() -> None:
    core = artifacts_core()
    assert core.GONE_STATES == frozenset({core.PURGED, core.EXPIRED})  # 아카이브 GET 이 410
    assert core.PROGRESS_STATES == frozenset({core.PENDING, core.COLLECTING, core.UPLOADING})  # 409
    assert core.NOTHING_STATES == frozenset(
        {core.DISABLED, core.EMPTY, core.SKIPPED, core.UNKNOWN}
    )  # 404
    for one, two in (
        (core.GONE_STATES, core.PROGRESS_STATES),
        (core.GONE_STATES, core.NOTHING_STATES),
        (core.PROGRESS_STATES, core.NOTHING_STATES),
    ):
        assert not (one & two)
    assert (core.GONE_STATES | core.PROGRESS_STATES | core.NOTHING_STATES) <= core.ARTIFACT_STATES
    for group in (core.GONE_STATES, core.PROGRESS_STATES, core.NOTHING_STATES):
        assert isinstance(group, frozenset)


def test_the_states_outside_the_three_subsets_are_the_four_that_carry_a_body_or_a_fault() -> None:
    core = artifacts_core()
    # ready 는 200 · dropped/failed 는 §6 표에 코드가 없다(명세 애매점) · unavailable 은 503
    rest = core.ARTIFACT_STATES - core.GONE_STATES - core.PROGRESS_STATES - core.NOTHING_STATES
    assert rest == frozenset({core.READY, core.DROPPED, core.FAILED, core.UNAVAILABLE})


def test_reasons_holds_the_fourteen_codes() -> None:
    core = artifacts_core()
    assert core.REASONS == frozenset(REASON_CODES)
    assert len(core.REASONS) == 16
    assert isinstance(core.REASONS, frozenset)


def test_size_constants_are_the_spec_values() -> None:
    core = artifacts_core()
    assert core.MAX_COMPONENT_BYTES == 255
    assert core.TAR_HEADER_BYTES == 512
    assert core.TAR_TRAILER_BYTES == 1024
    assert core.TAR_PER_FILE_BYTES == 1024  # 헤더 512 + 데이터 끝의 512 정렬 패딩
    assert core.TAR_RECORD_BYTES == 10240  # tarfile 이 닫을 때 채우는 레코드(20 x 512)


def test_error_types() -> None:
    core = artifacts_core()
    assert issubclass(core.PolicyError, ValueError)
    assert issubclass(core.ArtifactError, Exception)


# ── ArtifactPolicy · BundleFile · AckDecision ────────────────────────────────


def test_policy_defaults_are_the_configuration_defaults() -> None:
    p = artifacts_core().ArtifactPolicy()
    assert p.globs == ()
    assert p.max_bytes == 1_073_741_824  # 1 GiB
    assert p.max_files == 10_000
    assert p.timeout_seconds == 60
    assert p.cancel_timeout_seconds == 5


def test_policy_field_order_is_globs_bytes_files_timeout_cancel() -> None:
    core = artifacts_core()
    assert core.ArtifactPolicy(("a/*.png",), 1, 2, 3, 4) == core.ArtifactPolicy(
        globs=("a/*.png",), max_bytes=1, max_files=2, timeout_seconds=3, cancel_timeout_seconds=4
    )


def test_policy_enabled_follows_the_globs() -> None:
    core = artifacts_core()
    assert not core.ArtifactPolicy().enabled()
    assert not core.ArtifactPolicy(globs=()).enabled()
    assert core.ArtifactPolicy(globs=("goldens/*.png",)).enabled()


def test_policy_is_frozen() -> None:
    core = artifacts_core()
    with pytest.raises(dataclasses.FrozenInstanceError):
        core.ArtifactPolicy().max_bytes = 1


def test_bundle_file_fields_order_and_frozen() -> None:
    core = artifacts_core()
    f = core.BundleFile("goldens/a.png", 12, SHA_IN, 0o644)
    assert (f.path, f.size, f.sha256, f.mode) == ("goldens/a.png", 12, SHA_IN, 0o644)
    assert f == core.BundleFile(path="goldens/a.png", size=12, sha256=SHA_IN, mode=0o644)
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.size = 0


def test_ack_decision_fields_order_and_frozen() -> None:
    core = artifacts_core()
    d = core.AckDecision(200, True, True, "acked")
    assert (d.status, d.purge, d.record, d.reason_code) == (200, True, True, "acked")
    assert d == core.AckDecision(status=200, purge=True, record=True, reason_code="acked")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.status = 409


# ── validate_globs ───────────────────────────────────────────────────────────

GOOD_GLOBS = [
    "test/**/goldens/*.png",  # §4 예시
    "test/failures/*.png",  # §4 예시
    "goldens/*.png",
    "**/*.png",  # 리터럴 조각은 없지만 확장자가 있다
    "build/outputs/**",  # 확장자는 없지만 리터럴 조각이 있다
    "coverage/lcov.info",  # 리터럴만
    "out/*.tar.gz",
    "test/failures/[ab].png",  # 닫힌 문자 클래스
    "test/f?.png",
    "문서/보고서-*.png",
]

BAD_GLOBS = [
    # 절대 경로
    "/abs/*.png",
    "/",
    # `..` 조각
    "..",
    "../x/*.png",
    "a/../b/*.png",
    # 빈 조각 · `.` 조각
    "",
    "a//b/*.png",
    "goldens/",
    "./x/*.png",
    "a/./b/*.png",
    # 백슬래시 · NUL
    "a\\b/*.png",
    "\\*.png",
    "a\x00b/*.png",
    # `.git` 조각
    ".git/*.png",
    "a/.git/x.png",
    # 전체 포괄 — 워크스페이스를 통째로 돌려보내는 실수를 막는다
    "**",
    "*",
    "**/*",
    # 닫히지 않은 문자 클래스
    "test/[abc/*.png",
    "shots/*.[png",
    # 리터럴 조각도 확장자도 없다
    "*/*",
    "*/**",
    "**/*/*",
]


@pytest.mark.parametrize("glob", GOOD_GLOBS)
def test_accepted_globs(glob: str) -> None:
    assert artifacts_core().validate_globs([glob]) == (glob,)


@pytest.mark.parametrize("glob", BAD_GLOBS)
def test_rejected_globs(glob: str) -> None:
    core = artifacts_core()
    with pytest.raises(core.PolicyError) as e:
        core.validate_globs([glob])
    msg = str(e.value)
    assert msg and "\n" not in msg and len(msg) <= 200


@pytest.mark.parametrize("bad", [None, 3, b"a/*.png", ["a/*.png"], 1.5])
def test_non_string_globs_are_rejected(bad: Any) -> None:
    core = artifacts_core()
    with pytest.raises(core.PolicyError):
        core.validate_globs([bad])


def test_validate_globs_returns_a_tuple_in_the_given_order() -> None:
    got = artifacts_core().validate_globs(["b/*.png", "a/*.png"])
    assert got == ("b/*.png", "a/*.png")
    assert isinstance(got, tuple)


def test_validate_globs_accepts_an_empty_sequence() -> None:
    core = artifacts_core()
    assert core.validate_globs([]) == ()
    assert core.validate_globs(()) == ()


def test_one_bad_entry_rejects_the_whole_list() -> None:
    core = artifacts_core()
    with pytest.raises(core.PolicyError):
        core.validate_globs(["goldens/*.png", "**"])


# ── compile_globs — 앵커가 붙는다 ────────────────────────────────────────────


def test_compiled_globs_are_anchored_at_both_ends() -> None:
    """`core/snapshot.py::_glob_to_regex` 는 앵커 없는 조각을 주고 `search` 로 쓰인다 — 함정이다."""
    (pat,) = artifacts_core().compile_globs(["goldens/*.png"])
    assert pat.match("goldens/a.png")
    assert pat.match("deep/goldens/a.png") is None
    assert pat.search("deep/goldens/a.png") is None  # search 로도 안 걸려야 한다
    assert pat.match("goldens/a.png.bak") is None
    assert pat.search("x goldens/a.png") is None


def test_compiled_star_does_not_cross_a_slash() -> None:
    (pat,) = artifacts_core().compile_globs(["goldens/*.png"])
    assert pat.match("goldens/sub/a.png") is None
    assert pat.match("a.png") is None


def test_compiled_double_star_slash_spans_zero_or_more_directories() -> None:
    (pat,) = artifacts_core().compile_globs(["test/**/goldens/*.png"])
    for path in ("test/goldens/a.png", "test/x/goldens/a.png", "test/x/y/goldens/a.png"):
        assert pat.match(path), path
    for path in ("goldens/a.png", "other/goldens/a.png", "test/goldens/sub/a.png"):
        assert pat.match(path) is None, path


def test_compiled_globs_keep_order_and_count() -> None:
    pats = artifacts_core().compile_globs(["a/*.png", "b/*.png", "a/*.png"])
    assert isinstance(pats, tuple) and len(pats) == 3
    assert all(isinstance(p, re.Pattern) for p in pats)
    assert pats[0].match("a/one.png") and pats[1].match("b/one.png")
    assert pats[1].match("a/one.png") is None


def test_compile_globs_of_nothing_is_nothing() -> None:
    assert artifacts_core().compile_globs([]) == ()


def test_compiled_character_class_and_question_mark() -> None:
    (pat,) = artifacts_core().compile_globs(["shots/[ab]?.png"])
    assert pat.match("shots/a1.png") and pat.match("shots/b2.png")
    assert pat.match("shots/c1.png") is None
    assert pat.match("shots/a12.png") is None


def test_a_dot_in_a_glob_is_a_literal_dot() -> None:
    (pat,) = artifacts_core().compile_globs(["goldens/a.png"])
    assert pat.match("goldens/a.png")
    assert pat.match("goldens/axpng") is None


# ── check_path — manifest 규칙 + 조각 255바이트 ──────────────────────────────

GOOD_PATHS = [
    "a",
    "goldens/a.png",
    "test/widget/goldens/shot.png",
    "문서/보고서.txt",
    "emoji/🚀.png",
    "with space/file name.png",
    ".gitignore",  # `.git` 조각이 아니다
    ".github/workflows/ci.yml",
    "a.git/x.png",
    "src/.git.bak",
    "-rf",
    "x/..y/z",  # `..` 로 시작하는 이름은 `..` 조각이 아니다
    "a" * 255,
]

BAD_PATHS = [
    "/etc/passwd",
    "/a",
    "/",
    "",
    "..",
    "../x",
    "a/../b",
    "a/..",
    ".",
    "./a",
    "a/./b",
    "a/.",
    "a//b",
    "a/",
    "//a",
    "a\\b",
    "\\a",
    "a\x00b",
    "\x00",
    ".git",
    ".git/config",
    "sub/.git/HEAD",
]


@pytest.mark.parametrize("path", GOOD_PATHS)
def test_accepted_paths(path: str) -> None:
    assert artifacts_core().check_path(path) == path


@pytest.mark.parametrize("path", BAD_PATHS)
def test_rejected_paths(path: str) -> None:
    core = artifacts_core()
    with pytest.raises(core.PolicyError):
        core.check_path(path)


@pytest.mark.parametrize("path", [None, 3, b"a", ["a"]])
def test_non_string_paths_are_rejected(path: Any) -> None:
    core = artifacts_core()
    with pytest.raises(core.PolicyError):
        core.check_path(path)


def test_check_path_returns_the_path_untouched() -> None:
    """정규화하지 않는다 — 정규형만 받는다. NFD 로 온 이름을 NFC 로 바꾸지 않는다."""
    core = artifacts_core()
    nfd = unicodedata.normalize("NFD", "goldens/가.png")
    assert core.check_path(nfd) == nfd
    assert core.check_path(nfd) != unicodedata.normalize("NFC", "goldens/가.png")


def test_a_component_at_the_byte_limit_is_accepted() -> None:
    core = artifacts_core()
    assert core.check_path("a" * core.MAX_COMPONENT_BYTES) == "a" * core.MAX_COMPONENT_BYTES
    korean = "가" * 85  # 85 × 3 = 255 바이트
    assert len(korean.encode()) == core.MAX_COMPONENT_BYTES
    assert core.check_path("goldens/" + korean) == "goldens/" + korean


def test_a_component_over_the_byte_limit_is_rejected() -> None:
    core = artifacts_core()
    with pytest.raises(core.PolicyError):
        core.check_path("a" * (core.MAX_COMPONENT_BYTES + 1))
    with pytest.raises(core.PolicyError):
        core.check_path("goldens/" + "가" * 86)  # 258 바이트


def test_a_hundred_character_korean_component_is_over_the_byte_limit() -> None:
    """조각 제한은 글자 수가 아니라 UTF-8 바이트다 — 한글 100자는 300바이트다."""
    core = artifacts_core()
    name = "한" * 100
    assert len(name) == 100 and len(name.encode()) == 300
    with pytest.raises(core.PolicyError):
        core.check_path(f"goldens/{name}")


def test_every_component_is_measured_not_only_the_last() -> None:
    core = artifacts_core()
    assert core.check_path("가" * 85 + "/a.png") == "가" * 85 + "/a.png"
    with pytest.raises(core.PolicyError):
        core.check_path("가" * 86 + "/a.png")


def test_the_whole_path_is_still_capped_at_4096() -> None:
    core = artifacts_core()
    ok = "/".join(["a" * 200] * 20)  # 4019 자 — 조각은 전부 200바이트다
    assert len(ok) <= 4096
    assert core.check_path(ok) == ok
    too_long = "/".join(["a" * 200] * 21)  # 4220 자
    assert len(too_long) > 4096
    with pytest.raises(core.PolicyError):
        core.check_path(too_long)


# ── collision_key ────────────────────────────────────────────────────────────


def test_collision_key_folds_case() -> None:
    core = artifacts_core()
    assert core.collision_key("Goldens/A.PNG") == core.collision_key("goldens/a.png")
    assert core.collision_key("A/B") == "a/b"


def test_collision_key_uses_casefold_not_lower() -> None:
    """`casefold` 는 ß → ss 까지 접는다. 이름을 접는 파일 시스템을 위한 이식성 정책이다(§4)."""
    core = artifacts_core()
    assert core.collision_key("straße.png") == core.collision_key("STRASSE.PNG")
    assert "straße.png".lower() != "strasse.png"


def test_collision_key_normalises_to_nfc_first() -> None:
    core = artifacts_core()
    nfc = unicodedata.normalize("NFC", "goldens/가.png")
    nfd = unicodedata.normalize("NFD", "goldens/가.png")
    assert nfc != nfd
    assert core.collision_key(nfc) == core.collision_key(nfd)
    # 라틴 결합 문자도 마찬가지다 — 이스케이프로 적어 편집기가 정규화해도 안 무너진다
    assert core.collision_key("caf\u00e9.png") == core.collision_key("cafe\u0301.png")


def test_collision_key_keeps_genuinely_different_paths_apart() -> None:
    core = artifacts_core()
    keys = {core.collision_key(p) for p in ("a.png", "b.png", "sub/a.png", "a.png.bak")}
    assert len(keys) == 4


# ── collisions — 정규화 · 대소문자 · 디렉터리 접두 ───────────────────────────


def unordered(core: Any, paths: list[str]) -> set[tuple[str, ...]]:
    """짝 안의 순서는 명세가 정하지 않는다 — 짝의 집합으로 비교한다."""
    return {tuple(sorted(pair)) for pair in core.collisions(paths)}


def test_plain_distinct_paths_do_not_collide() -> None:
    core = artifacts_core()
    assert core.collisions(["goldens/a.png", "goldens/b.png", "failures/a.png"]) == []


def test_sibling_names_that_share_a_prefix_do_not_collide() -> None:
    core = artifacts_core()
    assert core.collisions(["a", "ab", "a.txt", "a_dir/c"]) == []
    assert core.collisions(["dir", "directory/x.png"]) == []
    assert core.collisions(["a", "ab/c.png"]) == []


def test_a_case_only_difference_collides() -> None:
    core = artifacts_core()
    assert unordered(core, ["goldens/A.png", "goldens/a.png"]) == {
        ("goldens/A.png", "goldens/a.png")
    }


def test_a_normalisation_only_difference_collides() -> None:
    core = artifacts_core()
    nfc = unicodedata.normalize("NFC", "goldens/가.png")
    nfd = unicodedata.normalize("NFD", "goldens/가.png")
    assert len(core.collisions([nfc, nfd])) == 1


def test_a_directory_prefix_collides() -> None:
    """`A` 는 파일이고 `a/b.png` 는 `a` 안이다 — 이름을 접는 파일 시스템에서 겹친다(§4)."""
    core = artifacts_core()
    assert unordered(core, ["A", "a/b.png"]) == {("A", "a/b.png")}
    assert unordered(core, ["a", "a/b.png"]) == {("a", "a/b.png")}
    assert unordered(core, ["x/Y", "X/y/z.png"]) == {("X/y/z.png", "x/Y")}
    assert unordered(core, ["Deep/Dir", "deep/dir/x/y.png"]) == {("Deep/Dir", "deep/dir/x/y.png")}


def test_the_same_path_twice_collides() -> None:
    assert len(artifacts_core().collisions(["a.png", "a.png"])) == 1


def test_the_result_is_sorted() -> None:
    got = artifacts_core().collisions(["z/B", "Z/b/x.png", "goldens/A.png", "goldens/a.png"])
    assert len(got) == 2
    assert got == sorted(got)


def test_collisions_take_any_iterable_and_report_the_original_paths() -> None:
    got = artifacts_core().collisions(p for p in ("Goldens/A.png", "goldens/a.png"))
    assert len(got) == 1
    assert set(got[0]) == {"Goldens/A.png", "goldens/a.png"}


def test_nothing_and_one_path_never_collide() -> None:
    core = artifacts_core()
    assert core.collisions([]) == []
    assert core.collisions(["goldens/a.png"]) == []


# ── select ───────────────────────────────────────────────────────────────────


def test_select_is_sorted_and_anchored() -> None:
    core = artifacts_core()
    assert core.select(["b.png", "a.png", "sub/c.png"], ["*.png"]) == ["a.png", "b.png"]


def test_select_unions_globs_without_duplicates() -> None:
    core = artifacts_core()
    paths = ["test/goldens/a.png", "test/failures/b.png", "test/other/c.txt"]
    assert core.select(paths, ["test/**/*.png", "test/goldens/*.png"]) == [
        "test/failures/b.png",
        "test/goldens/a.png",
    ]


def test_select_drops_dot_git_whatever_the_glob_says() -> None:
    core = artifacts_core()
    paths = [
        ".git/x.png",
        ".git/objects/aa/bb.png",
        "sub/.git/y.png",
        "ok.png",
        "sub/ok.png",
        ".gitignore.png",
        "a.git/x.png",
    ]
    globs = ["**/*.png", ".git/*.png", "**/.git/**/*.png"]
    assert core.select(paths, globs) == [".gitignore.png", "a.git/x.png", "ok.png", "sub/ok.png"]


def test_select_with_no_globs_selects_nothing() -> None:
    assert artifacts_core().select(["a.png", "b.png"], []) == []


def test_select_is_order_independent_and_takes_any_iterable() -> None:
    core = artifacts_core()
    first = core.select(iter(["b.png", "a.png"]), ["*.png"])
    second = core.select(iter(["a.png", "b.png"]), ["*.png"])
    assert first == second == ["a.png", "b.png"]


def test_select_deduplicates_a_repeated_path() -> None:
    assert artifacts_core().select(["a.png", "a.png"], ["*.png"]) == ["a.png"]


def test_select_of_nothing_is_nothing() -> None:
    assert artifacts_core().select([], ["*.png"]) == []


# ── archive_allowance — 원본 상한을 아카이브 바이트로 환산한다 ───────────────


def sample_tar(sizes: list[int]) -> bytes:
    """USTAR 로 만든 tar 바이트. 조각 이름은 100자 미만이라 pax 확장 헤더가 붙지 않는다."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for i, size in enumerate(sizes):
            info = tarfile.TarInfo(f"goldens/{i}.png")
            info.size = size
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(b"\0" * size))
    return buf.getvalue()


def test_archive_allowance_is_max_bytes_plus_a_file_share_plus_a_record() -> None:
    core = artifacts_core()
    assert core.archive_allowance(1000, 2) == 1000 + 2 * 1024 + 10240
    assert core.archive_allowance(0, 0) == 10240
    assert core.archive_allowance(1_073_741_824, 10_000) == 1_073_741_824 + 10_000 * 1024 + 10240


def test_archive_allowance_uses_the_locked_constants() -> None:
    core = artifacts_core()
    for max_bytes, max_files in ((0, 0), (7, 1), (1 << 20, 64), (1_073_741_824, 10_000)):
        assert core.archive_allowance(max_bytes, max_files) == (
            max_bytes + max_files * core.TAR_PER_FILE_BYTES + core.TAR_RECORD_BYTES
        )


def test_a_bundle_at_the_raw_limit_is_not_refused_by_the_archive_side_check() -> None:
    """원본 상한과 딱 같은 묶음이 tar 헤더 때문에 거절되면 안 된다(§8 회계).

    허용치는 **헤더 + 데이터 + 트레일러**를 정확히 덮는다. 파일 크기가 512 의 배수면 파일별
    패딩이 없어 이 경계가 딱 맞는다.
    """
    core = artifacts_core()
    sizes = [1024, 2048, 512]
    max_bytes = sum(sizes)  # 원본 바이트가 상한과 정확히 같다
    headers_data_trailer = len(sizes) * core.TAR_HEADER_BYTES + sum(sizes) + core.TAR_TRAILER_BYTES
    assert headers_data_trailer > max_bytes  # tar 는 언제나 원본보다 크다 — 환산이 필요하다
    assert headers_data_trailer <= core.archive_allowance(max_bytes, len(sizes))


def test_a_real_tar_is_padded_to_a_full_record() -> None:
    """`tarfile` 은 마지막에 레코드(20 × 512 = 10240)까지 패딩한다.

    이 사실 때문에 허용치 식이 바뀌었다(§8) — 원본 3584 바이트짜리 묶음의 실제 파일이 10240
    바이트여서, 예전 식(`max_bytes + files × 512 + 1024`)으로는 정상 묶음이 413 으로 거절됐다.
    """
    archive = sample_tar([1024, 2048, 512])  # 원본 3584 바이트
    assert len(archive) % tarfile.RECORDSIZE == 0
    assert len(archive) == tarfile.RECORDSIZE  # 10240 — 원본의 세 배다


def test_the_allowance_is_always_looser_than_the_raw_limit() -> None:
    core = artifacts_core()
    # 아카이브 검사가 원본 검사보다 엄해지는 일은 없어야 한다 — 그러면 경계 묶음이 거절된다
    for max_bytes in (0, 1, 3584, 1_073_741_824):
        for max_files in (0, 1, 3, 10_000):
            assert core.archive_allowance(max_bytes, max_files) > max_bytes


# ── classify — §11 표 그대로 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("baseline", "local", "incoming", "want"),
    [
        # 기준선 없음 · 로컬 없음 → 쓴다
        (None, None, SHA_IN, "new"),
        # 기준선 없음 · 로컬이 다르다 → 기준선이 없다는 것은 덮어쓸 자격이 아니다
        (None, SHA_LOCAL, SHA_IN, "conflicted"),
        (None, SHA_BASE, SHA_IN, "conflicted"),
        # 기준선 없음 · 로컬이 받은 것과 같다 → 쓰든 말든 잃을 게 없다(§11)
        (None, SHA_IN, SHA_IN, "unchanged"),
        # 기준선 있음 · 로컬 = 받은 것 → 쓸 것이 없다
        (SHA_IN, SHA_IN, SHA_IN, "unchanged"),
        (SHA_BASE, SHA_IN, SHA_IN, "unchanged"),
        # 기준선 있음 · 로컬 = 기준선 ≠ 받은 것 → 골든 갱신의 본체
        (SHA_BASE, SHA_BASE, SHA_IN, "changed"),
        # 기준선 있음 · 로컬 ≠ 기준선 (사람이 손댔다)
        (SHA_BASE, SHA_LOCAL, SHA_IN, "conflicted"),
        # 기준선 있음 · 로컬이 지워졌다 — 지운 것도 사람이 한 변경이다
        (SHA_BASE, None, SHA_IN, "conflicted"),
        (SHA_IN, None, SHA_IN, "conflicted"),
    ],
)
def test_classify_table(baseline: str | None, local: str | None, incoming: str, want: str) -> None:
    assert artifacts_core().classify(baseline, local, incoming) == want


def test_classify_only_ever_returns_the_four_verdicts() -> None:
    core = artifacts_core()
    seen = {
        core.classify(baseline, local, SHA_IN)
        for baseline in (None, SHA_BASE, SHA_IN)
        for local in (None, SHA_LOCAL, SHA_BASE, SHA_IN)
    }
    assert seen == {"new", "unchanged", "changed", "conflicted"}


def test_classify_compares_bytes_only() -> None:
    # 시각·픽셀 비교는 하지 않는다(§11) — 같은 해시면 같은 것이다
    assert artifacts_core().classify(SHA_BASE, SHA_BASE, SHA_BASE) == "unchanged"


# ── ack_decision — §7 삭제 규칙 ──────────────────────────────────────────────


def ack(core: Any, **kw: Any) -> Any:
    """기본은 「아무도 안 붙은 잡의 요청자가 맞는 해시로 확인했다」."""
    args: dict[str, Any] = {
        "state": core.READY,
        "join_count": 0,
        "owner": True,
        "stored_sha": BUNDLE,
        "sent_sha": BUNDLE,
        "expired": False,
    }
    args.update(kw)
    return core.ack_decision(**args)


def test_ack_by_the_requester_of_a_job_nobody_joined_purges_at_once() -> None:
    d = ack(artifacts_core(), join_count=0, owner=True)
    assert (d.status, d.purge, d.record) == (200, True, True)
    assert d.reason_code == "acked"


@pytest.mark.parametrize("join_count", [1, 2, 7])
def test_ack_on_a_joined_job_is_accepted_but_keeps_the_bundle(join_count: int) -> None:
    d = ack(artifacts_core(), join_count=join_count)
    assert (d.status, d.purge, d.record) == (200, False, True)


@pytest.mark.parametrize("join_count", [0, 1, 5])
def test_an_admin_who_is_neither_requester_nor_joiner_is_a_no_op(join_count: int) -> None:
    """`join_count == 0` 이어도 남의 잡을 지우지 않는다 — acked_at 도 안 쓴다."""
    d = ack(artifacts_core(), owner=False, join_count=join_count)
    assert (d.status, d.purge, d.record) == (200, False, False)


@pytest.mark.parametrize("owner", [True, False])
def test_a_hash_mismatch_is_409(owner: bool) -> None:
    d = ack(artifacts_core(), owner=owner, stored_sha=BUNDLE, sent_sha=OTHER_BUNDLE)
    assert (d.status, d.purge, d.record) == (409, False, False)


def test_a_missing_stored_hash_cannot_match() -> None:
    d = ack(artifacts_core(), stored_sha=None)
    assert (d.status, d.purge, d.record) == (409, False, False)


@pytest.mark.parametrize("state", NOT_READY_PURGED_OR_EXPIRED)
def test_a_state_that_is_neither_ready_nor_purged_is_409(state: str) -> None:
    d = ack(artifacts_core(), state=state)
    assert (d.status, d.purge, d.record) == (409, False, False)


def test_a_replay_after_purge_with_the_same_hash_is_200() -> None:
    """응답을 잃은 클라이언트가 409 를 받지 않게 해시를 묘비로 남긴다(§7 재생)."""
    core = artifacts_core()
    d = ack(core, state=core.PURGED, join_count=0, owner=True)
    assert d.status == 200
    assert d.purge is False  # 이미 없다 — 다시 지우지 않는다
    assert d.record is False  # 묘비의 acked_at 을 덮어쓰지 않는다


@pytest.mark.parametrize("join_count", [0, 3])
def test_a_replay_after_purge_is_200_whatever_the_join_count(join_count: int) -> None:
    core = artifacts_core()
    assert ack(core, state=core.PURGED, join_count=join_count).status == 200


def test_a_replay_after_purge_with_a_different_hash_is_409() -> None:
    core = artifacts_core()
    d = ack(core, state=core.PURGED, stored_sha=BUNDLE, sent_sha=OTHER_BUNDLE)
    assert (d.status, d.purge, d.record) == (409, False, False)


@pytest.mark.parametrize("owner", [True, False])
@pytest.mark.parametrize("join_count", [0, 3])
def test_an_expired_bundle_is_410_and_never_purges(owner: bool, join_count: int) -> None:
    d = ack(artifacts_core(), expired=True, owner=owner, join_count=join_count)
    assert (d.status, d.purge, d.record) == (410, False, False)


def test_the_expired_state_is_410_too_not_409() -> None:
    core = artifacts_core()
    d = ack(core, state=core.EXPIRED, expired=False)
    assert (d.status, d.purge, d.record) == (410, False, False)


def test_every_decision_is_one_of_the_three_statuses() -> None:
    core = artifacts_core()
    cases = [
        ack(core),
        ack(core, join_count=2),
        ack(core, owner=False),
        ack(core, sent_sha=OTHER_BUNDLE),
        ack(core, state=core.PENDING),
        ack(core, state=core.PURGED),
        ack(core, state=core.EXPIRED),
        ack(core, expired=True),
    ]
    for d in cases:
        assert d.status in (200, 409, 410)
        assert isinstance(d.purge, bool) and isinstance(d.record, bool)
        assert not (d.purge and not d.record)  # 기록 없이 지우지 않는다


def test_the_reason_code_of_an_accepted_or_expired_decision_is_a_known_code() -> None:
    core = artifacts_core()
    # 결정 37 — 서버는 문장이 아니라 코드를 내려보낸다
    for d in (
        ack(core),
        ack(core, join_count=2),
        ack(core, owner=False),
        ack(core, state=core.PURGED),
        ack(core, expired=True),
    ):
        assert d.reason_code is None or d.reason_code in core.REASONS


def test_ack_decision_is_keyword_only() -> None:
    core = artifacts_core()
    with pytest.raises(TypeError):
        core.ack_decision(core.READY, 0, True, BUNDLE, BUNDLE, False)


# ── expires_at ───────────────────────────────────────────────────────────────


def test_expires_at_adds_the_hours() -> None:
    core = artifacts_core()
    assert core.expires_at(NOW, 24) == NOW + timedelta(hours=24)
    assert core.expires_at(NOW, 1) == NOW + timedelta(hours=1)
    assert core.expires_at(NOW, 24) - NOW == timedelta(days=1)  # 기본 TTL


def test_expires_at_of_zero_hours_is_the_same_instant() -> None:
    assert artifacts_core().expires_at(NOW, 0) == NOW


def test_expires_at_keeps_the_timezone() -> None:
    got = artifacts_core().expires_at(NOW, 24)
    assert got.tzinfo is NOW.tzinfo
    assert got.utcoffset() == timedelta(0)


def test_expires_at_is_measured_from_ready_at_not_from_the_wall_clock() -> None:
    core = artifacts_core()
    earlier = NOW - timedelta(days=3)
    assert core.expires_at(earlier, 24) == earlier + timedelta(hours=24)
    assert core.expires_at(earlier, 24) != core.expires_at(NOW, 24)
    assert core.expires_at(NOW, 24) == core.expires_at(NOW, 24)
