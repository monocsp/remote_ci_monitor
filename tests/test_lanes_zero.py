"""M5t-a PR3 — 분배 전용 코디네이터 `lanes = 0`(결정 243 · `docs/m5t-workplan.md` §4.2 · §7 TL 표).

`lanes = 0` 인 서버는 잡을 받아 큐에 두고 원격 워커에 나눠 주기만 한다. `@local` 은 살아 있지
않고, 후보 함수(`machines_for`, 결정 254)가 어느 프리셋에도 `@local` 을 후보로 내지 않는다.
`/api/health` 의 `capacity` 는 「지금 갈 수 있는 머신이 하나라도 있는 프리셋 수 / 전체 프리셋
수」다.

서버는 `tests/test_server_m5n.py` 의 `M5nServer`(in-process HTTP · 주입 시계 · 로컬 레인 스레드
없음)를 `lanes = 0` 머리로 띄운다. 프리셋과 도우미는 `tests/test_local_only.py` 의 것을 쓴다 —
원격 워커 `build-02` 는 기본 풀, `mbp` 는 `mac2` 풀이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import test_server_m5n
from remote_ci_monitor.config import ConfigError, load_server_config
from test_local_only import OTHER, PRESETS, REMOTE, block, write_config
from test_server_m5n import M5nServer

LANES_ONE = "lanes = 1\n"
LANES_ZERO = "lanes = 0\n"


def zero(tmp_path: Path, *, lanes: int = 0, presets: str = PRESETS, **kw: Any) -> M5nServer:
    """`[server] lanes` 만 바꾼 `M5nServer`. 머리는 모듈 상수라 띄우는 동안만 바꿔 둔다."""
    head = test_server_m5n.HEAD
    assert LANES_ONE in head
    test_server_m5n.HEAD = head.replace(LANES_ONE, f"lanes = {lanes}\n")
    try:
        s = M5nServer(tmp_path, presets=presets, **kw)
    finally:
        test_server_m5n.HEAD = head
    s.pools_of = {REMOTE: "default", OTHER: "mac2", "mac2-worker": "mac2"}
    return s


@pytest.fixture
def lz(tmp_path):
    s = zero(tmp_path)
    yield s
    s.close()


# ── TL1 설정 · TL10 mDNS ────────────────────────────────────────────────────


def test_tl1_lanes_zero_loads_and_the_server_starts(tmp_path):
    p = write_config(tmp_path, LANES_ZERO + block("gate"))
    try:
        cfg = load_server_config(p, environ={}, check_tools=False)
        err = ""
    except ConfigError as e:
        cfg, err = None, str(e)
    assert cfg is not None, err
    assert cfg.server.lanes == 0
    # 음수는 여전히 기동 실패다
    bad = write_config(tmp_path, "lanes = -1\n" + block("gate"))
    with pytest.raises(ConfigError) as e:
        load_server_config(bad, environ={}, check_tools=False)
    assert "lanes" in str(e.value)
    (tmp_path / "srv").mkdir()
    s = zero(tmp_path / "srv", workers=True)
    try:
        assert s.app.workers == []  # 로컬 레인 스레드가 없다
        status, health = s.req("GET", "/api/health")
        assert status == 200 and health["ok"] is True, health
        assert health["lanes"] == 0
    finally:
        s.close()


def test_tl10_the_mdns_txt_carries_lanes_zero(tmp_path, monkeypatch):
    from remote_ci_monitor import server as server_module

    seen: list[dict[str, str]] = []

    class FakeResponder:
        def __init__(self, *args: Any, txt_fn: Any, **kw: Any):
            seen.append(txt_fn())

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(server_module, "Responder", FakeResponder)
    s = zero(tmp_path, extra='advertise = true\nbind = "0.0.0.0"\n', workers=True)
    try:
        assert seen, "the server did not advertise"
        assert seen[0]["lanes"] == "0", seen
    finally:
        s.close()
