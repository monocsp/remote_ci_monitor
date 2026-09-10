"""서버가 자기 클라이언트 wheel 을 조립한다 (M5i I8-1 · 결정 81).

배포 채널이 GitHub 릴리스뿐이면 「서버가 도는 코드」와 「클라가 받을 수 있는 코드」가 다른 물건이다
(docs/gate-replay-fixes-workplan.md §3 I8 — 서버 0.2.5 · 노트북 0.2.2 · 릴리스 v0.2.4). 그래서
서버가 기동할 때 **설치된 패키지에서 표준 라이브러리만으로** wheel 을 조립해 `/client/<정확한
파일명>` 으로 준다. 빌드 도구·hatchling 은 들이지 않는다(런타임 의존성 0).

내용: `importlib.resources.files("remote_ci_monitor")` 아래의 `.py` 와 `web/`·`templates/` 전부
(`__pycache__` 제외) + `remote_ci_monitor-<X>.dist-info/`. `METADATA` 와 `entry_points.txt` 는
설치된 메타데이터를 그대로 쓰되 **`Version:` 은 도는 `__version__` 으로** 맞춘다 — 운영은 editable
설치를 `git pull` + 재시작으로 올리므로 디스크의 dist-info 는 설치 당시 버전에 머문다. `WHEEL` 과
`RECORD` 는 새로 쓴다. 릴리스 wheel 과 바이트가 같지 않고, 같아야 하는 것은 설치 결과다(§9).

조립 실패(메타데이터·파일 없음)는 `WheelBuildError(code)` — 빈 wheel 이나 옛 wheel 을 만들지
않는다(fail-open 금지).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib.resources
import io
import zipfile
from collections.abc import Iterable

PACKAGE = "remote_ci_monitor"
#: 서버가 받는 가장 오래된 클라이언트 버전 (I8-3 · 결정 83). 와이어 계약이 깨질 때만 올린다.
#: 0.2.x 클라이언트는 전부 붙는다.
MIN_CLIENT_VERSION = "0.2.0"
#: 패키지 파일 중 wheel 에 싣는 것 — `.py` 는 어디든, 이 두 디렉터리는 통째로.
ASSET_DIRS = ("web", "templates")
#: 이것들이 없으면 wheel 이 아니라 오류다 — release.yml 의 산출물 검사와 같은 셋.
REQUIRED_FILES = (
    "__init__.py",
    "cli.py",
    "web/index.html",
    "templates/server.toml",
    "templates/client.toml",
)
#: 결정적 zip — 기동마다 sha256 이 같아야 ETag 와 health 의 해시가 뜻을 가진다.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


class WheelBuildError(Exception):
    """조립 실패. `code` 는 health 의 `client_wheel_error` 에 그대로 실린다."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def wheel_filename(version: str) -> str:
    """`remote_ci_monitor-<X>-py3-none-any.whl`. pip 는 URL 의 마지막 마디로 파일 종류를 정한다."""
    return f"{PACKAGE}-{version}-py3-none-any.whl"


def dist_info_name(version: str) -> str:
    return f"{PACKAGE}-{version}.dist-info"


def _record_hash(data: bytes) -> str:
    """RECORD 의 해시 표기 — `sha256=<urlsafe base64, 패딩 없음>` (PEP 427)."""
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _package_files() -> list[tuple[str, bytes]]:
    """설치된 패키지의 파일들 — (패키지 안 상대 경로, 바이트). 이름순."""
    try:
        root = importlib.resources.files(PACKAGE)
    except (ModuleNotFoundError, TypeError) as e:
        raise WheelBuildError("package_missing", str(e)) from e
    out: list[tuple[str, bytes]] = []

    def walk(node: importlib.resources.abc.Traversable, prefix: str) -> None:
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            name = child.name
            if name.startswith("."):
                continue  # 편집기·OS 찌꺼기(.DS_Store)
            rel = prefix + name
            if child.is_dir():
                if name == "__pycache__":
                    continue
                walk(child, rel + "/")
                continue
            if name.endswith((".pyc", ".pyo")):
                continue
            if rel.endswith(".py") or rel.startswith(tuple(d + "/" for d in ASSET_DIRS)):
                try:
                    out.append((rel, child.read_bytes()))
                except OSError as e:
                    raise WheelBuildError("file_unreadable", rel) from e

    walk(root, "")
    have = {rel for rel, _ in out}
    missing = [f for f in REQUIRED_FILES if f not in have]
    if missing:
        raise WheelBuildError("files_missing", ", ".join(missing))
    return out


def _dist_metadata(version: str) -> tuple[str, str, list[tuple[str, bytes]]]:
    """설치된 dist-info 에서 (METADATA, entry_points.txt, licenses/* 파일들). 없으면 오류.

    `Version:` 줄은 도는 버전으로 바꾼다 — 파일명·dist-info 이름과 다르면 pip 가 거부한다.
    """
    try:
        dist = importlib.metadata.distribution(PACKAGE)
    except importlib.metadata.PackageNotFoundError as e:
        raise WheelBuildError("metadata_missing", PACKAGE) from e
    metadata = dist.read_text("METADATA")
    entry_points = dist.read_text("entry_points.txt")
    if not metadata or "Name:" not in metadata:
        raise WheelBuildError("metadata_missing", "METADATA")
    if not entry_points or "rcm" not in entry_points:
        raise WheelBuildError("metadata_missing", "entry_points.txt")
    lines = metadata.splitlines()
    fixed = [f"Version: {version}" if ln.startswith("Version:") else ln for ln in lines]
    if fixed == lines and not any(ln.startswith("Version:") for ln in lines):
        raise WheelBuildError("metadata_missing", "Version")
    licenses: list[tuple[str, bytes]] = []
    for p in dist.files or ():
        parts = p.parts
        if len(parts) == 3 and parts[0].endswith(".dist-info") and parts[1] == "licenses":
            try:
                licenses.append((f"licenses/{parts[2]}", p.read_binary()))
            except OSError:
                continue  # 라이선스 사본은 있으면 싣고 없으면 만다 — 설치에 필요하지 않다
    return "\n".join(fixed) + "\n", entry_points, licenses


def _zip(entries: Iterable[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zi = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, data)
    return buf.getvalue()


def build_wheel(version: str) -> bytes:
    """`remote_ci_monitor-<version>-py3-none-any.whl` 의 바이트. 실패는 `WheelBuildError`."""
    files = _package_files()
    metadata, entry_points, licenses = _dist_metadata(version)
    info = dist_info_name(version)
    entries: list[tuple[str, bytes]] = [(f"{PACKAGE}/{rel}", data) for rel, data in files]
    entries.append((f"{info}/METADATA", metadata.encode()))
    entries.append((f"{info}/entry_points.txt", entry_points.encode()))
    entries.append(
        (
            f"{info}/WHEEL",
            (
                "Wheel-Version: 1.0\n"
                f"Generator: {PACKAGE} {version}\n"
                "Root-Is-Purelib: true\n"
                "Tag: py3-none-any\n"
            ).encode(),
        )
    )
    entries.extend((f"{info}/{rel}", data) for rel, data in licenses)
    record_name = f"{info}/RECORD"
    record = "".join(f"{name},{_record_hash(data)},{len(data)}\n" for name, data in entries)
    record += f"{record_name},,\n"
    entries.append((record_name, record.encode()))
    return _zip(entries)
