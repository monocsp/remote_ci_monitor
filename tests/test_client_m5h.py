"""클라이언트(M5h) — tree 잡이 브랜치를 실어 보낸다. 그리고 그것이 **못 건드리는** 두 가지.

명세는 `docs/m5h-implementation.md` §4.2. **구현보다 먼저 썼다(test-first).**

브랜치는 **표시용**이다(결정 71). 잠그는 불변식은 둘이다:

1. `tree_hash` 는 `(경로, 모드, 내용 sha256)` 목록만으로 만든다 — 같은 트리를 다른 브랜치에서
   올린 두 세션은 **같은 해시**를 낸다.
2. `join_key(preset, inputs, source.identity)` 도 그대로다 — 그 두 세션은 **지금처럼 합류한다**.

git 은 `tests/gitrepo.py` 의 `git()`·`commit()` 으로만 부른다(전역 `~/.gitconfig` 를 안 탄다).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gitrepo import commit, git
from remote_ci_monitor.client import make_snapshot
from remote_ci_monitor.core.model import Source
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.core.status import source_json


@pytest.fixture
def repo(tmp_path) -> Path:
    """커밋 하나짜리 체크아웃. 기본 브랜치는 `main`(gitrepo 가 그렇게 초기화한다)."""
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", cwd=root)
    commit(root, "hello.txt", "hello\n", "first")
    return root


def snapshot(root: Path, tmp_path: Path):
    return make_snapshot(root, tar_dir=tmp_path)


# ── I. 스냅샷의 브랜치 (§4.2) ────────────────────────────────────────────────


def test_a_checkout_snapshot_carries_its_branch(repo, tmp_path):
    """`git rev-parse --abbrev-ref HEAD` 하나 — 목록이 「어느 코드였나」를 말할 재료다."""
    snap = snapshot(repo, tmp_path)
    assert snap.branch == "main"
    assert snap.base_sha  # 커밋 정보는 오늘 그대로 함께 온다


def test_a_detached_head_has_no_branch(repo, tmp_path):
    """detached 면 git 이 `HEAD` 라는 **문자열**을 준다 — 그것을 브랜치 이름으로 싣지 않는다."""
    git("checkout", "-q", "--detach", cwd=repo)
    assert snapshot(repo, tmp_path).branch is None


def test_a_directory_that_is_not_a_checkout_has_no_branch(tmp_path):
    """git 이 아닌 트리도 잡을 낼 수 있다 — 모르는 값은 null 이다."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    snap = snapshot(root, tmp_path)
    assert snap.branch is None and snap.base_sha is None


def test_the_branch_never_changes_the_tree_hash(repo, tmp_path):
    """**잠근 불변식 ①** — 같은 트리, 다른 브랜치 이름이면 같은 해시다."""
    on_main = snapshot(repo, tmp_path)
    git("checkout", "-q", "-b", "feature/x", cwd=repo)
    on_feature = snapshot(repo, tmp_path)
    assert on_main.branch == "main" and on_feature.branch == "feature/x"
    assert on_feature.tree_hash == on_main.tree_hash
    assert on_feature.base_sha == on_main.base_sha  # 커밋도 그대로다


def test_two_branches_with_the_same_tree_still_join_the_same_job(repo, tmp_path):
    """**잠근 불변식 ②** — 합류 신원에 브랜치가 안 들어간다(§4.2 · 결정 71)."""
    on_main = snapshot(repo, tmp_path)
    git("checkout", "-q", "-b", "feature/x", cwd=repo)
    on_feature = snapshot(repo, tmp_path)
    a = Source(mode="tree", tree_hash=on_main.tree_hash, branch=on_main.branch)
    b = Source(mode="tree", tree_hash=on_feature.tree_hash, branch=on_feature.branch)
    assert a.identity == b.identity == on_main.tree_hash
    assert join_key("gate", {"scope": "full"}, a.identity) == join_key(
        "gate", {"scope": "full"}, b.identity
    )


def test_the_tree_source_document_carries_the_branch_and_nulls_it_for_old_jobs(repo, tmp_path):
    """`source_json` 은 DB 의 JSON 을 그대로 읽는다 — 마이그레이션이 없고 옛 잡은 null 이다."""
    doc = source_json(Source(mode="tree", tree_hash="9f" * 32, branch="chore/ci-guard"))
    assert doc["branch"] == "chore/ci-guard"
    old = source_json(Source(mode="tree", tree_hash="9f" * 32))
    assert old["branch"] is None


def test_a_git_ref_source_document_has_no_branch_field():
    """git_ref 잡의 코드 신원은 `ref` 다 — 같은 뜻의 칸을 둘 두지 않는다."""
    doc = source_json(Source(mode="git_ref", repo="org/app", ref="main", sha="25e1494"))
    assert "branch" not in doc
