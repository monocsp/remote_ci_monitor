"""릴리스 프로파일의 비밀 저장소 — `<config_dir>/secrets/<repo>/` (폴더 0700 · 파일 0600).

계약(docs/release-contract.md §4 · 스토어 탭 API v1):

- 비밀 하나가 파일 하나다. `kind = "value"` 는 입력한 문자열이 파일이 되고, `"file"` 은 올린
  바이트 그대로, `"dir"` 은 프로파일의 `files` 이름으로 파일 여럿을 가진 폴더다.
- **값은 이 모듈 밖으로 나가지 않는다.** 응답에 싣는 것은 이름 · 모양 · 있음 · 크기 · 지문 ·
  검증 시각뿐이다. 예외 문구에도 값 · 절대 경로를 넣지 않는다(응답 `error` 로 나간다).
- 이름은 프로파일에 적힌 것과 **글자까지 같아야** 한다 — 경로는 이름을 이어 붙여 만들 뿐 정규화
  하지 않으므로, 프로파일 밖의 이름은 파일 시스템에 닿기 전에 거절된다.
- 쓰기는 같은 폴더의 임시 파일에 쓰고 `os.replace` 로 바꾼다 — 반쯤 쓰인 비밀이 보이지 않게.
- 검증 결과(`verified_at` · `verify_error` · `verify_detail`)는 `.verify.json` 한 파일에 둔다.
  값을 바꾸면 그 비밀의 기록은 지운다 — 옛 값의 검증이 새 값을 보증하지 않는다.
- 잡 stdout 마스킹: `kind = "value"` 인 비밀 중 8자 이상은 로그에서 `****` 로 바뀐다
  (`mask_bytes`). 짧은 값은 흔한 낱말과 겹쳐 로그를 못 읽게 만들므로 두지 않는다 — 계약이
  8자를 문턱으로 못 박았다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor.config import ReleaseProfile, ReleaseSecret, ServerConfig
from remote_ci_monitor.core.model import Preset

VERIFY_FILE = ".verify.json"
MASK = b"****"
MASK_MIN_LENGTH = 8
#: `kind = "value"` 본문 상한 — 토큰 · 웹훅 URL 은 몇백 바이트다. 파일은 프로파일의 `max_kb`.
VALUE_MAX_BYTES = 64 * 1024
_TMP_PREFIX = ".tmp-"


class SecretError(Exception):
    """저장소가 거절했다. `status` 는 HTTP 코드, 문구에는 값도 경로도 없다."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def secrets_dir(config_path: Path | None, repo: str) -> Path | None:
    """`<config_dir>/secrets/<repo>/` — 설정 파일 옆(계약 §4). 설정 파일이 없으면(메모리 설정)
    비밀 둘 곳도 없다 → None."""
    if config_path is None:
        return None
    return Path(config_path).parent / "secrets" / repo


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SecretStore:
    """저장소 하나 = 프로파일 하나 + 폴더 하나. 폴더는 첫 쓰기 때 만든다(`rcm check` 의 warn 이
    「Settings 가 만든다」고 말하는 그 순간)."""

    def __init__(
        self,
        root: Path | None,
        profile: ReleaseProfile,
        *,
        now_fn: Callable[[], datetime] = _utcnow,
    ):
        self.root = root
        self.profile = profile
        self.now_fn = now_fn

    # ── 이름 → 선언 ──────────────────────────────────────────────────────

    def secret(self, name: str) -> ReleaseSecret:
        """프로파일에 적힌 그 이름만. 비교는 글자 그대로 — 정규화도 대소문자 무시도 없다."""
        for sec in self.profile.secrets:
            if sec.name == name:
                if name == VERIFY_FILE or name.startswith(_TMP_PREFIX):
                    raise SecretError(400, "that secret name is reserved by rcm")
                return sec
        raise SecretError(404, "no such secret in this repository's profile")

    def _path(self, sec: ReleaseSecret, file_name: str | None = None) -> Path:
        # 설정 파일이 없는 서버(root None)는 읽기에서 「없음」이고 쓰기는 `_need_root` 가 503 을
        # 낸다 — 여기서는 어디에도 닿지 않는 경로를 돌려준다
        root = self.root if self.root is not None else Path(os.devnull) / "rcm-no-config"
        if sec.kind == "dir":
            if file_name is None:
                raise SecretError(400, "a dir secret takes /<secret-name>/<file-name>")
            if file_name not in sec.files:
                raise SecretError(400, "file name is not in the profile's files list")
            return root / sec.name / file_name
        if file_name is not None:
            raise SecretError(400, "only a dir secret takes a file name")
        return root / sec.name

    # ── 읽기(값은 절대 안 나간다) ─────────────────────────────────────────

    def item(self, sec: ReleaseSecret) -> dict[str, Any]:
        """API 의 항목 하나. `present` 는 kind 별로: value/file 은 파일이 있고 비어 있지 않음,
        dir 은 `files` 전부가 있음."""
        doc: dict[str, Any] = {
            "name": sec.name,
            "kind": sec.kind,
            "optional": sec.optional,
            "verify": sec.verify,
            "present": False,
            "size": None,
            "fingerprint": None,
        }
        if sec.kind == "dir":
            files = []
            total = 0
            for f in sec.files:
                st = self._stat(self._path(sec, f))
                files.append({"name": f, "present": st is not None})
                total += st if st is not None else 0
            n = sum(1 for f in files if f["present"])
            doc["files"] = files
            doc["present"] = bool(files) and n == len(files)
            doc["size"] = total if n else None
            doc["fingerprint"] = f"{n}/{len(files)} files"
        else:
            path = self._path(sec)
            size = self._stat(path)
            if size:
                doc["present"] = True
                doc["size"] = size
                doc["fingerprint"] = self._fingerprint(sec, path)
        rec = self._verify_records().get(sec.name, {})
        doc["verified_at"] = rec.get("verified_at") if doc["present"] else None
        doc["verify_error"] = rec.get("verify_error") if doc["present"] else None
        doc["verify_detail"] = rec.get("verify_detail") if doc["present"] else None
        return doc

    def items(self) -> list[dict[str, Any]]:
        return [self.item(sec) for sec in self.profile.secrets]

    def view(self) -> dict[str, Any]:
        """`GET …/secrets` 본문."""
        return {"dir_env": self.profile.secrets_dir_env, "items": self.items()}

    def setup(self, *, with_missing: bool = True) -> dict[str, Any]:
        """설정 게이트의 셈. 필수(비-optional) 비밀만 센다 — `complete` 는 전부 있고 검증까지
        끝났을 때(verify 가 none 이면 있는 것으로 끝)."""
        required = present = verified = 0
        missing: list[str] = []
        for sec in self.profile.secrets:
            if sec.optional:
                continue
            required += 1
            doc = self.item(sec)
            if doc["present"]:
                present += 1
                if sec.verify == "none" or (doc["verified_at"] and not doc["verify_error"]):
                    verified += 1
            elif sec.kind == "dir":
                missing.extend(f"{sec.name}/{f['name']}" for f in doc["files"] if not f["present"])
            else:
                missing.append(sec.name)
        out: dict[str, Any] = {
            "required": required,
            "present": present,
            "verified": verified,
            "complete": present == required and verified == required,
        }
        if with_missing:
            out["missing"] = missing
        return out

    def is_complete(self) -> bool:
        return bool(self.setup(with_missing=False)["complete"])

    def mask_values(self) -> tuple[bytes, ...]:
        """로그에서 지울 값들 — `kind = "value"` 이고 8자 이상. 긴 것부터(짧은 값이 긴 값의
        일부일 때 긴 것을 먼저 지워야 조각이 안 남는다)."""
        if self.root is None:
            return ()
        out: list[bytes] = []
        for sec in self.profile.secrets:
            if sec.kind != "value":
                continue
            try:
                data = self._path(sec).read_bytes()
            except OSError:
                continue
            if len(data) >= MASK_MIN_LENGTH:
                out.append(data)
        return tuple(sorted(set(out), key=len, reverse=True))

    # ── 쓰기 ─────────────────────────────────────────────────────────────

    def put(
        self,
        name: str,
        data: bytes,
        *,
        content_type: str,
        file_name: str | None = None,
    ) -> dict[str, Any]:
        """비밀 하나를 쓴다. 본문 검사 → 임시 파일 → 0600 → rename. 값을 바꾸면 검증 기록을
        지운다."""
        sec = self.secret(name)
        mime = content_type.split(";", 1)[0].strip().lower()
        if sec.kind == "value":
            if mime != "text/plain":
                raise SecretError(400, "a value secret takes Content-Type: text/plain")
            if len(data) > VALUE_MAX_BYTES:
                raise SecretError(413, f"value larger than {VALUE_MAX_BYTES} bytes")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise SecretError(400, "value must be UTF-8 text") from None
            text = text.rstrip("\r\n")
            if not text or "\n" in text or "\r" in text:
                raise SecretError(400, "value must be one non-empty line")
            data = text.encode("utf-8")
        elif sec.kind == "file":
            if mime != "application/octet-stream":
                raise SecretError(400, "a file secret takes Content-Type: application/octet-stream")
            if len(data) > sec.max_kb * 1024:
                raise SecretError(413, f"file larger than max_kb = {sec.max_kb}")
            if not data:
                raise SecretError(400, "file must not be empty")
        else:  # dir
            if mime not in ("application/octet-stream", "text/plain"):
                raise SecretError(400, "a dir file takes application/octet-stream or text/plain")
            if len(data) > VALUE_MAX_BYTES:
                raise SecretError(413, f"file larger than {VALUE_MAX_BYTES} bytes")
            if not data:
                raise SecretError(400, "file must not be empty")
        path = self._path(sec, file_name)
        self._need_root()  # 본문이 통과한 뒤에야 폴더를 만든다
        self._write_atomic(path, data)
        self._record_verify(sec.name, None)
        return self.item(sec)

    def delete(self, name: str) -> dict[str, Any]:
        """선택(`optional`) 비밀만 지울 수 있다 — 필수는 새 값으로 덮는 길뿐이다(게이트가 다시
        닫히는 것을 실수로 만들지 않게)."""
        sec = self.secret(name)
        if not sec.optional:
            raise SecretError(409, "only an optional secret can be deleted; overwrite it instead")
        if self.root is not None:
            try:
                if sec.kind == "dir":
                    for f in sec.files:
                        _unlink(self.root / sec.name / f)
                    _rmdir(self.root / sec.name)
                else:
                    _unlink(self.root / sec.name)
            except OSError:
                raise SecretError(503, "could not delete the secret file") from None
            self._record_verify(sec.name, None)
        return self.item(sec)

    def record_verify(
        self, name: str, error: str | None, *, detail: str | None = None
    ) -> dict[str, Any]:
        """검증 한 번의 결과. 실패도 시각과 함께 남긴다 — 「언제 마지막으로 시도했나」가 화면에
        필요하고, `complete` 는 오류 없는 기록만 센다."""
        sec = self.secret(name)
        self._need_root()
        self._record_verify(
            sec.name,
            {"verified_at": _iso(self.now_fn()), "verify_error": error, "verify_detail": detail},
        )
        return self.item(sec)

    def value_path(self, name: str) -> Path | None:
        """검증기가 읽을 파일(value/file) 또는 폴더(dir). 없으면 None. 값은 검증기 안에서만 산다."""
        sec = self.secret(name)
        if self.root is None:
            return None
        path = self.root / sec.name
        return path if path.exists() else None

    # ── 파일 시스템 ──────────────────────────────────────────────────────

    def _need_root(self) -> None:
        if self.root is None:
            raise SecretError(
                503, "this server has no config file, so it has nowhere to keep secrets"
            )
        try:
            self.root.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.root.parent, 0o700)
            self.root.mkdir(exist_ok=True)
            os.chmod(self.root, 0o700)
        except OSError:
            raise SecretError(503, "could not create the secrets folder") from None

    @staticmethod
    def _stat(path: Path) -> int | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_size if st.st_size > 0 else None

    @staticmethod
    def _fingerprint(sec: ReleaseSecret, path: Path) -> str | None:
        """value 는 앞 4글자 + `…`(어느 토큰인지 알아보는 데 충분하고 그 이상은 없다), file 은
        내용 sha256 의 앞 8자리."""
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if sec.kind == "value":
            return data.decode("utf-8", errors="replace")[:4] + "…"
        return hashlib.sha256(data).hexdigest()[:8]

    def _write_atomic(self, path: Path, data: bytes) -> None:
        assert self.root is not None
        try:
            if path.parent != self.root:
                path.parent.mkdir(exist_ok=True)
                os.chmod(path.parent, 0o700)
            fd, tmp = tempfile.mkstemp(prefix=_TMP_PREFIX, dir=path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except BaseException:
                _unlink(Path(tmp))
                raise
        except OSError:
            raise SecretError(503, "could not write the secret file") from None

    def _verify_records(self) -> dict[str, dict[str, Any]]:
        if self.root is None:
            return {}
        try:
            raw = json.loads((self.root / VERIFY_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _record_verify(self, name: str, record: dict[str, Any] | None) -> None:
        if self.root is None:
            return
        records = self._verify_records()
        if record is None:
            if name not in records:
                return
            records.pop(name)
        else:
            records[name] = record
        self._write_atomic(
            self.root / VERIFY_FILE, json.dumps(records, indent=1, sort_keys=True).encode()
        )


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _rmdir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


# ── 마스킹 ───────────────────────────────────────────────────────────────


def mask_bytes(data: bytes, values: Iterable[bytes]) -> bytes:
    """`values` 가 나오는 자리를 `****` 로. 호출자는 줄 단위 배치를 넘긴다 — 두 배치에 걸친
    값은 못 지우므로 러너는 개행 단위로 모아 넘긴다(한 값이 줄을 넘는 일은 없다)."""
    for v in values:
        if v and v in data:
            data = data.replace(v, MASK)
    return data


def masker(values: Iterable[bytes]) -> Callable[[bytes], bytes] | None:
    """값이 없으면 None — 러너가 마스킹 호출 자체를 건너뛴다."""
    vals = tuple(v for v in values if v)
    if not vals:
        return None
    return lambda data: mask_bytes(data, vals)


def store_for(config: ServerConfig, repo_name: str | None) -> SecretStore | None:
    """프리셋 · 라우트가 가리키는 저장소의 비밀 저장소. 프로파일이 없으면 None."""
    repo = config.repo(repo_name)
    if repo is None or repo.release is None:
        return None
    return SecretStore(secrets_dir(config.path, repo.name), repo.release)


def job_secrets(
    config: ServerConfig, preset: Preset
) -> tuple[dict[str, str], Callable[[bytes], bytes] | None]:
    """잡 하나가 받는 것 — `{<secrets_dir_env>: <폴더>}`(프로파일에 변수 이름이 있고 폴더가
    **있을 때만**) 과 stdout 마스킹 함수(value 비밀이 없으면 None)."""
    store = store_for(config, preset.repo)
    if store is None:
        return {}, None
    env: dict[str, str] = {}
    if store.profile.secrets_dir_env and store.root is not None and store.root.is_dir():
        env[store.profile.secrets_dir_env] = str(store.root)
    return env, masker(store.mask_values())
