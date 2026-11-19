"""에이전트 스킬 잠금: commit·pr·docs 스킬이 korean-writing 을 부르는지, 스킬 파일의 꼴이 맞는지.

오너 지시(2026-11-19): 커밋과 PR 의 한국어는 korean-writing 스킬로 쓴다. 이 연결이 스킬 문서에서
빠지면 세션은 규칙을 로드하지 않고 쓴다. S3 표의 낱말 수는 문서가 적은 수와 같아야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
KOREAN_WRITING = SKILLS / "korean-writing" / "SKILL.md"
KOREAN_WRITER = ROOT / ".claude" / "agents" / "korean-writer.md"


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    head = text.split("---\n", 2)[1]
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith((" ", "#")):
            fields[key.strip()] = value.strip()
    return fields


def _table_rows(text: str, heading: str) -> list[str]:
    # heading 다음의 첫 표에서 머리행과 구분행을 뺀 행들
    after = text.split(heading, 1)[1]
    rows = []
    for line in after.splitlines():
        if line.startswith("|"):
            rows.append(line)
        elif rows:
            break
    return rows[2:]


@pytest.mark.parametrize("skill_md", sorted(SKILLS.glob("*/SKILL.md")), ids=lambda p: p.parent.name)
def test_skill_frontmatter_names_its_folder(skill_md: Path) -> None:
    fields = _frontmatter(skill_md)
    assert fields.get("name") == skill_md.parent.name
    assert fields.get("description")


@pytest.mark.parametrize("skill", ["commit", "pr", "docs"])
def test_writing_skills_load_korean_writing(skill: str) -> None:
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "`korean-writing`" in text
    assert ".claude/skills/korean-writing/SKILL.md" in text


def test_commit_and_pr_steps_load_it_before_writing() -> None:
    commit = (SKILLS / "commit" / "SKILL.md").read_text(encoding="utf-8")
    steps = commit.split("## Steps", 1)[1]
    assert steps.index("korean-writing") < steps.index("git commit -m")
    pr = (SKILLS / "pr" / "SKILL.md").read_text(encoding="utf-8")
    steps = pr.split("## Steps", 1)[1]
    assert steps.index("korean-writing") < steps.index("gh pr create")


def test_lexicon_tables_hold_the_counts_they_announce() -> None:
    text = KOREAN_WRITING.read_text(encoding="utf-8")
    for heading in ("#### 바꿈 우선", "#### 조건부"):
        announced = int(re.search(re.escape(heading) + r"\((\d+)개\)", text).group(1))
        assert len(_table_rows(text, heading)) == announced, heading


def test_ai_tell_thresholds_keep_the_or_more_reading() -> None:
    # im-not-ai 의 「N회+」는 N 번 이상이다. 「N번 넘게」로 옮기면 문턱이 하나씩 올라간다
    text = KOREAN_WRITING.read_text(encoding="utf-8")
    section = text.split("## 3. ", 1)[1].split("## 4. ", 1)[0]
    assert "넘게" not in section
    assert "넘는" not in section
    assert "세 번 이상" in section
    assert "네 번 이상" in section


def test_korean_writer_agent_reads_the_skill() -> None:
    fields = _frontmatter(KOREAN_WRITER)
    assert fields.get("name") == "korean-writer"
    assert ".claude/skills/korean-writing/SKILL.md" in KOREAN_WRITER.read_text(encoding="utf-8")
