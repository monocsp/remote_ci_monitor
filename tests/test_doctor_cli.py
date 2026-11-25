"""`rcm doctor` 명령 전체와 `rcm worker --check` 의 두 줄(M5t-a 결정 246 · 247, §4.5 · §7 TD).

서버는 `fake_rcm_http.FakeRcm`(127.0.0.1 임시 포트)이다. `/worker/spec` 의 새 키(`pool` · `pools` ·
`placement` · `checks` · `tools`)는 서버 쪽 구현(2라운드)보다 먼저 손으로 쓴 문서로 못 박는다 —
doctor 가 그 문서를 어떻게 읽는지만 본다. 서비스 파일 · 도구 · 설정은 모두 `tmp_path` 아래이고
HOME 도 임시 폴더다. 운영 설정(`~/.config/rcm`)과 운영 데이터는 입력으로 쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, RawReply, free_port
from remote_ci_monitor import __version__, cli, doctor, follow
from test_cli_discover import FakeDiscover, found

TOKEN = "rcm-td-worker-token"
CLIENT_TOKEN = "rcm-td-client-token"
HAVE = "rcm-td-have"
LACK = "rcm-td-lack"


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    for var in (
        "XDG_CONFIG_HOME",
        "RCM_CONFIG",
        "RCM_SERVER",
        "RCM_TOKEN",
        "RCM_WORKER_TOKEN",
        "JAVA_HOME",
        "XDG_RUNTIME_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(doctor, "is_tty", lambda: False)
    return tmp_path


@pytest.fixture
def svcbin(env) -> Path:
    """서비스 PATH 에만 있는 폴더 — `rcm-td-have` 와 버전을 찍는 프로브."""
    d = env / "home" / "svcbin"
    d.mkdir()
    script(d / HAVE, "exit 0")
    script(d / "rcm-td-version", "echo 'td-tool 1.2.3'")
    return d


def preset(
    name: str,
    *,
    requires: tuple[str, ...] = (HAVE,),
    placement: str = "pool",
    pool: str = "mbp",
    pools: tuple[str, ...] = (),
    checks: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    passthrough: tuple[str, ...] = ("PATH",),
    unreachable: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "name": name,
        "requires": list(requires),
        "env_passthrough": list(passthrough),
        "env": dict(env or {}),
        "pool": pool,
        "pools": list(pools),
        "placement": placement,
        "checks": list(checks),
        # 진짜 서버(`capabilities.worker_spec`)는 모든 프리셋에 `source_modes` 와 `repo` 를 싣는다
        "source_modes": ["tree"],
        "repo": None,
        "unreachable_checks": [{"name": c, "code": "check_local_only"} for c in unreachable],
    }


def spec_doc(*presets: dict[str, Any], probes=(), tools=()) -> dict[str, Any]:
    return {
        "generation": "1:0",
        "probe_interval_seconds": 600,
        "probes": list(probes),
        "presets": list(presets),
        "tools": list(tools),
    }


LACK_HINT = {
    "name": LACK,
    "docs": "https://example.invalid/td-lack",
    "install": {"darwin": "brew install td-lack", "linux": "apt-get install td-lack"},
}


@pytest.fixture
def srv() -> Iterator[FakeRcm]:
    s = FakeRcm()
    s.route("GET", "/api/health", {"ok": True, "version": __version__})
    s.route("GET", "/api/whoami", {"name": "mbp", "admin": False, "kind": "worker"})
    s.route("GET", "/worker/spec", spec_doc(preset("build")))
    yield s
    s.close()


def worker_toml(env: Path, server: str, *, pool: str = "mbp", extra: str = "") -> Path:
    p = env / "worker.toml"
    p.write_text(f'server = "{server}"\npool = "{pool}"\n{extra}')
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return p


def plist(
    where: Path,
    path_value: str,
    *,
    extra: dict[str, str] | None = None,
    command: str = "worker",
    name: str = "com.remote-ci-monitor.worker.plist",
) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    p = where / name
    p.write_bytes(
        plistlib.dumps(
            {
                "Label": name.removesuffix(".plist"),
                "ProgramArguments": ["/x/rcm", command, "--config", "/x/c.toml"],
                "EnvironmentVariables": {"PATH": path_value, **(extra or {})},
            }
        )
    )
    return p


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(["doctor", *argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def run_json(capsys, *argv: str) -> tuple[int, dict[str, Any]]:
    code, out, err = run(capsys, *argv, "--json")
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        raise AssertionError(f"not JSON: {out!r} {err!r}") from None
    assert doc["exit"] == code, doc
    return code, doc


class Worker:
    """`rcm doctor --role worker` 를 그 자리의 서비스 파일 · worker.toml 로 부른다."""

    def __init__(self, env: Path, srv: FakeRcm, svcbin: Path):
        self.env, self.srv, self.svcbin = env, srv, svcbin

    def argv(self, path: str | None, svc_env: dict[str, str] | None, pool: str) -> list[str]:
        cfg = worker_toml(self.env, self.srv.url, pool=pool)
        sf = plist(self.env / "svc", path or f"{self.svcbin}:/usr/bin:/bin", extra=svc_env)
        return ["--role", "worker", "--config", str(cfg), "--service-file", str(sf)]

    def __call__(self, capsys, *extra: str, path=None, svc_env=None, pool="mbp"):
        return run_json(capsys, *self.argv(path, svc_env, pool), *extra)

    def text(self, capsys, *extra: str, path=None, svc_env=None, pool="mbp"):
        return run(capsys, *self.argv(path, svc_env, pool), *extra)


@pytest.fixture
def worker(env, srv, svcbin, monkeypatch) -> Worker:
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    return Worker(env, srv, svcbin)


def row(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in doc["presets"] if p["name"] == name)


# ── 종료 코드 (TD13 · TD34 · TD3 · TD4 · TD5 · TD35) ──────────────────────────


def test_td13_all_ready_exits_0(worker, capsys):
    code, doc = worker(capsys)
    assert code == 0, doc
    assert row(doc, "build")["state"] == "ready"
    assert doc["summary"] == {
        "ready": 1,
        "needs_check": 0,
        "missing": 0,
        "unknown": 0,
        "applies_unknown": 0,
    }
    assert doc["role"] == "worker" and doc["measured_in"] == "clean_service_env"


def test_td13_a_missing_tool_in_this_pool_exits_1_with_the_install_hint(worker, srv, capsys):
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(preset("build"), preset("lint", requires=(LACK,)), tools=[LACK_HINT]),
    )
    code, doc = worker(capsys)
    assert code == 1, doc
    lint = row(doc, "lint")
    assert lint["state"] == "missing" and lint["missing"][0]["tool"] == LACK, lint
    assert lint["missing"][0]["install"] in ("brew install td-lack", "apt-get install td-lack")
    assert lint["missing"][0]["docs"] == "https://example.invalid/td-lack"
    code2, out, _ = worker.text(capsys)
    assert code2 == 1 and LACK in out and "td-lack" in out, out


def test_td13_usage_errors_exit_2(env, capsys):
    code, out, err = run(capsys, "--role", "worker", "--config", str(env / "nope.toml"))
    assert code == 2, (out, err)
    with pytest.raises(SystemExit) as e:
        cli.main(["doctor", "--role", "nope"])
    assert e.value.code == 2


def test_td34_missing_and_undecided_together_exit_1(worker, srv, svcbin, capsys):
    slow = script(svcbin / "rcm-td-slow", "sleep 5")
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("lint", requires=(LACK,)),
            probes=[{"name": "slow", "argv": [str(slow)], "timeout_seconds": 1}],
        ),
    )
    code, doc = worker(capsys)
    assert code == 1, doc
    assert doc["undecided"] and doc["problems"], doc


def test_td3_a_probe_timeout_is_unknown_and_exits_3(worker, srv, svcbin, capsys):
    slow = script(svcbin / "rcm-td-slow", "sleep 5")
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("build"),
            probes=[{"name": "slow", "argv": [str(slow)], "timeout_seconds": 1}],
        ),
    )
    code, doc = worker(capsys)
    assert code == 3, doc
    assert doc["probes"]["slow"]["state"] == "unknown", doc["probes"]


def test_td4_an_unreachable_server_exits_3_not_0(env, svcbin, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    cfg = worker_toml(env, f"http://127.0.0.1:{free_port()}")
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    code, doc = run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert code == 3, doc
    assert any("cannot reach" in u for u in doc["undecided"]), doc


def test_td5_an_older_server_without_the_new_keys_exits_3(worker, srv, capsys):
    old = {"name": "build", "requires": [HAVE], "env_passthrough": ["PATH"], "env": {}}
    srv.route(
        "GET",
        "/worker/spec",
        {"generation": "1:0", "probe_interval_seconds": 600, "probes": [], "presets": [old]},
    )
    code, doc = worker(capsys)
    assert code == 3, doc
    assert any("other pools" in u and "older rcm" in u for u in doc["undecided"]), doc


def test_td35_no_token_or_401_exits_3_and_says_a_token_is_needed(
    env, srv, svcbin, monkeypatch, capsys
):
    cfg = worker_toml(env, srv.url)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert code == 3, (out, err)
    assert "token" in (out + err).lower() and "need" in (out + err).lower(), out + err
    assert str(env / "home") not in out + err
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.route("GET", "/api/whoami", {"error": "unauthorized"}, status=401)
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert code == 3, (out, err)
    assert "token" in (out + err).lower(), out + err
    assert str(env / "home") not in out + err


# ── 서버에는 읽기 요청만 · 비밀 · 서버가 보낸 문구 ────────────────────────────


def test_td12_doctor_sends_only_read_requests_to_the_server(worker, srv, capsys):
    """진단은 조언이다 — GET 만 보내고 등록도 능력 보고도 하지 않는다(위임 결정 E2: 「아무것도
    보내지 않는다」가 아니라 「읽기 요청만 보낸다」가 사실이다)."""
    worker(capsys)
    assert srv.requests, "doctor never asked the server"
    assert {r.method for r in srv.requests} == {"GET"}, [(r.method, r.path) for r in srv.requests]
    assert not srv.calls("POST", "/worker/capabilities")
    assert not srv.calls("POST", "/worker/register")


def test_td8_preset_env_values_never_reach_the_output(worker, srv, capsys):
    secret = "td8-secret-value-abc"
    srv.route("GET", "/worker/spec", spec_doc(preset("build", env={"API_KEY": secret})))
    code, doc = worker(capsys)
    assert secret not in json.dumps(doc), doc
    code, out, err = worker.text(capsys)
    assert secret not in out + err, out


def test_td32_install_text_from_the_server_is_cleaned_before_printing(worker, srv, capsys):
    evil = {
        "name": LACK,
        "install": {"darwin": "brew\x1b[2J install\x07 td", "linux": "apt\x1b[2J get\x07 td"},
    }
    srv.route("GET", "/worker/spec", spec_doc(preset("lint", requires=(LACK,)), tools=[evil]))
    code, doc = worker(capsys)
    text = json.dumps(doc)
    assert "\\u001b" not in text and "\\u0007" not in text, text
    hint = row(doc, "lint")["missing"][0]["install"]
    assert hint in ("brew[2J install td", "apt[2J get td"), hint


# ── 역할별 규칙 (TD26 · TD53 · TD40 · TD27 · TD11 · TD16) ─────────────────────


def test_td26_no_preset_comes_to_this_machine_exits_1_with_the_reason(worker, srv, capsys):
    srv.route("GET", "/worker/spec", spec_doc(preset("build", pool="linux")))
    code, doc = worker(capsys)
    assert code == 1, doc
    assert any("no jobs will come to this machine" in p for p in doc["problems"]), doc
    assert any("mbp" in p for p in doc["problems"]), doc  # 이유: 이 풀에 프리셋이 없다


def test_td53_any_capable_missing_tools_and_zero_coming_still_exit_1(worker, srv, capsys):
    srv.route(
        "GET", "/worker/spec", spec_doc(preset("e2e", requires=(LACK,), placement="any-capable"))
    )
    code, doc = worker(capsys)
    assert code == 1, doc
    assert row(doc, "e2e")["applies"] == "not_coming"


def test_td40_any_capable_missing_tool_is_a_warning_not_an_exit_code(worker, srv, capsys):
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("build"),
            preset("e2e", requires=(LACK,), placement="any-capable"),
            tools=[LACK_HINT],
        ),
    )
    code, doc = worker(capsys)
    assert code == 0, doc
    e2e = row(doc, "e2e")
    assert e2e["applies"] == "not_coming" and e2e["missing"][0]["install"], e2e
    assert any("e2e" in w and "not coming" in w for w in doc["warnings"]), doc


def test_td27_workspace_tools(worker, srv, svcbin, capsys):
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("build"),
            preset("node", requires=("rcm-td-npx",)),
            preset("web", requires=("rcm-td-npx",), placement="any-capable"),
        ),
    )
    code, doc = worker(capsys, path=f"{svcbin}:node_modules/.bin:/usr/bin")
    assert code == 0, doc
    assert row(doc, "node")["state"] == "ready" and row(doc, "node")["warn"], doc
    assert row(doc, "web")["applies"] == "not_coming", doc


def test_td11_needs_check_is_not_called_ok(worker, srv, capsys):
    # 서버는 점검 프리셋도 명세에 싣는다. 이 머신에서 돌 수 없는 점검은 `unreachable_checks` 로
    # 따로 말한다(TO18) — 여기서는 빈 목록이다
    srv.route(
        "GET", "/worker/spec", spec_doc(preset("ios", checks=("sim-check",)), preset("sim-check"))
    )
    code, doc = worker(capsys)
    assert code == 0, doc
    assert row(doc, "ios")["state"] == "needs_check" and row(doc, "ios")["checks"] == ["sim-check"]
    code, out, _ = worker.text(capsys)
    line = next(ln for ln in out.splitlines() if " ios " in f" {ln} ")
    assert "needs check" in line and "ready" not in line and " ok " not in f" {line} ", line


def test_td16_another_pools_preset_is_left_out(worker, srv, capsys):
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(preset("build"), preset("linux-only", requires=(LACK,), pool="linux")),
    )
    code, doc = worker(capsys)
    assert code == 0, doc
    assert row(doc, "linux-only")["applies"] == "other_pool"
    assert doc["summary"]["missing"] == 0


# ── 깨끗한 환경 (TD2 · TD46 · TD29 · TD37 · TD55) ─────────────────────────────


def test_td2_shell_and_service_results_differ_and_both_show(worker, srv, env, monkeypatch, capsys):
    shell_bin = env / "home" / "shellbin"
    shell_bin.mkdir()
    script(shell_bin / LACK, "exit 0")
    monkeypatch.setenv("PATH", f"{shell_bin}:/usr/bin:/bin")
    srv.route("GET", "/worker/spec", spec_doc(preset("build"), preset("lint", requires=(LACK,))))
    code, doc = worker(capsys, "--shell-path")
    lint = row(doc, "lint")
    assert lint["state"] == "missing" and lint["shell_differs"] is True, lint
    assert doc["shell"]["path"].startswith("~/shellbin"), doc["shell"]
    assert doc["service"]["path"].startswith("~/svcbin"), doc["service"]


def test_td46_both_paths_hide_the_home_folder(worker, env, monkeypatch, capsys):
    monkeypatch.setenv("PATH", f"{env / 'home' / 'shellbin'}:/usr/bin")
    code, doc = worker(capsys, "--shell-path")
    assert str(env / "home") not in json.dumps(doc), doc
    assert doc["service"]["path"].startswith("~/svcbin"), doc
    assert doc["shell"]["path"].startswith("~/shellbin"), doc


def test_td29_the_shells_java_home_does_not_leak_into_the_measurement(
    worker, srv, svcbin, monkeypatch, capsys
):
    monkeypatch.setenv("JAVA_HOME", "/opt/td29-jdk")
    show = script(svcbin / "rcm-td-java-home", 'echo "java_home=$JAVA_HOME"')
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("android", passthrough=("PATH", "JAVA_HOME")),
            probes=[{"name": "jh", "argv": [str(show)], "timeout_seconds": 5}],
        ),
    )
    code, doc = worker(capsys)
    assert doc["probes"]["jh"]["value"] == "java_home=", doc["probes"]
    assert any("JAVA_HOME" in w and "android" in w for w in doc["warnings"]), doc


@pytest.mark.parametrize("manager", ["launchd", "systemd"])
def test_td37_probes_see_only_the_managers_defaults_and_the_service_env(
    env, srv, svcbin, monkeypatch, capsys, manager
):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setenv("JAVA_HOME", "/opt/jdk")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(env))
    dump = env / "env-dump.txt"
    probe = script(svcbin / "rcm-td-dump", f"env > {dump}")
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("build"), probes=[{"name": "d", "argv": [str(probe)], "timeout_seconds": 5}]
        ),
    )
    path_value = f"{svcbin}:/usr/bin:/bin"
    if manager == "launchd":
        sf = plist(env / "svc", path_value, extra={"TD_EXTRA": "1"})
        want = {"HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "PATH", "TD_EXTRA"}
    else:
        (env / "svc").mkdir()
        sf = env / "svc" / "rcm-worker.service"
        sf.write_text(
            f"[Service]\nExecStart=/x/rcm worker\nEnvironment=PATH={path_value}\n"
            "Environment=TD_EXTRA=1\n"
        )
        want = {"HOME", "USER", "LOGNAME", "SHELL", "XDG_RUNTIME_DIR", "PATH", "TD_EXTRA"}
    cfg = worker_toml(env, srv.url)
    run_json(capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf))
    names = {ln.split("=", 1)[0] for ln in dump.read_text().splitlines() if "=" in ln}
    # `sh` 가 스스로 넣는 변수(PWD · SHLVL · _ · OLDPWD)는 빼고 본다
    names -= {"PWD", "SHLVL", "_", "OLDPWD"}
    assert names == want, names


def test_td55_service_env_shows_names_never_values(worker, capsys):
    code, doc = worker(capsys, svc_env={"TD_SECRET": "td55-hidden-value"})
    assert "TD_SECRET" in doc["service"]["env_names"], doc["service"]
    assert "td55-hidden-value" not in json.dumps(doc)


# ── 서비스 파일 찾기 · 역할 읽기 (TD45 · TD47 · TD48 · TD49) ──────────────────


def test_td45_service_file_search_order(env, srv, svcbin, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    cfg = worker_toml(env, srv.url)
    home = env / "home"
    # 없으면 「서비스 파일 없음」과 WORKER_SERVICE_PATH
    code, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] is None and doc["service"]["path"] == cli.WORKER_SERVICE_PATH
    # 설정 폴더 아래 setup 자리
    staged = plist(home / ".config" / "rcm" / "service", f"{svcbin}:/usr/bin")
    _, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] == "~/.config/rcm/service/" + staged.name, doc["service"]
    # 설치 자리가 있으면 그것이 먼저다(6-B-V2 — launchd 가 실제로 읽는 파일)
    installed = plist(home / "Library" / "LaunchAgents", f"{svcbin}:/bin")
    _, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] == "~/Library/LaunchAgents/" + installed.name, doc["service"]
    # 역할의 명령으로 거른다 — 서버 서비스 파일은 worker 진단의 후보가 아니다
    plist(
        home / "Library" / "LaunchAgents",
        "/bin",
        command="serve",
        name="com.remote-ci-monitor.server.plist",
    )
    _, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] == "~/Library/LaunchAgents/" + installed.name
    # --service-file 이 무엇보다 먼저다
    explicit = plist(env / "elsewhere", f"{svcbin}:/usr/bin")
    _, doc = run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(explicit)
    )
    assert doc["service"]["file"].endswith("elsewhere/" + explicit.name), doc["service"]
    # 같은 자리에 둘 이상이면 고르게 한다 — 비TTY 면 종료 2
    plist(home / "Library" / "LaunchAgents", "/bin", name="com.remote-ci-monitor.worker2.plist")
    code, out, err = run(capsys, "--role", "worker", "--config", str(cfg))
    assert code == 2, (out, err)
    assert "--service-file" in err, err


def test_td47_the_role_comes_from_the_config_files(env, srv, svcbin, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    cfgdir = env / "home" / ".config" / "rcm"
    cfgdir.mkdir(parents=True)
    w = cfgdir / "worker.toml"
    w.write_text(f'server = "{srv.url}"\npool = "mbp"\n')
    w.chmod(0o600)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin")
    code, doc = run_json(capsys, "--service-file", str(sf))
    assert doc["role"] == "worker", doc
    (cfgdir / "client.toml").write_text(f'server = "{srv.url}"\n')
    code, out, err = run(capsys, "--service-file", str(sf))
    assert code == 2, (out, err)
    assert "--role" in err and "client" in err and "worker" in err, err


def test_td48_an_auto_server_in_worker_toml_is_a_warning(env, srv, svcbin, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(doctor, "is_tty", lambda: True)
    monkeypatch.setattr(doctor, "ask", lambda _q: True)
    monkeypatch.setattr(cli, "discover", FakeDiscover((found("macmini", "127.0.0.1", srv.port),)))
    cfg = worker_toml(env, "auto")
    sf = plist(env / "svc", f"{svcbin}:/usr/bin")
    code, doc = run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert any('server = "auto"' in w for w in doc["warnings"]), doc


def test_td49_an_env_only_worker_counts_as_configured(env, srv, svcbin, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin")
    code, doc = run_json(capsys, "--server", srv.url, "--service-file", str(sf))
    assert doc["role"] == "worker", doc


# ── mDNS 로 찾은 서버 (TD10 · TD28) ──────────────────────────────────────────


def test_td10_a_discovered_server_gets_nothing_before_a_person_confirms(
    env, srv, svcbin, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(doctor, "is_tty", lambda: True)
    asked: list[str] = []

    def no(question: str) -> bool:
        asked.append(question)
        assert srv.requests == [], "a request went out before the person answered"
        return False

    monkeypatch.setattr(doctor, "ask", no)
    monkeypatch.setattr(cli, "discover", FakeDiscover((found("macmini", "127.0.0.1", srv.port),)))
    cfg = worker_toml(env, "auto")
    sf = plist(env / "svc", f"{svcbin}:/usr/bin")
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert code == 3 and asked and "macmini" in asked[0], (out, err, asked)
    assert srv.requests == []
    monkeypatch.setattr(doctor, "ask", lambda _q: True)
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert code == 0, (out, err)
    assert srv.calls("GET", "/worker/spec")


def test_td10_an_explicit_url_is_not_asked_about(worker, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "is_tty", lambda: True)
    monkeypatch.setattr(doctor, "ask", lambda _q: pytest.fail("asked about an explicit URL"))
    code, doc = worker(capsys)
    assert code == 0, doc


def test_td28_non_tty_with_a_discovered_server_exits_3(env, srv, svcbin, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "discover", FakeDiscover((found("macmini", "127.0.0.1", srv.port),)))
    cfg = worker_toml(env, "auto")
    sf = plist(env / "svc", f"{svcbin}:/usr/bin")
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert code == 3, (out, err)
    assert srv.requests == []
    # 종료 3 의 이유까지 — 다른 모름(토큰 · 서비스 파일)으로 3 이 난 것이 아니다
    said = [ln for ln in out.splitlines() if ln.startswith("?")]
    assert said and all("nobody confirmed" in ln for ln in said), out


# ── 원격 git · Local Network · Python (TD17 · TD50 · TD14) ─────────────────────


def test_td17_offline_skips_git_ls_remote_but_still_asks_the_coordinator(
    env, srv, svcbin, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    calls: list[str] = []
    monkeypatch.setattr(
        cli, "_ls_remote_problem", lambda url, timeout=10.0, **_kw: calls.append(url)
    )
    repo = '[[repos]]\nname = "app"\nurl = "https://example.invalid/app.git"\n'
    cfg = worker_toml(env, srv.url, extra=repo)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    run_json(capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf))
    assert calls == ["https://example.invalid/app.git"], calls
    calls.clear()
    srv.requests.clear()
    run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf), "--offline"
    )
    assert calls == [], calls
    assert srv.calls("GET", "/worker/spec"), "offline must still ask the coordinator"


def test_td50_local_network_permission_is_guidance_only(worker, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "PLATFORM", "darwin")
    code, doc = worker(capsys)
    ln = next(r for r in doc["rcm_requirements"] if r["name"] == "local network")
    assert ln["state"] == "info", ln
    assert code == 0, doc


def test_td14_an_old_python_fails_and_exits_1(worker, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "python_version", lambda: (3, 11, 3))
    code, doc = worker(capsys)
    assert code == 1, doc
    py = next(r for r in doc["rcm_requirements"] if r["name"] == "python")
    assert py["state"] == "fail" and py["install"], py


# ── coordinator 계열 · client (TD30 · TD41 · TD39) ─────────────────────────────


def server_toml(env: Path, port: int) -> Path:
    p = env / "server.toml"
    p.write_text(
        f"""[server]
bind = "0.0.0.0"
port = {port}
data_dir = "{env / "data"}"

[[presets]]
name = "unit"
argv = ["true"]
requires = ["{HAVE}"]
env_passthrough = ["PATH"]

[[presets]]
name = "lint"
argv = ["true"]
requires = ["{LACK}"]
env_passthrough = ["PATH"]
"""
    )
    return p


def machines_doc() -> dict[str, Any]:
    ok = {"state": "eligible", "code": "ok", "detail": {}}
    bad = {"state": "ineligible", "code": "tool_missing", "detail": {"tools": [LACK]}}
    return {
        "generation": "1:0",
        "machines": [
            {
                "id": "@local",
                "display_name": "build-01",
                "kind": "local",
                "pool": "default",
                "lanes": 2,
                "alive": True,
                "presets": {"unit": ok, "lint": bad},
            },
            {
                "id": "mbp",
                "display_name": "mbp",
                "kind": "remote",
                "pool": "default",
                "lanes": 1,
                "alive": True,
                "presets": {"unit": ok, "lint": ok},
            },
        ],
    }


@pytest.fixture
def coordinator(env, monkeypatch) -> Iterator[FakeRcm]:
    s = FakeRcm()
    s.route(
        "GET",
        "/api/health",
        {
            "ok": True,
            "version": __version__,
            "preset_env": {
                "unit": {"missing_tools": [], "missing_passthrough": []},
                "lint": {"missing_tools": [LACK], "missing_passthrough": []},
            },
        },
    )
    s.route("GET", "/api/whoami", {"name": "alice", "admin": False, "kind": "client"})
    s.route("GET", "/api/machines", machines_doc())
    yield s
    s.close()


def test_td30_coordinator_worker_reads_what_the_server_measured(
    env, coordinator, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = server_toml(env, coordinator.port)
    code, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert doc["measured_in"] == "server_service_env", doc
    assert row(doc, "lint")["state"] == "missing" and row(doc, "unit")["state"] == "ready", doc
    assert code == 1, doc
    assert [m["name"] for m in doc["remote_machines"]] == ["mbp"], doc
    # 0.0.0.0 에 묶인 서버는 127.0.0.1 로 묻는다
    assert doc["server"]["url"] == f"http://127.0.0.1:{coordinator.port}"
    assert coordinator.calls("GET", "/api/machines") and coordinator.calls("GET", "/api/health")


def test_td41_without_a_client_token_the_server_cells_are_dashes_and_exit_3(
    env, coordinator, capsys
):
    cfg = server_toml(env, coordinator.port)
    code, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert code == 3, doc
    assert doc["server"]["state"] == "—", doc["server"]
    assert not (env / "data" / "rcm.sqlite3").exists()  # DB 를 열지 않는다
    assert not (env / "data").exists()
    token_line = [u for u in doc["undecided"] if u.startswith("a client token is needed")]
    assert token_line and doc["problems"] == [], doc
    code, doc = run_json(capsys, "--role", "coordinator", "--config", str(cfg))
    assert code == 3 and doc["role"] == "coordinator", doc
    assert any(u.startswith("a client token is needed") for u in doc["undecided"]), doc


def test_td39_client_checks_reach_version_and_token_only(env, coordinator, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    code, doc = run_json(capsys, "--role", "client", "--server", coordinator.url)
    assert code == 0, doc
    assert doc["role"] == "client" and doc["presets"] == [], doc
    assert {r.path for r in coordinator.requests} <= {"/api/health", "/api/whoami"}
    code, doc = run_json(capsys, "--role", "client", "--server", f"http://127.0.0.1:{free_port()}")
    assert code == 3, doc


# ── rcm worker --check 의 두 줄 (TD18) ───────────────────────────────────────


def worker_check(capsys, env: Path, srv: FakeRcm) -> tuple[int, list[str]]:
    cfg = worker_toml(env, srv.url)
    code = cli.main(["worker", "--check", "--config", str(cfg), "--data", str(env / "wd")])
    return code, capsys.readouterr().out.splitlines()


def check_line(lines: list[str], name: str) -> str | None:
    return next((ln for ln in lines if ln.split()[1:2] == [name]), None)


def test_td18_worker_check_keeps_its_rows_and_adds_a_missing_line(
    env, srv, svcbin, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setenv("PATH", f"{svcbin}:/usr/bin:/bin")
    srv.route("GET", "/worker/spec", spec_doc(preset("build"), preset("lint", requires=(LACK,))))
    code, lines = worker_check(capsys, env, srv)
    names = [ln.split()[1] for ln in lines if ln.strip()]
    assert names[:2] == ["python", "server"], lines
    for kept in ("token", "pool", "repos", "fingerprint", "presets"):
        assert kept in names, (kept, lines)
    last = lines[-1]
    assert "1 missing" in last and "rcm doctor --role worker" in last, lines
    version = check_line(lines, "version")
    assert version is not None and version.startswith("ok "), lines


@pytest.mark.parametrize("kind,want", [("wheel", "warn"), ("editable", "FAIL")])
def test_td18_worker_check_version_row(env, srv, svcbin, monkeypatch, capsys, kind, want):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.route("GET", "/api/health", {"ok": True, "version": "99.0.0"})
    monkeypatch.setattr(follow, "install_kind", lambda: kind)
    code, lines = worker_check(capsys, env, srv)
    version = check_line(lines, "version")
    assert version is not None and version.startswith(want), lines
    if want == "warn":
        assert "registering follows the server version" in version, version
        assert code == 0, lines
    else:
        assert "cannot follow" in version and code == 1, lines


@pytest.mark.parametrize(
    "presets,mark",
    [
        ((preset("build"),), "ok "),  # 빠진 것 0개
        ((preset("build"), preset("lint", requires=(LACK,))), "warn"),  # 1개
    ],
)
def test_td18_the_missing_line_is_ok_only_when_nothing_is_missing(
    env, srv, svcbin, monkeypatch, capsys, presets, mark
):
    """결정표 3: 끝 줄은 빠진 것이 0개일 때만 ok, 하나라도 있거나 셀 수 없으면(—) warn 이다.
    프로세스 종료 코드는 다른 행이 정한 그대로다 — 이 줄은 FAIL 이 되지 않는다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setenv("PATH", f"{svcbin}:/usr/bin:/bin")
    srv.route("GET", "/worker/spec", spec_doc(*presets))
    code, lines = worker_check(capsys, env, srv)
    assert lines[-1].startswith(mark) and " missing " in lines[-1], lines
    assert code == 0, lines


def test_td18_the_missing_line_warns_when_it_cannot_count(env, srv, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.routes.pop(("GET", "/worker/spec"))  # 옛 서버 — 명세를 못 받는다
    code, lines = worker_check(capsys, env, srv)
    assert lines[-1].startswith("warn") and "— missing" in lines[-1], lines
    assert code == 0, lines


@pytest.mark.parametrize("tty,answer", [(False, None), (True, False)])
def test_td28_worker_check_with_a_discovered_server_needs_a_person(
    env, srv, monkeypatch, capsys, tty, answer
):
    """§4.7: 비TTY 에서 mDNS 로 찾은 서버를 확인받지 못한 `rcm worker --check` 는 종료 3 이다.
    TTY 에서 사람이 거절해도 같다. 어느 쪽이든 서버에는 요청이 하나도 가지 않는다(TD10 과 같은
    규칙 — 프로브 argv 는 그 서버가 정한다)."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(doctor, "is_tty", lambda: tty)
    monkeypatch.setattr(doctor, "ask", lambda _q: answer)
    monkeypatch.setattr(cli, "discover", FakeDiscover((found("macmini", "127.0.0.1", srv.port),)))
    cfg = worker_toml(env, "auto")
    code = cli.main(["worker", "--check", "--config", str(cfg), "--data", str(env / "wd")])
    cap = capsys.readouterr()
    assert code == 3, (cap.out, cap.err)
    assert srv.requests == [], [(r.method, r.path) for r in srv.requests]
    assert "macmini" in cap.err or srv.url in cap.err, cap.err


def test_td28_worker_check_asks_and_goes_on_once_confirmed(env, srv, monkeypatch, capsys):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    asked: list[str] = []
    monkeypatch.setattr(doctor, "is_tty", lambda: True)
    monkeypatch.setattr(doctor, "ask", lambda q: asked.append(q) or True)
    monkeypatch.setattr(cli, "discover", FakeDiscover((found("macmini", "127.0.0.1", srv.port),)))
    cfg = worker_toml(env, "auto")
    code = cli.main(["worker", "--check", "--config", str(cfg), "--data", str(env / "wd")])
    cap = capsys.readouterr()
    assert code == 0, (cap.out, cap.err)
    assert asked and "macmini" in asked[0], asked
    assert srv.calls("GET", "/worker/spec")


# ── PR1(#266) 이 넘긴 것: recheck 의 skipped · eta --job 의 앞선 잡 ─────────────


@pytest.fixture
def client_env(env, monkeypatch) -> Iterator[FakeRcm]:
    s = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", s.url)
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    yield s
    s.close()


def test_recheck_names_the_skipped_local_only_checks_and_does_not_count_them(client_env, capsys):
    """서버가 `skipped` 에 실은 점검(그 머신에서는 못 도는 `local_only`)을 이름으로 보인다 —
    실패가 아니므로 종료 코드에 넣지 않는다(PR1 Remaining findings (i), 위임 결정 4)."""
    client_env.route(
        "POST",
        "/machines/mbp/recheck",
        {
            "jobs": [{"preset": "sim-check", "job_id": 3651}],
            "skipped": [{"preset": "store-check", "code": "local_only"}],
        },
    )
    code = cli.main(["recheck", "mbp"])
    cap = capsys.readouterr()
    assert code == 0, (cap.out, cap.err)
    assert "#3651" in cap.out, cap.out
    assert "store-check skipped on mbp [local_only]" in cap.out + cap.err, (cap.out, cap.err)


def test_recheck_with_only_skipped_checks_says_so_instead_of_nothing(client_env, capsys):
    client_env.route(
        "POST",
        "/machines/mbp/recheck",
        {"jobs": [], "skipped": [{"preset": "store-check", "code": "local_only"}]},
    )
    code = cli.main(["recheck", "mbp"])
    cap = capsys.readouterr()
    assert code == 0, (cap.out, cap.err)
    assert "store-check skipped on mbp [local_only]" in cap.out + cap.err, (cap.out, cap.err)
    assert "nothing to recheck" not in cap.out, cap.out


def test_eta_job_counts_no_line_for_a_job_no_machine_can_take(client_env, capsys):
    """`no_capable_machine` 잡은 갈 레인이 없다 — 「N ahead」를 세면 없는 순서를 지어낸다.
    `/api/eta` 의 `ahead` null 과 같이 그 칸을 찍지 않는다(PR1 Remaining findings (ii))."""
    waiting = {
        "id": 7,
        "preset": "rel",
        "state": "queued",
        "position": 2,
        "reason": "no_capable_machine",
        "estimate": {"wait_seconds": None, "expected_seconds": 600, "finish_at": None},
    }
    busy = {"id": 3, "preset": "gate", "state": "running", "position": None, "estimate": {}}
    ahead_of_it = {**busy, "id": 5, "state": "queued", "position": 1, "reason": None}
    client_env.route(
        "GET",
        "/api/status",
        {"pools": [{"name": "default", "queue": [busy, ahead_of_it, waiting]}]},
    )
    code = cli.main(["eta", "--job", "7"])
    cap = capsys.readouterr()
    assert code == 0, (cap.out, cap.err)
    assert "ahead" not in cap.out, cap.out
    assert "no capable machine" in cap.out or "no_capable_machine" in cap.out, cap.out


# ── JSON 의 모양 (위임 결정 J · 2) · 명시한 서비스 파일의 역할 (E1) ────────────

TOP_KEYS = {
    "doctor_version",
    "role",
    "measured_in",
    "host",
    "server",
    "service",
    "shell",
    "rcm_requirements",
    "presets",
    "probes",
    "remote_machines",
    "summary",
    "problems",
    "undecided",
    "warnings",
    "exit",
}


def test_json_keys_are_always_there_and_unknown_values_are_null(worker, env, srv, capsys):
    """위임 결정 J: 모르는 값은 키를 빼지 않고 null 이다 — `--shell-path` 를 안 줬으면 `shell` 이
    null, 워커 진단에는 `remote_machines` 가 null. 서버 칸도 같은 키 다섯을 늘 싣는다."""
    code, doc = worker(capsys)
    assert set(doc) == TOP_KEYS, set(doc) ^ TOP_KEYS
    assert doc["doctor_version"] == 1
    assert doc["shell"] is None and doc["remote_machines"] is None, doc
    assert set(doc["server"]) == {"url", "found_by", "state", "generation", "version"}
    assert set(doc["service"]) == {"file", "manager", "path", "env_names"}
    # 닿지 못한 서버 — 버전 · 세대는 모르니 null 이고 키는 그대로 있다
    cfg = worker_toml(env, f"http://127.0.0.1:{free_port()}")
    sf = plist(env / "svc", "/usr/bin:/bin")
    code, doc = run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert set(doc) == TOP_KEYS
    assert doc["server"]["version"] is None and doc["server"]["generation"] is None, doc["server"]


def test_json_keys_for_the_client_and_coordinator_roles(env, coordinator, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    code, doc = run_json(capsys, "--role", "client", "--server", coordinator.url)
    assert set(doc) == TOP_KEYS, set(doc) ^ TOP_KEYS
    assert doc["service"] is None and doc["remote_machines"] is None, doc
    cfg = server_toml(env, coordinator.port)
    code, doc = run_json(capsys, "--role", "coordinator", "--config", str(cfg))
    assert set(doc) == TOP_KEYS, set(doc) ^ TOP_KEYS


def test_e1_an_explicit_service_file_for_another_role_is_used_with_a_warning(worker, env, capsys):
    """위임 결정 E1: `--service-file` 은 사람이 고른 파일이라 거르지 않는다. 그 역할의 명령을
    띄우지 않으면 warn 하고 그 파일로 잰다."""
    cfg = worker_toml(env, worker.srv.url)
    sf = plist(env / "svc", f"{worker.svcbin}:/usr/bin:/bin", command="serve")
    code, doc = run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert doc["service"]["file"] is not None and doc["service"]["path"].startswith("~/svcbin")
    assert any("rcm serve" in w and "rcm worker" in w for w in doc["warnings"]), doc["warnings"]
    assert code == 0, doc


# ── rcm 자신의 요구 (위임 결정 4 · 5 · 6 · E4) ─────────────────────────────────

REPO = '[[repos]]\nname = "app"\nurl = "https://example.invalid/app.git"\n'


def git_preset(name: str, repo: str = "app", **kw: Any) -> dict[str, Any]:
    return {**preset(name, **kw), "source_modes": ["git_ref"], "repo": repo}


def req(doc: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((r for r in doc["rcm_requirements"] if r["name"] == name), None)


def run_worker(capsys, env: Path, srv: FakeRcm, sf: Path, *extra: str, toml_extra: str = ""):
    cfg = worker_toml(env, srv.url, extra=toml_extra)
    return run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf), *extra
    )


def test_d4_an_unreachable_repo_fails_and_exits_1_but_offline_is_info(
    worker, env, srv, svcbin, monkeypatch, capsys
):
    """위임 결정 4: `[[repos]]` 에 닿지 못하면 git_ref 일을 못 받는다 — FAIL, 종료 1.
    `--offline` 이면 `git ls-remote` 를 돌리지 않고 info 다."""
    monkeypatch.setattr(
        cli, "_ls_remote_problem", lambda url, timeout=10.0, **_kw: "unreachable: nope"
    )
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    code, doc = run_worker(capsys, env, srv, sf, toml_extra=REPO)
    assert req(doc, "repos")["state"] == "fail", doc["rcm_requirements"]
    assert code == 1, doc
    code, doc = run_worker(capsys, env, srv, sf, "--offline", toml_extra=REPO)
    assert req(doc, "repos")["state"] == "info", doc["rcm_requirements"]
    assert code == 0, doc


def test_d5_a_systemd_unit_without_path_is_not_guessed_and_exits_3(worker, env, srv, capsys):
    """위임 결정 5: systemd 의 기본 PATH 는 배포판마다 달라 추정하지 않는다. 서비스 PATH 칸은
    null, 안내는 「Environment=PATH=… 를 넣어라」, 판정 못 함(종료 3)이다."""
    (env / "svc").mkdir()
    sf = env / "svc" / "rcm-worker.service"
    sf.write_text("[Service]\nExecStart=/x/rcm worker\nEnvironment=TD_EXTRA=1\n")
    srv.route("GET", "/worker/spec", spec_doc(preset("lint", requires=(LACK,))))
    code, doc = run_worker(capsys, env, srv, sf)
    assert doc["service"]["manager"] == "systemd" and doc["service"]["path"] is None, doc
    assert any("Environment=PATH=" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc  # 모르는 PATH 로 재서 「빠짐」(종료 1)을 지어내지 않는다
    assert doc["presets"] == [], doc["presets"]


def test_d5_a_launchd_plist_without_path_gets_launchds_default(worker, env, srv, capsys):
    """launchd 의 기본 PATH 는 `/usr/bin:/bin:/usr/sbin:/sbin` 이다(`_PATH_STDPATH`,
    `launchctl print gui/<uid>/<service>` 의 `default environment` 로 이 Mac 에서 확인)."""
    (env / "svc").mkdir()
    sf = env / "svc" / "com.remote-ci-monitor.worker.plist"
    sf.write_bytes(plistlib.dumps({"Label": "x", "ProgramArguments": ["/x/rcm", "worker"]}))
    code, doc = run_worker(capsys, env, srv, sf)
    assert doc["service"]["path"] == "/usr/bin:/bin:/usr/sbin:/sbin", doc["service"]


def test_d6_git_is_measured_for_git_ref_presets_and_a_missing_repo_fails(
    worker, env, srv, svcbin, monkeypatch, capsys
):
    """위임 결정 6 · E4: git 은 `[[repos]]` 유무가 아니라 이 머신에 오는 git_ref 프리셋이 있으면
    잰다. 그 프리셋의 저장소가 이 워커의 `[[repos]]` 에 없으면 `repo_missing` 으로 FAIL 이다 —
    넣어야 할 저장소 이름을 알려 줄 뿐 채우지는 않는다(§3.6 D8)."""
    monkeypatch.setattr(cli, "_ls_remote_problem", lambda url, timeout=10.0, **_kw: None)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    srv.route("GET", "/worker/spec", spec_doc(git_preset("deploy")))
    code, doc = run_worker(capsys, env, srv, sf)
    assert req(doc, "git") is not None, doc["rcm_requirements"]
    assert any("repo_missing" in p and "app" in p and "deploy" in p for p in doc["problems"]), doc
    assert code == 1, doc
    code, doc = run_worker(capsys, env, srv, sf, toml_extra=REPO)
    assert not any("repo_missing" in p for p in doc["problems"]), doc["problems"]
    assert code == 0, doc
    # git_ref 프리셋이 이 머신에 오지 않으면 git 을 재지 않는다 — [[repos]] 가 있어도
    srv.route("GET", "/worker/spec", spec_doc(preset("build"), git_preset("deploy", pool="linux")))
    code, doc = run_worker(capsys, env, srv, sf, toml_extra=REPO)
    assert req(doc, "git") is None, doc["rcm_requirements"]
    assert not any("repo_missing" in p for p in doc["problems"]), doc["problems"]


def test_d6_the_rcm_venv_on_the_service_path_is_a_warning(worker, env, srv, svcbin, capsys):
    """docs/operating.md 「The service PATH is what your presets run with」: rcm 이 든 venv 의
    bin 이 서비스 PATH 에 있으면 프리셋의 `python3` 이 그 venv 의 것이 된다 — warn."""
    venv_bin = env / "home" / "rcm-venv" / "bin"
    venv_bin.mkdir(parents=True)
    (env / "svc").mkdir()
    sf = env / "svc" / "com.remote-ci-monitor.worker.plist"
    sf.write_bytes(
        plistlib.dumps(
            {
                "Label": "x",
                "ProgramArguments": [str(venv_bin / "rcm"), "worker"],
                "EnvironmentVariables": {"PATH": f"{venv_bin}:{svcbin}:/usr/bin:/bin"},
            }
        )
    )
    code, doc = run_worker(capsys, env, srv, sf)
    assert any("~/rcm-venv/bin" in w and "PATH" in w for w in doc["warnings"]), doc["warnings"]
    assert str(env / "home") not in json.dumps(doc)
    assert code == 0, doc


def test_d6_sleep_is_guidance_only_on_macos(worker, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "PLATFORM", "darwin")
    code, doc = worker(capsys)
    row_ = req(doc, "sleep")
    assert row_ is not None and row_["state"] == "info" and "pmset" in row_["detail"], row_


# ── local_only 와 coordinator 진단 (TO6 · TO18 · 위임 결정 E3) ───────────────

LOCAL_ONLY_PRESET = """
[[presets]]
name = "rel"
argv = ["true"]
local_only = true
"""

TOOLS_HINT = f"""
[[tools]]
name = "{LACK}"
docs = "https://example.invalid/td-lack"
[tools.install]
any = "install td-lack from the docs"
"""


def test_to6_coordinator_shows_a_local_only_preset_as_needing_a_local_lane(
    env, coordinator, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = server_toml(env, coordinator.port)
    cfg.write_text(cfg.read_text() + LOCAL_ONLY_PRESET)
    code, doc = run_json(capsys, "--role", "coordinator", "--config", str(cfg))
    assert any(p["name"] == "rel" for p in doc["presets"]), doc["presets"]
    rel = row(doc, "rel")
    assert rel["applies"] == "local_lane_only" and "needs a local lane" in rel["note"], rel
    assert not any(
        p["name"] == "unit" and p["applies"] == "local_lane_only" for p in doc["presets"]
    )
    code, out, _ = run(capsys, "--role", "coordinator", "--config", str(cfg))
    line = next(ln for ln in out.splitlines() if " rel " in f" {ln} ")
    assert "needs a local lane" in line, line


def test_to6_coordinator_asks_the_servers_own_candidate_function(
    env, coordinator, monkeypatch, capsys
):
    """6-A-U1: doctor 는 같은 규칙을 다시 쓰지 않는다 — 서버의 후보 함수가 원격을 뺐는지로 본다.
    그 함수가 원격도 후보로 두면(변이) 「로컬 레인 필요」가 사라져야 한다."""
    from remote_ci_monitor import capabilities

    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = server_toml(env, coordinator.port)
    cfg.write_text(cfg.read_text() + LOCAL_ONLY_PRESET)
    monkeypatch.setattr(
        capabilities.CapabilitiesMixin, "machines_for", lambda self, preset, views, **kw: views
    )
    code, doc = run_json(capsys, "--role", "coordinator", "--config", str(cfg))
    assert not any(p["applies"] == "local_lane_only" for p in doc["presets"]), doc["presets"]


def test_to18_a_preset_whose_check_is_local_only_is_not_coming_and_not_needs_check(
    worker, srv, capsys
):
    """서버가 프리셋의 `unreachable_checks` 에 그 점검을 적으면 이 머신에서 돌 수 없는 점검이다 —
    「오지 않음(점검이 로컬 전용)」이고 「점검 필요」가 아니다(TO18 의 doctor 절반)."""
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("build"), preset("qa", checks=("localcheck",), unreachable=("localcheck",))
        ),
    )
    code, doc = worker(capsys)
    qa = row(doc, "qa")
    assert qa["applies"] == "not_coming" and qa["state"] != "needs_check", qa
    assert qa["code"] == "check_local_only" and "check is local-only" in (qa["note"] or ""), qa
    assert doc["summary"]["needs_check"] == 0, doc["summary"]
    assert code == 0, doc
    code, out, _ = worker.text(capsys)
    line = next(ln for ln in out.splitlines() if " qa " in f" {ln} ")
    assert "needs check" not in line and "local-only" in line, line


def test_to18_doctor_reads_unreachable_checks_not_which_presets_are_listed(worker, srv, capsys):
    """doctor 는 명세 목록에 무엇이 빠졌는지로 짐작하지 않는다 — 점검 프리셋이 목록에 있어도
    `unreachable_checks` 가 그 이름을 적으면 「오지 않음」이다."""
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(
            preset("build"),
            preset("localcheck"),
            preset("qa", checks=("localcheck",), unreachable=("localcheck",)),
        ),
    )
    code, doc = worker(capsys)
    qa = row(doc, "qa")
    assert (qa["applies"], qa["code"]) == ("not_coming", "check_local_only"), qa
    assert "localcheck" in (qa["note"] or ""), qa
    assert code == 0, doc


def test_to18_a_check_missing_from_the_list_is_not_guessed_local_only(worker, srv, capsys):
    """`unreachable_checks` 가 빈 목록이면 서버가 「닿을 수 있다」고 한 것이다. 점검 프리셋이
    명세 목록에 없어도(풀로 거른 명세 등) 로컬 전용으로 짐작하지 않는다 — 「점검 필요」다."""
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(preset("build"), preset("qa", checks=("elsewhere",))),
    )
    code, doc = worker(capsys)
    qa = row(doc, "qa")
    assert (qa["applies"], qa["state"]) == ("pool", "needs_check"), qa
    assert qa["code"] != "check_local_only", qa
    assert code == 0, doc


def test_to18_without_unreachable_checks_the_check_is_unknown_and_exits_3(worker, srv, capsys):
    """칸이 없는 명세는 그 점검이 이 머신에 닿는지 말하지 않는다 — 「오지 않음」도 「점검 필요」도
    단정하지 않고 「모름」, 종료 3 이다. `checks` 가 없는 프리셋은 이 칸과 상관없이 판정한다.
    `placement` 까지 없는 옛 서버는 TD5 가 이미 종료 3 이다(그 명세에는 `checks` 도 없다)."""
    qa = preset("qa", checks=("localcheck",))
    build = preset("build")
    for p in (qa, build):
        del p["unreachable_checks"]
    srv.route("GET", "/worker/spec", spec_doc(build, qa))
    code, doc = worker(capsys)
    got = row(doc, "qa")
    assert got["state"] == "unknown" and got["applies"] != "not_coming", got
    assert got["code"] != "check_local_only", got
    assert row(doc, "build")["state"] == "ready", doc["presets"]
    said = [u for u in doc["undecided"] if "check" in u]
    assert len(said) == 1 and "qa" in said[0] and "build" not in said[0], doc["undecided"]
    assert code == 3, doc


def test_e3_the_local_lane_rows_use_the_coordinators_tools_hints(
    env, coordinator, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = server_toml(env, coordinator.port)
    cfg.write_text(cfg.read_text() + TOOLS_HINT)
    code, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    lint = row(doc, "lint")
    assert lint["missing"][0]["install"] == "install td-lack from the docs", lint
    assert lint["missing"][0]["docs"] == "https://example.invalid/td-lack", lint


# ── 호스트 사실의 서버 저장과 노출 (TD21 · TD42 · TD31 · TD44 · TD19) ─────────

HOST = {
    "os": "linux",
    "os_release": "ubuntu 24.04",
    "os_like": "debian",
    "arch": "aarch64",
    "rosetta": None,
    "container": True,
    "root": True,
    "package_manager": "apt-get",
    "python": "3.12.3",
}


def m5n(tmp_path: Path, **kw: Any):
    from test_server_m5n import M5nServer

    return M5nServer(tmp_path, **kw)


def machine(srv: Any, worker: str, token: str | None = "alice") -> dict[str, Any]:
    status, doc = srv.req("GET", "/api/machines", token=token)
    assert status == 200, doc
    return {m["id"]: m for m in doc["machines"]}[worker]


def test_td21_the_workers_registration_carries_host_facts_into_rcm_machines(
    tmp_path, monkeypatch, capsys
):
    """e2e: 진짜 `RemoteWorker.register` → 진짜 서버 → `/api/machines` → `rcm machines`. 키 집합을
    손으로 맞춘 본문이 아니라 생산자가 실제로 보낸 것을 소비자가 읽는다."""
    from remote_ci_monitor import remote_worker as rw
    from remote_ci_monitor.client import WorkerClient
    from remote_ci_monitor.config import WorkerConfig

    srv = m5n(tmp_path)
    try:
        url = f"http://127.0.0.1:{srv.port}"
        cfg = WorkerConfig(server=url, token=srv.tokens["build-02"], pool="mac2", lanes=1)
        w = rw.RemoteWorker(cfg, client=WorkerClient(url, cfg.token), log=lambda _m: None)
        monkeypatch.setattr(doctor.hostsample, "host_facts", lambda **_kw: dict(HOST, machine="x"))
        w.register()
        host = machine(srv, "build-02").get("host")
        assert host == HOST, host  # 허용 목록 밖의 키(machine)는 서버가 버린다
        # `rcm machines` 는 HOME 의 client.toml 도 찾는다 — 실제 홈(운영 설정)을 읽지 않게
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("RCM_SERVER", url)
        monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
        code = cli.main(["machines", "--machine", "build-02"])
        out = capsys.readouterr().out
        assert code == 0, out
        line = next((ln for ln in out.splitlines() if ln.startswith("host ")), "")
        assert "ubuntu 24.04" in line and "aarch64" in line and "apt-get" in line, out
        assert "container" in line and "root" in line and "Python 3.12.3" in line, line
    finally:
        srv.close()


def test_td42_after_a_restart_host_facts_come_back_with_the_next_capability_report(tmp_path):
    srv = m5n(tmp_path)
    tokens = srv.tokens
    try:
        status, body = srv.register("build-02", pool="mac2")
        assert status == 200, body
        # 등록 본문의 host — 시험 도우미는 옛 워커처럼 host 를 안 싣는다. 진짜 본문을 보낸다
        reg = {"pool": "mac2", "lanes": 1, "host_name": "b2", "version": __version__, "host": HOST}
        status, body = srv.req("POST", "/worker/register", token="build-02", json_body=reg)
        assert status == 200, body
        assert machine(srv, "build-02").get("host") == HOST
    finally:
        srv.close()
    srv = m5n(tmp_path, tokens=tokens)  # 같은 data_dir — 메모리의 호스트 사실은 사라진다
    try:
        assert machine(srv, "build-02").get("host") is None  # 받기 전에는 모른다(—)
        status, body = srv.report("build-02", srv.app.generation, extra={"host": HOST})
        assert status == 200, body
        assert machine(srv, "build-02").get("host") == HOST
    finally:
        srv.close()


def test_td31_anonymous_machines_reads_carry_no_host_facts(tmp_path):
    srv = m5n(tmp_path)
    try:
        srv.reported("build-02", pool="mac2", extra={"host": HOST})
        assert machine(srv, "build-02").get("host") == HOST  # 토큰 있는 응답에는 있다
        anon = machine(srv, "build-02", token=None)  # read_auth = none — 토큰 없이 읽힌다
        assert "host" not in anon, anon
    finally:
        srv.close()


def test_td44_with_basic_read_auth_host_facts_need_a_token(tmp_path):
    import base64

    srv = m5n(tmp_path, extra='read_auth = "basic"')
    try:
        srv.reported("build-02", pool="mac2", extra={"host": HOST})
        assert srv.req("GET", "/api/machines")[0] == 401
        assert machine(srv, "build-02").get("host") == HOST  # Bearer
        basic = base64.b64encode(f"alice-laptop:{srv.tokens['alice']}".encode()).decode()
        status, doc = srv.req("GET", "/api/machines", headers={"Authorization": f"Basic {basic}"})
        assert status == 200, doc
        got = {m["id"]: m for m in doc["machines"]}["build-02"]
        assert got.get("host") == HOST, got
    finally:
        srv.close()


def test_td19_tools_hints_are_not_in_status_or_machines(tmp_path):
    """`[[tools]]` 는 `/worker/spec`(워커 토큰)으로만 나간다. `/api/status` · `/api/machines` 는
    토큰 없이 읽힐 수 있어 싣지 않는다 — 처음부터 초록인 성질을 잠근다."""
    hint = '\n[[tools]]\nname = "rcm-m5n-faketool"\n[tools.install]\nany = "td19-install-text"\n'
    srv = m5n(tmp_path, extra=hint)
    try:
        srv.reported("build-02", pool="mac2")
        for path in ("/api/status", "/api/machines"):
            for token in (None, "alice"):
                status, doc = srv.req("GET", path, token=token)
                assert status == 200, doc
                text = json.dumps(doc)
                assert "td19-install-text" not in text and '"tools"' not in text, path
    finally:
        srv.close()


# ── /worker/spec 의 새 키 (TD15 · TD51 · TD16 의 서버 절반) ────────────────────


def test_td15_worker_spec_carries_pool_pools_placement_checks_and_tools(tmp_path):
    """doctor 가 이 머신에 올 프리셋만 세려면 서버가 키를 실어야 한다. 더하기만 한다 — 옛 워커는
    모르는 키를 무시한다(`RemoteWorker.measure` 가 같은 명세로 그대로 잰다)."""
    from remote_ci_monitor import remote_worker as rw
    from remote_ci_monitor.config import WorkerConfig

    hint = '\n[[tools]]\nname = "rcm-m5n-faketool"\n[tools.install]\nany = "td15-install"\n'
    srv = m5n(tmp_path, extra=hint)
    try:
        srv.generation("build-02")
        status, spec = srv.spec()
        assert status == 200, spec
        by = {p["name"]: p for p in spec["presets"]}
        need = by["needtool"]
        assert (need.get("pool"), need.get("placement")) == ("mac2", "pool"), need
        assert by["capgate"].get("placement") == "any-capable", by["capgate"]
        assert by["scenario"].get("checks") == ["simcheck"], by["scenario"]
        sim = by["simcheck"]
        assert (sim.get("source_modes"), sim.get("repo")) == (["git_ref"], "app"), sim
        assert all({"pool", "pools", "placement", "checks"} <= set(p) for p in by.values()), by
        assert spec.get("tools") == [
            {"name": "rcm-m5n-faketool", "docs": "", "install": {"any": "td15-install"}}
        ]
        cfg = WorkerConfig(server="http://x", token="t", pool="mac2", lanes=1)
        body = rw.RemoteWorker(cfg, client=None, log=lambda _m: None).measure(spec)  # type: ignore[arg-type]
        assert set(body["presets"]) == set(by), body["presets"]
    finally:
        srv.close()


def test_td51_worker_spec_pools_is_the_presets_pools_key_as_written(tmp_path):
    from test_server_m5n import PRESETS

    extra_preset = '\n[[presets]]\nname = "multi"\nargv = ["true"]\npools = ["mac2", "linux"]\n'
    srv = m5n(tmp_path, presets=PRESETS + extra_preset)
    try:
        srv.generation("build-02")
        _status, spec = srv.spec()
        multi = next(p for p in spec["presets"] if p["name"] == "multi")
        assert (multi.get("pool"), multi.get("pools")) == ("default", ["mac2", "linux"]), multi
    finally:
        srv.close()


def test_td15_doctor_reads_the_real_servers_spec(tmp_path, monkeypatch, capsys):
    """생산자(진짜 서버의 `/worker/spec`) → 소비자(doctor). 손으로 쓴 명세만 보면 키 이름이 갈려도
    초록이다 — 진짜 명세로 옛 서버 판정(TD5)이 나지 않고 풀 · 배치가 읽히는지 본다."""
    monkeypatch.setattr(doctor, "is_tty", lambda: False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    srv = m5n(tmp_path)
    try:
        monkeypatch.setenv("RCM_WORKER_TOKEN", srv.tokens["build-02"])
        cfg = worker_toml(tmp_path, f"http://127.0.0.1:{srv.port}", pool="mac2", extra=REPO)
        sf = plist(tmp_path / "svc", "/usr/bin:/bin")
        monkeypatch.setattr(cli, "_ls_remote_problem", lambda url, timeout=10.0, **_kw: None)
        code, doc = run_json(
            capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
        )
        assert not any("older rcm" in u for u in doc["undecided"]), doc["undecided"]
        assert (
            row(doc, "needtool")["applies"] == "pool" and row(doc, "needtool")["state"] == "missing"
        )
        assert row(doc, "capgate")["applies"] == "not_coming", row(doc, "capgate")
        assert row(doc, "ok")["applies"] == "other_pool", row(doc, "ok")  # 기본 풀 전용
        assert code == 1, doc
    finally:
        srv.close()


# ── /worker/spec 의 unreachable_checks (TO18 의 서버 절반 · 위임 결정 2) ───────

GATED = """
[[presets]]
name = "relcheck"
source_modes = ["git_ref"]
repo = "app"
argv = ["true"]
local_only = true

[[presets]]
name = "gated"
pool = "mac2"
argv = ["true"]
checks = ["relcheck", "simcheck"]
"""


def test_to18_worker_spec_names_the_checks_a_remote_worker_cannot_reach(tmp_path):
    """서버가 프리셋마다 그 워커에서 닿을 수 없는 점검을 이유 코드와 함께 적는다. 빠진 것에서
    짐작하게 두지 않는다. 키는 모든 항목에 있고 없으면 빈 목록이다."""
    from test_server_m5n import PRESETS

    srv = m5n(tmp_path, presets=PRESETS + GATED)
    try:
        srv.generation("build-02")
        status, spec = srv.spec()
        assert status == 200, spec
        by = {p["name"]: p for p in spec["presets"]}
        assert "relcheck" not in by, sorted(by)
        assert by["gated"].get("unreachable_checks") == [
            {"name": "relcheck", "code": "check_local_only"}
        ], by["gated"]
        assert by["scenario"].get("unreachable_checks") == [], by["scenario"]
        assert all(p.get("unreachable_checks") == [] for n, p in by.items() if n != "gated"), by
    finally:
        srv.close()


def test_to18_worker_spec_asks_the_servers_check_reachable(tmp_path, monkeypatch):
    """칸은 서버의 후보 함수(`check_reachable`)가 채운다 — 같은 규칙을 다시 쓰지 않는다(6-A-U1).
    그 함수가 「닿는다」고 하면(변이) 칸이 비어야 한다."""
    from remote_ci_monitor import capabilities
    from test_server_m5n import PRESETS

    srv = m5n(tmp_path, presets=PRESETS + GATED)
    try:
        srv.generation("build-02")
        monkeypatch.setattr(
            capabilities.CapabilitiesMixin, "check_reachable", lambda self, view, check: True
        )
        _status, spec = srv.spec()
        gated = next(p for p in spec["presets"] if p["name"] == "gated")
        assert gated.get("unreachable_checks") == [], gated
    finally:
        srv.close()


def test_to18_doctor_reads_the_real_servers_unreachable_checks(tmp_path, monkeypatch, capsys):
    """생산자(진짜 서버) → 소비자(doctor). 서버가 칸을 비우면(변이) doctor 는 「점검 필요」로
    잘못 읽는다 — 손으로 쓴 명세만으로는 그 어긋남이 보이지 않는다."""
    from test_server_m5n import PRESETS

    monkeypatch.setattr(doctor, "is_tty", lambda: False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    srv = m5n(tmp_path, presets=PRESETS + GATED)
    try:
        monkeypatch.setenv("RCM_WORKER_TOKEN", srv.tokens["build-02"])
        cfg = worker_toml(tmp_path, f"http://127.0.0.1:{srv.port}", pool="mac2", extra=REPO)
        sf = plist(tmp_path / "svc", "/usr/bin:/bin")
        monkeypatch.setattr(cli, "_ls_remote_problem", lambda url, timeout=10.0, **_kw: None)
        _code, doc = run_json(
            capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
        )
        gated = row(doc, "gated")
        assert (gated["applies"], gated["code"]) == ("not_coming", "check_local_only"), gated
        assert "relcheck" in (gated["note"] or ""), gated
        assert not any("check" in u for u in doc["undecided"]), doc["undecided"]
    finally:
        srv.close()


# ── 읽지 못하는 서비스 파일 (P3 1라운드 HIGH · 검증자 2 #1) ───────────────────

TRUNCATED_PLIST = b'<?xml version="1.0"?><plist><dict><key>Label</key>'
NOT_A_DICT_PLIST = b'<?xml version="1.0"?><plist version="1.0"><array></array></plist>'


@pytest.mark.parametrize("body", [TRUNCATED_PLIST, NOT_A_DICT_PLIST], ids=["truncated", "array"])
def test_an_unreadable_plist_in_launch_agents_is_named_and_exits_3(
    env, srv, svcbin, monkeypatch, capsys, body
):
    """`--service-file` 없는 doctor 도 설치 자리의 plist 를 모두 읽는다. 깨진 파일 하나가
    traceback 을 내지 않는다 — 그 파일 이름과 이유를 「판정 못 함」에 적고 종료 3 이다. 함께 있는
    읽히는 파일로도 재지 않는다(그 깨진 파일이 이 역할의 것이었는지 모른다)."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    # 도구가 없는 프리셋 — 서비스 파일 없이 기본 PATH 로 재도 빠짐(종료 1)이 생기지 않게
    srv.route("GET", "/worker/spec", spec_doc(preset("build", requires=())))
    cfg = worker_toml(env, srv.url)
    agents = env / "home" / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.remote-ci-monitor.broken.plist").write_bytes(body)
    code, out, err = run(capsys, "--role", "worker", "--config", str(cfg), "--json")
    assert "Traceback" not in err, err
    doc = json.loads(out)
    assert code == 3, doc
    said = [u for u in doc["undecided"] if "com.remote-ci-monitor.broken.plist" in u]
    assert said and "cannot read" in said[0], doc["undecided"]
    assert str(env / "home") not in out + err
    # 읽히는 파일이 함께 있어도 그것으로 재지 않는다 — 못 읽은 파일이 이 역할의 서비스였는지
    # 모르므로 그 자리 전체가 모름이다(P3 4라운드 LOW, 검증자 1 #5)
    plist(agents, f"{svcbin}:/usr/bin:/bin")
    code, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] is None and doc["service"]["path"] is None, doc["service"]
    assert doc["presets"] == [], doc
    assert code == 3 and any("broken.plist" in u for u in doc["undecided"]), doc


@pytest.mark.parametrize("body", [TRUNCATED_PLIST, NOT_A_DICT_PLIST], ids=["truncated", "array"])
def test_an_unreadable_explicit_service_file_is_unknown_not_a_crash(
    env, srv, monkeypatch, capsys, body
):
    """`--service-file` 로 준 파일을 못 읽으면 그 PATH 를 모른다 — 기본 PATH 로 재지 않고
    종료 3 이다. 파일이 아예 없는 것은 지금처럼 사용법 오류(2)다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    (env / "svc").mkdir()
    sf = env / "svc" / "com.remote-ci-monitor.worker.plist"
    sf.write_bytes(body)
    cfg = worker_toml(env, srv.url)
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf), "--json"
    )
    assert "Traceback" not in err, err
    doc = json.loads(out)
    assert code == 3, doc
    assert doc["service"]["path"] is None and doc["presets"] == [], doc
    assert any("cannot read" in u and sf.name in u for u in doc["undecided"]), doc["undecided"]


def test_a_plist_with_wrong_value_types_is_unreadable_not_a_crash(env, srv, monkeypatch, capsys):
    """dict 안의 값이 틀린 모양(환경 변수가 목록, 한도가 dict)이어도 traceback 이 아니다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    (env / "svc").mkdir()
    sf = env / "svc" / "com.remote-ci-monitor.worker.plist"
    sf.write_bytes(
        plistlib.dumps(
            {
                "Label": "x",
                "ProgramArguments": ["/x/rcm", "worker"],
                "EnvironmentVariables": ["PATH=/usr/bin"],
                "SoftResourceLimits": {"NumberOfFiles": {"soft": 1}},
            }
        )
    )
    cfg = worker_toml(env, srv.url)
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf), "--json"
    )
    assert "Traceback" not in err, err
    doc = json.loads(out)
    assert code == 3 and any("cannot read" in u for u in doc["undecided"]), doc


def test_a_broken_systemd_line_is_named_not_silently_dropped(env, srv, monkeypatch, capsys):
    """닫히지 않은 따옴표의 `Environment=` 줄을 조용히 건너뛰면 「PATH 를 주지 않았다」는 틀린
    안내가 나간다. 그 줄을 이름으로 보이고(값은 찍지 않는다) 종료 3 이다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    (env / "svc").mkdir()
    sf = env / "svc" / "rcm-worker.service"
    sf.write_text(
        '[Service]\nExecStart=/x/rcm worker\nEnvironment="PATH=/usr/bin:/bin TD_SECRET=td-v\n'
    )
    cfg = worker_toml(env, srv.url)
    code, out, err = run(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf), "--json"
    )
    assert "Traceback" not in err, err
    doc = json.loads(out)
    assert code == 3, doc
    said = [u for u in doc["undecided"] if "line 3" in u and "Environment" in u]
    assert said and sf.name in said[0], doc["undecided"]
    assert "td-v" not in out + err


# ── git ls-remote 는 서비스 환경과 shim 규칙으로 (P3 1라운드 MED · 검증자 1 #1 #2, 2 #2) ──


@pytest.fixture
def clt_shim(env, monkeypatch) -> tuple[Path, Path]:
    """가짜 `/usr/bin/` 의 `git` shim(바이트에 `libxcselect`)과 CLT 가 없는 `xcode-select`.
    shim 이 실행되면 표지 파일이 생긴다. (shim 폴더, 표지)"""
    from remote_ci_monitor import runner

    d = env / "usr-bin"
    d.mkdir()
    marker = env / "shim-ran"
    script(d / "git", f"# links against libxcselect.dylib\ntouch {marker}\necho 'git 2.0'")
    monkeypatch.setattr(runner, "SHIM_DIR", str(d) + "/")
    monkeypatch.setattr(runner, "SHIM_PLATFORM", "darwin")
    monkeypatch.setattr(runner, "XCODE_SELECT", str(script(env / "no-clt-select", "exit 2")))
    return d, marker


def local_repo(env: Path) -> str:
    """닿지 않는 저장소 — 네트워크를 쓰지 않는 로컬 경로."""
    return f'[[repos]]\nname = "app"\nurl = "{env / "nowhere.git"}"\n'


def shell_git(env: Path, monkeypatch) -> Path:
    """셸 PATH 에만 있는 `git` — 실행되면 표지를 남긴다. 서비스 환경으로 재는 쪽은 이것을 쓰면
    안 된다."""
    d = env / "shellbin"
    d.mkdir()
    marker = env / "shell-git-ran"
    script(d / "git", f"touch {marker}\nexit 128")
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    return marker


def test_doctor_ls_remote_runs_the_service_paths_git_in_the_service_env(
    env, srv, svcbin, monkeypatch, capsys
):
    """doctor 의 `git ls-remote` 는 셸 PATH 의 git 이 아니라 서비스 PATH 에서 찾은 git 을 서비스
    환경으로 돌린다 — 셸의 `JAVA_HOME` 이 새지 않고 서비스 파일의 변수는 보인다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setenv("JAVA_HOME", "/td-shell-java")
    shell_marker = shell_git(env, monkeypatch)
    seen = env / "svc-git-env"
    script(svcbin / "git", f'env > "{seen}"\nexit 0')
    cfg = worker_toml(env, srv.url, extra=local_repo(env))
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin", extra={"TD_SVC_ONLY": "1"})
    code, doc = run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert not shell_marker.exists(), "doctor ran the shell's git"
    assert seen.exists(), doc["rcm_requirements"]
    got = seen.read_text()
    assert "TD_SVC_ONLY=1" in got and "GIT_TERMINAL_PROMPT=0" in got, got
    assert "JAVA_HOME" not in got, got
    assert req(doc, "repos")["state"] == "ok", doc["rcm_requirements"]


def test_doctor_never_runs_a_git_shim_for_ls_remote(env, srv, clt_shim, monkeypatch, capsys):
    """CLT 없는 macOS 의 `/usr/bin/git` shim 을 실행하면 설치 대화상자가 뜬다. doctor 는 그 shim
    으로 `git ls-remote` 를 돌리지 않고 git 을 「없음(CLT 필요)」으로, 저장소를 FAIL 로 보인다."""
    shims, marker = clt_shim
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    shell_marker = shell_git(env, monkeypatch)
    cfg = worker_toml(env, srv.url, extra=local_repo(env))
    sf = plist(env / "svc", f"{shims}:/bin")
    code, doc = run_json(
        capsys, "--role", "worker", "--config", str(cfg), "--service-file", str(sf)
    )
    assert not marker.exists() and not shell_marker.exists(), "a git ran"
    repos = req(doc, "repos")
    assert repos["state"] == "fail" and "git missing" in repos["detail"], repos
    assert code == 1, doc


def test_worker_check_git_row_follows_the_shim_rule(env, srv, clt_shim, monkeypatch, capsys):
    """`rcm worker --check` 의 git 행도 `shutil.which` 가 아니라 shim 규칙을 거친다. shim 은
    「missing (needs Command Line Tools)」이고 `git ls-remote` 를 돌리지 않는다."""
    shims, marker = clt_shim
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setenv("PATH", f"{shims}:/bin")
    cfg = worker_toml(env, srv.url, extra=local_repo(env))
    code = cli.main(["worker", "--check", "--config", str(cfg), "--data", str(env / "wd")])
    lines = capsys.readouterr().out.splitlines()
    git = check_line(lines, "git")
    assert git is not None and git.startswith("FAIL"), lines
    assert "needs Command Line Tools" in git, git
    assert not marker.exists(), "worker --check ran the git shim"
    assert code == 1, lines


def test_rcm_check_git_row_follows_the_shim_rule(env, clt_shim, monkeypatch, capsys):
    shims, marker = clt_shim
    monkeypatch.setenv("PATH", f"{shims}:/bin")
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = env / "server.toml"
    cfg.write_text(f'[server]\ndata_dir = "{env / "data"}"\n\n' + local_repo(env))
    cli.main(["check", "--config", str(cfg)])
    lines = capsys.readouterr().out.splitlines()
    git = check_line(lines, "git")
    assert git is not None and git.startswith("FAIL"), lines
    assert "needs Command Line Tools" in git, git
    assert not marker.exists(), "rcm check ran the git shim"


# ── 서버가 재지 않은 로컬 레인 프리셋은 모름 (P3 1라운드 MED · 검증자 1 #3) ────


@pytest.mark.parametrize(
    "measured",
    [None, {"unit": {"missing_tools": [], "missing_passthrough": []}}],
    ids=["no-key", "no-entry"],
)
def test_a_local_lane_preset_the_server_did_not_measure_is_unknown(
    env, coordinator, monkeypatch, capsys, measured
):
    """`/api/health` 에 `preset_env` 가 없거나 그 프리셋 항목이 없으면 서버가 무엇을 쟀는지
    모른다. `requires` 가 있는 프리셋을 빈 「빠진 도구」로 읽어 ready 라 부르지 않는다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health: dict[str, Any] = {"ok": True, "version": __version__}
    if measured is not None:
        health["preset_env"] = measured
    coordinator.route("GET", "/api/health", health)
    cfg = server_toml(env, coordinator.port)
    code, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert row(doc, "lint")["state"] == "unknown", row(doc, "lint")
    if measured is None:
        assert row(doc, "unit")["state"] == "unknown", row(doc, "unit")
    else:
        assert row(doc, "unit")["state"] == "ready", row(doc, "unit")
    assert any("lint" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc


# ── 서버가 받는 호스트 사실의 정리 (P3 1라운드 MED · 검증자 2 #3 · #7) ───────


def register_host(srv: Any, worker: str, host: Any = None) -> None:
    body: dict[str, Any] = {"pool": "mac2", "lanes": 1, "host_name": "b2", "version": __version__}
    if host is not None:
        body["host"] = host
    status, got = srv.req("POST", "/worker/register", token=worker, json_body=body)
    assert status == 200, got


def test_host_facts_lose_control_characters_and_stop_at_64(tmp_path):
    """값은 `rcm machines` 의 터미널과 웹에 그대로 나간다 — 제어문자(ESC · BEL · C1)를 지우고
    64자에서 자른다."""
    srv = m5n(tmp_path)
    try:
        register_host(
            srv,
            "build-02",
            {**HOST, "os_release": "ubuntu\x1b[2J 24.04\x07\x9b", "package_manager": "a" * 200},
        )
        host = machine(srv, "build-02")["host"]
        assert host["os_release"] == "ubuntu[2J 24.04", host
        assert host["package_manager"] == "a" * 64, host
    finally:
        srv.close()


def test_host_facts_with_the_wrong_type_are_unknown(tmp_path):
    """`rosetta` · `container` · `root` 는 참 · 거짓 · null 만 받는다 — 문자열 `"false"` 를
    참으로 읽지 않는다. 문자열 칸에 온 숫자 · 목록 · 참거짓도 「모름」(null)이다."""
    srv = m5n(tmp_path)
    try:
        register_host(
            srv,
            "build-02",
            {
                **HOST,
                "rosetta": "false",
                "container": 1,
                "root": "true",
                "python": 3.12,
                "os": ["linux"],
                "arch": True,
            },
        )
        host = machine(srv, "build-02")["host"]
        for key in ("rosetta", "container", "root", "python", "os", "arch"):
            assert host[key] is None, (key, host)
        assert host["os_release"] == HOST["os_release"], host
    finally:
        srv.close()


def test_a_registration_without_host_forgets_the_old_facts_but_a_report_does_not(tmp_path):
    """등록은 새 프로세스다 — `host` 없는 등록(옛 워커)은 앞 프로세스의 사실을 물려받지 않는다.
    능력 보고에 `host` 가 없으면 지금 사실을 그대로 둔다."""
    srv = m5n(tmp_path)
    try:
        register_host(srv, "build-02", HOST)
        assert machine(srv, "build-02")["host"] == HOST
        status, body = srv.report("build-02", srv.app.generation)
        assert status == 200, body
        assert machine(srv, "build-02")["host"] == HOST  # 보고에 host 가 없어도 그대로
        register_host(srv, "build-02")  # host 없는 등록
        assert machine(srv, "build-02").get("host") is None
    finally:
        srv.close()


# ── 시험 대조 WEAK 여덟 (P3 1라운드) ─────────────────────────────────────────


@pytest.mark.parametrize("where", ["env", "file"])
def test_td18_worker_check_version_row_fails_for_a_pinned_install(
    env, srv, monkeypatch, capsys, where
):
    """고정 설치(`RCM_CLIENT_VERSION` 이나 한 줄 파일의 버전)는 서버 버전을 따라가지 않는다 —
    설치 종류가 따라갈 수 있는 wheel 이어도 버전이 다르면 FAIL 이다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.delenv("RCM_CLIENT_VERSION", raising=False)
    srv.route("GET", "/api/health", {"ok": True, "version": "99.0.0"})
    monkeypatch.setattr(follow, "install_kind", lambda: "wheel")
    if where == "env":
        monkeypatch.setenv("RCM_CLIENT_VERSION", "0.9.0")
    else:
        pin = env / "home" / ".config" / "rcm" / "client-version"
        pin.parent.mkdir(parents=True)
        pin.write_text("0.9.0\n")
    code, lines = worker_check(capsys, env, srv)
    version = check_line(lines, "version")
    assert version is not None and version.startswith("FAIL"), lines
    assert "pinned" in version and "cannot follow" in version, version
    assert code == 1, lines


MAC_HOST = {
    "os": "darwin",
    "os_release": "15.6",
    "os_like": None,
    "arch": "arm64",
    "rosetta": True,
    "container": False,
    "root": False,
    "package_manager": "brew",
    "python": "3.12.1",
}


def test_td21_rosetta_from_a_real_registration_shows_in_rcm_machines(tmp_path, monkeypatch, capsys):
    """macOS 모양의 호스트 사실 — Rosetta 가 참이면 진짜 등록 본문을 거쳐 `rcm machines` 의
    host 줄에 `(Rosetta)` 로 보인다."""
    from remote_ci_monitor import remote_worker as rw
    from remote_ci_monitor.client import WorkerClient
    from remote_ci_monitor.config import WorkerConfig

    srv = m5n(tmp_path)
    try:
        url = f"http://127.0.0.1:{srv.port}"
        cfg = WorkerConfig(server=url, token=srv.tokens["build-02"], pool="mac2", lanes=1)
        w = rw.RemoteWorker(cfg, client=WorkerClient(url, cfg.token), log=lambda _m: None)
        monkeypatch.setattr(doctor.hostsample, "host_facts", lambda **_kw: dict(MAC_HOST))
        w.register()
        assert machine(srv, "build-02").get("host") == MAC_HOST
        # `rcm machines` 는 HOME 의 client.toml 도 찾는다 — 실제 홈(운영 설정)을 읽지 않게
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("RCM_SERVER", url)
        monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
        code = cli.main(["machines", "--machine", "build-02"])
        out = capsys.readouterr().out
        assert code == 0, out
        line = next((ln for ln in out.splitlines() if ln.startswith("host ")), "")
        assert "arm64 (Rosetta)" in line and "15.6" in line and "brew" in line, line
    finally:
        srv.close()


def test_td26_coordinator_worker_with_no_preset_coming_exits_1(
    env, coordinator, monkeypatch, capsys
):
    """서버 자신의 레인(`default` 풀)에 올 프리셋이 하나도 없으면 coordinator-worker 도 워커처럼
    종료 1 이고 이유를 적는다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = env / "server.toml"
    cfg.write_text(
        f"""[server]
bind = "127.0.0.1"
port = {coordinator.port}
data_dir = "{env / "data"}"

[[presets]]
name = "ios"
argv = ["true"]
pool = "mac2"
"""
    )
    code, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert row(doc, "ios")["applies"] == "other_pool", doc["presets"]
    assert any("no jobs will come to this machine" in p for p in doc["problems"]), doc
    assert code == 1, doc


def test_td39_client_version_mismatch_warns_and_a_missing_token_is_undecided(
    env, coordinator, monkeypatch, capsys
):
    """client 역할: 서버와 버전이 다르면 경고(종료 그대로), 토큰이 없으면 판정 못 함(3)이다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    coordinator.route("GET", "/api/health", {"ok": True, "version": "99.0.0"})
    code, doc = run_json(capsys, "--role", "client", "--server", coordinator.url)
    assert any("v99.0.0" in w and __version__ in w for w in doc["warnings"]), doc["warnings"]
    assert code == 0, doc
    monkeypatch.delenv("RCM_TOKEN")
    coordinator.requests.clear()
    code, doc = run_json(capsys, "--role", "client", "--server", coordinator.url)
    assert any("client token is needed" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc
    assert not coordinator.calls("GET", "/api/whoami"), "asked whoami without a token"


def test_td41_td35_td39_reads_carry_the_right_bearer_token(
    env, coordinator, srv, svcbin, monkeypatch, capsys
):
    """coordinator 진단은 client 토큰으로, worker 진단은 worker 토큰으로 읽는다 — 요청마다
    `Authorization: Bearer <그 토큰>` 이 실린다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = server_toml(env, coordinator.port)
    run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    paths = {r.path for r in coordinator.requests}
    assert {"/api/health", "/api/whoami", "/api/machines"} <= paths, paths
    for r in coordinator.requests:
        assert r.headers.get("authorization") == f"Bearer {CLIENT_TOKEN}", (r.path, r.headers)
    run_json(capsys, "--role", "client", "--server", coordinator.url)
    who = coordinator.calls("GET", "/api/whoami")[-1]
    assert who.headers.get("authorization") == f"Bearer {CLIENT_TOKEN}", who.headers
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    wcfg = worker_toml(env, srv.url)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    run_json(capsys, "--role", "worker", "--config", str(wcfg), "--service-file", str(sf))
    for path in ("/api/whoami", "/worker/spec"):
        got = srv.calls("GET", path)[-1]
        assert got.headers.get("authorization") == f"Bearer {TOKEN}", (path, got.headers)


def test_td42_the_real_workers_periodic_report_carries_host_facts(tmp_path, monkeypatch):
    """e2e: 서버가 재시작해 메모리의 사실을 잃은 뒤, 진짜 `RemoteWorker.measure_and_report` 가 만든
    주기 보고 본문이 `host` 를 다시 채운다 — 손으로 쓴 본문이 아니라 생산자가 보낸 것이다."""
    from remote_ci_monitor import remote_worker as rw
    from remote_ci_monitor.client import WorkerClient
    from remote_ci_monitor.config import WorkerConfig

    monkeypatch.setattr(doctor.hostsample, "host_facts", lambda **_kw: dict(HOST))
    srv = m5n(tmp_path)
    tokens = srv.tokens
    try:
        url = f"http://127.0.0.1:{srv.port}"
        cfg = WorkerConfig(server=url, token=tokens["build-02"], pool="mac2", lanes=1)
        w = rw.RemoteWorker(cfg, client=WorkerClient(url, cfg.token), log=lambda _m: None)
        w.register()
    finally:
        srv.close()
    srv = m5n(tmp_path, tokens=tokens)  # 같은 data_dir — 메모리의 호스트 사실은 사라진다
    try:
        url = f"http://127.0.0.1:{srv.port}"
        assert machine(srv, "build-02").get("host") is None
        w.client = WorkerClient(url, cfg.token)
        assert w.measure_and_report(), "the report was refused"
        assert machine(srv, "build-02").get("host") == HOST
    finally:
        srv.close()


def test_td45_two_candidates_in_a_terminal_let_the_person_choose(
    env, srv, svcbin, monkeypatch, capsys
):
    """같은 자리에 후보가 둘이고 TTY 면 사람이 고른다 — 고른 파일로 잰다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    cfg = worker_toml(env, srv.url)
    agents = env / "home" / "Library" / "LaunchAgents"
    plist(agents, "/bin", name="com.remote-ci-monitor.worker-a.plist")
    picked = plist(agents, f"{svcbin}:/usr/bin:/bin", name="com.remote-ci-monitor.worker-b.plist")
    offered: list[list[str]] = []

    def choose(paths: list[Path], _home: str) -> Path:
        offered.append([p.name for p in paths])
        return next(p for p in paths if p.name == picked.name)

    monkeypatch.setattr(doctor, "is_tty", lambda: True)
    monkeypatch.setattr(doctor, "choose", choose)
    code, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert offered == [[f"com.remote-ci-monitor.worker-{x}.plist" for x in "ab"]], offered
    assert doc["service"]["file"] == "~/Library/LaunchAgents/" + picked.name, doc["service"]
    assert row(doc, "build")["state"] == "ready" and code == 0, doc


def test_td54_the_servers_local_probes_use_the_shared_run_probes_without_lang_c(
    tmp_path, monkeypatch
):
    """서버가 자기 레인을 잴 때도 워커 · doctor 와 같은 `hostsample.run_probes` 를 쓴다. 그래서
    프로브는 `LANG=C` 를 받지 않는다(호스트 사실 명령만 받는다)."""
    from remote_ci_monitor import hostsample

    show = tmp_path / "show-lang"
    script(show, 'echo "lang=$LANG"')
    probes = f'\n[[probes]]\nname = "lang"\nargv = ["{show}"]\n'
    srv = m5n(tmp_path, probes=probes)
    calls: list[Any] = []
    real = hostsample.run_probes
    monkeypatch.setattr(hostsample, "run_probes", lambda p, e: calls.append(list(p)) or real(p, e))
    try:
        srv.app.measure_capabilities(environ={"PATH": "/usr/bin:/bin"})
        assert calls and [p.name for p in calls[-1]] == ["lang"], calls
        probe = machine(srv, "@local")["fingerprint"]["probes"]["lang"]
        assert (probe["state"], probe["value"]) == ("ok", "lang="), probe
    finally:
        srv.close()


# ── 싸게 고친 LOW (P3 1라운드) ────────────────────────────────────────────────


def test_a_503_not_ok_health_is_not_shown_as_server_ok(worker, srv, env, coordinator, capsys):
    """서버가 503 `ok: false` 로 답하면(janitor 멈춤 · 워커 down) 본문은 오지만 서버가 성한 것은
    아니다 — `server.state` 를 ok 로 적지 않고 그 이유를 경고로 보인다(검증자 1 #4)."""
    sick = {"ok": False, "error": "janitor stalled", "version": __version__}
    srv.route("GET", "/api/health", sick, status=503)
    code, doc = worker(capsys)
    assert doc["server"]["state"] == "not ok", doc["server"]
    assert any("janitor stalled" in w for w in doc["warnings"]), doc["warnings"]
    coordinator.route("GET", "/api/health", sick, status=503)
    code, doc = run_json(capsys, "--role", "client", "--server", coordinator.url)
    assert doc["server"]["state"] == "not ok", doc["server"]


def test_d5_probes_are_not_run_when_the_service_path_is_unknown(worker, env, srv, svcbin, capsys):
    """서비스 PATH 를 모르면(PATH 없는 systemd 유닛) 프로브도 돌리지 않는다 — 빈 PATH 로 돌려
    「missing」을 지어내지 않고 「unknown」으로 둔다(검증자 1 #6)."""
    (env / "svc").mkdir()
    sf = env / "svc" / "rcm-worker.service"
    sf.write_text("[Service]\nExecStart=/x/rcm worker\n")
    probe = {"name": "td-ver", "argv": ["rcm-td-version"], "timeout_seconds": 5}
    srv.route("GET", "/worker/spec", spec_doc(preset("build"), probes=[probe]))
    code, doc = run_worker(capsys, env, srv, sf)
    assert doc["probes"]["td-ver"]["state"] == "unknown", doc["probes"]
    assert code == 3, doc


# ── 망가진 입력 표 (P3 2라운드 HIGH · MED) ────────────────────────────────────
#
# 같은 결함 부류(남이 준 값의 모양을 가정한다)가 두 라운드 이어졌다. 입력 하나씩 막지 않고 표로
# 잠근다: 서버의 네 라우트 · 설정 파일 · 서비스 파일의 두 관리자에 망가진 입력을 주면 traceback
# 없이 종료 2(사용법) 나 3(판정 못 함)이다. 그리고 그 이유가 읽는 자리의 사람용 문장이어야 한다 —
# 맨 바깥 경계의 `could not decide: <예외 종류>` 가 보이면 읽는 자리의 모양 검사가 빠진 것이다.

BOUNDARY = "could not decide:"
HTML_200 = RawReply(b"<!doctype html><title>proxy</title>", {"Content-Type": "text/html"})
EMPTY_200 = RawReply(b"")
#: 라우트마다 doctor 가 쓰는 칸의 타입만 틀린 dict
WRONG_TYPES: dict[str, Any] = {
    "/api/health": {"ok": "yes", "version": ["9"], "preset_env": {"unit": {"missing_tools": "j"}}},
    "/api/whoami": {"name": "mbp", "kind": ["worker"]},
    "/worker/spec": {
        "generation": "1:0",
        "probes": [{"name": "p", "argv": ["true"], "timeout_seconds": "soon"}],
        "presets": [{"name": "build", "requires": [HAVE], "env": ["A=1"], "pool": "mbp"}],
    },
    "/api/machines": {
        "generation": "1:0",
        "machines": [{"id": "mbp", "kind": "remote", "pool": "default", "presets": ["unit"]}],
    },
}
BAD_KINDS = ("html", "empty", "list", "null", "wrong-types")
ROLE_ROUTES = {
    "worker": ("/api/health", "/api/whoami", "/worker/spec"),
    "coordinator-worker": ("/api/health", "/api/whoami", "/api/machines"),
    "client": ("/api/health",),
}
SERVER_CASES = [
    pytest.param(role, route, kind, id=f"{role}{route.replace('/', '-')}-{kind}")
    for role, routes in ROLE_ROUTES.items()
    for route in routes
    for kind in BAD_KINDS
]


def run_guarded(capsys, *argv: str) -> tuple[int | None, str, str]:
    """`rcm doctor` 를 부르되 새어 나온 예외를 traceback 문자열로 바꾼다 — 시험이 「traceback
    없음」 단언에서 빨개지게(수집 · 호출 오류가 아니라)."""
    import traceback

    tb = ""
    try:
        code: int | None = cli.main(["doctor", *argv])
    except Exception:  # noqa: BLE001 — 바로 그것을 잡아 단언으로 보인다
        code, tb = None, traceback.format_exc()
    cap = capsys.readouterr()
    return code, cap.out, cap.err + tb


def said_lines(out: str, err: str) -> list[str]:
    """사람이 받은 「판정 못 함」 · 사용법 줄 — `--json` 의 undecided 와 stderr."""
    lines = err.splitlines()
    if out.strip():
        lines += json.loads(out)["undecided"]
    return lines


def assert_handled(code, out, err, want: str, codes=(2, 3)) -> None:
    assert "Traceback" not in err, err
    assert code in codes, (code, out, err)
    lines = said_lines(out, err)
    # 경계에 닿았다 = 읽는 자리가 이 모양을 몰랐다
    assert not any(BOUNDARY in ln for ln in lines), lines
    assert any(want in ln for ln in lines), (want, lines)


def broken_server(role: str) -> FakeRcm:
    s = FakeRcm()
    s.route(
        "GET",
        "/api/health",
        {
            "ok": True,
            "version": __version__,
            "preset_env": {"unit": {"missing_tools": [], "missing_passthrough": []}},
        },
    )
    kind = "worker" if role == "worker" else "client"
    s.route("GET", "/api/whoami", {"name": "mbp", "admin": False, "kind": kind})
    s.route("GET", "/worker/spec", spec_doc(preset("build")))
    s.route("GET", "/api/machines", machines_doc())
    return s


@pytest.mark.parametrize("role,route,kind", SERVER_CASES)
def test_a_server_answer_of_the_wrong_shape_is_named_and_exits_3(
    env, svcbin, monkeypatch, capsys, role, route, kind
):
    """HTML 200(틀린 포트 · 프록시) · 빈 200 · `[]` · `null` · 칸 타입이 틀린 dict 는 「서버가
    알아볼 수 없는 답을 줬다」이고 종료 3 이다 — 그 라우트 이름을 보인다."""
    srv = broken_server(role)
    try:
        body = {
            "html": HTML_200,
            "empty": EMPTY_200,
            "list": [],
            "null": None,
            "wrong-types": WRONG_TYPES[route],
        }[kind]
        srv.route("GET", route, body)
        if role == "worker":
            monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
            cfg = worker_toml(env, srv.url)
            sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
            argv = ["--role", role, "--config", str(cfg), "--service-file", str(sf)]
        elif role == "client":
            monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
            argv = ["--role", role, "--server", srv.url]
        else:
            monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
            argv = ["--role", role, "--config", str(server_toml(env, srv.port))]
        code, out, err = run_guarded(capsys, *argv, "--json")
        assert srv.calls("GET", route), "the broken route was never asked"
        assert_handled(code, out, err, route, codes=(3,))
    finally:
        srv.close()


NOT_UTF8 = b"\xff\xfe[server]\nlanes = 0\n"
SERVER_STRING = b'server = "x"\n'
SERVER_ARRAY = b"[[server]]\nlanes = 0\n"
#: (--role, 파일 이름, 내용) — 역할이 없으면 설정에서 역할을 읽는 길(`./rcm.toml`)이다
CONFIG_CASES = {
    "infer-not-utf8": (None, "rcm.toml", NOT_UTF8),
    "infer-server-string": (None, "rcm.toml", SERVER_STRING),
    "infer-server-array": (None, "rcm.toml", SERVER_ARRAY),
    "coordinator-not-utf8": ("coordinator", "server.toml", NOT_UTF8),
    "coordinator-server-string": ("coordinator", "server.toml", SERVER_STRING),
    "coordinator-server-array": ("coordinator", "server.toml", SERVER_ARRAY),
    "worker-not-utf8": ("worker", "worker.toml", NOT_UTF8),
    "worker-server-string": ("worker", "worker.toml", SERVER_STRING),
    "worker-server-array": ("worker", "worker.toml", b'[[server]]\nurl = "x"\n'),
    "client-not-utf8": ("client", "client.toml", NOT_UTF8),
    "client-server-string": ("client", "client.toml", SERVER_STRING),
    "client-server-array": ("client", "client.toml", b'[[server]]\nurl = "x"\n'),
}


@pytest.mark.parametrize("case", list(CONFIG_CASES))
def test_a_config_file_of_the_wrong_shape_is_a_usage_error(env, monkeypatch, capsys, case):
    """UTF-8 이 아닌 설정 · 표가 아닌 `server` 는 사용법 오류(종료 2)다 — 파일 이름과 이유를
    보이고 홈 폴더는 `~` 로 가린다."""
    role, name, body = CONFIG_CASES[case]
    proj = env / "home" / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    cfg = proj / name
    cfg.write_bytes(body)
    cfg.chmod(stat.S_IRUSR | stat.S_IWUSR)
    argv = ["--json"] if role is None else ["--role", role, "--config", str(cfg), "--json"]
    code, out, err = run_guarded(capsys, *argv)
    assert_handled(code, out, err, name, codes=(2,))
    assert str(env / "home") not in out + err, err


SVC_ENV_FILE = "[Service]\nExecStart=/x/rcm worker\nEnvironment=PATH={bin}:/usr/bin:/bin\n" + (
    "EnvironmentFile=/etc/rcm/worker.env\n"
)
SVC_SPECIFIER = "[Service]\nExecStart=/x/rcm worker\nEnvironment=PATH=%h/svcbin:/usr/bin:/bin\n"
#: (파일 이름, 내용, 판정 못 함 줄에 보일 것)
SERVICE_CASES = {
    "launchd-garbage": ("com.remote-ci-monitor.worker.plist", b"not a plist", "cannot read"),
    "launchd-array": ("com.remote-ci-monitor.worker.plist", NOT_A_DICT_PLIST, "cannot read"),
    "systemd-not-utf8": (
        "rcm-worker.service",
        b"[Service]\nExecStart=/x/rcm worker\nEnvironment=PATH=\xff\n",
        "not UTF-8",
    ),
    "systemd-environment-file": ("rcm-worker.service", SVC_ENV_FILE, "EnvironmentFile="),
    "systemd-specifier": ("rcm-worker.service", SVC_SPECIFIER, "line 3 (Environment=)"),
}


@pytest.mark.parametrize("case", list(SERVICE_CASES))
def test_a_service_file_rcm_cannot_fully_read_is_named_and_exits_3(
    env, srv, svcbin, monkeypatch, capsys, case
):
    """두 관리자의 깨진 서비스 파일, 그리고 rcm 이 읽지 않는 systemd 줄(`EnvironmentFile=`)과
    풀지 않는 지정자(`%h`)는 그 줄 · 파일 이름을 보이고 종료 3 이다 — 지정자를 글자 그대로 PATH 에
    넣어 없는 「빠짐」(종료 1)을 지어내지 않는다(P3 2라운드 MED)."""
    name, body, want = SERVICE_CASES[case]
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    (env / "svc").mkdir()
    sf = env / "svc" / name
    if isinstance(body, str):
        sf.write_text(body.format(bin=svcbin))
    else:
        sf.write_bytes(body)
    cfg = worker_toml(env, srv.url)
    argv = ["--role", "worker", "--config", str(cfg), "--service-file", str(sf), "--json"]
    code, out, err = run_guarded(capsys, *argv)
    assert_handled(code, out, err, want, codes=(3,))
    assert str(env / "home") not in out + err


def test_an_unexpected_error_is_could_not_decide_not_a_traceback(worker, env, monkeypatch, capsys):
    """읽는 자리가 모르는 예외는 맨 바깥 경계가 「판정 못 함」으로 바꾼다 — 종료 3, stderr 와
    `--json` 의 undecided 에 `could not decide: <예외 종류>: <정리된 한 줄>`. 홈은 가리고
    제어문자와 둘째 줄은 뺀다. 모름을 모름으로 적을 뿐 성공으로 적지 않는다."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(f"td-boom\x07 in {env / 'home' / 'x'}\nsecond line")

    monkeypatch.setattr(doctor._Run, "measure", boom)
    line = "could not decide: RuntimeError: td-boom in ~/x"
    code, out, err = run_guarded(capsys, *worker.argv(None, None, "mbp"), "--json")
    assert "Traceback" not in err, err
    assert code == 3, (code, out, err)
    doc = json.loads(out)
    assert doc["undecided"] == [line] and doc["exit"] == 3, doc
    assert doc["role"] == "worker" and doc["problems"] == [], doc
    assert line in err.splitlines(), err
    code, out, err = run_guarded(capsys, *worker.argv(None, None, "mbp"))
    assert code == 3 and "Traceback" not in err, err
    assert line in err.splitlines() and "second line" not in out + err, err
    assert str(env / "home") not in out + err


# ── 싸게 고친 LOW (P3 2라운드) ────────────────────────────────────────────────


def test_git_version_is_read_in_the_service_env(env, monkeypatch):
    """`token_file` 의 git 버전은 서비스 환경으로 잰다 — 셸 환경의 값으로 다른 git 을 보지
    않는다(검증자 1 #4)."""
    d = env / "gitbin"
    d.mkdir()
    script(
        d / "git",
        'if [ "$TD_SVC" = 1 ]; then echo "git version 2.40.0"; else echo "git version 2.20.0"; fi',
    )
    monkeypatch.delenv("TD_SVC", raising=False)
    got = doctor.git_requirement(
        {"PATH": str(d), "TD_SVC": "1"},
        token_file=True,
        platform="linux",
        package_manager=None,
        root=False,
    )
    assert got["state"] == "ok", got


def test_server_given_names_are_cleaned_before_printing(worker, srv, svcbin, capsys):
    """서버가 준 프리셋 · 점검 · 도구 · 프로브 이름의 제어문자는 찍기 전에 지운다(검증자 1 #7)."""
    probe = {"name": "td\x1b[2Jver", "argv": ["rcm-td-version"], "timeout_seconds": 5}
    evil = preset("bu\x1b[2Jild", requires=("rcm-td\x07-lack",), checks=("ch\x1b]0;k",))
    srv.route("GET", "/worker/spec", spec_doc(evil, probes=[probe]))
    _code, out, err = worker.text(capsys)
    for bad in ("\x1b", "\x07"):
        assert bad not in out + err, (out, err)
    assert "build" in out.replace("[2J", ""), out


def test_basic_read_with_a_worker_token_gets_no_host_facts(tmp_path):
    """`read_auth = "basic"` 에서 워커 토큰을 Basic 으로 보내도 호스트 사실은 오지 않는다 — 호스트
    사실은 사람(클라이언트 토큰)의 읽기에만 싣는다(검증자 2 #2)."""
    import base64

    srv = m5n(tmp_path, extra='read_auth = "basic"')
    try:
        srv.reported("build-02", pool="mac2", extra={"host": HOST})
        basic = base64.b64encode(f"build-02:{srv.tokens['build-02']}".encode()).decode()
        status, doc = srv.req("GET", "/api/machines", headers={"Authorization": f"Basic {basic}"})
        if status == 200:
            got = {m["id"]: m for m in doc["machines"]}["build-02"]
            assert "host" not in got, got
    finally:
        srv.close()


def test_the_worker_and_the_server_agree_on_the_host_fact_keys():
    """워커가 싣는 키와 서버가 받는 키는 한 벌이다(검증자 2 #4) — 한쪽만 늘면 그 칸은 조용히
    버려진다."""
    from remote_ci_monitor import capabilities, remote_worker

    assert remote_worker.HOST_KEYS == capabilities.HOST_FACT_KEYS


# ── 생성형: 필수 사실이 없으면 0 이나 1 이 아니다 (P3 3라운드 HIGH · 근본 원인) ────
#
# 원인: doctor 에는 「모름」이라는 값이 없었다. 읽는 자리마다 없거나 null 인 값을 `or default` 로
# 확정값으로 바꿨고, 종료 3 은 자리마다 손으로 넣어야 나왔다. 그래서 답 하나씩이 아니라 표로
# 잠근다 — 역할마다 성한 답을 두고, 그 답의 **모든 칸**을 지우고 · null 로 · 틀린 타입으로 · 빈
# 그릇으로 바꿔 본다. 칸마다 무엇이 맞는지는 아래 표가 정한다(필수면 3, 아니면 성한 답과 같은 종료).
# 새 칸이 생기면 표에 없어서 빨갛다 — 칸마다 확정인지 모름인지 정하게 한다.

B = "baseline"  # 성한 답과 같은 종료 코드
ANY = "any"  # 빈 그릇이 다른 확정 답인 칸 — 종료는 따지지 않고 None · 경계 · traceback 만 본다
#: 없으면 이 답을 알아볼 수 없거나 판정할 수 없다. 이름 · 버전 · 종류 같은 필수 문자열은 비어도
#: 없는 것과 같다(`blank`, P3 4라운드 LOW) — rcm 서버는 빈 문자열을 보내지 않는다
REQUIRED = {"drop": 3, "null": 3, "wrong": 3, "blank": 3}
OPTIONAL = {"drop": B, "null": B, "wrong": 3}  # 없으면 모르는 값으로 null — 판정은 그대로
#: 필수가 아니지만 있으면 이름인 칸(풀) — 빈 문자열은 rcm 서버가 보내지 않는 값이다
OPTIONAL_NAME = {**OPTIONAL, "blank": 3}
FREE = {"drop": B, "null": B, "wrong": B}  # doctor 가 읽지 않는 칸
#: 목록의 항목 — 빼는 것은 다른 확정 답이라 보지 않는다. 빈 객체는 필수 칸이 없는 항목이다
ITEM = {"null": 3, "wrong": 3, "empty": 3, "blank": 3}
ENTRY = {"drop": B, "null": 3, "wrong": 3}  # 이름이 서버가 정한 객체의 값


def walk(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """(경로, 값) — 답의 모든 칸과 목록 항목. 경로는 `presets[0].requires` 꼴이다."""
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            yield p, v
            yield from walk(v, p)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            p = f"{path}[{i}]"
            yield p, v
            yield from walk(v, p)


def _steps(path: str) -> list[str | int]:
    import re

    return [int(i) if i else k for k, i in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path)]


def wrong_value(value: Any) -> Any:
    """그 칸의 타입이 아닌 값 — 문자열 칸에는 목록, 목록 칸에는 문자열."""
    if isinstance(value, bool):
        return "yes"
    if isinstance(value, (int, float)):
        return "soon"
    if isinstance(value, (list, dict)):
        return "x" if isinstance(value, list) else ["x"]
    return [7]


def mutated(doc: Any, path: str, how: str) -> Any:
    import copy

    out = copy.deepcopy(doc)
    *head, last = _steps(path)
    parent = out
    for step in head:
        parent = parent[step]
    if how == "drop":
        del parent[last]
    elif how == "null":
        parent[last] = None
    elif how == "wrong":
        parent[last] = wrong_value(parent[last])
    elif how == "blank":
        parent[last] = ""
    else:  # empty
        parent[last] = type(parent[last])()
    return out


def mutations(value: Any, rule: dict[str, Any], is_item: bool) -> list[tuple[str, Any]]:
    """(어떻게, 기대 종료) — 이미 null 인 칸은 null 로 다시 바꾸지 않는다."""
    out = [(how, rule[how]) for how in ("drop", "null", "wrong") if how in rule]
    if is_item:
        out = [(h, w) for h, w in out if h != "drop"]
    if value is None:
        out = [(h, w) for h, w in out if h != "null"]
    if isinstance(value, (list, dict)) and value:
        out.append(("empty", rule.get("empty", B)))
    if isinstance(value, str) and value:
        out.append(("blank", rule.get("blank", B)))
    return out


GEN_SPEC: dict[str, Any] = {
    "generation": "1:0",
    "probe_interval_seconds": 600,
    "probes": [{"name": "ver", "argv": ["rcm-td-version"], "timeout_seconds": 5}],
    "presets": [
        {
            "name": "build",
            "requires": [HAVE],
            "env_passthrough": ["PATH"],
            "env": {"TD_A": "1"},
            "pool": "mbp",
            "pools": ["mbp2"],
            "placement": "pool",
            "checks": [],
            "source_modes": ["tree", "git_ref"],
            "repo": "app",
            "unreachable_checks": [],
        },
        {
            "name": "wide",
            "requires": [HAVE],
            "env_passthrough": [],
            "env": {},
            "pool": "default",
            "pools": [],
            "placement": "any-capable",
            "checks": ["build"],
            "source_modes": ["tree"],
            "repo": None,
            "unreachable_checks": [{"name": "build", "code": "check_local_only"}],
        },
    ],
    "tools": [LACK_HINT],
}
#: 워커 명세의 칸마다 — 필수 칸, 판정에 쓰는 칸(없으면 모름), 표시만 하는 칸
GEN_SPEC_RULES: dict[str, dict[str, Any]] = {
    "generation": OPTIONAL,
    "probe_interval_seconds": FREE,
    "probes": OPTIONAL,
    "probes[0].name": REQUIRED,
    "probes[0].argv": {**REQUIRED, "empty": 3},
    "probes[0].timeout_seconds": OPTIONAL,
    "presets": {**REQUIRED, "empty": 1},  # 빈 목록은 「서버에 프리셋이 없다」는 확정 답이다
    "presets[0].name": REQUIRED,
    "presets[0].requires": REQUIRED,
    "presets[0].env_passthrough": {**REQUIRED, "empty": ANY},
    "presets[0].env": REQUIRED,
    "presets[0].env.TD_A": ENTRY,
    "presets[0].pool": REQUIRED,  # 이 머신의 풀에 오는지가 이것으로 정해진다
    "presets[0].pools": OPTIONAL,  # `pool` 이 이미 이 머신의 풀이다
    "presets[0].placement": REQUIRED,  # TD5: 옛 서버는 모름이다 — `pool` 로 짐작하지 않는다
    "presets[0].checks": REQUIRED,
    "presets[0].source_modes": {**REQUIRED, "empty": B},
    "presets[0].repo": REQUIRED,  # git_ref 프리셋의 저장소를 모르면 repo_missing 도 모른다
    "presets[0].unreachable_checks": OPTIONAL,  # 점검이 없다
    "presets[1].name": REQUIRED,
    "presets[1].requires": {**REQUIRED, "empty": B},
    "presets[1].env_passthrough": REQUIRED,
    "presets[1].env": REQUIRED,
    "presets[1].pool": OPTIONAL_NAME,  # any-capable 은 풀을 보지 않는다
    "presets[1].pools": OPTIONAL,
    "presets[1].placement": REQUIRED,
    "presets[1].checks": {**REQUIRED, "empty": ANY},
    "presets[1].source_modes": {**OPTIONAL, "empty": B},  # 오지 않는 프리셋이다
    "presets[1].repo": OPTIONAL,  # git_ref 가 아니다
    "presets[1].unreachable_checks": {**REQUIRED, "empty": ANY},  # 점검이 있다 — 없으면 모름
    "presets[1].unreachable_checks[0].name": REQUIRED,
    "presets[1].unreachable_checks[0].code": REQUIRED,
    "tools": OPTIONAL,
    "tools[0].name": REQUIRED,
    "tools[0].docs": OPTIONAL,
    "tools[0].install": OPTIONAL,
    "tools[0].install.darwin": ENTRY,
    "tools[0].install.linux": ENTRY,
}
GEN_HEALTH = {"ok": True, "version": __version__}
GEN_HEALTH_RULES = {"ok": REQUIRED, "version": REQUIRED}
GEN_LANE_HEALTH = {
    **GEN_HEALTH,
    "preset_env": {"unit": {"missing_tools": [], "missing_passthrough": []}},
}
#: coordinator-worker 는 로컬 레인을 `preset_env` 로 판정한다 — 없으면 모름
GEN_LANE_HEALTH_RULES = {
    **GEN_HEALTH_RULES,
    "preset_env": {**REQUIRED, "empty": 3},
    "preset_env.unit": {**REQUIRED, "empty": 3},
    "preset_env.unit.missing_tools": REQUIRED,
    "preset_env.unit.missing_passthrough": REQUIRED,
}
#: coordinator 는 `preset_env` 를 읽지 않는다 — 그래도 있는 칸의 모양은 본다
GEN_COORD_HEALTH_RULES = {
    **GEN_HEALTH_RULES,
    "preset_env": OPTIONAL,
    "preset_env.unit": {**ENTRY, "empty": 3},
    "preset_env.unit.missing_tools": REQUIRED,
    "preset_env.unit.missing_passthrough": REQUIRED,
}
GEN_WORKER_ME = {"name": "mbp", "admin": False, "kind": "worker"}
GEN_CLIENT_ME = {"name": "alice", "admin": False, "kind": "client"}
GEN_ME_RULES = {"name": OPTIONAL, "admin": OPTIONAL, "kind": REQUIRED}
GEN_OK = {"state": "eligible", "code": "ok", "detail": {}}
GEN_MACHINES = {
    "generation": "1:0",
    "machines": [
        {
            "id": "@local",
            "display_name": "build-01",
            "kind": "local",
            "pool": "default",
            "lanes": 1,
            "alive": True,
            "presets": {"unit": dict(GEN_OK)},
        },
        {
            "id": "mbp",
            "display_name": "mbp",
            "kind": "remote",
            "pool": "default",
            "lanes": 1,
            "alive": True,
            "presets": {"unit": dict(GEN_OK)},
        },
    ],
}
GEN_MACHINE_RULES: dict[str, dict[str, Any]] = {"generation": OPTIONAL, "machines": OPTIONAL}
GEN_MACHINE_RULES["machines"] = {**REQUIRED, "empty": B}
for _i in (0, 1):
    GEN_MACHINE_RULES.update(
        {
            f"machines[{_i}].id": REQUIRED,
            f"machines[{_i}].display_name": OPTIONAL,
            f"machines[{_i}].kind": REQUIRED,  # 로컬 레인인지 원격인지가 이것으로 정해진다
            f"machines[{_i}].pool": OPTIONAL_NAME,
            f"machines[{_i}].lanes": FREE,
            f"machines[{_i}].alive": OPTIONAL,
            f"machines[{_i}].presets": OPTIONAL,
            f"machines[{_i}].presets.unit": ENTRY,
            f"machines[{_i}].presets.unit.state": OPTIONAL,
            f"machines[{_i}].presets.unit.code": OPTIONAL,
            f"machines[{_i}].presets.unit.detail": FREE,
        }
    )
#: 역할 → 라우트 → (성한 답, 칸 규칙)
GEN_ROLES: dict[str, dict[str, tuple[Any, dict[str, dict[str, Any]]]]] = {
    "worker": {
        "/api/health": (GEN_HEALTH, GEN_HEALTH_RULES),
        "/api/whoami": (GEN_WORKER_ME, GEN_ME_RULES),
        "/worker/spec": (GEN_SPEC, GEN_SPEC_RULES),
    },
    "client": {
        "/api/health": (GEN_HEALTH, GEN_HEALTH_RULES),
        "/api/whoami": (GEN_CLIENT_ME, GEN_ME_RULES),
    },
    "coordinator": {
        "/api/health": (GEN_LANE_HEALTH, GEN_COORD_HEALTH_RULES),
        "/api/whoami": (GEN_CLIENT_ME, GEN_ME_RULES),
        "/api/machines": (GEN_MACHINES, GEN_MACHINE_RULES),
    },
    "coordinator-worker": {
        "/api/health": (GEN_LANE_HEALTH, GEN_LANE_HEALTH_RULES),
        "/api/whoami": (GEN_CLIENT_ME, GEN_ME_RULES),
        "/api/machines": (GEN_MACHINES, GEN_MACHINE_RULES),
    },
}
WHOLE = "(the whole answer)"
GEN_CASES = [
    pytest.param(role, route, path, id=f"{role}{route.replace('/', '-')}:{path}")
    for role, routes in GEN_ROLES.items()
    for route, (doc, _rules) in routes.items()
    for path in [WHOLE, *(p for p, _v in walk(doc))]
]


def gen_server(role: str) -> FakeRcm:
    s = FakeRcm()
    for route, (doc, _rules) in GEN_ROLES[role].items():
        s.route("GET", route, doc)
    return s


def gen_argv(role: str, env: Path, s: FakeRcm, svcbin: Path, monkeypatch) -> list[str]:
    if role == "worker":
        monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
        script(svcbin / "git", "echo 'git version 2.45.0'")
        monkeypatch.setattr(cli, "_ls_remote_problem", lambda url, timeout=10.0, **_kw: None)
        cfg = worker_toml(env, s.url, extra=REPO)
        sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
        return ["--role", role, "--config", str(cfg), "--service-file", str(sf)]
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    if role == "client":
        return ["--role", role, "--server", s.url]
    cfg = env / "server.toml"
    cfg.write_text(
        f'[server]\nbind = "127.0.0.1"\nport = {s.port}\ndata_dir = "{env / "data"}"\n\n'
        f'[[presets]]\nname = "unit"\nargv = ["true"]\nrequires = ["{HAVE}"]\n'
        'env_passthrough = ["PATH"]\n'
    )
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin", command="serve")
    return ["--role", role, "--config", str(cfg), "--service-file", str(sf)]


@pytest.mark.parametrize("role,route,path", GEN_CASES)
def test_a_required_fact_the_server_did_not_give_is_never_exit_0_or_1(
    env, svcbin, monkeypatch, capsys, role, route, path
):
    """역할 · 라우트 · 칸마다 성한 답을 망가뜨린다. 필수 칸이 없거나 null · 틀린 타입이면 종료 3,
    답 전체가 `{}` 나 남의 서비스의 `{"status":"ok"}` 여도 3 이다. 필수가 아닌 칸을 지우거나 null 로
    두면 성한 답과 같은 종료다. 어느 경우에도 출력에 `None` 이 없고 경계 줄 · traceback 이 없다."""
    doc, rules = GEN_ROLES[role][route]
    s = gen_server(role)
    try:
        argv = [*gen_argv(role, env, s, svcbin, monkeypatch), "--json"]
        base_code, out, err = run_guarded(capsys, *argv)
        assert base_code == 0 and "Traceback" not in err, (base_code, out, err)
        if path == WHOLE:
            cases = [("{}", {}, 3), ('{"status":"ok"}', {"status": "ok"}, 3)]
        else:
            assert path in rules or path.endswith("]"), f"{route} {path}: not in the rule table"
            rule = rules.get(path, ITEM)
            value = dict(walk(doc))[path]
            cases = [
                (how, mutated(doc, path, how), want)
                for how, want in mutations(value, rule, path.endswith("]"))
            ]
        wrong: list[Any] = []
        for how, body, want in cases:
            s.route("GET", route, body)
            code, out, err = run_guarded(capsys, *argv)
            lines = said_lines(out, err) if code is not None and out.strip() else [err]
            expected = base_code if want == B else want
            bad = []
            if "Traceback" in err or code is None:
                bad.append("traceback")
            elif want != ANY and code != expected:
                bad.append(f"exit {code}, want {expected}")
            if "None" in out + err:
                bad.append("'None' in the output")
            if any(BOUNDARY in ln for ln in lines):
                bad.append("reached the outer boundary")
            if bad:
                wrong.append((how, bad, json.loads(out)["undecided"] if out.strip() else err))
        assert not wrong, wrong
    finally:
        s.close()


# ── 생성형: 못 읽은 서비스 파일 뒤에 PATH 를 짐작하지 않는다 (P3 3라운드 HIGH) ───

FILE_STATES = ("readable", "unreadable", "absent")
SERVICE_ORDER_CASES = [
    pytest.param(installed, staged, tool, id=f"installed-{installed}-staged-{staged}-{tool}")
    for installed in FILE_STATES
    for staged in FILE_STATES
    for tool in ("present", "missing")
]


@pytest.mark.parametrize("installed,staged,tool", SERVICE_ORDER_CASES)
def test_an_unreadable_service_file_leaves_the_path_unknown_in_every_order(
    env, srv, svcbin, monkeypatch, capsys, installed, staged, tool
):
    """`--service-file` 없이 설치 자리와 setup 자리의 파일을 {읽힘, 못 읽음, 없음} 으로, 필요한
    도구를 {있음, 없음} 으로 엇갈린다. 읽은 파일을 고르지 못했는데 못 읽은 후보가 있었으면 서비스
    PATH 는 null 이고 도구를 재지 않으며 종료 3 이다. 기본 PATH 를 짐작해도 되는 것은 후보가 하나도
    없을 때뿐이다. 설치 자리의 파일을 못 읽었으면 setup 자리의 사본으로 넘어가지 않는다 — 서비스
    관리자가 읽는 것은 설치 자리의 파일이다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(preset("build", requires=(HAVE if tool == "present" else LACK,))),
    )
    cfg = worker_toml(env, srv.url)
    home = env / "home"
    places = {
        "installed": (home / "Library" / "LaunchAgents", "com.remote-ci-monitor.worker.plist"),
        "staged": (home / ".config" / "rcm" / "service", "worker.plist"),
    }
    for where, state in (("installed", installed), ("staged", staged)):
        folder, name = places[where]
        if state == "readable":
            plist(folder, f"{svcbin}:/usr/bin:/bin", name=name)
        elif state == "unreadable":
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(TRUNCATED_PLIST)
    code, out, err = run_guarded(capsys, "--role", "worker", "--config", str(cfg), "--json")
    assert code is not None and "Traceback" not in err, err
    doc = json.loads(out)
    chosen = (
        "installed"
        if installed == "readable"
        else "staged"
        if installed == "absent" and staged == "readable"
        else None
    )
    seen_unreadable = installed == "unreadable" or (
        installed == "absent" and staged == "unreadable"
    )
    if chosen is None and seen_unreadable:
        assert doc["service"]["path"] is None, doc["service"]
        assert doc["presets"] == [], doc["presets"]
        assert code == 3, doc
        assert any("cannot read the service file" in u for u in doc["undecided"]), doc
    elif chosen is not None:
        folder, name = places[chosen]
        assert doc["service"]["file"] == "~/" + str((folder / name).relative_to(home)), doc
        assert doc["service"]["path"] == "~/svcbin:/usr/bin:/bin", doc["service"]
        assert code == (1 if tool == "missing" else 3 if seen_unreadable else 0), doc
    else:
        assert doc["service"]["file"] is None and doc["service"]["path"], doc["service"]


# ── 같은 원인의 다른 자리 (P3 3라운드 근본 원인 분석 (a)~(g) · TD5) ───────────


def test_td5_an_older_server_with_a_missing_tool_exits_3_not_1(worker, srv, capsys):
    """TD5(메인 세션 결정): `placement` 가 없는 옛 서버의 프리셋은 모름이다. `pool` 로 짐작해
    빠진 도구를 문제(종료 1)로 만들지 않는다 — 종료 3, 행은 「오는지 모름」이다."""
    old = {"name": "build", "requires": [LACK], "env_passthrough": ["PATH"], "env": {}}
    srv.route("GET", "/worker/spec", {"generation": "1:0", "probes": [], "presets": [old]})
    code, doc = worker(capsys)
    got = row(doc, "build")
    assert (got["applies"], got["state"]) == ("unknown", "unknown"), got
    assert doc["problems"] == [], doc["problems"]
    assert any("build" in u and "older rcm" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc


def test_f_a_placement_rcm_does_not_know_is_not_read_as_pool(worker, srv, capsys):
    """(f) 모르는 `placement` 값을 `pool` 로 읽지 않는다 — 빠진 도구가 종료 1 이 되지 않는다."""
    srv.route("GET", "/worker/spec", spec_doc(preset("build", requires=(LACK,), placement="near")))
    code, doc = worker(capsys)
    assert row(doc, "build")["state"] == "unknown" and doc["problems"] == [], doc
    assert any("near" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc


def test_d_presets_without_a_name_are_not_the_server_has_no_presets(worker, srv, capsys):
    """(d) 이름 없는 프리셋을 조용히 버리면 「서버에 프리셋이 없다」(종료 1)가 된다 — 그 답은
    rcm 서버의 답이 아니다(종료 3)."""
    nameless = {k: v for k, v in preset("build").items() if k != "name"}
    srv.route("GET", "/worker/spec", spec_doc(nameless))
    code, doc = worker(capsys)
    assert not any("no presets" in p for p in doc["problems"]), doc["problems"]
    assert any("presets[0].name is missing" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc


def test_g_a_git_ref_preset_without_a_repo_is_unknown_not_skipped(worker, srv, capsys):
    """(g) git_ref 프리셋인데 서버가 `repo` 를 말하지 않으면 `repo_missing` 검사를 조용히
    건너뛰지 않는다 — 모름, 종료 3."""
    srv.route("GET", "/worker/spec", spec_doc({**preset("build"), "source_modes": ["git_ref"]}))
    code, doc = worker(capsys)
    assert any("build" in u and "repository" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc


def test_e_a_preset_env_entry_without_measurements_is_not_ready(
    env, coordinator, monkeypatch, capsys
):
    """(e) `preset_env` 의 항목이 빈 객체면 서버가 무엇을 쟀는지 모른다 — ready 가 아니다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health = {"ok": True, "version": __version__, "preset_env": {"unit": {}, "lint": {}}}
    coordinator.route("GET", "/api/health", health)
    cfg = server_toml(env, coordinator.port)
    code, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert not any(p["state"] == "ready" for p in doc["presets"]), doc["presets"]
    assert code == 3, doc


def test_abc_unknown_server_values_are_null_never_none_or_down(
    env, coordinator, monkeypatch, capsys
):
    """(a) `version` 없는 health 는 「server vNone」이 아니다. (b) `generation` · `pool` 이 없으면
    null 이다. (c) `alive` 가 없는 머신은 down 이 아니라 모름이다. 사람용 표에도 `None` 이 없다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    machines = machines_doc()
    del machines["generation"]
    for m in machines["machines"]:
        del m["pool"], m["alive"]
    coordinator.route("GET", "/api/machines", machines)
    cfg = server_toml(env, coordinator.port)
    code, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    mbp = doc["remote_machines"][0]
    assert (mbp["pool"], mbp["alive"]) == (None, None), mbp
    assert doc["server"]["generation"] is None, doc["server"]
    code, out, err = run(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert "None" not in out + err, out
    line = next(ln for ln in out.splitlines() if "mbp" in ln and "eligible" in ln)
    assert "down" not in line and "unknown" in line, line
    coordinator.route("GET", "/api/health", {"ok": True})
    code, out, err = run(capsys, "--role", "client", "--server", coordinator.url)
    assert "None" not in out + err and code == 3, (code, out, err)
    assert "version is missing" in out, out


# ── token_file 저장소는 워커와 같은 자격으로 잰다 (P3 3라운드 MED) ──────────────


def token_repo(env: Path, token_file: Path) -> str:
    return (
        '[[repos]]\nname = "app"\nurl = "https://example.invalid/app.git"\n'
        f'token_file = "{token_file}"\n'
    )


def credential_git(svcbin: Path, token_file: Path) -> None:
    """서비스 PATH 의 가짜 git — 워커가 쓰는 자격(그 파일을 읽는 헬퍼 하나)을 받았을 때만
    `ls-remote` 가 닿는다. 네트워크는 쓰지 않는다."""
    script(
        svcbin / "git",
        'if [ "$1" = "--version" ]; then echo "git version 2.45.0"; exit 0; fi\n'
        f'case "$GIT_CONFIG_VALUE_1" in *"{token_file}"*) exit 0;; esac\n'
        'echo "fatal: could not read Username for https://example.invalid" >&2; exit 128',
    )


def test_a_token_file_repo_is_checked_with_the_workers_credential(
    env, srv, svcbin, monkeypatch, capsys
):
    """`token_file` 이 있는 저장소는 워커가 git 을 부를 때처럼(`gitops.git_env` 의 자격 헬퍼)
    잰다 — URL 만 넘겨 전역 자격으로 재면 워커는 닿는 저장소를 「닿지 않음」(종료 1)으로
    단정한다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    token = env / "home" / "app.token"
    token.write_text("td-not-a-real-token\n")
    credential_git(svcbin, token)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    code, doc = run_worker(capsys, env, srv, sf, toml_extra=token_repo(env, token))
    assert req(doc, "repos")["state"] == "ok", doc["rcm_requirements"]
    assert code == 0, doc
    assert "td-not-a-real-token" not in json.dumps(doc)


def test_a_token_file_doctor_cannot_read_is_unknown_not_unreachable(
    env, srv, svcbin, monkeypatch, capsys
):
    """doctor 가 그 토큰 파일을 못 읽으면(서비스는 다른 사용자일 수 있다) 저장소에 닿는지 모른다 —
    「닿지 않음」(종료 1)이 아니라 판정 못 함(종료 3)이다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    token = env / "home" / "missing.token"
    credential_git(svcbin, token)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    code, doc = run_worker(capsys, env, srv, sf, toml_extra=token_repo(env, token))
    repos = req(doc, "repos")
    assert repos["state"] == "unknown" and "token_file" in repos["detail"], repos
    assert any("repos" in u and "token_file" in u for u in doc["undecided"]), doc["undecided"]
    assert str(env / "home") not in json.dumps(doc)
    assert code == 3, doc


def test_a_git_version_rcm_cannot_read_is_unknown_not_a_fail(env):
    """`token_file` 에는 git 2.31+ 가 필요하다. 버전을 읽지 못한 것은 「낮다」가 아니라 모름이다."""
    d = env / "gitbin"
    d.mkdir()
    script(d / "git", "echo 'not a version'")
    got = doctor.git_requirement(
        {"PATH": str(d)}, token_file=True, platform="linux", package_manager=None, root=False
    )
    assert got["state"] == "unknown", got


def test_repos_with_an_unknown_service_path_are_unknown_not_unreachable(
    env, srv, monkeypatch, capsys
):
    """서비스 PATH 를 모르면(PATH 없는 systemd 유닛) git 을 찾을 수 없다 — 저장소는 「닿지
    않음」(종료 1)이 아니라 재지 못함이다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    (env / "svc").mkdir()
    sf = env / "svc" / "rcm-worker.service"
    sf.write_text("[Service]\nExecStart=/x/rcm worker\n")
    code, doc = run_worker(capsys, env, srv, sf, toml_extra=REPO)
    assert req(doc, "repos")["state"] == "unknown", doc["rcm_requirements"]
    assert doc["problems"] == [] and code == 3, doc


# ── 싸게 고친 LOW (P3 3라운드) ────────────────────────────────────────────────


def test_a_probe_value_with_the_home_folder_is_masked(worker, srv, svcbin, monkeypatch, capsys):
    """프로브 값에 이 머신의 홈 경로가 섞일 수 있다(SDK 경로를 찍는 프로브) — `~` 로 가린다.
    프로브 값은 길이가 잘리므로 짧은 HOME 으로 본다(값만 찍을 뿐 그 폴더를 쓰지 않는다)."""
    script(svcbin / "rcm-td-where", 'echo "$HOME/sdk"')
    monkeypatch.setenv("HOME", "/rcm-td-home")
    probe = {"name": "where", "argv": ["rcm-td-where"], "timeout_seconds": 5}
    srv.route("GET", "/worker/spec", spec_doc(preset("build"), probes=[probe]))
    code, doc = worker(capsys)
    assert doc["probes"]["where"]["value"] == "~/sdk", doc["probes"]
    assert code == 0, doc


@pytest.mark.parametrize("timeout", [float("inf"), 0, 600, 2.5], ids=["inf", "0", "600", "2.5"])
def test_a_probe_timeout_rcm_would_never_send_is_an_unreadable_answer(
    env, srv, svcbin, monkeypatch, capsys, timeout
):
    """서버의 프로브 시한은 1~60 의 정수다. JSON 의 `Infinity` 나 범위 밖 값은 rcm 서버의 답이
    아니다 — 맨 바깥 경계(`OverflowError`)가 아니라 읽는 자리가 그 칸을 말하고 종료 3 이다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    probe = {"name": "p", "argv": ["rcm-td-version"], "timeout_seconds": timeout}
    srv.route("GET", "/worker/spec", spec_doc(preset("build"), probes=[probe]))
    cfg = worker_toml(env, srv.url)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    argv = ["--role", "worker", "--config", str(cfg), "--service-file", str(sf), "--json"]
    code, out, err = run_guarded(capsys, *argv)
    assert_handled(code, out, err, "probes[0].timeout_seconds", codes=(3,))


@pytest.mark.parametrize("route", ["/api/health", "/api/whoami"])
def test_worker_check_with_an_answer_that_is_not_an_object_does_not_crash(
    env, srv, svcbin, monkeypatch, capsys, route
):
    """`rcm worker --check` 도 health · whoami 가 객체가 아니면(틀린 포트) traceback 이 아니라
    FAIL 줄이다."""
    import traceback

    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.route("GET", route, [])
    try:
        code, lines = worker_check(capsys, env, srv)
    except Exception:  # noqa: BLE001 — traceback 을 단언으로 보인다
        raise AssertionError(traceback.format_exc()) from None
    row_ = check_line(lines, "server" if route == "/api/health" else "token")
    assert row_ is not None and row_.startswith("FAIL") and "not rcm's" in row_, lines
    assert code == 1, lines


# ── 열거: 읽지 못한 입력이 하나라도 있으면 0 · 1 이 아니다 (P3 4라운드 HIGH · 메타 루프) ──
#
# 같은 부류(읽지 못한 입력을 기본값으로 메워 사실처럼 판정한다)가 네 라운드 이어졌다. 입력 하나씩이
# 아니라 역할마다 입력의 있음 · 없음 · 못 읽음을 모두 엮어 잠근다. 이 셋업에는 확정된 문제(빠진
# 도구 · 낮은 Python)가 없다 — 그래서 모든 입력을 읽었으면 0, 하나라도 못 읽었으면 2 나 3 이다.

#: 설정 파일: --config 로 줌 · 표준 자리 · 없음 · 못 읽음(--config) · 못 읽음(표준 자리) · 서비스
#: 파일의 `--config` 가 가리킴 · 서비스 파일의 `--config` 가 가리키는 파일을 못 읽음
INPUT_CONFIGS = (
    "flag",
    "standard",
    "absent",
    "unreadable-flag",
    "unreadable-standard",
    "service",
    "service-unreadable",
)
INPUT_SERVICES = ("readable", "absent", "unreadable")
INPUT_ROLES = ("worker", "coordinator", "coordinator-worker", "client")
NOT_TOML = b"\xff\xfe not toml\n"


def input_cases() -> Iterator[Any]:
    for role in INPUT_ROLES:
        # 클라이언트는 서비스 파일을 읽지 않는다
        configs = INPUT_CONFIGS if role != "client" else INPUT_CONFIGS[:5]
        services = INPUT_SERVICES if role != "client" else ("absent",)
        for config in configs:
            for service in services:
                if config.startswith("service") and service != "readable":
                    continue
                for server in ("answers", "silent"):
                    for token in ("token", "no-token"):
                        yield pytest.param(
                            role,
                            config,
                            service,
                            server,
                            token,
                            id=f"{role}-config_{config}-service_{service}-{server}-{token}",
                        )


def unread_inputs(role: str, config: str, service: str, server: str, token: str) -> list[str]:
    """doctor 가 그 실행에서 읽지 못한 입력. 워커 · 클라이언트는 설정 파일 없이도 된다(환경 변수와
    `--server`, TD49). coordinator 계열은 설정 파일이 없으면 무엇을 잴지(프리셋 · 포트) 모른다."""
    out = []
    if config in ("unreadable-flag", "unreadable-standard", "service-unreadable"):
        out.append("config")
    if config == "absent" and role in ("coordinator", "coordinator-worker"):
        out.append("config")
    if service == "unreadable":
        out.append("service file")
    if server == "silent":
        out.append("server")
    if token == "no-token":
        out.append("token")
    return out


def input_setup(
    env: Path, svcbin: Path, monkeypatch, role: str, config: str, service: str, port: int
) -> list[str]:
    """그 칸의 입력을 깔고 `rcm doctor` 의 인자를 돌려준다."""
    home = env / "home"
    url = f"http://127.0.0.1:{port}"
    std = home / ".config" / "rcm"
    std.mkdir(parents=True, exist_ok=True)
    kind = {"worker": "worker", "client": "client"}.get(role, "server")
    if kind == "worker":
        body = f'server = "{url}"\npool = "default"\n'
    elif kind == "client":
        body = f'server = "{url}"\n'
    else:
        body = (
            f'[server]\nbind = "127.0.0.1"\nport = {port}\ndata_dir = "{env / "data"}"\n\n'
            '[[presets]]\nname = "unit"\nargv = ["true"]\nrequires = ["sh"]\n'
            'env_passthrough = ["PATH"]\n'
        )
    argv = ["--role", role]
    elsewhere = env / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    named = elsewhere / f"{kind}.toml"  # 서비스 파일의 `--config` 가 가리키는 파일
    if config == "flag":
        p = env / f"{kind}.toml"
        p.write_text(body)
        p.chmod(0o600)
        argv += ["--config", str(p)]
    elif config == "standard":
        (std / f"{kind}.toml").write_text(body)
        (std / f"{kind}.toml").chmod(0o600)
    elif config == "unreadable-flag":
        p = env / f"{kind}.toml"
        p.write_bytes(NOT_TOML)
        argv += ["--config", str(p)]
    elif config == "unreadable-standard":
        (std / f"{kind}.toml").write_bytes(NOT_TOML)
    elif config == "service":
        named.write_text(body)
        named.chmod(0o600)
    elif config == "service-unreadable":
        named.write_bytes(NOT_TOML)
    if config == "absent" and kind in ("worker", "client"):
        argv += ["--server", url]  # 설정 파일 없는 워커 · 클라이언트(TD49)
    if kind != "client":
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        label = "worker" if kind == "worker" else "server"
        sf = agents / f"com.remote-ci-monitor.{label}.plist"
        command = "worker" if kind == "worker" else "serve"
        args = ["/x/rcm", command]
        if config.startswith("service"):
            args += ["--config", str(named)]
        if service == "readable":
            sf.write_bytes(
                plistlib.dumps(
                    {
                        "Label": sf.stem,
                        "ProgramArguments": args,
                        "EnvironmentVariables": {"PATH": f"{svcbin}:/usr/bin:/bin"},
                    }
                )
            )
        elif service == "unreadable":
            sf.write_bytes(TRUNCATED_PLIST)
    if kind == "server":
        # 설정 파일이 없을 때 서버 설정의 기본 포트(8787)는 이 머신의 운영 서버일 수 있다 — 시험의
        # 서버로 돌린다. 설정 파일이 있으면 같은 값이라 아무것도 바꾸지 않는다
        monkeypatch.setenv("RCM_SERVER_PORT", str(port))
    return argv


def input_server() -> FakeRcm:
    s = FakeRcm()
    unit = {"missing_tools": [], "missing_passthrough": []}
    s.route(
        "GET", "/api/health", {"ok": True, "version": __version__, "preset_env": {"unit": unit}}
    )
    s.route("GET", "/api/machines", GEN_MACHINES)
    s.route("GET", "/worker/spec", spec_doc(preset("build", requires=("sh",), pool="default")))
    return s


@pytest.mark.parametrize("role,config,service,server,token", list(input_cases()))
def test_every_input_doctor_did_not_read_ends_in_2_or_3(
    env, svcbin, monkeypatch, capsys, role, config, service, server, token
):
    """역할 넷 × 설정 파일(있음 · 없음 · 못 읽음, --config 줌 · 안 줌, 서비스 파일이 가리킴) ×
    서비스 파일(있음 · 없음 · 못 읽음) × 서버(답함 · 안 답함) × 토큰(있음 · 없음). doctor 가 그
    실행에서 읽지 못한 입력이 하나라도 있으면 종료는 2 나 3 이다 — 0 이나 1 의 근거가 될 수 없다.
    모두 읽었으면 0 이다(이 셋업에는 확정된 문제가 없다)."""
    monkeypatch.setattr(doctor, "PLATFORM", "darwin")
    monkeypatch.setattr(cli, "discover", FakeDiscover(()))
    s = input_server()
    try:
        port = s.port if server == "answers" else free_port()
        kind = "worker" if role == "worker" else "client"
        s.route("GET", "/api/whoami", {"name": "td", "admin": False, "kind": kind})
        if token == "token":
            monkeypatch.setenv("RCM_WORKER_TOKEN" if role == "worker" else "RCM_TOKEN", TOKEN)
        argv = input_setup(env, svcbin, monkeypatch, role, config, service, port)
        code, out, err = run_guarded(capsys, *argv, "--json")
        assert code is not None and "Traceback" not in err, err
        doc = json.loads(out) if out.strip() else None
        unread = unread_inputs(role, config, service, server, token)
        if unread:
            assert code in (2, 3), (unread, code, doc and doc["problems"], err)
        else:
            assert doc is not None and code == 0, (code, doc, err)
            assert doc["problems"] == [] and doc["undecided"] == [], doc
    finally:
        s.close()


# ── 설정은 서비스가 읽는 것으로 — 기본값을 사실로 읽지 않는다 (P3 4라운드 HIGH) ──────


@pytest.mark.parametrize("role", ["coordinator", "coordinator-worker"])
def test_a_coordinator_role_without_a_server_config_is_a_usage_error(
    env, coordinator, monkeypatch, capsys, role
):
    """`--config` 도 표준 자리의 server.toml 도 없으면 doctor 는 설정을 읽지 않았다. 기본값(프리셋
    없음 · 8787)으로 그 포트의 아무 서버에 묻고 0 이나 「프리셋 없음」(1)로 끝내지 않는다 — 사용법
    오류(2)이고 서버에는 아무것도 묻지 않는다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("RCM_SERVER_PORT", str(coordinator.port))  # 8787 로 가지 않게
    code, out, err = run_guarded(capsys, "--role", role, "--json")
    assert code == 2, (code, out, err)
    assert "no server config" in err and "--config" in err, err
    assert coordinator.requests == [], coordinator.requests


def launch_agent(env: Path, args: list[str], path: str, extra: dict[str, str] | None = None):
    agents = env / "home" / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    name = "worker" if args[1] == "worker" else "server"
    p = agents / f"com.remote-ci-monitor.{name}.plist"
    doc = {"Label": p.stem, "ProgramArguments": args, "EnvironmentVariables": {"PATH": path}}
    doc["EnvironmentVariables"].update(extra or {})
    p.write_bytes(plistlib.dumps(doc))
    return p


def test_the_service_files_config_argument_is_the_config_doctor_reads(
    env, srv, svcbin, monkeypatch, capsys
):
    """서비스가 `rcm worker --config X` 로 뜨면 그 서비스는 X 를 읽는다. `--config` 없는 doctor 도
    X 로 잰다 — 기본값(서버 없음 · 풀 default)으로 재지 않는다. 인자는 rcm 자신의 파서로 읽으므로
    `--config=X` 꼴도 같다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "discover", FakeDiscover(()))
    (env / "conf").mkdir()
    x = env / "conf" / "w.toml"
    x.write_text(f'server = "{srv.url}"\npool = "mbp"\n')
    x.chmod(0o600)
    launch_agent(env, ["/x/rcm", "worker", f"--config={x}"], f"{svcbin}:/usr/bin:/bin")
    code, doc = run_json(capsys, "--role", "worker")
    assert srv.calls("GET", "/worker/spec"), doc["undecided"]
    assert row(doc, "build")["applies"] == "pool", doc["presets"]
    assert code == 0, doc


@pytest.mark.parametrize("state", ["missing", "not-toml"])
def test_a_config_the_service_names_and_doctor_cannot_read_is_unknown(
    env, srv, svcbin, monkeypatch, capsys, state
):
    """서비스 파일이 가리키는 설정을 doctor 가 못 읽으면 그 서비스가 무엇으로 도는지 모른다 —
    사람이 준 `--config` 가 아니므로 사용법 오류(2)가 아니라 판정 못 함(3)이다. 기본값으로 재지
    않고 서버에도 묻지 않는다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "discover", FakeDiscover(()))
    x = env / "home" / "conf" / "w.toml"
    if state == "not-toml":
        x.parent.mkdir(parents=True)
        x.write_bytes(NOT_TOML)
    launch_agent(env, ["/x/rcm", "worker", "--config", str(x)], f"{svcbin}:/usr/bin:/bin")
    code, out, err = run_guarded(capsys, "--role", "worker", "--json")
    assert_handled(code, out, err, "~/conf/w.toml", codes=(3,))
    assert srv.requests == [], srv.requests
    assert str(env / "home") not in out + err


def test_the_servers_service_arguments_and_environment_choose_the_port(
    env, coordinator, svcbin, monkeypatch, capsys
):
    """`rcm serve --port P` 나 서비스 환경의 `RCM_SERVER_PORT` 로 뜬 서버는 설정 파일의 포트가
    아니라 P 에서 듣는다. doctor 도 P 에 묻는다 — 설정 파일의 포트로 남의 서버에 묻지 않는다."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    # 서비스 파일을 읽지 않는 doctor 가 기본 포트(8787, 이 머신의 운영 서버일 수 있다)로 가지 않게
    monkeypatch.setenv("RCM_SERVER_PORT", str(free_port()))
    cfg = server_toml(env, free_port())  # 서비스는 이 포트로 뜨지 않는다
    path = f"{svcbin}:/usr/bin:/bin"
    launch_agent(
        env, ["/x/rcm", "serve", "--config", str(cfg), "--port", str(coordinator.port)], path
    )
    code, doc = run_json(capsys, "--role", "coordinator-worker")
    assert doc["server"]["url"] == f"http://127.0.0.1:{coordinator.port}", doc["server"]
    assert coordinator.calls("GET", "/api/machines"), doc["undecided"]
    launch_agent(
        env,
        ["/x/rcm", "serve", "--config", str(cfg)],
        path,
        extra={"RCM_SERVER_PORT": str(coordinator.port)},
    )
    code, doc = run_json(capsys, "--role", "coordinator-worker")
    assert doc["server"]["url"] == f"http://127.0.0.1:{coordinator.port}", doc["server"]


# ── 재지 못한 것은 「닿지 않음」이 아니다 · 홈 · worker --check (P3 4라운드 MED) ────


@pytest.mark.parametrize("how", ["timeout", "oserror"])
def test_a_git_ls_remote_that_times_out_or_cannot_run_is_unknown_not_unreachable(
    env, srv, svcbin, monkeypatch, capsys, how
):
    """`git ls-remote` 가 시한을 넘기거나 실행되지 못한 것은 저장소에 닿지 않는다는 사실이 아니다 —
    프로브 시한 초과처럼 모름(종료 3)이다. 저장소 행은 FAIL 이 아니다."""
    import subprocess

    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    script(svcbin / "git", "echo 'git version 2.45.0'")
    real = subprocess.run

    def fake(argv, *a, **kw):
        if len(argv) > 1 and argv[1] == "ls-remote":
            if how == "timeout":
                raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 10)
            raise PermissionError(13, "Permission denied")
        return real(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", fake)
    sf = plist(env / "svc", f"{svcbin}:/usr/bin:/bin")
    code, doc = run_worker(capsys, env, srv, sf, toml_extra=REPO)
    repos = req(doc, "repos")
    assert repos["state"] == "unknown", doc["rcm_requirements"]
    assert doc["problems"] == [] and any("repos" in u for u in doc["undecided"]), doc
    assert code == 3, doc


def test_without_a_home_folder_doctor_says_so_and_exits_3(env, monkeypatch, capsys):
    """HOME 이 없고 이 uid 에 passwd 항목도 없으면 홈 폴더를 모른다. 그러면 설정과 서비스 파일의
    표준 자리를 찾을 수 없다 — traceback 이 아니라 그 이유를 말하고 종료 3 이다."""

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv("HOME")
    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    code, out, err = run_guarded(capsys, "--role", "worker", "--json")
    assert_handled(code, out, err, "HOME", codes=(3,))


@pytest.mark.parametrize(
    "body",
    [
        [],
        "spec",
        HTML_200,
        {"presets": [], "probes": [{"name": "p", "argv": ["true"], "timeout_seconds": "soon"}]},
    ],
    ids=["list", "scalar", "html", "timeout-not-a-number"],
)
def test_worker_check_with_a_spec_rcm_cannot_read_is_a_fail_line(
    env, srv, svcbin, monkeypatch, capsys, body
):
    """`rcm worker --check` 도 `/worker/spec` 이 rcm 의 명세가 아니면(목록 · 문자열 · HTML · 숫자가
    아닌 시한) traceback 이 아니라 health · whoami 와 같은 FAIL 줄이다."""
    import traceback

    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.route("GET", "/worker/spec", body)
    try:
        code, lines = worker_check(capsys, env, srv)
    except Exception:  # noqa: BLE001 — traceback 을 단언으로 보인다
        raise AssertionError(traceback.format_exc()) from None
    row_ = check_line(lines, "presets")
    assert row_ is not None and row_.startswith("FAIL") and "not rcm's" in row_, lines
    assert code == 1, lines


def test_worker_check_never_prints_none_for_what_the_server_did_not_say(
    env, srv, svcbin, monkeypatch, capsys
):
    """서버가 `version` 이나 토큰 `name` 을 말하지 않으면 `vNone` · `None (worker)` 가 아니다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.route("GET", "/api/health", {"ok": True})
    srv.route("GET", "/api/whoami", {"admin": False, "kind": "worker"})
    _code, lines = worker_check(capsys, env, srv)
    assert not any("None" in ln for ln in lines), lines


# ── 서비스 파일 찾기의 systemd 절반 (P3 4라운드 시험 대조 WEAK · TD45) ───────────


def test_td45_the_systemd_user_folder_is_the_installed_place_too(
    env, srv, svcbin, monkeypatch, capsys
):
    """§4.5: 표준 라벨의 설치 자리는 launchd 의 `~/Library/LaunchAgents` 와 systemd 의
    `~/.config/systemd/user/rcm-*.service` 다. systemd 쪽에서도 설치 자리가 setup 자리보다 먼저고,
    다른 역할의 유닛(`rcm-server.service`)은 후보가 아니며, 고른 파일의 PATH 를 읽는다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(doctor, "PLATFORM", "linux")
    cfg = worker_toml(env, srv.url)
    home = env / "home"
    staged = home / ".config" / "rcm" / "service"
    staged.mkdir(parents=True)
    (staged / "worker.service").write_text(
        "[Service]\nExecStart=/x/rcm worker\nEnvironment=PATH=/usr/bin:/bin\n"
    )
    code, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] == "~/.config/rcm/service/worker.service", doc["service"]
    user = home / ".config" / "systemd" / "user"
    user.mkdir(parents=True)
    (user / "rcm-server.service").write_text(
        "[Service]\nExecStart=/x/rcm serve\nEnvironment=PATH=/bin\n"
    )
    (user / "rcm-worker.service").write_text(
        f"[Service]\nExecStart=/x/rcm worker\nEnvironment=PATH={svcbin}:/usr/bin:/bin\n"
    )
    code, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] == "~/.config/systemd/user/rcm-worker.service", doc["service"]
    assert doc["service"]["manager"] == "systemd", doc["service"]
    assert doc["service"]["path"] == "~/svcbin:/usr/bin:/bin", doc["service"]
    assert row(doc, "build")["state"] == "ready" and code == 0, doc


# ── 싼 LOW (P3 4라운드) ───────────────────────────────────────────────────────


def test_a_readable_file_next_to_an_unreadable_one_is_not_chosen(
    env, srv, svcbin, monkeypatch, capsys
):
    """같은 자리에 읽히는 파일과 못 읽는 파일이 함께 있으면, 못 읽은 파일이 이 역할의 서비스였는지
    모른다. 읽히는 쪽으로 재서 종료 1 을 내지 않는다 — 그 자리 전체가 모름이다(종료 3)."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    srv.route("GET", "/worker/spec", spec_doc(preset("build", requires=(LACK,))))
    cfg = worker_toml(env, srv.url)
    agents = env / "home" / "Library" / "LaunchAgents"
    plist(agents, f"{svcbin}:/usr/bin:/bin")
    (agents / "com.remote-ci-monitor.other.plist").write_bytes(TRUNCATED_PLIST)
    code, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["path"] is None and doc["presets"] == [], doc
    assert doc["problems"] == [] and code == 3, doc


@pytest.mark.parametrize(
    "args",
    [
        ["/usr/bin/env", "rcm", "worker"],
        ["/bin/sh", "-c", "exec rcm worker"],
        ["/x/rcm", "worker", "--no-such-flag"],
    ],
    ids=["env", "sh-c", "rcm-refuses-the-arguments"],
)
def test_a_service_file_whose_rcm_command_rcm_cannot_tell_is_named(
    env, srv, svcbin, monkeypatch, capsys, args
):
    """표준 자리에 있는데 띄우는 명령을 rcm 의 하위 명령으로 풀지 못한 파일(`env rcm worker` ·
    `sh -c` · rcm 이 받지 않을 인자)은 조용히 빼지 않는다 — 이 역할의 서비스일 수 있으므로 못 읽은
    파일로 이름을 보이고 종료 3 이다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    cfg = worker_toml(env, srv.url)
    launch_agent(env, args, f"{svcbin}:/usr/bin:/bin")
    code, out, err = run_guarded(capsys, "--role", "worker", "--config", str(cfg), "--json")
    assert_handled(code, out, err, "com.remote-ci-monitor.", codes=(3,))
    doc = json.loads(out)
    assert doc["service"]["path"] is None, doc["service"]


@pytest.mark.parametrize(
    "doc_",
    [
        {"ProgramArguments": ["/x/rcm", "worker"], "EnvironmentVariables": {"PATH": 7}},
        {"ProgramArguments": ["/x/rcm", "worker"], "EnvironmentVariables": {"PATH": True}},
        {"ProgramArguments": ["/x/rcm", 3], "EnvironmentVariables": {"PATH": "/usr/bin"}},
    ],
    ids=["env-integer", "env-boolean", "argument-integer"],
)
def test_a_plist_value_that_is_not_a_string_is_unreadable(env, srv, monkeypatch, capsys, doc_):
    """launchd 는 환경 변수 값과 인자를 문자열로만 받는다. 정수 · 참거짓을 `str()` 로 바꿔 「PATH 가
    7」로 재지 않는다 — 그 파일을 못 읽은 것으로 둔다(종료 3)."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    (env / "svc").mkdir()
    sf = env / "svc" / "com.remote-ci-monitor.worker.plist"
    sf.write_bytes(plistlib.dumps({"Label": "x", **doc_}))
    cfg = worker_toml(env, srv.url)
    argv = ["--role", "worker", "--config", str(cfg), "--service-file", str(sf), "--json"]
    code, out, err = run_guarded(capsys, *argv)
    assert_handled(code, out, err, "is not a string", codes=(3,))


def test_the_summary_counts_presets_that_may_come_apart(worker, srv, capsys):
    """요약은 오는 프리셋만 센다. 오는지 모르는 프리셋(`applies: unknown`)을 빼면 「0 unknown」인데
    종료 3 이다 — 따로 센다."""
    old = {"name": "build", "requires": [LACK], "env_passthrough": ["PATH"], "env": {}}
    srv.route("GET", "/worker/spec", {"generation": "1:0", "probes": [], "presets": [old]})
    code, doc = worker(capsys)
    assert doc["summary"]["applies_unknown"] == 1, doc["summary"]
    _code, out, _err = worker.text(capsys)
    assert "1 may come (unknown)" in out, out


def test_a_pool_preset_with_unknown_checks_is_unknown_even_with_a_missing_tool(worker, srv, capsys):
    """`checks` 를 모르는 `pool` 프리셋은 빠진 도구가 있어도 종료 1 이 아니다. 그 점검이 이 머신에
    닿지 않는 것(TO18)이면 이 프리셋은 오지 않고, 그러면 빠진 도구는 문제가 아니다 — 1 의 근거가
    확정되지 않았다(P3 4라운드 검증자 1 #10 을 받지 않은 이유)."""
    p = preset("build", requires=(LACK,))
    p["checks"] = None
    srv.route("GET", "/worker/spec", spec_doc(p))
    code, doc = worker(capsys)
    assert row(doc, "build")["state"] == "unknown" and doc["problems"] == [], doc
    assert code == 3, doc


# ── 검증 사이클 2 의 1라운드: 확정을 모름으로 · 셸이 서비스를 대신하지 않는다 ─────────────
#
# 앞 라운드들이 「모름을 확정으로」를 닫았다. 이번 지적 둘은 그 반대(rcm 의 것이 아닌 파일을
# 「못 읽음」으로, 아는 홈 `%h` 를 모름으로)와, 서비스 대신 doctor 셸의 `RCM_CONFIG` · 작업 폴더를
# 읽는 누수다. 고치는 김에 「rcm 이 실제로 아는 것은 모름이 아니다」를 `rcm init worker` 와
# `examples/` 의 서비스 파일 그대로로 잠근다.

CI_WATCH = (
    Path(cli.__file__).parent
    / "skills"
    / "rcm-ci-connect"
    / "templates"
    / "com.remote-ci-monitor.ci-watch.plist"
)
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def ci_watch_plist(env: Path) -> Path:
    """`rcm-ci-connect` 스킬이 빌드 머신의 `~/Library/LaunchAgents` 에 까는 감시기 — 템플릿 그대로
    (`{{home}}` 만 시험의 홈으로)."""
    agents = env / "home" / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    p = agents / CI_WATCH.name
    p.write_bytes(CI_WATCH.read_bytes().replace(b"{{home}}", str(env / "home").encode()))
    return p


def run_input(env, svcbin, monkeypatch, capsys, role, config, service, after=None, extra=()):
    """열거 시험의 셋업(모두 읽히는 칸)을 깔고 `after(env)` 로 하나를 더한 뒤 doctor 를 부른다."""
    monkeypatch.setattr(doctor, "PLATFORM", "darwin")
    monkeypatch.setattr(cli, "discover", FakeDiscover(()))
    s = input_server()
    try:
        kind = "worker" if role == "worker" else "client"
        s.route("GET", "/api/whoami", {"name": "td", "admin": False, "kind": kind})
        monkeypatch.setenv("RCM_WORKER_TOKEN" if role == "worker" else "RCM_TOKEN", TOKEN)
        argv = input_setup(env, svcbin, monkeypatch, role, config, service, s.port)
        if after is not None:
            after(env)
        code, out, err = run_guarded(capsys, *argv, *extra, "--json")
        return code, out, err, s
    finally:
        s.close()


@pytest.mark.parametrize("role", ["worker", "coordinator", "coordinator-worker"])
@pytest.mark.parametrize("service", ["readable", "absent"])
def test_the_ci_watch_plist_next_to_the_service_is_not_a_candidate(
    env, svcbin, monkeypatch, capsys, role, service
):
    """`rcm-ci-connect` 의 `com.remote-ci-monitor.ci-watch.plist` 는 `python3 rcm_ci_watch.py` 를
    띄운다 — rcm 의 서비스 파일이 아님이 확실하다. 같은 자리에 있어도 후보도 「못 읽음」도 아니다:
    모든 입력을 읽은 칸은 그대로 종료 0 이다(검증 사이클 2 의 1라운드 HIGH 1)."""
    code, out, err, _s = run_input(
        env, svcbin, monkeypatch, capsys, role, "flag", service, after=ci_watch_plist
    )
    assert "Traceback" not in err, err
    doc = json.loads(out)
    assert doc["undecided"] == [] and doc["problems"] == [], doc
    assert code == 0, doc
    shown = doc["service"]["file"] or ""
    assert "ci-watch" not in shown, doc["service"]


#: (파일 이름, 내용, 후보인가) — rcm 의 것이 아님이 확실한 파일은 빠지고(None), rcm 처럼 보이는데
#: 풀지 못한 파일만 「못 읽음」이다. 실행 파일이 rcm 도 `python -m remote_ci_monitor` 도 아니고,
#: 인자에 rcm 이 없고, 라벨(파일 이름)도 worker · server 가 아니면 rcm 의 것이 아니다
FOREIGN_CASES = {
    "ci-watch-template": (CI_WATCH.name, None, "skip"),
    "launchd-shell-script": (
        "com.remote-ci-monitor.backup.plist",
        ["/bin/sh", "/x/backup.sh"],
        "skip",
    ),
    "systemd-python-script": (
        "rcm-ci-watch.service",
        "[Service]\nExecStart=/usr/bin/python3 /x/watch.py --once\n",
        "skip",
    ),
    "launchd-worker-label-python": (
        "com.remote-ci-monitor.worker.plist",
        ["/usr/bin/python3", "/x/w.py"],
        "unreadable",
    ),
    "launchd-env-rcm": (
        "com.remote-ci-monitor.other.plist",
        ["/usr/bin/env", "rcm", "worker"],
        "unreadable",
    ),
    "launchd-sh-c-rcm": (
        "com.remote-ci-monitor.other.plist",
        ["/bin/sh", "-c", "exec /x/rcm worker"],
        "unreadable",
    ),
    "launchd-python-m": (
        "com.remote-ci-monitor.other.plist",
        ["/usr/bin/python3", "-m", "remote_ci_monitor", "worker"],
        "unreadable",
    ),
    "systemd-worker-label-python": (
        "rcm-worker.service",
        "[Service]\nExecStart=/usr/bin/python3 /x/w.py\n",
        "unreadable",
    ),
}


@pytest.mark.parametrize("case", list(FOREIGN_CASES))
def test_only_files_that_look_like_rcm_are_named_unreadable(env, case):
    """표준 자리의 파일 가운데 rcm 의 서비스가 아님이 확실한 것은 후보에서 빠진다. rcm 처럼
    보이는데(실행 파일 · 인자 · 라벨) 명령을 풀지 못한 것만 「못 읽음」이다."""
    name, body, want = FOREIGN_CASES[case]
    home = env / "home"
    if name.endswith(".plist"):
        if body is None:
            ci_watch_plist(env)
        else:
            launch_agent_named(env, name, body)
    else:
        user = home / ".config" / "systemd" / "user"
        user.mkdir(parents=True)
        (user / name).write_text(body)
    skipped: list[Any] = []
    got = doctor.pick_service_file(
        None, "worker", home=home, config_dir=home / ".config" / "rcm", unreadable=skipped
    )
    assert got is None
    if want == "skip":
        assert skipped == [], [(e.path.name, e.reason) for e in skipped]
    else:
        assert [e.path.name for e in skipped] == [name], skipped


def launch_agent_named(env: Path, name: str, args: list[str], **keys: Any) -> Path:
    agents = env / "home" / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    p = agents / name
    p.write_bytes(plistlib.dumps({"Label": p.stem, "ProgramArguments": args, **keys}))
    return p


SERVER_BODY = (
    '[server]\nbind = "127.0.0.1"\nport = {port}\ndata_dir = "{data}"\n\n'
    '[[presets]]\nname = "{preset}"\nargv = ["true"]\nrequires = ["sh"]\n'
    'env_passthrough = ["PATH"]\n'
)


def server_body(env: Path, port: int, preset: str = "unit") -> str:
    return SERVER_BODY.format(port=port, data=env / "data", preset=preset)


@pytest.mark.parametrize("leak", ["shell-RCM_CONFIG", "cwd-rcm.toml"])
@pytest.mark.parametrize("service", ["launchd-workdir-home", "launchd-no-workdir", "none"])
def test_the_doctor_shells_rcm_config_and_folder_never_choose_the_servers_config(
    env, svcbin, monkeypatch, capsys, leak, service
):
    """서비스는 doctor 셸의 `RCM_CONFIG` 도 doctor 가 선 폴더의 `rcm.toml` 도 보지 않는다. 서비스
    파일이 `--config` 없이 `rcm serve` 를 띄우면 doctor 는 서비스가 읽을 것(서비스 환경의
    `RCM_CONFIG`, 서비스 작업 폴더의 `rcm.toml`, 표준 자리)을 스스로 정한다 — 서비스 파일이 없을
    때도 셸의 것은 쓰지 않는다(검증 사이클 2 의 1라운드 HIGH 2)."""
    home = env / "home"

    def leaks(_env: Path) -> None:
        other = env / "other.toml"  # 운영 포트가 아닌 빈 포트 — 빨강에서도 아무 서버에 닿지 않는다
        other.write_text(server_body(env, free_port(), preset="leaked"))
        other.chmod(0o600)
        if leak == "shell-RCM_CONFIG":
            monkeypatch.setenv("RCM_CONFIG", str(other))
        else:
            (env / "rcm.toml").write_text(other.read_text())  # doctor 의 작업 폴더(= env)
        if service != "none":
            keys = {"EnvironmentVariables": {"PATH": f"{svcbin}:/usr/bin:/bin"}}
            if service == "launchd-workdir-home":
                keys["WorkingDirectory"] = str(home)
            launch_agent_named(
                env, "com.remote-ci-monitor.server.plist", ["/x/rcm", "serve"], **keys
            )

    code, out, err, s = run_input(
        env, svcbin, monkeypatch, capsys, "coordinator-worker", "standard", "absent", after=leaks
    )
    assert "Traceback" not in err, err
    doc = json.loads(out)
    assert doc["server"]["url"] == f"http://127.0.0.1:{s.port}", (doc["server"], err)
    assert code == 0, doc


def test_the_services_working_directory_rcm_toml_is_the_servers_config(
    env, svcbin, monkeypatch, capsys
):
    """서비스가 `WorkingDirectory` 에서 `rcm serve` 로 뜨면 그 폴더의 `rcm.toml` 이 표준 자리보다
    먼저다(`rcm serve` 의 탐색 순서). doctor 는 그 폴더를 보고 재며 — doctor 가 선 폴더가 아니다."""
    wd = env / "svcwd"

    def service(_env: Path) -> None:
        std = env / "home" / ".config" / "rcm" / "server.toml"
        good = std.read_text()
        std.write_text(server_body(env, free_port(), preset="standard"))  # 서비스가 읽지 않는다
        wd.mkdir()
        (wd / "rcm.toml").write_text(good)
        launch_agent_named(
            env,
            "com.remote-ci-monitor.server.plist",
            ["/x/rcm", "serve"],
            WorkingDirectory=str(wd),
            EnvironmentVariables={"PATH": f"{svcbin}:/usr/bin:/bin"},
        )

    code, out, err, s = run_input(
        env, svcbin, monkeypatch, capsys, "coordinator-worker", "standard", "absent", after=service
    )
    doc = json.loads(out)
    assert doc["server"]["url"] == f"http://127.0.0.1:{s.port}", (doc["server"], err)
    assert code == 0, doc


@pytest.mark.parametrize("home_rcm_toml", [True, False])
def test_a_units_unknown_working_directory_is_unknown_only_when_rcm_toml_may_be_there(
    env, svcbin, monkeypatch, capsys, home_rcm_toml
):
    """사용자 유닛인지 모르는 systemd 유닛(다른 자리에서 준 파일)이 `WorkingDirectory=` 없이
    `--config` 없는 `rcm serve` 를 띄우면 시스템 유닛이면 `/`, 사용자 유닛이면 홈에서 뜬다. 홈에
    `rcm.toml` 이 있으면 서버가 그것을 읽는지 모른다 — 종료 3. 없으면 모를 것이 없다: 표준
    자리로 재고 0 이다(과잉 교정 잠금)."""
    sf = env / "svc" / "rcm-server.service"

    def unit(_env: Path) -> None:
        sf.parent.mkdir()
        sf.write_text(
            f"[Service]\nExecStart=/x/rcm serve\nEnvironment=PATH={svcbin}:/usr/bin:/bin\n"
        )
        if home_rcm_toml:
            (env / "home" / "rcm.toml").write_text(server_body(env, free_port()))

    code, out, err, s = run_input(
        env,
        svcbin,
        monkeypatch,
        capsys,
        "coordinator-worker",
        "standard",
        "absent",
        after=unit,
        extra=("--service-file", str(sf)),
    )
    if home_rcm_toml:
        assert_handled(code, out, err, "may be the server's config", codes=(3,))
        assert s.requests == [], s.requests
    else:
        doc = json.loads(out)
        assert doc["undecided"] == [] and code == 0, doc


def test_the_doctor_shells_config_home_does_not_choose_the_workers_config(
    env, svcbin, monkeypatch, capsys
):
    """워커 서비스가 `--config` 없이 `rcm worker` 로 뜨면 그 서비스의 설정 폴더(서비스 환경의
    `XDG_CONFIG_HOME`, 없으면 서비스 홈의 `.config`)의 `worker.toml` 을 읽는다. doctor 셸의
    `XDG_CONFIG_HOME` 이 아니다."""

    def leak(_env: Path) -> None:
        xdg = env / "xdg" / "rcm"
        xdg.mkdir(parents=True)
        (xdg / "worker.toml").write_text(
            f'server = "http://127.0.0.1:{free_port()}"\npool = "default"\n'
        )
        (xdg / "worker.toml").chmod(0o600)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(env / "xdg"))
        launch_agent_named(
            env,
            "com.remote-ci-monitor.worker.plist",
            ["/x/rcm", "worker"],
            EnvironmentVariables={"PATH": f"{svcbin}:/usr/bin:/bin"},
        )

    code, out, err, s = run_input(
        env, svcbin, monkeypatch, capsys, "worker", "standard", "absent", after=leak
    )
    doc = json.loads(out)
    assert doc["server"]["url"] == s.url, (doc["server"], err)
    assert code == 0, doc


@pytest.mark.parametrize("with_config", [False, True])
@pytest.mark.parametrize(
    "args",
    [["/usr/bin/env", "rcm", "worker"], ["/bin/sh", "-c", "exec /x/rcm worker"]],
    ids=["env", "sh-c"],
)
def test_an_explicit_service_file_whose_command_rcm_cannot_resolve_is_unknown(
    env, srv, svcbin, monkeypatch, capsys, args, with_config
):
    """`--service-file` 로 준 파일도 rcm 이 그 명령을 풀지 못하면 그 실행이 읽는 설정과 PATH 를
    모른다(`env PATH=…` · `sh -c` 가 바꿀 수 있다). 표준 자리의 설정과 그 파일의 PATH 로 재고 0 을
    내지 않는다 — 종료 3(검증 사이클 2 의 1라운드 MED 3)."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    std = env / "home" / ".config" / "rcm"
    std.mkdir(parents=True)
    (std / "worker.toml").write_text(f'server = "{srv.url}"\npool = "mbp"\n')
    (std / "worker.toml").chmod(0o600)
    (env / "svc").mkdir()
    sf = env / "svc" / "w.plist"
    sf.write_bytes(
        plistlib.dumps(
            {"ProgramArguments": args, "EnvironmentVariables": {"PATH": f"{svcbin}:/usr/bin"}}
        )
    )
    argv = ["--role", "worker", "--service-file", str(sf), "--json"]
    if with_config:
        argv += ["--config", str(std / "worker.toml")]
    code, out, err = run_guarded(capsys, *argv)
    assert_handled(code, out, err, "w.plist", codes=(3,))
    doc = json.loads(out)
    assert doc["service"]["path"] is None, doc["service"]
    assert doc["problems"] == [], doc


def systemd_user_unit(env: Path, body: str) -> Path:
    user = env / "home" / ".config" / "systemd" / "user"
    user.mkdir(parents=True, exist_ok=True)
    p = user / "rcm-worker.service"
    p.write_text(body)
    return p


def test_h_in_a_user_unit_is_the_home_doctor_knows(env, srv, svcbin, monkeypatch, capsys):
    """사용자 유닛(`~/.config/systemd/user/`)의 `%h` 는 그 사용자의 홈이다. doctor 가 아는 홈으로
    풀어 `ExecStart=%h/.local/bin/rcm worker --config %h/.config/rcm/worker.toml` 과
    `Environment=PATH=%h/…` 를 읽는다 — 「상대 --config」나 「풀지 않는 지정자」로 모르지 않는다
    (검증 사이클 2 의 1라운드 MED 4)."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(doctor, "PLATFORM", "linux")
    conf = env / "home" / ".config" / "rcm"
    conf.mkdir(parents=True)
    (conf / "worker.toml").write_text(f'server = "{srv.url}"\npool = "mbp"\n')
    (conf / "worker.toml").chmod(0o600)
    systemd_user_unit(
        env,
        "[Service]\nWorkingDirectory=%h\n"
        "ExecStart=%h/.local/bin/rcm worker --config %h/.config/rcm/worker.toml\n"
        "Environment=PATH=%h/svcbin:/usr/bin:/bin\n",
    )
    code, doc = run_json(capsys, "--role", "worker")
    assert doc["undecided"] == [], doc["undecided"]
    assert doc["service"]["path"] == "~/svcbin:/usr/bin:/bin", doc["service"]
    assert srv.calls("GET", "/worker/spec") and code == 0, doc


@pytest.mark.parametrize(
    "where,line",
    [
        ("user", "ExecStart=%S/rcm worker --config /x/c.toml"),
        ("explicit", "ExecStart=%h/.local/bin/rcm worker --config %h/.config/rcm/worker.toml"),
    ],
    ids=["other-specifier", "h-outside-the-user-folder"],
)
def test_a_specifier_rcm_does_not_resolve_in_exec_start_is_unknown(
    env, srv, svcbin, monkeypatch, capsys, where, line
):
    """`%h` 말고 다른 지정자, 그리고 사용자 유닛인지 모르는 파일(사람이 준 파일)의 `%h` 는 지금처럼
    모름이다 — 종료 3 이고 그 줄을 이름으로 보인다. 「상대 --config」라고 잘못 말하지 않는다."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(doctor, "PLATFORM", "linux")
    body = f"[Service]\n{line}\nEnvironment=PATH={svcbin}:/usr/bin:/bin\n"
    if where == "user":
        systemd_user_unit(env, body)
        argv = ["--role", "worker", "--json"]
    else:
        (env / "svc").mkdir()
        sf = env / "svc" / "rcm-worker.service"
        sf.write_text(body)
        argv = ["--role", "worker", "--service-file", str(sf), "--json"]
    code, out, err = run_guarded(capsys, *argv)
    assert_handled(code, out, err, "ExecStart=", codes=(3,))
    assert "relative --config" not in out + err, out + err
    assert srv.requests == [], srv.requests


def init_worker(env: Path, url: str) -> Path:
    """`rcm init worker` 로 설정을 만들고 문서대로 토큰 줄을 넣는다(모드 600 그대로)."""
    assert cli.main(["init", "worker", "--server", url, "--pool", "default"]) == 0
    cfg = env / "home" / ".config" / "rcm" / "worker.toml"
    cfg.write_text(f'token = "{TOKEN}"\n' + cfg.read_text())  # 표(`[host]`) 앞, 맨 위
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    return cfg


@pytest.mark.parametrize(
    "shape", ["example-launchd", "example-systemd", "init-launchd", "init-systemd"]
)
def test_what_rcm_init_and_the_examples_write_is_known_and_exits_0(env, monkeypatch, capsys, shape):
    """과잉 교정 잠금: `rcm init worker` 가 쓴 설정과 문서의 서비스 파일(`examples/` 그대로, 또는
    `rcm init worker --service-file` 이 쓰는 모양)은 rcm 이 다 안다. 도구가 다 있으면 종료 0 이다 —
    서비스 파일 · 설정 · PATH 가운데 무엇도 모름이 되지 않는다."""
    monkeypatch.setattr(cli, "discover", FakeDiscover(()))
    s = input_server()
    try:
        s.route("GET", "/api/whoami", {"name": "td", "admin": False, "kind": "worker"})
        cfg = init_worker(env, s.url)
        capsys.readouterr()
        home = env / "home"
        rcm = home / ".local" / "bin" / "rcm"
        if shape.endswith("launchd"):
            monkeypatch.setattr(doctor, "PLATFORM", "darwin")
            dest = home / "Library" / "LaunchAgents" / "com.remote-ci-monitor.worker.plist"
            if shape.startswith("example"):
                src = EXAMPLES / "launchd" / dest.name
                data = src.read_bytes().replace(b"/Users/me", str(home).encode())
            else:
                data = cli.worker_plist(rcm, cfg, home)
        else:
            monkeypatch.setattr(doctor, "PLATFORM", "linux")
            dest = home / ".config" / "systemd" / "user" / "rcm-worker.service"
            if shape.startswith("example"):
                src = EXAMPLES / "systemd" / dest.name
                data = src.read_bytes().replace(b"/home/me", str(home).encode())
            else:
                data = cli.worker_unit(rcm, cfg, home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        code, doc = run_json(capsys, "--role", "worker")
        assert doc["service"]["file"] is not None and doc["service"]["path"], doc["service"]
        assert doc["undecided"] == [] and doc["problems"] == [], doc
        assert s.calls("GET", "/worker/spec") and code == 0, doc
    finally:
        s.close()


@pytest.mark.parametrize("shape", ["example-launchd", "example-systemd"])
def test_the_server_examples_are_known_and_exit_0(env, svcbin, monkeypatch, capsys, shape):
    """서버 쪽 과잉 교정 잠금: `examples/` 의 서버 서비스 파일 그대로(절대 `--config`,
    `WorkingDirectory`)는 rcm 이 다 안다 — 도구가 다 있으면 종료 0 이다."""
    home = env / "home"

    def service(_env: Path) -> None:
        conf = home / ".config" / "rcm"
        if shape == "example-launchd":
            src = EXAMPLES / "launchd" / "com.remote-ci-monitor.server.plist"
            dest = home / "Library" / "LaunchAgents" / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes().replace(b"/Users/rcm", str(home).encode()))
        else:
            monkeypatch.setattr(doctor, "PLATFORM", "linux")
            src = EXAMPLES / "systemd" / "rcm-server.service"
            dest = home / ".config" / "systemd" / "user" / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes().replace(b"/home/rcm", str(home).encode()))
        assert (conf / "server.toml").is_file()

    code, out, err, s = run_input(
        env, svcbin, monkeypatch, capsys, "coordinator-worker", "standard", "absent", after=service
    )
    doc = json.loads(out)
    assert doc["service"]["file"] is not None, doc["service"]
    assert doc["undecided"] == [] and doc["problems"] == [], doc
    assert doc["server"]["url"] == f"http://127.0.0.1:{s.port}" and code == 0, doc


def test_a_relative_config_in_the_service_is_not_compared_from_doctors_folder(
    env, srv, svcbin, monkeypatch, capsys
):
    """서비스의 `--config rel/w.toml` 은 서비스의 작업 폴더에 달렸다. doctor 가 선 폴더로 풀어
    「같은 파일」이라고 조용히 넘어가지 않는다 — 비교할 수 없다고 알린다(검증 사이클 2 의 1라운드
    LOW)."""
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    (env / "rel").mkdir()
    mine = env / "rel" / "w.toml"  # doctor 의 작업 폴더(= env) 기준으로는 같은 파일이다
    mine.write_text(f'server = "{srv.url}"\npool = "mbp"\n')
    mine.chmod(0o600)
    launch_agent(env, ["/x/rcm", "worker", "--config", "rel/w.toml"], f"{svcbin}:/usr/bin:/bin")
    code, doc = run_json(capsys, "--role", "worker", "--config", str(mine))
    assert any("relative --config" in w for w in doc["warnings"]), doc["warnings"]
    assert code == 0, doc


def test_host_facts_lose_line_separators_and_bidi_controls(tmp_path):
    """U+2028 · U+2029(줄 · 문단 구분자)와 bidi 제어(U+202A–202E, U+2066–2069)도 지운다 — 터미널과
    웹에서 줄을 나누거나 글자 순서를 뒤집는다. 규칙은 `core/progress.clean_line` 하나다."""
    srv = m5n(tmp_path)
    try:
        raw = "ubuntu  24.04 ‪‮⁦⁩"
        register_host(srv, "build-02", {**HOST, "os_release": raw, "os_like": "‮debian"})
        host = machine(srv, "build-02")["host"]
        assert host["os_release"] == "ubuntu 24.04", host
        assert host["os_like"] == "debian", host
    finally:
        srv.close()


# ── 시험 대조 보강(싸면): 텍스트 출력도 JSON 과 같은 것을 말한다 ──────────────────────


def test_td13_the_text_report_shows_the_real_install_command(worker, srv, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "PLATFORM", "darwin")
    srv.route(
        "GET",
        "/worker/spec",
        spec_doc(preset("build"), preset("lint", requires=(LACK,)), tools=[LACK_HINT]),
    )
    code, out, _err = worker.text(capsys)
    assert code == 1 and "brew install td-lack" in out, out
    assert "https://example.invalid/td-lack" in out, out


def test_td55_the_text_report_shows_no_service_env_value(worker, capsys):
    code, out, err = worker.text(capsys, svc_env={"TD_SECRET": "td55-hidden-value"})
    assert "td55-hidden-value" not in out + err, out + err


def test_td35_a_refused_token_says_a_token_is_needed(worker, srv, capsys):
    srv.route("GET", "/api/whoami", {"error": "unauthorized"}, status=401)
    code, out, err = worker.text(capsys)
    assert code == 3 and "needed" in (out + err), out + err


# ── P6 gap-fix 1 ─────────────────────────────────────────────────────────────


def doctor_child(env: Path, **extra: str) -> dict[str, str]:
    """자식 프로세스로 띄우는 `rcm doctor` 의 환경 — 임시 HOME, 셸의 rcm 변수 없음. 이 시험이 읽은
    `remote_ci_monitor` 를 그대로 쓴다(`PYTHONPATH`) — 변이를 넣은 사본에서도 같은 코드가 돈다."""
    return {
        "PYTHONPATH": str(Path(doctor.__file__).resolve().parents[1]),
        "HOME": str(env / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(env),
        **extra,
    }


def doctor_argv(*argv: str) -> list[str]:
    return [sys.executable, "-m", "remote_ci_monitor.cli", "doctor", *argv]


def test_e3_a_closed_pipe_keeps_the_verdict_and_prints_no_traceback(env):
    """`rcm doctor | true` — 읽는 쪽이 닫힌 파이프에 써도 traceback 이 없고 종료 코드는 판정
    그대로다(P6 GAP E3). 파이썬이 끝날 때의 flush 오류(`Exception ignored … BrokenPipeError`,
    종료 120)도 없다."""
    url = f"http://127.0.0.1:{free_port()}"
    proc = subprocess.Popen(
        doctor_argv("--role", "client", "--server", url),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        env=doctor_child(env),
        cwd=env,
    )
    assert proc.stdout is not None
    proc.stdout.close()  # `| true` — 읽는 쪽이 먼저 끝났다
    proc.stdout = None
    _out, err = proc.communicate(timeout=60)
    text = err.decode("utf-8", "replace")
    assert "Traceback" not in text and "BrokenPipe" not in text, text
    assert proc.returncode == 3, (proc.returncode, text)


@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_e3_a_stdout_that_is_not_utf8_keeps_the_verdict(env, as_json):
    """UTF-8 이 아닌 stdout(`PYTHONIOENCODING=ascii`)에서도 판정을 잃지 않는다(P6 GAP E3).
    글은 못 찍는 글자만 바꾸고, JSON 은 `\\uXXXX` 로 적어 그대로 읽힌다."""
    url = f"http://127.0.0.1:{free_port()}"
    argv = doctor_argv("--role", "client", "--server", url, *(["--json"] if as_json else []))
    utf8 = subprocess.run(
        argv,
        capture_output=True,
        env=doctor_child(env, PYTHONIOENCODING="utf-8"),
        cwd=env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    plain = subprocess.run(
        argv,
        capture_output=True,
        env=doctor_child(env, PYTHONIOENCODING="ascii"),
        cwd=env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    err = plain.stderr.decode("utf-8", "replace")
    assert utf8.returncode == 3 and not utf8.stdout.isascii(), utf8  # 바꿀 글자가 있는 출력이다
    assert "Traceback" not in err, err
    assert plain.returncode == 3, (plain.returncode, err)
    assert plain.stdout.isascii(), plain.stdout
    if as_json:
        assert json.loads(plain.stdout) == json.loads(utf8.stdout)
    else:
        assert "cannot reach" in plain.stdout.decode() and "exit 3" in plain.stdout.decode()
