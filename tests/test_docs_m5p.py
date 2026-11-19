"""문서 잠금 — M5p 보관함(명세 §7 P3 · 시나리오 c 의 「문서 잠금」 표 · CB21).

기능이 문서 없이 들어가면 빨갛다. 특히 보안 문단(결정 138 — 로그인 없는 읽기)과 Host 허용 목록
(결정 148), 업그레이드 순서 경고(프리셋은 모르는 키를 거절한다)는 빠지면 사고가 나는 문장이라
글자로 잠근다. **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def flat(text: str) -> str:
    """줄바꿈 · 연속 공백을 하나로 — 문단 안의 줄 나눔에 시험이 매이지 않게."""
    return re.sub(r"\s+", " ", text)


def paragraphs(text: str) -> list[str]:
    return [flat(p) for p in re.split(r"\n\s*\n", text)]


def section(text: str, head: str) -> str:
    start = text.index(head)
    rest = text[start + len(head) :]
    m = re.search(r"^## ", rest, re.M)
    return text[start : start + len(head) + (m.start() if m else len(rest))]


# ── docs/configuration.md ────────────────────────────────────────────────────

HEAD = "## Keeping a job's files: the shelf"


def test_configuration_has_the_shelf_section_with_the_four_keys_and_defaults():
    raw = read("docs/configuration.md")
    assert HEAD in raw
    text = flat(section(raw, HEAD))
    for key in ("shelf_dir", "shelf_max_bytes", "shelf_stale_days", "shelf_delete_days"):
        assert f"`{key}`" in text, key
    assert "`53687091200`" in text
    assert "`182`" in text and "`365`" in text
    assert "<data_dir>/shelf" in text


def test_configuration_documents_the_preset_key_and_its_rule():
    text = flat(section(read("docs/configuration.md"), HEAD))
    assert "keep = true" in text
    assert "keep needs artifacts" in text
    assert 'artifacts_on = "failure"' in text
    assert "only failed jobs are kept" in text


def test_configuration_security_paragraph():
    text = flat(section(read("docs/configuration.md"), HEAD))
    assert (
        "anyone on that network, and anyone who reaches this machine over Tailscale, can open them"
        in text
    )
    assert "Do not put secrets in artifacts" in text
    assert 'read_auth = "basic"' in text


def test_configuration_host_allowlist_is_one_paragraph():
    paras = paragraphs(section(read("docs/configuration.md"), HEAD))
    want = ("421", "localhost", ".local", "100.64.0.0/10", "public_url")
    assert any(all(w in p for w in want) for p in paras), "Host allowlist paragraph"


def test_configuration_upgrade_warning():
    text = flat(section(read("docs/configuration.md"), HEAD))
    assert "only after the server runs v0.7.0" in text


def test_configuration_notification_paragraph():
    text = flat(read("docs/configuration.md"))
    assert "RCM_SHELF_URL" in text
    assert "`shelf_url`" in text


def test_configuration_other_disk_and_marker():
    text = flat(section(read("docs/configuration.md"), HEAD))
    assert ".rcm-shelf" in text
    assert "workspaces are deleted earlier" in text


def test_configuration_names_the_clock_and_the_commands():
    text = flat(section(read("docs/configuration.md"), HEAD))
    assert "/shelf/latest/" in text
    assert "rcm keep" in text and "rcm shelf rm" in text
    assert "cleanup pending" in text


# ── usage 두 언어 ────────────────────────────────────────────────────────────


def test_usage_guides_show_keep_in_both_languages():
    for rel in ("docs/usage.md", "docs/usage.ko.md"):
        text = flat(read(rel))
        assert "rcm run golden-update --keep" in text, rel
        assert "rcm keep" in text, rel
        assert "/shelf/latest/golden-update?branch=dev" in text, rel
    assert "use --keep from the start" in flat(read("docs/usage.md"))
    assert "처음부터 --keep" in flat(read("docs/usage.ko.md"))


# ── README 두 언어 ───────────────────────────────────────────────────────────


def test_readme_command_tables_list_keep_and_shelf():
    for rel in ("README.md", "README.ko.md"):
        text = read(rel)
        assert "| `rcm keep N [--json]` |" in text, rel
        assert "`rcm shelf [--state active\\|stale\\|all] [--preset P] [--json]`" in text, rel
        run_row = next(ln for ln in text.splitlines() if ln.startswith("| `rcm run PRESET"))
        assert "[--keep]" in run_row, rel


# ── examples/server.toml (= templates/server.toml) ───────────────────────────


def test_example_server_toml_has_the_keys_commented():
    for rel in ("examples/server.toml", "src/remote_ci_monitor/templates/server.toml"):
        text = read(rel)
        assert "# shelf_max_bytes = 53687091200" in text, rel
        assert "# shelf_stale_days = 182" in text, rel
        assert "# shelf_delete_days = 365" in text, rel
        assert "# keep = true" in text, rel


def test_uncommenting_the_shelf_keys_still_loads(tmp_path):
    from remote_ci_monitor.config import load_server_config

    text = read("examples/server.toml")
    for key in ("shelf_max_bytes", "shelf_stale_days", "shelf_delete_days"):
        text = re.sub(rf"^# ({key} = )", r"\1", text, flags=re.M)
    data = f'data_dir = "{tmp_path / "data"}"'
    text = re.sub(r"^data_dir = .*$", data, text, count=1, flags=re.M)
    tomllib.loads(text)
    p = tmp_path / "server.toml"
    p.write_text(text)
    cfg = load_server_config(p, check_tools=False)
    assert cfg.server.shelf_max_bytes == 53687091200
    assert cfg.server.shelf_stale_days == 182 and cfg.server.shelf_delete_days == 365


# ── CHANGELOG ────────────────────────────────────────────────────────────────


def test_changelog_unreleased_names_the_shelf():
    text = read("CHANGELOG.md")
    unreleased = text[text.index("## [Unreleased]") : text.index("## [0.6.1]")]
    for word in ("--keep", "rcm keep", "/shelf/", "RCM_SHELF_URL", "v24"):
        assert word in unreleased, word


# ── operating ────────────────────────────────────────────────────────────────


def test_operating_names_the_shelf_in_security_and_upgrade():
    text = flat(read("docs/operating.md"))
    assert "/shelf/" in text
    assert "rcm.sqlite3.v23.bak" in text


# ── 추가 요구 — app-map-shots (명세 §16) ─────────────────────────────────────


def test_workplan_has_the_app_map_shots_section():
    raw = read("docs/m5p-workplan.md")
    assert "## 16. 추가 요구 — app-map-shots (2026-11-19)" in raw
    text = flat(section(raw, "## 16. 추가 요구"))
    for word in (
        "rcm shelf get",
        "--latest",
        "/api/shelf/latest/",
        "sha256",
        "expanded_from",
        "--keep --no-wait",
        "v0.7.0",
    ):
        assert word in text, word


def test_configuration_has_the_script_subsection():
    text = flat(section(read("docs/configuration.md"), HEAD))
    assert "### Using the shelf from a script" in read("docs/configuration.md")
    assert "rcm run app-map-shots --ref dev --keep --no-wait" in text
    assert "rcm shelf get" in text and "--latest app-map-shots" in text
    assert "curl http://macmini.local:8787/api/shelf/latest/app-map-shots?branch=dev" in text
    assert "only after the server runs v0.7.0" in text
    assert "check_presets.py" in text


def test_usage_guides_show_the_script_path_in_both_languages():
    for rel in ("docs/usage.md", "docs/usage.ko.md"):
        text = flat(read(rel))
        assert "rcm shelf get" in text, rel
        assert "--keep --no-wait" in text, rel
        assert "/api/shelf/latest/app-map-shots?branch=dev" in text, rel
        assert "v0.7.0" in text, rel


def test_readme_names_shelf_get():
    for rel in ("README.md", "README.ko.md"):
        assert "`rcm shelf get N\\|URL --output DIR`" in read(rel), rel
