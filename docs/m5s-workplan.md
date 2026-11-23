# M5s 작업 명세: 스토어 심사 확인 · 승인 뒤 출시 · 예약 실행

> 2026-11-23 앱 레포 세션이 오너 지시로 rcm(이 레포)에 기능 요청서를 썼다. 요청은 넷이다. F1 은 예약 실행이고 F2 는
> 심사 상태 역할 `status` 다. F3 은 출시 역할 `golive` 와 원칙 변경이고 F4 는 연결 스킬과 `rcm check` 규칙이다.
>
> 같은 날 오너가 요구를 두 가지로 다시 정리했다. 원문을 그대로 옮긴다.
>
> 1. 「심사 올린 후 심사 완료되었는지 안드로이드 ios 둘다 확인하는 스킬 만들기(다른세션 다른컴퓨터에서 접근가능하게)」
> 2. 「심사가 완료되었다면, 배포도 스킬로 호출해서 가능하게 한다 (ios 에서는 api로 배포가 되는데 안드로이드 구글은
>    관리형게시라서 자동배포가 안 됨, 그래서 로그인해서 크롬으로 자동으로 하게 함, 근데 로그인이 풀릴 수 있으므로
>    풀렸다면 요청한 세션에 다른방식으로 로그인 할 수 있또록 함)」
>
> 오너는 같은 날 두 가지를 더 골랐다. 첫째로 예약 실행도 이번에 같이 만든다. 둘째로 로그인이 풀리면 A 길을 기본으로
> 하고 B 길을 대안으로 둔다. A 길은 화면 공유로 Mac mini 에 들어가 로그인하는 방법이다. B 길은 요청한 컴퓨터의 Chrome
> 에서 게시하는 방법이다.
>
> 오너는 진행 방식도 지시했다. 워크트리를 나눠 병렬로 할 수 있는 일은 반드시 병렬로 한다. dev 에서 main 으로 PR 을
> 올릴 때는 개인정보가 없는지 다 확인하고 진행한다.
>
> 결정 번호는 201 부터다. M5r 이 175~200 을 쓴다(`docs/m5r-workplan.md`).
>
> 이 판은 둘째 판이다. 첫 판에 격리 설계 반박(P0 3 · P1 18 · P2 15)을 반영했다. 무엇이 왜 바뀌었는지는 §12 에 있다.

## 0. 한 줄

다른 컴퓨터에서도 rcm 으로 두 스토어의 심사 상태를 묻는다. 출시는 사람이 버전과 빌드 번호와 시각을 적어 요청한 것만
rcm 이 그 시각에 돌린다. Android 처럼 사람이 손대야 하는 순간이 오면 rcm 이 요청한 세션과 화면에 알린다. 로그인이
풀렸을 때가 그런 순간이다. rcm 은 일반 기능만 만든다. 예약 실행, 종료코드 75 반복, 「사람이 할 일」 표식, 선택 역할
두 개(`status`·`golive`)가 그 기능이다. 스토어를 부르는 코드는 앱 레포의 스크립트에 둔다.

## 1. 출처와 오너 결정

요청서와 오너의 말이 어긋나는 곳이 있다. 아래 표는 원하는 것마다 요청서가 적은 것과 이 명세가 정한 것을 나란히 둔다.

| 원하는 것 | 요청서 | 이 명세 |
|---|---|---|
| 1. 두 스토어의 심사 완료를 확인하는 스킬, 다른 컴퓨터에서도 | F2 `status` 역할. 스킬 `store-golive` 는 앱 레포 쪽이 만든다 | 그대로 받는다 |
| 2-iOS. 승인되면 API 로 출시 | F3 `golive` mode=release 가 App Store Connect 의 `POST /v1/appStoreVersionReleaseRequests` 를 부른다(앱 레포 스크립트) | 그대로 받는다 |
| 2-Android. Chrome 으로 Play Console 에서 자동 게시 | 요청서 D2: 정한 시각에 Teams 로 「게시를 누르세요」를 보내고 사람이 누른다 | D2 를 버린다(결정 202). 앱 레포 스크립트가 Mac mini 의 Chrome 전용 프로필로 게시 버튼을 누른다. 관리형 게시는 켠 채로 둔다 |
| 2-Android. 로그인이 풀리면 요청한 세션이 다른 방법으로 로그인 | 없음 | 새로 설계한다: 「사람이 할 일」 표식(§4.3)과 A·B 두 길(결정 203) |
| 정한 시각에 출시 | F1 예약 실행 | 이번에 만든다(결정 204) |

요청서의 D1·D4·D5 와 11-23 오너 답으로 확정된 결정은 셋이다.

- **D1·D4 승인 뒤 출시 원칙.** 원칙을 바꾼다. 사람이 버전과 빌드 번호와 시각을 적어 요청한 출시만 도구가 한다. 승인을 감시해 저절로 출시하는 기능은 계속 금지다. 이번에 바뀌는 원칙은 2026-09-17 에 정한 「승인 뒤 출시는
  절대 자동으로 하지 않는다. 출시는 사람이 콘솔에서」다. 원칙을 정하는 사람은 오너다.
- **D5 앱 레포 쪽 일.** 앱 레포 세션이 맡는다. fastlane 을 2.236.1 에서 2.242.0 으로 올리는 일도 여기에 든다.
- **결정 202, 203, 204.** 위 표에 적은 대로다.

## 2. 범위: 누가 무엇을 하나

| 쪽 | 하는 일 |
|---|---|
| rcm (이 레포) | 예약 실행 · 종료코드 75 반복(프리셋이 켤 때) · 「사람이 할 일」 표식 · 선택 역할 `status`·`golive` 와 Store 탭 화면 · 원칙 문장 교체 · `rcm check` 규칙 · `rcm-store-connect` 템플릿 뼈대 · 문서 · 릴리스 |
| 앱 레포 세션 | 스토어 REST 를 부르는 `status`·`golive` 스크립트 · Play Console 게시용 Chrome 자동화(전용 프로필, 로그인 확인, 표식 찍기) · Mac mini 에서 Chrome 을 그 프로필로 띄우는 로그인 프리셋 · 프리셋 정본과 프로필 두 줄 · Teams 알림 단계 추가 · 스킬 `store-golive` · fastlane 업데이트 · 기존 스냅샷이 승인 대기를 놓치는 문제 |
| 오너 | Mac mini 의 Chrome 전용 프로필에 처음 한 번 로그인 · Mac mini 화면 공유 켜기(A 길) · `[[notify]]` 훅 등록 · 운영 반영 승인 · 기존 main 의 개인정보 처리 결정(§8) |

rcm 에는 프로젝트 이름도, 경로도, 스토어 이름도 적어 넣지 않는다(「Nothing project-specific in rcm.」). 스토어를
부르는 코드는 rcm 에 없다. 이 경계는 GitHub 백엔드를 버린 결정 30 과 같다.

## 3. 실측과 코드에서 확인한 제약

이 절의 파일:줄은 HEAD `c26ff1a`(2026-11-23) 기준이다.

이 명세가 쓰는 내부 용어를 먼저 풀어 둔다. 레인은 서버가 잡을 동시에 돌리는 자리이고, claim 은 비어 있는 레인이 대기 잡을
하나 집어 가는 일이다. 스냅샷 blob 은 세션이 올린 작업 트리를 내용별로 쪼개 저장한 파일 조각이다. supersede 는 같은
브랜치에 새로 낸 잡이 아직 돌지 않은 옛 잡을 대신하는 기능이다(M5o). ETA 는 큐가 계산하는 예상 완료 시각이다.

요청서에도 실측이 있다. 앱 레포 세션이 11-23 에 읽기 전용 잡 두 개로 확인했고 두 스토어 API 호출은 모두 200 이었다.
iOS 1.2.0 빌드 185 는 `PENDING_DEVELOPER_RELEASE` 와 `releaseType=MANUAL` 이었다. Android production 트랙은
`RELEASE_LIFECYCLE_STATE_IN_REVIEW` 였다. iOS 에는 출시 API(`POST /v1/appStoreVersionReleaseRequests`)가 있다. Play API
에는 관리형 게시로 보류한 릴리스를 게시하는 메서드가 없다. revision 20261119 판의 메서드 149개를 보고 확인했다. 승인
상태 `RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED` 의 설명은 「ready to be published manually by developer」다. 기존
fastlane 스냅샷이 보여 준 Android `completed` 는 「검토를 위해 보냈다」는 뜻이지 게시됐다는 뜻이 아니다.

아래 표는 이 세션이 코드와 Mac mini 에서 확인한 것이다.

| # | 사실 | 근거 | 설계에 주는 영향 |
|---|---|---|---|
| 1 | 종료코드 75 는 이미 `rcm-ci-connect` 템플릿에서 EX_TEMPFAIL(인프라 실패)이라는 뜻이다 | `skills/rcm-ci-connect/templates/rcm_ci_config.py:34,313`, `rcm_ci_job.sh:9,61`, `SKILL.md:65` | 75 반복을 모든 프리셋에 켜면 CI 상태가 늦어진다. 반복은 프리셋이 선언할 때만 켠다(결정 210) |
| 2 | 상태 집합의 뜻이 여러 곳에 퍼져 있다. `ACTIVE_STATES` 에 넣으면 같은 입력의 즉시 실행이 예약에 합류한다(`store.py:1073` `JOINABLE_STATES`). `compute_queue` 는 대기도 실행도 아닌 활성 잡의 행을 말없이 빠뜨린다(`core/queue.py:447-454`). 집합에서 빼면 스냅샷 blob 이 지워질 수 있다(`janitor.py:779-795` 는 `list_active()` 잡만 보호한다) | 위 | `scheduled` 는 활성 집합 밖에 두고 필요한 곳에 명시적으로 넣는다(결정 206) |
| 3 | 대기 중앙값은 `created_at` 부터 잰다(`core/queue.py:137`, `core/status.py:288`). `not_scheduled` 판정은 `queued_at or created_at` 을 쓴다(`core/queue.py:631`) | 위 | 예약 잡은 승격 시각을 `queued_at` 으로 두고 대기를 거기서부터 잰다 |
| 4 | 모르는 상태의 잡을 취소하면 조용히 아무 일도 일어나지 않는다. `request_cancel` 은 None 을 돌려주고(`store.py:3202-3246`) 서버는 200 을(`server.py:4577-4587`), CLI 는 0 을 돌려준다 | 위 | 예약 취소 경로를 명시적으로 만든다 |
| 5 | 옛 클라이언트는 자기 `TERMINAL_STATES` 만 안다(`client.py:30-32,1083,1207`). 옛 서버는 모르는 본문 키를 무시하므로 시각을 붙인 제출을 바로 돌린다 | 위 | `/api/health` 에 기능 키 `schedule` 을 싣고 클라이언트는 그 키가 없으면 거부한다. `shelf`·`reload_dry_run` 과 같은 방식이다(`cli._keep_refusal` 614-630) |
| 6 | `[[notify]]` 에 `on` 이 없으면 기본은 `TERMINAL_STATES` 전부다(`config.py:760`). `notify` 는 reload 로 바꿀 수 없다(`server.py:4593`) | 위 | 새 종료 상태는 `on` 이 없는 규칙에서도 울린다. 행동이 바뀌므로 CHANGELOG 에 적는다. `retried` 는 기본에서 뺀다 |
| 7 | `expired` 는 이미 산출물 묶음의 상태 이름이고 보관함의 사유 이름이다(`core/artifacts.py:47,91`, `core/shelf.py:83`) | 위 | 잡 상태 이름은 요청서 계약대로 `expired` 를 쓴다. 쓰는 필드가 다르고 i18n 키는 이름공간을 나눈다 |
| 8 | 「승인 뒤 출시 금지」 고정 문장이 코드 일곱 곳에 있다: `store.policy`(app.js:5367 목록, :5415 버전 페이지, :5875 상태 화면, index.html:142 대체 문구), `review.policy`(app.js:5776 하단 시트, :6356, :6409 검토 패널), `review.dialog_body`(i18n.js en:981 ko:2073). 문서에도 있다: release-contract.md :7-10 머리말, :374-376 §6, configuration.md :266, README.md :314, README.ko.md :314, rcm-store-connect SKILL.md :421, rcm-connect SKILL.md :165, 템플릿 profile.toml :9 주석과 그 복사본인 골든 픽스처 셋, 와이어프레임 web-store.html :796,918,965, web-version-page.html :264,341. 시험에도 있다: `tests/test_web_browser.py:1446,1811,2297,2916` 의 `"Approval does not release" in body`, `tests/web/store_review.test.js:712-718`. 문서와 화면에 더 있다: `README.ko.md:293`, `docs/wireframes/web-store.html:583,855`, `web-version-page.html:197,413`, `index.html:137`(주석), `rcm-store-connect/SKILL.md:233`. 템플릿 `release_review.sh:309` 의 완료 문장 「Release after approval is a human step in the console」도 바꿀 자리다. `app.js:3090` 은 키로 읽으므로 i18n 만 바꾸면 따라온다 | 탐색 결과 | 요청서가 말한 네 곳보다 많다. 한 PR(F)에서 전부 바꾼다 |
| 9 | 브라우저 시험의 금지 버튼 가드(`tests/test_web_browser.py:1687-1692` `FORBIDDEN_BUTTONS_JS`)는 글자에 출시·배포·게시·publish·rollout·release this version 이 든 버튼을 실패로 잡는다. 이 가드는 약 20번 쓰이고 예외는 `[data-sheet-fix]`·`[data-inputs]` 둘이다. `tests/web/store_review.test.js:113` 은 API 메서드 이름에 `release$\|publish\|rollout` 이 들어가지 못하게 막는다 | 위 | 예외는 `[data-golive]` 하나만 더한다. 그 예외가 다른 곳에서 통하지 않는지 탐침 시험으로 잠근다(결정 218) |
| 10 | 심사를 제출한 초안은 409 `version_closed` 로 닫힌다(`server.py:1774-1782`). 시트는 `closed` 면 조작부를 숨긴다(app.js:2954,5755,5769) | 위 | golive 는 제출된 초안에서도 받는다. 버튼은 시트의 닫힘 블록 밖, 버전 페이지에 둔다(결정 216) |
| 11 | `release_view` 는 역할 프리셋 잡 500개를 한 창으로 읽고 `jobs[]` 를 50개로 자른다(`server.py:1292-1311`, `release_state.py:180-225`) | 위 | 자주 도는 `status` 잡이 옛 plan·review 잡을 창 밖으로 밀어낼 수 있다. 역할마다 최신 잡을 따로 찾도록 고친다 |
| 12 | `rcm check` 는 빈 선택 역할을 warn 으로 친다(`cli.py:3517-3522`). 역할 둘을 더하면 지금 모두 초록인 프로필이 warn 이 된다 | 위 | 요청서대로 warn 으로 둔다(결정 219). 시험 `test_config_release_profile.py:412-424` 를 바꾼다 |
| 13 | 서버의 `unsafe_preset` 가드는 모든 역할 프리셋에 걸린다(`server.py:1243-1251`, 입력 이름 `automatic_release`·`rollout`·`release_status`). `rcm-store-connect` SKILL.md :274 의 grep 은 프리셋 파일 어디에든 `rollout`·`promote`·`auto_release` 낱말이 있으면 FAIL 을 낸다 | 위 | 가드는 그대로 둔다. golive 프리셋의 입력 이름과 설명에는 그 낱말을 쓰지 않는다 |
| 14 | 로컬 워커와 원격 워커는 같은 `outcome_for`(`worker.py:143-220`)를 지난다. 원격 결과는 서버의 `worker_finish`(`remote_workers.py:746-846`)가 한 번 더 판정한다 | 위 | 반복 판정을 서버 한 곳에 두면 원격 워커 프로토콜을 바꾸지 않아도 된다 |
| 15 | 서버에서는 5초 주기의 `_janitor_loop`(`server.py:719-734`, `JANITOR_SECONDS = 5.0` :226)가 이미 돈다. 업로드 포기와 워커 유실을 이 루프가 판정한다 | 위 | 예약 승격과 마감 판정도 이 루프 안에서 한다. 새 스레드도, 새 상주 프로세스도 생기지 않는다 |
| 16 | Mac mini 의 rcm 서버와 워커는 사용자 GUI 도메인의 launchd 에이전트로 돈다(`launchctl print gui/<uid>` 로 확인) | 2026-11-23 | 워커가 띄운 Chrome 이 화면에 뜰 수 있다. 화면 공유(A 길)도 같은 세션을 본다 |
| 17 | 구글은 자동화 도구가 조종하는 브라우저의 로그인을 자주 막는다 | 일반 사실 | 로그인은 사람이 전용 프로필에 한 번 하고 스크립트는 그 세션을 이어 쓴다. 로그인이 풀릴 때를 반드시 대비해야 한다 |

## 4. 설계

소절마다 결정 번호를 단다. 결정 번호의 내용과 오너 확인 여부는 §11 에 모았다.

### 4.1 예약 실행 (F1)

요청서 F1 이다. 정한 시각에 잡을 돌린다. 예약 잡의 상태는 아래처럼 흐른다.

```
제출(start_at) ──▶ scheduled ──(시각 T, 5초 주기 승격)──▶ queued ──▶ running ──▶ …
                      │                                    │
                      ├─ cancelled (취소)                   └─ expired (T + 마감 안에 못 집힘)
                      └─ expired (서버가 꺼져 있다 마감을 넘김 — 시작할 때 판정)
```

| 항목 | 정한 것 | 결정 |
|---|---|---|
| 상태 | `scheduled` 는 새 집합 `SCHEDULED_STATES` 에 든다. `ACTIVE_STATES`·`WAITING_STATES` 에는 넣지 않는다. 대신 blob 보호, 취소, `job_view`, `rcm wait`(비종료 상태로 기다린다), `rcm jobs` 목록에 명시적으로 넣는다. 합류·supersede·ETA 계산의 대상이 아니다 | 206 |
| 합류 안 함 | `start_at`·`start_in_seconds` 가 있는 제출은 `want_join = False` 다. 큐에 같은 입력의 잡이 있거나 그 잡이 돌고 있어도 거기에 붙지 않고 새 `scheduled` 잡을 만든다. 근거는 `server.py:3786-3810` 이다. `:4309-4330` 은 잡을 만들기 전에 `join_or_bump_with_submission` 을 부른다 | 226 |
| 제출 HTTP | `POST /jobs` 에 `start_at`(문자열) 또는 `start_in_seconds`(정수)를 준다. `start_at` 은 오프셋 있는 ISO-8601, 오프셋 없는 `YYYY-MM-DDTHH:MM`, `HH:MM`(오늘) 세 꼴을 받는다. 오프셋이 없으면 서버가 `[display] timezone` 으로 읽고 그 값이 비면 서버 로컬 시간대로 읽는다. 지난 시각은 400 `start_at_past` 다. 내일로 넘기지 않는다. `schedule_max_ahead_days`(기본 14)를 넘어도 400 이다. 응답에는 정해진 UTC 시각과 해석에 쓴 시간대를 싣는다 | 207 |
| 제출 CLI | `rcm run <preset> --at <시각>` · `rcm run <preset> --in 5m`. 클라이언트는 서버 시간대를 모를 수 있으므로 문자열은 서버가 해석한다 | 207 |
| 소스 | `git_ref` 는 지금 규칙대로 제출할 때 sha 를 정한다. `tree` 는 제출할 때 올리고 `uploading → scheduled` 로 간다. 지금은 `mark_uploaded` 가 늘 queued 로 보낸다(`store.py:3003-3026`). tree 업로드를 마치는 경로는 셋이다. `receive_upload`·`receive_blobs`·manifest 이고 자리는 `server.py:4125-4130`, `:4255-4259`, `:4479-4482` 다. 셋 다 `mark_uploaded` 뒤에 `_supersede_after_queue`·`wake` 를 부르고 응답 상태를 `QUEUED` 로 고정한다. 예약 잡이면 세 경로 모두 분기하고 응답 `state` 에 실제 상태를 돌려준다. 클라이언트가 `state` 기본값을 queued 로 채우는 곳(`client.py:991`)도 고친다. 출시 스크립트는 실행할 때 스토어 상태를 다시 읽으므로 sha 가 오래돼도 상관없다 | 206 |
| supersede 끄기 보존 | 제출의 `supersede: false` 는 지금 git_ref 에서는 기록되지 않는다(`server.py:4345-4351`). tree 에서는 메모리 집합 `_no_supersede` 에만 있다. 그래서 서버를 재시작하면 이 값을 잊고 승격할 때 옛 잡을 치운다. 이 값을 DB v25 의 열로 얼린다. 얼린다는 것은 제출할 때 값을 잡 행에 적어 두고 그 뒤로 바꾸지 않는다는 뜻이다 | 237 |
| 승격 | `_janitor_loop` 가 `not_before ≤ now` 인 예약을 `queued` 로 올리고 `queued_at = now` 로 둔다. 조건부 UPDATE 라서 한 번만 오른다. 올린 뒤 `_publish_job`·`_supersede_after_queue`·`wake` 를 부른다. 서버를 켤 때는 `recover_on_start` 가 마감을 넘긴 예약을 `expired` 로 닫는다. 시각은 지났지만 마감 안에 있는 예약은 승격한다 | 206 |
| 시작 마감 | 서버 기본값은 `[server] schedule_start_deadline_seconds = 900`(15분)이고 reload 로 못 바꾸는 절에 둔다. 프리셋의 `start_deadline_seconds` 가 이 값을 덮고 reload 된다. 제출은 더 짧게만 줄 수 있다. 마감은 T 에 이 값을 더한 시각이다. 레인이 바빠 큐에서 기다리다 마감을 넘긴 잡도 똑같이 다룬다. claim 은 마감이 지난 잡을 집지 않고 주기 판정이 그 잡을 `expired` 로 닫는다. 서버가 꺼져 있는 동안 마감을 넘긴 예약은 `recover_on_start`(`store.py:3414-3460`)가 워커가 뜨기 전에 닫는다. claim 의 마감 필터는 SQL 두 갈래에 모두 넣는다. `store.py:3080` 의 옛 경로와 `_claimable`(`:3130`)이다. 시각은 트랜잭션 안에서 다시 읽는다. 지금은 admit 판정 전에 받은 `now` 를 쓴다. 정지(pause) 중에도 마감 시계는 흐른다. 배포 절차(정지 → 도는 잡 0 → 재시작)가 예약을 만료시킬 수 있으므로 이 점을 `docs/operating.md` 에 적는다. 반복 시도에는 시작 마감을 적용하지 않는다(결정 231, 4.2). 템플릿의 golive 프리셋은 `start_deadline_seconds = 3600` 을 권한다. lanes=2 에서 30~40분짜리 게이트 둘이 돌고 있으면 15:00 예약이 15분 안에 레인을 받지 못하기 때문이다(게이트 시간은 2026-10-21 운영 실측). 서버 기본 900 은 그대로 둔다 | 208, 231 |
| 우선순위 | 승격할 때 제출 때 정해진 잡의 우선순위(admin 의 `--priority` 포함)를 그대로 쓴다. 템플릿의 golive 프리셋은 `priority = "high"` 를 권한다 | 208 |
| 중복(일반 예약) | `join_key`(프리셋 + 입력 + 소스 신원, `core/queue.py:107`)가 같은 `scheduled` 잡이 이미 있으면 409 `schedule_exists` 를 돌려주고 본문에 `job_id` 를 싣는다. 판정과 삽입은 `BEGIN IMMEDIATE` 하나 안에서 한다(`_join_tx` 와 같은 방식). 소스를 비교하지 않으면 브랜치 A 와 브랜치 B 에 같은 입력의 야간 gate 를 걸 때 둘째가 막힌다. 그래서 요청서 R6 의 「프리셋과 입력」에 소스 신원을 더했다 | 209, 227 |
| 중복(golive) | golive 는 4.5 의 409 `golive_running` 이 더 넓게 막는다 | 227, 232 |
| 시각 경계 | 오프셋 없는 시각이 서머타임 경계에 걸리면 400 `start_at_ambiguous` 다. 없는 시각과 두 번 오는 시각이 그런 경우다 | — |
| bump · eta | `rcm bump` 는 예약 잡에도 쓸 수 있다. `set_priority` 가 받는 상태를 `scheduled` 까지 넓힌다(`store.py:2719`). `rcm eta <id>` 는 예약 잡에 「scheduled for T」를 답하고 ETA 숫자를 지어내지 않는다. 새 제출의 ETA 는 곧 승격될 예약을 세지 않는다(`queue.py:722`). 이 한계를 문서에 적는다 | — |
| 워커 잊기 | `forget_workers`(`store.py:4141-4155`)가 예약 잡도 본다. 예약 잡을 보지 않으면 14일 뒤에 예약이 있는 풀의 워커가 잊혀 `pools_without_workers` 경보가 꺼지지 않는다 | — |
| 이유 이름 | 기존 이유 코드 `not_scheduled`(「빈 레인이 집지 않았다」, `model.py:95`, `i18n.js:448`)는 코드를 그대로 두고 화면 문구만 바꾼다. en 은 「idle lane did not pick it up」, ko 는 「빈 레인이 집지 않음」이다. 새 상태 `scheduled` 와 화면에서 헷갈리지 않게 하려는 것이다 | 238 |
| 권한 | 즉시 실행과 같은 토큰 규칙을 쓴다. 릴리스 경로의 되돌릴 수 없는 모드는 admin 토큰이 있어야 한다 | — |
| 취소 | `scheduled` 잡은 바로 `cancelled` 가 된다 | — |
| reload | 예약은 DB 에 있으므로 그대로 남는다. 승격한 뒤에 프리셋이 없으면 지금 claim 규칙대로 `failed` + `preset_missing` 이다 | — |
| 표시 | `/api/status` 에 새 키 `pools[].scheduled[]` 를 둔다. 시각순이고 큐 표와 따로 간다. `rcm jobs` 에 `scheduled` 행과 `--state scheduled` 를 더한다. `rcm top` 에는 한 줄을 더한다. `rcm wait` 는 stderr 에 「scheduled for 15:00」을 찍고 기다린다. `pools[].scheduled[]` 에는 기존 관례대로 `scheduled_error`·`scheduled_error_code` 를 둔다. 스키마 잠금 시험 `tests/test_status_schema.py:273-277` 의 `POOL_KEYS`·`ROW_KEYS`·`RECENT_KEYS` 를 바꾼다 | 206 |
| 종료코드 | `rcm wait` 에서 `expired` 는 2 다. 잡이 돌지 않았다는 뜻이고 `cancelled` 와 같은 줄에 선다 | 212 |
| 기능 알림 | `/api/health` 에 `schedule: true` 를 싣는다. 이 키가 없으면 클라이언트가 `--at` 을 거부하고 종료 2 로 끝난다(「server does not support scheduling — upgrade it」) | 206 |
| 알림 | `[[notify]]` 의 `on` 에 `expired` 를 쓸 수 있다. `on` 이 없는 규칙에서도 울린다. 행동이 바뀌는 곳이다. 기본 `on` 은 두 곳(`config.py:760`, `core/notify.py:28`)에 있으므로 둘 다 고친다 | — |
| 보존 | 끝난 예약은 기존 보존 규칙을 따른다. 비성공 잡의 로그는 30일, 메타데이터는 180일이다. 요청서가 제안한 7일 규칙은 따로 만들지 않는다 | 220 |

### 4.2 종료코드 75 반복 (R5)

요청서 R5 다. 프로젝트 스크립트가 종료코드 75 로 끝나면 정한 간격 뒤에 다시 돌린다.

| 항목 | 정한 것 | 결정 |
|---|---|---|
| 켜는 법 | 프리셋의 `[presets.retry]` 표에 `on_exit = 75`, `interval_seconds = 600`, `max_attempts = 12` 를 둔다. 요청서가 제안한 기본값이고 합치면 2시간이다. 선언이 없으면 75 는 지금처럼 `failed` 다(§3 #1, rcm-ci 보호). 제출 본문에 `retry: false` 를 주면 그 잡만 반복을 끌 수 있다. 반복을 켜는 것은 프리셋만 한다. 요청서는 「잡을 걸 때 고르는 선택」을 제안했는데 이렇게 바꿨다 | 210 |
| 소스 제한 | 반복 프리셋은 `source_modes = ["git_ref"]` 만 허용하고 다른 값은 설정 오류로 막는다. tree 스냅샷을 시도마다 복사하지 않으려는 것이다 | 211 |
| 기제 | 시도마다 새 잡 행을 만든다. 75 로 끝난 시도는 새 종료 상태 `retried` 로 닫는다. 실패가 아니므로 실패 통계, 실패 이름, 알림 기본값에서 빠진다. 다음 시도는 같은 입력과 같은 sha 로 `scheduled` 상태에 만든다(`not_before = now + interval`, `retry_of`, `attempt`). 마지막 시도가 75 면 그 잡은 `exhausted` 로 닫는다. `retried` 전이는 `only_from=(RUNNING,)` 로만 한다. 그 사이에 취소가 들어왔으면 `cancelled` 로 닫고 다음 시도를 만들지 않는다. 지금은 로컬(`worker.py:578-610`)과 원격(`remote_workers.py:830`)이 모두 `only_from` 없이 finish 한다. 다음 시도는 `retried` 로 닫는 같은 `store.finish` 트랜잭션 안에서 만든다. 그래서 `retried_as` 없는 `retried` 는 생기지 않는다. 그런 잡이 생겼다면 `rcm wait` 는 3(모른다)으로 끝난다 | 211, 225 |
| 따라가기 | `rcm wait`·`rcm run` 은 `retried` 를 만나면 `retried_as` 를 따라간다. 이것은 새로 만드는 동작이다. supersede 는 따라가지 않는다(`cli.py:1230-1235`, 결정 114). 최종 JSON 의 `job_id` 는 마지막 시도이고 `chain_id`(뿌리 잡 id)와 `attempts[]`(시도 id·상태·종료코드)를 싣는다. 사슬은 뿌리 잡에서 이어진 시도들이다. `--timeout` 은 사슬 전체에 건다. `--fetch-artifacts` 는 마지막 시도의 묶음을 받는다. 지금 `cli.py:1149-1160` 은 원래 id 로 받는다. 보관 줄도 마지막 시도를 기준으로 한다 | 211, 228 |
| 취소 | 잡 행에 `chain_id`(뿌리 잡 id) 열을 둔다. 취소 요청이 사슬의 어느 id 로 오든 서버가 살아 있는 머리 시도로 돌린다. 머리 시도는 사슬의 끝에서 아직 끝나지 않은 시도다. capability 검사는 뿌리 id 로 한다. 지금 `submission_role` 은 잡 번호가 다르면 None 을 돌려준다(`store.py:2923-2927`). 클라이언트 상태 파일(`cli.py:288-330`)도 뿌리 id 로 찾는다. 그래서 `retried` 로 끝난 첫 잡에 `rcm cancel <첫 id>` 를 보내도 409 「already finished」(`server.py:4580`)가 나지 않는다 | 211, 228 |
| 넘기는 것 | 다음 시도로 넘기는 열은 입력 · sha · 잡 우선순위(합류로 오른 값 포함) · `link` · `target_worker`·`placement`·`pool` · 얼린 반복 정책 · `no_supersede` · `chain_id` 다. 합류자 행과 `join_count` 도 옮긴다. `keep` 은 넘기지만 보관(shelf 행과 묶음)은 사슬의 마지막 시도에서만 만든다. 시도마다 묶음이 생겨 12개가 쌓이지 않게 하려는 것이다(`_upsert_bundle`) | 228 |
| 정책 얼리기 | 반복 정책(`on_exit`·`interval_seconds`·`max_attempts`)과 제출의 `retry: false` 는 제출할 때 잡 행의 열로 얼린다. 잡이 끝날 때 프리셋을 다시 읽지 않는다. `outcome_for` 는 잡만 받는다(`worker.py:143`). 얼려 두므로 reload 나 프리셋 삭제가 사슬 도중에 동작을 바꾸지 않는다 | 229 |
| 시작 마감 | 반복 시도에는 시작 마감을 적용하지 않는다. 레인이나 그룹을 기다려도 `expired` 가 되지 않고 사슬은 `max_attempts` 로만 끝난다. 이유는 A 길에 있다. 사람이 로그인하는 동안 로그인 프리셋이 공용 `concurrency_group` 을 차지하고 있어서 다음 시도는 `blocked_by_group` 으로 기다린다. 여기에 마감을 걸면 15분 뒤 사슬이 `expired` 로 끝나 A 길이 성립하지 않는다. 로그인 프리셋은 사람이 Chrome 을 닫거나 자기 `timeout_seconds`(템플릿 권장 1800)가 지나면 끝나고 그룹을 내준다 | 231 |
| 옛 클라이언트 | 업그레이드하지 않은 클라이언트의 `rcm wait` 는 `retried` 를 모르므로 끝없이 폴링한다(`client.py:1083`, `:1207`). 그래서 반복을 켠 프리셋이 하나라도 있으면 서버가 health 의 `min_client_version` 을 0.10.0 으로 올린다. 그러면 옛 `rcm check` 가 FAIL 을 그린다. M5q 마일스톤에서 들인 클라이언트 버전 따라가기를 쓰는 것이다. 이 변화는 CHANGELOG 에 적는다 | 230 |
| 목록 | `retried` 시도는 `recent`(기본 8개, `config.py:109`)에 넣지 않는다. 다른 결과를 밀어내지 않게 하려는 것이다. 사슬은 마지막 시도 한 행으로 보이고 시도 수를 같이 적는다. `keep_workspace_on_failure` 는 `retried` 시도의 워크스페이스를 남기지 않는다 | — |
| 판정 자리 | 로컬 `finish` 와 원격 `worker_finish` 가 만나는 서버 한 곳에서 판정한다. 원격 워커 프로토콜은 그대로 둔다 | 211 |
| 종료코드 | `rcm wait` 에서 `exhausted` 는 1 이다. 끝내 확인하지 못했으니 사람이 봐야 한다는 뜻이다. `retried` 에서는 기다림이 끝나지 않는다 | 212 |
| 알림 | `on` 에 `exhausted`·`retried` 를 쓸 수 있다. `on` 이 없는 기본에는 `exhausted` 가 들고 `retried` 는 빠진다 | 211 |
| 표본 | `retried` 시도는 소요 시간 표본에서 뺀다 | 211 |
| 중복과의 관계 | 다음 시도도 `scheduled` 이므로 같은 입력으로 새 예약을 걸면 409 `schedule_exists` 다. 다만 `schedule_exists` 는 `scheduled` 끼리만 보므로 시도가 도는 동안에는 같은 입력이 통과하고 즉시 실행은 검사받지 않는다. golive 의 출시 중복은 4.5 의 409 `golive_running` 이 막는다 | 209, 232 |
| 멱등성 | 다시 실행해도 결과가 같게 만드는 일은 프로젝트 스크립트가 맡는다(요청서 R5) | — |

### 4.3 「사람이 할 일」 표식 (로그인 풀림)

로그인이 풀린 때처럼 사람이 손대야 하는 순간을 잡이 rcm 에 알리는 표식이다.

| 항목 | 정한 것 | 결정 |
|---|---|---|
| 표식 | 새 마커 `::rcm::action::<code>::<note>` 를 둔다. `code` 는 `[a-z0-9_-]{1,32}` 이고 `note` 는 200자까지다. 프로젝트 이름이 들어가지 않는 일반 기능이다. 예: `::rcm::action::login::<안내 문장>`. `note` 에는 `clean_name` 과 같은 정리를 적용해 제어문자를 지운다. 지금 summary 는 `progress.py:172` 에서 잘리기만 한다. note 는 토큰 없이 읽히는 `/api/status` 와 알림 env, 터미널로 흐른다. 그래서 note 에 계정 이메일 같은 개인정보를 넣지 말라는 규칙을 템플릿 머리말에 둔다. 마커 종류 잠금 시험 `test_progress_m5h.py:122`(`MARKER_KINDS`)를 바꾼다 | 213 |
| 잡에 남는 것 | 마지막 표식을 `action {code, note, at}` 으로 남긴다. 이벤트로 저장하므로 잡이 끝나도 남는다 | 213 |
| 어디에 보이나 | 큐 행과 예약 행의 이유 칸, 화면 요약 「Needs a look」(새 actionable reason `needs_action`), `rcm wait` 의 stderr 한 줄과 최종 JSON 의 `action`, `[[notify]]` env `RCM_ACTION_CODE`·`RCM_ACTION_NOTE`, Store 탭의 golive 결과 | 213 |
| 반복과 함께 | 스크립트는 로그인이 없으면 표식을 찍고 75 로 끝난다. 반복이 10분마다 다시 확인한다. 표식은 75 로 끝난 시도의 이벤트에 있고 다음 시도(`scheduled`)에는 없다. `markers_for` 는 BUSY 잡에만 불린다(`server.py:3121`). 그래서 규칙을 이렇게 정한다. 사슬에서 가장 최근에 돈 시도의 마지막 `action` 을 사슬 전체의 표식으로 쓴다. 표식 없이 끝까지 돈 시도가 나오면 표식을 지운다. 예약 행에도 이유를 계산하므로 시도 사이를 기다리는 동안에도 `needs_action` 이 「Needs a look」에 남는다 | 213, 233 |
| 중간에 돌아오기 | 에이전트 세션은 명령 출력을 대개 명령이 끝날 때 받는다. 그래서 `rcm wait --return-on-action` 을 둔다. 새 표식이 오면 최종 JSON(`action` 포함)을 찍고 종료코드 4 로 돌아온다. 잡은 계속 돈다. 4 는 이 플래그를 줄 때만 나온다. 스킬은 4 를 받으면 사람에게 A 길과 B 길 중 어느 쪽인지 묻고 다시 `rcm wait --job <chain_id>` 로 기다린다 | 234 |
| 중간 알림 | rcm 은 잡 도중에 알림을 만들지 않는다. Teams 알림은 프로젝트 스크립트가 직접 보낸다(앱 레포의 기존 알림 스크립트) | 214 |
| A 길(기본) | 사람이 Mac mini 에 화면 공유로 들어가 앱 레포의 로그인 프리셋이 띄운 Chrome(전용 프로필)에서 로그인한다. 그러면 다음 시도가 게시하고 0 으로 끝난다 | 203 |
| B 길(대안) | 요청한 세션의 스킬은 `rcm wait --return-on-action` 이 돌려준 `action.code == login` 을 보면 사람에게 두 길 가운데 어느 쪽인지 묻는다. 사람이 B 를 고르면 그 컴퓨터의 Chrome 으로 게시한다. 다음 시도가 Play API 로 `PUBLISHED` 를 확인하고 0 으로 끝난다. 위의 「중간에 돌아오기」 말고 rcm 에 더할 기능은 없다 | 203 |
| 게시 판정 | 게시됐는지는 화면이 아니라 스토어 API 를 읽어 판정한다. Android 는 `RELEASE_LIFECYCLE_STATE_PUBLISHED`, iOS 는 `READY_FOR_DISTRIBUTION` 이다. 버튼을 못 찾으면 스크린샷을 남기고 실패(1)로 끝낸다 | 205 |
| 브라우저 겹침 | 템플릿의 golive 프리셋과 로그인 프리셋은 같은 `concurrency_group` 을 쓴다. 그래서 Chrome 두 개가 한 프로필을 동시에 열지 않는다 | 205 |
| 프로필 위치 | Chrome 전용 프로필 폴더는 잡 워크스페이스가 아니라 비밀 폴더 쪽에 둔다. 워크스페이스는 잡이 끝나면 지워지기 때문이다. 경로는 프로필과 프로젝트 스크립트가 정한다 | 205 |

### 4.4 `status` 역할 (F2)

요청서 F2 다. 두 스토어의 심사 상태를 읽어 오는 선택 역할이다.

| 항목 | 정한 것 |
|---|---|
| 역할 등록 | `config.RELEASE_ROLES`(:199)와 `release_state.LISTED_ROLES`(:38)에 `status`·`golive` 를 선택 역할로 더한다. `RELEASE_REQUIRED_ROLES` 는 그대로 둔다. `release_state.ROLE_FILES`(:31-36)에 `status: ("status.json",)` 와 `golive: ("golive-plan.json", "golive.json")` 를 더한다 |
| 입력 | `build_name`. 비우면 최근 버전들을 읽는다 |
| 산출물 `status.json` 최소 필드 | 최상위 `schema`, `checked_at` · `ios{version, build, state, raw_state, release_type, phased}`(`phased` 는 `{state, day}` 이거나 null) · `android{version, version_code, state, raw_state, user_fraction}` |
| `state` 8값 | `preparing` · `waiting_for_review` · `in_review` · `approved` · `rejected` · `releasing` · `released` · `unknown`. 정규화는 프로젝트 스크립트가 하고 rcm 은 그리기만 한다. 목록에 없는 값은 `unknown` 으로 그린다 |
| `GET …/release` | `status{job_id, state, measured_at, age_seconds, stale, doc}` 를 더한다. 키가 늘어나므로 본문을 정확히 비교하는 시험(`test_server_release_routes.py:328-353`)을 바꾼다 |
| 창 문제 | 역할마다 최신 잡을 따로 찾도록 고친다(§3 #11) |
| `POST …/release/status` | 클라이언트 토큰으로 부른다. 정규 `POST /jobs` 경로로 잡을 낸다(`_release_submit`, `server.py:1318-1340`). 라우터 `_RELEASE_RE`(:251-254)와 클라이언트 토큰 분기(:5550-5559)에 더한다 |
| 화면 | 검토 패널(`reviewPanelHtml`, app.js:6343-6411)에 두 스토어의 상태를 읽기 전용으로 그리고 「새로 조회」 버튼을 둔다. 웹 상태의 이름은 `state.store.releaseStatus`(HTTP 코드)와 겹치지 않게 짓는다 |
| 계약 | `rcm_contract.py` 의 `KINDS`(:36)에 `status` 를 더하고 검사한다 |

### 4.5 `golive` 역할과 원칙 문장 (F3)

요청서 F3 이다. 승인된 빌드를 사람이 요청한 때에 출시하는 선택 역할이다. 출시 원칙을 말하는 고정 문장도 이 소절에서
바꾼다.

| 항목 | 정한 것 | 결정 |
|---|---|---|
| 입력 | `build_name` · `build_name_android` · `confirm_build_number` · `platform`(ios·android·both) · `mode`(plan 기본, release) | — |
| `golive-plan.json` | `schema` · `build_name` · `measured_at` · `platforms.ios{approved, approved_build, raw_state}` · `platforms.android{approved, approved_build(version code), raw_state}` · `plan_verdict`(ok·blocked) · `reasons[]`. review 역할의 plan 과 같은 방식으로 만든다 | — |
| `golive.json` | `schema` · `build_name` · `platforms.ios{result, raw_state}`(requested·released·pending·failed) · `platforms.android{result, raw_state}`(published·pending·needs_login·failed) · `finished_at` | — |
| 종료코드(plan 모드) | 스토어를 읽었으면 0, 읽지 못했으면 1 이다. 승인 여부는 종료코드가 아니라 `platforms.<p>.approved` 에만 적는다. plan 이 Android 미승인에서 2 로 끝나면 잡이 `failed` 가 된다(`worker.py:186-188`). 서버의 문은 `succeeded` 가 아닌 plan 을 `golive_plan_required` 로 막는다(`server.py:1356-1362`). 그러면 iOS 만 출시하는 길이 막힌다. 11-23 실측 상태가 바로 그 경우다(iOS 승인 대기, Android 심사 중) | 224 |
| 종료코드(release 모드) | 0 은 끝, 75 는 아직, 1 은 실패, 2 는 막힘, 3 은 번호 불일치다. 고른 플랫폼이 승인 상태가 아니면 언제나 2 다. 75 는 승인된 뒤 남은 일에만 쓴다. 로그인 대기, iOS 출시 처리 중, 게시 확인 대기가 그런 일이다. 75 로 승인을 기다리면 승인을 감시하는 자동 출시가 되어 결정 201 을 어긴다. 로그인이 풀리면 표식을 찍고 75 로 끝난다. plan 모드와 release 모드의 두 규칙은 템플릿 `--selftest` 로 잠근다 | 236 |
| `POST …/release/golive` | 본문은 `{mode, build_name 또는 version_id, confirm_build_number, platform, start_at·start_in_seconds}` 다. `mode = plan` 은 클라이언트 토큰으로 부른다. 화면은 「출시 예약」 시트를 열 때 plan 을 먼저 돌린다. CLI 와 세션도 plan 을 먼저 돌린 뒤 예약을 건다 | — |
| 서버가 막는 것 | admin 이 아니면 403 `admin_required` 다(review 의 관례). 409 는 이렇다. `golive_plan_required` 는 같은 build_name 으로 성공한 golive-plan 이 없을 때다. `golive_plan_stale` 은 plan 이 `plan_max_age_minutes`(기본 30분)보다 오래됐을 때다. 이 검사는 예약을 거는 순간에만 한다. 그래서 오전 10시에 건 오후 3시 예약이 실행할 때 막히지 않는다. `golive_plan_blocked` 는 고른 플랫폼이 승인 상태가 아닐 때다. 이 판정은 고른 플랫폼의 `approved` 만 본다. `build_number_mismatch` 는 고른 플랫폼의 `approved_build` 와 번호가 다를 때다. `platform = both` 면 친 번호가 두 플랫폼의 `approved_build` 와 모두 같아야 한다. 두 플랫폼의 `approved_build` 가 서로 다르면 409 `build_number_mismatch` 이고 응답에 플랫폼별로 따로 걸라고 적는다. `plan.json` 의 `n` 과는 비교하지 않는다. `auto` 는 받지 않고 사람이 친 번호만 받는다. 나머지는 `schedule_exists` · `unsafe_preset` · `role_not_configured` · `setup_incomplete` 다. 새 409 `golive_running` 은 같은 build_name 의 golive 사슬이 살아 있으면(`scheduled` ∪ 활성) 막는다(결정 232). 즉시 실행도 똑같이 막는다. `schedule_exists` 는 `scheduled` 끼리만 보므로 이것만으로는 시도가 도는 동안 같은 입력이 통과하고 즉시 실행은 아예 검사받지 않는다. plan 모드에 `start_at` 이 오면 400 이다 | 215, 232 |
| 일반 경로 막기 | 지금 `POST /jobs` 의 `submit` 은 역할 프리셋이나 되돌릴 수 없는 모드를 막지 않는다. 클라이언트 토큰으로 `rcm run <golive 프리셋> -f mode=release --at 15:00` 을 보내면 admin·plan·번호 검사를 모두 건너뛴다. 그래서 `POST /jobs` 는 golive 역할 프리셋의 `mode = release` 만 누가 보내든 409 `release_route_required` 로 거절한다. golive 는 새 역할이라 기존 사용자가 없다. review submit 과 upload upload 가 `POST /jobs` 로 문을 우회하는 틈은 이번 범위 밖의 후속으로 남긴다. 지금 드라이버 템플릿이 `mode=upload` 를 `rcm run --source git_ref` 로 내기 때문이다(`skills/rcm-release-driver/templates/release_driver.sh:169-175`, `:350`). 이 경로는 곧 `POST /jobs` 이고 실행마다 발급하는 client 토큰을 쓴다. 그래서 그 틈은 드라이버를 라우트로 옮기는 일과 함께 따로 다룬다. CLI 에는 `rcm release golive <repo> --build-name … --platform … [--confirm-build-number N] [--at …] [--plan]` 와 `rcm release status <repo> [--build-name …]` 를 새로 둔다. 지금 `rcm release` 에는 new·list·delete·open 만 있다(`cli.py:4818-4838`) | 235 |
| 제출된 초안 | golive 는 `version_closed` 를 `submitted` 초안에서만 풀고 `discarded` 초안은 계속 막는다(`server.py:1946-1955`). 그래서 심사를 제출한 초안에서도 받는다 | 216 |
| 제출 전달 | `_release_submit`(`server.py:1318-1340`)은 본문을 새로 만들고 응답을 줄인다. 그래서 `start_at`·`start_in_seconds` 를 넘기도록 고친다. 응답에는 `submission`(취소 비밀)을 싣고 201 과 409 의 구분을 살린다 | — |
| 화면 | 버전 페이지에 「출시 예약」을 둔다. `status` 문서에서 그 플랫폼의 `state` 가 `approved` 일 때만 열린다. 한 플랫폼만 승인됐으면 그 플랫폼만 고를 수 있고 `both` 는 둘 다 승인됐을 때만 고를 수 있다. 시트에서는 지금 돌릴지 시각을 정할지, 빌드 번호, 예약 목록과 취소, 결과(`golive.json`)와 표식을 본다. 버튼은 `data-golive` 로 표시한다. 금지 버튼 가드는 속성만 본다(`test_web_browser.py:1687-1692`). 그래서 예외 조건을 `[data-golive]` 이면서 버전 페이지의 출시 예약 영역 안(`closest('[data-golive-sheet]')`)에 있을 때로 묶는다. 탐침 시험은 같은 속성을 다른 자리에 붙인 버튼이 여전히 걸리는지 본다 | 216, 218 |
| 원칙 문장 | 계약 머리말과 §6 첫 규칙, 화면의 고정 문장을 바꾼다. 문안(영어/한국어): en 「Nothing goes live on approval by itself. A release runs only when a person asks for it with the version, the build number and a time.」 ko 「승인됐다고 저절로 출시되지 않습니다. 출시는 사람이 버전과 빌드 번호와 시각을 적어 요청할 때만 돕니다.」 Android 고정 문장: en 「Play has no API to publish a held release, so the project's go-live script publishes through a browser or asks a person.」 ko 「Play 에는 보류한 출시를 게시하는 API 가 없습니다. 그래서 프로젝트의 출시 스크립트가 브라우저로 게시하거나 사람에게 요청합니다.」 §3 #8 의 모든 자리를 한 PR 에서 바꾼다 | 217 |
| 가드 | `unsafe_preset` 는 그대로 둔다. 이 가드는 이미 모든 역할에 걸리므로 golive 도 검사 대상이다. golive·status 의 입력 이름에 `automatic_release`·`rollout`·`release_status` 를 쓰지 않는다 | — |
| 나중의 B안 | 요청서 11절의 Android 안은 막지 않는다. 작은 비율로 심사한 뒤 사람이 한 번 게시하고 정한 시각에 100% 로 올리는 안이다. 오너는 이 안을 아직 정하지 않았다. 플랫폼별 동작은 스크립트가 맡고 입력은 나중에 하나 더할 수 있다 | — |

### 4.6 `rcm check` · 템플릿 · 스킬 (F4)

요청서 F4 다.

| 항목 | 정한 것 |
|---|---|
| `rcm check` | `RELEASE_ROLE_INPUTS`(cli.py:3462-3475)에 `status: build_name` 과 `golive: build_name · confirm_build_number · platform · mode` 를 더한다. `RELEASE_IRREVERSIBLE_MODE`(:3477)에 `golive: release` 를 더하고 기본값이 release 면 FAIL 이다. 필수 입력이 없어도 FAIL 이다. `build_name_android` 가 없으면 warn 이다(두 스토어가 같은 버전 이름을 써야 한다). `status`·`golive` 가 비어 있으면 선택 역할 warn 이다. 역할 목록의 오류 문구는 튜플에서 만들어지므로 저절로 바뀐다(config.py:1633-1638) |
| 템플릿 | `rcm-store-connect/templates/` 에 `release_status.sh`·`release_golive.sh` 뼈대를 둔다. 머리말에는 입력·종료코드·안전 규칙을 적는다. bash 5 `SECRETS_ENV` 가드, `TODO(project)` 훅(스토어 조회 · iOS 출시 요청 · Android 게시 훅과 로그인 확인 · 표식 찍기), `--selftest` 를 넣는다. `presets.release.toml` 에 프리셋 둘을 둔다. golive 프리셋은 git_ref · `priority = "high"` · `[presets.retry]` · 공용 `concurrency_group` · `artifacts_on = "always"` 다. `profile.toml` 에 역할 두 줄을 더한다. `rcm_contract.py` 의 `KINDS` 에 `status`·`golive-plan`·`golive` 를 더한다 |
| 시험 | `tests/test_skills_templates.py:50-68` 의 selftest 목록에 두 스크립트를 더한다. `tests/test_packaging.py:57-70` 은 `templates/{server,client,worker}.toml` 과 `examples/` 를 비교한다. 이 바이트 비교의 대상은 `server.toml` 에 더할 `schedule_start_deadline_seconds` 이고 `rcm-store-connect` 템플릿과는 관계없다. 템플릿 `presets.release.toml` 의 golive·status 프리셋은 B 가 아니라 F 에서 더한다(§5). 프리셋 키 검사가 엄격해서(`config.py:623` `_PRESET_KEYS`) `[presets.retry]`·`start_deadline_seconds` 가 들어오기 전에는 템플릿과 골든 시험(`test_rcm_candidate.py`)이 깨지기 때문이다 |
| 문서 | 계약 §2 표에 두 행, §3 에 `action` 마커, §6 에 바뀐 첫 규칙을 적는다. configuration.md 에는 409 표와 역할 목록 문장(:320-321)을 고친다. 그 문장은 `test_skills_templates.py:517` 의 정규식이 잠근다 |

### 4.7 상주 구조 (요청서 R9 표)

요청서 R9 가 정한 칸에 이 설계의 답을 채운 표다.

| 칸 | 값 |
|---|---|
| 확인 주기 | 5초다. 기존 `_janitor_loop` 안에서 승격과 마감 판정을 한다. 새 스레드도, 새 프로세스도, 새 launchd 항목도 없다 |
| 동시 실행 방지 | DB 를 여는 서버는 하나다. 승격과 만료는 `state = 'scheduled'` 와 마감 조건을 건 조건부 UPDATE 를 `BEGIN IMMEDIATE` 안에서 하므로 한 번만 일어난다. claim 도 마감이 지난 잡을 집지 않는다 |
| 고아 프로세스 | 예약 잡에는 프로세스가 없고 DB 행만 있다. 서버가 꺼져 있다가 켜지면 `recover_on_start` 가 마감을 넘긴 예약을 워커가 뜨기 전에 `expired` 로 닫는다. 도는 시도의 프로세스는 지금 규칙을 따라 재시작하면 `lost` 가 된다 |
| 끝난 예약 기록 정리 | 기존 보존 규칙을 따른다. 비성공 잡의 로그는 30일, 메타데이터는 180일 뒤에 janitor 가 지운다(결정 220) |
| 실패를 누가 어떻게 아나 | `expired`·`exhausted` 는 `job_finished` 를 내므로 `[[notify]]` 가 울린다. 알림이 실패하면 다시 시도하지 않고 `server.notify_failures` 로 센다. 이 값은 지금 `/api/status` 에만 실리고(`core/status.py:378`) `cli.py`·`app.js` 에는 쓰는 곳이 없다. 메모리 카운터라서 재시작하면 0 이 된다. 그래서 C 가 `rcm check` 에 이 값의 행(warn)을 더하고 G 가 웹 머리에 배지를 더한다. 재시작하면 0 이 되는 한계는 문서에 적는다. 기다리던 세션은 `rcm wait` 의 종료코드(2·1)로 바로 안다. 스킬은 `rcm jobs --state expired` 로 다시 볼 수 있다. 다만 `rcm jobs` 는 `/api/status` 의 `recent`(기본 8개) 안에서만 찾는다 |

## 5. 작업 분할과 병렬

오너는 병렬로 할 수 있는 일은 반드시 병렬로 하라고 지시했다. 규칙은 이렇다. PR 하나에 브랜치 하나, 워크트리 하나를
쓴다. 워크트리는 `../remote_ci_monitor-<what-it-does>` 이고 자기 `.venv` 를 둔다. 같은 물결(wave)에 든 PR 은 동시에
띄운다. 앞 물결의 의존 PR 이 dev 에 머지되면 다음 물결로 넘어간다.

| PR | 브랜치 | 내용 | 시작 조건(머지된 PR) | 물결 | 같이 도는 것 | 겹치는 파일과 머지 순서 |
|---|---|---|---|---|---|---|
| P0 | `docs/m5s-store-golive-workplan` | 이 명세 + 격리 설계 반박 반영 | — | 0 | 앱 레포 세션 인계 | — |
| A | `feat/m5s-scheduled-jobs` | 4.1 서버·저장소·CLI·`/api/status`·health, DB v25 에 이번 마일스톤의 열 전부(표 아래). 웹은 빼고 | P0 | 1 | B, C | C 와 `core/status.py`·`cli.py`(wait)가 겹친다. A 를 먼저 머지하고 C 가 다시 맞춘다. 공유 파일은 표 아래 |
| B | `feat/m5s-release-status-golive-roles` | 4.4·4.6 의 역할 등록·`release_state`·`rcm check`·`rcm_contract.py` KINDS·스크립트 뼈대 두 개·계약 §2. 라우트와 `presets.release.toml`·`profile.toml` 의 프리셋·역할 추가는 빼고(F) | P0 | 1 | A, C | `config.py` 를 A 와 다른 자리에서 고친다 |
| C | `feat/m5s-action-marker` | 4.3 마커 파싱·저장·잡 JSON 의 `action`·`rcm wait`(`--return-on-action` 포함)·notify env·`rcm check` 의 `notify_failures` 행. 예약 행의 이유와 사슬 기준 표식은 빼고(D·G), 웹도 빼고 | P0 | 1 | A, B | A 와 `core/status.py`·`cli.py`(wait)가 겹친다. A 를 먼저 머지하고 C 가 다시 맞춘다 |
| D | `feat/m5s-retry-on-exit` | 4.2 전부, 4.3 의 예약 행 이유·사슬 기준 표식(사슬 쪽). DB 열은 A 의 v25 를 마이그레이션 없이 쓴다 | A | 2 | E, F, G | G 와 `cli.py`·`core/model.py`·`core/status.py` 가 겹친다. 물결 2 에서 가장 먼저 머지한다 |
| E | `feat/m5s-release-status-panel` | 4.4 라우트·`GET …/release` 키·창 고치기·검토 패널 | B | 2 | D, F, G | F 와 `server.py` 릴리스 경로·`app.js` Store 탭·`i18n.js` 가 겹친다. E 를 먼저 |
| F | `feat/m5s-golive-route-and-principle` | 4.5 전부(라우트·409·원칙 문장 전 자리·출시 예약 시트·가드 예외), 템플릿 `presets.release.toml`·`profile.toml` 의 프리셋과 역할 | A, B (머지는 D 뒤) | 2 | D, E, G | E 와 위의 파일이 겹치고 G 와 `app.js`·`i18n.js`(en·ko 두 블록)·`test_web_browser.py` 가 겹친다. E 다음에 머지하고 D 가 머지된 뒤 템플릿의 프리셋 키를 D 에 맞춘다 |
| G | `feat/m5s-web-schedule-and-action` | 웹 큐의 예약 절·새 상태 넷(`scheduled`·`expired`·`retried`·`exhausted`)·표식 표시·`rcm top`, 4.3 의 예약 행 이유·사슬 기준 표식(화면 쪽), 웹 머리의 `notify_failures` 배지 | A, C (머지는 D 뒤) | 2 | D, E, F | D 의 상태 이름은 이 명세대로 미리 넣는다. D 와 `cli.py`·`core/model.py`·`core/status.py` 가 겹치고 E·F 와 `app.js`·`i18n.js`(en·ko 두 블록)·`test_web_browser.py` 가 겹친다. 물결 2 에서 마지막에 머지한다 |
| H | `test/m5s-acceptance` | 시험 서버 실기(§7 의 실기 칸), 보고서 `docs/acceptance/reports/`, 스크린샷, CHANGELOG 정리 | A~G | 3 | 앱 레포 세션의 프리셋 작업 | — |
| R | `release/v0.10.0` | 버전 0.10.0 → dev → main(§8 개인정보 점검) → 태그 | H | 4 | — | — |
| 운영 | — | 큐가 빌 때 서비스 워크트리를 main 으로 · 재시작. 오너 승인 뒤 | R | 5 | — | — |

A 의 DB v25 에는 이번 마일스톤의 열을 모두 넣는다. `not_before`, `start_deadline`, `no_supersede`, `chain_id`, `retry_of`, `attempt` 와 얼린 반복 정책 열(`retry_on_exit`·`retry_interval_seconds`·`retry_max_attempts`·`retry_disabled`)이다. D 는 마이그레이션 없이 그 열을 쓴다. 결정 221 의 「DB v25 하나」는 그대로다(결정 237).

물결 2 는 D, E, F, G 를 동시에 띄우고 D, E, F, G 순서로 머지한다.

여러 PR 이 같이 고치는 파일도 있다. `scripts/mutcheck.py`(A·D·F), `CHANGELOG.md`, `tests/test_docs_m5s.py` 다. 각 PR 은 자기 항목만 더하고 앞 PR 이 머지되면 다시 맞춘다.

모든 PR 은 같은 절차를 밟는다.

| 단계 | 하는 일 |
|---|---|
| 1 | `branch` 스킬로 이름 확인 → `git worktree add -b <브랜치> ../remote_ci_monitor-<…> origin/dev` → `.venv` |
| 2 | 시험 먼저: 시험 작성 에이전트가 §7 의 해당 시나리오로 빨간 시험을 쓴다 |
| 3 | 구현 → `ruff check . && ruff format --check . && pytest` · `node --test tests/web/*.test.js` · `python scripts/mutcheck.py` · `scripts/smoke_install.sh` |
| 4 | 같은 PR 에서 문서를 고친다(docs 스킬 체크리스트). 문서 잠금 시험은 `tests/test_docs_m5s.py` |
| 5 | `commit` 스킬 · `korean-writing` 스킬로 커밋과 PR 본문을 쓴다. 하위 에이전트의 임시 파일 이름은 고유하게 짓는다(`pr-body-<브랜치>.md`) |
| 6 | 격리 검증 에이전트(내 결론 없이 코드만 받는다) → 통과하면 PR 에 `verified: <head sha>` 댓글 |
| 7 | `rcm/ci` 초록 → `python3 tools/merge_pr.py <N>` → 워크트리 치우기 |

물결마다 PR 수만큼 하위 에이전트를 띄운다. 에이전트는 각자 자기 워크트리에서 동시에 돈다. 물결 1 은 셋이고 물결 2 는
넷이다. 본 세션은 설계 질문에 답하고 머지 순서를 지킨다. Mac mini 의 rcm 큐는 하나라서 `rcm/ci` 는 차례로
돈다(lanes=2).

앱 레포 세션에는 P0 가 머지된 직후, 물결 1 과 동시에 인계한다. 인계문에는 아래 여덟 가지를 넣는다.

- 결정 202(D2 폐기)
- 표식 약속(§4.3)
- 반복 프리셋 선언(§4.2)
- `status`·`golive` 계약(§4.4·4.5)
- 409 이름
- Chrome 프로필 위치
- 공용 `concurrency_group`
- 게시는 API 로 판정한다는 것

인계문은 레포 밖 파일로 쓰고 오너에게 그 경로를 준다. 앱 쪽 세부에는 개인정보가 섞일 수 있기 때문이다.

## 6. 진행 체크리스트

진행하면서 상태 칸을 채운다. 처음에는 모두 `[ ]` 였다. 릴리스 PR(R)을 여는 2026-11-23 에 #0~#14 가 끝났다. #4 의 인계문은 작성을 마쳤고 전달은 오너가 한다. #15~#18 은 릴리스와 운영 반영 때 채운다.

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 이 명세 작성 | P0 #250 | — | [x] |
| 1 | 격리 설계 반박 받기 · 반영 | P0 #250 | — | [x] |
| 2 | 오너 확인을 기다리는 결정 표시(§11) | P0 #250 | — | [x] |
| 3 | P0 머지 | P0 #250 | — | [x] |
| 4 | 앱 레포 세션에 인계문 전달 | — | 3 | 작성 끝, 전달은 오너 |
| 5 | A 시험 먼저 · 구현 · 검증 · 머지 | A #253 | 3 | [x] |
| 6 | B 시험 먼저 · 구현 · 검증 · 머지 | B #252 | 3 | [x] |
| 7 | C 시험 먼저 · 구현 · 검증 · 머지 | C #251 | 3 | [x] |
| 8 | D 반복 | D #256 | 5 | [x] |
| 9 | E status 패널 | E #254 | 6 | [x] |
| 10 | F golive 와 원칙 문장 | F #255 | 5·6·8 | [x] |
| 11 | G 웹 예약·상태·표식 | G #257 | 5·7·8 | [x] |
| 12 | mutcheck 에 새 변이 일곱(§7)이 들어갔는지 확인 | A·D·F #253 · #256 · #255 | — | [x] |
| 13 | 시험 서버 실기: §7 의 실기 칸 전부 | H #258 | 8~11 | [x] |
| 14 | CHANGELOG `[Unreleased]` 정리 · 스크린샷 | H #258 | — | [x] |
| 15 | 릴리스 PR dev→main 개인정보 점검(§8) | R | 13·14 | [ ] |
| 16 | 태그 v0.10.0 · 릴리스 확인 | R | — | [ ] |
| 17 | 운영 반영 오너 승인 · 큐가 빌 때 재시작 | 운영 | — | [ ] |
| 18 | 요청서 13절에 돌려줄 것 작성 · 인계 | — | 16 | [ ] |

## 7. 시험 계획

시험에서는 스토어에 쓰는 일을 하지 않는다. 가짜 프리셋과 가짜 `[[notify]]` 훅만 쓴다. 가짜 프리셋은 `sh -c` 로 정해진
종료코드와 표식을 찍고 가짜 훅은 받은 알림을 파일에 기록한다. 실기 시험 서버는 자기 설정과 포트 8790 을 쓰고
`data_dir` 은 `~/.local/share/rcm-devtest-m5s` 로 둔다. `/tmp` 아래는 금지다. 알림 훅도 가짜만 쓴다.

| ID | 시나리오 | 확인할 결과 | 어디서(단위·e2e·실기) | PR |
|---|---|---|---|---|
| S1 | 5분 뒤로 예약 | 그 시각에 실행된다(단위는 시계 주입, 실기는 실제 5분) | 단위·실기 | A |
| S2 | 예약 뒤 취소 | 실행되지 않고 `cancelled` | 단위·실기 | A |
| S3 | `rcm reload` · 서버 재시작 | 예약이 남는다 | e2e·실기 | A |
| S4 | 예약을 놓친다(서버를 끈 채 마감이 지남, 레인이 막혀 마감이 지남 두 가지) | `expired`, 가짜 훅이 파일에 기록 | e2e·실기 | A |
| S5 | 75 로 끝나는 가짜 프리셋(반복 선언)을 예약 | 정한 간격으로 반복하다 상한에서 `exhausted`(시험은 간격과 상한을 줄인다) | e2e·실기 | D |
| S6 | 같은 예약을 한 번 더 | 409 `schedule_exists` | 단위·실기 | A |
| S7 | 반복 선언 없는 프리셋이 75 로 끝남 | 지금처럼 `failed`(rcm-ci 보호) | 단위 | D |
| S8 | 예약이 있을 때 같은 입력으로 즉시 실행 | 예약에 합류하지 않는다 | 단위 | A |
| S9 | tree 예약의 스냅샷 blob | 시각 전 GC 에 지워지지 않는다 | 단위 | A |
| S10 | 예약 잡의 대기 표본 | `queued_at` 부터 잰다 | 단위 | A |
| S11 | `schedule` 기능 키가 없는 서버에 `--at` | 클라이언트가 거부하고 종료 2 | 단위 | A |
| S12 | 표식을 찍는 가짜 프리셋 | `rcm wait` stderr · JSON `action` · notify env · Needs a look · 큐 행 | 단위·실기 | C·G |
| S13 | 사슬 취소 | 기다리던 다음 시도도 취소 | e2e | D |
| S14 | golive 문들 | 비admin release 는 403, plan 없음·낡음·blocked·번호 불일치는 409, 오전에 건 오후 예약이 실행 때 막히지 않음, 제출된 초안에서 받음 | 단위 | F |
| S15 | status 문서 | 검토 패널에 그려짐, 모르는 state 는 unknown, 창 밖으로 밀리지 않음 | 단위·브라우저 | E |
| S16 | 원칙 문장 | §3 #8 의 모든 자리가 새 문장, 금지 버튼 가드 예외는 `[data-golive]` 에서만 통함 | 브라우저·문서 잠금 | F |
| S17 | `rcm check` 규칙 | 4.6 표대로 FAIL·warn | 단위 | B |
| S18 | 템플릿 | 두 스크립트의 `--selftest` PASS | 단위 | B |
| S19 | 활성 잡이 있을 때 같은 입력으로 예약 | 합류하지 않고 새 `scheduled` 잡 | 단위 | A |
| S20 | 75 로 끝난 직후 취소 | `cancelled`, 다음 시도 없음 | e2e | D |
| S21 | plan 모드, Android 미승인 | 0 + `android.approved = false`, `platform = ios` release 예약이 통과 | 단위·템플릿 selftest | F |
| S22 | release 모드, 미승인 | 2(75 아님) | 템플릿 selftest | F |
| S23 | `POST /jobs` 로 golive release | 409 `release_route_required`. 드라이버의 upload 제출은 그대로 통과 | 단위 | F |
| S24 | golive 사슬이 살아 있을 때 즉시 실행·새 예약 | 409 `golive_running` | 단위 | F |
| S25 | `rcm wait --return-on-action` | 표식에서 4 로 돌아오고 잡은 계속 돎 | e2e | C |
| S26 | 사슬의 첫 id 로 `rcm cancel` | 살아 있는 머리가 취소됨 | e2e | D |
| S27 | 반복 시도가 그룹에 막혀 오래 기다림 | `expired` 가 되지 않음 | e2e | D |
| S28 | tree 예약 업로드 세 경로 | 모두 `scheduled` 로 가고 응답 state 가 맞음 | 단위 | A |
| S29 | 서머타임 경계 시각 | 400 | 단위 | A |

변이 검사 `scripts/mutcheck.py` 에는 새 변이 일곱을 더한다. 일곱 모두 시험을 빨갛게 만들어야 한다.

- 승격이 `not_before` 를 무시한다
- 만료가 마감을 무시한다
- 반복 선언 없이 75 를 반복한다
- 예약에 즉시 실행이 합류한다
- golive release 의 admin 검사를 없앤다
- 취소 뒤에도 `retried` 로 닫고 다음 시도를 만든다
- `start_at` 있는 제출이 합류한다

## 8. dev → main PR 개인정보 점검 (오너 지시, 결정 222)

점검 범위는 `git diff origin/main...origin/dev` 의 모든 파일(이미지 포함)과 `git log origin/main..origin/dev` 의 커밋
메시지·작성자·커미터다.

| 단계 | 하는 일 | 통과 조건 |
|---|---|---|
| 1 | 개인 패턴을 검사한다. 패턴 목록은 레포 밖 파일 `~/.config/rcm-privacy/patterns.txt` 에 둔다. 오너 이름·이메일·macOS 사용자 이름·GitHub 로그인·실제 Tailscale 주소·호스트 이름·앱 번들 ID·비밀 폴더 이름·Google 계정이 그 목록에 든다. 목록을 레포에 두면 그 자체가 유출이므로 레포에 넣지 않는다 | 0건 |
| 2 | 일반 패턴을 검사한다. 이메일 정규식, `/Users/<이름>` 경로, `100.64.0.0/10` 안의 실제 주소(시험 경계값 100.63.x·100.128.x·100.127.255.x 는 제외), Teams·Slack 웹훅 도메인, 토큰 모양을 본다 | 0건, 또는 하나씩 확인해 무해함을 적는다 |
| 3 | `gitleaks git --log-opts="origin/main..origin/dev"` 를 돌린다(이 머신에 있다) | 0건 |
| 4 | 커밋 메타데이터: 범위 안 작성자·커미터 이메일이 모두 noreply 주소인지 본다 | 전부 noreply |
| 5 | 바뀐 이미지(스크린샷)를 눈으로 보고 이름·주소·토큰·계정·호스트를 찾는다 | 0건 |
| 6 | 격리 에이전트가 같은 범위를 따로 훑는다. 이 세션의 결론은 주지 않는다 | 0건 |
| 7 | 걸린 것이 있으면 dev 로 고치는 PR 을 먼저 머지하고 1단계부터 다시 한다 | — |
| 8 | 릴리스 PR 본문 「검사」 절에 단계별 결과를 한 줄씩 적는다. 패턴 내용은 적지 않는다 | — |

이미 main 에 들어가 있는 것도 있다. 2026-11-23 에 확인했고 이번 점검 범위 밖이다.

| 자리 | 든 것 |
|---|---|
| 수용 검사 보고서 셋(`docs/acceptance/reports/2026-09-10-m5i-verify-{db,suite,web}.md`)과 리뷰 하나(`docs/reviews/2026-09-13-impl-reviews/pr-84.md`) | macOS 사용자 이름이 든 절대 경로 |
| 와이어프레임 `docs/wireframes/web-store.html` | GitHub 로그인 이름 · 앱 번들 ID · 비밀 폴더 이름 |
| 수용 검사 보고서 둘(`docs/acceptance/reports/2026-09-06-user-personas.md` · `2026-11-16-m5n-verify-real.md`)과 명세 둘(`docs/m5n-workplan.md` · `docs/m5o-workplan.md`) | Mac mini 의 호스트 이름 |
| 2026-09-09 커밋 13개 | 작성자가 회사 이메일 |

지금 파일에서 지우는 PR 은 만들 수 있다. 하지만 git 역사에는 남는다. 룰셋이 강제 push 를 막으므로 역사를 다시 쓸지는
오너만 정할 수 있다. 오너는 2026-11-23 에 현재 파일에서만 지우고 역사는 남기기로 정했다(결정 223).

## 9. 위험

| 위험 | 막는 법 |
|---|---|
| 예약이 늦게 조용히 돈다 | 시작 마감과 `expired`, 재시작할 때 먼저 판정 |
| 옛 서버가 시각을 무시하고 바로 돌린다 | health 기능 키, 클라이언트 거부 |
| 출시가 두 번 걸린다 | `golive_running`, `schedule_exists`, 사슬 안의 다음 시도도 예약이라 같이 막힘, 스크립트 멱등성 |
| 일반 `POST /jobs` 로 출시 문을 우회한다 | golive 는 409 `release_route_required`, review·upload 의 틈은 후속으로 적어 둠 |
| 75 가 승인 감시 출시가 된다 | release 모드 미승인은 언제나 2, selftest |
| 사람의 취소를 반복이 덮는다 | `only_from=(RUNNING,)`, 같은 트랜잭션 |
| rcm-ci 의 75 가 반복된다 | 반복은 프리셋이 선언할 때만 |
| 상태 집합을 잘못 골라 잡이 사라지거나 합류한다 | 결정 206, 시험 S8·S9, 새 변이 |
| Play Console 화면이 바뀌어 버튼을 못 찾는다 | 앱 레포 스크립트가 스크린샷을 남기고 실패(1), 게시 판정은 API |
| 구글 계정 위험(자동 조작을 위험 신호로 볼 수 있다) | 오너가 감수하기로 하고 진행한다(결정 202). 로그인은 사람이 한다 |
| 공용 빌드 머신에 오너의 구글 세션이 남는다 | 프로필은 비밀 폴더 쪽, 권한 0700 |
| 고정 문장을 한 곳이라도 빠뜨린다 | §3 #8 목록 전부를 F 에서, 문서 잠금 시험과 브라우저 시험 |
| 알림 실패를 아무도 모른다 | `notify_failures` 를 `rcm check` 와 웹에 |

## 10. 요청서 13절에 돌려줄 것

| 13절 항목 | 어디서 나오나 | 언제 |
|---|---|---|
| 1 확정한 문법(예약 걸기·목록·취소·반복·status·golive, CLI·HTTP 한 줄씩) | §4.1·4.2·4.4·4.5 + A·D·E·F 의 실제 도움말 | R 뒤 |
| 2 계약 변경 요약(§2 행, 머리말·§6 문장, 새 상태, 새 409, 바꾼 제안) | 이 명세 §11 의 「요청서와 다른 점」 열 | R 뒤 |
| 3 R9 표 | §4.7 | R 뒤(실측으로 다시 확인) |
| 4 시험 결과 6개 | §7 S1~S6 실기 | H |
| 5 버전과 올리는 방법 | v0.10.0, 맥북 클라이언트 올리기 절차 | R 뒤 |
| 6 앱 레포 쪽에 더 필요한 것 | §2 앱 레포 칸 + 표식·반복 선언·프로필 위치 | P0 인계 때 먼저, R 뒤 최종 |

13절 2번에는 요청서와 다르게 정한 곳을 적는다.

- D2 를 버렸다. Android 도 도구가 게시한다.
- 반복은 제출 때 고르는 선택이 아니라 프리셋이 선언한다. 제출에서는 끄기만 할 수 있다.
- 반복 시도는 새 잡으로 만들고 `retried` 상태로 닫는다.
- 끝난 예약은 7일이 아니라 기존 보존 규칙으로 보관한다.
- 「사람이 할 일」 표식을 새로 만들었다.
- `exhausted` 이름은 그대로 확정했다.
- `--in` 상대 시각을 더했다.
- golive 번호에 `auto` 는 없다.
- plan 모드는 승인 여부를 종료코드가 아니라 문서에 적는다.
- release 모드의 75 는 승인 뒤 남은 일에만 쓴다.
- 반복 시도에는 시작 마감이 없다.
- 일반 예약 중복은 소스까지 본다. golive 는 `golive_running` 으로 막는다.
- `rcm wait --return-on-action`(종료코드 4)을 더했다.
- `POST /jobs` 로는 golive 의 `mode = release` 를 낼 수 없다.

## 11. 결정 표

「오너 확인」 열의 ⛔ 는 사람이 정해야 하는 항목이다.

| # | 항목 | 정한 것 | 오너 확인 | 요청서와 다른 점 |
|---|---|---|---|---|
| 201 | 승인 뒤 출시 원칙 | 사람이 버전·빌드 번호·시각을 적은 출시만, 승인 감시 자동 출시 금지 | 확정(D4) | — |
| 202 | Android 게시 주체 | 앱 레포 스크립트가 Mac mini Chrome 전용 프로필로 게시, 관리형 게시는 켠 채 | 확정(2026-11-23) | D2 폐기 |
| 203 | 로그인 풀림 | A 화면 공유 기본 + B 요청 컴퓨터 Chrome 대안 | 확정(2026-11-23) | 신설 |
| 204 | 예약 실행 | 이번 마일스톤에 포함 | 확정(2026-11-23) | — |
| 205 | 게시 판정 · 브라우저 겹침 · 프로필 위치 | 스토어 API 로 판정, 공용 concurrency_group, 비밀 폴더 쪽 | 추천 | — |
| 206 | `scheduled` 상태 집합 | 활성 집합 밖, 필요한 곳에 명시적으로, health `schedule` | 추천 | — |
| 207 | 시각 해석 | 서버가 해석, `--at`·`--in`, 지난 시각 400, 14일 상한 | 추천 | `--in` 추가 |
| 208 | 시작 마감 · 우선순위 | 서버 900초 + 프리셋 덮기, 제출 때 정해진 잡의 우선순위(admin 의 `--priority` 포함)를 그대로 | 추천 | — |
| 209 | 중복 | 일반 예약은 join_key(소스 포함) 기준 409 `schedule_exists`, golive 는 232 가 더 넓게 | 추천 | 이름 확정 |
| 210 | 반복 켜기 | 프리셋 `[presets.retry]` 선언, 제출은 끄기만 | 추천 | 제출 선택에서 바꿈 |
| 211 | 반복 기제 | 시도마다 새 잡, `retried`, `retried_as` 따라가기, `chain_id`, git_ref 만 | 추천 | 신설 상태 `retried` |
| 212 | 종료코드 | `expired` 2, `exhausted` 1 | 추천 | — |
| 213 | 「사람이 할 일」 표식 | `::rcm::action::<code>::<note>` | 추천 | 신설 |
| 214 | 중간 알림 | rcm 은 만들지 않음, 프로젝트 스크립트가 Teams | 추천 | — |
| 215 | golive 번호 | 사람이 친 번호만, `approved_build` 와 비교 | 추천 | `auto` 없음 |
| 216 | 제출된 초안 | golive 는 받는다, 버튼은 버전 페이지 | 추천 | — |
| 217 | 원칙 문장 문안 | §4.5 문안 | 추천 | — |
| 218 | 금지 버튼 가드 | `[data-golive]` + 출시 예약 영역 안일 때만 예외, 탐침 | 추천 | — |
| 219 | 빈 선택 역할 | warn | 요청서대로 | — |
| 220 | 끝난 예약 보존 | 기존 보존 규칙 | 추천 | 7일 대신 |
| 221 | 버전 | v0.10.0, DB v25 | 추천 | — |
| 222 | main PR 개인정보 점검 | §8 절차 | 확정(2026-11-23 오너 지시) | — |
| 223 | 기존 main 의 개인정보 | 파일에서 지울지, 역사를 다시 쓸지 | 확정(2026-11-23): 현재 파일에서만 지움, 역사는 남김 | — |
| 224 | plan 모드 종료코드 | 읽었으면 0, 승인은 `platforms.<p>.approved` 에만 | 추천 | 요청서 종료코드 표를 모드별로 나눔 |
| 225 | 반복 원자성 | `retried` 는 RUNNING 에서만, 다음 시도는 같은 트랜잭션 | 추천 | — |
| 226 | 예약 제출은 합류하지 않음 | `want_join = False` | 추천 | — |
| 227 | 일반 예약 중복 | join_key 기준 | 추천 | R6 의 「프리셋과 입력」을 소스 포함으로 |
| 228 | 사슬 | `chain_id`, 취소는 머리로, capability 는 뿌리, 넘기는 열 목록, 보관은 마지막 시도만 | 추천 | — |
| 229 | 반복 정책 얼리기 | 제출 때 잡 열로 | 추천 | — |
| 230 | 옛 클라이언트 | 반복 프리셋이 있으면 `min_client_version` 0.10.0 | 추천 | — |
| 231 | 반복 시도 마감 | 적용 안 함, golive 템플릿 마감 3600초 | 추천 | R4 를 첫 시도에만 |
| 232 | golive 중복 | 살아 있는 사슬이면 409 `golive_running` | 추천 | 신설 |
| 233 | 사슬 표식 | 가장 최근에 돈 시도의 마지막 action, 표식 없이 돈 시도가 지움 | 추천 | — |
| 234 | 중간에 돌아오기 | `rcm wait --return-on-action` 종료코드 4 | 추천 | 신설 |
| 235 | 일반 경로 막기 | `POST /jobs` 의 golive `mode = release` 는 409 `release_route_required`, review·upload 의 같은 틈은 후속(드라이버가 기댐), `rcm release golive`·`rcm release status` 신설 | 추천 | 신설 |
| 236 | release 모드 75 | 승인 뒤 남은 일에만, 미승인은 2 | 추천 | 요청서 「75 = 아직」을 좁힘 |
| 237 | DB | v25 에 이번 마일스톤의 열 전부 | 추천 | — |
| 238 | `not_scheduled` 화면 문구 | 코드는 두고 문구만 | 추천 | — |

## 12. 격리 반박에서 바뀐 것

2026-11-23 에 격리 에이전트가 첫 판을 한 번 반박했다. 반박은 P0 가 3건, P1 이 18건, P2 가 15건이다. 여기서 P0·P1·P2 는 반박의 등급이고 §5 의 PR 이름 P0 와는 다르다. §3 의 파일:줄 #1~#15 는 모두 맞았다.

반복 기제(결정 211)는 「시도마다 새 잡」을 그대로 둔다. 그쪽이 덜 위험하다고 판단했다. 같은 행을 다시 쓰면 원격 워커 소유 검사(`remote_workers.py:582-591`)가 워커 이름과 BUSY 만 보므로 시도 N 의 늦은 finish·로그·산출물이 N+1 에 들어간다. 이를 막으려면 시도 번호를 담는 프로토콜 변경이 필요하고 마커·로그 오프셋·`job_artifacts`(잡 id 기본 키)·`job_failures` 가 섞인다.

반박 번호는 리뷰의 번호를 그대로 쓴다.

| 반박 | 등급 | 첫 판 | 둘째 판 | 결정 |
|---|---|---|---|---|
| P0-1 | P0 | plan 이 막힘이면 2 | plan 은 읽었으면 0, 승인은 문서에 | 224 |
| P0-2 | P0 | 75 → retried 에 취소 경쟁 없음 | RUNNING 에서만, 같은 트랜잭션 | 225 |
| P0-3 | P0 | 예약 제출의 합류 언급 없음 | 합류 안 함 | 226 |
| P1-1 | P1 | `mark_uploaded` 만 | 업로드 완료 세 경로와 응답 state | — |
| P1-2 | P1 | supersede 끄기를 안 다룸 | DB 열로 얼림 | 237 |
| P1-3 | P1 | 중복은 소스 무시 | 일반은 join_key | 227 |
| P1-4 | P1 | 원자성 없음 | 같은 트랜잭션, 없으면 wait 3 | 225 |
| P1-5 | P1 | supersede 처럼 따라간다(틀림) | 새 동작, JSON·timeout·산출물 규칙 | 228 |
| P1-6 | P1 | capability 를 옮긴다 | `chain_id`, 머리로 돌리기 | 228 |
| P1-7 | P1 | 넘기는 규칙 없음 | 넘기는 열 목록, 보관은 마지막 | 228 |
| P1-8 | P1 | 정책을 끝날 때 읽음 | 제출 때 얼림 | 229 |
| P1-9 | P1 | 옛 클라이언트 언급은 예약만 | `min_client_version` | 230 |
| P1-10 | P1 | 반복 시도에도 마감 | 반복엔 없음, golive 3600 | 231 |
| P1-11 | P1 | `schedule_exists` 로 충분 | `golive_running` | 232 |
| P1-12 | P1 | 「최신 시도 기준」 | 가장 최근에 돈 시도 규칙, 예약 행 이유 | 233 |
| P1-13 | P1 | stderr 한 줄 | `--return-on-action` | 234 |
| P1-14 | P1 | 라우트 문만 | golive release 는 `POST /jobs` 에서 막음, review·upload 는 후속(드라이버가 기댐), CLI 신설 | 235 |
| P1-15 | P1 | 75 = 아직 | release 미승인은 2 | 236 |
| P1-16 | P1 | 원칙 문장 자리 일부 | 목록 보탬 | 217 |
| P1-17 | P1 | 물결 1 셋이 독립 | 템플릿 프리셋은 F, DB 열은 A, 머지 순서 | 237 |
| P1-18 | P1 | 없는 기능에 기댐 | C·G 가 만든다, 한계 적음 | — |
| P2 15건 | P2 | — | §4.1·4.2·4.3·4.5·4.6·§5 에 반영(이유 이름, 워커 잊기, claim 두 갈래, pause, 서머타임, bump·eta, 우선순위, recent, 알림 두 곳, note 정리, 스키마 잠금, discarded, 제출 전달, 가드 묶기, 바이트 비교 대상, 공유 파일) | 238 외 |
