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
  맨 `python -m pytest` 도 같다(2026-11-27). 경로 검사는 `tests/` 의 파일 이름도 본다.
- 2026-11-27 다른 프로젝트의 새 판에서 들인 규칙(다시 받는 계획-시험 대조, 사람에게 쓰는 커버리지
  낱말, P5 실기 레인, 압축 뒤 다시 열기, 머지 뒤 dev, 시계 주입, 변이 앵커)이 정본에 있고, 셈이 든
  규칙은 정본 밖에서 다시 적지 않는다(파일 끝 절).
- 시험 서버 문서의 셸 블록은 모두 운영 가드(`tools/guard_production.py`)를 통과한다 — 막히면
  적힌 대로 따라 할 수 없다(2026-11-27 batch-4: 띄우기 줄과 `server.toml` heredoc 이 막혔다).
  설정 파일은 Write 도구로 쓰고, 띄우기 · 신호 · 정리는 `tools/live_proc.py` 를 부른다. 「Signals」
  의 그 줄들은 문서에서 꺼내 가짜 rcm 으로 글자 그대로 돌린다(도구 자체의 규칙은
  `tests/test_live_proc.py`).
"""

from __future__ import annotations

import importlib.util
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from remote_ci_monitor.config import load_server_config, load_worker_config

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
#: `tools/` · `scripts/` · `tests/` 의 파일 이름. 글롭(`m5*.py`)과 자리표시(`<topic>`)는
#: 이름이 아니다.
TOOL_RE = re.compile(r"(?<![\w~./<-])((?:tools|scripts|tests)/[\w./-]+\.(?:py|sh|js))(?![\w*<>-])")
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
#: `pkill` · `killall` · `pgrep` — 이름으로 죽이지 않는 규칙(「띄울 때 적은 pid 에만」)도 정본
#: 한 곳에만 둔다. `lstart` · `.start` — 신원(pid 와 시작 시각)을 읽고 적는 줄도 정본 밖에서
#: 다시 쓰지 않는다. 다시 쓴 사본은 재확인 비교나 `LC_ALL=C TZ=UTC` 를 빠뜨린 채 남는다.
TEST_SERVER_WORDS = (
    *("8790", "8899", "rcm-devtest"),
    *("pkill", "killall", "pgrep", "lstart", ".start"),
)
TOML_BLOCK_RE = re.compile(r"^```toml\n(.*?)^```", re.S | re.M)
#: 워크트리 `.venv` 가 아닌 명령 꼴. 허용: `./.venv/bin/python tools/…` ·
#: `./.venv/bin/python scripts/mutcheck.py` · `./.venv/bin/ruff`.
BARE_COMMANDS = {
    "python3 tools/redgreen.py": re.compile(r"(?<![\w/.-])python3?\s+tools/redgreen\.py"),
    "python scripts/mutcheck.py": re.compile(r"(?<![\w/.-])python3?\s+scripts/mutcheck\.py"),
    "ruff …": re.compile(r"(?<![\w/.-])ruff\s+(check|format)\b"),
    "python -m pytest": re.compile(r"(?<![\w/.-])python3?\s+-m\s+pytest\b"),
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

_LP = "./.venv/bin/python tools/live_proc.py"
#: 「Signals」 가 시키는 도구 호출 — 띄우기 셋, 신호 둘, 정리 하나.
SIGNAL_LINES = (
    f"cd <abs worktree> && HOME=$D/home {_LP} start --dir $D --name server -- "
    "./.venv/bin/rcm serve --config $D/server.toml",
    "cd <abs worktree> && HOME=$D/home RCM_WORKER_TOKEN=$(cat $D/worker.token) "
    f"{_LP} start --dir $D --name worker -- ./.venv/bin/rcm worker --config $D/worker.toml",
    "cd <abs worktree> && HOME=$D/home RCM_SERVER=http://127.0.0.1:<port> "
    f"RCM_TOKEN=$(cat $D/client.token) {_LP} start --dir $D --name client "
    "--interactive-sigint -- ./.venv/bin/rcm run slow --by acceptance",
    f"cd <abs worktree> && {_LP} signal --dir $D --name worker --sig TERM "
    "--expect-config $D/worker.toml",
    f"cd <abs worktree> && {_LP} signal --dir $D --name client --sig INT",
    f"cd <abs worktree> && {_LP} stop-all --dir $D",
)

#: (파일, 그 파일에 있어야 하는 글) — 규칙의 정본과 그 정본을 가리키는 자리.
ADOPTED = (
    # 다시 받는 계획-시험 대조: 앞 검토를 주고, 바뀌지 않은 행은 이월하고,
    # 새 약점은 반례가 있어야 받는다.
    ("skills/_shared/scenario-trace.md", "## Re-reviews"),
    # 바늘은 규칙 문장 그 자체다. 낱말 하나면 표의 다른 칸에 남아, 문장을 지워도 초록이다.
    ("skills/_shared/scenario-trace.md", "**A new weakness needs a counterexample.**"),
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
    # 「Rounds」 1: 읽는 라운드만 나란히, 실기 레인은 명령을 돌리니 차례로.
    ("agents/isolated-verifier.md", "its live lane runs one after another"),
    # 실기 레인의 신호는 띄울 때 적은 pid 에만 보낸다. 정본은 test-server «Signals», 나머지는
    # 가리킨다. 신원은 pid 와 띄운 직후 적은 시작 시각이다 — 명령줄 대조만으로는 `--config` 가
    # 없는 클라이언트(`HOME` · `RCM_SERVER` 로 data_dir 을 받는다)가 영원히 통과하지 못했다.
    ("skills/_shared/test-server.md", "## Signals"),
    # 바늘은 띄우고 신호를 보내는 줄 그 자체다(2026-11-27 batch-5, 선택 C). 셸로 띄우던 줄은
    # 운영 가드 훅에 막혔다. 도구가 pid 와 `LC_ALL=C TZ=UTC` 의 시작 시각을 적고 다시 견준다.
    *(("skills/_shared/test-server.md", line) for line in SIGNAL_LINES),
    ("skills/_shared/test-server.md", "`LC_ALL=C TZ=UTC ps -p <pid> -o lstart=`"),
    ("skills/_shared/test-server.md", "a client has no `--config`"),
    ("skills/_shared/test-server.md", "sends nothing, prints both start times"),
    ("skills/_shared/test-server.md", "There is no lookup\nby name."),
    # 셸이 작업 제어 없이 뒤로 보낸 작업은 SIGINT 를 무시한 채 시작한다(파이썬은 그때 ctrl-C
    # 처리기를 달지 않는다). 그래서 SIGINT 를 받을 클라이언트는 기본 처분으로 돌려 띄운다.
    ("skills/_shared/test-server.md", "`--interactive-sigint` gives the child SIGINT's default"),
    # 설정 파일은 Write 도구로 — heredoc 블록은 운영 가드 훅에 막혔다.
    ("skills/_shared/test-server.md", "write the two files with the **Write tool**"),
    ("agents/live-verifier.md", "with the Write tool"),
    # 바늘은 금지 문장 그 자체다. 「never by name」 낱말만이면 굵은 머리말에도 맞는다.
    (
        "skills/_shared/test-server.md",
        "Never by name: `pkill rcm`, `killall` or a `pgrep` pattern also hit the production",
    ),
    ("skills/edge-audit/SKILL.md", "test-server.md «Signals»"),
    ("skills/live-verify/SKILL.md", "test-server.md) «Signals»"),
    ("agents/live-verifier.md", "test-server.md) «Signals»"),
    # 압축 뒤에는 돌리던 스킬과 그 단계 절을 다시 연다.
    ("skills/_shared/stage-lead.md", "reopen the `SKILL.md` you were running"),
    ("skills/dev-batch/SKILL.md", "«When a lead stops or is needed again»"),
    # 이번 세션에서 부딪친 것: 하네스만 바꾼 PR · 머지 뒤 dev · 시계 주입 · 변이 앵커.
    ("skills/pr/SKILL.md", "tests/test_skill_links.py"),
    ("skills/pr/SKILL.md", "`rcm/ci` on the merge commit"),
    ("skills/pr/SKILL.md", "git branch -d"),
    ("skills/pr/SKILL.md", "the main session's `dev` worktree"),
    ("skills/pr/SKILL.md", "git branch -r --contains"),
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


#: (파일, 없어야 하는 글) — 고친 뒤 남으면 안 되는 옛 문장.
RETIRED = (
    # 「Rounds」 1 의 옛 말은 P5 전체가 나란히 돈다고 읽힌다(실기 레인은 차례로 돈다).
    ("agents/isolated-verifier.md", "P5's run in parallel"),
    # 옛 신원 확인 — 명령줄만 보면 클라이언트는 이 data_dir 의 설정을 보일 수 없다.
    ("skills/_shared/test-server.md", "still shows this `data_dir`'s config"),
    ("skills/edge-audit/SKILL.md", "«Parallel servers and cleanup»"),
    # 명령줄 조건을 산문으로만 적던 문장 — 이제 블록의 `case` 가 견준다.
    ("skills/_shared/test-server.md", "must also show this `data_dir`'s config"),
    # 두 시각을 이유로 적으라고만 하고 찍는 줄이 없던 문장.
    ("skills/_shared/test-server.md", "with the\ntwo start times as its reason"),
    # 셸로 띄우고 견주던 꼴 — 운영 가드 훅에 막혀 따라 할 수 없었다(batch-4).
    ("skills/_shared/test-server.md", "& echo $! >"),
    ("skills/_shared/test-server.md", "kill -$SIG"),
    ("skills/_shared/test-server.md", "set -m"),
    ("skills/_shared/test-server.md", "<<EOF"),
    ("agents/live-verifier.md", "with its shell commands"),
)


@pytest.mark.parametrize(("rel", "gone"), RETIRED)
def test_each_retired_sentence_is_gone(rel: str, gone: str) -> None:
    text = (CLAUDE / rel).read_text(encoding="utf-8")
    assert gone not in text, f".claude/{rel} still says {gone!r}"


def test_the_edge_scope_page_gives_the_reason_not_a_quote() -> None:
    # 규칙 쪽에는 다른 프로젝트 오너의 말과 날짜를 옮기지 않고 규칙의 이유만 쓴다.
    text = (SKILLS / "_shared" / "edge-scope.md").read_text(encoding="utf-8")
    assert not re.search(r"[\uac00-\ud7a3]", text), "edge-scope.md quotes Korean words"
    # 옛 문장의 앞머리(batch-1). 그 문장은 줄바꿈으로 「came from」 과 「asked」 가 갈라져 있었다.
    assert "2026-11-26" not in text and "The owner of the project these skills" not in text


#: 프로세스에 신호를 보내는 말 — 낱말이 바뀌어도 걸리게 넓게 잡는다.
SIGNAL_RE = re.compile(r"\bSIG[A-Z]{2,}|\bsignal|\bkill|ctrl-C|\binterrupt|\bterminat", re.I)
#: 실기 레인 표에서는 서버 · 워커를 멈추거나 다시 띄우는 말도 신호를 보내는 말이다.
STOP_RE = re.compile(r"\bstop|\brestart", re.I)
LIVE_LANE_HEAD = "| condition | means | described in |"


def _live_lane_rows(text: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(LIVE_LANE_HEAD) + 2  # 머리줄과 구분줄 다음
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return rows


def test_every_signal_row_of_the_live_lane_points_at_the_pid_rule() -> None:
    # 행 목록이나 행 수가 아니라 표의 모든 행을 본다. 신호 · 멈춤 · 재시작을 말하거나 «Signals»
    # 를 가리키는 행은 적은 pid 와 «Signals» 를 둘 다 말한다. 행이 늘어도, 신호 낱말을 다른 말로
    # 바꿔도(SIGINT → interrupt) 그 행은 골라진다.
    text = (SKILLS / "_shared" / "edge-scope.md").read_text(encoding="utf-8")
    rows = _live_lane_rows(text)
    assert rows, f"edge-scope.md has no {LIVE_LANE_HEAD!r} table"
    picked = [r for r in rows if "«Signals»" in r or SIGNAL_RE.search(r) or STOP_RE.search(r)]
    assert picked, rows
    for row in picked:
        assert "recorded pid" in row and "test-server.md` «Signals»" in row, row


def test_live_verify_points_its_signals_at_the_pid_rule() -> None:
    # live-verify 본문(머리말 뒤)에서 신호를 말하는 항목마다 «Signals» 링크.
    text = (SKILLS / "live-verify" / "SKILL.md").read_text(encoding="utf-8")
    body = text.split("\n---\n", 1)[1]
    items = [p for p in _paragraphs(body) if SIGNAL_RE.search(p)]
    assert items, "live-verify names no signal"
    for item in items:
        assert "test-server.md) «Signals»" in item, item


def _sh_blocks(text: str) -> list[str]:
    return re.findall(r"^```sh\n(.*?)^```", text, re.S | re.M)


def test_the_ctrl_c_client_waits_on_a_slow_preset(tmp_path: Path) -> None:
    # ctrl-C 행을 따라 하려면 신호가 닿을 때 클라이언트가 아직 기다리고 있어야 한다. `ok` 는
    # 곧바로 끝나 SIGINT 가 빈 pid 에 간다. 그래서 클라이언트는 느린 가짜 프리셋으로 기다린다.
    text = TEST_SERVER_CANON.read_text(encoding="utf-8")
    block = TOML_BLOCK_RE.search(text)
    assert block, "test-server.md has no ```toml block"
    config = tmp_path / "server.toml"
    config.write_text(block.group(1), encoding="utf-8")
    presets = {p.name: p for p in load_server_config(config, environ={}).presets}
    client = re.search(
        r"--name client --interactive-sigint -- \./\.venv/bin/rcm run (\S+) --by acceptance(.*)$",
        text,
        re.M,
    )
    assert client, "test-server.md has no --interactive-sigint client line"
    assert "--no-wait" not in client.group(2), client.group(0)
    preset = presets.get(client.group(1))
    assert preset, f"the client runs {client.group(1)!r}, not a preset of the heredoc"
    sleep = re.search(r"\bsleep (\d+)", " ".join(preset.argv))
    assert sleep and int(sleep.group(1)) >= 10, preset.argv
    assert preset.timeout_seconds > int(sleep.group(1)), preset


def _load_guard_production():
    path = ROOT / "tools" / "guard_production.py"
    spec = importlib.util.spec_from_file_location("guard_production", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fill(block: str, worktree: Path, port: str = "8795") -> str:
    return (
        block.replace("<abs worktree>", str(worktree))
        .replace("<topic>", "t")
        .replace("<port>", port)
    )


def test_every_shell_block_of_the_test_server_passes_the_production_guard(tmp_path: Path) -> None:
    # 문서의 셸 블록은 Bash 도구로 그대로 돈다. 운영 설치가 있는 빌드 머신의
    # `tools/guard_production.py` 가 막으면 적힌 대로 따라 할 수 없다(batch-4: 띄우기 줄 셋과
    # `server.toml` heredoc 이 막혔다). 블록 통째로도, 한 줄씩도 그물에 걸리지 않고 판정도 없다.
    guard = _load_guard_production()
    prod = guard.Production(config_dir=tmp_path / "prod-config", data_dir=tmp_path / "prod-data")
    blocks = _sh_blocks(TEST_SERVER_CANON.read_text(encoding="utf-8"))
    assert len(blocks) >= 5, blocks
    refused = []
    for block in blocks:
        filled = _fill(block, ROOT)
        lines = [line for line in filled.splitlines() if line.strip()]
        for command in (filled, *lines):
            verdict = guard.decide("Bash", {"command": command}, prod, ROOT, environ={})
            if guard._setup_net(command) or verdict is not None:
                refused.append((command, verdict))
    assert not refused, refused


def test_the_worker_config_block_loads_as_a_worker_config(tmp_path: Path) -> None:
    blocks = TOML_BLOCK_RE.findall(TEST_SERVER_CANON.read_text(encoding="utf-8"))
    assert len(blocks) >= 2, "test-server.md has no worker.toml block"
    config = tmp_path / "worker.toml"
    config.write_text(blocks[1], encoding="utf-8")
    cfg = load_worker_config(config, environ={})
    assert cfg.server.startswith("http://127.0.0.1:")
    assert 8790 <= int(cfg.server.rsplit(":", 1)[1]) <= 8899
    assert "rcm-devtest" in str(cfg.data_dir)


#: 가짜 rcm — 준비를 찍고 잔다. 서버 · 워커 · 클라이언트를 모두 이것이 맡는다.
FAKE_RCM = "import time; print('ready', flush=True); time.sleep(30)\n"


def _signals_blocks(text: str) -> list[str]:
    section = text[text.index("## Signals") : text.index("## Production is read-only")]
    return _sh_blocks(section)


def _ps(pid: int, field: str) -> str:
    env = dict(os.environ, LC_ALL="C", TZ="UTC")
    out = subprocess.run(
        ["ps", "-p", str(pid), "-o", f"{field}="], env=env, capture_output=True, text=True
    )
    return out.stdout


def _state(pid: int) -> str:
    out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return out.stdout.strip()


def _ended(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _state(pid)
        if not state or state.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def _ready(log: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if log.exists() and "ready" in log.read_text(encoding="utf-8"):
            return
        time.sleep(0.05)
    raise AssertionError(f"{log.name} never printed ready")


@pytest.fixture
def outside_tmp() -> Iterator[Path]:
    """`/tmp` 밖의 시험 폴더 하나. 도구는 진짜 홈의 운영 자리를 보고 `/tmp` 아래의 `--dir` 를
    거부하는데, 맥미니 CI 는 TMPDIR 을 /private/tmp 아래에 두어 tmp_path 도 거기 있다(잡 4380).
    그래서 진짜 홈(`HOME` 이 아니라 계정의 홈 — 도구와 같다)의
    `~/.local/share/rcm-devtest-livep-<무작위>` 를 만들고 끝나면 그 폴더만 지운다."""
    base = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "share"
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="rcm-devtest-livep-", dir=base))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.parametrize("shell", ["sh", "zsh"])
def test_the_signals_blocks_run_as_written(tmp_path: Path, outside_tmp: Path, shell: str) -> None:
    # «Signals» 의 블록을 문서에서 꺼내 차례로 글자 그대로 돌린다: 서버 · 워커 띄우기 → 클라이언트
    # 띄우기 → 워커 SIGTERM · 클라이언트 SIGINT → stop-all. rcm 은 가짜다. data_dir 는 `/tmp`
    # 밖(`outside_tmp`)이라 tmp_path 가 `/tmp` 아래인 CI 에서도 건너뛰지 않고 돈다(잡 4380 뒤).
    if shutil.which(shell) is None:
        pytest.skip(f"no {shell}")
    text = TEST_SERVER_CANON.read_text(encoding="utf-8")
    blocks = _signals_blocks(text)
    assert len(blocks) == 4, blocks  # 서버 · 워커, 클라이언트, 신호, 정리
    home = outside_tmp / "home"
    d = home / ".local" / "share" / "rcm-devtest-t"
    (d / "home").mkdir(parents=True)
    worktree = tmp_path / "wt"
    (worktree / ".venv" / "bin").mkdir(parents=True)
    (worktree / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (worktree / "tools").symlink_to(ROOT / "tools")
    rcm = worktree / ".venv" / "bin" / "rcm"
    rcm.write_text(f"#!{sys.executable}\n{FAKE_RCM}", encoding="utf-8")
    rcm.chmod(0o755)
    server_toml, worker_toml = TOML_BLOCK_RE.findall(text)[:2]
    for name, body in (("server.toml", server_toml), ("worker.toml", worker_toml)):
        body = body.replace("/home/example", str(home)).replace("devtest-example", "devtest-t")
        (d / name).write_text(body, encoding="utf-8")
    for name in ("worker.token", "client.token"):
        (d / name).write_text("not-a-secret\n", encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}

    def run(block: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [shell, "-c", _fill(block, worktree)], env=env, capture_output=True, text=True,
            timeout=60,
        )  # fmt: skip

    def pid(name: str) -> int:
        return int((d / f"{name}.pid").read_text(encoding="utf-8"))

    try:
        for block in blocks[:2]:
            out = run(block)
            assert out.returncode == 0, out
        for name in ("server", "worker", "client"):
            assert (d / f"{name}.start").read_text(encoding="utf-8").strip(), name
            _ready(d / f"{name}.log")
        assert f"--config {d}/worker.toml" in _ps(pid("worker"), "command")
        out = run(blocks[2])  # 워커 SIGTERM, 클라이언트 SIGINT
        assert out.returncode == 0 and "BLOCKED" not in out.stdout, out
        assert _ended(pid("worker"), 5) and _ended(pid("client"), 5), out
        assert not _ended(pid("server"), 0.3), "the server got a signal meant for others"
        out = run(blocks[2])  # 같은 신호를 다시 — 이제 그 pid 는 우리 것이 아니다
        assert out.returncode == 3 and out.stdout.splitlines()[-1] == "BLOCKED", out
        out = run(blocks[3])  # stop-all
        assert out.returncode == 0, out
        assert _ended(pid("server"), 5), out
    finally:
        for path in d.glob("*.pid"):
            number = int(path.read_text(encoding="utf-8"))
            command = _ps(number, "command")
            if str(tmp_path) in command or str(outside_tmp) in command:
                os.kill(number, signal.SIGKILL)


def test_the_pr_cleanup_prunes_before_it_deletes_the_branch() -> None:
    # upstream 이 사라진 뒤 `git branch -d` 는 HEAD 와 견준다. 먼저 받아 와야 머지된 가지를 안다.
    text = (SKILLS / "pr" / "SKILL.md").read_text(encoding="utf-8")
    step = text[text.index("Clean up") :]
    assert step.index("git fetch --prune") < step.index("git branch -d <type>")


#: 셈이 든 규칙 — (정본, 이름, 규칙을 적는 문단의 꼴). 문단 = 빈 줄이나 목록 항목 머리로 나뉜
#: 덩어리 · 표의 한 행. 목록 전체를 한 덩어리로 보면 다른 항목의 「three times」(같은 게이트
#: 실패)가 UNSOUND 와 한 덩어리에 들어 거짓 빨강이 난다.
#: 셈 — 숫자 낱말(영어 · 한국어)과 숫자. 「first · second · two」는 넣지 않는다. 「the first
#: review」 「From the second review on」 같은 절차 말이 규칙으로 잡힌다.
_COUNT = (
    r"\b(?:twice|thrice|three|four|five|six|seven|eight|nine|ten"
    r"|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|in a row)\b"
    r"|\b\d+\s*(?:times|x|rounds?|reviews?)\b|\b\d+(?:st|nd|rd|th)\b"
    r"|(?:두|세|네|다섯|여섯|일곱|[2-9])\s*번|연달아|연속"
)
#: 사람에게 올리는 말. 이 말 없이 「UNSOUND 가 일곱 번 나왔다」고 하면 규칙이 아니라 내력이다
#: (`scenario-trace.md` 「Re-reviews」 첫 문단).
_ESCALATE = r"\b(?:owner|person|people|human|escalat\w*|stops?)\b|사람|오너|멈"
ESCALATION_RE = re.compile(rf"(?=.*\bUNSOUND\b)(?=.*(?:{_COUNT}))(?=.*(?:{_ESCALATE}))", re.S)
COUNTED_RULES = (
    (
        AGENTS / "isolated-verifier.md",
        "plan-to-test escalation",
        ESCALATION_RE,
        # 정본의 그 문단은 두 조건을 다 적는다: 같은 행이 두 번 연달아 · 다섯 번째.
        (re.compile(r"\btwice in a row\b"), re.compile(r"\bfifth\b")),
    ),
    (
        SKILLS / "_shared" / "edge-scope.md",
        "live lane cap",
        re.compile(r"(?=.*\blive lane\b)(?=.*\b(five|5)\b)", re.S | re.I),
        (re.compile(r"\bat most five rows per PR\b"),),
    ),
)


def _paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n|\n(?=[ \t]*(?:\d+\.|[-*]|\|) )", text)
    return [p for p in parts if p.strip()]


@pytest.mark.parametrize(
    ("canon", "name", "pattern", "musts"), COUNTED_RULES, ids=[n for _, n, _, _ in COUNTED_RULES]
)
def test_counted_rules_are_written_only_in_their_canon(
    canon: Path, name: str, pattern: re.Pattern[str], musts: tuple[re.Pattern[str], ...]
) -> None:
    paragraphs = _paragraphs(canon.read_text(encoding="utf-8"))
    stated = [p for p in paragraphs if pattern.match(p) and all(m.search(p) for m in musts)]
    assert stated, f"{_rel(canon)} no longer states the {name} ({[m.pattern for m in musts]})"
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
    ("text", "caught"),
    [
        ("still UNSOUND twice in a row: the owner decides", True),
        ("the fifth UNSOUND goes to the owner", True),
        ("UNSOUND four times: escalate", True),
        ("UNSOUND 3 times, then stop", True),
        ("the 4th UNSOUND goes to a person", True),
        ("UNSOUND 이 세 번이면 사람에게 올린다", True),
        ("UNSOUND 다섯 번째면 오너에게", True),
        # 셈이 없다 · 사람에게 올리는 말이 없다(내력) · 절차의 「second」.
        ("`UNSOUND` sends the tests back to a new lead round", False),
        ("came back `UNSOUND` seven times running while its WEAK count went 8, 11", False),
        ("From the second review on, after an `UNSOUND`, the owner reads it", False),
    ],
)
def test_the_escalation_pattern_reads_each_form(text: str, caught: bool) -> None:
    assert bool(ESCALATION_RE.match(text)) is caught


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
