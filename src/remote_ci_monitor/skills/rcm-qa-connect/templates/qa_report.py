#!/usr/bin/env python3
"""qa_report.py — rcm `qa` 역할의 report.json 을 만들고 검증한다(표준 라이브러리만).

잡 본체(scripts/rcm/qa.sh)는 판정을 하지 않는다. 단위(unit)마다 결과 한 줄을 results 파일에 적고,
이 스크립트가 그 줄들로 report.json 을 **한 번만** 쓴다. 계약은 rcm 의 docs/release-contract.md §2:

    { "schema": 1, "verdict": "PASS|FAIL|BLOCKED",
      "platforms": { "<plat>": { "verdict": "...", "chunks": N, "failed": M } | "not_run" },
      "failures": [ { "chunk_id", "platform", "rc", "step", "screenshot" } ] }

results 파일(탭 구분, 잡 본체가 쓴다):
    unit<TAB><unit_id><TAB><platform><TAB><rc><TAB><log path|-><TAB><screenshot|->
    blocked<TAB><platform><TAB><exit code 2|10><TAB><reason>

rc 규칙(단위 하나): 0 PASS · 1 FAIL · 2 환경(증거 없이 죽음) · 10 기기 BLOCKED · 그 밖은 FAIL.
플랫폼 판정은 최악 우선(BLOCKED(10) > FAIL(1) > 환경(2) > PASS(0)). 앞 플랫폼이 빨개서 안 돈
플랫폼은 `not_run` — 줄이 하나도 없으면 not_run 이지 PASS 가 아니다(fail-closed).

「선언 == 실행」: --declared 목록의 단위가 어느 플랫폼에서든 덜 돌았는데 그 플랫폼이 PASS 면
BLOCKED 로 접는다(덜 돌고 초록은 없다). skip(rc 자리의 `skip`)은 실행으로 센다 — 「그 플랫폼엔
그 화면이 없다」다.

사용:
    qa_report.py collect --results R --declared D --order ios,android --out report.json
        → report.json 을 쓰고 stdout 에 "<VERDICT> <exit> <plat>=<v> ..." 한 줄
    qa_report.py collect --order ... --out report.json --blocked "reason" [--exit-code 2|10]
        → 아무것도 안 돈 채 막혔을 때(프리플라이트) — 전 플랫폼 not_run
    qa_report.py validate report.json          → 계약 위반이면 1 과 사유
    qa_report.py check-markers stdout.log      → 마커 불변식(분자 ≤ 분모 · summary 마지막) 어기면 1
    qa_report.py --selftest                    → 가짜 결과로 위 셋을 실증
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

VERDICTS = ("PASS", "FAIL", "BLOCKED")
EXIT_OF = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}
PROGRESS_RE = re.compile(
    r"^::rcm::progress::(\d+)/(\d+)::([^:]+(?::[^:]+)*?)::([a-z]+)(?:::(.*))?$"
)
STATES = {"run", "ok", "fail", "skip", "env", "review", "blocked", "wait"}


def rank(rc: str) -> int:
    """최악 우선순위 — 기기 BLOCKED(10) > FAIL(1) > 환경(2) > PASS(0). skip 은 PASS 와 같다."""
    if rc == "skip":
        return 0
    try:
        n = int(rc)
    except ValueError:
        return 3
    return {10: 4, 1: 3, 2: 2, 0: 0}.get(n, 3)


def verdict_of_rank(r: int) -> str:
    return {0: "PASS", 2: "BLOCKED", 3: "FAIL", 4: "BLOCKED"}[r]


def parse_results(path: str | None) -> tuple[list[dict], dict[str, dict]]:
    """results 파일 → (단위 줄들, 플랫폼별 blocked 줄). 없는 파일은 빈 결과다."""
    units: list[dict] = []
    blocked: dict[str, dict] = {}
    if not path or not os.path.exists(path):
        return units, blocked
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            cols = line.split("\t")
            if cols[0] == "unit" and len(cols) >= 4:
                units.append(
                    {
                        "chunk_id": cols[1],
                        "platform": cols[2],
                        "rc": cols[3],
                        "log": cols[4] if len(cols) > 4 and cols[4] != "-" else None,
                        "screenshot": cols[5] if len(cols) > 5 and cols[5] != "-" else None,
                    }
                )
            elif cols[0] == "blocked" and len(cols) >= 4:
                blocked[cols[1]] = {
                    "exit_code": int(cols[2]) if cols[2].isdigit() else 2,
                    "reason": cols[3],
                }
    return units, blocked


def build_report(
    order: list[str],
    units: list[dict],
    blocked: dict[str, dict],
    declared: list[str] | None,
    blocked_all: str | None = None,
    blocked_exit: int = 2,
) -> tuple[dict, int]:
    """report.json 본문과 종료 코드를 만든다. 순수 함수 — selftest 가 바로 문다."""
    platforms: dict[str, object] = {}
    failures: list[dict] = []
    blockers: list[str] = []
    coverage: dict[str, object] = {
        "declared": len(declared) if declared is not None else None,
        "executed": {},
    }
    exit_code = 0
    worst = "PASS"

    def bump(v: str) -> None:
        nonlocal worst
        order_v = {"PASS": 0, "BLOCKED": 1, "FAIL": 2}
        if order_v[v] > order_v[worst]:
            worst = v

    if blocked_all:
        for plat in order:
            platforms[plat] = "not_run"
        blockers.append(blocked_all)
        rep = {
            "schema": 1,
            "verdict": "BLOCKED",
            "platforms": platforms,
            "failures": [],
            "blockers": blockers,
            "coverage": coverage,
        }
        return rep, (blocked_exit if blocked_exit in (2, 10) else 2)

    for plat in order:
        mine = [u for u in units if u["platform"] == plat]
        if not mine and plat not in blocked:
            platforms[plat] = "not_run"
            continue
        r = 0
        ran = 0
        failed = 0
        for u in mine:
            r = max(r, rank(u["rc"]))
            ran += 1
            if rank(u["rc"]) >= 2:
                failed += 1
                if u["rc"] in ("2", "10"):
                    # BLOCKED 에는 사유가 있어야 한다 — 단위 이름과 로그의 마지막 줄을 싣는다.
                    why = "died without evidence" if u["rc"] == "2" else "device blocked"
                    blockers.append(
                        f"{plat}: {u['chunk_id']} rc {u['rc']} ({why}){log_tail(u['log'])}"
                    )
                failures.append(
                    {
                        "chunk_id": u["chunk_id"],
                        "platform": plat,
                        "rc": int(u["rc"]) if u["rc"].isdigit() else u["rc"],
                        "step": u["log"] or "",
                        "screenshot": u["screenshot"],
                    }
                )
        if plat in blocked:
            r = max(r, 4 if blocked[plat]["exit_code"] == 10 else 2)
            blockers.append(f"{plat}: {blocked[plat]['reason']}")
        v = verdict_of_rank(r)
        # 「선언 == 실행」 — PASS 인데 덜 돌았으면 초록이 아니다.
        if declared is not None:
            ids_ran = {u["chunk_id"] for u in mine}
            missing = [d for d in declared if d not in ids_ran]
            coverage["executed"][plat] = len(ids_ran & set(declared))
            if v == "PASS" and missing:
                v = "BLOCKED"
                r = max(r, 2)
                blockers.append(
                    f"{plat}: {len(missing)} of {len(declared)} declared units did not run"
                )
        else:
            coverage["executed"][plat] = ran
        platforms[plat] = {"verdict": v, "chunks": ran, "failed": failed}
        bump(v)
        if r == 4:
            exit_code = 10
    # rc 2·10 은 blockers 쪽이 더 맞지만 계약은 failures[] 에 rc 를 그대로 싣는 것을 허용한다.
    #   FAIL 이면 failures 가 반드시 비지 않는다.
    verdict = worst
    if verdict == "FAIL" and not failures:
        verdict = "BLOCKED"  # 계약: FAIL 은 failures 를 요구한다 — 비면 fail-closed 로 BLOCKED
        blockers.append("verdict was FAIL without a failure record — folded to BLOCKED")
    if verdict == "BLOCKED" and exit_code != 10:
        exit_code = 2
    if verdict == "FAIL":
        exit_code = 1
    if verdict == "PASS":
        exit_code = 0
    if all(v == "not_run" for v in platforms.values()):
        verdict = "BLOCKED"
        exit_code = 2
        blockers.append("no platform ran")
    rep = {
        "schema": 1,
        "verdict": verdict,
        "platforms": platforms,
        "failures": failures,
        "blockers": blockers,
        "coverage": coverage,
    }
    return rep, exit_code


def log_tail(path: str | None) -> str:
    """단위 로그의 마지막 비어 있지 않은 줄 — 사유 한 줄. 못 읽으면 빈 문자열."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return ""
    return f" — {lines[-1][:160]}" if lines else ""


def validate_report(rep: object) -> list[str]:
    """계약 §2 의 최소 필드를 검사한다. 빈 목록 = 유효."""
    errs: list[str] = []
    if not isinstance(rep, dict):
        return ["report is not an object"]
    if rep.get("schema") != 1:
        errs.append("schema must be 1")
    v = rep.get("verdict")
    if v not in VERDICTS:
        errs.append(f"verdict must be one of {'|'.join(VERDICTS)}: {v!r}")
    plats = rep.get("platforms")
    if not isinstance(plats, dict) or not plats:
        errs.append("platforms must be a non-empty object")
        plats = {}
    for name, p in plats.items():
        if p == "not_run":
            continue
        if not isinstance(p, dict):
            errs.append(f"platforms.{name} must be an object or 'not_run'")
            continue
        if p.get("verdict") not in VERDICTS:
            errs.append(f"platforms.{name}.verdict must be PASS|FAIL|BLOCKED")
        for k in ("chunks", "failed"):
            if not isinstance(p.get(k), int) or p[k] < 0:
                errs.append(f"platforms.{name}.{k} must be a non-negative integer")
    fails = rep.get("failures")
    if not isinstance(fails, list):
        errs.append("failures must be a list")
        fails = []
    for i, f in enumerate(fails):
        if not isinstance(f, dict) or not f.get("chunk_id") or not f.get("platform"):
            errs.append(f"failures[{i}] needs chunk_id and platform")
        elif f["platform"] not in plats:
            errs.append(f"failures[{i}].platform {f['platform']!r} is not in platforms")
    if v == "FAIL" and not fails:
        errs.append("verdict FAIL requires a non-empty failures[]")
    if v == "PASS":
        bad = [n for n, p in plats.items() if isinstance(p, dict) and p.get("verdict") != "PASS"]
        if bad:
            errs.append(f"verdict PASS but platforms not PASS: {', '.join(bad)}")
        if fails:
            errs.append("verdict PASS with failures[] non-empty")
    if v == "BLOCKED" and not rep.get("blockers"):
        errs.append("verdict BLOCKED should name at least one blocker")
    return errs


def check_markers(lines: list[str]) -> list[str]:
    """stdout 의 ::rcm:: 마커 불변식. 빈 목록 = 유효.

    · progress 분자 ≤ 분모, 분모는 0 이 아니고 한 잡 안에서 바뀌지 않는다
    · state 는 계약의 어휘 안
    · ::rcm::steps::N 이 있으면 step 줄은 N 개 이하
    · 마지막 ::rcm:: 줄은 summary
    """
    errs: list[str] = []
    total = None
    steps_n = None
    step_lines = 0
    last = None
    for ln in lines:
        if not ln.startswith("::rcm::"):
            continue
        last = ln
        if ln.startswith("::rcm::steps::"):
            try:
                steps_n = int(ln.split("::")[3])
            except (IndexError, ValueError):
                errs.append(f"bad steps line: {ln}")
        elif ln.startswith("::rcm::step::"):
            step_lines += 1
        elif ln.startswith("::rcm::progress::"):
            m = PROGRESS_RE.match(ln)
            if not m:
                errs.append(f"bad progress line: {ln}")
                continue
            done, tot, _unit, state = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
            if tot <= 0:
                errs.append(f"progress denominator must be > 0: {ln}")
            if done > tot:
                errs.append(f"progress numerator exceeds denominator: {ln}")
            if total is not None and tot != total:
                errs.append(f"progress denominator changed {total} -> {tot}: {ln}")
            total = tot
            if state not in STATES:
                errs.append(f"progress state {state!r} not in {sorted(STATES)}")
    if steps_n is not None and step_lines > steps_n:
        errs.append(f"{step_lines} step lines but steps::{steps_n} declared")
    if last is None or not last.startswith("::rcm::summary::"):
        errs.append("last ::rcm:: line is not a summary")
    return errs


def cmd_collect(a: argparse.Namespace) -> int:
    order = [p for p in a.order.split(",") if p]
    if not order:
        print("collect: --order is empty", file=sys.stderr)
        return 2
    declared = None
    if a.declared and os.path.exists(a.declared):
        with open(a.declared, encoding="utf-8") as fh:
            declared = [ln.strip() for ln in fh if ln.strip()]
    units, blocked = parse_results(a.results)
    rep, code = build_report(order, units, blocked, declared, a.blocked, a.exit_code)
    errs = validate_report(rep)
    if errs:  # 자기 출력이 계약을 어기면 그대로 내보내지 않는다
        for e in errs:
            print(f"collect: {e}", file=sys.stderr)
        rep["verdict"] = "BLOCKED"
        rep.setdefault("blockers", []).extend(errs)
        code = 2
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    plat_words = " ".join(
        f"{k}={v['verdict'] if isinstance(v, dict) else v}" for k, v in rep["platforms"].items()
    )
    print(f"{rep['verdict']} {code} {plat_words}")
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    try:
        with open(a.path, encoding="utf-8") as fh:
            rep = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"validate: cannot read {a.path}: {e}", file=sys.stderr)
        return 1
    errs = validate_report(rep)
    for e in errs:
        print(f"validate: {e}", file=sys.stderr)
    return 1 if errs else 0


def cmd_check_markers(a: argparse.Namespace) -> int:
    with open(a.path, encoding="utf-8", errors="replace") as fh:
        errs = check_markers([ln.rstrip("\n") for ln in fh])
    for e in errs:
        print(f"check-markers: {e}", file=sys.stderr)
    return 1 if errs else 0


def selftest() -> int:
    """가짜 결과로 실증한다 — 오염된 보고서 거부 · not_run 전파 · 분자 ≤ 분모."""
    fails = 0

    def t(name: str, got: object, want: object) -> None:
        nonlocal fails
        if got == want:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name} — got {got!r} want {want!r}")
            fails += 1

    order = ["ios", "android"]
    declared = ["a", "b"]

    # ① 전부 PASS
    rep, code = build_report(
        order,
        [u("a", "ios", "0"), u("b", "ios", "0"), u("a", "android", "0"), u("b", "android", "0")],
        {},
        declared,
    )
    t("all pass -> PASS/0", (rep["verdict"], code), ("PASS", 0))
    t("all pass validates", validate_report(rep), [])

    # ② iOS 에 FAIL 하나, android 줄 없음 → FAIL/1, android not_run, failures 비지 않음
    rep, code = build_report(order, [u("a", "ios", "1"), u("b", "ios", "0")], {}, declared)
    t("ios fail -> FAIL/1", (rep["verdict"], code), ("FAIL", 1))
    t("android not_run propagates", rep["platforms"]["android"], "not_run")
    t("failures non-empty", [f["chunk_id"] for f in rep["failures"]], ["a"])
    t("fail report validates", validate_report(rep), [])

    # ③ 오염된 보고서 — FAIL 인데 failures 가 빔 → validate 거부
    poisoned = {
        "schema": 1,
        "verdict": "FAIL",
        "platforms": {"ios": {"verdict": "FAIL", "chunks": 2, "failed": 1}},
        "failures": [],
    }
    t(
        "poisoned FAIL/empty failures rejected",
        any("non-empty failures" in e for e in validate_report(poisoned)),
        True,
    )
    # ③b PASS 인데 플랫폼이 FAIL → 거부 · not_run 을 PASS 로 속이는 것도 거부
    t(
        "PASS with failing platform rejected",
        validate_report(
            {
                "schema": 1,
                "verdict": "PASS",
                "platforms": {"ios": {"verdict": "FAIL", "chunks": 1, "failed": 1}},
                "failures": [],
            }
        )
        != [],
        True,
    )
    t(
        "unknown verdict word rejected",
        validate_report(
            {"schema": 1, "verdict": "GREEN", "platforms": {"ios": "not_run"}, "failures": []}
        )
        != [],
        True,
    )
    t(
        "empty platforms rejected",
        validate_report({"schema": 1, "verdict": "PASS", "platforms": {}, "failures": []}) != [],
        True,
    )

    # ④ 덜 돌고 초록은 없다 — 선언 2 개, ios 1 개만 돌고 PASS → BLOCKED/2
    rep, code = build_report(order, [u("a", "ios", "0")], {}, declared)
    t("under-coverage PASS -> BLOCKED/2", (rep["verdict"], code), ("BLOCKED", 2))
    t("coverage names the gap", any("did not run" in b for b in rep["blockers"]), True)
    # ④b skip 은 실행으로 센다
    rep, code = build_report(
        order,
        [u("a", "ios", "0"), u("b", "ios", "skip"), u("a", "android", "0"), u("b", "android", "0")],
        {},
        declared,
    )
    t("skip counts as executed", (rep["verdict"], code), ("PASS", 0))

    # ⑤ 기기 BLOCKED(10) → exit 10 · 환경(2) → exit 2 · FAIL 이 환경을 이긴다
    rep, code = build_report(order, [u("a", "ios", "10"), u("b", "ios", "0")], {}, declared)
    t("device blocked -> BLOCKED/10", (rep["verdict"], code), ("BLOCKED", 10))
    rep, code = build_report(order, [u("a", "ios", "2"), u("b", "ios", "0")], {}, declared)
    t("env -> BLOCKED/2", (rep["verdict"], code), ("BLOCKED", 2))
    t("env blocker names the unit", any("a rc 2" in b for b in rep["blockers"]), True)
    t("env report validates without the validator's own complaint", validate_report(rep), [])
    rep, code = build_report(order, [u("a", "ios", "1"), u("b", "ios", "2")], {}, declared)
    t("FAIL beats env", (rep["verdict"], code), ("FAIL", 1))
    # ⑤b 플랫폼 단위 blocked 줄(기기 부팅 실패) — 단위 0 개
    rep, code = build_report(
        order, [], {"ios": {"exit_code": 10, "reason": "no simulator"}}, declared
    )
    t(
        "platform blocked line -> BLOCKED/10, android not_run",
        (rep["verdict"], code, rep["platforms"]["android"]),
        ("BLOCKED", 10, "not_run"),
    )

    # ⑥ 프리플라이트에서 막힘 — 전 플랫폼 not_run · blockers 에 사유
    rep, code = build_report(order, [], {}, declared, blocked_all="tool missing", blocked_exit=2)
    t(
        "preflight blocked -> all not_run",
        (rep["verdict"], code, set(rep["platforms"].values())),
        ("BLOCKED", 2, {"not_run"}),
    )
    t("preflight blocked validates", validate_report(rep), [])

    # ⑦ 마커 불변식 — 분자 ≤ 분모 · 분모 고정 · summary 마지막
    good = [
        "::rcm::steps::5",
        "::rcm::step::preflight",
        "::rcm::progress::0/4::a/ios::run",
        "::rcm::progress::1/4::a/ios::ok",
        "::rcm::progress::4/4::b/android::ok",
        "::rcm::summary::PASS",
    ]
    t("good markers accepted", check_markers(good), [])
    t(
        "numerator > denominator rejected",
        any(
            "exceeds" in e
            for e in check_markers(
                good[:2] + ["::rcm::progress::5/4::a/ios::ok", "::rcm::summary::PASS"]
            )
        ),
        True,
    )
    t(
        "denominator drift rejected",
        any(
            "changed" in e
            for e in check_markers(
                good[:3] + ["::rcm::progress::1/5::a/ios::ok", "::rcm::summary::PASS"]
            )
        ),
        True,
    )
    t(
        "summary must be last",
        any("summary" in e for e in check_markers(good + ["::rcm::step::late"])),
        True,
    )
    t(
        "unknown state rejected",
        any(
            "state" in e
            for e in check_markers(
                good[:2] + ["::rcm::progress::1/4::a/ios::green", "::rcm::summary::PASS"]
            )
        ),
        True,
    )

    # ⑧ 파일 왕복 — collect 가 쓴 report.json 을 validate 가 받아들인다
    with tempfile.TemporaryDirectory() as tmp:
        res = os.path.join(tmp, "results.tsv")
        dec = os.path.join(tmp, "declared.txt")
        out = os.path.join(tmp, "report.json")
        with open(dec, "w", encoding="utf-8") as fh:
            fh.write("a\nb\n")
        with open(res, "w", encoding="utf-8") as fh:
            fh.write("unit\ta\tios\t1\t/x/a-ios.log\t/x/a.png\nunit\tb\tios\t0\t-\t-\n")
        log = os.path.join(tmp, "c-ios.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("TODO(project): run_unit is not implemented\n")
        rep2, _ = build_report(
            ["ios"],
            [{"chunk_id": "c", "platform": "ios", "rc": "2", "log": log, "screenshot": None}],
            {},
            ["c"],
        )
        t(
            "blocker carries the log's last line",
            any("run_unit is not implemented" in b for b in rep2["blockers"]),
            True,
        )
        rc = main(
            ["collect", "--results", res, "--declared", dec, "--order", "ios,android", "--out", out]
        )
        t("collect exits 0", rc, 0)
        t("collect output validates", main(["validate", out]), 0)
        with open(out, encoding="utf-8") as fh:
            rep = json.load(fh)
        t("round-trip keeps screenshot", rep["failures"][0]["screenshot"], "/x/a.png")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(poisoned, fh)
        t("validate rejects poisoned file", main(["validate", out]), 1)

    print("selftest", "PASS" if fails == 0 else f"FAIL({fails})")
    return 0 if fails == 0 else 1


def u(cid: str, plat: str, rc: str) -> dict:
    return {"chunk_id": cid, "platform": plat, "rc": rc, "log": None, "screenshot": None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("collect")
    c.add_argument("--results")
    c.add_argument("--declared")
    c.add_argument("--order", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--blocked", help="nothing ran: reason for a BLOCKED report")
    c.add_argument(
        "--exit-code", type=int, default=2, help="with --blocked: 2 environment, 10 device"
    )
    v = sub.add_parser("validate")
    v.add_argument("path")
    m = sub.add_parser("check-markers")
    m.add_argument("path")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.cmd == "collect":
        return cmd_collect(a)
    if a.cmd == "validate":
        return cmd_validate(a)
    if a.cmd == "check-markers":
        return cmd_check_markers(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
