"""`rcm shelf get` 과 스크립트가 읽는 `--keep --no-wait` 출력 — M5p 추가 요구(app-map-shots,
docs/m5p-workplan.md §16).

dolomood 는 PR 마다 `rcm run app-map-shots --keep --no-wait` 의 출력에서 잡 번호와 보관 URL 을 읽고,
다른 컴퓨터에서 그 링크로 산출물을 다시 받는다. 받는 명령은 토큰 없이(읽기 규칙) 돌고, 받은 파일의
sha256 을 `/api/shelf/N` 의 값과 대조한다. **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

서버는 `fake_rcm_http.FakeRcm` 이다. P2 가 만드는 모양(§16)을 그대로 못 박는다:
- `GET /api/shelf/N` → `{job_id, state, files: [{path, url, sha256, size, type, expanded_from}]}`
- `GET /api/shelf/latest/<preset>?branch=` → 같은 모양, 없으면 404
- `GET /shelf/N.zip` → `files/` 아래 전체(경로는 `files/` 기준), `GET /shelf/N/f/<path>` → 그 파일
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, RawReply, free_port
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor.cli import build_parser, main
from test_cli_m1 import last_json

JOB = 412
FILES = {
    "build/app_map/shots.tar.gz": b"\x1f\x8b fake tarball bytes",
    "build/app_map/manifest.json": b'{"screens": 3}\n',
    "build/app_map/run.log": b"ok\n",
    "build/app_map/shots.tar.gz.d/home.png": b"\x89PNG fake",
}
SUMMARY = {
    "enabled": True,
    "stored_bytes": 1000,
    "limit_bytes": 53_687_091_200,
    "items": {"active": 1, "stale": 0, "pending": 0, "failed": 0},
    "last_sweep_at": None,
    "error_code": None,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def item_doc(files: dict[str, bytes] = FILES, *, state: str = "active", **over: Any) -> dict:
    doc = {
        "job_id": JOB,
        "state": state,
        "preset": "app-map-shots",
        "branch": "dev",
        "files": [
            {
                "path": p,
                "url": f"/shelf/{JOB}/f/{p}",
                "sha256": sha(b),
                "size": len(b),
                "type": "application/octet-stream",
                "expanded_from": "build/app_map/shots.tar.gz" if ".d/" in p else None,
            }
            for p, b in files.items()
        ],
    }
    doc.update(over)
    return doc


def zip_of(files: dict[str, bytes], extra: list[zipfile.ZipInfo | tuple] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p, b in files.items():
            z.writestr(p, b)
        for e in extra:
            if isinstance(e, tuple):
                z.writestr(*e)
            else:
                z.writestr(e, b"x")
    return buf.getvalue()


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def serve(fake: FakeRcm, *, zip_body: bytes | None = None, doc: dict | None = None) -> None:
    fake.route("GET", "/api/health", {"ok": True, "version": __version__, "shelf": SUMMARY})
    fake.route("GET", f"/api/shelf/{JOB}", doc or item_doc())
    fake.route("GET", f"/shelf/{JOB}.zip", RawReply(zip_body or zip_of(FILES)))
    for p, b in FILES.items():
        fake.route("GET", f"/shelf/{JOB}/f/{p}", RawReply(b))


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake(env, monkeypatch):
    f = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", f.url)
    serve(f)
    yield f
    f.close()


def written(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── 파서 ─────────────────────────────────────────────────────────────────────


def test_shelf_get_parses_a_number_a_url_or_latest():
    a = build_parser().parse_args(["shelf", "get", "412", "--output", "d"])
    assert a.target == "412" and a.output == "d" and a.file is None
    b = build_parser().parse_args(
        ["shelf", "get", "--latest", "app-map-shots", "--branch", "dev", "--output", "d"]
    )
    assert b.latest == "app-map-shots" and b.branch == "dev" and b.target is None
    c = build_parser().parse_args(["shelf", "get", "412", "--output", "d", "--file", "a/b.png"])
    assert c.file == "a/b.png"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("412", (None, 412)),
        ("http://mac.local:8787/shelf/412/", ("http://mac.local:8787", 412)),
        ("http://mac.local:8787/shelf/412", ("http://mac.local:8787", 412)),
        ("http://10.0.0.5:8787/api/shelf/412", ("http://10.0.0.5:8787", 412)),
        ("http://mac.local:8787/shelf/412/?lang=en", ("http://mac.local:8787", 412)),
        ("https://mac.tail1.ts.net/shelf/412/", ("https://mac.tail1.ts.net", 412)),
    ],
)
def test_shelf_target_parsing(text, want):
    assert cli_mod.parse_shelf_target(text) == want


@pytest.mark.parametrize(
    "text", ["", "abc", "-1", "ftp://x/shelf/1/", "http://x/jobs/1", "http://x/shelf/abc/"]
)
def test_bad_shelf_targets(text):
    assert cli_mod.parse_shelf_target(text) is None


def test_get_needs_exactly_one_of_target_and_latest(fake, tmp_path, capsys):
    out = str(tmp_path / "o")
    assert run(capsys, ["shelf", "get", "--output", out])[0] == 2
    code, _o, err = run(capsys, ["shelf", "get", "412", "--latest", "x", "--output", out])
    assert code == 2, err
    code, _o, err = run(capsys, ["shelf", "get", "nope", "--output", out])
    assert code == 2 and "rcm: shelf get:" in err, err


# ── 받기 (§16-2) ─────────────────────────────────────────────────────────────


def test_get_by_number_writes_every_file_without_a_token(fake, tmp_path, capsys):
    out = tmp_path / "o"
    code, stdout, err = run(capsys, ["shelf", "get", "412", "--output", str(out)])
    assert code == 0, err
    assert written(out) == FILES
    assert stdout.strip().splitlines()[-1] == (
        f"wrote 4 files to {out} from {fake.url}/shelf/{JOB}/"
    ), stdout
    assert all("authorization" not in r.headers for r in fake.requests)
    assert fake.calls("GET", f"/shelf/{JOB}.zip"), "whole item comes as one zip"


def test_get_by_url_uses_that_server_and_never_sends_the_token(env, monkeypatch, tmp_path, capsys):
    mine, other = FakeRcm(), FakeRcm()
    try:
        serve(other)
        mine.route("GET", "/api/health", {"ok": True, "version": __version__, "shelf": SUMMARY})
        monkeypatch.setenv("RCM_SERVER", mine.url)
        monkeypatch.setenv("RCM_TOKEN", "secret-token")
        out = tmp_path / "o"
        url = f"{other.url}/shelf/{JOB}/"
        code, stdout, err = run(capsys, ["shelf", "get", url, "--output", str(out)])
        assert code == 0, err
        assert written(out) == FILES
        assert mine.requests == []
        assert all("authorization" not in r.headers for r in other.requests)
        assert stdout.strip().endswith(f"from {other.url}/shelf/{JOB}/")
    finally:
        mine.close()
        other.close()


def test_get_by_number_sends_the_token_to_its_own_server(fake, monkeypatch, tmp_path, capsys):
    """`read_auth = "basic"` 서버는 읽기에도 자격이 필요하다 — 자기 서버에는 토큰을 싣는다."""
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
    assert code == 0, err
    assert any(r.headers.get("authorization") == "Bearer alice-token" for r in fake.requests)


def test_get_one_file(fake, tmp_path, capsys):
    out = tmp_path / "o"
    code, stdout, err = run(
        capsys,
        ["shelf", "get", "412", "--output", str(out), "--file", "build/app_map/manifest.json"],
    )
    assert code == 0, err
    assert written(out) == {"build/app_map/manifest.json": FILES["build/app_map/manifest.json"]}
    assert "wrote 1 file to" in stdout
    assert fake.calls("GET", f"/shelf/{JOB}.zip") == []


def test_get_one_file_that_is_not_there_exits_1(fake, tmp_path, capsys):
    code, _o, err = run(
        capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o"), "--file", "nope.txt"]
    )
    assert code == 1, err
    assert "rcm: shelf get: #412 has no file nope.txt" in err


def test_get_latest_asks_for_the_preset_and_branch(fake, tmp_path, capsys, monkeypatch):
    fake.route("GET", "/api/shelf/latest/app-map-shots", item_doc())
    seen: list[str] = []
    real = cli_mod._shelf_fetch_json

    def spy(client, path):
        seen.append(path)
        return real(client, path)

    monkeypatch.setattr(cli_mod, "_shelf_fetch_json", spy)
    out = tmp_path / "o"
    code, _o, err = run(
        capsys,
        [
            "shelf",
            "get",
            "--latest",
            "app-map-shots",
            "--branch",
            "dev",
            "--output",
            str(out),
        ],
    )
    assert code == 0, err
    assert "/api/shelf/latest/app-map-shots?branch=dev" in seen, seen
    assert written(out) == FILES


def test_get_latest_with_nothing_kept_exits_1(fake, tmp_path, capsys):
    code, _o, err = run(
        capsys, ["shelf", "get", "--latest", "nothing", "--output", str(tmp_path / "o")]
    )
    assert code == 1, err
    assert "rcm: shelf get: nothing kept for nothing" in err, err


@pytest.mark.parametrize(
    ("status", "body", "text"),
    [
        (404, {"error": "not found"}, "#412 is not on the shelf"),
        (
            410,
            {"error": "deleted", "state": "deleted", "deleted_at": "2026-11-19T16:30:00Z"},
            "#412 was deleted",
        ),
    ],
)
def test_missing_or_deleted_exits_1(fake, tmp_path, capsys, status, body, text):
    fake.route("GET", f"/api/shelf/{JOB}", body, status=status)
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
    assert code == 1, err
    assert text in err, err
    assert not (tmp_path / "o").exists() or written(tmp_path / "o") == {}


def test_a_failed_item_exits_1(fake, tmp_path, capsys):
    fake.route("GET", f"/api/shelf/{JOB}", item_doc({}, state="failed", reason_code="shelf_full"))
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
    assert code == 1, err
    assert "not kept (shelf_full)" in err


def test_an_item_still_copying_exits_3(fake, tmp_path, capsys):
    fake.route("GET", f"/api/shelf/{JOB}", item_doc({}, state="copying"))
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
    assert code == 3, err
    assert "still being copied" in err


def test_connection_refused_exits_3(env, tmp_path, capsys):
    url = f"http://127.0.0.1:{free_port()}/shelf/{JOB}/"
    code, _o, err = run(capsys, ["shelf", "get", url, "--output", str(tmp_path / "o")])
    assert code == 3, err
    assert "rcm: shelf get: cannot reach" in err


def test_an_old_server_says_upgrade(fake, tmp_path, capsys):
    fake.route("GET", "/api/health", {"ok": True, "version": "0.6.1"})
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
    assert code == 2, err
    assert "server does not support rcm shelf (rcm ≥ 0.7.0)" in err


def test_a_redirect_is_not_followed(fake, tmp_path, capsys):
    target = FakeRcm()
    try:
        serve(target)
        fake.route(
            "GET",
            f"/api/shelf/{JOB}",
            RawReply(b"", {"Location": f"{target.url}/api/shelf/{JOB}"}),
            status=302,
        )
        code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(tmp_path / "o")])
        assert code == 3, err
        assert "302" in err and "redirect" in err
        assert target.requests == []
    finally:
        target.close()


# ── 무결성 · 경로 안전 ───────────────────────────────────────────────────────


def test_a_sha256_mismatch_writes_nothing_and_exits_3(fake, tmp_path, capsys):
    bad = dict(FILES)
    bad["build/app_map/run.log"] = b"no\n"  # 같은 크기 — 해시만 다르다
    fake.route("GET", f"/shelf/{JOB}.zip", RawReply(zip_of(bad)))
    out = tmp_path / "o"
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(out)])
    assert code == 3, err
    assert "sha256" in err and "build/app_map/run.log" in err
    assert not out.exists() or written(out) == {}


def test_a_file_missing_from_the_zip_writes_nothing(fake, tmp_path, capsys):
    short = {k: v for k, v in FILES.items() if not k.endswith("run.log")}
    fake.route("GET", f"/shelf/{JOB}.zip", RawReply(zip_of(short)))
    out = tmp_path / "o"
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(out)])
    assert code == 3, err
    assert not out.exists() or written(out) == {}


def symlink_member(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize(
    "extra",
    [
        ("../escape.txt", b"x"),
        ("/abs/escape.txt", b"x"),
        ("a/../../escape.txt", b"x"),
        ("a/.git/config", b"x"),
        ("bad\nname.txt", b"x"),
        symlink_member("link"),
    ],
    ids=["dotdot", "absolute", "nested-dotdot", "git", "control", "symlink"],
)
def test_an_unsafe_zip_member_is_refused_and_nothing_is_written(fake, tmp_path, capsys, extra):
    fake.route("GET", f"/shelf/{JOB}.zip", RawReply(zip_of(FILES, [extra])))
    out = tmp_path / "work" / "o"
    code, _o, err = run(capsys, ["shelf", "get", "412", "--output", str(out)])
    assert code == 3, err
    assert "unsafe" in err, err
    assert not (tmp_path / "work" / "escape.txt").exists()
    assert not out.exists() or written(out) == {}


def test_a_manifest_path_that_escapes_is_refused_for_one_file(fake, tmp_path, capsys):
    doc = item_doc({"../escape.txt": b"x"})
    fake.route("GET", f"/api/shelf/{JOB}", doc)
    fake.route("GET", f"/shelf/{JOB}/f/../escape.txt", RawReply(b"x"))
    code, _o, err = run(
        capsys,
        ["shelf", "get", "412", "--output", str(tmp_path / "o"), "--file", "../escape.txt"],
    )
    assert code == 3, err
    assert not (tmp_path / "escape.txt").exists()


def test_get_leaves_no_temporary_files_behind(fake, tmp_path, capsys):
    out = tmp_path / "o"
    assert run(capsys, ["shelf", "get", "412", "--output", str(out)])[0] == 0
    assert sorted(written(out)) == sorted(FILES)
    assert not [p for p in out.iterdir() if p.name.startswith(".rcm")]


# ── `--keep --no-wait` 의 출력은 스크립트가 읽는다 (§16-1) ───────────────────


def test_keep_no_wait_output_is_parseable(env, monkeypatch, capsys):
    fake = FakeRcm()
    try:
        monkeypatch.setenv("RCM_SERVER", fake.url)
        monkeypatch.setenv("RCM_TOKEN", "t")
        fake.route("GET", "/api/health", {"ok": True, "version": __version__, "shelf": SUMMARY})
        fake.route("GET", "/api/whoami", {"name": "ci", "admin": False})
        fake.route(
            "GET",
            "/api/status",
            {
                "presets": [
                    {
                        "name": "app-map-shots",
                        "source_modes": ["git_ref"],
                        "inputs": [],
                        "has_artifacts": True,
                        "keep": False,
                    }
                ]
            },
        )
        shelf_url = f"{fake.url}/shelf/{JOB}/"
        fake.route(
            "POST",
            "/jobs",
            {
                "job_id": JOB,
                "state": "queued",
                "joined": False,
                "url": f"{fake.url}/#/jobs/{JOB}",
                "keep": True,
                "shelf_url": shelf_url,
            },
        )
        fake.route("GET", f"/jobs/{JOB}", {"id": JOB, "state": "queued", "keep": True})
        code, out, err = run(
            capsys, ["run", "app-map-shots", "--ref", "dev", "--keep", "--no-wait"]
        )
        assert code == 0, err
        m = re.search(r"^submitted job #(\d+)", err, re.M)
        assert m and int(m.group(1)) == JOB, err
        m = re.search(r"^shelf: (\S+) \(after the job finishes\)$", err, re.M)
        assert m and m.group(1) == shelf_url, err
        doc = last_json(out)
        assert doc["job_id"] == JOB and doc["shelf_url"] == shelf_url
    finally:
        fake.close()
