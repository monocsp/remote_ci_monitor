"""릴리스·배포 파일 문면 잠금 (M4 명세 §3~§6).

release.yml · ci.yml · smoke_install.sh · Dockerfile · README · CHANGELOG · LICENSE.
YAML 파서는 없다(표준 라이브러리만) — 정규식과 줄 스캔으로 문면만 본다. 워크플로·이미지는
돌리지 않는다. 셸 스크립트는 `bash -n` 까지만. 아직 없는 파일은 `missing:` 으로 빨갛다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
CI = WORKFLOWS / "ci.yml"
SMOKE = ROOT / "scripts" / "smoke_install.sh"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
LICENSE = ROOT / "LICENSE"
PYPROJECT = ROOT / "pyproject.toml"
CI_GATE = ROOT / "examples" / "session" / "ci-gate.sh"
PLIST = ROOT / "examples" / "launchd" / "com.remote-ci-monitor.server.plist"
UNIT = ROOT / "examples" / "systemd" / "rcm-server.service"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")


# ── 도우미 ───────────────────────────────────────────────────────────────────


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def read(path: Path) -> str:
    """파일 문면. 없으면 import 오류가 아니라 이 assert 로 빨갛다."""
    assert path.is_file(), f"missing: {rel(path)}"
    return path.read_text()


def has(text: str, pattern: str, flags: int = re.M) -> bool:
    return re.search(pattern, text, flags) is not None


def job_block(text: str, job: str) -> str:
    """`jobs:` 아래 `<job>:` 부터 같은 들여쓰기의 다음 키 직전까지(들여쓰기로 자른다)."""
    jobs = re.search(r"^jobs:\s*$", text, re.M)
    assert jobs, "no top-level `jobs:`"
    body = text[jobs.end() :]
    m = re.search(rf"^(?P<indent>[ \t]+){re.escape(job)}:[ \t]*(#.*)?$", body, re.M)
    assert m, f"no job named {job!r}"
    rest = body[m.end() :]
    nxt = re.search(rf"^{m.group('indent')}[A-Za-z_][\w-]*:", rest, re.M)
    return body[m.start() : m.end() + (nxt.start() if nxt else len(rest))]


def job_head(block: str) -> str:
    """잡 블록의 `steps:` 앞부분 — needs · if · permissions · environment 는 잡 수준이어야 한다."""
    return block.split("steps:", 1)[0]


def needs_of(block: str) -> set[str]:
    """`needs:` 를 flow(`[a, b]`) · 스칼라(`a`) · 블록(`- a`) 세 꼴 모두 집합으로."""
    m = re.search(r"^[ \t]*needs:[ \t]*(?P<val>.*)$", block, re.M)
    assert m, "no `needs:`"
    val = m.group("val").split("#", 1)[0].strip()
    if val.startswith("["):
        items = val.strip("[]").split(",")
    elif val:
        items = [val]
    else:
        items = []
        for line in block[m.end() :].lstrip("\n").splitlines():
            lm = re.match(r"^[ \t]*-[ \t]*([^\s#]+)", line)
            if not lm:
                break
            items.append(lm.group(1))
    return {i.strip().strip("'\"") for i in items if i.strip()}


def heading(text: str, prefix: str) -> re.Match[str] | None:
    """`## <prefix>` 로 시작하는 2단계 제목. 뒤에 붙는 말(`(3 commands)`)은 허용, 대소문자 무시."""
    return re.search(rf"^##[ \t]+{re.escape(prefix)}\b[^\n]*$", text, re.M | re.I)


def section(text: str, prefix: str) -> str:
    """그 제목부터 다음 `## ` 제목 직전까지(`###` 하위 절 포함)."""
    m = heading(text, prefix)
    assert m, f"README has no '## {prefix}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


# ── release.yml (§4) ─────────────────────────────────────────────────────────


def test_release_triggers_on_v_tags():
    text = read(RELEASE)
    assert has(text, r"^on:")
    assert has(text, r"^\s*push:")
    # `tags: ["v*"]` 든 블록 목록 `- "v*"` 든 따옴표 유무와 무관하게
    assert has(text, r"^\s*tags:\s*(\[\s*['\"]?v\*|\n\s*-\s*['\"]?v\*)"), "no `push.tags: v*`"


def test_release_build_requires_tag_on_main():
    build = job_block(read(RELEASE), "build")
    assert has(build, r"fetch-depth:\s*0")  # merge-base 는 전체 이력이 있어야 한다
    assert has(build, r"git merge-base --is-ancestor")
    assert "origin/main" in build
    assert has(build, r"release tags must point at[^\n]*main")  # 명세 §4 의 실패 문구


def test_release_build_checks_version_matches_tag():
    build = job_block(read(RELEASE), "build")
    assert "__version__" in build  # 단일 출처: remote_ci_monitor.__version__
    assert has(build, r"GITHUB_REF_NAME|github\.ref_name|GITHUB_REF\b|github\.ref\b"), "no tag"


def test_release_build_builds_checks_and_uploads_dist():
    build = job_block(read(RELEASE), "build")
    assert "python -m build" in build
    assert "twine check" in build
    assert "actions/upload-artifact" in build
    assert has(build, r"^\s*(name|path):\s*['\"]?dist"), "artifact must be the `dist` directory"


def test_release_smoke_matrix_installs_wheel_on_both_os():
    smoke = job_block(read(RELEASE), "smoke")
    assert "build" in needs_of(job_head(smoke))
    assert "matrix" in job_head(smoke)
    assert "ubuntu-latest" in smoke and "macos-latest" in smoke
    assert "actions/download-artifact" in smoke
    assert "scripts/smoke_install.sh" in smoke
    assert has(smoke, r"smoke_install\.sh[^\n]*dist/"), "the script must get the built wheel"


def test_release_github_release_job():
    gh = job_block(read(RELEASE), "github-release")
    head = job_head(gh)
    assert "smoke" in needs_of(head)
    assert has(head, r"^\s*permissions:") and has(head, r"^\s*contents:\s*write")  # 잡 수준
    assert "actions/download-artifact" in gh
    assert "gh release" in gh
    assert has(gh, r"gh release[^\n]*dist/"), "wheel · sdist must be attached"


def test_release_github_release_notes_and_rerun():
    gh = job_block(read(RELEASE), "github-release")
    assert "CHANGELOG" in gh  # 릴리스 노트 = CHANGELOG 의 해당 절
    assert "--clobber" in gh  # 이미 있는 릴리스면 실패 대신 업로드만


def test_release_pypi_job_is_gated_by_repository_variable():
    pypi = job_block(read(RELEASE), "pypi")
    head = job_head(pypi)
    assert "smoke" in needs_of(head)
    assert has(head, r"^\s*if:[^\n]*vars\.PYPI_PUBLISH[^\n]*'true'"), "not gated by PYPI_PUBLISH"
    assert has(head, r"^\s*environment:\s*(pypi\s*$|\n\s*name:\s*pypi)"), "no `environment: pypi`"
    assert has(head, r"^\s*id-token:\s*write")  # trusted publishing 은 OIDC 토큰
    assert "actions/download-artifact" in pypi
    assert "pypa/gh-action-pypi-publish" in pypi


# ── ci.yml (§3) ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("job", ["unit", "secrets", "test"])
def test_ci_keeps_existing_job_names(job: str):
    # 룰셋 필수 체크는 잡 이름 `test` 로 잡혀 있다(PLAN 「브랜치 정책」). matrix 잡은 `unit` 그대로.
    block = job_block(read(CI), job)
    assert has(job_head(block), rf"^\s*name:\s*{job}\s*$"), f"`name: {job}` gone"


def test_ci_smoke_job_builds_wheel_and_runs_script_on_both_os():
    smoke = job_block(read(CI), "smoke")
    assert "matrix" in job_head(smoke)
    assert "ubuntu-latest" in smoke and "macos-latest" in smoke
    assert has(smoke, r"python -m build|pip wheel"), "no wheel build step"
    assert "scripts/smoke_install.sh" in smoke


def test_ci_test_job_aggregates_unit_secrets_and_smoke():
    test = job_block(read(CI), "test")
    assert has(job_head(test), r"^\s*if:\s*always\(\)")  # 실패한 잡도 집계에 잡히도록
    assert {"unit", "secrets", "smoke"} <= needs_of(job_head(test))
    for job in ("unit", "secrets", "smoke"):
        assert f"needs.{job}.result" in test, f"`test` does not look at needs.{job}.result"


# ── scripts/smoke_install.sh (§3) ────────────────────────────────────────────

# README 5분 절차의 rcm 명령 전부 + `rcm top`·`rcm jobs --json` (명세 §3.3)
RCM_COMMANDS = [
    "version",
    "init server",
    "token add",
    "serve",
    "init client",
    "check",
    "run",
    "top",
    "jobs",
]
# 정규식. 필요한 도구는 python3 · curl 뿐(명세 §3). 데이터 디렉터리 격리는 HOME 덮어쓰기로 한다
SMOKE_PATTERNS = [
    r"\bpython3\b",
    r"\bcurl\b",
    r"-m venv",
    r"pip install",
    r"/api/health",
    r"RCM_TOKEN",
    r"succeeded",  # `rcm run ok` 의 JSON state
    r"rcm queue",  # `GET /` 의 <title>
    r"kill -TERM|kill -15|SIGTERM",  # 서버는 SIGTERM 으로 끝낸다(§3.5)
    r"smoke: ok",
]


@pytest.mark.parametrize("script", [SMOKE, CI_GATE], ids=rel)
def test_shell_scripts_are_executable_bash(script: Path):
    text = read(script)
    assert os.access(script, os.X_OK), f"{rel(script)} is not executable"
    assert text.splitlines()[0] in ("#!/usr/bin/env bash", "#!/bin/bash")


@needs_bash
@pytest.mark.parametrize("script", [SMOKE, CI_GATE], ids=rel)
def test_shell_scripts_parse(script: Path):
    read(script)
    proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_smoke_script_is_strict_and_cleans_up():
    text = read(SMOKE)
    assert has(text, r"^\s*set -euo pipefail")
    assert has(text, r"^\s*trap\s+[^\n]*\bEXIT\b"), "no `trap … EXIT` (tmp dir · server cleanup)"
    assert has(text, r"\b(HOME|XDG_CONFIG_HOME)="), "rcm init must not touch the real ~/.config/rcm"


@pytest.mark.parametrize("cmd", RCM_COMMANDS)
def test_smoke_script_runs_each_readme_command(cmd: str):
    # `rcm run` 도 `"$venv/bin/rcm" run` 도 받는다
    pattern = r"\brcm[\"']?\s+" + r"\s+".join(re.escape(w) for w in cmd.split())
    assert has(read(SMOKE), pattern), f"smoke script never runs `rcm {cmd}`"


@pytest.mark.parametrize("pattern", SMOKE_PATTERNS)
def test_smoke_script_mentions(pattern: str):
    assert has(read(SMOKE), pattern), f"smoke script lacks /{pattern}/"


def test_readme_marked_commands_are_all_in_smoke_script():
    """README 의 `<!-- smoke:begin/end -->` 블록 안 `rcm …` 명령은 전부 스크립트에 있어야 한다.

    스크립트 0단계가 같은 검사를 하지만 pytest 에서 먼저 잡는다(README 가 바뀌면 스크립트도 바뀐다).
    """
    script = read(SMOKE)
    if "smoke:begin" not in script:
        pytest.skip("smoke script does not use README markers")
    blocks = re.findall(r"<!-- smoke:begin -->(.*?)<!-- smoke:end -->", read(README), re.S)
    assert blocks, "README has no <!-- smoke:begin --> … <!-- smoke:end --> block"
    cmds: set[str] = set()
    for block in blocks:
        for raw in block.splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith("rcm "):
                cmds.add(" ".join(line.split()[:3]))  # 스크립트와 같은 규칙: 앞 세 단어
    assert cmds, "marked README blocks contain no `rcm …` command"
    missing = sorted(c for c in cmds if c not in script)
    assert not missing, f"README commands not in smoke script: {missing}"


def test_smoke_script_runs_on_macos_default_bash():
    text = read(SMOKE)
    assert "declare -A" not in text  # bash 3.2 엔 연관 배열이 없다
    assert not has(text, r"^\s*(mapfile|readarray)\b")  # bash 4+
    # `timeout` 명령은 macOS 에 없다. `--timeout` 플래그는 무관하므로 줄 머리만 본다
    assert not has(text, r"^\s*timeout\s"), "`timeout` is not on macOS — poll with a deadline"


# ── Dockerfile · .dockerignore (§5) ──────────────────────────────────────────


def dockerfile_flat() -> str:
    """`\\` 줄이음을 붙여서 RUN 한 줄이 한 줄로 보이게."""
    return re.sub(r"\\\s*\n", " ", read(DOCKERFILE))


def test_dockerfile_base_image_is_python_311_or_newer():
    assert has(read(DOCKERFILE), r"^FROM\s+python:3\.(1[1-9]|[2-9]\d)"), "FROM python:3.11+"


def test_dockerfile_installs_git_without_recommends():
    runs = [ln for ln in dockerfile_flat().splitlines() if ln.startswith("RUN") and "apt-get" in ln]
    assert runs, "no `RUN apt-get …`"
    apt = " ".join(runs)
    assert "--no-install-recommends" in apt
    assert has(apt, r"\bgit\b"), "git_ref deploys need git in the image"


def test_dockerfile_runs_as_non_root_rcm():
    flat = dockerfile_flat()  # useradd 는 apt-get RUN 의 이어진 줄에 있을 수 있다
    assert has(flat, r"^RUN[^\n]*\b(useradd|adduser)\b[^\n]*\brcm\b"), "user `rcm` not created"
    assert has(flat, r"^USER\s+rcm\s*$")
    assert "pip install" in flat


def test_dockerfile_entrypoint_and_serve_cmd():
    flat = dockerfile_flat()
    assert has(flat, r'^ENTRYPOINT\s+\[\s*"rcm"\s*\]')
    cmd = re.search(r"^CMD\s+(.*)$", flat, re.M)
    assert cmd, "no CMD"
    for part in ("serve", "--bind", "0.0.0.0", "--data-dir", "/data"):
        assert part in cmd.group(1), f"CMD lacks {part}"


def test_dockerfile_exposes_port_and_data_volume():
    text = read(DOCKERFILE)
    assert has(text, r"^EXPOSE\s+8787\b")
    assert has(text, r"^VOLUME\s+[^\n]*/data\b")


@pytest.mark.parametrize("entry", [".git", "tests", ".venv"])
def test_dockerignore_excludes(entry: str):
    assert has(read(DOCKERIGNORE), rf"^{re.escape(entry)}(/|/?\*\*?)?\s*$"), f"lacks {entry}"


# ── README (§6) ──────────────────────────────────────────────────────────────

README_ORDER = ["Install", "Build machine", "Session machine", "Docker", "Releasing", "Development"]
README_MENTIONS = [
    ("Install", r"pipx install remote-ci-monitor"),
    ("Install", r"\buvx\b"),
    ("Install", r"git\+https://github\.com/monocsp/remote_ci_monitor"),
    ("Build machine", r"rcm init server"),
    ("Build machine", r"rcm token add"),
    ("Build machine", r"rcm serve"),
    ("Session machine", r"rcm init client --server"),
    ("Session machine", r"RCM_TOKEN"),
    ("Session machine", r"rcm check"),
    ("Session machine", r"rcm run"),
    ("Docker", r"docker (build|run)"),
    ("Docker", r"127\.0\.0\.1:8787:8787|[Tt]ailscale"),  # 호스트 포트 매핑을 좁히라는 안내
    # dev → main 경로: 화살표 · `--base main --head dev` · "PRs from `dev`" 어느 표현이든
    ("Releasing", r"`?dev`?\s*(→|->|to)\s*`?main`?|--base main --head dev|PRs? from `?dev`?"),
    ("Releasing", r"git tag v"),
    ("Releasing", r"PYPI_PUBLISH"),
    ("Releasing", r"[Tt]rusted [Pp]ublish"),
    ("Verify on the real build machine", r"--ref"),  # M3 항목 3개
    ("Verify on the real build machine", r"read_auth"),
    ("Verify on the real build machine", r"retention"),
]


def test_readme_status_line_says_m4():
    text = read(README)
    assert has(text, r"^[*_ ]*Status[*_]*:[^\n]*(M4|v0\.1\.0)"), "Status line is not M4 / v0.1.0"
    assert "Status: **M1**" not in text


def test_readme_sections_exist_in_order():
    text = read(README)
    positions = []
    for prefix in README_ORDER:
        m = heading(text, prefix)
        assert m, f"README has no '## {prefix}' heading"
        positions.append(m.start())
    assert positions == sorted(positions), f"headings out of order: {README_ORDER}"


@pytest.mark.parametrize(("prefix", "pattern"), README_MENTIONS)
def test_readme_section_mentions(prefix: str, pattern: str):
    assert has(section(read(README), prefix), pattern), f"'## {prefix}' lacks /{pattern}/"


def test_readme_development_counts_eight_mutations():
    text = read(README)
    assert "three known mutations" not in text
    dev = section(text, "Development")
    assert has(dev, r"\b8\b[^\n]{0,40}mutation|mutation[^\n]{0,40}\b8\b", re.M | re.I)


# ── CHANGELOG.md (§6) ────────────────────────────────────────────────────────


def test_changelog_has_unreleased_and_first_release():
    text = read(CHANGELOG)
    unreleased = re.search(r"^## \[Unreleased\]", text, re.M)
    first = re.search(r"^## \[0\.1\.0\]\s*-\s*\d{4}-\d{2}-\d{2}", text, re.M)
    assert unreleased, "no `## [Unreleased]`"
    assert first, "no `## [0.1.0] - YYYY-MM-DD`"
    assert unreleased.start() < first.start()  # Keep a Changelog: 최신이 위


@pytest.mark.parametrize(
    "pattern", [r"schema[_ ]version`?\s*`?1\b", r"git_ref", r"[Ww]eb UI", r"retention"]
)
def test_changelog_first_release_summarises_m0_to_m4(pattern: str):
    text = read(CHANGELOG)
    m = re.search(r"^## \[0\.1\.0\][^\n]*$", text, re.M)
    assert m, "no `## [0.1.0]`"
    rest = text[m.end() :]
    nxt = re.search(r"^## \[", rest, re.M)
    sec = rest[: nxt.start()] if nxt else rest
    assert has(sec, pattern), f"0.1.0 section lacks /{pattern}/"


# ── examples · LICENSE · pyproject (회귀 · §1) ────────────────────────────────


@pytest.mark.parametrize("path", [PLIST, UNIT], ids=rel)
def test_service_examples_still_exist(path: Path):
    assert path.is_file(), f"missing: {rel(path)}"


def test_license_is_mit():
    assert "MIT License" in read(LICENSE)


def test_pyproject_declares_mit_license():
    text = read(PYPROJECT)
    assert has(text, r"^license\b"), "no `license` field"  # 값·형식은 test_packaging 이 본다
    assert "MIT" in text
