"""클라이언트가 서버 버전을 따라간다 (M5q PR A · 결정 149~174).

설치된 `rcm` 은 부트로더다. 서버에 붙는 명령은 argparse 가 돌기 **전에** 서버 버전을 보고, 다르면
서버가 주는 wheel 을 받아 `$XDG_CACHE_HOME/rcm/clients/<sha256 앞 16자>/` 에 풀고 같은
인터프리터로 자기를 그 코드로 다시 실행한다(`os.execve` — PID · 종료 코드 · fd 가 이어진다). 실패는
설치본으로 계속하고 stderr 에 한 줄을 쓴다(결정 157). 정본은 `docs/m5q-workplan.md` 이고 §14 가 앞
절보다 이긴다.

`cli.py` 와 `server.py` 를 import 하지 않는다 — 콘솔 스크립트의 앞단에서 가볍게 돈다. 서버 주소
해석(mDNS 발견 포함)은 `cli.py` 가 하고 그 결과를 넘긴다.

보안(결정 169 · §4): 따라가기는 서버가 준 코드를 세션 사용자의 권한으로 실행한다. sha256 은 전송 중
깨짐을 잡는 장치이지 위조를 막는 장치가 아니다. 여기서 막는 것은 토큰이 다른 호스트로 새는 길
(리다이렉트를 따르지 않는다)과 남이 캐시를 바꿔치는 길(폴더 권한)과 이상한 값이 경로가 되는
길(형식 검사 · 이름 허용 목록)뿐이다.
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.metadata
import json
import math
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, NoReturn

import remote_ci_monitor
from remote_ci_monitor.clientwheel import PACKAGE, dist_info_name, wheel_filename
from remote_ci_monitor.config import ConfigError

try:  # POSIX 전용 — 없는 플랫폼에서는 따라가기 자체가 꺼진다(결정 172)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

DIST_NAME = "remote-ci-monitor"
ENV_MODE = "RCM_CLIENT_VERSION"
ENV_FOLLOWED = "RCM_FOLLOWED"  # BOOT 에 건네는 값 — BOOT 가 환경에서 꺼낸다
ENV_FOLLOWED_PATH = "RCM_FOLLOWED_PATH"
ENV_FOLLOWED_FROM = "RCM_FOLLOWED_FROM"  # 설치본의 버전
ENV_FOLLOW_FROM = "RCM_FOLLOW_FROM"  # 부트로더의 설치 종류(§14 A질문14)
ENV_FOLLOW_FOUND = "RCM_FOLLOW_FOUND"  # "1" 이면 RCM_SERVER 는 mDNS 로 찾은 주소
ENV_WORKER_RESTARTED_AT = "RCM_WORKER_RESTARTED_AT"  # PR B2
BOOT_ENV = (ENV_FOLLOWED, ENV_FOLLOWED_PATH, ENV_FOLLOWED_FROM, ENV_FOLLOW_FROM, ENV_FOLLOW_FOUND)
PIN_FILE = "client-version"  # $XDG_CONFIG_HOME/rcm/ 먼저, 없으면 ~/.config/rcm/
MODE_FOLLOW, MODE_INSTALLED, MODE_PIN = "follow", "installed", "pin"
#: `fullmatch` 로만 쓴다 — `match` 의 `$` 는 끝 줄바꿈 앞에서도 맞는다(§14 A질문1).
VERSION_RE = re.compile(r"[0-9]+(\.[0-9]+)*([.+-][0-9A-Za-z]+)*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
VERSION_MAX = 64  # 버전은 폴더 이름과 파일 이름이 된다
CHECK_TTL_SECONDS = 600
NEG_TTL_SECONDS = 60
HEALTH_TIMEOUT_SECONDS = 3.0  # 소켓 동작마다
HEALTH_TOTAL_SECONDS = 5.0  # health 한 번 전체
FETCH_SOCKET_SECONDS = 10.0  # 받기의 소켓 동작마다(명세 밖 — 전체 상한이 정본이다)
FETCH_TOTAL_SECONDS = 60.0  # /client/ 받기 전체
RESTART_WINDOW_SECONDS = 300  # PR B2
KEEP_VERSIONS = 3
LEFTOVER_SECONDS = 3600  # 죽은 프로세스의 .tmp-* · .trash-* 를 치우는 나이
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
HEALTH_BODY_MAX = 1024 * 1024
#: `doctor` 는 이 설치본이 무엇을 할 수 있는지 잰다 — 서버 버전으로 갈아타면 잴 대상이 바뀐다
#: (M5t-a 결정 171 행 · TR17). `setup` 은 이 머신에 설정을 쓴다 — 설정이 아직 없으니 따라갈 서버도
#: 없고, `--server` 를 받아도 그 서버의 판으로 갈아타지 않는다(TR14). `setup` 을 따로 더하는
#: 까닭: 아래 집합 줄은 scripts/mutcheck.py 의 변이 `m5q-follow-serve-follows` 가 글자 그대로 찾는다
NO_FOLLOW_COMMANDS = frozenset(
    {"serve", "worker", "token", "init", "discover", "version", "skills", "doctor"} | {"setup"}
)
MARKER, LOCK, BAD = ".rcm-client.json", ".lock", ".bad"
RECORD = "follow.json"

#: 다시 실행한 자식이 처음 도는 코드(§2.5 · §14.7). 설치본이 쓰는 짧은 코드라 M5q 이전 버전으로
#: 내려갈 때도 똑같이 돈다. 경로 넣기(표지가 있을 때만) · 잠금(best-effort) · import(실패하면
#: `.bad` 를 남기고 설치본으로)를 나눈다. 고리 막기는 `remote_ci_monitor._follow_boot` 다.
BOOT = """\
import os, sys

info = {
    k: os.environ.pop(k, None)
    for k in (
        "RCM_FOLLOWED",
        "RCM_FOLLOWED_PATH",
        "RCM_FOLLOWED_FROM",
        "RCM_FOLLOW_FROM",
        "RCM_FOLLOW_FOUND",
    )
}
p = info["RCM_FOLLOWED_PATH"] or ""
usable = (
    bool(p)
    and os.path.isfile(os.path.join(p, ".rcm-client.json"))
    and not os.path.exists(os.path.join(p, ".bad"))
)
if usable:
    sys.path.insert(0, p)
    try:
        import fcntl

        _lock = open(os.path.join(p, ".lock"), "a")
        fcntl.flock(_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        pass
loaded = False
try:
    import remote_ci_monitor
    from remote_ci_monitor.cli import main

    loaded = usable and os.path.realpath(remote_ci_monitor.__file__).startswith(
        os.path.realpath(p) + os.sep
    )
except Exception:
    pass
if not loaded:
    if usable:
        try:
            open(os.path.join(p, ".bad"), "a").close()
        except OSError:
            pass
    if usable and p in sys.path:
        sys.path.remove(p)
    for m in [m for m in sys.modules if m.split(".")[0] == "remote_ci_monitor"]:
        del sys.modules[m]
    sys.stderr.write("rcm: follow: the cached client did not load \\u2014 running installed\\n")
    import remote_ci_monitor
    from remote_ci_monitor.cli import main
remote_ci_monitor._follow_boot = {**info, "loaded": loaded}
sys.exit(main())
"""

#: 드레인 뒤에 설치본 부트로더로 다시 실행하는 코드(M5q §3.3 · PR B2). 경로를 앞에 넣지 않으니
#: `-P` 아래에서 site-packages 의 설치본이 import 된다 — 다음 판정은 언제나 설치본이 ① 부터 한다.
RESTART = "import sys\nfrom remote_ci_monitor.cli import main\nsys.exit(main())\n"

#: 이유 코드 → 문구(§2.6 · §14.5). `{}` 자리에 `detail` 이 들어간다.
_REASONS = {
    "unreachable": "cannot reach the server",
    "bad_health": "the server's health has a malformed {}",
    "no_wheel": "the server has no client wheel ({})",
    "old_server": "the server is too old to hand out its client",
    "sha_mismatch": "sha256 mismatch",
    "unpack": "cannot unpack: {}",
    "version_mismatch": "the wheel says v{}",
    "python": "needs Python {}",
    "cache_unsafe": "{}",
    "pinned_unavailable": "{}",
    "platform": "not supported on this platform",
    "exec_failed": "cannot start the cached client: {}",
    "cache_broken": "the cached client v{} did not load before",
    "boot_failed": "the cached client did not load",
}


def reason_text(code: str, detail: str = "", *, token: bool = False) -> str:
    """경고 줄과 `client` 행 꼬리의 이유 문구. `unauthorized` 는 `detail`(`HTTP 401`)이 있으면
    「토큰을 거절했다」, 없으면 「토큰이 필요하다」다 — 토큰이 틀린 사람에게 「needs a token」은
    맞지 않는 말이다(§14.5 A질문6). `token` 은 옛 호출을 위한 자리다."""
    del token
    if code == "unauthorized":
        if detail:
            return f"the server refused the token for its client ({detail})"
        return "the server needs a token to hand out its client"
    tpl = _REASONS.get(code, "{}")
    return tpl.format(detail) if "{}" in tpl else tpl


class FollowError(Exception):
    """따라가지 못한 이유. `.code` 는 §2.6 · §14.5 표의 코드다."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    @property
    def reason(self) -> str:
        return reason_text(self.code, self.detail)


class SettingError(ConfigError):
    """`RCM_CLIENT_VERSION` 이나 한 줄 파일의 값이 틀렸다(종료 2). `where` 는 변수 이름이나 경로."""

    def __init__(self, message: str, *, where: str, value: str | None, path: Path | None = None):
        super().__init__(message)
        self.where = where
        self.value = value
        self.path = path


@dataclass(frozen=True)
class Prescan:
    command: str | None  # 첫 위치 인자
    no_follow: bool
    server: str | None
    token: str | None
    client_config: str | None
    wants_help: bool  # -h · --help · --version
    has_gc_config: bool  # gc --config
    ambiguous: bool = False  # 줄인 이름이 여럿과 맞는다 — 따라가지 않는다(§14 A질문2)


@dataclass(frozen=True)
class Setting:
    mode: str  # follow | installed | pin
    pin: str | None
    source: str  # flag | env | file | default
    path: Path | None = None  # source 가 file 이면 그 경로


@dataclass(frozen=True)
class ServerInfo:
    server: str  # 정규화한 주소
    version: str
    wheel_path: str | None
    sha256: str | None
    bytes: int | None
    wheel_error: str | None  # health 의 client_wheel_error, 키가 없으면 "old_server"
    checked_at: float


@dataclass(frozen=True)
class CacheEntry:
    version: str
    sha256: str
    path: Path
    last_used: float
    bad: bool = False  # `.bad` 표지 — BOOT 가 import 에 실패한 폴더(§14.6 B질문14)


@dataclass(frozen=True)
class Plan:
    action: str  # stay | exec_cached | fetch
    target: str | None
    entry: CacheEntry | None
    warn: str | None  # §2.6 · §14.5 의 코드


@dataclass
class FollowState:
    """이 프로세스의 따라가기 결과 — `rcm check` 의 `client` 행이 읽는다."""

    setting: Setting
    install: str  # wheel | editable | source (따라간 자식이면 부트로더의 것)
    installed: str  # 설치본 버전(따라간 자식이면 BOOT 의 RCM_FOLLOWED_FROM)
    running: str  # 지금 도는 __version__
    followed: bool  # 지금 프로세스가 캐시 코드인가
    server: str | None
    server_version: str | None
    warn: str | None
    warn_text: str | None


_STATE: FollowState | None = None


def _set_state(state: FollowState | None) -> None:
    global _STATE
    _STATE = state


def current_state() -> FollowState | None:
    """이 프로세스의 결과. 인자 목록으로 부른 `main(argv)` 에는 없다(결정 162)."""
    return _STATE


def booted() -> dict[str, Any] | None:
    """BOOT 를 거친 프로세스면 그 정보. 있으면 이 프로세스는 다시 따라가지 않는다(결정 161)."""
    b = getattr(remote_ci_monitor, "_follow_boot", None)
    return b if isinstance(b, dict) else None


def _os_name() -> str:
    return os.name


# ── 값의 형식 ────────────────────────────────────────────────────────────────


def valid_version(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= VERSION_MAX
        and VERSION_RE.fullmatch(value) is not None
    )


def _is_control(ch: str) -> bool:
    o = ord(ch)
    return o < 0x20 or 0x7F <= o <= 0x9F


def clean(value: Any, limit: int = 64) -> str:
    """서버가 보낸 문자열 — 제어 문자(C0 · DEL · C1)를 지우고 64자로 자른다(§5.5)."""
    return "".join(ch for ch in str(value) if not _is_control(ch))[:limit]


def shown(value: Any, limit: int = 64) -> str:
    """사람이 적은 값 — 제어 문자를 `\\n` 꼴로 드러내 보인다. 줄바꿈 꼬리가 눈에 보여야 한다."""
    out = []
    for ch in str(value):
        out.append(ch.encode("unicode_escape").decode("ascii") if _is_control(ch) else ch)
    return "".join(out)[:limit]


def fmt_size(b: int) -> str:
    """`render_text._bytes` 와 같은 십진 눈금(`707.7 KB`). 그 모듈을 들이지 않으려고 작게 둔다 —
    두 함수의 출력이 같은지 시험이 잠근다(§14 C질문12)."""
    if b < 10**3:
        return f"{b} B"
    if b < 10**6:
        return f"{b / 10**3:.1f} KB"
    if b < 10**9:
        return f"{b / 10**6:.1f} MB"
    return f"{b / 10**9:.1f} GB"


# ── 훑기와 따라가는 명령 (§2.1 · §2.3 · §14.2) ──────────────────────────────────

_CMD_RE = re.compile(r"[a-z][a-z0-9-]*")
#: 설치본이 모르는 명령이면 이 여섯 이름만으로 견준다.
_TARGETS = frozenset(
    {"--server", "--token", "--client-config", "--no-follow", "--help", "--version"}
)
_VALUED = ("--server", "--token", "--client-config")
_TOP_DEFAULT = frozenset({"--help", "--version", "--no-follow"})


def _resolve(name: str, candidates: frozenset[str] | set[str]) -> tuple[str | None, bool]:
    """argparse 의 줄임 규칙 — 정확한 이름이 먼저, 아니면 유일한 접두. (이름, 모호한가)."""
    if name in candidates:
        return name, False
    hits = [c for c in candidates if c.startswith(name)]
    if len(hits) == 1:
        return hits[0], False
    return None, len(hits) > 1


def prescan(
    argv: Sequence[str], *, options: Mapping[str | None, frozenset[str]] | None = None
) -> Prescan:
    """argv 를 해석하지 않고 훑는다. 오류 판정은 실제로 돌 버전의 파서가 한다.

    `options` 는 설치본 파서의 긴 옵션(키 `None` 은 최상위, 명령 이름은 그 하위 파서와 그 아래
    파서들의 합)이다. 명령 앞의 토큰은 최상위 목록과, 명령 뒤의 토큰은 그 명령의 목록과 견준다.
    명령 앞의 `--server` 는 최상위 옵션이 아니라 값으로 쓰지 않는다(A121).
    """
    opts = options or {}
    top = frozenset(opts.get(None, frozenset())) | _TOP_DEFAULT
    command: str | None = None
    no_follow = wants_help = has_gc_config = ambiguous = False
    server = token = client_config = None
    args = list(argv)
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            break
        if tok == "-h":
            wants_help = True
            continue
        if command is None:
            if tok.startswith("--"):
                name, eq, _ = tok.partition("=")
                full, amb = _resolve(name, top)
                ambiguous = ambiguous or amb
                if full in ("--help", "--version"):
                    wants_help = True
                elif full == "--no-follow" and not eq:
                    no_follow = True
                continue
            if tok.startswith("-"):
                continue
            command = tok
            continue
        if not tok.startswith("--"):
            continue
        name, eq, value = tok.partition("=")
        if name == "--version":
            wants_help = True
            continue
        if command in opts:
            cands = frozenset(opts[command])
        else:
            cands = _TARGETS | ({"--config"} if command == "gc" else set())
        full, amb = _resolve(name, cands)
        ambiguous = ambiguous or amb
        if full is None:
            continue
        if full in ("--help", "--version"):
            wants_help = True
            continue
        if full == "--no-follow":
            no_follow = no_follow or not eq
            continue
        if full not in _VALUED and not (command == "gc" and full == "--config"):
            continue
        val: str | None = value if eq else None
        if not eq:
            if i < len(args):
                val = args[i]
                i += 1
        if full == "--server":
            server = val
        elif full == "--token":
            token = val
        elif full == "--client-config":
            client_config = val
        else:
            has_gc_config = True
    if command is not None and not _CMD_RE.fullmatch(command):
        command = None
    return Prescan(
        command, no_follow, server, token, client_config, wants_help, has_gc_config, ambiguous
    )


def follows(p: Prescan) -> bool:
    """§2.3 의 표. 목록 밖은 설치본이 모르는 명령까지 따라간다(결정 160)."""
    if p.command is None or p.wants_help or p.ambiguous:
        return False
    if p.command in NO_FOLLOW_COMMANDS:
        return False
    return not (p.command == "gc" and p.has_gc_config)


# ── 설정 (§2.2 · §14.3) ──────────────────────────────────────────────────────


def install_kind() -> str:
    """`wheel` · `editable` · `source`. 메타데이터가 없거나 그 버전이 도는 `__version__` 과 다르면
    `source` 다 — 체크아웃을 `git pull` 한 editable 설치나 다른 체크아웃을 가리키는 `PYTHONPATH`."""
    try:
        dist = importlib.metadata.distribution(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "source"
    if dist.version != remote_ci_monitor.__version__:
        return "source"
    try:
        raw = dist.read_text("direct_url.json")
        doc = json.loads(raw) if raw else {}
    except (OSError, ValueError):
        doc = {}
    info = doc.get("dir_info") if isinstance(doc, dict) else None
    if isinstance(info, dict) and info.get("editable") is True:
        return "editable"
    return "wheel"


def _home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("HOME") or str(Path.home()))


def pin_file_paths(environ: Mapping[str, str]) -> list[Path]:
    """한 줄 파일 후보 — `$XDG_CONFIG_HOME/rcm/client-version`(절대 경로일 때만), 그다음
    `~/.config/rcm/client-version`. 앞 파일이 없을 때만 뒤를 본다(§14 A질문10)."""
    out: list[Path] = []
    xdg = environ.get("XDG_CONFIG_HOME") or ""
    if xdg and os.path.isabs(xdg):
        out.append(Path(xdg) / "rcm" / PIN_FILE)
    legacy = _home(environ) / ".config" / "rcm" / PIN_FILE
    if legacy not in out:
        out.append(legacy)
    return out


def read_pin_file(environ: Mapping[str, str]) -> tuple[str | None, Path | None]:
    """(값, 경로). 파일이 없으면 (None, None), 비었거나 주석뿐이면 (None, 경로). 폴더 · 읽을 수 없음
    · UTF-8 아님은 경로가 든 `ConfigError` 다 — 조용히 따라가거나 조용히 멈추지 않는다."""
    for path in pin_file_paths(environ):
        if not os.path.lexists(path):
            continue
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise SettingError(
                f"{path}: cannot read: {e.strerror or e}", where=str(path), value=None, path=path
            ) from e
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise SettingError(
                f"{path}: cannot read: not UTF-8", where=str(path), value=None, path=path
            ) from e
        for line in text.splitlines():
            value = line.split("#", 1)[0].strip()
            if value:
                return value, path
        return None, path
    return None, None


def parse_mode(text: str) -> tuple[str, str | None] | None:
    """`follow` · `installed` · 버전. 대소문자를 가린다. 아니면 None."""
    if text == MODE_FOLLOW:
        return MODE_FOLLOW, None
    if text == MODE_INSTALLED:
        return MODE_INSTALLED, None
    if valid_version(text):
        return MODE_PIN, text
    return None


def resolve_setting(
    *,
    no_follow: bool,
    environ: Mapping[str, str],
    file_value: str | None,
    file_path: Path | None,
    kind: str,
) -> Setting:
    """순수 함수. `--no-follow` > `RCM_CLIENT_VERSION` > 한 줄 파일 > 설치 종류 기본값(결정 154)."""
    if no_follow:
        return Setting(MODE_INSTALLED, None, "flag")
    env = environ.get(ENV_MODE) or ""
    if env:
        parsed = parse_mode(env)
        if parsed is None:
            raise SettingError(
                f"{ENV_MODE} '{shown(env)}' is not follow, installed or a version",
                where=ENV_MODE,
                value=env,
            )
        return Setting(parsed[0], parsed[1], "env")
    if file_value:
        parsed = parse_mode(file_value)
        if parsed is None:
            raise SettingError(
                f"{file_path}: '{shown(file_value)}' is not follow, installed or a version",
                where=str(file_path),
                value=file_value,
                path=file_path,
            )
        return Setting(parsed[0], parsed[1], "file", file_path)
    return Setting(MODE_FOLLOW if kind == "wheel" else MODE_INSTALLED, None, "default")


def setting_for(
    *, no_follow: bool, environ: Mapping[str, str], kind: str
) -> tuple[Setting, str | None]:
    """(설정, 경고). 필요한 만큼만 읽는다 — `--no-follow` 면 환경 변수만(틀렸으면 경고), 환경
    변수가 있으면 한 줄 파일을 읽지 않는다(§14 A질문9)."""
    env = environ.get(ENV_MODE) or ""
    if no_follow:
        warning = None
        if env and parse_mode(env) is None:
            warning = f"ignoring invalid {ENV_MODE} '{shown(env)}' (--no-follow)"
        return Setting(MODE_INSTALLED, None, "flag"), warning
    if env:
        setting = resolve_setting(
            no_follow=False, environ=environ, file_value=None, file_path=None, kind=kind
        )
        return setting, None
    value, path = read_pin_file(environ)
    setting = resolve_setting(
        no_follow=False, environ=environ, file_value=value, file_path=path, kind=kind
    )
    return setting, None


def tilde(path: Path | str | None, environ: Mapping[str, str] | None = None) -> str:
    """홈 아래 경로는 `~` 로 줄여 쓴다."""
    if path is None:
        return ""
    env = os.environ if environ is None else environ
    text = str(path)
    home = str(_home(env)).rstrip("/")
    if home and text.startswith(home + "/"):
        return "~" + text[len(home) :]
    return text


# ── 서버 주소와 health (§2.4 · §14.4) ──────────────────────────────────────────


def normalize_server(url: Any) -> str | None:
    """기록의 키이자 요청의 바탕 주소. 스킴 · 호스트 소문자, 기본 포트를 채우고 경로 접두는 끝 `/`
    만 지운다. 쓸 수 없는 주소면 None — 따라가기를 경고 없이 건너뛰고 명령이 오늘의 문장으로
    말한다(§14 A질문11)."""
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        return None
    if url.startswith("--"):
        return None
    text = url if "://" in url else "http://" + url
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = parts.hostname
    if not host or parts.username is not None or parts.password is not None:
        return None
    if parts.query or parts.fragment:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    hostpart = f"[{host}]" if ":" in host else host
    return f"{scheme}://{hostpart}:{port}{parts.path.rstrip('/')}"


def parse_health(server: str, body: Any, *, now: float) -> ServerInfo:
    """health 본문의 형식 검사(§2.4). 어긋나면 `FollowError("bad_health", <키>)` — 이 값들은
    폴더 이름 · 파일 이름 · 요청 경로가 된다."""
    if not isinstance(body, Mapping):
        raise FollowError("bad_health", "body")
    if "version" not in body:
        raise FollowError("bad_health", "version")
    version = body["version"]
    if not valid_version(version):
        raise FollowError("bad_health", "version")
    if "client_wheel" not in body:
        return ServerInfo(server, version, None, None, None, "old_server", now)
    cw = body["client_wheel"]
    if cw is None:
        err = body.get("client_wheel_error")
        text = clean(err) if isinstance(err, str) else ""
        return ServerInfo(server, version, None, None, None, text or "unknown", now)
    if not isinstance(cw, Mapping):
        raise FollowError("bad_health", "client_wheel")
    path = cw.get("path")
    if path != "/client/" + wheel_filename(version):
        raise FollowError("bad_health", "client_wheel.path")
    sha = cw.get("sha256")
    if not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None:
        raise FollowError("bad_health", "client_wheel.sha256")
    size = cw.get("bytes")
    if type(size) is not int or size <= 0 or size > MAX_WHEEL_BYTES:
        raise FollowError("bad_health", "client_wheel.bytes")
    return ServerInfo(server, version, path, sha, size, None, now)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx 를 따르지 않는다 — 토큰을 다른 호스트로 실어 나르지 않는다(§2.4)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def no_redirect_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect())


def _headers(token: str | None, accept: str) -> dict[str, str]:
    # 토큰이 있으면 언제나 싣는다 — 클라이언트는 서버의 읽기 규칙을 미리 알 수 없다
    h = {"User-Agent": f"rcm/{remote_ci_monitor.__version__}", "Accept": accept}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


_MALFORMED = object()
_TIMEOUT = object()


def _bounded(fn: Callable[[], Any], seconds: float, name: str = "rcm-follow-health") -> Any:
    """`fn` 을 도우미 스레드에서 돌리고 `seconds` 안에 안 끝나면 `_TIMEOUT`. 조금씩 흘리는 서버도
    전체 제한을 넘기지 못한다. 기다리는 동안의 Ctrl-C 는 그대로 올라간다."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — 부른 쪽에서 다시 던진다
            box["error"] = e

    t = threading.Thread(target=run, name=name, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return _TIMEOUT
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _health_request(server: str, token: str | None, opener: Any) -> Any:
    """GET /api/health — 본문(파싱한 JSON) · `_MALFORMED` · None(못 닿음)."""
    req = urllib.request.Request(
        server + "/api/health", headers=_headers(token, "application/json")
    )
    op = opener or no_redirect_opener()
    try:
        with op.open(req, timeout=HEALTH_TIMEOUT_SECONDS) as resp:
            raw = resp.read(HEALTH_BODY_MAX)
    except urllib.error.HTTPError as e:
        # 503 이어도 본문에 version 이 있으면 쓴다(`Client.health` 와 같은 판단). 3xx 는 못 닿음
        try:
            raw = e.read(HEALTH_BODY_MAX) if e.code == 503 else b""
        except (OSError, http.client.HTTPException):
            raw = b""
        finally:
            e.close()
        try:
            doc = json.loads(raw) if raw else None
        except ValueError:
            return None
        return doc if isinstance(doc, dict) and "version" in doc else None
    except (OSError, http.client.HTTPException, ValueError):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return _MALFORMED


def _read_doc(root: Path) -> dict[str, Any]:
    try:
        with open(root / RECORD, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}  # 기록은 다시 만들 수 있는 캐시다 — 망가졌으면 빈 기록(§14 A질문22)
    return doc if isinstance(doc, dict) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def load_record(root: Path, server: str) -> tuple[ServerInfo | None, float | None]:
    """(기록, failed_at). 모양이 틀린 항목은 없는 것과 같다."""
    entry = _read_doc(root).get(server)
    if not isinstance(entry, dict):
        return None, None
    failed_at = _num(entry.get("failed_at"))
    version, checked_at = entry.get("version"), _num(entry.get("checked_at"))
    if not valid_version(version) or checked_at is None:
        return None, failed_at
    wheel = entry.get("wheel")
    if isinstance(wheel, dict):
        try:
            info = parse_health(server, {"version": version, "client_wheel": wheel}, now=checked_at)
        except FollowError:
            return None, failed_at
        return info, failed_at
    err = entry.get("wheel_error")
    err = clean(err) if isinstance(err, str) and err else "unknown"
    return ServerInfo(server, version, None, None, None, err, checked_at), failed_at


def save_record(
    root: Path, info: ServerInfo | None, *, server: str, failed_at: float | None
) -> None:
    """임시 파일에 쓰고 `os.replace`. 동시에 쓰면 나중 것이 이긴다. 실패는 조용히 넘긴다.

    `info` 가 None 이면(health 실패) 그 서버의 `version` 과 `wheel` 을 그대로 두고 `failed_at` 만
    바꾼다 — 서버가 재시작하는 동안 마지막 기록의 캐시로 돌 수 있게(§14 A질문22, 계약)."""
    tmp: str | None = None
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        doc = _read_doc(root)
        if info is not None:
            wheel = None
            if info.wheel_path is not None:
                wheel = {"path": info.wheel_path, "sha256": info.sha256, "bytes": info.bytes}
            doc[server] = {
                "version": info.version,
                "checked_at": info.checked_at,
                "failed_at": failed_at,
                "wheel": wheel,
                "wheel_error": info.wheel_error,
            }
        else:
            prev = doc.get(server) if isinstance(doc.get(server), dict) else {}
            doc[server] = {
                "version": prev.get("version"),
                "checked_at": prev.get("checked_at"),
                "failed_at": failed_at,
                "wheel": prev.get("wheel"),
                "wheel_error": prev.get("wheel_error"),
            }
        fd, tmp = tempfile.mkstemp(prefix=".follow-", suffix=".tmp", dir=root)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, sort_keys=True)
        os.replace(tmp, root / RECORD)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _fresh(at: float | None, now: float, ttl: float) -> bool:
    # 미래 시각은 만료로 본다 — 시계가 그 값을 지날 때까지 새것으로 남지 않게(§14 A질문21)
    return at is not None and 0 <= now - at < ttl


def server_info(
    server: str,
    token: str | None,
    *,
    root: Path,
    now: float,
    ttl: float,
    opener: Any = None,
) -> ServerInfo | None:
    """서버 버전. 10분 기록 → 60초 실패 기억 → health 순서다. `ttl <= 0` 이면 둘 다 무시한다
    (`rcm check` 와 워커 기동, §14 A질문13). 못 닿으면 None(`failed_at` 을 남긴다), 형식이 틀리면
    `FollowError("bad_health")` 이고 기록하지 않는다."""
    rec, failed_at = load_record(root, server)
    if ttl > 0:
        if rec is not None and _fresh(rec.checked_at, now, ttl):
            return rec
        if _fresh(failed_at, now, NEG_TTL_SECONDS):
            return None
    body = _bounded(lambda: _health_request(server, token, opener), HEALTH_TOTAL_SECONDS)
    if body is _MALFORMED:
        raise FollowError("bad_health", "body")
    if body is None or body is _TIMEOUT:
        save_record(root, None, server=server, failed_at=now)
        return None
    info = parse_health(server, body, now=now)
    save_record(root, info, server=server, failed_at=None)
    return info


# ── 캐시 (§2.5 · §2.8 · §14.6) ───────────────────────────────────────────────


def cache_root(environ: Mapping[str, str] | None = None) -> Path:
    """`$XDG_CACHE_HOME/rcm`(절대 경로일 때만), 아니면 `~/.cache/rcm`. 실제 경로로 푼다 — 권한은
    링크를 푼 자리에서 본다(결정 152 · 164)."""
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_CACHE_HOME") or ""
    base = Path(xdg) if xdg and os.path.isabs(xdg) else _home(env) / ".cache"
    return Path(os.path.realpath(base / "rcm"))


def _unsafe(path: Path) -> str | None:
    """내 소유가 아니거나 그룹 · 남이 쓸 수 있으면 그 사실. 남이 쓸 수 있는 폴더의 코드는 그
    사람의 코드다."""
    real = os.path.realpath(path)
    st = os.stat(real)
    if st.st_uid != os.geteuid():
        return f"{real} belongs to another user"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"{real} is writable by others"
    return None


def check_cache_safe(root: Path) -> None:
    """캐시 뿌리와 `clients/` — 없으면 0700 으로 만들고, 있으면 권한을 본다.

    뿌리의 부모도 본다. 남이 쓸 수 있고 sticky 가 아니면 남이 뿌리를 지우고 자기 폴더로 바꿔칠 수
    있다. 그룹 쓰기는 받는다 — 사용자마다 자기 그룹을 두는 Linux(umask 002)에서 `~/.cache` 가
    0775 다."""
    parent = Path(os.path.realpath(root)).parent
    try:
        st = os.stat(parent)
    except OSError:
        st = None
    if st is not None and st.st_mode & stat.S_IWOTH and not st.st_mode & stat.S_ISVTX:
        raise FollowError("cache_unsafe", f"{parent} is writable by others")
    for d in (root, root / "clients"):
        try:
            if not os.path.isdir(d):
                os.makedirs(d, mode=0o700, exist_ok=True)
                os.chmod(d, 0o700)
            why = _unsafe(d)
        except OSError as e:
            raise FollowError("unpack", e.strerror or str(e)) from e
        if why:
            raise FollowError("cache_unsafe", why)


def _read_marker(path: Path) -> tuple[str, str, float] | None:
    marker = path / MARKER
    try:
        st = os.stat(marker)
        doc = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    version, sha = doc.get("version"), doc.get("sha256")
    if not valid_version(version) or not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
        return None
    return version, sha, st.st_mtime


def _entry_at(path: Path, *, include_bad: bool) -> CacheEntry | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode):  # 링크 항목은 고르지 않는다
        return None
    if st.st_uid != os.geteuid() or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return None  # §14 A질문16 — 위험한 항목 폴더는 고르지도 지우지도 않는다
    mark = _read_marker(path)
    if mark is None:
        return None  # 표지가 없는 폴더는 완성본이 아니다
    bad = os.path.lexists(path / BAD)
    if bad and not include_bad:
        return None
    return CacheEntry(mark[0], mark[1], path, mark[2], bad)


def scan_cache(root: Path, *, include_bad: bool = False) -> list[CacheEntry]:
    """표지가 있는 항목, 마지막 사용(표지 mtime)이 최근인 순. 버전은 폴더 이름이 아니라 표지에서
    읽는다(§14 A질문15)."""
    clients = root / "clients"
    try:
        names = os.listdir(clients)
    except OSError:
        return []
    out = []
    for name in names:
        if name.startswith("."):
            continue
        entry = _entry_at(clients / name, include_bad=include_bad)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda e: e.last_used, reverse=True)
    return out


def _remove_tree(path: Path) -> None:
    if os.path.islink(path):
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    shutil.rmtree(path, ignore_errors=True)


def prune(root: Path, *, keep: int, protect: Path) -> list[Path]:
    """받은 직후에만 부른다(§2.8 · 결정 163). 최근 사용 `keep` 개(막 받은 것 포함) + `protect` +
    잠긴 것 + 모르는 폴더 · 파일을 남긴다. 지울 것은 `.trash-*` 로 옮긴 뒤 지운다 — 반쯤 지운
    폴더가 완성본처럼 보이지 않게. 링크 항목은 링크만 지운다. 한 시간보다 오래된 `.tmp-*` ·
    `.trash-*` 도 지운다. 지운 원래 경로를 돌려준다."""
    clients = root / "clients"
    removed: list[Path] = []
    now = time.time()
    try:
        names = os.listdir(clients)
    except OSError:
        return removed
    cands: list[tuple[float, Path, bool]] = []
    for name in names:
        path = clients / name
        if name.startswith((".tmp-", ".trash-")):
            try:
                if now - os.lstat(path).st_mtime > LEFTOVER_SECONDS:
                    _remove_tree(path)
            except OSError:
                pass
            continue
        if name.startswith("."):
            continue
        if os.path.islink(path):
            mark = _read_marker(path)
            if mark is not None:
                cands.append((mark[2], path, True))
            continue
        entry = _entry_at(path, include_bad=True)
        if entry is not None:
            cands.append((entry.last_used, path, False))
    cands.sort(key=lambda c: c[0], reverse=True)
    for i, (_, path, link) in enumerate(cands):
        if i < keep or path == protect:
            continue
        if link:
            try:
                os.unlink(path)
                removed.append(path)
            except OSError:
                pass
            continue
        if fcntl is None:
            continue
        try:
            fh = open(path / LOCK, "a")  # noqa: SIM115 — 잠금을 쥔 채 옮긴다
        except OSError:
            continue
        try:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                continue  # 누가 쓰는 중이다(`rcm wait` · `rcm top --watch`)
            trash = clients / f".trash-{secrets.token_hex(6)}"
            try:
                os.rename(path, trash)
            except OSError:
                continue
        finally:
            fh.close()
        shutil.rmtree(trash, ignore_errors=True)
        removed.append(path)
    return removed


# ── 받기 · 풀기 (§2.5 · 결정 157 · 165) ──────────────────────────────────────────

_INIT_VERSION_RE = re.compile(r"""^__version__\s*=\s*(["'])([^"'\n]*)\1""", re.M)
_REQ_RE = re.compile(r"\s*>=\s*([0-9]+)\.([0-9]+)(?:\.([0-9]+))?\s*")


def python_ok(requires: str | None, python: Sequence[int]) -> bool:
    """`Requires-Python` 의 `>=X.Y[.Z]` 한 꼴만 읽는다. 없으면 True, 다른 꼴은 False(결정 159)."""
    if requires is None:
        return True
    m = _REQ_RE.fullmatch(requires)
    if m is None:
        return False
    need = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    return tuple(python[:3]) >= need


def _download(url: str, token: str | None, dest: Path, info: ServerInfo, opener: Any) -> None:
    """받기 전체(연결 · 응답 머리 · 본문)에 `FETCH_TOTAL_SECONDS` 를 건다. 소켓 제한은 동작마다라
    머리 줄을 조금씩 흘리는 서버는 그것으로 끊기지 않는다 — 도우미 스레드에서 받고 시간이 넘으면
    `unreachable` 이다. 남은 스레드는 임시 폴더와 함께 버려진다(#226 격리 검증 D1)."""
    got = _bounded(
        lambda: _download_body(url, token, dest, info, opener),
        FETCH_TOTAL_SECONDS,
        "rcm-follow-fetch",
    )
    if got is _TIMEOUT:
        raise FollowError("unreachable")


def _download_body(url: str, token: str | None, dest: Path, info: ServerInfo, opener: Any) -> None:
    """흘려 받으며 sha256 을 계산한다. 선언한 크기를 넘으면 끊는다."""
    assert info.bytes is not None and info.sha256 is not None
    req = urllib.request.Request(url, headers=_headers(token, "application/zip"))
    op = opener or no_redirect_opener()
    try:
        resp = op.open(req, timeout=FETCH_SOCKET_SECONDS)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        if code in (401, 403):
            raise FollowError("unauthorized", f"HTTP {code}" if token else "") from None
        if code in (404, 503):
            raise FollowError("no_wheel", f"HTTP {code}") from None
        raise FollowError("unreachable") from None  # 3xx 와 나머지
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise FollowError("unreachable") from e
    digest = hashlib.sha256()
    total = 0
    with resp:
        try:
            fh = open(dest, "wb")  # noqa: SIM115
        except OSError as e:
            raise FollowError("unpack", e.strerror or str(e)) from e
        with fh:
            while True:
                try:
                    reader = getattr(resp, "read1", None) or resp.read
                    chunk = reader(65536)
                except (OSError, http.client.HTTPException) as e:
                    raise FollowError("unreachable") from e
                if not chunk:
                    break
                total += len(chunk)
                if total > info.bytes:
                    raise FollowError("sha_mismatch")  # 선언보다 길다 — 같은 바이트가 아니다
                digest.update(chunk)
                try:
                    fh.write(chunk)
                except OSError as e:
                    raise FollowError("unpack", e.strerror or str(e)) from e
    if digest.hexdigest() != info.sha256:
        raise FollowError("sha_mismatch")


def _too_big() -> str:
    return f"the wheel unpacks to more than {MAX_UNPACKED_BYTES} bytes"


class _Capped:
    """풀기의 읽기 — 실제로 푼 바이트 합이 상한을 넘으면 끊는다(헤더의 크기를 믿지 않는다)."""

    def __init__(self, fh: IO[bytes], used: list[int]):
        self.fh = fh
        self.used = used

    def read(self, n: int = -1) -> bytes:
        data = self.fh.read(n if n and n > 0 else 65536)
        self.used[0] += len(data)
        if self.used[0] > MAX_UNPACKED_BYTES:
            raise FollowError("unpack", _too_big())
        return data


def _member_ok(m: zipfile.ZipInfo, allowed: tuple[str, str], seen: set[str]) -> bool:
    """이름 허용 목록 · `..` · 절대 경로 · 역슬래시 · `.` 과 빈 조각 · 중복 · 링크 · FIFO ·
    장치 파일(§2.5 · §14 A질문17)."""
    name = m.filename
    if not name or "\\" in name or name.startswith("/") or "\0" in name:
        return False
    is_dir = name.endswith("/")
    parts = name[:-1].split("/") if is_dir else name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    if parts[0] not in allowed or (len(parts) < 2 and not is_dir):
        return False
    if name in seen:
        return False
    seen.add(name)
    kind = stat.S_IFMT(m.external_attr >> 16)
    return kind in (0, stat.S_IFREG, stat.S_IFDIR)


def _unpack(wheel: Path, tree: Path, version: str) -> None:
    """pip 없이 푼다(결정 165). 런타임 의존성이 0 이라 순수 파이썬 wheel 은 풀기만 하면 된다.
    실행 비트는 `external_attr` 에서 옮긴다 — 결과가 `pip install` 과 같게(§14 A질문17)."""
    allowed = (PACKAGE, dist_info_name(version))
    try:
        with zipfile.ZipFile(wheel) as zf:
            members = zf.infolist()
            seen: set[str] = set()
            declared = 0
            for m in members:
                if not _member_ok(m, allowed, seen):
                    why = f"the wheel has an unsafe entry: {clean(m.filename)}"
                    raise FollowError("unpack", why)
                declared += m.file_size
            if declared > MAX_UNPACKED_BYTES:
                raise FollowError("unpack", _too_big())
            tree.mkdir(mode=0o700)
            used = [0]
            for m in members:
                rel = m.filename.rstrip("/").split("/")
                target = tree.joinpath(*rel)
                if m.filename.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as src, open(target, "xb") as dst:
                    shutil.copyfileobj(_Capped(src, used), dst)
                mode = (m.external_attr >> 16) & 0o777
                os.chmod(target, 0o755 if mode & 0o111 else 0o644)
    except FollowError:
        raise
    except OSError as e:
        raise FollowError("unpack", e.strerror or str(e)) from e
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        NotImplementedError,
        ValueError,
        EOFError,
        zlib.error,
    ) as e:
        raise FollowError("unpack", clean(str(e)) or type(e).__name__) from e
    init = tree / PACKAGE / "__init__.py"
    if not init.is_file():
        raise FollowError("unpack", f"the wheel has no {PACKAGE}/__init__.py")
    m = _INIT_VERSION_RE.search(init.read_text(encoding="utf-8", errors="replace"))
    found = m.group(2) if m else None
    if found != version:
        # 다시 실행한 자식이 「아직 버전이 다르다」고 또 따라가는 고리를 여기서 끊는다(결정 161)
        raise FollowError("version_mismatch", clean(found) if found else "?")
    meta = tree / allowed[1] / "METADATA"
    reqs: list[str] = []
    if meta.is_file():
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                break  # 머리 끝 — 본문의 같은 글자는 보지 않는다
            if line.lower().startswith("requires-python:"):
                reqs.append(clean(line.split(":", 1)[1].strip()))
    v = sys.version_info
    py = f"{v.major}.{v.minor}.{v.micro}"
    if len(reqs) > 1:
        raise FollowError("python", f"{', '.join(reqs)}, this is {py}")
    if reqs and not python_ok(reqs[0], v[:3]):
        raise FollowError("python", f"{reqs[0]}, this is {py}")


def fetch(
    server: str, token: str | None, info: ServerInfo, *, root: Path, opener: Any = None
) -> CacheEntry:
    """받기 → sha256 → 풀기 → 표지 → rename 하나로 완성(§2.5). 실패는 `FollowError` 이고 임시
    폴더는 `finally` 에서 지운다 — Ctrl-C 도 같다. sha 불일치면 캐시에 아무것도 남기지 않는다."""
    if info.wheel_path is None or info.sha256 is None or info.bytes is None:
        raise FollowError("no_wheel", info.wheel_error or "unknown")
    check_cache_safe(root)
    clients = root / "clients"
    try:
        tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=clients))
    except OSError as e:
        raise FollowError("unpack", e.strerror or str(e)) from e
    try:
        wheel = tmp / "client.whl"
        _download(server + info.wheel_path, token, wheel, info, opener)
        tree = tmp / "tree"
        _unpack(wheel, tree, info.version)
        marker = {
            "version": info.version,
            "sha256": info.sha256,
            "server": server,
            "fetched_at": time.time(),
        }
        try:
            (tree / MARKER).write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
            os.chmod(tree, 0o700)
        except OSError as e:
            raise FollowError("unpack", e.strerror or str(e)) from e
        target = clients / info.sha256[:16]
        try:
            os.rename(tree, target)
            won = True
        except OSError:
            won = False  # 같은 것을 동시에 받은 다른 프로세스가 먼저 옮겼다
        # 진 쪽도 고를 때와 같은 검사를 거친다 — 그 이름에 남이 둔 링크 · 0777 폴더 · `.bad` 폴더의
        # 코드를 실행하지 않는다(#226 격리 검증 D2)
        entry = _entry_at(target, include_bad=False)
        if entry is None or (entry.version, entry.sha256) != (info.version, info.sha256):
            raise FollowError("unpack", f"{target} holds another client")
        if won:
            try:
                prune(root, keep=KEEP_VERSIONS, protect=target)
            except OSError:
                pass
        return entry
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 목표 정하기 (§2.7 · 결정 150 · 158) ─────────────────────────────────────────


def plan(
    setting: Setting,
    *,
    installed: str,
    info: ServerInfo | None,
    cached: Sequence[CacheEntry],
    os_name: str = "posix",
    reachable: bool = True,
) -> Plan:
    """순수 함수 — 시계 · 파일 · 네트워크를 보지 않는다. 버전은 크기가 아니라 문자열로만 견준다.

    `reachable=False` 면 `info` 는 health 를 못 읽었을 때의 마지막 기록이다. 그때는 버전과
    sha256 이 둘 다 맞는 캐시만 쓴다(§2.4)."""
    if os_name != "posix":
        return Plan("stay", None, None, "platform")
    if setting.mode == MODE_INSTALLED:
        return Plan("stay", None, None, None)
    good = sorted((e for e in cached if not e.bad), key=lambda e: e.last_used, reverse=True)
    if setting.mode == MODE_PIN:
        x = setting.pin
        if x == installed:
            return Plan("stay", x, None, None)
        hit = next((e for e in good if e.version == x), None)
        if hit is not None:
            return Plan("exec_cached", x, hit, None)
        if reachable and info is not None and info.version == x and info.sha256:
            broken = next((e for e in cached if e.bad and e.sha256 == info.sha256), None)
            if broken is not None:
                return Plan("stay", x, broken, "cache_broken")
            return Plan("fetch", x, None, None)
        return Plan("stay", x, None, "pinned_unavailable")
    if info is None:
        return Plan("stay", None, None, "unreachable")
    target = info.version
    if target == installed:
        return Plan("stay", target, None, None)
    if info.sha256 is None:
        # wheel 이 없다(조립 실패 · 옛 서버). 같은 버전 캐시가 있으면 가장 최근 것으로(§14 A질문19)
        hit = next((e for e in good if e.version == target), None)
        if hit is not None:
            return Plan("exec_cached", target, hit, None)
        if not reachable:
            return Plan("stay", target, None, "unreachable")
        code = "old_server" if info.wheel_error == "old_server" else "no_wheel"
        return Plan("stay", target, None, code)
    hit = next((e for e in good if e.version == target and e.sha256 == info.sha256), None)
    if hit is not None:
        return Plan("exec_cached", target, hit, None)
    if not reachable:
        return Plan("stay", target, None, "unreachable")
    broken = next((e for e in cached if e.bad and e.sha256 == info.sha256), None)
    if broken is not None:
        # 고칠 폴더를 경고 다음 줄에서 알려 줄 수 있게 그 항목을 싣는다
        return Plan("stay", target, broken, "cache_broken")
    return Plan("fetch", target, None, None)


# ── 재실행 (§2.5 · 결정 151) ─────────────────────────────────────────────────


def exec_into(
    entry: CacheEntry,
    argv: Sequence[str],
    *,
    environ: Mapping[str, str],
    installed: str,
    kind: str = "wheel",
    extra: Mapping[str, str] | None = None,
    execve: Callable[..., Any] = os.execve,
) -> NoReturn:
    """표지의 mtime 을 지금으로 바꾸고(청소가 막 쓰려는 폴더를 오래된 것으로 보지 않게) 같은
    인터프리터를 `-P -c BOOT` 로 다시 실행한다. `PYTHONPATH` 는 쓰지 않는다 — 그 변수는 rcm 이
    띄우는 모든 자식 프로세스로 샌다."""
    marker = entry.path / MARKER
    try:
        now = time.time()
        os.utime(marker, (now, now))
    except OSError as e:  # 다른 프로세스의 청소가 옮겼다 — 다시 받지 않고 설치본으로(A질문25)
        raise FollowError("exec_failed", e.strerror or str(e)) from e
    env = {k: v for k, v in environ.items() if k not in BOOT_ENV}
    env[ENV_FOLLOWED] = entry.version
    env[ENV_FOLLOWED_PATH] = str(entry.path)
    env[ENV_FOLLOWED_FROM] = installed
    env[ENV_FOLLOW_FROM] = kind
    if extra:
        env.update(extra)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass
    exe = sys.executable
    try:
        execve(exe, [exe, "-P", "-c", BOOT, *argv], env)
    except OSError as e:
        raise FollowError("exec_failed", e.strerror or str(e)) from e
    raise FollowError("exec_failed", "execve returned")


def restart_via_installed(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str],
    execve: Callable[..., Any] = os.execve,
    now: float | None = None,
) -> None:
    """같은 인터프리터를 `-P -c RESTART` 로 다시 실행한다(M5q §3.3 의 5). 따라가기 변수를 지우고
    `RCM_WORKER_RESTARTED_AT` 에 지금(epoch 초)을 남긴다. 다음 프로세스가 300초 안의 ③ 을
    드레인하지 않게 하는 고리 막기다. `OSError` 는 그대로 올린다(부르는 쪽이 종료 1 로 끝낸다).
    진짜 execve 는 돌아오지 않는다."""
    env = {k: v for k, v in environ.items() if k not in BOOT_ENV}
    env[ENV_WORKER_RESTARTED_AT] = str(int(time.time() if now is None else now))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass
    exe = sys.executable
    execve(exe, [exe, "-P", "-c", RESTART, *argv], env)


def restarted_at(environ: Mapping[str, str], now: float) -> float | None:
    """`RCM_WORKER_RESTARTED_AT` 의 시각. 없으면 None. 숫자가 아니거나 미래면 `now` 로 본다 —
    창은 읽은 때부터 300초다(§14 B질문9)."""
    raw = environ.get(ENV_WORKER_RESTARTED_AT)
    if raw is None:
        return None
    try:
        at = float(raw)
    except ValueError:
        return now
    if not math.isfinite(at) or at > now:
        return now
    return at


# ── 한 명령의 따라가기 (§2.1) ────────────────────────────────────────────────


def _warn(state: FollowState, code: str, detail: str, err: IO[str]) -> FollowState:
    """경고 한 줄(§5.5). 서버 버전을 알면 `server v<Y>`, 모르거나 형식이 틀렸으면 주소."""
    text = reason_text(code, detail)
    state.warn, state.warn_text = code, text
    head = f"server v{state.server_version}" if state.server_version else state.server
    err.write(f"rcm: not following {head}: {text} — running installed v{state.installed}\n")
    try:
        err.flush()
    except (OSError, ValueError):
        pass
    return state


def _ask(
    server: str, token: str | None, root: Path, now: float, fresh: bool, opener: Any
) -> tuple[ServerInfo | None, bool]:
    """(서버 정보, 지금 닿았나). 못 닿으면 그 서버의 마지막 기록을 돌려준다."""
    ttl = 0 if fresh else CHECK_TTL_SECONDS
    info = server_info(server, token, root=root, now=now, ttl=ttl, opener=opener)
    if info is not None:
        return info, True
    rec, _ = load_record(root, server)
    return rec, False


def maybe_follow(
    argv: Sequence[str],
    *,
    setting: Setting,
    server: str,
    token: str | None,
    discovered: bool,
    fresh: bool,
    environ: Mapping[str, str] | None = None,
    execve: Callable[..., Any] | None = None,
    now: Callable[[], float] | None = None,
    opener: Any = None,
    err: IO[str] | None = None,
    kind: str = "wheel",
    installed: str | None = None,
    os_name: str | None = None,
) -> FollowState:
    """따라가야 하면 exec 하고 돌아오지 않는다. 아니면 이 프로세스의 상태를 돌려준다(설치본으로
    계속한다). 실패는 경고 한 줄과 함께 설치본이다(결정 157)."""
    env = os.environ if environ is None else environ
    run = os.execve if execve is None else execve
    clock = time.time if now is None else now
    out = sys.stderr if err is None else err
    mine = installed or remote_ci_monitor.__version__
    norm = normalize_server(server)
    state = FollowState(setting, kind, mine, mine, False, norm, None, None, None)
    _set_state(state)
    if setting.mode == MODE_INSTALLED or norm is None:
        return state
    osn = os_name or _os_name()
    if osn != "posix" or not sys.executable:
        return _warn(state, "platform", "", out)
    root = cache_root(env)
    extra = {"RCM_SERVER": server, ENV_FOLLOW_FOUND: "1"} if discovered else None
    try:
        cached = scan_cache(root, include_bad=True)
        info: ServerInfo | None = None
        reachable = False
        if setting.mode == MODE_PIN:
            p = plan(setting, installed=mine, info=None, cached=cached, reachable=False)
            if p.warn == "pinned_unavailable":
                info, reachable = _ask(norm, token, root, clock(), fresh, opener)
                p = plan(setting, installed=mine, info=info, cached=cached, reachable=reachable)
        else:
            info, reachable = _ask(norm, token, root, clock(), fresh, opener)
            p = plan(setting, installed=mine, info=info, cached=cached, reachable=reachable)
        if reachable and info is not None:
            state.server_version = info.version
        if p.action == "stay":
            if p.warn is None:
                return state
            detail = ""
            if p.warn == "pinned_unavailable":
                tail = f"runs v{info.version}" if reachable and info else "cannot be reached"
                detail = f"v{setting.pin} is not installed or cached and the server {tail}"
            elif p.warn == "no_wheel" and info is not None:
                detail = info.wheel_error or "unknown"
            elif p.warn == "cache_broken":
                detail = p.target or ""
            _warn(state, p.warn, detail, out)
            if p.warn == "cache_broken" and p.entry is not None:
                # 그 폴더를 지우면 다음 명령이 다시 받는다 — 사람이 할 일을 한 줄로
                out.write(f"rcm: remove {p.entry.path} to fetch it again\n")
                out.flush()
            return state
        check_cache_safe(root)
        if p.action == "exec_cached":
            assert p.entry is not None
            exec_into(
                p.entry, argv, environ=env, installed=mine, kind=kind, extra=extra, execve=run
            )
            return state  # 가짜 execve 가 돌아온 경우(시험)
        assert info is not None and info.bytes is not None
        entry = fetch(norm, token, info, root=root, opener=opener)
        out.write(
            f"rcm: following server v{info.version} (installed v{mine}) — "
            f"fetched {fmt_size(info.bytes)}\n"
        )
        out.flush()
        exec_into(entry, argv, environ=env, installed=mine, kind=kind, extra=extra, execve=run)
        return state
    except FollowError as e:
        return _warn(state, e.code, e.detail, out)


def boot_state(environ: Mapping[str, str] | None = None) -> FollowState | None:
    """따라간 자식(BOOT 를 거친 프로세스)의 상태 — `rcm check` 꼬리용. BOOT 가 설치본으로 돌아간
    자식은 `loaded` 가 거짓이다."""
    b = booted()
    if b is None:
        return None
    env = os.environ if environ is None else environ
    kind = b.get(ENV_FOLLOW_FROM) or "wheel"
    installed = b.get(ENV_FOLLOWED_FROM) or remote_ci_monitor.__version__
    try:
        setting, _ = setting_for(no_follow=False, environ=env, kind=kind)
    except ConfigError:
        setting = Setting(MODE_FOLLOW, None, "default")
    loaded = bool(b.get("loaded"))
    return FollowState(
        setting,
        kind,
        installed,
        remote_ci_monitor.__version__,
        loaded,
        None,
        None,
        None if loaded else "boot_failed",
        None if loaded else reason_text("boot_failed"),
    )
