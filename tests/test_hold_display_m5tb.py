"""M5t-b PR3 — 그룹 홀드의 사람용 화면(docs/m5tb-workplan.md §4.8 · 결정 330 · 350).

홀드의 기계 판독 표면(`needs_action` · `action.code = irreversible_hold` · `blocked_by` ·
`irreversible_holds`)은 PR1 이 만들었다. 여기서는 사람이 읽는 줄만 잠근다.

- `rcm wait`: 기다리는 잡이 홀드에 막히면 stderr 한 줄(원문 코드 없이 그룹 · 홀드 잡 · 풀 명령),
  `--return-on-action` 이면 4.
- `rcm top`: 막힌 대기 행의 이유 줄도 같은 말이다.
- `rcm check`: 홀드마다 경고 줄(warn, 종료 코드는 그대로). 홀드를 못 읽었으면(null) 경고 한 줄.
- `rcm doctor`: 역할이 worker · coordinator-worker 이면 이 머신의 홀드를 `needs action` 줄로,
  `--json` 에 `irreversible_holds`(이 머신의 것만, 모르면 null). 종료 코드 규칙은 그대로다.

시나리오: RL45(PR3 몫).
"""

from __future__ import annotations

import functools
from typing import Any

import pytest

import test_doctor_cli as doctor_tests
from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import client as client_mod
from remote_ci_monitor.cli import main
from remote_ci_monitor.core.render_text import hold_check_text, render
from test_cli_m1 import last_json
from test_doctor_cli import CLIENT_TOKEN, TOP_KEYS, run, run_json, server_toml

# `rcm doctor` 시험의 픽스처를 이 파일에도 등록한다 — pytest 는 픽스처 정의의 이름으로 찾는다
env = doctor_tests.env
srv = doctor_tests.srv
svcbin = doctor_tests.svcbin
worker = doctor_tests.worker
coordinator = doctor_tests.coordinator

HOLD_A = {
    "job_id": 812,
    "preset": "ship",
    "group": "irreversible:ship",
    "scope": "global",
    "worker": "mbp",
    "cause": "worker_unreachable",
    "since": "2026-11-27T08:50:00Z",
}
HOLD_B = {
    "job_id": 815,
    "preset": "deploy",
    "group": "devices",
    "scope": "pool",
    "worker": None,
    "cause": "worker_restarted_without_job",
    "since": "2026-11-27T08:55:00Z",
}
HOLD_OTHER = {**HOLD_A, "job_id": 816, "worker": "build-02", "group": "release"}

ACTION = {
    "code": "irreversible_hold",
    "note": "group held — run rcm release-hold 812",
    "at": "2026-11-27T08:50:00Z",
}
BLOCKED = {"job_id": 812, "group": "irreversible:ship", "remaining_seconds": None}
RAW = ("irreversible_hold", "worker_unreachable", "worker_restarted_without_job")


def no_raw_code(text: str) -> None:
    for code in RAW:
        assert code not in text, (code, text)


# ── rcm wait ─────────────────────────────────────────────────────────────────

JOB = 821


class Script:
    def __init__(self, fake: FakeRcm):
        self.fake = fake
        self.docs: list[dict[str, Any]] = []
        fake.route("GET", "/api/health", {"ok": True, "version": __version__})
        fake.route("GET", f"/jobs/{JOB}", lambda _b: self.next_doc())

    def next_doc(self) -> dict[str, Any]:
        return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]


def queued(held: bool = True, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": JOB,
        "state": "queued",
        "preset": "ship",
        "position": 1,
        "exit_code": None,
        "url": f"http://build.example:8787/#/jobs/{JOB}",
        "keep": False,
        "shelf": None,
        "reason": "needs_action" if held else "waiting_for_lane",
        "blocked_by": BLOCKED if held else None,
        "action": ACTION if held else None,
        "estimate": {"finish_at": None, "wait_seconds": None},
    }
    d.update(extra)
    return d


def finished() -> dict[str, Any]:
    return {**queued(False), "state": "succeeded", "exit_code": 0, "reason": None}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    srv_ = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", srv_.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    fast = functools.partial(client_mod.wait_for_job, poll_seconds=0.01)
    monkeypatch.setattr(cli_mod, "wait_for_job", fast)
    yield Script(srv_)
    srv_.close()


def cli_run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


HELD_LINE = (
    f"#{JOB} needs action · group irreversible:ship is held by job #812 — check that machine "
    "for a leftover process, then ask an admin to run rcm release-hold 812"
)


def test_rl45_rcm_wait_says_in_words_that_the_group_is_held(fake, capsys):
    fake.docs = [queued(), queued(), queued(held=False), finished()]
    code, _out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err
    # 진행 줄(`#821 queued · 1st in line · needs action`)은 오늘 그대로다 — 남는 줄만 센다
    assert [ln for ln in err.splitlines() if "needs action ·" in ln] == [HELD_LINE], err
    no_raw_code(err)


def test_rl45_rcm_run_prints_the_hold_line_while_it_waits(fake, capsys):
    """# RL45 P3 r2: `rcm run` 도 같은 `_wait` 로 기다린다 — 홀드 줄을 `rcm wait` 와 똑같이 한 번
    찍는다(docs/operating.md 「When a group is held」 표의 「`rcm wait` and `rcm run`, while they
    wait」)."""
    fake.fake.route("GET", "/api/whoami", {"name": "alice", "admin": False})
    fake.fake.route(
        "GET",
        "/api/status",
        {"presets": [{"name": "ship", "source_modes": ["git_ref"], "inputs": [], "keep": False}]},
    )
    fake.fake.route(
        "POST",
        "/jobs",
        {"job_id": JOB, "state": "queued", "joined": False, "url": f"{fake.fake.url}/#/jobs/{JOB}"},
    )
    fake.docs = [queued(), queued(), queued(held=False), finished()]
    code, _out, err = cli_run(capsys, ["run", "ship", "--ref", "main", "--poll"])
    assert code == 0, err
    assert [ln for ln in err.splitlines() if "needs action ·" in ln] == [HELD_LINE], err
    no_raw_code(err)


def test_rl45_return_on_action_ends_4_on_a_hold(fake, capsys):
    fake.docs = [queued(), finished()]
    code, out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll", "--return-on-action"])
    assert code == 4, err
    assert HELD_LINE in err.splitlines(), err
    doc = last_json(out)
    assert doc["wait_exit_code"] == 4 and doc["action"]["code"] == "irreversible_hold"


def test_rl45_a_hold_action_without_blocked_by_falls_back_to_the_plain_line(fake, capsys):
    """옛 · 가짜 서버의 문서 — 그룹을 모르면 지어내지 않고 오늘의 줄 그대로다."""
    fake.docs = [queued(blocked_by=None), finished()]
    code, _out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err
    assert (
        f"#{JOB} needs action · irreversible_hold · group held — run rcm release-hold 812"
        in err.splitlines()
    ), err


# ── rcm top ──────────────────────────────────────────────────────────────────


def test_rl45_rcm_top_reason_line_of_a_held_waiting_job_has_no_raw_code():
    row = {
        **queued(),
        "pool": "default",
        "key": "ship",
        "requester": {"name": "alice", "label": "alice@mbp"},
        "joiners": [],
        "source": {"mode": "tree"},
        "placement": "pool",
        "placement_detail": [],
        "estimate": {"waited_seconds": 30.0, "source": "preset", "sample_count": 0},
    }
    doc = {
        "schema_version": 1,
        "generated_at": "2026-11-27T09:00:00Z",
        "display_timezone": "UTC",
        "server": {"lanes": 1, "workers": []},
        "pools": [{"name": "default", "queue": [row], "recent": [], "medians": {}, "hosts": []}],
    }
    text = render(doc)
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if f"#{JOB} " in ln)
    assert lines[i + 1].strip() == (
        "needs action · group irreversible:ship is held by #812 — rcm release-hold 812 after "
        "checking the machine"
    ), text
    no_raw_code(lines[i + 1])


# ── rcm check ────────────────────────────────────────────────────────────────


def check_lines(fake: Script, capsys, monkeypatch, health_extra: dict[str, Any]) -> tuple:
    fake.fake.route("GET", "/api/health", {"ok": True, "version": __version__, **health_extra})
    fake.fake.route("GET", "/api/whoami", {"name": "alice", "admin": False})
    fake.fake.route(
        "GET", "/api/status", {"presets": [{"name": "ship"}], "pools": [], "server": {}}
    )

    def no_local_config(*_a, **_k):
        from remote_ci_monitor.config import ConfigError

        raise ConfigError("no server config here")

    monkeypatch.setattr(cli_mod, "load_server_config", no_local_config)
    code, out, _err = cli_run(capsys, ["check"])
    return code, [ln for ln in out.splitlines() if "irreversible hold" in ln]


def test_rl45_rcm_check_warns_once_per_hold_and_keeps_its_exit_code(fake, capsys, monkeypatch):
    code0, base = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": []})
    assert base == []
    code, lines = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": [HOLD_A, HOLD_B]})
    assert code == code0  # 경고다 — FAIL(종료 1)이 아니다
    assert lines == [
        "warn  irreversible hold group irreversible:ship · job #812 (ship) on mbp closed without "
        "a stop confirmation — check mbp, then rcm release-hold 812",
        "warn  irreversible hold group devices · job #815 (deploy) on the server's local lane "
        "closed without a stop confirmation — check the server, then rcm release-hold 815",
    ], lines
    for ln in lines:
        no_raw_code(ln)


def test_rl45_rcm_check_says_it_could_not_read_the_holds(fake, capsys, monkeypatch):
    code0, _ = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": []})
    code, lines = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": None})
    assert code == code0
    assert lines == [
        "warn  irreversible hold the server could not read its group holds — unknown"
    ], lines


UNREADABLE_HOLDS = (
    {"job_id": "812", "group": "irreversible:ship"},  # 번호가 문자열
    {"job_id": 813, "group": ""},  # 그룹이 비었다
    {"job_id": True, "group": "devices"},  # bool 은 번호가 아니다
    "not a hold",
)


@pytest.mark.parametrize("bad", UNREADABLE_HOLDS, ids=["str-id", "no-group", "bool-id", "str"])
def test_rl45_rcm_check_says_unknown_when_one_hold_cannot_be_read(fake, capsys, monkeypatch, bad):
    """# RL45 읽을 수 없는 항목을 조용히 빼면 짧은 목록이 「그게 전부」로 읽힌다. `rcm doctor` 의
    `parse_holds` 와 웹의 `holdsOf` 와 같은 규칙: 하나라도 못 읽으면 목록 전체가 모름이다(P3 r1)."""
    code0, _ = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": []})
    code, lines = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": [HOLD_A, bad]})
    assert code == code0
    assert lines == [
        "warn  irreversible hold could not read every group hold the server sent — unknown"
    ], lines


def test_rl45_doctor_json_is_null_when_one_hold_cannot_be_read(worker, srv, capsys):
    """# RL45 doctor 의 `parse_holds` 는 못 읽는 항목 하나에 목록 전체를 모름(null)으로 둔다 —
    `rcm check` · 웹과 같은 규칙."""
    srv.route(
        "GET",
        "/api/health",
        {"ok": True, "version": __version__, "irreversible_holds": [HOLD_A, {"job_id": "x"}]},
    )
    _code, doc = worker(capsys)
    assert doc["irreversible_holds"] is None, doc["irreversible_holds"]


def test_rl45_rcm_check_has_no_hold_row_on_an_old_server(fake, capsys, monkeypatch):
    _code, lines = check_lines(fake, capsys, monkeypatch, {})
    assert lines == []


# ── rcm doctor ───────────────────────────────────────────────────────────────

DOCTOR_LINE_A = (
    "needs action  job #812 (ship) · group irreversible:ship: a job that ran here was closed "
    "without a stop confirmation — check for a leftover process, then ask an admin to run "
    "rcm release-hold 812"
)


def test_rl45_doctor_json_has_the_key_and_lists_only_this_machines_holds(worker, srv, capsys):
    code0, doc0 = worker(capsys)
    assert set(doc0) == TOP_KEYS | {"irreversible_holds"}
    assert doc0["irreversible_holds"] is None  # 서버가 키를 싣지 않았다(옛 서버) — 모른다
    srv.route(
        "GET",
        "/api/health",
        {"ok": True, "version": __version__, "irreversible_holds": [HOLD_A, HOLD_OTHER]},
    )
    code, doc = worker(capsys)
    assert code == code0  # 종료 코드 규칙은 그대로다
    assert doc["irreversible_holds"] == [HOLD_A], doc["irreversible_holds"]
    assert doc["doctor_version"] == 1


def test_rl45_doctor_prints_a_needs_action_line_for_this_machine(worker, srv, capsys):
    srv.route(
        "GET",
        "/api/health",
        {"ok": True, "version": __version__, "irreversible_holds": [HOLD_A, HOLD_OTHER]},
    )
    _code, out, _err = worker.text(capsys)
    lines = [ln for ln in out.splitlines() if ln.startswith("needs action")]
    assert lines == [DOCTOR_LINE_A], out
    no_raw_code("\n".join(lines))


def test_rl45_doctor_with_no_hold_here_prints_nothing_and_has_an_empty_list(worker, srv, capsys):
    srv.route("GET", "/api/health", {"ok": True, "version": __version__, "irreversible_holds": []})
    _code, doc = worker(capsys)
    assert doc["irreversible_holds"] == []
    _code, out, _err = worker.text(capsys)
    assert "needs action" not in out


HOLDS_UNKNOWN = "group holds: unknown — "


def test_rl45_doctor_text_says_the_holds_are_unknown_on_a_worker(worker, srv, capsys):
    """# RL45 P3 r1: 텍스트 출력에서 null(모름)과 [](없음)이 둘 다 아무것도 안 찍으면 모름이
    「없음」으로 읽힌다. 홀드를 가질 수 있는 역할이면 null 에 한 줄."""
    _code, doc = worker(capsys)
    assert doc["irreversible_holds"] is None  # 옛 서버 — 키가 없다
    _code, out, _err = worker.text(capsys)
    lines = [ln for ln in out.splitlines() if HOLDS_UNKNOWN in ln]
    assert len(lines) == 1, out
    srv.route("GET", "/api/health", {"ok": True, "version": __version__, "irreversible_holds": []})
    _code, out, _err = worker.text(capsys)
    assert HOLDS_UNKNOWN not in out  # 없음은 조용하다


def test_rl45_doctor_text_says_unknown_on_a_coordinator_worker(
    env, coordinator, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health = coordinator.routes[("GET", "/api/health")][1]
    coordinator.route("GET", "/api/health", {**health, "irreversible_holds": None})
    cfg = server_toml(env, coordinator.port)
    _code, out, _err = run(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert len([ln for ln in out.splitlines() if HOLDS_UNKNOWN in ln]) == 1, out


def test_rl45_doctor_text_is_quiet_about_holds_on_a_client(env, coordinator, monkeypatch, capsys):
    """client · coordinator 역할의 null 은 「이 머신과 무관」이다 — 지금처럼 조용히."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    _code, out, _err = run(capsys, "--role", "client", "--server", coordinator.url)
    assert HOLDS_UNKNOWN not in out, out


def test_rl45_coordinator_worker_sees_the_local_lane_holds(env, coordinator, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health = coordinator.routes[("GET", "/api/health")][1]
    coordinator.route("GET", "/api/health", {**health, "irreversible_holds": [HOLD_A, HOLD_B]})
    cfg = server_toml(env, coordinator.port)
    code0, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert doc["irreversible_holds"] == [HOLD_B], doc["irreversible_holds"]
    _code, out, _err = run(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert [ln for ln in out.splitlines() if ln.startswith("needs action")] == [
        "needs action  job #815 (deploy) · group devices: a job that ran here was closed without "
        "a stop confirmation — check for a leftover process, then ask an admin to run "
        "rcm release-hold 815"
    ], out


def test_rl45_client_role_has_a_null_hold_list(env, coordinator, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health = coordinator.routes[("GET", "/api/health")][1]
    coordinator.route("GET", "/api/health", {**health, "irreversible_holds": [HOLD_B]})
    code, doc = run_json(capsys, "--role", "client", "--server", coordinator.url)
    assert code == 0 and doc["irreversible_holds"] is None, doc


# ── P6-1 테마 B: 홀드는 어디서나 계산의 입력이다 ─────────────────────────────────


@pytest.fixture
def held_server(tmp_path):
    """`ship`(되돌릴 수 없음)의 잡이 확인 없이 닫혀 그 그룹이 홀드된 서버. `ok` 는 그룹이 없다."""
    from test_worker_api import WorkerServer, sh

    s = WorkerServer(
        tmp_path, presets=[sh("ship", "echo ship", irreversible=True), sh("ok", "echo ok")]
    )
    try:
        s.registered("build-02")
        jid = s.queued_job(preset="ship")
        assert s.claimed("build-02", 1) == jid
        # finish 에 group_gone 이 없다 — 확인 없는 닫힘이라 서버가 홀드를 건다(§4.12)
        assert s.finish("build-02", jid, "succeeded", exit_code=0)[0] == 200
        assert s.store.has_open_hold(jid)
        s.hold_job = jid  # type: ignore[attr-defined]
        yield s
    finally:
        s.close()


def test_g2_rl45_eta_for_a_preset_in_a_held_group_has_no_wait_finish_or_ahead(held_server):
    """# RL45 P6-1 G2: `rcm eta <preset>`(POST /api/eta, 풀 배치 경로)도 열린 홀드를 넘긴다.
    안 넘기면 홀드된 그룹의 새 잡이 곧 시작할 것처럼 유한한 wait · finish 를 받는다 — 실제로
    넣은 잡은 needs_action 이고 ETA 가 없다. 그룹 없는 프리셋은 그대로 숫자를 받는다(대조)."""
    s = held_server
    status, doc = s.req("POST", "/api/eta", token="alice", json_body={"preset": "ship"})
    assert status == 200, doc
    row = doc["job"]
    assert row["reason"] == "needs_action", row
    assert row["action"]["code"] == "irreversible_hold", row
    assert row["blocked_by"]["job_id"] == s.hold_job, row
    assert row["estimate"]["wait_seconds"] is None, row["estimate"]
    assert row["estimate"]["finish_at"] is None, row["estimate"]
    assert doc["ahead"] is None, doc  # 오지 않을 순번을 세지 않는다
    status, ok = s.req("POST", "/api/eta", token="alice", json_body={"preset": "ok"})
    assert status == 200 and ok["job"]["reason"] != "needs_action", ok
    assert ok["job"]["estimate"]["wait_seconds"] is not None, ok


def test_g2_g4_rl45_rcm_eta_preset_says_the_group_is_held(fake, capsys):
    """# RL45 P6-1 G2 · G4: 사람용 줄은 「wait — · eta —」만 찍지 않고 사유 문장(rcm top 과 같은
    말)을 붙인다. 앞선 잡 수는 모른다."""
    row = {**queued(), "id": None, "url": None, "position": 1}
    fake.fake.route("POST", "/api/eta", {"job": row, "ahead": None, "generated_at": "x"})
    code, out, err = cli_run(capsys, ["eta", "ship"])
    assert code == 0, err
    assert "group irreversible:ship is held by #812 — rcm release-hold 812" in out, out
    assert "ahead" not in out, out
    no_raw_code(out)


def top_doc(queue: list[dict[str, Any]], holds: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "2026-11-27T09:00:00Z",
        "display_timezone": "UTC",
        "server": {"lanes": 1, "workers": []},
        "pools": [{"name": "default", "queue": queue, "recent": [], "medians": {}, "hosts": []}],
        "irreversible_holds": holds,
    }


def test_g3_rl45_rcm_top_does_not_promise_an_immediate_start_while_a_group_is_held():
    """# RL45 P6-1 G3: 대기 잡이 없어도 열린 홀드가 있으면 「rcm run <preset> starts immediately」는
    그 그룹의 프리셋에 거짓이다. 그 말을 하지 않고 홀드마다 한 줄을 더한다. 홀드가 없거나 키가 없는
    옛 서버는 오늘 그대로다."""
    text = render(top_doc([], [HOLD_A, HOLD_B]))
    assert "starts immediately" not in text, text
    lines = text.splitlines()
    assert any(
        "group irreversible:ship is held by #812 — rcm release-hold 812" in ln for ln in lines
    ), text
    assert any("group devices is held by #815 — rcm release-hold 815" in ln for ln in lines), text
    no_raw_code(text)
    for holds in ([], ...):
        doc = top_doc([], holds)
        if holds is ...:
            del doc["irreversible_holds"]
        assert "queue — empty (rcm run <preset> starts immediately)" in render(doc)


# ── P6-1 G7: 진짜 생산자 → 세 소비자 ───────────────────────────────────────────

_WEB_NOT_MOVING = """
globalThis.window = globalThis;
const rcm = require(process.argv[1]);
const holds = JSON.parse(process.argv[2]);
const st = { schema_version: 1, generated_at: "2026-11-27T09:00:00Z",
  server: { lanes: 1, workers: [] }, pools: [{ name: "default", queue: [], recent: [] }],
  irreversible_holds: holds };
const nm = rcm.notMoving(st, null, "en");
process.stdout.write(JSON.stringify({ kind: nm.kind, why: nm.why || null,
  lines: nm.lines.map((l) => l.text) }));
"""


def web_not_moving(holds: list[Any]) -> dict[str, Any]:
    """웹의 「확인이 필요한 작업」(`notMoving` → `holdsOf`)에 홀드 목록을 그대로 넣는다."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed — the web half runs in tests/web (node --test)")
    app = Path(__file__).resolve().parent.parent / "src/remote_ci_monitor/web/app.js"
    out = subprocess.run(
        [node, "-e", _WEB_NOT_MOVING, str(app), json.dumps(holds)],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    return json.loads(out.stdout)


def produced_holds() -> list[dict[str, Any]]:
    """`/api/status` · `/api/health` 가 실제로 싣는 항목 — 손으로 쓴 사본이 아니라 생산자의 출력."""
    from datetime import UTC, datetime

    from remote_ci_monitor.core.model import OpenHold
    from remote_ci_monitor.core.status import hold_item_json

    since = datetime(2026, 11, 27, 8, 50, tzinfo=UTC)
    remote = OpenHold(
        job_id=812,
        group="irreversible:ship",
        scope="global",
        pool="default",
        since=since,
        preset="ship",
        worker="mbp",
        cause="worker_unreachable",
    )
    local = OpenHold(
        job_id=815,
        group="devices",
        scope="pool",
        pool="default",
        since=since,
        preset="deploy",
        worker=None,
        cause="worker_restarted_without_job",
    )
    return [hold_item_json(remote), hold_item_json(local)]


def test_g7_rl45_the_real_hold_producer_feeds_doctor_check_and_the_web():
    """# RL45 P6-1 G7: 소비자 셋(doctor `parse_holds`, rcm check `hold_check_text`, 웹
    `holdsOf`)에 진짜 생산자 `hold_item_json` 의 출력을 그대로 넣는다. 원격 홀드는 그 워커의
    것으로, `worker: null` 은 서버의 로컬 레인으로 읽는다."""
    from remote_ci_monitor.doctor import parse_holds

    holds = produced_holds()
    got = parse_holds(holds)
    assert got is not None and [(h["job_id"], h["worker"]) for h in got] == [
        (812, "mbp"),
        (815, None),
    ], got
    texts = [hold_check_text(h) for h in holds]
    assert texts[0] is not None and "#812 (ship) on mbp" in texts[0], texts
    assert texts[1] is not None and "#815 (deploy) on the server's local lane" in texts[1], texts
    web = web_not_moving(holds)
    assert web["kind"] == "list", web
    assert "job #812 (ship) on build-02" not in web["lines"][0]
    assert "job #812 (ship) on mbp" in web["lines"][0], web
    assert "job #815 (deploy) on the server's local lane" in web["lines"][1], web


@pytest.mark.parametrize("value", ["drop", 7, ["mbp"]], ids=["no-worker-key", "int", "list"])
def test_g7_rl45_a_hold_without_a_readable_worker_is_unknown_not_local(value):
    """# RL45 P6-1 G7: `worker` 키가 **없는** 항목(또는 문자열 · null 이 아닌 값)은 로컬
    레인(`null`)과 구별해 읽을 수 없다 — 로컬로 읽으면 coordinator-worker 의 doctor 가
    남의 홀드를 제 것으로, worker 역할은 제 홀드를 「여기 없음」으로 읽는다. 세 소비자 모두
    목록 전체를 모름으로 둔다."""
    from remote_ci_monitor.doctor import parse_holds

    holds = produced_holds()
    if value == "drop":
        del holds[0]["worker"]
    else:
        holds[0]["worker"] = value
    assert parse_holds(holds) is None
    assert hold_check_text(holds[0]) is None
    web = web_not_moving(holds)
    assert (web["kind"], web["why"]) == ("unknown", "holds"), web


# ── P6-1 G9: 홀드를 만든 잡의 rcm wait 이 그 홀드를 말한다 ─────────────────────────────


def lost_holder(hold: Any) -> dict[str, Any]:
    return {
        **queued(False),
        "state": "lost",
        "exit_code": None,
        "reason": None,
        "position": None,
        "concurrency_group": "irreversible:ship",
        "summary_code": "worker_unreachable",
        "summary_args": {"name": "mbp", "seconds": 660},
        "hold": hold,
    }


OPEN_HOLD = {
    "cause": "worker_unreachable",
    "created_at": "2026-11-27T08:50:00Z",
    "released_at": None,
    "release_kind": None,
    "released_by": None,
    "release_reason": None,
    "history": [],
}
OWNER_LINE = (
    f"#{JOB} group irreversible:ship is held — check the machine for a leftover process, then "
    f"ask an admin to run rcm release-hold {JOB}"
)


def test_g9_rl45_rcm_wait_on_the_job_that_holds_its_group_says_so(fake, capsys):
    """# RL45 P6-1 G9: 홀드를 만든 잡(#N, lost)의 `rcm wait` 은 lost 와 종료 3 만 보였다 — 그룹이
    막혔다는 것과 `rcm release-hold N` 은 대기 잡 · 웹 · check · doctor 에만 나왔다. 잡 문서에
    열린 `hold` 가 있으면 끝에 한 줄. 종료 코드는 그대로 3 이다."""
    fake.docs = [lost_holder(OPEN_HOLD)]
    code, _out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 3, err
    assert OWNER_LINE in err.splitlines(), err
    no_raw_code(OWNER_LINE)


@pytest.mark.parametrize(
    "hold",
    [None, {**OPEN_HOLD, "released_at": "2026-11-27T09:10:00Z", "release_kind": "manual"}, "x"],
    ids=["no-hold", "released", "unreadable"],
)
def test_g9_rl45_no_hold_line_without_an_open_hold(fake, capsys, hold):
    fake.docs = [lost_holder(hold)]
    code, _out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 3, err
    assert "is held" not in err, err
