"""스토어 탭 서버 라우트 v2 — `/api/repos/<name>/release/*`: 상태 · plan · review · upload · 문안 ·
GitHub(미러) · 드라이버.

명세: 스토어 탭 API 계약 v2(2026-09-17) · docs/release-contract.md §2 · §5 · §6.

여기서 지키는 것:
- 인증 표: GET 넷(release · listing · github · driver)은 읽기 규칙이고 게이트와 무관하다. plan ·
  review(plan) · upload(rehearsal) · listing/validate 는 클라이언트 토큰. review(submit) ·
  upload(upload) 는 admin. 드라이버 start · confirm · abort · retry 는 admin. 워커 토큰은 403.
- 409 표 — UI 가 무엇을 보내든 서버가 문을 지킨다: setup_incomplete · review_plan_required ·
  review_plan_stale · review_plan_blocked · managed_publishing_unconfirmed · build_number_mismatch ·
  plan_required · plan_stale · unsafe_preset · role_not_configured · release_running · no_driver ·
  driver_missing · mirror_missing.
- 잡은 정규 제출 경로로(git_ref · 합류 · 같은 검증) 만들고 입력은 계약 §2 의 것만 보낸다.
- 드라이버는 가짜 셸 스크립트(네트워크 없음). 내부 토큰은 응답 · 로그 어디에도 없고 끝나면 폐기된다.
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gitrepo import RemoteRepo, build_remote, git, isolate_git_env
from remote_ci_monitor import release_verify
from remote_ci_monitor.collect import CollectResult
from remote_ci_monitor.config import RepoConfig, parse_preset, parse_release_profile
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import QUEUED, SUCCEEDED, Requester, Source
from remote_ci_monitor.release_verify import VerifyResult
from test_release_secrets import PROFILE_RAW, TOKEN
from test_server import Server

pytestmark = pytest.mark.skipif(__import__("shutil").which("git") is None, reason="no git")

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
OCTET = {"Content-Type": "application/octet-stream"}
TEXT = {"Content-Type": "text/plain"}
CHOICE = "choice"


def preset(name: str, inputs: list[dict]) -> dict:
    return {
        "name": name,
        "argv": ["sh", "-c", "true"],
        "source_modes": ["git_ref"],
        "repo": "app",
        "inputs": inputs,
    }


BUILD = {"name": "build_name", "type": "string"}
CONFIRM = {"name": "confirm_build_number", "type": "string", "default": ""}
PLATFORM = {
    "name": "platform",
    "type": CHOICE,
    "choices": ["both", "ios", "android"],
    "default": "both",
}
REVIEW_INPUTS = [
    BUILD,
    CONFIRM,
    {"name": "mode", "type": CHOICE, "choices": ["plan", "submit"], "default": "plan"},
    PLATFORM,
    {
        "name": "play_managed_publishing",
        "type": CHOICE,
        "choices": ["not-checked", "confirmed-on"],
        "default": "not-checked",
    },
    {"name": "listing", "type": CHOICE, "choices": ["notes-only", "full"], "default": "notes-only"},
    {"name": "phased", "type": CHOICE, "choices": ["1", "0"], "default": "1"},
    {"name": "extra", "type": "string", "default": "kept"},  # rcm 이 안 보내는 입력은 기본값
]
PRESETS = [
    preset("release-plan", [BUILD]),
    preset(
        "release-upload",
        [
            BUILD,
            CONFIRM,
            {
                "name": "mode",
                "type": CHOICE,
                "choices": ["rehearsal", "upload"],
                "default": "rehearsal",
            },
            PLATFORM,
            {"name": "android_track", "type": "string", "default": "internal"},
        ],
    ),
    preset("release-review", REVIEW_INPUTS),
    preset(
        "release-review-unsafe",
        [*REVIEW_INPUTS, {"name": "rollout", "type": "string", "default": "0"}],
    ),
    preset("gate-smoke", []),
]
# 요즘 드라이버 — `--status` 가 `--version-id` 없이도 아는 단계를 말한다(계약 §5).
DRIVER = """#!/bin/sh
echo "driver $*"
echo "server=${RCM_SERVER:-none} token=${RCM_TOKEN:+set}"
case " $* " in
  *" --status "*) echo "stages: V S0 S1 S2 S3 S4 S5 S6 S7 S8"; echo "stage: S1 planned"; exit 0;;
  *" --confirm-build-number "*) echo "confirmed $*"; exit 0;;
  *" --abort "*) echo "aborted"; exit 0;;
  *" --retry "*) echo "retried"; exit 0;;
esac
while [ -f "$APP_SECRETS/hold" ]; do sleep 0.05; done
echo "plan: N = 181"
exit 2
"""
# V 단계를 모르는 옛 드라이버 — `stages:` 줄이 없고 `--version-id` 를 주면 exit 2 로 죽는다.
OLD_DRIVER = DRIVER.replace(
    '*" --status "*) echo "stages: V S0 S1 S2 S3 S4 S5 S6 S7 S8"; echo "stage: S1 planned"',
    '*" --status "*) echo "stage: S1 planned"',
).replace(
    'case " $* " in',
    'case " $* " in\n  *" --version-id "*) echo "unknown argument: --version-id" >&2; exit 2;;',
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PROFILE = {
    **PROFILE_RAW,
    "driver": "scripts/driver.sh",
    "listing": {
        "preview": ["sh", "-c", "echo Title; echo 'Short description'"],
        "diff": ["sh", "-c", "echo '+ new sentence'; echo '- old sentence'"],
        "validate": ["sh", "-c", 'echo "v=$1 b=$2"; test -n "$2"', "x", "{version}", "{build}"],
        "screenshots": ["store/screenshots/*.png"],
        "release_notes": "store/release_notes/{version}/*.txt",
    },
}
PLAN_DOC = {
    "schema": 1,
    "build_name": "1.0.1",
    "n": 181,
    "first_release": False,
    "blockers": [],
    "warnings": [],
    "measured_at": "2026-09-17T11:56:00Z",
}
REVIEW_PLAN_DOC = {
    "schema": 2,
    "build_name": "1.0.1",
    "n": 181,
    "plan_verdict": "ok",
    "ios": "ready",
    "android": "ready",
    "measured_at": "2026-09-17T11:57:00Z",
}


def make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class ReleaseServer(Server):
    """프로파일(드라이버 · 문안) 있는 `app` 과 로컬 bare 원격. 게이트는 `open_gate()` 로 연다."""

    def __init__(self, tmp_path: Path, remote: RemoteRepo, **overrides):
        super().__init__(tmp_path, workers=False, **overrides)
        self.cfg.path = tmp_path / "etc" / "server.toml"
        self.cfg.path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.repos = (
            RepoConfig(name="app", url=remote.url, release=parse_release_profile("app", PROFILE)),
        )
        self.cfg.presets = tuple(parse_preset(p) for p in PRESETS) + self.cfg.presets
        self.remote = remote
        self.app.now_fn = lambda: NOW
        self.app.driver.now_fn = lambda: NOW
        self.server_log: list[str] = []
        self.app.log = self.server_log.append
        self.app.driver.log = self.server_log.append

    @property
    def secrets_root(self) -> Path:
        return self.cfg.path.parent / "secrets" / "app"

    def put_secret(self, name, data, *, headers=TEXT, file=None):
        path = f"/api/repos/app/secrets/{name}" + (f"/{file}" if file else "")
        return self.req("PUT", path, token="admin", body=data, headers=headers)

    def open_gate(self, monkeypatch) -> None:
        for kind, detail in (("github", "login: x"), ("keystore", "1 entry"), ("asc", "app")):
            monkeypatch.setitem(
                release_verify.VERIFIERS, kind, lambda ctx, d=detail: VerifyResult(None, d)
            )
        assert self.put_secret("GH_TOKEN", TOKEN.encode())[0] == 200
        assert self.put_secret("AuthKey.p8", b"-----KEY-----", headers=OCTET)[0] == 200
        assert self.put_secret("upload-keystore.jks", b"\xfe\xed", headers=OCTET)[0] == 200
        assert self.put_secret("review_information", b"demo", file="demo_user.txt")[0] == 200
        assert self.put_secret("review_information", b"pw", file="demo_password.txt")[0] == 200
        status, body = self.req("POST", "/api/repos/app/verify", token="admin", json_body={})
        assert status == 200
        assert self.req("GET", "/api/repos/app")[1]["setup"]["complete"] is True

    def fetch(self) -> str:
        status, body = self.req("POST", "/api/repos/app/fetch", token="alice", json_body={})
        assert status == 200, body
        return body["branches"]["main"]

    def role_job(
        self,
        preset_name: str,
        files: dict[str, bytes] | None,
        *,
        state: str = SUCCEEDED,
        finished: datetime = NOW - timedelta(minutes=5),
        inputs: dict | None = None,
    ) -> int:
        """git_ref 잡 하나를 만들어 끝낸다. `files` 가 있으면 그 파일들의 묶음을 발행한다."""
        sha = self.remote.main
        src = Source(mode="git_ref", repo="app", ref="main", sha=sha, base_sha=sha, dirty=False)
        job = self.store.create_job(
            preset=preset_name,
            inputs=inputs if inputs is not None else {"build_name": "1.0.1"},
            key=preset_name,
            concurrency_group=None,
            source=src,
            requester=Requester(name="alice-laptop", label="store:alice-laptop"),
            timeout_seconds=600,
            join_key=None,
            now=finished - timedelta(minutes=3),
            state=QUEUED,
        )
        # claim 은 FIFO 라 먼저 큐에 든 다른 잡을 집는다 — 이 잡만 running 으로 옮긴다
        self.store._conn().execute(
            "UPDATE jobs SET state='running', started_at=?, lane=1 WHERE id=?",
            ((finished - timedelta(minutes=2)).timestamp(), job.id),
        )
        bundle = None
        if files is not None:
            payload = make_tar(files)
            d = self.cfg.data_dir / "artifacts" / str(job.id)
            d.mkdir(parents=True, exist_ok=True)
            (d / "bundle.tar").write_bytes(payload)
            entries = tuple(
                art.BundleFile(
                    path=p, size=len(b), sha256=hashlib.sha256(b).hexdigest(), mode=0o644
                )
                for p, b in files.items()
            )
            bundle = CollectResult(
                state=art.READY,
                files=entries,
                total_bytes=sum(len(b) for b in files.values()),
                bundle_bytes=len(payload),
                skipped_count=0,
                bundle_sha256=hashlib.sha256(payload).hexdigest(),
                bundle_path=d / "bundle.tar",
            )
        assert self.store.finish(
            job.id, state, now=finished, exit_code=0 if state == SUCCEEDED else 1, bundle=bundle
        )
        return job.id

    def plan_job(self, doc: dict = PLAN_DOC, **kw) -> int:
        return self.role_job("release-plan", {"out/plan.json": json.dumps(doc).encode()}, **kw)

    def review_plan_job(self, doc: dict = REVIEW_PLAN_DOC, **kw) -> int:
        return self.role_job("release-review", {"review-plan.json": json.dumps(doc).encode()}, **kw)

    def post(self, action: str, body: dict, token="alice"):
        return self.req("POST", f"/api/repos/app/release/{action}", token=token, json_body=body)


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RemoteRepo:
    isolate_git_env(tmp_path, monkeypatch)
    r = build_remote(tmp_path)
    work = r.work
    (work / "scripts").mkdir()
    for name, text in (("driver.sh", DRIVER), ("old_driver.sh", OLD_DRIVER)):
        script = work / "scripts" / name
        script.write_text(text)
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    shots = work / "store" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "01-home.png").write_bytes(PNG)
    (shots / "notes.txt").write_text("not a screenshot")
    notes = work / "store" / "release_notes" / "1.0.1"
    notes.mkdir(parents=True)
    (notes / "en.txt").write_text("Bug fixes and a faster start.\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "release scripts and store copy", cwd=work)
    git("tag", "-a", "-m", "prod 1.0.0", "prod/1.0.0-180", cwd=work)
    git("push", "-q", "origin", "main", "prod/1.0.0-180", cwd=work)
    main = git("rev-parse", "HEAD", cwd=work)
    return RemoteRepo(**{**r.__dict__, "main": main})


@pytest.fixture
def srv(tmp_path: Path, remote: RemoteRepo):
    s = ReleaseServer(tmp_path, remote)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def open_srv(srv: ReleaseServer, monkeypatch):
    srv.open_gate(monkeypatch)
    return srv


def code_of(resp) -> tuple[int, str | None]:
    status, body = resp
    return status, (body or {}).get("code")


# ── GET …/release ─────────────────────────────────────────────────────────────


def test_the_release_view_is_empty_but_answers_before_the_gate_opens(srv):
    status, body = srv.req("GET", "/api/repos/app/release")
    assert status == 200, body
    assert body == {
        "setup": {
            "required": 4,
            "present": 0,
            "verified": 0,
            "complete": False,
            "missing": [
                "GH_TOKEN",
                "AuthKey.p8",
                "upload-keystore.jks",
                "review_information/demo_user.txt",
                "review_information/demo_password.txt",
            ],
        },
        "plan": None,
        "review": {"plan": None, "result": None},
        "upload": None,
        "jobs": [],
    }
    assert srv.req("GET", "/api/repos/nope/release")[0] == 404
    assert srv.req("GET", "/api/repos/app/release/plan/x", token="alice")[0] == 404
    assert srv.req("POST", "/api/repos/app/release", token="alice", json_body={})[0] == 405
    assert srv.req("GET", "/api/repos/app/release/plan", token="alice")[0] == 405


def test_the_release_view_with_a_plan_job_reads_the_document_from_the_bundle(open_srv, remote):
    srv = open_srv
    plan_id = srv.plan_job()
    status, body = srv.req("GET", "/api/repos/app/release")
    assert status == 200, body
    print("RELEASE_JSON", json.dumps(body, ensure_ascii=False))
    assert body["setup"]["complete"] is True
    assert body["plan"] == {
        "job_id": plan_id,
        "state": "succeeded",
        "measured_at": "2026-09-17T11:56:00Z",
        "age_seconds": 240,
        "stale": False,
        "build_name": "1.0.1",
        "doc": PLAN_DOC,
    }
    assert body["review"] == {"plan": None, "result": None} and body["upload"] is None
    assert body["jobs"] == [
        {
            "id": plan_id,
            "preset": "release-plan",
            "role": "plan",
            "state": "succeeded",
            "sha": remote.main,
            "ref": "main",
            "started_at": "2026-09-17T11:53:00Z",
            "finished_at": "2026-09-17T11:55:00Z",
            "artifacts": ["plan.json"],
        }
    ]


def test_a_gone_or_broken_bundle_gives_doc_null_with_a_reason(open_srv):
    srv = open_srv
    jid = srv.plan_job()
    (srv.cfg.data_dir / "artifacts" / str(jid) / "bundle.tar").unlink()
    body = srv.req("GET", "/api/repos/app/release")[1]
    assert body["plan"]["doc"] is None
    assert body["plan"]["doc_error"] == "cannot read plan.json: FileNotFoundError"
    jid2 = srv.role_job("release-plan", {"plan.json": b"{oops"})
    body = srv.req("GET", "/api/repos/app/release")[1]
    assert body["plan"]["job_id"] == jid2 and body["plan"]["doc_error"].startswith("not valid")


def test_worker_tokens_and_method_mismatches_are_refused_on_every_release_route(open_srv):
    srv = open_srv
    secret = srv.store.add_token("build-02", admin=False, now=NOW, kind="worker")
    for method, path in [
        ("GET", "/api/repos/app/release"),
        ("GET", "/api/repos/app/release/listing"),
        ("GET", "/api/repos/app/release/github"),
        ("GET", "/api/repos/app/release/driver"),
        ("POST", "/api/repos/app/release/plan"),
        ("POST", "/api/repos/app/release/review"),
        ("POST", "/api/repos/app/release/upload"),
        ("POST", "/api/repos/app/release/listing/validate"),
        ("POST", "/api/repos/app/release/start"),
        ("POST", "/api/repos/app/release/confirm"),
        ("POST", "/api/repos/app/release/abort"),
        ("POST", "/api/repos/app/release/retry"),
    ]:
        kw = {"json_body": {}} if method == "POST" else {}
        assert srv.req(method, path, token=secret, **kw)[0] == 403, (method, path)
    for action in ("plan", "review", "upload", "start", "confirm", "abort", "retry"):
        assert srv.req("POST", f"/api/repos/app/release/{action}", json_body={})[0] == 401
    for action in ("start", "confirm", "abort", "retry"):
        assert srv.post(action, {"build_name": "1.0.1"}, token="alice")[0] == 403


def test_the_read_routes_follow_the_read_rule(tmp_path, remote):
    s = ReleaseServer(tmp_path, remote, read_auth="basic")
    try:
        for sub in ("", "/listing", "/github", "/driver"):
            assert s.req("GET", f"/api/repos/app/release{sub}")[0] == 401
            assert s.req("GET", f"/api/repos/app/release{sub}", token="alice")[0] == 200
    finally:
        s.close()


# ── 설정 게이트 ──────────────────────────────────────────────────────────────


def test_every_write_route_is_409_setup_incomplete_until_the_gate_opens(srv):
    for action, token in [
        ("plan", "alice"),
        ("review", "alice"),
        ("upload", "alice"),
        ("listing/validate", "alice"),
        ("start", "admin"),
        ("confirm", "admin"),
        ("abort", "admin"),
        ("retry", "admin"),
    ]:
        status, body = srv.post(action, {"build_name": "1.0.1"}, token=token)
        assert status == 409, (action, body)
        assert body["code"] == body["error_code"] == "setup_incomplete"
        assert body["setup"]["complete"] is False and body["setup"]["missing"]
    assert srv.store.list_jobs_by_preset(["release-plan"], 10) == []
    # 읽기 넷은 게이트와 무관하다
    for sub in ("", "/listing", "/github", "/driver"):
        assert srv.req("GET", f"/api/repos/app/release{sub}")[0] == 200


# ── plan ──────────────────────────────────────────────────────────────────────


def test_plan_submits_the_plan_preset_through_the_job_path_with_build_name_only(open_srv, remote):
    srv = open_srv
    status, body = srv.post("plan", {"build_name": "1.0.1"})
    assert status == 202, body
    assert body == {"job_id": 1, "joined": False, "state": "queued", "sha": remote.main}
    job = srv.store.get_job(1)
    assert job.preset == "release-plan" and job.inputs == {"build_name": "1.0.1"}
    assert job.source.mode == "git_ref" and job.source.ref == "main"
    assert job.requester.name == "alice-laptop" and job.requester.label == "store:alice-laptop"
    # 같은 잡을 다시 보내면 합류한다(기존 규칙)
    status, body = srv.post("plan", {"build_name": "1.0.1"}, token="bob")
    assert status == 202 and body["job_id"] == 1 and body["joined"] is True
    status, body = srv.post("plan", {"build_name": "1.0.2", "ref": "dev"})
    assert status == 202 and body["job_id"] == 2 and body["sha"] == remote.dev
    view = srv.req("GET", "/api/repos/app/release")[1]
    assert [(r["id"], r["state"], r["artifacts"]) for r in view["jobs"]] == [
        (2, "queued", []),
        (1, "queued", []),
    ]
    assert view["plan"] is None


def test_plan_needs_a_build_name_and_a_resolvable_ref(open_srv):
    srv = open_srv
    assert srv.post("plan", {})[0] == 400
    assert srv.post("plan", {"build_name": "../x"})[0] == 400
    assert srv.post("plan", {"build_name": "1.0.1", "ref": "no-such-branch"})[0] == 502
    assert srv.req("POST", "/api/repos/app/release/plan", token="alice", json_body=[])[0] == 400


def test_a_role_without_a_preset_or_with_an_unsafe_preset_is_409(open_srv):
    srv = open_srv
    profile = srv.cfg.repos[0].release
    srv.cfg.repos[0].release = parse_release_profile(
        "app", {**PROFILE, "presets": {"plan": "gone", "upload": "release-upload"}}
    )
    assert code_of(srv.post("plan", {"build_name": "1.0.1"})) == (409, "role_not_configured")
    assert code_of(srv.post("review", {"build_name": "1.0.1"})) == (409, "role_not_configured")
    srv.cfg.repos[0].release = parse_release_profile(
        "app", {**PROFILE, "presets": {**PROFILE["presets"], "review": "release-review-unsafe"}}
    )
    status, body = srv.post("review", {"build_name": "1.0.1", "mode": "plan"})
    assert (status, body["code"]) == (409, "unsafe_preset") and body["inputs"] == ["rollout"]
    srv.cfg.repos[0].release = profile
    assert srv.store.list_jobs_by_preset(["release-review", "release-review-unsafe"], 10) == []


# ── review ────────────────────────────────────────────────────────────────────


def test_review_in_plan_mode_sends_the_contract_inputs_and_keeps_preset_defaults(open_srv):
    srv = open_srv
    status, body = srv.post("review", {"build_name": "1.0.1", "mode": "plan", "platform": "ios"})
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.preset == "release-review"
    assert job.inputs == {
        "build_name": "1.0.1",
        "confirm_build_number": "",
        "mode": "plan",
        "platform": "ios",
        "play_managed_publishing": "not-checked",
        "listing": "notes-only",
        "phased": "1",
        "extra": "kept",
    }
    assert srv.post("review", {"build_name": "1.0.1", "mode": "publish"})[0] == 400
    assert srv.post("review", {"build_name": "1.0.1", "phased": "yes"})[0] == 400


SUBMIT = {
    "build_name": "1.0.1",
    "mode": "submit",
    "platform": "both",
    "confirm_build_number": "181",
    "play_managed_publishing": "confirmed-on",
}


def test_submit_needs_an_admin_token_whatever_the_ui_sends(open_srv):
    srv = open_srv
    srv.plan_job()
    srv.review_plan_job()
    status, body = srv.post("review", SUBMIT, token="alice")
    assert status == 403 and body["code"] == "admin_required"
    assert srv.store.list_jobs_by_preset(["release-review"], 10)[0].state == "succeeded"


def test_submit_needs_a_typed_build_number(open_srv):
    srv = open_srv
    srv.plan_job()
    srv.review_plan_job()
    body = {**SUBMIT, "confirm_build_number": ""}
    assert code_of(srv.post("review", body, token="admin")) == (409, "build_number_mismatch")
    del body["confirm_build_number"]
    assert code_of(srv.post("review", body, token="admin")) == (409, "build_number_mismatch")


def test_submit_with_auto_takes_the_plans_number_and_still_needs_a_fresh_green_plan(open_srv):
    """«빌드 번호 자동» — `confirm_build_number: "auto"` 는 플랜의 `n` 을 그대로 쓴다. 플랜이 없거나
    낡았으면 자동이어도 같은 409 — 서버는 번호를 지어내지 않는다."""
    srv = open_srv
    body = {**SUBMIT, "confirm_build_number": "auto"}
    assert code_of(srv.post("review", body, token="admin")) == (409, "review_plan_required")
    srv.plan_job()
    srv.review_plan_job()
    status, resp = srv.post("review", body, token="admin")
    assert status == 202, resp
    job = srv.store.get_job(resp["job_id"])
    assert job.inputs["confirm_build_number"] == "181"
    loud = {**body, "confirm_build_number": "AUTO "}
    assert srv.post("review", loud, token="admin")[0] == 202
    srv.plan_job({**PLAN_DOC, "n": None})
    assert code_of(srv.post("review", body, token="admin")) == (409, "build_number_mismatch")


def test_submit_needs_a_succeeded_review_plan_for_the_same_build(open_srv):
    srv = open_srv
    srv.plan_job()
    assert code_of(srv.post("review", SUBMIT, token="admin")) == (409, "review_plan_required")
    srv.review_plan_job({**REVIEW_PLAN_DOC, "build_name": "1.0.0"})
    assert code_of(srv.post("review", SUBMIT, token="admin")) == (409, "review_plan_required")
    srv.review_plan_job(state="failed")
    assert code_of(srv.post("review", SUBMIT, token="admin")) == (409, "review_plan_required")


def test_submit_refuses_a_stale_review_plan(open_srv):
    srv = open_srv
    srv.plan_job()
    srv.review_plan_job({**REVIEW_PLAN_DOC, "measured_at": "2026-09-17T11:29:00Z"})
    status, body = srv.post("review", SUBMIT, token="admin")
    assert (status, body["code"]) == (409, "review_plan_stale") and body["age_seconds"] == 1860
    view = srv.req("GET", "/api/repos/app/release")[1]
    assert view["review"]["plan"]["stale"] is True


def test_submit_refuses_a_blocked_review_plan(open_srv):
    srv = open_srv
    srv.plan_job()
    srv.review_plan_job({**REVIEW_PLAN_DOC, "plan_verdict": "blocked"})
    assert code_of(srv.post("review", SUBMIT, token="admin")) == (409, "review_plan_blocked")


def test_android_submit_needs_managed_publishing_confirmed_in_this_submission(open_srv):
    srv = open_srv
    srv.plan_job()
    srv.review_plan_job()
    for platform in ("both", "android"):
        body = {**SUBMIT, "platform": platform, "play_managed_publishing": "not-checked"}
        assert code_of(srv.post("review", body, token="admin")) == (
            409,
            "managed_publishing_unconfirmed",
        )
        body.pop("play_managed_publishing")
        assert code_of(srv.post("review", body, token="admin")) == (
            409,
            "managed_publishing_unconfirmed",
        )
    # iOS 만이면 그 확인은 필요 없다
    body = {**SUBMIT, "platform": "ios", "play_managed_publishing": "not-checked"}
    assert srv.post("review", body, token="admin")[0] == 202
    assert srv.post("review", {**SUBMIT, "play_managed_publishing": "yes"}, token="admin")[0] == 400


def test_submit_compares_the_typed_number_with_the_latest_plan(open_srv):
    srv = open_srv
    srv.review_plan_job()
    assert code_of(srv.post("review", SUBMIT, token="admin")) == (409, "plan_required")
    srv.plan_job({**PLAN_DOC, "measured_at": "2026-09-17T11:00:00Z"})
    assert code_of(srv.post("review", SUBMIT, token="admin")) == (409, "plan_stale")
    srv.plan_job()  # 새 계획이 최신이다
    body = {**SUBMIT, "confirm_build_number": "182"}
    assert code_of(srv.post("review", body, token="admin")) == (409, "build_number_mismatch")
    srv.plan_job({**PLAN_DOC, "n": None})
    assert code_of(srv.post("review", SUBMIT, token="admin")) == (409, "build_number_mismatch")
    srv.plan_job()
    status, body = srv.post("review", SUBMIT, token="admin")
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.inputs["mode"] == "submit" and job.inputs["confirm_build_number"] == "181"
    assert job.inputs["play_managed_publishing"] == "confirmed-on"
    assert job.requester.name == "macmini-admin"


# ── upload ────────────────────────────────────────────────────────────────────


def test_rehearsal_takes_a_client_token_and_upload_takes_admin_plus_the_plan(open_srv):
    srv = open_srv
    status, body = srv.post("upload", {"build_name": "1.0.1", "platform": "android"})
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.inputs == {
        "build_name": "1.0.1",
        "confirm_build_number": "",
        "mode": "rehearsal",
        "platform": "android",
        "android_track": "internal",
    }
    up = {"build_name": "1.0.1", "mode": "upload", "confirm_build_number": "181"}
    assert code_of(srv.post("upload", up)) == (403, "admin_required")
    assert code_of(srv.post("upload", up, token="admin")) == (409, "plan_required")
    srv.plan_job({**PLAN_DOC, "measured_at": "2026-09-17T11:00:00Z"})
    assert code_of(srv.post("upload", up, token="admin")) == (409, "plan_stale")
    srv.plan_job(state="failed")
    assert code_of(srv.post("upload", up, token="admin")) == (409, "plan_required")
    srv.plan_job()
    bad = {**up, "confirm_build_number": "180"}
    assert code_of(srv.post("upload", bad, token="admin")) == (409, "build_number_mismatch")
    assert code_of(srv.post("upload", {**up, "confirm_build_number": ""}, token="admin")) == (
        409,
        "build_number_mismatch",
    )
    status, body = srv.post("upload", {**up, "android_track": "beta"}, token="admin")
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.inputs["mode"] == "upload" and job.inputs["confirm_build_number"] == "181"
    status, resp = srv.post("upload", {**up, "confirm_build_number": "auto"}, token="admin")
    assert status == 202, resp
    assert srv.store.get_job(resp["job_id"]).inputs["confirm_build_number"] == "181"
    assert job.inputs["android_track"] == "beta"
    assert srv.post("upload", {**up, "android_track": "a b"}, token="admin")[0] == 400
    assert srv.post("upload", {"build_name": "1.0.1", "mode": "publish"})[0] == 400


# ── listing · github ──────────────────────────────────────────────────────────


def test_listing_needs_the_mirror_and_then_runs_the_profile_commands_in_a_checkout(srv, remote):
    status, body = srv.req("GET", "/api/repos/app/release/listing")
    assert status == 200 and body["sha"] is None and body["preview"] == []
    assert body["errors"] == ["the mirror has no branch 'main' — fetch the repository first"]
    main = srv.fetch()
    assert main == remote.main
    status, body = srv.req("GET", "/api/repos/app/release/listing?build_name=1.0.1")
    assert status == 200, body
    assert body == {
        "configured": True,
        "sha": remote.main,
        "preview": ["Title", "Short description"],
        "diff": ["+ new sentence", "- old sentence"],
        "release_notes": {
            "path": "store/release_notes/1.0.1/en.txt",
            "text": "Bug fixes and a faster start.\n",
        },
        "screenshots": [
            {
                "path": "store/screenshots/01-home.png",
                "bytes": len(PNG),
                "width": None,
                "height": None,
            }
        ],
        "errors": [],
    }
    checkout = srv.cfg.data_dir / "listing" / "app" / "checkout"
    assert (checkout / "scripts" / "driver.sh").exists()
    assert (checkout.parent / "checkout.sha").read_text() == remote.main
    # 브랜치가 움직이면 체크아웃도 새로
    new = remote.push_commit("more.txt", "x\n", "more")
    srv.fetch()
    body = srv.req("GET", "/api/repos/app/release/listing")[1]
    assert body["sha"] == new and (checkout / "more.txt").exists()
    assert body["release_notes"]["path"] == "store/release_notes/1.0.1/en.txt"  # `*` 로 찾는다


def test_the_listing_answer_is_remembered_briefly_and_never_across_shas(srv, remote, monkeypatch):
    """워크플랜 §14-1 — 버전 페이지가 5초마다 부르는 바람에 이 명령 둘이 빌드 머신에서 5초마다
    돌면 안 된다(진짜 스토어를 읽는 프로젝트가 있다). 같은 (저장소 · 체크아웃 sha · build_name)
    이면 `LISTING_CACHE_TTL` 초 동안 같은 답을 돌려준다. **다른 sha 의 답은 절대 나가지 않고**,
    미러가 움직이면 그 저장소의 기억은 통째로 버린다."""
    from remote_ci_monitor import server as server_mod

    srv.fetch()
    ran: list[tuple[str, ...]] = []
    real = srv.app._run_listing

    def counting(argv, cwd, env):
        ran.append(tuple(argv))
        return real(argv, cwd, env)

    monkeypatch.setattr(srv.app, "_run_listing", counting)
    first = srv.req("GET", "/api/repos/app/release/listing?build_name=1.0.1")[1]
    assert len(ran) == 2 and first["preview"] == ["Title", "Short description"]
    assert srv.req("GET", "/api/repos/app/release/listing?build_name=1.0.1")[1] == first
    assert len(ran) == 2  # 기억에서 나갔다 — 명령은 다시 돌지 않았다
    # build_name 이 다르면 다른 키다(릴리스 노트를 그 이름으로 찾는다)
    other = srv.req("GET", "/api/repos/app/release/listing")[1]
    assert len(ran) == 4 and other["sha"] == first["sha"]
    assert len(srv.app._listing_cache) == 2
    # 미러가 움직이면 그 저장소의 기억은 버린다 — 옛 sha 의 답이 남아 있을 자리가 없다
    new = remote.push_commit("more.txt", "x\n", "more")
    srv.fetch()
    fresh = srv.req("GET", "/api/repos/app/release/listing?build_name=1.0.1")[1]
    assert len(ran) == 6 and fresh["sha"] == new
    assert list(srv.app._listing_cache) == [("app", new, "1.0.1")]
    # 기억을 붙잡아 두는 것은 TTL 뿐이다 — 0 이면 매번 다시 돈다
    monkeypatch.setattr(server_mod, "LISTING_CACHE_TTL", 0.0)
    assert srv.req("GET", "/api/repos/app/release/listing?build_name=9.9.9")[1]["sha"] == new
    assert len(ran) == 8
    assert srv.req("GET", "/api/repos/app/release/listing?build_name=9.9.9")[1]["sha"] == new
    assert len(ran) == 10


def test_listing_ref_reads_the_copy_from_that_branch(srv, remote):
    """`listing.ref = "dev"` — dev → main 으로 내보내는 프로젝트는 릴리스에 실릴 문안이 dev 에 있다.
    체크아웃은 그 브랜치의 sha 로 만들고, 프로파일 JSON 의 listing.ref 는 그 이름이다."""
    srv.cfg.repos[0].release = parse_release_profile(
        "app", {**PROFILE, "listing": {**PROFILE["listing"], "ref": "dev"}}
    )
    srv.fetch()
    body = srv.req("GET", "/api/repos/app/release/listing?build_name=1.0.1")[1]
    assert body["sha"] == remote.dev and body["release_notes"] is None, body  # dev 엔 문안이 없다
    dev = remote.push_branch("dev", "store/release_notes/1.0.1/en.txt", "From dev.\n", "dev notes")
    srv.fetch()
    body = srv.req("GET", "/api/repos/app/release/listing?build_name=1.0.1")[1]
    assert body["sha"] == dev and body["release_notes"]["text"] == "From dev.\n"
    assert (srv.cfg.data_dir / "listing" / "app" / "checkout.sha").read_text() == dev
    profile = srv.req("GET", "/api/repos/app")[1]["profile"]
    assert profile["listing"]["ref"] == "dev"
    srv.cfg.repos[0].release = parse_release_profile("app", PROFILE)
    assert srv.req("GET", "/api/repos/app")[1]["profile"]["listing"]["ref"] == "main"


def test_listing_without_a_profile_section_is_configured_false(srv):
    srv.cfg.repos[0].release = parse_release_profile(
        "app", {k: v for k, v in PROFILE.items() if k != "listing"}
    )
    assert srv.req("GET", "/api/repos/app/release/listing")[1] == {"configured": False}
    assert srv.req("GET", "/api/repos/app/release/listing/file?path=x.png")[0] == 404


def test_listing_file_serves_only_screenshot_matches_as_images(srv):
    srv.fetch()
    status, headers, data = srv.req(
        "GET", "/api/repos/app/release/listing/file?path=store/screenshots/01-home.png", raw=True
    )
    assert status == 200 and data == PNG and headers["Content-Type"] == "image/png"
    base = "/api/repos/app/release/listing/file?path="
    assert srv.req("GET", base + "store/screenshots/notes.txt")[0] == 404
    assert srv.req("GET", base + "scripts/driver.sh")[0] == 404
    assert srv.req("GET", base + "store/screenshots/missing.png")[0] == 404
    assert srv.req("GET", base + "../etc/x.png")[0] == 400
    assert srv.req("GET", base + "/etc/x.png")[0] == 400
    assert srv.req("GET", base)[0] == 400


def test_listing_validate_substitutes_version_and_build(open_srv):
    srv = open_srv
    srv.fetch()
    status, body = srv.post("listing/validate", {"build_name": "1.0.1", "build": "181"})
    assert status == 200 and body == {"ok": True, "lines": ["v=1.0.1 b=181"], "exit": 0}
    status, body = srv.post("listing/validate", {"build_name": "1.0.1"})
    assert status == 200 and body["ok"] is False and body["exit"] == 1
    assert body["lines"] == ["v=1.0.1 b="] and body["error"] == "sh exited 1"
    assert srv.post("listing/validate", {"build_name": "1.0.1", "build": "x"})[0] == 400


def test_a_failing_or_slow_listing_command_is_reported_not_fatal(srv, monkeypatch):
    srv.fetch()
    srv.cfg.repos[0].release = parse_release_profile(
        "app",
        {
            **PROFILE,
            "listing": {
                "preview": ["sh", "-c", "echo half; echo boom >&2; exit 3"],
                "diff": ["no-such-cmd-xyz"],
            },
        },
    )
    body = srv.req("GET", "/api/repos/app/release/listing")[1]
    assert body["preview"] == ["half"] and body["diff"] == []
    assert body["errors"] == [
        "sh exited 3: boom",
        "no-such-cmd-xyz could not start: FileNotFoundError",
    ]
    assert body["release_notes"] is None and body["screenshots"] == []


def test_github_reads_the_mirror_only_and_leaves_prs_for_later(srv, remote):
    assert srv.req("GET", "/api/repos/app/release/github")[1] == {
        "log": [],
        "tags": [],
        "prs": None,
    }
    srv.fetch()
    body = srv.req("GET", "/api/repos/app/release/github")[1]
    assert [c["subject"] for c in body["log"]] == [
        "release scripts and store copy",
        "third",
        "second",
        "first",
    ]
    assert body["log"][0]["sha"] == remote.main and body["log"][0]["author"] == "rcm-test"
    assert body["log"][0]["at"].startswith("20")
    assert [t["name"] for t in body["tags"]] == ["prod/1.0.0-180"]  # v1.0.0 · lw 는 접두사 밖
    assert body["tags"][0]["at"] and body["prs"] is None


# ── 드라이버 ──────────────────────────────────────────────────────────────────


def wait_run(srv: ReleaseServer, release_id: int) -> dict:
    """회차가 닫힐 때까지. `driver.wait` 는 **모르는** 실행에 바로 True 를 주는데, 행이 생긴 직후
    (아직 Popen 전)가 그렇다 — 그래서 대장이 닫히는 것까지 본다. 안 그러면 로그·exit_code 를
    도는 중에 읽는 경쟁이 남는다."""
    deadline = time.monotonic() + 20
    while True:
        assert srv.app.driver.wait(release_id, timeout=15)
        row = srv.store.get_release(release_id)
        assert row is not None, f"no release #{release_id}"
        if row["finished_at"] is not None or time.monotonic() > deadline:
            assert row["finished_at"] is not None, f"release #{release_id} did not finish"
            return row
        time.sleep(0.02)


def test_driver_view_before_any_run_and_without_a_mirror(open_srv):
    srv = open_srv
    body = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert body == {
        "configured": True,
        "running": False,
        "release_id": None,
        "kind": None,
        "build_name": None,
        "started_at": None,
        "started_by": None,
        "pid": None,
        "exit_code": None,
        "confirmed_n": None,
        "auto_n": False,
        "log_tail": [],
        "plan_n": None,
        "status": None,
        "status_error": "the mirror has no branch 'main' — fetch the repository first",
        "stages": None,
        "knows_version_stage": False,
    }
    assert code_of(srv.post("start", {"build_name": "1.0.1"}, token="admin")) == (
        409,
        "mirror_missing",
    )
    srv.fetch()
    body = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert body["status"] == [
        "driver --status",
        "server=none token=",
        "stages: V S0 S1 S2 S3 S4 S5 S6 S7 S8",
        "stage: S1 planned",
    ]
    assert body["status_error"] is None and body["running"] is False
    # 능력 신호(계약 §5 · 워크플랜 §13-2) — `stages:` 줄을 그대로 읽는다
    assert body["stages"] == ["V", "S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
    assert body["knows_version_stage"] is True
    # 버전을 아는 회차가 있으면 --status 에 --build-name 이 붙는다(없이 부르면 드라이버가 되묻는다)
    srv.plan_job()
    body = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert body["status"][0] == "driver --status --build-name 1.0.1", body["status"]


def test_start_runs_the_driver_detached_with_an_internal_token_that_is_revoked_after(
    open_srv, monkeypatch
):
    srv = open_srv
    srv.fetch()
    real = srv.store.add_token
    monkeypatch.setattr(
        srv.store, "add_token", lambda name, **kw: real(name, **kw) and "SECRET-TOKEN-VALUE-XYZ"
    )
    status, body = srv.post(
        "start", {"build_name": "1.0.1", "android_track": "beta"}, token="admin"
    )
    assert status == 202, body
    assert body["release_id"] == 1 and body["build_name"] == "1.0.1" and body["pid"] > 0
    row = wait_run(srv, 1)
    assert row["exit_code"] == 2 and row["started_by"] == "macmini-admin"
    assert row["android_track"] == "beta" and row["dry_run"] is False
    assert row["log_path"] == str(srv.cfg.data_dir / "driver" / "app" / "1.0.1.log")
    tokens = {t.name: t for t in srv.store.list_tokens()}
    assert tokens["store-driver:app:1"].kind == "client"
    assert tokens["store-driver:app:1"].admin is False
    assert tokens["store-driver:app:1"].revoked_at is not None
    assert srv.app.authenticate("Bearer SECRET-TOKEN-VALUE-XYZ") is None
    status, body = srv.req("GET", "/api/repos/app/release/driver")
    assert status == 200
    assert body["running"] is False and body["exit_code"] == 2 and body["plan_n"] == 181
    assert body["release_id"] == 1 and body["kind"] == "start" and body["pid"] == row["pid"]
    assert body["started_at"] == "2026-09-17T12:00:00Z" and body["started_by"] == "macmini-admin"
    assert body["log_tail"][-2:] == ["plan: N = 181", ""] or body["log_tail"][-1] == "plan: N = 181"
    assert any(
        "driver --build-name 1.0.1 --android-track beta" in line for line in body["log_tail"]
    )
    assert any(
        "server=http://127.0.0.1:" in line and "token=set" in line for line in body["log_tail"]
    )
    everything = json.dumps(body) + " ".join(srv.server_log)
    assert "SECRET-TOKEN-VALUE-XYZ" not in everything
    assert "SECRET-TOKEN-VALUE-XYZ" not in Path(row["log_path"]).read_text()
    assert srv.post("start", {"build_name": "1.0.1", "dry_run": "yes"}, token="admin")[0] == 400


def test_one_running_release_per_repo(open_srv):
    srv = open_srv
    srv.fetch()
    (srv.secrets_root / "hold").touch()
    status, body = srv.post("start", {"build_name": "1.0.1"}, token="admin")
    assert status == 202, body
    view = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert view["running"] is True and view["exit_code"] is None and view["plan_n"] is None
    status, again = srv.post("start", {"build_name": "1.0.2"}, token="admin")
    assert (status, again["code"]) == (409, "release_running") and again["release_id"] == 1
    assert code_of(srv.post("abort", {}, token="admin")) == (409, "release_running")
    (srv.secrets_root / "hold").unlink()
    wait_run(srv, 1)
    assert srv.req("GET", "/api/repos/app/release/driver")[1]["running"] is False
    assert srv.post("start", {"build_name": "1.0.2"}, token="admin")[0] == 202


def test_confirm_forwards_only_the_number_the_log_showed_and_the_human_typed(open_srv):
    srv = open_srv
    srv.fetch()
    body = {"build_name": "1.0.1", "build_number": "181"}
    assert code_of(srv.post("confirm", body, token="admin")) == (409, "release_required")
    srv.post("start", {"build_name": "1.0.1", "dry_run": True}, token="admin")
    wait_run(srv, 1)
    for typed in ("180", "", "abc", None):
        b = (
            {"build_name": "1.0.1", "build_number": typed}
            if typed is not None
            else {"build_name": "1.0.1"}
        )
        assert code_of(srv.post("confirm", b, token="admin")) == (409, "build_number_mismatch")
    status, resp = srv.post("confirm", body, token="admin")
    assert status == 202 and resp["release_id"] == 2
    row = wait_run(srv, 2)
    assert row["kind"] == "confirm" and row["confirmed_n"] == 181
    assert row["confirmed_by"] == "macmini-admin" and row["exit_code"] == 0 and row["dry_run"]
    log = Path(row["log_path"]).read_text()
    assert "confirmed --build-name 1.0.1 --dry-run --confirm-build-number 181" in log
    view = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert view["kind"] == "confirm" and view["confirmed_n"] == 181 and view["exit_code"] == 0
    assert srv.store.count_releases() == 2
    assert all(t.revoked_at for t in srv.store.list_tokens() if t.name.startswith("store-driver"))


def test_confirm_with_auto_forwards_the_number_the_log_showed(open_srv):
    srv = open_srv
    srv.fetch()
    srv.post("start", {"build_name": "1.0.1", "dry_run": True}, token="admin")
    wait_run(srv, 1)
    auto = {"build_name": "1.0.1", "build_number": "auto"}
    status, resp = srv.post("confirm", auto, token="admin")
    assert status == 202, resp
    row = wait_run(srv, 2)
    assert row["kind"] == "confirm" and row["confirmed_n"] == 181 and row["auto_n"] is False
    assert "--confirm-build-number 181" in Path(row["log_path"]).read_text()


def test_a_round_started_with_build_number_auto_continues_by_itself_after_exit_2(open_srv):
    """«빌드 번호 자동» 으로 시작한 회차: 드라이버가 `plan: N = 181` 을 찍고 exit 2 로 물으면
    서버가 사람 없이 `--confirm-build-number 181` 로 이어 달린다. 대장에는 두 행(start ·
    confirm), confirm 의 started_by 는 「<시작한 사람> (auto)」. 자동이 아닌 회차는 그대로 사람
    차례(exit 2 에서 멈춤)."""
    srv = open_srv
    srv.fetch()
    typed = {"build_name": "1.0.1", "build_number": "181"}
    assert srv.post("start", typed, token="admin")[0] == 400
    status, resp = srv.post(
        "start", {"build_name": "1.0.1", "dry_run": True, "build_number": "auto"}, token="admin"
    )
    assert status == 202, resp
    first = wait_run(srv, 1)
    assert first["exit_code"] == 2 and first["auto_n"] is True
    deadline = time.monotonic() + 15
    while srv.store.count_releases() < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert srv.store.count_releases() == 2
    second = wait_run(srv, 2)
    assert second["kind"] == "confirm" and second["confirmed_n"] == 181
    assert second["started_by"] == "macmini-admin (auto)" and second["exit_code"] == 0
    assert second["dry_run"] is True and second["auto_n"] is True
    assert (
        "confirmed --build-name 1.0.1 --dry-run --confirm-build-number 181"
        in Path(second["log_path"]).read_text()
    )
    view = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert view["kind"] == "confirm" and view["auto_n"] is True and view["running"] is False
    # 자동이 아니면 exit 2 에서 멈춘다
    assert srv.post("start", {"build_name": "1.0.2", "dry_run": True}, token="admin")[0] == 202
    wait_run(srv, 3)
    time.sleep(0.3)
    assert srv.store.count_releases() == 3


def test_abort_and_retry_rerun_the_driver_with_the_flag(open_srv):
    srv = open_srv
    srv.fetch()
    assert code_of(srv.post("retry", {}, token="admin")) == (409, "release_required")
    srv.post("start", {"build_name": "1.0.1"}, token="admin")
    wait_run(srv, 1)
    assert srv.post("abort", {}, token="admin")[0] == 202
    assert wait_run(srv, 2)["kind"] == "abort"
    assert srv.post("retry", {"build_name": "1.0.1"}, token="admin")[0] == 202
    assert wait_run(srv, 3)["kind"] == "retry"
    log = Path(srv.store.get_release(1)["log_path"]).read_text()
    assert "driver --build-name 1.0.1 --abort" in log and "driver --build-name 1.0.1 --retry" in log
    assert log.count("--- rcm driver") == 3


def test_a_profile_without_a_driver_or_with_a_missing_one(open_srv):
    srv = open_srv
    srv.fetch()
    srv.cfg.repos[0].release = parse_release_profile(
        "app", {**PROFILE, "driver": "scripts/nope.sh"}
    )
    assert code_of(srv.post("start", {"build_name": "1.0.1"}, token="admin")) == (
        409,
        "driver_missing",
    )
    view = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert view["status"] is None and "not an executable file" in view["status_error"]
    srv.cfg.repos[0].release = parse_release_profile(
        "app", {k: v for k, v in PROFILE.items() if k != "driver"}
    )
    assert srv.req("GET", "/api/repos/app/release/driver")[1] == {"configured": False}
    for action in ("start", "confirm", "abort", "retry"):
        assert code_of(srv.post(action, {"build_name": "1.0.1"}, token="admin")) == (
            409,
            "no_driver",
        )
    assert srv.store.count_releases() == 0


def test_the_driver_view_closes_an_orphan_run_after_a_restart(open_srv):
    """서버가 재기동되면 감시 스레드가 없다 — 보기(GET)가 pid 를 확인해 대장을 닫고 토큰을
    폐기한다."""
    srv = open_srv
    srv.fetch()
    rid = srv.store.create_release(
        repo="app",
        build_name="0.9.0",
        kind="start",
        started_by="macmini-admin",
        now=NOW - timedelta(hours=1),
        log_path=str(srv.cfg.data_dir / "driver" / "app" / "0.9.0.log"),
    )
    srv.store.add_token("store-driver:app:1", admin=False, now=NOW, kind="client")
    srv.store.set_release_started(rid, pid=2**22 + 11, token_name="store-driver:app:1")
    (srv.cfg.data_dir / "driver" / "app").mkdir(parents=True)
    (srv.cfg.data_dir / "driver" / "app" / f"{rid}.exit").write_text("4")
    view = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert view["running"] is False and view["exit_code"] == 4 and view["log_tail"] == []
    # 이름으로 찾는다 — 순서는 created_at 이라 NOW(고정) 와 실제 시각의 앞뒤에 따라 달라진다
    orphan = [t for t in srv.store.list_tokens() if t.name == "store-driver:app:1"]
    assert orphan and orphan[0].revoked_at is not None
    assert srv.post("start", {"build_name": "1.0.1"}, token="admin")[0] == 202
