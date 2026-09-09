"""받은 산출물을 세션의 작업 트리에 쓴다(M5e) — 분류 · 안전 검사 · 적용 · 저널.

**`materialize.extract_tree` 를 쓰면 안 된다.** 그 함수는 목적지를 먼저 `rmtree` 한다 — 사용자의
작업 트리가 사라진다. 여기서 쓰는 것은 `core/artifacts.py` 의 순수 규칙(분류·경로·충돌)뿐이다.

절차(명세 §11): 스테이징으로 통째로 받고 → 쓰기 전에 목적지를 전수 검사하고 → 파일마다 쓰기
직전에 다시 확인하고 → 같은 디렉터리에 임시 파일을 만들어 `os.replace` 한다. 64개가 통째로
원자적이지는 않으므로 저널에 「몇 개까지 갔는지」를 정직하게 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remote_ci_monitor.core.artifacts import PolicyError, check_path, classify, collisions

CHUNK = 64 * 1024
#: 계획 단계에서 걸러진 항목. 하나라도 있으면 **아무것도 쓰지 않는다**.
UNSAFE = "unsafe"
VERDICTS = ("new", "unchanged", "changed", "conflicted", UNSAFE)
#: 저널이 「적용됨」으로 적는 낱말.
WRITTEN = "written"


@dataclass(frozen=True)
class Entry:
    """받은 파일 하나의 판정과 그 근거. 근거가 있어야 쓰기 직전 재확인이 된다(§11 절차 3)."""

    path: str
    verdict: str
    incoming_sha: str
    baseline_sha: str | None = None
    local_sha: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ApplyPlan:
    entries: tuple[Entry, ...]

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(VERDICTS, 0)
        for e in self.entries:
            out[e.verdict] = out.get(e.verdict, 0) + 1
        return out

    def safe(self) -> bool:
        """안전하지 않은 항목이 하나라도 있으면 묶음째 거부한다 — 골라 쓰지 않는다(§15.1)."""
        return not any(e.verdict == UNSAFE for e in self.entries)


@dataclass(frozen=True)
class ApplyResult:
    wrote: int = 0
    skipped: int = 0
    conflicted: int = 0
    failed: int = 0
    complete: bool = False


def _sha_of(path: Path) -> str | None:
    """디스크에 있는 파일의 sha256. 없거나 일반 파일이 아니면 None."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _unsafe_reason(root: Path, rel: str) -> str | None:
    """목적지가 안전하지 않은 이유. 안전하면 None.

    조각 어디에도 심링크가 없어야 한다 — `out/` 이 링크면 그 아래는 남의 트리다. 자재화의 `data`
    필터를 믿지 않는다(§2): 그것은 절대 경로를 **거부하지 않고 상대화**한다.
    """
    try:
        check_path(rel)
    except PolicyError as e:
        return str(e)
    here = root
    parts = rel.split("/")
    for part in parts[:-1]:
        here = here / part
        if here.is_symlink():
            return "a parent component is a symlink"
    dest = here / parts[-1]
    if dest.is_symlink():
        return "the destination is a symlink"
    try:
        dest.relative_to(root)
    except ValueError:
        return "the destination is outside the tree"
    return None


def plan(files: Sequence[Any], baseline: Mapping[str, str], root: Path) -> ApplyPlan:
    """받은 파일들을 §11 의 표로 가른다. 경로순으로 정렬한다(「41개까지 갔다」가 말이 되게)."""
    root = Path(root)
    clashes = {p for pair in collisions([f.path for f in files]) for p in pair}
    entries: list[Entry] = []
    for f in sorted(files, key=lambda x: x.path):
        reason = _unsafe_reason(root, f.path)
        if f.path in clashes and reason is None:
            reason = "collides with another path after normalisation"
        if reason is not None:
            entries.append(Entry(path=f.path, verdict=UNSAFE, incoming_sha=f.sha256, reason=reason))
            continue
        base = baseline.get(f.path)
        local = _sha_of(root / f.path)
        entries.append(
            Entry(
                path=f.path,
                verdict=classify(base, local, f.sha256),
                incoming_sha=f.sha256,
                baseline_sha=base,
                local_sha=local,
            )
        )
    return ApplyPlan(entries=tuple(entries))


def read_journal(path: Path) -> dict[str, Any] | None:
    """없거나 깨진 저널은 None — 반쯤 읽은 저널로 이어 쓰지 않는다."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write_journal(path: Path, doc: dict[str, Any]) -> None:
    """사람이 읽을 수 있는 JSON 으로. 같은 디렉터리 임시 파일 → `os.replace`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _install(staging: Path, root: Path, rel: str) -> None:
    """스테이징의 파일을 목적지로. 같은 디렉터리 임시 파일 → `os.replace`(부분 파일이 없게)."""
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix="." + dest.name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, (staging / rel).open("rb") as src:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply(
    plan: ApplyPlan,
    staging: Path,
    root: Path,
    *,
    force: bool = False,
    journal: Path | None = None,
) -> ApplyResult:
    """계획을 트리에 적용한다(§11 절차 3~4).

    기본은 `new`·`changed` 만 쓴다. `force` 는 `conflicted` 까지 덮되 **계획 뒤에 바뀐 파일은
    그때도 건너뛴다** — 사람에게 보여 준 표에 없던 변경이다.
    """
    root, staging = Path(root), Path(staging)
    if not plan.safe():
        return ApplyResult(complete=False)
    doc = (read_journal(journal) if journal is not None else None) or {}
    doc["root"] = str(root)
    done: dict[str, str] = dict(doc.get("files") or {})

    def mark(path: str, state: str) -> None:
        if journal is None:
            return
        done[path] = state
        doc["files"] = done
        write_journal(journal, doc)

    wrote = skipped = conflicted = failed = 0
    for e in plan.entries:
        if e.verdict == "unchanged":
            skipped += 1
            continue
        want = e.verdict in ("new", "changed") or (force and e.verdict == "conflicted")
        if not want:
            conflicted += 1
            continue
        # 절차 3 — 비교와 교체 **사이**에 사람이 고쳤으면 그 파일은 건너뛴다
        if _sha_of(root / e.path) != e.local_sha:
            conflicted += 1
            continue
        try:
            _install(staging, root, e.path)
        except OSError:
            # 디스크가 찼거나 권한이 없다 — 원본은 그대로다(임시 파일만 지웠다)
            failed += 1
            mark(e.path, "failed")
            continue
        except BaseException:
            # 중간에 죽었다. **쓰지 못한 파일을 적용됐다고 적지 않는다** — 재개가 거짓말이 된다.
            raise
        mark(e.path, WRITTEN)
        wrote += 1
    return ApplyResult(
        wrote=wrote,
        skipped=skipped,
        conflicted=conflicted,
        failed=failed,
        complete=conflicted == 0 and failed == 0,
    )
