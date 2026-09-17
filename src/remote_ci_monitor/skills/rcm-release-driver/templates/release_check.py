#!/usr/bin/env python3
"""release_check.py — 릴리스 드라이버의 판단 전부(stdlib 만).

bash 는 명령을 부르고, 판단은 여기서 한다.

    stage     --prs F --statuses F --jobs F --tags F --sha SHA --build-name X.Y.Z
              [--build-number N] --tag-pattern 'prod/{version}-{build}' --upload-preset NAME
              --gate-context CTX [--qa-context CTX] [--skip-qa]   → 다음 단계 이름 한 줄
    validate  plan|upload FILE --build-name X.Y.Z [--build-number N] [--mode rehearsal|upload]
              [--tag-pattern …]                 → 0 이면 계약에 맞음, 1 이면 이유를 한 줄씩
    exitcode  --rcm-json FILE                   → "<code> <reason>"  (rcm wait 의 마지막 JSON 줄)
    find-job  --jobs F --preset NAME --sha SHA [--states a,b] [--inputs k=v,k=v]
                                                → "id state" 또는 빈 줄
    confirm   --typed N --plan FILE             → 사람이 친 N 이 plan.json 의 n 과 같을 때만 0
    get       --file F --key K                  → 상태 캐시의 값(없으면 빈 줄)
    profile   --file scripts/rcm/profile.release.toml [--repo NAME] [--presets FILE]
                                                → 드라이버가 읽는 KEY=value 줄들(조각에서)
    --selftest                                  → 오염 입력이 실제로 빨개지는지

종료코드(드라이버 계약): 0 완료 · 1 빨강/계약 위반/N 불일치/이미 릴리스됨 · 2 전제 조건/N 없음 ·
3 결과 불명(잡 lost/timed_out — 업로드는 절대 재제출하지 않는다) · 4 스토어 드리프트.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

EXIT_DONE, EXIT_RED, EXIT_PREREQ, EXIT_UNKNOWN, EXIT_DRIFT = 0, 1, 2, 3, 4
STAGES = ("S1", "S3", "S4", "S5", "S6", "S7", "S8", "DONE", "BLOCKED_PR_CLOSED")
BUILD_NAME_RE = re.compile(r"^\d+\.\d+\.\d+$")


# ── 입출력 ────────────────────────────────────────────────────────────────────
def _load(path: str, default: Any) -> Any:
    """JSON 파일 하나. 없거나 깨졌으면 default — 판정은 «모른다 = 안 붙는다» 로 접힌다."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []


def render_tag(pattern: str, build_name: str, build_number: str | int) -> str:
    return pattern.replace("{version}", build_name).replace("{build}", str(build_number))


# ── 원격 진실 → 단계 ──────────────────────────────────────────────────────────
def status_state(statuses: Any, context: str) -> str | None:
    """커밋 combined status(`gh api repos/R/commits/SHA/status`) 의 한 context 의 state."""
    rows = statuses.get("statuses", []) if isinstance(statuses, dict) else (statuses or [])
    for row in rows:
        if row.get("context") == context:
            return row.get("state")
    return None


def pick_pr(prs: list[dict[str, Any]], sha: str) -> dict[str, Any] | None:
    """**이 sha 를 head 로 가진** PR 하나 — merged > open > closed.

    브랜치 이름만으로 고르면 같은 버전명을 새 N 으로 다시 올리는 회차에서 지난 회차의 MERGED PR 을
    집어 새 sha 를 머지도 없이 S7 로 보낸다. headRefOid 가 없는 행은 못 믿으니 안 집는다
    (fail-closed).
    """
    rank = {"MERGED": 0, "OPEN": 1, "CLOSED": 2}
    best: tuple[int, dict[str, Any]] | None = None
    for pr in prs or []:
        if (pr.get("headRefOid") or "") != sha:
            continue
        key = rank.get((pr.get("state") or "").upper(), 3)
        if best is None or key < best[0]:
            best = (key, pr)
    return best[1] if best else None


def find_job(
    jobs: Any,
    preset: str,
    sha: str,
    states: set[str] | None = None,
    inputs: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """같은 preset + 같은 sha (+ 같은 입력) 의 가장 최근 잡.

    inputs 를 줬는데 잡 행에 inputs 가 없으면 모른다 = 안 붙는다 — 리허설 잡을 업로드 잡으로
    세는 일이 없게.
    """
    best: dict[str, Any] | None = None
    for row in jobs or []:
        if row.get("preset") != preset:
            continue
        src = row.get("source") or {}
        if (src.get("sha") or row.get("sha")) != sha:
            continue
        if states is not None and row.get("state") not in states:
            continue
        if inputs:
            have = row.get("inputs")
            if not isinstance(have, dict) or any(
                str(have.get(k)) != str(v) for k, v in inputs.items()
            ):
                continue
        jid = row.get("id", row.get("job_id")) or 0
        if best is None or jid > (best.get("id", best.get("job_id")) or 0):
            best = row
    return best


def decide_stage(
    *,
    prs: list[dict[str, Any]],
    statuses: Any,
    jobs: Any,
    tags: list[str],
    sha: str,
    build_name: str,
    build_number: str | int | None,
    tag_pattern: str,
    upload_preset: str,
    gate_context: str | None,
    qa_context: str | None,
    skip_qa: bool,
) -> str:
    """다음 단계. 로컬 상태파일은 보지 않는다.

    DONE 은 정확한 태그(`tag_pattern` 에 X.Y.Z 와 N 을 넣은 것)가 있을 때뿐이고, N 을 모르면 DONE 을
    돌려주지 않는다 — 같은 버전명의 다른 빌드 태그는 «끝났다» 가 아니다.
    """
    if build_number and render_tag(tag_pattern, build_name, build_number) in set(tags):
        return "DONE"
    pr = pick_pr(prs, sha)
    if pr is None:
        return "S1"
    state = (pr.get("state") or "").upper()
    if state == "MERGED" or pr.get("mergedAt"):
        up = None
        if build_number:
            up = find_job(
                jobs,
                upload_preset,
                sha,
                {"succeeded"},
                {"mode": "upload", "confirm_build_number": str(build_number)},
            )
        return "S8" if up is not None else "S7"
    if state == "CLOSED":
        return "BLOCKED_PR_CLOSED"
    gate_ok = not gate_context or status_state(statuses, gate_context) == "success"
    qa_ok = skip_qa or not qa_context or status_state(statuses, qa_context) == "success"
    if gate_ok and qa_ok:
        return "S6"
    if gate_ok:
        return "S5"
    return "S4"


# ── 산출물 계약 ───────────────────────────────────────────────────────────────
def validate_plan(doc: Any, build_name: str) -> list[str]:
    bad: list[str] = []
    if not isinstance(doc, dict):
        return ["plan.json is not an object"]
    if doc.get("schema") != 1:
        bad.append("schema != 1")
    if doc.get("build_name") != build_name:
        bad.append(f"build_name {doc.get('build_name')!r} != {build_name!r}")
    n = doc.get("n")
    blockers = doc.get("blockers")
    if n is None:
        if not isinstance(blockers, list) or not blockers:
            bad.append("n is null but blockers[] is empty")
    elif not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        bad.append(f"n must be a positive integer, got {n!r}")
    return bad


def validate_upload(
    doc: Any, build_number: str | int | None, mode: str, expected_tag: str | None
) -> list[str]:
    bad: list[str] = []
    if not isinstance(doc, dict):
        return ["upload.json is not an object"]
    if doc.get("schema") != 1:
        bad.append("schema != 1")
    if build_number is not None and str(doc.get("n")) != str(build_number):
        bad.append(f"n {doc.get('n')!r} != confirmed {build_number!r}")
    if doc.get("mode") != mode:
        bad.append(f"mode {doc.get('mode')!r} != {mode!r}")
    status = doc.get("status")
    if mode == "rehearsal" and status != "rehearsal":
        bad.append(f"rehearsal must report status=rehearsal, got {status!r}")
    if mode == "upload" and status not in ("success", "partial"):
        bad.append(f"status {status!r} is not success|partial")
    if mode == "upload" and expected_tag and doc.get("tag") != expected_tag:
        bad.append(f"tag {doc.get('tag')!r} != {expected_tag!r}")
    return bad


# ── rcm 잡 JSON → 종료코드 ────────────────────────────────────────────────────
def map_exit(job: Any) -> tuple[int, str]:
    """rcm wait 의 마지막 JSON 줄을 드라이버 종료코드로. 모르면 3 — 0 이나 1 로 접지 않는다."""
    if not isinstance(job, dict) or not job.get("state"):
        return EXIT_UNKNOWN, "no rcm job JSON (result unknown)"
    state, code = job.get("state"), job.get("exit_code")
    step = job.get("failed_step") or "-"
    if state == "succeeded":
        return EXIT_DONE, "succeeded"
    if state == "failed":
        if code == EXIT_DRIFT:
            return EXIT_DRIFT, f"store drift (job exit 4, step {step})"
        if code in (EXIT_PREREQ, 10):
            return EXIT_PREREQ, f"environment/blocked (job exit {code}, step {step})"
        return EXIT_RED, f"failed (job exit {code}, step {step})"
    if state in ("lost", "timed_out"):
        return EXIT_UNKNOWN, f"result unknown ({state})"
    if state == "cancelled":
        return EXIT_PREREQ, "cancelled"
    return EXIT_UNKNOWN, f"not finished ({state})"


# ── CLI ──────────────────────────────────────────────────────────────────────
def _parse_inputs(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def cmd_stage(a: argparse.Namespace) -> int:
    stage = decide_stage(
        prs=_load(a.prs, []),
        statuses=_load(a.statuses, {}),
        jobs=_load(a.jobs, []),
        tags=_lines(a.tags),
        sha=a.sha,
        build_name=a.build_name,
        build_number=a.build_number or None,
        tag_pattern=a.tag_pattern,
        upload_preset=a.upload_preset,
        gate_context=a.gate_context or None,
        qa_context=a.qa_context or None,
        skip_qa=a.skip_qa,
    )
    print(stage)
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    doc = _load(a.file, None)
    if a.kind == "plan":
        bad = validate_plan(doc, a.build_name)
    else:
        tag = render_tag(a.tag_pattern, a.build_name, a.build_number) if a.build_number else None
        bad = validate_upload(doc, a.build_number or None, a.mode, tag)
    for line in bad:
        print(f"{a.kind}.json: {line}")
    return EXIT_RED if bad else EXIT_DONE


def cmd_exitcode(a: argparse.Namespace) -> int:
    code, reason = map_exit(_load(a.rcm_json, None))
    print(f"{code} {reason}")
    return 0


def cmd_find_job(a: argparse.Namespace) -> int:
    states = {s for s in (a.states or "").split(",") if s} or None
    row = find_job(_load(a.jobs, []), a.preset, a.sha, states, _parse_inputs(a.inputs))
    if row is not None:
        print(f"{row.get('id', row.get('job_id'))} {row.get('state')}")
    return 0


def cmd_confirm(a: argparse.Namespace) -> int:
    """사람이 친 N 과 plan.json 의 n 의 대조 — 되돌릴 수 없는 호출은 전부 이 0 뒤에서만."""
    if not re.fullmatch(r"[1-9][0-9]*", a.typed or ""):
        print(f"typed build number is not a positive integer: {a.typed!r}")
        return EXIT_PREREQ
    doc = _load(a.plan, None)
    n = doc.get("n") if isinstance(doc, dict) else None
    if n is None:
        print("plan.json has no n — nothing to confirm against")
        return EXIT_RED
    if str(n) != a.typed:
        print(f"typed {a.typed} != plan n {n}")
        return EXIT_RED
    return EXIT_DONE


SHELL_SAFE = re.compile(r"^[A-Za-z0-9_.+/{}\-]*$")
PROFILE_KEYS = (
    ("DEFAULT_BRANCH", ("default_branch",), "main"),
    ("TAG_PATTERN", ("tag",), "prod/{version}-{build}"),
    ("DRIVER", ("driver",), ""),
    ("PRESET_PLAN", ("presets", "plan"), ""),
    ("PRESET_UPLOAD", ("presets", "upload"), ""),
    ("PRESET_REVIEW", ("presets", "review"), ""),
    ("PRESET_GATE", ("presets", "gate"), ""),
    ("PRESET_QA", ("presets", "qa"), ""),
)


def read_profile(
    doc: Any, repo: str | None, preset_names: set[str] | None
) -> tuple[dict[str, str], list[str]]:
    """프로파일 조각(`[repos.<name>.release]`)에서 드라이버 상수를. 값은 셸에 안전한 글자만.

    preset_names 를 주면 참조하는 프리셋이 전부 거기 있어야 한다(빈 역할은 «미설정» 이라 안 본다).
    """
    bad: list[str] = []
    repos = doc.get("repos") if isinstance(doc, dict) else None
    if not isinstance(repos, dict) or not repos:
        return {}, ["no [repos.<name>.release] table in the fragment"]
    names = [
        n for n, r in repos.items() if isinstance(r, dict) and isinstance(r.get("release"), dict)
    ]
    if repo:
        names = [n for n in names if n == repo]
    if len(names) != 1:
        return {}, [f"expected exactly one [repos.<name>.release], found {names or 'none'}"]
    name = names[0]
    rel = repos[name]["release"]
    out = {"REPO_NAME": name}
    for key, path, default in PROFILE_KEYS:
        cur: Any = rel
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        val = default if cur is None else str(cur)
        if not SHELL_SAFE.match(val):
            bad.append(f"{'.'.join(path)} has characters the driver will not eval: {val!r}")
            val = ""
        out[key] = val
    for role in ("PRESET_PLAN", "PRESET_UPLOAD"):
        if not out.get(role):
            bad.append(f"presets.{role[7:].lower()} is required")
    if preset_names is not None:
        for key, _path, _d in PROFILE_KEYS:
            if key.startswith("PRESET_") and out.get(key) and out[key] not in preset_names:
                bad.append(f"{key.lower()} names preset {out[key]!r} not in the presets file")
    return out, bad


def _toml(path: str) -> Any:
    import tomllib

    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError) as e:
        return {"__error__": str(e)}


def cmd_profile(a: argparse.Namespace) -> int:
    doc = _toml(a.file)
    if "__error__" in doc:
        print(f"cannot read {a.file}: {doc['__error__']}", file=sys.stderr)
        return EXIT_PREREQ
    names: set[str] | None = None
    if a.presets:
        pdoc = _toml(a.presets)
        if "__error__" in pdoc:
            print(f"cannot read {a.presets}: {pdoc['__error__']}", file=sys.stderr)
            return EXIT_PREREQ
        names = {p.get("name") for p in pdoc.get("presets", []) if isinstance(p, dict)}
    out, bad = read_profile(doc, a.repo or None, names)
    for line in bad:
        print(f"profile: {line}", file=sys.stderr)
    if bad:
        return EXIT_PREREQ
    for k, v in out.items():
        print(f"{k}={v}")
    return EXIT_DONE


def cmd_get(a: argparse.Namespace) -> int:
    doc = _load(a.file, {})
    val = doc.get(a.key) if isinstance(doc, dict) else None
    print("" if val is None else val)
    return 0


# ── selftest — 오염 입력이 실제로 빨개지는지 ─────────────────────────────────
def selftest() -> int:
    fails: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        mark = "ok " if got == want else "BAD"
        print(f"  {mark} {name}: {got!r}")
        if got != want:
            fails.append(name)

    sha, other = "a" * 40, "b" * 40
    pat, tags_done = "prod/{version}-{build}", ["prod/1.0.1-181"]

    def st(**ctx: str) -> dict[str, Any]:
        return {"statuses": [{"context": k, "state": v} for k, v in ctx.items()]}

    def job(preset: str, state: str, jid: int = 1, sha_: str = sha, **inputs: str) -> dict:
        return {
            "id": jid,
            "preset": preset,
            "state": state,
            "source": {"sha": sha_},
            "inputs": inputs,
        }

    def stage(**kw: Any) -> str:
        base: dict[str, Any] = dict(
            prs=[],
            statuses={},
            jobs=[],
            tags=[],
            sha=sha,
            build_name="1.0.1",
            build_number="181",
            tag_pattern=pat,
            upload_preset="release-upload",
            gate_context="ci/gate",
            qa_context="ci/qa",
            skip_qa=False,
        )
        base.update(kw)
        return decide_stage(**base)

    pr_open = [{"state": "OPEN", "headRefOid": sha}]
    pr_merged = [{"state": "MERGED", "headRefOid": sha}]
    up_ok = [job("release-upload", "succeeded", mode="upload", confirm_build_number="181")]

    print("stage from remote truth")
    check("no PR → S1", stage(), "S1")
    check(
        "PR whose head sha differs is ignored",
        stage(prs=[{"state": "MERGED", "headRefOid": other}]),
        "S1",
    )
    check("PR without headRefOid is ignored", stage(prs=[{"state": "MERGED"}]), "S1")
    check("open PR, nothing green → S4", stage(prs=pr_open), "S4")
    check("gate green only → S5", stage(prs=pr_open, statuses=st(**{"ci/gate": "success"})), "S5")
    check("qa green only is NOT S6", stage(prs=pr_open, statuses=st(**{"ci/qa": "success"})), "S4")
    check(
        "gate+qa green → S6",
        stage(prs=pr_open, statuses=st(**{"ci/gate": "success", "ci/qa": "success"})),
        "S6",
    )
    check(
        "gate green + --skip-qa → S6",
        stage(prs=pr_open, statuses=st(**{"ci/gate": "success"}), skip_qa=True),
        "S6",
    )
    check(
        "gate green + no qa role → S6",
        stage(prs=pr_open, statuses=st(**{"ci/gate": "success"}), qa_context=None),
        "S6",
    )
    check(
        "no gate role and no qa role → S6 (nothing to wait for)",
        stage(prs=pr_open, gate_context=None, qa_context=None),
        "S6",
    )
    check(
        "closed PR → BLOCKED",
        stage(prs=[{"state": "CLOSED", "headRefOid": sha}]),
        "BLOCKED_PR_CLOSED",
    )
    check("merged, no upload → S7", stage(prs=pr_merged), "S7")
    check("merged + upload succeeded → S8", stage(prs=pr_merged, jobs=up_ok), "S8")
    check(
        "rehearsal success is not an upload",
        stage(
            prs=pr_merged,
            jobs=[job("release-upload", "succeeded", mode="rehearsal", confirm_build_number="181")],
        ),
        "S7",
    )
    check(
        "upload with another N is not this round",
        stage(
            prs=pr_merged,
            jobs=[job("release-upload", "succeeded", mode="upload", confirm_build_number="180")],
        ),
        "S7",
    )
    check(
        "upload job without inputs is not trusted",
        stage(
            prs=pr_merged,
            jobs=[
                {"id": 1, "preset": "release-upload", "state": "succeeded", "source": {"sha": sha}}
            ],
        ),
        "S7",
    )
    check("exact tag → DONE", stage(tags=tags_done), "DONE")
    check("another build's tag is not DONE", stage(tags=["prod/1.0.1-180"]), "S1")
    check("tag without known N is not DONE", stage(tags=tags_done, build_number=None), "S1")

    print("find-job")
    jobs = [
        job("gate", "failed", 3),
        job("gate", "succeeded", 5),
        job("gate", "succeeded", 9, other),
    ]
    check("newest job of this sha", (find_job(jobs, "gate", sha) or {}).get("id"), 5)
    check("other sha never matches", find_job(jobs, "gate", other, {"failed"}), None)
    check("state filter", (find_job(jobs, "gate", sha, {"failed"}) or {}).get("id"), 3)

    print("validate plan")
    plan = {"schema": 1, "build_name": "1.0.1", "n": 181, "blockers": []}
    check("good plan", validate_plan(plan, "1.0.1"), [])
    check("wrong build_name", bool(validate_plan(plan, "1.0.2")), True)
    check("n null without blockers", bool(validate_plan({**plan, "n": None}, "1.0.1")), True)
    check(
        "n null with blockers is a legal blocked plan",
        validate_plan({**plan, "n": None, "blockers": [{"code": "B", "text": "x"}]}, "1.0.1"),
        [],
    )
    check("n as string", bool(validate_plan({**plan, "n": "181"}, "1.0.1")), True)
    check("n as bool", bool(validate_plan({**plan, "n": True}, "1.0.1")), True)

    print("validate upload")
    up = {"schema": 1, "n": 181, "status": "success", "mode": "upload", "tag": "prod/1.0.1-181"}
    check("good upload", validate_upload(up, "181", "upload", "prod/1.0.1-181"), [])
    check("n mismatch", bool(validate_upload(up, "180", "upload", "prod/1.0.1-181")), True)
    check("wrong tag", bool(validate_upload(up, "181", "upload", "prod/1.0.1-180")), True)
    check(
        "rehearsal claiming success",
        bool(validate_upload({**up, "mode": "rehearsal"}, "181", "rehearsal", None)),
        True,
    )
    check(
        "upload claiming rehearsal",
        bool(validate_upload({**up, "status": "rehearsal"}, "181", "upload", "prod/1.0.1-181")),
        True,
    )
    check("mode drift", bool(validate_upload(up, "181", "rehearsal", None)), True)

    print("exit code from rcm JSON")
    check("succeeded → 0", map_exit({"state": "succeeded"})[0], 0)
    check("failed exit 1 → 1", map_exit({"state": "failed", "exit_code": 1})[0], 1)
    check("failed exit 2 → 2", map_exit({"state": "failed", "exit_code": 2})[0], 2)
    check("failed exit 10 → 2 (blocked env)", map_exit({"state": "failed", "exit_code": 10})[0], 2)
    check("failed exit 4 → 4 (drift)", map_exit({"state": "failed", "exit_code": 4})[0], 4)
    check("lost → 3", map_exit({"state": "lost"})[0], 3)
    check("timed_out → 3", map_exit({"state": "timed_out"})[0], 3)
    check("empty JSON → 3", map_exit({})[0], 3)
    check("running → 3", map_exit({"state": "running"})[0], 3)

    print("typed N")
    import os
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(plan, fh)
    try:
        check("typed == plan n", main(["confirm", "--typed", "181", "--plan", fh.name]), 0)
        check("typed != plan n", main(["confirm", "--typed", "180", "--plan", fh.name]), 1)
        check("typed empty", main(["confirm", "--typed", "", "--plan", fh.name]), 2)
        check("typed garbage", main(["confirm", "--typed", "18a", "--plan", fh.name]), 2)
    finally:
        os.unlink(fh.name)

    print("profile fragment")
    presets = {"plan": "release-plan", "upload": "release-upload", "review": "release-review"}
    frag = {
        "repos": {
            "app": {
                "release": {
                    "tag": "v{version}+{build}",
                    "presets": {**presets, "gate": "gate-smoke"},
                }
            }
        }
    }
    out, bad = read_profile(frag, None, None)
    check(
        "reads name/tag/presets",
        (
            out.get("REPO_NAME"),
            out.get("TAG_PATTERN"),
            out.get("PRESET_GATE"),
            out.get("PRESET_QA"),
            out.get("DEFAULT_BRANCH"),
        ),
        ("app", "v{version}+{build}", "gate-smoke", "", "main"),
    )
    check("no errors on a good fragment", bad, [])
    check("wrong --repo", bool(read_profile(frag, "other", None)[1]), True)
    check(
        "preset missing from the presets file",
        bool(read_profile(frag, None, set(presets.values()))[1]),
        True,
    )
    check("all presets present", read_profile(frag, None, {*presets.values(), "gate-smoke"})[1], [])
    check(
        "missing upload role",
        bool(
            read_profile({"repos": {"app": {"release": {"presets": {"plan": "p"}}}}}, None, None)[1]
        ),
        True,
    )
    check(
        "shell-unsafe value refused",
        bool(
            read_profile(
                {
                    "repos": {
                        "app": {
                            "release": {"tag": "x$(rm)", "presets": {"plan": "p", "upload": "u"}}
                        }
                    }
                },
                None,
                None,
            )[1]
        ),
        True,
    )
    check(
        "two release tables without --repo",
        bool(read_profile({"repos": {"a": {"release": {}}, "b": {"release": {}}}}, None, None)[1]),
        True,
    )

    print("tag pattern")
    check("render", render_tag(pat, "1.0.1", 181), "prod/1.0.1-181")

    print(f"\nselftest: {'FAIL ' + str(len(fails)) if fails else 'all green'}")
    return EXIT_RED if fails else EXIT_DONE


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stage")
    for name in (
        "prs",
        "statuses",
        "jobs",
        "tags",
        "sha",
        "build-name",
        "upload-preset",
    ):
        s.add_argument(f"--{name}", required=True)
    s.add_argument("--gate-context", default="")
    s.add_argument("--build-number", default="")
    s.add_argument("--tag-pattern", default="prod/{version}-{build}")
    s.add_argument("--qa-context", default="")
    s.add_argument("--skip-qa", action="store_true")
    s.set_defaults(fn=cmd_stage)

    v = sub.add_parser("validate")
    v.add_argument("kind", choices=["plan", "upload"])
    v.add_argument("file")
    v.add_argument("--build-name", required=True)
    v.add_argument("--build-number", default="")
    v.add_argument("--mode", default="upload", choices=["rehearsal", "upload"])
    v.add_argument("--tag-pattern", default="prod/{version}-{build}")
    v.set_defaults(fn=cmd_validate)

    e = sub.add_parser("exitcode")
    e.add_argument("--rcm-json", required=True)
    e.set_defaults(fn=cmd_exitcode)

    f = sub.add_parser("find-job")
    f.add_argument("--jobs", required=True)
    f.add_argument("--preset", required=True)
    f.add_argument("--sha", required=True)
    f.add_argument("--states", default="")
    f.add_argument("--inputs", default="")
    f.set_defaults(fn=cmd_find_job)

    c = sub.add_parser("confirm")
    c.add_argument("--typed", required=True)
    c.add_argument("--plan", required=True)
    c.set_defaults(fn=cmd_confirm)

    pr = sub.add_parser("profile")
    pr.add_argument("--file", required=True)
    pr.add_argument("--repo", default="")
    pr.add_argument("--presets", default="")
    pr.set_defaults(fn=cmd_profile)

    g = sub.add_parser("get")
    g.add_argument("--file", required=True)
    g.add_argument("--key", required=True)
    g.set_defaults(fn=cmd_get)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
