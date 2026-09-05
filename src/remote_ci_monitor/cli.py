"""`rcm` CLI — run · wait · cancel · pause · resume · serve · check · token · version.

stdout 에는 JSON 한 줄(run·wait), stderr 에는 사람용 진행 표시. 종료 코드:
  run/wait: succeeded 0 · failed 1 · cancelled/timed_out 2 · lost/조회 실패/--timeout 3
  사용 오류·설정 오류·검증 실패(서버에 보내기 전): 2
Ctrl-C 는 detach — 잡은 계속 돌고 `rcm wait --job ID` / `rcm cancel ID` 를 안내한다(합류자면 자기
대기만 best-effort 로 뺀다). 도움말과 메시지는 영어(제품 규칙).
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor import SCHEMA_VERSION, __version__
from remote_ci_monitor.client import (
    Client,
    ClientError,
    default_label,
    make_snapshot,
    wait_for_job,
)
from remote_ci_monitor.config import (
    ConfigError,
    load_client_config,
    load_server_config,
    user_config_dir,
)
from remote_ci_monitor.core.gitref import validate_ref
from remote_ci_monitor.core.inputs import InputError, parse_kv, validate_inputs
from remote_ci_monitor.core.model import EXIT_UNKNOWN, TERMINAL_STATES, Preset
from remote_ci_monitor.core.render_text import fmt_clock, fmt_duration

USAGE_EXIT = 2


def _err(msg: str) -> None:
    print(f"rcm: {msg}", file=sys.stderr, flush=True)


def _info(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class _StatusLine:
    """TTY 면 한 줄을 덮어쓰고, 아니면 바뀔 때만 새 줄을 찍는다."""

    def __init__(self, stream=sys.stderr):
        self.stream = stream
        self.tty = hasattr(stream, "isatty") and stream.isatty()
        self.last = ""

    def update(self, text: str) -> None:
        if text == self.last:
            return
        self.last = text
        if self.tty:
            self.stream.write("\r\x1b[2K" + text)
        else:
            self.stream.write(text + "\n")
        self.stream.flush()

    def done(self) -> None:
        if self.tty and self.last:
            self.stream.write("\n")
            self.stream.flush()


def describe(job: dict[str, Any]) -> str:
    """wait 진행 한 줄: 상태 · 순번/스텝 · 경과 · ETA."""
    state = job.get("state", "?")
    est = job.get("estimate") or {}
    parts = [f"#{job.get('id')} {state}"]
    if job.get("position"):
        parts.append(f"{_ordinal(job['position'])} in line")
        reason = job.get("reason")
        if reason and reason not in ("waiting_for_lane", "uploading"):
            parts.append(reason.replace("_", " "))
        if est.get("wait_seconds") is not None:
            parts.append(f"wait {fmt_duration(est['wait_seconds'])}")
    prog = job.get("progress")
    if prog and prog.get("phase") == "executing" and prog.get("steps"):
        total = prog.get("steps_total")
        head = f"step {prog.get('current_index') or prog.get('steps_done')}/{total or '?'}"
        if prog.get("steps_total_partial"):
            head += "+"
        if prog.get("current_name"):
            head += f" {prog['current_name']}"
        parts.append(head)
    elif prog and prog.get("phase") == "materializing":
        parts.append("preparing workspace")
    if est.get("elapsed_seconds") is not None:
        parts.append(f"elapsed {fmt_duration(est['elapsed_seconds'])}")
    if est.get("finish_at"):
        parts.append(f"eta {fmt_clock(est['finish_at'], datetime.now().astimezone().tzinfo)}")
    elif est.get("overdue"):
        parts.append("overdue")
    if job.get("summary") and state in TERMINAL_STATES:
        parts.append(str(job["summary"]))
    return " · ".join(parts)


NO_SERVER_HINT = "no server configured (use --server, RCM_SERVER or client.toml)"


def _no_token_hint(cfg) -> str:
    # client.toml 이 token_env 를 바꿨으면 그 이름을 말해 준다(RCM_TOKEN 만 말하면 헤맨다)
    return f"no token (use --token, {cfg.token_env} or client.toml token)"


def _client_config(args: argparse.Namespace):
    return load_client_config(
        getattr(args, "client_config", None),
        server=getattr(args, "server", None),
        token=getattr(args, "token", None),
    )


def _client(args: argparse.Namespace, *, need_token: bool = True) -> Client:
    try:
        cfg = _client_config(args)
    except ConfigError as e:
        raise SystemExit(_usage(str(e))) from e
    if not cfg.server:
        raise SystemExit(_usage(NO_SERVER_HINT))
    if need_token and not cfg.token:
        raise SystemExit(_usage(_no_token_hint(cfg)))
    return Client(cfg.server, cfg.token or None)


def _usage(msg: str) -> int:
    _err(msg)
    return USAGE_EXIT


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), flush=True)


# ── run ──────────────────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        inputs_raw = parse_kv(args.f or [])
    except InputError as e:
        return _usage(str(e))
    ref: str | None = None
    if args.ref is not None:
        # 서버에 보내기 전에 같은 규칙으로 거른다(옵션처럼 보이는 값은 여기서 끝난다)
        try:
            ref = validate_ref(args.ref)
        except ValueError as e:
            return _usage(f"--ref: {e}")
    # ① 서버 스키마로 검증 — 실패면 보내지 않는다
    try:
        presets = client.presets()
    except ClientError as e:
        return _usage(f"cannot read presets: {e.message}")
    preset = presets.get(args.preset)
    if preset is None:
        names = ", ".join(sorted(presets)) or "(none)"
        return _usage(f"unknown preset '{args.preset}' — server has: {names}")
    # 소스 모드: --source 명시 > --ref 가 있으면 git_ref > 프리셋이 git_ref 만 받으면 git_ref > tree
    mode = args.source
    if mode is None:
        if ref is not None or tuple(preset.source_modes) == ("git_ref",):
            mode = "git_ref"
        else:
            mode = "tree"
    if mode not in preset.source_modes:
        allowed = ", ".join(preset.source_modes)
        return _usage(f"preset '{preset.name}' accepts source modes: {allowed}")
    if mode == "git_ref" and ref is None:
        return _usage(f"preset '{preset.name}' needs --ref <branch|tag|sha>")
    if mode == "tree" and ref is not None:
        return _usage(f"--ref only applies to git_ref presets; '{preset.name}' takes a tree")
    try:
        inputs = validate_inputs(preset, inputs_raw)
    except InputError as e:
        return _usage(str(e))
    label = args.by or default_label(None)
    if mode == "git_ref":
        return _run_git_ref(client, args, preset, inputs, ref or "", label)
    # ② 스냅샷
    root = Path(args.dir or os.getcwd())
    try:
        snap = make_snapshot(root, excludes=args.exclude or [], progress=_info)
    except OSError as e:
        return _usage(f"snapshot failed: {e.strerror or e}")
    job_id: int | None = None
    joined = False
    try:
        source = {
            "mode": "tree",
            "repo": snap.repo,
            "base_sha": snap.base_sha,
            "dirty": snap.dirty,
            "tree_hash": snap.tree_hash,
            "bytes": snap.bytes,
        }
        # ③ 제출 (합류면 업로드 생략)
        try:
            resp = client.submit(
                preset.name, inputs, source, requester_label=label, join=not args.no_join
            )
        except ClientError as e:
            _err(f"submit failed: {e.message}")
            return USAGE_EXIT if e.status in (400, 401, 403, 413, 0) else EXIT_UNKNOWN
        job_id = int(resp["job_id"])
        joined = bool(resp.get("joined"))
        if joined:
            _info(f"joined job #{job_id} ({resp.get('state')}) — same preset, inputs and tree")
        else:
            # ④ 업로드
            line = _StatusLine()

            def progress(sent: int, total: int) -> None:
                pct = 100 * sent // total if total else 100
                line.update(
                    f"uploading #{job_id}: {sent / 1e6:.1f} / {total / 1e6:.1f} MB ({pct}%)"
                )

            try:
                client.upload(job_id, snap.tar_path, progress=progress)
            except ClientError as e:
                line.done()
                _err(f"upload failed: {e.message}")
                return EXIT_UNKNOWN
            line.done()
            _info(f"submitted job #{job_id} · {resp.get('url', '')}")
    finally:
        try:
            snap.tar_path.unlink()
        except OSError:
            pass
    if args.no_wait:
        _print_json(
            {"job_id": job_id, "joined": joined, "state": "submitted", "url": resp.get("url")}
        )
        return 0
    # ⑤ wait
    return _wait(client, job_id, timeout=args.timeout, joined=joined, use_sse=not args.poll)


def _run_git_ref(
    client: Client,
    args: argparse.Namespace,
    preset: Preset,
    inputs: dict[str, Any],
    ref: str,
    label: str,
) -> int:
    """git_ref 제출: 스냅샷·업로드 없이 ③ 제출 → ⑤ wait. 서버가 ref 를 sha 로 확정한다."""
    try:
        resp = client.submit(
            preset.name,
            inputs,
            {"mode": "git_ref", "ref": ref},
            requester_label=label,
            join=not args.no_join,
        )
    except ClientError as e:
        _err(f"submit failed: {e.message}")
        # 502 「cannot resolve」 는 ref 가 틀렸다는 확정 거절(잡 없음) — 400 과 같은 usage 2.
        # 504 (해석 타임아웃) · 503 · 연결 오류는 결과를 모르는 것이라 3.
        return USAGE_EXIT if e.status in (400, 401, 403, 413, 502, 0) else EXIT_UNKNOWN
    job_id = int(resp["job_id"])
    joined = bool(resp.get("joined"))
    sha = resp.get("sha")
    short = str(sha)[:7] if sha else "—"
    if joined:
        _info(f"joined job #{job_id} ({resp.get('state')}) — same preset, inputs, commit {short}")
    else:
        _info(f"submitted job #{job_id} ({preset.name} · {ref} @{short}) · {resp.get('url', '')}")
    if args.no_wait:
        _print_json(
            {
                "job_id": job_id,
                "joined": joined,
                "state": resp.get("state") or "submitted",
                "ref": ref,
                "sha": sha,
                "url": resp.get("url"),
            }
        )
        return 0
    return _wait(client, job_id, timeout=args.timeout, joined=joined, use_sse=not args.poll)


def _wait(
    client: Client, job_id: int, *, timeout: float | None, joined: bool, use_sse: bool = True
) -> int:
    line = _StatusLine()
    last: dict[str, Any] | None = None

    def on_update(job: dict[str, Any]) -> None:
        nonlocal last
        last = job
        line.update(describe(job))

    try:
        code, job, reason = wait_for_job(
            client, job_id, timeout=timeout, on_update=on_update, use_sse=use_sse
        )
    except KeyboardInterrupt:
        line.done()
        left = False
        if joined:
            try:
                resp = client.cancel(job_id)
                left = bool(resp.get("left"))
            except ClientError:
                pass
        _info(
            f"detached from job #{job_id} — it keeps running. "
            f"Resume with `rcm wait --job {job_id}`; stop it with `rcm cancel {job_id}`."
            + (" (left the join list)" if left else "")
        )
        _print_json({**(last or {"job_id": job_id}), "detached": True, "left": left})
        return EXIT_UNKNOWN
    line.done()
    if reason:
        _err(reason)
    out = dict(job or {"job_id": job_id, "state": None})
    out["wait_exit_code"] = code
    if joined:
        out["joined"] = True
    _print_json(out)
    return code


def cmd_wait(args: argparse.Namespace) -> int:
    client = _client(args, need_token=False)
    return _wait(client, args.job, timeout=args.timeout, joined=False, use_sse=not args.poll)


def cmd_cancel(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        resp = client.cancel(args.job)
    except ClientError as e:
        _err(f"cancel failed: {e.message}")
        return USAGE_EXIT if e.status else EXIT_UNKNOWN
    _print_json(resp)
    if resp.get("left"):
        _info(f"left the join list of job #{args.job} (job keeps running)")
    else:
        _info(f"job #{args.job} is now {resp.get('state')}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        resp = client.pause() if args.command == "pause" else client.resume()
    except ClientError as e:
        _err(f"{args.command} failed: {e.message}")
        return USAGE_EXIT if e.status else EXIT_UNKNOWN
    _print_json(resp)
    return 0


# ── eta · top · jobs · logs · presets (M1) ───────────────────────────────────


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _fmt_eta_row(row: dict[str, Any], ahead: int | None) -> str:
    """`rcm eta` 한 줄. 모르는 값은 —, 시작할 수 없으면 이유를 붙인다.

    이미 도는 잡은 「0 ahead · wait 0s」(곧 시작할 것처럼 읽힌다) 대신 상태와 경과를 보인다.
    """
    est = row.get("estimate") or {}
    parts: list[str] = []
    if row.get("id"):
        parts.append(f"#{row['id']}")
    if row.get("position") is None and row.get("state") in ("running", "cancelling"):
        parts.append(str(row["state"]))
        parts.append(f"elapsed {fmt_duration(est.get('elapsed_seconds'))}")
    else:
        if row.get("position"):
            parts.append(f"{_ordinal(row['position'])} in line")
        if ahead is not None:
            parts.append(f"{ahead} ahead")
        parts.append(f"wait {fmt_duration(est.get('wait_seconds'))}")
    parts.append(f"expected {fmt_duration(est.get('expected_seconds'))}")
    if est.get("finish_at"):
        parts.append(f"eta {fmt_clock(est['finish_at'], _local_tz())}")
    else:
        parts.append("eta —")
        reason = row.get("reason")
        if reason in ("paused", "worker_down", "overdue", "stuck"):
            parts.append(reason.replace("_", " "))
    conf = est.get("confidence")
    source = est.get("source")
    n = est.get("sample_count")
    tail = f"{conf} · {source}" if conf else f"{source}"
    if source == "measured" and n:
        tail += f" n={n}"
    parts.append(tail)
    return " · ".join(parts)


def cmd_eta(args: argparse.Namespace) -> int:
    client = _client(args, need_token=False)
    try:
        if args.job is not None:
            doc = client.status()
            pool = (doc.get("pools") or [{}])[0]
            queue = pool.get("queue")
            if queue is None:
                return _usage(f"queue unavailable: {pool.get('queue_error') or 'unknown'}")
            row = next((r for r in queue if r.get("id") == args.job), None)
            if row is None:
                job = client.job(args.job)
                _print_json(job) if args.json else print(
                    f"#{args.job} {job.get('state')} · finished "
                    f"{fmt_clock(job.get('finished_at'), _local_tz())} · {job.get('summary') or ''}"
                )
                return 0
            busy_others = sum(
                1 for r in queue if r.get("id") != args.job and r.get("position") is None
            )
            ahead = busy_others + (row["position"] - 1) if row.get("position") else 0
            if args.json:
                _print_json(row)
            else:
                print(_fmt_eta_row(row, ahead))
            return 0
        if not args.preset:
            return _usage("give a PRESET or --job ID")
        try:
            inputs = parse_kv(args.f or [])
        except InputError as e:
            return _usage(str(e))
        resp = client.eta(args.preset, inputs)
    except ClientError as e:
        return _usage(f"eta failed: {e.message}") if e.status else EXIT_UNKNOWN
    if args.json:
        _print_json(resp)
    else:
        print(_fmt_eta_row(resp["job"], resp.get("ahead")))
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    from remote_ci_monitor.core.render_text import render

    client = _client(args, need_token=False)
    try:
        while True:
            try:
                doc = client.status()
            except ClientError as e:
                if args.json:
                    _print_json({"error": e.message, "server": client.server})
                    return EXIT_UNKNOWN
                text = f"━━━ rcm · {client.server} · unreachable: {e.message}\n"
                doc = None
            else:
                text = render(doc, tz=_local_tz())
            if args.json:
                _print_json(doc)
                return 0
            if args.watch:
                sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(text)
            sys.stdout.flush()
            if not args.watch:
                return 0 if doc is not None else EXIT_UNKNOWN
            time.sleep(max(1.0, float(args.watch)))
    except KeyboardInterrupt:
        return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    client = _client(args, need_token=bool(args.mine))
    me: str | None = None
    try:
        if args.mine:
            me = client.whoami()["name"]
        doc = client.status()
    except ClientError as e:
        return _usage(f"jobs failed: {e.message}") if e.status else EXIT_UNKNOWN
    pool = (doc.get("pools") or [{}])[0]
    rows: list[dict[str, Any]] = []
    if pool.get("queue") is None:
        print(f"queue unavailable: {pool.get('queue_error') or 'unknown'}", file=sys.stderr)
    else:
        rows.extend(pool["queue"])
    if pool.get("recent") is None:
        print(f"recent unavailable: {pool.get('recent_error') or 'unknown'}", file=sys.stderr)
    else:
        rows.extend(pool["recent"])
    if me is not None:
        rows = [
            r
            for r in rows
            if (r.get("requester") or {}).get("name") == me
            or any(j.get("name") == me for j in r.get("joiners") or [])
        ]
    if args.state:
        rows = [r for r in rows if r.get("state") == args.state]
    if args.json:
        _print_json(rows)
        return 0
    tz = _local_tz()
    if not rows:
        print("no jobs")
        return 0
    for r in rows:
        est = r.get("estimate") or {}
        state = r.get("state", "?")
        if state in ("running", "cancelling"):
            timing = f"elapsed {fmt_duration(est.get('elapsed_seconds'))}"
        elif state in ("queued", "uploading"):
            timing = f"waiting {fmt_duration(est.get('waited_seconds'))}"
        else:
            timing = f"took {fmt_duration(r.get('job_seconds'))}"
        when = est.get("finish_at") or r.get("finished_at")
        pos = f"{_ordinal(r['position'])} in line · " if r.get("position") else ""
        label = (r.get("requester") or {}).get("label") or "?"
        summary = r.get("summary") or ""
        print(
            f"#{r.get('id')}  {state:<10} {r.get('key', '?'):<16} {label:<20} {pos}{timing:<16} "
            f"{fmt_clock(when, tz)}  {summary}".rstrip()
        )
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        if args.follow:
            for chunk in client.log_follow(args.job):
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        else:
            data, _, _ = client.log(args.job, 0)
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except ClientError as e:
        return _usage(f"logs failed: {e.message}") if e.status else EXIT_UNKNOWN
    except KeyboardInterrupt:
        return 130
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    client = _client(args, need_token=False)
    try:
        doc = client.status()
    except ClientError as e:
        return _usage(f"presets failed: {e.message}") if e.status else EXIT_UNKNOWN
    presets = doc.get("presets") or []
    if args.json:
        _print_json(presets)
        return 0
    if not presets:
        print("no presets configured on the server")
        return 0
    for p in presets:
        extra = []
        if p.get("expected_seconds"):
            extra.append(f"expected {fmt_duration(p['expected_seconds'])}")
        if p.get("timeout_seconds"):
            extra.append(f"timeout {fmt_duration(p['timeout_seconds'])}")
        if p.get("concurrency_group"):
            extra.append(f"group {p['concurrency_group']}")
        extra.append("modes " + ",".join(p.get("source_modes") or []))
        if p.get("repo"):
            extra.append(f"repo {p['repo']}")
        print(f"{p['name']:<16} {p.get('description') or ''}  [{' · '.join(extra)}]")
        for i in p.get("inputs") or []:
            detail = i.get("type", "string")
            if i.get("choices"):
                detail += " " + "|".join(i["choices"])
            if i.get("pattern"):
                detail += f" /{i['pattern']}/"
            default = "" if i.get("default") is None else f" (default {i['default']})"
            print(f"    -f {i['name']}=<{detail}>{default}")
    return 0


# ── serve · check · token ────────────────────────────────────────────────────


def _server_config(args: argparse.Namespace):
    overrides = {
        "server": {
            "bind": getattr(args, "bind", None),
            "port": getattr(args, "port", None),
            "data_dir": getattr(args, "data_dir", None),
        }
    }
    return load_server_config(getattr(args, "config", None), overrides=overrides)


def cmd_serve(args: argparse.Namespace) -> int:
    from remote_ci_monitor.server import serve

    try:
        cfg = _server_config(args)
    except ConfigError as e:
        return _usage(f"config: {e}")
    if not cfg.presets:
        _info("warning: no [[presets]] configured — the server will accept nothing to run")
    try:
        return serve(cfg, debug=args.debug)
    except OSError as e:
        return _usage(f"cannot start server: {e.strerror or e}")


# ── init · version ───────────────────────────────────────────────────────────

MIN_PYTHON = (3, 11, 4)  # tarfile data 필터


def read_template(name: str) -> bytes:
    """패키지 안 `templates/<name>` — `examples/` 와 바이트 단위로 같다(테스트가 잠근다)."""
    return (importlib.resources.files("remote_ci_monitor") / "templates" / name).read_bytes()


_SERVER_LINE_RE = re.compile(r'^server\s*=\s*"[^"]*"', re.M)


def client_template(server: str) -> bytes:
    """클라이언트 템플릿의 `server = "…"` 를 주어진 URL 로 바꾼다.

    정확히 한 줄이어야 한다 — 0개나 2개면 템플릿이 바뀐 것이니 조용히 기본 URL 을 남기지 않고 실패.
    """
    text = read_template("client.toml").decode("utf-8")
    if len(_SERVER_LINE_RE.findall(text)) != 1:
        raise RuntimeError("client template must have exactly one 'server = \"…\"' line")
    return _SERVER_LINE_RE.sub(f'server = "{server}"', text, count=1).encode("utf-8")


def write_private(path: Path, data: bytes, mode: int) -> None:
    """임시 파일을 올바른 권한으로 만들고 `os.replace` — 잘못된 권한의 순간이 없다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)  # umask 가 깎은 비트를 되돌린다
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def cmd_init(args: argparse.Namespace) -> int:
    kind = args.init_kind
    server = ""
    if kind == "client":
        server = (args.server or "").strip().rstrip("/")
        if not server.startswith(("http://", "https://")):
            return _usage("--server must be a URL starting with http:// or https://")
    path = Path(args.path).expanduser() if args.path else user_config_dir() / f"{kind}.toml"
    if path.exists() and not args.force:
        return _usage(f"refusing to overwrite {path} (use --force)")
    data = client_template(server) if kind == "client" else read_template("server.toml")
    mode = 0o600 if kind == "client" else 0o644  # 클라이언트 파일엔 토큰이 들어갈 수 있다
    try:
        write_private(path, data, mode)
    except OSError as e:
        return _usage(f"cannot write {path}: {e.strerror or e}")
    print(path, flush=True)
    if kind == "server":
        _info(
            "next: edit the [[presets]] in that file, then `rcm token add <client-name>` "
            "and `rcm serve`"
        )
    else:
        _info("next: `export RCM_TOKEN=<token from rcm token add>` then `rcm check`")
    return 0


def version_info() -> dict[str, Any]:
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "schema_version": SCHEMA_VERSION,
    }


def cmd_version(args: argparse.Namespace) -> int:
    info = version_info()
    if getattr(args, "json", False):
        _print_json(info)
    else:
        py, plat, mach = info["python"], info["platform"], info["machine"]
        print(f"rcm {info['version']} (Python {py}, {plat} {mach})")
    return 0


def python_row() -> tuple[str, bool, str]:
    """`rcm check` 첫 행 — tar 안전 추출에 필요한 3.11.4+ 인가."""
    ok = sys.version_info >= MIN_PYTHON and hasattr(tarfile, "data_filter")
    detail = platform.python_version() + (
        " · tarfile data filter" if ok else " — tarfile data filter needs Python 3.11.4+"
    )
    return ("python", ok, detail)


def cmd_check(args: argparse.Namespace) -> int:
    rows: list[tuple[str, bool, str]] = [python_row()]
    client = None
    cfg = None
    try:
        cfg = _client_config(args)
    except ConfigError as e:
        rows.append(("client config", False, str(e)))
    if cfg is not None:
        if cfg.server:
            client = Client(cfg.server, cfg.token or None)
        else:
            rows.append(("server", False, NO_SERVER_HINT))
    if client is not None and cfg is not None:
        try:
            h = client.health()
            rows.append(("server", bool(h.get("ok")), f"{client.server} · v{h.get('version')}"))
        except ClientError as e:
            rows.append(("server", False, e.message))
        if client.token:
            try:
                me = client.whoami()
                rows.append(
                    ("token", True, f"{me['name']}" + (" (admin)" if me.get("admin") else ""))
                )
            except ClientError as e:
                rows.append(("token", False, e.message))
        else:
            rows.append(("token", False, _no_token_hint(cfg)))
        try:
            doc = client.status()
            names = ", ".join(p["name"] for p in doc.get("presets", [])) or "(none)"
            rows.append(("presets", bool(doc.get("presets")), names))
            rows.append(("timezone", True, doc.get("display_timezone") or "server local"))
        except ClientError as e:
            rows.append(("presets", False, e.message))
    try:
        cfg = load_server_config(getattr(args, "config", None), check_tools=False)
        if cfg.path is not None:
            d = cfg.data_dir
            writable = os.access(d, os.W_OK) if d.exists() else os.access(d.parent, os.W_OK)
            rows.append(
                ("data dir", writable, f"{d} ({'writable' if writable else 'not writable'})")
            )
            if cfg.repos:
                git = shutil.which("git")
                rows.append(("git", git is not None, git or "not on PATH (git_ref presets)"))
    except ConfigError as e:
        rows.append(("server config", False, str(e)))
    ok_all = all(ok for _, ok, _ in rows)
    for name, ok, detail in rows:
        print(f"{'ok ' if ok else 'FAIL'}  {name:<13} {detail}")
    return 0 if ok_all else 1


def cmd_token(args: argparse.Namespace) -> int:
    from remote_ci_monitor.store import Store, StoreError

    try:
        cfg = _server_config(args)
    except ConfigError as e:
        return _usage(f"config: {e}")
    store = Store(cfg.data_dir / "rcm.sqlite3")
    now = datetime.now(UTC)
    try:
        if args.token_command == "add":
            secret = store.add_token(args.name, admin=args.admin, now=now)
            _info(f"token '{args.name}' created — shown once, store it as RCM_TOKEN on the client:")
            print(secret, flush=True)
            return 0
        if args.token_command == "list":
            for t in store.list_tokens():
                flag = "admin" if t.admin else "user "
                state = f"revoked {t.revoked_at:%Y-%m-%d}" if t.revoked_at else "active"
                print(f"{t.name:<24} {flag}  created {t.created_at:%Y-%m-%d}  {state}")
            return 0
        if args.token_command == "revoke":
            if store.revoke_token(args.name, now):
                _info(f"token '{args.name}' revoked")
                return 0
            return _usage(f"no active token named '{args.name}'")
    except StoreError as e:
        return _usage(str(e))
    finally:
        store.close()
    return USAGE_EXIT


# ── 파서 ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rcm",
        description="Local job server for one build machine: submit a preset, wait for the result.",
    )
    p.add_argument("--version", action="version", version=f"rcm {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def client_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--server", help="server URL (default: RCM_SERVER or client.toml)")
        sp.add_argument("--token", help="bearer token (default: RCM_TOKEN or client.toml)")
        sp.add_argument(
            "--client-config", help="client.toml path (default: ~/.config/rcm/client.toml)"
        )

    run = sub.add_parser("run", help="snapshot the working tree, submit a preset and wait")
    run.add_argument("preset")
    run.add_argument("-f", action="append", metavar="NAME=VALUE", help="preset input (repeatable)")
    run.add_argument(
        "--source",
        choices=["tree", "git_ref"],
        default=None,
        help="tree = upload the working tree (default); git_ref = the server fetches --ref",
    )
    run.add_argument(
        "--ref", metavar="REF", help="branch, tag or commit sha for git_ref presets (no upload)"
    )
    run.add_argument("--by", metavar="LABEL", help="requester label (default: user@host)")
    run.add_argument("--no-join", action="store_true", help="never join an identical active job")
    run.add_argument("--no-wait", action="store_true", help="submit and exit 0 without waiting")
    run.add_argument(
        "--exclude", action="append", metavar="PATTERN", help="extra .rcmignore pattern"
    )
    run.add_argument("--dir", help="directory to snapshot (default: current directory)")
    run.add_argument("--timeout", type=float, help="give up waiting after N seconds (exit 3)")
    run.add_argument(
        "--poll", action="store_true", help="poll every 2s instead of the event stream"
    )
    client_opts(run)
    run.set_defaults(func=cmd_run)

    wait = sub.add_parser("wait", help="wait for a job and exit with 0/1/2/3")
    wait.add_argument("--job", type=int, required=True)
    wait.add_argument("--timeout", type=float, help="give up waiting after N seconds (exit 3)")
    wait.add_argument(
        "--poll", action="store_true", help="poll every 2s instead of the event stream"
    )
    client_opts(wait)
    wait.set_defaults(func=cmd_wait)

    cancel = sub.add_parser("cancel", help="cancel a job (joiners only leave the join list)")
    cancel.add_argument("job", type=int)
    client_opts(cancel)
    cancel.set_defaults(func=cmd_cancel)

    for name, help_text in (
        ("pause", "pause the queue (admin)"),
        ("resume", "resume the queue (admin)"),
    ):
        sp = sub.add_parser(name, help=help_text)
        client_opts(sp)
        sp.set_defaults(func=cmd_pause)

    eta = sub.add_parser("eta", help="estimate wait and finish time for a job or a new submission")
    eta.add_argument("preset", nargs="?")
    eta.add_argument("-f", action="append", metavar="NAME=VALUE", help="preset input (repeatable)")
    eta.add_argument("--job", type=int, help="an existing job id")
    eta.add_argument("--json", action="store_true")
    client_opts(eta)
    eta.set_defaults(func=cmd_eta)

    top = sub.add_parser("top", help="one screen: queue, recent, medians, host")
    top.add_argument("--watch", type=float, metavar="N", help="refresh every N seconds")
    top.add_argument("--json", action="store_true", help="print /api/status as JSON")
    client_opts(top)
    top.set_defaults(func=cmd_top)

    jobs = sub.add_parser("jobs", help="list queued, running and recent jobs")
    jobs.add_argument("--mine", action="store_true", help="only jobs you requested or joined")
    jobs.add_argument("--state", help="filter by state (running, queued, failed, ...)")
    jobs.add_argument("--json", action="store_true")
    client_opts(jobs)
    jobs.set_defaults(func=cmd_jobs)

    logs = sub.add_parser("logs", help="print a job log (token required)")
    logs.add_argument("job", type=int)
    logs.add_argument("--follow", action="store_true", help="keep printing until the job ends")
    client_opts(logs)
    logs.set_defaults(func=cmd_logs)

    presets = sub.add_parser("presets", help="list presets and their inputs")
    presets.add_argument("--json", action="store_true")
    client_opts(presets)
    presets.set_defaults(func=cmd_presets)

    def server_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--config",
            help="server.toml (default: $RCM_CONFIG, ./rcm.toml, ~/.config/rcm/server.toml)",
        )
        sp.add_argument(
            "--data-dir", dest="data_dir", help="data directory (default: ~/.local/share/rcm)"
        )

    serve = sub.add_parser("serve", help="run the job server on the build machine")
    server_opts(serve)
    serve.add_argument("--bind", help="bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, help="port (default: 8787)")
    serve.add_argument("--debug", action="store_true", help="log every request and stack traces")
    serve.set_defaults(func=cmd_serve)

    check = sub.add_parser("check", help="verify server, token, presets, timezone and data dir")
    client_opts(check)
    check.add_argument("--config", help="server.toml to check the data dir of")
    check.set_defaults(func=cmd_check)

    token = sub.add_parser("token", help="manage client tokens (run on the build machine)")
    server_opts(token)
    tsub = token.add_subparsers(dest="token_command", required=True)
    add = tsub.add_parser("add", help="create a token and print it once")
    add.add_argument("name")
    add.add_argument(
        "--admin", action="store_true", help="admin token (cancel any job, pause/resume)"
    )
    tsub.add_parser("list", help="list tokens (never shows secrets)")
    revoke = tsub.add_parser("revoke", help="revoke a token")
    revoke.add_argument("name")
    token.set_defaults(func=cmd_token)

    init = sub.add_parser("init", help="write a starter config file (server or client)")
    isub = init.add_subparsers(dest="init_kind", required=True)
    iserver = isub.add_parser("server", help="~/.config/rcm/server.toml from the packaged example")
    iclient = isub.add_parser("client", help="~/.config/rcm/client.toml pointing at your server")
    iclient.add_argument("--server", required=True, metavar="URL", help="http://build-machine:8787")
    for sp in (iserver, iclient):
        sp.add_argument("--path", help="write here instead of the default location")
        sp.add_argument("--force", action="store_true", help="overwrite an existing file")
        sp.set_defaults(func=cmd_init)

    version = sub.add_parser("version", help="print the version (and Python/OS with --json)")
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=cmd_version)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except SystemExit as e:  # _usage 가 SystemExit(2) 로 나온다
        return int(e.code or 0)
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    except ClientError as e:
        _err(e.message)
        return EXIT_UNKNOWN


if __name__ == "__main__":
    sys.exit(main())
