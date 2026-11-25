"""운영 rcm 설치를 개발 세션이 건드리지 못하게 막는 PreToolUse 훅.

`.claude/settings.json` 이 Bash · Write · Edit 앞에서 이 파일을 부른다. 훅 입력 JSON 을
stdin 으로 받아 판정 JSON 을 stdout 으로 낸다.

운영 설치는 기계에서 스스로 찾는다. **서비스 venv** 는 「PATH 의 `rcm` 이 있는 venv」가
아니다 — 워크트리 `.venv` 를 활성화한 셸에서는 그게 워크트리 빌드라 규칙이 스스로를 무력화한다
(2026-09-10 의 사고 모양, 리뷰 #89 B-1). 서비스 venv 는 **editable 설치(`direct_url.json`)가
본 체크아웃(링크된 워크트리가 아닌 것)을 가리키고 그 체크아웃 밖에 있는 venv** 다. 후보는
문서의 자리(`~/.local/share/rcm-venv`) → 설치된 서비스 유닛이 적은 `rcm` → PATH 의 `rcm`
전부의 순서로 보고 첫 것을 잡는다. 그 원본 폴더가 「운영 체크아웃」이다. 그런 설치가 없는
컴퓨터(이 레포를 clone 한 다른 사람)에서는 아무것도 막지 않는다.

판정 `decide()` 는 운영 설치를 인자(`Production`)로, 환경을 인자(`environ`)로 받는다 — 시계도
운영 설치도 안 본다. 읽는 파일은 명령이 이름 댄 설정 파일(`--config` · `./rcm.toml` ·
`$XDG_CONFIG_HOME/rcm/server.toml`)과 PATH 의 실행 파일(`_which`)뿐이다.
발견 `find_production()` 이 기계를 본다.

`rcm setup` 에는 규칙 셋이 있다(M5t-a §4.1): 운영 설정 폴더에 쓰는 것, 운영 라벨의 서비스
파일을 쓰는 것, 운영 `data_dir`(그 안 포함)을 고르는 것을 막는다. setup 은 DB 를 열지 않으므로
`DB_OPENING` · `MIGRATING` 에는 넣지 않는다.

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
은 다르다: 셸 단계(따옴표 · `$VAR` · `~` · 리다이렉션)를 훅이 하고, 어디에 쓰는지는 setup 의
해석기(`remote_ci_monitor.core.setup.where`)를 들여와 묻는다. 셸 단계로 다 펼칠 수 없는 값
(`${VAR:-x}` · `$(…)` · 훅의 환경에 없는 변수)과 해석기를 못 들여오는 경우는 막는다(fail closed).
macOS 에서는 경로를 대소문자 구분 없이 견준다(`FOLD_CASE`).

훅 자체가 터지면 막지 않고 경고만 남긴다. 모든 Bash 를 막는 가드는 가드가 아니라 고장이다.
"""

from __future__ import annotations

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
#: 모두 이 안에 있다. `rcm setup --service-file` 이 이 안을 가리키면 거절한다 — 운영 서비스 파일
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
#: `core.setup.FOLD_CASE` 와 같은 규칙이다. 시험이 바꿔 끼운다
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


def _hit(tokens: list[str], cwd: Path, prod: Production) -> tuple[str, Path] | None:
    """토큰들 중 운영 경로를 가리키는 첫 자리를 돌려준다."""
    for token in tokens:
        path = _as_path(token, cwd)
        if path is None:
            continue
        for name, root in prod.labelled():
            if inside(path, root):
                return name, path
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


def _strip_prefix(tokens: list[str]) -> tuple[list[str], dict[str, str], set[str], bool]:
    """(argv, 조각 앞의 VAR=value, `-u` 로 지운 이름, `-i` 가 있었나). `env` 의 `-i`·`-u NAME`·
    `--unset=NAME`·`--` 를 알고, `sudo`·`exec`·`nohup` 같은 감싸기를 뗀다."""
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
    if inside(target, prod.checkout):
        return Verdict(
            "deny",
            f"{target} is inside the production checkout ({prod.checkout}): the build machine "
            "runs this folder through an editable install, so editing it changes what is running "
            "right now, and the next `git pull` will conflict. Make the change in a worktree, "
            f"open a pull request to `dev`, and let it reach `main`. See {DOCS}.",
        )
    for name, root in prod.labelled():
        if inside(target, root):
            return Verdict(
                "deny",
                f"{target} is inside {name} ({root}). A development session does not write there; "
                f"give a test server its own config, port and data_dir instead. See {DOCS}.",
            )
    return None


def _bash_verdict(
    command: str, cwd: Path, prod: Production, local_config: bool, session: Mapping[str, str]
) -> Verdict | None:
    """조각마다 판정한다. `cd <dir>` 은 뒤 조각의 cwd 를 바꾸고, 그러면 `./rcm.toml` 이 있는지도
    그 폴더에서 다시 본다(`(cd …)` 서브셸의 `cd` 도 뒤로 새지만 그쪽이 안전한 방향이다)."""
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
        verdict = _segment_verdict(argv, cwd, prod, local_config, env=env, segment=segment)
        if verdict is not None:
            return verdict
    return None


def _segment_verdict(
    argv: list[str],
    cwd: Path,
    prod: Production,
    local_config: bool,
    env: SegmentEnv | None = None,
    segment: str = "",
) -> Verdict | None:
    """조각 하나의 판정. `segment` 는 따옴표가 남은 원래 글자다 — `rcm setup` 은 셸 단계를 그
    글자로 다시 한다(`_setup_calls`)."""
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
    if subcommand == "setup" and not _wants_help(argv):
        verdict = _setup_verdict(segment or shlex.join(argv), cwd, prod, env)
        if verdict is not None:
            return verdict
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
# 그것을 들여와 부른다. 훅이 하는 일은 셸 단계뿐이다: 따옴표를 풀고, 변수와 `~` 를 펼치고,
# 리다이렉션과 주석을 뗀다. P3 1 · 2라운드의 HIGH 는 모두 훅이 setup 의 계산을 따로 다시 하다
# 어긋난 것이었다(빈 값 · `${VAR:-x}` · 대소문자 · 줄인 옵션 · `--service-file`).


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


class _Unreadable(Exception):
    """셸 단계를 다 해도 setup 이 받을 글자를 모른다 — 운영으로 본다."""


#: 리다이렉션 — `2>&1` · `>>log` · `&>x` · `<in`. 단어의 맨 앞에서만 본다
_REDIRECT = re.compile(r"(\d*|&)(>>|>&|<&|<<<|<<-|<<|<>|>\||>|<)")
#: 훅이 펼칠 수 있는 변수 참조 — `$NAME` · `${NAME}` 뿐이다. 나머지 `$` 는 모른다
_REF = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
#: 따옴표 밖에서 셸이 단어를 바꾸는 글자 — 글롭과 중괄호 펼치기
_GLOB = frozenset("*?[{")

#: 단어 하나 — (종류, 글자) 조각들. 종류는 `bare`(따옴표 밖), `dq`(큰따옴표 안), `lit`(작은따옴표
#: 안이나 백슬래시로 escape 한 글자 — 펼치지 않는다)
_Word = list[tuple[str, str]]


def _group_trimmed(segment: str) -> str:
    """조각을 감싼 `(` · `{` 와 짝 `)` · `}`, 끝의 `&`(백그라운드)를 뗀다."""
    text = segment.strip()
    while text[:1] in ("(", "{"):
        text = text[1:].strip()
    while True:
        bare = text.rstrip(" \t;")
        if bare.endswith("&") and not bare.endswith(("&&", ">&", "<&")):
            text = bare[:-1]
        elif bare.endswith(")") and bare.count("(") < bare.count(")"):
            text = bare[:-1]
        elif bare.endswith("}") and bare[-2:-1] in ("", " ", "\t", ";"):
            text = bare[:-1]
        else:
            return bare


def _lex(text: str) -> list[_Word]:
    """셸이 나누는 대로 단어를 나눈다. 리다이렉션과 그 대상, `#` 주석은 뺀다. 명령 치환
    (`$(…)` · 백틱), `$'…'`, 단어 속의 `<` · `>` · `(` · `|` · `&` 처럼 훅이 읽지 못하는 것은
    `_Unreadable` 이다."""
    words: list[_Word] = []
    parts: _Word = []
    started = False
    target = False  # 다음 단어는 리다이렉션의 대상이다
    i, n = 0, len(text)

    def push(kind: str, chunk: str) -> None:
        if parts and parts[-1][0] == kind:
            parts[-1] = (kind, parts[-1][1] + chunk)
        else:
            parts.append((kind, chunk))

    def end() -> None:
        nonlocal parts, started, target
        if started:
            if target:
                target = False
            else:
                words.append(parts)
        parts, started = [], False

    while i < n:
        c = text[i]
        if c in " \t\n":
            end()
            i += 1
            continue
        if not started:
            if c == "#":
                break
            m = _REDIRECT.match(text, i)
            if m:
                i = m.end()
                if m.group(2) in (">&", "<&"):
                    j = i
                    while j < n and (text[j].isdigit() or text[j] == "-"):
                        j += 1
                    if j > i:  # `2>&1` · `>&-` — 대상이 파일이 아니다
                        i = j
                        continue
                target = True
                continue
        started = True
        if c == "\\":
            push("lit", text[i + 1 : i + 2])
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise _Unreadable("an unclosed single quote")
            push("lit", text[i + 1 : j])
            i = j + 1
        elif c == '"':
            i += 1
            while True:
                if i >= n:
                    raise _Unreadable("an unclosed double quote")
                c = text[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\" and text[i + 1 : i + 2] in ("$", "`", '"', "\\"):
                    push("lit", text[i + 1])
                    i += 2
                    continue
                push("dq", c)
                i += 1
        elif c == "$" and text[i + 1 : i + 2] in ("'", '"'):
            raise _Unreadable("$'…' or $\"…\" quoting")
        elif c in "`|&;<>()":
            raise _Unreadable(f"{c!r} inside a word")
        else:
            push("bare", c)
            i += 1
    end()
    return words


def _plain(word: _Word) -> str:
    """따옴표만 푼 글자(shlex 와 같다) — 명령 이름과 감싸기를 찾을 때 쓴다."""
    return "".join(chunk for _, chunk in word)


def _expand(chunk: str, kind: str, lookup: Mapping[str, str]) -> str:
    """조각 하나의 `$NAME` · `${NAME}` 를 펼친다. 그 밖의 `$`(`${X:-y}` · `${X%y}` · `$1` · `$$`
    · `$(…)`), 백틱, 훅의 환경에 없는 변수, 따옴표 밖의 글롭 · 중괄호와 단어를 쪼갤 값은
    모른다(`_Unreadable`)."""
    if kind == "lit":
        return chunk
    out: list[str] = []
    pos = 0
    for m in _REF.finditer(chunk):
        out.append(chunk[pos : m.start()])
        name = m.group(1) or m.group(2)
        value = lookup.get(name)
        if value is None:
            raise _Unreadable(f"${name} is not set in the hook's environment")
        if kind == "bare" and (any(c.isspace() for c in value) or _GLOB & set(value)):
            raise _Unreadable(f"${name} would be split or globbed by the shell")
        out.append(value)
        pos = m.end()
    out.append(chunk[pos:])
    rest = _REF.sub("", chunk)
    if "$" in rest or "`" in rest:
        raise _Unreadable(f"{chunk!r} needs the shell to expand it")
    if kind == "bare" and _GLOB & set(rest):
        raise _Unreadable(f"{chunk!r} would be globbed or brace-expanded by the shell")
    return "".join(out)


def _expand_word(word: _Word, lookup: Mapping[str, str], home: Path, *, start: int = 0) -> str:
    """단어 하나를 셸처럼 펼친다. `start` 는 할당(`NAME=value`)의 값이 시작하는 자리다 — 값의
    맨 앞 `~` 도 펼친다. `~` · `~/…` 는 `home`, `~user` 는 그 사용자의 홈이다."""
    parts = list(word)
    if start:
        kind, chunk = parts[0]
        parts[0] = (kind, chunk[start:])
    kind, chunk = parts[0] if parts else ("lit", "")
    if kind == "bare" and chunk.startswith("~"):
        prefix, slash, rest = chunk.partition("/")
        if not slash and len(parts) > 1:
            raise _Unreadable(f"{_plain(word)!r} mixes a ~ prefix with quotes")
        if prefix in ("~+", "~-"):
            raise _Unreadable(f"{prefix} needs the shell's directory stack")
        head = str(home) if prefix == "~" else os.path.expanduser(prefix)
        parts[0:1] = [("lit", head), ("bare", slash + rest)]
    return "".join(_expand(chunk, kind, lookup) for kind, chunk in parts)


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


@dataclass(frozen=True)
class SetupCall:
    """셸이 setup 에 넘길 것 하나 — `setup` 뒤의 인자, 그 명령의 환경, 그 명령의 HOME."""

    args: tuple[str, ...]
    environ: Mapping[str, str]
    home: Path


def _setup_calls(
    segment: str, cwd: Path, session: Mapping[str, str], core: ModuleType
) -> list[SetupCall]:
    """셸 단계: 조각 하나를 셸처럼 풀어 setup 이 받을 인자와 환경을 만든다.

    셸은 인자의 `$VAR` 와 `~` 를 제 환경(세션 + 그 조각의 `$PWD`)으로 펼치고, 앞머리
    `VAR=value` 는 명령에만 준다. 셸마다 앞머리 할당이 뒤의 펼치기에 닿는지가 다르므로 앞머리를
    먼저 본 펼치기도 후보로 둔다. 둘이 같으면 하나다."""
    words = _lex(_group_trimmed(segment))
    plain = [_plain(word) for word in words]
    shell = {**session, "PWD": str(cwd)}
    shell_home = Path(session["HOME"]) if session.get("HOME") else Path.home()
    calls: list[SetupCall] = []
    for prefix_first in (False, True):
        assigned: dict[str, str] = {}
        unknown: set[str] = set()
        unset: set[str] = set()
        cleared = in_env = False
        index = 0
        while index < len(words):
            head = plain[index]
            if _name(head) in WRAPPERS:
                in_env = _name(head) == "env"
            elif in_env and head in ("-i", "--ignore-environment", "-"):
                cleared = True
            elif in_env and head in ("-u", "--unset"):
                unset.add(plain[index + 1] if index + 1 < len(plain) else "")
                index += 1
            elif in_env and head.startswith("--unset="):
                unset.add(head.split("=", 1)[1])
            elif in_env and head == "--":
                in_env = False
            elif _ASSIGNMENT.match(head) and (
                in_env or (words[index][0][0] == "bare" and _ASSIGNMENT.match(words[index][0][1]))
            ):
                name = head.split("=", 1)[0]
                lookup = {**shell, **assigned} if prefix_first else shell
                home = (
                    Path(assigned["HOME"]) if prefix_first and assigned.get("HOME") else shell_home
                )
                try:
                    assigned[name] = _expand_word(words[index], lookup, home, start=len(name) + 1)
                    unknown.discard(name)
                except _Unreadable:
                    unknown.add(name)
            else:
                break
            index += 1
        found = _subcommand_index(plain[index:])
        if found is None or plain[index + found] != "setup":
            raise _Unreadable("the shell words do not read as `rcm setup`")
        if unknown & set(_WHERE_ENV):
            raise _Unreadable(f"{', '.join(sorted(unknown & set(_WHERE_ENV)))} before the command")
        environ = {} if cleared else {k: v for k, v in session.items() if k not in unset}
        environ.update(assigned)
        command_home = core.home_of(environ, Path.home())
        lookup = {**shell, **assigned} if prefix_first else shell
        tilde = command_home if prefix_first else shell_home
        args = tuple(_expand_word(word, lookup, tilde) for word in words[index + found + 1 :])
        call = SetupCall(args, environ, command_home)
        if call not in calls:
            calls.append(call)
    return calls


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


def _service_place(path: Path, home: Path) -> Path | None:
    """서비스 관리자가 읽는 자리(`SETUP_SERVICE_PLACES`) 안이면 그 자리. `~` 는 이 사용자의 홈
    (세션의 HOME)이다 — 운영 서비스는 명령 앞의 `HOME=` 으로 옮겨 가지 않는다."""
    for place in SETUP_SERVICE_PLACES:
        root = Path(str(home) + place[1:]) if place.startswith("~") else Path(place)
        if inside(path, root):
            return _norm(root)
    return None


def _setup_verdict(segment: str, cwd: Path, prod: Production, env: SegmentEnv) -> Verdict | None:
    """`rcm setup` 의 세 규칙. setup 은 DB 를 열지 않지만 운영 자리에 설정 · 서비스 파일을 쓰거나
    운영 `data_dir` 을 고른 서버 설정을 만들 수 있다 — 그 서비스는 setup 을 돌린 빌드로 운영 DB 를
    연다. 자리는 setup 의 해석기 `core.where` 가 정한다(셸 단계는 `_setup_calls`).

    1. 쓸 자리(설정 파일 · 서비스 파일)가 운영 설정 폴더 안이거나, 서비스 파일이 서비스 관리자가
       읽는 자리(`SETUP_SERVICE_PLACES` — 운영 서비스 파일이 있는 곳) 안이면 거절한다.
    2. 서비스 라벨이 운영 라벨이면 거절한다. `--label` 이 없으면 역할의 표준 라벨이다. 역할을 안
       주면 setup 이 물을 수 있으니 모든 역할로 본다. client 는 서비스 파일이 없다.
    3. 역할 2 · 4 의 `data_dir` 이 운영 `data_dir` 이거나 그 안이면 거절한다(6-A-L4 · 6-B-V5).

    모르면 운영으로 본다(fail closed): 해석기를 못 들여오거나, 셸 단계를 다 해도 글자가 남거나,
    setup 의 파서 · 해석기가 거절할 명령줄이면 거절한다."""
    if not prod.known:
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
    try:
        calls = _setup_calls(segment, cwd, env.session, core)
    except _Unreadable as e:
        return Verdict(
            "deny",
            f"`rcm setup` here has a value the hook cannot read the way the shell will ({e}), so "
            "it cannot tell whether setup would write to the production install. Write the paths, "
            f"role and label out, or quote them. See {DOCS}.",
        )
    session_home = Path(env.session["HOME"]) if env.session.get("HOME") else Path.home()
    units = {unit_name(standard) for standard in SETUP_STANDARD_LABELS}
    manager = core.manager_of(sys.platform)
    for call in calls:
        for args in _with_earlier_values(call.args):
            try:
                ns = core.parse_args(args)
                roles = [ns.role] if ns.role else list(core.ROLES)
                places = [
                    core.where(
                        role,
                        environ=call.environ,
                        cwd=cwd,
                        home=call.home,
                        manager=manager,
                        config=ns.config,
                        label=ns.label,
                        service_file=ns.service_file,
                        data_dir=ns.data_dir,
                    )
                    for role in roles
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


def _setup_place_verdict(
    places: list[Any], prod: Production, session_home: Path, units: set[str]
) -> Verdict | None:
    """`core.where` 의 답 하나하나에 세 규칙을 댄다."""
    for found in places:
        for target in (found.config, found.service_file):
            if target is None:
                continue
            if inside(target, prod.config_dir):
                return Verdict(
                    "deny",
                    f"`rcm setup` here would write {target}, inside the server's config directory "
                    f"({prod.config_dir}). Give it a test HOME or XDG_CONFIG_HOME, or `--config` "
                    f"and `--service-file` paths of its own. See {DOCS}.",
                )
        if found.service_file is not None:
            target = found.service_file
            place = _service_place(target, session_home)
            if place is not None:
                return Verdict(
                    "deny",
                    f"`rcm setup --service-file {target}` would write inside {place}, where the "
                    "service manager reads service files — the production service's files live "
                    "there. Let setup write under its config folder's `service/` and copy the "
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
            if inside(data, prod.data_dir):
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


def _venv_candidates() -> list[Path]:
    """서비스 venv 후보를 순서대로: 문서의 자리 → 설치된 서비스 유닛이 적은 `rcm` → PATH 의
    `rcm`·`remote-ci-monitor` 전부. 심링크(`~/.local/bin/rcm`)는 실제 파일로 푼다."""
    out: list[Path] = []

    def add(launcher: Path | None) -> None:
        if launcher is None:
            return
        real = Path(os.path.realpath(launcher))
        venv = _norm(real.parent.parent)
        if venv not in out and (venv / "pyvenv.cfg").exists():
            out.append(venv)

    documented = Path(SERVICE_VENV).expanduser()
    if (documented / "pyvenv.cfg").exists():
        out.append(_norm(documented))
    for unit in SERVICE_UNITS:
        add(_launcher_in_unit(Path(unit).expanduser()))
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        for name in ("rcm", "remote-ci-monitor"):
            launcher = Path(directory) / name
            if directory and launcher.is_file():
                add(launcher)
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
    """(서비스 venv, 운영 체크아웃). editable 원본이 본 체크아웃이고 venv 가 그 밖에 있는 첫
    후보 — 워크트리 `.venv` 는 원본이 링크된 워크트리이고 venv 가 그 안에 있어 둘 다에 걸린다."""
    for venv in _venv_candidates():
        checkout = _editable_source(venv)
        if checkout is None or _is_linked_worktree(checkout) or inside(venv, checkout):
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


def main() -> int:
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
    except Exception as error:  # 가드가 터져도 작업은 막지 않는다 — 대신 눈에 보이게 알린다
        print(json.dumps({"systemMessage": f"guard_production.py failed: {error!r}"}))
        return 0
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
