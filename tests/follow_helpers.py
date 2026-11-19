"""따라가기(M5q PR A) 시험의 공통 도우미 — `docs/m5q-test-scenarios-a.md` 「공통 픽스처」.

서버는 자기 버전의 wheel 만 주므로 「다른 버전의 서버」는 여기서 만든다.

- `fake_wheel(version, …)` 은 지금 패키지 파일로 wheel 을 조립하되 `__version__` · METADATA 를 그
  버전으로 바꾼다. 악성 항목(`extra`), 뺄 파일(`drop`), 바꿔 끼울 `cli.py`(`cli`)를 받는다.
- `FakeServer` 는 `/api/health` 와 `/client/<이름>` 만 흉내 내고 받은 요청을 남긴다.
- `VersionedServer` 는 진짜 `App` 을 다른 버전 이름과 그 버전의 가짜 wheel 로 띄운다.
- `make_bootloader(dir)` 은 pip 없이 비 editable 부트로더 venv 를 만든다(실제 execve 시험).
"""

from __future__ import annotations

import base64
import functools
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import remote_ci_monitor
from remote_ci_monitor import clientwheel

INSTALLED = remote_ci_monitor.__version__
NEW = "99.0.1"
NEW2 = "99.0.2"
OLD = "0.0.9"
SHA_A = "a" * 64
SHA_B = "b" * 64
T = 1_763_532_000.0
M = 60

#: `cli=` 에 넣는 짧은 `cli.py`. 명령 이름은 설치본이 모르는 `probe` 다.
PROBE_CLI = """\
import json, os, subprocess, sys, time

import remote_ci_monitor


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out = {
        "pid": os.getpid(),
        "version": remote_ci_monitor.__version__,
        "file": remote_ci_monitor.__file__,
        "argv": args,
        "path0": sys.path[0],
        "boot": getattr(remote_ci_monitor, "_follow_boot", None),
        "env": {
            k: v for k, v in os.environ.items() if k.startswith("RCM_FOLLOW") or k == "RCM_SERVER"
        },
    }
    if "--spawn" in args:
        env = subprocess.run(["env"], capture_output=True, text=True).stdout.splitlines()
        out["spawned"] = [ln for ln in env if ln.startswith("RCM_FOLLOW")]
    print(json.dumps(out), flush=True)
    try:
        if "--sleep" in args:
            time.sleep(float(args[args.index("--sleep") + 1]))
    except KeyboardInterrupt:
        return 130
    if "--exit" in args:
        return int(args[args.index("--exit") + 1])
    return 0
"""


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _set_version(init_py: bytes, version: str) -> bytes:
    text = init_py.decode()
    text, n = re.subn(r'(?m)^__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', text)
    assert n == 1
    return text.encode()


@functools.lru_cache(maxsize=64)
def _fake_wheel_cached(
    version: str,
    init_version: str | None,
    requires_python: str | None,
    extra: tuple[tuple[str, bytes, int], ...],
    drop: tuple[str, ...],
    cli: str | None,
    init_text: str | None,
) -> bytes:
    files = clientwheel._package_files()
    metadata, entry_points, _licenses = clientwheel._dist_metadata(version)
    lines = [ln for ln in metadata.splitlines() if not ln.startswith("Requires-Python:")]
    if requires_python is not None:
        lines.insert(3, f"Requires-Python: {requires_python}")
    metadata = "\n".join(lines) + "\n"
    pkg = clientwheel.PACKAGE
    info = clientwheel.dist_info_name(version)
    entries: list[tuple[str, bytes, int]] = []
    for rel, data, mode in files:
        if rel in drop:
            continue
        if rel == "__init__.py":
            data = init_text.encode() if init_text is not None else data
            if init_text is None:
                data = _set_version(data, init_version or version)
        if rel == "cli.py" and cli is not None:
            data = cli.encode()
        entries.append((f"{pkg}/{rel}", data, (stat.S_IFREG | mode) << 16))
    entries.append((f"{info}/METADATA", metadata.encode(), (stat.S_IFREG | 0o644) << 16))
    entries.append(
        (f"{info}/entry_points.txt", entry_points.encode(), (stat.S_IFREG | 0o644) << 16)
    )
    wheel_meta = (
        f"Wheel-Version: 1.0\nGenerator: fake {version}\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    entries.append((f"{info}/WHEEL", wheel_meta.encode(), (stat.S_IFREG | 0o644) << 16))
    record = "".join(f"{n},{_record_hash(d)},{len(d)}\n" for n, d, _ in entries)
    record += f"{info}/RECORD,,\n"
    entries.append((f"{info}/RECORD", record.encode(), (stat.S_IFREG | 0o644) << 16))
    entries.extend(extra)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data, attr in entries:
            zi = zipfile.ZipInfo(name, date_time=clientwheel.ZIP_DATE_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = attr
            zf.writestr(zi, data)
    return buf.getvalue()


def fake_wheel(
    version: str,
    *,
    init_version: str | None = None,
    requires_python: str | None = ">=3.11",
    extra: tuple[tuple[str, bytes, int], ...] = (),
    drop: tuple[str, ...] = (),
    cli: str | None = None,
    init_text: str | None = None,
) -> bytes:
    """지금 패키지로 조립한 `version` 의 wheel. `requires_python=None` 이면 그 줄을 지운다.

    `extra` 는 `(zip 안 이름, 바이트, external_attr)` 를 그대로 더한다(악성 항목용).
    `drop` 은 패키지 안 상대 경로(`"follow.py"`), `cli` 는 `cli.py` 를 바꿔 끼울 소스,
    `init_text` 는 `__init__.py` 전체를 바꿔 끼울 소스다."""
    return _fake_wheel_cached(
        version, init_version, requires_python, tuple(extra), tuple(drop), cli, init_text
    )


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def health_doc(version: str, wheel: bytes | None, **over: Any) -> dict[str, Any]:
    """진짜 서버의 health 와 같은 꼴의 본문."""
    doc: dict[str, Any] = {
        "ok": True,
        "version": version,
        "min_client_version": clientwheel.MIN_CLIENT_VERSION,
        "client_wheel": None
        if wheel is None
        else {
            "path": "/client/" + clientwheel.wheel_filename(version),
            "sha256": sha(wheel),
            "bytes": len(wheel),
        },
        "client_wheel_error": None if wheel is not None else "metadata_missing",
    }
    doc.update(over)
    return doc


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)


class FakeServer:
    """`/api/health` 와 `/client/<이름>` 만 주는 가짜 서버.

    바꿀 수 있는 것(속성): `health`(dict 또는 bytes), `health_status`, `client_bytes`,
    `client_status`, `client_delay`, `redirect`(`(상태, Location)` — health 와 `/client/` 둘 다),
    `client_redirect`(`/client/` 만), `client_barrier`(`threading.Barrier`), `require_token`(토큰
    없으면 `/client/` 401), `extra_bytes`(선언보다 더 보낸다), `health_delay`, `drip`(health 를 한
    바이트씩 이 간격으로 흘린다), `client_hold`(`threading.Event` — 보내기 전에 기다린다),
    `client_header_drip` · `client_body_drip`(`/client/` 의 응답 머리 줄이나 본문 바이트를 이
    간격으로 하나씩 흘린다 — 소켓 동작마다의 제한에는 걸리지 않는 느린 서버).
    """

    def __init__(self, version: str | None = None, wheel: bytes | None = None, **opts: Any):
        if version is not None and wheel is None and opts.get("health") is None:
            wheel = fake_wheel(version)
        self.version = version
        self.wheel = wheel
        self.health: Any = opts.pop("health", None)
        if self.health is None and version is not None:
            self.health = health_doc(version, wheel)
        self.health_status = opts.pop("health_status", 200)
        self.client_bytes = opts.pop("client_bytes", wheel)
        self.client_status = opts.pop("client_status", 200)
        self.client_delay = opts.pop("client_delay", 0.0)
        self.health_delay = opts.pop("health_delay", 0.0)
        self.drip = opts.pop("drip", 0.0)
        self.redirect = opts.pop("redirect", None)
        self.client_redirect = opts.pop("client_redirect", None)
        self.client_barrier = opts.pop("client_barrier", None)
        self.client_hold = opts.pop("client_hold", None)
        self.require_token = opts.pop("require_token", False)
        self.extra_bytes = opts.pop("extra_bytes", 0)
        self.client_header_drip = opts.pop("client_header_drip", 0.0)
        self.client_body_drip = opts.pop("client_body_drip", 0.0)
        assert not opts, opts
        self.requests: list[Seen] = []
        self._lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer._handle(self)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.key = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    # ── 셈 ──
    @property
    def health_count(self) -> int:
        return sum(1 for r in self.requests if r.path == "/api/health")

    @property
    def client_count(self) -> int:
        return sum(1 for r in self.requests if r.path.startswith("/client/"))

    @property
    def wheel_path(self) -> str:
        return "/client/" + clientwheel.wheel_filename(self.version or "0")

    def _send(self, h: BaseHTTPRequestHandler, status: int, body: bytes, ctype: str, **hdr):
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(hdr.pop("length", len(body))))
        for k, v in hdr.items():
            h.send_header(k.replace("_", "-"), v)
        h.end_headers()
        try:
            h.wfile.write(body)
        except OSError:
            pass

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        with self._lock:
            self.requests.append(Seen("GET", h.path, dict(h.headers.items())))
        if self.redirect is not None and h.path in ("/api/health", self.wheel_path):
            status, location = self.redirect
            self._send(h, status, b"", "text/plain", Location=location)
            return
        if h.path == "/api/health":
            if self.health_delay:
                time.sleep(self.health_delay)
            body = self.health if isinstance(self.health, bytes) else json.dumps(self.health)
            body = body.encode() if isinstance(body, str) else body
            if self.drip:
                h.send_response(self.health_status)
                h.send_header("Content-Type", "application/json")
                h.send_header("Content-Length", str(len(body)))
                h.end_headers()
                for i in range(len(body)):
                    try:
                        h.wfile.write(body[i : i + 1])
                        h.wfile.flush()
                    except OSError:
                        return
                    time.sleep(self.drip)
                return
            self._send(h, self.health_status, body, "application/json")
            return
        if h.path.startswith("/client/"):
            if self.client_redirect is not None:
                status, location = self.client_redirect
                self._send(h, status, b"", "text/plain", Location=location)
                return
            if self.require_token and not h.headers.get("Authorization"):
                self._send(h, 401, b'{"error":"read access requires a token"}', "application/json")
                return
            if self.client_barrier is not None:
                try:
                    self.client_barrier.wait(timeout=30)
                except threading.BrokenBarrierError:
                    pass
            if self.client_hold is not None:
                self.client_hold.wait(timeout=60)
            if self.client_delay:
                time.sleep(self.client_delay)
            if h.path != self.wheel_path or self.client_status != 200:
                status = self.client_status if self.client_status != 200 else 404
                self._send(h, status, b'{"error":"no"}', "application/json")
                return
            data = (self.client_bytes or b"") + b"\0" * self.extra_bytes
            if self.client_header_drip or self.client_body_drip:
                self._drip(h, data)
                return
            self._send(h, 200, data, "application/zip")
            return
        self._send(h, 404, b'{"error":"not found"}', "application/json")

    def _drip(self, h: BaseHTTPRequestHandler, data: bytes) -> None:
        """응답 머리 줄이나 본문 바이트를 하나씩 흘린다. 끊기면 조용히 그만둔다."""
        try:
            h.wfile.write(b"HTTP/1.0 200 OK\r\nContent-Type: application/zip\r\n")
            h.wfile.write(f"Content-Length: {len(data)}\r\n".encode())
            if self.client_header_drip:
                for i in range(1000):
                    h.wfile.write(f"X-Drip-{i}: x\r\n".encode())
                    h.wfile.flush()
                    time.sleep(self.client_header_drip)
            h.wfile.write(b"\r\n")
            for i in range(len(data)):
                h.wfile.write(data[i : i + 1])
                h.wfile.flush()
                time.sleep(self.client_body_drip)
        except OSError:
            return

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class SilentListener:
    """연결은 받지만(백로그) 답하지 않는 리스너 — 시간 제한 시험용."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def close(self):
        self.sock.close()


def closed_port() -> int:
    """지금 아무도 듣지 않는 포트."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class VersionedServer:
    """`tests/test_server.Server` 와 같되 `App(version=…)` 과 그 버전의 wheel 로 띄운다.

    실제 라우터가 그 wheel 을 준다 — 다른 파일 이름은 진짜 404, `read_auth = "basic"` 은 진짜 401.
    받은 요청 경로는 `paths` 에 남는다."""

    def __init__(self, tmp_path: Path, version: str, wheel: bytes | None, *, workers=False, **ov):
        from remote_ci_monitor.config import ServerConfig, parse_preset
        from remote_ci_monitor.server import App, make_server
        from remote_ci_monitor.store import Store
        from test_server import PRESETS

        cfg = ServerConfig()
        cfg.server.data_dir = str(tmp_path / f"data-{version}-{id(self)}")
        cfg.server.grace_seconds = 1
        cfg.server.max_snapshot_bytes = 10_000_000
        for k, v in ov.items():
            setattr(cfg.server, k, v)
        cfg.presets = tuple(parse_preset(p) for p in PRESETS)
        self.cfg = cfg
        self.store = Store(cfg.data_dir / "rcm.sqlite3")
        now = datetime.now(UTC)
        self.tokens = {
            "alice": self.store.add_token("alice-laptop", admin=False, now=now),
            "admin": self.store.add_token("macmini-admin", admin=True, now=now),
        }
        self.app = App(cfg, self.store, version=version)
        if wheel is not None:
            self.app._wheel = (wheel, sha(wheel))
        else:
            self.app._wheel = None
            self.app._wheel_error = "metadata_missing"
        self.app._wheel_done = True
        self.paths: list[str] = []
        self.httpd = make_server(self.app, bind="127.0.0.1", port=0)
        outer = self
        base = self.httpd.RequestHandlerClass

        def parse_request(handler) -> bool:
            ok = base.parse_request(handler)
            outer.paths.append(handler.path)
            return ok

        self.httpd.RequestHandlerClass = type("Counting", (base,), {"parse_request": parse_request})
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.key = self.url
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        )
        self.thread.start()
        if workers:
            self.app.start()

    @property
    def health_count(self) -> int:
        return sum(1 for p in self.paths if p == "/api/health")

    @property
    def client_count(self) -> int:
        return sum(1 for p in self.paths if p.startswith("/client/"))

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.shutdown()
        self.store.close()


# ── 실제 execve 시험의 부트로더 venv ─────────────────────────────────────────


@dataclass
class Bootloader:
    root: Path
    python: Path
    rcm: Path
    site: Path


def make_bootloader(root: Path) -> Bootloader:
    """pip 없는 venv 에 `build_wheel(INSTALLED)` 를 풀어 비 editable 설치본을 만든다.

    `direct_url.json` 이 없으니 그 안의 `install_kind()` 는 `wheel` 이다."""
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    py = root / "bin" / "python"
    site = Path(
        subprocess.run(
            [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    )
    data = clientwheel.build_wheel(INSTALLED)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            target = site / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode)
    rcm = root / "bin" / "rcm"
    rcm.write_text(
        f"#!{py}\nimport sys\nfrom remote_ci_monitor.cli import main\nsys.exit(main())\n"
    )
    rcm.chmod(0o755)
    return Bootloader(root, py, rcm, site)


def child_env(tmp: Path, boot: Bootloader, **extra: str) -> dict[str, str]:
    """하위 프로세스의 환경 — 처음부터 만든다. `PYTHONPATH` 와 다른 `RCM_*` 는 넣지 않는다."""
    env = {
        "HOME": str(tmp / "home"),
        "XDG_CACHE_HOME": str(tmp / "cache"),
        "XDG_CONFIG_HOME": str(tmp / "config"),
        "PATH": f"{boot.root / 'bin'}:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    (tmp / "home").mkdir(parents=True, exist_ok=True)
    env.update(extra)
    return env


def run_rcm(
    boot: Bootloader, args: list[str], env: dict[str, str], *, cwd: Path | None = None, timeout=60
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(boot.rcm), *args],
        env=env,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def probe_out(stdout: str) -> dict[str, Any]:
    """PROBE 의 JSON 한 줄."""
    lines = [ln for ln in stdout.splitlines() if ln.startswith("{")]
    assert lines, stdout
    return json.loads(lines[-1])


def leftovers(clients: Path) -> list[str]:
    """`clients/` 아래 `.tmp-*` · `.trash-*` 찌꺼기."""
    if not clients.is_dir():
        return []
    return sorted(p.name for p in clients.iterdir() if p.name.startswith((".tmp-", ".trash-")))


def entries(clients: Path) -> list[Path]:
    """표지가 있는 캐시 항목 폴더."""
    if not clients.is_dir():
        return []
    return sorted(p for p in clients.iterdir() if (p / ".rcm-client.json").is_file())


def write_pin(config_home: Path, text: str) -> Path:
    p = config_home / "rcm" / "client-version"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def raw_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    parts = url.split("/", 3)
    host, port = parts[2].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.request("GET", "/" + (parts[3] if len(parts) > 3 else ""), headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


__all__ = [n for n in dir() if not n.startswith("_")] + ["_fake_wheel_cached"]
_ = (os, shutil)
