"""`rcm` CLI — run · wait · cancel · pause · resume · serve · check · token · version.

stdout 에는 JSON 한 줄(run·wait), stderr 에는 사람용 진행 표시. 종료 코드:
  run/wait: succeeded 0 · failed 1 · cancelled/timed_out 2 · lost/조회 실패/--timeout 3
  사용 오류·설정 오류·검증 실패(서버에 보내기 전): 2
Ctrl-C 는 detach — 잡은 계속 돌고 `rcm wait --job ID` / `rcm cancel ID` 를 안내한다(합류자면 자기
대기만 best-effort 로 뺀다). 도움말과 메시지는 영어(제품 규칙).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor import __version__
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
)
from remote_ci_monitor.core.inputs import InputError, parse_kv, validate_inputs
from remote_ci_monitor.core.model import EXIT_UNKNOWN, TERMINAL_STATES
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


def _client(args: argparse.Namespace, *, need_token: bool = True) -> Client:
    try:
        cfg = load_client_config(
            getattr(args, "client_config", None),
            server=getattr(args, "server", None),
            token=getattr(args, "token", None),
        )
    except ConfigError as e:
        raise SystemExit(_usage(str(e))) from e
    if not cfg.server:
        raise SystemExit(_usage("no server configured (use --server, RCM_SERVER or client.toml)"))
    if need_token and not cfg.token:
        raise SystemExit(_usage("no token (use --token, RCM_TOKEN or client.toml token_env)"))
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
    if args.source != "tree":
        return _usage("only --source tree is available in this version (git_ref arrives in M3)")
    # ① 서버 스키마로 검증 — 실패면 보내지 않는다
    try:
        presets = client.presets()
    except ClientError as e:
        return _usage(f"cannot read presets: {e.message}")
    preset = presets.get(args.preset)
    if preset is None:
        names = ", ".join(sorted(presets)) or "(none)"
        return _usage(f"unknown preset '{args.preset}' — server has: {names}")
    if "tree" not in preset.source_modes:
        return _usage(f"preset '{preset.name}' does not accept a tree snapshot")
    try:
        inputs = validate_inputs(preset, inputs_raw)
    except InputError as e:
        return _usage(str(e))
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
        label = args.by or default_label(None)
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
    return _wait(client, job_id, timeout=args.timeout, joined=joined)


def _wait(client: Client, job_id: int, *, timeout: float | None, joined: bool) -> int:
    line = _StatusLine()
    last: dict[str, Any] | None = None

    def on_update(job: dict[str, Any]) -> None:
        nonlocal last
        last = job
        line.update(describe(job))

    try:
        code, job, reason = wait_for_job(client, job_id, timeout=timeout, on_update=on_update)
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
    return _wait(client, args.job, timeout=args.timeout, joined=False)


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


def cmd_check(args: argparse.Namespace) -> int:
    rows: list[tuple[str, bool, str]] = []
    client = None
    try:
        client = _client(args, need_token=False)
    except SystemExit:
        rows.append(("server", False, "no server configured"))
    if client is not None:
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
            rows.append(("token", False, "no token (RCM_TOKEN or client.toml)"))
        try:
            doc = client.status()
            names = ", ".join(p["name"] for p in doc.get("presets", [])) or "(none)"
            rows.append(("presets", bool(doc.get("presets")), names))
            rows.append(("timezone", True, doc.get("display_timezone") or "server local"))
        except ClientError as e:
            rows.append(("presets", False, e.message))
    try:
        cfg = load_server_config(getattr(args, "config", None))
        if cfg.path is not None:
            d = cfg.data_dir
            writable = os.access(d, os.W_OK) if d.exists() else os.access(d.parent, os.W_OK)
            rows.append(
                ("data dir", writable, f"{d} ({'writable' if writable else 'not writable'})")
            )
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
    run.add_argument("--source", choices=["tree", "git_ref"], default="tree")
    run.add_argument("--ref", help="git ref (git_ref mode, M3)")
    run.add_argument("--by", metavar="LABEL", help="requester label (default: user@host)")
    run.add_argument("--no-join", action="store_true", help="never join an identical active job")
    run.add_argument("--no-wait", action="store_true", help="submit and exit 0 without waiting")
    run.add_argument(
        "--exclude", action="append", metavar="PATTERN", help="extra .rcmignore pattern"
    )
    run.add_argument("--dir", help="directory to snapshot (default: current directory)")
    run.add_argument("--timeout", type=float, help="give up waiting after N seconds (exit 3)")
    client_opts(run)
    run.set_defaults(func=cmd_run)

    wait = sub.add_parser("wait", help="wait for a job and exit with 0/1/2/3")
    wait.add_argument("--job", type=int, required=True)
    wait.add_argument("--timeout", type=float, help="give up waiting after N seconds (exit 3)")
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

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=lambda a: print(f"rcm {__version__}") or 0)
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
