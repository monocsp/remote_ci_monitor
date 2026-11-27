"""워커의 서버 찾기와 `rcm worker --check` 의 새 행(M5n 역할 C · 명세 §8.2 · §9).

- `server` 가 비었거나 `"auto"` → mDNS(`cli.discover`). 하나면 그것, 여럿이면 `server_name` 과
  이름이 맞는 것, 그래도 모호하거나 없으면 이름들을 찍고 usage 2.
- 등록·heartbeat 가 **연속 5번** 연결 실패하면 다시 찾는다 — `auto` 일 때만(가정 C15:
  `RemoteWorker(..., rediscover=콜백)` · `remote_worker.REDISCOVER_AFTER_FAILURES = 5`).
- `rcm worker --check` 에 `fingerprint` · `presets` 행 — 서버의 `GET /worker/spec` 을 받아
  **그 자리에서** 잰다. 서버에 보고하지 않는다(등록도 capabilities 도 없다).

실제 LAN 은 없다. `cli.discover` 는 `test_cli_discover.FakeDiscover` 로 갈아 끼우고, 서버는
`fake_rcm_http.FakeRcm`(127.0.0.1 임시 포트)이다. **구현보다 먼저 썼다(test-first).**
"""

from __future__ import annotations

import signal
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, free_port
from remote_ci_monitor import __version__, cli
from remote_ci_monitor import remote_worker as rw
from remote_ci_monitor.client import ClientError
from remote_ci_monitor.config import WorkerConfig
from test_cli_discover import FakeDiscover, found

TOKEN = "wk-test-token"
NONE_FOUND = "no rcm server found on this network"


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def check_row(out: str, name: str) -> tuple[str, str] | None:
    import re

    m = re.search(rf"^(ok |FAIL|warn)  {re.escape(name)} +(.*)$", out, re.M)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


@pytest.fixture
def env(monkeypatch, tmp_path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    return tmp_path


@pytest.fixture
def srv() -> Iterator[FakeRcm]:
    s = FakeRcm()
    s.route("GET", "/api/health", {"ok": True, "version": __version__})
    s.route("GET", "/api/whoami", {"name": "mbp", "admin": False, "kind": "worker"})
    s.route(
        "GET",
        "/worker/spec",
        {"generation": "1:0", "probe_interval_seconds": 600, "probes": [], "presets": []},
    )
    s.route("POST", "/worker/register", {"heartbeat_seconds": 5, "name": "mbp"})
    yield s
    s.close()


@pytest.fixture
def fake_discover(monkeypatch):
    def use(*results) -> FakeDiscover:
        rec = FakeDiscover(results)
        monkeypatch.setattr(cli, "discover", rec)
        return rec

    return use


def config(env: Path, text: str) -> Path:
    p = env / "worker.toml"
    p.write_text(text)
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return p


def worker_check(capsys, env: Path, cfg: Path, *extra: str):
    return run(
        capsys, ["worker", "--check", "--config", str(cfg), "--data", str(env / "wd"), *extra]
    )


def at(srv: FakeRcm, name: str = "macmini"):
    return found(name, "127.0.0.1", srv.port)


@pytest.fixture
def confirmed(monkeypatch) -> list[str]:
    """터미널에서 사람이 「예」라고 답한다. M5t-a §4.7 · TD28 부터 mDNS 로 찾은 서버는 확인을 받아야
    `rcm worker --check` 가 그 서버에 묻는다 — 비TTY 는 종료 3 이다(tests/test_doctor_cli.py)."""
    from remote_ci_monitor import doctor

    asked: list[str] = []
    monkeypatch.setattr(doctor, "is_tty", lambda: True)
    monkeypatch.setattr(doctor, "ask", lambda q: asked.append(q) or True)
    return asked


# ═══ K. 서버 찾기 ════════════════════════════════════════════════════════════


def test_auto_with_one_discovered_server_uses_it(env, srv, fake_discover, confirmed, capsys):
    rec = fake_discover(at(srv))
    code, out, err = worker_check(capsys, env, config(env, 'server = "auto"\npool = "mbp"\n'))
    assert rec.called
    assert confirmed and "macmini" in confirmed[0], confirmed  # TD28 — 묻고 나서야 쓴다
    assert check_row(out, "server") == ("ok", f"{srv.url} · v{__version__}"), out + err
    assert code == 0, out + err


def test_an_empty_server_also_discovers(env, srv, fake_discover, confirmed, capsys):
    rec = fake_discover(at(srv))
    code, out, err = worker_check(capsys, env, config(env, 'server = ""\npool = "mbp"\n'))
    assert rec.called, err
    assert check_row(out, "server") == ("ok", f"{srv.url} · v{__version__}"), out + err


def test_several_servers_pick_the_one_named_by_server_name(
    env, srv, fake_discover, confirmed, capsys
):
    other = found("build-02", "127.0.0.1", free_port())
    fake_discover(other, at(srv, "macmini"))
    cfg = config(env, 'server = "auto"\nserver_name = "macmini"\npool = "mbp"\n')
    code, out, err = worker_check(capsys, env, cfg)
    assert check_row(out, "server") == ("ok", f"{srv.url} · v{__version__}"), out + err
    assert code == 0


@pytest.mark.parametrize("server_name", [None, "nope"])
def test_several_servers_without_a_match_is_a_usage_error_listing_them(
    env, fake_discover, capsys, server_name
):
    fake_discover(found("macmini", "10.0.0.2", 8787), found("build-02", "10.0.0.3", 8787))
    text = 'server = "auto"\npool = "mbp"\n'
    if server_name:
        text += f'server_name = "{server_name}"\n'
    code, out, err = worker_check(capsys, env, config(env, text))
    assert code == 2, out + err
    assert "macmini" in err and "build-02" in err, err
    assert "server_name" in err, err


def test_no_discovered_server_is_a_usage_error_with_the_network_hint(env, fake_discover, capsys):
    fake_discover()
    code, out, err = worker_check(capsys, env, config(env, 'server = "auto"\npool = "mbp"\n'))
    assert code == 2, out + err
    assert NONE_FOUND in err, err


def test_a_pinned_server_never_discovers(env, srv, fake_discover, capsys):
    rec = fake_discover(found("macmini", "10.0.0.2", 8787))
    cfg = config(env, f'server = "{srv.url}"\npool = "mbp"\n')
    code, out, err = worker_check(capsys, env, cfg)
    assert not rec.called
    assert check_row(out, "server") == ("ok", f"{srv.url} · v{__version__}"), out + err


def test_the_server_flag_beats_auto(env, srv, fake_discover, capsys):
    rec = fake_discover(found("macmini", "10.0.0.2", 8787))
    cfg = config(env, 'server = "auto"\npool = "mbp"\n')
    code, out, err = worker_check(capsys, env, cfg, "--server", srv.url)
    assert not rec.called
    assert check_row(out, "server") == ("ok", f"{srv.url} · v{__version__}"), out + err


# ── cmd_worker 배선 ──────────────────────────────────────────────────────────


class Recorder:
    """`RemoteWorker` 대역 — 생성 인자만 남기고 바로 끝난다."""

    made: list[tuple[Any, dict[str, Any]]] = []

    def __init__(self, cfg, **kw):
        Recorder.made.append((cfg, kw))
        self.stopping = None
        self.restart_for = None  # M5q B2 — 드레인이 남기는 재실행 목표
        self.signalled = False

    def stop(self) -> None:
        return None

    def run(self) -> int:
        return 0


@pytest.fixture
def recorder(monkeypatch) -> Iterator[type[Recorder]]:
    Recorder.made = []
    monkeypatch.setattr(rw, "RemoteWorker", Recorder)
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    yield Recorder
    for s, h in saved.items():
        signal.signal(s, h)


def test_cmd_worker_gives_an_auto_worker_a_rediscover_callback(
    env, srv, fake_discover, recorder, capsys
):
    fake_discover(at(srv))
    cfg = config(env, 'server = "auto"\npool = "mbp"\n')
    code, _, err = run(capsys, ["worker", "--config", str(cfg), "--data", str(env / "wd")])
    assert code == 0, err
    (made_cfg, kw) = recorder.made[0]
    assert made_cfg.server == srv.url
    again = kw.get("rediscover")
    assert callable(again), kw
    fake_discover(found("macmini", "10.0.0.9", 8790))  # DHCP 로 주소가 바뀌었다
    assert again() == "http://10.0.0.9:8790"
    fake_discover()
    assert again() is None


def test_cmd_worker_rediscover_honours_server_name(env, srv, fake_discover, recorder, capsys):
    fake_discover(at(srv, "macmini"))
    cfg = config(env, 'server = "auto"\nserver_name = "macmini"\npool = "mbp"\n')
    run(capsys, ["worker", "--config", str(cfg), "--data", str(env / "wd")])
    again = recorder.made[0][1]["rediscover"]
    fake_discover(found("build-02", "10.0.0.3", 8787), found("macmini", "10.0.0.9", 8787))
    assert again() == "http://10.0.0.9:8787"


def test_cmd_worker_gives_a_pinned_worker_no_rediscover(env, srv, fake_discover, recorder, capsys):
    rec = fake_discover(at(srv))
    cfg = config(env, f'server = "{srv.url}"\npool = "mbp"\n')
    code, _, err = run(capsys, ["worker", "--config", str(cfg), "--data", str(env / "wd")])
    assert code == 0, err
    assert recorder.made[0][1].get("rediscover") is None
    assert not rec.called


# ═══ L. 연속 5번 실패하면 다시 찾는다 ════════════════════════════════════════


class Flaky:
    """`WorkerClient` 대역 — 처음 `fail` 번은 연결 실패(status 0), 그 뒤엔 성공."""

    def __init__(self, server: str, fail: int):
        self.server = server
        self.token = TOKEN
        self.fail = fail
        self.calls = 0

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.calls <= self.fail:
            raise ClientError(0, f"cannot reach {self.server}: [Errno 61] Connection refused")

    def register(self, **kw) -> dict[str, Any]:
        self._maybe_fail()
        return {"heartbeat_seconds": 5, "name": "mbp"}

    def heartbeat(self, jobs, host_sample=None, **_kw) -> dict[str, Any]:  # M5t-b: seq
        self._maybe_fail()
        return {}


def make_worker(client: Flaky, **kw) -> rw.RemoteWorker:
    cfg = WorkerConfig(server="auto", token=TOKEN, pool="mbp", lanes=1)
    return rw.RemoteWorker(cfg, client=client, log=lambda _m: None, **kw)


@pytest.fixture
def fast(monkeypatch) -> None:
    monkeypatch.setattr(rw, "REGISTER_RETRY_SECONDS", 0.0)


def test_the_threshold_is_five():
    assert rw.REDISCOVER_AFTER_FAILURES == 5


def test_register_rediscovers_after_five_connection_failures(fast, srv):
    calls: list[int] = []
    old = Flaky("http://10.0.0.2:8787", fail=10**6)

    def rediscover() -> str | None:
        calls.append(old.calls)
        return srv.url

    w = make_worker(old, rediscover=rediscover)
    out = w.register()
    assert calls == [5], calls  # 다섯 번째 실패 직후 정확히 한 번
    assert old.calls == 5
    assert w.client.server == srv.url
    assert srv.calls("POST", "/worker/register"), "registered with the rediscovered server"
    assert out.get("name") == "mbp"


def test_register_keeps_retrying_a_pinned_server(fast):
    client = Flaky("http://10.0.0.2:8787", fail=7)
    w = make_worker(client)
    w.register()
    assert client.calls == 8 and w.client is client


def test_register_does_not_rediscover_before_five_failures(fast):
    calls: list[str] = []
    client = Flaky("http://10.0.0.2:8787", fail=4)
    w = make_worker(client, rediscover=lambda: calls.append("x") or None)
    w.register()
    assert calls == [] and w.client is client


def test_register_keeps_the_client_when_rediscovery_finds_nothing_and_tries_again_later(fast):
    calls: list[int] = []
    client = Flaky("http://10.0.0.2:8787", fail=11)

    def rediscover() -> None:
        calls.append(client.calls)
        return None

    w = make_worker(client, rediscover=rediscover)
    w.register()
    assert calls == [5, 10], calls  # 매 실패마다가 아니라 다섯 번마다
    assert w.client is client


def test_register_keeps_the_client_when_rediscovery_returns_the_same_address(fast):
    client = Flaky("http://10.0.0.2:8787", fail=6)
    w = make_worker(client, rediscover=lambda: "http://10.0.0.2:8787")
    w.register()
    assert w.client is client


def test_a_refusal_is_not_a_connection_failure(fast):
    """403(토큰) 은 주소 문제가 아니다 — 다시 찾지 않고 오늘처럼 종료 2."""
    calls: list[str] = []

    class Refused(Flaky):
        def register(self, **kw):
            raise ClientError(403, "worker token required")

    w = make_worker(Refused("http://10.0.0.2:8787", 0), rediscover=lambda: calls.append("x"))
    with pytest.raises(rw.WorkerExit) as ei:
        w.register()
    assert ei.value.code == 2 and calls == []


def test_heartbeat_rediscovers_after_five_consecutive_failures(srv):
    calls: list[int] = []
    old = Flaky("http://10.0.0.2:8787", fail=10**6)
    w = make_worker(old, rediscover=lambda: calls.append(old.calls) or srv.url)
    results = [w.heartbeat_once() for _ in range(5)]
    assert results == [False] * 5
    assert calls == [5]
    assert w.client.server == srv.url


def test_heartbeat_failures_must_be_consecutive():
    calls: list[str] = []
    client = Flaky("http://10.0.0.2:8787", fail=4)
    w = make_worker(client, rediscover=lambda: calls.append("x") or None)
    for _ in range(5):
        w.heartbeat_once()  # 4 실패 + 1 성공 → 카운터가 0 으로
    client.fail, client.calls = 4, 0
    for _ in range(4):
        w.heartbeat_once()
    assert calls == []


def test_a_pinned_worker_heartbeat_never_rediscovers():
    client = Flaky("http://10.0.0.2:8787", fail=10**6)
    w = make_worker(client)
    for _ in range(12):
        w.heartbeat_once()
    assert w.client is client


# ═══ M. rcm worker --check 의 fingerprint · presets 행 ══════════════════════


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def toolbox(env, monkeypatch) -> Path:
    """tmp/bin 에 가짜 도구 하나와 버전 찍는 스크립트. PATH 는 절대 항목만."""
    bin_dir = env / "bin"
    bin_dir.mkdir()
    script(bin_dir / "rcm-m5n-fake-tool", "exit 0")
    script(bin_dir / "fake-version", "echo 'fake-tool 1.2.3'; echo 'second line'")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return bin_dir


def spec(bin_dir: Path) -> dict[str, Any]:
    return {
        "generation": "1731740000:3",
        "probe_interval_seconds": 600,
        "probes": [
            {"name": "fake", "argv": [str(bin_dir / "fake-version")], "timeout_seconds": 10},
            {"name": "gone", "argv": ["/nonexistent/rcm-m5n-gone"], "timeout_seconds": 10},
        ],
        "presets": [
            # 잡의 PATH 는 env_passthrough 로만 들어온다(runner.build_env) — 넘기지 않으면 없다
            {
                "name": "have",
                "requires": ["rcm-m5n-fake-tool"],
                "env_passthrough": ["PATH"],
                "env": {},
            },
            {
                "name": "lack",
                "requires": ["rcm-m5n-missing-tool"],
                "env_passthrough": ["PATH"],
                "env": {},
            },
        ],
    }


def pinned(env: Path, srv: FakeRcm) -> Path:
    return config(env, f'server = "{srv.url}"\npool = "mbp"\n')


def test_worker_check_measures_the_fingerprint_locally(env, srv, toolbox, capsys):
    srv.route("GET", "/worker/spec", spec(toolbox))
    code, out, err = worker_check(capsys, env, pinned(env, srv))
    row = check_row(out, "fingerprint")
    assert row is not None and row[0] != "FAIL", out + err
    assert row[1] == "fake 1.2.3 · gone missing", out


def test_worker_check_counts_the_presets_this_worker_can_run(env, srv, toolbox, capsys):
    srv.route("GET", "/worker/spec", spec(toolbox))
    code, out, err = worker_check(capsys, env, pinned(env, srv))
    row = check_row(out, "presets")
    assert row is not None and row[0] != "FAIL", out + err
    assert row[1] == "1/2 ok · 1 rcm-m5n-missing-tool missing", out
    assert code == 0, out + err  # 못 도는 프리셋은 사실이지 검사 실패가 아니다


def test_worker_check_says_workspace_only_when_path_has_a_relative_entry(
    env, srv, toolbox, monkeypatch, capsys
):
    """결정 93: PATH 에 상대 항목이 있으면 못 찾은 도구는 `unknown`(workspace_tool)이다."""
    monkeypatch.setenv("PATH", f"{toolbox}:node_modules/.bin:/usr/bin:/bin")
    srv.route("GET", "/worker/spec", spec(toolbox))
    _, out, _ = worker_check(capsys, env, pinned(env, srv))
    assert check_row(out, "presets") == ("ok", "1/2 ok · 1 rcm-m5n-missing-tool workspace only")


def test_worker_check_reports_nothing_to_the_server(env, srv, toolbox, capsys):
    srv.route("GET", "/worker/spec", spec(toolbox))
    worker_check(capsys, env, pinned(env, srv))
    assert srv.calls("GET", "/worker/spec"), [r.path for r in srv.requests]
    assert not srv.calls("POST", "/worker/capabilities")
    assert not srv.calls("POST", "/worker/register")


def test_worker_check_on_an_old_server_without_spec_does_not_fail(env, srv, toolbox, capsys):
    srv.routes.pop(("GET", "/worker/spec"))
    code, out, err = worker_check(capsys, env, pinned(env, srv))
    for name in ("fingerprint", "presets"):
        row = check_row(out, name)
        assert row is not None and row[0] != "FAIL" and row[1].startswith("—"), out
    assert code == 0, out + err
