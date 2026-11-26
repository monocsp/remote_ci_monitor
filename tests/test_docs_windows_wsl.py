"""문서 잠금 — Windows(WSL) 길과 여러 서버 가운데 고르기 (dev-lite 카드 WW, 2026-11-26).

격리 에이전트 넷이 「Windows 에서 쓰면 잘 연결되나」, 「같은 망의 다른 컴퓨터를 찾아 붙나」 를
따라가 보고 찾은 것을 문서에 적었다. 이 파일은 그 글이 남아 있는지, 두 거울(usage.md ·
usage.ko.md)이 같은 단계를 같은 번호로 말하는지, 문서가 인용하는 안내 문구가 코드의 문구와
같은지(코드에서 읽어 견준다) 본다.

- WW4 사용 안내 3절의 「Windows (WSL)」: WSL2 Ubuntu 의 Python, pipx, `rcm setup --role client`,
  NAT 망과 mirrored 망, Tailscale, 저장소 자리와 CRLF, `--notify`, 네이티브 Windows 의 한 줄.
- WW5 README 두 언어: PEP 668 배포판의 pipx 한 줄, 「Windows is out of scope」 문장의 새 절 링크.
- WW6 여러 서버: 클라이언트는 고르지 않고 멈춘다, `--server` · `client.toml` · `server_name` 으로
  하나를 적는다, `rcm setup --role client` 는 네트워크를 뒤지지 않는다, `advertise_name` 은
  server.toml 에 적는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remote_ci_monitor import cli
from test_docs_m5t import anchors, flat, numbered, rcm_lines, steps

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
README_KO = ROOT / "README.ko.md"
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
CLI_SRC = ROOT / "src" / "remote_ci_monitor" / "cli.py"
SETUP_SRC = ROOT / "src" / "remote_ci_monitor" / "setup.py"

USAGE_GUIDES = [pytest.param(USAGE, id="en"), pytest.param(USAGE_KO, id="ko")]
READMES = [pytest.param(README, id="en"), pytest.param(README_KO, id="ko")]
WINDOWS = "Windows (WSL)"
ANCHOR = "windows-wsl"
README_LINK = {README: f"docs/usage.md#{ANCHOR}", README_KO: f"docs/usage.ko.md#{ANCHOR}"}
#: 절 안에서 허용하는 바깥 링크는 Microsoft 의 WSL 네트워크 문서 하나다
MS_NETWORKING = re.compile(r"https://learn\.microsoft\.com/[a-z-]+/windows/wsl/networking")
#: 여러 서버가 보일 때 클라이언트가 멈추며 찍는 문장의 고정된 앞 · 뒤(`_resolve_server`)
SEVERAL_HEAD = "several rcm servers on this network:"
SEVERAL_TAIL = "pick one with --server or client.toml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def windows_section(path: Path) -> str:
    """3절 안의 `### Windows (WSL)` — 다음 제목 직전까지."""
    sec = numbered(read(path), 3)
    m = re.search(rf"^### {re.escape(WINDOWS)}[ \t]*$", sec, re.M)
    assert m, f"{path.name}: no '### {WINDOWS}' in section 3"
    rest = sec[m.end() :]
    nxt = re.search(r"^##+ ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


# ── WW4 사용 안내의 Windows (WSL) ─────────────────────────────────────────────

WINDOWS_MUST = [
    ("Windows 11", r"Windows 11"),
    ("WSL2 Ubuntu", r"WSL2?\b.*Ubuntu|Ubuntu.*WSL2?\b"),
    ("Python floor", r"3\.11\.4"),
    ("22.04 is too old", r"22\.04.*3\.10|3\.10.*22\.04"),
    ("24.04 has 3.12", r"24\.04.*3\.12|3\.12.*24\.04"),
    ("pipx from apt", r"sudo apt install pipx"),
    ("ensurepath", r"pipx ensurepath"),
    ("PEP 668", r"PEP 668"),
    ("pip --user is refused", r"pip install --user pipx"),
    ("client setup", r"rcm setup --role client --server http://192\.0\.2\.\d+:8787 --yes"),
    ("the token", r"RCM_TOKEN"),
    ("IP over .local", r"\.local"),
    ("NAT network", r"NAT"),
    ("mirrored", r"networkingMode=mirrored"),
    ("wslconfig", r"\.wslconfig"),
    ("22H2", r"22H2"),
    ("mirrored is unconfirmed", r"not (been )?(confirmed|verified|tested)|확인(되|하)지 않았다"),
    ("server auto", r'server = "auto"'),
    ("Tailscale on Windows", r"Tailscale"),
    ("not under /mnt/c", r"/mnt/c"),
    ("execute bit", r"execute bit|실행 비트"),
    ("CRLF", r"core\.autocrlf"),
    ("gitattributes", r"\*\.sh text eol=lf"),
    ("notify", r"--notify"),
]


@pytest.mark.parametrize("path", USAGE_GUIDES)
@pytest.mark.parametrize(("what", "pattern"), WINDOWS_MUST, ids=[w for w, _ in WINDOWS_MUST])
def test_ww4_usage_windows_section_has(path: Path, what: str, pattern: str):
    sec = flat(windows_section(path))
    assert re.search(pattern, sec, re.S), f"{path.name} {WINDOWS} lacks {what}: /{pattern}/"


def test_ww4_usage_mirrors_number_the_same_windows_steps():
    got = [steps(windows_section(p)) for p in (USAGE, USAGE_KO)]
    assert got[0] == got[1], got
    assert len(got[0]) >= 5, got
    cmds = [rcm_lines(windows_section(p)) for p in (USAGE, USAGE_KO)]
    assert cmds[0] == cmds[1], cmds
    assert cmds[0], "no rcm command in a code block"


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ww4_usage_windows_section_quotes_the_native_windows_line_from_the_code(path: Path):
    """네이티브 Windows 에서 찍는 줄이 문서에 글자 그대로 있다 — 코드의 상수에서 읽는다."""
    assert cli.NATIVE_WINDOWS_HINT in windows_section(path), path.name


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ww4_usage_windows_section_quotes_the_wsl_hint_from_the_code(path: Path):
    sec = flat(windows_section(path))
    assert cli.DISCOVER_ADDRESS_HINT in sec, path.name
    assert cli.WSL_MULTICAST_HINT in sec, path.name


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ww4_usage_windows_section_links_only_the_microsoft_networking_page(path: Path):
    """명령과 예시 주소 안의 URL 은 링크가 아니다 — 마크다운 링크 `](…)` 만 센다."""
    links = re.findall(r"\]\((https?://[^)\s]+)\)", windows_section(path))
    assert links, f"{path.name}: no link to the WSL networking page"
    assert all(MS_NETWORKING.fullmatch(u) for u in links), links


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_ww4_usage_windows_section_uses_documentation_addresses_only(path: Path):
    """공개 레포 — 예시 주소는 문서용 대역(192.0.2.x)만 쓴다."""
    ips = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", windows_section(path))
    assert ips, f"{path.name}: no example address"
    assert all(ip.startswith("192.0.2.") for ip in ips), ips


def test_ww4_the_native_windows_line_names_the_section_that_exists():
    """한 줄이 가리키는 「docs/usage.md «Windows (WSL)»」 가 실제 제목이다."""
    assert f"docs/usage.md «{WINDOWS}»" in cli.NATIVE_WINDOWS_HINT
    assert ANCHOR in anchors(USAGE) and ANCHOR in anchors(USAGE_KO)


# ── WW5 README ───────────────────────────────────────────────────────────────


def install(path: Path) -> str:
    text = read(path)
    m = re.search(r"^## Install[ \t]*$", text, re.M)
    assert m, f"{path.name}: no '## Install'"
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


@pytest.mark.parametrize("path", READMES)
def test_ww5_readme_install_names_apt_pipx_on_pep_668_distributions(path: Path):
    sec = flat(install(path))
    assert "python3 -m pip install --user pipx" in sec, path.name  # 옛 줄은 그대로 둔다
    assert "sudo apt install pipx" in sec, path.name
    assert "PEP 668" in sec, path.name
    assert re.search(r"Ubuntu 23\.04", sec) and re.search(r"Debian 12", sec), path.name


@pytest.mark.parametrize("path", READMES)
def test_ww5_readme_out_of_scope_sentence_links_the_windows_section(path: Path):
    text = flat(read(path))
    m = re.search(r"Windows (is out of scope|는 범위 밖)[^.]*\.", text)
    assert m, f"{path.name}: no 'Windows is out of scope' sentence"
    tail = text[m.start() : m.start() + 400]
    assert f"]({README_LINK[path]})" in tail, f"{path.name}: {tail!r}"


# ── WW6 여러 서버 가운데 고르기 ───────────────────────────────────────────────


def test_ww6_the_quoted_several_servers_message_is_the_codes():
    """문서가 인용하는 앞 · 뒤가 `_resolve_server` 의 문장 그대로다."""
    src = read(CLI_SRC)
    assert SEVERAL_HEAD in src and f"— {SEVERAL_TAIL}" in src


SEVERAL_MUST = [
    ("the message", re.escape(SEVERAL_HEAD) + r" … — " + re.escape(SEVERAL_TAIL)),
    ("the client does not pick", r"does not pick|고르지 않"),
    ("--server", r"--server"),
    ("client.toml server", r"client\.toml"),
    ("worker server_name", r"server_name"),
    (
        "setup does not search",
        r"rcm setup --role client.{0,40}(does not|never) (search|look)"
        r"|rcm setup --role client.{0,60}(찾지|뒤지지) 않",
    ),
    ("server auto", r'server = "auto"'),
    ("advertise_name in server.toml", r"advertise_name.*server\.toml|server\.toml.*advertise_name"),
]


def several_paragraph(path: Path) -> str:
    """3절에서 여러 서버를 말하는 문단."""
    sec = numbered(read(path), 3)
    paras = [p for p in re.split(r"\n\s*\n", sec) if SEVERAL_HEAD in p]
    assert len(paras) == 1, f"{path.name}: {len(paras)} paragraphs quote {SEVERAL_HEAD!r}"
    return flat(paras[0])


@pytest.mark.parametrize("path", USAGE_GUIDES)
@pytest.mark.parametrize(("what", "pattern"), SEVERAL_MUST, ids=[w for w, _ in SEVERAL_MUST])
def test_ww6_usage_section_3_says_how_to_pick_one_of_several(path: Path, what: str, pattern: str):
    assert re.search(pattern, several_paragraph(path)), f"{path.name} lacks {what}: /{pattern}/"


def client_files(path: Path) -> str:
    text = read(path)
    m = re.search(r"^## Client and worker files[ \t]*$", text, re.M)
    assert m, "no '## Client and worker files'"
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    return flat(rest[: nxt.start()] if nxt else rest)


@pytest.mark.parametrize(("what", "pattern"), SEVERAL_MUST, ids=[w for w, _ in SEVERAL_MUST])
def test_ww6_configuration_client_files_say_how_to_pick_one_of_several(what: str, pattern: str):
    assert re.search(pattern, client_files(CONFIGURATION)), f"lacks {what}: /{pattern}/"


def test_ww6_setup_really_refuses_auto_and_has_no_advertise_name_flag():
    """문서의 두 말이 코드와 맞는다 — setup 은 `server = "auto"` 를 쓰지 않고 `--server` 가
    필요하다, `advertise_name` 을 받는 setup 플래그는 없다."""
    setup_src = read(SETUP_SRC)
    assert 'setup does not write server = "auto"' in setup_src
    assert "--advertise-name" not in read(CLI_SRC) + setup_src
