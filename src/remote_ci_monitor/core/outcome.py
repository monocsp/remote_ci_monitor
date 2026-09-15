"""잡 요약을 **코드**로 말한다 — 오너 결정 37(2026-09-08), 명세 `docs/m5d-workplan.md` §4.5.

서버는 「무슨 일이 있었는지」를 코드와 **원시 인자**로 남기고, 보여 주는 쪽(웹 화면 · CLI ·
알림 훅)이 각자의 말로 그린다. 서버가 `48 MB` 같은 문자열로 굳혀 보내면 화면이 다시 쓸 수
없기 때문에, 인자는 바이트 수 · 초 · 종료 코드 · 이름 같은 값 그대로 담는다.

잡이 `::rcm::summary::` 로 찍은 문장에는 **코드가 없다**. 팀이 쓴 문장이라 번역 대상이 아니고,
그대로 보여 준다. 그래서 `summary_code` 가 없는 요약이 정상이다.

영어 문장은 여기서 만든다. 지금까지 서버 곳곳에 흩어져 있던 문장을 한 표로 모은 것이라, 이 모듈이
바뀌면 화면 · CLI · 알림이 함께 바뀐다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

#: 코드 → 영어 문장을 만드는 함수. 인자는 `summary_args` 의 원시 값이다.
#: 여기 없는 코드는 「모르는 코드」이고, 보여 주는 쪽은 저장된 `summary` 문장으로 물러선다.
Renderer = Callable[[dict[str, Any]], str]


def _mb(n: Any) -> str:
    """바이트 수를 사람이 읽는 크기로. 서버의 기존 표기와 글자 하나까지 같다."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "?"
    return f"{v / 1e6:.0f} MB" if v >= 1e6 else f"{v / 1e3:.0f} KB"


def _secs(n: Any) -> str:
    """초를 분/초 표기로. 60초 미만은 초, 그 위는 분(내림) — 기존 표기와 같다."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "?"
    return f"{v // 60}m" if v >= 60 else f"{v}s"


def _limit(seconds: Any) -> str:
    """시간 초과 문구. `worker.format_limit` 과 글자까지 같다."""
    if seconds is None:
        return "limit"
    try:
        v = int(seconds)
    except (TypeError, ValueError):
        return "limit"
    if v % 3600 == 0:
        return f"limit {v // 3600}h"
    if v % 60 == 0:
        return f"limit {v // 60}m"
    return f"limit {v}s"


def _dur(n: Any) -> str:
    """시·분·초 표기. `gitops._fmt_seconds` 와 글자까지 같다 — 같은 시간 초과를 잡 로그와 요약이
    다른 글자로 말하면 사람이 같은 사건인 줄 모른다."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "?"
    if v >= 3600 and v % 3600 == 0:
        return f"{v // 3600}h"
    if v >= 60 and v % 60 == 0:
        return f"{v // 60}m"
    return f"{v}s"


def _s(args: dict[str, Any], key: str, default: str = "?") -> str:
    v = args.get(key)
    return default if v is None else str(v)


#: 아카이브를 거절한 까닭 → 영어 문구. 키가 정본이고 문구는 여기서만 만든다 — `tarfile` 예외
#: 이름(`OutsideDestinationError`)을 그대로 실으면 파이썬을 아는 사람만 읽을 수 있고, 예외 문구를
#: 실으면 그 안에 **목적지 절대 경로**가 따라온다.
REJECT_KINDS: dict[str, str] = {
    "absolute_path": "absolute path in archive",
    "escapes_workspace": "member escapes the workspace",
    "link_outside": "link points outside the workspace",
    "absolute_link": "absolute link target in archive",
    "special_file": "device or special file in archive",
    "not_a_tarball": "not a valid tar.gz",
    "unsupported_compression": "unsupported compression",
    "truncated": "truncated archive",
    "unreadable": "unreadable archive",
}


def _rejected(a: dict[str, Any]) -> str:
    """`snapshot rejected: <까닭>[: <멤버>]`. 멤버는 basename 이다 — 어느 파일인지는 남기되
    클라이언트가 보낸 경로 전체를 공개 문서에 싣지 않는다(`clean_args` 의 `member`)."""
    kind = a.get("kind")
    text = REJECT_KINDS.get(str(kind), _s(a, "kind"))
    member = a.get("member")
    return f"{text}: {member}" if member else text


def _git(a: dict[str, Any]) -> str:
    """git 실패 한 줄. `op`(`git fetch` 같은 소스 리터럴)와 닫힌 `kind` 로만 만든다 — git 의
    stderr 는 URL·경로·자격 증명을 담고 있어 보호된 잡 로그에만 간다."""
    op = _s(a, "op", "git")
    kind = a.get("kind")
    if kind == "timeout":
        return f"{op} timed out after {_dur(a.get('seconds'))}"
    if kind == "exit":
        return f"{op} failed (exit {_s(a, 'code')}), see the job log"
    if kind == "no_git":
        return "git is not installed on the build machine"
    if kind == "spawn":
        return f"{op} could not start git: {_s(a, 'error')}"
    return f"{op} failed, see the job log"


#: 서버가 만드는 요약 전부. 새 요약을 추가할 때는 여기 코드부터 만든다.
CODES: dict[str, Renderer] = {
    # ── 취소 · 재시작 ──────────────────────────────────────────────────────
    "cancelled_before_start": lambda a: "cancelled before start",
    "cancelled_by": lambda a: f"cancelled by {_s(a, 'by')}",
    "server_restarted": lambda a: f"server restarted {_s(a, 'at')}",
    "server_restarted_during_upload": lambda a: "server restarted during upload",
    "server_stopped_while_running": lambda a: "server stopped while running",
    # ── 업로드 · 스냅샷 ────────────────────────────────────────────────────
    "upload_abandoned": lambda a: f"upload abandoned after {_secs(a.get('seconds'))}",
    "upload_interrupted": lambda a: f"upload interrupted after {_mb(a.get('bytes'))}",
    "snapshot_too_big": lambda a: f"snapshot {_mb(a.get('bytes'))} exceeds {_mb(a.get('limit'))}",
    "snapshot_rejected": lambda a: f"snapshot rejected: {_rejected(a)}",
    "snapshot_blobs_missing": lambda a: (
        f"snapshot rejected: {_s(a, 'count')} blob(s) missing in upload"
    ),
    # ── 실행 결과 ──────────────────────────────────────────────────────────
    "exit_code": lambda a: f"exit {_s(a, 'code')}",
    "timed_out": lambda a: _limit(a.get("seconds")),
    # ── 프로세스가 뜨기 전에 끝난 잡(`PREFLIGHT_CODES`) ────────────────────
    # 고칠 곳이 다르면 코드도 다르다. 스냅샷을 고칠 일 · 레포 설정을 되돌릴 일 · 프리셋 argv 를
    # 고칠 일 · 디스크를 볼 일은 서로 다른 사람의 서로 다른 조치다. 한 코드로 묶으면 화면은
    # 「무언가 실패」밖에 말하지 못하고, 코드를 아예 안 달면 「테스트가 깨졌다」와 구별되지 않는다.
    # 인자는 **경계가 정해진 값**뿐이다 — `PREFLIGHT_ARGS` 참고.
    "preset_missing": lambda a: f"preset '{_s(a, 'preset')}' is no longer configured",
    "tool_missing": lambda a: f"required tool {_s(a, 'tool')} is missing",
    "snapshot_missing": lambda a: "snapshot file is missing",
    "blob_missing": lambda a: f"snapshot blob missing {_s(a, 'sha')}",
    "repo_missing": lambda a: (
        f"repo '{_s(a, 'repo')}' is not configured on this worker"
        if a.get("where") == "worker"
        else f"repo '{_s(a, 'repo')}' is no longer configured"
    ),
    "commit_missing": lambda a: (
        f"commit {_s(a, 'sha')} not found after fetch (ref moved or was force-pushed?)"
    ),
    "git_failed": _git,
    "snapshot_download_failed": lambda a: (
        "cannot download the snapshot from the server"
        + (f" (HTTP {a['status']})" if a.get("status") is not None else "")
    ),
    "launch_executable_missing": lambda a: "the preset's command was not found",
    "launch_permission_denied": lambda a: "the preset's command is not executable",
    "launch_failed": lambda a: f"the preset's command could not be started ({_s(a, 'error')})",
    "log_unavailable": lambda a: f"the job log file could not be opened ({_s(a, 'error')})",
    "workspace_failed": lambda a: (
        "the workspace could not be prepared"
        + (f" ({a['error']})" if a.get("error") is not None else "")
    ),
    # ── 워커 ───────────────────────────────────────────────────────────────
    "worker_error": lambda a: f"worker error: {_s(a, 'detail')}",
    "worker_failed": lambda a: "failed on the worker",
    "worker_stopped_while_running": lambda a: "worker stopped while running",
    "worker_restarted_without_job": lambda a: f"worker {_s(a, 'name')} restarted without the job",
    "worker_unreachable": lambda a: f"worker {_s(a, 'name')} unreachable for {_s(a, 'seconds')}s",
    "cancel_unconfirmed": lambda a: "worker did not confirm the cancel",
}

#: 오류 필드의 코드. 파이썬 예외 문자열은 닫힌 집합이 아니라서, 종류만 코드로 말하고
#: 원문은 상세로 남긴다.
ERROR_CODES: dict[str, str] = {
    "internal_error": "internal error",
    "database_unavailable": "database unavailable",
    "sampler_failed": "sampler failed",
}

#: GPU 표본이 없는 이유. 화면이 「GPU — 없음」을 자기 말로 쓸 수 있게 한다.
GPU_NOTE_CODES: dict[str, str] = {
    "no_sampler": "no GPU sampler",
    "no_gpu": "no GPU",
    "sampler_failed": "GPU sampler failed",
}

#: 요약 한 줄의 길이 상한. 저장된 문장이 이보다 길면 자른다(기존 동작과 같다).
MAX_SUMMARY = 200

# ── 시작 전 실패의 닫힌 어휘 ────────────────────────────────────────────────

#: 프로세스가 뜨기 **전**에 끝난 잡의 코드 전부. 이 코드가 붙은 잡은 `exit_code` 가 언제나
#: `null` 이고 스크립트가 아무 말도 하지 않았다 — 스크립트는 이 집합만 보면 「내 테스트가
#: 깨졌다」와 「잡이 아예 안 돌았다」를 문장을 읽지 않고 가른다.
PREFLIGHT_CODES: tuple[str, ...] = (
    "preset_missing",
    "tool_missing",
    "snapshot_missing",
    "snapshot_rejected",
    "blob_missing",
    "repo_missing",
    "commit_missing",
    "git_failed",
    "snapshot_download_failed",
    "launch_executable_missing",
    "launch_permission_denied",
    "launch_failed",
    "log_unavailable",
    "workspace_failed",
)

#: 코드마다 공개 문서에 실을 수 있는 **인자 이름과 그 모양**. 여기 없는 키는 버려진다.
#:
#: 자유 문구를 아예 두지 않는 이유: `summary_args` 는 `summary` 와 같은 등급으로 `/api/status`·
#: `/events`·`GET /jobs/<id>` 에 실리고 읽기 인증의 기본값은 `none` 이다(PLAN 「보안」). 예외
#: 문구에는 `argv[0]`(비공개 SDK 경로 · 토큰이 박힌 러너 이름) · git stderr(URL · 자격 증명) ·
#: 목적지 절대 경로가 들어간다. **정규식으로 씻는 것은 방어가 아니다**: `~/x` · `$HOME/x` ·
#: `..\\host\\share` · `ghp_…` · 사용자명은 어떤 경로 정규식에도 안 걸린다. 그래서 문구를 씻는
#: 대신 문구를 **안 싣는다** — 원문은 토큰이 있어야 읽는 잡 로그로만 간다.
#:
#: 모양의 뜻: `class` 예외 클래스 이름 꼴, `name` 설정/잡에 이미 공개된 이름, `basename` 경로에서
#: 이름만, `member` 클라이언트가 보낸 아카이브 멤버(이름만), `sha` 16진, `git_op` `git fetch` 같은
#: 소스 리터럴, `kind` 닫힌 거절 까닭, `int` 정수. 튜플은 그 자리에서 닫힌 값 집합이다.
PREFLIGHT_ARGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "preset_missing": {"preset": "name"},
    "tool_missing": {"tool": "basename"},
    "snapshot_missing": {},
    "snapshot_rejected": {"kind": "kind", "member": "member"},
    "blob_missing": {"sha": "sha"},
    "repo_missing": {"repo": "name", "where": ("worker",)},
    "commit_missing": {"sha": "sha"},
    "git_failed": {
        "op": "git_op",
        "kind": ("timeout", "exit", "no_git", "spawn"),
        "seconds": "int",
        "code": "int",
        "error": "class",
    },
    "snapshot_download_failed": {"status": "int"},
    "launch_executable_missing": {},
    "launch_permission_denied": {},
    "launch_failed": {"error": "class"},
    "log_unavailable": {"error": "class"},
    "workspace_failed": {"error": "class"},
}

#: 이 인자가 없으면 문장이 `?` 밖에 못 말한다 — 신뢰 경계에서는 400 이다(워커가 코드만 보내고
#: 인자를 빠뜨리면 로컬 레인이 남기는 행과 달라진다). 표가 맞는지는 시험이 `render(code, {})` 에
#: `?` 가 남는지로 되짚는다 — 손으로 적은 목록은 생산자를 안 보기 때문이다.
PREFLIGHT_REQUIRED: dict[str, str] = {
    "preset_missing": "preset",
    "tool_missing": "tool",
    "blob_missing": "sha",
    "repo_missing": "repo",
    "commit_missing": "sha",
    "snapshot_rejected": "kind",
    "launch_failed": "error",
    "log_unavailable": "error",
}

#: 이름 하나의 최대 길이. 길이는 화면이 아니라 여기서 막는다 — 렌더러는 자를 줄 모른다.
MAX_ARG = 120
MAX_MEMBER = 60

#: 예외 클래스 이름 꼴(`OSError` · `FileNotFoundError`)과 이 모듈이 쓰는 소스 리터럴만.
#: 밑줄·하이픈·점을 **일부러** 뺐다: 그것까지 허용하면 `internal-runner-ghp_0123…` 같은 값이
#: 모양 검사를 통과한다 — 원격 워커는 토큰이 있을 뿐 신뢰 경계 안이 아니다.
_CLASS_RE = re.compile(r"[A-Z][A-Za-z0-9]{0,39}\Z")
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:+-]{0,59}\Z")
_SHA_RE = re.compile(r"[0-9a-f]{4,64}\Z")
_GIT_OP_RE = re.compile(r"git [a-z-]{1,12}\Z")
#: 보이지 않거나 글자 방향을 뒤집는 문자. 멤버 이름은 사람이 읽는 자리에 그대로 찍히므로 여기서
#: 지운다 — bidi 문자 하나면 화면의 파일 이름이 실제와 반대로 보인다.
_INVISIBLE_RE = re.compile("[\x00-\x1f\x7f؜​-‏ -‮⁠-⁤⁦-⁩﻿]")


class OutcomeError(ValueError):
    """코드나 인자가 이 표의 어휘가 아니다. 서버 안에서는 오타, 경계에서는 400 이다."""


def _norm_path(v: Any) -> str:
    """경로처럼 생긴 값을 다루기 전에 고른다 — 서로게이트 · 보이지 않는 문자 · `\\` 구분자.

    `\\` 를 구분자로 보는 이유: POSIX 의 `os.path.basename` 은 그것을 글자로 본다. 그래서
    `\\\\build-secret\\share\\tool` 이 통째로 「이름」이 되어 내부 호스트명이 토큰 없이 읽히는
    문서에 남는다 — 윈도 경로를 쓰는 곳이 없어도 **값을 보내는 쪽**은 있다.

    외톨이 서로게이트를 지우는 이유: `tarfile` 은 멤버 이름을 `surrogateescape` 로 읽는다. UTF-8 이
    아닌 이름이 그대로 SQLite 로 가면 인코딩이 던지고, `finish` 가 던지면 워커 레인이 `down` 이
    된다 — 잡 하나의 실패가 레인 전체를 죽인다.
    """
    s = str(v)
    s = "".join(ch for ch in s if not 0xD800 <= ord(ch) <= 0xDFFF)
    return _INVISIBLE_RE.sub("", s).replace("\\", "/")


def _clean_member(v: Any) -> str:
    """클라이언트가 보낸 아카이브 멤버 이름 → **이름만**.

    경로 전체를 싣지 않는 이유: 공격자가 고른 문자열이 토큰 없이 읽히는 문서에 그대로 남는다
    (`../\\\\private-host\\secret-share`). 이름을 아예 지우지 않는 이유: 어느 파일이 문제였는지가
    스냅샷을 고칠 사람에게 유일한 단서다 — 멤버가 여럿이면 `<path>` 한 낱말로는 못 고친다.

    `a/b/`(디렉터리 멤버)도 `b` 로 읽는다. 도구 이름(`basename`)과 다른 점이다: 저기서는 이름이
    비는 선언을 설정이 이미 거절하므로, 이름이 비면 **거절이 정답**이다.
    """
    parts = [p for p in _norm_path(v).split("/") if p not in ("", ".", "..")]
    name = parts[-1].strip() if parts else ""
    if not name:
        raise OutcomeError("member has no usable name")
    return name[:MAX_MEMBER]


def _clean_text(v: Any, pattern: re.Pattern[str], what: str) -> str:
    s = str(v).strip()
    if not pattern.fullmatch(s):
        raise OutcomeError(f"not a {what}: {s[:40]!r}")
    return s


def _clean_int(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise OutcomeError(f"not an integer: {v!r}")
    if not -1 << 31 < v < 1 << 31:
        raise OutcomeError("integer out of range")
    return v


def _clean_one(shape: str | tuple[str, ...], v: Any) -> Any:
    if isinstance(shape, tuple):
        if v not in shape:
            raise OutcomeError(f"not one of {shape}: {v!r}")
        return v
    if shape == "int":
        return _clean_int(v)
    if shape == "member":
        return _clean_member(v)
    if shape == "basename":
        # 절대경로로 선언한 도구는 **이름만** 남긴다 — 워커가 경로를 실어 보내도 새지 않는다
        # (검증 G4.15). 자르기 전에 이름을 떠야 `/opt/…/f` 가 `/opt/…` 로 남지 않는다.
        name = _norm_path(v).rsplit("/", 1)[-1].strip()[:MAX_ARG]
        if not name:
            raise OutcomeError("not a tool name")
        return name
    if shape == "kind":
        # 닫힌 까닭 열쇠, 아니면 예외 클래스 이름. 클래스 이름을 아직 받는 이유: 0.2.x 가
        # 업로드 거절을 `kind=type(e).__name__` 으로 저장했고 그 행들이 DB 에 남아 있다 —
        # 여기서 막으면 옛 잡의 요약이 `snapshot rejected: ?` 로 바뀐다.
        s = str(v).strip()
        if s in REJECT_KINDS:
            return s
        return _clean_text(s, _CLASS_RE, "rejection kind")
    shapes = {"class": (_CLASS_RE, "class name"), "name": (_NAME_RE, "name")}
    shapes["sha"] = (_SHA_RE, "hex sha")
    shapes["git_op"] = (_GIT_OP_RE, "git op")
    pattern, what = shapes[shape]
    return _clean_text(v, pattern, what)


def clean_args(code: str, args: dict[str, Any] | None, *, strict: bool = False) -> dict[str, Any]:
    """시작 전 실패의 인자를 표의 모양으로 줄인다. 모르는 키와 모양이 틀린 값은 **버린다**.

    `strict` 는 신뢰 경계에서 쓴다(원격 워커의 finish): **아는 키의 값**이 모양에 안 맞으면
    던져서 400 을 만든다 — 워커가 `tool` 자리에 objekt 를 넣었다는 것은 그 자체로 버그이고,
    조용히 반쪽짜리 요약을 저장하면 로컬 레인이 남기는 행과 달라진다.

    **모르는 키는 strict 에서도 버린다.** 던지면 그 자리가 배포 사고가 된다: 새 워커가 옛 서버에
    새 인자를 하나 실어 보내면 finish 가 400 을 받고, 잡은 안 닫힌 채 heartbeat 시한으로 `lost`
    가 된다. 버리면 요약이 한 낱말 덜 말할 뿐 잡은 닫힌다. 어차피 저장되는 값은 이 표를 지난
    것뿐이라 보안 쪽으로는 같다.

    서버 안에서(`summary()`) 부를 때는 아무것도 던지지 않는다: 요약 한 줄 때문에 잡이 종료
    상태에 못 가면 그 잡은 heartbeat 시한까지 `running` 으로 남고, 레인 하나가 아니라 큐가 막힌다.
    """
    schema = PREFLIGHT_ARGS.get(code)
    if schema is None:
        if strict:
            raise OutcomeError(f"not a preflight code: {code}")
        return dict(args or {})
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value is None:
            continue
        shape = schema.get(key)
        if shape is None:
            continue
        try:
            out[key] = _clean_one(shape, value)
        except OutcomeError:
            if strict:
                raise
    return out


def render(code: str | None, args: dict[str, Any] | None = None) -> str | None:
    """코드를 영어 문장으로. 코드가 없거나 모르는 코드면 `None` — 부르는 쪽이 물러설 수 있게."""
    if not code:
        return None
    fn = CODES.get(code)
    if fn is None:
        return None
    try:
        return fn(args or {})[:MAX_SUMMARY]
    except Exception:  # noqa: BLE001 — 인자가 이상해도 요약 때문에 잡이 깨지면 안 된다
        return None


def summary(code: str, /, **args: Any) -> tuple[str, str, dict[str, Any]]:
    """`(문장, 코드, 인자)`. 서버가 잡을 끝낼 때 세 값을 함께 남긴다.

    시작 전 실패의 인자는 여기서 **반드시** `clean_args` 를 지난다 — 생산자를 믿고 통로를 하나
    열어 두면 그 통로로 `argv[0]` 이 돌아온다. 요약을 만드는 길이 이 함수 하나뿐이라, 여기서
    막으면 새 생산자가 생겨도 자유 문구가 공개 문서로 못 간다.
    """
    if code not in CODES:
        raise OutcomeError(f"unknown outcome code: {code}")
    clean = clean_args(code, {k: v for k, v in args.items() if v is not None})
    text = render(code, clean)
    assert text is not None  # CODES 에 있으므로 None 이 나올 수 없다
    return text, code, clean


def dump_args(args: dict[str, Any] | None) -> str | None:
    """인자를 DB 에 넣을 JSON 으로. 비어 있으면 `None`."""
    if not args:
        return None
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None


def load_args(raw: Any) -> dict[str, Any]:
    """DB 의 JSON 을 인자로. 깨져 있으면 빈 사전 — 요약 때문에 화면이 죽지 않는다."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}
