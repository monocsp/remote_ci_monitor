"""`rcm setup` 의 순수한 판단(M5t-a 결정 239~242, docs/m5t-workplan.md §4.1).

역할별 결정 · 검증 · 서비스 파일 내용 · 출력할 명령을 만든다. 시계도 I/O 도 없다 — 묻고 쓰고
조회하는 쪽은 `remote_ci_monitor/setup.py` 다. 질문과 안내는 CLI 문구라 영어다(AGENTS.md).

**setup 이 어디에 쓰는지는 `where` 하나가 정한다.** setup 의 명령줄 정의(`add_arguments`)와
경로 해석기(`where`)가 여기 있고, setup 과 운영 보호 훅(`tools/guard_production.py`)이 둘 다
이것을 쓴다. 훅은 셸이 하는 일(변수 펼치기 · `~` · 따옴표)만 하고 나머지는 이 함수에 맡긴다.
P3 1 · 2라운드의 HIGH 는 모두 훅이 setup 의 계산을 따로 다시 하다 어긋난 것이었다. 그래서 이
모듈과 이것이 들여오는 모듈(`core.doctor` · `core.mdns` · `core.progress` · `core.model`)은
표준 라이브러리만 쓴다 — 훅은 패키지를 설치하지 않은 python3 로 돈다.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import plistlib
import re
import shlex
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remote_ci_monitor.core import doctor as dc
from remote_ci_monitor.core.mdns import ipv4_rank

LAUNCHD, SYSTEMD = dc.LAUNCHD, dc.SYSTEMD
ROLES = dc.ROLES
SERVER_ROLES = (dc.ROLE_COORDINATOR, dc.ROLE_COORDINATOR_WORKER)
#: 표준 라벨 — `examples/` 의 서비스 파일과 `rcm init worker --service-file` 이 쓰는 이름
STANDARD_LABELS = {
    "server": "com.remote-ci-monitor.server",
    "worker": "com.remote-ci-monitor.worker",
}
#: systemd 유닛 이름 규칙(6-B-V1): 표준 라벨은 `examples/systemd/` 의 이름, 다른 라벨은
#: `<label>.service`. `tools/guard_production.py` 의 `unit_name` 이 같은 규칙을 따로 들고 있다
#: (훅은 패키지를 들여오지 않는다) — tests/test_guard_production_setup.py 가 둘을 맞춘다
_STANDARD_UNITS = {
    STANDARD_LABELS["server"]: "rcm-server",
    STANDARD_LABELS["worker"]: "rcm-worker",
}
#: 서버 서비스의 파일 디스크립터 상한 — `examples/` 의 서버 서비스 파일과 같은 값
NOFILE = 4096
#: 라벨 — launchd 의 `Label` 이자 파일 이름이다. 경로 구분자나 공백이 들어가면 안 된다.
#: `fullmatch` 로 본다 — `$` 는 끝 줄바꿈 앞에서도 맞는다(P3 2라운드 v1-5)
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

#: `--yes`(또는 물을 사람이 없을 때) 역할마다 꼭 줘야 하는 인자(§4.1 표)
YES_REQUIRED: dict[str, tuple[str, ...]] = {
    dc.ROLE_CLIENT: ("server",),
    dc.ROLE_WORKER: ("server", "pool", "lanes"),
    dc.ROLE_COORDINATOR: ("bind",),
    dc.ROLE_COORDINATOR_WORKER: ("bind",),
}

#: 역할마다 쓰는 플래그(§4.1 `--yes` 표의 꼭 줄 인자와 선택 인자, 그리고 모든 역할의 `--config` ·
#: `--force`). 이 밖의 플래그는 그 역할에 쓰이지 않는다 — 조용히 버리지 않고 알린다(P3 v1-7)
_COMMON_FLAGS = frozenset({"config", "force"})
_SERVER_FLAGS = frozenset(
    {"bind", "port", "data_dir", "label", "advertise", "service_file", "reuse_data_dir"}
)
ROLE_FLAGS: dict[str, frozenset[str]] = {
    dc.ROLE_CLIENT: _COMMON_FLAGS | {"server", "by"},
    dc.ROLE_WORKER: _COMMON_FLAGS | {"server", "pool", "lanes", "label", "service_file"},
    dc.ROLE_COORDINATOR: _COMMON_FLAGS | _SERVER_FLAGS,
    dc.ROLE_COORDINATOR_WORKER: _COMMON_FLAGS | _SERVER_FLAGS,
}


def unused_flags(role: str, given: Mapping[str, Any]) -> list[str]:
    """준 플래그 가운데 그 역할이 쓰지 않는 것(`--lanes` 처럼 플래그 이름으로). 값이 None 이나
    False 인 것은 주지 않은 것이다. `advertise` 는 False 도 준 것이다(`--no-advertise`)."""
    out: list[str] = []
    for name, value in given.items():
        if name in ROLE_FLAGS.get(role, frozenset()) or value is None:
            continue
        if name == "advertise":
            out.append("--advertise" if value else "--no-advertise")
        elif value is not False:
            out.append("--" + name.replace("_", "-"))
    return out


#: bind 의 종류
TAILSCALE, LAN, ALL, LOOPBACK = "tailscale", "lan", "all", "loopback"
ALL_ADDRESS = "0.0.0.0"
_LOOPBACK = ("127.0.0.1", "localhost", "::1")

READ_OPEN = (
    "anyone on this network can read the queue, the shelf and the machine list "
    "(reads need no token)"
)
TAILNET_OPEN = (
    "everyone on your tailnet can read the queue, the shelf and the machine list "
    "(reads need no token)"
)
DHCP_NOTE = (
    "reserve this address for this machine in your router (DHCP reservation) — the server "
    "does not start when the address changes"
)
TAILSCALE_NOTE = (
    "advertise = false: on a Tailscale address mDNS would hand LAN workers an address they "
    "cannot reach — give workers and clients the server URL instead"
)
LOOPBACK_NOTE = (
    "only this machine can reach a loopback address — workers and clients on other machines "
    "cannot connect"
)


def service_kind(role: str) -> str | None:
    """역할이 띄우는 서비스 — `server`(rcm serve) · `worker`(rcm worker) · 없음(client)."""
    if role in SERVER_ROLES:
        return "server"
    if role == dc.ROLE_WORKER:
        return "worker"
    return None


def default_label(role: str) -> str | None:
    kind = service_kind(role)
    return STANDARD_LABELS[kind] if kind else None


def unit_name(label: str) -> str:
    """systemd 유닛 파일 이름(6-B-V1)."""
    return f"{_STANDARD_UNITS.get(label, label)}.service"


def service_file_name(label: str, manager: str) -> str:
    return unit_name(label) if manager == SYSTEMD else f"{label}.plist"


def log_file_name(label: str) -> str:
    """로그 파일 이름은 라벨에서 만든다. 표준 라벨은 지금의 이름(`server.log` ·
    `worker.log`) 그대로다(6-B-V7) — 운영 문서와 doctor 가 그 이름을 읽는다."""
    for kind, standard in STANDARD_LABELS.items():
        if label == standard:
            return f"{kind}.log"
    return f"{label}.log"


def label_problem(label: str) -> str | None:
    if _LABEL_RE.fullmatch(label):
        return None
    return f"--label must be letters, digits, '.', '_' or '-' (got {label!r})"


def missing_for_yes(role: str, given: Mapping[str, Any]) -> list[str]:
    """묻지 않고 돌 때 빠진 필수 인자(`--bind` 처럼 플래그 이름으로)."""
    return [f"--{name}" for name in YES_REQUIRED.get(role, ()) if given.get(name) is None]


#: 이름을 준 `--bind` 의 거절 — setup 은 이름을 풀지 않는다(DNS · mDNS, P5 A-E7)
NAME_NOT_ADDRESS = "give an address, not a name"


#: 괄호를 친 IPv6 주소(`[::1]`)의 거절 — 이름이 아니라 괄호가 문제다(P6 재확인 6)
BRACKETED_ADDRESS = "write an IPv6 address without the brackets, such as ::1"


def is_address(text: str) -> bool:
    """IPv4 · IPv6 주소 글자인가. 이름(`localhost` · `macmini.local`)은 아니다 — 풀지 않는다."""
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def address_problem(text: str) -> str | None:
    """`--bind` 글자의 문제 — 주소면 None, 괄호를 친 주소(`[::1]`)면 괄호를 빼라는 말, 그 밖은
    이름이라는 말. 이름을 풀지 않는다."""
    if is_address(text):
        return None
    if text.startswith("[") and text.endswith("]") and is_address(text[1:-1]):
        return BRACKETED_ADDRESS
    return NAME_NOT_ADDRESS


def normal_address(text: str) -> str:
    """주소를 적는 꼴 하나로 — IPv6 은 줄인 꼴(`0:0:0:0:0:0:0:0` → `::`, `0:0::1` → `::1`)이다.
    그래야 `bind_kind` 의 `::` · `::1` 과 같은 글자로 견준다. IPv4 는 그대로다."""
    address = ipaddress.ip_address(text)
    return str(address) if address.version == 6 else text


def bind_kind(address: str) -> str:
    if address == ALL_ADDRESS or address == "::":
        return ALL
    if address in _LOOPBACK or address.startswith("127."):
        return LOOPBACK
    if ipv4_rank(address) == 1:  # 100.64/10 — Tailscale(CGNAT)
        return TAILSCALE
    return LAN


def bind_choices(addresses: Sequence[str]) -> list[tuple[str, str]]:
    """역할 2·4 의 bind 선택지(⛔ Q10 추천). Tailscale 주소가 있으면 그것만 권하고, 없으면 LAN
    인터페이스 주소와 `0.0.0.0` 이다. 루프백은 선택지가 아니다 — 워커가 닿지 못한다. 직접 입력은
    따로 받는다(이 머신의 주소만)."""
    seen = list(dict.fromkeys(a for a in addresses if bind_kind(a) in (TAILSCALE, LAN)))
    tailnet = [(a, TAILSCALE) for a in seen if bind_kind(a) == TAILSCALE]
    if tailnet:
        return tailnet
    return [(a, LAN) for a in seen] + [(ALL_ADDRESS, ALL)]


def bind_warnings(kind: str) -> list[str]:
    """그 bind 를 고르면 따라오는 경고. 읽기 인증은 제안하지 않는다 — 있는 모드(`none` · `basic`)
    가운데 `basic` 은 TLS 프록시 뒤에서만 쓰고, 토큰으로 읽기를 막는 모드는 없다(⛔ Q14)."""
    if kind == TAILSCALE:
        return [TAILSCALE_NOTE, TAILNET_OPEN]
    if kind == LAN:
        return [READ_OPEN, DHCP_NOTE]
    if kind == ALL:
        return [READ_OPEN]
    if kind == LOOPBACK:
        return [LOOPBACK_NOTE]
    return []


def advertise_default(kind: str) -> bool:
    """대화형 질문의 기본값 — 서버의 기본 규칙(bind 가 루프백이 아니면 켬)과 같다. Tailscale 은
    묻지 않고 끈다."""
    return kind in (LAN, ALL)


def toml_str(value: str) -> str:
    # JSON 문자열의 이스케이프는 TOML 기본 문자열에서도 그 뜻이다
    return json.dumps(value, ensure_ascii=False)


def set_value(text: str, key: str, literal: str, *, commented: bool = False) -> str:
    """템플릿의 `key = …` 줄 하나의 값만 바꾼다. 뒤 주석은 남긴다. `commented` 면 `# key = …`
    줄을 살려 쓴다. 정확히 한 줄이 아니면 템플릿이 바뀐 것이다 — 조용히 넘어가지 않는다."""
    head = r"#\s*" if commented else ""
    rx = re.compile(rf'^{head}{re.escape(key)}\s*=\s*("[^"\n]*"|[^\s#]+)', re.M)
    if len(rx.findall(text)) != 1:
        raise RuntimeError(f"template must have exactly one '{key} = …' line")
    return rx.sub(lambda m: f"{key} = {literal}", text, count=1)


def server_toml(
    template: str, *, bind: str, port: int, data_dir: str, lanes: int, advertise: bool | None
) -> str:
    """서버 템플릿에서 bind · port · data_dir · lanes(· advertise)만 바꾼다. 줄 수는 그대로다."""
    text = set_value(template, "bind", toml_str(bind))
    text = set_value(text, "port", str(port))
    text = set_value(text, "data_dir", toml_str(data_dir))
    text = set_value(text, "lanes", str(lanes))
    if advertise is not None:
        text = set_value(text, "advertise", "true" if advertise else "false", commented=True)
    return text


def template_value(template: str, key: str) -> str | None:
    """템플릿의 기본값(`port = 8787` 의 `8787`) — 대화형 질문의 기본값으로 쓴다."""
    m = re.search(rf'^{re.escape(key)}\s*=\s*"?([^"\s#]+)"?', template, re.M)
    return m.group(1) if m else None


def server_plist(*, rcm: Path, config: Path, home: Path, label: str, log: Path, path: str) -> bytes:
    """launchd plist(macOS) — `rcm serve --config <server.toml>`. 토큰은 싣지 않는다."""
    doc = {
        "Label": label,
        "ProgramArguments": [str(rcm), "serve", "--config", str(config)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "SoftResourceLimits": {"NumberOfFiles": NOFILE},
        "WorkingDirectory": str(home),
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": {
            "PATH": path,
            "HOME": str(home),
            "LANG": "en_US.UTF-8",
            "PYTHONUNBUFFERED": "1",
        },
    }
    return plistlib.dumps(doc)


class UnitValueError(ValueError):
    """systemd 유닛에 쓸 수 없는 값 — 줄바꿈 · 제어 문자. setup 은 사용법 오류(종료 2)로 본다."""


def _unit_text(value: str) -> str:
    """유닛의 한 값. 줄바꿈 · 제어 문자는 유닛의 줄을 끊거나 바꾸므로 거절하고, `%` 는 지정자라
    `%%` 로 쓴다(systemd.unit 「Specifiers」)."""
    bad = next((c for c in value if ord(c) < 32 or ord(c) == 127), None)
    if bad is not None:
        raise UnitValueError(
            f"{value!r} holds a line break or control character, which a systemd unit cannot hold"
        )
    return value.replace("%", "%%")


def unit_path(value: str | Path) -> str:
    """경로 하나를 받는 키(`WorkingDirectory=` · `StandardOutput=append:`)의 값 — 따옴표를 풀지
    않는 키라 공백은 그대로 둔다."""
    return _unit_text(str(value))


def unit_word(value: str | Path, *, exec_line: bool = False) -> str:
    """`ExecStart=` 의 인자 하나 또는 `Environment=` 의 할당 하나(systemd.syntax 「Quoting」).
    공백 · 따옴표 · 백슬래시가 있으면 큰따옴표로 감싸고 안의 `\\` · `"` 를 escape 한다.
    `ExecStart=` 에서는 `$` 가 환경변수라 `$$` 로 쓴다(systemd.service 「Command lines」)."""
    text = _unit_text(str(value))
    if exec_line:
        text = text.replace("$", "$$")
    if text == "" or any(c.isspace() or c in "\"'\\;" for c in text):
        text = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def exec_start(argv: Sequence[str | Path]) -> str:
    """`ExecStart=` 의 값 — 인자마다 `unit_word`."""
    return " ".join(unit_word(a, exec_line=True) for a in argv)


def server_unit(*, rcm: Path, config: Path, home: Path, label: str, log: Path, path: str) -> bytes:
    """systemd 사용자 유닛(Linux) — `systemctl --user` 로 올린다. 토큰은 싣지 않는다. 경로는
    `unit_path` · `unit_word` 로 쓴다 — 공백 · `%` · `$` 가 든 홈과 venv 도 그대로 읽힌다."""
    text = f"""# remote_ci_monitor server ({label}) — written by `rcm setup`.
[Unit]
Description=remote_ci_monitor job server ({label})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={unit_path(home)}
ExecStart={exec_start([rcm, "serve", "--config", config])}
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
KillMode=mixed
TimeoutStopSec=30
LimitNOFILE={NOFILE}
Environment=PYTHONUNBUFFERED=1
Environment=LANG=en_US.UTF-8
Environment={unit_word(f"HOME={home}")}
Environment={unit_word(f"PATH={path}")}
StandardOutput=append:{unit_path(log)}
StandardError=append:{unit_path(log)}

[Install]
WantedBy=default.target
"""
    return text.encode("utf-8")


def worker_unit(*, rcm: Path, config: Path, home: Path, label: str, log: Path, path: str) -> bytes:
    """setup 이 쓰는 워커의 systemd 사용자 유닛 — 서버 유닛(`server_unit`)과 같은 방식으로 라벨의
    로그(`StandardOutput=append:`)와 `HOME` 을 싣는다(P5 B-E13 · A-E9). 토큰은 싣지 않는다.
    `rcm init worker --service-file` 은 지금처럼 `cli.worker_unit` 을 쓴다."""
    text = f"""# remote_ci_monitor worker ({label}) — written by `rcm setup`.
# The token is not in this file: put `token = "…"` in {unit_path(config)} (mode 600).
[Unit]
Description=remote_ci_monitor worker ({label})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={unit_path(home)}
ExecStart={exec_start([rcm, "worker", "--config", config])}
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
KillMode=mixed
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=LANG=en_US.UTF-8
Environment={unit_word(f"HOME={home}")}
Environment={unit_word(f"PATH={path}")}
StandardOutput=append:{unit_path(log)}
StandardError=append:{unit_path(log)}

[Install]
WantedBy=default.target
"""
    return text.encode("utf-8")


#: 서비스 관리자가 서비스 파일을 읽는 시스템 자리 — 사용자 자리(`install_dir`)와 함께 setup 이
#: 쓰지 않는 곳이다(§4.1 「설치 자리에 쓰지 않는다」). guard 의 `SETUP_SERVICE_PLACES` 와 같다
SYSTEM_SERVICE_DIRS = (
    Path("/Library/LaunchAgents"),
    Path("/Library/LaunchDaemons"),
    Path("/etc/systemd/user"),
    Path("/etc/systemd/system"),
)


def install_places(home: Path) -> tuple[Path, ...]:
    """서비스 관리자가 읽는 자리 전부 — launchd · systemd 의 사용자 자리와 시스템 자리."""
    return (install_dir(LAUNCHD, home), install_dir(SYSTEMD, home), *SYSTEM_SERVICE_DIRS)


def folds_case(platform: str) -> bool:
    """그 플랫폼의 파일시스템을 대소문자를 가리지 않는 것으로 보나. macOS 의 기본 볼륨(APFS ·
    HFS+)은 가리지 않는다 — `~/library/launchagents` 가 `~/Library/LaunchAgents` 다. 볼륨마다
    재는 길(pathconf `_PC_CASE_SENSITIVE`)은 파이썬이 내주지 않으므로 darwin 이면 늘 접는다(막는
    쪽). 플랫폼은 호출자가 준다(`sys.platform` 을 여기서 읽지 않는다 — P3 3라운드 v1-5). 훅
    (`tools/guard_production.py`)의 `FOLD_CASE` 와 같은 규칙이다."""
    return platform.startswith("darwin")


def place_key(path: str | Path, *, fold_case: bool) -> str:
    """경로를 견줄 때 쓰는 글자 — `normpath` 하고, 대소문자를 가리지 않는 디스크면 접는다
    (유니코드 정규화도 같이 한다. macOS 는 정규화 형태도 가리지 않는다)."""
    text = os.path.normpath(str(path))
    if fold_case:
        text = unicodedata.normalize("NFC", text).casefold()
    return text


def install_place_of(path: Path, home: Path, *, fold_case: bool) -> Path | None:
    """`path` 가 설치 자리 안이면 그 자리. 바로 아래만이 아니라 그 안 전부다 — systemd 는
    `<유닛>.d/` 의 drop-in 도 읽는다. 경로는 글자로만 비교한다(심링크는 풀지 않는다). 대소문자는
    `place_key` 가 `fold_case` 대로 다룬다."""
    text = place_key(path, fold_case=fold_case)
    for place in install_places(home):
        root = place_key(place, fold_case=fold_case)
        if text == root or text.startswith(root.rstrip("/") + "/"):
            return place
    return None


def install_dir(manager: str, home: Path) -> Path:
    if manager == LAUNCHD:
        return home / "Library" / "LaunchAgents"
    return home / ".config" / "systemd" / "user"


def install_path(label: str, manager: str, home: Path) -> Path:
    """라벨의 서비스 파일이 설치될 자리. setup 이 찍는 복사 명령의 목적지이고, doctor 가
    setup 자리의 파일에서 거꾸로 찾는 자리다(6-B-V1: 유닛 이름 규칙 하나를 같이 쓴다)."""
    return install_dir(manager, home) / service_file_name(label, manager)


def label_of_file(name: str) -> tuple[str, str] | None:
    """서비스 파일 이름 → (라벨, 서비스 관리자). `service_file_name` 을 거꾸로 푼다 — 표준 유닛
    이름(`rcm-server.service`)은 표준 라벨로 돌린다. 서비스 파일 이름이 아니면 None."""
    if name.endswith(".plist"):
        return name.removesuffix(".plist"), LAUNCHD
    if name.endswith(".service"):
        base = name.removesuffix(".service")
        standard = {unit: label for label, unit in _STANDARD_UNITS.items()}
        return standard.get(base, base), SYSTEMD
    return None


def log_dir(manager: str, home: Path) -> Path:
    if manager == LAUNCHD:
        return home / "Library" / "Logs" / "rcm"
    return home / ".local" / "state" / "rcm"


def install_commands(
    manager: str, *, staged: Path, installed: Path, label: str, log: Path
) -> list[str]:
    """사람이 칠 명령(⛔ Q9: setup 은 등록도 시작도 하지 않는다).

    복사는 있는 파일을 덮지 않는 `cp -n` 이다. 그 종료 코드는 macOS 와 GNU 가 다르게 낸다(macOS 는
    건너뛰면 1, coreutils 는 판마다 다르다, 6-B-U2) — 그래서 시작 명령은 `cp` 의 종료 코드가 아니라
    `cmp -s` 로 설치 자리의 파일이 방금 쓴 파일과 같을 때만 돈다. 다르면 있던 파일을 그대로 두고
    멈춘다."""
    q = shlex.quote
    copy = f"cp -n {q(str(staged))} {q(str(installed))}"
    same = f"cmp -s {q(str(staged))} {q(str(installed))} && "
    # 시작하지 않았으면 조용히 끝나지 않는다(P3 v1-9). `a && b || c` 라 시작이 실패해도 이 줄이
    # 나온다 — 그래서 문장이 둘 다 말하고, 바꿔 끼우는 길도 가리킨다(P5 B-E2)
    if manager == LAUNCHD:
        start = f"launchctl bootstrap gui/$(id -u) {q(str(installed))}"
        swap = "launchctl bootout, cp without -n, launchctl bootstrap"
    else:
        unit = installed.name
        start = f"systemctl --user daemon-reload && systemctl --user enable --now {q(unit)}"
        swap = "systemctl --user disable --now, cp without -n, daemon-reload, enable --now"
    said = (
        ' || echo "not started: the installed file differs from the one setup wrote,'
        f' or the start failed; to replace an installed one: {swap}"'
    )
    folders = f"mkdir -p {q(str(log.parent))} {q(str(installed.parent))}"
    return [folders, copy, same + start + said]


def replace_commands(manager: str, *, staged: Path, installed: Path, label: str) -> list[str]:
    """이미 올라간 서비스를 setup 이 쓴 파일로 바꿔 끼우는 순서(P5 B-E2) — 내리고, 덮어
    쓰고(`cp`, `-n` 없이), 다시 올린다. `--force service` 로 다시 썼을 때와 같은 라벨이 이미
    올라가 있을 때 찍는다."""
    q = shlex.quote
    copy = f"cp {q(str(staged))} {q(str(installed))}"
    if manager == LAUNCHD:
        return [
            f"launchctl bootout gui/$(id -u)/{q(label)}",
            copy,
            f"launchctl bootstrap gui/$(id -u) {q(str(installed))}",
        ]
    unit = q(installed.name)
    return [
        f"systemctl --user disable --now {unit}",
        copy,
        "systemctl --user daemon-reload",
        f"systemctl --user enable --now {unit}",
    ]


def remove_line(
    manager: str | None,
    *,
    installed: Path | None,
    label: str | None,
    files: Sequence[Path],
    staged: Path | None = None,
    preloaded: bool = False,
) -> str:
    """setup 이 한 일을 되돌리는 한 줄(P5 B-E3) — 서비스를 내리고(launchd `bootout`, systemd
    `disable --now`), 설치한 서비스 파일과 setup 이 쓴 파일을 지운다. data_dir 은 지우지 않는다.

    설치 자리의 파일은 setup 이 쓴 파일(`staged`)과 같을 때만(`cmp -s`) 내리고 지운다 — 다르면
    그 서비스는 다른 누가 쓴 것이라 그대로 둔다(P6 재확인 3 · v2-3). 같으면 내리기의 성패와
    상관없이 지운다(`cmp && { 내리기; rm; }`). 같은 라벨이 setup 전에 이미
    올라가 있었으면(`preloaded`) 그 서비스는 setup 이 쓴 것이 아닐 수 있다고 덧붙인다."""
    q = shlex.quote
    rm = "rm -f " + " ".join(q(str(p)) for p in files)
    service = ""
    if manager in (LAUNCHD, SYSTEMD) and installed is not None and staged is not None:
        same = f"cmp -s {q(str(staged))} {q(str(installed))}"
        gone = f"rm -f {q(str(installed))}"
        # 내리기가 실패해도(이미 내려간 서비스 · 닿지 않는 버스) cmp 가 같으면 지운다 — `&&` 로
        # 이으면 내리기의 실패가 `rm -f` 를 건너뛰었다(P6 재확인 v1-5 · v2-4)
        if manager == LAUNCHD and label:
            service = f"{same} && {{ launchctl bootout gui/$(id -u)/{q(label)}; {gone}; }}; "
        elif manager == SYSTEMD:
            unit = q(installed.name)
            service = (
                f"{same} && {{ systemctl --user disable --now {unit}; {gone}; "
                "systemctl --user daemon-reload; }; "
            )
    line = f"to remove: {service}{rm}"
    if service and preloaded:
        line += (
            f" (a service labelled {label} was already loaded before setup: it may not be one "
            "setup wrote, so the cmp keeps it unless its installed file is the one setup wrote)"
        )
    return line


def token_commands(rcm: Path, config: Path) -> list[str]:
    """coordinator 계열의 토큰 명령 — 서비스와 같은 빌드로 실행한다(다른 빌드는 DB 를 열면서
    마이그레이션한다). client 권한이고 `--admin` 은 붙이지 않는다. `--config` 는 하위 명령
    앞이다."""
    head = f"{shlex.quote(str(rcm))} token --config {shlex.quote(str(config))} add"
    return [f"{head} CLIENT_NAME", f"{head} WORKER_NAME --worker"]


def doctor_command(
    role: str,
    *,
    rcm: Path | None = None,
    config: Path | None = None,
    service_file: Path | None = None,
) -> str:
    """마지막에 칠 `rcm doctor` — setup 이 쓴 rcm 의 절대 경로로, 사용자가 준 `--config` ·
    `--service-file` 을 그대로 붙인다(P5 B-E7). 그래야 PATH 의 다른 빌드나 기본 자리의 다른
    설정을 보지 않는다."""
    q = shlex.quote
    words = [q(str(rcm)) if rcm is not None else "rcm", "doctor", "--role", role]
    if config is not None:
        words += ["--config", q(str(config))]
    if service_file is not None:
        words += ["--service-file", q(str(service_file))]
    return " ".join(words)


def db_version_note(have: int | None, want: int) -> str | None:
    """`--reuse-data-dir` 로 고른 폴더의 DB 헤더(`user_version`)와 이 빌드의 `DB_VERSION`
    (6-B-V3). 같으면 None. 헤더를 못 읽었으면 모른다고 적는다."""
    if have is None:
        return "cannot read the database version from rcm.sqlite3's header"
    if have < want:
        return (
            f"rcm.sqlite3 is v{have} and this build knows v{want}: the service migrates it to "
            f"v{want} when it starts, and a build older than this one cannot open it afterwards"
        )
    if have > want:
        return (
            f"rcm.sqlite3 is v{have}, newer than this build (v{want}): this build's server "
            "refuses to start on it"
        )
    return None


# ── 명령줄과 경로 해석 — setup 과 훅이 같이 쓴다 ─────────────────────────────


def add_arguments(p: argparse.ArgumentParser) -> None:
    """`rcm setup` 의 인자. setup 의 파서(`setup.build_parser` · `setup.register`)와 훅의 파서
    (`parse_args`)가 이것 하나로 만든다."""
    p.add_argument("--role", choices=ROLES, help="the role this machine takes")
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="ask nothing; flags the role needs must be given",
    )
    p.add_argument("--server", metavar="URL", help="the coordinator's URL (client, worker)")
    p.add_argument("--by", metavar="LABEL", help="requester label in client.toml (client)")
    p.add_argument("--config", metavar="PATH", help="where to write the config file")
    p.add_argument("--pool", metavar="NAME", help="the worker's pool (worker)")
    p.add_argument("--lanes", type=int, metavar="N", help="parallel job lanes, 1-64 (worker)")
    p.add_argument(
        "--label",
        metavar="LABEL",
        help="service label (default: com.remote-ci-monitor.server or .worker)",
    )
    p.add_argument(
        "--service-file",
        dest="service_file",
        metavar="PATH",
        help="where to write the service file (default: <config dir>/service/)",
    )
    p.add_argument("--port", type=int, metavar="N", help="server port (coordinator roles)")
    p.add_argument(
        "--data-dir", dest="data_dir", metavar="PATH", help="server data_dir (coordinator roles)"
    )
    p.add_argument(
        "--advertise",
        dest="advertise",
        action="store_true",
        default=None,
        help="advertise the server over mDNS",
    )
    p.add_argument("--no-advertise", dest="advertise", action="store_false")
    p.add_argument(
        "--bind",
        metavar="ADDRESS",
        help="address the server listens on — an address of this machine or 0.0.0.0 "
        "(coordinator roles; required with --yes)",
    )
    p.add_argument(
        "--force",
        action="append",
        nargs="?",
        const="all",
        choices=("config", "service", "all"),
        help="replace an existing config file, service file, or both (no value)",
    )
    p.add_argument(
        "--reuse-data-dir",
        dest="reuse_data_dir",
        action="store_true",
        help="use a data_dir that already holds rcm.sqlite3",
    )


#: 줄인 옵션(`--conf` · `--data`)을 받지 않는다 — 훅과 setup 이 같은 이름만 읽는다(P3 v1-1 · v2-2)
PARSER_OPTIONS: dict[str, Any] = {"allow_abbrev": False}


class SetupArgError(ValueError):
    """setup 의 파서가 거절할 명령줄 — setup 은 사용법 오류(종료 2)로 끝난다."""


class _Parser(argparse.ArgumentParser):
    """종료하지 않고 예외를 내는 파서 — 훅이 명령줄을 setup 과 같은 규칙으로 읽을 때 쓴다."""

    def error(self, message: str) -> Any:
        raise SetupArgError(message)

    def exit(self, status: int = 0, message: str | None = None) -> Any:
        raise SetupArgError(message or f"argparse exit {status}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """`rcm setup` 뒤의 인자를 setup 과 같은 정의로 읽는다. 거절할 명령줄이면 `SetupArgError`."""
    p = _Parser(prog="rcm setup", add_help=False, **PARSER_OPTIONS)
    add_arguments(p)
    return p.parse_args(list(argv))


def manager_of(platform: str) -> str | None:
    """그 플랫폼의 서비스 관리자 — launchd(macOS) · systemd(Linux) · 없음."""
    if platform.startswith("darwin"):
        return LAUNCHD
    if platform.startswith("linux"):
        return SYSTEMD
    return None


#: 서버 템플릿(`examples/rcm.toml`)의 `data_dir` — setup 은 템플릿에서 읽고, 훅은 이 값을 쓴다.
#: tests/test_setup.py 가 둘을 맞춘다
DEFAULT_DATA_DIR = "~/.local/share/rcm"
#: 역할마다 `--config` 가 없을 때 쓰는 설정 파일 이름
CONFIG_NAMES = {
    dc.ROLE_CLIENT: "client.toml",
    dc.ROLE_WORKER: "worker.toml",
    dc.ROLE_COORDINATOR: "server.toml",
    dc.ROLE_COORDINATOR_WORKER: "server.toml",
}
#: 빈 값을 받지 않는 플래그 — 빈 `--config` 는 기본 자리로 떨어졌다(P3 2라운드 v2-1)
NONEMPTY_FLAGS = ("config", "service_file", "data_dir", "label")


class SetupValueError(ValueError):
    """`where` 가 풀 수 없는 값 — 빈 경로 · 맞지 않는 라벨 · 모르는 역할. setup 은 종료 2 다."""


def blank_flags(values: Mapping[str, Any]) -> list[str]:
    """빈 값이나 공백뿐인 값을 받은 플래그(`--config` 처럼 플래그 이름으로)."""
    return [
        "--" + name.replace("_", "-")
        for name in NONEMPTY_FLAGS
        if isinstance(values.get(name), str) and not values[name].strip()
    ]


#: `~user` 를 그 사용자의 홈으로 푸는 함수 — passwd 를 읽는 I/O 라 호출자가 준다
#: (`os.path.expanduser`). 풀 수 없으면 받은 글자를 그대로 돌려준다
ExpandUser = Callable[[str], str]


def abs_path(raw: str, *, home: Path, cwd: Path, expand_user: ExpandUser) -> Path:
    """setup 이 받은 경로 글자 하나를 절대 경로로 — `os.path.abspath(os.path.expanduser(raw))`
    와 같은데 HOME · 작업 폴더 · `~user` 를 푸는 함수를 인자로 받는다. `~` · `~/…` 는 `home`,
    `~user` 는 `expand_user`, 상대 경로는 `cwd` 기준이다. 심링크는 풀지 않는다."""
    if raw == "~" or raw.startswith("~/"):
        root = str(home).rstrip("/")
        text = (root + raw[1:]) or "/"
    elif raw.startswith("~"):
        text = expand_user(raw)
    else:
        text = raw
    return Path(os.path.normpath(os.path.join(str(cwd), text)))


def home_of(environ: Mapping[str, str], fallback: Path) -> Path:
    """`Path.home()` 이 그 환경에서 낼 값 — `HOME` 이 있으면 그것(빈 값은 `/`), 없으면
    `fallback`(passwd 의 홈)."""
    if "HOME" not in environ:
        return fallback
    return Path(environ["HOME"].rstrip("/") or "/")


def config_dir(
    environ: Mapping[str, str], *, home: Path, cwd: Path, expand_user: ExpandUser
) -> Path:
    """설정 폴더 — `$XDG_CONFIG_HOME/rcm`, 없으면 `~/.config/rcm`(config.py `user_config_dir`)."""
    raw = environ.get("XDG_CONFIG_HOME") or "~/.config"
    return abs_path(raw, home=home, cwd=cwd, expand_user=expand_user) / "rcm"


@dataclass(frozen=True)
class Where:
    """setup 이 그 역할로 쓸 자리 — 설정 파일, 서비스 파일, 서버의 data_dir."""

    role: str
    config_dir: Path
    config: Path
    #: 서비스 라벨 — 서비스가 없는 역할(client)은 None
    label: str | None
    #: 쓸 서비스 파일 — client 나 서비스 관리자가 없는 플랫폼은 None
    service_file: Path | None
    #: 서비스 파일이 서비스 관리자가 읽는 자리 안이면 그 자리 — setup 은 거절한다
    install_place: Path | None
    #: 서버 역할(2 · 4)의 data_dir — 다른 역할은 None
    data_dir: Path | None


def where(
    role: str,
    *,
    environ: Mapping[str, str],
    cwd: Path,
    home: Path,
    manager: str | None,
    config: str | None = None,
    label: str | None = None,
    service_file: str | None = None,
    data_dir: str | None = None,
    default_data_dir: str = DEFAULT_DATA_DIR,
    fold_case: bool,
    expand_user: ExpandUser,
) -> Where:
    """setup 이 어디에 쓸지 — setup 의 경로 결정은 모두 이 함수를 거친다.

    값은 setup 이 받은 글자 그대로다(셸이 펼친 뒤). `--config` 가 없으면 설정 폴더의 역할 파일,
    `--service-file` 이 없으면 설정 파일 옆 `service/` 의 라벨 파일, `--data-dir` 이 없으면
    `default_data_dir`(템플릿의 기본값)을 `home` 으로 푼 것이다. 빈 값 · 맞지 않는 라벨 · 모르는
    역할은 `SetupValueError` 다 — 빈 `--config` 를 기본 자리로 읽지 않는다.

    기계에서 읽는 것은 없다: 대소문자 규칙(`fold_case`, `folds_case(platform)`)과 `~user` 를 푸는
    함수(`expand_user`)는 호출자가 준다(P3 3라운드 v1-5)."""
    if role not in CONFIG_NAMES:
        raise SetupValueError(f"--role must be one of {', '.join(ROLES)} (got {role!r})")
    given = {"config": config, "service_file": service_file, "data_dir": data_dir, "label": label}
    blank = blank_flags(given)
    if blank:
        raise SetupValueError(f"{' '.join(blank)} must not be empty")
    if label is not None and (problem := label_problem(label)):
        raise SetupValueError(problem)
    folder = config_dir(environ, home=home, cwd=cwd, expand_user=expand_user)
    conf = (
        abs_path(config, home=home, cwd=cwd, expand_user=expand_user)
        if config is not None
        else folder / CONFIG_NAMES[role]
    )
    kind = service_kind(role)
    chosen = (label if label is not None else default_label(role)) if kind else None
    service: Path | None = None
    place: Path | None = None
    if chosen is not None and manager is not None:
        if service_file is not None:
            service = abs_path(service_file, home=home, cwd=cwd, expand_user=expand_user)
        else:
            service = conf.parent / "service" / service_file_name(chosen, manager)
        place = install_place_of(service, home, fold_case=fold_case)
    data: Path | None = None
    if role in SERVER_ROLES:
        raw = data_dir if data_dir is not None else default_data_dir
        data = abs_path(raw, home=home, cwd=cwd, expand_user=expand_user)
    return Where(role, folder, conf, chosen, service, place, data)


# ── 운영 알아보기(결정 352) ──────────────────────────────────────────────────
#
# 훅(`tools/guard_production.py`)은 셸 글을 판정하므로 닫힌 목록(런처 · 메타 문자 · 자리)마다
# 바깥이 있다(P6 재확인 여덟 라운드). 그래서 setup 자신이 쓰기 직전에, 이 머신에서 찾을 수 있는
# rcm 서비스의 설정 · data_dir · venv · 체크아웃 · 라벨과 쓸 자리를 견준다. 사실을 모으는 것(스캔)은
# `remote_ci_monitor/setup.py` 의 일이고, 여기는 모은 사실과 대상만 보고 거절 이유를 낸다.


@dataclass(frozen=True)
class RcmService:
    """설치 자리에서 찾은 rcm 서비스 하나 — 경로는 모두 심볼릭 링크를 푼 것이다."""

    #: 거절 문장에 쓰는 이름 — plist 의 `Label`, systemd 는 유닛 이름(`.service` 를 뗀 것)
    name: str
    #: 설치된 서비스 파일
    file: Path
    #: 라벨 비교에 쓰는 이름들 — `Label` 과 파일 이름(확장자를 뗀 것)
    names: tuple[str, ...] = ()
    #: 서비스가 읽는 설정 파일
    config: Path | None = None
    #: 그 설정의 data_dir(서버는 `[server]`, 워커는 `[worker]`)
    data_dir: Path | None = None
    #: 서비스가 실행하는 venv
    venv: Path | None = None
    #: 그 venv 가 editable 로 설치한 소스 폴더
    checkout: Path | None = None
    #: 서비스 파일의 바이트 — 설치 파일이 setup 이 쓴 것과 같은지(cmp) 본다
    data: bytes | None = None
    #: rcm 처럼 보이는데 읽지 못했거나 설정 · data_dir 을 풀지 못한 이유 — 있으면 닫는 쪽이다
    problem: str | None = None


#: 운영 서비스에서 견주는 자리와 거절 문장의 이름
PRODUCTION_PIECES = (
    ("file", "service file"),
    ("config", "config"),
    ("data_dir", "data_dir"),
    ("venv", "venv"),
    ("checkout", "checkout"),
)


def same_or_inside(path: Path, root: Path, *, fold_case: bool) -> bool:
    """`path` 가 `root` 이거나 그 안인가 — 글자로 견준다(심볼릭 링크는 호출자가 푼다)."""
    text, top = place_key(path, fold_case=fold_case), place_key(root, fold_case=fold_case)
    return text == top or text.startswith(top.rstrip("/") + "/")


def production_refusal(
    services: Sequence[RcmService],
    targets: Mapping[str, Path],
    *,
    label: str | None,
    written: Sequence[bytes],
    force_service: bool,
    fold_case: bool,
) -> str | None:
    """setup 이 쓸 자리(`targets`: 플래그 이름 → 경로)가 이 머신의 rcm 서비스가 쓰는 것이면 그
    거절 문장, 아니면 None. 우회 플래그는 없다.

    1. 읽지 못했거나 설정 · data_dir 을 풀지 못한 rcm 서비스가 하나라도 있으면 거절한다(닫는
       쪽) — 그 서비스가 무엇을 쓰는지 모른다.
    2. 대상이 어느 서비스의 서비스 파일 · 설정 · data_dir · venv · 체크아웃이거나 그 안이면
       거절한다.
    3. setup 의 라벨(과 그 유닛 이름)이 설치된 서비스의 이름이면 `--force service` 이고 설치
       파일이 setup 이 쓴(쓸) 파일과 같을 때(`written`)만 받는다. 그 서비스는 setup 자신의
       것이라 2 에서도 견주지 않는다 — 설치한 뒤 `--force` 로 다시 쓰는 길이다(덮는 것은
       여전히 `--force` 의 몫이다)."""
    for svc in services:
        if svc.problem is not None:
            return (
                f"{svc.file} looks like an rcm service, but {svc.problem} — setup cannot tell "
                "what that service uses, so it is refusing (nothing was written)"
            )
    mine = {label, unit_name(label)} if label is not None else set()

    def own(svc: RcmService) -> bool:
        named = bool(mine.intersection(svc.names))
        return named and force_service and svc.data is not None and svc.data in written

    for flag, target in targets.items():
        for svc in services:
            if own(svc):
                continue
            for attr, what in PRODUCTION_PIECES:
                root = getattr(svc, attr)
                if root is not None and same_or_inside(target, root, fold_case=fold_case):
                    return (
                        f"{flag} {target}: this is {svc.name}'s {what} — refusing (nothing was "
                        "written)"
                    )
    for svc in services:
        if mine.intersection(svc.names) and not own(svc):
            return (
                f"--label {label}: this is {svc.name}'s label — refusing (nothing was written); "
                f"{svc.file} is installed and is not a file this setup wrote. Pass another "
                "--label; --force service rewrites it only when the installed file is the one "
                "setup wrote"
            )
    return None
