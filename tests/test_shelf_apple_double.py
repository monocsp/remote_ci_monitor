"""보관함 펼치기가 macOS 메타데이터 멤버를 건너뛴다 — 2026-11-20 운영 잡 #3821.

app-map-shots 의 `shots.tar.gz` 는 macOS `tar` 가 묶었다. bsdtar 는 `COPYFILE_DISABLE` 이
없으면 확장 속성이 있는 파일마다 AppleDouble 멤버(`._intro.png`, 디렉터리 `.` 의 `._.`)를 끼워
넣는다. 펼친 48개 가운데 23개가 그것이었고 `.png` 라 `image/png` 로 분류돼 항목 페이지에 깨진
그림으로 보였다.

펼침은 rcm 이 만든 것이라 거른다. 바깥 묶음은 잡이 직접 선언한 파일이라 그대로 둔다.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from remote_ci_monitor import shelf_copier
from remote_ci_monitor.core import shelf as shelf_rules
from shelfkit import PNG_1PX

JOB = 3821


def apple_double_blob() -> bytes:
    """AppleDouble 헤더(매직 0x00051607 · 버전 2 · 엔트리 하나) — 운영에서 본 163 바이트 모양."""
    head = struct.pack(">II16sH", 0x00051607, 0x00020000, b"Mac OS X        ", 1)
    entry = struct.pack(">III", 2, 50, 113)  # resource fork 자리
    body = head + entry
    return body + bytes(163 - len(body))


def mac_tar_gz(members: list[tuple[str, bytes | None]]) -> bytes:
    """bsdtar 모양의 tar.gz — 이름 앞 `./`, pax 형식. `None` 은 디렉터리."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)
                continue
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def bundle_of(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """수집기가 만드는 바깥 묶음(무압축 tar, 일반 파일만)."""
    path = tmp_path / "bundle.tar"
    with tarfile.open(path, mode="w", format=tarfile.GNU_FORMAT) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return path


def shelve(tmp_path: Path, files: dict[str, bytes]) -> tuple[dict, Path]:
    root = tmp_path / "shelf"
    shelf_copier.shelve_bundle(
        JOB,
        bundle_of(tmp_path, files),
        root,
        expand_bytes=64 * 1024 * 1024,
        expand_files=1000,
        verdict=lambda _actual: None,
    )
    item = root / str(JOB)
    return json.loads((item / "manifest.json").read_text("utf-8")), item / "files"


SHOTS = "build/app_map/shots.tar.gz"
EXPANDED = SHOTS + ".d"

#: 운영 #3821 의 펼침에서 본 모양 + `__MACOSX/` · `.DS_Store` · 하위 폴더.
MAC_MEMBERS: list[tuple[str, bytes | None]] = [
    ("./", None),
    ("./._.", apple_double_blob()),
    ("./._intro.png", apple_double_blob()),
    ("./intro.png", PNG_1PX),
    ("./._intro.json", apple_double_blob()),
    ("./intro.json", b'{"route":"/intro"}'),
    ("./.DS_Store", b"\x00\x00\x00\x01Bud1" + bytes(64)),
    ("./sub/", None),
    ("./sub/._deep.png", apple_double_blob()),
    ("./sub/deep.png", PNG_1PX),
    ("./sub/.DS_Store", b"\x00\x00\x00\x01Bud1"),
    ("__MACOSX/", None),
    ("__MACOSX/._intro.png", apple_double_blob()),
    ("__MACOSX/sub/notes.txt", b"resource fork"),
]
REAL = {f"{EXPANDED}/intro.png", f"{EXPANDED}/intro.json", f"{EXPANDED}/sub/deep.png"}
APPLE = {
    f"{EXPANDED}/._.",
    f"{EXPANDED}/._intro.png",
    f"{EXPANDED}/._intro.json",
    f"{EXPANDED}/.DS_Store",
    f"{EXPANDED}/sub/._deep.png",
    f"{EXPANDED}/sub/.DS_Store",
    f"{EXPANDED}/__MACOSX/._intro.png",
    f"{EXPANDED}/__MACOSX/sub/notes.txt",
}


@pytest.mark.parametrize(
    "name",
    [
        "._intro.png",
        "._.",
        "sub/._deep.png",
        ".DS_Store",
        "a/b/.DS_Store",
        "__MACOSX/._x.png",
        "__MACOSX/sub/notes.txt",
        "x/__MACOSX/y.txt",
    ],
)
def test_apple_double_names_are_recognised(name):
    assert shelf_rules.apple_double_member(name)


@pytest.mark.parametrize(
    "name",
    ["intro.png", "a._b.png", "_intro.png", ".hidden.png", "DS_Store", "MACOSX/x.png", "sub/x._"],
)
def test_ordinary_names_are_not_apple_double(name):
    assert not shelf_rules.apple_double_member(name)


def test_the_reason_code_is_catalogued():
    assert shelf_rules.SKIP_APPLE_DOUBLE == "apple_double"
    assert shelf_rules.SKIP_APPLE_DOUBLE in shelf_rules.MEMBER_SKIP_REASONS


def test_expanding_a_mac_tarball_drops_apple_double_members(tmp_path):
    doc, files = shelve(
        tmp_path,
        {SHOTS: mac_tar_gz(MAC_MEMBERS), "build/app_map/manifest.json": b"{}"},
    )
    paths = {f["path"] for f in doc["files"]}
    expanded = {p for p in paths if p.startswith(EXPANDED + "/")}
    assert expanded == REAL, expanded
    for p in APPLE:
        assert not (files / p).exists(), p
    # 진짜 그림은 바이트 그대로, 그림으로 분류된다
    assert (files / EXPANDED / "intro.png").read_bytes() == PNG_1PX
    types = {f["path"]: f["type"] for f in doc["files"]}
    assert types[f"{EXPANDED}/intro.png"] == "image/png"
    # 건너뛴 것은 이유와 함께 남고 수로 센다
    skipped = {s["path"]: s for s in doc["skipped"]}
    assert set(skipped) == APPLE, skipped
    assert all(s["reason"] == "apple_double" for s in skipped.values())
    assert all(s["expanded_from"] == SHOTS for s in skipped.values())
    assert doc["skipped_count"] == len(APPLE)
    assert {s["reason"] for s in doc["skipped"]} <= set(shelf_rules.MEMBER_SKIP_REASONS)
    assert doc["expand_skipped"] == []


def test_the_outer_bundle_keeps_what_the_job_declared(tmp_path):
    """바깥 묶음의 `._x.png` · `.DS_Store` 는 잡이 글롭으로 고른 파일이다 — 거르지 않는다."""
    doc, files = shelve(
        tmp_path,
        {
            "out/._x.png": apple_double_blob(),
            "out/.DS_Store": b"\x00\x00\x00\x01Bud1",
            "out/x.png": PNG_1PX,
        },
    )
    assert {f["path"] for f in doc["files"]} == {"out/._x.png", "out/.DS_Store", "out/x.png"}
    assert doc["skipped"] == [] and doc["skipped_count"] == 0
    assert (files / "out/._x.png").read_bytes() == apple_double_blob()


def test_a_tarball_of_only_apple_double_members_expands_to_nothing(tmp_path):
    """펼칠 것이 메타데이터뿐이면 `.d` 폴더도 줄도 없다. 원본 tar 는 남는다."""
    doc, files = shelve(
        tmp_path,
        {"out/meta.tar.gz": mac_tar_gz([("./._.", apple_double_blob())]), "out/a.txt": b"a"},
    )
    assert {f["path"] for f in doc["files"]} == {"out/a.txt", "out/meta.tar.gz"}
    assert not (files / "out/meta.tar.gz.d").exists()
    assert [s["reason"] for s in doc["skipped"]] == ["apple_double"]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 의 /usr/bin/tar(bsdtar) 가 필요하다")
def test_a_tarball_made_by_macos_tar_expands_without_apple_double(tmp_path):
    """진짜 bsdtar 로 확장 속성을 단 파일을 `COPYFILE_DISABLE` 없이 묶는다(app-map-shots 의 길)."""
    tar = Path("/usr/bin/tar")
    xattr = shutil.which("xattr")
    if not tar.exists() or xattr is None:
        pytest.skip("/usr/bin/tar 또는 xattr 가 없다")
    src = tmp_path / "shots"
    src.mkdir()
    (src / "intro.png").write_bytes(PNG_1PX)
    (src / "intro.json").write_text('{"route":"/intro"}', "utf-8")
    for name in ("intro.png", "intro.json"):
        subprocess.run([xattr, "-w", "com.example.rcm", "kept", str(src / name)], check=True)
    out = tmp_path / "shots.tar.gz"
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("COPYFILE_DISABLE", "COPY_EXTENDED_ATTRIBUTES_DISABLE")
    }
    subprocess.run([str(tar), "-czf", str(out), "-C", str(src), "."], check=True, env=env)
    with tarfile.open(out) as tf:
        raw = [shelf_rules.normalize_member(n) for n in tf.getnames()]
    if not any(n.rsplit("/", 1)[-1].startswith("._") for n in raw):
        pytest.skip(f"이 macOS 의 tar 가 AppleDouble 멤버를 만들지 않았다: {raw}")
    doc, files = shelve(tmp_path, {"build/app_map/shots.tar.gz": out.read_bytes()})
    expanded = {f["path"] for f in doc["files"] if f["path"].startswith(EXPANDED + "/")}
    assert expanded == {f"{EXPANDED}/intro.png", f"{EXPANDED}/intro.json"}, (expanded, raw)
    assert (files / EXPANDED / "intro.png").read_bytes() == PNG_1PX
    assert doc["skipped"] and all(s["reason"] == "apple_double" for s in doc["skipped"])
    assert doc["skipped_count"] == sum(1 for n in raw if n.rsplit("/", 1)[-1].startswith("._"))
