"""따라가기의 CLI 진입 (M5q PR A · `docs/m5q-test-scenarios-a.md` · 명세 §2 · §14).

`cli._main(None, follow_enabled=True, execve=가짜)` 로 돈다. 가짜 `execve` 는 받은 인자를 남기고
`Exec` 을 던져 돌아오지 않는 척한다. 시계는 `follow.time.time` 을 바꿔 민다. 서버는
`follow_helpers` 의 `FakeServer`(health 와 `/client/` 만)와 `VersionedServer`(진짜 `App`)다.
시험을 먼저 썼다 — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import errno
import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor
from follow_helpers import (
    INSTALLED,
    NEW,
    NEW2,
    OLD,
    SHA_A,
    SHA_B,
    FakeServer,
    M,
    T,
    VersionedServer,
    closed_port,
    entries,
    fake_wheel,
    health_doc,
    write_pin,
)
from remote_ci_monitor import cli, follow
from remote_ci_monitor.core.mdns import Found
from remote_ci_monitor.core.render_text import _bytes

INVALID = "is not follow, installed or a version"


class Exec(BaseException):  # noqa: N818 — 「돌아오지 않는 exec」을 흉내 내는 신호
    """가짜 execve 가 던진다. `except Exception` 에 먹히지 않게 BaseException 이다."""


@dataclass
class Result:
    code: Any
    out: str
    err: str

    @property
    def err_lines(self) -> list[str]:
        return [ln for ln in self.err.splitlines() if ln.strip()]

    def row(self, name: str) -> str | None:
        """`rcm check` 의 한 행(라벨 · 이름 · 내용 그대로)."""
        for ln in self.out.splitlines():
            parts = ln.split()
            if len(parts) >= 2 and parts[1] == name:
                return ln
        return None


class FollowEnv:
    """시험 안의 세션 머신. HOME · cwd 는 tmp, 설치 종류는 주입, 시계는 `now`."""

    def __init__(self, monkeypatch, capsys, tmp_path: Path):
        self.mp = monkeypatch
        self.capsys = capsys
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.cwd = tmp_path / "cwd"
        self.cwd.mkdir()
        monkeypatch.chdir(self.cwd)
        monkeypatch.setenv("HOME", str(self.home))
        for var in ("RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG", "RCM_LABEL", "MY_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        self.kind("wheel")
        self.now = T
        monkeypatch.setattr(follow.time, "time", lambda: self.now)
        self.execs: list[tuple[str, list[str], dict[str, str]]] = []
        self.closers: list[Any] = []
        self.root = Path(os.path.realpath(os.environ["XDG_CACHE_HOME"])) / "rcm"
        self.clients = self.root / "clients"
        self.config = Path(os.environ["XDG_CONFIG_HOME"]) / "rcm"

    # ── 준비 ──
    def kind(self, kind: str) -> None:
        self.mp.setattr(follow, "install_kind", lambda: kind)

    def serve(self, *args: Any, **kw: Any) -> FakeServer:
        srv = FakeServer(*args, **kw)
        self.closers.append(srv)
        return srv

    def real(self, version: str, wheel: bytes | None, **kw: Any) -> VersionedServer:
        srv = VersionedServer(self.tmp, version, wheel, **kw)
        self.closers.append(srv)
        return srv

    def use(self, srv: Any, token: str | None = None) -> None:
        self.mp.setenv("RCM_SERVER", srv.url)
        if token is not None:
            self.mp.setenv("RCM_TOKEN", token)

    def env(self, name: str, value: str | None) -> None:
        if value is None:
            self.mp.delenv(name, raising=False)
        else:
            self.mp.setenv(name, value)

    def client_toml(self, text: str) -> Path:
        p = self.config / "client.toml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        p.chmod(0o600)
        return p

    def info(self, srv: FakeServer, now: float = T) -> follow.ServerInfo:
        return follow.parse_health(srv.key, srv.health, now=now)

    def cache(self, srv: FakeServer) -> follow.CacheEntry:
        """진짜 `fetch` 로 캐시 항목을 만든다(손으로 쓴 캐시는 두 쪽이 같이 틀려도 초록이다)."""
        return follow.fetch(srv.key, None, self.info(srv), root=self.root)

    def record(self, srv: FakeServer, *, checked_at: float, health: dict | None = None) -> None:
        info = follow.parse_health(srv.key, health or srv.health, now=checked_at)
        follow.save_record(self.root, info, server=srv.key, failed_at=None)

    # ── 실행 ──
    def fake_execve(self, path: str, args: list[str], env: dict[str, str]) -> None:
        self.execs.append((path, list(args), dict(env)))
        raise Exec()

    def run(self, argv: list[str], *, execve=None) -> Result:
        self.mp.setattr(sys, "argv", ["rcm", *argv])
        try:
            code: Any = cli._main(None, follow_enabled=True, execve=execve or self.fake_execve)
        except Exec:
            code = "exec"
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
        cap = self.capsys.readouterr()
        return Result(code, cap.out, cap.err)

    @property
    def last_env(self) -> dict[str, str]:
        return self.execs[-1][2]


@pytest.fixture
def fx(monkeypatch, capsys, tmp_path):
    env = FollowEnv(monkeypatch, capsys, tmp_path)
    yield env
    for c in env.closers:
        c.close()


def fetched_line(version: str, wheel: bytes) -> str:
    return (
        f"rcm: following server v{version} (installed v{INSTALLED}) — fetched {_bytes(len(wheel))}"
    )


def not_following(srv: Any, reason: str, version: str | None = None) -> str:
    where = f"server v{version}" if version else srv.key
    return f"rcm: not following {where}: {reason} — running installed v{INSTALLED}"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 베스트 ───────────────────────────────────────────────────────────────────


def test_same_version_runs_installed_and_records_ten_minutes(fx):
    # A1 (F1 · 결정 153): 같은 버전이면 exec 없음, 둘째는 10분 기록으로 health 0번
    srv = fx.serve(INSTALLED)
    fx.use(srv)
    fx.run(["jobs"])
    fx.now = T + 9 * M
    fx.run(["jobs"])
    assert fx.execs == []
    assert srv.health_count == 1 and srv.client_count == 0
    doc = json.loads((fx.root / "follow.json").read_text())
    assert list(doc) == [srv.key]


def test_first_fetch_execs_into_the_cache(fx):
    # A2 (F2 · 결정 151 · 166): 받아서 풀고 BOOT 로 exec, stderr 는 한 줄
    srv = fx.serve(NEW)
    fx.use(srv)
    r = fx.run(["jobs", "--json"])
    assert r.code == "exec"
    assert srv.client_count == 1
    path, args, env = fx.execs[0]
    assert path == sys.executable
    assert args == [sys.executable, "-P", "-c", follow.BOOT, "jobs", "--json"]
    folder = fx.clients / follow.parse_health(srv.key, srv.health, now=T).sha256[:16]
    assert env["RCM_FOLLOWED"] == NEW
    assert Path(env["RCM_FOLLOWED_PATH"]) == folder
    assert env["RCM_FOLLOWED_FROM"] == INSTALLED
    assert env["RCM_FOLLOW_FROM"] == "wheel"
    marker = json.loads((folder / ".rcm-client.json").read_text())
    assert marker["version"] == NEW and marker["sha256"] == srv.health["client_wheel"]["sha256"]
    assert r.err_lines == [fetched_line(NEW, srv.wheel)]
    assert r.out == ""


def test_next_run_execs_from_the_cache_silently(fx):
    # A3 (F3): 10분 안의 둘째 실행은 요청 0 · 같은 폴더 · stderr 빔
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.run(["jobs"])
    fx.now = T + M
    r = fx.run(["jobs"])
    assert r.code == "exec" and r.err == ""
    assert srv.health_count == 1 and srv.client_count == 1
    assert fx.execs[0][2]["RCM_FOLLOWED_PATH"] == fx.execs[1][2]["RCM_FOLLOWED_PATH"]


def test_follows_down_to_an_older_server(fx):
    # A4 (F4 · 결정 150): 크기를 비교하지 않는다 — 내려가도 따라간다
    srv = fx.serve(OLD)
    fx.use(srv)
    assert fx.run(["jobs"]).code == "exec"
    assert fx.last_env["RCM_FOLLOWED"] == OLD


def test_pin_installed_stays_without_asking(fx):
    # A6 (가) · A93: 고정 버전이 설치본이면 서버에 묻지 않는다(서버가 꺼져 있어도 경고 없음)
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", INSTALLED)
    r = fx.run(["jobs"])
    assert fx.execs == [] and srv.health_count == 0 and "not following" not in r.err
    fx.mp.setenv("RCM_SERVER", f"http://127.0.0.1:{closed_port()}")
    r = fx.run(["jobs"])
    assert fx.execs == [] and "not following" not in r.err


def test_pin_uses_the_cache_without_asking(fx):
    # A6 (나): 캐시에 고정 버전이 있으면 서버 health 0번
    old = fx.serve(OLD)
    entry = fx.cache(old)
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", OLD)
    assert fx.run(["jobs"]).code == "exec"
    assert Path(fx.last_env["RCM_FOLLOWED_PATH"]) == entry.path
    assert srv.health_count == 0


def test_pin_fetches_when_the_server_runs_it(fx):
    # A6 (다)
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", NEW)
    assert fx.run(["jobs"]).code == "exec"
    assert fx.last_env["RCM_FOLLOWED"] == NEW and srv.client_count == 1


def test_pin_unavailable_runs_installed_with_one_warning(fx):
    # A6 (라): 설치본도 캐시도 서버도 아니다
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", NEW2)
    r = fx.run(["jobs"])
    assert fx.execs == [] and srv.client_count == 0
    reason = f"v{NEW2} is not installed or cached and the server runs v{NEW}"
    assert [ln for ln in r.err_lines if "not following" in ln] == [not_following(srv, reason, NEW)]


def test_pin_unavailable_when_the_server_is_down(fx):
    # A116 (§14.5 A질문8): 서버를 볼 수 없으면 주소 꼴의 pinned_unavailable
    port = closed_port()
    fx.mp.setenv("RCM_SERVER", f"http://127.0.0.1:{port}")
    fx.env("RCM_CLIENT_VERSION", NEW2)
    r = fx.run(["jobs"])
    assert fx.execs == []
    reason = f"v{NEW2} is not installed or cached and the server cannot be reached"
    line = f"rcm: not following http://127.0.0.1:{port}: {reason} — running installed v{INSTALLED}"
    assert r.err_lines[0] == line


def test_pin_picks_the_most_recently_used_of_one_version(fx):
    # A33 (§2.7 순서 2)
    a = fx.serve(OLD, fake_wheel(OLD))
    b = fx.serve(OLD, fake_wheel(OLD, requires_python=None))
    ea, eb = fx.cache(a), fx.cache(b)
    os.utime(ea.path / ".rcm-client.json", (T, T))
    os.utime(eb.path / ".rcm-client.json", (T + M, T + M))
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", OLD)
    assert fx.run(["jobs"]).code == "exec"
    assert Path(fx.last_env["RCM_FOLLOWED_PATH"]) == eb.path
    assert srv.health_count == 0


@pytest.mark.parametrize("argv", [["--no-follow", "jobs"], ["jobs", "--no-follow"]])
def test_no_follow_in_both_places(fx, argv):
    # A7 (F7 · 결정 154): 두 자리 모두 health 0 · exec 없음 · 설치본 파서가 받아 종료 0
    srv = fx.real(NEW, fake_wheel(NEW))
    fx.use(srv)
    r = fx.run(argv)
    assert r.code == 0, r.err
    assert fx.execs == [] and srv.health_count == 0


def test_env_installed_never_asks(fx):
    # A8 (F8)
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", "installed")
    fx.run(["jobs"])
    assert srv.health_count == 0 and fx.execs == []


def test_env_beats_the_pin_file(fx):
    # A9 (F9 · 결정 154)
    srv = fx.serve(NEW)
    fx.use(srv)
    write_pin(fx.config.parent, "installed\n")
    fx.env("RCM_CLIENT_VERSION", "follow")
    assert fx.run(["jobs"]).code == "exec"
    write_pin(fx.config.parent, "follow\n")
    fx.env("RCM_CLIENT_VERSION", "installed")
    before = srv.health_count
    fx.run(["jobs"])
    assert srv.health_count == before and len(fx.execs) == 1


def test_editable_default_is_off_and_quiet(fx):
    # A10 (F10 · 결정 156 · §14.8 A질문3): editable 기본은 안 따라가고 `rcm version` 은 한 줄
    fx.kind("editable")
    srv = fx.serve(NEW)
    fx.use(srv)
    r = fx.run(["jobs"])
    assert srv.health_count == 0 and fx.execs == []
    assert "follow" not in r.err
    fx.record(srv, checked_at=T - M)  # 기록이 있어도 기본 설정이면 한 줄
    r = fx.run(["version"])
    assert r.code == 0 and r.out.count("\n") == 1, r.out


def test_editable_follows_when_told(fx):
    # A11 (F11)
    fx.kind("editable")
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", "follow")
    assert fx.run(["jobs"]).code == "exec"


def test_version_is_one_line_without_information(fx):
    # A13 (F44 · 결정 166): 기록 없음 · 기본 설정이면 한 줄, `--version` 도 한 줄
    r = fx.run(["version"])
    assert r.code == 0 and r.err == ""
    assert r.out.count("\n") == 1 and r.out.startswith(f"rcm {INSTALLED} (Python ")
    r = fx.run(["--version"])
    assert r.code == 0 and r.out.strip() == f"rcm {INSTALLED}"


def test_version_second_line_and_json_follow(fx):
    # A14 · A45 (F45 · §2.9 · §14.8 C질문9): 진짜 생산자가 만든 기록과 캐시를 읽는다
    srv = fx.serve(NEW)
    mine = fx.serve(INSTALLED)
    e_new, e_mine = fx.cache(srv), fx.cache(mine)
    os.utime(e_new.path / ".rcm-client.json", (T - 60, T - 60))
    os.utime(e_mine.path / ".rcm-client.json", (T - 3600, T - 3600))
    fx.record(srv, checked_at=T - 3 * M)
    fx.use(srv)
    before = (srv.health_count, srv.client_count)
    r = fx.run(["version"])
    assert r.code == 0 and r.err == ""
    lines = r.out.splitlines()
    assert lines[0].startswith(f"rcm {INSTALLED} (Python ")
    assert lines[1] == (
        f"follow: on (default) · server {srv.key} v{NEW} checked 3m ago · cached {NEW}, {INSTALLED}"
    )
    r = fx.run(["version", "--json"])
    doc = json.loads(r.out)
    assert {"version", "python", "platform", "machine", "schema_version"} <= set(doc)
    assert doc["follow"] == {
        "mode": "follow",
        "pin": None,
        "source": "default",
        "install": "wheel",
        "server": srv.key,
        "server_version": NEW,
        "checked_at": iso(T - 3 * M),
        "cached": [NEW, INSTALLED],
        "installed": INSTALLED,
        "running": NEW,
    }
    assert "server" not in {k for k in doc if k != "follow"}
    assert (srv.health_count, srv.client_count) == before  # 서버에 묻지 않는다


def test_version_json_has_follow_even_without_information(fx):
    # A13 · §14.8 C질문9: `follow` 는 언제나 있고 모르는 값은 null
    r = fx.run(["version", "--json"])
    f = json.loads(r.out)["follow"]
    assert f["mode"] == "follow" and f["source"] == "default" and f["install"] == "wheel"
    assert f["server_version"] is None and f["checked_at"] is None and f["cached"] == []
    assert f["installed"] == INSTALLED and f["running"] == INSTALLED


@pytest.mark.parametrize(
    "where, expect",
    [
        ("env", "follow: pinned 0.0.9 (RCM_CLIENT_VERSION) · cached 0.0.9"),
        ("file", "follow: pinned 0.0.9 ({path}) · cached 0.0.9"),
    ],
)
def test_version_second_line_for_a_pin(fx, where, expect):
    # §14.8 C질문8: 고정은 출처와 캐시를 말한다
    fx.cache(fx.serve(OLD))
    path = ""
    if where == "env":
        fx.env("RCM_CLIENT_VERSION", OLD)
    else:
        path = str(write_pin(fx.config.parent, f"{OLD}\n"))
    r = fx.run(["version"])
    assert r.out.splitlines()[1] == expect.format(path=path)


@pytest.mark.parametrize(
    "setup, expect",
    [
        ("env-installed", "follow: off (RCM_CLIENT_VERSION)"),
        (
            "env-bad",
            "follow: invalid RCM_CLIENT_VERSION 'lates' (use follow, installed or a version)",
        ),
    ],
)
def test_version_second_line_off_and_invalid(fx, setup, expect):
    # §14.8 C질문8 · A112 (C질문10): 틀린 설정이어도 `rcm version` 은 종료 0
    fx.env("RCM_CLIENT_VERSION", "installed" if setup == "env-installed" else "lates")
    r = fx.run(["version"])
    assert r.code == 0
    assert r.out.splitlines()[1] == expect


def test_version_pin_file_under_home_is_shortened(fx):
    # §14.8: 경로는 홈을 `~` 로 줄여 쓴다
    fx.mp.delenv("XDG_CONFIG_HOME")
    write_pin(fx.home / ".config", "installed\n")
    r = fx.run(["version"])
    assert r.out.splitlines()[1] == "follow: off (~/.config/rcm/client-version)"


def test_version_cached_lists_each_version_once_newest_first(fx):
    # A139 (§14.8 C질문8)
    a = fx.serve(NEW, fake_wheel(NEW))
    b = fx.serve(NEW, fake_wheel(NEW, requires_python=None))
    o = fx.serve(OLD)
    ea, eb, eo = fx.cache(a), fx.cache(b), fx.cache(o)
    for e, t in ((ea, T), (eo, T - 60), (eb, T - 120)):
        os.utime(e.path / ".rcm-client.json", (t, t))
    fx.record(a, checked_at=T - 10)
    fx.use(a)
    doc = json.loads(fx.run(["version", "--json"]).out)
    assert doc["follow"]["cached"] == [NEW, OLD]


def test_version_with_auto_server_does_not_discover(fx, monkeypatch):
    # A140 (§14.8): 기본 서버가 auto 면 발견하지 않고 서버 토막이 없다 — 기본 설정이면 한 줄
    calls: list[Any] = []
    monkeypatch.setattr(cli, "discover", lambda **kw: calls.append(kw) or [])
    srv = fx.serve(NEW)
    fx.record(srv, checked_at=T - 10)
    fx.client_toml('server = "auto"\n')
    r = fx.run(["version"])
    assert calls == [] and r.out.count("\n") == 1


# ── rcm check 의 client 행 ───────────────────────────────────────────────────


def test_check_tail_in_a_followed_child(fx, monkeypatch):
    # A15 (가) · F46: 따라간 자식은 `following (installed vX)`
    srv = fx.serve(NEW)
    fx.use(srv)
    boot = {
        "RCM_FOLLOWED": NEW,
        "RCM_FOLLOWED_PATH": str(fx.tmp / "x"),
        "RCM_FOLLOWED_FROM": INSTALLED,
        "RCM_FOLLOW_FROM": "wheel",
        "RCM_FOLLOW_FOUND": None,
        "loaded": True,
    }
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", boot, raising=False)
    monkeypatch.setattr(cli, "__version__", NEW)
    monkeypatch.setattr(remote_ci_monitor, "__version__", NEW)
    r = fx.run(["check"])
    assert fx.execs == []
    assert r.row("client") == (
        f"ok   client        v{NEW} · same as server · following (installed v{INSTALLED})"
    )


def test_check_tail_when_following_failed(fx):
    # A15 (나) · CG21: 경고 한 줄과 꼬리가 같은 이유를 쓴다
    wheel = fake_wheel(NEW)
    doc = health_doc(NEW, wheel)
    doc["client_wheel"] = {**doc["client_wheel"], "sha256": SHA_A}
    srv = fx.serve(NEW, wheel, health=doc)
    fx.use(srv)
    r = fx.run(["check"])
    hint = f"pip install {srv.url}/client/remote_ci_monitor-{NEW}-py3-none-any.whl"
    assert r.row("client") == (
        f"warn  client        v{INSTALLED} · server v{NEW} · older · "
        f"not following: sha256 mismatch — {hint}"
    )
    assert [ln for ln in r.err_lines if "not following" in ln] == [
        not_following(srv, "sha256 mismatch", NEW)
    ]


def test_check_tail_when_pinned(fx):
    # A15 (다): 고정이면 pip 힌트를 뺀다(§14.8)
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", INSTALLED)
    r = fx.run(["check"])
    assert r.row("client") == (
        f"warn  client        v{INSTALLED} · server v{NEW} · older · pinned by RCM_CLIENT_VERSION"
    )


def test_check_tail_for_editable(fx):
    # A15 (라)
    fx.kind("editable")
    srv = fx.serve(INSTALLED)
    fx.use(srv)
    r = fx.run(["check"])
    assert r.row("client") == (
        f"ok   client        v{INSTALLED} · same as server · not following (editable install)"
    )


@pytest.mark.parametrize(
    "setup, tail",
    [
        ("flag", "not following (--no-follow)"),
        ("env", "not following (RCM_CLIENT_VERSION=installed)"),
        ("source", "not following (source install)"),
    ],
)
def test_check_tail_other_offs(fx, setup, tail):
    # §14.8 A질문20 · CG15
    srv = fx.serve(INSTALLED)
    fx.use(srv)
    argv = ["check"]
    if setup == "flag":
        argv = ["check", "--no-follow"]
    elif setup == "env":
        fx.env("RCM_CLIENT_VERSION", "installed")
    else:
        fx.kind("source")
    r = fx.run(argv)
    assert r.row("client") == f"ok   client        v{INSTALLED} · same as server · {tail}"


def test_check_tail_no_wheel_drops_the_hint(fx):
    # CE9 (§14.8): no_wheel 은 이유를 한 번만 말한다
    srv = fx.serve(NEW, health=health_doc(NEW, None, client_wheel_error="metadata_missing"))
    fx.use(srv)
    r = fx.run(["check"])
    assert r.row("client") == (
        f"warn  client        v{INSTALLED} · server v{NEW} · older · "
        "not following: the server has no client wheel (metadata_missing)"
    )


def test_check_reads_health_fresh(fx):
    # A16 (F47 · §2.3): 10분 기록이 있어도 check 는 새로 읽어 따라간다
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.record(srv, checked_at=T - M, health=health_doc(INSTALLED, fake_wheel(INSTALLED)))
    fx.run(["jobs"])  # 보통 명령은 기록(INSTALLED)을 믿고 그대로 돈다
    assert srv.health_count == 0 and fx.execs == []
    assert fx.run(["check"]).code == "exec"
    assert srv.health_count == 1 and fx.last_env["RCM_FOLLOWED"] == NEW
    doc = json.loads((fx.root / "follow.json").read_text())
    assert doc[srv.key]["checked_at"] == T and doc[srv.key]["version"] == NEW


def test_check_asks_twice_and_ignores_the_failure_memory(fx):
    # A129 (§14.4 A질문13): 따라가기의 새 읽기 + check 자신의 health. fresh 는 실패 기억도 무시
    srv = fx.serve(INSTALLED)
    fx.use(srv)
    follow.save_record(fx.root, None, server=srv.key, failed_at=T - 10)
    fx.run(["check"])
    assert srv.health_count == 2 and fx.execs == []


# ── exec 의 모양 ─────────────────────────────────────────────────────────────


class _Out:
    """stdout 흉내 — exec 순간에 비워졌는지 본다."""

    def __init__(self):
        self.pending = ""
        self.flushed = ""

    def write(self, s: str) -> int:
        self.pending += s
        return len(s)

    def flush(self) -> None:
        self.flushed += self.pending
        self.pending = ""

    def isatty(self) -> bool:
        return False


def test_exec_flushes_stdout_and_touches_the_marker(fx, monkeypatch):
    # A28 · A83 (F49 · 결정 163): exec 직전에 stdout 을 비우고 표지 mtime 을 지금으로
    srv = fx.serve(NEW)
    entry = fx.cache(srv)
    marker = entry.path / ".rcm-client.json"
    os.utime(marker, (T - 30 * 86400, T - 30 * 86400))
    fx.use(srv)
    out = _Out()
    seen: dict[str, Any] = {}

    def execve(path, args, env):
        seen["pending"] = out.pending
        seen["flushed"] = out.flushed
        seen["mtime"] = marker.stat().st_mtime
        seen["env"] = dict(env)
        raise Exec()

    monkeypatch.setattr(sys, "stdout", out)
    out.write("x")
    fx.run(["jobs"], execve=execve)
    assert seen["pending"] == "" and seen["flushed"].startswith("x")
    stamp = fx.tmp / "stamp"
    stamp.write_text("")
    real_now = stamp.stat().st_mtime
    # 지금이 주입한 시계(T)든 실제 시계든 30일 전 값은 아니다
    assert abs(seen["mtime"] - T) < 5 or abs(seen["mtime"] - real_now) < 60
    env = seen["env"]
    for k, v in os.environ.items():
        if not k.startswith("RCM_FOLLOW"):
            assert env.get(k) == v, k  # 환경은 그대로 + 따라가기 변수


def test_stale_followed_variables_are_ignored_and_overwritten(fx):
    # A99: BOOT 를 거치지 않은 보통 실행의 남은 변수
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_FOLLOWED", "9.9.9")
    fx.env("RCM_FOLLOWED_PATH", "/nope")
    assert fx.run(["jobs"]).code == "exec"
    assert fx.last_env["RCM_FOLLOWED"] == NEW
    assert fx.last_env["RCM_FOLLOWED_PATH"] != "/nope"


def test_exec_failure_runs_installed(fx):
    # A109 (§14.5 A질문5): execve 가 OSError 면 exec_failed 경고 + 설치본
    srv = fx.serve(NEW)
    fx.use(srv)

    def execve(path, args, env):
        raise OSError(errno.ENOENT, "No such file or directory")

    r = fx.run(["jobs"], execve=execve)
    assert r.code not in ("exec", 0) and "Traceback" not in r.err
    reason = "cannot start the cached client: No such file or directory"
    assert not_following(srv, reason, NEW) in r.err_lines


def test_vanished_marker_runs_installed(fx, monkeypatch):
    # A110 (§14.5 A질문25): utime 이 FileNotFoundError 면 다시 받지 않고 설치본
    srv = fx.serve(NEW)
    fx.cache(srv)
    fx.use(srv)
    real_utime = os.utime

    def utime(path, *a, **k):
        if str(path).endswith(".rcm-client.json"):
            raise FileNotFoundError(errno.ENOENT, "No such file or directory")
        return real_utime(path, *a, **k)

    monkeypatch.setattr(follow.os, "utime", utime)
    r = fx.run(["jobs"])
    assert fx.execs == [] and "Traceback" not in r.err
    assert any("cannot start the cached client" in ln for ln in r.err_lines)
    assert srv.client_count == 1  # fx.cache 의 한 번뿐 — 다시 받지 않았다


# ── 파서 ─────────────────────────────────────────────────────────────────────


def test_no_follow_parser():
    # A32 (§2.2 · §5.2): 하위 파서의 SUPPRESS 가 최상위 값을 덮지 않는다
    p = cli.build_parser()
    assert p.parse_args(["--no-follow", "jobs"]).no_follow is True
    assert p.parse_args(["jobs", "--no-follow"]).no_follow is True
    assert p.parse_args(["jobs"]).no_follow is False
    assert p.parse_args(["--no-follow", "skills", "list"]).no_follow is True
    with pytest.raises(SystemExit):
        p.parse_args(["skills", "list", "--no-follow"])  # skills 에는 client_opts 가 없다


def test_no_follow_is_in_the_help(capsys):
    # CB21: 최상위와 하위 명령의 도움말에 `--no-follow`
    for argv in (["--help"], ["run", "--help"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(argv)
        assert "--no-follow" in capsys.readouterr().out


# ── 에러 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["lates", "../x", "1" + ".1" * 32, "99.0.1\n", "Follow"])
def test_invalid_env_is_exit_two_and_no_request(fx, value):
    # A34 · A102 (F13 · F48 · 결정 154): 틀린 값은 종료 2, 서버에 요청 0
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", value)
    r = fx.run(["jobs"])
    assert r.code == 2
    assert "RCM_CLIENT_VERSION '" in r.err and INVALID in r.err
    assert srv.requests == [] and fx.execs == []


def test_invalid_pin_file_names_its_path(fx):
    # A34: 파일이면 문장에 경로가 들어간다
    srv = fx.serve(NEW)
    fx.use(srv)
    path = write_pin(fx.config.parent, "lates\n")
    r = fx.run(["jobs"])
    assert r.code == 2
    assert f"{path}: 'lates' {INVALID}" in r.err
    assert srv.requests == []


@pytest.mark.parametrize("kind", ["dir", "binary", "unreadable"])
def test_unreadable_pin_file_is_exit_two(fx, kind):
    # A115 (§14.3 A질문10): 폴더 · 읽을 수 없음 · UTF-8 아님은 경로가 든 ConfigError
    if kind == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads anything")
    srv = fx.serve(NEW)
    fx.use(srv)
    path = fx.config / "client-version"
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "dir":
        path.mkdir()
    elif kind == "binary":
        path.write_bytes(b"\xff\xfe\x00bad")
    else:
        path.write_text("follow\n")
        path.chmod(0)
    try:
        r = fx.run(["jobs"])
    finally:
        if kind == "unreadable":
            path.chmod(0o600)
    assert r.code == 2
    assert f"{path}: cannot read" in r.err
    assert srv.requests == []


def test_no_follow_beats_an_invalid_env_with_a_warning(fx):
    # A112 (§14.3 A질문9)
    srv = fx.real(NEW, fake_wheel(NEW))
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", "lates")
    r = fx.run(["jobs", "--no-follow"])
    assert r.code == 0
    assert "rcm: ignoring invalid RCM_CLIENT_VERSION 'lates' (--no-follow)" in r.err_lines
    assert srv.health_count == 0


def test_a_valid_env_means_the_pin_file_is_not_read(fx):
    # A112: 환경 변수가 맞으면 틀린 한 줄 파일은 읽지도 않는다
    srv = fx.serve(NEW)
    fx.use(srv)
    write_pin(fx.config.parent, "lates\n")
    fx.env("RCM_CLIENT_VERSION", "installed")
    r = fx.run(["jobs"])
    assert INVALID not in r.err
    assert srv.health_count == 0


@pytest.mark.parametrize(
    "argv, patch",
    [
        (["init", "client", "--server", "http://h:1", "--path", "{tmp}/c.toml"], None),
        (["discover"], "discover"),
        (["token", "list"], "cmd_token"),
        (["jobs", "--help"], None),
        (["serve"], "cmd_serve"),
        (["skills", "list"], None),
        (["gc", "--config", "{tmp}/s.toml", "--dry-run"], "cmd_gc"),
    ],
)
def test_commands_that_do_not_follow_ignore_a_bad_setting(fx, monkeypatch, argv, patch):
    # A58 · A67 · A68 (F36 · F37 · §14.3 A질문9): 설정을 읽지 않고 따라가기 코드도 안 부른다
    srv = fx.serve(NEW)
    fx.use(srv)
    called: list[Any] = []

    def boom(*a, **k):
        called.append(a)
        raise AssertionError("maybe_follow must not run")

    monkeypatch.setattr(follow, "maybe_follow", boom)
    if patch == "discover":
        monkeypatch.setattr(cli, "discover", lambda **kw: [])
    elif patch:
        monkeypatch.setattr(cli, patch, lambda args: 0)
    fx.env("RCM_CLIENT_VERSION", "lates")
    r = fx.run([a.replace("{tmp}", str(fx.tmp)) for a in argv])
    assert INVALID not in r.err
    assert called == [] and srv.health_count == 0 and fx.execs == []
    fx.env("RCM_CLIENT_VERSION", "follow")
    fx.run([a.replace("{tmp}", str(fx.tmp)) for a in argv])
    assert called == [] and srv.health_count == 0


def test_version_reads_the_setting_but_never_follows(fx, monkeypatch):
    # A67: `rcm version` 은 서버에 묻지 않는다
    srv = fx.serve(NEW)
    fx.use(srv)
    fx.env("RCM_CLIENT_VERSION", "follow")
    monkeypatch.setattr(follow, "maybe_follow", lambda *a, **k: pytest.fail("followed"))
    assert fx.run(["version"]).code == 0
    assert srv.health_count == 0


def test_broken_client_config_is_said_once_by_the_command(fx):
    # A113 (§2.1 의 4): 따라가기는 조용히 건너뛰고 명령이 오늘의 문장을 한 번
    srv = fx.serve(NEW)
    fx.use(srv)
    r = fx.run(["jobs", "--client-config", str(fx.tmp / "missing.toml")])
    assert r.code == 2 and r.err.count("client config not found") == 1
    assert srv.health_count == 0 and "not following" not in r.err
    bad = fx.client_toml('server = "x"\nnope = "y"\n')
    r = fx.run(["jobs", "--client-config", str(bad)])
    assert r.code == 2 and r.err.count("unknown key") == 1


@pytest.mark.parametrize(
    "server", ["ftp://127.0.0.1:1", "http://127.0.0.1:1 x", "--no-follow", "http://127.0.0.1:1?x=1"]
)
def test_unusable_server_address_is_skipped_quietly(fx, server):
    # A114 (§14.4 A질문11): 쓸 수 없는 주소는 경고 없이 건너뛴다
    r = fx.run(["jobs", "--server", server])
    assert fx.execs == [] and "not following" not in r.err and "Traceback" not in r.err


def test_server_down_warns_once_with_the_address(fx):
    # A35 (F14 · §14.5 A질문8): 첫 줄이 따라가기 경고, 그다음 명령 자신의 연결 실패. 종료 3
    port = closed_port()
    fx.mp.setenv("RCM_SERVER", f"http://127.0.0.1:{port}")
    r = fx.run(["jobs"])
    assert r.err_lines[0] == (
        f"rcm: not following http://127.0.0.1:{port}: cannot reach the server "
        f"— running installed v{INSTALLED}"
    )
    assert sum("not following" in ln for ln in r.err_lines) == 1
    assert any("cannot reach" in ln for ln in r.err_lines[1:])
    assert r.code == 3 and r.out == ""


def test_health_500_without_body_is_unreachable(fx):
    # A38 (F17)
    srv = fx.serve(health=b"", health_status=500)
    fx.use(srv)
    r = fx.run(["jobs"])
    assert not_following(srv, "cannot reach the server") in r.err_lines


def test_health_503_with_body_follows(fx):
    # A37 (F16)
    srv = fx.serve(NEW, health_status=503)
    fx.use(srv)
    assert fx.run(["jobs"]).code == "exec"


def test_no_wheel_warns_or_uses_the_same_version_cache(fx):
    # A39 (F18 · §14.5 A질문19): 캐시에 같은 버전이 있으면 경고 없이 그것으로
    srv = fx.serve(NEW, health=health_doc(NEW, None, client_wheel_error="metadata_missing"))
    fx.use(srv)
    r = fx.run(["jobs"])
    reason = "the server has no client wheel (metadata_missing)"
    assert fx.execs == [] and not_following(srv, reason, NEW) in r.err_lines
    entry = fx.cache(fx.serve(NEW))
    fx.now = T + 11 * M
    r = fx.run(["jobs"])
    assert r.code == "exec" and "not following" not in r.err
    assert Path(fx.last_env["RCM_FOLLOWED_PATH"]) == entry.path


def test_old_server_without_the_key(fx):
    # A40 (F19)
    doc = health_doc(NEW, None)
    del doc["client_wheel"], doc["client_wheel_error"]
    srv = fx.serve(NEW, health=doc)
    fx.use(srv)
    r = fx.run(["jobs"])
    reason = "the server is too old to hand out its client"
    assert not_following(srv, reason, NEW) in r.err_lines


def _bad_version_health(fx):
    doc = health_doc(NEW, fake_wheel(NEW))
    doc["version"] = "../x"
    return fx.serve(NEW, health=doc), None, "the server's health has a malformed version"


def _sha(fx):
    wheel = fake_wheel(NEW)
    doc = health_doc(NEW, wheel)
    doc["client_wheel"] = {**doc["client_wheel"], "sha256": SHA_B}
    return fx.serve(NEW, wheel, health=doc), NEW, "sha256 mismatch"


def _python(fx):
    w = fake_wheel(NEW, requires_python=">=3.99")
    reason = f"needs Python >=3.99, this is {platform.python_version()}"
    return fx.serve(NEW, w), NEW, reason


def _mismatch(fx):
    return (
        fx.serve(NEW, fake_wheel(NEW, init_version=INSTALLED)),
        NEW,
        (f"the wheel says v{INSTALLED}"),
    )


def _unsafe(fx):
    fx.clients.mkdir(parents=True)
    fx.root.chmod(0o700)
    fx.clients.chmod(0o777)
    return fx.serve(NEW), NEW, f"{fx.clients} is writable by others"


def _platform(fx):
    # 서버에 묻기 전에 멈춘다 — 서버 버전을 모르니 주소 꼴이다(§5.5)
    fx.mp.setattr(follow, "_os_name", lambda: "nt")
    return fx.serve(NEW), None, "not supported on this platform"


def _no_executable(fx):
    fx.mp.setattr(sys, "executable", "")
    return fx.serve(NEW), None, "not supported on this platform"


def _client_404(fx):
    return fx.serve(NEW, client_status=404), NEW, "the server has no client wheel (HTTP 404)"


def _client_503(fx):
    return fx.serve(NEW, client_status=503), NEW, "the server has no client wheel (HTTP 503)"


def _client_403(fx):
    srv = fx.serve(NEW, client_status=403)
    fx.mp.setenv("RCM_TOKEN", "t")
    return srv, NEW, "the server refused the token for its client (HTTP 403)"


def _client_500(fx):
    return fx.serve(NEW, client_status=500), NEW, "cannot reach the server"


@pytest.mark.parametrize(
    "make",
    [
        _bad_version_health,
        _sha,
        _python,
        _mismatch,
        _unsafe,
        _platform,
        _no_executable,
        _client_404,
        _client_503,
        _client_403,
        _client_500,
    ],
)
def test_failure_lines(fx, make):
    # A56 · A46 · A48 · A50 · A69 · A104 (§2.6 · §14.5): 이유 문구 그대로, 한 줄, stdout 은 그대로
    srv, version, reason = make(fx)
    if not os.environ.get("RCM_SERVER"):
        fx.use(srv)
    r = fx.run(["jobs"])
    assert fx.execs == [] and "Traceback" not in r.err
    lines = [ln for ln in r.err_lines if "not following" in ln]
    assert lines == [not_following(srv, reason, version)]
    assert "not following" not in r.out
    if make in (_sha, _python, _mismatch):
        assert entries(fx.clients) == []  # 대상 폴더가 생기지 않는다


def test_basic_server_with_a_token_follows_and_without_says_so(fx):
    # A44 · A45 · A105 (F23 · F24 · §14.5 A질문6)
    srv = fx.real(NEW, fake_wheel(NEW), read_auth="basic")
    fx.use(srv)
    r = fx.run(["jobs"])
    assert fx.execs == []
    assert not_following(srv, "the server needs a token to hand out its client", NEW) in (
        r.err_lines
    )
    fx.mp.setenv("RCM_TOKEN", "wrong")
    r = fx.run(["jobs"])
    reason = "the server refused the token for its client (HTTP 401)"
    assert not_following(srv, reason, NEW) in r.err_lines
    fx.mp.setenv("RCM_TOKEN", srv.tokens["alice"])
    assert fx.run(["jobs"]).code == "exec"


def test_followed_process_does_not_follow_again(fx, monkeypatch):
    # A62 (F31 · 결정 161): `_follow_boot` 가 있으면 health 0 · exec 없음
    srv = fx.serve(NEW2)
    fx.use(srv)
    boot = {"RCM_FOLLOWED": NEW, "RCM_FOLLOWED_PATH": "/x", "loaded": True}
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", boot, raising=False)
    fx.run(["jobs"])
    assert srv.health_count == 0 and fx.execs == []


def test_argument_list_calls_never_follow(fx):
    # A70 (F39 · 결정 162)
    srv = fx.serve(NEW)
    fx.use(srv)
    cli.main(["jobs"])
    assert srv.health_count == 0


def test_negative_memory_sixty_seconds(fx):
    # A81 (R12 · 결정 153): 시도는 T 와 T+61 에만, 경고는 매번 한 줄
    srv = fx.serve(health=b"", health_status=500)
    fx.use(srv)
    seen = []
    for t in (T, T + 30, T + 59, T + 61):
        fx.now = t
        r = fx.run(["jobs"])
        seen.append(srv.health_count)
        assert sum("not following" in ln for ln in r.err_lines) == 1
    assert seen == [1, 1, 1, 2]


def test_ten_minute_and_sixty_second_boundaries(fx):
    # A86 (§2.4): 599 는 신선, 600 은 아니다. 실패 59 는 시도 0, 60 은 1
    srv = fx.serve(INSTALLED)
    fx.use(srv)
    fx.record(srv, checked_at=T)
    fx.now = T + 599
    fx.run(["jobs"])
    assert srv.health_count == 0
    fx.now = T + 600
    fx.run(["jobs"])
    assert srv.health_count == 1
    down = fx.serve(health=b"", health_status=500)
    fx.use(down)
    follow.save_record(fx.root, None, server=down.key, failed_at=T)
    fx.now = T + 59
    fx.run(["jobs"])
    assert down.health_count == 0
    fx.now = T + 60
    fx.run(["jobs"])
    assert down.health_count == 1


def test_future_record_is_not_fresh(fx):
    # A131 (§14.4 A질문21)
    srv = fx.serve(INSTALLED)
    fx.use(srv)
    fx.record(srv, checked_at=T + 3600)
    fx.run(["jobs"])
    assert srv.health_count == 1


def test_stale_record_and_cache_carry_a_server_restart(fx):
    # A36 (F15): 꺼진 서버 · 11분 된 기록 · sha 가 맞는 캐시로 경고 없이
    a = fx.serve(NEW, fake_wheel(NEW))
    b = fx.serve(NEW, fake_wheel(NEW, requires_python=None))
    ea = fx.cache(a)
    eb = fx.cache(b)
    os.utime(eb.path / ".rcm-client.json", (T + M, T + M))  # 더 최근이어도 sha 가 다르다
    fx.record(a, checked_at=T - 11 * M)
    key = a.key
    a.close()
    fx.closers.remove(a)
    fx.mp.setenv("RCM_SERVER", key)
    r = fx.run(["jobs"])
    assert r.code == "exec" and "not following" not in r.err
    assert Path(fx.last_env["RCM_FOLLOWED_PATH"]) == ea.path
    doc = json.loads((fx.root / "follow.json").read_text())
    assert doc[key]["version"] == NEW and doc[key]["failed_at"] == T  # A91


# ── 엣지 ─────────────────────────────────────────────────────────────────────


def _found(port: int) -> Found:
    return Found(name="mini", host="mini.local.", port=port, ips=("127.0.0.1",), version=NEW)


def test_discovered_server_is_found_once_and_passed_on(fx, monkeypatch):
    # A72 (F41 · 결정 170 · §14.4 A질문12)
    srv = fx.serve(NEW)
    calls: list[Any] = []
    monkeypatch.setattr(cli, "discover", lambda **kw: calls.append(kw) or [_found(srv.port)])
    fx.client_toml('server = "auto"\n')
    r = fx.run(["jobs"])
    assert r.code == "exec" and len(calls) == 1
    assert fx.last_env["RCM_SERVER"] == f"http://127.0.0.1:{srv.port}"
    assert fx.last_env["RCM_FOLLOW_FOUND"] == "1"
    assert r.err.count("server: found") == 1


@pytest.mark.parametrize("found", [True, False])
def test_discovery_runs_once_per_process(fx, monkeypatch, found):
    # A128 (§14.4 A질문12): exec 하지 않을 때도 발견은 한 번
    srv = fx.serve(INSTALLED)
    calls: list[Any] = []
    result = [_found(srv.port)] if found else []
    monkeypatch.setattr(cli, "discover", lambda **kw: calls.append(kw) or result)
    fx.client_toml('server = "auto"\n')
    r = fx.run(["jobs"])
    assert fx.execs == [] and len(calls) == 1
    assert r.err.count("server: found") == (1 if found else 0)
    if not found:
        assert r.code == 2 and "no rcm server found" in r.err


def test_followed_child_keeps_the_found_tag(fx, monkeypatch):
    # A127 (§14.4 A질문12): 자식의 `rcm check` 가 `(found on this network)` 를 그대로 쓴다
    srv = fx.serve(NEW)
    fx.client_toml('server = "auto"\n')
    fx.use(srv)
    boot = {
        "RCM_FOLLOWED": NEW,
        "RCM_FOLLOWED_PATH": "/x",
        "RCM_FOLLOWED_FROM": INSTALLED,
        "RCM_FOLLOW_FROM": "wheel",
        "RCM_FOLLOW_FOUND": "1",
        "loaded": True,
    }
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", boot, raising=False)
    monkeypatch.setattr(cli, "discover", lambda **kw: pytest.fail("child must not discover"))
    r = fx.run(["check"])
    assert "(found on this network)" in (r.row("server") or "")


def test_child_install_kind_comes_from_the_bootloader(fx, monkeypatch):
    # A126 (§14.7 A질문14): 따라간 자식의 install 은 RCM_FOLLOW_FROM
    boot = {
        "RCM_FOLLOWED": NEW,
        "RCM_FOLLOWED_PATH": "/x",
        "RCM_FOLLOWED_FROM": INSTALLED,
        "RCM_FOLLOW_FROM": "editable",
        "RCM_FOLLOW_FOUND": None,
        "loaded": True,
    }
    monkeypatch.setattr(remote_ci_monitor, "_follow_boot", boot, raising=False)
    monkeypatch.setattr(cli, "__version__", NEW)
    monkeypatch.setattr(remote_ci_monitor, "__version__", NEW)
    doc = json.loads(fx.run(["version", "--json"]).out)
    assert doc["follow"]["install"] == "editable"
    assert doc["follow"]["installed"] == INSTALLED


def test_json_stdout_stays_clean_when_following_fails(fx):
    # A73 (F42 · §2.6): `run --no-wait` 의 stdout 은 JSON 한 줄, 경고는 stderr
    wheel = fake_wheel(NEW)
    srv = fx.real(NEW, wheel)
    srv.app._wheel = (wheel, SHA_A)  # health 가 거짓 sha 를 말한다
    fx.use(srv, srv.tokens["alice"])
    (fx.cwd / "hello.txt").write_text("hi\n")
    r = fx.run(["run", "ok", "--no-wait"])
    assert r.code == 0, r.err
    lines = r.out.splitlines()
    assert len(lines) == 1 and isinstance(json.loads(lines[0]), dict)
    assert any("sha256 mismatch" in ln for ln in r.err_lines)


def test_unknown_command_and_flag_follow(fx):
    # A75 (R ① · 결정 160): 설치본 argparse 가 돌기 전에 exec
    srv = fx.serve(NEW)
    fx.use(srv)
    assert fx.run(["probe"]).code == "exec"
    assert fx.execs[-1][1][4:] == ["probe"]
    assert fx.run(["run", "gate", "--new-flag"]).code == "exec"
    assert fx.execs[-1][1][4:] == ["run", "gate", "--new-flag"]


def test_unknown_command_without_a_server_is_a_usage_error(fx, monkeypatch):
    # A75: 서버를 모르면 설치본 파서가 오늘처럼 끝낸다
    monkeypatch.setattr(cli, "discover", lambda **kw: [])
    r = fx.run(["probe"])
    assert r.code == 2 and "invalid choice" in r.err
    r = fx.run(["run", "gate", "--new-flag"])
    assert r.code == 2 and fx.execs == []


@pytest.mark.parametrize("argv", [["run", "--help"], ["-h"], ["--version"], ["jobs", "--he"]])
def test_help_and_version_never_follow(fx, argv):
    # A78 · A119 (R9 · §2.1 의 2): 설치본의 도움말과 버전
    srv = fx.serve(NEW)
    fx.use(srv)
    r = fx.run(argv)
    assert r.code == 0 and srv.health_count == 0 and fx.execs == []
    if argv == ["--version"]:
        assert r.out.strip() == f"rcm {INSTALLED}"
    else:
        assert "usage:" in r.out


def test_skills_never_follow(fx, monkeypatch):
    # A80 (R11 · §14.2 A질문23): 셋 다 health 0, 발견 없음, 경고 없음
    calls: list[Any] = []
    monkeypatch.setattr(cli, "discover", lambda **kw: calls.append(kw) or [])
    srv = fx.serve(NEW)
    fx.use(srv)
    r = fx.run(["skills", "list"])
    assert r.code == 0 and srv.health_count == 0 and fx.execs == []
    fx.mp.delenv("RCM_SERVER")
    fx.client_toml('server = "auto"\n')
    r = fx.run(["skills", "list"])
    assert r.code == 0 and calls == []
    fx.mp.setenv("RCM_SERVER", f"http://127.0.0.1:{closed_port()}")
    r = fx.run(["skills", "list"])
    assert r.code == 0 and "not following" not in r.err


def test_follow_uses_the_commands_server_and_token(fx):
    # A100 (§2.1 의 4): `--server` > RCM_SERVER > client.toml, 토큰은 token_env
    target = fx.serve(NEW)
    fx.client_toml(f'server = "http://127.0.0.1:{closed_port()}"\ntoken_env = "MY_TOKEN"\n')
    fx.mp.setenv("MY_TOKEN", "tok-from-env")
    fx.mp.setenv("RCM_SERVER", f"http://127.0.0.1:{closed_port()}")
    assert fx.run(["jobs", "--server", target.url]).code == "exec"
    health = [r for r in target.requests if r.path == "/api/health"]
    assert health and health[0].headers.get("Authorization") == "Bearer tok-from-env"
    wheel = [r for r in target.requests if r.path.startswith("/client/")]
    assert wheel and wheel[0].headers.get("Authorization") == "Bearer tok-from-env"


def test_abbreviated_options_match_argparse(fx, monkeypatch):
    # A119 (§14.2 A질문2): 훑기는 argparse 와 같은 유일 접두 규칙
    a = fx.serve(INSTALLED)
    b = fx.serve(NEW)
    fx.use(a)
    assert fx.run(["jobs", "--serv", b.url]).code == "exec"
    assert a.health_count == 0 and b.health_count == 1
    fx.now = T + 11 * M  # 10분 기록을 넘겨 health 를 다시 부르게 한다
    fx.run(["jobs", "--serv", b.url, "--tok", "sekrit"])
    assert b.requests[-1].headers.get("Authorization") == "Bearer sekrit"
    before = a.health_count
    fx.run(["jobs", "--no-f"])
    assert a.health_count == before
    monkeypatch.setattr(cli, "cmd_gc", lambda args: 0)
    fx.run(["gc", "--conf", str(fx.tmp / "s.toml"), "--dry-run"])
    assert a.health_count == before
    b_before = b.health_count
    r = fx.run(["jobs", "--s", "x"])  # --state · --server 둘 다 맞는다: 모호
    assert r.code == 2 and a.health_count == before and b.health_count == b_before


@pytest.mark.parametrize("argv", [[], ["--no-follow"]])
def test_no_command_never_follows(fx, argv):
    # A120 (§14.2 A질문23)
    srv = fx.serve(NEW)
    fx.use(srv)
    r = fx.run(argv)
    assert r.code == 2 and srv.health_count == 0 and fx.execs == []


def test_server_before_the_command_is_not_a_value(fx):
    # A121: 최상위에 둔 `--server` 는 오늘도 사용법 오류이고 그 주소에 묻지 않는다
    other = fx.serve(NEW)
    r = fx.run(["--server", other.url, "jobs"])
    assert r.code == 2 and other.health_count == 0 and fx.execs == []


def test_skills_with_client_flags(fx):
    # A122 (§14.2 A질문23)
    srv = fx.serve(NEW)
    assert fx.run(["skills", "list", "--server", srv.url]).code == 2
    assert fx.run(["skills", "--no-follow", "list"]).code == 2
    assert fx.run(["--no-follow", "skills", "list"]).code == 0
    assert srv.health_count == 0


def test_logs_follow_is_not_no_follow(fx):
    # A97: `rcm logs N --follow` 는 따라간다
    srv = fx.serve(NEW)
    fx.use(srv)
    assert fx.run(["logs", "412", "--follow"]).code == "exec"


def test_interrupt_while_following_is_130(fx, monkeypatch):
    # R ④ · A53 (결정 157): 받는 중 Ctrl-C 는 130, 임시 폴더 없음
    srv = fx.serve(NEW)
    fx.use(srv)

    def fetch(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(follow, "fetch", fetch)
    r = fx.run(["jobs"])
    assert r.code == 130 and "interrupted" in r.err
    assert fx.execs == []


def test_cache_broken_says_how_to_recover(fx):
    # 개선 4: `.bad` 폴더를 다시 고르지 않는다는 경고 뒤에 그 폴더를 지우는 법을 한 줄로
    srv = fx.serve(NEW)
    fx.use(srv)
    entry = fx.cache(srv)
    (entry.path / ".bad").touch()
    r = fx.run(["jobs"])
    assert fx.execs == []
    assert r.err_lines[:2] == [
        not_following(srv, f"the cached client v{NEW} did not load before", NEW),
        f"rcm: remove {entry.path} to fetch it again",
    ]


def test_check_cleans_the_servers_version_string(fx):
    # D3 (§14.5): 서버가 보낸 version 은 제어 문자를 지우고 64자로 자른 뒤에 찍는다
    nasty = "9.9.9\x1b[2J\nfake row" + "x" * 80
    srv = fx.serve(health={"ok": True, "version": nasty, "min_client_version": "0.2.0"})
    fx.use(srv)
    r = fx.run(["check"])
    assert "\x1b" not in r.out and "x" * 70 not in r.out
    server_row = r.row("server") or ""
    client_row = r.row("client") or ""
    assert "9.9.9[2Jfake row" in server_row, r.out
    assert "9.9.9[2Jfake row" in client_row, r.out
    assert not any(ln.startswith("fake row") for ln in r.out.splitlines())
