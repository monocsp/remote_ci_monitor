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


# --- 2026-11-27 다른 프로젝트의 개발 · QA 스킬 새 판을 들인 규칙 ---------------------------------
#
# 들인 규칙은 정본 한 곳에 두고 나머지는 가리킨다(dev-loop 의 「Canon」 원칙). 아래 셋을 잠근다.
# 1) 들인 규칙이 정본 파일에 실제로 있고, 그 규칙을 쓰는 자리가 정본을 가리킨다.
# 2) 셈이 든 규칙(계획-시험 대조를 사람에게 올리는 때, 실기 레인의 상한)은 정본 밖에서 다시 적지
#    않는다. 2026-11-25 이식 때 「UNSOUND 세 번이면 사람에게」가 네 파일에 따로 적혔다 — 하나를
#    고치면 셋이 옛 규칙으로 남는다.
# 3) 스캐너가 `tests/` 경로와 맨 `python -m pytest` 도 본다.

#: (파일, 그 파일에 있어야 하는 글) — 규칙의 정본과 그 정본을 가리키는 자리.
ADOPTED = (
    # 다시 받는 계획-시험 대조: 앞 검토를 주고, 바뀌지 않은 행은 이월하고,
    # 새 약점은 반례가 있어야 받는다.
    ("skills/_shared/scenario-trace.md", "## Re-reviews"),
    ("skills/_shared/scenario-trace.md", "counterexample"),
    ("skills/_shared/scenario-trace.md", "carried r<N-1>"),
    ("skills/_shared/scenario-trace.md", "## Ambiguous"),
    # 사람이 읽는 글에서는 SOUND · COVERED 대신 커버리지 말로 쓴다.
    ("skills/_shared/scenario-trace.md", "## Words for people"),
    ("skills/_shared/scenario-trace.md", "커버리지 미충족"),
    ("skills/_shared/decision-page.md", "scenario-trace.md) «Words for people»"),
    # 한 행에는 관측할 결과 하나, 시간 · 나타남 행은 두 경계를 다 적는다.
    ("skills/_shared/scenario-trace.md", "one observable result"),
    # 사람에게 올리는 때의 정본은 isolated-verifier «Rounds» 6 이다.
    ("agents/isolated-verifier.md", "«not closed» twice"),
    ("skills/build-pr/SKILL.md", "«Rounds» 6"),
    ("skills/qa-defect-fix/SKILL.md", "«Rounds» 6"),
    # P5 실기 레인: 고르는 기준은 edge-scope, 절차는 edge-audit.
    ("skills/_shared/edge-scope.md", "## Live lane"),
    ("skills/edge-audit/SKILL.md", "## Live lane"),
    ("skills/edge-audit/SKILL.md", "edge-scope.md) «Live lane»"),
    ("skills/workplan/SKILL.md", "edge-scope.md) «Live lane»"),
    ("agents/isolated-verifier.md", "P5 live lane"),
    # 압축 뒤에는 돌리던 스킬과 그 단계 절을 다시 연다.
    ("skills/_shared/stage-lead.md", "reopen"),
    ("skills/dev-batch/SKILL.md", "«When a lead stops or is needed again»"),
    # 이번 세션에서 부딪친 것: 하네스만 바꾼 PR · 머지 뒤 dev · 시계 주입 · 변이 앵커.
    ("skills/pr/SKILL.md", "tests/test_skill_links.py"),
    ("skills/pr/SKILL.md", "`rcm/ci` on the merge commit"),
    ("skills/pr/SKILL.md", "git branch -d"),
    ("agents/build-lead.md", "injected clock"),
    ("agents/build-lead.md", "pattern not found"),
    # QA: 코드 지적은 문서만 바꾼 커밋으로 닫지 않는다 · 그림 이름의 배율 접미사.
    ("skills/qa-defect-verify/SKILL.md", "documents-only commit"),
    ("skills/qa-defect-report/SKILL.md", "@2x"),
)


@pytest.mark.parametrize(("rel", "needle"), ADOPTED)
def test_each_adopted_rule_sits_where_it_belongs(rel: str, needle: str) -> None:
    text = (CLAUDE / rel).read_text(encoding="utf-8")
    assert needle in text, f".claude/{rel} lacks {needle!r}"


#: 셈이 든 규칙 — (정본, 이름, 규칙을 적는 문단의 꼴). 문단 = 빈 줄이나 목록 항목 머리로 나뉜
#: 덩어리 · 표의 한 행. 목록 전체를 한 덩어리로 보면 다른 항목의 「three times」(같은 게이트
#: 실패)가 UNSOUND 와 한 덩어리에 들어 거짓 빨강이 난다.
COUNTED_RULES = (
    (
        AGENTS / "isolated-verifier.md",
        "plan-to-test escalation",
        re.compile(r"(?=.*\bUNSOUND\b)(?=.*\b(twice|three|third|five|fifth|in a row)\b)", re.S),
    ),
    (
        SKILLS / "_shared" / "edge-scope.md",
        "live lane cap",
        re.compile(r"(?=.*\blive lane\b)(?=.*\b(five|5)\b)", re.S | re.I),
    ),
)


def _paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n|\n(?=[ \t]*(?:\d+\.|[-*]|\|) )", text)
    return [p for p in parts if p.strip()]


@pytest.mark.parametrize(
    ("canon", "name", "pattern"), COUNTED_RULES, ids=[n for _, n, _ in COUNTED_RULES]
)
def test_counted_rules_are_written_only_in_their_canon(
    canon: Path, name: str, pattern: re.Pattern[str]
) -> None:
    paragraphs = _paragraphs(canon.read_text(encoding="utf-8"))
    assert any(pattern.match(p) for p in paragraphs), f"{_rel(canon)} no longer states the {name}"
    elsewhere = {
        _rel(p): [
            q.strip()[:80] for q in _paragraphs(p.read_text(encoding="utf-8")) if pattern.match(q)
        ]
        for p in _docs()
        if p != canon
    }
    restated = {k: v for k, v in elsewhere.items() if v}
    assert not restated, f"the {name} is restated outside {_rel(canon)}: {restated}"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("run `tests/test_nope.py` first", ["tests/test_nope.py"]),
        ("the CDP helper in `tests/test_web_browser.py`", ["tests/test_web_browser.py"]),
        ("`node --test tests/web/queue_card.test.js`", ["tests/web/queue_card.test.js"]),
        ("`tests/test_docs_m5*.py`", []),  # 글롭은 파일 이름이 아니다
        ("`scripts/acceptance/<topic>_live.sh`", []),  # 자리표시
    ],
)
def test_the_path_scan_reads_each_form(text: str, found: list[str]) -> None:
    assert TOOL_RE.findall(text) == found


@pytest.mark.parametrize(
    ("text", "bare"),
    [
        ("python3 -m pytest tests/test_x.py -q", True),
        ("then `python -m pytest`", True),
        ("./.venv/bin/python -m pytest tests/test_x.py -q", False),
        ("pytest covers those", False),  # 낱말일 뿐 명령이 아니다
    ],
)
def test_the_command_scan_reads_each_form(text: str, bare: bool) -> None:
    assert any(p.search(text) for p in BARE_COMMANDS.values()) is bare
