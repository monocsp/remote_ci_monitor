"""M5n 격리 검증(계약 대조)이 찾은 결함 — 명세와 구현이 어긋난 곳마다 하나씩.

보고서: docs/acceptance/reports/2026-11-16-m5n-verify-contract.md. 서버 시험은
`test_server_m5n.M5nServer`(in-process HTTP · 주입 시계 · 로컬 레인 스레드 없음)를 그대로 쓴다.
"""

from __future__ import annotations

import pytest

from test_cli_m5n import fake, run  # noqa: F401 — `fake` 는 픽스처다
from test_server_m5n import PRESETS, PROBES, M5nServer

ANYWHERE = 'name = "anywhere"\nplacement = "any-capable"\n'

# ── 1. 웹의 「다시 점검」은 `@local` 을 퍼센트 인코딩해 보낸다 ─────────────────


@pytest.fixture
def srv(tmp_path):
    s = M5nServer(tmp_path)
    yield s
    s.close()


def test_recheck_accepts_a_percent_encoded_local_machine_id(srv):
    """web/app.js 는 `encodeURIComponent("@local")` = `%40local` 로 부른다. 서버가 경로를 풀지
    않으면 Mac mini(로컬) 의 다시 점검 버튼만 언제나 404 다 — 가짜 서버는 경로를 풀어
    이것을 가렸다."""
    srv.app.measure_capabilities()
    plain = srv.req("POST", "/machines/@local/recheck", token="admin", json_body={})
    encoded = srv.req("POST", "/machines/%40local/recheck", token="admin", json_body={})
    assert plain[0] == 200, plain
    assert encoded[0] == 200, encoded
    assert [j["preset"] for j in encoded[1]["jobs"]] == ["simcheck"]


def test_recheck_still_refuses_an_encoded_slash(srv):
    status, _ = srv.req("POST", "/machines/a%2Fb/recheck", token="admin", json_body={})
    assert status in (400, 404)


# ── 2. reload 가 any-capable 프리셋을 바꿔도 줄 선 잡은 갇히지 않는다 ─────────


def test_a_capable_job_queued_before_a_reload_to_pool_is_still_claimed(srv):
    """잡은 제출 때의 `placement` 를 지닌다. claim 의 `capable` 목록을 **지금 설정의** placement
    로 거르면, reload 로 `any-capable` 을 뗀 프리셋의 대기 잡은 어느 갈래에도 안 걸려 영원히 선다
    — 화면은 그 잡에 시각을 약속한다(fail-open)."""
    srv.reported("build-02")
    jid = srv.queued("anywhere")
    srv.write(PROBES + PRESETS.replace(ANYWHERE, 'name = "anywhere"\n'))
    status, body = srv.req("POST", "/reload", token="admin", json_body={})
    assert status == 200, body
    gen = srv.machines_doc()["generation"]
    assert srv.report("build-02", gen)[0] == 200
    assert srv.claimed("build-02") == jid


def test_a_capable_job_of_a_removed_preset_is_not_promised_a_time(srv):
    """reload 로 프리셋이 사라지면 그 `capable` 대기 잡은 아무도 집지 않는다. 그런데 배치가 pool
    갈래로 떨어져 표시 풀의 레인에 시각을 매겼다."""
    srv.generation("mbp", pool="default")  # 표시 풀(default)에 살아 있는 레인 하나
    jid = srv.queued("anywhere")
    srv.write(PROBES + PRESETS.replace(ANYWHERE, ANYWHERE.replace("anywhere", "gone")))
    status, body = srv.req("POST", "/reload", token="admin", json_body={})
    assert status == 200, body
    row = srv.row(jid)
    assert row["reason"] == "no_capable_machine"
    assert row["estimate"]["finish_at"] is None
    assert srv.claim("mbp")[0] == 204
