"""알림 스레드(M5a-3, `notify.Notifier`) — 규칙 필터(상태·프리셋) · argv 는 env 로(셸 없음) ·
사용자 문자열 정화(NUL·제어문자 제거, 4 KB) · (잡, 규칙)당 정확히 한 번(이벤트 중복은 정상 입력) ·
시작 시 미알림 종료 잡 스캔 · 실패(종료 ≠ 0 · 타임아웃 · HTTP ≠ 2xx · 3xx)는 카운터만, 재시도 없음,
`last_error` 는 안 건드린다 · url 은 POST JSON · 서버 wiring(`server.notify_failures`).
명세는 docs/m5-workplan.md M5a-3. 아직 구현 전이라 빨간 것이 정상이다.

실행은 가짜 `run`/`opener` 로 기록만 한다. 스레드는 마감 있는 폴링으로 본다.
"""

import http.server
import importlib
import itertools
import json
import subprocess
import threading
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from typing import Any

import pytest

from remote_ci_monitor import config as config_mod
from remote_ci_monitor.core.model import (
    FAILED,
    LOST,
    QUEUED,
    SUCCEEDED,
    TIMED_OUT,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.events import KIND_JOB_FINISHED, EventBus
from remote_ci_monitor.store import Store
from test_server import Server
from test_worker import make_config

NOW = datetime(2026, 9, 6, 10, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")
_seq = itertools.count(1)
ENV_KEYS = (
    "RCM_JOB_ID",
    "RCM_STATE",
    "RCM_PRESET",
    "RCM_KEY",
    "RCM_REQUESTER",
    "RCM_SUMMARY",
    "RCM_FAILED_STEP",
    "RCM_EXIT_CODE",
    "RCM_JOB_SECONDS",
    "RCM_URL",
    "RCM_NOTIFY",
)


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


# ── 도우미 ───────────────────────────────────────────────────────────────────


def rule(
    name: str,
    *,
    on: tuple[str, ...] = (),
    presets: tuple[str, ...] = (),
    argv: tuple[str, ...] | None = None,
    url: str | None = None,
    timeout_seconds: int = 30,
) -> Any:
    """`[[notify]]` 하나(`core.notify.NotifyRule`). on/presets 를 안 주면 규칙 기본값(전부)."""
    mod = importlib.import_module("remote_ci_monitor.core.notify")
    kw: dict[str, Any] = {"name": name, "timeout_seconds": timeout_seconds}
    if on:
        kw["on"] = frozenset(on)
    if presets:
        kw["presets"] = frozenset(presets)
    if argv is not None:
        kw["argv"] = tuple(argv)
    if url is not None:
        kw["url"] = url
    return mod.NotifyRule(**kw)


def failures_of(notifier: Any) -> int:
    """누적 실패 수. 속성 이름(`failures` · `notify_failures`)은 구현이 정한다."""
    if hasattr(notifier, "notify_failures"):
        return int(notifier.notify_failures)
    return int(notifier.failures)


class FakeRun:
    """`subprocess.run` 대역. (argv, kwargs) 를 기록하고 정해진 종료 코드나 타임아웃을 낸다."""

    def __init__(self, returncode: int = 0, *, timeout: bool = False):
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.returncode = returncode
        self.timeout = timeout
        self._lock = threading.Lock()

    def __call__(self, argv: Any, **kw: Any) -> subprocess.CompletedProcess:
        with self._lock:
            self.calls.append((list(argv), dict(kw)))
        if self.timeout:
            raise subprocess.TimeoutExpired(list(argv), kw.get("timeout") or 0)
        return subprocess.CompletedProcess(list(argv), self.returncode, stdout=b"", stderr=b"")

    def envs(self) -> list[dict[str, str]]:
        return [dict(kw.get("env") or {}) for _, kw in self.calls]


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return b""

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeOpener:
    """`urllib.request.OpenerDirector` 대역. 상태별로 응답하거나 HTTPError 를 낸다."""

    def __init__(self, status: int = 200):
        self.status = status
        self.requests: list[Any] = []
        self.timeouts: list[float | None] = []
        self._lock = threading.Lock()

    def open(self, req: Any, *, timeout: float | None = None, **_: Any) -> Any:
        with self._lock:
            self.requests.append(req)
            self.timeouts.append(timeout)
        if self.status >= 300:  # 리다이렉트 없는 opener 는 3xx 도 HTTPError 다
            raise urllib.error.HTTPError(req.full_url, self.status, "nope", Message(), None)
        return _FakeResponse(self.status)


def notifier_for(store: Store, cfg: Any, bus: EventBus, *, run: Any, opener: Any = None, now=NOW):
    mod = importlib.import_module("remote_ci_monitor.notify")
    logs: list[str] = []
    n = mod.Notifier(store, cfg, bus, now_fn=lambda: now, log=logs.append, run=run, opener=opener)
    return n, logs


def finished_job(
    store: Store,
    *,
    state: str = FAILED,
    preset: str = "ok",
    finished: datetime,
    summary: str | None = "2 tests failed",
    failed_step: str | None = "test",
    exit_code: int | None = 2,
    label: str = ALICE.label,
) -> Any:
    """enqueue → claim → finish. 다른 queued 잡이 없을 때 부른다."""
    tree = f"tree-{next(_seq)}"  # 합류 키가 겹치지 않게
    created = finished - timedelta(seconds=90)
    j = store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", base_sha="abc", dirty=False, tree_hash=tree),
        requester=Requester(name=ALICE.name, label=label),
        timeout_seconds=60,
        join_key=join_key(preset, {}, tree),
        now=created,
        state=QUEUED,
    )
    claimed = store.claim(1, created + timedelta(seconds=10))
    assert claimed is not None and claimed.id == j.id
    store.finish(
        j.id, state, now=finished, exit_code=exit_code, summary=summary, failed_step=failed_step
    )
    return store.get_job(j.id)


def publish_finished(bus: EventBus, job: Any) -> None:
    data = {"job_id": job.id, "state": job.state, "exit_code": job.exit_code}
    bus.publish(KIND_JOB_FINISHED, data, at=NOW)


def poll(pred, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def settle(seconds: float = 0.3) -> None:
    """「더는 안 온다」를 확인하려면 잠깐 기다리는 수밖에 없다."""
    time.sleep(seconds)


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, public_url="http://build.example:8787")
    cfg.presets = tuple(
        config_mod.parse_preset({"name": n, "argv": ["true"], "timeout_seconds": 60})
        for n in ("ok", "gate", "deploy")
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    bus = EventBus()
    yield store, cfg, bus
    store.close()


@pytest.fixture
def started():
    """테스트가 만든 Notifier 를 모아 끝에 멈춘다."""
    created: list[Any] = []
    yield created
    for n in created:
        n.stop()


# ── argv 규칙 · env ──────────────────────────────────────────────────────────


def test_argv_rule_runs_once_with_the_job_env_and_no_shell(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("slack-fail", argv=("bash", "/opt/rcm/notify.sh")),)
    run = FakeRun()
    n, logs = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    job = finished_job(store, state=FAILED, preset="gate", finished=at(0))
    publish_finished(bus, job)
    poll(lambda: len(run.calls) == 1, 3.0, "notify command")
    argv, kw = run.calls[0]
    assert argv == ["bash", "/opt/rcm/notify.sh"]
    assert not kw.get("shell")
    assert kw.get("timeout") == 30
    e = kw["env"]
    assert all(k in e for k in ENV_KEYS), sorted(k for k in ENV_KEYS if k not in e)
    assert e["RCM_JOB_ID"] == str(job.id) and e["RCM_STATE"] == "failed"
    assert e["RCM_PRESET"] == "gate" and e["RCM_KEY"] == "gate"
    assert e["RCM_REQUESTER"] in (ALICE.label, ALICE.name)
    assert e["RCM_SUMMARY"] == "2 tests failed" and e["RCM_FAILED_STEP"] == "test"
    assert e["RCM_EXIT_CODE"] == "2" and e["RCM_NOTIFY"] == "slack-fail"
    assert e["RCM_URL"] == f"http://build.example:8787/#/jobs/{job.id}"
    assert float(e["RCM_JOB_SECONDS"]) == pytest.approx(80.0, abs=1.0)  # started→finished
    assert all(isinstance(v, str) for v in e.values())
    settle()
    assert len(run.calls) == 1
    assert failures_of(n) == 0
    assert store.claim_notification(job.id, "slack-fail", at(5)) is False  # 행이 남았다


def test_null_fields_become_empty_strings_not_the_word_none(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("all", argv=("true",)),)
    run = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    job = finished_job(
        store, state=LOST, finished=at(0), summary=None, failed_step=None, exit_code=None
    )
    publish_finished(bus, job)
    poll(lambda: len(run.calls) == 1, 3.0, "notify command")
    e = run.envs()[0]
    assert e["RCM_STATE"] == "lost"
    assert e["RCM_SUMMARY"] == "" and e["RCM_FAILED_STEP"] == "" and e["RCM_EXIT_CODE"] == ""


def test_rules_filter_by_state_and_preset(env, started):
    store, cfg, bus = env
    cfg.notify = (
        rule("fail-only", on=("failed", "timed_out", "lost"), argv=("true",)),
        rule("gate-only", presets=("gate",), argv=("true",)),
        rule("all", argv=("true",)),
    )
    run = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    ok = finished_job(store, state=SUCCEEDED, preset="ok", finished=at(0), exit_code=0)
    gate = finished_job(store, state=FAILED, preset="gate", finished=at(1))
    deploy = finished_job(store, state=TIMED_OUT, preset="deploy", finished=at(2))
    for job in (ok, gate, deploy):
        publish_finished(bus, job)
    poll(lambda: len(run.calls) == 6, 3.0, "six notifications")
    settle()
    fired = sorted((int(e["RCM_JOB_ID"]), e["RCM_NOTIFY"]) for e in run.envs())
    assert fired == sorted(
        [
            (ok.id, "all"),
            (gate.id, "fail-only"),
            (gate.id, "gate-only"),
            (gate.id, "all"),
            (deploy.id, "fail-only"),
            (deploy.id, "all"),
        ]
    )
    assert store.claim_notification(ok.id, "fail-only", at(9)) is True  # 안 맞는 규칙은 행도 없다


def test_user_strings_are_sanitized_and_capped_at_4kb(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("all", argv=("true",)),)
    run = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    nasty = "xy\x00z\x1b[31m red\x07 tail " + "A" * 5000
    job = finished_job(
        store,
        finished=at(0),
        summary=nasty,
        failed_step="step\x00one\x1b",
        label="eve\x00@ci\x1b[0m",
    )
    publish_finished(bus, job)
    poll(lambda: len(run.calls) == 1, 3.0, "notify command")
    e = run.envs()[0]
    for key in ("RCM_SUMMARY", "RCM_FAILED_STEP", "RCM_REQUESTER"):
        assert "\x00" not in e[key] and "\x1b" not in e[key] and "\x07" not in e[key], key
        assert len(e[key].encode()) <= 4096, key
    assert e["RCM_SUMMARY"].startswith("xyz[31m red tail ")
    assert e["RCM_FAILED_STEP"] == "stepone"
    assert e["RCM_REQUESTER"].startswith("eve@ci")


# ── 정확히 한 번 ─────────────────────────────────────────────────────────────


def test_duplicate_job_finished_events_notify_once(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("all", argv=("true",)),)
    run = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    job = finished_job(store, finished=at(0))
    publish_finished(bus, job)  # finish
    publish_finished(bus, job)  # recover · 재발행
    publish_finished(bus, job)
    poll(lambda: len(run.calls) >= 1, 3.0, "notify command")
    settle()
    assert len(run.calls) == 1
    assert store.claim_notification(job.id, "all", at(9)) is False
    # 알림 스레드를 다시 띄워도(재시작) 같은 잡은 다시 보내지 않는다
    n.stop()
    n2, _ = notifier_for(store, cfg, bus, run=run)
    n2.start()
    started.append(n2)
    publish_finished(bus, job)
    settle()
    assert len(run.calls) == 1


def test_start_scans_finished_jobs_without_rows(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("all", argv=("true",)),)
    missed = finished_job(store, state=FAILED, finished=at(-30))  # 스레드가 없을 때 끝났다
    done = finished_job(store, state=SUCCEEDED, finished=at(-20), exit_code=0)
    assert store.claim_notification(done.id, "all", at(-19)) is True  # 이미 보낸 것
    old = finished_job(store, finished=NOW - timedelta(days=400))  # metadata_retention_days 밖
    run = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    poll(lambda: len(run.calls) == 1, 3.0, "startup scan")
    settle()
    assert [e["RCM_JOB_ID"] for e in run.envs()] == [str(missed.id)]
    assert old.id != missed.id
    assert store.claim_notification(missed.id, "all", at(1)) is False


def test_startup_scan_and_a_racing_event_still_notify_once(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("all", argv=("true",)),)
    job = finished_job(store, finished=at(-1))
    run = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    publish_finished(bus, job)  # recover_on_start 가 내는 이벤트와 스캔이 겹친다
    poll(lambda: len(run.calls) >= 1, 3.0, "notify command")
    settle()
    assert len(run.calls) == 1


# ── 실패 ─────────────────────────────────────────────────────────────────────


def test_failures_count_without_retry(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("bad-exit", argv=("false",)), rule("hook", url="https://hooks.example/x"))
    run = FakeRun(returncode=1)
    opener = FakeOpener(status=500)
    n, logs = notifier_for(store, cfg, bus, run=run, opener=opener)
    n.start()
    started.append(n)
    job = finished_job(store, finished=at(0))
    publish_finished(bus, job)
    poll(lambda: failures_of(n) == 2, 3.0, "two failures")
    settle()
    assert len(run.calls) == 1 and len(opener.requests) == 1  # 재시도 없음
    assert failures_of(n) == 2
    assert any("bad-exit" in line for line in logs) and any("hook" in line for line in logs)
    assert all(str(job.id) in line for line in logs if "bad-exit" in line or "hook" in line)
    assert store.claim_notification(job.id, "bad-exit", at(9)) is False  # 실패도 행은 남는다
    publish_finished(bus, job)
    settle()
    assert len(run.calls) == 1 and failures_of(n) == 2


def test_argv_timeout_is_passed_and_expiry_counts_as_failure(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("slow", argv=("sleep", "99"), timeout_seconds=5),)
    run = FakeRun(timeout=True)
    n, logs = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    job = finished_job(store, finished=at(0))
    publish_finished(bus, job)
    poll(lambda: failures_of(n) == 1, 3.0, "timeout failure")
    assert run.calls[0][1]["timeout"] == 5
    settle()
    assert len(run.calls) == 1
    assert any("slow" in line and ("timed out" in line or "timeout" in line) for line in logs), logs


def test_3xx_from_a_url_hook_is_a_failure(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("hook", url="https://hooks.example/x"),)
    opener = FakeOpener(status=302)
    n, _ = notifier_for(store, cfg, bus, run=FakeRun(), opener=opener)
    n.start()
    started.append(n)
    job = finished_job(store, finished=at(0))
    publish_finished(bus, job)
    poll(lambda: failures_of(n) == 1, 3.0, "3xx failure")
    settle()
    assert len(opener.requests) == 1


def test_url_rule_posts_json_with_the_job_and_rule_name(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("hook", url="http://127.0.0.1:1/hook", timeout_seconds=7),)
    opener = FakeOpener(status=204)
    n, _ = notifier_for(store, cfg, bus, run=FakeRun(), opener=opener)
    n.start()
    started.append(n)
    job = finished_job(store, state=FAILED, preset="gate", finished=at(0))
    publish_finished(bus, job)
    poll(lambda: len(opener.requests) == 1, 3.0, "hook request")
    req = opener.requests[0]
    assert req.get_method() == "POST" and req.full_url == "http://127.0.0.1:1/hook"
    assert req.get_header("Content-type", "").startswith("application/json")
    body = json.loads(req.data)
    assert body["notify"] == "hook"
    row = body.get("job", body)  # 최근 완료 행 — 중첩(`job`)이든 평평하든 내용만 본다
    assert row["id"] == job.id and row["state"] == "failed" and row["preset"] == "gate"
    assert row["summary"] == "2 tests failed" and row["failed_step"] == "test"
    assert row["exit_code"] == 2
    assert row["url"] == f"http://build.example:8787/#/jobs/{job.id}"
    assert opener.timeouts[0] == 7
    settle()
    assert failures_of(n) == 0 and len(opener.requests) == 1


class _HookHandler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []
    lock = threading.Lock()

    def log_message(self, *args: Any) -> None:  # 조용히
        return

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        with self.lock:
            self.hits.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/followed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def test_default_opener_never_follows_redirects(env, started):
    """opener 를 안 주면 Notifier 가 만드는 것은 리다이렉트를 따라가지 않아야 한다(3xx = 실패)."""
    store, cfg, bus = env
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HookHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05})
    thread.daemon = True
    thread.start()
    _HookHandler.hits = []
    try:
        cfg.notify = (
            rule("bounce", url=f"http://127.0.0.1:{port}/redirect", timeout_seconds=5),
            rule("plain", url=f"http://127.0.0.1:{port}/ok", timeout_seconds=5),
        )
        n, _ = notifier_for(store, cfg, bus, run=FakeRun(), opener=None)
        n.start()
        started.append(n)
        job = finished_job(store, finished=at(0))
        publish_finished(bus, job)
        poll(lambda: len(_HookHandler.hits) >= 2, 5.0, "two hook requests")
        settle()
        assert sorted(_HookHandler.hits) == ["/ok", "/redirect"]  # /followed 는 없다
        assert failures_of(n) == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


# ── 서버 안에서 ───────────────────────────────────────────────────────────────


def test_server_wires_the_notifier_and_counts_failures_without_last_error(tmp_path):
    srv = Server(tmp_path, workers=False)
    out = tmp_path / "notified.txt"
    try:
        srv.cfg.notify = (
            rule("file", argv=("sh", "-c", f'echo "$RCM_JOB_ID:$RCM_STATE:$RCM_NOTIFY" >> {out}')),
            rule("broken", on=("failed",), argv=("sh", "-c", "exit 3")),
        )
        srv.app.start()
        jid = srv.submit(preset="bad")[1]["job_id"]  # exit 2 → failed
        srv.upload(jid)
        assert srv.wait_terminal(jid).state == "failed"
        poll(lambda: out.exists() and out.read_text().strip() != "", 5.0, "file hook")
        status, doc = 0, None

        def failed_once() -> bool:
            nonlocal status, doc
            status, doc = srv.req("GET", "/api/status")
            return status == 200 and doc["server"]["notify_failures"] == 1

        poll(failed_once, 5.0, "notify_failures == 1")
        assert doc["server"]["last_error"] is None  # 알림 실패는 큐의 병이 아니다
        status, health = srv.req("GET", "/api/health")
        assert status == 200 and health["ok"] is True
        time.sleep(0.5)
        assert out.read_text().splitlines() == [f"{jid}:failed:file"]  # 정확히 한 번
        assert srv.req("GET", "/api/status")[1]["server"]["notify_failures"] == 1
        # 성공 잡은 `on = ["failed"]` 규칙을 건드리지 않는다
        ok = srv.submit(preset="ok", tree_hash="ab" * 32)[1]["job_id"]
        srv.upload(ok)
        assert srv.wait_terminal(ok).state == "succeeded"
        poll(lambda: len(out.read_text().splitlines()) == 2, 5.0, "second file hook")
        assert out.read_text().splitlines()[1] == f"{ok}:succeeded:file"
        time.sleep(0.3)
        assert srv.req("GET", "/api/status")[1]["server"]["notify_failures"] == 1
    finally:
        srv.close()
    assert not any("notify" in t.name for t in threading.enumerate())  # shutdown 이 스레드를 멈춘다


def test_server_without_notify_rules_reports_zero_failures_and_no_thread(tmp_path):
    srv = Server(tmp_path, workers=True)
    try:
        doc = srv.req("GET", "/api/status")[1]
        assert doc["server"]["notify_failures"] == 0
        jid = srv.submit()[1]["job_id"]
        srv.upload(jid)
        assert srv.wait_terminal(jid).state == "succeeded"
        assert srv.req("GET", "/api/status")[1]["server"]["notify_failures"] == 0
        assert srv.req("GET", "/api/status")[1]["server"]["last_error"] is None
    finally:
        srv.close()


# ── 시작 스캔은 스레드 안 · lag 뒤 재스캔 (격리 검증에서 추가) ─────────────────────────────


class _SlowRun(FakeRun):
    """훅 하나가 오래 걸린다 — 시작 스캔이 `start()` 를 붙잡으면 안 된다."""

    def __call__(self, argv: Any, **kw: Any) -> subprocess.CompletedProcess:
        time.sleep(1.0)
        return super().__call__(argv, **kw)


def test_start_returns_at_once_even_when_the_startup_scan_runs_a_slow_hook(env, started):
    store, cfg, bus = env
    cfg.notify = (rule("slow", argv=("sleep", "1")),)
    missed = finished_job(store, finished=at(-30))
    run = _SlowRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    t = time.monotonic()
    n.start()
    started.append(n)
    assert time.monotonic() - t < 0.5  # 스캔은 스레드가 한다 — 서버 기동(HTTP)이 기다리지 않는다
    poll(lambda: len(run.calls) == 1, 5.0, "startup scan in the thread")
    assert [e["RCM_JOB_ID"] for e in run.envs()] == [str(missed.id)]


def test_lag_on_the_bus_triggers_a_rescan_so_nothing_is_lost(env, started):
    from remote_ci_monitor.events import KIND_LAG

    store, cfg, bus = env
    cfg.notify = (rule("all", argv=("true",)),)
    run = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run)
    n.start()
    started.append(n)
    settle()
    job = finished_job(store, finished=at(-1))  # job_finished 이벤트는 (넘쳐서) 오지 않았다
    settle()
    assert run.calls == []
    bus.publish(KIND_LAG, {}, at=NOW)
    poll(lambda: len(run.calls) == 1, 3.0, "rescan after lag")
    settle()
    assert [e["RCM_JOB_ID"] for e in run.envs()] == [str(job.id)]
    assert store.claim_notification(job.id, "all", at(1)) is False
