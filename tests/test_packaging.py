"""패키징(M4) — 템플릿 == 예시(바이트) · pyproject 동적 버전 · LICENSE · sdist 포함 목록 ·
CHANGELOG · wheel 내용(templates/ · web/ · tests 없음 · METADATA). 명세는 docs/m4-workplan.md
§1 · §6 · §7.

wheel 은 test_web.test_wheel_ships_web_assets 와 같은 방식이다(`pip wheel . --no-deps`, 격리 빌드가
hatchling 을 못 받으면 skip). 한 번만 빌드해 여러 검사가 나눠 쓴다. 구현보다 먼저 썼다(test-first).
"""

from __future__ import annotations

import importlib.resources
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ("server.toml", "client.toml")
# `pip wheel .` 은 격리된 빌드 환경에 hatchling 을 내려받는다 — 오프라인이면 실패가 아니라 skip.
NO_NETWORK_MARKERS = ("Could not", "No matching", "No module named pip")


def example(name: str) -> bytes:
    """정본 `examples/<name>` 의 바이트."""
    return (REPO_ROOT / "examples" / name).read_bytes()


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    """`pip wheel .` 로 만든 wheel 하나. test_web.test_wheel_ships_web_assets 와 같은 명령·skip."""
    dist = tmp_path_factory.mktemp("dist")
    cmd = [sys.executable, "-m", "pip", "wheel", ".", "-w", str(dist), "--no-deps", "-q"]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 and any(m in proc.stderr for m in NO_NETWORK_MARKERS):
        pytest.skip(f"pip wheel could not build (offline?): {proc.stderr.strip()[-300:]}")
    assert proc.returncode == 0, proc.stderr
    wheels = sorted(dist.glob("remote_ci_monitor-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


# ── 템플릿 == 예시 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_matches_the_example_byte_for_byte(name):
    src = REPO_ROOT / "src" / "remote_ci_monitor" / "templates" / name
    assert src.is_file(), f"missing {src.relative_to(REPO_ROOT)}"
    assert src.read_bytes() == example(name), (
        f"{src.relative_to(REPO_ROOT)} differs from examples/{name} — fix both together"
    )


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_is_reachable_as_a_package_resource(name):
    res = importlib.resources.files("remote_ci_monitor") / "templates" / name
    assert res.is_file(), f"missing package resource templates/{name}"
    assert res.read_bytes() == example(name)


# ── pyproject.toml ───────────────────────────────────────────────────────────


def test_version_is_dynamic_and_read_from_init(pyproject):
    project = pyproject["project"]
    assert project.get("dynamic") == ["version"], project.get("dynamic")
    assert "version" not in project  # 정적 버전이 남아 있으면 두 출처가 된다
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/remote_ci_monitor/__init__.py"
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_license_is_an_spdx_expression_with_license_files(pyproject):
    """Codex 리뷰 ①(반영): PEP 639 — `license = "MIT"` + `license-files`.

    `License ::` 분류자는 License-Expression 의 대체 대상이라 두지 않는다.
    """
    project = pyproject["project"]
    assert project.get("license") == "MIT", project.get("license")
    assert "LICENSE" in project.get("license-files", []), project.get("license-files")
    path = REPO_ROOT / "LICENSE"
    assert path.is_file(), "LICENSE missing at the repo root"
    assert "MIT License" in path.read_text()
    deprecated = [c for c in project["classifiers"] if c.startswith("License ::")]
    assert deprecated == [], f"PEP 639: drop license classifiers: {deprecated}"
    # hatchling 의 PEP 639(License-Expression) 지원은 1.27 부터
    requires = pyproject["build-system"]["requires"]
    spec = next((r for r in requires if r.startswith("hatchling")), None)
    m = re.fullmatch(r"hatchling>=(\d+)\.(\d+)(?:\.\d+)?", spec or "")
    assert m and (int(m.group(1)), int(m.group(2))) >= (1, 27), requires


def test_runtime_dependencies_stay_empty_and_dev_extra_has_build(pyproject):
    project = pyproject["project"]
    assert project["dependencies"] == []  # PLAN.md 「의존성 원칙」
    dev = project["optional-dependencies"]["dev"]
    assert any(re.match(r"build\b", d) for d in dev), dev
    assert any(re.match(r"pytest\b", d) for d in dev) and any(re.match(r"ruff\b", d) for d in dev)


def test_sdist_includes_examples_license_and_changelog_but_not_tests(pyproject):
    include = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    names = {i.rstrip("/") for i in include}
    for needed in ("src/remote_ci_monitor", "README.md", "pyproject.toml"):
        assert needed in names, include
    for needed in ("examples", "LICENSE", "CHANGELOG.md"):
        assert needed in names, f"{needed} not in sdist include: {include}"
    assert not any(n == "tests" or n.startswith("tests/") for n in names), include


def test_project_urls_classifiers_and_keywords(pyproject):
    project = pyproject["project"]
    urls = project["urls"]
    for key in ("Homepage", "Repository", "Issues", "Changelog"):
        assert key in urls, urls
        assert urls[key].startswith("https://"), urls[key]
    classifiers = project["classifiers"]
    for minor in (11, 12, 13):
        assert f"Programming Language :: Python :: 3.{minor}" in classifiers
    assert "Development Status :: 4 - Beta" in classifiers
    assert "Intended Audience :: Developers" in classifiers
    assert project["requires-python"] == ">=3.11"
    assert project.get("keywords"), "keywords are empty"


def test_console_scripts_are_unchanged(pyproject):
    scripts = pyproject["project"]["scripts"]
    assert scripts["rcm"] == "remote_ci_monitor.cli:main"
    assert scripts["remote-ci-monitor"] == "remote_ci_monitor.cli:main"


def test_server_template_has_an_ok_preset_that_needs_no_repo_script():
    """Codex 리뷰 ⑩(반영): README 의 첫 실행은 `rcm run ok` — 빈 디렉터리에서도 성공하는 프리셋."""
    doc = tomllib.loads((REPO_ROOT / "examples" / "server.toml").read_text())
    names = [p.get("name") for p in doc.get("presets", [])]
    ok = next((p for p in doc.get("presets", []) if p.get("name") == "ok"), None)
    assert ok is not None, names
    argv = ok.get("argv") or []
    assert argv, ok
    assert not any(a.startswith("scripts/") or a.endswith(".sh") for a in argv), argv
    assert ok.get("source_modes", ["tree"]) == ["tree"], ok  # --ref 없이 도는 프리셋


# ── CHANGELOG.md ─────────────────────────────────────────────────────────────


def test_changelog_leads_with_unreleased_or_the_current_version():
    path = REPO_ROOT / "CHANGELOG.md"
    assert path.is_file(), "CHANGELOG.md missing at the repo root"
    text = path.read_text()
    headings = re.findall(r"^## \[([^\]]+)\]", text, re.M)
    assert headings, "CHANGELOG.md has no '## [version]' headings"
    assert headings[0] in ("Unreleased", __version__), headings
    assert __version__ in headings, f"no section for {__version__}: {headings}"
    # Keep a Changelog: `## [0.1.0] - 2026-09-06`
    assert re.search(rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}\s*$", text, re.M)


# ── wheel ────────────────────────────────────────────────────────────────────


def test_wheel_name_embeds_the_package_version(wheel):
    assert wheel.name.startswith(f"remote_ci_monitor-{__version__}-"), wheel.name
    assert wheel.name.endswith("-py3-none-any.whl"), wheel.name  # 순수 파이썬


def test_wheel_ships_templates_and_web_but_no_tests(wheel):
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        shipped = sorted(n for n in names if "/templates/" in n)
        for name in TEMPLATES:
            member = f"remote_ci_monitor/templates/{name}"
            assert member in names, f"{member} not in wheel; templates/ members: {shipped}"
            assert zf.read(member) == example(name), f"{member} differs from examples/{name}"
        assert "remote_ci_monitor/web/index.html" in names
        leaked = sorted(n for n in names if n.startswith("tests/") or "/tests/" in n)
        assert leaked == [], leaked


def test_wheel_metadata_license_python_and_no_runtime_deps(wheel):
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = zf.read(meta_name).decode("utf-8")
    assert meta_name == f"remote_ci_monitor-{__version__}.dist-info/METADATA", meta_name
    assert re.search(rf"^Version: {re.escape(__version__)}$", meta, re.M), meta
    # hatchling >= 1.27 + `license = "MIT"` → PEP 639 의 License-Expression(분류자 없이)
    assert re.search(r"^License-Expression: MIT$", meta, re.M), meta
    assert re.search(r"^License-File: LICENSE$", meta, re.M), meta
    assert not re.search(r"^Classifier: License ::", meta, re.M), meta
    assert re.search(r"^Requires-Python: >=3\.11$", meta, re.M), meta
    # 런타임 의존성 0 — extra 가 붙지 않은 Requires-Dist 가 없어야 한다
    runtime = [
        ln for ln in meta.splitlines() if ln.startswith("Requires-Dist:") and "extra ==" not in ln
    ]
    assert runtime == [], runtime
