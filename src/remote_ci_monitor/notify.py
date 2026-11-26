"""알림 스레드 — 잡이 끝나면 `[[notify]]` 규칙대로 argv 를 돌리거나 URL 에 JSON 을 POST 한다.

- 트리거는 이벤트 버스의 `job_finished` + 시작 시 미전송 스캔(재시작 직후 recover 이벤트를
  놓치지 않는다). 실행 전에 `notifications` 행을 unique insert 로 **claim** 하므로 같은 이벤트가
  두 번 와도(recover · finish · 재발행) 한 번만 보낸다 — 이벤트 중복은 정상 입력이다.
- argv 는 셸 없이, 규칙에 적힌 그대로. env 는 PATH·HOME·LANG(서버 것) + `core/notify.sanitize_text`
  로 정화된 RCM_*.
- url 은 리다이렉트를 따라가지 않는다(3xx 는 실패). 응답 본문은 버린다.
- 실패는 재시도하지 않는다. 서버 로그 한 줄 + `failures` 카운터. `server.last_error` 는 안 건드린다
  (알림 실패로 큐가 아픈 것처럼 보이면 안 된다).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from remote_ci_monitor.config import ServerConfig, server_base_url
from remote_ci_monitor.core.model import Action, Job, LocalMachine
from remote_ci_monitor.core.notify import (
    EVENT_IRREVERSIBLE_HOLD,
    EVENT_JOB_FINISHED,
    HOLD_ROW_PREFIX,
    NotifyRule,
    failure_lines,
    hold_body,
    hold_env,
    hold_rules_for,
    notify_env,
    rules_for,
    sanitize_text,
    shelf_url,
)
from remote_ci_monitor.core.queue import hold_note
from remote_ci_monitor.core.status import iso, recent_json
from remote_ci_monitor.events import (
    KIND_IRREVERSIBLE_HOLD,
    KIND_JOB_FINISHED,
    KIND_LAG,
    KIND_RESET,
    EventBus,
)
from remote_ci_monitor.store import Store

POLL_SECONDS = 1.0
NOTIFY_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG")  # 프리셋의 env_passthrough 기본값과 같다


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _pid_of(exc: BaseException) -> int | None:
    """`TimeoutExpired` 에서 프로세스 그룹을 찾는다 — 표준 예외엔 pid 가 없어 `subprocess.run` 을
    감싼 `_run_group` 이 `pid` 속성을 붙여 준다. 없으면 None(가짜 run 등)."""
    return getattr(exc, "pid", None)


def _kill_group(pid: int | None) -> None:
    if not pid:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.2)


def run_hook(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
    """`subprocess.run` 과 같되, 타임아웃 예외에 `pid` 를 실어 호출자가 그룹째 죽일 수 있게 한다."""
    timeout = kw.pop("timeout", None)
    if kw.pop("capture_output", False):  # subprocess.run 의 편의 인자를 Popen 용으로 푼다
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.PIPE
    with subprocess.Popen(argv, **kw) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            e.pid = proc.pid  # type: ignore[attr-defined]
            _kill_group(proc.pid)
            proc.kill()
            proc.wait()
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)


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
        run: Callable[..., Any] = run_hook,
        opener: urllib.request.OpenerDirector | None = None,
        base_url: str | None = None,
        stop: threading.Event | None = None,
        local_machine: Callable[[], LocalMachine | None] | None = None,
    ):
        self.store = store
        self.config = config
        self.bus = bus
        self.now_fn = now_fn
        self.log = log or (lambda msg: None)
        self.run = run
        self.opener = opener
        # `0.0.0.0` 은 링크에 넣지 않는다 — 다른 기기에서 안 열린다(M5o 결정 120)
        self.base_url = base_url or server_base_url(config.server, hostname=socket.gethostname())
        self.stop_event = stop or threading.Event()
        # 이 서버 머신(M5t-a) — 로컬 레인 잡의 `RCM_MACHINE`. 없으면 그 값은 빈 문자열이다
        self.local_machine = local_machine
        self.failures = 0
        self.delivered = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def rules(self) -> tuple[NotifyRule, ...]:
        return tuple(self.config.notify)

    # ── 실행 ────────────────────────────────────────────────────────────────

    def _run_rule(
        self,
        rule: NotifyRule,
        row: dict[str, Any],
        failures: Sequence[str] = (),
        failures_truncated: bool = False,
        shelf_link: str = "",
        hold: dict[str, Any] | None = None,
        event: str = EVENT_JOB_FINISHED,
    ) -> bool:
        # 프리셋과 같은 통과 변수(PATH · HOME · LANG) 위에 RCM_* — 없으면 `date` 조차 못 찾는
        # 훅 스크립트가 exit 1 로 끝난다(실배치에서 발견). 사용자 문자열은 RCM_* 뿐이다.
        env = {k: os.environ[k] for k in NOTIFY_ENV_PASSTHROUGH if k in os.environ}
        kw: dict[str, Any] = {
            "failures": failures,
            "failures_truncated": failures_truncated,
            "shelf_url": shelf_link,
        }
        if event == EVENT_IRREVERSIBLE_HOLD and hold is not None:
            env.update(hold_env(row, rule.name, hold, **kw))
        else:
            env.update(notify_env(row, rule.name, hold=hold, **kw))
        tag = f"notify {rule.name} #{row.get('id')}"
        if event == EVENT_IRREVERSIBLE_HOLD:
            tag = f"notify {rule.name} hold #{row.get('id')}"
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
            except subprocess.TimeoutExpired as e:
                # subprocess.run 은 자식(보통 /bin/sh)만 죽인다 — 훅이 띄운 손자(python · gh)가
                # 고아로 남아 쌓였다(실배치: 11개). 세션 = 프로세스 그룹이므로 그룹째 정리한다.
                _kill_group(getattr(e, "pid", None) or _pid_of(e))
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
            # 실패 이름은 잡 행 **밖**에 둔다 — `GET /jobs/<id>` 의 `failures[]`(판정이 붙은 객체
            # 목록)와 같은 키로 다른 모양을 싣지 않는다(M5o 결정 119)
            job_id = int(row.get("id") or 0)
            payload: dict[str, Any] = {
                "notify": rule.name,
                # 그룹 홀드(M5t-b 결정 339-e): 사건 이름과 `hold {held, group, note}` 는 늘 있다
                "event": event,
                "job": row,
                "hold": hold_body(hold, job_id),
                "failures": failure_lines(failures),
                "failure_count": len(failures),
                "failures_truncated": bool(failures_truncated),
            }
            if event == EVENT_IRREVERSIBLE_HOLD and hold is not None:
                # opt-in 홀드 알림만 뒤 다섯 칸을 더 싣는다(§4.12)
                payload["hold"].update(
                    scope=hold.get("scope"),
                    cause=hold.get("cause"),
                    worker=hold.get("worker"),
                    since=iso(hold.get("created_at")),
                    release_command=f"rcm release-hold {job_id}",
                )
            # 보관함 링크(M5p 결정 144)는 맨 위 키 — keep 이 아니거나 확정 실패면 키가 없다(§15.6)
            if shelf_link:
                payload["shelf_url"] = shelf_link
            body = json.dumps(payload, separators=(",", ":")).encode()
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
        row = recent_json(
            job, base_url=self.base_url, action=self._action(job), local=self._local()
        )
        row["summary"] = sanitize_text(row.get("summary"))
        # 잡이 찍은 순서의 대장(M5o 결정 119). 성공 · 취소 잡은 행이 없다
        failures = self.store.failure_names(job.id)
        link = self._shelf_link(job)
        # 그 잡의 열린 홀드는 여기서 **한 번** 읽는다 — env 와 URL 본문이 같은 읽기를 쓴다(339-e)
        hold = self._open_hold(job.id)
        count = 0
        for rule in matched:
            now = self.now_fn()
            if not self.store.claim_notification(job.id, rule.name, now):
                continue  # 이미 보냈거나 보내는 중 — 이벤트 중복
            ok = self._run_rule(rule, row, failures, job.fail_truncated, link, hold=hold)
            self.store.mark_notification(job.id, rule.name, delivered=ok, now=self.now_fn())
            with self._lock:
                if ok:
                    self.delivered += 1
                else:
                    self.failures += 1
            count += 1
        return count

    def deliver_hold(self, job_id: int) -> int:
        """열린 홀드 하나를 `on` 에 `irreversible_hold` 를 적은 규칙에 한 번씩 알린다(§4.12).

        중복 막기 행은 `(job_id, "hold:" + 규칙 이름)` 이다. 이미 풀린 홀드는 알리지 않는다 — 사람이
        풀었거나 워커가 확인한 것이고 사건과 감사 이력으로 남는다. 실행한 규칙 수를 돌려준다."""
        job = self.store.get_job(job_id)
        if job is None:
            return 0
        matched = hold_rules_for(job.preset, self.rules)
        if not matched:
            return 0
        hold = self._open_hold(job_id)
        if hold is None:
            return 0
        row = recent_json(
            job, base_url=self.base_url, action=self._action(job), local=self._local()
        )
        row["summary"] = sanitize_text(row.get("summary"))
        count = 0
        for rule in matched:
            name = HOLD_ROW_PREFIX + rule.name
            if not self.store.claim_notification(job_id, name, self.now_fn()):
                continue
            ok = self._run_rule(rule, row, hold=hold, event=EVENT_IRREVERSIBLE_HOLD)
            self.store.mark_notification(job_id, name, delivered=ok, now=self.now_fn())
            with self._lock:
                if ok:
                    self.delivered += 1
                else:
                    self.failures += 1
            count += 1
        return count

    def _open_hold(self, job_id: int) -> dict[str, Any] | None:
        """그 잡의 **열린** 홀드(`store.hold_of` 에 워커 이름을 더한 것). 없으면 None.

        못 읽으면 None 이고 서버 로그에 남긴다 — 알림은 홀드 때문에 멈추지 않는다. 홀드 자체는
        DB 에 있고 대기열 · 상태가 따로 보인다."""
        try:
            hold = self.store.hold_of(job_id)
            if hold is None or hold.get("released_at") is not None:
                return None
            worker = next(
                (h.get("worker") for h in self.store.open_holds() if h["job_id"] == job_id),
                None,
            )
        except Exception as e:  # noqa: BLE001
            self.log(f"notify: hold of #{job_id}: {type(e).__name__}")
            return None
        return {**hold, "worker": worker, "note": hold_note(job_id)}

    def _local(self) -> LocalMachine | None:
        """이 서버 머신. 못 읽으면 None — 머신 이름 때문에 알림을 멈추지 않는다(빈 RCM_MACHINE)."""
        if self.local_machine is None:
            return None
        try:
            return self.local_machine()
        except Exception as e:  # noqa: BLE001 — 알림 스레드가 머신 이름 하나로 죽지 않게
            self.log(f"notify: cannot tell this machine: {type(e).__name__}")
            return None

    def _action(self, job: Job) -> Action | None:
        """잡의 마지막 「사람이 할 일」 표식(M5s). 못 읽으면 None — 알림은 표식 때문에 멈추지
        않는다(`_shelf_link` 와 같은 판단). 훅은 빈 `RCM_ACTION_CODE` 를 받는다."""
        try:
            return self.store.actions_for((job.id,)).get(job.id)
        except Exception as e:  # noqa: BLE001
            self.log(f"notify: action of #{job.id}: {type(e).__name__}")
            return None

    def _shelf_link(self, job: Job) -> str:
        """keep 인 잡의 보관함 링크(M5p 결정 144). 보관 행과 묶음 행으로 「확정적으로 없다」만
        가린다(`core.notify.shelf_url`). 읽다 깨지면 정해진 링크를 그대로 싣는다 — 알림은
        보관 상태 때문에 멈추지 않는다."""
        if not getattr(job, "keep", False):
            return ""
        item_state = bundle_state = None
        try:
            item = self.store.shelf_item(job.id)
            bundle = self.store.get_bundle(job.id)
            item_state = item.state if item is not None else None
            bundle_state = bundle.get("state") if bundle is not None else None
        except Exception as e:  # noqa: BLE001 — 보관 상태를 못 읽어도 알림은 간다
            self.log(f"notify: shelf state of #{job.id}: {type(e).__name__}")
        return shelf_url(
            self.base_url,
            job.id,
            keep=True,
            item_state=item_state,
            bundle_state=bundle_state,
        )

    def scan(self) -> int:
        """시작 시: 최근 종료 잡 중 알림 행이 하나도 없는 것을 보낸다."""
        if not self.rules:
            return 0
        since = self.now_fn() - timedelta(days=self.config.server.metadata_retention_days)
        n = 0
        for job in self.store.list_unnotified_finished(since):
            n += self.deliver(job)
        # 홀드 알림(M5t-b §4.12) — 보존 기간 안의 열린 홀드 가운데 그 규칙의 `hold:` 행이 없는 것
        if any(EVENT_IRREVERSIBLE_HOLD in r.on for r in self.rules):
            for job_id in self.store.open_hold_jobs(since):
                n += self.deliver_hold(job_id)
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
                if ev.kind not in (KIND_JOB_FINISHED, KIND_IRREVERSIBLE_HOLD):
                    continue
                job_id = ev.data.get("job_id")
                if not isinstance(job_id, int):
                    continue
                try:
                    if ev.kind == KIND_IRREVERSIBLE_HOLD:
                        self.deliver_hold(job_id)
                        continue
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
