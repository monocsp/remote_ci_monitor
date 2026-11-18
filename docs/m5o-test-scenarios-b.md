# M5o 테스트 시나리오 B — 실패를 이름으로 말한다 · 알림 링크를 바로잡는다 (2026-11-18)

`docs/m5o-workplan.md` §3 · §5 B 표 · 결정 117~120 의 역할 문서다. 브랜치
`feat/m5o-failure-name-summaries`. 테스트를 먼저 쓰고(빨간 것을 본 뒤) 구현한다.

## 무엇을 잠그나

1. **요약 한 줄이 이름을 말한다.** `exit_code` 요약은 실패 이름과 스텝을 알면 그것을 먼저 말한다.
   코드는 `exit_code` 그대로이고 인자만 는다(`step` · `first` · `more` · `more_truncated`).
   `timed_out` 은 `last_step` 을 알면 `(in <스텝>)` 을 붙인다.
2. **두 완료 자리가 같은 글자를 낸다.** 로컬 레인(`worker.py`)과 원격 워커의 finish
   (`remote_workers.py`)가 같은 `outcome.exit_summary` 를 지난다(`worker.outcome_for` 를 둘 다 쓴다).
3. **공개 인자는 경계가 있다.** `summary_args` 는 `/api/status` 와 같은 등급으로 실린다 — 제어 문자 ·
   보이지 않는 문자를 지우고, 경로는 마지막 조각만, 80자에서 자른다.
4. **알림 훅이 이름을 받는다.** `RCM_FAILURES`(줄바꿈 · 20개) · `RCM_FAILURE_COUNT`, 웹훅 본문의
   `failures` 배열.
5. **링크에 `0.0.0.0` 이 없다.** `server_base_url(section, *, hostname)` 한 함수를 알림과 서버가 같이
   쓰고, `rcm check` 가 빈 `public_url` 을 warn 으로 알린다.

## 표 — 요약 (`core/outcome.py` · `worker.py` · `remote_workers.py`)

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| B1 | 베스트: 스텝 + 단위 이름 둘 | `step::test` · `fail::test/a/x_test.dart` · `fail::test/b/y_test.dart` · `fail::test`, exit 1 | `test: x_test.dart +1 more (exit 1)`, 코드 `exit_code`, 인자 `{"code":1,"step":"test","first":"x_test.dart","more":1}` |
| B1b | 단위 이름 하나 | 위에서 이름 하나 | `test: x_test.dart (exit 1)` — `more` 없음(0 은 싣지 않는다) |
| B2 | 스텝만 | `step::analyze` · `step-end::fail`, exit 1 | `analyze failed (exit 1)`, 인자에 `first` 없음 |
| B2b | 단위 이름만(스텝 선언 없음) | `fail::x_test.dart`, 스텝 마커 없음 | `x_test.dart (exit 1)` |
| B2c | 실패 스텝 둘 | `analyze`·`test` 둘 다 `step-end::fail` | `analyze failed (exit 1)` — 스텝은 `failed_step`(첫 선언), 스텝 이름은 `more` 에 안 센다 |
| B3 | 아무것도 없다 | 마커 없음, exit 1 | `exit 1`, 인자 `{"code":1}` — 오늘과 같은 글자 · 같은 인자 |
| B4 | `::rcm::summary::` 를 찍었다 | `summary::2 tests failed` + `fail::x` | `2 tests failed`, 코드 없음 |
| B5 | 이름이 120자 경로 | `fail::` + `a/`×20 + 길이 100 의 파일 이름 | `first` 는 마지막 조각, 80자 |
| B5b | 경로 끝이 `/` · `\` 구분자 | `fail::dir\sub\x_test.dart` | `x_test.dart` |
| B6 | `fail_truncated` | 서로 다른 이름 101개 | `+99+ more` — 정확한 수를 주장하지 않는다, 인자 `more_truncated: true` |
| B7 | 원격 워커 완료 | B1 과 같은 로그를 워커가 올리고 `finish failed exit 1` | 로컬 레인과 **같은 글자 · 같은 인자** |
| B8 | 이름에 제어 문자 · bidi | 마커 입구가 지운 뒤에도 남는 U+202E · 대장에 직접 넣은 `\n` | 요약 · 인자 · env 에서 지워진다 |
| B8b | C1 · 줄 구분자 (검증 라운드 1) | `fail::a<U+0085>b<U+2028>c<U+202E>evil<ESC>[31mred`, 로컬 레인과 원격 finish 둘 다 | 입구(`progress.clean_name`)가 U+0080–U+009F · U+2028 · U+2029 · U+061C 를 지운다. 대장 · `failed_step` · 요약 · `RCM_FAILURES` 어디에도 줄을 나누는 글자가 없고, `splitlines()` 로 센 줄 수가 `RCM_FAILURE_COUNT` 와 같다. `outcome._INVISIBLE_RE` · `notify.failure_lines` 도 같은 글자를 지운다(방어 한 겹 더) |
| B9 | 타임아웃 + `last_step` | `step::test`, 시간 초과(60초) | `limit 1m (in test)`, 인자 `{"seconds":60,"last_step":"test"}` |
| B9b | 타임아웃, 스텝 없음 | 마커 없음 | `limit 1m` — 오늘과 같다 |
| B14 | 성공 잡 | exit 0 + `fail::x` 마커 | 요약에 아무것도 안 붙는다(코드 없음, 성공의 요약은 잡의 문장) |
| B15 | 취소 · 유실 | `fail::x` 뒤 취소 | `cancelled by …` — 이름을 안 붙인다(결정 64) |
| B16 | 시작 전 실패(원격 preflight) | 워커가 `summary_code: tool_missing` | 그 코드 그대로 — `exit_summary` 를 지나지 않는다 |
| B17 | 스텝 이름이 80자 넘음 | 120자 스텝 이름 | 인자 `step` 80자, 문장 200자 이하 |
| B18 | 옛 행 · 화면 | 인자 `{"code":1}` 만 있는 옛 잡 | 웹 · CLI 가 그대로 `exit 1` |
| B19 | 원격 경계 | 워커가 finish 에 `summary_code: exit_code` 를 실어 보냄 | 400 그대로(워커가 고를 수 있는 코드는 시작 전 실패뿐) |

## 표 — 알림 (`core/notify.py` · `notify.py`)

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| B10 | env | 실패 이름 셋(스텝 하나 포함) | `RCM_FAILURES` = 잡이 찍은 순서로 줄바꿈 연결, `RCM_FAILURE_COUNT` = `3` |
| B10b | 상한 | 이름 25개 | `RCM_FAILURES` 20줄, `RCM_FAILURE_COUNT` = `25` |
| B10c | 잘림 | `fail_truncated` + 이름 100개 | `RCM_FAILURE_COUNT` = `100+` |
| B10d | 이름 없음 · 성공 | 대장 행 없음 | 두 변수 모두 빈 문자열(키는 있다) |
| B10e | 4 KB | 120자 한글 이름 20개(7 KB) | 줄 단위로 잘려 4096 바이트 이하, 반쪽 줄 없음 |
| B11 | 웹훅 본문 | url 규칙 | 본문 최상위 `failures`(20개 상한) · `failure_count` · `failures_truncated` |

## 표 — 링크 (`config.server_base_url` · `server.App.base_url` · `notify.Notifier`)

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| B12a | `public_url` 있음 | `public_url = "https://ci.example/"` | `https://ci.example` |
| B12b | bind 특정 | `bind = "100.64.0.5"` | `http://100.64.0.5:8787` |
| B12c | `0.0.0.0` + 광고 | `bind = "0.0.0.0"`, advertise 기본 | `http://macmini.local:8787`(hostname `macmini.lan` 의 짧은 이름) |
| B12d | `0.0.0.0` + 광고 끔 | `advertise = false` | `http://macmini.lan:8787` |
| B12e | `::` | `bind = "::"` | B12c/d 와 같다 |
| B12f | 빈 bind | `bind = ""` | 같다 |
| B12g | IPv6 특정 | `bind = "fd00::5"` | `http://[fd00::5]:8787` |
| B12h | hostname 이 비었다 | hostname `""` | `http://localhost:8787` — 그래도 `0.0.0.0` 은 아니다 |
| B12i | 서버 · 요청 Host | Host 헤더 `10.0.0.5:8787` | Host 가 이긴다(오늘 규칙) |
| B12j | 서버 · Host 가 `0.0.0.0` | Host `0.0.0.0:8787` | Host 를 버리고 `server_base_url` |
| B12k | 알림 | `bind = "0.0.0.0"`, `public_url` 빈 서버의 Notifier | `RCM_URL` 에 `0.0.0.0` 없음 |
| B13 | `rcm check` | 빈 `public_url` · bind 0.0.0.0 | `warn  public url    empty — links use http://<host>.local:<port> (set [server] public_url)`, 종료 코드 0 |
| B13b | `rcm check` | `public_url` 있음 | `ok    public url    <그 주소>` |

## 표 — 화면 (`web/i18n.js` · `web/app.js`)

| # | 경우 | 기대 |
|---|---|---|
| W1 | 영어 화면 | `outcome.exit_code` 가 서버와 **같은 글자**(B1 · B2 · B3 · B6) |
| W2 | 한국어 화면 | `test: x_test.dart 외 1개 (종료 코드 1)` · `analyze 실패 (종료 코드 1)` · `외 99개 이상` |
| W3 | 타임아웃 | `limit 1m (in test)` — 옛 화면은 `a.limit`(서버가 안 보내는 키)을 찍어 `undefined` 였다 |
| W4 | 최근 행 | 요약이 이미 스텝을 말하면 ` · step test` 를 다시 붙이지 않는다 |
| W5 | 최근 행 · 긴 이름 (검증 라운드 1) | 행의 `failed_step`/`last_step` 원문을 인자와 같은 모양(씻고 코드 포인트 80개)으로 만든 뒤 견준다 — 120자 · 보이지 않는 문자가 든 스텝도 두 번 안 찍힌다. `rcm top` 도 같다 |

## 변이 (`scripts/mutcheck.py`)

- `failure-summary-remote-old-text` — 원격 finish 가 마커를 안 읽어 옛 `exit 1` 로 돌아감.
- `notify-link-zero-address` — `server_base_url` 이 bind 를 그대로 써서 `http://0.0.0.0:…` 을 냄.
- `failure-summary-full-path` — `first` 가 마지막 조각이 아니라 경로 전체.
