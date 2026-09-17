"""비밀 검증기(`release_verify.py`) — 계약 §4 · API 계약 v1 「verify」.

여기서 지키는 것:
- `github` 는 `GET /user` 하나(헤더로만 토큰), 200 이면 `login: <이름>`, 아니면 `HTTP <코드>`.
- `keystore` 는 keytool 이 없으면 `keytool missing`(present 는 그대로), 있으면 `-list` 의 종료 코드.
- `asc` · `play` 는 「not implemented in this build」 — 오류가 아니라 detail 이다(관문을 막지 않는다).
- 결과 문구에 토큰 · 경로 · 예외 원문이 없다. 네트워크는 부르지 않는다(전부 스텁).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from remote_ci_monitor.config import ReleaseSecret
from remote_ci_monitor.release_verify import (
    GITHUB_USER_URL,
    NOT_IMPLEMENTED,
    VERIFIERS,
    VerifyResult,
    run_verify,
)

TOKEN = "ghp_" + "0123456789abcdef" * 2


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    p = tmp_path / "GH_TOKEN"
    p.write_text(TOKEN + "\n")
    return p


def github_secret() -> ReleaseSecret:
    return ReleaseSecret(name="GH_TOKEN", kind="value", verify="github")


def test_github_sends_the_token_as_a_bearer_header_and_reports_the_login(token_file):
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        return 200, b'{"login": "octocat", "id": 1}'

    result = run_verify(github_secret(), token_file, http_get=fake_get)
    assert result == VerifyResult(None, "login: octocat")
    assert result.ok
    assert calls == [
        (
            GITHUB_USER_URL,
            {
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "rcm",
            },
            5.0,
        )
    ]


@pytest.mark.parametrize("status", [401, 403, 500])
def test_github_reports_the_status_code_without_the_token(token_file, status):
    result = run_verify(github_secret(), token_file, http_get=lambda *_a: (status, b"nope"))
    assert result == VerifyResult(f"HTTP {status}")
    assert TOKEN not in repr(result)


def test_github_network_errors_are_named_by_type_only(token_file):
    def boom(url, headers, timeout):
        raise OSError(f"cannot reach {url} with {headers['Authorization']}")

    result = run_verify(github_secret(), token_file, http_get=boom)
    assert result.error == "network: OSError"
    assert TOKEN not in repr(result)


def test_github_with_an_unreadable_or_empty_token_does_not_call_out(tmp_path):
    empty = tmp_path / "GH_TOKEN"
    empty.write_text("\n")
    called = []
    result = run_verify(github_secret(), empty, http_get=lambda *a: called.append(a) or (200, b""))
    assert result.error == "token is empty" and called == []


def test_github_rejects_a_body_without_a_login(token_file):
    result = run_verify(github_secret(), token_file, http_get=lambda *_a: (200, b"not json"))
    assert result.error == "unexpected response"
    result = run_verify(github_secret(), token_file, http_get=lambda *_a: (200, b'{"id": 3}'))
    assert result.error == "unexpected response"


# ── keystore ─────────────────────────────────────────────────────────────────


def keystore_secret() -> ReleaseSecret:
    return ReleaseSecret(name="upload-keystore.jks", kind="file", verify="keystore")


def test_keystore_without_keytool_is_an_error_not_absence(tmp_path):
    ks = tmp_path / "upload-keystore.jks"
    ks.write_bytes(b"\xfe\xed\xfe\xed")
    result = run_verify(keystore_secret(), ks, which=lambda _n: None, run=None)
    assert result == VerifyResult("keytool missing")


def test_keystore_runs_keytool_list_with_the_sibling_password_when_there_is_one(tmp_path):
    ks = tmp_path / "upload-keystore.jks"
    ks.write_bytes(b"\xfe\xed\xfe\xed")
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        return subprocess.CompletedProcess(
            argv, 0, "upload, PrivateKeyEntry,\nca, trustedCertEntry\n", ""
        )

    result = run_verify(
        keystore_secret(),
        ks,
        which=lambda _n: "/usr/bin/keytool",
        run=fake_run,
        sibling_value=lambda name: "s3cret-pw" if name == "KEYSTORE_PASSWORD" else None,
    )
    assert result == VerifyResult(None, "1 key entry")
    (argv, kw), *_ = seen
    assert argv == ["/usr/bin/keytool", "-list", "-keystore", str(ks), "-storepass", "s3cret-pw"]
    assert kw["stdin"] is subprocess.DEVNULL and kw["capture_output"] is True
    # 비밀번호 없이도 돈다 — 그때는 -storepass 가 없다
    seen.clear()
    run_verify(keystore_secret(), ks, which=lambda _n: "keytool", run=fake_run)
    assert seen[0][0] == ["keytool", "-list", "-keystore", str(ks)]


def test_keystore_failure_reports_the_exit_code_only(tmp_path):
    ks = tmp_path / "upload-keystore.jks"
    ks.write_bytes(b"x")

    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", f"keytool error: {ks} is not a keystore")

    result = run_verify(keystore_secret(), ks, which=lambda _n: "keytool", run=fake_run)
    assert result == VerifyResult("keytool exit 1")
    assert str(ks) not in repr(result)

    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    assert run_verify(keystore_secret(), ks, which=lambda _n: "keytool", run=hang).error == (
        "keytool timed out"
    )


# ── asc · play · unknown · 검증기 예외 ───────────────────────────────────────


@pytest.mark.parametrize("kind", ["asc", "play"])
def test_asc_and_play_say_they_are_not_implemented(tmp_path, kind):
    f = tmp_path / "x"
    f.write_bytes(b"x")
    sec = ReleaseSecret(name="x", kind="file", verify=kind)
    assert run_verify(sec, f) == VerifyResult(None, NOT_IMPLEMENTED)


def test_every_declared_verify_kind_but_none_has_a_verifier():
    from remote_ci_monitor.config import RELEASE_VERIFY_KINDS

    assert set(VERIFIERS) == set(RELEASE_VERIFY_KINDS) - {"none"}


def test_an_unknown_kind_and_a_crashing_verifier_are_errors_not_exceptions(tmp_path):
    f = tmp_path / "x"
    f.write_bytes(b"x")
    sec = ReleaseSecret(name="x", kind="value", verify="github")

    def crash(ctx):
        raise RuntimeError(f"leaked {ctx.path}")

    result = run_verify(sec, f, verifiers={"github": crash})
    assert result.error == "verifier failed: RuntimeError" and str(f) not in result.error
    assert run_verify(sec, f, verifiers={}).error == "unknown verify kind 'github'"
