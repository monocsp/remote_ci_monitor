"""문서·래퍼 예시 잠금 — 서버가 주는 클라이언트 wheel (M5i I8 · 결정 81·83).

tests/test_client_wheel.py 가 코드를, 이 파일이 docs/operating.md 의 절 · usage 안내 · CHANGELOG ·
scripts/mutcheck.py 의 변이 · examples/session/update-client.sh(실제로 돌린다 — 해시가 다르면
설치하지 않는다 · 실패의 종료 코드를 보존한다)를 잠근다. 문서 잠금은 mutcheck 의 복사본(src ·
tests · pyproject.toml 만)에는 없는 파일을 읽으므로 코드 테스트와 나눠 둔다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor.clientwheel import MIN_CLIENT_VERSION, build_wheel
from test_client_wheel import NO_PIP_MARKERS, WHEEL_PATH, _fresh_venv
from test_server import Server

ROOT = Path(__file__).resolve().parents[1]
OPERATING = ROOT / "docs" / "operating.md"
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
CHANGELOG = ROOT / "CHANGELOG.md"
UPDATE_SH = ROOT / "examples" / "session" / "update-client.sh"
MUTCHECK = ROOT / "scripts" / "mutcheck.py"


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^#{{2,3}}\s+{re.escape(heading)}\s*$", text, re.M)
    assert m, f"no '{heading}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_operating_docs_explain_how_a_client_follows_the_server():
    sec = _section(OPERATING.read_text(), "Keeping clients on the server's version")
    assert "client_wheel" in sec and "min_client_version" in sec
    assert "/client/remote_ci_monitor-" in sec and "sha256" in sec
    assert "pip install" in sec and "rcm check" in sec
    assert "examples/session/update-client.sh" in sec
    assert "read_auth" in sec  # 읽기 규칙을 따른다는 말


def test_usage_guides_point_at_the_server_wheel():
    for path in (USAGE, USAGE_KO):
        text = path.read_text()
        assert "/client/remote_ci_monitor-" in text, path.name
        assert "operating.md#keeping-clients-on-the-servers-version" in text, path.name


def test_changelog_has_the_entry():
    unreleased = _section(CHANGELOG.read_text(), "[Unreleased]")
    assert "/client/" in unreleased and "min_client_version" in unreleased


def test_mutcheck_lists_the_exact_name_mutant():
    text = MUTCHECK.read_text()
    assert 'name="client-wheel-any-name"' in text
    assert 'tests=("tests/test_client_wheel.py",)' in text


needs_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None or shutil.which("jq") is None,
    reason="bash, curl and jq are needed to run the wrapper example",
)


def _run_wrapper(server_url: str, venv: Path, tmpdir: Path) -> subprocess.CompletedProcess[str]:
    """래퍼를 돌린다. 임시 파일은 `tmpdir` 에만 — 끝난 뒤 비어 있어야 한다."""
    tmpdir.mkdir(exist_ok=True)
    env_ = {
        **os.environ,
        "RCM_SERVER": server_url,
        "RCM_VENV": str(venv),
        "TMPDIR": str(tmpdir),
        "PATH": f"{venv / 'bin'}:{os.environ.get('PATH', '')}",
    }
    env_.pop("RCM_TOKEN", None)
    return subprocess.run(
        ["bash", str(UPDATE_SH)], capture_output=True, text=True, timeout=600, env=env_
    )


@needs_tools
def test_the_wrapper_example_brings_a_fresh_venv_to_the_servers_version(srv, tmp_path):
    venv = _fresh_venv(tmp_path / "venv", with_pip=True)
    proc = _run_wrapper(f"http://127.0.0.1:{srv.port}", venv, tmp_path / "tmp")
    if proc.returncode != 0 and any(m in proc.stderr for m in NO_PIP_MARKERS):
        pytest.skip(f"pip could not install: {proc.stderr[-300:]}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"rcm {__version__} " in proc.stdout, proc.stdout
    assert list((tmp_path / "tmp").iterdir()) == []  # 임시 wheel 을 남기지 않는다
    out = subprocess.run(
        [str(venv / "bin" / "rcm"), "version"], capture_output=True, text=True, timeout=60
    )
    assert out.stdout.startswith(f"rcm {__version__} "), out


class _LyingServer:
    """health 의 sha256 이 실제 wheel 과 다른 서버 — 래퍼가 설치하지 않아야 한다."""

    def __init__(self, wheel: bytes, sha256: str):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/api/health":
                    body = json.dumps(
                        {
                            "ok": True,
                            "version": __version__,
                            "min_client_version": MIN_CLIENT_VERSION,
                            "client_wheel": {
                                "path": WHEEL_PATH,
                                "sha256": sha256,
                                "bytes": len(wheel),
                            },
                            "client_wheel_error": None,
                        }
                    ).encode()
                    ctype = "application/json"
                elif self.path == WHEEL_PATH:
                    body, ctype = wheel, "application/zip"
                    outer.downloads += 1
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.downloads = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@needs_tools
def test_the_wrapper_example_refuses_a_wheel_whose_hash_does_not_match(tmp_path):
    fake = _LyingServer(build_wheel(__version__), "0" * 64)
    try:
        venv = _fresh_venv(tmp_path / "venv", with_pip=False)
        proc = _run_wrapper(fake.url, venv, tmp_path / "tmp")
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "sha256" in proc.stderr and "mismatch" in proc.stderr, proc.stderr
        assert fake.downloads == 1
        assert not (venv / "bin" / "rcm").exists()
        assert list((tmp_path / "tmp").iterdir()) == []  # 실패해도 임시 파일을 남기지 않는다
    finally:
        fake.close()


@needs_tools
def test_the_wrapper_example_preserves_the_failure_of_the_health_call(tmp_path):
    venv = _fresh_venv(tmp_path / "venv", with_pip=False)
    proc = _run_wrapper("http://127.0.0.1:9", venv, tmp_path / "tmp")
    assert proc.returncode != 0
    assert not (venv / "bin" / "rcm").exists()
