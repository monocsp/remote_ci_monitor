"""`rcm setup` 의 순수한 판단(M5t-a 결정 239~242, docs/m5t-workplan.md §4.1).

역할별 결정 · 검증 · 서비스 파일 내용 · 출력할 명령을 만든다. 시계도 I/O 도 없다 — 묻고 쓰고
조회하는 쪽은 `remote_ci_monitor/setup.py` 다. 질문과 안내는 CLI 문구라 영어다(AGENTS.md).
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from remote_ci_monitor.core import doctor as dc
from remote_ci_monitor.core.mdns import ipv4_rank

LAUNCHD, SYSTEMD = dc.LAUNCHD, dc.SYSTEMD
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
#: 라벨 — launchd 의 `Label` 이자 파일 이름이다. 경로 구분자나 공백이 들어가면 안 된다
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: `--yes`(또는 물을 사람이 없을 때) 역할마다 꼭 줘야 하는 인자(§4.1 표)
YES_REQUIRED: dict[str, tuple[str, ...]] = {
    dc.ROLE_CLIENT: ("server",),
    dc.ROLE_WORKER: ("server", "pool", "lanes"),
    dc.ROLE_COORDINATOR: ("bind",),
    dc.ROLE_COORDINATOR_WORKER: ("bind",),
}

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
    if _LABEL_RE.match(label):
        return None
    return f"--label must be letters, digits, '.', '_' or '-' (got {label!r})"


def missing_for_yes(role: str, given: Mapping[str, Any]) -> list[str]:
    """묻지 않고 돌 때 빠진 필수 인자(`--bind` 처럼 플래그 이름으로)."""
    return [f"--{name}" for name in YES_REQUIRED.get(role, ()) if given.get(name) is None]


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


def server_unit(*, rcm: Path, config: Path, home: Path, label: str, log: Path, path: str) -> bytes:
    """systemd 사용자 유닛(Linux) — `systemctl --user` 로 올린다. 토큰은 싣지 않는다."""
    text = f"""# remote_ci_monitor server ({label}) — written by `rcm setup`.
[Unit]
Description=remote_ci_monitor job server ({label})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={home}
ExecStart={rcm} serve --config {config}
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
KillMode=mixed
TimeoutStopSec=30
LimitNOFILE={NOFILE}
Environment=PYTHONUNBUFFERED=1
Environment=LANG=en_US.UTF-8
Environment=HOME={home}
Environment=PATH={path}
StandardOutput=append:{log}
StandardError=append:{log}

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


def install_place_of(path: Path, home: Path) -> Path | None:
    """`path` 가 설치 자리 안이면 그 자리. 바로 아래만이 아니라 그 안 전부다 — systemd 는
    `<유닛>.d/` 의 drop-in 도 읽는다. 경로는 글자로만 비교한다(심링크는 풀지 않는다)."""
    text = os.path.normpath(str(path))
    for place in install_places(home):
        root = os.path.normpath(str(place))
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
    folders = f"mkdir -p {q(str(log.parent))} {q(str(installed.parent))}"
    if manager == LAUNCHD:
        return [folders, copy, same + f"launchctl bootstrap gui/$(id -u) {q(str(installed))}"]
    unit = installed.name
    return [
        folders,
        copy,
        same + f"systemctl --user daemon-reload && systemctl --user enable --now {q(unit)}",
    ]


def token_commands(rcm: Path, config: Path) -> list[str]:
    """coordinator 계열의 토큰 명령 — 서비스와 같은 빌드로 실행한다(다른 빌드는 DB 를 열면서
    마이그레이션한다). client 권한이고 `--admin` 은 붙이지 않는다. `--config` 는 하위 명령
    앞이다."""
    head = f"{shlex.quote(str(rcm))} token --config {shlex.quote(str(config))} add"
    return [f"{head} CLIENT_NAME", f"{head} WORKER_NAME --worker"]


def doctor_command(role: str) -> str:
    return f"rcm doctor --role {role}"


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
