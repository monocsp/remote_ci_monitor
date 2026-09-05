"""호스트 자원 파서 — macOS(vm_stat · top · ps · ioreg · sysctl) · Linux(/proc/* · ps · nvidia-smi).

순수 함수만 있다. 명령을 부르지도, 파일을 읽지도 않는다(그건 `hostsample.py`). 값이 없으면 `None`
(0 이 아니다). 입력이 이상해도 예외 대신 `None` 을 돌려준다 — 화면은 「모름」을 「0」과 다르게
그린다.
규칙은 `docs/m1-workplan.md` 1절.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

PAGE_SIZE_DEFAULT = 4096
STALE_MULTIPLIER = 3.0

_VM_PAGE_SIZE_RE = re.compile(r"page size of (\d+) bytes")
_VM_LINE_RE = re.compile(r"^\s*Pages ([A-Za-z ]+?):\s+(\d+)\.?\s*$")
_VM_KEY_MAP = {
    "free": "free",
    "active": "active",
    "inactive": "inactive",
    "speculative": "speculative",
    "wired down": "wired",
    "occupied by compressor": "compressor",
    "throttled": "throttled",
    "purgeable": "purgeable",
    "stored in compressor": "stored_in_compressor",
}
_TOP_CPU_RE = re.compile(
    r"CPU usage:\s*([\d.]+)%\s*user,\s*([\d.]+)%\s*sys,\s*([\d.]+)%\s*idle", re.IGNORECASE
)
_TOP_LOAD_RE = re.compile(r"Load Avg:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)")
_PS_LINE_RE = re.compile(r"^\s*([\d.]+)\s+(\d+)\s+(.+?)\s*$")
_IOREG_PERF_RE = re.compile(r'"PerformanceStatistics"\s*=\s*\{([^}]*)\}')
_IOREG_KV_RE = re.compile(r'"([^"]+)"\s*=\s*(-?\d+)')
_LOADAVG_RE = re.compile(r"^\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)")
_MEMINFO_RE = re.compile(r"^(\w+):\s+(\d+)\s*kB", re.MULTILINE)
_NVIDIA_ERROR_HINTS = ("failed", "error", "not found", "couldn't", "unable")


def _float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _round(v: float | None, digits: int = 2) -> float | None:
    return None if v is None else round(v, digits)


# ── macOS ────────────────────────────────────────────────────────────────────


def parse_vm_stat(text: str) -> dict[str, int] | None:
    """`vm_stat` → 페이지 수 dict(+ `page_size`). 「Pages …:」 줄이 하나도 없으면 None."""
    if not text:
        return None
    out: dict[str, int] = {}
    m = _VM_PAGE_SIZE_RE.search(text)
    out["page_size"] = int(m.group(1)) if m else PAGE_SIZE_DEFAULT
    for line in text.splitlines():
        lm = _VM_LINE_RE.match(line)
        if not lm:
            continue
        raw_key = lm.group(1).strip().lower()
        key = _VM_KEY_MAP.get(raw_key, raw_key.replace(" ", "_"))
        out[key] = int(lm.group(2))
    if len(out) == 1:  # page_size 뿐
        return None
    return out


def mac_memory(vm: dict[str, int] | None, total_bytes: int | None) -> dict[str, int | None]:
    """used = (active + wired + compressor) × page_size. compressed = compressor × page_size."""
    used: int | None = None
    compressed: int | None = None
    if vm:
        page = vm.get("page_size") or PAGE_SIZE_DEFAULT
        parts = [vm.get("active"), vm.get("wired"), vm.get("compressor")]
        if all(p is not None for p in parts):
            used = sum(parts) * page  # type: ignore[arg-type]
        if vm.get("compressor") is not None:
            compressed = vm["compressor"] * page
    return {"total_bytes": total_bytes, "used_bytes": used, "compressed_bytes": compressed}


def parse_top_cpu(text: str) -> dict[str, float | None] | None:
    """`top -l 2 -n 0 -s 1` 의 **마지막** CPU usage 줄. 첫 표본은 부팅 이후 누적이라 버린다."""
    if not text:
        return None
    matches = _TOP_CPU_RE.findall(text)
    if not matches:
        return None
    user_s, sys_s, idle_s = matches[-1]  # mutcheck ⑤ 표적: 마지막 표본
    user, sys_, idle = _float(user_s), _float(sys_s), _float(idle_s)
    busy = None if idle is None else round(100.0 - idle, 2)
    return {"user": _round(user), "sys": _round(sys_), "idle": _round(idle), "busy": busy}


def parse_top_load(text: str) -> tuple[float, float, float] | None:
    if not text:
        return None
    matches = _TOP_LOAD_RE.findall(text)
    if not matches:
        return None
    a, b, c = (_float(x) for x in matches[-1])
    if a is None or b is None or c is None:
        return None
    return (a, b, c)


def parse_ps(text: str, limit: int = 5) -> list[dict[str, Any]]:
    """`%cpu rss comm` 줄 → cpu 내림차순 limit 개. comm 은 basename, rss 는 KiB → MB 정수."""
    if not text or limit <= 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _PS_LINE_RE.match(line)
        if not m:
            continue
        cpu = _float(m.group(1))
        if cpu is None:
            continue
        rss_kib = int(m.group(2))
        comm = m.group(3).strip()
        if not comm:
            continue
        rows.append(
            {
                "comm": comm.rstrip("/").rsplit("/", 1)[-1] or comm,
                "cpu": round(cpu, 1),
                "rss_mb": int(round(rss_kib / 1024)),
            }
        )
    rows.sort(key=lambda r: r["cpu"], reverse=True)
    return rows[:limit]


def parse_ioreg_gpu(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """`ioreg -r -d 1 -w 0 -c IOAccelerator` 의 PerformanceStatistics → (gpu dict, note).

    Apple Silicon 은 통합 메모리라 `mem_total_bytes` 는 None. 가속기가 여럿이면 util 은 max, 메모리는 합.
    """
    if not text:
        return None, "no IOAccelerator PerformanceStatistics"
    blocks = _IOREG_PERF_RE.findall(text)
    if not blocks:
        return None, "no IOAccelerator PerformanceStatistics"
    utils: list[int] = []
    mems: list[int] = []
    for block in blocks:  # 가속기가 여럿이면 util 은 max, 메모리는 합
        stats = {key: int(value) for key, value in _IOREG_KV_RE.findall(block)}
        if "Device Utilization %" in stats:
            utils.append(stats["Device Utilization %"])
        if "In use system memory" in stats:
            mems.append(stats["In use system memory"])
    if not utils and not mems:
        return None, "PerformanceStatistics without utilization or memory keys"
    util = max(utils) if utils else None
    mem = sum(mems) if mems else None
    return (
        {
            "util_pct": util,
            "mem_used_bytes": mem,
            "mem_total_bytes": None,
            "source": "ioreg",
        },
        None,
    )


def parse_sysctl_int(text: str) -> int | None:
    if not text:
        return None
    try:
        return int(text.strip().split()[0])
    except (ValueError, IndexError):
        return None


# ── Linux ────────────────────────────────────────────────────────────────────


def parse_proc_loadavg(text: str) -> tuple[float, float, float] | None:
    if not text:
        return None
    m = _LOADAVG_RE.match(text)
    if not m:
        return None
    a, b, c = (_float(x) for x in m.groups())
    if a is None or b is None or c is None:
        return None
    return (a, b, c)


def parse_proc_meminfo(text: str) -> dict[str, int | None]:
    """MemTotal · MemAvailable(kB) → bytes. MemAvailable 이 없으면(옛 커널) used 는 None."""
    values = {k: int(v) for k, v in _MEMINFO_RE.findall(text or "")}
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    return {
        "total_bytes": total * 1024 if total is not None else None,
        "used_bytes": (total - available) * 1024
        if total is not None and available is not None
        else None,
        "compressed_bytes": None,
    }


def _cpu_fields(text: str) -> list[int] | None:
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            try:
                return [int(x) for x in line.split()[1:]]
            except ValueError:
                return None
    return None


def parse_proc_stat_cpu(first: str, second: str) -> dict[str, float | None] | None:
    """두 /proc/stat 표본의 `cpu` 줄 차분. total 이 0 이면 None."""
    a = _cpu_fields(first)
    b = _cpu_fields(second)
    if a is None or b is None or len(a) < 4 or len(b) < 4:
        return None
    n = min(len(a), len(b))
    delta = [b[i] - a[i] for i in range(n)]
    if any(d < 0 for d in delta):  # 카운터가 줄었다(재부팅·오버플로) — 모른다
        return None
    total = sum(delta[:8])  # guest/guest_nice 는 user/nice 에 이미 포함돼 있어 뺀다
    if total <= 0:
        return None

    def at(i: int) -> int:
        return delta[i] if i < n else 0

    def pct(v: float) -> float:
        return round(min(100.0, max(0.0, v / total * 100)), 2)

    user = pct(at(0) + at(1))
    sys_ = pct(at(2) + at(5) + at(6))
    idle = pct(at(3) + at(4))
    return {"user": user, "sys": sys_, "idle": idle, "busy": round(100.0 - idle, 2)}


def parse_nvidia_smi(text: str) -> dict[str, Any] | None:
    """`--query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits`."""
    if not text:
        return None
    first = text.strip().splitlines()[0] if text.strip() else ""
    if not first:
        return None
    lowered = first.lower()
    if any(hint in lowered for hint in _NVIDIA_ERROR_HINTS):
        return None
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 3:
        return None
    try:
        util = int(float(parts[0]))
        used = int(float(parts[1]))
        total = int(float(parts[2]))
    except ValueError:
        return None
    return {
        "util_pct": util,
        "mem_used_bytes": used * 2**20,
        "mem_total_bytes": total * 2**20,
        "source": "nvidia-smi",
    }


# ── 공통 ─────────────────────────────────────────────────────────────────────


def stale(sampled_at: datetime, now: datetime, interval_seconds: float) -> bool:
    """표본 나이가 3×주기를 넘으면 낡았다. 정확히 3×주기는 아직 아니다."""
    age = (now - sampled_at).total_seconds()
    return age > STALE_MULTIPLIER * interval_seconds  # mutcheck ④ 표적
