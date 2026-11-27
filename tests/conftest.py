"""모든 시험의 공통 격리 (M5q §6 · 시나리오 A101).

따라가기는 `$XDG_CACHE_HOME/rcm` 에 받고 `$XDG_CONFIG_HOME/rcm/client-version` 을 읽는다. 시험이
사용자의 진짜 캐시와 한 줄 파일을 건드리지 않게 두 변수를 `tmp_path` 아래로 돌리고, 개발자 셸에
남은 `RCM_CLIENT_VERSION` 과 따라가기 변수, 워커 재실행 시각을 지운다. 따라간 자식의
표지(`_follow_boot`)와 이 프로세스의 따라가기 결과도 시험마다 비운다.

시험 위생 둘(2026-11-20 사건 4 — macOS 의 역방향 DNS 가 변이마다 36초, 진짜 시간 제한을 기다린
M5q 시험이 CI 를 25분 늘렸다):

- 역방향 DNS 를 막는다. `socket.getfqdn(name)` 은 묻지 않고 `name`(빈 값이면 `gethostname()`)을,
  `socket.gethostbyaddr` 는 `socket.herror` 를 돌려준다. 진짜 조회가 필요한 시험은
  `@pytest.mark.allow_reverse_dns`. 세션 범위 픽스처가 서버를 띄우기 전에 걸려야 해서 픽스처가
  아니라 `pytest_configure` 에서 바꾼다.
- `@pytest.mark.slow` 없이 setup · call · teardown 합이 예산을 넘는 시험이 있으면 세션 끝에
  실패(exit 1)로 만든다. 예산은 직렬 20초, xdist 워커가 둘 이상이면 60초다 — rcm-ci 잡 #3849
  (`-n auto`, 워커 10)에서 직렬 5초짜리 Chrome 시험이 29.6초가 됐다. `RCM_TEST_SLOW_SECONDS` 가
  있으면 그 값이 이긴다. 판정은 `pytest_runtest_logreport` 로 모으므로 xdist
  (`-n`)에서도 워커가 아니라 컨트롤러가 한 번 한다.

Chrome 을 띄우는 시험(`test_web_browser.py` 와 그 `Chrome` 하네스를 들여오는 모듈 전부)은 `-n`
에서 한 워커로 모은다. 워커 여럿이 동시에 Chrome 을 띄우면 CDP 가 마감을 넘겨 실패했다 — rcm-ci 잡
#3861(`-n 6`)의 `test_web_layout_queue_edges.py`. `-n` 이 켜져 있고 `--dist` 가 xdist 의 기본값
(`load`)이면 `pytest_configure` 가 그것을 `loadgroup` 으로 바꾼다. `--dist loadgroup` 을
`pyproject.toml` 의 addopts 에 두면 xdist 가 없는 환경에서 pytest 가 그 옵션을 몰라 시작하지
못했다(#245 격리 검증). 그때 `pytest_xdist_make_scheduler` 가 xdist 의 loadgroup 스케줄러를
고쳐 그 파일들의 시험을 그룹 `chrome` 으로 묶는다. 시험에도 `xdist_group("chrome")` 을 달지만
그것만으로는 안 묶인다: xdist 3.8.0 은 그룹 이름을 `item._nodeid` 에 붙이는데 pytest 9 의
nodeid 는 `_id` 를 읽는다. 모듈마다 표시를 다는 대신 파일을 보고 정하므로 새 Chrome 시험 모듈이
표시를 잊어도 같은 워커로 간다.

운영 보호(2026-11-25 — `rcm doctor` 시험 하나가 이 Mac 의 운영 서버 127.0.0.1:8787 에 읽기 요청을
보냈다): 시험이 도는 동안 8787 로의 연결(주소와 상관없이)과, 실제 홈의 `~/.config/rcm` ·
`~/.local/share/rcm` 아래 파일 열기(`open` · `os.open` · `sqlite3.connect`)를 막고 그 시험을
실패로 만든다. 감사 훅(`sys.addaudithook`)이라 `socket.create_connection` · `http.client` · `Path`
읽기가 모두 걸린다. 실제 홈은 이 파일을 들여올 때 한 번 읽는다(시험이 HOME 을 바꾸기 전).
rcm-ci 잡은 그 데이터 폴더 안(`workspaces/<id>`)에서 돌 수 있으므로 이 레포 · 파이썬 설치
(`sys.prefix`) · 임시 폴더 · 시작 폴더 아래는 막지 않는다. 그 가운데 운영 폴더의 조상인 것(홈에서
시작한 pytest 의 cwd)은 버린다. 자식 프로세스는 이 훅 밖이다. 가드 자신의 시험은
`tests/test_doctor_cli.py` 의 `test_the_production_guard_*` 다.
"""

from __future__ import annotations

import contextlib
import functools
import os
import re
import shutil
import socket
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

import remote_ci_monitor

#: 표시 없이 넘으면 안 되는 한 시험의 시간(초) — 직렬과 병렬(xdist 워커 둘 이상).
SERIAL_SLOW_SECONDS = 20.0
PARALLEL_SLOW_SECONDS = 60.0


def worker_count(config, environ=os.environ) -> int:
    """xdist 워커 수. 컨트롤러는 `-n` 값(`auto` · `logical` 은 CPU 수)을, 워커 프로세스는
    `PYTEST_XDIST_WORKER_COUNT` 를 본다. xdist 가 없거나 `-n` 이 없으면 0."""
    env = environ.get("PYTEST_XDIST_WORKER_COUNT")
    if env and env.isdigit():
        return int(env)
    value = getattr(getattr(config, "option", None), "numprocesses", None)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        if value in ("auto", "logical"):
            return os.cpu_count() or 1
    return 0


def slow_budget(workers: int, environ=os.environ) -> float:
    """한 시험의 예산(초). `RCM_TEST_SLOW_SECONDS` 가 있으면 그 값, 아니면 워커 수로 정한다."""
    explicit = environ.get("RCM_TEST_SLOW_SECONDS")
    if explicit:
        return float(explicit)
    return PARALLEL_SLOW_SECONDS if workers > 1 else SERIAL_SLOW_SECONDS


#: 이번 세션의 예산 — `pytest_configure` 가 정한다.
SLOW_SECONDS = SERIAL_SLOW_SECONDS

_REAL_GETFQDN = socket.getfqdn
_REAL_GETHOSTBYADDR = socket.gethostbyaddr
#: 지금 도는 시험이 `allow_reverse_dns` 인가 — 함수 범위 픽스처가 켜고 끈다.
_DNS_ALLOWED = [False]
_DURATIONS: dict[str, float] = {}
_MARKED_SLOW: set[str] = set()


def _getfqdn(name: str = "") -> str:
    if _DNS_ALLOWED[0]:
        return _REAL_GETFQDN(name)
    return name or socket.gethostname()


def _gethostbyaddr(address: str):
    if _DNS_ALLOWED[0]:
        return _REAL_GETHOSTBYADDR(address)
    raise socket.herror(
        1, f"reverse DNS for {address!r} is stubbed in tests — mark the test allow_reverse_dns"
    )


def pytest_configure(config):
    global SLOW_SECONDS
    SLOW_SECONDS = slow_budget(worker_count(config))
    config.addinivalue_line(
        "markers", f"slow: this test may take more than {SLOW_SECONDS:g}s (tests/conftest.py)"
    )
    config.addinivalue_line(
        "markers", "allow_reverse_dns: real socket.getfqdn / gethostbyaddr (tests/conftest.py)"
    )
    socket.getfqdn = _getfqdn
    socket.gethostbyaddr = _gethostbyaddr
    _default_dist_to_loadgroup(config)


def _default_dist_to_loadgroup(config) -> None:
    """`-n` 인데 `--dist` 가 xdist 의 기본값(`load`)이면 `loadgroup` 으로 — Chrome 시험을 한 워커로
    모은다. 묶음 없는 시험에서 loadgroup 은 load 와 같으므로 `--dist load` 를 직접 줘도 같은 일이다.
    xdist 가 없으면 `xdist_group` 표시만 알린다(모르는 표시 경고가 나지 않게)."""
    if not config.pluginmanager.hasplugin("xdist"):
        config.addinivalue_line("markers", "xdist_group(name): ignored without pytest-xdist")
        return
    if hasattr(config, "workerinput"):  # 워커는 컨트롤러가 정한 대로 받는다
        return
    if config.getoption("numprocesses", None) and config.getoption("dist", None) == "load":
        config.option.dist = "loadgroup"


#: Chrome 하네스를 들여오는 줄 — `from test_web_browser import Chrome, …` 이나 괄호로 여러 줄.
CHROME_IMPORT_RE = re.compile(r"^from test_web_browser import (?:\([^)]*\)|[^\n]*)", re.M)


@functools.cache
def uses_chrome(path: str) -> bool:
    """그 시험 파일이 Chrome 을 띄우나 — 하네스 자신이거나 하네스의 `Chrome` 을 들여온다."""
    file = Path(path)
    if file.name == "test_web_browser.py":
        return True
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(re.search(r"\bChrome\b", m.group(0)) for m in CHROME_IMPORT_RE.finditer(text))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Chrome 시험에 `xdist_group("chrome")` 을 단다(`-m xdist_group` 으로 고를 수 있다)."""
    for item in items:
        if uses_chrome(str(item.path)):
            item.add_marker(pytest.mark.xdist_group("chrome"))


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_make_scheduler(config, log):
    """`--dist loadgroup` 의 스케줄러 — Chrome 파일의 시험은 그룹 `chrome`, 나머지는 시험 하나가
    한 그룹(loadgroup 과 같다). 다른 `--dist` 는 xdist 에 맡긴다."""
    if config.getvalue("dist") != "loadgroup":
        return None
    from xdist.scheduler import LoadGroupScheduling

    root = config.rootpath

    class ChromeOnOneWorker(LoadGroupScheduling):
        def _split_scope(self, nodeid: str) -> str:
            if "@" in nodeid:  # xdist 가 그룹 이름을 붙여 보냈다(pytest 9 밖)
                return super()._split_scope(nodeid)
            return "chrome" if uses_chrome(str(root / nodeid.split("::", 1)[0])) else nodeid

    return ChromeOnOneWorker(config, log)


def pytest_unconfigure(config):
    socket.getfqdn = _REAL_GETFQDN
    socket.gethostbyaddr = _REAL_GETHOSTBYADDR


@pytest.fixture(autouse=True)
def _reverse_dns_marker(request):
    _DNS_ALLOWED[0] = request.node.get_closest_marker("allow_reverse_dns") is not None
    yield
    _DNS_ALLOWED[0] = False


def pytest_runtest_logreport(report):
    if report.when not in ("setup", "call", "teardown"):
        return
    _DURATIONS[report.nodeid] = _DURATIONS.get(report.nodeid, 0.0) + report.duration
    if "slow" in report.keywords:
        _MARKED_SLOW.add(report.nodeid)


def _too_slow() -> list[tuple[float, str]]:
    return sorted(
        ((took, nodeid) for nodeid, took in _DURATIONS.items() if took > SLOW_SECONDS),
        reverse=True,
    )


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):  # xdist 워커 — 컨트롤러가 판정한다
        return
    unmarked = [(took, nodeid) for took, nodeid in _too_slow() if nodeid not in _MARKED_SLOW]
    session.config.stash[_UNMARKED] = unmarked
    if unmarked and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


_UNMARKED = pytest.StashKey[list]()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    unmarked = config.stash.get(_UNMARKED, [])
    if not unmarked:
        return
    terminalreporter.section(
        f"tests over {SLOW_SECONDS:g}s without @pytest.mark.slow", sep="=", red=True, bold=True
    )
    for took, nodeid in unmarked:
        terminalreporter.write_line(f"{took:7.1f}s  {nodeid}")
    terminalreporter.write_line(
        "Make the test quicker (fake the clock, stub the network) or mark it @pytest.mark.slow."
    )


FOLLOW_ENV = (
    "RCM_CLIENT_VERSION",
    "RCM_FOLLOWED",
    "RCM_FOLLOWED_PATH",
    "RCM_FOLLOWED_FROM",
    "RCM_FOLLOW_FROM",
    "RCM_FOLLOW_FOUND",
    "RCM_WORKER_RESTARTED_AT",  # 드레인 재실행의 고리 막기(PR B2)
)


@pytest.fixture(autouse=True)
def _follow_isolation(monkeypatch, tmp_path_factory):
    base = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    for name in FOLLOW_ENV:
        monkeypatch.delenv(name, raising=False)
    if hasattr(remote_ci_monitor, "_follow_boot"):
        monkeypatch.delattr(remote_ci_monitor, "_follow_boot")
    try:
        from remote_ci_monitor import follow
    except ImportError:  # 따라가기 모듈이 없는 판(뮤테이션 사본의 옛 코드)
        follow = None
    if follow is not None and hasattr(follow, "_STATE"):
        monkeypatch.setattr(follow, "_STATE", None)
    # 워커가 exec 직전에 프로브를 닫는다(M5q B2). 한 시험이 닫은 것이 다른 시험의 프로브를 막지
    # 않게 시험마다 되돌린다
    from remote_ci_monitor import hostsample

    if hasattr(hostsample, "_PROBES_CLOSED"):
        monkeypatch.setattr(hostsample, "_PROBES_CLOSED", False)
    yield


# ── 운영 보호: 시험이 운영 서버 · 운영 설정 · 운영 데이터에 닿지 않는다 ───────────────────

#: 운영 서버의 포트. 운영 서버는 0.0.0.0 에 묶이므로 LAN 주소 · `.local` 이름 · IPv4 대응 IPv6
#: 로도 닿는다 — 주소와 상관없이 이 포트로 가는 연결을 막는다(P6 GAP E7). 시험이 8787 에 실제로
#: 연결할 정당한 경우는 없다(URL 글자로만 쓴다)
PRODUCTION_PORT = 8787
#: 실제 홈 — 시험이 HOME 을 바꾸기 전에 한 번 읽는다
_REAL_HOME = os.path.expanduser("~")
PRODUCTION_DIRS = tuple(
    os.path.join(_REAL_HOME, *part.split("/")) for part in (".config/rcm", ".local/share/rcm")
)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def allowed_roots(candidates: tuple[str, ...]) -> tuple[str, ...]:
    """운영 데이터 폴더 안에서 돌아도 막지 않는 자리 — rcm-ci 잡의 체크아웃 · venv · 임시 폴더 ·
    시작 폴더. 운영 폴더를 덮는 자리(그 조상, 예: 홈에서 pytest 를 시작한 cwd)는 버린다 — 그 뿌리
    하나로 가드가 아무것도 막지 않게 된다(P3 사이클 2 r2 두 명)."""
    roots = {os.path.abspath(p) for p in candidates}
    return tuple(sorted(r for r in roots if not any(_under(d, r) for d in PRODUCTION_DIRS)))


_ALLOWED_ROOTS = allowed_roots(
    (
        str(Path(__file__).resolve().parent.parent),
        sys.prefix,
        sys.base_prefix,
        sys.exec_prefix,
        tempfile.gettempdir(),
        os.path.realpath(tempfile.gettempdir()),
        os.getcwd(),
    )
)
#: 가드가 처음 돈 날(2026-11-26) 실제 `~/.config/rcm/client.toml` 을 읽던 시험 — 이 가드를 더한
#: PR(#268) 밖의 파일이라 고치지 않고 이름만 둔다. 그 시험의 HOME 을 임시 폴더로 돌리면 여기서
#: 뺀다. 이 목록은 설정 폴더 읽기만 봐준다 — 8787 연결과 데이터 폴더는 그대로 막는다
KNOWN_CONFIG_READERS = frozenset(
    {
        "tests/test_ci_connect_watch.py::test_h6_an_unreachable_server_is_retried_for_three_rounds"
        "_then_error",
        "tests/test_ci_connect_watch.py::test_h7_h23_a_failed_submit_posts_unavailable_with_a_code",
        "tests/test_cli_m5g.py::test_a_timeout_exits_three_and_says_the_server_may_still_be_deleting",
        "tests/test_client_wheel.py::test_version_json_stays_server_free",
    }
)
_GUARD_ON = [False]
_CONFIG_READ_OK = [False]
_GUARD_HITS: list[str] = []


def production_path(raw: object, config_ok: bool = False) -> bool:
    """운영 설정 · 데이터 폴더 아래이고 허용된 자리 밖인 경로인가. `config_ok` 면 설정 폴더는
    본다(`KNOWN_CONFIG_READERS`)."""
    if isinstance(raw, int) or not isinstance(raw, (str, bytes, os.PathLike)):
        return False
    path = os.path.abspath(os.fsdecode(os.fspath(raw)))
    dirs = PRODUCTION_DIRS[1:] if config_ok else PRODUCTION_DIRS
    if not any(_under(path, d) for d in dirs):
        return False
    return not any(_under(path, root) for root in _ALLOWED_ROOTS)


def production_address(address: object) -> bool:
    """운영 포트로 가는 연결인가 — 주소는 보지 않는다(IPv6 의 4칸 주소도 같다)."""
    return isinstance(address, tuple) and len(address) >= 2 and address[1] == PRODUCTION_PORT


def _production_audit(event: str, args: tuple) -> None:
    if not _GUARD_ON[0]:
        return
    if event == "socket.connect":
        if production_address(args[1]):
            _GUARD_HITS.append(f"connect {args[1][0]}:{args[1][1]}")
            raise ConnectionRefusedError(111, "tests must not reach the production server")
    elif event in ("open", "sqlite3.connect"):
        if args and production_path(args[0], _CONFIG_READ_OK[0]):
            shown = os.fsdecode(os.fspath(args[0])).replace(_REAL_HOME, "~", 1)
            _GUARD_HITS.append(f"{event} {shown}")
            raise PermissionError(13, "tests must not open production config or data", shown)


sys.addaudithook(_production_audit)


@pytest.fixture(autouse=True)
def _production_guard(request):
    del _GUARD_HITS[:]
    _CONFIG_READ_OK[0] = request.node.nodeid.split("[", 1)[0] in KNOWN_CONFIG_READERS
    _GUARD_ON[0] = True
    try:
        yield
    finally:
        _GUARD_ON[0] = False
    if _GUARD_HITS:
        hits = list(_GUARD_HITS)
        del _GUARD_HITS[:]
        pytest.fail(f"the test reached production: {', '.join(hits)} (tests/conftest.py)")


# ── /tmp 밖의 시험 폴더 (`tools/live_proc.py` 시험) ───────────────────────────────────


@contextlib.contextmanager
def outside_tmp_folder() -> Iterator[Path]:
    """`/tmp` 밖의 시험 폴더 하나. 맥미니 CI 는 TMPDIR 을 /private/tmp 아래에 두어 tmp_path 도
    거기 있고, `tools/live_proc.py` 는 /tmp 아래의 `--dir` 를 거부한다(잡 4380). 그래서 계정의
    진짜 홈(`HOME` 이 아니라 — 도구와 같다)에 `~/.local/share/rcm-devtest-livep-<무작위>` 를
    만들고 끝나면 그 폴더만 지운다. 운영 자리 `~/.local/share/rcm` 의 형제일 뿐 안이 아니다.

    못 지우면 시험이 실패한다(2026-11-27 batch-8 — 예전에는 `ignore_errors` 로 조용히 남았다).
    강제 종료로 남은 `rcm-devtest-livep-*` 는 다음 실행이 지우지 않는다. 같은 이름의 폴더는
    다른 실행 · 다른 세션의 것일 수 있다 — 손으로 확인하고 지운다."""
    import pwd  # 유닉스에만 있다 — 이 함수를 부르는 시험만 들여온다

    base = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "share"
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="rcm-devtest-livep-", dir=base))
    try:
        yield folder
    finally:
        shutil.rmtree(folder)  # mutcheck: conftest-outside-tmp-ignores-cleanup


@pytest.fixture
def outside_tmp() -> Iterator[Path]:
    """`outside_tmp_folder` 의 픽스처 꼴. `tests/test_live_proc.py` 와 `tests/test_skill_links.py`
    가 쓴다."""
    with outside_tmp_folder() as folder:
        yield folder
