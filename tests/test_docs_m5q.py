"""M5q PR A 문서 잠금 — 클라이언트가 서버 버전을 따라간다.

잠그는 글자는 `docs/m5q-test-scenarios-c.md` 「문서 잠금」의 표다.

mutcheck 의 복사본(`src` · `tests` · `pyproject.toml`)에는 문서가 없으므로 코드 시험과 나눈다.
실패 문구는 빠진 낱말과 파일을 말한다(CE13).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_docs_m5 import paragraphs, read, unreleased

ROOT = Path(__file__).resolve().parents[1]
OPERATING = ROOT / "docs" / "operating.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
README = ROOT / "README.md"
README_KO = ROOT / "README.ko.md"
CHANGELOG = ROOT / "CHANGELOG.md"
MUTCHECK = ROOT / "scripts" / "mutcheck.py"
FOLLOW_HEADING = "Keeping clients on the server's version"


def section(text: str, heading: str) -> str:
    """`## <heading>` 부터 다음 `## ` 앞까지(`###` 는 안에 든다)."""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.M)
    assert m, f"no '## {heading}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def one_paragraph(text: str, *words: str) -> bool:
    return any(all(w in flat(p) for w in words) for p in paragraphs(text))


def one_sentence(text: str, *words: str) -> bool:
    for sentence in re.split(r"(?<=[.!?。])\s+|\n\s*\n", flat(text)):
        if all(w in sentence for w in words):
            return True
    return False


@pytest.fixture(scope="module")
def follow_section() -> str:
    return section(read(OPERATING), FOLLOW_HEADING)


# ── docs/operating.md 「Keeping clients on the server's version」 ──


@pytest.mark.parametrize(
    "word",
    [
        # 기존 잠금(test_docs_client_wheel)의 여덟 낱말 — 재작성해도 남는다
        "client_wheel",
        "min_client_version",
        "/client/remote_ci_monitor-",
        "sha256",
        "pip install",
        "rcm check",
        "examples/session/update-client.sh",
        "read_auth",
        # 따라가기
        "RCM_CLIENT_VERSION",
        "--no-follow",
        "client-version",
        "~/.cache/rcm",
        "following server v",
        "not following",
        "follow: on (default)",
        "following (installed v",
    ],
)
def test_operating_follow_section_has(follow_section, word):
    assert word in follow_section, f"docs/operating.md ({FOLLOW_HEADING}): missing {word!r}"


@pytest.mark.parametrize(
    "words",
    [
        # 개발 체크아웃과 빌드 머신의 서비스 venv 는 기본으로 따라가지 않는다
        ("editable", "installed"),
        # 설치본은 부트로더다 — 한 번은 0.8.0 이상이어야 한다(§9 완료 기준 4)
        ("examples/session/update-client.sh", "bootstrap", "0.8.0"),
        # 내려가면 그 버전의 파서가 돈다(§2.7 끝 문단)
        ("usage error", "--no-supersede"),
        # 위험(§4.1 · 결정 169)
        ("~/.ssh", "gh"),
        ("sha256", "man-in-the-middle"),
        (".local", "mDNS"),
        # 끄는 법 셋(§4.3)
        ("export RCM_CLIENT_VERSION=installed", "~/.config/rcm/client-version", "--no-follow"),
    ],
)
def test_operating_follow_section_paragraphs(follow_section, words):
    assert one_paragraph(follow_section, *words), (
        f"docs/operating.md ({FOLLOW_HEADING}): no paragraph with all of {words!r}"
    )


def test_operating_help_is_the_installed_help(follow_section):
    # §2.1 의 2 — `--help` 는 언제나 설치본의 도움말이다
    assert one_sentence(follow_section, "--help", "installed"), (
        "docs/operating.md: no sentence saying --help shows the installed rcm's help"
    )


def test_operating_tailscale_is_the_first_advice(follow_section):
    # §4.3 — Tailscale 이 첫 권고다(끄는 법보다 앞)
    assert "Tailscale" in follow_section, "docs/operating.md: no Tailscale advice"
    assert follow_section.index("Tailscale") < follow_section.index(
        "export RCM_CLIENT_VERSION=installed"
    ), "docs/operating.md: Tailscale must come before turning following off"


def test_operating_security_notes_mention_following():
    notes = section(read(OPERATING), "Security notes")
    bullets = [b for b in re.split(r"\n(?=- )", notes) if b.startswith("- ")]
    hit = [
        b
        for b in bullets
        if "RCM_CLIENT_VERSION" in b and "#keeping-clients-on-the-servers-version" in b
    ]
    assert hit, "docs/operating.md (Security notes): no bullet about following"
    assert any(re.search(r"\b(runs|executes)\b", b) for b in hit), (
        "docs/operating.md (Security notes): the bullet must say the code runs on the session"
    )


# ── docs/configuration.md ──


def test_configuration_client_files_name_the_setting():
    sec = section(read(CONFIGURATION), "Client and worker files")
    for word in (
        "RCM_CLIENT_VERSION",
        "`follow`",
        "`installed`",
        "$XDG_CONFIG_HOME/rcm/client-version",
        "~/.config/rcm/client-version",
        "$XDG_CACHE_HOME/rcm",
        "client.toml",
    ):
        assert word in sec, f"docs/configuration.md (Client and worker files): missing {word!r}"
    order = [
        s
        for s in re.split(r"(?<=[.!?])\s+", flat(sec))
        if "--no-follow" in s and "RCM_CLIENT_VERSION" in s and "client-version" in s
    ]
    assert order, "docs/configuration.md: no sentence with the precedence"
    s = order[0]
    assert s.index("--no-follow") < s.index("RCM_CLIENT_VERSION") < s.rindex("client-version"), s


# ── usage 두 언어 ──


@pytest.mark.parametrize("path", [USAGE, USAGE_KO], ids=lambda p: p.name)
def test_usage_guides_mention_following(path):
    text = read(path)
    for word in (
        "RCM_CLIENT_VERSION",
        "--no-follow",
        "operating.md#keeping-clients-on-the-servers-version",
    ):
        assert word in text, f"{path.name}: missing {word!r}"
    installed = "installed" if path == USAGE else "설치"
    assert one_sentence(text, "--help", installed), (
        f"{path.name}: no sentence saying --help shows the installed help"
    )


# ── README 두 언어 ──


def test_readme_keep_the_client_current_says_it_follows():
    text = read(README)
    m = re.search(r"\*\*Keep the client current\.\*\*(.+?)(?:\n\d+\.|\n\n)", text, re.S)
    assert m, "README.md: no 'Keep the client current' step"
    step = flat(m.group(1))
    assert "follows the server" in step and "RCM_CLIENT_VERSION" in step, step
    assert "update-client.sh" in step, step


def test_readme_ko_keep_the_client_current_says_it_follows():
    text = flat(read(README_KO))
    assert "서버 버전을 따라" in text, "README.ko.md: missing '서버 버전을 따라'"
    assert "RCM_CLIENT_VERSION" in text, "README.ko.md: missing RCM_CLIENT_VERSION"


# ── CHANGELOG ──


def test_changelog_unreleased_has_following():
    text = unreleased(read(CHANGELOG))
    for word in ("RCM_CLIENT_VERSION", "--no-follow", "client-version", "~/.cache/rcm", "runs v"):
        assert word in text, f"CHANGELOG.md [Unreleased]: missing {word!r}"


# ── mutcheck ──


@pytest.mark.parametrize(
    "name",
    [
        "m5q-follow-sha-unchecked",
        "m5q-follow-editable-default-flipped",
        "m5q-follow-boot-loop",
        "m5q-follow-unpack-in-place",
        "m5q-follow-serve-follows",
        "m5q-follow-health-path-unchecked",
        "m5q-client-log-before-auth",
        "m5q-client-log-every-submit",
        "m5q-client-log-unchecked-agent",
        "m5q-version-second-line-always",
        "m5q-check-row-drops-follow-tail",
    ],
)
def test_mutcheck_lists_the_m5q_mutants(name):
    assert f'name="{name}"' in read(MUTCHECK), f"scripts/mutcheck.py: no mutant {name!r}"


# ── PR B1 — 원격 워커가 기동과 기동 등록 409 에서 따라간다 ──


def subsection(text: str, heading: str) -> str:
    """`### <heading>` 부터 다음 `##`/`###` 앞까지."""
    m = re.search(rf"^###\s+{re.escape(heading)}\s*$", text, re.M)
    assert m, f"no '### {heading}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^###?\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


@pytest.mark.parametrize(
    "word",
    [
        "RCM_CLIENT_VERSION",
        "rcm worker --no-follow",
        "server_version",
        "register: server runs v",
        "exit 2",
        "· pinned by RCM_CLIENT_VERSION",
        "already following",
        "rcm worker --once",
        "rcm worker --check",
        "SuccessfulExit = false",
        "Restart=on-failure",
        "PID",
        "exit 0",
    ],
)
def test_operating_remote_workers_has(follow_section, word):
    sec = subsection(follow_section, "Remote workers")
    assert word in flat(sec), f"docs/operating.md (Remote workers): missing {word!r}"


def test_configuration_second_machine_links_remote_workers():
    sec = section(read(CONFIGURATION), "Second build machine (remote worker)")
    for word in ("rcm worker --no-follow", "operating.md#remote-workers", "exit 2"):
        assert word in flat(sec), f"docs/configuration.md (Second build machine): missing {word!r}"


@pytest.mark.parametrize("path", [USAGE, USAGE_KO], ids=lambda p: p.name)
def test_usage_second_machine_mentions_the_worker_following(path):
    text = read(path)
    for word in ("rcm worker --no-follow", "operating.md#remote-workers"):
        assert word in text, f"{path.name}: missing {word!r}"


def test_changelog_unreleased_has_worker_following():
    text = flat(unreleased(read(CHANGELOG)))
    for word in ("rcm worker --no-follow", "server_version", "exits 2"):
        assert word in text, f"CHANGELOG.md [Unreleased]: missing {word!r}"


@pytest.mark.parametrize(
    "name",
    [
        "m5q-worker-409-after-lost",
        "m5q-worker-409-no-server-version",
        "m5q-worker-start-uses-the-record",
        "m5q-worker-lane-exit-zero",
        "m5q-worker-follow-not-a-worker-exit",
        "m5q-worker-drain-does-not-wait",
        "m5q-worker-lane-claims-while-draining",
        "m5q-worker-drain-window-removed",
        "m5q-worker-restart-keeps-follow-env",
        "m5q-heartbeat-no-server-version",
    ],
)
def test_mutcheck_lists_the_m5q_worker_mutants(name):
    assert f'name="{name}"' in read(MUTCHECK), f"scripts/mutcheck.py: no mutant {name!r}"


# ── PR B2: 돌던 워커의 드레인 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "word",
    [
        "While it runs.",
        "server runs v0.8.1 — draining: no new claims, restarting when running jobs finish",
        "drained — restarting through the installed rcm for v0.8.1",
        "drain cancelled",
        "RCM_WORKER_RESTARTED_AT",
        "not draining: restarted",
        "heartbeat: worker not registered",
        "restart failed: <reason>",
        "job #<id> still not reported",
        "before it registers again",
        "server v0.8.1 — not following (editable install)",
        "· not following: the cached client did not load",
        "rcm: remove <folder> to fetch it again",
        "exits 1",
    ],
)
def test_operating_remote_workers_explains_the_drain(follow_section, word):
    sec = subsection(follow_section, "Remote workers")
    assert word in flat(sec), f"docs/operating.md (Remote workers): missing {word!r}"


def test_operating_no_longer_says_a_running_worker_keeps_its_version(follow_section):
    sec = flat(subsection(follow_section, "Remote workers"))
    assert "A worker that is already registered keeps its version until it restarts" not in sec


def test_changelog_unreleased_has_the_drain():
    text = flat(unreleased(read(CHANGELOG)))
    for word in ("server_version", "draining: no new claims", "RCM_WORKER_RESTARTED_AT", "--once"):
        assert word in text, f"CHANGELOG.md [Unreleased]: missing {word!r}"
    assert "keeps its version until it restarts" not in text


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "examples" / "launchd" / "com.remote-ci-monitor.worker.plist",
        ROOT / "examples" / "systemd" / "rcm-worker.service",
    ],
    ids=lambda p: p.name,
)
def test_service_examples_say_the_restart_keeps_the_pid(path):
    text = flat(read(path))
    for word in ("finishes its running jobs", "same process", "RCM_CLIENT_VERSION", "exits 2"):
        assert word in text, f"{path.name}: missing {word!r}"


@pytest.mark.parametrize("path", [USAGE, USAGE_KO], ids=lambda p: p.name)
def test_usage_says_a_running_worker_follows(path):
    text = flat(read(path))
    word = "next heartbeat" if path == USAGE else "다음 heartbeat"
    assert word in text, f"{path.name}: missing {word!r}"
