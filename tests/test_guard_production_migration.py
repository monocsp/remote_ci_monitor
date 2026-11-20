"""`tools/guard_production.py` — 다른 빌드가 옛 DB 를 그 자리에서 마이그레이션하는 것(사건 6f).

2026-11-20: 격리 검증 에이전트가 dev 워크트리 안에서 `--config` 없이 `rcm token add` · `rcm serve`
를 돌렸다. CLI 는 작업 폴더의 `rcm.toml` 을 집었고, 그 설정이 가리키는 개발 DB(`.rcm-data`, v7)를
v24 로 올렸다. 운영 DB 는 무사했지만 그 폴더의 옛 빌드는 이제 그 DB 를 거절한다.

규칙: `rcm serve|worker|token` 이 유효 `data_dir`(명시 `--config`/`--data-dir`, env, 작업 폴더의
`rcm.toml`) 의 `rcm.sqlite3` 를 열 때, 파일 헤더의 user_version(60번째 바이트부터 4바이트,
big-endian — sqlite 로 연결하지 않는다)이 실행 파일의 빌드가 아는 `DB_VERSION` 보다 낮으면 막는다.
`RCM_ALLOW_MIGRATE=1` 을 명령 앞에 붙이면 지나간다. 둘 중 하나라도 모르면 지나간다. 서비스 venv 가
운영 데이터를 여는 것은 정상 운영이라 지나간다.

경로는 전부 `tmp_path` 아래의 진짜 파일이다 — 판정이 읽는 것이 헤더와 `store.py` 이기 때문이다.
"""

import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_guard():
    path = ROOT / "tools" / "guard_production.py"
    spec = importlib.util.spec_from_file_location("guard_production_migration", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()
NO_PROD = guard.Production()


def _db(data_dir: Path, version: int) -> Path:
    """user_version 이 `version` 인 sqlite 헤더 100바이트 — 판정은 이것만 읽는다."""
    data_dir.mkdir(parents=True, exist_ok=True)
    header = bytearray(100)
    header[:16] = b"SQLite format 3\x00"
    header[60:64] = struct.pack(">I", version)
    path = data_dir / "rcm.sqlite3"
    path.write_bytes(bytes(header) + b"\x00" * 3996)
    return path


def _source(root: Path, db_version: int | None) -> Path:
    """`src/remote_ci_monitor/store.py` 에 `DB_VERSION = N` 이 있는 체크아웃."""
    store = root / "src" / "remote_ci_monitor" / "store.py"
    store.parent.mkdir(parents=True)
    line = f"DB_VERSION = {db_version}\n" if db_version is not None else "VERSION = 'x'\n"
    store.write_text('"""store"""\n\nimport sqlite3\n\n' + line)
    return root


def _venv(root: Path, source: Path | None = None, *, installed: int | None = None) -> Path:
    """`source` 를 가리키는 editable venv, 또는 `installed` 판의 store.py 를 품은 보통 설치."""
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n")
    for name in ("rcm", "python"):
        launcher = root / "bin" / name
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
    site = root / "lib" / "python3.11" / "site-packages"
    info = site / "remote_ci_monitor-0.7.0.dist-info"
    info.mkdir(parents=True)
    if source is not None:
        (info / "direct_url.json").write_text(
            json.dumps({"dir_info": {"editable": True}, "url": f"file://{source}"})
        )
    if installed is not None:
        store = site / "remote_ci_monitor" / "store.py"
        store.parent.mkdir(parents=True)
        store.write_text(f"DB_VERSION = {installed}\n")
    return root


@pytest.fixture
def world(tmp_path, monkeypatch):
    """사건의 모양: dev 워크트리(`rcm.toml` 이 `.rcm-data` 를 가리킨다)와 v24 빌드의 `.venv`,
    그 폴더에 남은 v7 개발 DB."""
    worktree = tmp_path / "remote_ci_monitor-dev"
    _source(worktree, 24)
    venv = _venv(worktree / ".venv", worktree)
    (worktree / "rcm.toml").write_text('[server]\nport = 8799\ndata_dir = ".rcm-data"\n')
    db = _db(worktree / ".rcm-data", 7)
    monkeypatch.setattr(guard, "_which", lambda name: str(venv / "bin" / name))
    monkeypatch.delenv("RCM_ALLOW_MIGRATE", raising=False)
    return {"worktree": worktree, "venv": venv, "db": db, "tmp": tmp_path}


def bash(command: str, cwd: Path, prod=NO_PROD, environ: dict[str, str] | None = None):
    return guard.decide(
        "Bash",
        {"command": command},
        prod,
        cwd,
        local_config=(cwd / "rcm.toml").exists(),
        environ=environ if environ is not None else {},
    )


# ── 막는 것 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "rcm token add verifier",
        "rcm token list",
        "rcm serve",
        ".venv/bin/rcm serve",
        "./.venv/bin/python -m remote_ci_monitor.cli token add verifier",
        "cd . && rcm serve",
        "env PYTHONUNBUFFERED=1 rcm serve",
    ],
)
def test_the_incident_shape_is_denied(world, command):
    """dev 워크트리 안에서 `--config` 없이 — 작업 폴더의 `rcm.toml` 이 v7 DB 를 가리킨다."""
    v = bash(command, world["worktree"])
    assert v is not None and v.decision == "deny", command
    assert "v7" in v.reason and "v24" in v.reason, v.reason
    assert "RCM_ALLOW_MIGRATE=1" in v.reason and "data_dir" in v.reason, v.reason


def test_an_explicit_config_or_data_dir_is_read_too(world):
    other = world["tmp"] / "elsewhere"
    other.mkdir()
    config = world["tmp"] / "old.toml"
    data = world["tmp"] / "old-data"
    _db(data, 21)
    config.write_text(f'[server]\ndata_dir = "{data}"\n')
    for command in (
        f"rcm serve --config {config}",
        f"rcm token --config {config} add laptop",
        f"rcm serve --data-dir {data}",
        f"RCM_SERVER_DATA_DIR={data} rcm serve",
        f"RCM_CONFIG={config} rcm token list",
    ):
        v = bash(command, other)
        assert v is not None and v.decision == "deny", command
        assert "v21" in v.reason and "v24" in v.reason, v.reason


def test_a_plain_install_reads_its_own_store_py(world, tmp_path, monkeypatch):
    venv = _venv(tmp_path / "pipx" / "rcm", installed=24)
    monkeypatch.setattr(guard, "_which", lambda name: str(venv / "bin" / name))
    v = bash("rcm token add x", world["worktree"])
    assert v is not None and v.decision == "deny" and "v24" in v.reason


def test_a_remote_worker_whose_data_dir_holds_an_old_db_is_denied(world):
    """`rcm worker` 는 `worker.toml` 의 최상위 `data_dir` 을 연다."""
    data = world["tmp"] / "worker-data"
    _db(data, 3)
    config = world["tmp"] / "worker.toml"
    config.write_text(f'server = "http://127.0.0.1:8799"\ndata_dir = "{data}"\n')
    v = bash(f"rcm worker --config {config}", world["tmp"])
    assert v is not None and v.decision == "deny"


def test_the_service_build_on_some_other_old_db_is_still_denied(world, tmp_path, monkeypatch):
    """서비스 venv 가 지나가는 것은 **운영 데이터**를 열 때뿐이다 — 다른 옛 DB 는 그대로 올린다."""
    checkout = _source(tmp_path / "service-checkout", 24)
    service = _venv(tmp_path / "rcm-venv", checkout)
    prod = guard.Production(checkout=checkout, venv=service, data_dir=tmp_path / "prod-data")
    monkeypatch.setattr(guard, "_which", lambda name: str(service / "bin" / name))
    v = bash("rcm token add x", world["worktree"], prod=prod)
    assert v is not None and v.decision == "deny" and "v7" in v.reason


# ── 지나가는 것 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "RCM_ALLOW_MIGRATE=1 rcm token add verifier",
        "RCM_ALLOW_MIGRATE=1 rcm serve",
        "env RCM_ALLOW_MIGRATE=1 rcm serve",
        "rcm serve --help",
        "rcm token --help",
        "rcm version",
        "rcm status",
        "rcm gc --dry-run --config rcm.toml",
        "sqlite3 -readonly .rcm-data/rcm.sqlite3 'pragma user_version'",
    ],
)
def test_an_explicit_prefix_and_commands_that_do_not_open_the_db_pass(world, command):
    assert bash(command, world["worktree"]) is None, command


def test_a_session_variable_is_not_the_prefix(world):
    """명령 앞에 쓴 것만 의도로 본다 — 셸에 남은 export 는 다음 세션까지 따라간다."""
    v = bash("rcm serve", world["worktree"], environ={"RCM_ALLOW_MIGRATE": "1"})
    assert v is not None and v.decision == "deny"


@pytest.mark.parametrize("version", [24, 25])
def test_a_db_at_or_above_the_build_version_passes(world, version):
    """같은 판이면 열어도 안 바뀐다. 더 새 DB 는 빌드가 스스로 거절한다(훅이 볼 일이 아니다)."""
    _db(world["worktree"] / ".rcm-data", version)
    assert bash("rcm serve", world["worktree"]) is None
    assert bash("rcm token add x", world["worktree"]) is None


def test_a_missing_db_passes(world):
    world["db"].unlink()
    assert bash("rcm serve", world["worktree"]) is None


def test_a_non_empty_wal_makes_the_header_unknown(world):
    """WAL 모드에서 막 마이그레이션한 DB 는 새 user_version 이 아직 `-wal` 에만 있을 수 있다 —
    헤더의 판이 낡았을 수 있으니 모르는 것으로 보고 지나간다(이미 올라간 DB 를 막는 오탐)."""
    wal = world["db"].with_name("rcm.sqlite3-wal")
    wal.write_bytes(b"\x00" * 32)
    assert bash("rcm serve", world["worktree"]) is None
    wal.write_bytes(b"")  # 비어 있는 -wal 은 헤더가 정본이다
    v = bash("rcm serve", world["worktree"])
    assert v is not None and v.decision == "deny"


def test_a_file_that_is_not_sqlite_passes(world):
    world["db"].write_bytes(b"not a database at all, just text\n" * 10)
    assert bash("rcm serve", world["worktree"]) is None


def test_an_unknown_build_version_passes(world, tmp_path, monkeypatch):
    """`store.py` 에 `DB_VERSION` 이 없으면 빌드 판을 모른다 — 모르면 막지 않는다."""
    source = _source(tmp_path / "odd", None)
    venv = _venv(tmp_path / "odd-venv", source)
    monkeypatch.setattr(guard, "_which", lambda name: str(venv / "bin" / name))
    assert bash("rcm serve", world["worktree"]) is None


def test_an_executable_outside_any_venv_passes(world, monkeypatch):
    monkeypatch.setattr(guard, "_which", lambda name: None)
    assert bash("rcm serve", world["worktree"]) is None
    monkeypatch.setattr(guard, "_which", lambda name: "/usr/bin/" + name)
    assert bash("python3 -m remote_ci_monitor.cli serve", world["worktree"]) is None


def test_the_service_build_on_production_data_passes(tmp_path, monkeypatch):
    """서비스 자신이 운영 DB 를 여는 것은 정상 운영(배포 뒤 재시작이 마이그레이션한다)."""
    checkout = _source(tmp_path / "service-checkout", 24)
    service = _venv(tmp_path / "rcm-venv", checkout)
    data = tmp_path / "prod-data"
    _db(data, 23)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "server.toml").write_text(f'[server]\ndata_dir = "{data}"\n')
    prod = guard.Production(checkout=checkout, venv=service, config_dir=config_dir, data_dir=data)
    monkeypatch.setattr(guard, "_which", lambda name: str(service / "bin" / name))
    cwd = tmp_path / "anywhere"
    cwd.mkdir()
    assert (
        bash(f"{service}/bin/rcm token --config {config_dir}/server.toml list", cwd, prod) is None
    )


# ── 진짜 훅: JSON stdin → 종료 코드 · stdout ─────────────────────────────────


def test_the_real_hook_denies_the_incident_and_lets_the_prefix_through(world):
    def run(command: str):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(world["worktree"]),
        }
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "guard_production.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
        )

    venv_rcm = world["venv"] / "bin" / "rcm"
    proc = run(f"{venv_rcm} token add verifier")
    assert proc.returncode == 0 and proc.stderr == "", proc
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    assert "v7" in out["permissionDecisionReason"] and "v24" in out["permissionDecisionReason"]

    proc = run(f"RCM_ALLOW_MIGRATE=1 {venv_rcm} token add verifier")
    assert proc.returncode == 0 and proc.stdout.strip() == "" and proc.stderr == "", proc
