"""`runner.run_job` 순수 규칙(M5b-3 §1) — 자재화 → phase executing → 줄 단위 output 배치 → 종료.

관찰자는 기록만 하는 `RecordingObserver`(`RunObserver` 프로토콜을 구조적으로 만족), 자재화는
워크스페이스에 파일을 쓰는 `FileMaterializer`. 실제 `sh` 를 돌린다(bash-ism 없음 — sh · sleep ·
printf · cat 만). 띄운 프로세스는 세션 그룹째 죽인다(`reap` 픽스처). 시각은 실제 벽시계다 —
펌프가 큐 타임아웃(실시간)과 `now_fn` 을 섞어 쓰는 오늘의 구조를 그대로 두기 때문이다.

명세는 docs/m5b3-workplan.md §1. 구현 전이라 빨간 것이 정상이다(`remote_ci_monitor.runner` 없음).

잠근 선택(명세가 안 정한 것 — 구현이 달리 정하면 여기부터 고친다):
- `materialize(spec)` 가 `MaterializeError` 를 내면 `run_job` 이 **그대로 올린다**(결과 객체 없음).
  호출자가 failed 로 보고한다. 관찰자는 `executing` 을 보지 못한다.
- argv[0] 을 못 띄우면 `RunnerError` 이고 문구는 `cannot start 'nope'` 로 시작한다(오늘의 워커
  문구). 종료 코드가 0 이 아닌 것은 예외가 아니다 — `rc` 로 돌려준다.
- 러너가 `spec.log_path` 에 **직접**(append) 쓰고, **같은 바이트**를 `observer.output` 으로도 준다.
  마커 줄도 raw 로 넘어간다 — 러너는 마커를 파싱하지 않는다.
- `should_stop` 이 `should_cancel` 보다 우선한다(오늘의 `_pump`: shutting_down 검사가 먼저).
"""

from __future__ import annotations

import dataclasses
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.inputs import env_for_inputs
from remote_ci_monitor.core.model import PHASE_EXECUTING, PHASE_MATERIALIZING, Source
from remote_ci_monitor.materialize import MaterializeError
from remote_ci_monitor.runner import (
    MAX_LINE_BYTES,
    RunnerError,
    RunObserver,
    RunResult,
    RunSpec,
    run_job,
)

TREE = Source(mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash="t" * 8)
GIT_REF = Source(mode="git_ref", repo="app", ref="refs/heads/main", sha="f" * 40)
#: 자식이 `sleep` 을 찾을 수 있게 PATH 만 통과시킨다. 나머지는 테스트가 고른 값이다.
BASE_ENVIRON = {
    "PATH": os.environ.get("PATH") or os.defpath,
    "HOME": "/nonexistent-home",
    "LANG": "C",
    "SECRET": "from-environ",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Clock:
    """`now_fn` — 돌려준 값을 기억해 결과의 시각이 이 시계에서 나왔는지 본다."""

    def __init__(self) -> None:
        self.values: list[datetime] = []

    def __call__(self) -> datetime:
        now = utcnow()
        self.values.append(now)
        return now


class RecordingObserver:
    """`RunObserver` 를 구조적으로 만족하는 기록기. `events` 에 phase · output · materialize 순서가
    남는다. `cancel_if`/`stop_if` 는 폴링 때 평가되는 조건이고, 처음 True 가 된 시각을 기억한다.
    `deadline` 초가 지나면 `should_stop` 이 True — 러너가 멈추지 않는 버그가 있어도 자식을
    거둔다."""

    def __init__(self, *, deadline: float = 15.0) -> None:
        self.events: list[tuple[str, Any]] = []
        self.cancel = False
        self.stop = False
        self.cancel_if: Callable[[], bool] | None = None
        self.stop_if: Callable[[], bool] | None = None
        self.cancel_seen_at: float | None = None
        self.stop_seen_at: float | None = None
        self.stamps: list[float] = []
        self._born = time.monotonic()
        self._deadline = deadline

    # ── RunObserver ──
    def phase(self, phase: str) -> None:
        self.events.append(("phase", phase))

    def output(self, data: bytes) -> None:
        self.events.append(("output", data))
        self.stamps.append(time.monotonic())

    def should_cancel(self) -> bool:
        hit = self.cancel or (self.cancel_if is not None and self.cancel_if())
        if hit and self.cancel_seen_at is None:
            self.cancel_seen_at = time.monotonic()
        return hit

    def should_stop(self) -> bool:
        hit = self.stop or (self.stop_if is not None and self.stop_if())
        if not hit and time.monotonic() - self._born > self._deadline:
            hit = True  # 안전장치 — 정상 테스트에서는 절대 오지 않는다
        if hit and self.stop_seen_at is None:
            self.stop_seen_at = time.monotonic()
        return hit

    # ── 조회 ──
    @property
    def phases(self) -> list[str]:
        return [v for k, v in self.events if k == "phase"]

    @property
    def chunks(self) -> list[bytes]:
        return [v for k, v in self.events if k == "output"]

    @property
    def out(self) -> bytes:
        return b"".join(self.chunks)


class FileMaterializer:
    """`materialize(spec)` — 워크스페이스를 만들고 파일을 쓴다. 호출 순서는 관찰자 `events` 에
    남긴다."""

    def __init__(self, files: dict[str, bytes] | None = None, *, observer: RecordingObserver):
        self.files = files if files is not None else {"hello.txt": b"hello\n"}
        self.observer = observer
        self.specs: list[RunSpec] = []

    def __call__(self, spec: RunSpec) -> None:
        self.specs.append(spec)
        self.observer.events.append(("materialize", spec.job_id))
        spec.workspace.mkdir(parents=True, exist_ok=True)
        for name, data in self.files.items():
            (spec.workspace / name).write_bytes(data)


def make_spec(
    tmp_path: Path,
    argv: tuple[str, ...],
    *,
    job_id: int = 7,
    preset_name: str = "gate",
    env: dict[str, str] | None = None,
    env_passthrough: tuple[str, ...] = ("PATH",),
    timeout_seconds: int | None = None,
    inputs: dict[str, Any] | None = None,
    requester_label: str = "alice@laptop",
    source: Source = TREE,
    grace_seconds: int = 5,
) -> RunSpec:
    """오늘의 워커와 같은 배치 — `<data>/workspaces/<id>` · `<data>/jobs/<id>/log.txt`
    (부모 디렉터리는 있다)."""
    log_path = tmp_path / "jobs" / str(job_id) / "log.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return RunSpec(
        job_id=job_id,
        preset_name=preset_name,
        argv=argv,
        env=dict(env or {}),
        env_passthrough=env_passthrough,
        timeout_seconds=timeout_seconds,
        inputs=dict(inputs or {}),
        requester_label=requester_label,
        source=source,
        workspace=tmp_path / "workspaces" / str(job_id),
        log_path=log_path,
        grace_seconds=grace_seconds,
    )


def sh(script: str) -> tuple[str, ...]:
    return ("sh", "-c", script)


def run(
    spec: RunSpec,
    observer: RunObserver,
    *,
    materialize: Callable[[RunSpec], None],
    now_fn: Callable[[], datetime] = utcnow,
    environ: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> RunResult:
    """`run_job` 을 스레드에서 돌려 마감을 건다 — 러너가 안 돌아오는 버그가 테스트를 영원히 막지
    않게."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = run_job(
                spec,
                observer,
                now_fn=now_fn,
                environ=BASE_ENVIRON if environ is None else environ,
                materialize=materialize,
            )
        except BaseException as e:  # noqa: BLE001 — 원래 스레드로 옮겨 다시 올린다
            box["error"] = e

    t = threading.Thread(target=target, name="test-run_job", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        pytest.fail(f"run_job did not return within {timeout}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


def wait_until(pred: Callable[[], Any], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def is_dead(pid: int) -> bool:
    """없거나(ESRCH) zombie 면 죽은 것으로 본다(test_e2e_m3 와 같은 판정)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10
    ).stdout.strip()
    return out == "" or out.startswith("Z")


def group_gone(pgid: int) -> bool:
    """프로세스 그룹에 살아 있는 구성원이 없다(`killpg(pgid, 0)` 이 ESRCH)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def read_int(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return int(text) if text else None


@pytest.fixture(autouse=True)
def reap(monkeypatch):
    """러너가 띄운 프로세스를 기억했다가 테스트 끝에 그룹째 KILL — 실패해도 `sleep 30` 이 남지 않게.
    러너가 `subprocess.Popen` 을 속성으로 부르는 한 잡힌다(`from subprocess import Popen` 이면
    지나간다)."""
    started: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    class TrackedPopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", TrackedPopen)
    yield started
    for proc in started:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass


def assert_executing_once_before_output(obs: RecordingObserver) -> None:
    """§1: phase `executing` 은 한 번, 자재화 뒤 · 첫 output 앞. 다른 phase 는 `materializing`
    뿐."""
    kinds = [k for k, _ in obs.events]
    assert obs.phases.count(PHASE_EXECUTING) == 1, obs.phases
    assert set(obs.phases) <= {PHASE_MATERIALIZING, PHASE_EXECUTING}, obs.phases
    executing_at = obs.events.index(("phase", PHASE_EXECUTING))
    assert "materialize" in kinds and kinds.index("materialize") < executing_at, obs.events
    first_output = next((i for i, k in enumerate(kinds) if k == "output"), None)
    if first_output is not None:
        assert executing_at < first_output, obs.events[: first_output + 1]


def assert_log_matches(spec: RunSpec, obs: RecordingObserver, *, prefix: bytes = b"") -> None:
    """러너가 `log_path` 에 쓴 바이트 == 관찰자가 받은 바이트(앞에 자재화 단계의 줄이 있으면 그
    뒤)."""
    assert spec.log_path.read_bytes() == prefix + obs.out


# ── 모양 ─────────────────────────────────────────────────────────────────────


def test_runspec_and_runresult_have_the_fields_of_the_spec():
    """§1 의 dataclass 필드 이름을 잠근다 — B(claim 응답 → RunSpec) · C(e2e) 가 같은 이름을 쓴다."""
    assert dataclasses.is_dataclass(RunSpec) and dataclasses.is_dataclass(RunResult)
    assert [f.name for f in dataclasses.fields(RunSpec)] == [
        "job_id",
        "preset_name",
        "argv",
        "env",
        "env_passthrough",
        "timeout_seconds",
        "inputs",
        "requester_label",
        "source",
        "workspace",
        "log_path",
        "grace_seconds",
    ]
    assert {f.name for f in dataclasses.fields(RunResult)} >= {
        "rc",
        "cancelled",
        "timed_out",
        "lost",
        "started",
        "finished",
    }
    assert isinstance(MAX_LINE_BYTES, int) and MAX_LINE_BYTES == 64 * 1024
    assert issubclass(RunnerError, Exception) and not issubclass(RunnerError, MaterializeError)
    assert callable(getattr(RunObserver, "phase", None))  # protocol 의 메서드 이름


# ── 정상 경로 ─────────────────────────────────────────────────────────────────


def test_happy_path_materialize_then_executing_then_raw_output_then_result(tmp_path):
    """§1: 순서는 materialize → phase executing(한 번) → output 배치 → 반환. 마커 줄은 **raw
    그대로** 관찰자에게 간다(러너는 마커를 파싱하지 않는다). `rc == 0`, 강제 종료 플래그는 전부
    False, `started <= finished` 이고 둘 다 주입한 `now_fn` 이 돌려준 값(aware UTC)이다."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, sh("echo hi; echo ::rcm::summary::ok"))
    mat = FileMaterializer(observer=obs)
    clock = Clock()
    result = run(spec, obs, materialize=mat, now_fn=clock)
    assert result.rc == 0
    assert (result.cancelled, result.timed_out, result.lost) == (False, False, False)
    assert result.started.tzinfo is not None and result.started <= result.finished
    assert result.started in clock.values and result.finished in clock.values
    assert mat.specs == [spec]
    assert_executing_once_before_output(obs)
    assert obs.out == b"hi\n::rcm::summary::ok\n"
    assert_log_matches(spec, obs)


def test_env_has_rcm_vars_passthrough_preset_env_and_inputs_and_cwd_is_the_workspace(tmp_path):
    """§1 + 오늘의 `_env`: `RCM_JOB_ID · RCM_PRESET · RCM_REQUESTER · RCM_SOURCE_MODE · RCM_REF ·
    RCM_BASE_SHA · RCM_DIRTY · RCM_WORKSPACE · RCM_LOG_FILE` + `env_passthrough` 에 있는 키만
    `environ` 에서 + `spec.env` 가 통과값을 덮고 + `env_for_inputs(inputs)`. `RCM_*` 는 `spec.env`
    보다 세다. cwd 는 워크스페이스(자재화한 hello.txt 가 보인다)."""
    obs = RecordingObserver()
    script = (
        'echo "JOB=$RCM_JOB_ID|PRESET=$RCM_PRESET|REQ=$RCM_REQUESTER|MODE=$RCM_SOURCE_MODE"; '
        'echo "REF=$RCM_REF|BASE=$RCM_BASE_SHA|DIRTY=$RCM_DIRTY"; '
        'echo "WS=$RCM_WORKSPACE|LOG=$RCM_LOG_FILE"; '
        'echo "CI=$CI|SECRET=$SECRET|HOME=${HOME-unset}|LANG=${LANG-unset}"; '
        'echo "SCOPE=$RCM_INPUT_SCOPE|DRY=$RCM_INPUT_DRY_RUN|PWD=$(pwd)"; cat hello.txt'
    )
    inputs = {"scope": "fast", "dry-run": True}
    spec = make_spec(
        tmp_path,
        sh(script),
        job_id=511,
        preset_name="gate",
        env={"CI": "1", "SECRET": "from-preset", "RCM_JOB_ID": "999"},
        env_passthrough=("PATH", "SECRET"),
        inputs=inputs,
        requester_label="alice@laptop",
        source=GIT_REF,
    )
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    assert result.rc == 0, obs.out
    got = dict(
        pair.split("=", 1) for line in obs.out.decode().splitlines()[:5] for pair in line.split("|")
    )
    assert got["JOB"] == "511" and got["PRESET"] == "gate" and got["REQ"] == "alice@laptop"
    assert got["MODE"] == "git_ref" and got["REF"] == "refs/heads/main" and got["BASE"] == ""
    assert got["DIRTY"] == "0"
    assert Path(got["WS"]).resolve() == spec.workspace.resolve()
    assert Path(got["LOG"]).resolve() == spec.log_path.resolve()
    assert got["CI"] == "1"
    assert got["SECRET"] == "from-preset"  # spec.env 가 통과값을 덮는다
    assert got["HOME"] == "unset" and got["LANG"] == "unset"  # passthrough 에 없으면 안 간다
    assert env_for_inputs(inputs) == {"RCM_INPUT_SCOPE": "fast", "RCM_INPUT_DRY_RUN": "1"}
    assert got["SCOPE"] == "fast" and got["DRY"] == "1"
    assert Path(got["PWD"]).resolve() == spec.workspace.resolve()
    assert obs.out.endswith(b"hello\n")  # cwd 가 워크스페이스라 hello.txt 가 보인다


def test_tree_source_sets_base_sha_and_dirty_and_empty_ref(tmp_path):
    """오늘의 `_env`: tree 잡은 `RCM_BASE_SHA = base_sha` · `RCM_DIRTY = 1`(dirty) · `RCM_REF` 는
    빈 값."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, sh('echo "$RCM_SOURCE_MODE|$RCM_REF|$RCM_BASE_SHA|$RCM_DIRTY"'))
    assert run(spec, obs, materialize=FileMaterializer(observer=obs)).rc == 0
    assert obs.out == b"tree||abc123f|1\n"


# ── 자재화 · 시작 실패 · 종료 코드 ────────────────────────────────────────────


def test_materialize_error_propagates_and_executing_is_never_reported(tmp_path):
    """잠근 선택: `materialize` 의 `MaterializeError` 는 `run_job` 이 그대로 올린다(문구 보존).
    관찰자는 `executing` 도 output 도 보지 못한다 — 호출자가 failed 로 보고한다."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, sh("echo never"))

    def broken(spec: RunSpec) -> None:
        obs.events.append(("materialize", spec.job_id))
        raise MaterializeError("snapshot file is missing")

    with pytest.raises(MaterializeError) as e:
        run(spec, obs, materialize=broken)
    assert str(e.value) == "snapshot file is missing"
    assert PHASE_EXECUTING not in obs.phases and obs.out == b""
    assert not spec.workspace.exists()


def test_cannot_start_raises_runner_error_with_the_worker_wording(tmp_path):
    """잠근 선택: argv[0] 을 못 띄우면 `RunnerError` — 문구는 `cannot start 'nope'` 로 시작하고
    트레이스백·절대 경로가 없다. 자재화는 이미 끝났고 output 은 없다."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, ("nope", "--flag"))
    mat = FileMaterializer(observer=obs)
    with pytest.raises(RunnerError) as e:
        run(spec, obs, materialize=mat)
    text = str(e.value)
    assert text.startswith("cannot start 'nope'"), text
    assert "Traceback" not in text and str(tmp_path) not in text
    assert mat.specs == [spec] and obs.out == b""


def test_nonzero_exit_is_a_result_not_an_exception(tmp_path):
    """§1: 종료 코드는 결과다 — `rc == 3`, 플래그 없음, output 은 그대로. 예외는 안 난다."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, sh("echo ::rcm::step::test; echo boom; exit 3"))
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    assert result.rc == 3
    assert (result.cancelled, result.timed_out, result.lost) == (False, False, False)
    assert obs.out == b"::rcm::step::test\nboom\n"
    assert_executing_once_before_output(obs)
    assert_log_matches(spec, obs)


# ── 출력 배치 ─────────────────────────────────────────────────────────────────


def test_output_is_line_batched_in_order_and_the_partial_tail_arrives_at_eof(tmp_path):
    """§1 「줄 단위 배치」: 모든 `output` 조각은 개행으로 끝나고(비어 있지 않다), 이어 붙이면 순서
    그대로다. 개행 없는 마지막 조각은 EOF 에 개행을 붙여 넘긴다(오늘의 로그 규칙과 같다)."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, sh("echo one; echo two; printf three"))
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    assert result.rc == 0
    assert obs.chunks and all(c and c.endswith(b"\n") for c in obs.chunks), obs.chunks
    assert obs.out == b"one\ntwo\nthree\n"
    assert_log_matches(spec, obs)


def test_a_line_longer_than_max_line_bytes_is_delivered_without_waiting_for_eof(tmp_path):
    """§1 `MAX_LINE_BYTES`: 개행 없는 긴 출력은 잘라서 흘린다 — 자식이 아직 `sleep` 중일 때 이미
    관찰자에게 갔다(첫 조각과 `done` 조각 사이가 1초 이상). 바이트는 하나도 잃지 않는다(잘린 자리의
    개행만 더해진다). 로그 파일도 같은 바이트."""
    obs = RecordingObserver()
    payload = b"x" * (3 * MAX_LINE_BYTES + 17)
    spec = make_spec(tmp_path, sh("cat big.txt; sleep 1.2; echo; echo done"))
    mat = FileMaterializer({"big.txt": payload}, observer=obs)
    result = run(spec, obs, materialize=mat)
    assert result.rc == 0
    assert obs.out.replace(b"\n", b"") == payload + b"done"
    assert obs.out.endswith(b"done\n")
    first_x = next(i for i, c in enumerate(obs.chunks) if b"x" * 1024 in c)
    done = next(i for i, c in enumerate(obs.chunks) if c.endswith(b"done\n"))
    assert first_x < done
    assert obs.stamps[done] - obs.stamps[first_x] >= 1.0, obs.stamps[done] - obs.stamps[first_x]
    assert all(len(c) <= 2 * MAX_LINE_BYTES + 1 for c in obs.chunks[: done - 1]), [
        len(c) for c in obs.chunks
    ]
    assert_log_matches(spec, obs)


def test_log_file_is_appended_with_the_same_bytes_the_observer_gets(tmp_path):
    """잠근 선택: 러너가 `spec.log_path` 에 **직접** 쓴다(관찰자만이 아니다) — 호출자가 자재화
    단계에 남긴 줄 뒤에 append. 내용은 관찰자가 받은 바이트와 같다(마커 줄 포함)."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, sh("echo ::rcm::steps::1; echo ::rcm::step::a; echo hi"))
    spec.log_path.write_bytes(b"fetching refs/heads/main\n")
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    assert result.rc == 0
    assert obs.out == b"::rcm::steps::1\n::rcm::step::a\nhi\n"
    assert_log_matches(spec, obs, prefix=b"fetching refs/heads/main\n")


# ── 취소 · 타임아웃 · 정지 · 손자 ────────────────────────────────────────────


def test_cancel_sends_term_and_returns_well_before_grace(tmp_path):
    """§1 취소: `should_cancel` 이 True 가 되면 SIGTERM → 프로세스가 죽으면 바로 돌아온다(grace 를
    기다리지 않는다). `cancelled=True`, 나머지 플래그 False, `rc == -SIGTERM`, 뒤 출력은 없다."""
    obs = RecordingObserver()
    obs.cancel_if = lambda: b"start\n" in obs.out
    spec = make_spec(tmp_path, sh("echo start; sleep 30; echo never"), grace_seconds=5)
    t0 = time.monotonic()
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    took = time.monotonic() - t0
    assert result.cancelled is True and result.timed_out is False and result.lost is False
    assert result.rc == -signal.SIGTERM
    assert obs.cancel_seen_at is not None
    assert time.monotonic() - obs.cancel_seen_at < 4.0 and took < 6.0, took
    assert obs.out == b"start\n"
    assert_executing_once_before_output(obs)
    assert_log_matches(spec, obs)


def test_term_ignoring_child_is_killed_after_grace(tmp_path):
    """§1 취소: TERM 을 무시하는 자식(`trap '' TERM`)은 `grace_seconds`(1) 뒤 SIGKILL — 그 전엔
    죽이지 않는다(≥ 0.9초). `cancelled=True` · `rc == -SIGKILL`(test_e2e_m3 의 -9 와 같다)."""
    obs = RecordingObserver()
    obs.cancel_if = lambda: b"start\n" in obs.out
    spec = make_spec(tmp_path, sh("trap '' TERM; echo start; sleep 30"), grace_seconds=1)
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    finished_at = time.monotonic()
    assert result.cancelled is True and result.timed_out is False and result.lost is False
    assert result.rc == -signal.SIGKILL
    assert obs.cancel_seen_at is not None
    took = finished_at - obs.cancel_seen_at
    assert 0.9 <= took < 4.0, took


def test_timeout_marks_timed_out_and_kills_the_process(tmp_path):
    """§1 타임아웃: `timeout_seconds=1` 을 넘기면 SIGTERM → `timed_out=True`(cancelled · lost 는
    False), `rc == -SIGTERM`, `sleep 30` 을 기다리지 않는다. 관찰자의 취소·정지는 물은 적 없어도
    된다."""
    obs = RecordingObserver()
    spec = make_spec(tmp_path, sh("echo start; sleep 30; echo never"), timeout_seconds=1)
    t0 = time.monotonic()
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    took = time.monotonic() - t0
    assert result.timed_out is True and result.cancelled is False and result.lost is False
    assert result.rc == -signal.SIGTERM
    assert 0.9 <= took < 5.0, took
    assert obs.out == b"start\n"
    assert (result.finished - result.started).total_seconds() >= 1.0


def test_should_stop_marks_lost_and_the_process_group_is_gone(tmp_path):
    """§1 `should_stop`(SIGTERM/Ctrl-C): 도는 잡을 SIGTERM → `lost=True`(cancelled · timed_out 은
    False). 돌아왔을 때 세션 그룹에 살아 있는 프로세스가 없다."""
    obs = RecordingObserver()
    pgid_file = tmp_path / "pgid"
    obs.stop_if = lambda: read_int(pgid_file) is not None
    spec = make_spec(tmp_path, sh(f'echo $$ > "{pgid_file}"; echo start; sleep 30'))
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    assert result.lost is True and result.cancelled is False and result.timed_out is False
    assert result.rc == -signal.SIGTERM
    pgid = read_int(pgid_file)
    assert pgid is not None
    wait_until(lambda: group_gone(pgid), 5.0, f"process group {pgid} to be gone")


def test_should_stop_wins_over_should_cancel_when_both_are_true(tmp_path):
    """오늘의 `_pump` 순서 그대로: 정지와 취소가 같이 True 면 `lost` 다(cancelled 가 아니다) —
    서버 종료 중 cancelling 잡은 lost 로 남는 규칙."""
    obs = RecordingObserver()
    obs.cancel_if = lambda: b"start\n" in obs.out
    obs.stop_if = lambda: b"start\n" in obs.out
    spec = make_spec(tmp_path, sh("echo start; sleep 30"))
    result = run(spec, obs, materialize=FileMaterializer(observer=obs))
    assert result.lost is True and result.cancelled is False and result.timed_out is False
    assert result.rc == -signal.SIGTERM


def test_cancel_kills_the_grandchildren_of_the_job_process(tmp_path):
    """§1 손자: `start_new_session` + `killpg` — 자식이 띄운 `sleep 60`(손자)도 취소로 죽는다
    (test_e2e_m3 와 같은 판정: pid 가 없거나 zombie · 그룹에 산 프로세스 없음)."""
    obs = RecordingObserver()
    pid_file = tmp_path / "pid"
    pgid_file = tmp_path / "pgid"
    obs.cancel_if = lambda: read_int(pid_file) is not None
    spec = make_spec(
        tmp_path,
        sh(f'sleep 60 & echo $! > "{pid_file}"; echo $$ > "{pgid_file}"; wait'),
        grace_seconds=5,
    )
    try:
        result = run(spec, obs, materialize=FileMaterializer(observer=obs))
        assert result.cancelled is True and result.lost is False and result.timed_out is False
        pid, pgid = read_int(pid_file), read_int(pgid_file)
        assert pid is not None and pgid is not None
        wait_until(lambda: is_dead(pid), 5.0, f"grandchild {pid} to die")
        wait_until(lambda: group_gone(pgid), 5.0, f"process group {pgid} to be gone")
        assert obs.cancel_seen_at is not None
        assert time.monotonic() - obs.cancel_seen_at < 5.0
    finally:
        pid = read_int(pid_file)
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
