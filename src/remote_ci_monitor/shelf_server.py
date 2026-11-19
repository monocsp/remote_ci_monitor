"""보관함(M5p) 서버 — `App` 과 `Handler` 에 섞는 mixin 둘.

명세는 docs/m5p-workplan.md §4.1 · §4.3 · §4.5~§4.8 · §15(앞 절보다 이긴다). `server.py` 가 이미
6천 줄이라 보관함의 경로 · 회계 · 보안 규칙을 여기 모았다(원격 워커의 `RemoteWorkersMixin` 과 같은
방식). 규칙 자체는 순수 계층(`core/shelf.py`)에 있고 여기서는 HTTP 와 저장을 잇는다.

경로(§4.6):

| 경로 | 권한 | 응답 |
|---|---|---|
| `GET /shelf/` | 읽기 규칙 | HTML 목록 |
| `GET /shelf/latest/<preset>?branch=` | 읽기 규칙 | 303 → `/shelf/N/` |
| `GET /shelf/N/` | 읽기 규칙 | HTML 항목 |
| `GET /shelf/N/f/<path>` | 읽기 규칙 | 파일 바이트(sandbox CSP) |
| `GET /shelf/N.zip` | 읽기 규칙 | 무압축 zip 스트림 |
| `POST /shelf/N/restore` | 읽기 규칙 + 같은 출처 | 303 → `/shelf/N/` |
| `DELETE /shelf/N` | admin Bearer | `{job_id, deleted: true}` |
| `GET /api/shelf` · `GET /api/shelf/N` | 읽기 규칙 | JSON |
| `GET /api/shelf/latest/<preset>?branch=` | 읽기 규칙 | `/api/shelf/N` 과 같은 JSON(§16 추가) |

보안 순서는 Host 허용 목록(421, 유효한 Bearer 는 건너뜀) → 읽기 인증(401) → 보관함 폴더(503) → 경로.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import threading
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from remote_ci_monitor import shelf_pages as pages
from remote_ci_monitor.config import ANY_BINDS
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core import shelf as shelf_rules
from remote_ci_monitor.core.model import SUCCEEDED, Job, ShelfItem
from remote_ci_monitor.core.status import iso, shelf_json, shelf_stage_state
from remote_ci_monitor.janitor import DELETING, remove_under_root
from remote_ci_monitor.store import StoreError, TokenInfo

#: 보관함 전용 전송 슬롯(결정 148) — M5e 의 `transfer_slots` 는 **쓰지 않는다**. 원격 워커의 묶음
#: 업로드가 그 슬롯을 비대기로 잡고 503 을 다시 시도하지 않아서, 보관함 내려받기가 그 슬롯을
#: 차지하면 keep 잡의 묶음이 원격에서 통째로 사라진다.
SHELF_TRANSFER_SLOTS = 4
#: 이보다 큰 단일 파일과 zip 은 보관함 슬롯을 잡는다. 작은 파일과 그림은 일반 요청 슬롯만 쓴다.
SHELF_BIG_FILE_BYTES = 8 * 1024 * 1024
SHELF_CHUNK = 64 * 1024
#: `/api/shelf?limit=` 의 범위.
API_LIMIT_DEFAULT = 200
API_LIMIT_MAX = 1000
#: 메모리에 들고 있는 manifest 수 — 넘으면 비운다(펼친 tar 로 수천 줄일 수 있다).
MANIFEST_CACHE_MAX = 256
PAGE_CSP = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
    "form-action 'self'; frame-ancestors 'none'"
)
#: 잡이 만든 파일은 웹 UI 와 **같은 출처**에서 나간다 — `sandbox` 로 불투명 출처가 되어 웹 UI 의
#: 토큰(`localStorage["rcm.token"]`)을 못 읽는다(결정 139). `allow-same-origin` 을 넣지 않는다.
FILE_CSP = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"

_N = r"(\d{1,18})"  # 그보다 긴 번호는 SQLite 정수를 넘는다 — 500 이 아니라 404
_API_ITEM_RE = re.compile(rf"^/api/shelf/{_N}$")
#: `/shelf/latest/…` 의 JSON 판(§16 추가 요구 — 스크립트가 HTML 을 긁지 않고 받는다)
_API_LATEST_RE = re.compile(r"^/api/shelf/latest/([^/]+)$")
_ITEM_RE = re.compile(rf"^/shelf/{_N}$")
_ZIP_RE = re.compile(rf"^/shelf/{_N}\.zip$")
_FILE_RE = re.compile(rf"^/shelf/{_N}/f/(.+)$")
_RESTORE_RE = re.compile(rf"^/shelf/{_N}/restore$")
_LATEST_RE = re.compile(r"^/shelf/latest/([^/]+)$")
_LIST_STATES = (*shelf_rules.SHELF_STATES, "all")


def is_shelf_path(path: str) -> bool:
    return (
        path == "/shelf"
        or path.startswith("/shelf/")
        or path == "/api/shelf"
        or path.startswith("/api/shelf/")
    )


class ShelfAppMixin:
    """`App` 의 보관함 쪽. `_shelf_init` 은 `App.__init__` 이, 화해와 copier 는 `App.start` 가."""

    # App 이 가진 것들(타입만)
    config: Any
    store: Any
    now_fn: Any
    retention: Any

    def _shelf_init(self) -> None:
        self.shelf_slots = threading.BoundedSemaphore(SHELF_TRANSFER_SLOTS)
        self._shelf_lock = threading.Lock()
        self._shelf_holds: dict[int, int] = {}
        self._shelf_deleting: set[int] = set()
        self._shelf_manifests: dict[int, tuple[int, dict[str, Any], dict[str, Any]]] = {}
        self.copier: Any = None
        self.shelf_startup: Any = None
        # 설정을 모르는 곳(`rcm gc` 사본 · store 시험)의 기본은 False 다 — 서버만 켠다(§15.1)
        self.store.shelf_enabled = self.config.shelf_enabled

    # ── 사건 ──

    def _on_shelf_change(self, job_id: int, state: str | None = None) -> None:
        """보관 상태가 바뀌었다 — SPA 가 링크를 바로 그리게 SSE 를 낸다(§15.5)."""
        if state is None:
            try:
                item = self.store.shelf_item(job_id)
            except Exception:  # noqa: BLE001
                item = None
            state = item.state if item is not None else None
        self._mark_dirty()  # type: ignore[attr-defined]
        self.publish("shelf_changed", {"job_id": int(job_id), "state": state})  # type: ignore[attr-defined]

    def _shelf_purged(self, job_id: int) -> None:
        """미뤄 둔 ack 삭제가 풀렸다 — 묶음의 `artifacts_changed`(purged) 한 번."""
        self.publish_artifacts(job_id, art.PURGED)  # type: ignore[attr-defined]

    def wake_copier(self) -> None:
        if self.copier is not None:
            self.copier.wake()

    # ── hold · 캐시 ──

    def shelf_hold(self, job_id: int) -> bool:
        """지금 zip 으로 내려보내는 중인가 — 청소기 · 손 삭제가 그 항목을 건너뛴다(§4.5)."""
        with self._shelf_lock:
            return self._shelf_holds.get(int(job_id), 0) > 0

    def _shelf_hold_begin(self, job_id: int) -> bool:
        with self._shelf_lock:
            if job_id in self._shelf_deleting:
                return False
            self._shelf_holds[job_id] = self._shelf_holds.get(job_id, 0) + 1
            return True

    def _shelf_hold_end(self, job_id: int) -> None:
        with self._shelf_lock:
            left = self._shelf_holds.get(job_id, 0) - 1
            if left > 0:
                self._shelf_holds[job_id] = left
            else:
                self._shelf_holds.pop(job_id, None)

    def shelf_manifest(self, job_id: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """(manifest, 경로 → 줄). (job, mtime) 으로 메모리에 캐시한다(§4.6)."""
        path = self.config.shelf_dir / str(int(job_id)) / "manifest.json"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        with self._shelf_lock:
            hit = self._shelf_manifests.get(job_id)
            if hit is not None and hit[0] == mtime:
                return hit[1], hit[2]
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
            return None
        index = {
            f["path"]: f
            for f in doc["files"]
            if isinstance(f, dict) and isinstance(f.get("path"), str)
        }
        with self._shelf_lock:
            if len(self._shelf_manifests) >= MANIFEST_CACHE_MAX:
                self._shelf_manifests.clear()
            self._shelf_manifests[job_id] = (mtime, doc, index)
        return doc, index

    # ── 상태 · 회계 ──

    def shelf_tz(self) -> Any:
        name = self.config.display.timezone
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — 검증된 설정이지만 tzdata 가 없을 수 있다
            return None

    def shelf_view(self, item: ShelfItem) -> str:
        """보관 중 · 정리 대기 항목이 지금 어느 쪽인가 — 열이 아니라 `stage()` 로(§15.5)."""
        s = self.config.server
        return shelf_stage_state(
            item.state,
            item.last_used_at,
            item.requested_at,
            now=self.now_fn().timestamp(),
            stale_days=s.shelf_stale_days,
            delete_days=s.shelf_delete_days,
        )

    def _shelf_stage(self, item: ShelfItem) -> str:
        s = self.config.server
        used = item.last_used_at if item.last_used_at is not None else item.requested_at
        return shelf_rules.stage(
            used, self.now_fn().timestamp(), s.shelf_stale_days, s.shelf_delete_days
        )

    def shelf_touch(self, job_id: int, route: str, method: str, item: ShelfItem) -> None:
        """이 요청이 항목을 「썼」으면 시계를 돌린다(§4.3). 판정은 HEAD 를 바꾸기 전의 메서드로."""
        if item.state not in shelf_rules.SHELVED_STATES:
            return
        if not shelf_rules.touches(method, route, self._shelf_stage(item)):
            return
        if self.store.touch_shelf_item(job_id, self.now_fn()):
            self._on_shelf_change(job_id)

    def shelf_url(self, job_id: int, base: str) -> str:
        return f"{base}/shelf/{int(job_id)}/"

    def _shelf_none_reason(self, job: Job, artifact_state: str) -> str | None:
        """묶음 행이 없는 keep 잡의 이유를 정책으로 계산한다(§15.1)."""
        if artifact_state != art.UNKNOWN:
            return None
        preset = self.config.preset(job.preset)
        if (
            preset is not None
            and preset.artifacts_on == art.COLLECT_FAILURE
            and job.state == SUCCEEDED
        ):
            return "not_collected_on_success"
        return None

    def shelf_public(self, job: Job, base: str) -> dict[str, Any] | None:
        """잡 행의 `shelf` 객체(§4.8). keep 이 아니면 None."""
        if not job.keep:
            return None
        try:
            item = self.store.shelf_item(job.id)
            row = self.store.get_bundle(job.id)
        except Exception:  # noqa: BLE001 — 보관함 조회 실패가 상태 전체를 막으면 안 된다
            item, row = None, None
        state = self.artifact_state(job, row)  # type: ignore[attr-defined]
        s = self.config.server
        return shelf_json(
            keep=True,
            item=item,
            artifact_state=state,
            url=self.shelf_url(job.id, base),
            now=self.now_fn().timestamp(),
            stale_days=s.shelf_stale_days,
            delete_days=s.shelf_delete_days,
            none_reason=self._shelf_none_reason(job, state),
        )

    def shelf_error_code(self) -> str | None:
        startup = self.shelf_startup
        if startup is not None and not startup.available:
            return startup.error_code
        if not self.store.shelf_available:
            return "shelf_dir_missing"
        retention = self.retention
        if retention is not None and getattr(retention, "shelf_error_code", None):
            return retention.shelf_error_code
        if self.copier is not None:
            return self.copier.error_code
        return None

    def shelf_storage(self) -> dict[str, Any]:
        """`server.shelf_storage` · `health.shelf`(§4.8). **경로는 싣지 않는다**(홈 경로가 보인다).

        보관 중 · 정리 대기의 수는 DB 열이 아니라 `stage()` 로 다시 센다(§15.5). 이 키가 있다는 것이
        「서버가 keep 을 안다」의 신호다(옛 서버 감지, §4.1).
        """
        s = self.config.server
        items = {"active": 0, "stale": 0, "pending": 0, "failed": 0}
        stored: int | None = None
        error = self.shelf_error_code()
        try:
            totals = self.store.shelf_totals()
            stored = int(totals["stored_bytes"])
            items["pending"] = int(totals["items"]["pending"])
            items["failed"] = int(totals["items"]["failed"])
            for item in self.store.shelf_items(state=shelf_rules.SHELVED_STATES, limit=None):
                items[self.shelf_view(item)] += 1
        except Exception as e:  # noqa: BLE001 — 회계 실패가 상태 문서를 막으면 안 된다
            error = error or type(e).__name__
        retention = self.retention
        last = getattr(retention, "shelf_last_sweep_at", None) if retention is not None else None
        return {
            "enabled": bool(self.config.shelf_enabled),
            "stored_bytes": stored,
            "limit_bytes": int(s.shelf_max_bytes),
            "items": items,
            "last_sweep_at": iso(last),
            "error_code": error,
        }

    # ── Host 허용 목록 (결정 148 · §15.5) ──

    def shelf_host_ok(self, host: str | None) -> bool:
        s = self.config.server
        public = urlsplit(s.public_url).hostname if s.public_url else None
        also = [urlsplit(self.base_url()).hostname or ""]  # type: ignore[attr-defined]
        bind = (s.bind or "").strip()
        if bind and bind not in ANY_BINDS:
            also.append(bind)
        return shelf_rules.host_allowed(
            host or "", public_host=public, hostname=socket.gethostname(), also=also
        )

    # ── JSON ──

    def shelf_row(self, item: ShelfItem) -> dict[str, Any]:
        """페이지에 넘기는 행 — 열 그대로 + 정리 예정일(epoch 초)."""
        doc = asdict(item)
        if item.state in shelf_rules.SHELVED_STATES and item.last_used_at is not None:
            s = self.config.server
            stale_at, delete_at = shelf_rules.due_dates(
                item.last_used_at, s.shelf_stale_days, s.shelf_delete_days
            )
            doc["stale_at"], doc["delete_at"] = stale_at, delete_at
        else:
            doc["stale_at"] = doc["delete_at"] = None
        return doc

    def shelf_item_json(self, item: ShelfItem, base: str) -> dict[str, Any]:
        row = self.shelf_row(item)
        try:
            inputs = json.loads(item.inputs_json) if item.inputs_json else {}
        except ValueError:
            inputs = {}

        def t(v: float | None) -> str | None:
            return iso(datetime.fromtimestamp(v, UTC)) if v is not None else None

        return {
            "job_id": item.job_id,
            "state": self.shelf_view(item),
            "preset": item.preset,
            "inputs": inputs if isinstance(inputs, dict) else {},
            "repo": item.repo,
            "branch": item.branch,
            "sha": item.sha,
            "source_mode": item.source_mode,
            "requester": item.requester,
            "job_state": item.job_state,
            "job_finished_at": t(item.job_finished_at),
            "requested_at": t(item.requested_at),
            "shelved_at": t(item.shelved_at),
            "last_used_at": t(item.last_used_at),
            "stale_since": t(item.stale_since),
            "stale_at": t(row["stale_at"]),
            "delete_at": t(row["delete_at"]),
            "deleted_at": t(item.deleted_at),
            "failed_at": t(item.failed_at),
            "file_count": item.file_count,
            "total_bytes": item.total_bytes,
            "reason_code": item.reason_code,
            "reason_args": item.reason_args,
            "url": self.shelf_url(item.job_id, base),
        }

    # ── `POST /jobs/N/keep` (§4.1 · §15.1) ──

    KEEP_STATUS = {
        "armed": 200,
        "requested": 202,
        "already_requested": 200,
        "retouched": 200,
        "retried": 202,
        "no_bundle": 409,
        "gone": 410,
        "disabled": 409,
    }
    KEEP_ERRORS = {
        "no_bundle": "nothing to keep — this job has no bundle",
        "gone": "the bundle is already gone — keep it with --keep next time",
        "disabled": "the shelf is off on this server",
    }

    def keep_job(self, job_id: int, token: TokenInfo, host: str | None) -> tuple[int, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            from remote_ci_monitor.server import ApiError

            raise ApiError(404, "no such job")
        if not self.can_read_log(job, token):  # type: ignore[attr-defined]
            from remote_ci_monitor.server import ApiError

            raise ApiError(403, "not your job")
        preset = self.config.preset(job.preset)
        reason: str | None = None
        if (
            (preset is None or not preset.artifacts)
            and not job.is_terminal
            and self.store.shelf_item(job.id) is None
        ):
            result, reason = "no_bundle", "no_artifacts"  # 도는 잡은 store 가 모른다(설정 밖)
        else:
            try:
                result = self.store.set_job_keep(job.id, self.now_fn())
            except StoreError:
                from remote_ci_monitor.server import ApiError

                raise ApiError(404, "no such job") from None
        status = self.KEEP_STATUS[result]
        if result == "disabled" and not self.store.shelf_available:
            status = 503
        if result in ("requested", "retried"):
            self.wake_copier()
        if result in ("requested", "retried", "retouched", "armed"):
            self._on_shelf_change(job.id)
        fresh = self.store.get_job(job.id) or job
        base = self.base_url(host)  # type: ignore[attr-defined]
        body: dict[str, Any] = {
            "job": job.id,
            "result": result,
            "shelf": self.shelf_public(fresh, base),
        }
        if result == "no_bundle":
            if reason is None:
                row = self.store.get_bundle(job.id)
                state = self.artifact_state(fresh, row)  # type: ignore[attr-defined]
                reason = self._shelf_none_reason(fresh, state) or (
                    "no_artifacts" if state == art.DISABLED else state
                )
            body["reason_code"] = reason
        if result in self.KEEP_ERRORS:
            body["error"] = self.KEEP_ERRORS[result]
        return status, body

    # ── `DELETE /shelf/N` (결정 146) ──

    def shelf_delete(self, job_id: int) -> dict[str, Any]:
        from remote_ci_monitor.server import ApiError

        item = self.store.shelf_item(job_id)
        if item is None:
            raise ApiError(404, "not on the shelf")
        if item.state == shelf_rules.STATE_DELETED:
            raise ApiError(
                410, "already deleted", state="deleted", deleted_at=_iso(item.deleted_at)
            )
        if item.state in shelf_rules.IN_FLIGHT_STATES:
            raise ApiError(409, "still being copied to the shelf", reason_code="shelf_item_copying")
        with self._shelf_lock:
            if self._shelf_holds.get(job_id, 0) > 0:
                raise ApiError(
                    409, "being downloaded as a zip right now", reason_code="shelf_item_busy"
                )
            self._shelf_deleting.add(job_id)
        now = self.now_fn()
        try:
            if item.state == shelf_rules.STATE_FAILED:
                done = self.store.mark_shelf(
                    job_id,
                    shelf_rules.STATE_DELETED,
                    now,
                    only_from=(shelf_rules.STATE_FAILED,),
                    total_bytes=0,
                )
            else:
                live = shelf_rules.SHELVED_STATES
                if not self.store.mark_shelf(
                    job_id, item.state, now, only_from=live, error_code=DELETING
                ):
                    raise ApiError(
                        409, "the item changed — try again", reason_code="shelf_item_busy"
                    )
                root = self.config.shelf_dir.resolve()
                try:
                    remove_under_root(self.config.shelf_dir / str(job_id), root)
                except OSError as e:
                    self.store.mark_shelf(
                        job_id,
                        item.state,
                        now,
                        only_from=live,
                        error_code=f"delete_{type(e).__name__}",
                    )
                    raise ApiError(503, "could not delete the folder — see the server log") from e
                done = self.store.mark_shelf(
                    job_id,
                    shelf_rules.STATE_DELETED,
                    now,
                    only_from=live,
                    deleted_at=now.timestamp(),
                    total_bytes=0,
                    error_code=None,
                )
        finally:
            with self._shelf_lock:
                self._shelf_deleting.discard(job_id)
                self._shelf_manifests.pop(job_id, None)
        if not done:
            raise ApiError(409, "the item changed — try again", reason_code="shelf_item_busy")
        self.log(f"shelf: #{job_id} deleted by hand")  # type: ignore[attr-defined]
        self._on_shelf_change(job_id, shelf_rules.STATE_DELETED)
        return {"job_id": job_id, "deleted": True}


def _iso(ts: float | None) -> str | None:
    return iso(datetime.fromtimestamp(ts, UTC)) if ts is not None else None


class _ZipSink:
    """`zipfile` 이 쓰는 스트림 — 소켓에 그대로. `tell`·`seek` 이 없어 zipfile 이 data descriptor 를
    쓴다(길이를 미리 모르는 스트리밍)."""

    def __init__(self, out: Any) -> None:
        self.out = out

    def write(self, data: bytes) -> int:
        self.out.write(data)
        return len(data)

    def flush(self) -> None:
        pass


class ShelfHandlerMixin:
    """`Handler` 의 보관함 경로."""

    app: Any
    headers: Any
    command: str
    connection: Any
    wfile: Any
    close_connection: bool

    # ── 응답 ──

    def _shelf_html(self, status: int, text: str, *, extra: dict[str, str] | None = None) -> None:
        body = text.encode("utf-8")
        self.send_response(status)  # type: ignore[attr-defined]
        for k, v in {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "Content-Security-Policy": PAGE_CSP,
            "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            **(extra or {}),
        }.items():
            self.send_header(k, v)  # type: ignore[attr-defined]
        self.end_headers()  # type: ignore[attr-defined]
        if self.command != "HEAD":
            self.wfile.write(body)

    def _shelf_redirect(self, location: str) -> None:
        self.send_response(303)  # type: ignore[attr-defined]
        self.send_header("Location", location)  # type: ignore[attr-defined]
        self.send_header("Content-Length", "0")  # type: ignore[attr-defined]
        self.send_header("Cache-Control", "no-store")  # type: ignore[attr-defined]
        self.send_header("Referrer-Policy", "same-origin")  # type: ignore[attr-defined]
        self.end_headers()  # type: ignore[attr-defined]

    # ── 라우터 ──

    def _shelf_route(self, path: str, query: dict[str, list[str]]) -> None:  # noqa: C901
        from remote_ci_monitor.server import ApiError

        app = self.app
        method = "GET" if self.command == "HEAD" else self.command
        host = self.headers.get("Host")
        # 유효한 Bearer 는 Host 검사를 건너뛴다 — 재바인딩 공격자는 토큰이 없다(§15.5).
        # 순서는 421 → 401: 재바인딩 페이지에 로그인 창을 띄우지 않는다(E25).
        if self._token() is None and not app.shelf_host_ok(host):  # type: ignore[attr-defined]
            raise ApiError(421, "misdirected request — this host name does not serve the shelf")
        self._read_only_ok()  # type: ignore[attr-defined]
        lang = pages.lang_of(query)
        api = path.startswith("/api/")
        if not app.store.shelf_available:
            code = app.shelf_error_code() or "shelf_dir_missing"
            if api or method not in ("GET",):
                raise ApiError(503, "the shelf folder is unavailable", reason_code=code)
            self._shelf_html(503, pages.message_page(lang=lang, key="unavailable"))
            return
        if path == "/api/shelf":
            self._only_shelf(method, "GET")
            self._send_json(200, self._shelf_api_list(query))  # type: ignore[attr-defined]
            return
        m = _API_ITEM_RE.match(path)
        if m:
            self._only_shelf(method, "GET")
            self._send_json(200, self._shelf_api_item(int(m.group(1))))  # type: ignore[attr-defined]
            return
        m = _API_LATEST_RE.match(path)
        if m:
            # 시계를 안 돌린다(§4.3 의 latest · api 행). 없으면 404 JSON
            self._only_shelf(method, "GET")
            branch = (query.get("branch") or [None])[0]
            item = app.store.latest_shelf_item(unquote(m.group(1)), branch or None)
            if item is None:
                raise ApiError(404, "nothing on the shelf for that preset and branch")
            self._send_json(200, self._shelf_api_item(item.job_id))  # type: ignore[attr-defined]
            return
        if api:
            raise ApiError(404, "not found")
        if path == "/shelf":
            self._only_shelf(method, "GET")
            self._shelf_list_page(lang, query)
            return
        m = _LATEST_RE.match(path)
        if m:
            self._only_shelf(method, "GET")
            preset = unquote(m.group(1))
            branch = (query.get("branch") or [None])[0]
            item = app.store.latest_shelf_item(preset, branch or None)
            if item is None:
                self._shelf_html(404, pages.message_page(lang=lang, key="not_found"))
                return
            self._shelf_redirect(f"/shelf/{item.job_id}/{pages.lang_query(lang)}")
            return
        m = _ZIP_RE.match(path)
        if m:
            self._only_shelf(method, "GET")
            self._shelf_zip(int(m.group(1)))
            return
        m = _FILE_RE.match(path)
        if m:
            self._only_shelf(method, "GET")
            self._shelf_file(int(m.group(1)), m.group(2))
            return
        m = _RESTORE_RE.match(path)
        if m:
            self._only_shelf(method, "POST")
            self._shelf_restore(int(m.group(1)), lang)
            return
        m = _ITEM_RE.match(path)
        if m:
            job_id = int(m.group(1))
            if method == "DELETE":
                app.require_admin(self._token())  # type: ignore[attr-defined]
                self._send_json(200, app.shelf_delete(job_id))  # type: ignore[attr-defined]
                return
            self._only_shelf(method, "GET")
            self._shelf_item_page(job_id, lang)
            return
        raise ApiError(404, "not found")

    @staticmethod
    def _only_shelf(method: str, allowed: str) -> None:
        if method != allowed:
            from remote_ci_monitor.server import ApiError

            raise ApiError(405, f"method not allowed; use {allowed}")

    # ── 페이지 ──

    def _shelf_list_page(self, lang: str, query: dict[str, list[str]]) -> None:
        app = self.app
        preset = (query.get("preset") or [None])[0] or None
        shelved = app.store.shelf_items(state=shelf_rules.SHELVED_STATES, limit=None)
        presets = {i.preset for i in shelved}
        if preset is not None:
            shelved = [i for i in shelved if i.preset == preset]
        kept, stale = [], []
        for item in shelved:
            (kept if app.shelf_view(item) == "active" else stale).append(app.shelf_row(item))
        cutoff = (app.now_fn() - timedelta(days=shelf_rules.TOMBSTONE_DAYS)).timestamp()
        removed = [
            app.shelf_row(i)
            for i in app.store.shelf_items(
                state=shelf_rules.STATE_DELETED, preset=preset, limit=None
            )
            if i.deleted_at is not None and i.deleted_at >= cutoff
        ]
        storage = app.shelf_storage()
        self._shelf_html(
            200,
            pages.list_page(
                lang=lang,
                tz=app.shelf_tz(),
                kept=kept,
                stale=stale,
                removed=removed,
                stored_bytes=storage["stored_bytes"],
                limit_bytes=storage["limit_bytes"],
                presets=presets,
                preset=preset,
            ),
        )

    def _shelf_item_page(self, job_id: int, lang: str) -> None:
        app = self.app
        tz = app.shelf_tz()
        item = app.store.shelf_item(job_id)
        if item is None:
            self._shelf_html(404, pages.message_page(lang=lang, key="not_found"))
            return
        if item.state == shelf_rules.STATE_DELETED:
            self._shelf_html(
                410, pages.gone_page(lang=lang, tz=tz, job_id=job_id, deleted_at=item.deleted_at)
            )
            return
        row = app.shelf_row(item)
        job_link = app.store.get_job(job_id) is not None
        if item.state in shelf_rules.IN_FLIGHT_STATES:
            self._shelf_html(200, pages.copying_page(lang=lang, tz=tz, item=row, job_link=job_link))
            return
        if item.state == shelf_rules.STATE_FAILED:
            self._shelf_html(200, pages.failed_page(lang=lang, tz=tz, item=row, job_link=job_link))
            return
        view = app.shelf_view(item)
        got = app.shelf_manifest(job_id)
        doc, _index = got if got is not None else ({"files": []}, {})
        app.shelf_touch(job_id, "item", self.command, item)
        self._shelf_html(
            200,
            pages.item_page(
                lang=lang,
                tz=tz,
                item=row,
                files=[f for f in doc.get("files", []) if isinstance(f, dict)],
                expand_skipped=doc.get("expand_skipped") or [],
                skipped_count=len(doc.get("skipped") or []),
                view=view,
                job_link=job_link,
            ),
        )

    # ── 파일 · zip ──

    def _shelved_or_error(self, job_id: int) -> ShelfItem:
        from remote_ci_monitor.server import ApiError

        item = self.app.store.shelf_item(job_id)
        if item is None:
            raise ApiError(404, "not on the shelf")
        if item.state == shelf_rules.STATE_DELETED:
            raise ApiError(410, f"deleted at {_iso(item.deleted_at)}", state="deleted")
        if item.state in shelf_rules.IN_FLIGHT_STATES:
            raise ApiError(409, "still being copied to the shelf", reason_code=item.state)
        if item.state == shelf_rules.STATE_FAILED:
            raise ApiError(404, "this job was not kept", reason_code="not_kept")
        return item

    def _open_member(self, job_id: int, rel: str) -> tuple[int, os.stat_result]:
        """보관 항목의 파일 하나를 연다 — `files/` 밖이거나 링크면 404."""
        from remote_ci_monitor.server import ApiError

        files = self.app.config.shelf_dir / str(job_id) / "files"
        target = files / rel
        try:
            real = target.resolve()
            base = files.resolve()
        except OSError:
            raise ApiError(404, "no such file") from None
        if base not in real.parents:
            raise ApiError(404, "no such file")
        try:
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise ApiError(404, "no such file") from None
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise ApiError(404, "no such file")
        return fd, st

    def _shelf_file(self, job_id: int, raw: str) -> None:
        from remote_ci_monitor.server import REQUEST_TIMEOUT, ApiError

        app = self.app
        item = self._shelved_or_error(job_id)
        # 퍼센트 디코딩은 **한 번**, 그 **뒤에** 검사한다. 라우터의 `..` 검사는 디코딩 전 문자열을
        # 보므로 `%2e%2e` 를 통과시킨다(E1). `+` 는 공백이 아니다.
        try:
            rel = unquote(raw, errors="strict")
        except UnicodeDecodeError:
            raise ApiError(400, "bad path") from None
        if not shelf_rules.safe_member(rel):
            raise ApiError(400, "bad path")
        got = app.shelf_manifest(job_id)
        entry = got[1].get(rel) if got is not None else None
        if entry is None:
            raise ApiError(404, "no such file")
        fd, st = self._open_member(job_id, rel)
        with os.fdopen(fd, "rb") as fh:
            sha = str(entry.get("sha256") or "")
            etag = f'"{sha[:16]}"'
            if self.headers.get("If-None-Match") == etag:
                app.shelf_touch(job_id, "file", self.command, item)
                self.send_response(304)  # type: ignore[attr-defined]
                self.send_header("ETag", etag)  # type: ignore[attr-defined]
                self.send_header("Content-Length", "0")  # type: ignore[attr-defined]
                self.end_headers()  # type: ignore[attr-defined]
                return
            big = st.st_size > SHELF_BIG_FILE_BYTES
            if big and self.command != "HEAD" and not app.shelf_slots.acquire(blocking=False):
                raise ApiError(
                    503, "too many shelf downloads right now", headers={"Retry-After": "5"}
                )
            try:
                ctype, inline = shelf_rules.inline_disposition(rel)
                app.shelf_touch(job_id, "file", self.command, item)
                self.send_response(200)  # type: ignore[attr-defined]
                for k, v in {
                    "Content-Type": ctype,
                    "Content-Length": str(st.st_size),
                    "ETag": etag,
                    "Content-Disposition": shelf_rules.content_disposition(rel, inline),
                    "Content-Security-Policy": FILE_CSP,
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                    "Cache-Control": "private, max-age=300",
                }.items():
                    self.send_header(k, v)  # type: ignore[attr-defined]
                self.end_headers()  # type: ignore[attr-defined]
                if self.command == "HEAD":
                    return
                self.connection.settimeout(app.config.server.artifact_transfer_timeout_seconds)
                try:
                    while True:
                        chunk = fh.read(SHELF_CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except OSError:
                    self.close_connection = True  # 끊겼거나 느린 독자 — 슬롯을 돌려준다
                finally:
                    self.connection.settimeout(REQUEST_TIMEOUT)
            finally:
                if big and self.command != "HEAD":
                    app.shelf_slots.release()

    def _shelf_zip(self, job_id: int) -> None:
        from remote_ci_monitor.server import REQUEST_TIMEOUT, ApiError

        app = self.app
        item = self._shelved_or_error(job_id)
        got = app.shelf_manifest(job_id)
        if got is None:
            raise ApiError(503, "the item's manifest is unreadable")
        files = [
            f for f in got[0]["files"] if isinstance(f, dict) and isinstance(f.get("path"), str)
        ]
        headers = {
            "Content-Type": "application/zip",
            "Content-Disposition": f'attachment; filename="shelf-{job_id}.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Connection": "close",
        }
        if self.command == "HEAD":  # hold 도 슬롯도 시계도 안 건다
            self.send_response(200)  # type: ignore[attr-defined]
            for k, v in headers.items():
                self.send_header(k, v)  # type: ignore[attr-defined]
            self.end_headers()  # type: ignore[attr-defined]
            self.close_connection = True
            return
        if not app.shelf_slots.acquire(blocking=False):
            raise ApiError(503, "too many shelf downloads right now", headers={"Retry-After": "5"})
        try:
            if not app._shelf_hold_begin(job_id):
                raise ApiError(409, "being deleted", reason_code="shelf_item_busy")
            try:
                app.shelf_touch(job_id, "zip", self.command, item)
                self.send_response(200)  # type: ignore[attr-defined]
                for k, v in headers.items():
                    self.send_header(k, v)  # type: ignore[attr-defined]
                self.end_headers()  # type: ignore[attr-defined]
                self.close_connection = True
                self.connection.settimeout(app.config.server.artifact_transfer_timeout_seconds)
                try:
                    self._write_zip(job_id, files, item)
                except (OSError, ApiError):
                    self.close_connection = True  # 끊겼거나 느린 독자 — hold 와 슬롯을 돌려준다
                finally:
                    self.connection.settimeout(REQUEST_TIMEOUT)
            finally:
                app._shelf_hold_end(job_id)
        finally:
            app.shelf_slots.release()

    def _write_zip(self, job_id: int, files: list[dict[str, Any]], item: ShelfItem) -> None:
        stamp = datetime.fromtimestamp(item.shelved_at or item.requested_at, UTC)
        when = max(stamp, datetime(1980, 1, 1, tzinfo=UTC)).timetuple()[:6]
        with zipfile.ZipFile(_ZipSink(self.wfile), "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for f in files:
                rel = f["path"]
                if not shelf_rules.safe_member(rel):
                    continue
                fd, st = self._open_member(job_id, rel)
                with os.fdopen(fd, "rb") as src:
                    zi = zipfile.ZipInfo(rel, date_time=when)
                    zi.compress_type = zipfile.ZIP_STORED
                    zi.file_size = st.st_size
                    zi.external_attr = 0o644 << 16
                    big = st.st_size > zipfile.ZIP64_LIMIT // 2
                    with zf.open(zi, "w", force_zip64=big) as dst:
                        while True:
                            chunk = src.read(SHELF_CHUNK)
                            if not chunk:
                                break
                            dst.write(chunk)

    # ── 되살리기 (결정 145) ──

    def _shelf_restore(self, job_id: int, lang: str) -> None:
        from remote_ci_monitor.server import ApiError

        app = self.app
        item = app.store.shelf_item(job_id)
        if item is None:
            raise ApiError(404, "not on the shelf")
        if item.state == shelf_rules.STATE_DELETED:
            raise ApiError(410, f"deleted at {_iso(item.deleted_at)}", state="deleted")
        if item.state not in shelf_rules.SHELVED_STATES:
            raise ApiError(409, "nothing to restore", reason_code=item.state)
        schemes = (
            ("http", "https") if app.config.server.public_url.startswith("https") else ("http",)
        )
        if not shelf_rules.same_origin(
            self.headers.get("Host") or "",
            self.headers.get("Origin"),
            self.headers.get("Referer"),
            schemes=schemes,
        ):
            # 쓰기는 원래 Bearer 만 받는다(CSRF). 되살리기는 시계만 미는 일이라 토큰 없이 받되
            # 이 서버가 그린 페이지에서 온 것만 받는다
            raise ApiError(403, "restore must come from this server's own shelf page")
        app.shelf_touch(job_id, "restore", "POST", item)
        self._shelf_redirect(f"/shelf/{job_id}/{pages.lang_query(lang)}")

    # ── JSON ──

    def _shelf_api_list(self, query: dict[str, list[str]]) -> dict[str, Any]:
        from remote_ci_monitor.server import ApiError

        app = self.app
        state = (query.get("state") or [None])[0]
        if state is not None and state not in _LIST_STATES:
            raise ApiError(400, f"state must be one of: {', '.join(_LIST_STATES)}")
        raw_limit = (query.get("limit") or [None])[0]
        limit = API_LIMIT_DEFAULT
        if raw_limit is not None:
            try:
                limit = int(raw_limit)
            except ValueError:
                raise ApiError(400, "limit must be an integer") from None
            if not 1 <= limit <= API_LIMIT_MAX:
                raise ApiError(400, f"limit must be between 1 and {API_LIMIT_MAX}")
        preset = (query.get("preset") or [None])[0] or None
        base = app.base_url(self.headers.get("Host"))
        if state in (None, "active", "stale"):
            rows = app.store.shelf_items(
                state=shelf_rules.SHELVED_STATES, preset=preset, limit=None
            )
            want = ("active", "stale") if state is None else (state,)
            rows = [r for r in rows if app.shelf_view(r) in want]
        elif state == "all":
            rows = [
                r
                for r in app.store.shelf_items(preset=preset, limit=None)
                if r.state != shelf_rules.STATE_DELETED
            ]
        else:
            rows = app.store.shelf_items(state=state, preset=preset, limit=None)
        return {
            "items": [app.shelf_item_json(r, base) for r in rows[:limit]],
            "storage": app.shelf_storage(),
            "display_timezone": app.config.display.timezone or None,
        }

    def _shelf_api_item(self, job_id: int) -> dict[str, Any]:
        from remote_ci_monitor.server import ApiError

        app = self.app
        item = app.store.shelf_item(job_id)
        if item is None:
            raise ApiError(404, "not on the shelf")
        if item.state == shelf_rules.STATE_DELETED:
            raise ApiError(
                410, "deleted from the shelf", state="deleted", deleted_at=_iso(item.deleted_at)
            )
        doc = app.shelf_item_json(item, app.base_url(self.headers.get("Host")))
        files: list[Any] = []
        expand_skipped: list[Any] = []
        skipped: list[Any] = []
        if item.state in shelf_rules.SHELVED_STATES:
            got = app.shelf_manifest(job_id)
            if got is not None:
                # 줄마다 받을 경로(호스트 없이)와 사실 — 스크립트가 그대로 받고 해시로 맞춘다
                files = [
                    {
                        "path": f["path"],
                        "url": pages.file_href(job_id, f["path"]),
                        "size": f.get("size"),
                        "sha256": f.get("sha256"),
                        "type": f.get("type"),
                        "expanded_from": f.get("expanded_from"),
                    }
                    for f in got[0].get("files") or []
                    if isinstance(f, dict) and isinstance(f.get("path"), str)
                ]
                expand_skipped = got[0].get("expand_skipped") or []
                skipped = got[0].get("skipped") or []
        doc["files"] = files
        doc["expand_skipped"] = expand_skipped
        doc["skipped"] = skipped
        return doc


__all__ = [
    "FILE_CSP",
    "PAGE_CSP",
    "SHELF_BIG_FILE_BYTES",
    "SHELF_TRANSFER_SLOTS",
    "ShelfAppMixin",
    "ShelfHandlerMixin",
    "is_shelf_path",
]
