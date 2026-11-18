"""알림이 실패 이름을 받고, 링크에 `0.0.0.0` 이 없다 — M5o B, `docs/m5o-workplan.md` §3.2 · §3.3.

`docs/m5o-test-scenarios-b.md` 의 알림 표(B10 · B11)와 링크 표(B12 · B13)다.

운영 DB 30일치의 알림 3,721건이 전부 `http://0.0.0.0:8787/...` 을 실어 보냈다 — `public_url` 이
비었고 서버가 `bind` 를 그대로 링크에 넣었기 때문이다. 다른 기기에서는 열리지 않는다. 그래서
`0.0.0.0` · `::` 은 **어떤 갈래에서도** 링크에 들어가지 않는다는 것을 표로 잠근다.
"""

from __future__ import annotations

import json
import re
import socket
from datetime import timedelta
from typing import Any

import pytest

from remote_ci_monitor import config as config_mod
from remote_ci_monitor.cli import main
from remote_ci_monitor.config import ServerSection, server_base_url
from remote_ci_monitor.core.model import FAILED, QUEUED, SUCCEEDED, Source
from remote_ci_monitor.core.notify import (
    ENV_TEXT_LIMIT,
    MAX_NOTIFY_FAILURES,
    notify_env,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.events import EventBus
from remote_ci_monitor.server import App
from remote_ci_monitor.store import Store
from test_notify import ALICE, NOW, FakeOpener, FakeRun, at, notifier_for, rule
from test_server import Server
from test_worker import make_config

ROW: dict[str, Any] = {"id": 7, "state": "failed", "preset": "gate", "url": "http://x/#/jobs/7"}


# ── B10: env ─────────────────────────────────────────────────────────────────


def test_b10_failures_are_newline_joined_in_the_order_the_job_printed_them():
    names = ["test/a/x_test.dart", "test/b/y_test.dart", "test"]
    env = notify_env(ROW, "hook", failures=names)
    assert env["RCM_FAILURES"] == "test/a/x_test.dart\ntest/b/y_test.dart\ntest"
    assert env["RCM_FAILURE_COUNT"] == "3"


def test_b10b_at_most_twenty_names_but_the_count_is_all_of_them():
    names = [f"t/u{i}_test.dart" for i in range(25)]
    env = notify_env(ROW, "hook", failures=names)
    assert MAX_NOTIFY_FAILURES == 20
    assert env["RCM_FAILURES"].split("\n") == names[:20]
    assert env["RCM_FAILURE_COUNT"] == "25"


def test_b10c_a_truncated_ledger_does_not_claim_an_exact_count():
    names = [f"u{i}" for i in range(100)]
    env = notify_env(ROW, "hook", failures=names, failures_truncated=True)
    assert env["RCM_FAILURE_COUNT"] == "100+"


def test_b10d_no_names_means_empty_strings_but_the_keys_are_there():
    env = notify_env(ROW, "hook")
    assert env["RCM_FAILURES"] == "" and env["RCM_FAILURE_COUNT"] == ""


def test_b10e_the_list_is_cut_on_a_line_and_fits_the_env_limit():
    names = [f"{i:02d}" + "회" * 118 for i in range(20)]  # 이름 하나가 ~356바이트, 20개면 7 KB
    value = notify_env(ROW, "hook", failures=names)["RCM_FAILURES"]
    assert len(value.encode()) <= ENV_TEXT_LIMIT
    lines = value.split("\n")
    assert lines == names[: len(lines)]  # 반쪽 줄이 없다
    assert len(lines) >= 10


def test_b8_control_characters_and_newlines_inside_a_name_are_dropped():
    env = notify_env(ROW, "hook", failures=["a\nb\x1b[31m", "c‮d", "\x00"])
    assert env["RCM_FAILURES"] == "ab[31m\ncd"
    assert env["RCM_FAILURE_COUNT"] == "3"  # 셈은 대장 행의 수다 — 지운 글자는 사실을 안 바꾼다


# ── B10 · B11: Notifier 가 대장을 읽어 넘긴다 ──────────────────────────────────


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, public_url="http://build.example:8787")
    cfg.presets = (config_mod.parse_preset({"name": "gate", "argv": ["true"]}),)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg, EventBus()
    store.close()


def failed_with_names(store: Store, names: list[str], *, truncated: bool = False, state=FAILED):
    created = NOW - timedelta(seconds=90)
    j = store.create_job(
        preset="gate",
        inputs={},
        key="gate",
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", base_sha="abc", dirty=False, tree_hash="t1"),
        requester=ALICE,
        timeout_seconds=60,
        join_key=join_key("gate", {}, "t1"),
        now=created,
        state=QUEUED,
    )
    assert store.claim(1, created + timedelta(seconds=10)).id == j.id
    store.finish(
        j.id,
        state,
        now=at(0),
        exit_code=1 if state == FAILED else 0,
        failed_step="test" if state == FAILED else None,
        fail_names=names,
        fail_truncated=truncated,
    )
    return store.get_job(j.id)


def test_b10_the_notifier_reads_the_ledger_in_seq_order(env):
    store, cfg, bus = env
    cfg.notify = (rule("hook", argv=("true",)),)
    run_ = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run_)
    job = failed_with_names(store, ["z_test.dart", "a_test.dart", "test"])
    assert store.failure_names(job.id) == ["z_test.dart", "a_test.dart", "test"]
    assert n.deliver(job) == 1
    e = run_.envs()[0]
    assert e["RCM_FAILURES"] == "z_test.dart\na_test.dart\ntest"  # 이름 순이 아니라 찍은 순서
    assert e["RCM_FAILURE_COUNT"] == "3"


def test_b14_a_green_job_has_empty_failure_variables(env):
    store, cfg, bus = env
    cfg.notify = (rule("hook", argv=("true",)),)
    run_ = FakeRun()
    n, _ = notifier_for(store, cfg, bus, run=run_)
    job = failed_with_names(store, [], state=SUCCEEDED)
    n.deliver(job)
    e = run_.envs()[0]
    assert e["RCM_FAILURES"] == "" and e["RCM_FAILURE_COUNT"] == ""


def test_b11_the_webhook_body_carries_the_failures(env):
    store, cfg, bus = env
    cfg.notify = (rule("hook", url="http://127.0.0.1:1/hook"),)
    opener = FakeOpener(status=204)
    n, _ = notifier_for(store, cfg, bus, run=FakeRun(), opener=opener)
    names = [f"t/u{i}_test.dart" for i in range(25)]
    job = failed_with_names(store, names, truncated=True)
    n.deliver(job)
    body = json.loads(opener.requests[0].data)
    assert body["failures"] == names[:20]
    assert body["failure_count"] == 25 and body["failures_truncated"] is True
    assert body["job"]["id"] == job.id  # 잡 행은 그대로다
    assert "failures" not in body["job"]  # `GET /jobs` 의 `failures[]`(객체 목록)와 섞지 않는다


# ── B12: 링크의 앞부분 ─────────────────────────────────────────────────────────


def section(**kw: Any) -> ServerSection:
    s = ServerSection()
    s.port = 8787
    for k, v in kw.items():
        setattr(s, k, v)
    return s


@pytest.mark.parametrize(
    ("kw", "hostname", "want"),
    [
        (
            {"public_url": "https://ci.example/", "bind": "0.0.0.0"},
            "macmini.lan",
            "https://ci.example",
        ),
        ({"bind": "100.64.0.5"}, "macmini.lan", "http://100.64.0.5:8787"),
        ({"bind": "127.0.0.1"}, "macmini.lan", "http://127.0.0.1:8787"),
        ({"bind": "0.0.0.0"}, "macmini.lan", "http://macmini.local:8787"),
        ({"bind": "0.0.0.0", "advertise": True}, "macmini", "http://macmini.local:8787"),
        ({"bind": "0.0.0.0", "advertise": False}, "macmini.lan", "http://macmini.lan:8787"),
        ({"bind": "::"}, "macmini.lan", "http://macmini.local:8787"),
        ({"bind": "::", "advertise": False}, "macmini.lan", "http://macmini.lan:8787"),
        ({"bind": ""}, "macmini.lan", "http://macmini.local:8787"),
        ({"bind": "fd00::5"}, "macmini.lan", "http://[fd00::5]:8787"),
        ({"bind": "0.0.0.0", "advertise": False}, "", "http://localhost:8787"),
        ({"bind": "0.0.0.0"}, "", "http://localhost:8787"),
    ],
    ids=[
        "public_url",
        "specific-bind",
        "loopback-bind",
        "any+mdns",
        "any+mdns-short-host",
        "any+no-advertise",
        "v6-any+mdns",
        "v6-any+no-advertise",
        "empty-bind",
        "v6-specific",
        "no-hostname",
        "no-hostname+mdns",
    ],
)
def test_b12_the_four_branches_never_yield_an_any_address(kw, hostname, want):
    got = server_base_url(section(**kw), hostname=hostname)
    assert got == want
    assert "0.0.0.0" not in got and "[::]" not in got and "//::" not in got


def _app(tmp_path, **server: Any) -> App:
    cfg = make_config(tmp_path, **server)
    return App(cfg, Store(cfg.data_dir / "rcm.sqlite3"))


def test_b12i_the_request_host_still_wins_on_the_server(tmp_path):
    app = _app(tmp_path, bind="0.0.0.0")
    try:
        assert app.base_url("10.0.0.5:8787") == "http://10.0.0.5:8787"
    finally:
        app.store.close()


@pytest.mark.parametrize("host", ["0.0.0.0:8787", "0.0.0.0", "[::]:8787", "[::]"])
def test_b12j_an_any_address_host_header_is_ignored(tmp_path, host):
    app = _app(tmp_path, bind="0.0.0.0", port=8787)
    try:
        got = app.base_url(host)
        assert "0.0.0.0" not in got and "[::]" not in got
        assert got == server_base_url(app.config.server, hostname=socket.gethostname())
    finally:
        app.store.close()


def test_b12_the_server_without_a_host_uses_the_same_helper(tmp_path):
    app = _app(tmp_path, bind="0.0.0.0", port=8787)
    try:
        assert app.base_url() == server_base_url(app.config.server, hostname=socket.gethostname())
    finally:
        app.store.close()


def test_b12k_notifier_links_never_carry_the_bind_address(tmp_path):
    cfg = make_config(tmp_path, bind="0.0.0.0", port=8787, public_url="")
    cfg.presets = (config_mod.parse_preset({"name": "gate", "argv": ["true"]}),)
    cfg.notify = (rule("hook", argv=("true",)),)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        run_ = FakeRun()
        n, _ = notifier_for(store, cfg, EventBus(), run=run_)
        n.deliver(failed_with_names(store, ["x"]))
        url = run_.envs()[0]["RCM_URL"]
        assert "0.0.0.0" not in url
        want = server_base_url(cfg.server, hostname=socket.gethostname())
        assert url.startswith(want + "/#/jobs/")
    finally:
        store.close()


# ── B13: rcm check ───────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). `test_cli_m4.run` 과 같다 — 그 모듈은 import 할 때
    `examples/` 를 읽어 mutcheck 사본(src · tests 만)에서 깨진다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


CHECK_TOML = """\
[server]
data_dir = "{data_dir}"
bind = "0.0.0.0"
port = 8792
{extra}
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
"""


def _check(tmp_path, monkeypatch, capsys, extra: str = "") -> tuple[int, str]:
    """`rcm check --config` 를 시험 서버에 대고 돈다. 클라이언트 주소를 **반드시** 준다 — 비우면
    `rcm check` 가 같은 네트워크를 뒤져(mDNS) 이 머신의 운영 서버를 찾아간다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    for var in ("XDG_CONFIG_HOME", "RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    srv = Server(tmp_path / "srv", workers=False)
    try:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{srv.port}")
        monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
        p = tmp_path / "server.toml"
        p.write_text(CHECK_TOML.format(data_dir=tmp_path / "data", extra=extra))
        code, out, _ = run(capsys, ["check", "--config", str(p)])
    finally:
        srv.close()
    return code, out


def _row(out: str, name: str) -> tuple[str, str] | None:
    m = re.search(rf"^(ok |warn|FAIL)  {re.escape(name)}\s+(.*)$", out, re.M)
    return (m.group(1).strip(), m.group(2)) if m else None


def test_b13_an_empty_public_url_is_a_warning_that_shows_the_link(tmp_path, monkeypatch, capsys):
    _, out = _check(tmp_path, monkeypatch, capsys)
    row = _row(out, "public url")
    assert row is not None, out
    label, detail = row
    host = socket.gethostname().split(".")[0] or "localhost"
    assert label == "warn", out
    assert detail == f"empty — links use http://{host}.local:8792 (set [server] public_url)"
    assert "0.0.0.0" not in detail


def test_b13b_a_set_public_url_is_ok(tmp_path, monkeypatch, capsys):
    _, out = _check(tmp_path, monkeypatch, capsys, extra='public_url = "https://ci.example/"')
    assert _row(out, "public url") == ("ok", "https://ci.example"), out


def test_b13_the_warning_does_not_fail_the_check(tmp_path, monkeypatch, capsys):
    """warn 은 종료 코드를 바꾸지 않는다 — 나머지가 다 ok 인 설치에서 `rcm check` 는 0 이다."""
    code, out = _check(tmp_path, monkeypatch, capsys)
    assert _row(out, "public url")[0] == "warn", out
    assert code == 0, out
