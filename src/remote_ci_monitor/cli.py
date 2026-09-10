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
import signal
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor import SCHEMA_VERSION, __version__
from remote_ci_monitor import apply as apply_mod
from remote_ci_monitor.client import (
    Client,
    ClientError,
    default_label,
    make_snapshot,
    upload_cached,
    wait_for_job,
)
from remote_ci_monitor.config import (
    ConfigError,
    load_client_config,
    load_server_config,
    user_config_dir,
)
from remote_ci_monitor.core.artifacts import BundleFile
from remote_ci_monitor.core.gitref import validate_ref
from remote_ci_monitor.core.inputs import InputError, parse_kv, validate_inputs
from remote_ci_monitor.core.model import EXIT_UNKNOWN, TERMINAL_STATES, Preset
from remote_ci_monitor.core.render_text import (
    MAX_IDENT,
    failure_lines,
    fmt_clock,
    fmt_duration,
    render_gc,
    source_ident,
    storage_row,
)
from remote_ci_monitor.core.status import parse_iso
from remote_ci_monitor.mdns import discover

USAGE_EXIT = 2
#: 전달 실패 전용 종료 코드 — 실행 결과(`wait_exit_code`)는 건드리지 않는다(M5e §12).
EXIT_DELIVERY = 5


def _err(msg: str) -> None:
    print(f"rcm: {msg}", file=sys.stderr, flush=True)


def _info(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class _StatusLine:
    """TTY 면 한 줄을 덮어쓰고, 아니면 바뀔 때만 새 줄을 찍는다."""

    def __init__(self, stream=None, clock=time.monotonic):
        self.stream = stream if stream is not None else sys.stderr  # 호출 시점의 stderr(캡처 포함)
        # self.stream 으로 본다 — 인자 stream(None) 을 보면 진짜 터미널에서도 줄을 덮어쓰지 못한다
        self.tty = hasattr(self.stream, "isatty") and self.stream.isatty()
        self.last = ""
        self.clock = clock
        self.last_write = 0.0
        self.pending: str | None = None  # 비 TTY 에서 1초 안에 몰린 줄은 마지막 것만 나중에

    def update(self, text: str) -> None:
        if text == self.last:
            return
        self.last = text
        if self.tty:
            self.stream.write("\r\x1b[2K" + text)
            self.stream.flush()
            return
        now = self.clock()
        if now - self.last_write < 1.0:  # CI 로그·파일에 진행 줄이 수백 줄 쌓이지 않게
            self.pending = text
            return
        self.pending = None
        self.last_write = now
        self.stream.write(text + "\n")
        self.stream.flush()

    def done(self) -> None:
        if self.tty and self.last:
            self.stream.write("\n")
            self.stream.flush()
        elif self.pending is not None:
            self.stream.write(self.pending + "\n")
            self.stream.flush()
            self.pending = None


def describe(job: dict[str, Any], *, head: str | None = None) -> str:
    """wait 진행 한 줄: 상태 · 순번/스텝 · 경과 · ETA.

    `head` 를 주면 맨 앞의 `#<id> <state>` 자리에 그 문구가 들어간다(`--no-wait` 의 제출 줄).
    """
    state = job.get("state", "?")
    est = job.get("estimate") or {}
    parts = [head or f"#{job.get('id')} {state}"]
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
        # `head` 를 다시 쓰지 않는다 — 인자를 가리면 순서만 바뀌어도 머리가 조용히 스텝이 된다
        step = f"step {prog.get('current_index') or prog.get('steps_done')}/{total or '?'}"
        if prog.get("steps_total_partial"):
            step += "+"
        if prog.get("current_name"):
            step += f" {prog['current_name']}"
        parts.append(step)
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


#: `--no-wait` 의 표시용 조회 상한(초). 제출은 이미 끝났으니 오래 붙들지 않는다.
NO_WAIT_VIEW_TIMEOUT = 5.0

#: 조회한 잡 문서에서 `--no-wait` JSON 이 그대로 싣는 칸. 모르는 값은 서버가 이미 null 로 준다.
NO_WAIT_KEYS = ("position", "reason", "ahead_job_id", "blocked_by", "estimate")


def _job_view(client: Client, job_id: int) -> dict[str, Any] | None:
    """순번·ETA 를 그리려고 잡을 **한 번** 조회한다. 표시용이라 실패는 삼킨다.

    이 시점의 잡은 이미 큐에 들어가 있다 — 조회가 깨졌다고 제출을 실패로 만들지 않는다.
    Ctrl-C 도 여기서는 삼킨다: 잡은 이미 났고 세션이 알아야 하는 건 그 번호다(결정 17 의 뜻).

    문서는 우리가 만든 게 아니라 **값의 타입까지 믿을 수 없다**. `{"position": "3"}` 하나면
    `describe()` 가 터지고, 그 예외는 `main()` 의 그물에도 안 걸려 이미 큐에 있는 잡을 실패로
    만든다. 그래서 **한 번 그려 보고** 터지면 조회가 실패한 것과 똑같이 취급한다 — 줄도 JSON 도
    순번 조각을 통째로 뺀다. `state` 없는 문서는 잡 문서가 아니다(`{}` 를 「순번 없는 대기 잡」
    으로 읽지 않는다).
    """
    try:
        view = client.job(job_id, timeout=NO_WAIT_VIEW_TIMEOUT)
    except (ClientError, ValueError, OSError, KeyboardInterrupt):
        return None
    if not isinstance(view, dict) or not view.get("state"):
        return None
    try:
        describe(view)  # 그려지는 문서만 쓴다(진짜 줄은 head 만 바꿔 다시 그린다)
    except Exception:
        return None
    return view


def _submitted_line(
    job_id: int,
    view: dict[str, Any] | None,
    *,
    joined: bool,
    state: str | None,
    url: str | None,
    detail: str = "",
) -> str:
    """`--no-wait` 이 stderr 에 찍는 한 줄: 무엇을 냈나 · 몇 번째인가 · 언제 끝나나 · 어디서 보나.

    조회가 안 됐으면 순번 조각만 빠진다(`submitted job #155 queued · <url>`).
    """
    head = f"{'joined' if joined else 'submitted'} job #{job_id}"
    if state:
        head += f" {state}"
    if view is not None:
        head = describe(view, head=head)
    return " · ".join([head, *([detail] if detail else []), *([url] if url else [])])


def _no_wait_json(
    job_id: int,
    view: dict[str, Any] | None,
    *,
    joined: bool,
    state: str | None,
    url: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`--no-wait` 의 stdout JSON.

    조회가 안 됐으면 순번 칸을 **아예 넣지 않는다** — null 은 「순번이 없다」는 뜻이라 다르다.
    """
    body: dict[str, Any] = {"job_id": job_id, "joined": joined, "state": state}
    if view is not None:
        body.update({k: view.get(k) for k in NO_WAIT_KEYS})
    body.update(extra or {})
    body["url"] = url
    return body


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


DISCOVER_NONE_HINT = (
    "no rcm server found on this network (is the server's advertise on? same Wi-Fi?)"
)


def _resolve_server(cfg: Any, *, timeout: float = 1.5) -> Any:
    """서버 주소가 없으면 같은 네트워크에서 찾는다(M5c). 정확히 하나면 그것, 아니면 `ConfigError`.
    찾은 서버는 `cfg.discovered` 에 남는다(`rcm check` 표시용)."""
    cfg.discovered = None
    if not cfg.wants_discovery:
        return cfg
    found = discover(timeout=timeout)
    if len(found) == 1:
        cfg.server = found[0].address
        cfg.discovered = found[0]
        ip = found[0].ips[0] if found[0].ips else found[0].host.rstrip(".")
        _info(f"server: found {found[0].name} ({ip}:{found[0].port}) on this network")
        return cfg
    if not found:
        raise ConfigError(f"{NO_SERVER_HINT} — {DISCOVER_NONE_HINT}")
    names = ", ".join(f"{f.name} ({f.address})" for f in found)
    raise ConfigError(
        f"several rcm servers on this network: {names} — pick one with --server or client.toml"
    )


def _client(args: argparse.Namespace, *, need_token: bool = True) -> Client:
    try:
        cfg = _resolve_server(_client_config(args))
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


def _client_fail(client: Client, what: str, e: ClientError) -> int:
    """읽기 명령의 실패 문구. 조용한 3 은 없다(사용자 검사 U2.9)."""
    if e.status == 401 and "read access" in e.message:
        _err(f"{what}: read access denied — this server needs a token for reads (RCM_TOKEN)")
        return USAGE_EXIT
    if e.status:
        return _usage(f"{what}: {e.message}")
    msg = (
        e.message
        if e.message.startswith("cannot reach")
        else f"cannot reach {client.server}: {e.message}"
    )
    _err(f"{what}: {msg}")
    return EXIT_UNKNOWN


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
    fetch = bool(getattr(args, "fetch_artifacts", False))
    if fetch and args.no_wait:
        return _usage("--fetch-artifacts cannot be used with --no-wait (you have to wait to fetch)")
    if fetch and mode == "git_ref" and not getattr(args, "output", None):
        return _usage("--fetch-artifacts needs --output DIR for a git_ref preset (no local tree)")
    try:
        inputs = validate_inputs(preset, inputs_raw)
    except InputError as e:
        return _usage(str(e))
    # --by > client.toml 의 label / RCM_LABEL > "<user>@<host>" (수용 검사 J2 지적)
    try:
        cfg_label = _client_config(args).label
    except ConfigError:
        cfg_label = ""
    label = args.by or cfg_label or default_label(None)
    pool = getattr(args, "pool", None)
    if pool is not None and pool != preset.pool and pool not in preset.pools:
        allowed = ", ".join([preset.pool, *preset.pools])
        return _usage(f"preset '{preset.name}' runs in pools: {allowed} — not '{pool}'")
    # 토큰은 스냅샷을 만들기 전에 확인한다 — 큰 트리를 다 싸고 나서 401 을 보면 늦다(실배치 224 MB)
    try:
        client.whoami()
    except ClientError as e:
        if e.status in (401, 403):
            return _usage(
                f"token rejected by {client.server}: {e.message} — check RCM_TOKEN/client.toml"
            )
        if e.status == 0:
            return _client_fail(client, "run", e)
    if mode == "git_ref":
        return _run_git_ref(client, args, preset, inputs, ref or "", label, pool)
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
            "branch": snap.branch,  # 목록에서 「내 잡」을 알아보는 칸 (M5h)
            "base_sha": snap.base_sha,
            "dirty": snap.dirty,
            "tree_hash": snap.tree_hash,
            "bytes": snap.bytes,
        }
        # ③ 제출 (합류면 업로드 생략)
        try:
            resp = client.submit(
                preset.name,
                inputs,
                source,
                requester_label=label,
                join=not args.no_join,
                priority=args.priority,
                pool=pool,
            )
        except ClientError as e:
            _err(f"submit failed: {e.message}")
            return USAGE_EXIT if e.status in (400, 401, 403, 413, 0) else EXIT_UNKNOWN
        job_id = int(resp["job_id"])
        joined = bool(resp.get("joined"))
        state = resp.get("state")  # 합류면 그 잡의 상태, 새 잡이면 uploading
        if joined:
            if not args.no_wait:  # --no-wait 은 순번까지 실은 한 줄로 대신 말한다
                _info(f"joined job #{job_id} ({state}) — same preset, inputs and tree")
        else:
            # ④ 업로드
            line = _StatusLine()

            def progress(sent: int, total: int) -> None:
                pct = 100 * sent // total if total else 100
                line.update(
                    f"uploading #{job_id}: {sent / 1e6:.1f} / {total / 1e6:.1f} MB ({pct}%)"
                )

            try:
                if resp.get("cache") and not args.no_cache:
                    up = upload_cached(client, job_id, snap, progress=progress)
                    total = snap.total_bytes
                    cached = up.get("cached_bytes") or 0
                    pct = 100 * cached // total if total else 100
                    line.update(
                        f"uploading #{job_id}: {max(0, total - cached) / 1e6:.1f} / "
                        f"{total / 1e6:.1f} MB (cache {pct}%)"
                    )
                else:
                    up = client.upload(job_id, snap.tar_path, progress=progress)
            except ClientError as e:
                line.done()
                hint = " (retry with --no-cache)" if e.status in (400, 409) else ""
                _err(f"upload failed: {e.message}{hint}")
                return EXIT_UNKNOWN
            line.done()
            state = up.get("state") or state  # 트리를 다 받았다 — 이제 queued 다
            if not args.no_wait:
                _info(f"submitted job #{job_id} · {resp.get('url', '')}")
    finally:
        try:
            snap.tar_path.unlink()
        except OSError:
            pass
    if args.no_wait:
        # 순번·ETA 는 표시용이다 — 조회가 실패해도 잡은 큐에 있고 종료 코드는 0 이다
        view = _job_view(client, job_id)
        state = (view or {}).get("state") or state
        url = resp.get("url")
        detail = "same preset, inputs and tree" if joined else ""
        _info(_submitted_line(job_id, view, joined=joined, state=state, url=url, detail=detail))
        _info(f"fetch its artifacts later with `rcm artifacts {job_id} --fetch --output DIR`")
        _print_json(_no_wait_json(job_id, view, joined=joined, state=state, url=url))
        return 0
    # ⑤ wait — 끝나면 산출물을 제출한 그 트리에 쓴다(§11)
    spec = None
    if fetch:
        spec = _FetchSpec(
            root=Path(getattr(args, "output", None) or root),
            baseline={e.path: e.sha256 for e in snap.entries if e.kind != "link"},
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    # 장부를 놓는다 — 대기는 20분이고 tar 은 이미 지웠다. 2만 파일 트리에서 64 → 32 MB 다
    # (M5h §4.6). `--fetch-artifacts` 가 쓰는 것은 위에서 뽑은 해시 사전 하나뿐이다.
    snap = None  # noqa: F841 — 참조를 끊는 것이 목적이다
    del snap
    return _wait(
        client, job_id, timeout=args.timeout, joined=joined, use_sse=not args.poll, fetch=spec
    )


def _run_git_ref(
    client: Client,
    args: argparse.Namespace,
    preset: Preset,
    inputs: dict[str, Any],
    ref: str,
    label: str,
    pool: str | None = None,
) -> int:
    """git_ref 제출: 스냅샷·업로드 없이 ③ 제출 → ⑤ wait. 서버가 ref 를 sha 로 확정한다."""
    try:
        resp = client.submit(
            preset.name,
            inputs,
            {"mode": "git_ref", "ref": ref},
            requester_label=label,
            join=not args.no_join,
            priority=args.priority,
            pool=pool,
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
    state = resp.get("state")
    url = resp.get("url")
    if args.no_wait:
        # 제출 응답의 state 는 「방금 만들었다」는 뜻이다 — 순번과 함께 지금 상태를 다시 본다
        view = _job_view(client, job_id)
        state = (view or {}).get("state") or state
        detail = (
            f"same preset, inputs, commit {short}"
            if joined
            else f"({preset.name} · {ref} @{short})"  # 안에 `·` 가 있다 — 목록 항목과 안 섞이게
        )
        _info(_submitted_line(job_id, view, joined=joined, state=state, url=url, detail=detail))
        _print_json(
            _no_wait_json(
                job_id, view, joined=joined, state=state, url=url, extra={"ref": ref, "sha": sha}
            )
        )
        return 0
    if joined:
        _info(f"joined job #{job_id} ({state}) — same preset, inputs, commit {short}")
    else:
        _info(f"submitted job #{job_id} ({preset.name} · {ref} @{short}) · {url or ''}")
    return _wait(client, job_id, timeout=args.timeout, joined=joined, use_sse=not args.poll)


@dataclass(frozen=True)
class _FetchSpec:
    """`--fetch-artifacts` 가 정해 둔 것. 기준선은 **제출할 때 실제로 보낸** 내용의 해시다(§11)."""

    root: Path
    baseline: dict[str, str]
    force: bool = False
    dry_run: bool = False


def _wait(
    client: Client,
    job_id: int,
    *,
    timeout: float | None,
    joined: bool,
    use_sse: bool = True,
    fetch: _FetchSpec | None = None,
) -> int:
    line = _StatusLine()
    last: dict[str, Any] | None = None

    def on_update(job: dict[str, Any]) -> None:
        nonlocal last
        last = job
        line.update(describe(job))

    try:
        code, job, reason = wait_for_job(
            client,
            job_id,
            timeout=timeout,
            on_update=on_update,
            use_sse=use_sse,
            on_info=line.update,
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
    # 0 이 아닌 끝에는 로그로 가는 길과 이름별 최근 이력을 붙인다(M5h §2.5). 3(모른다)에도
    # 붙인다 — 모를수록 로그가 필요하다.
    # 확정 404 만 로그 줄을 뺀다 — 없는 잡의 로그 길은 아무것도 안 가리킨다(M5i I3).
    if code != 0:
        cause = getattr(reason, "cause", None)
        for text in failure_lines(
            job or {}, job_id=job_id, url=(job or {}).get("url"), cause=cause
        ):
            _err(text)
    out = dict(job or {"job_id": job_id, "state": None})
    out.setdefault("job_id", out.get("id", job_id))  # --no-wait 출력과 같은 키로도 읽히게
    out["wait_exit_code"] = code
    if joined:
        out["joined"] = True
    if fetch is not None:
        delivery = _fetch_artifacts(
            client,
            job_id,
            fetch.root,
            baseline=fetch.baseline,
            force=fetch.force,
            dry_run=fetch.dry_run,
            journal=fetch.root / ".rcm-artifacts.json",
        )
        if delivery is not None:
            out["artifact_fetch"] = delivery
            # **실행 실패가 전달 실패보다 우선한다** — 테스트가 깨진 것을 먼저 알아야 한다(§12)
            if code == 0 and not delivery.get("complete"):
                code = EXIT_DELIVERY
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


def cmd_bump(args: argparse.Namespace) -> int:
    """대기 잡의 우선순위 변경(admin)."""
    client = _client(args)
    try:
        resp = client.set_priority(args.job, args.priority)
    except ClientError as e:
        _err(f"bump failed: {e.message}")
        return USAGE_EXIT if e.status else EXIT_UNKNOWN
    _print_json(resp)
    _info(f"job #{args.job} priority is now {resp.get('priority')}")
    return 0


def _offline_gc(args: argparse.Namespace) -> int:
    """서버 없이 도는 `--dry-run`(결정 61) — 설정과 데이터 디렉터리만 읽고 아무것도 안 지운다.

    업그레이드 안전 게이트가 이것 위에 서 있다. `POST /gc` 는 새 서버에만 있고 새 서버는 뜨자마자
    sweep 하므로, 올리기 **전에** 무엇이 지워질지 보려면 서버 없이 도는 길이 있어야 한다.
    """
    from remote_ci_monitor.janitor import Janitor, _item_json
    from remote_ci_monitor.store import Store

    try:
        cfg = load_server_config(args.config, check_tools=False)
    except ConfigError as e:
        return _usage(str(e))
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jan = Janitor(store, cfg)
        now = datetime.now(UTC)
        plan = jan.plan(now)
        body = {
            "dry_run": True,
            "planned": [_item_json(i) for i in plan.items],
            "deleted": [],
            "failed": [],
            "freed_bytes": 0,
            "storage_before": jan.storage(now),
            "storage_after": None,
        }
    finally:
        store.close()
    if getattr(args, "json", False):
        _print_json(body)
    else:
        print(render_gc(body), flush=True)
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    """`rcm gc [--dry-run]` — 청소기와 **같은 계획 함수**를 손으로 돌린다(admin 토큰)."""
    if args.dry_run and args.config:
        return _offline_gc(args)
    client = _client(args)
    try:
        body = client.gc(dry_run=args.dry_run, timeout=args.timeout)
    except ClientError as e:
        if not e.status:  # 연결·시한 — 「모른다」이지 실패가 아니다(`rcm wait` 와 같은 규칙)
            _err(
                f"gc: {e.message}. The server may still be deleting — "
                "run `rcm gc --dry-run` again to see what is left."
            )
            return EXIT_UNKNOWN
        _err(f"gc failed: {e.message}")
        return USAGE_EXIT
    if args.json:
        _print_json(body)
    else:
        print(render_gc(body), flush=True)
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
            pools = doc.get("pools") or [{}]
            bad = next((p for p in pools if p.get("queue") is None), None)
            if bad is not None:
                return _usage(f"queue unavailable: {bad.get('queue_error') or 'unknown'}")
            queue = [r for p in pools for r in p.get("queue") or []]
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
        resp = client.eta(
            args.preset,
            inputs,
            priority=getattr(args, "priority", None),
            pool=getattr(args, "pool", None),
        )
    except ClientError as e:
        return _client_fail(client, "eta failed", e)
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
                label = "read access denied" if e.status == 401 else "unreachable"
                text = f"━━━ rcm · {client.server} · {label}: {e.message}\n"
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


def _cut(text: str, width: int) -> str:
    """칸에 맞춰 자른다 — 안 자르면 긴 값 하나가 그 줄의 뒤 칸을 전부 민다."""
    return text if len(text) <= width else text[: width - 1] + "…"


def _row_ref(row: dict[str, Any]) -> str:
    """그 잡이 돌린 ref(git_ref) 또는 브랜치(tree). `--ref` 는 여기에 부분 일치한다."""
    src = row.get("source") or {}
    return str(src.get("ref") or src.get("branch") or "")


def cmd_jobs(args: argparse.Namespace) -> int:
    client = _client(args, need_token=bool(args.mine))
    me: str | None = None
    try:
        if args.mine:
            me = client.whoami()["name"]
        doc = client.status()
    except ClientError as e:
        return _client_fail(client, "jobs failed", e)
    rows: list[dict[str, Any]] = []
    for pool in doc.get("pools") or [{}]:
        pname = pool.get("pool") or pool.get("name") or "default"
        if args.pool and pname != args.pool:
            continue
        if pool.get("queue") is None:
            print(f"queue unavailable: {pool.get('queue_error') or 'unknown'}", file=sys.stderr)
        else:
            rows.extend({**r, "pool": r.get("pool") or pname} for r in pool["queue"])
        if pool.get("recent") is None:
            print(f"recent unavailable: {pool.get('recent_error') or 'unknown'}", file=sys.stderr)
        else:
            rows.extend({**r, "pool": r.get("pool") or pname} for r in pool["recent"])
    if me is not None:
        rows = [
            r
            for r in rows
            if (r.get("requester") or {}).get("name") == me
            or any(j.get("name") == me for j in r.get("joiners") or [])
        ]
    if args.state:
        rows = [r for r in rows if r.get("state") == args.state]
    if getattr(args, "ref", None):
        # 한 기계의 세션들이 토큰을 나눠 쓰면 `--mine` 은 「이 기계의 잡」이다. 브랜치·ref 는
        # 세션이 자기 잡을 알아보는 가장 가까운 칸이다(M5h §4.3).
        want = args.ref
        rows = [r for r in rows if want in _row_ref(r)]
    if args.json:
        _print_json(rows)
        return 0
    tz = _local_tz()
    if not rows:
        print("no jobs")
        return 0
    pools_seen = {r.get("pool") or "default" for r in rows}
    current_pool: str | None = None
    for r in rows:
        if len(pools_seen) > 1 and r.get("pool") != current_pool:  # 풀이 둘 이상일 때만 헤더
            current_pool = r.get("pool")
            print(f"pool {current_pool}")
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
        jid = f"#{r.get('id')}"
        key = str(r.get("key") or "?")
        print(
            f"{jid:<6} {state:<10} {_cut(key, 16):<16} {label:<20} "
            f"{source_ident(r.get('source')):<{MAX_IDENT}} {pos}{timing:<16} "
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
        return _client_fail(client, "logs failed", e)
    except KeyboardInterrupt:
        return 130
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    client = _client(args, need_token=False)
    try:
        doc = client.status()
    except ClientError as e:
        return _client_fail(client, "presets failed", e)
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
        # 템플릿의 `server = "…"` 안에 그대로 들어간다 — 따옴표·역슬래시·공백이 있으면 TOML 이
        # 깨지거나 엉뚱한 주소가 조용히 남으니 여기서 막는다.
        if any(c in '"\\' or c.isspace() for c in server):
            return _usage("--server must not contain quotes, backslashes or whitespace")
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


def _dir_writable(d: Path) -> bool:
    """`rcm check`·`rcm worker --check` 의 data dir 행 — 있으면 그 디렉터리, 없으면 **가장 가까운
    있는 조상**이 쓰기 가능한가(서버·워커가 `mkdir -p` 로 만든다). 부모만 보면 새 머신의 기본
    `~/.local/share/rcm-worker` 는 `~/.local/share` 가 없어 거짓 FAIL 이 난다."""
    p = d
    while not p.exists():
        if p.parent == p:
            return False
        p = p.parent
    return p.is_dir() and os.access(p, os.W_OK)


#: 이만큼 넘게 막혀 있으면 「사람이 한번 봐라」. 서버의 `admission_cooldown_seconds` 를 쓰지 않는
#: 이유: 그건 **원격 서버의** 설정이라 `rcm check` 가 볼 수 없다. 이 경고는 게이트의 타이밍이
#: 아니라 「누가 이 머신을 오래 잡고 있다」는 뜻이므로 고정 상수가 맞다(M5f §5.4).
HELD_WARN_SECONDS = 300


def _held_too_long(workers: list[dict[str, Any]], now: datetime | None) -> list[str]:
    out: list[str] = []
    for w in workers:
        if w.get("state") != "held":
            continue
        since = parse_iso(w.get("held_since"))
        if now is None or since is None:
            continue
        seconds = (now - since).total_seconds()
        if seconds > HELD_WARN_SECONDS:
            label = w.get("display_name") or f"lane {w.get('lane')}"
            out.append(f"{label} held for {fmt_duration(seconds)}")
    return out


def _pools_row(doc: dict[str, Any], client: Client) -> tuple[str, bool | None, str]:
    """`rcm check` 의 pools 행(M5b-4): `default (1 lane) · linux (build-02/1 idle · build-03 down)`.
    어떤 풀의 원격 워커가 전부 down 이면 FAIL(`/api/health.pools_without_workers`).

    부하로 보류된 레인은 세어서 보이되 **FAIL 이 아니다**(의도된 동작). 다만 오래 막혀 있으면
    경고한다 — 「게이트가 제 일을 하는 중」과 「누가 두 시간째 잡고 있음」은 다르다(M5f)."""
    server = doc.get("server") or {}
    now = parse_iso(doc.get("generated_at"))
    lanes = server.get("lanes") or 0
    all_workers = server.get("workers") or []
    local_held = [w for w in all_workers if not w.get("worker") and w.get("state") == "held"]
    head = f"default ({lanes} lane{'s' if lanes != 1 else ''}"
    head += f" · {len(local_held)} held)" if local_held else ")"
    parts = [head]
    by_pool: dict[str, list[dict[str, Any]]] = {}
    for w in server.get("workers") or []:
        if w.get("worker"):
            by_pool.setdefault(w.get("pool") or "default", []).append(w)
    for name in sorted(by_pool):
        pills: list[str] = []
        for w in sorted(by_pool[name], key=lambda x: (str(x.get("worker")), x.get("lane") or 0)):
            if w.get("state") == "down":
                pill = f"{w['worker']} down"
                if pill not in pills:
                    pills.append(pill)
                continue
            pill = f"{w.get('display_name') or w['worker']}/{w.get('lane')} {w.get('state')}"
            if w.get("display_name"):
                pill = f"{w['display_name']} {w.get('state')}"
            if w.get("job_id"):
                pill += f" #{w['job_id']}"
            pills.append(pill)
        parts.append(f"{name} ({' · '.join(pills)})")
    dead: list[str] = []
    try:
        dead = list(client.health().get("pools_without_workers") or [])
    except ClientError:
        dead = [n for n, ws in by_pool.items() if all(w.get("state") == "down" for w in ws)]
    text = " · ".join(parts)
    stuck = _held_too_long(all_workers, now)
    if dead:
        return ("pools", False, text)
    if stuck:  # warn — 실패는 아니다
        return ("pools", None, f"{text} — {', '.join(stuck)}")
    return ("pools", True, text)


def cmd_check(args: argparse.Namespace) -> int:
    # ok 는 True(ok) · False(FAIL, 종료 코드 1) · None(warn — 알려는 주되 실패는 아니다)
    rows: list[tuple[str, bool | None, str]] = [python_row()]
    client = None
    cfg = None
    try:
        cfg = _client_config(args)
    except ConfigError as e:
        rows.append(("client config", False, str(e)))
    if cfg is not None:
        try:
            _resolve_server(cfg)  # 주소가 없으면 같은 네트워크에서 찾는다 — 결과는 server 행에
        except ConfigError as e:
            rows.append(("server", False, str(e)))
            cfg = None
    if cfg is not None:
        if cfg.server:
            client = Client(cfg.server, cfg.token or None)
        else:
            rows.append(("server", False, NO_SERVER_HINT))
    if client is not None and cfg is not None:
        found_tag = " (found on this network)" if getattr(cfg, "discovered", None) else ""
        try:
            h = client.health()
            rows.append(
                ("server", bool(h.get("ok")), f"{client.server} · v{h.get('version')}{found_tag}")
            )
            adv = h.get("advertise") or {}
            if adv.get("error"):
                # 광고가 켜져 있는데 실제로는 못 나간다 — 발견은 부가 기능이라 FAIL 은 아니다
                rows.append(("advertise", None, f"server cannot be discovered: {adv['error']}"))
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
            rows.append(_pools_row(doc, client))
            rows.append(("timezone", True, doc.get("display_timezone") or "server local"))
            storage = (doc.get("server") or {}).get("job_storage")
            row = storage_row(storage, now=doc.get("generated_at")) if storage else None
            if row is not None:  # 옛 서버엔 키가 없고, 갓 뜬 서버는 아직 잰 게 없다
                rows.append(row)
        except ClientError as e:
            rows.append(("presets", False, e.message))
    try:
        cfg = load_server_config(getattr(args, "config", None), check_tools=False)
        if cfg.path is not None:
            d = cfg.data_dir
            writable = _dir_writable(d)
            # 서버는 `data_dir` 을 API 로 내리지 않는다 — 이 행은 **로컬 설정**의 사실이지
            # `--server` 가 가리키는 서버의 디렉터리가 아니다. 이름과 출처가 그렇게 말한다(M5i I4).
            state = "writable" if writable else "not writable"
            rows.append(("local data dir", writable, f"{d} ({state}) · from {cfg.path}"))
            if cfg.repos:
                git = shutil.which("git")
                rows.append(("git", git is not None, git or "not on PATH (git_ref presets)"))
    except ConfigError as e:
        rows.append(("server config", False, str(e)))
    ok_all = all(ok is not False for _, ok, _ in rows)
    for name, ok, detail in rows:
        label = "ok " if ok else ("warn" if ok is None else "FAIL")
        print(f"{label}  {name:<13} {detail}")
    return 0 if ok_all else 1


def cmd_discover(args: argparse.Namespace) -> int:
    """`rcm discover` — 같은 네트워크의 rcm 서버들(mDNS). 없으면 1."""
    found = discover(timeout=float(getattr(args, "timeout", 1.5) or 1.5))
    if getattr(args, "json", False):
        _print_json(
            [
                {
                    "name": f.name,
                    "host": f.host,
                    "port": f.port,
                    "ips": list(f.ips),
                    "address": f.address,
                    "version": f.version,
                    "lanes": f.lanes,
                }
                for f in found
            ]
        )
        return 0 if found else 1
    if not found:
        _err(DISCOVER_NONE_HINT)
        return 1
    print(f"{'name':<20} {'address':<30} {'version':<9} lanes")
    for f in found:
        lanes = "—" if f.lanes is None else str(f.lanes)
        print(f"{f.name:<20} {f.address:<30} {f.version or '—':<9} {lanes}")
    return 0


def _worker_config(args: argparse.Namespace):
    from remote_ci_monitor.config import load_worker_config

    overrides = {
        "server": getattr(args, "server", None),
        "pool": getattr(args, "pool", None),
        "lanes": getattr(args, "lanes", None),
        "name": getattr(args, "name", None),
        "data_dir": getattr(args, "data", None),
    }
    return load_worker_config(getattr(args, "config", None), overrides=overrides)


def cmd_worker(args: argparse.Namespace) -> int:
    """`rcm worker` — 원격 워커 프로세스(M5b-3). 토큰은 RCM_WORKER_TOKEN 또는 worker.toml 로만."""
    from remote_ci_monitor.client import WorkerClient
    from remote_ci_monitor.remote_worker import RemoteWorker, WorkerExit

    try:
        cfg = _worker_config(args)
    except ConfigError as e:
        return _usage(str(e))
    if not cfg.token:
        return _usage(
            "no worker token: set RCM_WORKER_TOKEN (create one on the server with "
            "`rcm token add NAME --worker`)"
        )
    client = WorkerClient(cfg.server, cfg.token)
    if getattr(args, "check", False):
        return _worker_check(cfg, client)
    worker = RemoteWorker(cfg, client=client, once=bool(getattr(args, "once", False)))

    def _stop(signum: int, _frame: Any) -> None:
        if worker.stopping.is_set():
            # 두 번째 신호는 즉시 — SystemExit 은 `run()` 의 finally 가 레인 join(grace+10초)으로
            # 붙잡고, 레인 스레드는 daemon 이 아니라 인터프리터 종료도 기다린다
            _info(f"signal {signum} again: exiting now")
            sys.stderr.flush()
            os._exit(1)
        _info(f"signal {signum}: stopping — running jobs are reported as lost")
        worker.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        return worker.run()
    except WorkerExit as e:
        if e.message:
            _err(e.message)
        return e.code


def _ls_remote_problem(url: str, timeout: float = 10.0) -> str | None:
    """`git ls-remote` 가 닿는지. 문제면 짧은 사유(경로·URL 없이), 아니면 None."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--exit-code", url, "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return f"unreachable: timed out after {int(timeout)}s"
    except OSError as e:
        return f"cannot run git: {type(e).__name__}"
    if proc.returncode == 0:
        return None
    lines = [ln.strip() for ln in proc.stderr.decode("utf-8", errors="replace").splitlines()]
    lines = [ln for ln in lines if ln]
    # git 은 사유를 첫 줄(`fatal: '…' does not appear to be a git repository` · `ssh: …`)에 두고
    # 마지막 줄은 일반 안내문(`and the repository exists.`)이다 — 첫 줄을 쓰고 경로·자격은 지운다
    reason = lines[0] if lines else f"exit {proc.returncode}"
    reason = re.sub(r"^(fatal|error|warning):\s*", "", reason)
    reason = re.sub(r"://[^/\s@]+@", "://<redacted>@", reason)
    reason = re.sub(r"/[^\s'\"]+", "<path>", reason)
    return "unreachable: " + reason[:80]


def _worker_check(cfg: Any, client: Any) -> int:
    """`rcm worker --check` — 서버 · 토큰(kind worker) · 풀 · repos 를 표로."""
    from remote_ci_monitor.client import ClientError

    rows: list[tuple[str, bool, str]] = [python_row()]
    try:
        h = client.health()
        rows.append(("server", bool(h.get("ok")), f"{client.server} · v{h.get('version')}"))
    except ClientError as e:
        rows.append(("server", False, e.message if e.status else f"cannot reach {client.server}"))
    try:
        me = client.whoami()
        kind = me.get("kind") or ("admin" if me.get("admin") else "client")
        ok = kind == "worker"
        rows.append(
            (
                "token",
                ok,
                f"{me.get('name')} ({kind})"
                + ("" if ok else " — worker token required: rcm token add NAME --worker"),
            )
        )
    except ClientError as e:
        rows.append(("token", False, e.message if e.status else f"cannot reach {client.server}"))
    rows.append(("pool", True, f"{cfg.pool} · lanes {cfg.lanes}"))
    if cfg.repos:
        git = shutil.which("git")
        rows.append(("git", git is not None, git or "not on PATH (git_ref presets)"))
        details: list[str] = []
        all_ok = git is not None
        for r in cfg.repos:
            problem = _ls_remote_problem(r.url) if git else "git missing"
            all_ok = all_ok and problem is None
            details.append(f"{r.name} ({'ok' if problem is None else problem})")
        rows.append(("repos", all_ok, " · ".join(details)))
    else:
        rows.append(("repos", True, "none (git_ref presets cannot run on this worker)"))
    d = cfg.data_path
    writable = _dir_writable(d)
    rows.append(("data dir", writable, f"{d} ({'writable' if writable else 'not writable'})"))
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
            worker = bool(getattr(args, "worker", False))
            if args.admin and worker:
                return _usage("--admin and --worker cannot be combined")
            kind = "worker" if worker else ("admin" if args.admin else "client")
            secret = store.add_token(args.name, kind=kind, now=now)
            where = "RCM_TOKEN on the worker machine" if worker else "RCM_TOKEN on the client"
            _info(f"token '{args.name}' created — shown once, store it as {where}:")
            print(secret, flush=True)
            return 0
        if args.token_command == "list":
            print(f"{'name':<24} {'kind':<7} {'created':<11} revoked")
            for t in store.list_tokens():
                revoked = f"{t.revoked_at:%Y-%m-%d}" if t.revoked_at else "—"
                print(f"{t.name:<24} {t.kind:<7} {t.created_at:%Y-%m-%d}  {revoked}")
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


def _fetch_artifacts(
    client: Client,
    job_id: int,
    root: Path,
    *,
    baseline: dict[str, str],
    force: bool = False,
    dry_run: bool = False,
    journal: Path | None = None,
) -> dict[str, Any] | None:
    """묶음을 받아 트리에 쓴다(M5e §11). 결과 요약을 돌려준다. 받을 것이 없으면 None.

    절차: 스테이징으로 통째로 받고 → 전수 검사와 분류를 **보여 주고** → 쓰고 → 충돌 없이 전부
    적용됐을 때만 ack 한다. `--dry-run` 은 표만 찍고 아무것도 안 쓴다(ack 도 안 한다).
    """
    try:
        doc = client.artifacts(job_id)
    except ClientError as e:
        _err(f"artifacts: {e.message}")
        return {"state": "error", "complete": False, "wrote": 0, "conflicted": 0}
    state = doc.get("state")
    if state != "ready":
        # `disabled`·`empty` 는 실패가 아니다 — 모을 것을 선언하지 않았거나 없었던 것이다
        reason = doc.get("reason_code")
        _info(f"artifacts: {state}" + (f" ({reason})" if reason else ""))
        return None
    files = tuple(
        BundleFile(path=f["path"], size=f["size"], sha256=f["sha256"], mode=f["mode"])
        for f in (doc.get("files") or [])
    )
    _info(f"artifacts: {len(files)} files · {(doc.get('bundle_bytes') or 0) / 1e6:.1f} MB")
    with tempfile.TemporaryDirectory(prefix="rcm-artifacts-") as tmp:
        staging = Path(tmp) / "files"
        staging.mkdir()
        bundle = Path(tmp) / "bundle.tar"
        try:
            client.download_bundle(job_id, bundle)
            _extract_bundle(bundle, staging)
        except (ClientError, OSError, tarfile.TarError) as e:
            _err(f"artifacts: download failed: {e}")
            return {"state": state, "complete": False, "wrote": 0, "conflicted": 0}
        plan = apply_mod.plan(files, baseline, root)
        counts = plan.counts()
        _info(
            "artifacts: "
            + " · ".join(f"{k} {counts.get(k, 0)}" for k in ("new", "changed", "unchanged"))
            + f" · conflicted {counts.get('conflicted', 0)}"
        )
        if not plan.safe():
            for e in plan.entries:
                if e.verdict == apply_mod.UNSAFE:
                    _err(f"artifacts: refusing {e.path}: {e.reason}")
            return {"state": state, "complete": False, "wrote": 0, "conflicted": 0}
        if dry_run:
            _info("artifacts: dry run — wrote 0, unchanged 0, conflicted 0 (nothing written)")
            return {"state": state, "complete": False, "wrote": 0, "conflicted": 0, "dry_run": True}
        result = apply_mod.apply(plan, staging, root, force=force, journal=journal)
    _info(
        f"artifacts: wrote {result.wrote}, unchanged {result.skipped}, "
        f"conflicted {result.conflicted}" + (f", failed {result.failed}" if result.failed else "")
    )
    if result.complete and doc.get("bundle_sha256"):
        try:
            client.ack_artifacts(job_id, doc["bundle_sha256"])
        except ClientError as e:
            _err(f"artifacts: acknowledge failed: {e.message}")
    return {
        "state": state,
        "wrote": result.wrote,
        "unchanged": result.skipped,
        "conflicted": result.conflicted,
        "failed": result.failed,
        "complete": result.complete,
    }


def _extract_bundle(bundle: Path, staging: Path) -> None:
    """묶음을 **새로 만든** 스테이징에 푼다. `materialize.extract_tree` 는 목적지를 지운다."""
    with tarfile.open(bundle, "r:") as tar:
        tar.extractall(path=staging, filter="data")


def cmd_artifacts(args: argparse.Namespace) -> int:
    """`rcm artifacts JOB_ID [--fetch --output DIR]` — 끝난 잡의 산출물을 보고, 받는다(§12)."""
    if args.fetch and not args.output:
        # 기준선이 없다 — 어느 트리에 쓸지 사람이 정해야 한다(§11)
        return _usage("rcm artifacts --fetch needs --output DIR (there is no submitted tree here)")
    client = _client(args)
    if not args.fetch:
        try:
            doc = client.artifacts(args.job)
        except ClientError as e:
            _err(f"artifacts: {e.message}")
            return USAGE_EXIT if e.status in (400, 401, 403, 404) else EXIT_UNKNOWN
        _info(f"artifacts: {doc.get('state')}")
        for f in doc.get("files") or []:
            _info(f"  {f['path']}  {f['size']} bytes")
        _print_json(doc)
        return 0
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    journal = root / ".rcm-artifacts.json"
    out = _fetch_artifacts(
        client,
        args.job,
        root,
        baseline={},  # 기준선이 없다 — 이미 있는 파일은 --force 여야 덮는다(§11)
        force=args.force,
        dry_run=args.dry_run,
        journal=journal,
    )
    if out is None:
        return 0
    return 0 if out.get("complete") else EXIT_DELIVERY


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
    run.add_argument(
        "--priority",
        choices=["low", "normal", "high"],
        default=None,
        help="queue priority (default: the preset's; raising above it needs an admin token)",
    )
    run.add_argument(
        "--no-cache", action="store_true", help="upload a full tarball instead of changed files"
    )
    run.add_argument("--pool", metavar="NAME", help="worker pool (must be allowed by the preset)")
    run.add_argument("--by", metavar="LABEL", help="requester label (default: user@host)")
    run.add_argument("--no-join", action="store_true", help="never join an identical active job")
    run.add_argument("--no-wait", action="store_true", help="submit and exit 0 without waiting")
    run.add_argument(
        "--exclude", action="append", metavar="PATTERN", help="extra .rcmignore pattern"
    )
    run.add_argument("--dir", help="directory to snapshot (default: current directory)")
    run.add_argument(
        "--fetch-artifacts",
        action="store_true",
        help="write the job's artifacts back into the submitted tree when it finishes",
    )
    run.add_argument(
        "--force", action="store_true", help="with --fetch-artifacts: overwrite files you edited"
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="with --fetch-artifacts: show what would be written and write nothing",
    )
    run.add_argument(
        "--output", metavar="DIR", help="with --fetch-artifacts: write here instead of the tree"
    )
    run.add_argument("--timeout", type=float, help="give up waiting after N seconds (exit 3)")
    run.add_argument(
        "--poll", action="store_true", help="poll every 2s instead of the event stream"
    )
    client_opts(run)
    run.set_defaults(func=cmd_run)

    artifacts = sub.add_parser("artifacts", help="show or fetch a finished job's artifacts")
    artifacts.add_argument("job", type=int)
    artifacts.add_argument("--fetch", action="store_true", help="download and write the files")
    artifacts.add_argument(
        "--output", metavar="DIR", help="directory to write into (required with --fetch)"
    )
    artifacts.add_argument("--force", action="store_true", help="overwrite existing files")
    artifacts.add_argument(
        "--resume", action="store_true", help="continue an interrupted fetch from its journal"
    )
    artifacts.add_argument(
        "--dry-run", action="store_true", help="show what would be written and write nothing"
    )
    client_opts(artifacts)
    artifacts.set_defaults(func=cmd_artifacts)

    wait = sub.add_parser("wait", help="wait for a job and exit with 0/1/2/3")
    wait.add_argument("--job", type=int, required=True)
    wait.add_argument("--timeout", type=float, help="give up waiting after N seconds (exit 3)")
    wait.add_argument(
        "--poll", action="store_true", help="poll every 2s instead of the event stream"
    )
    client_opts(wait)
    wait.set_defaults(func=cmd_wait)

    bump = sub.add_parser("bump", help="change a waiting job's priority (admin token)")
    bump.add_argument("job", type=int)
    bump.add_argument("--priority", choices=["low", "normal", "high"], default="high")
    client_opts(bump)
    bump.set_defaults(func=cmd_bump)

    gc = sub.add_parser("gc", help="reclaim workspace storage now (admin token)")
    gc.add_argument("--dry-run", action="store_true", help="show what would go, delete nothing")
    gc.add_argument("--json", action="store_true")
    gc.add_argument("--timeout", type=float, default=600.0, help="seconds (default 600)")
    gc.add_argument("--config", help="server config: run --dry-run without a server")
    client_opts(gc)
    gc.set_defaults(func=cmd_gc)

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
    eta.add_argument("--priority", choices=["low", "normal", "high"], default=None)
    eta.add_argument("--pool", metavar="NAME", help="worker pool to estimate for")
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
    jobs.add_argument("--pool", metavar="NAME", help="only jobs of this worker pool")
    jobs.add_argument("--ref", metavar="REF", help="only jobs whose ref or branch contains REF")
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

    token = sub.add_parser("token", help="manage client/worker tokens (run on the server)")
    server_opts(token)
    tsub = token.add_subparsers(dest="token_command", required=True)
    add = tsub.add_parser("add", help="create a token and print it once")
    add.add_argument("name")
    add.add_argument(
        "--admin", action="store_true", help="admin token (cancel any job, pause/resume)"
    )
    add.add_argument(
        "--worker",
        action="store_true",
        help="worker token for `rcm worker` on another machine (/worker/* only)",
    )
    tsub.add_parser("list", help="list tokens (never shows secrets)")
    revoke = tsub.add_parser("revoke", help="revoke a token")
    revoke.add_argument("name")
    token.set_defaults(func=cmd_token)

    disc = sub.add_parser("discover", help="find rcm servers on this network (mDNS)")
    disc.add_argument("--json", action="store_true", help="machine-readable list")
    disc.add_argument("--timeout", type=float, default=1.5, help="seconds to listen (default 1.5)")
    disc.set_defaults(func=cmd_discover)

    worker = sub.add_parser(
        "worker", help="run a remote worker for a pool (token via RCM_WORKER_TOKEN)"
    )
    worker.add_argument("--server", help="server URL (or `server` in worker.toml)")
    worker.add_argument(
        "--pool", help="worker pool to serve (default: worker.toml pool or 'default')"
    )
    worker.add_argument("--lanes", type=int, help="parallel jobs on this machine (1-64)")
    worker.add_argument("--name", help="display name for the host sample (default: hostname)")
    worker.add_argument("--config", help="worker.toml path (repos, host sampler, data_dir)")
    worker.add_argument("--data", help="worker data dir (workspaces, logs)")
    worker.add_argument(
        "--check", action="store_true", help="verify server, token kind, pool and repos, then exit"
    )
    worker.add_argument(
        "--once", action="store_true", help="run at most one job, then exit (tests, cron)"
    )
    worker.set_defaults(func=cmd_worker)

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
