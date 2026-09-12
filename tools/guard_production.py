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

알려진 한계(실수 방지용이지 적대자 방어가 아니다): `bash -c '…'` · `sh -c` · `xargs` ·
`uv run` · `$(…)` 안의 명령은 보지 않는다. 심링크 별칭(`/tmp` ↔ `/private/tmp`, 운영 data_dir
로 가는 링크)은 못 본다 — 경로는 `normpath` 로만 비교한다. `$VAR` 은 훅 프로세스의 환경으로
펼치고, 같은 명령줄 앞 조각의 `VAR=…; …` 정의는 못 본다. `pushd` 는 `cd` 가 아니다.

훅 자체가 터지면 막지 않고 경고만 남긴다. 모든 Bash 를 막는 가드는 가드가 아니라 고장이다.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

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
#: 명령 앞에서 떼어 내는 감싸기 — 뒤의 명령을 그대로 실행한다.
WRAPPERS = frozenset({"sudo", "env", "exec", "command", "nohup", "time", "builtin"})
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


def inside(path: Path | None, parent: Path | None) -> bool:
    """`parent` 안에 있나. 이름이 겹치는 형제 폴더(`…-dev`)는 밖이다."""
    if path is None or parent is None:
        return False
    try:
        _norm(path).relative_to(_norm(parent))
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
        if head in WRAPPERS:
            in_env = head == "env"
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
    """`rcm serve` 처럼 이 도구를 실행하는 조각이면 하위 명령을 돌려준다."""
    if not argv:
        return None
    head = _name(argv[0])
    rest = argv[1:]
    if head.startswith("python") and rest[:1] == ["-m"]:
        if rest[1:2] not in (["remote_ci_monitor.cli"], ["remote_ci_monitor"]):
            return None
        rest = rest[2:]
    elif head not in ("rcm", "remote-ci-monitor"):
        return None
    for token in rest:
        if not token.startswith("-"):
            return token
    return None


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

    return None


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


def decide(
    tool_name: str,
    tool_input: dict[str, object],
    prod: Production,
    cwd: Path,
    *,
    local_config: bool = False,
    environ: Mapping[str, str] | None = None,
) -> Verdict | None:
    """막을 이유가 있으면 Verdict, 없으면 None. 운영 설치를 못 찾았으면 언제나 None.

    `local_config` 는 세션 cwd 에 `./rcm.toml` 이 있다는 뜻이다 — 그러면 `--config` 없는
    `rcm serve` 도 운영 설정을 집지 않는다(`cd` 뒤에는 그 폴더에서 다시 본다). `$RCM_CONFIG` ·
    `$RCM_SERVER_DATA_DIR` · `$XDG_CONFIG_HOME` 은 `environ`(None 이면 프로세스 환경)에서 읽는다.
    """
    if not prod.known:
        return None
    session = os.environ if environ is None else environ
    if tool_name in ("Write", "Edit", "NotebookEdit"):
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
