"""M5t-b 문서 잠금 — 끊김 유예 · `irreversible` · 그룹 홀드 풀기 · 홀드 알림(docs/m5tb-workplan.md
§6 의 18 · §4.12 · F49 · RL49).

문서가 코드와 같은 숫자 · 같은 문구를 말하는지 본다. 뒤 PR(PR2 · PR3)이 이 파일에 더한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def test_rl25_rl32_the_worker_exits_of_protocol_2_are_documented():
    """P3 1라운드 MED: 서버는 버전을 먼저 본다. 옛 워커는 버전 거절을 받아 따라가거나 종료 2 이고,
    같은 버전 문자열의 옛 워커가 받는 `protocol_required` 도 종료 2 다(옛 등록 길). 종료 3 은 새
    워커가 프로토콜 2 를 모르는 같은 버전의 서버를 만났을 때뿐이고 그 문구는 워커의 것이다."""
    from remote_ci_monitor.remote_worker import EXIT_PROTOCOL, PROTOCOL_OLD_SERVER
    from remote_ci_monitor.remote_workers import PROTOCOL_REQUIRED

    assert EXIT_PROTOCOL == 3
    changelog = flat((ROOT / "CHANGELOG.md").read_text())
    for name, text in (("operating", flat(OPERATING)), ("configuration", flat(CONFIGURATION))):
        assert f"`{PROTOCOL_OLD_SERVER}` and exit 3" in text, name
        assert f"`{PROTOCOL_REQUIRED}` and exit 3" not in text, name
    text = flat(OPERATING)
    assert f"`{PROTOCOL_REQUIRED}` and also stops with exit 2" in text
    assert "The server checks a worker's version before its protocol" in text
    assert "exits 0, so it is not started again" in text
    assert f"`{PROTOCOL_OLD_SERVER}`, exit 3" in changelog
    assert "the worker exits 3 and its service starts it again" not in changelog


def test_every_hold_env_key_of_a_notification_is_documented():
    keys = [k for k in notify_env({}, "r") if k == "RCM_EVENT" or k.startswith("RCM_HOLD")]
    assert keys == ["RCM_EVENT", "RCM_HOLD", "RCM_HOLD_GROUP", "RCM_HOLD_NOTE"]
    text = flat(CONFIGURATION)
    for key in [*keys, "RCM_HOLD_CAUSE", "RCM_HOLD_WORKER"]:
        assert f"`{key}`" in text, key
    assert "group held — run rcm release-hold <job>" in text


def _unreleased() -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    start = text.index("## [Unreleased]")
    return text[start : text.index("\n## [", start + 1)]


def test_the_changelog_names_what_a_user_sees_change_in_this_release():
    """P3 2라운드 LOW: 사용자에게 보이는 바뀜이 CHANGELOG `[Unreleased]` 에 있다 — 표식 잡의 세션
    폴더 기록 실패(`launch_failed`), 긴 대기 claim 의 409 `session_fenced`, 못 읽은 홀드의 `null`,
    업그레이드 때 프로토콜 1 워커의 finish 와 홀드, 그리고 `### Fixed` 의 `/api/status` 500."""
    from remote_ci_monitor.remote_worker import WORKER_PROTOCOL

    unreleased = _unreleased()
    text = flat(unreleased)
    assert "`launch_failed`" in text and "session folder" in text
    assert "`session_fenced`" in text and "409" in text
    assert "`irreversible_holds` is `null`" in text
    assert f"protocol {WORKER_PROTOCOL - 1} worker" in text and "without `group_gone`" in text
    fixed = flat(section(unreleased, "Fixed"))
    assert "`/api/status`" in fixed and "500" in fixed


def test_operating_says_what_an_upgrade_does_to_a_job_an_old_worker_claimed():
    """업그레이드 절이 프로토콜 1 워커가 이미 claim 한 잡의 끝을 말한다 — 끝낼 수 있지만
    `group_gone` 이 없는 finish 는 표식 프리셋의 그룹을 홀드한다(P3 2라운드 LOW)."""
    text = flat(section(OPERATING, "Upgrade"))
    assert "already claimed" in text and "`group_gone`" in text
    assert "[`rcm release-hold`](#when-a-group-is-held-rcm-release-hold)" in text


def test_operating_says_a_job_of_a_preset_no_longer_configured_counts_as_irreversible():
    """P3 3라운드 LOW(B4 제안): 업그레이드 전에 집힌 잡의 프리셋이 설정에서 빠졌으면 그 잡은 표식
    잡으로 센다(모름은 닫힌 쪽, 결정 339-d · `Store._is_irreversible_row`). 확인 없이 닫히면
    `irreversible:<preset>` 그룹을 홀드한다 — 업그레이드 단락이 그것을 말한다."""
    text = flat(section(OPERATING, "Upgrade"))
    assert "no longer in the configuration" in text
    assert "`irreversible:<preset name>`" in text


# ── README 두 언어의 명령 표(P2-4 · §4.12) ───────────────────────────────────

#: 명령 표에 둘 `rcm release-hold` 행의 첫 칸 — 표의 다른 admin 명령처럼 잡 번호는 `N`
RELEASE_HOLD_CELL = "`rcm release-hold N [--reason TEXT] [--json]`"
#: 그 행이 가리키는 절 — docs/operating.md 의 「When a group is held: `rcm release-hold`」
RELEASE_HOLD_LINK = "docs/operating.md#when-a-group-is-held-rcm-release-hold"


def test_readme_command_tables_list_release_hold_in_both_languages():
    """README.md · README.ko.md 의 「Session commands」 표가 같은 한 줄을 싣는다
    (관리자 표시 · 운영 문서 절 링크)."""
    from remote_ci_monitor.cli import build_parser

    sub = next(a for a in build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    hold_help = sub.choices["release-hold"].format_help()
    assert "--reason" in hold_help and "--json" in hold_help
    assert "## When a group is held: `rcm release-hold`" in OPERATING
    admin = {"README.md": "(admin)", "README.ko.md": "(관리자)"}
    for name, word in admin.items():
        table = section((ROOT / name).read_text(), "Session commands")
        rows = [line for line in table.splitlines() if line.startswith(f"| {RELEASE_HOLD_CELL} |")]
        assert len(rows) == 1, name
        assert word in rows[0] and f"]({RELEASE_HOLD_LINK})" in rows[0], name


# ── P6 엣지 감사의 문서 ───────────────────────────────────────────────────────


def test_gap4_the_irreversible_section_says_retry_is_refused_with_the_loader_message():
    """# GAP4 표식 프리셋의 `[presets.retry]` 는 설정 로드가 거절한다 — 문서가 그 사실과 오류
    문구를 말한다."""
    import pytest

    body = flat(section(CONFIGURATION, "Jobs that must not run twice: `irreversible`"))
    assert "An irreversible preset cannot have" in body
    assert "a retry runs the command again" in body
    quoted = "irreversible presets cannot have [retry]"
    assert f"`{quoted}`" in body
    preset = {
        "name": "ship",
        "argv": ["true"],
        "source_modes": ["git_ref"],
        "irreversible": True,
        "retry": {"on_exit": 75},
    }
    with pytest.raises(config_mod.ConfigError) as e:
        config_mod.parse_preset(preset)
    assert quoted in str(e.value)


def test_p62_1_the_irreversible_section_says_a_frozen_retry_is_not_used():
    """# P6-2 #1 표식 전에 반복을 얼린 잡도 표식 잡으로 claim 되면 반복하지 않는다 — 문서가
    그것을 말한다."""
    body = flat(section(CONFIGURATION, "Jobs that must not run twice: `irreversible`"))
    assert "submitted with a retry before you marked its preset" in body
    assert "it is never retried" in body
    assert "ends it `failed`, with no next attempt" in body


IRREVERSIBLE = "Jobs that must not run twice: `irreversible`"


def test_gap8_irreversible_says_avoid_clock_steps_and_how_it_differs_from_the_release_mode():
    """# GAP8 명세 §9 의 문서 두 줄: 표식 잡을 도는 워커는 NTP 단계 보정을 피한다(점프가 G − m
    이상이면 일찍 멈춘다), 그리고 프리셋 키 `irreversible` 은 릴리스 쪽의 「되돌릴 수 없는 입력」과
    다르다(입력이 rehearsal 이어도 자기 멈춤이 걸린다)."""
    body = flat(section(CONFIGURATION, IRREVERSIBLE))
    assert "Keep the worker's clock from jumping." in body
    assert "`G − m` or more makes the worker stop an irreversible job early" in body
    assert "`worker_self_stopped`, exit 3 from `rcm wait`" in body
    assert "let NTP slew the clock instead of stepping it" in body
    assert "This key is not the release checks' irreversible mode" in body
    assert "even when its input is a rehearsal" in body


def test_gap8_a_replaced_session_split_from_the_server_shows_running_until_the_grace():
    """# GAP8 펜싱된 세션이 분단이면 그 잡은 G 까지 레인 · 그룹을 잡고 화면은 running 이다."""
    text = flat(CONFIGURATION)
    assert "If the first process cannot be reached when it is replaced (a network split)" in text
    assert "the queue still shows them `running`" in text


def test_gap8_operating_says_a_replaced_service_stays_down_and_how_to_restart_it():
    """# GAP8 같은 토큰의 둘째 프로세스가 서비스를 펜싱하면 서비스는 종료 0 이라 다시 뜨지
    않는다 — 둘째 프로세스의 로그 줄(워커 코드의 문구)과 되살리는 명령 둘. 그리고 `server` ·
    `server_name` 을 바꾸기 전에 워커를 멈추고 잡이 끝나기를 기다린다."""
    text = flat(OPERATING)
    line = (
        "[rcm worker] replaced 1 other process of worker build-02 — a service worker with this"
        " token has stopped and will not start again by itself"
    )
    assert line in OPERATING
    assert "`launchctl kickstart -k gui/<uid>/<label>`" in text
    assert "`systemctl --user restart <unit>`" in text
    assert "Change a worker's `server` or `server_name` only while it runs no job" in text
    assert "stop the worker and wait for its jobs to end first" in text


def test_p62_2_operating_says_a_restarted_server_answers_null_and_what_the_worker_says():
    """# P6-2 #2 재시작한 서버는 `worker_timeout_seconds` 동안 펜싱한 세션이 살아 있었는지 모른다 —
    `fenced_sessions: null` 과 워커의 「모른다」 줄(워커 코드의 문구 앞부분)."""
    text = flat(OPERATING)
    assert "For `worker_timeout_seconds` after the server restarts it cannot tell" in text
    assert "`fenced_sessions: null`" in text
    assert (
        "`the server restarted recently and cannot tell whether this replaced another process of"
        " worker build-02`" in text
    )


def test_p62_3_operating_says_an_unsent_finish_leaves_the_folder_for_the_next_process():
    """# P6-2 #3 멈추기 전에 끝을 알리지 못한 잡은 폴더가 남고 다음 프로세스가 확인한다."""
    text = flat(OPERATING)
    assert "stops before it could tell the server how a job ended" in text
    assert "leaves that job's folder in its `data_dir`" in text
    assert "the job closes without holding its group" in text


def test_gap5_operating_says_a_worker_whose_job_holds_a_group_is_not_forgotten():
    """# GAP5 일주일 조용한 워커를 잊는 규칙의 예외에 열린 홀드가 든다."""
    text = flat(OPERATING)
    assert "neither is a worker whose job holds a group" in text
    assert "confirm the job's processes are gone and release the hold" in text


def test_p63_2_the_irreversible_section_says_its_job_shows_no_retry_limit():
    """# P6-3 #2 표식 잡으로 claim 된 잡은 반복하지 않는다 — 잡 문서의
    `max_attempts` 가 null 이다."""
    body = flat(section(CONFIGURATION, IRREVERSIBLE))
    assert "its job document shows `max_attempts: null`" in body


def test_p63_3_the_irreversible_section_says_a_late_cancel_keeps_the_failed_close():
    """# P6-3 #3 취소가 명령을 멈추기 전에 명령이 반복 종료 코드로 끝나면 `failed` 다."""
    body = flat(section(CONFIGURATION, IRREVERSIBLE))
    assert "A cancel that has not stopped the command yet does not change that" in body
    assert "the job ends `failed`, as any job that does not retry does" in body


# ── PR3: 사람용 화면(§4.8) ─────────────────────────────────────────────────────

UNREACHABLE = "When a worker cannot be reached"
HELD = "When a group is held: `rcm release-hold`"


def test_rl28_operating_quotes_the_unreachable_lines_rcm_prints():
    """# RL28 문서의 줄이 `rcm top` · `rcm wait` 이 실제로 찍는 줄과 같다."""
    from remote_ci_monitor.core.render_text import unreachable_text

    body = flat(section(OPERATING, UNREACHABLE))
    assert "`worker_unreachable_grace`" in body and "its ETA is `—`" in body
    assert unreachable_text("build-02") in body
    assert f"#812 {unreachable_text('worker build-02')}" in body
    assert "#812 worker build-02 is back — the job goes on" in body


def test_rl29_operating_names_the_five_lost_endings_and_the_late_result_line():
    """# RL29 다섯 끝의 `summary_code` 와 늦은 결과 · 불완전한 로그 줄."""
    from remote_ci_monitor.core.render_text import (
        late_result_line,
        log_incomplete_line,
        lost_line,
    )

    body = flat(section(OPERATING, UNREACHABLE))
    for code in (
        "worker_unreachable",
        "worker_self_stopped",
        "worker_replaced",
        "worker_restarted_without_job",
        "orphan_unverified",
    ):
        assert f"`{code}`" in body, code
    lost = {
        "state": "lost",
        "summary_code": "worker_unreachable",
        "summary_args": {"name": "build-02", "seconds": 660},
    }
    assert lost_line(lost, job_id=812) in body
    late = {"late_result": {"outcome": "succeeded", "exit_code": 0}}
    assert late_result_line(late, job_id=812) in body
    assert log_incomplete_line({"log_complete": False}, job_id=812) in body


def test_rl45_operating_says_where_a_hold_shows_and_quotes_the_lines():
    """# RL45 웹 · `rcm wait` · `rcm top` · `rcm check` · `rcm doctor` 의 홀드 줄."""
    from remote_ci_monitor.cli import HOLD_ROW, action_line
    from remote_ci_monitor.core.doctor import hold_line

    body = flat(section(OPERATING, HELD))
    action = {"code": "irreversible_hold", "note": "group held — run rcm release-hold 812"}
    blocked = {"job_id": 812, "group": "irreversible:ship"}
    assert action_line(821, action, blocked) in body
    assert flat(f"`warn  {HOLD_ROW}`") in body
    doctor = hold_line({"job_id": 812, "preset": "ship", "group": "irreversible:ship"})
    assert doctor.split(": ", 1)[1].replace("812", "N") in body
    assert "`group held` chip" in body and "exit 4" in body
    assert "`unknown — group holds unavailable`" in body
    # P3 r1: doctor 텍스트의 「모름」 한 줄은 실제 문구 그대로
    from remote_ci_monitor.core.doctor import HOLDS_UNKNOWN_LINE

    assert f"`{HOLDS_UNKNOWN_LINE}`" in body
    # P3 r1: 못 읽는 항목 하나면 세 곳 모두 목록 전체가 모름 — `rcm check` 의 실제 문구를 인용한다
    from remote_ci_monitor.cli import _hold_rows

    unknown = _hold_rows({"irreversible_holds": [{"job_id": "x"}]})
    assert len(unknown) == 1 and f"`{unknown[0][2]}`" in body, unknown
    assert "`irreversible_holds: null`" in body


def test_rl28_both_usage_guides_show_the_grace_and_the_two_wait_lines():
    """# RL28 사용 안내 두 거울의 「When a worker stops」 가 유예 중 화면과 `rcm wait` 의 두 줄을
    같은 뜻 · 같은 번호로 말한다. 옛 「자동으로 재개되지 않는다」 는 남지 않는다."""
    from remote_ci_monitor.core.render_text import unreachable_text

    usage = (ROOT / "docs" / "usage.md").read_text()
    usage_ko = (ROOT / "docs" / "usage.ko.md").read_text()
    for text, heading in ((usage, "When a worker stops"), (usage_ko, "워커가 멈추면")):
        raw = section(text, heading)
        body = flat(raw)
        assert re.findall(r"^(\d+)\. ", raw, re.M) == ["1", "2", "3"], heading
        # 이름과 잡 번호는 web-worker-down.png 장면(tools/screenshots/capture.py 의 down)과 같다
        assert unreachable_text("mac2-worker") in body, heading
        assert "`—`" in body, heading
        assert "(configuration.md#a-lost-connection-disconnect_grace_seconds)" in body, heading
        assert "(operating.md#when-a-worker-cannot-be-reached)" in body, heading
        assert f"#11 {unreachable_text('worker mac2-worker')}\n" in raw, heading
        assert "#11 worker mac2-worker is back — the job goes on\n" in raw, heading
        assert "`lost`" in body, heading
    assert "not resumed automatically" not in usage
    assert "자동으로 재개되지도 않는다" not in usage_ko
    # 조사 고침(batch-3) 뒤로는 이름까지 인용한다 — 「mac2-worker과」가 아니다
    assert "`워커 mac2-worker와 연락이 끊김 · 유예 시간 안에 돌아오면 이어서 진행`" in flat(
        section(usage_ko, "워커가 멈추면")
    )


def test_rl28_both_usage_guides_quote_the_banner_as_it_is_now():
    """# RL28 「When a worker stops」 1번은 띠를 지금 문장대로 인용한다(i18n.js 의
    `header.workers_unreachable_one`). 옛 「결과를 잃는다 · 알 수 없다」는 남지 않는다."""
    en = (
        "`Worker mac2-worker unreachable — its jobs go on if back within the grace period,"
        " else end as lost`"
    )
    ko = "`워커 mac2-worker 연결 끊김 — 유예 시간 안에 돌아오면 작업이 이어지고 넘기면 결과를 잃음`"
    usage = flat(section((ROOT / "docs" / "usage.md").read_text(), "When a worker stops"))
    usage_ko = flat(section((ROOT / "docs" / "usage.ko.md").read_text(), "워커가 멈추면"))
    assert en in usage
    assert en in usage_ko and ko in usage_ko
    i18n = (ROOT / "src" / "remote_ci_monitor" / "web" / "i18n.js").read_text()
    assert en.strip("`").replace("mac2-worker", '" + a.name + "') in i18n
    assert ko.strip("`").replace("mac2-worker", '" + a.name + "') in i18n
    for text in (usage, usage_ko):
        assert "running jobs are lost" not in text
        assert "결과를 알 수 없습니다" not in text


def test_operating_says_an_unreported_job_waits_on_disk_not_a_minute():
    """§4.5(PR2) — 못 보낸 보고는 디스크 큐에 남아 24시간 안에 다시 보낸다. 옛 「1분 뒤 포기」
    문장은 돌아오지 않는다(드레인 끝의 1분 기다림은 남는다 — 그 뒤 큐는 디스크에 있다)."""
    body = flat(OPERATING)
    assert "gives the report up" not in body
    assert "After that minute it logs" not in body
    assert "keeps it in its heartbeats" not in body
    assert "in a queue on disk" in body
    assert "`job #<id> still not reported — restarting anyway`, leaves the queue on disk" in body
    assert "for up to 24 hours after the job ends" in body
    assert (
        "`#<id> report: log and artifacts not delivered in 24h — sending the finish without them`"
        in body
    )


def test_operating_drain_paragraph_follows_spec_4_5_and_has_the_300_second_cap():
    """P3 r1 — 드레인 문단은 명세 §4.5(docs/m5tb-workplan.md 「재전송 창」)의 순서대로: 24시간이
    지나면 로그 · 산출물 없이 finish 를 `log_bytes` 와 함께 보내고, finish 마저 24시간 안에 못
    보내면 큐 항목을 버리고 로그 한 줄. 다시 시작한 워커가 등록 전에 큐를 보내는 데는 서버가
    응답하는 시간으로 300초 상한(결정 337 · RL46). 인용하는 로그 문구는 PR2 의
    remote_worker.py 에 있는 것만이다."""
    body = flat(OPERATING)
    send = (
        "`#<id> report: log and artifacts not delivered in 24h — sending the finish without them`"
    )
    drop = "`#<id> report: finish not delivered in 24h — dropping the report`"
    assert send in body and drop in body
    assert body.index(send) < body.index(drop)
    assert "with the size of the log it had (`log_bytes`)" in body
    assert "for at most 300 seconds of server error answers" in body
    assert "connection failures do not count" in body
    assert (
        "`dead session <id> queue not delivered after 300s of server errors — registering;"
        " job #<id> will close as restarted-without-job`" in body
    )
    assert "`worker_restarted_without_job`" in body


def test_rl28_docs_say_rcm_run_prints_the_waiting_lines_too():
    """# RL28 P3 r1: 끊김 · 돌아옴 · 홀드 줄은 기다리는 동안의 줄이라 `rcm run` 도 찍는다(같은
    `_wait`). 끝난 뒤의 줄(유실 원인 · 늦은 결과 · 불완전한 로그)만 `rcm wait` 의 것이다."""
    unreachable = flat(section(OPERATING, UNREACHABLE))
    held = flat(section(OPERATING, HELD))
    row = "| `rcm wait` and `rcm run`, while they wait (stderr) |"
    assert row in unreachable and row in held
    changelog = flat((ROOT / "CHANGELOG.md").read_text())
    assert "`rcm wait` and `rcm run` print one line while they wait when contact is lost" in (
        changelog
    )
    assert "`rcm wait` and `rcm run` (while they wait) and `rcm top` name the group" in changelog
    usage = flat(section((ROOT / "docs" / "usage.md").read_text(), "When a worker stops"))
    assert "`rcm wait` and `rcm run` keep waiting through the grace" in usage
    usage_ko = flat(section((ROOT / "docs" / "usage.ko.md").read_text(), "워커가 멈추면"))
    assert "`rcm wait` 과 `rcm run` 은 유예 동안에도 계속 기다리고" in usage_ko


PR3_ENTRIES = (
    "**A job whose worker is out of reach says so in words.**",
    "**Group holds are shown to people.**",
    "**The web's unreachable-worker banner no longer says the running jobs are lost.**",
    "**Korean screens pick the right particle after a name like `mac2-worker`.**",
)


@pytest.mark.parametrize("head", PR3_ENTRIES)
def test_changelog_pr3_entries_link_their_pull_request(head):
    """P3 r1: CHANGELOG 의 이 PR 항목 넷은 PR 링크를 단다(docs 스킬 체크리스트 1)."""
    text = (ROOT / "CHANGELOG.md").read_text()
    start = text.index(head)
    end = min(i for i in (text.find("\n- ", start), text.find("\n\n", start)) if i != -1)
    entry = text[start:end]
    assert "([#285](https://github.com/monocsp/remote_ci_monitor/pull/285))" in entry, entry


def test_operating_doctor_json_key_table_has_irreversible_holds():
    """P3 r1 — `rcm doctor --json` 키 표에 `irreversible_holds` 행(역할에 따라 목록 또는 null)."""
    rows = [ln for ln in OPERATING.splitlines() if ln.startswith("| `irreversible_holds` |")]
    assert len(rows) == 1, rows
    row = rows[0]
    assert "`worker`" in row and "`coordinator-worker`" in row and "`null`" in row
