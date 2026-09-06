"""CLI(M5b-1) — `rcm run --pool` · `rcm eta --pool` · `rcm jobs [--pool]`(풀별 묶음) · `rcm top` 의
풀 헤더 · `presets[].pool/pools`. 명세는 docs/m5-workplan.md 「M5b. 원격 워커」 「모델」 과
「순서」 3 (M5b-1: `pools[]` 순회 — 풀 하나일 때 화면은 그대로).

test_cli_m5 처럼 `main(argv)` 를 in-process 로 부르고 RCM_SERVER/RCM_TOKEN 만으로 서버를 가리킨다.
프리셋 `lin`(`pool = "linux"`, `pools = ["default"]`) 을 끼운 `pool_srv` 가 두 풀을 만든다 — 아직
`parse_preset` 이 `pool` 을 모르면 픽스처가 ConfigError 로 error 다(정상: 구현 전). 로컬 워커(같은
프로세스)는 풀 `default` 라 linux 잡은 워커가 없는 풀(`lanes 0`)에 머문다.
구현보다 먼저 썼다(test-first).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.cli import main
from remote_ci_monitor.client import Client
from remote_ci_monitor.config import parse_preset
from test_cli_m1 import last_json
from test_server import PRESETS, Server, sh

# 풀 linux 를 기본으로 하되 default 도 허용하는 프리셋. 명세 「모델」 첫 항목.
LIN_PRESET = sh("lin", "echo lin", pool="linux", pools=["default"])


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def pools_of(server: Server) -> dict[str, dict[str, Any]]:
    """`/api/status.pools[]` 를 이름으로."""
    doc = server.req("GET", "/api/status")[1]
    pools = {p["name"]: p for p in doc["pools"]}
    assert len(pools) == len(doc["pools"]), "pool names must be unique"
    return pools


def all_rows(server: Server) -> list[dict[str, Any]]:
    """모든 풀의 큐 행(조회 실패한 풀은 없어야 한다)."""
    rows: list[dict[str, Any]] = []
    for p in pools_of(server).values():
        assert p["queue"] is not None, p.get("queue_error")
        rows.extend(p["queue"])
    return rows


def pool_of(server: Server, job_id: int) -> str:
    """잡이 들어 있는 풀 이름. 정확히 한 풀에만 있어야 하고 행의 `pool` 키도 그 이름이어야 한다."""
    hits = [
        (name, r)
        for name, p in pools_of(server).items()
        for r in (p["queue"] or [])
        if r["id"] == job_id
    ]
    assert len(hits) == 1, hits
    name, row = hits[0]
    assert row.get("pool") == name, row
    return name


def row_of(rows: list[dict[str, Any]], job_id: int) -> dict[str, Any]:
    return next(r for r in rows if r["id"] == job_id)


def line_index(out: str, needle: str) -> int:
    """`needle` 이 든 첫 줄의 번호. 없으면 AssertionError."""
    for i, ln in enumerate(out.splitlines()):
        if needle in ln:
            return i
    raise AssertionError(f"{needle!r} not in output:\n{out}")


def pool_header_lines(out: str) -> list[str]:
    """`rcm jobs` 텍스트의 풀 헤더 줄(`pool <name>` 로 시작하는 줄)."""
    return [ln for ln in out.splitlines() if re.match(r"^pool \S+", ln)]


def record_posts(monkeypatch) -> list[tuple[str, Any]]:
    """`Client.post_json` 의 (path, body) 를 기록하되 원래 동작은 그대로 둔다."""
    calls: list[tuple[str, Any]] = []
    original = Client.post_json

    def recording(self, path, obj=None):
        calls.append((path, obj))
        return original(self, path, obj)

    monkeypatch.setattr(Client, "post_json", recording)
    return calls


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_m1.env 와 같다 — `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


def make_pool_server(tmp_path: Path, *, workers: bool) -> Server:
    """기본 프리셋 + `lin`(pool linux). test_server_m3.make_server 의 요령."""
    s = Server(tmp_path, workers=False)
    try:
        s.cfg.presets = tuple(parse_preset(p) for p in [*PRESETS, LIN_PRESET])
        if workers:
            s.app.start()
    except BaseException:
        s.close()
        raise
    return s


@pytest.fixture
def pool_srv(tmp_path):
    s = make_pool_server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def pool_live(tmp_path):
    s = make_pool_server(tmp_path, workers=True)
    yield s
    s.close()


@pytest.fixture
def tree(tmp_path) -> Path:
    """`rcm run --dir` 에 줄 작은 트리(파일 둘). 기본 Server 의 10 KB 상한 안."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "gate.sh").write_text("#!/bin/sh\necho gate\n")
    return root


def submit(capsys, tree: Path, preset: str, *extra: str) -> int:
    """`rcm run <preset> --no-wait --dir <tree> …` → job id. 제출·업로드까지 끝나 queued 다.

    같은 트리라도 프리셋이 다르면 합류하지 않는다 — 기본 풀에 둘을 넣을 땐 `ok`·`bad` 를 쓴다.
    """
    code, out, err = run(capsys, ["run", preset, "--no-wait", "--dir", str(tree), *extra])
    assert code == 0, err
    body = last_json(out)
    assert body["joined"] is False, body
    return int(body["job_id"])


# ── rcm run --pool ───────────────────────────────────────────────────────────


def test_run_uses_the_preset_pool_by_default(pool_srv, env, tree, capsys):
    env(pool_srv)
    lin = submit(capsys, tree, "lin")
    ok = submit(capsys, tree, "ok")
    assert pool_of(pool_srv, lin) == "linux"
    assert pool_of(pool_srv, ok) == "default"
    # `rcm jobs --json` 행도 풀을 싣는다
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    rows = json.loads(out)
    assert row_of(rows, lin)["pool"] == "linux"
    assert row_of(rows, ok)["pool"] == "default"


def test_run_pool_flag_picks_an_allowed_pool_and_is_sent_in_the_submit_body(
    pool_srv, env, tree, capsys, monkeypatch
):
    env(pool_srv)
    posts = record_posts(monkeypatch)
    lin = submit(capsys, tree, "lin", "--pool", "default")
    bodies = [body for path, body in posts if path == "/jobs"]
    assert len(bodies) == 1 and bodies[0].get("pool") == "default", bodies
    assert pool_of(pool_srv, lin) == "default"
    # 자기 풀을 명시해도 된다(`pools = ["default"]` 에 linux 가 없어도 자기 풀은 언제나 허용)
    lin2 = submit(capsys, tree, "lin", "--pool", "linux", "--no-join")
    assert pool_of(pool_srv, lin2) == "linux"


def test_run_pool_not_allowed_by_the_preset_is_2_and_names_the_allowed_pools(
    pool_srv, env, tree, capsys
):
    """클라이언트가 `presets[].pools` 로 걸러도, 서버 400 을 2 로 바꿔도 된다 — 잡은 없어야 한다."""
    env(pool_srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree), "--pool", "nope"])
    assert code == 2, err
    assert "pool" in err.lower() and "default" in err, err  # 허용 풀을 말한다
    assert out.strip() == ""
    assert all_rows(pool_srv) == []
    code, out, err = run(capsys, ["run", "lin", "--no-wait", "--dir", str(tree), "--pool", "nope"])
    assert code == 2, err
    assert "linux" in err and "default" in err, err  # 자기 풀 + pools 둘 다
    assert out.strip() == "" and all_rows(pool_srv) == []


# ── rcm jobs [--pool] ────────────────────────────────────────────────────────


def test_jobs_json_rows_carry_pool_and_pool_filter_lists_only_that_pool(
    pool_srv, env, tree, capsys
):
    env(pool_srv)
    ok = submit(capsys, tree, "ok")
    lin = submit(capsys, tree, "lin")
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    rows = json.loads(out)
    assert {r["id"] for r in rows} == {ok, lin}  # 기본은 모든 풀
    assert all("pool" in r for r in rows), rows
    code, out, _ = run(capsys, ["jobs", "--pool", "linux", "--json"])
    assert code == 0
    rows = json.loads(out)
    assert [r["id"] for r in rows] == [lin] and rows[0]["pool"] == "linux"
    code, out, _ = run(capsys, ["jobs", "--pool", "default", "--json"])
    assert code == 0
    rows = json.loads(out)
    assert [r["id"] for r in rows] == [ok] and rows[0]["pool"] == "default"
    # 텍스트도 걸러진다
    code, out, _ = run(capsys, ["jobs", "--pool", "linux"])
    assert code == 0 and f"#{lin}" in out and f"#{ok}" not in out


def test_jobs_text_shows_pool_headers_only_when_more_than_one_pool_has_jobs(
    pool_srv, env, tree, capsys
):
    env(pool_srv)
    ok = submit(capsys, tree, "ok")
    code, out, _ = run(capsys, ["jobs"])
    assert code == 0 and f"#{ok}" in out
    assert pool_header_lines(out) == [], out  # 풀 하나 — 오늘과 같은 모양
    lin = submit(capsys, tree, "lin")
    code, out, _ = run(capsys, ["jobs"])
    assert code == 0
    heads = pool_header_lines(out)
    assert any(h.startswith("pool default") for h in heads), out
    assert any(h.startswith("pool linux") for h in heads), out
    # 각 잡은 자기 풀 헤더 아래에
    d, x = line_index(out, "pool default"), line_index(out, "pool linux")
    assert d < line_index(out, f"#{ok}") and line_index(out, f"#{ok}") < x, out
    assert x < line_index(out, f"#{lin}"), out
    # 풀 하나로 거르면 헤더가 다시 사라진다
    code, out, _ = run(capsys, ["jobs", "--pool", "linux"])
    assert code == 0 and pool_header_lines(out) == [], out


# ── rcm top ──────────────────────────────────────────────────────────────────


def test_top_shows_a_pool_header_only_when_a_non_default_pool_has_jobs(pool_srv, env, tree, capsys):
    env(pool_srv)
    ok = submit(capsys, tree, "ok")
    code, out, _ = run(capsys, ["top"])
    assert code == 0 and f"#{ok}" in out
    assert "(pool " not in out, out  # 기본 풀만 — 오늘과 같은 화면
    assert "queue — 1 jobs · 0 running · 1 waiting" in out, out
    lin = submit(capsys, tree, "lin")
    code, out, _ = run(capsys, ["top"])
    assert code == 0
    assert "queue — 1 (pool linux" in out, out
    assert "(pool linux · no workers)" in out, out  # 이 서버엔 linux 워커가 없다(lanes 0)
    assert "queue — 1 jobs · 0 running · 1 waiting" in out, out  # 기본 풀 절은 그대로
    assert line_index(out, "(pool linux") < line_index(out, f"#{lin}"), out
    assert line_index(out, f"#{ok}") < line_index(out, "(pool linux"), out
    # --json: 풀이 이름별로 따로, linux 는 lanes 0 · 워커 없음 이유
    code, out, _ = run(capsys, ["top", "--json"])
    assert code == 0
    pools = {p["name"]: p for p in json.loads(out)["pools"]}
    assert {"default", "linux"} <= set(pools), pools.keys()
    assert pools["linux"]["lanes"] == 0
    assert [r["id"] for r in pools["linux"]["queue"]] == [lin]
    assert pools["linux"]["queue"][0]["reason"] == "worker_down"
    assert [r["id"] for r in pools["default"]["queue"]] == [ok]


# ── rcm eta [--pool] ─────────────────────────────────────────────────────────


def test_eta_json_carries_the_pool_and_counts_only_that_pool(pool_srv, env, tree, capsys):
    env(pool_srv)
    submit(capsys, tree, "ok")
    submit(capsys, tree, "bad")
    submit(capsys, tree, "lin")
    code, out, _ = run(capsys, ["eta", "lin", "--json"])
    assert code == 0
    doc = json.loads(out)
    assert doc["job"]["pool"] == "linux"
    assert doc["job"]["position"] == 2, doc  # linux 에 하나만 앞선다
    assert doc.get("ahead", 1) == 1, doc
    code, out, _ = run(capsys, ["eta", "ok", "--json"])
    assert code == 0
    doc = json.loads(out)
    assert doc["job"]["pool"] == "default" and doc["job"]["position"] == 3, doc
    # --pool 로 다른 허용 풀에 넣으면 그 풀에서 센다
    code, out, _ = run(capsys, ["eta", "lin", "--pool", "default", "--json"])
    assert code == 0
    doc = json.loads(out)
    assert doc["job"]["pool"] == "default" and doc["job"]["position"] == 3, doc
    code, out, _ = run(capsys, ["eta", "lin"])
    assert code == 0 and "2nd in line" in out, out
    code, _, err = run(capsys, ["eta", "ok", "--pool", "nope"])
    assert code == 2 and "pool" in err.lower(), err


# ── presets[].pool · pools (추가 키) ─────────────────────────────────────────


def test_status_presets_carry_pool_and_pools(pool_srv, env, capsys):
    env(pool_srv, token=None)  # 읽기는 토큰이 필요 없다
    code, out, _ = run(capsys, ["top", "--json"])
    assert code == 0
    presets = {p["name"]: p for p in json.loads(out)["presets"]}
    assert presets["lin"]["pool"] == "linux"
    assert presets["lin"]["pools"] == ["default"], presets["lin"]  # 추가 허용 풀만(자기 풀은 규칙)
    assert presets["ok"]["pool"] == "default"
    assert presets["ok"]["pools"] == []


# ── live: 로컬 워커는 풀 default 만 · 완료 행도 풀을 싣는다 ───────────────────


def test_local_worker_never_runs_a_linux_job(pool_live, env, tree, capsys):
    env(pool_live)
    lin = submit(capsys, tree, "lin")
    time.sleep(1.0)  # 로컬 워커(default)가 집어 갔다면 이미 running/끝났을 시간
    assert pool_live.store.get_job(lin).state == "queued"
    code, out, _ = run(capsys, ["top"])
    assert code == 0 and "(pool linux · no workers)" in out, out
    pool_live.req("POST", f"/jobs/{lin}/cancel", token="alice", json_body={})
    pool_live.wait_terminal(lin)


def test_recent_rows_carry_pool(pool_live, env, tree, capsys):
    env(pool_live)
    code, out, err = run(capsys, ["run", "ok", "--dir", str(tree)])
    assert code == 0, err
    jid = int(last_json(out)["job_id"])
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    row = row_of(json.loads(out), jid)
    assert row["state"] == "succeeded" and row["pool"] == "default", row
