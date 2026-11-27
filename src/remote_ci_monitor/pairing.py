"""짝짓기 · 서버 신원의 I/O 계층(M5t-c · docs/m5tc-workplan.md §4.2 · §4.4, PR1).

순수 계산은 `core/pairing.py` 에 있고 여기는 그것을 부르는 쪽이다: `pair.key` 파일, `secrets` 로
만드는 nonce · 코드 · `pair_id` · 챌린지 난수, 서버 시계, 짝짓기 창(메모리)과 그 잠금, 쓴 챌린지
집합, 서버 믹스인(라우트 처리 · 등록 증명 검사 · 거절 MAC).

값(코드 · 코드 키 · 토큰 · `pair_secret` · `pair.key`)은 로그 · 오류 문장 · 응답 어디에도 싣지
않는다(PI17). 코드는 `POST /pair` 의 201 응답에만, 토큰과 `pair_secret` 은 교환 200 에만 있다.
"""

from __future__ import annotations

import errno
import hmac
import os
import secrets
import socket
import sqlite3
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from remote_ci_monitor.config import ANY_BINDS, LOOPBACK_BINDS, advertise_enabled
from remote_ci_monitor.core import pairing as cp
from remote_ci_monitor.core.model import DEFAULT_POOL, PLACEMENT_CAPABLE
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.store import StoreError, TokenNameTaken

PAIR_KEY_FILE = "pair.key"
PAIR_KEY_BYTES = 32
#: 창 수명(분) — 기본 10, 상한 60(§4.4 「수명」)
PAIR_TTL_DEFAULT_MINUTES = 10
PAIR_TTL_MAX_MINUTES = 60
#: `GET /pair` 가 닫힌 창을 남기는 시간(닫힌 때부터)과 개수(319 f)
PAIR_LIST_KEEP_SECONDS = 3600
PAIR_LIST_KEEP = 20
#: 짝지은 머신이 토큰을 싣는 요청마다 새로 보내는 nonce 헤더와, 거절 MAC 을 싣는 본문 키(340)
NONCE_HEADER = "X-RCM-Pair-Nonce"
REJECT_MAC_KEY = "pair_reject_mac"
#: 관리 라우트 `POST /pair` 가 받는 본문 키(§4.4 「관리 라우트」)
OPEN_KEYS = frozenset({"kind", "pool", "name", "replace", "ttl_minutes"})
CANCEL_KEYS = frozenset({"kind", "id"})

_KEY_LOCK = threading.Lock()


class PairKeyError(StoreError):
    """`pair.key` 를 읽거나 만들 수 없다 — `rcm serve` 는 `cannot start server: …` 와 종료 2 로
    끝난다(F23: `StoreError` 의 하위 클래스라 `cli.py` 의 같은 줄이 받는다). 문장에는 무엇이
    틀렸는지와 고치는 법만 있고 값은 없다."""


def _reset_hint(path: Path) -> str:
    return (
        "or reset pairing: revoke the paired tokens (rcm token list shows them in "
        f'"paired from"; rcm token revoke <name>), then delete {path}, start again and pair again'
    )


def read_pair_key(path: Path) -> bytes:
    """`O_RDONLY` · `O_NOFOLLOW` 로 열고 `fstat` 으로 본다: 보통 파일 · 그룹과 기타 비트 없음 ·
    32바이트(§4.2 「키 읽기」). 없으면 `FileNotFoundError` 를 그대로 올린다(부르는 쪽이 판단)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise PairKeyError(
                f"{path}: is a symbolic link — pair.key must be a regular file in data_dir; "
                + _reset_hint(path)
            ) from e
        raise PairKeyError(
            f"{path}: cannot open it ({errno.errorcode.get(e.errno, e.errno)})"
        ) from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PairKeyError(f"{path}: is not a regular file; " + _reset_hint(path))
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:  # mutcheck: pair-key-mode-not-checked
            raise PairKeyError(
                f"{path}: mode is {mode:04o} — other users could read the server identity; "
                f"chmod 600 {path}"
            )
        if st.st_size != PAIR_KEY_BYTES:
            raise PairKeyError(
                f"{path}: must be {PAIR_KEY_BYTES} bytes, not {st.st_size}; " + _reset_hint(path)
            )
        data = b""
        while len(data) < PAIR_KEY_BYTES:
            chunk = os.read(fd, PAIR_KEY_BYTES - len(data))
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    if len(data) != PAIR_KEY_BYTES:
        raise PairKeyError(f"{path}: must be {PAIR_KEY_BYTES} bytes; " + _reset_hint(path))
    return data


def _fsync_dir(folder: Path) -> None:
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def load_or_create_pair_key(
    data_dir: Path, store: Any, *, stage: Callable[[str], None] | None = None
) -> bytes:
    """`serve()` 가 Store 를 연 뒤 부른다(§4.2 「키 읽기」 · 「키가 없을 때」 · 「키 만들기」).

    - 있으면 읽기 규칙대로 읽는다.
    - 없는데 폐기되지 않은 짝 토큰이 있으면 **기동 거절**(306). 조용히 새 키를 만들지 않는다 —
      fail-open 금지(PLAN.md).
    - 없고 짝 토큰도 없으면 만든다: 같은 폴더의 임시 파일(`O_CREAT|O_EXCL`, 0600) → 32바이트 ·
      `fsync` → `os.link(tmp, pair.key)` → 임시 파일을 지운다. `link` 가 `FileExistsError` 면 다른
      프로세스가 먼저 만든 키를 읽는다. 하드 링크를 못 하는 파일 시스템이면 거절한다 — 덮어쓰는
      rename 으로 물러서지 않는다(두 서버가 서로 다른 키를 쥔 채 뜰 수 있다, F59).

    `stage` 는 시험이 단계 사이에 멈춤을 넣는 자리다(`opened` · `written`)."""
    data_dir = Path(data_dir)
    final = data_dir / PAIR_KEY_FILE
    try:
        return read_pair_key(final)
    except FileNotFoundError:
        pass
    active = store.active_paired_tokens()
    if active:  # mutcheck: pair-key-silently-regenerated
        raise PairKeyError(
            f"{final} is missing but {active} paired tokens are still active — restore pair.key "
            "from the backup taken with this data_dir, or reset pairing: revoke the paired tokens "
            '(rcm token list shows them in "paired from"; rcm token revoke <name>), then start '
            "again and pair again"
        )
    tmp_name = f"{PAIR_KEY_FILE}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    tmp = data_dir / tmp_name  # mutcheck: pair-key-written-in-place
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.fchmod(fd, 0o600)  # umask 가 느슨해도 처음부터 0600(PI17)
        if stage is not None:
            stage("opened")
        os.write(fd, secrets.token_bytes(PAIR_KEY_BYTES))
        os.fsync(fd)
    except BaseException:
        # 디스크가 가득 차거나 멈춤이 와도 반쯤 쓴 임시 파일을 남기지 않는다(G5)
        os.close(fd)
        _unlink_quietly(tmp)  # mutcheck: pair-key-temp-left-on-write-error
        raise
    os.close(fd)
    if stage is not None:
        stage("written")
    try:
        os.link(tmp, final)
    except FileExistsError:
        _unlink_quietly(tmp)
        return read_pair_key(final)
    except OSError as e:
        _unlink_quietly(tmp)  # mutcheck: pair-key-link-falls-back-to-rename
        name = errno.errorcode.get(e.errno or 0, str(e.errno))
        raise PairKeyError(
            f"{final}: cannot create it here — this filesystem does not support hard links "
            f"({name}); put data_dir on a local disk, or create the key yourself: "
            f"(umask 077; head -c 32 /dev/urandom > {final})"
        ) from e
    _unlink_quietly(tmp)
    _fsync_dir(data_dir)
    return read_pair_key(final)


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


# ── 난수 ────────────────────────────────────────────────────────────────────


def new_nonce() -> str:
    """요청마다 새 nonce(소문자 hex 64자) — `secrets.token_hex(32)`."""
    return secrets.token_hex(32)  # mutcheck: pair-hello-fixed-nonce


def new_code() -> str:
    """짝짓기 코드 10자(base32 알파벳, `secrets.choice`)."""
    return "".join(secrets.choice(cp.PAIR_CODE_ALPHABET) for _ in range(cp.PAIR_CODE_LENGTH))


# ── 거절 ────────────────────────────────────────────────────────────────────


class PairRefusal(Exception):
    """pair 라우트의 거절 — 서버 핸들러가 같은 상태 · 본문의 `ApiError` 로 바꿔 보낸다(본문을 먹고
    거절 MAC 을 다는 `_send_error` 한 곳을 지나게)."""

    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def _no_window() -> PairRefusal:
    """틀린 증명 · 닫힌 창 · 연 적 없는 창 · 만료 · 이미 쓴 코드 · 모르는 kind 의 같은 404
    (고정 바이트, 경로 힌트 없음 — §4.4 「같은 404」)."""
    return PairRefusal(404, "no open pairing window for that code", code="no_pairing_window")


def _bad_admin(message: str) -> PairRefusal:
    return PairRefusal(400, message, code="bad_pair_admin_request")


def reject_target(status: int, extra: dict[str, Any]) -> tuple[str, str] | None:
    """거절 MAC 을 다는 거절이면 (`code`, `reason`), 아니면 None(§4.2 「거절 MAC」 (a)~(f)).

    모든 라우트의 401 · 403 `pair_proof_required` · 403 `pair_proof_invalid` 의 `bad_mac` ·
    409 `pool_mismatch` · M5t-b 의 409 `session_fenced` · `protocol_required`(343). 없는 칸은 빈
    문자열이다."""
    code = extra.get("code") if isinstance(extra.get("code"), str) else ""
    reason = extra.get("reason") if isinstance(extra.get("reason"), str) else ""
    if status == 401:
        return code, reason
    listed = (  # mutcheck: reject-mac-register-omitted
        (status == 403 and code == "pair_proof_required")
        or (status == 403 and code == "pair_proof_invalid" and reason == "bad_mac")
        or (status == 409 and code in ("pool_mismatch", "session_fenced", "protocol_required"))
    )
    return (code, reason) if listed else None


def bearer_secret(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


# ── 짝짓기 창(메모리) ───────────────────────────────────────────────────────


@dataclass
class PairWindow:
    """한 번 쓰는 짧은 발급 기간(§4.4). 코드 자체는 두지 않는다 — 코드 키만."""

    id: str
    kind: str
    pool: str | None
    name: str | None
    replace: bool
    code_key: bytes
    opened_by: str
    opened_at: datetime
    expires_at: datetime
    state: str = "open"  # open · used · closed
    reason: str | None = (
        None  # closed 의 이유: expired · too_many_wrong_codes · cancelled · pair_failed
    )
    closed_at: datetime | None = None
    failures: int = 0
    paired: dict[str, Any] | None = None

    def close(self, reason: str, at: datetime) -> None:
        self.state, self.reason, self.closed_at = "closed", reason, at

    def doc(self) -> dict[str, Any]:
        paired = None
        if self.state == "used" and self.paired is not None:
            paired = {**self.paired, "at": iso(self.paired["at"])}
        return {
            "id": self.id,
            "kind": self.kind,
            "pool": self.pool,
            "name": self.name,
            "opened_by": self.opened_by,
            "opened_at": iso(self.opened_at),
            "expires_at": iso(self.expires_at),
            "closed_at": iso(self.closed_at),
            "state": self.state,
            "reason": self.reason,
            "paired": paired,
        }


class PairWindows:
    """서버 프로세스 메모리의 창들 — DB 표가 없다(291). 재시작하면 열린 창은 사라진다. kind 마다
    열린 창은 하나다. 잠금 하나가 열기 · 교환 · 취소 · 목록을 차례로 세운다(같은 코드의 동시 교환
    N개 → 200 정확히 1)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.windows: list[PairWindow] = []

    def expire(self, now: datetime) -> None:
        for w in self.windows:
            if w.state == "open" and now >= w.expires_at:
                w.close("expired", w.expires_at)  # 닫힌 때는 만료 시각이다(서버 시계)

    def open_of(self, kind: str) -> PairWindow | None:
        return next((w for w in self.windows if w.state == "open" and w.kind == kind), None)

    def listing(self, now: datetime) -> list[PairWindow]:
        """열린 창 전부 + 닫힌 지 `PAIR_LIST_KEEP_SECONDS` 안의 최근 `PAIR_LIST_KEEP` 개. 보존은
        닫힌 때(`closed_at`)로 잰다 — 60분 창이 59분에 쓰여도 그 뒤 한 시간 남는다(V2-10)."""
        self.expire(now)
        keep_from = now - timedelta(seconds=PAIR_LIST_KEEP_SECONDS)
        closed = [
            w
            for w in self.windows
            if w.state != "open"
            and w.closed_at is not None
            and w.closed_at >= keep_from  # mutcheck: pair-list-keeps-by-opened-at
        ]
        closed.sort(key=lambda w: w.closed_at or w.opened_at, reverse=True)
        kept = [w for w in self.windows if w.state == "open"] + closed[:PAIR_LIST_KEEP]
        self.windows = sorted(kept, key=lambda w: w.opened_at)
        return list(self.windows)


class ChallengeLedger:
    """쓴 챌린지 집합 — 만료(120초 + 미래 여유 5초)까지만 둔다.

    재시작하면 비지만 120초 창이 묶는다."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._seen: dict[str, int] = {}

    def burn(self, challenge: str, t: int, now: int) -> bool:
        """처음이면 넣고 True, 이미 쓴 것이면 False. 한 잠금 안에서 본다(⑤ · ⑥)."""
        with self.lock:
            floor = now - cp.PAIR_CHALLENGE_SECONDS - cp.PAIR_CHALLENGE_FUTURE_SECONDS
            for c in [c for c, seen_t in self._seen.items() if seen_t < floor]:
                del self._seen[c]
            if challenge in self._seen:
                return False
            self._seen[challenge] = t
            return True


# ── 서버 믹스인 ─────────────────────────────────────────────────────────────


class PairingMixin:
    """`App` 의 짝짓기 · 서버 신원 동작(§4.2 · §4.4). `App.__init__` 이 `_pair_key` ·
    `_pair_windows` · `_pair_challenges` · `pair_ips_fn` 을 채운다."""

    config: Any
    store: Any
    now_fn: Callable[[], datetime]
    _pair_key: bytes | None
    _pair_windows: PairWindows
    _pair_challenges: ChallengeLedger
    pair_ips_fn: Callable[[], list[str]]

    def log(self, msg: str) -> None:  # pragma: no cover — App 이 덮는다
        raise NotImplementedError

    @property
    def pair_key(self) -> bytes:
        """서버 신원 키. `serve()` 가 기동 때 넘긴 값이고, 없으면(직접 만든 App) 처음 쓸 때
        읽거나 만든다."""
        key = self._pair_key
        if key is None:
            with _KEY_LOCK:
                if self._pair_key is None:
                    self._pair_key = load_or_create_pair_key(self.config.data_dir, self.store)
                key = self._pair_key
        return key

    def _advertise_host(self) -> str | None:
        """광고가 켜져 있으면 `<짧은 호스트 이름>` — `App.start` 의 mDNS 이름과 같은 규칙(F12)."""
        if not advertise_enabled(self.config.server):
            return None
        return (socket.gethostname().split(".")[0] or "host").lower()

    # ── POST /pair/hello ──

    def pair_hello(self, body: Any, sock_ip: str, sock_port: int) -> dict[str, Any]:
        """익명 서버 신원 증명(§4.2). Store 를 부르지 않는다 — K 는 `pair_id` 로 매번 파생하므로
        없는 · 폐기된 · 옛 `pair_id` 도 같은 계산 · 같은 모양이다(PI5 · PI20)."""
        if not cp.hello_request_ok(body):
            raise PairRefusal(400, "bad hello request", code="bad_hello")
        s = self.config.server
        own = cp.own_dialed_set(sock_ip, sock_port, s.public_url or None, self._advertise_host())
        dialed = body["dialed"]
        if dialed not in own:  # mutcheck: pair-hello-dialed-check-skipped
            raise PairRefusal(
                421,
                "this server does not answer to that address — set public_url if it is reached "
                "through a name or a proxy",
                code="host_not_this_server",
            )
        key = self.pair_key
        pair_id = body["pair_id"]
        now = int(self.now_fn().timestamp())
        challenge = cp.make_challenge(key, pair_id, now, secrets.token_bytes(8))
        k = cp.derive_pair_secret(key, pair_id)
        mac = cp.hello_mac(k, pair_id, body["nonce"], dialed, challenge)
        return {"v": 1, "challenge": challenge, "mac": mac}

    # ── 관리 라우트 ──

    def _pair_urls(self, port: int) -> list[str]:
        """`POST /pair` 응답의 `urls[]`(§4.4 「화면 URL」).

        루프백 bind 에 `public_url` 이 없으면 다른 머신이 닿을 수 없으므로 창을 열지 않는다."""
        s = self.config.server
        if s.public_url:
            return [s.public_url.rstrip("/")]
        bind = (s.bind or "").strip()
        if bind in LOOPBACK_BINDS:
            raise PairRefusal(
                409,
                f"this server listens on {bind} only — other machines cannot reach it; "
                "set bind (and public_url) first",
                code="pair_unreachable",
            )
        if bind not in ANY_BINDS:
            host = f"[{bind}]" if ":" in bind and not bind.startswith("[") else bind
            return [f"http://{host}:{port}"]
        urls = [f"http://{ip}:{port}" for ip in self.pair_ips_fn()]
        host = self._advertise_host()
        if host:
            urls.append(f"http://{host}.local:{port}")
        return urls

    def pair_presets_count(self, pool: str) -> int:
        """그 풀로 갈 프리셋 수 — `/worker/spec` 의 풀 거르기 규칙(§4.5)과 같다: `capable` 이거나
        그 풀이 프리셋의 `pool` · `pools` 에 있고, 후보 함수가 원격을 빼지 않을 때(`local_only`)."""
        from remote_ci_monitor.capabilities import remote_can_take

        return sum(
            1
            for p in self.config.presets
            if (p.placement == PLACEMENT_CAPABLE or p.pool == pool or pool in p.pools)
            and remote_can_take(self.config, p)
        )

    def pair_open(self, body: Any, opened_by: str, port: int) -> dict[str, Any]:
        """`POST /pair`(admin) — 창을 열고 코드를 한 번 돌려준다(§4.4 「관리 라우트」)."""
        if not isinstance(body, dict):
            raise _bad_admin("the pairing request must be a JSON object")
        unknown = sorted(set(body) - OPEN_KEYS)
        if unknown:  # mutcheck: pair-admin-unknown-key-accepted
            raise _bad_admin(f"unknown key(s): {', '.join(unknown)}")
        kind = body.get("kind")
        if kind not in cp.PAIR_KINDS:
            raise _bad_admin('kind must be "worker" or "client"')
        pool = body.get("pool")
        if kind == "client":
            if pool is not None:
                raise _bad_admin("a client window has no pool")
        elif pool is None:
            pool = DEFAULT_POOL
        elif not isinstance(pool, str) or not cp.PAIR_NAME_RE.fullmatch(pool):
            raise _bad_admin("pool must be a short name (letters, digits, . _ -)")
        name = body.get("name")
        if name is not None and (not isinstance(name, str) or not cp.PAIR_NAME_RE.fullmatch(name)):
            raise _bad_admin("name must be a short name (letters, digits, . _ -)")
        replace = body.get("replace", False)
        if not isinstance(replace, bool):
            raise _bad_admin("replace must be true or false")
        if replace and name is None:
            raise _bad_admin("replace needs a name")
        ttl = body.get("ttl_minutes", PAIR_TTL_DEFAULT_MINUTES)
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, int)
            or not 1 <= ttl <= PAIR_TTL_MAX_MINUTES
        ):
            raise _bad_admin(f"ttl_minutes must be a whole number from 1 to {PAIR_TTL_MAX_MINUTES}")
        if not cp.scrypt_available():  # mutcheck: pair-unsupported-crashes
            raise PairRefusal(
                501,
                "this server's Python has no hashlib.scrypt (built without OpenSSL 1.1+) — "
                "pairing is not available on it; use rcm token add",
                code="pair_unsupported",
            )
        urls = self._pair_urls(port)
        windows = self._pair_windows
        with windows.lock:
            now = self.now_fn()
            windows.expire(now)
            current = windows.open_of(kind)
            if current is not None:
                until = iso(current.expires_at)
                raise PairRefusal(
                    409,
                    f"a {kind} pairing window is already open until {until} — close it with "
                    f"rcm pair --cancel --{kind}",
                    code="pair_window_open",
                    expires_at=until,
                )
            if name is not None:
                row = self.store.token_row(name)
                if replace and (row is None or row.kind != kind):
                    raise PairRefusal(
                        400,
                        f"replace needs an existing {kind} token named '{name}'",
                        code="replace_needs_same_kind",
                    )
                if not replace and row is not None:
                    raise PairRefusal(
                        409,
                        f"the coordinator already has a token named '{name}'",
                        code="name_taken",
                        name=name,
                    )
            code = new_code()
            win = PairWindow(
                id=secrets.token_hex(8),
                kind=kind,
                pool=pool,
                name=name,
                replace=replace,
                code_key=cp.pair_code_key(code),
                opened_by=opened_by,
                opened_at=now,
                expires_at=now + timedelta(minutes=ttl),
            )
            windows.windows.append(win)
        count = self.pair_presets_count(pool) if kind == "worker" else None
        warnings = [f"0 presets reach pool {pool}"] if count == 0 else []
        self.log(
            f"pair: {kind} window opened by {opened_by} until {iso(win.expires_at)}"
            + (f" (pool {pool})" if pool else "")
        )
        return {
            "id": win.id,
            "kind": kind,
            "pool": pool,
            "name": name,
            "replace": replace,
            "code": cp.format_pair_code(code),
            "urls": urls,
            "opened_at": iso(win.opened_at),
            "expires_at": iso(win.expires_at),
            "presets_count": count,
            "warnings": warnings,
        }

    def pair_list(self) -> dict[str, Any]:
        """`GET /pair`(admin) — 코드 · 코드 키는 싣지 않는다."""
        windows = self._pair_windows
        with windows.lock:
            rows = windows.listing(self.now_fn())
            return {"windows": [w.doc() for w in rows]}

    def pair_cancel(self, body: Any) -> dict[str, Any]:
        """`POST /pair/cancel`(admin) — 그 kind 의 열린 창을 닫는다. `id` 가 있으면 그 창일 때만."""
        if not isinstance(body, dict) or set(body) - CANCEL_KEYS:
            raise _bad_admin('the cancel request is {"kind": "worker" or "client", "id": <id>}')
        kind, wid = body.get("kind"), body.get("id")
        if kind not in cp.PAIR_KINDS:
            raise _bad_admin('kind must be "worker" or "client"')
        if wid is not None and (not isinstance(wid, str) or not cp.WINDOW_ID_RE.fullmatch(wid)):
            raise _bad_admin("id must be the window id rcm pair printed")
        windows = self._pair_windows
        # 교환과 같은 잠금 — 교환 200 → 취소 404, 또는 취소 200 → 교환 404 뿐이다(G3)
        with windows.lock:  # mutcheck: pair-cancel-without-lock
            now = self.now_fn()
            windows.expire(now)
            win = windows.open_of(kind)
            if win is None or (wid is not None and wid != win.id):
                raise PairRefusal(404, "no open pairing window of that kind", code="no_open_window")
            win.close("cancelled", now)
        self.log(f"pair: {kind} window cancelled")
        return {"id": win.id, "kind": kind, "state": "closed", "reason": "cancelled"}

    def pair_window_state(self, kind: str) -> dict[str, Any] | None:
        """그 kind 의 마지막 창 — 시험과 진단용(실패 수를 본다). 코드 키는 싣지 않는다."""
        with self._pair_windows.lock:
            rows = [w for w in self._pair_windows.windows if w.kind == kind]
            if not rows:
                return None
            w = rows[-1]
            return {"id": w.id, "state": w.state, "reason": w.reason, "failures": w.failures}

    # ── POST /pair/exchange ──

    def pair_exchange(self, body: Any, addr: str) -> dict[str, Any]:
        """익명 교환(§4.4 「교환 요청」 · 「교환 처리」). 코드는 오지 않는다 — 코드 키 증명만(301).
        형식이 틀리면 창을 찾기 **전에** 고정 400(세지 않는다). 익명 교환은 scrypt 를 부르지
        않는다."""
        if not cp.exchange_request_ok(body):  # mutcheck: pair-exchange-shape-not-checked
            raise PairRefusal(400, "bad pairing request", code="bad_pair_request")
        kind, host_name = body["kind"], body["host_name"]
        worker_nonce, proof = body["worker_nonce"], body["proof"]
        if kind not in cp.PAIR_KINDS:
            raise _no_window()
        windows = self._pair_windows
        with windows.lock:
            now = self.now_fn()
            windows.expire(now)
            win = windows.open_of(kind)  # mutcheck: pair-exchange-trusts-body-kind
            proof_kind = kind
            if win is None:
                raise _no_window()
            want = cp.exchange_proof(win.code_key, proof_kind, host_name, worker_nonce)
            # 상수 시간 비교(PI9 spy) — mutcheck: pair-code-compare-not-constant-time
            matched = hmac.compare_digest(proof, want)
            if not matched:
                win.failures += 1  # mutcheck: pair-wrong-code-not-counted
                if win.failures >= cp.PAIR_WRONG_CODE_LIMIT:
                    win.close("too_many_wrong_codes", now)
                    self.log(
                        f"pair: {kind} window closed after {cp.PAIR_WRONG_CODE_LIMIT} wrong codes "
                        "— open a new one with rcm pair"
                    )
                raise _no_window()
            label = cp.clean_pair_label(host_name)
            name = win.name or label or f"{kind}-{secrets.token_hex(3)}"
            if not win.replace and self.store.token_row(name) is not None:
                # 증명이 맞은 뒤에만 나오는 답이라 코드를 가진 쪽에게만 보인다. 창은 열린 채
                raise PairRefusal(
                    409,
                    f"the coordinator already has a token named '{name}'",
                    code="name_taken",
                    name=name,
                )
            pool = win.pool if kind == "worker" else None
            # 답에 실을 값은 행을 쓰기 **전에** 만든다(G2). 여기서 실패하면 창은 열린 채이고
            # 토큰도 없다. 행을 쓴 뒤에는 실패할 수 있는 일이 남지 않는다(MAC 은 순수 계산)
            count = self.pair_presets_count(pool) if pool is not None else None
            pair_id = secrets.token_hex(16)
            pair_secret = cp.format_pair_secret(
                pair_id, cp.derive_pair_secret(self.pair_key, pair_id)
            )
            server_nonce = new_nonce()
            try:
                token = self.store.add_paired_token(
                    name,
                    kind=kind,
                    pool=pool,
                    pair_id=pair_id,
                    paired_host=label or None,
                    paired_addr=addr,
                    now=now,
                    replace=win.replace,
                )
            # 그 사이 rcm token add 가 이름을 가져갔다(G1) — 창은 건드리지 않아 같은 코드가 남는다
            except TokenNameTaken as e:  # mutcheck: pair-name-race-burns-code
                raise PairRefusal(
                    409,
                    f"the coordinator already has a token named '{name}'",
                    code="name_taken",
                    name=name,
                ) from e
            except (StoreError, sqlite3.Error) as e:
                win.close("pair_failed", now)
                self.log(
                    f"pair: {kind} window closed — the token could not be stored "
                    f"({type(e).__name__}); open a new one with rcm pair"
                )
                raise PairRefusal(
                    503,
                    "the coordinator could not store the token and closed the window — open a "
                    "new one (rcm pair) and run this again",
                    code="pair_failed",
                ) from e
            # 창은 토큰 행이 생긴 뒤에야 「쓰임」이다 — 잠금 안이라 다른 교환은 그 사이를 못 본다
            win.state, win.closed_at = "used", now  # mutcheck: pair-code-reusable
            win.paired = {
                "name": name,
                "kind": kind,
                "pool": pool,
                "host_name": label or None,
                "addr": addr,
                "at": now,
            }
            code_key = win.code_key
        mac = cp.exchange_response_mac(
            code_key,
            kind=kind,
            host_name=host_name,
            worker_nonce=worker_nonce,
            server_nonce=server_nonce,
            name=name,
            pool=pool,
            presets_count=count,
            token=token,
            pair_secret=pair_secret,
        )
        # 남기는 것(286): 누가 · 어디서 · 어느 풀로. 원문 host_name · 토큰 · pair_secret 은
        # 싣지 않는다
        shown = label or "—"  # mutcheck: pair-label-not-cleaned
        line = f"pair: {kind} '{name}' paired from {addr} (host {shown}, pool {pool or '—'})"
        self.log(line)  # mutcheck: pair-secret-logged
        return {
            "v": 1,
            "name": name,
            "kind": kind,
            "pool": pool,
            "presets_count": count,
            "token": token,
            "pair_secret": pair_secret,
            "server_nonce": server_nonce,
            "mac": mac,
        }

    # ── 등록 증명 · 거절 MAC ──

    def check_register_proof(
        self,
        token_pair_id: str,
        challenge: Any,
        mac: Any,
        worker_session_id: Any,
        now: datetime | None = None,
    ) -> str | None:
        """짝지은 토큰의 등록 증명(§4.2 「등록 증명」, 313 순서 · V2-6). 통과면 None, 아니면
        `bad_mac` · `expired` · `reused`. K 는 **토큰의** `pair_id` 로 만든다. ①~⑤ 에서 거절된
        요청은 쓴 챌린지 집합을 바꾸지 않는다 — 엿본 챌린지에 틀린 MAC 을 실어 정당한 워커의
        챌린지를 태우지 못한다. 부르는 자리는 M5t-b 등록 순서의 4(PR2)다."""
        at = int((now or self.now_fn()).timestamp())
        verdict = cp.register_proof_verdict(
            self.pair_key, token_pair_id, challenge, mac, worker_session_id, at
        )
        if verdict is not None:
            return verdict
        t = cp.challenge_time(challenge)
        assert t is not None  # 형식은 위에서 봤다
        if not self._pair_challenges.burn(challenge, t, at):
            return "reused"
        return None

    def pair_reject_mac_for(
        self,
        *,
        method: str,
        raw_path: str,
        status: int,
        code: str,
        reason: str,
        nonce: str | None,
        authorization: str | None,
        body_hash: str,
    ) -> str | None:
        """그 거절의 `pair_reject_mac`, 달 수 없으면 None(§4.2 「거절 MAC」). nonce 헤더가 꼴에 맞고
        Bearer 토큰의 행(폐기된 행 포함)에 `pair_id` 가 있을 때만. K 는 그 행의 `pair_id` 로 —
        본문의 어떤 값도 쓰지 않는다(313 과 같은 규칙)."""
        if not isinstance(nonce, str) or not cp.HEX64_RE.fullmatch(nonce):
            return None
        secret = bearer_secret(authorization)
        if secret is None:
            return None
        pair_id = self.store.token_pair_id(secret)  # mutcheck: reject-mac-missing-on-revoked
        if pair_id is None:
            return None
        k = cp.derive_pair_secret(self.pair_key, pair_id)
        bound = nonce  # mutcheck: reject-mac-unbound-nonce
        return cp.pair_reject_mac(k, method, raw_path, status, code, reason, bound, body_hash)


__all__ = [
    "NONCE_HEADER",
    "PAIR_KEY_FILE",
    "REJECT_MAC_KEY",
    "ChallengeLedger",
    "PairKeyError",
    "PairRefusal",
    "PairWindow",
    "PairWindows",
    "PairingMixin",
    "load_or_create_pair_key",
    "new_code",
    "new_nonce",
    "read_pair_key",
    "reject_target",
]
