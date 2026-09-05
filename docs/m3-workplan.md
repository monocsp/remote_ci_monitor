# M3 작업 명세 — 운영 (git_ref · 보존 정리 · 서비스 · read_auth basic)

> PLAN.md 「M3 — 운영」의 구현 명세. **Codex 리뷰(`docs/reviews/2026-09-05-codex-m3-design.md`) 반영본** — 바뀐 곳은 「(리뷰 반영)」로 표시. 인터페이스를 먼저 못 박고, 테스트-퍼스트 서브에이전트가 이 문서만 보고 시나리오·엣지케이스를 쓴다. 완료 기준(PLAN): **배포 프리셋이 원격 ref 로 돌고, QA 두 개가 concurrency 그룹으로 직렬화된다.** 브랜치 `feat/m3-ops` → PR → `dev`.

## 0. 범위와 지금 상태

| PLAN M3 항목 | 지금(M2 까지) | M3 에서 |
|---|---|---|
| `git_ref` 소스 | 설정에 `[[repos]]` 파싱만. 제출은 400 「planned for M3」, `materialize.prepare_git_ref` 는 즉시 실패, CLI 는 `--source git_ref` 거부 | 서버가 원격 ref 를 sha 로 확정하고 로컬 미러로 fetch → 워크스페이스 체크아웃. 합류 신원은 sha. CLI `rcm run deploy --ref v1.2.3` |
| concurrency 그룹 | 큐 규칙·claim 배제·실기 7단계 PASS | **실제 프로세스 두 개**가 레인 2 에서도 겹치지 않는다는 e2e 를 잠근다 |
| 보존 정리 | 설정 키만 있고(`retention_days_*`) 아무것도 지우지 않는다 | 서버 안 청소 스레드(janitor)가 기간 지난 잡의 `jobs/<id>/`·`workspaces/<id>/` 를 지우고 DB 에 표시 |
| 타임아웃/취소 신호 | 프로세스 그룹 SIGTERM → grace → SIGKILL, 단위 테스트 있음 | 손자 프로세스까지 죽는지 · TERM 을 무시하는 스크립트가 grace 뒤 KILL 되는지 e2e 로 잠근다 |
| launchd/systemd | 없음 | `examples/launchd/*.plist` · `examples/systemd/*.service` + README 「Run as a service」. `rcm serve` 의 SIGTERM 은 이미 정상 종료(실행 중 잡 → lost) |
| macOS CI 잡 | `ci.yml` 에 macos-latest 3.13 이 M0 부터 있다 | 유지. git 테스트가 두 OS 에서 돈다 |
| `read_auth = basic` (결정 23) | `basic` = 읽기에도 bearer 필수 → 브라우저가 `/` 를 못 연다 | **진짜 HTTP Basic**: 사용자명 = 토큰 이름, 비밀번호 = 토큰. 자격 저장소를 새로 만들지 않는다 |

바꾸지 않는 것: 스키마 v1 의 기존 키 · `test` 잡 이름 · 바인드 기본 127.0.0.1 · 쓰기는 토큰 필수 · 프리셋만 실행 · argv 배열.

## 1. `git_ref` 소스 모드

### 1.1 설정

```toml
[[repos]]
name = "app"                          # 프리셋이 이 이름으로 가리킨다
url = "git@github.com:org/app.git"    # 어느 git 호스팅이든. 빌드 머신의 git 자격(ssh 키·credential helper)을 쓴다

[[presets]]
name = "deploy"
argv = ["bash", "scripts/deploy.sh"]
source_modes = ["git_ref"]            # tree 요청은 400
repo = "app"                          # [[repos]].name. git_ref 를 받는 프리셋은 필수 — 단, repos 가 정확히 하나면 생략 가능(그것으로 채운다)
timeout_seconds = 1800
concurrency_group = "deploy"

[server]
git_resolve_timeout_seconds = 20      # 제출 시 ls-remote 상한
git_fetch_timeout_seconds = 600       # 자재화(fetch · clone) 상한. 넘으면 failed + "git fetch timed out after 10m"
```

- `Preset.repo: str = ""` 추가. 검증(서버 시작 시, `ConfigError` 에 프리셋 이름·키 이름): `source_modes` 에 `git_ref` 가 있으면 `repo` 가 `[[repos]]` 의 이름과 일치해야 한다(repos 가 하나뿐이면 자동). `git_ref` 가 없는데 `repo` 가 있으면 오류(`preset 'gate': repo is only valid with source_modes git_ref`). `[[repos]]` 의 `url` 은 비어 있으면 안 되고 `-` 로 시작하면 안 된다(argv 옵션 주입 방지). `name` 은 `_NAME_RE`. 이름 중복은 오류.
- `[[repos]]` 가 하나라도 있으면 시작 시 `shutil.which("git")` 이 없을 때 `ConfigError("[[repos]] configured but git is not on PATH")`.
- (리뷰 반영) `[[repos]].url` 은 **허용 목록**: `https://` · `ssh://` · `git://` · `file://` · scp 형(`user@host:path`) · 절대 로컬 경로. 그 외(`ext::` 같은 원격 헬퍼 · `-` 시작 · 공백·제어문자)는 `ConfigError`. 규칙은 순수 `core/gitref.validate_repo_url`.
- 새 키: `[server] metadata_retention_days = 180`(잡 행·이벤트·합류자 삭제. `estimate.sample_days` · `retention_days_*` 이상이어야 한다) · `retention_sweep_interval_seconds = 3600`(하한 60).
- 프리셋 JSON(`/api/status.presets[]`·`rcm presets`)에 `repo` 를 싣는다(없으면 null).

### 1.2 순수 규칙 — `core/gitref.py` (I/O 없음)

```python
MAX_REF_LEN = 200
def validate_ref(ref: str) -> str          # 정규화된 ref. 틀리면 ValueError(사유 한 줄)
def is_full_sha(ref: str) -> bool          # 40 자리 소문자 hex
def pick_sha(ls_remote_output: str, ref: str) -> str | None
def short_sha(sha: str | None) -> str      # 앞 7자 또는 "—"
```

- `validate_ref`: 공백·제어문자 없음, 길이 ≤ 200, `-` 로 시작 금지(옵션 주입), `..`·`@{`·`\`·`^`·`:`·`?`·`*`·`[`·`~` 금지, `/` 로 시작·끝 금지, `.lock` 으로 끝 금지, 빈 문자열 금지 — `git check-ref-format` 의 규칙 부분집합. 40 hex 는 그대로 통과(소문자로 정규화).
- `pick_sha`: `git ls-remote -- <url> <ref>` 출력(`<sha>\t<refname>` 줄들)에서 고른다. 우선순위 ① `refs/heads/<ref>` ② `refs/tags/<ref>^{}`(annotated 태그가 가리키는 커밋) ③ `refs/tags/<ref>` ④ refname 이 `<ref>` 와 정확히 같은 줄(`refs/heads/x` 처럼 완전한 이름을 줬을 때 — `^{}` 변형이 있으면 그것). 없으면 None. `ref` 가 40 hex 면 출력과 무관하게 그 값을 돌려준다.

### 1.3 I/O — `gitops.py`

```python
class GitError(Exception): ...             # 메시지는 짧고 경로·URL 없음(잡 summary 에 실린다). 전체 stderr 는 로그로
def resolve_ref(url: str, ref: str, *, timeout: float, run=subprocess.run) -> str   # sha. 40 hex 면 ls-remote 없이 그대로
def ensure_mirror(mirror: Path, url: str, *, timeout: float, log: Callable[[str], None]) -> None  # 없으면 `git init --bare`, 있으면 그대로
def fetch_ref(mirror: Path, url: str, ref: str, *, timeout: float, log) -> None    # `git fetch --no-tags?` — 아래
def has_commit(mirror: Path, sha: str) -> bool                                       # `git cat-file -e <sha>^{commit}`
def checkout(mirror: Path, workspace: Path, sha: str, *, timeout: float, log) -> None
```

- 모든 호출은 argv 배열, 인자 앞에 `--`, `env={"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "PATH": ..., "HOME": ..., "SSH_AUTH_SOCK": ...(있으면)}`, `stdin=DEVNULL`, `timeout=` — 초과는 `GitError("git fetch timed out after Ns")`, 프로세스는 `start_new_session` + 프로세스 그룹 kill.
- 미러 경로 `<data_dir>/mirrors/<repo.name>/` (이름은 `_NAME_RE` 라 경로 안전). `ensure_mirror` 는 `git init --bare` + `gc.auto = 0`(미러가 객체를 지우지 않게). (리뷰 반영) `fetch_ref(mirror, url, ref, want_sha=)` 는 **ref 하나만 먼저** 받는다 — 완전한 refname 이면 그것, 아니면 `+refs/heads/<ref>:refs/heads/<ref>` → `+refs/tags/<ref>:refs/tags/<ref>`(`--no-tags`) — 그것으로 `want_sha` 가 미러에 오면 끝. 안 오면(또는 40 hex ref) 전체 `+refs/heads/*:refs/heads/* +refs/tags/*:refs/tags/*` `--prune` fetch 로 폴백. 같은 미러는 프로세스 안 `Lock` 으로 직렬화(레인 두 개가 같은 레포를 동시에 fetch 하면 ref 락 충돌). ref 가 40 hex 이고 이미 `has_commit` 이면 fetch 를 건너뛴다.
- 자재화 순서: `ensure_mirror` → `fetch_ref` → `has_commit(sha)` 아니면 `MaterializeError("commit <sha7> not found after fetch — ref moved or was force-pushed?")` → `checkout`: (리뷰 반영) `git clone -q --no-checkout -- <mirror> <workspace>`(**`--shared` 없음** — 로컬 clone 은 객체를 하드링크하므로 미러가 gc/prune 해도 워크스페이스가 안 깨진다) 뒤 `git -C <workspace> checkout -q --detach <sha>`(`--` 없음 — `--` 뒤는 pathspec 이다; sha 는 40 hex 로 검증됨). `.git` 이 남아 `git describe` 가 된다. submodule 은 하지 않는다(README 에 명시: 필요하면 프리셋 스크립트가 `git submodule update --init`).
- 로그: 자재화 중 `[rcm] fetching <ref> from <repo name>` · `[rcm] checked out <sha7>` 과 git 의 stderr 마지막 20줄을 잡 로그(`log.txt`)에 남긴다(토큰이 있어야 보는 곳). `GitError` 메시지에는 URL·경로·stderr 를 넣지 않는다 — `git fetch failed (exit 128) — see the job log`.

### 1.4 서버 — `POST /jobs` 의 git_ref 분기

요청 `{preset, inputs, source: {mode: "git_ref", ref}, requester_label, join}`.

1. `mode not in preset.source_modes` → 400(기존). `mode == "git_ref"` 인데 `preset.repo` 가 비어 있으면 500 이 아니라 시작 시 설정 검증이 막았어야 한다(방어적으로 400 `preset has no repo`).
2. `validate_ref(ref)` 실패 → 400 `source.ref: <사유>`. `ref` 가 없거나 문자열이 아니면 400.
3. `resolve_ref(repo.url, ref, timeout=git_resolve_timeout_seconds)` — 핸들러 스레드에서 돈다(DB 락 밖). (리뷰 반영) 동시에 도는 해석은 `BoundedSemaphore(2)` 로 제한 — 핸들러 32개가 20초짜리 원격 호출에 묶이지 않게; 못 얻으면 503. 실패 → **502** `cannot resolve '<ref>' in repo '<name>': <GitError>` (사유에 URL 없음), 타임아웃 → 504 `resolving '<ref>' timed out after 20s`. 40 hex 면 원격을 부르지 않는다.
4. `Source(mode="git_ref", repo=repo.name, ref=ref, sha=sha, base_sha=sha, dirty=False)` → `join_key(preset, inputs, sha)` 로 합류 판정(같은 sha 면 합류, ref 이름이 달라도 합류) → 없으면 `create_job(..., state=QUEUED)`(트리 업로드 없음, `queued_at = created_at`).
5. 응답 **201** `{job_id, joined: false, state: "queued", sha, url}` (`upload` 키 없음 — tree 경로와 같은 201). 합류면 200 + 기존과 같은 모양 + `sha`. 타임아웃 판정은 `GitTimeout(GitError)` 하위 클래스로(문자열 검사 아님).
6. `PUT /jobs/{id}/tree` 를 git_ref 잡에 하면 409 `job takes no tree upload`.

워커: `execute` 의 자재화 분기 — `prepare_git_ref(job, workspace, repo=cfg.repo(job.source.repo), mirror=data_dir/"mirrors"/name, timeout=git_fetch_timeout_seconds, log=append_to_log)`. 제출 시점의 repo 이름이 설정에서 사라졌으면 `failed` + `repo 'app' is no longer configured`. env 에 `RCM_REF=<ref>` 추가, `RCM_BASE_SHA=<sha>`, `RCM_DIRTY=0`.

### 1.5 CLI

- `rcm run PRESET [--ref REF] [--source tree|git_ref]`: `--source` 기본값을 없앤다(None). 결정 규칙: `--ref` 가 있거나 프리셋의 `source_modes == ["git_ref"]` 면 `git_ref`, 아니면 `tree`. `git_ref` 인데 `--ref` 가 없으면 usage 2 `preset 'deploy' needs --ref`. `--ref` 를 줬는데 프리셋이 git_ref 를 안 받으면 usage 2. `--source tree` 를 명시했는데 프리셋이 tree 를 안 받으면 usage 2(기존).
- git_ref 면 스냅샷·업로드를 건너뛰고 ③ 제출 → ⑤ wait. stderr 에 `submitted job #N (deploy · main @a1b2c3d)`. stdout JSON(`--no-wait`)에 `ref`·`sha` 가 들어간다(서버 응답의 `sha`).
- `rcm run` 은 `--ref` 값에 `validate_ref` 를 먼저 적용해 서버에 보내기 전에 usage 2 로 끝낸다.
- `rcm eta deploy --ref main` 은 지원하지 않는다(ETA 는 소스와 무관 — `rcm eta deploy` 로 충분). `rcm jobs`·`rcm top`·웹은 git_ref 행을 `app · ref main` 과 `@a1b2c3d` 로 보인다(`render_text` 148 · `app.js sourceHtml` 이미 있음 — 테스트로 잠근다).

## 2. 보존 정리 — janitor

### 2.1 순수 규칙 — `core/retention.py`

```python
@dataclass(frozen=True)
class RetentionPolicy: success_days: int; failure_days: int
def retention_seconds(state: str, policy: RetentionPolicy) -> float | None   # succeeded → success_days*86400 · failed/timed_out/cancelled/lost → failure_days*86400 · 활성 상태 → None(절대 안 지움)
def due_for_purge(jobs: Iterable[Job], now: datetime, policy: RetentionPolicy) -> list[Job]
```

- `due_for_purge`: `finished_at` 이 있고 `now - finished_at >= retention` 인 잡. `artifacts_purged_at` 이 이미 있으면 제외. 활성 상태(`uploading·queued·running·cancelling`)는 `finished_at` 이 어떻게 찍혀 있어도 제외. `finished_at` 이 None 인 종료 잡(있으면 안 되지만)은 `created_at` 기준. days 가 0 이면 「끝나자마자 다음 sweep 에」, 음수는 설정 검증이 막는다. 경계는 `>=`.
- `Job` 에 `artifacts_purged_at: datetime | None = None` 필드 추가. DB 마이그레이션 `user_version 1 → 2`: `ALTER TABLE jobs ADD COLUMN artifacts_purged_at REAL`.

### 2.2 I/O — `janitor.py`

```python
class Janitor:
    def __init__(self, store, config, *, now_fn, on_error: Callable[[str], None], log: Callable[[str], None]): ...
    def sweep_once(self, now: datetime | None = None) -> int     # 지운 잡 수. 예외는 잡별로 삼키고 on_error 로 보고
    def start(self) -> None   # 스레드: 시작 직후 한 번, 이후 retention_sweep_interval_seconds 마다
    def stop(self) -> None
```

- `store.list_unpurged_finished(limit=1000) -> list[Job]`(finished_at 오름차순) · `store.mark_artifacts_purged(job_ids, now)`.
- 잡별로 `<data_dir>/jobs/<id>/` 와 `<data_dir>/workspaces/<id>/` 를 지운다. 경로는 **정수 id 로만** 만든다. 둘 중 없는 것은 건너뛴다(이미 손으로 지웠어도 표시는 한다). (리뷰 반영) `lstat` 으로 **심볼릭 링크면 링크만 `unlink`** 하고 따라가지 않는다; 디렉터리면 `resolve()` 가 data_dir 아래인지 확인하고 `rmtree`(프리셋 스크립트가 워크스페이스를 바꿔치기했을 때 밖을 지우지 않는다). 삭제가 실패하면 그 잡은 표시하지 않고(다음 sweep 에 다시) `on_error("retention: job 12: <errno 이름>")`.
- 지운 뒤 `mark_artifacts_purged`(UPDATE 조건에 `state IN terminal AND artifacts_purged_at IS NULL` — 삼중 가드). (리뷰 반영) DB 행은 영구 보존하지 않는다: 산출물이 지워진 종료 잡 중 `metadata_retention_days`(180) 지난 것은 `store.delete_old_jobs(cutoff)` 가 잡 행·이벤트·합류자를 함께 지운다. `sample_days`(45) 보다 길어야 중앙값이 계속 맞는다(설정 검증). `GET /jobs/{id}/log` 는 종료 잡의 파일이 없으면 404 `log expired` (아직 시작 전이면 빈 본문). 웹의 최근 완료 행은 그대로 보인다.
- 활성 잡의 디렉터리는 절대 건드리지 않는다(순수 규칙이 이미 걸러도 janitor 가 한 번 더 `job.is_terminal` 을 확인한다 — 이중 안전).
- `mirrors/` 는 지우지 않는다.
- 설정: `[server] retention_sweep_interval_seconds = 3600`(하한 60). 서버 시작 시 `App.start()` 가 recover 뒤에 `App.retention = Janitor(...)` 를 띄우고 `shutdown()` 에서 내린다. (리뷰 반영) 스레드가 죽으면 `Janitor.dead = "janitor died: <예외 이름>"` · `server.last_error` 같은 문구 · `/api/health` 503 `{janitor: false, error: "janitor died: …"}`; 살아 있어도 `last_sweep_at` 이 주기의 2배보다 오래됐으면 503 `janitor stale`. (fail-open 금지 — 조용히 멈추지 않는다.)
- `Janitor` 는 `last_sweep_at`·`purged_total` 을 갖고 서버 로그(`app.log`)에 `retention: purged N jobs` · `retention: deleted N job records` 를 남긴다(N > 0 일 때만). 상태 JSON 은 바꾸지 않는다(`/api/health` 에 `janitor: bool` 만 추가).

## 3. 신호·그룹 e2e (기존 구현을 잠근다)

`tests/test_e2e_m3.py`(실제 프로세스, 루프백 서버 또는 워커 직접):

- **그룹 직렬화**: `lanes = 2`, `concurrency_group = "devices"` 프리셋 두 잡을 연달아 넣는다. 스크립트는 `date +%s.%N`(macOS 는 `python3 -c 'import time;print(time.time())'`)을 시작·끝에 파일로 찍고 1초 잔다. 두 번째의 시작 시각 ≥ 첫 번째의 끝 시각. 같은 시간에 그룹 없는 세 번째 잡은 두 번째 레인에서 **병행**된다(레인 2 가 놀지 않는다).
- **손자까지 죽는다**: `sh -c 'sleep 300 & echo $! > pidfile; wait'` 를 취소 → `cancelled` 뒤 pidfile 의 pid 에 `os.kill(pid, 0)` 이 `ProcessLookupError`(잠깐의 zombie 는 `psutil` 없이 `/bin/ps -p` 로 상태 확인).
- **TERM 무시 → KILL**: `trap '' TERM; sleep 300` 을 `grace_seconds = 1` 로 취소 → 3초 안에 `cancelled`, `cancel.kill_at` 이 요청 + 1s.
- **타임아웃도 같은 경로**: `timeout_seconds = 1` + TERM 무시 → `timed_out` + `summary "limit 1s"`.

## 4. 서비스 파일

- `examples/launchd/com.remote-ci-monitor.server.plist`: `Label` · `ProgramArguments` `[/Users/rcm/.local/bin/rcm, serve, --config, /Users/rcm/.config/rcm/server.toml]` · `RunAtLoad` · `KeepAlive` (`SuccessfulExit false`) · (리뷰 반영) `StandardOutPath`/`StandardErrorPath` 는 **절대 경로** `/Users/rcm/Library/Logs/rcm/server.log`(launchd 는 `~` 를 안 푼다) · `EnvironmentVariables.PATH` (Homebrew 경로 포함 — 프리셋이 부르는 도구가 여기 있어야 한다) · `ThrottleInterval 10` · `WorkingDirectory`. 주석으로 「전용 사용자 · `launchctl bootstrap gui/$(id -u)` · 잠자기 금지 `pmset`」.
- `examples/systemd/rcm-server.service`: `[Unit] After=network-online.target` · `[Service] User=rcm` · `WorkingDirectory=/home/rcm` · `ExecStart=/home/rcm/.local/bin/rcm serve --config …` · `Restart=on-failure` · `RestartSec=5` · `KillSignal=SIGTERM` · `KillMode=mixed` · `TimeoutStopSec=30` · `LimitNOFILE=4096` · `Environment=PYTHONUNBUFFERED=1` · `NoNewPrivileges=true` · `PrivateTmp=true` · (리뷰 반영) `ProtectSystem=strict` + `ReadWritePaths=/home/rcm`(`ProtectHome` 은 데이터·설정이 홈에 있어 쓰지 않는다) · `[Install] WantedBy=multi-user.target`.
- 둘 다 `tests/test_examples.py` 로 잠근다: plist 는 `plistlib.loads` 로 파싱되고 `ProgramArguments[0:2]` 가 `rcm serve` 이며 `KeepAlive`·`RunAtLoad` 가 있다. unit 은 `[Service]` 에 `ExecStart=` 가 `rcm serve` 로 시작하고 `Restart=` 가 있다. `examples/server.toml` 은 `load_server_config` 로 읽힌다(이미 테스트 있으면 유지) — M3 에서 `[[repos]]` + `deploy` 프리셋(주석 처리 예시)을 추가한다.
- README 「Run as a service」: 두 OS 절차 5줄씩 + 「서버 SIGTERM = 실행 중 잡 lost」 + 잠자기 금지.

## 5. `read_auth = basic` (결정 23 확정안)

- 의미: 읽기(`/` · `/static/*` · `/api/status` · `/events` · `/api/eta` · `GET /jobs/{id}` · `/jobs/{id}/events` · `/jobs/{id}/log` · `/api/whoami`)에 **자격이 필요**하다. 자격은 둘 중 하나: `Authorization: Bearer <token>`(기존) 또는 `Authorization: Basic base64(<token name>:<token>)`. Basic 은 `read_auth = "basic"` 일 때만 받는다(`none` 모드에서 Basic 헤더는 무시 → 401 이 아니라 그냥 익명 읽기).
- 검증(`App.authenticate_read`): `store.verify_token(password)` 가 `TokenInfo` 를 주고 `info.name == username`(`compare_digest`) 일 때만 통과. 읽기 라우트의 401 은 basic 모드에서 언제나 `WWW-Authenticate: Basic realm="rcm", charset="UTF-8"`(브라우저 프롬프트). (리뷰 반영) **쓰기(`POST /jobs` · `PUT tree` · `/cancel` · `/pause` · `/resume`)는 Bearer 만** — 브라우저가 Basic 자격을 자동으로 붙이므로 쓰기에 허용하면 내부망의 아무 페이지가 잡을 넣거나 취소할 수 있다(CSRF). 쓰기 401 은 기존 `Bearer realm="rcm"`.
- `/api/health` 는 계속 인증 없음(모니터링용, 내용에 비밀 없음). `/api/whoami` 는 Basic 도 받는다(UI 가 토큰 확인에 쓴다).
- 웹 UI 는 바꾸지 않는다: 브라우저가 Basic 자격을 캐시해 `<script src>`·`EventSource`·`fetch` 에 자동으로 붙인다. UI 가 저장 토큰으로 `Bearer` 를 붙이면 그것이 우선(둘 다 같은 토큰).
- Basic 은 평문이라 README 에 「TLS 프록시(Tailscale HTTPS·Caddy) 뒤에서만 · `bind` 가 루프백이 아니고 `read_auth = none` 이면 기존 경고 유지」.
- 기존 테스트 `test_read_auth_basic_guards_events_and_eta` 는 bearer 로 통과하므로 유지된다.

## 6. mutcheck 추가

- ⑦ `retention-active-guard` — `core/retention.py` 의 활성 상태를 걸러내는 두 줄을 없애는 변이 → `tests/test_retention.py` 가 빨개져야 한다.
- ⑧ `gitref-leading-dash` — `core/gitref.py` `validate_ref` 의 「`-` 로 시작 금지」 두 줄을 없애는 변이 → `tests/test_gitref.py` 가 빨개져야 한다.

## 7. 테스트 배치(서브에이전트 분담)

| 파일 | 담당 | 무엇 |
|---|---|---|
| `tests/test_gitref.py` · `tests/test_retention.py` | A | 순수 규칙 전부(경계·주입·우선순위) |
| `tests/test_gitops.py` · `tests/test_worker_gitref.py` | A | 임시 bare 레포(커밋 3 · 브랜치 2 · annotated/lightweight 태그) 로 resolve·mirror·fetch·checkout·ref 이동·강제 push·타임아웃(느린 `run` 스텁)·워커 자재화·env·로그·실패 문구(경로 없음) |
| `tests/test_janitor.py` · `tests/test_store.py`(추가) | B | 마이그레이션 1→2 · sweep 삭제/표시/활성 보호/실패 재시도/idempotent · 로그 404 문구 · 스레드 죽음 → last_error/health |
| `tests/test_server_m3.py` · `tests/test_cli_m3.py` | B | git_ref 제출(400·502·504·합류·409 tree)·Basic 인증 전 경로·CLI 결정 규칙·JSON 출력 |
| `tests/test_e2e_m3.py` | B | 3절 |
| `tests/test_examples.py` · `tests/web/source.test.js` · `tests/test_config.py`(추가) | C | 서비스 파일 · 웹 source 셀(git_ref) · 설정 검증(repo·타임아웃·sweep 하한·git 없음) |

규칙: 서브에이전트는 `src/` 를 건드리지 않는다. 구현이 없어 import 가 실패하는 테스트는 그대로 둔다(빨간 채로 인계). `git` 이 없으면 `pytest.skip`. 실제 원격을 부르지 않는다(모든 URL 은 tmp 의 bare 레포 경로). 각자 가정을 `docs/m3-test-scenarios-<담당>.md` 에 적는다.

## 8. 순서

① 이 명세 → Codex 리뷰(`docs/reviews/2026-09-05-codex-m3-design.md`) 반영 ② 테스트-퍼스트 A·B·C 병행 ③ 구현(모듈별 커밋: config → core → gitops/materialize → store/janitor → server → cli → examples/README → mutcheck) ④ 격리 워크트리 에이전트가 pytest · node · ruff · mutcheck 8/8 · e2e 를 돌리고 수정 ⑤ PR → CI → 머지 ⑥ PLAN 갱신(M3 완료 · 결정 23 확정 · 설정 키 · 스키마 `presets[].repo`).
