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
    읽히는 파일로는 그대로 잰다(그 파일이 이 역할의 것이었는지 모르므로 0 으로 끝내지 않는다)."""
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
    good = plist(agents, f"{svcbin}:/usr/bin:/bin")
    code, doc = run_json(capsys, "--role", "worker", "--config", str(cfg))
    assert doc["service"]["file"] == "~/Library/LaunchAgents/" + good.name, doc["service"]
    assert row(doc, "build")["state"] == "ready", doc
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
    "measured", [None, {"unit": {"missing_tools": []}}], ids=["no-key", "no-entry"]
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
