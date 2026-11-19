"""보관함(M5p) copier — 묶음 tar 를 보관함 폴더로 풀고, 안의 tar 를 한 단계 펼친다.

명세는 docs/m5p-workplan.md §4.2(①~⑦) · §4.4 · §15.2 · §15.4 · §15.5. 순서:

1. 행을 `copying` 으로 집는다(`Store.claim_shelf_item`, pending 가운데 오래된 것부터, 하나씩).
2. `<shelf_dir>/.staging/<job>.<hex>/files/` 에 `bundle.tar` 를 **스트림으로**(`r|*`) 멤버마다 푼다.
   서버가 받은 tar 를 믿지 않는다 — 멤버마다 `./` 를 떼고 `safe_member` 를 다시 보고, 일반 파일만
   만든다. 링크 · 장치 · FIFO · sparse · 제어문자 이름은 **그 멤버만** 건너뛰고 `skipped[]` 에
   적는다.
3. 펼치기: 묶음의 `*.tar` · `*.tar.gz` · `*.tgz` 를 한 단계 `<이름>.d/` 로. 상한은 항목 전체 합
   (`max_artifact_bytes` · `max_artifact_files`)이고 헤더의 크기로 미리 거절하며 쓴 바이트도 센다.
   넘으면 그 tar 의 펼침만 버리고 `expand_skipped[]` 에 이유를 적는다. 망가진 tar 도 같다.
4. 이름 충돌(대소문자 · NFC · 파일과 디렉터리 접두)은 묶음과 펼침을 **합친 집합**에서 본다. 있으면
   항목 전체가 `failed(name_collision)` — APFS 에서 하나가 다른 것을 덮은 채 manifest 가 거짓이 되는
   것을 막는다. 쓰기는 `O_EXCL` 이라 덮어쓰기 자체도 일어나지 않는다.
5. 상한은 다 만든 뒤 **실측 바이트**(원본 + 펼침 + manifest)로 판정한다.
6. manifest 를 쓰고 폴더를 `<shelf_dir>/<job>/` 로 `os.replace`. 대상이 이미 있으면 덮지 않는다.
7. 어디서든 실패하면 staging 을 지우고 `failed(<이유>)`. 잡과 묶음은 건드리지 않는다.

끝나면(성공이든 실패든) 미뤄 둔 ack 삭제를 푼다(`Store.release_deferred_bundle`). 스레드는 하나이고
이벤트 + 5초 폴링으로 깨어난다. 항목 하나의 예외는 그 항목의 실패로 접고, DB 잠김은 다음 깨움에 다시
한다. 같은 예외가 5번 연달아 나면 `copier_stuck`, 스레드가 죽으면 `copier_dead` 다.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tarfile
import threading
import zlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core import shelf as shelf_rules

#: 이벤트를 놓쳐도 이만큼 안에 pending 을 줍는다(§15.5) — 값싼 쿼리 하나다.
SHELF_POLL_SECONDS = 5.0
#: 같은 예외가 이만큼 연달아 나면 `copier_stuck`(§15.5).
STUCK_AFTER = 5
CHUNK = 64 * 1024
#: manifest 의 `skipped[]` · `expand_skipped[]` 에 적는 줄 수의 상한 — 나머지는 수로만 센다(D1).
#: 링크 멤버 200만 개짜리 tar 하나가 187 MB manifest 를 만들고 API 한 번에 RSS 를 1 GB 올렸다
SKIPPED_LINES_MAX = 200
#: 펼치다 깨진 tar 로 보는 예외들 — 그 tar 의 펼침만 버린다(항목은 산다).
_BROKEN_TAR = (tarfile.TarError, EOFError, zlib.error, OSError, ValueError)


class ShelveError(Exception):
    """항목 하나를 접는 이유(`SHELF_REASON_CODES` 의 하나)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _SkipExpand(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _Names:
    """접는 파일 시스템에서 겹치는 이름을 들어오는 대로 잡는다(`name_collisions` 의 증분판)."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()

    @staticmethod
    def fold(path: str) -> str:
        import unicodedata

        return unicodedata.normalize("NFC", path).casefold()

    def add(self, path: str) -> None:
        key = self.fold(path)
        if key in self.files or key in self.dirs:
            raise ShelveError("name_collision")
        cut = key.find("/")
        while cut != -1:
            if key[:cut] in self.files:
                raise ShelveError("name_collision")
            cut = key.find("/", cut + 1)
        cut = key.find("/")
        while cut != -1:
            self.dirs.add(key[:cut])
            cut = key.find("/", cut + 1)
        self.files[key] = path


def _unsafe(member: tarfile.TarInfo, name: str) -> str | None:
    """이 멤버를 왜 안 만드나. 만들어도 되면 None."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return "bad_encoding"
    if not shelf_rules.safe_member(name):
        return "unsafe_name"
    if member.issparse():
        return "sparse"
    if not member.isreg():
        return "not_a_file"
    return None


def _printable(name: str) -> str:
    """manifest 의 `skipped[]` 에 적을 이름 — 길이만 자른다(JSON 이 제어문자를 이스케이프한다)."""
    return name.encode("utf-8", "backslashreplace").decode("utf-8")[:300]


def _write(src: IO[bytes], target: Path, limit: int | None = None) -> tuple[int, str]:
    """`src` 를 새 파일 `target` 에 쓴다 — 덮어쓰지 않고 링크를 안 따라간다. (바이트, sha256)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o644)
    except FileExistsError:
        raise ShelveError("name_collision") from None
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if limit is not None and size > limit:
                raise _SkipExpand("too_big")
            digest.update(chunk)
            out.write(chunk)
    return size, digest.hexdigest()


def _entry(path: str, size: int, sha: str, **extra: Any) -> dict[str, Any]:
    ctype, _inline = shelf_rules.inline_disposition(path)
    return {"path": path, "size": size, "sha256": sha, "type": ctype, **extra}


def _unpack_bundle(
    bundle: Path, files: Path, names: _Names, *, max_members: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """바깥 묶음을 푼다. (파일 줄들, 건너뛴 멤버들).

    건너뛸 멤버와 디렉터리까지 **모든 멤버**를 `max_members` 에 센다 — 수집기가 만든 묶음은
    일반 파일만 담고 그 수가 이미 이 상한 안이다. 넘으면 악의적인 묶음이라 항목을 접는다(D1).
    """
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen = 0
    with bundle.open("rb") as fh, tarfile.open(fileobj=fh, mode="r|*") as tf:
        for m in tf:
            name = shelf_rules.normalize_member(m.name)
            if not name:
                continue
            seen += 1
            if seen > max_members:
                raise ShelveError("too_many_files")
            if m.isdir():
                continue  # 디렉터리는 파일을 쓸 때 만든다 — 빈 디렉터리는 남기지 않는다
            reason = _unsafe(m, name)
            if reason is not None:
                skipped.append({"path": _printable(name), "reason": reason})
                continue
            names.add(name)
            src = tf.extractfile(m)
            if src is None:
                skipped.append({"path": _printable(name), "reason": "not_a_file"})
                continue
            size, sha = _write(src, files / name)
            entries.append(_entry(name, size, sha))
    return entries, skipped


def _expand_one(
    tar_path: Path,
    tar_rel: str,
    work: Path,
    *,
    bytes_left: int,
    files_left: int,
) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]], int]:
    """tar 하나를 `work` 아래 임시 폴더에 펼친다. (임시 폴더, 파일 줄들, 건너뛴 멤버들, 바이트).

    안의 tar 는 파일로 둔다(한 단계만). 같은 tar 안의 이름 충돌은 항목 전체의 실패다.
    """
    tmp = work / "x" / secrets.token_hex(4)
    tmp.mkdir(parents=True)
    local = _Names()
    base = shelf_rules.expand_dir(tar_rel)
    got: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    spent = 0
    dirs = 0
    with tar_path.open("rb") as fh, tarfile.open(fileobj=fh, mode="r|*") as tf:
        for m in tf:
            name = shelf_rules.normalize_member(m.name)
            if not name:
                continue
            if m.isdir():
                dirs += 1  # `tar -C dir .` 의 디렉터리 멤버 — 파일 수만큼은 봐 준다
                if dirs > max(files_left, 1):
                    raise _SkipExpand("too_many_files")
                continue
            # 건너뛸 멤버도 센다 — 링크 200만 개짜리 tar 가 manifest 를 부풀리지 못하게(D1)
            if len(got) + len(skipped) + 1 > files_left:
                raise _SkipExpand("too_many_files")
            full = f"{base}/{name}"
            reason = _unsafe(m, name) or (None if shelf_rules.safe_member(full) else "unsafe_name")
            if reason is not None:
                skipped.append(
                    {"path": _printable(full), "reason": reason, "expanded_from": tar_rel}
                )
                continue
            if spent + int(m.size) > bytes_left:
                raise _SkipExpand("too_big")  # 헤더가 말하는 크기로 미리 — 쓰기 전에 거절한다
            local.add(name)
            src = tf.extractfile(m)
            if src is None:
                continue
            size, sha = _write(src, tmp / name, limit=bytes_left - spent)
            spent += size
            got.append(_entry(full, size, sha, expanded_from=tar_rel) | {"_tmp": name})
    return tmp, got, skipped, spent


def shelve_bundle(
    job_id: int,
    bundle: Path,
    root: Path,
    *,
    expand_bytes: int,
    expand_files: int,
    verdict: Callable[[int], str | None],
) -> tuple[int, int]:
    """묶음 하나를 보관함 항목으로 만든다. (파일 수, 실측 바이트). 실패는 `ShelveError`."""
    staging = root / shelf_rules.STAGING_NAME
    staging.mkdir(parents=True, exist_ok=True)
    work = staging / f"{int(job_id)}.{secrets.token_hex(4)}"
    files = work / "files"
    try:
        files.mkdir(parents=True)
        if not bundle.is_file():
            raise ShelveError("bundle_gone")
        names = _Names()
        try:
            entries, skipped = _unpack_bundle(bundle, files, names, max_members=expand_files)
        except (tarfile.TarError, EOFError, zlib.error) as e:
            raise ShelveError("copy_error") from e
        if not entries:
            raise ShelveError("no_safe_files")
        expanded: dict[str, list[dict[str, Any]]] = {}
        expand_skipped: list[dict[str, Any]] = []
        used_bytes = used_files = 0
        for tar_rel in shelf_rules.expand_candidates([e["path"] for e in entries]):
            try:
                tmp, got, bad, spent = _expand_one(
                    files / tar_rel,
                    tar_rel,
                    work,
                    bytes_left=max(0, expand_bytes - used_bytes),
                    files_left=max(0, expand_files - used_files),
                )
            except _SkipExpand as s:
                expand_skipped.append({"path": tar_rel, "reason": s.code})
                continue
            except ShelveError:
                raise
            except _BROKEN_TAR as e:
                if isinstance(e, OSError) and e.errno == errno.ENOSPC:
                    raise
                expand_skipped.append({"path": tar_rel, "reason": "unreadable"})
                continue
            skipped.extend(bad)
            for g in got:
                names.add(g["path"])  # 묶음과 펼침을 합친 집합 — 겹치면 항목 전체의 실패
            for g in got:
                dest = files / g["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.rename(tmp / g.pop("_tmp"), dest)
            expanded[tar_rel] = sorted(got, key=lambda e: e["path"])
            used_bytes += spent
            used_files += len(got)
        shutil.rmtree(work / "x", ignore_errors=True)
        rows: list[dict[str, Any]] = []
        for e in sorted(entries, key=lambda e: e["path"]):
            rows.append(e)
            rows.extend(expanded.get(e["path"], ()))
        doc = {
            "job": int(job_id),
            "files": rows,
            "expand_skipped": expand_skipped[:SKIPPED_LINES_MAX],
            "expand_skipped_count": len(expand_skipped),
            "skipped": skipped[:SKIPPED_LINES_MAX],
            "skipped_count": len(skipped),
        }
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (work / "manifest.json").write_bytes(raw)
        actual = sum(int(r["size"]) for r in rows) + len(raw)
        code = verdict(actual)
        if code is not None:
            raise ShelveError(code)
        dest = root / str(int(job_id))
        if os.path.lexists(dest):
            raise ShelveError("dest_exists")  # DB 를 백업으로 되돌린 경우 — 남의 것을 덮지 않는다
        os.replace(work, dest)
        return len(rows), actual
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)


class ShelfCopier:
    """보관 스레드 하나. `wake()` 로 깨우고, 깨우지 않아도 `poll_seconds` 마다 돈다."""

    def __init__(
        self,
        store: Any,
        config: Any,
        *,
        now_fn: Callable[[], datetime],
        log: Callable[[str], None],
        on_change: Callable[[int, str], None],
        on_purged: Callable[[int], None],
        poll_seconds: float = SHELF_POLL_SECONDS,
    ):
        self.store = store
        self.config = config
        self.now_fn = now_fn
        self.log = log
        self.on_change = on_change
        self.on_purged = on_purged
        self.poll_seconds = poll_seconds
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dead: str | None = None
        self._streak: tuple[str, int] = ("", 0)

    # ── 수명 ──

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="rcm-shelf-copier", daemon=True)
        self._thread.start()
        self.wake()  # 기동 직후 — DB 에 남은 pending 을 줍는다

    def stop(self) -> None:
        self._stop.set()
        self._event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def wake(self) -> None:
        self._event.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.dead is None

    @property
    def error_code(self) -> str | None:
        if self.dead is not None or (self._thread is not None and not self._thread.is_alive()):
            return None if self._stop.is_set() else "copier_dead"
        return "copier_stuck" if self._streak[1] >= STUCK_AFTER else None

    # ── 루프 ──

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._event.wait(self.poll_seconds)
                self._event.clear()
                if self._stop.is_set():
                    break
                try:
                    self.drain()
                except sqlite3.OperationalError as e:
                    # DB 잠김은 실패가 아니다 — 다음 깨움에 다시 한다(§15.5)
                    self.log(f"shelf: copier: {type(e).__name__} — retrying on the next wake")
                    continue
                except Exception as e:  # noqa: BLE001 — 루프는 죽지 않는다
                    kind = type(e).__name__
                    count = self._streak[1] + 1 if self._streak[0] == kind else 1
                    self._streak = (kind, count)
                    self.log(f"shelf: copier: {kind} ({count} in a row)")
                    continue
                self._streak = ("", 0)
        except BaseException as e:  # noqa: BLE001 — 죽음은 숨기지 않는다: status 가 copier_dead
            self.dead = type(e).__name__
            self.log(f"shelf: copier died: {self.dead}")

    def drain(self) -> None:
        """pending 이 없어질 때까지 하나씩."""
        while not self._stop.is_set():
            if not self.store.shelf_available:
                return
            item = self.store.claim_shelf_item(self.now_fn())
            if item is None:
                return
            self.copy(item.job_id)

    def _verdict(self, actual: int) -> str | None:
        s = self.config.server
        stored = int(self.store.shelf_totals()["stored_bytes"])
        try:
            free: int | None = int(shutil.disk_usage(self.config.shelf_dir).free)
        except OSError:
            free = None
        limit = s.shelf_max_bytes if self.store.shelf_enabled else 0
        return shelf_rules.capacity_verdict(stored, actual, limit, free, s.min_free_bytes)

    def copy(self, job_id: int) -> None:
        """집은 항목 하나를 끝까지(성공 · 실패). 미뤄 둔 묶음 삭제도 여기서 푼다."""
        s = self.config.server
        count = total = None
        reason: str | None = None
        row = self.store.get_bundle(job_id)
        try:
            if row is None or row["state"] != art.READY or row["purged_at"] is not None:
                raise ShelveError("bundle_gone")
            bundle = self.config.data_dir / "artifacts" / str(int(job_id)) / "bundle.tar"
            count, total = shelve_bundle(
                job_id,
                bundle,
                self.config.shelf_dir,
                expand_bytes=s.max_artifact_bytes,
                expand_files=s.max_artifact_files,
                verdict=self._verdict,
            )
        except ShelveError as e:
            reason = e.code
        except Exception as e:  # noqa: BLE001 — 항목 하나의 예외는 그 항목의 실패다
            reason = "copy_error"
            self.log(f"shelf: #{job_id}: {type(e).__name__}: {str(e)[:120]}")
        now = self.now_fn()
        self.store.finish_shelf_item(
            job_id,
            ok=reason is None,
            file_count=count,
            total_bytes=total,
            reason_code=reason,
            now=now,
        )
        if reason is None:
            self.log(f"shelf: #{job_id} kept ({count} files)")
        else:
            self.log(f"shelf: #{job_id} not kept ({reason})")
        released = self.store.release_deferred_bundle(job_id, now)
        if released is not None:
            shutil.rmtree(released, ignore_errors=True)
            self.on_purged(job_id)
        item = self.store.shelf_item(job_id)
        self.on_change(job_id, item.state if item is not None else "deleted")


__all__ = ["SHELF_POLL_SECONDS", "STUCK_AFTER", "ShelfCopier", "ShelveError", "shelve_bundle"]
