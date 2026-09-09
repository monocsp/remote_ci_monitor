"""CLI(M5e) — `rcm run --fetch-artifacts [--force|--dry-run]` · `rcm artifacts JOB_ID` · 종료 코드.

명세는 docs/m5e-workplan.md §12(명령 표 · 결과 줄 · 종료 코드)와 §11(적용 · ack) · §18 의 CLI 줄.
**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.** 프리셋의 `artifacts` 키가 아직 없어서
서버 픽스처는 `ConfigError: unknown key 'artifacts'` 로 죽는다 — 그 키도 아직 없다는 뜻이다.
파서만 보는 시험(`build_parser`)은 서버 없이 따로 빨갛다.

test_cli_m5 처럼 `main(argv)` 를 in-process 로 부르고 `RCM_SERVER`/`RCM_TOKEN` 으로만 서버를
가리킨다. 서버는 진짜 로컬 워커가 도는 `test_server.Server(workers=True)` — 잡이 실제로 파일을
만들고, 그것이 세션의 트리로 돌아오는 데까지가 한 시험이다(완료 기준 ①).

결정적으로 충돌을 만드는 법: 트리에 있는 `out/c.txt` 를 `--exclude` 로 스냅샷에서 빼면 그 경로는
**기준선이 없고** 로컬 파일은 있다 — §11 표 2행의 `conflicted` 다. 잡이 도는 동안 사람이 파일을
고치는 흉내(경주)를 낼 필요가 없다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from remote_ci_monitor.cli import build_parser, main
from remote_ci_monitor.client import Client
from remote_ci_monitor.config import parse_preset
from test_cli_m1 import last_json
from test_server import PRESETS, Server, sh

#: 전달 실패 전용 종료 코드(§18 「전달 실패 종료 코드는 5」).
EXIT_DELIVERY = 5

#: 잡이 워크스페이스에 남기는 골든 세 장. `c.txt` 는 스냅샷에 없던 경로다.
GOLD_BODY = (
    "mkdir -p out; printf 'v2\\n' > out/a.txt; printf 'v2\\n' > out/b.txt; "
    "printf 'v2\\n' > out/c.txt; echo '::rcm::summary::goldens updated'"
)

ARTIFACT_PRESETS = [
    *PRESETS,
    sh("gold", f"{GOLD_BODY}; exit 0", artifacts=["out/*.txt"]),
    sh("goldfail", f"{GOLD_BODY}; exit 3", artifacts=["out/*.txt"]),
    sh("plain", "echo nothing to collect; exit 0"),
    sh("golddeploy", "echo deploy", source_modes=["git_ref"], artifacts=["out/*.txt"]),
]

GIT_SHA = "0123456789abcdef0123456789abcdef01234567"

#: §12 결과 줄 — 「비교해서 다른 것」과 「실제로 쓴 것」을 낱말로 가른다.
RESULT_RE = re.compile(r"wrote (\d+), unchanged (\d+), conflicted (\d+)")


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def counts(out: str, err: str) -> tuple[int, int, int]:
    """결과 줄의 (wrote, unchanged, conflicted). 사람용 줄이라 두 스트림 다 본다."""
    m = RESULT_RE.search(out + "\n" + err)
    assert m, f"no artifacts result line:\nSTDOUT {out!r}\nSTDERR {err!r}"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bundle(srv: Server, jid: int, token: str = "alice") -> dict:
    """`GET /jobs/{id}/artifacts` 의 상태 문서."""
    status, body = srv.req("GET", f"/jobs/{jid}/artifacts", token=token)
    assert status == 200, (status, body)
    return body


def refuse_network(monkeypatch) -> dict[str, list]:
    """스냅샷과 제출을 못 하게 막는다 — 「제출 전에 거절한다」를 증명한다."""
    seen: dict[str, list] = {"snapshot": [], "submit": []}

    def no_snapshot(*args, **kwargs):
        seen["snapshot"].append(args)
        raise AssertionError("rcm run must not snapshot after a usage error")

    def no_submit(self, *args, **kwargs):
        seen["submit"].append(args)
        raise AssertionError("rcm run must not submit after a usage error")

    monkeypatch.setattr("remote_ci_monitor.cli.make_snapshot", no_snapshot)
    monkeypatch.setattr(Client, "submit", no_submit)
    return seen


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path):
    """HOME 을 tmp 로 옮기고 `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    def use(server: Server, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def live(tmp_path):
    """진짜 로컬 워커가 도는 서버 + 산출물 글롭을 선언한 프리셋."""
    s = Server(tmp_path, workers=True, max_snapshot_bytes=200_000)
    try:
        s.cfg.presets = tuple(parse_preset(p) for p in ARTIFACT_PRESETS)
    except BaseException:
        s.close()
        raise
    yield s
    s.close()


@pytest.fixture
def tree(tmp_path) -> Path:
    """제출할 트리. `out/a.txt` 는 갱신되고 `out/b.txt` 는 이미 최신 · `out/c.txt` 는 로컬 전용."""
    root = tmp_path / "tree"
    (root / "out").mkdir(parents=True)
    (root / "hello.txt").write_text("hello\n")
    (root / "out" / "a.txt").write_text("v1\n")
    (root / "out" / "b.txt").write_text("v2\n")
    (root / "out" / "c.txt").write_text("mine\n")
    return root


def gold(capsys, tree: Path, *extra: str, preset: str = "gold") -> tuple[int, str, str]:
    """`rcm run <preset> --dir <tree> --exclude out/c.txt …` — c.txt 만 스냅샷에서 뺀다."""
    return run(
        capsys,
        ["run", preset, "--dir", str(tree), "--exclude", "out/c.txt", *extra],
    )


# ── 파서 (§12 표) ───────────────────────────────────────────────────────────


def test_run_takes_fetch_artifacts_force_and_dry_run():
    args = build_parser().parse_args(["run", "gold", "--fetch-artifacts", "--force", "--dry-run"])
    assert args.fetch_artifacts is True
    assert args.force is True
    assert args.dry_run is True


def test_run_defaults_have_no_fetch_no_force_no_dry_run():
    args = build_parser().parse_args(["run", "gold"])
    assert args.fetch_artifacts is False
    assert args.force is False
    assert args.dry_run is False


def test_artifacts_command_takes_a_job_id_fetch_output_and_resume():
    args = build_parser().parse_args(["artifacts", "412", "--fetch", "--output", "d", "--resume"])
    assert args.job == 412
    assert args.fetch is True and args.output == "d" and args.resume is True


def test_artifacts_command_help_is_english(capsys):
    code, out, _err = run(capsys, ["artifacts", "--help"])
    assert code == 0, out
    assert "--fetch" in out and "--output" in out and "--resume" in out, out
    assert not re.search(r"[가-힣]", out), out  # CLI 도움말은 영어 그대로다


# ── 제출 전에 거절 (§12) ────────────────────────────────────────────────────


def test_no_wait_with_fetch_artifacts_is_refused_before_submission(
    live, env, tree, monkeypatch, capsys
):
    """§12: `--no-wait --fetch-artifacts` 는 **제출 전에** 거절한다.

    기다리지 않으면 받을 수도 없다 — 큰 트리를 다 싸고 나서 알려 주면 늦다.
    """
    env(live)
    seen = refuse_network(monkeypatch)
    code, out, err = gold(capsys, tree, "--no-wait", "--fetch-artifacts")
    assert code == 2, (code, out, err)
    assert "--fetch-artifacts" in err and "--no-wait" in err, err
    assert seen["submit"] == [] and seen["snapshot"] == [], seen
    assert out.strip() == "", out


def test_a_git_ref_job_needs_an_output_directory(live, env, monkeypatch, capsys):
    """§12: git_ref 잡은 대응하는 로컬 트리가 없다 → `--output DIR` 을 요구한다(제출 전에)."""
    env(live)
    seen = refuse_network(monkeypatch)
    code, out, err = run(capsys, ["run", "golddeploy", "--ref", GIT_SHA, "--fetch-artifacts"])
    assert code == 2, (code, out, err)
    assert "--output" in err, err
    assert seen["submit"] == [], seen


def test_a_standalone_fetch_needs_an_output_directory(live, env, capsys):
    """§12: `rcm artifacts JOB_ID --fetch` 에는 기준선이 없다 → `--output DIR` 이 필수다."""
    env(live)
    code, out, err = run(capsys, ["artifacts", "1", "--fetch"])
    assert code == 2, (code, out, err)
    assert "--output" in err, err


def test_plain_no_wait_prints_the_command_that_fetches_later(live, env, tree, capsys):
    """§12: 그냥 `--no-wait` 로 냈으면 끝에 받아 가는 명령 한 줄을 찍는다."""
    env(live)
    code, out, err = gold(capsys, tree, "--no-wait")
    assert code == 0, err
    jid = int(last_json(out)["job_id"])
    line = next((ln for ln in err.splitlines() if "rcm artifacts" in ln), None)
    assert line is not None, err
    assert f"rcm artifacts {jid}" in line and "--fetch" in line, line
    live.wait_terminal(jid)


# ── 받아서 트리에 쓰기 (§11 · 완료 기준 ①) ─────────────────────────────────


def test_fetch_artifacts_writes_the_goldens_back_into_the_submitted_tree(live, env, tree, capsys):
    """제출한 그 트리 뿌리의 **같은 경로**에 받아 쓴다. `a.txt` 는 갱신 · `b.txt` 는 이미 최신."""
    env(live)
    code, out, err = gold(capsys, tree, "--fetch-artifacts", "--force")
    assert code == 0, (code, out, err)
    assert (tree / "out" / "a.txt").read_text() == "v2\n"
    assert (tree / "out" / "b.txt").read_text() == "v2\n"
    assert (tree / "hello.txt").read_text() == "hello\n"  # 매니페스트 밖은 그대로


def test_the_result_line_says_how_many_were_written_not_just_compared(live, env, tree, capsys):
    """§12: `wrote 1, unchanged 1, conflicted 1` — 「12」가 무엇인지 낱말로 가른다."""
    env(live)
    code, out, err = gold(capsys, tree, "--fetch-artifacts")
    assert code == EXIT_DELIVERY, (code, out, err)
    wrote, unchanged, conflicted = counts(out, err)
    assert (wrote, unchanged, conflicted) == (1, 1, 1), (out, err)
    text = out + err
    assert re.search(r"3 files", text), text  # 비교한 것은 셋, 쓴 것은 하나


def test_a_local_file_without_a_baseline_is_not_overwritten(live, env, tree, capsys):
    """§11 2행: 제출에 없던 경로에 로컬 파일이 있으면 `--force` 없이는 안 덮는다."""
    env(live)
    code, _out, _err = gold(capsys, tree, "--fetch-artifacts")
    assert code == EXIT_DELIVERY
    assert (tree / "out" / "c.txt").read_text() == "mine\n"


def test_force_overwrites_the_conflicted_file_and_the_run_ends_clean(live, env, tree, capsys):
    """§12: `--fetch-artifacts --force` 는 `conflicted` 까지 덮는다 — 남은 충돌이 없으니 0."""
    env(live)
    code, out, err = gold(capsys, tree, "--fetch-artifacts", "--force")
    assert code == 0, (code, out, err)
    wrote, _unchanged, conflicted = counts(out, err)
    assert wrote == 2 and conflicted == 0, (out, err)
    assert (tree / "out" / "c.txt").read_text() == "v2\n"


def test_dry_run_prints_the_table_and_writes_nothing(live, env, tree, capsys):
    """§12: `--dry-run` 은 분류 표만 찍는다. 쓰지도, ack 하지도 않는다."""
    env(live)
    code, out, err = gold(capsys, tree, "--fetch-artifacts", "--dry-run")
    assert (tree / "out" / "a.txt").read_text() == "v1\n"
    assert (tree / "out" / "c.txt").read_text() == "mine\n"
    wrote, _unchanged, _conflicted = counts(out, err)
    assert wrote == 0, (out, err)
    jid = int(last_json(out)["job_id"])
    assert bundle(live, jid)["state"] == "ready", bundle(live, jid)  # ack 가 가지 않았다
    assert code in (0, EXIT_DELIVERY), code


def test_a_complete_apply_acks_and_the_unjoined_bundle_disappears(live, env, tree, capsys):
    """§7: 충돌 없이 전부 적용되면 ack. 아무도 안 붙은 잡(`join_count == 0`)은 즉시 사라진다."""
    env(live)
    code, out, _err = gold(capsys, tree, "--fetch-artifacts", "--force")
    assert code == 0
    jid = int(last_json(out)["job_id"])
    assert bundle(live, jid)["state"] == "purged", bundle(live, jid)


def test_an_incomplete_apply_never_acks(live, env, tree, capsys):
    """§7: 「충돌 빼고 전부」는 완료가 아니다 — 묶음은 서버에 남아 다시 받을 수 있다."""
    env(live)
    code, out, _err = gold(capsys, tree, "--fetch-artifacts")
    assert code == EXIT_DELIVERY
    jid = int(last_json(out)["job_id"])
    assert bundle(live, jid)["state"] == "ready", bundle(live, jid)


# ── rcm artifacts JOB_ID (§12) ──────────────────────────────────────────────


def test_artifacts_shows_the_state_and_the_manifest(live, env, tree, capsys):
    env(live)
    code, out, _err = gold(capsys, tree, "--no-wait")
    jid = int(last_json(out)["job_id"])
    live.wait_terminal(jid)
    code, out, err = run(capsys, ["artifacts", str(jid)])
    assert code == 0, (code, out, err)
    text = out + err
    assert "ready" in text, text
    assert "out/a.txt" in text and "out/c.txt" in text, text


def test_a_standalone_fetch_writes_into_the_output_directory(live, env, tree, tmp_path, capsys):
    """§12: `rcm artifacts JOB_ID --fetch --output DIR` — 기준선이 없으니 디렉터리를 명시한다."""
    env(live)
    code, out, _err = gold(capsys, tree, "--no-wait")
    jid = int(last_json(out)["job_id"])
    live.wait_terminal(jid)
    dest = tmp_path / "elsewhere"
    code, out, err = run(capsys, ["artifacts", str(jid), "--fetch", "--output", str(dest)])
    assert code == 0, (code, out, err)
    assert (dest / "out" / "a.txt").read_text() == "v2\n"
    assert (dest / "out" / "c.txt").read_text() == "v2\n"


def test_a_standalone_fetch_does_not_overwrite_an_existing_file_without_force(
    live, env, tree, tmp_path, capsys
):
    """기준선이 없으므로 이미 있는 파일은 `--force` 여야 덮는다(§11 「기준선 없음」 행)."""
    env(live)
    code, out, _err = gold(capsys, tree, "--no-wait")
    jid = int(last_json(out)["job_id"])
    live.wait_terminal(jid)
    dest = tmp_path / "elsewhere"
    (dest / "out").mkdir(parents=True)
    (dest / "out" / "a.txt").write_text("do not touch\n")
    code, out, err = run(capsys, ["artifacts", str(jid), "--fetch", "--output", str(dest)])
    assert (dest / "out" / "a.txt").read_text() == "do not touch\n"
    assert code != 0, (code, out, err)
    code, out, err = run(
        capsys, ["artifacts", str(jid), "--fetch", "--output", str(dest), "--force"]
    )
    assert code == 0, (code, out, err)
    assert (dest / "out" / "a.txt").read_text() == "v2\n"


def test_resume_never_reports_success_without_the_files(live, env, tree, tmp_path, capsys):
    """§11 절차 5: `--resume` 은 저널을 읽어 이어 한다. 저널이 없어도 **성공으로 포장하지 않는다** —
    0 으로 끝났다면 파일이 실제로 거기 있어야 한다."""
    env(live)
    code, out, _err = gold(capsys, tree, "--no-wait")
    jid = int(last_json(out)["job_id"])
    live.wait_terminal(jid)
    dest = tmp_path / "resumed"
    dest.mkdir()
    code, out, err = run(
        capsys, ["artifacts", str(jid), "--fetch", "--output", str(dest), "--resume"]
    )
    if code == 0:
        assert (dest / "out" / "a.txt").read_text() == "v2\n", (out, err)
    else:
        assert err.strip(), "조용한 실패는 없다 — 왜 못 이어 했는지 말해야 한다"


# ── 종료 코드 (§12) ─────────────────────────────────────────────────────────


def test_wait_exit_codes_and_the_json_field_are_unchanged_without_fetching(live, env, tree, capsys):
    """기존 `rcm wait` 규칙과 `wait_exit_code` 는 그대로 — 산출물을 안 받으면 키도 안 생긴다."""
    env(live)
    code, out, err = gold(capsys, tree)
    assert code == 0, err
    body = last_json(out)
    assert body["state"] == "succeeded" and body["wait_exit_code"] == 0, body
    assert "artifact_fetch" not in body, body
    code, out, err = gold(capsys, tree, preset="goldfail")
    assert code == 1, err
    body = last_json(out)
    assert body["state"] == "failed" and body["wait_exit_code"] == 1, body


def test_a_delivery_failure_uses_its_own_code_and_json_field(live, env, tree, capsys):
    """§12: 전달 실패는 **5** 와 `artifact_fetch` 로 낸다. `wait_exit_code` 는 건드리지 않는다."""
    env(live)
    code, out, err = gold(capsys, tree, "--fetch-artifacts")
    assert code == EXIT_DELIVERY, (code, err)
    body = last_json(out)
    assert body["state"] == "succeeded" and body["wait_exit_code"] == 0, body
    fetch = body.get("artifact_fetch")
    assert isinstance(fetch, dict), body
    assert set(fetch) >= {"wrote", "conflicted", "complete"}, fetch
    assert fetch["complete"] is False and fetch["conflicted"] == 1, fetch


def test_a_successful_run_whose_delivery_failed_does_not_exit_zero(live, env, tree, capsys):
    """실행은 성공했는데 전달이 실패하면 전체를 성공으로 끝내지 않는다."""
    env(live)
    code, out, _err = gold(capsys, tree, "--fetch-artifacts")
    assert last_json(out)["state"] == "succeeded"
    assert code != 0, code


def test_an_execution_failure_outranks_a_delivery_failure(live, env, tree, capsys):
    """§12: 실행 실패가 전달 실패보다 우선한다 — 테스트가 깨진 것을 먼저 알아야 한다."""
    env(live)
    code, out, err = gold(capsys, tree, "--fetch-artifacts", preset="goldfail")
    assert code == 1, (code, err)
    body = last_json(out)
    assert body["state"] == "failed" and body["wait_exit_code"] == 1, body
    assert body.get("artifact_fetch") is not None, body  # 전달 사정은 여전히 보고한다


def test_failed_jobs_still_deliver_their_diff_images(live, env, tree, capsys):
    """실패한 잡의 golden diff 가 이 기능의 이유다 — 실행이 실패해도 파일은 돌아온다."""
    env(live)
    code, _out, err = gold(capsys, tree, "--fetch-artifacts", "--force", preset="goldfail")
    assert code == 1, err
    assert (tree / "out" / "a.txt").read_text() == "v2\n"
    assert (tree / "out" / "c.txt").read_text() == "v2\n"


def test_a_preset_without_globs_is_not_a_delivery_failure(live, env, tree, capsys):
    """`disabled` 은 실패가 아니다 — 모을 것을 선언하지 않은 프리셋에 5 를 내지 않는다."""
    env(live)
    code, out, err = run(capsys, ["run", "plain", "--dir", str(tree), "--fetch-artifacts"])
    assert code == 0, (code, err)
    body = last_json(out)
    assert body["wait_exit_code"] == 0, body
    assert (tree / "out" / "a.txt").read_text() == "v1\n"  # 아무것도 안 왔다


def test_the_json_line_is_still_one_line_of_json(live, env, tree, capsys):
    """stdout 은 JSON 한 줄이라는 규칙은 그대로 — 표는 stderr 로 간다."""
    env(live)
    _code, out, _err = gold(capsys, tree, "--fetch-artifacts", "--force")
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, out
    assert json.loads(lines[0])["wait_exit_code"] == 0
