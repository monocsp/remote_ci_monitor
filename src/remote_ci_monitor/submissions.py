"""제출 capability 의 로컬 상태(M5j G5 · 결정 87) — `rcm run` 이 받은 cancel token 을 두는 파일.

`POST /jobs` 는 시도마다 `submission: {id, cancel_token}` 을 준다. 그 비밀은 취소 권한이라 토큰과
같은 취급이다: `$XDG_STATE_HOME/rcm/submissions.json`(없으면 `~/.local/state/rcm/submissions.json`)
에 0600 으로 두고, 나중의 `rcm cancel N` 이 같은 서버·같은 잡의 항목을 꺼내 보낸다. 항목은
`{server, job_id, submission_id, cancel_token, role, token_fingerprint}` 이고 최근 `MAX_ENTRIES`
개만 남긴다.

같은 머신에서 두 세션이 한 잡에 얽힐 수 있다 — 요청자가 내고, 다른 토큰의 세션(또는 같은 토큰의
두 번째 세션)이 합류한다. 그래서 항목은 잡당 하나가 아니라 **세션의 토큰 지문 · 역할**마다
하나이고, 꺼낼 때는 **내 토큰의 항목 → `cancel_job` → 최근 것** 순으로 고른다. 합류자의 `rcm
cancel` 이 요청자의 비밀을 집어 잡을 죽이거나, 요청자의 것이 합류자의 비밀을 집어 「나가기」로
끝나지 않게 하려는 것이다. 지문은 bearer 토큰 SHA-256 의 앞 16자다 — 세션을 가르는 표지이지
비밀이 아니다(서버도 전체 해시를 둔다).

읽기 실패(없음 · 깨짐)는 「없음」이다 — 취소의 최종 판정은 서버가 하고, 없으면 그렇게 말한다.
쓰기는 임시 파일 + `os.replace` 로 원자적이다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

STATE_FILE = "submissions.json"
ROLE_CANCEL_JOB = "cancel_job"
ROLE_LEAVE_SUBMISSION = "leave_submission"
#: 남기는 항목 수. 잡 하나에 한 줄이라 넉넉하다 — 그 뒤의 취소는 `--cancel-token` 으로.
MAX_ENTRIES = 200


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


def remember(
    server: str,
    job_id: int,
    submission_id: str,
    cancel_token: str,
    *,
    role: str,
    token_fingerprint: str,
    path: Path | None = None,
) -> None:
    """항목 하나를 덧붙인다(같은 서버·잡·지문·역할의 옛 항목은 지운다). 디렉터리 0700 · 파일 0600.

    실패는 `OSError` 로 올린다 — 부르는 쪽이 경고 한 줄로 바꾼다(잡은 이미 큐에 있다).
    """
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = _normalize_server(server)
    entries = [
        e
        for e in _load(path)
        if not (
            e.get("server") == server
            and e.get("job_id") == job_id
            and e.get("token_fingerprint") == token_fingerprint
            and e.get("role") == role
        )
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
    entries = entries[-MAX_ENTRIES:]
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(entries, fh, indent=1)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def lookup(
    server: str, job_id: int, *, token_fingerprint: str = "", path: Path | None = None
) -> str | None:
    """같은 서버·같은 잡의 cancel token — 내 토큰의 항목, 그중 `cancel_job`, 그중 최근 것.
    없거나 파일이 깨졌으면 None."""
    path = path or state_path()
    server = _normalize_server(server)
    best: tuple[int, int, int] | None = None
    found: str | None = None
    for index, e in enumerate(_load(path)):
        if e.get("server") != server or e.get("job_id") != job_id:
            continue
        tok = e.get("cancel_token")
        if not isinstance(tok, str) or not tok:
            continue
        rank = (
            1 if token_fingerprint and e.get("token_fingerprint") == token_fingerprint else 0,
            1 if e.get("role") == ROLE_CANCEL_JOB else 0,
            index,
        )
        if best is None or rank > best:
            best, found = rank, tok
    return found
