"""M5t-a PR1 — 원격 금지 표식 `local_only`(결정 254 · `docs/m5t-workplan.md` §4.3 · §7 TO 표).

`local_only = true` 인 프리셋에게 서버의 머신 목록은 `@local` 하나뿐이다. 거르는 곳은 후보 함수
하나(`machines_for`)이고 배치 · ETA · 제출 · 점검 · `/worker/spec` · claim · `/api/machines` 는 모두
그 결과를 쓴다. TO15 는 진입점마다 한 경우로 나눠, 후보 함수의 원격 제외를 지운 변이 하나가
진입점마다 빨개지는지 본다(6-A-M3).

서버는 `tests/test_server_m5n.py` 의 `M5nServer` 다(in-process HTTP · 주입 시계 · 로컬 레인
스레드 없음). 원격 워커 `build-02` 는 기본 풀에, `mbp` 는 `mac2` 풀에 붙는다. 원격 워커는 설정의
**모든** 프리셋에 사실을 보낸다 — 이 PR 이전의 워커는 `/worker/spec` 에서 `local_only` 프리셋도
받았으므로 그것을 잰 보고가 그대로 온다. 서버는 그 보고가 있어도 원격을 후보로 삼지 않아야 한다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import server as server_module
from remote_ci_monitor.config import ConfigError, load_server_config
from remote_ci_monitor.core.model import (
    PLACEMENT_CAPABLE,
    PLACEMENT_POOL,
    QUEUED,
    Requester,
    Source,
)
from remote_ci_monitor.store import Claimant, Store
from test_server import TAR, TREE_HASH
from test_server_m5n import GIT_SHA, HOST_B, HOST_C, PROBES, M5nServer, ok_probe
from test_worker_api import T0

LOCAL = "@local"
REMOTE = "build-02"  # 기본 풀의 원격 워커
OTHER = "mbp"  # mac2 풀의 원격 워커
FAKE_TOOL = "rcm-m5t-faketool"  # 어느 PATH 에도 없는 이름
REL_ENV = "kept-on-the-server"  # `rel` 의 env 값 — 원격 워커의 명세 어디에도 없어야 한다


def block(name: str, *lines: str) -> str:
    body = "\n".join(lines)
    return f'\n[[presets]]\nname = "{name}"\nargv = ["sh", "-c", "echo {name}"]\n{body}\n'


GIT = 'source_modes = ["git_ref"]\nrepo = "app"'

#: gate · gateany 는 표식 없음. rel* · localcheck 는 `local_only`.
#: qa(mac2 풀) · qadef(기본 풀)는 표식 없이 로컬 전용 점검을 쓴다
PRESETS = "".join(
    [
        block("gate", "expected_seconds = 600"),
        block("gateany", 'placement = "any-capable"', "expected_seconds = 600"),
        block(
            "rel",
            "local_only = true",
            'pools = ["mac2"]',
            "expected_seconds = 600",
            f'env = {{ RCM_LO_MARK = "{REL_ENV}" }}',
        ),
        block("relany", "local_only = true", 'placement = "any-capable"'),
        block(
            "relneed",
            "local_only = true",
            'placement = "any-capable"',
            f'requires = ["{FAKE_TOOL}"]',
        ),
        block("relq", "local_only = true", 'checks = ["simcheck"]'),
        block(
            "relgated", "local_only = true", 'placement = "any-capable"', 'checks = ["simcheck"]'
        ),
        block("simcheck", GIT),
        block("localcheck", "local_only = true", GIT),
        block("qa", 'pool = "mac2"', 'checks = ["localcheck"]'),
        block("qadef", 'checks = ["localcheck"]'),
        block("relgit", GIT, 'pools = ["mac2"]'),
    ]
)
NAMES = [
    "gate",
    "gateany",
    "rel",
    "relany",
    "relneed",
    "relq",
    "relgated",
    "simcheck",
    "localcheck",
    "qa",
    "qadef",
    "relgit",
]
LOCAL_ONLY = {"rel", "relany", "relneed", "relq", "relgated", "localcheck"}


# ── 도우미 ───────────────────────────────────────────────────────────────────


def report(
    s: M5nServer,
    worker: str,
    *,
    host_key: str = HOST_B,
    accept_any: bool = True,
    xcode: str = "Xc 1",
):
    """등록한 워커의 보고 — 모든 프리셋에 「전부 있음」(옛 워커가 보내던 꼴)."""
    gen = s.registered(worker, pool=s.pools_of[worker])["generation"]
    body = {
        "generation": gen,
        "host_key": host_key,
        "accept_any": accept_any,
        "fingerprint": {"xc": ok_probe(xcode)},
        "presets": {
            n: {"missing_tools": [], "workspace_tools": [], "missing_passthrough": []}
            for n in NAMES
        },
    }
    status, resp = s.req("POST", "/worker/capabilities", token=worker, json_body=body)
    assert status == 200, resp


def server(tmp_path: Path, *, presets: str = PRESETS, **kw: Any) -> M5nServer:
    s = M5nServer(tmp_path, presets=presets, **kw)
    s.pools_of = {REMOTE: "default", OTHER: "mac2", "mac2-worker": "mac2"}
    return s


def ready(s: M5nServer) -> M5nServer:
    """로컬은 이 프로세스로 재고, 기본 풀 원격 `build-02` 가 붙어 보고한 서버."""
    s.app.measure_capabilities()
    report(s, REMOTE)
    return s


@pytest.fixture
def lo(tmp_path):
    s = server(tmp_path)
    yield s
    s.close()


@pytest.fixture
def lo_ready(lo):
    return ready(lo)


def post(
    s: M5nServer,
    preset: str,
    *,
    token: str = "alice",
    machine: str | None = None,
    pool: str | None = None,
    join: bool = False,
    extra: dict[str, Any] | None = None,
):
    source: dict[str, Any]
    if preset in ("simcheck", "localcheck") or preset.startswith("relgit"):
        source = {"mode": "git_ref", "ref": GIT_SHA}
    else:
        source = {
            "mode": "tree",
            "repo": "org/app",
            "branch": "main",
            "base_sha": "abc123f",
            "dirty": True,
            "tree_hash": TREE_HASH,
            "bytes": len(TAR),
        }
    body: dict[str, Any] = {
        "preset": preset,
        "inputs": {},
        "source": source,
        "requester_label": f"{token}@host",
        "join": join,
        **(extra or {}),
    }
    if machine is not None:
        body["machine"] = machine
    if pool is not None:
        body["pool"] = pool
    return s.req("POST", "/jobs", token=token, json_body=body)


def queued(s: M5nServer, preset: str, **kw: Any) -> int:
    status, body = post(s, preset, **kw)
    assert status == 201, body
    jid = int(body["job_id"])
    if body["state"] not in (QUEUED, "scheduled"):
        token = kw.get("token", "alice")
        status, resp = s.req("PUT", f"/jobs/{jid}/tree", token=token, body=TAR)
        assert status == 200, resp
    return jid


def job_count(s: M5nServer) -> int:
    return int(s.store._conn().execute("SELECT count(*) AS n FROM jobs").fetchone()["n"])


def stored_job(s: M5nServer, preset: str, *, target: str | None = None, pool: str = "default"):
    """제출 검사를 거치지 않은 대기 잡 — 표식을 달기 전부터 있던 잡을 흉내 낸다."""
    return s.store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=None,
        source=Source(mode="tree", tree_hash=f"stored-{preset}-{target}-{pool}"),
        requester=Requester(name="alice-laptop", label="alice@host"),
        timeout_seconds=600,
        join_key=None,
        now=s.clock(),
        state=QUEUED,
        pool=pool,
        placement=PLACEMENT_POOL,
        target_worker=target,
    )


def placement(s: M5nServer, jid: int):
    now = s.clock()
    return s.app.placement_for(s.store.get_job(jid), now, s.app.machine_views(now))


def busy_local_lane(s: M5nServer) -> int:
    """로컬 레인 하나를 600초짜리 잡으로 채운다 — 원격이 후보면 ETA 가 0 이 된다."""
    jid = queued(s, "gate")
    now = s.clock()
    job = s.store.claim(1, now, claimant=s.app.local_claimant(now))
    assert job is not None and job.id == jid and job.worker_name is None
    return jid


def eta(s: M5nServer, preset: str) -> dict[str, Any]:
    status, body = s.req("POST", "/api/eta", token="alice", json_body={"preset": preset})
    assert status == 200, body
    return body["job"]


def record_checks(s: M5nServer, monkeypatch) -> list[tuple[str, str]]:
    """점검 잡을 만드는 자리를 가로챈다 — 누가 어디에 걸려 했는지만 남기고 만들지 않는다."""
    calls: list[tuple[str, str]] = []

    def fake(preset, view, *, fingerprint_hash):
        calls.append((preset.name, view.id))
        raise RuntimeError("test: not submitted")

    monkeypatch.setattr(s.app, "submit_internal_check", fake)
    return calls


def write_config(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "rcm.toml"
    p.write_text(f'[server]\ndata_dir = "{tmp_path / "data"}"\n{text}', encoding="utf-8")
    return p


# ── TO1 · TO9 설정 ───────────────────────────────────────────────────────────


def test_to1_local_only_is_read_and_defaults_to_false(tmp_path):
    p = write_config(
        tmp_path,
        block("a", "local_only = true")
        + block("b", "local_only = true", "pools = []")
        + block("c"),
    )
    cfg = load_server_config(p, environ={}, check_tools=False)
    assert cfg.preset("a").local_only is True
    assert cfg.preset("b").local_only is True
    assert cfg.preset("c").local_only is False


def test_to1_local_only_must_be_true_or_false(tmp_path):
    p = write_config(tmp_path, block("a", 'local_only = "yes"'))
    with pytest.raises(ConfigError) as e:
        load_server_config(p, environ={}, check_tools=False)
    assert "local_only" in str(e.value) and "unknown key" not in str(e.value)


def test_to9_local_only_on_a_pool_other_than_the_default_stops_the_server(tmp_path):
    p = write_config(tmp_path, block("a", "local_only = true", 'pool = "mac2"'))
    with pytest.raises(ConfigError) as e:
        load_server_config(p, environ={}, check_tools=False)
    msg = str(e.value)
    assert "'a'" in msg, msg
    assert "local_only" in msg and "default" in msg and "unknown key" not in msg, msg


def test_to9_extra_pools_load_and_their_submission_is_409(lo):
    """`pools` 에 기본 풀이 아닌 항목이 있어도 기동한다 — 그 풀로 낸 제출이 409 다(TO10)."""
    assert lo.cfg.preset("rel").pools == ("mac2",)
    status, body = post(lo, "rel", pool="mac2")
    assert status == 409, body


# ── TO10 · TO13 · TO16 제출 ──────────────────────────────────────────────────


def test_to10_a_local_only_submission_to_another_pool_is_409(lo_ready):
    before = job_count(lo_ready)
    status, body = post(lo_ready, "rel", pool="mac2")
    assert status == 409, body
    assert body["error_code"] == "no_capable_machine"
    assert "local lanes" in body["error"] and "mac2" in body["error"]
    assert job_count(lo_ready) == before  # 잡이 큐에 들지 않는다


SHA_A, SHA_B, SHA_C = ("a" * 40, "b" * 40, "c" * 40)


def submissions(s: M5nServer) -> int:
    return int(s.store._conn().execute("SELECT count(*) AS n FROM submissions").fetchone()["n"])


@pytest.mark.parametrize("marked", [False, True], ids=["control", "marked"])
def test_to13_the_409_comes_before_joining_and_superseding(tmp_path, monkeypatch, marked):
    """합류할 잡과 치울 잡을 둘 다 세워 두고 같은 두 제출을 낸다. 표식이 없으면(control) 앞의
    제출은 합류하고 뒤의 제출은 옛 잡을 치운다 — 대상이 진짜다. 표식이 있으면 둘 다 409 이고
    기존 잡은 그대로다. git_ref 잡은 제출하는 순간 supersede 하므로 그 순서가 보인다."""
    sha = {"now": SHA_A}
    monkeypatch.setattr(server_module, "resolve_ref", lambda *a, **k: sha["now"])
    unmarked = PRESETS + block("relsup", GIT, 'pools = ["mac2"]', 'supersede = "all"')
    s = ready(server(tmp_path, presets=unmarked))

    def submit(at: str, **extra: Any):
        sha["now"] = at
        body = {
            "preset": "relsup",
            "inputs": {},
            "source": {"mode": "git_ref", "ref": "main"},
            "requester_label": "alice@host",
            "pool": "mac2",
            **extra,
        }
        return s.req("POST", "/jobs", token="alice", json_body=body)

    try:
        status, a = submit(SHA_A)
        assert status == 201, a
        status, b = submit(SHA_B, supersede=False)  # 옛 잡 A 를 남긴다 — 치울 대상이다
        assert status == 201, b
        old, target = int(a["job_id"]), int(b["job_id"])
        assert s.store.get_job(old).state == QUEUED
        if marked:
            s.write(
                PROBES
                + unmarked.replace('supersede = "all"', 'supersede = "all"\nlocal_only = true')
            )
            status, body = s.req("POST", "/reload", token="admin", json_body={})
            assert status == 200, body
            assert s.app.config.preset("relsup").local_only is True
        jobs, subs = job_count(s), submissions(s)
        # 합류: 같은 sha 는 B 와 같은 잡이다(합류 키는 풀을 보지 않는다)
        status, joined = submit(SHA_B, join=True)
        # supersede: 같은 브랜치의 새 sha 는 A · B 를 치운다
        status2, newer = submit(SHA_C)
        if not marked:
            assert status == 200 and joined["joined"] is True, joined
            assert joined["job_id"] == target
            assert status2 == 201, newer
            assert s.store.get_job(old).state == "cancelled"
            assert s.store.get_job(target).state == "cancelled"
            return
        assert (status, status2) == (409, 409), (joined, newer)
        assert joined["error_code"] == newer["error_code"] == "no_capable_machine"
        assert job_count(s) == jobs and submissions(s) == subs  # 합류도 새 잡도 없다
        assert s.store.get_job(old).state == QUEUED  # 치워지지 않았다
        assert s.store.get_job(target).state == QUEUED
    finally:
        s.close()


def test_to16_pinning_a_local_only_preset_to_a_remote_worker_is_409(lo_ready):
    before = job_count(lo_ready)
    status, body = post(lo_ready, "rel", token="admin", machine=REMOTE)
    assert status == 409, body
    assert "runs only on this server's local lanes" in body["error"], body
    assert body["error_code"] == "no_capable_machine"
    # 점검 프리셋도 같다 — 아무 토큰이나 지정할 수 있는 점검이어도 원격에는 안 간다
    status, body = post(lo_ready, "localcheck", machine=REMOTE)
    assert status == 409, body
    assert "runs only on this server's local lanes" in body["error"], body
    assert job_count(lo_ready) == before


def test_to16_an_any_capable_local_only_preset_without_local_accept_any_is_409(tmp_path):
    s = ready(server(tmp_path, extra="local_accept_any = false\n"))
    try:
        before = job_count(s)
        status, body = post(s, "relany")
        assert status == 409, body
        assert body["error_code"] == "no_capable_machine"
        assert "local_accept_any" in body["error"], body
        assert job_count(s) == before
    finally:
        s.close()


def test_to16_a_local_only_preset_whose_tool_is_missing_on_the_local_lanes_is_409(lo_ready):
    before = job_count(lo_ready)
    status, body = post(lo_ready, "relneed")
    assert status == 409, body
    assert body["error_code"] == "tool_missing"
    assert FAKE_TOOL in body["error"], body
    assert job_count(lo_ready) == before


def test_to16_a_pool_placed_local_only_preset_whose_tool_is_missing_locally_queues(tmp_path):
    """`tool_missing` 409 는 capable 갈래에만 있다 — pool 배치의 `local_only` 프리셋은 도구가
    빠져도 지금처럼 큐에 들고 `_why_none` 이 이유를 보인다(`local_only_refusal` 의 이른 return)."""
    presets = PRESETS + block("relpoolneed", "local_only = true", f'requires = ["{FAKE_TOOL}"]')
    s = ready(server(tmp_path, presets=presets))
    try:
        before = job_count(s)
        status, body = post(s, "relpoolneed")
        assert status == 201, body
        assert job_count(s) == before + 1
    finally:
        s.close()


def test_to10_an_any_capable_local_only_preset_sent_to_another_pool_is_409(tmp_path):
    """any-capable 프리셋도 `--pool` 을 주면 그 풀의 잡이다 — 기본 풀이 아니면 로컬 레인이 못
    받으니 409 다(`rel` 은 pool 배치로만 그 경우를 본다)."""
    presets = PRESETS + block(
        "relanypool", "local_only = true", 'placement = "any-capable"', 'pools = ["mac2"]'
    )
    s = ready(server(tmp_path, presets=presets))
    try:
        report(s, OTHER, host_key=HOST_C)  # mac2 풀에 산 원격이 있어도 후보가 아니다
        before = job_count(s)
        status, body = post(s, "relanypool", pool="mac2")
        assert status == 409, body
        assert body["error_code"] == "no_capable_machine", body
        assert "mac2" in body["error"], body
        assert job_count(s) == before
        status, body = post(s, "relanypool")  # 비교 행: 풀을 안 주면 any-capable 로 큐에 든다
        assert status == 201, body
    finally:
        s.close()


@pytest.mark.parametrize("report_state", ["unmeasured", "stale"])
@pytest.mark.parametrize("preset", ["relany", "relneed"])
def test_to16_an_unmeasured_or_stale_local_report_is_not_a_409(lo, preset, report_state):
    """6-A-M5 — 409 는 정적인 넷과 `@local` 의 `tool_missing` 뿐이다. `@local` 의 보고가 없거나
    낡아 `eligible_now` 가 비어도 거절하지 않는다(§4.3). 도구가 빠진 `relneed` 도 보고가 낡은
    동안에는 모른다 — 「eligible 이 아니면 409」로 바꾸면 빨개진다."""
    if report_state == "stale":
        lo.app.measure_capabilities()
        lo.clock.advance(3 * lo.app.config.server.probe_interval_seconds + 60)
    before = job_count(lo)
    status, body = post(lo, preset)
    assert status == 201, body
    assert LOCAL not in body["placement"]["eligible_now"], body["placement"]
    codes = {b["worker"]: b["code"] for b in body["placement"]["blocked"]}
    assert codes.get(LOCAL) in ("report_missing", "report_stale"), body["placement"]
    assert job_count(lo) == before + 1


def test_to16_a_local_only_preset_waiting_for_a_local_check_queues_with_the_check_reason(
    lo_ready,
):
    """비교 행 — 점검 판정으로 ineligible 이면 409 가 아니라 큐에 들고 `_why_none` 의 이유다."""
    jid = queued(lo_ready, "relgated")
    assert lo_ready.store.get_job(jid).state == QUEUED
    assert lo_ready.row(jid)["reason"] == "check_pending"
    # 로컬에서 그 점검이 실패하면 check_failed
    status, body = post(lo_ready, "simcheck", machine=LOCAL)
    assert status == 201, body
    lo_ready.store._conn().execute(
        "UPDATE jobs SET state='failed' WHERE id=?", (int(body["job_id"]),)
    )
    lo_ready.app._mark_dirty()
    assert lo_ready.row(jid)["reason"] == "check_failed"
    status, again = post(lo_ready, "relgated")
    assert status == 201, again  # 점검 실패도 409 가 아니다


@pytest.mark.parametrize("case", ["other-pool", "no-local-accept-any"])
def test_to10_to16_rcm_eta_has_no_estimate_when_no_local_lane_can_take_it(tmp_path, case):
    """후보가 빈 `local_only` 프리셋의 `/api/eta` — 원격 레인으로 시각을 지어내지 않고
    `no_capable_machine` 과 ETA null 이다(§4.3 의 새 분기). 비교 행은 같은 자리의 표식 없는
    프리셋이고, 살아 있는 원격이 받으므로 0초다."""
    extra = "local_accept_any = false\n" if case == "no-local-accept-any" else ""
    s = ready(server(tmp_path, extra=extra))
    try:
        report(s, OTHER, host_key=HOST_C)  # mac2 풀에도 살아 있는 원격이 있다
        if case == "other-pool":
            marked, control = (
                {"preset": "rel", "pool": "mac2"},
                {"preset": "relgit", "pool": "mac2"},
            )
        else:
            marked, control = {"preset": "relany"}, {"preset": "gateany"}
        status, doc = s.req("POST", "/api/eta", token="alice", json_body=control)
        assert status == 200, doc
        assert doc["job"]["estimate"]["wait_seconds"] == 0, doc["job"]
        status, doc = s.req("POST", "/api/eta", token="alice", json_body=marked)
        assert status == 200, doc
        # 갈 레인이 없으면 앞선 잡 수도 없다 — 0 은 「바로 선다」로 읽힌다(위임 결정 7)
        assert doc["ahead"] is None, doc
        job = doc["job"]
        assert job["reason"] == "no_capable_machine", job
        assert job["estimate"]["wait_seconds"] is None, job
        assert job["estimate"]["finish_at"] is None, job
    finally:
        s.close()


@pytest.mark.parametrize("preset", ["rel", "relany"])
def test_to10_rcm_eta_ahead_counts_only_jobs_that_contend_for_the_local_lanes(lo_ready, preset):
    """`local_only` 프리셋의 `ahead` 는 후보 로컬 레인을 실제로 다투는 잡만 센다(위임 결정 7).
    원격 워커를 지정한 대기 잡과 산 머신이 없는 다른 풀의 대기 잡은 그 레인에 오지 않으므로
    세지 않는다. 로컬 레인에서 도는 잡과, 로컬 레인도 집을 수 있는 기본 풀 대기 잡은 센다."""
    s = lo_ready
    busy_local_lane(s)  # 셈: 로컬 레인에서 돈다
    stored_job(s, "gate")  # 셈: 기본 풀 — 로컬 레인도 집는다
    stored_job(s, "gate", target=REMOTE)  # 빼기: 원격 지정
    stored_job(s, "gate", pool="mac2")  # 빼기: mac2 풀에는 산 머신이 없다
    status, doc = s.req("POST", "/api/eta", token="alice", json_body={"preset": preset})
    assert status == 200, doc
    assert doc["ahead"] == 2, doc


# ── TO3 · TO7 로컬 레인과 표식 없는 프리셋 ───────────────────────────────────


@pytest.mark.parametrize("preset", ["rel", "relany"])
def test_to3_the_local_lane_takes_a_local_only_job(lo_ready, preset):
    jid = queued(lo_ready, preset)
    assert lo_ready.claim(REMOTE)[0] == 204
    now = lo_ready.clock()
    job = lo_ready.store.claim(1, now, claimant=lo_ready.app.local_claimant(now))
    assert job is not None and job.id == jid
    assert job.worker_name is None


@pytest.mark.parametrize("preset", ["gate", "gateany"])
def test_to7_a_preset_without_local_only_still_goes_to_the_remote_worker(lo_ready, preset):
    jid = queued(lo_ready, preset)
    assert lo_ready.claimed(REMOTE) == jid
    excluded = lo_ready.app.claimant_for(REMOTE, "default", lo_ready.clock()).excluded
    assert preset not in excluded
    spec = {p["name"] for p in lo_ready.spec(REMOTE)[1]["presets"]}
    assert preset in spec


# ── TO15 여러 진입점 ─────────────────────────────────────────────────────────


def _placement_pool(s: M5nServer) -> None:
    place = placement(s, queued(s, "rel"))
    assert REMOTE not in place.workers and None in place.workers


def _placement_capable(s: M5nServer) -> None:
    place = placement(s, queued(s, "relany"))
    assert REMOTE not in place.workers and None in place.workers
    assert REMOTE not in {n.worker for n in place.notes}


def _placement_target(s: M5nServer) -> None:
    place = placement(s, stored_job(s, "rel", target=REMOTE).id)
    assert REMOTE not in place.workers
    assert place.reason == "no_capable_machine"


def _eta_pool(s: M5nServer) -> None:
    busy_local_lane(s)
    assert eta(s, "gate")["estimate"]["wait_seconds"] == 0  # 비교: 표식 없으면 원격이 바로 받는다
    assert eta(s, "rel")["estimate"]["wait_seconds"] > 0


def _eta_capable(s: M5nServer) -> None:
    busy_local_lane(s)
    assert eta(s, "gateany")["estimate"]["wait_seconds"] == 0
    job = eta(s, "relany")
    assert job["estimate"]["wait_seconds"] > 0
    assert REMOTE not in {n["worker"] for n in job["placement_detail"]}


def _eligible_now(s: M5nServer) -> None:
    for preset in ("rel", "relany"):
        status, body = post(s, preset)
        assert status == 201, body
        seen = [
            *body["placement"]["eligible_now"],
            *(b["worker"] for b in body["placement"]["blocked"]),
        ]
        assert REMOTE not in seen, (preset, body["placement"])
        assert LOCAL in body["placement"]["eligible_now"], (preset, body["placement"])


def _auto_check(s: M5nServer, monkeypatch) -> None:
    calls = record_checks(s, monkeypatch)
    s.app.sweep_checks()
    assert ("simcheck", LOCAL) in calls  # 스위퍼는 돈다 — 로컬의 relq 가 simcheck 를 원한다
    assert ("simcheck", REMOTE) not in calls


def _auto_local_only_check(s: M5nServer, monkeypatch) -> None:
    """`checks` 의 점검이 `local_only` 면(qadef → localcheck) 스위퍼는 그 점검을 `@local` 에만 건다.
    원격 `build-02` 는 qadef 를 받을 자리(기본 풀)에 있고 보고도 「다 있음」이다."""
    calls = record_checks(s, monkeypatch)
    s.app.sweep_checks()
    assert ("localcheck", LOCAL) in calls
    assert ("localcheck", REMOTE) not in calls


def _stalled_checks(s: M5nServer) -> None:
    """점검 정체 판정(`_stalled_checks` · `_why_none`) — `checks` 가 있는 `local_only` 잡의 이유와
    `placement_detail` 은 `@local` 만으로 정한다. 원격의 「점검 필요」가 끼어들지 않는다."""
    for preset in ("relq", "relgated"):
        row = s.row(queued(s, preset))
        workers = {n["worker"] for n in row["placement_detail"]}
        assert REMOTE not in workers, (preset, row["placement_detail"])
        assert LOCAL in workers, (preset, row["placement_detail"])
        assert row["reason"] == "check_pending", (preset, row["reason"])


def _could_receive(s: M5nServer) -> None:
    """수신 가능 판정 `_could_receive` — 원격은 `local_only` 프리셋을 받을 자리가 아니다."""
    views = {v.id: v for v in s.app.machine_views(s.clock())}
    for name in sorted(LOCAL_ONLY):
        assert not s.app._could_receive(views[REMOTE], s.app.config.preset(name)), name
    assert s.app._could_receive(views[REMOTE], s.app.config.preset("gate"))  # 비교: 표식 없음


def _recheck(s: M5nServer, monkeypatch) -> None:
    calls = record_checks(s, monkeypatch)
    status, body = s.req(
        "POST", f"/machines/{REMOTE}/recheck", token="admin", json_body={"checks": ["localcheck"]}
    )
    assert status == 409, body
    assert calls == []


def _worker_spec(s: M5nServer) -> None:
    status, spec = s.spec(REMOTE)
    assert status == 200, spec
    names = [p["name"] for p in spec["presets"]]
    assert LOCAL_ONLY.isdisjoint(names), names
    assert {"gate", "gateany", "simcheck", "qa", "qadef", "relgit"} <= set(names)
    # env 도 함께 빠진다 — 값도 이름도 명세 어디에도 없다
    text = json.dumps(spec)
    assert REL_ENV not in text and "RCM_LO_MARK" not in text


def _claim_pool(s: M5nServer) -> None:
    jid = queued(s, "rel")
    assert s.claim(REMOTE)[0] == 204
    assert s.store.get_job(jid).state == QUEUED


def _claim_capable(s: M5nServer) -> None:
    jid = queued(s, "relany")
    assert s.claim(REMOTE)[0] == 204
    assert s.store.get_job(jid).state == QUEUED


def _claim_target(s: M5nServer) -> None:
    jid = stored_job(s, "rel", target=REMOTE).id
    assert s.claim(REMOTE)[0] == 204
    assert s.store.get_job(jid).state == QUEUED


def _claim_excluded(s: M5nServer) -> None:
    now = s.clock()
    assert set(s.app.claimant_for(REMOTE, "default", now).excluded) == LOCAL_ONLY
    assert s.app.local_claimant(now).excluded == ()


def _machines(s: M5nServer) -> None:
    got = s.machines()[REMOTE]["presets"]
    for name in LOCAL_ONLY:
        assert got[name]["state"] != "eligible", (name, got[name])
        assert got[name]["code"] == "local_only", (name, got[name])
    summary = {m["id"]: m for m in s.status()["server"]["machines"]}[REMOTE]
    eligible = {n for n, v in got.items() if v["state"] == "eligible"}
    assert summary["eligible_count"] == len(eligible)
    assert eligible.isdisjoint(LOCAL_ONLY)


ENTRIES: dict[str, Callable[..., None]] = {
    "placement-pool": _placement_pool,
    "placement-capable": _placement_capable,
    "placement-target": _placement_target,
    "eta-pool": _eta_pool,
    "eta-capable": _eta_capable,
    "eligible-now": _eligible_now,
    "auto-check": _auto_check,
    "auto-local-only-check": _auto_local_only_check,
    "stalled-checks": _stalled_checks,
    "could-receive": _could_receive,
    "recheck": _recheck,
    "worker-spec": _worker_spec,
    "claim-pool": _claim_pool,
    "claim-capable": _claim_capable,
    "claim-target": _claim_target,
    "claim-excluded": _claim_excluded,
    "machines": _machines,
}


@pytest.mark.parametrize("entry", list(ENTRIES))
def test_to15_no_entry_point_offers_a_local_only_preset_to_a_remote_machine(
    lo_ready, monkeypatch, entry
):
    # 명세가 이 시험 하나로 묶은 행들(§7 TO 표 「TO15 로 묶었다」). 각 행의 내용은 넷째 판의
    # TO 표(§13 4라운드 · 5라운드 표가 묶은 이유를 적었다)이고, 아래가 그 행을 맡은 진입점이다.
    # TO2 원격 claim 에서 빠진다, capable 잡도 — claim-pool · claim-capable
    #     (`--machine` 원격 지정의 409 는 test_to16_pinning_… 이 맡는다)
    # TO5 `/worker/spec` 에서 env 와 함께 빠진다 — worker-spec(env 값과 이름이 명세에 없다)
    # TO8 대기 계산과 제출 판정 — placement-pool(pool 갈래) · eligible-now(`submission_placement`
    #     와 `eligible_now`) · could-receive(`_could_receive`)
    # TO11 `local_only` 점검은 `@local` 만 겨냥한다 — auto-local-only-check(스위퍼) · recheck(409)
    # TO14 원격 claim 의 네 갈래 — claim-target · claim-pool · claim-capable · claim-excluded,
    #     `known` 갈래와 WHERE 전체는 test_to15_the_claim_sql_… 이 맡는다
    fn = ENTRIES[entry]
    if fn in (_auto_check, _auto_local_only_check, _recheck):
        fn(lo_ready, monkeypatch)
    else:
        fn(lo_ready)


@pytest.mark.parametrize("branch", ["target", "pool", "capable", "known"])
def test_to15_the_claim_sql_drops_excluded_presets_on_every_branch(tmp_path, branch):
    """claim 에 넘기는 제외 목록은 네 갈래 모두에 걸린다 — 제외가 없으면 같은 잡을 집는다."""
    # TO14 네 갈래(지정 · pool · capable · known) 모두, 거르기는 WHERE 전체에 걸린다
    store = Store(tmp_path / "rcm.sqlite3")
    try:
        cols: dict[str, Any] = {
            "target": {"placement": PLACEMENT_POOL, "pool": "p", "target_worker": "w"},
            "pool": {"placement": PLACEMENT_POOL, "pool": "p"},
            "capable": {"placement": PLACEMENT_CAPABLE, "pool": "default"},
            "known": {"placement": PLACEMENT_CAPABLE, "pool": "default"},
        }[branch]
        preset = "gone" if branch == "known" else "rel"
        job = store.create_job(
            preset=preset,
            inputs={},
            key=preset,
            concurrency_group=None,
            source=Source(mode="tree", tree_hash="t"),
            requester=Requester(name="a", label="a"),
            timeout_seconds=60,
            join_key=None,
            now=T0,
            state=QUEUED,
            **cols,
        )
        base = {
            "worker": "w",
            "pool": "p",
            "host_key": None,
            "fingerprint_hash": None,
            "capable": ("rel",),
            "known": ("rel",),
        }
        blocked = Claimant(**base, excluded=(preset,))
        assert store.claim(1, T0, claimant=blocked) is None
        got = store.claim(1, T0, claimant=Claimant(**base))
        assert got is not None and got.id == job.id
    finally:
        store.close()


# ── TO17 reload · 재시작 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("how", ["reload", "restart"])
def test_to17_waiting_jobs_have_no_capable_machine_once_the_preset_is_local_only(tmp_path, how):
    s = ready(server(tmp_path))
    report(s, OTHER, host_key=HOST_C)
    pinned = queued(s, "relgit", token="admin", machine=REMOTE)
    other_pool = queued(s, "relgit", pool="mac2")
    later = queued(s, "relgit", pool="mac2", extra={"start_in_seconds": 600})
    assert s.store.get_job(later).state == "scheduled"
    assert s.row(pinned)["reason"] != "no_capable_machine"  # 표식 전에는 갈 곳이 있다
    marked = PRESETS.replace(
        'name = "relgit"\nargv = ["sh", "-c", "echo relgit"]\n',
        'name = "relgit"\nargv = ["sh", "-c", "echo relgit"]\nlocal_only = true\n',
    )
    assert marked != PRESETS
    if how == "reload":
        s.write(PROBES + marked)
        status, body = s.req("POST", "/reload", token="admin", json_body={})
        assert status == 200, body
    else:
        tokens, clock = s.tokens, s.clock
        s.close()
        s = server(tmp_path, presets=marked, tokens=tokens, clock=clock)
    try:
        for jid in (pinned, other_pool):
            row = s.row(jid)
            assert row["reason"] == "no_capable_machine", row
            assert row["estimate"]["finish_at"] is None, row
            assert row["estimate"]["wait_seconds"] is None, row
            assert s.store.get_job(jid).state == QUEUED  # 실패로 닫지 않는다
        assert s.store.get_job(later).state == "scheduled"
        s.clock.advance(601)
        s.app.tick_schedule()
        assert s.store.get_job(later).state == QUEUED
        assert s.row(later)["reason"] == "no_capable_machine"
    finally:
        s.close()


# ── TO18 · TO19 로컬 전용 점검 ───────────────────────────────────────────────


def test_to18_a_remote_machine_does_not_wait_for_a_check_that_runs_only_locally(lo, monkeypatch):
    report(lo, OTHER, host_key=HOST_C)
    m = lo.machines()[OTHER]
    assert m["presets"]["qa"] == {
        "state": "ineligible",
        "code": "check_local_only",
        "detail": {"check": "localcheck"},
    }
    row = {c["preset"]: c for c in m["checks"]}["localcheck"]
    assert row["error_code"] == "local_only"
    assert row["recheck_needed"] is False  # 다시 걸어도 409 다 — 사람에게 시키지 않는다
    jid = queued(lo, "qa")
    got = lo.row(jid)
    assert got["reason"] == "no_capable_machine", got  # check_pending 이 아니다
    notes = {n["worker"]: n for n in got["placement_detail"]}
    assert notes[OTHER]["code"] == "check_local_only"
    calls = record_checks(lo, monkeypatch)
    lo.app.sweep_checks()
    assert ("localcheck", OTHER) not in calls
    assert lo.claim(OTHER)[0] == 204


def test_to18_a_local_only_check_that_passed_on_a_remote_machine_holds_for_that_fingerprint(
    tmp_path,
):
    """표식을 달기 전에 원격 워커에서 통과한 점검은 지문이 같은 동안 그대로 유효하다 — 그
    머신의 `qa` 는 계속 eligible 이다. 지문이 바뀌면 다시 걸 수 없으니 `check_local_only` 다
    (위임 결정 4 의 추가 문장, `docs/configuration.md` 의 local_only 절)."""
    head = 'name = "localcheck"\nargv = ["sh", "-c", "echo localcheck"]\n'
    unmarked = PRESETS.replace(head + "local_only = true\n", head)
    assert unmarked != PRESETS
    s = server(tmp_path, presets=unmarked)
    try:
        report(s, OTHER, host_key=HOST_C)
        check = queued(s, "localcheck", machine=OTHER)
        assert s.claimed(OTHER) == check
        assert s.finish(OTHER, check, "succeeded", exit_code=0)[0] == 200
        assert s.machines()[OTHER]["presets"]["qa"]["code"] == "ok"
        s.write(PROBES + PRESETS)  # 표식을 단다
        status, body = s.req("POST", "/reload", token="admin", json_body={})
        assert status == 200, body
        assert s.app.config.preset("localcheck").local_only is True
        report(s, OTHER, host_key=HOST_C)  # 같은 지문
        m = s.machines()[OTHER]
        assert (m["presets"]["qa"]["state"], m["presets"]["qa"]["code"]) == ("eligible", "ok")
        row = {c["preset"]: c for c in m["checks"]}["localcheck"]
        assert (row["state"], row["job_id"]) == ("passed", check), row
        report(s, OTHER, host_key=HOST_C, xcode="Xc 2")  # 지문이 바뀌었다
        assert s.machines()[OTHER]["presets"]["qa"] == {
            "state": "ineligible",
            "code": "check_local_only",
            "detail": {"check": "localcheck"},
        }
    finally:
        s.close()


def test_to19_rechecking_a_local_only_check_on_a_remote_machine_is_409(lo, monkeypatch):
    report(lo, OTHER, host_key=HOST_C)
    calls = record_checks(lo, monkeypatch)
    status, body = lo.req(
        "POST", f"/machines/{OTHER}/recheck", token="admin", json_body={"checks": ["localcheck"]}
    )
    assert status == 409, body
    assert "localcheck" in body["error"] and "local lanes" in body["error"], body
    assert calls == []


def test_to19_rechecking_every_check_skips_the_local_only_one(lo, monkeypatch):
    """이름 없이 다시 걸면 걸 수 있는 점검만 건다. 로컬 전용 점검은 실패 항목(`jobs` 의 번호 없는
    항목)이 아니라 `skipped` 에 정책 코드로 남는다 — CLI 와 웹은 번호 없는 `jobs` 항목을 실패로
    부른다(위임 결정 4)."""
    report(lo, OTHER, host_key=HOST_C)
    calls = record_checks(lo, monkeypatch)
    status, body = lo.req("POST", f"/machines/{OTHER}/recheck", token="admin", json_body={})
    assert status == 200, body
    assert [j["preset"] for j in body["jobs"]] == ["simcheck"], body
    assert body["skipped"] == [{"preset": "localcheck", "code": "local_only"}], body
    assert ("simcheck", OTHER) in calls and ("localcheck", OTHER) not in calls
    # `@local` 에서는 건너뛸 것이 없다 — 키는 늘 있고 비어 있다
    lo.app.measure_capabilities()
    status, body = lo.req("POST", f"/machines/{LOCAL}/recheck", token="admin", json_body={})
    assert status == 200, body
    assert sorted(j["preset"] for j in body["jobs"]) == ["localcheck", "simcheck"], body
    assert body["skipped"] == [], body


def test_to19_rechecking_a_machine_whose_only_check_is_local_only_submits_nothing(
    tmp_path, monkeypatch
):
    """건너뛴 것만 있고 낸 것이 없을 때의 모양 — 200, `jobs: []`, `skipped` 에 그 점검(위임 결정
    4). 실패가 아니다: 「걸 수 있는 것을 다 걸었다」가 맞는 답이다."""
    only_local = PRESETS.replace('checks = ["simcheck"]', "expected_seconds = 60")
    assert only_local != PRESETS
    s = server(tmp_path, presets=only_local)
    try:
        assert s.app.check_preset_names() == ["localcheck"]
        report(s, OTHER, host_key=HOST_C)
        calls = record_checks(s, monkeypatch)
        before = job_count(s)
        status, body = s.req("POST", f"/machines/{OTHER}/recheck", token="admin", json_body={})
        assert status == 200, body
        assert body == {"jobs": [], "skipped": [{"preset": "localcheck", "code": "local_only"}]}
        assert calls == [] and job_count(s) == before
    finally:
        s.close()


def test_to19_removing_the_mark_leaves_no_local_only_code_on_the_check(lo, monkeypatch):
    """표식을 뗀 뒤의 점검 칸에 옛 `local_only` 가 남지 않는다. 다음에 걸 때 로그도 옛 원인
    (「after ref_unresolved」)을 지어내지 않는다(P3 1라운드 두 검증자의 LOW)."""
    report(lo, OTHER, host_key=HOST_C)
    record_checks(lo, monkeypatch)
    status, body = lo.req("POST", f"/machines/{OTHER}/recheck", token="admin", json_body={})
    assert status == 200, body
    assert body.get("skipped") == [{"preset": "localcheck", "code": "local_only"}], body
    head = 'name = "localcheck"\nargv = ["sh", "-c", "echo localcheck"]\n'
    unmarked = PRESETS.replace(head + "local_only = true\n", head)
    assert unmarked != PRESETS
    lo.write(PROBES + unmarked)
    status, body = lo.req("POST", "/reload", token="admin", json_body={})
    assert status == 200, body
    assert lo.app.config.preset("localcheck").local_only is False
    row = {c["preset"]: c for c in lo.machines()[OTHER]["checks"]}["localcheck"]
    assert row["error_code"] is None, row
    # 이제 걸린다 — 걸린 점검은 옛 원인을 로그에 남기지 않는다
    logs: list[str] = []
    monkeypatch.setattr(lo.app, "log", logs.append)
    monkeypatch.setattr(
        lo.app,
        "submit_internal_check",
        lambda preset, view, *, fingerprint_hash: stored_job(lo, preset.name, target=view.id),
    )
    status, body = lo.req(
        "POST", f"/machines/{OTHER}/recheck", token="admin", json_body={"checks": ["localcheck"]}
    )
    assert status == 200, body
    assert body["jobs"][0]["job_id"] is not None, body
    assert body["skipped"] == [], body  # 표식을 뗐다 — 이제 건너뛰지 않는다
    assert not [m for m in logs if "ref_unresolved" in m], logs


# ── TO10 · TO16 `rcm run` 의 종료 코드 ───────────────────────────────────────
#
# 원격 금지 409 는 서버가 잡을 만들지 않았다고 말한 것이다 — `--machine` 이 없어도 「결과 모름」(3)
# 이 아니라 거절(2)이다(P3 2라운드 검증자 지적). 진짜 서버에 진짜 CLI 를 붙여 두 경로를 다 본다.

#: CLI 시험용 git_ref 짝 — `rel` · `relany` · `relneed` 와 같은 자리에 선 git_ref 프리셋
CLI_PRESETS = PRESETS + "".join(
    [
        block("relgitpool", "local_only = true", GIT, 'pools = ["mac2"]'),
        block("relgitany", "local_only = true", GIT, 'placement = "any-capable"'),
        block(
            "relgitneed",
            "local_only = true",
            GIT,
            'placement = "any-capable"',
            f'requires = ["{FAKE_TOOL}"]',
        ),
    ]
)


class _CliSnap:
    """tree 경로의 제출까지만 가는 가짜 스냅샷 — 409 는 업로드 전에 온다."""

    repo = "org/app"
    branch = "main"
    base_sha = "abc123f"
    dirty = True
    tree_hash = TREE_HASH
    bytes = len(TAR)
    entries: tuple[Any, ...] = ()

    def __init__(self, tar_path: Path) -> None:
        self.tar_path = tar_path


@pytest.mark.parametrize("path", ["tree", "git_ref"])
@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("other-pool", "no_capable_machine"),
        ("no-local-accept-any", "no_capable_machine"),
        ("tool-missing", "tool_missing"),
    ],
)
def test_to10_to16_rcm_run_exits_2_on_a_local_only_refusal(
    tmp_path, monkeypatch, capsys, path, case, code
):
    from remote_ci_monitor import cli as cli_mod

    monkeypatch.setattr(server_module, "resolve_ref", lambda *a, **k: GIT_SHA)
    extra = "local_accept_any = false\n" if case == "no-local-accept-any" else ""
    s = ready(server(tmp_path, presets=CLI_PRESETS, extra=extra))
    try:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{s.port}")
        monkeypatch.setenv("RCM_TOKEN", s.tokens["alice"])
        names = {
            "tree": {"other-pool": "rel", "no-local-accept-any": "relany"},
            "git_ref": {"other-pool": "relgitpool", "no-local-accept-any": "relgitany"},
        }[path]
        tool = {"tree": "relneed", "git_ref": "relgitneed"}[path]
        preset = names.get(case, tool)
        argv = ["run", preset, "--no-wait"]
        if case == "other-pool":
            argv += ["--pool", "mac2"]
        if path == "git_ref":
            argv += ["--ref", GIT_SHA]
        else:
            monkeypatch.setattr(
                cli_mod, "make_snapshot", lambda *_a, **_k: _CliSnap(tmp_path / "snap.tar")
            )
            (tmp_path / "t").mkdir()
            argv += ["--dir", str(tmp_path / "t")]
        before = job_count(s)
        try:
            rc = cli_mod.main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
        err = capsys.readouterr().err
        # 서버의 409 를 그대로 보인다 — 진짜 서버가 이 코드로 거절했는지도 확인한다
        assert "submit failed" in err, err
        assert "runs only on this server's local lanes" in err, err
        status, body = post(s, preset, pool="mac2" if case == "other-pool" else None)
        assert (status, body["error_code"]) == (409, code), body
        assert job_count(s) == before
        assert rc == 2, err  # 잡이 안 생긴 확실한 거절 — 결과 모름(3)이 아니다
    finally:
        s.close()


# ── 문서 ─────────────────────────────────────────────────────────────────────


def test_to1_the_configuration_guide_and_the_changelog_name_local_only():
    """사용자가 보는 키다 — 설정 문서의 절과 키 표, CHANGELOG 에 있어야 한다(docs 스킬)."""
    from test_docs_m5 import CHANGELOG, CONFIGURATION, read, unreleased

    text = read(CONFIGURATION)
    assert "### Keeping a preset on the server: `local_only`" in text
    assert "| `[[presets]]` | `local_only` | `false` |" in text
    for code in ("`local_only`", "`check_local_only`", "409"):
        assert code in text, code
    # 판정 표 밑의 문장은 코드대로다 — local_only 는 표보다 먼저, check_local_only 는
    # check_needed 자리에만 선다(`capabilities.py` 의 `_verdict`, P3 1라운드 검증자 2)
    flat = " ".join(text.split())
    assert "`local_only` (ineligible) is shown first, before any row of this table" in flat
    assert "replaces `check_needed` on a remote worker" in flat
    assert "shown before any of these" not in flat
    # 원격에서 이미 통과한 로컬 전용 점검은 지문이 같은 동안 유효하다(위임 결정 4 의 추가)
    assert "keeps counting there until that machine's fingerprint changes" in flat
    # 원격 금지 409 는 `rcm run` 의 2 다 · `/api/eta` 의 `ahead` 가 null 일 수 있다(P3 2라운드)
    assert "`rcm run` prints the server's reason and exits 2, since no job was made" in flat
    assert "`POST /api/eta` answers `ahead: null`" in flat
    changelog = " ".join(unreleased(read(CHANGELOG)).split())
    assert "local_only = true" in changelog
    assert "`rcm run` exits 2 on these refusals" in changelog
    assert "`ahead: null`" in changelog


def test_to10_to16_the_readme_exit_code_paragraph_names_the_local_only_409s():
    """README 두 판의 종료 코드 문단이 `rcm run` 의 2 를 고르는 409 를 다 적는다. `local_only`
    거절의 `no_capable_machine` · `tool_missing` 은 `cli.py` 의 `_REFUSED_CODES` 에 있어 2 다(P3
    2라운드). M5s 가 잠근 구절(`tests/test_docs_m5s.py`)은 그대로 남는다."""
    from test_docs_m5 import README, README_KO, read

    en = " ".join(read(README).split())
    assert (
        "a 409 whose code is `schedule_exists` or `release_route_required`, and a 409 "
        "`no_capable_machine` or `tool_missing` for a "
        "[`local_only`](docs/configuration.md#keeping-a-preset-on-the-server-local_only) preset"
    ) in en
    assert "any other 409 is 3" in en
    ko = " ".join(read(README_KO).split())
    assert (
        "`schedule_exists` · `release_route_required` 인 409 와, "
        "[`local_only`](docs/configuration.md#keeping-a-preset-on-the-server-local_only) "
        "프리셋을 서버 레인이 받을 수 없을 때의 409 `no_capable_machine` · `tool_missing` 이 그렇다"
    ) in ko
    assert "그 밖의 409 는 작업이 생겼는지 알 수 없으므로 3 이다" in ko
