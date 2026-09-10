"""표시 층(M5i · 순수) — `--ref <sha>` 잡의 코드 신원 · 없는 잡의 끝줄.

명세는 `docs/gate-replay-fixes-workplan.md` §3 I2(`source_ident`·`_source_text`) ·
I3(`failure_lines` 의 구조화된 종료 사유). **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

잠그는 모양:
- `--ref <40-hex sha>` 로 넣은 잡은 ref 와 sha 가 같은 것이라 **한 번만** 보인다 — 최근 칸
  `@092dc58`, 큐 칸 `dolomood @092dc58`. 생략은 ref 가 40자리 hex 이고 정규화(소문자·공백) 뒤
  sha 와 **정확히** 같을 때뿐이다. 7자리 접두는 우연히 같은 브랜치·태그가 있을 수 있어 넓다.
- `failure_lines()` 는 종료 사유(`cause`)를 받아 **확정 404**(`CAUSE_NOT_FOUND`)일 때만 `log:`
  줄을 뺀다. 연결 실패·타임아웃은 여전히 「모른다」라 로그 길을 남긴다(결정 70).
"""

from __future__ import annotations

import pytest

from remote_ci_monitor.core.render_text import (
    CAUSE_NOT_FOUND,
    CAUSE_SERVER_ERROR,
    CAUSE_TIMEOUT,
    CAUSE_UNREACHABLE,
    _source_text,
    failure_lines,
    ref_ident,
    source_ident,
)

SHA = "092dc5854301a87eab47c0f1e2d3c4b5a6978877"
REF_JOB = {"mode": "git_ref", "repo": "dolomood", "ref": SHA, "sha": SHA}


# ── I2. `source_ident()` — 최근 칸 ──────────────────────────────────────────


def test_a_ref_that_is_the_full_sha_shows_the_sha_once():
    """`rcm run gate --ref <sha>` 의 잡: `092dc5854301a87eab47c0… @092dc58` 가 아니라 `@092dc58`."""
    assert source_ident(REF_JOB) == "@092dc58"


def test_an_uppercase_or_padded_ref_is_still_the_same_sha():
    """정규화 뒤 비교한다 — 손으로 붙여 넣은 대문자·공백이 「다른 ref」가 되지 않는다."""
    assert source_ident({**REF_JOB, "ref": f"  {SHA.upper()} "}) == "@092dc58"


def test_a_seven_character_prefix_is_a_ref_not_the_sha():
    """접두 일치는 생략하지 않는다 — `092dc58` 이라는 브랜치·태그가 있을 수 있다."""
    assert source_ident({**REF_JOB, "ref": SHA[:7]}) == "092dc58 @092dc58"


def test_a_forty_hex_ref_that_resolved_to_another_sha_shows_both():
    """40자리 hex 인데 sha 가 다르다(가리키는 것이 옮겨 갔다) — 둘 다 보여야 알 수 있다."""
    text = source_ident({**REF_JOB, "ref": "a" * 40})
    assert text.startswith("aaaaaaaa") and text.endswith("… @092dc58"), text


def test_a_branch_ref_is_unchanged():
    assert source_ident({**REF_JOB, "ref": "main"}) == "main @092dc58"


def test_a_ref_equal_to_the_sha_without_a_sha_is_still_the_ref():
    """sha 를 아직 모르면(체크아웃 전) ref 가 유일한 신원이다 — 비우지 않는다."""
    text = source_ident({**REF_JOB, "sha": None})
    assert text.startswith(SHA[:20]) and text.endswith("…") and "@" not in text, text


# ── I2. `ref_ident()` — `rcm run` 의 제출 줄 ─────────────────────────────────
#
# `submitted job #1 (gate · <ref> @<sha>)` 도 같은 규칙이다 — 목록 칸만 고치면 제출 줄이
# `0b605a69823ff83e14018b733927445a568671bb @0b605a6` 로 같은 것을 두 번 찍는다(검증 V6.2).


def test_the_submit_ident_shows_a_full_sha_ref_once():
    assert ref_ident(SHA, SHA) == "@092dc58"


def test_the_submit_ident_keeps_a_branch_and_a_prefix_ref():
    assert ref_ident("main", SHA) == "main @092dc58"
    assert ref_ident(SHA[:7], SHA) == "092dc58 @092dc58"


def test_the_submit_ident_without_a_sha_is_the_ref_and_a_dash():
    """제출 응답에 sha 가 없으면(옛 서버) 지어내지 않는다 — `—` 다."""
    assert ref_ident("main", None) == "main @—"
    assert ref_ident(SHA, None) == f"{SHA} @—"


# ── I2. `_source_text()` — 큐 칸 ────────────────────────────────────────────


def test_the_queue_cell_drops_the_ref_when_it_is_the_sha():
    assert _source_text(REF_JOB) == "dolomood @092dc58"


def test_the_queue_cell_keeps_a_branch_ref():
    assert _source_text({**REF_JOB, "ref": "main"}) == "dolomood @092dc58 ref main"


def test_the_queue_cell_keeps_a_prefix_ref():
    assert _source_text({**REF_JOB, "ref": SHA[:7]}) == "dolomood @092dc58 ref 092dc58"


# ── I3. `failure_lines()` — 구조화된 종료 사유 ───────────────────────────────


def test_a_definite_404_has_no_log_line():
    """없는 잡에는 로그도 없다 — `log: rcm logs 999` 는 아무것도 안 가리키는 안내다."""
    lines = failure_lines({}, job_id=999, url=None, cause=CAUSE_NOT_FOUND)
    assert not any("rcm logs" in ln for ln in lines), lines


@pytest.mark.parametrize("cause", [CAUSE_UNREACHABLE, CAUSE_TIMEOUT, CAUSE_SERVER_ERROR, None])
def test_every_other_unknown_end_keeps_the_log_line(cause):
    """첫 조회 전에 네트워크가 끊긴 exit 3 도 로그 길을 잃지 않는다(결정 70) — 잡은 있을 수 있다."""
    lines = failure_lines({}, job_id=999, url=None, cause=cause)
    assert lines[0] == "log: rcm logs 999", lines


def test_the_cause_does_not_touch_a_finished_job():
    """끝난 잡의 문서가 있으면 사유는 없다(`None`) — 옛 호출 모양 그대로 로그 줄이 첫 줄이다."""
    job = {"id": 162, "state": "failed", "url": "http://macmini:8787/#/jobs/162"}
    lines = failure_lines(job, job_id=162, url=job["url"])
    assert lines == ["log: rcm logs 162 · http://macmini:8787/#/jobs/162"]


def test_a_succeeded_job_still_says_nothing_whatever_the_cause():
    assert failure_lines({"state": "succeeded"}, job_id=1, url=None, cause=CAUSE_NOT_FOUND) == []
