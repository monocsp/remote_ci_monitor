"""문서용 스크린샷 캡처 — 버릴 서버 · 워커 · 잡을 띄우고 headless Chrome 으로 원본과 좌표를 남긴다.

    python tools/screenshots/capture.py all        # setup → … → teardown
    python tools/screenshots/capture.py setup      # 단계 하나만

원본은 작업 디렉터리(`RCM_SHOT_WORK`, 기본 `<tmp>/rcm-screenshots`)의 `raw/` 에 쌓인다.
그다음 `python tools/screenshots/build.py` 가 주석(빨간 사각형 + 번호)을 그려 `docs/images/ui/`
에 넣는다. Chrome 과 Pillow 가 필요하다(`pip install -e ".[docs]"`).

이 머신의 이름 · 계정이 화면에 남지 않게 서버는 `_rcm.py` 로 띄운다(호스트명 고정).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CAP = Path(__file__).resolve().parent
ROOT = CAP.parents[1]
SHOT_LANG = os.environ.get("RCM_SHOT_LANG") or "en"
WORK = Path(os.environ.get("RCM_SHOT_WORK") or Path(tempfile.gettempdir()) / "rcm-screenshots")
SRV = WORK / "srv"
RAW = WORK / "raw"
STATE = WORK / "state.json"
DEMO_SRC = CAP / "scripts"
#: 서버·클라이언트 모두 이 진입점으로 — 저장소의 소스를 그대로 쓰고 호스트명만 고정한다.
RCM_ARGV = [sys.executable, str(CAP / "_rcm.py")]
os.environ["PYTHONPATH"] = os.pathsep.join(
    [str(ROOT / "src"), *([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])]
)
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src")]

from remote_ci_monitor.mdns import local_ipv4s  # noqa: E402 — sys.path 를 먼저 세운 뒤에 import

SELECTORS = [
    "#hdr",
    "[data-workers]",
    ".wk",
    "#tok-btn",
    "#live-btn",
    "[data-host-name]",
    "#summary",
    ".sum-yours",
    ".sum-stuck",
    ".sum-host",
    "#queue",
    "#queue .queue-header",
    "#queue thead",
    "tr[data-job]",
    "tr.qbar",
    ".pwrap",
    ".plab",
    "tr.expanded",
    ".minibar",
    ".steps",
    ".step",
    ".prog .head",
    ".tail",
    ".actions",
    ".actions .log",
    ".actions .cancel",
    "td.eta",
    "td.reason",
    "td.job",
    "td.key",
    "td.requester",
    "td.elapsed",
    "td.source",
    ".pos",
    ".rrow .id",
    ".you",
    ".conf",
    ".pool-h",
    "tr.qgroup.running",
    "tr.qgroup.waiting",
    ".pill",
    ".chip",
    ".uncommitted",
    ".sha",
    ".exp-btn",
    "#host",
    ".hostcard",
    ".hostcard .hn",
    '[data-metric="cpu"]',
    '[data-metric="mem"]',
    '[data-metric="disk"]',
    '[data-metric="gpu"]',
    ".spark",
    ".hostcard .top",
    "#recent",
    "#recent .s-h",
    ".rrow",
    ".rdetail",
    ".rerun",
    "#estimates",
    "[data-more-recent]",
    "#banner-note",
    "#banner-lost",
    "#drawer",
    ".drawer-head",
    "[data-drawer-title]",
    "[data-drawer-log]",
    "[data-drawer-close]",
    ".mark",
    ".foot",
]

RECT_JS = """
(function (sels) {
  var out = {};
  sels.forEach(function (s) {
    try {
      out[s] = Array.prototype.map.call(document.querySelectorAll(s), function (e) {
        var r = e.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height, id: e.id || null,
                job: e.getAttribute('data-job') || e.getAttribute('data-log')
                     || e.getAttribute('data-rtoggle') || e.getAttribute('data-pool')
                     || e.getAttribute('data-bar') || null,
                cls: (typeof e.className === 'string') ? e.className : null,
                hidden: !!e.hidden, text: (e.innerText || '').slice(0, 80)};
      });
    } catch (err) { out[s] = null; }
  });
  return JSON.stringify(out);
})(%s)
"""


def load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=1))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rcm(
    *args: str,
    env: dict | None = None,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 180,
) -> subprocess.CompletedProcess:
    e = {**os.environ, **(env or {})}
    p = subprocess.run(
        [*RCM_ARGV, *args], env=e, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    if check and p.returncode != 0:
        raise SystemExit(f"rcm {' '.join(args)} rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


def client_env(st: dict, who: str) -> dict:
    return {"RCM_SERVER": st["url"], "RCM_TOKEN": st["tokens"][who]}


def status(st: dict) -> dict:
    with urllib.request.urlopen(f"{st['url']}/api/status", timeout=5) as r:
        return json.load(r)


def wait_for(pred, *, timeout: float, what: str, every: float = 0.5):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = pred()
        except Exception as e:  # noqa: BLE001 — 폴링 중 일시 오류는 무시
            last = f"error: {e}"
        if last is True:
            return
        time.sleep(every)
    raise SystemExit(f"timeout waiting for {what}: last={last!r}")


def git(*args: str, cwd: Path) -> None:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=alice",
            "-c",
            "user.email=alice@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )


def queue_rows(doc: dict) -> dict[int, dict]:
    rows = {}
    for p in doc.get("pools") or []:
        for r in p.get("queue") or []:
            rows[r["id"]] = r
    return rows


def remote_workers(doc: dict) -> list[dict]:
    return [w for w in (doc.get("server") or {}).get("workers") or [] if w.get("worker")]


def job_json(p: subprocess.CompletedProcess) -> dict:
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("{")][-1]
    return json.loads(line)


def save_output(name: str, cmdline: str, p: subprocess.CompletedProcess) -> None:
    text = f"$ {cmdline}\n" + p.stderr + p.stdout
    (RAW / f"{name}.txt").write_text(text)


# ── 단계 ────────────────────────────────────────────────────────────────────


def setup() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    if SRV.exists():
        shutil.rmtree(SRV)
    (SRV / "scripts").mkdir(parents=True)
    (SRV / "data").mkdir()
    (SRV / "w").mkdir()
    RAW.mkdir(exist_ok=True)
    for name in ("demo.sh", "fail-demo.sh"):
        shutil.copy(DEMO_SRC / name, SRV / "scripts" / name)
    port = free_port()
    advertise = os.environ.get("RCM_SHOT_ADVERTISE") == "1"
    toml = f"""# 캡처용 버릴 서버 — 문서 스크린샷 전용
[server]
bind = "{"0.0.0.0" if advertise else "127.0.0.1"}"
port = {port}
advertise = {str(advertise).lower()}
advertise_name = "macmini"
data_dir = "{SRV / "data"}"
lanes = 1
read_auth = "none"
grace_seconds = 5
recent_count = 8
failure_min_jobs = 2        # 시연용 — 두 번만 돌려도 이름별 이력이 판정을 낸다 (M5h)
worker_timeout_seconds = 10
worker_heartbeat_seconds = 5
public_url = "http://macmini:8787"

[estimate]
min_job_seconds = 30

[host]
interval_seconds = 5

[display]
timezone = "Asia/Seoul"

[[presets]]
name = "demo"
description = "4 steps with markers, ~40 s (speed=fast: ~10 s)"
argv = ["/bin/sh", "{SRV / "scripts" / "demo.sh"}"]
expected_seconds = 40
timeout_seconds = 300
[[presets.inputs]]
name = "speed"
type = "choice"
choices = ["normal", "fast"]
default = "normal"

[[presets]]
name = "fail-demo"
description = "fails at step 2 — shows failed_step and summary"
argv = ["/bin/sh", "{SRV / "scripts" / "fail-demo.sh"}"]
expected_seconds = 6
timeout_seconds = 120

[[presets]]
name = "remote-demo"
description = "the demo job on the mac2 worker pool"
pool = "mac2"
pools = ["default"]
argv = ["/bin/sh", "{SRV / "scripts" / "demo.sh"}"]
expected_seconds = 40
timeout_seconds = 300
[[presets.inputs]]
name = "speed"
type = "choice"
choices = ["normal", "fast"]
default = "normal"
"""
    cfg = SRV / "server.toml"
    cfg.write_text(toml)
    tokens = {}
    for name, flag in (
        ("alice", None),
        ("bob", None),
        ("ops", "--admin"),
        ("mac2-worker", "--worker"),
    ):
        args = ["token", "--config", str(cfg), "add", name] + ([flag] if flag else [])
        p = rcm(*args)
        tokens[name] = p.stdout.strip().splitlines()[-1].strip()
    url = f"http://127.0.0.1:{port}"
    log = open(SRV / "server.log", "ab")
    srv = subprocess.Popen(
        [*RCM_ARGV, "serve", "--config", str(cfg)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    def healthy():
        with urllib.request.urlopen(f"{url}/api/health", timeout=2) as r:
            return r.status == 200

    wait_for(healthy, timeout=20, what="server health")
    st = {"port": port, "url": url, "cfg": str(cfg), "tokens": tokens, "server_pid": srv.pid}
    save(st)
    wenv = {"RCM_WORKER_TOKEN": tokens["mac2-worker"]}
    wargs = ["worker", "--server", url, "--pool", "mac2", "--lanes", "1", "--data", str(SRV / "w")]
    p = rcm(*wargs, "--check", env=wenv)
    (RAW / "worker-check.txt").write_text(p.stdout + p.stderr)
    wlog = open(SRV / "worker.log", "ab")
    wk = subprocess.Popen(
        [*RCM_ARGV, *wargs],
        env={**os.environ, **wenv},
        stdout=wlog,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    st["worker_pid"] = wk.pid
    save(st)
    wait_for(lambda: bool(remote_workers(status(st))), timeout=20, what="worker registered")
    # 프로젝트 두 개: alice 의 트리(미커밋 변경 있음) · bob 의 트리(다른 변경)
    pa = SRV / "proj-alice"
    (pa / "app").mkdir(parents=True)
    (pa / "tests").mkdir()
    (pa / "README.md").write_text("# demo-app\n\nA small app used to demonstrate rcm.\n")
    (pa / "app" / "__init__.py").write_text("")
    (pa / "app" / "main.py").write_text("def add(a, b):\n    return a + b\n")
    (pa / "tests" / "test_main.py").write_text(
        "from app.main import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    (pa / ".rcmignore").write_text("dist/\n*.log\n")
    git("init", "-q", cwd=pa)
    git("add", "-A", cwd=pa)
    git("commit", "-q", "-m", "initial", cwd=pa)
    git("remote", "add", "origin", "git@github.com:acme/demo-app.git", cwd=pa)
    pb = SRV / "proj-bob"
    shutil.copytree(pa, pb)
    (pa / "app" / "main.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    )
    (pb / "app" / "main.py").write_text("def add(a, b):\n    return int(a) + int(b)\n")
    st["proj"] = {"alice": str(pa), "bob": str(pb)}
    save(st)
    print(json.dumps({k: v for k, v in st.items() if k != "tokens"}, indent=1))


def history() -> None:
    st = load()
    pa, pb = Path(st["proj"]["alice"]), Path(st["proj"]["bob"])
    p = rcm("run", "demo", "--by", "alice@laptop", env=client_env(st, "alice"), cwd=pa)
    save_output("run-demo", "rcm run demo", p)
    st.setdefault("jobs", {})["hist_demo1"] = job_json(p)["job_id"]
    p = rcm(
        "run", "fail-demo", "--by", "alice@laptop", env=client_env(st, "alice"), cwd=pa, check=False
    )
    save_output("run-fail", "rcm run fail-demo", p)
    assert p.returncode == 1, p
    st["jobs"]["fail"] = job_json(p)["job_id"]
    p = rcm("run", "demo", "--by", "bob@desk", env=client_env(st, "bob"), cwd=pb)
    save_output("run-demo-bob", "rcm run demo", p)
    st["jobs"]["hist_demo2"] = job_json(p)["job_id"]
    save(st)
    print(st["jobs"])


class Shooter:
    def __init__(self, st: dict, window: str = "1280,900"):
        from test_web_browser import Chrome

        self.st = st
        self.chrome = Chrome(WORK / "chrome-profile", window=window)
        # 언어를 못 박는다. M5d-1 이후 화면 기본은 한국어지만, `docs/images/ui/` 의 이미지는 두
        # 사용 설명서가 **함께** 쓰고 `build.py` 의 글자 선택자도 영어다. `RCM_SHOT_LANG=ko` 로
        # 한국어 판을 찍을 수는 있다(그때는 build.py 의 text= 선택자를 같이 바꿔야 한다).
        self.url = f"{st['url']}/?poll=1&lang={SHOT_LANG}"

    def open(self, ready_js: str, timeout: float = 20.0) -> None:
        self.chrome.open(self.url, ready_js=ready_js, timeout=timeout)

    def js(self, expr: str):
        return self.chrome.eval(expr)

    def shot(self, name: str) -> None:
        time.sleep(0.4)
        self.chrome.screenshot(RAW / f"{name}.png")
        rects = self.js(RECT_JS % json.dumps(SELECTORS))
        (RAW / f"{name}.json").write_text(rects)
        (RAW / f"{name}.html").write_text(self.js("document.documentElement.outerHTML"))
        print("shot", name)

    def close(self) -> None:
        self.chrome.close()


def rows_ready_js(ids: list[int]) -> str:
    return (
        f"{json.dumps(ids)}.every(function(id)"
        "{return document.querySelector('#queue [data-job=\"'+id+'\"]')!==null})"
        " && document.querySelector('#host .meter[data-metric=\"cpu\"]')!==null"
    )


def round1() -> None:
    st = load()
    pa, pb = Path(st["proj"]["alice"]), Path(st["proj"]["bob"])
    a = job_json(
        rcm("run", "demo", "--no-wait", "--by", "alice@laptop", env=client_env(st, "alice"), cwd=pa)
    )["job_id"]
    b = job_json(
        rcm("run", "demo", "--no-wait", "--by", "bob@desk", env=client_env(st, "bob"), cwd=pb)
    )["job_id"]
    r = job_json(
        rcm(
            "run",
            "remote-demo",
            "--no-wait",
            "--by",
            "alice@laptop",
            env=client_env(st, "alice"),
            cwd=pa,
        )
    )["job_id"]
    st.setdefault("jobs", {}).update({"A": a, "B": b, "R": r})
    save(st)

    def ready():
        doc = status(st)
        rows = queue_rows(doc)
        ra, rb, rr = rows.get(a, {}), rows.get(b, {}), rows.get(r, {})
        ok = (
            ra.get("state") == "running"
            and (ra.get("progress") or {}).get("steps_done", 0) >= 1
            and rb.get("state") == "queued"
            and rr.get("state") == "running"
        )
        return (
            True
            if ok
            else (
                ra.get("state"),
                (ra.get("progress") or {}).get("steps_done"),
                rb.get("state"),
                rr.get("state"),
            )
        )

    wait_for(ready, timeout=40, what="A running with a step done, B queued, R running on mac2")
    p = rcm("top", env=client_env(st, "alice"))
    save_output("top", "rcm top", p)
    sh = Shooter(st)
    try:
        sh.open(rows_ready_js([a, b]))
        # 도는 행도 평소엔 접혀 있다(오너 결정 13, 2026-09-09 개정) — 문서 사진은 스텝 목록이
        # 보이는 펼친 모습을 찍는다. 편 상태는 `rcm.expanded` 에 남아 다음 열기에도 이어진다.
        sh.js(f"document.querySelector('[data-toggle=\"{a}\"]').click()")
        wait_for(
            lambda: sh.js("document.querySelectorAll('#queue tr.expanded').length > 0"),
            timeout=5,
            what="the running row expanded",
        )
        sh.shot("queue")
        sh.js(f"localStorage.setItem('rcm.token', {json.dumps(st['tokens']['alice'])})")
        sh.open(
            rows_ready_js([a, b])
            + " && document.querySelector('#tok-btn').textContent.indexOf('alice')>=0"
            " && document.querySelector('.sum-yours .v').textContent.indexOf('#')>=0"
        )
        sh.shot("yours")
        sh.js(f"document.querySelector('[data-log=\"{a}\"]').click()")
        wait_for(
            lambda: sh.js(
                "!document.querySelector('#drawer').hidden"
                " && document.querySelector('[data-drawer-log]').textContent.length > 40"
            ),
            timeout=10,
            what="drawer with log text",
        )
        sh.shot("log")
        sh.js("document.querySelector('[data-drawer-close]').click()")
        # 호스트 절은 평소 접혀 있다(M5d-2 §4.1) — 문서 사진은 펼친 모습을 찍는다
        sh.js("var d=document.querySelector('#host-details'); if (d) d.open = true")
        sh.js("document.querySelector('#host').scrollIntoView({block:'start'})")
        time.sleep(0.4)
        sh.shot("host")
        sh.chrome.call(
            "Emulation.setDeviceMetricsOverride",
            # 390 은 iPhone 세로 폭이다. 창 크기로는 못 낸다 — macOS 크롬 창은 500px 밑으로 안
            # 줄어들어서, 이걸 안 쓰면 「폰 사진」이 실은 500px 짜리다(명세 §4.7).
            # 높이는 900 이면 다섯 번째 주석(버튼 줄)이 화면 밖으로 나간다 — 폭이 레이아웃을
            # 정하고 높이는 사진에 담기는 양만 정하므로, 높이만 조금 늘린다.
            {"width": 390, "height": 1000, "deviceScaleFactor": 1, "mobile": True},
        )
        sh.js("window.scrollTo(0,0)")
        time.sleep(0.6)
        sh.shot("phone")
        sh.chrome.call("Emulation.clearDeviceMetricsOverride")
        doc = status(st)
        st["round1_check"] = {
            j: queue_rows(doc).get(i, {}).get("state") for j, i in (("A", a), ("B", b), ("R", r))
        }
        save(st)
        print(st["round1_check"])
    finally:
        sh.close()


def round2() -> None:
    st = load()
    f = st["jobs"]["fail"]

    def drained():
        rows = queue_rows(status(st))
        return True if not rows else {k: v.get("state") for k, v in rows.items()}

    wait_for(drained, timeout=150, what="queue drained")
    sh = Shooter(st)
    try:
        sh.open(
            "document.querySelector('#recent .rrow') !== null"
            " && document.querySelector('#host .meter[data-metric=\"cpu\"]')!==null"
        )
        sh.js(f"document.querySelector('.rrow[data-rtoggle=\"{f}\"]').click()")
        wait_for(
            lambda: sh.js("document.querySelector('.rdetail') !== null"), timeout=5, what="rdetail"
        )
        sh.js("document.querySelector('#recent').scrollIntoView({block:'start'})")
        time.sleep(0.3)
        sh.shot("recent")
    finally:
        sh.close()


def down() -> None:
    st = load()
    pa = Path(st["proj"]["alice"])
    r2 = job_json(
        rcm(
            "run",
            "remote-demo",
            "--no-wait",
            "--by",
            "alice@laptop",
            env=client_env(st, "alice"),
            cwd=pa,
        )
    )["job_id"]
    st["jobs"]["R2"] = r2
    save(st)
    wait_for(
        lambda: (
            queue_rows(status(st)).get(r2, {}).get("state") == "running"
            or queue_rows(status(st)).get(r2, {}).get("state")
        ),
        timeout=30,
        what="R2 running on mac2",
    )
    time.sleep(4)
    os.kill(st["worker_pid"], signal.SIGKILL)
    st["worker_killed"] = True
    save(st)
    wait_for(
        lambda: (
            any(w.get("state") == "down" for w in remote_workers(status(st)))
            or [w.get("state") for w in remote_workers(status(st))]
        ),
        timeout=40,
        what="worker down",
    )
    time.sleep(1)
    sh = Shooter(st)
    try:
        sh.open(
            "!document.querySelector('#banner-note').hidden"
            " && document.querySelector('#banner-note').textContent.indexOf('unreachable')>=0"
            " && document.querySelector('.wk.down') !== null"
        )
        sh.shot("down")
    finally:
        sh.close()


def teardown() -> None:
    st = load()
    for key in ("worker_pid", "server_pid"):
        pid = st.get(key)
        if not pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.2)
        else:
            os.kill(pid, signal.SIGKILL)
    print("teardown done")


# ── 터미널 출력 ─────────────────────────────────────────────────────────────


#: 문서에 들어가는 가짜 토큰 — 진짜 토큰은 절대 캡처에 남기지 않는다.
#: 문서용 자리표시자 — 진짜 토큰과 같은 모양(43자 urlsafe base64)이지만 아무 데서도 통하지 않는다.
#: 상수로 적으면 비밀 검사기(gitleaks)가 오탐하므로 해시에서 만든다.
FAKE_TOKEN = base64.urlsafe_b64encode(hashlib.sha256(b"rcm docs placeholder").digest()).decode()
FAKE_TOKEN = FAKE_TOKEN.rstrip("=")
#: 문서용 가짜 주소 — 표가 어긋나지 않게 진짜 주소와 **같은 길이**로 고른다.
FAKE_IPS = {
    8: "10.0.0.5",
    9: "10.0.0.15",
    10: "10.10.0.15",
    11: "192.168.1.5",
    12: "192.168.0.10",
    13: "192.168.0.100",
    14: "192.168.10.100",
    15: "192.168.100.100",
}


def _fake_ip(ip: str) -> str:
    return FAKE_IPS.get(len(ip), "192.168.0.10")


def _tidy(text: str, st: dict, homes: list[Path], proj: Path, secrets: list[str]) -> str:
    """캡처한 출력에서 이 머신의 흔적을 지운다 — 경로 · 포트 · LAN 주소 · 토큰."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, FAKE_TOKEN)
    subs = [(str(proj), "~/src/demo-app")]
    subs += [(str(h), "~") for h in homes]
    subs += [(str(SRV), "~/.config/rcm"), (f":{st['port']}", ":8787")]
    for real, shown in subs:
        text = text.replace(real, shown)
    for ip in local_ipv4s():
        text = text.replace(ip, _fake_ip(ip))
    return text


def _block(cmd: str, p: subprocess.CompletedProcess, *, rc: bool = False) -> str:
    out = f"$ {cmd}\n" + p.stderr + p.stdout
    if rc:
        out += f"$ echo $?\n{p.returncode}\n"
    return out


def terminal() -> None:
    """가이드에 쓰는 터미널 출력. 토큰만 있는 새 HOME 에서 돌려 `raw/x-*.txt` 로 남긴다.

    `RCM_SHOT_ADVERTISE=1` 이면 광고하는 서버를 따로 띄워 `rcm discover` 까지 진짜로 찍는다 —
    같은 네트워크에 다른 rcm 서버가 없을 때만 쓸 것(있으면 여러 줄이 나온다).
    """
    st = load()
    home = SRV / "home-session"
    (home / ".config" / "rcm").mkdir(parents=True, exist_ok=True)
    proj = Path(st["proj"]["alice"])
    token = st["tokens"]["alice"]
    ct = home / ".config" / "rcm" / "client.toml"
    advertise = "advertise = true" in Path(st["cfg"]).read_text()
    ct.write_text(
        ("" if advertise else f'server = "{st["url"]}"\n')
        + f'token = "{token}"\nlabel = "alice@laptop"\n'
    )
    ct.chmod(0o600)
    env = {"HOME": str(home), "RCM_SERVER": "", "RCM_TOKEN": "", "RCM_CONFIG": ""}

    setup_home = SRV / "home-setup"
    (setup_home / ".config" / "rcm").mkdir(parents=True, exist_ok=True)
    homes = [setup_home, home]
    secrets = list(st["tokens"].values())

    def shot(name: str, text: str) -> None:
        (RAW / f"{name}.txt").write_text(_tidy(text, st, homes, proj, secrets))
        print("text", name)

    # 1) 빌드 머신의 첫 명령 — 설정 파일 · 토큰 발급 · 목록
    senv = {"HOME": str(setup_home), "RCM_CONFIG": ""}
    p = rcm("init", "server", env=senv)
    text = _block("rcm init server", p)
    cfg = str(st["cfg"])
    p = rcm("token", "--config", cfg, "add", "laptop", env=senv)
    secrets.append(p.stdout.strip().splitlines()[-1].strip())  # 새로 만든 토큰도 가린다
    text += _block("rcm token add laptop", p)
    p = rcm("token", "--config", cfg, "list", env=senv)
    shot("x-setup", text + _block("rcm token list", p))

    # 2) 세션 머신 — 서버를 찾고 점검한다
    # 발견은 client.toml 과 무관하다 — 같은 네트워크에 서버가 하나면 한 줄이 나온다.
    shot("x-discover", _block("rcm discover", rcm("discover", env=env, check=False)))
    shot("x-check", _block("rcm check", rcm("check", env=env, check=False)))
    shot("x-presets", _block("rcm presets", rcm("presets", env=env)))

    # 3) 첫 잡 — 끝까지 기다린다
    p = rcm("run", "demo", "-f", "speed=fast", env=env, cwd=proj)
    shot("x-run", _block("rcm run demo -f speed=fast", p, rc=True))

    # 4) 떼어 놓고 보기 — 제출만 · 목록 · 로그 따라가기 · 기다리기
    p = rcm("run", "demo", "--no-wait", env=env, cwd=proj)
    jid = job_json(p)["job_id"]
    text = _block("rcm run demo --no-wait", p)
    time.sleep(3)
    text += _block("rcm jobs", rcm("jobs", env=env))
    shot("x-logs", _block(f"rcm logs {jid} --follow", rcm("logs", str(jid), "--follow", env=env)))
    shot(
        "x-nowait",
        text + _block(f"rcm wait --job {jid}", rcm("wait", "--job", str(jid), env=env), rc=True),
    )

    # 5) 실패 · 합류
    p = rcm("run", "fail-demo", env=env, cwd=proj, check=False)
    shot("x-fail", _block("rcm run fail-demo", p, rc=True))
    p = rcm("run", "demo", "--no-wait", "-f", "speed=fast", env=env, cwd=proj)
    text = _block("rcm run demo --no-wait -f speed=fast", p)
    p = rcm("run", "demo", "--no-wait", "-f", "speed=fast", env=env, cwd=proj)
    shot(
        "x-join", text + _block("rcm run demo --no-wait -f speed=fast   # 다른 세션, 같은 트리", p)
    )
    shot("x-top", _block("rcm top", rcm("top", env=env)))
    wait_for(lambda: not queue_rows(status(st)) or True, timeout=1, what="noop")


STAGES = {
    "setup": setup,
    "history": history,
    "round1": round1,
    "round2": round2,
    "terminal": terminal,
    "down": down,
    "teardown": teardown,
}


def main(argv: list[str]) -> int:
    want = argv[1] if len(argv) > 1 else "all"
    if want != "all":
        print(f"=== {want}", flush=True)
        STAGES[want]()
        return 0
    # 단계마다 새 프로세스 — 한 프로세스에서 headless Chrome 을 세 번 띄우면 파이프 fd 가 엉킨다
    for name in STAGES:
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), name])
        if p.returncode != 0:
            if name != "teardown":
                subprocess.run([sys.executable, str(Path(__file__).resolve()), "teardown"])
            return p.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
