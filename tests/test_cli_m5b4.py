"""CLI(M5b-4) — `rcm check` 의 `pools` 행. 명세는 docs/m5b4-workplan.md §3.

잠그는 모양(구현 전 — test-first):
- 행 이름은 `pools`. 상세는 `default (<N> lane[s])` 로 시작한다(N 은 `/api/status` 의 `server.lanes`
  — `1 lane` · `2 lanes`). 원격 워커가 하나도 없으면 상세는 그것뿐이다.
- 원격 워커가 있는 풀마다 ` · <pool> (<workers>)` 를 잇는다. 워커 표기는 머리줄 필(M5b-2)과 같은
  `<worker>/<lane> idle` · `<worker>/<lane> busy #<job>`, down 은 워커 단위로 `<worker> down`
  (레인 표기 없음). 한 풀 안의 워커는 ` · ` 로 잇고 이름순이다.
- FAIL 은 **어떤 풀의 워커가 전부 down** 일 때(`/api/health` 의 `pools_without_workers`). 살아 있는
  워커가 하나라도 있으면 down 워커가 보여도 ok. FAIL 이면 `rcm check` 는 1 로 끝나고 다른 행은
  그대로.
- 자리: `presets` 행 **바로 다음**(`timezone` 앞). 서버에 못 닿으면 행 자체가 없다(오늘처럼
  `server` · `token` · `presets` 의 FAIL 만).

test_cli_m4 처럼 `main(argv)` 를 in-process 로 부르고 `RCM_SERVER`/`RCM_TOKEN` 만으로 서버를
가리킨다. 서버는 `test_worker_api.WorkerServer`(진짜 `/worker/*` + 주입 시계) — 워커의 down 은
`srv.clock.advance()` 로 `worker_timeout_seconds` 를 넘겨서 만든다(janitor 는 안 돈다: down 은
읽을 때 `last_seen_at` 로 계산된다). HOME 은 tmp 로 옮겨 실제 설정을 안 본다.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from remote_ci_monitor.cli import main
from test_worker_api import TIMEOUT, WorkerServer

WORKER = "build-02"
WORKER2 = "build-03"
POOL = "linux"
DEFAULT_1 = "default (1 lane)"

#: cmd_check 의 행 형식 `{'ok ' if ok else 'FAIL'}  {name:<13} {detail}` — 이름은 13칸 고정.
ROW_RE = re.compile(r"^(ok |warn|FAIL)  (.{13}) (.*)$")  # warn 은 `cmd_check` 의 세 번째 등급


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def rows(out: str) -> list[tuple[str, str, str]]:
    """`rcm check` 출력 → `[(status, name, detail)]` (행 순서 그대로).

    행이 아닌 줄은 없어야 한다(행은 전부 stdout · 안내는 stderr).
    """
    assert out.strip(), "rcm check printed nothing"
    parsed: list[tuple[str, str, str]] = []
    for ln in out.splitlines():
        m = ROW_RE.match(ln)
        assert m, f"not a check row: {ln!r}\n{out}"
        parsed.append((m.group(1).strip(), m.group(2).rstrip(), m.group(3)))
    return parsed


def row(out: str, name: str) -> tuple[str, str] | None:
    """`name` 행의 (status, detail). 행이 없으면 None. 같은 이름이 둘이면 실패."""
    found = [(s, d) for s, n, d in rows(out) if n == name]
    assert len(found) <= 1, f"row {name!r} appears {len(found)} times:\n{out}"
    return found[0] if found else None


def names(out: str) -> list[str]:
    return [n for _, n, _ in rows(out)]


def not_ok(out: str) -> list[str]:
    """FAIL 인 행 이름들 — 「다른 행은 그대로」를 한 번에 잠근다."""
    return [n for s, n, _ in rows(out) if s != "ok"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """HOME 을 tmp 로, `XDG_CONFIG_HOME`·`RCM_*` 은 없이. cwd 도 빈 tmp 로(`./rcm.toml` 방지)."""
    h = tmp_path / "home"
    h.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in (
        "XDG_CONFIG_HOME",
        "RCM_CONFIG",
        "RCM_SERVER",
        "RCM_TOKEN",
        "RCM_LABEL",
        "RCM_WORKER_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    return h


@pytest.fixture
def srv(tmp_path, request):
    """진짜 `/worker/*` 서버 + 주입 시계. 로컬 레인 수는 `indirect` 파라미터(기본 1)."""
    s = WorkerServer(tmp_path, lanes=getattr(request, "param", 1))
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, home):
    """`use(srv)` — 서버·클라이언트 토큰(alice)을 `RCM_SERVER`/`RCM_TOKEN` 으로 건다."""

    def use(server: WorkerServer, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


def check(capsys) -> tuple[int, str]:
    """`rcm check` — (code, stdout). stderr 는 비어 있어야 한다(행은 전부 stdout)."""
    code, out, err = run(capsys, ["check"])
    assert err == "", err
    return code, out


# ── 원격 워커 없음 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "srv,expected",
    [(1, "default (1 lane)"), (2, "default (2 lanes)")],
    indirect=["srv"],
    ids=["1-lane", "2-lanes"],
)
def test_pools_row_shows_only_the_default_pool_without_remote_workers(srv, env, capsys, expected):
    """§3: 원격 워커가 하나도 없으면 상세는 `default (N lane[s])` 뿐. N 은 `server.lanes`."""
    env(srv)
    assert all(w["worker"] is None for w in srv.workers())  # 전제: 로컬 레인뿐
    code, out = check(capsys)
    assert code == 0, out
    assert row(out, "pools") == ("ok", expected), out


def test_pools_row_comes_right_after_presets(srv, env, capsys):
    """§3(자리): `presets` 바로 다음 행이 `pools` 다 — `timezone` 은 그 뒤로 밀린다."""
    env(srv)
    code, out = check(capsys)
    assert code == 0, out
    order = names(out)
    assert "pools" in order, out
    assert order.index("pools") == order.index("presets") + 1, order
    assert order.index("timezone") > order.index("pools"), order
    assert order[:4] == ["python", "server", "token", "presets"], order  # 앞쪽은 오늘 그대로


# ── 원격 워커 idle · busy ────────────────────────────────────────────────────


def test_pools_row_lists_an_idle_remote_worker(srv, env, capsys):
    """§3: 등록된 워커(linux · 1 레인 · 살아 있음) → ` · linux (build-02/1 idle)`."""
    env(srv)
    srv.registered(WORKER, pool=POOL)
    assert srv.worker_lane(WORKER, 1)["state"] == "idle"  # 전제
    code, out = check(capsys)
    assert code == 0, out
    assert row(out, "pools") == ("ok", f"{DEFAULT_1} · {POOL} ({WORKER}/1 idle)"), out
    order = names(out)
    assert order.index("pools") == order.index("presets") + 1, order


def test_pools_row_shows_the_running_job_of_a_busy_remote_worker(srv, env, capsys):
    """§3: claim 한 잡이 돌고 있으면 ` · linux (build-02/1 busy #N)` — 머리줄 필과 같은 표기."""
    env(srv)
    jid = srv.queued_job(preset="lin")
    srv.registered(WORKER, pool=POOL)
    assert srv.claimed(WORKER) == jid
    lane = srv.worker_lane(WORKER, 1)
    assert lane["state"] == "busy" and lane["job_id"] == jid  # 전제
    code, out = check(capsys)
    assert code == 0, out
    assert row(out, "pools") == ("ok", f"{DEFAULT_1} · {POOL} ({WORKER}/1 busy #{jid})"), out


# ── down → FAIL ─────────────────────────────────────────────────────────────


def test_pools_row_fails_when_every_worker_of_a_pool_is_down(srv, env, capsys):
    """§3(FAIL 조건): heartbeat 이 `worker_timeout_seconds` 를 넘긴 워커뿐인 풀 →
    ` · linux (build-02 down)` · 행은 FAIL · 종료 1. 다른 행(server · token · presets · timezone)은
    그대로 ok — `/api/health` 의 `ok` 는 원격 풀과 무관하다(`pools_without_workers` 는 정보
    필드)."""
    env(srv)
    srv.registered(WORKER, pool=POOL)
    srv.clock.advance(TIMEOUT + 1)
    # 전제: 서버는 이미 그 풀을 「워커 전부 down」으로 본다
    status, health = srv.req("GET", "/api/health")
    assert status == 200 and health["ok"] is True, health
    assert health["pools_without_workers"] == [POOL], health
    assert srv.worker_lane(WORKER, 1)["state"] == "down"
    code, out = check(capsys)
    assert code == 1, out
    assert row(out, "pools") == ("FAIL", f"{DEFAULT_1} · {POOL} ({WORKER} down)"), out
    assert not_ok(out) == ["pools"], out  # 다른 행은 영향 없음
    for name in ("python", "server", "token", "presets", "timezone"):
        assert row(out, name) is not None and row(out, name)[0] == "ok", (name, out)


def test_pools_row_is_ok_while_one_worker_of_the_pool_is_still_alive(srv, env, capsys):
    """§3: 같은 풀에 down 하나 · idle 하나 → `linux (build-02 down · build-03/1 idle)` 이고 ok —
    풀에 살아 있는 워커가 있으면 FAIL 이 아니다. down 워커는 접지 않고 이름순으로 보인다."""
    env(srv)
    srv.registered(WORKER, pool=POOL)
    srv.clock.advance(TIMEOUT + 1)
    srv.registered(WORKER2, pool=POOL)  # 지금 등록 → 살아 있다
    status, health = srv.req("GET", "/api/health")
    assert status == 200 and health["pools_without_workers"] == [], health  # 전제
    assert srv.worker_lane(WORKER, 1)["state"] == "down"
    assert srv.worker_lane(WORKER2, 1)["state"] == "idle"
    code, out = check(capsys)
    assert code == 0, out
    detail = f"{DEFAULT_1} · {POOL} ({WORKER} down · {WORKER2}/1 idle)"
    assert row(out, "pools") == ("ok", detail), out
    assert not_ok(out) == [], out


# ── 서버에 못 닿음 ──────────────────────────────────────────────────────────


def test_pools_row_is_absent_when_the_server_is_unreachable(home, monkeypatch, capsys):
    """§3(오늘 그대로): 서버에 못 닿으면 `server`·`token`·`presets` 의 FAIL 만 — `pools` 행은
    없다."""
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("RCM_TOKEN", "not-a-real-token")
    code, out, _err = run(capsys, ["check"])
    assert code == 1, out
    assert row(out, "server") is not None and row(out, "server")[0] == "FAIL", out
    assert row(out, "pools") is None, out
    assert row(out, "timezone") is None, out  # status 를 못 읽으니 timezone 도 없다(오늘 그대로)
    assert names(out) == ["python", "server", "token", "presets"], names(out)
