"""실제 execve 로 따라간다 (M5q PR A · 시나리오 A5 · A29 · A61 · A63 · A66 · A71 · A74 · A76 · A77 ·
A82 · A117 · A53 · A141).

`make_bootloader` 가 pip 없는 venv 에 지금 빌드의 wheel 을 풀어 비 editable 설치본을 만든다. 하위
프로세스 `rcm` 이 가짜 버전 wheel 을 내는 서버를 보고 같은 인터프리터를 `-P -c BOOT` 로 다시
실행한다. 환경은 처음부터 만든다(`child_env`) — 사용자의 `~/.cache` 와 한 줄 파일을 보지 않는다.
시험을 먼저 썼다 — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from follow_helpers import (
    INSTALLED,
    NEW,
    OLD,
    PROBE_CLI,
    FakeServer,
    SilentListener,
    VersionedServer,
    child_env,
    entries,
    fake_wheel,
    leftovers,
    make_bootloader,
    probe_out,
    raw_get,
    run_rcm,
)
from remote_ci_monitor import follow

pytestmark = pytest.mark.skipif(os.name != "posix", reason="follow is POSIX only (결정 172)")

FALLBACK = "rcm: follow: the cached client did not load — running installed"


@pytest.fixture(scope="session")
def bootloader(tmp_path_factory):
    return make_bootloader(tmp_path_factory.mktemp("boot") / "venv")


@pytest.fixture
def servers():
    made: list = []
    yield made
    for s in made:
        s.close()


def clients_of(tmp: Path) -> Path:
    return Path(os.path.realpath(tmp / "cache")) / "rcm" / "clients"


def root_of(tmp: Path) -> Path:
    return Path(os.path.realpath(tmp / "cache")) / "rcm"


def prime(tmp: Path, srv) -> None:
    """그 서버의 health 기록을 미리 넣는다. 자식은 10분 기록을 쓰고 health 를 부르지 않는다.

    health 의 3초 제한은 부하가 걸린 러너에서 가끔 넘친다. 그러면 자식이 설치본으로 돌아 이
    파일의 시험이 엉뚱한 이유로 빨개진다. 제한 자체는 `test_follow_cache` 가 잠근다."""
    doc = srv.health if isinstance(getattr(srv, "health", None), dict) else None
    if doc is None:
        doc = json.loads(raw_get(srv.url + "/api/health")[1])
    key = follow.normalize_server(srv.url)
    info = follow.parse_health(key, doc, now=time.time())
    follow.save_record(root_of(tmp), info, server=key, failed_at=None)


def probe_wheel(version: str = NEW) -> bytes:
    return fake_wheel(version, cli=PROBE_CLI)


def popen(boot, args, env, cwd=None):
    return subprocess.Popen(
        [str(boot.rcm), *args],
        env=env,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_until(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def boot_argv(boot, *args: str, preamble: str = "") -> list[str]:
    return [str(boot.python), "-P", "-c", preamble + follow.BOOT, *args]


def under(path: str, folder: Path) -> bool:
    return os.path.realpath(path).startswith(os.path.realpath(folder) + os.sep)


# ── 베스트 ───────────────────────────────────────────────────────────────────


def test_real_exec_runs_the_servers_version(bootloader, tmp_path, servers):
    # A5 (F5 · 결정 151): 자식이 NEW 코드로 돌고 check 가 그것을 말한다
    srv = VersionedServer(tmp_path, NEW, fake_wheel(NEW))
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url, RCM_TOKEN=srv.tokens["alice"])
    prime(tmp_path, srv)
    r = run_rcm(bootloader, ["presets"], env)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout
    assert f"rcm: following server v{NEW} (installed v{INSTALLED})" in r.stderr
    r = run_rcm(bootloader, ["check"], env)
    rows = [ln for ln in r.stdout.splitlines() if ln.split()[1:2] == ["client"]]
    assert rows == [
        f"ok   client        v{NEW} · same as server · following (installed v{INSTALLED})"
    ], r.stdout + r.stderr
    assert srv.client_count == 1


def test_real_exec_keeps_the_pid(bootloader, tmp_path, servers):
    # A5 PROBE 판 · A74: exec 는 같은 PID 다
    srv = FakeServer(NEW, probe_wheel())
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url)
    prime(tmp_path, srv)
    proc = popen(bootloader, ["probe"], env)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    doc = probe_out(out)
    assert doc["pid"] == proc.pid
    assert doc["version"] == NEW
    assert under(doc["file"], clients_of(tmp_path))


def test_boot_imports_the_cache_not_the_cwd(bootloader, tmp_path, servers):
    # A29 · A82 (결정 161 · R13): `-P` 라 cwd 의 가짜 패키지를 안 본다. 따라가기 변수는 꺼낸다
    srv = FakeServer(NEW, probe_wheel())
    servers.append(srv)
    cwd = tmp_path / "work"
    (cwd / "remote_ci_monitor").mkdir(parents=True)
    (cwd / "remote_ci_monitor" / "__init__.py").write_text("raise SystemExit(99)\n")
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url)
    prime(tmp_path, srv)
    r = run_rcm(bootloader, ["probe", "--spawn"], env, cwd=cwd)
    assert r.returncode == 0, r.stderr
    doc = probe_out(r.stdout)
    folder = Path(doc["path0"])
    assert under(doc["file"], folder) and folder.parent == clients_of(tmp_path)
    boot = doc["boot"]
    assert boot["loaded"] is True
    assert boot["RCM_FOLLOWED"] == NEW and boot["RCM_FOLLOWED_FROM"] == INSTALLED
    assert Path(boot["RCM_FOLLOWED_PATH"]) == folder
    assert boot["RCM_FOLLOW_FROM"] == "wheel"
    assert not [k for k in doc["env"] if k.startswith("RCM_FOLLOW")]
    assert doc["spawned"] == []  # 손자(env)에게도 새지 않는다


# ── 엣지 ─────────────────────────────────────────────────────────────────────


def test_concurrent_fetch_makes_one_folder(bootloader, tmp_path, servers):
    # A61 (F30 · §2.5): 동시에 받아도 대상 폴더 하나, 찌꺼기 없음. 열 프로세스가 장벽에서 한꺼번에
    # 받는다. 둘의 경주는 이 경우에 들어 있다. 한 프로세스 안의 경주는 test_follow_cache 가 본다
    n = 10
    srv = FakeServer(NEW, probe_wheel(), client_barrier=threading.Barrier(n))
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url)
    prime(tmp_path, srv)
    procs = [popen(bootloader, ["probe"], env) for _ in range(n)]
    outs = [p.communicate(timeout=90) for p in procs]
    assert [p.returncode for p in procs] == [0] * n, [e for _, e in outs]
    assert srv.client_count == n
    clients = clients_of(tmp_path)
    assert len(entries(clients)) == 1 and leftovers(clients) == []
    folder = entries(clients)[0]
    assert all(under(probe_out(o)["file"], folder) for o, _ in outs)


def test_boot_falls_back_when_the_folder_is_gone(bootloader, tmp_path, servers):
    # A63 (F32): BOOT 가 가리키는 폴더가 없으면 설치본으로, 다시 따라가지 않는다
    srv = VersionedServer(tmp_path, NEW, fake_wheel(NEW))
    servers.append(srv)
    env = child_env(
        tmp_path,
        bootloader,
        RCM_SERVER=srv.url,
        RCM_FOLLOWED=NEW,
        RCM_FOLLOWED_PATH=str(tmp_path / "missing"),
        RCM_FOLLOWED_FROM=INSTALLED,
    )
    r = subprocess.run(
        boot_argv(bootloader, "presets"), env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout
    assert [ln for ln in r.stderr.splitlines() if ln.strip()] == [FALLBACK]
    assert srv.health_count == 0 and srv.client_count == 0


def test_prune_keeps_a_locked_entry(bootloader, tmp_path, servers):
    # A66 (F35 · §2.8 · §14.6 A질문16): 최근 셋(막 받은 것 포함) + 잠긴 것만 남는다
    root = root_of(tmp_path)
    first = FakeServer("90.0.1", probe_wheel("90.0.1"))
    servers.append(first)
    info = follow.parse_health(first.key, first.health, now=time.time())
    locked = follow.fetch(first.key, None, info, root=root).path
    base = time.time() - 10_000
    os.utime(locked / ".rcm-client.json", (base, base))
    copies = []
    for i, v in enumerate(("90.0.2", "90.0.3", "90.0.4", "90.0.5"), start=1):
        dest = locked.parent / f"{i:016x}"
        shutil.copytree(locked, dest)
        marker = {"version": v, "sha256": f"{i:x}" * 64, "server": first.key, "fetched_at": base}
        marker["sha256"] = marker["sha256"][:64]
        (dest / ".rcm-client.json").write_text(json.dumps(marker))
        os.utime(dest / ".rcm-client.json", (base + i * 100, base + i * 100))
        copies.append(dest)
    env = child_env(
        tmp_path,
        bootloader,
        RCM_FOLLOWED="90.0.1",
        RCM_FOLLOWED_PATH=str(locked),
        RCM_FOLLOWED_FROM=INSTALLED,
    )
    sleeper = subprocess.Popen(
        boot_argv(bootloader, "probe", "--hold"),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = sleeper.stdout.readline()  # PROBE 는 자기 전에 한 줄을 쓴다 — 그때는 잠금을 쥐었다
    assert probe_out(line)["boot"]["loaded"] is True
    second = FakeServer("91.0.0", probe_wheel("91.0.0"))
    servers.append(second)
    info = follow.parse_health(second.key, second.health, now=time.time())
    new = follow.fetch(second.key, None, info, root=root).path
    left = set(entries(locked.parent))
    assert left == {new, copies[3], copies[2], locked}
    assert leftovers(locked.parent) == []
    sleeper.communicate(timeout=30)  # stdin 이 닫히면 PROBE 가 잠금을 놓고 끝난다
    assert sleeper.returncode == 0


def test_following_down_to_a_pre_m5q_build(bootloader, tmp_path, servers):
    # A71 (F40 · 결정 155): 옛 판은 한 줄 파일을 모르고 client.toml 을 엄격하게 읽는다
    old_cli = (
        "import json, sys\n"
        "import remote_ci_monitor\n"
        "from remote_ci_monitor.config import load_client_config\n"
        "def main(argv=None):\n"
        "    cfg = load_client_config(None)\n"
        "    print(json.dumps({'version': remote_ci_monitor.__version__, 'server': cfg.server}))\n"
        "    return 0\n"
    )
    srv = FakeServer(OLD, fake_wheel(OLD, drop=("follow.py",), cli=old_cli))
    servers.append(srv)
    env = child_env(tmp_path, bootloader)
    conf = tmp_path / "config" / "rcm"
    conf.mkdir(parents=True)
    (conf / "client-version").write_text("follow\n")
    (conf / "client.toml").write_text(f'server = "{srv.url}"\n')
    prime(tmp_path, srv)
    r = run_rcm(bootloader, ["jobs"], env)
    assert r.returncode == 0, r.stderr
    doc = probe_out(r.stdout)
    assert doc == {"version": OLD, "server": srv.url}


def test_exit_codes_pass_through(bootloader, tmp_path, servers):
    # A74 (F43): 자식 잡이 실패하면 `rcm wait` 가 1
    srv = VersionedServer(tmp_path, NEW, fake_wheel(NEW), workers=True)
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url, RCM_TOKEN=srv.tokens["alice"])
    prime(tmp_path, srv)
    work = tmp_path / "tree"
    work.mkdir()
    (work / "hello.txt").write_text("hi\n")
    r = run_rcm(bootloader, ["run", "bad", "--no-wait"], env, cwd=work)
    assert r.returncode == 0, r.stderr
    job = json.loads(r.stdout.splitlines()[-1])["job_id"]
    r = run_rcm(bootloader, ["wait", "--job", str(job), "--timeout", "40"], env, cwd=work)
    assert r.returncode == 1, r.stdout + r.stderr


def test_sigint_reaches_the_child(bootloader, tmp_path, servers):
    # A74 (F43): Ctrl-C 는 130, PID 가 같다
    srv = FakeServer(NEW, probe_wheel())
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url)
    prime(tmp_path, srv)
    proc = popen(bootloader, ["probe", "--sleep", "30"], env)
    doc = probe_out(proc.stdout.readline())
    assert doc["pid"] == proc.pid
    proc.send_signal(signal.SIGINT)
    proc.communicate(timeout=30)
    assert proc.returncode == 130


def test_lock_failure_still_runs_the_cache(bootloader, tmp_path, servers):
    # A76 (R ⑤): flock 이 ENOLCK 이거나 `.lock` 을 못 만들어도 캐시 코드로 돈다
    srv = FakeServer(NEW, probe_wheel())
    servers.append(srv)
    info = follow.parse_health(srv.key, srv.health, now=time.time())
    entry = follow.fetch(srv.key, None, info, root=root_of(tmp_path))
    env = child_env(
        tmp_path,
        bootloader,
        RCM_FOLLOWED=NEW,
        RCM_FOLLOWED_PATH=str(entry.path),
        RCM_FOLLOWED_FROM=INSTALLED,
    )
    preamble = (
        "import errno, fcntl\n"
        "def _no_lock(*a, **k):\n"
        "    raise OSError(errno.ENOLCK, 'no locks')\n"
        "fcntl.flock = _no_lock\n"
    )
    r = subprocess.run(
        boot_argv(bootloader, "probe", preamble=preamble),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert probe_out(r.stdout)["boot"]["loaded"] is True
    lock = entry.path / ".lock"
    if lock.exists():
        lock.unlink()
    entry.path.chmod(0o500)
    try:
        r = subprocess.run(
            boot_argv(bootloader, "probe"), env=env, capture_output=True, text=True, timeout=60
        )
    finally:
        entry.path.chmod(0o700)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert probe_out(r.stdout)["boot"]["loaded"] is True


@pytest.mark.parametrize("damage", ["deleted", "syntax"])
def test_broken_cache_falls_back_and_is_marked(bootloader, tmp_path, servers, damage):
    # A77 (R ⑥ · §14.6 B질문14): 트레이스백 없이 설치본, `.bad` 를 남기고 다음엔 고르지 않는다
    srv = VersionedServer(tmp_path, NEW, fake_wheel(NEW))
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url)
    prime(tmp_path, srv)
    assert run_rcm(bootloader, ["presets"], env).returncode == 0
    (folder,) = entries(clients_of(tmp_path))
    cli_py = folder / "remote_ci_monitor" / "cli.py"
    if damage == "deleted":
        cli_py.unlink()
    else:
        cli_py.write_text("def main(:\n")
    r = run_rcm(bootloader, ["presets"], env)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout and "Traceback" not in r.stderr
    assert FALLBACK in r.stderr.splitlines()
    assert (folder / ".bad").exists()
    r = run_rcm(bootloader, ["presets"], env)
    assert r.returncode == 0 and FALLBACK not in r.stderr
    reason = f"the cached client v{NEW} did not load before"
    line = f"rcm: not following server v{NEW}: {reason} — running installed v{INSTALLED}"
    assert line in r.stderr.splitlines()
    assert srv.client_count == 1  # 같은 sha 를 다시 받지 않는다


def test_check_in_a_fallback_says_so(bootloader, tmp_path, servers):
    # A141 (§14.7 A질문14): BOOT 가 설치본으로 돌아간 자식의 `client` 행 꼬리
    srv = VersionedServer(tmp_path, NEW, fake_wheel(NEW))
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url)
    prime(tmp_path, srv)
    assert run_rcm(bootloader, ["presets"], env).returncode == 0
    (folder,) = entries(clients_of(tmp_path))
    (folder / "remote_ci_monitor" / "cli.py").unlink()
    r = run_rcm(bootloader, ["check"], env)
    row = [ln for ln in r.stdout.splitlines() if ln.split()[1:2] == ["client"]]
    assert row and "not following: the cached client did not load" in row[0], r.stdout


def test_sigint_while_waiting_for_health(bootloader, tmp_path):
    # A117: 답하지 않는 서버 앞에서 Ctrl-C 는 130, 기록에 아무것도 남지 않는다
    silent = SilentListener()
    conn = None
    try:
        env = child_env(tmp_path, bootloader, RCM_SERVER=silent.url)
        proc = popen(bootloader, ["jobs"], env)
        # 1초를 재지 않고 자식의 health 연결이 들어온 것을 본 뒤 누른다. 그때 자식은 답을 기다린다
        silent.sock.settimeout(15)
        conn, _ = silent.sock.accept()
        time.sleep(0.1)
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=30)
    finally:
        if conn is not None:
            conn.close()
        silent.close()
    assert proc.returncode == 130, err
    record = root_of(tmp_path) / "follow.json"
    assert not record.exists() or silent.url not in json.loads(record.read_text())


def test_sigint_while_fetching_leaves_nothing(bootloader, tmp_path, servers):
    # A53 (R ④ · 결정 157): 받는 중 Ctrl-C 는 130, `.tmp-*` 와 대상 폴더 없음
    hold = threading.Event()
    srv = FakeServer(NEW, probe_wheel(), client_hold=hold)
    servers.append(srv)
    env = child_env(tmp_path, bootloader, RCM_SERVER=srv.url)
    prime(tmp_path, srv)
    proc = popen(bootloader, ["probe"], env)
    try:
        assert wait_until(lambda: srv.client_count >= 1)
        time.sleep(0.3)
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=30)
    finally:
        hold.set()
    assert proc.returncode == 130, err
    clients = clients_of(tmp_path)
    assert entries(clients) == [] and leftovers(clients) == []
