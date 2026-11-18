"""M5o C — reload 가 들인 파일을 따라가고, `--dry-run` 으로 미리 본다(명세 §4.1 · §4.2 · 결정 124).

시나리오는 `docs/m5o-test-scenarios-c.md`(C5 · C7 · C9~C12 · C14 · C22~C29). 구현보다 먼저 썼다.

dry-run 의 약속:
- 진짜 reload 와 **같은 로드 · 같은 거절**(`RELOADABLE_NOT`)을 돌리고 **대입만 하지 않는다**.
- 바뀐 프리셋마다 **어느 키가** 바뀌었는지 이름만 — 값은 절대 안 싣는다(env · argv 에 무엇이
  있을지 모른다).
- 옛 서버는 본문을 무시하고 **진짜 reload 를 해 버린다**. 그래서 CLI 는 서버가 dry-run 을 안다고
  말할 때만(`health.reload_dry_run`) 요청을 보낸다.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

import pytest

from remote_ci_monitor import config, server, store
from remote_ci_monitor.cli import main

SECRET = "hunter2-should-never-leave-the-machine"
MARK = "argv-marker-should-never-leave-the-machine"


def preset(name: str, extra: str = "") -> str:
    return f'\n[[presets]]\nname = "{name}"\nargv = ["bash", "{name}.sh"]\n{extra}\n'


def put(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o644)


@pytest.fixture
def live():
    """server.toml(프리셋 `local`) + presets.dolomood.toml(프리셋 `gate`) 위에 뜬 App.

    `inc(text)` 는 들인 파일을, `top(body, head=…)` 는 server.toml 을 다시 쓴다."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        path = tmp / "server.toml"
        inc_path = tmp / "presets.dolomood.toml"
        head = f'include = ["presets.dolomood.toml"]\n[server]\ndata_dir = "{tmp}"\n'

        def top(body: str = preset("local"), *, head: str = head) -> None:
            put(path, head + body)

        def inc(text: str) -> None:
            put(inc_path, text)

        top()
        inc(preset("gate"))
        cfg = config.load_server_config(path)
        app = server.App(cfg, store.Store(cfg.data_dir / "db.sqlite3"))
        yield app, top, inc, head, inc_path
        for t in threading.enumerate():
            if t.name == "rcm-measure":
                t.join(timeout=30)


# ── C9 reload 는 들인 파일도 다시 읽는다 ──────────────────────────────────────


def test_c9_a_change_only_in_the_included_file_is_reloaded(live):
    app, _top, inc, _head, inc_path = live
    inc(preset("gate", "timeout_seconds = 99"))
    out = app.reload_config()
    assert out["presets_changed"] == ["gate"]
    assert out["presets_changed_keys"] == {"gate": ["timeout_seconds"]}
    assert out["includes"] == [str(inc_path)]
    assert app.config.preset("gate").timeout_seconds == 99


def test_c9_a_preset_added_to_the_included_file_is_added(live):
    app, _top, inc, *_ = live
    inc(preset("gate") + preset("game-web-smoke"))
    assert app.reload_config()["presets_added"] == ["game-web-smoke"]


def test_c5_a_missing_included_file_changes_nothing(live):
    app, _top, _inc, _head, inc_path = live
    inc_path.unlink()
    before = app.config
    with pytest.raises(server.ApiError) as e:
        app.reload_config()
    assert e.value.status == 400 and "config unchanged" in e.value.message
    assert "presets.dolomood.toml" in e.value.message
    assert app.config is before


def test_c7_a_broken_included_file_changes_nothing(live):
    app, _top, inc, *_ = live
    inc("=== not toml")
    before = app.config
    with pytest.raises(server.ApiError) as e:
        app.reload_config()
    assert e.value.status == 400 and "presets.dolomood.toml" in e.value.message
    assert app.config is before


def test_c6_a_group_writable_included_file_changes_nothing(live):
    app, _top, _inc, _head, inc_path = live
    os.chmod(inc_path, 0o664)
    with pytest.raises(server.ApiError) as e:
        app.reload_config()
    assert e.value.status == 400 and "chmod go-w" in e.value.message


def test_c21_moving_a_preset_into_the_included_file_is_not_a_change(live):
    app, top, inc, *_ = live
    top(preset("local") + preset("moved"))
    app.reload_config()
    top(preset("local"))
    inc(preset("gate") + preset("moved"))
    out = app.reload_config()
    assert out["presets_changed"] == [] and out["presets_added"] == []
    assert out["presets_removed"] == []


# ── C10 · C25 dry-run 은 대입하지 않는다 ──────────────────────────────────────


def test_c10_dry_run_leaves_the_config_object_alone_and_names_the_keys(live):
    app, _top, inc, *_ = live
    app.start()
    try:
        before = app.config
        generation = app.generation
        inc(preset("gate", "timeout_seconds = 99\nexpected_seconds = 5") + preset("new-one"))
        out = app.reload_config(dry_run=True)
        assert out["dry_run"] is True
        assert out.get("reloaded") is not True
        assert out["presets_changed"] == ["gate"]
        assert out["presets_changed_keys"] == {"gate": ["expected_seconds", "timeout_seconds"]}
        assert out["presets_added"] == ["new-one"]
        assert app.config is before
        assert app.config.preset("new-one") is None
        assert app.generation == generation  # 워커가 다시 재러 오지 않는다
        for worker in app.workers:
            assert worker.config is before
        assert app.retention is None or app.retention.config is before
    finally:
        app.shutdown()


def test_c10_a_real_reload_after_a_dry_run_applies_the_same_change(live):
    app, _top, inc, *_ = live
    inc(preset("gate", "timeout_seconds = 99"))
    preview = app.reload_config(dry_run=True)
    applied = app.reload_config()
    for key in ("presets_added", "presets_removed", "presets_changed", "presets_changed_keys"):
        assert preview[key] == applied[key], key


def test_c25_a_dry_run_with_nothing_to_change_says_so(live):
    app, *_ = live
    out = app.reload_config(dry_run=True)
    assert out["dry_run"] is True
    assert out["presets_changed"] == [] and out["presets_changed_keys"] == {}


# ── C11 dry-run 도 같은 거절 ──────────────────────────────────────────────────


def test_c11_dry_run_refuses_a_restart_only_change_with_the_same_sentence(live):
    app, top, _inc, head, _ = live
    top(head=head + "lanes = 3\n")
    with pytest.raises(server.ApiError) as dry:
        app.reload_config(dry_run=True)
    with pytest.raises(server.ApiError) as real:
        app.reload_config()
    assert dry.value.status == real.value.status == 409
    assert dry.value.message == real.value.message
    assert "[server]" in dry.value.message


def test_c7_dry_run_reports_a_broken_included_file_like_a_reload(live):
    app, _top, inc, *_ = live
    inc("=== not toml")
    with pytest.raises(server.ApiError) as e:
        app.reload_config(dry_run=True)
    assert e.value.status == 400 and "presets.dolomood.toml" in e.value.message


# ── C12 값은 절대 안 싣는다 ───────────────────────────────────────────────────


def test_c12_dry_run_names_keys_never_values(live):
    app, _top, inc, *_ = live
    inc(
        f'\n[[presets]]\nname = "gate"\nargv = ["bash", "{MARK}"]\n'
        f'[presets.env]\nTOKEN = "{SECRET}"\n'
    )
    out = app.reload_config(dry_run=True)
    text = json.dumps(out)
    assert SECRET not in text and MARK not in text
    assert out["presets_changed_keys"] == {"gate": ["argv", "env"]}


def test_c12_the_real_reload_response_carries_no_values_either(live):
    app, _top, inc, *_ = live
    inc(f'\n[[presets]]\nname = "gate"\nargv = ["{MARK}"]\n[presets.env]\nTOKEN = "{SECRET}"\n')
    text = json.dumps(app.reload_config())
    assert SECRET not in text and MARK not in text


# ── 키 이름 비교(순수) ────────────────────────────────────────────────────────


def test_changed_keys_lists_dataclass_fields_that_differ_sorted():
    from remote_ci_monitor.core.model import Preset
    from remote_ci_monitor.core.reload import changed_keys

    a = Preset(name="g", argv=("a",))
    b = Preset(name="g", argv=("b",), timeout_seconds=5, env={"X": "1"})
    assert changed_keys(a, b) == ["argv", "env", "timeout_seconds"]
    assert changed_keys(a, a) == []


# ── HTTP ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def srv(tmp_path):
    from test_server import Server

    s = Server(tmp_path, workers=False)
    path = tmp_path / "server.toml"
    inc_path = tmp_path / "presets.dolomood.toml"
    put(inc_path, preset("gate"))
    put(
        path,
        f'include = ["presets.dolomood.toml"]\n[server]\ndata_dir = "{s.cfg.data_dir}"\n'
        + preset("local"),
    )
    s.app.config = config.load_server_config(path)
    yield s, path, inc_path
    s.close()


def test_c24_dry_run_needs_an_admin_token(srv):
    s, *_ = srv
    assert s.req("POST", "/reload", json_body={"dry_run": True})[0] == 401
    assert s.req("POST", "/reload", token="alice", json_body={"dry_run": True})[0] == 403


def test_post_reload_dry_run_previews_and_applies_nothing(srv):
    s, _path, inc_path = srv
    before = s.app.config
    put(inc_path, preset("gate", "timeout_seconds = 7"))
    status, body = s.req("POST", "/reload", token="admin", json_body={"dry_run": True})
    assert status == 200, body
    assert body["dry_run"] is True
    assert body["presets_changed_keys"] == {"gate": ["timeout_seconds"]}
    assert body["includes"] == [str(inc_path)]
    assert s.app.config is before


def test_c23_dry_run_must_be_a_boolean(srv):
    s, _path, inc_path = srv
    before = s.app.config
    put(inc_path, preset("gate", "timeout_seconds = 7"))
    status, body = s.req("POST", "/reload", token="admin", json_body={"dry_run": "yes"})
    assert status == 400 and "dry_run" in body["error"]
    assert s.app.config is before


def test_post_reload_without_a_body_still_reloads(srv):
    """옛 클라이언트(`rcm reload` 가 빈 본문)는 오늘처럼 진짜 reload 를 한다."""
    s, _path, inc_path = srv
    put(inc_path, preset("gate", "timeout_seconds = 7"))
    status, body = s.req("POST", "/reload", token="admin", json_body={})
    assert status == 200 and body["presets_changed"] == ["gate"]
    assert s.app.config.preset("gate").timeout_seconds == 7


def test_health_says_the_server_knows_dry_run(srv):
    s, *_ = srv
    status, body = s.req("GET", "/api/health")
    assert body["reload_dry_run"] is True


# ── CLI: rcm reload --dry-run ─────────────────────────────────────────────────


@pytest.fixture
def cli_env(monkeypatch, tmp_path, srv):
    s, path, inc_path = srv
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(home)
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{s.port}")
    monkeypatch.setenv("RCM_TOKEN", s.tokens["admin"])
    return s, path, inc_path


def run(capsys, argv):
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_c27_rcm_reload_dry_run_prints_what_would_change(cli_env, capsys):
    s, path, inc_path = cli_env
    before = s.app.config
    put(inc_path, preset("gate", "timeout_seconds = 7") + preset("game-web-smoke"))
    code, out, err = run(capsys, ["reload", "--dry-run"])
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == f"would reload {path} (+ presets.dolomood.toml)"
    assert "  presets changed: gate (timeout_seconds)" in lines
    assert "  presets added:   game-web-smoke" in lines
    assert lines[-1] == "  nothing applied (--dry-run)"
    assert s.app.config is before


def test_c25_rcm_reload_dry_run_with_nothing_to_change(cli_env, capsys):
    code, out, err = run(capsys, ["reload", "--dry-run"])
    assert code == 0, err
    assert "  nothing would change" in out.splitlines()


def test_c28_rcm_reload_dry_run_json(cli_env, capsys):
    s, _path, inc_path = cli_env
    put(inc_path, preset("gate", "timeout_seconds = 7"))
    code, out, err = run(capsys, ["reload", "--dry-run", "--json"])
    assert code == 0, err
    doc = json.loads(out)
    assert doc["dry_run"] is True
    assert doc["presets_changed_keys"] == {"gate": ["timeout_seconds"]}


def test_c11_rcm_reload_dry_run_refused_exits_2(cli_env, capsys):
    s, path, _inc = cli_env
    put(path, path.read_text().replace("[server]", "[server]\nlanes = 3"))
    code, _out, err = run(capsys, ["reload", "--dry-run"])
    assert code == 2
    assert "[server]" in err


def test_c22_an_old_server_is_not_sent_a_dry_run(cli_env, capsys, monkeypatch):
    """옛 서버는 본문을 무시하고 진짜 reload 를 한다 — 미리 보자던 사람의 설정이 바뀐다."""
    s, _path, inc_path = cli_env
    real_health = s.app.health

    def old_health():
        status, body = real_health()
        body.pop("reload_dry_run", None)
        return status, body

    monkeypatch.setattr(s.app, "health", old_health)
    calls = []
    real_reload = s.app.reload_config
    monkeypatch.setattr(s.app, "reload_config", lambda **kw: calls.append(kw) or real_reload(**kw))
    before = s.app.config
    put(inc_path, preset("gate", "timeout_seconds = 7"))
    code, _out, err = run(capsys, ["reload", "--dry-run"])
    assert code == 2
    assert "dry-run" in err and "nothing was sent" in err
    assert calls == []
    assert s.app.config is before


def test_rcm_reload_names_the_included_files(cli_env, capsys):
    _s, path, _inc = cli_env
    code, out, err = run(capsys, ["reload"])
    assert code == 0, err
    assert f"config: {path} (+ presets.dolomood.toml)" in out.splitlines()


# ── C14 · C29 rcm check ──────────────────────────────────────────────────────


def check_row(out: str, name: str) -> str | None:
    m = re.search(rf"^(?:ok |warn|FAIL)  {re.escape(name)}(?: .*)?$", out, re.M)
    return m.group(0) if m else None


def test_c14_rcm_check_counts_presets_per_file(cli_env, capsys):
    s, path, inc_path = cli_env
    put(path, path.read_text() + preset("local-b"))
    put(inc_path, "".join(preset(f"p{i:02d}") for i in range(22)))
    code, out, err = run(capsys, ["check", "--config", str(path)])
    row = check_row(out, "local presets")
    assert row is not None, out
    assert row == "ok   local presets 24 (server.toml 2 · presets.dolomood.toml 22)", row


def test_c29_rcm_check_shows_a_broken_include_as_a_server_config_failure(cli_env, capsys):
    s, path, inc_path = cli_env
    inc_path.unlink()
    code, out, _err = run(capsys, ["check", "--config", str(path)])
    assert code == 1
    row = check_row(out, "server config")
    assert row is not None and row.startswith("FAIL") and "presets.dolomood.toml" in row, out
