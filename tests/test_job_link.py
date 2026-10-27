"""잡 링크 — 호출자가 잡에 붙이는 범용 링크 하나(`link: {url, label}`).

rcm 은 GitHub 을 모른다(PLAN 결정 30). PR 번호와 주소는 호출자가 알고, rcm 은 http(s) 링크
하나를 저장해 잡 JSON(`/jobs/<id>` · 대기열 행 · 최근 목록)에 돌려줄 뿐이다. 웹은 그것을 잡 이름
옆의 칩으로 그린다(tests/web/job_link.test.js · tests/test_web_layout.py).

여기서 잠그는 것:
- 검증(서버와 CLI 가 같은 함수): http(s) 절대 URL · 500자 · 공백/제어문자 금지 · 라벨 40자.
- 서버: 400 과 그 문장, 잡 JSON 의 `link` 모양(없으면 null), 합류자의 링크는 무시.
- 저장소: v22 의 두 열 · v21 → v22 마이그레이션(백업 먼저) · 옛 잡은 링크 없음.
- 클라이언트 · CLI: `--link` · `--link-label` → 요청 본문, 라벨만 주면 exit 2, `--help` 감지 문자열.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime

import pytest

from remote_ci_monitor.cli import main
from remote_ci_monitor.client import Client
from remote_ci_monitor.core.joblink import LINK_LABEL_MAX, LINK_URL_MAX, LinkError, parse_link
from remote_ci_monitor.core.model import JobLink
from remote_ci_monitor.store import DB_VERSION, Store
from test_server import Server
from test_store import enqueue

PR = "https://github.com/fmmc-tech/dolomood-app-renew/pull/816"
NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


def submit_with(srv: Server, link, *, token: str = "alice", tree_hash: str | None = None):
    body = {
        "preset": "ok",
        "inputs": {},
        "source": {
            "mode": "tree",
            "repo": "org/app",
            "base_sha": "abc123f",
            "dirty": False,
            "tree_hash": tree_hash or "9f" * 32,
            "bytes": 10,
        },
        "requester_label": f"{token}@host",
    }
    if link is not ...:
        body["link"] = link
    return srv.req("POST", "/jobs", token=token, json_body=body)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


# ── 검증 (서버와 CLI 가 같이 쓴다) ─────────────────────────────────────────────


def test_a_good_link_parses_into_url_and_label():
    assert parse_link({"url": PR, "label": "PR #816"}) == JobLink(url=PR, label="PR #816")
    assert parse_link({"url": "http://ci.local:8080/x?y=1#z"}) == JobLink(
        url="http://ci.local:8080/x?y=1#z", label=None
    )
    # 빈 라벨은 라벨이 없는 것이다 — 웹이 주소에서 줄여 보인다
    assert parse_link({"url": PR, "label": ""}) == JobLink(url=PR, label=None)
    assert parse_link({"url": PR, "label": None}) == JobLink(url=PR, label=None)
    assert parse_link(None) is None


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ({"url": "javascript:alert(1)"}, "must start with http:// or https://"),
        ({"url": "JAVASCRIPT:alert(1)"}, "must start with http:// or https://"),
        ({"url": "data:text/html,<b>x</b>"}, "must start with http:// or https://"),
        ({"url": "ftp://example.com/a"}, "must start with http:// or https://"),
        ({"url": "//example.com/a"}, "must start with http:// or https://"),
        ({"url": "https://"}, "absolute"),
        ({"url": "https:///path-only"}, "absolute"),
        ({"url": "https://example.com/a b"}, "spaces or control characters"),
        ({"url": "https://example.com/a\tb"}, "spaces or control characters"),
        ({"url": "https://example.com/\x00"}, "spaces or control characters"),
        ({"url": "https://example.com/‮"}, "spaces or control characters"),
        ({"url": "https://example.com/" + "a" * LINK_URL_MAX}, f"at most {LINK_URL_MAX}"),
        ({"url": PR, "label": "x" * (LINK_LABEL_MAX + 1)}, f"at most {LINK_LABEL_MAX}"),
        ({"url": PR, "label": "PR\n#816"}, "control characters"),
        ({"url": PR, "label": "PR ‮618#"}, "control characters"),
        ({"url": PR, "label": 816}, "link.label must be a string"),
        ({"label": "PR #816"}, "link.url is required"),
        ({"url": 5}, "link.url must be a string"),
        ("https://example.com", "link must be an object"),
        ([PR], "link must be an object"),
    ],
)
def test_a_bad_link_is_refused_with_a_sentence_that_names_the_field(raw, needle):
    with pytest.raises(LinkError) as e:
        parse_link(raw)
    assert needle in str(e.value), str(e.value)


def test_the_limits_are_the_contract_numbers():
    assert LINK_URL_MAX == 500 and LINK_LABEL_MAX == 40
    exact = "https://example.com/" + "a" * (LINK_URL_MAX - len("https://example.com/"))
    assert len(exact) == LINK_URL_MAX and parse_link({"url": exact}).url == exact
    # 한국어 라벨도 글자 수로 센다(바이트가 아니다)
    assert parse_link({"url": PR, "label": "가" * LINK_LABEL_MAX}).label == "가" * LINK_LABEL_MAX


# ── 서버 ─────────────────────────────────────────────────────────────────────


def test_submit_with_a_link_returns_it_on_the_job_and_in_the_queue(srv):
    status, body = submit_with(srv, {"url": PR, "label": "PR #816"})
    assert status == 201, body
    jid = body["job_id"]
    view = srv.req("GET", f"/jobs/{jid}")[1]
    assert view["link"] == {"url": PR, "label": "PR #816"}
    queue = srv.req("GET", "/api/status")[1]["pools"][0]["queue"]
    assert [r["link"] for r in queue if r["id"] == jid] == [{"url": PR, "label": "PR #816"}]


def test_a_job_without_a_link_says_null_not_missing(srv):
    jid = submit_with(srv, ...)[1]["job_id"]
    view = srv.req("GET", f"/jobs/{jid}")[1]
    assert "link" in view and view["link"] is None
    other = submit_with(srv, None, tree_hash="ab" * 32)[1]["job_id"]
    assert srv.req("GET", f"/jobs/{other}")[1]["link"] is None


def test_a_link_without_a_label_keeps_label_null(srv):
    jid = submit_with(srv, {"url": PR})[1]["job_id"]
    assert srv.req("GET", f"/jobs/{jid}")[1]["link"] == {"url": PR, "label": None}


@pytest.mark.parametrize(
    ("link", "needle"),
    [
        ({"url": "javascript:alert(document.cookie)"}, "link.url must start with http://"),
        ({"url": "https://example.com/" + "a" * 500}, "link.url must be at most 500 characters"),
        ({"url": PR, "label": "x" * 41}, "link.label must be at most 40 characters"),
        ("https://example.com", "link must be an object"),
    ],
)
def test_a_bad_link_is_a_400_and_no_job_is_made(srv, link, needle):
    status, body = submit_with(srv, link)
    assert status == 400, body
    assert needle in body["error"], body
    assert srv.store.list_active() == []


def test_a_joiner_keeps_the_first_submitters_link(srv):
    first = submit_with(srv, {"url": PR, "label": "PR #816"})[1]
    status, second = submit_with(srv, {"url": PR + "0", "label": "PR #8160"}, token="bob")
    assert status == 200 and second["joined"] is True and second["job_id"] == first["job_id"]
    view = srv.req("GET", f"/jobs/{first['job_id']}")[1]
    assert view["link"] == {"url": PR, "label": "PR #816"}
    # 링크 없이 합류해도 지워지지 않는다
    submit_with(srv, ..., token="admin")
    assert srv.req("GET", f"/jobs/{first['job_id']}")[1]["link"]["label"] == "PR #816"


def test_a_finished_job_keeps_its_link_in_the_recent_list(tmp_path):
    live = Server(tmp_path, workers=True)
    try:
        body = {
            "preset": "ok",
            "inputs": {},
            "source": {"mode": "tree", "tree_hash": "9f" * 32, "bytes": 10},
            "link": {"url": PR, "label": "PR #816"},
        }
        jid = live.req("POST", "/jobs", token="alice", json_body=body)[1]["job_id"]
        assert live.upload(jid)[0] == 200
        assert live.wait_terminal(jid).state == "succeeded"
        recent = live.req("GET", "/api/status")[1]["pools"][0]["recent"]
        assert [r["link"] for r in recent if r["id"] == jid] == [{"url": PR, "label": "PR #816"}]
        assert live.req("GET", f"/jobs/{jid}")[1]["link"] == {"url": PR, "label": "PR #816"}
    finally:
        live.close()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_git_ref_job_takes_a_link_too(tmp_path):
    from test_server_m3 import build_bare_repo, git_server

    bare = build_bare_repo(tmp_path)
    s = git_server(tmp_path, bare)
    try:
        body = {
            "preset": "deploy",
            "inputs": {},
            "source": {"mode": "git_ref", "ref": "main"},
            "link": {"url": PR, "label": "PR #816"},
        }
        status, resp = s.req("POST", "/jobs", token="alice", json_body=body)
        assert status == 201, resp
        assert s.req("GET", f"/jobs/{resp['job_id']}")[1]["link"] == {
            "url": PR,
            "label": "PR #816",
        }
        body["link"] = {"url": "javascript:void(0)"}
        status, resp = s.req("POST", "/jobs", token="bob", json_body=body)
        assert status == 400 and "link.url" in resp["error"], resp
    finally:
        s.close()


# ── 저장소 ───────────────────────────────────────────────────────────────────


def jobs_columns(path) -> list[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[1] for r in c.execute("PRAGMA table_info(jobs)")]
    finally:
        c.close()


def test_this_build_is_database_version_22_and_jobs_end_with_the_link_columns(tmp_path):
    assert DB_VERSION == 22
    path = tmp_path / "data" / "rcm.sqlite3"
    Store(path).close()
    # 마이그레이션이 뒤에 붙이므로 새 DB 에서도 맨 끝이다 — 두 DB 의 열 차례가 같아야 한다
    assert jobs_columns(path)[-2:] == ["link_url", "link_label"]


def test_the_link_round_trips_through_the_store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    try:
        with_link = enqueue(s, tree="aaaa", now=NOW, link=JobLink(url=PR, label="PR #816"))
        without = enqueue(s, tree="bbbb", now=NOW)
        assert s.get_job(with_link.id).link == JobLink(url=PR, label="PR #816")
        assert s.get_job(without.id).link is None
        bare = enqueue(s, tree="cccc", now=NOW, link=JobLink(url=PR, label=None))
        assert s.get_job(bare.id).link == JobLink(url=PR, label=None)
    finally:
        s.close()


def test_a_v21_database_gains_the_link_columns_after_a_backup(tmp_path):
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    old = enqueue(s, tree="aaaa", now=NOW)
    s.close()
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE jobs DROP COLUMN link_label")
    c.execute("ALTER TABLE jobs DROP COLUMN link_url")
    c.execute("PRAGMA user_version=21")
    c.commit()
    c.close()
    assert "link_url" not in jobs_columns(path)
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert jobs_columns(path)[-2:] == ["link_url", "link_label"]
        assert s.get_job(old.id).link is None  # 옛 잡은 링크가 없다 — 지어내지 않는다
        new = enqueue(s, tree="bbbb", now=NOW, link=JobLink(url=PR, label="PR #816"))
        assert s.get_job(new.id).link.label == "PR #816"
    finally:
        s.close()
    bak = path.parent / "backup" / "rcm.sqlite3.v21.bak"
    assert bak.exists()
    assert "link_url" not in jobs_columns(bak)


# ── 클라이언트 · CLI ──────────────────────────────────────────────────────────


def capture_posts(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def fake_post(self, path, body):
        sent.append({"path": path, "body": json.loads(json.dumps(body))})
        return {"job_id": 1, "joined": False, "state": "uploading"}

    monkeypatch.setattr(Client, "post_json", fake_post)
    return sent


def test_client_submit_puts_the_link_in_the_body_only_when_given(monkeypatch):
    sent = capture_posts(monkeypatch)
    c = Client("http://127.0.0.1:9", "t")
    c.submit("ok", {}, {"mode": "tree"}, requester_label=None, link=PR, link_label="PR #816")
    c.submit("ok", {}, {"mode": "tree"}, requester_label=None, link=PR)
    c.submit("ok", {}, {"mode": "tree"}, requester_label=None)
    assert sent[0]["body"]["link"] == {"url": PR, "label": "PR #816"}
    assert sent[1]["body"]["link"] == {"url": PR}
    # 링크가 없으면 키도 없다 — 옛 서버와 오늘의 본문이 같다
    assert "link" not in sent[2]["body"]


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def last_json(out: str) -> dict:
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    assert lines, out
    return json.loads(lines[-1])


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RCM_LABEL", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "hello.txt").write_text("hello\n")
    monkeypatch.chdir(proj)

    def use(server: Server, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


def test_run_help_carries_the_feature_detection_string(capsys):
    """호출자(dolomood-renew 의 remote_ci.sh)는 `rcm run --help` 에 `--link-label` 이 있으면
    링크를 붙인다 — 이 문자열이 계약이다."""
    with pytest.raises(SystemExit) as e:
        main(["run", "--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0
    assert "--link-label" in out and "--link URL" in out


def test_run_with_link_flags_sends_them_and_the_job_shows_them(srv, env, capsys):
    env(srv)
    code, out, err = run(
        capsys, ["run", "ok", "--no-wait", "--link", PR, "--link-label", "PR #816"]
    )
    assert code == 0, err
    jid = last_json(out)["job_id"]
    assert srv.req("GET", f"/jobs/{jid}")[1]["link"] == {"url": PR, "label": "PR #816"}


def test_run_link_label_without_link_is_a_usage_error_before_any_request(env, capsys, monkeypatch):
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:9")  # 닿으면 exit 3 이 된다
    monkeypatch.setenv("RCM_TOKEN", "x")
    code, _, err = run(capsys, ["run", "ok", "--no-wait", "--link-label", "PR #816"])
    assert code == 2, err
    assert "--link-label needs --link" in err


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["--link", "javascript:alert(1)"], "must start with http:// or https://"),
        (["--link", PR, "--link-label", "x" * 41], "at most 40 characters"),
    ],
)
def test_run_with_a_bad_link_is_a_usage_error_before_any_request(
    env, capsys, monkeypatch, argv, needle
):
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:9")
    monkeypatch.setenv("RCM_TOKEN", "x")
    code, _, err = run(capsys, ["run", "ok", "--no-wait", *argv])
    assert code == 2, err
    assert needle in err and "--link" in err, err
