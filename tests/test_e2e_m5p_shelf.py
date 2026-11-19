"""끝단 시험(M5p P3 완료 조건) — 진짜 서버 · 진짜 로컬 워커 · 진짜 CLI · 진짜 HTTP.

명세 §7 P3 「완료 조건」: 응답 모양을 손으로 흉내 낸 시험만으로 끝내지 않는다(잠긴 키 집합이 버그를
숨긴 선례가 있다). 시나리오는 docs/m5p-test-scenarios-c.md 의 CB22~25 · CE21 · CG41 · CG42.

서버 쪽(P2: copier · `/shelf*` · `POST /jobs/N/keep` · health `shelf`)이 머지되기 전에는 **skip**
한다. P3 는 P1 위에서 P2 와 병렬로 만들었고, 리드가 P2 머지 뒤 이 브랜치를 그 위로 올리며 돌린다.
"""

from __future__ import annotations

import base64
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import server as server_mod
from remote_ci_monitor.cli import main
from remote_ci_monitor.client import Client
from remote_ci_monitor.config import parse_preset
from test_server import PRESETS, Server

pytestmark = pytest.mark.skipif(
    not hasattr(server_mod, "ShelfCopier"),
    reason="P2 머지 뒤 — 서버의 보관함(copier · /shelf · POST /jobs/N/keep)이 아직 없다",
)

PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG = base64.b64decode(PNG_B64)
MAKE = (
    "import io,tarfile,base64,pathlib;"
    "p=pathlib.Path('out');p.mkdir(exist_ok=True);"
    f"png=base64.b64decode('{PNG_B64}');"
    "(p/'home.png').write_bytes(png);"
    "b=io.BytesIO();t=tarfile.open(fileobj=b,mode='w:gz');"
    "i=tarfile.TarInfo('goldens/home.png');i.size=len(png);t.addfile(i,io.BytesIO(png));t.close();"
    "(p/'goldens.tar.gz').write_bytes(b.getvalue());"
    "(p/'run.log').write_text('ok\\n')"
)


def py(name: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "argv": [sys.executable, "-c", code], "timeout_seconds": 60, **extra}


SHELF_PRESETS = [*PRESETS, py("shelfopt", MAKE, artifacts=["out/*"])]


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def shelf_link(err: str) -> str:
    m = re.search(r"^shelf: (\S+)$", err, re.M)
    assert m, err
    return m.group(1)


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli_mod, "SHELF_WAIT_SECONDS", 30)

    def use(server: Server, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True, max_snapshot_bytes=200_000)
    try:
        s.cfg.presets = tuple(parse_preset(p) for p in SHELF_PRESETS)
    except BaseException:
        s.close()
        raise
    yield s
    s.close()


@pytest.fixture
def tree(tmp_path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    return root


def wait_active(s: Server, job_id: int, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = s.req("GET", f"/api/shelf/{job_id}")
        if status == 200 and body and body.get("state") == "active":
            return body
        time.sleep(0.2)
    raise AssertionError(f"#{job_id} never became active")


def test_cb22_the_link_rcm_run_keep_prints_opens(live, env, tree, capsys):
    env(live)
    code, _out, err = run(capsys, ["run", "shelfopt", "--keep", "--dir", str(tree)])
    assert code == 0, err
    url = shelf_link(err)
    status, page = get(url)
    assert status == 200, page[:300]
    html = page.decode("utf-8")
    assert "out/home.png" in html
    assert "out/goldens.tar.gz.d/goldens/home.png" in html
    path = re.search(r"/shelf/\d+/f/out/home\.png", html)
    assert path, html[:500]
    status, data = get(f"http://127.0.0.1:{live.port}{path.group(0)}")
    assert status == 200 and data == PNG


def test_cb23_late_keep_then_list_then_remove(live, env, tree, capsys):
    env(live)
    code, out, err = run(capsys, ["run", "shelfopt", "--dir", str(tree)])
    assert code == 0, err
    job_id = int(re.search(r'"job_id":\s*(\d+)', out).group(1))
    code, out, err = run(capsys, ["keep", str(job_id)])
    assert code == 0, err
    assert f"#{job_id} is being copied to the shelf" in out
    wait_active(live, job_id)
    code, out, err = run(capsys, ["shelf", "--json"])
    assert code == 0, err
    assert f'"job_id": {job_id}' in out.replace('"job_id":', '"job_id": ').replace("  ", " ")
    env(live, "admin")
    code, out, err = run(capsys, ["shelf", "rm", str(job_id)])
    assert code == 0, err
    status, _ = get(f"http://127.0.0.1:{live.port}/shelf/{job_id}/")
    assert status == 410


def test_cb24_keep_with_fetch_artifacts(live, env, tree, capsys):
    env(live)
    code, _out, err = run(
        capsys, ["run", "shelfopt", "--keep", "--fetch-artifacts", "--dir", str(tree)]
    )
    assert code == 0, err
    assert (tree / "out" / "home.png").read_bytes() == PNG
    lines = err.splitlines()
    wrote = next(i for i, ln in enumerate(lines) if ln.startswith("artifacts: wrote"))
    shelf = next(i for i, ln in enumerate(lines) if ln.startswith("shelf: "))
    assert wrote < shelf, err
    job_id = int(re.search(r"/shelf/(\d+)/", lines[shelf]).group(1))
    wait_active(live, job_id)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _status, body = live.req("GET", f"/jobs/{job_id}/artifacts", token="alice")
        if body and body.get("state") == "purged":
            break
        time.sleep(0.2)
    assert body.get("state") == "purged", body


def test_cb25_the_notify_hook_gets_the_same_link_as_the_cli(tmp_path, env, tree, capsys):
    out_file = tmp_path / "hook.env"
    code = (
        "import os,pathlib;"
        f"pathlib.Path({str(out_file)!r}).write_text(os.environ.get('RCM_SHELF_URL',''))"
    )
    from remote_ci_monitor.core.notify import NotifyRule

    s = Server(tmp_path / "srv", workers=True, max_snapshot_bytes=200_000)
    try:
        s.cfg.server.public_url = f"http://127.0.0.1:{s.port}"
        s.cfg.presets = tuple(parse_preset(p) for p in SHELF_PRESETS)
        s.cfg.notify = (NotifyRule(name="hook", argv=(sys.executable, "-c", code)),)
        s.app.notifier.start() if getattr(s.app, "notifier", None) else None
        env(s)
        rc, _out, err = run(capsys, ["run", "shelfopt", "--keep", "--dir", str(tree)])
        assert rc == 0, err
        url = shelf_link(err)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not out_file.exists():
            time.sleep(0.1)
        assert out_file.read_text() == url
    finally:
        s.close()


def test_ce21_old_server_detection_on_the_real_server(live, env, tree, capsys, monkeypatch):
    env(live)
    real = live.app.health

    def old_health(*a, **k):
        status, body = real(*a, **k)
        body.pop("shelf", None)
        return status, body

    monkeypatch.setattr(live.app, "health", old_health)
    before = live.store._conn().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    code, _out, err = run(capsys, ["run", "shelfopt", "--keep", "--dir", str(tree)])
    assert code == 2, err
    assert "server does not support --keep (rcm ≥ 0.7.0)" in err
    after = live.store._conn().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert before == after


def test_cg41_the_real_submission_carries_keep_only_with_the_flag(
    live, env, tree, capsys, monkeypatch
):
    env(live)
    bodies: list[dict[str, Any]] = []
    real = Client.post_json

    def post_json(self, path, obj=None, **kw):
        if path == "/jobs":
            bodies.append(dict(obj or {}))
        return real(self, path, obj, **kw)

    monkeypatch.setattr(Client, "post_json", post_json)
    assert run(capsys, ["run", "shelfopt", "--keep", "--dir", str(tree)])[0] == 0
    assert run(capsys, ["run", "ok", "--dir", str(tree)])[0] == 0
    assert bodies[0].get("keep") is True
    assert "keep" not in bodies[1]


def test_cg42_opening_the_link_restarts_the_clock(live, env, tree, capsys):
    env(live)
    code, _out, err = run(capsys, ["run", "shelfopt", "--keep", "--dir", str(tree)])
    assert code == 0, err
    url = shelf_link(err)
    job_id = int(re.search(r"/shelf/(\d+)/", url).group(1))
    wait_active(live, job_id)
    old = time.time() - 2 * 3600
    conn = sqlite3.connect(live.cfg.data_dir / "rcm.sqlite3")
    conn.execute("UPDATE shelf_items SET last_used_at=? WHERE job_id=?", (old, job_id))
    conn.commit()
    conn.close()
    status, _ = get(url.rstrip("/") + "/f/out/home.png")
    assert status == 200
    item = live.store.shelf_item(job_id)
    assert item is not None and item.last_used_at > old + 3600
