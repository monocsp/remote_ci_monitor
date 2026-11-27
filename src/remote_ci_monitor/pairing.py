"""짝짓기 · 서버 신원의 I/O 계층(M5t-c · docs/m5tc-workplan.md §4.2 · §4.4, PR1).

순수 계산은 `core/pairing.py` 에 있고 여기는 그것을 부르는 쪽이다: `pair.key` 파일, `secrets` 로
만드는 nonce · 코드 · `pair_id` · 챌린지 난수, 서버 시계, 짝짓기 창(메모리)과 그 잠금, 쓴 챌린지
집합, 서버 믹스인(라우트 처리 · 등록 증명 검사 · 거절 MAC).

값(코드 · 코드 키 · 토큰 · `pair_secret` · `pair.key`)은 로그 · 오류 문장 · 응답 어디에도 싣지
않는다(PI17). 코드는 `POST /pair` 의 201 응답에만, 토큰과 `pair_secret` 은 교환 200 에만 있다.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote_ci_monitor.store import StoreError

PAIR_KEY_FILE = "pair.key"
PAIR_KEY_BYTES = 32


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
        os.close(fd)
        _unlink_quietly(tmp)
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


__all__ = ["PAIR_KEY_FILE", "PairKeyError", "load_or_create_pair_key", "read_pair_key"]
