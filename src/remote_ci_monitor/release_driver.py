"""릴리스 드라이버 — 프로젝트의 `driver` 스크립트를 서버가 **분리된** 자식으로 띄우고 대장에 적는다
(스토어 탭 API 계약 v2 「Driver」 · docs/release-contract.md §5).

여기 있는 것:
- `DriverRunner.spawn()`: `<driver> --build-name X …` 를 `setsid`(새 세션) 로 띄운다.
  stdout·stderr 는 `<data_dir>/driver/<repo>/<build_name>.log` 에 **덧붙인다**(confirm·retry 가
  같은 로그를 잇는다).
  종료 코드는 셸 래퍼가 `<id>.exit` 에 남긴다 — 서버가 재기동돼 자식을 잃어도 코드를 안다.
- 끝나면(프로세스 안 감시 스레드 또는 `reconcile()` 의 pid 생존 확인) 대장에 종료를 적고 그 실행에
  발급한 내부 토큰을 **폐기**한다. 토큰 비밀은 자식의 env 로만 나가고 로그·응답·DB 어디에도 없다.
- `status()`: `<driver> --status` 를 동기(10초)로 돌려 줄들을 돌려준다. 읽기 전용이라 토큰을
  안 준다.
- `log_tail()` · `plan_n()`: 로그 끝 60줄과 마지막 `plan: N = <n>` — 사람이 칠 번호는 여기서 **보여
  주기만** 한다. 서버가 대신 채우지 않는다(계약 §6).

이 모듈은 HTTP 를 모른다. 라우트와 409 규칙은 `server.py` 에 있다.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor.store import Store

#: 드라이버 · 문안 명령이 받는 서버 env. 잡의 기본 passthrough 와 같다(`Preset.env_passthrough`).
PASSTHROUGH_ENV = ("PATH", "HOME", "LANG")
#: 드라이버 실행 종류 — 대장의 `kind`.
KIND_START = "start"
KIND_CONFIRM = "confirm"
KIND_ABORT = "abort"
KIND_RETRY = "retry"
KINDS = (KIND_START, KIND_CONFIRM, KIND_ABORT, KIND_RETRY)
#: 내부 토큰 이름의 앞부분. 토큰 이름은 PK 라 실행마다 다르게 짓는다(`store-driver:<repo>:<id>`).
TOKEN_PREFIX = "store-driver"
LOG_TAIL_LINES = 60
STATUS_TIMEOUT = 10.0
MAX_STATUS_BYTES = 64 * 1024
_PLAN_RE = re.compile(r"^plan:\s*N\s*=\s*(\d+)\s*$")
#: 셸 래퍼 — 드라이버를 로그에 덧붙이며 돌리고 종료 코드를 exit 파일에 남긴다. 인자는 `"$@"` 로만
#: 넘어간다(보간 없음).
_WRAPPER = '"$@" >>"$RCM_DRIVER_LOG" 2>&1; rc=$?; printf %s "$rc" >"$RCM_DRIVER_EXIT"; exit $rc'

LogFn = Callable[[str], None]


def pid_alive(pid: int | None) -> bool:
    """그 pid 의 프로세스가 있는가. 권한이 없어도 「있다」. pid 재사용은 감지하지 못한다."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def token_name(repo: str, release_id: int) -> str:
    return f"{TOKEN_PREFIX}:{repo}:{release_id}"


def driver_argv(
    driver: Path,
    *,
    build_name: str,
    kind: str,
    android_track: str | None = None,
    dry_run: bool = False,
    confirm_n: int | None = None,
) -> list[str]:
    """계약 §5 의 명령줄. 되돌릴 수 없는 단계의 확인 번호는 사람이 친 것만 들어간다."""
    argv = [str(driver), "--build-name", build_name]
    if android_track:
        argv += ["--android-track", android_track]
    if dry_run:
        argv.append("--dry-run")
    if kind == KIND_CONFIRM:
        argv += ["--confirm-build-number", str(int(confirm_n or 0))]
    elif kind == KIND_ABORT:
        argv.append("--abort")
    elif kind == KIND_RETRY:
        argv.append("--retry")
    return argv


def base_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    src = os.environ if environ is None else environ
    return {k: src[k] for k in PASSTHROUGH_ENV if k in src}


def _tail_bytes(path: Path, limit: int = 64 * 1024) -> bytes:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read()
    except OSError:
        return b""


def log_lines(path: Path, mask: Callable[[bytes], bytes] | None = None) -> list[str]:
    """로그 끝 64 KB 의 줄들(마스킹 뒤). 파일이 없으면 빈 목록."""
    data = _tail_bytes(path)
    if mask is not None:
        data = mask(data)
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[1:] if len(data) >= 64 * 1024 and lines else lines  # 잘린 첫 줄은 버린다


def log_tail(path: Path, mask: Callable[[bytes], bytes] | None = None) -> list[str]:
    return log_lines(path, mask)[-LOG_TAIL_LINES:]


def plan_n(path: Path) -> int | None:
    """로그의 **마지막** `plan: N = <n>`. 없으면 None — 서버는 번호를 지어내지 않는다."""
    found: int | None = None
    for line in log_lines(path):
        m = _PLAN_RE.match(line.strip())
        if m:
            found = int(m.group(1))
    return found


def read_exit_code(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.lstrip("-").isdigit() else None


class DriverRunner:
    """실행 대장과 프로세스. 한 서버 프로세스에 하나 — `App` 이 만든다."""

    def __init__(
        self,
        store: Store,
        *,
        now_fn: Callable[[], datetime],
        log: LogFn | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self.store = store
        self.now_fn = now_fn
        self.log = log or (lambda _msg: None)
        self.environ = environ
        self._lock = threading.Lock()
        self._procs: dict[int, subprocess.Popen[bytes]] = {}
        # 실행이 끝난 뒤(대장에 종료가 적힌 뒤) 부르는 훅 — 서버가 «빌드 번호 자동» 이어 달리기에
        # 쓴다. 감시 스레드에서 불리고, 예외는 삼켜 로그에만 남긴다.
        self.on_exit: Callable[[dict[str, Any]], None] | None = None

    # ── 경로 ─────────────────────────────────────────────────────────────

    @staticmethod
    def root(data_dir: Path, repo: str) -> Path:
        return data_dir / "driver" / repo

    @classmethod
    def log_path(cls, data_dir: Path, repo: str, build_name: str) -> Path:
        return cls.root(data_dir, repo) / f"{build_name}.log"

    @classmethod
    def exit_path(cls, data_dir: Path, repo: str, release_id: int) -> Path:
        return cls.root(data_dir, repo) / f"{release_id}.exit"

    # ── 실행 ─────────────────────────────────────────────────────────────

    def running(self, repo: str) -> dict[str, Any] | None:
        """정리(`reconcile`) 뒤 아직 도는 실행 행. 없으면 None."""
        self.reconcile(repo)
        for row in self.store.list_open_releases(repo):
            if pid_alive(row["pid"]):
                return row
        return None

    def spawn(
        self,
        *,
        data_dir: Path,
        repo: str,
        driver: Path,
        checkout: Path,
        build_name: str,
        kind: str,
        started_by: str,
        env: Mapping[str, str],
        server_url: str,
        android_track: str | None = None,
        dry_run: bool = False,
        confirm_n: int | None = None,
        auto_n: bool = False,
    ) -> dict[str, Any]:
        """대장에 행을 열고 내부 토큰을 발급해 드라이버를 띄운다. 돌려주는 행에는 pid 가 있다.
        띄우지 못하면 행을 닫고(exit_code None) 토큰을 폐기한 뒤 `OSError` 를 올린다."""
        if kind not in KINDS:
            raise ValueError(f"unknown driver kind {kind!r}")
        now = self.now_fn()
        root = self.root(data_dir, repo)
        root.mkdir(parents=True, exist_ok=True)
        log_file = self.log_path(data_dir, repo, build_name)
        release_id = self.store.create_release(
            repo=repo,
            build_name=build_name,
            kind=kind,
            started_by=started_by,
            now=now,
            log_path=str(log_file),
            confirmed_n=confirm_n if kind == KIND_CONFIRM else None,
            confirmed_by=started_by if kind == KIND_CONFIRM else None,
            android_track=android_track,
            dry_run=dry_run,
            auto_n=auto_n,
        )
        name = token_name(repo, release_id)
        secret = self.store.add_token(name, admin=False, now=now, kind="client")
        argv = driver_argv(
            driver,
            build_name=build_name,
            kind=kind,
            android_track=android_track,
            dry_run=dry_run,
            confirm_n=confirm_n,
        )
        exit_file = self.exit_path(data_dir, repo, release_id)
        with contextlib.suppress(OSError):
            exit_file.unlink()
        child_env = {
            **base_env(self.environ),
            **dict(env),
            "RCM_SERVER": server_url,
            "RCM_TOKEN": secret,
            "RCM_DRIVER_LOG": str(log_file),
            "RCM_DRIVER_EXIT": str(exit_file),
        }
        stamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        shown = " ".join(argv[1:])  # 토큰은 argv 에 없다 — 로그 머리에 인자만 적는다
        with log_file.open("ab") as fh:
            head = f"--- rcm driver {kind} #{release_id} {stamp} by {started_by}: {shown}\n"
            fh.write(head.encode())
        try:
            proc = subprocess.Popen(
                ["/bin/sh", "-c", _WRAPPER, "rcm-driver", *argv],
                cwd=str(checkout),
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self._finish(release_id, None, name, why=f"spawn failed: {type(e).__name__}")
            raise
        self.store.set_release_started(release_id, pid=proc.pid, token_name=name)
        with self._lock:
            self._procs[release_id] = proc
        threading.Thread(
            target=self._watch, args=(release_id, proc, name), name=f"rcm-driver-{release_id}"
        ).start()
        self.log(f"driver: {repo} {kind} #{release_id} pid {proc.pid} ({build_name})")
        row = self.store.get_release(release_id)
        assert row is not None
        return row

    def _watch(self, release_id: int, proc: subprocess.Popen[bytes], name: str) -> None:
        """자식을 기다렸다가 종료를 적는다 — 기다리지 않으면 좀비가 남아 pid 가 산 것처럼 보인다."""
        try:
            rc = proc.wait()
            self._finish(release_id, rc, name)
            self._after_exit(release_id)
        finally:
            with self._lock:
                self._procs.pop(release_id, None)
            self.store.close()  # 이 스레드의 연결

    def _finish(self, release_id: int, exit_code: int | None, name: str | None, *, why=""):
        now = self.now_fn()
        # 토큰을 **먼저** 거둔다 — `finished_at` 이 적히는 순간 `wait()` 가 돌아오므로, 그 뒤에
        # 거두면 「끝났는데 토큰은 아직 산」 창이 생긴다(CI ubuntu 3.11 에서 실측된 경합).
        # 이미 끝난 실행(중복 호출)이면 revoke 는 멱등이라 두 번 해도 해가 없다.
        if name:
            self.store.revoke_token(name, now)
        if not self.store.finish_release(release_id, exit_code, now):
            return
        tail = f" ({why})" if why else ""
        self.log(f"driver: #{release_id} exit {exit_code}{tail}")

    def _after_exit(self, release_id: int) -> None:
        hook = self.on_exit
        if hook is None:
            return
        row = self.store.get_release(release_id)
        if row is None:
            return
        try:
            hook(row)
        except Exception as e:  # noqa: BLE001 — 훅의 실패가 감시 스레드를 죽이면 안 된다
            self.log(f"driver: #{release_id} on_exit failed: {type(e).__name__}: {e}")

    def wait(self, release_id: int, timeout: float) -> bool:
        """테스트 · 종료 절차용 — 프로세스 안에서 띄운 실행이 끝날 때까지. 모르는 실행이면 True."""
        with self._lock:
            proc = self._procs.get(release_id)
        if proc is None:
            return True
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        for _ in range(200):  # 감시 스레드가 대장을 적을 시간을 준다
            row = self.store.get_release(release_id)
            if row is None or row["finished_at"] is not None:
                return True
            threading.Event().wait(0.01)
        return False

    def reconcile(self, repo: str | None = None, *, data_dir: Path | None = None) -> list[int]:
        """열려 있는데 pid 가 죽은 실행을 닫는다(서버 재기동 뒤의 고아). 종료 코드는 exit 파일에서,
        없으면 None. 닫은 실행 번호들."""
        closed: list[int] = []
        for row in self.store.list_open_releases(repo):
            with self._lock:
                watched = row["id"] in self._procs
            if watched or pid_alive(row["pid"]):
                continue
            code = None
            if data_dir is not None:
                code = read_exit_code(self.exit_path(data_dir, row["repo"], row["id"]))
            else:
                code = read_exit_code(Path(row["log_path"]).with_name(f"{row['id']}.exit"))
            self._finish(row["id"], code, row["token_name"], why="process gone")
            closed.append(row["id"])
        return closed

    # ── --status ─────────────────────────────────────────────────────────

    def status(
        self,
        driver: Path,
        checkout: Path,
        env: Mapping[str, str],
        *,
        timeout: float = STATUS_TIMEOUT,
    ) -> tuple[list[str] | None, str | None]:
        """`<driver> --status` 의 stdout 줄들(≤ 64 KB). 못 돌리면 (None, 이유)."""
        try:
            proc = subprocess.run(
                [str(driver), "--status"],
                cwd=str(checkout),
                env={**base_env(self.environ), **dict(env)},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, f"--status did not finish within {timeout:g}s"
        except OSError as e:
            return None, f"--status could not start: {type(e).__name__}"
        out = proc.stdout[:MAX_STATUS_BYTES].decode("utf-8", errors="replace").splitlines()
        if proc.returncode != 0:
            return out, f"--status exited {proc.returncode}"
        return out, None


__all__ = [
    "KINDS",
    "KIND_ABORT",
    "KIND_CONFIRM",
    "KIND_RETRY",
    "KIND_START",
    "LOG_TAIL_LINES",
    "TOKEN_PREFIX",
    "DriverRunner",
    "driver_argv",
    "log_tail",
    "pid_alive",
    "plan_n",
    "token_name",
]
