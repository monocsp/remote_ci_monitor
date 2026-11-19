# M5q 작업 명세: 클라이언트가 서버 버전을 따라간다

> 2026-11-19 오너 요청 원문: 「네가 이 버전 업데이트 하면, 다른 세션 다른 컴퓨터에서 이 rcm 을 바라보고
> 있다면, 버전을 고정해서 쓰는 게 아니라 이 rcm 의 프로덕트 버전을 가져와서 그걸 그대로 쓰도록 하는 건
> 어때? 여기는 버전 업데이트 되었는데 다른 세션들에서 버전을 올려야 하는 게 좀 그래서. 물론 버전 명시하면
> 그 버전을 계속 쓰는 것도 가능하게 하는 건 좋아 보인다.」
>
> 결정 번호는 149~174 다(M5p 가 125~148 을 쓴다). 보안 결정 169 는 오너가 (a) 를 골랐다. 나머지는 추천값으로
> 구현하고 확인을 기다린다.
>
> 이 판은 격리 설계 검토를 반영한 둘째 판이다. 초안과 달라진 것은 §13 「설계 검토가 바꾼 것」에, 구현 중에 바뀐
> 것은 §12 「구현하며 바뀐 것」에 남긴다.

## 0. 한 줄

서버에 붙는 `rcm` 명령은 그 서버가 돌리는 버전의 코드로 돈다. 설치된 `rcm` 은 서버 버전을 확인하고 다르면
서버가 주는 wheel 을 받아 캐시에 풀고 같은 인터프리터로 자기를 그 코드로 다시 실행한다. 설치본은 이제
부트로더 역할만 한다. 버전을 고정하고 싶은 사람은 환경 변수 하나나 한 줄짜리 파일 하나로 고정한다.

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
`.py` 템플릿이 있는 셋만 설명 없이 보이고 `rcm skills install` 은 그 `.py` 몇 개만 복사한다. 이 마일스톤은 그 wheel 을
실제로 실행할 코드로 쓰므로 따라가기보다 먼저 고친다(PR A0, 결정 167).

따라가기가 더하는 비용은 재실행 한 번의 import 다. 명령마다 0.1 초쯤 늘고 서버 버전 확인은 10분에 한 번이다.

### 1.5 코드에서 확인한 제약

리드가 준 설계 방향을 코드와 대조하다가 다음 여섯 가지가 걸렸다. 각각 §2 이하에서 방향을 고쳤다.

| 제약 | 자리 | 영향 |
|---|---|---|
| `client.toml` 은 평면 키다(`server` `token` `token_env` `label`). 모르는 키가 있으면 `ConfigError` 다 | `config.py` `_CLIENT_KEYS` · `load_client_config` | 새 키를 넣은 파일을 옛 버전이 읽으면 죽는다. 0.6.1 로 고정하거나 내려가면 그 0.6.1 이 같은 파일을 읽다 죽는다. 그래서 `client.toml` 에는 키를 넣지 않고 부트로더만 읽는 파일을 따로 둔다(결정 155) |
| 서버의 기본 `data_dir` 이 `~/.local/share/rcm` 이다 | `config.py` `DEFAULT_DATA_DIR` | 리드 안의 `~/.local/share/rcm/clients/` 는 빌드 머신에서 운영 데이터 폴더 안이다(결정 152) |
| 서버는 자기 버전의 wheel 만 준다. 다른 이름은 404 다 | `server.py` `_client_wheel` | 고정 버전은 서버가 마침 그 버전일 때만 받을 수 있다. 리드 안과 같다 |
| 워커 등록은 버전이 정확히 같아야 받는다(409 `install the same release`). 등록은 DB 에 남아 서버 재시작을 넘긴다 | `remote_workers.py` `worker_register` | 서버를 올린 뒤에도 이미 등록된 워커는 옛 버전으로 계속 돈다. 그 워커가 재시작하면 등록이 409 로 막혀 종료 2 로 끝난다. PR B1 의 계기가 이 409 다(결정 171) |
| `rcm version` 은 한 줄이라고 시험이 잠근다 | `tests/test_cli_m4.py` | 따라가기 정보를 늘 붙이면 시험을 약하게 만들어야 한다. 정보가 있을 때만 둘째 줄을 쓴다(결정 166) |
| `cli.py` 가 맨 위에서 `submissions` 를, 그것이 `fcntl` 을 import 한다 | `submissions.py` | Windows 지원은 지금도 사실상 없다. pyproject classifiers 에도 Windows 가 없다(결정 172) |

## 2. 설계: 따라가기 (PR A)

### 2.1 흐름

`rcm` 콘솔 스크립트가 `cli.main()` 을 부르면 argparse 가 돌기 **전에** 다음을 한다. 설치본의 파서는 더 새 서버의
명령이나 플래그를 모를 수 있다. 설치본 파서가 먼저 돌면 따라가기 전에 사용법 오류로 끝난다.

1. argv 를 가볍게 훑는다(`follow.prescan`). 찾는 것은 여섯 가지다. 첫 위치 인자(명령), `--no-follow`,
   `--server` 와 `--server=…`, `--token` 과 `--token=…`, `--client-config` 와 `--client-config=…`, `-h` 와 `--help`.
   값을 받는 세 옵션은 다음 토큰을 값으로 먹는다. `--` 를 만나면 거기서 멈춘다. 해석과 오류 판정은 하지 않는다.
   그것은 실제로 돌 버전의 파서가 한다.
2. 이 명령이 따라가는 명령인지 본다(§2.3). `-h` · `--help` · `--version` 이 있으면 따라가지 않는다. 그때 보이는 도움말은
   설치본의 도움말이다.
3. 설정을 정한다(§2.2). `installed` 면 끝이다.
4. 서버 주소를 정한다. 플래그, 환경 변수, `client.toml`, mDNS 발견 순서이고 명령이 쓰는 규칙과 같다. 설정 파일이
   깨졌거나 서버가 없으면 따라가기를 건너뛰고 명령이 오늘의 문장으로 실패하게 둔다.
5. 서버 버전을 안다. 10분 안의 기록이 있으면 그것을 쓰고 없으면 `GET /api/health` 를 3초 제한으로 한 번 부른다(§2.4).
6. 목표 버전을 정한다(§2.7). 목표가 설치본과 같으면 끝이다.
7. 캐시에 목표가 있으면 그 폴더로, 없으면 받아서 풀고 그 폴더로 다시 실행한다(§2.5).
8. 4~7 의 어디서든 실패하면 설치본으로 계속하고 stderr 에 한 줄을 쓴다(§2.6). 그 뒤에 설치본의 argparse 가 돈다.

`main(argv)` 에 인자 목록을 직접 넘겨 부르는 경우(시험, 다른 파이썬 프로그램 안)는 아무것도 하지 않는다(결정 162).
남의 프로세스를 `exec` 로 갈아치우면 안 되기 때문이다.

### 2.2 설정과 우선순위 (결정 154·155·156)

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
| 2 | 환경 변수 `RCM_CLIENT_VERSION` | 셸 프로필에 `export RCM_CLIENT_VERSION=0.6.1` |
| 3 | 한 줄 파일 `$XDG_CONFIG_HOME/rcm/client-version`(없으면 `~/.config/rcm/client-version`) | 내용이 `installed` 한 줄 |
| 4 | 기본값 | 설치 종류가 `wheel` 이면 `follow`, `editable` 이나 `source` 면 `installed` |

- 따라간 자식 프로세스는 이 표를 보지 않고 다시 따라가지 않는다(결정 161, §2.5 의 BOOT).
- **한 줄 파일은 부트로더만 읽는다**(결정 155). `client.toml` 에 키를 두면 그 키를 모르는 옛 버전이 같은 파일을 읽다
  `ConfigError` 로 죽는다(§1.5). 별도 파일은 옛 버전이 열지 않으므로 고정 · 내려가기와 함께 써도 안전하다. 파일은
  앞뒤 공백과 빈 줄을 무시하고 첫 줄만 읽는다. `#` 로 시작하는 줄은 주석이다.
- `RCM_CLIENT_VERSION` 과 파일의 값은 `follow`, `installed`, 또는 버전 정규식(§2.4 의 `VERSION_RE`, 64자 이하)만
  받는다. 아니면 종료 2 와 `ConfigError` 문장으로 끝난다. 파일이 틀렸으면 문장에 그 경로가 들어간다. 오타 때문에
  조용히 따라가거나 조용히 멈추지 않는다.
- **설치 종류**는 `importlib.metadata.distribution("remote-ci-monitor")` 로 정한다. `direct_url.json` 의
  `dir_info.editable` 이 참이면 `editable` 이다. 메타데이터가 없거나 메타데이터의 버전이 `__version__` 과 다르면
  `source` 다. 체크아웃을 `git pull` 한 editable 설치가 뒤쪽에 걸린다. 나머지는 `wheel` 이다. 개발 워크트리의
  `.venv` 와 빌드 머신의 서비스 venv 는 editable 이라 기본으로 따라가지 않는다. 개발 중인 코드를 운영 버전으로
  바꿔치면 안 되기 때문이다. 명시적으로 `follow` 를 주면 editable 도 따라간다.
- `--no-follow` 는 argv 훑기가 어디서든 찾는다. 설치본 파서도 받아야 하므로 최상위 파서와 `client_opts` 둘 다에 둔다.
  하위 파서 쪽은 `default=argparse.SUPPRESS` 로 둬야 최상위 값을 덮지 않는다.

### 2.3 따라가지 않는 명령 (결정 160)

목록에 없는 명령은 전부 따라간다. **설치본이 모르는 명령도 따라간다.** 더 새 서버에 생긴 명령을 쓰려면 그래야 한다.
서버를 모르면 따라갈 수 없으니 설치본의 파서가 오늘처럼 `invalid choice` 로 끝낸다.

| 명령 | 따라가나 | 이유 |
|---|---|---|
| `serve` | 절대 안 따라간다 | 서버가 정본이다. `RCM_CLIENT_VERSION` 도 보지 않는다 |
| `worker` | PR A 에서는 안 따라간다 | 원격 워커는 따로 다룬다(§3, PR B1 · B2) |
| `token` `init` `discover` `version` | 안 따라간다 | 서버 없이 돈다. `token` 은 서버 머신의 DB 를 연다 |
| `gc` 에 `--config` 가 있을 때 | 안 따라간다 | `gc --config … --dry-run` 은 서버 없이 로컬 DB 사본에서 돈다 |
| `skills` | 서버가 설정돼 있을 때만 따라간다 | 서버 쪽 스킬을 받는 게 맞지만 서버 없이도 돌아야 한다. 서버가 비었거나 `auto` 면 mDNS 발견을 하지 않고 설치본으로 돈다. 실패하면 설치본이다 |
| `check` | 따라간다 | 진단도 실제로 돌 코드로 해야 맞다. 대신 10분 기록을 쓰지 않고 health 를 매번 새로 읽는다. `client` 행이 따라가기 상태를 말한다(§2.9) |
| `-h` · `--help` · `--version` 이 있는 호출 | 안 따라간다 | 설치본의 도움말과 버전이 나온다. 문서에 한 줄로 적는다 |
| 그 밖(`run` `wait` `jobs` … 와 모르는 명령) | 따라간다 | 서버와 말한다 |

### 2.4 서버 버전 확인과 기록 (결정 153)

기록 파일은 `<캐시 뿌리>/follow.json` 하나다(캐시 뿌리는 §2.5). 키는 정규화한 서버 주소(스킴, 소문자 호스트, 포트,
끝 `/` 없음)이고 값은 다음과 같다.

```json
{"http://macmini.local:8787": {
   "version": "0.8.0", "checked_at": 1763532000.0, "failed_at": null,
   "wheel": {"path": "/client/remote_ci_monitor-0.8.0-py3-none-any.whl",
             "sha256": "…", "bytes": 707673},
   "wheel_error": null}}
```

- 기록이 10분(`CHECK_TTL_SECONDS = 600`) 안이면 health 를 부르지 않는다. 같은 서버를 여러 주소(LAN IP, Tailscale,
  `.local`)로 부르면 기록도 따로다.
- health 는 3초(`HEALTH_TIMEOUT_SECONDS`) 안에 답해야 한다. 503 이어도 본문에 `version` 이 있으면 쓴다.
  `Client.health()` 가 이미 그렇게 하고 `update-client.sh` 도 같은 판단을 한다.
- **health 실패는 60초 기억한다**(`NEG_TTL_SECONDS = 60`, `failed_at`). 그동안은 health 를 다시 부르지 않는다. 서버가
  죽었을 때 스크립트가 `rcm` 을 연달아 부르면 명령마다 3초씩 붙는 것을 막는다.
- health 를 못 읽었는데 그 서버의 기록이 있으면 기록의 버전과 **sha256 이 둘 다 맞는** 캐시 폴더로 돈다. 경고는
  쓰지 않는다. 서버가 재시작하는 동안의 `rcm wait` 가 그렇다. 명령 자신이 연결 실패를 말할 것이다. 같은 버전이라도
  sha 가 다른 폴더는 쓰지 않는다.
- **값의 형식을 검사한다.** 어긋나면 그 응답을 버리고 `bad_health` 다. 기록에도 남기지 않는다.

  | 값 | 규칙 |
  |---|---|
  | `version` | `VERSION_RE = r"^[0-9]+(\.[0-9]+)*([.+-][0-9A-Za-z]+)*$"`, 64자 이하. 이 값은 폴더 이름과 파일 이름이 된다 |
  | `client_wheel.sha256` | 소문자 16진수 정확히 64자 |
  | `client_wheel.path` | 정확히 `"/client/" + wheel_filename(version)` |
  | `client_wheel.bytes` | 양의 정수, `MAX_WHEEL_BYTES` 이하 |

- **리다이렉트를 따르지 않는다.** health 와 `/client/` 요청은 리다이렉트 처리기를 뺀 opener 로 보낸다. 3xx 는
  `unreachable` 이다. 토큰을 다른 호스트로 실어 나르지 않는다.
- **토큰이 있으면 언제나 Bearer 로 싣는다.** `read_auth = "none"` 서버에서도 그렇다. 클라이언트는 서버의 읽기 규칙을
  미리 알 수 없다.
- 기록 파일은 임시 파일에 쓰고 `os.replace` 로 바꾼다. 동시에 쓰면 나중 것이 이긴다. 기록은 다시 만들 수 있는
  캐시라 그것으로 충분하다.

### 2.5 받기 · 검증 · 풀기 · 재실행 (결정 151·152·161·164·165)

**캐시 뿌리**(결정 152·164). `$XDG_CACHE_HOME/rcm/` 이고 없으면 `~/.cache/rcm/` 이다. `XDG_CACHE_HOME` 이 상대
경로면 XDG 명세대로 무시한다. 리드 안의 `~/.local/share/rcm/clients/` 를 쓰지 않는 이유는 그 폴더가 빌드 머신에서
서버의 기본 `data_dir` 이기 때문이다(§1.5). 다시 받을 수 있는 파일이라 XDG 의 cache 자리가 맞다.

```
~/.cache/rcm/
  follow.json
  clients/                         # 0700, 이 사용자 소유
    0.8.0-a82e75a4/                # <버전>-<sha256 앞 8자>
      remote_ci_monitor/…
      remote_ci_monitor-0.8.0.dist-info/…
      .rcm-client.json             # 완성 표지: {version, sha256, server, fetched_at}
      .lock                        # 쓰는 프로세스가 공유 잠금을 잡는다
    .tmp-<무작위>/                 # 받는 중. 실패하면 지운다
```

**폴더 권한**(결정 164). 캐시 뿌리와 `clients/` 는 `os.path.realpath` 로 푼 실제 경로에서 검사한다. 이 사용자 소유가
아니거나 그룹이나 남이 쓸 수 있으면 따라가지 않는다. 남이 쓸 수 있는 폴더의 코드는 그 사람의 코드다. M5o 결정
122 가 들인 프리셋 파일에 건 규칙과 같다. 새로 만들 때는 0700 이다.

**받기.** `GET <server><client_wheel.path>` 를 받는다. `.tmp-<무작위>/` 안의 파일로 흘려 받으면서 sha256 을 계산하고,
`client_wheel.bytes` 를 넘으면 끊는다. 해시가 health 의 `client_wheel.sha256` 과 다르면 임시 폴더를 통째로
지운다(결정 157). 다른 실행이 그 바이트를 쓰지 않도록 캐시에 아무것도 남기지 않는다. 받는 중에 Ctrl-C 가 오면 종료
130 이고 임시 폴더는 `finally` 에서 지운다.

**풀기**(결정 165). pip 를 쓰지 않는다. 런타임 의존성이 0 이라 순수 파이썬 wheel 은 풀기만 하면 import 할 수 있다.
`zipfile` 로 풀되 다음을 지킨다.

- 이름은 `remote_ci_monitor/` 와 `remote_ci_monitor-<버전>.dist-info/` 아래만 받는다. 절대 경로, `..` 조각,
  역슬래시가 있으면 거부한다.
- 심링크 항목(`external_attr` 의 상위 비트가 링크)은 거부한다.
- 푼 크기 합이 `MAX_UNPACKED_BYTES`(256 MB)를 넘으면 거부한다.
- 푼 뒤 `remote_ci_monitor/__init__.py` 의 `__version__` 이 목표 버전과 같아야 한다. 정규식 하나로 읽는다.
  다르면 거부한다. 다시 실행한 자식이 「아직 버전이 다르다」고 보고 또 따라가는 고리를 여기서 끊는다(결정 161).
- `dist-info/METADATA` 의 `Requires-Python` 을 본다(결정 159). `>=X.Y` 나 `>=X.Y.Z` 한 꼴만 해석한다. 줄이 없으면
  통과다. 다른 꼴이거나 지금 인터프리터가 미달이면 거부한다.
- 표지 `.rcm-client.json` 을 마지막에 쓴다.

**완성은 rename 하나로**. `.tmp-<무작위>/tree` 를 `clients/<버전>-<sha8>` 로 `os.rename` 한다. 같은 버전을 두
프로세스가 동시에 받으면 둘 다 자기 임시 폴더에 풀고 늦은 쪽의 rename 은 대상이 있어 실패한다. 늦은 쪽은 자기
임시 폴더를 지우고 이미 있는 폴더의 표지를 확인한 뒤 그것을 쓴다. 표지가 없는 폴더는 완성본이 아니다. 디스크가
가득 차 풀다가 실패하면 임시 폴더만 지워지고 대상 폴더는 생기지 않는다.

**재실행**(결정 151). `PYTHONPATH` 를 쓰지 않는다. 그 변수는 rcm 이 띄우는 모든 자식 프로세스(git 등)로 새고,
`python -m` 은 현재 폴더를 `sys.path` 맨 앞에 둔다. 대신 같은 인터프리터를 `-P -c` 로 다시 실행한다. `-P` 는
Python 3.11 부터 있고 현재 폴더를 경로에 넣지 않는다. 부모는 `exec` 바로 전에 표지의 mtime 을 지금으로 바꾼다
(`os.utime`). 청소(§2.8)가 막 쓰려는 폴더를 「오래 안 쓴 것」으로 보지 않게 한다.

```python
os.execve(
    sys.executable,
    [sys.executable, "-P", "-c", BOOT, *argv],  # argv = 사용자가 친 인자 그대로
    {
        **os.environ,
        "RCM_FOLLOWED": "0.8.0",
        "RCM_FOLLOWED_PATH": "/Users/me/.cache/rcm/clients/0.8.0-a82e75a4",
        "RCM_FOLLOWED_FROM": "0.7.0",
    },
)
```

`BOOT` 는 설치본이 쓰는 짧은 코드다. 목표 버전의 코드가 아니라서 M5q 이전 버전으로 내려갈 때도 똑같이 돈다.
세 가지 일을 나눠서 한다. 경로 넣기는 표지가 있을 때만 하고 잠금은 실패해도 넘어가고 import 가 실패하면 설치본으로
돌아간다.

```python
import os, sys

info = {
    k: os.environ.pop(k, None)  # 자식 프로세스(git, 중첩 rcm)로 새지 않게 꺼낸다
    for k in ("RCM_FOLLOWED", "RCM_FOLLOWED_PATH", "RCM_FOLLOWED_FROM")
}
p = info["RCM_FOLLOWED_PATH"] or ""
usable = bool(p) and os.path.isfile(os.path.join(p, ".rcm-client.json"))
if usable:
    sys.path.insert(0, p)
    try:  # 잠금은 청소를 막는 장치일 뿐 — 실패해도 돈다
        import fcntl

        _lock = open(os.path.join(p, ".lock"), "a")
        fcntl.flock(_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        pass
loaded = False
try:
    import remote_ci_monitor
    from remote_ci_monitor.cli import main

    loaded = usable and os.path.realpath(remote_ci_monitor.__file__).startswith(
        os.path.realpath(p) + os.sep
    )
except Exception:
    pass
if not loaded:  # 캐시가 망가졌다 — 설치본으로
    if usable and p in sys.path:
        sys.path.remove(p)
    for m in [m for m in sys.modules if m.split(".")[0] == "remote_ci_monitor"]:
        del sys.modules[m]
    sys.stderr.write("rcm: follow: the cached client did not load — running installed\n")
    import remote_ci_monitor
    from remote_ci_monitor.cli import main
remote_ci_monitor._follow_boot = {**info, "loaded": loaded}  # 이 프로세스는 다시 따라가지 않는다
sys.exit(main())
```

- 고리 막기는 환경 변수가 아니라 `remote_ci_monitor._follow_boot` 로 한다. BOOT 를 거친 프로세스는 이 속성이 있고,
  따라가기 코드는 이 속성이 있으면 아무것도 하지 않는다. 설치본으로 돌아간 경우에도 같다. 환경 변수는 BOOT 가
  꺼내므로 그 프로세스가 띄우는 `rcm` 은 처음부터 자기 판단을 한다.
- `exec` 는 같은 PID 를 쓴다. 종료 코드, 신호, 열린 stdin·stdout·stderr 가 그대로 이어진다. `rcm wait` 의 0/1/2/3 도
  그대로다. 부모는 `exec` 전에 stdout 과 stderr 를 비운다. 부모는 stdin 을 읽지 않는다.
- `sys.argv[0]` 은 자식에서 `-c` 가 된다. 파서는 `prog="rcm"` 이라 도움말이 바뀌지 않는다. `_this_rcm()` 은 `argv[0]`
  이 `rcm` 일 때만 그것을 쓰므로 다음 후보(`sys.executable` 옆의 `rcm`)로 넘어간다.
- 서버를 mDNS 로 찾았으면 찾은 주소를 자식에게 `RCM_SERVER` 로 넘긴다. 자식이 1.5초 발견을 다시 하지 않는다.
- 프리셋 잡의 환경은 `runner.build_env` 가 `env_passthrough` 에 적힌 이름만 넘긴다. 따라가기 변수는 어차피 잡으로
  새지 않는다.

**처음 받을 때만** stderr 에 한 줄을 쓴다. 캐시에서 바로 실행할 때는 아무것도 쓰지 않는다.

```
rcm: following server v0.8.0 (installed v0.7.0) — fetched 707 KB
```

### 2.6 실패는 설치본으로 계속 + 경고 한 줄 (결정 157)

fail-open 금지 규칙과 부딪치지 않는다. 설치본으로 도는 것은 오늘의 동작이고 틀린 값을 자신 있게 보여 주는 것이
아니다. 그리고 실패를 숨기지 않고 한 줄로 말한다. JSON 을 내는 명령(`run` `wait`)의 stdout 은 그대로 깨끗하다.

```
rcm: not following server v0.8.0: <이유> — running installed v0.7.0
```

| 코드 | `<이유>` | 언제 |
|---|---|---|
| `unreachable` | `cannot reach the server` | health 실패나 3xx, 기록도 캐시도 없음 |
| `bad_health` | `the server's health has a malformed <key>` | §2.4 의 형식 검사에 어긋남 |
| `no_wheel` | `the server has no client wheel (<client_wheel_error>)` | `client_wheel: null` |
| `old_server` | `the server is too old to hand out its client` | health 에 `client_wheel` 키 자체가 없다(0.2.6 이전) |
| `auth` | `the server needs a token to hand out its client` | `/client/…` 가 401 이다(`read_auth = "basic"`, 토큰 없음) |
| `sha_mismatch` | `sha256 mismatch` | 받은 바이트의 해시가 health 와 다르다 |
| `unpack` | `cannot unpack: <strerror>` | 디스크 가득, 권한, 깨진 zip, 거부한 항목 |
| `version_mismatch` | `the wheel says v<X>` | 푼 `__init__.py` 의 버전이 목표와 다르다 |
| `python` | `needs Python <spec>, this is <py>` | `Requires-Python` 미달, 또는 `>=X.Y` 꼴이 아니다 |
| `cache_unsafe` | `<path> is writable by others` | §2.5 폴더 권한 |
| `pinned_unavailable` | `v<X> is not installed or cached and the server runs v<Y>` | §2.7 |
| `platform` | `not supported on this platform` | `os.name != "posix"` 이거나 `sys.executable` 이 비었다 |

경고는 한 명령에 한 번만 쓴다. 같은 이유가 10분 기록 안에서 되풀이돼도 명령마다 한 번이다.

### 2.7 목표 버전 정하기 (결정 150·158)

`follow` 의 목표는 서버 버전이다. 버전은 크기를 비교하지 않고 문자열이 같은지만 본다. 서버가 0.6.1 로 내려가면 클라이언트도
0.6.1 로 내려간다(결정 150). 서버가 정본이고 `min_client_version` 같은 와이어 계약은 바뀌지 않는다. 서버의 sha256 이 캐시와
다르면(같은 버전 번호로 다시 빌드한 개발 서버) 새로 받는다. 서버가 버전은 말하지만 wheel 이 없으면(`client_wheel: null`) 캐시에
같은 버전이 있을 때 가장 최근에 쓴 것으로 돈다. 없으면 `no_wheel` 이다.

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

### 2.8 캐시 청소 (결정 163)

새 버전을 받아 rename 에 성공한 직후에만 청소한다. 명령마다 하지 않는다.

- 표지의 마지막 사용 시각(`.rcm-client.json` 의 mtime, `exec` 직전마다 `os.utime`) 순으로 최근 3개(`KEEP_VERSIONS`)를 남긴다.
- 지금 목표 폴더는 남긴다.
- `.lock` 에 `LOCK_EX | LOCK_NB` 를 걸어 보고 실패하면 누가 쓰는 중이라 남긴다. 오래 도는 `rcm wait` · `rcm top --watch` 가
  그렇다. BOOT 의 잠금은 best-effort 이므로 이 검사도 「잠긴 것은 확실히 남긴다」까지만 약속한다.
- 지울 폴더는 먼저 `.trash-<무작위>` 로 rename 하고 그다음 지운다. 반쯤 지운 폴더가 완성본처럼 보이지 않게 한다.
- 한 시간보다 오래된 `.tmp-*` · `.trash-*` 도 지운다(죽은 프로세스의 찌꺼기).

### 2.9 보이는 것 (결정 166)

**`rcm version`** 은 서버에 묻지 않는다는 규칙을 지킨다. 첫 줄은 오늘과 똑같다. 따라가기 정보가 있을 때만(설정이 기본이
아니거나 기록 파일에 기본 서버의 항목이 있을 때) 둘째 줄을 쓴다. `rcm --version` 은 언제나 한 줄이다. 셸 스크립트가
`rcm --version | awk '{print $2}'` 로 읽는다(dolomood).

```
$ rcm version
rcm 0.8.0 (Python 3.12.4, darwin arm64)
follow: on (default) · server http://macmini.local:8787 v0.8.1 checked 3m ago · cached 0.8.1, 0.8.0

follow: off (editable install)
follow: pinned 0.8.0 (~/.config/rcm/client-version) · cached 0.8.0
```

`rcm version --json` 은 기존 키를 그대로 두고 `follow` 객체를 더한다. 서버에 묻지 않고 기록 파일만 읽는다.

```json
"follow": {"mode": "follow", "pin": null, "source": "default", "install": "wheel",
           "server": "http://macmini.local:8787", "server_version": "0.8.1",
           "checked_at": "2026-11-19T06:20:00Z", "cached": ["0.8.1", "0.8.0"]}
```

**`rcm check` 의 `client` 행**은 지금 판정(FAIL 은 `min_client_version` 아래일 때뿐)을 그대로 두고 꼬리에 따라가기 상태를 붙인다.

```
ok    client        v0.8.1 · same as server · following (installed v0.8.0)
warn  client        v0.8.0 · server v0.8.1 · older · not following: sha256 mismatch — pip install http://…/client/…whl
warn  client        v0.8.0 · server v0.8.1 · older · pinned by RCM_CLIENT_VERSION
ok    client        v0.8.1 · same as server · not following (editable install)
```

따라간 자식이 M5q 이전 버전이면(내려갔을 때) 그 버전의 옛 `client` 행이 그대로 나온다. 고칠 수 없고 고칠 필요도 없다.

### 2.10 서버 쪽 작은 변경 (결정 167·168)

- **wheel 에 skills 를 싣는다**(PR A0). `clientwheel.ASSET_DIRS = ("web", "templates", "skills")`. 시험은 wheel 의 파일 목록이
  패키지 폴더의 파일 목록(`__pycache__` · `.pyc` · 점 파일 제외)과 같은지 본다. 앞으로 새 자산 폴더가 생겨도 이 시험이 빨개진다.
  따라가기와 상관없이 지금 있는 결함이라 따로 먼저 낸다.
- **클라이언트 버전을 서버 로그에 한 줄** 남긴다(PR A). 자리는 이미 토큰을 검사하는 제출 경로(`POST /jobs`)뿐이다.
  `User-Agent: rcm/<X>` 를 보고 `(토큰 이름, X)` 쌍을 프로세스가 처음 볼 때 `client macbook runs v0.8.1` 을 쓴다. 인증하지 않는
  경로의 헤더로 로그를 채울 수 없게 한다. DB 는 바꾸지 않는다. §1.1 처럼 「기록 없음」으로 끝나지 않고 실기에서 따라가기가
  실제로 퍼졌는지 로그로 볼 수 있다.

## 3. 원격 워커 (PR B1 · B2, 결정 171)

### 3.1 지금 무슨 일이 일어나나

워커 등록은 버전이 정확히 같아야 한다. 그런데 등록은 DB 에 남으므로 서버를 올리고 재시작해도 이미 등록된 워커는 옛 버전으로
heartbeat 와 claim 을 계속한다. 문제는 그 워커가 재시작할 때다. 등록이 409 `worker version 0.6.1, server 0.7.0 — install the
same release` 로 막히고 `rcm worker` 는 종료 2 로 끝난다. launchd 의 `KeepAlive` 나 systemd 의 `Restart=` 가 다시 띄우면 같은
409 를 되풀이한다. 지금 운영의 워커(`mac2-worker`)는 서버와 같은 머신의 같은 editable 설치라 이 일을 겪지 않았다.

### 3.2 무엇을 바꾸나

워커는 같은 설정(§2.2)을 따른다. 고정은 `RCM_CLIENT_VERSION`, 한 줄 파일, `rcm worker --no-follow` 셋이다. launchd plist 와
systemd unit 은 이미 환경 변수를 싣는 자리가 있다. 계기는 넷이고 두 PR 로 나눈다.

| 계기 | 그때 도는 잡 | 하는 일 | PR |
|---|---|---|---|
| ① 기동할 때 | 없다 | 등록 전에 health 를 **새로**(10분 기록 없이) 읽고 CLI 와 같은 흐름(§2.1 의 5~7)으로 따라간다 | B1 |
| ② 기동 등록이 409 이고 본문의 `server_version` 이 자기와 다르다 | 없다 | 그 버전으로 따라간다. 실패하면 오늘처럼 종료 2 이고 문장에 이유가 붙는다 | B1 |
| ③ heartbeat 응답의 `server_version` 이 자기와 다르다 | 있을 수 있다 | 드레인(§3.3) | B2 |
| ④ 레인의 claim 이 409 `not registered` 를 받아 재등록했는데 그 등록이 버전 불일치 409 다 | 있을 수 있다 | 드레인(§3.3). 오늘은 `WorkerExit` 로 `stopping` 을 켜서 다른 레인의 잡까지 잃는다 | B2 |

- 서버는 버전 불일치 409 본문에 `server_version` 을 싣고(B1), heartbeat 응답에도 `server_version` 을 더한다(B2). 키 추가뿐이라 옛
  워커는 무시한다.
- 내려간 워커가 M5q 이전 버전이면 ③ 과 ④ 를 모른다. 그 워커는 다음 재시작까지 옛 버전으로 돈다. 등록이 살아 있는 동안은 오늘과 같다.
- **고정한 워커**: `RCM_CLIENT_VERSION=0.8.0` 인 워커는 ③ 을 무시한다. ② 와 ④ 에서는 따라가지 않고 오늘처럼 끝나며 문장이
  「pinned」라고 말한다.
- **editable 워커**는 기본 `installed` 다. ③ 에서 로그 한 줄(`server v0.8.1 — not following (editable install)`)만 프로세스당 한 번 쓴다.

### 3.3 드레인 (PR B2)

드레인은 `stopping` 과 다른 상태다. `stopping` 은 SIGTERM 의 경로이고 도는 잡을 lost 로 보고한다. 드레인은 잡을 하나도
잃지 않는다.

1. ③ 이나 ④ 가 오면 `RemoteWorker.draining` 을 켜고 목표 버전을 `follow_to` 에 둔다.
2. 각 레인은 루프 맨 위에서 `draining` 을 보면 claim 하지 않고 빠져나간다. 이미 받은 claim 의 잡(드레인 직전 long-poll 이 돌려준 잡
   포함)은 끝까지 돌리고 `finish` 보고를 마친 뒤에 나간다.
3. heartbeat 는 드레인 동안 계속 돈다. 서버는 이 워커를 살아 있는 것으로 본다.
4. `run()` 은 모든 레인 스레드가 끝난 뒤에 `stopping` 을 켜서 heartbeat 와 샘플러를 멈추고 `restart_for = follow_to` 를 남기고
   돌아온다.
5. `cmd_worker` 가 `restart_for` 를 보고 **설치본 부트로더로** 자기를 다시 실행한다. 그 실행이 ① 을 다시 한다.

- 설치본으로 돌아가 다시 시작하는 이유: 지금 프로세스가 내려간 옛 버전일 수도 있다. 다음 판정은 언제나 설치본 부트로더가 한다.
  설치본으로 돌아가는 `exec` 는 따라가기 변수를 지운 환경으로 `-P -c "from remote_ci_monitor.cli import main…"` 을 실행한다.
  경로를 앞에 넣지 않으니 site-packages 의 설치본이 import 된다.
- **고리 막기**: 드레인 재실행은 `RCM_WORKER_RESTARTED_AT`(epoch 초)을 남긴다. 300초 안에 다시 ③ 이 오면 드레인하지 않고 로그를
  한 번 쓴다. ④ 는 상한과 상관없이 드레인한다. 등록이 막힌 레인은 어차피 일을 못 한다.
- 드레인 중에 SIGTERM 이 오면 `stopping` 의 정지 경로를 그대로 탄다. 재실행은 취소된다.

### 3.4 launchd · systemd 아래에서 안전한가

- `exec` 는 PID 를 바꾸지 않는다. 감독자는 같은 프로세스가 계속 도는 것으로 본다. 재시작 횟수 제한에 걸리지 않는다.
- stdout · stderr 는 감독자가 연 로그 파일 fd 1·2 이고 상속된다. 로그가 같은 파일로 이어진다.
- 신호 처리기는 `exec` 로 초기화되고 새 코드가 `cmd_worker` 에서 다시 단다.
- `exec` 직전에는 레인 스레드가 끝났고(join), heartbeat 와 샘플러는 `stopping` 으로 멈췄고 도는 잡이 없다. 파이썬이 연 소켓은
  상속되지 않는다(PEP 446).
- `exec` 자체가 `OSError` 로 실패하면 로그를 쓰고 종료 1 로 끝난다. 감독자가 다시 띄우면 설치본이 ① 부터 다시 한다.

## 4. 보안 (결정 169·170)

따라가기는 서버가 준 코드를 세션 머신에서, 세션 사용자의 권한으로 실행한다. 이것은 새로 생기는 신뢰다. 오늘 서버가 세션
머신에 하는 일은 결과와 산출물을 돌려주는 것까지다.

### 4.1 무엇이 위험한가

- 그 코드는 `rcm` 을 부른 사용자가 읽을 수 있는 모든 것에 닿는다. `gh` 의 GitHub 토큰, git 자격 헬퍼, `~/.ssh` 의 키,
  클라우드 자격 파일, 열려 있는 다른 레포가 그렇다. 빌드 머신 한 대를 넘어 세션 머신과 그 사람의 계정으로 번진다.
- LAN 의 평문 HTTP 에서는 중간자가 wheel 을 바꿔칠 수 있다. sha256 도 같은 통로(`/api/health`)로 오므로 이것을 막지 못한다.
  sha256 은 전송 중 깨짐을 잡는 장치이지 위조를 막는 장치가 아니다.
- `.local` 이름도 mDNS 로 푼다. `server = "auto"` 가 아니라 `http://macmini.local:8787` 처럼 이름을 적어도, 같은 망의 누군가가
  그 이름에 먼저 답하면 가짜 서버로 간다. 「발견을 끄면 안전하다」는 말은 `.local` 주소에는 맞지 않는다.
- 오늘의 관행도 이미 서버를 꽤 믿는다. 클라이언트는 토큰을 평문으로 보내고 작업 트리를 올리며 `rcm artifacts --fetch` 는 서버가 준
  파일을 작업 트리에 쓴다. dolomood 의 `remote_ci.sh` 는 서버 wheel 을 해시만 보고 `pip install` 한다. 그래도 「다음 `rcm` 호출
  때 그 코드가 사람 손 없이 바로 돈다」는 점은 다르다. 문서는 이 차이를 숨기지 않는다.

### 4.2 선택지와 오너 선택

| 선택지 | 내용 | 막는 것 | 잃는 것 |
|---|---|---|---|
| (a) 어디서나 켬 | 기본 `follow`. 적은 주소든 `.local` 이든 mDNS 발견이든 따라간다 | 위 위험을 막지 않는다. 끄는 법 셋으로 사람이 고른다 | 없다 |
| (b) 적은 주소에서만 켬 | `server = "auto"` 로 찾은 서버는 따라가지 않는다 | mDNS 발견 위조만 막는다. `.local` 이름 위조는 못 막는다 | 발견으로 붙는 세션은 손으로 올려야 한다 |
| (c) 기본 끔 | 사람이 `RCM_CLIENT_VERSION=follow` 로 켠다 | 켜지 않은 머신은 오늘과 같다 | 오너 요구 자체(「올리지 않아도 되게」)가 기본으로는 안 된다 |

**오너 선택은 (a) 다.** 근거는 둘이다. 오너 요구가 「프로덕트 버전을 그대로 쓰도록」이고 LAN 의 세션들이 `.local` 로 붙으므로
(b) 가 실제로 막는 것이 거의 없다.

### 4.3 줄이는 법과 끄는 법

- 신뢰할 수 없는 Wi-Fi 에서는 Tailscale 주소로 붙는다. WireGuard 가 통로를 암호화하고 노드를 인증하므로 중간자와 이름 위조가
  어렵다. 문서가 이것을 첫 권고로 둔다.
- 끄는 법은 셋이다. 셸 프로필의 `export RCM_CLIENT_VERSION=installed`, 한 줄 파일 `~/.config/rcm/client-version` 에 `installed`,
  한 번만이면 `--no-follow`.
- 리다이렉트를 따르지 않고(§2.4) health 값 형식을 검사하고(§2.4) 캐시 폴더 권한을 본다(§2.5). 이것들은 위조를 막지 못하지만
  토큰이 다른 호스트로 새거나 남이 캐시를 바꿔치는 길은 닫는다.
- 대안(서명 · https · sha 고정)의 평가는 §8 에 있다.

## 5. 인터페이스 계약

구현 에이전트가 둘 이상 나란히 짤 수 있게 이름과 모양을 여기서 고정한다. 바꿔야 하면 §12 에 남긴다.

### 5.1 새 모듈 `src/remote_ci_monitor/follow.py` (표준 라이브러리만)

`cli.py` 와 `server.py` 를 import 하지 않는다. 콘솔 스크립트의 앞단에서 가볍게 돌아야 한다. 가져오는 것은
`remote_ci_monitor.__version__`, `clientwheel.wheel_filename`, `config.ConfigError` 뿐이다. 서버 주소 해석(mDNS 발견 포함)은
`cli.py` 가 이미 갖고 있으므로 `cli.py` 가 해석한 값을 넘긴다.

```python
ENV_MODE = "RCM_CLIENT_VERSION"
ENV_FOLLOWED = "RCM_FOLLOWED"            # BOOT 에 건네는 값. BOOT 가 꺼낸다
ENV_FOLLOWED_PATH = "RCM_FOLLOWED_PATH"
ENV_FOLLOWED_FROM = "RCM_FOLLOWED_FROM"
ENV_WORKER_RESTARTED_AT = "RCM_WORKER_RESTARTED_AT"   # PR B2
PIN_FILE = "client-version"              # $XDG_CONFIG_HOME/rcm/ 또는 ~/.config/rcm/
MODE_FOLLOW, MODE_INSTALLED, MODE_PIN = "follow", "installed", "pin"
VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*([.+-][0-9A-Za-z]+)*$")   # 64자 이하와 함께
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHECK_TTL_SECONDS = 600
NEG_TTL_SECONDS = 60
HEALTH_TIMEOUT_SECONDS = 3.0
KEEP_VERSIONS = 3
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
NO_FOLLOW_COMMANDS = frozenset({"serve", "worker", "token", "init", "discover", "version"})
MARKER, LOCK = ".rcm-client.json", ".lock"

class FollowError(Exception):            # .code 는 §2.6 표의 코드
    def __init__(self, code: str, detail: str = ""): ...

@dataclass(frozen=True)
class Prescan:
    command: str | None                  # 첫 위치 인자
    no_follow: bool
    server: str | None
    token: str | None
    client_config: str | None
    wants_help: bool                     # -h · --help · --version
    has_gc_config: bool                  # gc --config

@dataclass(frozen=True)
class Setting:
    mode: str                            # follow | installed | pin
    pin: str | None
    source: str                          # flag | env | file | default

@dataclass(frozen=True)
class ServerInfo:
    server: str                          # 정규화한 주소
    version: str
    wheel_path: str | None
    sha256: str | None
    bytes: int | None
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
    installed: str                       # 설치본 버전(따라간 자식이면 BOOT 의 RCM_FOLLOWED_FROM)
    running: str                         # 지금 도는 __version__
    followed: bool                       # 지금 프로세스가 캐시 코드인가
    server: str | None
    server_version: str | None
    warn: str | None
    warn_text: str | None

def prescan(argv: Sequence[str]) -> Prescan                                 # 해석하지 않고 훑기만
def follows(p: Prescan, *, server_configured: bool) -> bool                 # §2.3 표
def install_kind() -> str
def read_pin_file(environ: Mapping[str, str]) -> tuple[str | None, Path | None]
def resolve_setting(*, no_follow: bool, environ: Mapping[str, str],
                    file_value: str | None, file_path: Path | None, kind: str) -> Setting   # 순수. 틀린 값은 ConfigError
def parse_health(server: str, body: Mapping[str, Any], *, now: float) -> ServerInfo           # 형식 검사. 어긋나면 FollowError("bad_health")
def plan(setting: Setting, *, installed: str, info: ServerInfo | None,
         cached: Sequence[CacheEntry], os_name: str) -> Plan                # 순수. 시계·파일·네트워크를 안 본다
def python_ok(requires: str | None, python: tuple[int, int, int]) -> bool  # None 이면 True, ">=X.Y[.Z]" 만 해석, 나머지 False
def no_redirect_opener() -> urllib.request.OpenerDirector
def cache_root(environ: Mapping[str, str] | None = None) -> Path            # 상대 XDG 무시, realpath
def scan_cache(root: Path) -> list[CacheEntry]
def load_record(root: Path, server: str) -> tuple[ServerInfo | None, float | None]   # (기록, failed_at)
def save_record(root: Path, info: ServerInfo | None, *, server: str, failed_at: float | None) -> None
def server_info(server: str, token: str | None, *, root: Path, now: float, ttl: float,
                opener=None) -> ServerInfo | None                           # 기록 · 음성 기록 · health
def fetch(server: str, token: str | None, info: ServerInfo, *, root: Path,
          opener=None) -> CacheEntry                                        # 실패는 FollowError. finally 로 .tmp 정리
def prune(root: Path, *, keep: int, protect: Path) -> list[Path]
def exec_into(entry: CacheEntry, argv: Sequence[str], *, environ: Mapping[str, str],
              installed: str, execve=os.execve) -> NoReturn                 # 표지 utime 뒤 exec
def restart_via_installed(argv: Sequence[str], *, environ: Mapping[str, str],
                          execve=os.execve) -> NoReturn                     # PR B2
def booted() -> dict[str, Any] | None                                       # remote_ci_monitor._follow_boot
def maybe_follow(argv: Sequence[str], *, setting: Setting, server: str, token: str | None,
                 discovered: bool, fresh: bool,                             # check 는 fresh=True
                 environ=os.environ, execve=os.execve, now=time.time,
                 opener=None, err=sys.stderr) -> FollowState                # exec 하면 안 돌아온다
def current_state() -> FollowState | None                                   # 이 프로세스의 결과
```

### 5.2 `cli.py`

| 바꾸는 것 | 모양 |
|---|---|
| 진입 | `main(argv=None)` 은 `_main(argv, follow_enabled=argv is None, execve=os.execve)` 를 부른다. 시험은 `_main` 에 가짜 `execve` 를 넣는다 |
| 위치 | `_main` 맨 앞, **`build_parser().parse_args` 보다 먼저**다. `follow.booted()` 가 있으면 건너뛴다. `follow.prescan(sys.argv[1:])` 로 훑고 따라가는 명령이면 `load_client_config(p.client_config, server=p.server, token=p.token)` 와 `_resolve_server` 로 서버 · 토큰을 구해 `follow.maybe_follow(...)` 에 넘긴다(`skills` 는 발견하지 않는다). 설정 파일이 깨졌거나 서버가 없으면 건너뛴다. `RCM_CLIENT_VERSION` 이나 한 줄 파일의 값이 틀린 `ConfigError` 만 종료 2 다 |
| 플래그 | 최상위 `--no-follow`(`dest="no_follow"`, `default=False`). `client_opts` 의 `--no-follow` 는 `default=argparse.SUPPRESS`. `worker` 에도 둔다(PR B1) |
| `_client_row(client, h, state=None)` | §2.9 의 꼬리. `state` 가 None 이면 오늘과 같은 글자 |
| `cmd_version` | §2.9 의 둘째 줄과 `--json` 의 `follow` |
| `cmd_worker` | B1: 기동 따라가기(fresh), `WorkerFollow` 처리. B2: `worker.restart_for` 면 `follow.restart_via_installed` |

`client.toml` 과 `config.py` 의 `_CLIENT_KEYS` 는 바꾸지 않는다.

### 5.3 서버 · 와이어

| 자리 | 추가 | PR |
|---|---|---|
| `clientwheel.ASSET_DIRS` | `"skills"` | A0 |
| 서버 로그(제출 경로 `POST /jobs` 에서만) | `client <token name> runs v<X>` (프로세스당 쌍마다 한 번) | A |
| `POST /worker/register` 409(버전) 본문 | `server_version: str` | B1 |
| `POST /worker/heartbeat` 응답 | `server_version: str` | B2 |
| `schema_version` · `/api/health` | 그대로. 키 추가도 없다 | |

### 5.4 `remote_worker.py`

| 이름 | 하는 일 | PR |
|---|---|---|
| `WorkerFollow(Exception)` | 기동 `register()` 가 버전 불일치 409 와 `server_version` 을 받으면 던진다. `.version` 을 든다 | B1 |
| `RemoteWorker.draining: threading.Event` | ③ · ④ 가 켠다. `stopping` 과 다르다 | B2 |
| `RemoteWorker.follow_to: str \| None` | 드레인의 목표 버전 | B2 |
| `_lane_loop` | 루프 맨 위에서 `draining` 이면 claim 하지 않고 빠진다. 받은 claim 의 잡은 `run_claimed` 가 끝까지 돌리고 `finish` 를 보낸 뒤에 루프로 돌아오므로 그다음 맨 위에서 빠진다. 재등록이 버전 불일치 409 면 `WorkerExit` 대신 드레인을 켠다 | B2 |
| `RemoteWorker.run()` | 모든 레인 스레드가 끝나면 `stopping` 을 켜 heartbeat 와 샘플러를 멈추고 드레인이었으면 `restart_for = follow_to` 를 남긴다 | B2 |
| `RemoteWorker.restart_for: str \| None` | `cmd_worker` 가 `run()` 뒤에 본다 | B2 |

워커의 health 와 `/client/` 요청은 워커 토큰을 Bearer 로 싣는다. 읽기 규칙(`_read_only_ok`)은 유효한 토큰이면 종류를 가리지 않는다.

## 6. 테스트

시험을 먼저 쓴다. 서버는 자기 버전의 wheel 만 주므로 「다른 버전의 서버」는 시험 도우미가 만든다. `tests/follow_helpers.py`
의 `fake_wheel(version, *, init_version=None, requires_python=">=3.11", extra=())` 는 설치본 파일로 wheel 을 조립하되
`__init__.py` 의 `__version__` 과 dist-info 를 그 버전으로 바꾼다. 작은 `ThreadingHTTPServer` 가 `/api/health` 와
`/client/<이름>` 을 흉내 낸다(`tests/test_docs_client_wheel.py` 에 같은 꼴이 이미 있다). `tests/conftest.py` 를 새로 두고
`XDG_CACHE_HOME` 과 `XDG_CONFIG_HOME` 을 `tmp_path` 아래로 돌려 시험이 사용자의 `~/.cache` 와 한 줄 파일을 건드리지 않게 한다.

### F: 따라가기 (PR A)

준비의 「설치본」은 `install_kind` 를 주입한 값이다. 「exec」 은 가짜 `execve` 가 받은 인자의 기록이다. 「실제 exec」 이라고
적힌 경우만 진짜 하위 프로세스로 돈다.

| # | 갈래 | 준비 | 기대 |
|---|---|---|---|
| F1 | 베스트 | 설치본 0.8.0 · 서버 0.8.0 | exec 없음, `/client/` 요청 0, health 1번. 10분 안 두 번째 실행은 health 0번 |
| F2 | 베스트 | 설치본 0.8.0 · 서버 0.8.1 · 캐시 빔 | `/client/` 1번, 폴더 `0.8.1-<sha8>` 생성, 표지 있음, exec 인자 `[py, -P, -c, BOOT, *argv]` 와 `RCM_FOLLOWED=0.8.1`. stderr 에 `following server v0.8.1` 한 줄 |
| F3 | 베스트 | F2 다음 실행 | `/client/` 0번, health 0번(10분 안), 같은 폴더로 exec, stderr 비어 있음 |
| F4 | 베스트 | 설치본 0.8.0 · 서버 0.6.1 | 0.6.1 로 내려가 exec |
| F5 | 베스트 | 실제 exec: 설치본 venv(비 editable) · 가짜 서버 0.8.1 · `rcm presets` | 자식이 0.8.1 코드로 돌고(가짜 wheel 의 `cli.py` 가 표식을 찍는다) 종료 코드가 자식의 것 |
| F6 | 고정 | `RCM_CLIENT_VERSION=0.6.1` 로 네 갈래: 설치본이 0.6.1 / 캐시에 0.6.1 / 서버가 0.6.1 / 셋 다 아님 | 설치본 / 캐시 exec 이고 health 0번 / 받아서 exec / 설치본 + `pinned_unavailable` |
| F7 | 고정 | `--no-follow` 를 최상위와 하위 명령 뒤 두 자리에 | 둘 다 health 0번, exec 없음, 설치본 파서도 두 자리를 받는다 |
| F8 | 고정 | `RCM_CLIENT_VERSION=installed` | health 0번 |
| F9 | 우선순위 | 한 줄 파일 `installed` + `RCM_CLIENT_VERSION=follow` | 따라간다(환경 변수가 이긴다). 반대로 두면 안 따라간다 |
| F10 | 우선순위 | editable 설치 · 설정 없음 | 안 따라간다, 경고 없음, `rcm version` 둘째 줄 `follow: off (editable install)` |
| F11 | 우선순위 | editable 설치 + `RCM_CLIENT_VERSION=follow` | 따라간다 |
| F12 | 우선순위 | 메타데이터 버전 ≠ `__version__` (pull 한 editable) | `install_kind() == "source"`, 안 따라간다 |
| F13 | 에러 | `RCM_CLIENT_VERSION=lates` · `RCM_CLIENT_VERSION=../x` · 65자 버전 · 한 줄 파일에 `lates` | 넷 다 종료 2, `ConfigError` 문장(파일이면 경로가 들어간다), 서버에 요청 0 |
| F14 | 에러 | 서버 꺼짐 · 기록 없음 | 설치본으로 계속, 경고 `unreachable` 한 줄, 명령 자신의 연결 실패 문장은 그대로 |
| F15 | 에러 | 서버 꺼짐 · 11분 된 기록 0.8.1(sha `aaaa…`) · 캐시에 `0.8.1-aaaa…` 와 `0.8.1-bbbb…` | `0.8.1-aaaa…` 로 exec, 경고 없음 |
| F16 | 에러 | health 503 · 본문에 `version` 과 `client_wheel` | 따라간다(update-client.sh 와 같은 판단) |
| F17 | 에러 | health 500 · 본문 없음 | `unreachable` |
| F18 | 에러 | `client_wheel: null` · `client_wheel_error: "metadata_missing"` | `no_wheel` 경고에 코드가 들어간다. 캐시에 같은 버전이 있으면 그것으로 exec |
| F19 | 에러 | health 에 `client_wheel` 키 없음(옛 서버) | `old_server` |
| F20 | 에러 | sha256 불일치 | 경고 `sha_mismatch`, `clients/` 아래에 대상 폴더도 `.tmp-*` 도 없다, exec 없음 |
| F21 | 에러 | `/client/` 가 `client_wheel.bytes` 보다 길게 보냄 | 끊고 `unpack` 또는 `sha_mismatch`, 대상 폴더 없음 |
| F22 | 에러 | 풀다가 `OSError(ENOSPC)`(파일 쓰기를 주입) | `unpack: No space left on device`, 대상 폴더 없음, 임시 폴더 지워짐 |
| F23 | 에러 | `read_auth = "basic"` 서버 · 토큰 있음 | 따라간다(Bearer 를 실었다) |
| F24 | 에러 | `read_auth = "basic"` 서버 · 토큰 없음 | `auth` |
| F25 | 에러 | 가짜 wheel 의 METADATA `Requires-Python: >=3.99` | `python`, 대상 폴더 없음 |
| F26 | 에러 | `Requires-Python: ~=3.11` · `>=3.11,<4` | 둘 다 `python`, 따라가지 않는다 |
| F27 | 에러 | `clients/` 가 0o777 · 남의 소유(소유자 검사는 `os.stat` 주입) | `cache_unsafe`, 받지 않는다 |
| F28 | 에러 | wheel 에 `../evil.py` · 절대 경로 · 심링크 항목 · `other_pkg/x.py` | 각각 `unpack`, 대상 폴더 없음, 캐시 밖에 파일이 안 생긴다 |
| F29 | 에러 | 가짜 wheel 의 `__init__.py` 가 0.8.0 인데 이름은 0.8.1 | `version_mismatch`, 대상 폴더 없음 |
| F30 | 엣지 | 두 프로세스가 빈 캐시에서 같은 0.8.1 을 동시에 받는다(실제 프로세스 둘, 시작 장벽으로 맞춘다) | 대상 폴더 하나, 표지 하나, 둘 다 같은 폴더로 exec, `.tmp-*` 없음 |
| F31 | 엣지 | `remote_ci_monitor._follow_boot` 가 있는 프로세스 · 서버 0.8.2 | health 0번, exec 없음(고리 방지) |
| F32 | 엣지 | 실제 exec: BOOT 가 가리키는 폴더를 실행 직전에 지운다 | 자식이 설치본을 import 하고 `the cached client did not load` 한 줄, 다시 따라가지 않는다 |
| F33 | 엣지 | 서버 주소를 바꿔 가며(8790 은 0.6.1, 8791 은 0.8.1) | 기록 둘, 캐시 폴더 둘, 각 서버에 맞는 버전으로 exec |
| F34 | 엣지 | 같은 버전 0.8.1, sha 가 다른 두 서버 | 폴더 둘(`0.8.1-aaaa…` · `0.8.1-bbbb…`) |
| F35 | 엣지 | 캐시에 다섯 버전, 하나는 다른 프로세스가 `.lock` 을 공유 잠금 중 · 새 버전을 받는다 | 최근 사용 3개 + 잠긴 것 + 새 것만 남는다. 지운 것은 `.trash-*` 를 거쳐 사라진다 |
| F36 | 엣지 | `init` `token` `discover` `version` `gc --config … --dry-run` 을 `RCM_CLIENT_VERSION=follow` 로 | 각각 health 요청 0 |
| F37 | 엣지 | `rcm serve` 를 `RCM_CLIENT_VERSION=follow` · 캐시에 다른 버전이 있는 채로 | 따라가기 코드를 부르지 않는다(`maybe_follow` 가 불리면 실패하는 가짜로 확인). 서버의 `/api/health.version` 은 설치본 |
| F38 | 엣지 | `os.name = "nt"` 를 주입 | `platform`, 설치본. pyproject classifiers 에 Windows 가 없음을 `test_packaging` 이 잠근다 |
| F39 | 엣지 | `main(["jobs"])` 처럼 인자 목록을 넘긴 호출 · 서버 0.8.1 | health 0번, exec 없음 |
| F40 | 엣지 | 한 줄 파일 `follow` · 서버 0.6.1(M5q 이전) | 내려간다. 0.6.1 자식이 `client.toml` 을 오늘처럼 읽는다(한 줄 파일은 열지 않는다) |
| F41 | 엣지 | `server = "auto"` · 발견된 서버 하나 · 버전 다름 | 발견은 한 번, 자식 환경에 `RCM_SERVER=<찾은 주소>` |
| F42 | 엣지 | `rcm run gate --no-wait` 가 따라가다 실패 | stdout 은 JSON 한 줄 그대로 파싱되고 경고는 stderr 에만 |
| F43 | 엣지 | 실제 exec: 자식 잡이 실패해 `rcm wait` 가 1 · Ctrl-C 로 130 | 부모가 본 종료 코드가 1 · 130, PID 가 같다 |
| F44 | 표시 | 기록 없음 · 설정 기본 | `rcm version` 한 줄, stderr 비어 있음(기존 잠금 그대로) |
| F45 | 표시 | 기록 있음 | 둘째 줄 `follow: on (default) · server … v0.8.1 checked …`, `--json` 의 `follow` 키 모양 |
| F46 | 표시 | `rcm check` 를 네 상태(따라간 자식 · 실패 · 고정 · editable)에서 | §2.9 의 네 줄. FAIL 은 `min_client_version` 아래일 때뿐 |
| F47 | 표시 | `rcm check` 는 10분 기록이 있어도 | health 를 새로 읽는다 |

### R: 설계 검토로 더한 것 (PR A, ①~⑧)

| # | 준비 | 기대 |
|---|---|---|
| ① | 설치본이 모르는 명령 `rcm newcmd --flag` · 서버 0.8.1 · 가짜 wheel 이 그 명령을 안다 | 설치본 argparse 가 돌기 전에 따라가 자식이 명령을 실행한다. 서버가 없으면 설치본이 `invalid choice` 로 종료 2 |
| ② | `rcm run --help` · `rcm -h` · `rcm --version` · 서버 0.8.1 | health 0번, 설치본의 도움말과 버전 |
| ③ | `prescan` 에 `--server=URL` · `--server URL` · `--token=T` · `--client-config P` · `--` 뒤의 `--no-follow` · `-f x=--server` | 앞의 넷은 값을 정확히 집고 `--` 뒤와 다른 옵션의 값은 플래그로 보지 않는다 |
| ④ | `rcm skills list` 를 서버 설정 있음 / 서버 `auto` / 서버 꺼짐에서 | 따라감 / 발견 없이 설치본 / 설치본(경고 한 줄) |
| ⑤ | health 의 `version` 이 `../../x` · 65자, `sha256` 이 63자 · 대문자, `path` 가 `/client/other.whl` · `http://evil/x.whl` | 전부 `bad_health`, `/client/` 요청 0, 기록에 남지 않는다 |
| ⑥ | health 가 302 로 다른 호스트를 가리킴 · `/client/` 가 301 | 둘 다 `unreachable`, 리다이렉트 대상에 요청 0(토큰이 새지 않는다) |
| ⑦ | 서버 꺼짐 · 기록 없음 · `rcm jobs` 를 60초 안에 세 번 | health 시도 1번, 경고는 매번 한 줄. 61초 뒤에는 다시 시도 |
| ⑧ | 실제 exec: 캐시 폴더의 `remote_ci_monitor/cli.py` 를 깨진 파이썬으로 바꿈 · 자식이 `git` 을 부르는 명령 | 자식이 설치본으로 돌아가 한 줄을 쓰고 명령이 끝난다. 자식이 띄운 `git` 의 환경에 `RCM_FOLLOWED*` 가 없다 |

그 밖에 검토가 짚은 작은 경우를 F 표에 붙인다.

| # | 준비 | 기대 |
|---|---|---|
| F48 | 받는 중 Ctrl-C(`KeyboardInterrupt` 주입) | 종료 130, `.tmp-*` 없음 |
| F49 | `exec` 직전 | 대상 폴더 표지의 mtime 이 지금으로 바뀌어 있다 |
| F50 | `XDG_CACHE_HOME=relative/dir` | 무시하고 `~/.cache/rcm` 을 쓴다 |
| F51 | `~/.cache/rcm` 이 남 소유 폴더로 가는 심링크 | realpath 로 검사해 `cache_unsafe` |
| F52 | `read_auth = "none"` 서버 · 토큰 있음 | health 와 `/client/` 요청에 Bearer 가 실린다 |

### S: 서버 쪽

| # | 준비 | 기대 | PR |
|---|---|---|---|
| S1 | `build_wheel` | 파일 목록 = 패키지 폴더 파일 목록(`__pycache__` · `.pyc` · 점 파일 제외). `skills/*/SKILL.md` 가 들어 있다 | A0 |
| S2 | 조립한 wheel 을 새 venv 에 `pip install` | `rcm skills list` 가 스킬 다섯을 설명과 함께 보이고 `install` 이 각 폴더에 `SKILL.md` 를 쓴다(pip 없으면 skip) | A0 |
| S3 | 클라이언트 토큰 `macbook` 으로 `User-Agent: rcm/0.8.1` 제출 셋, 이어서 `rcm/0.8.2` 하나 | 로그 `client macbook runs v0.8.1` 한 줄과 `… v0.8.2` 한 줄 | A |
| S4 | 같은 헤더로 `GET /api/status` · `/api/health` · 토큰 없는 제출(401) | 로그 없음 | A |

### W: 원격 워커

| # | 갈래 | 준비 | 기대 | PR |
|---|---|---|---|---|
| W1 | 베스트 | 비 editable 워커 0.8.0 · 서버 0.8.1 · 기동 · 10분 안 기록이 0.8.0 | 기록을 무시하고 health 를 새로 읽어 등록 전에 0.8.1 로 exec. 등록 요청의 `version` 이 0.8.1 | B1 |
| W2 | 베스트 | 서버가 워커 아래에서 올라갔고 워커가 재시작(등록 409, 본문 `server_version`) | 따라가 다시 등록, 종료 2 없음 | B1 |
| W3 | 에러 | ② 에서 sha 불일치 | 종료 2, 문장에 `sha256 mismatch` | B1 |
| W4 | 엣지 | `RCM_CLIENT_VERSION=0.8.0` 워커 · ② | 종료 2 이고 문장에 `pinned` | B1 |
| W5 | 베스트 | heartbeat `server_version` 이 다름 · 도는 잡 없음 | `draining` 이 켜지고 `stopping` 은 레인이 다 끝난 뒤에야 켜진다. `restart_via_installed` 가 불리고 그 환경에 따라가기 변수가 없다 | B2 |
| W6 | 베스트 | W5 인데 레인 둘 중 하나가 잡을 도는 중 | 새 claim 0, 잡이 끝나 `finish` 를 보낸 뒤에 재실행. 서버에서 그 잡은 lost 가 아니라 원래 결과 | B2 |
| W7 | 베스트 | 레인 claim 이 409 `not registered` · 재등록이 버전 불일치 409 · 다른 레인이 잡을 도는 중 | `WorkerExit` 없이 드레인. 도는 잡은 끝까지 가고 보고된다 | B2 |
| W8 | 에러 | ③ 이 300초 안에 다시 옴(`RCM_WORKER_RESTARTED_AT` 이 60초 전) | 드레인 없음, 로그 한 번 | B2 |
| W9 | 에러 | `exec` 가 `OSError` | 로그 한 줄, 종료 1 | B2 |
| W10 | 엣지 | 옛 서버(heartbeat 에 `server_version` 없음) | 아무 일 없음 | B2 |
| W11 | 엣지 | editable 워커 · ③ | 드레인 없음, 로그 한 줄(프로세스당 한 번) | B2 |
| W12 | 엣지 | 고정 워커 · ③ · ④ | ③ 무시, ④ 는 오늘처럼 끝나고 문장에 `pinned` | B2 |
| W13 | 엣지 | 드레인 중 SIGTERM | `stopping` 경로, 도는 잡은 lost `worker stopped`, 재실행 없음 | B2 |
| W14 | 엣지 | 드레인 시작 직후 long-poll claim 이 잡을 돌려줌 | 그 잡을 끝까지 돌리고 보고한 뒤 재실행 | B2 |
| W15 | 엣지 | 실제 exec(e2e): 두 프로세스 시험의 워커가 ③ 으로 재실행 | 재실행 전후 `os.getpid()` 가 같고 같은 stderr 파일에 로그가 이어진다 | B2 |
| W16 | 엣지 | 프리셋 잡의 환경 | 따라가기 변수와 `RCM_WORKER_RESTARTED_AT` 이 없다 | B2 |

### mutcheck 에 더할 변이

| 변이 | 잡는 시험 | PR |
|---|---|---|
| `ASSET_DIRS` 에서 `skills` 를 뺀다 | S1 | A0 |
| sha256 비교를 지운다 | F20 | A |
| 기본값에서 editable 판정을 뒤집는다 | F10 | A |
| `_follow_boot` 고리 방지를 지운다 | F31 | A |
| 임시 폴더 없이 대상 폴더에 바로 푼다 | F22 · F30 | A |
| `serve` 를 따라가는 명령에 넣는다 | F37 | A |
| health 의 `path` 검사를 지운다 | ⑤ | A |
| 드레인이 도는 잡을 기다리지 않는다 | W6 | B2 |

## 7. 실기 확인 절차

운영 설정(`~/.config/rcm/server.toml`), 운영 데이터(`~/.local/share/rcm`), 포트 8787 은 쓰지 않는다. 다른 세션이 8788 을 쓴다.
시험 서버의 `data_dir` 은 `/tmp` 아래에 두지 않는다(dolomood selftest 가 `/private/tmp` 를 거부한 적이 있다).

클라이언트 C 의 **모든 명령은 `HOME=~/.local/share/rcm-devtest-m5q/home` 에서** 친다. 시험용 `client.toml`, 토큰, 캐시
(`$HOME/.cache/rcm`), 한 줄 파일, state(`$HOME/.local/state/rcm`)가 전부 그 안에 생긴다. 사용자의 진짜 `~/.config/rcm` 과
`~/.cache` 는 건드리지 않는다. 아래에서 `C$` 는 `HOME=~/.local/share/rcm-devtest-m5q/home <C 의 venv>/bin/rcm` 이다.

| 자리 | 버전 | 포트 | data_dir | venv |
|---|---|---|---|---|
| 서버 A | 0.6.1 (릴리스 wheel, M5q 이전) | 8790 | `~/.local/share/rcm-devtest-m5q/a` | `~/.local/share/rcm-devtest-m5q/venv-a` |
| 빌드 dev0 | PR A(·B) 빌드, `__version__` 을 임시로 `0.7.0.dev0` 으로 바꾼 사본 | | | `~/.local/share/rcm-devtest-m5q/venv-dev0`, 비 editable |
| 빌드 dev1 | 같은 빌드, `__version__` 만 `0.7.0.dev1` | | | `~/.local/share/rcm-devtest-m5q/venv-dev1`, 비 editable |
| 서버 B | 빌드 dev0 | 8791 | `~/.local/share/rcm-devtest-m5q/b` | venv-dev0 |
| 클라이언트 C | 빌드 dev0 | 없음 | `HOME=~/.local/share/rcm-devtest-m5q/home` | `~/.local/share/rcm-devtest-m5q/venv-c`, 비 editable |

각 서버는 자기 `server.toml`(포트, `data_dir`, `advertise = false`, 알림 훅 없음, 데모 프리셋 하나)과 자기 토큰을 쓴다.

1. `C$ jobs --server http://127.0.0.1:8791` 을 친다. 같은 버전이라 아무 일이 없다. `C$ version` 둘째 줄을 본다.
2. `C$ check --server http://127.0.0.1:8790` 을 친다. stderr 에 `following server v0.6.1` 이 한 번 나오고 0.6.1 의 옛 `client`
   행이 `same as server` 를 말한다. `$HOME/.cache/rcm/clients/0.6.1-*` 가 생긴다.
3. 2 를 다시 친다. stderr 가 비어 있고 서버 A 에 `/client/` 요청이 없다(서버를 `debug` 로 띄워 접근 로그로 본다).
4. `C$ run demo --server http://127.0.0.1:8790` 을 친다. 0.6.1 코드가 작업 트리를 올리고 종료 0 이다.
5. 다시 서버 B 에 붙는다. 설치본으로 돈다.
6. `RCM_CLIENT_VERSION=0.6.1 C$ check --server http://127.0.0.1:8791` 은 캐시의 0.6.1 로 돌고 `--no-follow` 를 붙인 서버 A 호출은
   설치본으로 돈다. `$HOME/.config/rcm/client-version` 에 `installed` 를 쓰면 서버 A 에서도 설치본으로 돈다. 다 보면 지운다.
7. 캐시를 비우고 `C$ jobs --server http://127.0.0.1:8790` 을 열 개 동시에 띄운다. 폴더가 하나만 생기고 `.tmp-*` 가 남지 않는다.
8. 서버 A 를 끄고 4 의 잡에 `C$ wait --job <번호> --server http://127.0.0.1:8790` 을 친다. 마지막 기록의 0.6.1 로 돌면서 연결
   실패를 말한다. 60초 안에 한 번 더 치면 3초를 기다리지 않는다.
9. 서버 B 로그에 `client <토큰> runs v0.7.0.dev0` 이 있다.
10. (PR B1 · B2) 워커 시험은 M5q 이후 빌드 둘로 한다. 서버 0.6.1 은 heartbeat 에 `server_version` 을 싣지 않으므로 이 단계에 쓸 수 없다.
    1. 서버 B(dev0, 8791)에 워커 토큰을 만든다. 워커를 **dev1** venv 로 `C$ worker --server http://127.0.0.1:8791 --pool m5q` 처럼
       `HOME` 을 같게 해서 띄운다. 워커가 기동하면서 health 를 새로 읽고 dev0 으로 따라간다. `rcm machines` 의 워커 버전이 `0.7.0.dev0`
       이다(①).
    2. 데모 프리셋을 그 풀에 하나 넣어 도는 중에, 서버 B 를 멈추고 같은 `data_dir` 과 포트 8791 로 **dev1** venv 의 서버를 띄운다
       (서버 업그레이드 흉내, DB v23 그대로).
    3. 워커가 heartbeat 의 `server_version` 을 보고 드레인한다. 도는 데모 잡은 끝까지 가서 성공으로 보고되고 그 뒤에 워커가 설치본
       부트로더(dev1)로 재실행해 등록한다. `rcm machines` 의 워커 버전이 `0.7.0.dev1` 이 되고 워커 PID 가 같다(③).
    4. 서버를 다시 dev0 으로 되돌리고 워커를 재시작한다. 등록 409 를 받고 따라가 dev0 으로 등록한다(②).
11. 끝나면 서버들과 워커를 멈추고 `~/.local/share/rcm-devtest-m5q` 를 통째로 지운다.

## 8. 안 만드는 것

- **pip 로 설치본을 갈아끼우는 `rcm self-update`.** 결정 83 의 이유(설치 방식마다 다르고 도는 프로세스 자신을 바꾼다)가 그대로다.
  따라가기는 설치본을 건드리지 않는다.
- **`client.toml` 의 고정 키.** 옛 버전이 모르는 키에서 죽는다(§1.5). 한 줄 파일과 환경 변수로 충분하다.
- **서버가 여러 버전의 wheel 을 보관하고 주는 것.** 고정 버전을 서버에서 받으려면 서버가 옛 버전을 들고 있어야 한다. 운영 머신에
  새 저장 의무가 생기고 「서버가 도는 코드를 준다」(결정 81)는 단순함을 깬다. 캐시와 설치본으로 충분하다.
- **서명.** 릴리스 서명(Sigstore · GPG)은 의존성이나 키 관리를 들인다. 그리고 서버 wheel 은 서버가 조립해 릴리스 wheel 과 바이트가
  다르므로(결정 81) 릴리스 서명으로 검증할 수 없다. 파일별 해시를 GitHub 릴리스와 대조하는 길은 런타임 경로에 GitHub 를 다시
  넣는다(결정 30). 후속 후보로만 둔다.
- **https.** 서버에 TLS 를 넣는 일은 인증서 배포가 따라온다. Tailscale 주소가 같은 효과를 더 싸게 낸다. 문서로 권한다.
- **처음 본 sha 고정(trust on first use).** 같은 버전에 다른 sha 가 오면 거부하는 규칙이다. 같은 번호로 다시 빌드하는 개발 서버가
  정상적으로 걸린다. 오너가 원하면 후속으로 한 줄 파일에 `0.8.1@sha256:<hex>` 꼴을 검토한다.
- **`Requires-Python` 의 일반 해석기.** `packaging` 없이 지정자 문법 전체를 다시 짜지 않는다. `>=X.Y` 한 꼴만 읽는다.
- **health 의 `requires_python` 키.** 받기 전에 거르는 이득이 작다(wheel 700 KB). METADATA 로 충분하다.
- **응답마다 서버 버전 헤더를 싣고 명령 도중에 갈아타는 것.** 한 명령은 한 버전으로 끝난다. 10분 기록이면 충분하다.
- **DB 에 클라이언트 버전 기록.** 로그 한 줄(결정 168)로 시작한다. 화면에 보여야 하면 후속에서 컬럼을 검토한다.

## 9. PR 순서 · 완료 기준

| PR | 브랜치 | 만지는 곳 | 실리는 릴리스 |
|---|---|---|---|
| 0 | `docs/m5q-client-follows-server-workplan` | 이 문서 | |
| A0 | `fix/clientwheel-ships-skill-files` | `clientwheel.py`(`ASSET_DIRS`) · 시험 S1 · S2 · CHANGELOG [Unreleased] ### Fixed | M5p 와 함께 v0.7.0 에 실을 수 있다 |
| A | `feat/m5q-client-follows-server-version` | `follow.py`(새) · `cli.py`(진입 · 플래그 · version · check) · `server.py`(제출 경로의 로그 한 줄) · `tests/conftest.py`(새) · `tests/follow_helpers.py`(새) · 문서 | v0.8.0 |
| B1 | `feat/m5q-worker-follows-on-start` | `remote_worker.py`(`WorkerFollow`) · `remote_workers.py`(409 본문) · `cli.py`(`cmd_worker` 기동) · 문서 | v0.8.0 |
| B2 | `feat/m5q-worker-drains-on-server-version` | `remote_worker.py`(드레인) · `remote_workers.py`(heartbeat) · `cli.py`(`cmd_worker` 재실행) · `follow.py`(`restart_via_installed`) · `examples/launchd` · `examples/systemd` 주석 · 문서 | v0.8.0 |
| C | `docs/m5q-follow-plan-and-guides` | `PLAN.md` 결정 149~174 행 · 이 문서 §12 · README 의 한 줄 안내 | |

순서는 A0 가 먼저다. A0 는 따라가기와 상관없는 결함 수정이라 M5p 와 같은 v0.7.0 에 실을 수 있다. A 는 M5p 의 P3 가 dev 에 머지된
뒤 그 위로 rebase 한다(같은 `cli.py` · `server.py` 를 만진다). B1 은 A 의 `follow.py` 를, B2 는 B1 을 쓴다. A · B1 · B2 를 묶어
**v0.8.0** 으로 릴리스한다.

리드가 문서를 별도 PR 로 나누자고 했지만 집안 규칙(「사용자에게 보이는 변경은 같은 PR 에서 문서를 고친다」)과 문서 잠금 시험
때문에 각 기능 PR 이 자기 문서를 들고 간다.

- A: `docs/operating.md` 「Keeping clients on the server's version」 재작성. 따라가기가 첫 길이고 고정 · 끄기 · 보안(§4 의 위험과
  오너 선택) 문단, `--help` 는 설치본 도움말이라는 한 줄, 그리고 `update-client.sh` 는 「설치본(부트로더)을 올리는 길」로 남는다.
  잠금 시험(`test_docs_client_wheel.py`)이 요구하는 말(`client_wheel` · `min_client_version` · `/client/remote_ci_monitor-` ·
  `sha256` · `pip install` · `rcm check` · `examples/session/update-client.sh` · `read_auth`)은 그대로 남긴다.
  `docs/configuration.md` 의 환경 변수와 한 줄 파일, `docs/usage.md` · `docs/usage.ko.md` 한 문단, CHANGELOG [Unreleased].
- B1 · B2: `docs/operating.md` 의 워커 절에 계기와 드레인, 감독자 문단, CHANGELOG [Unreleased].
- C: 계획서와 이 문서의 마무리.

완료 기준은 PR 마다 다음과 같다.

1. 시험을 먼저 쓴다. `ruff check . && ruff format --check . && pytest` · `python scripts/mutcheck.py` · `scripts/smoke_install.sh` 가 통과한다.
2. mutcheck 에 §6 의 변이를 더한다.
3. **격리 에이전트 검증.** 구현하지 않은 새 에이전트가 이 문서와 diff 만 보고 §6 의 표와 §7 의 절차를 실제로 돌린다.
4. 운영 반영은 기존 절차다. 서버를 올려도 클라이언트가 바로 따라오지는 않는다. 각 세션 머신의 설치본이 한 번은 0.8.0 이상이어야
   한다(부트스트랩). 그 뒤로는 서버를 올리기만 하면 된다.

## 10. 이 마일스톤 밖: dolomood-renew 쪽 (그쪽 세션의 일)

dolomood `scripts/remote_ci.sh`(origin/dev)의 클라이언트 관리가 이렇게 바뀐다. 고치는 일은 그쪽 세션이 한다.

| 지금 | M5q 뒤 |
|---|---|
| `RCM_WHEEL` = GitHub 릴리스 v0.5.0, `RCM_MIN_VERSION` = 0.5.0. 서버가 오를 때마다 손으로 올렸다 | 부트스트랩 전용이 된다. 한 번 0.8.0 으로 올리면 서버가 오를 때 다시 올릴 필요가 없다. 따라가기 부트로더에 결함이 있을 때만 올린다 |
| `--update-client` 가 클라이언트 venv 를 서버 wheel 로 다시 깐다 | 설치본(부트로더)을 올리는 길로만 남는다. 평소에는 필요 없다 |
| `client_drift_hint` 가 「서버보다 낮다」를 한 줄로 알린다 | 따라가면 차이가 없으니 조용해진다. 고정한 경우만 의미가 있다. 남길지는 그쪽 판단이다 |
| `may_reinstall` · `venv_is_editable` 가 운영 editable venv 를 덮지 않는다 | 그대로 필요하다. rcm 의 기본도 editable 은 따라가지 않으니 두 규칙이 맞물린다 |
| `~/.local/bin/rcm` 이 Mac mini 에서는 운영 editable venv 를 가리킨다 | 그 rcm 은 editable 이라 따라가지 않는다. 오늘과 같다 |
| selftest ⑨-b 의 가짜 rcm 버전이 바닥 이상이어야 한다 | 바닥을 올릴 때 한 번 맞춘다 |

dolomood 에 설치된 `rcm-*-connect` 스킬이 rcm 의 플래그를 부르는 자리도 그쪽 세션이 본다. 서버가 내려가면 새 플래그가 사용법 오류가
되는 경우(§2.7)가 있다.

운영 서버에서 켜거나 바꿀 설정은 없다. 따라가기는 세션 머신의 설정으로 켜고 끈다.

## 11. 결정 (149~174)

| # | 결정 | 추천 |
|---|---|---|
| 149 | 기본 동작 | 서버에 붙는 명령은 서버 버전으로 돈다. 설치본은 부트로더다 |
| 150 | 방향 | 문자열이 다르면 따라간다. 내려가기도 따라간다. 와이어 계약(`min_client_version`)은 그대로 |
| 151 | 받고 실행하는 길 | health 의 `client_wheel` → `/client/…whl` → sha256 → pip 없이 풀기 → 같은 인터프리터 `-P -c BOOT` 로 `execve`. `PYTHONPATH` 는 안 쓴다 |
| 152 | 캐시 자리 | `$XDG_CACHE_HOME/rcm/`(기본 `~/.cache/rcm/`). 서버 기본 `data_dir` 인 `~/.local/share/rcm` 과 떼어 둔다 |
| 153 | 버전 확인 | 서버 주소별 10분 기록, 실패 60초 기억, health 3초 제한, 값 형식 검사(`bad_health`), 리다이렉트 안 따름, 토큰은 언제나 Bearer. 못 닿으면 기록과 sha 가 맞는 캐시 |
| 154 | 설정 우선순위 | `--no-follow` > `RCM_CLIENT_VERSION` > 한 줄 파일 > 설치 종류 기본값. 틀린 값은 종료 2 |
| 155 | 고정 자리 | `client.toml` 키는 없다. 부트로더만 읽는 `~/.config/rcm/client-version` 한 줄 파일 |
| 156 | editable · source 설치 | 기본 `installed`. 명시하면 따라간다 |
| 157 | 실패 | 설치본으로 계속 + 경고 한 줄(§2.6 의 코드). sha 불일치는 캐시에 아무것도 안 남긴다. Ctrl-C 는 130 이고 임시 폴더를 지운다 |
| 158 | 고정 `X.Y.Z` | 설치본 → 캐시 → 서버가 X 일 때 받기 → 설치본 + 경고 |
| 159 | Python 요구 | METADATA 의 `Requires-Python` 에서 `>=X.Y` 한 꼴만 읽는다. 다른 꼴은 안 따라간다 |
| 160 | 따라가는 명령 | argparse 전에 argv 를 훑는다. 안 따라가는 목록(`serve` `worker` `token` `init` `discover` `version`, `gc --config`, 도움말) 밖은 모르는 명령까지 따라간다. `skills` 는 서버가 설정돼 있을 때만 |
| 161 | 고리 막기 | BOOT 가 따라가기 변수를 꺼내 `_follow_boot` 로 남긴다. 푼 `__version__` 이 목표와 같아야 받는다. 캐시가 망가졌으면 설치본으로 돌아간다 |
| 162 | 프로그램 안 호출 | `main(argv)` 에 목록을 넘기면 아무것도 안 한다 |
| 163 | 캐시 청소 | 받은 직후에만. 최근 사용 3개 + 잠긴 것 + 지금 목표를 남긴다. exec 직전에 표지 utime |
| 164 | 캐시 폴더 권한 | realpath 기준 0700 · 내 소유 · 그룹과 남이 쓸 수 없음. 상대 `XDG_CACHE_HOME` 은 무시 |
| 165 | 풀기 안전 | 이름 허용 목록 · `..` 와 절대 경로와 심링크 거부 · 크기 상한 |
| 166 | 표시 | `rcm version` 둘째 줄(정보 있을 때만) · `--json` 의 `follow` · `rcm check` `client` 행 꼬리 · 처음 받을 때 stderr 한 줄. `--version` 은 한 줄 그대로 |
| 167 | wheel 의 skills | `ASSET_DIRS` 에 `skills`. wheel 파일 목록 = 패키지 파일 목록을 시험이 잠근다. PR A0 로 먼저 |
| 168 | 클라이언트 버전 기록 | 제출 경로에서만 서버 로그 한 줄(`client <토큰 이름> runs v<X>`). DB 변경 없음 |
| 169 | 보안 | §4.2 의 (a) 어디서나 켬. **오너 선택**. 위험(세션 사용자의 GitHub 토큰 · SSH 키까지 닿는 코드 실행, `.local` 위조)을 문서에 그대로 적고 끄는 법 셋을 준다 |
| 170 | mDNS 로 찾은 서버 | (a) 에 들어 있다. 따라간다. 둘 이상이면 지금처럼 거절 |
| 171 | 원격 워커 | B1: 기동(fresh health) · 기동 등록 409. B2: heartbeat `server_version` · 레인 재등록 409 에서 `draining`(`stopping` 과 별개). 잡이 끝난 뒤 설치본 부트로더로 재실행, 300초 상한 |
| 172 | Windows | 따라가기 꺼짐(`os.name != "posix"`). Windows 지원은 지금도 사실상 없다 |
| 173 | 릴리스 | A0 는 M5p 와 함께 v0.7.0 에 실을 수 있다. A 는 M5p P3 뒤 rebase, A · B1 · B2 를 묶어 v0.8.0 |
| 174 | dolomood | 부트스트랩만 남는다. 그쪽 레포의 변경은 그쪽 세션 |

## 12. 구현하며 바뀐 것

(구현과 격리 검증이 끝난 뒤 PR 마다 채운다.)

## 13. 설계 검토가 바꾼 것

초안(PR #214 의 첫 커밋)을 격리 설계 검토가 읽었다. 받아들인 지적과 이 판에서 달라진 곳이다.

| 지적 | 초안 | 이 판 |
|---|---|---|
| P0-1 | 인자를 argparse 로 해석한 뒤 따라갔다. 설치본이 모르는 새 명령 · 플래그는 따라가기 전에 사용법 오류로 끝났다 | argparse 전에 argv 를 훑는다(§2.1). 「안 따라가는 명령 목록」 밖은 모르는 명령까지 따라간다. `skills` 는 서버가 설정돼 있을 때만. `--help` 는 설치본 도움말 |
| P0-2 | 결정 147~172 | M5p 가 125~148 을 써서 149~174 로 다시 매겼다 |
| P0-3 | 워커 드레인이 `stopping` 을 켰다. 그 경로는 도는 잡을 lost 로 보고한다 | 별도 `draining`. 레인은 맨 위에서 빠지되 받은 잡은 끝까지 돌리고 보고한다. `run()` 이 레인이 다 끝난 뒤 `stopping` 을 켠다(§3.3 · §5.4) |
| P0-4 | 실기 워커 단계가 서버 0.6.1 을 썼다. 0.6.1 은 heartbeat 에 `server_version` 이 없어 단계가 성립하지 않았다. 클라이언트가 사용자의 홈을 썼다 | M5q 이후 빌드 둘(dev0 · dev1)로 다시 썼다. 클라이언트 C 의 모든 명령은 시험용 `HOME` 에서 친다(§7) |
| P1-1 | `client.toml` 키 `client_version` 과 `FOLLOW_CONFIG_SINCE` · `config_key` 경고 | 키를 없앴다. 고정은 환경 변수, `--no-follow`, 부트로더만 읽는 한 줄 파일(§2.2) |
| P1-2 | health 값을 그대로 믿었다 | `version` · `sha256` · `path` · `bytes` 형식 검사, 어긋나면 `bad_health`. `RCM_CLIENT_VERSION` 도 같은 정규식(§2.4) |
| P1-3 | 리다이렉트 처리를 정하지 않았다. 토큰은 `basic` 일 때만 실었다 | 리다이렉트를 따르지 않고 3xx 는 `unreachable`. 토큰이 있으면 언제나 Bearer |
| P1-4 | BOOT 가 잠금 실패나 import 실패에서 죽을 수 있었다 | 경로 넣기(표지가 있을 때만)와 잠금(best-effort)을 나누고 import 를 감싸 설치본으로 돌아간다(§2.5) |
| P1-5 | 레인 재등록 409 는 다루지 않았다 | 버전 불일치면 드레인(④, W7) |
| P1-6 | 보안 서술이 짧았고 mDNS 발견만 위험으로 봤다 | GitHub 토큰 · SSH 키까지 닿는 실행, `.local` 위조를 적고 선택지 (a)(b)(c) 를 두었다. 오너 선택 (a)(§4) |
| P1-7 | PR A 하나에 skills 수정을 넣었다. 릴리스는 0.7.0 | A0 를 먼저 내고 M5p 와 v0.7.0 에 실을 수 있다. A 는 M5p P3 뒤 rebase, 릴리스는 v0.8.0 |
| P1-8 | 위 경우를 잡는 시험이 없었다 | R 표 ①~⑧ 과 F48~F52 · W7 을 더했다 |
| P2-1 | 고리 막기가 `RCM_FOLLOWED` 환경 변수였고 자식 프로세스로 샜다 | BOOT 가 꺼내고 `_follow_boot` 로 남긴다 |
| P2-2 | health 실패를 기억하지 않았다 | 60초 음성 기록 |
| P2-3 | 받는 중 Ctrl-C 를 정하지 않았다 | 종료 130, `finally` 로 `.tmp` 정리 |
| P2-4 | 표지 utime 을 실행할 때 한다고만 했다 | `exec` 직전에 부모가 한다 |
| P2-5 | 기록만으로 돌 때 같은 버전이면 아무 폴더나 썼다 | sha 가 맞는 폴더만 |
| P2-6 | 캐시 폴더를 적힌 경로로 검사했다 | realpath 로 검사, 상대 `XDG_CACHE_HOME` 무시 |
| P2-7 | 워커 ① 이 10분 기록을 쓸 수 있었다 | 기동은 언제나 health 를 새로 읽는다 |
| P2-9 | 서버 로그 한 줄을 모든 클라이언트 요청에서 남겼다 | 이미 인증하는 제출 경로에서만 |
| P2-10 | health 에 `requires_python` 키를 더하고 지정자 해석기를 뒀다. 워커 드레인이 B 하나에 들어 있었다 | 키와 해석기를 뺐고 METADATA 의 `>=X.Y` 만 읽는다. ③ 드레인은 PR B2 로 나눴다 |
| P2-11 | 「Windows 에서는 지금도 클라이언트가 돌지 않는다」 | 「Windows 지원은 지금도 사실상 없다」 |
