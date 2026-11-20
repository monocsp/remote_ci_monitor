"""따라가기가 사람에게 보이는 글자 (M5q PR A · 결정 166 · §2.9 · §14.8).

`docs/m5q-test-scenarios-c.md` 의 display 줄(CB13~CB21 · CE6~CE12 · CG11~CG21)을 옮긴다. 구현은
`docs/m5q-workplan.md` §14 가 시나리오의 「제안」을 이긴다. 잠그는 것:

- `rcm version` 의 첫 줄은 오늘과 같고 둘째 줄은 정보가 있을 때만 쓴다. `rcm --version` 은 한 줄.
  `--json` 은 기존 다섯 키에 `follow` 객체를 더한다. 서버에 묻지 않는다(소켓 0).
- `rcm check` 의 `client` 행은 `v<도는 버전> · <관계> · <꼬리>[ — <힌트>]` 이고 꼬리는 §14.8 표다.
- 처음 받을 때의 stderr 한 줄과 실패 경고 한 줄의 글자, 서버가 보낸 값의 제어 문자 제거.
- `_version_key` 의 PEP 440 순서와 「different build」.

기록과 캐시는 손으로 쓰지 않고 진짜 `follow.save_record` · `follow.fetch` 로 만든다 — 표시 코드가
실제 생산자의 결과를 읽어야 두 쪽이 같이 틀렸을 때 빨개진다.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

import remote_ci_monitor
import remote_ci_monitor.cli as cli
from follow_helpers import (
    INSTALLED,
    NEW,
    SHA_A,
    FakeServer,
    VersionedServer,
    closed_port,
    fake_wheel,
    health_doc,
)
from remote_ci_monitor import follow
from remote_ci_monitor.clientwheel import wheel_filename
from remote_ci_monitor.core import render_text

DEFAULT = "http://127.0.0.1:8791"
OTHER = "http://127.0.0.1:8790"
INVALID_HINT = "(use follow, installed or a version)"


class Exec(BaseException):
    """가짜 execve 가 던진다 — 돌아오지 않는 척. `except Exception` 에 잡히지 않는다."""


# ── 준비 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """HOME · cwd 를 tmp 로, 서버 · 토큰 변수는 없이. XDG 는 conftest 가 tmp 로 돌려 두었다."""
    h = tmp_path / "home"
    h.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in ("RCM_LABEL", "RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    return h


def xdg_config() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"])


def write_client_toml(server: str) -> None:
    p = xdg_config() / "rcm" / "client.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'server = "{server}"\n')


def write_home_pin(home: Path, text: str) -> Path:
    p = home / ".config" / "rcm" / "client-version"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def root() -> Path:
    r = follow.cache_root()
    r.mkdir(parents=True, exist_ok=True)
    return r


def record(server: str, version: str, *, checked_at: float, wheel: bytes | None = None) -> None:
    """진짜 `parse_health` → `save_record` 로 기록을 남긴다."""
    wheel = wheel if wheel is not None else fake_wheel(version)
    info = follow.parse_health(server, health_doc(version, wheel), now=checked_at)
    follow.save_record(root(), info, server=server, failed_at=None)


def cached(version: str, last_used: float, *, variant: str | None = None) -> Path:
    """진짜 `fetch` 로 캐시 항목을 만들고 마지막 사용 시각을 표지 mtime 으로 맞춘다."""
    extra = ()
    if variant is not None:
        extra = (
            ("remote_ci_monitor/_variant.txt", variant.encode(), (stat.S_IFREG | 0o644) << 16),
        )
    wheel = fake_wheel(version, extra=extra)
    srv = FakeServer(version, wheel)
    try:
        info = follow.parse_health(srv.key, srv.health, now=time.time())
        entry = follow.fetch(srv.key, None, info, root=root())
    finally:
        srv.close()
    os.utime(entry.path / ".rcm-client.json", (last_used, last_used))
    return entry.path


@pytest.fixture
def no_network(monkeypatch) -> list:
    """모든 소켓 연결과 mDNS 발견을 실패로 만든다 — 「서버에 묻지 않는다」를 증명한다."""
    seen: list = []

    def refuse(self, *a, **k):
        seen.append(a)
        raise AssertionError("rcm version must not open a connection")

    def no_discover(*a, **k):
        seen.append(("discover", a))
        raise AssertionError("rcm version must not discover")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(cli, "discover", no_discover)
    return seen


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` — 따라가지 않는 진입. argparse 의 SystemExit 도 코드로."""
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def run_follow(monkeypatch, capsys, argv: list[str], *, kind: str = "wheel"):
    """콘솔 스크립트처럼 따라가는 진입 — `sys.argv` 와 설치 종류를 주입한다."""
    calls: list = []

    def fake_execve(path, args, env):
        calls.append((path, list(args), dict(env)))
        raise Exec()

    monkeypatch.setattr(sys, "argv", ["rcm", *argv])
    monkeypatch.setattr(follow, "install_kind", lambda: kind)
    try:
        code = cli._main(None, follow_enabled=True, execve=fake_execve)
    except Exec:
        code = "exec"
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err, calls


def use(monkeypatch, url: str, token: str | None = None) -> None:
    monkeypatch.setenv("RCM_SERVER", url)
    if token is None:
        monkeypatch.delenv("RCM_TOKEN", raising=False)
    else:
        monkeypatch.setenv("RCM_TOKEN", token)


def set_versions(monkeypatch, version: str) -> None:
    """설치본(또는 따라간 자식의 도는 코드) 버전을 바꾼다 — 어느 모듈이 읽어도 같게."""
    monkeypatch.setattr(remote_ci_monitor, "__version__", version)
    monkeypatch.setattr(cli, "__version__", version)
    monkeypatch.setattr(follow, "__version__", version, raising=False)


def as_child(monkeypatch, running: str, installed: str) -> None:
    """따라간 자식 흉내 — BOOT 가 남기는 표지와 도는 버전."""
    set_versions(monkeypatch, running)
    monkeypatch.setattr(
        remote_ci_monitor,
        "_follow_boot",
        {
            "RCM_FOLLOWED": running,
            "RCM_FOLLOWED_PATH": "/nowhere/cache/clients/0123456789abcdef",
            "RCM_FOLLOWED_FROM": installed,
            "RCM_FOLLOW_FROM": "wheel",
            "RCM_FOLLOW_FOUND": None,
            "loaded": True,
        },
        raising=False,
    )


def client_row(out: str) -> str:
    rows = [ln for ln in out.splitlines() if re.match(r"^(ok |warn|FAIL)  client ", ln)]
    assert len(rows) == 1, out
    return rows[0]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def wheel_url(base: str, version: str) -> str:
    return f"{base}/client/{wheel_filename(version)}"


@pytest.fixture
def vservers(tmp_path):
    made: list[VersionedServer] = []

    def make(version: str, wheel: bytes | None | str = "fake", **kw) -> VersionedServer:
        w = fake_wheel(version) if wheel == "fake" else wheel
        s = VersionedServer(tmp_path, version, w, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


@pytest.fixture
def fakes():
    made: list[FakeServer] = []

    def make(*a, **kw) -> FakeServer:
        s = FakeServer(*a, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


# ── rcm version ─────────────────────────────────────────────────────────────


def test_cb13_version_is_one_line_without_information(home, capsys, monkeypatch, no_network):
    """CB13: 기록도 설정도 없으면 한 줄, stderr 빔."""
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    code, out, err = run(capsys, ["version"])
    assert code == 0 and err == ""
    assert out.count("\n") == 1 and out.startswith(f"rcm {INSTALLED} (Python "), out
    assert no_network == []


def test_cb14_second_line_with_a_record_and_cache(home, capsys, monkeypatch):
    """CB14: 기본 서버의 기록 · 캐시 둘 — 둘째 줄의 글자 전부."""
    now = int(time.time())
    cached("0.8.1", now - 86400)
    cached("0.8.0", now - 7 * 86400)
    record(DEFAULT, "0.8.1", checked_at=now - 180)
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    code, out, err = run(capsys, ["version"])
    assert code == 0, err
    first, second = out.splitlines()
    assert first.startswith(f"rcm {INSTALLED} (Python ")
    assert second == (
        f"follow: on (default) · server {DEFAULT} v0.8.1 checked 3m ago · cached 0.8.1, 0.8.0"
    )


def _refuse(self, *a, **k):
    raise AssertionError("no connection allowed")


def test_cb15_editable_default_stays_one_line_even_with_a_record(home, capsys, monkeypatch):
    """CB15 (§14.8 A질문3 이 이긴다): editable 기본값이면 기록이 있어도 한 줄."""
    record(DEFAULT, "0.8.1", checked_at=time.time() - 180)
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "editable")
    code, out, err = run(capsys, ["version"])
    assert code == 0 and err == ""
    assert out.count("\n") == 1, out


def test_cb16_pinned_by_file_and_by_env(home, capsys, monkeypatch):
    """CB16: 고정 둘 — 파일이면 홈을 `~` 로 줄인 경로, 환경 변수면 그 이름."""
    cached("0.8.0", time.time() - 60)
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    write_home_pin(home, "0.8.0\n")
    code, out, _ = run(capsys, ["version"])
    assert code == 0
    assert (
        out.splitlines()[1] == "follow: pinned 0.8.0 (~/.config/rcm/client-version) · cached 0.8.0"
    )
    (home / ".config" / "rcm" / "client-version").unlink()
    monkeypatch.setenv("RCM_CLIENT_VERSION", "0.8.0")
    code, out, _ = run(capsys, ["version"])
    assert out.splitlines()[1] == "follow: pinned 0.8.0 (RCM_CLIENT_VERSION) · cached 0.8.0"


def test_pinned_without_cache_has_no_cached_segment(home, capsys, monkeypatch):
    """§14.8: 캐시가 비었으면 `· cached` 토막을 뺀다."""
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    write_home_pin(home, "0.8.0  # pinned\n")
    code, out, _ = run(capsys, ["version"])
    assert out.splitlines()[1] == "follow: pinned 0.8.0 (~/.config/rcm/client-version)"


@pytest.mark.parametrize(
    ("how", "line"),
    [
        ("env", "follow: off (RCM_CLIENT_VERSION)"),
        ("file", "follow: off (~/.config/rcm/client-version)"),
    ],
)
def test_off_by_setting_is_said(home, capsys, monkeypatch, how, line):
    """§14.8: 사람이 적은 `installed` 는 기록이 없어도 둘째 줄로 말한다."""
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    if how == "env":
        monkeypatch.setenv("RCM_CLIENT_VERSION", "installed")
    else:
        write_home_pin(home, "installed\n")
    code, out, _ = run(capsys, ["version"])
    assert code == 0 and out.splitlines()[1] == line


def test_on_by_env_names_the_variable(home, capsys, monkeypatch):
    """§14.8: `follow: on (RCM_CLIENT_VERSION) · server … checked …`."""
    now = int(time.time())
    record(DEFAULT, "0.8.1", checked_at=now - 180)
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "editable")
    monkeypatch.setenv("RCM_CLIENT_VERSION", "follow")
    code, out, _ = run(capsys, ["version"])
    assert out.splitlines()[1] == (
        f"follow: on (RCM_CLIENT_VERSION) · server {DEFAULT} v0.8.1 checked 3m ago"
    )


def test_cb17_dash_dash_version_is_always_one_line(home, capsys, monkeypatch):
    """CB17: `rcm --version` 은 CB14 · CB16 상태에서도 설치본 한 줄."""
    now = time.time()
    record(DEFAULT, "0.8.1", checked_at=now - 180)
    write_client_toml(DEFAULT)
    write_home_pin(home, "0.8.0\n")
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    code, out, _ = run(capsys, ["--version"])
    assert code == 0 and out == f"rcm {INSTALLED}\n"


def test_cb18_version_json_follow_object(home, capsys, monkeypatch):
    """CB18 (+ §14.8 C질문9 의 `installed` · `running`): 기존 키는 그대로, `follow` 를 더한다."""
    now = int(time.time())
    checked = now - 180
    cached("0.8.1", now - 86400)
    cached("0.8.0", now - 7 * 86400)
    record(DEFAULT, "0.8.1", checked_at=checked)
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    code, out, err = run(capsys, ["version", "--json"])
    assert code == 0, err
    assert out.count("\n") == 1
    doc = json.loads(out)
    assert set(doc) == {"version", "python", "platform", "machine", "schema_version", "follow"}
    assert doc["version"] == INSTALLED and "server" not in doc
    assert doc["follow"] == {
        "mode": "follow",
        "pin": None,
        "source": "default",
        "install": "wheel",
        "server": DEFAULT,
        "server_version": "0.8.1",
        "checked_at": iso(checked),
        "cached": ["0.8.1", "0.8.0"],
        "installed": INSTALLED,
        "running": "0.8.1",  # 기록의 sha 와 맞는 캐시가 있다
    }


def test_version_json_follow_is_always_there(home, capsys, monkeypatch, no_network):
    """§14.8 C질문9: 정보가 없어도 `follow` 는 있고 모르는 값은 null."""
    monkeypatch.setattr(follow, "install_kind", lambda: "editable")
    code, out, _ = run(capsys, ["version", "--json"])
    f = json.loads(out)["follow"]
    assert f["mode"] == "installed" and f["source"] == "default" and f["install"] == "editable"
    assert f["server"] is None and f["server_version"] is None and f["checked_at"] is None
    assert f["cached"] == [] and f["running"] == INSTALLED and f["installed"] == INSTALLED


@pytest.mark.parametrize("how", ["env", "file"])
def test_ce11_invalid_setting_is_a_second_line_and_exit_0(home, capsys, monkeypatch, how):
    """CE11 (§14.3 C질문10): `rcm version` 은 설정이 틀려도 종료 0, 둘째 줄이 무엇이 틀렸는지."""
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    if how == "env":
        monkeypatch.setenv("RCM_CLIENT_VERSION", "lates")
        want = f"follow: invalid RCM_CLIENT_VERSION 'lates' {INVALID_HINT}"
    else:
        write_home_pin(home, "lates\n")
        want = f"follow: invalid ~/.config/rcm/client-version 'lates' {INVALID_HINT}"
    code, out, _ = run(capsys, ["version"])
    assert code == 0
    assert out.splitlines()[1] == want


def test_ce11_following_command_still_exits_2(home, capsys, monkeypatch):
    """CE11: 따라가는 명령은 틀린 설정에서 종료 2 와 ConfigError 문장."""
    monkeypatch.setenv("RCM_CLIENT_VERSION", "lates")
    use(monkeypatch, f"http://127.0.0.1:{closed_port()}")
    code, _, err, calls = run_follow(monkeypatch, capsys, ["jobs"])
    assert code == 2 and calls == []
    assert "RCM_CLIENT_VERSION 'lates' is not follow, installed or a version" in err


@pytest.mark.parametrize(
    "content",
    [
        "{",
        json.dumps({DEFAULT: {"version": 7, "checked_at": 1.0, "failed_at": None}}),
        json.dumps([1, 2]),
    ],
)
def test_ce12_broken_record_is_no_record(home, capsys, monkeypatch, content):
    """CE12: 깨진 기록 파일은 트레이스백 없이 「기록 없음」과 같다."""
    (root() / "follow.json").write_text(content)
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    code, out, err = run(capsys, ["version"])
    assert code == 0 and "Traceback" not in err
    assert out.count("\n") == 1, out
    code, out, _ = run(capsys, ["version", "--json"])
    assert code == 0 and json.loads(out)["follow"]["server_version"] is None


def test_cg11_another_servers_record_is_not_shown(home, capsys, monkeypatch):
    """CG11: 기본 서버(8791)의 항목이 없으면 둘째 줄이 없다."""
    record(OTHER, "0.8.1", checked_at=time.time() - 60)
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    code, out, _ = run(capsys, ["version"])
    assert code == 0 and out.count("\n") == 1, out


def test_cg12_auto_server_never_discovers(home, capsys, monkeypatch, no_network):
    """CG12: 기본 서버가 `auto` 면 발견하지 않고 서버 토막도 없다 — 기본 설정이면 한 줄."""
    record(DEFAULT, "0.8.1", checked_at=time.time() - 60)
    write_client_toml("auto")
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    code, out, err = run(capsys, ["version"])
    assert code == 0 and out.count("\n") == 1, out + err
    assert no_network == []


def test_cg13_ages_and_the_failure_record(home, capsys, monkeypatch):
    """CG13: `checked 5s ago` · `checked 2h ago` · 실패 기록은 `unreachable … (last seen …)`."""
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    now = time.time()
    record(DEFAULT, "0.8.1", checked_at=now - 5)
    _, out, _ = run(capsys, ["version"])
    assert re.fullmatch(
        rf"follow: on \(default\) · server {re.escape(DEFAULT)} v0\.8\.1 checked [56]s ago",
        out.splitlines()[1],
    ), out
    record(DEFAULT, "0.8.1", checked_at=now - 7200)
    _, out, _ = run(capsys, ["version"])
    assert out.splitlines()[1] == f"follow: on (default) · server {DEFAULT} v0.8.1 checked 2h ago"
    follow.save_record(root(), None, server=DEFAULT, failed_at=now - 30)
    _, out, _ = run(capsys, ["version"])
    assert re.fullmatch(
        rf"follow: on \(default\) · server {re.escape(DEFAULT)} unreachable 3[01]s ago "
        r"\(last seen v0\.8\.1 2h ago\)",
        out.splitlines()[1],
    ), out


def test_cg14_cached_list_order_and_dedupe(home, capsys, monkeypatch):
    """CG14: 같은 버전은 한 번, 마지막 사용이 최근인 순. 찌꺼기와 표지 없는 폴더는 세지 않는다."""
    now = time.time()
    cached("0.8.1", now - 3000, variant="a")
    cached("0.8.1", now - 9000, variant="b")
    cached("0.8.0", now - 60)
    clients = root() / "clients"
    for junk in (".tmp-x", ".trash-y", "0.7.9-cccc"):
        (clients / junk).mkdir()
        (clients / junk / "x").write_text("x")
    record(DEFAULT, "0.8.0", checked_at=now - 180)
    write_client_toml(DEFAULT)
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    _, out, _ = run(capsys, ["version"])
    assert out.splitlines()[1].endswith(" · cached 0.8.0, 0.8.1"), out
    _, out, _ = run(capsys, ["version", "--json"])
    assert json.loads(out)["follow"]["cached"] == ["0.8.0", "0.8.1"]


# ── rcm check 의 client 행 ─────────────────────────────────────────────────


def test_cb19_followed_child(home, capsys, monkeypatch, vservers):
    """CB19 (가): 따라간 자식은 서버와 같고 꼬리가 설치본 버전을 말한다."""
    vs = vservers(NEW)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    as_child(monkeypatch, NEW, INSTALLED)
    code, out, err, calls = run_follow(monkeypatch, capsys, ["check"])
    assert calls == [] and vs.client_count == 0
    assert client_row(out) == (
        f"ok   client        v{NEW} · same as server · following (installed v{INSTALLED})"
    )


def test_cb19_cg21_sha_mismatch_row_and_one_warning(home, capsys, monkeypatch, vservers):
    """CB19 (나) · CG21: 실패는 stderr 경고 한 줄과 꼬리, 이유 문구가 같다. 힌트는 남는다."""
    vs = vservers(NEW)
    vs.app._wheel = (vs.app._wheel[0], SHA_A)  # health 의 sha 가 실제 바이트와 다르다
    use(monkeypatch, vs.url, vs.tokens["alice"])
    code, out, err, calls = run_follow(monkeypatch, capsys, ["check"])
    assert calls == []
    assert client_row(out) == (
        f"warn  client        v{INSTALLED} · server v{NEW} · older · not following: "
        f"sha256 mismatch — pip install {wheel_url(vs.url, NEW)}"
    )
    warn = f"rcm: not following server v{NEW}: sha256 mismatch — running installed v{INSTALLED}"
    assert err.splitlines().count(warn) == 1, err
    assert err.count("not following") == 1, err


def test_cb19_pinned_by_env(home, capsys, monkeypatch, vservers):
    """CB19 (다): 고정은 사람이 고른 상태 — 힌트를 뺀다."""
    vs = vservers(NEW)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    monkeypatch.setenv("RCM_CLIENT_VERSION", INSTALLED)
    code, out, err, calls = run_follow(monkeypatch, capsys, ["check"])
    assert calls == [] and vs.client_count == 0
    assert client_row(out) == (
        f"warn  client        v{INSTALLED} · server v{NEW} · older · pinned by RCM_CLIENT_VERSION"
    )


def test_cb19_editable_same_version(home, capsys, monkeypatch, vservers):
    """CB19 (라): editable 기본값은 따라가지 않는다고 말한다."""
    vs = vservers(INSTALLED)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    code, out, err, calls = run_follow(monkeypatch, capsys, ["check"], kind="editable")
    assert calls == [] and err == ""
    assert client_row(out) == (
        f"ok   client        v{INSTALLED} · same as server · not following (editable install)"
    )


def test_ce8_follow_failure_below_the_floor_is_fail(home, capsys, monkeypatch, vservers):
    """CE8: 판정은 오늘처럼 설치본 버전으로 — 바닥 아래면 FAIL, 종료 1."""
    set_versions(monkeypatch, "0.1.9")
    vs = vservers(NEW)
    vs.app._wheel = (vs.app._wheel[0], SHA_A)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    code, out, err, calls = run_follow(monkeypatch, capsys, ["check"])
    assert code == 1 and calls == []
    assert client_row(out) == (
        f"FAIL  client        v0.1.9 · server v{NEW} · older · not following: "
        f"sha256 mismatch — pip install {wheel_url(vs.url, NEW)}"
    )


def test_ce9_no_wheel_tail_without_a_second_hint(home, capsys, monkeypatch, vservers):
    """CE9: 꼬리가 이미 이유를 말하므로 `— no client wheel …` 힌트를 겹치지 않는다."""
    vs = vservers(NEW, wheel=None)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    code, out, err, calls = run_follow(monkeypatch, capsys, ["check"])
    assert calls == []
    assert client_row(out) == (
        f"warn  client        v{INSTALLED} · server v{NEW} · older · not following: "
        "the server has no client wheel (metadata_missing)"
    )
    warn = (
        f"rcm: not following server v{NEW}: the server has no client wheel (metadata_missing)"
        f" — running installed v{INSTALLED}"
    )
    assert warn in err.splitlines(), err


def test_ce10_old_server_tail(home, capsys, monkeypatch, fakes):
    """CE10: health 에 `client_wheel` 키가 없는 옛 서버 — 꼬리 하나, 힌트 없음."""
    old = fakes(health={"ok": True, "version": NEW, "min_client_version": "0.2.0"})
    use(monkeypatch, old.url, "tok")
    code, out, err, calls = run_follow(monkeypatch, capsys, ["check"])
    assert calls == [] and old.client_count == 0
    assert client_row(out) == (
        f"warn  client        v{INSTALLED} · server v{NEW} · older · not following: "
        "the server is too old to hand out its client"
    )


@pytest.mark.parametrize(
    ("argv", "env", "pin", "kind", "tail"),
    [
        (["check", "--no-follow"], None, None, "wheel", "not following (--no-follow)"),
        (["--no-follow", "check"], None, None, "wheel", "not following (--no-follow)"),
        (["check"], "installed", None, "wheel", "not following (RCM_CLIENT_VERSION=installed)"),
        (["check"], None, "installed", "wheel", "not following (~/.config/rcm/client-version)"),
        (["check"], None, None, "source", "not following (source install)"),
        (["check"], None, INSTALLED, "wheel", "pinned by ~/.config/rcm/client-version"),
        (["check"], INSTALLED, None, "wheel", "pinned by RCM_CLIENT_VERSION"),
    ],
)
def test_cg15_the_other_tails(home, capsys, monkeypatch, vservers, argv, env, pin, kind, tail):
    """CG15 (§14.8 꼬리 표): `--no-follow` · 환경 변수 · 한 줄 파일 · source · 파일 고정."""
    vs = vservers(INSTALLED)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    if env is not None:
        monkeypatch.setenv("RCM_CLIENT_VERSION", env)
    if pin is not None:
        write_home_pin(home, pin + "\n")
    code, out, err, calls = run_follow(monkeypatch, capsys, argv, kind=kind)
    assert calls == [] and vs.client_count == 0
    assert client_row(out) == f"ok   client        v{INSTALLED} · same as server · {tail}"


def test_cg16_following_clears_the_floor(home, capsys, monkeypatch, vservers):
    """CG16: 도는 코드가 서버 버전이므로 설치본이 바닥 아래여도 FAIL 이 아니다."""
    vs = vservers(NEW)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    as_child(monkeypatch, NEW, "0.1.9")
    code, out, _, calls = run_follow(monkeypatch, capsys, ["check"])
    assert calls == []
    assert client_row(out) == (
        f"ok   client        v{NEW} · same as server · following (installed v0.1.9)"
    )
    assert code == 0, out


def test_cg17_newer_install_followed_down(home, capsys, monkeypatch, vservers):
    """CG17: 서버보다 새 설치본이 내려간 자식도 같은 꼴."""
    vs = vservers(NEW)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    as_child(monkeypatch, NEW, "99.0.3")
    _, out, _, _ = run_follow(monkeypatch, capsys, ["check"])
    assert client_row(out) == (
        f"ok   client        v{NEW} · same as server · following (installed v99.0.3)"
    )


def test_cg19_main_argv_keeps_todays_row(home, capsys, monkeypatch, vservers):
    """CG19: `main(argv)` 는 따라가지 않으므로 꼬리가 없다 — 오늘과 같은 글자로 끝난다."""
    vs = vservers(INSTALLED)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    code, out, _ = run(capsys, ["check"])
    assert re.search(rf"^ok   client\s+v{re.escape(INSTALLED)} · same as server$", out, re.M), out


@pytest.mark.parametrize("server_v", [f"{INSTALLED}.0", f"{INSTALLED}+local.1"])
def test_different_build_is_a_warning(home, capsys, monkeypatch, vservers, server_v):
    """§14.8 C질문5: 문자열은 다른데 크기가 같으면 `different build` 로 warn."""
    vs = vservers(server_v)
    use(monkeypatch, vs.url, vs.tokens["alice"])
    code, out, _ = run(capsys, ["check"])
    assert client_row(out) == (
        f"warn  client        v{INSTALLED} · server v{server_v} · different build"
    )


def test_version_key_knows_pep440_order():
    """§14.8 C질문5: dev < a < rc < 최종 < post, `+local` 은 크기 비교에서 뺀다."""
    order = ["0.7.0.dev0", "0.7.0.dev1", "0.7.0a1", "0.7.0b2", "0.7.0rc1", "0.7.0"]
    order += ["0.7.0.post1", "0.7.1"]
    keys = [cli._version_key(v) for v in order]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    assert cli._version_key("0.7") == cli._version_key("0.7.0")
    assert cli._version_key("0.7.0+local.1") == cli._version_key("0.7.0")
    assert cli._version_key("0.2.0") < cli._version_key("0.2.7") < cli._version_key("0.10.0")


# ── stderr 한 줄들 ──────────────────────────────────────────────────────────


def test_cb20_first_fetch_line(home, capsys, monkeypatch, fakes):
    """CB20: 처음 받을 때 stderr 정확히 한 줄, 크기는 `render_text._bytes` 눈금. stdout 빔."""
    srv = fakes(NEW)
    use(monkeypatch, srv.url, "tok")
    code, out, err, calls = run_follow(monkeypatch, capsys, ["jobs"])
    assert code == "exec" and len(calls) == 1
    size = render_text._bytes(len(srv.wheel))
    assert err == f"rcm: following server v{NEW} (installed v{INSTALLED}) — fetched {size}\n"
    assert out == ""


@pytest.mark.parametrize("n", [0, 999, 1000, 707_673, 999_949, 1_000_000, 64 * 1024 * 1024])
def test_fetched_size_uses_the_same_scale_as_render_text(n):
    """§14.8 C질문12: `follow.fmt_size` 와 `render_text._bytes` 가 같은 글자를 낸다."""
    assert follow.fmt_size(n) == render_text._bytes(n)
    assert follow.fmt_size(707_673) == "707.7 KB"


@pytest.mark.parametrize("bad", ["\x1b]0;evil\x07", "../x", "1" * 65])
def test_ce6_bad_health_warning_has_no_server_bytes(home, capsys, monkeypatch, fakes, bad):
    """CE6: `bad_health` 경고는 키 이름만 말하고 서버가 보낸 값을 찍지 않는다."""
    srv = fakes(health={"ok": True, "version": bad, "client_wheel": None})
    use(monkeypatch, srv.url, "tok")
    _, out, err, calls = run_follow(monkeypatch, capsys, ["jobs"])
    assert calls == [] and srv.client_count == 0
    want = (
        f"rcm: not following {srv.key}: the server's health has a malformed version "
        f"— running installed v{INSTALLED}"
    )
    assert want in err.splitlines(), err
    assert "\x1b" not in err and "\x07" not in err and bad not in err + out


def test_ce7_unknown_server_version_names_the_address(home, capsys, monkeypatch):
    """CE7: 서버 버전을 모르면 `v` 뒤에 빈 값이나 None 을 찍지 않고 주소를 쓴다."""
    port = closed_port()
    use(monkeypatch, f"http://127.0.0.1:{port}", "tok")
    _, _, err, calls = run_follow(monkeypatch, capsys, ["jobs"])
    assert calls == []
    want = (
        f"rcm: not following http://127.0.0.1:{port}: cannot reach the server "
        f"— running installed v{INSTALLED}"
    )
    assert err.splitlines()[0] == want, err
    assert "vNone" not in err and "server v " not in err


# ── 플래그 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("argv", [["--help"], ["run", "--help"], ["jobs", "--help"]])
def test_cb21_no_follow_is_in_the_help(home, capsys, argv):
    """CB21: `--no-follow` 가 도움말에 설명과 함께 있다(설치본의 도움말)."""
    code, out, _ = run(capsys, argv)
    assert code == 0
    assert re.search(r"--no-follow\s+\S", out), out


@pytest.mark.parametrize(
    ("argv", "ok"),
    [
        (["--no-follow", "jobs"], True),
        (["jobs", "--no-follow"], True),
        (["--no-follow", "skills", "list"], True),
        (["skills", "list", "--no-follow"], False),
    ],
)
def test_cg20_no_follow_positions(argv, ok):
    """CG20: 최상위와 하위 명령 뒤 둘 다 받는다. `skills` 에는 `client_opts` 가 없다(§14.2)."""
    parser = cli.build_parser()
    if ok:
        args = parser.parse_args(argv)
        assert getattr(args, "no_follow", False) is True
    else:
        with pytest.raises(SystemExit) as e:
            parser.parse_args(argv)
        assert e.value.code == 2


def test_no_follow_defaults_false_and_subparser_does_not_clobber():
    """§5.2: 하위 파서의 `SUPPRESS` 가 최상위 값을 덮지 않는다."""
    parser = cli.build_parser()
    assert parser.parse_args(["jobs"]).no_follow is False
    assert parser.parse_args(["--no-follow", "jobs"]).no_follow is True
