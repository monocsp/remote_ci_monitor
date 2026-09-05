"""git_ref 순수 규칙 — ref 검증(주입·금지 문자·길이) · 40 hex 판정 · ls-remote 에서 sha 고르기.

mutcheck ⑧: `validate_ref` 의 「`-` 로 시작 금지」를 없애면
`test_validate_ref_guards_argv_injection_leading_dash` 가 빨개진다.
"""

from __future__ import annotations

import pytest

from remote_ci_monitor.core.gitref import (
    MAX_REF_LEN,
    is_full_sha,
    pick_sha,
    short_sha,
    validate_ref,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA_B = "fedcba9876543210fedcba9876543210fedcba98"
SHA_C = "1111111111111111111111111111111111111111"


def ls_remote(*pairs: tuple[str, str]) -> str:
    """`git ls-remote` 출력 모양(`<sha>\\t<refname>` 줄) 을 만든다."""
    return "".join(f"{sha}\t{ref}\n" for sha, ref in pairs)


# ── validate_ref ─────────────────────────────────────────────────────────────


def test_max_ref_len_is_200() -> None:
    assert MAX_REF_LEN == 200


@pytest.mark.parametrize(
    "ref",
    [
        "main",
        "feature/x-1",
        "refs/heads/main",
        "refs/tags/v1.2.3",
        "v1.2.3",
        "release_2026.09",
        "HEAD",
        "a" * MAX_REF_LEN,
    ],
)
def test_validate_ref_accepts_ordinary_refs(ref: str) -> None:
    assert validate_ref(ref) == ref


def test_validate_ref_keeps_case_of_branch_names() -> None:
    # 브랜치 이름은 대소문자를 구분한다 — 40 hex 만 소문자로 정규화한다
    assert validate_ref("Feature/X") == "Feature/X"


def test_validate_ref_lowercases_full_sha() -> None:
    assert validate_ref(SHA.upper()) == SHA
    assert validate_ref(SHA) == SHA


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("", id="empty"),
        pytest.param("a..b", id="double-dot"),
        pytest.param("a@{1}", id="at-brace"),
        pytest.param("a\\b", id="backslash"),
        pytest.param("a^b", id="caret"),
        pytest.param("a:b", id="colon"),
        pytest.param("a?b", id="question"),
        pytest.param("a*b", id="star"),
        pytest.param("a[b", id="bracket"),
        pytest.param("a~1", id="tilde"),
        pytest.param("a b", id="space"),
        pytest.param(" main", id="leading-space"),
        pytest.param("main ", id="trailing-space"),
        pytest.param("a\tb", id="tab"),
        pytest.param("a\nb", id="newline"),
        pytest.param("a\x01b", id="control-char"),
        pytest.param("a\x7fb", id="del"),
        pytest.param("/main", id="leading-slash"),
        pytest.param("main/", id="trailing-slash"),
        pytest.param("main.lock", id="trailing-dot-lock"),
        pytest.param("a" * (MAX_REF_LEN + 1), id="201-chars"),
    ],
)
def test_validate_ref_rejects_bad_refs(ref: str) -> None:
    with pytest.raises(ValueError) as e:
        validate_ref(ref)
    reason = str(e.value)
    assert reason and "\n" not in reason  # 사유 한 줄 — 400 본문에 그대로 실린다


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("-x", id="short-option"),
        pytest.param("--upload-pack=x", id="upload-pack"),
        pytest.param("-", id="lone-dash"),
        pytest.param("--", id="double-dash"),
        pytest.param("--output=x", id="long-option"),
    ],
)
def test_validate_ref_guards_argv_injection_leading_dash(ref: str) -> None:
    """`-` 로 시작하는 ref 는 git 옵션으로 읽힌다. argv 의 `--` 와 별개로 여기서 먼저 막는다."""
    with pytest.raises(ValueError):
        validate_ref(ref)


# ── is_full_sha ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (SHA, True),
        (SHA_C, True),
        (SHA.upper(), True),  # 대소문자 무관 — validate_ref 가 소문자로 정규화한다
        (SHA[:39], False),
        (SHA + "0", False),
        ("g" + SHA[1:], False),
        (SHA[:7], False),
        ("main", False),
        ("", False),
    ],
)
def test_is_full_sha(value: str, expected: bool) -> None:
    assert is_full_sha(value) is expected


# ── pick_sha ─────────────────────────────────────────────────────────────────


def test_pick_sha_head_beats_tag_of_the_same_name() -> None:
    # 태그 줄이 먼저 와도 refs/heads 가 이긴다(출력 순서와 무관)
    out = ls_remote((SHA_B, "refs/tags/v1"), (SHA_C, "refs/tags/v1^{}"), (SHA, "refs/heads/v1"))
    assert pick_sha(out, "v1") == SHA


def test_pick_sha_peeled_annotated_tag_beats_tag_object() -> None:
    out = ls_remote((SHA_B, "refs/tags/v1"), (SHA_C, "refs/tags/v1^{}"))
    assert pick_sha(out, "v1") == SHA_C


def test_pick_sha_lightweight_tag() -> None:
    assert pick_sha(ls_remote((SHA_B, "refs/tags/lw")), "lw") == SHA_B


def test_pick_sha_exact_full_refname() -> None:
    out = ls_remote((SHA, "refs/heads/main"), (SHA_B, "refs/tags/main"))
    assert pick_sha(out, "refs/heads/main") == SHA
    assert pick_sha(out, "refs/tags/main") == SHA_B


def test_pick_sha_exact_full_tag_refname_prefers_peeled_variant() -> None:
    out = ls_remote((SHA_B, "refs/tags/v1"), (SHA_C, "refs/tags/v1^{}"))
    assert pick_sha(out, "refs/tags/v1") == SHA_C


def test_pick_sha_head_symref_line_matches_exactly() -> None:
    # `git ls-remote -- <url> HEAD` 는 `<sha>\tHEAD` 한 줄을 준다 — 원격 기본 브랜치
    assert pick_sha(ls_remote((SHA, "HEAD")), "HEAD") == SHA


@pytest.mark.parametrize(
    "out",
    [
        pytest.param("", id="empty-output"),
        pytest.param(ls_remote((SHA, "refs/heads/dev")), id="other-branch"),
        pytest.param(ls_remote((SHA, "refs/heads/main2")), id="prefix-main2"),
        pytest.param(ls_remote((SHA, "refs/heads/feature/main")), id="suffix-feature-main"),
        pytest.param(ls_remote((SHA, "refs/remotes/origin/main")), id="remote-tracking"),
        pytest.param(ls_remote((SHA, "refs/heads/mai")), id="shorter-name"),
    ],
)
def test_pick_sha_unrelated_refs_give_none(out: str) -> None:
    assert pick_sha(out, "main") is None


def test_pick_sha_full_sha_is_returned_regardless_of_output() -> None:
    assert pick_sha("", SHA) == SHA
    assert pick_sha(ls_remote((SHA_B, "refs/heads/main")), SHA) == SHA


def test_pick_sha_tolerates_blank_lines_and_missing_trailing_newline() -> None:
    out = f"\n{SHA_B}\trefs/tags/main\n\n{SHA}\trefs/heads/main"
    assert pick_sha(out, "main") == SHA


# ── short_sha ────────────────────────────────────────────────────────────────


def test_short_sha() -> None:
    assert short_sha(SHA) == SHA[:7]
    assert short_sha(None) == "—"
    assert short_sha("abc") == "abc"
