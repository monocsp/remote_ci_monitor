"""보관함(M5p) 설정 — `[server]` 키 넷 · 파생 `shelf_dir` · `shelf_enabled` · 금지 위치 ·
프리셋 `keep`.

명세는 docs/m5p-workplan.md §3 · §15.3. 시나리오 A16 · A17 · A47~A51 · A85 · A86 · A94 · A95 · A138.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_ci_monitor.config import ConfigError, load_server_config, parse_preset
from test_worker import make_config

GLOBS = '["build/.golden-update/goldens.tar.gz"]'


def write(tmp_path: Path, server: str = "", preset: str = "") -> Path:
    data = tmp_path / "data"
    text = f'[server]\ndata_dir = "{data}"\n{server}\n{preset}'
    path = tmp_path / "server.toml"
    path.write_text(text)
    return path


def load(tmp_path: Path, server: str = "", preset: str = "", **kw):
    return load_server_config(write(tmp_path, server, preset), environ=kw.pop("environ", {}))


def refused(tmp_path: Path, server: str) -> str:
    """설정 오류의 문장. 「모르는 키」로 거절된 것은 이 키를 아는 거절이 아니다."""
    with pytest.raises(ConfigError) as e:
        load(tmp_path, server)
    msg = str(e.value)
    assert "unknown key" not in msg, msg
    return msg


def keep_preset(extra: str = "", artifacts: str | None = GLOBS) -> str:
    lines = ["[[presets]]", 'name = "golden-update"', 'argv = ["true"]']
    if artifacts is not None:
        lines.append(f"artifacts = {artifacts}")
    return "\n".join(lines) + "\n" + extra + "\n"


# ── A16 기본값 ───────────────────────────────────────────────────────────────


def test_a16_defaults(tmp_path):
    cfg = make_config(tmp_path)
    assert cfg.shelf_dir == cfg.data_dir / "shelf"
    assert cfg.shelf_max_bytes == 53_687_091_200
    assert cfg.shelf_stale_days == 182
    assert cfg.shelf_delete_days == 365
    assert cfg.shelf_enabled is True
    # 같은 값이 `[server]` 구역에도 있다(설정 파일 · 환경 변수로 바뀌는 자리)
    assert cfg.server.shelf_max_bytes == 53_687_091_200


def test_a16_tilde_expands(tmp_path):
    cfg = load(tmp_path, 'shelf_dir = "~/rcm-shelf"')
    assert cfg.shelf_dir == Path("~/rcm-shelf").expanduser()
    assert cfg.shelf_dir.is_absolute()


def test_a16_the_default_follows_data_dir(tmp_path):
    cfg = make_config(tmp_path)
    cfg.server.data_dir = str(tmp_path / "elsewhere")
    assert cfg.shelf_dir == tmp_path / "elsewhere" / "shelf"


def test_a138_the_default_follows_the_environment_data_dir(tmp_path):
    other = tmp_path / "env-data"
    cfg = load(tmp_path, environ={"RCM_SERVER_DATA_DIR": str(other)})
    assert cfg.data_dir == other
    assert cfg.shelf_dir == other / "shelf"


def test_server_keys_from_file(tmp_path):
    other = tmp_path / "other-disk" / "shelf"
    cfg = load(
        tmp_path,
        f'shelf_dir = "{other}"\nshelf_max_bytes = 1000\nshelf_stale_days = 10\n'
        "shelf_delete_days = 20\n",
    )
    assert (cfg.shelf_dir, cfg.shelf_max_bytes, cfg.shelf_stale_days, cfg.shelf_delete_days) == (
        other,
        1000,
        10,
        20,
    )


# ── A47~A50 · A95 · A138 오류 ────────────────────────────────────────────────


@pytest.mark.parametrize(("stale", "delete"), [(182, 182), (365, 100)])
def test_a47_delete_must_be_after_stale(tmp_path, stale, delete):
    assert "shelf_delete_days" in refused(
        tmp_path, f"shelf_stale_days = {stale}\nshelf_delete_days = {delete}\n"
    )


@pytest.mark.parametrize("value", [0, -1])
def test_a48_stale_at_least_one_day(tmp_path, value):
    assert "shelf_stale_days" in refused(tmp_path, f"shelf_stale_days = {value}\n")


def test_a49_negative_limit(tmp_path):
    assert "shelf_max_bytes" in refused(tmp_path, "shelf_max_bytes = -1\n")


@pytest.mark.parametrize(
    "inside", ["artifacts/s", "jobs/s", "workspaces/s", "blobs/s", "mirrors/s", "artifacts"]
)
def test_a50_inside_another_sweepers_folder(tmp_path, inside):
    target = tmp_path / "data" / inside
    assert "shelf_dir" in refused(tmp_path, f'shelf_dir = "{target}"\n')


@pytest.mark.parametrize(
    "server",
    ["shelf_max_bytes = true", 'shelf_stale_days = "abc"', "shelf_dir = 3"],
)
def test_a95_types(tmp_path, server):
    refused(tmp_path, server + "\n")


def test_a138_a_path_that_normalises_into_artifacts(tmp_path):
    target = f"{tmp_path}/data/shelf/../artifacts/x"
    refused(tmp_path, f'shelf_dir = "{target}"\n')


@pytest.mark.parametrize("which", ["same", "parent"])
def test_a138_the_data_dir_itself_or_a_folder_holding_it(tmp_path, which):
    """§15.3 — `data_dir` 과 같거나 `data_dir` 의 상위 폴더면 설정 오류다."""
    target = tmp_path / "data" if which == "same" else tmp_path
    assert "shelf_dir" in refused(tmp_path, f'shelf_dir = "{target}"\n')


def test_a138_a_link_to_artifacts_is_refused(tmp_path):
    (tmp_path / "data" / "artifacts").mkdir(parents=True)
    link = tmp_path / "shelf-link"
    link.symlink_to(tmp_path / "data" / "artifacts")
    refused(tmp_path, f'shelf_dir = "{link}"\n')


def test_a138_a_relative_path_is_read_like_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load(tmp_path, 'shelf_dir = "rel-shelf"\n')
    assert cfg.shelf_dir.resolve() == (tmp_path / "rel-shelf").resolve()


# ── A86 통과 경계 ────────────────────────────────────────────────────────────


def test_a86_boundaries_that_pass(tmp_path):
    cfg = load(
        tmp_path,
        f"shelf_stale_days = 1\nshelf_delete_days = 2\nshelf_max_bytes = 0\n"
        f'shelf_dir = "{tmp_path / "data" / "artifactsX"}"\n',
    )
    assert cfg.shelf_enabled is False
    assert cfg.shelf_stale_days == 1 and cfg.shelf_delete_days == 2


# ── 프리셋 keep (A17 · A51 · A85 · A94) ──────────────────────────────────────


def test_a17_keep_on_a_preset():
    raw = {"name": "g", "argv": ["true"], "artifacts": ["out/*.png"]}
    assert parse_preset({**raw, "keep": True}).keep is True
    assert parse_preset(raw).keep is False
    assert parse_preset({**raw, "keep": False}).keep is False


def test_a17_keep_from_a_file(tmp_path):
    cfg = load(tmp_path, preset=keep_preset("keep = true"))
    assert cfg.preset("golden-update").keep is True


def test_a51_keep_needs_artifacts():
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "g", "argv": ["true"], "keep": True})
    assert "keep needs artifacts" in str(e.value)


def test_a85_keep_with_failure_only_collection_is_allowed():
    p = parse_preset(
        {
            "name": "g",
            "argv": ["true"],
            "artifacts": ["out/*.png"],
            "artifacts_on": "failure",
            "keep": True,
        }
    )
    assert p.keep is True and p.artifacts_on == "failure"


@pytest.mark.parametrize("value", ["yes", 1, "true"])
def test_a94_keep_must_be_a_bool(value):
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "g", "argv": ["true"], "artifacts": ["out/*.png"], "keep": value})
    assert "keep must be true or false" in str(e.value)


def test_keep_is_part_of_the_preset_compare_so_reload_sees_it():
    """reload 는 프리셋을 `!=` 로 비교한다 — `keep` 만 바꿔도 「바뀜」이어야 한다(§3.2)."""
    from remote_ci_monitor.core.reload import changed_keys

    raw = {"name": "g", "argv": ["true"], "artifacts": ["out/*.png"]}
    before, after = parse_preset(raw), parse_preset({**raw, "keep": True})
    assert before != after
    assert changed_keys(before, after) == ["keep"]
