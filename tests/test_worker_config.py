"""`load_worker_config`(M5b-3 §2 `worker.toml`) — 원격 워커 설정의 키 · 우선순위 · 토큰 규칙.

명세는 docs/m5b3-workplan.md §2: `worker.toml`(선택)의 `server · token · pool · lanes ·
data_dir(기본 `~/.local/share/rcm-worker`) · [[repos]](서버 것과 같은 규칙) · [host](서버와 같은
섹션)`, CLI 플래그가 파일보다 우선, 파싱은 `config.py` 의 `load_worker_config(path) → WorkerConfig`.
토큰은 `RCM_WORKER_TOKEN`(우선) 또는 파일의 `token`.

잠그는 모양(구현 전 — test-first):
- `load_worker_config(path: Path | None, *, overrides: dict) -> WorkerConfig`. `overrides` 는 CLI
  플래그(`server · pool · lanes · name · data_dir`), 값이 None 이면 덮어쓰지 않는다(서버 로더와
  같다). `overrides` 에 `token` 은 **올 수 없다**(토큰은 플래그로 받지 않는다 — 셸 히스토리·ps 에
  남는다) → `ValueError`.
- `WorkerConfig`: `server: str`(필수 — 없으면 `ConfigError` 에 'server') · `token: str`(없으면 "")
  · `pool: str`(기본 "default", 이름 규칙) · `lanes: int`(기본 1, 1~64) · `name: str`(선택, 기본
  "" — 워커 이름은 서버가 토큰으로 정하므로 설정은 보관만) · `data_dir: str`(쓴 값 그대로, 기본
  `~/.local/share/rcm-worker`) + `data_path: Path`(`~` 를 푼 것 — 서버의 `ServerConfig.data_dir`
  프로퍼티와 같은 역할) · `repos: tuple[RepoConfig, ...]`(`repo(name) -> RepoConfig | None` 로
  찾는다) · `host: HostSection` · `grace_seconds: int = 10` · `path: Path | None`.
- 모르는 키·섹션은 `ConfigError` 에 그 이름. 파일의 `token` 은 600 이어야 한다(client.toml 과
  같다). 정수 키에 숫자 문자열(`lanes = "2"`)은 다른 로더처럼 받아들인다(환경변수 규칙) — 잠그지
  않는다.
- `load_worker_config` 는 모듈 속성으로 찾는다(`config_mod.load_worker_config`) — 아직 없으면
  수집 오류 대신 시험마다 빨갛다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import config as config_mod
from remote_ci_monitor.config import ConfigError, HostSection, RepoConfig

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

TOKEN_ENV = "RCM_WORKER_TOKEN"
DEFAULT_DATA_DIR = "~/.local/share/rcm-worker"

MINIMAL = 'server = "http://build:8787"\n'

FULL = """
server = "http://build:8787/"
pool = "linux"
lanes = 2
name = "build-02"
data_dir = "{data_dir}"

[host]
interval_seconds = 3
gpu = "off"
top_processes = 3
history_samples = 12

[[repos]]
name = "app"
url = "git@example.com:org/app.git"

[[repos]]
name = "lib"
url = "https://example.com/org/lib.git"
"""


# ── 도우미 ───────────────────────────────────────────────────────────────────


def write(tmp_path: Path, text: str, name: str = "worker.toml", mode: int | None = None) -> Path:
    p = tmp_path / name
    p.write_text(text)
    if mode is not None:
        os.chmod(p, mode)
    return p


def load(path: Path | None, **overrides: Any):
    """`load_worker_config(path, overrides={…})` — 키워드 인자를 overrides 로 넘긴다."""
    return config_mod.load_worker_config(path, overrides=overrides)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """HOME 을 tmp 로, `RCM_WORKER_TOKEN`·`XDG_CONFIG_HOME` 은 없이 — 개발자의 실제 설정·토큰을
    안 본다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    for var in (TOKEN_ENV, "XDG_CONFIG_HOME", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return home


# ── 기본값 · 파일 값 ─────────────────────────────────────────────────────────


def test_minimal_file_gives_defaults(tmp_path, isolated_env):
    """§2: `server` 만 있는 파일 — pool "default" · lanes 1 · name "" · token "" · data_dir 은
    `~/.local/share/rcm-worker` 를 푼 경로 · repos 없음 · host 는 서버와 같은 기본값 · grace 10 ·
    `path` 는 그 파일."""
    p = write(tmp_path, MINIMAL)
    cfg = load(p)
    assert cfg.server == "http://build:8787"
    assert cfg.pool == "default"
    assert cfg.lanes == 1
    assert cfg.name == ""
    assert cfg.token == ""
    assert cfg.data_path == Path(DEFAULT_DATA_DIR).expanduser()
    assert cfg.data_dir == DEFAULT_DATA_DIR  # 파일 값 그대로(표시용)
    assert str(cfg.data_path).startswith(str(isolated_env))  # `~` 는 (격리한) HOME 으로 풀린다
    assert cfg.repos == ()
    assert cfg.host == HostSection()
    assert cfg.grace_seconds == 10
    assert cfg.path == p


@needs_git
def test_full_file_is_read_and_server_trailing_slash_is_stripped(tmp_path):
    """§2: 모든 키를 읽는다 — `[host]` 는 서버의 `HostSection` 그대로, `[[repos]]` 는 `RepoConfig`
    순서 그대로, `data_dir` 는 파일 값(`~` 풀기), server 끝의 `/` 는 뗀다(Client 와 같다)."""
    p = write(tmp_path, FULL.format(data_dir="~/wk"))
    cfg = load(p)
    assert cfg.server == "http://build:8787"
    assert cfg.pool == "linux" and cfg.lanes == 2 and cfg.name == "build-02"
    assert cfg.data_path == Path("~/wk").expanduser()
    assert cfg.host == HostSection(
        interval_seconds=3, gpu="off", top_processes=3, history_samples=12
    )
    assert cfg.repos == (
        RepoConfig(name="app", url="git@example.com:org/app.git"),
        RepoConfig(name="lib", url="https://example.com/org/lib.git"),
    )


def test_no_file_needs_a_server_from_overrides(tmp_path):
    """§2: 파일이 없어도(`path=None`) 플래그만으로 된다 — `server` 는 필수. `path` 는 None."""
    cfg = load(None, server="http://build:8787", pool="linux")
    assert cfg.server == "http://build:8787" and cfg.pool == "linux" and cfg.lanes == 1
    assert cfg.path is None
    assert cfg.data_path == Path(DEFAULT_DATA_DIR).expanduser()


def test_missing_server_is_a_config_error_naming_the_key(tmp_path):
    """§2: `server` 가 파일에도 플래그에도 없으면 `ConfigError` 에 'server'(`rcm worker` 가 usage 2
    로 옮긴다)."""
    with pytest.raises(ConfigError) as e:
        load(None)
    assert "server" in str(e.value)
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, 'pool = "linux"\n'))
    assert "server" in str(e.value)


def test_explicit_missing_path_is_a_config_error(tmp_path):
    """서버·클라이언트 로더와 같다 — 명시한 파일이 없으면 조용히 기본값으로 가지 않고 실패."""
    with pytest.raises(ConfigError) as e:
        load(tmp_path / "missing.toml", server="http://x")
    assert "missing.toml" in str(e.value)


def test_invalid_toml_names_the_file(tmp_path):
    p = write(tmp_path, "server = \n")
    with pytest.raises(ConfigError) as e:
        load(p)
    assert p.name in str(e.value)


# ── 우선순위: 플래그 > 파일 ──────────────────────────────────────────────────


def test_overrides_beat_file_values_and_none_does_not_override(tmp_path):
    """§2 「CLI 플래그가 파일보다 우선」: server · pool · lanes · name · data_dir 모두. None 인
    플래그는 파일 값을 지우지 않는다(서버 로더의 규칙과 같다)."""
    p = write(tmp_path, FULL.format(data_dir="~/wk"))
    cfg = load(
        p,
        server="http://other:1/",
        pool="default",
        lanes=4,
        name="b-two",
        data_dir=str(tmp_path / "d"),
    )
    assert cfg.server == "http://other:1"
    assert cfg.pool == "default" and cfg.lanes == 4 and cfg.name == "b-two"
    assert cfg.data_path == tmp_path / "d"
    kept = load(p, server=None, pool=None, lanes=None, name=None, data_dir=None)
    assert kept.server == "http://build:8787" and kept.pool == "linux" and kept.lanes == 2
    assert kept.name == "build-02" and kept.data_path == Path("~/wk").expanduser()


def test_override_data_dir_expands_tilde(tmp_path, isolated_env):
    cfg = load(None, server="http://x", data_dir="~/elsewhere")
    assert cfg.data_path == isolated_env / "elsewhere"


# ── 토큰: 환경변수 > 파일, 플래그 금지 ───────────────────────────────────────


def test_token_from_env_beats_the_file(tmp_path, monkeypatch):
    """§2: `RCM_WORKER_TOKEN` 이 있으면 파일의 `token` 보다 우선. 파일에만 있으면 파일 것(600
    이어야)."""
    p = write(tmp_path, MINIMAL + 'token = "from-file"\n', mode=0o600)
    assert load(p).token == "from-file"
    monkeypatch.setenv(TOKEN_ENV, "from-env")
    assert load(p).token == "from-env"
    assert load(None, server="http://x").token == "from-env"


def test_token_in_file_requires_600(tmp_path):
    """client.toml 과 같은 규칙 — 토큰을 담은 파일이 남이 읽을 수 있으면 `chmod 600` 을 안내하며
    실패."""
    p = write(tmp_path, MINIMAL + 'token = "abc"\n', mode=0o644)
    with pytest.raises(ConfigError) as e:
        load(p)
    assert "chmod 600" in str(e.value) and str(p) in str(e.value)
    os.chmod(p, 0o600)
    assert load(p).token == "abc"


def test_file_without_a_token_may_be_world_readable(tmp_path):
    """토큰이 없는 worker.toml(서버·풀·레포만)은 644 여도 된다 — 권한 검사는 토큰이 있을 때만."""
    p = write(tmp_path, MINIMAL, mode=0o644)
    assert load(p).token == ""


def test_token_may_not_come_from_overrides():
    """§2: 토큰은 `RCM_WORKER_TOKEN` 또는 파일로만 — `overrides` 에 `token` 이 있으면 `ValueError`
    (`--token` 플래그는 없다: 셸 히스토리와 `ps` 에 남는다)."""
    with pytest.raises(ValueError) as e:
        load(None, server="http://x", token="leaked")
    assert "token" in str(e.value)


# ── 검증: 키 · 타입 · 범위 ───────────────────────────────────────────────────


def test_unknown_key_names_the_key(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, MINIMAL + 'pool_name = "linux"\n'))
    assert "pool_name" in str(e.value)


def test_unknown_section_names_the_section(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, MINIMAL + "[hots]\ninterval_seconds = 5\n"))
    assert "hots" in str(e.value)


def test_unknown_host_key_names_section_and_key(tmp_path):
    """`[host]` 는 서버와 같은 섹션 — 모르는 키는 `[host] unknown key '…'`."""
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, MINIMAL + "[host]\ninterval = 5\n"))
    assert "[host]" in str(e.value) and "interval" in str(e.value)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("lanes = 0\n", "lanes"),
        ("lanes = 65\n", "lanes"),
        ("lanes = true\n", "lanes"),
        ("server = 5\n", "server"),
        ('pool = "bad pool"\n', "pool"),
        ('pool = ""\n', "pool"),
        ("name = 7\n", "name"),
        ("data_dir = 3\n", "data_dir"),
        ("[host]\ninterval_seconds = 1\n", "interval_seconds"),
        ('[host]\ngpu = "maybe"\n', "gpu"),
    ],
)
def test_bad_values_fail_naming_the_key(tmp_path, text, key):
    """§2: `lanes` 1~64 의 정수(bool 아님) · `server`/`name`/`data_dir` 문자열 · `pool` 이름 규칙 ·
    `[host]` 는 서버의 검증(interval ≥ 2 · gpu auto|off) — 어긋나면 `ConfigError` 에 키 이름."""
    body = text if text.startswith("server") else MINIMAL + text
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, body))
    assert key in str(e.value), str(e.value)


@pytest.mark.parametrize("lanes", [0, 65, True])
def test_bad_lanes_from_overrides_fail_too(lanes):
    """플래그로 온 `lanes` 도 같은 범위·타입 검사를 받는다(문자열은 argparse 가 먼저 거른다)."""
    with pytest.raises(ConfigError) as e:
        load(None, server="http://x", lanes=lanes)
    assert "lanes" in str(e.value)


def test_lanes_bounds_are_inclusive(tmp_path):
    assert load(write(tmp_path, MINIMAL + "lanes = 1\n")).lanes == 1
    assert load(write(tmp_path, MINIMAL + "lanes = 64\n")).lanes == 64


# ── [[repos]] — 서버와 같은 규칙 ─────────────────────────────────────────────


def test_repos_need_exactly_name_and_url(tmp_path):
    for bad in (
        '[[repos]]\nname = "app"\n',
        '[[repos]]\nurl = "git@example.com:org/app.git"\n',
        '[[repos]]\nname = "app"\nurl = "git@example.com:org/app.git"\nbranch = "main"\n',
    ):
        with pytest.raises(ConfigError) as e:
            load(write(tmp_path, MINIMAL + bad))
        assert "repos" in str(e.value), str(e.value)


def test_repos_reject_duplicate_names_and_bad_urls(tmp_path):
    """§2 「`[[repos]]` 는 서버 것과 같은 규칙」— 서버 `_validate_server` 처럼 이름 중복 · 이름
    규칙 · `validate_repo_url`(`-` 로 시작하는 URL 은 git 에 옵션으로 들어간다) 을 시작 시 거른다.
    워커는 이 URL 을 그대로 `git fetch` 에 넘기므로 서버와 같은 문지기가 있어야 한다."""
    dup = (
        '[[repos]]\nname = "app"\nurl = "git@example.com:org/app.git"\n'
        '[[repos]]\nname = "app"\nurl = "https://example.com/org/lib.git"\n'
    )
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, MINIMAL + dup))
    assert "duplicate" in str(e.value) and "app" in str(e.value)
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, MINIMAL + '[[repos]]\nname = "app"\nurl = "-oProxyCommand=x"\n'))
    assert "app" in str(e.value)
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, MINIMAL + '[[repos]]\nname = "bad name"\nurl = "https://x/y.git"\n'))
    assert "repos" in str(e.value)


@needs_git
def test_repo_lookup_by_name(tmp_path):
    """워커가 git_ref 잡의 `repo` 이름으로 URL 을 찾는다 — 없으면 None(→ finish failed
    `repo 'app' is not configured on this worker`)."""
    p = write(tmp_path, FULL.format(data_dir="~/wk"))
    cfg = load(p)
    assert cfg.repo("app") == RepoConfig(name="app", url="git@example.com:org/app.git")
    assert cfg.repo("nope") is None
    assert load(write(tmp_path, MINIMAL)).repo("app") is None
