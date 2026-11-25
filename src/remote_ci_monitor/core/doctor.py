"""`rcm doctor` 의 순수 판정(M5t-a 결정 246 · 247, docs/m5t-workplan.md §4.5).

입출력이 없다. 잰 사실(프리셋별 빠진 도구 · 서비스 환경 · rcm 자신의 요구)을 받아 프리셋 상태,
종료 코드, 사람용 표와 `--json` 문서를 만든다. 재는 쪽은 `remote_ci_monitor.doctor` 다.

- 프리셋 상태는 넷이다: `ready` · `needs_check` · `missing` · `unknown`. `needs_check` 는 도구는
  다 있고 `checks` 가 남은 것이라 ok 라 부르지 않는다.
- 종료 코드는 0 · 1 · 2 · 3 이고 우선순위는 1 > 3 > 0 이다. 1 은 이 머신이 든 풀의 `pool`
  프리셋에 빠진 도구, rcm 자신의 요구 FAIL, 역할별 규칙(올 프리셋 0개)뿐이다. 3 은 판정하지
  못한 것(서버 불통 · 시한 초과 · 옛 서버 · 확인 못 받음 · 토큰 없음)뿐이다.
- 진단은 조언이다. 서버에는 읽기 요청(GET)만 보내고 자격 판정을 바꾸지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from remote_ci_monitor.core.progress import clean_line

DOCTOR_VERSION = 1

ROLE_CLIENT = "client"
ROLE_WORKER = "worker"
ROLE_COORDINATOR = "coordinator"
ROLE_COORDINATOR_WORKER = "coordinator-worker"
ROLES = (ROLE_CLIENT, ROLE_WORKER, ROLE_COORDINATOR, ROLE_COORDINATOR_WORKER)

EXIT_OK, EXIT_PROBLEM, EXIT_USAGE, EXIT_UNKNOWN = 0, 1, 2, 3

READY, NEEDS_CHECK, MISSING, UNKNOWN = "ready", "needs_check", "missing", "unknown"
STATES = (READY, NEEDS_CHECK, MISSING, UNKNOWN)

#: 도구 상태(프로브와 `requires`). 코어는 프리셋 도구의 버전을 비교하지 않으므로 `outdated` 는 없다
TOOL_OK, TOOL_MISSING, TOOL_BROKEN, TOOL_UNKNOWN = "ok", "missing", "broken", "unknown"

#: 이 머신에 그 프리셋의 잡이 오나
APPLIES_POOL = "pool"  # 이 머신이 든 풀의 `pool` 프리셋 — 빠지면 종료 1
APPLIES_CAPABLE = "any-capable"  # 판정이 eligible 이면 오는 프리셋
APPLIES_NOT_COMING = "not_coming"  # `any-capable` 인데 이 머신에는 오지 않는다(warn)
APPLIES_OTHER_POOL = "other_pool"  # 다른 풀 전용 — 대상이 아니다
APPLIES_LOCAL_LANE = "local_lane_only"  # coordinator 진단: 원격 머신은 후보가 아니다(TO6)
APPLIES_UNKNOWN = "unknown"  # 서버가 판정에 쓸 칸(배치 · 풀 · 점검)을 말하지 않아 오는지 모른다
COMING = (APPLIES_POOL, APPLIES_CAPABLE)

#: 설정 문자열(`any-capable`)과 내부 값(`capable`) 둘 다 받는다
CAPABLE_PLACEMENTS = frozenset({"any-capable", "capable"})
DEFAULT_POOL = "default"

LAUNCHD, SYSTEMD = "launchd", "systemd"
#: 서비스 관리자가 프로세스에 넣는 기본 변수. 깨끗한 환경 = 이것 + 서비스 파일의 환경 변수.
#: 셸의 `JAVA_HOME` 같은 값이 측정에 새지 않게 이 밖의 것은 넣지 않는다(명세 §4.5 「측정 환경」).
MANAGER_DEFAULTS: dict[str, tuple[str, ...]] = {
    LAUNCHD: ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "PATH"),
    SYSTEMD: ("HOME", "USER", "LOGNAME", "SHELL", "XDG_RUNTIME_DIR", "PATH"),
}
#: 서비스 파일이 PATH 를 주지 않을 때 관리자가 쓰는 PATH. launchd 는 `_PATH_STDPATH`(paths.h)이고
#: `launchctl print gui/<uid>/<service>` 의 `default environment` 가 같은 값을 보인다(2026-11-25,
#: macOS 27.2 에서 확인). systemd 는 배포판과 빌드마다 달라 추정하지 않는다 — 없으면 PATH 를
#: 모르는 것으로 둔다(위임 결정 5)
DEFAULT_PATHS: dict[str, str] = {
    LAUNCHD: "/usr/bin:/bin:/usr/sbin:/sbin",
}

#: 서버가 보낸 설치 문구 · 문서 주소의 길이 상한(`[[tools]]` 의 검증과 같은 300자)
TEXT_MAX = 300
_SUDO = re.compile(r"(^|&&\s*|;\s*|\|\s*)sudo\s+")

#: 사람용 문구(명세 §4.5 「문구」). CLI 문구라 영어다
STATE_TEXT = {
    READY: "ready",
    NEEDS_CHECK: "needs check",
    MISSING: "missing",
    UNKNOWN: "unknown",
}
READY_NOTE = "starts taking jobs once the service reports"
LOCAL_LANE_NOTE = "needs a local lane (local_only)"
CHECK_LOCAL_ONLY = (
    "check_local_only"  # 서버 판정 코드와 같은 이름(capabilities.CODE_CHECK_LOCAL_ONLY)
)
#: 서버가 그 점검이 이 머신에 닿는지 말하지 않았다(`unreachable_checks` 가 없는 명세) — doctor 만의
#: 코드다. 서버 판정 코드가 아니다
CHECK_UNKNOWN = "check_unknown"
NEEDS_CHECK_NOTE = "rcm runs the check once after it registers"
NO_JOBS = "no jobs will come to this machine"
#: 맨 바깥 경계(`doctor.run`)가 예상 못 한 예외에 다는 머리 — 사람용 이유가 아니다
BOUNDARY = "could not decide"


class Unsure(str):
    """재지 못한 이유 — 문제(「닿지 않음」)가 아니라 모름이다(`git ls-remote` 의 시한 초과 · 실행
    실패, P3 4라운드 MED). `str` 이라 문제 문장만 찍는 쪽(`rcm worker --check`)은 그대로 쓴다."""

    __slots__ = ()


def decide_exit(problems: Sequence[str], undecided: Sequence[str]) -> int:
    """우선순위 1 > 3 > 0. 빠진 것과 판정 못 함이 함께 있으면 1 이다(TD34)."""
    if problems:
        return EXIT_PROBLEM
    if undecided:
        return EXIT_UNKNOWN
    return EXIT_OK


def clean_env(
    manager: str,
    base: Mapping[str, str],
    service_env: Mapping[str, str],
    *,
    default_path: str | None = None,
) -> dict[str, str]:
    """`env -i` + 관리자의 기본 변수(값은 이 사용자의 것) + 서비스 파일의 환경 변수.

    PATH 는 셸의 것이 아니다. 서비스 파일이 주면 그것, 아니면 관리자의 기본 PATH 다. 기본값을
    모르는 관리자(systemd)면 PATH 키가 없다 — 지어내지 않는다.
    """
    keys = MANAGER_DEFAULTS[manager]
    env = {k: base[k] for k in keys if k != "PATH" and k in base}
    path = default_path if default_path is not None else DEFAULT_PATHS.get(manager)
    if path is not None:
        env["PATH"] = path
    env.update({str(k): str(v) for k, v in service_env.items()})
    return env


def preset_env_digest(
    requires: Sequence[str], env_passthrough: Sequence[str], env: Mapping[str, str]
) -> str:
    """서버가 `preset_env` 로 잰 프리셋 정의의 지문 — 그 측정이 기대는 것만 넣는다: `requires`,
    `env_passthrough`, 프리셋 env 의 이름들과 PATH 값. 서버(`/api/health` 의
    `preset_env.<이름>.digest`)와 doctor 가 이 함수 하나로 만든다. doctor 는 디스크의 설정과 서버가
    들고 있는 설정이 같은 정의인지 이것으로 본다(P6 GAP E4). PATH 밖의 env 값은 넣지 않는다 —
    토큰 없이 읽히는 답이다."""
    raw = json.dumps(
        {
            "requires": list(requires),
            "env_passthrough": list(env_passthrough),
            "env": sorted(env),
            "path": env.get("PATH"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def mask_home(text: str, home: str) -> str:
    """`:` 로 나눈 항목마다 홈 폴더를 `~` 로 가린다(PATH 든 경로 하나든)."""
    home = home.rstrip("/")
    if not home:
        return text
    out: list[str] = []
    for part in text.split(":"):
        if part == home:
            out.append("~")
        elif part.startswith(home + "/"):
            out.append("~" + part[len(home) :])
        else:
            out.append(part)
    return ":".join(out)


def hide_home(text: str, home: str) -> str:
    """문장 안 어디에 있든 홈 폴더 경로를 `~` 로 가린다(사용법 오류 · 예외 문장). 경로 하나나
    PATH 처럼 `:` 로 나뉜 값은 `mask_home` 이다."""
    home = home.rstrip("/")
    if not home:
        return text
    return re.sub(re.escape(home) + r"(?![\w.-])", "~", text)


def could_not_decide(error: BaseException, home: str) -> str:
    """맨 바깥 경계에 닿은 예외 → `could not decide: <예외 종류>: <정리된 한 줄>`. 첫 줄만,
    제어문자 없이, 홈 폴더는 `~` 로."""
    first = (str(error).splitlines() or [""])[0]
    text = hide_home(clean_text(first, 200), home)
    return f"{BOUNDARY}: {type(error).__name__}" + (f": {text}" if text else "")


def clean_text(value: Any, limit: int = TEXT_MAX) -> str:
    """서버가 보낸 문자열 — 제어문자를 지우고 자른다. 찍기 전에 한 번 더 정리한다(TD32).
    None(모름)은 빈 문자열이다 — `"None"` 이라는 글자를 지어내지 않는다(P3 3라운드).

    규칙은 `core/progress.clean_line` 하나다 — C0 · C1 제어문자에 더해 줄 · 문단 구분자, bidi
    제어, 폭 없는 글자도 지운다. 호스트 사실(`capabilities.clean_host_facts`)과 같다(P6 GAP
    X-bidi)."""
    if value is None:
        return ""
    return clean_line(str(value))[:limit]


def without_sudo(text: str) -> str:
    """root 로 도는 머신(컨테이너)에는 sudo 가 없다 — rcm 자신의 안내에서만 뺀다(TD7)."""
    return _SUDO.sub(r"\1", text)


def tool_hints(raw: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """`/worker/spec` 의 `tools`(`[[tools]]`) → 이름 → {install(OS 별), docs}. 문구는 정리한다.
    모양은 읽는 자리(`doctor.reach`)가 이미 봤다 — 항목마다 `name` 이 있다. 안내는 표시만 하므로
    `docs` · `install` 이 없으면 안내가 없을 뿐이다."""
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        install = item.get("install")
        docs = item.get("docs")
        texts = {} if install is None else {str(k): clean_text(v) for k, v in install.items()}
        out[clean_text(item["name"], 64)] = {
            "install": texts,
            "docs": "" if docs is None else clean_text(docs),
        }
    return out


def hint_for(tool: str, hints: Mapping[str, Any], os_name: str) -> tuple[str, str]:
    """(설치 문구, 문서 주소). OS 키가 없으면 `any`. 관리자의 문구라 sudo 를 빼지 않는다."""
    item = hints.get(tool) or {}
    install = item.get("install") or {}
    return str(install.get(os_name) or install.get("any") or ""), str(item.get("docs") or "")


@dataclass
class PresetRow:
    name: str
    applies: str
    state: str | None
    missing: list[dict[str, Any]] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    code: str | None = None
    warn: str | None = None
    tools: list[str] = field(default_factory=list)
    note: str | None = None
    shell_differs: bool | None = None

    @property
    def counts(self) -> bool:
        """이 머신에 오는 프리셋인가(요약과 「올 프리셋 0개」 규칙에 든다)."""
        return self.applies in COMING

    @property
    def problem(self) -> bool:
        """종료 1 인가 — 이 머신이 든 풀의 `pool` 프리셋에 빠진 도구가 있을 때만."""
        return self.applies == APPLIES_POOL and self.state == MISSING

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "applies": self.applies,
            "state": self.state,
            "missing": [dict(m) for m in self.missing],
            "checks": list(self.checks),
            "code": self.code,
            "tools": list(self.tools),
            "warn": self.warn,
            "note": self.note,
            "shell_differs": self.shell_differs,
        }


def _names(items: Any) -> list[str]:
    """도구 이름들 — 경로면 끝 이름만, 제어문자는 지운다(서버가 보낸 이름이라 그대로 찍힌다)."""
    return [clean_text(os.path.basename(str(x)), 64) for x in items or ()]


def classify_preset(
    entry: Mapping[str, Any],
    facts: Mapping[str, Any],
    *,
    pool: str,
    accept_any: bool,
    hints: Mapping[str, Any],
    os_name: str,
    unreachable: Mapping[str, str] | None,
) -> PresetRow:
    """`/worker/spec` 의 프리셋 하나와 이 머신에서 잰 사실 → 행(명세 §4.5 「프리셋 상태」).

    `pool` 프리셋은 이 머신의 풀이 그 프리셋의 `pool` 이나 `pools` 에 있을 때만 대상이다. 빠진
    도구는 `missing`(종료 1), 워크스페이스에만 있을 수 있는 도구는 `ready` + warn 이다.
    `any-capable` 프리셋은 도구가 빠졌거나 워크스페이스 안 도구면 서버 판정이 eligible 이 아니라
    이 머신에 오지 않는다. 그때는 「오지 않음」과 설치 안내를 warn 으로만 적는다. `checks` 가
    남으면 `needs_check` 다. 서버가 `unreachable` 에 적은 점검(이름 → 이유 코드, 점검 프리셋이
    `local_only`)이 `checks` 에 있으면 도구와 상관없이 「오지 않음(점검이 로컬 전용)」이다 — 점검이
    영영 안 오므로 「점검 필요」라 부르지 않는다(TO18). `unreachable` 이 None(서버가 말하지 않음)
    인데 `checks` 가 있으면 그 점검이 이 머신에 닿는지 모르므로 「모름」(`check_unknown`)이다.
    `{}` 는 「모두 닿는다」이다 — 서버의 로컬 레인처럼 칸이 필요 없는 자리는 `{}` 를 준다.
    """
    # 이름은 서버가 보낸 것이다 — 사람용 표에 그대로 찍히므로 제어문자를 지운다(검증자 1 #7)
    name = clean_text(entry["name"], 64)
    tools = _names(entry["requires"])
    raw_checks = entry["checks"]
    checks = [] if raw_checks is None else [clean_text(c, 64) for c in raw_checks]
    missing = _names(facts["missing_tools"])
    workspace = _names(facts["workspace_tools"])
    missing_rows = []
    for tool in missing:
        install, docs = hint_for(tool, hints, os_name)
        missing_rows.append({"tool": tool, "install": install, "docs": docs})
    placement = entry["placement"]

    def row(applies: str, state: str | None, code: str | None, **kw: Any) -> PresetRow:
        return PresetRow(name, applies, state, missing_rows, checks, code, tools=tools, **kw)

    def unknown(code: str, note: str) -> PresetRow:
        # 판정에 쓸 칸을 서버가 말하지 않았다 — 오는지도 모르므로 「오지 않음」도 「빠짐」도 아니다
        return row(APPLIES_UNKNOWN, UNKNOWN, code, note=note)

    if placement is None:
        return unknown(
            "placement_unknown",
            "the server does not say which pool this preset runs in (older rcm) — presets for "
            "other pools cannot be counted",
        )
    if placement != "pool" and placement not in CAPABLE_PLACEMENTS:
        return unknown(
            "placement_unknown",
            f"the server names a placement rcm does not know ({clean_text(placement, 64)})",
        )
    capable = placement in CAPABLE_PLACEMENTS

    far = [c for c in checks if unreachable is not None and c in unreachable]
    blind = bool(checks) and unreachable is None

    def check_far() -> PresetRow:
        codes = {unreachable[c] for c in far} if unreachable else set()
        if codes <= {CHECK_LOCAL_ONLY}:
            return row(
                APPLIES_NOT_COMING,
                None,
                CHECK_LOCAL_ONLY,
                note=f"not coming (check is local-only: {', '.join(far)})",
            )
        return row(
            APPLIES_NOT_COMING,
            None,
            sorted(codes)[0],
            note=f"not coming (check cannot run on this machine: {', '.join(far)})",
        )

    def check_blind(applies: str) -> PresetRow:
        return row(
            applies,
            UNKNOWN,
            CHECK_UNKNOWN,
            note=f"the server does not say whether {', '.join(checks)} can run here",
        )

    if not capable:
        home, also = entry["pool"], entry["pools"]
        if home != pool and (also is None or pool not in also):
            if home is None or also is None:
                return unknown(
                    "pool_unknown", "the server does not say which pools this preset runs in"
                )
            pools = {home, *also}
            return row(
                APPLIES_OTHER_POOL, None, None, note=f"runs in pool {', '.join(sorted(pools))}"
            )
        if raw_checks is None:
            return unknown("checks_unknown", "the server does not say which checks it needs")
        if far:
            return check_far()
        if blind:
            return check_blind(APPLIES_POOL)
        if missing:
            return row(APPLIES_POOL, MISSING, "tool_missing")
        if workspace:
            warn = (
                f"{', '.join(workspace)} may only be in the job's workspace (relative PATH entry)"
            )
            return row(APPLIES_POOL, READY, "workspace_tool", warn=warn)
        if checks:
            return row(APPLIES_POOL, NEEDS_CHECK, "check_needed")
        return row(APPLIES_POOL, READY, "ok")
    if not accept_any:
        return row(
            APPLIES_NOT_COMING,
            None,
            None,
            note="this machine does not take any-capable jobs (accept_any = false)",
        )
    if raw_checks is None:
        return unknown("checks_unknown", "the server does not say which checks it needs")
    if far:
        return check_far()
    if blind:
        return check_blind(APPLIES_CAPABLE)
    if missing:
        hint = "; ".join(f"{m['tool']}: {m['install']}" for m in missing_rows if m["install"])
        warn = f"not coming to this machine: {', '.join(missing)} missing"
        return row(
            APPLIES_NOT_COMING,
            MISSING,
            "tool_missing",
            warn=warn + (f" — install {hint}" if hint else ""),
        )
    if workspace:
        warn = (
            f"not coming to this machine: {', '.join(workspace)} may only be in a job's "
            "workspace, so the server cannot judge it"
        )
        return row(APPLIES_NOT_COMING, UNKNOWN, "workspace_tool", warn=warn)
    if checks:
        return row(APPLIES_CAPABLE, NEEDS_CHECK, "check_needed")
    return row(APPLIES_CAPABLE, READY, "ok")


def summary(rows: Sequence[PresetRow]) -> dict[str, int]:
    """이 머신에 오는 프리셋만 상태별로 센다 — 다른 풀 전용과 「오지 않음」은 빠진다. 오는지
    모르는 프리셋(`applies: unknown`)은 `applies_unknown` 에 따로 센다 — 빼면 「0 unknown」인데
    종료 3 이 된다(P3 4라운드 LOW)."""
    out = dict.fromkeys(STATES, 0)
    for r in rows:
        if r.counts and r.state in out:
            out[r.state] += 1
    out["applies_unknown"] = sum(1 for r in rows if r.applies == APPLIES_UNKNOWN)
    return out


def preset_problems(rows: Sequence[PresetRow]) -> list[str]:
    out = []
    for r in rows:
        if r.problem:
            tools = ", ".join(m["tool"] for m in r.missing)
            out.append(f"{r.name}: {tools} missing")
    return out


def unknown_rows(rows: Sequence[PresetRow]) -> list[str]:
    """이 머신에 올 수도 있는 프리셋 가운데 모름인 행 → 「판정 못 함」 줄. 종료 3 은 행의 상태에서
    나온다 — 모름을 만든 자리가 따로 줄을 적어야 하는 것이 아니다."""
    return [
        f"{r.name}: {r.note or 'unknown'}"
        for r in rows
        if r.state == UNKNOWN and r.applies in (*COMING, APPLIES_UNKNOWN)
    ]


def no_jobs_reason(rows: Sequence[PresetRow], pool: str) -> str | None:
    """올 프리셋이 0개면 그 이유 한 줄(TD26 · TD53). 하나라도 오거나, 오는지 모르는 프리셋이
    있으면 None — 모르는 것을 「아무것도 오지 않는다」로 단정하지 않는다."""
    if any(r.counts or r.applies == APPLIES_UNKNOWN for r in rows):
        return None
    if not rows:
        return f"{NO_JOBS}: the server has no presets"
    parts: list[str] = []
    if not any(r.applies == APPLIES_POOL for r in rows):
        parts.append(f"no preset runs in pool {pool}")
    capable = sum(1 for r in rows if r.applies == APPLIES_NOT_COMING)
    if capable:
        parts.append(f"{capable} any-capable preset(s) cannot run here")
    return f"{NO_JOBS}: " + "; ".join(parts or ["no preset can run here"])


# ── 사람용 표 ────────────────────────────────────────────────────────────────


def _cells(rows: Sequence[Sequence[str]]) -> list[str]:
    if not rows:
        return []
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    out = []
    for r in rows:
        head = "  ".join(c.ljust(w) for c, w in zip(r[:-1], widths, strict=False))
        out.append(f"  {head}  {r[-1]}".rstrip())
    return out


#: 상태 대신 메모를 보이는 행
NOTE_ONLY = (APPLIES_OTHER_POOL, APPLIES_NOT_COMING, APPLIES_LOCAL_LANE, APPLIES_UNKNOWN)


def _preset_detail(p: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for m in p.get("missing") or []:
        bit = f"{m['tool']} {m.get('note') or 'missing'}"
        if m.get("install"):
            bit += f" — install: {m['install']}"
        if m.get("docs"):
            bit += f" · docs: {m['docs']}"
        parts.append(bit)
    if p["applies"] in NOTE_ONLY:
        parts.append(p.get("note") or "")
    elif p["state"] == READY:
        parts.append(READY_NOTE)
    elif p["state"] == NEEDS_CHECK:
        parts.append(f"{NEEDS_CHECK_NOTE} (check: {', '.join(p.get('checks') or [])})")
    if p.get("warn") and p["applies"] != APPLIES_NOT_COMING:
        parts.append(f"warn: {p['warn']}")
    if p.get("shell_differs"):
        parts.append("differs in your shell")
    return " · ".join(x for x in parts if x)


def _preset_state(p: Mapping[str, Any]) -> str:
    if p["applies"] == APPLIES_OTHER_POOL:
        return "other pool"
    if p["applies"] == APPLIES_NOT_COMING:
        return "not coming"
    if p["applies"] == APPLIES_LOCAL_LANE:
        return "local lane"
    return STATE_TEXT.get(p["state"], "—") if p["state"] is not None else "—"


def _shown(value: Any) -> str:
    """사람용 칸 — 모름(None)은 `—` 이다."""
    return "—" if value is None else str(value)


REQ_MARK = {"ok": "ok", "fail": "FAIL", "warn": "warn", "info": "info", "unknown": "?"}


def render(doc: Mapping[str, Any]) -> list[str]:
    """`--json` 문서와 같은 내용을 사람용 줄로. 판정은 문서에서만 읽는다(둘이 갈리지 않게)."""
    lines: list[str] = []
    where = {
        "clean_service_env": "measured in a clean service environment",
        "server_service_env": "local lane as the server's service measured it",
    }.get(str(doc.get("measured_in")), "")
    lines.append(" · ".join(x for x in ("rcm doctor", str(doc.get("role")), where) if x))
    host = doc.get("host") or {}
    host_bits = [
        " ".join(str(x) for x in (host.get("os"), host.get("os_release")) if x),
        str(host.get("arch") or "") + (" (Rosetta)" if host.get("rosetta") else ""),
        "container" if host.get("container") else "",
        "root" if host.get("root") else "",
        f"Python {host['python']}" if host.get("python") else "",
        f"rcm {host['rcm']}" if host.get("rcm") else "",
        str(host.get("package_manager") or ""),
    ]
    lines.append("host     " + " · ".join(b for b in host_bits if b))
    server = doc.get("server")
    if server:
        bits = [str(server.get("url") or "—"), str(server.get("state") or "—")]
        if server.get("version"):
            bits.append(f"v{server['version']}")
        if server.get("found_by") == "mdns":
            bits.append("found on this network")
        lines.append("server   " + " · ".join(bits))
    service = doc.get("service")
    if service:
        if service.get("file"):
            env = ", ".join(service.get("env_names") or []) or "none"
            lines.append(f"service  {service['file']} ({service.get('manager')}) · env {env}")
        elif service.get("path"):
            lines.append("service  no service file found — measured with the default worker PATH")
        else:
            lines.append("service  no service file rcm could read — PATH unknown")
        lines.append(f"PATH     {service.get('path') or 'unknown'}")
    shell = doc.get("shell")
    if shell:
        lines.append(f"shell    {shell.get('path')}")
    reqs = doc.get("rcm_requirements") or []
    if reqs:
        lines.append("rcm's own needs")
        rows = []
        for r in reqs:
            detail = str(r.get("detail") or "")
            if r.get("install"):
                detail += f" — install: {r['install']}"
            rows.append([REQ_MARK.get(str(r.get("state")), "?"), str(r["name"]), detail])
        lines.extend(_cells(rows))
    presets = doc.get("presets") or []
    if presets:
        lines.append("presets")
        lines.extend(_cells([[_preset_state(p), p["name"], _preset_detail(p)] for p in presets]))
    probes = doc.get("probes") or {}
    if probes:
        lines.append("probes")
        lines.extend(
            _cells([[str(p.get("state")), n, str(p.get("value") or "")] for n, p in probes.items()])
        )
    machines = doc.get("remote_machines")
    if machines:
        lines.append("remote workers")
        lines.extend(
            _cells(
                [
                    [
                        {True: "alive", False: "down"}.get(m["alive"], "unknown"),
                        str(m["name"]),
                        f"pool {_shown(m['pool'])} · {_shown(m['eligible'])}/"
                        f"{_shown(m['presets'])} eligible",
                    ]
                    for m in machines
                ]
            )
        )
    s = doc.get("summary")
    if s and presets:
        maybe = s.get("applies_unknown") or 0
        lines.append(
            f"summary  {s['ready']} ready · {s['needs_check']} needs check · "
            f"{s['missing']} missing · {s['unknown']} unknown"
            + (f" · {maybe} may come (unknown)" if maybe else "")
        )
    for w in doc.get("warnings") or []:
        lines.append(f"warn  {w}")
    for p in doc.get("problems") or []:
        lines.append(f"FAIL  {p}")
    for u in doc.get("undecided") or []:
        lines.append(f"?     {u}")
    lines.append(f"exit {doc.get('exit')}")
    return lines
