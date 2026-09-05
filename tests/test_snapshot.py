"""스냅샷 규칙 — .rcmignore(gitignore 문법) · 파일 선택(삭제·.git·무시) · tree_hash."""

from remote_ci_monitor.core.snapshot import (
    is_ignored,
    normalize_mode,
    parse_ignore,
    select_files,
    tree_hash,
)

RULES = parse_ignore(
    """
# build outputs
build/
*.log
/secrets.env
docs/**/draft*
!keep.log
node_modules
"""
)


def test_ignore_basic_patterns():
    assert is_ignored("build/app.bin", RULES)
    assert is_ignored("sub/build/x", RULES)  # 디렉터리 패턴은 어느 깊이든
    assert not is_ignored("build", RULES)  # 파일 이름이 build 인 파일은 dir-only 에 안 걸린다
    assert is_ignored("build", RULES, is_dir=True)
    assert is_ignored("a/b/c.log", RULES)
    assert is_ignored("secrets.env", RULES)
    assert not is_ignored("sub/secrets.env", RULES)  # 앞에 / 가 있으면 루트 고정
    assert is_ignored("docs/draft1.md", RULES)
    assert is_ignored("docs/x/y/draft-2.md", RULES)
    assert not is_ignored("docs/final.md", RULES)
    assert not is_ignored("keep.log", RULES)  # 되살림
    assert is_ignored("node_modules/x/y.js", RULES)


def test_negation_cannot_revive_inside_ignored_dir():
    rules = parse_ignore("out/\n!out/keep.txt\n")
    assert is_ignored("out/keep.txt", rules)


def test_select_files_drops_deleted_git_and_ignored():
    candidates = [
        "src/a.py",
        ".git/config",
        "deleted.py",
        "build/x.o",
        "a/.git/HEAD",
        "keep.log",
        "x.log",
    ]
    present = {"src/a.py", ".git/config", "build/x.o", "a/.git/HEAD", "keep.log", "x.log"}
    got = select_files(candidates, rules=RULES, present=lambda p: p in present)
    assert got == ["keep.log", "src/a.py"]


def test_select_files_without_rules_keeps_everything_present():
    assert select_files(["b", "a", "a"], present=lambda p: True) == ["a", "b"]


def test_tree_hash_is_order_independent_but_content_mode_path_sensitive():
    base = [("a.py", 0o100644, "aa"), ("b.sh", 0o100755, "bb")]
    assert tree_hash(base) == tree_hash(reversed(base))
    assert tree_hash(base) != tree_hash([("a.py", 0o100644, "aa"), ("b.sh", 0o100644, "bb")])
    assert tree_hash(base) != tree_hash([("a.py", 0o100644, "ax"), ("b.sh", 0o100755, "bb")])
    assert tree_hash(base) != tree_hash([("a.py", 0o100644, "aa"), ("c.sh", 0o100755, "bb")])
    assert len(tree_hash(base)) == 64


def test_normalize_mode():
    assert normalize_mode(0o100644, is_symlink=False) == 0o100644
    assert normalize_mode(0o100755, is_symlink=False) == 0o100755
    assert normalize_mode(0o100600, is_symlink=False) == 0o100644
    assert normalize_mode(0o120777, is_symlink=True) == 0o120000
