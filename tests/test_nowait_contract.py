"""CLI — `rcm run --no-wait` 의 **출력 계약**: stdout JSON · stderr 한 줄 · 순번 경계.

명세는 `docs/nowait-workplan.md` §3(JSON 의 키와 순서) · §4(한 줄의 모양) · §9(완료 기준 1~3).
격리 검증 A 다 — 구현을 믿지 않고 밖에서 다시 잰다. 조회가 깨지거나 느릴 때(B)와 진짜 워커
e2e · 문서 대조(C)는 다른 문서가 맡는다. 시나리오는 `docs/nowait-test-scenarios-a.md`.

`Server(..., workers=False)` 는 낸 잡을 전부 `queued` 로 세워 둔다 — 순번이 시간에 안 흔들린다.
시간을 재는 단언은 하나도 없다.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from remote_ci_monitor.cli import _submitted_line, describe
from remote_ci_monitor.client import Client
from remote_ci_monitor.core.render_text import fmt_clock
from test_cli_m1 import last_json, run
from test_server import Server
from test_server_m3 import build_bare_repo, git_server

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

#: 명세 §3 의 키 순서. 순번 다섯은 조회한 문서에서 온다.
QUEUE_KEYS = ["position", "reason", "ahead_job_id", "blocked_by", "estimate"]
TREE_KEYS = ["job_id", "joined", "state", *QUEUE_KEYS, "url"]
GIT_KEYS = ["job_id", "joined", "state", *QUEUE_KEYS, "ref", "sha", "url"]

FINISH = "2026-09-04T01:03:52Z"
URL = "http://macmini:8787/#/jobs/8"


# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_m5.env 와 같다 — `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""
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
    """워커가 없다 — 낸 잡은 전부 `queued` 로 줄을 선다."""
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True)
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


@pytest.fixture
def bare(tmp_path):
    return build_bare_repo(tmp_path)


@pytest.fixture
def git_srv(tmp_path, bare):
    s = git_server(tmp_path, bare)
    yield s
    s.close()


@pytest.fixture
def cwd(monkeypatch, tmp_path):
    """git_ref 는 스냅샷을 안 찍는다 — 찍으면 티가 나게 빈 디렉터리에서 돌린다."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    return empty


def no_wait(capsys, tree: Path, preset: str, *extra: str) -> tuple[int, dict, str]:
    """`rcm run <preset> --no-wait --dir <tree> …` → (종료 코드, JSON, stderr)."""
    code, out, err = run(capsys, ["run", preset, "--no-wait", "--dir", str(tree), *extra])
    return code, last_json(out), err


#: 잡을 **이름 붙이는** 줄. 업로드 진행 줄(`uploading #7: …`)도 `#7` 을 담지만 이건 아니다.
ID_LINE = re.compile(r"^(?:submitted|joined) job #\d+")


def id_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ID_LINE.match(ln)]


def id_line(err: str) -> str:
    """제출 줄 하나. 두 개면 명세 §4 의 「한 줄이 그 자리를 대신한다」가 깨진 것이다."""
    lines = id_lines(err)
    assert len(lines) == 1, err
    return lines[0]


def eta_text(finish_at: str | None) -> str:
    """CLI 가 쓰는 것과 같은 시계 문자열 — 로컬 tz 의 `HH:MM`."""
    return fmt_clock(finish_at, datetime.now().astimezone().tzinfo)


def queue_rows(server: Server) -> list[dict]:
    return server.req("GET", "/api/status")[1]["pools"][0]["queue"]


def row_of(rows: list[dict], job_id: int) -> dict:
    return next(r for r in rows if r["id"] == job_id)


# ── describe(head=) — 순수 단위 ──────────────────────────────────────────────

DOCS = {
    "queued": {
        "id": 413,
        "state": "queued",
        "position": 2,
        "reason": "blocked_by_group",
        "estimate": {"wait_seconds": 160, "finish_at": FINISH},
    },
    "running": {
        "id": 412,
        "state": "running",
        "position": None,
        "estimate": {"elapsed_seconds": 59, "finish_at": FINISH},
        "progress": {
            "phase": "executing",
            "steps": [{}],
            "steps_total": 8,
            "current_index": 5,
            "current_name": "test",
        },
    },
    "terminal": {"id": 1, "state": "failed", "summary": "2 tests failed", "estimate": {}},
}


@pytest.mark.parametrize("kind", sorted(DOCS))
def test_a_head_replaces_only_the_first_fragment_whatever_the_document_is(kind):
    """`head=` 는 맨 앞의 `#<id> <state>` 자리만 바꾼다 — 뒤는 기다리는 경로와 같은 코드다."""
    job = DOCS[kind]
    head = f"submitted job #9 {job['state']}"
    plain = describe(job).split(" · ")
    with_head = describe(job, head=head).split(" · ")
    assert plain[0] == f"#{job['id']} {job['state']}"
    assert with_head[0] == head
    assert with_head[1:] == plain[1:]  # 순번·스텝·경과·ETA·요약이 한 글자도 안 바뀐다


@pytest.mark.parametrize("position", [None, 0])
def test_a_job_without_a_position_says_nothing_about_a_line_or_a_reason(position):
    """순번이 없으면 조각이 통째로 빠진다 — `0th` 도, 순번에 딸린 이유도 안 나온다(§9-3)."""
    job = {
        "id": 7,
        "state": "running",
        "position": position,
        "reason": "blocked_by_group",
        "estimate": {"elapsed_seconds": 3},
    }
    assert describe(job, head="joined job #7 running") == "joined job #7 running · elapsed 3s"


@pytest.mark.parametrize(
    ("position", "text"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (101, "101st"),
        (111, "111th"),
        (112, "112th"),
    ],
)
def test_the_position_is_written_as_an_english_ordinal(position, text):
    job = {"id": 1, "state": "queued", "position": position}
    assert (
        describe(job, head="submitted job #1 queued") == f"submitted job #1 queued · {text} in line"
    )


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("waiting_for_lane", None),  # 「줄 서 있다」는 순번이 이미 말했다
        ("uploading", None),
        (None, None),
        ("blocked_by_group", "blocked by group"),
        ("worker_down", "worker down"),
    ],
)
def test_only_a_reason_worth_reading_is_added_after_the_position(reason, text):
    job = {"id": 5, "state": "queued", "position": 1, "reason": reason, "estimate": {}}
    tail = f" · {text}" if text else ""
    assert (
        describe(job, head="submitted job #5 queued")
        == f"submitted job #5 queued · 1st in line{tail}"
    )


def test_a_document_with_nothing_in_it_leaves_just_the_head():
    """조회한 문서에 아무 칸도 없어도 줄은 만들어진다 — 없는 값을 지어내지 않는다."""
    assert describe({}, head="submitted job #7 queued") == "submitted job #7 queued"
    assert (
        describe({"state": "queued"}, head="submitted job #7 queued") == "submitted job #7 queued"
    )
    assert describe({}) == "#None ?"  # head 를 안 주면 오늘 그대로


# ── stderr 한 줄의 모양 (명세 §4 의 예시 셋) ────────────────────────────────


def test_the_line_of_a_queued_job_is_the_documented_shape():
    """§4 첫째 예시: `submitted job #8 queued · 1st in line · wait 0s · eta … · <url>`."""
    view = {
        "id": 8,
        "state": "queued",
        "position": 1,
        "reason": "waiting_for_lane",
        "estimate": {"wait_seconds": 0, "finish_at": FINISH},
    }
    line = _submitted_line(8, view, joined=False, state="queued", url=URL)
    assert (
        line == f"submitted job #8 queued · 1st in line · wait 0s · eta {eta_text(FINISH)} · {URL}"
    )


def test_a_joined_line_puts_the_join_reason_between_the_estimate_and_the_url():
    """§4 둘째 예시. detail 은 URL 바로 앞이고, URL 은 언제나 줄의 끝이다."""
    view = {
        "id": 10,
        "state": "running",
        "position": None,
        "estimate": {"elapsed_seconds": 0, "finish_at": FINISH},
        "progress": {
            "phase": "executing",
            "steps": [{}],
            "steps_total": 4,
            "current_index": 1,
            "current_name": "fetch deps",
        },
    }
    line = _submitted_line(
        10, view, joined=True, state="running", url=URL, detail="same preset, inputs and tree"
    )
    assert line == (
        f"joined job #10 running · step 1/4 fetch deps · elapsed 0s · "
        f"eta {eta_text(FINISH)} · same preset, inputs and tree · {URL}"
    )


def test_a_new_git_ref_line_carries_the_preset_the_ref_and_the_sha_after_the_estimate():
    """§4 의 **모양 규칙**대로 detail 은 순번 조각 뒤·URL 앞이다.

    같은 절의 셋째 예시는 detail 을 `queued (deploy · main @a1b2c3d)` 처럼 상태 뒤 괄호에
    두었다 — 규칙과 어긋난다. 규칙 쪽을 잠근다(문서의 「명세에 대한 의견」 참조).
    """
    view = {
        "id": 12,
        "state": "queued",
        "position": 1,
        "reason": "waiting_for_lane",
        "estimate": {"wait_seconds": 0, "finish_at": FINISH},
    }
    line = _submitted_line(
        12, view, joined=False, state="queued", url=URL, detail="(deploy · main @a1b2c3d)"
    )
    assert line == (
        f"submitted job #12 queued · 1st in line · wait 0s · eta {eta_text(FINISH)} · "
        f"(deploy · main @a1b2c3d) · {URL}"
    )


# ── stdout JSON 계약 ─────────────────────────────────────────────────────────


def test_the_no_wait_json_has_the_documented_keys_in_the_documented_order(srv, env, tree, capsys):
    """§3: `job_id` · `joined` · `state` · 순번 다섯 · `url`. stdout 은 그 한 줄뿐이다."""
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    assert len(out.strip().splitlines()) == 1, out
    body = last_json(out)
    assert list(body) == TREE_KEYS, list(body)
    assert body["joined"] is False and body["state"] == "queued"
    assert body["position"] == 1 and body["ahead_job_id"] is None and body["blocked_by"] is None


def test_the_submitted_line_of_a_first_in_line_job_is_exactly_the_documented_shape(
    srv, env, tree, capsys
):
    """진짜 서버로 §4 첫째 예시를 통째로 맞춘다 — 조각의 순서까지."""
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    body = last_json(out)
    assert id_line(err) == (
        f"submitted job #{body['job_id']} queued · 1st in line · wait 0s · "
        f"eta {eta_text(body['estimate']['finish_at'])} · {body['url']}"
    )


def test_a_joined_tree_line_says_same_preset_inputs_and_tree_before_the_url(srv, env, tree, capsys):
    """합류한 세션도 **그 잡의** 순번을 보고, 왜 합류했는지가 URL 앞에 붙는다."""
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    env(srv, "bob")
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["joined"] is True and body["job_id"] == first["job_id"]
    assert list(body) == TREE_KEYS, list(body)
    assert id_line(err) == (
        f"joined job #{body['job_id']} queued · 1st in line · wait 0s · "
        f"eta {eta_text(body['estimate']['finish_at'])} · "
        f"same preset, inputs and tree · {body['url']}"
    )


def test_a_joiner_of_the_second_job_in_line_is_told_second_not_first(srv, env, tree, capsys):
    """합류자가 보는 건 **그 잡의** 순번이다 — 자기가 몇 번째로 왔는지가 아니다(§9-2)."""
    env(srv)
    no_wait(capsys, tree, "ok")  # 1번째
    _code, second, _err = no_wait(capsys, tree, "bad")  # 2번째
    env(srv, "bob")
    code, body, err = no_wait(capsys, tree, "bad")
    assert code == 0, err
    assert body["joined"] is True and body["job_id"] == second["job_id"]
    assert body["position"] == 2 and body["ahead_job_id"] != second["job_id"]
    assert "2nd in line" in id_line(err), err


def test_a_join_that_bumps_the_priority_reports_the_new_position(srv, env, tree, capsys):
    """합류는 잡의 우선순위를 올릴 수 있다(`join_or_bump`). 그러면 순번도 그 자리에서 바뀐다."""
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    _code, second, _err = no_wait(capsys, tree, "bad")
    env(srv, "admin")
    code, body, err = no_wait(capsys, tree, "bad", "--priority", "high")
    assert code == 0, err
    assert body["joined"] is True and body["job_id"] == second["job_id"]
    assert body["position"] == 1, body  # 합류하면서 앞으로 당겨졌다
    assert "1st in line" in id_line(err), err
    rows = queue_rows(srv)
    assert [row_of(rows, j["job_id"])["position"] for j in (second, first)] == [1, 2]


@needs_git
def test_a_git_ref_json_puts_ref_and_sha_between_the_estimate_and_the_url(
    git_srv, bare, env, cwd, capsys
):
    env(git_srv)
    code, out, err = run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])
    assert code == 0, err
    body = last_json(out)
    assert list(body) == GIT_KEYS, list(body)
    assert body["state"] == "queued" and body["position"] == 1
    assert body["ref"] == "main" and body["sha"] == bare.main_sha
    assert id_line(err) == (
        f"submitted job #{body['job_id']} queued · 1st in line · wait 0s · "
        f"eta {eta_text(body['estimate']['finish_at'])} · "
        f"(deploy · main @{bare.main_sha[:7]}) · {body['url']}"
    )


@needs_git
def test_a_joined_git_ref_reports_the_joined_jobs_position_and_names_the_commit(
    git_srv, bare, env, cwd, capsys
):
    """태그 이름으로 같은 커밋을 낸 세션도 그 잡의 순번을 본다. `ref` 는 **내가 낸** 이름이다."""
    env(git_srv)
    first = last_json(run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])[1])
    env(git_srv, "bob")
    code, out, err = run(capsys, ["run", "deploy", "--ref", "v1", "--no-wait"])
    assert code == 0, err
    body = last_json(out)
    assert body["joined"] is True and body["job_id"] == first["job_id"]
    assert list(body) == GIT_KEYS, list(body)
    assert body["state"] == "queued" and body["position"] == 1
    assert body["ref"] == "v1" and body["sha"] == bare.main_sha
    assert id_line(err) == (
        f"joined job #{body['job_id']} queued · 1st in line · wait 0s · "
        f"eta {eta_text(body['estimate']['finish_at'])} · "
        f"same preset, inputs, commit {bare.main_sha[:7]} · {body['url']}"
    )


# ── 순번 경계 ────────────────────────────────────────────────────────────────


def test_the_third_in_line_names_the_job_just_ahead_of_it_and_waits_for_both(
    srv, env, tree, capsys
):
    """세 번째 잡: 순번 3 · 앞선 잡은 **바로 앞**(맨 앞이 아니다) · 대기는 앞의 둘을 합친 값."""
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    _code, second, _err = no_wait(capsys, tree, "bad")
    code, body, err = no_wait(capsys, tree, "slow")
    assert code == 0, err
    assert body["position"] == 3
    assert body["ahead_job_id"] == second["job_id"] != first["job_id"]
    assert body["blocked_by"] is None
    line = id_line(err)
    assert "3rd in line" in line and "wait 20m 00s" in line, line  # 표본 없는 잡의 기본 600초 × 2
    rows = queue_rows(srv)
    assert [row_of(rows, j["job_id"])["position"] for j in (first, second, body)] == [1, 2, 3]


def test_a_running_job_takes_no_slot_so_the_next_job_is_first_in_line(live, env, tree, capsys):
    """순번은 **대기 잡만** 센다. 도는 잡은 `position: null` 이고 뒤 잡은 1 부터 시작한다."""
    env(live)
    _code, running, _err = no_wait(capsys, tree, "slow")
    live.wait_state(running["job_id"], "running")
    code, second, err2 = no_wait(capsys, tree, "ok")
    assert code == 0, err2
    assert second["position"] == 1 and second["ahead_job_id"] == running["job_id"]
    code, third, err3 = no_wait(capsys, tree, "bad")
    assert code == 0, err3
    assert third["position"] == 2
    assert "1st in line" in id_line(err2) and "2nd in line" in id_line(err3)
    rows = queue_rows(live)
    assert row_of(rows, running["job_id"])["position"] is None  # 도는 잡은 줄에서 세지 않는다
    assert [row_of(rows, j["job_id"])["position"] for j in (second, third)] == [1, 2]


def test_a_high_priority_job_reports_first_in_line_and_pushes_the_others_back(
    srv, env, tree, capsys
):
    """순번은 우선순위를 따른다 — admin 의 `--priority high` 는 줄 앞으로 간다(명세 §10)."""
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    _code, second, _err = no_wait(capsys, tree, "bad")
    env(srv, "admin")
    code, body, err = no_wait(capsys, tree, "slow", "--priority", "high")
    assert code == 0, err
    assert body["position"] == 1 and body["ahead_job_id"] is None
    line = id_line(err)
    assert "1st in line" in line and "wait 0s" in line, line
    assert "3rd" not in line, line
    rows = queue_rows(srv)
    assert [row_of(rows, j["job_id"])["position"] for j in (body, first, second)] == [1, 2, 3]


def test_a_job_that_finished_before_the_lookup_has_no_position_and_no_in_line_fragment(
    srv, env, tree, capsys, monkeypatch
):
    """업로드와 조회 사이에 잡이 끝날 수 있다(명세 §10 「경합」).

    그러면 지금 상태는 업로드 응답의 `queued` 가 아니라 **끝난 상태**이고, 순번 조각은 통째로
    빠진다. 조회 자체는 진짜 요청 그대로다 — 그 **직전**에 잡을 끝내 순간을 만들 뿐이다.
    """
    env(srv)
    real_job = Client.job

    def cancel_then_look(self, job_id, **kwargs):
        status, resp = srv.req("POST", f"/jobs/{job_id}/cancel", token="alice", json_body={})
        assert (status, resp["state"]) == (200, "cancelled"), resp
        return real_job(self, job_id, **kwargs)

    monkeypatch.setattr(Client, "job", cancel_then_look)
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["state"] == "cancelled"  # 조회한 문서가 업로드 응답을 이긴다
    assert body.get("position") is None and body.get("estimate") is None
    assert list(body)[:3] == ["job_id", "joined", "state"] and list(body)[-1] == "url"
    line = id_line(err)
    assert "in line" not in line and "0th" not in line, line
    assert line.startswith(f"submitted job #{body['job_id']} cancelled · "), line
    assert line.endswith(body["url"]), line
    # 끝난 잡의 문서에는 순번 칸이 **아예 없다** — 서버가 null 을 주는 게 아니다.
    # 명세 §3 은 「종료 잡은 서버가 position: null 을 준다」고 적었지만 그건 사실이 아니다.
    doc = srv.req("GET", f"/jobs/{body['job_id']}")[1]
    assert doc["state"] == "cancelled" and not [k for k in QUEUE_KEYS if k in doc], sorted(doc)


# ── 한 줄이 옛 두 줄을 대신한다 ─────────────────────────────────────────────


def test_no_wait_names_the_job_exactly_once_and_drops_the_old_identification_lines(
    srv, env, tree, capsys
):
    """§4: `--no-wait` 이면 옛 식별 줄(`submitted job #N · <url>` · `joined job #N (queued) — …`)은
    안 찍는다. 한 줄이 그 자리를 대신한다."""
    env(srv)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    assert len(id_lines(err)) == 1, err
    jid = last_json(out)["job_id"]
    assert f"submitted job #{jid} · " not in err, err  # 상태 없는 옛 제출 줄
    env(srv, "bob")
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    assert len(id_lines(err)) == 1, err
    assert re.search(r"^joined job #\d+ \(", err, re.M) is None, err  # 옛 합류 줄


@needs_git
def test_a_git_ref_no_wait_names_the_job_exactly_once(git_srv, env, cwd, capsys):
    env(git_srv)
    code, _out, err = run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])
    assert code == 0, err
    assert len(id_lines(err)) == 1, err
    assert re.search(r"^submitted job #\d+ \(", err, re.M) is None, err  # 옛 괄호 줄
    env(git_srv, "bob")
    code, _out, err = run(capsys, ["run", "deploy", "--ref", "v1", "--no-wait"])
    assert code == 0, err
    assert len(id_lines(err)) == 1, err
    assert re.search(r"^joined job #\d+ \(", err, re.M) is None, err
