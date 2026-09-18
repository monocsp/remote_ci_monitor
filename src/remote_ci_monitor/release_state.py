"""릴리스 화면의 상태 — 잡 표와 산출물 표에서 **역할별 최신**을 고른다(스토어 탭 API 계약 v2
`GET …/release`). 네트워크도 저장소도 부르지 않는다: 여기 있는 것은 순수 규칙이고, 잡 목록과
묶음 파일은 호출자(`server.App`)가 넘긴다.

규칙:
- 「역할의 최신」 = 그 역할 프리셋의 잡 중 산출물에 역할의 JSON 파일이 **들어 있는** 가장 새 잡.
  파일 이름은 basename 으로 맞춘다(묶음 안 어디에 있든). 돌고 있는 잡은 산출물이 없으니 후보가
  아니다 — 화면은 `jobs[]` 로 그 잡을 본다.
- `doc` 은 묶음에서 읽은 JSON(≤ 256 KB). 못 읽으면 `doc: null` + `doc_error` — 있는 척 안 한다.
- `measured_at` 은 문서의 `measured_at`(ISO) 이고 없으면 잡이 끝난 시각. `age_seconds` 는 지금과의
  차이, `stale` 은 `plan_max_age_minutes` 를 넘었는가. 오래된 계획은 되돌릴 수 없는 버튼을 열지
  않는다(계약 §6).
"""

from __future__ import annotations

import json
import re
import tarfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor.core.model import Job
from remote_ci_monitor.core.status import iso

#: 역할 → 묶음에서 찾는 파일. review 는 모드에 따라 둘
#: (plan → review-plan.json · submit → review.json).
ROLE_FILES: dict[str, tuple[str, ...]] = {
    "plan": ("plan.json",),
    "upload": ("upload.json",),
    "review": ("review-plan.json", "review.json"),
    "version": ("version.json", "prefill.json"),
}
#: `jobs[]` 에 싣는 역할 — 산출물 파일이 있는 셋에 더해 gate · qa · dev · version 도 행으로는
#: 보인다.
LISTED_ROLES = ("plan", "upload", "review", "gate", "qa", "dev", "version")
#: `prefill.json` · 편집본의 플랫폼별 문안 키(계약 §2 review 의 listing 필드 이름과 같다).
#: 스크린샷은 보기만이라 편집본에 없다.
PREFILL_KEYS: dict[str, tuple[str, ...]] = {
    "ios": (
        "subtitle",
        "promotional_text",
        "description",
        "keywords",
        "support_url",
        "marketing_url",
        "whats_new",
    ),
    "android": ("title", "short_description", "full_description", "whats_new"),
}
PLATFORMS = tuple(PREFILL_KEYS)
_VERSION_NAME_RE = re.compile(r"^\d+\.\d+\.\d+$")
_TRAILING_INT_RE = re.compile(r"^((?:\d+\.)*)(\d+)$")
MAX_DOC_BYTES = 256 * 1024
JOBS_LIMIT = 50

#: `(job_id, 묶음 안 경로)` → 바이트. 실패는 예외로.
ReadFile = Callable[[int, str], bytes]


def read_bundle_member(bundle_path: Path, member: str) -> bytes:
    """묶음 tar 에서 파일 하나. 크기 상한을 넘으면 `ValueError`."""
    with tarfile.open(bundle_path, "r") as tar:
        info = tar.getmember(member)
        if not info.isfile():
            raise ValueError("not a regular file")
        if info.size > MAX_DOC_BYTES:
            raise ValueError(f"larger than {MAX_DOC_BYTES} bytes")
        fh = tar.extractfile(info)
        if fh is None:
            raise ValueError("cannot read member")
        return fh.read()


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def artifact_names(files: Iterable[Any]) -> list[str]:
    """묶음 매니페스트의 항목들 → basename 목록(순서 유지 · 중복 제거)."""
    out: list[str] = []
    for f in files:
        path = f.get("path") if isinstance(f, dict) else getattr(f, "path", None)
        if isinstance(path, str) and path:
            name = _basename(path)
            if name not in out:
                out.append(name)
    return out


def _find_member(files: Iterable[Any], basename: str) -> str | None:
    for f in files:
        path = f.get("path") if isinstance(f, dict) else getattr(f, "path", None)
        if isinstance(path, str) and _basename(path) == basename:
            return path
    return None


def artifact_member(files: Iterable[Any], basename: str) -> str | None:
    """묶음 매니페스트에서 basename 이 맞는 첫 항목의 경로(어디에 있든). 없으면 None."""
    return _find_member(files, basename)


def parse_doc(data: bytes) -> tuple[Any, str | None]:
    """바이트 → (문서, 오류). JSON 이 아니거나 객체가 아니면 문서는 None."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return None, f"not valid JSON: {type(e).__name__}"
    if not isinstance(doc, dict):
        return None, "not a JSON object"
    return doc, None


def parse_iso(value: Any) -> datetime | None:
    """문서의 `measured_at`(ISO 8601, `Z` 허용). 못 읽으면 None — 지어내지 않는다."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def job_row(job: Job, role: str, names: list[str]) -> dict[str, Any]:
    """`jobs[]` 의 행 하나 — 잡 번호 · 프리셋 · 역할 · 상태 · sha · ref · 시각 · 산출물 이름."""
    return {
        "id": job.id,
        "preset": job.preset,
        "role": role,
        "state": job.state,
        "sha": job.source.sha,
        "ref": job.source.ref,
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "artifacts": list(names),
    }


def role_entry(
    job: Job,
    member: str,
    read_file: ReadFile,
    *,
    now: datetime | None = None,
    max_age_minutes: int | None = None,
) -> dict[str, Any]:
    """역할의 최신 항목. `now` 와 `max_age_minutes` 를 주면 나이와 stale 도 잰다
    (plan · review plan)."""
    doc: Any = None
    error: str | None = None
    try:
        doc, error = parse_doc(read_file(job.id, member))
    except (OSError, KeyError, ValueError, tarfile.TarError) as e:
        error = f"cannot read {_basename(member)}: {type(e).__name__}"
    out: dict[str, Any] = {"job_id": job.id, "state": job.state}
    if now is not None and max_age_minutes is not None:
        measured = parse_iso(doc.get("measured_at")) if isinstance(doc, dict) else None
        if measured is None:
            measured = job.finished_at
        age = max(0, int((now - measured).total_seconds())) if measured is not None else None
        out["measured_at"] = iso(measured)
        out["age_seconds"] = age
        out["stale"] = age is None or age > max_age_minutes * 60
        if isinstance(doc, dict):
            name = doc.get("build_name")
            out["build_name"] = name if isinstance(name, str) else None
        else:
            out["build_name"] = None
    out["doc"] = doc
    if error is not None:
        out["doc_error"] = error
    return out


def release_view(
    jobs: Iterable[Job],
    presets: dict[str, str | None],
    names_of: Callable[[int], list[Any]],
    read_file: ReadFile,
    *,
    now: datetime,
    max_age_minutes: int,
) -> dict[str, Any]:
    """`GET …/release` 의 `plan` · `review` · `upload` · `jobs`. `jobs` 는 새것부터 ≤ 50 이고
    역할 프리셋의 잡만이다. `names_of(job_id)` 는 묶음 매니페스트의 파일 항목들(없으면 빈 목록)."""
    role_of = {name: role for role, name in presets.items() if name}
    rows: list[dict[str, Any]] = []
    latest: dict[str, tuple[Job, str]] = {}  # 파일 basename → (잡, 묶음 안 경로)
    for job in jobs:
        role = role_of.get(job.preset)
        if role is None:
            continue
        files = names_of(job.id)
        if len(rows) < JOBS_LIMIT:
            rows.append(job_row(job, role, artifact_names(files)))
        for basename in ROLE_FILES.get(role, ()):
            if basename in latest:
                continue
            member = _find_member(files, basename)
            if member is not None:
                latest[basename] = (job, member)

    def entry(basename: str, *, aged: bool) -> dict[str, Any] | None:
        found = latest.get(basename)
        if found is None:
            return None
        job, member = found
        if aged:
            return role_entry(job, member, read_file, now=now, max_age_minutes=max_age_minutes)
        return role_entry(job, member, read_file)

    return {
        "plan": entry("plan.json", aged=True),
        "review": {
            "plan": entry("review-plan.json", aged=True),
            "result": entry("review.json", aged=False),
        },
        "upload": entry("upload.json", aged=False),
        "jobs": rows,
    }


def plan_number(entry: dict[str, Any] | None) -> int | None:
    """계획 문서의 `n` — 정수만. 문자열 "181" 도 정수로 받되 그 밖은 None(모른다)."""
    if entry is None or not isinstance(entry.get("doc"), dict):
        return None
    n = entry["doc"].get("n")
    if isinstance(n, bool):
        return None
    if isinstance(n, int):
        return n
    if isinstance(n, str) and n.strip().isdigit():
        return int(n.strip())
    return None


AUTO_BUILD_NUMBER = "auto"


def resolve_build_number(typed: Any, n: int | None) -> int | None:
    """보낸 값 → 실제로 쓸 번호. `"auto"` 는 계획의 `n` 그대로(«빌드 번호 자동» 토글), 숫자는 계획의
    `n` 과 같을 때만. 둘 다 아니거나 계획에 번호가 없으면 None — 서버는 번호를 지어내지 않는다."""
    if n is None:
        return None
    if isinstance(typed, str) and typed.strip().lower() == AUTO_BUILD_NUMBER:
        return n
    return n if numbers_match(typed, n) else None


def numbers_match(typed: Any, n: int | None) -> bool:
    """사람이 친 번호와 계획의 `n` — 정수로서 같은가. 비어 있거나 숫자가 아니면 거짓."""
    if n is None or not isinstance(typed, str | int) or isinstance(typed, bool):
        return False
    text = str(typed).strip()
    return text.isdigit() and int(text) == n


def _store_of(plan_doc: Any) -> dict[str, Any]:
    store = plan_doc.get("store") if isinstance(plan_doc, dict) else None
    return store if isinstance(store, dict) else {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def live_versions(plan_doc: Any) -> dict[str, str | None]:
    """계획 문서가 말하는 라이브 이름 — iOS 는 `store.asc_live`, Android 는
    `store.play.production_name`. 모르면 None(지어내지 않는다)."""
    store = _store_of(plan_doc)
    play = store.get("play") if isinstance(store.get("play"), dict) else {}
    return {"ios": _text(store.get("asc_live")), "android": _text(play.get("production_name"))}


def bump_last_number(name: str | None) -> str | None:
    """`1.1.0 → 1.1.1` · `2.0 → 2.1`. 끝이 정수가 아니면(`1.0.0-rc1`) None."""
    if name is None:
        return None
    m = _TRAILING_INT_RE.match(name.strip())
    if m is None:
        return None
    return f"{m.group(1)}{int(m.group(2)) + 1}"


def next_version_hint(plan_doc: Any) -> dict[str, str | None]:
    """다음 버전 이름의 힌트(워크플랜 §3.1). 문서의 `next_version_hint` 가 있으면 그대로, 없으면
    라이브 이름의 마지막 정수 +1. 웹의 `nextVersionHint` 와 같은 규칙이다."""
    out: dict[str, str | None] = {"ios": None, "android": None}
    given = plan_doc.get("next_version_hint") if isinstance(plan_doc, dict) else None
    live = live_versions(plan_doc)
    for platform in PLATFORMS:
        hint = _text(given.get(platform)) if isinstance(given, dict) else None
        out[platform] = hint if hint is not None else bump_last_number(live[platform])
    return out


def _parts(name: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in name.strip().split("."))
    except ValueError:
        return None


def version_name_check(name: Any, live: str | None) -> tuple[bool, str | None]:
    """새 버전 이름 검사 — `(ok, reason)`. `empty` · `pattern`(major.minor.patch 정수 셋) ·
    `not_greater`(라이브를 알 때만, 정수 튜플 비교). 라이브가 숫자 꼴이 아니면 크기는 안 본다."""
    if not isinstance(name, str) or not name.strip():
        return False, "empty"
    if not _VERSION_NAME_RE.match(name.strip()):
        return False, "pattern"
    if live:
        mine, theirs = _parts(name), _parts(live)
        if mine is not None and theirs is not None and mine <= theirs:
            return False, "not_greater"
    return True, None


def _norm(value: Any) -> str:
    """비교용 정규화 — 양끝 공백 · CRLF → LF. 문자열이 아니면 빈 값."""
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "\n").strip()


def _platform_doc(doc: Any, platform: str) -> dict[str, Any]:
    part = doc.get(platform) if isinstance(doc, dict) else None
    return part if isinstance(part, dict) else {}


def listing_diff(prefill: Any, edited: Any) -> dict[str, Any]:
    """편집본과 이전 문안의 차이 — `fields[]` 는 바뀐 것만(`{platform, key, old, new}`), 공백 ·
    줄끝만 다른 것은 같다. `screenshots` 는 플랫폼별 `same`(이전 문안이 그 플랫폼을 안다) 또는
    `n/a` — 웹 업로드는 범위 밖이라 편집본에 스크린샷이 없다."""
    fields: list[dict[str, Any]] = []
    shots: dict[str, str] = {}
    for platform in PLATFORMS:
        before = _platform_doc(prefill, platform)
        after = _platform_doc(edited, platform)
        for key in PREFILL_KEYS[platform]:
            if key not in after:
                continue
            old = before.get(key)
            new = after.get(key)
            if _norm(old) == _norm(new):
                continue
            fields.append(
                {
                    "platform": platform,
                    "key": key,
                    "old": old if isinstance(old, str) else None,
                    "new": new if isinstance(new, str) else None,
                }
            )
        shots[platform] = "same" if before else "n/a"
    return {"fields": fields, "screenshots": shots}


def merged_listing(prefill: Any, edited: Any) -> dict[str, dict[str, str]]:
    """review · upload 잡의 `listing_json` 본문 — 플랫폼별 문안 키를 이전 문안 위에 편집본으로
    덮은 것(edited ⊕ prefill). 문자열 값만, 빈 플랫폼은 뺀다."""
    out: dict[str, dict[str, str]] = {}
    for platform in PLATFORMS:
        merged: dict[str, str] = {}
        for source in (_platform_doc(prefill, platform), _platform_doc(edited, platform)):
            for key in PREFILL_KEYS[platform]:
                if isinstance(source.get(key), str):
                    merged[key] = source[key]
        if merged:
            out[platform] = merged
    return out


__all__ = [
    "AUTO_BUILD_NUMBER",
    "JOBS_LIMIT",
    "LISTED_ROLES",
    "MAX_DOC_BYTES",
    "PLATFORMS",
    "PREFILL_KEYS",
    "ROLE_FILES",
    "artifact_member",
    "artifact_names",
    "bump_last_number",
    "job_row",
    "listing_diff",
    "live_versions",
    "merged_listing",
    "next_version_hint",
    "numbers_match",
    "parse_doc",
    "parse_iso",
    "plan_number",
    "resolve_build_number",
    "read_bundle_member",
    "release_view",
    "role_entry",
    "version_name_check",
]
