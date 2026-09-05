"""CLI(M4) — `rcm init server/client` · `rcm version [--json]` · `rcm check` 의 python·git 행.
명세는 docs/m4-workplan.md §2.

test_cli_m1 처럼 `main(argv)` 를 in-process 로 부르고 RCM_SERVER/RCM_TOKEN 만으로 서버를 가리킨다.
HOME 은 tmp 로 옮기고 XDG_CONFIG_HOME 은 지워서 기본 경로가 `<HOME>/.config/rcm/` 이 되게 한다.
argparse 의 usage 오류는 `main` 의 try 밖(`parse_args`)에서 SystemExit(2) 로 나오므로 `run` 이 받아
코드로 바꾼다. 구현보다 먼저 썼다(test-first) — 아직 없는 기능은 빨갛다.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import stat
import sys
import tarfile
from pathlib import Path
from typing import NamedTuple

import pytest

from remote_ci_monitor import SCHEMA_VERSION, __version__
from remote_ci_monitor.cli import main
from remote_ci_monitor.config import load_client_config, load_server_config
from test_server import Server

ROOT = Path(__file__).resolve().parents[1]
# 템플릿의 정본은 examples/ 다(명세 §1: 패키지 안 templates/ 와 바이트 단위로 같다 —
# test_packaging 이 잠근다).
SERVER_TEMPLATE = (ROOT / "examples" / "server.toml").read_bytes()
CLIENT_TEMPLATE = (ROOT / "examples" / "client.toml").read_bytes()
SERVER_LINE_RE = re.compile(r"^\s*server\s*=")

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

# `rcm check --config` 에 줄 서버 설정. data_dir 은 tmp 안(데이터 디렉터리 행이 ok 가 되게).
REPO_SERVER_TOML = """\
[server]
data_dir = "{data_dir}"

[[repos]]
name = "app"
url = "git@example.com:org/app.git"

[[presets]]
name = "deploy"
argv = ["bash", "scripts/deploy.sh"]
source_modes = ["git_ref"]
repo = "app"
"""

TREE_SERVER_TOML = """\
[server]
data_dir = "{data_dir}"

[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
"""


class _VersionInfo(NamedTuple):
    """`sys.version_info` 대역 — 진짜는 structseq 라 만들 수 없다.

    튜플 비교와 `.major` 속성 접근이 둘 다 된다.
    """

    major: int
    minor: int
    micro: int
    releaselevel: str
    serial: int


# ── 도우미 ───────────────────────────────────────────────────────────────────


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` → (code, stdout, stderr). argparse 의 SystemExit(2) 도 코드로 돌려준다."""
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def row_status(out: str, name: str) -> str | None:
    """`rcm check` 출력에서 `name` 행의 상태(`ok`/`FAIL`). 행이 없으면 None.

    출력 형식은 cmd_check 의 `{'ok ' if ok else 'FAIL'}  {name:<13} {detail}` — 이름 뒤가 공백이거나
    줄 끝이어야 `git` 이 `gitops` 같은 다른 이름에 맞지 않는다.
    """
    m = re.search(rf"^(ok |FAIL)  {re.escape(name)}(?:\s|$)", out, re.M)
    return m.group(1).strip() if m else None


def first_line(out: str) -> str:
    assert out.strip(), "rcm check printed nothing"
    return out.splitlines()[0]


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
def env(monkeypatch, home):
    """test_cli_m1.env 와 같다 — `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def server_toml(tmp_path):
    """`write(text)` — tmp 에 server.toml 을 쓰고 경로를 준다. data_dir 은 tmp/check-data."""

    def write(text: str) -> Path:
        p = tmp_path / "server.toml"
        p.write_text(text.format(data_dir=tmp_path / "check-data"))
        return p

    return write


@pytest.fixture
def no_git(monkeypatch, tmp_path) -> Path:
    """빈 디렉터리만 PATH 에 두면 `shutil.which("git")` 이 None 이다(test_config 의 요령)."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("git") is None
    return empty


# ── rcm init server ──────────────────────────────────────────────────────────


def test_init_server_writes_the_template_to_the_default_path(home, capsys):
    code, out, err = run(capsys, ["init", "server"])
    assert code == 0, err
    path = home / ".config" / "rcm" / "server.toml"
    assert path.is_file(), f"not created: {path}"
    assert path.read_bytes() == SERVER_TEMPLATE  # 템플릿 그대로
    assert out == f"{path}\n"  # stdout 은 경로 한 줄뿐
    assert mode(path) == 0o644
    # 다음 단계 안내: edit presets → rcm token add <name> → rcm serve
    assert "preset" in err.lower() and "rcm token add" in err and "rcm serve" in err, err


@pytest.mark.parametrize(
    "kind,extra",
    [("server", []), ("client", ["--server", "http://build:8787"])],
    ids=["server", "client"],
)
def test_init_honours_xdg_config_home(home, monkeypatch, tmp_path, capsys, kind, extra):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    code, out, err = run(capsys, ["init", kind, *extra])
    assert code == 0, err
    path = xdg / "rcm" / f"{kind}.toml"
    assert path.is_file() and out == f"{path}\n"
    assert not (home / ".config").exists()  # HOME 쪽에는 아무것도 안 생긴다


def test_init_server_refuses_to_overwrite_unless_forced(home, capsys):
    path = home / ".config" / "rcm" / "server.toml"
    path.parent.mkdir(parents=True)
    edited = b"# edited by hand - must survive\n"
    path.write_bytes(edited)
    code, out, err = run(capsys, ["init", "server"])
    assert code == 2, err
    assert "refusing to overwrite" in err and str(path) in err and "--force" in err, err
    assert out == ""
    assert path.read_bytes() == edited
    code, out, err = run(capsys, ["init", "server", "--force"])
    assert code == 0, err
    assert path.read_bytes() == SERVER_TEMPLATE and out == f"{path}\n"
    assert mode(path) == 0o644


def test_init_server_path_option_creates_parents(home, tmp_path, capsys):
    custom = tmp_path / "deep" / "er" / "rcm.toml"
    code, out, err = run(capsys, ["init", "server", "--path", str(custom)])
    assert code == 0, err
    assert custom.read_bytes() == SERVER_TEMPLATE and out == f"{custom}\n"
    assert mode(custom) == 0o644
    assert not (home / ".config" / "rcm" / "server.toml").exists()


def test_init_without_a_kind_or_with_an_unknown_kind_is_usage(home, capsys):
    code, out, _ = run(capsys, ["init"])
    assert code == 2 and out == ""
    code, out, _ = run(capsys, ["init", "agent"])
    assert code == 2 and out == ""
    assert not (home / ".config").exists()


# ── rcm init client ──────────────────────────────────────────────────────────


def test_init_client_substitutes_server_keeps_the_rest_and_is_private(home, capsys):
    code, out, err = run(capsys, ["init", "client", "--server", "http://build:8787/"])
    assert code == 0, err
    path = home / ".config" / "rcm" / "client.toml"
    assert out == f"{path}\n"
    assert mode(path) == 0o600  # 토큰을 넣을 파일
    lines = path.read_text().splitlines()
    server_lines = [ln for ln in lines if SERVER_LINE_RE.match(ln)]
    assert len(server_lines) == 1, server_lines
    # 끝의 `/` 는 뗀다. 템플릿 줄의 꼬리 주석은 남아도 되고 없어도 된다.
    assert re.fullmatch(r'server = "http://build:8787"\s*(#.*)?', server_lines[0]), server_lines
    # server 줄 말고는 템플릿(examples/client.toml) 그대로 — 주석 · token_env · label
    template_lines = CLIENT_TEMPLATE.decode().splitlines()
    template_rest = [ln for ln in template_lines if not SERVER_LINE_RE.match(ln)]
    assert [ln for ln in lines if not SERVER_LINE_RE.match(ln)] == template_rest
    assert any(ln.startswith('token_env = "RCM_TOKEN"') for ln in lines)
    assert "RCM_TOKEN" in err and "rcm check" in err, err  # export RCM_TOKEN=… → rcm check
    cfg = load_client_config(path, environ={})
    assert cfg.server == "http://build:8787" and cfg.token_env == "RCM_TOKEN" and not cfg.token


@pytest.mark.parametrize(
    "given,expected",
    [
        ("http://build:8787/", "http://build:8787"),
        ("http://127.0.0.1:8787", "http://127.0.0.1:8787"),
        ("https://build.example:8443/", "https://build.example:8443"),
    ],
)
def test_init_client_accepts_http_and_https_and_strips_the_trailing_slash(
    home, capsys, given, expected
):
    code, _, err = run(capsys, ["init", "client", "--server", given])
    assert code == 0, err
    path = home / ".config" / "rcm" / "client.toml"
    assert f'server = "{expected}"' in path.read_text()
    assert load_client_config(path, environ={}).server == expected


def test_init_client_requires_server(home, capsys):
    code, out, err = run(capsys, ["init", "client"])
    assert code == 2 and out == ""
    assert "--server" in err, err
    assert not (home / ".config" / "rcm" / "client.toml").exists()


@pytest.mark.parametrize("url", ["ftp://x", "build:8787", "//build:8787"])
def test_init_client_rejects_urls_without_an_http_scheme(home, capsys, url):
    code, out, err = run(capsys, ["init", "client", "--server", url])
    assert code == 2 and out == "", err
    assert "http://" in err, err  # 사용법을 말한다: http:// 또는 https:// 로 시작해야 한다
    assert not (home / ".config" / "rcm" / "client.toml").exists()


def test_init_client_refuses_to_overwrite_unless_forced(home, capsys):
    path = home / ".config" / "rcm" / "client.toml"
    assert run(capsys, ["init", "client", "--server", "http://a:1"])[0] == 0
    before = path.read_bytes()
    code, out, err = run(capsys, ["init", "client", "--server", "http://b:2"])
    assert code == 2 and out == "", err
    assert "refusing to overwrite" in err and str(path) in err and "--force" in err, err
    assert path.read_bytes() == before
    code, out, err = run(capsys, ["init", "client", "--server", "http://b:2", "--force"])
    assert code == 0, err
    assert out == f"{path}\n" and mode(path) == 0o600
    assert load_client_config(path, environ={}).server == "http://b:2"


def test_init_client_path_option(home, tmp_path, capsys):
    custom = tmp_path / "elsewhere" / "c.toml"
    argv = ["init", "client", "--server", "http://build:8787", "--path", str(custom)]
    code, out, err = run(capsys, argv)
    assert code == 0, err
    assert out == f"{custom}\n" and mode(custom) == 0o600
    assert load_client_config(custom, environ={}).server == "http://build:8787"
    assert not (home / ".config").exists()


# ── init 이 만든 파일을 로더가 찾는다 ────────────────────────────────────────


@pytest.mark.parametrize("use_xdg", [False, True], ids=["home", "xdg"])
def test_init_writes_where_serve_and_check_look(srv, home, monkeypatch, tmp_path, capsys, use_xdg):
    """Codex 리뷰 ④(반영): 로더가 `$XDG_CONFIG_HOME/rcm` 을 먼저 본다.

    init 이 만든 파일을 serve/check 가 못 찾으면 5분 셋업이 거기서 깨진다.
    """
    if use_xdg:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        conf = tmp_path / "xdg" / "rcm"
    else:
        conf = home / ".config" / "rcm"
    # 템플릿의 data_dir(~/.local/share/rcm) 부모를 만들어 두면 데이터 디렉터리 행이 ok 다
    (home / ".local" / "share").mkdir(parents=True)
    assert run(capsys, ["init", "server"])[0] == 0
    assert run(capsys, ["init", "client", "--server", f"http://127.0.0.1:{srv.port}/"])[0] == 0
    # 명시 경로 · RCM_CONFIG · RCM_SERVER 없이 — 로더가 스스로 찾는다
    assert load_server_config(None).path == conf / "server.toml"
    client = load_client_config(None, environ={})
    assert client.path == conf / "client.toml" and client.server == f"http://127.0.0.1:{srv.port}"
    monkeypatch.setenv("RCM_TOKEN", srv.tokens["alice"])
    code, out, err = run(capsys, ["check"])
    assert code == 0, out + err
    assert row_status(out, "server") == "ok" and row_status(out, "token") == "ok", out
    assert row_status(out, "data dir") == "ok", out


# ── rcm version ──────────────────────────────────────────────────────────────

VERSION_RE = re.compile(r"^rcm (\S+) \(Python (\S+), (\S+) (\S+)\)$")


def test_version_prints_python_and_platform_on_one_line(home, capsys):
    code, out, err = run(capsys, ["version"])
    assert code == 0 and err == ""
    assert out.count("\n") == 1 and out.endswith("\n"), out
    assert out.startswith(f"rcm {__version__} (Python "), out
    m = VERSION_RE.match(out.strip())
    assert m, out  # rcm 0.1.0 (Python 3.13.2, darwin arm64)
    assert m.group(1) == __version__
    assert m.group(2) == platform.python_version()
    assert m.group(3) == sys.platform
    assert m.group(4) == platform.machine()


def test_version_json(home, capsys):
    code, out, err = run(capsys, ["version", "--json"])
    assert code == 0, err
    assert out.count("\n") == 1, out
    doc = json.loads(out)
    assert set(doc) == {"version", "python", "platform", "machine", "schema_version"}
    assert doc["version"] == __version__
    assert doc["python"] == platform.python_version()
    assert doc["platform"] == sys.platform
    assert doc["machine"] == platform.machine()
    assert doc["schema_version"] == SCHEMA_VERSION


def test_version_flag_still_prints_the_short_form(home, capsys):
    code, out, _ = run(capsys, ["--version"])  # argparse 의 version action → SystemExit(0)
    assert code == 0 and out.strip() == f"rcm {__version__}"


# ── rcm check: python · git 행 ──────────────────────────────────────────────


def test_check_python_row_is_first_and_ok_on_a_supported_python(srv, env, capsys):
    assert sys.version_info >= (3, 11, 4) and hasattr(tarfile, "data_filter")  # 전제
    env(srv)
    code, out, err = run(capsys, ["check"])
    assert code == 0, out + err
    head = first_line(out)
    assert head.startswith("ok   python"), out  # 항상 첫 행
    assert platform.python_version() in head, head
    assert row_status(out, "server") == "ok" and row_status(out, "token") == "ok"
    assert row_status(out, "git") is None  # 서버 설정이 없으니 git 행도 없다


def test_check_python_row_fails_below_3_11_4(srv, env, capsys, monkeypatch):
    env(srv)
    fake = _VersionInfo(3, 11, 3, "final", 0)
    with monkeypatch.context() as m:
        m.setattr(sys, "version_info", fake)
        m.delattr(tarfile, "data_filter", raising=False)
        code, out, err = run(capsys, ["check"])
    assert code == 1, out + err
    head = first_line(out)
    assert head.startswith("FAIL  python"), out
    assert "3.11.4" in head, head  # tarfile data filter needs Python 3.11.4+
    assert row_status(out, "server") == "ok"  # 나머지 행은 계속 검사한다


@needs_git
def test_check_git_row_is_ok_when_repos_are_configured_and_git_is_found(
    srv, env, server_toml, capsys
):
    env(srv)
    cfg = server_toml(REPO_SERVER_TOML)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err
    assert first_line(out).startswith("ok   python")
    assert row_status(out, "git") == "ok", out
    assert row_status(out, "data dir") == "ok", out


def test_check_git_row_fails_when_repos_are_configured_but_git_is_missing(
    srv, env, server_toml, no_git, capsys
):
    env(srv)
    cfg = server_toml(REPO_SERVER_TOML)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 1, out + err
    assert first_line(out).startswith("ok   python")
    assert row_status(out, "git") == "FAIL", out
    line = next(ln for ln in out.splitlines() if re.match(r"^FAIL  git(?:\s|$)", ln))
    assert "PATH" in line, line
    assert row_status(out, "server") == "ok"  # 서버 쪽 행은 git 과 무관하게 ok


def test_check_has_no_git_row_without_repos(srv, env, server_toml, no_git, capsys):
    env(srv)
    cfg = server_toml(TREE_SERVER_TOML)
    code, out, err = run(capsys, ["check", "--config", str(cfg)])
    assert code == 0, out + err  # repos 가 없으면 git 이 없어도 상관없다
    assert first_line(out).startswith("ok   python")
    assert row_status(out, "git") is None, out
    assert row_status(out, "data dir") == "ok", out
