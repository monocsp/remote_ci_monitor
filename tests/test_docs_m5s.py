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


# ── PR C: 「사람이 할 일」 표식 (§4.3 · 결정 213 · 234) ───────────────────────


def action_flat(text: str) -> str:
    """줄바꿈 · 연속 공백을 하나로 — 문단 안의 줄 나눔에 시험이 매이지 않게. `read` 는 PR A 의
    것을 그대로 쓴다."""
    return re.sub(r"\s+", " ", text)


def test_action_marker_contract_section_3_names_the_marker_and_the_privacy_rule():
    text = read("docs/release-contract.md")
    sec = text[text.index("## 3. Markers") : text.index("## 4. Secrets")]
    assert "::rcm::action::<code>::<note>" in sec
    assert "[a-z0-9_-]{1,32}" in sec
    assert "200 characters" in sec
    assert "personal detail" in action_flat(sec)
    for word in ("needs_action", "--return-on-action", "RCM_ACTION_CODE", "RCM_ACTION_NOTE"):
        assert word in sec, word


def test_action_marker_readme_lists_the_marker_and_exit_4_in_both_languages():
    for rel in ("README.md", "README.ko.md"):
        text = read(rel)
        assert "::rcm::action::login::" in text, rel
        assert "configuration.md#when-a-person-has-to-act" in text, rel
        rows = [ln for ln in text.splitlines() if ln.startswith("| 4 |")]
        assert len(rows) == 1 and "--return-on-action" in rows[0], (rel, rows)


def test_action_marker_configuration_has_the_section_and_the_hook_env():
    text = read("docs/configuration.md")
    assert "### When a person has to act" in text
    sec = text[text.index("### When a person has to act") : text.index("### Saying what failed")]
    body = action_flat(sec)
    for word in ("{code, note, at}", "needs_action", "--return-on-action", "exit", "**4**"):
        assert word in body, word
    # 이미 있던 표식에서도 돌아오고, 다시 기다릴 때는 플래그 없이다 — 고리를 막는 문장
    assert "already there when the wait started" in body
    assert "**without** the flag" in body
    hooks = action_flat(text[text.index("- **Notifications**") :])
    assert "`RCM_ACTION_CODE` and `RCM_ACTION_NOTE`" in hooks


def test_action_marker_notify_failures_limit_is_written_down():
    """`rcm check` 의 warn 행과 「재시작하면 0」 한계(명세 §4.7)."""
    config = action_flat(read("docs/configuration.md"))
    assert "`warn notify` row" in config
    assert "a restart sets it back to 0" in config
    operating = action_flat(read("docs/operating.md"))
    assert "warn notify N hook deliveries failed" in operating
    assert "sets it to 0" in operating


def test_action_marker_usage_guides_mirror_the_return_on_action_paragraph():
    en = action_flat(read("docs/usage.md"))
    ko = action_flat(read("docs/usage.ko.md"))
    for text in (en, ko):
        assert "rcm wait --job N --return-on-action" in text
        assert "rcm wait --return-on-action" in text  # 종료 코드 4 문단
        assert "#412 needs action · login · Sign in on the build machine" in text
    assert "When the job needs a person." in en
    assert "사람이 손대야 할 때." in ko


def test_action_marker_cli_help_names_the_flag():
    from remote_ci_monitor.cli import build_parser

    sub = next(a for a in build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    wait_help = sub.choices["wait"].format_help()
    assert "--return-on-action" in wait_help and "exit 4" in wait_help


def test_action_marker_docs_say_cancelled_jobs_never_return_4_and_the_wider_cleanup():
    """#251 격리 검증 P2 — 취소 중인 잡은 4 도, 알림 줄도 없다 · note 는 bidi 까지 지운다."""
    config = action_flat(read("docs/configuration.md"))
    assert "being cancelled, or was cancelled, prints no such line and never returns 4" in config
    assert "control, bidirectional and zero-width characters removed" in config
    contract = action_flat(read("docs/release-contract.md"))
    assert "control, bidirectional and zero-width characters are removed" in contract


# ── PR E: status 라우트 · 역할별 창 · 검토 패널 (§4.4 · §3 #11) ──────────────


def test_release_status_panel_configuration_names_the_route_and_the_window():
    text = CONFIGURATION.read_text()
    for needle in (
        "| `POST …/release/status` | client token | `{build_name?, ref?}` → submits "
        "`presets.status`",
        "409 `role_not_configured` when the profile has no `status` preset",
        "`status` (`job_id`, `state`, `measured_at`, `age_seconds`, `stale`, `doc` — aged from the "
        "document's `checked_at`, no `build_name`)",
        "its own window of its newest 500 jobs",
        "at most 3 rows of their own in `jobs[]`",
    ):
        assert needle in text, f"docs/configuration.md lacks {needle!r}"


def test_release_status_panel_readmes_and_changelog_name_the_panel():
    en = re.sub(r"\s+", " ", (ROOT / "README.md").read_text())
    ko = re.sub(r"\s+", " ", (ROOT / "README.ko.md").read_text())
    assert "With a `status` preset the panel also shows each store's review state" in en, (
        "README.md lacks the review panel's store state"
    )
    assert "**Check again** runs the preset once more" in en, "README.md lacks **Check again**"
    assert "`status` 프리셋이 있으면 패널에 두 스토어의 심사 상태도 나온다" in ko, (
        "README.ko.md lacks the review panel's store state"
    )
    assert "**새로 조회**는 그 프리셋을 한 번 더 돌린다" in ko, "README.ko.md lacks **새로 조회**"
    text = re.sub(r"\s+", " ", unreleased_only(CHANGELOG.read_text()))
    for needle in (
        "`POST /api/repos/<repo>/release/status`",
        "**Check again**",
        "own window of 500 jobs",
        "`unknown` for any other",
    ):
        assert needle in text, f"CHANGELOG.md [Unreleased] lacks {needle!r}"


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
