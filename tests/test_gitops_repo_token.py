"""저장소별 자격(`[[repos]] token_file`) — git 자격을 서버 전역 gh 활성 계정에서 떼어 낸다.

2026-10-14 11:19 빌드 머신의 gh 활성 계정이 다른 계정으로 바뀌었고, github.com 의 git 자격은
`gh auth git-credential`(활성 계정의 토큰)에서만 왔다. 그 계정은 private 저장소를 못 봐서 GitHub 가
404 를 주었고 `git ls-remote` 가 128 로 끝나 **git_ref 제출이 큐에 들어가기 전에 전부 거부**됐다.
같은 머신의 커밋 status 올리기는 토큰 **파일**을 써서 멀쩡했다 — 두 경로가 다른 신원을 썼다.

여기 테스트는 그 사고를 오프라인으로 재현한다: 전역 설정에 「틀린 계정」 토큰을 내주는 헬퍼를 두고,
진짜 `git credential fill` 이 저장소 토큰 파일을 고르는지 본다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gitrepo import isolate_git_env
from remote_ci_monitor import gitops
from remote_ci_monitor.config import ConfigError, load_server_config
from remote_ci_monitor.gitops import GitError, git_env, resolve_ref

WRONG = "WRONG-ACCOUNT-TOKEN"  # 전역 헬퍼(=gh 활성 계정)가 내주는 값
RIGHT = "RIGHT-REPO-TOKEN"  # 저장소 토큰 파일의 값

# 운영 머신의 실제 모양 그대로: 빈 값으로 앞선 헬퍼를 비우고 URL 범위 헬퍼 하나
_WRONG_HELPER = (
    f'!f() {{ test \\"$1\\" = get || exit 0; echo username=wrong; echo password={WRONG}; }}; f'
)
_GLOBAL_GITCONFIG = f"""\
[credential "https://github.com"]
\thelper =
\thelper = "{_WRONG_HELPER}"
"""


def _home_with_wrong_global_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_env(tmp_path, monkeypatch)
    (tmp_path / "home" / ".gitconfig").write_text(_GLOBAL_GITCONFIG)


def _token_file(tmp_path: Path, value: str = RIGHT) -> Path:
    p = tmp_path / "tokens" / "repo.token"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value + "\n")
    p.chmod(0o600)
    return p


def _fill(env: dict[str, str]) -> dict[str, str]:
    """진짜 git 의 자격 해석을 그대로 돌린다. 시스템 설정(osxkeychain 등)은 끈다 — 개발자의
    실제 키체인을 건드리지 않게."""
    env = {**env, "GIT_CONFIG_NOSYSTEM": "1"}
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\npath=fmmc-tech/dolomood-app-renew.git\n\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return out


# ── 사고 재현 — 진짜 git 의 자격 우선순위 ─────────────────────────────────────


def test_control_without_a_token_file_git_takes_the_global_helper(tmp_path, monkeypatch):
    # 대조군: 픽스처가 사고를 정말 재현하는지.
    # 이게 RIGHT 를 내면 아래 테스트는 아무것도 증명하지 못한다.
    _home_with_wrong_global_helper(tmp_path, monkeypatch)
    assert _fill(git_env()).get("password") == WRONG


def test_a_token_file_wins_over_the_global_active_account(tmp_path, monkeypatch):
    _home_with_wrong_global_helper(tmp_path, monkeypatch)
    got = _fill(git_env(credential=_token_file(tmp_path)))
    assert got.get("password") == RIGHT
    assert got.get("username")  # GitHub 는 토큰이면 아무 이름이나 받는다 — 비어 있지만 않으면 된다


def test_a_token_file_without_a_trailing_newline_still_works(tmp_path, monkeypatch):
    _home_with_wrong_global_helper(tmp_path, monkeypatch)
    p = _token_file(tmp_path)
    p.write_text(RIGHT)  # 줄바꿈 없음
    assert _fill(git_env(credential=p)).get("password") == RIGHT


def test_a_token_file_path_with_spaces_and_quotes_is_read(tmp_path, monkeypatch):
    # 경로는 셸 헬퍼 안에 들어간다 — 인용이 깨지면 다른 파일을 읽거나 명령이 된다
    _home_with_wrong_global_helper(tmp_path, monkeypatch)
    d = tmp_path / "it's a dir"
    d.mkdir()
    p = d / "tok en"
    p.write_text(RIGHT + "\n")
    assert _fill(git_env(credential=p)).get("password") == RIGHT


# ── 비밀은 rcm 의 손을 거치지 않는다 ───────────────────────────────────────────


def test_the_token_never_appears_in_the_env_rcm_builds(tmp_path):
    p = _token_file(tmp_path, value="SENTINEL-SECRET-1234")
    env = git_env(credential=p)
    blob = json.dumps(env)
    assert "SENTINEL-SECRET-1234" not in blob  # 값은 git 이 헬퍼를 부를 때 파일에서 읽는다
    assert str(p) in blob  # 넘기는 건 경로뿐


def test_without_a_token_file_the_env_is_exactly_what_it_was(tmp_path):
    # 기존 저장소의 동작은 그대로 — token_file 이 없으면 아무 키도 안 붙는다
    assert not any(k.startswith("GIT_CONFIG_") for k in git_env())


# ── 파일이 없거나 못 읽으면 git 을 부르지도 않는다 ─────────────────────────────


def test_an_unreadable_token_file_is_refused_before_git_runs(tmp_path):
    missing = tmp_path / "nope" / "repo.token"

    def must_not_run(*a, **kw):
        raise AssertionError("git must not run without a readable credential")

    with pytest.raises(GitError) as ei:
        resolve_ref(
            "https://github.com/org/app.git",
            "main",
            timeout=5,
            run=must_not_run,
            credential=missing,
        )
    assert ei.value.kind == "no_credential"
    assert str(missing) not in str(ei.value)  # 잡 요약에 실리는 문구 — 경로를 싣지 않는다


# ── 네트워크에 나가는 호출마다 자격이 실린다 ───────────────────────────────────


@pytest.fixture
def captured_envs(monkeypatch):
    """`_run_git` 이 받은 자격으로 **진짜** `git_env` 를 만들어 모은다. 원격에는 안 나간다.

    `_run_process` 를 바꾸면 안 된다 — `_run_git` 이 그걸 기본 인자로 묶어 둬서 진짜 git 이 돈다.
    """
    envs: list[dict[str, str]] = []

    def spy(argv, **kw):
        envs.append(gitops.git_env(credential=kw.get("credential")))
        return subprocess.CompletedProcess(["git", *argv], 0, "", "")

    monkeypatch.setattr(gitops, "_run_git", spy)
    return envs


def test_every_fetch_carries_the_credential(tmp_path, captured_envs):
    # 미러 만들기(`init --bare`·`config`)는 로컬이라 자격이 필요 없다 — 원격에 나가는 것만 본다
    p = _token_file(tmp_path)
    url = "https://github.com/org/app.git"
    mirror = tmp_path / "mirror"
    gitops.fetch_all(mirror, url, timeout=5, credential=p)
    gitops.fetch_ref(mirror, url, "main", timeout=5, credential=p)
    assert len(captured_envs) >= 2, "no git call was made"
    for env in captured_envs:
        # 전체 fetch · ref 하나 fetch 어느 것도 빠지지 않는다
        assert env.get("GIT_CONFIG_COUNT"), env


def test_a_fetch_without_a_credential_adds_nothing(tmp_path, captured_envs):
    gitops.fetch_all(tmp_path / "mirror", "https://github.com/org/app.git", timeout=5)
    assert captured_envs and not any("GIT_CONFIG_COUNT" in env for env in captured_envs)


# ── 설정 ─────────────────────────────────────────────────────────────────────


def _load(tmp_path: Path, repo_block: str):
    cfg = tmp_path / "server.toml"
    cfg.write_text(f'[server]\ndata_dir = "{tmp_path / "data"}"\n\n{repo_block}\n')
    return load_server_config(cfg, check_tools=False)


def test_token_file_is_read_and_tilde_is_expanded(tmp_path):
    cfg = _load(
        tmp_path,
        '[[repos]]\nname = "app"\nurl = "https://github.com/org/app.git"\n'
        'token_file = "~/.config/rcm/tokens/app.token"\n',
    )
    repo = cfg.repo("app")
    assert repo.token_file == "~/.config/rcm/tokens/app.token"
    assert repo.credential == Path("~/.config/rcm/tokens/app.token").expanduser()


def test_a_repo_without_token_file_has_no_credential(tmp_path):
    cfg = _load(tmp_path, '[[repos]]\nname = "app"\nurl = "https://github.com/org/app.git"\n')
    assert cfg.repo("app").credential is None


@pytest.mark.parametrize("url", ["git@github.com:org/app.git", "ssh://git@github.com/org/app.git"])
def test_token_file_on_an_ssh_url_is_refused(tmp_path, url):
    # 토큰은 https 자격이다. ssh 에 붙이면 조용히 무시돼 「설정했는데 안 먹는」 꼴이 된다
    with pytest.raises(ConfigError, match="token_file"):
        _load(tmp_path, f'[[repos]]\nname = "app"\nurl = "{url}"\ntoken_file = "/x/app.token"\n')


@pytest.mark.parametrize("value", ["1", '""', "true"])
def test_token_file_must_be_a_non_empty_string(tmp_path, value):
    with pytest.raises(ConfigError, match="token_file"):
        _load(
            tmp_path,
            '[[repos]]\nname = "app"\nurl = "https://github.com/org/app.git"\n'
            f"token_file = {value}\n",
        )


def test_an_unknown_key_is_still_refused(tmp_path):
    with pytest.raises(ConfigError, match="tokenfile"):
        _load(
            tmp_path,
            '[[repos]]\nname = "app"\nurl = "https://github.com/org/app.git"\ntokenfile = "/x"\n',
        )
