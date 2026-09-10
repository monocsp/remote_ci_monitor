"""자동 태그 워크플로 문면 잠금 (docs/gate-replay-fixes-workplan.md §3 I8-2 · 결정 82).

`main` 에 push 된 `__version__` 의 태그 `v<X>` 가 없으면 `tag-release.yml` 이 만들고, 같은 run 에서
`release.yml` 을 `workflow_call` 로 부른다 — `GITHUB_TOKEN` 이 만든 태그는 다른 워크플로를 깨우지
않는다는 GitHub 의 규칙 때문이다. YAML 파서는 없다(표준 라이브러리만) — tests/test_release_files.py
와 같은 방식으로 정규식과 줄 스캔으로 문면만 본다. `run: |` 블록은 `bash -n` 까지만.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TAG_RELEASE = WORKFLOWS / "tag-release.yml"
RELEASE = WORKFLOWS / "release.yml"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

#: 릴리스 잡을 부르는 잡이 가져야 하는 권한 — release.yml 의 잡 수준 권한을 전부 덮어야 한다
#: (호출된 워크플로의 잡은 호출한 잡의 권한을 넘을 수 없다).
CALLER_PERMISSIONS = {"contents": "write", "id-token": "write"}

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")


# ── 도우미 (tests/test_release_files.py 와 같은 규칙) ─────────────────────────


def read(path: Path) -> str:
    assert path.is_file(), f"missing: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def has(text: str, pattern: str, flags: int = re.M) -> bool:
    return re.search(pattern, text, flags) is not None


def top_block(text: str, key: str) -> str:
    """최상위 `<key>:` 부터 다음 최상위 키 직전까지."""
    m = re.search(rf"^{re.escape(key)}:[ \t]*(#.*)?$", text, re.M)
    assert m, f"no top-level `{key}:`"
    rest = text[m.end() :]
    nxt = re.search(r"^[A-Za-z_][\w-]*:", rest, re.M)
    return text[m.start() : m.end() + (nxt.start() if nxt else len(rest))]


def job_block(text: str, job: str) -> str:
    """`jobs:` 아래 `<job>:` 부터 같은 들여쓰기의 다음 키 직전까지(들여쓰기로 자른다)."""
    body = top_block(text, "jobs")
    m = re.search(rf"^(?P<indent>[ \t]+){re.escape(job)}:[ \t]*(#.*)?$", body, re.M)
    assert m, f"no job named {job!r}"
    rest = body[m.end() :]
    nxt = re.search(rf"^{m.group('indent')}[A-Za-z_][\w-]*:", rest, re.M)
    return body[m.start() : m.end() + (nxt.start() if nxt else len(rest))]


def needs_of(block: str) -> set[str]:
    m = re.search(r"^[ \t]*needs:[ \t]*(?P<val>.*)$", block, re.M)
    assert m, "no `needs:`"
    val = m.group("val").split("#", 1)[0].strip()
    items = val.strip("[]").split(",") if val.startswith("[") else [val]
    return {i.strip().strip("'\"") for i in items if i.strip()}


def permissions_of(block: str) -> dict[str, str]:
    """블록 안 `permissions:` 아래 `<scope>: <level>` 줄들. 없으면 빈 dict."""
    m = re.search(r"^(?P<indent>[ \t]*)permissions:[ \t]*$", block, re.M)
    if not m:
        return {}
    found: dict[str, str] = {}
    for line in block[m.end() :].lstrip("\n").splitlines():
        lm = re.match(rf"^{m.group('indent')}[ \t]+([\w-]+):[ \t]*(\w+)", line)
        if not lm:
            break
        found[lm.group(1)] = lm.group(2)
    return found


def run_scripts(text: str) -> list[str]:
    """`run: |` 블록의 본문(들여쓰기 제거). 워크플로 표현식 `${{ … }}` 은 본문에 없어야 한다."""
    scripts: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^([ \t]*)(- )?run:[ \t]*\|[ \t]*$", line)
        if not m:
            continue
        body: list[str] = []
        for nxt in lines[i + 1 :]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(m.group(1)) + len(
                m.group(2) or ""
            ):
                break
            body.append(nxt)
        indent = min(len(ln) - len(ln.lstrip()) for ln in body if ln.strip())
        scripts.append("\n".join(ln[indent:] for ln in body) + "\n")
    return scripts


# ── tag-release.yml ──────────────────────────────────────────────────────────


def test_tag_release_triggers_only_on_push_to_main():
    on = top_block(read(TAG_RELEASE), "on")
    assert has(on, r"^\s*push:")
    assert has(on, r"^\s*branches:\s*\[\s*['\"]?main['\"]?\s*\]"), "no `push.branches: [main]`"
    for other in ("tags", "pull_request", "workflow_dispatch", "workflow_call", "schedule"):
        assert not has(on, rf"^\s*{other}:"), f"`on.{other}` must not exist — main push only"


def test_tag_release_declares_contents_write_at_workflow_level():
    text = read(TAG_RELEASE)
    assert permissions_of(top_block(text, "permissions")) == {"contents": "write"}


def test_tag_release_reads_version_from_the_package():
    tag = job_block(read(TAG_RELEASE), "tag")
    # 단일 출처: remote_ci_monitor.__version__ — release.yml 과 같은 한 줄
    assert "__version__" in tag
    assert has(tag, r"sys\.path\.insert\(0, ['\"]src['\"]\)")
    assert "import remote_ci_monitor" in tag


def test_tag_release_is_idempotent_and_refuses_a_reused_version():
    tag = job_block(read(TAG_RELEASE), "tag")
    assert has(tag, r"git ls-remote --tags origin[^\n]*refs/tags/"), (
        "existence check is `ls-remote`"
    )
    # 같은 커밋에 이미 있으면 무동작(exit 0) · 다른 커밋을 가리키면 거절(::error:: + exit 1)
    assert has(tag, r'"\$GITHUB_SHA"'), "must compare the existing tag against GITHUB_SHA"
    assert has(tag, r"::error::[^\n]*(already|exists|points)[^\n]*")
    assert has(tag, r"^\s*exit 1\s*$")
    assert has(tag, r"^\s*exit 0\s*$")


def test_tag_release_creates_an_annotated_tag_at_github_sha_and_pushes_it():
    tag = job_block(read(TAG_RELEASE), "tag")
    assert has(tag, r"git tag -a [^\n]*\"\$GITHUB_SHA\""), "annotated tag at the pushed commit"
    assert has(tag, r"git push origin [^\n]*refs/tags/"), "push the tag ref only, never a branch"
    assert not has(tag, r"git push [^\n]*(main|HEAD)"), "the branch itself must never be pushed"
    assert has(tag, r"git config user\.(name|email)")


def test_tag_release_calls_release_workflow_with_the_tag():
    text = read(TAG_RELEASE)
    release = job_block(text, "release")
    assert needs_of(release) == {"tag"}
    assert has(release, r"^\s*if:[^\n]*needs\.tag\.outputs\.created == 'true'"), "gate on `created`"
    assert has(release, r"^\s*uses:\s*\./\.github/workflows/release\.yml\s*$")
    assert has(release, r"^\s*with:\s*$") and has(
        release, r"^\s*tag:\s*\$\{\{\s*needs\.tag\.outputs\.tag\s*\}\}"
    )
    assert has(release, r"^\s*secrets:\s*inherit\s*$")
    assert has(release, r"^\s*steps:") is False, "a `uses:` job has no steps"
    # 태그 잡은 릴리스 잡이 읽는 두 출력을 낸다
    tag = job_block(text, "tag")
    assert has(tag, r"^\s*outputs:\s*$")
    for name in ("created", "tag"):
        assert has(tag, rf"^\s*{name}:\s*\$\{{\{{\s*steps\.[\w-]+\.outputs\.{name}\s*\}}\}}"), name


def test_tag_release_caller_permissions_cover_every_called_job():
    caller = permissions_of(job_block(read(TAG_RELEASE), "release"))
    assert caller == CALLER_PERMISSIONS
    called = read(RELEASE)
    jobs = top_block(called, "jobs")
    for job in re.findall(r"^  ([\w-]+):[ \t]*$", jobs, re.M):
        for scope, level in permissions_of(job_block(called, job)).items():
            assert caller.get(scope) == level, f"release.yml job {job!r} needs {scope}: {level}"


# ── release.yml — 재사용 가능해야 한다 ───────────────────────────────────────


def test_release_has_workflow_call_with_a_required_string_tag_input():
    on = top_block(read(RELEASE), "on")
    assert has(on, r"^\s*tags:\s*\[\s*['\"]?v\*"), "hand-pushed tags must still release"
    m = re.search(
        r"^\s*workflow_call:\s*\n\s*inputs:\s*\n\s*tag:\s*\n(?P<body>(\s{8,}\S[^\n]*\n)+)",
        on,
        re.M,
    )
    assert m, "no `workflow_call.inputs.tag`"
    assert has(m.group("body"), r"^\s*type:\s*string\s*$")
    assert has(m.group("body"), r"^\s*required:\s*true\s*$")
    assert not has(m.group("body"), r"^\s*default:"), (
        "no default — a call without the tag must fail"
    )


def test_release_resolves_the_tag_once_per_job_and_never_bare():
    text = read(RELEASE)
    bare = [
        ln
        for ln in text.splitlines()
        if "github.ref_name" in ln and "inputs.tag || github.ref_name" not in ln
    ]
    assert not bare, f"bare github.ref_name (wrong ref when called): {bare}"
    assert "GITHUB_REF_NAME" not in text and "GITHUB_REF" not in text
    for job in ("build", "github-release"):
        head = job_block(text, job).split("steps:", 1)[0]
        assert has(head, r"^\s*TAG:\s*\$\{\{\s*inputs\.tag \|\| github\.ref_name\s*\}\}"), job


def test_release_checks_out_the_tag_it_was_given():
    text = read(RELEASE)
    for job in ("build", "github-release"):
        block = job_block(text, job)
        assert has(block, r"^\s*ref:\s*refs/tags/\$\{\{\s*env\.TAG\s*\}\}"), f"{job}: checkout ref"
    build = job_block(text, "build")
    assert has(build, r"git merge-base --is-ancestor HEAD origin/main"), (
        "the checked-out tag, not the caller's sha"
    )


# ── run: | 블록은 문법이 맞는 bash 여야 한다 ────────────────────────────────


@needs_bash
@pytest.mark.parametrize("path", [TAG_RELEASE, RELEASE], ids=lambda p: p.name)
def test_run_blocks_parse_as_bash(path: Path):
    scripts = run_scripts(read(path))
    assert scripts, "no `run: |` blocks"
    for script in scripts:
        assert "${{" not in script, "expressions go through `env:`, not into the script"
        proc = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
        assert proc.returncode == 0, f"{proc.stderr}\n---\n{script}"


# ── CONTRIBUTING 「Releasing」 ────────────────────────────────────────────────


def test_contributing_releasing_says_the_tag_is_automatic():
    text = read(CONTRIBUTING)
    m = re.search(r"^## Releasing\b[^\n]*$", text, re.M)
    assert m
    rest = text[m.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    sec = rest[: nxt.start()] if nxt else rest
    assert "tag-release" in sec
    assert has(sec, r"git tag v"), "the hand-pushed tag remains the re-run path"
    assert not has(sec, r"^3\.\s+`git tag"), "step 3 is no longer a manual tag push"
