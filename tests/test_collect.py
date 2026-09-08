"""M5e 수집기(`collect.py`) — 워크스페이스에서 산출물을 모아 불변 tar 로 만든다.
명세는 docs/m5e-workplan.md §4(글롭 · 경로 · 걷기 · 여는 방법) · §5(언제 · 예산) · §18.

`remote_ci_monitor/collect.py` 도 `core/artifacts.py` 도 아직 없다 — **구현 전이라 빨간 것이
정상이다.** 두 모듈은 파일 맨 위가 아니라 **시험 안에서 늦게** 부른다(`collector()` ·
`artifacts_core()`). 위에서 import 하면 파일 하나가 통째로 수집 오류가 되어 나머지 스위트까지
안 돌고, 「몇 개가 무엇 때문에 빨간지」도 알 수 없다.

집안 규칙: sleep 을 쓰지 않는다 — 예산은 `clock` 을 끼워 시각을 뛰게 해서 본다. 파일은 전부
`tmp_path` 안이고 워크스페이스 밖으로 나가는 것은 `tmp_path` 안에 따로 둔다. 네트워크는 없다.
`now_fn` 은 이 저장소의 관례대로 **`datetime`**, 예산을 재는 `clock` 은 monotonic float 다(§18,
`notify.py:97` · `hostsample.py:104`). 시나리오 문서 「명세에서 애매했던 것」에 적어 뒀다.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import shutil
import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

GOLDENS = ("test/**/goldens/*.png", "test/failures/*.png")


# ── 아직 없는 모듈 (§18) ─────────────────────────────────────────────────────


def artifacts_core() -> Any:
    """`remote_ci_monitor.core.artifacts` — 순수 규칙(§18). 구현 전에는 여기서 빨개진다."""
    try:
        import remote_ci_monitor.core.artifacts as mod
    except ImportError as e:
        pytest.fail(
            f"remote_ci_monitor.core.artifacts is not implemented yet "
            f"(docs/m5e-workplan.md §18): {e}",
            pytrace=False,
        )
    return mod


def collector() -> Any:
    """`remote_ci_monitor.collect` — 수집기(§18). 구현 전에는 여기서 빨개진다."""
    try:
        import remote_ci_monitor.collect as mod
    except ImportError as e:
        pytest.fail(
            f"remote_ci_monitor.collect is not implemented yet (docs/m5e-workplan.md §18): {e}",
            pytrace=False,
        )
    return mod


class Clock:
    """주입 시계. 부를 때마다 다음 초를 주고 마지막 값은 계속 되풀이한다 — sleep 이 필요 없다.

    예산은 monotonic 으로 잰다(§18 의 `clock`) — 벽시계가 뒤로 튀어도 예산이 음수가 되지 않는다.
    그래서 `datetime` 이 아니라 **float** 를 돌려준다.
    """

    def __init__(self, *offsets: float) -> None:
        self.times = list(offsets or (0.0,))
        self.calls = 0

    def __call__(self) -> float:
        at = self.times[min(self.calls, len(self.times) - 1)]
        self.calls += 1
        return at


def write(root: Path, rel: str, data: bytes = b"x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def workspace(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    for rel, data in (files or {}).items():
        write(ws, rel, data)
    return ws


def staging(tmp_path: Path, name: str = "staging") -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def policy(core: Any, *globs: str, **kw: Any) -> Any:
    return core.ArtifactPolicy(globs=globs, **kw)


def paths(result: Any) -> list[str]:
    return [f.path for f in result.files]


def tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


@contextlib.contextmanager
def opened(mod: Any, root: Path, rel: str) -> Iterator[int]:
    fd = mod.open_anchored(root, rel)
    try:
        yield fd
    finally:
        os.close(fd)


def folding_filesystem(tmp_path: Path) -> bool:
    """이 파일 시스템이 이름을 접는가(대소문자·정규화). macOS APFS 는 접는다.

    접는 파일 시스템에서는 **충돌하는 두 파일을 애초에 만들 수 없다** — 규칙 자체는
    `tests/test_artifacts_rules.py::collisions` 가 순수하게 잠근다. 여기서는 배선만 본다.
    """
    probe = tmp_path / "fold-probe"
    probe.mkdir()
    (probe / "A.png").write_bytes(b"1")
    (probe / "a.png").write_bytes(b"2")
    folded = len(list(probe.iterdir())) == 1
    shutil.rmtree(probe)
    return folded


# ── open_anchored — 조각마다 닻을 내린다 ─────────────────────────────────────


def test_open_anchored_opens_a_plain_file(tmp_path: Path) -> None:
    mod = collector()
    ws = workspace(tmp_path, {"goldens/a.png": b"PNGDATA"})
    with opened(mod, ws, "goldens/a.png") as fd:
        assert isinstance(fd, int) and fd >= 0
        assert os.fstat(fd).st_size == 7
        assert os.read(fd, 100) == b"PNGDATA"


def test_open_anchored_opens_a_file_at_the_root(tmp_path: Path) -> None:
    mod = collector()
    ws = workspace(tmp_path, {"a.png": b"AA"})
    with opened(mod, ws, "a.png") as fd:
        assert os.read(fd, 100) == b"AA"


def test_open_anchored_refuses_a_symlinked_parent_component(tmp_path: Path) -> None:
    """`O_NOFOLLOW` 는 마지막 조각만 지킨다 — 부모가 링크인 경우가 이 함수의 존재 이유다(§4)."""
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"real/x.png": b"1"})
    (ws / "via").symlink_to(ws / "real")
    assert (ws / "via" / "x.png").read_bytes() == b"1"  # 평범하게 열면 열린다
    with pytest.raises(core.ArtifactError):
        mod.open_anchored(ws, "via/x.png")


def test_open_anchored_refuses_a_symlinked_parent_two_levels_up(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"real/deep/x.png": b"1"})
    (ws / "via").symlink_to(ws / "real")
    with pytest.raises(core.ArtifactError):
        mod.open_anchored(ws, "via/deep/x.png")


def test_open_anchored_refuses_a_symlinked_final_component(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"1"})
    (ws / "goldens" / "link.png").symlink_to(ws / "goldens" / "a.png")
    with pytest.raises(core.ArtifactError):
        mod.open_anchored(ws, "goldens/link.png")


def test_open_anchored_refuses_a_symlink_even_when_it_stays_inside(tmp_path: Path) -> None:
    """경계는 「링크가 없다」지 「밖으로 안 나간다」가 아니다 — 안을 가리켜도 거부한다."""
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"1"})
    (ws / "inside.png").symlink_to(ws / "goldens" / "a.png")
    with pytest.raises(core.ArtifactError):
        mod.open_anchored(ws, "inside.png")


def test_open_anchored_refuses_a_symlink_pointing_outside(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not yours")
    ws = workspace(tmp_path)
    (ws / "escape.png").symlink_to(outside)
    with pytest.raises(core.ArtifactError):
        mod.open_anchored(ws, "escape.png")


def test_open_anchored_refuses_a_dangling_symlink(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path)
    (ws / "dangle.png").symlink_to(ws / "nowhere.png")
    with pytest.raises(core.ArtifactError):
        mod.open_anchored(ws, "dangle.png")


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.txt",
        "a/../../outside.txt",
        "..",
        "a/..",
        "/etc/hosts",
        "/",
        "",
        "./a.png",
        "a//b.png",
        "a\\b.png",
        "a\x00b.png",
    ],
)
def test_open_anchored_refuses_a_path_that_leaves_the_root_or_is_not_normal(
    tmp_path: Path, rel: str
) -> None:
    """`..` 조각은 `O_DIRECTORY|O_NOFOLLOW` 로도 그냥 열린다 — 경로 규칙이 먼저 막아야 한다.

    거절만 하면 예외 종류는 묶지 않는다: `check_path` 를 그대로 통과시키면 `PolicyError` 고,
    감싸면 `ArtifactError` 다(§18 은 링크·비정규 파일만 `ArtifactError` 로 못 박았다).
    """
    mod, core = collector(), artifacts_core()
    (tmp_path / "outside.txt").write_bytes(b"not yours")
    ws = workspace(tmp_path, {"a.png": b"1", "a/b.png": b"1"})
    with pytest.raises((core.ArtifactError, core.PolicyError)):
        mod.open_anchored(ws, rel)


def test_open_anchored_refuses_a_directory(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"1"})
    with pytest.raises(core.ArtifactError):
        mod.open_anchored(ws, "goldens")


def test_open_anchored_on_a_missing_file_does_not_succeed(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path)
    with pytest.raises((core.ArtifactError, FileNotFoundError)):
        mod.open_anchored(ws, "nope.png")


# ── collect — 무엇을 모으는가 ────────────────────────────────────────────────


def test_collect_takes_only_the_glob_matches(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(
        tmp_path,
        {
            "test/goldens/b.png": b"BB",
            "test/widget/goldens/a.png": b"AAA",
            "test/failures/diff.png": b"DDDD",
            "test/goldens/notes.txt": b"nope",
            "test/other/x.png": b"nope",
            "README.md": b"nope",
        },
    )
    r = mod.collect(ws, policy(core, *GOLDENS), staging(tmp_path))
    assert r.state == core.READY
    assert paths(r) == [
        "test/failures/diff.png",
        "test/goldens/b.png",
        "test/widget/goldens/a.png",
    ]
    assert r.total_bytes == 2 + 3 + 4
    assert r.skipped_count == 0
    assert r.reason_code is None and r.reason_args is None


def test_collect_records_size_hash_and_normalised_mode(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"AAA", "goldens/run.png": b"BB"})
    (ws / "goldens" / "a.png").chmod(0o600)
    (ws / "goldens" / "run.png").chmod(0o700)
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    by_path = {f.path: f for f in r.files}
    assert by_path["goldens/a.png"] == core.BundleFile(
        path="goldens/a.png", size=3, sha256=hashlib.sha256(b"AAA").hexdigest(), mode=0o644
    )
    assert by_path["goldens/run.png"].mode == 0o755  # 실행 비트만 본다


def test_collect_writes_into_staging_and_leaves_the_workspace_alone(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"AAA"})
    stage = staging(tmp_path)
    before = tree(ws)
    r = mod.collect(ws, policy(core, "goldens/*.png"), stage)
    assert r.bundle_path is not None
    assert stage in r.bundle_path.parents
    assert r.bundle_path.is_file()
    assert r.bundle_bytes == r.bundle_path.stat().st_size
    assert r.bundle_sha256 == hashlib.sha256(r.bundle_path.read_bytes()).hexdigest()
    assert len(r.bundle_sha256) == 64
    assert tree(ws) == before


def test_dot_git_is_never_collected_whatever_the_glob_says(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(
        tmp_path,
        {
            ".git/objects/aa/bb.png": b"GIT",
            "sub/.git/HEAD.png": b"GIT",
            "sub/ok.png": b"OK",
            ".gitignore.png": b"OK",
        },
    )
    r = mod.collect(ws, policy(core, "**/*.png", ".git/**/*.png"), staging(tmp_path))
    assert paths(r) == [".gitignore.png", "sub/ok.png"]
    assert r.skipped_count == 0


def test_a_symlinked_directory_is_not_followed(tmp_path: Path) -> None:
    """`os.walk(followlinks=False)` — 링크된 디렉터리 안으로 내려가지 않는다(§4)."""
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"test/keep.txt": b"x"})
    outside = tmp_path / "outside" / "goldens"
    outside.mkdir(parents=True)
    (outside / "leak.png").write_bytes(b"NOT YOURS")
    (ws / "test" / "goldens").symlink_to(outside)
    r = mod.collect(
        ws,
        policy(core, "test/**/goldens/*.png", "test/goldens/*.png"),
        staging(tmp_path),
    )
    assert r.state == core.EMPTY and r.reason_code == "no_match"
    assert r.files == () and r.skipped_count == 0


# ── collect — 일반 파일만 모으고, 맞았지만 아닌 것을 센다 ────────────────────


def test_symlinks_hardlinks_and_fifos_are_skipped_and_counted(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"AAA", "data/orig.bin": b"HARD"})
    (ws / "goldens" / "link.png").symlink_to(ws / "goldens" / "a.png")
    os.link(ws / "data" / "orig.bin", ws / "goldens" / "hard.png")  # st_nlink > 1
    os.mkfifo(ws / "goldens" / "pipe.png")
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.state == core.READY
    assert paths(r) == ["goldens/a.png"]
    assert r.skipped_count == 3
    assert (ws / "goldens" / "a.png").stat().st_nlink == 1
    assert (ws / "goldens" / "hard.png").stat().st_nlink == 2


def test_skipped_count_only_counts_entries_that_matched_a_glob(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"A", "notes/plain.txt": b"x"})
    (ws / "goldens" / "link.png").symlink_to(ws / "goldens" / "a.png")
    (ws / "notes" / "link.txt").symlink_to(ws / "notes" / "plain.txt")
    os.mkfifo(ws / "notes" / "pipe.txt")
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.state == core.READY
    assert paths(r) == ["goldens/a.png"]
    assert r.skipped_count == 1  # 글롭에 맞은 링크 하나만 — notes/ 의 둘은 세지 않는다


def test_a_bundle_of_only_skipped_entries_is_empty_not_ready(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"data/orig.bin": b"HARD"})
    (ws / "goldens").mkdir()
    os.link(ws / "data" / "orig.bin", ws / "goldens" / "hard.png")
    os.mkfifo(ws / "goldens" / "pipe.png")
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.state == core.EMPTY and r.reason_code is None  # 맞긴 맞았다 — no_match 는 거짓이다
    assert r.skipped_count == 2
    assert r.files == () and r.bundle_path is None


# ── collect — tar 의 모양 ────────────────────────────────────────────────────


def test_the_written_tar_holds_regular_files_only_in_sorted_order(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(
        tmp_path, {"goldens/b.png": b"BB", "goldens/a.png": b"AAA", "goldens/run.png": b"C"}
    )
    (ws / "goldens" / "run.png").chmod(0o755)
    (ws / "goldens" / "link.png").symlink_to(ws / "goldens" / "a.png")
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.bundle_path is not None
    with tarfile.open(r.bundle_path) as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == ["goldens/a.png", "goldens/b.png", "goldens/run.png"]
        assert all(m.isfile() for m in members)
        assert not any(m.issym() or m.islnk() or m.isdir() for m in members)
        assert {m.name: m.mode for m in members} == {
            "goldens/a.png": 0o644,
            "goldens/b.png": 0o644,
            "goldens/run.png": 0o755,
        }
        extracted = tar.extractfile("goldens/a.png")
        assert extracted is not None and extracted.read() == b"AAA"


def test_the_tar_members_match_the_manifest_exactly(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"AAA", "goldens/b.png": b"BB"})
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.bundle_path is not None
    with tarfile.open(r.bundle_path) as tar:
        assert [m.name for m in tar.getmembers()] == paths(r)
        assert [m.size for m in tar.getmembers()] == [f.size for f in r.files]


def test_bundle_sha256_is_stable_across_two_runs_of_the_same_input(tmp_path: Path) -> None:
    """묶음은 불변이다 — 같은 입력이면 같은 바이트, 같은 해시다(§3)."""
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"AAA", "goldens/b.png": b"BB"})
    one = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path, "s1"))
    two = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path, "s2"))
    assert one.bundle_sha256 == two.bundle_sha256
    assert one.bundle_bytes == two.bundle_bytes
    assert one.bundle_path != two.bundle_path
    assert one.bundle_path is not None and two.bundle_path is not None
    assert one.bundle_path.read_bytes() == two.bundle_path.read_bytes()


def test_different_content_gives_a_different_bundle_hash(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    first = workspace(tmp_path, {"goldens/a.png": b"AAA"})
    one = mod.collect(first, policy(core, "goldens/*.png"), staging(tmp_path, "s1"))
    (first / "goldens" / "a.png").write_bytes(b"BBB")
    two = mod.collect(first, policy(core, "goldens/*.png"), staging(tmp_path, "s2"))
    assert one.bundle_sha256 != two.bundle_sha256


# ── collect — 상한 ──────────────────────────────────────────────────────────


def numeric(args: dict[str, Any] | None) -> bool:
    """공개 `reason_args` 는 수치만 담는다(§3) — 참/거짓도 수치가 아니다."""
    return bool(args) and all(
        isinstance(v, int) and not isinstance(v, bool) for v in (args or {}).values()
    )


def test_over_max_bytes_is_dropped_with_over_bytes(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {f"goldens/{i}.png": b"x" * 100 for i in range(5)})
    r = mod.collect(ws, policy(core, "goldens/*.png", max_bytes=250), staging(tmp_path))
    assert r.state == core.DROPPED and r.reason_code == "over_bytes"
    assert r.reason_args is not None
    assert r.reason_args["limit"] == 250
    assert r.reason_args["seen"] > 250
    assert numeric(r.reason_args)
    assert r.bundle_path is None and r.bundle_sha256 is None
    assert r.files == () and r.bundle_bytes == 0  # 버린 묶음의 파일 목록은 내보내지 않는다


def test_max_bytes_exactly_at_the_limit_is_not_over(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"x" * 100, "goldens/b.png": b"y" * 150})
    r = mod.collect(ws, policy(core, "goldens/*.png", max_bytes=250), staging(tmp_path))
    assert r.state == core.READY and r.total_bytes == 250


def test_over_max_files_is_dropped_with_over_files(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {f"goldens/{i}.png": b"x" for i in range(6)})
    r = mod.collect(ws, policy(core, "goldens/*.png", max_files=5), staging(tmp_path))
    assert r.state == core.DROPPED and r.reason_code == "over_files"
    assert r.reason_args is not None
    assert r.reason_args["limit"] == 5
    assert r.reason_args["seen"] > 5
    assert numeric(r.reason_args)
    assert r.bundle_path is None and r.files == ()


def test_max_files_exactly_at_the_limit_is_not_over(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {f"goldens/{i}.png": b"x" for i in range(5)})
    r = mod.collect(ws, policy(core, "goldens/*.png", max_files=5), staging(tmp_path))
    assert r.state == core.READY and len(r.files) == 5


def test_the_two_limits_are_counted_separately(tmp_path: Path) -> None:
    # 파일은 상한 안인데 바이트가 넘는다 → over_bytes 지 over_files 가 아니다
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {f"goldens/{i}.png": b"x" * 100 for i in range(3)})
    r = mod.collect(
        ws, policy(core, "goldens/*.png", max_bytes=100, max_files=10), staging(tmp_path)
    )
    assert r.reason_code == "over_bytes"


# ── collect — 예산 (시계를 끼운다, sleep 없음) ──────────────────────────────


def test_a_blown_budget_is_dropped_and_timed_out(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {f"goldens/{i:03d}.png": b"x" * 10 for i in range(40)})
    clock = Clock(0.0, 90.0)  # 두 번째 호출부터 예산 밖이다
    r = mod.collect(
        ws, policy(core, "goldens/*.png", timeout_seconds=60), staging(tmp_path), clock=clock
    )
    assert r.state == core.DROPPED and r.reason_code == "timed_out"
    assert clock.calls >= 2  # 걷는 동안 시계를 다시 본다 — 끝나고 한 번 보는 것으로는 못 막는다
    assert r.bundle_path is None and r.files == ()
    assert numeric(r.reason_args)


def test_budget_seconds_overrides_the_policy_timeout(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {f"goldens/{i:03d}.png": b"x" for i in range(40)})
    clock = Clock(0.0, 9.0)
    r = mod.collect(
        ws,
        policy(core, "goldens/*.png", timeout_seconds=3600),
        staging(tmp_path),
        clock=clock,
        budget_seconds=5,
    )
    assert r.state == core.DROPPED and r.reason_code == "timed_out"


def test_a_budget_that_is_not_blown_collects_normally(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"A", "goldens/b.png": b"B"})
    clock = Clock(0.0, 1.0, 2.0, 3.0)
    r = mod.collect(
        ws, policy(core, "goldens/*.png", timeout_seconds=60), staging(tmp_path), clock=clock
    )
    assert r.state == core.READY and paths(r) == ["goldens/a.png", "goldens/b.png"]


@pytest.mark.parametrize(("timeout", "jump"), [(1, 5.0), (5, 6.0), (60, 61.0)])
def test_the_policy_timeout_is_the_budget_when_none_is_passed(
    tmp_path: Path, timeout: int, jump: float
) -> None:
    """예산은 `policy.timeout_seconds` 다 — 60초가 코드에 박혀 있으면 안 된다."""
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {f"goldens/{i:03d}.png": b"x" for i in range(20)})
    r = mod.collect(
        ws,
        policy(core, "goldens/*.png", timeout_seconds=timeout),
        staging(tmp_path),
        clock=Clock(0.0, jump),
    )
    # 산출물만 버린다 — `timed_out` 은 이유 코드지 산출물 상태가 아니다(§5)
    assert (r.state, r.reason_code) == (core.DROPPED, "timed_out")


# ── collect — 없는 워크스페이스 · 맞는 것이 없음 ────────────────────────────


def test_a_missing_workspace_is_skipped_and_not_run(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = tmp_path / "gone"
    assert not ws.exists()
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.state == core.SKIPPED and r.reason_code == "not_run"
    assert r.files == () and r.total_bytes == 0
    assert r.bundle_path is None and r.bundle_sha256 is None


def test_no_match_is_empty_not_ready(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"README.md": b"x", "test/goldens/a.txt": b"x"})
    r = mod.collect(ws, policy(core, "test/**/goldens/*.png"), staging(tmp_path))
    assert r.state == core.EMPTY and r.reason_code == "no_match"
    assert r.files == () and r.total_bytes == 0
    assert r.bundle_path is None and r.bundle_sha256 is None


def test_an_empty_workspace_is_empty_not_skipped(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    r = mod.collect(workspace(tmp_path), policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.state == core.EMPTY and r.reason_code == "no_match"


# ── collect — 정규화 충돌 ───────────────────────────────────────────────────


def test_a_normalisation_collision_drops_the_whole_bundle(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    if folding_filesystem(tmp_path):
        pytest.skip("이 파일 시스템은 이름을 접는다(macOS APFS) — 충돌하는 두 파일을 못 만든다")
    ws = workspace(
        tmp_path, {"goldens/A.png": b"AAA", "goldens/a.png": b"BBB", "goldens/c.png": b"C"}
    )
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert r.state == core.DROPPED and r.reason_code == "path_conflict"
    assert r.bundle_path is None and r.files == ()  # 「충돌 빼고 전부」는 없다 — 묶음째 버린다
    assert "A.png" not in repr(r.reason_args)  # 경로는 공개 인자에 안 들어간다(§3)


def test_a_directory_prefix_collision_drops_the_whole_bundle(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    if folding_filesystem(tmp_path):
        pytest.skip("이 파일 시스템은 이름을 접는다(macOS APFS) — 충돌하는 두 파일을 못 만든다")
    ws = workspace(tmp_path, {"out/A": b"1", "out/a/b.png": b"2"})
    r = mod.collect(ws, policy(core, "out/**"), staging(tmp_path))
    assert r.state == core.DROPPED and r.reason_code == "path_conflict"
    assert r.bundle_path is None and r.files == ()


# ── reason_args 에는 경로가 없다 · detail 에만 있다 (§3) ────────────────────


def test_reason_args_never_carry_a_path(tmp_path: Path) -> None:
    """거부된 파일 이름은 그 자체로 남의 트리 구조다 — 수치만 공개한다."""
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/secret-project-name.png": b"x" * 300})
    r = mod.collect(ws, policy(core, "goldens/*.png", max_bytes=100), staging(tmp_path))
    assert r.state == core.DROPPED
    assert "secret-project-name" not in repr(r.reason_args)
    assert "goldens" not in repr(r.reason_args)
    assert numeric(r.reason_args)


def test_detail_may_carry_paths_and_both_fields_survive_json(tmp_path: Path) -> None:
    # detail 은 보호 라우트 전용이라 경로를 담아도 된다. 둘 다 DB 에 JSON 으로 들어간다(§9)
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"x" * 300})
    r = mod.collect(ws, policy(core, "goldens/*.png", max_bytes=100), staging(tmp_path))
    assert r.detail is None or isinstance(r.detail, dict)
    json.dumps(r.reason_args)
    json.dumps(r.detail)


# ── CollectResult 의 모양 ───────────────────────────────────────────────────


def test_collect_result_defaults_and_frozen() -> None:
    mod, core = collector(), artifacts_core()
    r = mod.CollectResult(state=core.EMPTY)
    assert r.state == core.EMPTY
    assert (r.files, r.total_bytes, r.bundle_bytes, r.skipped_count) == ((), 0, 0, 0)
    assert r.bundle_sha256 is None and r.bundle_path is None
    assert r.reason_code is None and r.reason_args is None and r.detail is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.state = core.READY


def test_collect_returns_a_collect_result_with_a_tuple_of_bundle_files(tmp_path: Path) -> None:
    mod, core = collector(), artifacts_core()
    ws = workspace(tmp_path, {"goldens/a.png": b"AAA"})
    r = mod.collect(ws, policy(core, "goldens/*.png"), staging(tmp_path))
    assert isinstance(r, mod.CollectResult)
    assert isinstance(r.files, tuple)
    assert all(isinstance(f, core.BundleFile) for f in r.files)
    assert isinstance(r.bundle_path, Path)
