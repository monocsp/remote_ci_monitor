"""서버가 자기 클라이언트 wheel 을 준다 (M5i I8-1 · I8-3 · 결정 81 · 83).

명세는 docs/gate-replay-fixes-workplan.md §3 I8. 잠그는 것:
- 조립한 wheel 의 파일 목록이 release.yml 의 산출물 검사와 같은 셋을 담고 RECORD 해시가 맞다.
- 새 venv 에 `pip install` 하면 `rcm version` 이 서버와 같고 `rcm --help` 가 뜬다(pip 없으면 skip).
- `/client/<정확한 파일명>` 만 200 · 다른 이름은 404 + hint · `If-None-Match` 304 · HEAD.
- 인증은 읽기 규칙(`read_auth = basic` 이면 토큰 없이 401).
- 조립 실패는 health `client_wheel: null` + `client_wheel_error`, 엔드포인트 503 — 옛 것·빈 것을
  주지 않는다(fail-open 금지).
- `min_client_version` · `rcm check` 의 `client` 행 · `rcm run` 의 경고 한 줄.
문서와 래퍼 예시(examples/session/update-client.sh)는 tests/test_docs_client_wheel.py 가 잠근다 —
mutcheck 의 복사본에는 docs/·examples/ 가 없어서 이 파일과 나눈다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import remote_ci_monitor.cli as cli_module
import remote_ci_monitor.server as server_module
from remote_ci_monitor import __version__
from remote_ci_monitor.cli import main
from remote_ci_monitor.clientwheel import (
    MIN_CLIENT_VERSION,
    WheelBuildError,
    build_wheel,
    wheel_filename,
)
from test_server import Server

ROOT = Path(__file__).resolve().parents[1]
WHEEL_NAME = f"remote_ci_monitor-{__version__}-py3-none-any.whl"
WHEEL_PATH = f"/client/{WHEEL_NAME}"
#: release.yml 이 릴리스 wheel 에서 확인하는 것과 같은 셋
RELEASE_CHECKED = ("web/index.html", "templates/server.toml", "templates/client.toml")
# `python -m venv` 가 pip 를 못 깔거나(ensurepip 없음) pip 가 없으면 실패가 아니라 skip —
# tests/test_packaging.py 와 같은 규칙.
NO_PIP_MARKERS = ("ensurepip", "No module named pip", "Could not", "No matching")


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """`use(srv, token)` — 그 서버·토큰을 환경변수로 건다(test_cli_m1 과 같은 꼴)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RCM_LABEL", raising=False)

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _fresh_venv(where: Path, *, with_pip: bool) -> Path:
    """새 venv 하나. pip 가 필요한데 못 깔면 skip."""
    args = [sys.executable, "-m", "venv"]
    if not with_pip:
        args.append("--without-pip")
    proc = subprocess.run([*args, str(where)], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        if any(m in proc.stderr for m in NO_PIP_MARKERS):
            pytest.skip(f"python -m venv could not create a venv with pip: {proc.stderr[-300:]}")
        raise AssertionError(proc.stderr)
    if with_pip and not (where / "bin" / "pip").exists():
        pytest.skip("the fresh venv has no pip")
    return where


def _pip_install(venv: Path, wheel: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        str(venv / "bin" / "python"),
        "-m",
        "pip",
        "install",
        "-q",
        "--disable-pip-version-check",
        "--no-deps",
        "--no-index",
        str(wheel),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _record_hash(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


# ── 조립 ────────────────────────────────────────────────────────────────────


def test_the_wheel_carries_the_package_the_assets_and_the_entry_point():
    data = build_wheel(__version__)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    for needed in RELEASE_CHECKED:
        assert f"remote_ci_monitor/{needed}" in names, needed
    assert "remote_ci_monitor/__init__.py" in names
    assert "remote_ci_monitor/cli.py" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")], names
    info = f"remote_ci_monitor-{__version__}.dist-info"
    ep = zf.read(f"{info}/entry_points.txt").decode()
    assert "rcm = remote_ci_monitor.cli:main" in ep
    meta = zf.read(f"{info}/METADATA").decode()
    assert f"\nVersion: {__version__}\n" in meta
    assert "Name: remote-ci-monitor" in meta
    wheel = zf.read(f"{info}/WHEEL").decode()
    assert "Wheel-Version: 1.0" in wheel and "Tag: py3-none-any" in wheel
    assert "Root-Is-Purelib: true" in wheel
    assert names[-1] == f"{info}/RECORD"  # RECORD 는 맨 끝(wheel 규격)


def test_the_record_hashes_match_the_zip_contents():
    data = build_wheel(__version__)
    zf = zipfile.ZipFile(io.BytesIO(data))
    info = f"remote_ci_monitor-{__version__}.dist-info"
    record = zf.read(f"{info}/RECORD").decode().splitlines()
    rows = {r.split(",")[0]: r.split(",")[1:] for r in record if r}
    assert set(rows) == set(zf.namelist())
    for name in zf.namelist():
        digest, size = rows[name]
        if name == f"{info}/RECORD":
            assert (digest, size) == ("", "")
            continue
        body = zf.read(name)
        assert digest == f"sha256={_record_hash(body)}", name
        assert size == str(len(body)), name


def test_the_wheel_is_deterministic():
    assert build_wheel(__version__) == build_wheel(__version__)


def test_the_wheel_is_named_after_the_version_it_is_asked_for():
    assert wheel_filename("0.9.1") == "remote_ci_monitor-0.9.1-py3-none-any.whl"
    zf = zipfile.ZipFile(io.BytesIO(build_wheel("0.9.1")))
    assert "remote_ci_monitor-0.9.1.dist-info/RECORD" in zf.namelist()
    meta = zf.read("remote_ci_monitor-0.9.1.dist-info/METADATA").decode()
    assert "\nVersion: 0.9.1\n" in meta  # 파일명과 METADATA 가 다르면 pip 가 거부한다


def test_missing_metadata_fails_closed(monkeypatch):
    """설치 메타데이터가 없으면 빈 wheel 이 아니라 오류다."""
    import importlib.metadata

    def gone(name: str):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", gone)
    with pytest.raises(WheelBuildError) as e:
        build_wheel(__version__)
    assert e.value.code == "metadata_missing"


def test_the_wheel_installs_into_a_fresh_venv(tmp_path):
    """조립 wheel 은 릴리스 wheel 과 바이트가 같지 않다 — 같아야 하는 것은 설치 결과다(§9)."""
    whl = tmp_path / WHEEL_NAME
    whl.write_bytes(build_wheel(__version__))
    venv = _fresh_venv(tmp_path / "venv", with_pip=True)
    proc = _pip_install(venv, whl)
    if proc.returncode != 0 and any(m in proc.stderr for m in NO_PIP_MARKERS):
        pytest.skip(f"pip could not install: {proc.stderr[-300:]}")
    assert proc.returncode == 0, proc.stderr
    rcm = venv / "bin" / "rcm"
    out = subprocess.run([str(rcm), "version"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.startswith(f"rcm {__version__} "), out
    assert subprocess.run([str(rcm), "--help"], capture_output=True, timeout=60).returncode == 0


# ── 엔드포인트 ──────────────────────────────────────────────────────────────


def test_health_names_the_wheel_and_the_oldest_accepted_client(srv):
    status, h = srv.req("GET", "/api/health")
    assert status == 200
    assert h["min_client_version"] == MIN_CLIENT_VERSION == "0.2.0"
    assert h["client_wheel_error"] is None
    cw = h["client_wheel"]
    assert cw["path"] == WHEEL_PATH
    assert re.fullmatch(r"[0-9a-f]{64}", cw["sha256"])
    assert cw["bytes"] > 100_000
    assert cw["sha256"] == hashlib.sha256(build_wheel(__version__)).hexdigest()


def test_the_exact_filename_answers_with_the_wheel(srv):
    status, headers, body = srv.req("GET", WHEEL_PATH, raw=True)
    assert status == 200, body[:200]
    assert headers["Content-Type"] == "application/zip"
    assert headers["Content-Length"] == str(len(body))
    assert headers["Content-Disposition"] == f'attachment; filename="{WHEEL_NAME}"'
    sha = hashlib.sha256(body).hexdigest()
    assert headers["ETag"] == f'"{sha}"'
    _, h = srv.req("GET", "/api/health")
    assert h["client_wheel"] == {"path": WHEEL_PATH, "sha256": sha, "bytes": len(body)}
    zf = zipfile.ZipFile(io.BytesIO(body))
    assert "remote_ci_monitor/web/index.html" in zf.namelist()


def test_the_same_bytes_are_served_for_the_life_of_the_process(srv):
    """기동 시점에 고정한다 — 요청마다 다시 조립하지 않는다."""
    a = srv.req("GET", WHEEL_PATH, raw=True)[2]
    b = srv.req("GET", WHEEL_PATH, raw=True)[2]
    assert a == b


def test_head_and_if_none_match(srv):
    status, headers, body = srv.req("HEAD", WHEEL_PATH, raw=True)
    assert status == 200 and body == b""
    assert int(headers["Content-Length"]) > 100_000
    etag = headers["ETag"]
    status, headers, body = srv.req("GET", WHEEL_PATH, raw=True, headers={"If-None-Match": etag})
    assert status == 304 and body == b"" and headers["ETag"] == etag
    status, _, body = srv.req("GET", WHEEL_PATH, raw=True, headers={"If-None-Match": '"nope"'})
    assert status == 200 and len(body) > 100_000


@pytest.mark.parametrize(
    "path",
    [
        "/client/remote_ci_monitor-0.0.1-py3-none-any.whl",
        "/client/remote_ci_monitor-99.0.0-py3-none-any.whl",
        f"/client/remote_ci_monitor-{__version__}-py3-none-any.tar.gz",
        f"/client/other-{__version__}-py3-none-any.whl",
        "/client/",
        "/client",
    ],
)
def test_any_other_name_is_404_with_the_right_name_in_the_hint(srv, path):
    status, body = srv.req("GET", path)
    assert status == 404, (path, body)
    assert body["error"] == "not found"
    assert WHEEL_PATH in body["hint"], body


def test_the_wheel_follows_the_read_rule(tmp_path):
    s = Server(tmp_path, workers=False, read_auth="basic")
    try:
        status, body = s.req("GET", WHEEL_PATH)
        assert status == 401, body
        assert s.req("GET", WHEEL_PATH, token="alice", raw=True)[0] == 200
        assert s.req("GET", "/api/health")[0] == 200  # health 는 열려 있다
    finally:
        s.close()


def test_a_wheel_that_cannot_be_built_is_null_and_503_never_an_old_one(tmp_path, monkeypatch):
    def boom(version: str) -> bytes:
        raise WheelBuildError("metadata_missing")

    monkeypatch.setattr(server_module, "build_wheel", boom)
    s = Server(tmp_path, workers=False)
    try:
        status, h = s.req("GET", "/api/health")
        assert status == 200 and h["ok"] is True  # 서버 건강과는 별개다(정보)
        assert h["client_wheel"] is None
        assert h["client_wheel_error"] == "metadata_missing"
        status, body = s.req("GET", WHEEL_PATH)
        assert status == 503, body
        assert "metadata_missing" in body["error"]
        assert body["client_wheel_error"] == "metadata_missing"
        assert s.req("HEAD", WHEEL_PATH, raw=True)[0] == 503
    finally:
        s.close()


def test_start_builds_the_wheel_once_and_unexpected_errors_are_a_code_too(tmp_path, monkeypatch):
    calls = []

    def count(version: str) -> bytes:
        calls.append(version)
        raise OSError("disk went away")

    monkeypatch.setattr(server_module, "build_wheel", count)
    s = Server(tmp_path, workers=True)
    try:
        assert calls == [__version__]
        _, h = s.req("GET", "/api/health")
        assert h["client_wheel"] is None
        assert h["client_wheel_error"] == "OSError"
        s.req("GET", WHEEL_PATH)
        assert calls == [__version__]  # 실패도 한 번만 — 요청마다 다시 시도하지 않는다
    finally:
        s.close()


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_check_says_the_client_is_the_same_as_the_server(srv, env, capsys):
    env(srv, "alice")
    code, out, _ = run(capsys, ["check"])
    assert code == 0, out
    assert re.search(rf"^ok   client\s+v{re.escape(__version__)} · same as server$", out, re.M)


def test_check_client_row_is_red_below_the_oldest_accepted_version(srv, env, capsys, monkeypatch):
    monkeypatch.setattr(cli_module, "__version__", "0.1.9")
    env(srv, "alice")
    code, out, _ = run(capsys, ["check"])
    assert code == 1, out
    url = f"http://127.0.0.1:{srv.port}{WHEEL_PATH}"
    line = f"FAIL  client        v0.1.9 · server v{__version__} · older — pip install {url}"
    assert line in out, out


def test_check_client_row_warns_when_older_but_still_accepted(srv, env, capsys, monkeypatch):
    monkeypatch.setattr(cli_module, "__version__", "0.2.0")
    env(srv, "alice")
    code, out, _ = run(capsys, ["check"])
    assert code == 0, out
    url = f"http://127.0.0.1:{srv.port}{WHEEL_PATH}"
    line = f"warn  client        v0.2.0 · server v{__version__} · older — pip install {url}"
    assert line in out, out


def test_check_client_row_does_not_invent_a_url_when_there_is_no_wheel(
    tmp_path, env, capsys, monkeypatch
):
    """조립에 실패한 서버(또는 `/client/` 가 없는 옛 서버)에 대고 있지도 않은 URL 을 찍지 않는다."""

    def boom(version: str) -> bytes:
        raise WheelBuildError("metadata_missing")

    monkeypatch.setattr(server_module, "build_wheel", boom)
    monkeypatch.setattr(cli_module, "__version__", "0.2.0")
    s = Server(tmp_path / "srv", workers=False)
    try:
        env(s, "alice")
        code, out, _ = run(capsys, ["check"])
        assert code == 0, out
        line = f"warn  client        v0.2.0 · server v{__version__} · older — no client wheel"
        assert f"{line} from this server (metadata_missing)" in out, out
        assert "/client/" not in out
    finally:
        s.close()


def test_check_client_row_says_newer_without_failing(srv, env, capsys, monkeypatch):
    monkeypatch.setattr(cli_module, "__version__", "9.0.0")
    env(srv, "alice")
    code, out, _ = run(capsys, ["check"])
    assert code == 0, out
    assert f"warn  client        v9.0.0 · server v{__version__} · newer" in out, out


def test_run_warns_once_on_stderr_when_the_client_is_too_old(srv, env, capsys, monkeypatch):
    monkeypatch.setattr(cli_module, "__version__", "0.1.9")
    env(srv, "alice")
    code, _, err = run(capsys, ["run", "no-such-preset"])
    assert code == 2
    warnings = [ln for ln in err.splitlines() if "older than the server accepts" in ln]
    assert len(warnings) == 1, err
    assert "v0.1.9" in warnings[0] and MIN_CLIENT_VERSION in warnings[0]
    assert f"pip install http://127.0.0.1:{srv.port}{WHEEL_PATH}" in warnings[0]


def test_run_stays_quiet_when_the_client_is_accepted(srv, env, capsys):
    env(srv, "alice")
    _, _, err = run(capsys, ["run", "no-such-preset"])
    assert "older than the server accepts" not in err


def test_version_json_stays_server_free(capsys, monkeypatch):
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:9")
    code, out, _ = run(capsys, ["version", "--json"])
    assert code == 0
    doc = json.loads(out.strip().splitlines()[-1])
    assert doc["version"] == __version__
    assert "server" not in doc and "min_client_version" not in doc
