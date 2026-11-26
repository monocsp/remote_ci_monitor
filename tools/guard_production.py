"""운영 rcm 설치를 개발 세션이 건드리지 못하게 막는 PreToolUse 훅.

`.claude/settings.json` 이 Bash · Write · Edit 앞에서 이 파일을 부른다. 훅 입력 JSON 을
stdin 으로 받아 판정 JSON 을 stdout 으로 낸다.

운영 설치는 기계에서 스스로 찾는다. **서비스 venv** 는 「PATH 의 `rcm` 이 있는 venv」가
아니다 — 워크트리 `.venv` 를 활성화한 셸에서는 그게 워크트리 빌드라 규칙이 스스로를 무력화한다
(2026-09-10 의 사고 모양, 리뷰 #89 B-1). 서비스 venv 는 **editable 설치(`direct_url.json`)가
본 체크아웃(링크된 워크트리가 아닌 것)을 가리키고 그 체크아웃 밖에 있는 venv** 다. 후보는
문서의 자리(`~/.local/share/rcm-venv`) → 설치된 서비스 유닛이 적은 `rcm` → PATH 의 `rcm`
전부의 순서로 보고 첫 것을 잡는다. 앞의 둘은 서비스가 실행하는 것이라 링크된 워크트리나
체크아웃 안의 `.venv` 여도 운영이다. 그 원본 폴더가 「운영 체크아웃」이다. 그런 설치가 없는
컴퓨터(이 레포를 clone 한 다른 사람)에서는 아무것도 막지 않는다.

판정 `decide()` 는 운영 설치를 인자(`Production`)로, 환경을 인자(`environ`)로 받는다 — 시계도
운영 설치도 안 본다. 읽는 파일은 명령이 이름 댄 설정 파일(`--config` · `./rcm.toml` ·
`$XDG_CONFIG_HOME/rcm/server.toml`)과 PATH 의 실행 파일(`_which`)뿐이다.
발견 `find_production()` 이 기계를 본다.

`rcm setup` 에는 규칙 셋이 있다(M5t-a §4.1): 운영 뿌리(편집 규칙과 같은 `production_root`)나
서비스 관리자 자리에 쓰는 것, 운영 라벨의 서비스 파일을 쓰는 것, 운영 `data_dir`(그 안 포함)을
고르는 것을 막는다. setup 은 DB 를 열지 않으므로 `DB_OPENING` · `MIGRATING` 에는 넣지 않는다.
setup 판정은 허용 목록이다(P3 4라운드): setup 에 닿을 수 있는 명령(`_setup_net`)은 명령 글 전체가
평범한 setup 명령 하나(`_plain_setup` — 셸 메타 문자 · 역슬래시 · 글롭이 없고 경로는 절대 경로나
`~`)일 때만 해석기에 묻고, 그 밖에는 막는다. 경로는 글자로도, 심볼릭 링크를 푼 경로로도 견준다.

운영 설치가 없어도 하나는 본다 — **다른 빌드가 옛 DB 를 그 자리에서 마이그레이션하는 것**(사건 6f,
2026-11-20: 검증 에이전트가 dev 워크트리 안에서 `--config` 없이 `rcm token add` · `rcm serve` 를
돌려 그 폴더 `rcm.toml` 의 개발 DB 를 v7 → v24 로 올렸다). `rcm serve|worker|token` 의 유효
`data_dir` 에 있는 `rcm.sqlite3` 의 헤더(user_version)와 실행 파일의 빌드가 아는 `DB_VERSION` 을
**파일로만** 읽어(sqlite 로 연결하지 않는다) 낮으면 막는다. 명령 앞의 `RCM_ALLOW_MIGRATE=1` 이
「그게 목적이다」라는 표시다. 둘 중 하나라도 모르면 막지 않는다.

알려진 한계(실수 방지용이지 적대자 방어가 아니다): `bash -c '…'` · `sh -c` · `xargs` ·
`uv run` · `$(…)` 안의 명령은 보지 않는다. 심링크 별칭(`/tmp` ↔ `/private/tmp`, 운영 data_dir
로 가는 링크)은 못 본다 — 경로는 `normpath` 로만 비교한다. `$VAR` 은 훅 프로세스의 환경으로
펼치고, 같은 명령줄 앞 조각의 `VAR=…; …` 정의는 못 본다. `pushd` 는 `cd` 가 아니다. `rcm setup`
은 다르다: 위 한계를 셸 해석으로 메우지 않고, 평범한 명령 하나가 아닌 setup 을 막는다. 어디에
쓰는지는 setup 의 해석기(`remote_ci_monitor.core.setup.where`)를 들여와 묻고, 못 들여오면
막는다(fail closed). setup 에도 남는 한계: 스크립트 · 별칭 · `python -c` 프로그램 안에서 만든
setup 호출, 세션 환경의 변수를 모르는 감싸기 뒤에서 명령으로 쓰는 것(`caffeinate $R …`)은
보지 못한다. macOS 에서는 경로를 대소문자 구분 없이 견준다(`FOLD_CASE`).

남는 위험 — setup 자신이 막는다(결정 352 · 353). 이 훅은 셸 글을 판정하므로 닫힌 목록(런처 ·
메타 문자 · 낱말 자리)마다 바깥이 있다. 변수가 명령 자리에 오는 꼴(`R=rcm; $R setup`), `sed … |
sh`, 인터프리터가 명령을 만드는 꼴(`python3 -c 'os.system(…)'`), 명령 낱말의 글롭 · `r(c)m`, 목록
밖의 런처는 고치지 않는다. 훅은 1차 방어이고, `rcm setup` 은 쓰기 직전에 이 머신의 rcm 서비스
(launchd · systemd 의 사용자 · 시스템 자리)가 쓰는 설정 · data_dir · venv · 체크아웃 · 라벨을 스스로
알아보고 거절한다(`remote_ci_monitor.setup._check_production`).

훅 자체가 터지면 막지 않고 경고만 남긴다. 모든 Bash 를 막는 가드는 가드가 아니라 고장이다.
다만 `rcm setup` 에 닿을 수 있는 명령(`_setup_net`)의 판정 중에 터지면 막는다 — setup 이
어디에 쓸지 모르기 때문이다(P3 3라운드).
"""

from __future__ import annotations

import fnmatch
import functools
import importlib
import json
import os
import re
import shlex
import shutil
import sys
import unicodedata
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # 3.10 이하 — data_dir 은 설정 대신 기본 경로로만 찾는다
    tomllib = None  # type: ignore[assignment]

#: 파일을 지우거나 옮기는 명령. 운영 경로가 인자로 보이면 막는다.
DESTRUCTIVE = frozenset({"rm", "rmdir", "mv", "shred", "truncate", "dd"})

#: 브랜치를 바꾸거나 커밋을 만드는 git 하위 명령. 운영 체크아웃에서는 막는다.
GIT_MUTATING = frozenset(
    {
        "switch",
        "checkout",
        "reset",
        "rebase",
        "merge",
        "restore",
        "clean",
        "stash",
        "cherry-pick",
        "revert",
        "commit",
        "add",
    }
)

#: 서비스를 멈추거나 다시 띄우는 하위 명령. 배포라서 막지 않고 물어본다.
LAUNCHCTL_MUTATING = frozenset(
    {"kickstart", "bootout", "bootstrap", "load", "unload", "stop", "kill", "remove"}
)
SYSTEMCTL_MUTATING = frozenset({"start", "stop", "restart", "reload", "enable", "disable"})

#: examples/ 의 유닛이 쓰는 서비스 이름.
SERVICE_WORDS = ("com.remote-ci-monitor", "rcm-server", "rcm-worker")

DOCS = "docs/operating.md (Upgrade → From a git checkout)"
#: 운영 데이터 디렉터리 안의 DB 를 **여는** rcm 하위 명령(M5i 결정 75). `token` 은 `list` 도
#: Store 를 열어 마이그레이션하므로 전부 쓰기다. `gc --dry-run --config` 는 사본 위에서 돌아 허용.
DB_OPENING = frozenset({"token"})
#: 열면서 옛 DB 를 마이그레이션하는 하위 명령(사건 6f). `gc` 는 넣지 않는다 — `gc --dry-run
#: --config` 는 임시 사본 위에서 돌고(결정 73), 나머지 `gc` 는 서버에 HTTP 로 부탁할 뿐 DB 를
#: 열지 않는다(`cli.cmd_gc`).
MIGRATING = frozenset({"serve", "worker", "token"})
#: 명령 앞에 붙이면 마이그레이션이 의도라는 뜻이다.
ALLOW_MIGRATE = "RCM_ALLOW_MIGRATE"
DB_FILE = "rcm.sqlite3"
SQLITE_MAGIC = b"SQLite format 3\x00"
DB_VERSION_RE = re.compile(r"^DB_VERSION\s*=\s*(\d+)\s*$", re.M)
DEFAULT_DATA_DIR = "~/.local/share/rcm"
#: docs/operating.md 「From a git checkout」이 서비스 venv 를 두라고 하는 자리.
SERVICE_VENV = "~/.local/share/rcm-venv"
#: 설치된 서비스 유닛 — 서비스가 실제로 실행하는 `rcm` 의 venv 가 후보다.
SERVICE_UNITS = (
    "~/Library/LaunchAgents/com.remote-ci-monitor.server.plist",
    "/Library/LaunchDaemons/com.remote-ci-monitor.server.plist",
    "~/.config/systemd/user/rcm-server.service",
    "/etc/systemd/system/rcm-server.service",
)
#: `rcm setup` 의 표준 라벨 — 운영 서비스가 쓰는 이름이다(`examples/` · `SERVICE_UNITS`).
SETUP_STANDARD_LABELS = ("com.remote-ci-monitor.server", "com.remote-ci-monitor.worker")
#: systemd 유닛 이름 규칙(M5t-a 6-B-V1) — 표준 라벨은 `examples/systemd/` 의 이름, 다른 라벨은
#: `<label>.service`. 훅은 패키지를 들여오지 않으므로 `remote_ci_monitor/core/setup.py` 의 같은
#: 규칙을 여기 따로 둔다 — tests/test_guard_production_setup.py 가 둘을 맞춘다.
_STANDARD_UNITS = {
    "com.remote-ci-monitor.server": "rcm-server",
    "com.remote-ci-monitor.worker": "rcm-worker",
}
#: 서비스 관리자가 서비스 파일을 읽는 자리(launchd · systemd, 사용자 · 시스템). `SERVICE_UNITS` 가
#: 모두 이 안에 있다. `rcm setup` 의 설정 · 서비스 파일이 이 안이면 거절한다 — 운영 서비스 파일
#: (`com.remote-ci-monitor.*.plist` · `rcm-*.service`)과 그 drop-in(`<유닛>.d/`)을 라벨과 상관없이
#: 덮을 수 있고, setup 자신도 이 자리에는 쓰지 않는다(§4.1 「설치 자리에 쓰지 않는다」)
SETUP_SERVICE_PLACES = (
    "~/Library/LaunchAgents",
    "/Library/LaunchAgents",
    "/Library/LaunchDaemons",
    "~/.config/systemd/user",
    "/etc/systemd/user",
    "/etc/systemd/system",
)
#: 서버 데이터 폴더를 고르는 setup 역할(역할 2 · 4). 역할을 안 주면 setup 이 물을 수 있어
#: 이쪽으로 본다.
SETUP_SERVER_ROLES = ("coordinator", "coordinator-worker")
SETUP_ROLE_LABELS = {
    "coordinator": SETUP_STANDARD_LABELS[0],
    "coordinator-worker": SETUP_STANDARD_LABELS[0],
    "worker": SETUP_STANDARD_LABELS[1],
}
#: 명령 앞에서 떼어 내는 감싸기 — 뒤의 명령을 그대로 실행한다. 이름(basename)으로 본다
#: (`/usr/bin/env`).
WRAPPERS = frozenset({"sudo", "env", "exec", "command", "nohup", "time", "builtin"})
#: 대소문자를 가리지 않는 파일시스템으로 보나 — macOS 의 기본 볼륨(APFS · HFS+)은 가리지 않는다.
#: 볼륨마다 재는 길을 파이썬이 내주지 않으므로 darwin 이면 늘 접는다(막는 쪽). setup 의
#: `core.setup.folds_case` 와 같은 규칙이고, `core.where` 에 이 값을 준다. 시험이 바꿔 끼운다
FOLD_CASE = sys.platform == "darwin"
#: 맨 이름(`rcm`)을 실행 파일로 푸는 함수 — 테스트가 바꿔 끼운다.
_which = shutil.which


@dataclass(frozen=True)
class Production:
    """이 컴퓨터의 운영 설치. 못 찾은 자리는 None 이고, 전부 None 이면 훅은 침묵한다."""

    checkout: Path | None = None
    venv: Path | None = None
    config_dir: Path | None = None
    data_dir: Path | None = None

    @property
    def known(self) -> bool:
        return any((self.checkout, self.venv, self.config_dir, self.data_dir))

    def labelled(self) -> tuple[tuple[str, Path], ...]:
        """(사람에게 보일 이름, 경로) — 메시지에 그대로 쓴다."""
        pairs = (
            ("the production checkout", self.checkout),
            ("the service virtualenv", self.venv),
            ("the server's config directory", self.config_dir),
            ("the server's data directory", self.data_dir),
        )
        return tuple((name, path) for name, path in pairs if path is not None)


@dataclass(frozen=True)
class Verdict:
    """`deny` 는 막고 `ask` 는 사람에게 물어본다. reason 은 다음 행동까지 말한다."""

    decision: str
    reason: str


# ── 경로 다루기 ──────────────────────────────────────────────────────────────


def _norm(path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def _as_path(token: str, cwd: Path) -> Path | None:
    """명령의 토큰 하나를 경로로 읽는다. 플래그·빈 문자열은 경로가 아니다."""
    text = token.strip().strip("'\"")
    if not text or text.startswith("-"):
        return None
    text = os.path.expandvars(os.path.expanduser(text))
    try:
        candidate = Path(text)
    except (OSError, ValueError):
        return None
    return _norm(candidate if candidate.is_absolute() else cwd / candidate)


def _key(path: Path) -> Path:
    """견줄 때의 경로 — `normpath`, 대소문자를 가리지 않는 디스크면 접은 것(유니코드 정규화도)."""
    text = os.path.normpath(str(path))
    if FOLD_CASE:
        text = unicodedata.normalize("NFC", text).casefold()
    return Path(text)


def inside(path: Path | None, parent: Path | None) -> bool:
    """`parent` 안에 있나. 이름이 겹치는 형제 폴더(`…-dev`)는 밖이다. macOS 에서는
    `~/.config/RCM` 도 `~/.config/rcm` 안이다(`FOLD_CASE`)."""
    if path is None or parent is None:
        return False
    try:
        _key(path).relative_to(_key(parent))
    except ValueError:
        return False
    return True


def production_root(path: Path, prod: Production) -> tuple[str, Path] | None:
    """`path` 가 운영 뿌리(체크아웃 · 서비스 venv · 설정 폴더 · data_dir) 안이면 (이름, 뿌리).

    이 집의 「운영」 정의는 이것 하나다 — 편집 규칙(`_edit_verdict`), 지우기 규칙(`_hit`),
    `rcm setup` 규칙(`_setup_place_verdict`)이 모두 이 함수를 부른다. setup 규칙이 설정 폴더만
    따로 「운영」으로 들고 있다가 체크아웃 · venv · data_dir 에 쓰는 setup 을 지나보냈다(P3
    3라운드 v1-1)."""
    for name, root in prod.labelled():
        if inside(path, root):
            return name, root
    return None


def _hit(tokens: list[str], cwd: Path, prod: Production) -> tuple[str, Path] | None:
    """토큰들 중 운영 경로를 가리키는 첫 자리를 돌려준다."""
    for token in tokens:
        path = _as_path(token, cwd)
        if path is None:
            continue
        found = production_root(path, prod)
        if found is not None:
            return found[0], path
    return None


# ── 명령 쪼개기 ──────────────────────────────────────────────────────────────


def _split_segments(command: str) -> list[str]:
    """`;` `&&` `||` `|` 로 나눈다. 각 조각을 따로 판정한다."""
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
            buf.append(char)
            index += 1
            continue
        if char in ";\n" or command.startswith(("&&", "||"), index) or char == "|":
            out.append("".join(buf))
            buf = []
            index += 2 if command.startswith(("&&", "||"), index) else 1
            continue
        buf.append(char)
        index += 1
    out.append("".join(buf))
    return [segment for segment in out if segment.strip()]


@dataclass(frozen=True)
class SegmentEnv:
    """조각 하나가 CLI 에 주는 환경: 조각 앞의 `VAR=value`·`env VAR=value` 가 세션 환경 위에
    얹히고, `env -u NAME` 은 그 이름을 지우고, `env -i` 는 세션 환경을 통째로 버린다."""

    session: Mapping[str, str]
    vars: dict[str, str] = field(default_factory=dict)
    unset: frozenset[str] = frozenset()
    cleared: bool = False

    def get(self, name: str) -> str | None:
        """값. 빈 문자열도 값이다(CLI 의 `_env_overrides` 는 `in os.environ` 으로 본다)."""
        if name in self.vars:
            return self.vars[name]
        if self.cleared or name in self.unset:
            return None
        return self.session.get(name)


def _tokens(segment: str) -> list[str]:
    """조각을 토큰으로. 앞의 `(`·`{` 와 뒤의 `)`·`}`·`&` 는 감싸기라 벗긴다."""
    text = segment.strip()
    while text[:1] in ("(", "{"):
        text = text[1:].strip()
    while text.rstrip("; \t")[-1:] in (")", "}", "&"):
        text = text.rstrip("; \t")[:-1].rstrip()
    try:
        return shlex.split(text, comments=True)
    except ValueError:
        return text.split()


#: timeout 의 시간 낱말(`30` · `1.5m` · `2h`). GNU timeout 은 strtod 로 읽으므로 `.5` · `1.` ·
#: `1e1` · `inf` · `infinity` 도 시간이다(P6 재확인 v1-6 · v2-5)
_DURATION = re.compile(
    r"(?:(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|inf(?:inity)?)[smhd]?", re.IGNORECASE
)
#: 값을 다음 낱말로 받는 timeout 플래그
_TIMEOUT_VALUE_FLAGS = frozenset({"-s", "--signal", "-k", "--kill-after"})
#: nice 의 조정값을 붙여 쓴 꼴(`-5` · `-n5` · `-n-5` · `--adjustment=5`)
_NICE_GLUED = re.compile(r"-n?-?\d+|--adjustment=-?\d+")


def _split_string(tokens: list[str], index: int) -> list[str] | None:
    """`tokens[index]` 가 `env` 의 `-S STRING` · `-SSTRING` · `--split-string[=]STRING` 이면 그
    글을 낱말로 나눠 그 자리에 끼운 토큰 목록, 아니면 None."""
    head = tokens[index]
    if head in ("-S", "--split-string"):
        text, used = (tokens[index + 1] if index + 1 < len(tokens) else ""), 2
    elif head.startswith("--split-string="):
        text, used = head.split("=", 1)[1], 1
    elif head.startswith("-S"):
        text, used = head[2:], 1
    else:
        return None
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    return tokens[:index] + words + tokens[index + used :]


def _strip_prefix(tokens: list[str]) -> tuple[list[str], dict[str, str], set[str], bool]:
    """(argv, 조각 앞의 VAR=value, `-u` 로 지운 이름, `-i` 가 있었나). `env` 의 `-i`·`-u NAME`·
    `--unset=NAME`·`-S STRING`·`--` 를 알고, `sudo`·`exec`·`nohup` 같은 감싸기와 `nice`(조정값)
    · `timeout`(플래그와 시간 하나)을 뗀다(P6 재확인 7 · v2-7)."""
    env: dict[str, str] = {}
    unset: set[str] = set()
    cleared = False
    in_env = False
    index = 0
    while index < len(tokens):
        head = tokens[index]
        if _name(head) in WRAPPERS:
            in_env = _name(head) == "env"
            index += 1
            continue
        if _name(head) == "nice":
            in_env, index = False, index + 1
            if index < len(tokens) and tokens[index] in ("-n", "--adjustment"):
                index += 2
            elif index < len(tokens) and _NICE_GLUED.fullmatch(tokens[index]):
                index += 1
            continue
        if _name(head) in ("timeout", "gtimeout"):
            in_env, index = False, index + 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 2 if tokens[index] in _TIMEOUT_VALUE_FLAGS else 1
            if index < len(tokens) and _DURATION.fullmatch(tokens[index]):
                index += 1
            continue
        if in_env:
            split = _split_string(tokens, index)
            if split is not None:
                tokens = split
                continue
        if in_env and head in ("-i", "--ignore-environment", "-"):
            cleared = True
            index += 1
            continue
        if in_env and head in ("-u", "--unset"):
            if index + 1 < len(tokens):
                unset.add(tokens[index + 1])
            index += 2
            continue
        if in_env and head.startswith("--unset="):
            unset.add(head.split("=", 1)[1])
            index += 1
            continue
        if in_env and head == "--":
            index += 1
            in_env = False
            continue
        name, sep, value = head.partition("=")
        if sep and not head.startswith("-") and "/" not in name:
            env[name] = value
            index += 1
            continue
        break
    return tokens[index:], env, unset, cleared


def _env_of(segment: str, session: Mapping[str, str]) -> SegmentEnv:
    """조각 앞의 `VAR=value` 와 `env` 의 플래그가 만드는 환경 — CLI 가 읽는 `RCM_CONFIG` ·
    `RCM_SERVER_DATA_DIR` · `XDG_CONFIG_HOME` 이 여기 온다."""
    _, env, unset, cleared = _strip_prefix(_tokens(segment))
    return SegmentEnv(session, env, frozenset(unset), cleared)


def _argv(segment: str) -> list[str]:
    """조각 하나를 argv 로. 앞의 감싸기(`sudo`·`env …`·`exec`·`nohup`·`time`·`command`)와
    `VAR=value` 는 떼어 낸다."""
    return _strip_prefix(_tokens(segment))[0]


def _name(token: str) -> str:
    return Path(token).name


def _is_pip(argv: list[str]) -> bool:
    head = _name(argv[0]) if argv else ""
    if head.startswith("pip"):
        return True
    if head.startswith("python") and argv[1:3] == ["-m", "pip"]:
        return True
    return head == "uv" and len(argv) > 1 and argv[1] == "pip"


def _rcm_subcommand(argv: list[str]) -> str | None:
    """`rcm serve` 처럼 이 도구를 실행하는 조각이면 하위 명령을 돌려준다. `python -u -m
    remote_ci_monitor` 처럼 인터프리터 플래그가 있어도 같다(`_subcommand_index`)."""
    index = _subcommand_index(argv)
    return None if index is None else argv[index]


def _option_value(argv: list[str], names: tuple[str, ...]) -> str | None:
    for index, token in enumerate(argv):
        if token in names:
            return argv[index + 1] if index + 1 < len(argv) else None
        for name in names:
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return None


def _git_target(argv: list[str], cwd: Path) -> tuple[str | None, Path]:
    """(하위 명령, 대상 레포). `-C` 가 있으면 그 폴더가 대상이다."""
    target = cwd
    subcommand: str | None = None
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "-C" and index + 1 < len(argv):
            resolved = _as_path(argv[index + 1], cwd)
            if resolved is not None:
                target = resolved
            index += 2
            continue
        if token in ("-c", "--git-dir", "--work-tree"):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        subcommand = token
        break
    return subcommand, target


# ── 판정 (순수) ──────────────────────────────────────────────────────────────


def _edit_verdict(file_path: str, cwd: Path, prod: Production) -> Verdict | None:
    target = _as_path(file_path, cwd)
    if target is None:
        return None
    found = production_root(target, prod)
    if found is None:
        return None
    name, root = found
    if root == prod.checkout:
        return Verdict(
            "deny",
            f"{target} is inside the production checkout ({prod.checkout}): the build machine "
            "runs this folder through an editable install, so editing it changes what is running "
            "right now, and the next `git pull` will conflict. Make the change in a worktree, "
            f"open a pull request to `dev`, and let it reach `main`. See {DOCS}.",
        )
    return Verdict(
        "deny",
        f"{target} is inside {name} ({root}). A development session does not write there; "
        f"give a test server its own config, port and data_dir instead. See {DOCS}.",
    )


def _bash_verdict(
    command: str, cwd: Path, prod: Production, local_config: bool, session: Mapping[str, str]
) -> Verdict | None:
    """조각마다 판정한다. `cd <dir>` 은 뒤 조각의 cwd 를 바꾸고, 그러면 `./rcm.toml` 이 있는지도
    그 폴더에서 다시 본다(`(cd …)` 서브셸의 `cd` 도 뒤로 새지만 그쪽이 안전한 방향이다).

    `rcm setup` 은 조각으로 나누지 않는다. 운영 설치가 있고 명령이 setup 에 닿을 수 있으면
    (`_setup_net`) 명령 글 전체가 평범한 setup 명령 하나여야 한다(`_setup_verdict`, P3 4라운드)."""
    if prod.known and _setup_net(command):
        return _setup_verdict(command, cwd, prod, session)
    for segment in _split_segments(command):
        argv = _argv(segment)
        if not argv:
            continue
        if argv[0] == "cd":
            target = _as_path(argv[1], cwd) if len(argv) > 1 else _as_path("~", cwd)
            if target is not None:
                cwd = target
                local_config = (cwd / "rcm.toml").exists()
            continue
        env = _env_of(segment, session)
        verdict = _segment_verdict(argv, cwd, prod, local_config, env=env)
        if verdict is not None:
            return verdict
    return None


def _segment_verdict(
    argv: list[str],
    cwd: Path,
    prod: Production,
    local_config: bool,
    env: SegmentEnv | None = None,
) -> Verdict | None:
    """조각 하나의 판정. `rcm setup` 은 여기 오지 않는다 — 명령 글 전체로 `_setup_verdict` 가
    본다."""
    head = _name(argv[0])
    env = env or SegmentEnv({})

    if head in DESTRUCTIVE:
        found = _hit(argv[1:], cwd, prod)
        if found is not None:
            name, path = found
            return Verdict(
                "deny",
                f"`{head}` would remove or move {path}, which is inside {name}. That is the "
                f"running build machine, not a scratch directory. See {DOCS}.",
            )

    if _is_pip(argv):
        if inside(_as_path(argv[0], cwd), prod.venv):
            return Verdict(
                "ask",
                f"This installs into the service virtualenv ({prod.venv}) — it changes what the "
                "build machine runs as soon as the service restarts. Confirm only if you mean to "
                f"deploy. See {DOCS}.",
            )
        found = _hit(argv, cwd, prod)
        if found is not None:
            name, path = found
            return Verdict(
                "ask",
                f"This pip command names {path} ({name}). Confirm that you mean to change the "
                f"build machine. See {DOCS}.",
            )

    if head == "launchctl":
        words = [token for token in argv[1:] if not token.startswith("-")]
        if words and words[0] in LAUNCHCTL_MUTATING and _mentions_service(argv):
            return Verdict(
                "ask",
                "This restarts or stops the rcm service. Jobs that are running become `lost` "
                "(exit 3 for the sessions waiting on them), so do it with the queue empty. "
                f"See {DOCS}.",
            )

    if head == "systemctl":
        words = [token for token in argv[1:] if not token.startswith("-")]
        if words and words[0] in SYSTEMCTL_MUTATING and _mentions_service(argv):
            return Verdict(
                "ask",
                "This restarts or stops the rcm service. Jobs that are running become `lost` "
                f"(exit 3 for the sessions waiting on them). See {DOCS}.",
            )

    if head == "git":
        subcommand, target = _git_target(argv, cwd)
        if subcommand in GIT_MUTATING and inside(target, prod.checkout):
            if (
                subcommand in ("switch", "checkout")
                and "main" in argv[argv.index(subcommand) + 1 :]
            ):
                return None
            return Verdict(
                "deny",
                f"`git {subcommand}` in {target} would change the production checkout, and the "
                "build machine runs whatever is checked out there. Keep it on `main` and move it "
                f"only with `git pull --ff-only`. Work in a worktree instead. See {DOCS}.",
            )

    subcommand = _rcm_subcommand(argv)
    if subcommand in ("serve", "worker") and not _wants_help(argv):
        verdict = _server_verdict(argv, cwd, prod, subcommand, local_config, env)
        if verdict is not None:
            return verdict
    if subcommand in DB_OPENING and not _wants_help(argv):
        verdict = _db_write_verdict(argv, cwd, prod, subcommand, local_config, env)
        if verdict is not None:
            return verdict
    if subcommand in MIGRATING and not _wants_help(argv):
        verdict = _migration_verdict(argv, cwd, prod, subcommand, local_config, env)
        if verdict is not None:
            return verdict

    return None


# ── 다른 빌드가 옛 DB 를 올리는 것 (사건 6f) ─────────────────────────────────


def _venv_of(argv: list[str], cwd: Path) -> Path | None:
    """이 조각이 실행하는 파일이 든 venv. venv 의 `bin/python` 은 바탕 인터프리터로 가는
    심링크라 먼저 풀지 않은 경로를 보고, 그다음 실제 파일(`~/.local/bin/rcm` → venv)을 본다."""
    token = argv[0]
    if "/" in token:
        found: Path | None = _as_path(token, cwd)
    else:
        located = _which(token)
        found = Path(located) if located else None
    if found is None:
        return None
    for candidate in (found, Path(os.path.realpath(found))):
        venv = _norm(candidate.parent.parent)
        if (venv / "pyvenv.cfg").exists():
            return venv
    return None


def _db_user_version(db: Path) -> int | None:
    """sqlite 헤더의 user_version(오프셋 60, 4바이트 big-endian). 연결하지 않는다 — 연결은 그
    자체로 `-wal`·`-shm` 을 만들고, 이 훅은 아무것도 바꾸면 안 된다. 없거나 sqlite 가 아니면
    None."""
    try:
        with db.open("rb") as handle:
            header = handle.read(100)
    except OSError:
        return None
    if len(header) < 64 or not header.startswith(SQLITE_MAGIC):
        return None
    wal = db.with_name(db.name + "-wal")
    try:
        if wal.stat().st_size > 0:
            return None  # 새 user_version 이 아직 -wal 에만 있을 수 있다 — 헤더는 낡았을지 모른다
    except OSError:
        pass
    return int.from_bytes(header[60:64], "big")


def _build_db_version(venv: Path) -> int | None:
    """그 venv 의 빌드가 아는 `DB_VERSION` — editable 이면 원본의 `store.py`, 아니면
    site-packages 의 것. 못 찾거나 숫자가 없으면 None."""
    stores: list[Path] = []
    source = _editable_source(venv)
    if source is not None:
        stores.append(source / "src" / "remote_ci_monitor" / "store.py")
    stores.extend(sorted(venv.glob("lib/python*/site-packages/remote_ci_monitor/store.py")))
    for store in stores:
        try:
            text = store.read_text(encoding="utf-8")
        except OSError:
            continue
        match = DB_VERSION_RE.search(text)
        if match:
            return int(match.group(1))
    return None


def _migration_verdict(
    argv: list[str],
    cwd: Path,
    prod: Production,
    subcommand: str,
    local_config: bool,
    env: SegmentEnv,
) -> Verdict | None:
    """유효 `data_dir` 의 DB 가 이 빌드보다 옛 판이면 막는다 — 열면 그 자리에서 올라가고, 그 DB 를
    만든 옛 빌드는 다시 못 연다. 서비스 venv 가 운영 데이터를 여는 것은 배포 뒤의 정상 운영이다."""
    if env.vars.get(ALLOW_MIGRATE) == "1":
        return None
    venv = _venv_of(argv, cwd)
    if venv is None:
        return None
    effective = _effective_data_dir(argv, cwd, prod, local_config, env, subcommand)
    if effective is None:
        return None
    if not effective.is_absolute():
        effective = _norm(cwd / effective)  # CLI 도 상대 `data_dir` 을 작업 폴더 기준으로 연다
    if inside(venv, prod.venv) and inside(effective, prod.data_dir):
        return None
    db = effective / DB_FILE
    have = _db_user_version(db)
    want = _build_db_version(venv)
    if have is None or want is None or have >= want:
        return None
    return Verdict(
        "deny",
        f"`rcm {subcommand}` here opens {db} (v{have}) with the build in {venv}, which knows "
        f"v{want}: it migrates that database from v{have} to v{want} on open, and the build that "
        "made it will refuse it afterwards (2026-11-20, a dev database went v7 → v24 this way). "
        "Point `--data-dir` or a config of its own at a new data_dir, or prefix the command with "
        f"`{ALLOW_MIGRATE}=1` if migrating this database is the point. See {DOCS}.",
    )


def _executable(argv: list[str], cwd: Path) -> Path | None:
    """이 조각이 실행하는 파일. `python -m …` 이면 그 python, 맨 이름이면 PATH 로 푼다."""
    token = argv[0]
    if "/" in token:
        return _as_path(token, cwd)
    found = _which(token)
    return _norm(Path(os.path.realpath(found))) if found else None


def _config_data_dir(config: Path) -> Path | None:
    """설정 파일의 `data_dir` — `server.toml` 은 `[server].data_dir`, `worker.toml` 은 최상위 키.
    없으면 기본값. 못 읽으면 None — CLI 도 그 자리에서 먼저 실패한다.

    CLI(`ServerConfig.data_dir`)와 같이 `expanduser` 만 한다 — `$VAR` 은 펼치지 않는다."""
    if tomllib is None:
        return None
    try:
        with config.open("rb") as handle:
            raw = tomllib.load(handle)
        section = raw.get("server")  # worker.toml 에서는 `server` 가 URL 문자열이다
        declared = section.get("data_dir") if isinstance(section, dict) else raw.get("data_dir")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None
    if isinstance(declared, str) and declared:
        return _norm(Path(declared).expanduser())
    return _norm(Path(DEFAULT_DATA_DIR).expanduser())


def _effective_data_dir(
    argv: list[str],
    cwd: Path,
    prod: Production,
    local_config: bool,
    env: SegmentEnv,
    subcommand: str = "token",
) -> Path | None:
    """CLI 와 같은 우선순위로 이 명령이 열 데이터 디렉터리를 정한다: `--data-dir` →
    `$RCM_SERVER_DATA_DIR` → 선택된 설정(`--config` → `$RCM_CONFIG` → `./rcm.toml` →
    `$XDG_CONFIG_HOME/rcm/server.toml` → 운영 설정)의 `data_dir` → 기본값. 모르면 None(막지 않는다).

    `RCM_SERVER_DATA_DIR` 은 CLI 의 env 덮어쓰기(`RCM_<SECTION>_<KEY>`, config.py `_env_overrides`)
    다 — 설정 파일보다 우선하고 `--data-dir` 보다는 뒤다. `[server]` 의 다른 키도 env 로 덮이지만
    DB 의 자리를 정하는 경로 키는 `data_dir` 하나라 그것만 본다. 빈 값(`RCM_SERVER_DATA_DIR=`)도
    CLI 는 적용한다 — `data_dir = ""` 은 현재 디렉터리다. `worker` 는 `[server]` 를 읽지 않으니
    그 env 는 건너뛴다(`load_worker_config` 는 `RCM_WORKER_TOKEN` 만 본다)."""
    data = _option_value(argv, ("--data", "--data-dir"))
    if data is not None:
        return _as_path(data, cwd)
    if subcommand != "worker":
        data = env.get("RCM_SERVER_DATA_DIR")
        if data is not None:
            return _norm(cwd) if data == "" else _as_path(data, cwd)
    config = _option_value(argv, ("--config", "-c"))
    if config is None:
        config = env.get("RCM_CONFIG") or None  # CLI 도 빈 `RCM_CONFIG=` 는 무시한다
    if config is not None:
        path = _as_path(config, cwd)
        if path is None:
            return None
        if inside(path, prod.config_dir):
            return prod.data_dir  # 운영 설정 그 자체 — 파일을 읽을 것도 없다
        return _config_data_dir(path)
    if local_config:
        local = cwd / "rcm.toml"
        return _config_data_dir(local) if local.exists() else None
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        candidate = _norm(Path(xdg).expanduser() / "rcm" / f"{_config_kind(subcommand)}.toml")
        if inside(candidate, prod.config_dir):
            return prod.data_dir
        if candidate.exists():
            return _config_data_dir(candidate)
    if prod.config_dir is not None:
        return prod.data_dir  # 탐색이 운영 설정까지 내려간다
    return _norm(Path(DEFAULT_DATA_DIR).expanduser())


def _config_kind(subcommand: str) -> str:
    return "worker" if subcommand == "worker" else "server"


def _db_write_verdict(
    argv: list[str],
    cwd: Path,
    prod: Production,
    subcommand: str,
    local_config: bool,
    env: SegmentEnv,
) -> Verdict | None:
    """운영 DB 를 서비스 venv 밖의 빌드로 여는 것을 막는다(결정 75). 옛 빌드는 새 DB 를 거절하고
    새 빌드는 옛 DB 를 **그 자리에서 마이그레이션한다** — 2026-09-10 에 그렇게 운영 서비스가
    재시작 불가가 됐다. 서비스 자신의 rcm 은 정상 운영이라 막지 않는다."""
    if prod.data_dir is None:
        return None
    executable = _executable(argv, cwd)
    if executable is not None and inside(executable, prod.venv):
        return None
    effective = _effective_data_dir(argv, cwd, prod, local_config, env)
    if not inside(effective, prod.data_dir):
        return None
    own = f"{prod.venv}/bin/rcm" if prod.venv else "the service's own rcm"
    return Verdict(
        "deny",
        f"`rcm {subcommand}` here would open the production database in {prod.data_dir} with a "
        "build that is not the service's — a different build migrates the database on open, and "
        "the running service may then refuse to start (2026-09-10). Use "
        f"`{own} {subcommand} …` for production, or point `--config`/`--data-dir` at a test "
        f"server. `rcm gc --dry-run --config` is fine: it plans on a temporary copy. See {DOCS}.",
    )


def _wants_help(argv: list[str]) -> bool:
    return "--help" in argv or "-h" in argv


def _mentions_service(argv: list[str]) -> bool:
    return any(word in token for token in argv for word in SERVICE_WORDS)


def _server_verdict(
    argv: list[str],
    cwd: Path,
    prod: Production,
    subcommand: str,
    local_config: bool,
    env: SegmentEnv,
) -> Verdict | None:
    """운영 데이터 디렉터리를 쓰는 서버·워커를 막는다 — `--data` 로든, 운영 설정으로든, 설정
    **사본**(`data_dir` 만 운영)이나 `RCM_SERVER_DATA_DIR` 로든. 유효 `data_dir` 은
    `_effective_data_dir` 이 CLI 와 같은 순서로 정한다."""
    config = _option_value(argv, ("--config", "-c"))
    data = _option_value(argv, ("--data", "--data-dir"))
    if data is not None and inside(_as_path(data, cwd), prod.data_dir):
        return Verdict(
            "deny",
            f"`rcm {subcommand}` here would write the production data directory ({prod.data_dir}) "
            f"while the service is using it — two servers, one SQLite database. See {DOCS}.",
        )
    if config is not None and inside(_as_path(config, cwd), prod.config_dir):
        return Verdict(
            "deny",
            f"`rcm {subcommand}` with the production config ({config}) would bind the "
            "production port and data directory. Copy it, change `port` and `data_dir`, and "
            f"point `--config` at the copy. See {DOCS}.",
        )
    effective = _effective_data_dir(argv, cwd, prod, local_config, env, subcommand)
    if not inside(effective, prod.data_dir):
        return None
    if config is not None or env.get("RCM_CONFIG"):
        return Verdict(
            "deny",
            f"`rcm {subcommand}` with this config would write the production data directory "
            f"({prod.data_dir}) while the service is using it — two servers, one SQLite database. "
            "A copy of the production config still points at the production `data_dir`, and "
            "`RCM_SERVER_DATA_DIR` overrides whatever the file says: give the test server its own "
            f"`data_dir`. See {DOCS}.",
        )
    return Verdict(
        "deny",
        f"`rcm {subcommand}` with no `--config` finds the production config in "
        f"{prod.config_dir}, so it would bind the production port and data directory. A test "
        f"server gets its own config file, `port` and `data_dir`. See {DOCS}.",
    )


# ── `rcm setup` (M5t-a §4.1, TR20 · TR33) ────────────────────────────────────
#
# 「setup 이 어디에 쓰나」는 setup 의 해석기 `remote_ci_monitor.core.setup.where` 가 정한다 — 훅은
# 그것을 들여와 부른다. P3 1 · 2라운드의 HIGH 는 모두 훅이 setup 의 계산을 따로 다시 하다
# 어긋난 것이었다(빈 값 · `${VAR:-x}` · 대소문자 · 줄인 옵션 · `--service-file`). 3라운드까지는
# 훅이 셸 단계(따옴표 · 변수 · `~` · 리다이렉션)를 했고, 4라운드부터는 셸 단계가 따옴표 벗기기와
# 맨 앞 `~` 뿐인 명령(`_plain_setup`)만 받는다. P3 5라운드에 셸 단계(`_lex` · `_expand_word` ·
# `cd` 따라가기)를 지웠다 — setup 이 아닌 규칙의 `cd` 는 PR 전처럼 `_as_path` 로 짐작한다.


def unit_name(label: str) -> str:
    """systemd 유닛 파일 이름(6-B-V1) — setup · doctor 와 같은 규칙."""
    return f"{_STANDARD_UNITS.get(label, label)}.service"


def _setup_core() -> ModuleType:
    """setup 의 해석기 모듈. 이 레포의 `src` 를 앞에 둔다 — 훅은 패키지를 설치하지 않은
    python3 로 돈다. 그 모듈은 표준 라이브러리만 쓴다."""
    src = str(Path(__file__).resolve().parents[1] / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return importlib.import_module("remote_ci_monitor.core.setup")


#: 앞머리 할당 `NAME=` — 그물과 허용 모양이 본다
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

#: `core.where` 가 읽는 환경 — 앞머리 할당의 값을 모르면 setup 이 쓸 자리도 모른다
_WHERE_ENV = ("HOME", "XDG_CONFIG_HOME")


def _python_module_end(argv: list[str]) -> int | None:
    """`python [플래그…] -m remote_ci_monitor[.cli]` 이면 모듈 이름 다음 자리. 인터프리터
    플래그(`-u` · `-E` · `-I` · `-X dev` · `-W x`)는 건너뛴다."""
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "-m":
            module, i = (argv[i + 1] if i + 1 < len(argv) else None), i + 2
        elif token.startswith("-m"):
            module, i = token[2:], i + 1
        elif token in ("-W", "-X"):
            i += 2
            continue
        elif token.startswith("-") and token not in ("-", "--") and not token.startswith("-c"):
            i += 1
            continue
        else:
            return None
        return i if module in ("remote_ci_monitor", "remote_ci_monitor.cli") else None
    return None


def _subcommand_index(argv: list[str]) -> int | None:
    """`rcm serve` 처럼 이 도구를 실행하는 argv 면 하위 명령의 자리. 실행 파일과 python 은
    이름(basename)으로 본다."""
    if not argv:
        return None
    head = _name(argv[0])
    if head.startswith("python"):
        start = _python_module_end(argv)
        if start is None:
            return None
    elif head in ("rcm", "remote-ci-monitor"):
        start = 1
    else:
        return None
    for index in range(start, len(argv)):
        if not argv[index].startswith("-"):
            return index
    return None


# ── `rcm setup`: 허용 목록 (P3 4라운드) ───────────────────────────────────────
#
# 네 라운드 동안 훅은 임의의 셸 글을 setup 에 대해 해석하려 했고, 라운드마다 새 셸 문법(줄
# 잇기 · 실행되지 않은 `cd` · 앞 세그먼트의 변수 · `${S}tup` · `--help` 뒤의 밀반입)이 그 해석을
# 비껴갔다. 셸 문법의 거부 목록은 완성할 수 없다. 그래서 운영 설치가 있는 머신에서는 setup 에
# 닿을 수 있는 명령(`_setup_net`)이 평범한 명령 하나(`_plain_setup`)일 때만 해석기에 묻고,
# 나머지는 막는다.

#: rcm 실행 파일(이름이 `rcm` · `remote-ci-monitor`)이나 이 패키지(`remote_ci_monitor`, `-m` ·
#: `-mremote_ci_monitor` · `-Imremote_ci_monitor` · `-c` 로 부를 때). `rcm.toml` · `rcm-server` ·
#: 워크트리 이름(`remote_ci_monitor-x`)은 아니다. 경로 조각(앞뒤에 `/`,
#: `src/remote_ci_monitor/x.py`)도 아니다 — 그 폴더를 읽는 `find` · `git ls-files` 가
#: 막혔다(P6 재확인 354 · v2-1)
_RCM_WORD = re.compile(
    r"(?<![\w.-])(?:rcm|remote-ci-monitor)(?![\w./-])"
    r"|(?:(?<![\w/-])|(?<![\w])-[A-Za-z]*m)remote_ci_monitor(?![\w/-])",
    re.IGNORECASE,
)
#: 낱말 setup: 하위 명령 `setup` 과 모듈 `remote_ci_monitor.setup`. `setup.py` · `test_setup` ·
#: `…-setup-…` 은 아니다
_SETUP_WORD = re.compile(r"(?<![\w-])setup(?![\w./-])", re.IGNORECASE)
#: 그물이 명령 글을 자르는 자리: 셸의 분리 · 묶음 글자와 백틱. 따옴표는 보지 않는다(넓게 자른다)
_ROUGH_TOKEN = re.compile(r"[;&|()\n`]|[^\s;&|()`]+")
_BREAKS = frozenset(";&|()\n`")
#: 셸이 낱말을 쓰인 글자와 다르게 만드는 글자: 펼치기(`$` 백틱 `{` `}`), 글롭(`*` `?` `[`),
#: 리다이렉션(`<` `>`). 명령 자리나 하위 명령 자리의 낱말에 있으면 그 낱말이 무엇이 될지 모른다
_SHAPING = frozenset("$`{}*?[<>")
#: 뒤 낱말을 여전히 명령 자리로 두는 셸 낱말
_KEYWORDS = frozenset({"!", "if", "then", "else", "elif", "do", "while", "until", "{", "}"})
#: 명령 자리의 `[` · `[[` 는 시험 명령이다 — 다른 명령을 실행하지 않는다
_TESTS = frozenset({"[", "[["})
#: 받은 명령을 실행하는 명령. 그 뒤의 변수 낱말은 모두 명령 자리일 수 있다(플래그의 값인지
#: 가리지 않는다)
_LAUNCHERS = WRAPPERS | frozenset(
    {
        "nice", "timeout", "gtimeout", "xargs", "eval", "doas", "su", "caffeinate", "stdbuf",
        "setsid", "watch", "bash", "sh", "zsh", "dash", "ksh", "fish", "source", ".", "arch",
        "ssh", "uv", "uvx", "pipx", "poetry", "script", "unbuffer", "flock", "ionice", "chronic",
        "expect", "socat", "parallel",
    }
)  # fmt: skip
#: 받은 줄(stdin · 파일 · `:::` · 찾은 파일)을 다른 명령의 인자로 붙여 실행하는 런처 — 그 인자는
#: 명령 글에 없다(P3 5라운드 v1-1). 이름(basename)으로 본다
_FEEDERS = frozenset({"xargs", "gxargs", "parallel", "entr", "rush", "xe"})
#: 어느 명령의 낱말이든 명령을 실행하는 긴 플래그 — fd 의 `--exec` 무리(`git rebase --exec` 도)
_FEEDER_FLAGS = frozenset({"--exec", "--exec-batch"})
#: find 의 실행 플래그 — 그 명령이 find 일 때만 본다(m5t-a-w3 F1: `grep -- -exec` 는 아니다)
_FIND_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FINDS = frozenset({"find", "gfind", "bfs"})
#: fd 의 짧은 실행 플래그(`-x` · `-X`) — 그 명령이 fd · fdfind 일 때만 본다(m5t-a-w3 F1:
#: `tar -x` · `unzip -x` · `grep -x` 의 `-x` 는 아니다)
_FD = frozenset({"fd", "fdfind"})
#: 따옴표 글을 코드로 실행하는 인터프리터와 그 코드 플래그 글자(`perl -e` · `osascript -e`).
#: 빈 글자는 첫 인자가 코드라는 뜻이다(awk). python 은 `-c` 다(`_PYTHON_CODE`)
_CODE_FLAGS = {
    "perl": "eE", "ruby": "e", "node": "ep", "nodejs": "ep", "bun": "e", "osascript": "e",
    "lua": "e", "php": "r", "Rscript": "e", "awk": "", "gawk": "", "mawk": "", "nawk": "",
    "sh": "c", "bash": "c", "zsh": "c", "dash": "c", "ksh": "c", "fish": "c",
}  # fmt: skip
_PYTHON_CODE = "c"
#: 코드 플래그 뒤의 인자를 셸 명령으로 실행하는 셸. `eval` 은 인자 전부가 셸 명령이다
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
#: 따옴표 글을 나중에 명령으로 실행하게 만드는 것 — `alias r='…'`, git 의 셸 별칭(`alias.x=!…`)
_DEFINERS = frozenset({"alias"})
_GIT_SHELL_ALIAS = re.compile(r"alias\.[^=\s]*=!", re.IGNORECASE)
#: 따옴표를 아는 자르기(`_lex`)가 치환(`$(…)` · 백틱 · `<(…)`) 자리에 남기는 글자 — 셸이 무엇으로
#: 바꿀지 모른다(`$` 가 있어 변수처럼 읽힌다)
_SUBST = "$(…)"
#: 이보다 깊게 겹친 치환은 읽지 않고 그물을 켠다(fail closed — 훅이 터지면 setup 이 새어 나간다)
_MAX_DEPTH = 64
#: 명령에 가상 터미널을 주는 런처 — 그 아래 하위 명령 없는 `rcm` 은 첫 실행 질문을 띄우고
#: 예면 setup 을 돌린다(P3 5라운드 v1-2)
_PTY_LAUNCHERS = frozenset({"script", "unbuffer", "expect", "socat", "tmux", "screen"})
#: ssh 가 터미널을 주는 플래그: `-t` · `-tt` 와 그 글자를 묶은 꼴(`-qt`). `RequestTTY` 는 따로 본다
_SSH_TTY = re.compile(r"-[A-Za-z]*t[A-Za-z]*")
#: python 이 터미널을 여는 낱말(`import pty` · `pty.spawn` · `os.openpty` · `pexpect`)
_PYTHON_PTY = re.compile(r"\b(?:pty|openpty|forkpty|pexpect)\b")
#: 낱말 앞의 리다이렉션 연산자(`<` · `2>` · `>>` · `&>` · `2>&`) — 그 뒤는 파일 이름이다
_REDIRECTION = re.compile(r"^(?:\d*|&)?[<>]+&?")
#: 글롭이 펼쳐져 될 수 있는 이 도구의 이름
_TOOL_NAMES = ("rcm", "remote-ci-monitor", "remote_ci_monitor")


def _letters(text: str) -> str:
    """셸이 글자로 남길 것 — 줄 잇기(`\\`+줄바꿈)를 지우고 따옴표와 역슬래시를 벗긴다. 그래서
    `se\\<줄바꿈>tup` · `s'etup'` · `r\\cm` 도 낱말로 보인다."""
    return re.sub(r"[\\'\"]", "", text.replace("\\\n", ""))


def _shaped(word: str) -> bool:
    return any(c in _SHAPING for c in word)


def _shaped_after(tokens: list[str], index: int) -> bool:
    """`tokens[index]` 다음의 하위 명령 자리가 글자 그대로가 아닌가. 글자 그대로의 플래그
    (`--no-follow`)는 건너뛰고, 첫 다른 낱말이 펼치기 · 글롭이면, 또는 그 자리에 명령
    치환(백틱 · `(`)이 오면 참이다. 명령이 끝나면(`;` `&` `|` `)` 줄바꿈) 거짓이다."""
    for position in range(index + 1, len(tokens)):
        token = tokens[position]
        if token in ("`", "("):
            return True
        if token in _BREAKS:
            return False
        word = _letters(token)
        if word.startswith("-"):
            continue
        # zsh 의 `s(e)tup` 글롭. `_lex` 의 낱말은 붙은 `(…)` 를 이미 `_SUBST` 로 담았다 — 그 뒤의
        # `(` 는 다음 낱말의 치환이다
        glued = not isinstance(token, _Word) and tokens[position + 1 : position + 2] == ["("]
        return _shaped(word) or glued
    return False


class _Word(str):
    """`_lex` 의 낱말 — 쓰인 글자 그대로(따옴표 · 역슬래시 포함, 치환 자리는 `_SUBST`).
    `prose` 는 따옴표 안에 빈칸이 든 낱말이다: 명령 이름 하나가 될 수 없는 글(제목 · 메시지 ·
    본문)이거나, 그 글을 실행하는 명령(`sh -c` · `eval` · `python -c`)에게는 프로그램이다."""

    prose: bool = False


class _Unreadable(Exception):
    """`_lex` 가 셸처럼 자를 수 없는 글 — 닫히지 않은 따옴표, 히어독(`<<EOF`, 그 본문은 명령이
    아니다), `_MAX_DEPTH` 보다 깊게 겹친 치환. 그물은 그 글을 옛 거친 자르기로 본다
    (`_rough_view`)."""


def _quote_end(text: str, i: int) -> int:
    """`'…'` 나 `$'…'`(역슬래시가 다음 글자를 감싼다)가 끝난 다음 자리. 닫히지 않으면
    `_Unreadable`."""
    if text[i] == "$":
        j = i + 2
        while j < len(text):
            if text[j] == "\\":
                j += 2
            elif text[j] == "'":
                return j + 1
            else:
                j += 1
        raise _Unreadable
    j = text.find("'", i + 1)
    if j < 0:
        raise _Unreadable
    return j + 1


def _lex(text: str) -> list[str]:
    """명령 글을 낱말(`_Word`)과 셸 글자(`;` `&` `|` 줄바꿈, 치환 · 서브셸의 `(` · `)`)로 자른다.

    `_ROUGH_TOKEN` 과 달리 따옴표를 안다 — 따옴표 안의 `(` · `|` · `;` 는 글자다(m5t-a-w3 F2:
    제목의 `feat(cli):` 가 명령 자리를 만들었다). 맨 글과 겹 따옴표 안의 `$(…)` · 백틱, 낱말에 붙은
    `(…)`(`<(…)` · `s(e)tup`)는 그 안을 명령으로 자르고(`(` … `)`, 바깥 낱말보다 먼저 나온다),
    바깥 낱말에는 `_SUBST` 를 남긴다. 리다이렉션 연산자(`<` · `2>&1` · `&>` · `>|`)는 낱말의
    머리다. 자를 수 없는 글이면 `_Unreadable`."""
    items: list[str] = []
    end = len(text)

    def flush(word: list[str], quoted: list[bool]) -> None:
        if word:
            raw = _Word("".join(word))
            raw.prose = quoted[0] and any(c in " \t\n" for c in _letters(raw))
            items.append(raw)
            word.clear()
        quoted[0] = False

    def double(i: int, word: list[str], depth: int) -> int:
        word.append('"')
        i += 1
        while i < end:
            c = text[i]
            if c == '"':
                word.append(c)
                return i + 1
            if c == "\\":
                word.append(text[i : i + 2])
                i += 2
            elif c == "`" or text.startswith("$(", i):
                word.append(_SUBST)
                items.append("(")
                i = command(i + (1 if c == "`" else 2), c if c == "`" else ")", depth + 1)
            else:
                word.append(c)
                i += 1
        raise _Unreadable

    def command(i: int, closer: str, depth: int) -> int:
        if depth > _MAX_DEPTH:
            raise _Unreadable
        word: list[str] = []
        quoted = [False]
        while i < end:
            c = text[i]
            head = "".join(word)
            if closer and c == closer:
                flush(word, quoted)
                items.append(")")
                return i + 1
            if c in " \t":
                flush(word, quoted)
                i += 1
            elif c == "\\":
                word.append(text[i : i + 2])
                i += 2
            elif c == "'" or text.startswith("$'", i):
                j = _quote_end(text, i)
                word.append(text[i:j])
                quoted[0] = True
                i = j
            elif c == '"':
                quoted[0] = True
                i = double(i, word, depth)
            elif c == "`" or c == "(" or text.startswith("$(", i):
                if c == "(" and not word:
                    flush(word, quoted)  # 서브셸 · 배열
                else:
                    word.append(_SUBST)
                items.append("(")
                i = command(i + (2 if c == "$" else 1), c if c == "`" else ")", depth + 1)
            elif c in "<>":
                if text.startswith("<<", i) and not text.startswith("<<<", i):
                    raise _Unreadable  # 히어독 본문은 명령이 아니다 — 줄 단위로 자르면 틀린다
                if word and not (head.isdigit() or head[-1] in "<>&"):
                    flush(word, quoted)
                word.append(c)
                i += 1
            elif (
                c == "&" and (head[-1:] in ("<", ">") or (not word and text[i + 1 : i + 2] == ">"))
            ) or (c == "|" and head[-1:] == ">"):
                word.append(c)  # `2>&1` · `&>` · `>|`
                i += 1
            elif c in ";&|\n)":
                flush(word, quoted)
                items.append(c)
                i += 1
            else:
                word.append(c)
                i += 1
        flush(word, quoted)
        return i

    command(0, "", 0)
    return items


@dataclass
class _Frame:
    """명령 하나를 읽는 동안의 상태. `(` 가 새 틀을 쌓고 `)` 가 앞 틀로 돌아간다 — 그래서
    `xargs -a <(…) rcm` 의 rcm 은 여전히 xargs 의 명령 안이다."""

    position: bool = True  # 다음 낱말이 명령 자리다
    wrapped: bool = False  # 감싸기 뒤 — 변수 낱말은 모두 명령 자리일 수 있다
    runs: bool = False  # 받은 인자 · 글을 실행하는 명령(감싸기 · 런처 · 코드 플래그) 뒤
    target: bool = False  # 앞 낱말이 리다이렉션 연산자 하나 — 이 낱말은 파일 이름이다
    code: str | None = None  # 명령이 인터프리터면 그 코드 플래그 글자
    interp: str = ""  # 그 인터프리터의 이름
    shell: bool = False  # `eval` · `sh -c` 뒤 — 인자가 셸 명령이다
    commands: list[str] = field(default_factory=list)  # 명령 자리 낱말(감싸기 · 런처 뒤 포함)
    words: list[str] = field(default_factory=list)  # 이 명령의 낱말
    launcher: str | None = None  # 이 명령의 런처(인자를 넘기거나 터미널을 주는 것)


@dataclass(frozen=True)
class _Seat:
    """낱말 하나가 선 자리."""

    letters: str  # 셸이 남길 글자(`_letters`)
    position: bool  # 명령 자리
    command: bool  # 명령 자리이거나 감싸기 뒤
    wrapped: bool  # 감싸기 뒤
    script: bool  # 실행되는 따옴표 글 — 그 안은 옛 거친 자르기(`_rough_net`)로 본다
    inert: bool  # 실행되지 않는 따옴표 글 — 그 안의 rcm 낱말을 세지 않는다
    shell: bool = False  # `eval` · `sh -c` 가 셸 명령으로 실행할 인자


@dataclass(frozen=True)
class _View:
    items: list[str]
    seats: list[_Seat | None]
    runs: bool  # 글 어디엔가 받은 글을 실행하는 명령이 있다
    cause: tuple[str, str] | None  # 그물의 (c): (런처, rcm 일 수 있는 낱말)


def _walk(items: list[str], live_prose: bool) -> _View:
    """`_lex` 의 낱말마다 자리를 정하고 런처 규칙(c)의 원인을 찾는다.

    런처는 **명령 자리**의 런처뿐이다(m5t-a-w3 F1). 명령 자리는 앞머리 할당 · 키워드 · 리다이렉션
    (`<f` · `< f` · `2>&1`)을 건너뛴 첫 낱말과, 감싸기 · 런처 · 코드 플래그 뒤의 낱말이다. `-x` ·
    `-X` 는 그 명령이 fd 일 때만, `-exec` 무리는 find 일 때만 런처다. rcm 일 수 있는 낱말은 실행되지
    않는 따옴표 글이 아니면 자리를 보지 않고 세되, `$` · 치환 낱말은 같은 명령의 런처 뒤에서만
    센다(P6 재확인 354).

    `live_prose` 가 거짓이면 명령 자리가 아닌 따옴표 글(`_Word.prose`)은 실행되지 않는 글이다.
    참이면(글 어디엔가 `sh` · `eval` · `xargs` · `python -c` 처럼 받은 글을 실행하는 명령이 있다)
    그 글은 프로그램일 수 있다 — 옛 거친 자르기로 그 안의 낱말을 모두 명령 자리처럼 본다."""
    stack: list[_Frame] = []
    frame = _Frame()
    seats: list[_Seat | None] = []
    runs = False
    launcher: str | None = None
    candidate: str | None = None
    for item in items:
        if not isinstance(item, _Word):
            seats.append(None)
            if item == "(":
                stack.append(frame)
                frame = _Frame()
            elif item == ")" and stack:
                frame = stack.pop()
            else:
                frame = _Frame()
            continue
        letters = _letters(item)
        if frame.target or _REDIRECTION.match(item):
            # 리다이렉션과 그 파일 이름 — 명령 자리를 옮기지 않는다
            frame.target = not frame.target and _REDIRECTION.fullmatch(item) is not None
            if candidate is None and _could_be_rcm(item, after=frame.launcher is not None):
                candidate = letters or item
            seats.append(
                _Seat(letters, False, frame.wrapped, frame.wrapped, False, False, frame.shell)
            )
            continue
        position, command = frame.position, frame.position or frame.wrapped
        commandish = command or frame.runs
        # 명령 자리의 따옴표 글은 명령 이름이다. 앞머리 할당의 값(`T='feat(cli): rcm …'`)은 아니다
        prose = item.prose and (not frame.position or bool(_ASSIGNMENT.match(letters)))
        script, inert = prose and live_prose, prose and not live_prose
        if not inert:
            parts = _ROUGH_TOKEN.findall(letters) if script else [item]
            for part in parts:
                if part in _BREAKS:
                    continue
                word = _letters(part)
                if candidate is None and _could_be_rcm(part, after=frame.launcher is not None):
                    candidate = word or part
                frame.words.append(word)
                if commandish or script:
                    frame.commands.append(word)
                if frame.launcher is None and (
                    _launches(frame.words, frame.commands)
                    or _gives_a_terminal(frame.words, frame.commands)
                ):
                    frame.launcher = word
                    launcher = launcher or word
        seats.append(_Seat(letters, position, command, frame.wrapped, script, inert, frame.shell))
        if command and (_ASSIGNMENT.match(letters) or letters in _KEYWORDS):
            continue
        name = _name(letters)
        if command and name in _LAUNCHERS:
            frame.wrapped = frame.runs = True
        if commandish and name == "eval":
            frame.shell = True
        if (commandish and name in _DEFINERS) or _GIT_SHELL_ALIAS.search(letters):
            frame.runs = True  # 정의한 글을 다음 명령이 실행한다
        if commandish and frame.code is None:
            if name.startswith("python"):
                frame.code = _PYTHON_CODE
            elif name in _CODE_FLAGS:
                frame.code = _CODE_FLAGS[name]
                frame.runs = frame.runs or not frame.code  # awk 의 첫 인자는 프로그램이다
            frame.interp = name
        elif (
            frame.code
            and letters.startswith("-")
            and not letters.startswith("--")
            and any(c in frame.code for c in letters[1:])
        ):
            frame.runs = True  # `python -c` · `perl -e` 의 뒤는 프로그램이다
            frame.shell = frame.shell or frame.interp in _SHELLS
        if frame.launcher is not None:
            frame.runs = True
        runs = runs or frame.runs
        frame.position = False
    cause = (launcher, candidate) if launcher is not None and candidate is not None else None
    return _View(items, seats, runs, cause)


def _view(command: str) -> _View:
    """명령 글의 자리. 먼저 따옴표 글을 모두 실행되지 않는 글로 읽어, 받은 글을 실행하는 명령이
    있는지 본다. 있으면 따옴표 글을 프로그램으로 다시 읽는다 — `T='… rcm $S'; eval "$T"` 처럼
    글을 나중에 실행하는 꼴은 옛 그물 그대로다."""
    items = _lex(command.replace("\\\n", ""))
    first = _walk(items, live_prose=False)
    return _walk(items, live_prose=True) if first.runs else first


def _setup_net(command: str) -> bool:
    """이 명령이 `rcm setup` 에 닿을 수 있나 — 하나뿐인 그물.

    rcm 을 부를 수 있는 명령(글에 rcm 낱말이 있거나, 명령 자리에 변수 · 글롭이 있다)이 (a) 글
    어디에든 낱말 setup 을 담거나, (b) rcm(또는 그 변수) 다음 하위 명령 자리가 글자 그대로가
    아니면 참이다. 참이면 `_setup_verdict` 가 명령 글 전체를 평범한 setup 명령 하나로 읽을 수
    있을 때만 허용한다. 평범한 `rcm setup …` 도 (a) 로 여기 걸린다.

    따옴표는 하위 명령을 숨기지 못한다 — (a) 는 따옴표를 벗긴 글 전체(`_letters`)를 본다. 그래서
    `git commit -m "fix rcm setup"` 도 막는다(받아들인 과잉 차단, 메시지가 파일로 쓰는 길을
    알려 준다). (b) 는 따옴표가 아니라 셸이 글자를 바꾸는 것(`_SHAPING`)을 본다. 글에 rcm 낱말이
    있으면 변수 낱말은 어디서든 rcm 일 수 있다(`R=rcm; nice $R …`).

    (b) 는 실행되지 않는 따옴표 글 안의 rcm 낱말을 세지 않는다(m5t-a-w3 F2): 제목 · 커밋 메시지 ·
    본문의 「rcm wait」 가 `"$T"` 뒤의 `"$H/…"` 를 rcm 의 하위 명령 자리로 만들었다. 글 어디엔가
    받은 글을 실행하는 명령이 있으면 따옴표 글도 지금처럼 센다(`_view`).

    (c) 명령 자리의 런처(인자를 넘기는 `_launches`, 터미널을 주는 `_gives_a_terminal`)와 rcm 일
    수 있는 낱말(`_could_be_rcm`)이 함께 있어도 참이다(`_walk`). 리다이렉션이 앞에 붙어도
    런처는 명령 자리다(`<f xargs rcm`, P6 재확인 1). 셸 글을 읽지 못하면(치환이 너무 깊다)
    참이다."""
    text = command.replace("\\\n", "")
    letters = _letters(text)
    try:
        view = _view(text)
    except _Unreadable:
        return _rough_view(text)[0]
    if view.cause is not None:
        return True
    items, seats = view.items, view.seats
    names_rcm = any(
        seat is not None and not seat.inert and _RCM_WORD.search(seat.letters) for seat in seats
    )
    can_call = bool(_RCM_WORD.search(letters))
    for index, seat in enumerate(seats):
        if seat is None or seat.inert:
            continue
        if seat.script:
            hit, calls = _rough_net(seat.letters, seat.wrapped, names_rcm)
            if hit:
                return True
            can_call = can_call or calls
            continue
        word = seat.letters
        found = _RCM_WORD.search(word)
        if found is not None and (_shaped(word[found.end() :]) or _shaped_after(items, index)):
            return True
        assignment = bool(_ASSIGNMENT.match(word))
        if seat.command and (assignment or word in _KEYWORDS):
            continue
        variable = "$" in word or "`" in items[index]
        if seat.shell and variable and names_rcm:
            # `eval "$(printf 'rcm %s' …)"` · `bash -c "$X"` — 셸이 실행할 글을 셸이 만든다
            return True
        if not assignment and (
            (seat.position and _shaped(word) and word not in _TESTS)
            or ((seat.wrapped or names_rcm) and variable)
        ):
            can_call = True
            if _shaped_after(items, index):
                return True
    return can_call and bool(_SETUP_WORD.search(letters))


def _rough_net(text: str, wrapped: bool, names_rcm: bool) -> tuple[bool, bool]:
    """실행되는 따옴표 글(`sh -c '…'` 의 글)을 옛 그물의 거친 자르기(`_ROUGH_TOKEN`)로 본다 —
    (그물이 켜지나, rcm 을 부를 수 있나). 글은 명령 자리에서 시작한다."""
    tokens = _ROUGH_TOKEN.findall(text)
    position, can_call = True, False
    for index, token in enumerate(tokens):
        if token in _BREAKS:
            position, wrapped = True, False
            continue
        word = _letters(token)
        found = _RCM_WORD.search(word)
        if found is not None and (_shaped(word[found.end() :]) or _shaped_after(tokens, index)):
            return True, True
        at_command = position or wrapped
        assignment = bool(_ASSIGNMENT.match(word))
        if at_command and (assignment or word in _KEYWORDS):
            continue
        variable = "$" in word or "`" in token
        if not assignment and (
            (position and _shaped(word) and word not in _TESTS)
            or ((wrapped or names_rcm) and variable)
        ):
            can_call = True
            if _shaped_after(tokens, index):
                return True, True
        if at_command and _name(word) in _LAUNCHERS:
            wrapped = True
        position = False
    return False, can_call


def _launcher_cause(command: str) -> tuple[str, str] | None:
    """(런처 낱말, rcm 일 수 있는 낱말) — 그물의 (c) 가 이 명령을 잡은 원인(`_walk`). 없으면 None.

    런처는 명령 자리의 낱말을 더해 가며 `_launches` · `_gives_a_terminal` 이 처음 참이 되는
    낱말이다. rcm 일 수 있는 낱말(`_could_be_rcm`)은 실행되지 않는 따옴표 글이 아니면 자리를
    보지 않되, `$` · 치환 낱말만은 같은 명령의 그 런처 **뒤**에 있을 때 센다(P6 재확인 354) —
    `echo $HOME | xargs ls` 의 `$HOME` 은 런처가 실행할 명령이 될 수 없다."""
    try:
        return _view(command).cause
    except _Unreadable:
        return _rough_view(command.replace("\\\n", ""))[1]


def _rough_view(text: str) -> tuple[bool, tuple[str, str] | None]:
    """`_lex` 가 자를 수 없는 글의 그물(m5t-a-w3 F 앞의 그물 그대로) — (켜지나, 런처 원인).
    따옴표를 보지 않고 거칠게 자르며(`_ROUGH_TOKEN`), 런처와 rcm 일 수 있는 낱말은 자리를 보지
    않는다. `$` · 백틱 낱말만 런처 뒤에서 센다."""
    tokens = _ROUGH_TOKEN.findall(text)
    words: list[str] = []
    cause: tuple[str, str] | None = None
    at = -1
    for index, token in enumerate(tokens):
        if token in _BREAKS:
            continue
        words.append(_letters(token))
        if _launches(words, words) or _gives_a_terminal(words, words):
            at = index
            break
    if at >= 0:
        for index, token in enumerate(tokens):
            if token not in _BREAKS and _could_be_rcm(token, after=index > at):
                cause = (_letters(tokens[at]), _letters(token) or token)
                break
    letters = _letters(text)
    names_rcm = bool(_RCM_WORD.search(letters))
    hit, calls = _rough_net(text, False, names_rcm)
    armed = cause is not None or hit or ((names_rcm or calls) and bool(_SETUP_WORD.search(letters)))
    return armed, cause


def _launches(words: list[str], commands: list[str]) -> bool:
    """명령 하나(`words`)가 받은 인자로 명령을 실행하는 런처인가: 명령 자리(`commands`)의
    xargs · parallel · entr · rush · xe, 어느 명령의 `--exec`, 명령이 find 일 때의 `-exec` 무리,
    명령이 fd 일 때의 `-x` · `-X`(m5t-a-w3 F1 — `tar -x` 의 `-x` 는 아니다)."""
    names = {_name(word) for word in commands}
    if names & _FEEDERS or _FEEDER_FLAGS.intersection(words):
        return True
    if names & _FINDS and _FIND_FLAGS.intersection(words):
        return True
    return bool(names & _FD) and bool({"-x", "-X"}.intersection(words))


def _gives_a_terminal(words: list[str], commands: list[str]) -> bool:
    """명령 하나가 가상 터미널을 주는 런처인가: 명령 자리의 script · unbuffer · expect · socat ·
    tmux · screen, 명령이 ssh 일 때의 `-t` · `RequestTTY`, 명령이 python 일 때의 pty · pexpect."""
    names = {_name(word) for word in commands}
    if names & _PTY_LAUNCHERS:
        return True
    letters = " ".join(words)
    if "ssh" in names and (
        any(_SSH_TTY.fullmatch(word) for word in words) or "requesttty" in letters.lower()
    ):
        return True
    return any(name.startswith("python") for name in names) and bool(_PYTHON_PTY.search(letters))


def _could_be_rcm(token: str, *, after: bool = True) -> bool:
    """이 낱말이 rcm 일 수 있나 — rcm 낱말이거나, 셸이 펼치는 변수 · 명령 치환(`$` · 백틱),
    펼쳐 rcm 이 될 수 있는 글롭(`r?m` · `*`)이나 중괄호 펼치기(`{r,x}cm`). 앞의 리다이렉션
    연산자는 떼고 본다 — `2>/dev/null` · `<f` 는 rcm 이 아니다. `*.py` · find 의 `{}` 도 아니다.

    `$` · 백틱 낱말은 런처 뒤(`after`)에 있고, 펼친 끝 조각(마지막 `/` 뒤)이 글자 그대로의 다른
    이름이 아닐 때만 센다(P6 재확인 354 · v2-2): `"$PWD/x"` 는 펼쳐도 `…/x` 다."""
    if "$" in token or "`" in token:
        return after and _open_tail(token)
    word = _letters(token)
    if _RCM_WORD.search(word):
        return True
    word = _REDIRECTION.sub("", word)
    name = word.rstrip("/").rsplit("/", 1)[-1].lower()
    if any(c in name for c in "*?[") and any(
        fnmatch.fnmatchcase(tool, name) for tool in _TOOL_NAMES
    ):
        return True
    return "{" in word and ("," in word or ".." in word)


def _open_tail(token: str) -> bool:
    """펼치기가 든 낱말의 끝 조각(마지막 `/` 뒤)이 rcm 일 수 있나 — 그 조각에 `$` · 백틱이
    있거나 글자 그대로 rcm 이면 참이다."""
    tail = _letters(token).rstrip("/").rsplit("/", 1)[-1]
    if not tail or "$" in tail or "`" in tail:
        return True
    return _could_be_rcm(tail)


#: 허용 모양에 있으면 안 되는 글자: 셸 메타 문자(`;` `&` `|` `$` 백틱 `(` `)` `{` `}` `<` `>`),
#: 역슬래시, 글롭(`*` `?` `[` `]`), 주석 `#`, 이력 · zsh 글롭 `!` `^`. 줄바꿈과 다른 제어 문자도
#: 안 된다(`_plain_words`)
_PLAIN_FORBIDDEN = frozenset(";&|$`(){}<>\\*?[]#!^")
#: 허용 모양의 낱말: 따옴표 없는 글자, 또는 홑 · 겹 따옴표로 통째로 감싼 값(앞에 `NAME=` ·
#: `--flag=` 가 붙을 수 있다). 따옴표 안에 다른 따옴표는 없다
_PLAIN_WORD = re.compile(r"""(?:[^ \t'"=]*=)?(?:'[^'"]*'|"[^'"]*")|[^ \t'"]+""")
#: 허용 모양의 python: `python` · `python3` · `python3.11`
_PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")
_RCM_NAMES = ("rcm", "remote-ci-monitor")
#: setup 의 경로 플래그(`core.parse_args` 의 이름)
_SETUP_PATHS = ("config", "service_file", "data_dir")
#: 앞머리 할당에서 `~` 를 setup 이 푸는 환경 이름 — HOME 은 글자 그대로 홈이 된다
_ENV_TILDE_OK = frozenset({"XDG_CONFIG_HOME"})
#: 그물의 거절 — 허용 모양을 그대로 말한다
PLAIN_RULE = (
    "run `rcm setup` as one plain command: no ; & | $ ` ( ) < > or backslash, "
    "absolute or ~ paths only"
)


@dataclass(frozen=True)
class PlainSetup:
    """평범한 `rcm setup` 명령 하나 — 앞머리 `NAME=value`(값, 따옴표로 감쌌나)와 `setup` 뒤의
    인자(따옴표를 벗긴 글자)."""

    prefix: tuple[tuple[str, str, bool], ...]
    args: tuple[str, ...]


def _plain_words(command: str) -> list[tuple[str, bool]] | None:
    """명령 글을 (따옴표를 벗긴 낱말, 따옴표가 있었나) 로. 허용 모양이 아니면 None."""
    if any(c in _PLAIN_FORBIDDEN or (c < " " and c != "\t") or c == "\x7f" for c in command):
        return None
    words: list[tuple[str, bool]] = []
    pos, end = 0, len(command)
    while True:
        while pos < end and command[pos] in " \t":
            pos += 1
        if pos >= end:
            return words
        m = _PLAIN_WORD.match(command, pos)
        if m is None or (m.end() < end and command[m.end()] not in " \t"):
            return None
        raw = m.group(0)
        words.append((raw.replace("'", "").replace('"', ""), "'" in raw or '"' in raw))
        pos = m.end()


def _plain_setup(command: str) -> PlainSetup | None:
    """명령 글 **전체**가 평범한 setup 명령 하나면 그 내용, 아니면 None.

    모양: 선택적인 `NAME=value` 접두 → rcm 낱말(이름이 `rcm` · `remote-ci-monitor`, 또는
    `python[3[.x]] [플래그] -m remote_ci_monitor[.cli]`) → 글자 그대로의 `setup` → 인자들.
    글 어디에도 `_PLAIN_FORBIDDEN` 이 없다. 그래서 셸은 따옴표를 벗기고 낱말을 나누고 맨 앞
    `~` 를 펼치는 것밖에 하지 않는다 — 훅이 그것을 그대로 한다. 앞머리 값의 맨 앞이 아닌 `~`
    (`a:~/b`, 할당에서 셸이 펼친다)는 받지 않는다."""
    words = _plain_words(command)
    if not words:
        return None
    index = 0
    prefix: list[tuple[str, str, bool]] = []
    while index < len(words) and _ASSIGNMENT.match(words[index][0]):
        name, _, value = words[index][0].partition("=")
        if "~" in value[1:]:
            return None
        prefix.append((name, value, words[index][1]))
        index += 1
    head = _name(words[index][0]) if index < len(words) else ""
    if head in _RCM_NAMES:
        start = index + 1
    elif _PYTHON.fullmatch(head):
        offset = _python_module_end([word for word, _ in words[index:]])
        if offset is None:
            return None
        start = index + offset
    else:
        return None
    if start >= len(words) or words[start][0] != "setup":
        return None
    return PlainSetup(tuple(prefix), tuple(word for word, _ in words[start + 1 :]))


def _anchored(value: str) -> bool:
    """작업 폴더에 기대지 않는 경로 글자 — 절대 경로, `~`, `~/…`(`~user` 는 아니다)."""
    return value.startswith("/") or value == "~" or value.startswith("~/")


def _home_joined(home: Path, value: str) -> str:
    """`~` · `~/…` 를 그 홈으로 — 셸의 펼치기."""
    return (str(home).rstrip("/") + value[1:]) or "/"


def _not_plain(cause: tuple[str, str] | None = None) -> Verdict:
    """그물의 거절. 런처 규칙이 잡았으면 원인(런처 낱말 + rcm 일 수 있는 낱말)과 나누는 법을
    말한다(P6 재확인 354)."""
    why = ""
    if cause is not None:
        why = (
            f" It was stopped because the launcher `{cause[0]}` and the word `{cause[1]}` are in "
            "the same command — a launcher can run that word as a command the hook cannot see. "
            "If they belong to different steps, split them into two Bash calls."
        )
    return Verdict(
        "deny",
        "This command can reach `rcm setup`, but not as a plain `rcm setup …` call the hook can "
        "read, so it cannot tell what setup would write — its config file, service file, label "
        f"or data directory. In short, {PLAIN_RULE} (`NAME=value` in front is fine; no `cd`, "
        "`env`, `sudo` or a second command). If the words are only text — a "
        "commit message, an echo — put the text in a file (`git commit -F <file>`, "
        f'`gh pr create --title "$(cat <file>)"`).{why} See {DOCS}.',
    )


def _loose_path(what: str, value: str) -> Verdict:
    return Verdict(
        "deny",
        f"`rcm setup` here gives {what} the path {value!r}, which depends on the working folder "
        "or another user's home; the hook does not trust the working folder to tell where setup "
        f"would write. In short, {PLAIN_RULE}. See {DOCS}.",
    )


#: 반복할 수 있는 setup 의 값 플래그 — setup 은 마지막 값을 쓰지만 훅은 앞의 값도 본다(막는 쪽)
_SETUP_VALUE_FLAGS = ("--role", "--config", "--label", "--service-file", "--data-dir")


def _with_earlier_values(args: tuple[str, ...]) -> list[list[str]]:
    """그대로의 인자와, 반복한 값 플래그마다 앞의 값이 마지막이 되게 뒤의 것을 뺀 인자들."""
    out = [list(args)]
    for flag in _SETUP_VALUE_FLAGS:
        spots = [i for i, t in enumerate(args) if t == flag or t.startswith(flag + "=")]
        for keep in spots[:-1]:
            drop: set[int] = set()
            for spot in spots:
                if spot > keep:
                    drop |= {spot, spot + 1} if args[spot] == flag else {spot}
            out.append([t for i, t in enumerate(args) if i not in drop])
    return out


def _tilde_choices(ns: Any, session_home: Path) -> list[dict[str, str | None]]:
    """경로 플래그 값의 `~` 를 누가 푸나 — 셸(맨 앞의 따옴표 없는 `~`, 세션의 HOME)과
    setup(`--config=~/x` · 따옴표 안, 그 명령의 HOME). 어느 쪽인지 글자로 가리지 않고 둘 다 본다."""
    choices: list[dict[str, str | None]] = [{}]
    for flag in _SETUP_PATHS:
        value = getattr(ns, flag)
        options = [value]
        if isinstance(value, str) and (value == "~" or value.startswith("~/")):
            options.append(_home_joined(session_home, value))
        choices = [{**choice, flag: option} for choice in choices for option in options]
    return choices


def _real(path: Path) -> Path:
    """심볼릭 링크를 푼 경로 — 존재하는 가장 깊은 조상까지 `os.path.realpath` 가 풀고, 없는
    꼬리는 글자 그대로 붙인다. setup 이 쓸 파일은 아직 없으니 링크는 조상에 있다(P3 4라운드)."""
    return Path(_realpath(str(path)))


@functools.lru_cache(maxsize=256)
def _realpath(text: str) -> str:
    """`os.path.realpath` 를 한 번만 — 훅은 한 판정에 같은 뿌리를 여러 번 푼다(macOS 의 `/home`
    처럼 자동 마운트를 거치는 자리는 한 번에 수십 ms 다)."""
    return os.path.realpath(text)


def _real_production(prod: Production) -> Production:
    def real(path: Path | None) -> Path | None:
        return None if path is None else _real(path)

    return Production(
        checkout=real(prod.checkout),
        venv=real(prod.venv),
        config_dir=real(prod.config_dir),
        data_dir=real(prod.data_dir),
    )


def _service_place(path: Path, home: Path) -> Path | None:
    """서비스 관리자가 읽는 자리(`SETUP_SERVICE_PLACES`) 안이면 그 자리. `~` 는 이 사용자의 홈
    (세션의 HOME)이다 — 운영 서비스는 명령 앞의 `HOME=` 으로 옮겨 가지 않는다. 글자로도,
    심볼릭 링크를 푼 경로로도 본다."""
    for place in SETUP_SERVICE_PLACES:
        root = Path(str(home) + place[1:]) if place.startswith("~") else Path(place)
        if inside(path, root) or inside(_real(path), _real(root)):
            return _norm(root)
    return None


def _setup_verdict(
    command: str, cwd: Path, prod: Production, session: Mapping[str, str]
) -> Verdict | None:
    """setup 에 닿을 수 있는 명령(`_setup_net`)의 판정. 평범한 명령 하나(`_plain_setup`)가
    아니면 막는다. 그렇다면 setup 의 세 규칙을 setup 의 해석기 `core.where` 로 본다:

    1. 쓸 자리(설정 파일 · 서비스 파일)가 운영 뿌리 넷(`production_root` — 체크아웃 · 서비스
       venv · 설정 폴더 · data_dir, 편집 규칙과 같은 함수) 안이거나 서비스 관리자가 읽는 자리
       (`SETUP_SERVICE_PLACES`) 안이면 거절한다.
    2. 서비스 라벨이 운영 라벨이면 거절한다. `--label` 이 없으면 역할의 표준 라벨이다. 역할을 안
       주면 setup 이 물을 수 있으니 모든 역할로 본다. client 는 서비스 파일이 없다.
    3. 역할 2 · 4 의 `data_dir` 이 운영 `data_dir` 이거나 그 안이면 거절한다(6-A-L4 · 6-B-V5).

    경로는 절대 경로나 `~` · `~/…` 만 받는다(작업 폴더를 믿지 않는다). 견줄 때는 글자로도,
    심볼릭 링크를 푼 경로(`_real`)로도 본다. 모르면 운영으로 본다(fail closed): 해석기를 못
    들여오거나, setup 의 파서 · 해석기가 거절할 명령줄이면 거절한다. `--help` · `-h` 는 허용
    모양일 때만 지나간다."""
    plain = _plain_setup(command)
    if plain is None:
        return _not_plain(_launcher_cause(command))
    if _wants_help(list(plain.args)):
        return None
    try:
        core = _setup_core()
    except Exception as e:  # noqa: BLE001 — 해석기를 못 들여오면 setup 을 막는다(fail closed)
        return Verdict(
            "deny",
            "`rcm setup` is refused: the hook could not load remote_ci_monitor.core.setup, which "
            f"decides where setup writes ({type(e).__name__}: {e}). Run the hook from a checkout "
            f"with src/ on Python 3.9 or newer. See {DOCS}.",
        )
    session_home = Path(session["HOME"]) if session.get("HOME") else Path.home()
    environ = dict(session)
    for name, value, quoted in plain.prefix:
        if not quoted and (value == "~" or value.startswith("~/")):
            value = _home_joined(session_home, value)  # 셸이 할당의 맨 앞 `~` 를 펼친다
        environ[name] = value
    for name in _WHERE_ENV:
        value = environ.get(name)
        if value and not (value.startswith("/") or (name in _ENV_TILDE_OK and _anchored(value))):
            return _loose_path(f"${name}", value)
    home = core.home_of(environ, Path.home())
    units = {unit_name(standard) for standard in SETUP_STANDARD_LABELS}
    manager = core.manager_of(sys.platform)
    for args in _with_earlier_values(plain.args):
        try:
            ns = core.parse_args(args)
            for flag in _SETUP_PATHS:
                value = getattr(ns, flag)
                if value is not None and not _anchored(value):
                    return _loose_path("--" + flag.replace("_", "-"), value)
            places = [
                place
                for paths in _tilde_choices(ns, session_home)
                for place in _resolve(core, ns, paths, environ, home, cwd, manager)
            ]
        except (core.SetupArgError, core.SetupValueError) as e:
            return Verdict(
                "deny",
                f"`rcm setup {shlex.join(args)}` is not a command setup reads ({e}) — the hook "
                f"cannot tell where it would write, so it refuses it. See {DOCS}.",
            )
        verdict = _setup_place_verdict(places, prod, session_home, units)
        if verdict is not None:
            return verdict
    return None


def _resolve(
    core: ModuleType,
    ns: Any,
    paths: Mapping[str, str | None],
    environ: Mapping[str, str],
    home: Path,
    cwd: Path,
    manager: str | None,
) -> list[Any]:
    """setup 이 그 명령줄로 쓸 자리 — 역할을 안 주면 setup 이 물을 수 있으니 모든 역할."""
    roles = [ns.role] if ns.role else list(core.ROLES)
    return [
        core.where(
            role,
            environ=environ,
            cwd=cwd,
            home=home,
            manager=manager,
            config=paths["config"],
            label=ns.label,
            service_file=paths["service_file"],
            data_dir=paths["data_dir"],
            fold_case=FOLD_CASE,
            expand_user=os.path.expanduser,
        )
        for role in roles
    ]


def _setup_place_verdict(
    places: list[Any], prod: Production, session_home: Path, units: set[str]
) -> Verdict | None:
    """`core.where` 의 답 하나하나에 세 규칙을 댄다. 쓰는 파일(설정 · 서비스)마다 운영 뿌리 넷
    (`production_root` — 편집 규칙과 같은 함수)과 서비스 관리자 자리를 본다. 글자로도, 심볼릭
    링크를 푼 경로로도 본다."""
    real_prod = _real_production(prod)
    for found in places:
        for target in (found.config, found.service_file):
            if target is None:
                continue
            hit = production_root(target, prod) or production_root(_real(target), real_prod)
            if hit is not None:
                name, root = hit
                return Verdict(
                    "deny",
                    f"`rcm setup` here would write {target}, inside {name} ({root}). Give it a "
                    "test HOME or XDG_CONFIG_HOME, or `--config` and `--service-file` paths of its "
                    f"own. See {DOCS}.",
                )
            place = _service_place(target, session_home)
            if place is not None:
                return Verdict(
                    "deny",
                    f"`rcm setup` here would write {target} inside {place}, where the service "
                    "manager reads service files — the production service's files live there. "
                    "Let setup write its config and service file elsewhere and copy the service "
                    f"file yourself. See {DOCS}.",
                )
        label = found.label
        if label is not None:
            if label in SETUP_STANDARD_LABELS or unit_name(label) in units:
                return Verdict(
                    "deny",
                    f"`rcm setup` here would write a service file labelled {label} — the "
                    "production service's label on this machine. Pass `--label` with a test "
                    f"label. See {DOCS}.",
                )
        if found.role in SETUP_SERVER_ROLES and found.data_dir is not None:
            data = found.data_dir
            if inside(data, prod.data_dir) or inside(_real(data), real_prod.data_dir):
                return Verdict(
                    "deny",
                    f"`rcm setup` here would point a server at {data}, inside the production "
                    f"data directory ({prod.data_dir}) — that server would open the production "
                    "database with the build that ran setup. Pass `--data-dir` with a new folder "
                    f"of its own. See {DOCS}.",
                )
    return None


def decide(
    tool_name: str,
    tool_input: dict[str, object],
    prod: Production,
    cwd: Path,
    *,
    local_config: bool = False,
    environ: Mapping[str, str] | None = None,
) -> Verdict | None:
    """막을 이유가 있으면 Verdict, 없으면 None. 운영 설치를 못 찾았으면 옛 DB 마이그레이션
    (`_migration_verdict`) 하나만 본다 — 운영 경로를 묻는 다른 규칙은 그때 할 말이 없다.

    `local_config` 는 세션 cwd 에 `./rcm.toml` 이 있다는 뜻이다 — 그러면 `--config` 없는
    `rcm serve` 도 운영 설정을 집지 않는다(`cd` 뒤에는 그 폴더에서 다시 본다). `$RCM_CONFIG` ·
    `$RCM_SERVER_DATA_DIR` · `$XDG_CONFIG_HOME` 은 `environ`(None 이면 프로세스 환경)에서 읽는다.
    """
    session = os.environ if environ is None else environ
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        if not prod.known:
            return None
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return _edit_verdict(str(file_path), cwd, prod) if isinstance(file_path, str) else None
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None
        return _bash_verdict(command, cwd, prod, local_config, session)
    return None


# ── 발견 (I/O) ───────────────────────────────────────────────────────────────


def _venv_candidates() -> list[tuple[Path, bool]]:
    """서비스 venv 후보를 순서대로: 문서의 자리 → 설치된 서비스 유닛이 적은 `rcm` → PATH 의
    `rcm`·`remote-ci-monitor` 전부. 심링크(`~/.local/bin/rcm`)는 실제 파일로 푼다. 둘째 값은
    서비스가 그 venv 를 실행한다는 근거가 있나다 — 문서의 자리와 유닛은 있고, PATH 는 짐작이다."""
    out: list[tuple[Path, bool]] = []

    def add(launcher: Path | None, runs: bool) -> None:
        if launcher is None:
            return
        real = Path(os.path.realpath(launcher))
        venv = _norm(real.parent.parent)
        if all(venv != seen for seen, _ in out) and (venv / "pyvenv.cfg").exists():
            out.append((venv, runs))

    documented = Path(SERVICE_VENV).expanduser()
    if (documented / "pyvenv.cfg").exists():
        out.append((_norm(documented), True))
    for unit in SERVICE_UNITS:
        add(_launcher_in_unit(Path(unit).expanduser()), True)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        for name in ("rcm", "remote-ci-monitor"):
            launcher = Path(directory) / name
            if directory and launcher.is_file():
                add(launcher, False)
    return out


def _launcher_in_unit(unit: Path) -> Path | None:
    """유닛 파일(plist·service)에 적힌 `…/bin/rcm` — 서비스가 실제로 실행하는 것."""
    try:
        text = unit.read_text(encoding="utf-8")
    except OSError:
        return None
    for match in re.finditer(r"(/[^\s<>\"']*?/bin/(?:rcm|remote-ci-monitor))\b", text):
        return Path(match.group(1))
    return None


def _editable_source(venv: Path) -> Path | None:
    for info in venv.glob("lib/python*/site-packages/remote_ci_monitor-*.dist-info"):
        record = info / "direct_url.json"
        if not record.exists():
            continue
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("dir_info", {}).get("editable"):
            continue
        url = str(data.get("url", ""))
        if url.startswith("file://"):
            return _norm(Path(urllib.parse.unquote(url[len("file://") :])))
    return None


def _is_linked_worktree(checkout: Path) -> bool:
    """`git worktree add` 가 만든 폴더는 `.git` 이 디렉터리가 아니라 파일이다."""
    return (checkout / ".git").is_file()


def _service_install() -> tuple[Path | None, Path | None]:
    """(서비스 venv, 운영 체크아웃). editable 원본이 있는 첫 후보다. 서비스가 그 venv 를 실행한다는
    근거(문서의 자리 · 설치된 유닛, `runs`)가 있으면 자리와 상관없이 운영이다 — 서비스가 체크아웃
    안의 `.venv` 를 실행하는 꼴도 있다(P6 재확인 2 · v2-2). 근거가 없는 후보(PATH)는 venv 가 원본
    밖에 있고 원본이 링크된 워크트리가 아닐 때만 운영이다: 개발자 자신의 워크트리 `.venv` 는
    venv 가 원본 안에 있어 빠지고, 링크된 워크트리를 설치한 venv 는 개발 빌드다. 서비스를
    워크트리(`remote_ci_monitor-service`)에서 돌리는 머신이 있다(P3 5라운드 v1-3)."""
    for venv, runs in _venv_candidates():
        checkout = _editable_source(venv)
        if checkout is None or (inside(venv, checkout) and not runs):
            continue
        if _is_linked_worktree(checkout) and not runs:
            continue
        return venv, checkout
    return None, None


def _config_dir() -> Path | None:
    """서비스의 설정 자리 — 문서의 `~/.config/rcm` 뿐이다. 세션의 `XDG_CONFIG_HOME` 은 **이
    세션의** CLI 가 설정을 찾는 자리지 서비스의 것이 아니다(launchd·systemd 유닛은 셸 환경을
    물려받지 않는다). 그것을 따르면 시험 XDG 설정이 「운영」이 되어 진짜 data_dir 이 보호에서
    빠진다(M5l L4.10). 판정 쪽은 `_effective_data_dir` 이 XDG 를 CLI 순서대로 본다."""
    root = Path("~/.config/rcm").expanduser()
    if (root / "server.toml").exists() or (root / "worker.toml").exists():
        return _norm(root)
    return None


def _data_dir(config_dir: Path | None) -> Path | None:
    if config_dir is not None and tomllib is not None:
        config = config_dir / "server.toml"
        try:
            with config.open("rb") as handle:
                raw = tomllib.load(handle)
            declared = raw.get("server", {}).get("data_dir")
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            declared = None
        if isinstance(declared, str) and declared:
            return _norm(Path(declared).expanduser())
    default = Path("~/.local/share/rcm").expanduser()
    return _norm(default) if default.exists() else None


def find_production() -> Production:
    venv, checkout = _service_install()
    config_dir = _config_dir()
    return Production(
        checkout=checkout, venv=venv, config_dir=config_dir, data_dir=_data_dir(config_dir)
    )


# ── 훅 진입점 ────────────────────────────────────────────────────────────────


def _about_setup(payload: Any) -> bool:
    """훅 입력이 `rcm setup` 에 닿을 수 있는 Bash 명령인가(`_setup_net`). 무엇이 터져도 답한다."""
    try:
        tool_input = payload.get("tool_input") or {}
        command = tool_input.get("command")
        return payload.get("tool_name") == "Bash" and _setup_net(str(command or ""))
    except Exception:  # noqa: BLE001 — 모양이 이상한 입력은 setup 이 아니다
        return False


def main() -> int:
    payload: Any = {}
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        cwd = Path(str(payload.get("cwd") or os.getcwd()))
        verdict = decide(
            str(payload.get("tool_name", "")),
            payload.get("tool_input") or {},
            find_production(),
            cwd,
            local_config=(cwd / "rcm.toml").exists(),
            environ=os.environ,
        )
    except Exception as error:
        if not _about_setup(payload):
            # 가드가 터져도 작업은 막지 않는다 — 대신 눈에 보이게 알린다
            print(json.dumps({"systemMessage": f"guard_production.py failed: {error!r}"}))
            return 0
        # setup 판정 중 터지면 막는다 — setup 이 어디에 쓸지 모른다(P3 3라운드, fail closed)
        verdict = Verdict(
            "deny",
            f"The hook failed while judging this `rcm setup` command ({error!r}), so it cannot "
            "tell where setup would write; it refuses it. Write it as a plain `rcm setup …` "
            f"command, or fix tools/guard_production.py. See {DOCS}.",
        )
    if verdict is None:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": verdict.decision,
                    "permissionDecisionReason": verdict.reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
