"""릴리스 드라이버 러너(`release_driver.py`) — 분리 실행 · 로그 · 종료 코드 · 내부 토큰 폐기 ·
pid 생존 · `--status` · 명령줄 조립. 드라이버는 작은 가짜 셸 스크립트다(네트워크 없음).

명세: 스토어 탭 API 계약 v2 「Driver」 · docs/release-contract.md §5.
"""

from __future__ import annotations

import os
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.release_driver import (
    KIND_ABORT,
    KIND_CONFIRM,
    KIND_RETRY,
    KIND_START,
    LOG_TAIL_LINES,
    DriverRunner,
    driver_argv,
    log_tail,
    pid_alive,
    plan_n,
    token_name,
)
from remote_ci_monitor.store import Store

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
FAKE = """#!/bin/sh
echo "driver $*"
echo "server=${RCM_SERVER:-none} token=${RCM_TOKEN:+set} secrets=${APP_SECRETS:-none}"
case " $* " in
  *" --status "*) echo "stage: S1 planned"; echo "sha: abc"; exit 0;;
  *" --confirm-build-number "*) echo "confirmed $*"; exit 0;;
  *" --abort "*) echo "aborted"; exit 0;;
  *" --retry "*) echo "retried"; exit 0;;
esac
while [ -f hold ]; do sleep 0.05; done
echo "plan: N = 181"
exit 2
"""


def write_script(path: Path, text: str = FAKE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "data" / "rcm.sqlite3")
    yield s
    s.close()


@pytest.fixture
def runner(store: Store):
    logs: list[str] = []
    r = DriverRunner(store, now_fn=lambda: NOW, log=logs.append)
    r.logs = logs  # type: ignore[attr-defined]
    return r


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    d = tmp_path / "checkout"
    d.mkdir()
    write_script(d / "scripts" / "driver.sh")
    return d


def spawn(runner: DriverRunner, tmp_path: Path, checkout: Path, **kw):
    args = {
        "data_dir": tmp_path / "data",
        "repo": "app",
        "driver": checkout / "scripts" / "driver.sh",
        "checkout": checkout,
        "build_name": "1.0.1",
        "kind": KIND_START,
        "started_by": "macmini-admin",
        "env": {"APP_SECRETS": str(tmp_path / "secrets")},
        "server_url": "http://127.0.0.1:1",
    }
    args.update(kw)
    return runner.spawn(**args)


# ── 명령줄 ───────────────────────────────────────────────────────────────────


def test_the_command_line_follows_the_contract_and_carries_only_typed_numbers():
    d = Path("/r/scripts/driver.sh")
    assert driver_argv(d, build_name="1.0.1", kind=KIND_START) == [str(d), "--build-name", "1.0.1"]
    assert driver_argv(
        d, build_name="1.0.1", kind=KIND_START, android_track="internal", dry_run=True
    ) == [str(d), "--build-name", "1.0.1", "--android-track", "internal", "--dry-run"]
    assert driver_argv(d, build_name="1.0.1", kind=KIND_CONFIRM, confirm_n=181) == [
        str(d),
        "--build-name",
        "1.0.1",
        "--confirm-build-number",
        "181",
    ]
    assert driver_argv(d, build_name="1.0.1", kind=KIND_ABORT)[-1] == "--abort"
    assert driver_argv(d, build_name="1.0.1", kind=KIND_RETRY)[-1] == "--retry"
    for name in ("automatic_release", "rollout", "release_status"):
        assert name not in " ".join(driver_argv(d, build_name="1.0.1", kind=KIND_START))


# ── 실행 · 로그 · 종료 ────────────────────────────────────────────────────────


def test_start_runs_the_driver_detached_logs_to_the_build_log_and_records_exit_2(
    runner, store, tmp_path, checkout
):
    row = spawn(runner, tmp_path, checkout)
    assert row["id"] == 1 and row["kind"] == "start" and row["pid"] and row["finished_at"] is None
    assert row["log_path"] == str(tmp_path / "data" / "driver" / "app" / "1.0.1.log")
    assert row["token_name"] == token_name("app", 1) == "store-driver:app:1"
    assert runner.wait(1, timeout=10)
    done = store.get_release(1)
    assert done["exit_code"] == 2 and done["finished_at"] == NOW
    text = Path(done["log_path"]).read_text()
    assert text.splitlines()[0].startswith("--- rcm driver start #1 2026-09-17T12:00:00Z by ")
    assert "driver --build-name 1.0.1" in text
    assert "server=http://127.0.0.1:1 token=set secrets=" in text
    assert "plan: N = 181" in text
    assert plan_n(Path(done["log_path"])) == 181
    assert (tmp_path / "data" / "driver" / "app" / "1.exit").read_text() == "2"
    assert not pid_alive(row["pid"])  # 감시 스레드가 거둬서 좀비도 아니다
    assert any("exit 2" in line for line in runner.logs)


def test_the_internal_token_is_a_plain_client_token_while_running_and_revoked_after(
    runner, store, tmp_path, checkout
):
    (checkout / "hold").touch()
    row = spawn(runner, tmp_path, checkout)
    info = {t.name: t for t in store.list_tokens()}[row["token_name"]]
    assert info.kind == "client" and info.admin is False and info.revoked_at is None
    assert runner.running("app")["id"] == row["id"]
    (checkout / "hold").unlink()
    assert runner.wait(row["id"], timeout=10)
    info = {t.name: t for t in store.list_tokens()}[row["token_name"]]
    assert info.revoked_at == NOW
    assert runner.running("app") is None


def test_the_token_secret_reaches_only_the_child_environment(
    runner, store, tmp_path, checkout, monkeypatch
):
    real = store.add_token
    monkeypatch.setattr(
        store, "add_token", lambda name, **kw: real(name, **kw) and "SECRET-TOKEN-VALUE-XYZ"
    )
    write_script(
        checkout / "scripts" / "driver.sh", '#!/bin/sh\necho "len=${#RCM_TOKEN}"\nexit 0\n'
    )
    row = spawn(runner, tmp_path, checkout)
    assert runner.wait(row["id"], timeout=10)
    log_text = Path(row["log_path"]).read_text()
    assert "len=22" in log_text  # 자식은 받았다
    assert "SECRET-TOKEN-VALUE-XYZ" not in log_text
    assert "SECRET-TOKEN-VALUE-XYZ" not in " ".join(runner.logs)
    assert "SECRET-TOKEN-VALUE-XYZ" not in repr(store.get_release(row["id"]))


def test_confirm_abort_and_retry_append_to_the_same_build_log(runner, store, tmp_path, checkout):
    spawn(runner, tmp_path, checkout)
    assert runner.wait(1, timeout=10)
    spawn(runner, tmp_path, checkout, kind=KIND_CONFIRM, confirm_n=181, android_track="beta")
    assert runner.wait(2, timeout=10)
    spawn(runner, tmp_path, checkout, kind=KIND_ABORT)
    assert runner.wait(3, timeout=10)
    spawn(runner, tmp_path, checkout, kind=KIND_RETRY, dry_run=True)
    assert runner.wait(4, timeout=10)
    text = Path(store.get_release(1)["log_path"]).read_text()
    assert "confirmed --build-name 1.0.1 --android-track beta --confirm-build-number 181" in text
    assert "driver --build-name 1.0.1 --abort" in text
    assert "driver --build-name 1.0.1 --dry-run --retry" in text
    r2 = store.get_release(2)
    assert r2["confirmed_n"] == 181 and r2["confirmed_by"] == "macmini-admin"
    assert r2["android_track"] == "beta" and r2["exit_code"] == 0
    assert store.get_release(4)["dry_run"] is True
    assert store.latest_release("app")["id"] == 4
    assert store.latest_release("app", "9.9.9") is None
    assert all(t.revoked_at is not None for t in store.list_tokens())


def test_a_driver_that_cannot_start_closes_the_row_and_revokes_the_token(
    runner, store, tmp_path, checkout
):
    """실행 파일 검사는 서버가 한다(409 `driver_missing`). 러너가 못 띄우는 경우는 체크아웃 폴더가
    사라진 것 같은 OS 오류다 — 열린 행과 발급한 토큰을 남기지 않는다."""
    with pytest.raises(OSError):
        spawn(runner, tmp_path, tmp_path / "gone", driver=checkout / "scripts" / "driver.sh")
    row = store.get_release(1)
    assert row["pid"] is None and row["exit_code"] is None and row["finished_at"] == NOW
    tokens = store.list_tokens()
    assert tokens and all(t.revoked_at is not None for t in tokens)


def test_an_unknown_kind_is_refused_before_anything_is_written(runner, store, tmp_path, checkout):
    with pytest.raises(ValueError):
        spawn(runner, tmp_path, checkout, kind="publish")
    assert store.count_releases() == 0 and store.list_tokens() == []


# ── 서버 재기동 뒤 — pid 생존과 대장 정리 ──────────────────────────────────────


def dead_pid() -> int:
    proc = subprocess.run(["true"])
    pid = proc.pid if hasattr(proc, "pid") else 0
    if not pid or pid_alive(pid):
        pid = 2**22 + 7  # 그런 pid 는 없다(macOS 99998 · Linux 기본 4194304 안)
        while pid_alive(pid):
            pid += 1
    return pid


def test_pid_alive_says_no_for_a_finished_process_and_yes_for_this_one():
    assert pid_alive(os.getpid()) is True
    assert pid_alive(None) is False and pid_alive(0) is False and pid_alive(-1) is False
    assert pid_alive(dead_pid()) is False


def test_reconcile_closes_orphans_with_the_exit_file_and_revokes_their_tokens(
    runner, store, tmp_path
):
    """서버가 재기동되면 감시 스레드는 없다. 열린 행의 pid 가 죽어 있으면 exit 파일에서 코드를
    읽어 닫고(없으면 None) 토큰을 폐기한다. 아직 도는 것은 건드리지 않는다."""
    data_dir = tmp_path / "data"
    root = data_dir / "driver" / "app"
    root.mkdir(parents=True)
    log = root / "1.0.1.log"
    log.write_text("plan: N = 180\nplan: N = 181\n")
    ids = []
    for n in (1, 2, 3):
        rid = store.create_release(
            repo="app",
            build_name="1.0.1",
            kind="start",
            started_by="admin",
            now=NOW - timedelta(minutes=n),
            log_path=str(log),
        )
        store.add_token(token_name("app", rid), admin=False, now=NOW, kind="client")
        ids.append(rid)
    store.set_release_started(ids[0], pid=dead_pid(), token_name=token_name("app", ids[0]))
    (root / f"{ids[0]}.exit").write_text("3")
    store.set_release_started(ids[1], pid=dead_pid(), token_name=token_name("app", ids[1]))
    store.set_release_started(ids[2], pid=os.getpid(), token_name=token_name("app", ids[2]))
    assert runner.reconcile("app", data_dir=data_dir) == ids[:2]
    assert store.get_release(ids[0])["exit_code"] == 3
    assert store.get_release(ids[1])["exit_code"] is None
    assert store.get_release(ids[1])["finished_at"] == NOW
    assert store.get_release(ids[2])["finished_at"] is None
    revoked = {t.name: t.revoked_at is not None for t in store.list_tokens()}
    assert revoked == {
        token_name("app", ids[0]): True,
        token_name("app", ids[1]): True,
        token_name("app", ids[2]): False,
    }
    assert runner.running("app")["id"] == ids[2]
    assert runner.reconcile("app", data_dir=data_dir) == []  # 두 번째는 할 일이 없다
    assert plan_n(log) == 181  # 마지막 줄이 이긴다


# ── 로그 끝 · --status ───────────────────────────────────────────────────────


def test_log_tail_is_the_last_60_lines_masked(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("\n".join(f"line {i} ghp_secretvalue" for i in range(100)) + "\n")
    tail = log_tail(log, lambda b: b.replace(b"ghp_secretvalue", b"****"))
    assert len(tail) == LOG_TAIL_LINES and tail[0] == "line 40 ****" and tail[-1] == "line 99 ****"
    assert log_tail(tmp_path / "missing.log") == []
    assert plan_n(tmp_path / "missing.log") is None


def test_status_runs_the_driver_synchronously_and_reports_timeouts(runner, tmp_path, checkout):
    lines, err = runner.status(checkout / "scripts" / "driver.sh", checkout, {"APP_SECRETS": "/s"})
    assert lines == [
        "driver --status",
        "server=none token= secrets=/s",
        "stage: S1 planned",
        "sha: abc",
    ]
    assert err is None
    slow = write_script(tmp_path / "slow.sh", "#!/bin/sh\nsleep 5\n")
    lines, err = runner.status(slow, checkout, {}, timeout=0.2)
    assert lines is None and "did not finish" in err
    bad = write_script(tmp_path / "bad.sh", "#!/bin/sh\necho partial\nexit 4\n")
    assert runner.status(bad, checkout, {}) == (["partial"], "--status exited 4")
    lines, err = runner.status(tmp_path / "missing.sh", checkout, {})
    assert lines is None and err.startswith("--status could not start")
