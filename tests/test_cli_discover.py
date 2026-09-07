"""CLI(M5c) — `rcm discover [--json] [--timeout N]` · `_client` 의 서버 결정 순서 ·
`rcm check` 의 `(found on this network)`. 명세는 docs/m5c-workplan.md §3.

test_cli_m4 처럼 `main(argv)` 를 in-process 로 부른다. 실제 LAN 은 없다 —
`remote_ci_monitor.cli.discover` 를 가짜(호출 기록 + 정해진 `Found` 목록)로 바꾼다. 그래서 cli 는
`discover` 를 **모듈 수준 이름으로** import 해야 한다
(`from remote_ci_monitor.mdns import discover`).
서버가 필요한 시나리오는 test_server.Server(진짜 HTTP · 워커 없음)를 127.0.0.1 에 띄우고 발견
결과의 ip/port 로 가리킨다. 구현보다 먼저 썼다(test-first) — 구현 전에는 cli 에 `discover` 가 없어
AttributeError, `rcm discover` 는 argparse 오류(2)로 빨갛다.

잠그는 모양:
- `rcm discover` 표: 첫 줄 헤더 `name  address  version  lanes`(공백으로 나누면 네 단어, 이 순서),
  서버마다 한 줄 `<name> http://<첫 ip>:<port> <version> <lanes|—>`. 0개면 stdout 은 비고 종료 1,
  stderr 에 `no rcm server found on this network (is the server's advertise on? same Wi-Fi?)`.
- `rcm discover --json`: 객체 목록 `{name, host, port, ips, address, version, lanes}`
  (lanes 없으면 null).
- `--timeout N` 은 `discover(timeout=N)` 으로 간다. 기본은 1.5(넘기지 않아도 된다).
- `_client`: `--server` > `RCM_SERVER` > `client.toml server`(비어 있지 않고 "auto" 가 아닌 것)
  > 발견. 서버가 정해졌으면 `discover` 를 **부르지 않는다**. 1개 발견 → 그 서버 · stderr 한 줄
  `server: found <name> (<ip>:<port>) on this network`. 여러 개 → 종료 2 · 이름 나열 ·
  `--server` 와 `client.toml` 안내. 0개 → 종료 2 · 오늘의 `no server configured …` 에
  위 `no rcm server found …` 문장.
- `client.toml` 의 `server = "auto"` 는 빈 것과 같다(발견). `RCM_SERVER` · `--server` 가
  여전히 이긴다.
- `rcm check`: 발견으로 정한 서버는 `server` 행 상세가 `(found on this network)` 로 끝난다.
  여러 개 · 0개도 **`server` 행**(오늘의 `no server configured` 행 자리)에 FAIL 로 적는다 —
  `client config` 행은 파일이 잘못됐을 때만.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor.cli import NO_SERVER_HINT, main
from test_server import Server

HEADER = ["name", "address", "version", "lanes"]
DASH = "—"
NO_SERVER_FOUND = "no rcm server found on this network (is the server's advertise on? same Wi-Fi?)"
FOUND_LINE = "server: found {name} ({ip}:{port}) on this network"
JSON_KEYS = {"name", "host", "port", "ips", "address", "version", "lanes"}


@dataclass(frozen=True)
class Found:
    """`remote_ci_monitor.core.mdns.Found` 와 필드가 같은 대역.

    (name, host, port, ips, version, lanes) 와 `address`. cli 는 속성 접근만 쓴다고 본다 —
    실제 타입에 묶지 않아야 A 의 파일 상태와 무관하게 이 파일이 수집된다.
    """

    name: str
    host: str
    port: int
    ips: tuple[str, ...]
    version: str
    lanes: int | None = None

    @property
    def address(self) -> str:
        target = self.ips[0] if self.ips else self.host.rstrip(".")
        return f"http://{target}:{self.port}"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def found(
    name: str = "macmini",
    ip: str = "192.168.0.10",
    port: int = 8787,
    *,
    version: str = "0.2.1",
    lanes: int | None = 1,
    ips: tuple[str, ...] | None = None,
) -> Found:
    """발견 결과 하나. `ips` 를 주면 그것이 우선(첫 ip 가 주소가 된다)."""
    return Found(
        name=name,
        host=f"{name}.local",
        port=port,
        ips=tuple(ips) if ips else (ip,),
        version=version,
        lanes=lanes,
    )


class FakeDiscover:
    """`cli.discover` 대역 — 호출을 기록하고 정해진 목록을 돌려준다."""

    def __init__(self, results: tuple[Found, ...]):
        self.results = list(results)
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> list[Found]:
        self.calls.append((args, kwargs))
        return list(self.results)

    @property
    def called(self) -> bool:
        return bool(self.calls)

    def timeout_of(self, i: int = 0) -> float | None:
        """i 번째 호출의 timeout — 키워드든 첫 위치 인자든. 안 넘겼으면 None."""
        args, kwargs = self.calls[i]
        if "timeout" in kwargs:
            return kwargs["timeout"]
        return args[0] if args else None


def found_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith("server: found ")]


def row(out: str, name: str) -> tuple[str, str] | None:
    """`rcm check` 출력에서 `name` 행의 (상태, 상세). 행이 없으면 None.

    형식은 cmd_check 의 `{'ok ' if ok else 'FAIL'}  {name:<13} {detail}`.
    """
    m = re.search(rf"^(ok |FAIL)  {re.escape(name)} +(.*)$", out, re.M)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def client_toml(home: Path, text: str) -> Path:
    """`<HOME>/.config/rcm/client.toml` 을 쓴다 — 로더가 스스로 찾는 기본 경로."""
    p = home / ".config" / "rcm" / "client.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """HOME 을 tmp 로, XDG_CONFIG_HOME·RCM_* 은 없이. cwd 도 빈 tmp 로(`./rcm.toml` 방지)."""
    h = tmp_path / "home"
    h.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    return h


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def fake(monkeypatch):
    """`use(*found)` — `cli.discover` 를 가짜로 바꾸고 기록기를 준다.

    cli 에 모듈 수준 `discover` 가 없으면 여기서 AttributeError(구현 전의 빨간 이유).
    """

    def use(*results: Found) -> FakeDiscover:
        rec = FakeDiscover(results)
        monkeypatch.setattr(cli, "discover", rec)
        return rec

    return use


# ── rcm discover ─────────────────────────────────────────────────────────────


def test_discover_prints_a_table_with_one_row_per_server(home, fake, capsys):
    rec = fake(
        found("build-02", "10.0.0.5", 8790, version="0.3.0", lanes=None),
        found("macmini", ips=("192.168.0.10", "100.64.0.1"), lanes=2),
    )
    code, out, err = run(capsys, ["discover"])
    assert code == 0, err
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 3, out  # 헤더 + 서버 둘
    assert lines[0].split() == HEADER, lines[0]
    assert lines[1].split() == ["build-02", "http://10.0.0.5:8790", "0.3.0", DASH], lines[1]
    # 주소는 **첫** ip 로 · lanes 는 숫자 그대로
    assert lines[2].split() == ["macmini", "http://192.168.0.10:8787", "0.2.1", "2"], lines[2]
    assert len(rec.calls) == 1


def test_discover_json_lists_objects_with_the_locked_keys(home, fake, capsys):
    a = found("build-02", "10.0.0.5", 8790, version="0.3.0", lanes=None)
    b = found("macmini", ips=("192.168.0.10", "100.64.0.1"), lanes=2)
    fake(a, b)
    code, out, err = run(capsys, ["discover", "--json"])
    assert code == 0, err
    docs = json.loads(out)
    assert isinstance(docs, list) and len(docs) == 2, docs
    for doc, f in zip(docs, (a, b), strict=True):
        assert set(doc) == JSON_KEYS, doc
        assert doc["name"] == f.name and doc["host"] == f.host and doc["port"] == f.port, doc
        assert doc["ips"] == list(f.ips), doc
        assert doc["address"] == f"http://{f.ips[0]}:{f.port}", doc
        assert doc["version"] == f.version, doc
        assert doc["lanes"] == f.lanes, doc  # None → null


def test_discover_passes_timeout_to_discover(home, fake, capsys):
    rec = fake(found())
    code, _, err = run(capsys, ["discover", "--timeout", "0.3"])
    assert code == 0, err
    assert len(rec.calls) == 1
    assert rec.timeout_of() == pytest.approx(0.3), rec.calls


def test_discover_default_timeout_is_the_querier_default(home, fake, capsys):
    rec = fake(found())
    assert run(capsys, ["discover"])[0] == 0
    assert rec.timeout_of() in (None, 1.5), rec.calls  # 안 넘기거나 명세의 1.5


def test_discover_with_no_server_exits_1_with_the_network_hint(home, fake, capsys):
    rec = fake()
    code, out, err = run(capsys, ["discover"])
    assert code == 1, (out, err)
    assert out == "", out  # 빈 표는 찍지 않는다
    assert NO_SERVER_FOUND in err, err
    assert rec.called


def test_discover_needs_no_config_and_ignores_a_configured_server(home, fake, monkeypatch, capsys):
    # client.toml 도 토큰도 없다. RCM_SERVER 가 있어도 `rcm discover` 는 언제나 찾는다.
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:1")
    rec = fake(found())
    code, out, err = run(capsys, ["discover"])
    assert code == 0, err
    assert "macmini" in out and "http://192.168.0.10:8787" in out, out
    assert rec.called


def test_discover_help_lists_json_and_timeout(home, capsys):
    code, out, _ = run(capsys, ["discover", "--help"])
    assert code == 0, out
    assert "--json" in out and "--timeout" in out, out


# ── _client: 결정 순서 (플래그 > env > client.toml > 발견) ────────────────────


def test_the_only_discovered_server_is_used_and_announced_on_stderr(srv, home, fake, capsys):
    rec = fake(found("macmini", "127.0.0.1", srv.port))
    code, out, err = run(capsys, ["presets"])  # 토큰이 필요 없는 명령
    assert code == 0, err
    assert "gate" in out, out  # test_server 의 프리셋 — 발견된 서버에 붙었다
    line = FOUND_LINE.format(name="macmini", ip="127.0.0.1", port=srv.port)
    assert found_lines(err) == [line], err
    assert len(rec.calls) == 1


def test_server_flag_wins_and_skips_discovery(srv, home, fake, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
    monkeypatch.setenv("RCM_SERVER", "http://127.0.0.1:1")  # 닫힌 포트
    client_toml(home, 'server = "http://127.0.0.1:2"\n')
    rec = fake(found("ghost", "127.0.0.1", 3))
    code, out, err = run(capsys, ["check", "--server", f"http://127.0.0.1:{srv.port}"])
    assert code == 0, out + err
    assert row(out, "server") is not None, out
    status, detail = row(out, "server")
    assert status == "ok" and detail.startswith(f"http://127.0.0.1:{srv.port}"), detail
    assert "found on this network" not in out and found_lines(err) == []
    assert not rec.called


def test_env_wins_over_client_toml_and_skips_discovery(srv, home, fake, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{srv.port}")
    client_toml(home, 'server = "http://127.0.0.1:2"\n')
    rec = fake(found("ghost", "127.0.0.1", 3))
    code, out, err = run(capsys, ["check"])
    assert code == 0, out + err
    assert row(out, "server") is not None, out
    status, detail = row(out, "server")
    assert status == "ok" and detail.startswith(f"http://127.0.0.1:{srv.port}"), detail
    assert "found on this network" not in out and found_lines(err) == []
    assert not rec.called


def test_client_toml_server_skips_discovery(srv, home, fake, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
    client_toml(home, f'server = "http://127.0.0.1:{srv.port}"\n')
    rec = fake(found("ghost", "127.0.0.1", 3))
    code, out, err = run(capsys, ["check"])
    assert code == 0, out + err
    assert row(out, "server") is not None, out
    status, detail = row(out, "server")
    assert status == "ok" and detail.startswith(f"http://127.0.0.1:{srv.port}"), detail
    assert "found on this network" not in out and found_lines(err) == []
    assert not rec.called


def test_client_toml_auto_forces_discovery(srv, home, fake, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
    client_toml(home, 'server = "auto"\n')
    rec = fake(found("macmini", "127.0.0.1", srv.port))
    code, out, err = run(capsys, ["check"])
    assert code == 0, out + err
    assert row(out, "server") is not None, out
    status, detail = row(out, "server")
    assert status == "ok", out
    assert detail.startswith(f"http://127.0.0.1:{srv.port}"), detail
    assert detail.endswith("(found on this network)"), detail
    assert rec.called


@pytest.mark.parametrize("how", ["env", "flag"])
def test_env_and_flag_override_auto(srv, home, fake, monkeypatch, capsys, how: str):
    client_toml(home, 'server = "auto"\n')
    real = f"http://127.0.0.1:{srv.port}"
    argv = ["presets"]
    if how == "env":
        monkeypatch.setenv("RCM_SERVER", real)
    else:
        argv += ["--server", real]
    rec = fake(found("ghost", "127.0.0.1", 3))
    code, out, err = run(capsys, argv)
    assert code == 0, err
    assert "gate" in out, out
    assert not rec.called and found_lines(err) == [], err


def test_several_discovered_servers_is_a_usage_error_listing_their_names(home, fake, capsys):
    fake(found("macmini", "192.168.0.10"), found("build-02", "192.168.0.11", 8790))
    code, out, err = run(capsys, ["presets"])
    assert code == 2, (out, err)
    assert out == "", out
    assert "macmini" in err and "build-02" in err, err  # 어느 서버들이 보였는지
    assert "--server" in err and "client.toml" in err, err  # 고르는 법


def test_no_discovered_server_keeps_the_no_server_hint_and_adds_the_network_sentence(
    home, fake, capsys
):
    rec = fake()
    code, out, err = run(capsys, ["presets"])
    assert code == 2, (out, err)
    assert out == "", out
    assert NO_SERVER_HINT in err, err  # 오늘의 안내는 그대로
    assert NO_SERVER_FOUND in err, err  # 거기에 한 문장
    assert rec.called


# ── rcm check ────────────────────────────────────────────────────────────────


def test_check_shows_the_discovered_server_as_found_on_this_network(
    srv, home, fake, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
    rec = fake(found("macmini", "127.0.0.1", srv.port))
    code, out, err = run(capsys, ["check"])
    assert code == 0, out + err
    assert row(out, "server") is not None, out
    status, detail = row(out, "server")
    assert status == "ok", out
    assert detail.startswith(f"http://127.0.0.1:{srv.port}"), detail
    assert detail.endswith("(found on this network)"), detail
    assert row(out, "token") == ("ok", "alice-laptop"), out  # 나머지 행은 그대로
    assert len(rec.calls) == 1  # 한 번만 찾는다


def test_check_with_several_discovered_servers_fails_the_server_row_and_names_them(
    home, fake, capsys
):
    fake(found("macmini", "192.168.0.10"), found("build-02", "192.168.0.11", 8790))
    code, out, err = run(capsys, ["check"])
    assert code == 1, (out, err)
    assert row(out, "server") is not None, out
    assert row(out, "server")[0] == "FAIL", out
    assert "macmini" in out + err and "build-02" in out + err, out + err


def test_check_with_no_discovered_server_keeps_the_no_server_row(home, fake, capsys):
    rec = fake()
    code, out, err = run(capsys, ["check"])
    assert code == 1, (out, err)
    assert row(out, "server") is not None, out
    status, detail = row(out, "server")
    assert status == "FAIL" and NO_SERVER_HINT in detail, out
    assert "advertise" in out + err, (
        out + err
    )  # 같은 네트워크에 서버가 없거나 advertise 가 꺼져 있다
    assert rec.called
