"""M5n 격리 검증(계약 대조)이 찾은 결함 — 명세와 구현이 어긋난 곳마다 하나씩.

보고서: docs/acceptance/reports/2026-11-16-m5n-verify-contract.md. 서버 시험은
`test_server_m5n.M5nServer`(in-process HTTP · 주입 시계 · 로컬 레인 스레드 없음)를 그대로 쓴다.
"""

from __future__ import annotations

import pytest

from test_cli_m5n import fake, run  # noqa: F401 — `fake` 는 픽스처다
from test_server_m5n import M5nServer

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
