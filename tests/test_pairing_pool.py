"""M5t-c PR2: 토큰에 묶인 풀과 등록 증명(docs/m5tc-workplan.md §4.2 「등록 증명」 · §4.5 · §7
PI13 · PI14 · PI15 · PI16 · PI23 · PI46 (e) · PI52).

서버는 in-process HTTP(`test_pairing_server.PairServer`, 주입 시계)다. 증명 MAC 과 거절 MAC 은
시험 안의 독립 구현(`test_pairing_wire.ref_mac`)으로 만든다 — 서버의 계산을 바꾸는 변이가 이
시험을 빨갛게 하도록. 토큰 · `pair_secret` · nonce · 세션 id 는 실행 중에 만든다(§8).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor.core import pairing as cp
from remote_ci_monitor.core.model import QUEUED, RUNNING, SUCCEEDED
from remote_ci_monitor.store import Store
from test_pairing_server import (
    PairServer,
    downgrade_tokens_to_v26,
    fresh_pair_id,
    raw_request,
    want_reject,
)
from test_pairing_wire import ref_mac
from test_server import sh
from test_worker_api import T0

#: 프리셋 env 값 — 다른 풀 토큰의 `/worker/spec` 응답에 없어야 한다(PI14). 실행 중에 만든다
DEFAULT_ENV_VALUE = "dflt-" + secrets.token_hex(6)
X_ENV_VALUE = "xpool-" + secrets.token_hex(6)
RELEASE_ENV_VALUE = "rel-" + secrets.token_hex(6)

POOL_PRESETS = [
    sh("ok", "echo ok", env={"DEFAULT_ONLY_VALUE": DEFAULT_ENV_VALUE}),
    sh("gate", "echo gate"),
    sh("slow", "sleep 20"),
    sh("xbuild", "echo x", pool="x", env={"X_VALUE": X_ENV_VALUE}),
    sh("xalso", "echo x too", pools=["x"]),
    sh("anycap", "echo any", placement="any-capable"),
    sh("lin", "echo linux", pool="linux"),
    sh("release", "echo deploy", local_only=True, env={"RELEASE_VALUE": RELEASE_ENV_VALUE}),
]
DEFAULT_ONLY = {"ok", "gate", "slow"}


class PoolServer(PairServer):
    def __init__(self, tmp_path: Path, **kw: Any):
        kw.setdefault("presets", POOL_PRESETS)
        super().__init__(tmp_path, **kw)

    # ── 짝지은 워커 ──

    def pair_worker(self, host_name: str = "build-01", **open_body: Any) -> dict[str, Any]:
        """교환으로 짝지은 worker 토큰. 이름 · 토큰을 도우미 표에 넣어 `req` 가 세션을 싣게 한다."""
        out = self.paired_by_exchange("worker", host_name, **open_body)
        pair_id, k = cp.parse_pair_secret(out["pair_secret"])
        out["pair_id"], out["k"] = pair_id, k
        self.tokens[out["name"]] = out["token"]
        self.worker_names.add(out["name"])
        return out

    def challenge(self, pair_id: str) -> str:
        _req, (status, doc) = self.hello(pair_id)
        assert status == 200, doc
        return doc["challenge"]

    def proof(self, w: dict[str, Any], session: str, challenge: str | None = None) -> dict:
        """등록 증명 — 독립 구현으로 만든다(`pair_mac(K, "rcm-register-v1", pair_id, c, sid)`)."""
        c = challenge or self.challenge(w["pair_id"])
        return {"challenge": c, "mac": ref_mac(w["k"], "rcm-register-v1", w["pair_id"], c, session)}

    def register_body(self, session: str, **extra: Any) -> dict[str, Any]:
        return {
            "pool": "default",
            "lanes": 1,
            "host_name": "build.local",
            "version": __version__,
            "worker_session_id": session,
            **extra,
        }

    def post_register(self, token: str, body: dict, nonce: str | None = None):
        """`POST /worker/register` 를 날 바이트로 — 보낸 본문 바이트와 응답을 함께 돌려준다."""
        sent = json.dumps(body).encode()
        status, _h, doc = raw_request(
            self,
            "POST",
            "/worker/register",
            token=token,
            nonce=nonce,
            body=sent,
            headers={"Content-Type": "application/json"},
        )
        return status, doc, sent

    def proof_registered(self, w: dict[str, Any], **body: Any) -> dict[str, Any]:
        """짝지은 워커의 지금 세션으로 증명을 실어 등록한다(200)."""
        name = w["name"]
        return self.registered(name, pair_proof=self.proof(w, self.session(name)), **body)

    # ── 상태 ──

    def snapshot(self) -> dict[str, Any]:
        """등록 실패가 바꾸면 안 되는 것 — 세션 행(펜싱 포함) · 워커 행 · 잡 상태."""
        conn = self.store._conn()
        return {
            "sessions": sorted(tuple(r) for r in conn.execute("SELECT * FROM worker_sessions")),
            "workers": sorted(tuple(r) for r in conn.execute("SELECT * FROM workers")),
            "jobs": sorted(
                tuple(r) for r in conn.execute("SELECT id, state, worker_name, lane FROM jobs")
            ),
        }


@pytest.fixture
def pp(tmp_path):
    s = PoolServer(tmp_path)
    yield s
    s.close()


class SideEffects:
    """등록 실패가 부르면 안 되는 App 메서드 — 능력 보고 지우기 · 호스트 사실 교체 · 세션 열기."""

    def __init__(self, pp: PoolServer, monkeypatch):
        self.calls: list[str] = []
        for name in ("forget_report", "remember_host"):
            real = getattr(pp.app, name)
            monkeypatch.setattr(pp.app, name, self._spy(name, real))
        real_open = pp.store.open_session
        monkeypatch.setattr(pp.store, "open_session", self._spy("open_session", real_open))

    def _spy(self, name, real):
        def spy(*a, **kw):
            self.calls.append(name)
            return real(*a, **kw)

        return spy


def spec_of(pp: PoolServer, token: str, query: str = "") -> tuple[dict, bytes]:
    status, _h, raw = pp.req("GET", "/worker/spec" + query, token=token, raw=True)
    assert status == 200, raw
    return json.loads(raw), raw


def preset_names(doc: dict) -> set[str]:
    return {p["name"] for p in doc["presets"]}


def x_presets() -> set[str]:
    return {"xbuild", "xalso", "anycap"}


def default_presets() -> set[str]:
    return DEFAULT_ONLY | {"xalso", "anycap"}  # xalso 의 `pool` 은 default 다


# ── PI23 탈취 토큰 · 등록 ──────────────────────────────────────────────────


def test_pi23_a_a_stolen_paired_token_cannot_register_without_a_valid_proof(pp, monkeypatch):
    w = pp.pair_worker()
    name = w["name"]
    legit = pp.session(name)
    pp.proof_registered(w)
    jid = pp.queued_job(preset="gate")
    assert pp.claimed(name) == jid
    assert pp.store.get_job(jid).state == RUNNING
    used = pp.proof(w, legit)
    pp.registered(name, pair_proof=used)  # 같은 세션의 재등록 — 이 챌린지를 쓴다
    before = pp.snapshot()
    spy = SideEffects(pp, monkeypatch)
    thief = secrets.token_hex(16)
    now = int(pp.clock().timestamp())

    def forged(t: int) -> str:
        r = secrets.token_hex(8)
        return f"{t}.{r}.{ref_mac(pp.key, 'rcm-challenge-v1', w['pair_id'], str(t), r)}"

    def good_mac(c: str, sid: str = thief) -> str:
        return ref_mac(w["k"], "rcm-register-v1", w["pair_id"], c, sid)

    other_pair = fresh_pair_id()
    cases: list[tuple[str, Any, int, str, str | None]] = [
        ("no proof", None, 403, "pair_proof_required", None),
        ("empty proof", {}, 403, "pair_proof_invalid", "bad_mac"),
        (
            "wrong mac",
            {"challenge": pp.challenge(w["pair_id"]), "mac": "0" * 64},
            403,
            "pair_proof_invalid",
            "bad_mac",
        ),
        ("used challenge", used, 403, "pair_proof_invalid", "bad_mac"),  # 다른 세션 id 로 만든 증명
        (
            "used challenge, this session",
            {"challenge": used["challenge"], "mac": good_mac(used["challenge"])},
            403,
            "pair_proof_invalid",
            "reused",
        ),
        ("121 s old", None, 403, "pair_proof_invalid", "expired"),
        ("6 s ahead", None, 403, "pair_proof_invalid", "expired"),
        ("another pair_id", None, 403, "pair_proof_invalid", "bad_mac"),
        ("another session", None, 403, "pair_proof_invalid", "bad_mac"),
    ]
    stale = forged(now - 121)
    cases[5] = (*cases[5][:1], {"challenge": stale, "mac": good_mac(stale)}, *cases[5][2:])
    ahead = forged(now + 6)
    cases[6] = (*cases[6][:1], {"challenge": ahead, "mac": good_mac(ahead)}, *cases[6][2:])
    stranger = pp.challenge(other_pair)  # K 를 가진 워커라도 다른 pair_id 의 챌린지는 안 된다
    cases[7] = (*cases[7][:1], {"challenge": stranger, "mac": good_mac(stranger)}, *cases[7][2:])
    c = pp.challenge(w["pair_id"])
    other_sid = good_mac(c, secrets.token_hex(16))
    cases[8] = (*cases[8][:1], {"challenge": c, "mac": other_sid}, *cases[8][2:])
    for label, proof, status, code, reason in cases:
        extra = {} if proof is None else {"pair_proof": proof}
        got, doc, _sent = pp.post_register(w["token"], pp.register_body(thief, **extra))
        assert (got, doc.get("code"), doc.get("reason")) == (status, code, reason), (label, doc)
        if code == "pair_proof_required":
            assert doc["error"] == (
                "this worker token was paired — its pairing proof is missing: upgrade rcm on "
                "this worker and pair it (rcm setup --role worker --pair)"
            )
        else:
            assert doc["error"] == f"pairing proof rejected ({reason})", label
        assert pp.snapshot() == before, label
    assert spy.calls == []
    # 정당한 워커의 세션과 도는 잡은 그대로다
    assert pp.store.get_session(legit).fenced_at is None
    assert pp.store.get_job(jid).state == RUNNING
    assert pp.heartbeat(name, jobs=[jid])[0] == 200


def test_pi23_a_the_proof_check_reads_the_pair_id_of_the_token_not_the_body(pp):
    """313: 본문에 다른 `pair_id` 를 실어도 서버는 토큰 행의 `pair_id` 로 K 를 만든다."""
    a = pp.pair_worker("build-01")
    b = pp.pair_worker("build-05")
    sid = secrets.token_hex(16)
    proof = pp.proof(b, sid)  # b 의 K · 챌린지로 만든 맞는 증명
    body = pp.register_body(sid, pair_proof=proof, pair_id=b["pair_id"])
    status, doc, _ = pp.post_register(a["token"], body)
    assert (status, doc.get("code"), doc.get("reason")) == (403, "pair_proof_invalid", "bad_mac")


def test_pi23_g_a_peeked_challenge_with_a_wrong_mac_does_not_burn_it(pp):
    w = pp.pair_worker()
    c = pp.challenge(w["pair_id"])  # 엿본 hello 응답의 챌린지
    thief = secrets.token_hex(16)
    status, doc, _ = pp.post_register(
        w["token"], pp.register_body(thief, pair_proof={"challenge": c, "mac": "1" * 64})
    )
    assert (status, doc.get("reason")) == (403, "bad_mac")
    # 정당한 워커가 같은 챌린지로 한 등록이 성공한다
    body = pp.registered(w["name"], pair_proof=pp.proof(w, pp.session(w["name"]), c))
    assert body["name"] == w["name"]


def test_pi23_b_without_registering_the_token_gets_no_claim_and_only_its_pool_spec(pp):
    w = pp.pair_worker(pool="x")
    jid = pp.queued_job(preset="xbuild")
    thief = secrets.token_hex(16)
    for path, body in (
        ("/worker/claim", {"lane": 1, "wait_seconds": 0, "claim_seq": 1}),
        ("/worker/heartbeat", {"lane": 1, "seq": 1, "claims_settled": 0}),
    ):
        status, doc = pp.req(
            "POST", path, token=w["name"], json_body={**body, "worker_session_id": thief}
        )
        assert (status, doc.get("code")) == (409, "not_registered"), (path, doc)
    assert pp.store.get_job(jid).state == QUEUED
    doc, raw = spec_of(pp, w["name"])
    assert preset_names(doc) == x_presets()
    assert DEFAULT_ENV_VALUE.encode() not in raw


def test_pi23_c_an_old_explicit_token_registers_without_proof_and_the_address_is_logged(pp):
    name = "build-02"
    first = pp.session(name)
    info = pp.store.verify_token(pp.tokens[name])
    assert info.pair_id is None
    pp.app.worker_register(info, pp.register_body(first))
    pp.app.note_register_addr(name, "192.0.2.7")  # 다른 머신에서 한 앞 등록
    pp.logs.clear()
    # 다른 머신(루프백)의 새 프로세스 — 증명이 없어도(본문의 쓰레기 증명은 무시) 등록되고 펜싱한다
    pp.new_process(name)
    body = pp.registered(name, pair_proof={"challenge": "x", "mac": "y"})
    assert body["name"] == name
    assert pp.store.get_session(first).fenced_at is not None
    lines = [line for line in pp.logs if "registered from" in line]
    assert lines == [
        f"worker {name} registered from 127.0.0.1 — its previous registration came from 192.0.2.7"
    ], pp.logs
    # 같은 주소에서 다시 등록하면 줄이 없다
    pp.logs.clear()
    pp.restarted(name)
    assert not [line for line in pp.logs if "registered from" in line], pp.logs


def test_pi23_c_a_paired_worker_moving_address_is_logged_too(pp):
    w = pp.pair_worker()
    info = pp.store.verify_token(w["token"])
    sid = secrets.token_hex(16)
    pp.app.worker_register(info, pp.register_body(sid, pair_proof=pp.proof(w, sid)))
    pp.app.note_register_addr(w["name"], "192.0.2.9")
    pp.logs.clear()
    pp.proof_registered(w)
    assert (
        f"worker {w['name']} registered from 127.0.0.1 — its previous registration came from "
        "192.0.2.9"
    ) in pp.logs


def test_pi23_d_the_paired_worker_token_cannot_use_client_or_pair_admin_routes(pp):
    w = pp.pair_worker()
    for method, path, body in (
        ("POST", "/jobs/1/cancel", {}),
        ("POST", "/jobs", {"preset": "ok"}),
        ("POST", "/pair", {"kind": "worker"}),
        ("GET", "/pair", None),
        ("POST", "/pair/cancel", {"kind": "worker"}),
    ):
        status, doc = pp.req(method, path, token=w["name"], json_body=body)
        assert status == 403, (path, status, doc)


def test_pi23_e_another_workers_job_answers_as_it_does_for_any_other_worker(pp):
    w = pp.pair_worker()
    pp.proof_registered(w)
    pp.registered("build-03")
    pp.registered("build-02")
    jid = pp.queued_job(preset="gate")
    assert pp.claimed("build-02") == jid

    def answers(worker: str) -> list[int]:
        return [
            pp.tree(worker, jid)[0],
            pp.req(
                "POST",
                f"/worker/jobs/{jid}/log",
                token=worker,
                body=b"x",
                headers={"Content-Type": "application/octet-stream"},
            )[0],
            pp.finish(worker, jid, "succeeded", exit_code=0)[0],
        ]

    paired_answers = answers(w["name"])
    assert paired_answers == answers("build-03")  # 오늘과 같다(짝지은 토큰이라 달라지지 않는다)
    assert set(paired_answers) <= {403, 404, 409}, paired_answers
    assert pp.store.get_job(jid).state == RUNNING


# ── PI46 (e) 실제 등록 라우트의 거절 MAC ───────────────────────────────────


def test_pi46_e_the_register_route_puts_a_mac_on_required_bad_mac_and_pool_mismatch(pp):
    w = pp.pair_worker(pool="x")
    used_sid = secrets.token_hex(16)
    used = pp.proof(w, used_sid)
    assert (
        pp.post_register(w["token"], pp.register_body(used_sid, pool="x", pair_proof=used))[0]
        == 200
    )
    now = int(pp.clock().timestamp())
    r = secrets.token_hex(8)
    old = f"{now - 121}.{r}.{ref_mac(pp.key, 'rcm-challenge-v1', w['pair_id'], str(now - 121), r)}"
    sid = secrets.token_hex(16)
    cases = [
        ({"pool": "x"}, 403, "pair_proof_required", "", True),
        (
            {"pool": "x", "pair_proof": {"challenge": pp.challenge(w["pair_id"]), "mac": "2" * 64}},
            403,
            "pair_proof_invalid",
            "bad_mac",
            True,
        ),
        ({"pool": "default", "pair_proof": pp.proof(w, sid)}, 409, "pool_mismatch", "x", True),
        (
            {
                "pool": "x",
                "pair_proof": {
                    "challenge": old,
                    "mac": ref_mac(w["k"], "rcm-register-v1", w["pair_id"], old, sid),
                },
            },
            403,
            "pair_proof_invalid",
            "expired",
            False,
        ),
        (
            {
                "pool": "x",
                "pair_proof": {
                    "challenge": used["challenge"],
                    "mac": ref_mac(w["k"], "rcm-register-v1", w["pair_id"], used["challenge"], sid),
                },
            },
            403,
            "pair_proof_invalid",
            "reused",
            False,
        ),
    ]
    for extra, status, code, reason, wants in cases:
        nonce = secrets.token_hex(32)
        got, doc, sent = pp.post_register(w["token"], pp.register_body(sid, **extra), nonce=nonce)
        assert (got, doc.get("code"), doc.get("reason", "")) == (status, code, reason), doc
        if wants:
            assert doc["pair_reject_mac"] == want_reject(
                pp, w["pair_id"], "POST", "/worker/register", status, code, reason, nonce, sent
            ), code
            assert cp.verify_pair_reject(
                w["k"],
                "POST",
                "/worker/register",
                status,
                doc,
                nonce,
                sent_body_sha256=hashlib.sha256(sent).hexdigest(),
            )
        else:
            assert "pair_reject_mac" not in doc, (code, reason)


# ── PI13 틀린 풀 ────────────────────────────────────────────────────────────


def test_pi13_a_b_a_bound_token_registering_another_pool_is_409_and_changes_nothing(
    pp, monkeypatch
):
    w = pp.pair_worker(pool="x")
    name = w["name"]
    legit = pp.session(name)
    pp.proof_registered(w, pool="x")
    jid = pp.queued_job(preset="xbuild")
    assert pp.claimed(name) == jid
    before = pp.snapshot()
    spy = SideEffects(pp, monkeypatch)
    for wrong in ("default", "linux"):  # (a) 본문의 다른 풀 (b) worker.toml 을 고치고 다시 시작
        sid = secrets.token_hex(16)
        nonce = secrets.token_hex(32)
        body = pp.register_body(
            sid, pool=wrong, pair_proof=pp.proof(w, sid), previous_sessions=[legit]
        )
        status, doc, sent = pp.post_register(w["token"], body, nonce=nonce)
        assert status == 409, doc
        assert doc["error"] == (
            "this worker token is bound to pool 'x' — set pool = \"x\" in worker.toml or pair again"
        )
        assert (doc.get("code"), doc.get("bound_pool"), doc.get("reason")) == (
            "pool_mismatch",
            "x",
            "x",
        )
        assert cp.verify_pair_reject(
            w["k"],
            "POST",
            "/worker/register",
            409,
            doc,
            nonce,
            sent_body_sha256=hashlib.sha256(sent).hexdigest(),
        )
        assert pp.snapshot() == before, wrong
    assert spy.calls == []
    assert pp.store.get_session(legit).fenced_at is None
    assert pp.store.get_job(jid).state == RUNNING


def test_pi13_c_the_bound_pool_registers_as_today(pp):
    w = pp.pair_worker(pool="x")
    body = pp.proof_registered(w, pool="x")
    assert (body["name"], body["pool"]) == (w["name"], "x")
    assert pp.store.get_worker(w["name"]).pool == "x"
    jid = pp.queued_job(preset="xbuild")
    assert pp.claimed(w["name"]) == jid


def test_pi13_the_proof_is_checked_before_the_pool(pp):
    w = pp.pair_worker(pool="x")
    sid = secrets.token_hex(16)
    status, doc, _ = pp.post_register(w["token"], pp.register_body(sid, pool="default"))
    assert (status, doc.get("code")) == (403, "pair_proof_required")
    bad = {"challenge": pp.challenge(w["pair_id"]), "mac": "3" * 64}
    status, doc, _ = pp.post_register(w["token"], pp.register_body(sid, pool="y", pair_proof=bad))
    assert (status, doc.get("code"), doc.get("reason")) == (403, "pair_proof_invalid", "bad_mac")


# ── PI14 다른 풀 비밀 ──────────────────────────────────────────────────────


def test_pi14_a_token_bound_to_x_reads_only_x_and_any_capable_presets(pp):
    x = pp.pair_worker("build-x", pool="x")
    d = pp.pair_worker("build-d")
    x_doc, x_raw = spec_of(pp, x["name"])
    assert preset_names(x_doc) == x_presets()
    for name in DEFAULT_ONLY | {"release", "lin"}:
        assert f'"{name}"'.encode() not in x_raw, name
    assert DEFAULT_ENV_VALUE.encode() not in x_raw
    assert RELEASE_ENV_VALUE.encode() not in x_raw
    assert X_ENV_VALUE.encode() in x_raw
    # 묶인 토큰은 질의를 무시한다
    assert preset_names(spec_of(pp, x["name"], "?pool=default")[0]) == x_presets()
    d_doc, d_raw = spec_of(pp, d["name"])
    assert preset_names(d_doc) == default_presets()
    assert X_ENV_VALUE.encode() not in d_raw and RELEASE_ENV_VALUE.encode() not in d_raw
    assert DEFAULT_ENV_VALUE.encode() in d_raw


# ── PI15 기본 풀 · local_only ──────────────────────────────────────────────


def test_pi15_a_default_pool_paired_worker_takes_the_gate_job_but_never_local_only(pp):
    w = pp.pair_worker()  # --pool 없이 연 창
    assert pp.store.verify_token(w["token"]).pool == "default"
    pp.proof_registered(w, lanes=2)
    release = pp.queued_job(preset="release")
    gate = pp.queued_job(preset="gate")
    assert pp.claimed(w["name"], 1) == gate
    assert pp.claim(w["name"], 2)[0] == 204
    assert pp.store.get_job(release).state == QUEUED
    assert "release" not in preset_names(spec_of(pp, w["name"])[0])


# ── PI16 풀 NULL(옛 명시 토큰) ─────────────────────────────────────────────


def test_pi16_a_d_an_explicit_token_made_before_v27_works_as_today(tmp_path):
    data = tmp_path / "data"
    db = data / "rcm.sqlite3"
    s = Store(db)
    old = s.add_token("old-w", now=T0, kind="worker")
    s.close()
    downgrade_tokens_to_v26(db)
    pp = PoolServer(tmp_path)
    try:
        pp.tokens["old-w"] = old
        info = pp.store.verify_token(old)
        assert (info.kind, info.pool, info.pair_id) == ("worker", None, None)  # (d)
        assert pp.registered("old-w", pool="linux")["pool"] == "linux"
        jid = pp.queued_job(preset="lin")
        assert pp.claimed("old-w") == jid
        assert pp.finish("old-w", jid, "succeeded", exit_code=0)[0] == 200
        assert pp.store.get_job(jid).state == SUCCEEDED
        # (d) 짝지은 토큰은 DB 값과 같다
        w = pp.pair_worker(pool="x")
        got = pp.store.verify_token(w["token"])
        assert (got.pool, got.pair_id) == ("x", w["pair_id"])
    finally:
        pp.close()


def test_pi16_b_an_unbound_token_reads_its_registered_pool_or_the_query_never_everything(pp):
    name = "build-02"
    # 등록 전: 질의 pool, 없으면 default
    assert preset_names(spec_of(pp, name)[0]) == default_presets()
    linux = {"lin", "anycap"}
    assert preset_names(spec_of(pp, name, "?pool=linux")[0]) == linux
    assert preset_names(spec_of(pp, name, "?pool=x")[0]) == x_presets()
    status, doc = pp.req("GET", "/worker/spec?pool=no%20such", token=name)
    assert status == 400, doc
    # 등록한 뒤: 그 워커가 등록한 풀 — 질의는 보지 않는다
    pp.registered(name, pool="linux")
    assert preset_names(spec_of(pp, name)[0]) == linux
    assert preset_names(spec_of(pp, name, "?pool=default")[0]) == linux
    # 어느 길로도 전체가 나오지 않는다
    for query in ("", "?pool=default", "?pool=x", "?pool=linux"):
        doc, raw = spec_of(pp, "build-03", query)
        assert "release" not in preset_names(doc)
        assert preset_names(doc) != {p["name"] for p in POOL_PRESETS} - {"release"}


def test_pi16_b_the_worker_client_sends_its_own_pool_before_registering(pp):
    """§4.5 「질의 `pool`」: `WorkerClient.spec()` 이 자기 `pool` 을 싣는다. 등록 전 기본 풀이 아닌
    워커가 `default` 의 프리셋을 보지 않게(V2-15 는 옛 빌드만의 일이다). 풀을 모르는 클라이언트는
    오늘처럼 질의 없이 묻는다 — 서버가 `default` 로 거른다."""
    from remote_ci_monitor.client import WorkerClient

    client = WorkerClient(f"http://127.0.0.1:{pp.port}", pp.tokens["build-02"])
    assert preset_names(client.spec()) == default_presets()  # 풀을 모른다 — 질의 없음
    assert preset_names(client.spec_answer()) == default_presets()
    client.pool = "linux"  # 세션 id 처럼 부르는 쪽이 자기 설정의 풀을 넣는다
    assert preset_names(client.spec()) == {"lin", "anycap"}
    assert preset_names(client.spec_answer()) == {"lin", "anycap"}
    client.pool = "x"
    assert preset_names(client.spec()) == x_presets()


def test_pi16_the_window_count_uses_the_same_filter_as_the_spec(pp):
    for pool in ("default", "x", "linux"):
        doc = pp.opened("worker", pool=pool)
        status, _ = pp.req("POST", "/pair/cancel", token="admin", json_body={"kind": "worker"})
        assert status == 200
        assert doc["presets_count"] == len(pp.app.spec_presets_for_pool(pool)), pool
    assert [p.name for p in pp.app.spec_presets_for_pool("x")] == ["xbuild", "xalso", "anycap"]


# ── PI52 경로 위 본문 변조 — 서버 응답 바이트 ─────────────────────────────


class BodyRelay:
    """루프백 중계 — 헤더(`Authorization` · `X-RCM-Pair-Nonce`)는 그대로 넘기고 본문만 바꾼다.
    진짜 서버의 응답은 그대로 돌려준다."""

    def __init__(self, target_port: int):
        self.change = None  # 본문 dict → dict
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 시험 출력에 섞지 않는다
                pass

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                if relay.change is not None:
                    data = json.dumps(relay.change(json.loads(data))).encode()
                headers = {
                    k: v
                    for k, v in self.headers.items()
                    if k.lower() in ("authorization", "x-rcm-pair-nonce", "content-type")
                }
                headers["Content-Length"] = str(len(data))
                conn = http.client.HTTPConnection("127.0.0.1", target_port, timeout=15)
                conn.request("POST", self.path, body=data, headers=headers)
                resp = conn.getresponse()
                payload = resp.read()
                conn.close()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def through(relay: BodyRelay, token: str, body: dict, nonce: str):
    sent = json.dumps(body).encode()
    conn = http.client.HTTPConnection("127.0.0.1", relay.port, timeout=15)
    conn.request(
        "POST",
        "/worker/register",
        body=sent,
        headers={
            "Authorization": f"Bearer {token}",
            "X-RCM-Pair-Nonce": nonce,
            "Content-Type": "application/json",
            "Content-Length": str(len(sent)),
        },
    )
    resp = conn.getresponse()
    doc = json.loads(resp.read())
    conn.close()
    return resp.status, doc, sent


def flip_mac(body: dict) -> dict:
    mac = body["pair_proof"]["mac"]
    return {
        **body,
        "pair_proof": {**body["pair_proof"], "mac": ("0" if mac[0] != "0" else "1") + mac[1:]},
    }


@pytest.mark.parametrize(
    ("change", "status", "code", "reason"),
    [
        (flip_mac, 403, "pair_proof_invalid", "bad_mac"),
        (lambda b: {**b, "pool": "x"}, 409, "pool_mismatch", "default"),
    ],
    ids=["a-proof-mac", "b-pool"],
)
def test_pi52_a_b_a_relay_changing_the_body_gets_a_mac_over_what_the_server_received(
    pp, change, status, code, reason
):
    w = pp.pair_worker()
    relay = BodyRelay(pp.port)
    try:
        relay.change = change
        for _ in range(4):  # 세 번 넘게 되풀이한다
            sid = secrets.token_hex(16)
            nonce = secrets.token_hex(32)
            body = pp.register_body(sid, pair_proof=pp.proof(w, sid))
            got, doc, sent = through(relay, w["token"], body, nonce)
            assert (got, doc.get("code"), doc.get("reason")) == (status, code, reason), doc
            received = json.dumps(change(json.loads(sent))).encode()
            assert doc["pair_reject_mac"] == want_reject(
                pp, w["pair_id"], "POST", "/worker/register", status, code, reason, nonce, received
            )
            # 워커는 자기가 보낸 본문으로 검증한다 — 맞지 않으므로 종료 증거가 아니다
            assert not cp.verify_pair_reject(
                w["k"],
                "POST",
                "/worker/register",
                status,
                doc,
                nonce,
                sent_body_sha256=hashlib.sha256(sent).hexdigest(),
            )
        assert pp.store.get_worker(w["name"]) is None
        relay.change = None  # 중계가 바꾸기를 멈추면 다음 등록이 성공한다
        sid = secrets.token_hex(16)
        got, doc, _ = through(
            relay,
            w["token"],
            pp.register_body(sid, pair_proof=pp.proof(w, sid)),
            secrets.token_hex(32),
        )
        assert got == 200 and doc["name"] == w["name"], doc
    finally:
        relay.close()
