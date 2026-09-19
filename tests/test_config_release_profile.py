"""`[repos.<name>.release]` — 릴리스 프로파일 파싱과 `rcm check` 의 `release <repo>` 행.

명세: docs/release-contract.md §1 과 스킬 공동 명세 「Release profile schema」. 로더는 **모양**만
본다(타입 · 허용값 · 모르는 키 · 자리표시자). 프리셋이 있는지, 되돌릴 수 없는 모드가 기본값은
아닌지는 `rcm check` 의 몫이다 — 빠진 역할은 페이지를 흐리게 할 뿐 서버를 못 뜨게 하지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import test_cli_m4 as m4
from remote_ci_monitor.config import (
    ConfigError,
    ReleaseListing,
    ReleaseProfile,
    ReleaseSecret,
    load_server_config,
    parse_release_profile,
)
from test_cli_m4 import run

srv, env, home = m4.srv, m4.env, m4.home

FULL = """\
[[repos]]
name = "app"
url = "git@example.com:org/app.git"

[repos.app.release]
default_branch = "release"
tag = "store/{version}+{build}"
build_number_policy = "manual"
plan_max_age_minutes = 5
version_ttl_hours = 48
driver = "scripts/release/product_release.sh"
secrets_dir_env = "APP_SECRETS"

[repos.app.release.presets]
plan = "release-plan"
upload = "release-upload"
review = "release-review"
gate = "gate-smoke"
version = "release-version"

[[repos.app.release.secrets]]
name = "AuthKey.p8"
kind = "file"
verify = "asc"
max_kb = 16

[[repos.app.release.secrets]]
name = "TEAMS_WEBHOOK"
optional = true

[[repos.app.release.secrets]]
name = "review_information"
kind = "dir"
files = ["demo_user.txt", "demo_password.txt"]

[repos.app.release.listing]
preview = ["python3", "scripts/release/store_listing.py", "preview"]
screenshots = ["store/screenshots/**/*.png"]
release_notes = "store/release_notes/{version}/*.txt"

[[repos]]
name = "lib"
url = "git@example.com:org/lib.git"
"""


def load(tmp_path: Path, text: str):
    p = tmp_path / "server.toml"
    p.write_text(text)
    return load_server_config(p, environ={}, check_tools=False)


# ── 파싱 ─────────────────────────────────────────────────────────────────────


def test_full_profile_round_trips_every_key(tmp_path):
    cfg = load(tmp_path, FULL)
    app, lib = cfg.repos
    assert lib.release is None  # 프로파일은 자기 [[repos]] 에만 붙는다
    prof = app.release
    assert isinstance(prof, ReleaseProfile)
    assert prof.default_branch == "release"
    assert prof.tag == "store/{version}+{build}"
    assert prof.build_number_policy == "manual"
    assert prof.plan_max_age_minutes == 5
    assert prof.version_ttl_hours == 48
    assert prof.driver == "scripts/release/product_release.sh"
    assert prof.secrets_dir_env == "APP_SECRETS"
    assert prof.presets == {
        "plan": "release-plan",
        "upload": "release-upload",
        "review": "release-review",
        "gate": "gate-smoke",
        "version": "release-version",
    }
    assert prof.preset_for("qa") is None and prof.preset_for("plan") == "release-plan"
    assert prof.preset_for("version") == "release-version"
    assert prof.secrets == (
        ReleaseSecret(name="AuthKey.p8", kind="file", verify="asc", max_kb=16),
        ReleaseSecret(name="TEAMS_WEBHOOK", optional=True),
        ReleaseSecret(
            name="review_information", kind="dir", files=("demo_user.txt", "demo_password.txt")
        ),
    )
    assert prof.listing == ReleaseListing(
        preview=("python3", "scripts/release/store_listing.py", "preview"),
        screenshots=("store/screenshots/**/*.png",),
        release_notes="store/release_notes/{version}/*.txt",
    )


def test_listing_ref_names_the_branch_the_copy_is_read_from():
    prof = parse_release_profile(
        "app", {"presets": {"plan": "p", "upload": "u", "review": "r"}, "listing": {"ref": "dev"}}
    )
    assert prof.listing is not None and prof.listing.ref == "dev"
    assert parse_release_profile("app", {"listing": {}}).listing.ref is None


def test_defaults_when_only_the_presets_are_given():
    prof = parse_release_profile("app", {"presets": {"plan": "p", "upload": "u", "review": "r"}})
    assert prof.default_branch == "main"
    assert prof.tag == "prod/{version}-{build}"
    assert prof.build_number_policy == "auto"
    assert prof.plan_max_age_minutes == 30
    assert prof.version_ttl_hours == 24  # 버전 페이지 계획 Q1: 하루
    assert prof.driver is None and prof.secrets_dir_env is None
    assert prof.secrets == () and prof.listing is None
    sec = parse_release_profile("app", {"secrets": [{"name": "X"}]}).secrets[0]
    assert (sec.kind, sec.optional, sec.verify, sec.files, sec.max_kb) == (
        "value",
        False,
        "none",
        (),
        512,
    )


def test_release_is_also_accepted_as_a_direct_key(tmp_path):
    """`[repos.release]` 라고 써도 마지막 [[repos]] 원소의 `release` 키다 — 같은 뜻으로 받는다."""
    cfg = load(
        tmp_path,
        '[[repos]]\nname = "app"\nurl = "git@example.com:org/app.git"\n'
        '[repos.release]\ndefault_branch = "trunk"\n',
    )
    assert cfg.repos[0].release is not None and cfg.repos[0].release.default_branch == "trunk"


def test_repo_without_a_profile_is_unchanged(tmp_path):
    cfg = load(tmp_path, '[[repos]]\nname = "app"\nurl = "git@example.com:org/app.git"\n')
    assert cfg.repos[0].release is None


@pytest.mark.parametrize(
    "text, needle",
    [
        # 프로파일 말고 다른 키는 여전히 안 된다
        ('[[repos]]\nname = "app"\nurl = "u"\nbranch = "x"\n', "needs exactly 'name' and 'url'"),
        # 다른 저장소 이름 아래의 release — 자기 이름이 아니다
        ('[[repos]]\nname = "app"\nurl = "u"\n[repos.other.release]\n', "[repos.app.release]"),
        # 자기 이름 아래에 release 말고 다른 것
        ('[[repos]]\nname = "app"\nurl = "u"\n[repos.app.extra]\n', "extra key(s): app"),
    ],
)
def test_other_extra_keys_on_a_repo_are_still_refused(tmp_path, text, needle):
    with pytest.raises(ConfigError, match=re.escape(needle)):
        load(tmp_path, text)


@pytest.mark.parametrize(
    "raw, needle",
    [
        ({"colour": "red"}, "[repos.app.release]: unknown key(s): colour"),
        ({"default_branch": ""}, "default_branch"),
        ({"default_branch": 3}, "default_branch"),
        ({"tag": "prod/{version}"}, "tag must be a string containing {version} and {build}"),
        ({"tag": 7}, "tag must be"),
        ({"build_number_policy": "guess"}, 'build_number_policy must be "auto" or "manual"'),
        ({"plan_max_age_minutes": 0}, "plan_max_age_minutes must be a positive integer"),
        ({"plan_max_age_minutes": True}, "plan_max_age_minutes must be a positive integer"),
        ({"plan_max_age_minutes": "30"}, "plan_max_age_minutes must be a positive integer"),
        (
            {"version_ttl_hours": 0},
            "[repos.app.release]: version_ttl_hours must be a positive integer",
        ),
        ({"version_ttl_hours": True}, "version_ttl_hours must be a positive integer"),
        ({"version_ttl_hours": "24"}, "version_ttl_hours must be a positive integer"),
        ({"version_ttl_hours": 1.5}, "version_ttl_hours must be a positive integer"),
        ({"driver": "/usr/bin/release"}, "driver must be a relative path inside the repository"),
        ({"driver": "../release.sh"}, "driver must be a relative path"),
        ({"driver": ""}, "driver must be a relative path"),
        ({"secrets_dir_env": "app_secrets"}, "secrets_dir_env must match ^[A-Z][A-Z0-9_]*$"),
        ({"secrets_dir_env": "1APP"}, "secrets_dir_env must match"),
        ({"presets": ["plan"]}, "[repos.app.release.presets]: must be a table"),
        (
            {"presets": {"deploy": "x"}},
            "[repos.app.release.presets]: unknown key(s): deploy "
            "(roles are plan, upload, review, gate, qa, dev, version)",
        ),
        ({"presets": {"plan": ""}}, "[repos.app.release.presets]: plan must be a preset name"),
        ({"presets": {"upload": 3}}, "[repos.app.release.presets]: upload must be a preset name"),
        ({"secrets": {"name": "x"}}, "[[repos.app.release.secrets]]: must be an array of tables"),
        ({"secrets": ["x"]}, "[[repos.app.release.secrets]]: each secret must be a table"),
        ({"secrets": [{"kind": "file"}]}, "secret 'name' must be a file or env name"),
        ({"secrets": [{"name": "a/b"}]}, "secret 'name' must be a file or env name"),
        ({"secrets": [{"name": ".."}]}, "secret 'name' must be a file or env name"),
        (
            {"secrets": [{"name": "K", "mime": "x"}]},
            "[[repos.app.release.secrets]] 'K': unknown key(s): mime",
        ),
        ({"secrets": [{"name": "K", "kind": "blob"}]}, "'K': kind must be one of value, file, dir"),
        ({"secrets": [{"name": "K", "optional": "yes"}]}, "'K': optional must be true or false"),
        ({"secrets": [{"name": "K", "verify": "apple"}]}, "'K': verify must be one of asc, play"),
        (
            {"secrets": [{"name": "K", "files": ["a"]}]},
            "'K': 'files' is only valid for kind = \"dir\"",
        ),
        ({"secrets": [{"name": "K", "kind": "dir", "files": []}]}, "'K' files: must not be empty"),
        (
            {"secrets": [{"name": "K", "kind": "dir", "files": ["a/b"]}]},
            "'K': files entries must be plain file names",
        ),
        (
            {"secrets": [{"name": "K", "kind": "value", "max_kb": 1}]},
            "'K': 'max_kb' is only valid for kind = \"file\"",
        ),
        (
            {"secrets": [{"name": "K", "kind": "file", "max_kb": 0}]},
            "'K': max_kb must be a positive integer",
        ),
        ({"listing": "x"}, "[repos.app.release.listing]: must be a table"),
        ({"listing": {"thumbs": []}}, "[repos.app.release.listing]: unknown key(s): thumbs"),
        ({"listing": {"preview": []}}, "[repos.app.release.listing] preview: must not be empty"),
        ({"listing": {"diff": "cmd"}}, "[repos.app.release.listing] diff: expected a list"),
        ({"listing": {"screenshots": [1]}}, "[repos.app.release.listing] screenshots"),
        ({"listing": {"release_notes": 1}}, "release_notes must be a string"),
        ({"listing": {"ref": ""}}, "[repos.app.release.listing]: ref must be a branch name"),
        ({"listing": {"ref": "a b"}}, "ref must be a branch name"),
        ({"listing": {"ref": 3}}, "ref must be a branch name"),
    ],
)
def test_each_bad_key_names_the_section_and_the_key(raw, needle):
    with pytest.raises(ConfigError, match=re.escape(needle)):
        parse_release_profile("app", raw)


def test_profile_loader_does_not_cross_check_presets_or_secrets():
    """빠진 역할 · 없는 프리셋 · 겹친 비밀 이름 · secrets_dir_env 없음 — 로더는 받아들이고
    `rcm check` 가 말한다(계약 「degrades the page, never the server」)."""
    prof = parse_release_profile(
        "app",
        {
            "presets": {"plan": "nowhere"},
            "secrets": [{"name": "K"}, {"name": "K"}],
        },
    )
    assert prof.preset_for("upload") is None
    assert [s.name for s in prof.secrets] == ["K", "K"]
    assert prof.secrets_dir_env is None


# ── rcm check ────────────────────────────────────────────────────────────────

CHECK_HEAD = """\
[server]
data_dir = "{data_dir}"

[[repos]]
name = "app"
url = "git@example.com:org/app.git"

"""


def choice(name: str, choices: list[str], default: str) -> str:
    """TOML 인라인 테이블 한 줄 — 한 줄이어야 한다(TOML 1.0), 그래서 문자열이 아니라 함수다."""
    opts = ", ".join(f'"{c}"' for c in choices)
    return f'  {{ name = "{name}", type = "choice", choices = [{opts}], default = "{default}" }},\n'


PLAN_INPUTS = 'inputs = [{ name = "build_name", pattern = "^\\\\d+\\\\.\\\\d+\\\\.\\\\d+$" }]\n'
PHASED = choice("phased", ["1", "0"], "1")
LISTING_JSON = '  { name = "listing_json", default = "" },\n'
BUILD_NAME_ANDROID = '  { name = "build_name_android", default = "" },\n'


def preset_block(name: str, script: str, inputs: str = "") -> str:
    return (
        f'[[presets]]\nname = "{name}"\nargv = ["bash", "scripts/{script}"]\n'
        f'source_modes = ["git_ref"]\nrepo = "app"\n{inputs}\n'
    )


VERSION_MODES = ["prefill", "create", "delete"]


def version_inputs(mode_default: str = "prefill", modes: list[str] | None = None) -> str:
    """`version` 프리셋의 입력 넷 — rcm 이 보내는 이름 그대로(계약 §2)."""
    return (
        "inputs = [\n"
        + choice("mode", modes or VERSION_MODES, mode_default)
        + '  { name = "ios_version", default = "" },\n'
        + '  { name = "android_version", default = "" },\n'
        + '  { name = "asc_version_id", default = "" },\n'
        + "]\n"
    )


def presets(
    upload_mode: str = "rehearsal",
    review_mode: str = "plan",
    listing_json: bool = True,
    version_mode: str | None = "prefill",
    build_name_android: bool = True,
    version_modes: list[str] | None = None,
) -> str:
    listing = LISTING_JSON if listing_json else ""
    android = BUILD_NAME_ANDROID if build_name_android else ""
    upload = (
        "inputs = [\n"
        '  { name = "build_name" },\n'
        + android
        + '  { name = "confirm_build_number", type = "int" },\n'
        + choice("mode", ["rehearsal", "upload"], upload_mode)
        + choice("platform", ["both", "ios", "android"], "both")
        + listing
        + "]\n"
    )
    review = (
        "inputs = [\n"
        '  { name = "build_name" },\n'
        + android
        + '  { name = "confirm_build_number", default = "" },\n'
        + choice("mode", ["plan", "submit"], review_mode)
        + choice("platform", ["both", "ios", "android"], "both")
        + choice("play_managed_publishing", ["not-checked", "confirmed-on"], "not-checked")
        + choice("listing", ["notes-only", "full"], "notes-only")
        + PHASED
        + listing
        + "]\n"
    )
    version = (
        ""
        if version_mode is None
        else preset_block(
            "release-version", "release/version.sh", version_inputs(version_mode, version_modes)
        )
    )
    return (
        preset_block("release-plan", "release/plan.sh", PLAN_INPUTS)
        + preset_block("release-upload", "release/upload.sh", upload)
        + preset_block("release-review", "release/review.sh", review)
        + preset_block("gate-smoke", "gate.sh")
        + version
    )


def write_config(tmp_path: Path, profile: str, presets_text: str) -> Path:
    p = tmp_path / "server.toml"
    p.write_text(
        CHECK_HEAD.replace("{data_dir}", str(tmp_path / "check-data")) + profile + presets_text
    )
    return p


def release_row(out: str) -> tuple[str, str]:
    """`release app` 행의 (상태, 상세). 상태는 ok · warn · FAIL."""
    m = re.search(r"^(ok |warn|FAIL)  release app\s+(.*)$", out, re.M)
    assert m, out
    return m.group(1).strip(), m.group(2)


GOOD_PROFILE = """\
[repos.app.release]
driver = "scripts/release/product_release.sh"
secrets_dir_env = "APP_SECRETS"
[repos.app.release.presets]
plan = "release-plan"
upload = "release-upload"
review = "release-review"
gate = "gate-smoke"
qa = "scenario-qa"
dev = "deploy-dev"
version = "release-version"
[[repos.app.release.secrets]]
name = "AuthKey.p8"
kind = "file"

[[presets]]
name = "scenario-qa"
argv = ["bash", "scripts/qa.sh"]
source_modes = ["git_ref"]
repo = "app"

[[presets]]
name = "deploy-dev"
argv = ["bash", "scripts/deploy_dev.sh"]
source_modes = ["git_ref"]
repo = "app"

"""


def test_check_row_is_ok_when_every_role_and_secret_is_in_place(srv, env, tmp_path, capsys):
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, presets())
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    status, detail = release_row(out)
    assert status == "ok"
    assert detail.startswith(
        "plan=release-plan upload=release-upload review=release-review gate=gate-smoke "
        "qa=scenario-qa dev=deploy-dev version=release-version "
        "· driver=scripts/release/product_release.sh · 1 secret(s)"
    ), detail


def test_check_row_warns_about_unset_optional_roles_and_a_missing_secrets_dir(
    srv, env, tmp_path, capsys
):
    """선택 역할이 비었거나 비밀 폴더가 아직 없으면 warn — 종료 코드는 0 이다."""
    env(srv)
    profile = (
        '[repos.app.release]\nsecrets_dir_env = "APP_SECRETS"\n'
        '[repos.app.release.presets]\nplan = "release-plan"\nupload = "release-upload"\n'
        'review = "release-review"\n[[repos.app.release.secrets]]\nname = "K"\n\n'
    )
    cfg = write_config(tmp_path, profile, presets())
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    status, detail = release_row(out)
    assert status == "warn"
    assert "gate not configured" in detail and "qa not configured" in detail, detail
    assert "driver=" not in detail  # 프로파일에 driver 가 없으면 행에도 없다
    assert detail.startswith(
        "plan=release-plan upload=release-upload review=release-review · 1 secret(s)"
    )
    assert "dev not configured" in detail
    assert "version not configured" in detail  # 선택 역할 — 없으면 새 버전 만들기가 즉시 editing
    assert f"secrets dir {tmp_path / 'secrets' / 'app'} does not exist yet" in detail, detail


def test_check_row_fails_when_a_required_role_is_empty_or_names_a_missing_preset(
    srv, env, tmp_path, capsys
):
    env(srv)
    profile = (
        "[repos.app.release]\n[repos.app.release.presets]\n"
        'plan = "release-plan"\nupload = "nowhere"\n\n'
    )
    cfg = write_config(tmp_path, profile, presets())
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    status, detail = release_row(out)
    assert status == "FAIL"
    assert "presets.upload = 'nowhere' is not in [[presets]]" in detail, detail
    assert "presets.review is empty" in detail, detail


@pytest.mark.parametrize(
    "kwargs, needle",
    [
        (
            {"upload_mode": "upload"},
            "preset 'release-upload' input 'mode' defaults to 'upload' — "
            "the irreversible mode must not be the default",
        ),
        (
            {"review_mode": "submit"},
            "preset 'release-review' input 'mode' defaults to 'submit' — "
            "the irreversible mode must not be the default",
        ),
    ],
)
def test_check_row_fails_when_the_irreversible_mode_is_the_default(
    srv, env, tmp_path, capsys, kwargs, needle
):
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, presets(**kwargs))
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    status, detail = release_row(out)
    assert status == "FAIL" and needle in detail, detail


def test_check_row_fails_when_a_preset_lacks_the_inputs_rcm_sends(srv, env, tmp_path, capsys):
    env(srv)
    bare = presets().replace(PLAN_INPUTS, "").replace(PHASED, "")
    cfg = write_config(tmp_path, GOOD_PROFILE, bare)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    status, detail = release_row(out)
    assert status == "FAIL"
    assert "preset 'release-plan' lacks inputs rcm sends: build_name" in detail, detail
    assert "preset 'release-review' lacks inputs rcm sends: phased" in detail, detail


def test_check_row_fails_on_secrets_without_env_and_on_duplicate_names(srv, env, tmp_path, capsys):
    env(srv)
    profile = (
        "[repos.app.release]\n[repos.app.release.presets]\n"
        'plan = "release-plan"\nupload = "release-upload"\nreview = "release-review"\n'
        '[[repos.app.release.secrets]]\nname = "K"\n[[repos.app.release.secrets]]\nname = "K"\n\n'
    )
    cfg = write_config(tmp_path, profile, presets())
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    status, detail = release_row(out)
    assert status == "FAIL"
    assert "duplicate secret name(s): K" in detail, detail
    assert "secrets_dir_env is required when secrets are listed" in detail, detail


@pytest.mark.parametrize("bad_default", ["create", "delete"])
def test_check_row_fails_when_the_version_preset_does_not_default_to_prefill(
    srv, env, tmp_path, capsys, bad_default
):
    """AC-A2: create 는 스토어에 드래프트를 만들고 delete 는 되돌릴 수 없다 — 기본은 읽기 전용
    prefill 이어야 하고, FAIL 문구가 «prefill» 을 말한다."""
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, presets(version_mode=bad_default))
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    status, detail = release_row(out)
    assert status == "FAIL"
    assert (
        f"preset 'release-version' input 'mode' defaults to {bad_default!r} — "
        "it must default to 'prefill' (the read-only mode)"
    ) in detail, detail


def test_check_row_fails_when_the_version_preset_lacks_the_four_inputs(srv, env, tmp_path, capsys):
    """서버 단계는 이 이름 넷을 그대로 보낸다 — 하나라도 없으면 제출이 튕긴다."""
    env(srv)
    bare = presets().replace(version_inputs(), "")
    cfg = write_config(tmp_path, GOOD_PROFILE, bare)
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    status, detail = release_row(out)
    assert status == "FAIL"
    assert (
        "preset 'release-version' lacks inputs rcm sends: "
        "mode, ios_version, android_version, asc_version_id"
    ) in detail, detail


def test_check_row_warns_once_per_preset_without_listing_json(srv, env, tmp_path, capsys):
    """AC-A2: review/upload 에 `listing_json` 이 없으면 warn — 웹에서 편집한 문안이 스크립트에
    닿지 않는다. FAIL 은 아니다(옛 프로젝트도 스토어 탭은 열린다)."""
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, presets(listing_json=False))
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    status, detail = release_row(out)
    assert status == "warn"
    for name in ("release-review", "release-upload"):
        assert (
            f"preset '{name}' has no listing_json input — copy edited in the web UI "
            "will not reach it (re-run /rcm-store-connect)"
        ) in detail, detail
    assert detail.count("listing_json") == 2, detail
    # 프리셋 하나만 고치면 그 경고만 사라진다
    half = presets(listing_json=False).replace(
        choice("platform", ["both", "ios", "android"], "both") + "]\n",
        choice("platform", ["both", "ios", "android"], "both") + LISTING_JSON + "]\n",
        1,
    )
    cfg = write_config(tmp_path, GOOD_PROFILE, half)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    status, detail = release_row(out)
    assert status == "warn" and "preset 'release-upload' has no listing_json" not in detail
    assert "preset 'release-review' has no listing_json" in detail, detail


def test_check_row_warns_once_per_preset_without_build_name_android(srv, env, tmp_path, capsys):
    """워크플랜 §11: 한 회차가 두 스토어에 **서로 다른** 버전 이름으로 나갈 수 있으려면
    review/upload 프리셋이 `build_name_android` 를 받아야 한다. 없으면 warn 한 줄 — FAIL 은
    아니다(두 이름을 늘 같게 쓰는 프로젝트는 그대로 두면 된다)."""
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, presets(build_name_android=False))
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    status, detail = release_row(out)
    assert status == "warn"
    for name in ("release-review", "release-upload"):
        assert (
            f"preset '{name}' has no build_name_android input — the two stores must then "
            "share one version name (re-run /rcm-store-connect)"
        ) in detail, detail
    assert detail.count("build_name_android") == 2, detail
    # 프리셋 하나만 고치면 그 경고만 사라진다
    half = presets(build_name_android=False).replace(
        '  { name = "build_name" },\n',
        '  { name = "build_name" },\n' + BUILD_NAME_ANDROID,
        1,
    )
    cfg = write_config(tmp_path, GOOD_PROFILE, half)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    status, detail = release_row(out)
    assert status == "warn" and "preset 'release-upload' has no build_name_android" not in detail
    assert "preset 'release-review' has no build_name_android" in detail, detail


def test_check_row_says_nothing_when_both_presets_declare_build_name_android(
    srv, env, tmp_path, capsys
):
    """입력이 있으면 행은 조용하다 — 경고가 나는 쪽만 말한다."""
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, presets())
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    status, detail = release_row(out)
    assert status == "ok" and "build_name_android" not in detail, detail


#: `mode` 가 자유 문자열인 version 프리셋의 입력 — 선택지를 안 걸었으니 어떤 값도 받는다.
FREE_MODE_INPUTS = (
    "inputs = [\n"
    '  { name = "mode", default = "prefill" },\n'
    '  { name = "ios_version", default = "" },\n'
    '  { name = "android_version", default = "" },\n'
    '  { name = "asc_version_id", default = "" },\n'
    "]\n"
)


@pytest.mark.parametrize(
    ("modes", "missing"),
    [(["prefill"], "create, delete"), (["prefill", "create"], "delete")],
)
def test_check_row_warns_when_the_version_mode_cannot_take_create_and_delete(
    srv, env, tmp_path, capsys, modes, missing
):
    """워크플랜 §13 3: `choices = ["prefill"]` 인 프리셋도 기본값 검사는 통과한다. 서버는
    `create`·`delete` 를 보내므로 «새 버전 만들기» 가 제출 순간 400 으로만 드러난다 — warn 한
    줄로 미리 말한다. FAIL 은 아니다(스토어 탭의 나머지는 그대로 돈다)."""
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, presets(version_modes=modes))
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    status, detail = release_row(out)
    assert status == "warn"
    assert (
        f"preset 'release-version' input 'mode' cannot take {missing} — "
        "rcm sends them and the job will be refused (re-run /rcm-store-connect)"
    ) in detail, detail


@pytest.mark.parametrize("text", [presets(), presets().replace(version_inputs(), FREE_MODE_INPUTS)])
def test_check_row_says_nothing_when_the_version_mode_can_take_them(
    srv, env, tmp_path, capsys, text
):
    """선택지가 다 있거나 아예 없으면(자유 문자열) 조용하다 — 후자는 어떤 값도 받는다."""
    env(srv)
    cfg = write_config(tmp_path, GOOD_PROFILE, text)
    (tmp_path / "secrets" / "app").mkdir(parents=True)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    status, detail = release_row(out)
    assert status == "ok" and "cannot take" not in detail, detail


def test_check_prints_no_release_row_for_a_repo_without_a_profile(srv, env, tmp_path, capsys):
    env(srv)
    cfg = write_config(tmp_path, "", presets())
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    assert "release app" not in out
