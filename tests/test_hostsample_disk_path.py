"""`data_dir = "~/…"` 로 적은 서버의 호스트 표본에 `disk` 가 있어야 한다(M5i B5).

명세는 docs/gate-replay-fixes-workplan.md §3 B5. `server.py` 가 샘플러에 설정 파일의 원시
문자열을 넘겨 `~` 가 안 풀린 채 `shutil.disk_usage("~/…")` 가 실패했고, 표본의 `disk` 가 `null`
이라 디스크 막대도 「rcm 데이터」 줄도 안 보였다. `examples/server.toml` 의 기본값이 정확히 이
경우다. 테스트는 `Server` 픽스처(절대경로)와 별개로 `~` 설정 문자열이 `HostSampler` 까지 가는
진짜 경로를 탄다 — 고치기 전에는 빨갛다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_ci_monitor.config import load_server_config
from remote_ci_monitor.hostsample import _disk_usage
from remote_ci_monitor.server import App
from remote_ci_monitor.store import Store

DISK_KEYS = frozenset({"used_bytes", "free_bytes", "total_bytes", "path"})


@pytest.fixture
def tilde_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`~` 로 적은 `data_dir` 설정 파일로 만든 App. HOME 은 tmp_path — 실제 홈은 안 건드린다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[server]",
                'data_dir = "~/rcm-devtest"',
                'bind = "127.0.0.1"',
                "advertise = false",
                "grace_seconds = 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_server_config(config_path, check_tools=False)
    assert cfg.server.data_dir == "~/rcm-devtest"  # 원시 문자열은 그대로 — 푸는 건 프로퍼티다
    store = Store(cfg.data_dir / "rcm.sqlite3")
    app = App(cfg, store)
    app.start()
    try:
        yield app
    finally:
        app.shutdown()
        store.close()


def test_the_sampler_gets_the_expanded_data_dir(tilde_app: App, tmp_path: Path):
    """샘플러의 `disk_path` 는 `~` 를 푼 절대경로다 — 설정 파일의 원시 문자열이 아니다."""
    sampler = tilde_app.sampler
    assert sampler is not None
    assert sampler.disk_path is not None
    assert not sampler.disk_path.startswith("~")
    assert Path(sampler.disk_path).is_absolute()
    assert Path(sampler.disk_path) == tmp_path / "rcm-devtest"


def test_the_host_sample_carries_a_disk_reading(tilde_app: App):
    """그 경로로 실제 `disk_usage` 가 되고, 표본의 `disk` 가 dict 다(`null` 이 아니다)."""
    sampler = tilde_app.sampler
    assert sampler is not None
    usage = _disk_usage(sampler.disk_path)
    assert usage is not None and DISK_KEYS <= set(usage)
    sample = sampler.sample_once()
    assert sample is not None
    assert sample.disk is not None
    assert DISK_KEYS <= set(sample.disk)
    assert sample.disk["total_bytes"] > 0
    assert sample.disk["path"] == sampler.disk_path
