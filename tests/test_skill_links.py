"""`.claude/` 의 스킬 · 에이전트 문서가 서로를 가리키는 고리가 끊기지 않았는지 잠근다(2026-11-25).

개발 루프 · dev-batch · QA 스킬은 규칙 하나를 한 파일에만 두고 나머지는 한 줄로 가리킨다(정본
목록은 `dev-loop` 스킬의 「Canon」 표). 그래서 가리키는 이름이 틀리면 세션은 규칙을 못 읽고
지나간다. 지키는 것:

- 스킬 · 에이전트 문서가 적은 `.claude/agents/<x>.md` · `.claude/skills/<x>/SKILL.md` ·
  `.claude/skills/_shared/<x>.md` 경로, 상대 링크, `tools/` · `scripts/` 경로가 실제로 있다.
- 백틱으로 적은 에이전트 · 스킬 이름(「`build-lead` agent」 · 「`pr` skill」 · `subagent_type`)이
  있다.
- 에이전트 파일의 `name` 이 파일 이름과 같다(스킬 폴더는 `tests/test_agent_skills.py` 가 본다).
- 심각도(HIGH · MED · LOW)를 **정의하는** 줄은 `.claude/agents/isolated-verifier.md` 에만 있다.
- 시험 서버 규칙(포트 8790–8899, `rcm-devtest` data_dir)은 `.claude/skills/_shared/test-server.md`
  에만 있다. `.claude/` 밖(AGENTS.md 등)은 이 잠금의 범위가 아니다.
- 그 파일의 첫 ```` ```toml ```` 블록은 그대로 서버 설정으로 읽힌다(`load_server_config`).
- 스킬 · 에이전트 문서와 `CONTRIBUTING.md` 가 예로 든 커밋 제목 · PR 제목(`gh pr create --title`,
  `git commit -m` 과 그 heredoc 첫 줄, `guard_naming.py commit|pr` 의 인자, 백틱으로 적은
  `<type>(<scope>): …` 꼴, 제목 앞에 붙이라는 접두어)은 `tools/guard_naming.py` 의 `check_subject`
  를 통과한다. 2026-11-25 3라운드 — 스킬이 `WIP: ` 접두어를 시켰는데 같은 커밋이 들인 훅은 그
  제목을 거부했다. 자리표시(`<title>` · `…`)는 건너뛴다.
- 스킬 · 에이전트가 시키는 명령은 워크트리의 `.venv` 것이다 — `python3 tools/redgreen.py` 는 pytest
  가 없는 인터프리터로 돌고, 맨 `ruff` · `python scripts/mutcheck.py` 는 PATH 의 남의 것을 쓴다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from remote_ci_monitor.config import load_server_config

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / ".claude"
AGENTS = CLAUDE / "agents"
SKILLS = CLAUDE / "skills"
SEVERITY_CANON = AGENTS / "isolated-verifier.md"
TEST_SERVER_CANON = SKILLS / "_shared" / "test-server.md"

#: 개발 루프 이식이 만든 파일. 하나라도 빠지면 다른 문서의 링크가 허공을 가리킨다.
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
TOML_BLOCK_RE = re.compile(r"^```toml\n(.*?)^```", re.S | re.M)
#: 워크트리 `.venv` 가 아닌 명령 꼴. 허용: `./.venv/bin/python tools/…` ·
#: `./.venv/bin/python scripts/mutcheck.py` · `./.venv/bin/ruff`.
BARE_COMMANDS = {
    "python3 tools/redgreen.py": re.compile(r"(?<![\w/.-])python3?\s+tools/redgreen\.py"),
    "python scripts/mutcheck.py": re.compile(r"(?<![\w/.-])python3?\s+scripts/mutcheck\.py"),
    "ruff …": re.compile(r"(?<![\w/.-])ruff\s+(check|format)\b"),
}


#: 예로 든 커밋 · PR 제목을 찾는 꼴. 값은 두 번째 그룹이다.
TITLE_EXAMPLE_RES = (
    re.compile(r"""gh pr (?:create|edit)\b[^\n]*?--title[ =](["'])(.+?)\1"""),
    re.compile(r"""git commit\b[^\n]*?\s-m\s+(["'])(?!\$\()(.+?)\1"""),
    re.compile(r"""git commit\b[^\n]*?-m\s+"\$\(cat <<-?\s*'?(\w+)'?\n[ \t]*(.+)"""),
    re.compile(r"""guard_naming\.py\s+(?:commit|pr)\s+(["'])(.+?)\1"""),
)
#: 백틱 안의 `type(scope): 요약` 꼴과, 제목을 말하는 줄의 맨 접두어(`WIP: `).
TITLE_PREFIX_RE = re.compile(r"(?<!`)`([A-Za-z][\w-]*:) ?`(?!`)")
TITLE_CONTEXT_RE = re.compile(r"\b(title|draft|header)|제목", re.I)
PLACEHOLDER_RE = re.compile(r"<[^>\n]*>|…|\.\.\.|\$")


def _load_guard_naming():
    path = ROOT / "tools" / "guard_naming.py"
    spec = importlib.util.spec_from_file_location("guard_naming", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_the_test_server_config_block_loads_as_a_server_config(tmp_path: Path) -> None:
    block = TOML_BLOCK_RE.search(TEST_SERVER_CANON.read_text(encoding="utf-8"))
    assert block, f"{_rel(TEST_SERVER_CANON)} has no ```toml block"
    config = tmp_path / "server.toml"
    config.write_text(block.group(1), encoding="utf-8")
    cfg = load_server_config(config, environ={})
    assert 8790 <= cfg.server.port <= 8899
    assert "rcm-devtest" in str(cfg.server.data_dir)


@pytest.mark.parametrize("doc", _docs(), ids=_rel)
def test_commands_run_from_the_worktree_venv(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    bare = [
        f"line {text.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r} ({form})"
        for form, pattern in BARE_COMMANDS.items()
        for m in pattern.finditer(text)
    ]
    assert not bare, f"{_rel(doc)}: use ./.venv/bin/python or ./.venv/bin/ruff — {bare}"


def _title_examples(text: str, types: tuple[str, ...]) -> list[tuple[int, str, str]]:
    """(줄 번호, 찾은 글, 검사할 제목). 자리표시가 든 예는 뺀다."""

    def line(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    out = []
    for pattern in TITLE_EXAMPLE_RES:
        for m in pattern.finditer(text):
            out.append((line(m.start()), m.group(2), m.group(2).strip()))
    header = re.compile(rf"(?<!`)`((?:{'|'.join(types)})(?:\([^)`]*\))?!?: [^`\n]+)`")
    for m in header.finditer(text):
        out.append((line(m.start()), m.group(1), m.group(1)))
    for m in TITLE_PREFIX_RE.finditer(text):
        # 제목 · draft 를 말하는 40자 안의 접두어만 친다(`verified:` 같은 댓글 표식은 뺀다).
        # 맞는 제목 앞에 붙여 본다 — 붙인 결과가 훅을 통과해야 한다.
        near = text[max(0, m.start() - 40) : m.end() + 40]
        if TITLE_CONTEXT_RE.search(near):
            out.append((line(m.start()), m.group(0), f"{m.group(1)} docs(skills): 예시 제목"))
    return [(n, seen, title) for n, seen, title in out if not PLACEHOLDER_RE.search(title)]


def test_example_titles_pass_the_naming_guard() -> None:
    guard = _load_guard_naming()
    checked, wrong = 0, []
    for doc in (*_docs(), ROOT / "CONTRIBUTING.md"):
        for number, seen, title in _title_examples(doc.read_text(encoding="utf-8"), guard.TYPES):
            checked += 1
            if (reason := guard.check_subject(title)) is not None:
                wrong.append(f"{_rel(doc)}:{number}: {seen!r} — {reason}")
    assert checked >= 5, f"the title scan found only {checked} examples — the patterns broke"
    assert not wrong, "examples tools/guard_naming.py refuses:\n" + "\n".join(wrong)


@pytest.mark.parametrize(
    ("text", "refused"),
    [
        ("open it as a draft (`WIP: `, `--draft`)", True),
        ('gh pr create --base dev --title "WIP: docs(x): 초안"', True),
        ('git commit -m "Fix bug"', True),
        ("the header `fix web: 요약` is wrong", False),  # 타입 꼴이 아니면 예로 안 친다
        ('gh pr create --base dev --title "<title>" --draft', False),
        ('git commit -m "fix(web): 카드가 local 을 읽던 것"', False),
        ("header `<type>(<scope>): …`", False),
    ],
)
def test_the_title_scan_reads_each_form(text: str, refused: bool) -> None:
    guard = _load_guard_naming()
    found = _title_examples(text, guard.TYPES)
    assert any(guard.check_subject(t) for _, _, t in found) is refused, found
