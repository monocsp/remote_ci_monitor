"""`rcm doctor` 명령 전체와 `rcm worker --check` 의 두 줄(M5t-a 결정 246 · 247, §4.5 · §7 TD).

서버는 `fake_rcm_http.FakeRcm`(127.0.0.1 임시 포트)이다. `/worker/spec` 의 새 키(`pool` · `pools` ·
`placement` · `checks` · `tools`)는 서버 쪽 구현(2라운드)보다 먼저 손으로 쓴 문서로 못 박는다 —
doctor 가 그 문서를 어떻게 읽는지만 본다. 서비스 파일 · 도구 · 설정은 모두 `tmp_path` 아래이고
HOME 도 임시 폴더다. 운영 설정(`~/.config/rcm`)과 운영 데이터는 입력으로 쓰지 않는다.
"""

from __future__ import annotations

import json
import plistlib
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, free_port
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
    assert doc["summary"] == {"ready": 1, "needs_check": 0, "missing": 0, "unknown": 0}
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


# ── 서버에 아무것도 보내지 않는다 · 비밀 · 서버가 보낸 문구 ───────────────────


def test_td12_doctor_sends_nothing_to_the_server(worker, srv, capsys):
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
    srv.route("GET", "/worker/spec", spec_doc(preset("ios", checks=("sim-check",))))
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


# ── 원격 git · Local Network · Python (TD17 · TD50 · TD14) ─────────────────────


def test_td17_offline_skips_git_ls_remote_but_still_asks_the_coordinator(
    env, srv, svcbin, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_WORKER_TOKEN", TOKEN)
    calls: list[str] = []
    monkeypatch.setattr(cli, "_ls_remote_problem", lambda url, timeout=10.0: calls.append(url))
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
    code, doc = run_json(capsys, "--role", "coordinator", "--config", str(cfg))
    assert code == 3 and doc["role"] == "coordinator", doc


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
