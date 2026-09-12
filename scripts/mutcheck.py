#!/usr/bin/env python3
"""뮤테이션 확인 — 테스트가 실제로 빨개지는지 본다(PLAN.md 「테스트·품질」).

`src/` + `tests/` + `pyproject.toml` 을 임시 디렉터리에 복사하고 변이 하나를 넣은 뒤 그 복사본에서
pytest 를 돌린다. **pytest 가 실패해야 통과**다. 원본은 건드리지 않는다. 변이 패턴을 못 찾으면
그 자체로 실패다(코드가 바뀌어 감시가 풀린 것).

변이 목록(개수는 `MUTANTS` 가 정본이다 — 문서의 숫자는 머지마다 썩는다):
  ① remaining-floor  — 잔여 하한 제거 (`core/queue.py`)
  ② join-key-inputs  — 합류 키에서 inputs 제외 (`core/queue.py`)
  ③ restart-lost     — 재시작 정리에서 running → lost 를 succeeded 로 (`store.py`)
  ④ stale-threshold  — 호스트 표본 stale 판정의 3×interval 을 0 으로 (`core/hostparse.py`, M1)
  ⑤ top-first-sample — macOS top 의 마지막 표본 대신 첫 표본 사용 (`core/hostparse.py`, M1)
  ⑥ web-not-moving-unknown — 웹 UI 「Not moving」이 queue null 을 「Nothing is stuck」으로
     (`web/app.js`, M2, node --test)
  ⑪ admission-fail-open — 부하 게이트가 「표본 없음」에 열림 (`core/admission.py`, M5f)
  ⑫ admission-samples-any — 창의 **전부**가 아니라 **하나라도** 기준 아래면 열림 (같은 파일)
  ⑬ web-progress-partial-total — 전체 진행 막대가 「지금까지 본」 스텝 총계를 확정 총계처럼 씀
     (`web/app.js`, node --test)
  ⑭ web-progress-default-estimate — 표본도 프리셋 값도 없는 설치 기본값(600초)으로 눈금을 그림
     (같은 파일 · Codex 리뷰 1)
  ⑮ web-progress-full-bar — 도는 잡의 예측 막대가 100% 까지 차오름 (같은 파일 · Codex 리뷰 2)
  ⑯ nowait-view-trusted — `--no-wait` 의 표시용 조회가 문서를 곧이곧대로 믿음 (`cli.py`)
  ⑰ retention-budget-active — 부피 회수가 **도는 잡과 고아**까지 후보로 삼음
     (`core/retention.py`, M5g — 이 기능의 최대 사고)
  ⑱ retention-measure-fail-open — 못 잰 크기를 None 이 아니라 0 으로 세어 예산을 지키는 척함
     (같은 파일)
  ⑲ retention-budget-unreachable — 못 이룰 목표(지울 수 없는 바이트가 이미 상한 초과)에도
     종료 잡을 전부 태움 (같은 파일 · Codex 2차 리뷰 E)
  ⑳ failed-step-fallback — 실패 스텝이 다시 「마지막으로 시작한 스텝」으로 추론됨
     (`core/progress.py`, M5h 결정 63 — 운영 잡 #162 가 이 폴백으로 성공한 스텝을 지목했다)
  ㉑ failure-window-cancelled — 이력 창이 취소·유실 잡을 분모에 넣음 (`store.py`, M5h 결정 66)
  ㉒ ledger-outside-tx — 실패 이름 대장을 finish 커밋 **뒤에** 씀 (`store.py`, M5h §2.1)
  ㉓ retention-shared-any-inode — 공유 블록 판정에서 `S_ISREG` 를 뺌: 디렉터리의 `nlink > 1`
     (하위 디렉터리 때문에 정상)까지 「지워도 안 는다」로 셈 (`janitor.py`, M5i 결정 76)
  ㉔ offline-gc-opens-original — 오프라인 dry-run 이 임시 사본이 아니라 **살아 있는 DB** 를 열어
     마이그레이션함 (`cli.py`, M5i 결정 73 — 2026-09-10 운영 DB 7→15 사고)
  ㉕ v16-without-ledger-check — v16 복구가 대장 행이 있는 **선언 라벨까지** 옮김
     (`store.py`, 결정 78)
  ㉖ client-wheel-any-name — `/client/*.whl` 이 이름이 달라도 200 (정확한 파일명 검사 제거 —
     `server.py`, M5i I8 결정 81. pip 는 URL 의 파일명으로 버전을 믿는다)
  ㉗ cancel-capability-skipped — 강제 모드(`cancel_requires_submission_token`)에서 capability
     검사를 건너뛰어 옛 공유 토큰 규칙으로 물러남 (`server.py`, M5j G5 결정 87 — 같은 토큰의
     다른 세션이 남의 잡을 지운다)

사용: python scripts/mutcheck.py [--keep] [--only NAME]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTEST_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]
    runner: str = "pytest"  # "pytest" | "node" (tests 는 node --test 에 넘길 경로)


MUTANTS = (
    Mutant(
        name="remaining-floor",
        path="src/remote_ci_monitor/core/queue.py",
        old="return max(expected - elapsed, float(cfg.floor_remaining_seconds))",
        new="return expected - elapsed",
        tests=("tests/test_queue.py", "tests/test_progress.py"),
    ),
    Mutant(
        name="join-key-inputs",
        path="src/remote_ci_monitor/core/queue.py",
        old="canonical = [preset, dict(sorted(inputs.items())), source_identity]",
        new="canonical = [preset, source_identity]",
        tests=("tests/test_queue.py",),
    ),
    Mutant(
        name="restart-lost",
        path="src/remote_ci_monitor/store.py",
        old="recover_state = LOST",
        new='recover_state = "succeeded"',
        tests=("tests/test_store.py",),
    ),
    Mutant(
        name="stale-threshold",
        path="src/remote_ci_monitor/core/hostparse.py",
        old="return age > STALE_MULTIPLIER * interval_seconds",
        new="return age > 0 * interval_seconds",
        tests=("tests/test_hostparse.py", "tests/test_status_schema.py"),
    ),
    Mutant(
        name="top-first-sample",
        path="src/remote_ci_monitor/core/hostparse.py",
        old="user_s, sys_s, idle_s = matches[-1]",
        new="user_s, sys_s, idle_s = matches[0]",
        tests=("tests/test_hostparse.py",),
    ),
    Mutant(
        name="web-progress-partial-total",
        path="src/remote_ci_monitor/web/app.js",
        old=(
            "if (isNum(total) && total > 0 && isNum(done) && !(prog && prog.steps_total_partial)) {"
        ),
        new="if (isNum(total) && total > 0 && isNum(done)) {",
        tests=("tests/web/progress_overall.test.js",),
        runner="node",
    ),
    Mutant(
        name="web-progress-default-estimate",
        path="src/remote_ci_monitor/web/app.js",
        old='&& est.source !== "default"',
        new="",
        tests=("tests/web/progress_overall.test.js",),
        runner="node",
    ),
    Mutant(
        name="web-progress-full-bar",
        path="src/remote_ci_monitor/web/app.js",
        old="Math.max(0, Math.min(99, Math.floor(elapsed / expected * 100)))",
        new="Math.max(0, Math.min(100, Math.round(elapsed / expected * 100)))",
        tests=("tests/web/progress_overall.test.js",),
        runner="node",
    ),
    Mutant(
        name="web-not-moving-unknown",
        path="src/remote_ci_monitor/web/app.js",
        old='if (!Array.isArray(q)) return { kind: "unknown", lines: [] };\n    var lines = [];',
        new='if (!Array.isArray(q)) return { kind: "ok", lines: [] };\n    var lines = [];',
        tests=("tests/web/summary.test.js",),
        runner="node",
    ),
    Mutant(
        name="retention-active-guard",
        path="src/remote_ci_monitor/core/retention.py",
        old=(
            "    if state in TERMINAL_STATES:\n"
            "        return policy.failure_days * DAY_SECONDS\n"
            "    return None\n"
        ),
        new="    return policy.failure_days * DAY_SECONDS\n",
        tests=("tests/test_retention.py",),
    ),
    Mutant(
        name="gitref-leading-dash",
        path="src/remote_ci_monitor/core/gitref.py",
        old=(
            '    if ref.startswith("-"):\n'
            "        raise ValueError(\"ref must not start with '-'\")\n"
        ),
        new="",
        tests=("tests/test_gitref.py",),
    ),
    Mutant(
        name="priority-order",
        path="src/remote_ci_monitor/core/queue.py",
        old="key=lambda j: (-j.priority, j.id)",
        new="key=lambda j: j.id",
        tests=("tests/test_priority.py",),
    ),
    Mutant(
        name="admission-fail-open",
        path="src/remote_ci_monitor/core/admission.py",
        old=(
            "    if sample is None or (now - sample.sampled_at).total_seconds()"
            " > cfg.stale_seconds:"
        ),
        new="    if False:",
        tests=("tests/test_admission.py",),
    ),
    Mutant(
        name="admission-samples-any",
        path="src/remote_ci_monitor/core/admission.py",
        old="    if any(v is None for v in values):",
        new="    if all(v is None for v in values):",
        tests=("tests/test_admission.py",),
    ),
    Mutant(
        name="retention-budget-active",
        path="src/remote_ci_monitor/core/retention.py",
        old="        (i for i in inventory if i.evictable),",
        new="        (i for i in inventory),",
        tests=("tests/test_retention_workspace.py",),
    ),
    Mutant(
        name="retention-measure-fail-open",
        path="src/remote_ci_monitor/core/retention.py",
        old="        if v is None:\n            return None\n",
        new="        if v is None:\n            continue\n",
        tests=("tests/test_retention_workspace.py",),
    ),
    Mutant(
        name="retention-budget-unreachable",
        path="src/remote_ci_monitor/core/retention.py",
        old="        if not unreachable:\n",
        new="        if True:\n",
        tests=("tests/test_retention_workspace.py",),
    ),
    Mutant(
        name="manifest-link-escape",
        path="src/remote_ci_monitor/core/manifest.py",
        old=(
            '    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):\n'
            '        raise ManifestError(f"link {path!r}: target escapes the workspace")\n'
        ),
        new="",
        tests=("tests/test_manifest.py",),
    ),
    Mutant(
        name="nowait-view-trusted",
        path="src/remote_ci_monitor/cli.py",
        old=(
            '    if not isinstance(view, dict) or not view.get("state"):\n'
            "        return None\n"
            "    try:\n"
            "        describe(view)  # 그려지는 문서만 쓴다(진짜 줄은 head 만 바꿔 다시 그린다)\n"
            "    except Exception:\n"
            "        return None\n"
            "    return view"
        ),
        new="    return view if isinstance(view, dict) else None",
        tests=("tests/test_nowait_resilience.py",),
    ),
    # ⑰ M5h 결정 63 — 이 폴백이 운영 잡 #162 에서 **성공한 스텝**을 범인으로 지목했다.
    Mutant(
        name="failed-step-fallback",
        path="src/remote_ci_monitor/core/progress.py",
        old="""    failed = next((s.name for s in steps if s.ok is False), None)
""",
        new="""    failed = next((s.name for s in steps if s.ok is False), None)
    if failed is None and exit_code not in (None, 0) and steps:
        failed = steps[-1].name
""",
        tests=("tests/test_progress_m5h.py", "tests/test_progress.py"),
    ),
    # ⑱ M5h 결정 66 — 취소·유실 잡은 아무 말도 안 한다. 분모에 넣으면 간헐 판정이 흐려진다.
    Mutant(
        name="failure-window-cancelled",
        path="src/remote_ci_monitor/store.py",
        old="""        states = (SUCCEEDED, FAILED, TIMED_OUT)
""",
        new="""        states = (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, LOST)
""",
        tests=("tests/test_store_m5h.py",),
    ),
    # ⑲ M5h §2.1 — 증거와 결과는 같은 커밋이다. 대장을 커밋 **뒤로** 옮기면(= 실패한 대장
    # 쓰기가 finish 를 되돌리지 못하면) 빨개져야 한다.
    # ㉓ 결정 73 — 사본 대신 원본을 열면 원본이 마이그레이션된다(user_version 7 → 16). 사고 그 자체.
    Mutant(
        name="offline-gc-opens-original",
        path="src/remote_ci_monitor/cli.py",
        old="            store = Store(copy, log=_err)",
        new="            store = Store(db, log=_err)",
        tests=("tests/test_offline_gc.py",),
    ),
    # ㉔ 결정 78 — 「대장 행이 하나도 없다」 조건을 빼면 새 코드의 선언 라벨도 옮겨진다.
    Mutant(
        name="v16-without-ledger-check",
        path="src/remote_ci_monitor/store.py",
        old=""""WHERE state IN ('failed','timed_out') AND failed_step IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM job_failures WHERE job_failures.job_id=jobs.id)",""",
        new=""""WHERE state IN ('failed','timed_out') AND failed_step IS NOT NULL",""",
        tests=("tests/test_store_m5i.py",),
    ),
    Mutant(
        name="client-wheel-any-name",
        path="src/remote_ci_monitor/server.py",
        old="        if name != expected:\n            raise ApiError(\n                404,",
        new="        if False:\n            raise ApiError(\n                404,",
        tests=("tests/test_client_wheel.py",),
    ),
    Mutant(
        name="cancel-capability-skipped",
        path="src/remote_ci_monitor/server.py",
        old="if self.config.server.cancel_requires_submission_token and not token.admin:",
        new="if False and not token.admin:",
        tests=("tests/test_cancel_capability.py",),
    ),
    Mutant(
        name="ledger-outside-tx",
        path="src/remote_ci_monitor/store.py",
        old="""            for seq, name in enumerate(fail_names, start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO job_failures(job_id, name, seq) VALUES (?,?,?)",
                    (job_id, name, seq),
                )
            if bundle is not None:
                _upsert_bundle(conn, job_id, bundle, now=now, ttl_hours=ttl_hours)
            conn.execute("COMMIT")
""",
        new="""            if bundle is not None:
                _upsert_bundle(conn, job_id, bundle, now=now, ttl_hours=ttl_hours)
            conn.execute("COMMIT")
            for seq, name in enumerate(fail_names, start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO job_failures(job_id, name, seq) VALUES (?,?,?)",
                    (job_id, name, seq),
                )
""",
        tests=("tests/test_store_m5h.py",),
    ),
    # ㉓ M5i 결정 76 — 공유 블록은 **일반 파일**의 `nlink > 1` 만이다. 디렉터리를 세면 ext4 에서
    # 모든 워크스페이스가 「지워도 안 는다」 쪽으로 새고, 바닥 규칙이 필요 이상 지운다.
    Mutant(
        name="retention-shared-any-inode",
        path="src/remote_ci_monitor/janitor.py",
        old="    if st.st_nlink > 1 and stat.S_ISREG(st.st_mode):\n",
        new="    if st.st_nlink > 1:\n",
        tests=("tests/test_janitor_m5i.py",),
    ),
)


def _pytest(cmd: list[str], cwd: Path) -> tuple[int, float, str] | None:
    """(exit code, seconds, output tail) — timeout 이면 None."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=PYTEST_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return None
    took = time.monotonic() - started
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, took, "\n".join(lines[-6:])


def run_mutant(m: Mutant, keep: bool) -> tuple[bool, str]:
    """복사본에 변이를 넣고 pytest 를 돌린다. (감지됨?, 설명)."""
    tmp = Path(tempfile.mkdtemp(prefix=f"mutcheck-{m.name}-"))
    try:
        for name in ("src", "tests", "pyproject.toml"):
            src = ROOT / name
            if src.is_dir():
                shutil.copytree(src, tmp / name, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src, tmp / name)
        target = tmp / m.path
        text = target.read_text(encoding="utf-8")
        if text.count(m.old) != 1:
            return False, f"pattern not found exactly once in {m.path}: {m.old!r}"
        if m.runner == "node":
            node = shutil.which("node")
            if node is None:
                return False, "node is not installed (needed for the web mutant)"
            cmd = [node, "--test", *m.tests]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *m.tests]
        # 대조군: 변이 없이 복사본에서 초록이어야 한다(환경 문제로 빨간 것을 감지로 착각하지 않게)
        control = _pytest(cmd, tmp)
        if control is None:
            return False, f"control run timed out: {' '.join(cmd)}"
        if control[0] != 0:
            return False, f"control run is RED without the mutant (exit {control[0]})\n{control[2]}"
        target.write_text(text.replace(m.old, m.new), encoding="utf-8")
        mutated = _pytest(cmd, tmp)
        if mutated is None:
            return False, f"mutant run timed out: {' '.join(cmd)}"
        code, took, tail = mutated
        if code == 0:
            return False, f"tests stayed GREEN with mutant applied ({took:.1f}s)\n{tail}"
        return True, f"control green, mutant red (exit {code}, {took:.1f}s)"
    finally:
        if keep:
            print(f"  kept copy: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check that tests catch known mutations.")
    ap.add_argument("--keep", action="store_true", help="keep the mutated copies")
    ap.add_argument("--only", help="run one mutant by name")
    args = ap.parse_args(argv)
    mutants = [m for m in MUTANTS if not args.only or m.name == args.only]
    if not mutants:
        print(f"no mutant named {args.only!r}", file=sys.stderr)
        return 2
    failures = 0
    for m in mutants:
        ok, info = run_mutant(m, args.keep)
        mark = "OK " if ok else "FAIL"
        print(f"[{mark}] {m.name}: {info}")
        if not ok:
            failures += 1
    if failures:
        print(f"mutcheck: {failures} of {len(mutants)} mutants NOT caught", file=sys.stderr)
        return 1
    print(f"mutcheck: all {len(mutants)} mutants caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
