"""M5o C — server.toml 의 `include = [...]`: 프리셋을 옆 파일에서 읽는다(명세 §4.1 · 결정 121~123).

시나리오는 `docs/m5o-test-scenarios-c.md`. 구현보다 먼저 썼다(test-first).

지키는 약속 셋:
- 들인 파일에는 **`[[presets]]` 만** 있다. 저장소 토큰 · 비밀 · 바인드 주소는 server.toml 에만.
- 들인 파일은 **서버 사용자 소유 · 그룹/남 쓰기 불가**다 — 프리셋은 서버가 실행할 argv 다.
- 검증은 **합친 목록** 위에서 돈다(`checks` 가 파일을 넘나든다).

들인 파일은 시험이 직접 `chmod 0o644` 한다 — umask 002 인 CI 에서 그냥 만든 파일은 그룹 쓰기
가능이라 권한 검사가 엉뚱한 데서 빨개진다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from remote_ci_monitor.config import ConfigError, load_server_config


def preset(name: str, extra: str = "") -> str:
    return f'\n[[presets]]\nname = "{name}"\nargv = ["bash", "{name}.sh"]\n{extra}\n'


def write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def server_toml(tmp_path: Path, include: str | None, body: str = "") -> Path:
    head = f"include = {include}\n" if include is not None else ""
    return write(tmp_path / "server.toml", f'{head}[server]\ndata_dir = "{tmp_path}"\n{body}')


def load(path: Path):
    return load_server_config(path, environ={}, check_tools=False)


def fails(path: Path) -> str:
    with pytest.raises(ConfigError) as e:
        load(path)
    return str(e.value)


# ── C1 베스트 ─────────────────────────────────────────────────────────────────


def test_c1_presets_from_the_included_file_follow_the_ones_in_server_toml(tmp_path):
    names = [f"p{i:02d}" for i in range(22)]
    write(tmp_path / "presets.dolomood.toml", "".join(preset(n) for n in names))
    cfg = load(
        server_toml(tmp_path, '["presets.dolomood.toml"]', preset("local-a") + preset("local-b"))
    )
    assert [p.name for p in cfg.presets] == ["local-a", "local-b", *names]
    assert len(cfg.presets) == 24


def test_c1_two_included_files_keep_the_include_order(tmp_path):
    write(tmp_path / "b.toml", preset("from-b"))
    write(tmp_path / "a.toml", preset("from-a"))
    cfg = load(server_toml(tmp_path, '["b.toml", "a.toml"]', preset("here")))
    assert [p.name for p in cfg.presets] == ["here", "from-b", "from-a"]


def test_c1_the_config_remembers_which_file_each_preset_came_from(tmp_path):
    inc = write(tmp_path / "presets.dolomood.toml", preset("gate"))
    path = server_toml(tmp_path, '["presets.dolomood.toml"]', preset("local"))
    cfg = load(path)
    assert cfg.includes == (inc,)
    assert cfg.preset_sources == {"local": path, "gate": inc}


def test_without_include_nothing_changes(tmp_path):
    path = server_toml(tmp_path, None, preset("gate"))
    cfg = load(path)
    assert [p.name for p in cfg.presets] == ["gate"]
    assert cfg.includes == ()
    assert cfg.preset_sources == {"gate": path}


# ── C2 경로 ───────────────────────────────────────────────────────────────────


def test_c2_a_relative_path_is_relative_to_the_server_toml_folder_not_the_cwd(
    tmp_path, monkeypatch
):
    conf = tmp_path / "conf"
    write(conf / "sub" / "p.toml", preset("gate"))
    path = server_toml(conf, '["sub/p.toml"]')
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    cfg = load(path)
    assert cfg.preset("gate") is not None
    assert cfg.includes == (conf / "sub" / "p.toml",)


def test_c2_an_absolute_path_is_used_as_it_is(tmp_path):
    inc = write(tmp_path / "far" / "away" / "p.toml", preset("gate"))
    cfg = load(server_toml(tmp_path / "conf", f'["{inc}"]'))
    assert cfg.includes == (inc,)
    assert cfg.preset("gate") is not None


def test_c2_tilde_is_the_home_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    inc = write(home / "rcm-presets" / "p.toml", preset("gate"))
    monkeypatch.setenv("HOME", str(home))
    cfg = load(server_toml(tmp_path / "conf", '["~/rcm-presets/p.toml"]'))
    assert cfg.includes == (inc,)


# ── C3 들인 파일에는 프리셋만 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, section",
    [
        ("[server]\nport = 1\n", "server"),
        ('[[repos]]\nname = "r"\nurl = "https://example.invalid/r.git"\n', "repos"),
        ('[[notify]]\nname = "n"\nargv = ["true"]\n', "notify"),
        ('[[probes]]\nname = "x"\nargv = ["true"]\n', "probes"),
        ('include = ["other.toml"]\n', "include"),
        ("[estimate]\nmin_samples = 2\n", "estimate"),
        ('[display]\ntimezone = "UTC"\n', "display"),
    ],
)
def test_c3_an_included_file_may_only_hold_presets(tmp_path, text, section):
    write(tmp_path / "other.toml", preset("other"))
    write(tmp_path / "presets.extra.toml", text + preset("gate"))
    msg = fails(server_toml(tmp_path, '["presets.extra.toml"]'))
    assert "presets.extra.toml" in msg
    assert section in msg
    assert "[[presets]]" in msg


# ── C4 이름 겹침 ──────────────────────────────────────────────────────────────


def test_c4_a_name_defined_in_server_toml_and_an_included_file_names_both(tmp_path):
    write(tmp_path / "presets.dolomood.toml", preset("gate"))
    msg = fails(server_toml(tmp_path, '["presets.dolomood.toml"]', preset("gate")))
    assert msg == ("preset 'gate' is defined in both server.toml and presets.dolomood.toml"), msg


def test_c4_a_name_defined_in_two_included_files_names_both(tmp_path):
    write(tmp_path / "a.toml", preset("gate"))
    write(tmp_path / "b.toml", preset("gate"))
    msg = fails(server_toml(tmp_path, '["a.toml", "b.toml"]'))
    assert "preset 'gate'" in msg and "a.toml" in msg and "b.toml" in msg


def test_c30_two_files_with_the_same_basename_are_told_apart_by_path(tmp_path):
    a = write(tmp_path / "one" / "p.toml", preset("gate"))
    b = write(tmp_path / "two" / "p.toml", preset("gate"))
    msg = fails(server_toml(tmp_path, '["one/p.toml", "two/p.toml"]'))
    assert str(a) in msg and str(b) in msg


def test_a_duplicate_inside_one_included_file_is_still_a_duplicate(tmp_path):
    write(tmp_path / "a.toml", preset("gate") + preset("gate"))
    msg = fails(server_toml(tmp_path, '["a.toml"]'))
    assert "gate" in msg and "duplicate" in msg


# ── C5 · C7 파일 없음 · 깨진 TOML ─────────────────────────────────────────────


def test_c5_a_missing_included_file_refuses_the_config(tmp_path):
    msg = fails(server_toml(tmp_path, '["nope.toml"]'))
    assert "nope.toml" in msg and "not found" in msg


def test_c7_a_broken_included_file_refuses_the_config_and_names_it(tmp_path):
    write(tmp_path / "broken.toml", "this is === not toml")
    msg = fails(server_toml(tmp_path, '["broken.toml"]'))
    assert "broken.toml" in msg and "invalid TOML" in msg


def test_c20_a_bad_preset_in_an_included_file_names_the_file_and_the_preset(tmp_path):
    write(tmp_path / "p.toml", '[[presets]]\nname = "gate"\nargv = []\n')
    msg = fails(server_toml(tmp_path, '["p.toml"]'))
    assert "p.toml" in msg and "preset 'gate'" in msg


def test_presets_in_an_included_file_must_be_an_array_of_tables(tmp_path):
    write(tmp_path / "p.toml", '[presets]\nname = "gate"\n')
    msg = fails(server_toml(tmp_path, '["p.toml"]'))
    assert "p.toml" in msg and "[[presets]]" in msg


# ── C6 권한 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", [0o664, 0o646, 0o666, 0o620])
def test_c6_an_included_file_others_can_write_is_refused(tmp_path, mode):
    inc = write(tmp_path / "p.toml", preset("gate"), mode=mode)
    msg = fails(server_toml(tmp_path, '["p.toml"]'))
    assert str(inc) in msg
    assert "chmod go-w" in msg


@pytest.mark.parametrize("mode", [0o644, 0o600, 0o444, 0o640])
def test_c6_an_included_file_only_its_owner_can_write_is_fine(tmp_path, mode):
    write(tmp_path / "p.toml", preset("gate"), mode=mode)
    assert load(server_toml(tmp_path, '["p.toml"]')).preset("gate") is not None


def test_c6_an_included_file_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    inc = write(tmp_path / "p.toml", preset("gate"))
    real = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real + 1)
    msg = fails(server_toml(tmp_path, '["p.toml"]'))
    assert str(inc) in msg and "owned" in msg


def test_c6_server_toml_itself_is_not_held_to_the_include_rule(tmp_path):
    """오늘의 server.toml 은 그대로 뜬다 — 새 규칙은 들인 파일에만 붙는다(기존 설치를 깨지
    않는다)."""
    path = server_toml(tmp_path, None, preset("gate"))
    os.chmod(path, 0o664)
    assert load(path).preset("gate") is not None


# ── C8 · C17 include 의 모양 ──────────────────────────────────────────────────


def test_c8_include_as_a_single_string_is_refused_with_the_list_spelling(tmp_path):
    write(tmp_path / "p.toml", preset("gate"))
    msg = fails(server_toml(tmp_path, '"p.toml"'))
    assert "include" in msg and "list" in msg


def test_c8_an_empty_include_list_is_the_same_as_none(tmp_path):
    cfg = load(server_toml(tmp_path, "[]", preset("gate")))
    assert [p.name for p in cfg.presets] == ["gate"]
    assert cfg.includes == ()


@pytest.mark.parametrize("value", ['[""]', "[1]", '["  "]'])
def test_c17_an_include_entry_must_be_a_path(tmp_path, value):
    msg = fails(server_toml(tmp_path, value))
    assert "include" in msg


def test_c16_include_written_under_a_section_says_where_it_belongs(tmp_path):
    """TOML 에서 `[server]` 아래의 `include` 는 `[server] include` 다 — 「모르는 키」보다 자리를
    알려 주는 쪽이 고치는 사람에게 맞는 답이다."""
    write(tmp_path / "p.toml", preset("gate"))
    path = write(
        tmp_path / "server.toml",
        f'[server]\ndata_dir = "{tmp_path}"\ninclude = ["p.toml"]\n',
    )
    msg = fails(path)
    assert "include" in msg and "top of the file" in msg


# ── C15 · C19 자기 자신 · 중복 ────────────────────────────────────────────────


def test_c15_including_server_toml_itself_is_refused(tmp_path):
    msg = fails(server_toml(tmp_path, '["server.toml"]', preset("gate")))
    assert "itself" in msg


def test_c19_a_symlink_to_server_toml_is_still_itself(tmp_path):
    path = server_toml(tmp_path, '["self.toml"]', preset("gate"))
    (tmp_path / "self.toml").symlink_to(path)
    msg = fails(path)
    assert "itself" in msg


def test_c15_the_same_file_twice_is_refused_even_spelled_differently(tmp_path):
    write(tmp_path / "a.toml", preset("gate"))
    msg = fails(server_toml(tmp_path, '["a.toml", "./a.toml"]'))
    assert "a.toml" in msg and "twice" in msg


# ── C13 검증은 합친 목록 위에서 ───────────────────────────────────────────────

REPO = '\n[[repos]]\nname = "app"\nurl = "git@example.com:org/app.git"\n'
CHECK = (
    '\n[[presets]]\nname = "sim-check"\nsource_modes = ["git_ref"]\nrepo = "app"\nargv = ["true"]\n'
)


def test_c13_an_included_preset_can_check_with_a_preset_in_server_toml(tmp_path):
    write(tmp_path / "p.toml", preset("scenario-qa", 'checks = ["sim-check"]'))
    cfg = load(server_toml(tmp_path, '["p.toml"]', REPO + CHECK))
    assert cfg.preset("scenario-qa").checks == ("sim-check",)
    assert cfg.preset("sim-check").sample is False  # 점검 프리셋 판정도 합친 목록에서


def test_c13_a_server_toml_preset_can_check_with_an_included_preset(tmp_path):
    write(tmp_path / "p.toml", CHECK)
    cfg = load(server_toml(tmp_path, '["p.toml"]', REPO + preset("qa", 'checks = ["sim-check"]')))
    assert cfg.preset("qa").checks == ("sim-check",)
    assert cfg.preset("sim-check").repo == "app"


def test_c13_an_unknown_check_is_still_refused(tmp_path):
    write(tmp_path / "p.toml", preset("qa", 'checks = ["nope"]'))
    msg = fails(server_toml(tmp_path, '["p.toml"]', REPO))
    assert "nope" in msg


def test_c13_notify_rules_in_server_toml_may_name_included_presets(tmp_path):
    write(tmp_path / "p.toml", preset("gate"))
    rule = '\n[[notify]]\nname = "n"\nargv = ["true"]\npresets = ["gate"]\n'
    assert load(server_toml(tmp_path, '["p.toml"]', rule)).notify[0].presets == frozenset({"gate"})


# ── C18 빈 들인 파일 ──────────────────────────────────────────────────────────


def test_c18_an_empty_included_file_is_fine(tmp_path):
    inc = write(tmp_path / "empty.toml", "# nothing yet\n")
    cfg = load(server_toml(tmp_path, '["empty.toml"]', preset("gate")))
    assert [p.name for p in cfg.presets] == ["gate"]
    assert cfg.includes == (inc,)


# ── C21 출처는 Preset 의 동등성에 끼어들지 않는다 ─────────────────────────────


def test_c21_moving_a_preset_between_files_does_not_change_the_preset(tmp_path):
    before = load(server_toml(tmp_path / "x", None, preset("gate")))
    write(tmp_path / "y" / "p.toml", preset("gate"))
    after = load(server_toml(tmp_path / "y", '["p.toml"]'))
    assert before.preset("gate") == after.preset("gate")
    assert before.preset_sources["gate"] != after.preset_sources["gate"]


# ── 검증 후속(PR #201 리뷰) ───────────────────────────────────────────────────


def test_a_duplicate_inside_one_included_file_names_that_file(tmp_path):
    """한 들인 파일 안의 겹침도 **그 파일 이름**으로 말한다 — 프리셋 24개 중 어디서 겹쳤는지."""
    write(tmp_path / "presets.dolomood.toml", preset("gate") + preset("gate"))
    msg = fails(server_toml(tmp_path, '["presets.dolomood.toml"]', preset("local")))
    assert "presets.dolomood.toml" in msg and "'gate'" in msg and "duplicate" in msg


def test_the_permission_check_reads_the_open_file_not_the_name(tmp_path, monkeypatch):
    """TOCTOU — 이름으로 stat 하고 다시 이름으로 열면 그 사이에 바뀐 파일을 읽는다. 검사는 **연
    fd** 의 `fstat` 여야 한다. 이름 기준 stat 이 「괜찮다」고 거짓말해도 그룹 쓰기 파일은
    거절된다."""
    inc = write(tmp_path / "p.toml", preset("gate"), mode=0o664)
    real_stat = os.stat

    def lying_stat(p, *a, **kw):
        st = real_stat(p, *a, **kw)
        if os.fspath(p) == str(inc):
            fields = list(st)
            fields[0] = 0o100644  # st_mode: 이름으로 물으면 644 라고 답한다
            return os.stat_result(fields)
        return st

    monkeypatch.setattr(os, "stat", lying_stat)
    msg = fails(server_toml(tmp_path, '["p.toml"]'))
    assert "chmod go-w" in msg


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_is_refused_without_hanging(tmp_path):
    """FIFO 를 그냥 열면 쓰는 쪽이 올 때까지 멈춘다 — 기동 · reload 가 멈추면 안 된다."""
    os.mkfifo(tmp_path / "p.toml", 0o600)
    msg = fails(server_toml(tmp_path, '["p.toml"]'))
    assert "regular file" in msg
