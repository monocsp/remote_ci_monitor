# M5q 작업 명세: 클라이언트가 서버 버전을 따라간다

> 2026-11-19 오너 요청 원문: 「네가 이 버전 업데이트 하면, 다른 세션 다른 컴퓨터에서 이 rcm 을 바라보고
> 있다면, 버전을 고정해서 쓰는 게 아니라 이 rcm 의 프로덕트 버전을 가져와서 그걸 그대로 쓰도록 하는 건
> 어때? 여기는 버전 업데이트 되었는데 다른 세션들에서 버전을 올려야 하는 게 좀 그래서. 물론 버전 명시하면
> 그 버전을 계속 쓰는 것도 가능하게 하는 건 좋아 보인다.」
>
> 결정 번호는 147~172 다(M5p 가 125~146 을 쓴다). 전부 추천값으로 구현하고 오너 확인을 기다린다.
> 오너가 꼭 봐야 할 둘은 결정 147(기본으로 켠다)과 167(평문 HTTP 로 받은 코드를 실행한다)이다.
>
> 구현 중에 바뀐 것은 이 문서의 §12 「구현하며 바뀐 것」에 남긴다.

## 0. 한 줄

서버에 붙는 `rcm` 명령은 그 서버가 돌리는 버전의 코드로 돈다. 설치된 `rcm` 은 서버 버전을 확인하고 다르면
서버가 주는 wheel 을 받아 캐시에 풀고 같은 인터프리터로 자기를 그 코드로 다시 실행한다. 설치본은 이제
부트로더 역할만 한다. 버전을 고정하고 싶은 사람은 환경 변수 하나로 고정한다.

## 1. 실측

### 1.1 지금 클라이언트가 몇 버전인지 알 수 있나 (2026-11-19, 운영 DB `sqlite3 -readonly`)

결론부터 적는다. 세션 머신의 클라이언트 버전은 어디에도 기록되지 않는다. 그래서 「지금 몇 대가 몇 버전에
머무는지」는 운영 데이터로 답할 수 없다.

| 본 곳 | 내용 | 클라이언트 버전 |
|---|---|---|
| `jobs` (DB v23, 3,789건, 2026-09-07 ~ 11-19) | 컬럼에도, `source_json` 의 키 11개(`mode` `repo` `branch` `base_sha` `dirty` `tree_hash` `bytes` `ref` `sha` `cached_bytes` `uploaded_bytes`)에도 없다 | 기록 없음 |
| `submissions` | `job_id` `submission_id` `role` `capability_hash` `created_at` `participant` | 기록 없음 |
| `events` | `kind` 는 `state` 와 `marker` 둘뿐이다 | 기록 없음 |
| 요청 헤더 | 클라이언트는 모든 요청에 `User-Agent: rcm/<버전>` 을 싣는다 | 서버는 `debug` 일 때만 접근 로그를 쓴다. `~/Library/Logs/rcm/server.log` 에 없다 |
| `workers.version` | 원격 워커는 등록할 때 버전을 보낸다 | `mac2-worker` 하나, 0.6.1. 서버와 같은 머신이다 |

최근 30일 제출을 낸 토큰 이름은 셋이다. 토큰 값은 읽지 않았다.

| 토큰 이름 | 제출 |
|---|---|
| `macbook` | 1,364 |
| `owner` | 75 |
| `macstudio` | 24 |

### 1.2 뒤처진 증거 (DB 밖)

DB 에는 없지만 사람이 남긴 기록은 둘 있다.

| 날짜 | 서버 | 클라이언트 | 출처 |
|---|---|---|---|
| 2026-09-10 | 0.2.5 | 노트북 0.2.2 | `docs/gate-replay-fixes-workplan.md` §3 I8 |
| 2026-11-18 | 0.5.0 | 맥북 0.4.4 | dolomood-renew `scripts/remote_ci.sh`(origin/dev) 의 `RCM_MIN_VERSION` 주석 |

두 번째 기록 뒤에 dolomood 세션은 바닥값 `RCM_MIN_VERSION` 을 손으로 0.5.0 으로 올렸다. 서버가 오를 때마다
누군가 이 줄을 따라 고쳐야 한다는 것이 오너가 말한 불편이다.

### 1.3 지금 클라이언트를 올리는 길

- `examples/session/update-client.sh`: `/api/health` 로 `client_wheel.path` 와 `sha256` 을 읽고 받은 파일의
  해시를 확인한 뒤 `pip install --upgrade` 한다. 사람이 부를 때만 돈다.
- dolomood `scripts/remote_ci.sh`: rcm 이 없으면 `~/.local/share/rcm-client-venv` 에 깔고 설치본이 바닥값
  `RCM_MIN_VERSION`(0.5.0)보다 낮으면 다시 깐다. 깔 때는 서버 wheel 이 sha256 을 통과하면 그것을 쓰고 아니면
  GitHub 릴리스 주소 `RCM_WHEEL`(v0.5.0 고정)로 간다. 바닥 위에서 서버보다 낮으면 `client_drift_hint` 가
  한 줄로 알릴 뿐 올리지 않는다. 올리는 일은 `--update-client` 를 사람이 부를 때만 일어난다.
- `rcm check` 의 `client` 행은 `min_client_version`(운영 0.2.0) 아래일 때만 FAIL 이고 그 위의 차이는 warn 이다.

### 1.4 서버 wheel 실물과 비용 (운영 8787 의 `/api/health` 와 `/client/…whl` 을 읽기만 했다)

| 항목 | 값 |
|---|---|
| 서버 버전 · `min_client_version` | 0.6.1 · 0.2.0 |
| wheel 크기 · sha256 앞 16자 | 707,673 바이트 · `a82e75a4b20b881f` |
| wheel 안 항목 수 | 66 |
| `skills/` 아래 실린 파일 | `.py` 4개뿐. `SKILL.md` 5개, `.sh` 8개, `.toml` 2개(15개)가 빠졌다 |
| `/api/health` 응답 시간(루프백) | 1.9 ms |
| `import remote_ci_monitor.cli`(Python 3.11, 이 Mac mini, 데운 뒤) | 약 0.10 초 |
| 빈 인터프리터 기동 | 약 0.02 초 |

skills 파일이 빠지는 것은 지금도 있는 결함이다. `clientwheel.ASSET_DIRS` 가 `web` 과 `templates` 만 싣기 때문이다.
서버 wheel 로 깐 클라이언트(dolomood 의 클라이언트 venv 가 그렇다)에서는 `rcm skills list` 가 다섯 스킬 중
`.py` 템플릿이 있는 셋만 설명 없이 보이고, `rcm skills install` 은 그 `.py` 몇 개만 복사한다. 이 마일스톤은 그 wheel 을
실제로 실행할 코드로 쓰므로 PR A 에서 함께 고친다(결정 165).

따라가기가 더하는 비용은 재실행 한 번의 import 다. 명령마다 0.1 초쯤 늘고 서버 버전 확인은 10분에 한 번이다.

### 1.5 코드에서 확인한 제약

리드가 준 설계 방향을 코드와 대조하다가 다음 여섯 가지가 걸렸다. 각각 §2 이하에서 방향을 고쳤다.

| 제약 | 자리 | 영향 |
|---|---|---|
| `client.toml` 은 평면 키다(`server` `token` `token_env` `label`). 모르는 키가 있으면 `ConfigError` 다 | `config.py` `_CLIENT_KEYS` · `load_client_config` | `[client]` 표는 쓸 수 없다. 그리고 새 키를 넣은 파일을 옛 버전이 읽으면 실패한다. 0.6.1 로 고정하거나 내려가면 그 0.6.1 이 같은 파일을 읽다 죽는다(결정 153) |
| 서버의 기본 `data_dir` 이 `~/.local/share/rcm` 이다 | `config.py` `DEFAULT_DATA_DIR` | 리드 안의 `~/.local/share/rcm/clients/` 는 빌드 머신에서 운영 데이터 폴더 안이다. 운영 보호 훅도 그 아래를 지킨다(결정 150) |
| 서버는 자기 버전의 wheel 만 준다. 다른 이름은 404 다 | `server.py` `_client_wheel` | 고정 버전은 서버가 마침 그 버전일 때만 받을 수 있다. 리드 안과 같다 |
| 워커 등록은 버전이 정확히 같아야 받는다(409 `install the same release`). 등록은 DB 에 남아 서버 재시작을 넘긴다 | `remote_workers.py` `worker_register` | 서버를 올린 뒤에도 이미 등록된 워커는 옛 버전으로 계속 돈다. 그 워커가 재시작하면 등록이 409 로 막혀 종료 2 로 끝난다. PR B 의 첫 번째 계기는 이 409 다(결정 169) |
| `rcm version` 은 한 줄이라고 시험이 잠근다 | `tests/test_cli_m4.py` | 따라가기 정보를 늘 붙이면 시험을 약하게 만들어야 한다. 정보가 있을 때만 둘째 줄을 쓴다(결정 164) |
| 클라이언트는 이미 `fcntl` 을 쓴다 | `submissions.py` | Windows 에서는 지금도 클라이언트가 돌지 않는다. pyproject classifiers 에도 Windows 가 없다(결정 170) |

## 2. 설계: 따라가기 (PR A)

### 2.1 흐름

`rcm` 콘솔 스크립트가 `cli.main()` 을 부르면, 인자를 해석한 바로 뒤이자 명령이 무엇이든 출력하기 전에 다음을 한다.

1. 이 명령이 따라가는 명령인지 본다(§2.3). 아니면 아무것도 안 한다.
2. 설정을 정한다(§2.2). `installed` 면 끝이다.
3. 서버 주소를 정한다. 플래그, 환경 변수, `client.toml`, mDNS 발견 순서이고 명령이 쓰는 규칙과 같다.
4. 서버 버전을 안다. 10분 안의 기록이 있으면 그것을 쓰고 없으면 `GET /api/health` 를 3초 제한으로 한 번 부른다(§2.4).
5. 목표 버전을 정한다(§2.7). 목표가 설치본과 같으면 끝이다.
6. 캐시에 목표가 있으면 그 폴더로, 없으면 받아서 풀고 그 폴더로 다시 실행한다(§2.5).
7. 3~6 의 어디서든 실패하면 설치본으로 계속하고 stderr 에 한 줄을 쓴다(§2.6).

`main(argv)` 에 인자 목록을 직접 넘겨 부르는 경우(시험, 다른 파이썬 프로그램 안)는 아무것도 하지 않는다(결정 160).
남의 프로세스를 `exec` 로 갈아치우면 안 되기 때문이다.

### 2.2 설정과 우선순위 (결정 152·153·154)

값은 셋 중 하나다.

| 값 | 뜻 |
|---|---|
| `follow` | 서버 버전으로 돈다. 위로든 아래로든 간다 |
| `installed` | 설치된 코드로 돈다. 오늘과 같다 |
| `X.Y.Z` | 그 버전으로 돈다. 해석 순서는 §2.7 |

정하는 곳은 넷이고 위가 이긴다.

| 순위 | 자리 | 예 |
|---|---|---|
| 1 | 플래그 `--no-follow` | `rcm --no-follow jobs` · `rcm jobs --no-follow` |
| 2 | 환경 변수 `RCM_CLIENT_VERSION` | `RCM_CLIENT_VERSION=0.6.1 rcm run gate` |
| 3 | `client.toml` 최상위 키 `client_version` | `client_version = "installed"` |
| 4 | 기본값 | 설치 종류가 `wheel` 이면 `follow`, `editable` 이나 `source` 면 `installed` |

- 따라간 자식 프로세스(`RCM_FOLLOWED` 가 있다)는 이 표를 보지 않고 다시 따라가지 않는다(결정 159).
- 값이 셋 중 어느 것도 아니면(`RCM_CLIENT_VERSION=lates`) 종료 2 와 `ConfigError` 문장으로 끝난다. 오타 때문에
  조용히 따라가거나 조용히 멈추지 않는다.
- **설치 종류**는 `importlib.metadata.distribution("remote-ci-monitor")` 로 정한다. `direct_url.json` 의
  `dir_info.editable` 이 참이면 `editable` 이다. 메타데이터가 없거나 메타데이터의 버전이 `__version__` 과 다르면
  `source` 다. 체크아웃을 `git pull` 한 editable 설치가 뒤쪽에 걸린다. 나머지는 `wheel` 이다. 개발 워크트리의
  `.venv` 와 빌드 머신의 서비스 venv 는 editable 이라 기본으로 따라가지 않는다. 개발 중인 코드를 운영 버전으로
  바꿔치면 안 되기 때문이다. 명시적으로 `follow` 를 주면 editable 도 따라간다.
- **`client.toml` 키 이름이 `client_version` 인 이유**: `client.toml` 은 평면이라 `[client]` 표를 둘 수 없다.
  `version` 이라고만 쓰면 서버 버전인지 헷갈린다.
- **`client.toml` 키의 제약**(결정 153): 키가 있는 파일은 그 키를 모르는 옛 버전이 읽지 못한다. 그래서 목표 버전이
  `FOLLOW_CONFIG_SINCE`(PR A 가 들어가는 릴리스, §9) 보다 낮고 자식이 읽을 `client.toml` 에 `client_version` 이
  있으면 따라가지 않는다. 경고는 `RCM_CLIENT_VERSION` 을 쓰라고 말한다. 고정의 주된 길은 그래서 환경 변수다.
- `--no-follow` 는 최상위 파서와 `client_opts` 둘 다에 둔다. 하위 파서 쪽은 `default=argparse.SUPPRESS` 로 둬야
  최상위 값을 덮지 않는다. 시험이 두 자리를 다 확인한다(F7).

### 2.3 어떤 명령이 따라가나 (결정 158)

기준은 하나다. 그 명령이 `client.toml` 의 서버와 말하면 따라간다.

| 명령 | 따라가나 | 이유 |
|---|---|---|
| `run` `wait` `cancel` `bump` `jobs` `logs` `eta` `top` `presets` `artifacts` `machines` `recheck` `pause` `resume` `reload` `release …` | 따라간다 | 서버와 말한다 |
| `check` | 따라간다 | 진단도 실제로 돌 코드로 해야 맞다. 대신 10분 기록을 쓰지 않고 health 를 매번 새로 읽는다. `client` 행이 따라가기 상태를 말한다(§2.9) |
| `gc` | 서버로 보낼 때만 따라간다 | `gc --config … --dry-run` 은 서버 없이 로컬 DB 사본에서 돈다 |
| `serve` | 절대 안 따라간다 | 서버가 정본이다. `RCM_CLIENT_VERSION` 도 보지 않는다 |
| `worker` | PR A 에서는 안 따라간다 | 원격 워커는 따로 다룬다(§3, PR B) |
| `token` `init` `discover` `skills` `version` | 안 따라간다 | 서버 없이 돈다. `token` 은 서버 머신의 DB 를 연다 |
| `--help` · `--version` | 안 따라간다 | 인자 해석 단계에서 끝난다 |

### 2.4 서버 버전 확인과 기록 (결정 151)

기록 파일은 `$XDG_CACHE_HOME/rcm/follow.json`(없으면 `~/.cache/rcm/follow.json`) 하나다. 키는 정규화한 서버
주소(스킴, 소문자 호스트, 포트, 끝 `/` 없음)이고 값은 다음과 같다.

```json
{"http://macmini.local:8787": {
   "version": "0.7.1", "checked_at": 1763532000.0,
   "wheel": {"path": "/client/remote_ci_monitor-0.7.1-py3-none-any.whl",
             "sha256": "…", "bytes": 707673, "requires_python": ">=3.11"},
   "wheel_error": null}}
```

- 기록이 10분(`CHECK_TTL_SECONDS = 600`) 안이면 health 를 부르지 않는다. 같은 서버를 여러 주소(LAN IP, Tailscale,
  `.local`)로 부르면 기록도 따로다.
- health 는 3초(`HEALTH_TIMEOUT_SECONDS`) 안에 답해야 한다. 503 이어도 본문에 `version` 이 있으면 쓴다.
  `Client.health()` 가 이미 그렇게 하고 `update-client.sh` 도 같은 판단을 한다.
- health 를 못 읽었는데 그 서버의 기록이 있고 기록의 버전이 캐시에 있으면, 마지막으로 본 버전으로 돈다. 경고는
  쓰지 않는다. 서버가 재시작하는 동안의 `rcm wait` 가 그렇다. 명령 자신이 연결 실패를 말할 것이다.
- 기록 파일은 임시 파일에 쓰고 `os.replace` 로 바꾼다. 동시에 쓰면 나중 것이 이긴다. 기록은 다시 만들 수 있는
  캐시라 그것으로 충분하다.

### 2.5 받기 · 검증 · 풀기 · 재실행 (결정 149·150·159·162·163)

**캐시 자리.** `$XDG_CACHE_HOME/rcm/clients/`, 없으면 `~/.cache/rcm/clients/` 다. 리드 안의
`~/.local/share/rcm/clients/` 를 쓰지 않는 이유는 그 폴더가 빌드 머신에서 서버의 기본 `data_dir` 이기
때문이다(§1.5). 다시 받을 수 있는 파일이라 XDG 의 cache 자리가 맞다.

```
~/.cache/rcm/
  follow.json
  clients/                         # 0700, 이 사용자 소유
    0.7.1-a82e75a4/                # <버전>-<sha256 앞 8자>
      remote_ci_monitor/…
      remote_ci_monitor-0.7.1.dist-info/…
      .rcm-client.json             # 완성 표지: {version, sha256, server, fetched_at, requires_python}
      .lock                        # 쓰는 프로세스가 공유 잠금을 잡는다
    .tmp-<무작위>/                 # 받는 중. 실패하면 지운다
```

**폴더 권한**(결정 162). `clients/` 와 그 위 `rcm/` 이 이 사용자 소유가 아니거나 그룹이나 남이 쓸 수 있으면
따라가지 않는다. 남이 쓸 수 있는 폴더의 코드는 그 사람의 코드다. M5o 결정 122 가 들인 프리셋 파일에 건 규칙과 같다.
새로 만들 때는 0700 이다.

**받기.** `GET <server><client_wheel.path>` 를 받는다. `read_auth = "basic"` 이면 토큰을 Bearer 로 싣는다.
`.tmp-<무작위>/` 안의 파일로 흘려 받으면서 sha256 을 계산하고 `client_wheel.bytes` 나 상한 `MAX_WHEEL_BYTES`(64 MB)를
넘으면 끊는다. 해시가 health 의 `client_wheel.sha256` 과 다르면 임시 폴더를 통째로 지운다(결정 155). 다른
실행이 그 바이트를 쓰지 않도록 캐시에 아무것도 남기지 않는다.

**풀기**(결정 163). pip 를 쓰지 않는다. 런타임 의존성이 0 이라 순수 파이썬 wheel 은 풀기만 하면 import 할 수 있다.
`zipfile` 로 풀되 다음을 지킨다.

- 이름은 `remote_ci_monitor/` 와 `remote_ci_monitor-<버전>.dist-info/` 아래만 받는다. 절대 경로, `..` 조각,
  역슬래시가 있으면 거부한다.
- 심링크 항목(`external_attr` 의 상위 비트가 링크)은 거부한다.
- 푼 크기 합이 `MAX_UNPACKED_BYTES`(256 MB)를 넘으면 거부한다.
- 푼 뒤 `remote_ci_monitor/__init__.py` 의 `__version__` 이 목표 버전과 같아야 한다. 정규식 하나로 읽는다.
  다르면 거부한다. 다시 실행한 자식이 「아직 버전이 다르다」고 보고 또 따라가는 고리를 여기서 끊는다(결정 159).
- `dist-info/METADATA` 의 `Requires-Python` 을 지금 인터프리터와 대조한다(결정 157).
- 표지 `.rcm-client.json` 을 마지막에 쓴다.

**완성은 rename 하나로**. `.tmp-<무작위>/tree` 를 `clients/<버전>-<sha8>` 로 `os.rename` 한다. 같은 버전을 두
프로세스가 동시에 받으면 둘 다 자기 임시 폴더에 풀고 늦은 쪽의 rename 은 대상이 있어 실패한다. 늦은 쪽은 자기
임시 폴더를 지우고 이미 있는 폴더의 표지를 확인한 뒤 그것을 쓴다. 표지가 없는 폴더는 완성본이 아니다. 디스크가
가득 차 풀다가 실패하면 임시 폴더만 지워지고 대상 폴더는 생기지 않는다.

**재실행**(결정 149). `PYTHONPATH` 를 쓰지 않는다. 그 변수는 rcm 이 띄우는 모든 자식 프로세스(git 등)로 새고,
`python -m` 은 현재 폴더를 `sys.path` 맨 앞에 둔다. 대신 같은 인터프리터를 `-P -c` 로 다시 실행한다. `-P` 는
Python 3.11 부터 있고 현재 폴더를 경로에 넣지 않는다.

```python
os.execve(sys.executable,
          [sys.executable, "-P", "-c", BOOT, *argv],   # argv = 사용자가 친 인자 그대로
          {**os.environ,
           "RCM_FOLLOWED": "0.7.1",
           "RCM_FOLLOWED_PATH": "/Users/me/.cache/rcm/clients/0.7.1-a82e75a4",
           "RCM_FOLLOWED_FROM": "0.7.0"})
```

`BOOT` 는 설치본이 쓰는 짧은 코드다. 목표 버전의 코드가 아니라서 M5q 이전 버전으로 내려갈 때도 똑같이 돈다.

```python
import fcntl, os, sys
p = os.environ.get("RCM_FOLLOWED_PATH", "")
try:
    _lock = open(os.path.join(p, ".lock"), "a")      # 프로세스가 끝날 때까지 쥔다
    fcntl.flock(_lock, fcntl.LOCK_SH)
    sys.path.insert(0, p)
except OSError:                                      # 폴더가 사라졌다 — 아래 위치 확인이 말한다
    pass
import remote_ci_monitor
if not os.path.realpath(remote_ci_monitor.__file__).startswith(os.path.realpath(p) + os.sep):
    sys.stderr.write("rcm: follow: the cached client did not load — running installed\n")
from remote_ci_monitor.cli import main
sys.exit(main())
```

- `exec` 는 같은 PID 를 쓴다. 종료 코드, 신호, 열린 stdin·stdout·stderr 가 그대로 이어진다. `rcm wait` 의 0/1/2/3 도
  그대로다. 부모는 `exec` 전에 stdout 과 stderr 를 비운다. 부모는 stdin 을 읽지 않는다.
- `sys.argv[0]` 은 자식에서 `-c` 가 된다. 파서는 `prog="rcm"` 이라 도움말이 바뀌지 않는다. `_this_rcm()` 은 `argv[0]`
  이 `rcm` 일 때만 그것을 쓰므로 다음 후보(`sys.executable` 옆의 `rcm`)로 넘어간다.
- 서버를 mDNS 로 찾았으면 찾은 주소를 자식에게 `RCM_SERVER` 로 넘긴다. 자식이 1.5초 발견을 다시 하지 않는다.
- editable 설치가 메타 경로 파인더(setuptools strict 모드)를 쓰면 `sys.path` 를 앞에 넣어도 설치본이 import 될 수
  있다. `BOOT` 의 위치 확인이 그것을 잡아 한 줄을 쓴다. editable 은 기본으로 따라가지 않으니 드물다.
- 프리셋 잡의 환경은 `runner.build_env` 가 `env_passthrough` 에 적힌 이름만 넘긴다. `RCM_FOLLOWED*` 는 잡으로 새지 않는다.

**처음 받을 때만** stderr 에 한 줄을 쓴다. 캐시에서 바로 실행할 때는 아무것도 쓰지 않는다.

```
rcm: following server v0.7.1 (installed v0.7.0) — fetched 707 KB
```

### 2.6 실패는 설치본으로 계속 + 경고 한 줄 (결정 155)

fail-open 금지 규칙과 부딪치지 않는다. 설치본으로 도는 것은 오늘의 동작이고 틀린 값을 자신 있게 보여 주는 것이
아니다. 그리고 실패를 숨기지 않고 한 줄로 말한다. JSON 을 내는 명령(`run` `wait`)의 stdout 은 그대로 깨끗하다.

```
rcm: not following server v0.7.1: <이유> — running installed v0.7.0
```

| 코드 | `<이유>` | 언제 |
|---|---|---|
| `unreachable` | `cannot reach the server` | health 실패, 기록도 캐시도 없음 |
| `no_wheel` | `the server has no client wheel (<client_wheel_error>)` | `client_wheel: null` |
| `old_server` | `the server is too old to hand out its client` | health 에 `client_wheel` 키 자체가 없다(0.2.6 이전) |
| `auth` | `the server needs a token to hand out its client` | `/client/…` 가 401 이다(`read_auth = "basic"`, 토큰 없음) |
| `sha_mismatch` | `sha256 mismatch` | 받은 바이트의 해시가 health 와 다르다 |
| `unpack` | `cannot unpack: <strerror>` | 디스크 가득, 권한, 깨진 zip, 거부한 항목 |
| `version_mismatch` | `the wheel says v<X>` | 푼 `__init__.py` 의 버전이 목표와 다르다 |
| `python` | `needs Python <spec>, this is <py>` | `Requires-Python` 미달, 또는 해석 못 하는 꼴 |
| `cache_unsafe` | `<path> is writable by others` | §2.5 폴더 권한 |
| `pinned_unavailable` | `v<X> is not installed or cached and the server runs v<Y>` | §2.7 |
| `config_key` | `<path> sets client_version, which v<X> cannot read — use RCM_CLIENT_VERSION` | §2.2 |
| `platform` | `not supported on this platform` | `os.name != "posix"` 이거나 `sys.executable` 이 비었다 |

경고는 한 명령에 한 번만 쓴다. 같은 이유가 10분 기록 안에서 되풀이돼도 명령마다 한 번이다.

### 2.7 목표 버전 정하기 (결정 148·156)

`follow` 의 목표는 서버 버전이다. 버전은 크기를 비교하지 않고 문자열이 같은지만 본다. 서버가 0.6.1 로 내려가면 클라이언트도
0.6.1 로 내려간다(결정 148). 서버가 정본이고 `min_client_version` 같은 와이어 계약은 바뀌지 않는다. 서버의 sha256 이 캐시와
다르면(같은 버전 번호로 다시 빌드한 개발 서버) 새로 받는다. 서버가 버전은 말하지만 wheel 이 없으면(`client_wheel: null`) 캐시에
같은 버전이 있을 때 그것을 쓴다. 없으면 `no_wheel` 이다.

`X.Y.Z` 로 고정했으면 다음 순서로 찾는다.

| 순서 | 조건 | 결과 |
|---|---|---|
| 1 | 설치본이 X 다 | 설치본으로 돈다. 서버에 묻지 않는다 |
| 2 | 캐시에 `X-*` 폴더가 있다 | 가장 최근에 쓴 것으로 돈다. 서버에 묻지 않는다 |
| 3 | 서버가 지금 X 다 | 받아 두고 그것으로 돈다 |
| 4 | 그 밖 | 설치본으로 돌고 `pinned_unavailable` 경고 |

서버는 자기 버전의 wheel 만 주므로(§1.5) 3 이 유일하게 받는 길이다. 한 번 받아 두면 서버가 다른 버전이 돼도 2 로 계속 쓴다.

내려가면 그 버전의 CLI 가 돈다. 사용자가 더 새 버전에만 있는 플래그를 쳤다면(서버 0.5.0 에서 `rcm run --no-supersede`)
그 버전의 파서가 사용법 오류(종료 2)로 끝낸다. 서버도 그 기능을 모르니 맞는 결과다. 문서에 한 문단으로 적는다.

### 2.8 캐시 청소 (결정 161)

새 버전을 받아 rename 에 성공한 직후에만 청소한다. 명령마다 하지 않는다.

- 표지의 마지막 사용 시각(`.rcm-client.json` 의 mtime, 실행할 때마다 `os.utime`) 순으로 최근 3개(`KEEP_VERSIONS`)를 남긴다.
- 지금 목표 폴더는 남긴다.
- `.lock` 에 `LOCK_EX | LOCK_NB` 를 걸어 보고 실패하면 누가 쓰는 중이라 남긴다. 오래 도는 `rcm wait` · `rcm top --watch` 가
  그렇다.
- 지울 폴더는 먼저 `.trash-<무작위>` 로 rename 하고 그다음 지운다. 반쯤 지운 폴더가 완성본처럼 보이지 않게 한다.
- 한 시간보다 오래된 `.tmp-*` · `.trash-*` 도 지운다(죽은 프로세스의 찌꺼기).

### 2.9 보이는 것 (결정 164)

**`rcm version`** 은 서버에 묻지 않는다는 규칙을 지킨다. 첫 줄은 오늘과 똑같다. 따라가기 정보가 있을 때만(설정이 기본이
아니거나 기록 파일에 기본 서버의 항목이 있을 때) 둘째 줄을 쓴다. `rcm --version` 은 언제나 한 줄이다. 셸 스크립트가
`rcm --version | awk '{print $2}'` 로 읽는다(dolomood).

```
$ rcm version
rcm 0.7.0 (Python 3.12.4, darwin arm64)
follow: on (default) · server http://macmini.local:8787 v0.7.1 checked 3m ago · cached 0.7.1, 0.6.1

follow: off (editable install)
follow: pinned 0.6.1 (RCM_CLIENT_VERSION) · cached 0.6.1
```

`rcm version --json` 은 기존 키를 그대로 두고 `follow` 객체를 더한다. 서버에 묻지 않고 기록 파일만 읽는다.

```json
"follow": {"mode": "follow", "pin": null, "source": "default", "install": "wheel",
           "server": "http://macmini.local:8787", "server_version": "0.7.1",
           "checked_at": "2026-11-19T06:20:00Z", "cached": ["0.7.1", "0.6.1"]}
```

**`rcm check` 의 `client` 행**은 지금 판정(FAIL 은 `min_client_version` 아래일 때뿐)을 그대로 두고 꼬리에 따라가기 상태를 붙인다.

```
ok    client        v0.7.1 · same as server · following (installed v0.7.0)
warn  client        v0.7.0 · server v0.7.1 · older · not following: sha256 mismatch — pip install http://…/client/…whl
warn  client        v0.6.1 · server v0.7.1 · older · pinned by RCM_CLIENT_VERSION
ok    client        v0.7.1 · same as server · not following (editable install)
```

따라간 자식이 M5q 이전 버전이면(내려갔을 때) 그 버전의 옛 `client` 행이 그대로 나온다. 고칠 수 없고 고칠 필요도 없다.

### 2.10 서버 쪽 작은 변경 (결정 165·166)

- **wheel 에 skills 를 싣는다.** `clientwheel.ASSET_DIRS = ("web", "templates", "skills")`. 시험은 wheel 의 파일 목록이 패키지
  폴더의 파일 목록(`__pycache__` · `.pyc` · 점 파일 제외)과 같은지 본다. 앞으로 새 자산 폴더가 생겨도 이 시험이 빨개진다.
- **health 에 `client_wheel.requires_python`** 을 더한다. 설치된 METADATA 의 `Requires-Python` 값이고 없으면 `null` 이다.
  클라이언트는 받기 전에 이것으로 거른다. 옛 서버는 키가 없으니 풀고 나서 METADATA 로 거른다.
- **클라이언트 버전을 서버 로그에 한 줄** 남긴다. 클라이언트 토큰으로 온 요청의 `User-Agent: rcm/<X>` 를 보고,
  `(토큰 이름, X)` 쌍을 프로세스가 처음 볼 때 `client macbook runs v0.7.1` 을 쓴다. DB 는 바꾸지 않는다.
  §1.1 처럼 「기록 없음」으로 끝나지 않고 실기에서 따라가기가 실제로 퍼졌는지 로그로 볼 수 있다.

## 3. 원격 워커 (PR B, 결정 169)

### 3.1 지금 무슨 일이 일어나나

워커 등록은 버전이 정확히 같아야 한다. 그런데 등록은 DB 에 남으므로 서버를 올리고 재시작해도 이미 등록된 워커는 옛 버전으로
heartbeat 와 claim 을 계속한다. 문제는 그 워커가 재시작할 때다. 등록이 409 `worker version 0.6.1, server 0.7.0 — install the
same release` 로 막히고 `rcm worker` 는 종료 2 로 끝난다. launchd 의 `KeepAlive` 나 systemd 의 `Restart=` 가 다시 띄우면 같은
409 를 되풀이한다. 지금 운영의 워커(`mac2-worker`)는 서버와 같은 머신의 같은 editable 설치라 이 일을 겪지 않았다.

### 3.2 무엇을 바꾸나

계기는 셋이다. 셋 다 같은 설정(§2.2)을 따른다. 워커는 `worker.toml` 키를 두지 않고 `RCM_CLIENT_VERSION` 과
`rcm worker --no-follow` 만 본다. launchd plist 와 systemd unit 은 이미 환경 변수를 싣는 자리가 있고 `worker.toml` 에 새 키를
넣으면 §2.2 의 「옛 버전이 못 읽는 파일」 문제가 워커에도 생긴다.

| 계기 | 그때 도는 잡 | 하는 일 |
|---|---|---|
| ① 기동할 때 | 없다 | 등록 전에 health 를 보고 CLI 와 같은 흐름(§2.1 의 4~6)으로 따라간다 |
| ② 등록이 409 이고 본문의 `server_version` 이 자기와 다르다 | 없다 | 그 버전으로 따라간다. 실패하면 오늘처럼 종료 2 이고 문장에 이유가 붙는다 |
| ③ heartbeat 응답의 `server_version` 이 자기와 다르다 | 있을 수 있다 | 새 claim 을 멈춘다. 도는 잡이 끝나 보고까지 마치면 레인을 닫고 설치본 부트로더로 자기를 다시 실행한다. 그 실행이 ① 을 다시 한다 |

- 서버는 heartbeat 응답에 `server_version` 을 더하고 버전 불일치 409 본문에도 `server_version` 을 싣는다. 키 추가뿐이라 옛 워커는
  무시한다.
- ③ 에서 설치본으로 돌아가 다시 시작하는 이유: 지금 프로세스가 내려간 옛 버전(M5q 이전)일 수도 있다. 다음 판정은 언제나 설치본
  부트로더가 한다. 설치본으로 돌아가는 `exec` 는 `RCM_FOLLOWED*` 를 지운 환경으로 `-P -c "from remote_ci_monitor.cli import main…"`
  을 실행한다. 경로를 앞에 넣지 않으니 site-packages 의 설치본이 import 된다.
- 내려간 워커가 M5q 이전 버전이면 ③ 을 모른다. 그 워커는 다음 재시작까지 옛 버전으로 돈다. 등록이 살아 있는 동안은 오늘과 같다.
- **고리 막기**: ③ 의 재실행은 `RCM_WORKER_RESTARTED_AT`(epoch 초)을 남긴다. 300초 안에 다시 ③ 이 오면 실행하지 않고 로그를 한 번 쓴다.
- **고정한 워커**: `RCM_CLIENT_VERSION=0.6.1` 인 워커는 ③ 을 무시한다. ② 에서는 따라가지 않고 오늘처럼 종료 2 이며 문장이
  「pinned」라고 말한다.
- **editable 워커**는 기본 `installed` 다. ③ 에서 로그 한 줄(`server v0.7.1 — not following (editable install)`)만 프로세스당 한 번 쓴다.

### 3.3 launchd · systemd 아래에서 안전한가

- `exec` 는 PID 를 바꾸지 않는다. 감독자는 같은 프로세스가 계속 도는 것으로 본다. 재시작 횟수 제한에 걸리지 않는다.
- stdout · stderr 는 감독자가 연 로그 파일 fd 1·2 이고 상속된다. 로그가 같은 파일로 이어진다.
- 신호 처리기는 `exec` 로 초기화되고 새 코드가 `cmd_worker` 에서 다시 단다.
- `exec` 직전의 상태: 레인 스레드는 끝났고(join), heartbeat 와 샘플러는 `stopping` 으로 멈췄고 도는 잡은 없다. 파이썬이 연 소켓은
  상속되지 않는다(PEP 446).
- `exec` 자체가 `OSError` 로 실패하면 로그를 쓰고 종료 1 로 끝난다. 감독자가 다시 띄우면 설치본이 ① 부터 다시 한다.
- 드레인 중에 SIGTERM 이 오면 오늘의 정지 경로를 그대로 탄다. 따라가기는 취소된다.
- 드레인 중 long-poll claim 이 잡을 하나 돌려줄 수 있다. 그 잡은 끝까지 돌리고 보고한 뒤에 재실행한다.

## 4. 보안 (결정 167·168)

따라가기는 서버가 준 코드를 세션 머신에서 실행한다. 이것은 새로 생기는 신뢰다. 오늘 서버가 세션 머신에 하는 일은 결과와
산출물을 돌려주는 것까지다.

- LAN 의 평문 HTTP 에서는 중간자가 wheel 을 바꿔칠 수 있다. sha256 도 같은 통로(`/api/health`)로 오므로 이것을 막지 못한다. sha256
  은 전송 중 깨짐을 잡는 장치이지 위조를 막는 장치가 아니다.
- 신뢰 경계는 크게 보면 이미 같다. 클라이언트는 그 서버에 토큰을 평문으로 보내고 작업 트리를 올리며 `rcm artifacts --fetch` 는
  서버가 준 파일을 작업 트리에 쓴다. dolomood 의 `remote_ci.sh` 는 지금도 서버 wheel 을 해시만 보고 `pip install` 한다. 그래도
  「다음 `rcm` 호출 때 그 코드가 바로 돈다」는 점은 다르다. 문서는 이 차이를 숨기지 않고 한 문단으로 적는다.
- mDNS 로 찾은 서버도 따라간다(결정 168). 같은 망의 누군가가 가짜 `_rcm._tcp` 를 광고할 수 있지만 발견은 이미 그 서버에 토큰을
  건넨다. 둘 이상이 보이면 지금처럼 거절한다.
- Tailscale 주소로 붙으면 WireGuard 가 통로를 암호화하므로 중간자 위험이 작다. 문서는 신뢰할 수 없는 Wi-Fi 에서는 Tailscale 주소를
  쓰거나 따라가기를 끄라고 권한다.
- **끄는 법**: `RCM_CLIENT_VERSION=installed`(셸 프로필), `client.toml` 의 `client_version = "installed"`, 한 번만이면 `--no-follow`.
- 대안 평가는 §8 에 있다.

## 5. 인터페이스 계약

구현 에이전트가 둘 이상 나란히 짤 수 있게 이름과 모양을 여기서 고정한다. 바꿔야 하면 §12 에 남긴다.

### 5.1 새 모듈 `src/remote_ci_monitor/follow.py` (표준 라이브러리만)

`cli.py` 와 `server.py` 를 import 하지 않는다. 콘솔 스크립트의 앞단에서 가볍게 돌아야 한다. 가져오는 것은
`remote_ci_monitor.__version__` 과 `config.ConfigError` 뿐이다. 서버 주소 해석(mDNS 발견 포함)은 `cli.py` 가 이미 갖고
있으므로 `cli.py` 가 해석한 값을 넘긴다.

```python
ENV_MODE = "RCM_CLIENT_VERSION"
ENV_FOLLOWED = "RCM_FOLLOWED"            # 따라간 자식에만. 값은 목표 버전
ENV_FOLLOWED_PATH = "RCM_FOLLOWED_PATH"  # 풀린 폴더
ENV_FOLLOWED_FROM = "RCM_FOLLOWED_FROM"  # 설치본 버전(표시용)
ENV_WORKER_RESTARTED_AT = "RCM_WORKER_RESTARTED_AT"   # PR B
MODE_FOLLOW, MODE_INSTALLED, MODE_PIN = "follow", "installed", "pin"
CHECK_TTL_SECONDS = 600
HEALTH_TIMEOUT_SECONDS = 3.0
KEEP_VERSIONS = 3
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
FOLLOW_CONFIG_SINCE = "0.7.0"            # PR A 가 들어가는 릴리스. 릴리스 PR 이 맞춘다
MARKER, LOCK = ".rcm-client.json", ".lock"

class FollowError(Exception):            # .code 는 §2.6 표의 코드
    def __init__(self, code: str, detail: str = ""): ...

@dataclass(frozen=True)
class Setting:
    mode: str                            # follow | installed | pin
    pin: str | None
    source: str                          # flag | env | client.toml | default

@dataclass(frozen=True)
class ServerInfo:
    server: str                          # 정규화한 주소
    version: str
    wheel_path: str | None
    sha256: str | None
    bytes: int | None
    requires_python: str | None
    wheel_error: str | None              # health 의 client_wheel_error, 키가 없으면 "old_server"
    checked_at: float

@dataclass(frozen=True)
class CacheEntry:
    version: str
    sha256: str
    path: Path
    last_used: float

@dataclass(frozen=True)
class Plan:
    action: str                          # stay | exec_cached | fetch
    target: str | None
    entry: CacheEntry | None
    warn: str | None                     # §2.6 코드

@dataclass
class FollowState:                       # check · version 이 읽는다
    setting: Setting
    install: str                         # wheel | editable | source
    installed: str                       # 설치본 __version__ (자식이면 RCM_FOLLOWED_FROM)
    running: str                         # 지금 도는 __version__
    followed: bool                       # 지금 프로세스가 캐시 코드인가
    server: str | None
    server_version: str | None
    warn: str | None
    warn_text: str | None

def install_kind() -> str
def resolve_setting(*, no_follow: bool, environ: Mapping[str, str],
                    file_value: str | None, kind: str) -> Setting          # 순수. 잘못된 값은 ConfigError
def follows(args: argparse.Namespace) -> bool                               # §2.3 표
def plan(setting: Setting, *, installed: str, info: ServerInfo | None,
         cached: Sequence[CacheEntry], python: tuple[int, int, int], os_name: str,
         file_has_key: bool) -> Plan                                         # 순수. 시계·파일·네트워크를 안 본다
def python_satisfies(spec: str, python: tuple[int, int, int]) -> bool | None  # >= < != 와 쉼표만. 모르면 None
def cache_root(environ: Mapping[str, str] | None = None) -> Path
def scan_cache(root: Path) -> list[CacheEntry]
def load_record(root: Path, server: str) -> ServerInfo | None
def save_record(root: Path, info: ServerInfo) -> None
def server_info(server: str, token: str | None, *, root: Path, now: float, ttl: float,
                opener=urllib.request.urlopen) -> ServerInfo | None
def fetch(server: str, token: str | None, info: ServerInfo, *, root: Path,
          opener=urllib.request.urlopen) -> CacheEntry                       # 실패는 FollowError
def prune(root: Path, *, keep: int, protect: Path) -> list[Path]
def exec_into(entry: CacheEntry, argv: Sequence[str], *, environ: Mapping[str, str],
              installed: str, execve=os.execve) -> NoReturn
def restart_via_installed(argv: Sequence[str], *, environ: Mapping[str, str],
                          execve=os.execve) -> NoReturn                     # PR B
def maybe_follow(argv: Sequence[str], *, no_follow: bool, server: str, token: str | None,
                 file_value: str | None, file_path: Path | None, discovered: bool,
                 fresh: bool,                                               # check 는 True: 10분 기록을 안 쓴다
                 environ=os.environ, execve=os.execve, now=time.time,
                 opener=urllib.request.urlopen, err=sys.stderr) -> FollowState  # exec 하면 안 돌아온다
def current_state() -> FollowState | None                                   # 이 프로세스의 결과. 따라가기를 안 했으면 None
```

### 5.2 `cli.py`

| 바꾸는 것 | 모양 |
|---|---|
| 진입 | `main(argv=None)` 은 `_main(argv, follow_enabled=argv is None, execve=os.execve)` 를 부른다. 시험은 `_main` 에 가짜 `execve` 를 넣는다 |
| 위치 | `_main` 안에서 `parser.parse_args` 바로 뒤, `args.func` 를 부르기 전이다. `follow.follows(args)` 가 참이면 `_client_config` 와 `_resolve_server` 로 서버 · 토큰 · 파일 값을 구해 `follow.maybe_follow(...)` 에 넘긴다. 설정 파일이 깨졌거나 서버가 없으면 따라가기를 건너뛰고 명령이 오늘의 문장으로 실패하게 둔다. `RCM_CLIENT_VERSION` 값이 틀린 `ConfigError` 만 종료 2 다 |
| 플래그 | 최상위 `--no-follow`(`dest="no_follow"`, `default=False`). `client_opts` 의 `--no-follow` 는 `default=argparse.SUPPRESS`. `worker` 에도 둔다(PR B) |
| `_client_row(client, h, state=None)` | §2.9 의 꼬리. `state` 가 None 이면 오늘과 같은 글자 |
| `cmd_version` | §2.9 의 둘째 줄과 `--json` 의 `follow` |
| `cmd_worker` | PR B: 기동 따라가기, `WorkerFollow` 처리, `worker.restart_for` 면 `follow.restart_via_installed` |

### 5.3 `config.py` · 템플릿

- `_CLIENT_KEYS` 에 `client_version`. `ClientConfig.client_version: str = ""`. 값 검사는 `follow.resolve_setting` 이 한다.
- `templates/client.toml` 에 주석 한 줄: `# client_version = "follow"   # follow (default) | installed | X.Y.Z`.

### 5.4 서버 · 와이어

| 자리 | 추가 | PR |
|---|---|---|
| `/api/health` `client_wheel` | `requires_python: str \| null` | A |
| `clientwheel.ASSET_DIRS` | `"skills"` | A |
| 서버 로그 | `client <token name> runs v<X>` (프로세스당 쌍마다 한 번) | A |
| `POST /worker/heartbeat` 응답 | `server_version: str` | B |
| `POST /worker/register` 409(버전) 본문 | `server_version: str` | B |
| `schema_version` | 그대로 1. 키 추가뿐이다 | |

### 5.5 `remote_worker.py` (PR B)

- `RemoteWorker.follow_to: str | None` 은 ③ 을 보고 정한 목표다. `_lane_loop` 는 이것이 있으면 claim 하지 않는다.
- `RemoteWorker.restart_for: str | None` 은 도는 잡이 0 이 된 순간 정해지고 `stopping` 을 켠다. `run()` 이 끝난 뒤 `cmd_worker` 가 본다.
- `WorkerFollow(Exception)` 은 ② 에서 `register()` 가 던진다. `.version` 을 든다.
- 워커의 health 와 `/client/` 요청은 워커 토큰을 Bearer 로 싣는다. 읽기 규칙(`_read_only_ok`)은 유효한 토큰이면 종류를 가리지 않는다.

## 6. 테스트

시험을 먼저 쓴다. 서버는 자기 버전의 wheel 만 주므로 「다른 버전의 서버」는 시험 도우미가 만든다. `tests/follow_helpers.py`
의 `fake_wheel(version, *, init_version=None, requires_python=">=3.11", extra=())` 는 설치본 파일로 wheel 을 조립하되
`__init__.py` 의 `__version__` 과 dist-info 를 그 버전으로 바꾼다. 작은 `ThreadingHTTPServer` 가 `/api/health` 와
`/client/<이름>` 을 흉내 낸다(`tests/test_docs_client_wheel.py` 에 같은 꼴이 이미 있다). `tests/conftest.py` 를 새로 두고
`XDG_CACHE_HOME` 을 `tmp_path` 로 돌려 시험이 사용자의 `~/.cache` 를 건드리지 않게 한다.

### F: 따라가기 (PR A)

준비의 「설치본」은 `install_kind` 를 주입한 값이다. 「exec」 은 가짜 `execve` 가 받은 인자의 기록이다. 「실제 exec」 이라고
적힌 경우만 진짜 하위 프로세스로 돈다.

| # | 갈래 | 준비 | 기대 |
|---|---|---|---|
| F1 | 베스트 | 설치본 0.7.0 · 서버 0.7.0 | exec 없음, `/client/` 요청 0, health 1번. 10분 안 두 번째 실행은 health 0번 |
| F2 | 베스트 | 설치본 0.7.0 · 서버 0.7.1 · 캐시 빔 | `/client/` 1번, 폴더 `0.7.1-<sha8>` 생성, 표지 있음, exec 인자 `[py, -P, -c, BOOT, *argv]` 와 `RCM_FOLLOWED=0.7.1`. stderr 에 `following server v0.7.1` 한 줄 |
| F3 | 베스트 | F2 다음 실행 | `/client/` 0번, health 0번(10분 안), 같은 폴더로 exec, stderr 비어 있음 |
| F4 | 베스트 | 설치본 0.7.0 · 서버 0.6.1 | 0.6.1 로 내려가 exec |
| F5 | 베스트 | 실제 exec: 설치본 venv(비 editable) · 가짜 서버 0.7.1 · `rcm presets` | 자식이 0.7.1 코드로 돌고(가짜 wheel 의 `cli.py` 가 표식을 찍는다) 종료 코드가 자식의 것 |
| F6 | 고정 | `RCM_CLIENT_VERSION=0.6.1` 로 네 갈래: 설치본이 0.6.1 / 캐시에 0.6.1 / 서버가 0.6.1 / 셋 다 아님 | 설치본 / 캐시 exec 이고 health 0번 / 받아서 exec / 설치본 + `pinned_unavailable` |
| F7 | 고정 | `--no-follow` 를 최상위와 하위 명령 뒤 두 자리에 | 둘 다 health 0번, exec 없음 |
| F8 | 고정 | `RCM_CLIENT_VERSION=installed` | health 0번 |
| F9 | 우선순위 | `client.toml` `client_version = "installed"` + `RCM_CLIENT_VERSION=follow` | 따라간다(환경 변수가 이긴다). 반대로 두면 안 따라간다 |
| F10 | 우선순위 | editable 설치 · 설정 없음 | 안 따라간다, 경고 없음, `rcm version` 둘째 줄 `follow: off (editable install)` |
| F11 | 우선순위 | editable 설치 + `RCM_CLIENT_VERSION=follow` | 따라간다 |
| F12 | 우선순위 | 메타데이터 버전 ≠ `__version__` (pull 한 editable) | `install_kind() == "source"`, 안 따라간다 |
| F13 | 에러 | `RCM_CLIENT_VERSION=lates` | 종료 2, `ConfigError` 문장, 서버에 요청 0 |
| F14 | 에러 | 서버 꺼짐 · 기록 없음 | 설치본으로 계속, 경고 `unreachable` 한 줄, 명령 자신의 연결 실패 문장은 그대로 |
| F15 | 에러 | 서버 꺼짐 · 11분 된 기록 0.7.1 · 캐시 0.7.1 | 0.7.1 로 exec, 경고 없음 |
| F16 | 에러 | health 503 · 본문에 `version` 과 `client_wheel` | 따라간다(update-client.sh 와 같은 판단) |
| F17 | 에러 | health 500 · 본문 없음 | `unreachable` |
| F18 | 에러 | `client_wheel: null` · `client_wheel_error: "metadata_missing"` | `no_wheel` 경고에 코드가 들어간다. 캐시에 같은 버전이 있으면 그것으로 exec |
| F19 | 에러 | health 에 `client_wheel` 키 없음(옛 서버) | `old_server` |
| F20 | 에러 | sha256 불일치 | 경고 `sha_mismatch`, `clients/` 아래에 대상 폴더도 `.tmp-*` 도 없다, exec 없음 |
| F21 | 에러 | `/client/` 가 `client_wheel.bytes` 보다 길게 보냄 | 끊고 `unpack` 또는 `sha_mismatch`, 대상 폴더 없음 |
| F22 | 에러 | 풀다가 `OSError(ENOSPC)`(파일 쓰기를 주입) | `unpack: No space left on device`, 대상 폴더 없음, 임시 폴더 지워짐 |
| F23 | 에러 | `read_auth = "basic"` 서버 · 토큰 있음 | 따라간다(Bearer 를 실었다) |
| F24 | 에러 | `read_auth = "basic"` 서버 · 토큰 없음 | `auth` |
| F25 | 에러 | 가짜 wheel 의 `Requires-Python: >=3.99` (health 키로 한 번, health 에 키 없이 METADATA 로 한 번) | 둘 다 `python`. 앞쪽은 `/client/` 요청 0 |
| F26 | 에러 | `Requires-Python: ~=3.11` (해석 못 하는 꼴) | `python`, 따라가지 않는다 |
| F27 | 에러 | `clients/` 가 0o777 · 남의 소유(소유자 검사는 `os.stat` 주입) | `cache_unsafe`, 받지 않는다 |
| F28 | 에러 | wheel 에 `../evil.py` · 절대 경로 · 심링크 항목 · `other_pkg/x.py` | 각각 `unpack`, 대상 폴더 없음, 캐시 밖에 파일이 안 생긴다 |
| F29 | 에러 | 가짜 wheel 의 `__init__.py` 가 0.7.0 인데 이름은 0.7.1 | `version_mismatch`, 대상 폴더 없음 |
| F30 | 엣지 | 두 프로세스가 빈 캐시에서 같은 0.7.1 을 동시에 받는다(실제 프로세스 둘, 시작 장벽으로 맞춘다) | 대상 폴더 하나, 표지 하나, 둘 다 같은 폴더로 exec, `.tmp-*` 없음 |
| F31 | 엣지 | `RCM_FOLLOWED=0.7.1` 이 이미 있는 환경 · 서버 0.7.2 | health 0번, exec 없음(고리 방지) |
| F32 | 엣지 | 실제 exec: BOOT 가 가리키는 폴더를 실행 직전에 지운다 | 자식이 설치본을 import 하고 `the cached client did not load` 한 줄 |
| F33 | 엣지 | 서버 주소를 바꿔 가며(8790 은 0.6.1, 8791 은 0.7.1) | 기록 둘, 캐시 폴더 둘, 각 서버에 맞는 버전으로 exec |
| F34 | 엣지 | 같은 버전 0.7.1, sha 가 다른 두 서버 | 폴더 둘(`0.7.1-aaaa…` · `0.7.1-bbbb…`) |
| F35 | 엣지 | 캐시에 다섯 버전, 하나는 다른 프로세스가 `.lock` 을 공유 잠금 중 · 새 버전을 받는다 | 최근 사용 3개 + 잠긴 것 + 새 것만 남는다. 지운 것은 `.trash-*` 를 거쳐 사라진다 |
| F36 | 엣지 | `init` `token` `discover` `skills` `version` `gc --config … --dry-run` 를 `RCM_CLIENT_VERSION=follow` 로 | 각각 health 요청 0 |
| F37 | 엣지 | `rcm serve` 를 `RCM_CLIENT_VERSION=follow` · 캐시에 다른 버전이 있는 채로 | 따라가기 코드를 부르지 않는다(`maybe_follow` 가 불리면 실패하는 가짜로 확인). 서버의 `/api/health.version` 은 설치본 |
| F38 | 엣지 | `os.name = "nt"` 를 주입 | `platform`, 설치본. pyproject classifiers 에 Windows 가 없음을 `test_packaging` 이 잠근다 |
| F39 | 엣지 | `main(["jobs"])` 처럼 인자 목록을 넘긴 호출 · 서버 0.7.1 | health 0번, exec 없음 |
| F40 | 엣지 | `client.toml` 에 `client_version = "follow"` · 서버 0.6.1 · `FOLLOW_CONFIG_SINCE = "0.7.0"` | `config_key` 경고와 함께 설치본으로 돈다. 같은 상황에서 `RCM_CLIENT_VERSION=follow` 만 쓰면 내려간다 |
| F41 | 엣지 | `server = "auto"` · 발견된 서버 하나 · 버전 다름 | 발견은 한 번, 자식 환경에 `RCM_SERVER=<찾은 주소>` |
| F42 | 엣지 | `rcm run gate --no-wait` 가 따라가다 실패 | stdout 은 JSON 한 줄 그대로 파싱되고 경고는 stderr 에만 |
| F43 | 엣지 | 실제 exec: 자식 잡이 실패해 `rcm wait` 가 1 · Ctrl-C 로 130 | 부모가 본 종료 코드가 1 · 130, PID 가 같다 |
| F44 | 표시 | 기록 없음 · 설정 기본 | `rcm version` 한 줄, stderr 비어 있음(기존 잠금 그대로) |
| F45 | 표시 | 기록 있음 | 둘째 줄 `follow: on (default) · server … v0.7.1 checked …`, `--json` 의 `follow` 키 모양 |
| F46 | 표시 | `rcm check` 를 네 상태(따라간 자식 · 실패 · 고정 · editable)에서 | §2.9 의 네 줄. FAIL 은 `min_client_version` 아래일 때뿐 |
| F47 | 표시 | `rcm check` 는 10분 기록이 있어도 | health 를 새로 읽는다 |

### S: 서버 쪽 (PR A)

| # | 준비 | 기대 |
|---|---|---|
| S1 | `build_wheel` | 파일 목록 = 패키지 폴더 파일 목록(`__pycache__` · `.pyc` · 점 파일 제외). `skills/*/SKILL.md` 가 들어 있다 |
| S2 | 조립한 wheel 을 새 venv 에 `pip install` | `rcm skills list` 가 스킬 다섯을 설명과 함께 보이고, `install` 이 각 폴더에 `SKILL.md` 를 쓴다(pip 없으면 skip) |
| S3 | `/api/health` | `client_wheel.requires_python == ">=3.11"`. METADATA 에 줄이 없으면 `null` |
| S4 | 클라이언트 토큰 `macbook` 으로 `User-Agent: rcm/0.7.1` 요청 셋, 이어서 `rcm/0.7.2` 하나 | 로그 `client macbook runs v0.7.1` 한 줄과 `… v0.7.2` 한 줄 |
| S5 | `User-Agent` 가 rcm 이 아님(브라우저) · 토큰 없음 | 로그 없음 |

### W: 원격 워커 (PR B)

| # | 갈래 | 준비 | 기대 |
|---|---|---|---|
| W1 | 베스트 | 비 editable 워커 0.7.0 · 서버 0.7.1 · 기동 | 등록 전에 0.7.1 로 exec. 등록 요청의 `version` 이 0.7.1 |
| W2 | 베스트 | 서버가 워커 아래에서 올라갔고 워커가 재시작(등록 409, 본문 `server_version`) | 따라가 다시 등록, 종료 2 없음 |
| W3 | 베스트 | heartbeat `server_version` 이 다름 · 도는 잡 없음 | claim 이 멈추고 `restart_via_installed` 가 불린다. 그 실행의 환경에 `RCM_FOLLOWED*` 가 없다 |
| W4 | 베스트 | W3 인데 잡 하나가 도는 중 | 새 claim 0, 잡이 끝나 `finish` 를 보낸 뒤에 재실행. 서버에서 그 잡은 lost 가 아니라 원래 결과 |
| W5 | 에러 | ② 에서 sha 불일치 | 종료 2, 문장에 `sha256 mismatch` |
| W6 | 에러 | ③ 이 300초 안에 다시 옴(`RCM_WORKER_RESTARTED_AT` 이 60초 전) | 재실행 없음, 로그 한 번 |
| W7 | 에러 | `exec` 가 `OSError` | 로그 한 줄, 종료 1 |
| W8 | 엣지 | 옛 서버(heartbeat 에 `server_version` 없음) | 아무 일 없음 |
| W9 | 엣지 | editable 워커 · ③ | 재실행 없음, 로그 한 줄(프로세스당 한 번) |
| W10 | 엣지 | `RCM_CLIENT_VERSION=0.7.0` 워커 · ③ · ② | ③ 무시, ② 는 종료 2 이고 문장에 `pinned` |
| W11 | 엣지 | 드레인 중 SIGTERM | 오늘의 정지 경로, 재실행 없음 |
| W12 | 엣지 | 드레인 시작 직후 long-poll claim 이 잡을 돌려줌 | 그 잡을 끝까지 돌리고 보고한 뒤 재실행 |
| W13 | 엣지 | 실제 exec(e2e): 두 프로세스 시험의 워커가 ③ 으로 재실행 | 재실행 전후 `os.getpid()` 가 같고 같은 stderr 파일에 로그가 이어진다 |
| W14 | 엣지 | 프리셋 잡의 환경 | `RCM_FOLLOWED*` · `RCM_WORKER_RESTARTED_AT` 이 없다 |

### mutcheck 에 더할 변이

| 변이 | 잡는 시험 |
|---|---|
| sha256 비교를 지운다 | F20 |
| 기본값에서 editable 판정을 뒤집는다 | F10 |
| `RCM_FOLLOWED` 고리 방지를 지운다 | F31 |
| 임시 폴더 없이 대상 폴더에 바로 푼다 | F22 · F30 |
| `serve` 를 따라가는 명령에 넣는다 | F37 |
| 드레인이 도는 잡을 기다리지 않는다(PR B) | W4 |

## 7. 실기 확인 절차

운영 설정(`~/.config/rcm/server.toml`), 운영 데이터(`~/.local/share/rcm`), 포트 8787 은 쓰지 않는다. 다른 세션이 8788 을 쓴다.
시험 서버의 `data_dir` 은 `/tmp` 아래에 두지 않는다(dolomood selftest 가 `/private/tmp` 를 거부한 적이 있다).

| 자리 | 버전 | 포트 | data_dir | venv |
|---|---|---|---|---|
| 서버 A | 0.6.1 (릴리스 wheel) | 8790 | `~/.local/share/rcm-devtest-m5q-a` | `~/.local/share/rcm-devtest-m5q-a/venv` |
| 서버 B | PR A 빌드, `__version__` 을 임시로 `0.7.0.dev0` 으로 바꾼 사본 | 8791 | `~/.local/share/rcm-devtest-m5q-b` | 워크트리 `.venv` 가 아닌 새 venv 에 그 사본을 비 editable 로 |
| 클라이언트 C | 서버 B 와 같은 빌드 | 없음 | 캐시는 `XDG_CACHE_HOME=~/.cache/rcm-devtest-m5q` | 또 하나의 새 venv, 비 editable |

각 서버는 자기 `server.toml`(포트, `data_dir`, `advertise = false`, 알림 훅 없음, 데모 프리셋 하나)과 자기 토큰을 쓴다.

1. C 로 `rcm jobs --server http://127.0.0.1:8791` 을 친다. 같은 버전이라 아무 일이 없다. `rcm version` 둘째 줄을 본다.
2. C 로 `rcm check --server http://127.0.0.1:8790` 을 친다. stderr 에 `following server v0.6.1` 이 한 번 나오고 0.6.1 의 옛 `client`
   행이 `same as server` 를 말한다. `~/.cache/rcm-devtest-m5q/rcm/clients/0.6.1-*` 가 생긴다.
3. 2 를 다시 친다. stderr 가 비어 있고 서버 A 에 `/client/` 요청이 없다(서버를 `debug` 로 띄워 접근 로그로 본다).
4. C 로 서버 A 에 데모 프리셋을 `rcm run` 한다. 0.6.1 코드가 작업 트리를 올리고 종료 0 이다.
5. C 로 다시 서버 B 에 붙는다. 설치본으로 돈다.
6. `RCM_CLIENT_VERSION=0.6.1 rcm check --server http://127.0.0.1:8791` 은 캐시의 0.6.1 로 돌고 `--no-follow` 를 붙인 서버 A 호출은
   설치본으로 돈다.
7. 빈 캐시에서 `rcm jobs --server :8790` 을 열 개 동시에 띄운다. 폴더가 하나만 생기고 `.tmp-*` 가 남지 않는다.
8. 서버 A 를 끄고 4 의 잡에 `rcm wait --job <번호> --server http://127.0.0.1:8790` 을 친다. 마지막 기록의 0.6.1 로 돌면서 연결
   실패를 말한다.
9. 서버 B 로그에 `client <토큰> runs v0.7.0.dev0` 이 있다.
10. (PR B) 서버 A 에 워커 토큰을 만들고 C 의 venv 로 `rcm worker --server http://127.0.0.1:8790 --pool m5q` 를 띄운다. 등록 버전이
    0.6.1 이다. 이제 서버 A 를 멈추고 같은 `data_dir` 과 포트 8790 으로 서버 B 의 빌드를 띄운다(서버 업그레이드 흉내, DB v23 그대로).
    워커가 heartbeat 의 `server_version` 을 보고 재실행하고 `rcm machines` 에서 워커 버전이 `0.7.0.dev0` 이 된다. 워커 PID 가 같다.
11. 끝나면 두 서버와 워커를 멈추고 `~/.local/share/rcm-devtest-m5q-*` 와 `~/.cache/rcm-devtest-m5q` 를 지운다.

## 8. 안 만드는 것

- **pip 로 설치본을 갈아끼우는 `rcm self-update`.** 결정 83 의 이유(설치 방식마다 다르고 도는 프로세스 자신을 바꾼다)가 그대로다.
  따라가기는 설치본을 건드리지 않는다.
- **서버가 여러 버전의 wheel 을 보관하고 주는 것.** 고정 버전을 서버에서 받으려면 서버가 옛 버전을 들고 있어야 한다. 운영 머신에
  새 저장 의무가 생기고 「서버가 도는 코드를 준다」(결정 81)는 단순함을 깬다. 캐시와 설치본으로 충분하다.
- **서명.** 릴리스 서명(Sigstore · GPG)은 의존성이나 키 관리를 들인다. 그리고 서버 wheel 은 서버가 조립해 릴리스 wheel 과 바이트가
  다르므로(결정 81) 릴리스 서명으로 검증할 수 없다. 파일별 해시를 GitHub 릴리스와 대조하는 길은 런타임 경로에 GitHub 를 다시
  넣는다(결정 30). 후속 후보로만 둔다.
- **https.** 서버에 TLS 를 넣는 일은 인증서 배포가 따라온다. Tailscale 주소가 같은 효과를 더 싸게 낸다. 문서로 권한다.
- **처음 본 sha 고정(trust on first use).** 같은 버전에 다른 sha 가 오면 거부하는 규칙이다. 같은 번호로 다시 빌드하는 개발 서버가
  정상적으로 걸린다. 오너가 원하면 후속으로 `client_version = "0.7.1@sha256:<hex>"` 꼴을 검토한다.
- **응답마다 서버 버전 헤더를 싣고 명령 도중에 갈아타는 것.** 한 명령은 한 버전으로 끝난다. 10분 기록이면 충분하다.
- **DB 에 클라이언트 버전 기록.** 로그 한 줄(결정 166)로 시작한다. 화면에 보여야 하면 후속에서 컬럼을 검토한다.

## 9. PR 순서 · 완료 기준

| PR | 브랜치 | 만지는 곳 |
|---|---|---|
| 0 | `docs/m5q-client-follows-server-workplan` | 이 문서 |
| A | `feat/m5q-client-follows-server-version` | `follow.py`(새) · `cli.py`(진입 · 플래그 · version · check) · `config.py`(키 하나) · `templates/client.toml` · `clientwheel.py`(skills) · `server.py`(health 키 · 로그 한 줄) · `tests/conftest.py`(새) · `tests/follow_helpers.py`(새) · 문서 |
| B | `feat/m5q-worker-restarts-on-server-version` | `remote_worker.py` · `remote_workers.py`(heartbeat · 409 본문) · `cli.py`(`cmd_worker`) · `follow.py`(`restart_via_installed`) · `examples/launchd` · `examples/systemd` 주석 · 문서 |
| C | `docs/m5q-follow-plan-and-guides` | `PLAN.md` 결정 147~172 행 · 이 문서 §12 · README 의 한 줄 안내 |

B 는 A 의 `follow.py` 를 쓰므로 A 가 dev 에 들어간 뒤 시작한다. 리드가 문서를 별도 PR 로 나누자고 했지만 집안 규칙(「사용자에게
보이는 변경은 같은 PR 에서 문서를 고친다」)과 문서 잠금 시험 때문에 A 와 B 가 각자 자기 문서를 들고 간다. 기능별 문서는 이렇다.

- A: `docs/operating.md` 「Keeping clients on the server's version」 재작성. 따라가기가 첫 길이고 고정 · 끄기 · 보안 문단, 그리고
  `update-client.sh` 는 「설치본(부트로더)을 올리는 길」로 남는다. 잠금 시험(`test_docs_client_wheel.py`)이 요구하는 말
  (`client_wheel` · `min_client_version` · `/client/remote_ci_monitor-` · `sha256` · `pip install` · `rcm check` ·
  `examples/session/update-client.sh` · `read_auth`)은 그대로 남긴다. `docs/configuration.md` 의 `client.toml` 키 · 환경 변수,
  `docs/usage.md` · `docs/usage.ko.md` 한 문단, CHANGELOG [Unreleased].
- B: `docs/operating.md` 의 워커 절에 ③ 과 감독자 문단, CHANGELOG [Unreleased].
- C: 계획서와 이 문서의 마무리.

완료 기준은 PR 마다 다음과 같다.

1. 시험을 먼저 쓴다. `ruff check . && ruff format --check . && pytest` · `python scripts/mutcheck.py` · `scripts/smoke_install.sh` 가 통과한다.
2. mutcheck 에 §6 의 변이를 더한다(A 다섯, B 하나).
3. **격리 에이전트 검증.** 구현하지 않은 새 에이전트가 이 문서와 diff 만 보고 §6 의 표와 §7 의 절차를 실제로 돌린다.
4. dev 머지 뒤 A 와 B 를 묶어 릴리스한다. 기본 동작이 바뀌므로 마이너다. M5p 가 먼저 0.7.0 을 가져가면 이 릴리스는 0.8.0 이고,
   `FOLLOW_CONFIG_SINCE` 는 그 번호로 맞춘다. 릴리스 PR 의 시험이 `FOLLOW_CONFIG_SINCE <= __version__` 를 잠근다.
5. 운영 반영은 기존 절차다. 서버를 올려도 클라이언트가 바로 따라오지는 않는다. 각 세션 머신의 설치본이 한 번은
   `FOLLOW_CONFIG_SINCE` 이상이어야 한다(부트스트랩). 그 뒤로는 서버를 올리기만 하면 된다.

## 10. 이 마일스톤 밖: dolomood-renew 쪽 (그쪽 세션의 일)

dolomood `scripts/remote_ci.sh`(origin/dev)의 클라이언트 관리가 이렇게 바뀐다. 고치는 일은 그쪽 세션이 한다.

| 지금 | M5q 뒤 |
|---|---|
| `RCM_WHEEL` = GitHub 릴리스 v0.5.0, `RCM_MIN_VERSION` = 0.5.0. 서버가 오를 때마다 손으로 올렸다 | 부트스트랩 전용이 된다. 한 번 `FOLLOW_CONFIG_SINCE` 로 올리면 서버가 오를 때 다시 올릴 필요가 없다. 따라가기 부트로더에 결함이 있을 때만 올린다 |
| `--update-client` 가 클라이언트 venv 를 서버 wheel 로 다시 깐다 | 설치본(부트로더)을 올리는 길로만 남는다. 평소에는 필요 없다 |
| `client_drift_hint` 가 「서버보다 낮다」를 한 줄로 알린다 | 따라가면 차이가 없으니 조용해진다. `RCM_CLIENT_VERSION` 을 고정한 경우만 의미가 있다. 남길지는 그쪽 판단이다 |
| `may_reinstall` · `venv_is_editable` 가 운영 editable venv 를 덮지 않는다 | 그대로 필요하다. rcm 의 기본도 editable 은 따라가지 않으니 두 규칙이 맞물린다 |
| `~/.local/bin/rcm` 이 Mac mini 에서는 운영 editable venv 를 가리킨다 | 그 rcm 은 editable 이라 따라가지 않는다. 오늘과 같다 |
| selftest ⑨-b 의 가짜 rcm 버전이 바닥 이상이어야 한다 | 바닥을 올릴 때 한 번 맞춘다 |

dolomood 에 설치된 `rcm-*-connect` 스킬이 rcm 의 플래그를 부르는 자리도 그쪽 세션이 본다. 서버가 내려가면 새 플래그가 사용법 오류가
되는 경우(§2.7)가 있다.

운영 서버에서 켜거나 바꿀 설정은 없다. 따라가기는 세션 머신의 설정으로 켜고 끈다.

## 11. 결정 (147~172, 추천값으로 구현하고 확인을 기다린다)

| # | 결정 | 추천 |
|---|---|---|
| 147 | 기본 동작 | 서버에 붙는 명령은 서버 버전으로 돈다. 설치본은 부트로더다. **오너 확인** |
| 148 | 방향 | 문자열이 다르면 따라간다. 내려가기도 따라간다. 와이어 계약(`min_client_version`)은 그대로 |
| 149 | 받고 실행하는 길 | health 의 `client_wheel` → `/client/…whl` → sha256 → pip 없이 풀기 → 같은 인터프리터 `-P -c BOOT` 로 `execve`. `PYTHONPATH` 는 안 쓴다 |
| 150 | 캐시 자리 | `$XDG_CACHE_HOME/rcm/`(기본 `~/.cache/rcm/`). 서버 기본 `data_dir` 인 `~/.local/share/rcm` 과 떼어 둔다 |
| 151 | 버전 확인 | 서버 주소별 10분 기록, health 3초 제한. 못 닿으면 마지막 기록과 캐시 |
| 152 | 설정 우선순위 | `--no-follow` > `RCM_CLIENT_VERSION` > `client.toml` `client_version` > 설치 종류 기본값. 잘못된 값은 종료 2 |
| 153 | `client.toml` 키 | 최상위 `client_version`. 키가 있으면 `FOLLOW_CONFIG_SINCE` 아래로는 안 간다. 고정의 주된 길은 환경 변수 |
| 154 | editable · source 설치 | 기본 `installed`. 명시하면 따라간다 |
| 155 | 실패 | 설치본으로 계속 + 경고 한 줄(§2.6 의 코드). sha 불일치는 캐시에 아무것도 안 남긴다 |
| 156 | 고정 `X.Y.Z` | 설치본 → 캐시 → 서버가 X 일 때 받기 → 설치본 + 경고 |
| 157 | Python 요구 | health 의 `requires_python`(새 키)과 METADATA. `>=` `<` `!=` 와 쉼표만 해석하고 모르는 꼴은 안 따라간다 |
| 158 | 따라가는 명령 | §2.3 표. `serve` 는 절대 안 따라간다. `worker` 는 PR B |
| 159 | 고리 막기 | 자식은 `RCM_FOLLOWED` 를 보고 다시 안 따라간다. 푼 `__version__` 이 목표와 같아야 받는다. BOOT 가 import 위치를 확인한다 |
| 160 | 프로그램 안 호출 | `main(argv)` 에 목록을 넘기면 아무것도 안 한다 |
| 161 | 캐시 청소 | 받은 직후에만. 최근 사용 3개 + 잠긴 것 + 지금 목표를 남긴다 |
| 162 | 캐시 폴더 권한 | 0700 · 내 소유 · 그룹과 남이 쓸 수 없음. 아니면 안 따라간다 |
| 163 | 풀기 안전 | 이름 허용 목록 · `..` 와 절대 경로와 심링크 거부 · 크기 상한 |
| 164 | 표시 | `rcm version` 둘째 줄(정보 있을 때만) · `--json` 의 `follow` · `rcm check` `client` 행 꼬리 · 처음 받을 때 stderr 한 줄. `--version` 은 한 줄 그대로 |
| 165 | wheel 의 skills | `ASSET_DIRS` 에 `skills`. wheel 파일 목록 = 패키지 파일 목록을 시험이 잠근다 |
| 166 | 클라이언트 버전 기록 | 서버 로그 한 줄(`client <토큰 이름> runs v<X>`). DB 변경 없음 |
| 167 | 보안 | 기본으로 켠다. 평문 HTTP 위험을 문서에 그대로 적고 끄는 법 셋을 준다. 서명 · https · sha 고정은 후속. **오너 확인** |
| 168 | mDNS 로 찾은 서버 | 따라간다. 둘 이상이면 지금처럼 거절 |
| 169 | 원격 워커 | 기동 · 등록 409 · heartbeat `server_version` 셋이 계기. 잡이 없을 때 설치본 부트로더로 재실행, 300초 상한. 설정은 환경 변수와 플래그만 |
| 170 | Windows | 따라가기 꺼짐(`os.name != "posix"`). 클라이언트 자체가 이미 POSIX 전용이다 |
| 171 | 릴리스 | A 와 B 를 묶어 마이너. `FOLLOW_CONFIG_SINCE` 는 그 번호 |
| 172 | dolomood | 부트스트랩만 남는다. 그쪽 레포의 변경은 그쪽 세션 |

## 12. 구현하며 바뀐 것

(구현과 격리 검증이 끝난 뒤 PR 마다 채운다.)
