"""CLI(M3) — `rcm run PRESET --ref REF`(git_ref 결정 규칙 · usage 2 · JSON 출력 · 스냅샷 생략 ·
wait) · `rcm top`/`jobs`/`presets` 의 git_ref 표시. 명세는 docs/m3-workplan.md §1.5.

test_cli_m1 처럼 `main(argv)` 를 in-process 로 부르고 RCM_SERVER/RCM_TOKEN 만으로 서버를 가리킨다.
git 은 tmp 안의 bare 레포뿐이다. git 이 PATH 에 없으면 전부 skip.
"""

import json
import re
import shutil

import pytest

import remote_ci_monitor.cli as cli_mod
from remote_ci_monitor.client import Client
from test_cli_m1 import last_json, run
from test_server import Server
from test_server_m3 import build_bare_repo, git_server, submit_ref

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_m1.env 와 같다 — `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RCM_LABEL", raising=False)

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def bare(tmp_path):
    return build_bare_repo(tmp_path)


@pytest.fixture
def git_srv(tmp_path, bare):
    s = git_server(tmp_path, bare)
    yield s
    s.close()


@pytest.fixture
def git_live(tmp_path, bare):
    s = git_server(tmp_path, bare, workers=True)
    yield s
    s.close()


@pytest.fixture
def cwd(monkeypatch, tmp_path):
    """스냅샷을 찍으면 곧바로 티가 나게 빈 디렉터리에서 돌린다."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    return empty


def refuse_submit(monkeypatch) -> list:
    """`Client.submit` 이 불리면 실패시키고 기록한다 — 「서버에 보내기 전에 2」 를 증명한다."""
    calls: list = []

    def refuse(self, *args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("rcm run must not submit after a usage error")

    monkeypatch.setattr(Client, "submit", refuse)
    return calls


def count_snapshots(monkeypatch) -> list:
    calls: list = []
    real = cli_mod.make_snapshot

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(cli_mod, "make_snapshot", counting)
    return calls


# ── run --ref ────────────────────────────────────────────────────────────────


def test_run_ref_no_wait_prints_ref_and_sha_and_skips_the_snapshot(
    git_srv, bare, env, cwd, capsys, monkeypatch
):
    env(git_srv)
    snapshots = count_snapshots(monkeypatch)
    code, out, err = run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])
    assert code == 0, err
    body = last_json(out)
    assert isinstance(body["job_id"], int) and body["joined"] is False
    assert body["state"] == "queued" and body["ref"] == "main"
    assert re.fullmatch(r"[0-9a-f]{40}", body["sha"]) and body["sha"] == bare.main_sha
    assert body["url"].endswith(f"/#/jobs/{body['job_id']}")
    assert "snapshot" not in err.lower() and "uploading" not in err.lower(), err
    assert snapshots == []
    assert f"#{body['job_id']}" in err and bare.main_sha[:7] in err, err
    view = git_srv.req("GET", f"/jobs/{body['job_id']}")[1]
    assert view["state"] == "queued"
    assert view["source"] == {"mode": "git_ref", "repo": "app", "ref": "main", "sha": bare.main_sha}
    # 다른 세션이 같은 커밋을 태그 이름으로 넣으면 합류한다
    env(git_srv, "bob")
    code, out, err = run(capsys, ["run", "deploy", "--ref", "v1", "--no-wait"])
    assert code == 0, err
    again = last_json(out)
    assert again["joined"] is True and again["job_id"] == body["job_id"]
    assert again["sha"] == bare.main_sha and again["ref"] == "v1"
    assert "joined job" in err
    # --source git_ref 를 명시해도 같다
    code, out, err = run(
        capsys, ["run", "deploy", "--source", "git_ref", "--ref", "main", "--no-wait"]
    )
    assert code == 0 and last_json(out)["joined"] is True
    assert snapshots == []


def test_run_git_ref_preset_without_ref_is_a_usage_error(git_srv, env, cwd, capsys, monkeypatch):
    env(git_srv)
    calls = refuse_submit(monkeypatch)
    snapshots = count_snapshots(monkeypatch)
    code, out, err = run(capsys, ["run", "deploy", "--no-wait"])
    assert code == 2 and "needs --ref" in err and "deploy" in err, err
    assert out.strip() == ""
    code, _, err = run(capsys, ["run", "deploy", "--source", "git_ref", "--no-wait"])
    assert code == 2 and "needs --ref" in err, err
    assert calls == [] and snapshots == []
    assert git_srv.req("GET", "/api/status")[1]["pools"][0]["queue"] == []


def test_run_ref_on_a_tree_only_preset_and_tree_on_a_git_ref_preset_are_usage_errors(
    git_srv, env, cwd, capsys, monkeypatch
):
    env(git_srv)
    calls = refuse_submit(monkeypatch)
    code, out, err = run(capsys, ["run", "gate", "--ref", "main", "--no-wait"])
    assert code == 2 and "gate" in err, err
    assert out.strip() == ""
    code, _, err = run(capsys, ["run", "deploy", "--source", "tree", "--no-wait"])
    assert code == 2 and "deploy" in err, err
    assert calls == []
    assert git_srv.req("GET", "/api/status")[1]["pools"][0]["queue"] == []


@pytest.mark.parametrize(
    "ref_args", [["--ref=-x"], ["--ref", "a..b"], ["--ref", ""], ["--ref", "x^"], ["--ref", "a b"]]
)
def test_run_rejects_bad_refs_before_any_submit(git_srv, env, cwd, capsys, monkeypatch, ref_args):
    env(git_srv)
    calls = refuse_submit(monkeypatch)
    code, out, err = run(capsys, ["run", "deploy", *ref_args, "--no-wait"])
    assert code == 2 and "ref" in err.lower(), err
    assert calls == [] and out.strip() == ""
    assert git_srv.req("GET", "/api/status")[1]["pools"][0]["queue"] == []


def test_run_unknown_ref_is_a_usage_error_not_unknown(git_srv, env, cwd, capsys, monkeypatch):
    """서버의 502 「cannot resolve」 는 확정 거절(잡 없음) — 400 처럼 exit 2. 3 은 「모른다」."""
    env(git_srv)
    snapshots = count_snapshots(monkeypatch)
    code, out, err = run(capsys, ["run", "deploy", "--ref", "nope", "--no-wait"])
    assert code == 2, err
    assert "cannot resolve" in err and "'nope'" in err, err
    assert out.strip() == "" and snapshots == []
    assert git_srv.req("GET", "/api/status")[1]["pools"][0]["queue"] == []


def test_run_ref_waits_and_succeeds_end_to_end(git_live, bare, env, cwd, capsys, monkeypatch):
    env(git_live)
    snapshots = count_snapshots(monkeypatch)
    code, out, err = run(capsys, ["run", "deploy", "--ref", "main", "--by", "cli@m3"])
    assert code == 0, err
    body = last_json(out)
    assert body["state"] == "succeeded" and body["wait_exit_code"] == 0 and body["exit_code"] == 0
    assert body["source"] == {"mode": "git_ref", "repo": "app", "ref": "main", "sha": bare.main_sha}
    assert body["requester"]["label"] == "cli@m3"
    assert [t["state"] for t in body["transitions"]] == ["queued", "running", "succeeded"]
    log = git_live.app.log_path(body["id"]).read_text()
    assert "app repo" in log and "ref=main" in log and "ok" in log  # 체크아웃된 트리에서 돌았다
    assert snapshots == [] and "uploading" not in err.lower()
    # 미러가 생겼고, 워크스페이스는 성공 뒤 지워졌다
    assert (git_live.cfg.data_dir / "mirrors" / "app").is_dir()
    assert not (git_live.cfg.data_dir / "workspaces" / str(body["id"])).exists()


# ── top · jobs · presets ─────────────────────────────────────────────────────


def test_top_and_jobs_show_the_git_ref_source(git_srv, bare, env, capsys):
    env(git_srv, token=None)  # 읽기는 토큰이 필요 없다
    status, resp = submit_ref(git_srv, "main")
    assert status == 201, resp
    jid = resp["job_id"]
    code, out, _ = run(capsys, ["top"])
    assert code == 0
    line = next(ln for ln in out.splitlines() if f"#{jid}" in ln)
    assert f"@{bare.main_sha[:7]}" in line and "ref main" in line and "app" in line, line
    assert "not received yet" not in out
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    row = next(r for r in json.loads(out) if r["id"] == jid)
    assert row["source"] == {"mode": "git_ref", "repo": "app", "ref": "main", "sha": bare.main_sha}
    code, out, _ = run(capsys, ["jobs"])
    assert code == 0 and f"#{jid}" in out and "queued" in out


def test_presets_show_repo_for_git_ref_presets(git_srv, env, capsys):
    env(git_srv, token=None)
    code, out, _ = run(capsys, ["presets", "--json"])
    assert code == 0
    by_name = {p["name"]: p for p in json.loads(out)}
    assert by_name["deploy"]["repo"] == "app" and by_name["gate"]["repo"] is None
    code, out, _ = run(capsys, ["presets"])
    assert code == 0
    deploy = next(ln for ln in out.splitlines() if ln.startswith("deploy"))
    gate = next(ln for ln in out.splitlines() if ln.startswith("gate"))
    assert "git_ref" in deploy and "app" in deploy, deploy
    assert "app" not in gate and "tree" in gate, gate
