"""M5s 문서 잠금 — 스토어 심사 확인 · 승인 뒤 출시 · 예약 실행.

명세는 `docs/m5s-workplan.md` 다. PR 마다 자기 몫의 함수만 더한다(함수 이름에 PR 주제를 넣는다).
mutcheck 의 복사본(`src` · `tests` · `pyproject.toml`)에는 문서가 없으므로 코드 시험과 나눈다.
실패 문구는 빠진 낱말과 파일을 말한다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "release-contract.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
CHANGELOG = ROOT / "CHANGELOG.md"


def unreleased_only(text: str) -> str:
    """`## [Unreleased]` 부터 다음 `## [` 절 앞까지."""
    m = re.search(r"^## \[Unreleased\][^\n]*$", text, re.M)
    assert m, "no `## [Unreleased]`"
    rest = text[m.end() :]
    nxt = re.search(r"^## \[", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def contract_roles_table() -> str:
    text = CONTRACT.read_text()
    start = text.index("## 2. Roles, inputs and artifacts")
    return text[start : text.index("\n\n**Required**", start)]


# ── PR B: status · golive 역할 등록 (§4.4 · §4.6) ────────────────────────────


def test_release_roles_contract_table_has_the_status_and_golive_rows():
    table = contract_roles_table()
    status = re.search(
        r"^\| `status` \| optional \| `build_name`.*\| `status\.json` \|", table, re.M
    )
    assert status, "docs/release-contract.md §2 lacks the `status` row"
    golive = re.search(r"^\| `golive` \| optional \|.*$", table, re.M)
    assert golive, "docs/release-contract.md §2 lacks the `golive` row"
    row = golive.group(0)
    for needle in (
        "`build_name_android`",
        "`confirm_build_number`",
        "`mode = plan\\|release` (default **plan**)",
        "`golive-plan.json` (plan) / `golive.json` (release)",
        "0 read (approved or not — that is in the file)",
        "2 blocked (a chosen platform is not approved; never 75) or environment / usage",
        "3 build number mismatch",
    ):
        assert needle in row, f"the `golive` row lacks {needle!r}"


def test_release_roles_contract_lists_the_status_and_golive_minimum_fields():
    text = CONTRACT.read_text()
    for needle in (
        "// status.json",
        "preparing · waiting_for_review · in_review · approved · rejected · releasing ·",
        "// golive-plan.json",
        "plan_verdict ok requires every listed platform approved",
        "// golive.json",
        '"result": "published|pending|needs_login|failed"',
    ):
        assert needle in text, f"docs/release-contract.md lacks {needle!r}"


def test_release_roles_configuration_names_the_check_rules():
    text = CONFIGURATION.read_text()
    for needle in (
        'status = "release-status"',
        'golive = "release-golive"',
        "or the `golive` preset's `mode` defaults to `release`",
        "a `golive` preset lacks `build_name confirm_build_number platform mode`",
        "(`gate`, `qa`, `dev`, `version`, `status`, `golive`) is unset",
        "the `review`, `upload` or `golive` preset has no `build_name_android` input",
    ):
        assert needle in text, f"docs/configuration.md lacks {needle!r}"


def test_release_roles_changelog_has_the_entry():
    text = re.sub(r"\s+", " ", unreleased_only(CHANGELOG.read_text()))
    for needle in (
        "`status` and `golive`",
        "`release_status.sh`",
        "`release_golive.sh`",
        "`golive-plan`",
        "`rcm check`",
    ):
        assert needle in text, f"CHANGELOG.md [Unreleased] lacks {needle!r}"


# ── PR A: 예약 실행 (§4.1) ────────────────────────────────────────────────────


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_scheduled_jobs_are_documented():
    """PR A — 예약 실행(§4.1). 설정 문서가 규칙의 정본이고 README · 사용 안내(영 · 한)가 그곳을
    가리킨다. 운영 문서는 정지 중에도 마감 시계가 흐른다고 말한다."""
    config = read("docs/configuration.md")
    assert "## Running a job later: `--at` and `--in`" in config
    for word in (
        "--at",
        "--in",
        "start_at",
        "start_in_seconds",
        "`scheduled`",
        "`expired`",
        "schedule_start_deadline_seconds",
        "schedule_max_ahead_days",
        "start_deadline_seconds",
        "start_at_past",
        "start_at_too_far",
        "start_at_ambiguous",
        "start_deadline_too_long",
        "schedule_exists",
        "schedule: true",
        "server does not support scheduling — upgrade it",
        "pools[].scheduled[]",
        "rcm jobs --state scheduled",
        "[display] timezone",
    ):
        assert word in config, f"docs/configuration.md does not mention {word}"
    anchor = "configuration.md#running-a-job-later---at-and---in"
    for rel in ("README.md", "README.ko.md", "docs/usage.md", "docs/usage.ko.md"):
        text = read(rel)
        assert anchor in text, f"{rel} does not link to the scheduling section"
        assert "--at" in text and "--in" in text, rel
    operating = read("docs/operating.md")
    assert "rcm jobs --state scheduled" in operating and "`expired`" in operating
    for rel in ("examples/server.toml", "src/remote_ci_monitor/templates/server.toml"):
        text = read(rel)
        assert "schedule_start_deadline_seconds" in text and "schedule_max_ahead_days" in text
    unreleased = read("CHANGELOG.md").split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    for word in ("--at", "`scheduled`", "`expired`", "schedule_exists", "schedule: true"):
        assert word in unreleased, f"CHANGELOG [Unreleased] does not mention {word}"


# ── PR F: golive 라우트와 원칙 문장 (§4.5 · §3 #8 · 결정 217 · 235) ─────────────────

PRINCIPLE_EN = (
    "Nothing goes live on approval by itself. A release runs only when a person asks for it "
    "with the version, the build number and a time."
)
PRINCIPLE_KO = (
    "승인됐다고 저절로 출시되지 않습니다. 출시는 사람이 버전과 빌드 번호와 시각을 적어 요청할 "
    "때만 돕니다."
)
ANDROID_EN = (
    "Play has no API to publish a held release, so the project's go-live script publishes "
    "through a browser or asks a person."
)
ANDROID_KO = (
    "Play 에는 보류한 출시를 게시하는 API 가 없습니다. 그래서 프로젝트의 출시 스크립트가 "
    "브라우저로 게시하거나 사람에게 요청합니다."
)
#: 옛 원칙 문장의 꼴들 — §3 #8 의 자리 어디에도 남으면 안 된다
OLD_PRINCIPLE = re.compile(
    r"Approval does not release|승인은 출시가 아닙니다|No release after approval|"
    r"rcm never releases after approval|must never be able to release after store approval|"
    r"release anything after store approval|releases anything after store approval|"
    r"Release after approval is a human step|publish stays in the console|게시는 콘솔에서|"
    r"승인 뒤 출시는 여전히 사람이 콘솔에서|releases\s+nothing\.|승인 뒤 출시하는 일은 하지|"
    r"출시 버튼 없음"
)
#: §3 #8 의 자리 — 코드 · 문서 · 템플릿 · 와이어프레임 · 골든 픽스처
PRINCIPLE_PLACES = (
    "src/remote_ci_monitor/web/i18n.js",
    "src/remote_ci_monitor/web/index.html",
    "src/remote_ci_monitor/web/app.js",
    "docs/release-contract.md",
    "docs/configuration.md",
    "README.md",
    "README.ko.md",
    "src/remote_ci_monitor/skills/rcm-store-connect/SKILL.md",
    "src/remote_ci_monitor/skills/rcm-connect/SKILL.md",
    "src/remote_ci_monitor/skills/rcm-store-connect/templates/profile.toml",
    "src/remote_ci_monitor/skills/rcm-store-connect/templates/presets.release.toml",
    "src/remote_ci_monitor/skills/rcm-store-connect/templates/release_review.sh",
    "src/remote_ci_monitor/skills/rcm-store-connect/templates/release_upload.sh",
    "tests/fixtures/rcm_candidate/profile.release.toml",
    "tests/fixtures/rcm_candidate/presets.release.toml",
    "tests/fixtures/rcm_candidate/golden.full.toml",
    "tests/fixtures/rcm_candidate/golden.new-repo.toml",
    "tests/fixtures/rcm_candidate/golden.empty-server.toml",
    "docs/wireframes/web-store.html",
    "docs/wireframes/web-version-page.html",
)


def flat(text: str) -> str:
    """줄바꿈 · 주석 기호 · 들여쓰기를 한 칸으로 — 문장이 줄을 넘어가도 찾는다."""
    return re.sub(r"[\s#]+", " ", text)


def test_golive_route_principle_sentence_replaces_the_old_one_everywhere():
    """S16 · 결정 217 — §3 #8 의 자리 어디에도 옛 원칙 문장이 없고, 새 문장이 있다."""
    for rel in PRINCIPLE_PLACES:
        old = OLD_PRINCIPLE.search(read(rel))
        assert old is None, f"{rel} still says {old.group(0)!r}"
    i18n = read("src/remote_ci_monitor/web/i18n.js")
    for sentence in (PRINCIPLE_EN, PRINCIPLE_KO, ANDROID_EN, ANDROID_KO):
        assert sentence in i18n, f"i18n.js lacks {sentence!r}"
    assert PRINCIPLE_KO in read("src/remote_ci_monitor/web/index.html")
    for rel in (
        "docs/release-contract.md",
        "docs/configuration.md",
        "README.md",
        "src/remote_ci_monitor/skills/rcm-store-connect/SKILL.md",
        "src/remote_ci_monitor/skills/rcm-connect/SKILL.md",
    ):
        assert "Nothing goes live on approval by itself" in flat(read(rel)), rel
    contract = flat(read("docs/release-contract.md"))
    assert PRINCIPLE_EN in contract and ANDROID_EN in contract
    section6 = contract[contract.index(" 6. Rules the page enforces") :]
    assert section6.index(PRINCIPLE_EN) < section6.index("Submitting for review is one button")
    assert "승인됐다고 저절로" in read("README.ko.md")
    for rel in (
        "docs/wireframes/web-store.html",
        "docs/wireframes/web-version-page.html",
    ):
        assert PRINCIPLE_KO in read(rel), rel


def test_golive_route_409s_and_the_cli_are_documented():
    """§4.5 · 결정 215 · 232 · 235 — 설정 문서가 라우트 · 409 · 일반 경로 막기의 정본이고
    README(영 · 한)가 두 CLI 명령을 싣는다. 계약 §2 의 2 설명과 §8 스킬 표도 맞춘다."""
    config = read("docs/configuration.md")
    assert "### Going live (`golive`)" in config
    for word in (
        "`POST …/release/golive`",
        "golive_plan_required",
        "golive_plan_stale",
        "golive_plan_blocked",
        "golive_running",
        "release_route_required",
        "golive_plan_now_only",
        "`auto` is not accepted",
        "rcm release golive",
        "rcm release status",
        "start_in_seconds",
    ):
        assert word in config, f"docs/configuration.md does not mention {word}"
    for rel in ("README.md", "README.ko.md"):
        text = read(rel)
        assert "rcm release golive" in text and "rcm release status" in text, rel
        assert "configuration.md#going-live-golive" in text, rel
    table = contract_roles_table()
    assert "2 blocked (a chosen platform is not approved; never 75) or environment / usage" in table
    assert "· also environment / usage" not in table
    contract = read("docs/release-contract.md")
    skills = contract[contract.index("## 8. Skills") :]
    assert "`status` and `golive`" in skills and "release_golive.sh" in skills
    assert "not_attempted" in contract  # golive.json 의 reason — 화면은 「시도 안 함」 으로 그린다
    unreleased = unreleased_only(read("CHANGELOG.md"))
    for word in (
        "release/golive",
        "rcm release golive",
        "rcm release status",
        "release_route_required",
        "golive_running",
        "Nothing goes live on approval by itself",
    ):
        assert word in unreleased, f"CHANGELOG [Unreleased] does not mention {word}"
