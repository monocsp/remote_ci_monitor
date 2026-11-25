"""`.claude/` 의 스킬 · 에이전트 문서가 서로를 가리키는 고리가 끊기지 않았는지 잠근다(2026-11-25).

개발 루프 · dev-batch · QA 스킬은 규칙 하나를 한 파일에만 두고 나머지는 한 줄로 가리킨다(정본 표는
개발 루프 이식 설계 §2). 그래서 가리키는 이름이 틀리면 세션은 규칙을 못 읽고 지나간다. 지키는 것:

- 스킬 · 에이전트 문서가 적은 `.claude/agents/<x>.md` · `.claude/skills/<x>/SKILL.md` ·
  `.claude/skills/_shared/<x>.md` 경로, 상대 링크, `tools/` · `scripts/` 경로가 실제로 있다.
- 백틱으로 적은 에이전트 · 스킬 이름(「`build-lead` agent」 · 「`pr` skill」 · `subagent_type`)이
  있다.
- 에이전트 파일의 `name` 이 파일 이름과 같다(스킬 폴더는 `tests/test_agent_skills.py` 가 본다).
- 심각도(HIGH · MED · LOW)를 **정의하는** 줄은 `.claude/agents/isolated-verifier.md` 에만 있다.
- 시험 서버 규칙(포트 8790–8899, `rcm-devtest` data_dir)은 `.claude/skills/_shared/test-server.md`
  에만 있다. `.claude/` 밖(AGENTS.md 등)은 이 잠금의 범위가 아니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / ".claude"
AGENTS = CLAUDE / "agents"
SKILLS = CLAUDE / "skills"
SEVERITY_CANON = AGENTS / "isolated-verifier.md"
TEST_SERVER_CANON = SKILLS / "_shared" / "test-server.md"

#: 설계 §2 의 표 — 이 이식이 만드는 파일. 하나라도 빠지면 다른 문서의 링크가 허공을 가리킨다.
EXPECTED = (
    "agents/isolated-verifier.md",
    "agents/live-verifier.md",
    "agents/plan-lead.md",
    "agents/build-lead.md",
    "agents/scenario-author.md",
    "agents/regression-test-author.md",
    *(f"skills/_shared/{n}.md" for n in ("stage-lead", "test-server", "scenario-trace")),
    *(f"skills/_shared/{n}.md" for n in ("edge-scope", "decision-page", "workplan-diff")),
    *(f"skills/{n}/SKILL.md" for n in ("dev-loop", "workplan", "build-pr", "live-verify")),
    *(f"skills/{n}/SKILL.md" for n in ("edge-audit", "dev-lite", "dev-batch", "qa-batch")),
    *(f"skills/{n}/SKILL.md" for n in ("qa-defect-loop", "qa-defect-fix", "qa-defect-verify")),
    "skills/qa-defect-report/SKILL.md",
)

#: 이 레포 밖의 이름 — 하네스가 주는 스킬 · 에이전트 형식과 따로 설치하는 im-not-ai 스킬.
EXTERNAL_SKILLS = frozenset({"artifact-design", "artifact-capabilities", "humanize-korean"})
EXTERNAL_AGENTS = frozenset({"general-purpose"})

PATH_RE = re.compile(
    r"(?<![\w~./-])\.claude/(agents/[\w-]+\.md|skills/[\w-]+/SKILL\.md|skills/_shared/[\w-]+\.md)"
)
LINK_RE = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
TOOL_RE = re.compile(r"(?<![\w~./<-])((?:tools|scripts)/[\w./-]+\.(?:py|sh))\b")
WORD = r"(sub-?agents?|agents?|skills?|에이전트|스킬)"
NAMED_RE = re.compile(
    rf"`([a-z][a-z0-9-]*)`\s*{WORD}(?![\w-])|(?<![\w-]){WORD}\s+`([a-z][a-z0-9-]*)`"
)
SUBAGENT_RE = re.compile(r"subagent_type\s*[:=]\s*[\"']?([a-z][a-z0-9-]*)")
#: 심각도를 정의하는 줄 — 「HIGH is any of …」, 「HIGH = …」, 「HIGH means …」.
DEFINES_RE = re.compile(
    r"\b(HIGH|MED|MEDIUM|LOW)\b\**\s*(=|:=|means\b|is (any|one|anything|when|if)\b)"
)
#: 정의를 다시 적는 줄 — 심각도 낱말과 정본의 기준 낱말 둘 이상이 한 줄에.
CRITERIA = ("fail-open", "traceback", "secret", "auth", "corrupt", "migrat", "mutcheck")
TEST_SERVER_WORDS = ("8790", "8899", "rcm-devtest")


def _docs() -> list[Path]:
    return sorted(p for p in CLAUDE.rglob("*.md") if p.is_file())


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


@pytest.mark.parametrize("rel", EXPECTED)
def test_every_file_of_the_port_exists(rel: str) -> None:
    assert (CLAUDE / rel).is_file(), f".claude/{rel} is missing"


@pytest.mark.parametrize("doc", _docs(), ids=_rel)
def test_named_paths_and_links_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    missing = [f".claude/{m}" for m in PATH_RE.findall(text) if not (CLAUDE / m).is_file()]
    for target in LINK_RE.findall(text):
        if re.match(r"^[a-z]+:", target) or not re.fullmatch(r"[\w./-]+", target):
            continue  # http: · mailto: · 자리표시(`…`)
        if not (doc.parent / target).resolve().exists():
            missing.append(target)
    missing += [t for t in TOOL_RE.findall(text) if not (ROOT / t).exists()]
    assert not missing, f"{_rel(doc)} points at files that do not exist: {missing}"


@pytest.mark.parametrize("doc", _docs(), ids=_rel)
def test_named_agents_and_skills_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    wrong = []
    for left, left_word, right_word, right in NAMED_RE.findall(text):
        name, word = (left, left_word) if left else (right, right_word)
        if word.startswith(("skill", "스킬")):
            ok = (SKILLS / name / "SKILL.md").is_file() or name in EXTERNAL_SKILLS
        else:
            ok = (AGENTS / f"{name}.md").is_file() or name in EXTERNAL_AGENTS
        if not ok:
            wrong.append(f"`{name}` {word}")
    for name in SUBAGENT_RE.findall(text):
        if not (AGENTS / f"{name}.md").is_file() and name not in EXTERNAL_AGENTS:
            wrong.append(f"subagent_type {name}")
    assert not wrong, f"{_rel(doc)} names what does not exist: {wrong}"


@pytest.mark.parametrize("agent", sorted(AGENTS.glob("*.md")), ids=lambda p: p.stem)
def test_agent_frontmatter_names_its_file(agent: Path) -> None:
    text = agent.read_text(encoding="utf-8")
    assert text.startswith("---\n"), agent
    head = text.split("---\n", 2)[1]
    fields = dict(
        (k.strip(), v.strip())
        for k, s, v in (line.partition(":") for line in head.splitlines())
        if s
    )
    assert fields.get("name") == agent.stem
    assert fields.get("description")


def test_the_shared_folder_is_not_a_skill() -> None:
    assert not (SKILLS / "_shared" / "SKILL.md").exists()


def _defining_lines(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        lower = line.lower()
        restated = re.search(r"\bHIGH\b", line) and sum(w in lower for w in CRITERIA) >= 2
        if DEFINES_RE.search(line) or restated:
            out.append(line.strip())
    return out


def test_severity_is_defined_only_in_the_isolated_verifier() -> None:
    canon = SEVERITY_CANON.read_text(encoding="utf-8")
    assert "## Severity" in canon and _defining_lines(SEVERITY_CANON)
    assert all(word in canon for word in ("HIGH", "MED", "LOW", "fail-open"))
    elsewhere = {
        _rel(p): lines for p in _docs() if p != SEVERITY_CANON and (lines := _defining_lines(p))
    }
    assert not elsewhere, f"link to {_rel(SEVERITY_CANON)} instead of defining: {elsewhere}"


def test_test_server_rules_live_only_in_the_shared_file() -> None:
    canon = TEST_SERVER_CANON.read_text(encoding="utf-8")
    assert all(word in canon for word in TEST_SERVER_WORDS)
    elsewhere = {
        _rel(p): [w for w in TEST_SERVER_WORDS if w in p.read_text(encoding="utf-8")]
        for p in _docs()
        if p != TEST_SERVER_CANON
    }
    assert not {k: v for k, v in elsewhere.items() if v}, elsewhere
