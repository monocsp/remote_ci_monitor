"""M5r K: `rcm-ci-connect` 스킬이 패키지와 클라이언트 wheel 에 실린다(명세 §5.1 · §6 K).

wheel 이 패키지 폴더의 파일을 전부 싣는지는 `test_client_wheel.py` 의 S1 이 이미 본다. 여기서는
§5.1 이 정한 파일 이름이 하나도 빠지지 않았는지, `rcm skills list` 가 설명과 함께 보이는지,
`install` 이 템플릿을 쓰는지, 세 스크립트가 격리 모드(`python -I`)에서도 같은 폴더의 설정 모듈을
찾는지를 본다.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
import zipfile

import pytest

from ci_fakes import TEMPLATES
from remote_ci_monitor import __version__, cli
from remote_ci_monitor.cli import main
from remote_ci_monitor.clientwheel import build_wheel

SKILL = TEMPLATES.parent
#: §5.1 의 폴더 그대로
FILES = (
    "SKILL.md",
    "templates/rcm_ci_config.py",
    "templates/rcm_ci_wait.py",
    "templates/rcm_ci_watch.py",
    "templates/rcm_ci_status.py",
    "templates/ci.toml",
    "templates/ci.yml",
    "templates/checks.yml",
    "templates/ci-watch.toml",
    "templates/presets.ci.toml",
    "templates/rcm_ci_job.sh",
    "templates/com.remote-ci-monitor.ci-watch.plist",
)


def run(capsys, argv):
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_k1_every_file_of_the_skill_is_in_the_client_wheel():
    zf = zipfile.ZipFile(io.BytesIO(build_wheel(__version__)))
    shipped = set(zf.namelist())
    for rel in FILES:
        name = f"remote_ci_monitor/skills/rcm-ci-connect/{rel}"
        assert name in shipped, name
        assert zf.read(name) == (SKILL / rel).read_bytes(), name


def test_k1_skills_list_shows_it_with_a_description(capsys):
    code, out, err = run(capsys, ["skills", "list"])
    assert code == 0, err
    rows = {ln.split(None, 1)[0]: ln.split(None, 1)[1] for ln in out.splitlines() if ln.strip()}
    assert "rcm-ci-connect" in rows
    assert rows["rcm-ci-connect"].strip()


def test_k1_install_writes_the_templates(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    code, out, err = run(capsys, ["skills", "install", "--into", str(project)])
    assert code == 0, err
    base = project / ".claude" / "skills" / "rcm-ci-connect"
    for rel in FILES:
        assert (base / rel).read_bytes() == (SKILL / rel).read_bytes(), rel
    assert cli.skills_root() is not None


def test_the_skill_frontmatter_names_its_folder():
    text = (SKILL / "SKILL.md").read_text()
    assert text.startswith("---\n")
    head = text.split("---\n", 2)[1]
    assert re.search(r"^name: rcm-ci-connect$", head, re.M)
    assert re.search(r"^description: \S", head, re.M)


@pytest.mark.parametrize("script", ["rcm_ci_wait.py", "rcm_ci_watch.py", "rcm_ci_status.py"])
def test_each_script_finds_its_config_module_in_isolated_mode(tmp_path, script):
    """빌드 머신에서는 `~/.config/rcm/scripts/` 에, 레포에서는 `scripts/ci/` 에 복사돼 돈다.
    `-I` 는 스크립트 폴더를 `sys.path` 에 넣지 않으므로 스크립트가 스스로 넣어야 한다."""
    proc = subprocess.run(
        [sys.executable, "-I", str(TEMPLATES / script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout.lower()


def test_the_templates_use_only_the_standard_library():
    allowed_local = {"rcm_ci_config"}
    for path in TEMPLATES.glob("*.py"):
        for m in re.finditer(r"^(?:from|import) ([A-Za-z_][\w]*)", path.read_text(), re.M):
            mod = m.group(1)
            assert mod in sys.stdlib_module_names or mod in allowed_local or mod == "__future__", (
                path.name,
                mod,
            )


def test_the_job_script_head_is_valid_bash():
    path = TEMPLATES / "rcm_ci_job.sh"
    proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "set -euo pipefail" in path.read_text()
