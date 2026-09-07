"""서버(M5b-1) — 프리셋 `pool`/`pools` 설정 · `POST /jobs` 의 `pool`(허용 풀 검증 · 400) ·
`/api/status` 의 풀별 `pools[]`(기본 풀 항상 먼저 · 원격 풀은 lanes 0 · hosts [] · `worker_down`) ·
`POST /api/eta` 의 `pool` · `GET /jobs/{id}` 의 `pool` · 로컬 워커는 다른 풀의 잡을 잡지 않는다 ·
`rcm top --json` 모양. 명세는 docs/m5-workplan.md 「M5b. 원격 워커」 · 순서 3(M5b-1).
구현 전이라 빨간 것이 정상이다.

test_server.Server(in-process HTTP, 워커 off) 를 쓰고 프리셋만 바꿔 단다(test_server_m5 와 같다).
M5b-1 에는 원격 워커가 없다 — 리눅스 풀의 잡은 아무도 못 돌리므로 `worker_down` 이어야 한다
(fail-open 금지).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.cli import main
from remote_ci_monitor.config import ConfigError, load_server_config, parse_preset
from remote_ci_monitor.core.model import SUCCEEDED
from test_server import PRESETS, TAR, TREE_HASH, Server, sh
from test_status_schema import POOL_KEYS

OTHER_TREE = "ab" * 32
THIRD_TREE = "cd" * 32

#: `ok`(기본 풀) · `lin`(기본 linux, 세션이 default 도 고를 수 있다) · `strict`(linux 만).
POOL_PRESETS = [
    *PRESETS,
    sh("lin", "echo linux", pool="linux", pools=["default"]),
    sh("strict", "echo linux only", pool="linux"),
]


# ── 도우미 ───────────────────────────────────────────────────────────────────


def pool_server(tmp_path: Path, *, workers: bool = False) -> Server:
    s = Server(tmp_path, workers=workers)
    try:
        s.cfg.presets = tuple(parse_preset(p) for p in POOL_PRESETS)
    except Exception:
        s.close()
        raise
    return s


def submit(
    srv: Server,
    token: str = "alice",
    *,
    preset: str = "ok",
    tree_hash: str = TREE_HASH,
    pool: Any = None,
    join: bool | None = None,
) -> tuple[int, Any]:
    body: dict[str, Any] = {
        "preset": preset,
        "inputs": {},
        "source": {
            "mode": "tree",
            "repo": "org/app",
            "base_sha": "abc123f",
            "dirty": True,
            "tree_hash": tree_hash,
            "bytes": len(TAR),
        },
        "requester_label": f"{token}@host",
    }
    if pool is not None:
        body["pool"] = pool
    if join is not None:
        body["join"] = join
    return srv.req("POST", "/jobs", token=token, json_body=body)


def new_job(srv: Server, token: str = "alice", **kw: Any) -> int:
    status, body = submit(srv, token, **kw)
    assert status == 201, body
    return int(body["job_id"])


def view(srv: Server, jid: int, token: str | None = None) -> dict[str, Any]:
    status, body = srv.req("GET", f"/jobs/{jid}", token=token)
    assert status == 200, body
    return body


def status_doc(srv: Server) -> dict[str, Any]:
    status, body = srv.req("GET", "/api/status")
    assert status == 200, body
    return body


def pools_by_name(srv: Server) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in status_doc(srv)["pools"]}


def eta(srv: Server, preset: str, *, pool: Any = None) -> tuple[int, Any]:
    body: dict[str, Any] = {"preset": preset, "inputs": {}}
    if pool is not None:
        body["pool"] = pool
    return srv.req("POST", "/api/eta", json_body=body)


def finish_via_store(srv: Server, jid: int, *, pool: str, seconds: float) -> None:
    """워커 없이 잡을 끝낸다 — 그 풀에서 claim 한 뒤 `seconds` 뒤에 성공으로. 표본용."""
    t0 = datetime.now(UTC) - timedelta(seconds=seconds + 5)
    claimed = srv.store.claim(1, t0, pool=pool)
    assert claimed is not None and claimed.id == jid, (claimed, jid)
    assert srv.store.finish(jid, SUCCEEDED, now=t0 + timedelta(seconds=seconds), exit_code=0)
    time.sleep(0.3)  # 상태 스냅샷 TTL(0.2초)을 넘긴다 — 저장소를 직접 만졌으니 dirty 표시가 없다


@pytest.fixture
def srv(tmp_path):
    s = pool_server(tmp_path)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = pool_server(tmp_path, workers=True)
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_m5.env 와 같다 — `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


# ── 설정: 프리셋 pool · pools ─────────────────────────────────────────────────


def test_parse_preset_pool_and_pools_default_and_parse():
    plain = parse_preset({"name": "ok", "argv": ["x"]})
    assert plain.pool == "default" and plain.pools == ()
    lin = parse_preset({"name": "lin", "argv": ["x"], "pool": "linux", "pools": ["default"]})
    assert lin.pool == "linux" and lin.pools == ("default",)
    strict = parse_preset({"name": "strict", "argv": ["x"], "pool": "linux"})
    assert strict.pool == "linux" and strict.pools == ()
    many = parse_preset({"name": "m", "argv": ["x"], "pools": ["linux", "mac2"]})
    assert many.pool == "default" and many.pools == ("linux", "mac2")


@pytest.mark.parametrize(
    "extra",
    [
        {"pool": ""},
        {"pool": "bad pool"},
        {"pool": "-linux"},
        {"pool": 5},
        {"pool": ["linux"]},
        {"pools": "default"},
        {"pools": [1]},
        {"pools": ["ok", "bad name"]},
        {"pools": [""]},
    ],
)
def test_parse_preset_rejects_bad_pool_names_naming_the_key(extra):
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "lin", "argv": ["x"], **extra})
    msg = str(e.value)
    assert "preset 'lin'" in msg and "pool" in msg
    assert "unknown key" not in msg  # 키를 모르는 게 아니라 값이 틀린 것이어야 한다


def test_server_config_file_accepts_pool_keys(tmp_path):
    text = """
[[presets]]
name = "lin"
argv = ["sh", "-c", "echo linux"]
pool = "linux"
pools = ["default"]

[[presets]]
name = "ok"
argv = ["sh", "-c", "echo ok"]
"""
    path = tmp_path / "rcm.toml"
    path.write_text(text)
    cfg = load_server_config(path, environ={})
    assert cfg.preset("lin").pool == "linux" and cfg.preset("lin").pools == ("default",)
    assert cfg.preset("ok").pool == "default" and cfg.preset("ok").pools == ()


# ── POST /jobs 의 pool ────────────────────────────────────────────────────────


def test_submit_uses_the_preset_pool_when_pool_is_omitted(srv):
    lin = new_job(srv, "alice", preset="lin")
    ok = new_job(srv, "bob", preset="ok", tree_hash=OTHER_TREE)
    strict = new_job(srv, "admin", preset="strict", tree_hash=THIRD_TREE)
    assert view(srv, lin)["pool"] == "linux"
    assert view(srv, ok)["pool"] == "default"
    assert view(srv, strict)["pool"] == "linux"
    assert srv.store.get_job(lin).pool == "linux" and srv.store.get_job(ok).pool == "default"


def test_submit_accepts_a_pool_the_preset_allows(srv):
    lin_default = new_job(srv, "alice", preset="lin", pool="default")
    lin_linux = new_job(srv, "bob", preset="lin", pool="linux", tree_hash=OTHER_TREE)
    ok_default = new_job(srv, "admin", preset="ok", pool="default", tree_hash=THIRD_TREE)
    assert view(srv, lin_default)["pool"] == "default"
    assert view(srv, lin_linux)["pool"] == "linux"
    assert view(srv, ok_default)["pool"] == "default"
    assert srv.store.get_job(lin_default).pool == "default"


def test_submit_rejects_pools_the_preset_does_not_allow_naming_the_allowed_ones(srv):
    status, body = submit(srv, "alice", preset="strict", pool="default")
    assert status == 400 and set(body) == {"error"}, body
    assert "linux" in body["error"] and "pool" in body["error"]
    status, body = submit(srv, "admin", preset="strict", pool="default")  # admin 도 예외 없다
    assert status == 400
    status, body = submit(srv, "alice", preset="lin", pool="windows")
    assert status == 400 and "linux" in body["error"] and "default" in body["error"], body
    status, body = submit(srv, "alice", preset="ok", pool="linux")
    assert status == 400 and "default" in body["error"], body
    assert srv.store.list_active() == []  # 잡을 만들지 않았다
    assert status_doc(srv)["pools"][0]["queue"] == []


@pytest.mark.parametrize("bad", [5, True, ["linux"], {"name": "linux"}, "", "bad pool", "-x"])
def test_submit_rejects_non_string_or_malformed_pool_values(srv, bad):
    status, body = submit(srv, "alice", preset="lin", pool=bad)
    assert status == 400 and "pool" in body["error"], (bad, body)
    assert srv.store.list_active() == []


# ── /api/status 의 pools[] ────────────────────────────────────────────────────


def test_status_lists_every_pool_with_its_own_rows_and_positions(srv):
    ok = new_job(srv, "alice", preset="ok")
    lin = new_job(srv, "bob", preset="lin", tree_hash=OTHER_TREE)
    lin2 = new_job(srv, "admin", preset="strict", tree_hash=THIRD_TREE)
    doc = status_doc(srv)
    assert doc["schema_version"] == 1
    assert [p["name"] for p in doc["pools"]] == ["default", "linux"]
    default, linux = doc["pools"]
    assert set(default) == POOL_KEYS and set(linux) == POOL_KEYS
    assert [(r["id"], r["pool"], r["position"]) for r in default["queue"]] == [(ok, "default", 1)]
    assert [(r["id"], r["pool"], r["position"]) for r in linux["queue"]] == [
        (lin, "linux", 1),
        (lin2, "linux", 2),
    ]
    # 로컬 레인은 server 와 기본 풀에만 — 원격 풀은 아직 워커가 없다
    assert doc["server"]["lanes"] == 1 and len(doc["server"]["workers"]) == 1
    assert default["lanes"] == 1 and linux["lanes"] == 0
    assert linux["hosts"] == [] and linux["hosts_error"] is None
    assert linux["queue_error"] is None and linux["recent"] == [] and linux["medians"] == {}
    assert linux["recent_count"] == default["recent_count"]


def test_status_keeps_the_default_pool_first_even_when_it_has_no_jobs(srv):
    lin = new_job(srv, "alice", preset="lin")
    doc = status_doc(srv)
    assert [p["name"] for p in doc["pools"]] == ["default", "linux"]
    assert doc["pools"][0]["queue"] == [] and doc["pools"][0]["lanes"] == 1
    assert [r["id"] for r in doc["pools"][1]["queue"]] == [lin]


def test_status_with_only_default_pool_jobs_is_a_single_pool(srv):
    new_job(srv, "alice", preset="ok")
    doc = status_doc(srv)
    assert [p["name"] for p in doc["pools"]] == ["default"]  # 풀 하나일 때 화면은 그대로
    assert doc["pools"][0]["queue"][0]["pool"] == "default"
    assert set(doc["pools"][0]["queue"][0]) >= {"pool", "position", "reason", "estimate"}


def test_queued_job_in_a_pool_without_workers_is_worker_down_with_null_eta(srv):
    lin = new_job(srv, "alice", preset="lin")
    ok = new_job(srv, "bob", preset="ok", tree_hash=OTHER_TREE)
    assert srv.upload(lin, token="alice")[0] == 200
    assert srv.upload(ok, token="bob")[0] == 200
    pools = pools_by_name(srv)
    (row,) = pools["linux"]["queue"]
    assert row["id"] == lin and row["state"] == "queued" and row["pool"] == "linux"
    assert row["reason"] == "worker_down" and row["position"] == 1
    assert row["estimate"]["finish_at"] is None and row["estimate"]["wait_seconds"] is None
    assert row["estimate"]["expected_seconds"] is not None  # 기대치는 안다 — 시각만 안 준다
    (drow,) = pools["default"]["queue"]
    assert drow["id"] == ok and drow["state"] == "queued"
    assert drow["reason"] in ("waiting_for_lane", "not_scheduled")  # 기본 풀은 평소 이유
    assert drow["estimate"]["finish_at"] is not None and drow["estimate"]["wait_seconds"] == 0
    # 잡 조회도 같은 계산
    v = view(srv, lin)
    assert v["reason"] == "worker_down" and v["estimate"]["finish_at"] is None


def test_uploading_job_in_a_remote_pool_keeps_the_uploading_reason(srv):
    new_job(srv, "alice", preset="lin")
    (row,) = pools_by_name(srv)["linux"]["queue"]
    assert row["state"] == "uploading" and row["reason"] == "uploading"  # 업로드가 먼저다
    assert row["estimate"]["finish_at"] is None and row["position"] == 1


def test_local_worker_never_claims_jobs_of_another_pool(live):
    lin = new_job(live, "alice", preset="lin")
    assert live.upload(lin, token="alice")[0] == 200
    ok = new_job(live, "bob", preset="ok", tree_hash=OTHER_TREE)
    assert live.upload(ok, token="bob")[0] == 200
    assert live.wait_terminal(ok).state == "succeeded"  # 기본 풀 잡은 그 사이 돌아 끝난다
    time.sleep(1.0)
    j = live.store.get_job(lin)
    assert j.state == "queued" and j.started_at is None and j.lane is None
    doc = status_doc(live)
    (row,) = pools_by_name(live)["linux"]["queue"]
    assert row["id"] == lin and row["reason"] == "worker_down"
    assert doc["server"]["workers"][0]["state"] == "idle"  # 로컬 레인은 놀고 있어도 안 잡는다
    assert [r["id"] for r in pools_by_name(live)["default"]["recent"]] == [ok]


# ── 최근 완료 · 중앙값은 풀별 ─────────────────────────────────────────────────


def test_finished_jobs_and_medians_land_in_their_own_pool(srv):
    lin_a = new_job(srv, "alice", preset="lin")
    assert srv.upload(lin_a, token="alice")[0] == 200
    finish_via_store(srv, lin_a, pool="linux", seconds=60)
    lin_b = new_job(srv, "alice", preset="lin", tree_hash=OTHER_TREE)
    assert srv.upload(lin_b, token="alice")[0] == 200
    finish_via_store(srv, lin_b, pool="linux", seconds=100)
    d_a = new_job(srv, "bob", preset="lin", pool="default", tree_hash=THIRD_TREE)
    assert srv.upload(d_a, token="bob")[0] == 200
    finish_via_store(srv, d_a, pool="default", seconds=200)
    d_b = new_job(srv, "bob", preset="lin", pool="default", tree_hash="ef" * 32)
    assert srv.upload(d_b, token="bob")[0] == 200
    finish_via_store(srv, d_b, pool="default", seconds=200)
    view(srv, d_b)  # 잡 조회는 스냅샷을 dirty 로 표시한다
    pools = pools_by_name(srv)
    assert [r["id"] for r in pools["linux"]["recent"]] == [lin_b, lin_a]
    assert [r["id"] for r in pools["default"]["recent"]] == [d_b, d_a]
    assert all(r["pool"] == "linux" for r in pools["linux"]["recent"])
    assert all(r["pool"] == "default" for r in pools["default"]["recent"])
    # 같은 키(`lin`) 라도 풀마다 중앙값이 다르다
    assert pools["linux"]["medians"]["lin"]["seconds"] == 80
    assert pools["linux"]["medians"]["lin"]["sample_count"] == 2
    assert pools["default"]["medians"]["lin"]["seconds"] == 200
    assert pools["default"]["medians"]["lin"]["sample_count"] == 2


def test_a_pool_with_only_finished_jobs_stays_listed(srv):
    lin = new_job(srv, "alice", preset="lin")
    assert srv.req("POST", f"/jobs/{lin}/cancel", token="alice", json_body={})[0] == 200
    pools = pools_by_name(srv)
    assert list(pools) == ["default", "linux"]
    assert pools["linux"]["queue"] == []
    assert [(r["id"], r["state"], r["pool"]) for r in pools["linux"]["recent"]] == [
        (lin, "cancelled", "linux")
    ]
    assert pools["default"]["recent"] == []


# ── POST /api/eta 의 pool ─────────────────────────────────────────────────────


def test_eta_counts_only_the_requested_pool(srv):
    a = new_job(srv, "alice", preset="ok")
    b = new_job(srv, "bob", preset="ok", tree_hash=OTHER_TREE)
    assert [r["id"] for r in pools_by_name(srv)["default"]["queue"]] == [a, b]
    status, doc = eta(srv, "lin")  # 프리셋 기본 풀 = linux, 비어 있다
    assert status == 200, doc
    assert doc["job"]["pool"] == "linux" and doc["job"]["position"] == 1 and doc["ahead"] == 0
    assert doc["job"]["reason"] == "worker_down"  # 아직 워커가 없는 풀
    assert doc["job"]["estimate"]["finish_at"] is None
    assert doc["job"]["id"] is None and doc["job"]["url"] is None
    status, doc = eta(srv, "lin", pool="default")
    assert status == 200 and doc["job"]["pool"] == "default"
    assert doc["job"]["position"] == 3 and doc["ahead"] == 2
    assert doc["job"]["estimate"]["finish_at"] is not None
    status, doc = eta(srv, "ok")
    assert status == 200 and doc["job"]["pool"] == "default" and doc["job"]["position"] == 3


def test_eta_validates_pool_like_submit(srv):
    status, body = eta(srv, "strict", pool="default")
    assert status == 400 and "linux" in body["error"], body
    status, body = eta(srv, "lin", pool="windows")
    assert status == 400 and "pool" in body["error"], body
    status, body = eta(srv, "ok", pool=5)
    assert status == 400 and "pool" in body["error"], body
    status, body = eta(srv, "strict")  # 생략 = 프리셋 기본 풀
    assert status == 200 and body["job"]["pool"] == "linux"


# ── GET /jobs/{id} 의 pool ────────────────────────────────────────────────────


def test_job_view_carries_pool_for_waiting_running_and_finished_jobs(srv):
    lin = new_job(srv, "alice", preset="lin")
    assert view(srv, lin)["pool"] == "linux"  # uploading
    assert srv.upload(lin, token="alice")[0] == 200
    assert view(srv, lin)["pool"] == "linux"  # queued
    claimed = srv.store.claim(1, datetime.now(UTC), pool="linux")
    assert claimed is not None and claimed.id == lin
    v = view(srv, lin, token="alice")
    assert v["state"] == "running" and v["pool"] == "linux" and v["position"] is None
    assert srv.store.finish(lin, SUCCEEDED, now=datetime.now(UTC), exit_code=0)
    v = view(srv, lin)
    assert v["state"] == "succeeded" and v["pool"] == "linux"  # 최근 완료 모양에도 pool
    assert "transitions" in v and "position" not in v


# ── rcm top --json 의 모양 ────────────────────────────────────────────────────


def test_top_json_keeps_pools_zero_as_the_default_pool(srv, env, capsys):
    lin = new_job(srv, "alice", preset="lin")
    env(srv, token=None)  # 읽기는 토큰이 필요 없다
    code = main(["top", "--json"])
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert code == 0 and doc["schema_version"] == 1
    assert doc["pools"][0]["name"] == "default" and doc["pools"][0]["queue"] == []
    assert [p["name"] for p in doc["pools"]] == ["default", "linux"]
    assert [r["id"] for r in doc["pools"][1]["queue"]] == [lin]
    assert doc["pools"][1]["queue"][0]["pool"] == "linux"
    code = main(["top"])  # 텍스트 렌더도 두 풀에서 죽지 않는다
    text = capsys.readouterr().out
    assert code == 0 and f"#{lin}" in text
