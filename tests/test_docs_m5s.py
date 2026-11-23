"""M5s 문서 잠금 — 기능마다 문서가 그 낱말을 품고 있는지 본다(`docs/m5s-workplan.md` §5).

여러 PR 이 이 파일에 자기 함수를 더한다. 함수 이름에 PR 의 주제를 넣는다.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_scheduled_jobs_are_documented():
    """PR A — 예약 실행(§4.1). 설정 문서가 규칙의 정본이고 README · 사용 안내(영 · 한)가 그곳을
    가리킨다. 운영 문서는 정지 중에도 마감 시계가 흐른다고 말한다."""
    config = read("docs/configuration.md")
    assert "## Running a job later: `--at` and `--in`" in config
    for word in (
        "--at",
        "--in",
        "start_at",
        "start_in_seconds",
        "`scheduled`",
        "`expired`",
        "schedule_start_deadline_seconds",
        "schedule_max_ahead_days",
        "start_deadline_seconds",
        "start_at_past",
        "start_at_too_far",
        "start_at_ambiguous",
        "start_deadline_too_long",
        "schedule_exists",
        "schedule: true",
        "server does not support scheduling — upgrade it",
        "pools[].scheduled[]",
        "rcm jobs --state scheduled",
        "[display] timezone",
    ):
        assert word in config, f"docs/configuration.md does not mention {word}"
    anchor = "configuration.md#running-a-job-later---at-and---in"
    for rel in ("README.md", "README.ko.md", "docs/usage.md", "docs/usage.ko.md"):
        text = read(rel)
        assert anchor in text, f"{rel} does not link to the scheduling section"
        assert "--at" in text and "--in" in text, rel
    operating = read("docs/operating.md")
    assert "rcm jobs --state scheduled" in operating and "`expired`" in operating
    for rel in ("examples/server.toml", "src/remote_ci_monitor/templates/server.toml"):
        text = read(rel)
        assert "schedule_start_deadline_seconds" in text and "schedule_max_ahead_days" in text
    unreleased = read("CHANGELOG.md").split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    for word in ("--at", "`scheduled`", "`expired`", "schedule_exists", "schedule: true"):
        assert word in unreleased, f"CHANGELOG [Unreleased] does not mention {word}"
