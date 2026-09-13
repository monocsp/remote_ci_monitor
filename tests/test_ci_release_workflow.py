"""자동 태그 워크플로 문면 잠금 (docs/gate-replay-fixes-workplan.md §3 I8-2 · 결정 82 · M5l L6).

`main` 에 push 된 `__version__` 의 태그 `v<X>` 가 없으면 `tag-release.yml` 이 만들고, 같은 run 에서
`release.yml` 을 `workflow_call` 로 부른다 — `GITHUB_TOKEN` 이 만든 태그는 다른 워크플로를 깨우지
않는다는 GitHub 의 규칙 때문이다. 태그가 이미 있으면 그 커밋이 `GITHUB_SHA` 의 조상일 때만
무동작이고(버전을 안 올린 main push), 아니면 거절한다(버전 재사용 · pr-85 리뷰 B-1). YAML 파서는
없다(표준 라이브러리만) — tests/test_release_files.py 와 같은 방식으로 정규식과 줄 스캔으로 문면을
보고, `run: |` 블록은 `bash -n` 을 통과해야 한다. 태그 단계만은 가짜 `git` 아래서 실제로 돌려 세
분기의 종료 코드를 잠근다(리뷰 C-1 — 문면만으로는 「조상 → 0 · 아니면 → 1」이 잠기지 않는다).
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


def test_tag_release_grants_contents_write_to_the_tag_job_only():
    text = read(TAG_RELEASE)
    # 최소 권한: 워크플로 수준 permissions 는 없고, 태그를 미는 `tag` 잡만 contents: write
    # (리뷰 B-3)
    assert not has(text, r"^permissions:"), "permissions belong to the jobs, not the workflow"
    assert permissions_of(job_block(text, "tag")) == {"contents": "write"}


def test_tag_release_checks_out_full_history_for_the_ancestor_check():
    tag = job_block(read(TAG_RELEASE), "tag")
    checkout = re.search(r"uses: actions/checkout@v4\n(?P<with>(\s{8,}[^\n]*\n)+)", tag)
    assert checkout, "no checkout with `with:`"
    assert has(checkout.group("with"), r"^\s*fetch-depth:\s*0\s*$"), (
        "merge-base needs the history between the tag and GITHUB_SHA — not a depth-1 clone"
    )


def test_tag_release_reads_version_from_the_package():
    tag = job_block(read(TAG_RELEASE), "tag")
    # 단일 출처: remote_ci_monitor.__version__ — release.yml 과 같은 한 줄
    assert "__version__" in tag
    assert has(tag, r"sys\.path\.insert\(0, ['\"]src['\"]\)")
    assert "import remote_ci_monitor" in tag


def test_tag_release_is_a_noop_when_the_tag_is_an_ancestor_and_refuses_otherwise():
    tag = job_block(read(TAG_RELEASE), "tag")
    assert has(tag, r"git ls-remote --tags origin[^\n]*refs/tags/"), (
        "existence check is `ls-remote`"
    )
    # 태그 커밋을 확실히 받아 둔다(없으면 merge-base 가 128 로 「조상 아님」이 된다)
    assert has(tag, r'git fetch [^\n]*"\+refs/tags/\$TAG:refs/tags/\$TAG"')
    # 조상이면 무동작(exit 0) · 아니면 거절(::error:: + exit 1)
    # — 같은 커밋 비교(`= "$GITHUB_SHA"`)가 규칙이 아니다
    ancestor = r'if git merge-base --is-ancestor "\$AT" "\$GITHUB_SHA"; then\n'
    m = re.search(ancestor + r"(?P<yes>(?:[^\n]*\n)*?)\s*fi\n(?P<no>(?:[^\n]*\n)*?)\s*fi\n", tag)
    assert m, 'no `if git merge-base --is-ancestor "$AT" "$GITHUB_SHA"` branch'
    assert has(m.group("yes"), r"^\s*exit 0\s*$") and not has(m.group("yes"), r"::error::")
    assert has(m.group("yes"), r'echo "created=false" >> "\$GITHUB_OUTPUT"')
    assert has(m.group("no"), r"::error::[^\n]*(already|exists|points)[^\n]*ancestor")
    assert has(m.group("no"), r"^\s*exit 1\s*$")
    assert not has(tag, r'\[ "\$AT" = "\$GITHUB_SHA" \]'), "same-commit equality is not the rule"


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
    assert not has(release, r"^\s*secrets:"), (
        "release.yml uses no secrets — do not hand it every repository secret"
    )
    assert "secrets." not in read(RELEASE), "release.yml must stay secret-free for that to hold"
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
    for job in ("build", "smoke", "github-release"):
        head = job_block(text, job).split("steps:", 1)[0]
        assert has(head, r"^\s*TAG:\s*\$\{\{\s*inputs\.tag \|\| github\.ref_name\s*\}\}"), job


def test_release_checks_out_the_tag_it_was_given():
    text = read(RELEASE)
    # smoke 도 포함 — 불려 왔을 때 기본 ref 는 호출한 쪽의 GITHUB_SHA 다(리뷰 B-4)
    for job in ("build", "smoke", "github-release"):
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


# ── 태그 단계를 가짜 git 아래서 실제로 돌린다 (리뷰 C-1) ─────────────────────

FAKE_GIT = """#!/bin/bash
# 가짜 git: ls-remote 는 STUB_AT 가 있을 때만 한 줄, merge-base 는 STUB_ANCESTOR 로 종료,
# 나머지는 호출만 기록한다
echo "$*" >> "$STUB_CALLS"
case "$1" in
  ls-remote) [ -n "$STUB_AT" ] && printf '%s\\trefs/tags/%s\\n' "$STUB_AT" "$TAG"; exit 0 ;;
  merge-base) exit "$STUB_ANCESTOR" ;;
  *) exit 0 ;;
esac
"""


def tag_step_script() -> str:
    (script,) = [s for s in run_scripts(read(TAG_RELEASE)) if "ls-remote" in s]
    return script


def run_tag_step(tmp_path: Path, *, at: str, ancestor: int) -> tuple[int, str, list[str]]:
    """태그 단계 스크립트를 가짜 git 으로 실행 — (종료 코드, GITHUB_OUTPUT, git 호출 목록)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "git").write_text(FAKE_GIT)
    (bindir / "git").chmod(0o755)
    out = tmp_path / "output"
    out.touch()
    calls = tmp_path / "calls"
    calls.touch()
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "TAG": "v9.9.9",
        "GITHUB_SHA": "b" * 40,
        "GITHUB_OUTPUT": str(out),
        "STUB_AT": at,
        "STUB_ANCESTOR": str(ancestor),
        "STUB_CALLS": str(calls),
    }
    proc = subprocess.run(
        ["bash", "-e", "-o", "pipefail"],
        input=tag_step_script(),
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, out.read_text(), calls.read_text().splitlines()


@needs_bash
def test_tag_step_creates_and_pushes_the_tag_when_it_does_not_exist(tmp_path: Path):
    code, output, calls = run_tag_step(tmp_path, at="", ancestor=1)
    assert code == 0
    assert "created=true" in output
    assert any(c.startswith("tag -a v9.9.9") and c.endswith("b" * 40) for c in calls), calls
    assert "push origin refs/tags/v9.9.9" in calls


@needs_bash
def test_tag_step_is_a_noop_when_the_existing_tag_is_an_ancestor(tmp_path: Path):
    # 버전을 안 올린 main push: 태그는 옛 릴리스 커밋(조상)에 있다
    # → exit 0 · created=false · push 없음
    code, output, calls = run_tag_step(tmp_path, at="a" * 40, ancestor=0)
    assert code == 0
    assert "created=false" in output
    assert any(c.startswith("merge-base --is-ancestor " + "a" * 40 + " " + "b" * 40) for c in calls)
    assert not any(c.startswith(("tag ", "push ")) for c in calls), calls


@needs_bash
def test_tag_step_refuses_when_the_existing_tag_is_not_an_ancestor(tmp_path: Path):
    # 버전 재사용: 태그가 GITHUB_SHA 의 조상이 아닌 커밋에 있다 → exit 1 · 출력 없음 · push 없음
    code, output, calls = run_tag_step(tmp_path, at="a" * 40, ancestor=1)
    assert code == 1
    assert "created=" not in output
    assert not any(c.startswith(("tag ", "push ")) for c in calls), calls


# ── CONTRIBUTING 「Releasing」 ────────────────────────────────────────────────


def test_contributing_releasing_says_the_tag_is_automatic():
    text = read(CONTRIBUTING)
    m = re.search(r"^## Releasing\b[^\n]*$", text, re.M)
    assert m
    rest = text[m.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    sec = rest[: nxt.start()] if nxt else rest
    assert "tag-release" in sec
    assert not has(sec, r"^3\.\s+`git tag"), "step 3 is no longer a manual tag push"
    # 무동작의 뜻: 태그가 머지 커밋의 조상 (리뷰 D-1)
    assert has(sec, r"no-op[^\n]*\n?[^\n]*ancestor"), "say when a merge is a no-op"
    # 재실행 길 둘: Actions 의 Re-run, 또는 태그 삭제 후 재푸시
    # — 태그만 다시 미는 것은 무동작 (리뷰 B-2)
    assert has(sec, r"Re-run"), "the Actions re-run path"
    assert has(sec, r"git push origin :refs/tags/v"), "delete the tag before pushing it again"
    assert has(sec, r"git tag (-f )?v[\d.]+ <main-sha> && git push origin v"), "the hand tag"
    assert has(sec, r"alone[^\n]*\n?[^\n]*no-op"), "`git push origin vX` alone starts nothing"
