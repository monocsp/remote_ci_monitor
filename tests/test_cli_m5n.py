"""CLI(M5n 역할 C) — `rcm machines` · `rcm check` 의 `machines`·`placement` 행 · `rcm recheck` ·
`rcm run --machine` 과 「맞는 머신 없음」 한 줄 · `rcm jobs` 의 대기 행.

명세는 docs/m5n-workplan.md §2(S1 · S3 · S4) · §5.3 · §7.3 · §8.1 · §8.2 · §12, 시나리오는
docs/m5n-test-scenarios-c.md 의 B~F 절. **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

서버는 `fake_rcm_http.FakeRcm`(127.0.0.1 임시 포트)이다. 응답 문서를 손으로 못 박고 CLI 가 그것을
어떻게 그리는지만 본다 — 역할 B 의 서버 구현을 기다리지 않는다. 문구가 명세에 있으면 그 글자
그대로, 없으면 시나리오 문서 「가정」의 글자를 잠근다.

표의 칸은 **두 칸 이상의 공백**으로 나눈다(가정 C1) — 칸 안에는 공백이 하나씩만 있다
(`Mac mini` · `xcode 27.2 · flutter 3.47.8`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, free_port
from remote_ci_monitor import __version__, cli

# ── 고정 문서 ────────────────────────────────────────────────────────────────

HEADER = ["machine", "host", "pool", "lanes", "fingerprint", "presets"]
XCODE_OLD = "Xcode 26.5 · Build version 26F75"
XCODE_NEW = "Xcode 27.2 · Build version 27B52"
FLUTTER = "Flutter 3.47.8 • channel stable"
MACMINI_HOST = "epeuem-…(a1f3)"


def iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ok(**detail: Any) -> dict[str, Any]:
    return {"state": "eligible", "code": "ok", "detail": detail}


def verdict(state: str, code: str, **detail: Any) -> dict[str, Any]:
    return {"state": state, "code": code, "detail": detail}


def check(preset: str, state: str, job_id: int | None, auto: bool = True) -> dict[str, Any]:
    """`checks[]` 항목 — 설정의 점검 프리셋마다 하나(가정 B-14)."""
    return {
        "preset": preset,
        "state": state,
        "job_id": job_id,
        "auto_submitted": auto and job_id is not None,
        "error_code": None,
    }


def machines_doc(now: datetime | None = None, **over: Any) -> dict[str, Any]:
    """§2 S1 의 세 머신(운영 모양: Mac mini 로컬 + 같은 호스트의 mac2-worker + 다른 컴퓨터 mbp).

    Mac mini 는 한 시간 전에 xcode 26.5 → 27.2 로 바뀌었고 sim-check #3620 이 실패했다(§2 S3).
    """
    now = now or datetime.now(UTC)
    doc: dict[str, Any] = {
        "generation": "1731740000:3",
        "machines": [
            {
                "id": "@local",
                "display_name": "Mac mini",
                "kind": "local",
                "pool": "default",
                "lanes": 2,
                "alive": True,
                "host_label": MACMINI_HOST,
                "host_key": "a1f3c9d2e4b5a6c7",
                "accept_any": True,
                "fingerprint": {
                    "hash": "1111aaaa2222bbbb",
                    "measured_at": iso(now - timedelta(minutes=2)),
                    "stale": False,
                    "changed_at": iso(now - timedelta(hours=1)),
                    "probes": {
                        "xcode": {"state": "ok", "value": XCODE_NEW, "previous": XCODE_OLD},
                        "flutter": {"state": "ok", "value": FLUTTER},
                    },
                },
                "presets": {
                    "gate": ok(),
                    "scenario-qa": verdict(
                        "ineligible",
                        "check_failed",
                        check="sim-check",
                        job_id=3620,
                        changed={"xcode": [XCODE_OLD, XCODE_NEW]},
                    ),
                    "sim-check": ok(),
                    "lint": ok(),
                },
                "checks": [check("sim-check", "failed", 3620)],
            },
            {
                "id": "mac2-worker",
                "display_name": "mac2-worker",
                "kind": "remote",
                "pool": "mac2",
                "lanes": 1,
                "alive": True,
                "host_label": MACMINI_HOST,
                "host_key": "a1f3c9d2e4b5a6c7",
                "accept_any": True,
                "fingerprint": {
                    "hash": "1111aaaa2222bbbb",
                    "measured_at": iso(now - timedelta(minutes=3)),
                    "stale": False,
                    "changed_at": iso(now - timedelta(days=5)),
                    "probes": {
                        "xcode": {"state": "ok", "value": XCODE_NEW},
                        "flutter": {"state": "ok", "value": FLUTTER},
                    },
                },
                "presets": {
                    "gate": ok(),
                    "scenario-qa": verdict("unknown", "check_needed", check="sim-check"),
                    "sim-check": ok(),
                    "lint": ok(),
                },
                "checks": [check("sim-check", "passed", 3611)],
            },
            {
                "id": "mbp",
                "display_name": "mbp",
                "kind": "remote",
                "pool": "mbp",
                "lanes": 1,
                "alive": True,
                "host_label": "mbp (7c20)",
                "host_key": "7c20aa11bb22cc33",
                "accept_any": True,
                "fingerprint": {
                    "hash": "3333cccc4444dddd",
                    "measured_at": iso(now - timedelta(minutes=1)),
                    "stale": False,
                    "changed_at": None,
                    "probes": {
                        "xcode": {"state": "ok", "value": XCODE_OLD},
                        "flutter": {"state": "missing", "value": None},
                    },
                },
                "presets": {
                    "gate": verdict("ineligible", "tool_missing", tools=["fvm"]),
                    "scenario-qa": verdict("ineligible", "tool_missing", tools=["fvm"]),
                    "sim-check": verdict("ineligible", "tool_missing", tools=["fvm"]),
                    "lint": ok(),
                },
                "checks": [check("sim-check", "needed", None)],
            },
        ],
    }
    doc.update(over)
    return doc


def preset_json(name: str, **over: Any) -> dict[str, Any]:
    p: dict[str, Any] = {
        "name": name,
        "description": "",
        "source_modes": ["git_ref"],
        "repo": "app",
        "inputs": [],
        "pool": "default",
        "pools": [],
        "placement": "pool",
        "checks": [],
        "concurrency_scope": "pool",
        "sample": True,
        "timeout_seconds": 1200,
    }
    p.update(over)
    return p


PRESETS = [
    preset_json("gate", placement="any-capable"),
    preset_json("scenario-qa", checks=["sim-check"]),
    preset_json("sim-check", sample=False),
    preset_json("lint"),
]


def status_doc(queue: list[dict[str, Any]] | None = None, **server: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": iso(datetime.now(UTC)),
        "display_timezone": "UTC",
        "server": {"lanes": 2, "workers": [], **server},
        "presets": PRESETS,
        "pools": [
            {"name": "default", "queue": queue or [], "recent": [], "medians": {}, "hosts": []}
        ],
    }


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def parsed(err: str) -> None:
    """argparse 가 명령·플래그를 알아봤다 — 「모르는 부명령이라 2」로 초록이 되지 않게."""
    assert "invalid choice" not in err and "unrecognized arguments" not in err, err


def cells(line: str) -> list[str]:
    """표 한 줄 → 칸(두 칸 이상의 공백으로 나눈다 — 가정 C1)."""
    return re.split(r"\s{2,}", line.strip())


def table(out: str) -> list[list[str]]:
    return [cells(ln) for ln in out.splitlines() if ln.strip()]


def check_row(out: str, name: str) -> tuple[str, str] | None:
    """`rcm check` 의 `{ok |FAIL|warn}  {name:<13} {detail}` 한 행 → (상태, 상세)."""
    m = re.search(rf"^(ok |FAIL|warn)  {re.escape(name)} +(.*)$", out, re.M)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def bodies(err: str) -> list[str]:
    """stderr 줄들에서 `rcm: ` 접두를 뗀 것(접두는 잠그지 않는다 — 가정 C9)."""
    return [ln[len("rcm: ") :] if ln.startswith("rcm: ") else ln for ln in err.splitlines()]


@pytest.fixture
def fake(monkeypatch, tmp_path) -> Iterator[FakeRcm]:
    """HOME·cwd 를 tmp 로 옮기고 RCM_SERVER 를 가짜 서버로. 토큰은 일반 클라이언트(alice)."""
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    srv = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", srv.url)
    monkeypatch.setenv("RCM_TOKEN", "t0ken-alice")
    srv.route("GET", "/api/health", {"ok": True, "version": __version__})
    srv.route("GET", "/api/whoami", {"name": "alice", "admin": False, "kind": "client"})
    srv.route("GET", "/api/status", status_doc())
    srv.route("GET", "/api/machines", machines_doc())
    yield srv
    srv.close()


# ═══ B. rcm machines ═════════════════════════════════════════════════════════


def test_machines_prints_the_s1_table_with_one_row_per_machine(fake, capsys):
    code, out, err = run(capsys, ["machines"])
    assert code == 0, err
    rows = table(out)
    assert rows[0] == HEADER, out
    assert rows[1:] == [
        [
            "Mac mini (@local)",  # 결정 108 — 표시 이름이 id 와 다르면 id 를 붙인다
            MACMINI_HOST,
            "default",
            "2",
            "xcode 27.2 · flutter 3.47.8",
            "3/4 ok · 1 sim-check failed #3620",
        ],
        [
            "mac2-worker",
            MACMINI_HOST,
            "mac2",
            "1",
            "xcode 27.2 · flutter 3.47.8",
            "3/4 ok · 1 need sim-check",
        ],
        ["mbp", "mbp (7c20)", "mbp", "1", "xcode 26.5 · flutter missing", "1/4 ok · 3 fvm missing"],
    ], out


def test_two_workers_on_one_host_show_the_same_host_label(fake, capsys):
    """운영 모양 그대로 — mac2-worker 는 다른 컴퓨터가 아니다(§1.1). 호스트 칸이 같아야 보인다."""
    code, out, _ = run(capsys, ["machines"])
    assert code == 0
    by_name = {r[0]: r for r in table(out)[1:]}
    assert by_name["Mac mini (@local)"][1] == by_name["mac2-worker"][1] == MACMINI_HOST
    assert by_name["mbp"][1] != MACMINI_HOST


def test_machines_reads_only_the_machines_route(fake, capsys):
    """읽기 명령이다 — 토큰 없이도(`read_auth = "none"`) 돌고 POST 를 하나도 안 보낸다."""
    code, _, err = run(capsys, ["machines"])
    assert code == 0, err
    assert fake.calls("GET", "/api/machines"), [r.path for r in fake.requests]
    assert not [r for r in fake.requests if r.method != "GET"]


def test_machines_works_without_a_token(fake, monkeypatch, capsys):
    monkeypatch.delenv("RCM_TOKEN")
    code, out, err = run(capsys, ["machines"])
    assert code == 0, err
    assert table(out)[0] == HEADER
    assert "authorization" not in fake.calls("GET", "/api/machines")[0].headers


def test_machines_json_passes_the_server_document_through(fake, capsys):
    doc = machines_doc()
    fake.route("GET", "/api/machines", doc)
    code, out, err = run(capsys, ["machines", "--json"])
    assert code == 0, err
    assert json.loads(out) == doc


def test_machine_flag_lists_every_preset_verdict_with_code_and_detail(fake, capsys):
    code, out, err = run(capsys, ["machines", "--machine", "mbp"])
    assert code == 0, err
    lines = out.splitlines()
    for preset, state, code_, text in (
        ("gate", "ineligible", "tool_missing", "fvm missing"),
        ("scenario-qa", "ineligible", "tool_missing", "fvm missing"),
        ("sim-check", "ineligible", "tool_missing", "fvm missing"),
        ("lint", "eligible", "ok", None),
    ):
        hit = [ln for ln in lines if cells(ln)[:3] == [preset, state, code_]]
        assert hit, f"no `{preset}  {state}  {code_}` line:\n{out}"
        if text:
            assert text in hit[0], hit[0]


def test_machine_flag_shows_full_probe_values_and_the_previous_one(fake, capsys):
    """요약 표가 자른 값을 한 머신 화면은 전부 보인다 — 무엇이 어떻게 바뀌었나(§2 S3)."""
    code, out, err = run(capsys, ["machines", "--machine", "@local"])
    assert code == 0, err
    assert XCODE_NEW in out and XCODE_OLD in out, out
    assert "sim-check" in out and "#3620" in out, out


def test_machine_flag_json_prints_only_that_machine(fake, capsys):
    code, out, err = run(capsys, ["machines", "--machine", "mbp", "--json"])
    assert code == 0, err
    got = json.loads(out)
    assert isinstance(got, dict) and got["id"] == "mbp", got
    assert got["presets"] == machines_doc()["machines"][2]["presets"], got


def test_machine_flag_with_an_unknown_id_is_a_usage_error_naming_the_machines(fake, capsys):
    code, out, err = run(capsys, ["machines", "--machine", "nope"])
    assert code == 2, (out, err)
    assert "unknown machine 'nope'" in err, err
    for mid in ("@local", "mac2-worker", "mbp"):
        assert mid in err, err


def test_a_machine_that_is_down_says_so_in_its_row(fake, capsys):
    doc = machines_doc()
    doc["machines"][1]["alive"] = False
    fake.route("GET", "/api/machines", doc)
    code, out, _ = run(capsys, ["machines"])
    assert code == 0
    assert table(out)[2][0] == "mac2-worker (down)", out


def test_only_the_top_three_reasons_are_listed(fake, capsys):
    """사유가 넷이면 많은 순으로 셋 — 칸이 끝없이 길어지지 않는다(S1 「상위 3개」)."""
    doc = machines_doc()
    doc["machines"][2]["presets"] = {
        "a": verdict("ineligible", "tool_missing", tools=["fvm"]),
        "b": verdict("ineligible", "tool_missing", tools=["fvm"]),
        "c": verdict("unknown", "check_needed", check="sim-check"),
        "d": verdict("unknown", "report_stale"),
        "e": verdict("unknown", "workspace_tool", tools=["melos"]),
        "f": ok(),
    }
    fake.route("GET", "/api/machines", doc)
    code, out, _ = run(capsys, ["machines"])
    assert code == 0
    # 많은 순 → 같으면 글자 순. 2 fvm 이 먼저, 1 개짜리 셋 중 글자 순으로 앞의 둘만
    assert table(out)[3][5] == "1/6 ok · 2 fvm missing · 1 melos workspace only · 1 need sim-check"


def test_probe_states_render_as_name_plus_version_missing_or_exit(fake, capsys):
    doc = machines_doc()
    doc["machines"][2]["fingerprint"]["probes"] = {
        "xcode": {"state": "error", "value": "exit 72"},
        "java": {"state": "ok", "value": 'openjdk version "17.0.12" 2024-07-16'},
        "idb": {"state": "missing", "value": None},
        "adb": {"state": "ok", "value": "Android Debug Bridge"},
    }
    fake.route("GET", "/api/machines", doc)
    code, out, _ = run(capsys, ["machines"])
    assert code == 0
    assert table(out)[3][4] == "xcode exit 72 · java 17.0.12 · idb missing · adb Android", out


def test_machines_on_an_unreachable_server_exits_3(fake, monkeypatch, capsys):
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{free_port()}")
    code, out, err = run(capsys, ["machines"])
    assert code == 3, (out, err)
    assert "cannot reach" in err, err


def test_machines_on_an_old_server_is_a_usage_error_not_a_traceback(fake, capsys):
    fake.routes.pop(("GET", "/api/machines"))
    code, out, err = run(capsys, ["machines"])
    assert code == 2, (out, err)
    parsed(err)
    assert "machines" in err and "Traceback" not in err, err


def test_machines_with_read_auth_denied_says_so(fake, capsys):
    fake.route("GET", "/api/machines", {"error": "read access requires a token"}, status=401)
    code, _, err = run(capsys, ["machines"])
    assert code == 2
    assert "read access denied" in err, err


def test_machines_with_no_machines_prints_the_header_only(fake, capsys):
    fake.route("GET", "/api/machines", {"generation": "1:0", "machines": []})
    code, out, _ = run(capsys, ["machines"])
    assert code == 0
    assert table(out) == [HEADER], out


def test_a_missing_value_is_a_dash_not_a_zero(fake, capsys):
    """모르는 칸은 `—`(AGENTS 「Never invent a number」) — 레인 수 · 지문이 없는 새 워커."""
    doc = machines_doc()
    m = doc["machines"][2]
    m.pop("lanes")
    m["fingerprint"] = {"hash": None, "measured_at": None, "stale": False, "probes": {}}
    m["presets"] = {"gate": verdict("unknown", "report_missing")}
    fake.route("GET", "/api/machines", doc)
    code, out, _ = run(capsys, ["machines"])
    assert code == 0
    row = table(out)[3]
    assert row[3] == "—" and row[4] == "—", row
    assert row[5] == "0/1 ok · 1 no report", row


# ═══ C. rcm check — machines · placement ═══════════════════════════════════


def test_check_machines_row_fails_naming_the_change_and_the_failed_check(fake, capsys):
    code, out, _ = run(capsys, ["check"])
    assert check_row(out, "machines") == (
        "FAIL",
        "3 machines · 1 environment changed (Mac mini: xcode 26.5 → 27.2) · "
        "1 check failed (Mac mini: sim-check #3620)",
    ), out
    assert code == 1


def test_check_machines_row_is_ok_when_the_change_passed_its_check(fake, capsys):
    doc = machines_doc()
    local = doc["machines"][0]
    local["checks"][0]["state"] = "passed"
    local["presets"]["scenario-qa"] = ok()
    fake.route("GET", "/api/machines", doc)
    _, out, _ = run(capsys, ["check"])
    assert check_row(out, "machines") == (
        "ok",
        "3 machines · 1 environment changed (Mac mini: xcode 26.5 → 27.2)",
    ), out


def test_check_machines_row_ignores_changes_older_than_a_day(fake, capsys):
    doc = machines_doc()
    local = doc["machines"][0]
    local["fingerprint"]["changed_at"] = iso(datetime.now(UTC) - timedelta(hours=25))
    local["checks"][0]["state"] = "passed"
    local["presets"]["scenario-qa"] = ok()
    fake.route("GET", "/api/machines", doc)
    _, out, _ = run(capsys, ["check"])
    assert check_row(out, "machines") == ("ok", "3 machines"), out


def test_check_machines_row_lists_every_changed_probe_of_a_machine(fake, capsys):
    doc = machines_doc()
    doc["machines"][0]["fingerprint"]["probes"]["idb"] = {
        "state": "ok",
        "value": "idb_companion 1.7.8",
        "previous": "idb_companion 1.1.8",
    }
    fake.route("GET", "/api/machines", doc)
    _, out, _ = run(capsys, ["check"])
    status, detail = check_row(out, "machines") or ("", "")
    assert "(Mac mini: xcode 26.5 → 27.2, idb 1.1.8 → 1.7.8)" in detail, out


def test_check_machines_row_counts_a_failed_check_even_without_a_change(fake, capsys):
    doc = machines_doc()
    doc["machines"][0]["fingerprint"]["changed_at"] = None
    fake.route("GET", "/api/machines", doc)
    _, out, _ = run(capsys, ["check"])
    assert check_row(out, "machines") == (
        "FAIL",
        "3 machines · 1 check failed (Mac mini: sim-check #3620)",
    ), out


def test_check_placement_row_names_each_placed_preset(fake, capsys):
    """`any-capable` 이거나 `checks` 가 있는 프리셋만 — 평범한 pool 프리셋은 행을 늘리지 않는다."""
    _, out, _ = run(capsys, ["check"])
    assert check_row(out, "placement") == (
        "warn",
        "gate: any machine (2 eligible) · scenario-qa: pool default (check failed)",
    ), out


def test_check_says_when_a_check_that_did_not_finish_needs_rcm_recheck(fake, capsys):
    """실측(2026-11-16): 자동 점검이 유실되면 다시 안 걸린다(결정 95). 그것을 ok · 「check pending」
    으로 그리면 잡이 조용히 영원히 기다린다 — machines 는 FAIL, placement 는 그 이유를 말한다."""
    doc = machines_doc()
    local = doc["machines"][0]
    local["fingerprint"]["changed_at"] = None
    local["checks"][0].update(state="needed", job_id=3621, recheck_needed=True)
    local["presets"]["scenario-qa"] = verdict(
        "unknown", "check_needed", check="sim-check", job_id=3621, recheck=True
    )
    fake.route("GET", "/api/machines", doc)
    code, out, _ = run(capsys, ["check"])
    assert check_row(out, "machines") == (
        "FAIL",
        "3 machines · 1 check needs rcm recheck (Mac mini: sim-check #3621)",
    ), out
    status, detail = check_row(out, "placement") or ("", "")
    assert "scenario-qa: pool default (check needs rcm recheck)" in detail, out
    assert code == 1
    from remote_ci_monitor.core.render_text import verdict_text

    assert (
        verdict_text("check_needed", {"check": "sim-check", "job_id": 3621, "recheck": True})
        == "sim-check #3621 did not finish — recheck"
    )
    assert verdict_text("check_needed", {"check": "sim-check"}) == "need sim-check"


def test_check_placement_row_is_ok_when_every_placed_preset_has_a_machine(fake, capsys):
    doc = machines_doc()
    doc["machines"][0]["presets"]["scenario-qa"] = ok()
    fake.route("GET", "/api/machines", doc)
    _, out, _ = run(capsys, ["check"])
    assert check_row(out, "placement") == (
        "ok",
        "gate: any machine (2 eligible) · scenario-qa: pool default (1 eligible)",
    ), out


def test_check_placement_row_says_no_capable_machine_for_an_any_capable_preset(fake, capsys):
    doc = machines_doc()
    for m in doc["machines"]:
        m["presets"]["gate"] = verdict("ineligible", "tool_missing", tools=["gitleaks"])
    fake.route("GET", "/api/machines", doc)
    _, out, _ = run(capsys, ["check"])
    status, detail = check_row(out, "placement") or ("", "")
    assert status == "warn" and detail.startswith("gate: any machine (no capable machine)"), out


def test_check_on_an_old_server_has_neither_row(fake, capsys):
    """`/api/machines` 가 404 인 옛 서버 — 행이 없을 뿐 FAIL 이 아니다(`client`·`cancel` 행처럼)."""
    fake.routes.pop(("GET", "/api/machines"))
    _, out, _ = run(capsys, ["check"])
    assert check_row(out, "machines") is None, out
    assert check_row(out, "placement") is None, out
    assert check_row(out, "server") == ("ok", f"{fake.url} · v{__version__}"), out


def test_check_without_placed_presets_has_no_placement_row(fake, capsys):
    plain = status_doc()
    plain["presets"] = [preset_json("lint")]
    fake.route("GET", "/api/status", plain)
    _, out, _ = run(capsys, ["check"])
    assert check_row(out, "placement") is None, out


# ═══ D. rcm recheck ═════════════════════════════════════════════════════════


def test_recheck_posts_the_machine_and_named_checks_and_prints_the_jobs(fake, capsys):
    fake.route(
        "POST",
        "/machines/@local/recheck",
        {"jobs": [{"preset": "sim-check", "job_id": 3650}]},
    )
    code, out, err = run(capsys, ["recheck", "@local", "sim-check"])
    assert code == 0, err
    sent = fake.calls("POST", "/machines/@local/recheck")
    assert len(sent) == 1 and sent[0].body == {"checks": ["sim-check"]}, sent
    assert "#3650" in out and "sim-check" in out, out


def test_recheck_without_check_names_asks_for_every_check(fake, capsys):
    fake.route("POST", "/machines/mbp/recheck", {"jobs": [{"preset": "sim-check", "job_id": 3651}]})
    code, out, err = run(capsys, ["recheck", "mbp"])
    assert code == 0, err
    body = fake.calls("POST", "/machines/mbp/recheck")[0].body or {}
    assert body.get("checks") in (None, []), body
    assert "#3651" in out


def test_recheck_with_a_client_token_is_refused_and_says_admin(fake, capsys):
    fake.route("POST", "/machines/mbp/recheck", {"error": "admin token required"}, status=403)
    code, out, err = run(capsys, ["recheck", "mbp"])
    assert code == 2, (out, err)
    assert "admin" in err, err


def test_recheck_of_an_unknown_machine_is_a_usage_error_with_the_server_message(fake, capsys):
    fake.route("POST", "/machines/nope/recheck", {"error": "unknown machine 'nope'"}, status=404)
    code, _, err = run(capsys, ["recheck", "nope"])
    assert code == 2
    assert "unknown machine 'nope'" in err, err


def test_recheck_with_nothing_to_run_says_so_and_exits_0(fake, capsys):
    fake.route("POST", "/machines/mbp/recheck", {"jobs": []})
    code, out, err = run(capsys, ["recheck", "mbp"])
    assert code == 0, err
    assert "nothing to recheck on mbp" in out + err, (out, err)


def test_recheck_on_an_unreachable_server_exits_3(fake, monkeypatch, capsys):
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{free_port()}")
    code, _, err = run(capsys, ["recheck", "mbp"])
    assert code == 3, err


def test_recheck_needs_a_token(fake, monkeypatch, capsys):
    monkeypatch.delenv("RCM_TOKEN")
    code, _, err = run(capsys, ["recheck", "mbp"])
    parsed(err)
    assert code == 2 and "token" in err, err
    assert not fake.calls("POST", "/machines/mbp/recheck")


def test_recheck_help_says_admin(capsys):
    code, out, _ = run(capsys, ["--help"])
    assert code == 0
    line = [ln for ln in out.splitlines() if ln.strip().startswith("recheck")]
    assert line and "admin" in line[0], out


# ═══ E. rcm run — --machine · 「맞는 머신 없음」 한 줄 ════════════════════════


S4_LINE = "gate: no capable machine — mbp: fvm missing · mac2-worker: down"


def submit_reply(placement: dict[str, Any] | None, job_id: int = 3700) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "job_id": job_id,
        "joined": False,
        "state": "queued",
        "sha": "25e1494ab0f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
        "url": f"http://macmini:8787/#/jobs/{job_id}",
    }
    if placement is not None:
        reply["placement"] = placement
    return reply


S4_PLACEMENT = {
    # 시나리오 B 의 가정 B-16: `blocked[]` 항목은 정확히 {worker, code, detail} — 이름표도 상태도
    # 없다. down 워커의 코드는 `worker_down` 으로 본다(가정 C5).
    "mode": "any-capable",
    "eligible_now": [],
    "blocked": [
        {"worker": "mbp", "code": "tool_missing", "detail": {"tools": ["fvm"]}},
        {"worker": "mac2-worker", "code": "worker_down", "detail": {}},
    ],
}


def run_gate(capsys, fake: FakeRcm, placement: dict[str, Any] | None, *extra: str):
    fake.route("POST", "/jobs", submit_reply(placement))
    return run(capsys, ["run", "gate", "--ref", "main", "--no-wait", *extra])


def test_run_prints_one_line_when_no_machine_can_take_the_job(fake, capsys):
    code, out, err = run_gate(capsys, fake, S4_PLACEMENT)
    assert code == 0, err  # 잡은 큐에 섰다 — 종료 코드는 그대로(결정 99)
    assert bodies(err).count(S4_LINE) == 1, err
    assert json.loads(out.strip().splitlines()[-1])["job_id"] == 3700


def test_run_says_nothing_when_some_machine_is_eligible_even_if_busy(fake, capsys):
    """바쁜 것은 정상이다 — 맞는 머신이 하나라도 있으면 평소처럼 기다린다(§2 S4)."""
    placement = dict(S4_PLACEMENT, eligible_now=["@local"])
    code, _, err = run_gate(capsys, fake, placement)
    assert code == 0
    assert not [b for b in bodies(err) if "no capable machine" in b], err


def test_run_against_an_old_server_without_placement_says_nothing(fake, capsys):
    code, _, err = run_gate(capsys, fake, None)
    assert code == 0
    assert "no capable machine" not in err


def test_run_with_an_empty_blocked_list_says_unknown_not_an_invented_reason(fake, capsys):
    """§12: 사유를 모르면 지어내지 않고 `unknown` 이라고 쓴다."""
    placement = {"mode": "any-capable", "eligible_now": [], "blocked": []}
    code, _, err = run_gate(capsys, fake, placement)
    assert code == 0
    assert "gate: no capable machine — unknown" in bodies(err), err


def test_run_line_uses_worker_ids_and_check_wording(fake, capsys):
    """`blocked[]` 에는 이름표가 없다(B-16) — 워커 id 를 그대로 쓴다. 점검 문구는 가정 C3."""
    placement = {
        "mode": "any-capable",
        "eligible_now": [],
        "blocked": [
            {
                "worker": "@local",
                "code": "check_failed",
                "detail": {"check": "sim-check", "job_id": 3620},
            },
            {
                "worker": "mbp",
                "code": "check_running",
                "detail": {"check": "sim-check", "job_id": 3651},
            },
        ],
    }
    code, _, err = run_gate(capsys, fake, placement)
    assert code == 0
    want = "gate: no capable machine — @local: sim-check failed #3620 · mbp: sim-check running"
    assert want in bodies(err), err


def test_run_line_also_prints_when_waiting_without_no_wait(fake, monkeypatch, capsys):
    """대기하는 `rcm run` 도 제출 직후 같은 줄을 찍는다 — 20분 기다린 뒤가 아니라."""
    seen: list[str] = []

    def fake_wait(client, job_id, **kw):
        seen.append("waited")
        return 0, {"id": job_id, "state": "succeeded"}, None

    monkeypatch.setattr(cli, "wait_for_job", fake_wait)
    fake.route("POST", "/jobs", submit_reply(S4_PLACEMENT))
    code, _, err = run(capsys, ["run", "gate", "--ref", "main"])
    assert code == 0, err
    assert seen == ["waited"]
    assert S4_LINE in bodies(err), err


def test_run_machine_flag_sends_the_machine_in_the_body(fake, capsys):
    fake.route("POST", "/jobs", submit_reply({"mode": "machine", "eligible_now": ["mbp"]}))
    code, _, err = run(
        capsys, ["run", "sim-check", "--ref", "main", "--machine", "mbp", "--no-wait"]
    )
    assert code == 0, err
    body = fake.calls("POST", "/jobs")[0].body
    assert body["machine"] == "mbp" and body["preset"] == "sim-check", body


def test_run_without_machine_flag_sends_no_machine_key(fake, capsys):
    run_gate(capsys, fake, S4_PLACEMENT)
    assert "machine" not in fake.calls("POST", "/jobs")[0].body


def test_run_machine_flag_refused_for_a_client_token_exits_2_with_the_message(fake, capsys):
    """결정 96: 점검 프리셋이 아니면 admin 만. 서버의 403 문구를 그대로 보인다."""
    fake.route("POST", "/jobs", {"error": "--machine needs an admin token for gate"}, status=403)
    code, _, err = run(capsys, ["run", "gate", "--ref", "main", "--machine", "mbp", "--no-wait"])
    assert code == 2, err
    assert "--machine needs an admin token for gate" in err, err


def test_run_machine_flag_on_a_down_machine_exits_2(fake, capsys):
    """409(머신 down) — 잡이 안 만들어졌다. 결과를 모르는 3 이 아니라 사용 오류 2(가정 C11)."""
    fake.route("POST", "/jobs", {"error": "machine 'mbp' is down"}, status=409)
    code, _, err = run(capsys, ["run", "sim-check", "--ref", "main", "--machine", "mbp"])
    assert code == 2, err
    assert "machine 'mbp' is down" in err, err


def test_run_help_lists_machine(capsys):
    code, out, _ = run(capsys, ["run", "--help"])
    assert code == 0
    assert "--machine" in out, out


# ═══ F. rcm jobs — 대기 행 ══════════════════════════════════════════════════


def queued_row(job_id: int, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": job_id,
        "pool": "default",
        "state": "queued",
        "key": "gate",
        "requester": {"name": "alice", "label": "alice@mbp"},
        "joiners": [],
        "summary": "",
        "source": {"mode": "git_ref", "repo": "org/app", "ref": "main", "sha": "25e1494ab0f1c2d3"},
        "estimate": {"waited_seconds": 30.0},
        "reason": "waiting_for_lane",
        "placement": "pool",
        "placement_detail": [],
        "position": 1,
    }
    row.update(over)
    return row


NO_CAPABLE_DETAIL = [
    {
        "worker": "mbp",
        "display_name": "mbp",
        "state": "ineligible",
        "code": "tool_missing",
        "detail": {"tools": ["fvm"]},
    },
    {
        "worker": "mac2-worker",
        "display_name": "mac2-worker",
        "state": "down",
        "code": "worker_down",
        "detail": {},
    },
]


def jobs_line(out: str, job_id: int) -> str:
    hit = [ln for ln in out.splitlines() if ln.startswith(f"#{job_id} ")]
    assert hit, out
    return hit[0]


def test_jobs_shows_any_machine_on_an_any_capable_waiting_row(fake, capsys):
    fake.route("GET", "/api/status", status_doc([queued_row(3701, placement="capable")]))
    code, out, err = run(capsys, ["jobs"])
    assert code == 0, err
    assert "any machine" in jobs_line(out, 3701), out


def test_jobs_does_not_say_any_machine_for_a_pool_row(fake, capsys):
    fake.route("GET", "/api/status", status_doc([queued_row(3702)]))
    _, out, _ = run(capsys, ["jobs"])
    assert "any machine" not in jobs_line(out, 3702), out


def test_jobs_shows_the_no_capable_machine_reason_with_each_machine(fake, capsys):
    row = queued_row(
        3703, placement="capable", reason="no_capable_machine", placement_detail=NO_CAPABLE_DETAIL
    )
    fake.route("GET", "/api/status", status_doc([row]))
    _, out, _ = run(capsys, ["jobs"])
    assert "no capable machine — mbp: fvm missing · mac2-worker: down" in jobs_line(out, 3703), out


def test_jobs_json_keeps_the_placement_keys(fake, capsys):
    row = queued_row(
        3704, placement="capable", reason="no_capable_machine", placement_detail=NO_CAPABLE_DETAIL
    )
    fake.route("GET", "/api/status", status_doc([row]))
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    got = json.loads(out)[0]
    assert got["placement"] == "capable" and got["placement_detail"] == NO_CAPABLE_DETAIL
