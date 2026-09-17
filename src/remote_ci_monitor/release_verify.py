"""비밀의 읽기 전용 검증 — `verify` 종류마다 함수 하나(계약 §4).

- 검증기는 **값을 받아 결과만 돌려준다.** 결과(`VerifyResult`)에는 오류 문구와 짧은 설명
  (`login: octocat`)만 있고 값 · 토큰 · 절대 경로는 없다 — 그대로 API 응답과 `.verify.json` 에
  실린다.
- 이 빌드에 진짜 구현이 있는 종류는 `github`(api.github.com/user GET 하나, 5초) 와
  `keystore`(`keytool -list`) 뿐이다. `asc` · `play` 는 「not implemented in this build」 를
  오류로 돌려준다 — 화면이 ✗ 와 그 문구를 보여 주게. 없는 검증을 ✓ 로 꾸미지 않는다.
- 네트워크 · 자식 프로세스는 `http_get` · `run` · `which` 인자로 갈아 끼운다. 테스트는 네트워크를
  부르지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from remote_ci_monitor.config import ReleaseSecret

GITHUB_USER_URL = "https://api.github.com/user"
TIMEOUT_SECONDS = 5.0
NOT_IMPLEMENTED = "not implemented in this build"
#: `keystore` 검증이 `-storepass` 로 쓸 같은 프로파일의 value 비밀 이름. 없으면 비밀번호 없이
#: 돈다(JKS 는 무결성 검사만 건너뛰고 목록을 낸다).
KEYSTORE_PASSWORD_SECRET = "KEYSTORE_PASSWORD"

HttpGet = Callable[[str, dict[str, str], float], tuple[int, bytes]]
RunFn = Callable[..., "subprocess.CompletedProcess[str]"]
WhichFn = Callable[[str], str | None]


@dataclass(frozen=True)
class VerifyResult:
    error: str | None = None  # None 이면 통과
    detail: str | None = None  # 통과했을 때 한 줄(`login: octocat`)

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class VerifyContext:
    """검증기가 받는 것 — 그 비밀의 파일(또는 폴더)과, 이웃 value 비밀을 읽는 함수."""

    secret: ReleaseSecret
    path: Path
    sibling_value: Callable[[str], str | None]  # 같은 프로파일의 value 비밀 → 문자열 또는 None
    http_get: HttpGet
    run: RunFn
    which: WhichFn
    timeout: float = TIMEOUT_SECONDS


Verifier = Callable[[VerifyContext], VerifyResult]


def _urllib_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https 고정
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), b""


def _read_value(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def verify_github(ctx: VerifyContext) -> VerifyResult:
    """토큰으로 `GET /user` 하나. 200 이면 `login: <이름>`, 아니면 상태 코드. 토큰은 헤더로만."""
    token = _read_value(ctx.path)
    if token is None:
        return VerifyResult("token is empty")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "rcm",
    }
    try:
        status, body = ctx.http_get(GITHUB_USER_URL, headers, ctx.timeout)
    except Exception as e:  # noqa: BLE001 — 종류만 남긴다(문구에 URL·토큰이 섞일 수 있다)
        return VerifyResult(f"network: {type(e).__name__}")
    if status != 200:
        return VerifyResult(f"HTTP {status}")
    try:
        login = json.loads(body).get("login")
    except (ValueError, AttributeError):
        login = None
    if not isinstance(login, str) or not login:
        return VerifyResult("unexpected response")
    return VerifyResult(None, f"login: {login}")


def verify_keystore(ctx: VerifyContext) -> VerifyResult:
    """`keytool -list -keystore <file> [-storepass <pw>]`. keytool 이 없으면 그것이 오류다 —
    파일이 없는 것과 다르다(`present` 는 그대로 true)."""
    keytool = ctx.which("keytool")
    if keytool is None:
        return VerifyResult("keytool missing")
    argv = [keytool, "-list", "-keystore", str(ctx.path)]
    password = ctx.sibling_value(KEYSTORE_PASSWORD_SECRET)
    if password:
        argv += ["-storepass", password]
    try:
        proc = ctx.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(ctx.timeout, 15.0),
        )
    except subprocess.TimeoutExpired:
        return VerifyResult("keytool timed out")
    except OSError as e:
        return VerifyResult(f"keytool could not start: {type(e).__name__}")
    if proc.returncode != 0:
        return VerifyResult(f"keytool exit {proc.returncode}")
    entries = sum(1 for line in (proc.stdout or "").splitlines() if "PrivateKeyEntry" in line)
    return VerifyResult(None, f"{entries} key entr{'y' if entries == 1 else 'ies'}")


def verify_not_implemented(ctx: VerifyContext) -> VerifyResult:
    return VerifyResult(NOT_IMPLEMENTED)


VERIFIERS: dict[str, Verifier] = {
    "github": verify_github,
    "keystore": verify_keystore,
    "asc": verify_not_implemented,
    "play": verify_not_implemented,
}


def run_verify(
    secret: ReleaseSecret,
    path: Path,
    *,
    sibling_value: Callable[[str], str | None] = lambda _name: None,
    http_get: HttpGet = _urllib_get,
    run: RunFn = subprocess.run,
    which: WhichFn = shutil.which,
    timeout: float = TIMEOUT_SECONDS,
    verifiers: dict[str, Verifier] | None = None,
) -> VerifyResult:
    """종류에 맞는 검증기를 고른다. `none` 은 여기까지 오지 않는다(호출자가 거른다)."""
    table = VERIFIERS if verifiers is None else verifiers
    fn = table.get(secret.verify)
    if fn is None:
        return VerifyResult(f"unknown verify kind {secret.verify!r}")
    ctx = VerifyContext(
        secret=secret,
        path=path,
        sibling_value=sibling_value,
        http_get=http_get,
        run=run,
        which=which,
        timeout=timeout,
    )
    try:
        return fn(ctx)
    except Exception as e:  # noqa: BLE001 — 검증기의 예외 문구를 그대로 내지 않는다
        return VerifyResult(f"verifier failed: {type(e).__name__}")


__all__: list[str] = [
    "GITHUB_USER_URL",
    "NOT_IMPLEMENTED",
    "VERIFIERS",
    "VerifyContext",
    "VerifyResult",
    "run_verify",
]
