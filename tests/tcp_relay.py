"""시험용 TCP 중계(M5t-b §7.5) — 표준 라이브러리만 쓴다.

워커가 서버 대신 이 중계에 붙는다. 중계는 바이트를 그대로 넘기고 세 가지 고장을 만든다.

- `pause()` 는 분단이다. 새 연결을 받지 않고(연결은 backlog 에서 기다린다) 기존 연결의 바이트를 어느
  쪽으로도 넘기지 않는다. 서버는 살아 있지만 워커의 요청은 닿지 않고 워커는 시한까지 매달린다.
  `resume()` 이 다시 흐르게 한다. 붙잡혀 있던 바이트는 그때 넘어간다.
- `stop()` 은 연결 거부다. 듣는 소켓과 기존 연결을 닫는다. 서버 정지를 흉내 내는 함수는 아니다 —
  정지는 실제 서버를 멈춰서 만든다. `resume()` 이 같은 포트로 다시 듣는다.
- `hold_next(path)` 는 요청줄에 `path` 가 든 다음 연결 하나의 요청 바이트를 붙잡는다.
  `release_held()` 가 그 바이트를 서버로 넘긴다. 나머지 연결은 그대로 흐른다(RL50 의 순서 뒤바꿈).

워커 클라이언트는 요청마다 새 연결을 쓴다(`urllib`, `Connection: close`). 그래서 연결의 첫 바이트가
곧 요청줄이다.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable

_CHUNK = 65536


class TcpRelay:
    def __init__(self, upstream_port: int, upstream_host: str = "127.0.0.1"):
        self.upstream = (upstream_host, upstream_port)
        self._lock = threading.Lock()
        self._flow = threading.Event()  # 세워져 있으면 흐른다(분단이 아님)
        self._flow.set()
        self._closed = False
        self._conns: list[socket.socket] = []
        self._hold_path: str | None = None
        self._held = threading.Event()  # 세워지면 붙잡은 연결을 놓는다
        self.held_count = 0  # 붙잡았던 연결 수(시험이 「정말 붙잡았나」를 본다)
        self.accepted = 0
        self._listener: socket.socket | None = None
        self.port = 0
        self._listen()

    # ── 듣기 ──

    def _listen(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(64)
        self.port = s.getsockname()[1]
        self._listener = s
        threading.Thread(target=self._accept_loop, args=(s,), daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _accept_loop(self, listener: socket.socket) -> None:
        while True:
            self._flow.wait()  # 분단이면 받지 않는다 — 연결은 backlog 에서 매달린다
            if self._listener is not listener:
                return
            try:
                client, _ = listener.accept()
            except OSError:
                return
            with self._lock:
                self.accepted += 1
                self._conns.append(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    # ── 한 연결 ──

    def _serve(self, client: socket.socket) -> None:
        try:
            upstream = socket.create_connection(self.upstream, timeout=30)
        except OSError:
            self._close(client)
            return
        upstream.settimeout(None)
        with self._lock:
            self._conns.append(upstream)
        first = self._recv(client)
        if first is None:
            self._close(client, upstream)
            return
        if self._should_hold(first):
            with self._lock:
                self.held_count += 1
            self._held.wait()
        if not self._send(upstream, first):
            self._close(client, upstream)
            return
        threading.Thread(target=self._pump, args=(upstream, client), daemon=True).start()
        self._pump(client, upstream)

    def _should_hold(self, first: bytes) -> bool:
        with self._lock:
            path = self._hold_path
            if path is None:
                return False
            line = first.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            if path not in line:
                return False
            self._hold_path = None  # 다음 연결 하나만
            return True

    def _recv(self, s: socket.socket) -> bytes | None:
        try:
            data = s.recv(_CHUNK)
        except OSError:
            return None
        return data or None

    def _send(self, s: socket.socket, data: bytes) -> bool:
        self._flow.wait()
        try:
            s.sendall(data)
        except OSError:
            return False
        return True

    def _pump(self, src: socket.socket, dst: socket.socket) -> None:
        while True:
            data = self._recv(src)
            if data is None or not self._send(dst, data):
                break
        self._close(src, dst)

    def _close(self, *socks: socket.socket) -> None:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
            with self._lock:
                if s in self._conns:
                    self._conns.remove(s)

    # ── 고장 ──

    def pause(self) -> None:
        """분단 — 받지도 넘기지도 않는다."""
        self._flow.clear()

    def stop(self) -> None:
        """연결 거부 — 듣는 소켓과 기존 연결을 닫는다."""
        with self._lock:
            listener, self._listener = self._listener, None
            conns, self._conns = list(self._conns), []
        if listener is not None:
            listener.close()
        for s in conns:
            try:
                s.close()
            except OSError:
                pass

    def resume(self) -> None:
        """분단이나 거부를 푼다. 거부였으면 같은 포트로 다시 듣는다."""
        if self._listener is None and not self._closed:
            self._listen()
        self._flow.set()

    def hold_next(self, path: str) -> None:
        """요청줄에 `path` 가 든 다음 연결 하나를 붙잡는다."""
        self._held.clear()
        with self._lock:
            self._hold_path = path

    def release_held(self) -> None:
        self._held.set()

    def wait_held(self, timeout: float, *, count: int = 1) -> bool:
        return _wait(lambda: self.held_count >= count, timeout)

    def close(self) -> None:
        self._closed = True
        self._flow.set()
        self._held.set()
        self.stop()


def _wait(pred: Callable[[], bool], timeout: float) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()
