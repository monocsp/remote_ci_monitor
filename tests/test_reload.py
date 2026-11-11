"""재시작 없이 설정을 다시 읽기(M5m) — `POST /reload` · `rcm reload` · SIGHUP.

왜 있는가: 프리셋 하나를 붙이자고 서버를 재시작하면 그때 로컬 레인에서 도는 잡이 `lost` 가
된다. 그리고 다른 머신에 있는 세션은 SIGHUP 을 보낼 수가 없다.

무엇을 **안** 하는가가 설계의 절반이다. 긴 수명 스레드(워커 · Janitor · Notifier · 샘플러)는
기동 때 설정 객체를 붙잡아 갔으므로 `[server]` 를 갈아 끼워도 그쪽은 옛 값으로 돈다 — 그래서
프리셋과 저장소만 바꾸고, 나머지가 달라졌으면 **통째로 거절**한다. 조용히 안 먹는 쪽이 더 비싸다.
"""

import tempfile
from pathlib import Path

import pytest

from remote_ci_monitor import config, server, store
from remote_ci_monitor.core.reload import reload_summary

ONE = '[[presets]]\nname = "a"\nargv = ["true"]\n'
TWO = ONE + '[[presets]]\nname = "b"\nargv = ["true"]\n'


@pytest.fixture
def live():
    """설정 파일 하나와 그 위에 뜬 App. `write(…)` 로 파일을 고친다."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        path = tmp / "rcm.toml"
        head = f'[server]\ndata_dir = "{tmp}"\n'

        def write(body: str, *, head: str = head) -> None:
            path.write_text(head + body, encoding="utf-8")

        write(ONE)
        cfg = config.load_server_config(path)
        app = server.App(cfg, store.Store(cfg.data_dir / "db.sqlite3"))
        yield app, write, head


# ── 되는 것 ──────────────────────────────────────────────────────────────────


def test_a_new_preset_is_there_without_a_restart(live):
    app, write, _ = live
    write(TWO)
    out = app.reload_config()
    assert out["presets_added"] == ["b"]
    assert app.config.preset("b") is not None


def test_a_removed_preset_goes_away(live):
    app, write, _ = live
    write(TWO)
    app.reload_config()
    write(ONE)
    assert app.reload_config()["presets_removed"] == ["b"]
    assert app.config.preset("b") is None


def test_an_edited_preset_is_reported_as_changed(live):
    app, write, _ = live
    write('[[presets]]\nname = "a"\nargv = ["true"]\ntimeout_seconds = 99\n')
    out = app.reload_config()
    assert out["presets_changed"] == ["a"]
    assert app.config.preset("a").timeout_seconds == 99


def test_a_repo_can_be_added_too(live):
    app, write, _ = live
    write(ONE + '[[repos]]\nname = "r"\nurl = "https://example.invalid/r.git"\n')
    assert app.reload_config()["repos_added"] == ["r"]


def test_reloading_an_unchanged_file_says_nothing_changed(live):
    app, _write, _ = live
    out = app.reload_config()
    assert reload_summary(out) == "nothing changed"


def test_the_swap_replaces_the_object_it_does_not_edit_it(live):
    """claim 은 락 없이 `self.config.preset(name)` 를 읽는다. 자리에서 고치면 목록을 읽는
    중에 바뀔 수 있다 — 대입 하나로 바꿔야 옛 것 아니면 새 것만 보인다."""
    app, write, _ = live
    before = app.config
    write(TWO)
    app.reload_config()
    assert app.config is not before
    assert [p.name for p in before.presets] == ["a"]  # 옛 객체는 건드리지 않았다


def test_the_local_workers_get_the_new_config_too(live):
    """레인은 claim 할 때 **제가 들고 있는** 설정에서 프리셋을 찾는다(worker.py). 거기에
    전파하지 않으면 「다시 읽었다」고 말해 놓고 레인은 옛 프리셋으로 돈다 — 제일 나쁜 거짓말."""
    app, write, _ = live
    app.start()
    try:
        assert app.workers, "이 시험은 레인이 있어야 뜻이 있다"
        write(TWO)
        app.reload_config()
        for worker in app.workers:
            assert worker.config is app.config
            assert worker.config.preset("b") is not None
    finally:
        app.shutdown()


def test_the_janitor_gets_the_new_config_too(live):
    """Janitor 는 만료된 버전의 저장소를 제 설정에서 찾는다(janitor.py)."""
    app, write, _ = live
    app.start()
    try:
        write(ONE + '[[repos]]\nname = "r"\nurl = "https://example.invalid/r.git"\n')
        app.reload_config()
        assert app.retention is not None
        assert app.retention.config.repo("r") is not None
    finally:
        app.shutdown()


def test_reloading_re_measures_the_job_environment(live):
    """재시작 없이 프리셋을 바꾸는 기능이, 재시작해야만 맞는 검사를 남겨서는 안 된다(#186)."""
    app, write, _ = live
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    assert "b" not in app.preset_env
    write(
        TWO.replace('name = "b"\nargv = ["true"]', 'name = "b"\nargv = ["true"]\nrequires = ["sh"]')
    )
    app.reload_config()
    assert "b" in app.preset_env  # 새 프리셋이 health 와 `rcm check` 에 바로 보인다


# ── 거절 ─────────────────────────────────────────────────────────────────────


def test_a_broken_file_changes_nothing(live):
    """도는 서버가 잘못된 편집 하나로 망가져서는 안 된다."""
    app, write, _ = live
    write("이건 toml 이 아니다 ===")
    with pytest.raises(server.ApiError) as e:
        app.reload_config()
    assert e.value.status == 400 and "config unchanged" in e.value.message
    assert [p.name for p in app.config.presets] == ["a"]


def test_a_preset_the_config_rejects_changes_nothing(live):
    app, write, _ = live
    write('[[presets]]\nname = "a"\nargv = []\n')  # argv 가 비면 설정이 거절한다
    with pytest.raises(server.ApiError):
        app.reload_config()
    assert app.config.preset("a").argv == ("true",)


@pytest.mark.parametrize(
    "section_toml, named",
    [
        ("lanes = 3\n", "server"),
        ("[host]\ninterval_seconds = 9\n", "host"),
    ],
)
def test_a_section_that_cannot_be_applied_live_is_refused_by_name(live, section_toml, named):
    """조용히 안 먹는 것보다 거절하고 이름을 대는 게 낫다 — `lanes = 3` 이 아무 일도 안 하는 걸
    한참 뒤에 아는 게 제일 비싸다."""
    app, write, head = live
    extra = section_toml if section_toml.startswith("[") else ""
    inline = "" if extra else section_toml
    write(ONE + extra, head=head + inline)
    with pytest.raises(server.ApiError) as e:
        app.reload_config()
    assert e.value.status == 409 and f"[{named}]" in e.value.message
    assert "restart" in e.value.message


def test_a_refused_reload_leaves_the_presets_alone(live):
    app, write, head = live
    write(TWO, head=head + "lanes = 3\n")
    with pytest.raises(server.ApiError):
        app.reload_config()
    assert app.config.preset("b") is None  # 같은 파일의 새 프리셋도 안 들어간다


def test_a_server_without_a_config_file_has_nothing_to_reload():
    with tempfile.TemporaryDirectory() as d:
        cfg = config.ServerConfig()
        object.__setattr__(cfg.server, "data_dir", d)
        app = server.App(cfg, store.Store(Path(d) / "db.sqlite3"))
        with pytest.raises(server.ApiError) as e:
            app.reload_config()
        assert e.value.status == 409 and "without a config file" in e.value.message


# ── 한 줄 요약 ───────────────────────────────────────────────────────────────


def test_the_summary_names_what_changed():
    line = reload_summary({"presets_added": ["x", "y"], "repos_removed": ["r"]})
    assert line == "preset added x, y · repo removed r"


def test_the_summary_puts_what_disappeared_last():
    line = reload_summary({"presets_removed": ["gone"], "presets_added": ["new"]})
    assert line.index("new") < line.index("gone")


def test_the_summary_says_so_when_nothing_moved():
    assert reload_summary({}) == "nothing changed"


# ── HTTP ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def srv(tmp_path):
    """`tests/test_server.py` 의 서버 하나를 그대로 쓰되, 설정 **파일**을 주고 그걸 가리키게
    한다 — 다시 읽기는 파일이 있어야 할 일이 있다."""
    from test_server import Server

    s = Server(tmp_path, workers=False)
    path = tmp_path / "rcm.toml"
    path.write_text(f'[server]\ndata_dir = "{s.cfg.data_dir}"\n{ONE}', encoding="utf-8")
    s.app.config = config.load_server_config(path)
    yield s, path
    s.close()


def test_post_reload_needs_an_admin_token(srv):
    s, _path = srv
    assert s.req("POST", "/reload", json_body={})[0] == 401
    assert s.req("POST", "/reload", token="alice", json_body={})[0] == 403


def test_post_reload_applies_the_file_and_names_what_changed(srv):
    s, path = srv
    path.write_text(path.read_text() + '[[presets]]\nname = "b"\nargv = ["true"]\n')
    status, body = s.req("POST", "/reload", token="admin", json_body={})
    assert status == 200 and body["presets_added"] == ["b"]
    assert s.app.config.preset("b") is not None


def test_post_reload_refuses_a_restart_only_change_with_409(srv):
    s, path = srv
    path.write_text(path.read_text().replace("[server]", "[server]\nlanes = 3"))
    status, body = s.req("POST", "/reload", token="admin", json_body={})
    assert status == 409 and "[server]" in body["error"] and "restart" in body["error"]


def test_get_reload_is_not_a_route(srv):
    s, _path = srv
    assert s.req("GET", "/reload", token="admin")[0] == 405
