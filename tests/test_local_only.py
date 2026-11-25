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

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

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


def block(name: str, *lines: str) -> str:
    body = "\n".join(lines)
    return f'\n[[presets]]\nname = "{name}"\nargv = ["sh", "-c", "echo {name}"]\n{body}\n'


GIT = 'source_modes = ["git_ref"]\nrepo = "app"'

#: gate · gateany 는 표식 없음. rel* · localcheck 는 `local_only`.
#: qa 는 표식 없이 로컬 전용 점검을 쓴다
PRESETS = "".join(
    [
        block("gate", "expected_seconds = 600"),
        block("gateany", 'placement = "any-capable"', "expected_seconds = 600"),
        block("rel", "local_only = true", 'pools = ["mac2"]', "expected_seconds = 600"),
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
    "relgit",
]
LOCAL_ONLY = {"rel", "relany", "relneed", "relq", "relgated", "localcheck"}


# ── 도우미 ───────────────────────────────────────────────────────────────────


def report(s: M5nServer, worker: str, *, host_key: str = HOST_B, accept_any: bool = True):
    """등록한 워커의 보고 — 모든 프리셋에 「전부 있음」(옛 워커가 보내던 꼴)."""
    gen = s.registered(worker, pool=s.pools_of[worker])["generation"]
    body = {
        "generation": gen,
        "host_key": host_key,
        "accept_any": accept_any,
        "fingerprint": {"xc": ok_probe("Xc 1")},
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
    if preset in ("simcheck", "localcheck", "relgit"):
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


def test_to13_the_409_comes_before_joining_and_superseding(tmp_path):
    """합류할 같은 트리 잡과 치울 같은 브랜치 잡이 있어도 409 가 먼저다 — 기존 잡은 그대로다."""
    presets = PRESETS.replace(
        'name = "rel"\nargv = ["sh", "-c", "echo rel"]\n',
        'name = "rel"\nargv = ["sh", "-c", "echo rel"]\nsupersede = "all"\n',
    )
    assert 'supersede = "all"' in presets
    s = ready(server(tmp_path, presets=presets))
    try:
        first = queued(s, "rel", join=True)
        subs = s.store._conn().execute("SELECT count(*) AS n FROM submissions").fetchone()["n"]
        status, body = post(s, "rel", pool="mac2", join=True)
        assert status == 409, body
        assert body.get("joined") is None and "job_id" not in body
        job = s.store.get_job(first)
        assert job.state == QUEUED  # 치워지지 않았다
        after = s.store._conn().execute("SELECT count(*) AS n FROM submissions").fetchone()["n"]
        assert after == subs  # 합류하지 않았다
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
    assert {"gate", "gateany", "simcheck", "qa", "relgit"} <= set(names)


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
    fn = ENTRIES[entry]
    if fn in (_auto_check, _recheck):
        fn(lo_ready, monkeypatch)
    else:
        fn(lo_ready)


@pytest.mark.parametrize("branch", ["target", "pool", "capable", "known"])
def test_to15_the_claim_sql_drops_excluded_presets_on_every_branch(tmp_path, branch):
    """claim 에 넘기는 제외 목록은 네 갈래 모두에 걸린다 — 제외가 없으면 같은 잡을 집는다."""
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
    """이름 없이 다시 걸면 걸 수 있는 점검만 건다 — 로컬 전용 점검은 이유 코드로 남긴다."""
    report(lo, OTHER, host_key=HOST_C)
    calls = record_checks(lo, monkeypatch)
    status, body = lo.req("POST", f"/machines/{OTHER}/recheck", token="admin", json_body={})
    assert status == 200, body
    items = {j["preset"]: j for j in body["jobs"]}
    assert items["localcheck"] == {
        "preset": "localcheck",
        "job_id": None,
        "error_code": "local_only",
    }
    assert ("simcheck", OTHER) in calls and ("localcheck", OTHER) not in calls
