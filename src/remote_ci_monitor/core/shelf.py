"""보관함(M5p)의 순수 규칙 — 시계 · 「쓴다」 · 정리 계획 · 파일 이름 · 멤버 검사 · 펼칠 후보 ·
이름 충돌 · 상한 · Host 허용 목록 · 같은 출처.

I/O 가 없다. 저장은 `store.py`, 청소와 기동 화해는 `janitor.py`, 보관 스레드와 HTTP 는 서버다.
명세는 docs/m5p-workplan.md §4.3 · §4.4 · §4.6 · §6 · §15(앞 절보다 이긴다).

시각은 전부 epoch 초(float)다. 하루는 `DAY` 초로 센다 — 달력 날짜가 아니라 경과 시간이다.
"""

from __future__ import annotations

import ipaddress
import unicodedata
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from remote_ci_monitor.core.artifacts import MAX_COMPONENT_BYTES, PolicyError, check_path

DAY = 86400.0
HOUR = 3600.0
#: `last_used_at` 은 이전 값보다 이만큼 이상 늦을 때만 쓴다(§4.3 「기록 횟수 줄이기」).
TOUCH_INTERVAL_SECONDS = HOUR
#: 묘비(`deleted`)와 실패 행을 지우기까지(결정 135 · §4.5-3 · 4).
TOMBSTONE_DAYS = 30
#: 보관함 폴더가 맞는 디스크인지 보는 표식 파일(§5.3).
MARKER_NAME = ".rcm-shelf"
#: 푸는 중인 항목이 사는 곳(기동 때 비운다). 항목 폴더와 같은 파일 시스템이라 `os.replace` 가 된다.
STAGING_NAME = ".staging"
#: 펼친 tar 의 옆 폴더 접미사(결정 133).
EXPAND_SUFFIX = ".d"

STATE_PENDING = "pending"
STATE_COPYING = "copying"
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_FAILED = "failed"
STATE_DELETED = "deleted"
#: DB 의 `shelf_items.state`.
SHELF_STATES = (
    STATE_PENDING,
    STATE_COPYING,
    STATE_ACTIVE,
    STATE_STALE,
    STATE_FAILED,
    STATE_DELETED,
)
#: 보관 요청이 아직 끝나지 않은 행 — 이 행의 묶음은 ack · TTL 로 지우지 않는다(결정 130).
IN_FLIGHT_STATES = (STATE_PENDING, STATE_COPYING)
#: 파일이 보관함에 있는 행.
SHELVED_STATES = (STATE_ACTIVE, STATE_STALE)
#: 공개 상태(`GET /jobs/N` 의 `shelf.state`). `waiting` 은 keep 인데 묶음이 아직 ready 도 종결도
#: 아닌 때, `none` 은 묶음이 ready 가 아닌 채 끝났을 때다(§15.1).
PUBLIC_SHELF_STATES = ("waiting", *SHELF_STATES, "none")

#: 보관 행의 `reason_code` 와 공개 `shelf.reason_code` 에 나올 수 있는 코드 전부(§15.6). 문장은
#: 화면 · CLI · 페이지가 만든다(결정 37). `empty` 부터는 `none` 일 때 묶음 상태 이름이 그대로 이유가
#: 되는 것들이다(`unavailable` 은 위에 이미 있다 — 같은 글자다).
SHELF_REASON_CODES = (
    # failed — 새 보관을 못 했다
    "disabled",  # shelf_max_bytes = 0
    "unavailable",  # 보관함 폴더가 표식 없이 비었다(§5.3)
    "shelf_full",
    "disk_low",
    "name_collision",
    "dest_exists",
    "no_safe_files",
    "copy_error",
    "timeout",  # 안전판 — 묶음 만료 + 24시간이 지나도록 못 집었다(§15.2)
    "bundle_gone",  # 보관하려던 묶음이 이미 없다
    "missing",  # 보관했는데 폴더가 사라졌다
    # none — 보관할 것이 없었다
    "not_collected_on_success",  # artifacts_on = "failure" 인데 성공
    "no_artifacts",  # 프리셋에 글롭이 없다
    "forgotten",  # 묘비까지 지워진 뒤라 무엇이었는지 모른다
    "empty",
    "dropped",
    "failed",
    "skipped",
    "purged",
    "expired",
    "unknown",
)
#: 보관함 전체의 `error_code`(`/api/status.server.shelf_storage`). 항목의 이유가 아니다.
SHELF_ERROR_CODES = (
    "shelf_dir_missing",
    "shelf_dir_unwritable",
    "copier_dead",
    "copier_stuck",
)

Stage = Literal["active", "stale", "delete"]
Route = Literal["item", "file", "zip", "restore", "keep", "list", "latest", "api"]

# ── 시계 (§4.3 · 결정 131 · 132) ─────────────────────────────────────────────


def stage(last_used_at: float, now: float, stale_days: int, delete_days: int) -> Stage:
    """마지막으로 쓴 지 얼마나 됐나로 정한 단계. 시계가 뒤로 가면 경과를 0 으로 본다(§15.3)."""
    elapsed = max(0.0, float(now) - float(last_used_at))
    if elapsed < stale_days * DAY:
        return "active"
    if elapsed < delete_days * DAY:
        return "stale"
    return "delete"


def due_dates(last_used_at: float, stale_days: int, delete_days: int) -> tuple[float, float]:
    """(정리 대기 예정 시각, 삭제 예정 시각). 화면이 그대로 보인다."""
    return last_used_at + stale_days * DAY, last_used_at + delete_days * DAY


def should_touch(prev_last_used: float | None, state: str, now: float) -> bool:
    """`last_used_at` 을 지금 써야 하나.

    보관 중(`active`)은 이전 값보다 **1시간 이상** 늦을 때만 쓴다 — 그림 격자 한 장마다 DB 쓰기가
    나가지 않게. 정리 대기(`stale`)는 첫 쓰기에 바로 쓴다(그 쓰기가 되살리기다). 그 밖의 상태는
    쓸 시계가 없다.
    """
    if state == STATE_STALE:
        return True
    if state != STATE_ACTIVE:
        return False
    if prev_last_used is None:
        return True
    return float(now) - float(prev_last_used) >= TOUCH_INTERVAL_SECONDS


#: route → 그 route 가 「쓰는」 메서드. 표에 없는 route 는 쓰지 않는다(§15.3).
_TOUCHING: dict[str, str] = {
    "item": "GET",
    "file": "GET",
    "zip": "GET",
    "restore": "POST",
    "keep": "POST",
}


def touches(method: str, route: str, item_state: str) -> bool:
    """이 요청이 항목을 「쓴」 것인가(§4.3 표 · 결정 132).

    `item_state` 는 DB 열이 아니라 `stage()` 값이다(§15.5) — 삭제 단계가 지났는데 아직 안 지운
    항목(`delete`)은 정리 대기로 대한다. 판정은 라우터가 HEAD 를 GET 으로 바꾸기 **전의** 메서드로
    한다. 정리 대기 항목의 페이지(`item`)는 쓰지 않는다 — 지울지 판단하려고 보는 것이 되살리기가
    되면 안 된다.
    """
    if _TOUCHING.get(route) != method:
        return False
    state = STATE_STALE if item_state == "delete" else item_state
    if route == "item":
        return state == STATE_ACTIVE
    return state in SHELVED_STATES


# ── 정리 계획 (§4.5 · 결정 134 · 135) ────────────────────────────────────────


@dataclass(frozen=True)
class SweepPlan:
    """청소 한 회차가 할 일. 각 목록은 항목 그대로다(잡 번호 순)."""

    to_stale: list[Any] = field(default_factory=list)
    #: `shelf_stale_days` 를 늘려 재시작하면 정리 대기가 다시 보관 중 구간이 된다(§15.3)
    to_active: list[Any] = field(default_factory=list)
    to_delete: list[Any] = field(default_factory=list)
    #: 30일 지난 묘비와 실패 행 — DB 행만 지운다
    to_forget: list[Any] = field(default_factory=list)


def _failed_since(item: Any) -> float:
    """실패 행의 기준 시각. `failed_at` 이 없는 옛 행은 요청 시각에서 잰다(영원히 남지 않게)."""
    at = getattr(item, "failed_at", None)
    return float(at if at is not None else item.requested_at)


def plan_sweep(
    items: Iterable[Any],
    now: float,
    stale_days: int,
    delete_days: int,
    tombstone_days: int = TOMBSTONE_DAYS,
) -> SweepPlan:
    """무엇을 정리 대기로 옮기고 무엇을 지우고 무엇을 잊을지. **아무것도 바꾸지 않는다.**

    진행 중(`pending` · `copying`) 행은 건드리지 않는다. 서버가 1년 꺼져 있었으면 그 항목은
    정리 대기를 거치지 않고 한 회차에 지운다.
    """
    plan = SweepPlan()
    keep_for = tombstone_days * DAY
    for item in sorted(items, key=lambda i: i.job_id):
        state = item.state
        if state in SHELVED_STATES:
            used = item.last_used_at if item.last_used_at is not None else item.requested_at
            st = stage(used, now, stale_days, delete_days)
            if st == "delete":
                plan.to_delete.append(item)
            elif st == "stale" and state == STATE_ACTIVE:
                plan.to_stale.append(item)
            elif st == "active" and state == STATE_STALE:
                plan.to_active.append(item)
        elif state == STATE_DELETED:
            at = item.deleted_at if item.deleted_at is not None else item.requested_at
            if float(now) - float(at) >= keep_for:
                plan.to_forget.append(item)
        elif state == STATE_FAILED and float(now) - _failed_since(item) >= keep_for:
            plan.to_forget.append(item)
    return plan


# ── 파일 응답 (§4.6 · 결정 139) ──────────────────────────────────────────────

_IMAGES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_TEXTS = (".txt", ".log", ".json", ".md", ".csv")
TEXT_PLAIN = "text/plain; charset=utf-8"
OCTET_STREAM = "application/octet-stream"


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def inline_disposition(path: str) -> tuple[str, bool]:
    """(content type, 인라인으로 보여도 되나). 허용 목록 밖은 전부 내려받기다.

    `.html` · `.svg` · `.js` · `.pdf` 를 인라인으로 내면 잡이 만든 문서가 웹 UI 와 같은 출처에서
    돈다(결정 139). 확장자는 대소문자를 가리지 않는다(§15.4).
    """
    ext = _suffix(path)
    if ext in _IMAGES:
        return _IMAGES[ext], True
    if ext in _TEXTS:
        return TEXT_PLAIN, True
    return OCTET_STREAM, False


def _ascii_fallback(name: str) -> str:
    """`filename=` 에 넣을 이름 — 인쇄 가능한 ASCII 만, 따옴표 · 역슬래시도 `_` 로."""
    return "".join(c if 0x20 <= ord(c) < 0x7F and c not in '"\\' else "_" for c in name)


def content_disposition(path: str, inline: bool) -> str:
    """`Content-Disposition` 값. 이름은 경로의 마지막 조각이다(§15.4).

    `send_header` 는 CRLF 를 검사하지 않고 latin-1 로만 인코딩한다 — 원래 이름을 그대로 넣으면
    한글 이름은 500, 개행이 든 이름은 헤더 주입이다. 그래서 안전한 ASCII `filename=` 과 퍼센트
    인코딩한 `filename*=UTF-8''…` 만 쓴다.
    """
    name = path.rsplit("/", 1)[-1]
    kind = "inline" if inline else "attachment"
    encoded = urllib.parse.quote(name, safe="")
    return f"{kind}; filename=\"{_ascii_fallback(name)}\"; filename*=UTF-8''{encoded}"


# ── 멤버 경로 (§4.6 · §15.4 · 검토 P0-7) ─────────────────────────────────────


def _has_control(path: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in path)


def safe_member(path: Any) -> bool:
    """보관함에 두거나 내보내도 되는 상대 경로인가. 예외를 내지 않는다.

    `core/artifacts.check_path`(정규형 · `..` · 절대 경로 · 조각 길이) + C0 제어문자 + `.git` 조각을
    대소문자 없이(APFS 에서 `.GIT` 은 `.git` 이다). 정규화하지 않는다 — tar 의 `./` 는 부르는 쪽이
    `normalize_member` 로 먼저 뗀다.
    """
    if not isinstance(path, str):
        return False
    try:
        check_path(path)
    except PolicyError:
        return False
    if _has_control(path):
        return False
    return not any(part.casefold() == ".git" for part in path.split("/"))


def normalize_member(name: str) -> str:
    """tar 멤버 이름 앞의 `./`(여러 번)와 디렉터리 끝의 `/` 를 뗀다(`tar -C dir .` 모양, §15.4)."""
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/")


# ── 펼치기 (§4.4 · 결정 133) ─────────────────────────────────────────────────

_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz")


def is_tar_name(path: str) -> bool:
    """`*.tar` · `*.tar.gz` · `*.tgz` 인가(대소문자 무관). zip 과 다른 압축은 아니다."""
    return path.lower().endswith(_TAR_SUFFIXES)


def expand_dir(path: str) -> str:
    """펼친 파일이 들어갈 옆 폴더 — `<이름>.d`."""
    return path + EXPAND_SUFFIX


def expand_candidates(paths: Iterable[str]) -> list[str]:
    """한 단계 펼칠 tar 들(정렬). `<이름>.d` 가 조각 길이 상한을 넘는 것은 뺀다(`name_too_long`)."""
    out = set()
    for path in paths:
        if not is_tar_name(path):
            continue
        last = expand_dir(path).rsplit("/", 1)[-1]
        if len(last.encode("utf-8")) > MAX_COMPONENT_BYTES:
            continue
        out.add(path)
    return sorted(out)


# ── 이름 충돌 (§4.2-4 · §15.4) ───────────────────────────────────────────────


def _fold(path: str) -> str:
    """접는 파일 시스템이 같다고 볼 형태(`core/artifacts.collision_key` 와 같다)."""
    return unicodedata.normalize("NFC", path).casefold()


def name_collisions(paths: Sequence[str] | Iterable[str]) -> list[tuple[str, str]]:
    """접었을 때 겹치는 경로 짝(정렬). 같은 이름과 **디렉터리 접두**(`A` 와 `a/b.png`)를 둘 다 본다.

    키로 묶은 사전 하나로 O(n · 깊이)다 — `core/artifacts.collisions` 의 O(n²) 는 펼친 tar 로
    수만 줄이 되는 보관 항목에서 못 쓴다. 결과는 그 함수와 같다(같은 경로 두 번도 한 짝이다).
    """
    items = list(paths)
    groups: dict[str, list[int]] = {}
    keys = [_fold(p) for p in items]
    for i, key in enumerate(keys):
        groups.setdefault(key, []).append(i)
    out: list[tuple[str, str]] = []
    for members in groups.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                x, y = items[members[a]], items[members[b]]
                out.append((x, y) if x <= y else (y, x))
    for j, key in enumerate(keys):
        cut = key.find("/")
        while cut != -1:
            for i in groups.get(key[:cut], ()):
                x, y = items[i], items[j]
                out.append((x, y) if x <= y else (y, x))
            cut = key.find("/", cut + 1)
    return sorted(out)


# ── 상한 (§4.2-5 · 결정 136 · 137) ───────────────────────────────────────────


def capacity_verdict(
    stored: int, actual: int, limit: int, free: int | None, min_free: int
) -> str | None:
    """새 항목을 들일 수 있나. 차례는 꺼짐 → 상한 → 여유 바닥이다.

    `free` 를 못 쟀으면(None) 바닥 검사를 건너뛴다(M5g 와 같다). 넘쳐도 남의 항목을 쫓아내지
    않는다 — 새 보관을 거절할 뿐이다.
    """
    if limit <= 0:
        return "disabled"
    if stored + actual > limit:
        return "shelf_full"
    if free is not None and free - actual < min_free:
        return "disk_low"
    return None


# ── Host 허용 목록 (결정 148 · §15.5) ────────────────────────────────────────

_PRIVATE_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",  # Tailscale(CGNAT)
        "127.0.0.0/8",
        "fc00::/7",
        "::1/128",
    )
)
_LOCAL_SUFFIXES = (".local", ".ts.net")


def split_host(value: str | None) -> tuple[str, int | None] | None:
    """`Host` 값 → (소문자 · 끝의 점을 뗀 이름, 포트). 모양이 틀리면 None.

    IPv6 는 괄호(`[fd00::1]:8787`)로만 받는다 — 괄호 없는 콜론 여럿은 Host 헤더가 아니다.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    port_text = ""
    if text.startswith("["):
        close = text.find("]")
        if close == -1:
            return None
        name, rest = text[1:close], text[close + 1 :]
        if rest:
            if not rest.startswith(":"):
                return None
            port_text = rest[1:]
    else:
        if text.count(":") > 1:
            return None
        name, _sep, port_text = text.partition(":")
        if _sep and not port_text:
            return None
    name = name.rstrip(".")
    if not name:
        return None
    port: int | None = None
    if port_text:
        if not port_text.isdigit() or not 0 < int(port_text) < 65536:
            return None
        port = int(port_text)
    return name, port


def _ip(name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(name)
    except ValueError:
        return None


def host_allowed(
    host: str,
    *,
    public_host: str | None,
    hostname: str,
    also: Iterable[str] = (),
) -> bool:
    """`/shelf*` 가 답해도 되는 `Host` 인가. 아니면 421 이다(DNS rebinding 을 막는다).

    로컬 이름(`localhost` · `*.local` · `*.ts.net`), 사설 IP(10/8 · 172.16/12 · 192.168/16 ·
    100.64/10 · 127/8 · fc00::/7 · ::1), `public_url` 의 호스트, 서버의 hostname, 그리고 `also`
    (bind 주소 · `base_url` 의 호스트). 비교 전에 소문자 · 끝의 점 · IPv6 괄호를 정리하고 포트는
    따로 뗀다.
    """
    parsed = split_host(host)
    if parsed is None:
        return False
    name, _port = parsed
    if name == "localhost":
        return True
    if any(name.endswith(s) and len(name) > len(s) for s in _LOCAL_SUFFIXES):
        return True
    ip = _ip(name)
    if ip is not None and any(ip in net for net in _PRIVATE_NETS):
        return True
    known = [public_host or "", hostname or "", *also]
    for other in known:
        got = split_host(other)
        if got is not None and got[0] == name:
            return True
    return False


# ── 같은 출처 (결정 145 · §15.5) ─────────────────────────────────────────────

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_of(value: str, schemes: Sequence[str]) -> tuple[str, str, int] | None:
    """`http://host:port[/…]` → (scheme, host, port). 포트가 없으면 그 scheme 의 기본 포트다.

    userinfo · 모르는 scheme · 틀린 포트는 None."""
    try:
        parts = urllib.parse.urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in schemes or "@" in parts.netloc or not parts.hostname:
        return None
    name = parts.hostname.lower().rstrip(".")
    return scheme, name, port if port is not None else _DEFAULT_PORTS.get(scheme, 0)


def same_origin(
    host: str,
    origin: str | None,
    referer: str | None,
    *,
    schemes: Sequence[str] = ("http",),
) -> bool:
    """되살리기 POST 가 같은 출처에서 왔나. 둘 다 없으면 False(403).

    `Origin` 이 있으면 그것만 본다 — Referer 로 물러서지 않는다. `Origin` 이 없거나 문자열
    `null` 일 때만 `Referer` 의 scheme + host 를 같은 방식으로 본다. host 는 소문자로, 포트는
    같아야 한다. `schemes` 는 기본 http 이고 `public_url` 이 https 면 서버가 https 도 넘긴다.
    """
    want = split_host(host)
    if want is None:
        return False
    if origin is not None and origin.strip() != "null":
        got = _origin_of(origin, schemes)
    elif referer is not None:
        got = _origin_of(referer, schemes)
    else:
        return False
    if got is None:
        return False
    name, port = want
    scheme, origin_name, origin_port = got
    if origin_name != name:
        return False
    if port is None:
        # 포트 없는 Host 는 그 scheme 의 기본 포트다 — `http://x:443` 은 다른 출처다(I5)
        return origin_port == _DEFAULT_PORTS.get(scheme)
    return origin_port == port


__all__ = [
    "DAY",
    "IN_FLIGHT_STATES",
    "MARKER_NAME",
    "PUBLIC_SHELF_STATES",
    "SHELF_ERROR_CODES",
    "SHELF_REASON_CODES",
    "SHELF_STATES",
    "SHELVED_STATES",
    "STAGING_NAME",
    "SweepPlan",
    "capacity_verdict",
    "content_disposition",
    "due_dates",
    "expand_candidates",
    "expand_dir",
    "host_allowed",
    "inline_disposition",
    "is_tar_name",
    "name_collisions",
    "normalize_member",
    "plan_sweep",
    "safe_member",
    "same_origin",
    "should_touch",
    "split_host",
    "stage",
    "touches",
]
