"""문서 문면 잠금(M5t-a PR6) — README 두 언어의 처음 설정 절, 사용 안내의 setup · doctor, 툴체인
카탈로그, CHANGELOG (명세 docs/m5t-workplan.md §5 PR6 행 · §6 #11).

README 의 「First run」 절만 보고 처음 쓰는 사람이 한 머신에서 역할을 고르고 `rcm setup` →
서비스 설치 → 토큰 → `rcm doctor` · `rcm check` → 첫 잡까지 가야 한다. 이 시험은 그 절에 단계가 다
있는지, 두 거울이 같은 단계를 같은 번호로 말하는지, 절이 가리키는 문서의 절이 정말 있는지를 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
README_KO = ROOT / "README.ko.md"
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
OPERATING = ROOT / "docs" / "operating.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
TEMPLATE = ROOT / "src" / "remote_ci_monitor" / "templates" / "server.toml"
EXAMPLE = ROOT / "examples" / "server.toml"
CHANGELOG = ROOT / "CHANGELOG.md"

READMES = [pytest.param(README, id="en"), pytest.param(README_KO, id="ko")]
FIRST_RUN = "First run"
ONE_MACHINE = "On one machine"
CATALOG_BEGIN = "# ── Toolchain catalog"
CATALOG_END = "# ── End of the toolchain catalog"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def h2(text: str, prefix: str) -> str:
    """`## <prefix>…` 부터 다음 `## ` 직전까지(`###` 포함)."""
    m = re.search(rf"^##[ \t]+{re.escape(prefix)}[^\n]*$", text, re.M)
    assert m, f"no '## {prefix}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def h3(text: str, prefix: str) -> str:
    m = re.search(rf"^###[ \t]+{re.escape(prefix)}[^\n]*$", text, re.M)
    assert m, f"no '### {prefix}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^##+ ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def numbered(text: str, number: int) -> str:
    """사용 안내의 `## <n>. …` 절."""
    m = re.search(rf"^## {number}\. [^\n]*$", text, re.M)
    assert m, f"no '## {number}.' section"
    rest = text[m.end() :]
    nxt = re.search(r"^## \d+\. ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def steps(text: str) -> list[str]:
    return re.findall(r"^(\d+)\. ", text, re.M)


def rcm_lines(text: str) -> list[str]:
    """코드 블록 안 `rcm …` 줄(뒤 주석과 앞의 `NAME=value` 는 뺀다).

    두 거울이 같은 명령을 보이는지 견주는 데 쓴다."""
    out: list[str] = []
    for block in re.findall(r"```sh\n(.*?)```", text, re.S):
        for raw in block.splitlines():
            line = raw.split("  #", 1)[0].strip()
            line = re.sub(r"^(?:[A-Z_]+=\S+\s+)+", "", line)
            if line.startswith("rcm "):
                out.append(line)
    return out


def slug(heading: str) -> str:
    """GitHub 의 제목 앵커 — 소문자, 글자 · 숫자 · `_` · `-` · 공백만 남기고, 공백은 `-`."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    out: set[str] = set()
    fence = False
    for line in read(path).splitlines():
        if line.startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^#{1,6}[ \t]+(.+?)[ \t]*$", line)
        if m:
            out.add(slug(m.group(1)))
    return out


# ── README: 처음 설정 절 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("path", READMES)
def test_readme_first_run_sits_between_install_and_build_machine(path: Path):
    text = read(path)
    heads = [m.group(1) for m in re.finditer(r"^## ([^\n]+)$", text, re.M)]
    names = [h.split(" (")[0].split(":")[0] for h in heads]
    assert FIRST_RUN in names, heads
    assert names.index("Install") < names.index(FIRST_RUN) < names.index("Build machine"), heads


#: 처음 설정 절에 있어야 하는 것 — 명세 §5 PR6 행과 batch-1 in 파일의 목록
FIRST_RUN_MUST = [
    ("four roles", r"`client`.*`worker`.*`coordinator`.*`coordinator-worker`"),
    ("first-run question", r"Set it up now\?"),
    ("setup, one machine", r"rcm setup --role coordinator-worker --bind \S+ --yes"),
    ("setup registers nothing", r"(registers|starts) nothing|등록하지도 띄우지도 않"),
    ("service on macOS", r"launchctl bootstrap"),
    ("service on Linux", r"loginctl enable-linger"),
    ("the printed token line", r"token --config \S+ add "),
    ("client on the same machine", r"rcm setup --role client --server \S+ --yes"),
    ("the token for the client", r"RCM_TOKEN"),
    ("doctor", r"rcm doctor --role coordinator-worker"),
    ("check", r"rcm check"),
    ("first job", r"rcm run ok"),
    ("wait", r"rcm wait --job \S+ --notify"),
    ("where it ran", r"succeeded on "),
    ("ran_on", r"ran_on"),
    ("artifacts", r"artifact|산출물"),
    ("local_only", r"local_only"),
    ("lanes = 0", r"lanes = 0"),
    ("worker", r"rcm setup --role worker --server \S+ --pool \S+ --lanes \S+ --yes"),
    ("worker token in the file", r"worker\.toml"),
    ("worker doctor", r"rcm doctor --role worker"),
    ("production refusal", r"refusing"),
    ("test install: port", r"--port 8790"),
    ("test install: no advertise", r"--no-advertise"),
    ("test install: label", r"--label \S+"),
    ("test install: data dir", r"--data-dir \S+"),
    ("test install: its own config", r"rcm serve --config \S+"),
    ("clear RCM_ from the shell", r"RCM_\*"),
    ("undo", r"to remove:"),
    ("uninstall", r"pipx uninstall remote-ci-monitor"),
]


@pytest.mark.parametrize("path", READMES)
@pytest.mark.parametrize(("what", "pattern"), FIRST_RUN_MUST, ids=[w for w, _ in FIRST_RUN_MUST])
def test_readme_first_run_has(path: Path, what: str, pattern: str):
    sec = flat(h2(read(path), FIRST_RUN))
    assert re.search(pattern, sec, re.S), f"{path.name} '## {FIRST_RUN}' lacks {what}: /{pattern}/"


#: 처음 설정 절이 가리켜야 하는 깊은 문서의 절
FIRST_RUN_LINKS = [
    "docs/operating.md#set-a-machine-up-rcm-setup",
    "docs/operating.md#check-a-machine-before-you-use-it-rcm-doctor",
    "docs/configuration.md#a-server-that-only-hands-out-jobs-lanes--0",
    "docs/configuration.md#keeping-a-preset-on-the-server-local_only",
    "docs/configuration.md#install-hints-tools",
]


@pytest.mark.parametrize("path", READMES)
@pytest.mark.parametrize("link", FIRST_RUN_LINKS)
def test_readme_first_run_links_into_the_docs(path: Path, link: str):
    assert f"]({link})" in h2(read(path), FIRST_RUN), f"{path.name} does not link {link}"


@pytest.mark.parametrize("path", READMES)
def test_every_readme_link_to_a_doc_section_resolves(path: Path):
    """README 가 거는 `docs/…#앵커` 와 쪽 안 `#앵커` 는 모두 있는 제목이어야 한다."""
    text = read(path)
    bad: list[str] = []
    for target, frag in re.findall(r"\]\(((?:docs/[\w.-]+\.md)?)#([^)\s]+)\)", text):
        doc = ROOT / target if target else path
        if not doc.is_file() or frag not in anchors(doc):
            bad.append(f"{target}#{frag}")
    for frag in re.findall(r'href="#([^"]+)"', text):
        if frag not in anchors(path):
            bad.append(f"#{frag}")
    assert bad == [], bad


def test_readme_mirrors_say_the_same_first_run():
    en = h2(read(README), FIRST_RUN)
    ko = h2(read(README_KO), FIRST_RUN)
    sub = [re.findall(r"^### ([^\n]+)$", s, re.M) for s in (en, ko)]
    assert sub[0] == sub[1], sub  # 같은 하위 절, 같은 순서
    assert ONE_MACHINE in [s.split(" (")[0] for s in sub[0]], sub[0]
    one = [steps(h3(s, ONE_MACHINE)) for s in (en, ko)]
    assert one[0] == one[1], one
    assert len(one[0]) >= 6, one[0]
    cmds = [rcm_lines(s) for s in (en, ko)]
    assert cmds[0] == cmds[1], cmds
    assert cmds[0], "no rcm command in a code block"


def test_readme_mirrors_have_the_same_h2_headings():
    heads = [re.findall(r"^## ([^\n]+)$", read(p), re.M) for p in (README, README_KO)]
    assert heads[0] == heads[1], heads


def test_readme_nav_line_links_the_first_run():
    for path in (README, README_KO):
        nav = read(path).split('<p align="center">')[3]
        assert 'href="#first-run' in nav, f"{path.name}: the nav line does not link the first run"


# ── 사용 안내 두 언어 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [USAGE, USAGE_KO], ids=["en", "ko"])
def test_usage_section_2_offers_setup_and_doctor(path: Path):
    sec = flat(numbered(read(path), 2))
    assert re.search(r"rcm setup --role coordinator-worker", sec), path.name
    assert re.search(r"rcm doctor --role coordinator-worker", sec), path.name
    assert re.search(r"README(\.ko)?\.md#first-run", sec), path.name


@pytest.mark.parametrize("path", [USAGE, USAGE_KO], ids=["en", "ko"])
def test_usage_section_11_offers_setup_for_a_worker(path: Path):
    sec = flat(numbered(read(path), 11))
    assert re.search(r"rcm setup --role worker --server \S+ --pool \S+ --lanes \S+ --yes", sec)
    assert "rcm doctor --role worker" in sec, path.name
    assert "worker.toml" in sec, path.name


@pytest.mark.parametrize("number", [2, 3, 11])
def test_usage_mirrors_number_the_same_steps(number: int):
    got = [steps(numbered(read(p), number)) for p in (USAGE, USAGE_KO)]
    assert got[0] == got[1], got
    cmds = [rcm_lines(numbered(read(p), number)) for p in (USAGE, USAGE_KO)]
    assert cmds[0] == cmds[1], cmds


# ── 툴체인 카탈로그 · 한계 · 알림 환경변수 ──────────────────────────────────────


def catalog(path: Path) -> str:
    text = read(path)
    assert text.count(CATALOG_BEGIN) == 1 and text.count(CATALOG_END) == 1, path.name
    return text[text.index(CATALOG_BEGIN) : text.index(CATALOG_END)]


def test_catalog_block_is_the_same_in_the_template_and_the_example():
    assert catalog(TEMPLATE) == catalog(EXAMPLE)


def test_configuration_points_at_the_catalog():
    sec = flat(read(CONFIGURATION))
    assert re.search(r"toolchain catalog", sec, re.I), "configuration.md never names the catalog"
    hints = flat(h3(read(CONFIGURATION), "Install hints"))
    assert re.search(r"toolchain catalog", hints, re.I), "the [[tools]] section lacks the catalog"


def test_java_stub_limit_is_written_down():
    assert re.search(r"Java stubs are not detected", flat(read(OPERATING))), "operating.md"
    assert re.search(r"/usr/bin/java is a stub", flat(catalog(TEMPLATE))), "the catalog"


def test_template_notify_comment_lists_rcm_machine():
    m = re.search(r"^# ── Notifications.*?(?=^# \[\[notify\]\])", read(TEMPLATE), re.M | re.S)
    assert m, "no Notifications block in the template"
    assert "RCM_MACHINE" in m.group(0), m.group(0)


def test_template_lanes_comment_names_zero():
    line = next(ln for ln in read(TEMPLATE).splitlines() if ln.startswith("lanes = "))
    assert re.search(r"\b0\b", line.split("#", 1)[1]), line


# ── CHANGELOG ────────────────────────────────────────────────────────────────


def unreleased() -> str:
    text = read(CHANGELOG)
    m = re.search(r"^## \[Unreleased\][^\n]*$", text, re.M)
    assert m, "no [Unreleased]"
    rest = text[m.end() :]
    return rest[: re.search(r"^## \[", rest, re.M).start()]


@pytest.mark.parametrize(
    "pattern",
    [r"README[^\n]*first run|first run[^\n]*README", r"toolchain catalog"],
)
def test_changelog_unreleased_mentions(pattern: str):
    assert re.search(pattern, flat(unreleased()), re.I), f"[Unreleased] lacks /{pattern}/"
