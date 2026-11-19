"""M5n 역할 C 시험용 가짜 rcm 서버 — 정해 둔 JSON 을 경로별로 돌려주는 stdlib HTTP 서버.

진짜 서버(`test_server.Server`)는 M5n 의 새 라우트(`/api/machines` · `/machines/{id}/recheck` ·
`/worker/spec`)를 역할 B 가 구현해야 생긴다. 사람에게 닿는 표시(역할 C)는 **서버가 무엇을
보냈는가**만으로 정해지므로, 응답을 손으로 쓴 문서로 못 박고 CLI·워커·페이지가 그것을 어떻게
그리는지만 잰다. 그래서 이 파일의 시험은 역할 A·B 의 구현을 기다리지 않는다.

- 127.0.0.1 의 임시 포트에만 뜬다. 운영 서버(8787)와 무관하다.
- 경로는 퍼센트 디코딩 뒤 쿼리를 떼고 맞춘다(`/machines/%40local/recheck` == `/machines/@local/…`).
- 모든 요청을 `requests` 에 (method, path, body, headers) 로 남긴다 — 「무엇을 보냈나」를 본다.
- 정해 두지 않은 경로는 404 `{"error": "not found"}` — 옛 서버 흉내가 기본값이다.
- `static_dir` 을 주면 그 아래 파일(웹 페이지)을 GET 으로 내준다.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


@dataclass
class RawReply:
    """JSON 이 아닌 응답(파일 바이트 · zip · 리다이렉트). 상태 코드는 route 의 것이다."""

    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Recorded:
    method: str
    path: str
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


class FakeRcm:
    """`routes[(METHOD, path)] = (status, body)` — body 가 callable 이면 요청 본문으로 부른다."""

    def __init__(self, static_dir: Path | None = None):
        self.routes: dict[tuple[str, str], tuple[int, Any]] = {}
        self.requests: list[Recorded] = []
        self.static_dir = static_dir
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # 시험 출력을 더럽히지 않는다
                return

            def _handle(self, method: str) -> None:
                raw_len = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(raw_len) if raw_len else b""
                path = urllib.parse.unquote(self.path.split("?", 1)[0])
                body: Any = None
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        body = raw
                outer.requests.append(
                    Recorded(method, path, body, {k.lower(): v for k, v in self.headers.items()})
                )
                route = outer.routes.get((method, path))
                if route is None and method == "GET" and outer.static_dir is not None:
                    # 진짜 서버와 같은 배치: `/` → index.html, `/static/<f>` → web/<f>
                    rel = "index.html" if path == "/" else path.removeprefix("/static/")
                    target = (outer.static_dir / rel).resolve()
                    if target.is_file() and outer.static_dir.resolve() in target.parents:
                        data = target.read_bytes()
                        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(data)
                        return
                if route is None:
                    status, payload = 404, {"error": "not found"}
                else:
                    status, payload = route
                    if callable(payload):
                        payload = payload(body)
                if status == 204:
                    self.send_response(204)
                    self.end_headers()
                    return
                if isinstance(payload, RawReply):
                    self.send_response(status)
                    headers = {"Content-Type": "application/octet-stream", **payload.headers}
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(payload.body)))
                    self.end_headers()
                    self.wfile.write(payload.body)
                    return
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

            def do_PUT(self) -> None:  # noqa: N802
                self._handle("PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle("DELETE")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        # 짧은 poll — 닫을 때 기본 0.5초를 시험마다 기다리지 않는다
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def route(self, method: str, path: str, body: Any, status: int = 200) -> None:
        self.routes[(method, path)] = (status, body)

    def calls(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and r.path == path]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def free_port() -> int:
    """아무도 안 듣는 포트 하나(연결 거절 = 「서버에 못 닿음」 흉내)."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
