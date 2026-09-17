"""워커 — sh 프리셋으로 성공·실패·타임아웃·취소·마커·env·tar 탈출 거부·프리셋 소멸·워커 다운."""

import io
import json
import tarfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor.config import ServerConfig, parse_preset
from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    QUEUED,
    SUCCEEDED,
    TIMED_OUT,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.core.status import recent_json
from remote_ci_monitor.materialize import MaterializeError, extract_tree
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import Worker, format_limit, tail_lines

ALICE = Requester(name="alice-laptop", label="alice@laptop")
#: 어떤 PATH 에도 없는 이름. `tests/test_requires.py` 와 같은 값이지만 그 모듈은 이 모듈을
#: import 하므로(순환) 여기서 따로 둔다.
MISSING = "definitely-missing-tool-rcm"


def sh(name: str, script: str, **extra) -> dict:
    return {"name": name, "argv": ["sh", "-c", script], "timeout_seconds": 60, **extra}


PRESETS = [
    sh(
        "ok",
        "echo '::rcm::steps::2'; echo '::rcm::step::a'; echo hi; echo '::rcm::step::b'; "
        "echo '::rcm::summary::all green'; exit 0",
    ),
    sh("bad", "echo '::rcm::step::test'; echo boom; echo '::rcm::summary::2 failed'; exit 3"),
    sh("slow", "echo start; sleep 30; echo never", timeout_seconds=1),
    sh("cancelme", "echo start; sleep 30; echo never"),
    sh("nomarker", "printf 'no newline at end'; exit 0"),
    sh(
        "env",
        "echo scope=$RCM_INPUT_SCOPE job=$RCM_JOB_ID preset=$RCM_PRESET mode=$RCM_SOURCE_MODE "
        "ci=$CI ws=$RCM_WORKSPACE; test -f hello.txt && cat hello.txt",
        env={"CI": "1"},
        inputs=[
            {"name": "scope", "type": "choice", "choices": ["full", "fast"], "default": "full"}
        ],
    ),
    {"name": "missing-bin", "argv": ["/nonexistent/binary-xyz"], "timeout_seconds": 5},
]


def make_config(tmp_path: Path, **server) -> ServerConfig:
    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    cfg.server.grace_seconds = 1
    for k, v in server.items():
        setattr(cfg.server, k, v)
    cfg.presets = tuple(parse_preset(p) for p in PRESETS)
    return cfg


def make_tar(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def enqueue(store: Store, cfg: ServerConfig, preset: str, inputs=None, tar_files=None) -> int:
    inputs = inputs or {}
    now = datetime.now(UTC)
    job = store.create_job(
        preset=preset,
        inputs=inputs,
        key=preset,
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", base_sha="abc", dirty=False, tree_hash="t"),
        requester=ALICE,
        timeout_seconds=cfg.preset(preset).timeout_seconds if cfg.preset(preset) else 5,
        join_key=join_key(preset, inputs, "t"),
        now=now,
        state=QUEUED,
    )
    make_tar(
        cfg.data_dir / "jobs" / str(job.id) / "tree.tar.gz", tar_files or {"hello.txt": b"hello\n"}
    )
    return job.id


def run_one(
    store: Store, cfg: ServerConfig, job_id: int, *, timeout=20.0, before_wait=None
) -> Worker:
    """워커 하나를 띄워 잡 하나가 끝날 때까지 기다린다."""
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop)
    w.start()
    deadline = time.monotonic() + timeout
    if before_wait:
        before_wait()
    while time.monotonic() < deadline:
        j = store.get_job(job_id)
        if j.is_terminal:
            break
        time.sleep(0.05)
    stop.set()
    w.wake.set()
    w.join(timeout=5)
    return w


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def test_success_records_markers_summary_and_deletes_workspace(env):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == SUCCEEDED and j.exit_code == 0 and j.summary == "all green"
    assert j.failed_step is None and j.lane is None and j.phase is None
    kinds = [(m.kind, m.value) for m in store.markers(jid)]
    assert kinds == [("steps", "2"), ("step", "a"), ("step", "b"), ("summary", "all green")]
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert "::rcm::step::a\nhi\n" in log  # 마커 줄도 로그에 그대로 남는다
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()
    assert [t.state for t in j.transitions] == ["queued", "running", "succeeded"]


def test_failure_keeps_workspace_and_names_no_step_without_a_declaration(env):
    """M5h 결정 63 — `bad` 는 실패를 선언하지 않는다(`::rcm::step-end::fail` 도 `::rcm::fail::`
    도 없다). 그래서 `failed_step` 은 비고 「어디였나」만 `last_step` 이 말한다. 선언하는 잡은
    `tests/test_progress_m5h.py` · `tests/test_store_m5h.py` 가 잠근다."""
    store, cfg = env
    jid = enqueue(store, cfg, "bad")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code == 3 and j.summary == "2 failed"
    assert j.failed_step is None and j.last_step == "test"
    assert (cfg.data_dir / "workspaces" / str(jid) / "hello.txt").exists()


def test_timeout_kills_process_group_and_marks_timed_out(env):
    store, cfg = env
    jid = enqueue(store, cfg, "slow")
    t0 = time.monotonic()
    run_one(store, cfg, jid)
    took = time.monotonic() - t0
    j = store.get_job(jid)
    assert j.state == TIMED_OUT and j.summary == "limit 1s" and j.timeout_seconds == 1
    assert took < 15  # sleep 30 을 기다리지 않았다
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert "start" in log and "never" not in log


def test_cancel_goes_through_cancelling_and_ends_cancelled(env):
    store, cfg = env
    jid = enqueue(store, cfg, "cancelme")

    def cancel_soon():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            j = store.get_job(jid)
            if j.state == "running" and j.phase == "executing":
                break
            time.sleep(0.05)
        assert store.request_cancel(jid, "alice-laptop", datetime.now(UTC), 1) == "cancelling"

    t0 = time.monotonic()
    run_one(store, cfg, jid, before_wait=cancel_soon)
    j = store.get_job(jid)
    assert j.state == CANCELLED and j.cancelled_by == "alice-laptop"
    assert j.summary == "cancelled by alice-laptop"
    assert time.monotonic() - t0 < 15
    assert [t.state for t in j.transitions] == ["queued", "running", "cancelling", "cancelled"]


def test_output_without_trailing_newline_is_logged(env):
    store, cfg = env
    jid = enqueue(store, cfg, "nomarker")
    run_one(store, cfg, jid)
    assert store.get_job(jid).state == SUCCEEDED
    assert (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text() == "no newline at end\n"
    assert store.markers(jid) == []


def test_env_passes_inputs_and_rcm_vars_and_runs_in_workspace(env):
    store, cfg = env
    jid = enqueue(store, cfg, "env", inputs={"scope": "fast"})
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert j.state == SUCCEEDED
    assert f"scope=fast job={jid} preset=env mode=tree ci=1 ws=" in log
    assert "hello\n" in log  # cwd 가 워크스페이스라 hello.txt 가 보인다


def test_missing_binary_fails_with_a_launch_code_and_keeps_argv_out_of_the_summary(env):
    """T3: 시작 실패는 자재화 실패와 **다른 코드**다 — 고칠 곳이 프리셋의 `argv` 지 스냅샷이
    아니다. 그리고 `argv[0]` 은 공개 요약에 **아예 안 실린다**(`/api/status` 는 기본 설정에서
    토큰 없이 읽힌다): 씻는 것이 아니라 싣지 않는 것이다. 원문은 잡 로그에만 남는다."""
    store, cfg = env
    jid = enqueue(store, cfg, "missing-bin")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary_code == "launch_executable_missing"
    assert j.summary == "the preset's command was not found"
    assert j.summary_args == {}
    assert "nonexistent" not in j.summary + json.dumps(j.summary_args)
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert "/nonexistent/binary-xyz" in log  # 단서는 토큰 뒤에 돌려준다


def test_preset_removed_from_config_fails_the_job(env):
    """T2: 문장은 코드가 생기기 **전과 글자까지 같다** — 보이는 글자를 바꾸지 않고 기계가 읽을
    사실만 더했다. 프리셋 하나를 지우고 재기동하면 대기 잡이 한꺼번에 이렇게 죽는데, 그때
    「게이트가 깨진 게 아니라 방금 한 편집 때문」임을 코드로 묶어 셀 수 있어야 한다."""
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    cfg.presets = tuple(p for p in cfg.presets if p.name != "ok")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary == "preset 'ok' is no longer configured"
    assert j.summary_code == "preset_missing" and j.summary_args == {"preset": "ok"}
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()


def test_tar_escape_is_rejected_and_job_fails(env, tmp_path):
    """T1: 자재화 실패가 코드를 단다. 까닭은 닫힌 열쇠고, 멤버는 **이름 하나**만 남는다 — 어느
    파일이 문제였는지는 스냅샷을 고칠 사람에게 유일한 단서라 지우지 않고, 경로는 싣지 않는다."""
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    tar_path = cfg.data_dir / "jobs" / str(jid) / "tree.tar.gz"
    make_tar(tar_path, {"../escape.txt": b"x"})
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary_code == "snapshot_rejected"
    assert j.summary_args == {"kind": "escapes_workspace", "member": "escape.txt"}
    assert j.summary == "snapshot rejected: member escapes the workspace: escape.txt"
    # 서버가 만든 코드다 — 스크립트가 선언한 실패가 아니니 라벨도 대장 행도 없다(M5h 불변식)
    assert j.failed_step is None and j.last_step is None
    assert store.markers(jid) == []
    assert not (tmp_path / "data" / "escape.txt").exists()
    with pytest.raises(MaterializeError):
        extract_tree(tar_path, tmp_path / "ws2")


def test_absolute_symlink_and_garbage_archive_are_rejected(tmp_path):
    p = tmp_path / "abs.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(MaterializeError) as e:
        extract_tree(p, tmp_path / "ws")
    assert "snapshot rejected" in str(e.value) and "/etc" not in str(e.value)
    garbage = tmp_path / "garbage.tar.gz"
    garbage.write_bytes(b"not a tarball")
    with pytest.raises(MaterializeError) as e:
        extract_tree(garbage, tmp_path / "ws3")
    assert str(e.value) == "snapshot rejected: not a valid tar.gz"


# ── 시작 전에 끝난 잡의 네 코드 (F2b) ───────────────────────────────────────


def ledger_rows(store: Store, job_id: int) -> list[str]:
    """M5h 의 실패 대장. 서버가 만든 코드는 스크립트의 선언이 아니므로 여기에 행이 없어야 한다."""
    return [
        str(r[0])
        for r in store._conn()
        .execute("SELECT name FROM job_failures WHERE job_id=? ORDER BY seq", (job_id,))
        .fetchall()
    ]


def _raise(e: BaseException):
    raise e


def test_each_failure_before_the_process_starts_carries_its_own_code(env):
    """T4: 이 결함의 본체다. 하나만 코드를 달면 나머지는 「테스트가 깨졌다」와 화면에서 같은
    모양이 되고, 전부 한 코드로 묶으면 사람이 고칠 곳을 못 찾는다 — 스냅샷을 고칠 일과 프리셋
    argv 를 고칠 일과 설정 편집을 되돌릴 일은 서로 다른 사람의 서로 다른 조치다."""
    store, cfg = env
    cfg.presets = (
        *cfg.presets,
        parse_preset(sh("needs-missing", "echo STARTED", requires=["sh", MISSING])),
    )
    codes, states = [], []

    # 하나씩 넣고 하나씩 돌린다 — 레인은 큐에 있는 아무 잡이나 집으므로 한꺼번에 넣으면
    # 프리셋을 지우기 **전에** ㉡ 이 실행돼 버린다.
    # ㉠ 스냅샷이 없다
    snapshotless = enqueue(store, cfg, "ok")
    (cfg.data_dir / "jobs" / str(snapshotless) / "tree.tar.gz").unlink()
    run_one(store, cfg, snapshotless)
    # ㉡ 큐에 있는 사이 프리셋이 설정에서 사라졌다
    doomed = enqueue(store, cfg, "bad")
    cfg.presets = tuple(p for p in cfg.presets if p.name != "bad")
    run_one(store, cfg, doomed)
    # ㉢ argv[0] 을 못 띄운다 · ㉣ `requires` 도구가 없다
    unstartable = enqueue(store, cfg, "missing-bin")
    run_one(store, cfg, unstartable)
    toolless = enqueue(store, cfg, "needs-missing")
    run_one(store, cfg, toolless)

    four = [snapshotless, doomed, unstartable, toolless]
    for jid in four:
        j = store.get_job(jid)
        codes.append(j.summary_code)
        states.append(j.state)
        assert j.exit_code is None, (jid, j.summary)
        # 서버가 만든 코드다 — 라벨도 대장 행도 남기지 않는다(M5h 불변식)
        assert j.failed_step is None and j.last_step is None, (jid, j.summary)
        assert ledger_rows(store, jid) == [], jid
    assert states == [FAILED] * 4
    assert codes == [
        "snapshot_missing",
        "preset_missing",
        "launch_executable_missing",
        "tool_missing",
    ]
    assert len(set(codes)) == 4 and None not in codes
    # 스크립트가 이 넷을 문장 없이 가려낼 수 있어야 한다 — 그게 코드를 다는 이유다
    assert set(codes) <= set(outcome.PREFLIGHT_CODES)


def test_the_raw_text_of_a_materialize_failure_goes_to_the_log_not_to_the_public_document(
    env, monkeypatch
):
    """T7: `/api/status` 는 기본 설정에서 토큰 없이 읽힌다(PLAN 「보안」). 자재화 문구의 절대
    경로는 빌드 머신의 배치를 알려 주므로 공개 요약에 **싣지 않는다** — 씻는 것이 아니라 코드와
    경계가 정해진 인자만 싣는 것이다. 그러면서도 원문은 버리지 않는다: 토큰이 있어야 읽는 잡
    로그에 그대로 남아야 사람이 고칠 수 있다."""
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    secret = "/Users/build/private-sdk/toolchain/x"
    monkeypatch.setattr(
        Worker,
        "_materialize",
        lambda self, *a: _raise(
            MaterializeError("workspace_failed", error="OSError", log=f"blew up at {secret}")
        ),
    )
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.summary_code == "workspace_failed"
    assert j.summary_args == {"error": "OSError"}
    assert j.summary == "the workspace could not be prepared (OSError)"
    doc = json.dumps(recent_json(j), ensure_ascii=False)
    assert "/Users/build" not in doc and secret not in doc
    assert secret in (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()


def test_every_raise_site_uses_a_code_the_table_knows(env):
    """T8: 오타 하나의 값이 크다. 모르는 코드로 잡을 닫으려 하면 `summary()` 가 던지고, 그
    예외는 잡 하나가 아니라 **레인 전체**를 `down` 으로 만든다(아래 시험이 그 경로다). 그래서
    문구 대신 코드를 들기로 한 김에, 코드가 표에 있는지를 **정적으로** 잠근다 — 자재화·시작
    실패는 디스크가 차거나 권한이 틀어졌을 때만 나는 자리가 많아 시험이 다 밟지 못한다."""
    import ast

    root = Path(__file__).parents[1] / "src/remote_ci_monitor"
    seen = 0
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in ("MaterializeError", "RunnerError"):
                continue
            seen += 1
            where = f"{path.name}:{node.lineno}"
            assert node.args, f"{where}: 코드 없이 예외를 든다"
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), where
            assert first.value in outcome.PREFLIGHT_CODES, (
                f"{where}: {first.value!r} 는 모르는 코드"
            )
            for kw in node.keywords:
                assert (
                    kw.arg is None
                    or kw.arg
                    in ("log", "key")  # 둘 다 비공개 — 잡 로그·워커 전용, 공개 요약엔 안 실린다
                    or kw.arg in outcome.PREFLIGHT_ARGS[first.value]
                ), f"{where}: {kw.arg!r} 는 {first.value} 의 인자가 아니다"
    assert seen >= 8, f"raise 자리를 {seen}개만 봤다 — 찾는 방법이 깨졌다"


def test_a_member_name_that_is_not_utf8_fails_the_job_without_taking_the_lane_down(env):
    """E10: `tarfile` 은 멤버 이름을 `surrogateescape` 로 읽는다. 외톨이 서로게이트가 그대로
    `store.finish` 로 가면 SQLite 인코딩이 던지고, 그러면 잡 하나가 아니라 **레인 전체**가
    `down` 이 된다. 인자를 만드는 자리에서 지우는 이유가 이것이다."""
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    make_tar(cfg.data_dir / "jobs" / str(jid) / "tree.tar.gz", {"../\udcff-escape.txt": b"x"})
    w = run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.summary_code == "snapshot_rejected"
    assert "\udcff" not in j.summary and "\udcff" not in json.dumps(j.summary_args)
    assert j.summary_args["member"] == "-escape.txt"
    assert j.summary.encode("utf-8")  # 다시 인코딩해도 던지지 않는다
    assert w.info().state == "idle" and w.info().error is None


def test_worker_goes_down_on_store_error_and_closes_the_job(env, monkeypatch):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop)
    real_execute = w.execute

    def boom(job):
        raise RuntimeError("disk exploded at /var/data/x")

    w.execute = boom
    w.start()
    w.join(timeout=10)
    info = w.info()
    assert info.state == "down" and info.error == "RuntimeError: disk exploded at <path>"
    j = store.get_job(jid)
    assert j.state == FAILED and j.summary == "worker error: RuntimeError: disk exploded at <path>"
    assert real_execute is not None


def test_paused_worker_does_not_claim(env):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    store.set_paused("admin", datetime.now(UTC))
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop)
    w.start()
    time.sleep(1.2)
    assert store.get_job(jid).state == QUEUED and w.info().state == "idle"
    store.clear_paused()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not store.get_job(jid).is_terminal:
        time.sleep(0.05)
    stop.set()
    w.join(timeout=5)
    assert store.get_job(jid).state == SUCCEEDED


def test_tail_lines_and_format_limit(tmp_path):
    p = tmp_path / "log.txt"
    assert tail_lines(p) is None
    p.write_text("\n".join(f"line {i}" for i in range(100)) + "\n")
    assert tail_lines(p, 3) == ["line 97", "line 98", "line 99"]
    assert tail_lines(p, 0) == []
    assert len(tail_lines(p, 1000, max_bytes=100)) < 20
    assert format_limit(1200) == "limit 20m" and format_limit(3600) == "limit 1h"
    assert format_limit(90) == "limit 90s" and format_limit(None) == "limit"
