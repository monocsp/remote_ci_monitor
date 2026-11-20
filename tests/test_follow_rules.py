"""따라가기(M5q PR A)의 순수 규칙 — 시나리오 a 의 훑기 · 설정 · health · 목표 표.

시계 · 파일 · 네트워크를 보지 않는 함수만 여기서 잠근다(`prescan` · `follows` · `resolve_setting` ·
`setting_for` · `read_pin_file` · `install_kind` · `parse_health` · `normalize_server` · `plan` ·
`python_ok` · `cache_root` · 정규식과 상수 · 이유 문구). 명세는 `docs/m5q-workplan.md` §2 와
§14 이고 §14 가 앞 절과 시나리오(`docs/m5q-test-scenarios-a.md`)의 「제안」을 이긴다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path

import pytest

import remote_ci_monitor
from follow_helpers import INSTALLED, NEW, NEW2, OLD, SHA_A, SHA_B, T, fake_wheel, health_doc
from remote_ci_monitor import clientwheel, follow
from remote_ci_monitor.config import ConfigError
from remote_ci_monitor.core.render_text import _bytes

KEY = "http://127.0.0.1:8790"

# ── 훑기 ─────────────────────────────────────────────────────────────────────

#: 설치본 파서의 긴 옵션 — 시험이 줄임 규칙을 정확히 보도록 손으로 적은 판
CLIENT = {"--help", "--server", "--token", "--client-config", "--no-follow"}
OPTS: dict[str | None, frozenset[str]] = {
    None: frozenset({"--help", "--version", "--no-follow"}),
    "jobs": frozenset(CLIENT | {"--mine", "--state", "--pool", "--ref", "--json"}),
    "gc": frozenset(CLIENT | {"--dry-run", "--json", "--timeout", "--config"}),
    "logs": frozenset(CLIENT | {"--follow"}),
    "run": frozenset(
        CLIENT
        | {"--source", "--ref", "--no-cache", "--no-join", "--no-supersede", "--no-wait", "--by"}
    ),
    "check": frozenset(CLIENT | {"--config"}),
}


def parser_options() -> dict[str | None, frozenset[str]]:
    """`cli.build_parser()` 에서 뽑은 긴 옵션 목록 — 명령 아래 하위 파서의 것까지 합친다."""
    from remote_ci_monitor.cli import build_parser

    def longs(p: argparse.ArgumentParser) -> set[str]:
        out: set[str] = set()
        for a in p._actions:
            out.update(o for o in a.option_strings if o.startswith("--"))
        return out

    def nested(p: argparse.ArgumentParser) -> set[str]:
        out = longs(p)
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                for sp in a.choices.values():
                    out |= nested(sp)
        return out

    top = build_parser()
    opts: dict[str | None, frozenset[str]] = {None: frozenset(longs(top))}
    for a in top._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                opts[name] = frozenset(nested(sp))
    return opts


def test_prescan_basics():  # A17
    p = follow.prescan(["jobs"], options=OPTS)
    assert p.command == "jobs" and not p.no_follow and not p.wants_help
    assert p.server is None and p.token is None and p.client_config is None
    p = follow.prescan(["--no-follow", "run", "gate", "-f", "a=1"], options=OPTS)
    assert p.command == "run" and p.no_follow
    argv = ["jobs", "--server", "http://h:1", "--token", "T", "--client-config", "c.toml"]
    p = follow.prescan(argv, options=OPTS)
    assert (p.command, p.server, p.token, p.client_config) == ("jobs", "http://h:1", "T", "c.toml")
    argv = ["jobs", "--server=http://h:1", "--token=T", "--client-config=c.toml"]
    p = follow.prescan(argv, options=OPTS)
    assert (p.command, p.server, p.token, p.client_config) == ("jobs", "http://h:1", "T", "c.toml")
    p = follow.prescan(["gc", "--config", "s.toml", "--dry-run"], options=OPTS)
    assert p.command == "gc" and p.has_gc_config
    assert not follow.prescan(["gc", "--dry-run"], options=OPTS).has_gc_config
    p = follow.prescan(["run", "-h"], options=OPTS)
    assert p.wants_help


@pytest.mark.parametrize("options", [OPTS, None])
def test_prescan_values_and_stops(options):  # A79 · R10
    assert follow.prescan(["jobs", "--server=URL"], options=options).server == "URL"
    assert follow.prescan(["jobs", "--server", "URL"], options=options).server == "URL"
    assert follow.prescan(["jobs", "--token=T"], options=options).token == "T"
    assert follow.prescan(["jobs", "--client-config", "P"], options=options).client_config == "P"
    # `--` 뒤는 플래그가 아니다
    p = follow.prescan(["jobs", "--", "--no-follow", "--server", "X"], options=options)
    assert not p.no_follow and p.server is None
    # 다른 옵션의 값은 플래그로 보지 않는다
    p = follow.prescan(["run", "gate", "-f", "x=--server"], options=options)
    assert p.server is None
    # 값을 받는 옵션은 다음 토큰을 먹는다 — `-h` 라도
    p = follow.prescan(["jobs", "--server", "-h"], options=options)
    assert p.server == "-h" and not p.wants_help


def test_prescan_logs_follow_is_not_no_follow():  # A97
    p = follow.prescan(["logs", "412", "--follow"], options=OPTS)
    assert p.command == "logs" and not p.no_follow
    assert follow.follows(p)


def test_prescan_unique_prefix_like_argparse():  # A119 · §14 A질문2
    p = follow.prescan(["jobs", "--serv", "http://B"], options=OPTS)
    assert p.server == "http://B"
    assert follow.prescan(["jobs", "--serv=http://B"], options=OPTS).server == "http://B"
    assert follow.prescan(["jobs", "--tok", "T"], options=OPTS).token == "T"
    assert follow.prescan(["jobs", "--client", "c.toml"], options=OPTS).client_config == "c.toml"
    assert follow.prescan(["jobs", "--no-f"], options=OPTS).no_follow
    assert follow.prescan(["--no", "jobs"], options=OPTS).no_follow  # 최상위 `--no` → --no-follow
    p = follow.prescan(["jobs", "--he"], options=OPTS)
    assert p.wants_help and not follow.follows(p)
    assert follow.prescan(["gc", "--conf", "s.toml", "--dry-run"], options=OPTS).has_gc_config
    assert follow.prescan(["--ver"], options=OPTS).wants_help


def test_prescan_ambiguous_prefix_does_not_follow():  # A119 · §14 A질문2
    # run 에는 --no-cache · --no-join · --no-supersede · --no-wait · --no-follow 가 있다
    p = follow.prescan(["run", "gate", "--no-"], options=OPTS)
    assert p.ambiguous and not follow.follows(p)
    # gc 의 `--c` 는 --config 와 --client-config 둘 다다
    p = follow.prescan(["gc", "--c", "x"], options=OPTS)
    assert p.ambiguous and not follow.follows(p)


def test_prescan_with_the_real_parser():  # A119 — 설치본 파서에서 뽑은 목록으로도 같다
    opts = parser_options()
    assert "--server" in opts["jobs"] and "--follow" in opts["logs"]
    assert follow.prescan(["jobs", "--serv", "http://B"], options=opts).server == "http://B"
    assert follow.prescan(["logs", "1", "--follow"], options=opts).no_follow is False
    p = follow.prescan(["shelf", "get", "1", "--output", "d", "--server", "S"], options=opts)
    assert p.command == "shelf" and p.server == "S"


def test_prescan_no_command():  # A120
    for argv in ([], ["--no-follow"]):
        p = follow.prescan(argv, options=OPTS)
        assert p.command is None
        assert not follow.follows(p)
    assert follow.prescan(["--no-follow"], options=OPTS).no_follow


def test_prescan_server_before_the_command_is_not_a_value():  # A121
    p = follow.prescan(["--server", "http://B", "jobs"], options=OPTS)
    assert p.server is None


def test_prescan_never_raises_on_odd_input():  # A17 「예외는 없다」
    for argv in (["--"], ["-"], ["--="], ["jobs", "--server"], ["jobs", "--token"], ["=", "x"]):
        follow.prescan(argv, options=OPTS)
        follow.prescan(argv)


# ── 따라가는 명령 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv, expect",
    [
        (["serve"], False),
        (["worker"], False),
        (["token", "list"], False),
        (["init", "client", "--server", "x"], False),
        (["discover"], False),
        (["version"], False),
        (["skills", "list"], False),  # §14 A질문23 — skills 는 따라가지 않는다
        (["gc", "--config", "s.toml", "--dry-run"], False),
        (["gc", "--dry-run"], True),
        (["check"], True),
        (["run", "gate"], True),
        (["wait", "--job", "1"], True),
        (["jobs"], True),
        (["newcmd"], True),  # 설치본이 모르는 명령도 따라간다(R ①)
        (["run", "--help"], False),
        (["-h"], False),
        (["--version"], False),
        (["jobs", "-h"], False),
    ],
)
def test_follows_table(argv, expect):  # A18 · §2.3 · 결정 160
    assert follow.follows(follow.prescan(argv, options=OPTS)) is expect
    assert follow.follows(follow.prescan(argv)) is expect


def test_no_follow_commands_constant():  # §5.1
    assert follow.NO_FOLLOW_COMMANDS == frozenset(
        {"serve", "worker", "token", "init", "discover", "version", "skills"}
    )


# ── 설정 ─────────────────────────────────────────────────────────────────────


def setting(**kw):
    base = {
        "no_follow": False,
        "environ": {},
        "file_value": None,
        "file_path": None,
        "kind": "wheel",
    }
    base.update(kw)
    return follow.resolve_setting(**base)


def test_resolve_setting_table(tmp_path):  # A19 · §2.2 · 결정 154 · 156
    pin = tmp_path / "client-version"
    s = setting(no_follow=True, environ={"RCM_CLIENT_VERSION": "follow"})
    assert (s.mode, s.pin, s.source) == ("installed", None, "flag")
    for value, mode, pin_v in (("follow", "follow", None), ("installed", "installed", None)):
        s = setting(environ={"RCM_CLIENT_VERSION": value})
        assert (s.mode, s.pin, s.source) == (mode, pin_v, "env")
        s = setting(file_value=value, file_path=pin)
        assert (s.mode, s.pin, s.source) == (mode, pin_v, "file")
    s = setting(environ={"RCM_CLIENT_VERSION": OLD})
    assert (s.mode, s.pin, s.source) == ("pin", OLD, "env")
    s = setting(file_value=OLD, file_path=pin)
    assert (s.mode, s.pin, s.source) == ("pin", OLD, "file") and s.path == pin
    for kind, mode in (("wheel", "follow"), ("editable", "installed"), ("source", "installed")):
        s = setting(kind=kind)
        assert (s.mode, s.pin, s.source) == (mode, None, "default")


def test_env_beats_file_and_empty_env_is_unset(tmp_path):  # A9 · §14 A질문10
    pin = tmp_path / "client-version"
    s = setting(environ={"RCM_CLIENT_VERSION": "follow"}, file_value="installed", file_path=pin)
    assert (s.mode, s.source) == ("follow", "env")
    s = setting(environ={"RCM_CLIENT_VERSION": "installed"}, file_value="follow", file_path=pin)
    assert (s.mode, s.source) == ("installed", "env")
    s = setting(environ={"RCM_CLIENT_VERSION": ""}, file_value="installed", file_path=pin)
    assert (s.mode, s.source) == ("installed", "file")


@pytest.mark.parametrize(
    "value",
    ["lates", "../x", "1" * 65, "Follow", "INSTALLED", "99.0.1\n", " follow", "v0.8.1", "0..1"],
)
def test_bad_env_value_is_a_config_error(value):  # A34 · A102 · A89 · §14 A질문10
    with pytest.raises(ConfigError) as e:
        setting(environ={"RCM_CLIENT_VERSION": value})
    assert "is not follow, installed or a version" in str(e.value)
    assert str(e.value).startswith("RCM_CLIENT_VERSION '")
    assert "\n" not in str(e.value)  # 줄바꿈은 그대로 찍지 않는다


def test_bad_file_value_names_the_path(tmp_path):  # A34
    pin = tmp_path / "client-version"
    with pytest.raises(ConfigError) as e:
        setting(file_value="lates", file_path=pin)
    assert str(e.value) == f"{pin}: 'lates' is not follow, installed or a version"


@pytest.mark.parametrize("value", ["1" * 64, "0.7.0.dev0", "99.0.1+local.1", "1"])
def test_version_boundaries_accepted(value):  # A89
    s = setting(environ={"RCM_CLIENT_VERSION": value})
    assert (s.mode, s.pin) == ("pin", value)


def env_for(tmp_path: Path, **extra) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"HOME": str(home), **extra}


def test_setting_for_reads_lazily(tmp_path):  # A112 · §14 A질문9
    env = env_for(tmp_path)
    pin = tmp_path / "home" / ".config" / "rcm" / "client-version"
    pin.parent.mkdir(parents=True)
    pin.write_text("lates\n")
    # 맞는 환경 변수가 있으면 틀린 한 줄 파일은 읽지 않는다
    s, warn = follow.setting_for(
        no_follow=False, environ={**env, "RCM_CLIENT_VERSION": "follow"}, kind="wheel"
    )
    assert (s.mode, s.source, warn) == ("follow", "env", None)
    # 환경 변수가 없으면 파일을 읽고 틀렸으면 경로가 든 ConfigError
    with pytest.raises(ConfigError) as e:
        follow.setting_for(no_follow=False, environ=env, kind="wheel")
    assert str(pin) in str(e.value)
    # --no-follow 는 틀린 환경 변수를 이기고 경고 한 줄만 남긴다. 파일은 읽지 않는다
    s, warn = follow.setting_for(
        no_follow=True, environ={**env, "RCM_CLIENT_VERSION": "lates"}, kind="wheel"
    )
    assert (s.mode, s.source) == ("installed", "flag")
    assert warn is not None
    assert "ignoring invalid RCM_CLIENT_VERSION 'lates' (--no-follow)" in warn
    s, warn = follow.setting_for(no_follow=True, environ=env, kind="wheel")
    assert (s.mode, s.source, warn) == ("installed", "flag", None)
    # 틀린 환경 변수는 따라가는 명령에서 종료 2 의 ConfigError
    with pytest.raises(ConfigError):
        follow.setting_for(
            no_follow=False, environ={**env, "RCM_CLIENT_VERSION": "lates"}, kind="wheel"
        )


def test_setting_for_default_and_file(tmp_path):  # A19 · A20
    env = env_for(tmp_path)
    s, warn = follow.setting_for(no_follow=False, environ=env, kind="wheel")
    assert (s.mode, s.source, warn) == ("follow", "default", None)
    s, _ = follow.setting_for(no_follow=False, environ=env, kind="editable")
    assert (s.mode, s.source) == ("installed", "default")
    pin = tmp_path / "home" / ".config" / "rcm" / "client-version"
    pin.parent.mkdir(parents=True)
    pin.write_text(f"{OLD}\n")
    s, _ = follow.setting_for(no_follow=False, environ=env, kind="editable")
    assert (s.mode, s.pin, s.source) == ("pin", OLD, "file")
    assert s.path == pin


# ── 한 줄 파일 ───────────────────────────────────────────────────────────────


def test_pin_file_location(tmp_path):  # A20 · §14 A질문10
    env = env_for(tmp_path)
    home_pin = tmp_path / "home" / ".config" / "rcm" / "client-version"
    assert follow.read_pin_file(env) == (None, None)
    home_pin.parent.mkdir(parents=True)
    home_pin.write_text("\n  # pinned\n\n  installed  \n")
    assert follow.read_pin_file(env) == ("installed", home_pin)
    # XDG 를 두었지만 그 자리에 파일이 없으면 ~/.config 의 파일을 본다
    xdg = tmp_path / "x"
    assert follow.read_pin_file({**env, "XDG_CONFIG_HOME": str(xdg)}) == ("installed", home_pin)
    xdg_pin = xdg / "rcm" / "client-version"
    xdg_pin.parent.mkdir(parents=True)
    xdg_pin.write_text("follow\n")
    assert follow.read_pin_file({**env, "XDG_CONFIG_HOME": str(xdg)}) == ("follow", xdg_pin)


def test_pin_file_relative_xdg_is_ignored(tmp_path, monkeypatch):  # A135
    env = env_for(tmp_path)
    monkeypatch.chdir(tmp_path)
    rel = tmp_path / "rel" / "rcm" / "client-version"
    rel.parent.mkdir(parents=True)
    rel.write_text("follow\n")
    home_pin = tmp_path / "home" / ".config" / "rcm" / "client-version"
    home_pin.parent.mkdir(parents=True)
    home_pin.write_text("installed\n")
    assert follow.read_pin_file({**env, "XDG_CONFIG_HOME": "rel"}) == ("installed", home_pin)


@pytest.mark.parametrize(
    "raw, value",
    [
        (b"0.6.1  # pinned\n", "0.6.1"),
        (b"installed\r\n", "installed"),
        (b"\xef\xbb\xbfinstalled\n", "installed"),
        (b"# only a comment\n# another\n", None),
        (b"", None),
        (b"\n\n   \n", None),
        (b"follow\nlates\n", "follow"),  # 첫 줄만 쓴다
        (b"Follow\n", "Follow"),  # 대소문자는 해석에서 거른다
    ],
)
def test_pin_file_shapes(tmp_path, raw, value):  # A136 · §14 A질문10
    env = env_for(tmp_path)
    pin = tmp_path / "home" / ".config" / "rcm" / "client-version"
    pin.parent.mkdir(parents=True)
    pin.write_bytes(raw)
    got, path = follow.read_pin_file(env)
    assert got == value
    assert path == pin


def test_pin_file_value_case_is_an_error(tmp_path):  # A136
    pin = tmp_path / "client-version"
    with pytest.raises(ConfigError):
        setting(file_value="Follow", file_path=pin)


def test_pin_file_that_cannot_be_read(tmp_path):  # A115 · §14 A질문10
    env = env_for(tmp_path)
    pin = tmp_path / "home" / ".config" / "rcm" / "client-version"
    pin.mkdir(parents=True)  # 파일 자리에 폴더
    with pytest.raises(ConfigError) as e:
        follow.read_pin_file(env)
    assert str(pin) in str(e.value) and "cannot read" in str(e.value)
    pin.rmdir()
    pin.write_bytes(b"\xff\xfe\xfa not utf-8\n")
    with pytest.raises(ConfigError) as e:
        follow.read_pin_file(env)
    assert str(pin) in str(e.value) and "cannot read" in str(e.value)
    if os.geteuid() != 0:
        pin.write_text("follow\n")
        pin.chmod(0)
        try:
            with pytest.raises(ConfigError) as e:
                follow.read_pin_file(env)
            assert str(pin) in str(e.value) and "cannot read" in str(e.value)
        finally:
            pin.chmod(0o600)


# ── 설치 종류 ────────────────────────────────────────────────────────────────


class FakeDist:
    def __init__(self, version: str, direct_url: dict | None):
        self.version = version
        self._direct = direct_url

    def read_text(self, name: str) -> str | None:
        if name == "direct_url.json" and self._direct is not None:
            return json.dumps(self._direct)
        return None


def fake_distribution(monkeypatch, dist):
    import importlib.metadata as md

    def distribution(name):
        assert name in ("remote-ci-monitor", "remote_ci_monitor"), name
        if dist is None:
            raise md.PackageNotFoundError(name)
        return dist

    monkeypatch.setattr(md, "distribution", distribution)


@pytest.mark.parametrize(
    "dist, kind",
    [
        (FakeDist(INSTALLED, {"url": "file:///x", "dir_info": {"editable": True}}), "editable"),
        (FakeDist(INSTALLED, None), "wheel"),
        (FakeDist(INSTALLED, {"url": "file:///x.whl", "archive_info": {}}), "wheel"),
        (None, "source"),
        (FakeDist("0.0.1", {"url": "file:///x", "dir_info": {"editable": True}}), "source"),
    ],
)
def test_install_kind(monkeypatch, dist, kind):  # A12 · A21 · §2.2
    fake_distribution(monkeypatch, dist)
    assert follow.install_kind() == kind


def test_install_kind_source_when_the_import_is_another_checkout(monkeypatch):  # A98
    fake_distribution(monkeypatch, FakeDist(INSTALLED, None))
    monkeypatch.setattr(remote_ci_monitor, "__version__", "77.7.7")
    assert follow.install_kind() == "source"
    s = setting(kind="source")
    assert s.mode == "installed"


# ── 서버 주소 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url, key",
    [
        ("HTTP://MacMini.Local:8787/", "http://macmini.local:8787"),
        ("127.0.0.1:8790", "http://127.0.0.1:8790"),
        ("http://h", "http://h:80"),
        ("http://h:80", "http://h:80"),
        ("http://h/", "http://h:80"),
        ("https://h", "https://h:443"),
        ("http://h:8787/rcm", "http://h:8787/rcm"),
        ("http://h:8787/rcm/", "http://h:8787/rcm"),
        ("http://[::1]:8790", "http://[::1]:8790"),
        ("http://[::1]:8790/", "http://[::1]:8790"),
    ],
)
def test_normalize_server(url, key):  # A22 · A130 · §14 A질문11
    assert follow.normalize_server(url) == key


@pytest.mark.parametrize(
    "url",
    ["ftp://x", "not a url", "--no-follow", "http://", "http://h /x", "http://u:p@h:1", ""],
)
def test_normalize_server_rejects(url):  # A114 · §14 A질문11
    assert follow.normalize_server(url) is None


# ── health 해석 ──────────────────────────────────────────────────────────────


def test_parse_health_reads_the_wheel():  # A22
    w = fake_wheel(NEW)
    info = follow.parse_health(KEY, health_doc(NEW, w), now=T)
    assert info.server == KEY and info.version == NEW
    assert info.wheel_path == "/client/" + clientwheel.wheel_filename(NEW)
    assert info.sha256 == hashlib.sha256(w).hexdigest()
    assert info.bytes == len(w) and info.wheel_error is None and info.checked_at == T


def test_parse_health_null_wheel_and_old_server():  # A22 · A39 · A40
    doc = health_doc(NEW, None, client_wheel_error="metadata_missing")
    info = follow.parse_health(KEY, doc, now=T)
    assert info.wheel_path is None and info.sha256 is None and info.bytes is None
    assert info.wheel_error == "metadata_missing"
    doc = {"ok": True, "version": NEW}
    info = follow.parse_health(KEY, doc, now=T)
    assert info.version == NEW and info.wheel_path is None and info.wheel_error == "old_server"


def test_parse_health_cleans_the_server_error_text():  # §5.5 서버 문자열은 제어 문자를 지우고 64자
    doc = health_doc(NEW, None, client_wheel_error="\x1b[2Jbad" + "x" * 100)
    info = follow.parse_health(KEY, doc, now=T)
    assert "\x1b" not in info.wheel_error and len(info.wheel_error) <= 64


def bad(doc, key):
    with pytest.raises(follow.FollowError) as e:
        follow.parse_health(KEY, doc, now=T)
    assert e.value.code == "bad_health"
    assert e.value.detail == key, e.value.detail
    return e.value


def with_wheel(**over):
    w = fake_wheel(NEW)
    doc = health_doc(NEW, w)
    doc["client_wheel"] = {**doc["client_wheel"], **over}
    return doc


@pytest.mark.parametrize("version", ["../x", "0.8.1/../../x", "1" * 65, "99.0.1\n", "", 7, None])
def test_parse_health_rejects_versions(version):  # A51 · A89 · A102 · R ②
    doc = health_doc(NEW, fake_wheel(NEW))
    doc["version"] = version
    bad(doc, "version")


@pytest.mark.parametrize(
    "path",
    [
        "/client@evil.example/x.whl",
        "//evil/x.whl",
        "/static/x.whl",
        "/client/" + clientwheel.wheel_filename(INSTALLED),  # A54 · R ⑦
        "/client/" + clientwheel.wheel_filename(NEW) + "\n",
        None,
    ],
)
def test_parse_health_rejects_paths(path):  # A51 · A54
    bad(with_wheel(path=path), "client_wheel.path")


@pytest.mark.parametrize("digest", ["a" * 63, "a" * 65, "A" * 64, SHA_A + "\n", None, 5])
def test_parse_health_rejects_sha(digest):  # A51 · A102
    bad(with_wheel(sha256=digest), "client_wheel.sha256")


@pytest.mark.parametrize(
    "size", [True, 0, -1, 707673.0, "707673", follow.MAX_WHEEL_BYTES + 1, None]
)
def test_parse_health_rejects_bytes(size):  # A57
    bad(with_wheel(bytes=size), "client_wheel.bytes")


def test_parse_health_bytes_boundary(monkeypatch):  # A90
    monkeypatch.setattr(follow, "MAX_WHEEL_BYTES", 10_000)
    info = follow.parse_health(KEY, with_wheel(bytes=10_000), now=T)
    assert info.bytes == 10_000
    bad(with_wheel(bytes=10_001), "client_wheel.bytes")


@pytest.mark.parametrize(
    "body, key",
    [("<html>", "body"), ([1, 2], "body"), ({"ok": True}, "version"), (None, "body")],
)
def test_parse_health_odd_bodies(body, key):  # A103 · §14 A질문7
    bad(body, key)


def test_parse_health_wheel_of_the_wrong_type():  # A103
    doc = health_doc(NEW, fake_wheel(NEW))
    doc["client_wheel"] = "/client/x.whl"
    bad(doc, "client_wheel")


# ── 정규식 · 상수 · 문구 ─────────────────────────────────────────────────────


def test_regexes_are_fullmatch_only():  # A102 · §14 A질문1
    for rx in (follow.VERSION_RE, follow.SHA256_RE):
        assert not rx.pattern.startswith("^") and not rx.pattern.endswith("$")
    assert follow.VERSION_RE.fullmatch("99.0.1\n") is None
    assert follow.SHA256_RE.fullmatch(SHA_A + "\n") is None
    assert follow.SHA256_RE.fullmatch(SHA_A) and follow.SHA256_RE.fullmatch(SHA_B)


@pytest.mark.parametrize(
    "text, ok",
    [
        ("1" * 64, True),
        ("0.7.0.dev0", True),
        ("99.0.1+local.1", True),
        ("1", True),
        ("0..1", False),
        ("0.8.0-", False),
        ("v0.8.1", False),
        ("0.8.1/x", False),
    ],
)
def test_version_regex(text, ok):  # A89
    assert bool(follow.VERSION_RE.fullmatch(text)) is ok


def test_constants():  # §5.1
    assert follow.CHECK_TTL_SECONDS == 600
    assert follow.NEG_TTL_SECONDS == 60
    assert follow.HEALTH_TIMEOUT_SECONDS == 3.0
    assert follow.HEALTH_TOTAL_SECONDS == 5.0
    assert follow.FETCH_TOTAL_SECONDS == 60.0
    assert follow.KEEP_VERSIONS == 3
    assert follow.MAX_WHEEL_BYTES == 64 * 1024 * 1024
    assert follow.MAX_UNPACKED_BYTES == 256 * 1024 * 1024
    assert (follow.MARKER, follow.LOCK, follow.BAD) == (".rcm-client.json", ".lock", ".bad")
    assert follow.ENV_MODE == "RCM_CLIENT_VERSION"
    assert follow.PIN_FILE == "client-version"


@pytest.mark.parametrize(
    "code, detail, text",
    [
        ("unreachable", "", "cannot reach the server"),
        ("bad_health", "version", "the server's health has a malformed version"),
        ("no_wheel", "metadata_missing", "the server has no client wheel (metadata_missing)"),
        ("no_wheel", "HTTP 404", "the server has no client wheel (HTTP 404)"),
        ("old_server", "", "the server is too old to hand out its client"),
        ("unauthorized", "", "the server needs a token to hand out its client"),
        (
            "unauthorized",
            "HTTP 401",
            "the server refused the token for its client (HTTP 401)",
        ),
        ("sha_mismatch", "", "sha256 mismatch"),
        ("unpack", "No space left on device", "cannot unpack: No space left on device"),
        ("version_mismatch", "0.8.0", "the wheel says v0.8.0"),
        ("python", ">=3.99, this is 3.11.9", "needs Python >=3.99, this is 3.11.9"),
        ("cache_unsafe", "/x/clients is writable by others", "/x/clients is writable by others"),
        ("platform", "", "not supported on this platform"),
        ("exec_failed", "No such file", "cannot start the cached client: No such file"),
        ("cache_broken", NEW, f"the cached client v{NEW} did not load before"),
    ],
)
def test_reason_text(code, detail, text):  # A56 · §2.6 · §14.5
    assert follow.reason_text(code, detail) == text


def test_follow_error_carries_code_and_detail():
    e = follow.FollowError("sha_mismatch")
    assert e.code == "sha_mismatch" and e.detail == ""
    assert follow.FollowError("unpack", "x").detail == "x"


@pytest.mark.parametrize("n", [0, 1, 999, 1000, 707_673, 999_999, 10**6, 2 * 10**9])
def test_fmt_size_is_render_text_bytes(n):  # A31 · §14 C질문12
    assert follow.fmt_size(n) == _bytes(n)
    if n == 707_673:
        assert follow.fmt_size(n) == "707.7 KB"


# ── Python 요구 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "req, py",
    [
        (None, (3, 11, 0)),
        (">=3.11", (3, 11, 0)),
        (">=3.11.4", (3, 11, 4)),
        (">=3.11", (3, 13, 0)),
        (">= 3.11", (3, 11, 0)),
        (" >=3.11 ", (3, 12, 1)),
    ],
)
def test_python_ok(req, py):  # A24 · §14 A질문17
    assert follow.python_ok(req, py) is True


@pytest.mark.parametrize(
    "req, py",
    [
        (">=3.99", (3, 11, 0)),
        (">=3.11.5", (3, 11, 4)),
        ("~=3.11", (3, 11, 0)),
        (">=3.11,<4", (3, 11, 0)),
        (">3.11", (3, 12, 0)),
        ("==3.11.*", (3, 11, 0)),
        ("", (3, 11, 0)),
    ],
)
def test_python_not_ok(req, py):  # A46 · A47 · 결정 159
    assert follow.python_ok(req, py) is False


# ── 캐시 뿌리 ────────────────────────────────────────────────────────────────


def test_cache_root(tmp_path, monkeypatch):  # A25 · A84 · A135 · 결정 152 · 164
    home = tmp_path / "home"
    home.mkdir()
    real = os.path.realpath
    assert follow.cache_root({"XDG_CACHE_HOME": str(tmp_path / "c"), "HOME": str(home)}) == Path(
        real(tmp_path / "c" / "rcm")
    )
    expect_home = Path(real(home / ".cache" / "rcm"))
    assert follow.cache_root({"HOME": str(home)}) == expect_home
    monkeypatch.chdir(tmp_path)
    for rel in ("relative/dir", "", "~/c"):
        assert follow.cache_root({"XDG_CACHE_HOME": rel, "HOME": str(home)}) == expect_home
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "linked").symlink_to(other)
    got = follow.cache_root({"XDG_CACHE_HOME": str(tmp_path / "linked"), "HOME": str(home)})
    assert got == Path(real(other)) / "rcm"


def test_cache_root_defaults_to_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cc"))
    assert follow.cache_root() == Path(os.path.realpath(tmp_path / "cc" / "rcm"))


# ── 목표 정하기 ──────────────────────────────────────────────────────────────

FOLLOW = follow.Setting("follow", None, "default")
INSTALLED_MODE = follow.Setting("installed", None, "env")


def pin(v: str) -> follow.Setting:
    return follow.Setting("pin", v, "env")


def entry(version: str, digest: str, last_used: float, *, bad=False) -> follow.CacheEntry:
    return follow.CacheEntry(version, digest, Path(f"/c/{digest[:16]}"), last_used, bad=bad)


def info(version: str, digest: str | None = SHA_A, *, error: str | None = None):
    path = "/client/" + clientwheel.wheel_filename(version) if digest else None
    return follow.ServerInfo(
        KEY, version, path, digest, 100 if digest else None, error if not digest else None, T
    )


def plan(s, inf, cached, **kw):
    return follow.plan(s, installed=INSTALLED, info=inf, cached=cached, **kw)


def test_plan_installed_and_same_version():  # A23 · A1
    p = plan(INSTALLED_MODE, info(NEW), [entry(NEW, SHA_A, T)])
    assert (p.action, p.warn) == ("stay", None)
    p = plan(FOLLOW, info(INSTALLED), [])
    assert (p.action, p.warn) == ("stay", None)


def test_plan_follow_cases():  # A23 · A4 · F2 · F3 · F34
    newest = entry(NEW, SHA_A, T + 60)
    p = plan(FOLLOW, info(NEW, SHA_A), [entry(NEW, SHA_B, T + 120), newest])
    assert (p.action, p.target, p.entry, p.warn) == ("exec_cached", NEW, newest, None)
    p = plan(FOLLOW, info(NEW, SHA_A), [entry(NEW, SHA_B, T)])
    assert (p.action, p.target, p.warn) == ("fetch", NEW, None)
    p = plan(FOLLOW, info(OLD, SHA_A), [])  # 내려가기 — 크기를 비교하지 않는다
    assert (p.action, p.target) == ("fetch", OLD)
    p = plan(FOLLOW, None, [entry(NEW, SHA_A, T)])
    assert (p.action, p.warn) == ("stay", "unreachable")


def test_plan_without_a_wheel():  # A39 · A40 · §14 A질문19
    older, newer = entry(NEW, SHA_A, T), entry(NEW, SHA_B, T + 5)
    for error in ("metadata_missing", "old_server"):
        p = plan(FOLLOW, info(NEW, None, error=error), [older, newer, entry(OLD, SHA_A, T + 9)])
        assert (p.action, p.entry, p.warn) == ("exec_cached", newer, None)
    p = plan(FOLLOW, info(NEW, None, error="metadata_missing"), [entry(OLD, SHA_A, T)])
    assert (p.action, p.warn) == ("stay", "no_wheel")
    p = plan(FOLLOW, info(NEW, None, error="old_server"), [])
    assert (p.action, p.warn) == ("stay", "old_server")
    # `.bad` 폴더는 고르지 않는다
    p = plan(FOLLOW, info(NEW, None, error="metadata_missing"), [entry(NEW, SHA_A, T, bad=True)])
    assert (p.action, p.warn) == ("stay", "no_wheel")


def test_plan_bad_target_is_cache_broken():  # §14 B질문14
    p = plan(FOLLOW, info(NEW, SHA_A), [entry(NEW, SHA_A, T, bad=True)])
    assert (p.action, p.warn) == ("stay", "cache_broken")
    # sha 가 다르면 깨진 폴더와 상관없이 받는다
    p = plan(FOLLOW, info(NEW, SHA_B), [entry(NEW, SHA_A, T, bad=True)])
    assert p.action == "fetch"


def test_plan_on_a_stale_record():  # A36 · F15 · §2.4
    a, b = entry(NEW, SHA_A, T), entry(NEW, SHA_B, T + 60)
    p = plan(FOLLOW, info(NEW, SHA_A), [a, b], reachable=False)
    assert (p.action, p.entry, p.warn) == ("exec_cached", a, None)
    p = plan(FOLLOW, info(NEW, SHA_A), [b], reachable=False)
    assert (p.action, p.warn) == ("stay", "unreachable")
    p = plan(FOLLOW, info(NEW, SHA_A), [a], reachable=True)
    assert p.action == "exec_cached"


def test_plan_pin_table():  # A6 · A33 · A93 · §2.7 · 결정 158
    p = plan(pin(INSTALLED), None, [])
    assert (p.action, p.warn) == ("stay", None)
    p = plan(pin(INSTALLED), info(NEW), [entry(NEW, SHA_A, T)])
    assert (p.action, p.warn) == ("stay", None)
    a, b = entry(OLD, SHA_A, T), entry(OLD, SHA_B, T + 60)
    p = plan(pin(OLD), None, [a, b, entry(NEW, SHA_A, T + 120)])
    assert (p.action, p.entry, p.warn) == ("exec_cached", b, None)
    p = plan(pin(NEW), info(NEW, SHA_A), [])
    assert (p.action, p.target) == ("fetch", NEW)
    p = plan(pin(NEW2), info(NEW, SHA_A), [entry(NEW, SHA_A, T)])
    assert (p.action, p.warn) == ("stay", "pinned_unavailable")
    p = plan(pin(NEW2), None, [])
    assert (p.action, p.warn) == ("stay", "pinned_unavailable")


def test_plan_ignores_a_dashed_version_for_a_pin():  # A123
    # 고정 버전은 설치본과 달라야 한다 — 같으면 경고 없이 stay 다(v0.8.0 릴리스 CI 에서 걸렸다)
    rc = entry(f"{NEW2}-rc1", SHA_A, T)
    p = plan(pin(NEW2), None, [rc])
    assert (p.action, p.warn) == ("stay", "pinned_unavailable")


def test_plan_platform():  # A69 · 결정 172
    p = plan(FOLLOW, info(NEW), [entry(NEW, SHA_A, T)], os_name="nt")
    assert (p.action, p.warn) == ("stay", "platform")


def test_plan_is_pure(monkeypatch):  # §5.1 — 시계 · 파일 · 네트워크를 안 본다
    import socket
    import time

    def boom(*a, **k):
        raise AssertionError("plan touched the outside world")

    monkeypatch.setattr(time, "time", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(os, "stat", boom)
    try:
        plan(FOLLOW, info(NEW, SHA_A), [entry(NEW, SHA_B, T)])
    finally:
        monkeypatch.undo()


def test_python_version_text_uses_the_running_interpreter():  # A46 문구의 꼬리
    py = platform.python_version()
    assert follow.reason_text("python", f">=3.99, this is {py}").endswith(py)


def test_plan_names_the_broken_entry():
    # 개선 4: `cache_broken` 은 고칠 폴더를 알려 줄 수 있게 그 항목을 싣는다
    info = follow.ServerInfo("http://h:1", NEW, "/client/x", SHA_A, 10, None, T)
    bad = follow.CacheEntry(NEW, SHA_A, Path("/c/clients/aaaa"), T, bad=True)
    p = follow.plan(
        follow.Setting("follow", None, "default"), installed=INSTALLED, info=info, cached=[bad]
    )
    assert p.action == "stay" and p.warn == "cache_broken" and p.entry == bad
