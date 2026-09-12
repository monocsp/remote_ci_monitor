"""제출 capability 의 로컬 상태(M5j G5 · 결정 87) — `rcm run` 이 받은 cancel token 을 두는 파일.

`POST /jobs` 는 시도마다 `submission: {id, cancel_token}` 을 준다. 그 비밀은 취소 권한이라 토큰과
같은 취급이다: `$XDG_STATE_HOME/rcm/submissions.json`(없으면 `~/.local/state/rcm/submissions.json`)
에 0600 으로 두고, 나중의 `rcm cancel N` 이 같은 서버·같은 잡의 항목을 꺼내 보낸다. 항목은
`{server, job_id, submission_id, cancel_token, role, token_fingerprint}` 이고 **제출마다** 한
줄이다. 지문은 bearer 토큰 SHA-256 의 앞 16자 — 세션의 토큰을 가르는 표지이지 비밀이 아니다
(서버도 전체 해시를 둔다).

같은 머신에서 두 세션이 한 잡에 얽힐 수 있다 — 요청자가 내고, 다른 토큰의 세션(또는 같은 토큰의
두 번째 세션)이 합류한다. 꺼낼 때는 **내 토큰 지문의 항목 중 그 잡의 가장 최근 제출**(= 이 상태
파일에서 이 토큰으로 마지막에 낸 것) 하나다; `submission_id` 를 주면 그것만. 다른 지문의 항목으로
넘어가는 폴백도, 지문 안에서 `cancel_job` 을 먼저 집는 순위도 없다(M5l S5 — 같은 토큰의 합류한
세션이 요청자의 `cancel_job` 을 집어 잡을 죽였다). 다른 토큰의 항목은 어차피 서버가 거절한다
(leave 는 참여자에 묶인다, S6). 요청자 세션이 같은 토큰의 나중 합류 항목을 집으면 「나가기」로
끝나 아무 일도 없다 — 안전한 쪽이고, `--submission-id` 가 고른다.

읽기 실패(없음 · 깨짐)는 「없음」이다 — 취소의 최종 판정은 서버가 하고, 없으면 그렇게 말한다.
쓰기는 `submissions.json.lock` 의 `fcntl.flock` 아래 read-modify-write 이고, 임시 파일은 pid
접미사 + `os.replace` 다(M5l S7 — 동시 `rcm run` 둘이 같은 `.tmp` 를 덮어 항목을 잃었다).
상한(`MAX_ENTRIES`)은 **끝난 잡**의 항목만 버린다 — 서버에 물어 모르면 남긴다(M5l S9: 도는 잡의
유일한 취소 권한을 버리지 않는다). 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

STATE_FILE = "submissions.json"
ROLE_CANCEL_JOB = "cancel_job"
ROLE_LEAVE_SUBMISSION = "leave_submission"
#: 이 수를 넘으면 끝난 잡의 항목부터 버린다(오래된 것부터). 끝났는지 모르는 항목은 남는다.
MAX_ENTRIES = 200
#: `remember(finished=…)` — `(server, job_id)` 가 끝났으면 True, 아니면 False, 모르면 None.
FinishedFn = Callable[[str, int], "bool | None"]


def state_path() -> Path:
    """`$XDG_STATE_HOME/rcm/submissions.json`, 없으면 `~/.local/state/rcm/submissions.json`."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "rcm" / STATE_FILE


def _normalize_server(server: str) -> str:
    return server.rstrip("/")


def fingerprint(token: str | None) -> str:
    """bearer 토큰의 지문(SHA-256 앞 16자). 토큰이 없으면 빈 문자열."""
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else ""


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """`<file>.lock` 에 `flock(LOCK_EX)` — 다른 프로세스의 read-modify-write 가 끝날 때까지
    기다린다. 락 파일은 남는다(지우면 다음 프로세스가 다른 inode 를 잠근다)."""
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # close 가 락도 푼다


def _write(path: Path, entries: list[dict[str, Any]]) -> None:
    """임시 파일(`<file>.<pid>.tmp`, 0600) 에 쓰고 `os.replace` — 락 안에서 부른다."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(entries, fh, indent=1)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.chmod(path, 0o600)


def _prune(entries: list[dict[str, Any]], finished: FinishedFn | None) -> list[dict[str, Any]]:
    """상한을 넘으면 오래된 것부터, **끝난 잡**의 항목만, 상한 아래로 내려갈 만큼만 버린다."""
    over = len(entries) - MAX_ENTRIES
    if over <= 0 or finished is None:
        return entries
    kept: list[dict[str, Any]] = []
    for e in entries:
        if over > 0 and isinstance(e.get("job_id"), int) and isinstance(e.get("server"), str):
            try:
                done = finished(e["server"], e["job_id"])
            except Exception:  # noqa: BLE001 — 모르면 남긴다
                done = None
            if done is True:
                over -= 1
                continue
        kept.append(e)
    return kept


def remember(
    server: str,
    job_id: int,
    submission_id: str,
    cancel_token: str,
    *,
    role: str,
    token_fingerprint: str = "",
    path: Path | None = None,
    finished: FinishedFn | None = None,
) -> None:
    """항목 하나를 덧붙인다(같은 서버·`submission_id` 의 옛 항목은 지운다). 디렉터리 0700 ·
    파일 0600. `token_fingerprint` 는 낸 세션의 토큰 지문(`fingerprint()`) — `lookup` 이 같은
    지문의 항목만 본다. 상한을 넘으면 `finished` 로 끝난 잡을 가려 그것만 버린다.

    실패는 `OSError` 로 올린다 — 부르는 쪽이 경고 한 줄로 바꾼다(잡은 이미 큐에 있다).
    """
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = _normalize_server(server)
    with _locked(path):
        entries = [
            e
            for e in _load(path)
            if not (e.get("server") == server and e.get("submission_id") == submission_id)
        ]
        entries.append(
            {
                "server": server,
                "job_id": job_id,
                "submission_id": submission_id,
                "cancel_token": cancel_token,
                "role": role,
                "token_fingerprint": token_fingerprint,
            }
        )
        _write(path, _prune(entries, finished))


def forget(server: str, submission_id: str, *, path: Path | None = None) -> bool:
    """쓴 비밀을 지운다(취소·나가기가 200 이면 더 통하지 않는다). 있었으면 True."""
    path = path or state_path()
    server = _normalize_server(server)
    try:
        with _locked(path):
            entries = _load(path)
            kept = [
                e
                for e in entries
                if not (e.get("server") == server and e.get("submission_id") == submission_id)
            ]
            if len(kept) == len(entries):
                return False
            _write(path, kept)
    except OSError:
        return False
    return True


def lookup(
    server: str,
    job_id: int,
    *,
    submission_id: str | None = None,
    token_fingerprint: str = "",
    path: Path | None = None,
) -> str | None:
    """같은 서버·같은 잡·같은 토큰 지문의 cancel token — `submission_id` 를 주면 그 항목(지문은
    안 본다: 명시한 것이 이긴다), 아니면 가장 최근 항목. 다른 지문의 항목으로는 넘어가지 않는다.
    없거나 파일이 깨졌으면 None."""
    found = _find(server, job_id, submission_id=submission_id, fp=token_fingerprint, path=path)
    return (found or {}).get("cancel_token")


def _find(
    server: str, job_id: int, *, submission_id: str | None, fp: str, path: Path | None
) -> dict[str, Any] | None:
    path = path or state_path()
    server = _normalize_server(server)
    found: dict[str, Any] | None = None
    for e in _load(path):
        if e.get("server") != server or e.get("job_id") != job_id:
            continue
        if submission_id is not None:
            if e.get("submission_id") != submission_id:
                continue
        elif (e.get("token_fingerprint") or "") != fp:
            continue
        tok = e.get("cancel_token")
        if not isinstance(tok, str) or not tok:
            continue
        found = e
    return found


def find(
    server: str,
    job_id: int,
    *,
    submission_id: str | None = None,
    token_fingerprint: str = "",
    path: Path | None = None,
) -> dict[str, Any] | None:
    """`lookup` 과 같되 항목 전체 — `rcm cancel` 이 쓴 뒤 `forget` 할 `submission_id` 를 안다."""
    return _find(server, job_id, submission_id=submission_id, fp=token_fingerprint, path=path)
