"""M5n 서버 · 프로토콜(역할 B) — 세대 · `/worker/spec` · `/worker/capabilities` ·
`/api/machines` · `/api/status` 의 새 키 · `POST /jobs` 의 `machine` 과 `placement` ·
claim 경로의 판정 · 호스트별 중앙값.

명세는 docs/m5n-workplan.md §4.2 · §4.5 · §5.2 · §5.3 · §7.1 · §8.1 · §11.3 · §11.4 · §14.1.
시나리오 표는 docs/m5n-test-scenarios-b.md. 구현 전이라 빨간 것이 정상이다 — 설정 파일의
`[[probes]]` · `placement` · `checks` 를 아직 못 읽어서 대부분 픽스처에서 `ConfigError` 로 선다.

- 서버는 `tests/test_worker_api.py` 의 `WorkerServer` 를 물려받는다(in-process HTTP · 주입 시계 ·
  로컬 워커 스레드 없음 · `admission = "always"`). 다른 점 하나: 설정을 **TOML 파일**에서 읽는다 —
  `[[probes]]` 와 새 프리셋 키를 그대로 쓰고, `POST /reload` 로 세대를 올릴 수 있게.
- 워커 토큰 `build-02`(풀 mac2 · 호스트 HOST_B) · `mac2-worker` · `mbp`. 워커 이름 = 토큰 이름.
- 아직 없는 이름(`core.capability.*`)은 테스트 **안에서** import 한다.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import load_server_config
from remote_ci_monitor.core.model import QUEUED
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.server import App, make_server
from remote_ci_monitor.store import Store
from test_server import TAR, TREE_HASH
from test_worker_api import T0, Clock, WorkerServer

PY = sys.executable
LOCAL = "@local"
HOST_A = "a1f3c9d2e4b5a6f7"
HOST_B = "7c20aa11bb22cc33"
HOST_C = "5e5e000011112222"
GIT_SHA = "0123456789abcdef0123456789abcdef01234567"  # 40 hex — 서버가 원격을 안 부른다
FAKE_TOOL = "rcm-m5n-faketool"  # 어느 PATH 에도 없는 이름

HEAD = """\
[server]
data_dir = "{data_dir}"
lanes = 1
grace_seconds = 10
max_snapshot_bytes = 10000
snapshot_cache = true
admission = "always"
worker_timeout_seconds = 60
worker_heartbeat_seconds = 5
worker_claim_wait_seconds = 20
{extra}
[[repos]]
name = "app"
url = "{repo_url}"
"""

PROBES = f"""
[[probes]]
name = "xc"
argv = ["{PY}", "-c", "print('Xc 1')"]
"""

#: ok · needtool(풀) · capgate · anywhere(any-capable) · simcheck(점검) · scenario(checks)
PRESETS = f"""
[[presets]]
name = "ok"
argv = ["sh", "-c", "echo ok"]

[[presets]]
name = "needtool"
pool = "mac2"
requires = ["{FAKE_TOOL}"]
argv = ["sh", "-c", "{FAKE_TOOL}"]

[[presets]]
name = "capgate"
placement = "any-capable"
requires = ["{FAKE_TOOL}"]
argv = ["sh", "-c", "{FAKE_TOOL}"]

[[presets]]
name = "anywhere"
placement = "any-capable"
argv = ["sh", "-c", "echo anywhere"]

[[presets]]
name = "simcheck"
source_modes = ["git_ref"]
repo = "app"
argv = ["sh", "-c", "echo check"]

[[presets]]
name = "scenario"
pool = "mac2"
checks = ["simcheck"]
requires = ["{FAKE_TOOL}"]
argv = ["sh", "-c", "echo qa"]
"""
#: reload 시나리오가 붙이는 프리셋 하나 — 파일이 실제로 바뀌어야 「다시 읽었다」가 확실하다
LATE = '\n[[presets]]\nname = "late"\nargv = ["true"]\n'
PRESET_NAMES = ["ok", "needtool", "capgate", "anywhere", "simcheck", "scenario"]
WORKERS = ("build-02", "mac2-worker", "mbp")


# ── 서버 ─────────────────────────────────────────────────────────────────────


class M5nServer(WorkerServer):
    """`WorkerServer` 와 같은 모양 — 설정만 TOML 파일에서 읽는다. `write()` 로 파일을 고친다."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        workers: bool = False,
        extra: str = "",
        probes: str = PROBES,
        presets: str = PRESETS,
        repo_url: str | None = None,
        tokens: dict[str, str] | None = None,
        clock: Clock | None = None,
    ):
        import threading

        self.tmp = tmp_path
        self.config_path = tmp_path / "server.toml"
        self.head = HEAD.format(
            data_dir=tmp_path / "data",
            extra=extra,
            repo_url=repo_url or (tmp_path / "nowhere.git"),
        )
        self.write(probes + presets)
        cfg = load_server_config(self.config_path)
        self.cfg = cfg
        self.clock = clock or Clock()
        self.store = Store(cfg.data_dir / "rcm.sqlite3")
        if tokens is None:
            self.tokens = {
                "alice": self.store.add_token("alice-laptop", admin=False, now=T0),
                "bob": self.store.add_token("bob-desk", admin=False, now=T0),
                "admin": self.store.add_token("macmini-admin", admin=True, now=T0),
            }
            for name in WORKERS:
                self.worker_token(name)
        else:
            self.tokens = dict(tokens)  # 같은 data dir 로 다시 띄울 때(재시작 시나리오)
        self.app = App(cfg, self.store, now_fn=self.clock)
        self.httpd = make_server(self.app, bind="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        )
        self.thread.start()
        if workers:
            self.app.start()

    def write(self, body: str) -> None:
        self.config_path.write_text(self.head + body, encoding="utf-8")

    # ── 워커 쪽 ──

    def generation(self, worker: str = "build-02", **kw: Any) -> str:
        """등록하고 응답의 세대를 돌려준다."""
        kw.setdefault("pool", "mac2")
        return self.registered(worker, **kw)["generation"]

    def report(
        self,
        worker: str,
        generation: str,
        *,
        host_key: str = HOST_B,
        accept_any: bool = True,
        probes: dict[str, Any] | None = None,
        presets: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ):
        body: dict[str, Any] = {
            "generation": generation,
            "host_key": host_key,
            "accept_any": accept_any,
            "fingerprint": probes if probes is not None else {"xc": ok_probe("Xc 1")},
            "presets": facts(**(presets or {})),
            **(extra or {}),
        }
        return self.req("POST", "/worker/capabilities", token=worker, json_body=body)

    def reported(self, worker: str = "build-02", **kw: Any) -> str:
        """등록 + 보고(200). 세대를 돌려준다."""
        reg = {k: kw.pop(k) for k in ("pool", "host_name") if k in kw}
        gen = self.generation(worker, **reg)
        status, body = self.report(worker, gen, **kw)
        assert status == 200, body
        return gen

    def spec(self, worker: str = "build-02"):
        return self.req("GET", "/worker/spec", token=worker)

    # ── 읽기 ──

    def machines_doc(self) -> dict[str, Any]:
        status, doc = self.req("GET", "/api/machines", token="alice")
        assert status == 200, doc
        return doc

    def machines(self) -> dict[str, dict[str, Any]]:
        return {m["id"]: m for m in self.machines_doc()["machines"]}

    # ── 클라이언트 쪽 ──

    def post_job(
        self,
        preset: str,
        *,
        token: str = "alice",
        machine: Any = None,
        git: bool | None = None,
        ref: str = GIT_SHA,
    ):
        """제출 한 번. git_ref 프리셋(simcheck)은 40 hex ref 로 — 원격을 안 부른다."""
        git = preset == "simcheck" if git is None else git
        source: dict[str, Any]
        if git:
            source = {"mode": "git_ref", "ref": ref}
        else:
            source = {
                "mode": "tree",
                "repo": "org/app",
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
            "join": False,
        }
        if machine is not None:
            body["machine"] = machine
        return self.req("POST", "/jobs", token=token, json_body=body)

    def queued(self, preset: str, **kw: Any) -> int:
        """제출(+ tree 면 업로드) → queued 잡 id."""
        status, body = self.post_job(preset, **kw)
        assert status == 201, body
        jid = int(body["job_id"])
        if body["state"] != QUEUED:
            token = kw.get("token", "alice")
            status, resp = self.req("PUT", f"/jobs/{jid}/tree", token=token, body=TAR)
            assert status == 200 and resp["state"] == QUEUED, resp
        return jid


def ok_probe(value: str) -> dict[str, Any]:
    return {"state": "ok", "value": value}


def facts(**overrides: Any) -> dict[str, Any]:
    """설정의 모든 프리셋에 빈 사실(전부 있음)을 깔고 `overrides` 로 덮는다."""
    out: dict[str, Any] = {
        name: {"missing_tools": [], "workspace_tools": [], "missing_passthrough": []}
        for name in PRESET_NAMES
    }
    for name, value in overrides.items():
        out[name] = {**out.get(name, {}), **value}
    return out


def expected_hash(values: dict[str, str]) -> str:
    from remote_ci_monitor.core.capability import ProbeResult, fingerprint_hash

    return fingerprint_hash({k: ProbeResult(state="ok", value=v) for k, v in values.items()})


@pytest.fixture
def srv(tmp_path):
    s = M5nServer(tmp_path)
    yield s
    s.close()


# ── 1. 세대와 명세 (§4.2) ────────────────────────────────────────────────────


def test_register_and_heartbeat_carry_the_generation(srv):
    gen = srv.generation()
    assert re.fullmatch(r"\d+:0", gen), gen  # "<기동 epoch>:<reload 횟수>"
    status, body = srv.heartbeat("build-02")
    assert status == 200, body
    assert body["generation"] == gen


def test_worker_spec_is_the_probes_and_the_preset_requirements(srv):
    gen = srv.generation()
    status, spec = srv.spec()
    assert status == 200, spec
    assert set(spec) == {"generation", "probe_interval_seconds", "probes", "presets"}
    assert spec["generation"] == gen
    assert spec["probe_interval_seconds"] == 600
    assert spec["probes"] == [
        {"name": "xc", "argv": [PY, "-c", "print('Xc 1')"], "timeout_seconds": 10}
    ]
    assert [p["name"] for p in spec["presets"]] == PRESET_NAMES
    capgate = next(p for p in spec["presets"] if p["name"] == "capgate")
    # claim payload 에 이미 실리던 것과 같은 범위 — argv · 비밀 · 저장소는 안 실린다
    assert capgate == {
        "name": "capgate",
        "requires": [FAKE_TOOL],
        "env_passthrough": ["PATH", "HOME", "LANG"],
        "env": {},
    }


def test_worker_spec_and_capabilities_need_a_worker_token(srv):
    assert srv.req("GET", "/worker/spec")[0] == 401
    status, body = srv.req("GET", "/worker/spec", token="alice")
    assert status == 403 and body["error"] == "worker token required"
    status, body = srv.req("POST", "/worker/capabilities", token="admin", json_body={})
    assert status == 403 and body["error"] == "worker token required"


def test_capabilities_with_the_current_generation_are_accepted(srv):
    gen = srv.generation()
    status, body = srv.report("build-02", gen)
    assert status == 200, body
    assert srv.machines()["build-02"]["fingerprint"]["hash"] == expected_hash({"xc": "Xc 1"})


def test_capabilities_from_another_generation_are_409_generation_mismatch(srv):
    srv.generation()
    status, body = srv.report("build-02", "1:0")
    assert status == 409, body
    assert body["code"] == "generation_mismatch"
    # 거절된 보고는 남지 않는다 — 그 워커는 여전히 「보고 없음」
    m = srv.machines()["build-02"]
    assert m["fingerprint"]["hash"] is None
    assert m["presets"]["anywhere"] == {"state": "unknown", "code": "report_missing", "detail": {}}


def test_capabilities_from_an_unregistered_worker_are_409(srv):
    status, body = srv.report("mbp", "1:0")
    assert status == 409, body
    assert body["error"] == "worker mbp is not registered"


@pytest.mark.parametrize(
    "where, overrides",
    [
        ("preset", {"presets": {"no-such-preset": {"missing_tools": []}}}),
        ("probe", {"probes": {"xc": ok_probe("Xc 1"), "no-such-probe": ok_probe("1")}}),
    ],
)
def test_capabilities_naming_what_the_config_does_not_have_are_400(srv, where, overrides):
    """가정 B-4: 설정에 없는 프리셋 · 프로브 이름은 400(어느 이름인지 댄다). 세대가 같은데 이름이
    다르면 워커가 잘못 잰 것이다 — 조용히 버리지 않는다."""
    gen = srv.generation()
    presets = overrides.get("presets")
    body: dict[str, Any] = {
        "generation": gen,
        "host_key": HOST_B,
        "accept_any": True,
        "fingerprint": overrides.get("probes", {"xc": ok_probe("Xc 1")}),
        "presets": {**facts(), **(presets or {})},
    }
    status, resp = srv.req("POST", "/worker/capabilities", token="build-02", json_body=body)
    assert status == 400, resp
    assert f"no-such-{where}" in resp["error"]


def test_a_probe_value_longer_than_120_characters_is_400(srv):
    gen = srv.generation()
    status, _ = srv.report("build-02", gen, probes={"xc": ok_probe("x" * 120)})
    assert status == 200
    status, body = srv.report("build-02", gen, probes={"xc": ok_probe("x" * 121)})
    assert status == 400, body
    assert "xc" in body["error"]


def test_unknown_keys_in_a_report_are_ignored(srv):
    gen = srv.generation()
    status, body = srv.report("build-02", gen, extra={"from_the_future": {"a": 1}})
    assert status == 200, body


def test_reload_bumps_the_generation_and_the_spec_follows(srv):
    gen = srv.generation()
    srv.write(PROBES + PRESETS + LATE)
    status, body = srv.req("POST", "/reload", token="admin", json_body={})
    assert status == 200, body
    epoch, n = gen.split(":")
    status, hb = srv.heartbeat("build-02")
    assert hb["generation"] == f"{epoch}:{int(n) + 1}"
    assert [p["name"] for p in srv.spec()[1]["presets"]] == [*PRESET_NAMES, "late"]
    # 옛 세대로 잰 보고는 이제 거절된다 — 워커는 명세를 다시 받는다
    assert srv.report("build-02", gen)[0] == 409
    assert srv.machines_doc()["generation"] == hb["generation"]


def test_the_local_machine_is_measured_by_the_server_process(tmp_path):
    probes = PROBES + '\n[[probes]]\nname = "gone"\nargv = ["rcm-m5n-no-such-probe"]\n'
    s = M5nServer(tmp_path, probes=probes)
    try:
        s.app.measure_capabilities()  # §4.2 — 기동 · reload · 주기에 서버가 부르는 그것
        m = s.machines()[LOCAL]
        assert (m["kind"], m["pool"], m["alive"]) == ("local", "default", True)
        assert m["accept_any"] is True
        assert m["fingerprint"]["probes"] == {
            "xc": {"state": "ok", "value": "Xc 1", "previous": None},
            "gone": {"state": "missing", "value": None, "previous": None},
        }
        assert m["presets"]["capgate"] == {
            "state": "ineligible",
            "code": "tool_missing",
            "detail": {"tools": [FAKE_TOOL]},
        }
        assert m["presets"]["anywhere"] == {"state": "eligible", "code": "ok", "detail": {}}
    finally:
        s.close()


def test_the_local_machine_is_measured_at_start(tmp_path):
    s = M5nServer(tmp_path, workers=True)
    try:
        m = s.machines()[LOCAL]
        assert m["fingerprint"]["probes"]["xc"]["value"] == "Xc 1"
        assert m["fingerprint"]["hash"] == expected_hash({"xc": "Xc 1"})
    finally:
        s.close()


# ── 2. GET /api/machines (§8.1) ──────────────────────────────────────────────

MACHINE_KEYS = {
    "id",
    "display_name",
    "kind",
    "pool",
    "lanes",  # 역할 C 가정 C1 — `rcm machines` 의 레인 칸
    "alive",
    "host_label",
    "host_key",
    "accept_any",
    "fingerprint",
    "presets",
    "checks",
}


def test_a_reported_remote_machine_has_the_spec_shape(srv):
    srv.reported("build-02", presets={"capgate": {"missing_tools": [FAKE_TOOL]}})
    doc = srv.machines_doc()
    assert set(doc) == {"generation", "machines"}
    m = {x["id"]: x for x in doc["machines"]}["build-02"]
    assert set(m) == MACHINE_KEYS
    assert (m["kind"], m["pool"], m["alive"], m["accept_any"]) == ("remote", "mac2", True, True)
    assert m["host_key"] == HOST_B
    assert m["host_label"].endswith(f"({HOST_B[:4]})")  # 해시 앞 넉 자 — UUID 원문은 없다
    assert m["fingerprint"] == {
        "hash": expected_hash({"xc": "Xc 1"}),
        "measured_at": iso(T0),
        "stale": False,
        "changed_at": None,
        "probes": {"xc": {"state": "ok", "value": "Xc 1", "previous": None}},
    }
    assert m["presets"]["capgate"] == {
        "state": "ineligible",
        "code": "tool_missing",
        "detail": {"tools": [FAKE_TOOL]},
    }
    assert m["presets"]["anywhere"] == {"state": "eligible", "code": "ok", "detail": {}}
    assert (m["presets"]["scenario"]["state"], m["presets"]["scenario"]["code"]) == (
        "unknown",
        "check_needed",
    )
    assert m["checks"] == [
        {
            "preset": "simcheck",
            "state": "needed",
            "job_id": None,
            "auto_submitted": False,
            "error_code": None,
        }
    ]


def test_a_workspace_only_tool_is_unknown_not_ineligible(srv):
    """edge 6 의 서버 쪽 — 보고의 `workspace_tools` 는 `workspace_tool`(unknown)."""
    srv.reported("build-02", presets={"capgate": {"workspace_tools": [FAKE_TOOL]}})
    got = srv.machines()["build-02"]["presets"]["capgate"]
    assert (got["state"], got["code"]) == ("unknown", "workspace_tool")


def test_missing_passthrough_is_shown_but_does_not_block(srv):
    srv.reported("build-02", presets={"anywhere": {"missing_passthrough": ["FLUTTER_ROOT"]}})
    got = srv.machines()["build-02"]["presets"]["anywhere"]
    assert (got["state"], got["code"]) == ("eligible", "ok")


def test_a_machine_without_heartbeat_is_not_alive(srv):
    srv.reported("build-02")
    srv.clock.advance(61)
    assert srv.machines()["build-02"]["alive"] is False


def test_machines_are_readable_without_a_token_under_the_default_read_auth(srv):
    srv.reported("build-02")
    status, doc = srv.req("GET", "/api/machines")
    assert status == 200 and "build-02" in [m["id"] for m in doc["machines"]]


def test_a_changed_fingerprint_shows_the_previous_value_and_the_time(srv):
    gen = srv.reported("build-02")
    srv.clock.advance(30)
    assert srv.report("build-02", gen, probes={"xc": ok_probe("Xc 2")})[0] == 200
    fp = srv.machines()["build-02"]["fingerprint"]
    assert fp["changed_at"] == iso(T0 + timedelta(seconds=30))
    assert fp["probes"]["xc"] == {"state": "ok", "value": "Xc 2", "previous": "Xc 1"}
    assert fp["hash"] == expected_hash({"xc": "Xc 2"})


def test_the_fingerprint_change_is_not_lost_across_a_server_restart(tmp_path):
    """edge 9 — `machines` 표가 앞 지문을 기억한다."""
    first = M5nServer(tmp_path)
    tokens = dict(first.tokens)
    try:
        first.reported("build-02")
    finally:
        first.close()
    clock = Clock(T0 + timedelta(hours=1))
    second = M5nServer(tmp_path, tokens=tokens, clock=clock)
    try:
        gen = second.generation()
        assert second.report("build-02", gen, probes={"xc": ok_probe("Xc 2")})[0] == 200
        fp = second.machines()["build-02"]["fingerprint"]
        assert fp["changed_at"] == iso(clock.now)
        assert fp["probes"]["xc"]["previous"] == "Xc 1"
    finally:
        second.close()


# ── 3. /api/status 의 새 키 (§11.3) ──────────────────────────────────────────

SUMMARY_KEYS = {
    "id",
    "display_name",
    "kind",
    "pool",
    "host_label",
    "alive",
    "fingerprint_hash",
    "fingerprint_changed_at",
    "eligible_count",
    "preset_count",
    "problems",
}
NOTE_KEYS = {"worker", "display_name", "state", "code", "detail"}


def test_status_carries_the_generation_and_a_summary_per_machine(srv):
    gen = srv.reported("build-02")
    server = srv.status()["server"]
    assert server["generation"] == gen
    m = {x["id"]: x for x in server["machines"]}["build-02"]
    assert SUMMARY_KEYS <= set(m)
    assert (m["kind"], m["pool"], m["alive"]) == ("remote", "mac2", True)
    assert m["fingerprint_hash"] == expected_hash({"xc": "Xc 1"})
    assert m["fingerprint_changed_at"] is None
    # ok · needtool · capgate · anywhere · simcheck 는 eligible, scenario 는 check_needed
    assert (m["eligible_count"], m["preset_count"]) == (5, 6)


def test_a_waiting_capable_job_row_says_any_machine_and_why_none_fits(srv):
    srv.generation()  # 등록만 — 보고 없음
    jid = srv.queued("anywhere")
    row = srv.row(jid)
    assert row["placement"] == "capable"
    assert row["display_pool"] == "default"
    assert row["target_worker"] is None
    assert row["concurrency_scope"] == "pool"
    assert row["host_key"] is None
    assert row["reason"] == "no_capable_machine"
    notes = {n["worker"]: n for n in row["placement_detail"]}
    assert set(notes["build-02"]) == NOTE_KEYS
    assert (notes["build-02"]["state"], notes["build-02"]["code"]) == ("unknown", "report_missing")
    assert len(row["placement_detail"]) <= 8


def test_a_capable_job_row_lists_an_eligible_worker_once_it_reports(srv):
    gen = srv.generation()
    jid = srv.queued("anywhere")
    assert srv.report("build-02", gen)[0] == 200
    row = srv.row(jid)
    assert row["reason"] != "no_capable_machine"
    notes = {n["worker"]: n for n in row["placement_detail"]}
    assert (notes["build-02"]["state"], notes["build-02"]["code"]) == ("eligible", "ok")


def test_a_down_worker_is_a_down_note(srv):
    srv.reported("build-02")
    jid = srv.queued("anywhere")
    srv.clock.advance(61)
    notes = {n["worker"]: n for n in srv.row(jid)["placement_detail"]}
    assert notes["build-02"]["state"] == "down"


def test_presets_in_status_carry_placement_checks_scope_and_sample(srv):
    presets = {p["name"]: p for p in srv.status()["presets"]}
    assert presets["capgate"]["placement"] == "any-capable"
    assert presets["ok"]["placement"] == "pool"
    assert presets["scenario"]["checks"] == ["simcheck"]
    assert presets["ok"]["checks"] == []
    assert presets["ok"]["concurrency_scope"] == "pool"
    assert presets["ok"]["sample"] is True
    assert presets["simcheck"]["sample"] is False  # 점검 프리셋의 기본은 표본 제외(§7.4)


# ── 4. POST /jobs — placement 블록과 machine (§4.5 · §5.3 · edge 11) ─────────

PLACEMENT_KEYS = {"mode", "eligible_now", "blocked"}


def test_a_pool_submission_says_mode_pool(srv):
    status, body = srv.post_job("ok")
    assert status == 201, body
    assert set(body["placement"]) == PLACEMENT_KEYS
    assert body["placement"]["mode"] == "pool"


def test_a_capable_submission_with_no_capable_machine_still_queues_and_says_why(srv):
    """결정 99 — 맞는 머신이 없어도 큐에 선다. 왜 없는지는 워커별로."""
    srv.reported("build-02", presets={"capgate": {"missing_tools": [FAKE_TOOL]}})
    status, body = srv.post_job("capgate")
    assert status == 201, body
    placement = body["placement"]
    assert placement["mode"] == "any-capable"
    assert placement["eligible_now"] == []
    blocked = {b["worker"]: b for b in placement["blocked"]}
    assert blocked["build-02"] == {
        "worker": "build-02",
        "code": "tool_missing",
        "detail": {"tools": [FAKE_TOOL]},
    }


def test_a_capable_submission_names_the_machines_that_can_take_it_now(srv):
    srv.reported("build-02")
    status, body = srv.post_job("capgate")
    assert status == 201, body
    assert body["placement"]["eligible_now"] == ["build-02"]  # 로컬은 이 PATH 에 도구가 없다
    assert "build-02" not in [b["worker"] for b in body["placement"]["blocked"]]


def test_machine_on_a_normal_preset_needs_an_admin_token(srv):
    srv.reported("build-02")
    status, body = srv.post_job("capgate", machine="build-02")
    assert status == 403, body
    status, body = srv.post_job("ok", token="admin", machine="build-02")
    assert status == 201, body
    assert srv.store.get_job(body["job_id"]).target_worker == "build-02"


def test_machine_on_a_check_preset_needs_only_a_client_token(srv):
    srv.reported("build-02")
    status, body = srv.post_job("simcheck", machine="build-02")
    assert status == 201, body
    job = srv.store.get_job(body["job_id"])
    assert job.target_worker == "build-02"
    assert job.pool == "mac2"  # 가정 B-6: 지정 잡은 그 워커의 풀에 선다
    assert job.requester.name == "alice-laptop"


def test_machine_local_targets_the_local_lanes(srv):
    status, body = srv.post_job("simcheck", machine=LOCAL)
    assert status == 201, body
    job = srv.store.get_job(body["job_id"])
    assert (job.target_worker, job.pool) == (LOCAL, "default")


@pytest.mark.parametrize("machine", ["nope", "", 7, ["build-02"]])
def test_an_unknown_or_malformed_machine_is_400(srv, machine):
    srv.reported("build-02")
    status, body = srv.post_job("simcheck", token="admin", machine=machine)
    assert status == 400, body


def test_a_down_machine_is_409(srv):
    srv.reported("build-02")
    srv.clock.advance(61)
    status, body = srv.post_job("simcheck", machine="build-02")
    assert status == 409, body


def test_a_targeted_job_is_claimed_only_by_its_machine(srv):
    srv.reported("build-02")
    srv.reported("mbp", pool="mbp", host_key=HOST_C)
    jid = srv.queued("simcheck", machine="build-02")
    assert srv.claim("mbp")[0] == 204
    assert srv.claimed("build-02") == jid
    job = srv.store.get_job(jid)
    assert job.fingerprint_hash == expected_hash({"xc": "Xc 1"})  # 점검 판정의 근거(§5.2)
    assert job.host_key == HOST_B


# ── 5. claim 경로의 판정 (edge 5 · 7 · 8 · 14) ────────────────────────────────


def test_a_worker_without_a_report_does_not_take_a_capable_job(srv):
    """edge 5 ① — fail-closed."""
    gen = srv.generation()
    jid = srv.queued("anywhere")
    assert srv.claim("build-02")[0] == 204
    assert srv.report("build-02", gen)[0] == 200
    assert srv.claimed("build-02") == jid


def test_a_capable_claim_moves_the_job_to_the_workers_pool(srv):
    """edge 8 — 끝난 잡은 「실제로 돈 곳」으로."""
    srv.reported("build-02")
    jid = srv.queued("anywhere")
    assert srv.claimed("build-02") == jid
    job = srv.store.get_job(jid)
    assert (job.pool, job.display_pool, job.worker_name) == ("mac2", "default", "build-02")
    assert job.host_key == HOST_B
    assert job.fingerprint_hash == expected_hash({"xc": "Xc 1"})
    assert srv.finish("build-02", jid, "succeeded", exit_code=0)[0] == 200
    recent = {p["name"]: p for p in srv.status()["pools"]}["mac2"]["recent"]
    assert [r["id"] for r in recent] == [jid]


def test_a_report_from_an_older_generation_does_not_take_a_capable_job(srv):
    """edge 5 ② — reload 뒤 옛 세대의 보고는 `report_stale`."""
    srv.reported("build-02")
    jid = srv.queued("anywhere")
    srv.write(PROBES + PRESETS + LATE)
    assert srv.req("POST", "/reload", token="admin", json_body={})[0] == 200
    assert srv.claim("build-02")[0] == 204
    got = srv.machines()["build-02"]["presets"]["anywhere"]
    assert (got["state"], got["code"]) == ("unknown", "report_stale")
    assert srv.store.get_job(jid).state == QUEUED


def test_a_report_older_than_three_probe_intervals_does_not_take_a_capable_job(tmp_path):
    """edge 5 ③ — heartbeat 은 살아 있어도 보고가 낡았다."""
    s = M5nServer(tmp_path, extra="probe_interval_seconds = 60\n")
    try:
        gen = s.reported("build-02")
        jid = s.queued("anywhere")
        s.clock.advance(181)
        assert s.heartbeat("build-02")[0] == 200
        assert s.claim("build-02")[0] == 204
        got = s.machines()["build-02"]["presets"]["anywhere"]
        assert (got["state"], got["code"]) == ("unknown", "report_stale")
        assert s.report("build-02", gen)[0] == 200  # 다시 재서 보내면 집는다
        assert s.claimed("build-02") == jid
    finally:
        s.close()


def test_a_worker_that_does_not_accept_any_takes_targeted_checks_only(srv):
    """edge 14."""
    srv.reported("build-02", accept_any=False)
    capable = srv.queued("anywhere")
    assert srv.claim("build-02")[0] == 204
    check = srv.queued("simcheck", machine="build-02")
    assert srv.claimed("build-02") == check
    assert srv.store.get_job(capable).state == QUEUED
    assert srv.machines()["build-02"]["accept_any"] is False


def test_a_pool_job_with_a_missing_tool_is_still_claimed_by_its_pool(srv):
    """edge 7 — `pool` 배치의 `requires` 불능은 오늘처럼 집힌다(워커가 즉시 tool_missing 으로
    끝낸다)."""
    srv.reported("build-02", presets={"needtool": {"missing_tools": [FAKE_TOOL]}})
    jid = srv.queued("needtool")
    assert srv.claimed("build-02") == jid


def test_a_pool_job_with_a_missing_tool_still_fails_tool_missing_on_a_local_lane(tmp_path):
    """edge 7 — 로컬 레인 쪽 회귀. 프로세스를 안 띄우고 `tool_missing` 으로 빨개진다."""
    presets = PRESETS + f'\n[[presets]]\nname = "needlocal"\nrequires = ["{FAKE_TOOL}"]\n'
    presets += 'argv = ["sh", "-c", "echo never"]\n'
    s = M5nServer(tmp_path, workers=True, presets=presets)
    try:
        jid = s.queued("needlocal")
        job = s.store.get_job(jid)
        for _ in range(400):
            job = s.store.get_job(jid)
            if job.is_terminal:
                break
            time.sleep(0.05)
        assert job.state == "failed"
        assert job.summary_code == "tool_missing"
    finally:
        s.close()


def test_a_pool_job_whose_check_is_needed_waits_until_the_check_passes(srv):
    """`checks` 는 opt-in 이고 막으면 pool 잡도 기다린다(결정 98)."""
    srv.reported("build-02")
    qa = srv.queued("scenario")
    assert srv.claim("build-02")[0] == 204
    check = srv.queued("simcheck", machine="build-02")
    assert srv.claimed("build-02") == check  # 점검이 먼저 — scenario 는 막혀 있다
    assert srv.finish("build-02", check, "succeeded", exit_code=0)[0] == 200
    assert srv.claimed("build-02") == qa
    got = srv.machines()["build-02"]
    assert (got["presets"]["scenario"]["state"], got["presets"]["scenario"]["code"]) == (
        "eligible",
        "ok",
    )
    assert got["checks"][0]["state"] == "passed"
    assert got["checks"][0]["job_id"] == check


def test_a_failed_check_blocks_the_pool_job_with_check_failed(srv):
    srv.reported("build-02")
    qa = srv.queued("scenario")
    check = srv.queued("simcheck", machine="build-02")
    assert srv.claimed("build-02") == check
    assert srv.finish("build-02", check, "failed", exit_code=1)[0] == 200
    assert srv.claim("build-02")[0] == 204
    got = srv.machines()["build-02"]["presets"]["scenario"]
    assert (got["state"], got["code"]) == ("ineligible", "check_failed")
    row = srv.row(qa)
    assert row["reason"] == "check_failed"


# ── 6. 호스트별 중앙값 (§7.1 · edge 8 · 12 · 13) ─────────────────────────────


def finished_ok(srv: M5nServer, seconds: float, n: int = 3, **cols: Any) -> list[int]:
    """`ok` 종료 잡 n 개(각 `seconds` 초). 새 열은 SQL 로 — 옛 행(NULL)을 흉내 낼 수 있게."""
    from remote_ci_monitor.core.model import Requester, Source

    ids = []
    for i in range(n):
        start = T0 - timedelta(hours=2 + i)
        job = srv.store.create_job(
            preset="ok",
            inputs={},
            key="ok",
            concurrency_group=None,
            source=Source(mode="tree", tree_hash=f"done-{seconds}-{i}-{len(cols)}"),
            requester=Requester(name="alice-laptop", label="alice@host"),
            timeout_seconds=600,
            join_key=None,
            now=start - timedelta(seconds=5),
            state=QUEUED,
        )
        sets = {
            "state": "succeeded",
            "started_at": start.timestamp(),
            "finished_at": (start + timedelta(seconds=seconds)).timestamp(),
            **cols,
        }
        sql = ", ".join(f"{k}=?" for k in sets)
        srv.store._conn().execute(f"UPDATE jobs SET {sql} WHERE id=?", (*sets.values(), job.id))
        ids.append(job.id)
    srv.app._mark_medians_dirty()
    srv.app._mark_dirty()
    return ids


def queued_ok_estimate(srv: M5nServer) -> dict[str, Any]:
    srv.app.measure_capabilities()  # 로컬 레인의 호스트를 안다
    jid = srv.queued("ok")
    return srv.row(jid)["estimate"]


def test_legacy_local_rows_count_as_the_local_host(srv):
    """edge 13 — 옛 행(host_key NULL · worker_name NULL)은 지금의 로컬 호스트다."""
    finished_ok(srv, 100)
    est = queued_ok_estimate(srv)
    assert (est["source"], est["expected_seconds"], est["sample_count"]) == ("measured", 100, 3)


def test_legacy_remote_rows_do_not_mix_into_the_local_median(srv):
    """edge 13 — 옛 원격 행은 `worker:<이름>` 호스트다. 오늘은 같은 풀이라 섞여 200 이 됐다."""
    finished_ok(srv, 100)
    finished_ok(srv, 300, worker_name="mbp")
    est = queued_ok_estimate(srv)
    assert (est["source"], est["expected_seconds"], est["sample_count"]) == ("measured", 100, 3)


def test_only_other_hosts_samples_give_measured_other_with_low_confidence(srv):
    """edge 12 의 서버 쪽 — 다른 호스트들의 중앙값으로 물러난다."""
    finished_ok(srv, 300, worker_name="mbp")
    est = queued_ok_estimate(srv)
    assert (est["source"], est["expected_seconds"]) == ("measured_other", 300)
    assert est["confidence"] == "low"


def test_rows_marked_sample_false_are_not_samples(srv):
    finished_ok(srv, 100, sample=0)
    est = queued_ok_estimate(srv)
    assert est["source"] == "default"


# ── 7. rcm eta 의 any-capable 가상 잡 (§7.2 — 구현하며 더함) ─────────────────


def test_eta_of_an_any_capable_preset_without_a_capable_machine_has_no_time(srv):
    """보고가 없으면 맞는 머신이 없다 — 시각을 지어내지 않는다(§12)."""
    srv.generation()
    status, body = srv.req("POST", "/api/eta", token="alice", json_body={"preset": "anywhere"})
    assert status == 200, body
    job = body["job"]
    assert job["placement"] == "capable"
    assert job["reason"] == "no_capable_machine"
    assert job["estimate"]["finish_at"] is None
    assert {n["worker"] for n in job["placement_detail"]} >= {"build-02"}


def test_eta_of_an_any_capable_preset_lands_on_a_capable_machine(srv):
    srv.reported("build-02")
    status, body = srv.req("POST", "/api/eta", token="alice", json_body={"preset": "anywhere"})
    assert status == 200, body
    job = body["job"]
    assert job["reason"] in ("waiting_for_lane", "not_scheduled")
    assert job["estimate"]["wait_seconds"] == 0
    assert job["estimate"]["finish_at"] is not None
    assert body["ahead"] == 0


def test_eta_with_an_explicit_pool_keeps_the_pool_rule(srv):
    srv.reported("build-02")
    status, body = srv.req(
        "POST", "/api/eta", token="alice", json_body={"preset": "anywhere", "pool": "default"}
    )
    assert status == 200, body
    assert body["job"]["placement"] == "pool"


def test_previous_is_only_on_the_probe_that_changed(tmp_path):
    """§8.1 — 바뀐 프로브만 앞 값을 싣는다. 안 바뀐 프로브의 `previous` 는 null 이다."""
    probes = PROBES + f'\n[[probes]]\nname = "idb"\nargv = ["{PY}", "-c", "print(1)"]\n'
    s = M5nServer(tmp_path, probes=probes)
    try:
        gen = s.reported("build-02", probes={"xc": ok_probe("Xc 1"), "idb": ok_probe("idb 1")})
        s.clock.advance(5)
        assert (
            s.report("build-02", gen, probes={"xc": ok_probe("Xc 2"), "idb": ok_probe("idb 1")})[0]
            == 200
        )
        fp = s.machines()["build-02"]["fingerprint"]["probes"]
        assert fp["xc"]["previous"] == "Xc 1"
        assert fp["idb"]["previous"] is None
    finally:
        s.close()
