"""CLI(M5b-2) — `rcm token add NAME --worker` · `rcm token list` 의 kind 열 · 워커 토큰 revoke.
명세는 docs/m5b2-workplan.md §1 「토큰 종류」(`--worker` → kind worker, `--admin` 과 같이 쓰면
오류 · list 에 kind 열) · §6 「CLI」(`rcm token add NAME [--admin|--worker]` · `rcm token list` 열
`name  kind  created  revoked`) · §6 「저장소」(`TokenInfo.kind`, `admin` 불리언은
`kind == "admin"`).

test_e2e_loopback.ServerProc.token 과 같은 서버 쪽 호출 — `rcm token --config <server.toml> …` 을
in-process `main(argv)` 로 부른다(서버 프로세스는 필요 없다: 토큰 명령은 DB 만 만진다). 저장소 행은
`Store.list_tokens()` 의 `TokenInfo.kind` 로, 찍힌 비밀은 `Store.verify_token()` 으로 확인한다.

잠그는 모양(구현 전 — test-first):
- `rcm token list` 첫 줄은 헤더 `name  kind  created  revoked`(공백으로 나누면 네 단어, 이 순서).
- 행은 `<name> <kind> <YYYY-MM-DD> <revoked>` — revoked 열은 폐기 전이면 `—`, 폐기 뒤면
  `YYYY-MM-DD`.
  (`created 2026-09-06  active` 같은 오늘의 두 단어 표기는 헤더 열과 어긋나므로 버린다.)
- `--admin --worker` 는 종료 코드 2(usage) · stdout 에 아무것도 없다(토큰이 만들어지지 않는다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from remote_ci_monitor.cli import main
from remote_ci_monitor.store import Store

HEADER = ["name", "kind", "created", "revoked"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DASH = "—"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@dataclass(frozen=True)
class Setup:
    config: Path
    data_dir: Path

    @property
    def db(self) -> Path:
        return self.data_dir / "rcm.sqlite3"


@pytest.fixture
def setup(tmp_path: Path, monkeypatch) -> Setup:
    """tmp 의 server.toml(data_dir 은 tmp/data). HOME · RCM_CONFIG 를 격리해 실제 설정을 안 본다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    data_dir = tmp_path / "data"
    config = tmp_path / "server.toml"
    config.write_text(f'[server]\ndata_dir = "{data_dir}"\n')
    return Setup(config=config, data_dir=data_dir)


def token(capsys, setup: Setup, *args: str) -> tuple[int, str, str]:
    """`rcm token --config <server.toml> <args…>` — e2e_loopback 의 ServerProc.token 과 같다."""
    return run(capsys, ["token", "--config", str(setup.config), *args])


def add(capsys, setup: Setup, name: str, *flags: str) -> str:
    """토큰을 만들고 stdout 에 한 줄로 찍힌 비밀을 돌려준다."""
    code, out, err = token(capsys, setup, "add", name, *flags)
    assert code == 0, err
    lines = out.splitlines()
    assert len(lines) == 1 and lines[0].strip(), out  # 비밀은 stdout 에 정확히 한 줄
    return lines[0].strip()


def stored(setup: Setup) -> dict[str, object]:
    """`Store.list_tokens()` 를 이름 → TokenInfo 로."""
    store = Store(setup.db)
    try:
        return {t.name: t for t in store.list_tokens()}
    finally:
        store.close()


def verified(setup: Setup, secret: str):
    store = Store(setup.db)
    try:
        return store.verify_token(secret)
    finally:
        store.close()


def list_rows(capsys, setup: Setup) -> tuple[list[str], dict[str, list[str]]]:
    """`rcm token list` → (헤더 단어들, 이름 → 행 단어들)."""
    code, out, err = token(capsys, setup, "list")
    assert code == 0, err
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, out
    header = lines[0].split()
    rows = {ln.split()[0]: ln.split() for ln in lines[1:]}
    return header, rows


# ── rcm token add --worker ───────────────────────────────────────────────────


def test_add_worker_prints_the_token_once_and_stores_kind_worker(capsys, setup):
    code, out, err = token(capsys, setup, "add", "build-02", "--worker")
    assert code == 0, err
    lines = out.splitlines()
    assert len(lines) == 1, out  # 비밀 한 줄 — 안내문은 stderr 에
    secret = lines[0].strip()
    assert len(secret) >= 32 and " " not in secret, secret
    assert "build-02" in err and "created" in err, err  # 오늘의 안내문 그대로(shown once)
    row = stored(setup)["build-02"]
    assert row.kind == "worker"
    assert row.admin is False  # admin 불리언은 kind == "admin" 과 항상 같다
    assert row.revoked_at is None
    # 찍힌 비밀이 곧 저장된 토큰이다 — 검증하면 같은 kind 로 돌아온다
    info = verified(setup, secret)
    assert info is not None and info.name == "build-02" and info.kind == "worker"


def test_add_without_flags_is_a_client_token(capsys, setup):
    secret = add(capsys, setup, "alice-laptop")
    row = stored(setup)["alice-laptop"]
    assert row.kind == "client" and row.admin is False
    assert verified(setup, secret).kind == "client"


def test_add_admin_is_kind_admin_and_the_admin_flag_stays_true(capsys, setup):
    secret = add(capsys, setup, "macmini-admin", "--admin")
    row = stored(setup)["macmini-admin"]
    assert row.kind == "admin" and row.admin is True
    assert verified(setup, secret).kind == "admin"


@pytest.mark.parametrize(
    "flags", [["--admin", "--worker"], ["--worker", "--admin"]], ids=["admin-first", "worker-first"]
)
def test_add_admin_and_worker_together_is_a_usage_error(capsys, setup, flags):
    code, out, err = token(capsys, setup, "add", "build-02", *flags)
    assert code == 2, (out, err)
    assert out == "", out  # 토큰(비밀)이 찍히면 안 된다
    assert "--worker" in err and "--admin" in err, err  # 어느 두 플래그가 부딪혔는지 말한다
    assert "build-02" not in stored(setup)  # 만들어지지도 않았다


def test_add_help_lists_the_worker_flag(capsys, setup):
    code, out, err = run(capsys, ["token", "add", "--help"])
    assert code == 0, err
    assert "--worker" in out and "--admin" in out, out


def test_worker_and_client_names_share_one_namespace(capsys, setup):
    """토큰 하나 = 워커 하나(§2) — 같은 이름으로 두 번 만들 수 없다(kind 가 달라도)."""
    add(capsys, setup, "build-02", "--worker")
    code, out, err = token(capsys, setup, "add", "build-02")
    assert code == 2 and out == "", (out, err)
    assert "build-02" in err and "exists" in err, err
    assert stored(setup)["build-02"].kind == "worker"  # 처음 것이 그대로


# ── rcm token list: kind 열 ──────────────────────────────────────────────────


def test_list_header_is_name_kind_created_revoked_in_that_order(capsys, setup):
    add(capsys, setup, "alice-laptop")
    header, _rows = list_rows(capsys, setup)
    assert header == HEADER, header


def test_list_shows_client_admin_and_worker_in_the_kind_column(capsys, setup):
    secrets = {
        "alice-laptop": add(capsys, setup, "alice-laptop"),
        "macmini-admin": add(capsys, setup, "macmini-admin", "--admin"),
        "build-02": add(capsys, setup, "build-02", "--worker"),
    }
    code, out, err = token(capsys, setup, "list")
    assert code == 0, err
    for secret in secrets.values():
        assert secret not in out  # list 는 비밀을 절대 안 보여 준다
    header, rows = list_rows(capsys, setup)
    kind_col = header.index("kind")
    assert set(rows) == set(secrets), rows
    assert rows["alice-laptop"][kind_col] == "client", rows["alice-laptop"]
    assert rows["macmini-admin"][kind_col] == "admin", rows["macmini-admin"]
    assert rows["build-02"][kind_col] == "worker", rows["build-02"]
    # 오늘의 `user `/`admin` 플래그 표기는 kind 열로 바뀐다 — 「user」라는 단어는 더 없다
    assert "user" not in out.split(), out


def test_list_rows_follow_the_header_columns(capsys, setup):
    add(capsys, setup, "build-02", "--worker")
    header, rows = list_rows(capsys, setup)
    row = rows["build-02"]
    assert len(row) == len(header), (header, row)
    assert row[header.index("name")] == "build-02"
    assert row[header.index("kind")] == "worker"
    assert DATE_RE.match(row[header.index("created")]), row  # created 는 날짜 한 단어
    assert row[header.index("revoked")] == DASH, row  # 폐기 전은 —


# ── rcm token revoke: 워커 토큰도 된다 ───────────────────────────────────────


def test_revoke_works_for_worker_tokens(capsys, setup):
    secret = add(capsys, setup, "build-02", "--worker")
    assert verified(setup, secret) is not None
    code, out, err = token(capsys, setup, "revoke", "build-02")
    assert code == 0, err
    assert "build-02" in err and "revoked" in err, err
    assert verified(setup, secret) is None  # 더는 인증되지 않는다
    row = stored(setup)["build-02"]
    assert row.kind == "worker" and row.revoked_at is not None  # kind 는 남는다(감사용)
    header, rows = list_rows(capsys, setup)
    assert rows["build-02"][header.index("kind")] == "worker"
    assert DATE_RE.match(rows["build-02"][header.index("revoked")]), rows["build-02"]


def test_revoke_twice_is_a_usage_error_for_worker_tokens_too(capsys, setup):
    add(capsys, setup, "build-02", "--worker")
    assert token(capsys, setup, "revoke", "build-02")[0] == 0
    code, _out, err = token(capsys, setup, "revoke", "build-02")
    assert code == 2 and "build-02" in err, err
