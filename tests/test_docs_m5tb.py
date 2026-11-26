"""M5t-b 문서 잠금 — 끊김 유예 · `irreversible` · 그룹 홀드 풀기 · 홀드 알림(docs/m5tb-workplan.md
§6 의 18 · §4.12 · F49 · RL49).

문서가 코드와 같은 숫자 · 같은 문구를 말하는지 본다. 뒤 PR(PR2 · PR3)이 이 파일에 더한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from remote_ci_monitor import config as config_mod
from remote_ci_monitor.core.notify import notify_env

ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = (ROOT / "docs" / "configuration.md").read_text()
OPERATING = (ROOT / "docs" / "operating.md").read_text()
EXAMPLE = (ROOT / "examples" / "server.toml").read_text()


def section(text: str, heading: str) -> str:
    m = re.search(rf"^#{{2,4}} {re.escape(heading)}\s*$", text, re.M)
    assert m, f"no heading {heading!r}"
    rest = text[m.end() :]
    nxt = re.search(r"^#{2,4} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def flat(text: str) -> str:
    return " ".join(text.split())


def test_rl49_the_irreversible_section_says_session_leaving_commands_are_fail_open():
    """§6 의 18 · 5차 위임 결정 (e): 세션을 빠져나가는 명령은 rcm 이 볼 수 없어 fail-open 이다."""
    body = flat(section(CONFIGURATION, "Jobs that must not run twice: `irreversible`"))
    assert "`setsid`" in body and "double-fork daemon" in body
    assert "rcm cannot see them, so for those processes this protection is fail-open" in body
    assert "Do not start rcm's supervisor yourself inside an irreversible preset" in body


def test_the_irreversible_section_names_the_synthetic_group_and_its_log_line():
    body = flat(section(CONFIGURATION, "Jobs that must not run twice: `irreversible`"))
    assert "`irreversible:<preset name>`" in body and '`concurrency_scope = "global"`' in body
    assert (
        "preset X: irreversible without concurrency_group — using group irreversible:X (global)"
        in body
    )
    assert "`rcm release-hold N`" in body and '`on = ["irreversible_hold"]`' in body


def test_the_grace_section_matches_the_config_constants():
    body = flat(section(CONFIGURATION, "A lost connection: `disconnect_grace_seconds`"))
    default = config_mod.DISCONNECT_GRACE_DEFAULT_SECONDS
    assert f"`max({default}, L + {config_mod.DISCONNECT_GRACE_MARGIN_SECONDS})`" in body
    assert f"`{config_mod.DISCONNECT_GRACE_MAX_SECONDS}`" in body
    s = config_mod.ServerSection()
    assert f"the floor is {config_mod.disconnect_grace_floor(s)} with the default" in body
    assert "`orphan_unverified`" in body
    assert "disconnect_grace_seconds" in EXAMPLE


def test_f49_no_document_says_a_quiet_worker_loses_its_jobs_after_the_timeout():
    """F49: 「60초 뒤 lost」 문장은 유예로 바뀌었다."""
    for text in (CONFIGURATION, OPERATING, EXAMPLE):
        assert "unreachable for 61s" not in text
        assert "its jobs lost" not in text
    assert "only after the [disconnect grace]" in flat(CONFIGURATION)


def test_the_hold_release_section_says_what_to_check_and_the_exit_codes():
    body = flat(section(OPERATING, "When a group is held: `rcm release-hold`"))
    assert "`ps -g <pgid>`" in body and "last log line" in body
    assert (
        "released the hold on group irreversible:ship (job #812) — queued jobs in that group can "
        "start" in body
    )
    for code in ("`0` | released", "`1` | the job has no open hold", "`3` | the server could"):
        assert code in body
    assert "irreversible_holds" in body and "needs_action" in body


def test_the_worker_exits_of_protocol_2_are_documented():
    text = flat(OPERATING)
    assert "`worker protocol 2 required: upgrade rcm on this worker` and exit 3" in text
    assert "exits 0, so it is not started again" in text


def test_every_hold_env_key_of_a_notification_is_documented():
    keys = [k for k in notify_env({}, "r") if k == "RCM_EVENT" or k.startswith("RCM_HOLD")]
    assert keys == ["RCM_EVENT", "RCM_HOLD", "RCM_HOLD_GROUP", "RCM_HOLD_NOTE"]
    text = flat(CONFIGURATION)
    for key in [*keys, "RCM_HOLD_CAUSE", "RCM_HOLD_WORKER"]:
        assert f"`{key}`" in text, key
    assert "group held — run rcm release-hold <job>" in text
