"""잡 산출물(M5e)의 순수 규칙 — 상태·이유 코드 · 글롭 · 경로·충돌 · 회계 · 분류 · ack 판정.

I/O 가 없다. 파일을 읽고 tar 를 만드는 것은 `collect.py`, 저장은 `store.py` 다.

이름 주의: 여기서 다루는 「산출물」은 잡이 만든 파일 묶음(**bundle**)이다.
`jobs.artifacts_purged_at`(M3)은 **다른 것**이다 — 보존 정리가 그 잡의 로그·스냅샷·워크스페이스를
지운 시각이다. 섞으면 청소기가 남의 것을 지운다(명세 §1).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# 글롭 → 정규식 조각은 스냅샷 무시 규칙과 **같은 문법**을 쓴다. 다만 저쪽은 조각을 `search` 로
# 쓰고(어디서든 맞는다) 이쪽은 뿌리 기준 전체 일치라, 여기서 앵커를 붙인다(명세 §4).
from remote_ci_monitor.core.snapshot import _glob_to_regex

#: 경로 조각 하나의 길이(UTF-8 바이트). `core/manifest.py` 에는 조각 제한이 없어서 여기서 더한다.
MAX_COMPONENT_BYTES = 255
#: 경로 전체 길이 — manifest 와 같은 값.
MAX_PATH_LEN = 4096

TAR_HEADER_BYTES = 512  # 멤버마다 붙는 헤더
TAR_TRAILER_BYTES = 1024  # 끝의 0 블록 두 개
TAR_PER_FILE_BYTES = 1024  # 헤더 512 + 데이터 끝의 512 정렬 패딩
TAR_RECORD_BYTES = 10240  # tarfile 이 닫을 때 채우는 레코드(20 × 512)

DISABLED = "disabled"  # 프리셋에 글롭이 없다
PENDING = "pending"  # 잡이 아직 안 끝났다
COLLECTING = "collecting"
UPLOADING = "uploading"
READY = "ready"
EMPTY = "empty"  # 모았는데 내보낼 것이 없었다 — `unknown` 과 다르다
DROPPED = "dropped"  # 상한·충돌로 버렸다
FAILED = "failed"  # 수집·업로드가 깨졌다
SKIPPED = "skipped"  # 실행이 시작되지 못했다
PURGED = "purged"  # 확인(ack)을 받고 지웠다
EXPIRED = "expired"  # TTL 이 지나 청소기가 지웠다
UNAVAILABLE = "unavailable"  # 메타는 있는데 파일이 없다
UNKNOWN = "unknown"  # M5e 이전 잡, 또는 보고하지 않은 옛 워커

ARTIFACT_STATES = frozenset(
    {
        DISABLED,
        PENDING,
        COLLECTING,
        UPLOADING,
        READY,
        EMPTY,
        DROPPED,
        FAILED,
        SKIPPED,
        PURGED,
        EXPIRED,
        UNAVAILABLE,
        UNKNOWN,
    }
)
#: 아카이브 GET 이 410 — 있었지만 이제 없다.
GONE_STATES = frozenset({PURGED, EXPIRED})
#: 아카이브 GET 이 409 — 아직 준비 중이다.
PROGRESS_STATES = frozenset({PENDING, COLLECTING, UPLOADING})
#: 아카이브 GET 이 404 — 애초에 없다.
NOTHING_STATES = frozenset({DISABLED, EMPTY, SKIPPED, UNKNOWN})

#: 서버는 문장이 아니라 코드를 내려보낸다(결정 37). 문장은 화면·CLI 가 만든다.
REASONS = frozenset(
    {
        "over_bytes",
        "over_files",
        "no_match",
        "path_conflict",
        "unsafe_path",
        "collect_failed",
        "upload_failed",
        "storage_full",
        "timed_out",
        "cancelled",
        "not_run",
        "interrupted",
        "acked",
        "expired",
        "hash_mismatch",
        "not_ready",
    }
)

_WILDCARD = "*?["


class ArtifactError(Exception):
    """산출물을 모으거나 낼 수 없다. 메시지는 짧고 서버 경로를 싣지 않는다."""


class PolicyError(ValueError):
    """글롭이나 경로가 규칙에 안 맞는다. 설정 검증과 수집기가 둘 다 쓴다."""


@dataclass(frozen=True)
class ArtifactPolicy:
    """잡 하나에 얼려 두는 수집 정책. 워커 claim 응답에 실려 나가고 업로드 검증도 이것으로 한다."""

    globs: tuple[str, ...] = ()
    max_bytes: int = 1_073_741_824  # 1 GiB
    max_files: int = 10_000
    timeout_seconds: int = 60
    cancel_timeout_seconds: int = 5

    def enabled(self) -> bool:
        """글롭을 선언하지 않은 프리셋은 아무것도 모으지 않는다."""
        return bool(self.globs)


@dataclass(frozen=True)
class BundleFile:
    """묶음 안의 파일 하나. `mode` 는 0o644 아니면 0o755 로 정규화된 값이다."""

    path: str
    size: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class AckDecision:
    """ack 하나의 판정. `record` 는 `acked_at` 을 쓸 것인가(관리자 대리 확인은 False)."""

    status: int  # 200 · 409 · 410
    purge: bool  # 지금 지워도 되는가
    record: bool
    reason_code: str | None


# ── 글롭 ─────────────────────────────────────────────────────────────────────


def _check_glob(glob: Any) -> str:
    if not isinstance(glob, str) or not glob:
        raise PolicyError("artifact glob must be a non-empty string")
    if "\x00" in glob or "\\" in glob:
        raise PolicyError(f"artifact glob has NUL or backslash: {glob[:80]!r}")
    if glob.startswith("/"):
        raise PolicyError(f"artifact glob must be relative: {glob[:80]!r}")
    parts = glob.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PolicyError(f"artifact glob has empty, '.' or '..' components: {glob[:80]!r}")
    if ".git" in parts:
        raise PolicyError(f"artifact glob names .git: {glob[:80]!r}")
    i = 0
    while i < len(glob):  # 닫히지 않은 문자 클래스는 조용히 리터럴 `[` 가 된다 — 막는다
        if glob[i] == "[":
            close = glob.find("]", i + 1)
            if close == -1:
                raise PolicyError(f"artifact glob has an unclosed character class: {glob[:80]!r}")
            i = close
        i += 1
    if not _has_a_literal_anchor(parts):
        # 워크스페이스를 통째로 돌려보내는 실수를 막는다. 유출 방어가 아니다 — 잡은 워커 권한으로
        # 돌기 때문에 글롭으로 막을 수 있는 것이 아니다(명세 §2).
        raise PolicyError(f"artifact glob matches the whole workspace: {glob[:80]!r}")
    return glob


def _has_a_literal_anchor(parts: Sequence[str]) -> bool:
    """리터럴 조각이 하나라도 있거나, 마지막 조각에 리터럴 확장자가 있어야 한다."""
    if any(not any(c in _WILDCARD for c in p) for p in parts):
        return True
    last = parts[-1]
    head, dot, ext = last.rpartition(".")
    return bool(dot and head and ext and not any(c in _WILDCARD for c in ext))


def validate_globs(globs: Sequence[Any]) -> tuple[str, ...]:
    """설정의 `[[presets]].artifacts` 검증. 하나라도 어긋나면 목록 전체를 거절한다."""
    return tuple(_check_glob(g) for g in globs)


def compile_globs(globs: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    """워크스페이스 뿌리 기준 **전체 일치** 패턴. 순서와 개수를 그대로 지킨다.

    `\\A…\\Z` 로 감싼다 — `$` 는 끝의 개행 앞에서도 맞아서 `goldens/evil.png\\n` 이 통과한다.
    """
    return tuple(re.compile(r"\A(?:" + _glob_to_regex(g) + r")\Z") for g in globs)


# ── 경로 · 충돌 ──────────────────────────────────────────────────────────────


def check_path(path: Any) -> str:
    """묶음에 담을 상대 경로 하나를 검사한다. 정규화하지 않는다 — 정규형만 받는다."""
    if not isinstance(path, str) or not path:
        raise PolicyError("artifact path must be a non-empty string")
    if len(path) > MAX_PATH_LEN:
        raise PolicyError(f"artifact path longer than {MAX_PATH_LEN}")
    if "\x00" in path or "\\" in path:
        raise PolicyError(f"artifact path has NUL or backslash: {path[:80]!r}")
    if path.startswith("/"):
        raise PolicyError(f"artifact path is absolute: {path[:80]!r}")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PolicyError(f"artifact path has empty, '.' or '..' components: {path[:80]!r}")
    if ".git" in parts:
        raise PolicyError(f"artifact path names .git: {path[:80]!r}")
    for part in parts:
        if len(part.encode("utf-8")) > MAX_COMPONENT_BYTES:
            raise PolicyError(f"artifact path component longer than {MAX_COMPONENT_BYTES} bytes")
    return path


def collision_key(path: str) -> str:
    """이름을 접는 파일 시스템이 같다고 볼 형태. NFC 로 모은 뒤 casefold 한다(명세 §4)."""
    return unicodedata.normalize("NFC", path).casefold()


def collisions(paths: Iterable[str]) -> list[tuple[str, str]]:
    """접었을 때 겹치는 경로 짝. 같은 이름과 **디렉터리 접두**를 둘 다 본다.

    `A` 와 `a/b.png` 는 겹친다 — 접는 파일 시스템에서는 파일 `A` 와 디렉터리 `a` 가 같은 이름이다.
    """
    items = list(paths)
    keys = [collision_key(p) for p in items]
    out: list[tuple[str, str]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = keys[i], keys[j]
            if a == b or b.startswith(a + "/") or a.startswith(b + "/"):
                out.append(tuple(sorted((items[i], items[j]))))  # type: ignore[arg-type]
    return sorted(out)


def select(paths: Iterable[str], globs: Sequence[str]) -> list[str]:
    """글롭에 맞는 경로만 정렬해서. `.git` 조각은 글롭이 뭐라고 하든 뺀다."""
    patterns = compile_globs(globs)
    if not patterns:
        return []
    out = set()
    for path in paths:
        if ".git" in path.split("/"):
            continue
        if any(p.match(path) for p in patterns):
            out.add(path)
    return sorted(out)


# ── 회계 ─────────────────────────────────────────────────────────────────────


def archive_allowance(max_bytes: int, max_files: int) -> int:
    """원본 바이트 상한 → 아카이브 바이트 허용치(업로드 admission 이 `Content-Length` 로 본다).

    파일마다 헤더 512 와 데이터 끝의 512 정렬 패딩, 그리고 `tarfile` 이 닫을 때 채우는 레코드
    10240. 원본 상한과 직접 비교하면 경계 크기의 정상 묶음이 413 으로 거절된다(명세 §8).
    """
    return max_bytes + max_files * TAR_PER_FILE_BYTES + TAR_RECORD_BYTES


# ── 클라이언트 적용 분류 ─────────────────────────────────────────────────────


def classify(baseline: str | None, local: str | None, incoming: str) -> str:
    """받은 파일 하나를 트리에 쓸 자격을 판정한다(명세 §11). 위에서부터 먼저 맞는 줄이 이긴다.

    `baseline` 은 **제출할 때 실제로 보낸** 내용의 sha256(없던 경로면 None), `local` 은 지금
    디스크에 있는 것(지워졌으면 None).
    """
    if local is None and baseline is None:
        return "new"
    if local == incoming:
        return "unchanged"  # 바이트가 같으면 쓰든 말든 잃을 게 없다
    if baseline is None or local is None:
        return "conflicted"  # 안 보낸 파일이 다르게 있거나, 사람이 지웠다
    if local == baseline:
        return "changed"  # 골든 갱신의 본체
    return "conflicted"  # 기다리는 동안 사람이 고쳤다


# ── 삭제 규칙 ────────────────────────────────────────────────────────────────


def ack_decision(
    *,
    state: str,
    join_count: int,
    owner: bool,
    stored_sha: str | None,
    sent_sha: str,
    expired: bool,
) -> AckDecision:
    """확인(ack) 하나의 판정(명세 §7). 검사 순서는 **만료 → 해시 → 상태 → 자격**이다.

    해시를 자격보다 먼저 보는 이유: 남의 잡을 확인해 주려는 관리자에게도 「해시가 틀렸다」는 사실을
    먼저 알려야 한다. 자격은 지울지 말지에만 관여한다.
    """
    if expired or state == EXPIRED:
        return AckDecision(410, False, False, "expired")
    if not stored_sha or sent_sha != stored_sha:
        return AckDecision(409, False, False, "hash_mismatch")
    if state not in (READY, PURGED):
        return AckDecision(409, False, False, "not_ready")
    if state == PURGED:
        # 응답을 잃은 클라이언트의 재시도다. 이미 없으니 다시 지우지 않고 묘비도 안 덮어쓴다.
        return AckDecision(200, False, False, "acked")
    if not owner:
        # 요청자도 합류자도 아닌 관리자다 — 남의 몫을 대신 확인해 주지 않는다.
        return AckDecision(200, False, False, None)
    # 합류가 한 번이라도 있었으면 확인이 와도 TTL 까지 둔다(결정 40).
    return AckDecision(200, join_count == 0, True, "acked")


def expires_at(ready_at: datetime, hours: int) -> datetime:
    """TTL 은 **발행 시각**부터 잰다. 읽어도 연장되지 않는다."""
    return ready_at + timedelta(hours=hours)
