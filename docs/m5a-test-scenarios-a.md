# M5a 테스트 시나리오 A — 우선순위 정렬 · manifest 검증 · blob GC 규칙 · 알림 필터 (2026-09-06)

`docs/m5-workplan.md` M5a-1 · M5a-2(프로토콜 · 저장·정리) · M5a-3 의 **순수 규칙**을 pytest 로 옮긴 것이다
(test-first, 역할 A). `src/` · `scripts/` · 워크플로 · 기존 테스트는 건드리지 않았다. I/O · 서버 · 스레드 · SQLite 는
없다 — 라우트(`/tree/manifest` · `/priority`) · blob 저장 · `store.claim`/`join_or_bump` · janitor · 알림 스레드 ·
설정 검증은 B/C 의 몫이다.

쓰는 동안 구현이 같은 워크트리에 병렬로 들어왔다(`core/manifest.py` · `core/notify.py`). 인계 시점 상태:

| 파일 | 대상 | 테스트 함수 / 수집 건수 | 상태 |
|---|---|---|---|
| `tests/test_priority.py` | `core/model.py` 의 `priority` · `PRIORITY_*` · `core/queue.py` 의 정렬 · `eta_for_new(priority=)` · `priority_from_name` · `join_priority` | 23 / 41 | ImportError(빨강 — 아직 없음) |
| `tests/test_manifest.py` | `core/manifest.py` — `validate_manifest` · `missing_hashes` · `assemble_plan` | 41 / 173 | 169 초록 · **4 빨강**(아래 「구현에서 발견한 것」) |
| `tests/test_retention_blobs.py` | `core/retention.py` — `BlobInfo` · `blobs_to_purge` | 18 / 18 | ImportError(빨강 — 아직 없음) |
| `tests/test_notify_rules.py` | `core/notify.py` — `NotifyRule` · `rules_for` · `notify_env` · `sanitize_text` | 25 / 47 | 46 초록 · **1 빨강** |

공통 규칙: 잡은 `jobfactory.job()` 으로 만들고(`**kw` 로 `priority=` 를 넘긴다) 시각은 고정 `NOW`. `compute_queue` ·
`eta_for_new` 호출 모양은 `test_queue.py` 의 `rows_for`/`eta` 와 같다. 파일마다 `ruff check` · `ruff format --check`
가 깨끗하다(line-length 100, CJK 2폭).

## 1. `tests/test_priority.py` — `(-priority, id)`

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 상수 | `PRIORITY_LOW/NORMAL/HIGH == -1/0/1` · `PRIORITY_NAMES == {"low": -1, "normal": 0, "high": 1}` |
| 2 | 기본값 | `Job.priority` · `Preset.priority` 기본 0 · `Preset(priority=1)` · `job(priority=1)` |
| 3 | 정렬 키 | normal·high·low·high(id 1~4) → 출력 순서와 `position` 이 `[(2,1),(4,2),(1,3),(3,4)]` |
| 4 | 동률은 id | 같은 high 에서 `created_at` 이 뒤집혀 있어도 id 순 |
| 5 | 순번 연속 | 7개 섞인 우선순위 → `position` 1..7 연속, 우선순위 내림차순 |
| 6 | 대기 계산이 순서를 따른다 | 레인 1: 오래된 normal 보다 새 high 가 `wait 0`, normal 은 400 · `ahead_job_id` = high · `finish_at` 800 · **reason 은 그대로 `waiting_for_lane`**(우선순위는 이유가 아니다) |
| 7 | low | 나중에 들어온 normal 두 개 뒤(대기 1000) |
| 8 | uploading 도 대기 잡 | uploading high 가 queued normal 앞(position 1, reason `uploading`) |
| 9 | 레인 2 | high 가 첫 레인, normal#1 이 둘째 레인(wait 0), normal#2 가 400 |
| 10 | paused | 순서·순번은 우선순위대로, `wait`·`finish_at` 은 null, reason `paused` |
| 11 | running 은 안 밀린다 | low running + high queued → running 이 먼저(position None), high 는 잔여 280 을 기다린다 |
| 12 | 출력 순서 | running → cancelling → 대기(우선순위순) — cancelling 이 high 여도 running 뒤 |
| 13 | `not_scheduled` | idle 레인이 놀 때 **우선순위순 첫 잡**(high) 이 `not_scheduled`, 다음이 `waiting_for_lane` |
| 14 | 그룹 | low running 이 `devices` 를 잡고 있으면 high 대기 잡도 `blocked_by_group` · `blocked_by` · 하한 `≥ blocker finish + expected` — 그룹 규칙은 우선순위와 무관 |
| 15 | 합류 키 | `join_key` 시그니처에 `priority` 가 없다(합류 판정은 우선순위와 무관) |
| 16 | `eta_for_new(priority=high)` | running 1 + 대기 2 → 가상 잡 position 1 · wait 280(running 잔여만) · `ahead == 1` |
| 17 | `eta_for_new` normal | low·normal·high 가 있으면 position 3(high·normal 뒤, low 앞) · wait 800 · `ahead == 2` |
| 18 | `eta_for_new(priority=low)` | 기존 low 뒤 맨 끝 |
| 19 | 기본값 | `priority` 인자를 생략하면 normal 이고 전부 normal 일 때 결과는 기존 FIFO 와 같다(회귀) |
| 20 | 레인 2 + running 2 | high 가상 잡은 먼저 비는 레인(잔여 280) · `ahead == 2` |
| 21 | `priority_from_name`(3건) | `low/normal/high` → −1/0/1 |
| 22 | 거부(9건, `ValueError`) | `urgent` · `""` · `High` · `HIGH` · `"1"` · `"0"` · `"-1"` · 앞뒤 공백 — 문구에 `priority` 또는 `low` |
| 23 | `join_priority`(9건) | 모든 조합에서 `max` — 낮은 요청이 기존을 내리지 못한다 |

## 2. `tests/test_manifest.py` — 경로 규칙 · 상한 · 링크 · 조립

헬퍼: `f(path, mode=0o100644, size=3, sha256=H1)` · `ln(path, target)` · `validate(files, links, max_bytes)` ·
`rejects(...)` — `ManifestError` 를 기대하고 **문구가 한 줄 · 200자 이하**인지도 본다. `flat(op)` 은 `Op` 를
`("mkdir", path)` · `("copy", path, sha256, mode)` · `("symlink", path, target)` 튜플로 편다.

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 빈 manifest | `files == ()` · `links == ()` · `total_bytes 0` · `unique_hashes` 비어 있음 |
| 2 | `links` 생략 | 키가 없으면 빈 튜플 |
| 3 | 필드·순서 | `files` 는 **manifest 순서 그대로**(조립 순서다) · `ManifestFile(path, mode, size, sha256)` · `ManifestLink(path, target)` · `total_bytes` 합 · `unique_hashes` 집합 · 튜플 타입 |
| 4 | frozen | 속성 대입은 `FrozenInstanceError` |
| 5 | 같은 내용 두 경로 | 바이트는 두 번, 해시는 한 번 |
| 6 | 0 바이트 파일 | 허용 |
| 7 | 문서 모양(9건) | dict 아님 · `files` 가 list 아님 · 항목이 dict 아님 · `links` 가 list 아님 → `ManifestError` |
| 8 | 파일 항목 키 누락(4건) | `path` · `mode` · `size` · `sha256` 하나라도 없으면 거부 |
| 9 | 링크 항목 키 누락(2건) | `path` · `target` |
| 10 | 허용 경로(15건) | `a` · `a/b/c.txt` · 한글 · 이모지 · 공백 · `.gitignore` · `.github/…` · `a.git/x` · `src/.git.bak` · `-rf` · `src/.hidden` · 255자 조각 · 끝 공백 · `x/..y/z` · `…/dots` |
| 11 | 거부 경로(24건) | 절대(`/etc/passwd` `/a` `/`) · `""` · `..` 조각(`..` `../x` `a/../b` `a/..` `a/../../x`) · `.` 조각(`.` `./a` `a/./b` `a/.`) · 빈 조각(`a//b` `a/` `//a`) · 백슬래시 · NUL · `.git` · `.git/config` · `.git/objects/aa/bb` · `sub/.git/HEAD` |
| 12 | 링크 경로도 같은 규칙(24건) | 11 의 목록을 링크 `path` 로 |
| 13 | 허용 링크 경로(4건) | 한글 · 공백 · `.gitignore.lnk` · 깊은 경로 |
| 14 | 문자열 아닌 경로(4건) | `None` · int · bytes · list — 파일·링크 둘 다 |
| 15 | mode 정규화(11건) | `0o100644→0o644` · `0o100755→0o755` · `0o600→0o644` · `0o700→0o755` · `0o111→0o755` · `0o777→0o755` · `0→0o644` … 실행 비트만 본다 |
| 16 | int 아닌 mode(5건) | `"644"` · `None` · `1.0` · `"0o755"` · list |
| 17 | sha256(9건) | 대문자 · 63자 · 65자 · `g` · `""` · `None` · int · **끝 개행** · 앞 공백 → 거부 |
| 18 | size(5건) | 음수 · `"3"` · `3.0` · `None` · list → 거부 |
| 19 | 합계 == 상한 | 허용(`total_bytes` 300) |
| 20 | 합계 > 상한 | 거부, 문구에 `exceeds` · 파일 하나가 상한을 넘어도 |
| 21 | `MAX_MANIFEST_FILES` | 200 000 · 정확히 200 000 파일 허용 · 200 001 거부 |
| 22 | 중복 | 파일 둘 · 파일+링크 · 링크 둘 |
| 23 | 파일/디렉터리 충돌(7건) | `a` + `a/b`(순서 무관) · `a/b/c` + `a/b` · 링크 `d` + 파일 `d/y` · 안을 가리키는 링크 `a/d → ..` + `a/d/x` · 링크 `a` + 파일 `a/b` · 링크 `l` + 링크 `l/inner` |
| 24 | 접두만 같은 이름 | `a` · `ab` · `a.txt` · `a_dir/c` 는 충돌 아님 |
| 25 | 허용 target(11건) | 형제 · 하위 · `../x`(정규화하면 안) · `../../c` · `a/../b` · `a/./b` · `..`(루트 자신) · `.` · dangling · 링크→링크 체인 · 한글 |
| 26 | 거부 target(11건) | 절대 · `/` · `../x`(루트에서) · `..` · `../` · `a/../../x` · `a/lnk → ../../x` · 세 단계 · **나갔다 들어오는 `../../a/x`** · `""` · NUL |
| 27 | 문자열 아닌 target(3건) | `None` · int · list |
| 28 | `missing_hashes` | 정렬 · 중복 제거 · `have` 에 있는 것 제외 · 무관한 `have` 해시 무시 · 링크는 해시가 없다 · 빈 manifest → `[]` |
| 29 | 조립 순서 | mkdir 전부 → copy 전부 → symlink 전부 · mkdir 은 모든 부모를 **한 번씩** · 부모가 자식보다 앞 · copy/symlink 는 manifest 순서 · copy 에 sha256·정규화된 mode |
| 30 | 루트 파일만 | mkdir 없음 |
| 31 | 깊은 경로 | `a` `a/b` `a/b/c` `a/b/c/d` 순 |
| 32 | 링크 부모 | 링크만 있어도 부모 mkdir |
| 33 | 같은 blob 두 경로 | copy 두 번(경로마다) |
| 34 | 빈 manifest | `[]` |
| 35 | 이중 안전 | 검증을 거치지 않은 `Manifest(files, links, total_bytes)` 에 파일/디렉터리 충돌이 있으면 `assemble_plan` 도 `ManifestError` |

## 3. `tests/test_retention_blobs.py` — `blobs_to_purge`

`blob(name, size, age=)` 는 `last_used_at = NOW − age` · `purge(blobs, referenced, days=30, max_bytes=큰 값, now=NOW)`.

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 필드 순서 | `BlobInfo(sha256, size, last_used_at)` 위치 인자 == 키워드 |
| 2 | 빈 입력 | `[]`, `max_bytes=0` 이어도 `[]` |
| 3 | iterable · 같은 객체 | 제너레이터를 받고 **같은 BlobInfo 객체**를 돌려준다 |
| 4 | 경계 `>=` | 정확히 30일 → 대상 · 30일 − 1초 → 아님 |
| 5 | `days=0` | 참조 안 된 것 전부(오래된 순) |
| 6 | `now` 기준 | 벽시계가 아니다 |
| 7 | days 규칙은 상한과 무관 | 합계가 작아도 오래된 것은 지운다 |
| 8 | 참조는 절대 | 400일 된 참조 blob 도 안 지운다 |
| 9 | 참조 + 상한 | 참조된 것만으로 상한을 넘어도 참조 안 된 것만 지우고 멈춘다(오류 아님) |
| 10 | 참조 바이트도 합계에 든다 | 참조 200 + 100 + 100, 상한 350 → 참조 안 된 것 중 가장 오래된 하나 |
| 11 | 상한: 오래된 순 | 300 == 상한 → 없음 · 299 → 1개 · 150 → 2개 · 0 → 3개, 항상 오래된 순 |
| 12 | 큰 blob 하나면 충분 | 500 이 가장 오래됐으면 그것만 |
| 13 | 필요한 만큼만 | 셋 다 오래돼도 상한 아래로 가는 데 필요한 하나만 |
| 14 | 섞임 | days 초과분 먼저, 그 다음 남은 것에 상한 → `[a(40d), b(10d)]` |
| 15 | 한 번만 | days 와 상한에 모두 걸려도 한 번 |
| 16 | 정렬 | 입력이 섞여 있어도 `last_used_at` 오름차순 |
| 17 | days+상한 한 목록 | 하나의 오름차순 목록 |

## 4. `tests/test_notify_rules.py` — `rules_for` · `notify_env` · `sanitize_text`

규칙 셋: `slack-fail`(failed·timed_out·lost, presets gate·deploy, argv) · `all-done`(모든 종료 상태, presets None) ·
`qa-ok`(succeeded, presets qa, url). `ROW` 는 `recent_json` 모양(`id` · `preset` · `key` · `requester{name,label}` ·
`state` · `exit_code` · `job_seconds` · `summary` · `failed_step` · `url` …).

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `NotifyRule` | frozen · 필드 `name/on/presets/argv/url/timeout_seconds` |
| 2 | 필터(11건) | 상태 ∈ `on` **그리고** (presets None 또는 preset ∈ presets) · `gate-fast` 는 `gate` 가 아니다 · `running`/`queued` → `[]` |
| 3 | 순서 | 설정 순서 보존(뒤집으면 뒤집힌다) |
| 4 | 없음 | 규칙 0개 · 안 맞음 → `[]` · 어떤 시퀀스든 받고 새 list 를 돌려준다 |
| 5 | env 키 | 정확히 11개 `RCM_JOB_ID · RCM_STATE · RCM_PRESET · RCM_KEY · RCM_REQUESTER · RCM_SUMMARY · RCM_FAILED_STEP · RCM_EXIT_CODE · RCM_JOB_SECONDS · RCM_URL · RCM_NOTIFY`, 값은 전부 `str` |
| 6 | env 값 | `"412"` · state · preset · key · **requester = label**(워커의 `RCM_REQUESTER` 와 같다) · summary · failed_step · `"1"` · `float(...) == 412.5` · url · 규칙 이름 |
| 7 | None → `""` | exit_code · job_seconds · summary · failed_step · url |
| 8 | `exit_code 0` | `"0"` 이지 `""` 가 아니다(falsy 함정) |
| 9 | 키 누락 | 최소 행(`id state preset key requester`)이어도 11개 키, 없는 것은 `""` |
| 10 | 행 불변 | `notify_env` 가 입력 dict 를 고치지 않는다 |
| 11 | 정화 적용 | summary · failed_step · requester label 의 NUL·ESC·BEL 제거 |
| 12 | 개행 보존 | `line1\nline2\n\ttabbed` 그대로 |
| 13 | 절단 | summary `"가"×2000`(6000 B) → `"가"×1365`(4095 B) · failed_step 5000 자 → 4096 · 모든 값이 4096 B 이하이고 strict UTF-8 로 인코딩된다 |
| 14 | `sanitize_text` 제거(13건) | NUL · ESC · BEL · BS · DEL · C0 전부 제거, `\r` 제거 · `\n` `\t` 보존 · 한글/일본어/이모지 보존 |
| 15 | 정확히 4096 | ASCII 4096 · `"가"×1365` · `"🚀"×1024` 그대로 |
| 16 | ASCII 절단 | 5000 → 4096 |
| 17 | 3바이트 경계 | `"가"×2000` → 1365 개(4095 B) |
| 18 | 4바이트 경계 | `"🚀"×1100` → 1024 개(4096 B) |
| 19 | 걸치는 문자 | `a×4095+가` → `a×4095` · `a×4094+가` → `a×4094` · `a×4093+가` → 그대로(4096) |
| 20 | 제거 → 절단 순서 | `NUL×10 + a×4096` → `a×4096`(자른 뒤 빼면 4086 이 된다) |
| 21 | 절단해도 개행 보존 | `"line\n"×1000` → 819 줄 + `l` |
| 22 | `limit` 인자 | 3 · 4(`가`) · 6(`가나`) · 0 |
| 23 | 깨진 서로게이트 | `"a\udcffb…"` 에 예외 없이 한도 안의 str |
| 24 | 항등 | 깨끗한 짧은 문자열은 그대로 |

## 가정

1. **manifest 경로는 정규형만 받는다** — `.` 조각(`./a` · `a/./b` · `a/.`)도 거부한다. 명세는 「절대 경로·`..`·빈 조각
   거부」만 적었지만, `./a` 와 `a` 를 둘 다 받으면 중복 검사가 뚫리고(같은 파일 두 항목) 자재화가 같은 경로에 두 번
   쓴다. 클라이언트는 `Snapshot.entries` 에서만 manifest 를 만들고 그 경로는 `git ls-files`/walk 의 정규형이라 `./`
   가 나올 일이 없다. 반면 **링크 target 은 자유 문자열**이다(`a/./b` · `a/../b` 허용) — 심볼릭 링크에 그대로 박히는
   값이고 검사는 「정규화한 join 이 안에 있는가」뿐이다.
2. **`.git` 은 어느 깊이든 거부**(`sub/.git/HEAD`). 명세는 「`.git/` 접두」라고만 했다. 클라이언트 `select_files` 가
   `a/.git/HEAD` 를 어느 깊이든 빼므로(`ALWAYS_EXCLUDED_DIRS`) 정상 클라이언트가 보낼 일은 없고, 서버가 같은 규칙을
   쓰면 「클라이언트가 안 보내는 것을 서버도 안 받는다」가 된다. `.gitignore` · `.github/` · `a.git/x` 는 허용이다
   (naive `startswith(".git")` 을 잡는다). → 구현은 루트만 거부한다(「구현에서 발견한 것」 2).
3. **파일 항목의 네 키(`path mode size sha256`)는 전부 필수**다. 명세 본문이 그 넷을 적었고 클라이언트는 항상
   보낸다 — 빠졌다면 망가진 클라이언트다. → 구현은 `mode` 를 0o644 로 기본 처리한다(「발견한 것」 1).
4. `MAX_MANIFEST_FILES` 초과 검사는 **파일 수**로 잠갔다(링크 없이 200 000 / 200 001). 구현은 파일+링크 합으로
   세는데 이 테스트는 둘 다 통과한다. 링크를 포함할지는 명세가 정하지 않았다(「의문」 1).
5. **파일/디렉터리 충돌 규칙은 링크에도 적용**한다 — 링크 경로가 다른 항목의 부모이면 거부. 그렇지 않으면 `d → ..`
   같은 「안을 가리키는 링크」 아래에 파일을 두는 manifest 로 자재화가 링크를 **통해** 쓰게 된다(루트 밖은 아니어도
   다른 경로를 덮어쓴다). 구현도 같은 규칙이다(`_check_tree_shape` 가 파일+링크 경로 집합으로 본다).
6. 링크 target 은 **존재를 검사하지 않는다**(dangling 허용). 체인(`l1 → l2 → x`)도 단계마다만 본다 — 명세의
   「keep simple」. 물리적으로 링크를 따라가 밖으로 나가는 조합은 5 의 규칙이 막는 범위까지만 본다(스크립트가 링크를
   따라가는 것은 위협 모델 밖 — 스크립트는 `cd ../..` 도 할 수 있다).
7. `Op` 는 `kind ∈ {mkdir, copy, symlink}` 와 `path` · `sha256` · `mode` · `target` 을 가진 frozen dataclass 로
   봤다(`flat()` 이 그 속성만 읽는다). 구현과 같다.
8. `assemble_plan` 의 mkdir 순서는 **부모가 자식보다 앞 · 중복 없음 · 모든 부모**만 잠갔다. 형제 디렉터리끼리의
   순서(깊이순인지 manifest 순인지)는 단정하지 않는다. copy 와 symlink 는 manifest 순서다(명세 4 「manifest
   순서대로」).
9. `eta_for_new` 의 두 번째 반환값 `ahead` 는 **실제로 앞에 있는 잡 수**(running·cancelling + 우선순위가 같거나
   높은 대기 잡)로 봤다. 지금 구현은 「활성 잡 전부」인데 전부 normal 이면 두 값이 같다(기존 test_queue 의 `ahead`
   단정은 그대로 통과). high 가상 잡에 「3 ahead · position 1」을 보이면 `rcm eta` 가 자기모순이라 이렇게 잠갔다
   (`cli._fmt_eta_row` 는 기존 잡에 `busy_others + position − 1` 을 쓴다 — 같은 뜻).
10. `priority_from_name` 은 **대소문자·공백을 그대로** 본다(`High` · `" high"` 거부). argparse `choices` 와 같은
    엄격함이다. 관대하게 받고 싶으면 CLI 층에서 `lower().strip()` 하고 이 함수는 그대로 두면 된다.
11. `blobs_to_purge` — 참조된 blob 의 바이트도 **합계에 든다**(상한 판정은 저장소 전체 크기다). 참조만으로 상한을
    넘으면 참조 안 된 것을 다 지우고 멈춘다(오류 아님 — 활성 잡을 위한 blob 은 어떤 경우에도 안 지운다). 출력은
    `last_used_at` 오름차순이고 동률의 순서는 단정하지 않았다(`sha256` 으로 안정 정렬하길 권한다).
12. `RCM_REQUESTER` 는 **label**(`alice@laptop`)이다 — 워커가 이미 `RCM_REQUESTER = job.requester.label` 을 준다.
    같은 이름의 변수가 두 곳에서 다른 값이면 스크립트가 헷갈린다.
13. `RCM_JOB_SECONDS` 는 `float()` 로 읽히는 문자열이면 된다(`"412.5"` 든 `"412"` 든). `RCM_EXIT_CODE` 는 정수의
    `str` 이고 `0` 은 `"0"` 이다.
14. `notify_env` 는 `row.get` 으로 읽는다 — 없는 키는 `""`. 알림 스레드가 오래된 행(마이그레이션 전 스키마)을
    만나도 `KeyError` 로 죽으면 안 된다(알림 실패는 조용해도 알림 스레드 사망은 안 된다).
15. 정화는 **제거 → 절단** 순서다(NUL 이 많은 문자열에서 내용을 더 남긴다). 제거 대상은 C0 전체(`\n` `\t` 제외) +
    DEL. C1(`\x80–\x9f`) 은 단정하지 않았다(「의문」 3).
16. `rules_for` 의 `presets` 는 `None` 이면 전부다. **빈 frozenset** 은 테스트하지 않았다 — 명세의 「비면 전부」는
    설정 층(C)이 빈 목록을 `None` 으로 바꾸는 것으로 본다(「의문」 2).

## 구현에서 발견한 것 (인계 시점에 빨간 5건 — `src/` 는 고치지 않았다)

1. `validate_manifest` 가 **`mode` 누락을 0o644 로 기본 처리**한다(`raw.get("mode", MODE_FILE)`) →
   `test_file_entry_missing_key_is_rejected[mode]` 빨강. 가정 3 과 충돌한다. 어느 쪽이든 되지만 「네 키 필수」가
   프로토콜 문서와 맞고, 기본값을 두려면 명세 M5a-2 ②에 「`mode` 생략 시 0o644」 한 줄이 있어야 한다.
2. `_check_path` 가 **루트 `.git` 만** 거부한다(`parts[0] == ".git"`) → `sub/.git/HEAD` 가 파일·링크 둘 다 통과
   (2건 빨강). 가정 2. 어느 깊이든 거부하는 편이 클라이언트 규칙과 맞다(`".git" in parts`).
3. **sha256 정규식 `^[0-9a-f]{64}$` 에 `re.match` — `$` 는 끝 개행 앞에서도 맞는다.** `H1 + "\n"` 이 통과한다
   (1건 빨강). 그 값은 `manifest.json` 에 저장되고 `missing` 목록과 blob 경로(`blobs/<aa>/<sha>`) 에 그대로
   쓰인다 — 개행이 든 파일 이름이 생긴다. `re.fullmatch` 또는 `\Z` 로 고쳐야 한다. **진짜 버그.**
4. **`sanitize_text` 가 깨진 서로게이트에서 `UnicodeEncodeError`** 로 죽는다(notify.py:49, 1건 빨강). 로그는
   `errors="replace"` 로 디코드하니 build 출력으로는 못 들어오지만, **JSON `"\udcff"` 이스케이프**는
   `json.loads` 가 그대로 lone surrogate 로 만든다 — 클라이언트 `label`(→ `requester.label` → `RCM_REQUESTER`) 로
   아무 토큰이나 넣을 수 있다. 그러면 알림 스레드가 예외로 죽고 이후 알림이 전부 조용히 사라진다(fail-open 금지
   위반). `encode("utf-8", "surrogateescape")` 로 길이를 재거나 서로게이트(`category == "Cs"`)를 제어문자처럼
   빼면 된다. 서버 `submit` 에서 label 을 검증하는 것도 별개로 필요하다(C 몫).
5. (빨강은 아니지만) `_check_link_target` 의 `resolved.startswith("/")` 분기는 `target.startswith("/")` 를 먼저
   걸러서 도달하지 않는다 — 해가 없다.

## 명세와 다른 것 · 의문

1. **`MAX_MANIFEST_FILES` 가 링크를 포함하는가.** 명세는 「more than MAX files」. 구현은 `files + links`. 포함하는
   쪽이 안전하다(링크 20만 개도 자재화 비용이다) — 명세에 「항목 수(파일+링크)」로 적으면 된다.
2. **`[[notify]].presets = []`(빈 목록)** — 명세는 「비면 전부」인데 순수 규칙은 `None → 전부`. C 의 설정 검증이
   빈 목록을 `None` 으로 접어야 한다. 빈 frozenset 을 「아무 프리셋도 아님」으로 두면 사용자는 「알림이 안 온다」로
   본다.
3. **C1 제어문자(`\x80–\x9f`)와 서로게이트**를 「제어문자」에 넣을지. `unicodedata.category == "Cc"` 면 C1 도
   빠지고, `ord < 0x20` 만 보면 남는다. 테스트는 어느 쪽도 단정하지 않았다. 4096 바이트 절단의 「바이트」는 UTF-8
   기준으로 잠갔다.
4. **`eta_for_new` 의 `ahead`** — 가정 9. 명세에 한 줄 필요: 「`ahead` = 가상 잡보다 앞에 놓이는 활성 잡 수」.
5. **`Job.priority` 와 `uploading`** — 대기 잡은 uploading 도 포함이라 uploading high 가 queued normal 보다 앞에
   선다(시나리오 1-8). `store.claim` 은 `queued` 만 보니 실제 실행은 업로드가 끝나야 한다 — 화면 순번과 실제 claim
   순서가 잠깐 어긋나는 것은 지금(FIFO)도 같다. README 「기아」 문단에 같이 적을 만하다.
6. **admin `POST /jobs/{id}/priority` 로 낮추기** — 명세는 `--priority high` 예만 있다. `join_priority` 가 max 라
   합류로는 못 내리고, admin bump 로는 내릴 수 있어야 한다(순수 규칙엔 없고 B 의 라우트 테스트 몫). 명세에 「bump 는
   올리고 내릴 수 있다, 합류는 올리기만」 한 줄.
7. `Manifest.unique_hashes` 는 구현이 **property** 다(생성자 인자가 아님). 시나리오 2-35 는 그에 맞춰
   `Manifest(files, links, total_bytes)` 로 만든다 — 생성자 모양이 바뀌면 그 테스트만 손보면 된다.
