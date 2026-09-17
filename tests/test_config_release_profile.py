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
driver = "scripts/release/product_release.sh"
secrets_dir_env = "APP_SECRETS"

[repos.app.release.presets]
plan = "release-plan"
upload = "release-upload"
review = "release-review"
gate = "gate-smoke"

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
    assert prof.driver == "scripts/release/product_release.sh"
    assert prof.secrets_dir_env == "APP_SECRETS"
    assert prof.presets == {
        "plan": "release-plan",
        "upload": "release-upload",
        "review": "release-review",
        "gate": "gate-smoke",
    }
    assert prof.preset_for("qa") is None and prof.preset_for("plan") == "release-plan"
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
        ({"driver": "/usr/bin/release"}, "driver must be a relative path inside the repository"),
        ({"driver": "../release.sh"}, "driver must be a relative path"),
        ({"driver": ""}, "driver must be a relative path"),
        ({"secrets_dir_env": "app_secrets"}, "secrets_dir_env must match ^[A-Z][A-Z0-9_]*$"),
        ({"secrets_dir_env": "1APP"}, "secrets_dir_env must match"),
        ({"presets": ["plan"]}, "[repos.app.release.presets]: must be a table"),
        (
            {"presets": {"deploy": "x"}},
            "[repos.app.release.presets]: unknown key(s): deploy (roles are plan, upload, review",
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


def preset_block(name: str, script: str, inputs: str = "") -> str:
    return (
        f'[[presets]]\nname = "{name}"\nargv = ["bash", "scripts/{script}"]\n'
        f'source_modes = ["git_ref"]\nrepo = "app"\n{inputs}\n'
    )


def presets(upload_mode: str = "rehearsal", review_mode: str = "plan") -> str:
    upload = (
        "inputs = [\n"
        '  { name = "build_name" },\n'
        '  { name = "confirm_build_number", type = "int" },\n'
        + choice("mode", ["rehearsal", "upload"], upload_mode)
        + choice("platform", ["both", "ios", "android"], "both")
        + "]\n"
    )
    review = (
        "inputs = [\n"
        '  { name = "build_name" },\n'
        '  { name = "confirm_build_number", default = "" },\n'
        + choice("mode", ["plan", "submit"], review_mode)
        + choice("platform", ["both", "ios", "android"], "both")
        + choice("play_managed_publishing", ["not-checked", "confirmed-on"], "not-checked")
        + choice("listing", ["notes-only", "full"], "notes-only")
        + PHASED
        + "]\n"
    )
    return (
        preset_block("release-plan", "release/plan.sh", PLAN_INPUTS)
        + preset_block("release-upload", "release/upload.sh", upload)
        + preset_block("release-review", "release/review.sh", review)
        + preset_block("gate-smoke", "gate.sh")
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
        "qa=scenario-qa dev=deploy-dev · driver=scripts/release/product_release.sh · 1 secret(s)"
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


def test_check_prints_no_release_row_for_a_repo_without_a_profile(srv, env, tmp_path, capsys):
    env(srv)
    cfg = write_config(tmp_path, "", presets())
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    assert "release app" not in out
