"""`rcm worker` 가 기동할 때와 기동 등록 409 일 때 서버 버전을 따라간다 (M5q PR B1).

`docs/m5q-test-scenarios-b.md` 의 start 줄 가운데 B1 범위다(B1 · B3 · B4 · B6 · E1~E6 · E11 · E19 ·
G15 · G23 · G26 · G29). 명세는 `docs/m5q-workplan.md` §3.2 ① · ② · §5.4 · §5.5 · §14.9 다.

`cli._main(None, follow_enabled=True, execve=가짜)` 로 돈다. 가짜 execve 는 받은 인자를 남기고
`Execed` 를 던져 돌아오지 않는 척한다. 서버는 진짜 `/worker/*` 를 가진 `WorkerServer` 이고 버전을
올릴 때 그 버전의 가짜 wheel 을 싣는다(`bump`). 요청 수는 요청 처리기에서 센다. 시험 venv 는
editable 이라 기본값이 `installed` 다 — 따라가는 갈래는 `follow.install_kind` 를 `wheel` 로 바꾼다.

시험을 먼저 썼다 — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor
from follow_helpers import INSTALLED, NEW, NEW2, entries, fake_wheel, leftovers, sha, write_pin
from remote_ci_monitor import cli, follow, remote_worker
from remote_ci_monitor.config import WorkerConfig
from remote_ci_monitor.core.mdns import Found
from remote_ci_monitor.remote_worker import RemoteWorker, WorkerExit
from remote_ci_monitor.server import ApiError
from test_cli_worker import TOKEN_ENV, WORKER, make_server

pytestmark = pytest.mark.skipif(os.name != "posix", reason="follow is POSIX only (결정 172)")

# 구현 전에는 없다 — 모듈을 import 하다 죽지 않고 시험마다 빨개지게 이름만 늦게 찾는다
WorkerFollow: Any = getattr(remote_worker, "WorkerFollow", type("Missing", (Exception,), {}))
MISMATCH = "install the same release"
MALFORMED = "ignoring a malformed server_version from the server"


class Execed(BaseException):  # noqa: N818 — 「돌아오지 않는 exec」을 흉내 내는 신호
    """가짜 execve 가 던진다. `except Exception` 에 먹히지 않게 BaseException 이다."""


@dataclass
class Result:
    code: Any
    out: str
    err: str


def sentence(server: str, worker: str = INSTALLED) -> str:
    return f"worker version {worker}, server {server} — {MISMATCH}"


class WorkerEnv:
    """시험 안의 워커 머신 하나와 진짜 서버 하나."""

    def __init__(self, monkeypatch, capsys, tmp_path: Path):
        self.mp = monkeypatch
        self.capsys = capsys
        self.tmp = tmp_path
        home = tmp_path / "home"
        (home / ".local" / "share").mkdir(parents=True)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("HOME", str(home))
        for var in ("RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN", "RCM_LABEL"):
            monkeypatch.delenv(var, raising=False)
        self.srv = make_server(tmp_path, worker_claim_wait_seconds=1)
        self.url = f"http://127.0.0.1:{self.srv.port}"
        self.key = follow.normalize_server(self.url)
        self.token = self.srv.tokens[WORKER]
        monkeypatch.setenv(TOKEN_ENV, self.token)
        self.data = tmp_path / "worker-data"
        self.root = Path(os.path.realpath(os.environ["XDG_CACHE_HOME"])) / "rcm"
        self.clients = self.root / "clients"
        self.config_home = Path(os.environ["XDG_CONFIG_HOME"])
        self.execs: list[tuple[str, list[str], dict[str, str]]] = []
        self.registers: list[str | None] = []
        self.before_register: list[Any] = []  # 등록 처리 직전에 한 번씩 부른다
        self.after_register: Any = None  # (ApiError) → 본문을 바꾼다
        self.paths: list[str] = []
        self.kind("wheel")
        self._count_requests()
        self._wrap_register()

    # ── 준비 ──
    def kind(self, kind: str) -> None:
        self.mp.setattr(follow, "install_kind", lambda: kind)

    def bump(self, version: str, *, tamper: bool = False) -> bytes:
        """서버가 `version` 을 돌리는 척 — 그 버전의 가짜 wheel 을 싣는다. `tamper` 면 health 의
        sha256 과 다른 바이트를 준다(한 바이트를 뒤집는다)."""
        wheel = fake_wheel(version)
        served = wheel
        if tamper:
            served = wheel[:100] + bytes([wheel[100] ^ 0xFF]) + wheel[101:]
        app = self.srv.app
        app.version = version
        app._wheel = (served, sha(wheel))
        app._wheel_error = None
        app._wheel_done = True
        return wheel

    def _count_requests(self) -> None:
        base = self.srv.httpd.RequestHandlerClass
        outer = self

        def parse_request(handler) -> bool:
            ok = base.parse_request(handler)
            outer.paths.append(handler.path)
            return ok

        self.srv.httpd.RequestHandlerClass = type(
            "Counting", (base,), {"parse_request": parse_request}
        )

    def _wrap_register(self) -> None:
        real = self.srv.app.worker_register
        outer = self

        def register(token, body):
            outer.registers.append(body.get("version") if isinstance(body, dict) else None)
            if outer.before_register:
                outer.before_register.pop(0)()
            try:
                return real(token, body)
            except ApiError as e:
                if outer.after_register is not None:
                    outer.after_register(e)
                raise

        self.mp.setattr(self.srv.app, "worker_register", register)

    def bump_before_register(self, version: str, **kw: Any) -> None:
        """① 은 지금 버전을 보고, 등록 직전에 서버가 바뀐다(B4)."""
        self.before_register.append(lambda: self.bump(version, **kw))

    @property
    def health(self) -> int:
        return sum(1 for p in self.paths if p == "/api/health")

    @property
    def client(self) -> int:
        return sum(1 for p in self.paths if p.startswith("/client/"))

    def argv(self, *extra: str, server: str | None = None) -> list[str]:
        return [
            "worker",
            "--server",
            server or self.url,
            "--pool",
            "linux",
            "--data",
            str(self.data),
            *extra,
        ]

    def record(self, version: str, *, age: float) -> None:
        """그 서버의 health 기록(나이 `age` 초)을 미리 넣는다."""
        wheel = fake_wheel(version)
        doc = {
            "ok": True,
            "version": version,
            "client_wheel": {
                "path": "/client/" + follow.wheel_filename(version),
                "sha256": sha(wheel),
                "bytes": len(wheel),
            },
            "client_wheel_error": None,
        }
        info = follow.parse_health(self.key, doc, now=time.time() - age)
        follow.check_cache_safe(self.root)
        follow.save_record(self.root, info, server=self.key, failed_at=None)

    # ── 실행 ──
    def fake_execve(self, path: str, args: list[str], env: dict[str, str]) -> None:
        self.execs.append((path, list(args), dict(env)))
        raise Execed

    def run(self, argv: list[str], *, execve: Any = None, enabled: bool = True) -> Result:
        self.mp.setattr(sys, "argv", ["rcm", *argv])
        try:
            if enabled:
                code: Any = cli._main(None, follow_enabled=True, execve=execve or self.fake_execve)
            else:
                code = cli.main(argv)
        except Execed:
            code = "exec"
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
        cap = self.capsys.readouterr()
        return Result(code, cap.out, cap.err)

    def close(self) -> None:
        self.srv.close()


@pytest.fixture
def w(monkeypatch, capsys, tmp_path):
    env = WorkerEnv(monkeypatch, capsys, tmp_path)
    yield env
    env.close()


def last_err_line(r: Result) -> str:
    lines = [ln for ln in r.err.splitlines() if MISMATCH in ln]
    assert lines, r.err
    return lines[-1]


# ── ① 기동 ────────────────────────────────────────────────────────────────


def test_start_reads_health_fresh_and_follows_before_registering(w):
    # B1 · W1 · §13 P2-7: 10분 안의 기록(설치본과 같은 버전)을 무시하고 health 를 새로 읽는다
    w.record(INSTALLED, age=60)
    w.bump(NEW)
    r = w.run(w.argv())
    assert r.code == "exec", r.err
    assert w.health == 1 and w.client == 1, w.paths
    assert w.registers == []  # 따라가기가 등록보다 먼저다
    path, args, env = w.execs[-1]
    assert path == sys.executable
    assert args == [sys.executable, "-P", "-c", follow.BOOT, *w.argv()]
    assert env["RCM_FOLLOWED"] == NEW and env["RCM_FOLLOWED_FROM"] == INSTALLED
    assert env[TOKEN_ENV] == w.token
    made = entries(w.clients)
    assert len(made) == 1
    assert json.loads((made[0] / follow.MARKER).read_text())["version"] == NEW
    assert f"rcm: following server v{NEW} (installed v{INSTALLED})" in r.err


def test_same_version_does_nothing(w):
    # B3: health 1번, `/client/` 0번, exec 0번, 등록 1번
    jid = w.srv.queued_job(preset="lin")
    r = w.run(w.argv("--once", "--lanes", "1"))
    assert r.code == 0, r.err
    assert w.health == 1 and w.client == 0 and w.execs == []
    assert w.registers == [INSTALLED]
    assert "following" not in r.err
    assert w.srv.store.get_job(jid).state == "succeeded"


def test_pinned_to_the_servers_version_fetches_it(w):
    # B6 (§2.7 의 3 갈래): 고정 버전이 서버와 같으면 받아서 exec 한다. 등록 409 가 없다
    w.mp.setenv("RCM_CLIENT_VERSION", NEW)
    w.bump(NEW)
    r = w.run(w.argv())
    assert r.code == "exec", r.err
    assert w.client == 1 and w.registers == []
    assert w.execs[-1][2]["RCM_FOLLOWED"] == NEW


def test_start_failure_runs_installed_and_registers(w, monkeypatch):
    # E2 (§2.6 · §3.2 ①): health 가 3초 제한을 넘기면 경고 한 줄을 쓰고 설치본으로 등록한다
    real = w.srv.app.health

    def slow():
        time.sleep(4)
        return real()

    monkeypatch.setattr(w.srv.app, "health", slow)
    jid = w.srv.queued_job(preset="lin")
    r = w.run(w.argv("--once", "--lanes", "1"))
    assert r.code == 0, r.err
    assert (
        f"rcm: not following {w.key}: cannot reach the server — running installed v{INSTALLED}"
        in r.err
    )
    assert w.registers == [INSTALLED] and w.execs == []
    assert w.srv.store.get_job(jid).state == "succeeded"


def test_start_exec_failure_runs_installed_then_409_says_why(w):
    # E11 · §14 B질문6: ① 의 exec 이 OSError 면 `exec_failed` 로 설치본이 등록을 이어 간다.
    # 등록이 409 면 ② 가 캐시로 다시 해 보고, 또 실패하면 이유를 붙여 종료 2
    w.bump(NEW)
    calls: list[Any] = []

    def broken(path, args, env):
        calls.append(args)
        raise OSError(errno.E2BIG, os.strerror(errno.E2BIG))

    r = w.run(w.argv(), execve=broken)
    assert r.code == 2, r.err
    assert len(calls) == 2
    why = f"cannot start the cached client: {os.strerror(errno.E2BIG)}"
    assert f"rcm: not following server v{NEW}: {why} — running installed v{INSTALLED}" in r.err
    assert last_err_line(r) == f"rcm: {sentence(NEW)} · not following: {why}"
    assert w.registers == [INSTALLED]
    assert w.client == 1  # ② 는 ① 이 받아 둔 캐시를 쓴다
    assert "Traceback" not in r.err


# ── ② 기동 등록 409 ────────────────────────────────────────────────────────


def test_start_409_follows_the_server(w):
    # B4 · W2: ① 은 같은 버전을 봤고 등록 직전에 서버가 바뀌었다
    w.bump_before_register(NEW)
    r = w.run(w.argv())
    assert r.code == "exec", r.err
    assert w.registers == [INSTALLED]
    assert w.health == 2 and w.client == 1, w.paths
    assert w.execs[-1][1][4:] == w.argv()
    assert w.execs[-1][2]["RCM_FOLLOWED"] == NEW
    assert f"[rcm worker] register: server runs v{NEW} — following" in r.err
    assert MISMATCH not in r.err  # 409 문장은 따라가지 못했을 때만 보인다
    assert w.srv.store.get_worker(WORKER) is None


def test_start_409_sha_mismatch_exits_2_saying_so(w):
    # E1 · W3: ② 에서 받은 wheel 의 sha256 이 다르다 — 종료 2, 캐시에 아무것도 없다
    w.bump_before_register(NEW, tamper=True)
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert last_err_line(r) == f"rcm: {sentence(NEW)} · not following: sha256 mismatch"
    assert w.execs == [] and w.client == 1
    assert entries(w.clients) == [] and leftovers(w.clients) == []
    assert "Traceback" not in r.err


def test_start_failure_then_409_fetches_again(w):
    # §14 B질문6: ① 이 sha_mismatch 로 설치본에 남았는데 등록이 409 면 ② 가 한 번 더 받는다
    w.bump(NEW, tamper=True)
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert f"rcm: not following server v{NEW}: sha256 mismatch" in r.err
    assert last_err_line(r) == f"rcm: {sentence(NEW)} · not following: sha256 mismatch"
    assert w.client == 2 and w.registers == [INSTALLED]


def test_pinned_worker_ends_on_409_saying_pinned(w):
    # E3 · W4: 고정 버전이 설치본이면 ① 은 서버에 묻지 않는다. 409 는 종료 2 이고 `pinned`
    w.mp.setenv("RCM_CLIENT_VERSION", INSTALLED)
    w.bump(NEW)
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert w.health == 0 and w.client == 0
    assert last_err_line(r) == f"rcm: {sentence(NEW)} · pinned by RCM_CLIENT_VERSION"


@pytest.mark.parametrize("how", ["env", "file", "flag", "top-flag", "editable"])
def test_not_following_worker_ends_on_409_with_the_reason(w, how):
    # E4 · §14 B질문19: installed · editable 워커는 따라가지 않고 오늘의 문장 뒤에 이유를 붙인다
    argv = w.argv()
    if how == "env":
        w.mp.setenv("RCM_CLIENT_VERSION", "installed")
        tail = "not following (RCM_CLIENT_VERSION=installed)"
    elif how == "file":
        pin = write_pin(w.config_home, "installed\n")
        tail = f"not following ({follow.tilde(pin)})"
    elif how == "flag":
        argv = w.argv("--no-follow")
        tail = "not following (--no-follow)"
    elif how == "top-flag":
        argv = ["--no-follow", *w.argv()]
        tail = "not following (--no-follow)"
    else:
        w.kind("editable")
        tail = "not following (editable install)"
    w.bump(NEW)
    r = w.run(argv)
    assert r.code == 2, r.err
    assert w.health == 0 and w.client == 0 and w.execs == []
    assert last_err_line(r) == f"rcm: {sentence(NEW)} · {tail}"


def test_409_without_server_version_is_todays_exit(w):
    # E5: B1 이전 서버(본문에 `server_version` 이 없다)면 따라가지 않는다
    w.bump_before_register(NEW)
    w.after_register = lambda e: e.extra.pop("server_version", None)
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert last_err_line(r) == f"rcm: {sentence(NEW)}"
    assert w.health == 1 and w.client == 0 and w.execs == []


@pytest.mark.parametrize("bad", ["../x", "0.8.1/../../x", "1" * 65, 7, "0.8.1\n", None])
def test_malformed_server_version_is_ignored(w, bad):
    # E6 · §14.1: 형식이 틀린 값은 없는 것으로 본다 — 따라가지 않고 오늘처럼 종료 2
    w.bump_before_register(NEW)

    def forge(e: ApiError) -> None:
        e.extra["server_version"] = bad

    w.after_register = forge
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert last_err_line(r) == f"rcm: {sentence(NEW)}"
    assert w.health == 1 and w.client == 0 and w.execs == []
    assert entries(w.clients) == []
    if bad is not None:
        assert r.err.count(MALFORMED) == 1
        assert "../x" not in r.err


LOADED_CHILD = {
    "RCM_FOLLOWED": INSTALLED,
    "RCM_FOLLOWED_PATH": "/x",
    "RCM_FOLLOWED_FROM": "0.0.1",
    "RCM_FOLLOW_FROM": "wheel",
    "RCM_FOLLOW_FOUND": None,
    "loaded": True,
}


def test_followed_child_restarts_through_the_installed_rcm_on_a_409(w, monkeypatch):
    # E19 · §14 B질문7(B2): 따라간 자식은 ① 을 하지 않고, ② 를 만나면 설치본 부트로더로 바로
    # 다시 실행한다 — 다음 판정은 설치본이 ① 부터 한다
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", dict(LOADED_CHILD), raising=False)
    w.bump(NEW2)
    r = w.run(w.argv())
    assert r.code == "exec", r.err
    assert w.health == 0 and w.client == 0
    path, args, env = w.execs[-1]
    assert args == [sys.executable, "-P", "-c", follow.RESTART, *w.argv()]
    assert abs(int(env[follow.ENV_WORKER_RESTARTED_AT]) - time.time()) <= 2
    assert f"[rcm worker] register: server runs v{NEW2} — following" in r.err
    assert MISMATCH not in r.err


def test_followed_child_inside_the_window_hands_the_409_to_the_supervisor(w, monkeypatch):
    # §14 B질문7: 300초 안에 이미 재실행했으면 종료 2 로 감독자에게 맡긴다(고리 막기)
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", dict(LOADED_CHILD), raising=False)
    monkeypatch.setenv(follow.ENV_WORKER_RESTARTED_AT, str(int(time.time()) - 10))
    w.bump(NEW2)
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert w.health == 0 and w.client == 0 and w.execs == []
    assert last_err_line(r) == (
        f"rcm: {sentence(NEW2)} · not following: already following v{INSTALLED}"
    )


def test_fallen_back_child_says_the_cache_did_not_load(w, monkeypatch):
    # §14.7 A질문14 · B질문14: BOOT 가 설치본으로 돌아간 자식은 ② 에서 그 이유를 말한다
    boot = {
        "RCM_FOLLOWED": NEW,
        "RCM_FOLLOWED_PATH": "/x",
        "RCM_FOLLOWED_FROM": INSTALLED,
        "RCM_FOLLOW_FROM": "wheel",
        "RCM_FOLLOW_FOUND": None,
        "loaded": False,
    }
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", boot, raising=False)
    w.bump(NEW)
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert w.health == 0 and w.client == 0 and w.execs == []
    assert last_err_line(r) == (
        f"rcm: {sentence(NEW)} · not following: the cached client did not load"
    )
    # B1 격리 검증: 첫 실패에도 그 폴더를 치우는 안내가 한 번 나온다. #234 격리 검증: 줄 전체가
    # 정확하고(`rcm: rcm:` 이 아니다) 두 번째 판과 같은 순서(안내 → 409 문장)다
    lines = r.err.splitlines()
    assert lines.count("rcm: remove /x to fetch it again") == 1, r.err
    assert "rcm: rcm:" not in r.err
    assert lines.index("rcm: remove /x to fetch it again") < lines.index(last_err_line(r))


def test_pinned_child_says_pinned_on_a_409(w, monkeypatch):
    # B1 격리 검증: 캐시에 있는 다른 버전으로 고정해 그 버전으로 도는 자식이 409 를 받으면 원인은
    # 고정이다 — 「already following」이 아니라 `pinned by …` 로 말하고 다시 실행하지 않는다
    boot = {**LOADED_CHILD, "RCM_FOLLOWED": INSTALLED, "RCM_FOLLOWED_FROM": "0.0.1"}
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", boot, raising=False)
    w.mp.setenv("RCM_CLIENT_VERSION", INSTALLED)
    w.bump(NEW)
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert w.execs == [] and w.health == 0 and w.client == 0
    assert last_err_line(r) == f"rcm: {sentence(NEW)} · pinned by RCM_CLIENT_VERSION"


def test_broken_cache_on_start_says_so_once_and_does_not_claim_to_follow(w):
    # B1 격리 검증: ① 이 `.bad` 표지가 있는 폴더를 만나면 경고와 치우는 안내를 한 번 쓴다. 이어지는
    # ② 는 같은 폴더로 따라가려 하지 않는다 — 「following」 줄도, 두 번째 안내도 없다
    w.bump(NEW)
    health = w.srv.app.health()[1]
    info = follow.parse_health(w.key, health, now=time.time())
    entry = follow.fetch(w.key, None, info, root=w.root)
    (entry.path / follow.BAD).touch()
    fetched = w.client
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert w.execs == [] and w.client == fetched
    remove = f"rcm: remove {entry.path} to fetch it again"
    lines = r.err.splitlines()
    assert lines.count(remove) == 1, r.err
    assert "rcm: rcm:" not in r.err
    assert lines.index(remove) < lines.index(last_err_line(r))
    assert "— following" not in r.err
    assert last_err_line(r) == (
        f"rcm: {sentence(NEW)} · not following: the cached client v{NEW} did not load before"
    )


# ── --once · --check · 결정 162 · 설정 ─────────────────────────────────────


def test_once_follows_on_start(w):
    # G15 · §14 B질문4: `--once` 도 ① 은 따라간다
    w.bump(NEW)
    r = w.run(w.argv("--once"))
    assert r.code == "exec", r.err
    assert w.execs[-1][1][4:] == w.argv("--once")


def test_once_does_not_follow_a_409(w):
    # G15 · §14 B질문4: `--once` 의 ② 는 오늘처럼 종료 2
    w.bump_before_register(NEW)
    r = w.run(w.argv("--once"))
    assert r.code == 2, r.err
    assert last_err_line(r) == f"rcm: {sentence(NEW)}"
    assert w.health == 1 and w.client == 0 and w.execs == []


def test_check_does_not_follow(w):
    # G26 · §14 B질문5: `rcm worker --check` 는 설치본으로 돌며 서버와의 차이를 보인다
    w.bump(NEW)
    r = w.run(w.argv("--check"))
    assert r.code in (0, 1), r.err
    assert w.execs == [] and w.client == 0 and w.registers == []
    assert f"v{NEW}" in r.out


def test_main_with_an_argument_list_never_follows(w):
    # 결정 162 · §14 B질문3: `main(argv)` 는 ① 도 ② 도 하지 않는다 — 오늘의 문장 그대로
    w.bump(NEW)
    r = w.run(w.argv(), enabled=False)
    assert r.code == 2, r.err
    assert w.health == 0 and w.client == 0 and w.execs == []
    assert last_err_line(r) == f"rcm: {sentence(NEW)}"


def test_invalid_setting_is_a_usage_error(w):
    # §14.3 A질문9: 워커도 설정을 읽는다 — 틀린 값은 종료 2, 서버에 요청 0
    w.mp.setenv("RCM_CLIENT_VERSION", "lates")
    r = w.run(w.argv())
    assert r.code == 2, r.err
    assert "RCM_CLIENT_VERSION 'lates' is not follow, installed or a version" in r.err
    assert w.paths == []


def test_no_follow_beats_an_invalid_setting(w):
    # §14.3 A질문9 · §5.5: `--no-follow` 면 틀린 환경 변수는 경고 한 줄이다
    w.mp.setenv("RCM_CLIENT_VERSION", "lates")
    jid = w.srv.queued_job(preset="lin")
    r = w.run(w.argv("--once", "--lanes", "1", "--no-follow"))
    assert r.code == 0, r.err
    assert "rcm: ignoring invalid RCM_CLIENT_VERSION 'lates' (--no-follow)" in r.err
    assert w.health == 0 and w.srv.store.get_job(jid).state == "succeeded"


def test_help_lists_no_follow(w):
    # §5.2: `worker` 에도 `--no-follow` 가 있다
    r = w.run(["worker", "--help"])
    assert r.code == 0 and "--no-follow" in r.out


# ── server = "auto" (§14 B질문16) ───────────────────────────────────────────


def found(port: int) -> Found:
    return Found(name="mini", host="mini.local.", port=port, ips=("127.0.0.1",), version=NEW)


def test_auto_worker_passes_the_found_address(w, monkeypatch):
    # G23: 발견은 한 번, 자식 환경에 `RCM_SERVER=<찾은 주소>` 와 `RCM_FOLLOW_FOUND=1`
    calls: list[Any] = []
    monkeypatch.setattr(cli, "discover", lambda **kw: calls.append(kw) or [found(w.srv.port)])
    w.bump(NEW)
    r = w.run(w.argv(server="auto"))
    assert r.code == "exec", r.err
    assert len(calls) == 1
    env = w.execs[-1][2]
    assert env["RCM_SERVER"] == w.url and env["RCM_FOLLOW_FOUND"] == "1"


def test_followed_auto_worker_uses_the_passed_address(w, monkeypatch):
    # G23 · §14 B질문16: 발견 표시가 있는 자식은 다시 찾지 않고 넘겨받은 주소에 등록한다
    monkeypatch.setattr(cli, "discover", lambda **kw: pytest.fail("child must not discover"))
    boot = {
        "RCM_FOLLOWED": INSTALLED,
        "RCM_FOLLOWED_PATH": "/x",
        "RCM_FOLLOWED_FROM": "0.0.1",
        "RCM_FOLLOW_FROM": "wheel",
        "RCM_FOLLOW_FOUND": "1",
        "loaded": True,
    }
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", boot, raising=False)
    monkeypatch.setenv("RCM_SERVER", w.url)
    jid = w.srv.queued_job(preset="lin")
    r = w.run(w.argv("--once", "--lanes", "1", server="auto"))
    assert r.code == 0, r.err
    assert w.registers == [INSTALLED]
    assert w.srv.store.get_job(jid).state == "succeeded"
    assert "RCM_SERVER" not in os.environ  # 읽은 뒤에는 지운다 — 잡으로 새지 않게


def test_auto_worker_without_the_found_mark_ignores_rcm_server(w, monkeypatch):
    # §14 B질문16: 발견 표시가 없으면 오늘처럼 `RCM_SERVER` 를 읽지 않는다
    calls: list[Any] = []
    monkeypatch.setattr(cli, "discover", lambda **kw: calls.append(kw) or [])
    monkeypatch.setenv("RCM_SERVER", w.url)
    r = w.run(w.argv(server="auto"))
    assert r.code == 2 and len(calls) == 1, r.err
    assert w.registers == []


# ── RemoteWorker (§5.4 · §14 B질문1 · B질문2) ───────────────────────────────


def test_worker_follow_is_a_worker_exit():
    # §14 B질문2: B1 만 있는 빌드에서도 레인이 `WorkerExit` 로 잡는다
    e = WorkerFollow(NEW, sentence(NEW))
    assert isinstance(e, WorkerExit)
    assert e.version == NEW and e.code == 2 and e.message == sentence(NEW)


def worker_for(w: WorkerEnv, logs: list[str], lanes: int = 2) -> RemoteWorker:
    cfg = WorkerConfig(
        server=w.url,
        token=w.token,
        pool="linux",
        lanes=lanes,
        data_dir=str(w.tmp / "rw"),
    )
    return RemoteWorker(cfg, log=logs.append)


def test_register_raises_worker_follow_with_the_version(w):
    # §5.4: 기동 `register()` 가 버전 불일치 409 와 `server_version` 을 받으면 던진다
    w.bump(NEW)
    rw = worker_for(w, [])
    with pytest.raises(WorkerFollow) as info:
        rw.register()
    assert info.value.version == NEW and info.value.message == sentence(NEW)


def test_lane_reregister_409_ends_with_exit_2(w, monkeypatch):
    # G29 · §14 B질문1: 레인 재등록이 버전 불일치 409 면 `run()` 은 종료 2 다(종료 0 이면 감독자가
    # 다시 띄우지 않는다). 예외가 레인 스레드 밖으로 새지 않는다
    leaked: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: leaked.append(args))
    logs: list[str] = []
    rw = worker_for(w, logs)
    out: dict[str, Any] = {}
    t = threading.Thread(target=lambda: out.update(code=rw.run()), daemon=True)
    t.start()
    deadline = time.monotonic() + 20
    while w.srv.store.get_worker(WORKER) is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert w.srv.store.get_worker(WORKER) is not None, logs
    w.bump(NEW)
    w.srv.store._conn().execute("DELETE FROM workers WHERE name=?", (WORKER,))
    t.join(30)
    assert not t.is_alive(), logs
    assert out.get("code") == 2, (out, logs)
    assert any(sentence(NEW) in ln for ln in logs), logs
    assert leaked == []


def test_check_server_row_cleans_the_servers_version(w, monkeypatch):
    # 격리 검증(#226 범위 밖): `--check` 의 server 행도 서버가 보낸 버전의 제어 문자를 지우고
    # 64자로 자른다(§14.5) — ESC 가 터미널을 바꾸거나 줄바꿈이 행을 쪼개지 않게
    real = w.srv.app.health

    def forged():
        status, body = real()
        return status, {**body, "version": "9.9\x1b[2J\n" + "x" * 100}

    monkeypatch.setattr(w.srv.app, "health", forged)
    r = w.run(w.argv("--check"))
    line = next(ln for ln in r.out.splitlines() if ln.split()[1:2] == ["server"])
    assert "\x1b" not in r.out and "\x1b" not in r.err
    assert line.endswith(" · v9.9[2J" + "x" * 58), line


# ── PR B2: 드레인 뒤 설치본 부트로더로 다시 실행 ───────────────────────────


def bump_once_registered(w: WorkerEnv, version: str) -> threading.Thread:
    """워커가 등록되면 서버 버전을 올린다 — heartbeat 가 그것을 보고 드레인한다(③). 30초 안에
    워커가 끝나지 않으면 멈춰서 시험이 매달리지 않고 빨개지게 한다."""
    made: list[RemoteWorker] = []
    real_init = RemoteWorker.__init__

    def init(self, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        made.append(self)

    w.mp.setattr(RemoteWorker, "__init__", init)

    def later() -> None:
        deadline = time.monotonic() + 20
        while w.srv.store.get_worker(WORKER) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        w.bump(version)
        time.sleep(30)
        for m in made:
            m.stop()

    t = threading.Thread(target=later, daemon=True)
    t.start()
    return t


def test_drain_restarts_through_the_installed_bootloader(w):
    # B8 · W5 · §3.3 의 5: 드레인이 끝나면 설치본 부트로더로 다시 실행한다. 코드 문자열에 캐시
    # 경로가 없고 나머지 인자는 원래 인자 그대로다. 따라가기 변수는 지우고 `PYTHONPATH` 는 더하지
    # 않는다. `RCM_WORKER_RESTARTED_AT` 이 지금이고 다른 변수는 그대로다
    w.srv.cfg.server.worker_heartbeat_seconds = 1
    w.mp.setenv("RCM_CLIENT_VERSION", "follow")
    w.mp.setenv("PYTHONUNBUFFERED", "1")
    w.mp.setenv("RCM_FOLLOWED_PATH", "/stale/cache")
    w.mp.delenv("PYTHONPATH", raising=False)
    bump_once_registered(w, NEW)
    r = w.run(w.argv("--lanes", "2"))
    assert r.code == "exec", r.err
    assert w.registers == [INSTALLED]
    path, args, env = w.execs[-1]
    assert path == sys.executable
    assert args[:3] == [sys.executable, "-P", "-c"]
    assert args[3] == follow.RESTART and str(w.root) not in args[3]
    assert args[4:] == w.argv("--lanes", "2")
    for name in follow.BOOT_ENV:
        assert name not in env, name
    assert "PYTHONPATH" not in env
    assert abs(int(env[follow.ENV_WORKER_RESTARTED_AT]) - time.time()) <= 2
    assert env[TOKEN_ENV] == w.token
    assert env["RCM_CLIENT_VERSION"] == "follow" and env["PYTHONUNBUFFERED"] == "1"
    lines = r.err.splitlines()
    drain = lines.index(
        f"[rcm worker] server runs v{NEW} — draining: no new claims, "
        "restarting when running jobs finish"
    )
    restart = lines.index(f"[rcm worker] drained — restarting through the installed rcm for v{NEW}")
    assert drain < restart
    # #234 격리 검증: 드레인 재실행은 「stopped」가 아니다 — 멈추는 것이 아니라 다시 뜬다
    assert "[rcm worker] stopped" not in lines


def test_restart_exec_failure_exits_1(w):
    # E10(가) · W9 · §3.4 끝: 재실행 exec 가 OSError 면 이유 한 줄과 종료 1 — 감독자가 다시 띄운다
    w.srv.cfg.server.worker_heartbeat_seconds = 1
    calls: list[Any] = []

    def broken(path, args, env):
        calls.append(args)
        raise OSError(errno.ENOENT, os.strerror(errno.ENOENT))

    bump_once_registered(w, NEW)
    r = w.run(w.argv(), execve=broken)
    assert r.code == 1, r.err
    assert len(calls) == 1 and calls[0][3] == follow.RESTART
    assert f"[rcm worker] restart failed: {os.strerror(errno.ENOENT)}" in r.err
    assert "Traceback" not in r.err


def test_signal_after_the_drain_cancels_the_restart(w, monkeypatch):
    # E13 · §14 B질문18: 드레인이 끝나 `stopping` 이 켜진 뒤의 첫 SIGTERM 은 평범한 멈춤이다 —
    # 「again」이 아니고 exec 하지 않는다
    import signal

    w.srv.cfg.server.worker_heartbeat_seconds = 1
    real_run = RemoteWorker.run

    def run_then_signal(self):
        code = real_run(self)
        assert self.restart_for == NEW
        signal.raise_signal(signal.SIGTERM)
        return code

    monkeypatch.setattr(RemoteWorker, "run", run_then_signal)
    saved = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        bump_once_registered(w, NEW)
        r = w.run(w.argv())
    finally:
        signal.signal(signal.SIGTERM, saved[0])
        signal.signal(signal.SIGINT, saved[1])
    assert r.code == 0, r.err
    assert w.execs == []
    assert "signal 15: stopping" in r.err
    assert "again" not in r.err


def test_restart_releases_the_probes_before_exec(w, monkeypatch):
    # §14 B질문13: exec 직전에 측정 프로브를 정리한다
    w.srv.cfg.server.worker_heartbeat_seconds = 1
    order: list[str] = []
    monkeypatch.setattr(RemoteWorker, "release_probes", lambda self: order.append("probes"))

    def fake(path, args, env):
        order.append("exec")
        raise Execed

    bump_once_registered(w, NEW)
    r = w.run(w.argv(), execve=fake)
    assert r.code == "exec", r.err
    assert order == ["probes", "exec"]


@pytest.mark.parametrize(
    ("how", "follows", "tail"),
    [
        ("default", True, None),
        ("editable", False, "not following (editable install)"),
        ("env-installed", False, "not following (RCM_CLIENT_VERSION=installed)"),
        ("pinned", False, "pinned by RCM_CLIENT_VERSION"),
        ("no-follow", False, "not following (--no-follow)"),
        ("main-argv", False, None),
        ("once", True, None),
    ],
)
def test_cmd_worker_hands_the_follow_setting_to_the_worker(w, monkeypatch, how, follows, tail):
    # §3.2 · §14 B질문19: 따라가는 워커만 ③ · ④ 에서 드레인한다. 따라가지 않는 워커는 ③ 의 로그와
    # ④ 의 종료 문장에 붙일 꼬리를 받는다. 인자 목록으로 부른 `main(argv)` 는 꼬리도 없다
    made: list[RemoteWorker] = []
    monkeypatch.setattr(RemoteWorker, "run", lambda self: made.append(self) or 0)
    argv = w.argv()
    enabled = True
    if how == "editable":
        w.kind("editable")
    elif how == "env-installed":
        w.mp.setenv("RCM_CLIENT_VERSION", "installed")
    elif how == "pinned":
        w.mp.setenv("RCM_CLIENT_VERSION", INSTALLED)
    elif how == "no-follow":
        argv = w.argv("--no-follow")
    elif how == "main-argv":
        enabled = False
    elif how == "once":
        argv = w.argv("--once")
    r = w.run(argv, enabled=enabled)
    assert r.code == 0, r.err
    assert len(made) == 1
    assert made[0].follows is follows
    assert made[0].follow_tail == tail
    assert made[0].once is (how == "once")
