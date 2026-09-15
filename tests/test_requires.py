"""프리셋 `requires`(M5j G4) — 잡 시작 전에 도구를 찾고, 없으면 프로세스를 띄우지 않는다.

명세 `docs/gate-optimization-workplan.md` §2 G4 · 결정 85 · Codex 리뷰 G4 행
(`docs/reviews/2026-09-10-codex-gate-optimization-design.md`).

- 검사는 `runner.build_env()` 가 만든 **최종 환경**에서 한다. PATH 가 없으면 빈 PATH 다 —
  검사 프로세스(서버·워커)의 PATH 로 물러나지 않는다.
- 없으면 `failed` · `summary_code = "tool_missing"` · `summary_args = {"tool": "<이름>"}`
  **까지만**. PATH 와 경로는 상태·요약·로그 어디에도 없다(PLAN 「보안」). 라벨(`failed_step` ·
  `last_step`)도 대장 행(`job_failures`)도 없다 — 서버가 만든 코드이지 스크립트의 선언이
  아니다(M5h 불변식).
- 원격 워커: claim 의 preset 문서에 `requires`, finish 에 구조화된 `summary_code/summary_args`.
  옛 워커(키 없음)는 그대로 돈다.
- `rcm check --config` 의 `local preset tools` 행은 `tests/test_requires_check.py`, 문서 잠금은
  `tests/test_docs_m5j_requires.py` — 이 파일은 mutcheck 대상이라 `examples/` 를 읽는 모듈을
  import 하지 않는다.

구현 전이라 빨간 것이 정상이다.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

import test_cli_worker as cliw
from remote_ci_monitor.config import ConfigError, ServerConfig, parse_preset
from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.model import CANCELLING, FAILED, SUCCEEDED, Source
from remote_ci_monitor.runner import RequiredToolMissing, RunSpec, run_job
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import Worker
from test_cli_worker import run, worker_argv
from test_server import sh
from test_worker import enqueue, make_config, run_one
from test_worker_api import WorkerServer, running_job

MISSING = "definitely-missing-tool-rcm"

#: `rcm worker --once` 시험의 픽스처 — test_cli_worker 의 것을 그대로 쓴다(이름을 다시 묶으면
#: 인자 이름이 import 를 가리는 F811 이 안 난다).
home, token, data_dir = cliw.home, cliw.token, cliw.data_dir


# ── 도우미 ───────────────────────────────────────────────────────────────────


def fake_tool(directory: Path, name: str) -> Path:
    """실행 가능한 가짜 도구 하나. `shutil.which` 가 찾을 수 있게 x 비트를 켠다."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


class _Observer:
    """관찰자 대역 — phase 와 출력만 모은다."""

    def __init__(self) -> None:
        self.phases: list[str] = []
        self.out = b""

    def phase(self, phase: str) -> None:
        self.phases.append(phase)

    def output(self, data: bytes) -> None:
        self.out += data

    def should_cancel(self) -> bool:
        return False

    def should_stop(self) -> bool:
        return False


def spec_for(tmp_path: Path, *, requires: tuple[str, ...], **kw: Any) -> RunSpec:
    """`sh -c 'echo STARTED'` 를 도는 RunSpec — 로그에 STARTED 가 있으면 프로세스가 떴다는 뜻."""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    fields: dict[str, Any] = dict(
        job_id=7,
        preset_name="gate",
        argv=("sh", "-c", "echo STARTED"),
        env={},
        env_passthrough=("PATH",),
        timeout_seconds=30,
        inputs={},
        requester_label="alice@laptop",
        source=Source(mode="tree"),
        workspace=ws,
        log_path=tmp_path / "log.txt",
        grace_seconds=1,
        requires=requires,
    )
    fields.update(kw)
    return RunSpec(**fields)


def ledger_rows(store: Store, job_id: int) -> list[str]:
    return [
        str(r[0])
        for r in store._conn()
        .execute("SELECT name FROM job_failures WHERE job_id=? ORDER BY seq", (job_id,))
        .fetchall()
    ]


# ── 설정 ─────────────────────────────────────────────────────────────────────


def test_requires_accepts_names_and_absolute_paths():
    p = parse_preset({"name": "gate", "argv": ["x"], "requires": ["fvm", "/opt/bin/gitleaks"]})
    assert p.requires == ("fvm", "/opt/bin/gitleaks")


def test_requires_is_empty_when_absent():
    """키가 없으면 오늘의 동작 그대로 — 검사도 로그 줄도 없다."""
    assert parse_preset({"name": "gate", "argv": ["x"]}).requires == ()


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        (["bin/fvm"], "bin/fvm"),  # 상대경로 — 어느 cwd 에서 찾는지 정해져 있지 않다
        (["./fvm"], "./fvm"),
        ([""], "empty"),
        (["fvm", "fvm"], "duplicate"),
        (["fvm", 3], "list of strings"),
        ("fvm", "list of strings"),
    ],
)
def test_requires_rejects_relative_paths_empty_entries_and_duplicates(value, needle):
    """§2 G4: 이름 또는 절대경로만. 오류에는 프리셋 이름과 키가 있다."""
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "gate", "argv": ["x"], "requires": value})
    msg = str(e.value)
    assert "preset 'gate'" in msg and "requires" in msg, msg
    assert needle in msg, msg


# ── 실행기: 최종 환경에서 검사 ───────────────────────────────────────────────


def test_a_missing_tool_stops_the_job_before_the_process_starts(tmp_path):
    """없으면 `RequiredToolMissing(tool)` — Popen 전이라 STARTED 도 `executing` phase 도 없다.
    로그에는 이름과 판정만: PATH 값은 없다."""
    secret_bin = tmp_path / "secret-bin"
    fake_tool(secret_bin, "present-tool")
    spec = spec_for(tmp_path, requires=("present-tool", MISSING))
    obs = _Observer()
    with pytest.raises(RequiredToolMissing) as e:
        run_job(spec, obs, environ={"PATH": str(secret_bin)})
    assert e.value.tool == MISSING
    log = spec.log_path.read_text()
    assert "STARTED" not in log and "executing" not in obs.phases
    assert f"[rcm] required tool {MISSING}: missing" in log, log
    assert str(secret_bin) not in log and str(secret_bin) not in str(e.value)
    assert "PATH=" not in log


def test_present_tools_are_named_in_the_log_and_the_job_runs(tmp_path):
    """다 있으면 한 줄 `[rcm] required tools: <name> ok · <name> ok` 뒤에 프로세스가 돈다.
    절대경로는 PATH 없이도 그 자리에서 보고, 로그에는 basename 만(M5l S3)."""
    tools = tmp_path / "tools"
    fake_tool(tools, "fvm")
    gitleaks = fake_tool(tools, "gitleaks")
    spec = spec_for(tmp_path, requires=("fvm", str(gitleaks)))
    obs = _Observer()
    result = run_job(spec, obs, environ={"PATH": f"{tools}{os.pathsep}/usr/bin:/bin"})
    assert result.rc == 0
    log = spec.log_path.read_text()
    assert "[rcm] required tools: fvm ok · gitleaks ok" in log, log
    assert str(tools) not in log
    assert "STARTED" in log
    assert log.index("required tools") < log.index("STARTED")
    # 관찰자(원격 워커의 로그 업로드)도 같은 줄을 받는다
    assert b"[rcm] required tools:" in obs.out


def test_no_requires_means_no_preflight_line(tmp_path):
    spec = spec_for(tmp_path, requires=())
    run_job(spec, _Observer(), environ={"PATH": "/usr/bin:/bin"})
    assert "[rcm] required tool" not in spec.log_path.read_text()


def test_the_check_uses_the_jobs_path_not_the_checkers(tmp_path, monkeypatch):
    """PATH 를 넘기지 않는 프리셋(`env_passthrough` 에 PATH 없음)이면 잡 환경에 PATH 가 없다 —
    그때 검사는 **빈 PATH** 로 하고, 검사 프로세스의 `os.environ["PATH"]` 로 물러나지 않는다.
    `sh` 조차 못 찾는다(argv 의 sh 는 Popen 이 execvp 로 찾지만, 검사는 잡의 PATH 가 정본)."""
    tools = tmp_path / "tools"
    fake_tool(tools, "fvm")
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}/usr/bin:/bin")
    spec = spec_for(tmp_path, requires=("fvm",), env_passthrough=())
    with pytest.raises(RequiredToolMissing) as e:
        run_job(spec, _Observer(), environ=dict(os.environ))
    assert e.value.tool == "fvm"
    assert "STARTED" not in spec.log_path.read_text()


def test_the_presets_own_path_wins(tmp_path):
    """`[presets.env] PATH = …` 가 passthrough 를 덮는다 — 문서의 launchd 우회가 실제로 통한다."""
    tools = tmp_path / "tools"
    fake_tool(tools, "fvm")
    spec = spec_for(
        tmp_path,
        requires=("fvm",),
        env={"PATH": f"{tools}{os.pathsep}/usr/bin:/bin"},
        env_passthrough=("PATH",),
    )
    result = run_job(spec, _Observer(), environ={"PATH": "/usr/bin:/bin"})  # 서버 PATH 엔 없다
    assert result.rc == 0


# ── 로컬 워커 ────────────────────────────────────────────────────────────────


@pytest.fixture
def wenv(tmp_path):
    cfg = make_config(tmp_path)
    cfg.presets = (
        *cfg.presets,
        parse_preset(sh("needs-missing", "echo STARTED", requires=["sh", MISSING])),
        parse_preset(sh("needs-sh", "echo '::rcm::step::t'; echo STARTED", requires=["sh"])),
        parse_preset(sh("needs-abs", "echo STARTED", requires=["/nonexistent/dir/fvm"])),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def test_worker_fails_the_job_with_tool_missing_and_no_label(wenv, tmp_path):
    """§2 G4 · 결정 85: `failed` · `tool_missing` · args 는 `{"tool": 이름}` 뿐 · `failed_step` 과
    `last_step` 은 null · 대장 행 0 · exit_code null. 로그엔 이름과 판정만, PATH 값은 없다."""
    store, cfg = wenv
    secret_bin = tmp_path / "secret-bin"
    secret_bin.mkdir()
    jid = enqueue(store, cfg, "needs-missing")
    w = Worker(1, store, cfg)
    w.environ = {"PATH": f"{secret_bin}{os.pathsep}/usr/bin:/bin"}
    run_one_with(store, cfg, jid, w)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None, (j.state, j.summary)
    assert j.summary_code == "tool_missing" and j.summary_args == {"tool": MISSING}
    assert j.summary == outcome.render("tool_missing", {"tool": MISSING})
    assert j.failed_step is None and j.last_step is None
    assert ledger_rows(store, jid) == []
    rows, _, _ = store.failure_stats(jid, j.key, j.finished_at, window=20)
    assert rows == []
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert f"[rcm] required tool {MISSING}: missing" in log, log
    assert "STARTED" not in log
    assert str(secret_bin) not in log and str(secret_bin) not in (j.summary or "")
    assert "/" not in j.summary  # 경로 없음


def test_worker_runs_when_the_tool_is_present(wenv):
    store, cfg = wenv
    jid = enqueue(store, cfg, "needs-sh")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == SUCCEEDED and j.summary_code is None
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert "[rcm] required tools: sh ok" in log and "STARTED" in log


def test_a_missing_absolute_path_reports_only_the_tool_name(wenv):
    """절대경로로 선언한 도구가 없으면 공개 args 에도 잡 로그에도 **이름만**(basename) 실린다 —
    서버의 디렉터리 배치는 `/api/status.recent` 로도 토큰 보호 로그로도 새지 않는다(M5l S3)."""
    store, cfg = wenv
    jid = enqueue(store, cfg, "needs-abs")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.summary_code == "tool_missing"
    assert j.summary_args == {"tool": "fvm"}
    assert "/nonexistent" not in (j.summary or "")
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert "[rcm] required tool fvm: missing" in log, log
    assert "/nonexistent" not in log


def test_a_tool_missing_job_is_left_out_of_the_failure_window(wenv):
    """검증 G4.4(명세 §2 G4 「대장 행 없음」의 귀결 · 결정 68 의 분모): `tool_missing` 잡은 같은
    key 의 창(`window`·`window_unnamed`)에 **들지 않는다** — 취소·유실처럼 스크립트에 대해 아무
    말도 못 한 잡이다. 세지면 결정적 실패 셋이 「4 중 3 · intermittent?」 로 보인다(8801 실측)."""
    from datetime import UTC, datetime, timedelta

    store, cfg = wenv
    t0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    ids = [enqueue(store, cfg, "needs-missing") for _ in range(4)]
    tm, *named = ids
    text, code, args = outcome.summary("tool_missing", tool=MISSING)
    assert store.finish(
        tm, FAILED, now=t0, exit_code=None, summary=text, summary_code=code, summary_args=args
    )
    for i, jid in enumerate(named, start=1):
        assert store.finish(
            jid,
            FAILED,
            now=t0 + timedelta(seconds=10 * i),
            exit_code=1,
            failed_step="build",
            fail_names=["build"],
        )
    key = store.get_job(named[-1]).key
    at = t0 + timedelta(seconds=30)
    rows, window, unnamed = store.failure_stats(named[-1], key, at, window=20)
    assert (window, unnamed) == (3, 0), (window, unnamed)
    assert [(r.name, r.seen) for r in rows] == [("build", 3)]
    # 앵커가 `tool_missing` 잡 자신이어도 창은 이름 실패만 센다
    rows, window, unnamed = store.failure_stats(tm, key, t0, window=20)
    assert rows == [] and (window, unnamed) == (0, 0)


def run_one_with(store: Store, cfg: ServerConfig, job_id: int, w: Worker, timeout=20.0):
    """`test_worker.run_one` 과 같되 미리 만든 워커(환경을 바꾼)를 쓴다."""
    import time

    w.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = store.get_job(job_id)
        if j.is_terminal:
            break
        time.sleep(0.05)
    w.stop_event.set()
    w.wake.set()
    w.join(timeout=5)


# ── 원격 워커 프로토콜 ───────────────────────────────────────────────────────


@pytest.fixture
def wsrv(tmp_path):
    s = WorkerServer(tmp_path)
    s.cfg.presets = (
        *s.cfg.presets,
        parse_preset(sh("needs", "echo x", requires=["fvm", "/opt/bin/gitleaks"])),
    )
    yield s
    s.close()


def test_claim_carries_requires_in_the_preset_document(wsrv):
    """Codex G4: claim 의 preset 문서에 `requires` — 워커가 같은 규칙으로 검사할 수 있게."""
    wsrv.registered("build-02")
    wsrv.registered("build-03")
    jid = wsrv.queued_job(preset="needs")
    status, body = wsrv.claim("build-02")
    assert status == 200 and body["job"]["id"] == jid, body
    assert body["preset"]["requires"] == ["fvm", "/opt/bin/gitleaks"]
    jid2 = wsrv.queued_job(preset="ok")
    status, body = wsrv.claim("build-03")
    assert status == 200 and body["job"]["id"] == jid2, body
    assert body["preset"]["requires"] == []  # 키는 언제나 있다 — 옛 워커는 무시한다


def test_finish_keeps_a_structured_tool_missing_and_drops_everything_else(wsrv):
    """finish 의 `summary_code`/`summary_args` 는 아는 코드만 받고, args 는 이름 하나로 줄인다 —
    워커가 PATH 를 실어 보내도 상태에 남지 않는다. 라벨·대장은 없다."""
    jid = running_job(wsrv)
    wsrv.log("build-02", jid, b"::rcm::step::build\n")  # 있어도 라벨이 되지 않는다
    status, body = wsrv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "tool_missing",
            "summary_args": {"tool": "fvm", "path": "/secret/bin:/usr/bin"},
        },
    )
    assert status == 200, body
    v = wsrv.view(jid)
    assert v["state"] == FAILED and v["exit_code"] is None
    assert v["summary_code"] == "tool_missing" and v["summary_args"] == {"tool": "fvm"}
    assert v["summary"] == outcome.render("tool_missing", {"tool": "fvm"})
    assert v["failed_step"] is None and v["last_step"] is None
    assert ledger_rows(wsrv.store, jid) == []
    assert "/secret" not in str(v)


@pytest.mark.parametrize(
    "body",
    [
        {"outcome": "failed", "exit_code": None, "summary_code": "exit_code"},  # 워커 몫 아님
        {"outcome": "failed", "exit_code": None, "summary_code": "tool_missing"},  # args 없음
        {"outcome": "failed", "exit_code": None, "summary_code": "tool_missing", "summary_args": 1},
        {
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "tool_missing",
            "summary_args": {"tool": ""},
        },
        {
            "outcome": "succeeded",
            "exit_code": 0,
            "summary_code": "tool_missing",
            "summary_args": {"tool": "fvm"},
        },
        {
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "tool_missing",
            "summary_args": {"tool": "/opt/bin/"},  # 이름이 없는 경로 — basename 이 비어 있다
        },
        {
            "outcome": "failed",
            "exit_code": 1,  # 프로세스가 떴다면 preflight 실패가 아니다
            "summary_code": "tool_missing",
            "summary_args": {"tool": "fvm"},
        },
        # F2b 로 어휘가 늘어도 fail-closed 는 그대로다 — 모르는 코드는 400 이지 「그냥 저장」이
        # 아니고, 아는 키의 값이 모양에 안 맞으면 400 이다.
        {"outcome": "failed", "exit_code": None, "summary_code": "rm_rf_everything"},
        {
            "outcome": "failed",
            "exit_code": 3,
            "summary_code": "snapshot_missing",
            "summary_args": {},
        },
        {
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "blob_missing",
            "summary_args": {"sha": "../../etc/passwd"},  # 16진이 아니다
        },
        {
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "snapshot_rejected",
            "summary_args": {"kind": "rm -rf /"},  # 닫힌 까닭도 클래스 이름도 아니다
        },
        # 코드만 오고 인자가 없으면 문장이 `?` 밖에 못 말한다 — 로컬 레인이 남기는 행과 달라진다
        {"outcome": "failed", "exit_code": None, "summary_code": "repo_missing"},
        {
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "git_failed",
            "summary_args": {"kind": "timeout", "op": "rm -rf", "seconds": 1},
        },
    ],
)
def test_finish_rejects_unknown_or_inconsistent_structured_summaries(wsrv, body):
    jid = running_job(wsrv)
    status, resp = wsrv.req("POST", f"/worker/jobs/{jid}/finish", token="build-02", json_body=body)
    assert status == 400, resp
    assert wsrv.view(jid)["state"] == "running"


def test_finish_keeps_only_the_arguments_the_code_declares(wsrv):
    """T12(F2b): 워커는 토큰이 있을 뿐 신뢰 경계 안이 아니다 — 잡 요약을 짜낼 권한까지 준 적은
    없다. `tool` 을 다시 basename 뜨는 것과 같은 이유로 멤버 이름도 서버가 **다시** 이름만 남기고,
    표에 없는 키는 통째로 버린다. 문장은 워커가 보낸 것이 아니라 서버가 코드로 그린 것이다."""
    jid = running_job(wsrv)
    wsrv.log("build-02", jid, b"::rcm::step::build\n")  # 있어도 라벨이 되지 않는다
    status, body = wsrv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "snapshot_rejected",
            "summary_args": {
                "kind": "absolute_path",
                "member": "/srv/private/blobs/aa/secret.txt",
                "detail": "cannot download snapshot: 503 from /srv/private/blobs/aa",
                "path": "/etc/shadow",
            },
        },
    )
    assert status == 200, body
    v = wsrv.view(jid)
    assert v["summary_code"] == "snapshot_rejected"
    assert v["summary_args"] == {"kind": "absolute_path", "member": "secret.txt"}
    assert v["summary"] == "snapshot rejected: absolute path in archive: secret.txt"
    assert v["failed_step"] is None and v["last_step"] is None
    assert ledger_rows(wsrv.store, jid) == []
    status, doc = wsrv.req("GET", "/api/status", token="build-02")
    assert status == 200 and "/srv/private" not in str(doc) and "/etc/shadow" not in str(doc)


def test_the_server_closes_a_cancelling_job_as_cancelled_over_a_preflight_finish(wsrv):
    """T13(F2b) 취소 경주: 자재화 실패도 사용자의 취소를 덮지 않는다 — 코드가 늘어도 같은 규칙이다
    (`tool_missing` 에 대한 M5l S4 의 쌍둥이)."""
    jid = running_job(wsrv)
    assert wsrv.cancel(jid)[1]["state"] == "cancelling"
    status, body = wsrv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "snapshot_missing",
            "summary_args": {},
        },
    )
    assert status == 200, body
    v = wsrv.view(jid)
    assert v["state"] == "cancelled" and v["exit_code"] is None, v
    assert v["summary_code"] == "cancelled_by" and v["summary_args"] == {"by": "alice-laptop"}


def test_a_cancel_that_lands_after_the_server_read_the_job_still_wins(wsrv):
    """T14(F2b · 검토 2): 원격 취소 경주의 **마지막 틈**. 서버는 요청 맨 앞에서 잡 상태를 한 번
    읽고, 그 사본으로 「취소 중인가」를 판단한 뒤, 맨 끝에서 finish 를 쓴다. 취소가 그 둘 **사이**
    에 착륙하면 낡은 사본은 `running` 이라 preflight 실패가 살아남고, 조건 없는 finish 가
    `cancelling` 을 `failed` 로 덮었다 — 사용자가 멈춘 잡이 실패로 기록된다.

    로컬 레인은 `only_from=(RUNNING,)` 으로 이 틈을 닫았다(M5l S4). 여기서는 서버가 잡을 읽은
    **뒤에** 부르는 `store.markers` 에 취소를 끼워 넣어 그 순서를 그대로 만든다.
    """
    jid = running_job(wsrv)
    real_markers = wsrv.store.markers
    landed = []

    def cancel_then_read(job_id: int):
        if not landed:
            landed.append(wsrv.store.request_cancel(jid, "alice-laptop", wsrv.clock(), 1))
        return real_markers(job_id)

    wsrv.store.markers = cancel_then_read  # type: ignore[method-assign]
    try:
        status, body = wsrv.req(
            "POST",
            f"/worker/jobs/{jid}/finish",
            token="build-02",
            json_body={
                "outcome": "failed",
                "exit_code": None,
                "summary_code": "git_failed",
                "summary_args": {"kind": "timeout", "op": "git fetch", "seconds": 300},
            },
        )
    finally:
        wsrv.store.markers = real_markers  # type: ignore[method-assign]
    assert landed == [CANCELLING], landed  # 취소가 정말 그 사이에 들어갔다
    assert status == 200, body
    v = wsrv.view(jid)
    assert v["state"] == "cancelled" and v["exit_code"] is None, v
    assert v["summary_code"] == "cancelled_by" and v["summary_args"] == {"by": "alice-laptop"}
    assert [t["state"] for t in v["transitions"]][-2:] == ["cancelling", "cancelled"]


def test_finish_reduces_a_tool_given_as_a_path_to_its_name(wsrv):
    """검증 G4.15: 워커가 `tool` 에 절대경로를 실어 보내도 서버는 **이름(basename)만** 남긴다 —
    로컬 워커(`RequiredToolMissing.public_name`)와 같은 규칙이고, 서버가 워커를 믿지 않는다.
    경로는 `GET /jobs/<id>` 에도 `/api/status.recent` 에도 없다(PLAN 「보안」)."""
    jid = running_job(wsrv)
    status, body = wsrv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "tool_missing",
            "summary_args": {"tool": "/opt/secret/bin/fvm"},
        },
    )
    assert status == 200, body
    v = wsrv.view(jid)
    assert v["summary_code"] == "tool_missing" and v["summary_args"] == {"tool": "fvm"}
    assert v["summary"] == outcome.render("tool_missing", {"tool": "fvm"})
    assert "/opt/secret" not in str(v)
    status, doc = wsrv.req("GET", "/api/status", token="build-02")
    assert status == 200 and "/opt/secret" not in str(doc)


def test_an_old_worker_without_the_keys_still_finishes(wsrv):
    """키 추가만이다 — 옛 워커의 finish(문자열 summary 뿐)는 그대로 통한다."""
    jid = running_job(wsrv)
    status, body = wsrv.finish("build-02", jid, "failed", exit_code=None, summary="old worker")
    assert status == 200, body
    v = wsrv.view(jid)
    assert v["state"] == FAILED and v["summary"] == "old worker" and v["summary_code"] is None


def test_rcm_worker_once_preflights_and_reports_tool_missing(
    home, srv_cli, token, data_dir, capsys, tmp_path
):
    """진짜 `rcm worker --once`: 워커가 자기 최종 환경에서 검사하고 구조화된 코드로 보고한다.
    서버 잡은 로컬과 같은 모양 — `tool_missing` · args 이름만 · 라벨 없음 · exit_code null."""
    token(srv_cli)
    srv_cli.cfg.presets = (
        *srv_cli.cfg.presets,
        parse_preset(sh("needsl", "echo STARTED", pool="linux", requires=["sh", MISSING])),
    )
    jid = srv_cli.queued_job(preset="needsl")
    code, out, err = run(capsys, worker_argv(srv_cli, "--once", data=data_dir))
    assert code == 0, out + err
    j = srv_cli.store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None, (j.state, j.summary, err)
    assert j.summary_code == "tool_missing" and j.summary_args == {"tool": MISSING}
    assert j.failed_step is None and j.last_step is None
    assert ledger_rows(srv_cli.store, jid) == []
    assert f"#{jid} failed" in err, err
    # 서버 쪽 로그(워커가 올린)에도 판정 줄이 있고 STARTED 는 없다
    log = (srv_cli.cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert f"[rcm] required tool {MISSING}: missing" in log, log
    assert "STARTED" not in log
    assert os.environ["PATH"] not in log  # 워커의 PATH 값은 어디에도 없다


@pytest.fixture
def srv_cli(tmp_path):
    from test_cli_worker import make_server

    s = make_server(tmp_path, worker_claim_wait_seconds=2)
    yield s
    s.close()


# ── outcome 코드 ─────────────────────────────────────────────────────────────


def test_tool_missing_is_a_server_outcome_code():
    text, code, args = outcome.summary("tool_missing", tool="fvm")
    assert code == "tool_missing" and args == {"tool": "fvm"}
    assert text == "required tool fvm is missing"
