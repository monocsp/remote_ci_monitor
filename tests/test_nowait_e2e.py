"""격리 검증 C — `rcm run --no-wait` 의 숫자가 **진짜**인가, 그리고 문서가 그것과 맞는가.

명세는 `docs/nowait-workplan.md` §8 의 C 행이다. 두 가지를 밖에서 다시 잰다:

1. **진짜 서버로**. 큐를 여러 개 쌓아 각자의 순번이 `GET /api/status` 의 그 잡 행과 같은지,
   concurrency 그룹에 막힌 잡이 막은 잡을 이름으로 대는지, 시작할 수 없는 잡(정지 · 워커 없음)이
   `wait`·`finish_at` 을 **null 로** 주고 줄에 `eta` 를 **안 찍는지**(fail-open 금지), 합류한
   세션과 원 요청자가 같은 순간에 같은 숫자를 말하는지, 그리고 그 숫자가 `rcm eta --job N` ·
   `rcm jobs --json` · `rcm top --json` 과 일치하는지.
2. **문서와 스크린샷 기계**. `docs/usage*.md` §5 두 거울이 지금 찍히는 것을 말하는지, README 의
   `rcm run` 행과 CHANGELOG `[Unreleased]` 항목이 있는지, 그리고 `tools/screenshots/build.py` 의
   상자 앵커가 CLI 가 **실제로 찍는 줄**을 96칸에서 접어도 여전히 찾아내는지.

JSON 키 계약·순번 경계(A)와 조회 실패·플래그 조합(B)은 다른 문서가 맡는다 — 여기서는 안 다룬다.
시간 단언은 하나도 없다. 기다림은 전부 마감 있는 폴링(`Server.wait_state`·`wait_terminal`)이다.
"""

from __future__ import annotations

import ast
import json
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.status import parse_iso
from test_cli_m1 import last_json, run
from test_server import Server
from test_server_m3 import make_server

ROOT = Path(__file__).resolve().parents[1]
BUILD_PY = ROOT / "tools" / "screenshots" / "build.py"
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
README = ROOT / "README.md"
README_KO = ROOT / "README.ko.md"
CHANGELOG = ROOT / "CHANGELOG.md"

#: 스크린샷이 접는 폭. `build.py` 의 `lines(..., wrap=96)` 과 같은 값이어야 한다.
WRAP = 96


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_nowait.env 와 같다 — `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""
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
    """워커가 없다 — 낸 잡은 전부 줄을 선다(순번이 시간에 안 흔들린다)."""
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def tree(tmp_path) -> Path:
    """`rcm run --dir` 에 줄 작은 트리. 기본 Server 의 10 KB 상한 안."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "gate.sh").write_text("#!/bin/sh\necho gate\n")
    return root


def sh(name: str, script: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "argv": ["sh", "-c", script], "timeout_seconds": 60, **extra}


@pytest.fixture
def lonely(tmp_path):
    """레인이 하나도 없는 풀(`linux`)에 프리셋 하나 — 잡은 영영 시작할 수 없다."""
    s = make_server(
        tmp_path,
        [sh("ok", "echo hi"), sh("far", "echo hi", pool="linux")],
        workers=False,
    )
    yield s
    s.close()


@pytest.fixture
def grouped(tmp_path):
    """레인 둘 · 같은 `concurrency_group` 프리셋 하나. 막는 잡을 먼저 돌려 둔다.

    `(서버, 막고 있는 잡 번호)` 를 준다. 레인이 둘이라 「레인이 없어서」가 아니라 **그룹 때문에**
    막힌 것이 분명하다. `admission="always"` 는 부하 게이트를 꺼 둔다(레인 2 가 표본이 없어
    보류되면 시나리오가 흔들린다 — test_e2e_m3 의 같은 주석).
    """
    s = make_server(
        tmp_path,
        [sh("qa", "echo '::rcm::step::wait'; sleep 10", concurrency_group="devices")],
        workers=True,
        lanes=2,
        admission="always",
    )
    try:
        blocker = s.submit(preset="qa", tree_hash="01" * 32)[1]["job_id"]
        assert s.upload(blocker)[0] == 200
        s.wait_state(blocker, "running")
        yield s, blocker
    finally:
        s.req("POST", f"/jobs/{blocker}/cancel", token="alice", json_body={})
        s.wait_terminal(blocker)
        s.close()


# ── 도우미 ───────────────────────────────────────────────────────────────────


def no_wait(capsys, tree: Path, preset: str, *extra: str) -> tuple[int, dict, str]:
    """`rcm run <preset> --no-wait --dir <tree> …` → (종료 코드, JSON, stderr)."""
    code, out, err = run(capsys, ["run", preset, "--no-wait", "--dir", str(tree), *extra])
    return code, last_json(out), err


def submitted_line(err: str) -> str:
    """stderr 에서 제출/합류 한 줄만 꺼낸다."""
    lines = [ln for ln in err.splitlines() if ln.startswith(("submitted job", "joined job"))]
    assert len(lines) == 1, err
    return lines[0]


def queue_rows(server: Server) -> list[dict]:
    """모든 풀의 대기·실행 행. 잡이 기본 풀에 없을 수도 있다(`lonely` 픽스처)."""
    doc = server.req("GET", "/api/status")[1]
    return [row for pool in doc["pools"] for row in pool["queue"]]


def row_for(server: Server, job_id: int) -> dict:
    row = next((r for r in queue_rows(server) if r["id"] == job_id), None)
    assert row is not None, f"job #{job_id} is not in the queue"
    return row


def blocker_of(doc: dict) -> tuple[int, str] | None:
    """`blocked_by` 에서 시간에 안 흔들리는 부분만(remaining_seconds 는 초마다 준다)."""
    b = doc.get("blocked_by")
    return None if b is None else (b["job_id"], b["group"])


def numbers(doc: dict) -> tuple:
    """두 화면이 같은 잡의 **같은 사실**을 말하는지 비교할 값. 시각은 따로 본다."""
    est = doc.get("estimate") or {}
    return (
        doc.get("position"),
        doc.get("reason"),
        doc.get("ahead_job_id"),
        blocker_of(doc),
        est.get("expected_seconds"),
        est.get("wait_seconds") is None,
        est.get("finish_at") is None,
    )


def eta_gap(a: dict, b: dict) -> float:
    """두 문서의 `estimate.finish_at` 차이(초). 둘 다 있어야 부른다."""
    ea, eb = a["estimate"]["finish_at"], b["estimate"]["finish_at"]
    return abs((parse_iso(ea) - parse_iso(eb)).total_seconds())


#: 같은 잡을 두 번 조회하는 사이에 벌어질 수 있는 ETA 차이(초). 진짜 불일치는 분 단위다
#: (레인·그룹이 다르게 잡히면 600초씩 벌어진다) — 이 값은 그 사이를 가른다.
ETA_SLACK = 30.0


# ── 1. 여러 잡이 줄을 서면 각자의 순번을 본다 ────────────────────────────────


def test_four_stacked_jobs_each_report_the_position_of_their_own_queue_row(srv, env, tree, capsys):
    """제출할 때마다 그 순간의 `/api/status` 행과 순번이 같아야 한다.

    마지막 잡의 순번은 곧 대기 중인 잡의 수다 — 「내가 꼴찌」가 사실인지까지 본다.
    """
    env(srv)
    seen: list[dict] = []
    for preset, extra in (("ok", ()), ("bad", ()), ("slow", ()), ("gate", ("-f", "scope=fast"))):
        code, body, err = no_wait(capsys, tree, preset, *extra)
        assert code == 0, err
        row = row_for(srv, body["job_id"])
        assert body["position"] == row["position"], (body, row)
        assert numbers(body) == numbers(row), (body, row)
        assert eta_gap(body, row) <= ETA_SLACK, (body["estimate"], row["estimate"])
        seen.append(body)
    assert [b["position"] for b in seen] == [1, 2, 3, 4]
    waiting = [r for r in queue_rows(srv) if r["position"] is not None]
    assert seen[-1]["position"] == len(waiting)
    # 앞 잡을 가리키는 손가락도 진짜다 — 두 번째부터는 바로 앞 잡의 번호다
    assert [b["ahead_job_id"] for b in seen] == [None, *[b["job_id"] for b in seen[:-1]]]


# ── 2. 그룹에 막힌 잡은 막은 잡을 이름으로 댄다 ──────────────────────────────


def test_a_group_blocked_job_names_its_blocker_and_says_blocked_by_group(
    grouped, env, tree, capsys
):
    """레인은 비어 있는데 그룹이 막는다 — 이유는 `blocked_by_group`, 막은 잡은 `blocked_by`."""
    srv, blocker = grouped
    env(srv)
    code, body, err = no_wait(capsys, tree, "qa")
    assert code == 0, err
    assert body["state"] == "queued" and body["reason"] == "blocked_by_group"
    assert body["blocked_by"]["job_id"] == blocker
    assert body["blocked_by"]["group"] == "devices"
    assert body["blocked_by"]["remaining_seconds"] > 0
    assert body["ahead_job_id"] is None  # 레인은 비어 있다 — 앞선 잡이 아니라 그룹이 막는다
    row = row_for(srv, body["job_id"])
    assert numbers(body) == numbers(row), (body, row)
    assert eta_gap(body, row) <= ETA_SLACK
    line = submitted_line(err)
    assert "blocked by group" in line and "eta " in line, line
    # 막은 잡은 여전히 돌고 있다 — 이 시나리오가 진짜였다는 증거
    assert srv.store.get_job(blocker).state == "running"


# ── 3. 시작할 수 없는 잡에는 시각을 지어내지 않는다 (fail-open 금지) ─────────


def test_a_paused_queue_gives_null_wait_and_finish_and_the_line_carries_no_eta(
    srv, env, tree, capsys
):
    """정지된 큐는 언제 끝날지 아무도 모른다 — null 을 그대로 싣고 줄에서 `eta` 를 뺀다."""
    env(srv, "admin")
    assert run(capsys, ["pause"])[0] == 0
    env(srv, "alice")
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["reason"] == "paused"
    assert body["estimate"]["wait_seconds"] is None
    assert body["estimate"]["finish_at"] is None
    assert body["estimate"]["expected_seconds"] > 0  # 기대치는 여전히 안다
    line = submitted_line(err)
    assert "paused" in line and "1st in line" in line, line
    assert "eta " not in line and "wait " not in line, line
    assert numbers(body) == numbers(row_for(srv, body["job_id"]))
    # 정지를 풀면 같은 잡에 시각이 생긴다 — null 은 「모른다」가 아니라 「멈춰 있다」였다
    env(srv, "admin")
    assert run(capsys, ["resume"])[0] == 0
    env(srv, "alice")
    again = json.loads(run(capsys, ["eta", "--job", str(body["job_id"]), "--json"])[1])
    assert again["estimate"]["finish_at"] and again["reason"] == "waiting_for_lane"


def test_a_pool_with_no_live_worker_gives_null_wait_and_finish_and_no_eta(
    lonely, env, tree, capsys
):
    """레인이 하나도 없는 풀에 낸 잡도 같다 — 순번은 말하되 끝나는 시각은 말하지 않는다.

    기본 풀에 잡 둘을 먼저 세워 둔다: 순번은 **자기 풀 안의** 순번이지 전체 대기열의 번호가
    아니다. 「3번째」라고 말했다면 그건 지어낸 숫자다.
    """
    env(lonely)
    no_wait(capsys, tree, "ok")
    no_wait(capsys, tree, "ok", "--no-join")
    code, body, err = no_wait(capsys, tree, "far")
    assert code == 0, err
    assert body["state"] == "queued" and body["position"] == 1
    assert len([r for r in queue_rows(lonely) if r["position"] is not None]) == 3
    assert body["reason"] == "worker_down"
    assert body["estimate"]["wait_seconds"] is None
    assert body["estimate"]["finish_at"] is None
    line = submitted_line(err)
    assert "worker down" in line and "1st in line" in line, line
    assert "eta " not in line and "wait " not in line, line
    assert numbers(body) == numbers(row_for(lonely, body["job_id"]))


# ── 4. 합류한 세션과 원 요청자가 같은 것을 본다 ──────────────────────────────


def test_the_joiner_and_the_requester_report_the_same_numbers_for_the_same_job(
    srv, env, tree, capsys
):
    """같은 잡을 두 세션이 말하면 두 말이 같아야 한다 — 그리고 큐 행과도 같아야 한다."""
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    no_wait(capsys, tree, "bad")  # 뒤에 하나 더 — 순번 1 이 우연이 아니게
    code, mine, err_mine = no_wait(capsys, tree, "ok")  # 낸 사람이 다시 낸다 → 합류
    assert code == 0, err_mine
    env(srv, "bob")
    code, theirs, err_theirs = no_wait(capsys, tree, "ok")  # 다른 사람이 낸다 → 합류
    assert code == 0, err_theirs
    assert mine["joined"] is True and theirs["joined"] is True
    assert mine["job_id"] == theirs["job_id"] == first["job_id"]
    assert numbers(mine) == numbers(theirs), (mine, theirs)
    assert numbers(mine) == numbers(row_for(srv, first["job_id"]))
    assert eta_gap(mine, theirs) <= ETA_SLACK
    assert mine["url"] == theirs["url"] == first["url"]  # 합류자도 볼 링크를 받는다
    for err in (err_mine, err_theirs):
        line = submitted_line(err)
        assert line.startswith(f"joined job #{first['job_id']} queued"), line
        assert "1st in line" in line and "eta " in line, line


# ── 5. 다른 화면들과 숫자가 같다 ─────────────────────────────────────────────


def test_the_numbers_agree_with_rcm_eta_rcm_jobs_and_rcm_top_for_the_same_job(
    srv, env, tree, capsys
):
    """`--no-wait` 이 말한 순번·ETA 는 `rcm eta --job N` · `rcm jobs` · `rcm top` 과 같다."""
    env(srv)
    no_wait(capsys, tree, "ok")
    _code, body, _err = no_wait(capsys, tree, "bad")
    jid = body["job_id"]

    eta_row = json.loads(run(capsys, ["eta", "--job", str(jid), "--json"])[1])
    jobs_rows = json.loads(run(capsys, ["jobs", "--json"])[1])
    top_doc = json.loads(run(capsys, ["top", "--json"])[1])
    jobs_row = next(r for r in jobs_rows if r["id"] == jid)
    top_row = next(r for p in top_doc["pools"] for r in p["queue"] if r["id"] == jid)

    for name, other in (("eta", eta_row), ("jobs", jobs_row), ("top", top_row)):
        assert numbers(body) == numbers(other), (name, body, other)
        assert eta_gap(body, other) <= ETA_SLACK, (name, body["estimate"], other["estimate"])
    # 사람이 읽는 줄도 같은 순번을 말한다
    eta_line = run(capsys, ["eta", "--job", str(jid)])[1]
    assert f"#{jid} · 2nd in line" in eta_line, eta_line
    assert "2nd in line" in submitted_line(_err)


# ── 6. 문서: usage §5 두 거울 · README 행 · CHANGELOG ────────────────────────


def read(path: Path) -> str:
    assert path.is_file(), f"missing: {path.relative_to(ROOT)}"
    return path.read_text()


def has(text: str, pattern: str, flags: int = re.M) -> bool:
    return re.search(pattern, text, flags) is not None


def numbered_section(text: str, number: int) -> str:
    """`## <n>. …` 부터 다음 `## ` 앞까지. 제목은 두 언어가 다르므로 번호로 찾는다."""
    m = re.search(rf"^## {number}\. [^\n]*$", text, re.M)
    assert m, f"no `## {number}.` section"
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def unreleased_entry(text: str) -> str:
    """`## [Unreleased]` 부터 첫 릴리스 `[0.1.0]` 절 앞까지 — 릴리스 뒤에는 항목이 그 버전의 절로
    옮겨가므로, 다른 문서 잠금(tests/test_docs_m5.py `unreleased()`)과 같은 범위를 본다."""
    from test_docs_m5 import unreleased

    return unreleased(text)


def table_row(text: str, command: str) -> str:
    rows = [ln for ln in text.splitlines() if ln.startswith("| `rcm " + command)]
    assert rows, f"no table row for rcm {command}"
    return rows[0]


USAGE_GUIDES = [pytest.param(USAGE, id="en"), pytest.param(USAGE_KO, id="ko")]
READMES = [pytest.param(README, id="en"), pytest.param(README_KO, id="ko")]

#: §5 가 반드시 말해야 하는 사실. 두 언어를 한 정규식으로 받는다(test_docs_m5c 의 방식).
SECTION_5_FACTS = [
    pytest.param(r"`position`", id="position-key"),
    pytest.param(r"`estimate`", id="estimate-key"),
    pytest.param(r"\bfinish\b|끝날", id="finish-time"),
    pytest.param(r"already running|이미 도는 잡", id="a-running-job"),
    pytest.param(r"guess|지어내|추측", id="never-guessed"),
    pytest.param(r"exit code is still 0|종료 코드는 그대로 0", id="exit-0-anyway"),
    pytest.param(r"not because it was looked up|조회됐다", id="0-means-submitted"),
]


@pytest.mark.parametrize("pattern", SECTION_5_FACTS)
@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_usage_section_5_describes_what_no_wait_now_prints(path: Path, pattern: str):
    """「내고 가기」 절이 순번 · ETA · 지어내지 않음 · 그래도 0 을 말한다 — 두 언어 모두."""
    sec = numbered_section(read(path), 5)
    assert has(sec, pattern), f"{path.name} §5 lacks /{pattern}/"


def test_the_two_usage_mirrors_number_the_same_steps_in_section_5():
    """거울은 같은 것을 같은 번호로 말한다 — 상자 번호가 두 언어에서 같은 뜻이어야 한다.

    `build.py` 가 `cli-nowait.png` 에 그리는 상자는 넷이다(①~④).
    """
    en = numbered_section(read(USAGE), 5)
    ko = numbered_section(read(USAGE_KO), 5)
    heads = [re.findall(r"^(\d+)\. ", s, re.M) for s in (en, ko)]
    assert heads[0] == heads[1] == ["1", "2", "3", "4", "5"], heads  # M5h 가 다섯째를 더했다
    # 같은 그림을 가리킨다
    assert has(en, r"images/ui/cli-nowait\.png") and has(ko, r"images/ui/cli-nowait\.png")


def test_the_two_usage_mirrors_number_the_same_two_steps_in_section_7():
    """`cli-join.png` 은 상자가 둘이다 — 이 변경이 그 상자 범위를 고쳤으므로 짝을 다시 잠근다."""
    en = numbered_section(read(USAGE), 7)
    ko = numbered_section(read(USAGE_KO), 7)
    heads = [re.findall(r"^(\d+)\. ", s, re.M) for s in (en, ko)]
    assert heads[0] == heads[1] == ["1", "2"], heads
    assert has(en, r"images/ui/cli-join\.png") and has(ko, r"images/ui/cli-join\.png")


@pytest.mark.parametrize("path", USAGE_GUIDES)
def test_the_section_5_image_alt_text_says_what_the_picture_now_shows(path: Path):
    """대체 텍스트는 그림이 **보여 주는 것**을 말해야 한다(`docs/documentation.md` 「README」).

    `cli-nowait.png` 의 ① 상자는 이제 순번과 ETA 가 붙은 제출 줄이다. 두 언어의 대체 텍스트는
    아직 「잡 번호를 찍는다」에 머물러 있다 — 그림을 못 보는 사람에게는 이 변경이 없는 셈이다.
    """
    sec = numbered_section(read(path), 5)
    alt = re.search(r"^!\[([^\]]*)\]\(images/ui/cli-nowait\.png\)", sec, re.M)
    assert alt, f"{path.name} §5 has no cli-nowait.png image"
    assert has(alt.group(1), r"position|in line|\bETA\b|순번|몇 번째", re.I), alt.group(1)


@pytest.mark.parametrize("path", READMES)
def test_readme_run_row_says_no_wait_returns_the_position_and_the_eta(path: Path):
    """명령 표의 `rcm run` 행이 `--no-wait` 가 이제 무엇을 주는지 말한다."""
    row = table_row(read(path), "run")
    assert "--no-wait" in row, row
    assert has(row, r"position|순번"), row
    assert has(row, r"\bETA\b|\beta\b", re.I), row


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param(r"--no-wait", id="the-flag"),
        pytest.param(r"`position`", id="position-key"),
        pytest.param(r'"state": "submitted"|`state`', id="the-state-fix"),
        pytest.param(r"exit code\s*\n?\s*is still 0|exits 0", id="still-0"),
        pytest.param(r"pull/72", id="pull-request-link"),
    ],
)
def test_changelog_unreleased_entry_covers_the_new_no_wait_output(pattern: str):
    """`[Unreleased]` 항목 하나가 사용자가 보는 변화를 다 말한다(PR 링크까지)."""
    sec = unreleased_entry(read(CHANGELOG))
    assert has(sec, pattern, re.M | re.I), f"[Unreleased] lacks /{pattern}/"


# ── 7. 스크린샷 앵커가 지금 찍히는 줄을 여전히 찾아낸다 ──────────────────────


def screenshot_helpers(raw_dir: Path):
    """`tools/screenshots/build.py` 의 `lines`·`at` 만 떼어 온다.

    build.py 를 import 하면 Pillow 가 딸려 온다 — CI 는 `.[dev]` 만 깐다(`docs` extra 가 아니다).
    그래서 AST 로 그 두 함수만 꺼내 **원본 그대로** 실행한다. 이름이나 서명이 바뀌면 여기서
    곧바로 빨개진다. `RAW` 는 build.py 의 모듈 전역이라 이름 공간에 끼워 넣는다.
    """
    picked = [
        node
        for node in ast.parse(BUILD_PY.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name in ("lines", "at")
    ]
    assert {n.name for n in picked} == {"lines", "at"}, [n.name for n in picked]
    ns: dict[str, Any] = {"textwrap": textwrap, "RAW": raw_dir}
    module = ast.fix_missing_locations(ast.Module(body=picked, type_ignores=[]))
    exec(compile(module, str(BUILD_PY), "exec"), ns)  # noqa: S102 — 원본 코드를 그대로 돌린다
    return ns["lines"], ns["at"]


def block(cmd: str, err: str, out: str, *, rc: int | None = None) -> str:
    """`tools/screenshots/capture.py` 의 `_block` 과 같은 조립: 프롬프트 + stderr + stdout."""
    text = f"$ {cmd}\n" + err + out
    if rc is not None:
        text += f"$ echo $?\n{rc}\n"
    return text


def tidy(text: str, server: Server) -> str:
    """`capture.py::_tidy` 가 하는 치환 중 줄 길이에 영향을 주는 것 — 포트와 호스트."""
    return text.replace(f":{server.port}", ":8787").replace("127.0.0.1", "macmini")


def write_capture(raw_dir: Path, name: str, text: str) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{name}.txt").write_text(text)


def check_boxes(boxes: list[tuple[int, int, int]], ls: list[str]) -> None:
    """`term()` 에 넘어가는 상자가 실제 줄 범위 안에 있고 비어 있지 않은가."""
    for number, start, end in boxes:
        assert 0 <= start <= end < len(ls), (number, start, end, len(ls))


def test_the_cli_nowait_anchors_still_find_the_submission_block(tmp_path, env, tree, capsys):
    """`cli-nowait.png` 의 앵커 넷을 **진짜 CLI 출력**으로 만든 캡처 위에서 다시 찾는다.

    프롬프트 줄(`$ …`)은 capture.py 가 쓰는 문구 그대로 쓰고, 그 아래 본문만 CLI 가 낸다 —
    앵커가 흔들릴 수 있는 곳은 CLI 가 찍는 줄뿐이기 때문이다.
    """
    server = Server(tmp_path / "srv", workers=True)
    try:
        env(server)
        # 레인 하나를 오래 도는 잡으로 채워 둔다 — 그래야 `--no-wait` 이 순번·ETA 를 다 찍는다
        busy = server.submit(preset="slow", tree_hash="0f" * 32)[1]["job_id"]
        assert server.upload(busy)[0] == 200
        server.wait_state(busy, "running")

        code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
        assert code == 0, err
        jid = last_json(out)["job_id"]
        text = block("rcm run demo --no-wait", err, out)
        code, out, err = run(capsys, ["jobs"])
        assert code == 0, err
        text += block("rcm jobs", err, out)
        # 레인을 비워 준다 — 남은 캡처(`rcm wait`)도 진짜로 돌린다
        server.req("POST", f"/jobs/{busy}/cancel", token="alice", json_body={})
        server.wait_terminal(busy)
        code, out, err = run(capsys, ["wait", "--job", str(jid)])
        assert code == 0, err
        text += block(f"rcm wait --job {jid}", err, out, rc=0)

        raw = tmp_path / "raw"
        write_capture(raw, "x-nowait", tidy(text, server))
        lines, at = screenshot_helpers(raw)
        ls = lines("x-nowait", wrap=WRAP)

        i_sub, i_jobs = at(ls, "submitted job"), at(ls, "$ rcm jobs")
        i_wait, i_rc = at(ls, "$ rcm wait"), at(ls, "$ echo")
        assert i_sub < i_jobs < i_wait < i_rc, (i_sub, i_jobs, i_wait, i_rc)
        boxes = [
            (1, i_sub, i_jobs - 1),
            (2, i_jobs + 1, i_jobs + 2),
            (3, i_wait + 1, i_wait + 1),
            (4, i_rc + 1, i_rc + 1),
        ]
        check_boxes(boxes, ls)
        # ① 은 「제출이 준 것」 전부다: 한 줄 · 산출물 안내 · 그리고 접힌 JSON 의 마지막 조각까지
        assert ls[i_sub].startswith("submitted job")
        assert at(ls, "fetch its artifacts") < i_jobs
        assert ls[i_jobs - 1].rstrip().endswith("}"), ls[i_sub:i_jobs]
        assert sum(ln.startswith("submitted job") for ln in ls) == 1, ls
    finally:
        server.close()


def test_the_cli_join_anchors_still_find_the_joined_block(srv, env, tree, capsys):
    """`cli-join.png` 의 앵커 둘. 합류 줄은 96칸을 넘어 **접힌다** — 그래도 찾아야 한다."""
    env(srv)
    code, out, err = run(capsys, ["run", "gate", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    text = block("rcm run demo --no-wait -f speed=fast", err, out)
    env(srv, "bob")
    code, out, err = run(capsys, ["run", "gate", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    assert last_json(out)["joined"] is True
    joined = submitted_line(err)
    text += block("rcm run demo --no-wait -f speed=fast   # 다른 세션, 같은 트리", err, out)

    raw = tree.parent / "raw"
    write_capture(raw, "x-join", tidy(text, srv))
    lines, at = screenshot_helpers(raw)
    assert len(tidy(joined, srv)) > WRAP, joined  # 접히는 경우를 실제로 지나간다
    ls = lines("x-join", wrap=WRAP)

    i_second = at(ls, "$ rcm run demo --no-wait -f speed=fast   #")
    i_joined = at(ls, "joined job")
    boxes = [(1, 0, i_second - 1), (2, i_joined, at(ls, "fetch its artifacts", after=i_joined) - 1)]
    check_boxes(boxes, ls)
    assert i_second < i_joined, (i_second, i_joined)
    assert ls[i_second - 1].rstrip().endswith("}")  # ① 은 첫 블록의 JSON 끝에서 닫힌다
    # 합류 줄이 접혀 여러 조각이 됐고, ② 가 그 조각을 전부 덮는다
    assert boxes[1][2] > i_joined, ls[i_joined:]
    assert sum(ln.startswith("joined job") for ln in ls) == 1, ls
    # `after=` 가 없으면 첫 블록의 안내 줄을 집는다 — 그래서 그 인자가 필요하다
    assert at(ls, "fetch its artifacts") < i_joined


def test_a_long_submitted_line_keeps_the_anchor_on_its_first_fragment(grouped, env, tree, capsys):
    """가장 긴 제출 줄(그룹에 막힌 잡)도 96칸에서 접힌 뒤 앵커가 첫 조각에 남는다."""
    server, _blocker = grouped
    env(server)
    code, out, err = run(capsys, ["run", "qa", "--no-wait", "--dir", str(tree)])
    assert code == 0, err
    line = tidy(submitted_line(err), server)
    assert len(line) > WRAP, line

    raw = tree.parent / "raw"
    write_capture(raw, "x-nowait", tidy(block("rcm run demo --no-wait", err, out), server))
    lines, at = screenshot_helpers(raw)
    ls = lines("x-nowait", wrap=WRAP)
    i_sub = at(ls, "submitted job")
    assert line.startswith(ls[i_sub]) and ls[i_sub] != line, ls[i_sub]  # 첫 조각이고 전부는 아니다
    assert sum(ln.startswith("submitted job") for ln in ls) == 1, ls
    assert at(ls, "fetch its artifacts") > i_sub  # 접힌 조각들이 안내 줄을 밀어내지 않았다
