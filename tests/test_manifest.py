"""manifest 검증(M5a-2) — 경로 규칙 · mode 정규화 · 해시/크기 · 상한 · 링크 target · 중복 ·
파일/디렉터리 충돌 · `missing_hashes` · `assemble_plan` 순서.

순수 `core/manifest.py` 만 본다. 라우트(`POST /jobs/{id}/tree/manifest`) · blob 저장 · 실제 자재화는
B 의 몫. 경로 규칙은 tar 데이터 필터보다 **엄격**하다 — 정규화하지 않고 정규형만 받는다(가정 1).
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any

import pytest

from remote_ci_monitor.core.manifest import (
    MAX_MANIFEST_FILES,
    Manifest,
    ManifestError,
    ManifestFile,
    ManifestLink,
    Op,
    assemble_plan,
    missing_hashes,
    validate_manifest,
)

MAX = 512 * 1024 * 1024


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


H1 = sha("one")
H2 = sha("two")
H3 = sha("three")
EMPTY = hashlib.sha256(b"").hexdigest()


def f(path: Any, *, mode: Any = 0o100644, size: Any = 3, sha256: Any = H1) -> dict[str, Any]:
    return {"path": path, "mode": mode, "size": size, "sha256": sha256}


def ln(path: Any, target: Any) -> dict[str, Any]:
    return {"path": path, "target": target}


def doc(files: Any = (), links: Any = ()) -> dict[str, Any]:
    return {"files": list(files), "links": list(links)}


def validate(files: Any = (), links: Any = (), *, max_bytes: int = MAX) -> Manifest:
    return validate_manifest(doc(files, links), max_bytes=max_bytes)


def rejects(files: Any = (), links: Any = (), *, max_bytes: int = MAX) -> str:
    """ManifestError 를 기대하고 그 문구를 돌려준다(짧은 한 줄인지도 본다)."""
    with pytest.raises(ManifestError) as e:
        validate_manifest(doc(files, links), max_bytes=max_bytes)
    msg = str(e.value)
    assert msg and "\n" not in msg and len(msg) <= 200
    return msg


def flat(op: Op) -> tuple:
    if op.kind == "mkdir":
        return ("mkdir", op.path)
    if op.kind == "copy":
        return ("copy", op.path, op.sha256, op.mode)
    assert op.kind == "symlink"
    return ("symlink", op.path, op.target)


# ── 모양 ─────────────────────────────────────────────────────────────────────


def test_empty_manifest_is_valid() -> None:
    m = validate()
    assert m.files == () and m.links == ()
    assert m.total_bytes == 0 and set(m.unique_hashes) == set()


def test_links_key_is_optional() -> None:
    m = validate_manifest({"files": [f("a")]}, max_bytes=MAX)
    assert m.links == () and [x.path for x in m.files] == ["a"]


def test_manifest_fields_and_order_are_preserved() -> None:
    m = validate(
        [f("b/x", mode=0o100755, size=7, sha256=H2), f("a", size=3, sha256=H1)],
        [ln("b/l", "x")],
    )
    assert [x.path for x in m.files] == ["b/x", "a"]  # manifest 순서 그대로(조립 순서다)
    assert m.files[0] == ManifestFile(path="b/x", mode=0o755, size=7, sha256=H2)
    assert m.files[1] == ManifestFile(path="a", mode=0o644, size=3, sha256=H1)
    assert m.links == (ManifestLink(path="b/l", target="x"),)
    assert m.total_bytes == 10
    assert set(m.unique_hashes) == {H1, H2}
    assert isinstance(m.files, tuple) and isinstance(m.links, tuple)


def test_manifest_is_frozen() -> None:
    m = validate([f("a")])
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.total_bytes = 0  # type: ignore[misc]


def test_same_content_in_two_paths_counts_bytes_twice_but_hash_once() -> None:
    m = validate([f("a", size=5, sha256=H1), f("b", size=5, sha256=H1)])
    assert m.total_bytes == 10 and set(m.unique_hashes) == {H1}


def test_zero_byte_file_is_fine() -> None:
    m = validate([f("empty", size=0, sha256=EMPTY)])
    assert m.total_bytes == 0 and set(m.unique_hashes) == {EMPTY}


@pytest.mark.parametrize(
    "bad_doc",
    [
        None,
        [],
        "files",
        {"files": "a"},
        {"files": {"path": "a"}},
        {"files": [f("a")], "links": "l"},
        {"files": ["a"]},
        {"files": [None]},
        {"files": [f("a")], "links": ["l"]},
    ],
)
def test_wrong_document_shape_is_rejected(bad_doc: Any) -> None:
    with pytest.raises(ManifestError):
        validate_manifest(bad_doc, max_bytes=MAX)


@pytest.mark.parametrize("missing", ["path", "mode", "size", "sha256"])
def test_file_entry_missing_key_is_rejected(missing: str) -> None:
    entry = f("a")
    del entry[missing]
    rejects([entry])


@pytest.mark.parametrize("missing", ["path", "target"])
def test_link_entry_missing_key_is_rejected(missing: str) -> None:
    entry = ln("l", "a")
    del entry[missing]
    rejects([f("a")], [entry])


# ── 경로 규칙 ────────────────────────────────────────────────────────────────

GOOD_PATHS = [
    "a",
    "a/b/c.txt",
    "문서/보고서.txt",
    "emoji/🚀.txt",
    "with space/file name.txt",
    ".gitignore",  # `.git/` 접두가 아니다
    ".github/workflows/ci.yml",
    "a.git/x",
    "src/.git.bak",
    "-rf",  # 셸을 안 거치니 앞 `-` 는 그냥 이름이다
    "src/.hidden",
    "a" * 255,
    "trailing. ",
    "x/..y/z",  # `..` 로 시작하는 조각 이름은 `..` 조각이 아니다
    "…/dots",
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
    "a/../../x",
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
    ".git/objects/aa/bb",
    "sub/.git/HEAD",
]


@pytest.mark.parametrize("path", GOOD_PATHS)
def test_accepted_file_paths(path: str) -> None:
    assert validate([f(path)]).files[0].path == path


@pytest.mark.parametrize("path", BAD_PATHS)
def test_rejected_file_paths(path: str) -> None:
    rejects([f(path)])


@pytest.mark.parametrize("path", BAD_PATHS)
def test_rejected_link_paths_follow_the_same_rules(path: str) -> None:
    rejects([f("a")], [ln(path, "a")])


@pytest.mark.parametrize("path", ["문서/링크", "with space/l", ".gitignore.lnk", "a/b/c/l"])
def test_accepted_link_paths(path: str) -> None:
    assert validate([f("t")], [ln(path, "t")]).links[0].path == path


@pytest.mark.parametrize("path", [None, 3, b"a", ["a"]])
def test_non_string_path_is_rejected(path: Any) -> None:
    rejects([f(path)])
    rejects([f("a")], [ln(path, "a")])


# ── mode · sha256 · size ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("given", "want"),
    [
        (0o100644, 0o644),
        (0o100755, 0o755),
        (0o644, 0o644),
        (0o755, 0o755),
        (0o600, 0o644),  # 실행 비트만 본다 — 나머지는 접는다
        (0o700, 0o755),
        (0o100600, 0o644),
        (0o100664, 0o644),
        (0o111, 0o755),
        (0o777, 0o755),
        (0, 0o644),
    ],
)
def test_mode_is_normalised_to_644_or_755_by_the_executable_bit(given: int, want: int) -> None:
    m = validate([f("a", mode=given)])
    assert m.files[0].mode == want


@pytest.mark.parametrize("mode", ["644", None, 1.0, "0o755", [0o644]])
def test_non_int_mode_is_rejected(mode: Any) -> None:
    rejects([f("a", mode=mode)])


@pytest.mark.parametrize(
    "digest",
    [
        H1.upper(),
        H1[:63],
        H1 + "0",
        "g" + H1[1:],
        "",
        None,
        12345,
        H1 + "\n",
        " " + H1[1:],
    ],
)
def test_sha256_must_be_64_lowercase_hex(digest: Any) -> None:
    rejects([f("a", sha256=digest)])


@pytest.mark.parametrize("size", [-1, "3", 3.0, None, [3]])
def test_size_must_be_a_non_negative_int(size: Any) -> None:
    rejects([f("a", size=size)])


# ── 상한 ─────────────────────────────────────────────────────────────────────


def test_total_bytes_at_the_limit_is_accepted() -> None:
    m = validate([f("a", size=100), f("b", size=200)], max_bytes=300)
    assert m.total_bytes == 300


def test_total_bytes_over_the_limit_is_rejected_with_exceeds() -> None:
    msg = rejects([f("a", size=100), f("b", size=200)], max_bytes=299)
    assert "exceeds" in msg


def test_single_huge_file_over_the_limit_is_rejected() -> None:
    assert "exceeds" in rejects([f("a", size=MAX + 1)])


def test_max_manifest_files_constant() -> None:
    assert MAX_MANIFEST_FILES == 200_000


def test_exactly_max_files_is_accepted() -> None:
    files = [f(f"f{i}", size=1) for i in range(MAX_MANIFEST_FILES)]
    m = validate(files)
    assert len(m.files) == MAX_MANIFEST_FILES and m.total_bytes == MAX_MANIFEST_FILES


def test_one_more_than_max_files_is_rejected() -> None:
    files = [f(f"f{i}", size=1) for i in range(MAX_MANIFEST_FILES + 1)]
    rejects(files)


# ── 중복 · 파일/디렉터리 충돌 ────────────────────────────────────────────────


def test_duplicate_file_path_is_rejected() -> None:
    rejects([f("a"), f("a", sha256=H2)])


def test_file_and_link_with_the_same_path_are_rejected() -> None:
    rejects([f("a"), f("t")], [ln("a", "t")])


def test_duplicate_link_path_is_rejected() -> None:
    rejects([f("a")], [ln("l", "a"), ln("l", "a")])


@pytest.mark.parametrize(
    ("files", "links"),
    [
        ([f("a"), f("a/b")], []),  # 파일 a 가 파일 a/b 의 디렉터리
        ([f("a/b"), f("a")], []),  # 순서를 바꿔도
        ([f("a/b/c"), f("a/b")], []),
        ([f("d/y")], [ln("d", "x")]),  # 링크 d 가 파일 d/y 의 디렉터리 — 링크를 통해 쓰게 된다
        ([f("a/d/x")], [ln("a/d", "..")]),  # 안을 가리키는 링크라도 디렉터리로는 못 쓴다
        ([f("a/b")], [ln("a", "x")]),
        ([f("x")], [ln("l", "x"), ln("l/inner", "x")]),  # 링크 아래에 링크
    ],
)
def test_a_path_cannot_be_both_a_file_or_link_and_a_directory(
    files: list[dict[str, Any]], links: list[dict[str, Any]]
) -> None:
    rejects(files, links)


def test_sibling_names_that_share_a_prefix_are_not_a_conflict() -> None:
    m = validate([f("a"), f("ab"), f("a.txt"), f("a_dir/c")])
    assert len(m.files) == 4


# ── 링크 target ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("lnk", "a.txt"),
        ("lnk", "dir/a.txt"),
        ("a/lnk", "../x"),  # 정규화하면 x — 안이다
        ("a/b/lnk", "../../c"),
        ("lnk", "a/../b"),
        ("lnk", "a/./b"),
        ("sub/up", ".."),  # 루트 자신
        ("sub/here", "."),
        ("lnk", "dangling"),  # target 이 manifest 에 없어도 된다(존재 검사는 안 한다)
        ("l1", "l2"),  # 링크 → 링크 체인은 각 단계만 본다
        ("lnk", "문서/보고서.txt"),
    ],
)
def test_accepted_link_targets(path: str, target: str) -> None:
    m = validate([f("a.txt"), f("dir/a.txt"), f("x"), f("c")], [ln(path, target), ln("l2", "x")])
    assert ManifestLink(path=path, target=target) in m.links


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("lnk", "/etc/passwd"),
        ("lnk", "/"),
        ("lnk", "../x"),
        ("lnk", ".."),
        ("lnk", "../"),
        ("lnk", "a/../../x"),
        ("a/lnk", "../../x"),
        ("a/b/lnk", "../../../x"),
        ("a/lnk", "../../a/x"),  # 밖으로 나갔다 들어와도 안 된다
        ("lnk", ""),
        ("lnk", "a\x00b"),
    ],
)
def test_rejected_link_targets(path: str, target: str) -> None:
    rejects([f("x")], [ln(path, target)])


@pytest.mark.parametrize("target", [None, 3, ["a"]])
def test_non_string_link_target_is_rejected(target: Any) -> None:
    rejects([f("x")], [ln("lnk", target)])


# ── missing_hashes ───────────────────────────────────────────────────────────


def test_missing_hashes_is_sorted_and_deduplicated() -> None:
    m = validate([f("c", sha256=H3), f("a", sha256=H1), f("b", sha256=H1), f("d", sha256=H2)])
    assert missing_hashes(m, set()) == sorted({H1, H2, H3})
    assert missing_hashes(m, {H1}) == sorted({H2, H3})
    assert missing_hashes(m, {H1, H2, H3}) == []


def test_missing_hashes_ignores_unrelated_known_hashes_and_links() -> None:
    m = validate([f("a", sha256=H1)], [ln("l", "a")])
    assert missing_hashes(m, {H2, H3}) == [H1]
    assert missing_hashes(validate(), set()) == []


# ── assemble_plan ────────────────────────────────────────────────────────────


def test_plan_is_mkdir_then_copy_then_symlink_in_manifest_order() -> None:
    m = validate(
        [
            f("a/b/c.txt", sha256=H1),
            f("a/x.txt", mode=0o100755, sha256=H2),
            f("top.txt", sha256=H3),
        ],
        [ln("a/b/l", "../x.txt"), ln("d/e/l2", "../../top.txt")],
    )
    ops = [flat(op) for op in assemble_plan(m)]
    kinds = [op[0] for op in ops]
    assert kinds == sorted(kinds, key=("mkdir", "copy", "symlink").index)
    mkdirs = [op[1] for op in ops if op[0] == "mkdir"]
    assert sorted(mkdirs) == ["a", "a/b", "d", "d/e"]  # 모든 부모, 한 번씩
    assert mkdirs.index("a") < mkdirs.index("a/b") and mkdirs.index("d") < mkdirs.index("d/e")
    assert [op for op in ops if op[0] == "copy"] == [
        ("copy", "a/b/c.txt", H1, 0o644),
        ("copy", "a/x.txt", H2, 0o755),
        ("copy", "top.txt", H3, 0o644),
    ]
    assert [op for op in ops if op[0] == "symlink"] == [
        ("symlink", "a/b/l", "../x.txt"),
        ("symlink", "d/e/l2", "../../top.txt"),
    ]


def test_plan_for_root_files_has_no_mkdir() -> None:
    ops = [flat(op) for op in assemble_plan(validate([f("a"), f("b", sha256=H2)]))]
    assert ops == [("copy", "a", H1, 0o644), ("copy", "b", H2, 0o644)]


def test_plan_creates_deep_parents_in_order() -> None:
    ops = [flat(op) for op in assemble_plan(validate([f("a/b/c/d/e.txt")]))]
    assert ops == [
        ("mkdir", "a"),
        ("mkdir", "a/b"),
        ("mkdir", "a/b/c"),
        ("mkdir", "a/b/c/d"),
        ("copy", "a/b/c/d/e.txt", H1, 0o644),
    ]


def test_plan_creates_link_parents_too() -> None:
    ops = [flat(op) for op in assemble_plan(validate([f("t")], [ln("x/y/l", "../../t")]))]
    assert ops == [
        ("mkdir", "x"),
        ("mkdir", "x/y"),
        ("copy", "t", H1, 0o644),
        ("symlink", "x/y/l", "../../t"),
    ]


def test_plan_copies_the_same_blob_once_per_path() -> None:
    ops = [flat(op) for op in assemble_plan(validate([f("a", sha256=H1), f("b", sha256=H1)]))]
    assert ops == [("copy", "a", H1, 0o644), ("copy", "b", H1, 0o644)]


def test_plan_for_empty_manifest_is_empty() -> None:
    assert assemble_plan(validate()) == []


def test_plan_rejects_a_path_that_is_both_file_and_directory() -> None:
    # 검증을 거치지 않은 Manifest 가 와도(자재화 쪽 이중 안전) 같은 규칙으로 막는다
    bad = Manifest(
        files=(
            ManifestFile(path="a", mode=0o644, size=1, sha256=H1),
            ManifestFile(path="a/b", mode=0o644, size=1, sha256=H1),
        ),
        links=(),
        total_bytes=2,
    )
    with pytest.raises(ManifestError):
        assemble_plan(bad)
    bad_link = Manifest(
        files=(ManifestFile(path="d/y", mode=0o644, size=1, sha256=H1),),
        links=(ManifestLink(path="d", target="x"),),
        total_bytes=1,
    )
    with pytest.raises(ManifestError):
        assemble_plan(bad_link)
