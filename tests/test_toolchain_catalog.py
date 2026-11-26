"""서버 설정 템플릿의 툴체인 카탈로그(M5t-a 명세 §4.5 「툴체인 카탈로그」 · §7 TD23).

카탈로그는 휠에 실리는 `templates/server.toml` 의 주석 블록이다. 주석을 풀면 그대로 설정으로 읽혀야
하고(기동 검사), 거기 적힌 도구 이름은 코어 코드에 없어야 한다(PLAN 이식성). 코어가 부르는 도구
이름은 호스트 사실용 허용 목록 한 곳(`hostsample.HOST_FACT_COMMANDS` · `PACKAGE_MANAGERS`)뿐이다.

블록의 약속: `# ── Toolchain catalog` 줄에서 `# ── End of the toolchain catalog` 줄까지. 그 안에서
`# ` 로 시작하는 줄은 설정이고(`# ` 를 지우면 TOML), `##` 로 시작하는 줄은 설명이다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from remote_ci_monitor import doctor, hostsample
from remote_ci_monitor.config import load_server_config, tool_warnings
from remote_ci_monitor.core import setup as core_setup

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "remote_ci_monitor" / "templates" / "server.toml"
EXAMPLE = ROOT / "examples" / "server.toml"
CORE = ROOT / "src" / "remote_ci_monitor"

BEGIN = "# ── Toolchain catalog"
END = "# ── End of the toolchain catalog"
#: rcm 자신이 쓰는 도구 — 코어에 이름이 있어도 된다(rcm 자신의 요구, 명세 §4.5)
RCM_OWN = {"python3", "git"}
#: 명세 §4.5 의 다섯 툴체인 — 카탈로그 프로브의 이름이 하나씩은 있어야 한다
TOOLCHAINS = ("flutter", "xcode", "java", "node", "python")


def catalog_lines(text: str) -> list[str]:
    """시작 줄과 끝 줄 사이(둘 다 뺀다)."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith(BEGIN)]
    ends = [i for i, ln in enumerate(lines) if ln.startswith(END)]
    assert len(starts) == 1, f"the template needs one '{BEGIN}' line, found {len(starts)}"
    assert len(ends) == 1, f"the template needs one '{END}' line, found {len(ends)}"
    assert starts[0] < ends[0], "the catalog ends before it starts"
    return lines[starts[0] + 1 : ends[0]]


def uncomment(line: str) -> str:
    """`# x` → `x`, `#` → 빈 줄, `## 설명` 은 주석 그대로."""
    if line.startswith("##"):
        return line
    if line == "#":
        return ""
    assert line.startswith("# "), f"a catalog line is neither config nor a note: {line!r}"
    return line[2:]


def uncommented_template() -> str:
    """템플릿에서 카탈로그 블록만 주석을 푼 글."""
    text = TEMPLATE.read_text(encoding="utf-8")
    block = catalog_lines(text)
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(BEGIN)) + 1
    lines[start : start + len(block)] = [uncomment(ln) for ln in block]
    return "\n".join(lines) + "\n"


def load_uncommented(tmp_path: Path):
    path = tmp_path / "server.toml"
    path.write_text(uncommented_template(), encoding="utf-8")
    return load_server_config(path, environ={})


def catalog_tool_names(tmp_path: Path) -> set[str]:
    cfg = load_uncommented(tmp_path)
    names = {t.name for t in cfg.tools}
    names |= {Path(p.argv[0]).name for p in cfg.probes}
    return names


# ── TD23: 주석을 풀면 기동 검사를 통과한다 ────────────────────────────────────


def test_td23_catalog_uncommented_passes_the_start_checks(tmp_path: Path):
    # TD23
    cfg = load_uncommented(tmp_path)
    assert cfg.tools, "the catalog has no [[tools]] entry"
    assert cfg.probes, "the catalog has no [[probes]] entry"
    # 안내가 어디에도 안 나오는 이름은 기동 경고다 — 카탈로그에는 하나도 없어야 한다
    assert tool_warnings(cfg) == []
    probe_names = {p.name for p in cfg.probes}
    for family in TOOLCHAINS:
        assert any(n == family or n.startswith(family) for n in probe_names), (
            f"no probe for the {family} toolchain: {sorted(probe_names)}"
        )
    for hint in cfg.tools:
        assert hint.docs.startswith("https://"), f"{hint.name}: docs must be an https link"
        assert hint.install, f"{hint.name}: no install line"


def test_td23_catalog_is_the_same_in_the_template_and_the_example():
    # TD23 — 휠의 템플릿과 sdist 의 예시가 같은 카탈로그를 싣는다
    assert TEMPLATE.read_bytes() == EXAMPLE.read_bytes()
    assert catalog_lines(EXAMPLE.read_text(encoding="utf-8"))


def test_td23_core_code_names_no_catalog_tool(tmp_path: Path):
    # TD23 — 카탈로그의 도구 이름은 코어 코드(문자열 상수)에 없다. 주석과 docstring 은 글이다
    names = catalog_tool_names(tmp_path) - RCM_OWN
    assert names, "the catalog names no tool"
    hits: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        if "skills" in path.relative_to(CORE).parts:
            continue  # 프로젝트에 복사해 주는 스킬 파일이다 — 코어가 아니다
        for value, lineno in string_constants(path):
            for name in names:
                if re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", value):
                    hits.append(f"{path.relative_to(ROOT)}:{lineno}: {name}")
    assert hits == [], hits


def test_td23_host_fact_allowlist_lives_in_one_place():
    # TD23 — 허용 목록은 hostsample 한 곳에서 정의하고, 그 이름은 거기서만 상수로 나온다
    defined: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for t in targets:
                if isinstance(t, ast.Name) and t.id in ("HOST_FACT_COMMANDS", "PACKAGE_MANAGERS"):
                    defined.append(f"{path.relative_to(CORE)}:{t.id}")
    assert sorted(defined) == [
        "hostsample.py:HOST_FACT_COMMANDS",
        "hostsample.py:PACKAGE_MANAGERS",
    ], defined
    allowed = {argv[0] for argv in hostsample.HOST_FACT_COMMANDS.values()}
    allowed |= set(hostsample.PACKAGE_MANAGERS)
    stray: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        rel = path.relative_to(CORE)
        if rel.parts[0] == "skills" or rel == Path("hostsample.py"):
            continue
        for value, lineno in string_constants(path, skip_dict_keys=True):
            if value in allowed:
                stray.append(f"{rel}:{lineno}: {value}")
    assert stray == [], stray
    # doctor 의 rcm 자신용 설치 안내는 같은 패키지 관리자 목록을 키로 쓴다
    for need, hints in doctor.INSTALL.items():
        extra = set(hints) - {"darwin", "any"} - set(hostsample.PACKAGE_MANAGERS)
        assert not extra, f"doctor.INSTALL[{need!r}] keys outside PACKAGE_MANAGERS: {extra}"


def test_setup_still_writes_a_server_toml_from_the_template_with_the_catalog(tmp_path: Path):
    """`rcm setup` 은 템플릿의 `bind` · `port` · `data_dir` · `lanes` · `# advertise` 줄을
    정확히 한 줄씩 찾아 바꾼다 — 카탈로그가 그런 줄을 더하면 setup 이 깨진다."""
    text = core_setup.server_toml(
        TEMPLATE.read_text(encoding="utf-8"),
        bind="127.0.0.1",
        port=8790,
        data_dir=str(tmp_path / "data"),
        lanes=1,
        advertise=False,
    )
    path = tmp_path / "server.toml"
    path.write_text(text, encoding="utf-8")
    cfg = load_server_config(path, environ={})
    assert (cfg.server.port, cfg.server.lanes) == (8790, 1)
    assert catalog_lines(text), "setup dropped the catalog"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def string_constants(path: Path, *, skip_dict_keys: bool = False) -> list[tuple[str, int]]:
    """모듈의 문자열 상수(docstring 은 뺀다). `skip_dict_keys` 면 dict 의 키도 뺀다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        if skip_dict_keys and isinstance(node, ast.Dict):
            skip.update(id(k) for k in node.keys if k is not None)
    return [
        (node.value, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
    ]


@pytest.mark.parametrize("line", ['# name = "x"', "#", "## a note"])
def test_uncomment_rule(line: str):
    assert uncomment(line) in ('name = "x"', "", "## a note")
