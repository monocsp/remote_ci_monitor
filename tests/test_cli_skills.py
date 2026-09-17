"""`rcm skills list` · `rcm skills install --into DIR [--force]` — 패키지의 연결 스킬을 프로젝트에.

스킬 트리는 가짜(tmp)를 `cli.skills_root` 에 끼워 쓴다 — 진짜 `skills/` 가 비어 있든 채워져
있든 같은 결과다. 마지막 시험만 진짜 패키지 트리를 본다(있을 때).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor.cli import main

SKILL_MD = """\
---
name: {name}
description: {desc}
---

# {name}
"""


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture
def fake_skills(tmp_path, monkeypatch) -> Path:
    """`skills/` 흉내: 스킬 둘, 하나는 templates/ 하위 파일까지. 숨김 · __pycache__ 는 뺀다."""
    root = tmp_path / "pkg-skills"
    a = root / "rcm-alpha-connect"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text(
        SKILL_MD.format(name="rcm-alpha-connect", desc="Wire the alpha role. Use it first.")
    )
    (a / "templates").mkdir()
    (a / "templates" / "helper.py").write_text("print('alpha')\n")
    (a / "templates" / "__pycache__").mkdir()
    (a / "templates" / "__pycache__" / "helper.cpython-311.pyc").write_bytes(b"\x00")
    b = root / "rcm-beta-connect"
    b.mkdir()
    (b / "SKILL.md").write_text(SKILL_MD.format(name="rcm-beta-connect", desc="Wire beta."))
    (root / ".DS_Store").write_bytes(b"junk")
    (root / "__pycache__").mkdir()
    monkeypatch.setattr(cli, "skills_root", lambda: root)
    return root


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    return p


def installed(project: Path) -> dict[str, bytes]:
    base = project / ".claude" / "skills"
    return {
        f.relative_to(base).as_posix(): f.read_bytes()
        for f in sorted(base.rglob("*"))
        if f.is_file()
    }


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_prints_one_line_per_skill_with_the_first_sentence(fake_skills, capsys):
    code, out, err = run(capsys, ["skills", "list"])
    assert code == 0, err
    assert out.splitlines() == [
        "rcm-alpha-connect        Wire the alpha role.",
        "rcm-beta-connect         Wire beta.",
    ]


def test_list_says_so_when_the_build_has_no_skills(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "skills_root", lambda: None)
    code, out, err = run(capsys, ["skills", "list"])
    assert code == 1 and out == ""
    assert "no skills are packaged in this build" in err


# ── install ──────────────────────────────────────────────────────────────────


def test_install_copies_every_skill_folder_and_prints_one_line_per_file(
    fake_skills, project, capsys
):
    code, out, err = run(capsys, ["skills", "install", "--into", str(project)])
    assert code == 0, err
    assert out.splitlines() == [
        "written  .claude/skills/rcm-alpha-connect/SKILL.md",
        "written  .claude/skills/rcm-alpha-connect/templates/helper.py",
        "written  .claude/skills/rcm-beta-connect/SKILL.md",
    ]
    assert installed(project) == {
        "rcm-alpha-connect/SKILL.md": (fake_skills / "rcm-alpha-connect" / "SKILL.md").read_bytes(),
        "rcm-alpha-connect/templates/helper.py": b"print('alpha')\n",
        "rcm-beta-connect/SKILL.md": (fake_skills / "rcm-beta-connect" / "SKILL.md").read_bytes(),
    }
    # 프로젝트의 다른 것은 건드리지 않는다
    assert sorted(p.name for p in project.iterdir()) == [".claude"]


def test_second_install_keeps_identical_files(fake_skills, project, capsys):
    run(capsys, ["skills", "install", "--into", str(project)])
    before = installed(project)
    code, out, err = run(capsys, ["skills", "install", "--into", str(project)])
    assert code == 0, err
    assert all(ln.startswith("kept     .claude/skills/") for ln in out.splitlines()), out
    assert len(out.splitlines()) == 3
    assert installed(project) == before


def test_install_refuses_to_overwrite_a_changed_file_without_force(fake_skills, project, capsys):
    run(capsys, ["skills", "install", "--into", str(project)])
    edited = project / ".claude" / "skills" / "rcm-alpha-connect" / "SKILL.md"
    edited.write_text("# my own version\n")
    code, out, err = run(capsys, ["skills", "install", "--into", str(project)])
    assert code == 1, out + err
    lines = out.splitlines()
    assert lines[0] == (
        "skipped  .claude/skills/rcm-alpha-connect/SKILL.md (differs; use --force to overwrite)"
    )
    assert lines[1:] == [
        "kept     .claude/skills/rcm-alpha-connect/templates/helper.py",
        "kept     .claude/skills/rcm-beta-connect/SKILL.md",
    ]
    assert "1 file(s) differ" in err and "--force" in err
    assert edited.read_text() == "# my own version\n"  # 손댄 파일은 그대로다


def test_install_force_overwrites_the_changed_file(fake_skills, project, capsys):
    run(capsys, ["skills", "install", "--into", str(project)])
    edited = project / ".claude" / "skills" / "rcm-alpha-connect" / "SKILL.md"
    edited.write_text("# my own version\n")
    code, out, err = run(capsys, ["skills", "install", "--into", str(project), "--force"])
    assert code == 0, err
    assert out.splitlines()[0] == "written  .claude/skills/rcm-alpha-connect/SKILL.md"
    assert edited.read_bytes() == (fake_skills / "rcm-alpha-connect" / "SKILL.md").read_bytes()


def test_install_needs_an_existing_project_directory(fake_skills, tmp_path, capsys):
    code, out, err = run(capsys, ["skills", "install", "--into", str(tmp_path / "nope")])
    assert code == 2 and out == ""
    assert "--into must be an existing project directory" in err


def test_install_with_no_packaged_skills_fails_plainly(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "skills_root", lambda: None)
    code, out, err = run(capsys, ["skills", "install", "--into", str(project)])
    assert code == 1 and out == ""
    assert "no skills are packaged in this build" in err
    assert not (project / ".claude").exists()


# ── 진짜 패키지 트리 ──────────────────────────────────────────────────────────


def test_real_packaged_skills_install_cleanly_when_present(project, capsys):
    """진짜 `skills/` 가 있으면: 모든 폴더가 `.claude/skills/<name>/` 로 가고, 이름은 rcm- 로
    시작하며, 두 번째 설치는 전부 kept 다. 없으면 건너뛴다."""
    root = cli.skills_root()
    if root is None or not cli._skill_dirs(root):
        pytest.skip("this build packages no skills")
    code, out, err = run(capsys, ["skills", "install", "--into", str(project)])
    assert code == 0, err
    names = sorted(p.name for p in (project / ".claude" / "skills").iterdir())
    assert names == [d.name for d in cli._skill_dirs(root)]
    assert all(re.match(r"^rcm-[a-z0-9-]+$", n) for n in names), names
    assert not list((project / ".claude").rglob("__pycache__"))
    code, out, err = run(capsys, ["skills", "install", "--into", str(project)])
    assert code == 0 and all(ln.startswith("kept     ") for ln in out.splitlines()), out
