"""`rcm setup` 의 몸통 — `remote_ci_monitor.setup.run` 과 순수 `core/setup.py` 를 바로 부른다
(M5t-a 결정 239~242, docs/m5t-workplan.md §4.1 · §7 TR).

운영 보호: 모든 시험이 임시 HOME · XDG_CONFIG_HOME · XDG_DATA_HOME 과 시험 라벨을 쓴다. 바깥 세계
(TTY · 인터페이스 주소 · 포트 · 서비스 관리자 조회 · 호스트 사실 · rcm 실행 파일)는 가짜 `World`
로 바꿔 끼운다 — 진짜 `launchctl` · `systemctl` 은 부르지 않는다.
"""

from __future__ import annotations

import io
import os
import plistlib
import socket
import sqlite3
import subprocess
import tomllib
from pathlib import Path

import pytest

from remote_ci_monitor import setup
from remote_ci_monitor.core import setup as core
from remote_ci_monitor.store import DB_VERSION

RCM = Path("/opt/rcm-test/venv/bin/rcm")
LAN = "192.168.77.20"
TAILNET = "100.101.102.103"
SERVER_LABEL = "com.remote-ci-monitor.server"
WORKER_LABEL = "com.remote-ci-monitor.worker"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """임시 HOME · XDG 와 셸의 `RCM_*` 없음. 작업 폴더도 임시 폴더다(`./rcm.toml` 을 줍지 않게)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    for name in (
        "RCM_CONFIG",
        "RCM_SERVER",
        "RCM_TOKEN",
        "RCM_WORKER_TOKEN",
        "RCM_SERVER_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


@pytest.fixture
def cfg(home, tmp_path):
    return tmp_path / "xdg-config" / "rcm"


class Fake:
    """가짜 바깥 세계. `calls` 에 서비스 관리자 조회를 모은다."""

    def __init__(self, **kw):
        self.out, self.err = io.StringIO(), io.StringIO()
        self.calls: list[tuple[str, str]] = []
        self.facts_called = 0
        local = set(kw.pop("local", (LAN, "127.0.0.1", "0.0.0.0")))
        loaded = kw.pop("loaded", False)
        rcm = kw.pop("rcm", (RCM, False))
        answers = kw.pop("answers", None)

        def _loaded(manager, label):
            self.calls.append((manager, label))
            return loaded

        def _facts():
            self.facts_called += 1
            return {"os": "darwin", "arch": "arm64", "package_manager": "brew", "root": False}

        fields = {
            "platform": "darwin",
            "stdin": io.StringIO("" if answers is None else "\n".join(answers) + "\n"),
            "stdout": self.out,
            "stderr": self.err,
            "interactive": lambda: answers is not None,
            "addresses": lambda: [LAN],
            "is_local_address": lambda a: a in local,
            "port_in_use": lambda host, port: False,
            "service_loaded": _loaded,
            "host_facts": _facts,
            "find_rcm": lambda: rcm,
        }
        fields.update(kw)
        self.world = setup.World(**fields)

    @property
    def text(self) -> str:
        return self.out.getvalue() + self.err.getvalue()


def go(fake: Fake, *argv: str) -> int:
    return setup.run(setup.build_parser().parse_args(list(argv)), fake.world)


def coordinator_args(data: Path, *extra: str, role: str = "coordinator-worker") -> list[str]:
    return [
        "--role",
        role,
        "--bind",
        "127.0.0.1",
        "--port",
        "18790",
        "--data-dir",
        str(data),
        "--label",
        "test.rcm.server",
        "--no-advertise",
        "--yes",
        *extra,
    ]


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def make_db(path: Path, user_version: int) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x)")
    con.execute(f"PRAGMA user_version = {user_version}")
    con.commit()
    con.close()
    return path.read_bytes()


# ── 역할 읽어 내기 (결정 239) ────────────────────────────────────────────────


def _server(path: Path, lanes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[server]\nport = 18790\nlanes = {lanes}\n", encoding="utf-8")


def test_tr4_roles_come_from_the_config_files_that_exist(cfg, tmp_path, monkeypatch):
    assert setup.existing_roles(os.environ) == []
    _server(cfg / "server.toml", 1)
    assert setup.existing_roles(os.environ) == ["coordinator-worker"]
    _server(cfg / "server.toml", 0)
    assert setup.existing_roles(os.environ) == ["coordinator"]
    (cfg / "worker.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    (cfg / "client.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    assert setup.existing_roles(os.environ) == ["coordinator", "worker", "client"]


def test_tr4_rcm_config_and_a_local_rcm_toml_are_read_as_server_configs(
    home, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere.toml"
    _server(elsewhere, 0)
    monkeypatch.setenv("RCM_CONFIG", str(elsewhere))
    assert setup.existing_roles(os.environ) == ["coordinator"]
    monkeypatch.delenv("RCM_CONFIG")
    _server(tmp_path / "rcm.toml", 2)
    assert setup.existing_roles(os.environ) == ["coordinator-worker"]


def test_tr38_a_missing_rcm_config_is_a_usage_error(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("RCM_CONFIG", str(tmp_path / "gone.toml"))
    with pytest.raises(setup.SetupUsage):
        setup.existing_roles(os.environ)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 2
    assert "RCM_CONFIG" in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()


# ── 쓰기: --yes · 덮지 않음 · 반쪽 없음 · 종료 코드 ─────────────────────────────


def test_tr5_coordinator_worker_with_yes_writes_server_toml_with_lanes_and_no_worker_toml(
    cfg, tmp_path
):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["lanes"] >= 1
    assert not (cfg / "worker.toml").exists()
    assert fake.world.stdin.read() == ""  # 아무것도 묻지 않았다


def test_tr5_the_coordinator_role_writes_lanes_zero(cfg, tmp_path):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", role="coordinator")) == 0
    assert read_toml(cfg / "server.toml")["server"]["lanes"] == 0


def test_tr6_an_existing_file_is_kept_and_only_the_forced_kind_is_replaced(cfg, tmp_path):
    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1
    assert (cfg / "server.toml").read_text(encoding="utf-8") == "old = 1\n"
    assert "--force" in fake.err.getvalue()
    # 서비스 파일만 덮으라고 했다 — 설정 파일은 그대로다
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force", "service")) == 1
    assert (cfg / "server.toml").read_text(encoding="utf-8") == "old = 1\n"
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force", "config")) == 0
    assert read_toml(cfg / "server.toml")["server"]["port"] == 18790


def test_tr6_an_existing_service_file_is_kept_without_force_and_nothing_else_is_written(
    cfg, tmp_path
):
    staged = cfg / "service" / "test.rcm.server.plist"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"mine")
    assert go(Fake(), *coordinator_args(tmp_path / "data")) == 1
    assert staged.read_bytes() == b"mine"
    assert not (cfg / "server.toml").exists()  # 하나라도 못 쓰면 아무것도 쓰지 않는다
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force", "service")) == 0
    assert staged.read_bytes() != b"mine"


def test_tr7_a_failure_part_way_leaves_no_half_written_config(cfg, tmp_path):
    cfg.mkdir(parents=True)
    (cfg / "service").write_text("a file where the service folder goes", encoding="utf-8")
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1
    assert not (cfg / "server.toml").exists()
    # 덮으라고 한 설정 파일은 옛 내용으로 돌아온다
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force")) == 1
    assert (cfg / "server.toml").read_text(encoding="utf-8") == "old = 1\n"


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["--force", "config"], 0),  # 썼다
        ([], 1),  # 있는 파일 — 쓰지 못했다
        (["--port", "70000"], 2),  # 사용법
    ],
)
def test_tr28_exit_codes(cfg, tmp_path, argv, code):
    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    assert go(Fake(), *coordinator_args(tmp_path / "data"), *argv) == code


# ── 토큰 · Store ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["--role", "client", "--server", "http://10.0.0.5:18790", "--yes"],
        [
            "--role",
            "worker",
            "--server",
            "http://10.0.0.5:18790",
            "--pool",
            "p1",
            "--lanes",
            "2",
            "--label",
            "test.rcm.worker",
            "--yes",
        ],
        None,
    ],
)
def test_tr8_tokens_in_the_shell_are_never_repeated(cfg, tmp_path, monkeypatch, argv):
    client_token, worker_token = "tok-" + "Q7x" * 6, "wtok-" + "Z9y" * 6
    monkeypatch.setenv("RCM_TOKEN", client_token)
    monkeypatch.setenv("RCM_WORKER_TOKEN", worker_token)
    fake = Fake()
    assert go(fake, *(argv or coordinator_args(tmp_path / "data"))) == 0
    assert fake.text.strip()
    assert client_token not in fake.text and worker_token not in fake.text
    for p in cfg.rglob("*"):
        if p.is_file():
            body = p.read_bytes()
            assert client_token.encode() not in body and worker_token.encode() not in body


def test_tr9_setup_opens_no_store_even_on_a_data_dir_with_a_database(cfg, tmp_path, monkeypatch):
    db = tmp_path / "data" / "rcm.sqlite3"
    before = make_db(db, 7)

    def refuse(*a, **k):
        raise AssertionError("setup opened a database")

    import remote_ci_monitor.store as store

    monkeypatch.setattr(sqlite3, "connect", refuse)
    monkeypatch.setattr(store.Store, "__init__", refuse)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", "--reuse-data-dir")) == 0
    assert read_toml(cfg / "server.toml")["server"]["data_dir"] == str(tmp_path / "data")
    assert db.read_bytes() == before
    assert not db.with_name("rcm.sqlite3-wal").exists()
    assert not db.with_name("rcm.sqlite3-shm").exists()


# ── bind (결정 241 · Q10 · Q14) ──────────────────────────────────────────────


def test_tr10_a_tailscale_address_is_the_recommended_bind():
    choices = core.bind_choices([LAN, TAILNET])
    assert choices[0] == (TAILNET, "tailscale")
    assert ("0.0.0.0", "all") not in choices  # Tailscale 이 있으면 LAN 을 권하지 않는다


def test_tr10_without_tailscale_the_choices_are_the_lan_address_or_all(cfg):
    choices = core.bind_choices([LAN])
    assert (LAN, "lan") in choices and ("0.0.0.0", "all") in choices
    lan, every = " ".join(core.bind_warnings("lan")), " ".join(core.bind_warnings("all"))
    assert "anyone on this network can read" in lan and "DHCP" in lan
    assert "anyone on this network can read" in every
    for kind in ("lan", "all", "tailscale", "loopback"):
        # 없는 읽기 인증 모드를 권하지 않는다(Q14)
        assert all("read_auth" not in w for w in core.bind_warnings(kind))


def test_tr10_choosing_lan_or_all_warns_that_reads_are_open(cfg, tmp_path):
    for bind in (LAN, "0.0.0.0"):
        fake = Fake()
        argv = coordinator_args(tmp_path / "data", "--force")
        argv[argv.index("--bind") + 1] = bind
        assert go(fake, *argv) == 0
        assert "anyone on this network can read" in fake.err.getvalue()


def test_tr10_a_typed_address_must_belong_to_this_machine(cfg, tmp_path):
    fake = Fake()
    argv = coordinator_args(tmp_path / "data")
    argv[argv.index("--bind") + 1] = "10.9.9.9"
    assert go(fake, *argv) == 2
    assert "not an address of this machine" in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()
    # 대화형: 이 머신에 없는 주소는 다시 묻는다
    answers = ["10.9.9.9", LAN, "", "", "", ""]
    fake = Fake(answers=answers)
    assert go(fake, "--role", "coordinator-worker") == 0
    assert "not an address of this machine" in fake.err.getvalue()
    assert read_toml(cfg / "server.toml")["server"]["bind"] == LAN


def test_tr25_a_tailscale_bind_writes_advertise_false_and_warns_about_the_tailnet(cfg, tmp_path):
    fake = Fake(local=(TAILNET,))
    argv = [
        "--role",
        "coordinator-worker",
        "--bind",
        TAILNET,
        "--data-dir",
        str(tmp_path / "data"),
        "--label",
        "test.rcm.server",
        "--yes",
    ]
    assert go(fake, *argv) == 0
    assert read_toml(cfg / "server.toml")["server"]["advertise"] is False
    assert "everyone on your tailnet can read" in fake.err.getvalue()


def test_tr23_yes_on_a_coordinator_role_needs_bind(cfg, tmp_path):
    for role in ("coordinator", "coordinator-worker"):
        fake = Fake()
        argv = [a for a in coordinator_args(tmp_path / "data", role=role)]
        i = argv.index("--bind")
        del argv[i : i + 2]
        assert go(fake, *argv) == 2
        assert "--bind" in fake.err.getvalue()
        assert not (cfg / "server.toml").exists()
    assert core.missing_for_yes("coordinator", {"bind": None}) == ["--bind"]


@pytest.mark.parametrize(
    ("role", "given", "missing"),
    [
        ("client", {}, ["--server"]),
        ("worker", {"pool": "p", "lanes": 1}, ["--server"]),
        ("worker", {"server": "http://h:1", "lanes": 1}, ["--pool"]),
        ("worker", {"server": "http://h:1", "pool": "p"}, ["--lanes"]),
    ],
)
def test_tr35_yes_without_a_required_flag_is_a_usage_error(cfg, role, given, missing):
    assert core.missing_for_yes(role, given) == missing
    argv = ["--role", role, "--yes"]
    for key, value in given.items():
        argv += [f"--{key}", str(value)]
    fake = Fake()
    assert go(fake, *argv) == 2
    assert missing[0] in fake.err.getvalue()
    assert not cfg.exists() or not any(cfg.glob("*.toml"))


# ── 서비스 파일 (결정 241 · 6-B-V1 · V7) ─────────────────────────────────────


def test_tr11_the_launchd_server_file_has_the_required_keys():
    home = Path("/Users/someone")
    doc = plistlib.loads(
        core.server_plist(
            rcm=RCM,
            config=home / ".config/rcm/server.toml",
            home=home,
            label="test.rcm.server",
            log=home / "Library/Logs/rcm/test.rcm.server.log",
            path="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        )
    )
    assert doc["SoftResourceLimits"]["NumberOfFiles"] == 4096
    assert doc["EnvironmentVariables"]["HOME"] == str(home)
    assert doc["WorkingDirectory"] == str(home)
    assert doc["StandardOutPath"] and doc["StandardErrorPath"]
    path = doc["EnvironmentVariables"]["PATH"].split(":")
    assert "/usr/sbin" in path and path[0] != str(RCM.parent)
    assert doc["ProgramArguments"][:2] == [str(RCM), "serve"]


def test_tr11_the_systemd_server_unit_has_the_required_keys():
    home = Path("/home/someone")
    text = core.server_unit(
        rcm=RCM,
        config=home / ".config/rcm/server.toml",
        home=home,
        label="test.rcm.server",
        log=home / ".local/state/rcm/test.rcm.server.log",
        path="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    ).decode()
    lines = text.splitlines()
    assert "LimitNOFILE=4096" in lines
    assert f"Environment=HOME={home}" in lines
    assert f"WorkingDirectory={home}" in lines
    assert any(line.startswith("StandardOutput=append:") for line in lines)
    path_line = next(line for line in lines if line.startswith("Environment=PATH="))
    entries = path_line.split("=", 2)[2].split(":")
    assert "/usr/sbin" in entries and entries[0] != str(RCM.parent)


def test_tr11_doctor_reads_the_written_server_file_with_its_fd_limit(cfg, tmp_path):
    from remote_ci_monitor import doctor

    assert go(Fake(), *coordinator_args(tmp_path / "data")) == 0
    sf = doctor.read_service_file(cfg / "service" / "test.rcm.server.plist")
    assert sf.nofile == 4096 and sf.command == "serve"


def test_tr12_setup_registers_and_starts_nothing_it_only_prints_the_commands(
    cfg, tmp_path, monkeypatch
):
    ran: list[list[str]] = []

    def record(argv, *a, **k):
        ran.append(list(argv))
        raise AssertionError(f"setup ran {argv}")

    monkeypatch.setattr(subprocess, "run", record)
    monkeypatch.setattr(subprocess, "Popen", record)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    assert ran == []
    assert "launchctl bootstrap" in fake.err.getvalue()
    assert not (Path(os.environ["HOME"]) / "Library" / "LaunchAgents").exists()


def test_tr13_the_last_step_runs_only_the_checks_that_need_no_server(cfg, tmp_path, monkeypatch):
    import remote_ci_monitor.client as client

    def no_server(*a, **k):
        raise AssertionError("setup talked to a server")

    monkeypatch.setattr(client.Client, "__init__", no_server)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    err = fake.err.getvalue()
    assert fake.facts_called == 1  # 호스트 사실
    assert "python" in err and "sqlite" in err  # rcm 자신의 요구
    assert "open files" in err and "4096" in err  # 쓴 서비스 파일에서 읽은 상한
    assert "config" in err  # 설정 파일 검증
    assert "advice" in err
    last = err.strip().splitlines()[-1]
    assert "rcm doctor --role coordinator-worker" in last
    assert "start the service" in last and "token" in last


def test_tr15_a_worker_toml_next_to_a_coordinator_worker_setup_is_only_a_warning(cfg, tmp_path):
    cfg.mkdir(parents=True)
    (cfg / "worker.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    assert "worker.toml" in fake.err.getvalue() and "warning" in fake.err.getvalue()
    assert (cfg / "server.toml").exists()


def test_tr18_no_label_means_the_standard_label_and_a_loaded_one_is_only_a_warning(cfg, tmp_path):
    argv = coordinator_args(tmp_path / "data")
    i = argv.index("--label")
    del argv[i : i + 2]
    fake = Fake(loaded=True)
    assert go(fake, *argv) == 0
    staged = cfg / "service" / f"{SERVER_LABEL}.plist"
    assert plistlib.loads(staged.read_bytes())["Label"] == SERVER_LABEL
    assert fake.calls == [("launchd", SERVER_LABEL)]
    assert "already loaded" in fake.err.getvalue()
    # 같은 라벨의 파일이 있으면 --force 없이 덮지 않는다(운영 라벨이라는 이유로 거절하지는 않는다)
    staged.write_bytes(b"kept")
    assert go(Fake(), *argv, "--force", "config") == 1
    assert staged.read_bytes() == b"kept"


def test_tr21_tr27_a_coordinator_setup_prints_a_client_and_a_worker_token_command(cfg, tmp_path):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    err = fake.err.getvalue()
    config = cfg / "server.toml"
    lines = [line for line in err.splitlines() if f"token --config {config} add" in line]
    assert any("--worker" not in line for line in lines)  # client 권한
    assert any(line.rstrip().endswith("--worker") for line in lines)
    assert all("--admin" not in line for line in lines)
    assert all(str(RCM) in line for line in lines)  # 서비스와 같은 빌드로 만든다


def test_tr22_flags_land_in_server_toml_and_the_service_file(cfg, tmp_path):
    fake = Fake()
    argv = [
        "--role",
        "coordinator-worker",
        "--bind",
        LAN,
        "--port",
        "18792",
        "--data-dir",
        "rel/data",
        "--advertise",
        "--label",
        "test.rcm.flags",
        "--yes",
    ]
    assert go(fake, *argv) == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["bind"] == LAN and server["port"] == 18792
    assert server["data_dir"] == str(tmp_path / "rel" / "data")  # 절대 경로
    assert server["advertise"] is True
    doc = plistlib.loads((cfg / "service" / "test.rcm.flags.plist").read_bytes())
    assert doc["Label"] == "test.rcm.flags"
    assert doc["ProgramArguments"][-1] == str(cfg / "server.toml")


def test_tr22_the_same_values_are_asked_in_a_terminal_with_template_defaults(cfg, home, tmp_path):
    # bind 고르기(1 = LAN), port, data_dir, advertise, label — 빈 답은 기본값이다
    fake = Fake(answers=["1", "", "", "", ""])
    assert go(fake, "--role", "coordinator-worker") == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["bind"] == LAN and server["port"] == 8787
    assert server["data_dir"] == str(home / ".local" / "share" / "rcm")
    assert (cfg / "service" / f"{SERVER_LABEL}.plist").exists()
    prompts = fake.err.getvalue()
    for word in ("port", "data_dir", "advertise", "label"):
        assert word in prompts
    fake = Fake(answers=["2", "18793", "mine", "n", "test.rcm.asked"])
    assert go(fake, "--role", "coordinator-worker", "--force") == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["bind"] == "0.0.0.0" and server["port"] == 18793
    assert server["data_dir"] == str(tmp_path / "mine") and server["advertise"] is False
    assert (cfg / "service" / "test.rcm.asked.plist").exists()


def test_tr24_the_service_file_goes_under_the_config_folder_not_launch_agents(cfg, home, tmp_path):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    staged = cfg / "service" / "test.rcm.server.plist"
    assert staged.is_file()
    agents = home / "Library" / "LaunchAgents"
    assert not agents.exists()
    err = fake.err.getvalue()
    assert f"cp -n {staged} {agents / 'test.rcm.server.plist'}" in err
    assert f"launchctl bootstrap gui/$(id -u) {agents / 'test.rcm.server.plist'}" in err
    other = tmp_path / "elsewhere" / "svc.plist"
    assert (
        go(Fake(), *coordinator_args(tmp_path / "data", "--force", "--service-file", str(other)))
        == 0
    )
    assert other.is_file()


def test_tr24_on_linux_the_unit_is_staged_under_its_unit_name(cfg, home, tmp_path):
    fake = Fake(platform="linux")
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    staged = cfg / "service" / "test.rcm.server.service"
    assert staged.is_file()
    err = fake.err.getvalue()
    assert f"cp -n {staged} {home / '.config/systemd/user/test.rcm.server.service'}" in err
    assert "systemctl --user enable --now test.rcm.server.service" in err
    assert fake.calls == [("systemd", "test.rcm.server")]


def test_tr26_a_worker_setup_writes_the_coordinators_url_never_auto(cfg, tmp_path):
    argv = ["--role", "worker", "--pool", "p1", "--lanes", "2", "--label", "test.rcm.worker"]
    fake = Fake()
    assert go(fake, *argv, "--server", "http://10.0.0.5:18790/", "--yes") == 0
    assert read_toml(cfg / "worker.toml")["server"] == "http://10.0.0.5:18790"
    doc = plistlib.loads((cfg / "service" / "test.rcm.worker.plist").read_bytes())
    assert doc["Label"] == "test.rcm.worker"
    assert doc["ProgramArguments"][1:3] == ["worker", "--config"]
    fake = Fake()
    assert go(fake, *argv, "--server", "auto", "--yes", "--force") == 2
    assert "auto" in fake.err.getvalue()


def test_tr31_a_data_dir_that_already_holds_a_database_is_refused_unless_reused(cfg, tmp_path):
    db = tmp_path / "data" / "rcm.sqlite3"
    make_db(db, DB_VERSION - 3)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1
    err = fake.err.getvalue()
    assert "already holds an rcm database" in err and "--reuse-data-dir" in err
    assert not (cfg / "server.toml").exists()
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", "--reuse-data-dir")) == 0
    assert read_toml(cfg / "server.toml")["server"]["data_dir"] == str(tmp_path / "data")
    # 6-B-V3: 헤더의 user_version 과 이 빌드의 DB_VERSION 이 다르면 알린다(DB 는 열지 않는다)
    assert setup.db_header_version(db) == DB_VERSION - 3
    assert f"v{DB_VERSION - 3}" in fake.err.getvalue() and f"v{DB_VERSION}" in fake.err.getvalue()


def test_tr32_a_port_in_use_is_a_warning(cfg, tmp_path):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    try:
        port = held.getsockname()[1]
        assert setup.port_in_use("127.0.0.1", port) is True
    finally:
        held.close()
    assert setup.port_in_use("127.0.0.1", port) is False
    fake = Fake(port_in_use=lambda host, p: True)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    assert "port 18790 is in use" in fake.err.getvalue()


def test_tr34_the_server_file_runs_the_build_that_ran_setup(cfg, tmp_path):
    build = Path("/wt/remote_ci_monitor-x/.venv/bin/rcm")
    fake = Fake(rcm=(build, False))
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    doc = plistlib.loads((cfg / "service" / "test.rcm.server.plist").read_bytes())
    assert doc["ProgramArguments"][0] == str(build)
    assert "found on PATH" not in fake.err.getvalue()
    # 6-B-V6: 돌린 빌드를 못 찾고 PATH 의 rcm 으로 떨어졌으면 그렇다고 알린다
    fake = Fake(rcm=(build, True))
    assert go(fake, *coordinator_args(tmp_path / "data", "--force")) == 0
    assert "found on PATH" in fake.err.getvalue()


def test_tr34_find_rcm_prefers_the_running_executable(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "rcm"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(exe)])
    assert setup.find_rcm() == (exe, False)


def test_tr36_the_copy_command_never_overwrites_and_bootstrap_waits_for_an_equal_file(tmp_path):
    staged, installed = tmp_path / "s.plist", tmp_path / "LaunchAgents" / "s.plist"
    for manager in ("launchd", "systemd"):
        lines = core.install_commands(
            manager, staged=staged, installed=installed, label="s", log=tmp_path / "s.log"
        )
        assert f"cp -n {staged} {installed}" in lines
        # 6-B-U2: `cp -n` 의 종료 코드는 macOS 와 GNU 가 다르다 — 그 코드에 기대지 않는다
        assert not any("cp -n" in line and "&&" in line for line in lines)
        start = next(line for line in lines if "bootstrap" in line or "enable --now" in line)
        assert start.startswith(f"cmp -s {staged} {installed} && ")


def _stub(bin_dir: Path, name: str, code: int, log: Path) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit {code}\n', encoding="utf-8")
    stub.chmod(0o755)


@pytest.mark.parametrize(("code", "loaded"), [(0, True), (113, False)])
def test_tr36_launchd_loaded_check_is_a_read_only_print(tmp_path, monkeypatch, code, loaded):
    log = tmp_path / "calls.log"
    _stub(tmp_path / "bin", "launchctl", code, log)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("launchd", "test.rcm.server") is loaded
    assert log.read_text().splitlines() == [f"print gui/{os.getuid()}/test.rcm.server"]


def test_tr36_systemd_loaded_check_uses_only_is_active_and_is_enabled(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    _stub(tmp_path / "bin", "systemctl", 0, log)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("systemd", "com.remote-ci-monitor.worker") is True
    words = {line.split()[1] for line in log.read_text().splitlines()}
    assert words <= {"is-active", "is-enabled"} and words
    assert all(line.split()[0] == "--user" for line in log.read_text().splitlines())
    assert all(line.split()[-1] == "rcm-worker.service" for line in log.read_text().splitlines())


def test_tr37_the_log_file_is_named_from_the_label(cfg, home, tmp_path):
    assert core.log_file_name("test.rcm.server") == "test.rcm.server.log"
    # 6-B-V7: 표준 라벨은 지금의 로그 이름 그대로다
    assert core.log_file_name(SERVER_LABEL) == "server.log"
    assert core.log_file_name(WORKER_LABEL) == "worker.log"
    assert go(Fake(), *coordinator_args(tmp_path / "data")) == 0
    doc = plistlib.loads((cfg / "service" / "test.rcm.server.plist").read_bytes())
    assert doc["StandardOutPath"] == str(home / "Library/Logs/rcm/test.rcm.server.log")


def test_tr37_one_systemd_unit_name_rule_for_setup():
    # 6-B-V1: 표준 라벨은 rcm-server · rcm-worker, 다른 라벨은 <label>.service
    assert core.unit_name(SERVER_LABEL) == "rcm-server.service"
    assert core.unit_name(WORKER_LABEL) == "rcm-worker.service"
    assert core.unit_name("test.rcm.server") == "test.rcm.server.service"
    assert core.service_file_name(SERVER_LABEL, "systemd") == "rcm-server.service"
    assert core.service_file_name(SERVER_LABEL, "launchd") == f"{SERVER_LABEL}.plist"


def test_tr24_doctor_finds_the_installed_copy_of_a_custom_label_service(
    cfg, home, tmp_path, monkeypatch
):
    """6-B-V1: setup 이 `--label` 로 쓴 서비스를 사람이 출력된 명령대로 설치하면 doctor 는 그 설치
    자리의 파일을 읽는다 — setup 자리의 사본이 아니라 서비스 관리자가 읽는 파일이다(6-B-V2)."""
    from remote_ci_monitor import doctor

    monkeypatch.setattr(doctor, "SYSTEM_UNIT_DIR", tmp_path / "system-units")
    monkeypatch.setattr(doctor, "LAUNCH_DAEMONS_DIR", tmp_path / "launch-daemons")
    data = tmp_path / "data"
    for platform, role, argv in (
        ("darwin", "coordinator-worker", coordinator_args(data)),
        ("linux", "coordinator-worker", coordinator_args(data, "--force")),
        (
            "darwin",
            "worker",
            ["--role", "worker", "--server", "http://10.0.0.5:18790", "--pool", "p1"]
            + ["--lanes", "1", "--label", "test.rcm.worker", "--yes", "--force"],
        ),
    ):
        fake = Fake(platform=platform)
        assert go(fake, *argv) == 0, fake.text
        manager = "launchd" if platform == "darwin" else "systemd"
        label = "test.rcm.worker" if role == "worker" else "test.rcm.server"
        name = core.service_file_name(label, manager)
        staged = cfg / "service" / name
        installed = core.install_dir(manager, home) / name
        assert f"cp -n {staged} {installed}" in fake.err.getvalue()
        # 설치하기 전: setup 자리의 사본을 읽는다
        found = doctor.pick_service_file(None, role, home=home, config_dir=cfg)
        assert found is not None and found.path == staged, (platform, role)
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(staged.read_bytes())
        found = doctor.pick_service_file(None, role, home=home, config_dir=cfg)
        assert found is not None and found.path == installed, (platform, role, found)
        installed.unlink()
        staged.unlink()  # 다음 경우의 setup 자리에 같은 역할의 파일이 둘 남지 않게
    # doctor 가 파일 이름에서 라벨을 되찾는 규칙은 setup 의 이름 규칙을 거꾸로 푼 것이다
    for label in (SERVER_LABEL, WORKER_LABEL, "test.rcm.server"):
        for manager in ("launchd", "systemd"):
            assert core.label_of_file(core.service_file_name(label, manager)) == (label, manager)
    assert core.label_of_file("notes.txt") is None


# ── P3 1라운드 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "abbreviated",
    [["--conf", "x"], ["--data", "x"], ["--service", "x"], ["--lab", "x"], ["--for", "config"]]
    + [["--reuse"]],
)
def test_tr20_setup_takes_no_abbreviated_option(cfg, tmp_path, abbreviated):
    # P3 v1-1 · v2-2: 줄인 옵션은 guard 가 보는 이름과 다르다 — setup 이 받지 않는다(종료 2)
    from remote_ci_monitor import cli

    full = {"--conf": "--config", "--data": "--data-dir", "--service": "--service-file"}
    full |= {"--lab": "--label", "--for": "--force", "--reuse": "--reuse-data-dir"}
    whole = [full[abbreviated[0]], *abbreviated[1:]]
    # 대조: 온전한 이름은 받는다
    setup.build_parser().parse_args(["--role", "coordinator", *whole])
    with pytest.raises(SystemExit) as raised:
        setup.build_parser().parse_args(["--role", "coordinator", *abbreviated])
    assert raised.value.code == 2
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["setup", "--role", "coordinator", *abbreviated])
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("platform", "where"),
    [
        ("darwin", "Library/LaunchAgents/test.rcm.server.plist"),
        ("linux", ".config/systemd/user/test.rcm.server.service"),
        ("linux", ".config/systemd/user/rcm-server.service.d/override.conf"),
        ("darwin", "/Library/LaunchDaemons/test.rcm.server.plist"),
        ("linux", "/etc/systemd/system/test.rcm.server.service"),
    ],
)
def test_tr24_setup_refuses_a_service_file_on_an_install_place(
    cfg, home, tmp_path, platform, where
):
    # P3 v1-2: 서비스 파일은 설치 자리에 쓰지 않는다(§4.1) — `--service-file` 로 가리켜도 같다
    target = Path(where) if where.startswith("/") else home / where
    fake = Fake(platform=platform)
    argv = coordinator_args(tmp_path / "data", "--service-file", str(target), "--force")
    assert go(fake, *argv) == 2, fake.text
    assert "install" in fake.err.getvalue()
    assert not target.exists()
    assert not (cfg / "server.toml").exists()


@pytest.mark.parametrize("odd", ["²", "١", "٣"])
def test_tr22_a_digit_that_is_not_ascii_is_asked_again(cfg, home, tmp_path, odd):
    # P3 v2-3: `"²".isdigit()` 은 참인데 `int("²")` 는 ValueError 다 — 다시 묻는다
    answers = [odd, "coordinator-worker", odd, "1", odd, "", "", "", ""]
    fake = Fake(answers=answers)
    assert go(fake) == 0, fake.text
    assert "Traceback" not in fake.text
    assert read_toml(cfg / "server.toml")["server"]["port"] == 8787
    fake = Fake(answers=["http://10.0.0.5:18790", "p1", odd, "2", ""])
    assert go(fake, "--role", "worker", "--force") == 0, fake.text
    assert read_toml(cfg / "worker.toml")["lanes"] == 2


def test_tr28_an_unexpected_error_is_one_line_and_exit_one(cfg, tmp_path):
    # P3 v2-3: 예상 못 한 예외는 traceback 이 아니라 한 줄 · 종료 1 이다
    def broken():
        raise RuntimeError("interfaces\nsecond line")

    fake = Fake(answers=["", "", "", ""], addresses=broken)
    assert go(fake, "--role", "coordinator-worker") == 1
    err = fake.err.getvalue()
    assert "RuntimeError" in err and "interfaces" in err
    assert "second line" not in err
    assert not (cfg / "server.toml").exists()


def test_tr28_a_data_dir_that_cannot_be_read_is_exit_one(cfg, tmp_path):
    # P3 v1-10: `rcm.sqlite3` 이 있는지 볼 수 없는 data_dir — PermissionError 는 종료 1 한 줄이다
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        fake = Fake()
        assert go(fake, *coordinator_args(locked / "data")) == 1
    finally:
        locked.chmod(0o755)
    assert str(locked / "data") in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()


def test_tr28_a_path_that_is_not_utf8_is_a_usage_error(cfg, tmp_path):
    # P3 v2-10: UTF-8 이 아닌 argv(`os.fsdecode` 의 surrogate)는 설정 파일에 쓸 수 없다 — 종료 2
    odd = os.fsdecode(str(tmp_path).encode() + b"/d\xff")
    fake = Fake()
    assert go(fake, *coordinator_args(Path(odd))) == 2
    assert "UTF-8" in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()


def test_tr28_ctrl_c_while_reporting_is_130_without_a_traceback(cfg, tmp_path):
    # P3 v2-11 b: 파일을 쓴 뒤 보고 중의 Ctrl-C — 130, 쓴 파일이 있다고 말한다
    def interrupted():
        raise KeyboardInterrupt

    fake = Fake(host_facts=interrupted)
    try:
        code: int | str = go(fake, *coordinator_args(tmp_path / "data"))
    except KeyboardInterrupt:
        code = "KeyboardInterrupt escaped setup.run"  # 빨간 시험이 pytest 를 멈추지 않게
    assert code == 130
    err = fake.err.getvalue()
    assert "interrupted" in err and "nothing was written" not in err
    assert (cfg / "server.toml").exists()


@pytest.mark.parametrize(
    "where",
    [
        "with space/home",
        "pct%h/home",
        "dollar$HOME/home",
        'quote"and\\back/home',
    ],
)
def test_tr11_a_systemd_unit_keeps_odd_paths_whole(tmp_path, where):
    # P3 v2-4 · v1-8: systemd 유닛의 경로는 공백 · `%` · `$` · 따옴표를 systemd 규칙대로 쓴다
    from remote_ci_monitor import cli, doctor

    home = tmp_path / where
    rcm = home / "venv dir" / "bin" / "rcm"
    config = home / ".config" / "rcm" / "server.toml"
    log = home / ".local" / "state" / "rcm" / "x.log"
    for text in (
        core.server_unit(
            rcm=rcm, config=config, home=home, label="t.s", log=log, path="/usr/bin:/bin"
        ),
        cli.worker_unit(rcm, config, home),
    ):
        unit = tmp_path / "x.service"
        unit.write_bytes(text)
        sf = doctor.read_service_file(unit, user_home=str(home))
        assert sf.argv[0] == str(rcm) and sf.argv[2:] == ["--config", str(config)], text.decode()
        assert sf.env.get("HOME", str(home)) == str(home)
        assert not sf.unfollowed, sf.unfollowed
        line = next(x for x in text.decode().splitlines() if x.startswith("WorkingDirectory="))
        assert line.removeprefix("WorkingDirectory=").replace("%%", "%") == str(home)


def test_tr11_a_line_break_in_a_unit_path_is_a_usage_error(cfg, home, tmp_path):
    # P3 v2-4: 줄바꿈 · 제어 문자는 유닛에 쓸 수 없다 — 쓰지 않고 종료 2
    fake = Fake(platform="linux")
    config = tmp_path / "odd\nname" / "server.toml"
    argv = coordinator_args(tmp_path / "data", "--config", str(config))
    assert go(fake, *argv) == 2, fake.text
    assert "systemd" in fake.err.getvalue()
    assert not config.exists()
    from remote_ci_monitor import cli

    with pytest.raises(ValueError):
        cli.worker_unit(RCM, config, home)


def test_tr10_the_interactive_run_never_offers_a_read_auth_mode(cfg, tmp_path):
    # P3 TR10 WEAK: 실제 대화형 stderr 에 없는 읽기 인증 모드(`read_auth`)가 나오지 않는다(Q14)
    for answer in ("1", "2"):
        fake = Fake(answers=[answer, "", "", "", ""])
        assert go(fake, "--role", "coordinator-worker", "--force") == 0, fake.text
        err = fake.err.getvalue()
        assert "anyone on this network can read" in err
        assert "read_auth" not in err and "auth =" not in err


def test_tr11_the_written_service_file_path_does_not_start_with_the_venv(cfg, home, tmp_path):
    # P3 TR11 WEAK: setup.run 이 실제로 쓴 파일 — rcm 이 venv bin 에 있어도 PATH 맨 앞은 아니다
    venv_rcm = tmp_path / "venv" / "bin" / "rcm"
    for platform in ("darwin", "linux"):
        fake = Fake(platform=platform, rcm=(venv_rcm, False))
        assert go(fake, *coordinator_args(tmp_path / "data", "--force")) == 0, fake.text
        if platform == "darwin":
            doc = plistlib.loads((cfg / "service" / "test.rcm.server.plist").read_bytes())
            path = doc["EnvironmentVariables"]["PATH"]
        else:
            text = (cfg / "service" / "test.rcm.server.service").read_text()
            line = next(x for x in text.splitlines() if x.startswith("Environment=PATH="))
            path = line.removeprefix("Environment=PATH=")
        entries = path.split(":")
        assert entries[0] != str(venv_rcm.parent), path
        assert str(venv_rcm.parent) not in entries
        assert "/usr/sbin" in entries


def test_tr22_an_unknown_answer_from_the_system_is_said(cfg, tmp_path):
    # P3 v1-5: 포트 · 서비스 조회가 모른다(None)면 그렇다고 한 줄 쓴다
    fake = Fake(port_in_use=lambda host, port: None, loaded=None)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    err = fake.err.getvalue()
    assert "could not tell whether port 18790" in err
    assert "could not tell whether a service labelled test.rcm.server" in err


def test_tr7_a_rollback_that_fails_is_named(cfg, tmp_path, monkeypatch):
    # P3 v1-6: 되돌리기가 실패하면 삼키지 않고 「could not restore <path>」라고 쓴다
    from remote_ci_monitor import cli

    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    real = cli.write_private
    calls: list[Path] = []

    def flaky(path, data, mode):
        calls.append(path)
        if len(calls) >= 2:  # 서비스 파일에서, 그리고 되돌리기에서 실패한다
            raise OSError(13, "Permission denied")
        return real(path, data, mode)

    monkeypatch.setattr(cli, "write_private", flaky)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", "--force")) == 1
    err = fake.err.getvalue()
    assert f"could not restore {cfg / 'server.toml'}" in err
    assert "nothing was written" not in err


@pytest.mark.parametrize(
    ("argv", "flags"),
    [
        (["--lanes", "3"], ["--lanes"]),
        (["--pool", "p", "--server", "http://10.0.0.5:1"], ["--pool", "--server"]),
    ],
)
def test_tr22_a_flag_the_role_does_not_use_is_a_warning(cfg, tmp_path, argv, flags):
    # P3 v1-7: 역할이 쓰지 않는 플래그는 조용히 버리지 않는다
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", *argv, role="coordinator")) == 0
    warned = [x for x in fake.err.getvalue().splitlines() if "not used by --role" in x]
    assert len(warned) == 1, fake.err.getvalue()
    assert all(flag in warned[0] for flag in flags)
    fake = Fake()
    argv = ["--role", "client", "--server", "http://10.0.0.5:1", "--advertise", "--yes"]
    assert go(fake, *argv) == 0
    err = fake.err.getvalue()
    assert "--advertise" in err and "not used by --role client" in err


def test_tr36_the_start_line_says_when_it_did_not_start(tmp_path):
    # P3 v1-9: 설치 자리의 파일이 다르면 조용히 끝나지 않고 그렇다고 말한다
    staged, installed = tmp_path / "s.plist", tmp_path / "LaunchAgents" / "s.plist"
    for manager in ("launchd", "systemd"):
        lines = core.install_commands(
            manager, staged=staged, installed=installed, label="s", log=tmp_path / "s.log"
        )
        start = lines[-1]
        assert start.startswith(f"cmp -s {staged} {installed} && ")
        assert '|| echo "' in start and "not started" in start


def test_tr28_the_no_manager_warning_quotes_the_config_path(cfg, tmp_path):
    # P3 v2-12: 경고 속 명령도 붙여 넣을 수 있게 따옴표를 친다
    config = tmp_path / "with space" / "server.toml"
    fake = Fake(platform="freebsd")
    assert go(fake, *coordinator_args(tmp_path / "data", "--config", str(config))) == 0
    assert f"rcm serve --config '{config}'" in fake.err.getvalue()


@pytest.mark.parametrize("url", ["http://", "https://", "http:///x", "http://:8787"])
def test_tr26_a_url_without_a_host_is_a_usage_error(cfg, tmp_path, url):
    # P3 v2-12: 호스트 없는 URL 은 설정에 쓰지 않는다
    fake = Fake()
    assert go(fake, "--role", "client", "--server", url, "--yes") == 2
    assert "--server" in fake.err.getvalue()
    assert not (cfg / "client.toml").exists()


# ── P3 2라운드: guard 와 setup 이 같은 해석기를 쓴다 ─────────────────────────


def test_tr20_an_empty_path_or_label_is_a_usage_error(cfg, tmp_path):
    # P3 2라운드 v2-1: 빈 `--config` 는 기본 자리(운영 설정 폴더일 수 있다)로 떨어졌다 — 빈 값 ·
    # 공백뿐인 값은 사용법 오류(종료 2)이고 아무것도 쓰지 않는다
    for extra in (
        ["--config="],
        ["--config", ""],
        ["--config", "   "],
        ["--service-file="],
        ["--service-file", " "],
        ["--data-dir", ""],
        ["--data-dir=  "],
        ["--label", ""],
        ["--label", " "],
    ):
        fake = Fake()
        code = go(fake, *coordinator_args(tmp_path / "data"), *extra)
        assert code == 2, (extra, fake.text)
        assert "empty" in fake.err.getvalue(), (extra, fake.text)
        assert not cfg.exists(), extra
        assert not (tmp_path / "data").exists(), extra


def test_tr20_setup_writes_exactly_where_the_shared_resolver_says(home, tmp_path, monkeypatch):
    # P3 2라운드 근본 원인: guard 는 setup 이 어디에 쓸지를 setup 과 따로 계산했다. 이제 둘 다
    # `core.where` 하나를 쓴다 — setup 이 실제로 쓴 자리가 그 함수의 답과 같다(상대 경로 · `~` ·
    # 상대 XDG · `--config` · `--service-file` · `--data-dir` 의 조합 전부)
    import itertools

    where = getattr(core, "where", None)
    assert where is not None, "core.where — the one resolver guard and setup share"
    monkeypatch.setenv("XDG_CONFIG_HOME", "rel-xdg")
    roles = ("worker", "coordinator-worker", "client")
    configs = (None, "c/s.toml", "~/c{n}/s.toml")
    services = (None, "svc/f.x", "~/svc{n}/f.x")
    datas = ("d", "~/d{n}")
    for n, (role, config, service, data) in enumerate(
        itertools.product(roles, configs, services, datas)
    ):
        work = tmp_path / f"w{n}"
        work.mkdir()
        monkeypatch.chdir(work)
        given = {
            "config": config and config.format(n=n),
            "service_file": service and service.format(n=n),
            "data_dir": data.format(n=n),
        }
        if role == "client":
            argv = ["--role", role, "--server", "http://10.0.0.5:18790", "--yes"]
            label = None
        elif role == "worker":
            argv = ["--role", role, "--server", "http://10.0.0.5:18790", "--pool", "p"]
            argv += ["--lanes", "1", "--yes"]
            label = f"test.rcm.w{n}"
        else:
            argv = ["--role", role, "--bind", "127.0.0.1", "--port", "18790", "--no-advertise"]
            argv += ["--yes"]
            label = f"test.rcm.s{n}"
        if label:
            argv += ["--label", label]
        for flag, value in given.items():
            if value is not None:
                argv += [f"--{flag.replace('_', '-')}", value]
        fake = Fake()
        assert go(fake, *argv) == 0, (argv, fake.text)
        want = where(
            role,
            config=given["config"],
            label=label,
            service_file=given["service_file"],
            data_dir=given["data_dir"],
            environ=dict(os.environ),
            cwd=Path.cwd(),
            home=Path.home(),
            manager=core.LAUNCHD,
        )
        wrote = {Path(line) for line in fake.out.getvalue().splitlines()}
        assert wrote == {p for p in (want.config, want.service_file) if p is not None}, argv
        assert all(p.is_file() for p in wrote), argv
        if role == "coordinator-worker":
            assert read_toml(want.config)["server"]["data_dir"] == str(want.data_dir), argv


def test_tr24_an_install_place_in_another_case_is_refused(cfg, home, tmp_path, monkeypatch):
    # P3 2라운드: macOS 의 기본 파일시스템은 대소문자를 가리지 않는다 — `~/library/launchagents`
    # 도 서비스 관리자가 읽는 자리다
    monkeypatch.setattr(core, "FOLD_CASE", True, raising=False)
    target = home / "library" / "launchagents" / "test.rcm.server.plist"
    fake = Fake(platform="darwin")
    argv = coordinator_args(tmp_path / "data", "--service-file", str(target))
    assert go(fake, *argv) == 2, fake.text
    assert "install" in fake.err.getvalue()
    assert not target.exists()


def test_tr20_a_label_with_a_trailing_line_break_is_a_usage_error(cfg, tmp_path):
    # P3 2라운드 v1-5: 라벨 정규식은 끝까지 맞아야 한다 — `$` 는 끝 줄바꿈 앞에서도 맞는다
    assert core.label_problem("test.rcm.server\n") is not None
    fake = Fake()
    argv = [*coordinator_args(tmp_path / "data"), "--label", "test.rcm.server\n"]
    assert go(fake, *argv) == 2, fake.text
    assert not cfg.exists()


def test_tr28_a_report_that_fails_after_writing_is_exit_zero(cfg, tmp_path):
    # P3 2라운드 v2-1(MED): 설정을 다 쓴 뒤 보고가 터지면 종료 0 이다(§4.1 「설정을 썼으면 0」)
    def broken():
        raise RuntimeError("facts\nsecond line")

    fake = Fake(host_facts=broken)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0, fake.text
    err = fake.err.getvalue()
    assert "files written; the report failed: RuntimeError: facts" in err
    assert "second line" not in err and "Traceback" not in err
    assert (cfg / "server.toml").is_file()
