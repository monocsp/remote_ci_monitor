"""알림 스레드 — 잡이 끝나면 `[[notify]]` 규칙대로 argv 를 돌리거나 URL 에 JSON 을 POST 한다.

- 트리거는 이벤트 버스의 `job_finished` + 시작 시 미전송 스캔(재시작 직후 recover 이벤트를
  놓치지 않는다). 실행 전에 `notifications` 행을 unique insert 로 **claim** 하므로 같은 이벤트가
  두 번 와도(recover · finish · 재발행) 한 번만 보낸다 — 이벤트 중복은 정상 입력이다.
- argv 는 셸 없이, 규칙에 적힌 그대로. 사용자 문자열은 `core/notify.sanitize_text` 로 정화된 env.
- url 은 리다이렉트를 따라가지 않는다(3xx 는 실패). 응답 본문은 버린다.
- 실패는 재시도하지 않는다. 서버 로그 한 줄 + `failures` 카운터. `server.last_error` 는 안 건드린다
  (알림 실패로 큐가 아픈 것처럼 보이면 안 된다).
"""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core.model import Job
from remote_ci_monitor.core.notify import NotifyRule, notify_env, rules_for, sanitize_text
from remote_ci_monitor.core.status import recent_json
from remote_ci_monitor.events import KIND_JOB_FINISHED, KIND_LAG, KIND_RESET, EventBus
from remote_ci_monitor.store import Store

POLL_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx 를 따라가지 않는다 — 훅 URL 이 내부 주소로 튀는 것을 막는다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def default_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


class Notifier:
    """알림 실행기. `start()` 는 스캔 뒤 버스를 구독하는 스레드를 띄운다."""

    def __init__(
        self,
        store: Store,
        config: ServerConfig,
        bus: EventBus,
        *,
        now_fn: Callable[[], datetime] = _utcnow,
        log: Callable[[str], None] | None = None,
        run: Callable[..., Any] = subprocess.run,
        opener: urllib.request.OpenerDirector | None = None,
        base_url: str | None = None,
        stop: threading.Event | None = None,
    ):
        self.store = store
        self.config = config
        self.bus = bus
        self.now_fn = now_fn
        self.log = log or (lambda msg: None)
        self.run = run
        self.opener = opener
        srv = config.server
        self.base_url = base_url or (
            srv.public_url.rstrip("/") if srv.public_url else f"http://{srv.bind}:{srv.port}"
        )
        self.stop_event = stop or threading.Event()
        self.failures = 0
        self.delivered = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def rules(self) -> tuple[NotifyRule, ...]:
        return tuple(self.config.notify)

    # ── 실행 ────────────────────────────────────────────────────────────────

    def _run_rule(self, rule: NotifyRule, row: dict[str, Any]) -> bool:
        env = notify_env(row, rule.name)
        tag = f"notify {rule.name} #{row.get('id')}"
        if rule.argv:
            try:
                proc = self.run(
                    list(rule.argv),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=rule.timeout_seconds,
                    start_new_session=True,
                )
            except subprocess.TimeoutExpired:
                self.log(f"{tag}: timed out after {rule.timeout_seconds}s")
                return False
            except OSError as e:
                self.log(f"{tag}: cannot start: {type(e).__name__}")
                return False
            rc = getattr(proc, "returncode", 0)
            if rc != 0:
                self.log(f"{tag}: exit {rc}")
                return False
            return True
        if rule.url:
            body = json.dumps({"notify": rule.name, "job": row}, separators=(",", ":")).encode()
            req = urllib.request.Request(
                rule.url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "rcm-notify"},
                method="POST",
            )
            opener = self.opener or default_opener()
            try:
                with opener.open(req, timeout=rule.timeout_seconds) as resp:
                    status = getattr(resp, "status", 200)
            except urllib.error.HTTPError as e:
                self.log(f"{tag}: HTTP {e.code}")
                return False
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                self.log(f"{tag}: {type(e).__name__}")
                return False
            if not 200 <= int(status) < 300:
                self.log(f"{tag}: HTTP {status}")
                return False
            return True
        return False

    def deliver(self, job: Job) -> int:
        """잡 하나에 해당하는 규칙을 (claim 된 것만) 실행한다. 실행한 규칙 수를 돌려준다."""
        matched = rules_for(job.state, job.preset, self.rules)
        if not matched:
            return 0
        row = recent_json(job, base_url=self.base_url)
        row["summary"] = sanitize_text(row.get("summary"))
        count = 0
        for rule in matched:
            now = self.now_fn()
            if not self.store.claim_notification(job.id, rule.name, now):
                continue  # 이미 보냈거나 보내는 중 — 이벤트 중복
            ok = self._run_rule(rule, row)
            self.store.mark_notification(job.id, rule.name, delivered=ok, now=self.now_fn())
            with self._lock:
                if ok:
                    self.delivered += 1
                else:
                    self.failures += 1
            count += 1
        return count

    def scan(self) -> int:
        """시작 시: 최근 종료 잡 중 알림 행이 하나도 없는 것을 보낸다."""
        if not self.rules:
            return 0
        since = self.now_fn() - timedelta(days=self.config.server.metadata_retention_days)
        n = 0
        for job in self.store.list_unnotified_finished(since):
            n += self.deliver(job)
        return n

    # ── 스레드 ──────────────────────────────────────────────────────────────

    def _safe_scan(self, what: str) -> None:
        try:
            self.scan()
        except Exception as e:  # noqa: BLE001 — 스캔 실패가 스레드를 죽이면 안 된다
            self.log(f"{what}: {type(e).__name__}")
            with self._lock:
                self.failures += 1

    def _loop(self) -> None:
        sub = self.bus.subscribe()
        try:
            # 시작 스캔은 이 스레드 안에서 — 느린 훅(타임아웃까지 붙잡는 명령)이 서버 기동과
            # HTTP 응답을 미루면 안 된다. 구독을 먼저 하고 스캔하므로 그 사이의 job_finished 도
            # 놓치지 않는다(겹치면 claim 이 한 번만 보내게 한다).
            self._safe_scan("notify scan")
            while not self.stop_event.is_set():
                ev = sub.get(timeout=POLL_SECONDS)
                if ev is None:
                    continue
                if ev.kind in (KIND_LAG, KIND_RESET):
                    # 구독 큐가 넘쳐 job_finished 를 잃었을 수 있다 — 행이 없는 종료 잡을 다시
                    self._safe_scan("notify rescan")
                    continue
                if ev.kind != KIND_JOB_FINISHED:
                    continue
                job_id = ev.data.get("job_id")
                if not isinstance(job_id, int):
                    continue
                try:
                    job = self.store.get_job(job_id)
                    if job is not None and job.is_terminal:
                        self.deliver(job)
                except Exception as e:  # noqa: BLE001 — 알림 하나가 스레드를 죽이면 안 된다
                    self.log(f"notify: {type(e).__name__}")
                    with self._lock:
                        self.failures += 1
        finally:
            self.bus.unsubscribe(sub)

    def start(self) -> None:
        if self._thread is not None or not self.rules:
            return
        self._thread = threading.Thread(target=self._loop, name="rcm-notify", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
