"""pipefail 아래에서는 출력을 다 읽기 전에 끝나는 소비자(`grep -q` · `head`)를
파이프 끝에 두지 않는다.

`grep -q` 는 찾는 줄을 보자마자 끝나며 파이프를 닫는다. 그 순간 앞 명령이 아직
쓰고 있으면 SIGPIPE 로 죽고, `set -o pipefail` 이 그 종료코드(141)를 파이프라인의
것으로 만든다 — **찾았는데도 실패한다.** 타이밍에 달려 있어서 간헐적이다.
rcm-release-driver 템플릿의 셀프테스트가 이렇게 깨졌다(2026-10-01: 이 저장소에서
유휴 30회 중 8회 · 동시 4개 부하 100회 중 42회, dolomood 게이트에서 20회 중 6회).
`do_status` 가 두 줄을 따로 쓰는데 셀프테스트는 **첫 줄**을 찾았다 — 마지막 줄을
찾던 옆 줄은 그 뒤에 쓸 것이 없어서 운 좋게 안 깨졌을 뿐이다.

규칙: pipefail 을 켜는 셸 파일에서는 출력을 변수로 다 받은 뒤 `<<<` 로 grep 하고,
첫 줄은 `sed -n 1p`(끝까지 읽는다)로 고른다. 앞 명령이 출력을 **한 번에** 다 쓰는
것이 확실하거나 종료코드가 흐름을 못 바꾸는 자리만 `ALLOWED` 에 사유와 함께 남긴다.
목록에 없는 새 자리는 이 테스트가 빨갛게 만든다 — 판단을 적어 두게 하려는 것이다.
줄이 바뀌면 다시 판단한다(줄 내용 전체로 묶었다).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("src", "scripts", "examples", "tools")

#: 파이프(`||` 가 아닌 `|`) 뒤의 조기 종료 소비자 — `grep` 의 짧은 옵션 묶음에 `q` 가
#: 있거나 `head`.
EARLY_EXIT = re.compile(r"(?<!\|)\|(?!\|)\s*(?:grep\b[^|]*?\s-[A-Za-z]*q|head\b)")
#: 이 파일이 pipefail 을 켜는가 — 주석이 아니라 `set …` 줄로.
SETS_PIPEFAIL = re.compile(r"^\s*set\b[^#\n]*\bpipefail\b", re.M)

_DRIVER = "src/remote_ci_monitor/skills/rcm-release-driver/templates/release_driver.sh"
_QA_JOB = "src/remote_ci_monitor/skills/rcm-qa-connect/templates/qa_job.sh"

#: (저장소 상대 경로, 앞뒤 공백을 뗀 줄) → 그 자리가 안전한 이유.
ALLOWED: dict[tuple[str, str], str] = {
    (
        _DRIVER,
        'if git ls-remote --tags origin "refs/tags/$(echo "${TAG_PATTERN}" | sed '
        '"s/{version}/${BUILD_NAME}/;s/{build}/${CONFIRM_N}/")" | grep -q .; then',
    ): "정확한 ref 하나 — 많아야 두 줄(태그와 ^{})을 git 이 한 번에 쓴다",
    (
        _DRIVER,
        'if ! git ls-remote --tags origin "refs/tags/${tag}" | grep -q .; then',
    ): "정확한 ref 하나 — 많아야 두 줄을 git 이 한 번에 쓴다",
    (
        _DRIVER,
        'if [ -n "${DEV_BRANCH}" ] && ! gh_ pr list --head "${DEFAULT_BRANCH}" '
        '--base "${DEV_BRANCH}" --state open --json number '
        "--jq 'length' | grep -qv '^0$'; then",
    ): "`--jq length` — 숫자 하나를 한 번에 쓴다",
    (
        _DRIVER,
        'for fn in s6_merge s7_upload; do grep -A4 "^${fn}()" "${SELF}" '
        "| grep -q require_typed_n "
        '|| { echo "${fn} lacks require_typed_n"; exit 1; }; done',
    ): "앞 grep 은 다섯 줄 이하 — 파이프로 나가는 grep 출력은 모였다가 한 번에 나간다",
    (
        _DRIVER,
        '[ "$(grep -n -E \'STATUS_ONLY.*do_status|^  preflight_remote$\' "${SELF}" '
        '| head -1 | grep -c do_status)" = 1 ] '
        '|| { echo "--status must return before preflight_remote"; exit 1; }',
    ): "`[ ]` 인자 안의 치환 — 종료코드가 흐름을 안 바꾼다(비교하는 것은 출력이다)",
    (
        _QA_JOB,
        'rc="$(awk -v u="$unit" -v p="$plat" \'$1==u && $2==p {print $3}\' '
        '"$FAKE_TABLE" | head -1)"; rc="${rc:-0}"',
    ): "이 파일은 `set -uo pipefail` — `-e` 가 없어 치환의 종료코드가 무시된다",
    (
        _QA_JOB,
        't "① declared list and steps::N come before the build step" '
        '"$([ "$(grep -n \'declared_units.txt\' "$L" | head -1 | cut -d: -f1)" '
        '-lt "$(grep -n \'^::rcm::steps::\' "$L" | cut -d: -f1)" ] '
        '&& [ "$(grep -n \'^::rcm::steps::\' "$L" | cut -d: -f1)" '
        '-lt "$(grep -n \'^::rcm::step::build ios\' "$L" | cut -d: -f1)" ] '
        '&& echo yes || echo no)" yes',
    ): "`[ ]` 인자 안의 치환 — 종료코드가 흐름을 안 바꾼다(비교하는 것은 출력이다)",
    (
        _QA_JOB,
        'bad_id="$(grep -E \'/|::\' "$DECLARED" | head -1 || true)"',
    ): "`|| true` — 종료코드를 버린다",
}


def _shell_files() -> list[Path]:
    out: list[Path] = []
    for top in SCAN_ROOTS:
        base = ROOT / top
        if base.is_dir():
            out.extend(p for p in base.rglob("*.sh") if ".venv" not in p.parts)
    return sorted(out)


def _offenders() -> list[tuple[str, int, str]]:
    found = []
    for path in _shell_files():
        text = path.read_text(encoding="utf-8")
        if not SETS_PIPEFAIL.search(text):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for n, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not EARLY_EXIT.search(stripped):
                continue
            if (rel, stripped) not in ALLOWED:
                found.append((rel, n, stripped))
    return found


def _bash(script: str) -> str:
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return res.stdout.strip()


def test_the_premise_grep_q_kills_a_writer_that_is_still_writing():
    """전제: pipefail 아래 `쓰는 중인 명령 | grep -q` 는 찾아도 141 이다. `yes` 는 끝없이
    쓰니 결정적이다. 같은 검사를 `<<<` 로 하면 0 이다 — 이 규칙이 고치는 차이가 이것이다."""
    assert _bash("set -o pipefail; yes | grep -q y; echo $?") == "141"
    assert _bash('set -o pipefail; out="$(printf \'y\\n\')"; grep -q y <<<"$out"; echo $?') == "0"


def test_the_scanner_sees_the_shell_files_it_is_meant_to_guard():
    """스캐너가 대상을 못 찾으면 `_offenders()` 가 빈 목록이 되어 아래 검사가 헛돈다 —
    그래서 지키려는 파일이 실제로 스캔되고 pipefail 파일로 잡히는지 따로 본다."""
    scanned = {p.relative_to(ROOT).as_posix() for p in _shell_files()}
    guarded = {
        _DRIVER,
        _QA_JOB,
        "src/remote_ci_monitor/skills/rcm-gate-connect/templates/gate_wrapper.sh",
        "scripts/smoke_install.sh",
    }
    assert guarded <= scanned, guarded - scanned
    for rel in guarded:
        assert SETS_PIPEFAIL.search((ROOT / rel).read_text(encoding="utf-8")), rel


def test_the_scanner_catches_the_shape_that_failed():
    """검출기 양성 대조: 깨졌던 바로 그 줄 모양을 잡고, `||` 와 `<<<` 는 잡지 않는다."""
    bad = '( BUILD_NAME=""; do_status ) | grep -q "^stages: x$" || { echo no; exit 1; }'
    assert EARLY_EXIT.search(bad)
    assert EARLY_EXIT.search('find "${ART_DIR}" -name plan.json | head -1')
    assert EARLY_EXIT.search('printf "%s\\n" "${line}" | grep -qE "${STEP_PATTERN}"')
    assert not EARLY_EXIT.search("cmd || grep -q x file")
    assert not EARLY_EXIT.search('grep -q "^stages: x$" <<<"${_st}"')
    assert not EARLY_EXIT.search('find "${ART_DIR}" -name plan.json | sed -n 1p')


def test_no_early_exit_consumer_under_pipefail():
    """pipefail 을 켜는 셸 파일에 `… | grep -q` · `… | head` 가 새로 생기면 빨갛다.
    고치는 법: `_out="$(cmd)"; grep -q pat <<<"${_out}"` · 첫 줄은 `cmd | sed -n 1p`.
    정말 안전하면(한 번에 쓰는 한 줄 · 종료코드를 버리는 자리) 사유와 함께 `ALLOWED` 에."""
    found = _offenders()
    assert not found, "\n".join(f"{rel}:{n}: {line}" for rel, n, line in found)


def test_every_allowed_line_still_exists():
    """목록이 썩지 않는다 — 고쳐서 사라진 줄이 허용 목록에 남으면 다음 사람이 그 사유를
    믿는다."""
    lines = {
        rel: {raw.strip() for raw in (ROOT / rel).read_text(encoding="utf-8").splitlines()}
        for rel in {rel for rel, _ in ALLOWED}
    }
    missing = [(rel, line) for rel, line in ALLOWED if line not in lines[rel]]
    assert not missing, missing
