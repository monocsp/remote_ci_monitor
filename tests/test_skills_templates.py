"""스킬 템플릿(`src/remote_ci_monitor/skills/…/templates/`)이 저장소 루트에서 그대로 돈다 —
버전 역할 계약(docs/version-page-workplan.md §1.3 · §7 AC-A3~A8).

템플릿은 프로젝트에 복사돼 `scripts/release/` 에서 돌지만, 셀프테스트는 어디서든(`SELF=` 규칙)
돌아야 한다. 여기서는 rcm 저장소 루트에서 부른다. 잠그는 것: 각 셀프테스트의 종료 코드와 PASS 줄 ·
`rcm_contract.py validate version` 의 거절 · `listing_json` → `listing.json` + 미리보기 줄 ·
드라이버의 `--version-id` · `rcm skills install` 뒤 파일 존재 · 채운 프리셋·프로파일 템플릿이
`rcm check` 의 `release <repo>` 행을 FAIL 로 만들지 않음 · 계약 문서의 문면.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor.cli import _release_row
from remote_ci_monitor.config import load_server_config
from remote_ci_monitor.core.inputs import InputError, validate_inputs

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "src" / "remote_ci_monitor" / "skills"
STORE = SKILLS / "rcm-store-connect" / "templates"
DRIVER = SKILLS / "rcm-release-driver" / "templates"
CONTRACT_DOC = ROOT / "docs" / "release-contract.md"
PASS_LINE = re.compile(r"selftest[: ]+(PASS|ok|all green)")
#: 아직 안 채워진 템플릿은 `{{secrets_env}}` 가 셸 이름이 아니라서 그 이름으로 비밀 폴더를 읽는다
#: (템플릿 맨 위의 case 문 — bash 5 는 이름이 아닌 것의 간접 확장을 거부한다).
SELFTEST_SECRETS_ENV = "RCM_TEMPLATE_SECRETS_DIR"


def sh(*argv: str, env: dict[str, str] | None = None, cwd: Path = ROOT):
    """템플릿을 저장소 루트에서 부른다. (rc, stdout+stderr)"""
    full = {**os.environ, **(env or {})}
    full.pop("PYTHONPATH", None)
    res = subprocess.run(
        list(argv), cwd=cwd, env=full, capture_output=True, text=True, timeout=300, check=False
    )
    return res.returncode, res.stdout + res.stderr


# ── 셀프테스트 (AC-A3 · AC-A6) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ("bash", str(STORE / "release_version.sh"), "--selftest"),
        ("bash", str(STORE / "release_review.sh"), "--selftest"),
        ("bash", str(STORE / "release_upload.sh"), "--selftest"),
        ("bash", str(STORE / "release_plan.sh"), "--selftest"),
        ("bash", str(STORE / "release_status.sh"), "--selftest"),
        # 스물두 경우를 차례로 돌리는 셀프테스트다. 한가할 때 2초 아래로 줄였지만 rcm-ci 의
        # 부하 속에서는 여덟 배까지 늘었다(#3999 에서 8초 → 65초). 이것 하나만 slow 로 둔다.
        pytest.param(
            ("bash", str(STORE / "release_golive.sh"), "--selftest"), marks=pytest.mark.slow
        ),
        (sys.executable, str(STORE / "rcm_contract.py"), "--selftest"),
        ("bash", str(DRIVER / "release_driver.sh"), "--selftest"),
        (sys.executable, str(DRIVER / "release_check.py"), "--selftest"),
    ],
    ids=lambda a: Path(a[1]).name,
)
def test_each_template_selftest_passes_from_the_repo_root(argv):
    """AC-A3 · AC-A6: exit 0 이고 마지막 줄이 PASS 줄이다(0 으로만 끝나는 스텁은 통과 못 한다)."""
    rc, out = sh(*argv)
    assert rc == 0, out
    assert PASS_LINE.search(out.strip().splitlines()[-1]), out.strip().splitlines()[-3:]


@pytest.mark.parametrize(
    "name",
    [
        "release_plan.sh",
        "release_upload.sh",
        "release_review.sh",
        "release_version.sh",
        "release_status.sh",
        "release_golive.sh",
    ],
)
def test_an_unfilled_template_never_indirects_through_an_invalid_name(name):
    """안 채워진 템플릿(`{{secrets_env}}` 그대로)도 저장소 루트에서 셀프테스트로 돌아야 한다.
    bash 5(리눅스 CI)는 이름이 아닌 것의 `${!VAR}` 를 «invalid variable name» 으로 거부하고
    `set -e` 가 그 자리에서 죽인다 — macOS 의 bash 3.2 는 조용히 빈 값을 준다. 그래서 간접
    확장 앞에 이름을 검사하는 case 문이 있어야 한다."""
    text = (STORE / name).read_text()
    guard = (
        'case "$SECRETS_ENV" in ""|[0-9]*|*[!A-Za-z0-9_]*) '
        f"SECRETS_ENV={SELFTEST_SECRETS_ENV};; esac"
    )
    assert guard in text, name
    assert text.index(guard) < text.index('SECRETS_DIR="${!SECRETS_ENV:-}"'), name


def test_version_selftest_names_its_four_cases():
    """AC-A3: 계획서의 네 경우 — store/ 파일 prefill · create 가 훅을 부르고 version.json ·
    제출된 버전 delete → 4 · 잘못된 이름 → 2 — 가 각각 ok 줄로 나온다."""
    rc, out = sh("bash", str(STORE / "release_version.sh"), "--selftest")
    assert rc == 0
    for needle in (
        "prefill from store/ files",
        "create both -> version.json + prefill.json",
        "delete a submitted version -> exit 4",
        "bad name -> usage exit 2",
    ):
        assert re.search(rf"^  ok  .*{re.escape(needle)}", out, re.M), (needle, out)


def test_golive_selftest_locks_the_two_exit_code_rules():
    """M5s S18 · 결정 224 · 236: plan 모드는 스토어를 읽었으면 0 이고 승인 여부는 문서에만 적는다.
    release 모드에서 고른 플랫폼이 승인 상태가 아니면 언제나 2 다 — 75 로 승인을 기다리면
    승인을 감시하는 자동 출시가 된다. 출시 훅은 그때 한 번도 불리지 않는다."""
    rc, out = sh("bash", str(STORE / "release_golive.sh"), "--selftest")
    assert rc == 0, out
    for needle in (
        "plan, Android not approved -> exit 0, android.approved = false",
        "plan, store unreadable -> exit 1",
        "release, not approved -> exit 2 (never 75), no release call",
        "release, build number mismatch -> exit 3, no release call",
        "release, login lost -> action marker + exit 75",
        "release ios, approved -> release requested, exit 75 until the store says live",
        "release ios, already live -> exit 0, no release call",
        "release both, Android not approved -> exit 2, iOS untouched too",
        "release android, publish pressed, not live yet -> exit 75, no second publish",
        # #252 재검증: 「기다리지 않는다」는 가짜 sleep 기록 0건이 잠근다. 시간 상한은 부하에
        # 흔들리지 않게 10초로 둔다(#255 검증 P2 — 3초는 rcm-ci 부하에서 흔들린다)
        "release, login lost -> exits 75 at once: one read, one publish, no sleep, under 10 s",
    ):
        assert re.search(rf"^  ok  .*{re.escape(needle)}", out, re.M), (needle, out)


def test_golive_selftest_records_every_sleep_and_fails_on_one(tmp_path):
    """잠금이 진짜 잡는지 본다 — 로그인 표식 자리에 `sleep 1` 을 넣은 사본은 selftest 가 빨갛다."""
    copy = tmp_path / "release_golive.sh"
    text = (STORE / "release_golive.sh").read_text()
    marker = '      echo "::rcm::action::login::'
    assert text.count(marker) == 1
    copy.write_text(text.replace(marker, '      sleep 1; echo "::rcm::action::login::'))
    (tmp_path / "rcm_contract.py").write_text((STORE / "rcm_contract.py").read_text())
    rc, out = sh("bash", str(copy), "--selftest")
    assert rc != 0, out
    assert "the script slept: sleep 1" in out, out


def test_the_golive_template_states_android_idempotency_and_no_wait_on_login():
    """격리 검증 지적(#252): Android 는 게시를 누른 뒤 `PUBLISHED` 가 되기 전까지 `in_progress` 로
    답해야 다음 시도가 버튼을 또 누르지 않는다. 로그인 표식을 찍었으면 그 자리에서 기다리지 않고
    바로 75 로 끝낸다 — 기다리면 서버의 `needs_action` 이 stuck · overdue 를 가린다."""
    text = (STORE / "release_golive.sh").read_text()
    head = text[: text.index("store_approval() {")]
    flat = re.sub(r"[\s#]+", " ", head)
    for needle in (
        "after publish was pressed, answer in_progress until the API says PUBLISHED",
        "remember that it pressed publish, or make the hook idempotent",
        "never wait for the sign-in",
        "exit 75 at once",
    ):
        assert needle in flat, (needle, "release_golive.sh header")
    # #252 재검증: Android TODO — 화면이 게시 시작을 확인한 뒤에만 그 버전 코드로 기록한다
    hook = re.sub(r"[\s#]+", " ", text[text.index("store_android_publish() {") :])
    assert "only after the screen confirms that publishing started" in hook


def test_status_selftest_reads_both_stores_and_never_writes_one():
    rc, out = sh("bash", str(STORE / "release_status.sh"), "--selftest")
    assert rc == 0, out
    for needle in (
        "both stores read -> status.json, exit 0",
        "store unreadable -> exit 1, no status.json",
        "a state outside the eight -> contract refuses, exit 1",
    ):
        assert re.search(rf"^  ok  .*{re.escape(needle)}", out, re.M), (needle, out)


def test_the_golive_and_status_templates_hold_no_word_the_preset_grep_refuses():
    """§3 #13: 스킬의 grep 이 `rollout`·`promote`·`auto_release` 를 찾고 서버의 `unsafe_preset`
    은 입력 이름 `automatic_release`·`rollout`·`release_status` 를 막는다. 두 뼈대의 입력
    이름(RCM_INPUT_*)에 그 낱말이 없어야 프리셋을 그대로 옮겨 써도 막히지 않는다."""
    for name in ("release_status.sh", "release_golive.sh"):
        text = (STORE / name).read_text()
        inputs = set(re.findall(r"RCM_INPUT_([A-Z0-9_]+)", text))
        assert inputs, name
        for word in ("ROLLOUT", "PROMOTE", "AUTO_RELEASE", "AUTOMATIC_RELEASE", "RELEASE_STATUS"):
            assert not any(word in i for i in inputs), (name, word, inputs)


def test_plan_selftest_proves_the_live_version_names_travel():
    """워크플랜 §13-1 — 플랜이 Play 의 라이브 **이름**(`store.play.production_name`)을 실어야
    Android 힌트와 목록의 Play 칸이 산다. 이름이 없는 스냅샷도 그대로 유효한 문서다."""
    rc, out = sh("bash", str(STORE / "release_plan.sh"), "--selftest")
    assert rc == 0, out
    for needle in (
        "snapshot without the names -> valid plan.json, no hint invented",
        "play.production_name lands in plan.json",
        "next_version_hint lands in plan.json",
    ):
        assert re.search(rf"^  ok  .*{re.escape(needle)}", out, re.M), (needle, out)


def test_the_plan_template_and_its_skill_ask_for_both_live_version_names():
    """훅 계약 · TODO 블록 · SKILL.md 가 두 필드를 이름으로 부른다 — 어느 스킬도 말하지 않으면
    스킬로 붙인 프로젝트의 `plan.json` 에 그 필드가 생기지 않는다(§13-1 이 바로 그 사고다)."""
    text = (STORE / "release_plan.sh").read_text()
    todo = text[text.index("TODO(project): read App Store Connect") :]
    for needle in ("production_name", "asc_live", "next_version_hint"):
        assert needle in todo, (needle, "TODO(project) block")
    skill = (SKILLS / "rcm-store-connect" / "SKILL.md").read_text()
    for needle in ("play.production_name", "next_version_hint", "asc_live"):
        assert needle in skill, needle


# ── rcm_contract.py validate version (AC-A4) ─────────────────────────────────


def validate(kind: str, path: Path):
    return sh(sys.executable, str(STORE / "rcm_contract.py"), "validate", kind, "--file", str(path))


def version_name(path: Path):
    return sh(sys.executable, str(DRIVER / "release_check.py"), "version-name", "--json", str(path))


def test_contract_rejects_a_version_document_with_both_stores_null(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "create",
                "ios": None,
                "android": None,
                "error": None,
                "measured_at": "2026-09-18T00:00:00Z",
            }
        )
    )
    rc, out = validate("version", bad)
    assert rc == 1, out
    assert "both null" in out, out
    # 같은 문서에 이유가 붙으면(실패한 create) 받는다 — 어떻게 끝나든 파일은 남긴다
    ok = tmp_path / "failed.json"
    ok.write_text(bad.read_text().replace('"error": null', '"error": "already exists (exit 3)"'))
    rc, out = validate("version", ok)
    assert rc == 0, out


@pytest.mark.parametrize(
    "kind, patch, needle",
    [
        ("version", {"mode": "prefill"}, "mode"),
        ("version", {"ios": {"version": "1.1.1"}}, "asc_version_id"),
        ("prefill", {"ios": None, "android": None}, "both null"),
        ("prefill", {"android": {"title": ["x"]}}, "android.title"),
    ],
)
def test_contract_names_the_bad_field_of_version_and_prefill(tmp_path, kind, patch, needle):
    good = {
        "version": {
            "schema": 1,
            "mode": "create",
            "ios": {"version": "1.1.1", "asc_version_id": "a", "state": "PREPARE_FOR_SUBMISSION"},
            "android": {"version": "1.0.1"},
            "error": None,
            "measured_at": "2026-09-18T00:00:00Z",
        },
        "prefill": {"schema": 1, "source": "file:store/", "locale": "ko", "ios": {}, "android": {}},
    }[kind]
    doc = {**good, **patch}
    f = tmp_path / f"{kind}.json"
    f.write_text(json.dumps(doc))
    rc, out = validate(kind, f)
    assert rc == 1 and needle in out, out


# ── rcm_contract.py — status · golive-plan · golive (M5s §4.4 · §4.5) ─────────

GOOD_STATUS = {
    "schema": 1,
    "checked_at": "2026-11-23T01:00:00Z",
    "ios": {
        "version": "1.2.0",
        "build": 185,
        "state": "approved",
        "raw_state": "PENDING_DEVELOPER_RELEASE",
        "release_type": "MANUAL",
        "phased": None,
    },
    "android": {
        "version": "1.2.0",
        "version_code": 185,
        "state": "in_review",
        "raw_state": "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
        "user_fraction": None,
    },
}
GOOD_GOLIVE_PLAN = {
    "schema": 1,
    "build_name": "1.2.0",
    "measured_at": "2026-11-23T01:00:00Z",
    "platforms": {
        "ios": {"approved": True, "approved_build": 185, "raw_state": "PENDING_DEVELOPER_RELEASE"},
        "android": {"approved": False, "approved_build": None, "raw_state": "IN_REVIEW"},
    },
    "plan_verdict": "blocked",
    "reasons": ["android_not_approved"],
}
GOOD_GOLIVE = {
    "schema": 1,
    "build_name": "1.2.0",
    "platforms": {
        "ios": {"result": "requested", "raw_state": "PENDING_DEVELOPER_RELEASE"},
        "android": {"result": "needs_login", "raw_state": "APPROVED_NOT_PUBLISHED"},
    },
    "finished_at": "2026-11-23T06:00:00Z",
}


@pytest.mark.parametrize(
    "kind, good",
    [("status", GOOD_STATUS), ("golive-plan", GOOD_GOLIVE_PLAN), ("golive", GOOD_GOLIVE)],
)
def test_contract_accepts_the_minimum_status_and_golive_documents(tmp_path, kind, good):
    f = tmp_path / f"{kind}.json"
    f.write_text(json.dumps(good))
    rc, out = validate(kind, f)
    assert rc == 0, out


@pytest.mark.parametrize(
    "kind, patch, needle",
    [
        ("status", {"checked_at": None}, "checked_at"),
        ("status", {"ios": None, "android": None}, "both null"),
        (
            "status",
            {"ios": {**GOOD_STATUS["ios"], "state": "PENDING_DEVELOPER_RELEASE"}},
            "ios.state",
        ),
        ("status", {"ios": {**GOOD_STATUS["ios"], "phased": {"day": 3}}}, "ios.phased"),
        (
            "status",
            {"android": {**GOOD_STATUS["android"], "version_code": "185"}},
            "android.version_code",
        ),
        ("golive-plan", {"plan_verdict": "maybe"}, "plan_verdict"),
        ("golive-plan", {"build_name": ""}, "build_name"),
        ("golive-plan", {"platforms": {}}, "platforms"),
        (
            "golive-plan",
            {"platforms": {"ios": {"approved": "yes", "approved_build": 185, "raw_state": "x"}}},
            "platforms.ios.approved",
        ),
        ("golive-plan", {"plan_verdict": "ok"}, "android"),  # ok 인데 승인 안 된 플랫폼이 있다
        ("golive-plan", {"reasons": []}, "reasons"),  # blocked 인데 이유가 없다
        (
            "golive",
            {"platforms": {"android": {"result": "released", "raw_state": None}}},
            "android.result",
        ),
        (
            "golive",
            {"platforms": {"ios": {"result": "published", "raw_state": None}}},
            "ios.result",
        ),
        ("golive", {"finished_at": 3}, "finished_at"),
    ],
)
def test_contract_names_the_bad_field_of_status_and_golive(tmp_path, kind, patch, needle):
    good = {"status": GOOD_STATUS, "golive-plan": GOOD_GOLIVE_PLAN, "golive": GOOD_GOLIVE}[kind]
    f = tmp_path / f"{kind}.json"
    f.write_text(json.dumps({**good, **patch}))
    rc, out = validate(kind, f)
    assert rc == 1 and needle in out, out


# ── listing_json → listing.json (AC-A5) ──────────────────────────────────────


def test_review_plan_with_listing_json_writes_the_file_and_previews_its_fields(tmp_path):
    """AC-A5: `RCM_INPUT_LISTING_JSON='{"ios":{"subtitle":"X"}}'` 로 돌리면 `listing.json` 이 생기고
    review-plan.json 의 preview 줄에 `subtitle: X` 가 있다."""
    work = tmp_path / "work"
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    rc, out = sh(
        "bash",
        str(STORE / "release_review.sh"),
        env={
            "RELEASE_SHIM": "ok",
            "RELEASE_WORK": str(work),
            SELFTEST_SECRETS_ENV: str(secrets),
            "RCM_INPUT_BUILD_NAME": "1.0.1",
            "RCM_INPUT_LISTING_JSON": '{"ios":{"subtitle":"X"}}',
        },
    )
    assert rc == 0, out
    assert json.loads((work / "listing.json").read_text()) == {"ios": {"subtitle": "X"}}
    plan = json.loads((work / "review-plan.json").read_text())
    assert any("subtitle: X" in line for line in plan["listing"]["preview"]), plan["listing"]
    assert "::rcm::summary::" in out and "nothing submitted" in out


def test_review_plan_without_listing_json_writes_no_listing_file(tmp_path):
    work = tmp_path / "work"
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    rc, out = sh(
        "bash",
        str(STORE / "release_review.sh"),
        env={
            "RELEASE_SHIM": "ok",
            "RELEASE_WORK": str(work),
            SELFTEST_SECRETS_ENV: str(secrets),
            "RCM_INPUT_BUILD_NAME": "1.0.1",
        },
    )
    assert rc == 0, out
    assert not (work / "listing.json").exists()


# ── 드라이버 --version-id (AC-A6 · E16) ──────────────────────────────────────


def test_driver_status_without_a_readable_version_row_prints_stage_v(tmp_path):
    """`--version-id` 만 있고 rcm 을 못 읽는 `--status` 는 `stage V` 를 찍고 끝난다(읽기 전용)."""
    profile = tmp_path / "profile.toml"
    profile.write_text(
        '[repos.app.release]\ndefault_branch = "main"\ntag = "prod/{version}-{build}"\n'
        '[repos.app.release.presets]\nplan = "release-plan"\nupload = "release-upload"\n'
        'review = "release-review"\n'
    )
    presets = tmp_path / "presets.toml"
    presets.write_text(
        "".join(
            f'[[presets]]\nname = "{n}"\nargv = ["bash", "x.sh"]\nrepo = "app"\n'
            for n in ("release-plan", "release-upload", "release-review")
        )
    )
    api = tmp_path / "api.sh"
    api.write_text("#!/usr/bin/env bash\nexit 7\n")
    api.chmod(0o755)
    rc, out = sh(
        "bash",
        str(DRIVER / "release_driver.sh"),
        "--version-id",
        "7",
        "--status",
        env={
            "RELEASE_PROFILE_FILE": str(profile),
            "RELEASE_PRESETS_FILE": str(presets),
            "RELEASE_VERSION_API": str(api),
            "RELEASE_GH_REPO": "org/app",
        },
    )
    assert rc == 0, out
    assert "stage V" in out and "build ? · version #7 · stage V" in out, out
    assert "stages: V S0 S1 S2 S3 S4 S5 S6 S7 S8" in out, out


def test_driver_refuses_a_version_id_that_is_not_a_number():
    rc, out = sh("bash", str(DRIVER / "release_driver.sh"), "--version-id", "seven", "--status")
    assert rc == 2 and "--version-id must be an integer" in out, out


STAGES_LINE = "stages: V S0 S1 S2 S3 S4 S5 S6 S7 S8"


def test_status_always_says_which_stages_the_driver_knows():
    """워크플랜 §13-2 · 계약 §5 — `--status` 는 `--version-id` 도 `--build-name` 도 없이 불러도
    `stages:` 한 줄을 찍고 0 으로 끝난다. 서버는 그 줄에 `V` 가 있는지로만 «이 드라이버가 버전
    단계를 아는가» 를 안다 — exit 2 는 이미 «번호가 필요하다» 와 «환경 막힘» 둘이라 못 쓴다.
    레포도 프로파일도 없는 곳에서 돌아야 한다(rcm 저장소 루트가 바로 그런 곳이다)."""
    rc, out = sh("bash", str(DRIVER / "release_driver.sh"), "--status")
    assert rc == 0, out
    assert out.strip() == STAGES_LINE, out
    # 판정기가 내는 목록 그대로다 — 목록은 한 곳(DRIVER_STAGES)에만 적혀 있다
    rc, listed = sh(sys.executable, str(DRIVER / "release_check.py"), "stages")
    assert rc == 0 and f"stages: {listed.strip()}" == STAGES_LINE, listed


def test_the_stage_list_lives_in_one_place():
    """워크플랜 §13-4 — 단계 목록이 두 벌이면 어긋난다(옛 `STAGES` 에는 `S2` 가 없었다).
    셸은 `release_check.py stages` 를 불러 찍을 뿐, 목록을 자기 안에 적어 두지 않는다."""
    shell = (DRIVER / "release_driver.sh").read_text()
    assert "${CHECK} stages" in shell
    body = "\n".join(ln for ln in shell.splitlines() if not ln.lstrip().startswith("#"))
    assert "S0 S1 S2" not in body, "the driver writes the stage list a second time"
    check = (DRIVER / "release_check.py").read_text()
    assert "DRIVER_STAGES = (" in check
    assert "STAGES = (*DRIVER_STAGES," in check, "STAGES must be derived, not written again"


def test_release_check_version_name_prefers_ios_and_never_invents(tmp_path):
    row = tmp_path / "row.json"
    row.write_text(json.dumps({"id": 7, "ios_version": "1.1.1", "android_version": "1.0.1"}))
    rc, out = version_name(row)
    assert (rc, out.strip()) == (0, "1.1.1")
    row.write_text(json.dumps({"id": 7, "ios_version": None, "android_version": "1.0.1"}))
    rc, out = version_name(row)
    assert (rc, out.strip()) == (0, "1.0.1")
    row.write_text(json.dumps({"id": 7, "ios_version": None, "android_version": None}))
    rc, out = version_name(row)
    assert rc == 1 and "neither" in out


# ── rcm skills install (AC-A7) ───────────────────────────────────────────────


def test_skills_install_ships_release_version_and_the_skill_docs_name_it(tmp_path, capsys):
    root = cli.skills_root()
    if root is None or not cli._skill_dirs(root):
        pytest.skip("this build packages no skills")
    project = tmp_path / "x"
    project.mkdir()
    try:
        code = cli.main(["skills", "install", "--into", str(project)])
    except SystemExit as e:  # pragma: no cover - argparse 경로
        code = e.code
    capsys.readouterr()
    assert code == 0
    installed = project / ".claude" / "skills" / "rcm-store-connect" / "templates"
    assert (installed / "release_version.sh").is_file()
    assert (installed / "release_version.sh").read_text().startswith("#!/usr/bin/env bash")
    store_md = (SKILLS / "rcm-store-connect" / "SKILL.md").read_text()
    assert "| `scripts/release/release_version.sh` |" in store_md
    assert "listing_json" in store_md and "adopt" in store_md.lower()
    connect_md = (SKILLS / "rcm-connect" / "SKILL.md").read_text()
    assert "| optional | `version`" in connect_md and "version: <yes|no>" in connect_md
    driver_md = (SKILLS / "rcm-release-driver" / "SKILL.md").read_text()
    assert "--version-id <id>" in driver_md and "| V |" in driver_md


# ── 채운 템플릿이 rcm check 를 통과한다 ─────────────────────────────────────


def fill(text: str) -> str:
    return (
        text.replace("{{repo}}", "app")
        .replace("{{secrets_env}}", "APP_SECRETS")
        .replace("{{scripts_dir}}", "scripts/release")
        .replace("{{platform_default}}", "both")
    )


def test_filled_presets_and_profile_templates_pass_the_release_check_row(tmp_path):
    """템플릿을 채워 server.toml 로 만들면 `release app` 행이 FAIL 이 아니고 listing_json 경고도
    없다."""
    toml = (
        f'[server]\ndata_dir = "{tmp_path / "data"}"\n\n[[repos]]\nname = "app"\n'
        'url = "git@example.com:org/app.git"\n\n'
        + fill((STORE / "profile.toml").read_text())
        + "\n"
        + fill((STORE / "presets.release.toml").read_text())
    )
    cfg_path = tmp_path / "server.toml"
    cfg_path.write_text(toml)
    cfg = load_server_config(cfg_path, environ={}, check_tools=False)
    profile = cfg.repos[0].release
    assert profile is not None
    assert profile.version_ttl_hours == 24
    assert profile.preset_for("version") == "release-version"
    version = cfg.preset("release-version")
    assert version is not None
    assert [i.name for i in version.inputs] == [
        "mode",
        "ios_version",
        "android_version",
        "asc_version_id",
    ]
    assert version.input_spec("mode").default == "prefill"
    for name in ("release-upload", "release-review"):
        assert cfg.preset(name).input_spec("listing_json") is not None, name
        assert cfg.preset(name).input_spec("build_name_android") is not None, name
    row, ok, detail = _release_row(cfg, "app", profile)
    assert row == "release app"
    assert ok is not False, detail
    assert "listing_json" not in detail, detail
    assert "build_name_android" not in detail, detail
    assert "version=release-version" in detail, detail


def test_filled_templates_carry_the_status_and_golive_roles(tmp_path):
    """M5s §4.6 · F — 템플릿이 두 선택 역할을 채운다. golive 프리셋은 git_ref · 높은 우선순위 ·
    시작 마감 3600초 · 로그인 프리셋과 나눌 공용 그룹 · 산출물 항상이고, 기본 모드는 plan 이다.
    두 역할이 차 있으니 `release app` 행에 그 둘의 「not configured」 경고가 없다."""
    toml = (
        f'[server]\ndata_dir = "{tmp_path / "data"}"\n\n[[repos]]\nname = "app"\n'
        'url = "git@example.com:org/app.git"\n\n'
        + fill((STORE / "profile.toml").read_text())
        + "\n"
        + fill((STORE / "presets.release.toml").read_text())
    )
    cfg_path = tmp_path / "server.toml"
    cfg_path.write_text(toml)
    cfg = load_server_config(cfg_path, environ={}, check_tools=False)
    profile = cfg.repos[0].release
    assert profile is not None
    assert profile.preset_for("status") == "release-status"
    assert profile.preset_for("golive") == "release-golive"
    status = cfg.preset("release-status")
    assert [i.name for i in status.inputs] == ["build_name"]
    assert status.input_spec("build_name").default == ""
    assert status.source_modes == ("git_ref",) and status.artifacts_on == "always"
    golive = cfg.preset("release-golive")
    assert golive.source_modes == ("git_ref",)
    assert golive.priority == 1  # "high"
    assert golive.start_deadline_seconds == 3600
    assert golive.concurrency_group == "store-browser-app"
    assert golive.artifacts_on == "always"
    assert [i.name for i in golive.inputs] == [
        "build_name",
        "build_name_android",
        "confirm_build_number",
        "mode",
        "platform",
    ]
    assert golive.input_spec("mode").default == "plan"
    assert golive.input_spec("mode").choices == ("plan", "release")
    row, ok, detail = _release_row(cfg, "app", profile)
    assert ok is not False, detail
    for role in ("status", "golive"):
        assert f"{role} not configured" not in detail, detail
    assert "status=release-status" in detail and "golive=release-golive" in detail, detail
    # 스킬의 grep(§3 #13)이 막는 낱말이 프리셋 파일 어디에도 없다
    text = (STORE / "presets.release.toml").read_text()
    assert not re.search(r"automatic_release|rollout|auto_release|promote", text, re.I)


@pytest.mark.parametrize(("default", "fails"), [("release", True), ("plan", False)])
def test_a_golive_preset_defaulting_to_release_fails_the_release_row(tmp_path, default, fails):
    """M5s §4.6: golive 의 되돌릴 수 없는 모드는 `release` 다. 기본값이면 손으로 한 번 돌린 잡이
    출시가 되므로 `release <repo>` 행이 FAIL 이다(mutcheck 가 이 시험을 쓴다 — `examples/` 를
    읽지 않는다)."""
    toml = (
        f'[server]\ndata_dir = "{tmp_path / "data"}"\n\n[[repos]]\nname = "app"\n'
        'url = "git@example.com:org/app.git"\n\n'
        '[repos.app.release.presets]\nplan = "p"\nupload = "u"\nreview = "r"\n'
        'golive = "release-golive"\n\n'
        + "".join(
            f'[[presets]]\nname = "{n}"\nargv = ["true"]\nsource_modes = ["git_ref"]\n'
            'repo = "app"\n\n'
            for n in ("p", "u", "r")
        )
        + '[[presets]]\nname = "release-golive"\nargv = ["true"]\nsource_modes = ["git_ref"]\n'
        'repo = "app"\ninputs = [\n  { name = "build_name" },\n'
        '  { name = "build_name_android", default = "" },\n'
        '  { name = "confirm_build_number", default = "" },\n'
        '  { name = "platform", type = "choice", choices = ["ios", "android", "both"], '
        'default = "ios" },\n'
        '  { name = "mode", type = "choice", choices = ["plan", "release"], '
        f'default = "{default}" }},\n]\n'
    )
    cfg_path = tmp_path / "server.toml"
    cfg_path.write_text(toml)
    cfg = load_server_config(cfg_path, environ={}, check_tools=False)
    profile = cfg.repos[0].release
    assert profile is not None
    _row, ok, detail = _release_row(cfg, "app", profile)
    # 필수 역할의 가짜 프리셋은 입력이 없어 따로 FAIL 이다 — 여기서 보는 것은 golive 의 한 줄뿐
    assert ok is False
    needle = "preset 'release-golive' input 'mode' defaults to 'release'"
    assert (needle in detail) is fails, detail
    assert ("'release-golive'" in detail) is fails, detail


def test_the_build_name_android_input_accepts_its_own_empty_default(tmp_path):
    """워크플랜 §11: `build_name_android` 는 기본값이 `""` 다. `pattern` 은 **기본값에도** 걸리므로
    (`core/inputs.validate_inputs` 가 기본값까지 `_coerce` 한다) 빈 값을 허용해야 한다 — 아니면
    이 입력을 안 보내는 회차마다 제출이 400 으로 튄다."""
    cfg_path = tmp_path / "server.toml"
    cfg_path.write_text(
        f'[server]\ndata_dir = "{tmp_path / "data"}"\n\n[[repos]]\nname = "app"\n'
        'url = "git@example.com:org/app.git"\n\n'
        + fill((STORE / "presets.release.toml").read_text())
    )
    cfg = load_server_config(cfg_path, environ={}, check_tools=False)
    for name in ("release-upload", "release-review"):
        preset = cfg.preset(name)
        # 기본값이 없는 입력(= rcm 이 늘 보내는 것)만 채운다 — 나머지는 프리셋 기본값으로 검증된다
        base: dict[str, object] = {
            spec.name: 181 if spec.type == "int" else "1.1.1"
            for spec in preset.inputs
            if spec.default is None
        }
        assert preset.input_spec("build_name_android").default == "", name
        assert validate_inputs(preset, base)["build_name_android"] == "", name
        assert (
            validate_inputs(preset, {**base, "build_name_android": "1.0.1"})["build_name_android"]
            == "1.0.1"
        ), name
        with pytest.raises(InputError, match="build_name_android"):
            validate_inputs(preset, {**base, "build_name_android": "1.0"})


@pytest.mark.parametrize("script", ["release_upload.sh", "release_review.sh"])
def test_each_store_gets_its_own_version_name(tmp_path, script):
    """워크플랜 §11: iOS 1.1.1 · Android 1.0.1 인 회차에서 훅은 플랫폼마다 그 스토어의 이름을
    받는다. 셀프테스트가 이미 잠그지만, 여기서도 진짜 스크립트를 돌려 호출 기록을 읽는다."""
    work = tmp_path / "work"
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    calls = tmp_path / "calls"
    common = {
        "RELEASE_SHIM": "ok",
        "RELEASE_SHIM_CALLS": str(calls),
        "RELEASE_WORK": str(work),
        SELFTEST_SECRETS_ENV: str(secrets),
        "RCM_INPUT_BUILD_NAME": "1.1.1",
        "RCM_INPUT_BUILD_NAME_ANDROID": "1.0.1",
        "RCM_INPUT_CONFIRM_BUILD_NUMBER": "181",
    }
    extra = (
        {"RCM_INPUT_MODE": "upload"}
        if script == "release_upload.sh"
        else {"RCM_INPUT_MODE": "submit", "RCM_INPUT_PLAY_MANAGED_PUBLISHING": "confirmed-on"}
    )
    rc, out = sh("bash", str(STORE / script), env={**common, **extra})
    assert rc == 0, out
    hook = "store_upload" if script == "release_upload.sh" else "store_submit"
    logged = calls.read_text().splitlines()
    assert [line.split()[:4] for line in logged if line.startswith(hook)] == [
        [hook, "ios", "181", "1.1.1"],
        [hook, "android", "181", "1.0.1"],
    ], logged
    # 안드로이드 이름이 비면 둘 다 대표 이름이다
    calls.unlink()
    rc, out = sh(
        "bash",
        str(STORE / script),
        env={**common, **extra, "RCM_INPUT_BUILD_NAME_ANDROID": ""},
    )
    assert rc == 0, out
    logged = calls.read_text().splitlines()
    assert [line.split()[:4] for line in logged if line.startswith(hook)] == [
        [hook, "ios", "181", "1.1.1"],
        [hook, "android", "181", "1.1.1"],
    ], logged


# ── 계약 문서 (AC-A8) ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    [
        r"^\| `version` \| optional \| `mode = prefill\\\|create\\\|delete`",
        r"`ios_version`, `android_version`",
        r"`asc_version_id`",
        r"`version\.json` \(create · delete\) / `prefill\.json` \(prefill · create\)",
        r"3 already exists",
        r"4 not deletable",
        r"^\*\*`listing_json`\*\*",
        r"next_version_hint",
        r"store\.play\.production_name",
        r"--version-id",
        r"stage \*\*`V`\*\*",
        r"^version_ttl_hours\s+= 24",
        r"^version = \"release-version\"",
        r"`mode` defaults to `prefill`",
        # 두 스토어 버전 이름 (워크플랜 §11)
        r"^### Two store version names",
        r"\*\*`build_name_android`\*\* \(`review` · `upload`; string, default `\"\"`\)",
        r"`build_name`, `build_name_android`, `confirm_build_number`",
        r"platform_build_name ios\|android",
        r"409 `split_version_unsupported`",
        r"`prod/1\.1\.1\+1\.0\.1-181`",
        r"`\+` is legal in a git tag name",
        r"tries the iOS name first and falls back to the Android name",
    ],
)
def test_contract_doc_states_the_version_role(pattern):
    text = CONTRACT_DOC.read_text()
    assert re.search(pattern, text, re.M), f"docs/release-contract.md lacks /{pattern}/"


@pytest.mark.parametrize(
    "path, pattern",
    [
        ("docs/configuration.md", r"^version_ttl_hours\s+= 24"),
        ("docs/configuration.md", r'^version = "release-version"'),
        ("docs/configuration.md", r"does not default to `prefill`"),
        ("docs/configuration.md", r"no `listing_json` input"),
        ("docs/configuration.md", r"no `build_name_android` input"),
        ("docs/configuration.md", r"`prod/1\.1\.1-181` or `prod/1\.1\.1\+1\.0\.1-181`"),
        ("CHANGELOG.md", r"`build_name_android`"),
        ("docs/configuration.md", r"gate, qa,\s+dev, version, status, golive\)"),
        ("examples/server.toml", r"^# version_ttl_hours = 24"),
        ("examples/server.toml", r'^# version = "release-version"'),
        ("CHANGELOG.md", r"`version` role"),
        ("CHANGELOG.md", r"`version_ttl_hours`"),
        ("CHANGELOG.md", r"`listing_json`"),
        ("CHANGELOG.md", r"`--version-id <id>`"),
    ],
)
def test_configuration_docs_and_changelog_mention_the_new_keys(path, pattern):
    text = (ROOT / path).read_text()
    assert re.search(pattern, text, re.M), f"{path} lacks /{pattern}/"
