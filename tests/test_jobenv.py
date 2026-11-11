"""잡이 보게 될 환경(`core/jobenv.py`)과 서버가 그것을 재는 자리(M5m).

실측에서 온 요구다(2026-11-11): 프리셋이 `JAVA_HOME` 을 `env_passthrough` 로 넘기도록 적어
두었는데 launchd 유닛에 그 변수가 없어, 세션의 셸에서는 `rcm check` 가 초록인데 잡은 빈 값을
받았다. 사람이 추론으로 찾아냈다 — 그래서 **서버가 자기 환경으로** 재고 말해야 한다.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remote_ci_monitor.core import jobenv  # noqa: E402

# ── 순수 규칙 ────────────────────────────────────────────────────────────────


def test_missing_passthrough_names_what_the_environment_lacks():
    assert jobenv.missing_passthrough(("PATH", "JAVA_HOME"), {"PATH": "/bin"}) == ("JAVA_HOME",)


def test_missing_passthrough_keeps_the_declared_order():
    absent = jobenv.missing_passthrough(("B", "A", "C"), {"A": "1"})
    assert absent == ("B", "C")


def test_an_empty_value_counts_as_present():
    """유닛이 일부러 빈 값을 주는 일이 있다. 판정은 키의 유무다."""
    assert jobenv.missing_passthrough(("JAVA_HOME",), {"JAVA_HOME": ""}) == ()


def test_nothing_declared_means_nothing_missing():
    assert jobenv.missing_passthrough((), {"PATH": "/bin"}) == ()


def test_job_env_passes_through_then_the_preset_overrides():
    env = jobenv.job_env(("PATH", "LANG"), {"LANG": "C.UTF-8"}, {"PATH": "/bin", "LANG": "ko_KR"})
    assert env == {"PATH": "/bin", "LANG": "C.UTF-8"}


def test_job_env_skips_a_passthrough_name_that_is_absent():
    env = jobenv.job_env(("PATH", "JAVA_HOME"), {}, {"PATH": "/bin"})
    assert env == {"PATH": "/bin"}
    assert "JAVA_HOME" not in env


def test_a_name_the_preset_supplies_is_not_missing():
    """v0.4.4 가 운영에서 낸 거짓 경보(2026-11-11). `JAVA_HOME` 이 없다길래 `[presets.env]`
    에 넣고 다시 읽혔는데 — 잡은 이제 값을 받는데도 — 그대로 「unset」이라고 했다.

    묻는 것은 「환경에 있는가」가 아니라 **「잡이 값을 받는가」**다."""
    assert jobenv.missing_passthrough(("JAVA_HOME",), {}, {"JAVA_HOME": "/jdk"}) == ()
    # 그리고 잡이 정말 그 값을 받는지 — 같은 사실의 다른 쪽
    assert jobenv.job_env(("JAVA_HOME",), {"JAVA_HOME": "/jdk"}, {})["JAVA_HOME"] == "/jdk"


def test_a_name_neither_the_environment_nor_the_preset_gives_is_still_missing():
    assert jobenv.missing_passthrough(("JAVA_HOME",), {}, {"LANG": "C"}) == ("JAVA_HOME",)


def test_the_preset_env_argument_is_optional():
    """옛 부름 자리를 깨지 않는다."""
    assert jobenv.missing_passthrough(("A",), {}) == ("A",)


def test_the_preset_can_set_a_variable_the_environment_lacks():
    """`env_passthrough` 에 없어도 `[presets.env]` 가 주면 잡은 받는다 — 그건 빠진 게 아니다."""
    env = jobenv.job_env((), {"JAVA_HOME": "/jdk"}, {})
    assert env["JAVA_HOME"] == "/jdk"


# ── 서버가 재는 자리 ─────────────────────────────────────────────────────────


def _app(tmp: Path, preset_toml: str):
    from remote_ci_monitor import config, server, store

    path = tmp / "rcm.toml"
    path.write_text(f'[server]\ndata_dir = "{tmp}"\n{preset_toml}', encoding="utf-8")
    cfg = config.load_server_config(path)
    return server.App(cfg, store.Store(cfg.data_dir / "db.sqlite3"))


PRESET = """
[[presets]]
name = "gate"
argv = ["sh", "-c", "true"]
env_passthrough = ["PATH", "JAVA_HOME"]
requires = ["sh"]
"""


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def test_the_server_stays_quiet_once_the_preset_supplies_the_name(tmp_dir):
    """운영 실측 그대로(2026-11-11): `JAVA_HOME` 경보 → `[presets.env]` 에 넣기 → 조용해진다."""
    app = _app(tmp_dir, PRESET)
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    assert app.preset_env["gate"]["missing_passthrough"] == ["JAVA_HOME"]

    with_value = PRESET + '\n[presets.env]\nJAVA_HOME = "/jdk"\n'
    (tmp_dir / "rcm.toml").write_text(
        f'[server]\ndata_dir = "{tmp_dir}"\n{with_value}', encoding="utf-8"
    )
    from remote_ci_monitor import config

    app.config = config.load_server_config(tmp_dir / "rcm.toml")
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    assert app.preset_env["gate"]["missing_passthrough"] == []
    assert "all tools and passthrough present" in app.preset_env_summary()


def test_the_server_reports_a_passthrough_name_its_environment_lacks(tmp_dir):
    app = _app(tmp_dir, PRESET)
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    assert app.preset_env["gate"]["missing_passthrough"] == ["JAVA_HOME"]
    assert app.preset_env["gate"]["missing_tools"] == []


def test_the_server_reports_a_tool_the_job_path_cannot_reach(tmp_dir):
    app = _app(tmp_dir, PRESET)
    app.measure_preset_env({"PATH": str(tmp_dir), "JAVA_HOME": "/jdk"})
    assert app.preset_env["gate"]["missing_tools"] == ["sh"]
    assert app.preset_env["gate"]["missing_passthrough"] == []


def test_an_absolute_requires_entry_is_reported_by_basename_only(tmp_dir):
    """`/api/health` 는 기본 설정에서 토큰 없이 읽힌다 — 선언한 경로를 그대로 실으면 안 된다
    (잡 로그·`local preset tools` 와 같은 규칙, M5l S3)."""
    app = _app(
        tmp_dir,
        '\n[[presets]]\nname = "gate"\nargv = ["true"]\n'
        'requires = ["/opt/nowhere/private-build-tool"]\n',
    )
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    assert app.preset_env["gate"]["missing_tools"] == ["private-build-tool"]
    _code, body = app.health()
    assert "/opt/nowhere" not in str(body["preset_env"])


def test_a_preset_that_declares_nothing_still_gets_the_default_passthrough(tmp_dir):
    """`env_passthrough` 의 기본값은 비어 있지 않다(HOME·LANG) — 그래서 모든 프리셋이 재어진다.

    그게 맞다. launchd 유닛에 `HOME` 이 없으면 그것이야말로 말해 줘야 할 사실이다.
    """
    app = _app(tmp_dir, '\n[[presets]]\nname = "bare"\nargv = ["sh", "-c", "true"]\n')
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    assert app.preset_env["bare"]["missing_passthrough"] == ["HOME", "LANG"]


def test_a_preset_that_declares_neither_is_left_out(tmp_dir):
    """`env_passthrough = []` 에 `requires` 도 없으면 잴 것이 없다 — 행을 만들지 않는다."""
    app = _app(
        tmp_dir,
        '\n[[presets]]\nname = "bare"\nargv = ["sh", "-c", "true"]\nenv_passthrough = []\n',
    )
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    assert app.preset_env == {}


def test_the_startup_line_names_the_preset_not_just_a_count(tmp_dir):
    """「문제 1건」만 찍으면 어느 프리셋인지 보려고 다시 물어야 한다."""
    app = _app(tmp_dir, PRESET)
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    line = app.preset_env_summary()
    assert "gate" in line and "JAVA_HOME unset" in line


def test_the_startup_line_says_so_when_everything_is_present(tmp_dir):
    app = _app(tmp_dir, PRESET)
    app.measure_preset_env({"PATH": "/bin:/usr/bin", "JAVA_HOME": "/jdk"})
    assert "all tools and passthrough present" in app.preset_env_summary()


def test_health_carries_what_the_server_measured(tmp_dir):
    app = _app(tmp_dir, PRESET)
    app.measure_preset_env({"PATH": "/bin:/usr/bin"})
    _code, body = app.health()
    assert body["preset_env"]["gate"]["missing_passthrough"] == ["JAVA_HOME"]


# ── `rcm check` 의 행 ────────────────────────────────────────────────────────


def _cli():
    spec = importlib.util.spec_from_file_location(
        "rcm_cli_for_rows", ROOT / "src" / "remote_ci_monitor" / "cli.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_check_row_fails_when_the_service_lacks_something():
    row = _cli()._service_preset_env_row(
        {"preset_env": {"gate": {"missing_passthrough": ["JAVA_HOME"], "missing_tools": []}}}
    )
    assert row is not None
    name, ok, detail = row
    assert name == "service preset tools" and ok is False
    assert "JAVA_HOME unset" in detail and "as the service sees it" in detail


def test_the_check_row_passes_when_nothing_is_missing():
    row = _cli()._service_preset_env_row(
        {"preset_env": {"gate": {"missing_passthrough": [], "missing_tools": []}}}
    )
    assert row is not None and row[1] is True and "gate (ok)" in row[2]


@pytest.mark.parametrize("health", [{}, {"preset_env": {}}, {"preset_env": None}, None])
def test_an_older_server_draws_no_row_at_all(health):
    """없는 키를 FAIL 로 그리면 업그레이드 안 된 서버가 고장으로 보인다."""
    assert _cli()._service_preset_env_row(health) is None
