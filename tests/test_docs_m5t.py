"""문서 문면 잠금(M5t-a PR6) — README 두 언어의 처음 설정 절, 사용 안내의 처음 설정 절 · setup ·
doctor, 툴체인 카탈로그, CHANGELOG (명세 docs/m5t-workplan.md §5 PR6 행 · §6 #11).

README 의 「First run」 절은 입구다. 역할 표, 가장 짧은 길, 사용 안내로 가는 링크, 운영 rcm 이 도는
머신이면 시험 설치부터 읽으라는 한 줄만 둔다. 단계별 차례(한 머신 → 머신 더 → 도는 설치 옆의 시험
설치 → 되돌리기)는 사용 안내의 「Getting started」 절에 있고, 처음 쓰는 사람이 그 절만 보고 한
머신에서 역할을 고르고 `rcm setup` → 서비스 설치 → 토큰 → `rcm doctor` · `rcm check` → 첫 잡까지
가야 한다. 이 시험은 그 절에 단계가 다 있는지, 두 거울이 같은 단계를 같은 번호로 말하는지, 링크가
가리키는 절이 정말 있는지를 본다.
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


# ── README: 처음 설정 절은 입구다 ─────────────────────────────────────────────

#: 사용 안내의 처음 설정 절 — 단계별 차례는 README 가 아니라 여기 산다
#: (docs/documentation.md 「README」: README 는 입구, 한 화면을 넘는 절은 docs/ 로)
GETTING_STARTED = {USAGE: "Getting started", USAGE_KO: "처음 설정"}
#: 그 절의 하위 절 — 언어마다 제목이 다르니 차례로 짝짓는다
SUBSECTIONS = {
    USAGE: [
        "On one machine",
        "More machines",
        "A test install next to one that runs",
        "Undo and uninstall",
    ],
    USAGE_KO: ["한 머신에서", "머신 더 붙이기", "도는 설치 옆의 시험 설치", "되돌리기와 제거"],
}
GETTING_STARTED_LINK = {
    README: "docs/usage.md#getting-started-pick-a-role-and-set-it-up",
    README_KO: "docs/usage.ko.md#처음-설정-역할을-고르고-설정한다",
}
TEST_INSTALL_LINK = {
    README: "docs/usage.md#a-test-install-next-to-one-that-runs",
    README_KO: "docs/usage.ko.md#도는-설치-옆의-시험-설치",
}
USAGE_GUIDES = [pytest.param(USAGE, id="en"), pytest.param(USAGE_KO, id="ko")]
#: README 의 처음 설정 절이 넘지 않을 줄 수 — 역할 표 · 가장 짧은 길 · 링크면 이 안에 든다
FIRST_RUN_MAX_LINES = 40


@pytest.mark.parametrize("path", READMES)
def test_readme_first_run_sits_between_install_and_build_machine(path: Path):
    text = read(path)
    heads = [m.group(1) for m in re.finditer(r"^## ([^\n]+)$", text, re.M)]
    names = [h.split(" (")[0].split(":")[0] for h in heads]
    assert FIRST_RUN in names, heads
    assert names.index("Install") < names.index(FIRST_RUN) < names.index("Build machine"), heads


#: README 처음 설정 절의 가장 짧은 길 — 역할 표와 명령 서너 줄
README_FIRST_RUN_MUST = [
    ("four roles", r"`client`.*`worker`.*`coordinator`.*`coordinator-worker`"),
    ("setup, one machine", r"rcm setup --role coordinator-worker --bind \S+ --yes"),
    ("the printed lines", r"`next —`"),
    ("the printed token line", r"`tokens —`"),
    ("client on the same machine", r"rcm setup --role client --server \S+ --yes"),
    ("the token for the client", r"RCM_TOKEN"),
    ("doctor", r"rcm doctor --role coordinator-worker"),
    ("check", r"rcm check"),
    ("first job", r"rcm run ok"),
    ("a machine that already runs rcm", r"already runs rcm as a service|이미 서비스로 도는"),
]


@pytest.mark.parametrize("path", READMES)
@pytest.mark.parametrize(
    ("what", "pattern"), README_FIRST_RUN_MUST, ids=[w for w, _ in README_FIRST_RUN_MUST]
)
def test_readme_first_run_has(path: Path, what: str, pattern: str):
    sec = flat(h2(read(path), FIRST_RUN))
    assert re.search(pattern, sec, re.S), f"{path.name} '## {FIRST_RUN}' lacks {what}: /{pattern}/"


@pytest.mark.parametrize("path", READMES)
def test_readme_first_run_links_the_usage_guide_walkthrough_and_its_test_install(path: Path):
    sec = h2(read(path), FIRST_RUN)
    for link in (GETTING_STARTED_LINK[path], TEST_INSTALL_LINK[path]):
        assert f"]({link})" in sec, f"{path.name} '## {FIRST_RUN}' does not link {link}"


@pytest.mark.parametrize("path", READMES)
def test_readme_first_run_is_a_front_door(path: Path):
    """단계별 차례 · 머신 더 붙이기 · 시험 설치 · 되돌리기는 사용 안내에 있다. README 절이 다시
    자라 그 차례를 베끼면 두 곳이 어긋난다(batch-1 의 577줄 README)."""
    sec = h2(read(path), FIRST_RUN)
    assert not re.search(r"^### ", sec, re.M), f"{path.name}: '## {FIRST_RUN}' grew subsections"
    lines = sec.strip("\n").count("\n") + 1
    assert lines <= FIRST_RUN_MAX_LINES, f"{path.name}: '## {FIRST_RUN}' is {lines} lines"


@pytest.mark.parametrize("path", READMES)
def test_every_readme_link_to_a_doc_section_resolves(path: Path):
    """README 가 거는 `docs/…#앵커` 와 쪽 안 `#앵커` 는 모두 있는 제목이어야 한다."""
    text = read(path)
    bad: list[str] = []
    links = re.findall(r"\]\(((?:docs/[\w.-]+\.md)?)#([^)\s]+)\)", text)
    links += re.findall(r'href="((?:docs/[\w.-]+\.md)?)#([^"]+)"', text)
    for target, frag in links:
        doc = ROOT / target if target else path
        if not doc.is_file() or frag not in anchors(doc):
            bad.append(f"{target}#{frag}")
    assert bad == [], bad


def test_readme_mirrors_say_the_same_first_run():
    en = h2(read(README), FIRST_RUN)
    ko = h2(read(README_KO), FIRST_RUN)
    cmds = [rcm_lines(s) for s in (en, ko)]
    assert cmds[0] == cmds[1], cmds
    assert cmds[0], "no rcm command in a code block"
    roles = [re.findall(r"^\| `([a-z-]+)` \|", s, re.M) for s in (en, ko)]
    assert roles[0] == roles[1] == ["client", "worker", "coordinator", "coordinator-worker"], roles


def test_readme_mirrors_have_the_same_h2_headings():
    heads = [re.findall(r"^## ([^\n]+)$", read(p), re.M) for p in (README, README_KO)]
    assert heads[0] == heads[1], heads


def test_readme_nav_line_links_the_first_run_and_the_test_install():
    """README 첫 화면(머리의 길잡이 줄)에서 처음 설정과 시험 설치로 갈 수 있다 — 운영 rcm 이 도는
    머신에서 따라 하는 사람이 운영 자리에 setup 을 치기 전에 시험 설치 길을 본다."""
    for path in (README, README_KO):
        nav = read(path).split('<p align="center">')[3]
        assert 'href="#first-run' in nav, f"{path.name}: the nav line does not link the first run"
        assert f'href="{TEST_INSTALL_LINK[path]}"' in nav, f"{path.name}: no test install link"


def test_readme_status_line_names_the_current_minor_release():
    """Status 줄이 v0.2.3 에 여덟 릴리스 동안 멈춰 있었다. 지금 판이거나 바로 앞 판이어야 한다."""
    m = re.search(
        r'^__version__ = "(\d+)\.(\d+)\.', read(ROOT / "src/remote_ci_monitor/__init__.py"), re.M
    )
    assert m, "no __version__"
    major, minor = int(m.group(1)), int(m.group(2))
    ok = {f"v{major}.{minor}", f"v{major}.{max(minor - 1, 0)}"}
    for path in (README, README_KO):
        line = next((ln for ln in read(path).splitlines() if ln.startswith("Status:")), "")
        named = set(re.findall(r"\bv\d+\.\d+", line))
        assert named & ok, f"{path.name}: Status line {line!r} names none of {sorted(ok)}"


# ── 사용 안내의 처음 설정 절 ──────────────────────────────────────────────────

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


def getting_started(path: Path) -> str:
    return h2(read(path), GETTING_STARTED[path])


@pytest.mark.parametrize("path", USAGE_GUIDES)
@pytest.mark.parametrize(("what", "pattern"), FIRST_RUN_MUST, ids=[w for w, _ in FIRST_RUN_MUST])
def test_usage_getting_started_has(path: Path, what: str, pattern: str):
    sec = flat(getting_started(path))
    assert re.search(pattern, sec, re.S), f"{path.name} getting started lacks {what}: /{pattern}/"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_usage_getting_started_sits_before_section_1_and_is_in_the_contents(path: Path):
    text = read(path)
    gs = re.search(rf"^## {re.escape(GETTING_STARTED[path])}[^\n]*$", text, re.M)
    one = re.search(r"^## 1\. ", text, re.M)
    assert gs and one and gs.start() < one.start(), path.name
    contents = next(p for p in re.split(r"\n\s*\n", text) if p.startswith("**"))
    assert f"](#{slug(gs.group(0)[3:])})" in contents, f"{path.name}: the contents skip it"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_usage_getting_started_has_the_four_subsections(path: Path):
    """한 머신 · 머신 더 · 도는 설치 옆의 시험 설치 · 되돌리기, 이 차례로."""
    got = re.findall(r"^### ([^\n]+)$", getting_started(path), re.M)
    assert got == SUBSECTIONS[path], got


#: 처음 설정 절이 가리켜야 하는 깊은 문서의 절(사용 안내에서 본 상대 경로)
FIRST_RUN_LINKS = [
    "operating.md#set-a-machine-up-rcm-setup",
    "operating.md#check-a-machine-before-you-use-it-rcm-doctor",
    "configuration.md#a-server-that-only-hands-out-jobs-lanes--0",
    "configuration.md#keeping-a-preset-on-the-server-local_only",
    "configuration.md#install-hints-tools",
]


@pytest.mark.parametrize("path", USAGE_GUIDES)
@pytest.mark.parametrize("link", FIRST_RUN_LINKS)
def test_usage_getting_started_links_into_the_docs(path: Path, link: str):
    assert f"]({link})" in getting_started(path), f"{path.name} does not link {link}"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_every_usage_getting_started_link_resolves(path: Path):
    bad: list[str] = []
    for target, frag in re.findall(
        r"\]\(((?:\.\./)?[\w.-]*\.md)?#([^)\s]+)\)", getting_started(path)
    ):
        doc = (path.parent / target).resolve() if target else path
        if not doc.is_file() or frag not in anchors(doc):
            bad.append(f"{target}#{frag}")
    assert bad == [], bad


def test_usage_mirrors_say_the_same_getting_started():
    en, ko = getting_started(USAGE), getting_started(USAGE_KO)
    for name_en, name_ko in zip(SUBSECTIONS[USAGE], SUBSECTIONS[USAGE_KO], strict=True):
        got = [steps(h3(en, name_en)), steps(h3(ko, name_ko))]
        assert got[0] == got[1], (name_en, got)
    assert len(steps(h3(en, ONE_MACHINE))) >= 6
    cmds = [rcm_lines(s) for s in (en, ko)]
    assert cmds[0] == cmds[1], cmds
    assert cmds[0], "no rcm command in a code block"


#: 스토어 탭의 화면 설명은 README 에서 사용 안내 10절로 옮겼다 — 옮기다 잃지 않았는지
STORE_WALKTHROUGH = [
    "n of m secrets set · k verified",
    "#/store/<name>/status",
    "next_version_hint",
    "#/store/<name>/v/<id>",
    "800 ms",
    "version_ttl_hours",
    "Rehearsal (no upload)",
    "unsafe_release_type",
    "plan_max_age_minutes",
]


@pytest.mark.parametrize("path", USAGE_GUIDES)
@pytest.mark.parametrize("needle", STORE_WALKTHROUGH)
def test_usage_section_10_carries_the_store_tab_walkthrough(path: Path, needle: str):
    assert needle in numbered(read(path), 10), f"{path.name} §10 lacks {needle!r}"


# ── 사용 안내 두 언어 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [USAGE, USAGE_KO], ids=["en", "ko"])
def test_usage_section_2_offers_setup_and_doctor(path: Path):
    sec = flat(numbered(read(path), 2))
    assert re.search(r"rcm setup --role coordinator-worker", sec), path.name
    assert re.search(r"rcm doctor --role coordinator-worker", sec), path.name
    anchor = GETTING_STARTED_LINK[README if path == USAGE else README_KO].split("#", 1)[1]
    assert f"](#{anchor})" in sec, f"{path.name} §2 does not point at its own getting started"


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
