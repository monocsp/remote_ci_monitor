"""CLI — `--no-wait` 의 표시용 조회는 제출을 깨뜨리지 않는다 (격리 검증 B).

명세는 `docs/nowait-workplan.md` §5 「표시용 조회는 제출을 깨뜨리지 않는다」와 §9 완료 기준 4·5 다.
**`--no-wait` 의 종료 코드 0 은 「제출됐다」는 뜻이지 「조회됐다」가 아니다** — 그래서 이 파일은
조회를 할 수 있는 모든 방법으로 깨뜨려 놓고, 그래도 잡이 서버의 큐에 서 있고 종료 코드가 0 이며
순번 다섯 칸이 **아예 없는지**(`null` 도 아니다) 를 잰다. 그리고 이 변경이 옮기지 않았어야 할 것 —
기다리는 경로(`rcm run` · `rcm wait`)의 줄·JSON·종료 코드 — 을 골든으로 잠근다.

성공한 조회의 JSON·한 줄 모양과 순번 경계는 A(`tests/test_nowait_contract.py`), 진짜 워커 e2e 와
문서 대조는 C(`tests/test_nowait_e2e.py`) 의 몫이다 — 여기서는 다루지 않는다.

시나리오 표는 `docs/nowait-test-scenarios-b.md`.
"""

from __future__ import annotations

import http.client
import io
import re
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor.cli as cli
from remote_ci_monitor.cli import main
from remote_ci_monitor.client import Client, ClientError
from remote_ci_monitor.core.model import ALL_STATES
from test_cli_m1 import last_json
from test_server import Server
from test_server_m3 import build_bare_repo, git_server

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

#: 표시용 조회의 URL. 기다리는 경로의 폴링도 **같은 URL** 이라 상한으로만 구분된다.
JOB_VIEW = re.compile(r"/jobs/(\d+)\?tail=0$")

#: `src/` 안에서 `state` 값으로 쓰인 `"submitted"` 를 잡는 모양들(고치기 전의 두 자리).
SUBMITTED_AS_STATE = (
    re.compile(r"""["']state["']\s*:\s*["']submitted["']"""),
    re.compile(r"""\bstate\b\s*=\s*["']submitted["']"""),
    re.compile(r"""\bor\s+["']submitted["']"""),
)


# ── 도우미 ───────────────────────────────────────────────────────────────────


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
def bare(tmp_path):
    return build_bare_repo(tmp_path)


@pytest.fixture
def git_srv(tmp_path, bare):
    s = git_server(tmp_path, bare)
    yield s
    s.close()


@pytest.fixture
def cwd(monkeypatch, tmp_path):
    """git_ref 는 트리를 안 싼다 — 스냅샷을 찍으면 티가 나게 빈 디렉터리에서 돌린다."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    return empty


@pytest.fixture
def tree(tmp_path) -> Path:
    """`rcm run --dir` 에 줄 작은 트리(파일 둘). 기본 Server 의 10 KB 상한 안."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "gate.sh").write_text("#!/bin/sh\necho gate\n")
    return root


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). test_cli_m5.run 처럼 argparse 의 `SystemExit(2)`
    (`--priority urgent` 같은 것)도 코드로 돌려준다 — 사용 오류도 이 파일의 대상이다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def no_wait(capsys, tree: Path, preset: str, *extra: str) -> tuple[int, dict, str]:
    """`rcm run <preset> --no-wait --dir <tree> …` → (종료 코드, JSON, stderr)."""
    code, out, err = run(capsys, ["run", preset, "--no-wait", "--dir", str(tree), *extra])
    return code, last_json(out), err


def queue_rows(server: Server) -> list[dict]:
    return server.req("GET", "/api/status")[1]["pools"][0]["queue"]


def line_with(err: str, needle: str) -> str:
    return next(ln for ln in err.splitlines() if needle in ln)


def identity_lines(err: str) -> list[str]:
    """`submitted job` · `joined job` 로 시작하는 줄 전부.

    기다리는 경로에는 오늘의 식별 줄이 **하나**만 있어야 한다. `--no-wait` 의 한 줄이 새어 나오면
    문자열이 달라서 곧바로 티가 난다(진행 줄의 `1st in line` 과 헷갈리지 않는다).
    """
    return [ln for ln in err.splitlines() if ln.startswith(("submitted job", "joined job"))]


class _Resp:
    """`urlopen` 이 돌려주는 것의 최소 흉내 — `with` · `status` · `headers` · `read()`."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def spy_http(monkeypatch, *, on_view: Callable[[str], Any] | None = None) -> list[tuple]:
    """`urlopen` 호출을 전부 `(url, timeout)` 로 적는다. `on_view` 는 표시용 조회만 대신한다.

    가짜를 `Client.job` 이 아니라 **소켓 바로 위**에 두는 이유: 상한이 `cli` → `Client.job` →
    `get_json` → `_request` → `urlopen` 까지 정말 내려가는지, 재시도가 없는지를 재려면 맨 아래에서
    봐야 한다. 제출·업로드는 진짜로 나가므로 잡은 진짜로 큐에 선다.
    """
    real = urllib.request.urlopen
    seen: list[tuple] = []

    def spy(req, *args, **kwargs):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        seen.append((url, kwargs.get("timeout", args[0] if args else None)))
        if on_view is not None and JOB_VIEW.search(url):
            return on_view(url)
        return real(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", spy)
    return seen


def view_calls(seen: list[tuple]) -> list[tuple]:
    """`GET /jobs/{id}?tail=0` 왕복 전부(폴링 포함). 길이가 곧 왕복 횟수다."""
    return [(url, t) for url, t in seen if JOB_VIEW.search(url)]


def capped_calls(seen: list[tuple]) -> list[str]:
    """표시용 조회만: `GET /jobs/{id}?tail=0` 이면서 상한이 `NO_WAIT_VIEW_TIMEOUT` 인 왕복.

    URL 만으로는 안 된다 — 기다리는 경로의 폴링이 같은 URL 이다. 상한만으로도 안 된다 —
    SSE 의 `idle_timeout`(`SSE_TICK_SECONDS`)이 마침 같은 5.0 이다. 둘 다 봐야 한다.
    """
    return [url for url, t in seen if JOB_VIEW.search(url) and t == cli.NO_WAIT_VIEW_TIMEOUT]


def http_error(status: int, body: bytes = b'{"error":"nope"}') -> Callable[[str], Any]:
    """진짜 `HTTPError` — `Client._request` 가 몸통을 읽어 `ClientError` 로 바꾸는 길을 탄다."""

    def effect(url: str):
        raise urllib.error.HTTPError(
            url, status, f"HTTP {status}", http.client.HTTPMessage(), io.BytesIO(body)
        )

    return effect


def responds(body: bytes, status: int = 200) -> Callable[[str], Any]:
    def effect(url: str) -> _Resp:
        return _Resp(body, status)

    return effect


def raises(exc: BaseException) -> Callable[[str], Any]:
    def effect(url: str):
        raise exc

    return effect


#: 조회를 깨뜨리는 방법 전부. 이름이 그대로 pytest id 가 된다.
BROKEN_LOOKUPS = {
    "connection_refused": raises(urllib.error.URLError(ConnectionRefusedError(61, "refused"))),
    "http_401": http_error(401, b'{"error":"read access denied"}'),
    "http_404": http_error(404, b'{"error":"no such job"}'),
    "http_500": http_error(500, b'{"error":"boom"}'),
    "http_503": http_error(503, b""),
    "not_json": responds(b"<html><body>502 Bad Gateway</body></html>"),
    "empty_body": responds(b""),
    "json_list": responds(b"[1,2,3]"),
    "json_string": responds(b'"queued"'),
    "json_null": responds(b"null"),
    "socket_timeout": raises(TimeoutError("timed out")),
    "socket_error": raises(OSError(54, "Connection reset by peer")),
}


# ── ① 조회가 깨져도 제출은 선다 ──────────────────────────────────────────────


@pytest.mark.parametrize("effect", BROKEN_LOOKUPS.values(), ids=list(BROKEN_LOOKUPS))
def test_a_broken_display_lookup_leaves_the_submission_standing(
    srv, env, tree, capsys, monkeypatch, effect
):
    """조회를 어떻게 깨뜨려도: exit 0 · 잡은 진짜 큐에 · 순번 다섯 칸은 **없다** · 왕복은 한 번.

    `null` 은 「순번이 없다」는 뜻이라 「모른다」와 다르다 — fail-open 금지(§3).
    """
    env(srv)
    seen = spy_http(monkeypatch, on_view=effect)
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["joined"] is False and isinstance(body["job_id"], int)
    assert body["state"] == "queued" and body["state"] in ALL_STATES
    for key in cli.NO_WAIT_KEYS:
        assert key not in body, key
    assert list(body) == ["job_id", "joined", "state", "submission", "url"]
    row = queue_rows(srv)[0]  # 잡은 정말로 줄에 서 있다 — 그게 종료 코드 0 의 뜻이다
    assert row["id"] == body["job_id"] and row["state"] == "queued"
    assert len(view_calls(seen)) == 1, view_calls(seen)  # 재시도하지 않는다


@pytest.mark.parametrize("effect", BROKEN_LOOKUPS.values(), ids=list(BROKEN_LOOKUPS))
def test_a_broken_display_lookup_still_names_the_job_and_its_url(
    srv, env, tree, capsys, monkeypatch, effect
):
    """조회가 안 되면 순번 조각**만** 빠진다 — `submitted job #1 queued · <url>`(§4 마지막 줄)."""
    env(srv)
    spy_http(monkeypatch, on_view=effect)
    _code, body, err = no_wait(capsys, tree, "ok")
    assert (
        line_with(err, "submitted job") == f"submitted job #{body['job_id']} queued · {body['url']}"
    )
    assert "in line" not in err and "eta " not in err, err


def test_a_broken_display_lookup_on_a_joined_submission_keeps_the_detail(
    srv, env, tree, capsys, monkeypatch
):
    """합류한 세션도 마찬가지 — 순번만 빠지고 「같은 트리라 합류했다」는 말은 남는다."""
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    env(srv, "bob")
    spy_http(monkeypatch, on_view=http_error(500))
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["joined"] is True and body["job_id"] == first["job_id"]
    assert body["state"] in ALL_STATES  # 합류 응답이 말해 준 그 잡의 진짜 상태
    for key in cli.NO_WAIT_KEYS:
        assert key not in body, key
    detail = "same preset, inputs and tree"
    assert line_with(err, "joined job") == (
        f"joined job #{body['job_id']} {body['state']} · {detail} · {body['url']}"
    )


def test_client_job_raising_oserror_is_swallowed_too(srv, env, tree, capsys, monkeypatch):
    """`_job_view` 의 `except OSError` 가지. 진짜 스택에서는 `_request` 가 먼저 잡아 `ClientError`
    로 바꾸므로(client.py:502) 이 가지는 `Client.job` 을 직접 갈아 끼워야 지나간다."""
    env(srv)

    def boom(self, job_id, **kw):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(Client, "job", boom)
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["state"] == "queued" and not (set(cli.NO_WAIT_KEYS) & set(body))
    assert [r["id"] for r in queue_rows(srv)] == [body["job_id"]]


def test_an_empty_json_object_falls_back_to_the_upload_state(srv, env, tree, capsys, monkeypatch):
    """몸통이 `{}` 인 200 — dict 라서 「조회됐다」로 친다. `state` 는 업로드 응답의 `queued` 로
    떨어진다(`{}.get("state") or state`). 순번 다섯 칸은 `null` 로 들어간다 — 시나리오 문서의
    「명세에 대한 의견」 1 참고. 여기서 잠그는 것은 종료 코드·제출·상태뿐이다."""
    env(srv)
    seen = spy_http(monkeypatch, on_view=responds(b"{}"))
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["state"] == "queued" and body["state"] in ALL_STATES
    assert [r["id"] for r in queue_rows(srv)] == [body["job_id"]]
    assert len(view_calls(seen)) == 1


#: JSON 객체이긴 한데 칸의 **타입**이 다른 문서. `isinstance(view, dict)` 는 통과하고
#: `describe()` 안에서 터진다. 낼 수 있는 곳: 판이 다른 서버 · 중간의 프록시 ·
#: `reason` 이 문장에서 코드 객체로 바뀌는 앞날(결정 37 은 이미 그 방향이다).
WRONG_TYPED_VIEWS = {
    "position_str": b'{"state":"queued","position":"3"}',
    "estimate_str": b'{"state":"queued","position":3,"estimate":"soon"}',
    "wait_seconds_str": b'{"state":"queued","position":3,"estimate":{"wait_seconds":"soon"}}',
    "finish_at_int": b'{"state":"queued","position":3,"estimate":{"finish_at":12345}}',
    "reason_int": b'{"state":"queued","position":3,"reason":7}',
}


@pytest.mark.parametrize("raw", WRONG_TYPED_VIEWS.values(), ids=list(WRONG_TYPED_VIEWS))
def test_a_wrong_typed_display_document_must_not_kill_the_submission(
    srv, env, tree, capsys, monkeypatch, raw
):
    """⚠️ **오늘 빨갛다 — 구현의 버그다**(시나리오 문서 「찾은 결함」 1).

    `_job_view` 는 `ClientError`·`ValueError`·`OSError` 와 dict 아님만 막는다. dict 이긴 한데
    칸의 타입이 다르면 그 다음 `describe()` 에서 `TypeError`/`AttributeError` 가 나고, 그건
    `main()` 의 그물(SystemExit·KeyboardInterrupt·ClientError)에도 안 걸려 **트레이스백과 0 이
    아닌 종료 코드**로 나간다. 잡은 이미 큐에 있는데 세션은 실패로 읽는다 — §5 가 막으려던 바로
    그것이다.

    여기서 단언하는 것은 §5·§9-4 가 약속한 최소치뿐이다: **종료 코드 0 · 잡은 큐에 · stdout 에
    그 잡의 JSON**. 순번 다섯 칸을 어떻게 할지(빼거나, 그대로 싣거나)는 고치는 쪽이 정한다.
    """
    env(srv)
    spy_http(monkeypatch, on_view=responds(raw))
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["job_id"] == queue_rows(srv)[0]["id"]
    assert queue_rows(srv)[0]["state"] == "queued"


@needs_git
def test_a_broken_display_lookup_on_a_git_ref_submission_keeps_ref_and_sha(
    git_srv, bare, env, cwd, capsys, monkeypatch
):
    """git_ref 는 다른 분기다(`_run_git_ref`). 조회가 깨져도 exit 0 · `ref`·`sha` 는 제출 응답에서
    그대로 나오고 · `state` 는 제출 응답의 `queued`(서버가 git_ref 를 바로 큐에 넣는다)."""
    env(git_srv)
    seen = spy_http(monkeypatch, on_view=http_error(503, b'{"error":"queue unavailable"}'))
    code, out, err = run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])
    assert code == 0, err
    body = last_json(out)
    assert body["state"] == "queued" and body["ref"] == "main" and body["sha"] == bare.main_sha
    assert list(body) == ["job_id", "joined", "state", "ref", "sha", "submission", "url"]
    for key in cli.NO_WAIT_KEYS:
        assert key not in body, key
    assert [r["id"] for r in queue_rows(git_srv)] == [body["job_id"]]
    assert len(view_calls(seen)) == 1
    assert line_with(err, "submitted job").startswith(
        f"submitted job #{body['job_id']} queued · (deploy · main @{bare.main_sha[:7]})"
    )


# ── ② 상한이 소켓까지 내려간다 · 왕복은 한 번 ───────────────────────────────


def test_the_five_second_cap_reaches_the_socket_and_nothing_else_uses_it(
    srv, env, tree, capsys, monkeypatch
):
    """상한이 `cli` → `Client.job` → `get_json` → `_request` → `urlopen` 까지 내려간다.

    「받아 놓고 버리지 않았다」의 증명은 **`urlopen` 이 실제로 받은 값**이다. 그 값이 Client 의
    기본 상한(15초)과 다르다는 것까지 확인해야 「그냥 기본값이었다」가 아니다.
    """
    env(srv)
    seen = spy_http(monkeypatch)
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["position"] == 1  # 조회는 성공했다 — 상한이 요청을 막지 않는다
    assert view_calls(seen) == [
        (f"http://127.0.0.1:{srv.port}/jobs/{body['job_id']}?tail=0", cli.NO_WAIT_VIEW_TIMEOUT)
    ]
    assert cli.NO_WAIT_VIEW_TIMEOUT == 5.0
    assert Client("http://x").timeout != cli.NO_WAIT_VIEW_TIMEOUT  # 기본값이 아니다
    others = [(url, t) for url, t in seen if not JOB_VIEW.search(url)]
    assert others and all(t != cli.NO_WAIT_VIEW_TIMEOUT for _url, t in others), others


def test_a_timing_out_lookup_is_not_retried(srv, env, tree, capsys, monkeypatch):
    """느린 조회는 상한에서 끊기고 **다시 걸지 않는다** — 재시도는 「안 기다리겠다」의 반대다."""
    env(srv)
    seen = spy_http(monkeypatch, on_view=raises(TimeoutError("timed out")))
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert view_calls(seen) == [
        (f"http://127.0.0.1:{srv.port}/jobs/{body['job_id']}?tail=0", cli.NO_WAIT_VIEW_TIMEOUT)
    ]
    assert not (set(cli.NO_WAIT_KEYS) & set(body))


# ── ③ `"submitted"` 는 상태 이름이 아니다 ────────────────────────────────────


def test_no_no_wait_path_ever_reports_the_state_submitted(srv, env, tree, capsys, monkeypatch):
    """제출·합류·조회 성공·조회 실패 — 어느 길로도 `state` 는 서버가 쓰는 이름이다."""
    env(srv)
    states = []
    _code, first, _err = no_wait(capsys, tree, "ok")
    states.append(first["state"])
    env(srv, "bob")
    states.append(no_wait(capsys, tree, "ok")[1]["state"])  # 합류
    env(srv, "alice")
    spy_http(monkeypatch, on_view=http_error(500))
    states.append(no_wait(capsys, tree, "bad")[1]["state"])  # 조회 실패
    assert states == ["queued", "queued", "queued"]
    assert all(s in ALL_STATES for s in states) and "submitted" not in ALL_STATES


def test_the_word_submitted_is_not_a_state_anywhere_in_the_source():
    """`src/` 전체 grep. 고치기 전의 두 자리(`"state": "submitted"` · `or "submitted"`)가 없다."""
    root = Path(__file__).resolve().parent.parent / "src"
    hits = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.suffix not in (".py", ".js", ".html", ".css", ".json"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(line) for p in SUBMITTED_AS_STATE):
                hits.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    assert hits == []


# ── ④ 기다리는 경로는 한 글자도 안 바뀌었다 (GOLDEN) ────────────────────────


def test_the_waiting_run_prints_the_same_lines_and_the_same_json(live, env, tree, capsys):
    """`rcm run`(기다림)의 stderr 와 stdout — `--no-wait` 의 한 줄이 여기 새지 않았다.

    JSON 은 「서버의 잡 문서 + `job_id` + `wait_exit_code`」다. 그 등식으로 잠그면 서버가
    스키마 v1 대로 키를 더해도(§10) 이 골든은 안 깨지고 **CLI 가 더한 것**만 잠긴다.
    """
    env(live)
    code, out, err = run(capsys, ["run", "ok", "--dir", str(tree)])
    assert code == 0, err
    body = last_json(out)
    jid = body["job_id"]
    lines = err.splitlines()
    # 오늘의 식별 줄 하나 그대로 — `--no-wait` 의 순번 한 줄이 여기 새지 않았다
    assert identity_lines(err) == [f"submitted job #{jid} · {body['url']}"], err
    assert lines[-1] == f"#{jid} succeeded · green"  # 마지막 진행 줄 = describe(job)
    assert all(ln.startswith(f"#{jid} ") for ln in lines if ln.startswith("#")), err
    assert body["wait_exit_code"] == 0 and body["state"] == "succeeded"
    doc = live.req("GET", f"/jobs/{jid}?tail=0", token="alice")[1]
    assert {k: v for k, v in body.items() if k not in ("job_id", "wait_exit_code")} == doc
    assert body["job_id"] == doc["id"]


def test_the_waiting_join_line_and_the_timeout_exit_are_unchanged(srv, env, tree, capsys):
    """합류 식별 줄은 em dash 그대로다(`--no-wait` 의 `·` 한 줄이 아니다).

    `--timeout` 은 여전히 3 이다.
    """
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    jid = first["job_id"]
    env(srv, "bob")
    code, out, err = run(capsys, ["run", "ok", "--dir", str(tree), "--timeout", "0"])
    assert code == 3, err
    lines = err.splitlines()
    assert identity_lines(err) == [f"joined job #{jid} (queued) — same preset, inputs and tree"], (
        err
    )
    assert f"rcm: --timeout 0s elapsed; job {jid} is still queued" in lines
    body = last_json(out)
    assert body["wait_exit_code"] == 3 and body["joined"] is True and body["job_id"] == jid


@needs_git
def test_the_waiting_git_ref_line_is_unchanged(git_srv, bare, env, cwd, capsys):
    """git_ref 의 식별 줄은 이 변경에서 자리를 옮겼다(`--no-wait` 분기 뒤로) — 문구는 그대로다."""
    env(git_srv)
    code, out, err = run(capsys, ["run", "deploy", "--ref", "main", "--timeout", "0"])
    assert code == 3, err
    body = last_json(out)
    short = bare.main_sha[:7]
    assert identity_lines(err) == [
        f"submitted job #{body['job_id']} (deploy · main @{short}) · {body['url']}"
    ], err
    assert body["wait_exit_code"] == 3 and body["state"] == "queued"


def test_the_waiting_path_never_makes_the_display_lookup(live, env, tree, capsys, monkeypatch):
    """기다리는 경로는 상한 걸린 왕복을 **한 번도** 하지 않는다.

    폴링의 `client.job` 은 기본 상한(15초)이라 같은 URL 이라도 구분된다.
    """
    env(live)
    seen = spy_http(monkeypatch)
    code, out, err = run(capsys, ["run", "ok", "--dir", str(tree)])
    assert code == 0, err
    assert capped_calls(seen) == [], capped_calls(seen)
    assert view_calls(seen), "wait 가 잡을 한 번도 안 봤다면 픽스처가 틀린 것이다"


@pytest.mark.parametrize("preset,expected", [("ok", 0), ("bad", 1)])
def test_rcm_wait_still_maps_finished_states_to_0_and_1(live, env, tree, capsys, preset, expected):
    """`rcm wait --job N` 의 종료 코드 표는 그대로다: succeeded 0 · failed 1."""
    env(live)
    _code, body, _err = no_wait(capsys, tree, preset)
    jid = body["job_id"]
    live.wait_terminal(jid)
    code, out, err = run(capsys, ["wait", "--job", str(jid)])
    assert code == expected, err
    assert last_json(out)["wait_exit_code"] == expected
    lines = err.splitlines()
    # M5h §2.5 — 0 이 아닌 끝에는 로그로 가는 길이 **마지막 줄**로 붙는다. 진행 줄은 그 위다.
    if expected == 0:
        assert lines[-1].startswith(f"#{jid} ")
    else:
        assert f"log: rcm logs {jid}" in lines[-1]
        assert any(ln.startswith(f"#{jid} ") for ln in lines)


def test_rcm_wait_still_maps_cancelled_to_2_and_a_waiting_job_to_3(srv, env, tree, capsys):
    """cancelled 는 2, 「아직 모른다」는 3 — 3 은 실패가 아니다(AGENTS.md)."""
    env(srv)
    _code, body, _err = no_wait(capsys, tree, "ok")
    jid = body["job_id"]
    code, out, err = run(capsys, ["wait", "--job", str(jid), "--timeout", "0"])
    assert code == 3, err
    assert last_json(out)["wait_exit_code"] == 3
    assert f"rcm: --timeout 0s elapsed; job {jid} is still queued" in err.splitlines()
    assert srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})[0] == 200
    code, out, err = run(capsys, ["wait", "--job", str(jid)])
    assert code == 2, err
    assert last_json(out)["wait_exit_code"] == 2 and last_json(out)["state"] == "cancelled"


# ── ⑤ 다른 플래그와의 조합 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--no-join"],
        ["--priority", "low"],
        ["--priority", "normal"],
        ["--pool", "default"],
        ["--no-cache"],
        ["--by", "release-bot"],
        ["--no-join", "--priority", "low", "--no-cache", "--by", "release-bot"],
    ],
    ids=["bare", "no-join", "low", "normal", "pool", "no-cache", "by", "all-at-once"],
)
def test_no_wait_works_with_every_other_run_flag(srv, env, tree, capsys, monkeypatch, extra):
    """플래그 조합이 조회를 건너뛰거나 두 번 하게 만들지 않는다 — 전부 exit 0 · 왕복 한 번."""
    env(srv)
    seen = spy_http(monkeypatch)
    code, body, err = no_wait(capsys, tree, "ok", *extra)
    assert code == 0, err
    assert body["state"] == "queued" and body["position"] == 1
    assert set(cli.NO_WAIT_KEYS) <= set(body)
    assert len(view_calls(seen)) == 1, view_calls(seen)
    assert [r["id"] for r in queue_rows(srv)] == [body["job_id"]]


def test_the_flags_still_reach_the_server_under_no_wait(srv, env, tree, capsys, monkeypatch):
    """조합이 「통과만」 하는 게 아니라 **효과**가 있다.

    우선순위 · 풀 · 라벨 · 합류 거부 · 캐시 생략이 전부 서버까지 간다.
    """
    env(srv)
    seen = spy_http(monkeypatch)
    _code, first, _err = no_wait(capsys, tree, "ok")  # 캐시를 채워 둔다(첫 잡은 manifest 를 쓴다)
    seen.clear()  # 여기부터가 `--no-cache` 를 준 실행이다
    code, body, err = no_wait(
        capsys,
        tree,
        "ok",
        "--no-join",
        "--priority",
        "low",
        "--pool",
        "default",
        "--no-cache",
        "--by",
        "release-bot",
    )
    assert code == 0, err
    assert body["joined"] is False and body["job_id"] != first["job_id"]  # --no-join
    row = next(r for r in queue_rows(srv) if r["id"] == body["job_id"])
    assert row["priority"] == -1 and row["pool"] == "default"
    assert row["requester"]["label"] == "release-bot"
    assert not [url for url, _t in seen if "/tree/manifest" in url], "--no-cache 인데 manifest"


def test_priority_high_needs_an_admin_token_and_then_still_exits_0(srv, env, tree, capsys):
    """`--priority high` 는 서버가 403 으로 거절한다(비 admin) → exit 2 이고 아무것도 안 남는다.
    admin 이면 exit 0 이고 순번 문서까지 온다."""
    env(srv)
    code, out, err = run(
        capsys, ["run", "ok", "--no-wait", "--dir", str(tree), "--priority", "high"]
    )
    assert code == 2 and "submit failed" in err, err
    assert out.strip() == "" and queue_rows(srv) == []
    env(srv, "admin")
    code, body, err = no_wait(capsys, tree, "ok", "--priority", "high")
    assert code == 0, err
    assert body["position"] == 1 and queue_rows(srv)[0]["priority"] == 1


def test_an_unknown_priority_is_argparse_usage_2_and_submits_nothing(srv, env, tree, capsys):
    """`--priority urgent` 는 argparse 가 `SystemExit(2)` 로 끊는다 — 서버에 아무것도 안 간다."""
    env(srv)
    code, out, err = run(
        capsys, ["run", "ok", "--no-wait", "--dir", str(tree), "--priority", "urgent"]
    )
    assert code == 2 and "urgent" in err
    assert out.strip() == "" and queue_rows(srv) == []


def test_fetch_artifacts_with_no_wait_is_refused_before_anything_is_sent(
    srv, env, tree, capsys, monkeypatch
):
    """사용 오류는 **제출 앞**에서 끝난다 — 스냅샷도 안 싸고 조회도 안 한다(exit 2)."""
    env(srv)
    seen = spy_http(monkeypatch)

    def refuse(self, *args, **kwargs):
        raise AssertionError("rcm run must not submit after a usage error")

    monkeypatch.setattr(Client, "submit", refuse)
    code, out, err = run(
        capsys, ["run", "ok", "--no-wait", "--dir", str(tree), "--fetch-artifacts"]
    )
    assert code == 2, err
    assert (
        err == "rcm: --fetch-artifacts cannot be used with --no-wait (you have to wait to fetch)\n"
    )
    assert out.strip() == "" and queue_rows(srv) == []
    assert view_calls(seen) == [] and "snapshot" not in err


# ── ⑥ 제출·업로드가 실패한 길은 조회를 하지 않는다 ──────────────────────────


@pytest.mark.parametrize(
    "status,expected", [(400, 2), (401, 2), (403, 2), (413, 2), (0, 2), (500, 3), (503, 3)]
)
def test_a_failed_submit_keeps_its_exit_code_and_makes_no_lookup(
    srv, env, tree, capsys, monkeypatch, status, expected
):
    """제출이 실패하면 잡이 없다 — 종료 코드 표는 그대로고 표시용 조회는 아예 없다."""
    env(srv)
    seen = spy_http(monkeypatch)

    def boom(self, *args, **kwargs):
        raise ClientError(status, "nope")

    monkeypatch.setattr(Client, "submit", boom)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree)])
    assert code == expected, err
    assert "rcm: submit failed: nope" in err
    assert out.strip() == "" and view_calls(seen) == []


@pytest.mark.parametrize("status,hint", [(0, ""), (409, " (retry with --no-cache)")])
def test_a_failed_upload_is_still_exit_3_and_makes_no_lookup(
    srv, env, tree, capsys, monkeypatch, status, hint
):
    """업로드 실패는 「모른다」(3)이고 조회로 이어지지 않는다 — 잡은 uploading 인 채 남는다."""
    env(srv)
    seen = spy_http(monkeypatch)

    def boom(self, *args, **kwargs):
        raise ClientError(status, "upload broke")

    monkeypatch.setattr(Client, "upload", boom)
    code, out, err = run(capsys, ["run", "ok", "--no-wait", "--dir", str(tree), "--no-cache"])
    assert code == 3, err
    assert f"rcm: upload failed: upload broke{hint}" in err.splitlines()
    assert out.strip() == "" and view_calls(seen) == []
    assert [r["state"] for r in queue_rows(srv)] == ["uploading"]
