"""CLI(M5h) — 실패한 대기의 끝줄 · `rcm jobs` 의 코드 신원과 `--ref` · 대기 전에 놓는 스냅샷.

명세는 `docs/m5h-implementation.md` §2.5(`cli._wait` 의 끝줄) · §4.1·§4.3(`rcm jobs`) ·
§4.2(제출하는 `source.branch`) · §5(`cmd_run` 이 대기 전에 장부를 놓는다).
**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

세 가지 방식으로 잰다:

- 끝줄은 `cli.wait_for_job` 을 대본으로 갈아 끼우고 `cli._wait` 을 직접 부른다 — 서버도 잡도
  필요 없고 종료 코드 1·2·3 을 **정해서** 준다.
- `rcm jobs` 는 `Client.status`(와 `--mine` 의 `Client.whoami`)만 갈아 끼운다. 행은 손으로 쓴
  스키마 v1 문서라 순번도 시각도 안 흔들린다.
- `rcm run` 은 진짜 in-process 서버(`test_server.Server(workers=False)`)에 진짜 git 체크아웃을
  올린다. 스냅샷이 놓였는지는 RSS 가 아니라 **약한 참조 + `gc.collect()`** 로 본다(§4.6 —
  RSS 는 CI 에서 결정적이지 않다).
"""

from __future__ import annotations

import gc
import json
import weakref
from pathlib import Path
from typing import Any

import pytest

from gitrepo import commit, git
from remote_ci_monitor import cli
from remote_ci_monitor.client import Client
from test_server import Server

# ── 도우미 ───────────────────────────────────────────────────────────────────

URL = "http://macmini:8787/#/jobs/162"


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit 도 코드로 돌려준다."""
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def last_json(out: str) -> Any:
    """stdout 의 마지막 JSON 줄. `rcm jobs --json` 은 배열이라 `{` 로 안 시작한다."""
    lines = [ln for ln in out.strip().splitlines() if ln[:1] in "[{"]
    assert lines, f"no JSON line in stdout: {out!r}"
    return json.loads(lines[-1])


def fail_item(name: str, *, seen: int, verdict: str, window: int = 8, unnamed: int = 0) -> dict:
    return {
        "name": name,
        "step": True,
        "seen": seen,
        "window": window,
        "window_unnamed": unnamed,
        "first_seen_job_id": 141,
        "last_seen_job_id": 162,
        "verdict": verdict,
    }


def job_doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": 162,
        "state": "failed",
        "exit_code": 1,
        "key": "gate",
        "summary": "exit 1",
        "url": URL,
    }
    doc.update(over)
    return doc


def scripted_wait(monkeypatch, *, code: int, job: dict[str, Any] | None) -> None:
    """`wait_for_job` 을 대본으로. `_wait` 이 그 뒤에 무엇을 찍는지만 잰다."""

    def fake(client, job_id, **kwargs):
        return code, job, None

    monkeypatch.setattr(cli, "wait_for_job", fake)


def wait_once(monkeypatch, capsys, *, code: int, job: dict[str, Any] | None):
    scripted_wait(monkeypatch, code=code, job=job)
    rc = cli._wait(object(), 162, timeout=None, joined=False, use_sse=False)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


TREE_SOURCE = {
    "mode": "tree",
    "repo": "git@github.com:org/app",
    "base_sha": "25e1494ab0f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
    "dirty": False,
    "branch": "chore/ci-guard",
    "tree_hash": "9f" * 32,
    "bytes": 100,
}
REF_SOURCE = {"mode": "git_ref", "repo": "org/app", "ref": "main", "sha": "25e1494ab0f1c2d3"}


def status_row(job_id: int, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": job_id,
        "pool": "default",
        "state": "failed",
        "key": "gate",
        "requester": {"name": "macbook", "label": "macbook@PCS"},
        "joiners": [],
        "summary": "exit 1",
        "source": dict(TREE_SOURCE),
        "estimate": {},
        "job_seconds": 660.0,
        "finished_at": "2026-09-04T00:50:00Z",
    }
    row.update(over)
    return row


def status_doc(recent: list[dict[str, Any]], queue: list[dict[str, Any]] | None = None) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-09-04T00:52:12Z",
        "server": {},
        "pools": [
            {
                "name": "default",
                "queue": queue if queue is not None else [],
                "recent": recent,
                "medians": {},
                "hosts": [],
            }
        ],
    }


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """서버 주소·토큰만 있는 환경. 모든 HTTP 는 갈아 끼운다 — 연결이 안 일어난다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:9")
    monkeypatch.setenv("RCM_TOKEN", "t0ken")

    def serve(doc: dict[str, Any], *, me: str = "macbook") -> None:
        monkeypatch.setattr(Client, "status", lambda self, **kw: doc)
        monkeypatch.setattr(Client, "whoami", lambda self, **kw: {"name": me})

    return serve


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_m1.env 와 같다 — 진짜 서버를 환경변수로 가리킨다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)

    def use(server: Server, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def srv(tmp_path):
    """워커가 없다 — 낸 잡은 queued 로 선다. 대기는 갈아 끼우므로 안 기다린다."""
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def checkout(tmp_path) -> Path:
    """작은 git 체크아웃(브랜치 `main`). `rcm run --dir` 이 스냅샷을 만드는 트리다."""
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", cwd=root)
    commit(root, "hello.txt", "hello\n", "first")
    return root


def failure_line(err: str, prefix: str) -> str | None:
    for line in err.splitlines():
        body = line[len("rcm: ") :] if line.startswith("rcm: ") else line
        if body.startswith(prefix):
            return body
    return None


# ── F. 실패한 대기의 끝줄 (§2.5) ─────────────────────────────────────────────


def test_a_failed_wait_ends_with_the_log_path_and_the_named_history(monkeypatch, capsys):
    """§5 의 화면 그대로 — 길 하나와 이름별 이력. stdout 의 JSON 은 그대로 남는다."""
    rows = [
        fail_item("test", seen=8, verdict="persistent"),
        fail_item("port_test.dart", seen=1, verdict="first_seen"),
    ]
    code, out, err = wait_once(monkeypatch, capsys, code=1, job=job_doc(failures=rows))
    assert code == 1
    assert failure_line(err, "log:") == f"log: rcm logs 162 · {URL}"
    assert failure_line(err, "failed: test") == "failed: test — every one of the last 8 gate runs"
    assert failure_line(err, "failed: port_test.dart") == (
        "failed: port_test.dart — first time in the last 8 gate runs"
    )
    assert last_json(out)["wait_exit_code"] == 1


@pytest.mark.parametrize("code", [1, 2, 3])
def test_every_non_zero_exit_gets_the_log_path(monkeypatch, capsys, code):
    """모를수록 로그가 필요하다(결정 70) — 2(취소·시간 초과)와 3(모름)도 길을 받는다."""
    states = {1: "failed", 2: "timed_out", 3: "lost"}
    doc = job_doc(state=states[code], exit_code=None if code == 3 else code)
    rc, _out, err = wait_once(monkeypatch, capsys, code=code, job=doc)
    assert rc == code
    assert failure_line(err, "log:") == f"log: rcm logs 162 · {URL}"


def test_a_green_wait_says_nothing_about_logs(monkeypatch, capsys):
    """성공한 잡의 끝줄은 없다 — 초록에 잡음을 얹지 않는다."""
    doc = job_doc(state="succeeded", exit_code=0, summary="green")
    code, out, err = wait_once(monkeypatch, capsys, code=0, job=doc)
    assert code == 0
    assert "rcm logs" not in err
    assert last_json(out)["wait_exit_code"] == 0


def test_a_job_document_without_failures_still_gets_the_log_path(monkeypatch, capsys):
    """대장을 못 읽은 서버(키 없음)나 옛 서버라도 길은 안 잃는다 — 이름 줄만 없다."""
    _code, _out, err = wait_once(monkeypatch, capsys, code=1, job=job_doc())
    assert failure_line(err, "log:") == f"log: rcm logs 162 · {URL}"
    assert failure_line(err, "failed:") is None


def test_the_end_lines_come_before_the_json_line(monkeypatch, capsys):
    """한 시간축으로 본다 — 래퍼가 stdout 마지막 줄을 JSON 으로 읽는다(§5 「jq 로 읽는다」)."""
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(cli, "_err", lambda msg: events.append(("err", msg)))
    monkeypatch.setattr(cli, "_print_json", lambda obj: events.append(("json", obj)))
    rows = [fail_item("test", seen=8, verdict="persistent")]
    scripted_wait(monkeypatch, code=1, job=job_doc(failures=rows))
    cli._wait(object(), 162, timeout=None, joined=False, use_sse=False)
    kinds = [k for k, _ in events]
    assert kinds.count("json") == 1
    assert kinds.index("json") == len(events) - 1
    assert any(k == "err" and str(v).startswith("log: rcm logs 162") for k, v in events)


def test_the_url_comes_from_the_job_document_and_may_be_absent(monkeypatch, capsys):
    """서버 주소를 문서가 안 주면 명령만 적는다 — 빈 `· ` 꼬리를 남기지 않는다."""
    doc = job_doc()
    doc.pop("url")
    _code, _out, err = wait_once(monkeypatch, capsys, code=1, job=doc)
    assert failure_line(err, "log:") == "log: rcm logs 162"


def test_the_history_stops_at_three_names_and_sends_the_rest_to_the_log(monkeypatch, capsys):
    """끝줄은 요약이다 — 스무 개를 쏟지 않고 로그로 보낸다."""
    rows = [fail_item(n, seen=2, verdict="intermittent") for n in ("a", "b", "c", "d", "e")]
    _code, _out, err = wait_once(monkeypatch, capsys, code=1, job=job_doc(failures=rows))
    named = [ln for ln in err.splitlines() if "failed: " in ln]
    assert len(named) == 3
    assert failure_line(err, "…") == "… and 2 more (rcm logs 162)"


# ── G. `rcm jobs` — 코드 신원과 `--ref` (§4.1 · §4.3) ────────────────────────


def test_a_jobs_row_says_which_code_ran(offline, capsys):
    """오늘의 한 줄에는 코드 신원이 없다 — sha 로 자기 잡을 찾을 수 없다(§3.2)."""
    offline(status_doc([status_row(162)]))
    code, out, err = run(capsys, ["jobs"])
    assert code == 0, err
    line = next(ln for ln in out.splitlines() if ln.startswith("#162"))
    assert "chore/ci-guard @25e1494" in line
    assert line.index("macbook@PCS") < line.index("chore/ci-guard @25e1494")
    assert line.index("chore/ci-guard @25e1494") < line.index("took")


def test_a_git_ref_row_shows_the_ref_and_an_unknown_source_shows_a_dash(offline, capsys):
    """칸은 사라지지 않는다 — 모르면 `—` 다."""
    rows = [status_row(163, source=dict(REF_SOURCE)), status_row(164, source={})]
    offline(status_doc(rows))
    _code, out, _err = run(capsys, ["jobs"])
    lines = {ln[:4]: ln for ln in out.splitlines() if ln.startswith("#")}
    assert "main @25e1494" in lines["#163"]
    assert "—" in lines["#164"]


def test_ref_keeps_only_rows_whose_branch_or_ref_contains_the_value(offline, capsys):
    """토큰 하나를 여러 세션이 나눠 쓰는 배치에서 「내 잡」에 가장 가까운 필터다(§4.3)."""
    rows = [
        status_row(162),  # tree · branch chore/ci-guard
        status_row(163, source=dict(REF_SOURCE)),  # git_ref · ref main
        status_row(164, source={**TREE_SOURCE, "branch": "dev"}),
    ]
    offline(status_doc(rows))
    _code, out, _err = run(capsys, ["jobs", "--ref", "ci-guard"])
    assert "#162" in out and "#163" not in out and "#164" not in out
    _code, out, _err = run(capsys, ["jobs", "--ref", "ain"])  # 부분 일치 — git_ref 의 ref 도 본다
    assert "#163" in out and "#162" not in out


def test_ref_is_case_sensitive(offline, capsys):
    """대소문자를 구분한다(§4.3) — 브랜치 이름은 대소문자가 다른 것을 다른 것으로 센다."""
    offline(status_doc([status_row(162)]))
    _code, out, _err = run(capsys, ["jobs", "--ref", "CI-GUARD"])
    assert "#162" not in out and "no jobs" in out


def test_ref_combines_with_state(offline, capsys):
    rows = [
        status_row(162),
        status_row(165, state="succeeded", summary="green"),
        status_row(166, source={**TREE_SOURCE, "branch": "dev"}),
    ]
    offline(status_doc(rows))
    _code, out, _err = run(capsys, ["jobs", "--ref", "ci-guard", "--state", "failed"])
    assert "#162" in out and "#165" not in out and "#166" not in out


def test_ref_combines_with_mine(offline, capsys):
    rows = [
        status_row(162),
        status_row(167, requester={"name": "other", "label": "bob@desk"}),
    ]
    offline(status_doc(rows), me="macbook")
    _code, out, _err = run(capsys, ["jobs", "--ref", "ci-guard", "--mine"])
    assert "#162" in out and "#167" not in out


def test_ref_filters_the_json_output_too(offline, capsys):
    """`--json` 은 걸러진 뒤의 행이다 — 사람 눈과 스크립트가 같은 목록을 본다."""
    rows = [status_row(162), status_row(164, source={**TREE_SOURCE, "branch": "dev"})]
    offline(status_doc(rows))
    _code, out, _err = run(capsys, ["jobs", "--ref", "dev", "--json"])
    rows_out = last_json(out)
    assert isinstance(rows_out, list) and [r["id"] for r in rows_out] == [164]


def test_without_ref_nothing_is_filtered(offline, capsys):
    """회귀 잠금 — `--ref` 를 안 주면 오늘 그대로 전부 나온다."""
    rows = [status_row(162), status_row(163, source=dict(REF_SOURCE))]
    offline(status_doc(rows))
    _code, out, _err = run(capsys, ["jobs"])
    assert "#162" in out and "#163" in out


def test_the_ref_help_says_what_it_matches(capsys):
    """도움말 문구는 계약이다(§4.3). 줄바꿈은 폭에 따라 달라지므로 공백을 눌러서 본다."""
    code, out, _err = run(capsys, ["jobs", "--help"])
    assert code == 0
    assert "only jobs whose ref or branch contains REF" in " ".join(out.split())
    assert cli.build_parser().parse_args(["jobs", "--ref", "main"]).ref == "main"


# ── H. `rcm run` — 브랜치를 싣고 대기 전에 장부를 놓는다 (§4.2 · §5) ─────────


def test_a_tree_submit_carries_the_branch(srv, env, checkout, capsys, monkeypatch):
    """표시용 칸 하나 — 서버가 그것으로 합류를 가르지는 않는다(불변식은 test_client_m5h)."""
    env(srv)
    seen: list[dict[str, Any]] = []
    original = Client.submit

    def recording(self, preset, inputs, source, **kw):
        seen.append(dict(source))
        return original(self, preset, inputs, source, **kw)

    monkeypatch.setattr(Client, "submit", recording)
    code, _out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(checkout)])
    assert code == 0, err
    assert seen and seen[0]["branch"] == "main"
    assert seen[0]["tree_hash"]


def held_snapshot(monkeypatch) -> dict[str, Any]:
    """스냅샷에 약한 참조를 걸고, 대기에 들어간 순간 살아 있는지 본다."""
    box: dict[str, Any] = {"ref": None, "alive": None, "fetch": None, "tar": None}
    original = cli.make_snapshot

    def capture(*args, **kwargs):
        snap = original(*args, **kwargs)
        box["ref"] = weakref.ref(snap)
        box["tar"] = Path(snap.tar_path)  # 경로만 베껴 둔다 — 스냅샷을 붙잡지 않게
        return snap

    def fake_wait(client, job_id, *, timeout=None, joined=False, use_sse=True, fetch=None):
        gc.collect()  # 순환이 없으므로 참조가 하나라도 남아 있으면 살아 있다
        box["alive"] = box["ref"]() is not None
        box["fetch"] = fetch
        return 0

    monkeypatch.setattr(cli, "make_snapshot", capture)
    monkeypatch.setattr(cli, "_wait", fake_wait)
    return box


def test_the_snapshot_is_released_before_the_wait(srv, env, checkout, capsys, monkeypatch):
    """2만 파일에서 32 MB 다 — 대기는 20분이고 아무도 그 장부를 안 쓴다(§4.6)."""
    env(srv)
    box = held_snapshot(monkeypatch)
    code, _out, err = run(capsys, ["run", "ok", "--dir", str(checkout)])
    assert code == 0, err
    assert box["ref"] is not None, "make_snapshot 이 안 불렸다 — 픽스처가 틀렸다"
    assert box["alive"] is False


def test_fetching_artifacts_keeps_the_baseline_but_not_the_snapshot(
    srv, env, checkout, capsys, monkeypatch
):
    """`--fetch-artifacts` 가 쓰는 것은 `{경로: sha256}` 하나뿐이다 — 그것만 들고 버린다."""
    env(srv)
    box = held_snapshot(monkeypatch)
    code, _out, err = run(capsys, ["run", "ok", "--dir", str(checkout), "--fetch-artifacts"])
    assert code == 0, err
    assert box["alive"] is False
    assert box["fetch"] is not None and "hello.txt" in box["fetch"].baseline


def test_releasing_the_snapshot_does_not_skip_the_tar_cleanup(
    srv, env, checkout, capsys, monkeypatch
):
    """장부를 놓는 자리는 업로드 `finally` **뒤**다 — 그 앞이면 tar 이 남는다(§5)."""
    env(srv)
    box = held_snapshot(monkeypatch)
    code, _out, err = run(capsys, ["run", "ok", "--dir", str(checkout)])
    assert code == 0, err
    assert box["tar"] is not None and not box["tar"].exists()
