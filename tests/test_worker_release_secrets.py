"""워커와 릴리스 비밀 — 잡 env 의 `<secrets_dir_env>` 와 stdout 마스킹(계약 §4).

여기서 지키는 것:
- 프로파일이 있는 저장소를 가리키는 프리셋의 잡은 비밀 폴더 경로를 그 변수로 받는다 — 폴더가
  **있을 때만**. 프로파일이 없거나 폴더가 없으면 변수도 없다.
- 그 잡의 stdout 에 8자 이상 value 비밀이 나오면 로그 파일에는 `****` 만 남는다. 짧은 값과 file
  비밀은 안 지운다. 프로파일 밖 프리셋은 손대지 않는다.
- 원격 워커가 올리는 로그(`POST /worker/jobs/<id>/log`)도 같은 규칙으로 지워져 저장된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_ci_monitor.config import RepoConfig, parse_preset, parse_release_profile
from remote_ci_monitor.core.model import SUCCEEDED
from remote_ci_monitor.release_secrets import SecretStore, secrets_dir
from remote_ci_monitor.store import Store
from test_release_secrets import PROFILE_RAW, TOKEN
from test_worker import enqueue, make_config, run_one
from test_worker_api import OCTET, WorkerServer

SHORT = "abc"  # 8자 미만 — 마스킹 안 함
RELEASE_PRESET = {
    "name": "release-plan",
    "argv": [
        "sh",
        "-c",
        'echo "dir=${APP_SECRETS:-unset}"; echo "tok=$(cat "$APP_SECRETS/GH_TOKEN" 2>/dev/null)";'
        ' echo "hook=$(cat "$APP_SECRETS/TEAMS_WEBHOOK" 2>/dev/null)"',
    ],
    "source_modes": ["tree", "git_ref"],
    "repo": "app",
    "timeout_seconds": 30,
}


def _config(tmp_path: Path, *, with_profile: bool = True):
    cfg = make_config(tmp_path)
    cfg.path = tmp_path / "etc" / "server.toml"
    cfg.repos = (
        RepoConfig(
            name="app",
            url=str(tmp_path / "r.git"),
            release=parse_release_profile("app", PROFILE_RAW) if with_profile else None,
        ),
    )
    cfg.presets = (*cfg.presets, parse_preset(RELEASE_PRESET))
    return cfg


@pytest.fixture
def env(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def log_of(cfg, jid: int) -> str:
    return (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()


def test_without_a_secrets_folder_the_env_variable_is_not_set(env):
    store, cfg = env
    jid = enqueue(store, cfg, "release-plan")
    run_one(store, cfg, jid)
    assert store.get_job(jid).state == SUCCEEDED
    assert "dir=unset\n" in log_of(cfg, jid)


def test_the_job_gets_the_folder_and_its_stdout_is_masked(env):
    store, cfg = env
    secrets = SecretStore(secrets_dir(cfg.path, "app"), cfg.repos[0].release)
    secrets.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    secrets.put("TEAMS_WEBHOOK", SHORT.encode(), content_type="text/plain")
    jid = enqueue(store, cfg, "release-plan")
    run_one(store, cfg, jid)
    assert store.get_job(jid).state == SUCCEEDED
    log = log_of(cfg, jid)
    assert f"dir={secrets.root}\n" in log
    assert "tok=****\n" in log  # 마스킹 지문: 값 → `****` (release_secrets.mask_bytes)
    assert TOKEN not in log
    assert f"hook={SHORT}\n" in log  # 8자 미만은 그대로


def test_a_preset_outside_the_profile_is_untouched(env):
    """같은 서버에 비밀이 있어도 다른 프리셋의 로그는 안 건드린다 — `repo` 가 없으니까."""
    store, cfg = env
    secrets = SecretStore(secrets_dir(cfg.path, "app"), cfg.repos[0].release)
    secrets.put("GH_TOKEN", TOKEN.encode(), content_type="text/plain")
    jid = enqueue(store, cfg, "env", inputs={"scope": "fast"})
    run_one(store, cfg, jid)
    log = log_of(cfg, jid)
    assert "APP_SECRETS" not in log and "scope=fast" in log


def test_a_repo_without_a_profile_gives_the_job_nothing(tmp_path):
    cfg = _config(tmp_path, with_profile=False)
    (tmp_path / "etc" / "secrets" / "app").mkdir(parents=True)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jid = enqueue(store, cfg, "release-plan")
        run_one(store, cfg, jid)
        assert "dir=unset\n" in log_of(cfg, jid)
    finally:
        store.close()


# ── 원격 워커의 로그 ──────────────────────────────────────────────────────────


def test_a_remote_worker_log_is_masked_before_it_is_written(tmp_path):
    srv = WorkerServer(tmp_path)
    try:
        srv.cfg.path = tmp_path / "etc" / "server.toml"
        srv.cfg.repos = (
            RepoConfig(
                name="app",
                url=str(tmp_path / "nowhere.git"),
                release=parse_release_profile("app", PROFILE_RAW),
            ),
        )
        SecretStore(secrets_dir(srv.cfg.path, "app"), srv.cfg.repos[0].release).put(
            "GH_TOKEN", TOKEN.encode(), content_type="text/plain"
        )
        jid = srv.git_ref_job()  # `deploy` 프리셋 — repo = "app", 40 hex 라 원격을 안 부른다
        srv.registered("build-02")
        assert srv.claimed("build-02") == jid
        line = f"::rcm::step::auth\nusing {TOKEN} now\n".encode()
        status, body = srv.req(
            "POST", f"/worker/jobs/{jid}/log", token="build-02", body=line, headers=OCTET
        )
        assert status == 200, body
        saved = srv.app.log_path(jid).read_bytes()
        assert saved == b"::rcm::step::auth\nusing **** now\n"
        assert TOKEN.encode() not in saved
        # 마커는 마스킹된 줄에서 그대로 읽힌다
        assert [m.value for m in srv.store.markers(jid)] == ["auth"]
    finally:
        srv.close()
