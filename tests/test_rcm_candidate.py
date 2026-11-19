"""`rcm_candidate.py`(rcm-store-connect 템플릿)가 server.toml 의 `include` 를 따른다.

rcm 0.6.0 부터 프리셋은 `include = [...]` 로 들인 파일에 둘 수 있다(docs/configuration.md
「Presets in their own file: `include`」). 붙여 넣기만 아는 후보 도구는 두 가지로 깨졌다
(docs/m5o-workplan.md §10.3 C-10):
- 프리셋을 server.toml 끝에 덧붙여 「defined in both」 후보를 만든다.
- 후보를 임시 폴더에 써서 상대 경로 include 가 「included file not found」 로 풀린다.

잠그는 것:
- include 없는 server.toml 은 **오늘과 바이트 동일**하다 — 골든은 고치기 전 템플릿이 만든 것이다
  (`tests/fixtures/rcm_candidate/golden.*`).
- include 가 있으면 후보 폴더에 `server.toml` + 들인 파일을 쓰고, **실제 로더**
  (`load_server_config`)와 **실제 `rcm check`** 가 그 후보를 받아들인다. 손으로 만든 기대값만
  비교하지 않는다.
- 이름 중복 · 자리 틀린 include · 없는 들인 파일은 이름을 대고 실패한다.

시험은 템플릿을 `python3 <template>` 로 tmp_path 안에서 부른다(cwd 의 docs/rcm-connect.md 를
읽지 않게). 들인 파일은 시험이 `chmod 0o644` 한다 — umask 002 인 CI 에서 권한 검사가 엉뚱한
데서 빨개지지 않게.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from remote_ci_monitor.config import load_server_config

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src/remote_ci_monitor/skills/rcm-store-connect/templates/rcm_candidate.py"
FIX = ROOT / "tests/fixtures/rcm_candidate"
#: 레포 `.venv` 의 진짜 rcm — 후보를 실제 `rcm check` 로 확인한다
REAL_RCM = Path(sys.executable).parent / "rcm"


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full = {**os.environ, **(env or {})}
    full.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(TEMPLATE), *args],
        cwd=cwd,
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def preset(name: str, extra: str = "") -> str:
    return f'\n[[presets]]\nname = "{name}"\nargv = ["bash", "{name}.sh"]\n{extra}'


def names(path: Path) -> list[str]:
    return [p["name"] for p in tomllib.loads(path.read_text()).get("presets", [])]


def load(path: Path):
    return load_server_config(path, environ={}, check_tools=False)


def live_server(tmp_path: Path, include: str, body: str = "") -> Path:
    """include 를 맨 위에 둔 운영 흉내 server.toml. data_dir 은 tmp_path(운영 아님)."""
    return write(
        tmp_path / "live/server.toml",
        f"# owner's server.toml\ninclude = {include}   # presets live next door\n\n"
        f'[server]\ndata_dir = "{tmp_path / "data"}"\n\n'
        '[[repos]]\nname = "app"\nurl = "git@example.invalid:o/app.git"\n'
        f"{body}",
    )


def fake_rcm(tmp_path: Path, version: str = "rcm 0.6.1") -> Path:
    """`--version` 은 version 을, 그 밖엔 고정 행을 찍고 부른 argv 를 calls.txt 에 남긴다."""
    calls = tmp_path / "calls.txt"
    p = tmp_path / "bin/rcm"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f'if [ "$1" = "--version" ]; then echo "{version}"; exit 0; fi\n'
        "printf 'ok   python        3.11\\n"
        "FAIL  server        cannot reach http://127.0.0.1:9\\n"
        "FAIL  token         no token\\n"
        "ok   server config %s\\n"
        'warn  release app   plan=release-plan · secrets dir does not exist yet\\n\' "$3"\n'
        "exit 1\n",
        encoding="utf-8",
    )
    p.chmod(0o755)
    return p


# ── 회귀: include 없는 server.toml 은 오늘과 바이트 동일 ──────────────────────────


@pytest.mark.parametrize(
    "case,args",
    [
        ("full", ["--repo", "app", "--profile", "{FIX}/profile.release.toml",
                  "--presets", "{FIX}/presets.release.toml"]),
        ("new-repo", ["--repo", "new-app", "--url", "git@example.invalid:o/new-app.git",
                      "--profile", "profile.new-app.toml", "--presets", "-"]),
        ("presets-only", ["--repo", "app", "--profile", "-",
                          "--presets", "{FIX}/presets.release.toml"]),
        ("empty-server", ["--repo", "app", "--profile", "-",
                          "--presets", "{FIX}/presets.release.toml"]),
    ],
)  # fmt: skip
def test_no_include_output_is_byte_identical_to_the_golden(tmp_path, case, args):
    (tmp_path / "profile.new-app.toml").write_text(
        (FIX / "profile.release.toml").read_text().replace("repos.app.", "repos.new-app.")
    )
    (tmp_path / "empty.toml").write_text("")
    server = "empty.toml" if case == "empty-server" else str(FIX / "server.toml")
    out = tmp_path / "c.toml"
    argv = ["--server", server, *[a.replace("{FIX}", str(FIX)) for a in args], "--out", str(out)]
    r = run(*argv, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"{out}\n"
    assert r.stderr == ""
    assert out.read_bytes() == (FIX / f"golden.{case}.toml").read_bytes()


def test_no_include_without_out_writes_one_file_in_a_fresh_temp_dir(tmp_path):
    r = run(
        "--server", str(FIX / "server.toml"), "--repo", "app",
        "--profile", str(FIX / "profile.release.toml"),
        "--presets", str(FIX / "presets.release.toml"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    out = Path(r.stdout.strip())
    assert out.name == "server.toml" and out.parent.name.startswith("rcm-candidate-")
    assert sorted(os.listdir(out.parent)) == ["server.toml"]
    assert out.read_bytes() == (FIX / "golden.full.toml").read_bytes()


def test_no_include_check_output_is_byte_identical_and_has_no_version_gate(tmp_path):
    """include 가 없으면 옛 rcm 이어도 막지 않는다 — 버전 문턱은 include 모드에서만."""
    for version in ("rcm 0.6.1", "rcm 0.5.0"):
        rcm = fake_rcm(tmp_path, version)
        out = tmp_path / "c.toml"
        r = run(
            "--server", str(FIX / "server.toml"), "--repo", "app",
            "--profile", str(FIX / "profile.release.toml"),
            "--presets", str(FIX / "presets.release.toml"),
            "--out", str(out), "--check",
            cwd=tmp_path, env={"RCM": str(rcm)},
        )  # fmt: skip
        want = (FIX / "golden.check.txt").read_text().replace("{OUT}", str(out))
        want = want.replace("{RCM}", str(rcm)).replace("rcm 0.6.1", version)
        assert r.returncode == 0, r.stderr
        assert r.stdout == want
        assert r.stderr == ""


def test_selftest_passes(tmp_path):
    r = run("--selftest", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rcm_candidate selftest ok" in r.stdout
    assert not [x for x in r.stdout.splitlines() if x.startswith("FAIL ")]


# ── 베스트: 들인 파일과 이름이 같은 프리셋 파일 → 후보 두 파일 ───────────────────


def _best(tmp_path: Path) -> tuple[Path, Path, Path]:
    """include 셋(대상 · 하위 폴더 상대 · 절대). 대상엔 주석과 기존 프리셋이 있다."""
    live = tmp_path / "live"
    write(
        live / "presets.release.toml",
        "# live presets — owner's comment, kept\n\n"
        + preset("gate-x")
        + "\n\n# a comment between presets, kept\n"
        + preset("release-plan", '[[presets.inputs]]\nname = "build_name"\n'),
    )
    write(live / "sub/other.toml", "# other\n" + preset("other-a"))
    abs_other = write(tmp_path / "elsewhere/abs.toml", preset("abs-a"))
    server = live_server(
        tmp_path,
        f'["presets.release.toml", "sub/other.toml", "{abs_other}"]',
        preset("local-a"),
    )
    return live, server, abs_other


def test_include_target_by_basename_writes_two_files_the_real_loader_accepts(tmp_path):
    live, server, abs_other = _best(tmp_path)
    out_dir = tmp_path / "cand"
    r = run(
        "--server", str(server), "--repo", "app",
        "--profile", str(FIX / "profile.release.toml"),
        "--presets", str(FIX / "presets.release.toml"),
        "--out", str(out_dir),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    cand = out_dir / "server.toml"
    inc = out_dir / "presets.release.toml"
    assert r.stdout == f"{cand}\n"
    assert sorted(os.listdir(out_dir)) == ["presets.release.toml", "server.toml"]

    # server.toml: 프로필은 여기, 들어온 프리셋은 없다
    raw = tomllib.loads(cand.read_text())
    assert [p["name"] for p in raw["presets"]] == ["local-a"]
    assert raw["repos"][0]["app"]["release"]["presets"]["plan"] == "release-plan"
    # include: 대상은 그 basename 그대로(줄이 안 바뀐 항목), 상대 경로는 절대 경로로, 절대는 그대로
    assert raw["include"] == [
        "presets.release.toml",
        str(live / "sub/other.toml"),
        str(abs_other),
    ]
    assert "# presets live next door" in cand.read_text()

    # 들인 파일: 주석 · 빈 줄 · 기존 프리셋 보존, 같은 이름은 갈아끼움, 새 것은 덧붙임
    text = inc.read_text()
    assert text.startswith("# live presets — owner's comment, kept\n\n")
    assert "# a comment between presets, kept" in text
    assert "generated by rcm-store-connect" not in text  # 들어온 파일의 서문은 버린다
    assert names(inc) == [
        "gate-x",
        "release-plan",
        "release-upload",
        "release-review",
        "release-version",
    ]
    assert 'argv = ["bash", "release-plan.sh"]' not in text
    assert os.stat(inc).st_mode & 0o022 == 0

    # 실제 로더가 받아들이고, 프리셋의 출처가 후보 들인 파일이다
    cfg = load(cand)
    assert [p.name for p in cfg.presets] == [
        "local-a",
        "gate-x",
        "release-plan",
        "release-upload",
        "release-review",
        "release-version",
        "other-a",
        "abs-a",
    ]
    assert cfg.preset_sources["release-plan"] == inc
    assert cfg.preset_sources["other-a"] == live / "sub/other.toml"
    assert cfg.repos[0].release is not None

    # 다음 단계 안내: 두 파일을 옮기고 reload
    assert f"cp {inc} {live / 'presets.release.toml'}" in r.stderr
    assert f"cp {cand} {server}" in r.stderr
    assert "rcm reload --dry-run" in r.stderr and "kickstart" not in r.stderr


@pytest.mark.skipif(not REAL_RCM.exists(), reason="no rcm next to this python")
def test_include_candidate_passes_the_real_rcm_check(tmp_path):
    live, server, _ = _best(tmp_path)
    (tmp_path / "data").mkdir()
    r = run(
        "--server", str(server), "--repo", "app",
        "--profile", str(FIX / "profile.release.toml"),
        "--presets", str(FIX / "presets.release.toml"),
        "--check",
        cwd=tmp_path, env={"RCM": str(REAL_RCM)},
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert "release app" in r.stdout
    assert "local presets 8" in r.stdout
    assert "presets.release.toml" in r.stdout and "other.toml" in r.stdout
    cand = Path(r.stdout.splitlines()[0])
    assert sorted(os.listdir(cand.parent)) == ["presets.release.toml", "server.toml"]
    assert "rcm reload --dry-run" in r.stderr


def test_into_names_the_target_when_the_basenames_differ(tmp_path):
    write(tmp_path / "live/presets.dolomood.toml", "# dolomood\n" + preset("gate"))
    server = live_server(tmp_path, '["presets.dolomood.toml"]')
    out_dir = tmp_path / "cand"
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"),
        "--into", "presets.dolomood.toml", "--out", str(out_dir),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert sorted(os.listdir(out_dir)) == ["presets.dolomood.toml", "server.toml"]
    assert names(out_dir / "presets.dolomood.toml")[:2] == ["gate", "release-plan"]
    assert "presets" not in tomllib.loads((out_dir / "server.toml").read_text())
    # 프로필이 안 바뀌었으면 server.toml 은 옮길 것이 없다
    assert "cp " + str(out_dir / "server.toml") not in r.stderr
    assert load(out_dir / "server.toml").preset_sources["gate"] == out_dir / (
        "presets.dolomood.toml"
    )


def test_into_not_listed_in_include_fails_with_the_list(tmp_path):
    write(tmp_path / "live/presets.dolomood.toml", preset("gate"))
    server = live_server(tmp_path, '["presets.dolomood.toml"]')
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"), "--into", "nope.toml",
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert "nope.toml" in r.stderr and "presets.dolomood.toml" in r.stderr
    assert r.stdout == ""


def test_include_without_a_match_appends_to_server_toml_and_absolutizes(tmp_path):
    write(tmp_path / "live/presets.other.toml", preset("gate"))
    server = live_server(tmp_path, '["presets.other.toml"]')
    out_dir = tmp_path / "cand"
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"), "--out", str(out_dir),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert os.listdir(out_dir) == ["server.toml"]
    raw = tomllib.loads((out_dir / "server.toml").read_text())
    assert raw["include"] == [str(tmp_path / "live/presets.other.toml")]
    assert [p["name"] for p in raw["presets"]][0] == "release-plan"
    assert "--into" in r.stderr  # 붙여 넣었다고, 고르려면 --into 라고 알린다
    cfg = load(out_dir / "server.toml")
    assert "gate" in {p.name for p in cfg.presets}


def test_tilde_and_absolute_entries_stay_and_a_far_target_points_at_its_copy(tmp_path):
    home = tmp_path / "home"
    write(home / "rcm/tilde.toml", preset("tilde-a"))
    far = write(tmp_path / "repo/scripts/rcm/presets.release.toml", "# far\n" + preset("gate"))
    server = live_server(tmp_path, f'["~/rcm/tilde.toml", "{far}"]')
    out_dir = tmp_path / "cand"
    r = run(
        "--server", str(server), "--repo", "app",
        "--profile", str(FIX / "profile.release.toml"),
        "--presets", str(FIX / "presets.release.toml"), "--out", str(out_dir),
        cwd=tmp_path, env={"HOME": str(home)},
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    raw = tomllib.loads((out_dir / "server.toml").read_text())
    assert raw["include"] == ["~/rcm/tilde.toml", "presets.release.toml"]
    assert names(out_dir / "presets.release.toml")[0] == "gate"
    # 대상 항목을 후보 쪽으로 돌렸으니 server.toml 통째 복사는 권하지 않는다
    assert f"cp {out_dir / 'server.toml'} {server}" not in r.stderr
    assert "[repos.app.release]" in r.stderr
    assert f"cp {out_dir / 'presets.release.toml'} {far}" in r.stderr
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        cfg = load(out_dir / "server.toml")
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
    assert cfg.preset_sources["tilde-a"] == home / "rcm/tilde.toml"


def test_missing_target_is_created_from_the_presets_file(tmp_path):
    """옮기는 중 — include 줄은 넣었고 파일은 아직 없다. 프리셋 파일이 그대로 들인 파일이 된다."""
    server = live_server(tmp_path, '["presets.release.toml"]')
    out_dir = tmp_path / "cand"
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"), "--out", str(out_dir),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (out_dir / "presets.release.toml").read_text() == (
        FIX / "presets.release.toml"
    ).read_text()
    assert "does not exist yet" in r.stderr
    load(out_dir / "server.toml")


def test_without_out_both_files_land_in_a_fresh_temp_dir(tmp_path):
    _, server, _ = _best(tmp_path)
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    cand = Path(r.stdout.strip())
    assert cand.name == "server.toml" and cand.parent.name.startswith("rcm-candidate-")
    assert sorted(os.listdir(cand.parent)) == ["presets.release.toml", "server.toml"]
    load(cand)


# ── 에러 ──────────────────────────────────────────────────────────────────────


def test_same_name_in_server_toml_and_the_target_fails_with_name_and_files(tmp_path):
    write(tmp_path / "live/presets.release.toml", preset("gate"))
    server = live_server(tmp_path, '["presets.release.toml"]', preset("release-plan"))
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"), "--out", str(tmp_path / "cand"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert "preset 'release-plan' is defined in both server.toml and presets.release.toml" in (
        r.stderr
    )
    assert not (tmp_path / "cand").exists()


def test_same_name_in_another_included_file_fails_with_name_and_files(tmp_path):
    write(tmp_path / "live/presets.release.toml", preset("gate"))
    write(tmp_path / "live/team.toml", preset("release-upload"))
    server = live_server(tmp_path, '["presets.release.toml", "team.toml"]')
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert "preset 'release-upload' is defined in both presets.release.toml and team.toml" in (
        r.stderr
    )


def test_pasting_into_server_toml_also_checks_the_included_files(tmp_path):
    write(tmp_path / "live/team.toml", preset("release-plan"))
    server = live_server(tmp_path, '["team.toml"]')
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert "preset 'release-plan' is defined in both server.toml and team.toml" in r.stderr


def test_include_after_the_first_section_fails_like_rcm(tmp_path):
    server = write(
        tmp_path / "live/server.toml",
        f'[server]\ndata_dir = "{tmp_path}"\ninclude = ["presets.release.toml"]\n',
    )
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert "include must be at the top of the file, before the first [section]" in r.stderr
    assert r.stdout == ""


def test_a_missing_other_included_file_fails_with_its_path(tmp_path):
    write(tmp_path / "live/presets.release.toml", preset("gate"))
    server = live_server(tmp_path, '["presets.release.toml", "gone.toml"]')
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert f"included file not found: {tmp_path / 'live/gone.toml'}" in r.stderr


def test_two_includes_with_the_presets_basename_need_into(tmp_path):
    write(tmp_path / "live/a/presets.release.toml", preset("a"))
    write(tmp_path / "live/b/presets.release.toml", preset("b"))
    server = live_server(tmp_path, '["a/presets.release.toml", "b/presets.release.toml"]')
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert "--into" in r.stderr


@pytest.mark.parametrize("bad", ["file", "toml", "live"])
def test_out_with_include_is_a_folder_and_never_the_live_one(tmp_path, bad):
    live, server, _ = _best(tmp_path)
    out = {
        "file": write(tmp_path / "exists.txt", "x"),
        "toml": tmp_path / "cand.toml",
        "live": live,
    }[bad]
    before = {p: p.read_bytes() for p in live.rglob("*") if p.is_file()}
    r = run(
        "--server", str(server), "--repo", "app", "--profile", "-",
        "--presets", str(FIX / "presets.release.toml"), "--out", str(out),
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 1
    assert "--out" in r.stderr
    assert {p: p.read_bytes() for p in live.rglob("*") if p.is_file()} == before


# ── --check: include 를 모르는 rcm ────────────────────────────────────────────


@pytest.mark.parametrize(
    "version,gated", [("rcm 0.5.2", True), ("rcm 0.6.0", False), ("rcm (unknown)", False)]
)
def test_check_refuses_an_rcm_older_than_0_6_0_when_include_is_used(tmp_path, version, gated):
    _, server, _ = _best(tmp_path)
    rcm = fake_rcm(tmp_path, version)
    r = run(
        "--server", str(server), "--repo", "app",
        "--profile", str(FIX / "profile.release.toml"),
        "--presets", str(FIX / "presets.release.toml"), "--check",
        cwd=tmp_path, env={"RCM": str(rcm)},
    )  # fmt: skip
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    if gated:
        assert r.returncode == 1
        assert "does not know include" in r.stderr and "0.6.0" in r.stderr
        assert calls == ["--version"]
        assert "rcm reload" not in r.stderr
    else:
        assert r.returncode == 0, r.stdout + r.stderr
        assert calls[0] == "--version" and calls[1].startswith("check --config")


# ── 함수 수준 ─────────────────────────────────────────────────────────────────


def _module():
    spec = importlib.util.spec_from_file_location("rcm_candidate_under_test", TEMPLATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rewrite_include_keeps_the_rest_of_the_file_and_a_multiline_array(tmp_path):
    c = _module()
    text = (
        "# head\ninclude = [\n  \"a.toml\",  # first\n  'b/c.toml',\n]  # tail comment\n\n"
        "[server]\nport = 1\n"
    )
    out = c.rewrite_include(text, ["/abs/a.toml", "b/c.toml"])
    assert tomllib.loads(out)["include"] == ["/abs/a.toml", "b/c.toml"]
    assert out.startswith("# head\n") and out.endswith("]  # tail comment\n\n[server]\nport = 1\n")
