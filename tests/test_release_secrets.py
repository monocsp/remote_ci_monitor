"""릴리스 비밀 저장소(`release_secrets.py`) — 스토어 탭 API 계약 v1 「Secrets」 · 계약 §4.

여기서 지키는 것:
- 값은 어떤 응답 항목에도 없다. 지문은 value 앞 4글자 + `…`, file 은 sha256 앞 8자리,
  dir 은 `n/m files`.
- 폴더 0700 · 파일 0600 · 임시 파일이 남지 않는다(원자적 쓰기).
- 이름은 프로파일과 글자까지 같아야 한다 — 비슷한 이름 · 경로 조각은 파일 시스템에 닿지 않는다.
- 삭제는 `optional` 만. 값을 바꾸면 검증 기록이 지워진다.
- 설정 게이트 셈: 필수만 세고, `complete` 는 전부 있고(verify none 이거나) 오류 없이 검증됐을 때.
- 마스킹: value 8자 이상만 `****`, 긴 값부터.
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor.config import RepoConfig, ServerConfig, parse_preset, parse_release_profile
from remote_ci_monitor.release_secrets import (
    MASK,
    VALUE_MAX_BYTES,
    SecretError,
    SecretStore,
    job_secrets,
    mask_bytes,
    masker,
    secrets_dir,
)

PROFILE_RAW = {
    "secrets_dir_env": "APP_SECRETS",
    "presets": {"plan": "release-plan", "upload": "release-upload", "review": "release-review"},
    "secrets": [
        {"name": "GH_TOKEN", "kind": "value", "verify": "github"},
        {"name": "AuthKey.p8", "kind": "file", "verify": "asc", "max_kb": 1},
        {"name": "upload-keystore.jks", "kind": "file", "verify": "keystore"},
        {"name": "TEAMS_WEBHOOK", "kind": "value", "optional": True},
        {
            "name": "review_information",
            "kind": "dir",
            "files": ["demo_user.txt", "demo_password.txt"],
        },
    ],
}
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
TOKEN = "ghp_" + "a1b2c3d4" * 4  # 36자 — 마스킹 대상


def profile():
    return parse_release_profile("app", PROFILE_RAW)


@pytest.fixture
def store(tmp_path: Path) -> SecretStore:
    return SecretStore(tmp_path / "secrets" / "app", profile(), now_fn=lambda: NOW)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── 읽기 모양 ────────────────────────────────────────────────────────────────


def test_an_absent_secret_is_present_false_with_nulls(store):
    item = store.item(store.secret("GH_TOKEN"))
    assert item == {
        "name": "GH_TOKEN",
        "kind": "value",
        "optional": False,
        "verify": "github",
        "present": False,
        "size": None,
        "fingerprint": None,
        "verified_at": None,
        "verify_error": None,
        "verify_detail": None,
    }


def test_a_value_is_fingerprinted_by_its_first_four_characters_and_never_returned(store):
    item = store.put("GH_TOKEN", (TOKEN + "\n").encode(), content_type="text/plain")
    assert item["present"] is True
    assert item["fingerprint"] == "ghp_…"
    assert item["size"] == len(TOKEN)  # 끝 개행은 벗긴다
    assert TOKEN not in repr(item) and TOKEN[4:] not in repr(item)
    assert (store.root / "GH_TOKEN").read_bytes() == TOKEN.encode()


def test_a_file_is_fingerprinted_by_sha256(store):
    import hashlib

    data = b"-----BEGIN PRIVATE KEY-----\nabc\n"
    item = store.put("AuthKey.p8", data, content_type="application/octet-stream")
    assert item["fingerprint"] == hashlib.sha256(data).hexdigest()[:8]
    assert item["size"] == len(data)
    assert data.decode() not in repr(item)


def test_a_dir_counts_its_files_and_is_present_only_when_all_are_there(store):
    sec = store.secret("review_information")
    assert store.item(sec)["fingerprint"] == "0/2 files"
    store.put("review_information", b"demo", content_type="text/plain", file_name="demo_user.txt")
    item = store.item(sec)
    assert item["present"] is False and item["fingerprint"] == "1/2 files"
    assert item["files"] == [
        {"name": "demo_user.txt", "present": True},
        {"name": "demo_password.txt", "present": False},
    ]
    store.put(
        "review_information",
        b"pw-1234567",
        content_type="application/octet-stream",
        file_name="demo_password.txt",
    )
    item = store.item(sec)
    assert item["present"] is True and item["fingerprint"] == "2/2 files"
    assert "pw-1234567" not in repr(item)


def test_view_carries_the_env_name_and_one_item_per_profile_secret(store):
    view = store.view()
    assert view["dir_env"] == "APP_SECRETS"
    assert [i["name"] for i in view["items"]] == [s["name"] for s in PROFILE_RAW["secrets"]]


# ── 파일 시스템 · 원자성 ─────────────────────────────────────────────────────


def test_dirs_are_0700_files_0600_and_no_temp_file_remains(store):
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    store.put("review_information", b"x", content_type="text/plain", file_name="demo_user.txt")
    assert mode(store.root) == 0o700
    assert mode(store.root.parent) == 0o700
    assert mode(store.root / "GH_TOKEN") == 0o600
    assert mode(store.root / "review_information") == 0o700
    assert mode(store.root / "review_information" / "demo_user.txt") == 0o600
    leftovers = [p.name for p in store.root.rglob(".tmp-*")]
    assert leftovers == []


def test_a_rewrite_replaces_the_whole_value_and_drops_the_old_verification(store):
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    store.record_verify("GH_TOKEN", None, detail="login: octocat")
    assert store.item(store.secret("GH_TOKEN"))["verified_at"] == "2026-09-17T12:00:00Z"
    item = store.put("GH_TOKEN", b"ghp_new", content_type="text/plain")
    assert (store.root / "GH_TOKEN").read_bytes() == b"ghp_new"
    assert item["verified_at"] is None and item["verify_detail"] is None


def test_a_failed_write_leaves_the_previous_value_in_place(store, monkeypatch):
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(SecretError) as ei:
        store.put("GH_TOKEN", b"ghp_other", content_type="text/plain")
    assert ei.value.status == 503
    assert "disk full" not in ei.value.message and str(store.root) not in ei.value.message
    assert (store.root / "GH_TOKEN").read_bytes() == TOKEN.encode()
    assert list(store.root.glob(".tmp-*")) == []


# ── 이름 · 종류 · 크기 검사 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["gh_token", "GH_TOKEN ", "../GH_TOKEN", "GH_TOKEN/x", ".verify.json", "", "nope"]
)
def test_only_the_exact_profile_name_is_accepted(store, name):
    with pytest.raises(SecretError) as ei:
        store.put(name, b"value-123", content_type="text/plain")
    assert ei.value.status == 404
    assert not (store.root or Path("/nonexistent")).exists()  # 파일 시스템에 닿지 않았다


def test_a_dir_file_must_be_in_the_profile_list(store):
    with pytest.raises(SecretError) as ei:
        store.put("review_information", b"x", content_type="text/plain", file_name="notes.txt")
    assert ei.value.status == 400
    with pytest.raises(SecretError) as ei:
        store.put("review_information", b"x", content_type="text/plain", file_name="../x")
    assert ei.value.status == 400
    with pytest.raises(SecretError) as ei:
        store.put("review_information", b"x", content_type="text/plain")
    assert ei.value.status == 400
    with pytest.raises(SecretError) as ei:
        store.put("GH_TOKEN", b"x", content_type="text/plain", file_name="a")
    assert ei.value.status == 400


def test_kind_and_content_type_must_agree(store):
    with pytest.raises(SecretError) as ei:
        store.put("GH_TOKEN", b"abc", content_type="application/octet-stream")
    assert ei.value.status == 400
    with pytest.raises(SecretError) as ei:
        store.put("AuthKey.p8", b"abc", content_type="text/plain")
    assert ei.value.status == 400
    # charset 파라미터는 괜찮다
    store.put("GH_TOKEN", b"abc-defgh", content_type="text/plain; charset=utf-8")


@pytest.mark.parametrize("body", [b"", b"\n", b"\r\n", b"a\nb\n", b"\xff\xfe"])
def test_a_value_is_one_non_empty_utf8_line(store, body):
    with pytest.raises(SecretError) as ei:
        store.put("GH_TOKEN", body, content_type="text/plain")
    assert ei.value.status == 400


def test_size_limits_are_413(store):
    with pytest.raises(SecretError) as ei:
        store.put("AuthKey.p8", b"x" * 1025, content_type="application/octet-stream")
    assert ei.value.status == 413
    store.put("AuthKey.p8", b"x" * 1024, content_type="application/octet-stream")
    with pytest.raises(SecretError) as ei:
        store.put("GH_TOKEN", b"x" * (VALUE_MAX_BYTES + 1), content_type="text/plain")
    assert ei.value.status == 413


def test_an_empty_file_is_rejected_and_never_counts_as_present(store):
    with pytest.raises(SecretError):
        store.put("AuthKey.p8", b"", content_type="application/octet-stream")
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "AuthKey.p8").write_bytes(b"")
    assert store.item(store.secret("AuthKey.p8"))["present"] is False


# ── 삭제 ────────────────────────────────────────────────────────────────────


def test_only_an_optional_secret_can_be_deleted(store):
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    with pytest.raises(SecretError) as ei:
        store.delete("GH_TOKEN")
    assert ei.value.status == 409
    assert (store.root / "GH_TOKEN").exists()
    store.put("TEAMS_WEBHOOK", b"https://hooks.example/abc", content_type="text/plain")
    item = store.delete("TEAMS_WEBHOOK")
    assert item["present"] is False and not (store.root / "TEAMS_WEBHOOK").exists()
    assert store.delete("TEAMS_WEBHOOK")["present"] is False  # 두 번째도 조용히


# ── 설정 게이트 ──────────────────────────────────────────────────────────────


def test_setup_counts_required_secrets_only_and_lists_what_is_missing(store):
    assert store.setup() == {
        "required": 4,
        "present": 0,
        "verified": 0,
        "complete": False,
        "missing": [
            "GH_TOKEN",
            "AuthKey.p8",
            "upload-keystore.jks",
            "review_information/demo_user.txt",
            "review_information/demo_password.txt",
        ],
    }
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    store.put("AuthKey.p8", b"k", content_type="application/octet-stream")
    store.put("upload-keystore.jks", b"j", content_type="application/octet-stream")
    store.put("review_information", b"u", content_type="text/plain", file_name="demo_user.txt")
    store.put("review_information", b"p", content_type="text/plain", file_name="demo_password.txt")
    s = store.setup()
    # dir 은 verify none → 있으면 곧 verified. 나머지 셋은 검증 전
    assert (s["required"], s["present"], s["verified"], s["complete"]) == (4, 4, 1, False)
    assert s["missing"] == []
    store.record_verify("GH_TOKEN", None, detail="login: octocat")
    store.record_verify("AuthKey.p8", "not implemented in this build")
    store.record_verify("upload-keystore.jks", None)
    s = store.setup()
    assert (s["verified"], s["complete"]) == (3, False)  # 오류가 남은 검증은 안 센다
    store.record_verify("AuthKey.p8", None)
    assert store.setup()["complete"] is True
    assert store.is_complete() is True


def test_the_optional_secret_never_blocks_the_gate(store):
    assert "TEAMS_WEBHOOK" not in store.setup()["missing"]


def test_a_verify_record_for_an_absent_secret_is_not_shown(store):
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    store.record_verify("GH_TOKEN", "HTTP 401")
    (store.root / "GH_TOKEN").unlink()
    item = store.item(store.secret("GH_TOKEN"))
    assert item["present"] is False and item["verified_at"] is None
    assert item["verify_error"] is None


def test_a_server_without_a_config_file_reads_nothing_and_refuses_writes():
    store = SecretStore(None, profile())
    assert all(i["present"] is False for i in store.items())
    assert store.setup()["complete"] is False
    assert store.mask_values() == ()
    with pytest.raises(SecretError) as ei:
        store.put("GH_TOKEN", b"abcdefghij", content_type="text/plain")
    assert ei.value.status == 503


# ── 마스킹 ───────────────────────────────────────────────────────────────────


def test_mask_values_are_values_of_eight_or_more_bytes_longest_first(store):
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    store.put("TEAMS_WEBHOOK", b"short", content_type="text/plain")  # 5자 — 제외
    store.put("AuthKey.p8", b"filecontent-long-enough", content_type="application/octet-stream")
    assert store.mask_values() == (TOKEN.encode(),)
    store.put("TEAMS_WEBHOOK", b"https://hooks.example/" + b"z" * 50, content_type="text/plain")
    vals = store.mask_values()
    assert len(vals) == 2 and len(vals[0]) >= len(vals[1])


def test_mask_bytes_replaces_every_occurrence_and_leaves_the_rest(store):
    data = b"token=" + TOKEN.encode() + b" again " + TOKEN.encode() + b"\nnext line\n"
    out = mask_bytes(data, (TOKEN.encode(),))
    assert out == b"token=" + MASK + b" again " + MASK + b"\nnext line\n"
    assert mask_bytes(b"nothing here\n", (TOKEN.encode(),)) == b"nothing here\n"
    assert masker(()) is None
    fn = masker((TOKEN.encode(),))
    assert fn is not None and fn(b"x" + TOKEN.encode()) == b"x" + MASK


# ── 잡이 받는 것 ────────────────────────────────────────────────────────────


def _config(tmp_path: Path, *, with_profile: bool = True) -> ServerConfig:
    cfg = ServerConfig()
    cfg.path = tmp_path / "server.toml"
    cfg.repos = (
        RepoConfig(
            name="app", url=str(tmp_path / "r.git"), release=profile() if with_profile else None
        ),
    )
    cfg.presets = (
        parse_preset(
            {
                "name": "release-plan",
                "argv": ["sh", "-c", "true"],
                "source_modes": ["git_ref"],
                "repo": "app",
            }
        ),
        parse_preset({"name": "ok", "argv": ["sh", "-c", "true"]}),
    )
    return cfg


def test_secrets_dir_sits_next_to_the_config_file(tmp_path):
    assert secrets_dir(tmp_path / "etc" / "server.toml", "app") == tmp_path / "etc/secrets/app"
    assert secrets_dir(None, "app") is None


def test_job_secrets_sets_the_env_only_when_the_folder_exists(tmp_path):
    cfg = _config(tmp_path)
    plan = cfg.preset("release-plan")
    assert job_secrets(cfg, plan) == ({}, None)
    store = SecretStore(secrets_dir(cfg.path, "app"), profile())
    store.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    env, mask = job_secrets(cfg, plan)
    assert env == {"APP_SECRETS": str(tmp_path / "secrets" / "app")}
    assert mask is not None and mask(b"x " + TOKEN.encode()) == b"x " + MASK
    # 프로파일 없는 저장소 · repo 없는 프리셋은 아무것도 안 받는다
    assert job_secrets(cfg, cfg.preset("ok")) == ({}, None)
    assert job_secrets(_config(tmp_path, with_profile=False), plan) == ({}, None)
