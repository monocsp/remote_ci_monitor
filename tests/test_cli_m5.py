"""CLI(M5a) — `rcm run --priority` · `--no-cache` · `rcm bump` · `rcm top` 의 `↑`/`↓` ·
`rcm jobs --json` 의 `priority` · `rcm eta --priority` · 캐시 업로드의 stderr(`(cache N%)`) ·
헤더의 `cache N blobs`. 명세는 docs/m5-workplan.md M5a-1 · M5a-2 「클라이언트」 · M5a-3.

test_cli_m3 처럼 `main(argv)` 를 in-process 로 부르고 RCM_SERVER/RCM_TOKEN 만으로 서버를 가리킨다.
argparse 의 usage 오류(`--priority urgent` · 아직 없는 `bump` 하위 명령)는 `parse_args` 가 try
밖이라 `SystemExit(2)` 로 나오므로 test_cli_m4 처럼 `run` 이 받아 코드로 바꾼다.
구현보다 먼저 썼다(test-first).
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

import pytest

from remote_ci_monitor.cli import main
from remote_ci_monitor.client import Client
from remote_ci_monitor.config import parse_preset
from test_cli_m1 import last_json
from test_server import PRESETS, Server

# 비-admin 이 `--priority high` 를 쓸 수 있는 프리셋(프리셋 기본이 high). 명세 M5a-1 「어디서」.
HIGH_PRESET = {
    "name": "urgent",
    "argv": ["sh", "-c", "echo urgent"],
    "timeout_seconds": 60,
    "priority": "high",
}


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def queue_rows(server: Server) -> list[dict]:
    doc = server.req("GET", "/api/status")[1]
    return doc["pools"][0]["queue"]


def row_of(rows: list[dict], job_id: int) -> dict:
    return next(r for r in rows if r["id"] == job_id)


def top_line(out: str, job_id: int) -> str:
    """`rcm top` 출력에서 `#<id>` 가 있는 첫 줄(큐 행의 첫 줄)."""
    return next(ln for ln in out.splitlines() if f"#{job_id}" in ln)


def make_tree(root: Path, *, big: int = 300_000, small: str = "v1\n") -> Path:
    """압축이 안 되는 난수 파일 하나 + 작은 텍스트 하나. 캐시 히트 비율을 재기 위한 트리."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "big.bin").write_bytes(random.Random(7).randbytes(big))
    (root / "small.txt").write_text(small)
    return root


def refuse_submit(monkeypatch) -> list:
    """`Client.submit` 이 불리면 실패시키고 기록한다 — 「서버에 보내기 전에 2」 를 증명한다."""
    calls: list = []

    def refuse(self, *args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("rcm run must not submit after a usage error")

    monkeypatch.setattr(Client, "submit", refuse)
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


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True)
    yield s
    s.close()


@pytest.fixture
def cache_srv(tmp_path):
    """캐시 업로드용 — 난수 300 KB 트리가 들어가게 상한만 올린다(기본 Server 는 10 KB)."""
    s = Server(tmp_path, workers=False, max_snapshot_bytes=50_000_000)
    yield s
    s.close()


@pytest.fixture
def prio_srv(tmp_path):
    """기본 프리셋 + `priority = "high"` 인 `urgent` 프리셋(test_server_m3.make_server 의 요령)."""
    s = Server(tmp_path, workers=False)
    try:
        s.cfg.presets = tuple(parse_preset(p) for p in [*PRESETS, HIGH_PRESET])
    except BaseException:
        s.close()
        raise
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
    """`rcm run <preset> --no-wait --dir <tree> …` → job id. 제출·업로드까지 끝나 queued 다."""
    code, out, err = run(capsys, ["run", preset, "--no-wait", "--dir", str(tree), *extra])
    assert code == 0, err
    body = last_json(out)
    assert body["joined"] is False, body
    return int(body["job_id"])


# ── rcm run --priority ───────────────────────────────────────────────────────


def test_run_priority_defaults_to_normal_and_rows_carry_it(srv, env, tree, capsys):
    env(srv)
    jid = submit(capsys, tree, "ok")
    assert row_of(queue_rows(srv), jid)["priority"] == 0
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    assert row_of(json.loads(out), jid)["priority"] == 0
    # 명시적 normal 도 같다
    jid2 = submit(capsys, tree, "bad", "--priority", "normal")
    assert row_of(queue_rows(srv), jid2)["priority"] == 0


def test_run_priority_low_is_allowed_for_anyone(srv, env, tree, capsys):
    env(srv)
    jid = submit(capsys, tree, "ok", "--priority", "low")
    assert row_of(queue_rows(srv), jid)["priority"] == -1


def test_run_priority_high_above_the_preset_default_needs_an_admin_token(srv, env, tree, capsys):
    """비-admin 이 normal 프리셋에 high 를 달면 서버가 403 — 잡은 만들어지지 않고 exit 2."""
    env(srv, "alice")
    code, out, err = run(
        capsys, ["run", "ok", "--no-wait", "--dir", str(tree), "--priority", "high"]
    )
    assert code == 2, err
    assert "admin" in err.lower(), err
    assert out.strip() == ""
    assert queue_rows(srv) == []
    # admin 토큰이면 된다
    env(srv, "admin")
    jid = submit(capsys, tree, "ok", "--priority", "high")
    assert row_of(queue_rows(srv), jid)["priority"] == 1


def test_run_priority_high_on_a_high_preset_is_allowed_for_non_admin(prio_srv, env, tree, capsys):
    env(prio_srv, "alice")
    jid = submit(capsys, tree, "urgent", "--priority", "high")
    assert row_of(queue_rows(prio_srv), jid)["priority"] == 1
    # 세션이 낮추는 건 언제나 된다
    jid2 = submit(capsys, tree, "urgent", "--priority", "low", "--no-join")
    assert row_of(queue_rows(prio_srv), jid2)["priority"] == -1


def test_run_priority_invalid_word_is_a_usage_error_before_any_submit(
    srv, env, tree, capsys, monkeypatch
):
    env(srv)
    calls = refuse_submit(monkeypatch)
    code, out, err = run(
        capsys, ["run", "ok", "--no-wait", "--dir", str(tree), "--priority", "urgent"]
    )
    assert code == 2 and "priority" in err, err
    assert out.strip() == "" and calls == []
    assert queue_rows(srv) == []


# ── rcm top: ↑ / ↓ · rcm jobs: position follows priority ─────────────────────


def test_top_marks_high_and_low_rows_and_positions_follow_priority(srv, env, tree, capsys):
    env(srv, "alice")
    normal = submit(capsys, tree, "ok")
    low = submit(capsys, tree, "slow", "--priority", "low")
    env(srv, "admin")
    high = submit(capsys, tree, "bad", "--priority", "high")
    code, out, _ = run(capsys, ["top"])
    assert code == 0
    hi_line, no_line, lo_line = top_line(out, high), top_line(out, normal), top_line(out, low)
    # 화살표는 잡 id 앞(행 앞)에. 세 잡 다 queued 라 업로드 글리프 `↑` 와 섞이지 않는다.
    assert "↑" in hi_line and hi_line.index("↑") < hi_line.index(f"#{high}"), hi_line
    assert "↓" not in hi_line
    assert "↓" in lo_line and lo_line.index("↓") < lo_line.index(f"#{low}"), lo_line
    assert "↑" not in lo_line
    assert "↑" not in no_line and "↓" not in no_line, no_line
    # 순번: high → normal → low (정렬 키 (-priority, id))
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    rows = json.loads(out)
    assert row_of(rows, high)["position"] == 1
    assert row_of(rows, normal)["position"] == 2
    assert row_of(rows, low)["position"] == 3
    assert [row_of(rows, j)["priority"] for j in (high, normal, low)] == [1, 0, -1]


# ── rcm bump ─────────────────────────────────────────────────────────────────


def test_bump_high_moves_a_waiting_job_ahead(srv, env, tree, capsys):
    env(srv, "alice")
    first = submit(capsys, tree, "ok")
    env(srv, "admin")
    high = submit(capsys, tree, "bad", "--priority", "high")
    assert row_of(queue_rows(srv), high)["position"] == 1
    code, out, err = run(capsys, ["bump", str(first), "--priority", "high"])
    assert code == 0, err
    body = last_json(out)
    assert body["job_id"] == first and body["priority"] == 1
    rows = queue_rows(srv)
    assert row_of(rows, first)["priority"] == 1
    # 같은 high 끼리는 id 순 — 먼저 들어온 `first` 가 앞
    assert row_of(rows, first)["position"] == 1 and row_of(rows, high)["position"] == 2
    # 다시 낮출 수도 있다
    code, out, _ = run(capsys, ["bump", str(first), "--priority", "low"])
    assert code == 0 and last_json(out)["priority"] == -1
    assert row_of(queue_rows(srv), first)["position"] == 2


def test_bump_needs_an_admin_token(srv, env, tree, capsys):
    env(srv, "alice")
    jid = submit(capsys, tree, "ok")
    code, out, err = run(capsys, ["bump", str(jid), "--priority", "high"])
    assert code == 2, err
    assert "admin" in err.lower() and out.strip() == "", err
    assert row_of(queue_rows(srv), jid)["priority"] == 0
    env(srv, token=None)
    code, _, err = run(capsys, ["bump", str(jid), "--priority", "high"])
    assert code == 2 and "token" in err


def test_bump_running_job_is_refused_with_409(live, env, tree, capsys):
    """대기 잡만 — running 은 서버 409. cancel 의 409 처럼 `bump failed: <서버 문구>` + exit 2."""
    env(live, "alice")
    jid = submit(capsys, tree, "slow")
    live.wait_state(jid, "running")
    env(live, "admin")
    try:
        code, out, err = run(capsys, ["bump", str(jid), "--priority", "high"])
        assert code == 2, err
        assert "bump" in err.lower() and out.strip() == "", err
        assert re.search(r"409|not waiting|running", err), err
        assert live.req("GET", f"/jobs/{jid}")[1].get("priority", 0) == 0
    finally:
        live.req("POST", f"/jobs/{jid}/cancel", token="admin", json_body={})
        live.wait_terminal(jid)


def test_bump_unknown_job_is_2_and_bad_priority_is_usage(srv, env, capsys):
    env(srv, "admin")
    code, out, err = run(capsys, ["bump", "999", "--priority", "high"])
    assert code == 2 and out.strip() == "" and "job" in err.lower(), err
    code, out, err = run(capsys, ["bump", "1", "--priority", "urgent"])
    assert code == 2 and out.strip() == "" and "priority" in err, err


# ── rcm eta --priority ───────────────────────────────────────────────────────


def test_eta_priority_counts_only_jobs_at_or_above_that_priority(srv, env, tree, capsys):
    env(srv, "alice")
    first = submit(capsys, tree, "ok")
    submit(capsys, tree, "bad")
    code, out, _ = run(capsys, ["eta", "ok"])
    assert code == 0 and "3rd in line" in out, out
    code, out, _ = run(capsys, ["eta", "ok", "--priority", "high"])
    assert code == 0 and "1st in line" in out and "0 ahead" in out, out
    code, out, _ = run(capsys, ["eta", "ok", "--priority", "low"])
    assert code == 0 and "3rd in line" in out, out
    env(srv, "admin")
    assert run(capsys, ["bump", str(first), "--priority", "high"])[0] == 0
    code, out, _ = run(capsys, ["eta", "ok", "--priority", "high", "--json"])
    assert code == 0
    doc = json.loads(out)
    row = doc.get("job", doc)
    assert row["position"] == 2 and doc.get("ahead", 1) == 1, doc
    code, out, _ = run(capsys, ["eta", "ok", "--priority", "normal"])
    assert code == 0 and "3rd in line" in out, out
    code, _, err = run(capsys, ["eta", "ok", "--priority", "urgent"])
    assert code == 2 and "priority" in err


# ── 캐시 업로드: stderr 의 (cache N%) · --no-cache ───────────────────────────


def test_second_upload_of_a_changed_tree_reports_the_cache_hit_ratio(
    cache_srv, env, tmp_path, capsys
):
    env(cache_srv, "alice")
    root = make_tree(tmp_path / "tree")
    first = submit(capsys, root, "ok")
    assert cache_srv.store.get_job(first).state == "queued"
    # 작은 파일만 바뀐다 — 난수 300 KB 는 서버 blob 에 이미 있다
    make_tree(root, small="v2\n")
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(root)])
    assert code == 0, err
    second = int(last_json(out)["job_id"])
    assert second != first
    m = re.search(r"cache (\d+)%", err)
    assert m, err  # `uploading #N: 0.0 / 0.3 MB (cache 99%)`
    assert int(m.group(1)) >= 50, err
    assert "uploading" in err.lower(), err
    src = cache_srv.req("GET", f"/jobs/{second}")[1]["source"]
    assert src["cached_bytes"] > 0, src
    assert src["uploaded_bytes"] < src["bytes"], src
    assert cache_srv.store.get_job(second).state == "queued"


def test_no_cache_uploads_the_full_tar_and_says_nothing_about_cache(
    cache_srv, env, tmp_path, capsys
):
    env(cache_srv, "alice")
    root = make_tree(tmp_path / "tree")
    submit(capsys, root, "ok")
    code, out, err = run(
        capsys, ["run", "ok", "--no-wait", "--no-cache", "--no-join", "--dir", str(root)]
    )
    assert code == 0, err
    jid = int(last_json(out)["job_id"])
    assert "uploading" in err.lower() and "cache" not in err.lower(), err
    src = cache_srv.req("GET", f"/jobs/{jid}")[1]["source"]
    assert not src.get("cached_bytes"), src  # 전체 tar 경로 — 캐시 히트가 없다
    assert cache_srv.store.get_job(jid).state == "queued"


def test_server_with_cache_off_makes_the_client_upload_the_full_tar(tmp_path, env, capsys):
    s = Server(tmp_path, workers=False, max_snapshot_bytes=50_000_000, snapshot_cache=False)
    try:
        env(s, "alice")
        root = make_tree(tmp_path / "tree")
        submit(capsys, root, "ok")
        make_tree(root, small="v2\n")
        code, _, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(root)])
        assert code == 0, err
        assert "uploading" in err.lower() and "cache" not in err.lower(), err
    finally:
        s.close()


# ── rcm top: 헤더의 cache · notify failures · --json 의 추가 키 ──────────────


def test_top_header_shows_cache_blobs_and_size(cache_srv, env, tmp_path, capsys):
    env(cache_srv, "alice")
    code, out, _ = run(capsys, ["top", "--json"])
    assert code == 0
    cache = json.loads(out)["server"]["snapshot_cache"]
    assert cache["blobs"] == 0 and cache["bytes"] == 0  # 새 서버 — 아직 blob 없음(0 은 진짜 0 이다)
    submit(capsys, make_tree(tmp_path / "tree"), "ok")
    code, out, _ = run(capsys, ["top", "--json"])
    assert code == 0
    cache = json.loads(out)["server"]["snapshot_cache"]
    assert cache["blobs"] >= 1 and cache["bytes"] >= 300_000, cache
    code, out, _ = run(capsys, ["top"])
    assert code == 0
    head = out.split("queue", 1)[0]
    assert re.search(r"cache \d+ blobs · \d+(\.\d+)? MB", head), head


def test_top_json_carries_notify_failures_and_text_hides_zero(srv, env, capsys):
    env(srv, token=None)  # 읽기는 토큰이 필요 없다
    code, out, _ = run(capsys, ["top", "--json"])
    assert code == 0
    assert json.loads(out)["server"]["notify_failures"] == 0
    code, out, _ = run(capsys, ["top"])
    assert code == 0 and "notify failures" not in out


# ── 회귀: 우선순위·캐시 키가 붙어도 기존 wait 흐름은 그대로 ─────────────────


def test_run_high_job_still_waits_and_exits_with_the_job_code(live, env, tree, capsys):
    env(live, "admin")
    t0 = time.monotonic()
    code, out, err = run(capsys, ["run", "ok", "--dir", str(tree), "--priority", "high"])
    assert code == 0, err
    body = last_json(out)
    assert body["state"] == "succeeded" and body["wait_exit_code"] == 0
    assert body.get("priority", 1) == 1
    assert time.monotonic() - t0 < 30


# ── `_StatusLine` — 진짜 터미널이면 한 줄을 덮어쓴다(M5a 의 `stream=None` 기본값 회귀) ────────


class _Tty:
    def __init__(self, tty: bool):
        self._tty = tty
        self.chunks: list[str] = []

    def isatty(self) -> bool:
        return self._tty

    def write(self, text: str) -> None:
        self.chunks.append(text)

    def flush(self) -> None:
        pass


def test_status_line_overwrites_on_a_tty_and_appends_lines_otherwise(monkeypatch):
    from remote_ci_monitor import cli as cli_mod

    tty = _Tty(True)
    monkeypatch.setattr(cli_mod.sys, "stderr", tty)
    line = cli_mod._StatusLine()  # 기본값 — 호출 시점의 stderr 를 본다
    line.update("uploading #1: 0.0 / 30.0 MB (0%)")
    line.update("uploading #1: 30.0 / 30.0 MB (cache 0%)")
    line.done()
    assert line.tty is True
    assert tty.chunks[0].startswith("\r\x1b[2K") and tty.chunks[1].startswith("\r\x1b[2K")
    assert tty.chunks[-1] == "\n"

    plain = _Tty(False)
    monkeypatch.setattr(cli_mod.sys, "stderr", plain)
    clock = iter([100.0, 100.5, 100.6])  # 두 번째 갱신은 1초 안 — 마지막 줄은 done() 이 찍는다
    line = cli_mod._StatusLine(clock=lambda: next(clock))
    line.update("uploading #2: 0.0 / 30.0 MB (0%)")
    line.update("uploading #2: 0.0 / 30.0 MB (cache 99%)")
    line.done()
    assert line.tty is False
    assert "".join(plain.chunks).splitlines() == [
        "uploading #2: 0.0 / 30.0 MB (0%)",
        "uploading #2: 0.0 / 30.0 MB (cache 99%)",
    ]
