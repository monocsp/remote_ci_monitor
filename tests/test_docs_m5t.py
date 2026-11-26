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


def nav_line(path: Path) -> str:
    """README 머리의 길잡이 줄 — `#install` 링크로 여는 `<p align="center">` 단락. 머리의 단락
    수가 바뀌어도(배지 줄 · 그림) 같은 단락을 찾는다."""
    m = re.search(r'<p align="center">\s*<a href="#install">.*?</p>', read(path), re.S)
    assert m, f"{path.name}: no nav line opening with the #install link"
    return m.group(0)


def test_readme_nav_line_links_the_first_run_and_the_test_install():
    """README 첫 화면(머리의 길잡이 줄)에서 처음 설정과 시험 설치로 갈 수 있다 — 운영 rcm 이 도는
    머신에서 따라 하는 사람이 운영 자리에 setup 을 치기 전에 시험 설치 길을 본다."""
    for path in (README, README_KO):
        nav = nav_line(path)
        assert 'href="#first-run' in nav, f"{path.name}: the nav line does not link the first run"
        assert f'href="{TEST_INSTALL_LINK[path]}"' in nav, f"{path.name}: no test install link"


# ── PR #275 P3: 운영 머신 주의 · 그대로 도는 코드 블록 · 시험 설치 ──────────────────

#: 운영 rcm 이 서비스로 도는 머신이면 이 명령을 돌리지 말라는 줄(두 언어)
RUNS_AS_A_SERVICE = {
    "en": r"If this machine already runs rcm as a service, do not run these commands",
    "ko": r"이 머신에서 rcm 이 이미 서비스로 돈다면 이 명령을 돌리지 않는다",
}
#: 같은 쪽 안에서 본 시험 설치 앵커
TEST_INSTALL_ANCHOR = {
    USAGE: "#a-test-install-next-to-one-that-runs",
    USAGE_KO: "#도는-설치-옆의-시험-설치",
}


def first_paragraph(sec: str) -> str:
    return next(p for p in re.split(r"\n\s*\n", sec) if p.strip())


def lang(path: Path) -> str:
    return "ko" if path.name.endswith(".ko.md") else "en"


@pytest.mark.parametrize("path", READMES)
def test_p3_1_readme_build_machine_opens_with_the_running_service_warning(path: Path):
    """# PR275-P3-1 — 「Build machine (3 commands)」의 `rcm init server` · `rcm token add` ·
    `rcm serve` 는 기본 server.toml 과 기본 data_dir 을 쓴다. 운영 rcm 이 도는 머신에서 서비스가
    아닌 빌드가 그 DB 를 열면 마이그레이션한다(AGENTS.md 「The build machine's own install」).
    그래서 절의 첫 단락이 멈추라고 하고 시험 설치로 보낸다."""
    para = flat(first_paragraph(h2(read(path), "Build machine")))
    assert re.search(RUNS_AS_A_SERVICE[lang(path)], para), f"{path.name}: {para[:160]!r}"
    assert f"]({TEST_INSTALL_LINK[path]})" in para, f"{path.name}: no test install link"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_p3_1_usage_section_2_opens_with_the_running_service_warning(path: Path):
    """# PR275-P3-1 — 사용 안내 2절의 `rcm token add` · `rcm serve` 도 같다."""
    para = flat(first_paragraph(numbered(read(path), 2)))
    assert re.search(RUNS_AS_A_SERVICE[lang(path)], para), f"{path.name}: {para[:160]!r}"
    assert f"]({TEST_INSTALL_ANCHOR[path]})" in para, f"{path.name}: no test install link"


def first_run_block(path: Path) -> list[str]:
    blocks = re.findall(r"```sh\n(.*?)```", h2(read(path), FIRST_RUN), re.S)
    assert len(blocks) == 1, f"{path.name}: '## {FIRST_RUN}' has {len(blocks)} sh blocks"
    return blocks[0].splitlines()


@pytest.mark.parametrize("path", READMES)
def test_p3_2_readme_first_run_block_runs_in_order(path: Path):
    """# PR275-P3-2 — 코드 블록만 따라 해도 된다: 서버 setup 뒤, 클라이언트 setup 앞에 「setup 이
    찍은 `next —` 줄과 `tokens —` 의 첫 줄을 돌린다」 주석 줄이 있다. 없으면 서비스도 토큰도 없이
    `export RCM_TOKEN` 으로 간다."""
    lines = first_run_block(path)
    server = next(i for i, ln in enumerate(lines) if "--role coordinator-worker" in ln)
    client = next(i for i, ln in enumerate(lines) if "--role client" in ln)
    token = next(i for i, ln in enumerate(lines) if "RCM_TOKEN" in ln)
    between = [ln for ln in lines[server + 1 : client] if ln.lstrip().startswith("#")]
    assert any("`next —`" in ln and "`tokens —`" in ln for ln in between), (path.name, lines)
    assert server < client < token, (path.name, lines)


def trial_install(path: Path) -> str:
    return h3(getting_started(path), SUBSECTIONS[path][2])


#: 시험 설치에서 setup 의 `next —` 줄을 돌리지 않는다는 문장(두 언어)
NO_SERVICE_IN_A_TEST = {
    "en": [
        r"Do not run the lines setup prints under `next —` \(the service install\)",
        r"`rcm serve`",
        r"launchd[^.]*every login",
    ],
    "ko": [
        r"setup 이 `next —` 아래에 찍는 줄\(서비스 설치\)은 돌리지 않는다",
        r"`rcm serve`",
        r"launchd[^.]*로그인할 때마다",
    ],
}


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_p3_3_test_install_says_not_to_install_the_service(path: Path):
    """# PR275-P3-3 — 시험 설치는 터미널의 `rcm serve` 로 돈다. setup 이 찍는 서비스 설치 줄을
    따라 하면 launchd 가 로그인마다 시험 서버를 다시 띄운다(operating.md 에만 있던 말)."""
    sec = flat(trial_install(path))
    for pattern in NO_SERVICE_IN_A_TEST[lang(path)]:
        assert re.search(pattern, sec), f"{path.name} test install lacks /{pattern}/"


#: 코드가 환경에서 읽는 RCM_* 이름 — `environ.get("RCM_X")` · `env["RCM_X"]` · `.pop("RCM_X")`
ENV_READ = re.compile(r"""(?:environ|env)(?:\.get\(|\.pop\(|\[)\s*["'](RCM_[A-Z0-9_]+)["']""")
#: 상수로 읽는 것(follow.ENV_MODE)
ENV_READ_BY_CONSTANT = {"RCM_CLIENT_VERSION"}


def env_names_the_code_reads() -> set[str]:
    """`src/remote_ci_monitor` 에서 사용자가 셸에 둘 수 있는 RCM_* 이름. 잡에 건네는 이름 ·
    프리셋 입력(`RCM_INPUT_*`) · 잡 안에서 도는 스킬 템플릿(`skills/`)은 뺀다."""
    pkg = ROOT / "src" / "remote_ci_monitor"
    names = set(ENV_READ_BY_CONSTANT)
    # setup 이 data_dir 을 읽는 변수는 `_DATA_DIRS` 표에서 이름이 나온다(정규식이 못 찾는 꼴)
    from remote_ci_monitor.setup import _DATA_DIRS

    names |= {env for _section, env, _default in _DATA_DIRS.values()}
    for py in pkg.rglob("*.py"):
        if "skills" in py.relative_to(pkg).parts:
            continue
        names |= set(ENV_READ.findall(read(py)))
    return {n for n in names if not n.startswith("RCM_INPUT")}


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_p3_4_test_install_clears_every_rcm_variable_with_a_command(path: Path):
    """# PR275-P3-4 — 「셸의 `RCM_*` 를 지운다」에 명령이 있다: 코드가 읽는 이름을 빠짐없이
    `unset` 하고, `RCM_<SECTION>_<KEY>` 덮어쓰기처럼 이름을 다 셀 수 없는 것은 `env | grep` 줄로
    남은 것을 본다. 토큰은 명령 줄에 두지 않는다(셸 기록에 남는다) — 파일에서 읽는다."""
    sec = trial_install(path)
    code = "\n".join(re.findall(r"```sh\n(.*?)```", sec, re.S))
    unset = next((ln for ln in code.splitlines() if ln.startswith("unset ")), "")
    named = set(unset.split("#", 1)[0].split()[1:])
    missing = sorted(env_names_the_code_reads() - named)
    assert unset and not missing, f"{path.name}: unset line {unset!r} lacks {missing}"
    assert re.search(r"^env \| grep '\^RCM_'", code, re.M), f"{path.name}: no env | grep check"
    assert code.index("unset ") < code.index("rcm setup "), f"{path.name}: unset after setup"
    assert not re.search(r"RCM_TOKEN=<", code), f"{path.name}: a token on the command line"
    assert re.search(r'export RCM_TOKEN="\$\(cat [^)\s]+\)"', code), f"{path.name}: token file"


def test_p3_4_the_code_reads_the_names_the_test_install_clears():
    """# PR275-P3-4 — 위 시험의 이름 목록이 비지 않았는지(정규식이 낡으면 빈 집합이 통과한다)."""
    assert {
        "RCM_CONFIG",
        "RCM_SERVER",
        "RCM_TOKEN",
        "RCM_WORKER_TOKEN",
        "RCM_SERVER_DATA_DIR",
        "RCM_WORKER_DATA_DIR",
    } <= env_names_the_code_reads()


def test_p3_5_the_wait_end_line_example_is_what_the_code_prints():
    """# PR275-P3-5 — 처음 설정 6단계의 끝 줄 예는 `render_text.wait_end_line` 의 꼴이다."""
    from remote_ci_monitor.core.render_text import wait_end_line

    job = {"state": "succeeded", "ran_on": "macmini", "job_seconds": 1, "started_at": "x"}
    line = wait_end_line(job, job_id=1)
    assert line == "#1 succeeded on macmini · 1s", line
    for path in USAGE_GUIDES:
        assert f"`{line}`" in getting_started(path.values[0]), path.values[0].name


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


# ── operating.md: setup 그물 단락 ────────────────────────────────────────────


def setup_net_paragraphs() -> str:
    """operating.md 에서 훅의 setup 그물을 말하는 단락들 — `PreToolUse` 단락부터 「The hook is
    the first line」 단락 끝까지."""
    text = flat(read(OPERATING))
    start = text.index("Claude Code sessions have this wired as a `PreToolUse` hook")
    end = text.index("The *service virtualenv* is the one", start)
    return text[start:end]


def test_operating_says_a_failing_net_refuses():
    """그물이 터지면(내부 예외) 훅은 그 명령을 setup 에 닿는 쪽으로 보아 거절한다(번들 F ·
    PR #274 의 결정)."""
    sec = setup_net_paragraphs()
    assert re.search(
        r"If the net itself fails \(an internal error\), the hook treats the command as one "
        r"that can reach `rcm setup` and refuses it\.",
        sec,
    ), "operating.md does not say what a failing net does"


def test_operating_names_the_net_over_refusal_and_the_way_round():
    """그물은 평범한 명령도 막는다 — 받아들인 비용이다. 단락은 무엇이 막히는지와 우회(글은
    파일로, 긴 사슬은 나눈다)를 말한다. 거둔 설계(번들 F batch-1 · batch-2 의 문장)는 쓰지
    않는다."""
    sec = setup_net_paragraphs()
    assert re.search(r"refuses some ordinary commands too", sec), "no over-refusal sentence"
    assert re.search(r"quoted text next to `\$` or `\(`", sec), "the quoted-text case is missing"
    assert re.search(r"`expect`", sec) and re.search(r"`fd -x`", sec), "no launcher example"
    assert re.search(r"This is accepted", sec), "the cost is not said to be accepted"
    for way in (
        "git commit -F <file>",
        "gh pr create --body-file <file>",
        "gh pr comment --body-file <file>",
    ):
        assert f"`{way}`" in sec, f"the way round lacks {way}"
    assert re.search(r"split long chains into separate commands", sec), "no split advice"
    for withdrawn in ("A launcher counts only where a command stands", "One kind of refusal"):
        assert withdrawn not in sec, f"withdrawn design written down: {withdrawn!r}"


# ── documentation.md: README 은 입구 ─────────────────────────────────────────

DOCUMENTATION = ROOT / "docs" / "documentation.md"


def map_row(name: str) -> str:
    """문서 규칙 「The map」 표에서 `name` 의 줄."""
    m = re.search(rf"^\| `{re.escape(name)}`[^\n]*$", read(DOCUMENTATION), re.M)
    assert m, f"documentation.md map has no row for {name}"
    return m.group(0)


def test_documentation_sends_the_first_setup_to_usage_getting_started():
    """README 줄은 입구라고 하고 처음 설정의 단계별 안내를 사용 안내 「Getting started」 로
    보낸다. 줄 수를 적으면 지어낸 값이 아니다 — 지금 README 가 그 안에 든다."""
    row = map_row("README.md")
    assert "front door" in row, row
    assert re.search(r"step-by-step first setup[^|]*`docs/usage\.md`[^|]*Getting started", row), row
    lines = len(read(README).splitlines())
    for number in re.findall(r"(\d+) lines", row):
        assert int(number) >= lines, f"documentation.md says {number} lines, README has {lines}"


def test_documentation_map_lists_usage_getting_started():
    row = map_row("docs/usage.md")
    assert "Getting started" in row, row
    assert re.search(r"test install", row), row


SMOKE = ROOT / "scripts" / "smoke_install.sh"


def test_install_smoke_checks_the_catalog_in_the_server_toml_it_wrote():
    """설치 스모크(명세 §5 PR6)는 새 venv 에 휠로 깐 rcm 이 `rcm init server` 로 쓴 설정에
    카탈로그가 실렸는지 본다. 템플릿이 휠에서 빠지거나 옛 판이 실리면 스모크가 그 자리에서
    멈춘다."""
    text = read(SMOKE)
    init = text.index('CFG=$("$RCM" init server)')
    check = re.search(
        r"grep -q '\^" + re.escape(CATALOG_BEGIN) + r"' \"\$CFG\" \|\| \{[^}]*exit 1; \}", text
    )
    assert check, "smoke_install.sh does not check the catalog in the written server.toml"
    assert check.start() > init, "the catalog check runs before rcm init server"


# ── CHANGELOG ────────────────────────────────────────────────────────────────


def unreleased() -> str:
    """릴리스 뒤에는 항목이 `[Unreleased]` 가 아니라 그 버전의 절(0.11.0)에 있다 — 다른 문서 잠금
    (tests/test_docs_m5.py `unreleased()`)처럼 「0.1.0 이후 전부」를 본다."""
    from test_docs_m5 import unreleased as since_first_release

    return since_first_release(read(CHANGELOG))


@pytest.mark.parametrize(
    "pattern",
    [r"README[^\n]*first run|first run[^\n]*README", r"toolchain catalog"],
)
def test_changelog_unreleased_mentions(pattern: str):
    assert re.search(pattern, flat(unreleased()), re.I), f"[Unreleased] lacks /{pattern}/"
