"""`rcm` CLI — run · wait · cancel · pause · resume · release · serve · check · token · version.

stdout 에는 JSON 한 줄(run·wait), stderr 에는 사람용 진행 표시. 종료 코드:
  run/wait: succeeded 0 · failed 1 · cancelled/timed_out 2 · lost/조회 실패/--timeout 3
  사용 오류·설정 오류·검증 실패(서버에 보내기 전): 2
Ctrl-C 는 detach — 잡은 계속 돌고 `rcm wait --job ID` / `rcm cancel ID` 를 안내한다(합류자면 자기
대기만 best-effort 로 뺀다). 도움말과 메시지는 영어(제품 규칙).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.resources
import json
import math
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor import SCHEMA_VERSION, __version__, follow, submissions
from remote_ci_monitor import apply as apply_mod
from remote_ci_monitor.client import (
    MAX_CHAIN_STEPS,
    Client,
    ClientError,
    default_label,
    make_snapshot,
    preset_from_json,
    upload_cached,
    wait_for_chain,
    wait_for_job,
)
from remote_ci_monitor.config import (
    RELEASE_REQUIRED_ROLES,
    RELEASE_ROLES,
    ConfigError,
    ReleaseProfile,
    ServerConfig,
    load_client_config,
    load_server_config,
    server_base_url,
    user_config_dir,
)
from remote_ci_monitor.core import artifacts as art_core
from remote_ci_monitor.core import shelf as shelf_rules
from remote_ci_monitor.core.artifacts import BundleFile
from remote_ci_monitor.core.gitref import validate_ref
from remote_ci_monitor.core.inputs import InputError, parse_kv, validate_inputs
from remote_ci_monitor.core.joblink import LinkError, parse_link
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    EXIT_CODE_BY_STATE,
    EXIT_UNKNOWN,
    PLACEMENT_CAPABLE,
    RETRIED,
    TERMINAL_STATES,
    Preset,
)
from remote_ci_monitor.core.progress import ACTION_CODE_RE, clean_line
from remote_ci_monitor.core.reload import reload_summary
from remote_ci_monitor.core.render_text import (
    CAUSE_NOT_FOUND,
    DASH,
    MAX_IDENT,
    blocked_notes_text,
    failure_lines,
    fingerprint_summary,
    fmt_clock,
    fmt_duration,
    placement_reason_text,
    placement_suffix,
    presets_summary,
    probe_token,
    ref_ident,
    render_gc,
    shelf_line,
    shelf_none_text,
    shelf_row,
    source_ident,
    storage_row,
    verdict_text,
)
from remote_ci_monitor.core.render_text import _bytes as _fmt_bytes
from remote_ci_monitor.core.render_text import _coarse as _fmt_age
from remote_ci_monitor.core.status import parse_iso
from remote_ci_monitor.mdns import discover
from remote_ci_monitor.release_secrets import secrets_dir

USAGE_EXIT = 2
#: 「사람이 할 일」 표식에서 돌아왔다 — 잡은 계속 돈다(M5s 결정 234). `rcm wait
#: --return-on-action` 을 줄 때만 나온다. 실패도 「모른다」도 아니다.
EXIT_ACTION = 4
#: 전달 실패 전용 종료 코드 — 실행 결과(`wait_exit_code`)는 건드리지 않는다(M5e §12).
EXIT_DELIVERY = 5

#: 받을 것이 **설계상** 없었던 상태. 「없는 것을 못 받은 것」은 전달 실패가 아니다
#: (m5e 시나리오 c #12). 나머지 비-ready 는 전부 전달 실패다 — 물어봐 놓고 빈 손인데
#: 초록을 내면, 「검사가 대상을 놓친 상태」와 「문제가 없는 상태」가 같은 색이 된다.
#: ⚠️ 여기 없는 상태는 **자동으로 전달 실패**다. 새 상태가 조용히 0 으로 새지 않게 하려는
#: 쪽 열림이다(fail-closed) — `tests/test_cli_m5e.py::EXIT_BY_STATE` 가 어휘 전체를 잠근다.
NOTHING_TO_DELIVER_BY_DESIGN = frozenset({art_core.DISABLED, art_core.EMPTY})


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
    if state == "scheduled":
        parts.append(_scheduled_for(job))
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
        sub = prog.get("sub")
        if isinstance(sub, dict) and isinstance(sub.get("done"), int) and sub.get("total"):
            # 스텝 안의 세부 진행 — 스크립트가 찍은 분모 그대로, 단위와 상태를 붙인다
            parts.append(f"{sub['done']}/{sub['total']} {sub.get('unit')} {sub.get('state')}")
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


def _scheduled_for(job: dict[str, Any]) -> str:
    """`scheduled for 15:00 · start by 15:15` — 예약 잡의 시각(M5s). 모르는 값은 —.

    이 머신의 시간대로 그린다. 오늘이 아니면 날짜를 붙인다(`Nov 24 · 15:00`)."""
    tz = datetime.now().astimezone().tzinfo
    now = datetime.now(UTC)
    text = f"scheduled for {fmt_clock(job.get('not_before'), tz, now=now)}"
    if job.get("start_deadline"):
        text += f" · start by {fmt_clock(job.get('start_deadline'), tz, now=now)}"
    return text


_DURATION_PART = re.compile(r"(\d+)([smhd])")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    """`rcm run --in` 의 길이 → 초. `90s` · `5m` · `2h` · `1d` · `1h30m` · 단위 없는 숫자는 초.

    0 과 음수, 소수, 공백은 받지 않는다. 길이일 뿐이라 시간대가 없다 — 서버가 제출을 받은 시각에
    더한다(M5s 결정 207). 이 머신의 시계로 절대 시각을 만들지 않는다."""
    raw = text.strip() if isinstance(text, str) else ""
    if raw.isdigit():
        total = int(raw)
    else:
        parts = _DURATION_PART.findall(raw)
        if not parts or "".join(n + u for n, u in parts) != raw:
            raise ValueError(f"{text!r} is not a duration (use 90s, 5m, 2h, 1h30m)")
        total = sum(int(n) * _DURATION_SECONDS[u] for n, u in parts)
    if total < 1:
        raise ValueError(f"{text!r} is not a positive duration")
    return total


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
    submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`--no-wait` 의 stdout JSON.

    조회가 안 됐으면 순번 칸을 **아예 넣지 않는다** — null 은 「순번이 없다」는 뜻이라 다르다.
    `submission`(M5j G5 · `{id, cancel_token}`)은 제출 응답이 준 그대로 `url` 앞에 — 래퍼가
    나중에 `rcm cancel --cancel-token` 으로 쓴다. 옛 서버가 안 주면 키도 없다.
    """
    body: dict[str, Any] = {"job_id": job_id, "joined": joined, "state": state}
    if view is not None:
        body.update({k: view.get(k) for k in NO_WAIT_KEYS})
    body.update(extra or {})
    if submission is not None:
        body["submission"] = submission
    body["url"] = url
    return body


def _keep_submission(client: Client, job_id: int, resp: dict[str, Any]) -> str | None:
    """제출 응답의 `submission` 을 상태 파일에 둔다(M5j G5) — 나중의 `rcm cancel N` 이 쓴다.

    돌려주는 값은 cancel token: 합류자의 Ctrl-C 가 자기 참여만 빼는 데 쓴다. 옛 서버(키 없음)면
    None. 못 두면 stderr 한 줄(경로 없이 — M5l S10) — 잡은 이미 큐에 있고, 비밀은 `--no-wait`
    JSON 이 아니면 어디에도 찍지 않는다. 상한을 넘으면 이 서버의 끝난 잡만 서버에 물어 버린다
    (M5l S9); 다른 서버의 항목은 모르니 남긴다.
    """
    sub = resp.get("submission")
    if not isinstance(sub, dict):
        return None
    sid, tok = sub.get("id"), sub.get("cancel_token")
    if not isinstance(sid, str) or not isinstance(tok, str) or not tok:
        return None
    role = submissions.ROLE_LEAVE_SUBMISSION if resp.get("joined") else submissions.ROLE_CANCEL_JOB
    mine = client.server.rstrip("/")

    def finished(server: str, other: int) -> bool | None:
        if server.rstrip("/") != mine:
            return None
        try:
            # `retried` 는 끝이 아니다(M5s) — 그 비밀이 사슬의 머리를 멈춘다(capability 는 뿌리에).
            # 뿌리는 끝까지 `retried` 이므로 `retried_as` 를 따라가 머리의 상태로 판정한다
            doc = client.job(other, timeout=5)
            for _ in range(MAX_CHAIN_STEPS):
                nxt = doc.get("retried_as")
                if doc.get("state") != RETRIED or not isinstance(nxt, int):
                    break
                doc = client.job(nxt, timeout=5)
            state = doc.get("state")
            return state in TERMINAL_STATES and state != RETRIED
        except ClientError as e:
            return True if e.status == 404 else None  # 지워진 잡의 비밀은 쓸 데가 없다

    try:
        submissions.remember(
            client.server,
            job_id,
            sid,
            tok,
            role=role,
            token_fingerprint=submissions.fingerprint(client.token),
            finished=finished,
        )
    except OSError as e:
        _err(
            f"warning: the cancel token was not saved in the rcm state file "
            f"({e.strerror or e}) — from another shell, `rcm cancel {job_id}` needs "
            "--cancel-token (the --no-wait JSON carries it)"
        )
    return tok


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


#: 한 프로세스 안의 발견 결과(M5q §14 A질문12). 따라가기가 먼저 찾았으면 명령이 1.5초 발견을
#: 다시 하지 않고 `server: found` 줄도 한 번만 쓴다. 찾은 서버가 없다는 결과도 기억한다.
#: `_main` 이 부를 때마다 비운다.
_DISCOVERY: list[Any] | None = None
_FOUND_SAID = False


def _discover_once(timeout: float) -> list[Any]:
    global _DISCOVERY
    if _DISCOVERY is None:
        _DISCOVERY = list(discover(timeout=timeout))
    return _DISCOVERY


def _resolve_server(cfg: Any, *, timeout: float = 1.5) -> Any:
    """서버 주소가 없으면 같은 네트워크에서 찾는다(M5c). 정확히 하나면 그것, 아니면 `ConfigError`.
    찾은 서버는 `cfg.discovered` 에 남는다(`rcm check` 표시용)."""
    global _FOUND_SAID
    cfg.discovered = None
    if not cfg.wants_discovery:
        return cfg
    found = _discover_once(timeout)
    if len(found) == 1:
        cfg.server = found[0].address
        cfg.discovered = found[0]
        ip = found[0].ips[0] if found[0].ips else found[0].host.rstrip(".")
        if not _FOUND_SAID:
            _FOUND_SAID = True
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


# ── 클라이언트 버전 (M5i I8-3 · 결정 83) ─────────────────────────────────────


_PEP440_RE = re.compile(
    r"v?(\d+(?:\.\d+)*)"
    r"(?:[-_.]?(a|b|c|rc|alpha|beta)[-_.]?(\d*))?"
    r"(?:[-_.]?(post|rev|r)[-_.]?(\d*))?"
    r"(?:[-_.]?(dev)[-_.]?(\d*))?",
    re.IGNORECASE,
)
_PRE_PHASE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2}


def _version_key(text: str) -> tuple[Any, ...]:
    """크기 비교용 키 — PEP 440 의 dev · a · b · rc · post 를 아는 작은 해석(M5q §14 C질문5).

    `0.7.0.dev0 < 0.7.0.dev1 < 0.7.0a1 < 0.7.0rc1 < 0.7.0 < 0.7.0.post1 < 0.7.1`. `+local` 꼬리는
    빼고 릴리스 끝의 0 은 무시한다(`0.7` 과 `0.7.0` 은 같은 크기). 읽지 못한 꼬리는 오늘처럼 숫자
    마디만 본다. 「same as server」는 이 키가 아니라 문자열이 같을 때만 쓴다."""
    s = str(text).strip().split("+", 1)[0]
    m = _PEP440_RE.match(s)
    if m is None:
        return ((), 3, 0, -1, math.inf)
    release = tuple(int(x) for x in m.group(1).split("."))
    while release and release[-1] == 0:
        release = release[:-1]
    if m.end() != len(s):
        return (release, 3, 0, -1, math.inf)
    pre, dev, post = m.group(2), m.group(6), m.group(4)
    post_n = int(m.group(5) or 0) if post else -1
    dev_n = int(m.group(7) or 0) if dev else math.inf
    if pre:
        return (release, _PRE_PHASE[pre.lower()], int(m.group(3) or 0), post_n, dev_n)
    # dev 만 붙은 릴리스는 모든 프리릴리스보다 앞이다
    phase = -1 if dev and not post else 3
    return (release, phase, 0, post_n, dev_n)


def _upgrade_hint(server: str, h: dict[str, Any]) -> str:
    """올리는 방법 한 토막. 서버가 wheel 경로를 주면 그 `pip install`, 아니면 없다고 말한다 —
    있지도 않은 URL 을 지어내지 않는다(옛 서버는 `/client/` 가 없고, 조립 실패면 503 이다)."""
    cw = h.get("client_wheel") or {}
    if isinstance(cw, dict) and cw.get("path"):
        # 서버가 보낸 문자열 — 제어 문자를 지운다(M5q §14.5). 경로는 64자로 자르지 않는다
        return f"pip install {server}{follow.clean(cw['path'], 512)}"
    reason = follow.clean(h.get("client_wheel_error") or "server too old to serve one")
    return f"no client wheel from this server ({reason})"


def _setting_where(setting: Any) -> str:
    """설정이 온 자리 — `RCM_CLIENT_VERSION` 이나 `~` 로 줄인 한 줄 파일 경로."""
    if setting.source == "file":
        return follow.tilde(setting.path)
    return follow.ENV_MODE


def _follow_tail(state: Any) -> tuple[str | None, bool]:
    """`client` 행 꼬리와 `pip install` 힌트를 붙여도 되는지(M5q §14.8 의 표).

    힌트는 고정일 때와 `no_wheel` · `old_server` 일 때 뺀다 — 고정은 사람이 고른 상태이고, 두
    코드는 오늘의 힌트와 같은 이유를 꼬리가 이미 말한다."""
    if state is None:
        return None, True
    s = state.setting
    pinned = s.mode == follow.MODE_PIN
    if state.followed and not pinned:
        return f"following (installed v{state.installed})", True
    if state.warn:
        hint = not pinned and state.warn not in ("no_wheel", "old_server")
        return f"not following: {state.warn_text}", hint
    if pinned:
        return f"pinned by {_setting_where(s)}", False
    if s.mode == follow.MODE_INSTALLED:
        if s.source == "flag":
            return "not following (--no-follow)", True
        if s.source == "env":
            return f"not following ({follow.ENV_MODE}=installed)", True
        if s.source == "file":
            return f"not following ({follow.tilde(s.path)})", True
        return f"not following ({state.install} install)", True
    return None, True


def _client_row(
    client: Client, h: dict[str, Any], state: Any = None
) -> tuple[str, bool | None, str] | None:
    """`rcm check` 의 `client` 행 — 이 클라이언트와 서버의 버전. 서버가 버전을 안 주면 행도 없다.

    FAIL 은 `min_client_version` 아래일 때뿐이고 그것을 **먼저** 본다(M5l S10) — 서버가 취소에
    capability 를 요구하면 같은 버전 번호라도 바닥이 그 위일 수 있다(0.2.6 서버가 0.2.7 을
    요구). 그 위의 「older」·「newer」는 warn(알려는 주되 실패는 아니다) — 실제 거부는 서버의
    400·403 이 한다.

    `state` 는 이 프로세스의 따라가기 결과(M5q §2.9)다. 있으면 관계 뒤에 꼬리를 붙인다. 인자
    목록으로 부른 `main(argv)` 는 따라가지 않으니 None 이고 오늘과 같은 글자다. 「same as
    server」는 문자열이 같을 때만이다 — 크기만 같으면(`0.7` 과 `0.7.0`) `different build`.
    """
    raw = h.get("version")
    if not raw:
        return None
    # 서버가 보낸 문자열은 제어 문자를 지우고 64자로 자른 뒤에 보인다(M5q §14.5)
    server_v = follow.clean(raw)
    mine, theirs = _version_key(__version__), _version_key(server_v)
    tail, hint_ok = _follow_tail(state)
    t = f" · {tail}" if tail else ""

    def hint() -> str:
        return f" — {_upgrade_hint(client.server, h)}" if hint_ok else ""

    floor = h.get("min_client_version")
    if floor and mine < _version_key(floor):
        why = "older" if mine < theirs else f"below min client v{floor}"
        return ("client", False, f"v{__version__} · server v{server_v} · {why}{t}{hint()}")
    if str(raw) == __version__:
        return ("client", True, f"v{__version__} · same as server{t}")
    if mine == theirs:
        return ("client", None, f"v{__version__} · server v{server_v} · different build{t}")
    if mine < theirs:
        return ("client", None, f"v{__version__} · server v{server_v} · older{t}{hint()}")
    return ("client", None, f"v{__version__} · server v{server_v} · newer{t}")


def _cancel_row(h: dict[str, Any]) -> tuple[str, bool | None, str] | None:
    """`rcm check` 의 `cancel` 행 — 서버가 취소에 제출 capability 를 요구하면(M5j G5) 그 사실과
    그것을 보낼 줄 아는 최소 클라이언트 버전을 말한다. 키가 꺼진(또는 옛) 서버면 행이 없다.

    버전을 비교하지 않는 이유: 이 코드가 도는 클라이언트는 이미 비밀을 보낸다 — 행은 **옛
    클라이언트를 쓰는 다른 세션**에게 알려 줄 숫자를 옮기는 것이지 자기 판정이 아니다. 옛
    클라이언트 자신은 이 키를 모르니 행도 못 그리고, 실제 거부는 서버의 403 이 한다.
    """
    floor = h.get("cancel_min_client_version")
    if not floor:
        return None
    return (
        "cancel",
        True,
        f"needs the submission's cancel token · min client v{floor} (this client sends one)",
    )


def _supersede_row(h: dict[str, Any]) -> tuple[str, bool | None, str] | None:
    """`rcm check` 의 `supersede` 행 — 프리셋이 supersede 를 켰는데 서버가
    `cancel_requires_submission_token` 때문에 건너뛰면 warn(실패 아님, M5o §10). 아니면 없다."""
    names = h.get("supersede_skipped_presets")
    if not isinstance(names, list) or not names:
        return None
    shown = ", ".join(str(n) for n in names)
    return (
        "supersede",
        None,
        f"skipped for {shown} — cancel_requires_submission_token is on "
        '(set supersede = "off" or turn the token rule off)',
    )


def _warn_if_client_too_old(client: Client, h: dict[str, Any] | None = None) -> None:
    """`rcm run` 의 stderr 한 줄 — 막지 않는다. health 를 못 읽으면 조용히 지나간다.

    `h` 를 주면 그 문서를 쓴다 — `--keep` 의 감지가 이미 읽었으면 health 를 두 번 부르지 않는다."""
    if h is None:
        try:
            h = client.health()
        except ClientError:
            return
    if not isinstance(h, dict):
        return
    floor = h.get("min_client_version")
    if floor and _version_key(__version__) < _version_key(floor):
        _err(
            f"warning: this client v{__version__} is older than the server accepts "
            f"(min_client_version {floor}) — {_upgrade_hint(client.server, h)}"
        )


# ── 보관함 (M5p) ─────────────────────────────────────────────────────────────

#: 잡이 끝난 뒤 보관이 끝나기를 기다리는 상한(결정 143)과 그 사이 다시 보는 간격(초).
SHELF_WAIT_SECONDS = 60.0
SHELF_POLL_SECONDS = 1.0
#: 보관이 아직 안 끝난 공개 상태 — 기다리는 동안 다시 본다(§15.1 · §15.6).
SHELF_IN_FLIGHT = ("waiting", "pending", "copying")
#: 보관함을 아는 서버의 첫 판(옛 서버 안내에 쓴다).
SHELF_SINCE = "rcm ≥ 0.7.0"


def _schedule_refusal(h: Any) -> str | None:
    """`--at` · `--in` 을 보내면 안 되는 서버인가(M5s 결정 206). 옛 서버는 모르는 `start_at` 을
    **무시하고 바로 돌린다** — 그래서 health 에 `schedule` 키가 있을 때만 보낸다(`_keep_refusal`
    과 같은 방식)."""
    if not isinstance(h, dict) or not h.get("schedule"):
        return "server does not support scheduling — upgrade it"
    return None


def _keep_refusal(h: Any) -> str | None:
    """`--keep` 을 보내면 안 되는 서버인가 — 그렇다면 그 까닭.

    옛 서버는 모르는 본문 키를 거절하지 않고 **무시한다**(검토 P0-3). 그러면 세션은 링크를
    기다리는데 아무것도 보관되지 않는다. 그래서 health 에 `shelf` **키가 있을 때만** 보낸다(값이
    null 이어도 키가 있으면 안다 — CG4). 꺼진 보관함(`enabled: false`)도 미리 거절한다(§15.6).
    """
    if not isinstance(h, dict) or "shelf" not in h:
        return f"server does not support --keep ({SHELF_SINCE})"
    summary = h.get("shelf")
    if isinstance(summary, dict) and summary.get("enabled") is False:
        return "shelf is off on this server"
    return None


def _shelf_health(client: Client, what: str, command: str) -> tuple[int | None, dict[str, Any]]:
    """`rcm keep` · `rcm shelf` 의 앞 단계 — 서버가 보관함을 아는가. (종료 코드 또는 None, health).

    옛 서버에는 경로가 없어 404 가 난다. 그것을 「없는 잡」으로 말하지 않으려고 먼저 묻는다(CG17).
    """
    try:
        h = client.health()
    except ClientError as e:
        return _client_fail(client, what, e), {}
    if not isinstance(h, dict) or "shelf" not in h:
        return _usage(f"{what}: server does not support {command} ({SHELF_SINCE})"), {}
    return None, h


def _server_tz(client: Client) -> Any:
    """서버의 `[display] timezone` — 날짜는 페이지와 같은 날로 보인다(§15.6). 못 읽으면 이 머신."""
    try:
        name = (client.status() or {}).get("display_timezone")
    except (ClientError, ValueError, OSError):
        name = None
    return _tz_named(name)


def _tz_named(name: Any) -> Any:
    """시간대 이름 → tzinfo. 비었거나 틀리면 이 머신의 시간대(서버가 「로컬」이라고 한 것)."""
    from zoneinfo import ZoneInfo

    if name:
        try:
            return ZoneInfo(str(name))
        except Exception:  # noqa: BLE001 — 이름이 틀려도 날짜는 그린다(이 머신 시간대로)
            pass
    return _local_tz()


def _shelf_date(value: Any, tz: Any) -> str:
    """ISO 문자열이나 epoch 초 → 그 시간대의 `YYYY-MM-DD`. 모르면 대시."""
    at: datetime | None = None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            at = datetime.fromtimestamp(float(value), UTC)
        elif isinstance(value, str):
            at = parse_iso(value)
    except (ValueError, OverflowError, OSError):
        at = None
    if at is None:
        return DASH
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(tz).strftime("%Y-%m-%d")


def _await_shelf(client: Client, job_id: int, shelf: Any) -> dict[str, Any] | None:
    """잡이 끝난 뒤 보관이 끝나기를 `SHELF_WAIT_SECONDS` 까지 기다린다. 마지막으로 본 `shelf`.

    표시용이다 — 조회가 깨지면 다음 차례에 다시 보고, 끝내 못 보면 마지막 값으로 말한다. Ctrl-C 는
    기다림만 그만둔다(잡은 이미 끝났다 — 종료 코드는 잡의 것, CG3).
    """
    current = shelf if isinstance(shelf, dict) else None
    deadline = time.monotonic() + SHELF_WAIT_SECONDS
    try:
        while current is not None and current.get("state") in SHELF_IN_FLIGHT:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(SHELF_POLL_SECONDS, left))
            try:
                doc = client.job(job_id, timeout=NO_WAIT_VIEW_TIMEOUT)
            except (ClientError, ValueError, OSError):
                continue
            nxt = doc.get("shelf") if isinstance(doc, dict) else None
            if isinstance(nxt, dict):
                current = nxt
    except KeyboardInterrupt:
        pass
    return current


def _shelf_url_of(doc: Any) -> str | None:
    shelf = (doc or {}).get("shelf") if isinstance(doc, dict) else None
    url = shelf.get("url") if isinstance(shelf, dict) else None
    return url if isinstance(url, str) and url else None


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
    # 잡 링크도 서버와 같은 규칙으로 먼저 거른다 — 큰 트리를 다 싼 뒤에 400 을 보지 않게
    link = getattr(args, "link", None)
    link_label = getattr(args, "link_label", None)
    if link_label is not None and link is None:
        return _usage("--link-label needs --link URL")
    if link is not None:
        try:
            parse_link({"url": link, "label": link_label})
        except LinkError as e:
            return _usage(f"--link: {e}")
    # ① 서버 스키마로 검증 — 실패면 보내지 않는다
    try:
        preset_docs = {p.get("name"): p for p in client.preset_docs()}
        presets = {name: preset_from_json(p) for name, p in preset_docs.items()}
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
    # 예약(M5s §4.1). `--at` 은 글자 그대로 서버에 맡기고(서버가 자기 시간대로 읽는다) `--in` 은
    # 길이라서 여기서 초로 바꾼다 — 어느 쪽도 이 머신의 시계로 절대 시각을 만들지 않는다
    schedule: dict[str, Any] = {}
    if getattr(args, "at", None) is not None:
        schedule["start_at"] = args.at
    if getattr(args, "start_in", None) is not None:
        try:
            schedule["start_in_seconds"] = parse_duration(args.start_in)
        except ValueError as e:
            return _usage(f"--in: {e}")
    keep = bool(getattr(args, "keep", False))
    # 글롭이 없는 프리셋은 서버가 400 `keep_without_artifacts` 로 거절한다 — 큰 트리를 다 싼 뒤에
    # 그 말을 듣지 않게 여기서 거른다(§15.6). 키가 없는 서버는 모르는 것이니 서버에 맡긴다
    if keep and preset_docs.get(preset.name, {}).get("has_artifacts") is False:
        return _usage(
            f"run --keep: preset '{preset.name}' has no artifacts to keep — nothing was sent"
        )
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
    # 사용 오류는 여기까지 — 이 아래부터 서버에 더 묻는다(health 는 경고용, 토큰은 제출 전 확인)
    # health 는 **한 번만** 읽는다 — 버전 경고와 `--keep` 의 감지가 같은 문서를 본다(CG6)
    try:
        health: dict[str, Any] | None = client.health()
    except ClientError as e:
        if keep:
            _err(f"run --keep: cannot reach the server ({e.message}) — nothing was sent")
            return EXIT_UNKNOWN
        if schedule:
            _err(f"run --at/--in: cannot reach the server ({e.message}) — nothing was sent")
            return EXIT_UNKNOWN
        health = None
    if health is not None:
        _warn_if_client_too_old(client, health)
    if keep:
        refusal = _keep_refusal(health)
        if refusal is not None:
            return _usage(f"run --keep: {refusal} — nothing was sent")
    if schedule:
        refusal = _schedule_refusal(health)
        if refusal is not None:
            return _usage(f"run --at/--in: {refusal} — nothing was sent")
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
        return _run_git_ref(
            client, args, preset, inputs, ref or "", label, pool, link, link_label, schedule
        )
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
                link=link,
                link_label=link_label,
                machine=getattr(args, "machine", None),
                supersede=not getattr(args, "no_supersede", False),
                keep=keep,
                **schedule,
            )
        except ClientError as e:
            _err(f"submit failed: {e.message}")
            refused = e.status in _submit_refused(args) or _refused_by_code(e)
            return USAGE_EXIT if refused else EXIT_UNKNOWN
        job_id = int(resp["job_id"])
        _no_capable_line(preset.name, resp)
        joined = bool(resp.get("joined"))
        cancel_token = _keep_submission(client, job_id, resp)
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
                # 업로드 중에 같은 브랜치의 새 잡에 밀렸으면 그것이 답이다(M5o A22) — 「upload
                # failed」 로 3 을 내면 세션은 무슨 일인지 모른다. 409 본문이 말해 주므로 다시
                # 조회하지 않는다(업로드 실패 자리에는 표시용 조회가 없다는 약속).
                gone = {"state": e.body.get("state"), "superseded_by": e.body.get("superseded_by")}
                note = superseded_line(gone, job_id) if e.status == 409 else None
                if note:
                    _err(note)
                    _print_json({"job_id": job_id, **gone, "wait_exit_code": 2})
                    return EXIT_CODE_BY_STATE[CANCELLED]
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
        shelf_extra = _no_wait_shelf(resp)
        _print_json(
            _no_wait_json(
                job_id,
                view,
                joined=joined,
                state=state,
                url=url,
                extra=shelf_extra,
                submission=resp.get("submission"),
            )
        )
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
        client,
        job_id,
        timeout=args.timeout,
        joined=joined,
        use_sse=not args.poll,
        fetch=spec,
        cancel_token=cancel_token,
        shelf_url=_submitted_shelf_url(resp),
    )


def _submit_refused(args: argparse.Namespace) -> tuple[int, ...]:
    """잡이 안 생긴 것이 확실한 제출 거절 — usage 2. `--machine` 이면 404(없는 머신)·409(down)도
    그렇다(가정 C11): 결과를 모르는 3 이 아니다."""
    base = (400, 401, 403, 413, 0)
    return (*base, 404, 409) if getattr(args, "machine", None) is not None else base


#: 잡을 만들지 않은 것이 확실한 409 코드 — 상태 코드만으로는 모르니(3) 코드로 2 를 고른다.
#: `release_route_required` 는 golive 출시를 `POST /jobs` 로 낸 것이다(M5s 결정 235).
#: `schedule_exists` 는 같은 예약이 이미 있다(결정 209). 서버가 새 잡을 만들지 않았다고 말한
#: 것이다(H 실기). `golive_running`(결정 232)은 `rcm run` 에는 나오지 않지만(`POST /jobs` 가
#: 먼저 `release_route_required` 로 막는다) 같은 뜻의 거절이라 둔다.
_REFUSED_CODES = ("release_route_required", "schedule_exists", "golive_running")
#: `no_capable_machine` · `tool_missing` 은 `local_only` 제출을 받을 로컬 레인이 없다는 거절이다
#: (M5t-a 결정 254). 서버가 합류 · supersede 와 잡 생성 전에 거절하니 `--machine` 이 없어도 2 다.
#: 위 줄과 나눠 둔 것은 그 줄을 잡는 변이(`m5s-cli-schedule-exists-is-unknown`)를 지키려는 것이다.
_REFUSED_CODES += ("no_capable_machine", "tool_missing")


def _refused_by_code(e: ClientError) -> bool:
    if not isinstance(e.body, dict):
        return False
    return e.body.get("code") in _REFUSED_CODES or e.body.get("error_code") in _REFUSED_CODES


def _run_git_ref(
    client: Client,
    args: argparse.Namespace,
    preset: Preset,
    inputs: dict[str, Any],
    ref: str,
    label: str,
    pool: str | None = None,
    link: str | None = None,
    link_label: str | None = None,
    schedule: dict[str, Any] | None = None,
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
            link=link,
            link_label=link_label,
            machine=getattr(args, "machine", None),
            supersede=not getattr(args, "no_supersede", False),
            keep=bool(getattr(args, "keep", False)),
            **(schedule or {}),
        )
    except ClientError as e:
        _err(f"submit failed: {e.message}")
        # 502 「cannot resolve」 는 ref 가 틀렸다는 확정 거절(잡 없음) — 400 과 같은 usage 2.
        # 504 (해석 타임아웃) · 503 · 연결 오류는 결과를 모르는 것이라 3.
        refused = e.status in (*_submit_refused(args), 502) or _refused_by_code(e)
        return USAGE_EXIT if refused else EXIT_UNKNOWN
    job_id = int(resp["job_id"])
    _no_capable_line(preset.name, resp)
    joined = bool(resp.get("joined"))
    cancel_token = _keep_submission(client, job_id, resp)
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
            else f"({preset.name} · {ref_ident(ref, sha)})"  # `·` 를 품는다 — 목록 항목과 안 섞이게
        )
        _info(_submitted_line(job_id, view, joined=joined, state=state, url=url, detail=detail))
        _print_json(
            _no_wait_json(
                job_id,
                view,
                joined=joined,
                state=state,
                url=url,
                extra={"ref": ref, "sha": sha, **_no_wait_shelf(resp)},
                submission=resp.get("submission"),
            )
        )
        return 0
    if joined:
        _info(f"joined job #{job_id} ({state}) — same preset, inputs, commit {short}")
    else:
        # ref 가 곧 sha 면 한 번만 — 목록 칸과 같은 규칙(M5i I2)
        _info(f"submitted job #{job_id} ({preset.name} · {ref_ident(ref, sha)}) · {url or ''}")
    # ⑤ wait — git_ref 잡도 산출물을 회수한다. 제출한 트리가 없으니 `--output DIR` 이 그 자리다
    #   (사용법은 `cmd_run` 이 이미 검사했다 — git_ref 면 `--output` 필수 · `--no-wait` 와 못 쓴다).
    #   기준선은 **빈 사전**이다 — 대조할 제출 트리가 없으므로 이미 있는 파일은 `--force` 여야
    #   덮는다. `cmd_artifacts` 와 같은 규칙이다.
    # ⚠️ 한때 이 자리가 `fetch=` 를 안 넘겼다. 사용법 검사는 두 단계나 통과시켜 놓고 정작
    #   회수를 건너뛰어서 **잡 초록 · 종료코드 0 · 받은 파일 0** 이 됐다 — 빈 손이 통과로 보였고
    #   아무 신호도 없었다. 산출물을 선언한 프리셋이 전부 git_ref 인 배치에서는 회수가 통째로
    #   죽어 있던 셈이다. 사용법만 맞고 동작이 없는 조합을 남기지 마라.
    spec = None
    if bool(getattr(args, "fetch_artifacts", False)):
        out_root = Path(args.output)
        out_root.mkdir(parents=True, exist_ok=True)
        spec = _FetchSpec(
            root=out_root,
            baseline={},
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    return _wait(
        client,
        job_id,
        timeout=args.timeout,
        joined=joined,
        use_sse=not args.poll,
        fetch=spec,
        cancel_token=cancel_token,
        shelf_url=_submitted_shelf_url(resp),
    )


def _submitted_shelf_url(resp: dict[str, Any]) -> str | None:
    """제출 응답의 보관함 링크(keep 인 잡에만 있다). 없으면 None — 링크를 지어내지 않는다."""
    url = resp.get("shelf_url")
    return url if isinstance(url, str) and url else None


def _no_wait_shelf(resp: dict[str, Any]) -> dict[str, Any]:
    """`--no-wait` 의 보관 줄과 JSON 칸. keep 이 아닌 잡은 줄도 키도 없다(null 도 아니다, CG13)."""
    url = _submitted_shelf_url(resp)
    if url is None:
        return {}
    _info(f"shelf: {url} (after the job finishes)")
    return {"shelf_url": url}


@dataclass(frozen=True)
class _FetchSpec:
    """`--fetch-artifacts` 가 정해 둔 것. 기준선은 **제출할 때 실제로 보낸** 내용의 해시다(§11)."""

    root: Path
    baseline: dict[str, str]
    force: bool = False
    dry_run: bool = False


def action_line(job_id: Any, action: Any) -> str | None:
    """`#N needs action · <code> · <note>` — 「사람이 할 일」 표식 한 줄(M5s). 표식이 없으면 None.

    code 는 서버가 어휘로 거른 값이지만 여기서도 어휘 밖이면 말하지 않는다 — 터미널에 그대로
    흐르는 문자열이다. note 는 서버가 이미 지웠지만(`progress.parse_action_value`) 옛 서버나 가짜
    서버의 문서일 수 있어 같은 `clean_line` 을 한 번 더 지난다."""
    if not isinstance(action, dict):
        return None
    code = action.get("code")
    if not isinstance(code, str) or not ACTION_CODE_RE.fullmatch(code):
        return None
    note = clean_line(action.get("note")) if isinstance(action.get("note"), str) else ""
    tail = f" · {note}" if note else ""
    return f"#{job_id} needs action · {code}{tail}"


class _ActionWatch:
    """기다리는 동안 본 「사람이 할 일」 표식. 새로 본 표식마다 stderr 한 줄, 플래그가 있으면 4.

    기다리기 시작할 때 **이미 있던** 표식에서도 4 로 돌아온다. `rcm run --no-wait` 뒤에 붙은
    세션이나 다른 컴퓨터의 세션은 표식이 찍힌 뒤에 붙는 것이 보통이다 — 그 표식을 「옛것」으로
    건너뛰면 사람에게 알릴 순간을 통째로 놓친다(S25 가 그 장면이다). 그래서 사람이 손을 쓴 뒤
    다시 기다릴 때는 플래그 없이 `rcm wait --job N` 이다 — 표식은 그 잡의 마지막 표식으로 남아
    있으므로 플래그를 다시 주면 곧바로 또 돌아온다.

    취소 중이거나 끝난 잡(성공 · 실패 · 시간 초과 · 취소 · 유실 · 만료 …)의 표식은 알리지도,
    4 로 돌아오지도 않는다. 그 잡은 더 돌지 않으므로 「잡은 계속 돈다」는 4 의 약속이 거짓이
    되고, 사람에게 할 일을 시켜도 받을 잡이 없다. 표식은 최종 JSON 의 `action` 에 그대로 남는다.
    """

    def __init__(self, job_id: int, *, return_on_action: bool) -> None:
        self.job_id = job_id
        self.return_on_action = return_on_action
        self.printed: tuple[Any, ...] | None = None

    @staticmethod
    def _ident(action: Any) -> tuple[Any, ...] | None:
        if not isinstance(action, dict) or not action.get("code"):
            return None
        return (action.get("code"), action.get("note"), action.get("at"))

    @staticmethod
    def _stopped(job: dict[str, Any]) -> bool:
        state = job.get("state")
        return state in TERMINAL_STATES or state == CANCELLING

    def observe(self, job: dict[str, Any]) -> str | None:
        """잡 문서 하나를 본다. 알릴 줄이 있으면 돌려준다(같은 표식은 한 번만)."""
        if self._stopped(job):
            return None
        ident = self._ident(job.get("action"))
        if ident is None or ident == self.printed:
            return None
        self.printed = ident
        return action_line(job.get("id", self.job_id), job.get("action"))

    def stop_when(self, job: dict[str, Any]) -> int | None:
        if not self.return_on_action or self._stopped(job):
            return None
        if action_line(self.job_id, job.get("action")) is None:
            return None
        return EXIT_ACTION


def _wait(
    client: Client,
    job_id: int,
    *,
    timeout: float | None,
    joined: bool,
    use_sse: bool = True,
    fetch: _FetchSpec | None = None,
    cancel_token: str | None = None,
    shelf_url: str | None = None,
    return_on_action: bool = False,
) -> int:
    """잡을 따라가다 종료 코드를 낸다. Ctrl-C 는 detach — 요청자는 아무것도 보내지 않고, 합류자는
    자기 `leave_submission` capability(`cancel_token`)로 합류만 뺀다(PLAN 계약 · M5j G5).

    keep 인 잡(잡 문서의 `keep`)이면 끝에 보관 줄을 찍는다(M5p 결정 143) — 받기(`--fetch-artifacts`)
    가 먼저이고 보관 줄이 마지막이다. `shelf_url` 은 제출 응답의 링크로, 잡 문서를 한 번도 못 본 채
    끝났을 때(detach · 연결 끊김) 쓴다.

    잡이 「사람이 할 일」 표식을 찍으면 stderr 에 한 줄을 남긴다(M5s). `return_on_action` 이면 새
    표식에서 4 로 돌아온다 — 잡은 계속 돈다(결정 234)."""
    line = _StatusLine()
    last: dict[str, Any] | None = None
    watch = _ActionWatch(job_id, return_on_action=return_on_action)

    def on_update(job: dict[str, Any]) -> None:
        nonlocal last
        last = job
        said = watch.observe(job)
        if said:
            line.done()  # 진행 줄을 덮어쓰지 않게 — 표식은 남아야 하는 줄이다
            _info(said)
            line.last = ""
        line.update(describe(job))

    def on_retry(job: dict[str, Any]) -> None:
        # 반복(M5s) — 한 시도가 끝나고 다음 시도를 기다린다. 남는 줄이다(진행 줄을 덮지 않는다)
        said = retry_line(job)
        if said:
            line.done()
            _info(said)
            line.last = ""

    try:
        code, job, reason, attempts = wait_for_chain(
            client,
            job_id,
            timeout=timeout,
            on_update=on_update,
            use_sse=use_sse,
            on_info=line.update,
            stop_when=watch.stop_when,
            on_retry=on_retry,
            wait=wait_for_job,  # 이 모듈의 이름으로 — 시험이 바꿔 끼운다
        )
    except KeyboardInterrupt:
        line.done()
        left = False
        if joined:
            try:
                resp = client.cancel(job_id, cancel_token=cancel_token)
                left = bool(resp.get("left"))
            except ClientError:
                pass
        _info(
            f"detached from job #{job_id} — it keeps running. "
            f"Resume with `rcm wait --job {job_id}`; stop it with `rcm cancel {job_id}`."
            + (" (left the join list)" if left else "")
        )
        later = _shelf_later_line(last, shelf_url)
        if later:
            _info(later)
        _print_json({**(last or {"job_id": job_id}), "detached": True, "left": left})
        return EXIT_UNKNOWN
    line.done()
    # 사슬을 따라왔으면 마지막 시도가 이 기다림의 잡이다(M5s 결정 228) — 받기 · 보관 · 로그 길이
    # 모두 그 시도를 본다. 뿌리 id 는 `chain_id` 로 남는다
    root_id = job_id
    if isinstance(job, dict) and isinstance(job.get("id"), int):
        job_id = int(job["id"])
    if reason:
        _err(reason)
    note = superseded_line(job or {}, job_id)
    if note:
        _err(note)
    # 0 이 아닌 끝에는 로그로 가는 길과 이름별 최근 이력을 붙인다(M5h §2.5). 3(모른다)에도
    # 붙인다 — 모를수록 로그가 필요하다.
    # 확정 404 만 로그 줄을 뺀다 — 없는 잡의 로그 길은 아무것도 안 가리킨다(M5i I3).
    # 표식에서 돌아온 4 는 실패가 아니다 — 로그 길 대신 다시 기다리는 길을 말한다(M5s).
    if code == EXIT_ACTION:
        _info(
            f"job #{job_id} keeps running — after the person acts, resume with "
            f"`rcm wait --job {job_id}`"
        )
    elif code != 0:
        cause = getattr(reason, "cause", None)
        for text in failure_lines(
            job or {}, job_id=job_id, url=(job or {}).get("url"), cause=cause
        ):
            _err(text)
    out = dict(job or {"job_id": job_id, "state": None})
    out.setdefault("job_id", out.get("id", job_id))  # --no-wait 출력과 같은 키로도 읽히게
    if attempts is not None:
        # 반복 사슬(M5s 결정 211) — `job_id` 는 마지막 시도, `chain_id` 는 뿌리다
        out["chain_id"] = out.get("chain_id") or root_id
        out["attempts"] = attempts
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
        out["artifact_fetch"] = delivery
        # **실행 실패가 전달 실패보다 우선한다** — 테스트가 깨진 것을 먼저 알아야 한다(§12)
        if code == 0 and not delivery.get("ok"):
            code = EXIT_DELIVERY
    # 보관 줄은 받기 **뒤** 마지막이다(§15.6). 종료 코드는 바꾸지 않는다(보관은 잡의 성패와 무관)
    shelf_note = _shelf_after_wait(
        client, job_id, job, out, shelf_url, cause=getattr(reason, "cause", None)
    )
    if shelf_note:
        _info(shelf_note)
    _print_json(out)
    return code


def _shelf_later_line(doc: Any, fallback: str | None) -> str | None:
    """잡이 아직 안 끝났는데 기다림이 끝났다(`--timeout` · detach · 연결 끊김) — 링크와 「끝나면」.

    잡 문서가 keep 이라고 하면 그 링크, 문서를 못 봤으면 제출 응답의 링크. 둘 다 없으면 말하지
    않는다(§15.6)."""
    if isinstance(doc, dict) and doc.get("keep") is False:
        return None
    url = _shelf_url_of(doc) if isinstance(doc, dict) and doc.get("keep") else None
    url = url or fallback
    return f"shelf: {url} (after the job finishes)" if url else None


def _shelf_after_wait(
    client: Client,
    job_id: int,
    job: dict[str, Any] | None,
    out: dict[str, Any],
    fallback: str | None,
    *,
    cause: str | None = None,
) -> str | None:
    """`_wait` 끝의 보관 줄. 마지막으로 본 `shelf` 를 `out` 에 싣는다(CG2).

    - 끝난 keep 잡: 보관이 끝나기를 `SHELF_WAIT_SECONDS` 까지 기다리고 상태대로 한 줄.
    - 새 잡에 밀려 취소된 잡: keep 은 새 잡으로 넘어갔다(결정 128) — `shelf: moved to #N`.
      옛 잡의 묶음 상태로 「nothing to keep」 을 말하면 틀린 말이다.
    - 아직 안 끝났다(`--timeout` · 연결 끊김): 기다리지 않고 「끝나면」 한 줄.
    - 404(없는 잡): 아무 말도 안 한다.
    """
    if cause == CAUSE_NOT_FOUND:
        return None
    doc = job if isinstance(job, dict) else None
    if doc is None or doc.get("state") not in TERMINAL_STATES:
        return _shelf_later_line(doc, fallback)
    if doc.get("keep") is not True:
        return None
    by = doc.get("superseded_by")
    if doc.get("state") == CANCELLED and isinstance(by, int) and not isinstance(by, bool):
        return f"shelf: moved to #{by}"
    shelf = _await_shelf(client, job_id, doc.get("shelf"))
    if shelf is not None:
        out["shelf"] = shelf
    if (
        isinstance(shelf, dict)
        and shelf.get("state") == "none"
        and "started_at" in doc
        and doc.get("started_at") is None
    ):
        # 시작 전에 취소된 잡 — 묶음 상태(`unknown`)보다 「돌지 않았다」가 맞는 말이다(격리 검증 D1)
        return f"shelf: nothing to keep — {shelf_none_text('never_ran')}"
    return shelf_line(shelf)


def retry_line(job: dict[str, Any]) -> str | None:
    """`#57 exit 75 · attempt 1 of 12 · next #58 at 15:10Z` — 반복 사슬을 따라갈 때 한 줄(M5s).

    서버 문서의 값이 정수가 아니면 그 조각을 빼거나 아무 말도 안 한다 — 번호를 지어내지 않는다."""
    nxt = job.get("retried_as")
    if not isinstance(nxt, int) or isinstance(nxt, bool):
        return None
    head = f"#{job.get('id', '?')}"
    if isinstance(job.get("exit_code"), int):
        head += f" exit {job['exit_code']}"
    parts = [head]
    attempt, limit = job.get("attempt"), job.get("max_attempts")
    if isinstance(attempt, int) and isinstance(limit, int):
        parts.append(f"attempt {attempt} of {limit}")
    parts.append(f"retrying as #{nxt} — waiting for it")
    return " · ".join(parts)


def superseded_line(job: dict[str, Any], job_id: int) -> str | None:
    """`#O superseded by #N — rcm wait --job N` — 새 잡에 밀려 취소된 잡의 안내 한 줄(M5o 결정 114).

    따라가지 않는다: 종료 코드는 O 의 것(2)이고, N 을 기다릴지는 부른 쪽이 정한다. 서버 문서의
    값이 정수가 아니면 아무 말도 안 한다 — 번호를 지어내지 않는다.
    """
    by = job.get("superseded_by")
    if job.get("state") != CANCELLED or not isinstance(by, int) or isinstance(by, bool):
        return None
    return f"#{job_id} superseded by #{by} — rcm wait --job {by}"


def cmd_wait(args: argparse.Namespace) -> int:
    client = _client(args, need_token=False)
    return _wait(
        client,
        args.job,
        timeout=args.timeout,
        joined=False,
        use_sse=not args.poll,
        return_on_action=bool(getattr(args, "return_on_action", False)),
    )


def _chain_root(client: Client, job_id: int) -> int | None:
    """잡 문서의 `chain_id`(반복 사슬의 뿌리, M5s). 사슬이 없거나 못 읽으면 None."""
    try:
        root = client.job(job_id, timeout=5).get("chain_id")
    except ClientError:
        return None
    return root if isinstance(root, int) and not isinstance(root, bool) else None


def cmd_cancel(args: argparse.Namespace) -> int:
    """`rcm cancel N [--cancel-token T | --submission-id S]` — 비밀은 플래그 > 상태 파일 순.
    상태 파일에서는 **이 토큰**으로 낸 그 잡의 가장 최근 제출(`--submission-id` 로 특정)이다
    (M5l S5 — 다른 세션의 항목으로 넘어가는 폴백은 없다). 둘 다 없으면
    어느 쪽이 없는지 말하고 **그래도 보낸다**(키가 꺼진 서버·admin 토큰은 오늘처럼 받는다).
    비밀 자체도 상태 파일의 경로도 어디에도 찍지 않는다."""
    client = _client(args)
    cancel_token = getattr(args, "cancel_token", None) or None
    submission_id = getattr(args, "submission_id", None) or None
    entry: dict[str, Any] | None = None
    if not cancel_token:
        entry = submissions.find(
            client.server,
            args.job,
            submission_id=submission_id,
            token_fingerprint=submissions.fingerprint(client.token),
        )
        if entry is None:
            # 반복 사슬(M5s 결정 228) — 비밀은 뿌리 잡으로 받았다. 뒤 시도 id 로 부르면
            # 뿌리로 찾는다
            root = _chain_root(client, args.job)
            if root is not None and root != args.job:
                entry = submissions.find(
                    client.server,
                    root,
                    submission_id=submission_id,
                    token_fingerprint=submissions.fingerprint(client.token),
                )
        cancel_token = (entry or {}).get("cancel_token")
    if not cancel_token:
        which = (
            f"submission {submission_id} is not in the rcm state file"
            if submission_id
            else "none saved in the rcm state file"
        )
        _info(
            f"no cancel token for job #{args.job}: {which} and no --cancel-token — "
            "sending the cancel without one"
        )
    try:
        resp = client.cancel(args.job, cancel_token=cancel_token)
    except ClientError as e:
        _err(f"cancel failed: {e.message}")
        return USAGE_EXIT if e.status else EXIT_UNKNOWN
    if entry is not None and isinstance(entry.get("submission_id"), str):
        submissions.forget(client.server, entry["submission_id"])  # 쓴 비밀은 더 안 통한다
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


# ── keep · shelf (M5p §4.1 · §4.8 · §15.1 · §15.6) ──────────────────────────

#: `rcm keep` 이 성공한 결과의 첫 토막. 링크는 뒤에 ` · <url>` 로 붙는다.
_KEEP_DONE = {
    "armed": "#{job} will go on the shelf when it finishes",
    "requested": "#{job} is being copied to the shelf",
    "already_requested": "#{job} is already being copied to the shelf",
}
#: 보관하지 못한 결과 — 종료 1(§15.1 표). 사용 오류(2)도 「모른다」(3)도 아니다.
_KEEP_REFUSED = ("no_bundle", "gone", "disabled")


def _keep_url(client: Client, job_id: int, body: dict[str, Any]) -> str:
    """응답의 보관함 링크. 서버가 안 주면 이 클라이언트가 부른 주소의 같은 경로다."""
    shelf = body.get("shelf") if isinstance(body.get("shelf"), dict) else {}
    url = shelf.get("url") or body.get("shelf_url")
    return str(url) if url else f"{client.server}/shelf/{job_id}/"


def _keep_reason(body: dict[str, Any]) -> Any:
    """거절의 이유 — 서버가 본문 맨 위에 준 것이 먼저, 없으면 `shelf` 의 것."""
    shelf = body.get("shelf") if isinstance(body.get("shelf"), dict) else {}
    return body.get("reason_code") or shelf.get("reason_code")


def _bundle_state_of(client: Client, job: int) -> str | None:
    """잡의 묶음 상태(`purged` · `expired` …). `gone` 의 이유를 서버가 안 줄 때 쓴다(표시용)."""
    try:
        doc = client.job(job, timeout=NO_WAIT_VIEW_TIMEOUT)
    except (ClientError, ValueError, OSError):
        return None
    art = doc.get("artifacts") if isinstance(doc, dict) else None
    state = art.get("state") if isinstance(art, dict) else None
    return str(state) if state else None


def cmd_keep(args: argparse.Namespace) -> int:
    """`rcm keep N [--json]` — 끝난 잡(묶음이 살아 있을 때)이나 도는 잡을 보관함에 넣는다.

    종료 코드(§15.1): 넣었거나 넣을 예정이면 0, 보관할 수 없으면(묶음 없음 · 이미 지움 · 꺼짐)
    1, 없는 잡 1, 옛 서버 · 권한 · 토큰 2, 서버에 못 닿거나 보관함 폴더가 unavailable(503)이면 3
    — 서버 쪽 일시 문제다(P3 격리 검증 개선 5).
    """
    client = _client(args)
    code, _h = _shelf_health(client, "keep", "rcm keep")
    if code is not None:
        return code
    job = args.job
    status = 200
    try:
        body = client.post_json(f"/jobs/{job}/keep", {}) or {}
    except ClientError as e:
        status = e.status
        if e.status == 404:
            _err(f"keep: no job #{job}")
            return 1
        if e.status == 403:
            _err(f"keep: #{job} — only its requester, a joiner or an admin token can keep it")
            return USAGE_EXIT
        if e.status in (409, 410, 503) and e.body.get("result") in _KEEP_REFUSED:
            body = e.body
        elif e.status == 503:
            _err(f"keep: {client.server} answered 503: {e.message} — try again later")
            return EXIT_UNKNOWN
        else:
            return _client_fail(client, "keep", e)
    if not isinstance(body, dict):
        body = {}
    if getattr(args, "json", False):
        _print_json(body)
    result = body.get("result")
    if result in _KEEP_REFUSED:
        if result == "no_bundle":
            _err(f"keep: #{job} has nothing to keep — {shelf_none_text(_keep_reason(body))}")
        elif result == "gone":
            why = _keep_reason(body) or _bundle_state_of(client, job) or "gone"
            _err(
                f"keep: #{job} cannot be kept — its artifacts are gone ({why}); "
                "next time use rcm run --keep"
            )
        elif status == 503:
            _err(
                "keep: the shelf folder is unavailable on this server (is its disk mounted?) "
                "— try again later"
            )
            return EXIT_UNKNOWN
        else:
            _err("keep: the shelf is off on this server (shelf_max_bytes = 0)")
        return 1
    if getattr(args, "json", False):
        return 0
    url = _keep_url(client, job, body)
    if result in _KEEP_DONE:
        print(f"{_KEEP_DONE[result].format(job=job)} · {url}", flush=True)
    elif result == "retouched":
        # 새 정리 대기 예정일 — 서버의 시간대로(페이지와 같은 날짜가 보이게)
        shelf = body.get("shelf") if isinstance(body.get("shelf"), dict) else {}
        stale_at = body.get("stale_at") or shelf.get("stale_at")
        if stale_at is None:
            # 응답에 날짜가 없으면 항목 JSON 에서 읽는다(표시용 — 못 읽으면 날짜만 뺀다)
            try:
                item = client.get_json(f"/api/shelf/{job}") or {}
                stale_at = item.get("stale_at") if isinstance(item, dict) else None
            except (ClientError, ValueError, OSError):
                stale_at = None
        when = ""
        if stale_at is not None:
            day = _shelf_date(stale_at, _server_tz(client))
            when = f" (cleanup pending from {day})" if day != DASH else ""
        print(f"#{job} is on the shelf; its clock starts again{when} · {url}", flush=True)
    elif result == "retried":
        # 서버가 지우기 전의 실패 이유(`previous_reason_code`). 없으면 이유를 빼고 말한다 —
        # 새 요청의 `reason_code` 는 이전 실패가 아니다
        why = body.get("previous_reason_code")
        failed = f" ({why})" if why else ""
        print(f"#{job}: the last copy failed{failed}; trying again · {url}", flush=True)
    else:  # 이 클라이언트가 모르는 새 결과 — 서버의 말을 그대로
        print(f"#{job}: {result or 'kept'} · {url}", flush=True)
    return 0


#: `rcm shelf` 의 기본 목록(§15.6) — 보관 중과 정리 대기. `--state all` 은 지운 것만 뺀다.
SHELF_LIST_DEFAULT = ("active", "stale")


def _shelf_table_rows(items: list[dict[str, Any]], tz: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for it in items:
        state = str(it.get("state") or DASH)
        shown = state
        if state == "failed" and it.get("reason_code"):
            shown = f"failed ({it.get('reason_code')})"
        n = it.get("file_count")
        files = DASH if not isinstance(n, int) else f"{n} file" + ("" if n == 1 else "s")
        size = it.get("total_bytes")
        used = it.get("last_used_at")
        due = ""
        if state == "active":
            due = f"stale from {_shelf_date(it.get('stale_at'), tz)}"
        elif state == "stale":
            due = f"removed on {_shelf_date(it.get('delete_at'), tz)}"
        rows.append(
            [
                f"#{it.get('job_id')}",
                str(it.get("preset") or DASH),
                str(it.get("branch") or DASH),
                shown,
                files,
                _fmt_size(size),
                f"used {_shelf_date(used, tz)}" if used is not None else DASH,
                due,
            ]
        )
    return rows


def _fmt_size(value: Any) -> str:
    return _fmt_bytes(value if isinstance(value, int) and not isinstance(value, bool) else None)


def cmd_shelf(args: argparse.Namespace) -> int:
    """`rcm shelf [--state …] [--preset P] [--json]` · `rcm shelf rm N`."""
    if getattr(args, "shelf_command", None) == "rm":
        return cmd_shelf_rm(args)
    if getattr(args, "shelf_command", None) == "get":
        return cmd_shelf_get(args)
    client = _client(args, need_token=False)
    code, h = _shelf_health(client, "shelf", "rcm shelf")
    if code is not None:
        return code
    query = {k: v for k, v in (("state", args.state), ("preset", args.preset)) if v}
    path = "/api/shelf" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    try:
        body = client.get_json(path)
    except ClientError as e:
        if e.status == 503:
            _err(f"shelf: {client.server} answered 503: {e.message} — try again later")
            return EXIT_UNKNOWN
        return _client_fail(client, "shelf", e)
    if getattr(args, "json", False):
        _print_json(body)
        return 0
    raw = body.get("items") if isinstance(body, dict) else body
    items = [i for i in (raw or []) if isinstance(i, dict)]
    if args.state == "all":
        items = [i for i in items if i.get("state") != "deleted"]
    elif args.state:
        items = [i for i in items if i.get("state") == args.state]
    else:
        items = [i for i in items if i.get("state") in SHELF_LIST_DEFAULT]
    link = f"{client.server}/shelf/"
    if not items:
        print(f"the shelf is empty · {link}", flush=True)
        return 0
    # 목록이 시간대와 사용량을 같이 싣는다(P2) — 없으면 status · health 에서 읽는다
    if isinstance(body, dict) and "display_timezone" in body:
        tz = _tz_named(body.get("display_timezone"))
    else:
        tz = _server_tz(client)
    for line in _table(_shelf_table_rows(items, tz)):
        print(line, flush=True)
    storage = body.get("storage") if isinstance(body, dict) else None
    summary = storage if isinstance(storage, dict) else h.get("shelf")
    summary = summary if isinstance(summary, dict) else {}
    count = f"{len(items)} item" + ("" if len(items) == 1 else "s")
    usage = f"{_fmt_size(summary.get('stored_bytes'))} / {_fmt_size(summary.get('limit_bytes'))}"
    print(f"{count} · {usage} · {link}", flush=True)
    return 0


def cmd_shelf_rm(args: argparse.Namespace) -> int:
    """`rcm shelf rm N`(admin) — 보관 항목을 지금 지운다(§4.5 · 결정 146).

    종료 코드(§15.6): 지웠으면 0, 이미 지운 것(410)도 0(같은 결과), 받는 중 · 보관하는 중(409)과
    없는 항목은 1, 권한 · 옛 서버는 2, 서버에 못 닿으면 3.
    """
    client = _client(args)
    code, _h = _shelf_health(client, "shelf rm", "rcm shelf")
    if code is not None:
        return code
    job = args.job
    try:
        client.delete_json(f"/shelf/{job}")
    except ClientError as e:
        if e.status == 410:
            day = _shelf_date(e.body.get("deleted_at"), _server_tz(client))
            print(f"#{job} was already deleted" + (f" on {day}" if day != DASH else ""), flush=True)
            return 0
        if e.status == 409:
            # 서버는 이유 코드로 가른다: `shelf_item_copying`(보관 중) · `shelf_item_busy`(zip)
            copying = e.body.get("reason_code") == "shelf_item_copying"
            if copying or e.body.get("state") in ("pending", "copying"):
                _err(f"shelf rm: #{job} is still being copied — try again when it is on the shelf")
            else:
                _err(f"shelf rm: #{job} is being downloaded right now — try again in a minute")
            return 1
        if e.status == 404:
            _err(f"shelf rm: #{job} is not on the shelf")
            return 1
        if e.status == 403:
            _err("shelf rm: needs an admin token")
            return USAGE_EXIT
        if e.status == 503:
            _err(f"shelf rm: {client.server} answered 503: {e.message} — try again later")
            return EXIT_UNKNOWN
        return _client_fail(client, "shelf rm", e)
    print(f"removed #{job} from the shelf", flush=True)
    return 0


# ── shelf get (M5p §16 — app-map-shots) ─────────────────────────────────────

_SHELF_TARGET_RE = re.compile(r"^(https?://[^/?#\s]+)(?:/api)?/shelf/(\d+)(?:[/?#].*)?$")
#: 보관이 아직 안 끝난 항목 상태 — 받을 것이 아직 없다(종료 3: 잠시 뒤 다시).
_SHELF_NOT_YET = ("waiting", "pending", "copying")


class _ShelfGetError(Exception):
    """받는 도중 멈춰야 하는 일 — (종료 코드, 문구). 아무것도 쓰지 않은 상태에서 올라온다."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_shelf_target(text: Any) -> tuple[str | None, int] | None:
    """`412` → (None, 412), `http://mac.local:8787/shelf/412/` → (그 서버, 412). 아니면 None.

    `/api/shelf/412` 와 항목 페이지 안의 경로(`/shelf/412/f/…`, `?lang=en`)도 받는다 — PR 댓글에
    남긴 링크를 그대로 붙여도 된다."""
    t = str(text or "").strip()
    if t.isdigit():
        return (None, int(t)) if int(t) > 0 else None
    m = _SHELF_TARGET_RE.match(t)
    if not m:
        return None
    return m.group(1).rstrip("/"), int(m.group(2))


def _shelf_fetch_json(client: Client, path: str) -> Any:
    return client.get_json(path)


def _shelf_get_client(args: argparse.Namespace, server: str | None) -> Client:
    """`rcm shelf get` 의 클라이언트 — 리다이렉트를 따르지 않는다.

    번호만 주면 설정의 서버(없으면 같은 네트워크에서 찾는다). URL 을 주면 그 서버다. 토큰은
    **설정의 서버와 같은 곳에만** 싣는다 — 남의 서버 주소에 내 토큰을 보내지 않는다."""
    try:
        cfg = _client_config(args)
        if server is None:
            cfg = _resolve_server(cfg)
    except ConfigError as e:
        raise SystemExit(_usage(str(e))) from e
    mine = (cfg.server or "").rstrip("/")
    if mine and "://" not in mine:
        mine = "http://" + mine
    if server is None:
        if not mine:
            raise SystemExit(_usage(NO_SERVER_HINT))
        return Client(mine, cfg.token or None, follow_redirects=False)
    token = cfg.token if mine and mine.lower() == server.lower() else None
    return Client(server, token or None, follow_redirects=False)


def _shelf_get_fail(client: Client, e: ClientError, *, missing: str, job: Any) -> int:
    """JSON · 파일 요청의 실패를 §16 의 종료 코드로: 없음 · 지움 1, 권한 2, 그 밖 3."""
    if e.status == 404:
        _err(f"shelf get: {missing}")
        return 1
    if e.status == 410:
        day = _shelf_date(e.body.get("deleted_at"), _server_tz(client))
        _err(f"shelf get: #{job} was deleted" + (f" on {day}" if day != DASH else ""))
        return 1
    if 300 <= e.status < 400:
        _err(
            f"shelf get: {client.server} answered {e.status} (a redirect) — rcm does not follow "
            "redirects for shelf downloads; use the server's own address"
        )
        return EXIT_UNKNOWN
    if e.status in (401, 403):
        return _client_fail(client, "shelf get", e)
    if e.status:
        _err(f"shelf get: {client.server} answered {e.status}: {e.message} (try again later)")
        return EXIT_UNKNOWN
    return _client_fail(client, "shelf get", e)


def _shelf_entries(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`/api/shelf/N` 의 파일 목록 → {경로: 항목}. 안전하지 않은 경로가 하나라도 있으면 멈춘다
    (서버가 준 목록도 믿지 않는다 — 그 경로대로 디스크에 쓴다)."""
    out: dict[str, dict[str, Any]] = {}
    for f in doc.get("files") or []:
        path = f.get("path") if isinstance(f, dict) else None
        if not isinstance(path, str) or not shelf_rules.safe_member(path) or path in out:
            raise _ShelfGetError(
                EXIT_UNKNOWN, f"the shelf lists an unsafe path {path!r} — nothing was written"
            )
        out[path] = f
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _unzip_shelf(archive: Path, dest: Path, sizes: dict[str, Any]) -> set[str]:
    """보관함 zip 을 `dest` 아래에 푼다. 멤버마다 경로를 다시 검사한다(서버의 zip 을 믿지 않는다):
    절대 경로 · `..` · `.git` · 제어문자 · 심볼릭 링크 · 같은 이름 두 번은 전부 거절.

    `sizes` 는 `/api/shelf/N` 이 나열한 {경로: 크기}다. 목록에 없는 멤버는 풀지 않고 멈추고, 푸는
    바이트가 목록의 크기를 넘는 순간 멈춘다 — zip 의 선언 크기는 거짓말할 수 있다(압축 폭탄)."""
    names: set[str] = set()
    root = dest.resolve()
    try:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                raw = info.filename
                mode = info.external_attr >> 16
                name = shelf_rules.normalize_member(raw)
                unsafe = (
                    raw.startswith("/")
                    or stat.S_ISLNK(mode)
                    or not shelf_rules.safe_member(name)
                    or name in names
                )
                if unsafe:
                    raise _ShelfGetError(
                        EXIT_UNKNOWN, f"unsafe path in the zip: {raw!r} — nothing was written"
                    )
                if info.is_dir():
                    continue
                if name not in sizes:
                    raise _ShelfGetError(
                        EXIT_UNKNOWN,
                        f"the zip has {name}, which the shelf does not list — nothing was written",
                    )
                limit = sizes[name]
                target = dest / name
                if root not in target.resolve().parents:
                    raise _ShelfGetError(
                        EXIT_UNKNOWN, f"unsafe path in the zip: {raw!r} — nothing was written"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                _copy_capped(z, info, target, limit, name)
                names.add(name)
    except zipfile.BadZipFile as e:
        raise _ShelfGetError(EXIT_UNKNOWN, f"the zip is broken ({e}) — nothing was written") from e
    return names


def _copy_capped(z: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, limit: Any, name: str):
    """멤버 하나를 풀되 목록의 크기(`limit`)를 넘으면 멈춘다. 크기를 모르면 상한 없이 푼다 —
    그 뒤의 sha256 대조가 막는다."""
    too_big = _ShelfGetError(
        EXIT_UNKNOWN, f"{name} is bigger than the shelf lists — nothing was written"
    )
    cap = limit if isinstance(limit, int) and not isinstance(limit, bool) else None
    if cap is not None and info.file_size > cap:
        raise too_big
    written = 0
    with z.open(info) as src, target.open("wb") as fh:
        while True:
            chunk = src.read(1 << 16)
            if not chunk:
                break
            written += len(chunk)
            if cap is not None and written > cap:
                raise too_big
            fh.write(chunk)


def _zip_cap(entries: dict[str, dict[str, Any]]) -> int | None:
    """zip 응답의 바이트 상한 — 목록의 크기 합 + 멤버마다 머리말 몫 + 여유 64 KiB. 크기를 모르는
    파일이 하나라도 있으면 None(상한 없음, sha256 대조가 막는다)."""
    total = 0
    for path, entry in entries.items():
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return None
        total += size + 200 + 2 * len(path.encode("utf-8"))
    return total + 64 * 1024


def _shelf_file_path(client: Client, job: int, entry: dict[str, Any]) -> str:
    """파일 하나의 경로. 서버가 준 `url` 이 이 서버의 것이면 그 경로, 아니면 정해진 모양."""
    url = entry.get("url")
    if isinstance(url, str):
        if url.startswith("/") and not url.startswith("//"):
            return url
        if url.startswith(client.server + "/"):
            return url[len(client.server) :]
    return f"/shelf/{job}/f/{urllib.parse.quote(entry['path'], safe='/')}"


def cmd_shelf_get(args: argparse.Namespace) -> int:
    """`rcm shelf get <N|URL> --output DIR [--file PATH]` · `rcm shelf get --latest P [--branch B]`.

    토큰 없이(보관함의 읽기 규칙) 받는다. 받은 파일은 하나하나 `/api/shelf/N` 의 sha256 과
    대조하고, 전부 맞을 때만 `DIR` 에 옮긴다 — 어긋나면 아무것도 쓰지 않는다. 종료 코드(§16):
    0 받음, 1 없음 · 지움 · 보관 못 함, 2 사용 오류 · 옛 서버 · 권한, 3 연결 실패 · 아직 보관하는 중
    · 받은 것이 목록과 다름.
    """
    target, latest, branch = args.target, args.latest, args.branch
    if (target is None) == (latest is None):
        return _usage("shelf get: give a job number, a shelf link or --latest PRESET")
    if branch is not None and latest is None:
        return _usage("shelf get: --branch goes with --latest")
    parsed = parse_shelf_target(target) if target is not None else (None, None)
    if parsed is None:
        return _usage(
            f"shelf get: {target!r} is not a job number or a shelf link "
            "(http://<host>:8787/shelf/412/)"
        )
    server, job = parsed
    client = _shelf_get_client(args, server)
    code, _h = _shelf_health(client, "shelf get", "rcm shelf")
    if code is not None:
        return code
    if latest is not None:
        query = f"?{urllib.parse.urlencode({'branch': branch})}" if branch else ""
        path = f"/api/shelf/latest/{urllib.parse.quote(latest, safe='')}{query}"
        where = f" on {branch}" if branch else ""
        missing = f"nothing kept for {latest}{where}"
    else:
        path = f"/api/shelf/{job}"
        missing = f"#{job} is not on the shelf"
    try:
        doc = _shelf_fetch_json(client, path)
    except ClientError as e:
        return _shelf_get_fail(client, e, missing=missing, job=job)
    if not isinstance(doc, dict) or not isinstance(doc.get("job_id"), int):
        _err(f"shelf get: {client.server} sent no shelf item for {path}")
        return EXIT_UNKNOWN
    job = doc["job_id"]
    state = doc.get("state")
    if state in _SHELF_NOT_YET:
        _err(f"shelf get: #{job} is still being copied to the shelf — try again in a minute")
        return EXIT_UNKNOWN
    if state == "failed":
        _err(f"shelf get: #{job} was not kept ({doc.get('reason_code') or 'unknown'})")
        return 1
    if state not in ("active", "stale"):
        _err(f"shelf get: #{job} is {state or 'not on the shelf'}")
        return 1
    out_root = Path(args.output)
    # 실패하면 이 명령이 새로 만든 폴더만 지운다 — 이미 있던 폴더는 그대로 둔다(격리 검증 개선 6)
    created = out_root
    while not created.parent.exists() and created.parent != created:
        created = created.parent
    created = None if out_root.exists() else created
    try:
        written = _shelf_get_into(client, job, doc, out_root, getattr(args, "file", None))
    except (_ShelfGetError, ClientError, OSError) as e:
        if created is not None:
            shutil.rmtree(created, ignore_errors=True)
        if isinstance(e, _ShelfGetError):
            _err(f"shelf get: {e.message}")
            return e.code
        if isinstance(e, ClientError):
            return _shelf_get_fail(client, e, missing=f"#{job} is not on the shelf", job=job)
        _err(f"shelf get: cannot write into {out_root}: {e.strerror or e}")
        return EXIT_UNKNOWN
    noun = "file" if written == 1 else "files"
    print(f"wrote {written} {noun} to {out_root} from {client.server}/shelf/{job}/", flush=True)
    return 0


def _capped_download(client: Client, path: str, dest: Path, cap: int | None) -> None:
    try:
        client.download(path, dest, max_bytes=cap)
    except ClientError as e:
        if e.body.get("too_big"):
            raise _ShelfGetError(
                EXIT_UNKNOWN,
                f"the download is bigger than the files the shelf lists ({cap} bytes) — "
                "nothing was written",
            ) from e
        raise


def _shelf_get_into(
    client: Client, job: int, doc: dict[str, Any], out_root: Path, only: str | None
) -> int:
    """받아서 대조하고 옮긴다. 임시 폴더는 `out_root` 안이라 옮기기가 같은 파일 시스템이다."""
    entries = _shelf_entries(doc)
    if only is not None and only not in entries:
        raise _ShelfGetError(1, f"#{job} has no file {only}")
    out_root.mkdir(parents=True, exist_ok=True)
    staging = out_root / f".rcm-shelf-get-{secrets.token_hex(4)}"
    staging.mkdir()
    try:
        files = staging / "files"
        files.mkdir()
        if only is not None:
            wanted = {only: entries[only]}
            target = files / only
            target.parent.mkdir(parents=True, exist_ok=True)
            size = entries[only].get("size")
            cap = size if isinstance(size, int) and not isinstance(size, bool) else None
            _capped_download(client, _shelf_file_path(client, job, entries[only]), target, cap)
            got = {only}
        else:
            wanted = entries
            archive = staging / "item.zip"
            _capped_download(client, f"/shelf/{job}.zip", archive, _zip_cap(entries))
            got = _unzip_shelf(archive, files, {p: e.get("size") for p, e in entries.items()})
        for path, entry in sorted(wanted.items()):
            if path not in got:
                raise _ShelfGetError(
                    EXIT_UNKNOWN, f"{path} is missing from the download — nothing was written"
                )
            want = entry.get("sha256")
            if not isinstance(want, str) or _sha256_file(files / path) != want.lower():
                raise _ShelfGetError(
                    EXIT_UNKNOWN,
                    f"sha256 of {path} does not match the shelf's list — nothing was written",
                )
        for path in sorted(wanted):
            dest = out_root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(files / path, dest)
        return len(wanted)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _offline_gc(args: argparse.Namespace) -> int:
    """서버 없이 도는 `--dry-run`(결정 61·73) — 설정과 데이터 디렉터리를 읽고, **DB 는 임시 사본
    위에서** 실제 마이그레이션과 실제 계획을 돌린 뒤 사본을 지운다. 아무것도 안 지우고, 살아 있는
    DB 는 `mode=ro` 로만 연다.

    업그레이드 안전 게이트가 이것 위에 서 있다. `POST /gc` 는 새 서버에만 있고 새 서버는 뜨자마자
    sweep 하므로, 올리기 **전에** 무엇이 지워질지 보려면 서버 없이 도는 길이 있어야 한다. 2026-09-10
    까지는 여기서 `Store(...)` 를 그냥 열어 **운영 DB 를 마이그레이션했다**(옛 빌드는 재시작하면 못
    뜬다 — docs/gate-replay-fixes-workplan.md §3 B2). 사본 위에서 돌면 마이그레이션 자체의 실패까지
    재시작 전에 드러난다. 어느 단계든 불완전하면 exit 3 — 빈 계획을 성공으로 내지 않는다.
    """
    from remote_ci_monitor.store import DB_VERSION, database_version, newer_database_message

    try:
        cfg = load_server_config(args.config, check_tools=False)
    except ConfigError as e:
        return _usage(str(e))
    db = cfg.data_dir / "rcm.sqlite3"
    if not db.is_file():
        _err(
            f"gc: no database at {db} — the server has not run with this data_dir, or the "
            "config points elsewhere. Nothing was created; the plan is unknown (exit 3)."
        )
        return EXIT_UNKNOWN
    try:
        old = database_version(db)
    except sqlite3.Error as e:
        _err(f"gc: cannot read {db}: {e} (exit 3: unknown)")
        return EXIT_UNKNOWN
    if old > DB_VERSION:
        _err(f"gc: {newer_database_message(db, old)} (exit 3: unknown)")
        return EXIT_UNKNOWN
    now = datetime.now(UTC)
    timeout = float(getattr(args, "timeout", 600.0) or 0.0)
    try:
        scratch = Path(tempfile.mkdtemp(prefix="rcm-gc-dryrun-"))  # 0700
    except OSError as e:
        _err(f"gc: could not make a temporary directory for the copy: {e} (exit 3: unknown).")
        return EXIT_UNKNOWN
    try:
        rc, body = _plan_on_copy(cfg, db, scratch / "rcm.sqlite3", old, now, timeout)
    finally:
        # 사본 · -wal · -shm · 사본의 backup/ — 못 지우면 운영 DB 전체 사본이 남은 것이다.
        # 그 위에 「지웠다」는 exit 0 을 내지 않는다(리뷰 #87 B P1 · M5l L1).
        try:
            shutil.rmtree(scratch)
        except OSError as e:
            _err(
                f"warning: the temporary copy of the database was left behind in {scratch}: "
                f"{e} — remove that directory yourself (exit 3: the copy was not cleaned up)."
            )
            rc = EXIT_UNKNOWN
    if body is None:
        return rc
    if getattr(args, "json", False):
        _print_json(body)
    else:
        upgraded = f"schema v{old} → v{DB_VERSION}" if old != DB_VERSION else f"schema v{old}"
        print(
            f"note: planned on a temporary copy of {db} ({upgraded}); "
            "the database itself was not changed",
            flush=True,
        )
        print(render_gc(body), flush=True)
    return rc


def _plan_on_copy(
    cfg: ServerConfig, db: Path, copy: Path, old: int, now: datetime, timeout: float
) -> tuple[int, dict[str, Any] | None]:
    """`_offline_gc` 의 가운데 — 사본을 뜨고, 올리고, 계획한다. `(exit code, 계획 문서)` 를
    돌려주고 어느 단계든 불완전하면 `(3, None)`. 사본 정리는 부르는 쪽의 `finally` 다.

    사본의 마이그레이션은 `StoreError` 만 내지 않는다 — `_MIGRATIONS` 의 SQL 이 죽으면
    `sqlite3.OperationalError`·`IntegrityError` 가 그대로 올라온다(리뷰 #87 B P1). 새 서버가
    재시작에서 똑같이 죽을 바로 그 실패라, 트레이스백이 아니라 한 줄과 exit 3 이어야 한다.
    """
    from remote_ci_monitor.janitor import Janitor, _item_json
    from remote_ci_monitor.store import (
        DB_VERSION,
        CopyDeadlineExceeded,
        Store,
        StoreError,
        _copy_database,
    )

    try:
        _copy_database(db, copy, deadline=time.monotonic() + timeout)
    except CopyDeadlineExceeded as e:
        _err(
            f"gc: copying the database did not finish within the deadline "
            f"(--timeout {timeout:g} s; {e}) — the plan is unknown (exit 3). "
            "Nothing was changed."
        )
        return EXIT_UNKNOWN, None
    except (sqlite3.Error, OSError) as e:
        _err(f"gc: could not copy the database: {e} — the plan is unknown (exit 3).")
        return EXIT_UNKNOWN, None
    try:
        store = Store(copy, log=_err)  # 사본만 올라간다 — 자동 백업도 임시 디렉터리 안
    except (StoreError, sqlite3.Error) as e:
        _err(
            f"gc: the copy could not be migrated from schema v{old} to v{DB_VERSION}: {e} "
            "— a new server would fail the same way on restart. Nothing was changed "
            "(exit 3: unknown)."
        )
        return EXIT_UNKNOWN, None
    try:
        jan = Janitor(store, cfg)
        plan = jan.plan(now)
        if plan.inventory_error:
            _err(
                f"gc: inventory failed ({plan.inventory_error}) — the plan is unknown, "
                "not empty (exit 3)."
            )
            return EXIT_UNKNOWN, None
        body: dict[str, Any] = {
            "dry_run": True,
            "planned": [_item_json(i) for i in plan.items],
            "deleted": [],
            "failed": [],
            "freed_bytes": 0,
            "storage_before": jan.storage(now),
            "storage_after": None,
            "shelf": _shelf_plan(jan, now),
            "offline": {
                "database": str(db),
                "schema_version": old,
                "planned_with_schema": DB_VERSION,
                "copy": True,
            },
        }
    finally:
        store.close()
    return 0, body


def _shelf_plan(jan: Any, now: datetime) -> dict[str, int] | None:
    """청소기의 다음 회차가 보관함에서 할 일의 수(§5.4). 계획만 — `rcm gc` 는 보관함을 안 건드린다.
    못 세우면 None(줄이 빠진다 — 0 으로 말하지 않는다)."""
    try:
        plan = jan.plan_shelf(now)
    except Exception as e:  # noqa: BLE001 — 보관함 계획이 부피 계획을 망치면 안 된다
        _err(f"gc: the shelf plan could not be made ({type(e).__name__}: {e})")
        return None
    return {"to_stale": len(plan.to_stale), "to_delete": len(plan.to_delete)}


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


def cmd_reload(args: argparse.Namespace) -> int:
    """`rcm reload` — 서버가 설정 파일을 다시 읽게 한다(M5m). 재시작이 아니다.

    프리셋 하나를 붙이자고 서버를 재시작하면 그때 도는 로컬 잡이 `lost` 가 된다. 그리고 다른
    머신에서는 SIGHUP 을 보낼 수가 없다 — 그래서 이 길이 있다. 바뀐 것의 **이름**을 찍는다.
    """
    client = _client(args)
    dry_run = bool(getattr(args, "dry_run", False))
    if dry_run:
        # 옛 서버는 본문을 무시하고 **진짜 reload 를 해 버린다** — 미리 보자던 사람의 설정이
        # 바뀐다. 서버가 dry-run 을 안다고 말할 때만 보낸다(M5o 결정 124)
        try:
            known = bool(client.health().get("reload_dry_run"))
        except ClientError as e:
            _err(f"reload --dry-run: cannot reach the server ({e.message}) — nothing was sent")
            return EXIT_UNKNOWN
        if not known:
            _err(
                "reload --dry-run: this server does not know dry runs and would apply the "
                "reload — upgrade the server first; nothing was sent"
            )
            return USAGE_EXIT
    try:
        resp = client.reload(dry_run=dry_run)
    except ClientError as e:
        _err(f"reload failed: {e.message}")
        return USAGE_EXIT if e.status else EXIT_UNKNOWN
    if getattr(args, "json", False):
        _print_json(resp)
        return 0
    if dry_run:
        print("\n".join(render_reload_dry_run(resp)))
        return 0
    print(reload_summary(resp))
    print(f"config: {resp.get('config')}{_includes_suffix(resp)}")
    return 0


def _includes_suffix(resp: dict[str, Any]) -> str:
    """`(+ presets.dolomood.toml)` — 들인 파일의 이름. 없으면 빈 글자(옛 서버도 키가 없다)."""
    names = [Path(p).name for p in resp.get("includes") or []]
    return f" (+ {', '.join(names)})" if names else ""


#: dry-run 출력의 줄 — (응답 키, 머리말). 머리말은 16칸으로 맞춘다
_DRY_RUN_ROWS = (
    ("presets_changed", "presets changed:"),
    ("presets_added", "presets added:"),
    ("presets_removed", "presets removed:"),
    ("repos_changed", "repos changed:"),
    ("repos_added", "repos added:"),
    ("repos_removed", "repos removed:"),
)


def render_reload_dry_run(resp: dict[str, Any]) -> list[str]:
    """`rcm reload --dry-run` 의 글자. 바뀐 프리셋은 `gate (argv, timeout_seconds)` — 키 이름만."""
    lines = [f"would reload {resp.get('config')}{_includes_suffix(resp)}"]
    keys = resp.get("presets_changed_keys") or {}
    for key, label in _DRY_RUN_ROWS:
        names = list(resp.get(key) or [])
        if not names:
            continue
        if key == "presets_changed":
            names = [f"{n} ({', '.join(keys[n])})" if keys.get(n) else n for n in names]
        lines.append(f"  {label:<16} {', '.join(names)}")
    if len(lines) == 1:
        lines.append("  nothing would change")
    lines.append("  nothing applied (--dry-run)")
    return lines


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
        if reason in (
            "paused",
            "worker_down",
            "overdue",
            "stuck",
            "no_capable_machine",
            "check_failed",
            "check_pending",
        ):
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
                if args.json:
                    _print_json(job)
                elif job.get("state") == "scheduled":
                    # 승격 전의 잡에 ETA 숫자를 지어내지 않는다(M5s §4.1) — 언제 오르는지만 말한다
                    print(f"#{args.job} scheduled · {_scheduled_for(job)}")
                else:
                    print(
                        f"#{args.job} {job.get('state')} · finished "
                        f"{fmt_clock(job.get('finished_at'), _local_tz())} · "
                        f"{job.get('summary') or ''}"
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
        # 예약(M5s) — 옛 서버에는 키가 없다(없는 것은 「예약 없음」이지 실패가 아니다)
        if "scheduled" in pool and pool.get("scheduled") is None:
            print(
                f"scheduled unavailable: {pool.get('scheduled_error') or 'unknown'}",
                file=sys.stderr,
            )
        else:
            rows.extend({**r, "pool": r.get("pool") or pname} for r in pool.get("scheduled") or [])
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
        when = est.get("finish_at") or r.get("finished_at")
        if state in ("running", "cancelling"):
            timing = f"elapsed {fmt_duration(est.get('elapsed_seconds'))}"
        elif state in ("queued", "uploading"):
            timing = f"waiting {fmt_duration(est.get('waited_seconds'))}"
        elif state == "scheduled":
            timing = f"at {fmt_clock(r.get('not_before'), tz, now=datetime.now(UTC))}"
            when = r.get("start_deadline")  # 끝 칸은 「언제까지 시작해야 하나」
        else:
            timing = f"took {fmt_duration(r.get('job_seconds'))}"
        pos = f"{_ordinal(r['position'])} in line · " if r.get("position") else ""
        label = (r.get("requester") or {}).get("label") or "?"
        summary = r.get("summary") or ""
        placed = _jobs_placement_text(r)
        if placed:
            summary = f"{summary}  {placed}" if summary else placed
        jid = f"#{r.get('id')}"
        key = str(r.get("key") or "?")
        print(
            f"{jid:<6} {state:<10} {_cut(key, 16):<16} {label:<20} "
            f"{source_ident(r.get('source')):<{MAX_IDENT}} {pos}{timing:<16} "
            f"{fmt_clock(when, tz)}  {summary}".rstrip()
        )
    return 0


# ── 머신 · 배치 (M5n) ─────────────────────────────────────────────────────────

MACHINES_HEADER = ("machine", "host", "pool", "lanes", "fingerprint", "presets")
#: `rcm check` 의 `machines` 행이 「최근 바뀜」으로 세는 창(§8.2 · 웹 칩과 같다)
ENV_CHANGED_WINDOW_SECONDS = 24 * 3600


def _machines_doc(client: Client) -> dict[str, Any]:
    """`GET /api/machines` — 모양이 틀리면 빈 목록으로 본다(지어내지 않는다)."""
    doc = client.get_json("/api/machines")
    if not isinstance(doc, dict):
        return {"machines": []}
    if not isinstance(doc.get("machines"), list):
        doc = {**doc, "machines": []}
    return doc


def _machine_name(m: dict[str, Any]) -> str:
    return str(m.get("display_name") or m.get("id") or "?")


def _machine_label(m: dict[str, Any]) -> str:
    """표의 첫 칸 — 표시 이름이 id 와 다르면 id 를 붙인다(`Mac mini (@local)`). `--machine` ·
    `recheck` 는 둘 다 받지만, 무엇을 치면 되는지 표에서 보여야 한다(결정 108)."""
    name = _machine_name(m)
    mid = str(m.get("id") or "")
    return name if not mid or mid == name else f"{name} ({mid})"


def _machines_fail(client: Client, e: ClientError) -> int:
    if e.status == 404:
        return _usage(
            f"machines: {client.server} has no /api/machines (older rcm) — upgrade the server"
        )
    return _client_fail(client, "machines", e)


def _table(rows: list[list[str]]) -> list[str]:
    """칸 사이를 두 칸 띄운 표(칸 안의 공백은 하나 — 가정 C1)."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [
        "  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip() for r in rows
    ]


def _machine_detail(m: dict[str, Any]) -> list[str]:
    """`rcm machines --machine ID` — 판정 전부 · 프로브 값 전부(앞 값과 함께) · 점검."""
    out = [
        f"{_machine_name(m)}  ({m.get('id')} · {m.get('kind') or DASH} · pool "
        f"{m.get('pool') or DASH} · {m.get('host_label') or DASH}"
        f"{'' if m.get('alive', True) else ' · down'})"
    ]
    presets = m.get("presets") if isinstance(m.get("presets"), dict) else {}
    out.append("presets")
    rows = [["preset", "state", "code", "detail"]]
    for name, v in presets.items():
        v = v if isinstance(v, dict) else {}
        state = str(v.get("state") or DASH)
        detail = "" if state == "eligible" else verdict_text(v.get("code"), v.get("detail"), state)
        rows.append([str(name), state, str(v.get("code") or DASH), detail])
    out.extend("  " + ln for ln in _table(rows)) if presets else out.append(f"  {DASH}")
    fp = m.get("fingerprint") if isinstance(m.get("fingerprint"), dict) else {}
    probes = fp.get("probes") if isinstance(fp.get("probes"), dict) else {}
    head = "fingerprint"
    if fp.get("hash"):
        head += f" {fp['hash']}"
    if fp.get("stale"):
        head += " (stale)"
    if fp.get("changed_at"):
        head += f" · changed {fp['changed_at']}"
    out.append(head)
    if not probes:
        out.append(f"  {DASH}")
    for name, p in probes.items():
        p = p if isinstance(p, dict) else {}
        value = p.get("value")
        line = f"  {name}  {p.get('state') or DASH}  {value if value is not None else DASH}"
        if _probe_changed(p):
            line += f"  (was {p['previous']})"
        out.append(line)
    checks = m.get("checks") if isinstance(m.get("checks"), list) else []
    out.append("checks")
    if not checks:
        out.append(f"  {DASH}")
    for c in checks:
        c = c if isinstance(c, dict) else {}
        line = f"  {c.get('preset') or '?'}  {c.get('state') or DASH}"
        if c.get("job_id") is not None:
            line += f"  #{c['job_id']}"
        if c.get("auto_submitted"):
            line += "  (automatic)"
        if c.get("error_code"):
            line += f"  {c['error_code']}"
        out.append(line)
    return out


def cmd_machines(args: argparse.Namespace) -> int:
    """`rcm machines` — 워커마다 호스트 · 풀 · 레인 · 지문 · 프리셋 가능 수(§8.2). 읽기 전용."""
    client = _client(args, need_token=False)
    try:
        doc = _machines_doc(client)
    except ClientError as e:
        return _machines_fail(client, e)
    machines = [m for m in doc["machines"] if isinstance(m, dict)]
    if args.machine is not None:
        # id 가 먼저, 아니면 표시 이름(결정 108 — 서버의 `--machine` · `recheck` 와 같은 규칙)
        hit = [m for m in machines if m.get("id") == args.machine] or [
            m for m in machines if m.get("display_name") == args.machine
        ]
        if not hit:
            ids = ", ".join(str(m.get("id")) for m in machines) or "(none)"
            return _usage(f"unknown machine '{args.machine}' — server has: {ids}")
        if args.json:
            _print_json(hit[0])
            return 0
        print("\n".join(_machine_detail(hit[0])))
        return 0
    if args.json:
        _print_json(doc)
        return 0
    rows = [list(MACHINES_HEADER)]
    for m in machines:
        name = _machine_label(m) + ("" if m.get("alive", True) else " (down)")
        lanes = m.get("lanes")
        rows.append(
            [
                name,
                str(m.get("host_label") or DASH),
                str(m.get("pool") or DASH),
                str(lanes) if isinstance(lanes, int) else DASH,
                fingerprint_summary(m.get("fingerprint")),
                presets_summary(m.get("presets")),
            ]
        )
    print("\n".join(_table(rows)))
    return 0


def _recent(at: Any, now: datetime) -> bool:
    when = parse_iso(at) if isinstance(at, str) else None
    return when is not None and 0 <= (now - when).total_seconds() <= ENV_CHANGED_WINDOW_SECONDS


def _probe_changed(probe: dict[str, Any]) -> bool:
    """서버는 모든 프로브에 `previous` 를 싣는다(앞 지문이 없으면 null) — 키가 있다는 것만으로는
    바뀐 게 아니다. 앞 값이 있고 지금 값과 달라야 바뀐 것이다."""
    prev = probe.get("previous")
    return prev is not None and prev != probe.get("value")


def _probe_change(name: str, probe: dict[str, Any]) -> str:
    """`xcode 26.5 → 27.2` — 앞 값의 짧은 꼴 → 지금 값의 짧은 꼴(사라졌으면 `missing`)."""
    before = probe_token("ok", probe.get("previous"))
    return f"{name} {before} → {probe_token(probe.get('state'), probe.get('value'))}"


def _machines_check_row(machines: list[dict[str, Any]]) -> tuple[str, bool, str]:
    """`rcm check` 의 `machines` 행(가정 C7) — 24시간 안에 바뀐 환경과 실패한 점검."""
    now = datetime.now(UTC)
    changed: list[str] = []
    failed: list[str] = []
    n_failed = 0
    recheck: list[str] = []
    n_recheck = 0
    for m in machines:
        name = _machine_name(m)
        fp = m.get("fingerprint") if isinstance(m.get("fingerprint"), dict) else {}
        probes = fp.get("probes") if isinstance(fp.get("probes"), dict) else {}
        if _recent(fp.get("changed_at"), now):
            moved = [
                _probe_change(pname, p)
                for pname, p in probes.items()
                if isinstance(p, dict) and _probe_changed(p)
            ]
            if moved:
                changed.append(f"{name}: {', '.join(moved)}")
        bad = [
            f"{c.get('preset')}" + (f" #{c['job_id']}" if c.get("job_id") is not None else "")
            for c in (m.get("checks") or [])
            if isinstance(c, dict) and c.get("state") == "failed"
        ]
        if bad:
            n_failed += len(bad)
            failed.append(f"{name}: {', '.join(bad)}")
        # 「점검 필요」인데 스스로 안 걸리는 것(유실 · 취소 뒤, 또는 auto_checks = false)
        stalled = [
            f"{c.get('preset')}" + (f" #{c['job_id']}" if c.get("job_id") is not None else "")
            for c in (m.get("checks") or [])
            if isinstance(c, dict) and c.get("state") == "needed" and c.get("recheck_needed")
        ]
        if stalled:
            n_recheck += len(stalled)
            recheck.append(f"{name}: {', '.join(stalled)}")
    parts = [f"{len(machines)} machine{'s' if len(machines) != 1 else ''}"]
    if changed:
        parts.append(f"{len(changed)} environment changed ({'; '.join(changed)})")
    if failed:
        parts.append(f"{n_failed} check failed ({'; '.join(failed)})")
    if recheck:
        parts.append(f"{n_recheck} check needs rcm recheck ({'; '.join(recheck)})")
    return ("machines", not failed and not recheck, " · ".join(parts))


def _machine_check_rows(
    client: Client, status: dict[str, Any]
) -> list[tuple[str, bool | None, str]]:
    """`rcm check` 의 `machines` · `placement` 두 행. `/api/machines` 가 없는 옛 서버면 행도
    없다(FAIL 이 아니다)."""
    try:
        machines = [m for m in _machines_doc(client)["machines"] if isinstance(m, dict)]
    except ClientError as e:
        if e.status == 404:
            return []
        return [("machines", None, f"cannot read /api/machines: {e.message}")]
    rows: list[tuple[str, bool | None, str]] = [_machines_check_row(machines)]
    placed = _placement_check_row(
        [p for p in status.get("presets") or [] if isinstance(p, dict)], machines
    )
    if placed is not None:
        rows.append(placed)
    return rows


_PENDING_CODES = ("check_running", "check_needed")


def _placement_check_row(
    presets: list[dict[str, Any]], machines: list[dict[str, Any]]
) -> tuple[str, bool | None, str] | None:
    """`rcm check` 의 `placement` 행(가정 C8) — any-capable 이거나 `checks` 가 있는 프리셋만."""
    parts: list[str] = []
    short = False
    alive = [m for m in machines if m.get("alive", True)]
    for p in presets:
        name = p.get("name")
        capable = p.get("placement") == "any-capable"
        if not capable and not p.get("checks"):
            continue
        if capable:
            where = "any machine"
            pool_machines = [m for m in alive if m.get("accept_any", True) is not False]
        else:
            pool = p.get("pool") or "default"
            where = f"pool {pool}"
            pool_machines = [m for m in alive if (m.get("pool") or "default") == pool]
        verdicts = [
            v for m in pool_machines if isinstance(v := (m.get("presets") or {}).get(name), dict)
        ]
        n = sum(1 for v in verdicts if v.get("state") == "eligible")
        if n:
            parts.append(f"{name}: {where} ({n} eligible)")
            continue
        short = True
        codes = {v.get("code") for v in verdicts}
        if "check_failed" in codes:
            why = "check failed"
        elif any(
            v.get("code") == "check_needed" and (v.get("detail") or {}).get("recheck")
            for v in verdicts
        ):
            why = "check needs rcm recheck"
        elif codes & set(_PENDING_CODES):
            why = "check pending"
        else:
            why = "no capable machine"
        parts.append(f"{name}: {where} ({why})")
    if not parts:
        return None
    return ("placement", None if short else True, " · ".join(parts))


def cmd_recheck(args: argparse.Namespace) -> int:
    """`rcm recheck MACHINE [CHECK…]` — 그 머신의 점검을 다시 건다(admin, §4.4)."""
    client = _client(args)
    from urllib.parse import quote

    body: dict[str, Any] = {"checks": list(args.checks)} if args.checks else {}
    try:
        resp = client.post_json(f"/machines/{quote(args.machine, safe='@')}/recheck", body)
    except ClientError as e:
        if e.status == 403:
            return _usage(f"recheck: {e.message} — rcm recheck needs an admin token")
        if e.status == 404 and not e.message:
            return _usage(f"recheck: unknown machine '{args.machine}' (or an older server)")
        return _client_fail(client, "recheck", e)
    jobs = [j for j in ((resp or {}).get("jobs") or []) if isinstance(j, dict)]
    if not jobs:
        print(f"nothing to recheck on {args.machine}")
        return 0
    failed = 0
    for j in jobs:
        if not isinstance(j.get("job_id"), int):
            # 서버가 잡을 못 세웠다 — `#None` 을 찍으면 점검이 도는 줄 안다. 코드도 함께 싣는다
            failed += 1
            code = str(j.get("error_code") or "not_submitted")
            why = (
                "could not resolve check_ref (repository unreachable?)"
                if code == "ref_unresolved"
                else "not submitted"
            )
            _err(f"{j.get('preset')} on {args.machine}: not submitted — {why} [{code}]")
            continue
        print(f"#{j.get('job_id')}  {j.get('preset')}  on {args.machine}")
    return 1 if failed else 0


def _no_capable_line(preset: str, resp: dict[str, Any]) -> None:
    """제출 직후 한 줄(§2 S4 · 가정 C9) — 맞는 머신이 지금 하나도 없을 때만. 바쁜 것은 정상이다."""
    placement = resp.get("placement")
    if not isinstance(placement, dict) or placement.get("mode") != "any-capable":
        return
    if placement.get("eligible_now"):
        return
    _err(f"{preset}: no capable machine — {blocked_notes_text(placement.get('blocked'))}")


def _jobs_placement_text(row: dict[str, Any]) -> str:
    """`rcm jobs` 행 끝의 배치 글자 — `rcm top` 의 이유 줄과 같은 글자(가정 C6)."""
    placed = placement_reason_text(row)
    if placed is not None:
        return placed
    return placement_suffix(row).removeprefix(" · ")


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


# ── release (스토어 버전 드래프트) ───────────────────────────────────────────
#
# 서버 API 만 부른다 — 이름을 검증하는 것도, 힌트를 계산하는 것도, 스토어를 부르는 것도 서버다
# (docs/version-page-workplan.md §2.3). 여기서 하는 일은 묻고 · 보내고 · 찍는 것뿐이다.


class ReleaseUsage(Exception):
    """사용 오류 — 문구 그대로 stderr 로 나가고 exit 2. 서버에 보내기 전에 끝난 것들이다."""


#: 「이 스토어는 만들지 않는다」를 뜻하는 대답. 빈 엔터는 힌트를 고르는 것이라 따로 필요하다.
RELEASE_SKIP = "-"
RELEASE_PLATFORMS = ("ios", "android")
RELEASE_PLATFORM_LABEL = {"ios": "iOS", "android": "Android"}
#: 설정의 저장소 이름 규칙(`config._NAME_RE`)과 같다 — 주소에 그대로 들어가므로 여기서 본다.
_RELEASE_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _release_repo(client: Client, wanted: str | None) -> str:
    """`--repo` 가 없으면 릴리스 프로파일이 있는 저장소가 **하나일 때만** 그것으로 고른다.
    둘 이상이면 고르지 않고 이름들을 보여 준다 — 엉뚱한 앱에 버전을 만들지 않는다."""
    if wanted:
        if not _RELEASE_REPO_RE.match(wanted):
            raise ReleaseUsage(f"--repo {wanted!r} is not a repository name")
        return wanted
    doc = client.get_json("/api/repos") or {}
    names = [r["name"] for r in doc.get("repos") or [] if r.get("release")]
    if len(names) == 1:
        return names[0]
    if not names:
        raise ReleaseUsage("no repository on this server has a release profile")
    raise ReleaseUsage(f"--repo is required — this server has {', '.join(names)}")


def _release_url(client: Client, repo: str, version_id: int) -> str:
    """버전 페이지의 웹 주소. 브라우저는 열지 않는다(주소만 찍는다)."""
    return f"{client.server}/#/store/{repo}/v/{version_id}"


def _release_ask(hints: dict[str, Any]) -> dict[str, str | None]:
    """«새 버전을 만듭니다» — 버전 **이름만** 묻는다. 엔터는 힌트, `-` 는 그 스토어를 건너뛴다.
    힌트가 없는 스토어는 빈 기본값으로 묻는다(그냥 엔터면 건너뛴 것과 같다).

    묻는 말은 **stderr** 다 — stdout 에는 새 버전의 번호와 주소만 남아야 `rcm release new > f`
    가 쓸 만하다(프롬프트가 파일로 새어 들어가면 화면에는 아무 말도 안 나온다)."""
    _info("Creating a new version. Enter keeps the hint, '-' skips that store.")
    out: dict[str, str | None] = {}
    for store in RELEASE_PLATFORMS:
        hint = (hints.get(store) or "").strip()
        shown = f" [{hint}]" if hint else ""
        sys.stderr.write(f"{RELEASE_PLATFORM_LABEL[store]}{shown}: ")
        sys.stderr.flush()
        try:
            typed = input().strip()
        except EOFError as e:
            sys.stderr.write("\n")  # 답 없이 끝났다 — 오류 문구가 프롬프트 뒤에 붙지 않게
            raise ReleaseUsage(
                "nothing to read the answers from — pass --ios/--android or --yes"
            ) from e
        out[store] = hint if not typed else (None if typed == RELEASE_SKIP else typed)
    return out


def _release_new(client: Client, repo: str, args: argparse.Namespace) -> int:
    doc = client.get_json(f"/api/repos/{repo}/release/versions") or {}
    hints = doc.get("hints") or {}
    typed = {p: getattr(args, p, None) for p in RELEASE_PLATFORMS}
    if any(v is not None for v in typed.values()):
        names = {p: (None if typed[p] in (None, "", RELEASE_SKIP) else typed[p]) for p in typed}
    elif args.yes:
        names = {p: (hints.get(p) or None) for p in RELEASE_PLATFORMS}
    else:
        names = _release_ask(hints)
    body = {f"{p}_version": names[p] for p in RELEASE_PLATFORMS if names[p]}
    if not body:
        raise ReleaseUsage("no version name to create — give --ios and/or --android")
    resp = client.post_json(f"/api/repos/{repo}/release/versions", body) or {}
    if args.json:
        _print_json(resp)
        return 0
    version_id = resp.get("id")
    job = f" · create job #{resp['job_id']}" if resp.get("job_id") else ""
    print(f"version #{version_id} {resp.get('build_name') or ''} {resp.get('state')}{job}".strip())
    print(_release_url(client, repo, version_id))
    return 0


def _release_names(row: dict[str, Any]) -> str:
    """`1.1.1` · `iOS 1.1.1 · Android 1.0.1` — 두 이름이 같으면 한 번만(워크플랜 §11). 행은
    드래프트(`ios_version`)일 수도 라이브·힌트·지난 것(`ios`)일 수도 있다."""
    ios = row.get("ios_version") or row.get("ios")
    android = row.get("android_version") or row.get("android")
    if ios and android and ios != android:
        return f"iOS {ios} · Android {android}"
    return str(ios or android or "—")


def _release_list(client: Client, repo: str, args: argparse.Namespace) -> int:
    doc = client.get_json(f"/api/repos/{repo}/release/versions") or {}
    if args.json:
        _print_json(doc)
        return 0
    tz, now = _local_tz(), datetime.now(UTC)  # now 를 줘야 오늘이 아닌 날짜가 날짜로 나온다
    print(f"live   {_release_names(doc.get('live') or {})}")
    print(f"next   {_release_names(doc.get('hints') or {})}  (hint)")
    rows = [("draft", r) for r in doc.get("drafts") or []]
    rows += [("submitted", r) for r in doc.get("history") or []]
    if not rows:
        print("no versions yet")
        return 0
    print(f"{'id':<6} {'state':<10} {'version':<30} {'edits':<6} when")
    for kind, row in rows:
        if kind == "submitted":
            print(
                f"#{row['id']:<5} {'submitted':<10} {_release_names(row):<30} "
                f"{'—':<6} {fmt_clock(row.get('submitted_at'), tz, now=now)}"
            )
            continue
        edits = str(row.get("changed") or 0) if row.get("has_edits") else "—"
        when = fmt_clock(row.get("expires_at"), tz, now=now)
        when = f"expired {when}" if row.get("expired") else f"expires {when}"
        print(
            f"#{row['id']:<5} {row.get('state') or '?':<10} {_release_names(row):<30} "
            f"{edits:<6} {when}"
        )
    return 0


def _release_delete(client: Client, repo: str, args: argparse.Namespace) -> int:
    resp = client.delete_json(f"/api/repos/{repo}/release/versions/{args.id}") or {}
    if args.json:
        _print_json(resp)
        return 0
    job = f" · store delete job #{resp['job_id']}" if resp.get("job_id") else ""
    print(f"version #{resp.get('id', args.id)} {resp.get('state')}{job}")
    return 0


#: golive 의 플랫폼 선택지(서버 `RELEASE_PLATFORMS` 와 같다).
GOLIVE_PLATFORMS = ("ios", "android", "both")
#: plan 을 먼저 돌리라고 말할 409 코드들(M5s §4.5).
_GOLIVE_PLAN_FIRST = ("golive_plan_required", "golive_plan_stale")


def _role_wait(client: Client, job_id: int, what: str) -> tuple[int, dict[str, Any] | None]:
    """읽기 전용 역할 잡(golive plan · status)을 끝날 때까지 기다린다 — 상태 줄은 stderr."""
    line = _StatusLine()
    try:
        code, job, reason = wait_for_job(
            client, job_id, on_update=lambda j: line.update(describe(j)), on_info=line.update
        )
    except KeyboardInterrupt:
        line.done()
        _info(f"detached from {what} job #{job_id} — it keeps running (rcm wait --job {job_id})")
        raise
    line.done()
    if reason:
        _err(reason)
    return code, job


def _role_entry(client: Client, repo: str, *path: str) -> dict[str, Any] | None:
    """`GET …/release` 에서 역할의 최신 항목 하나(`("golive", "plan")` · `("status",)`)."""
    doc: Any = client.get_json(f"/api/repos/{repo}/release") or {}
    for key in path:
        doc = doc.get(key) if isinstance(doc, dict) else None
    return doc if isinstance(doc, dict) else None


def _cell(value: Any) -> str:
    return "—" if value is None or value == "" else str(value)


def _golive_plan_lines(repo: str, entry: dict[str, Any], build_name: str) -> list[str]:
    """golive plan 문서를 사람이 읽는 줄로 — 승인 여부 · 승인된 빌드 · 스토어의 말. 번호를 지어내지
    않는다: 문서에 없으면 —. 승인된 플랫폼이 있으면 다음에 칠 명령을 한 줄 보여 준다."""
    doc = entry.get("doc") if isinstance(entry.get("doc"), dict) else {}
    platforms = doc.get("platforms") if isinstance(doc.get("platforms"), dict) else {}
    head = f"golive plan #{entry.get('job_id')} {entry.get('state')}"
    lines = [
        f"{head} · build {_cell(doc.get('build_name'))} · verdict {_cell(doc.get('plan_verdict'))}"
    ]
    approved: dict[str, Any] = {}
    for p in RELEASE_PLATFORMS:
        part = platforms.get(p)
        if not isinstance(part, dict):
            continue
        ok = part.get("approved") is True
        if ok:
            approved[p] = part.get("approved_build")
        word = "approved" if ok else "not approved"
        build = _cell(part.get("approved_build"))
        label = RELEASE_PLATFORM_LABEL[p]
        lines.append(f"{label:<8} {word:<13} build {build:<6} {_cell(part.get('raw_state'))}")
    builds = {b for b in approved.values() if b is not None}
    if approved and len(builds) == 1:
        which = "both" if len(approved) == 2 else next(iter(approved))
        lines.append(
            f"next: rcm release golive {repo} --build-name {build_name} --platform {which} "
            f"--confirm-build-number {next(iter(builds))} [--at HH:MM | --in 30m]"
        )
    return lines


def _release_golive(client: Client, repo: str, args: argparse.Namespace) -> int:
    """`rcm release golive` — plan(`--plan`, 읽기만 · 끝날 때까지 기다린다) 또는 출시 요청(admin ·
    사람이 친 번호 · 지금이나 정한 시각). 검사는 모두 서버가 한다 — 여기서는 보내고 찍는다."""
    build_name = args.build_name
    if args.plan:
        if args.confirm_build_number is not None or args.at or args.start_in:
            raise ReleaseUsage(
                "--plan only reads the stores now: it takes no --confirm-build-number, --at or --in"
            )
        resp = client.post_json(
            f"/api/repos/{repo}/release/golive",
            {"mode": "plan", "build_name": build_name, "platform": args.platform},
        )
        job_id = int(resp["job_id"])
        _info(f"golive plan job #{job_id} {resp.get('state')} — reading the stores (no release)")
        code, job = _role_wait(client, job_id, "golive plan")
        entry = _role_entry(client, repo, "golive", "plan")
        if entry is None or entry.get("job_id") != job_id:
            _err(f"golive plan job #{job_id} left no golive-plan.json")
            if args.json:
                _print_json({"job_id": job_id, "state": (job or {}).get("state"), "plan": None})
            return code if code else 1
        if args.json:
            _print_json({"job_id": job_id, "state": entry.get("state"), "plan": entry})
        else:
            for text in _golive_plan_lines(repo, entry, build_name):
                print(text)
        return code
    if args.confirm_build_number is None:
        raise ReleaseUsage(
            "--confirm-build-number N is required to go live — the approved build the plan "
            f"shows (rcm release golive {repo} --build-name {build_name} --plan)"
        )
    body: dict[str, Any] = {
        "mode": "release",
        "build_name": build_name,
        "platform": args.platform,
        "confirm_build_number": str(args.confirm_build_number),
    }
    if args.at or args.start_in:
        refusal = _schedule_refusal(client.health())
        if refusal:
            raise ReleaseUsage(refusal)
        if args.at:
            body["start_at"] = args.at
        else:
            try:
                body["start_in_seconds"] = parse_duration(args.start_in)
            except ValueError as e:
                raise ReleaseUsage(f"--in: {e}") from None
    try:
        resp = client.post_json(f"/api/repos/{repo}/release/golive", body) or {}
    except ClientError as e:
        if isinstance(e.body, dict) and e.body.get("code") in _GOLIVE_PLAN_FIRST:
            _err(
                f"hint: rcm release golive {repo} --build-name {build_name} "
                f"--platform {args.platform} --plan"
            )
        raise
    job_id = int(resp["job_id"])
    _keep_submission(client, job_id, resp)
    if args.json:
        _print_json(resp)
        return 0
    when = _scheduled_for(resp) if resp.get("not_before") else str(resp.get("state"))
    print(
        f"golive #{job_id} {when} · build {build_name} · {args.platform} "
        f"{args.confirm_build_number}"
    )
    print(f"follow: rcm wait --job {job_id} · cancel: rcm cancel {job_id}")
    return 0


def _status_lines(entry: dict[str, Any]) -> list[str]:
    """status 문서 → 두 스토어 한 줄씩. 모르는 `state` 는 그대로가 아니라 unknown 으로(§4.4)."""
    doc = entry.get("doc") if isinstance(entry.get("doc"), dict) else {}
    lines = [
        f"status #{entry.get('job_id')} {entry.get('state')} · checked "
        f"{_cell(entry.get('measured_at'))}" + (" · stale" if entry.get("stale") else "")
    ]
    for p in RELEASE_PLATFORMS:
        part = doc.get(p)
        if not isinstance(part, dict):
            continue
        state = part.get("state")
        if state not in STORE_STATUS_STATES:
            state = "unknown"
        build = _cell(part.get("build") if p == "ios" else part.get("version_code"))
        version = _cell(part.get("version"))
        lines.append(
            f"{RELEASE_PLATFORM_LABEL[p]:<8} {version:<9} build {build:<6} "
            f"{state:<17} {_cell(part.get('raw_state'))}"
        )
    return lines


#: status 문서의 `state` 8값(M5s §4.4) — 정규화는 프로젝트 스크립트가 한다.
STORE_STATUS_STATES = (
    "preparing",
    "waiting_for_review",
    "in_review",
    "approved",
    "rejected",
    "releasing",
    "released",
    "unknown",
)


def _release_status(client: Client, repo: str, args: argparse.Namespace) -> int:
    """`rcm release status` — 두 스토어의 심사 상태를 읽는 잡을 내고 끝날 때까지 기다린 뒤 그린다.
    읽기만 하는 역할이라 아무 클라이언트 토큰이면 된다."""
    body: dict[str, Any] = {"build_name": args.build_name} if args.build_name else {}
    resp = client.post_json(f"/api/repos/{repo}/release/status", body) or {}
    job_id = int(resp["job_id"])
    _info(f"status job #{job_id} {resp.get('state')} — reading the stores")
    code, job = _role_wait(client, job_id, "status")
    entry = _role_entry(client, repo, "status")
    if entry is None or entry.get("job_id") != job_id:
        _err(f"status job #{job_id} left no status.json")
        if args.json:
            _print_json({"job_id": job_id, "state": (job or {}).get("state"), "status": None})
        return code if code else 1
    if args.json:
        _print_json({"job_id": job_id, "state": entry.get("state"), "status": entry})
    else:
        for text in _status_lines(entry):
            print(text)
    return code


def _release_repo_arg(args: argparse.Namespace) -> str | None:
    """`rcm release golive <repo>` 의 자리 인자와 `--repo` — 둘이 다르면 사용 오류."""
    positional = getattr(args, "repo_arg", None)
    if positional and args.repo and positional != args.repo:
        raise ReleaseUsage(f"two repositories given: {positional} and --repo {args.repo}")
    return positional or args.repo


def cmd_release(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        repo = _release_repo(client, _release_repo_arg(args))
        if args.release_command == "open":
            url = _release_url(client, repo, args.id)
            if args.json:
                _print_json({"id": args.id, "url": url})
            else:
                print(url)
            return 0
        if args.release_command == "new":
            return _release_new(client, repo, args)
        if args.release_command == "list":
            return _release_list(client, repo, args)
        if args.release_command == "golive":
            return _release_golive(client, repo, args)
        if args.release_command == "status":
            return _release_status(client, repo, args)
        return _release_delete(client, repo, args)
    except ReleaseUsage as e:
        return _usage(str(e))
    except ClientError as e:
        return _client_fail(client, f"release {args.release_command} failed", e)
    except KeyboardInterrupt:
        _info("")
        return 130


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
    from remote_ci_monitor.store import StoreError

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
    except (StoreError, sqlite3.Error) as e:
        # DB 를 못 연다(마이그레이션 백업 실패 · 이 빌드보다 새 스키마 · 깨진 파일 · 읽기 전용 ·
        # 마이그레이션 SQL 실패) — 결정 74 의 복구 경로가 이 문장이다. 운영자가 launchd 로그에서
        # 읽는 것이라 트레이스백이 아니라 한 줄로(M5l L5 · 리뷰 pr-94 B-2).
        return _usage(f"cannot start server: {e}")


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


# ── rcm init worker (M5n §9) ─────────────────────────────────────────────────

#: 워커 서비스의 PATH. venv 의 bin 을 앞에 두지 않는다 — 프리셋의 `python` 이 rcm 의 것으로
#: 바뀌어 친다(docs/operating.md 의 운영 함정).
WORKER_SERVICE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
WORKER_SERVICE_LABEL = "com.remote-ci-monitor.worker"
_WORKER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # config._NAME_RE 와 같다


def _short_hostname() -> str:
    """이 컴퓨터의 짧은 호스트명(첫 `.` 앞, 대소문자 그대로). 이름 규칙에 안 맞으면 `default`."""
    import socket

    raw = ""
    try:
        raw = socket.gethostname() or ""
    except OSError:
        raw = ""
    raw = raw or platform.node() or ""
    short = raw.split(".", 1)[0]
    return short if _WORKER_NAME_RE.match(short) else "default"


def _replace_line(text: str, pattern: str, line: str) -> str:
    """템플릿의 그 줄 하나만 바꾼다(뒤 주석은 남긴다). 정확히 하나가 아니면 템플릿이 바뀐 것이다."""
    rx = re.compile(pattern, re.M)
    if len(rx.findall(text)) != 1:
        raise RuntimeError(f"worker template must have exactly one line matching {pattern!r}")
    return rx.sub(line.replace("\\", "\\\\"), text, count=1)


def worker_template(server: str, pool: str, lanes: int, name: str | None) -> bytes:
    """패키지 `templates/worker.toml` 에서 `server` · `pool` · `lanes` · `name` 줄만 바꾼다 —
    줄 수는 그대로다(주석 `# name = …` 줄을 `name = …` 로 바꿔 쓴다)."""
    text = read_template("worker.toml").decode("utf-8")
    text = _replace_line(text, r'^server\s*=\s*"[^"]*"', f'server = "{server}"')
    text = _replace_line(text, r'^pool\s*=\s*"[^"]*"', f'pool = "{pool}"')
    text = _replace_line(text, r"^lanes\s*=\s*\d+", f"lanes = {lanes}")
    if name is not None:
        # `[[repos]]` 예시의 `# name = "app"` 이 아니라 표시 이름 줄 — 뒤 주석으로 가린다
        pattern = r'^#\s*name\s*=\s*"[^"]*"(?=\s+#\s*display name)'
        text = _replace_line(text, pattern, f'name = "{name}"')
    return text.encode("utf-8")


def _this_rcm() -> Path | None:
    """지금 도는 `rcm` 의 절대경로 — 실제로 있는 파일만. 없는 경로를 지어내지 않는다."""
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    candidates: list[Path] = []
    if argv0 is not None and argv0.name == "rcm":
        candidates.append(argv0)
    candidates.append(Path(sys.executable).parent / "rcm")
    found = shutil.which("rcm")
    if found:
        candidates.append(Path(found))
    for c in candidates:
        c = c if c.is_absolute() else Path.cwd() / c
        if c.is_file():
            return c
    return None


def worker_plist(rcm: Path, config: Path, home: Path) -> bytes:
    """launchd plist(macOS) — `rcm worker --config <worker.toml>`. 토큰은 싣지 않는다."""
    import plistlib

    log = str(home / "Library" / "Logs" / "rcm" / "worker.log")
    doc = {
        "Label": WORKER_SERVICE_LABEL,
        "ProgramArguments": [str(rcm), "worker", "--config", str(config)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "WorkingDirectory": str(home),
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {
            "PATH": WORKER_SERVICE_PATH,
            "HOME": str(home),
            "LANG": "en_US.UTF-8",
            "PYTHONUNBUFFERED": "1",
        },
    }
    return plistlib.dumps(doc)


def worker_unit(rcm: Path, config: Path, home: Path) -> bytes:
    """systemd user unit(Linux) — `systemctl --user` 로 올린다. 토큰은 싣지 않는다."""
    text = f"""# remote_ci_monitor worker — written by `rcm init worker --service-file`.
# The token is not in this file: put `token = "…"` in {config} (mode 600).
[Unit]
Description=remote_ci_monitor worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={home}
ExecStart={rcm} worker --config {config}
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
KillMode=mixed
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=LANG=en_US.UTF-8
Environment=PATH={WORKER_SERVICE_PATH}

[Install]
WantedBy=default.target
"""
    return text.encode("utf-8")


def _init_worker_service(args: argparse.Namespace, config: Path) -> int:
    """`--service-file PATH` — 서비스 파일**만** 쓴다. worker.toml 은 안 건드린다(이미 있어도 된다).
    `launchctl` · `systemctl` 은 부르지 않고 다음에 칠 명령을 찍는다."""
    target = Path(args.service_file).expanduser().absolute()
    if sys.platform == "darwin":
        kind = "launchd"
    elif sys.platform.startswith("linux"):
        kind = "systemd"
    else:
        return _usage(
            f"--service-file writes a launchd plist (macOS) or a systemd unit (Linux); "
            f"this platform is {sys.platform}"
        )
    if target.exists() and not args.force:
        return _usage(f"refusing to overwrite {target} (use --force)")
    rcm = _this_rcm()
    if rcm is None:
        return _usage("cannot find the rcm executable this command runs from")
    home = Path.home()
    data = worker_plist(rcm, config, home) if kind == "launchd" else worker_unit(rcm, config, home)
    try:
        write_private(target, data, 0o644)
    except OSError as e:
        return _usage(f"cannot write {target}: {e.strerror or e}")
    print(target, flush=True)
    _info(
        f'the service reads its token from {config} (token = "…", mode 600) — '
        "your shell's RCM_WORKER_TOKEN does not reach it"
    )
    if kind == "launchd":
        _info(
            f"next: mkdir -p {home / 'Library' / 'Logs' / 'rcm'} && "
            f"launchctl bootstrap gui/$(id -u) {target}"
        )
    else:
        _info(
            f"next: put it in ~/.config/systemd/user/, then "
            f"systemctl --user enable --now {target.name}"
        )
    return 0


def cmd_init_worker(args: argparse.Namespace) -> int:
    """`rcm init worker` — 패키지 템플릿으로 `~/.config/rcm/worker.toml`(600)을 쓴다(§9)."""
    path = (
        Path(args.path).expanduser().absolute()
        if args.path
        else user_config_dir().absolute() / "worker.toml"
    )
    if args.service_file:
        return _init_worker_service(args, path)
    server = (args.server or "auto").strip().rstrip("/")
    if server.lower() == "auto":
        server = "auto"
    elif not server.startswith(("http://", "https://")):
        return _usage("--server must be auto or a URL starting with http:// or https://")
    # 템플릿의 따옴표 안에 그대로 들어간다 — `init client` 와 같은 규칙
    if any(c in '"\\' or c.isspace() for c in server):
        return _usage("--server must not contain quotes, backslashes or whitespace")
    pool = args.pool if args.pool is not None else _short_hostname()
    if not _WORKER_NAME_RE.match(pool):
        return _usage(f"--pool must be letters, digits, '.', '_' or '-' (got {pool!r})")
    lanes = args.lanes if args.lanes is not None else 1
    if not 1 <= lanes <= 64:
        return _usage(f"--lanes must be 1-64 (got {lanes})")
    name = args.name
    if name is not None and (
        not name.strip() or any(c in '"\\' or (c.isspace() and c != " ") for c in name)
    ):
        return _usage("--name must not be empty or contain quotes, backslashes or line breaks")
    if path.exists() and not args.force:
        return _usage(f"refusing to overwrite {path} (use --force)")
    try:
        write_private(path, worker_template(server, pool, lanes, name), 0o600)
    except OSError as e:
        return _usage(f"cannot write {path}: {e.strerror or e}")
    print(path, flush=True)
    _info(
        "next: get a token on the server with `rcm token add <name> --worker`, then "
        f"`RCM_WORKER_TOKEN=<token> rcm worker --check --config {path}`"
    )
    _info(f'pool = "{pool}" — presets with that pool, and any-capable presets, can run here')
    _info("then: `rcm init worker --service-file <path>` writes a service file that runs it")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    kind = args.init_kind
    if kind == "worker":
        return cmd_init_worker(args)
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


def _iso_z(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def follow_summary(*, no_follow: bool = False) -> tuple[dict[str, Any], str | None]:
    """`rcm version` 의 따라가기 정보 — (`--json` 의 `follow`, 둘째 줄 또는 None).

    서버에 묻지 않는다. 설정 · 기록 파일 · 캐시만 읽는다. 기본 서버가 비었거나 `auto` 면 발견하지
    않으므로 서버 토막이 없다. 설정이 틀려도 예외를 내지 않는다 — 무엇이 틀렸는지 보려고 치는
    명령이다(§14 C질문10). 둘째 줄은 사람이 설정을 적었을 때나, 기본으로 따라가는 설치에 기본
    서버의 기록이 있을 때만 쓴다(§14 A질문3)."""
    env = os.environ
    boot = follow.booted()
    kind = (boot or {}).get(follow.ENV_FOLLOW_FROM) or follow.install_kind()
    installed = (boot or {}).get(follow.ENV_FOLLOWED_FROM) or __version__
    root = follow.cache_root(env)
    entries = follow.scan_cache(root)
    cached: list[str] = []
    for e in entries:  # 마지막 사용이 최근인 순, 같은 버전은 한 번
        if e.version not in cached:
            cached.append(e.version)
    doc: dict[str, Any] = {
        "mode": None,
        "pin": None,
        "source": None,
        "install": kind,
        "server": None,
        "server_version": None,
        "checked_at": None,
        "cached": cached,
        "installed": installed,
        "running": installed,
    }
    try:
        setting, _ = follow.setting_for(no_follow=no_follow, environ=env, kind=kind)
    except follow.SettingError as e:
        doc["source"] = "env" if e.where == follow.ENV_MODE else "file"
        where = follow.ENV_MODE if e.where == follow.ENV_MODE else follow.tilde(e.path or e.where)
        if e.value is None:
            return doc, f"follow: invalid {where} ({str(e)[len(e.where) + 2 :]})"
        shown = follow.shown(e.value)
        return doc, f"follow: invalid {where} '{shown}' (use follow, installed or a version)"
    doc.update(mode=setting.mode, pin=setting.pin, source=setting.source)
    server = None
    try:
        cfg = load_client_config(None)
        if not cfg.wants_discovery:
            server = follow.normalize_server(cfg.server)
    except ConfigError:
        server = None
    rec = failed_at = None
    if server is not None:
        doc["server"] = server
        rec, failed_at = follow.load_record(root, server)
    if rec is not None:
        doc["server_version"] = rec.version
        doc["checked_at"] = _iso_z(rec.checked_at)
    if setting.mode == follow.MODE_PIN:
        if setting.pin == installed or setting.pin in cached:
            doc["running"] = setting.pin
    elif setting.mode == follow.MODE_FOLLOW and rec is not None:
        if rec.version == installed or any(
            e.version == rec.version and (rec.sha256 is None or e.sha256 == rec.sha256)
            for e in entries
        ):
            doc["running"] = rec.version
    where = {"default": "default", "flag": "--no-follow", "env": follow.ENV_MODE}.get(
        setting.source
    ) or follow.tilde(setting.path)
    has_record = rec is not None or failed_at is not None
    if setting.source == "default" and not (setting.mode == follow.MODE_FOLLOW and has_record):
        return doc, None
    cached_seg = f" · cached {', '.join(cached)}" if cached else ""
    if setting.mode == follow.MODE_INSTALLED:
        return doc, f"follow: off ({where})"
    if setting.mode == follow.MODE_PIN:
        return doc, f"follow: pinned {setting.pin} ({where}){cached_seg}"
    seg = ""
    now = time.time()
    if server is not None and failed_at is not None and (rec is None or failed_at > rec.checked_at):
        seg = f" · server {server} unreachable {_fmt_age(now - failed_at)} ago"
        if rec is not None:
            seg += f" (last seen v{rec.version} {_fmt_age(now - rec.checked_at)} ago)"
    elif server is not None and rec is not None:
        seg = f" · server {server} v{rec.version} checked {_fmt_age(now - rec.checked_at)} ago"
    return doc, f"follow: on ({where}){seg}{cached_seg}"


def cmd_version(args: argparse.Namespace) -> int:
    info = version_info()
    doc, line = follow_summary(no_follow=bool(getattr(args, "no_follow", False)))
    if getattr(args, "json", False):
        _print_json({**info, "follow": doc})
    else:
        py, plat, mach = info["python"], info["platform"], info["machine"]
        print(f"rcm {info['version']} (Python {py}, {plat} {mach})")
        if line:
            print(line)
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


def _local_preset_tools_row(cfg: Any) -> tuple[str, bool, str] | None:
    """`local preset tools` — `requires` 를 선언한 프리셋마다 **이 셸**에서 도구를 찾아 본다.

    잡과 같은 규칙(`env_passthrough` → `[presets.env]` 의 PATH · 없으면 빈 PATH)이지만 환경은
    이 프로세스의 것이다 — launchd 서비스의 PATH 가 아니다. 그래서 이름이 `local` 이고, 정본은
    잡 시작 전 검사다(M5j G4 · Codex: 셸의 ok 가 서비스의 ok 로 읽히면 fail-open). PATH 값은
    찍지 않고, 절대경로로 선언한 항목도 잡 로그와 같은 규칙으로 basename 만 찍는다(M5l S3).
    """
    from remote_ci_monitor.runner import missing_tools, public_tool_name

    parts: list[str] = []
    all_ok = True
    for preset in cfg.presets:
        if not preset.requires:
            continue
        env = {k: os.environ[k] for k in preset.env_passthrough if k in os.environ}
        env.update(preset.env)
        missing = set(missing_tools(tuple(preset.requires), env))
        verdicts = " · ".join(
            f"{public_tool_name(n)} {'missing' if n in missing else 'ok'}" for n in preset.requires
        )
        if preset.placement == PLACEMENT_CAPABLE:
            # 도구가 있는 머신으로 간다 — 여기 없는 것은 정상이다. 판정은 `placement` 행이 한다
            parts.append(f"{preset.name} ({verdicts} · any machine)")
            continue
        all_ok = all_ok and not missing
        parts.append(f"{preset.name} ({verdicts})")
    if not parts:
        return None
    detail = " · ".join(parts) + " — checked in this shell, not the service's environment"
    return ("local preset tools", all_ok, detail)


def _service_preset_env_row(
    health: Any, capable: frozenset[str] = frozenset()
) -> tuple[str, bool, str] | None:
    """`service preset tools` — **서버가 자기 환경으로 잰 것**을 그대로 읽는다(M5m).

    바로 위 `local preset tools` 와 짝이다. 그 줄은 세션의 셸에서 재고 이 줄은 launchd·systemd
    서비스의 환경에서 잰다. 둘이 어긋나는 것이 정보다 — 셸에서는 초록인데 잡은 깨지는 경우가
    그 모양이고(실측 2026-11-11 의 `JAVA_HOME`), 그때 고칠 곳은 **유닛 파일이나 프리셋**이지
    세션의 셸이 아니다.

    옛 서버는 이 키를 안 보낸다 — 그러면 줄을 아예 그리지 않는다(없는 것을 FAIL 로 그리면
    업그레이드가 안 된 서버가 고장으로 보인다).
    """
    facts = health.get("preset_env") if isinstance(health, dict) else None
    if not isinstance(facts, dict) or not facts:
        return None
    parts: list[str] = []
    all_ok = True
    for name, entry in sorted(facts.items()):
        if not isinstance(entry, dict):
            continue
        tools = [str(t) for t in entry.get("missing_tools") or []]
        unset = [str(v) for v in entry.get("missing_passthrough") or []]
        notes = [f"missing {t}" for t in tools] + [f"{v} unset" for v in unset]
        if name in capable:
            # any-capable — 도구가 있는 머신으로 간다. 서버에 없는 것은 정상(`placement` 행이 판정)
            parts.append(f"{name} ({', '.join(notes) if notes else 'ok'} · any machine)")
            continue
        all_ok = all_ok and not tools and not unset
        parts.append(f"{name} ({', '.join(notes) if notes else 'ok'})")
    if not parts:
        return None
    return ("service preset tools", all_ok, " · ".join(parts) + " — as the service sees it")


#: 역할마다 rcm 이 보내는 입력(docs/release-contract.md §2). 프리셋이 안 받으면 제출이 400 이다.
RELEASE_ROLE_INPUTS: dict[str, tuple[str, ...]] = {
    "plan": ("build_name",),
    "upload": ("build_name", "confirm_build_number", "mode", "platform"),
    "review": (
        "build_name",
        "confirm_build_number",
        "mode",
        "platform",
        "play_managed_publishing",
        "listing",
        "phased",
    ),
    "version": ("mode", "ios_version", "android_version", "asc_version_id"),
    # M5s §4.4 · §4.6 — status 는 build_name 하나(비면 최근 버전들), golive 는 넷
    "status": ("build_name",),
    "golive": ("build_name", "confirm_build_number", "platform", "mode"),
}
#: 역할별 **되돌릴 수 없는** `mode` 값 — 프리셋의 기본값이면 안 된다(계약 §2 의 첫 불변식).
RELEASE_IRREVERSIBLE_MODE = {"upload": "upload", "review": "submit", "golive": "release"}
#: 역할별로 `mode` 의 기본값이어야 **하는** 값 — version 은 읽기 전용 prefill 이 기본이다
#: (create 는 스토어에 드래프트를 만들고 delete 는 되돌릴 수 없다).
RELEASE_REQUIRED_MODE_DEFAULT = {"version": "prefill"}
#: 역할별로 `mode` 가 **받을 수 있어야** 하는 값. 기본값만 보면 `choices = ["prefill"]` 인 프리셋이
#: 통과하는데, 서버는 create · delete 를 보낸다 — «새 버전 만들기» 가 제출 순간 400 으로만 드러난다
#: (워크플랜 §13 3). 선택지를 안 건 프리셋(자유 문자열)은 아무 값이나 받으므로 조용하다.
RELEASE_REQUIRED_MODE_CHOICES = {"version": ("create", "delete")}
#: 이 역할들은 `listing_json` 입력을 받아야 웹에서 편집한 문안이 스크립트에 닿는다 — 없으면 warn.
RELEASE_LISTING_JSON_ROLES = ("review", "upload")
#: 이 역할들은 `build_name_android` 입력을 받아야 두 스토어에 **서로 다른** 버전 이름을 보낼 수
#: 있다(계약 §2 「Two store version names」). 없으면 warn — 늘 같은 이름을 쓰는 프로젝트는 그대로다.
RELEASE_BUILD_NAME_ANDROID_ROLES = ("review", "upload", "golive")


def release_secrets_dir(config_path: Path, repo: str) -> Path:
    """비밀 폴더 `<config_dir>/secrets/<repo>/` — 설정 파일 옆이다(계약 §4). 정본은
    `release_secrets.secrets_dir`(서버 · 워커가 같은 규칙을 쓴다)."""
    d = secrets_dir(config_path, repo)
    assert d is not None
    return d


def _release_row(
    cfg: ServerConfig, repo: str, profile: ReleaseProfile
) -> tuple[str, bool | None, str]:
    """`release <repo>` 행 — 프로파일이 가리키는 프리셋이 있고 계약대로 생겼는가.

    FAIL: 필수 역할이 비었거나 없는 프리셋을 가리킨다 · plan/upload/review/version/status/golive
    프리셋이 rcm 이 보내는 입력을 안 받는다 · 되돌릴 수 없는 mode(upload · submit · release)가
    기본값이다 · version 의 mode 기본값이 prefill 이 아니다 · 비밀이 있는데 `secrets_dir_env` 가
    없다 · 비밀 이름이 겹친다.
    warn: 선택 역할이 비었다 · review/upload 프리셋에 `listing_json` 이 없다(웹에서 편집한
    문안이 전달되지 않는다) · review/upload/golive 프리셋에 `build_name_android` 가 없다(두
    스토어가 한 버전 이름을 같이 써야 한다) · version 프리셋의 `mode` 선택지에 `create`·`delete`
    가 없다(서버가 보내는 값이라 제출 순간 400 이 된다) · 비밀 폴더가 아직 없다(Settings 화면이
    만든다).
    """
    problems: list[str] = []
    warnings: list[str] = []
    for role in RELEASE_ROLES:
        name = profile.preset_for(role)
        if name is None:
            if role in RELEASE_REQUIRED_ROLES:
                problems.append(f"presets.{role} is empty")
            else:
                warnings.append(f"{role} not configured")
            continue
        preset = cfg.preset(name)
        if preset is None:
            problems.append(f"presets.{role} = {name!r} is not in [[presets]]")
            continue
        declared = {spec.name for spec in preset.inputs}
        missing = [i for i in RELEASE_ROLE_INPUTS.get(role, ()) if i not in declared]
        if missing:
            problems.append(f"preset {name!r} lacks inputs rcm sends: {', '.join(missing)}")
        irreversible = RELEASE_IRREVERSIBLE_MODE.get(role)
        mode = preset.input_spec("mode") if irreversible else None
        if mode is not None and mode.default == irreversible:
            problems.append(
                f"preset {name!r} input 'mode' defaults to {irreversible!r} — "
                "the irreversible mode must not be the default"
            )
        required_default = RELEASE_REQUIRED_MODE_DEFAULT.get(role)
        mode = preset.input_spec("mode") if required_default else None
        if mode is not None and mode.default != required_default:
            problems.append(
                f"preset {name!r} input 'mode' defaults to {mode.default!r} — "
                f"it must default to {required_default!r} (the read-only mode)"
            )
        needed = RELEASE_REQUIRED_MODE_CHOICES.get(role)
        mode = preset.input_spec("mode") if needed else None
        if mode is not None and mode.choices:
            cannot = [v for v in needed or () if v not in mode.choices]
            if cannot:
                warnings.append(
                    f"preset {name!r} input 'mode' cannot take {', '.join(cannot)} — "
                    "rcm sends them and the job will be refused (re-run /rcm-store-connect)"
                )
        if role in RELEASE_LISTING_JSON_ROLES and "listing_json" not in declared:
            warnings.append(
                f"preset {name!r} has no listing_json input — copy edited in the web UI "
                "will not reach it (re-run /rcm-store-connect)"
            )
        if role in RELEASE_BUILD_NAME_ANDROID_ROLES and "build_name_android" not in declared:
            warnings.append(
                f"preset {name!r} has no build_name_android input — the two stores must then "
                "share one version name (re-run /rcm-store-connect)"
            )
    names = [sec.name for sec in profile.secrets]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.append(f"duplicate secret name(s): {', '.join(dupes)}")
    if profile.secrets and not profile.secrets_dir_env:
        problems.append("secrets_dir_env is required when secrets are listed")
    if profile.secrets and cfg.path is not None:
        d = release_secrets_dir(cfg.path, repo)
        if not d.is_dir():
            warnings.append(f"secrets dir {d} does not exist yet (Settings creates it)")
    if problems:
        return (f"release {repo}", False, " · ".join(problems))
    roles = " ".join(f"{r}={profile.presets[r]}" for r in RELEASE_ROLES if r in profile.presets)
    detail = roles
    if profile.driver:
        detail += f" · driver={profile.driver}"
    if profile.secrets:
        detail += f" · {len(profile.secrets)} secret(s)"
    if warnings:
        return (f"release {repo}", None, detail + " · " + " · ".join(warnings))
    return (f"release {repo}", True, detail)


def _public_url_row(section: Any) -> tuple[str, bool | None, str]:
    """잡 링크의 앞부분(M5o 결정 120). 비어 있으면 warn — 실패는 아니지만 알림 · `rcm run` 의 링크가
    서버가 고른 이름으로 나간다는 것을 그 주소와 함께 말한다(`config.server_base_url`)."""
    url = server_base_url(section, hostname=socket.gethostname())
    if section.public_url:
        return ("public url", True, url)
    return ("public url", None, f"empty — links use {url} (set [server] public_url)")


def local_presets_row(cfg: ServerConfig) -> tuple[str, bool | None, str]:
    """`rcm check` 의 `local presets` 행 — 로컬 설정의 프리셋 수를 파일별로(M5o §4.1).

    `24 (server.toml 2 · presets.dolomood.toml 22)`. 위의 `presets` 행은 **서버가** 지금 들고 있는
    목록이고 이 행은 **이 파일을** 읽으면 나올 목록이다 — 둘이 다르면 reload 를 안 한 것이다.
    프리셋이 하나도 없으면 warn(서버는 뜨지만 아무것도 못 돌린다).
    """
    files = [p for p in (cfg.path, *cfg.includes) if p is not None]
    counts = {p: 0 for p in files}
    for source in cfg.preset_sources.values():
        counts[source] = counts.get(source, 0) + 1
    names = [p.name for p in files]
    parts = [f"{p.name if names.count(p.name) == 1 else p} {counts[p]}" for p in files]
    total = len(cfg.presets)
    return ("local presets", True if total else None, f"{total} ({' · '.join(parts)})")


def _shelf_rows_ro(db: Path) -> tuple[set[int] | None, str | None]:
    """DB 의 보관 행 번호들 — **`mode=ro` 로만** 연다(§15.6). `Store` 로 열면 마이그레이션한다
    (2026-09-10 에 `rcm gc --dry-run --config` 가 운영 DB 를 7→15 로 올린 사고와 같다).
    표가 없으면(v24 이전) (None, 까닭)."""
    from remote_ci_monitor.store import _ro_uri

    if not db.is_file():
        return None, "no database yet"
    try:
        conn = sqlite3.connect(_ro_uri(db), uri=True)
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shelf_items'"
            ).fetchone()
            if not has:
                return None, f"the database is schema v{version}, before the shelf"
            return {int(r[0]) for r in conn.execute("SELECT job_id FROM shelf_items")}, None
        finally:
            conn.close()
    except sqlite3.Error as e:
        return None, f"cannot read the database: {e}"


def local_shelf_row(cfg: ServerConfig) -> tuple[str, bool | None, str]:
    """`rcm check --config` 의 `local shelf` 행 — 이 설정의 보관함 폴더와 행 없는 폴더 수(§5.3).

    행이 없는 숫자 폴더는 서버가 지우지 않는다(모르는 것은 안 지운다). 그래서 여기서 센다 —
    있으면 warn. `.staging` · `.rcm-shelf` 같은 숫자 아닌 이름은 세지 않는다. `keep` 프리셋이
    `artifacts_on = "failure"` 면 실패한 잡만 보관된다는 것도 이 행이 말한다(§3.2).
    """
    shelf = cfg.shelf_dir
    notes = [
        f'{p.name} keeps failed jobs only (artifacts_on = "failure")'
        for p in cfg.presets
        if p.keep and p.artifacts_on == art_core.COLLECT_FAILURE
    ]
    ok: bool | None = True
    if not shelf.is_dir():
        head = f"{shelf} (not created yet)"
    else:
        try:
            folders = {int(p.name) for p in shelf.iterdir() if p.name.isdigit() and p.is_dir()}
        except OSError as e:
            folders, head = None, f"{shelf} · cannot list it ({e.strerror or e})"
            ok = None
        if folders is not None:
            known, why = _shelf_rows_ro(cfg.data_dir / "rcm.sqlite3")
            if known is None:
                head = f"{shelf} · orphans ? ({why})"
            else:
                orphans = len(folders - known)
                head = f"{shelf} · orphans {orphans}"
                if orphans:
                    ok = None
    if not cfg.shelf_enabled:
        head = f"off (shelf_max_bytes = 0) · {head}"
    return ("local shelf", ok, " · ".join([head, *notes]))


def notify_failures_row(server: Any) -> tuple[str, bool | None, str] | None:
    """`server.notify_failures` 가 0 이 아니면 warn 행 하나(M5s §4.7). 0 · 옛 서버면 None.

    알림 훅은 실패해도 다시 보내지 않는다 — 이 수가 「아무도 모르는 실패」를 드러내는 유일한
    자리다. 서버 메모리의 수라 재시작하면 0 이 된다(행이 말한다). 서버 로그에 규칙 이름과 원인
    (`notify <규칙> #<잡>: exit 1`)이 있다."""
    if not isinstance(server, dict):
        return None
    n = server.get("notify_failures")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return None
    what = "delivery" if n == 1 else "deliveries"
    return (
        "notify",
        None,
        f"{n} hook {what} failed since the server started (not retried; a restart "
        "resets the count) — see the server log",
    )


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
    #: 서버가 보낸 health. 아래 `service preset tools` 행이 이걸 읽는다 — 서버에 못 닿았으면
    #: 빈 채로 남고 그 행은 그려지지 않는다.
    health_body: dict[str, Any] = {}
    if client is not None and cfg is not None:
        # 따라간 자식은 찾은 주소를 RCM_SERVER 로 받는다 — BOOT 가 남긴 발견 표시로 꼬리표를 잇는다
        # (M5q §14 A질문12)
        found = (
            getattr(cfg, "discovered", None)
            or (follow.booted() or {}).get(follow.ENV_FOLLOW_FOUND) == "1"
        )
        found_tag = " (found on this network)" if found else ""
        try:
            h = client.health()  # 503 이어도 본문이 온다 — server 행은 FAIL, client 행은 그대로
            health_body = h if isinstance(h, dict) else {}
            unwell = "" if h.get("ok") else f" · {h.get('error') or 'not ok'}"
            rows.append(
                (
                    "server",
                    bool(h.get("ok")),
                    f"{client.server} · v{follow.clean(h.get('version'))}{found_tag}{unwell}",
                )
            )
            client_row = _client_row(client, h, follow.current_state())
            if client_row is not None:
                rows.append(client_row)
            cancel_row = _cancel_row(h)
            if cancel_row is not None:
                rows.append(cancel_row)
            supersede_row = _supersede_row(h)
            if supersede_row is not None:
                rows.append(supersede_row)
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
            rows.extend(_machine_check_rows(client, doc))
            storage = (doc.get("server") or {}).get("job_storage")
            row = storage_row(storage, now=doc.get("generated_at")) if storage else None
            if row is not None:  # 옛 서버엔 키가 없고, 갓 뜬 서버는 아직 잰 게 없다
                rows.append(row)
            hook_row = notify_failures_row(doc.get("server"))
            if hook_row is not None:
                rows.append(hook_row)
            # 보관함(M5p §4.8) — status 의 `shelf_storage`, 없으면 health 의 같은 요약. 둘 다
            # 없으면 옛 서버다(행 없음). 링크는 이 클라이언트가 부른 주소다(§15.6)
            shelf_doc = (doc.get("server") or {}).get("shelf_storage")
            if not isinstance(shelf_doc, dict):
                shelf_doc = health_body.get("shelf")
            shelf = shelf_row(shelf_doc, url=f"{client.server}/shelf/")
            if shelf is not None:
                rows.append(shelf)
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
            rows.append(_public_url_row(cfg.server))
            rows.append(local_presets_row(cfg))
            rows.append(local_shelf_row(cfg))
            tools_row = _local_preset_tools_row(cfg)
            if tools_row is not None:
                rows.append(tools_row)
            service_row = _service_preset_env_row(
                health_body,
                frozenset(p.name for p in cfg.presets if p.placement == PLACEMENT_CAPABLE),
            )
            if service_row is not None:
                rows.append(service_row)
            if cfg.repos:
                git = shutil.which("git")
                rows.append(("git", git is not None, git or "not on PATH (git_ref presets)"))
            for repo in cfg.repos:
                if repo.release is not None:
                    rows.append(_release_row(cfg, repo.name, repo.release))
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
    path = getattr(args, "config", None)
    if path is None and overrides["server"] is None:
        # `rcm init worker` 가 쓴 자리. 문서의 순서(init → `rcm worker --check`)가 `--config` 없이
        # 이 파일을 읽어야 맞는다. `--server` 를 준 긴 꼴은 플래그만 쓴다 — 그 파일의 repos ·
        # data_dir 이 몰래 섞이지 않게(실측 2026-11-16: 안 읽어서 풀이 default 로, 서버는 같은 망의
        # 다른 서버로 잡혔다)
        default = user_config_dir() / "worker.toml"
        if default.is_file():
            path = default
            _info(f"config: {default}")
    cfg = load_worker_config(path, overrides=overrides)
    if cfg.wants_discovery:
        handed = _found_server_from_boot()
        cfg.server = handed or _pick_worker_server(cfg)
        cfg.discovered_server = True  # type: ignore[attr-defined]
    return cfg


def _found_server_from_boot() -> str | None:
    """따라간 자식이면 부모가 mDNS 로 찾아 `RCM_SERVER` 로 넘긴 주소(M5q §14 B질문16). 발견 표시가
    없는 프로세스는 오늘처럼 `RCM_SERVER` 를 읽지 않는다. 읽은 뒤에는 환경에서 지운다 — 워커가
    띄우는 잡과 다시 실행할 프로세스로 새지 않게. `discovered_server` 는 그대로 켜 두므로 주소가
    바뀌면 다시 찾는 동작도 남는다."""
    boot = follow.booted()
    if not boot or boot.get(follow.ENV_FOLLOW_FOUND) != "1":
        return None
    return os.environ.pop("RCM_SERVER", None) or None


def _pick_worker_server(cfg: Any, *, timeout: float = 1.5) -> str:
    """같은 망의 rcm 서버를 찾는다(M5n §9). 하나면 그것, 여럿이면 `server_name` 과 이름이 맞는
    것. 그래도 모호하거나 없으면 `ConfigError`(이름들과 고치는 법을 말한다)."""
    found = discover(timeout=timeout)
    want = (cfg.server_name or "").strip()
    picks = [f for f in found if f.name == want] if want else list(found)
    if len(picks) == 1:
        f = picks[0]
        ip = f.ips[0] if f.ips else f.host.rstrip(".")
        _info(f"server: found {f.name} ({ip}:{f.port}) on this network")
        return f.address
    if not found:
        raise ConfigError(f"worker needs a server — {DISCOVER_NONE_HINT}")
    names = ", ".join(f"{f.name} ({f.address})" for f in found)
    if want:
        raise ConfigError(
            f"no rcm server named {want!r} on this network (found: {names}) — "
            "fix server_name in worker.toml or pass --server"
        )
    raise ConfigError(
        f'several rcm servers on this network: {names} — set server_name = "<name>" in '
        "worker.toml or pass --server"
    )


def _worker_rediscover(cfg: Any) -> Any:
    """주소가 바뀌었을 때 다시 찾는 콜백(연속 연결 실패 5번마다 — `remote_worker`). 못 찾으면
    None 이고 워커는 옛 주소로 계속 시도한다."""

    def again() -> str | None:
        try:
            return _pick_worker_server(cfg)
        except ConfigError:
            return None

    return again


class _DropNotFollowing:
    """stderr 감싸개 — `rcm: not following …` 경고 줄만 버린다. 기동 등록 409(②)에서 따라가지
    못하면 그 이유가 종료 문장(`<409 문장> · not following: <이유>`)에 붙으므로 「설치본으로
    계속한다」는 경고는 거짓말이 된다. 받은 줄과 `remove …` 안내는 그대로 쓴다."""

    def __init__(self, inner: Any):
        self.inner = inner

    def write(self, text: str) -> int:
        if text.startswith("rcm: not following "):
            return len(text)
        return self.inner.write(text)

    def flush(self) -> None:
        self.inner.flush()


def _worker_follow_setting(args: argparse.Namespace) -> tuple[Any, str]:
    """(설정, 설치 종류). 틀린 `RCM_CLIENT_VERSION` 이나 한 줄 파일은 `ConfigError`(종료 2)."""
    kind = follow.install_kind()
    setting, warning = follow.setting_for(
        no_follow=bool(getattr(args, "no_follow", False)), environ=os.environ, kind=kind
    )
    if warning:
        _err(warning)
    return setting, kind


def _worker_follow_start(args: argparse.Namespace, cfg: Any) -> Any:
    """기동 ①(M5q §3.2) — 등록 **전에** health 를 새로(10분 기록 없이) 읽고 CLI 와 같은 흐름으로
    따라간다. exec 하면 돌아오지 않는다. 실패는 경고 한 줄과 함께 설치본으로 등록을 이어 간다.
    따라간 자식(BOOT 를 거쳤다)은 다시 따라가지 않는다(결정 161)."""
    if follow.booted() is not None:  # 자식 — `_follow_first` 가 남긴 상태를 그대로 쓴다
        return follow.current_state() or follow.boot_state(os.environ)
    setting, kind = _worker_follow_setting(args)
    return follow.maybe_follow(
        args.follow_argv,
        setting=setting,
        server=cfg.server,
        token=cfg.token or None,
        discovered=bool(getattr(cfg, "discovered_server", False)),
        fresh=True,
        environ=os.environ,
        execve=args.follow_execve,
        kind=kind,
        installed=__version__,
    )


def _worker_follow_409(args: argparse.Namespace, cfg: Any, worker: Any, e: Any) -> int:
    """기동 등록 409 ②(M5q §3.2 · §5.5). 따라가면 돌아오지 않는다. 따라가지 않거나 못 하면
    오늘의 409 문장 뒤에 이유를 붙여 종료 2 — 감독자가 다시 띄우면 ① 부터 다시 한다."""
    state = follow.current_state()
    boot = follow.booted()
    if getattr(args, "once", False) or state is None:
        _err(e.message)  # `--once` 의 ② 는 오늘처럼 끝난다(§14 B질문4)
        return e.code
    setting = state.setting
    if setting.mode == follow.MODE_PIN and setting.pin != e.version:
        # 고정이 원인이다 — 고정한 버전으로 도는 따라간 자식도 같은 문장이다(B1 격리 검증)
        _err(f"{e.message} · pinned by {_setting_where(setting)}")
        return e.code
    if boot is not None:
        # 따라간 자식은 스스로 다시 따라가지 않는다(결정 161). 설치본 부트로더로 다시 실행해
        # 설치본이 ① 부터 하게 한다(§14 B질문7). 300초 안에 이미 재실행했으면 감독자에게 맡긴다
        if boot.get("loaded"):
            if not worker.restart_window_open():
                worker.log(f"register: server runs v{follow.clean(e.version)} — following")
                return _worker_exec_installed(args, worker, e.code)
            _err(f"{e.message} · not following: already following v{__version__}")
        else:
            # BOOT 가 그 폴더에 `.bad` 를 남겼다 — 사람이 할 일을 첫 실패에도 한 줄로. 다음 판(①
            # 의 `cache_broken`)과 같은 순서로 안내를 먼저 쓴다
            path = boot.get(follow.ENV_FOLLOWED_PATH)
            if path:
                _err(f"remove {path} to fetch it again")
            _err(f"{e.message} · not following: {follow.reason_text('boot_failed')}")
        return e.code
    if state.warn == "cache_broken" and state.server_version == e.version:
        # ① 이 이미 그 폴더를 말했다(경고와 치우는 안내) — 같은 폴더로 다시 따라가려 하지 않는다
        _err(f"{e.message} · not following: {state.warn_text}")
        return e.code
    if setting.mode == follow.MODE_INSTALLED:
        tail, _ = _follow_tail(dataclasses.replace(state, warn=None, warn_text=None))
        _err(f"{e.message} · {tail}" if tail else e.message)
        return e.code
    worker.log(f"register: server runs v{follow.clean(e.version)} — following")
    again = follow.maybe_follow(
        args.follow_argv,
        setting=setting,
        server=cfg.server,
        token=cfg.token or None,
        discovered=bool(getattr(cfg, "discovered_server", False)),
        fresh=True,
        environ=os.environ,
        execve=args.follow_execve,
        err=_DropNotFollowing(sys.stderr),
        kind=state.install,
        installed=__version__,
    )
    if again.warn:
        _err(f"{e.message} · not following: {again.warn_text}")
    else:
        _err(e.message)  # health 가 이제 설치본 버전을 말한다 — 감독자가 다시 띄우면 등록된다
    return e.code


def cmd_worker(args: argparse.Namespace) -> int:
    """`rcm worker` — 원격 워커 프로세스(M5b-3). 토큰은 RCM_WORKER_TOKEN 또는 worker.toml 로만.

    서버 버전을 따라간다(M5q B1): 기동할 때(①)와 기동 등록이 버전 불일치 409 일 때(②).
    돌다가 서버 버전이 바뀌면(③ heartbeat · ④ 레인 재등록 409) 드레인한 뒤 설치본 부트로더로
    같은 PID 에서 다시 실행한다(B2). `--check` 와 인자 목록으로 부른 `main(argv)`(결정 162)는
    따라가지 않는다."""
    from remote_ci_monitor.client import WorkerClient
    from remote_ci_monitor.remote_worker import RemoteWorker, WorkerExit, WorkerFollow

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
    following = bool(getattr(args, "follow_enabled", False))
    state = None
    if following:
        try:
            state = _worker_follow_start(args, cfg)
        except ConfigError as e:
            return _usage(str(e))
    follows, tail = _worker_follow_policy(state)
    rediscover = _worker_rediscover(cfg) if getattr(cfg, "discovered_server", False) else None
    worker = RemoteWorker(
        cfg,
        client=client,
        once=bool(getattr(args, "once", False)),
        rediscover=rediscover,
        follows=follows,
        follow_tail=tail,
    )

    def _stop(signum: int, _frame: Any) -> None:
        # `stopping` 이 아니라 `signalled` 로 두 번째 신호를 가린다 — 드레인 끝에 `run()` 이 켠
        # `stopping` 뒤의 첫 신호는 평범한 멈춤이다(§14 B질문18)
        if worker.signalled:
            # 두 번째 신호는 즉시 — SystemExit 은 `run()` 의 finally 가 레인 join(grace+10초)으로
            # 붙잡고, 레인 스레드는 daemon 이 아니라 인터프리터 종료도 기다린다
            _info(f"signal {signum} again: exiting now")
            sys.stderr.flush()
            os._exit(1)
        worker.signalled = True
        _info(f"signal {signum}: stopping — running jobs are reported as lost")
        worker.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        code = worker.run()
    except WorkerFollow as e:
        if not following:
            _err(e.message)
            return e.code
        return _worker_follow_409(args, cfg, worker, e)
    except WorkerExit as e:
        if e.message:
            _err(e.message)
        return e.code
    if worker.restart_for is None or worker.signalled or not following:
        return code
    # 드레인이 끝났다 — 잡은 다 보고했다. 측정 프로브를 정리하고 설치본으로 다시 실행한다(§3.3 의 5)
    worker.release_probes()
    worker.log(
        f"drained — restarting through the installed rcm for v{follow.clean(worker.restart_for)}"
    )
    return _worker_exec_installed(args, worker, code)


def _worker_follow_policy(state: Any) -> tuple[bool, str | None]:
    """(드레인하나, 따라가지 않는 이유). 설정이 `follow` 면 ③ · ④ 에서 드레인한다. 고정 ·
    installed · editable 은 드레인하지 않고, ③ 의 로그와 ④ 의 종료 문장에 붙일 꼬리를 받는다
    (§14 B질문19). 따라가지 않는 호출(`main(argv)`)은 둘 다 없다. 다시 실행은 POSIX 의 exec
    이므로 다른 OS 는 드레인하지 않는다(결정 172)."""
    if state is None:
        return False, None
    if state.setting.mode == follow.MODE_FOLLOW:
        return os.name == "posix", None
    tail, _ = _follow_tail(dataclasses.replace(state, followed=False, warn=None, warn_text=None))
    return False, tail


def _worker_exec_installed(args: argparse.Namespace, worker: Any, code: int) -> int:
    """설치본 부트로더로 다시 실행한다. 진짜 exec 는 돌아오지 않는다. 실패는 로그 한 줄과 종료 1 —
    감독자가 다시 띄우면 설치본이 ① 부터 한다(§3.4 · W9)."""
    try:
        follow.restart_via_installed(
            args.follow_argv, environ=os.environ, execve=args.follow_execve
        )
    except OSError as e:
        worker.log(f"restart failed: {e.strerror or e}")
        return 1
    return code  # 가짜 execve 가 돌아온 경우(시험)


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

    rows: list[tuple[str, bool | None, str]] = [python_row()]
    health: dict[str, Any] | None = None
    try:
        h = client.health()
        health = h if isinstance(h, dict) else None
        # 서버가 보낸 문자열 — 제어 문자를 지우고 64자로 자른다(M5q §14.5, `rcm check` 와 같다)
        server_v = follow.clean(h.get("version"))
        rows.append(("server", bool(h.get("ok")), f"{client.server} · v{server_v}"))
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
    capability_rows, missing = _worker_capability_rows(cfg, client)
    rows.extend(capability_rows)
    version = _worker_version_row(health)
    if version is not None:
        rows.append(version)
    # 끝 줄(M5t-a §4.5): 빠진 도구 수와 설치 안내를 볼 곳. 지금 줄들처럼 FAIL 이 되지 않는다
    count = "—" if missing is None else f"{missing}"
    rows.append(
        ("missing", True, f"{count} missing · rcm doctor --role worker for what to install")
    )
    ok_all = all(ok is not False for _, ok, _ in rows)
    for name, ok, detail in rows:
        mark = "ok " if ok else ("warn" if ok is None else "FAIL")
        print(f"{mark}  {name:<13} {detail}")
    return 0 if ok_all else 1


def _probe_summary(name: str, state: str, value: str | None) -> str:
    """지문 한 칸 — `xcode 27.2` · `idb missing` · `java exit 1`. `rcm machines` 와 같다."""
    return f"{name} {probe_token(state, value)}"


def _worker_version_row(health: dict[str, Any] | None) -> tuple[str, bool | None, str] | None:
    """`version` 행(M5t-a §4.5 · TD18) — 지금은 버전이 다르면 등록할 때 409 를 받아야 안다.

    같으면 ok. 다르면 버전 따라가기(M5q)로 맞출 수 있을 때 warn, editable · 고정 설치처럼 따라갈
    수 없으면 FAIL 이다. 서버가 버전을 말하지 않으면 행을 그리지 않는다.
    """
    raw = (health or {}).get("version")
    if not isinstance(raw, str) or not raw:
        return None
    server_v = follow.clean(raw)
    if raw == __version__:
        return ("version", True, f"{__version__} · the same as the server")
    head = f"this worker runs v{__version__}, the server v{server_v}"
    kind = follow.install_kind()
    try:
        setting, _warning = follow.setting_for(no_follow=False, environ=os.environ, kind=kind)
    except ConfigError as e:
        return ("version", False, f"{head} and cannot follow it ({e}) — install the same release")
    if setting.mode == follow.MODE_FOLLOW and os.name == "posix":
        return ("version", None, f"{head} — registering follows the server version")
    why = "pinned" if setting.mode == follow.MODE_PIN else f"{kind} install"
    return ("version", False, f"{head} and cannot follow it ({why}) — install the same release")


def _worker_capability_rows(
    cfg: Any, client: Any
) -> tuple[list[tuple[str, bool | None, str]], int | None]:
    """`rcm worker --check` 의 `fingerprint` · `presets` — 서버의 명세를 받아 **여기서** 잰다.

    서버에 보고하지 않는다(등록도 capabilities 도 없다). 못 도는 프리셋은 사실이지 검사 실패가
    아니다 — 두 행은 FAIL 이 되지 않는다. 둘째 값은 빠진 도구 수(서로 다른 이름) — 명세를 못
    받았으면 None.
    """
    from remote_ci_monitor.client import ClientError
    from remote_ci_monitor.remote_worker import RemoteWorker

    try:
        spec = client.spec()
    except ClientError as e:
        why = "server has no /worker/spec (older rcm)" if e.status == 404 else e.message
        return [("fingerprint", None, f"— {why}"), ("presets", None, f"— {why}")], None
    body = RemoteWorker(cfg, client=client, log=lambda _m: None).measure(spec)
    fp = " · ".join(
        _probe_summary(n, p.get("state", ""), p.get("value"))
        for n, p in body["fingerprint"].items()
    )
    total = len(body["presets"])
    ok = 0
    reasons: dict[str, int] = {}
    for facts in body["presets"].values():
        if facts["missing_tools"]:
            key = verdict_text("tool_missing", {"tools": facts["missing_tools"]})
        elif facts["workspace_tools"]:
            key = verdict_text("workspace_tool", {"tools": facts["workspace_tools"]})
        else:
            ok += 1
            continue
        reasons[key] = reasons.get(key, 0) + 1
    top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    detail = " · ".join([f"{ok}/{total} ok", *(f"{n} {k}" for k, n in top)])
    missing = {t for facts in body["presets"].values() for t in facts["missing_tools"]}
    rows = [("fingerprint", True, fp or "no probes configured"), ("presets", True, detail)]
    return rows, len(missing)


def cmd_doctor(args: argparse.Namespace) -> int:
    """`rcm doctor` — 이 머신에서 무엇을 빌드 · 시험할 수 있고 무엇을 깔아야 하는지(M5t-a 결정
    246). 판정과 출력은 `doctor` 모듈이 한다. 여기서는 바깥 세계를 넘긴다 — mDNS 찾기와
    `git ls-remote` 는 부를 때 이 모듈의 이름으로 찾는다(시험이 바꿔 끼우는 자리)."""
    from remote_ci_monitor import doctor

    ctx = doctor.Context(
        environ=os.environ,
        discover=lambda timeout: discover(timeout=timeout),
        ls_remote=lambda url: _ls_remote_problem(url),
        worker_service_path=WORKER_SERVICE_PATH,
    )
    return doctor.main(args, ctx)


def cmd_token(args: argparse.Namespace) -> int:
    from remote_ci_monitor.store import Store, StoreError

    try:
        cfg = _server_config(args)
    except ConfigError as e:
        return _usage(f"config: {e}")
    try:
        store = Store(cfg.data_dir / "rcm.sqlite3")  # 열면서 마이그레이션한다 — 거절도 여기서
    except OSError as e:
        # `data_dir` 이 파일이거나 만들 수 없는 곳 — `serve` 와 같은 모양으로(리뷰 pr-94 B-3)
        return _usage(str(e.strerror or e))
    except (StoreError, sqlite3.Error) as e:
        return _usage(str(e))
    now = datetime.now(UTC)
    try:
        if args.token_command == "add":
            worker = bool(getattr(args, "worker", False))
            if args.admin and worker:
                return _usage("--admin and --worker cannot be combined")
            kind = "worker" if worker else ("admin" if args.admin else "client")
            secret = store.add_token(args.name, kind=kind, now=now)
            where = (
                "RCM_WORKER_TOKEN on the worker machine" if worker else "RCM_TOKEN on the client"
            )
            _info(f"token '{args.name}' created — shown once, store it as {where}:")
            print(secret, flush=True)
            if worker:
                # 다음에 칠 명령 — 비밀은 다시 찍지 않는다(M5n §9)
                _info("on the worker machine: RCM_WORKER_TOKEN=<token above> rcm worker --check")
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
    except (StoreError, sqlite3.Error) as e:
        # 중복 이름(StoreError) · 다른 프로세스의 쓰기 잠금(`database is locked`) 도 한 줄로
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
) -> dict[str, Any]:
    """묶음을 받아 트리에 쓴다(M5e §11). 결과 요약을 돌려준다 — **어느 갈래든 판정이 나온다**.

    `ok` 가 종료 코드를 정한다: 참이면 0, 거짓이면 `EXIT_DELIVERY`. 부르는 쪽 둘
    (`rcm run --fetch-artifacts` 의 `_wait` · `rcm artifacts --fetch` 의 `cmd_artifacts`)이
    같은 키를 읽는다. 한때 비-ready 에 `None` 을 돌려줬고 둘 다 그걸 0 으로 읽었다.

    절차: 스테이징으로 통째로 받고 → 전수 검사와 분류를 **보여 주고** → 쓰고 → 충돌 없이 전부
    적용됐을 때만 ack 한다. `--dry-run` 은 표만 찍고 아무것도 안 쓴다(ack 도 안 한다).
    """
    try:
        doc = client.artifacts(job_id)
    except ClientError as e:
        _err(f"artifacts: {e.message}")
        return {"state": "error", "ok": False, "complete": False, "wrote": 0, "conflicted": 0}
    state = doc.get("state")
    if state != "ready":
        # ⚠️ 한때 여기서 `None` 을 돌려줬고, 부르는 쪽 둘은 그걸 **전부 0** 으로 읽었다.
        #    주석은 `disabled`·`empty` 만 변호했는데 코드는 `pending`·`uploading`·`dropped`·
        #    `failed`·`expired`·`purged` 까지 같이 초록으로 만들었다 — 아직 끝나지도 않은 잡에
        #    받기를 걸면 빈 디렉터리에 종료 0 이 떨어졌다. 이제 판정을 실어 돌려준다.
        reason = doc.get("reason_code")
        _info(f"artifacts: {state}" + (f" ({reason})" if reason else ""))
        return {
            "state": state,
            "reason_code": reason,
            "ok": state in NOTHING_TO_DELIVER_BY_DESIGN,
            "complete": False,
            "wrote": 0,
            "conflicted": 0,
        }
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
            return {"state": state, "ok": False, "complete": False, "wrote": 0, "conflicted": 0}
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
            return {"state": state, "ok": False, "complete": False, "wrote": 0, "conflicted": 0}
        if dry_run:
            # 시나리오 c #13 은 이 종료 코드를 열어 뒀다(0·5 둘 다 받았다). 매듭: **진짜로 돌렸다면
            # 나왔을 코드**를 그대로 낸다 — 깨끗한 계획이면 0, 충돌이 있으면 5. 그래야
            # `--dry-run` 이 「이거 깨끗하게 적용되나?」의 값싼 사전 검사가 된다. 옛 동작은
            # `complete=False` 라 **충돌이 없어도 늘 5** 였다 — 멀쩡한 미리보기가 실패로 보였다.
            would_conflict = counts.get("conflicted", 0)
            would_write = counts.get("new", 0) + counts.get("changed", 0)
            _info(
                f"artifacts: dry run — would write {would_write}, "
                f"unchanged {counts.get('unchanged', 0)}, conflicted {would_conflict}"
                " (nothing written)"
            )
            return {
                "state": state,
                "ok": would_conflict == 0 or force,
                "complete": False,
                "wrote": 0,
                "conflicted": would_conflict,
                "dry_run": True,
            }
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
        "ok": result.complete,
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
    return 0 if out.get("ok") else EXIT_DELIVERY


# ── rcm skills — 패키지에 든 연결 스킬을 프로젝트에 복사한다 ─────────────────


def skills_root() -> Any | None:
    """패키지 안 `skills/` — 없으면 None(스킬 없이 빌드된 배포). 테스트가 갈아 끼운다."""
    root = importlib.resources.files("remote_ci_monitor") / "skills"
    return root if root.is_dir() else None


def _skill_dirs(root: Any) -> list[Any]:
    """`skills/<name>/` 폴더들 — 이름순. 숨김 폴더와 `__pycache__` 는 스킬이 아니다."""
    return sorted(
        (d for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))),
        key=lambda d: d.name,
    )


def _skill_files(folder: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """폴더 안 파일들을 (상대경로, 파일) 로 — 하위 폴더까지, 숨김 파일은 뺀다."""
    out: list[tuple[str, Any]] = []
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.startswith((".", "__pycache__")):
            continue
        rel = f"{prefix}{entry.name}"
        if entry.is_dir():
            out.extend(_skill_files(entry, rel + "/"))
        else:
            out.append((rel, entry))
    return out


def _skill_description(folder: Any) -> str:
    """SKILL.md 머리말의 `description:` 한 줄. 없으면 빈 문자열."""
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    m = re.search(r"^description:\s*(.+?)\s*$", text, re.M)
    if not m:
        return ""
    desc = m.group(1).strip("\"'")
    # 한 문단짜리 설명은 목록에 너무 길다 — 첫 문장까지만
    return re.split(r"(?<=[.!?])\s+", desc, maxsplit=1)[0]


def cmd_skills(args: argparse.Namespace) -> int:
    """`rcm skills list` · `rcm skills install --into DIR [--force]`.

    install 은 `skills/<name>/` 마다 `DIR/.claude/skills/<name>/` 로 파일을 복사한다. 이미 있는
    파일은 같으면 `kept`, 다르면 `--force` 없이는 `skipped` 로 두고 끝에 1 로 나간다 — 프로젝트가
    손본 스킬을 조용히 되돌리지 않는다.
    """
    root = skills_root()
    skills = _skill_dirs(root) if root is not None else []
    if args.skills_command == "list":
        if not skills:
            _err("no skills are packaged in this build of rcm")
            return 1
        for folder in skills:
            desc = _skill_description(folder)
            print(f"{folder.name:<24} {desc}".rstrip(), flush=True)
        return 0
    into = Path(args.into).expanduser()
    if not into.is_dir():
        return _usage(f"--into must be an existing project directory, got {into}")
    if not skills:
        _err("no skills are packaged in this build of rcm")
        return 1
    refused: list[str] = []
    for folder in skills:
        for rel, entry in _skill_files(folder):
            target = into / ".claude" / "skills" / folder.name / rel
            shown = target.relative_to(into).as_posix()
            data = entry.read_bytes()
            if target.exists():
                try:
                    same = target.read_bytes() == data
                except OSError:
                    same = False
                if same:
                    print(f"kept     {shown}", flush=True)
                    continue
                if not args.force:
                    print(f"skipped  {shown} (differs; use --force to overwrite)", flush=True)
                    refused.append(shown)
                    continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError as e:
                return _usage(f"cannot write {target}: {e.strerror or e}")
            print(f"written  {shown}", flush=True)
    if refused:
        _err(f"skills: {len(refused)} file(s) differ from the packaged copy — rerun with --force")
        return 1
    return 0


NO_FOLLOW_HELP = (
    "run this installed rcm instead of the server's version "
    "(default: follow the server; RCM_CLIENT_VERSION or ~/.config/rcm/client-version pins it)"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rcm",
        description="Local job server for one build machine: submit a preset, wait for the result.",
    )
    p.add_argument("--version", action="version", version=f"rcm {__version__}")
    p.add_argument(
        "--no-follow",
        dest="no_follow",
        action="store_true",
        default=False,
        help=NO_FOLLOW_HELP,
    )
    sub = p.add_subparsers(dest="command", required=True)

    def client_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--server", help="server URL (default: RCM_SERVER or client.toml)")
        sp.add_argument("--token", help="bearer token (default: RCM_TOKEN or client.toml)")
        sp.add_argument(
            "--client-config", help="client.toml path (default: ~/.config/rcm/client.toml)"
        )
        # 하위 파서 쪽은 SUPPRESS — 주지 않았을 때 최상위 값을 덮지 않는다(§2.2)
        sp.add_argument(
            "--no-follow",
            dest="no_follow",
            action="store_true",
            default=argparse.SUPPRESS,
            help=NO_FOLLOW_HELP,
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
    run.add_argument(
        "--link",
        metavar="URL",
        help="an http(s) page to show next to the job's name in the web UI, such as its pull "
        "request (ignored when the submission joins an existing job)",
    )
    run.add_argument(
        "--link-label",
        metavar="TEXT",
        help="short text for --link, at most 40 characters (default: the URL, shortened)",
    )
    run.add_argument(
        "--machine",
        metavar="ID",
        help="run on this machine only (an id or name from `rcm machines`, `@local` for the "
        "build machine); check presets need a token, any other preset an admin token",
    )
    run.add_argument("--no-join", action="store_true", help="never join an identical active job")
    run.add_argument(
        "--no-supersede",
        action="store_true",
        help="keep older jobs of this branch even if the preset supersedes them",
    )
    run.add_argument("--no-wait", action="store_true", help="submit and exit 0 without waiting")
    when = run.add_mutually_exclusive_group()
    when.add_argument(
        "--at",
        metavar="TIME",
        help="run at this time instead of now: HH:MM (today), YYYY-MM-DDTHH:MM or ISO-8601 with "
        "an offset; read in the server's [display] timezone (needs rcm 0.10.0+ on the server)",
    )
    when.add_argument(
        "--in",
        dest="start_in",
        metavar="DURATION",
        help="run after this long instead of now: 90s, 5m, 2h, 1h30m",
    )
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
    run.add_argument(
        "--keep",
        action="store_true",
        help="put the job's artifacts on the server's shelf when it finishes and print the link "
        "(needs a server with the shelf, rcm 0.7.0+)",
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

    keep = sub.add_parser(
        "keep", help="put a job's artifacts on the shelf (a finished job whose bundle still exists)"
    )
    keep.add_argument("job", type=int)
    keep.add_argument("--json", action="store_true", help="print the server's answer as JSON")
    client_opts(keep)
    keep.set_defaults(func=cmd_keep)

    shelf = sub.add_parser("shelf", help="list the shelf: kept job files with a browser link")
    shelf.add_argument(
        "--state",
        choices=["active", "stale", "all"],
        default=None,
        help="active = kept, stale = cleanup pending (default: both); all adds copying and failed",
    )
    shelf.add_argument("--preset", metavar="P", help="only this preset")
    shelf.add_argument("--json", action="store_true", help="print the server's list as JSON")
    client_opts(shelf)
    shelf.set_defaults(func=cmd_shelf)
    shelf_sub = shelf.add_subparsers(dest="shelf_command")
    shelf_rm = shelf_sub.add_parser("rm", help="remove a job's files from the shelf now (admin)")
    shelf_rm.add_argument("job", type=int)
    client_opts(shelf_rm)
    shelf_rm.set_defaults(func=cmd_shelf)
    shelf_get = shelf_sub.add_parser(
        "get", help="download a kept job's files (no token needed; checks every sha256)"
    )
    shelf_get.add_argument(
        "target",
        nargs="?",
        help="a job number, or a shelf link such as http://host:8787/shelf/412/",
    )
    shelf_get.add_argument("--latest", metavar="PRESET", help="the newest kept job of this preset")
    shelf_get.add_argument("--branch", metavar="B", help="with --latest: only this branch")
    shelf_get.add_argument(
        "--output", metavar="DIR", required=True, help="write the files here (same paths as kept)"
    )
    shelf_get.add_argument("--file", metavar="PATH", help="only this file (a path the item lists)")
    client_opts(shelf_get)
    shelf_get.set_defaults(func=cmd_shelf)

    wait = sub.add_parser(
        "wait", help="wait for a job and exit with 0/1/2/3 (a scheduled job is waited for too)"
    )
    wait.add_argument("--job", type=int, required=True)
    wait.add_argument("--timeout", type=float, help="give up waiting after N seconds (exit 3)")
    wait.add_argument(
        "--poll", action="store_true", help="poll every 2s instead of the event stream"
    )
    wait.add_argument(
        "--return-on-action",
        action="store_true",
        help="return with exit 4 when the job asks a person to act (::rcm::action::); "
        "the job keeps running",
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
    cancel.add_argument(
        "--cancel-token",
        metavar="TOKEN",
        help="the submission's cancel token (default: the newest one rcm run saved for this job "
        "with this token in $XDG_STATE_HOME/rcm/submissions.json or "
        "~/.local/state/rcm/submissions.json)",
    )
    cancel.add_argument(
        "--submission-id",
        metavar="ID",
        help="use the saved cancel token of this submission (the id from the --no-wait JSON) "
        "when two sessions of this user are on the same job",
    )
    client_opts(cancel)
    cancel.set_defaults(func=cmd_cancel)

    for name, help_text in (
        ("pause", "pause the queue (admin)"),
        ("resume", "resume the queue (admin)"),
    ):
        sp = sub.add_parser(name, help=help_text)
        client_opts(sp)
        sp.set_defaults(func=cmd_pause)

    reload_cmd = sub.add_parser(
        "reload", help="re-read the server config — presets and repos only (admin)"
    )
    reload_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="show which presets and keys would change, apply nothing",
    )
    reload_cmd.add_argument("--json", action="store_true")
    client_opts(reload_cmd)
    reload_cmd.set_defaults(func=cmd_reload)

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
    jobs.add_argument(
        "--state", help="filter by state (running, queued, scheduled, failed, expired, ...)"
    )
    jobs.add_argument("--pool", metavar="NAME", help="only jobs of this worker pool")
    jobs.add_argument("--ref", metavar="REF", help="only jobs whose ref or branch contains REF")
    jobs.add_argument("--json", action="store_true")
    client_opts(jobs)
    jobs.set_defaults(func=cmd_jobs)

    machines = sub.add_parser(
        "machines", help="list machines: host, pool, lanes, fingerprint, presets they can run"
    )
    machines.add_argument(
        "--machine", metavar="ID", help="every preset verdict of one machine (id or name)"
    )
    machines.add_argument("--json", action="store_true", help="the server's document as is")
    client_opts(machines)
    machines.set_defaults(func=cmd_machines)

    recheck = sub.add_parser("recheck", help="run a machine's check presets again (admin token)")
    recheck.add_argument(
        "machine",
        help="machine id or name from `rcm machines` (`@local` or its host name for the server)",
    )
    recheck.add_argument("checks", nargs="*", metavar="CHECK", help="check presets (default: all)")
    client_opts(recheck)
    recheck.set_defaults(func=cmd_recheck)

    logs = sub.add_parser("logs", help="print a job log (token required)")
    logs.add_argument("job", type=int)
    logs.add_argument("--follow", action="store_true", help="keep printing until the job ends")
    client_opts(logs)
    logs.set_defaults(func=cmd_logs)

    presets = sub.add_parser("presets", help="list presets and their inputs")
    presets.add_argument("--json", action="store_true")
    client_opts(presets)
    presets.set_defaults(func=cmd_presets)

    release = sub.add_parser(
        "release", help="store version drafts (new, list, delete, open), status and golive"
    )
    rsub = release.add_subparsers(dest="release_command", required=True)

    def release_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--repo", help="repository name (default: the only one with a profile)")
        sp.add_argument("--json", action="store_true")
        client_opts(sp)

    rnew = rsub.add_parser("new", help="create a version draft (admin token)")
    rnew.add_argument("--ios", metavar="X", help="App Store version name ('-' skips iOS)")
    rnew.add_argument("--android", metavar="Y", help="Google Play version name ('-' skips Play)")
    rnew.add_argument(
        "--yes", "-y", action="store_true", help="take the server's hints without asking"
    )
    release_opts(rnew)
    rlist = rsub.add_parser("list", help="open drafts and recently submitted versions")
    release_opts(rlist)
    rdelete = rsub.add_parser("delete", help="discard a draft (admin token)")
    rdelete.add_argument("id", type=int)
    release_opts(rdelete)
    ropen = rsub.add_parser("open", help="print the web address of a version page")
    ropen.add_argument("id", type=int)
    release_opts(ropen)
    rgolive = rsub.add_parser(
        "golive",
        help="go live with an approved build: --plan reads the stores; otherwise asks for a "
        "release now or at a time (admin token, the build number a person typed)",
    )
    rgolive.add_argument("repo_arg", nargs="?", metavar="repo", help="repository name")
    rgolive.add_argument("--build-name", required=True, metavar="X.Y.Z", help="version name")
    rgolive.add_argument("--platform", choices=GOLIVE_PLATFORMS, default="both")
    rgolive.add_argument(
        "--confirm-build-number",
        type=int,
        metavar="N",
        help="the approved build number the plan shows, typed by you (required to go live)",
    )
    rgolive_when = rgolive.add_mutually_exclusive_group()
    rgolive_when.add_argument(
        "--at", metavar="TIME", help="go live at this time: HH:MM (today), YYYY-MM-DDTHH:MM"
    )
    rgolive_when.add_argument(
        "--in", dest="start_in", metavar="DURATION", help="go live after this long: 30m, 2h"
    )
    rgolive.add_argument(
        "--plan",
        action="store_true",
        help="only read the stores: approved or not, and the approved build (waits)",
    )
    release_opts(rgolive)
    rstatus = rsub.add_parser(
        "status", help="read the review state of both stores (runs the status role and waits)"
    )
    rstatus.add_argument("repo_arg", nargs="?", metavar="repo", help="repository name")
    rstatus.add_argument("--build-name", metavar="X.Y.Z", help="empty = the newest versions")
    release_opts(rstatus)
    release.set_defaults(func=cmd_release)

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
    worker.add_argument(
        "--config",
        help="worker.toml path (repos, host sampler, data_dir; default without --server: "
        "~/.config/rcm/worker.toml if it exists)",
    )
    worker.add_argument("--data", help="worker data dir (workspaces, logs)")
    worker.add_argument(
        "--check", action="store_true", help="verify server, token kind, pool and repos, then exit"
    )
    worker.add_argument(
        "--once", action="store_true", help="run at most one job, then exit (tests, cron)"
    )
    # 워커도 서버 버전을 따라간다(M5q B1) — 최상위 값을 덮지 않게 SUPPRESS
    worker.add_argument(
        "--no-follow",
        dest="no_follow",
        action="store_true",
        default=argparse.SUPPRESS,
        help=NO_FOLLOW_HELP,
    )
    worker.set_defaults(func=cmd_worker)

    init = sub.add_parser("init", help="write a starter config file (server, client or worker)")
    isub = init.add_subparsers(dest="init_kind", required=True)
    iserver = isub.add_parser("server", help="~/.config/rcm/server.toml from the packaged example")
    iclient = isub.add_parser("client", help="~/.config/rcm/client.toml pointing at your server")
    iclient.add_argument("--server", required=True, metavar="URL", help="http://build-machine:8787")
    iworker = isub.add_parser(
        "worker", help="~/.config/rcm/worker.toml for another computer that runs jobs"
    )
    iworker.add_argument(
        "--server",
        metavar="URL|auto",
        help="the rcm server, or auto to find it on this network (default: auto)",
    )
    iworker.add_argument("--pool", help="pool this computer serves (default: short host name)")
    iworker.add_argument(
        "--lanes", type=int, help="parallel jobs on this computer, 1-64 (default 1)"
    )
    iworker.add_argument("--name", help="display name (default: the host name)")
    iworker.add_argument(
        "--service-file",
        metavar="PATH",
        help="instead: write a launchd plist (macOS) or systemd unit (Linux) that runs "
        "`rcm worker --config <worker.toml>` — the file only, nothing is started",
    )
    for sp in (iserver, iclient, iworker):
        sp.add_argument("--path", help="write here instead of the default location")
        sp.add_argument("--force", action="store_true", help="overwrite an existing file")
        sp.set_defaults(func=cmd_init)

    skills = sub.add_parser("skills", help="list or install the packaged rcm-*-connect skills")
    ssub = skills.add_subparsers(dest="skills_command", required=True)
    ssub.add_parser("list", help="one line per packaged skill")
    sinstall = ssub.add_parser("install", help="copy every skill into DIR/.claude/skills/<name>/")
    sinstall.add_argument("--into", required=True, metavar="DIR", help="the project directory")
    sinstall.add_argument(
        "--force", action="store_true", help="overwrite files whose content differs"
    )
    skills.set_defaults(func=cmd_skills)

    doc = sub.add_parser("doctor", help="what this machine can build and test, and what to install")
    doc.add_argument("--role", choices=("client", "worker", "coordinator", "coordinator-worker"))
    doc.add_argument("--server")
    doc.add_argument("--config")
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--offline", action="store_true")
    doc.add_argument("--shell-path", action="store_true")
    doc.add_argument("--service-file")
    doc.set_defaults(func=cmd_doctor)

    version = sub.add_parser("version", help="print the version (and Python/OS with --json)")
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=cmd_version)
    return p


def _long_options(parser: argparse.ArgumentParser) -> set[str]:
    """파서와 그 아래 하위 파서들의 긴 옵션 — 훑기의 줄임 규칙이 견줄 목록(§14 A질문2)."""
    out: set[str] = set()
    for action in parser._actions:
        out.update(o for o in action.option_strings if o.startswith("--"))
        if isinstance(action, argparse._SubParsersAction):
            for sp in action.choices.values():
                out |= _long_options(sp)
    return out


def _parser_options(parser: argparse.ArgumentParser) -> dict[str | None, frozenset[str]]:
    """설치본 파서의 긴 옵션 — 키 None 은 최상위, 명령 이름은 그 하위 파서(와 그 아래)."""
    out: dict[str | None, frozenset[str]] = {
        None: frozenset(o for a in parser._actions for o in a.option_strings if o.startswith("--"))
    }
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sp in action.choices.items():
                out[name] = frozenset(_long_options(sp))
    return out


def _follow_first(execve: Any) -> int | None:
    """argparse 전에 서버 버전을 따라간다(M5q §2.1). exec 하면 돌아오지 않는다. 종료 코드를
    돌려주면 명령을 돌리지 않고 끝낸다(틀린 `RCM_CLIENT_VERSION` 의 2). 그 밖의 실패는
    `maybe_follow` 가 경고 한 줄로 말하고 설치본으로 계속한다."""
    if follow.booted() is not None:
        # 따라간 자식(BOOT 를 거쳤다) — 다시 따라가지 않는다(결정 161)
        follow._set_state(follow.boot_state(os.environ))
        return None
    argv = sys.argv[1:]
    p = follow.prescan(argv, options=_parser_options(build_parser()))
    if not follow.follows(p):
        return None
    kind = follow.install_kind()
    try:
        setting, warning = follow.setting_for(no_follow=p.no_follow, environ=os.environ, kind=kind)
    except ConfigError as e:
        return _usage(str(e))
    if warning:
        _err(warning)
    if setting.mode == follow.MODE_INSTALLED:
        follow._set_state(
            follow.FollowState(
                setting, kind, __version__, __version__, False, None, None, None, None
            )
        )
        return None
    try:
        cfg = load_client_config(p.client_config, server=p.server, token=p.token)
        wanted = cfg.wants_discovery
        _resolve_server(cfg)
    except ConfigError:
        return None  # 명령이 오늘의 문장으로 말한다(§2.1 의 4)
    if not cfg.server:
        return None
    follow.maybe_follow(
        argv,
        setting=setting,
        server=cfg.server,
        token=cfg.token or None,
        discovered=bool(wanted and getattr(cfg, "discovered", None)),
        fresh=p.command == "check",
        environ=os.environ,
        execve=execve,
        kind=kind,
        installed=__version__,
    )
    return None


def main(argv: list[str] | None = None) -> int:
    """콘솔 스크립트의 진입. 인자 목록을 넘겨 부르면(시험 · 다른 프로그램 안) 따라가지 않는다 —
    남의 프로세스를 exec 로 갈아치우면 안 된다(결정 162)."""
    return _main(argv, follow_enabled=argv is None, execve=os.execve)


def _main(argv: list[str] | None, *, follow_enabled: bool, execve: Any = os.execve) -> int:
    global _DISCOVERY, _FOUND_SAID
    _DISCOVERY, _FOUND_SAID = None, False
    follow._set_state(None)
    if follow_enabled:
        try:
            code = _follow_first(execve)
        except KeyboardInterrupt:
            _err("interrupted")
            return 130
        if code is not None:
            return code
    parser = build_parser()
    args = parser.parse_args(argv)
    # `rcm worker` 는 argparse 뒤에 따라간다(M5q B1) — 그때 쓸 것을 넘긴다. 인자 목록으로 부른
    # `main(argv)` 는 따라가지 않는다(결정 162)
    args.follow_enabled = follow_enabled
    args.follow_execve = execve
    args.follow_argv = list(sys.argv[1:] if argv is None else argv)
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
    except sqlite3.Error as e:
        # 안전망 — 어느 서브커맨드가 SQLite 오류를 흘려도 트레이스백 + exit 1 이 아니라
        # 한 줄 + 3(모른다). 각 명령은 제 자리에서 먼저 잡아 제 문장을 낸다.
        _err(f"rcm: database error: {type(e).__name__}: {e} (exit 3: unknown)")
        return EXIT_UNKNOWN


if __name__ == "__main__":
    sys.exit(main())
