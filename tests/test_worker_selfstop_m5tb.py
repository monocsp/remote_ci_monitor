"""끊김 유예 설정 · 자기 멈춤(M5t-b §4.3 · 결정 253 · 263 · 320 · 335).

설정 검증과 claim 얼림을 잠근다. 시험 서버는 `test_worker_api.WorkerServer`(주입 시계)다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_ci_monitor.config import (
    DISCONNECT_GRACE_MARGIN_SECONDS,
    ConfigError,
    ServerSection,
    disconnect_grace,
    load_server_config,
)
from test_worker_api import WORKER_PRESETS, WorkerServer, sh

GOOD = '[[presets]]\nname = "gate"\nargv = ["true"]\n'


def load(tmp_path: Path, server: str) -> object:
    p = tmp_path / "server.toml"
    p.write_text("[server]\n" + server + "\n" + GOOD, encoding="utf-8")
    return load_server_config(p, environ={})


def test_rl30_the_default_grace_is_600_and_a_large_timeout_moves_it_up(tmp_path):
    assert disconnect_grace(ServerSection()) == 600
    cfg = load(tmp_path, "")
    assert cfg.server.disconnect_grace_seconds is None  # 적지 않음
    assert disconnect_grace(cfg.server) == 600
    # 키를 안 적은 큰 worker_timeout_seconds(업그레이드) — 기동 실패 없이 max(600, L + 35)
    cfg = load(tmp_path, "worker_timeout_seconds = 900")
    assert disconnect_grace(cfg.server) == 935


@pytest.mark.parametrize(
    ("keys", "floor"),
    [
        ("", 95),  # T 60 · H 5 → L = max(60, 10 + 45 + 5) = 60
        ("worker_timeout_seconds = 10\nworker_heartbeat_seconds = 1", 87),  # e2e 설정(§7.5)
        ("worker_timeout_seconds = 120\nworker_heartbeat_seconds = 5", 155),
    ],
)
def test_rl30_the_floor_is_l_plus_35_and_the_floor_itself_passes(tmp_path, keys, floor):
    cfg = load(tmp_path, f"{keys}\ndisconnect_grace_seconds = {floor}")
    assert disconnect_grace(cfg.server) == floor
    with pytest.raises(ConfigError) as e:
        load(tmp_path, f"{keys}\ndisconnect_grace_seconds = {floor - 1}")
    text = str(e.value)
    assert "[server] disconnect_grace_seconds" in text
    assert str(floor) in text and "remove the key" in text


@pytest.mark.parametrize("value", ["86401", "-5", "0", '"soon"', "true", "1.5"])
def test_rl30_out_of_range_or_not_an_integer_fails_to_start(tmp_path, value):
    with pytest.raises(ConfigError, match=r"\[server\] disconnect_grace_seconds"):
        load(tmp_path, f"disconnect_grace_seconds = {value}")


def test_rl30_the_margin_is_the_default_m():
    """config 의 하한 여유와 기본 워커 grace 10 일 때의 m(15 + 10 + 10)이 같다(결정 320)."""
    assert DISCONNECT_GRACE_MARGIN_SECONDS == 15 + 10 + 10


def test_rl30_claim_freezes_the_grace_and_a_reload_changes_only_the_next_claim(tmp_path):
    srv = WorkerServer(
        tmp_path,
        presets=[*WORKER_PRESETS, sh("ship", "echo ship", irreversible=True)],
        disconnect_grace_seconds=300,
    )
    try:
        srv.registered("build-02", lanes=2)
        a = srv.queued_job(preset="ship")
        status, body = srv.claim("build-02", 1)
        assert status == 200
        assert body["job"]["irreversible"] is True
        assert body["job"]["disconnect_grace_seconds"] == 300
        assert set(body) == {"artifacts", "job", "tree_url", "preset"}  # 최상위 키는 그대로
        # 서버 설정이 바뀌어도(재시작 흉내) 도는 잡은 얼린 값을 쓴다
        srv.cfg.server.disconnect_grace_seconds = 900
        b = srv.queued_job()
        status, body = srv.claim("build-02", 2)
        assert body["job"]["id"] == b and body["job"]["irreversible"] is False
        assert body["job"]["disconnect_grace_seconds"] == 900
        assert srv.store.get_job(a).disconnect_grace_seconds == 300
    finally:
        srv.close()
