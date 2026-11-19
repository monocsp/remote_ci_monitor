"""CLI(M5p P3) — `rcm run --keep` · 프리셋 keep 의 보관 줄 · `rcm keep N` · `rcm shelf` ·
`rcm shelf rm`.

명세는 docs/m5p-workplan.md §4.1 · §4.8 · §15.1 · §15.6(앞 절보다 이긴다), 시나리오는
docs/m5p-test-scenarios-c.md 의 cli 줄. **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

서버는 `fake_rcm_http.FakeRcm` 이다. 서버 쪽(P2)은 같은 P1 위에서 병렬로 만들어지므로, 응답 모양은
명세 §4.6 · §4.8 · §15.1 · §15.5 · §15.6 이 적은 그대로 손으로 못 박는다. 진짜 서버와 진짜 CLI 를
잇는 시험은 `tests/test_e2e_m5p_shelf.py` 이고 P2 가 머지된 뒤에 돈다.

가정(P2 와 대조할 것):
- `GET /api/health` 에 `shelf` 키(값은 `shelf_storage` 요약 또는 null)가 있으면 keep 을 안다.
- `/api/status.presets[]` 에 `has_artifacts` · `keep`.
- `POST /jobs` 응답에 `keep` 과 `shelf_url`(keep 일 때만).
- `GET /jobs/N` 에 `keep` 과 `shelf: {state, url, reason_code} | null`.
- `POST /jobs/N/keep` 본문 `{job, result, shelf}`(오류 응답도 같은 키 + `error`).
- `GET /api/shelf` 는 `{items: [...]}`, 항목은 `job_id · preset · branch · state · file_count ·
  total_bytes · last_used_at · stale_at · delete_at · reason_code` (시각은 ISO 또는 epoch 초).
- `DELETE /shelf/N` 은 200 `{deleted: true}`, 410 `{state: "deleted", deleted_at}`, 409 `{code}`.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, free_port
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor.cli import build_parser, main
from remote_ci_monitor.client import Client, ClientError
from test_cli_m1 import last_json

JOB = 412
SUMMARY = {
    "enabled": True,
    "stored_bytes": 12_300_000,
    "limit_bytes": 53_687_091_200,
    "items": {"active": 3, "stale": 1, "pending": 0, "failed": 0},
    "last_sweep_at": None,
    "error_code": None,
}


def preset(
    name: str, *, modes: tuple[str, ...] = ("git_ref",), has_artifacts: bool = True, keep=False
) -> dict[str, Any]:
    return {
        "name": name,
        "source_modes": list(modes),
        "inputs": [],
        "pool": "default",
        "has_artifacts": has_artifacts,
        "keep": keep,
    }


PRESETS = [
    preset("shelfopt"),
    preset("shelfgold", keep=True),
    preset("shelftree", modes=("tree",)),
    preset("plain", has_artifacts=False),
]


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def human(err: str) -> list[str]:
    """stderr 의 사람용 줄(진행 줄 · `rcm:` 오류 줄 포함)."""
    return [ln for ln in err.splitlines() if ln.strip()]


class Shelf:
    """가짜 서버 하나와 그 위의 잡 하나. 잡 문서는 `docs` 차례대로 내준다(마지막 것은 계속)."""

    def __init__(self, fake: FakeRcm):
        self.fake = fake
        self.url = fake.url
        self.shelf_url = f"{fake.url}/shelf/{JOB}/"
        self.job_url = f"{fake.url}/#/jobs/{JOB}"
        self.docs: list[dict[str, Any]] = []
        self.health: dict[str, Any] = {"ok": True, "version": __version__, "shelf": dict(SUMMARY)}
        self.presets = list(PRESETS)
        self.submit_resp: dict[str, Any] | None = None
        fake.route("GET", "/api/health", lambda _b: self.health)
        fake.route("GET", "/api/whoami", {"name": "alice", "admin": False})
        fake.route("GET", "/api/status", lambda _b: self.status())
        fake.route("POST", "/jobs", lambda body: self.submitted(body))
        fake.route("GET", f"/jobs/{JOB}", lambda _b: self.next_doc())
        fake.route("PUT", f"/jobs/{JOB}/tree", {"state": "queued"})

    def status(self) -> dict[str, Any]:
        return {
            "presets": self.presets,
            "display_timezone": "Asia/Seoul",
            "server": {"shelf_storage": self.health.get("shelf")},
        }

    def submitted(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.submit_resp is not None:
            return self.submit_resp
        resp: dict[str, Any] = {
            "job_id": JOB,
            "state": "queued",
            "joined": False,
            "url": self.job_url,
            "sha": "0123456789abcdef0123456789abcdef01234567",
        }
        keep = bool(body.get("keep")) or any(
            p["name"] == body.get("preset") and p.get("keep") for p in self.presets
        )
        resp["keep"] = keep
        if keep:
            resp["shelf_url"] = self.shelf_url
        return resp

    def next_doc(self) -> dict[str, Any]:
        assert self.docs, "the test did not script the job documents"
        return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]

    def job(
        self,
        shelf_state: str | None,
        *,
        state: str = "succeeded",
        keep: bool = True,
        reason: str | None = None,
        url: bool = True,
        **extra: Any,
    ) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "id": JOB,
            "state": state,
            "preset": "shelfopt",
            "exit_code": 0 if state == "succeeded" else 1,
            "url": self.job_url,
            "keep": keep,
            "shelf": None,
        }
        if keep and shelf_state is not None:
            doc["shelf"] = {
                "state": shelf_state,
                "url": self.shelf_url if url else None,
                "reason_code": reason,
            }
        doc.update(extra)
        return doc

    def submits(self) -> list[Any]:
        return self.fake.calls("POST", "/jobs")


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli_mod, "SHELF_WAIT_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(cli_mod, "SHELF_POLL_SECONDS", 0.02, raising=False)


@pytest.fixture
def srv(env, monkeypatch):
    fake = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", fake.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    yield Shelf(fake)
    fake.close()


def refuse_network(monkeypatch) -> dict[str, list]:
    seen: dict[str, list] = {"snapshot": [], "submit": []}

    def no_snapshot(*args, **kwargs):
        seen["snapshot"].append(args)
        raise AssertionError("rcm run must not snapshot before the server said it knows --keep")

    def no_submit(self, *args, **kwargs):
        seen["submit"].append(args)
        raise AssertionError("rcm run must not submit before the server said it knows --keep")

    monkeypatch.setattr("remote_ci_monitor.cli.make_snapshot", no_snapshot)
    monkeypatch.setattr(Client, "submit", no_submit)
    return seen


def run_keep(capsys, *extra: str, preset_name: str = "shelfopt") -> tuple[int, str, str]:
    return run(capsys, ["run", preset_name, "--ref", "main", *extra])


# ── 파서 ─────────────────────────────────────────────────────────────────────


def test_run_takes_keep_and_defaults_to_off():
    assert build_parser().parse_args(["run", "x", "--keep"]).keep is True
    assert build_parser().parse_args(["run", "x"]).keep is False


def test_keep_and_shelf_commands_parse():
    a = build_parser().parse_args(["keep", "412", "--json"])
    assert a.job == 412 and a.json is True
    b = build_parser().parse_args(["shelf", "--state", "stale", "--preset", "gold", "--json"])
    assert b.state == "stale" and b.preset == "gold" and b.json is True
    c = build_parser().parse_args(["shelf", "rm", "412"])
    assert c.job == 412
    with pytest.raises(SystemExit):
        build_parser().parse_args(["shelf", "--state", "deleted"])


@pytest.mark.parametrize("argv", [["keep", "--help"], ["shelf", "--help"], ["shelf", "rm", "-h"]])
def test_new_commands_have_english_help(capsys, argv):
    code, out, _ = run(capsys, argv)
    assert code == 0, out
    assert not re.search(r"[가-힣]", out), out


# ── rcm run --keep: 베스트 (CB1 · CB2 · CB3) ──────────────────────────────────


def test_cb1_run_keep_prints_the_shelf_link_last_and_the_json_carries_it(srv, capsys):
    srv.docs = [srv.job("active")]
    code, out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert human(err)[-1] == f"shelf: {srv.shelf_url}", err
    doc = last_json(out)
    assert doc["shelf"] == {"state": "active", "url": srv.shelf_url, "reason_code": None}
    body = srv.submits()[0].body
    assert body["keep"] is True


def test_cb2_a_keep_preset_prints_the_line_without_the_flag(srv, capsys):
    """프리셋 `keep = true` 만으로도 보관 줄을 찍는다(§15.6). 플래그가 없으니 감지도 거절도 없다."""
    srv.health.pop("shelf")  # 감지를 안 하므로 옛 서버 흉내여도 막지 않는다
    srv.docs = [srv.job("active")]
    code, _out, err = run_keep(capsys, preset_name="shelfgold")
    assert code == 0, err
    assert human(err)[-1] == f"shelf: {srv.shelf_url}", err
    assert "keep" not in srv.submits()[0].body  # 키가 없다 — 옛 서버에 무해


def test_without_keep_there_is_no_keep_key_and_no_shelf_line(srv, capsys):
    srv.docs = [srv.job(None, keep=False)]
    code, _out, err = run_keep(capsys)
    assert code == 0, err
    assert "keep" not in srv.submits()[0].body
    assert not any(ln.startswith("shelf:") for ln in human(err)), err


def test_cb3_keep_no_wait_prints_the_link_from_the_submission(srv, capsys):
    srv.docs = [srv.job("waiting", state="queued")]
    code, out, err = run_keep(capsys, "--keep", "--no-wait")
    assert code == 0, err
    assert f"shelf: {srv.shelf_url} (after the job finishes)" in human(err), err
    assert last_json(out)["shelf_url"] == srv.shelf_url


def test_cg13_no_wait_without_keep_has_no_shelf_url_key(srv, capsys):
    srv.docs = [srv.job(None, keep=False, state="queued")]
    code, out, err = run_keep(capsys, "--no-wait")
    assert code == 0, err
    assert "shelf_url" not in last_json(out)
    assert not any(ln.startswith("shelf:") for ln in human(err)), err


def test_cg12_keep_no_wait_that_joins_still_carries_the_link(srv, capsys):
    srv.submit_resp = {
        "job_id": JOB,
        "state": "running",
        "joined": True,
        "url": srv.job_url,
        "keep": True,
        "shelf_url": srv.shelf_url,
    }
    srv.docs = [srv.job("waiting", state="running")]
    code, out, err = run_keep(capsys, "--keep", "--no-wait")
    assert code == 0, err
    doc = last_json(out)
    assert doc["joined"] is True and doc["shelf_url"] == srv.shelf_url
    assert f"shelf: {srv.shelf_url} (after the job finishes)" in human(err)


# ── 옛 서버 · 꺼진 보관함 · 못 닿음 (CE1 · CE2 · CE3 · §15.6) ────────────────


def test_ce1_an_old_server_refuses_keep_before_the_snapshot(srv, monkeypatch, capsys, tmp_path):
    srv.health.pop("shelf")
    seen = refuse_network(monkeypatch)
    (tmp_path / "t").mkdir()
    code, out, err = run(capsys, ["run", "shelftree", "--keep", "--dir", str(tmp_path / "t")])
    assert code == 2, (out, err)
    assert (
        "rcm: run --keep: server does not support --keep (rcm ≥ 0.7.0) — nothing was sent" in err
    ), err
    assert seen == {"snapshot": [], "submit": []}
    assert out.strip() == ""


def test_ce2_the_git_ref_path_refuses_too(srv, monkeypatch, capsys):
    srv.health.pop("shelf")
    seen = refuse_network(monkeypatch)
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 2, err
    assert "server does not support --keep (rcm ≥ 0.7.0)" in err
    assert seen["submit"] == []


def test_a_server_with_the_shelf_off_refuses_keep(srv, monkeypatch, capsys):
    """§15.6: health 의 `shelf.enabled` 가 false 면 제출하지 않고 종료 2."""
    srv.health["shelf"] = dict(SUMMARY, enabled=False)
    seen = refuse_network(monkeypatch)
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 2, err
    assert "rcm: run --keep: shelf is off on this server — nothing was sent" in err, err
    assert seen["submit"] == []


def test_ce3_keep_with_an_unreachable_health_exits_3_and_sends_nothing(srv, monkeypatch, capsys):
    calls: list[int] = []

    def boom(self):
        calls.append(1)
        raise ClientError(0, "cannot reach x: connection refused")

    monkeypatch.setattr(Client, "health", boom)
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 3, err
    assert "rcm: run --keep: cannot reach the server (cannot reach x: connection refused)" in err
    assert "nothing was sent" in err
    assert srv.submits() == []


def test_without_keep_an_unreachable_health_is_still_ignored(srv, monkeypatch, capsys):
    """오늘의 동작: health 는 경고용이다 — 못 읽어도 `--keep` 이 없으면 그대로 낸다."""

    def boom(self):
        raise ClientError(0, "cannot reach x")

    monkeypatch.setattr(Client, "health", boom)
    srv.docs = [srv.job(None, keep=False)]
    code, _out, err = run_keep(capsys)
    assert code == 0, err
    assert len(srv.submits()) == 1


def test_cg4_a_null_shelf_key_still_counts_as_knowing(srv, capsys):
    srv.health["shelf"] = None
    srv.docs = [srv.job("active")]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert srv.submits()[0].body["keep"] is True


def test_cg5_a_503_health_with_the_shelf_key_still_submits(srv, capsys):
    body = {"ok": False, "error": "janitor stale", "version": __version__, "shelf": SUMMARY}
    srv.fake.route("GET", "/api/health", body, status=503)
    srv.docs = [srv.job("active")]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert len(srv.submits()) == 1


def test_cg6_keep_reads_health_once(srv, capsys):
    srv.docs = [srv.job("active")]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert len(srv.fake.calls("GET", "/api/health")) == 1


def test_ce4_a_preset_without_artifacts_is_refused_before_anything_is_sent(
    srv, monkeypatch, capsys
):
    seen = refuse_network(monkeypatch)
    code, _out, err = run_keep(capsys, "--keep", preset_name="plain")
    assert code == 2, err
    assert "rcm: run --keep: preset 'plain' has no artifacts to keep — nothing was sent" in err
    assert seen["submit"] == []


def test_ce4b_when_the_status_does_not_say_the_server_400_is_a_usage_error(srv, capsys):
    srv.presets = [{k: v for k, v in p.items() if k != "has_artifacts"} for p in PRESETS]
    srv.fake.route(
        "POST",
        "/jobs",
        {"error": "keep needs artifacts", "code": "keep_without_artifacts"},
        status=400,
    )
    code, _out, err = run_keep(capsys, "--keep", preset_name="plain")
    assert code == 2, err
    assert "rcm: submit failed: keep needs artifacts" in err


# ── 끝난 뒤의 보관 줄 (CE5 · CE6 · CE7 · CE8 · CG1 · CG2 · CG3 · CG11) ───────


def test_ce5_a_failed_copy_says_why_and_keeps_the_job_exit_code(srv, capsys):
    srv.docs = [srv.job("failed", reason="shelf_full")]
    code, out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert human(err)[-1] == "shelf: not kept — shelf_full", err
    assert last_json(out)["shelf"]["reason_code"] == "shelf_full"


def test_ce6_a_disabled_copy_reads_the_same_way(srv, capsys):
    srv.docs = [srv.job("failed", reason="disabled")]
    code, _out, err = run_keep(capsys, preset_name="shelfgold")
    assert code == 0, err
    assert human(err)[-1] == "shelf: not kept — disabled"


def test_ce7_nothing_to_keep_is_said_at_once(srv, monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "SHELF_WAIT_SECONDS", 30)
    srv.docs = [srv.job("none", reason="not_collected_on_success")]
    started = time.monotonic()
    code, _out, err = run_keep(capsys, "--keep")
    assert time.monotonic() - started < 5
    assert code == 0, err
    assert human(err)[-1] == (
        'shelf: nothing to keep — not collected for a green job (artifacts_on = "failure")'
    ), err
    assert len(srv.fake.calls("GET", f"/jobs/{JOB}")) == 1  # 기다리지 않는다


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("empty", "the globs matched no files"),
        ("no_artifacts", "the preset declares no artifacts"),
        ("purged", "artifacts purged"),
        (None, "no artifacts were kept"),
    ],
)
def test_nothing_to_keep_names_the_bundle_state(srv, capsys, reason, text):
    srv.docs = [srv.job("none", reason=reason)]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert human(err)[-1] == f"shelf: nothing to keep — {text}", err


def test_ce8_a_failed_job_still_prints_its_shelf_link_and_exits_1(srv, capsys):
    srv.docs = [srv.job("active", state="failed")]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 1, err
    assert f"shelf: {srv.shelf_url}" in human(err), err


def test_a_stale_item_still_prints_its_link(srv, capsys):
    srv.docs = [srv.job("stale")]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 0
    assert human(err)[-1] == f"shelf: {srv.shelf_url}"


def test_cg1_a_copy_that_does_not_finish_in_time_says_still_copying(srv, capsys):
    srv.docs = [srv.job("pending")]
    started = time.monotonic()
    code, out, err = run_keep(capsys, "--keep")
    elapsed = time.monotonic() - started
    assert code == 0, err
    assert human(err)[-1] == f"shelf: {srv.shelf_url} (still copying)", err
    assert last_json(out)["shelf"]["state"] == "pending"
    assert elapsed < 3, elapsed  # 상수(0.5초)를 넘겨 붙잡지 않는다
    assert len(srv.fake.calls("GET", f"/jobs/{JOB}")) >= 3  # 기다리는 동안 다시 봤다


def test_cg2_a_copy_that_finishes_while_waiting_prints_the_plain_link(srv, monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "SHELF_WAIT_SECONDS", 5)
    srv.docs = [srv.job("pending"), srv.job("copying"), srv.job("copying"), srv.job("active")]
    code, out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert human(err)[-1] == f"shelf: {srv.shelf_url}", err
    assert last_json(out)["shelf"]["state"] == "active"  # 마지막으로 본 값


def test_cg11_waiting_for_a_remote_bundle_is_not_nothing_to_keep(srv, monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "SHELF_WAIT_SECONDS", 5)
    srv.docs = [srv.job("waiting"), srv.job("waiting"), srv.job("pending"), srv.job("active")]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert "nothing to keep" not in err
    assert human(err)[-1] == f"shelf: {srv.shelf_url}"


def test_waiting_that_never_ends_says_so(srv, capsys):
    srv.docs = [srv.job("waiting")]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 0, err
    assert human(err)[-1] == f"shelf: {srv.shelf_url} (waiting for the artifacts)", err


def test_cg3_ctrl_c_while_waiting_for_the_copy_keeps_the_job_exit_code(srv, monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "SHELF_WAIT_SECONDS", 30)
    srv.docs = [srv.job("pending", state="failed")]
    real = Client.job
    calls = {"n": 0}

    def job(self, job_id, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:  # 첫 조회는 대기 루프, 둘째부터는 보관 대기
            raise KeyboardInterrupt
        return real(self, job_id, **kw)

    monkeypatch.setattr(Client, "job", job)
    code, out, err = run_keep(capsys, "--keep")
    assert code == 1, err  # 잡의 실패 코드 — detach(3)가 아니다
    assert human(err)[-1] == f"shelf: {srv.shelf_url} (still copying)", err
    assert "detached" not in err
    assert last_json(out)["shelf"]["state"] == "pending"


# ── 기다림이 잡보다 먼저 끝날 때 (CG9 · CG10 · detach) ───────────────────────


def test_cg9_a_superseded_keep_job_says_where_the_keep_went(srv, capsys):
    srv.docs = [srv.job("waiting", state="cancelled", superseded_by=57)]
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 2, err
    assert "#412 superseded by #57 — rcm wait --job 57" in err
    assert human(err)[-1] == "shelf: moved to #57", err
    assert "nothing to keep" not in err


def test_cg10_timeout_does_not_wait_for_the_copy(srv, capsys):
    srv.docs = [srv.job("waiting", state="running")]
    code, _out, err = run_keep(capsys, "--keep", "--timeout", "0")
    assert code == 3, err
    assert f"shelf: {srv.shelf_url} (after the job finishes)" in human(err), err
    assert len(srv.fake.calls("GET", f"/jobs/{JOB}")) == 1


def test_detach_names_the_link_from_the_submission(srv, monkeypatch, capsys):
    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "wait_for_job", interrupted)
    code, _out, err = run_keep(capsys, "--keep")
    assert code == 3, err
    assert "detached from job #412" in err
    assert f"shelf: {srv.shelf_url} (after the job finishes)" in human(err), err


def test_keep_with_fetch_artifacts_fetches_first_and_prints_the_shelf_line_last(
    srv, monkeypatch, capsys, tmp_path
):
    order: list[str] = []

    def fetched(client, job_id, root, **kw):
        order.append("fetch")
        print("artifacts: wrote 1, unchanged 0, conflicted 0", file=__import__("sys").stderr)
        return {"ok": True}

    monkeypatch.setattr(cli_mod, "_fetch_artifacts", fetched)
    srv.docs = [srv.job("active")]
    out_dir = tmp_path / "out"
    code, _out, err = run_keep(capsys, "--keep", "--fetch-artifacts", "--output", str(out_dir))
    assert code == 0, err
    lines = human(err)
    assert order == ["fetch"]
    assert lines.index("artifacts: wrote 1, unchanged 0, conflicted 0") < lines.index(
        f"shelf: {srv.shelf_url}"
    ), err
    assert lines[-1] == f"shelf: {srv.shelf_url}"


def test_rcm_wait_prints_the_shelf_line_for_a_keep_job(srv, capsys):
    srv.docs = [srv.job("active")]
    code, out, err = run(capsys, ["wait", "--job", str(JOB)])
    assert code == 0, err
    assert human(err)[-1] == f"shelf: {srv.shelf_url}"
    assert last_json(out)["shelf"]["state"] == "active"


def test_an_old_job_document_without_keep_prints_nothing(srv, capsys):
    doc = srv.job(None, keep=False)
    doc.pop("keep")
    doc.pop("shelf")
    srv.docs = [doc]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB)])
    assert code == 0, err
    assert not any(ln.startswith("shelf:") for ln in human(err))


# ── rcm keep N (§15.1 표) ────────────────────────────────────────────────────


def keep_route(srv: Shelf, result: str, status: int, *, shelf: dict | None = None, **extra):
    body = {"job": JOB, "result": result, "shelf": shelf, **extra}
    srv.fake.route("POST", f"/jobs/{JOB}/keep", body, status=status)
    return body


def shelf_obj(srv: Shelf, state: str, reason: str | None = None) -> dict[str, Any]:
    return {"state": state, "url": srv.shelf_url, "reason_code": reason}


@pytest.mark.parametrize(
    ("result", "status", "state", "line"),
    [
        ("armed", 200, "waiting", "#412 will go on the shelf when it finishes · {url}"),
        ("requested", 202, "pending", "#412 is being copied to the shelf · {url}"),
        ("already_requested", 200, "copying", "#412 is already being copied to the shelf · {url}"),
    ],
)
def test_keep_success_lines(srv, capsys, result, status, state, line):
    keep_route(srv, result, status, shelf=shelf_obj(srv, state))
    code, out, err = run(capsys, ["keep", str(JOB)])
    assert code == 0, err
    assert out.strip() == line.format(url=srv.shelf_url), out


def test_cb6_retouched_names_the_new_cleanup_date_in_the_server_timezone(srv, capsys):
    # 2027-05-20T16:30Z 는 서울로 2027-05-21 이다 — UTC 날짜를 그대로 찍으면 하루 틀린다
    keep_route(
        srv, "retouched", 200, shelf=shelf_obj(srv, "active"), stale_at="2027-05-20T16:30:00Z"
    )
    code, out, err = run(capsys, ["keep", str(JOB)])
    assert code == 0, err
    assert out.strip() == (
        f"#412 is on the shelf; its clock starts again (cleanup pending from 2027-05-21) · "
        f"{srv.shelf_url}"
    ), out


def test_retouched_without_a_date_leaves_the_date_out(srv, capsys):
    keep_route(srv, "retouched", 200, shelf=shelf_obj(srv, "active"))
    code, out, _err = run(capsys, ["keep", str(JOB)])
    assert code == 0
    assert out.strip() == f"#412 is on the shelf; its clock starts again · {srv.shelf_url}"


def test_cg15_retried_says_the_last_copy_failed(srv, capsys):
    keep_route(srv, "retried", 202, shelf=shelf_obj(srv, "pending", "shelf_full"))
    code, out, err = run(capsys, ["keep", str(JOB)])
    assert code == 0, err
    assert out.strip() == f"#412: the last copy failed (shelf_full); trying again · {srv.shelf_url}"


def test_cb7_keep_json_prints_the_server_body_only(srv, capsys):
    body = keep_route(srv, "requested", 202, shelf=shelf_obj(srv, "pending"))
    code, out, err = run(capsys, ["keep", str(JOB), "--json"])
    assert code == 0, err
    assert json.loads(out.strip()) == body
    assert len(out.strip().splitlines()) == 1


def test_ce9_no_bundle_exits_1_and_names_why(srv, capsys):
    keep_route(
        srv,
        "no_bundle",
        409,
        shelf=shelf_obj(srv, "none", "not_collected_on_success"),
        error="nothing to keep",
    )
    code, out, err = run(capsys, ["keep", str(JOB)])
    assert code == 1, (out, err)
    assert (
        "rcm: keep: #412 has nothing to keep — "
        'not collected for a green job (artifacts_on = "failure")' in err
    ), err


def test_ce10_gone_exits_1_and_points_at_keep_from_the_start(srv, capsys):
    keep_route(srv, "gone", 410, shelf=shelf_obj(srv, "none", "purged"), error="gone")
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 1, err
    assert (
        "rcm: keep: #412 cannot be kept — its artifacts are gone (purged); "
        "next time use rcm run --keep" in err
    ), err


@pytest.mark.parametrize("status", [409, 503])
def test_keep_disabled_exits_1(srv, capsys, status):
    keep_route(srv, "disabled", status, shelf=None, error="the shelf is off")
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 1, err
    assert "rcm: keep: the shelf is off or unavailable on this server" in err, err


def test_ce11_someone_elses_job_is_a_usage_error(srv, capsys):
    srv.fake.route("POST", f"/jobs/{JOB}/keep", {"error": "forbidden"}, status=403)
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 2, err
    assert "rcm: keep: #412 — only its requester, a joiner or an admin token can keep it" in err


def test_ce12_a_missing_job_exits_1(srv, capsys):
    code, _out, err = run(capsys, ["keep", "99999"])
    assert code == 1, err
    assert "rcm: keep: no job #99999" in err, err


def test_ce12b_no_token_is_the_usual_hint(srv, monkeypatch, capsys):
    monkeypatch.delenv("RCM_TOKEN")
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 2, err
    assert "no token" in err


def test_cg17_keep_on_an_old_server_says_upgrade_not_missing_job(srv, capsys):
    srv.health.pop("shelf")
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 2, err
    assert "rcm: keep: server does not support rcm keep (rcm ≥ 0.7.0)" in err, err
    assert srv.fake.calls("POST", f"/jobs/{JOB}/keep") == []


def test_keep_unreachable_exits_3(env, monkeypatch, capsys):
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("RCM_TOKEN", "t")
    code, _out, err = run(capsys, ["keep", str(JOB)])
    assert code == 3, err
    assert "rcm: keep: cannot reach" in err


# ── rcm shelf (CB8 · CB9 · CG18) ─────────────────────────────────────────────


def item(job_id: int, state: str, **over: Any) -> dict[str, Any]:
    base = {
        "job_id": job_id,
        "preset": "shelfopt",
        "branch": "main",
        "state": state,
        "file_count": 3,
        "total_bytes": 1_200,
        "last_used_at": "2026-11-19T16:30:00Z",
        "stale_at": "2027-05-20T16:30:00Z",
        "delete_at": "2027-11-19T16:30:00Z",
        "reason_code": None,
        "url": f"http://x/shelf/{job_id}/",
    }
    base.update(over)
    return base


def test_cb8_shelf_lists_rows_in_the_server_timezone_and_a_footer(srv, capsys):
    srv.fake.route(
        "GET",
        "/api/shelf",
        {"items": [item(412, "active"), item(380, "stale", last_used_at="2026-05-01T00:00:00Z")]},
    )
    code, out, err = run(capsys, ["shelf"])
    assert code == 0, err
    lines = out.strip().splitlines()
    assert re.match(
        r"^#412\s+shelfopt\s+main\s+active\s+3 files\s+1\.2 KB\s+used 2026-11-20\s+"
        r"stale from 2027-05-21$",
        lines[0],
    ), lines
    assert re.match(r"^#380\s+shelfopt\s+main\s+stale\s+.*removed on 2027-11-20$", lines[1]), lines
    assert lines[-1] == f"2 items · 12.3 MB / 53.7 GB · {srv.url}/shelf/", lines


def test_shelf_default_state_hides_the_rest(srv, capsys):
    srv.fake.route(
        "GET", "/api/shelf", {"items": [item(1, "active"), item(2, "failed"), item(3, "deleted")]}
    )
    code, out, _err = run(capsys, ["shelf"])
    assert code == 0
    assert "#2" not in out and "#3" not in out
    assert "#1" in out


def test_shelf_all_shows_everything_but_deleted(srv, capsys):
    srv.fake.route(
        "GET",
        "/api/shelf",
        {
            "items": [
                item(1, "active"),
                item(2, "failed", reason_code="shelf_full"),
                item(3, "deleted"),
                item(4, "pending"),
            ]
        },
    )
    code, out, _err = run(capsys, ["shelf", "--state", "all"])
    assert code == 0
    assert "#1" in out and "#2" in out and "#4" in out and "#3 " not in out
    assert "failed (shelf_full)" in out
    query = [r for r in srv.fake.requests if r.path == "/api/shelf"]
    assert query, srv.fake.requests


def test_cb9_shelf_json_is_the_server_body_with_the_query(srv, capsys, monkeypatch):
    seen: list[str] = []
    real = Client.get_json

    def get_json(self, path, **kw):
        seen.append(path)
        return real(self, path, **kw)

    monkeypatch.setattr(Client, "get_json", get_json)
    body = {"items": [item(380, "stale")]}
    srv.fake.route("GET", "/api/shelf", body)
    code, out, err = run(capsys, ["shelf", "--state", "stale", "--preset", "shelfopt", "--json"])
    assert code == 0, err
    assert json.loads(out.strip()) == body
    shelf_paths = [p for p in seen if p.startswith("/api/shelf")]
    assert shelf_paths and "state=stale" in shelf_paths[0] and "preset=shelfopt" in shelf_paths[0]


def test_cg18_an_empty_shelf_says_so_with_the_link(srv, capsys):
    srv.fake.route("GET", "/api/shelf", {"items": []})
    code, out, _err = run(capsys, ["shelf"])
    assert code == 0
    assert out.strip() == f"the shelf is empty · {srv.url}/shelf/"


def test_cg17b_shelf_on_an_old_server_says_upgrade(srv, capsys):
    srv.health.pop("shelf")
    code, _out, err = run(capsys, ["shelf"])
    assert code == 2, err
    assert "rcm: shelf: server does not support rcm shelf (rcm ≥ 0.7.0)" in err


def test_shelf_reads_without_a_token(srv, monkeypatch, capsys):
    monkeypatch.delenv("RCM_TOKEN")
    srv.fake.route("GET", "/api/shelf", {"items": []})
    code, _out, err = run(capsys, ["shelf"])
    assert code == 0, err


# ── rcm shelf rm N (§15.6) ───────────────────────────────────────────────────


def test_cb10_shelf_rm_removes(srv, capsys):
    srv.fake.route("DELETE", f"/shelf/{JOB}", {"deleted": True})
    code, out, err = run(capsys, ["shelf", "rm", str(JOB)])
    assert code == 0, err
    assert out.strip() == "removed #412 from the shelf"


def test_cg19_shelf_rm_on_a_tombstone_is_already_removed_and_0(srv, capsys):
    srv.fake.route(
        "DELETE",
        f"/shelf/{JOB}",
        {"error": "deleted", "state": "deleted", "deleted_at": "2026-11-19T16:30:00Z"},
        status=410,
    )
    code, out, err = run(capsys, ["shelf", "rm", str(JOB)])
    assert code == 0, err
    assert out.strip() == "#412 was already deleted on 2026-11-20", out


def test_ce15_shelf_rm_while_downloading_exits_1(srv, capsys):
    srv.fake.route(
        "DELETE",
        f"/shelf/{JOB}",
        {"error": "being downloaded", "code": "shelf_item_busy"},
        status=409,
    )
    code, _out, err = run(capsys, ["shelf", "rm", str(JOB)])
    assert code == 1, err
    assert "rcm: shelf rm: #412 is being downloaded right now — try again in a minute" in err


def test_shelf_rm_while_copying_exits_1(srv, capsys):
    srv.fake.route(
        "DELETE", f"/shelf/{JOB}", {"error": "still copying", "state": "copying"}, status=409
    )
    code, _out, err = run(capsys, ["shelf", "rm", str(JOB)])
    assert code == 1, err
    assert "rcm: shelf rm: #412 is still being copied — try again when it is on the shelf" in err


def test_ce13_shelf_rm_needs_an_admin_token(srv, capsys):
    srv.fake.route("DELETE", f"/shelf/{JOB}", {"error": "admin token required"}, status=403)
    code, _out, err = run(capsys, ["shelf", "rm", str(JOB)])
    assert code == 2, err
    assert "rcm: shelf rm: needs an admin token" in err


def test_ce14_shelf_rm_of_something_not_on_the_shelf_exits_1(srv, capsys):
    code, _out, err = run(capsys, ["shelf", "rm", "77"])
    assert code == 1, err
    assert "rcm: shelf rm: #77 is not on the shelf" in err


def test_shelf_rm_on_an_old_server_says_upgrade(srv, capsys):
    srv.health.pop("shelf")
    code, _out, err = run(capsys, ["shelf", "rm", str(JOB)])
    assert code == 2, err
    assert "server does not support rcm shelf (rcm ≥ 0.7.0)" in err
    assert srv.fake.calls("DELETE", f"/shelf/{JOB}") == []


# ── rcm gc --dry-run 의 보관함 한 줄 (§5.4 · §15.6) ──────────────────────────


def test_render_gc_adds_one_shelf_line_when_the_plan_has_it():
    from remote_ci_monitor.core.render_text import render_gc

    body = {
        "dry_run": True,
        "planned": [],
        "storage_before": {"volume_bytes": 0},
        "shelf": {"to_stale": 2, "to_delete": 1},
    }
    text = render_gc(body)
    assert text.splitlines()[-1] == (
        "shelf: the next sweep would mark 2 cleanup pending and remove 1 "
        "(rcm gc itself does not touch the shelf)"
    ), text


def test_render_gc_without_a_shelf_key_is_unchanged():
    from remote_ci_monitor.core.render_text import render_gc

    body = {"dry_run": True, "planned": [], "storage_before": {"volume_bytes": 0}}
    assert "shelf" not in render_gc(body)
