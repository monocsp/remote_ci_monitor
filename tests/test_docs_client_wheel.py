"""문서·래퍼 예시 잠금 — 서버가 주는 클라이언트 wheel (M5i I8 · 결정 81·83).

tests/test_client_wheel.py 가 코드를, 이 파일이 docs/operating.md 의 절 · usage 안내 · CHANGELOG ·
scripts/mutcheck.py 의 변이 · examples/session/update-client.sh(실제로 돌린다 — 해시가 다르면
설치하지 않는다 · 실패의 종료 코드를 보존한다)를 잠근다. 문서 잠금은 mutcheck 의 복사본(src ·
tests · pyproject.toml 만)에는 없는 파일을 읽으므로 코드 테스트와 나눠 둔다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor.clientwheel import MIN_CLIENT_VERSION, build_wheel
from test_client_wheel import NO_PIP_MARKERS, WHEEL_NAME, WHEEL_PATH, _fresh_venv
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
        # 본문은 「바로 아래 `client` 행」을 설명한다 — 스크린샷의 alt 텍스트도 그 행을 안다
        # (리뷰 #90 D: 화면과 문서가 충돌했다 · M5l L3.8)
        alt = re.search(r"!\[([^\]]*)\]\(images/ui/cli-check\.png\)", text)
        assert alt, f"{path.name}: no cli-check.png screenshot"
        assert "client" in alt.group(1), f"{path.name}: alt text without the client row"


def test_changelog_has_the_entry():
    """릴리스 뒤에는 항목이 `[Unreleased]` 가 아니라 그 버전의 절에 있다 — 다른 문서 잠금
    (tests/test_docs_m5.py `unreleased()`)처럼 「0.1.0 이후 전부」를 본다."""
    from test_docs_m5 import unreleased

    text = unreleased(CHANGELOG.read_text())
    assert "/client/" in text and "min_client_version" in text


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


class _FakeWheelServer:
    """health 와 wheel 만 주는 가짜 서버. 옵션으로 sha256 을 속이거나(`sha256`), health 를
    503 으로 보내거나(`health_status` — 본문은 그대로), 응답을 늦춘다(`delay` — 그 사이에
    래퍼의 argv 를 들여다본다). 받은 `Authorization` 헤더는 `auth_seen` 에 남긴다."""

    def __init__(
        self,
        wheel: bytes,
        sha256: str | None = None,
        *,
        health_status: int = 200,
        health_error: str | None = None,
        delay: float = 0.0,
    ):
        outer = self
        sha = sha256 or hashlib.sha256(wheel).hexdigest()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.auth_seen.append(self.headers.get("Authorization"))
                if outer.delay:
                    time.sleep(outer.delay)
                if self.path == "/api/health":
                    doc = {
                        "ok": health_status == 200,
                        "version": __version__,
                        "min_client_version": MIN_CLIENT_VERSION,
                        "client_wheel": {"path": WHEEL_PATH, "sha256": sha, "bytes": len(wheel)},
                        "client_wheel_error": None,
                    }
                    if health_error:
                        doc["error"] = health_error
                    body, ctype = json.dumps(doc).encode(), "application/json"
                    status = health_status
                elif self.path == WHEEL_PATH:
                    body, ctype, status = wheel, "application/zip", 200
                    outer.downloads += 1
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.downloads = 0
        self.delay = delay
        self.auth_seen: list[str | None] = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _LyingServer(wheel: bytes, sha256: str) -> _FakeWheelServer:
    """health 의 sha256 이 실제 wheel 과 다른 서버 — 래퍼가 설치하지 않아야 한다."""
    return _FakeWheelServer(wheel, sha256)


def _wheel_variant(wheel: bytes, tag: str) -> bytes:
    """같은 버전, 다른 바이트 — `remote_ci_monitor/_which.txt` 를 넣고 RECORD 에도 적는다.
    설치 뒤 site-packages 의 그 파일이 어느 서버의 wheel 이 들어왔는지 말해 준다."""
    src = zipfile.ZipFile(io.BytesIO(wheel))
    out = io.BytesIO()
    marker = "remote_ci_monitor/_which.txt"
    payload = tag.encode()
    record_name = next(n for n in src.namelist() if n.endswith(".dist-info/RECORD"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == record_name:
                digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                line = f"{marker},sha256={digest.rstrip(b'=').decode()},{len(payload)}\n"
                data = line.encode() + data
            dst.writestr(info, data)
        dst.writestr(marker, payload)
    return out.getvalue()


def _descendant_args(root: int) -> list[str]:
    """`root` 아래 프로세스 트리(자기 포함)의 argv 줄들 — `ps -axwwo pid,ppid,args`."""
    ps = subprocess.run(
        ["ps", "-axwwo", "pid,ppid,args"], capture_output=True, text=True, timeout=30
    )
    rows: list[tuple[int, int, str]] = []
    for ln in ps.stdout.splitlines()[1:]:
        parts = ln.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    family = {root}
    while True:
        more = {pid for pid, ppid, _ in rows if ppid in family and pid not in family}
        if not more:
            break
        family |= more
    return [args for pid, _, args in rows if pid in family]


def _installed_marker(venv: Path) -> str:
    hits = list(venv.glob("lib/python*/site-packages/remote_ci_monitor/_which.txt"))
    assert len(hits) == 1, hits
    return hits[0].read_text()


@needs_tools
def test_the_wrapper_never_touches_a_users_file_at_the_old_shared_path(srv, tmp_path):
    """검증·설치는 자기 `mktemp -d` 안에서만 — `$TMPDIR/<wheel 이름>` 은 남의 파일이다(리뷰 #90
    B P1: 옛 래퍼는 검증한 파일을 그 공용 이름으로 옮겨 기존 파일을 덮었다)."""
    venv = _fresh_venv(tmp_path / "venv", with_pip=True)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    mine = tmpdir / WHEEL_NAME
    mine.write_text("MINE\n")
    mine.chmod(0o444)
    proc = _run_wrapper(f"http://127.0.0.1:{srv.port}", venv, tmpdir)
    if proc.returncode != 0 and any(m in proc.stderr for m in NO_PIP_MARKERS):
        pytest.skip(f"pip could not install: {proc.stderr[-300:]}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert mine.read_text() == "MINE\n"
    assert list(tmpdir.iterdir()) == [mine]  # 래퍼가 만든 것은 하나도 남지 않는다
    assert f"rcm {__version__} " in proc.stdout, proc.stdout


@needs_tools
def test_two_wrappers_against_two_servers_of_the_same_version_install_what_each_verified(
    tmp_path,
):
    """같은 버전의 dev 서버 둘에 동시에 — 각 venv 에는 **자기 서버가 준** wheel 이 들어간다.
    (리뷰 #90 B P1: 공용 경로에서는 한쪽이 검증한 파일을 다른 쪽이 덮을 수 있었다)"""
    base = build_wheel(__version__)
    a = _FakeWheelServer(_wheel_variant(base, "A"))
    b = _FakeWheelServer(_wheel_variant(base, "B"))
    try:
        venv_a = _fresh_venv(tmp_path / "venv-a", with_pip=True)
        venv_b = _fresh_venv(tmp_path / "venv-b", with_pip=True)
        tmpdir = tmp_path / "tmp"
        tmpdir.mkdir()
        procs = []
        for fake, venv in ((a, venv_a), (b, venv_b)):
            env_ = {
                **os.environ,
                "RCM_SERVER": fake.url,
                "RCM_VENV": str(venv),
                "TMPDIR": str(tmpdir),
                "PATH": f"{venv / 'bin'}:{os.environ.get('PATH', '')}",
            }
            env_.pop("RCM_TOKEN", None)
            procs.append(
                subprocess.Popen(
                    ["bash", str(UPDATE_SH)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env_,
                )
            )
        outs = [p.communicate(timeout=600) for p in procs]
        for p, (out, err) in zip(procs, outs, strict=True):
            if p.returncode != 0 and any(m in err for m in NO_PIP_MARKERS):
                pytest.skip(f"pip could not install: {err[-300:]}")
            assert p.returncode == 0, out + err
        assert _installed_marker(venv_a) == "A"
        assert _installed_marker(venv_b) == "B"
        assert list(tmpdir.iterdir()) == []
    finally:
        a.close()
        b.close()


@needs_tools
def test_the_wrapper_keeps_the_token_out_of_every_argv(tmp_path):
    """토큰은 curl 의 인수가 아니라 0600 파일로 — 느린 health 동안 `ps` 에 보이지 않는다
    (리뷰 #90 B P1: `-H "Authorization: Bearer $RCM_TOKEN"` 은 같은 머신의 누구나 읽었다)."""
    token = "rcmtest" + base64.b32encode(os.urandom(15)).decode().lower()
    fake = _FakeWheelServer(build_wheel(__version__), delay=6.0)
    try:
        venv = _fresh_venv(tmp_path / "venv", with_pip=False)
        tmpdir = tmp_path / "tmp"
        tmpdir.mkdir()
        env_ = {
            **os.environ,
            "RCM_SERVER": fake.url,
            "RCM_TOKEN": token,
            "RCM_VENV": str(venv),
            "TMPDIR": str(tmpdir),
        }
        proc = subprocess.Popen(
            ["bash", str(UPDATE_SH)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env_
        )
        try:
            # health 가 6초 걸리는 동안 curl 이 떠 있다 — 그 줄이 보일 때까지 기다린다.
            # 래퍼의 **자손 프로세스만** 본다: 같은 머신의 다른 세션이 argv 에 무엇을 적든 우리
            # 몫이 아니다. `-ww`: Linux procps 는 tty 가 아니어도 80 컬럼에서 자른다.
            deadline = time.monotonic() + 5.0
            while True:
                mine = _descendant_args(proc.pid)
                curls = [a for a in mine if "curl" in a and fake.url in a]
                if curls or time.monotonic() > deadline:
                    break
                time.sleep(0.2)
            assert curls, mine
            joined = "\n".join(mine)
            assert token not in joined, joined
            assert "Authorization: Bearer" not in joined, joined
            # 그 파일은 래퍼만의 디렉터리(0700) 안의 0600
            private = [d for d in tmpdir.iterdir() if d.is_dir()]
            assert len(private) == 1, list(tmpdir.iterdir())
            assert stat.S_IMODE(private[0].stat().st_mode) == 0o700
            files = list(private[0].iterdir())
            assert files, private
            assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files), files
        finally:
            proc.communicate(timeout=600)
        assert fake.auth_seen and all(v == f"Bearer {token}" for v in fake.auth_seen), (
            fake.auth_seen
        )
        assert list(tmpdir.iterdir()) == []  # 끝나면 그 디렉터리째 지운다
    finally:
        fake.close()
    text = UPDATE_SH.read_text()
    assert "Bearer $RCM_TOKEN" not in text and "Bearer ${RCM_TOKEN" not in text
    assert "-K" in text or "--config" in text or "-H @" in text


@needs_tools
def test_the_wrapper_installs_when_health_is_503_but_the_wheel_is_fine(tmp_path):
    """janitor stale · worker down 은 503 이지만 본문의 `client_wheel` 은 멀쩡하다 — 설치하고
    stderr 로 알린다(리뷰 #90 B P2: `curl -f` 는 여기서 죽었다)."""
    fake = _FakeWheelServer(
        build_wheel(__version__), health_status=503, health_error="janitor stale"
    )
    try:
        venv = _fresh_venv(tmp_path / "venv", with_pip=True)
        proc = _run_wrapper(fake.url, venv, tmp_path / "tmp")
        if proc.returncode != 0 and any(m in proc.stderr for m in NO_PIP_MARKERS):
            pytest.skip(f"pip could not install: {proc.stderr[-300:]}")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "503" in proc.stderr and "janitor stale" in proc.stderr, proc.stderr
        assert f"rcm {__version__} " in proc.stdout, proc.stdout
        assert fake.downloads == 1
    finally:
        fake.close()


@needs_tools
def test_the_wrapper_names_the_health_status_when_the_server_cannot_answer(tmp_path):
    """health 가 wheel 없는 오류 문서(500 등)면 exit 3 에 상태와 본문의 오류를 말한다."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"error": "database unavailable"}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        venv = _fresh_venv(tmp_path / "venv", with_pip=False)
        proc = _run_wrapper(f"http://127.0.0.1:{httpd.server_address[1]}", venv, tmp_path / "tmp")
        assert proc.returncode == 3, proc.stderr
        assert "500" in proc.stderr and "database unavailable" in proc.stderr, proc.stderr
        assert list((tmp_path / "tmp").iterdir()) == []
    finally:
        httpd.shutdown()
        httpd.server_close()


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
