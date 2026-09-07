"""CLI(M5b-3) — `rcm worker` 의 인자 · 토큰 규칙 · `--check` 표 · 서버 403/409 문구 · `--once`.

명세는 docs/m5b3-workplan.md §2:
    rcm worker --server URL --pool NAME [--lanes N] [--name NAME] [--config worker.toml]
               [--data DIR] [--check] [--once]
- 토큰은 `RCM_WORKER_TOKEN`(우선) 또는 `--config` 의 `token`. 없으면 usage 2 이고 stderr 가
  `RCM_WORKER_TOKEN` 을 말한다. `--token` 플래그는 **없다**.
- 워커 토큰(kind worker)이어야 한다 — 서버가 403 을 주면 `worker token required` + 안내
  `rcm token add NAME --worker` 로 종료 2.
- register 409(버전 불일치)는 서버 메시지를 그대로 찍고 종료 2.
- `--check`: `/api/health` + `/api/whoami`(kind worker 확인) + `[[repos]]` 의 ls-remote →
  `rcm check` 와 같은 모양의 표(`ok ` / `FAIL` · 이름 · 상세). 행 `server` · `token` · `pool` ·
  `repos`(없으면 n/a). 문제면 종료 1. 서버에 못 닿으면 `cannot reach <url>`. **등록하지 않는다**
  (등록은 옛 잡을 lost 로 닫는 부작용이 있다).
- `--once`(이 명세에서 추가 — 시험·cron 용): 잡을 **하나만** 돌리고 종료 0 — 잡이 올 때까지는
  보통처럼 long-poll 로 기다린다(빈 큐라고 바로 끝나지 않는다). 나머지 잡은 queued 그대로. 긴
  루프(heartbeat · 취소 · lost · 신호)는 C 의 e2e 가 잠근다.
- claim 이 `wait_seconds` 보다 먼저 204 로 돌아오면(서버 `worker_claim_wait_seconds = 0` · long-poll
  슬롯 초과) 바로 다시 두드리지 않고 잠깐 쉰다 — 아니면 워커가 CPU 100% 로 서버를 때린다.

test_cli_m4 처럼 `main(argv)` 를 in-process 로 부르고 서버는 `test_worker_api.WorkerServer`(진짜
`/worker/*`). 토큰은 `RCM_WORKER_TOKEN` 환경변수(monkeypatch). HOME 은 tmp 로 옮겨 실제 설정을 안
본다. argparse 의 usage 오류는 `parse_args` 의 SystemExit(2) 라 `run` 이 코드로 바꾼다. 구현 전이라
빨간 것이 정상이다(오늘은 `worker` 가 모르는 부명령이라 argparse 가 2 로 끝난다 — 그래서 모든 시험이
종료 코드 말고도 문구·상태를 본다).
"""

from __future__ import annotations

import re
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor.cli import main
from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core.model import FAILED, QUEUED, SUCCEEDED
from test_server import sh
from test_worker_api import WORKER_PRESETS, WORKER_TOKEN_REQUIRED, WorkerServer

TOKEN_ENV = "RCM_WORKER_TOKEN"
WORKER = "build-02"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

#: test_worker_api 의 프리셋에서 `lin` 만 마커를 찍는 것으로 바꾼다(명세 §4 의 `lin`) — `--once`
#: 가 이 잡 하나를 끝까지 돌린다. 트리(`TAR`)에는 `hello.txt` 가 있어 `cat` 이 워크스페이스가
#: 풀렸음을 로그로 증명한다. `deploy`(git_ref · repo app · 기본 풀)는 그대로 쓴다.
LIN_SCRIPT = (
    "echo ::rcm::steps::2; echo ::rcm::step::build; cat hello.txt; echo ::rcm::step-end::ok; "
    "echo ::rcm::step::test; echo ::rcm::step-end::ok; echo '::rcm::summary::built ok'"
)
CLI_PRESETS = [
    *(p for p in WORKER_PRESETS if p["name"] != "lin"),
    sh("lin", LIN_SCRIPT, pool="linux"),
]
LIN_MARKERS = [
    ("steps", "2"),
    ("step", "build"),
    ("step-end", "ok"),
    ("step", "test"),
    ("step-end", "ok"),
    ("summary", "built ok"),
]


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def row_status(out: str, name: str) -> str | None:
    """`--check` 표에서 `name` 행의 상태(`ok`/`FAIL`). 행이 없으면 None(test_cli_m4 와 같다)."""
    m = re.search(rf"^(ok |FAIL)  {re.escape(name)}(?:\s|$)", out, re.M)
    return m.group(1).strip() if m else None


def row_line(out: str, name: str) -> str:
    line = next(
        (ln for ln in out.splitlines() if re.match(rf"^(ok |FAIL)  {name}(?:\s|$)", ln)), ""
    )
    assert line, f"no row {name!r} in:\n{out}"
    return line


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def url_of(srv: WorkerServer) -> str:
    return f"http://127.0.0.1:{srv.port}"


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """HOME 을 tmp 로, `XDG_CONFIG_HOME`·`RCM_*` 은 없이. cwd 도 빈 tmp 로. `~/.local/share` 를
    만들어 두어 기본 data_dir(`~/.local/share/rcm-worker`)의 부모가 쓰기 가능하다(`--check` 의
    data dir 행 — test_cli_m4 와 같은 요령)."""
    h = tmp_path / "home"
    (h / ".local" / "share").mkdir(parents=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN", "RCM_LABEL", TOKEN_ENV):
        monkeypatch.delenv(var, raising=False)
    return h


def make_server(tmp_path: Path, **overrides) -> WorkerServer:
    """진짜 `/worker/*` 서버. 프리셋은 `CLI_PRESETS`(마커를 찍는 `lin`)."""
    s = WorkerServer(tmp_path, **overrides)
    s.cfg.presets = tuple(parse_preset(p) for p in CLI_PRESETS)
    return s


@pytest.fixture
def srv(tmp_path):
    """claim long-poll 상한 2초 — 잡이 올라오면 `wake` 로 바로 깨고, 빈 큐라도 한 바퀴가 2초를 넘지
    않는다."""
    s = make_server(tmp_path, worker_claim_wait_seconds=2)
    yield s
    s.close()


@pytest.fixture
def srv0(tmp_path):
    """long-poll 이 **없는** 서버(`worker_claim_wait_seconds = 0`) — 빈 claim 이 즉시 204 다."""
    s = make_server(tmp_path, worker_claim_wait_seconds=0)
    yield s
    s.close()


@pytest.fixture
def token(monkeypatch, home):
    """`use(srv, name)` — 그 토큰을 `RCM_WORKER_TOKEN` 으로 건다."""

    def use(server: WorkerServer, name: str = WORKER) -> str:
        monkeypatch.setenv(TOKEN_ENV, server.tokens[name])
        return server.tokens[name]

    return use


@pytest.fixture
def data_dir(tmp_path) -> Path:
    return tmp_path / "worker-data"


def worker_argv(srv: WorkerServer, *extra: str, pool: str = "linux", data: Path | None = None):
    argv = ["worker", "--server", url_of(srv), "--pool", pool]
    if data is not None:
        argv += ["--data", str(data)]
    return [*argv, *extra]


# ── 인자 ─────────────────────────────────────────────────────────────────────


def test_help_lists_the_flags(home, capsys):
    """§2: `rcm worker --help` 에 --server · --pool · --lanes · --name · --config · --data ·
    --check · --once. `--token` 은 없다(토큰은 환경변수·파일로만)."""
    code, out, err = run(capsys, ["worker", "--help"])
    assert code == 0, err
    for flag in (
        "--server",
        "--pool",
        "--lanes",
        "--name",
        "--config",
        "--data",
        "--check",
        "--once",
    ):
        assert flag in out, (flag, out)
    assert "--token" not in out
    code, out, _ = run(capsys, ["--help"])
    assert code == 0 and re.search(r"\{[^}]*\bworker\b[^}]*\}", out), out  # 부명령 목록에 있다


def test_bad_lanes_is_a_usage_error(home, srv, token, capsys):
    """§2: `--lanes` 는 1~64 의 정수 — 0 · 65 · 문자열은 usage 2 이고 stderr 가 lanes 를 말한다."""
    token(srv)
    for bad in ("0", "65", "two"):
        code, out, err = run(capsys, worker_argv(srv, "--lanes", bad, "--check"))
        assert code == 2, (bad, out, err)
        assert "lanes" in err, (bad, err)
    assert srv.store.get_worker(WORKER) is None


def test_missing_server_is_a_usage_error(home, srv, token, capsys):
    """§2: `--server` 도 `--config` 의 `server` 도 없으면 usage 2, stderr 가 server 를 말한다."""
    token(srv)
    code, out, err = run(capsys, ["worker", "--pool", "linux", "--check"])
    assert code == 2, (out, err)
    assert "server" in err, err


# ── 토큰 ─────────────────────────────────────────────────────────────────────


def test_missing_token_is_a_usage_error_naming_the_env_var(home, srv, capsys):
    """§2: `RCM_WORKER_TOKEN` 도 파일의 `token` 도 없으면 종료 2, stderr 가 `RCM_WORKER_TOKEN` 을
    말한다. 서버에 가기 전에 끝난다(등록되지 않는다)."""
    code, out, err = run(capsys, worker_argv(srv))
    assert code == 2, (out, err)
    assert TOKEN_ENV in err, err
    assert srv.store.get_worker(WORKER) is None


def test_missing_token_with_check_is_also_2(home, srv, capsys):
    code, out, err = run(capsys, worker_argv(srv, "--check"))
    assert code == 2, (out, err)
    assert TOKEN_ENV in err, err


def test_token_from_config_file(home, srv, tmp_path, capsys):
    """§2: `--config worker.toml` 의 `token`(600) 으로도 된다 — `--check` 가 그 토큰으로 whoami
    한다."""
    cfg = tmp_path / "worker.toml"
    cfg.write_text(f'server = "{url_of(srv)}"\npool = "linux"\ntoken = "{srv.tokens[WORKER]}"\n')
    cfg.chmod(0o600)
    code, out, err = run(capsys, ["worker", "--config", str(cfg), "--check"])
    assert code == 0, out + err
    assert row_status(out, "token") == "ok" and WORKER in row_line(out, "token"), out
    assert row_status(out, "pool") == "ok" and "linux" in row_line(out, "pool"), out


def test_client_token_is_refused_with_the_worker_hint(home, srv, token, capsys):
    """§2: client 토큰이면 서버 register 가 403 `worker token required` — 그 문구와 안내
    `rcm token add NAME --worker` 를 찍고 종료 2. 등록되지 않는다."""
    token(srv, "alice")
    code, out, err = run(capsys, worker_argv(srv))
    assert code == 2, (out, err)
    assert WORKER_TOKEN_REQUIRED in err, err
    assert "rcm token add" in err and "--worker" in err, err
    assert srv.store.list_workers() == []


def test_check_with_a_client_token_fails_the_token_row(home, srv, token, capsys):
    """§2 `--check`: whoami 의 kind 가 worker 가 아니면 token 행 FAIL(kind 를 말한다) · 종료 1.
    서버 행은 ok(서버 자체는 멀쩡하다)."""
    token(srv, "alice")
    code, out, err = run(capsys, worker_argv(srv, "--check"))
    assert code == 1, out + err
    assert row_status(out, "server") == "ok", out
    assert row_status(out, "token") == "FAIL", out
    line = row_line(out, "token")
    assert "worker" in line and "client" in line, line
    assert srv.store.list_workers() == []


def test_invalid_token_fails_the_token_row(home, srv, monkeypatch, capsys):
    """§2 `--check`: 서버가 모르는 토큰(401)은 token 행 FAIL · 종료 1. 비밀은 출력에 없다."""
    monkeypatch.setenv(TOKEN_ENV, "not-a-real-token")
    code, out, err = run(capsys, worker_argv(srv, "--check"))
    assert code == 1, out + err
    assert row_status(out, "token") == "FAIL", out
    assert "not-a-real-token" not in out + err


# ── --check ──────────────────────────────────────────────────────────────────


def test_check_prints_a_table_and_exits_0_with_a_worker_token(home, srv, token, data_dir, capsys):
    """§2 `--check`: `rcm check` 와 같은 표 — `server`(URL · 버전) · `token`(이름 · kind worker) ·
    `pool` · `repos`(설정 없으면 `n/a`/`none`, ok) · `data dir`(`--data` 경로, 쓰기 가능). 종료 0.
    등록하지 않는다. stdout 은 표 행뿐이다."""
    token(srv)
    code, out, err = run(capsys, worker_argv(srv, "--check", data=data_dir))
    assert code == 0, out + err
    for name in ("server", "token", "pool", "repos", "data dir"):
        assert row_status(out, name) == "ok", (name, out)
    server = row_line(out, "server")
    assert url_of(srv) in server and __version__ in server, server
    tok = row_line(out, "token")
    assert WORKER in tok and "worker" in tok, tok
    assert srv.tokens[WORKER] not in out + err  # 비밀은 절대 안 찍는다
    assert re.search(r"\blinux\b", row_line(out, "pool")), out
    assert re.search(r"\b(n/a|none)\b", row_line(out, "repos")), out
    assert str(data_dir) in row_line(out, "data dir"), out
    assert srv.store.list_workers() == []  # --check 는 register 를 부르지 않는다
    assert all(ln.startswith(("ok ", "FAIL")) for ln in out.splitlines() if ln.strip()), out


def test_check_pool_defaults_to_default_and_flag_beats_file(home, srv, token, tmp_path, capsys):
    """§2: `--pool` 이 없으면 파일의 `pool`, 그것도 없으면 `default`. 플래그가 파일보다 우선."""
    token(srv)
    cfg = tmp_path / "worker.toml"
    cfg.write_text(f'server = "{url_of(srv)}"\npool = "linux"\n')
    code, out, err = run(capsys, ["worker", "--config", str(cfg), "--check"])
    assert code == 0, out + err
    assert re.search(r"\blinux\b", row_line(out, "pool")), out
    code, out, err = run(capsys, ["worker", "--config", str(cfg), "--pool", "default", "--check"])
    assert code == 0, out + err
    assert re.search(r"\bdefault\b", row_line(out, "pool")), out
    code, out, err = run(capsys, ["worker", "--server", url_of(srv), "--check"])
    assert code == 0, out + err
    assert re.search(r"\bdefault\b", row_line(out, "pool")), out


def test_check_unreachable_server_says_cannot_reach_and_exits_1(home, monkeypatch, capsys):
    """§2 `--check`: 서버에 못 닿으면 server 행 FAIL `cannot reach <url>` · 종료 1 — 재시도 없이
    바로(무한 재시도는 루프의 규칙이지 `--check` 의 것이 아니다)."""
    monkeypatch.setenv(TOKEN_ENV, "some-token")
    url = f"http://127.0.0.1:{free_port()}"
    code, out, err = run(capsys, ["worker", "--server", url, "--pool", "linux", "--check"])
    assert code == 1, out + err
    assert row_status(out, "server") == "FAIL", out
    assert f"cannot reach {url}" in out + err, out + err


@needs_git
def test_check_repos_row_runs_ls_remote(home, srv, token, tmp_path, capsys):
    """§2 `--check`: `[[repos]]` 가 있으면 repos 행이 각 레포에 ls-remote 를 해 본다 — 닿는 bare
    레포는 ok, 없는 경로는 FAIL(레포 이름을 말한다) · 종료 1."""
    from gitrepo import build_remote

    token(srv)
    (tmp_path / "git").mkdir()
    remote = build_remote(tmp_path / "git")
    good = tmp_path / "good.toml"
    good.write_text(
        f'server = "{url_of(srv)}"\npool = "linux"\n[[repos]]\nname = "app"\nurl = "{remote.url}"\n'
    )
    code, out, err = run(capsys, ["worker", "--config", str(good), "--check"])
    assert code == 0, out + err
    assert row_status(out, "repos") == "ok" and "app" in row_line(out, "repos"), out
    bad = tmp_path / "bad.toml"
    bad.write_text(
        f'server = "{url_of(srv)}"\npool = "linux"\n'
        f'[[repos]]\nname = "app"\nurl = "{tmp_path / "nowhere.git"}"\n'
    )
    code, out, err = run(capsys, ["worker", "--config", str(bad), "--check"])
    assert code == 1, out + err
    assert row_status(out, "repos") == "FAIL" and "app" in row_line(out, "repos"), out


def test_check_with_a_bad_config_file_is_usage_2(home, srv, token, tmp_path, capsys):
    """§2: 설정 오류(`ConfigError`)는 `rcm serve` 처럼 usage 2 — 메시지에 키 이름."""
    token(srv)
    cfg = tmp_path / "worker.toml"
    cfg.write_text(f'server = "{url_of(srv)}"\nlane = 2\n')
    code, out, err = run(capsys, ["worker", "--config", str(cfg), "--check"])
    assert code == 2, (out, err)
    assert "lane" in err, err


# ── register 409 ─────────────────────────────────────────────────────────────


def test_version_mismatch_prints_the_servers_message_and_exits_2(home, srv, token, capsys):
    """§2: register 409(버전 불일치)는 서버 메시지 `worker version X, server Y — install the same
    release` 를 **그대로** 찍고 종료 2. 재시도하지 않는다."""
    token(srv)
    srv.app.version = "9.9.9"  # 서버가 다른 릴리스인 척
    code, out, err = run(capsys, worker_argv(srv))
    assert code == 2, (out, err)
    assert f"worker version {__version__}, server 9.9.9 — install the same release" in err, err
    assert srv.store.get_worker(WORKER) is None


# ── --once: 잡 하나를 끝까지 ─────────────────────────────────────────────────


def test_once_runs_one_queued_job_end_to_end(home, srv, token, data_dir, capsys):
    """§2 루프 한 바퀴: register → claim → tree 받기 → `phase executing` → 실행(마커·출력을 `log`
    로) → `finish succeeded` → 종료 0. 서버에: 잡 succeeded · summary 는 마커 `built ok` · 마커
    전부 · 로그 전체(`hello` — 워크스페이스가 풀렸다). stderr 에 `claimed #N` · `#N succeeded`,
    토큰은 없다. 워커 쪽: `<data>/jobs/<id>/log.txt` 사본, 성공한 워크스페이스는 지운다."""
    secret = token(srv)
    jid = srv.queued_job(preset="lin")
    code, out, err = run(capsys, worker_argv(srv, "--once", "--lanes", "1", data=data_dir))
    assert code == 0, out + err
    j = srv.store.get_job(jid)
    assert j.state == SUCCEEDED, (j.state, j.summary, err)
    assert j.summary == "built ok" and j.exit_code == 0 and j.worker_name == WORKER
    log = srv.log_text(jid)
    assert "hello\n" in log and "::rcm::summary::built ok" in log, log
    assert [(m.kind, m.value) for m in srv.store.markers(jid)] == LIN_MARKERS  # 서버가 파싱했다
    row = srv.store.get_worker(WORKER)
    assert row is not None and row.pool == "linux" and row.lanes == 1
    assert srv.worker_lane(WORKER, 1)["state"] == "idle"
    assert f"claimed #{jid}" in err and f"#{jid} succeeded" in err, err
    assert secret not in out + err
    assert (data_dir / "jobs" / str(jid) / "log.txt").read_text() == log
    assert not (data_dir / "workspaces" / str(jid)).exists()


def test_once_runs_exactly_one_job_and_leaves_the_rest_queued(home, srv, token, data_dir, capsys):
    """§2 `--once`: 잡을 **하나만** — 같은 풀의 두 번째 잡도, 다른 풀의 잡도 queued 그대로. 등록은
    됐고(워커 행) 종료 0."""
    token(srv)
    first = srv.queued_job(preset="lin")
    second = srv.queued_job(token="bob", preset="lin")
    other_pool = srv.queued_job(token="admin")  # 기본 풀 — linux 워커는 받지 않는다
    code, out, err = run(capsys, worker_argv(srv, "--once", data=data_dir))
    assert code == 0, out + err
    assert srv.store.get_job(first).state == SUCCEEDED
    assert srv.store.get_job(second).state == QUEUED
    assert srv.store.get_job(other_pool).state == QUEUED
    assert srv.store.get_worker(WORKER) is not None
    assert err.count("claimed #") == 1, err


def test_once_waits_for_a_job_that_arrives_later(home, srv, token, data_dir, capsys):
    """§2 `--once` · claim long-poll: 빈 큐면 기다린다 — 0.3초 뒤에 올라온 잡을 `wake` 로 바로 받아
    끝까지 돌리고 종료 0(2초 long-poll 상한을 다 기다리지 않는다 — 전체 5초 안)."""
    token(srv)
    ids: list[int] = []
    timer = threading.Timer(0.3, lambda: ids.append(srv.queued_job(preset="lin")))
    timer.start()
    t0 = time.monotonic()
    code, out, err = run(capsys, worker_argv(srv, "--once", data=data_dir))
    elapsed = time.monotonic() - t0
    timer.join(5)
    assert code == 0, out + err
    assert ids and srv.store.get_job(ids[0]).state == SUCCEEDED, err
    assert elapsed < 5, elapsed


def test_an_immediate_204_is_not_retried_in_a_hot_loop(home, srv0, token, data_dir, capsys):
    """claim 이 즉시 204 로 돌아오는 서버(`worker_claim_wait_seconds = 0` — long-poll 슬롯이 다 찼을
    때도 같은 모양)에서 워커는 잠깐 쉬고 다시 묻는다: 잡이 0.5초 뒤에 오면 그 사이 claim 은 몇
    번이지 수백 번이 아니다. 오늘의 루프(`204 → 바로 다시`)는 CPU 100% 로 서버를 두드린다 — 이
    시험이 잠근다. 잡은 여전히 받아 끝낸다."""
    token(srv0)
    calls: list[float] = []
    orig = srv0.app.worker_claim

    def counting(tok, body):
        calls.append(time.monotonic())
        return orig(tok, body)

    srv0.app.worker_claim = counting  # type: ignore[method-assign]
    ids: list[int] = []
    timer = threading.Timer(0.5, lambda: ids.append(srv0.queued_job(preset="lin")))
    timer.start()
    code, out, err = run(capsys, worker_argv(srv0, "--once", data=data_dir))
    timer.join(5)
    assert code == 0, out + err
    assert ids and srv0.store.get_job(ids[0]).state == SUCCEEDED, err
    assert 1 <= len(calls) <= 10, f"{len(calls)} claims for one job — hot loop"


def test_once_reports_a_failed_job_and_still_exits_0(home, srv, token, data_dir, capsys):
    """§2: 잡이 실패해도 워커는 서비스다 — `finish failed` 를 보고하고 `--once` 는 종료 0. 서버에
    exit 코드와 failed_step. 실패한 워크스페이스는 남긴다(`keep_workspace_on_failure` 기본 true)."""
    token(srv)
    srv.cfg.presets = (
        *srv.cfg.presets,
        parse_preset(sh("badl", "echo ::rcm::step::t; exit 2", pool="linux")),
    )
    jid = srv.queued_job(preset="badl")
    code, out, err = run(capsys, worker_argv(srv, "--once", data=data_dir))
    assert code == 0, out + err
    j = srv.store.get_job(jid)
    assert j.state == FAILED and j.exit_code == 2 and j.failed_step == "t", (j.state, j.summary)
    assert f"#{jid} failed" in err, err
    assert (data_dir / "workspaces" / str(jid)).is_dir()


def test_once_git_ref_job_without_the_repo_fails_naming_the_repo(
    home, srv, token, data_dir, capsys
):
    """§2: git_ref 잡인데 워커 `[[repos]]` 에 그 레포가 없으면 tree 를 받지 않고 `finish failed`
    summary `repo 'app' is not configured on this worker`(exit_code null). 워커는 계속 서비스 —
    종료 0."""
    token(srv)
    jid = srv.git_ref_job()  # preset deploy · repo app · 기본 풀
    code, out, err = run(capsys, worker_argv(srv, "--once", pool="default", data=data_dir))
    assert code == 0, out + err
    j = srv.store.get_job(jid)
    assert j.state == FAILED, (j.state, j.summary, err)
    assert j.summary == "repo 'app' is not configured on this worker" and j.exit_code is None
