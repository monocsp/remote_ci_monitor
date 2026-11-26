#!/usr/bin/env python3
"""뮤테이션 확인 — 테스트가 실제로 빨개지는지 본다(PLAN.md 「테스트·품질」).

`src/` + `tests/` + `tools/` + `pyproject.toml` 을 임시 디렉터리에 복사하고 변이 하나를 넣은 뒤 그
복사본에서 pytest 를 돌린다. **pytest 가 실패해야 통과**다. 원본은 건드리지 않는다. 변이 패턴을
못 찾으면 그 자체로 실패다(코드가 바뀌어 감시가 풀린 것).

변이 목록(개수는 `MUTANTS` 가 정본이다 — 문서의 숫자는 머지마다 썩는다):
  M5t-a PR1 — `m5t-local-only-remote-kept`: 후보 함수가 `local_only` 프리셋의 원격 머신을 안 뺌
     (`capabilities.py`, `docs/m5t-workplan.md` §4.3 · TO15 — 진입점 17 경우가 모두 빨개진다)
     + `m5t-cli-local-only-refusal-is-unknown`: `rcm run` 이 원격 금지 409 를 3 으로 끝냄(`cli.py`)
     + `m5t-check-local-only-needed-only`: 원격에서 못 도는 점검의 대기 · 실패 기록이 판정에 남음
     (`capabilities.py`, P6 GAP X1)
     + `m5t-check-local-only-claimed-ignored`: 그 머신이 집은 점검도 check_local_only 로 냄
     (`capabilities.py`, batch 7 T3)
     + `m5t-claim-excluded-sql-dropped`: claim SQL 이 제외 목록을 안 씀(`store.py`, batch 7 T5)
  M5t-a PR2 — `doctor-unknown-exits-0`: 판정 못 함을 0 으로 끝냄(`core/doctor.py`, 결정 246) +
     `doctor-spec-unreachable-checks-empty`: `/worker/spec` 의 `unreachable_checks` 가 늘 빔
     (`capabilities.py`, TO18) + P3 1라운드의 넷: 잘린 XML plist 를 안 잡음(`doctor.py`) ·
     `rcm check` 의 git 행이 shim 규칙을 건너뜀(`cli.py`) · 서버가 재지 않은 로컬 레인 프리셋이
     ready(`doctor.py`) · 호스트 사실의 제어문자를 안 지움(`capabilities.py`) + P3 2라운드의 둘:
     doctor 의 맨 바깥 경계가 없음 · 서버 답의 모양을 안 봄(`doctor.py`)
  M5s C — `action-*` 다섯: 도는 행의 표식을 이유가 무시 · code 어휘 검사 제거 · note 제어문자를
     안 지움 · 플래그 없이 4 · 취소 중에도 4 + `clean-line-keeps-bidi`(note · 실패 이름이 bidi 를
     남김) (`docs/m5s-workplan.md` §4.3 · 결정 213 · 234 · #251 격리 검증)
  M5p P2 — `shelf-*` 넷: 파일 응답에서 `sandbox` CSP 를 뺌 · 퍼센트 디코딩 뒤의 경로 검사를 뺌 ·
     보관함 zip 이 M5e 전송 슬롯을 같이 씀 · Host 허용 목록을 건너뜀
     (`docs/m5p-workplan.md` §10 · `docs/m5p-test-scenarios-b.md`)
  M5p P1 — `shelf-*` 넷: 보관 항목을 지우기 **전에** deleted 로 표시 · 1시간 쓰기 규칙 제거 · 보관
     중인 묶음의 ack 를 미루지 않음 · keep 을 켤 때 ready 묶음의 보관 행을 안 세움
     (`docs/m5p-workplan.md` §10 · `docs/m5p-test-scenarios-a.md`)
  M5p P3 — `shelf-health-detection-removed`(옛 서버에 `--keep` 을 그냥 보냄) ·
     `notify-shelf-url-dropped`(알림 env 에서 `RCM_SHELF_URL` 이 빠짐) · `shelf-wait-skipped`(보관
     대기 0초) · `web-shelf-link-absolute`(SPA 보관함 링크가 행의 절대 URL 을 씀) ·
     `shelf-get-skips-sha256`(`rcm shelf get` 이 해시를 안 봄, §16)
     (`docs/m5p-workplan.md` §10 · `docs/m5p-test-scenarios-c.md`)
  M5p 뒤 — `shelf-expand-keeps-apple-double`: 펼침이 macOS 메타데이터 멤버(`._*`)를 거르지 않음
     (`shelf_copier.py`, 2026-11-20 운영 잡 #3821 — 깨진 그림 23개)
  M5s A — `m5s-*` 넷: 승격이 `not_before` 를 무시함 · 만료가 시작 마감을 무시함 · 합류 쿼리가
     예약에서 온 잡을 거르지 않음(즉시 실행이 예약에 합류함) · 시각을 준 제출이 합류함
     (`docs/m5s-workplan.md` §4.1 · §7)
  M5s D — `m5s-retry-*` 둘: 반복 선언 없이 75 를 반복함 · 취소 뒤에도 retried 로 닫고 다음
     시도를 만듦 (`docs/m5s-workplan.md` §4.2 · §7) + #256 격리 검증의 셋: 다음 시도가 더 새
     커밋을 supersede 함(판정 · 승격 두 자리) · 합류가 얼린 반복 정책을 안 봄 + 시험 구멍 넷
     (로컬 취소 경주 · 로컬 묶음 · finish 의 묶음 · 원격 묶음 만료)
  M5s F — `m5s-golive-release-without-admin`: golive release 의 admin 검사를 뺌 ·
     `m5s-golive-retries-not-approved`: 2(미승인)에도 반복함
     (`docs/m5s-workplan.md` §4.5 · §7)
  M5o A — `m5o-*` 셋: 합류자 이름 검사 제거 · 업로드 중 치움 · 취소를 닫는 길이 새 잡 번호를
     잊음 (`docs/m5o-workplan.md` §7.2 · `docs/m5o-test-scenarios-a.md`)
  M5o B — `failure-summary-remote-old-text`(원격 finish 만 옛 `exit 1`) ·
     `notify-link-zero-address`(링크에 `0.0.0.0`) · `failure-summary-full-path`(요약에 경로 전체)
  M5o C — `include-*` 둘: 들인 파일의 권한 검사 제거 · 들인 파일의 `[[repos]]` 허용 +
     `reload-dry-run-assigns`: dry-run 이 대입까지 감 (`docs/m5o-workplan.md` §4)
  M5n — `m5n-*` 여섯: 낡은 세대 · 자동 점검 반복 · timeout 지문 · host 범위 · 단계 중앙값
     물러남 · capable claim 의 풀 (`docs/m5n-workplan.md` §15) + 엣지 검증의 둘: 모르는
     호스트가 host 범위를 지나침 · 점검 기록 읽기의 전체 스캔
  ① remaining-floor  — 잔여 하한 제거 (`core/queue.py`)
  ② join-key-inputs  — 합류 키에서 inputs 제외 (`core/queue.py`)
  ③ restart-lost     — 재시작 정리에서 running → lost 를 succeeded 로 (`store.py`)
  ④ stale-threshold  — 호스트 표본 stale 판정의 3×interval 을 0 으로 (`core/hostparse.py`, M1)
  ⑤ top-first-sample — macOS top 의 마지막 표본 대신 첫 표본 사용 (`core/hostparse.py`, M1)
  ⑥ web-not-moving-unknown — 웹 UI 「Not moving」이 queue null 을 「Nothing is stuck」으로
     (`web/app.js`, M2, node --test)
  ⑪ admission-fail-open — 부하 게이트가 「표본 없음」에 열림 (`core/admission.py`, M5f)
  ⑫ admission-samples-any — 창의 **전부**가 아니라 **하나라도** 기준 아래면 열림 (같은 파일)
  ⑬ web-progress-partial-total — 전체 진행 막대가 「지금까지 본」 스텝 총계를 확정 총계처럼 씀
     (`web/app.js`, node --test)
  ⑭ web-progress-default-estimate — 표본도 프리셋 값도 없는 설치 기본값(600초)으로 눈금을 그림
     (같은 파일 · Codex 리뷰 1)
  ⑮ web-progress-full-bar — 도는 잡의 예측 막대가 100% 까지 차오름 (같은 파일 · Codex 리뷰 2)
  ⑯ nowait-view-trusted — `--no-wait` 의 표시용 조회가 문서를 곧이곧대로 믿음 (`cli.py`)
  ⑰ retention-budget-active — 부피 회수가 **도는 잡과 고아**까지 후보로 삼음
     (`core/retention.py`, M5g — 이 기능의 최대 사고)
  ⑱ retention-measure-fail-open — 못 잰 크기를 None 이 아니라 0 으로 세어 예산을 지키는 척함
     (같은 파일)
  ⑲ retention-budget-unreachable — 못 이룰 목표(지울 수 없는 바이트가 이미 상한 초과)에도
     종료 잡을 전부 태움 (같은 파일 · Codex 2차 리뷰 E)
  ⑳ failed-step-fallback — 실패 스텝이 다시 「마지막으로 시작한 스텝」으로 추론됨
     (`core/progress.py`, M5h 결정 63 — 운영 잡 #162 가 이 폴백으로 성공한 스텝을 지목했다)
  ㉑ failure-window-cancelled — 이력 창이 취소·유실 잡을 분모에 넣음 (`store.py`, M5h 결정 66)
  ㉒ ledger-outside-tx — 실패 이름 대장을 finish 커밋 **뒤에** 씀 (`store.py`, M5h §2.1)
  ㉓ retention-shared-any-inode — 공유 블록 판정에서 `S_ISREG` 를 뺌: 디렉터리의 `nlink > 1`
     (하위 디렉터리 때문에 정상)까지 「지워도 안 는다」로 셈 (`janitor.py`, M5i 결정 76)
  ㉔ offline-gc-opens-original — 오프라인 dry-run 이 임시 사본이 아니라 **살아 있는 DB** 를 열어
     마이그레이션함 (`cli.py`, M5i 결정 73 — 2026-09-10 운영 DB 7→15 사고)
  ㉕ v16-without-ledger-check — v16 복구가 대장 행이 있는 **선언 라벨까지** 옮김
     (`store.py`, 결정 78)
  ㉖ client-wheel-any-name — `/client/*.whl` 이 이름이 달라도 200 (정확한 파일명 검사 제거 —
     `server.py`, M5i I8 결정 81. pip 는 URL 의 파일명으로 버전을 믿는다)
  ㉗ serve-store-before-bind — `serve()` 가 포트를 잡기 **전에** DB 를 열어 마이그레이션함
     (`server.py`, M5l L5 · 리뷰 pr-94 B-1 — 도는 서비스 곁에서 다른 빌드로 치면 DB 가 먼저 바뀐다)
  ㉗ offline-gc-ignores-sqlite-error — 오프라인 dry-run 이 사본 마이그레이션의 `sqlite3.Error`
     를 안 잡음: 트레이스백 + exit 1 (`cli.py`, M5l L1 — 리뷰 #87 B P1)
  ㉗ retention-unknown-deleted-as-zero — 크기를 못 잰 항목을 지웠을 때 `unknown_count` 로 세지
     않고 0 B 로 더함 — 영수증이 「freed 0 B」를 확정 표시 (`janitor.py`, M5l L2 · 리뷰 #88 B1)
  ㉗ web-page-error-collector-removed — Chrome 테스트 하네스가 페이지 예외 수집기를 설치하지
     않음 (`tests/test_web_browser.py`, M5l L7 · pr-82 리뷰 P2). **Chrome 이 있어야 돈다** —
     없으면 그 테스트가 skip(exit 0)이라 「못 잡음」과 구별이 안 되므로 SKIP 으로 보고하고
     실패로 세지 않는다. `RCM_CHROME` 이 설정돼 있으면 무조건 돈다(못 찾으면 대조군이 빨강).
  ㉗ cancel-capability-skipped — 강제 모드(`cancel_requires_submission_token`)에서 capability
     검사를 건너뛰어 옛 공유 토큰 규칙으로 물러남 (`server.py`, M5j G5 결정 87 — 같은 토큰의
     다른 세션이 남의 잡을 지운다)
  ㉘ leave-binding-removed — `leave_submission` 비밀을 받은 참여자 확인을 뺌: 남의 leave 비밀로
     자기 joiner 행을 지운다 (`server.py`, M5l S6 — Bob 비밀 + Charlie bearer 가 Charlie 를 뺐다)
  ㉗ requires-check-skipped — 프리셋 `requires` 검사를 건너뜀: 없는 도구로도 프로세스가 뜬다
     (`runner.py`, M5j G4 결정 85 — 그게 「옛 SDK 로 초록」이었다)
  ㉘ requires-path-server-cwd — PATH 의 상대 항목을 워크스페이스가 아니라 검사 프로세스의 cwd
     기준으로 푼다: 서버 폴더에만 있는 도구로 통과한다(`runner.py`, M5l S1 — PR #109 리뷰 B)
  ㉙ requires-cancel-ignored — preflight 직전의 취소 확인 제거: 취소한 잡이 `tool_missing` 으로
     끝난다(`runner.py`, M5l S4)
  ㉚ web-host-panel-follows-load — 호스트 절의 열림을 다시 압력 판정으로 매 렌더 덮어씀:
     CPU 가 85 를 스칠 때마다 절이 여닫히며 그 아래가 315px 뛴다 (`web/app.js`, 2026-09-14 실측).
     **Chrome 이 있어야 돈다**
  ㉚ web-host-busy-no-history — 「바쁨」 판정에서 이력을 뺌(경계 하나로 되돌림): 같은 표본 열 개에
     판정이 여덟 번 뒤집힌다 (`web/app.js`, node --test)
  ㉛ step-stuck-ignores-step-median — 단계 판정이 다시 침묵으로 돌아감: 단계 실측을 재 놓고
     쓰지 않는다 (`core/queue.py`, 2026-09-15 사고 — 정상으로 도는 `gate` 가 매 실행 빨개졌다)
  ㉜ web-quiet-is-actionable — 「조용함」을 「확인이 필요한 작업」 패널에 올림: 빨간 소음이
     이름만 바꿔 그대로 남는다 (`web/app.js`, node --test)
  ㉝ web-progress-sub-full-bar — 스텝 안의 세부 진행(`::rcm::progress::`)이 `done == total` 에서
     막대를 100% 로 채움: 아직 도는 잡이 「끝났다」로 읽힌다 (`web/app.js`, node --test)
  ㉞ progress-sub-not-reset-by-step — 새 `::rcm::step::` 이 앞 스텝의 세부 진행을 안 비움: 다음
     스텝이 지난 스텝의 41/68 을 달고 돈다 (`core/progress.py`)
  M5q A0 — `client-wheel-drops-skills`: 서버가 조립하는 클라이언트 wheel 의 `ASSET_DIRS` 에서
     `skills` 를 뺌 — `SKILL.md` · 셸 템플릿 15개가 빠진다 (`clientwheel.py`, 결정 167 ·
     0.6.1 의 결함)
  ㉟ version-settle-ignores-what-is-still-running — 버전 행을 내려놓을 때 **무엇이 아직 도는지**
     안 봄: 회차가 스토어에 올리는 중인데 손으로 낸 잡이 먼저 끝나면 행이 `editing` 이 되어
     «버리기» 가 열린다 (`server.py`, 워크플랜 §14-2 — 그 버리기는 App Store 버전을 지운다)
  ㊱ claim-ignores-pause — claim 트랜잭션 안의 정지 검사를 뺌: `get_paused` 확인 뒤 · claim 전에
     걸린 정지를 놓쳐 정지 중에 잡을 집는다 (`store.py`, 2026-11-19 — 배포의 「정지 → 도는 잡 0
     확인 → 재시작」 직후 잡이 시작돼 lost 가 된다)
  ㊲ events-offer-outside-lock · events-replay-outside-lock — 이벤트 버스가 offer 와 replay 를
     잠금 밖에서 함: 동시 발행의 id 가 큐에서 뒤집혀, 뒤의 id 로 다시 붙은 SSE 클라이언트가 앞의
     id 를 영영 못 받는다 (`events.py`, 2026-11-20 PR #238 의 CI — 243 · 244)

  M5q A — `m5q-*` 열다섯(격리 검증이 더한 넷 — 받기 전체 제한 · rename 에 진 쪽의 검사 · 캐시
     뿌리의 부모 · `rcm check` 의 version 정리 — 을 포함): 받은 wheel 의 sha256 을 안 봄 ·
     editable 기본값 뒤집기 · 따라간 자식이 다시 따라감 · 임시 폴더 없이 대상에 바로 풂 ·
     `serve` 가 따라감 · health 의 wheel 경로를 믿음 ·
     서버의 클라이언트 버전 줄을 인증 전에 씀 · 쌍을 안 기억함 · User-Agent 형식을 안 봄 ·
     `rcm version` 둘째 줄을 늘 씀 · `rcm check` 의 꼬리를 버림 (`docs/m5q-workplan.md` §6 ·
     `docs/m5q-test-scenarios-c.md` 「변이」)
  M5q B1 — `m5q-worker-*` 다섯: 버전 409 전에 도는 잡을 닫음 · 409 본문에 `server_version`
     을 안 실음 · 기동 ① 이 10분 기록을 씀 · 레인 재등록이 막혀도 종료 0 · `WorkerFollow` 가
     `WorkerExit` 가 아님 (`docs/m5q-workplan.md` §6 · `docs/m5q-test-scenarios-b.md`)
  M5r A — `m5r-*` 아홉: 포크 검사 제거 · 틀린 설정을 파일의 mode 로 · rcm-only 갈래를 late 에 ·
     대기 잡이 failure 를 fallback 으로 · creators 거르기 제거 · verdict 가 ran 을 안 봄 · 훅이
     taken-over 를 덮음 · 훅이 종료 75 를 failure 로 · 감시가 포크 PR 을 받음
     (`skills/rcm-ci-connect/templates/`, `docs/m5r-workplan.md` §6 「mutcheck 에 더할 변이」)
  M5q B2 — 다섯: 드레인이 도는 잡을 기다리지 않음 · 레인이 드레인 중에 claim 함 · 300초 창을
     지움 · 설치본 재실행이 따라가기 변수를 남김 · heartbeat 에 `server_version` 을 안 실음
     (`docs/m5q-workplan.md` §6 · `docs/m5q-test-scenarios-b.md`)

  작업이 길어진 원인을 막는 장치 — `guard-*` · `merge-pr-*` 다섯: `guard_naming` 이 셸 치환
     값(`--title "$T"`)을 다시 글자로 잼 · PR 본문 파일 이름 검사 제거 · `guard_merge` 의 REST
     머지(`gh api -X PUT …/pulls/N/merge`) 패턴 제거 · `merge_pr` 가 pending 체크를 안 봄 ·
     `merge_pr` 가 룰셋의 필수 context 를 안 봄 · `merge_pr` 가 필수가 아닌 체크도 셈 ·
     `guard_production` 의 옛 DB 판 비교 제거 (`tools/`, 2026-11-20 사건 6d · 6e · 6c · 2 · 6f)
  #237 격리 검증의 지적과 mutcheck 병렬 · CI 부하 — 여섯: `guard_merge` 가 `pr` 뒤의 `-R` 을 못 봄 ·
     `guard_naming` 이 앞의 `VAR=$(…)` 대입에 명령 이름을 놓침 · `merge_pr` 가 4xx 를 모름으로 셈 ·
     mutcheck 가 결과를 끝난 순서로 찍음 · 변이 사본에서 바이트코드를 씀 · `-n` 에서 Chrome 파일을
     한 워커로 안 묶음(`tests/conftest.py`, rcm-ci 잡 #3861)
  #245 격리 검증이 남긴 후속 — 여섯: 감시가 서버 재시작 순간의 제출을 다시 내지 않음 · `--check` 가
     서비스 venv 의 rcm 을 못 알아봄 · 닫힌 PR 의 합류한 잡을 취소함 · `guard_merge` 가 붙여 쓴
     `-Ro/r` 를 못 봄 · `-n` 에서 `--dist` 를 loadgroup 으로 안 정함 · timeout 이 하네스의 새 세션
     자식을 남김 (`docs/m5r-workplan.md` §13.8)
  M5s E — `m5s-*` 여덟: 역할 프리셋을 다시 한 창으로 읽음 · status 행이 jobs[] 의 50 자리를 같이
     씀 · 필요 없는 묶음 목록까지 읽음 · 도는 status 잡이 「지금 하는 일」이 됨 · 목록에 없는
     상태 값을 믿음 · 행이 차면 문서 찾기도 건너뜀 · 합친 잡을 새것부터 안 세움 · 실패한 조회를
     모른 척함 (`docs/m5s-workplan.md` §4.4 · §3 #11, #254 격리 검증)
  개발 루프 도구 — `scenario-trace-zero-ids-pass`: 표지는 있고 ID 가 없는 표를 통과시킴
     (`tools/scenario_trace.py`, 2026-11-25) + `dev-batch-*` 셋: 보고 없는 rcm/ci 를 초록으로 ·
     낡은 `verified:` 를 yes 로 · porcelain 첫 줄의 앞 공백을 잃음 (`tools/dev_batch.py`, 격리 검증)
     + 2라운드 셋: `@` 로 시작하는 커밋 경로를 버림 · 상태 파일의 꼴 검사를 건너뜀
     (`tools/dev_batch.py`) · 영수증에 집 경로를 남김 (`tools/redgreen.py`)
     + 3라운드 넷: 댓글을 읽기 전에 verified 를 "no" 로 둠(`tools/dev_batch.py`) · 안 닫힌 펜스를
     문제로 안 셈(`tools/scenario_trace.py`) · pytest 만 죽이고 그 그룹은 남김 · 잠긴 워크트리를
     `--force` 하나로 지우려 함(`tools/redgreen.py`)
     + 4라운드 여섯: 404 아닌 브랜치 읽기 실패를 「not pushed」로 · 끝난 check-run 의 모르는 결론을
     통과로 · PR diff 를 안 봄(`merge -s ours`)(`tools/dev_batch.py`) · xfail 을 돈 시험으로 셈 ·
     건너뛴 시험의 요약 줄을 안 읽음(`tools/redgreen.py`) · 앞 `|` 없는 행을 안 봄
     (`tools/scenario_trace.py`)
     + 5라운드 둘: xfail 시험의 태그를 증명으로 셈(`tools/scenario_trace.py`) · add 의 잠금을 뺌
     (`tools/dev_batch.py`)
  #246 격리 검증 — 넷: 새 커밋으로 풀린 제출을 닫힌 PR 잡으로 취소함 · 다시 열린 PR 을 건너뜀 · PR
     목록의 첫 쪽만 읽음 · `guard_merge` 가 저장소 옵션을 한 번만 봄

사용: python scripts/mutcheck.py [--keep] [--only NAME] [--list] [-j N]

`-j N` 은 변이 N 개를 동시에 돌린다(기본 1 — 직렬, `0` 이면 CPU 수의 절반). 변이마다 이미 자기
임시 사본에서 대조군과 변이 pytest 를 돌리므로 사본끼리는 부딪히지 않는다. 같이 쓰는 것은 셋이다.
  - 임시 폴더: 변이마다 자기 `TMPDIR` 을 준다. pytest 의 `pytest-of-<user>` 번호 폴더 정리와 시스템
    임시 폴더를 세는 시험(`rcm-gc-dryrun-*`, `tests/test_offline_gc.py`)이 남의 것을 보지 않게.
  - Chrome: `needs_chrome` 변이는 한 번에 하나만 돈다. 부하 속에 Chrome 을 여럿 띄우면 CDP 가
    마감을 넘겨 대조군이 빨개진다.
  - 고정 포트: `tests/test_mdns_loopback.py` 는 15353 을 나눠 듣는다. 그 파일을 쓰는 변이는 두지
    않는다(`tests/test_mutcheck_parallel.py` 가 지킨다).
결과는 끝난 순서가 아니라 `MUTANTS` 순서로 찍는다 — 직렬과 같은 출력이고 종료 코드 규칙도 같다.
부하가 시간 문턱 시험을 밀 수 있으니 `-j` 를 CPU 수보다 크게 주지 않는다.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTEST_TIMEOUT_SECONDS = 300
#: e2e 하네스가 새 세션으로 띄운 자식의 pid 를 남기는 폴더(tests/test_e2e_worker.py 와 같은 이름)
SESSION_PIDS_ENV = "RCM_TEST_SESSION_PIDS"
# tests/test_web_browser.py 의 `find_chrome()` 과 같은 후보(그 모듈을 import 하면 서버 픽스처까지
# 끌려오므로 여기서 다시 적는다).
CHROME_PATHS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",)
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def chrome_available() -> bool:
    """Chrome 을 찾을 수 있으면 True. `RCM_CHROME` 이 설정돼 있으면 「있어야 한다」는 약속이므로
    찾든 못 찾든 True — 못 찾으면 대조군이 빨개져 실패로 드러난다."""
    if os.environ.get("RCM_CHROME"):
        return True
    return any(Path(p).is_file() for p in CHROME_PATHS) or any(
        shutil.which(n) for n in CHROME_NAMES
    )


@dataclass(frozen=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]
    runner: str = "pytest"  # "pytest" | "node" (tests 는 node --test 에 넘길 경로)
    needs_chrome: bool = False  # True 면 Chrome 이 없을 때 실패가 아니라 SKIP


#: M5r 변이가 겨누는 스킬 템플릿 폴더
T = "src/remote_ci_monitor/skills/rcm-ci-connect/templates/"

MUTANTS = (
    # ── M5p P2 — 보관함의 서버 · HTTP · 보안 (docs/m5p-workplan.md §10) ──
    Mutant(
        # 잡이 만든 파일이 웹 UI 와 같은 출처의 진짜 문서가 된다 — `.html` 하나가 토큰을 읽는다
        # (결정 139)
        name="shelf-file-csp-no-sandbox",
        path="src/remote_ci_monitor/shelf_server.py",
        old="""FILE_CSP = "sandbox; default-src 'none'; """,
        new="""FILE_CSP = "default-src 'none'; """,
        tests=(
            "tests/test_server_m5p_security.py::test_e15_e16_active_documents_are_downloads",
            "tests/test_server_m5p.py::test_b3_an_image_is_served_inline_inside_a_sandbox",
        ),
    ),
    Mutant(
        # 디코딩 뒤 검사 제거 — 라우터의 `..` 검사는 디코딩 전 글자를 보므로 `%2e%2e` 가 지나간다.
        # manifest 가 마지막 문이지만 「안전하지 않은 경로는 400」이 사라진다(§4.6 · E1)
        name="shelf-path-check-before-decode",
        path="src/remote_ci_monitor/shelf_server.py",
        old=(
            "        if not shelf_rules.safe_member(rel):\n"
            '            raise ApiError(400, "bad path")\n'
        ),
        new="",
        tests=(
            "tests/test_server_m5p_security.py::test_e1_to_e7_unsafe_paths_after_decoding_are_400",
        ),
    ),
    Mutant(
        # 보관함 zip 이 M5e 전송 슬롯(2)을 같이 쓴다 — 내려받기 둘이 원격 keep 잡의 묶음 업로드를
        # 503 으로 막고, 워커는 그 503 을 다시 시도하지 않는다(결정 148 · 검토 P0-4)
        name="shelf-zip-uses-m5e-slots",
        path="src/remote_ci_monitor/shelf_server.py",
        old="        self.shelf_slots = threading.BoundedSemaphore(SHELF_TRANSFER_SLOTS)\n",
        new="        self.shelf_slots = self.transfer_slots  # type: ignore[attr-defined]\n",
        tests=("tests/test_server_m5p_remote.py::test_b9_shelf_zips_never_take_the_upload_slots",),
    ),
    Mutant(
        # Host 허용 목록을 건너뜀 — DNS 재바인딩으로 외부 웹페이지가 보관 파일을 읽는다(결정 148)
        name="shelf-host-allowlist-skipped",
        path="src/remote_ci_monitor/shelf_server.py",
        old="        if self._token() is None and not app.shelf_host_ok(host):",
        new="        if False:",
        tests=(
            "tests/test_server_m5p_security.py::test_e22_a_foreign_host_gets_421_on_every_shelf_route",
        ),
    ),
    # ── M5q A0 — 서버 wheel 이 릴리스 wheel 과 같은 파일을 싣는다 (`docs/m5q-workplan.md` §6 S1) ──
    Mutant(
        # skills 를 다시 뺀다 — 서버 wheel 로 깐 클라이언트의 `rcm skills` 가 SKILL.md 를 못 쓴다
        name="client-wheel-drops-skills",
        path="src/remote_ci_monitor/clientwheel.py",
        old='ASSET_DIRS = ("web", "templates", "skills")',
        new='ASSET_DIRS = ("web", "templates")',
        tests=(
            "tests/test_client_wheel.py::"
            "test_s1_the_wheel_carries_every_file_of_the_package_folder",
        ),
    ),
    # ── M5p P1 — 보관함의 규칙 · 저장 · 청소 (docs/m5p-workplan.md §10) ──
    Mutant(
        # 행을 먼저 deleted 로 — 지우다 실패하면 폴더는 남았는데 색인은 「지웠다」(결정 134)
        name="shelf-mark-before-delete",
        path="src/remote_ci_monitor/janitor.py",
        old="""        try:
            remove_under_root(folder, root)
        except OSError as e:
            code = f"delete_{_errname(e)}"
""",
        new="""        self.store.mark_shelf(
            job_id, shelf_rules.STATE_DELETED, now, only_from=live, total_bytes=0
        )
        try:
            remove_under_root(folder, root)
        except OSError as e:
            code = f"delete_{_errname(e)}"
""",
        tests=("tests/test_janitor_m5p.py",),
    ),
    Mutant(
        # 1시간 규칙 제거 — 그림 격자 한 장마다 DB 쓰기가 나간다(§4.3 · 결정 132)
        name="shelf-touch-every-time",
        path="src/remote_ci_monitor/core/shelf.py",
        old="    return float(now) - float(prev_last_used) >= TOUCH_INTERVAL_SECONDS\n",
        new="    return True\n",
        tests=("tests/test_shelf_rules.py", "tests/test_store_m5p.py"),
    ),
    Mutant(
        # ack 미루기 제거 — 소유자가 받자마자 ack 하면 보관이 빈손이 된다(결정 130)
        name="shelf-ack-not-deferred",
        path="src/remote_ci_monitor/store.py",
        old="            if decision.purge and self._shelving(conn, job_id):\n",
        new="            if False:\n",
        tests=("tests/test_store_m5p.py",),
    ),
    Mutant(
        # keep 을 켤 때 ready 묶음을 안 본다 — 원격 잡의 ready 와 finish 사이에 켠 keep 은 행이
        # 영영 안 선다(§15.1 · A102 · A103)
        name="shelf-keep-on-skips-ready-bundle",
        path="src/remote_ci_monitor/store.py",
        old="        if _ready_bundle(conn, job_id):\n",
        new="        if False:\n",
        tests=("tests/test_store_m5p.py",),
    ),
    # ── M5p P3 — CLI · 웹 SPA · 알림 (docs/m5p-workplan.md §10) ──
    Mutant(
        # 옛 서버는 모르는 `keep` 을 거절하지 않고 무시한다 — 감지를 빼면 세션은 오지 않을 링크를
        # 기다린다(결정 143 · 검토 P0-3)
        name="shelf-health-detection-removed",
        path="src/remote_ci_monitor/cli.py",
        old='    if not isinstance(h, dict) or "shelf" not in h:\n'
        '        return f"server does not support --keep ({SHELF_SINCE})"\n',
        new='    if False:\n        return f"server does not support --keep ({SHELF_SINCE})"\n',
        tests=("tests/test_cli_m5p.py",),
    ),
    Mutant(
        # 알림 훅이 보관함 링크를 못 받는다(결정 144)
        name="notify-shelf-url-dropped",
        path="src/remote_ci_monitor/core/notify.py",
        old='        "RCM_SHELF_URL": sanitize_text(shelf_url),\n',
        new="",
        tests=("tests/test_notify_shelf_m5p.py", "tests/test_notify_rules.py"),
    ),
    Mutant(
        # 보관 대기를 0초로 — 복사가 몇 초 걸리는 평범한 잡이 늘 「still copying」 이 된다(결정 143)
        name="shelf-wait-skipped",
        path="src/remote_ci_monitor/cli.py",
        old="    deadline = time.monotonic() + SHELF_WAIT_SECONDS\n",
        new="    deadline = time.monotonic()\n",
        tests=("tests/test_cli_m5p.py",),
    ),
    Mutant(
        # SPA 링크가 public_url 로 만든 절대 주소를 쓴다 — 브라우저가 연 호스트와 다르면 다른
        # 출처로 간다(CG30 · 결정 148)
        name="web-shelf-link-absolute",
        path="src/remote_ci_monitor/web/app.js",
        old='return { text: text, href: shelfHref("/shelf/" + id + "/", lang), cls: s.state };',
        new="return { text: text, href: s.url, cls: s.state };",
        tests=("tests/web/shelf.test.js",),
        runner="node",
    ),
    Mutant(
        # `rcm shelf get` 이 받은 파일의 sha256 을 안 본다 — 잘리거나 바뀐 파일이 그대로 쓰인다(§16)
        name="shelf-get-skips-sha256",
        path="src/remote_ci_monitor/cli.py",
        old=(
            "            if not isinstance(want, str) or "
            "_sha256_file(files / path) != want.lower():\n"
        ),
        new="            if not isinstance(want, str):\n",
        tests=("tests/test_cli_m5p_get.py",),
    ),
    Mutant(
        # 펼침이 macOS tar 의 AppleDouble 멤버를 그대로 만든다 — `._intro.png` 가 깨진 그림으로
        # 항목 페이지에 뜬다(2026-11-20 운영 잡 #3821)
        name="shelf-expand-keeps-apple-double",
        path="src/remote_ci_monitor/shelf_copier.py",
        old=(
            "            if reason is None and shelf_rules.apple_double_member(name):\n"
            "                reason = shelf_rules.SKIP_APPLE_DOUBLE\n"
        ),
        new="",
        tests=("tests/test_shelf_apple_double.py",),
    ),
    # ── M5o A — 같은 브랜치의 옛 잡을 치운다(supersede) ──
    Mutant(
        # 합류자 이름 검사 제거 — 다른 사람이 기다리는 잡을 내 push 가 죽인다(결정 113)
        name="m5o-supersede-ignores-joiners",
        path="src/remote_ci_monitor/core/supersede.py",
        old="if old.requester.name != owner or any(j.name != owner for j in old.joiners):",
        new="if old.requester.name != owner:",
        tests=("tests/test_supersede.py",),
    ),
    Mutant(
        # 업로드 중에 치움 — 새 잡의 업로드가 깨지거나 서버가 다시 뜨면 옛 잡도 새 잡도 없다
        # (결정 111 · A10 · A11)
        name="m5o-supersede-while-uploading",
        path="src/remote_ci_monitor/server.py",
        old="                self._no_supersede.add(job.id)\n",
        new=(
            "                self._no_supersede.add(job.id)\n"
            "        else:\n"
            "            self._supersede_after_queue(job.id)\n"
        ),
        tests=("tests/test_server_supersede.py",),
    ),
    Mutant(
        # 취소를 닫는 길이 N 을 잊는다 — 원격 워커가 `cancelled` 를 보고하면 요약이
        # `cancelled by superseded` 가 되고 `rcm wait` 는 따라갈 번호를 잃는다(결정 114)
        name="m5o-cancel-forgets-successor",
        path="src/remote_ci_monitor/worker.py",
        old="    if successor is not None:\n",
        new="    if False:\n",
        tests=("tests/test_server_supersede.py",),
    ),
    # ── M5o B — 실패를 이름으로 말한다 · 링크에 0.0.0.0 이 없다 (`docs/m5o-workplan.md` §3) ──
    Mutant(
        # 원격 finish 만 옛 요약으로 돌아간다 — 어느 레인이 집었나가 요약 글자를 바꾼다(B7)
        name="failure-summary-remote-old-text",
        path="src/remote_ci_monitor/remote_workers.py",
        old="            code, args = oc.code, oc.args\n",
        new=(
            "            code, args = oc.code, oc.args\n"
            '            if code == "exit_code":\n'
            '                summary, code, args = outcome.summary("exit_code", code=rc)\n'
        ),
        tests=(
            "tests/test_failure_summaries_m5o.py::"
            "test_b7_local_lane_and_remote_worker_write_the_same_summary",
        ),
    ),
    Mutant(
        # `bind` 를 그대로 링크에 넣는다 — 운영 알림 3,721건의 `http://0.0.0.0:8787/...`
        name="notify-link-zero-address",
        path="src/remote_ci_monitor/config.py",
        old="    if bind not in ANY_BINDS:\n",
        new="    if True:\n",
        tests=("tests/test_notify_links_m5o.py",),
    ),
    Mutant(
        # 요약의 첫 이름이 경로 전체다 — 공개 문서에 작업 공간 구조가 실리고 80자가 경로로 찬다
        name="failure-summary-full-path",
        path="src/remote_ci_monitor/core/outcome.py",
        old='    name = parts[-1].strip()[:MAX_FAIL_ARG].strip() if parts else ""',
        new='    name = "/".join(parts)[:MAX_FAIL_ARG].strip() if parts else ""',
        tests=("tests/test_failure_summaries_m5o.py",),
    ),
    # ── M5o C — 프리셋을 파일에서 읽는다 · reload --dry-run (docs/m5o-workplan.md §4) ──
    Mutant(
        # 권한 검사가 없다 — 그룹 · 남이 쓸 수 있는 파일이 서버가 실행할 argv 를 정한다(결정 122)
        name="include-permission-check-removed",
        path="src/remote_ci_monitor/config.py",
        old="    if stat.S_IMODE(st.st_mode) & 0o022:",
        new="    if stat.S_IMODE(st.st_mode) & 0o000:",
        tests=("tests/test_config_include.py",),
    ),
    Mutant(
        # 들인 파일이 `[[repos]]` 를 가질 수 있다 — 토큰 파일 경로가 server.toml 밖으로
        # 샌다(결정 121)
        name="include-allows-repos",
        path="src/remote_ci_monitor/config.py",
        old='_INCLUDE_KEYS = frozenset({"presets"})',
        new='_INCLUDE_KEYS = frozenset({"presets", "repos"})',
        tests=("tests/test_config_include.py",),
    ),
    Mutant(
        # dry-run 이 미리 보기에서 멈추지 않고 대입까지 간다 — 「아무것도 안 바꿈」이
        # 거짓말이 된다(결정 124)
        name="reload-dry-run-assigns",
        path="src/remote_ci_monitor/server.py",
        old='            return {"dry_run": True, **files, **changed}\n',
        new="",
        tests=("tests/test_reload_dry_run.py",),
    ),
    # ── heartbeat 이 방금 넘긴 잡을 봐준다(2026-11-18, CI e2e 의 거짓 lost) ──
    Mutant(
        # 봐주기가 없다 — claim 직전에 찍힌 heartbeat 목록이 방금 넘긴 잡을 lost 로 닫는다
        name="heartbeat-spare-removed",
        path="src/remote_ci_monitor/remote_workers.py",
        old="if j.id not in set(known) and j.id not in spared]",
        new="if j.id not in set(known)]",
        tests=("tests/test_worker_api.py",),
    ),
    Mutant(
        # claim 커밋이 락 밖이다 — 커밋과 기록 사이에 낀 heartbeat 이 다시 lost 를 만든다
        name="heartbeat-spare-claim-outside-lock",
        path="src/remote_ci_monitor/remote_workers.py",
        old="""                with self._fresh_claims_lock:
                    job = self.store.claim(
                        lane,
                        now,
                        pool=pool,
                        worker_name=name,
                        claimant=claimant,
                        now_fn=self.now_fn,  # 시작 마감은 트랜잭션 안의 시각으로(M5s)
                    )
""",
        new=(
            "                job = self.store.claim(\n"
            "                    lane, now, pool=pool, worker_name=name, claimant=claimant,\n"
            "                    now_fn=self.now_fn,\n"
            "                )\n"
            "                with self._fresh_claims_lock:\n"
        ),
        tests=("tests/test_worker_api.py",),
    ),
    # ── claim 이 정지를 같은 트랜잭션에서 다시 본다(2026-11-19, 정지 중 집기) ──
    Mutant(
        # 정지 검사가 없다 — 호출자의 `get_paused` 뒤에 걸린 정지를 놓치고 잡을 집는다
        name="claim-ignores-pause",
        path="src/remote_ci_monitor/store.py",
        old="            if conn.execute(_PAUSED_SQL).fetchone() is not None:\n",
        new="            if False:\n",
        tests=("tests/test_store.py",),
    ),
    # ── 이벤트 버스가 id 순서대로 큐에 넣는다(2026-11-20, PR #238 의 CI 에서 뒤집힘) ──
    Mutant(
        # offer 를 버스 잠금 밖으로 되돌린다 — 두 발행 스레드의 id 가 큐에서 뒤집히고, 뒤의 id 로
        # 다시 붙은 SSE 클라이언트는 앞의 id 를 못 받는다
        name="events-offer-outside-lock",
        path="src/remote_ci_monitor/events.py",
        old=(
            "            for sub in self._subs:\n"
            "                sub._offer(event)\n"
            "        return event\n"
        ),
        new=(
            "            subs = list(self._subs)\n"
            "        for sub in subs:\n"
            "            sub._offer(event)\n"
            "        return event\n"
        ),
        tests=("tests/test_events.py",),
    ),
    Mutant(
        # replay 를 버스 잠금 밖에서 넣는다 — 그 사이 발행된 새 이벤트가 replay 보다 먼저 온다
        name="events-replay-outside-lock",
        path="src/remote_ci_monitor/events.py",
        old=(
            "            for e in replay:\n"
            "                sub._offer(e)\n"
            "            self._subs.append(sub)\n"
            "        return sub\n"
        ),
        new=(
            "            self._subs.append(sub)\n"
            "        for e in replay:\n"
            "            sub._offer(e)\n"
            "        return sub\n"
        ),
        tests=("tests/test_events.py",),
    ),
    # ── M5n — 머신의 능력과 배치(docs/m5n-workplan.md §15) ──
    Mutant(
        # 세대가 다른 보고를 믿는다 — reload 로 바뀐 프리셋을 옛 보고로 판정해 집는다(결정 91)
        name="m5n-stale-generation-trusted",
        path="src/remote_ci_monitor/core/capability.py",
        old="    if report.generation != generation:",
        new="    if False:",
        tests=("tests/test_capability.py", "tests/test_server_m5n.py"),
    ),
    Mutant(
        # 자동 점검의 한 번 제한이 없다 — 실패한 점검이 스위퍼 주기마다 다시 걸린다(결정 95)
        name="m5n-auto-check-repeats",
        path="src/remote_ci_monitor/capabilities.py",
        old="                if self.store.auto_check_done(view.id, name, fp):\n",
        new="                if False:\n",
        tests=("tests/test_checks_sweeper.py",),
    ),
    Mutant(
        # 느린 프로브(timeout)가 지문에 들어간다 — 한 번 느렸을 뿐인데 「환경 바뀜」(결정 90)
        name="m5n-timeout-in-fingerprint",
        path="src/remote_ci_monitor/core/capability.py",
        old="for name, p in probes.items() if p.state != PROBE_TIMEOUT",
        new="for name, p in probes.items() if True",
        tests=("tests/test_capability.py",),
    ),
    Mutant(
        # `host` 범위를 다른 호스트까지 막는 것으로 — 같은 호스트의 두 워커는 그대로 동시에 돈다
        name="m5n-host-scope-wrong-host",
        path="src/remote_ci_monitor/store.py",
        old="(? IS NULL OR b.host_key IS NULL OR b.host_key = ?)",
        new="(? IS NULL OR b.host_key IS NULL OR b.host_key != ?)",
        tests=("tests/test_store_m5n.py",),
    ),
    Mutant(
        # 호스트를 모르면 `host` 범위를 지나친다 — 보고 없는 워커가 같은 컴퓨터의 시뮬레이터 잡과
        # 함께 돈다(§12 fail-open 금지 · 엣지 검증). 자리표시자 수는 그대로 둔다
        name="m5n-host-scope-unknown-passes",
        path="src/remote_ci_monitor/store.py",
        old="(? IS NULL OR b.host_key IS NULL OR b.host_key = ?)",
        new="((0 AND ? IS NULL) OR (b.host_key IS NOT NULL AND b.host_key = ?))",
        tests=("tests/test_m5n_edge.py",),
    ),
    Mutant(
        # 점검 기록 읽기가 다시 jobs 를 통째로 훑는다 — status · claim 이 보존 잡 수에 끌려간다
        # (M5f 결정 49 · 엣지 검증 실측 5만 행에서 628 ms)
        name="m5n-check-records-scan",
        path="src/remote_ci_monitor/store.py",
        old='f"{cols} WHERE worker_name = ? AND preset IN ({marks}){fp_sql} AND {shown} "',
        new='f"{cols} WHERE +worker_name = ? AND preset IN ({marks}){fp_sql} AND {shown} "',
        tests=("tests/test_m5n_edge.py",),
    ),
    Mutant(
        # 단계 중앙값이 다른 호스트로 물러난다 — 느린 호스트에서 「멈춘 듯」을 지어낸다(결정 102)
        name="m5n-step-median-other-host",
        path="src/remote_ci_monitor/core/queue.py",
        old="(host_step_medians.get((host, job.key)) if host is not None else None)",
        new="next((t for (_h, k), t in host_step_medians.items() if k == job.key), None)",
        tests=("tests/test_queue_m5n.py",),
    ),
    Mutant(
        # any-capable 잡을 집어도 풀이 안 바뀐다 — 표본 · 알림 · 풀 표시가 집지 않은 풀로 간다
        name="m5n-capable-claim-keeps-pool",
        path="src/remote_ci_monitor/store.py",
        old=(
            'sets += ", display_pool=COALESCE(display_pool, pool), pool=?"\n'
            "                params.append(pool)"
        ),
        new="pass",
        tests=("tests/test_store_m5n.py", "tests/test_server_m5n.py"),
    ),
    Mutant(
        # 빈 값이 전역 헬퍼(gh 활성 계정)를 비운다. 없으면 git 은 먼저 오는 전역 헬퍼의 토큰을 쓰고
        # 저장소 토큰 파일은 영영 안 읽힌다 — 2026-10-14 사고가 그대로 남는다
        name="repo-token-keeps-global-helper",
        path="src/remote_ci_monitor/gitops.py",
        old='env["GIT_CONFIG_VALUE_0"] = ""',
        new='env["GIT_CONFIG_VALUE_0"] = _credential_helper(credential)',
        tests=("tests/test_gitops_repo_token.py",),
    ),
    Mutant(
        # 제출 경로가 저장소 자격을 안 넘기면 단위 테스트는 다 초록인데 운영은 그대로 깨진다
        name="repo-token-not-passed-on-fetch",
        path="src/remote_ci_monitor/gitops.py",
        old=(
            'what="git fetch",\n        timeout=timeout,\n        log=log,\n'
            "        credential=credential,\n"
        ),
        new='what="git fetch",\n        timeout=timeout,\n        log=log,\n',
        tests=("tests/test_gitops_repo_token.py",),
    ),
    Mutant(
        name="remaining-floor",
        path="src/remote_ci_monitor/core/queue.py",
        old="return max(expected - elapsed, float(cfg.floor_remaining_seconds))",
        new="return expected - elapsed",
        tests=("tests/test_queue.py", "tests/test_progress.py"),
    ),
    Mutant(
        name="join-key-inputs",
        path="src/remote_ci_monitor/core/queue.py",
        old="canonical = [preset, dict(sorted(inputs.items())), source_identity]",
        new="canonical = [preset, source_identity]",
        tests=("tests/test_queue.py",),
    ),
    Mutant(
        name="restart-lost",
        path="src/remote_ci_monitor/store.py",
        old="recover_state = LOST",
        new='recover_state = "succeeded"',
        tests=("tests/test_store.py",),
    ),
    Mutant(
        name="stale-threshold",
        path="src/remote_ci_monitor/core/hostparse.py",
        old="return age > STALE_MULTIPLIER * interval_seconds",
        new="return age > 0 * interval_seconds",
        tests=("tests/test_hostparse.py", "tests/test_status_schema.py"),
    ),
    Mutant(
        name="top-first-sample",
        path="src/remote_ci_monitor/core/hostparse.py",
        old="user_s, sys_s, idle_s = matches[-1]",
        new="user_s, sys_s, idle_s = matches[0]",
        tests=("tests/test_hostparse.py",),
    ),
    Mutant(
        name="web-progress-partial-total",
        path="src/remote_ci_monitor/web/app.js",
        old=(
            "if (isNum(total) && total > 0 && isNum(done) && !(prog && prog.steps_total_partial)) {"
        ),
        new="if (isNum(total) && total > 0 && isNum(done)) {",
        tests=("tests/web/progress_overall.test.js",),
        runner="node",
    ),
    Mutant(
        name="web-progress-default-estimate",
        path="src/remote_ci_monitor/web/app.js",
        old='&& est.source !== "default"',
        new="",
        tests=("tests/web/progress_overall.test.js",),
        runner="node",
    ),
    Mutant(
        name="web-progress-full-bar",
        path="src/remote_ci_monitor/web/app.js",
        old="Math.max(0, Math.min(99, Math.floor(elapsed / expected * 100)))",
        new="Math.max(0, Math.min(100, Math.round(elapsed / expected * 100)))",
        tests=("tests/web/progress_overall.test.js",),
        runner="node",
    ),
    Mutant(
        name="web-not-moving-unknown",
        path="src/remote_ci_monitor/web/app.js",
        old='if (!Array.isArray(q)) return { kind: "unknown", lines: [] };\n    var lines = [];',
        new='if (!Array.isArray(q)) return { kind: "ok", lines: [] };\n    var lines = [];',
        tests=("tests/web/summary.test.js",),
        runner="node",
    ),
    Mutant(
        name="retention-active-guard",
        path="src/remote_ci_monitor/core/retention.py",
        old=(
            "    if state in TERMINAL_STATES:\n"
            "        return policy.failure_days * DAY_SECONDS\n"
            "    return None\n"
        ),
        new="    return policy.failure_days * DAY_SECONDS\n",
        tests=("tests/test_retention.py",),
    ),
    Mutant(
        name="gitref-leading-dash",
        path="src/remote_ci_monitor/core/gitref.py",
        old=(
            '    if ref.startswith("-"):\n'
            "        raise ValueError(\"ref must not start with '-'\")\n"
        ),
        new="",
        tests=("tests/test_gitref.py",),
    ),
    Mutant(
        name="priority-order",
        path="src/remote_ci_monitor/core/queue.py",
        old="key=lambda j: (-j.priority, j.id)",
        new="key=lambda j: j.id",
        tests=("tests/test_priority.py",),
    ),
    Mutant(
        name="admission-fail-open",
        path="src/remote_ci_monitor/core/admission.py",
        old=(
            "    if sample is None or (now - sample.sampled_at).total_seconds()"
            " > cfg.stale_seconds:"
        ),
        new="    if False:",
        tests=("tests/test_admission.py",),
    ),
    Mutant(
        name="admission-samples-any",
        path="src/remote_ci_monitor/core/admission.py",
        old="    if any(v is None for v in values):",
        new="    if all(v is None for v in values):",
        tests=("tests/test_admission.py",),
    ),
    Mutant(
        name="retention-budget-active",
        path="src/remote_ci_monitor/core/retention.py",
        old="        (i for i in inventory if i.evictable),",
        new="        (i for i in inventory),",
        tests=("tests/test_retention_workspace.py",),
    ),
    Mutant(
        name="retention-measure-fail-open",
        path="src/remote_ci_monitor/core/retention.py",
        old="        if v is None:\n            return None\n",
        new="        if v is None:\n            continue\n",
        tests=("tests/test_retention_workspace.py",),
    ),
    Mutant(
        name="retention-budget-unreachable",
        path="src/remote_ci_monitor/core/retention.py",
        old="        if not unreachable:\n",
        new="        if True:\n",
        tests=("tests/test_retention_workspace.py",),
    ),
    Mutant(
        name="manifest-link-escape",
        path="src/remote_ci_monitor/core/manifest.py",
        old=(
            '    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):\n'
            '        raise ManifestError(f"link {path!r}: target escapes the workspace")\n'
        ),
        new="",
        tests=("tests/test_manifest.py",),
    ),
    Mutant(
        name="nowait-view-trusted",
        path="src/remote_ci_monitor/cli.py",
        old=(
            '    if not isinstance(view, dict) or not view.get("state"):\n'
            "        return None\n"
            "    try:\n"
            "        describe(view)  # 그려지는 문서만 쓴다(진짜 줄은 head 만 바꿔 다시 그린다)\n"
            "    except Exception:\n"
            "        return None\n"
            "    return view"
        ),
        new="    return view if isinstance(view, dict) else None",
        tests=("tests/test_nowait_resilience.py",),
    ),
    # ⑰ M5h 결정 63 — 이 폴백이 운영 잡 #162 에서 **성공한 스텝**을 범인으로 지목했다.
    Mutant(
        name="failed-step-fallback",
        path="src/remote_ci_monitor/core/progress.py",
        old="""    failed = next((s.name for s in steps if s.ok is False), None)
""",
        new="""    failed = next((s.name for s in steps if s.ok is False), None)
    if failed is None and exit_code not in (None, 0) and steps:
        failed = steps[-1].name
""",
        tests=("tests/test_progress_m5h.py", "tests/test_progress.py"),
    ),
    # ⑱ M5h 결정 66 — 취소·유실 잡은 아무 말도 안 한다. 분모에 넣으면 간헐 판정이 흐려진다.
    Mutant(
        name="failure-window-cancelled",
        path="src/remote_ci_monitor/store.py",
        old="""        states = (SUCCEEDED, FAILED, TIMED_OUT)
""",
        new="""        states = (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, LOST)
""",
        tests=("tests/test_store_m5h.py",),
    ),
    # ⑲ M5h §2.1 — 증거와 결과는 같은 커밋이다. 대장을 커밋 **뒤로** 옮기면(= 실패한 대장
    # 쓰기가 finish 를 되돌리지 못하면) 빨개져야 한다.
    # ㉔ 결정 73 — 사본 대신 원본을 열면 원본이 마이그레이션된다(user_version 7 → 16). 사고 그 자체.
    Mutant(
        name="offline-gc-opens-original",
        path="src/remote_ci_monitor/cli.py",
        old="        store = Store(copy, log=_err)",
        new="        store = Store(db, log=_err)",
        tests=("tests/test_offline_gc.py",),
    ),
    # ㉕ 결정 78 — 「대장 행이 하나도 없다」 조건을 빼면 새 코드의 선언 라벨도 옮겨진다.
    Mutant(
        name="v16-without-ledger-check",
        path="src/remote_ci_monitor/store.py",
        old=""""WHERE state IN ('failed','timed_out') AND failed_step IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM job_failures WHERE job_failures.job_id=jobs.id)",""",
        new=""""WHERE state IN ('failed','timed_out') AND failed_step IS NOT NULL",""",
        tests=("tests/test_store_m5i.py",),
    ),
    # ㉗ M5l L1 — `_MIGRATIONS` 의 SQL 이 죽으면 `StoreError` 가 아니라 `sqlite3.Error` 다.
    # 그걸 안 잡으면 업그레이드 전에 잡으라던 바로 그 실패가 트레이스백이 된다(리뷰 #87 B P1).
    Mutant(
        name="offline-gc-ignores-sqlite-error",
        path="src/remote_ci_monitor/cli.py",
        old="""임시 디렉터리 안
    except (StoreError, sqlite3.Error) as e:
""",
        new="""임시 디렉터리 안
    except StoreError as e:
""",
        tests=("tests/test_offline_gc.py",),
    ),
    # ㉖ M5i I8 결정 81 — 정확한 파일명 검사를 빼면 이름이 달라도 200.
    Mutant(
        name="client-wheel-any-name",
        path="src/remote_ci_monitor/server.py",
        old="        if name != expected:\n            raise ApiError(\n                404,",
        new="        if False:\n            raise ApiError(\n                404,",
        tests=("tests/test_client_wheel.py",),
    ),
    Mutant(
        name="cancel-capability-skipped",
        path="src/remote_ci_monitor/server.py",
        old="if self.config.server.cancel_requires_submission_token and not token.admin:",
        new="if False and not token.admin:",
        tests=("tests/test_cancel_capability.py",),
    ),
    # ㉘ M5l S6 — leave 비밀은 그것을 받은 토큰 이름만 쓸 수 있다. 확인을 빼면 남의 비밀로 남을
    # 뺀다(리뷰 pr-110 B P1).
    Mutant(
        name="leave-binding-removed",
        path="src/remote_ci_monitor/server.py",
        old="                if participant != token.name:",
        new="                if False:",
        tests=("tests/test_cancel_capability_m5l.py",),
    ),
    Mutant(
        name="ledger-outside-tx",
        path="src/remote_ci_monitor/store.py",
        old="""            for seq, name in enumerate(fail_names, start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO job_failures(job_id, name, seq) VALUES (?,?,?)",
                    (job_id, name, seq),
                )
            # 원격 잡의 보관 행은 묶음이 ready 일 때(잡이 아직 running 일 때) 섰다 — 잡 상태와 끝난
            # 시각은 위의 `_set_state` 가 같은 트랜잭션에서 채웠다(M5p §15.1).
            if bundle is not None:
                _upsert_bundle(
                    conn,
                    job_id,
                    bundle,
                    now=now,
                    ttl_hours=ttl_hours,
                    shelf_enabled=self.shelf_enabled,
                    shelf_available=self.shelf_available,
                )
            conn.execute("COMMIT")
""",
        new="""            if bundle is not None:
                _upsert_bundle(
                    conn,
                    job_id,
                    bundle,
                    now=now,
                    ttl_hours=ttl_hours,
                    shelf_enabled=self.shelf_enabled,
                    shelf_available=self.shelf_available,
                )
            conn.execute("COMMIT")
            for seq, name in enumerate(fail_names, start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO job_failures(job_id, name, seq) VALUES (?,?,?)",
                    (job_id, name, seq),
                )
""",
        tests=("tests/test_store_m5h.py",),
    ),
    # ㉓ M5i 결정 76 — 공유 블록은 **일반 파일**의 `nlink > 1` 만이다. 디렉터리를 세면 ext4 에서
    # 모든 워크스페이스가 「지워도 안 는다」 쪽으로 새고, 바닥 규칙이 필요 이상 지운다.
    Mutant(
        name="retention-shared-any-inode",
        path="src/remote_ci_monitor/janitor.py",
        old="    if st.st_nlink > 1 and stat.S_ISREG(st.st_mode):\n",
        new="    if st.st_nlink > 1:\n",
        tests=("tests/test_janitor_m5i.py",),
    ),
    # ㉗ M5l L5 · 리뷰 pr-94 B-1 — 포트를 못 잡으면 DB 를 열지도 않는다. 순서를 되돌리면(먼저
    # Store, 그 다음 bind) 「Address already in use」 뒤에 DB 는 이미 새 버전이다.
    Mutant(
        name="serve-store-before-bind",
        path="src/remote_ci_monitor/server.py",
        old="""    began = time.monotonic()
    httpd = RcmHTTPServer((config.server.bind, config.server.port))
    bound_at = time.monotonic()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        store = Store(data_dir / "rcm.sqlite3")
    except BaseException:
        httpd.server_close()
        raise
""",
        # 시각 변수는 남긴다 — 변이본이 NameError 로 죽으면 엉뚱한 이유로 빨개진다
        new="""    began = time.monotonic()
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "rcm.sqlite3")
    httpd = RcmHTTPServer((config.server.bind, config.server.port))
    bound_at = time.monotonic()
""",
        tests=("tests/test_cli_serve_refusal.py",),
        # ㉗ M5l L2 — 크기를 모르는 채 지운 항목은 **개수로** 따로 센다. 0 으로 섞으면 영수증이
        # 「freed 0 B from 1 jobs」라고 확정한다(리뷰 #88 B1 — 실제 호출에서 그렇게 찍혔다).
    ),
    Mutant(
        name="retention-unknown-deleted-as-zero",
        path="src/remote_ci_monitor/janitor.py",
        old=(
            "            if item.bytes is None:\n"
            "                unknown += 1\n"
            "            else:\n"
            "                charged += item.bytes\n"
        ),
        new="            charged += item.bytes or 0\n",
        tests=("tests/test_janitor_m5l.py",),
        # ㉗ M5l L7 · pr-82 리뷰 P2 — 수집기 설치를 지우면 `page_errors()` 가 「page error collector
        # missing」으로 빨개져야 한다. 예전에는 `[]` 를 돌려줘 I5 의 0건 단언이 그대로 초록이었다.
    ),
    Mutant(
        name="web-page-error-collector-removed",
        path="tests/test_web_browser.py",
        old="""        self.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": PAGE_ERROR_COLLECTOR_JS},
            timeout=COLD_START_SECONDS,
        )
""",
        new="",
        tests=(
            "tests/test_web_browser.py::"
            "test_local_host_card_draws_the_disk_and_the_rcm_data_line_without_page_errors",
            "tests/test_web_browser.py::"
            "test_remote_worker_sample_is_a_host_card_and_recent_has_no_pool_host_header",
        ),
        needs_chrome=True,
        # ㉔ M5j G1 결정 84 — 마커 조회가 깨지면 `step_timeline` 은 `null` + 코드다. 빈 타임라인으로
        # 뭉개면 「스텝을 안 찍은 잡」과 「못 읽은 잡」이 같은 모양이 된다(fail-open 금지).
    ),
    Mutant(
        name="step-timeline-db-error-empty",
        path="src/remote_ci_monitor/server.py",
        old="""        except Exception as e:  # noqa: BLE001 — 깨진 payload 행도 DB 오류다
            doc["step_timeline"] = None
            doc["step_timeline_error_code"] = _error_code(e)
            return set()
""",
        new="""        except Exception:  # noqa: BLE001
            doc["step_timeline"] = step_timeline_json(None)
            return set()
""",
        tests=("tests/test_step_timeline.py",),
    ),
    # ㉗ M5j G4 결정 85 — 검사가 아무것도 「없다」고 하지 않으면 없는 도구로도 프로세스가 뜬다.
    Mutant(
        name="requires-check-skipped",
        path="src/remote_ci_monitor/runner.py",
        old="    return [name for name in requires if find_tool(name, path) is None]\n",
        new="    return []  # noqa: mutant\n",
        tests=("tests/test_requires.py",),
    ),
    # ㉘ M5l S1 — 상대 PATH 항목은 프로세스가 뜨는 워크스페이스 기준이다. 검사 프로세스의 cwd 로
    # 풀면 서버 폴더의 `tools/fvm` 으로 통과하고 워크스페이스의 것은 못 찾는다(fail-open).
    Mutant(
        name="requires-path-server-cwd",
        path="src/remote_ci_monitor/runner.py",
        old="    base = os.fspath(cwd) if cwd is not None else os.getcwd()\n",
        new="    base = os.getcwd()  # noqa: mutant\n",
        tests=("tests/test_requires_m5l.py",),
    ),
    # ㉙ M5l S4 — preflight 직전의 취소 확인이 없으면 자재화 중 취소한 잡이 `tool_missing` 이 된다.
    Mutant(
        name="requires-cancel-ignored",
        path="src/remote_ci_monitor/runner.py",
        old="            if observer.should_cancel():\n                at = now_fn()\n",
        new="            if False:  # noqa: mutant\n                at = now_fn()\n",
        tests=("tests/test_requires_m5l.py",),
    ),
    Mutant(
        name="web-host-panel-follows-load",
        path="src/remote_ci_monitor/web/app.js",
        old=(
            'var want = choice === "open" ? true : choice === "closed" ? false '
            ": (d.alert || det.open);"
        ),
        new='var want = choice === "open" ? true : choice === "closed" ? false : d.warn;',
        tests=(
            "tests/test_web_browser.py::"
            "test_a_busy_host_does_not_open_the_host_panel_and_flipping_the_verdict_moves_nothing",
            "tests/test_web_browser.py::"
            "test_a_stale_sample_opens_the_host_panel_once_and_nothing_closes_it_again",
        ),
        needs_chrome=True,
    ),
    Mutant(
        name="web-host-busy-no-history",
        path="src/remote_ci_monitor/web/app.js",
        old='var limit = prev === "busy" ? BUSY_OFF : BUSY_ON;',
        new="var limit = BUSY_ON;",
        tests=("tests/web/host_hysteresis.test.js",),
        runner="node",
    ),
    Mutant(
        name="step-stuck-ignores-step-median",
        path="src/remote_ci_monitor/core/queue.py",
        old="        step_stuck = current_seconds > threshold",
        new="        step_stuck = silent",
        tests=("tests/test_queue.py",),
    ),
    Mutant(
        name="web-quiet-is-actionable",
        path="src/remote_ci_monitor/web/app.js",
        old='"blocked_by_group", "overdue", "paused"];',
        new='"blocked_by_group", "overdue", "paused", "quiet"];',
        tests=("tests/web/summary.test.js",),
        runner="node",
    ),
    Mutant(
        name="web-progress-sub-full-bar",
        path="src/remote_ci_monitor/web/app.js",
        old="out.pct = Math.min(99, pctOf(sub.done, sub.total));",
        new="out.pct = pctOf(sub.done, sub.total);",
        tests=("tests/web/progress_sub.test.js",),
        runner="node",
    ),
    Mutant(
        name="progress-sub-not-reset-by-step",
        path="src/remote_ci_monitor/core/progress.py",
        old="            sub, units, unit_pos, units_truncated = None, [], {}, False\n",
        new="",
        tests=("tests/test_progress_sub.py",),
    ),
    Mutant(
        name="version-settle-ignores-what-is-still-running",
        path="src/remote_ci_monitor/server.py",
        old="        busy = self._version_busy(row)",
        new="        busy = None",
        # 파일 전체가 아니라 이 둘 — 그 파일의 문서 잠금 절은 `docs/` 가 없는 사본에서 빨갛다
        tests=(
            "tests/test_server_release_versions.py::"
            "test_a_job_that_ends_during_a_round_leaves_the_row_running",
            "tests/test_server_release_versions.py::"
            "test_a_round_that_ends_first_waits_for_the_job_that_is_still_running",
        ),
    ),
    # ── M5s PR B — status · golive 역할 (docs/m5s-workplan.md §4.4 · §4.6) ──
    Mutant(
        # golive 의 release 를 되돌릴 수 없는 모드에서 뺀다 — 기본값이 release 인 프리셋을
        # rcm check 가 통과시켜, 손으로 한 번 돌린 golive 가 출시가 된다
        name="m5s-golive-release-not-irreversible",
        path="src/remote_ci_monitor/cli.py",
        old='"review": "submit", "golive": "release"}',
        new='"review": "submit"}',
        # test_config_release_profile 은 사본에 없는 examples/ 를 읽는다 — 이 시험만 쓴다
        tests=(
            "tests/test_skills_templates.py::"
            "test_a_golive_preset_defaulting_to_release_fails_the_release_row",
        ),
    ),
    Mutant(
        # status.json 을 checked_at 이 아니라 measured_at 으로 잰다 — 늘 잡이 끝난 시각이 되어
        # 오래된 상태가 새것처럼 보인다
        name="m5s-status-age-ignores-checked-at",
        path="src/remote_ci_monitor/release_state.py",
        old='MEASURED_KEY: dict[str, str] = {"status.json": "checked_at"}',
        new="MEASURED_KEY: dict[str, str] = {}",
        tests=(
            "tests/test_release_state.py::"
            "test_status_is_the_latest_status_json_aged_from_checked_at",
        ),
    ),
    # ── M5s PR F — golive 라우트 (docs/m5s-workplan.md §4.5 · §7) ──
    Mutant(
        # golive release 의 admin 검사를 없앤다 — 클라이언트 토큰 하나로 출시를 걸 수 있다
        name="m5s-golive-release-without-admin",
        path="src/remote_ci_monitor/server.py",
        old='        if not token.admin:\n            raise ApiError(403, "going live',
        new='        if False:\n            raise ApiError(403, "going live',
        tests=("tests/test_release_golive.py::test_release_needs_an_admin_token",),
    ),
    Mutant(
        # 2(미승인)에도 반복한다 — golive 가 75 대신 2 로 승인을 기다리게 되고, 승인을 감시하는
        # 자동 출시가 된다(결정 201 · 236, #255 재검증 P2)
        name="m5s-golive-retries-not-approved",
        path="src/remote_ci_monitor/core/model.py",
        old="            and rc == self.retry_on_exit\n",
        new="            and rc in (self.retry_on_exit, 2)\n",
        tests=(
            "tests/test_release_golive.py::test_a_golive_release_that_ends_1_2_or_3_is_never_retried",
        ),
    ),
    # ── M5s PR E — status 라우트 · 역할별 창 · 검토 패널 (docs/m5s-workplan.md §4.4 · §3 #11) ──
    Mutant(
        # 역할 프리셋 잡 500개를 다시 한 창으로 읽는다 — 자주 도는 status 잡이 그 창을 채우면
        # plan · review 의 최신 문서가 null 이 되고 되돌릴 수 없는 문이 닫힌다
        name="m5s-release-view-one-shared-window",
        path="src/remote_ci_monitor/server.py",
        old="self.store.list_jobs_by_preset([preset_name], window)",
        new="self.store.list_jobs_by_preset([n for n in presets.values() if n], 500)",
        tests=(
            "tests/test_release_status_panel.py::"
            "test_release_status_panel_frequent_status_jobs_do_not_push_the_plan_out",
        ),
    ),
    Mutant(
        # status 행이 jobs[] 의 50 자리를 같이 쓴다 — 다른 역할의 행과 그 끝난 시각이 사라진다
        name="m5s-status-rows-crowd-jobs",
        path="src/remote_ci_monitor/release_state.py",
        old="            wants_row = status_rows < STATUS_ROWS_LIMIT\n",
        new="            wants_row = len(rows) < JOBS_LIMIT\n",
        tests=(
            "tests/test_release_status_panel.py::test_release_status_panel_status_rows_do_not_crowd_jobs",
        ),
    ),
    Mutant(
        # 필요 없는 잡의 묶음 목록까지 읽는다 — 5분마다 도는 status 잡 500개를 매 조회마다 다 연다
        name="m5s-release-view-reads-every-bundle",
        path="src/remote_ci_monitor/release_state.py",
        old="        if not wants_row and not missing:\n",
        new="        if False:\n",
        tests=(
            "tests/test_release_status_panel.py::"
            "test_release_status_panel_unneeded_status_bundles_are_not_read",
        ),
    ),
    Mutant(
        # 도는 status 잡이 Build·upload 행의 「지금 하는 일」이 된다
        name="m5s-web-status-job-is-what-runs-now",
        path="src/remote_ci_monitor/web/app.js",
        old='return j && isNum(j.id) && j.role !== "status"; })',
        new="return j && isNum(j.id); })",
        tests=("tests/web/store_status.test.js",),
        runner="node",
    ),
    Mutant(
        # 목록에 없는 상태 값을 그대로 믿는다 — `APPROVED` 같은 원문이 승인처럼 그려질 수 있다
        name="m5s-web-store-state-outside-the-list",
        path="src/remote_ci_monitor/web/app.js",
        old='var st = d && STORE_CHECK_STATES.indexOf(d.state) >= 0 ? d.state : "unknown";',
        new='var st = d && d.state ? String(d.state).toLowerCase() : "unknown";',
        tests=("tests/web/store_status.test.js",),
        runner="node",
    ),
    Mutant(
        # 행이 찼으면 문서 찾기까지 건너뛴다 — 묶음 없는 status 잡 셋이나 새 다른 역할 잡 50개
        # 뒤의 status.json · plan.json 을 잃는다(#254 격리 검증 P1)
        name="m5s-release-view-skips-documents-when-rows-are-full",
        path="src/remote_ci_monitor/release_state.py",
        old="        if not wants_row and not missing:\n",
        new="        if not wants_row:\n",
        tests=(
            "tests/test_release_status_panel.py::"
            "test_release_status_panel_full_status_rows_still_find_an_older_status_document",
            "tests/test_release_status_panel.py::"
            "test_release_status_panel_full_rows_still_find_an_older_plan_document",
        ),
    ),
    Mutant(
        # 프리셋마다 읽은 잡을 합친 뒤 새것부터 다시 세우지 않는다 — jobs[] 가 프리셋 순서가 되고
        # 「역할의 최신」이 옛 잡이 된다(#254 격리 검증 P2)
        name="m5s-release-view-merged-jobs-unsorted",
        path="src/remote_ci_monitor/server.py",
        old="        jobs = [by_id[i] for i in sorted(by_id, reverse=True)]\n",
        new="        jobs = list(by_id.values())\n",
        tests=(
            "tests/test_release_status_panel.py::"
            "test_release_status_panel_jobs_from_several_presets_are_newest_first",
        ),
    ),
    Mutant(
        # 실패한 「새로 조회」를 모른 척한다 — 예전 상태가 아무 말 없이 그려진다
        name="m5s-web-failed-check-silent",
        path="src/remote_ci_monitor/web/app.js",
        old="var lastFailed = newest && STORE_CHECK_FAILED[newest.state] &&",
        new="var lastFailed = false && newest && STORE_CHECK_FAILED[newest.state] &&",
        tests=("tests/web/store_status.test.js",),
        runner="node",
    ),
    # ── M5q PR A — 클라이언트가 서버 버전을 따라간다 (docs/m5q-workplan.md §6) ──
    Mutant(
        # 받은 바이트의 해시를 안 본다 — 깨지거나 바뀐 wheel 이 캐시에 들어가 실행된다(결정 157)
        name="m5q-follow-sha-unchecked",
        path="src/remote_ci_monitor/follow.py",
        old="    if digest.hexdigest() != info.sha256:\n",
        new="    if False:\n",
        tests=("tests/test_follow_cache.py",),
    ),
    Mutant(
        # 기본값의 설치 종류 판정을 뒤집는다 — 개발 체크아웃이 운영 버전으로 바꿔치인다(결정 156)
        name="m5q-follow-editable-default-flipped",
        path="src/remote_ci_monitor/follow.py",
        old='MODE_FOLLOW if kind == "wheel" else MODE_INSTALLED',
        new='MODE_FOLLOW if kind != "wheel" else MODE_INSTALLED',
        tests=("tests/test_follow_rules.py",),
    ),
    Mutant(
        # 따라간 자식이 다시 따라간다 — 버전이 어긋나면 exec 고리가 된다(결정 161)
        name="m5q-follow-boot-loop",
        path="src/remote_ci_monitor/cli.py",
        old="    if follow.booted() is not None:\n",
        new="    if False:\n",
        tests=("tests/test_follow_cli.py",),
    ),
    Mutant(
        # 임시 폴더 없이 대상 폴더에 바로 푼다 — 풀다 실패하면 반쯤 푼 폴더가 남는다(§2.5)
        name="m5q-follow-unpack-in-place",
        path="src/remote_ci_monitor/follow.py",
        old='        tree = tmp / "tree"\n',
        new="        tree = clients / info.sha256[:16]\n",
        tests=("tests/test_follow_cache.py",),
    ),
    Mutant(
        # `rcm serve` 가 따라간다 — 서버가 정본이다(§2.3)
        name="m5q-follow-serve-follows",
        path="src/remote_ci_monitor/follow.py",
        old=(
            '    {"serve", "worker", "token", "init", "discover", "version", "skills", "doctor", '
            '"setup"}'
        ),
        new='    {"worker", "token", "init", "discover", "version", "skills", "doctor", "setup"}',
        tests=("tests/test_follow_rules.py",),
    ),
    Mutant(
        # health 의 wheel 경로를 그대로 믿는다 — 다른 경로 · 다른 버전의 파일을 받는다(R ② · ⑦)
        name="m5q-follow-health-path-unchecked",
        path="src/remote_ci_monitor/follow.py",
        old='    if path != "/client/" + wheel_filename(version):\n',
        new="    if False:\n",
        tests=("tests/test_follow_rules.py",),
    ),
    Mutant(
        # 받기 전체 제한을 버린다 — 응답 머리를 조금씩 흘리는 서버가 명령을 붙잡는다(#226 D1)
        name="m5q-follow-fetch-no-deadline",
        path="src/remote_ci_monitor/follow.py",
        old='    if got is _TIMEOUT:\n        raise FollowError("unreachable")\n',
        new="    if False:\n        pass\n",
        tests=("tests/test_follow_cache.py",),
    ),
    Mutant(
        # rename 에 진 쪽이 `.bad` 폴더를 받아들인다 — 남이 둔 폴더의 코드가 돈다(#226 D2)
        name="m5q-follow-race-loser-unchecked",
        path="src/remote_ci_monitor/follow.py",
        old="        entry = _entry_at(target, include_bad=False)\n",
        new="        entry = _entry_at(target, include_bad=True)\n",
        tests=("tests/test_follow_cache.py",),
    ),
    Mutant(
        # 캐시 뿌리의 부모가 남에게 열려 있어도 쓴다 — 남이 뿌리를 바꿔칠 수 있다
        name="m5q-follow-cache-parent-unchecked",
        path="src/remote_ci_monitor/follow.py",
        old="    if st is not None and st.st_mode & stat.S_IWOTH and",
        new="    if False and st is not None and st.st_mode & stat.S_IWOTH and",
        tests=("tests/test_follow_cache.py",),
    ),
    Mutant(
        # `rcm check` 의 client 행이 서버가 보낸 version 을 그대로 찍는다(#226 D3)
        name="m5q-check-row-unclean-version",
        path="src/remote_ci_monitor/cli.py",
        old=(
            "    server_v = follow.clean(raw)\n"
            "    mine, theirs = _version_key(__version__), _version_key(server_v)\n"
        ),
        new=(
            "    server_v = raw\n"
            "    mine, theirs = _version_key(__version__), _version_key(server_v)\n"
        ),
        tests=("tests/test_follow_cli.py",),
    ),
    Mutant(
        # 클라이언트 버전 한 줄을 인증 전에 쓴다 — 토큰 없는 요청이 서버 로그를 채운다(결정 168)
        name="m5q-client-log-before-auth",
        path="src/remote_ci_monitor/server.py",
        old=(
            "            t = self.app.require_client_token(self._token())\n"
            "            status, body = self.app.submit(self._json_body(), t, "
            'host=self.headers.get("Host"))\n'
            '            self.app.note_client(t.name, self.headers.get("User-Agent"))\n'
        ),
        new=(
            '            self.app.note_client("anonymous", self.headers.get("User-Agent"))\n'
            "            t = self.app.require_client_token(self._token())\n"
            "            status, body = self.app.submit(self._json_body(), t, "
            'host=self.headers.get("Host"))\n'
        ),
        tests=("tests/test_server_client_version_log.py",),
    ),
    Mutant(
        # 본 쌍을 기억하지 않는다 — 제출마다 한 줄이 남는다
        name="m5q-client-log-every-submit",
        path="src/remote_ci_monitor/server.py",
        old="            if pair in self._client_pairs:\n",
        new="            if False:\n",
        tests=("tests/test_server_client_version_log.py",),
    ),
    Mutant(
        # User-Agent 의 버전 형식을 안 본다 — 제어 문자 · 긴 값이 서버 로그에 그대로 찍힌다
        name="m5q-client-log-unchecked-agent",
        path="src/remote_ci_monitor/server.py",
        old="        if m is None or not valid_version(m.group(1)):\n",
        new="        if m is None:\n",
        tests=("tests/test_server_client_version_log.py",),
    ),
    Mutant(
        # 정보가 없어도 `rcm version` 둘째 줄을 쓴다 — 한 줄을 읽는 스크립트가 깨진다(결정 166)
        name="m5q-version-second-line-always",
        path="src/remote_ci_monitor/cli.py",
        old='    if setting.source == "default" and not (',
        new="    if False and not (",
        # test_cli_m4 는 사본에 없는 examples/ 를 읽어 대조군이 빨갛다 — 표시 시험만 쓴다
        tests=("tests/test_cli_m5q_display.py",),
    ),
    Mutant(
        # `rcm check` 의 `client` 행이 따라가기 상태를 버린다
        name="m5q-check-row-drops-follow-tail",
        path="src/remote_ci_monitor/cli.py",
        old="    tail, hint_ok = _follow_tail(state)\n",
        new="    tail, hint_ok = _follow_tail(None)\n",
        tests=("tests/test_cli_m5q_display.py",),
    ),
    # ── M5q PR B1 — 원격 워커가 기동과 기동 등록 409 에서 따라간다 (docs/m5q-workplan.md §6) ──
    Mutant(
        # 등록이 버전을 보기 **전에** 그 워커의 도는 잡을 lost 로 닫는다 — 따라가기 전의 409 가
        # 잡을 잃게 한다(시나리오 b B17)
        name="m5q-worker-409-after-lost",
        path="src/remote_ci_monitor/remote_workers.py",
        old="        if version != self.version:\n            # 이 검사는",
        new=(
            '        self.store.mark_lost_for_worker(token.name, self.now_fn(), "lost")\n'
            "        if version != self.version:\n            # 이 검사는"
        ),
        tests=("tests/test_server_m5q_worker.py",),
    ),
    Mutant(
        # 409 본문에 `server_version` 을 싣지 않는다 — 워커가 ② 에서 따라갈 버전을 모른다(§5.3)
        name="m5q-worker-409-no-server-version",
        path="src/remote_ci_monitor/remote_workers.py",
        old="                server_version=self.version,\n",
        new="",
        tests=("tests/test_server_m5q_worker.py", "tests/test_cli_worker_m5q.py"),
    ),
    Mutant(
        # 기동 ① 이 10분 기록을 쓴다 — 서버가 올라간 뒤 재시작한 워커가 옛 기록을 믿는다(§13 P2-7)
        name="m5q-worker-start-uses-the-record",
        path="src/remote_ci_monitor/cli.py",
        old=(
            "        fresh=True,\n        environ=os.environ,\n"
            "        execve=args.follow_execve,\n        kind=kind,\n"
        ),
        new=(
            "        fresh=False,\n        environ=os.environ,\n"
            "        execve=args.follow_execve,\n        kind=kind,\n"
        ),
        tests=("tests/test_cli_worker_m5q.py",),
    ),
    Mutant(
        # 레인 재등록이 막혀도 종료 0 — 감독자가 다시 띄우지 않아 워커가 조용히 멈춘다(§14 B질문1)
        name="m5q-worker-lane-exit-zero",
        path="src/remote_ci_monitor/remote_worker.py",
        old="                            self.exit_code = x.code\n",
        new="                            pass\n",
        tests=("tests/test_cli_worker_m5q.py",),
    ),
    Mutant(
        # `WorkerFollow` 가 `WorkerExit` 가 아니다 — B2 이전 빌드에서 레인 재등록 409 가 레인
        # 스레드를 예외로 죽인다(§14 B질문2)
        name="m5q-worker-follow-not-a-worker-exit",
        path="src/remote_ci_monitor/remote_worker.py",
        old="class WorkerFollow(WorkerExit):\n",
        new="class WorkerFollow(Exception):\n",
        tests=("tests/test_cli_worker_m5q.py",),
    ),
    # ── M5r PR A — rcm-ci-connect 스킬 템플릿 (docs/m5r-workplan.md §6) ──
    Mutant(
        # 포크 PR 을 rcm 모드로 둔다 — 남의 코드가 빌드 머신에서 돈다(결정 186)
        name="m5r-resolve-trusts-forks",
        path=T + "rcm_ci_config.py",
        old="    if fork:\n",
        new="    if False:\n",
        tests=("tests/test_ci_connect_config.py",),
    ),
    Mutant(
        # 틀린 설정을 「파일 없음」처럼 지나간다 — bad_file 이 경고 없이 사라진다
        name="m5r-bad-file-not-github",
        path=T + "rcm_ci_config.py",
        old="    if error is not None:\n",
        new="    if False:\n",
        tests=("tests/test_ci_connect_config.py",),
    ),
    Mutant(
        # rcm-only 갈래를 대체 갈래에 넣는다 — macOS 러너 부족이 dev 머지를 다시 막는다(결정 195)
        name="m5r-rcm-only-leg-in-late",
        path=T + "rcm_ci_config.py",
        old='if where(leg) == "rcm") if cfg.fallback',
        new='if where(leg) in ("rcm", "rcm-only")) if cfg.fallback',
        tests=("tests/test_ci_connect_config.py",),
    ),
    Mutant(
        # 대기 잡이 rcm 의 failure 를 대체로 읽는다 — 빨간 코드가 ubuntu 갈래로 통과한다(결정 180)
        name="m5r-wait-failure-falls-back",
        path=T + "rcm_ci_wait.py",
        old='return Waited("fail", "failed", job)',
        new='return Waited("fallback", "failed", job)',
        tests=("tests/test_ci_connect_wait.py",),
    ),
    Mutant(
        # creators 거르기를 지운다 — 아무 계정이 올린 success 로 통과한다(결정 179)
        name="m5r-creators-not-filtered",
        path=T + "rcm_ci_config.py",
        old="        elif creators and login not in creators:\n            continue\n",
        new="",
        tests=("tests/test_ci_connect_wait.py",),
    ),
    Mutant(
        # verdict 가 `ran` 을 보지 않는다 — 아무것도 안 돈 대체가 success 로 보인다(검토 P0-2)
        name="m5r-verdict-ignores-ran",
        path=T + "rcm_ci_wait.py",
        old='if job(name).get("result") == "success" and expected and ran == expected:',
        new='if job(name).get("result") == "success":',
        tests=("tests/test_ci_connect_wait.py",),
    ),
    Mutant(
        # 훅이 GitHub 이 넘겨받은 커밋을 덮는다(결정 185)
        name="m5r-hook-overwrites-taken-over",
        path=T + "rcm_ci_status.py",
        old='    if top is not None and cc.read_word(top.get("description"))[0] == "taken-over":\n',
        new="    if False:\n",
        tests=("tests/test_ci_connect_status.py",),
    ),
    Mutant(
        # 환경 탓 실패(종료 75)를 코드의 실패로 올린다 — 빨강 하나가 dev 머지를 막는다(결정 189)
        name="m5r-exit-75-is-failure",
        path=T + "rcm_ci_config.py",
        old="        if exit_code == EX_TEMPFAIL:\n",
        new="        if False:\n",
        tests=("tests/test_ci_connect_config.py", "tests/test_ci_connect_status.py"),
    ),
    Mutant(
        # 감시가 포크 PR 을 받는다 — 남의 코드를 rcm 에 낸다(결정 186)
        name="m5r-watch-takes-fork-prs",
        path=T + "rcm_ci_watch.py",
        old='            if cc.is_fork(self.repo.github, p.get("head_repo"), p.get("author")):\n',
        new="            if False:\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    # ── M5r R15 — 대체 없이 rcm 만 쓰는 레포 (docs/m5r-workplan.md §13) ──
    Mutant(
        # 잡 스크립트가 올린 결과 위에 감시가 running 을 올린다. 결과가 가려진다(결정 198)
        name="m5r-watch-pending-over-a-result",
        path=T + "rcm_ci_watch.py",
        old='        if top is not None and top.get("state") != "pending":\n',
        new="        if False:\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    Mutant(
        # 잡 스크립트의 pending 을 감시의 running 으로 덮는다(결정 198)
        name="m5r-watch-overwrites-the-job-pending",
        path=T + "rcm_ci_watch.py",
        old="        ours = top is None or word in OWN_PENDING\n",
        new="        ours = True\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    Mutant(
        # 대체 없는 레포에서도 멈춘 잡을 취소한다. 배포 때 큐를 멈추면 PR 이 error 다(결정 197)
        name="m5r-watch-no-fallback-still-cancels",
        path=T + "rcm_ci_watch.py",
        old='        if reason in STUCK_REASONS and entry.get("fallback") == "none":\n',
        new="        if False:\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    # ── #245 격리 검증이 남긴 후속 (docs/m5r-workplan.md §13.8) ──
    Mutant(
        # 서버 재시작 순간의 제출 실패를 다시 내지 않고 곧바로 error 로 둔다. 다음 push 까지 머지가
        # 막힌다
        name="m5r-watch-no-submit-retry",
        path=T + "rcm_ci_watch.py",
        old="            if reason in RETRY_REASONS and tries < SUBMIT_RETRIES:\n",
        new="            if False:\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    Mutant(
        # `--check` 가 서비스 venv 의 rcm 을 알아보지 못한다. 배포 중 checkout 이 바뀌는 사이 제출이
        # 실패할 수 있다
        name="m5r-watch-check-misses-service-venv",
        path=T + "rcm_ci_watch.py",
        old="    if found and os.access(found, os.X_OK) and in_service_venv(found):\n",
        new="    if False:\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    Mutant(
        # 닫힌 PR 의 잡을 남이 합류했어도 취소한다. 합류한 남의 검사가 같이 죽는다
        name="m5r-watch-cancels-a-joined-job",
        path=T + "rcm_ci_watch.py",
        old='            alone = requester.get("label") == by_label() and not doc.get("joiners")\n',
        new="            alone = True\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    # ── #246 격리 검증이 찾은 것 ──
    Mutant(
        # 서버가 더 새 커밋으로 풀어 받은 제출을 닫힌 PR 의 잡으로 보고 취소한다. 그 커밋은 이미 본
        # sha 라 다시 내지 않아 머지가 막힌다
        name="m5r-watch-cancels-a-moved-submission",
        path=T + "rcm_ci_watch.py",
        old="            heads |= {\n",
        new="            heads |= set() and {\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    Mutant(
        # 다시 열린 PR 의 head 가 취소한 커밋이면 이미 본 sha 로 건너뛴다. 그 PR 은 끝내 막힌다
        name="m5r-watch-reopened-pr-stays-gone",
        path=T + "rcm_ci_watch.py",
        old='        reopened = shas[sha] if word == "gone" else None\n',
        new="        reopened = None\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    Mutant(
        # 다시 열린 PR 에서 취소한 그 잡의 끝 상태(알림 훅의 `unavailable · #N · cancelled`)를
        # 남이 다룬 것으로 본다. 다시 내지 않아 PR 이 error 로 막힌다(#246 재검증)
        name="m5r-watch-reopened-hook-status-blocks",
        path=T + "rcm_ci_watch.py",
        old="            mine = same_job or at is None",
        new="            mine = at is None",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    Mutant(
        # 열린 PR 목록의 첫 쪽만 읽는다. 100개 너머의 PR 은 내지 않고 닫힌 것으로 본다
        name="m5r-watch-reads-one-page-of-pulls",
        path=T + "rcm_ci_watch.py",
        old="""            while 'rel="next"' in reply.headers.get("link", ""):\n""",
        new="            while False:\n",
        tests=("tests/test_ci_connect_watch.py",),
    ),
    # ── M5q PR B2 — 돌던 워커가 드레인하고 다시 실행한다 (docs/m5q-workplan.md §6) ──
    Mutant(
        # 드레인이 도는 잡을 기다리지 않는다 — 레인이 잡을 도는 중에 `stopping` 이 켜져 그 잡이
        # lost `worker stopped` 가 된다(§6 W6 · 시나리오 b B9)
        name="m5q-worker-drain-does-not-wait",
        path="src/remote_ci_monitor/remote_worker.py",
        old="            if not alive <= self._idle_lanes:\n",
        new="            if False:\n",
        tests=("tests/test_worker_m5q_drain.py",),
    ),
    Mutant(
        # 레인 맨 위의 드레인 검사를 지운다 — 드레인 중에도 새 claim 을 한다(시나리오 b B7)
        name="m5q-worker-lane-claims-while-draining",
        path="src/remote_ci_monitor/remote_worker.py",
        old="            if self._lane_waits(lane):\n",
        new="            if False and self._lane_waits(lane):\n",
        tests=("tests/test_worker_m5q_drain.py",),
    ),
    Mutant(
        # 300초 창을 지운다 — 버전이 오락가락하면 heartbeat 마다 재실행한다(시나리오 b E7 · G9)
        name="m5q-worker-drain-window-removed",
        path="src/remote_ci_monitor/remote_worker.py",
        old="            ago = self._restarted_ago()\n",
        new="            ago = None\n",
        tests=("tests/test_worker_m5q_drain.py",),
    ),
    Mutant(
        # 설치본으로 다시 실행할 때 따라가기 변수를 지우지 않는다 — 옛 캐시 경로가 다음 프로세스로
        # 샌다(시나리오 b B8)
        name="m5q-worker-restart-keeps-follow-env",
        path="src/remote_ci_monitor/follow.py",
        old=(
            "    env = {k: v for k, v in environ.items() if k not in BOOT_ENV}\n"
            "    env[ENV_WORKER_RESTARTED_AT]"
        ),
        new="    env = dict(environ)\n    env[ENV_WORKER_RESTARTED_AT]",
        tests=("tests/test_cli_worker_m5q.py",),
    ),
    Mutant(
        # heartbeat 응답에 `server_version` 을 싣지 않는다 — 돌던 워커가 서버가 바뀐 것을
        # 모른다(B16)
        name="m5q-heartbeat-no-server-version",
        path="src/remote_ci_monitor/remote_workers.py",
        old='            "server_version": self.version,\n        }\n',
        new="        }\n",
        tests=("tests/test_server_m5q_worker.py",),
    ),
    # ── 작업이 길어진 원인을 막는 장치 (2026-11-20 사건 2 · 6c · 6d · 6e · 6f) ──
    Mutant(
        # 셸 치환을 「모르는 값」으로 보지 않는다 — `--title "$T"` 를 글자 그대로 재서 같은 명령의
        # 앞 `git commit` 까지 막는다(사건 6d)
        name="guard-naming-judges-shell-values",
        path="tools/guard_naming.py",
        old="    return SHELL_SUBST_RE.search(text) is not None\n",
        new="    return False\n",
        tests=("tests/test_guard_naming.py",),
    ),
    Mutant(
        # PR 본문 파일 이름을 안 본다 — 병렬 세션이 `pr.md` 를 서로 덮는다(사건 6e)
        name="guard-naming-any-body-file",
        path="tools/guard_naming.py",
        old="    if stem in SHARED_BODY_STEMS:\n",
        new="    if False:\n",
        tests=("tests/test_guard_naming.py",),
    ),
    Mutant(
        # REST 머지(`gh api -X PUT …/pulls/N/merge` · curl)를 못 알아본다 — 감시 스크립트가 pending
        # 체크를 두고 머지했다(사건 6c)
        name="guard-merge-misses-rest-merge",
        path="tools/guard_merge.py",
        old="    if rest and PUT_RE.search(flat) and url:\n",
        new="    if False:\n",
        tests=("tests/test_guard_merge.py",),
    ),
    Mutant(
        # 관문이 끝나지 않은 체크를 안 본다 — 「아직 도는데 머지」가 그대로 돌아온다(사건 6c)
        name="merge-pr-ignores-pending",
        path="tools/merge_pr.py",
        old='        if run.get("status") != "completed":\n            states[name] = "pending"\n',
        new='        if False:\n            states[name] = "pending"\n',
        tests=("tests/test_merge_pr.py",),
    ),
    Mutant(
        # 필수가 아닌 check-run 도 센다 — pending 이거나 실패한 GitHub Actions 잡이 머지를 막는다
        # (2026-11-20 오너 결정: GitHub CI 는 머지 조건이 아니다)
        name="merge-pr-counts-unrequired-checks",
        path="tools/merge_pr.py",
        old='        name = str(run.get("name", "?"))\n        if name not in required:\n',
        new='        name = str(run.get("name", "?"))\n        if False:\n',
        tests=("tests/test_merge_pr.py",),
    ),
    Mutant(
        # 룰셋의 필수 context 를 안 본다 — GitHub Actions 만 초록이고 `rcm/ci` 가 아직 없는데
        # 머지한다(2026-11-20 오너 결정: 필수 체크가 `rcm/ci` 로 바뀐다)
        name="merge-pr-ignores-required-contexts",
        path="tools/merge_pr.py",
        old="    missing = tuple(context for context in required if context not in states)\n",
        new="    missing = ()\n",
        tests=("tests/test_merge_pr.py",),
    ),
    Mutant(
        # 옛 DB 판과 빌드 판을 비교하지 않는다 — 같은 판 DB 도 막거나(오탐) 비교 없이 지나간다
        # (사건 6f: 개발 DB v7 → v24)
        name="guard-production-no-version-compare",
        path="tools/guard_production.py",
        old="    if have is None or want is None or have >= want:\n",
        new="    if have is None or want is None:\n",
        tests=("tests/test_guard_production_migration.py",),
    ),
    # ── #237 격리 검증이 찾은 가드의 구멍과 mutcheck 병렬 (2026-11-20) ──
    Mutant(
        # `-R o/r` 를 `pr` 앞에서만 본다 — `gh pr -R o/r merge 7` 이 관문을 돌아간다
        name="guard-merge-repo-after-pr",
        path="tools/guard_merge.py",
        old='r"pr\\s+" + REPO_OPT + r"merge\\b")',
        new='r"pr\\s+merge\\b")',
        tests=("tests/test_guard_merge.py",),
    ),
    Mutant(
        # 앞의 `GH_TOKEN=$(gh auth token …)` 대입을 shlex 에 맡긴다 — 명령 이름이 `auth` 가 되어 PR
        # 제목 · 본문 파일 검사가 꺼진다
        name="guard-naming-assignment-hides-command",
        path="tools/guard_naming.py",
        old="    rest = _drop_prefix(segment)\n",
        new="    rest = segment\n",
        tests=("tests/test_guard_naming.py",),
    ),
    Mutant(
        # GitHub 의 4xx 답(룰셋 API 403 · 404)을 「모름(3)」으로 센다 — 설명과 코드가 어긋난다
        name="merge-pr-4xx-is-unknown",
        path="tools/merge_pr.py",
        old="        return self.status is None or self.status >= 500\n",
        new="        return self.status is None or self.status >= 400\n",
        tests=("tests/test_merge_pr.py",),
    ),
    Mutant(
        # 결과를 끝난 순서대로 바로 찍는다 — 병렬 출력이 실행마다 달라지고 직렬과 어긋난다
        name="mutcheck-reports-in-finish-order",
        path="scripts/mutcheck.py",
        old="            results[index] = outcome\n            done.notify_all()\n",
        new=(
            "            results[index] = outcome\n"
            "            report(mutants[index], *outcome)\n"
            "            done.notify_all()\n"
        ),
        tests=("tests/test_mutcheck_parallel.py",),
    ),
    Mutant(
        # 변이 사본에서 바이트코드를 쓴다 — 원본이 변이와 같은 초에 쓰였고 변이가 같은 길이면 옛
        # `.pyc` 를 읽어 잡혀야 할 변이가 「초록」으로 보인다
        name="mutcheck-writes-bytecode",
        path="scripts/mutcheck.py",
        old=', "PYTHONDONTWRITEBYTECODE": "1"}\n',
        new="}\n",
        tests=("tests/test_mutcheck_parallel.py",),
    ),
    Mutant(
        # Chrome 파일을 그룹으로 묶지 않는다 — `-n` 에서 여러 워커가 동시에 Chrome 을 띄워 CDP 가
        # 마감을 넘긴다(rcm-ci 잡 #3861)
        name="conftest-chrome-not-grouped",
        path="tests/conftest.py",
        old='            return "chrome" if uses_chrome(',
        new='            return nodeid if True else "chrome" if uses_chrome(',
        tests=("tests/test_conftest_hygiene.py::test_chrome_modules_run_on_one_xdist_worker",),
    ),
    # ── #245 격리 검증이 남긴 후속 — 가드 · 시험 하네스 ──
    Mutant(
        # 옵션과 값을 붙여 쓴 `-Ro/r` 를 못 본다 — `gh pr -Ro/r merge 7` 이 관문을 돌아간다
        name="guard-merge-glued-repo-option",
        path="tools/guard_merge.py",
        old='REPO_OPT = r"(?:(?:-R\\s*|--repo[\\s=]+)\\S+\\s+)*"\n',
        new='REPO_OPT = r"(?:(?:-R|--repo)[\\s=]+\\S+\\s+)*"\n',
        tests=("tests/test_guard_merge.py",),
    ),
    Mutant(
        # 저장소 옵션을 한 번만 본다 — `gh -Ro/r -R x/y pr merge 1` 이 관문을 돌아간다(#246)
        name="guard-merge-one-repo-option",
        path="tools/guard_merge.py",
        old='REPO_OPT = r"(?:(?:-R\\s*|--repo[\\s=]+)\\S+\\s+)*"\n',
        new='REPO_OPT = r"(?:(?:-R\\s*|--repo[\\s=]+)\\S+\\s+)?"\n',
        tests=("tests/test_guard_merge.py",),
    ),
    Mutant(
        # `-n` 에서 `--dist` 를 loadgroup 으로 정하지 않는다 — addopts 에서 뺀 뒤로 Chrome 시험이
        # 여러 워커에 흩어진다
        name="conftest-dist-not-defaulted",
        path="tests/conftest.py",
        old='        config.option.dist = "loadgroup"\n',
        new="        pass\n",
        tests=("tests/test_conftest_hygiene.py::test_chrome_modules_run_on_one_xdist_worker",),
    ),
    Mutant(
        # timeout 때 e2e 하네스가 새 세션으로 띄운 서버 · 워커를 죽이지 않는다 — 다른 변이의 CPU 를
        # 먹으며 남는다
        name="mutcheck-timeout-spares-session-children",
        path="scripts/mutcheck.py",
        old="        _kill_sessions(env)\n",
        new="        pass\n",
        tests=("tests/test_mutcheck_parallel.py",),
    ),
    Mutant(
        # M5s C — 도는 잡의 「사람이 할 일」 표식을 이유가 무시한다: 로그인을 기다리는 출시 잡이
        # 「Needs a look」에 안 오르고 그냥 running(또는 stuck)으로 보인다(결정 213)
        name="action-needs-action-dropped",
        path="src/remote_ci_monitor/core/queue.py",
        old="    if progress is not None and progress.action is not None:\n",
        new="    if False:\n",
        tests=("tests/test_action_marker_m5s.py",),
    ),
    Mutant(
        # M5s C — code 어휘 검사를 뺀다: ESC 가 든 code 가 마커로 저장돼 stderr · 알림 env 로 흐른다
        name="action-code-any",
        path="src/remote_ci_monitor/core/progress.py",
        old="    if not ACTION_CODE_RE.fullmatch(code):\n",
        new="    if not code:\n",
        tests=("tests/test_action_marker_m5s.py",),
    ),
    Mutant(
        # M5s C — note 의 제어문자를 안 지운다: `\\r` 하나로 `rcm wait` 의 앞 줄을 지운다
        name="action-note-raw",
        path="src/remote_ci_monitor/core/progress.py",
        old='    text = clean_line(note)[:MAX_ACTION_NOTE].strip() if sep else ""\n',
        new='    text = note[:MAX_ACTION_NOTE].strip() if sep else ""\n',
        tests=("tests/test_action_marker_m5s.py",),
    ),
    Mutant(
        # M5s C — 플래그 없이도 4 로 돌아온다: 결정 234 는 4 를 `--return-on-action` 에만 묶었다.
        # 지금 `rcm wait` 를 쓰는 CI 가 표식 하나로 「끝났다」고 읽는다
        name="action-exit-4-without-flag",
        path="src/remote_ci_monitor/cli.py",
        old="        if not self.return_on_action or self._stopped(job):\n",
        new="        if self._stopped(job):\n",
        tests=("tests/test_action_marker_m5s.py",),
    ),
    Mutant(
        # M5s C(#251 격리 검증 P2) — 취소 중인 잡에서도 4 와 「needs action」 줄이 나온다. 그 잡은
        # 더 돌지 않으므로 「잡은 계속 돈다」는 약속이 거짓이 된다
        name="action-4-while-cancelling",
        path="src/remote_ci_monitor/cli.py",
        old="        return state in TERMINAL_STATES or state == CANCELLING\n",
        new="        return state in TERMINAL_STATES\n",
        tests=("tests/test_action_marker_m5s.py", "tests/test_web_schedule_m5s.py"),
    ),
    Mutant(
        # M5s G(#251 재검증 P3) — 실패 · 성공 · 만료로 끝난 잡에도 「needs action」 줄을 찍는다.
        # 받을 잡이 없는데 사람에게 할 일을 시킨다
        name="action-line-on-a-finished-job",
        path="src/remote_ci_monitor/cli.py",
        old="        return state in TERMINAL_STATES or state == CANCELLING\n",
        new="        return state in (CANCELLING, CANCELLED)\n",
        tests=("tests/test_web_schedule_m5s.py",),
    ),
    Mutant(
        # M5s C(#251 격리 검증 P2) — 한 줄 정리가 bidi 덮어쓰기를 남긴다: note 와 훅의 실패 이름이
        # 터미널 · 알림에서 거꾸로 보인다
        name="clean-line-keeps-bidi",
        path="src/remote_ci_monitor/core/progress.py",
        old="        and not 0x2028 <= cp <= 0x202E  # ",
        new="        and not 0x2028 <= cp <= 0x2029  # ",
        tests=("tests/test_action_marker_m5s.py", "tests/test_failure_summaries_m5o.py"),
    ),
    # ── M5s A — 예약 실행 (`docs/m5s-workplan.md` §4.1 · §7) ──
    Mutant(
        # 승격이 `not_before` 를 보지 않는다 — 15:00 예약이 건 즉시 돈다
        name="m5s-promote-ignores-not-before",
        path="src/remote_ci_monitor/store.py",
        old='"SELECT id FROM jobs WHERE state=? AND not_before IS NOT NULL AND not_before <= ? "',
        new='"SELECT id FROM jobs WHERE state=? AND not_before IS NOT NULL AND ? IS NOT NULL "',
        tests=(
            "tests/test_schedule_m5s.py::test_s1_promotion_waits_for_not_before_then_sets_queued_at",
            "tests/test_server_schedule_m5s.py::test_s1_the_tick_promotes_at_the_time_and_not_before",
        ),
    ),
    Mutant(
        # 만료가 시작 마감을 보지 않는다 — 마감이 있는 예약 · 대기 잡이 다음 주기에 전부 닫힌다
        name="m5s-expire-ignores-deadline",
        path="src/remote_ci_monitor/store.py",
        old='"AND start_deadline IS NOT NULL AND start_deadline < ? ORDER BY id",',
        new='"AND start_deadline IS NOT NULL AND ? IS NOT NULL ORDER BY id",',
        tests=(
            "tests/test_schedule_m5s.py::test_expiry_closes_a_promoted_job_only_after_its_deadline",
            "tests/test_server_schedule_m5s.py::"
            "test_s4_a_promoted_job_no_lane_takes_expires_at_the_deadline",
        ),
    ),
    # `m5s-immediate-run-joins-a-schedule`(`JOINABLE_STATES` 에 `scheduled` 를 넣는다)는 PR D 에서
    # 뺐다 — 합류 쿼리가 `not_before IS NULL` 로 예약에서 온 잡을 전부 거르므로(넘어온 P2) 그
    # 변이는 같은 동작의 변이가 됐다. 예약 행은 언제나 `not_before` 가 있다(`_create_job_tx` 가
    # 막는다). 「예약에 즉시 실행이 합류한다」는 아래 변이가 지킨다.
    Mutant(
        # 합류 쿼리가 예약에서 온 잡을 거르지 않는다 — 즉시 실행이 승격된 예약에 붙어 그 마감을
        # 물려받고 레인이 늦으면 `expired` 로 끝난다(#253 격리 검증 P1). 마감 없는 예약(반복의
        # 다음 시도, 결정 231)에 붙으면 `interval` 뒤로 밀린다 — 그래서 필터는 `not_before` 다(PR D)
        name="m5s-join-ignores-not-before",
        path="src/remote_ci_monitor/store.py",
        old='+ f"AND state IN ({marks}) AND not_before IS NULL ORDER BY id LIMIT 1",',
        new='+ f"AND state IN ({marks}) ORDER BY id LIMIT 1",',
        tests=(
            "tests/test_server_schedule_m5s.py::"
            "test_s8_an_immediate_run_does_not_join_a_promoted_schedule",
            "tests/test_retry_m5s.py::test_nothing_joins_a_promoted_retry_attempt",
        ),
    ),
    Mutant(
        # 시각을 준 제출이 합류한다 — 지금 도는 같은 입력의 잡에 붙어 「그 시각에」가 사라진다
        # (결정 226 · S19)
        name="m5s-scheduled-submission-joins",
        path="src/remote_ci_monitor/server.py",
        old="        want_join = want_join and schedule is None  # 예약은 합류 안 함(결정 226)\n",
        new="",
        tests=(
            "tests/test_server_schedule_m5s.py::"
            "test_s19_a_schedule_does_not_join_an_active_identical_job",
        ),
    ),
    # ── M5s G — 화면의 예약 · 표식 (`docs/m5s-workplan.md` §4.1 「표시」 · §4.3 · §4.7) ──
    Mutant(
        # 웹이 `needs_action` 을 모른다 — 로그인을 기다리는 출시 잡이 「Needs a look」에 안 오른다
        name="m5s-web-needs-action-not-actionable",
        path="src/remote_ci_monitor/web/app.js",
        old='var ACTIONABLE = ["needs_action", "worker_down",',
        new='var ACTIONABLE = ["worker_down",',
        tests=("tests/web/schedule_m5s.test.js",),
        runner="node",
    ),
    Mutant(
        # 예약을 못 읽은 풀을 건너뛴다 — 조회 실패가 「예약 없음」으로 그려진다(fail-open)
        name="m5s-web-scheduled-error-reads-as-empty",
        path="src/remote_ci_monitor/web/app.js",
        old=(
            'return { kind: "error", rows: [], pools: {}, error: pl.scheduled_error || null, '
            "code: pl.scheduled_error_code || null };"
        ),
        new="continue;",
        tests=("tests/web/schedule_m5s.test.js",),
        runner="node",
    ),
    Mutant(
        # `rcm top` 이 예약 조회 실패를 빈 목록처럼 그린다(줄이 사라진다)
        name="m5s-top-scheduled-error-reads-as-empty",
        path="src/remote_ci_monitor/core/render_text.py",
        old='    if not isinstance(rows, list) or pool.get("scheduled_error"):\n',
        new="    if not isinstance(rows, list):\n        return []\n    if False:\n",
        tests=("tests/test_web_schedule_m5s.py",),
    ),
    Mutant(
        # 알림 실패가 0 이어도 배지를 그린다 — 늘 떠 있는 경고는 아무도 안 본다
        name="m5s-web-notify-badge-at-zero",
        path="src/remote_ci_monitor/web/app.js",
        old="if (!isNum(n) || n <= 0) return null;",
        new="if (!isNum(n)) return null;",
        tests=("tests/web/schedule_m5s.test.js",),
        runner="node",
    ),
    Mutant(
        # #257 검증 P1 — 예약을 못 읽었는데 「Nothing needs a look」(초록)을 그린다(fail-open)
        name="m5s-web-needs-a-look-ok-without-schedules",
        path="src/remote_ci_monitor/web/app.js",
        old=(
            "if (schedUnknown && !lines.length) "
            'return { kind: "unknown", lines: [], why: "scheduled" };'
        ),
        new="",
        tests=("tests/web/schedule_m5s.test.js",),
        runner="node",
    ),
    Mutant(
        # 예약 행 이유 칸이 note 를 escape 하지 않는다 — 잡이 찍은 `<img onerror>` 가 페이지에서
        # 돈다
        name="m5s-web-scheduled-note-unescaped",
        path="src/remote_ci_monitor/web/app.js",
        old="""title="' + esc(m.reason.text) + '">' + esc(m.reason.text) + "</span>\"""",
        new="""title="' + esc(m.reason.text) + '">' + m.reason.text + "</span>\"""",
        tests=(
            "tests/test_web_browser.py::"
            "test_scheduled_section_lists_my_schedule_with_cancel_and_the_header_counts_notify_failures",
        ),
        needs_chrome=True,
    ),
    Mutant(
        # #254 검증 P3 — 예약된 status 조회를 「가장 새 조회」로 친다: 앞선 실패 줄이 사라진다
        name="m5s-web-store-check-scheduled-hides-failure",
        path="src/remote_ci_monitor/web/app.js",
        old="if (!STORE_CHECK_NOT_A_RESULT[j.state] && (!newest || j.id > newest.id)) newest = j;",
        new="if (!newest || j.id > newest.id) newest = j;",
        tests=("tests/web/store_status.test.js",),
        runner="node",
    ),
    Mutant(
        # #257 재검증 P2 — 반복을 다 쓴 조회를 실패로 보지 않는다: 예전 문서를 아무 말 없이 그린다
        name="m5s-web-store-check-exhausted-not-failed",
        path="src/remote_ci_monitor/web/app.js",
        old="lost: 1, exhausted: 1, expired: 1 };",
        new="lost: 1, expired: 1 };",
        tests=("tests/web/store_status.test.js",),
        runner="node",
    ),
    Mutant(
        # H 실기 — 409 `schedule_exists` 가 「결과 모름」(3)이 된다: 같은 예약을 두 번 건 세션이
        # 잡이 생겼는지 몰라 다시 묻거나 기다린다
        name="m5s-cli-schedule-exists-is-unknown",
        path="src/remote_ci_monitor/cli.py",
        old='_REFUSED_CODES = ("release_route_required", "schedule_exists", "golive_running")',
        new='_REFUSED_CODES = ("release_route_required",)',
        tests=("tests/test_cli_schedule_m5s.py",),
    ),
    # ── M5s D — 종료코드 반복 (`docs/m5s-workplan.md` §4.2 · §7) ──
    Mutant(
        # 반복 선언이 없어도 75 를 반복한다 — rcm-ci 의 75(인프라 실패)가 10분마다 다시 돌아
        # 커밋 상태가 늦는다(§3 #1 · 결정 210 · S7)
        name="m5s-retry-without-declaration",
        path="src/remote_ci_monitor/core/model.py",
        old=(
            "            and self.retry_on_exit is not None\n"
            "            and not self.retry_disabled\n"
            "            and rc == self.retry_on_exit\n"
        ),
        new=(
            "            and not self.retry_disabled\n"
            "            and rc == (self.retry_on_exit or 75)\n"
        ),
        tests=(
            "tests/test_retry_m5s.py::"
            "test_s7_exit_75_without_a_retry_declaration_is_a_plain_failure",
        ),
    ),
    Mutant(
        # `retried` 가 `running` 에서만 가지 않는다 — 75 로 끝난 순간 들어온 사람의 취소를 덮고
        # 다음 시도까지 만든다(결정 225 · S20)
        name="m5s-retry-overrides-cancel",
        path="src/remote_ci_monitor/store.py",
        old="        if state == RETRIED:\n            only_from = (RUNNING,)\n",
        new="        if state == RETRIED:\n            only_from = None\n",
        tests=(
            "tests/test_retry_m5s.py::"
            "test_retried_only_leaves_running_and_a_cancel_in_between_makes_no_next_attempt",
            "tests/test_retry_m5s.py::"
            "test_s20_a_remote_75_after_a_cancel_closes_cancelled_with_no_next_attempt",
        ),
    ),
    Mutant(
        # 다음 시도를 supersede 의 「새 잡」으로 본다 — 옛 sha 를 물려받은 시도가 그 사이 같은
        # 브랜치에 들어온 더 새 커밋의 잡을 치운다(#256 격리 검증 P1)
        name="m5s-retry-attempt-supersedes-newer",
        path="src/remote_ci_monitor/core/supersede.py",
        old="    if new.retry_of is not None:\n        return []\n",
        new="",
        tests=(
            "tests/test_retry_m5s.py::test_a_retry_attempt_is_never_the_new_job_of_a_supersede",
        ),
    ),
    Mutant(
        # 승격된 다음 시도가 supersede 판정에 간다 — 위 변이의 방어가 없으면 더 새 커밋을 치운다
        name="m5s-promoted-retry-runs-supersede",
        path="src/remote_ci_monitor/server.py",
        old="            if job is not None and job.retry_of is not None:\n",
        new="            if False:\n",
        tests=(
            "tests/test_retry_m5s.py::"
            "test_a_promoted_retry_attempt_does_not_supersede_a_newer_commit",
        ),
    ),
    Mutant(
        # 로컬 레인이 `retried` 판정과 finish 사이의 취소를 닫지 않는다 — 잡이 `cancelling` 으로
        # 영영 남는다(#256 격리 검증 P2a)
        name="m5s-local-retry-race-left-cancelling",
        path="src/remote_ci_monitor/worker.py",
        old=(
            "        if not closed and oc.state == RETRIED:\n"
            "            # `retried` 는 `running` 에서만 간다"
        ),
        new="        if False:\n            # `retried` 는 `running` 에서만 간다",
        tests=(
            "tests/test_e2e_retry_m5s.py::"
            "test_a_cancel_between_the_verdict_and_the_finish_closes_cancelled",
        ),
    ),
    Mutant(
        # 로컬 레인이 반복할 시도의 묶음도 모은다 — 시도마다 묶음 파일이 쌓인다(#256 P2b)
        name="m5s-local-retry-collects-artifacts",
        path="src/remote_ci_monitor/worker.py",
        old="        bundle = None if retrying else self._collect_artifacts(",
        new="        bundle = None if False else self._collect_artifacts(",
        tests=(
            "tests/test_e2e_retry_m5s.py::"
            "test_a_local_keep_attempt_that_exits_75_leaves_no_bundle_and_no_shelf_row",
        ),
    ),
    Mutant(
        # `retried` finish 가 받은 묶음을 쓴다 — 중간 시도가 묶음 행과 보관 행을 남긴다(#256 P2b)
        name="m5s-retried-finish-writes-bundle",
        path="src/remote_ci_monitor/store.py",
        old="            only_from = (RUNNING,)\n            bundle = None\n",
        new="            only_from = (RUNNING,)\n",
        tests=(
            "tests/test_retry_m5s.py::test_a_retried_finish_writes_no_bundle_even_when_given_one",
        ),
    ),
    Mutant(
        # 원격 시도의 묶음을 `retried` 에서 만료시키지 않는다 — TTL 내내 남는다(#256 P2c)
        name="m5s-retried-remote-bundle-kept",
        path="src/remote_ci_monitor/store.py",
        old='WHERE job_id=? AND state=? AND expires_at > ?",',
        new='WHERE job_id=? AND state=? AND expires_at < ?",',
        tests=(
            "tests/test_retry_m5s.py::test_a_remote_bundle_of_a_retried_attempt_expires_at_once",
        ),
    ),
    Mutant(
        # 합류가 얼린 반복 정책을 보지 않는다 — 반복을 원하는 제출이 `retry: false` 잡에 붙어
        # 반복을 잃는다(#256 격리 검증 P2)
        name="m5s-join-ignores-retry-policy",
        path="src/remote_ci_monitor/store.py",
        old=(
            '    "AND retry_on_exit IS ? AND retry_interval_seconds IS ? '
            'AND retry_max_attempts IS ? "\n'
            '    "AND retry_disabled=? "\n'
        ),
        new=(
            '    "AND (retry_on_exit IS ? OR 1) AND (retry_interval_seconds IS ? OR 1) "\n'
            '    "AND (retry_max_attempts IS ? OR 1) AND (retry_disabled=? OR 1) "\n'
        ),
        tests=(
            "tests/test_retry_m5s.py::"
            "test_a_retry_wanting_submission_does_not_join_a_retry_false_job",
            "tests/test_retry_m5s.py::"
            "test_a_submission_after_a_reload_changed_max_attempts_does_not_join",
        ),
    ),
    # ── 개발 루프 도구 (2026-11-25) ──
    Mutant(
        # 시나리오 표지가 있는데 ID 가 하나도 없는 표를 통과시킨다 — 대조할 것이 없는데 「ok」라서
        # 검사하지 않은 명세가 검사한 것처럼 보인다(fail-open)
        name="scenario-trace-zero-ids-pass",
        path="tools/scenario_trace.py",
        old='        if not group["ids"]:\n',
        new="        if False:\n",
        tests=("tests/test_scenario_trace.py",),
    ),
    Mutant(
        # 아직 보고가 없는 rcm/ci 를 초록으로 읽는다 — status 가 「merge」를 권한다(격리 검증 F2)
        name="dev-batch-unreported-ci-success",
        path="tools/dev_batch.py",
        old='    found = ((c.pending, "pending"), (c.missing, "not reported"))\n',
        new='    found = ((c.pending, "pending"),)\n',
        tests=(
            "tests/test_dev_batch.py::"
            "test_status_never_reads_an_unfinished_check_or_an_old_marker_as_done",
        ),
    ),
    Mutant(
        # 다른 sha 에 단 `verified:` 표식을 지금 head 의 검증으로 센다(격리 검증 F2)
        name="dev-batch-stale-verified-yes",
        path="tools/dev_batch.py",
        old='    return "yes" if sha in marks else "stale" if marks else "no"',
        new='    return "yes" if marks else "no"',
        tests=(
            "tests/test_dev_batch.py::"
            "test_status_never_reads_an_unfinished_check_or_an_old_marker_as_done",
        ),
    ),
    Mutant(
        # porcelain 출력을 strip 한다 — 첫 줄 ` M docs/a.md` 가 `ocs/a.md` 가 되어 범위 밖으로
        # 보인다(격리 검증 F1)
        name="dev-batch-porcelain-first-line",
        path="tools/dev_batch.py",
        old="for entry in _git(*status, cwd=wt, strip=False).split",
        new="for entry in _git(*status, cwd=wt, strip=True).split",
        tests=(
            "tests/test_dev_batch.py::test_scope_reads_the_worktree_status_without_losing_characters",
        ),
    ),
    Mutant(
        # `@` 로 시작하는 커밋 경로를 버린다 — `git log --format=@%H` 를 한 줄기로 나누던 때의
        # 버그 꼴: `@src-config.py` 를 커밋 머리로 읽어 범위 밖인데 「scope ok」(2라운드 F1)
        name="dev-batch-scope-drops-at-paths",
        path="tools/dev_batch.py",
        old="touched += [(sha[:12], name) for name in names if name]",
        new='touched += [(sha[:12], name) for name in names if name and name[0] != "@"]',
        tests=(
            "tests/test_dev_batch.py::test_scope_sees_a_committed_path_that_looks_like_a_log_header",
        ),
    ),
    Mutant(
        # 상태 파일의 꼴 검사를 건너뛴다 — 문자열 owns · 없는 묶음을 가리키는 after 가 종료 3 이
        # 아니라 트레이스백 · 엉뚱한 범위가 된다(2라운드 F2 · F12)
        name="dev-batch-shape-check-skipped",
        path="tools/dev_batch.py",
        old="        if not all(shapes):\n",
        new="        if False:\n",
        tests=("tests/test_dev_batch.py::test_a_state_file_with_a_wrong_shape_is_corrupt",),
    ),
    Mutant(
        # 시험 출력의 집 경로를 `~` 로 바꾸지 않는다 — 영수증(PR 본문에 붙는다)에 개인 경로가
        # 남는다(2라운드 F13)
        name="redgreen-home-path-kept",
        path="tools/redgreen.py",
        old='[(Path(p), "<tmp>") for p in _temp_dirs()] + [(Path.home(), "~")]',
        new='[(Path(p), "<tmp>") for p in _temp_dirs()]',
        tests=("tests/test_redgreen.py::test_the_receipt_hides_a_path_under_the_home_folder",),
    ),
    Mutant(
        # 댓글을 읽기 **전에** verified 를 "no" 로 둔다 — 댓글 읽기가 실패해도 「no · verify ·
        # 종료 0」이 된다. 모르는 것을 아는 값으로 남기는 꼴(3라운드 H2)
        name="dev-batch-verified-default-before-read",
        path="tools/dev_batch.py",
        old="    cells: dict[str, str] = {}\n",
        new='    cells: dict[str, str] = {}\n    row["verified"] = "no"\n',
        tests=(
            "tests/test_dev_batch.py::"
            "test_a_read_that_did_not_answer_leaves_a_dash_never_a_known_value",
        ),
    ),
    Mutant(
        # 문서 끝까지 안 닫힌 펜스를 문제로 세지 않는다 — 펜스 뒤의 표지를 조용히 못 읽고
        # 통과한다(3라운드 H3)
        name="scenario-trace-unclosed-fence-ignored",
        path="tools/scenario_trace.py",
        old='    if fence:\n        message = f"the {fence} fence opened here never closes',
        new='    if False:\n        message = f"the {fence} fence opened here never closes',
        tests=(
            "tests/test_scenario_trace.py::"
            "test_an_unclosed_fence_is_a_format_problem_at_the_line_it_opened",
        ),
    ),
    Mutant(
        # 끊길 때 pytest 하나만 죽인다 — 시험이 띄운 자식이 남는다(3라운드 L1)
        name="redgreen-kills-only-pytest",
        path="tools/redgreen.py",
        old="                os.killpg(proc.pid, signal.SIGKILL)\n",
        new="                os.kill(proc.pid, signal.SIGKILL)\n",
        tests=(
            "tests/test_redgreen.py::"
            "test_a_signal_removes_the_worktrees_and_stops_the_whole_pytest_group",
        ),
    ),
    Mutant(
        # 잠긴(끊긴 add 의 initializing) 워크트리를 `--force` 하나로 지우려 한다 — 못 지우고,
        # 폴더를 지운 뒤의 prune 도 잠긴 등록은 남긴다(3라운드 M3)
        name="redgreen-locked-worktree-single-force",
        path="tools/redgreen.py",
        old='_git(root, "worktree", "remove", "-f", "-f", str(path))',
        new='_git(root, "worktree", "remove", "--force", str(path))',
        tests=("tests/test_redgreen.py::test_a_locked_worktree_left_by_a_cut_add_is_removed",),
    ),
    Mutant(
        # 404 가 아닌 브랜치 읽기 실패(502 …)도 「not pushed」로 읽는다 — 못 읽은 것을 「없다」로
        # 센다(4라운드 M4)
        name="dev-batch-branch-error-not-pushed",
        path="tools/dev_batch.py",
        old="            if error.status != 404:\n                raise\n",
        new="            if False:\n                raise\n",
        tests=(
            "tests/test_dev_batch.py::"
            "test_a_read_that_did_not_answer_leaves_a_dash_never_a_known_value",
        ),
    ),
    Mutant(
        # 끝난 check-run 의 모르는 결론(`weird`)을 거르지 않는다 — merge_pr 는 그것을 「ok」로
        # 센다(4라운드 M4)
        name="dev-batch-unknown-conclusion-ok",
        path="tools/dev_batch.py",
        old='                _need(done, path, f"unknown check-run conclusion {end!r}")\n',
        new='                _need(True, path, f"unknown check-run conclusion {end!r}")\n',
        tests=(
            "tests/test_dev_batch.py::"
            "test_a_read_that_did_not_answer_leaves_a_dash_never_a_known_value",
        ),
    ),
    Mutant(
        # scope 가 PR 의 diff 를 안 본다 — `merge -s ours` 가 dev 의 변경을 버려도 `--cc` 에는
        # 아무것도 없어 「scope ok」(4라운드 H2)
        name="dev-batch-scope-skips-pr-diff",
        path="tools/dev_batch.py",
        old='    touched += [("merge-result", name)',
        new='    touched += [] and [("merge-result", name)',
        tests=(
            "tests/test_dev_batch.py::"
            "test_scope_sees_paths_a_merge_that_kept_only_our_side_carries",
        ),
    ),
    Mutant(
        # xfail 을 돈 시험으로 센다 — 통과 하나와 xfail 하나인 파일 nodeid 가 「passed」(4라운드 M4)
        name="redgreen-xfail-counts-as-run",
        path="tools/redgreen.py",
        old='NOT_RUN = frozenset({"SKIPPED", "XFAIL", "XPASS"})',
        new='NOT_RUN = frozenset({"SKIPPED", "XPASS"})',
        tests=("tests/test_redgreen.py::test_an_xfail_next_to_a_passing_test_is_not_passed",),
    ),
    Mutant(
        # 건너뛴 시험의 요약 줄을 읽지 않는다 — 파일 · 클래스 · 매개변수 nodeid 의 건너뛴 시험이
        # 안 보여 영수증이 red-then-green(4라운드 H3)
        name="redgreen-skipped-lines-unread",
        path="tools/redgreen.py",
        old="(PASSED|FAILED|ERROR|XPASS|XFAIL|SKIPPED)",
        new="(PASSED|FAILED|ERROR|XPASS|XFAIL)",
        tests=("tests/test_redgreen.py::test_a_skipped_test_inside_a_requested_node_is_undecided",),
    ),
    Mutant(
        # 신호 막기를 try 밖에서 건다 — 막기 호출 안에서 처리기가 SystemExit 를 던지면 SIGTERM ·
        # SIGINT 가 막힌 채 남아 같은 pytest 워커의 다음 자식이 SIGTERM 을 못 받는다(rc -9,
        # 2026-11-25 dev rcm/ci #4136 · #4137 · #4165)
        name="redgreen-block-outside-try",
        path="tools/redgreen.py",
        old="    try:\n        signal.pthread_sigmask(signal.SIG_BLOCK, HELD)\n        yield\n",
        new="    signal.pthread_sigmask(signal.SIG_BLOCK, HELD)\n    try:\n        yield\n",
        tests=(
            "tests/test_redgreen.py::"
            "test_sm1_a_handler_that_fires_as_the_block_lands_leaves_no_mask",
        ),
    ),
    Mutant(
        # 표 바로 뒤의 앞 `|` 없는 행을 보지 않는다 — GFM 은 행으로 그리는데 대조하지 않고
        # 통과한다(4라운드 H1)
        name="scenario-trace-unpiped-row-ignored",
        path="tools/scenario_trace.py",
        old="        if after < len(lines) and after not in code and _row_like(lines[after]):\n",
        new="        if False:\n",
        tests=(
            "tests/test_scenario_trace.py::"
            "test_a_row_without_a_leading_pipe_right_after_the_table_is_a_format_problem",
        ),
    ),
    Mutant(
        # xfail 을 단 시험의 태그를 행의 증명으로 센다 — 실패해도 초록인 시험이 행을 채운다
        # (5라운드 H1)
        name="scenario-trace-xfail-counts-as-proof",
        path="tools/scenario_trace.py",
        old='    kind = "xfail" if name in ("xfail", "expectedfailure") else kind\n',
        new="    kind = kind\n",
        tests=(
            "tests/test_scenario_trace.py::"
            "test_a_tag_on_an_xfail_test_is_reported_like_a_skipped_one",
        ),
    ),
    Mutant(
        # add 의 잠금을 뺀다 — 동시에 add 한 세션들이 서로의 묶음을 덮어 하나만 남는다
        # (5라운드 M4)
        name="dev-batch-no-lock",
        path="tools/dev_batch.py",
        old="        fcntl.flock(handle, fcntl.LOCK_EX)\n",
        new="        pass\n",
        tests=("tests/test_dev_batch.py::test_concurrent_adds_keep_every_bundle",),
    ),
    # ── M5t-a PR2 — 환경 진단 rcm doctor (docs/m5t-workplan.md §7 「변이 검사」) ──
    Mutant(
        # doctor 가 판정 못 함(서버 불통 · 시한 초과 · 옛 서버)을 0 으로 끝낸다 — 「다 갖춤」과
        # 「알 수 없음」이 같은 0 이 되어 자동화가 모르는 머신을 준비됐다고 읽는다(결정 246)
        name="doctor-unknown-exits-0",
        path="src/remote_ci_monitor/core/doctor.py",
        old="    if undecided:\n        return EXIT_UNKNOWN\n",
        new="    if undecided:\n        return EXIT_OK\n",
        tests=(
            "tests/test_doctor.py::test_td34_exit_priority_is_problem_then_unknown_then_ok",
            "tests/test_doctor_cli.py::test_td3_a_probe_timeout_is_unknown_and_exits_3",
            "tests/test_doctor_cli.py::test_td4_an_unreachable_server_exits_3_not_0",
            "tests/test_doctor_cli.py::test_td5_an_older_server_without_the_new_keys_exits_3",
        ),
    ),
    Mutant(
        # `/worker/spec` 의 `unreachable_checks` 를 늘 빈 목록으로 낸다 — 원격 워커의 doctor 가
        # 영영 안 오는 `local_only` 점검을 「점검 필요」로 읽는다(TO18, 위임 결정 2)
        name="doctor-spec-unreachable-checks-empty",
        path="src/remote_ci_monitor/capabilities.py",
        old="                        if not self.check_reachable(remote, c)\n",
        new="                        if False\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_to18_worker_spec_names_the_checks_a_remote_worker_cannot_reach",
            "tests/test_doctor_cli.py::test_to18_doctor_reads_the_real_servers_unreachable_checks",
        ),
    ),
    Mutant(
        # 잘린 XML 의 plist(ExpatError — ValueError 가 아니다)를 잡지 않는다 — 설치 자리에 깨진
        # plist 하나만 있어도 `--service-file` 없는 doctor 가 traceback 으로 끝난다(P3 1라운드 HIGH)
        name="doctor-service-file-expat-uncaught",
        path="src/remote_ci_monitor/doctor.py",
        old=(
            "    except (ValueError, plistlib.InvalidFileException, xml.parsers.expat.ExpatError)"
            " as e:\n"
        ),
        new="    except (ValueError, plistlib.InvalidFileException) as e:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_an_unreadable_plist_in_launch_agents_is_named_and_exits_3",
            "tests/test_doctor_cli.py::"
            "test_an_unreadable_explicit_service_file_is_unknown_not_a_crash",
        ),
    ),
    Mutant(
        # `rcm check` · `rcm worker --check` 의 git 행이 shim 규칙 없이 `shutil.which` 를 쓴다 — CLT
        # 없는 Mac 에서 shim 을 「있음」으로 세고 그것으로 `git ls-remote` 를 돌려 설치 창을 띄운다
        # (P3 1라운드 MED, 검증자 1 #2)
        name="check-git-row-skips-shim-rule",
        path="src/remote_ci_monitor/cli.py",
        old='    git = runner.find_tool("git", path)\n',
        new='    git = shutil.which("git", path=path)\n',
        tests=(
            "tests/test_doctor_cli.py::test_worker_check_git_row_follows_the_shim_rule",
            "tests/test_doctor_cli.py::test_rcm_check_git_row_follows_the_shim_rule",
        ),
    ),
    Mutant(
        # 서버가 재지 않은 로컬 레인 프리셋(`preset_env` 에 없음)을 빈 「빠진 도구」로 읽어 ready 라
        # 부른다(P3 1라운드 MED, 검증자 1 #3)
        name="doctor-local-lane-unmeasured-ready",
        path="src/remote_ci_monitor/doctor.py",
        old="            if got is None and p.requires and row.counts:\n",
        new="            if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_a_local_lane_preset_the_server_did_not_measure_is_unknown",
        ),
    ),
    Mutant(
        # 워커가 보낸 호스트 사실의 제어문자를 지우지 않는다 — ESC 가 `rcm machines` 의 터미널과
        # 웹에 그대로 나간다(P3 1라운드 MED, 검증자 2 #3)
        name="host-facts-control-chars-kept",
        path="src/remote_ci_monitor/capabilities.py",
        old="            out[key] = clean_line(value)[:HOST_FACT_MAX] or None\n",
        new="            out[key] = value[:HOST_FACT_MAX] or None\n",
        tests=(
            "tests/test_doctor_cli.py::test_host_facts_lose_control_characters_and_stop_at_64",
            "tests/test_doctor_cli.py::test_host_facts_lose_line_separators_and_bidi_controls",
        ),
    ),
    Mutant(
        # doctor 의 맨 바깥 경계가 없다 — 읽는 자리가 모르는 예외가 「판정 못 함」(종료 3)이 아니라
        # traceback 과 종료 1 로 끝난다(P3 2라운드 HIGH)
        name="doctor-boundary-removed",
        path="src/remote_ci_monitor/doctor.py",
        old="    except Exception as e:  # noqa: BLE001 — doctor 의 맨 바깥 경계\n",
        new="    except ZeroDivisionError as e:  # 변이: 경계가 없다\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_an_unexpected_error_is_could_not_decide_not_a_traceback",
        ),
    ),
    Mutant(
        # `reach` 가 서버 답의 모양을 보지 않는다 — HTML 200 · 빈 200 · [] · null · 칸 타입이 틀린
        # 답을 dict 로 믿고 쓰다 경계에 닿는다. 경계가 있어도 망가진 입력 표가 빨개진다(P3 2라운드)
        name="doctor-answer-shape-unchecked",
        path="src/remote_ci_monitor/doctor.py",
        old="            why = shape_problem(answer, ANSWER_SHAPES[route])\n",
        new="            why = None\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_a_server_answer_of_the_wrong_shape_is_named_and_exits_3",
        ),
    ),
    Mutant(
        # 필수 칸 검사를 끈다 — `{}` · 남의 서비스의 `{"status":"ok"}` · `version` 없는 health 를
        # rcm 서버의 답으로 믿고 0 이나 1 로 끝낸다. 생성형 표가 빨개진다(P3 3라운드 근본 원인)
        name="doctor-required-key-unchecked",
        path="src/remote_ci_monitor/doctor.py",
        old="                if sub.required:\n",
        new="                if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_a_required_fact_the_server_did_not_give_is_never_exit_0_or_1",
        ),
    ),
    Mutant(
        # 서비스 파일 후보를 못 읽어 고른 파일이 없는데도 기본 PATH 를 짐작해 잰다 — 빠진 도구가
        # 종료 1 이 된다. 순서 표가 빨개진다(P3 3라운드 HIGH)
        name="doctor-unreadable-candidate-path-guessed",
        path="src/remote_ci_monitor/doctor.py",
        old="        elif skipped:\n",
        new="        elif False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_an_unreadable_service_file_leaves_the_path_unknown_in_every_order",
        ),
    ),
    Mutant(
        # `placement` 없는 옛 서버의 프리셋을 `pool` 로 짐작한다 — 빠진 도구가 종료 1 이 된다(TD5,
        # 메인 세션 결정: 모름 · 종료 3)
        name="doctor-older-server-read-as-pool",
        path="src/remote_ci_monitor/core/doctor.py",
        old="    if placement is None:\n",
        new='    if placement is None:\n        placement = "pool"\n    if False:\n',
        tests=(
            "tests/test_doctor_cli.py::test_td5_an_older_server_with_a_missing_tool_exits_3_not_1",
        ),
    ),
    Mutant(
        # 설정 파일이 어디에도 없는 coordinator 를 기본값(프리셋 없음 · 8787)으로 잰다 — 그 포트의
        # 아무 서버에 묻고 0 이나 「프리셋 없음」(1)으로 끝난다(P3 4라운드 HIGH)
        name="doctor-coordinator-defaults-trusted",
        path="src/remote_ci_monitor/doctor.py",
        old="        if path is None:  # 서버 설정 파일이 어디에도 없다\n",
        new="        if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_a_coordinator_role_without_a_server_config_is_a_usage_error",
            "tests/test_doctor_cli.py::test_every_input_doctor_did_not_read_ends_in_2_or_3",
        ),
    ),
    Mutant(
        # 서비스 파일의 실행 인자(`--config` · `--port`)를 읽지 않는다 — 서비스가 X 로 떠도 표준
        # 자리나 기본값으로 잰다(P3 4라운드 HIGH)
        name="doctor-service-launch-ignored",
        path="src/remote_ci_monitor/doctor.py",
        old="        if sf.launch is None or sf.command != ROLE_COMMANDS.get(self.role):\n",
        new="        if True:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_the_service_files_config_argument_is_the_config_doctor_reads",
            "tests/test_doctor_cli.py::"
            "test_the_servers_service_arguments_and_environment_choose_the_port",
        ),
    ),
    Mutant(
        # 같은 자리에 못 읽은 형제 파일이 있어도 읽히는 파일로 잰다(P3 4라운드 LOW)
        name="doctor-unreadable-sibling-ignored",
        path="src/remote_ci_monitor/doctor.py",
        old="        if len(skipped) > before:\n",
        new="        if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_a_readable_file_next_to_an_unreadable_one_is_not_chosen",
        ),
    ),
    Mutant(
        # 실행 명령을 rcm 의 하위 명령으로 풀지 못한 서비스 파일을 조용히 뺀다(P3 4라운드 LOW)
        name="doctor-unresolved-command-dropped",
        path="src/remote_ci_monitor/doctor.py",
        old="        if sf.launch is None:\n",
        new="        if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_a_service_file_whose_rcm_command_rcm_cannot_tell_is_named",
        ),
    ),
    Mutant(
        # `git ls-remote` 의 시한 초과 · 실행 실패를 「닿지 않음」(FAIL, 종료 1)으로 단정한다
        # (P3 4라운드 MED)
        name="doctor-ls-remote-unsure-is-unreachable",
        path="src/remote_ci_monitor/doctor.py",
        old="    if isinstance(result, dc.Unsure):\n",
        new="    if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_a_git_ls_remote_that_times_out_or_cannot_run_is_unknown_not_unreachable",
        ),
    ),
    Mutant(
        # 모름인 프리셋 행이 「판정 못 함」 줄을 내지 않는다 — 옛 서버의 프리셋이 종료 0 이 된다
        # (3라운드에 생긴 규칙, P3 4라운드 검증자 1 #9)
        name="doctor-unknown-rows-not-undecided",
        path="src/remote_ci_monitor/doctor.py",
        old="        undecided += dc.unknown_rows(rows)\n",
        new="        undecided += []\n",
        tests=(
            "tests/test_doctor_cli.py::test_td5_an_older_server_with_a_missing_tool_exits_3_not_1",
        ),
    ),
    Mutant(
        # 읽지 못한 git 버전을 「낮다」(FAIL)로 단정한다(3라운드에 생긴 규칙, 검증자 1 #9)
        name="doctor-git-version-unknown-fails",
        path="src/remote_ci_monitor/doctor.py",
        old='            return requirement("git", "unknown", detail, needed)\n',
        new='            return requirement("git", "fail", detail, needed)\n',
        tests=(
            "tests/test_doctor_cli.py::test_a_git_version_rcm_cannot_read_is_unknown_not_a_fail",
        ),
    ),
    Mutant(
        # 오는지 모르는 프리셋이 있어도 「아무것도 오지 않는다」(종료 1)로 단정한다(3라운드에 생긴
        # 규칙, 검증자 1 #9)
        name="doctor-no-jobs-ignores-unknown",
        path="src/remote_ci_monitor/core/doctor.py",
        old="    if any(r.counts or r.applies == APPLIES_UNKNOWN for r in rows):\n",
        new="    if any(r.counts for r in rows):\n",
        tests=("tests/test_doctor.py::test_no_jobs_reason_does_not_fire_while_a_preset_may_come",),
    ),
    Mutant(
        # 후보 함수가 `local_only` 프리셋에서 원격 머신을 빼지 않는다 — 배치 · ETA · 제출 · 점검 ·
        # `/worker/spec` · claim · `/api/machines` 가 모두 원격을 후보로 본다(M5t-a 결정 254, TO15)
        name="m5t-local-only-remote-kept",
        path="src/remote_ci_monitor/capabilities.py",
        old='        views = [v for v in views if v.kind == "local"]  # 원격 제외',
        new="        pass  # 원격 제외",
        tests=(
            "tests/test_local_only.py::"
            "test_to15_no_entry_point_offers_a_local_only_preset_to_a_remote_machine",
            "tests/test_local_only.py::"
            "test_to18_a_remote_machine_does_not_wait_for_a_check_that_runs_only_locally",
            "tests/test_local_only.py::"
            "test_e1_a_remote_run_finishes_after_the_mark_and_its_retry_stays_off_remote",
            "tests/test_local_only.py::test_e3_a_local_only_retry_chain_is_never_claimed_remotely",
        ),
    ),
    Mutant(
        # `local_only` 거절 409(`no_capable_machine` · `tool_missing`)가 `rcm run` 에서
        # 「결과 모름」(3)이 된다 — 잡이 안 생긴 확실한 거절인데 세션이 다시 묻거나 기다린다
        # (M5t-a P3 2라운드 HIGH)
        name="m5t-cli-local-only-refusal-is-unknown",
        path="src/remote_ci_monitor/cli.py",
        old='_REFUSED_CODES += ("no_capable_machine", "tool_missing")',
        new="_REFUSED_CODES += ()",
        tests=("tests/test_local_only.py::test_to10_to16_rcm_run_exits_2_on_a_local_only_refusal",),
    ),
    Mutant(
        # 원격에서 다시 못 도는 점검(`local_only`)의 재매핑이 `check_needed` 에만 걸린다 — 표식을 단
        # 순간 그 머신을 겨눈 대기 점검 잡은 「점검 중」으로 끝없이 기다리고, 실패 기록은 409 로
        # 끝나는 다시 점검을 권한다(M5t-a P6 GAP X1)
        name="m5t-check-local-only-needed-only",
        path="src/remote_ci_monitor/capabilities.py",
        old="        if v.code not in _CHECK_CODES_GATED or report is None:",
        new="        if v.code != cap.CODE_CHECK_NEEDED or report is None:",
        tests=(
            "tests/test_local_only.py::"
            "test_x1_a_remote_check_record_follows_the_mark_unless_that_machine_is_running_it",
        ),
    ),
    Mutant(
        # 원격에서 다시 못 도는 점검(`local_only`)의 기록 가운데 그 머신이 이미 집은 잡의 예외를
        # 지운다 — 돌고 있는 점검이 끝나기도 전에 check_local_only 로 보이고, 그 결과가 오면 다시
        # 통과로 돌아온다(M5t-a P6 재확인, batch 7 T3)
        name="m5t-check-local-only-claimed-ignored",
        path="src/remote_ci_monitor/capabilities.py",
        old="    return state == cap.CHECK_RUNNING and claimed\n",
        new="    return False\n",
        tests=(
            "tests/test_local_only.py::"
            "test_x1_a_remote_check_record_follows_the_mark_unless_that_machine_is_running_it",
            "tests/test_local_only.py::"
            "test_t4_a_remote_check_being_cancelled_stays_running_until_it_ends",
        ),
    ),
    Mutant(
        # claim SQL 의 제외 조건만 지운다(바인딩 값도 함께 빼 SQL 은 그대로 돈다) — 후보 함수가
        # 뺀 `local_only` 프리셋을 원격 claim 이 네 갈래 어디서든 집는다(M5t-a P3, batch 7 T5)
        name="m5t-claim-excluded-sql-dropped",
        path="src/remote_ci_monitor/store.py",
        old=(
            """        excluded_sql = f" AND c.preset NOT IN ({','.join('?' * len(excluded))})" """
            """if excluded else ""\n"""
        ),
        new='        excluded, excluded_sql = (), ""\n',
        tests=(
            "tests/test_local_only.py::"
            "test_to15_the_claim_sql_drops_excluded_presets_on_every_branch",
        ),
    ),
    # ── #268 검증 사이클 2 의 1라운드 — 확정을 모름으로 · 셸이 서비스를 대신한다 ──
    Mutant(
        # rcm 의 서비스가 아님이 확실한 파일(ci-watch plist)도 「못 읽음」으로 적는다 — 모든 입력을
        # 읽은 실행이 종료 3 이 된다(HIGH 1)
        name="doctor-foreign-service-file-unreadable",
        path="src/remote_ci_monitor/doctor.py",
        old="        if sf.launch is None and not looks_like_rcm(sf):\n",
        new="        if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_the_ci_watch_plist_next_to_the_service_is_not_a_candidate",
            "tests/test_doctor_cli.py::test_only_files_that_look_like_rcm_are_named_unreadable",
        ),
    ),
    Mutant(
        # 서비스 인자에 --config 가 없으면 doctor 셸의 RCM_CONFIG 를 읽는다(HIGH 2)
        name="doctor-shell-rcm-config-leaks",
        path="src/remote_ci_monitor/doctor.py",
        old='        raw = launch.get("config") or env.get("RCM_CONFIG")\n',
        new='        raw = launch.get("config") or os.environ.get("RCM_CONFIG")\n',
        tests=(
            "tests/test_doctor_cli.py::"
            "test_the_doctor_shells_rcm_config_and_folder_never_choose_the_servers_config",
        ),
    ),
    Mutant(
        # 작업 폴더가 적혀 있지 않은 launchd 서비스의 `./rcm.toml` 을 doctor 가 선 폴더에서 찾는다
        # (HIGH 2)
        name="doctor-cwd-is-the-services-folder",
        path="src/remote_ci_monitor/doctor.py",
        old="            return [LAUNCHD_DEFAULT_WORKDIR]\n",
        new="            return [os.getcwd()]\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_the_doctor_shells_rcm_config_and_folder_never_choose_the_servers_config",
        ),
    ),
    Mutant(
        # 사람이 준 서비스 파일의 명령을 풀지 못해도 그 파일의 PATH 로 잰다(MED 3)
        name="doctor-explicit-unresolved-path-trusted",
        path="src/remote_ci_monitor/doctor.py",
        old=(
            "        unresolved = bool(self.opts.service_file) and sf is not None "
            "and sf.launch is None\n"
        ),
        new="        unresolved = False\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_an_explicit_service_file_whose_command_rcm_cannot_resolve_is_unknown",
        ),
    ),
    Mutant(
        # 사용자 유닛 자리의 `%h` 를 풀지 않는다 — rcm 이 아는 홈을 모름으로 만든다(MED 4)
        name="doctor-user-unit-home-unexpanded",
        path="src/remote_ci_monitor/doctor.py",
        old="        found = _matching(stage, role, skipped, user_home)\n",
        new="        found = _matching(stage, role, skipped, None)\n",
        tests=("tests/test_doctor_cli.py::test_h_in_a_user_unit_is_the_home_doctor_knows",),
    ),
    Mutant(
        # 출력 뒤 stdout 을 flush 하지 않는다 — 닫힌 파이프를 파이썬 종료 때 알아 종료 120 과
        # `Exception ignored … BrokenPipeError` 가 난다(P6 GAP E3)
        name="doctor-closed-pipe-at-exit",
        path="src/remote_ci_monitor/doctor.py",
        old="        sys.stdout.flush()\n",
        new="        pass\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_e3_a_closed_pipe_keeps_the_verdict_and_prints_no_traceback",
        ),
    ),
    Mutant(
        # git_ref 잡의 git 이 shim 규칙을 건너뛴다 — CLT 없는 Mac 에서 잡마다 설치 창이 뜬다
        # (P6 GAP R2)
        name="gitops-git-shim-runs",
        path="src/remote_ci_monitor/gitops.py",
        old='    return shutil.which("git", path=path) is not None and '
        'runner.find_tool("git", path) is None\n',
        new="    return False\n",
        tests=("tests/test_tool_shim.py::test_r2_git_ref_jobs_never_run_a_blocked_git_shim",),
    ),
    Mutant(
        # 서버가 잰 정의와 디스크의 정의를 견주지 않는다 — reload 전 서버의 옛 측정으로 ready 를
        # 낸다(P6 GAP E4)
        name="doctor-lane-generation-unchecked",
        path="src/remote_ci_monitor/doctor.py",
        old="            stale = got is not None and got.digest != mine\n",
        new="            stale = False\n",
        tests=(
            "tests/test_doctor_cli.py::test_e4_a_server_that_has_not_reloaded_the_config_is_unknown",
        ),
    ),
    Mutant(
        # 시스템 자리(`/etc/systemd/system` · `/Library/LaunchDaemons`)를 보지 않는다 — 문서의
        # Linux 서버 설치를 못 찾고 워커의 PATH 로 짐작한다(P3 사이클 2 r2 검증자 1 #1)
        name="doctor-system-service-place-ignored",
        path="src/remote_ci_monitor/doctor.py",
        old="    for stage in (launch_agents + user_units + system_files, staged):\n",
        new="    for stage in (launch_agents + user_units, staged):\n",
        # `examples/` 는 변이 사본에 없다 — 그 파일을 읽지 않는 LaunchDaemons 칸으로 본다
        tests=(
            "tests/test_doctor_cli.py::"
            "test_p3_the_documented_system_server_service_is_found[LaunchDaemons]",
        ),
    ),
    Mutant(
        # 시험의 운영 보호 가드가 운영 폴더의 조상인 허용 뿌리(홈에서 시작한 pytest 의 cwd)를 그대로
        # 둔다 — 그 실행에서는 가드가 아무것도 막지 않는다(P3 사이클 2 r2 두 명)
        name="test-guard-ancestor-root-kept",
        path="tests/conftest.py",
        old="    return tuple(sorted(r for r in roots if not any(_under(d, r) for d in "
        "PRODUCTION_DIRS)))\n",
        new="    return tuple(sorted(roots))\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_the_production_guard_fails_a_test_that_reaches_production[home]",
        ),
    ),
    Mutant(
        # `[[tools]]` 의 글자 검사가 `str.strip()` 한 값과 견준다 — 앞뒤의 `\r` · `\n` · U+2028 이
        # 거절되지 않고 그대로 저장된다(batch 10 재확인 X-bidi)
        name="config-tool-text-ends-unchecked",
        path="src/remote_ci_monitor/config.py",
        old='    fenced = f"|{value}|"\n',
        new="    fenced = value.strip()\n",
        tests=("tests/test_doctor.py::test_x_bidi_tools_refuse_controls_at_either_end_too",),
    ),
    Mutant(
        # 자기 세션의 git · 서비스 Python 을 Ctrl-C 때 거두지 않는다 — `Popen.__exit__` 가 0.25초
        # 뒤 두고 가서 그 손자가 doctor 뒤에 남는다(batch 10 재확인 Ctrl-C 자식)
        name="gitops-run-process-ctrl-c-leaves-group",
        path="src/remote_ci_monitor/gitops.py",
        old="        except BaseException:\n",
        new="        except subprocess.TimeoutExpired:\n",
        tests=(
            "tests/test_doctor_cli.py::test_x_ctrlc_run_process_takes_its_group_down_on_ctrl_c",
            "tests/test_doctor_cli.py::"
            "test_x_ctrlc_interrupting_doctor_leaves_no_git_or_service_python_behind",
        ),
    ),
    Mutant(
        # 서비스의 Python 이 CLT shim 인지 보지 않고 돌린다 — CLT 없는 Mac 에서 doctor 가 설치 창을
        # 띄운다(batch 10 재확인 X-python shim)
        name="doctor-service-python-shim-runs",
        path="src/remote_ci_monitor/doctor.py",
        old="        if runner.tool_blocked(python):\n",
        new="        if False:\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_x_python_a_service_python_that_is_a_clt_shim_is_never_run",
        ),
    ),
    Mutant(
        # 시험의 운영 보호 가드가 루프백 이름으로 가는 8787 만 막는다(옛 규칙) — 운영 서버는
        # 0.0.0.0 에 묶여 LAN 주소 · `.local` 이름으로도 닿는다(P6 GAP E7, batch 10 재확인 5)
        name="test-guard-port-on-loopback-only",
        path="tests/conftest.py",
        old="    return isinstance(address, tuple) and len(address) >= 2 and address[1] == "
        "PRODUCTION_PORT\n",
        new="    return (\n"
        "        isinstance(address, tuple)\n"
        "        and len(address) >= 2\n"
        "        and address[1] == PRODUCTION_PORT\n"
        '        and address[0] in ("127.0.0.1", "::1", "localhost")\n'
        "    )\n",
        tests=(
            "tests/test_doctor_cli.py::"
            "test_the_production_guard_blocks_port_8787_on_any_address[192.168.0.10]",
        ),
    ),
    Mutant(
        # `rcm serve` 의 기동 점검이 git 을 `shutil.which` 로 찾는다 — CLT 없는 Mac 의 shim 을
        # 있음으로 센다(P6 GAP R2, batch 10 재확인 6)
        name="config-startup-git-shim-counted",
        path="src/remote_ci_monitor/config.py",
        old='        if runner.find_tool("git", path) is None:\n',
        new='        if __import__("shutil").which("git", path=path) is None:\n',
        tests=(
            "tests/test_tool_shim.py::test_r2_server_startup_counts_a_blocked_git_shim_as_missing",
        ),
    ),
    # ── M5t-a PR4 — rcm setup (docs/m5t-workplan.md §4.1, TR31 · TR33) ──
    Mutant(
        # `rcm setup` 이 rcm.sqlite3 가 이미 있는 data_dir 을 거절하지 않는다 — 그 서비스가
        # setup 을 돌린 빌드로 남의 DB 를 열어 마이그레이션한다(TR31)
        name="setup-existing-data-dir-accepted",
        path="src/remote_ci_monitor/setup.py",
        old="        if not args.reuse_data_dir:\n",
        new="        if False:\n",
        tests=(
            "tests/test_setup.py::"
            "test_tr31_a_data_dir_that_already_holds_a_database_is_refused_unless_reused",
        ),
    ),
    Mutant(
        # guard 가 운영 data_dir(그 안 포함)을 고르는 `rcm setup` 을 막지 않는다(TR33)
        name="guard-setup-production-data-dir-allowed",
        path="tools/guard_production.py",
        old=(
            "            if inside(data, prod.data_dir) or inside(_real(data), "
            "real_prod.data_dir):\n"
        ),
        new="            if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::test_tr33_setup_on_the_production_data_dir_is_denied",
            "tests/test_guard_production_setup.py::"
            "test_tr33_the_template_default_resolves_with_the_commands_home",
        ),
    ),
    Mutant(
        # 인자 없는 `rcm` 이 설정이 있는 머신에서도 첫 실행 질문을 띄운다 — 이미 설정한
        # 머신(환경변수만 쓰는 클라이언트 · 워커 포함)에 setup 을 다시 권한다(TR2 · TR19 · TR29)
        name="setup-first-run-asks-with-config",
        path="src/remote_ci_monitor/setup.py",
        old="    if roles:\n        return None\n",
        new="    if False:\n        return None\n",
        tests=(
            "tests/test_setup_cli.py::test_tr2_a_terminal_with_any_config_does_not_ask",
            "tests/test_setup_cli.py::test_tr19_an_env_only_client_counts_as_configured",
            "tests/test_setup_cli.py::test_tr29_an_env_only_worker_counts_as_configured",
        ),
    ),
    Mutant(
        # doctor 가 `rcm setup --label` 로 설치한 서비스의 설치 자리를 보지 않는다 — 서비스 관리자가
        # 읽는 파일 대신 setup 자리의 사본을 잰다(6-B-V1 · 6-B-V2)
        name="doctor-custom-label-install-place-ignored",
        path="src/remote_ci_monitor/doctor.py",
        old="        if there.is_file() and there not in place:\n",
        new="        if False:\n",
        tests=(
            "tests/test_setup.py::"
            "test_tr24_doctor_finds_the_installed_copy_of_a_custom_label_service",
        ),
    ),
    # ── M5t-a PR4 P3 1라운드 — guard · setup 의 규칙마다 하나(v2-5 · v1-4) ──
    Mutant(
        # guard 가 운영 설정 폴더에 쓰는 `rcm setup` 을 막지 않는다(TR20 규칙 1)
        name="guard-setup-production-config-allowed",
        path="tools/guard_production.py",
        old=(
            "            hit = production_root(target, prod) or production_root(_real(target), "
            "real_prod)\n"
        ),
        new="            hit = None\n",
        tests=(
            "tests/test_guard_production_setup.py::test_tr20_setup_that_would_write_the_production_config_folder_is_denied",
            "tests/test_guard_production_setup.py::test_tr20_a_config_written_with_a_shell_variable_is_denied",
        ),
    ),
    Mutant(
        # guard 가 운영 라벨의 서비스 파일을 쓰는 `rcm setup` 을 막지 않는다(TR20 규칙 2)
        name="guard-setup-production-label-allowed",
        path="tools/guard_production.py",
        old="            if label in SETUP_STANDARD_LABELS or unit_name(label) in units:\n",
        new="            if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::test_tr20_setup_that_would_write_a_production_label_is_denied",
            "tests/test_guard_production_setup.py::test_tr20_no_label_means_the_standard_one_and_is_denied_but_a_client_has_none",
        ),
    ),
    Mutant(
        # guard 가 운영 서비스 파일 자리를 가리키는 `--service-file` 을 막지 않는다(P3 v1-2)
        name="guard-setup-service-place-allowed",
        path="tools/guard_production.py",
        old="            place = _service_place(target, session_home)\n",
        new="            place = None\n",
        tests=(
            "tests/test_guard_production_setup.py::test_tr20_a_service_file_on_a_production_service_place_is_denied",
            "tests/test_guard_production_setup.py::test_tr20_every_production_service_unit_is_a_place_setup_may_not_write",
            "tests/test_guard_production_setup.py::test_tr20_setup_may_not_write_its_config_into_any_production_root",
        ),
    ),
    Mutant(
        # setup 이 줄인 옵션을 받는다 — guard 가 못 본 경로에 쓴다(P3 v1-1 · v2-2)
        name="setup-abbreviated-options-accepted",
        path="src/remote_ci_monitor/core/setup.py",
        old='PARSER_OPTIONS: dict[str, Any] = {"allow_abbrev": False}\n',
        new='PARSER_OPTIONS: dict[str, Any] = {"allow_abbrev": True}\n',
        tests=("tests/test_setup.py::test_tr20_setup_takes_no_abbreviated_option",),
    ),
    Mutant(
        # setup 이 설치 자리(~/Library/LaunchAgents …)에 서비스 파일을 쓴다(§4.1, P3 v1-2)
        name="setup-install-place-written",
        path="src/remote_ci_monitor/setup.py",
        old="    if place is not None:\n        # 설치 자리에 쓰지 않는다(§4.1, ⛔ Q9)",
        new="    if False:\n        # 설치 자리에 쓰지 않는다(§4.1, ⛔ Q9)",
        tests=("tests/test_setup.py::test_tr24_setup_refuses_a_service_file_on_an_install_place",),
    ),
    Mutant(
        # setup 이 있는 파일을 `--force` 없이 덮는다(TR6)
        name="setup-existing-file-overwritten",
        path="src/remote_ci_monitor/setup.py",
        old="    blocked = [f for f in plan.files if f.path.exists() and f.kind not in forced]\n",
        new="    blocked = []\n",
        tests=(
            "tests/test_setup.py::test_tr6_an_existing_file_is_kept_and_only_the_forced_kind_is_replaced",
            "tests/test_setup.py::test_tr6_an_existing_service_file_is_kept_without_force_and_nothing_else_is_written",
        ),
    ),
    Mutant(
        # setup 이 쓰다 멈춘 뒤 이미 쓴 파일을 되돌리지 않는다 — 반쪽 설정이 남는다(TR7)
        name="setup-partial-write-not-rolled-back",
        path="src/remote_ci_monitor/setup.py",
        old="        for written, old, mode in reversed(done):\n",
        new="        for written, old, mode in []:\n",
        tests=("tests/test_setup.py::test_tr7_a_failure_part_way_leaves_no_half_written_config",),
    ),
    Mutant(
        # 인자 없는 `rcm` 이 터미널이 아닌데도 첫 실행 질문을 띄운다(TR1, 결정 240)
        name="setup-first-run-asks-without-a-terminal",
        path="src/remote_ci_monitor/setup.py",
        old="    if not world.interactive():\n        return None\n",
        new="    if False:\n        return None\n",
        tests=("tests/test_setup_cli.py::test_tr1_without_a_terminal_bare_rcm_never_asks",),
    ),
    Mutant(
        # 대화형 숫자를 `isdigit()` 로 받는다 — `²` 에서 traceback(P3 v2-3)
        name="setup-non-ascii-digit-accepted",
        path="src/remote_ci_monitor/setup.py",
        old="    if answer.isascii() and answer.isdecimal():\n",
        new="    if answer.isdigit():\n",
        tests=("tests/test_setup.py::test_tr22_a_digit_that_is_not_ascii_is_asked_again",),
    ),
    Mutant(
        # setup 의 맨 바깥 경계가 없다 — 예상 못 한 예외가 traceback 으로 나온다(P3 v2-3)
        name="setup-boundary-missing",
        path="src/remote_ci_monitor/setup.py",
        old="    except Exception as e:  # noqa: BLE001 — setup 의 맨 바깥 경계\n",
        new="    except ZeroDivisionError as e:  # noqa: BLE001 — setup 의 맨 바깥 경계\n",
        tests=("tests/test_setup.py::test_tr28_an_unexpected_error_is_one_line_and_exit_one",),
    ),
    Mutant(
        # systemd 유닛의 `%` 를 지정자로 둔다 — `%h` 가 든 경로가 다른 경로로 읽힌다(P3 v2-4)
        name="setup-unit-percent-unescaped",
        path="src/remote_ci_monitor/core/setup.py",
        old='    return value.replace("%", "%%")\n',
        new="    return value\n",
        tests=("tests/test_setup.py::test_tr11_a_systemd_unit_keeps_odd_paths_whole",),
    ),
    Mutant(
        # 서버 서비스 파일의 PATH 맨 앞에 rcm 의 venv bin 을 둔다 — 프리셋의 `python` 이 rcm 의
        # 것으로 바뀐다(TR11)
        name="setup-service-path-starts-with-the-venv",
        path="src/remote_ci_monitor/setup.py",
        old="            path=cli.WORKER_SERVICE_PATH,\n",
        new='            path=f"{rcm.parent}:{cli.WORKER_SERVICE_PATH}",\n',
        tests=(
            "tests/test_setup.py::test_tr11_the_written_service_file_path_does_not_start_with_the_venv",
        ),
    ),
    Mutant(
        # LAN bind 의 경고가 없는 읽기 인증 모드(`read_auth`)를 권한다(TR10, Q14)
        name="setup-bind-warning-offers-read-auth",
        path="src/remote_ci_monitor/core/setup.py",
        old="        return [READ_OPEN, DHCP_NOTE]\n",
        new='        return [READ_OPEN, DHCP_NOTE, "set read_auth = token to close reads"]\n',
        tests=("tests/test_setup.py::test_tr10_the_interactive_run_never_offers_a_read_auth_mode",),
    ),
    # ── M5t-a PR4 P3 2라운드 — guard 와 setup 이 같은 해석기를 쓴다(batch-4) ──
    Mutant(
        # 빈 `--config` · `--data-dir` 을 받는다 — 빈 `--config` 는 기본 자리(운영 설정 폴더일 수
        # 있다)로 떨어진다(P3 2라운드 v2-1). setup 과 guard 가 같은 `blank_flags` 를 쓴다
        name="setup-blank-value-accepted",
        path="src/remote_ci_monitor/core/setup.py",
        old="        if isinstance(values.get(name), str) and not values[name].strip()\n",
        new="        if False\n",
        tests=(
            "tests/test_setup.py::test_tr20_an_empty_path_or_label_is_a_usage_error",
            "tests/test_guard_production_setup.py::test_tr20_an_empty_value_is_denied",
        ),
    ),
    Mutant(
        # client 에도 서비스 파일 자리를 준다 — guard 가 client 가 쓰지 않는 `--service-file` 을
        # 막고, 해석기의 답이 setup 이 쓴 자리와 달라진다(P3 2라운드 LOW)
        name="setup-client-gets-a-service-file",
        path="src/remote_ci_monitor/core/setup.py",
        old="    if chosen is not None and manager is not None:\n",
        new="    if manager is not None:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_a_client_writes_no_service_file_so_its_service_file_flag_is_free",
            "tests/test_setup.py::test_tr20_setup_writes_exactly_where_the_shared_resolver_says",
        ),
    ),
    Mutant(
        # setup 이 설치 자리를 대소문자를 가려 견준다 — macOS 에서 `~/library/launchagents` 에
        # 쓴다(P3 2라운드)
        name="setup-install-place-case-not-folded",
        path="src/remote_ci_monitor/core/setup.py",
        old="    if fold_case:\n",
        new="    if False:\n",
        tests=("tests/test_setup.py::test_tr24_an_install_place_in_another_case_is_refused",),
    ),
    Mutant(
        # 라벨 정규식을 앞에서만 맞춘다 — 끝 줄바꿈이 든 라벨이 plist 에 들어간다(P3 2라운드 v1-5)
        name="setup-label-trailing-newline-accepted",
        path="src/remote_ci_monitor/core/setup.py",
        old="    if _LABEL_RE.fullmatch(label):\n",
        new="    if _LABEL_RE.match(label):\n",
        tests=(
            "tests/test_setup.py::test_tr20_a_label_with_a_trailing_line_break_is_a_usage_error",
        ),
    ),
    Mutant(
        # 파일을 다 쓴 뒤 보고가 터지면 종료 1 이다 — 쓴 설정을 실패로 알린다(§4.1, P3 2라운드)
        name="setup-report-failure-exit-one",
        path="src/remote_ci_monitor/setup.py",
        old=(
            '        _say(world, f"rcm: files written; the report failed: {_one_line(e)}")\n'
            "        return EXIT_OK\n"
        ),
        new=(
            '        _say(world, f"rcm: files written; the report failed: {_one_line(e)}")\n'
            "        return EXIT_FAILED\n"
        ),
        tests=("tests/test_setup.py::test_tr28_a_report_that_fails_after_writing_is_exit_zero",),
    ),
    Mutant(
        # guard 가 경로를 대소문자를 가려 견준다 — macOS 에서 `~/.config/RCM` 이 운영 설정 폴더인데
        # 지나간다(P3 2라운드)
        name="guard-setup-case-not-folded",
        path="tools/guard_production.py",
        old="    if FOLD_CASE:\n",
        new="    if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_a_production_path_in_another_case_is_denied_on_a_case_insensitive_disk",
        ),
    ),
    Mutant(
        # guard 가 setup 의 해석기를 못 들여오면 setup 을 허용한다(fail open, P3 2라운드)
        name="guard-setup-resolver-import-fails-open",
        path="tools/guard_production.py",
        old=(
            "    except Exception as e:  # noqa: BLE001 — 해석기를 못 들여오면 setup 을 막는다"
            "(fail closed)\n"
        ),
        new=(
            "    except Exception as e:  # noqa: BLE001 — 해석기를 못 들여오면 setup 을 막는다"
            "(fail closed)\n        return None\n"
        ),
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_the_hook_refuses_setup_when_the_resolver_does_not_load",
        ),
    ),
    Mutant(
        # guard 가 python 의 인터프리터 플래그를 건너뛰지 않는다 — `python -u -m remote_ci_monitor
        # setup` 이 setup 규칙을 지난다(P3 2라운드)
        name="guard-python-flags-not-skipped",
        path="tools/guard_production.py",
        old=(
            '        elif token.startswith("-") and token not in ("-", "--") and not '
            'token.startswith("-c"):\n'
        ),
        new="        elif False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_a_wrapper_path_or_interpreter_flag_still_reaches_setup",
            # P3 4라운드: 그물이 막는 쪽을 덮으므로 허용 칸이 본다
            "tests/test_guard_production_setup.py::"
            "test_tr20_python_flags_with_a_test_home_are_read_not_caught_by_the_net",
        ),
    ),
    # P3 2라운드 v2-2 가 짚은, 변이가 없던 규칙 가운데 싼 것
    Mutant(
        # 역할이 쓰지 않는 플래그를 조용히 버린다(P3 v1-7)
        name="setup-unused-flag-silent",
        path="src/remote_ci_monitor/setup.py",
        old="    if unused:\n",
        new="    if False:\n",
        tests=("tests/test_setup.py::test_tr22_a_flag_the_role_does_not_use_is_a_warning",),
    ),
    Mutant(
        # 포트가 비었는지 모를 때 아무 말도 하지 않는다(P3 v1-5)
        name="setup-unknown-port-silent",
        path="src/remote_ci_monitor/setup.py",
        old="    elif held is None:  # 모름은 모름이라고 쓴다(P3 v1-5)\n",
        new="    elif False:  # 모름은 모름이라고 쓴다(P3 v1-5)\n",
        tests=("tests/test_setup.py::test_tr22_an_unknown_answer_from_the_system_is_said",),
    ),
    Mutant(
        # 호스트 없는 URL(`http://`)을 설정에 쓴다(P3 v2-12)
        name="setup-url-without-host-accepted",
        path="src/remote_ci_monitor/setup.py",
        old="    if not host:  # `http://` · `http:///x` · `http://:8787`",
        new="    if False:  # `http://` · `http:///x` · `http://:8787`",
        tests=("tests/test_setup.py::test_tr26_a_url_without_a_host_is_a_usage_error",),
    ),
    Mutant(
        # UTF-8 이 아닌 인자를 쓰기 전에 거르지 않는다(P3 v2-10)
        name="setup-not-utf8-accepted",
        path="src/remote_ci_monitor/setup.py",
        old='            value.encode("utf-8")\n',
        new='            value.encode("utf-8", "surrogateescape")\n',
        tests=("tests/test_setup.py::test_tr28_a_path_that_is_not_utf8_is_a_usage_error",),
    ),
    Mutant(
        # 되돌리지 못한 파일을 삼킨다 — 「nothing was written」이라고 거짓말한다(P3 v1-6)
        name="setup-rollback-failure-swallowed",
        path="src/remote_ci_monitor/setup.py",
        old="                plan.unrestored.append(written.path)\n",
        new="                pass\n",
        tests=("tests/test_setup.py::test_tr7_a_rollback_that_fails_is_named",),
    ),
    Mutant(
        # 시작하지 않았을 때 조용히 끝난다 — `|| echo "not started …"` 가 없다(P3 v1-9)
        name="setup-not-started-silent",
        path="src/remote_ci_monitor/core/setup.py",
        old="    return [folders, copy, same + start + said]\n",
        new="    return [folders, copy, same + start]\n",
        tests=("tests/test_setup.py::test_tr36_the_start_line_says_when_it_did_not_start",),
    ),
    Mutant(
        # systemd ExecStart 의 `$` 를 `$$` 로 쓰지 않는다 — `$HOME` 이 든 경로를 systemd 가
        # 펼친다(P3 v2-4)
        name="setup-unit-dollar-unescaped",
        path="src/remote_ci_monitor/core/setup.py",
        old='        text = text.replace("$", "$$")\n',
        new="        text = text\n",
        tests=("tests/test_setup.py::test_tr11_execstart_writes_a_dollar_twice",),
    ),
    # ── M5t-a PR4 P3 3라운드 — setup 규칙의 「운영」을 집의 정의 하나로(batch-5) ──
    Mutant(
        # setup 규칙이 운영 설정 폴더만 「운영」으로 본다 — 체크아웃 · 서비스 venv · data_dir 에
        # 쓰는 setup 이 지나간다(P3 3라운드 v1-1, 근본 원인)
        name="guard-setup-narrow-production-list",
        path="tools/guard_production.py",
        old=(
            "            hit = production_root(target, prod) or production_root(_real(target), "
            "real_prod)\n"
        ),
        new="            hit = production_root(target, Production(config_dir=prod.config_dir))\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_setup_may_not_write_its_config_into_any_production_root",
            "tests/test_guard_production_setup.py::"
            "test_tr20_the_setup_rule_and_the_edit_rule_share_one_production_list",
        ),
    ),
    Mutant(
        # setup 이 서비스 관리자가 읽는 자리 안의 `--config` 를 쓴다 — 운영 서비스 파일을
        # `--force config` 로 덮는다(P3 3라운드 v1-1)
        name="setup-config-in-install-place-written",
        path="src/remote_ci_monitor/setup.py",
        old="    place = _install_place(found.config, world)\n",
        new="    place = None\n",
        tests=("tests/test_setup.py::test_tr24_setup_refuses_a_config_inside_an_install_place",),
    ),
    Mutant(
        # 닫는 쪽의 그물이 없다 — `sudo -u` · `if …; then` · `python -Im` 뒤의 setup 이 setup 규칙을
        # 거치지 않는다(P3 3라운드 v1-2 · v1-3 · v2-3). P3 4라운드부터 그물은 `_setup_net` 하나다
        name="guard-setup-unread-call-allowed",
        path="tools/guard_production.py",
        old="    if prod.known and _setup_net(command):\n",
        new="    if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_a_setup_the_hook_cannot_read_as_a_plain_call_is_denied",
        ),
    ),
    Mutant(
        # mutcheck 가 문법 오류인 변이를 「잡혔다」로 센다 — 들여쓰기를 잃은 변이가 시험 없이
        # 통과했다(P3 3라운드 v2-1)
        name="mutcheck-syntax-error-counted-as-caught",
        path="scripts/mutcheck.py",
        old="    problem = syntax_problem(m.path, changed)\n",
        new="    problem = None\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_mutcheck_reports_a_mutant_that_does_not_parse_as_a_failure",
        ),
    ),
    Mutant(
        # 파일을 다 쓴 뒤 보고 중의 Ctrl-C 가 130 이다 — 쓴 설정을 실패로 알린다(§4.1, P3
        # 3라운드 v2-2)
        name="setup-report-interrupt-exit-130",
        path="src/remote_ci_monitor/setup.py",
        old=(
            "        return EXIT_OK\n"
            "    except Exception as e:  # noqa: BLE001 — 파일은 이미 다 썼다\n"
        ),
        new=(
            "        return EXIT_INTERRUPTED\n"
            "    except Exception as e:  # noqa: BLE001 — 파일은 이미 다 썼다\n"
        ),
        tests=("tests/test_setup.py::test_tr28_ctrl_c_while_reporting_after_writing_is_exit_zero",),
    ),
    Mutant(
        # 해석기가 `~user` 를 제 손으로 passwd 에서 읽는다 — core 의 순수성이 깨진다(P3 3라운드
        # v1-5)
        name="setup-resolver-reads-passwd",
        path="src/remote_ci_monitor/core/setup.py",
        old="        text = expand_user(raw)\n",
        new="        text = os.path.expanduser(raw)\n",
        tests=(
            "tests/test_setup.py::test_tr20_the_resolver_reads_nothing_from_the_machine",
            "tests/test_setup.py::"
            "test_tr20_where_takes_the_user_lookup_and_the_case_rule_from_its_caller",
        ),
    ),
    Mutant(
        # setup 판정 중 훅이 터지면 허용한다(fail open, P3 3라운드)
        name="guard-setup-crash-allowed",
        path="tools/guard_production.py",
        old="        if not _about_setup(payload):\n",
        new="        if True:\n",
        tests=(
            "tests/test_guard_production_setup.py::test_tr20_a_crash_while_judging_setup_is_a_deny",
        ),
    ),
    # ── M5t-a PR4 P3 4라운드 — setup 판정은 허용 목록이다(batch-6) ──
    Mutant(
        # 허용 모양 검사가 메타 문자 하나(`&`)를 놓친다 — `rcm setup --help & rcm setup …` 이
        # 도움말로 읽혀 지나간다(P3 4라운드)
        name="guard-setup-plain-misses-a-metachar",
        path="tools/guard_production.py",
        old='_PLAIN_FORBIDDEN = frozenset(";&|$`(){}<>\\\\*?[]#!^")\n',
        new='_PLAIN_FORBIDDEN = frozenset(";|$`(){}<>\\\\*?[]#!^")\n',
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_round4_shell_tricks_around_setup_are_denied",
            "tests/test_guard_production_setup.py::"
            "test_tr20_setup_help_passes_only_in_the_plain_shape",
        ),
    ),
    Mutant(
        # setup 의 상대 경로를 받는다 — 훅이 짐작한 cwd 로 풀어 시험 폴더로 본다(P3 4라운드)
        name="guard-setup-relative-path-allowed",
        path="tools/guard_production.py",
        old="                if value is not None and not _anchored(value):\n",
        new="                if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr33_a_relative_path_is_denied_wherever_the_session_stands",
            "tests/test_guard_production_setup.py::"
            "test_tr33_a_relative_data_dir_is_denied_in_any_working_folder",
        ),
    ),
    Mutant(
        # 심볼릭 링크를 풀지 않는다 — 운영 뿌리를 가리키는 링크 아래 경로가 지나간다(P3 4라운드
        # LOW 6)
        name="guard-setup-realpath-skipped",
        path="tools/guard_production.py",
        old="    return Path(_realpath(str(path)))\n",
        new="    return path\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_a_symlink_into_a_production_root_is_denied",
        ),
    ),
    Mutant(
        # 그물이 rcm 다음 하위 명령 자리를 보지 않는다 — `rcm ${S}tup` · `$R ${S}tup` 이 낱말 setup
        # 없이 지나간다(P3 4라운드)
        name="guard-setup-subcommand-place-unchecked",
        path="tools/guard_production.py",
        old="    for position in range(index + 1, len(tokens)):\n",
        new="    return False\n    for position in range(index + 1, len(tokens)):\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_a_subcommand_the_shell_makes_after_rcm_is_denied",
            "tests/test_guard_production_setup.py::"
            "test_tr20_round4_shell_tricks_around_setup_are_denied",
        ),
    ),
    # ── M5t-a PR4 P3 5라운드 · P6 (batch 7) — guard 의 남은 길과 변이 없던 규칙 ──
    Mutant(
        # 인자를 넘기는 런처(xargs · parallel · find -exec · fd -x …) 낱말을 그물이 보지 않는다 —
        # `… | xargs rcm` 이 낱말 setup 없이 setup 에 닿는다(P3 5라운드 v1-1, P6 재확인 1)
        name="guard-setup-xargs-feeder-allowed",
        path="tools/guard_production.py",
        old="    if names & _FEEDERS or _FEEDER_FLAGS.intersection(words):\n",
        new="    if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_b8_1_a_launcher_word_anywhere_with_an_rcm_word_is_denied",
        ),
    ),
    Mutant(
        # 터미널을 주는 런처(script · unbuffer · expect · tmux …) 낱말을 그물이 보지 않는다 —
        # `script -q /dev/null rcm` 의 첫 실행 질문이 setup 을 돌린다(P3 5라운드 v1-2, P6 재확인 1)
        name="guard-setup-pty-bare-rcm-allowed",
        path="tools/guard_production.py",
        old="    if names & _PTY_LAUNCHERS:\n",
        new="    if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_b8_1_a_launcher_word_anywhere_with_an_rcm_word_is_denied",
        ),
    ),
    Mutant(
        # 펼쳐 rcm 이 되는 글롭(`r?m`)을 rcm 낱말로 보지 않는다 — 리다이렉션이 앞에 붙어 명령 자리
        # 규칙이 못 보는 `<f xargs ./.venv/bin/r?m` 이 지나간다(P6 재확인 1)
        name="guard-launcher-glob-rcm-missed",
        path="tools/guard_production.py",
        old='    if any(c in name for c in "*?[") and any(\n',
        new="    if False and any(\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_b8_1_a_launcher_word_anywhere_with_an_rcm_word_is_denied",
        ),
    ),
    Mutant(
        # 셸이 바꾸는 글자(리다이렉션 `<` 포함)가 든 낱말을 모두 rcm 으로 본다 — rcm 이 없는
        # `xargs python3 x.py < /tmp/a` 를 막는다(과잉 차단, P6 재확인 1 · v2-5)
        name="guard-launcher-any-shaped-word-counts",
        path="tools/guard_production.py",
        old='    if "$" in token or "`" in token:\n',
        new="    if any(c in _SHAPING for c in token):\n        return True\n    if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::test_b8_1_a_launcher_without_an_rcm_word_passes",
        ),
    ),
    Mutant(
        # 서비스가 실행하는 venv 의 원본이 링크된 워크트리면 운영에서 뺀다 — 워크트리에서 도는
        # 운영 서비스의 체크아웃 · venv 를 편집 · setup 규칙이 모른다(P3 5라운드 v1-3)
        name="guard-linked-worktree-service-dropped",
        path="tools/guard_production.py",
        old="        if _is_linked_worktree(checkout) and not runs:\n",
        new="        if _is_linked_worktree(checkout):\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_g3_a_service_venv_that_installs_a_linked_worktree_is_production",
        ),
    ),
    Mutant(
        # 체크아웃 안의 venv 는 서비스가 실행해도 운영에서 뺀다 — 서비스가 워크트리의 `.venv` 를
        # 실행하는 머신에서 운영 보호가 꺼진다(P6 재확인 2 · v2-2)
        name="guard-service-venv-inside-checkout-dropped",
        path="tools/guard_production.py",
        old="        if checkout is None or (inside(venv, checkout) and not runs):\n",
        new="        if checkout is None or inside(venv, checkout):\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_b8_2_a_service_that_runs_the_venv_inside_its_checkout_is_production",
        ),
    ),
    Mutant(
        # `nice` 를 떼지 않는다 — `nice -n 5 rcm token --config <운영>` 이 지나간다(P6 재확인 7)
        name="guard-nice-not-stripped",
        path="tools/guard_production.py",
        old='        if _name(head) == "nice":\n',
        new="        if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::test_b8_7_nice_timeout_and_env_split_do_not_hide_rcm",
        ),
    ),
    Mutant(
        # timeout 의 시간 낱말을 남긴다 — `timeout 30 rcm serve` 의 명령이 `30` 이 된다(P6 재확인 7)
        name="guard-timeout-duration-kept",
        path="tools/guard_production.py",
        old="            if index < len(tokens) and _DURATION.fullmatch(tokens[index]):\n",
        new="            if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::test_b8_7_nice_timeout_and_env_split_do_not_hide_rcm",
        ),
    ),
    Mutant(
        # `env -S '…'` 의 글을 나누지 않는다 — 그 안의 rcm 을 보지 못한다(P6 재확인 7)
        name="guard-env-split-string-ignored",
        path="tools/guard_production.py",
        old="            if split is not None:\n",
        new="            if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::test_b8_7_nice_timeout_and_env_split_do_not_hide_rcm",
        ),
    ),
    Mutant(
        # `remote_ci_monitor` 경로 조각(`src/remote_ci_monitor/…`)을 rcm 낱말로 센다 — 그 폴더를
        # 읽는 `find … -exec ruff` · `git ls-files … | xargs` 가 막힌다(P6 재확인 354 · v2-1)
        name="guard-rcm-word-path-fragment-counts",
        path="tools/guard_production.py",
        old='    r"|(?:(?<![\\w/-])|(?<![\\w])-[A-Za-z]*m)remote_ci_monitor(?![\\w/-])",\n',
        new='    r"|(?:(?<![\\w-])|(?<![\\w])-[A-Za-z]*m)remote_ci_monitor(?![\\w-])",\n',
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_b9_354_a_path_fragment_or_an_argument_variable_beside_a_launcher_passes",
        ),
    ),
    Mutant(
        # 펼친 끝 조각을 보지 않는다 — `xargs ruff check "$PWD/x"` 가 막힌다(P6 재확인 354 · v2-2)
        name="guard-launcher-variable-tail-ignored",
        path="tools/guard_production.py",
        old="        return after and _open_tail(token)\n",
        new="        return after\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_b9_354_a_path_fragment_or_an_argument_variable_beside_a_launcher_passes",
        ),
    ),
    Mutant(
        # 런처 앞의 변수도 센다 — `echo $HOME | xargs ls` 가 막힌다(P6 재확인 354)
        name="guard-launcher-variable-before-counts",
        path="tools/guard_production.py",
        old="        return after and _open_tail(token)\n",
        new="        return _open_tail(token)\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_b9_354_a_path_fragment_or_an_argument_variable_beside_a_launcher_passes",
        ),
    ),
    Mutant(
        # 런처 규칙의 거절이 원인과 나누는 법을 말하지 않는다(P6 재확인 354)
        name="guard-launcher-deny-cause-dropped",
        path="tools/guard_production.py",
        old="        return _not_plain(_launcher_cause(command))\n",
        new="        return _not_plain()\n",
        tests=(
            "tests/test_guard_production_setup.py::test_b9_354_the_launcher_deny_names_its_cause_and_says_split",
        ),
    ),
    Mutant(
        # timeout 의 시간을 `\d+(.\d+)?` 로만 읽는다 — `timeout .5 rcm serve` 의 명령이 `.5` 가
        # 된다(P6 재확인 v1-6 · v2-5)
        name="guard-timeout-strtod-duration-missed",
        path="tools/guard_production.py",
        old=(
            '    r"(?:(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:e[+-]?\\d+)?|inf(?:inity)?)[smhd]?", '
            "re.IGNORECASE\n"
        ),
        new='    r"\\d+(?:\\.\\d+)?[smhd]?"\n',
        tests=(
            "tests/test_guard_production_setup.py::test_b9_timeout_durations_that_strtod_reads_are_stripped",
        ),
    ),
    Mutant(
        # 감싸기를 이름(basename)이 아니라 글자로 본다 — `/usr/bin/env rcm token --config <운영>`
        # · `/usr/bin/nohup rcm serve` 가 지나간다(P3 5라운드 v2-1)
        name="guard-wrapper-path-missed",
        path="tools/guard_production.py",
        old="        if _name(head) in WRAPPERS:\n",
        new="        if head in WRAPPERS:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_g4_a_wrapper_named_by_its_path_does_not_hide_rcm",
        ),
    ),
    Mutant(
        # 반복한 값 플래그의 앞 값을 보지 않는다 — `--data-dir <운영> --data-dir /tmp/d` 처럼 앞에
        # 둔 운영 값이 빠진다(P3 5라운드 v2-3)
        name="guard-setup-earlier-values-skipped",
        path="tools/guard_production.py",
        old="    for flag in _SETUP_VALUE_FLAGS:\n",
        new="    return out\n    for flag in _SETUP_VALUE_FLAGS:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_g6_an_earlier_value_of_a_repeated_flag_is_judged_too",
        ),
    ),
    Mutant(
        # 앞머리의 상대 HOME · XDG_CONFIG_HOME 을 받는다 — 작업 폴더에 기댄 자리를 훅이 믿는다
        # (P3 5라운드 v2-3)
        name="guard-setup-relative-home-allowed",
        path="tools/guard_production.py",
        old=(
            '        if value and not (value.startswith("/") or (name in _ENV_TILDE_OK and '
            "_anchored(value))):\n"
        ),
        new="        if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr33_a_relative_home_or_config_home_in_front_is_denied",
        ),
    ),
    Mutant(
        # 앞머리 값의 맨 앞이 아닌 `~`(`a:~/b`)를 받는다 — 셸은 펼치고 훅은 글자로 본다(P3 5라운드
        # v2-3)
        name="guard-setup-inner-tilde-allowed",
        path="tools/guard_production.py",
        old='        if "~" in value[1:]:\n',
        new="        if False:\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_g6_a_tilde_past_the_start_of_a_prefix_value_is_not_the_plain_shape",
        ),
    ),
    Mutant(
        # 경로 값의 `~` 를 셸(세션의 HOME)로 푸는 갈래를 뺀다 — `HOME=<시험> rcm setup --data-dir
        # ~/.local/share/rcm` 이 운영 data_dir 을 겨눠도 지나간다(P3 5라운드 v2-3)
        name="guard-setup-session-tilde-skipped",
        path="tools/guard_production.py",
        old="            options.append(_home_joined(session_home, value))\n",
        new="            pass\n",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr33_the_shell_expands_tilde_and_variables_with_its_own_home_not_the_prefix",
        ),
    ),
    Mutant(
        # 허용 모양의 `--help` 도 해석기에 넘긴다 — 해석기가 거절해 평범한 `rcm setup --help` 가
        # 막힌다(P3 5라운드 v2-3)
        name="guard-setup-help-judged",
        path="tools/guard_production.py",
        old="    if _wants_help(list(plain.args)):\n        return None\n",
        new="",
        tests=(
            "tests/test_guard_production_setup.py::"
            "test_tr20_the_plain_shapes_reach_the_resolver_and_pass",
        ),
    ),
    # ── M5t-a PR4 P5 GAP (batch 7) — setup 의 출력 · 조회 · 스캔 ──
    Mutant(
        # launchctl 의 0 아닌 종료를 모두 「올라가지 않음」으로 읽는다 — 도메인이 없는 머신에서
        # 「모름」 경고 없이 시작 명령을 찍는다(P5 A-E2)
        name="setup-launchd-unknown-read-as-absent",
        path="src/remote_ci_monitor/setup.py",
        old="        return False if LAUNCHD_NO_SERVICE in text else None\n",
        new="        return False\n",
        tests=("tests/test_setup.py::test_s7_launchd_only_a_missing_service_is_not_loaded",),
    ),
    Mutant(
        # systemctl 의 버스 실패(1)도 「올라가지 않음」으로 읽는다(P5 A-E2)
        name="setup-systemd-unknown-read-as-absent",
        path="src/remote_ci_monitor/setup.py",
        old="    if active is None or active[0] != SYSTEMD_INACTIVE:\n",
        new="    if active is None:\n",
        tests=("tests/test_setup.py::test_s7_systemd_only_an_inactive_unit_is_not_loaded",),
    ),
    Mutant(
        # is-active 의 3 을 stdout 과 상관없이 「없음」으로 읽는다 — activating · reloading 인
        # 유닛이 「올라가지 않음」이 된다(P6 재확인 4 · v1-2)
        name="setup-systemd-activating-read-as-absent",
        path="src/remote_ci_monitor/setup.py",
        old="    return False if state in SYSTEMD_ABSENT_STATES else None\n",
        new="    return False\n",
        tests=("tests/test_setup.py::test_b8_4_systemd_exit_3_is_absent_only_for_an_absent_state",),
    ),
    Mutant(
        # `--bind` 의 이름을 받는다 — 묶어 보는 자리에서 DNS · mDNS 로 이름을 푼다(P5 A-E7)
        name="setup-bind-name-resolved",
        path="src/remote_ci_monitor/setup.py",
        old=(
            "        problem = core.address_problem(bind)  "
            "# 이름은 풀지 않는다 — DNS · mDNS 를 묻지 않는다\n"
        ),
        new=(
            "        problem = None if world.is_local_address(bind) else "
            "core.address_problem(bind)\n"
        ),
        tests=("tests/test_setup.py::test_s10_bind_takes_an_address_not_a_name",),
    ),
    Mutant(
        # 괄호를 친 IPv6 주소(`[::1]`)를 이름이라고 거절한다 — 무엇을 고칠지 틀리게 말한다
        # (P6 재확인 6)
        name="setup-bind-brackets-called-a-name",
        path="src/remote_ci_monitor/core/setup.py",
        old="        return BRACKETED_ADDRESS\n",
        new="        return NAME_NOT_ADDRESS\n",
        tests=("tests/test_setup.py::test_b8_6_a_bracketed_bind_is_told_to_drop_the_brackets",),
    ),
    Mutant(
        # 모두 0 인 IPv6(`0:0:0:0:0:0:0:0`)를 그대로 적는다 — `::` 과 다른 글자라 「모든 주소」
        # 규칙이 보지 못한다(P6 재확인 6)
        name="setup-bind-ipv6-not-normalized",
        path="src/remote_ci_monitor/setup.py",
        old="        bind = core.normal_address(bind)\n",
        new="        pass\n",
        tests=(
            "tests/test_setup.py::test_b8_6_an_all_zero_ipv6_bind_is_written_as_the_all_address",
        ),
    ),
    Mutant(
        # 역할 스캔이 읽지 못한 설정으로 setup 을 멈춘다 — TOML · UTF-8 이 아닌 server.toml 하나가
        # 워커 setup 도, `--force config` 로 고치는 길도 막는다(P5 A-E5)
        name="setup-unreadable-config-blocks",
        path="src/remote_ci_monitor/setup.py",
        old="    roles_here, unread = scan_roles(os.environ)\n",
        new="    roles_here, unread = existing_roles(os.environ), None\n",
        tests=("tests/test_setup.py::test_s9_an_unreadable_existing_config_is_a_warning",),
    ),
    Mutant(
        # 읽지 못한 서버 설정의 길에서 클라이언트 역할을 찾지 않는다 — client.toml · RCM_SERVER 가
        # 있어도 역할 목록에서 빠진다(P6 재확인 5 · v1-3)
        name="setup-unreadable-config-drops-the-client",
        path="src/remote_ci_monitor/setup.py",
        old=(
            '        if client is not None or environ.get("RCM_SERVER") or '
            'environ.get("RCM_TOKEN"):\n'
        ),
        new="        if False:\n",
        tests=(
            "tests/test_setup.py::"
            "test_b8_5_an_unreadable_server_config_still_finds_the_client_role",
        ),
    ),
    Mutant(
        # setup 의 systemd 워커 유닛이 `init worker` 의 것으로 돌아간다 — 라벨 로그 · HOME 이 없고
        # 머리 주석이 `rcm init worker` 를 말한다(P5 B-E13 · A-E9)
        name="setup-worker-unit-without-log-and-home",
        path="src/remote_ci_monitor/setup.py",
        old="    return core.worker_unit(\n",
        new="    return cli.worker_unit(rcm, plan.config, home)\n    return core.worker_unit(\n",
        tests=(
            "tests/test_setup.py::test_s6_the_systemd_worker_unit_logs_to_its_label_and_sets_home",
        ),
    ),
    Mutant(
        # 서비스 관리자가 없는 OS 에서 토큰 명령을 찍지 않는다 — 코디네이터를 손으로 띄운 사람이
        # 같은 빌드로 토큰을 만드는 길을 모른다(P5 A-E3)
        name="setup-tokens-dropped-without-a-manager",
        path="src/remote_ci_monitor/setup.py",
        old='            plan.tokens = core.token_commands(rcm or Path("rcm"), plan.config)\n',
        new="            pass\n",
        tests=("tests/test_setup.py::test_s8_tokens_are_printed_without_a_service_manager",),
    ),
    Mutant(
        # 바꿔 끼우는 순서를 찍지 않는다 — `--force service` 로 다시 쓴 파일이나 이미 올라간 라벨을
        # `cp -n` 과 bootstrap 만으로는 바꿀 수 없다(P5 B-E2)
        name="setup-swap-order-not-printed",
        path="src/remote_ci_monitor/setup.py",
        old="    plan.replacing = bool(loaded) or (bool(forced) and staged.exists())\n",
        new="    plan.replacing = False\n",
        tests=(
            "tests/test_setup.py::test_s1_a_loaded_label_prints_the_launchd_swap_order",
            "tests/test_setup.py::test_s1_force_service_prints_the_systemd_swap_order",
        ),
    ),
    Mutant(
        # linger 줄을 시작 명령 뒤로 돌린다 — 헤드리스 Linux 에서 user manager 없이 시작 명령이 먼저
        # 실패한다(P5 B-E5)
        name="setup-linger-after-the-start",
        path="src/remote_ci_monitor/setup.py",
        old='            err.write(f"  {LINGER_LINE}\\n")\n',
        new="            pass\n",
        tests=("tests/test_setup.py::test_s3_on_linux_linger_comes_before_the_start",),
    ),
    Mutant(
        # 워커도 서비스 시작 명령을 토큰보다 먼저 찍는다 — 토큰 없이 시작한 워커는 거절당하며
        # 재시작을 되풀이한다(P5 B-E9)
        name="setup-worker-starts-before-its-token",
        path="src/remote_ci_monitor/setup.py",
        old="    token_first = plan.role == dc.ROLE_WORKER\n",
        new="    token_first = False\n",
        tests=(
            "tests/test_setup.py::test_s5_a_worker_puts_the_token_in_before_starting_the_service",
        ),
    ),
    Mutant(
        # 제거하는 한 줄을 찍지 않는다 — 되돌리는 길(bootout · disable, 설치한 파일)을 모른다
        # (P5 B-E3)
        name="setup-remove-line-dropped",
        path="src/remote_ci_monitor/setup.py",
        old='    err.write(f"{removal}\\n")\n',
        new="    pass\n",
        tests=(
            "tests/test_setup.py::test_s2_the_output_says_how_to_remove_it",
            "tests/test_setup.py::test_s2_a_client_is_told_which_file_to_remove",
        ),
    ),
    Mutant(
        # 제거 줄이 설치 자리의 파일을 cmp 없이 지운다 — setup 이 쓰지 않은 같은 이름의 서비스를
        # 내리고 지우라고 한다(P6 재확인 3 · v2-3)
        name="setup-remove-line-without-cmp",
        path="src/remote_ci_monitor/core/setup.py",
        old=(
            '            service = f"{same} && {{ launchctl bootout gui/$(id -u)/{q(label)}; '
            '{gone}; }}; "\n'
        ),
        new='            service = f"{{ launchctl bootout gui/$(id -u)/{q(label)}; {gone}; }}; "\n',
        tests=(
            "tests/test_setup.py::"
            "test_b8_3_the_remove_line_takes_the_installed_file_only_when_setup_wrote_it",
        ),
    ),
    Mutant(
        # 이미 올라간 라벨이어도 제거 줄이 아무 말을 하지 않는다 — setup 이 「건드리지 않는다」고
        # 한 서비스를 setup 의 것처럼 지우라고 읽힌다(P6 재확인 3)
        name="setup-remove-line-silent-on-a-preloaded-label",
        path="src/remote_ci_monitor/core/setup.py",
        old="    if service and preloaded:\n",
        new="    if False:\n",
        tests=("tests/test_setup.py::test_b8_3_a_label_already_loaded_may_not_be_setups",),
    ),
    Mutant(
        # 제거 줄이 내리기의 성공에 지우기를 건다 — 이미 내려간 서비스면 설치 자리의 파일이
        # 남는다(P6 재확인 v1-5 · v2-4)
        name="setup-remove-line-unload-gates-the-rm",
        path="src/remote_ci_monitor/core/setup.py",
        old=(
            '            service = f"{same} && {{ launchctl bootout gui/$(id -u)/{q(label)}; '
            '{gone}; }}; "\n'
        ),
        new=(
            '            service = f"{same} && launchctl bootout gui/$(id -u)/{q(label)} && '
            '{gone}; "\n'
        ),
        tests=(
            "tests/test_setup.py::"
            "test_b9_the_remove_line_removes_setups_file_even_when_the_unload_fails",
        ),
    ),
    Mutant(
        # is-active 의 `failed` 를 「없음」으로 읽는다 — 실패한 채 올라가 있는 같은 라벨을 setup 이
        # 모른다(P6 재확인 v1-7)
        name="setup-systemd-failed-read-as-absent",
        path="src/remote_ci_monitor/setup.py",
        old="    if state == SYSTEMD_FAILED:\n",
        new="    if False:\n",
        tests=("tests/test_setup.py::test_b9_systemd_failed_means_the_unit_is_there",),
    ),
    Mutant(
        # 운영 data_dir 「안」을 보지 않는다 — `--data-dir <운영>/x` 가 지나간다(결정 352)
        name="setup-production-inside-missed",
        path="src/remote_ci_monitor/core/setup.py",
        old='    return text == top or text.startswith(top.rstrip("/") + "/")\n',
        new="    return text == top\n",
        tests=(
            "tests/test_setup.py::test_b9_352_setup_refuses_a_production_target_whatever_shell_built_it",
        ),
    ),
    Mutant(
        # 읽지 못한 rcm 서비스를 건너뛴다 — 무엇을 쓰는지 모르는데 쓴다(결정 352, 닫는 쪽)
        name="setup-production-unreadable-ignored",
        path="src/remote_ci_monitor/core/setup.py",
        old="        if svc.problem is not None:\n",
        new="        if False:\n",
        tests=("tests/test_setup.py::test_b9_352_an_rcm_service_setup_cannot_read_closes",),
    ),
    Mutant(
        # 운영 라벨을 보지 않는다 — 운영 서비스와 같은 라벨의 서비스 파일을 쓴다(결정 352)
        name="setup-production-label-ignored",
        path="src/remote_ci_monitor/core/setup.py",
        old="        if mine.intersection(svc.names) and not own(svc):\n",
        new="        if False:\n",
        tests=(
            "tests/test_setup.py::test_b9_352_the_production_label_needs_force_service_and_the_same_file",
        ),
    ),
    Mutant(
        # `--force service` 만으로 운영 라벨을 받는다 — 설치 파일이 setup 이 쓴 것인지(cmp) 보지
        # 않는다(결정 352)
        name="setup-production-own-without-cmp",
        path="src/remote_ci_monitor/core/setup.py",
        old=(
            "        return named and force_service and placed and svc.data is not None "
            "and svc.data in written\n"
        ),
        new="        return named and force_service and placed\n",
        tests=(
            "tests/test_setup.py::test_b9_352_the_production_label_needs_force_service_and_the_same_file",
            "tests/test_setup.py::test_b9_352_setups_own_installed_service_still_guards_its_config",
        ),
    ),
    Mutant(
        # setup 자신의 서비스(라벨 · `--force service` · 같은 바이트)면 설정 · data_dir · venv ·
        # 체크아웃도 견주지 않는다 — 같은 인자에 `--force all` 이면 그 서비스의 설정을 덮는다
        # (batch 10 H1)
        name="setup-production-own-skips-every-piece",
        path="src/remote_ci_monitor/core/setup.py",
        old="        for svc in services:\n            for attr, what in PRODUCTION_PIECES:\n",
        new=(
            "        for svc in services:\n            if own(svc):\n                continue\n"
            "            for attr, what in PRODUCTION_PIECES:\n"
        ),
        tests=(
            "tests/test_setup.py::test_b9_352_setups_own_installed_service_still_guards_its_config",
            "tests/test_setup.py::test_b10_h1_setups_own_service_is_free_of_the_label_check_only",
        ),
    ),
    Mutant(
        # 쓰기 직전의 두 번째 확인이 없다 — 스캔 뒤에 생긴 유닛을 못 본다(결정 352)
        name="setup-production-second-check-dropped",
        path="src/remote_ci_monitor/setup.py",
        old="        _check_production(args, plan, world)  # 쓰기 직전에 한 번 더(결정 352)\n",
        new="        pass  # 쓰기 직전에 한 번 더(결정 352)\n",
        tests=(
            "tests/test_setup.py::test_b9_352_a_unit_that_appears_after_the_scan_is_seen_before_writing",
        ),
    ),
    Mutant(
        # 대상의 심볼릭 링크를 풀지 않는다 — 운영 data_dir 을 가리키는 링크가 지나간다(결정 352)
        name="setup-production-symlink-not-resolved",
        path="src/remote_ci_monitor/setup.py",
        old="        {flag: real_path(path) for flag, path in targets.items()},\n",
        new="        dict(targets),\n",
        tests=("tests/test_setup.py::test_b9_352_a_symlink_to_the_production_data_dir_is_refused",),
    ),
    Mutant(
        # darwin 에서도 대소문자를 가린다 — 대소문자만 다른 운영 경로가 지나간다(결정 352)
        name="setup-production-case-not-folded",
        path="src/remote_ci_monitor/setup.py",
        old="        fold_case=core.folds_case(world.platform),\n",
        new="        fold_case=False,\n",
        tests=("tests/test_setup.py::test_b9_352_darwin_compares_paths_without_case",),
    ),
    Mutant(
        # `$XDG_CONFIG_HOME/systemd/user` 를 찾지 않는다(결정 352)
        name="setup-scan-xdg-units-missed",
        path="src/remote_ci_monitor/setup.py",
        old='        found.append((Path(xdg) / "systemd" / "user", False, first))\n',
        new="        pass\n",
        tests=("tests/test_setup.py::test_b9_352_systemd_units_in_every_supported_place_are_seen",),
    ),
    Mutant(
        # 시스템 자리(`/Library/LaunchDaemons` · `/etc/systemd/system` …)를 찾지 않는다(결정 352)
        name="setup-scan-system-places-missed",
        path="src/remote_ci_monitor/setup.py",
        old=(
            "    found += [(place, place.name in _ROOT_PLACES, first) for place in "
            "SYSTEM_SERVICE_PLACES]\n"
        ),
        new="    found += []\n",
        tests=("tests/test_setup.py::test_b9_352_systemd_units_in_every_supported_place_are_seen",),
    ),
    Mutant(
        # 스캔이 `$HOME` 만 본다 — `HOME=/tmp/x rcm setup --config <진짜 홈>/…` 가 진짜 홈의
        # 운영 설정을 덮는다(batch 10 H3)
        name="setup-scan-account-home-missed",
        path="src/remote_ci_monitor/setup.py",
        old="        homes.append(account)\n",
        new="        pass\n",
        tests=("tests/test_setup.py::test_b10_h3_the_scan_reads_the_account_home_not_only_home",),
    ),
    Mutant(
        # `--service-file` 이 가리키는 아무 파일의 바이트가 `written` 에 든다 — 운영 plist 의
        # 사본을 주면 운영 라벨이 setup 의 것이 된다(batch 10 H2)
        name="setup-production-written-reads-any-service-file",
        path="src/remote_ci_monitor/setup.py",
        old="        real_path(plan.service), real_path(plan.config.parent), fold_case=fold\n",
        new="        real_path(plan.service), real_path(plan.service), fold_case=fold\n",
        tests=(
            "tests/test_setup.py::"
            "test_b10_h2_a_copy_outside_the_config_folder_does_not_make_the_label_setups",
        ),
    ),
    Mutant(
        # own 이 서비스 파일이 그 라벨의 설치 자리인지 보지 않는다(batch 10 H2)
        name="setup-production-own-anywhere",
        path="src/remote_ci_monitor/core/setup.py",
        old=(
            "        placed = installed is not None and same_or_inside(svc.file, installed, "
            "fold_case=fold_case)\n"
        ),
        new="        placed = True\n",
        tests=(
            "tests/test_setup.py::"
            "test_b10_h2_own_needs_the_install_place_and_the_service_file_is_refused_first",
        ),
    ),
    Mutant(
        # 설치 자리를 글자로만 견준다 — LaunchAgents 를 가리키는 폴더 링크 안에 쓴다(batch 10 H2)
        name="setup-install-place-links-not-resolved",
        path="src/remote_ci_monitor/core/setup.py",
        old="        if resolve is not None and same_or_inside(\n",
        new="        if False and same_or_inside(\n",
        tests=("tests/test_setup.py::test_b10_h2_an_install_place_is_found_through_links_and_xdg",),
    ),
    Mutant(
        # `$XDG_CONFIG_HOME/systemd/user` 가 설치 자리가 아니다 — setup 이 그 안에 쓴다(batch 10 H2)
        name="setup-install-place-xdg-missed",
        path="src/remote_ci_monitor/core/setup.py",
        old='        user += (xdg / "systemd" / "user",)\n',
        new="        pass\n",
        tests=("tests/test_setup.py::test_b10_h2_an_install_place_is_found_through_links_and_xdg",),
    ),
    Mutant(
        # 읽지 못한 rcm 같은 파일을 rcm 이 아닌 것으로 본다(결정 352, 닫는 쪽)
        name="setup-scan-unreadable-rcm-skipped",
        path="src/remote_ci_monitor/setup.py",
        old=(
            "            return core.RcmService(stem, file, (stem,), "
            'problem=f"setup cannot read it ({reason})")\n'
        ),
        new="            return None\n",
        tests=("tests/test_setup.py::test_b9_352_an_rcm_service_setup_cannot_read_closes",),
    ),
    Mutant(
        # `--config` 없는 rcm 서비스를 건너뛴다 — 어느 설정 · data_dir 인지 모르는데 쓴다(결정 352)
        name="setup-scan-no-config-allowed",
        path="src/remote_ci_monitor/setup.py",
        old=(
            '            return problem("it names no --config, so setup cannot tell which config '
            'it reads")\n'
        ),
        new="            return None\n",
        tests=("tests/test_setup.py::test_b9_352_an_rcm_service_setup_cannot_read_closes",),
    ),
    Mutant(
        # 서비스 venv 의 editable 소스(체크아웃)를 모른다(결정 352)
        name="setup-scan-checkout-missed",
        path="src/remote_ci_monitor/setup.py",
        old="    checkout = _editable_checkout(venv) if venv is not None else None\n",
        new="    checkout = None\n",
        tests=(
            "tests/test_setup.py::test_b9_352_setup_refuses_a_production_target_whatever_shell_built_it",
        ),
    ),
    Mutant(
        # 서비스가 실행하는 venv 를 모른다(결정 352)
        name="setup-scan-venv-missed",
        path="src/remote_ci_monitor/setup.py",
        old="    venv = _venv_of(sf.program, sf.env)\n",
        new="    venv = None\n",
        tests=(
            "tests/test_setup.py::test_b9_352_setup_refuses_a_production_target_whatever_shell_built_it",
        ),
    ),
    Mutant(
        # 마지막 doctor 줄이 맨 이름 rcm 이다 — PATH 의 다른 빌드가 그 머신을 잰다(P5 B-E7)
        name="setup-doctor-line-bare-rcm",
        path="src/remote_ci_monitor/core/setup.py",
        old='    words = [q(str(rcm)) if rcm is not None else "rcm", "doctor", "--role", role]\n',
        new='    words = ["rcm", "doctor", "--role", role]\n',
        tests=(
            "tests/test_setup.py::test_s4_the_last_doctor_line_names_the_rcm_and_the_paths_given",
        ),
    ),
    Mutant(
        # 사용자가 준 `--config` 를 마지막 doctor 줄에 붙이지 않는다 — doctor 가 기본 자리의 다른
        # 설정을 잰다(P5 B-E7)
        name="setup-doctor-line-drops-the-config",
        path="src/remote_ci_monitor/setup.py",
        old="    plan.doctor_config = plan.config if args.config is not None else None\n",
        new="    plan.doctor_config = None\n",
        tests=(
            "tests/test_setup.py::test_s4_the_last_doctor_line_names_the_rcm_and_the_paths_given",
        ),
    ),
    # ── M5t-a PR3 — 분배 전용 코디네이터 `lanes = 0` (docs/m5t-workplan.md §7 변이 검사) ──
    Mutant(
        # `lanes = 0` 에서 `@local` 을 살아 있다고 본다 — 레인이 없는 서버의 머신 목록이 로컬
        # 머신을 받을 수 있는 자리로 보이고 머신 탭 · 점검 스위퍼가 그것을 믿는다(TL7)
        name="m5t-lanes-zero-local-alive",
        path="src/remote_ci_monitor/capabilities.py",
        old="                alive=self._has_local_lanes(),\n",
        new="                alive=True,\n",
        tests=("tests/test_lanes_zero.py::test_tl7_at_local_is_not_alive",),
    ),
    Mutant(
        # 기본 풀을 늘 뺀다 — 분배 전용 서버에서 기본 풀 워커가 다 죽어도 `pools_without_workers`
        # 가 비어 `rcm check` 의 pools 경보가 꺼진다(TL13, batch 2 항목 3)
        name="m5t-lanes-zero-default-pool-skipped",
        path="src/remote_ci_monitor/remote_workers.py",
        old="            if row.pool == DEFAULT_POOL and local_lanes:\n",
        new="            if row.pool == DEFAULT_POOL:\n",
        tests=(
            "tests/test_lanes_zero.py::"
            "test_tl13_pools_without_workers_counts_the_default_pool_only_without_local_lanes",
        ),
    ),
    Mutant(
        # 후보 함수가 `lanes = 0` 에서도 `@local` 을 남긴다 — 대기 잡이 로컬 레인을 기다리고
        # 재시작 뒤에도 그렇다(TL4 · TL9, P5 G7)
        name="m5t-lanes-zero-candidates-keep-local",
        path="src/remote_ci_monitor/capabilities.py",
        old='            views = [v for v in views if v.kind != "local"]  # 분배 전용',
        new="            views = list(views)  # 분배 전용",
        tests=(
            "tests/test_lanes_zero.py::test_tl4_a_waiting_job_goes_to_the_worker_that_attaches",
            "tests/test_lanes_zero.py::test_tl9_the_queue_survives_a_restart",
        ),
    ),
    Mutant(
        # `--machine @local` 을 분배 전용 서버에서도 받는다 — 409 대신 「machine @local is down
        # — start rcm worker there」라는 엉뚱한 조언이 간다(TL2, P5 G7)
        name="m5t-lanes-zero-machine-local-accepted",
        path="src/remote_ci_monitor/server.py",
        old="            if view.id == LOCAL_WORKER and not self._has_local_lanes():\n",
        new="            if view.id == LOCAL_WORKER and False:\n",
        tests=(
            "tests/test_lanes_zero.py::"
            "test_tl2_without_workers_a_job_waits_without_an_eta_and_at_local_is_409",
        ),
    ),
    Mutant(
        # 로컬 레인이 없어도 `@local` 의 프로브를 잰다 — 잰 보고가 있으면 `@local` 이 「받을 수
        # 있다」로 보인다(TL8, P5 G7)
        name="m5t-lanes-zero-local-probes-run",
        path="src/remote_ci_monitor/capabilities.py",
        old=(
            "        if not self._has_local_lanes():\n"
            "            return\n"
            "        with self._measure_lock:\n"
        ),
        new="        with self._measure_lock:\n",
        tests=("tests/test_lanes_zero.py::test_tl8_the_local_probes_do_not_run",),
    ),
    Mutant(
        # 서버가 capacity 를 못 센(null) 답을 옛 서버처럼 읽어 행을 빼다 — 분배 전용 서버에서
        # 갈 곳을 셀 수 없는데 `rcm check` 가 0 으로 끝난다(batch 2 항목 1, PLAN.md 「rcm check」)
        name="m5t-lanes-zero-capacity-null-hidden",
        path="src/remote_ci_monitor/cli.py",
        old=(
            '        return ("capacity", False if health.get("lanes") == 0 else None, '
            "CAPACITY_UNKNOWN_TEXT)\n"
        ),
        new="        return None\n",
        tests=(
            "tests/test_lanes_zero.py::"
            "test_tl3_rcm_check_draws_capacity_unknown_when_the_server_could_not_count",
        ),
    ),
    Mutant(
        # 분배 전용 서버의 `@local` 다시 점검이 MachineDown 으로 떨어진다 — 「start rcm worker
        # there」라는 엉뚱한 조언(batch 2 항목 6 · P5 G6)
        name="m5t-lanes-zero-recheck-local-down",
        path="src/remote_ci_monitor/capabilities.py",
        old=(
            "        if view.id == LOCAL and not self._has_local_lanes():\n"
            "            raise NoLocalLanes(view.id)\n"
        ),
        new="",
        tests=(
            "tests/test_lanes_zero.py::"
            "test_tl7_recheck_on_at_local_says_this_server_has_no_local_lanes",
        ),
    ),
    Mutant(
        # `rcm check` 의 machines 행이 레인 없는 `@local` 을 센다 — 워커가 없는데 「1 machine」
        # (batch 2 항목 6)
        name="m5t-lanes-zero-machines-row-counts-local",
        path="src/remote_ci_monitor/cli.py",
        old=(
            "    machines = [m for m in machines "
            'if not (m.get("kind") == "local" and m.get("lanes") == 0)]\n'
        ),
        new="",
        tests=(
            "tests/test_lanes_zero.py::test_tl7_rcm_check_does_not_count_at_local_as_a_machine",
        ),
    ),
    Mutant(
        # 갈 곳 잃은 잡이 이유 메모 없이 돌아간다 — 화면이 「no capable machine · unknown」(P5 G5)
        name="m5t-lanes-zero-no-local-lanes-note-dropped",
        path="src/remote_ci_monitor/capabilities.py",
        old="                if local is not None and not self._has_local_lanes():\n",
        new="                if False:\n",
        tests=("tests/test_lanes_zero.py::test_tl15_a_job_with_nowhere_to_go_says_no_local_lanes",),
    ),
    Mutant(
        # 꼴이 깨진 capacity(문자열 · 빠진 키 · total 0 · reachable > total)를 옛 서버처럼 읽어
        # 행을 빼다 — 분배 전용 서버의 `rcm check` 가 0 으로 끝난다(batch 3 항목 1, fail-open)
        name="m5t-lanes-zero-capacity-malformed-hidden",
        path="src/remote_ci_monitor/cli.py",
        old='        return ("capacity", unknown_grade, CAPACITY_UNKNOWN_TEXT)\n',
        new="        return None\n",
        tests=(
            "tests/test_lanes_zero.py::"
            "test_tl3_rcm_check_fails_on_a_malformed_capacity_from_a_dispatch_only_server",
            "tests/test_lanes_zero.py::test_tl3_a_malformed_capacity_draws_the_same_unknown_row_as_null",
        ),
    ),
    Mutant(
        # `/api/health` 가 불릴 때마다 같은 capacity 예외를 다시 남긴다 — `last_error` 의 다른
        # 오류를 덮고 로그가 찬다(batch 3 항목 2)
        name="m5t-lanes-zero-capacity-error-every-call",
        path="src/remote_ci_monitor/server.py",
        old="            if fresh:\n                self.record_error(err[0], detail=err[1])\n",
        new="            if True:\n                self.record_error(err[0], detail=err[1])\n",
        tests=(
            "tests/test_lanes_zero.py::"
            "test_tl3_a_capacity_error_is_logged_once_not_on_every_health_call",
        ),
    ),
)


def _pytest(
    cmd: list[str], cwd: Path, env: dict[str, str] | None = None
) -> tuple[int, float, str] | None:
    """(exit code, seconds, output tail) — timeout 이면 None.

    자기 프로세스 그룹에서 돌리고 timeout 이면 그룹째 죽인다 — 시험이 띄운 서버 · 워커가 남아
    다른 변이의 CPU 를 먹지 않게. 그룹이 잡는 것은 pytest 와 그 그룹에 남은 자식뿐이다. e2e
    하네스가 `start_new_session=True` 로 띄운 서버 · 워커는 제 세션에 있어 그룹 밖이다. 그래서
    하네스(`tests/test_e2e_worker.py` 의 `SessionProc`)가 `env` 의 `SESSION_PIDS_ENV` 폴더에 남긴
    pid 의 그룹도 죽인다. 하네스가 거둔 자식의 파일은 지워져 있으니 남은 것만 죽는다."""
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=PYTEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        # 파이프를 비우기 전에 죽인다. 파이프를 물려받은 자식이 살아 있으면 communicate 가 안 끝난다
        _kill_sessions(env)
        proc.communicate()
        return None
    took = time.monotonic() - started
    lines = (out + err).strip().splitlines()
    return proc.returncode, took, "\n".join(lines[-6:])


def _kill_sessions(env: dict[str, str] | None) -> None:
    """하네스가 새 세션으로 띄우고 아직 거두지 않은 자식들의 그룹을 죽인다(`_pytest` 의 설명)."""
    folder = (env or {}).get(SESSION_PIDS_ENV)
    if not folder or not Path(folder).is_dir():
        return
    for mark in Path(folder).iterdir():
        if not mark.name.isdigit():
            continue
        try:
            os.killpg(int(mark.name), signal.SIGKILL)
        except OSError:
            pass
        mark.unlink(missing_ok=True)


def _descriptions() -> dict[str, str]:
    """변이마다 `Mutant(` 바로 아래 주석 — 이 파일이 정본이라 설명을 따로 적지 않고 읽는다."""
    text = Path(__file__).read_text(encoding="utf-8")
    out: dict[str, str] = {}
    pattern = r"Mutant\(\n((?:[ \t]+#[^\n]*\n)*)[ \t]+name=\"([^\"]+)\""
    for comment, name in re.findall(pattern, text):
        lines = [line.strip().lstrip("#").strip() for line in comment.splitlines()]
        out[name] = " ".join(line for line in lines if line)
    for m in MUTANTS:  # 주석이 없는 옛 변이는 이 docstring 의 「이름 — 설명」 줄을 쓴다
        if not out.get(m.name):
            found = re.search(re.escape(m.name) + r"\s+—\s+([^\n]+)", __doc__ or "")
            out[m.name] = found.group(1).strip() if found else ""
    return out


def list_mutants() -> int:
    """`--list` — 이름 · 파일 · 설명, 끝에 수. 문서는 수를 적지 않고 이것을 가리킨다."""
    about = _descriptions()
    for m in MUTANTS:
        print(f"{m.name}\t{m.path}\t{about.get(m.name, '')}")
    print(f"{len(MUTANTS)} mutants")
    return 0


def run_mutant(m: Mutant, keep: bool) -> tuple[bool | None, str]:
    """복사본에 변이를 넣고 pytest 를 돌린다. (감지됨? — None 은 SKIP, 설명).

    아무것도 직접 찍지 않는다. 병렬로 돌 때 줄이 섞이지 않게 `--keep` 의 사본 경로도 설명에
    싣는다."""
    if m.needs_chrome and not chrome_available():
        return None, "skipped: no Chrome binary found (set RCM_CHROME) — its tests would skip"
    tmp = Path(tempfile.mkdtemp(prefix=f"mutcheck-{m.name}-"))
    # 이 변이의 pytest 만 쓰는 임시 폴더 — 짧게 둔다(Chrome 등의 유닉스 소켓 경로 길이 제한)
    scratch = Path(tempfile.mkdtemp(prefix="mc-"))
    try:
        # 바이트코드를 쓰지 않는다. `.pyc` 는 원본의 (mtime 초, 크기) 로 낡았는지 본다. 사본은
        # mtime 을 그대로 가져오므로 원본이 변이를 쓴 그 초에 만들어졌고 변이가 같은 길이면 대조군이
        # 남긴 `.pyc` 를 그대로 읽어 잡혀야 할 변이가 「초록」으로 보인다(가짜 프로젝트 시험)
        env = {**os.environ, "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1"}
        env[SESSION_PIDS_ENV] = str(scratch / "sessions")  # timeout 때 같이 죽일 자식들
        ok, info = _run_in_copy(m, tmp, env)
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)
    if keep:
        info += f"\n  kept copy: {tmp}"
    return ok, info


def syntax_problem(path: str, text: str) -> str | None:
    """변이를 넣은 `.py` 가 파이썬으로 읽히지 않으면 그 까닭. 문법 오류인 변이는 시험을 빨갛게
    만들어도 감지가 아니다 — 들여쓰기를 잃은 `new` 가 「잡혔다」로 세어졌다(P3 3라운드 v2-1)."""
    if not path.endswith(".py"):
        return None
    try:
        ast.parse(text)
    except SyntaxError as e:
        return f"the mutant does not parse as Python ({e.msg}, line {e.lineno}) — fix its `new`"
    return None


def _run_in_copy(m: Mutant, tmp: Path, env: dict[str, str]) -> tuple[bool, str]:
    # `.rcm` — 시험이 레포의 `.rcm/ci.toml` 을 읽는다(R15)
    for name in ("src", "tests", "tools", "scripts", ".rcm", "pyproject.toml"):
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, tmp / name, ignore=shutil.ignore_patterns("__pycache__"))
        elif src.is_file():
            shutil.copy2(src, tmp / name)
    target = tmp / m.path
    text = target.read_text(encoding="utf-8")
    if text.count(m.old) != 1:
        return False, f"pattern not found exactly once in {m.path}: {m.old!r}"
    changed = text.replace(m.old, m.new)
    problem = syntax_problem(m.path, changed)
    if problem is not None:
        return False, problem
    if m.runner == "node":
        node = shutil.which("node")
        if node is None:
            return False, "node is not installed (needed for the web mutant)"
        cmd = [node, "--test", *m.tests]
    else:
        cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *m.tests]
    # 대조군: 변이 없이 복사본에서 초록이어야 한다(환경 문제로 빨간 것을 감지로 착각하지 않게)
    control = _pytest(cmd, tmp, env)
    if control is None:
        return False, f"control run timed out: {' '.join(cmd)}"
    if control[0] != 0:
        return False, f"control run is RED without the mutant (exit {control[0]})\n{control[2]}"
    _write_mutant(target, changed)
    mutated = _pytest(cmd, tmp, env)
    if mutated is None:
        return False, f"mutant run timed out: {' '.join(cmd)}"
    code, took, tail = mutated
    if code == 0:
        return False, f"tests stayed GREEN with mutant applied ({took:.1f}s)\n{tail}"
    return True, f"control green, mutant red (exit {code}, {took:.1f}s)"


def _write_mutant(target: Path, text: str) -> None:
    """변이를 사본에 쓴다. 시험이 이것을 감싸 mtime 을 원본 값으로 되돌린다 — 옛 `.pyc` 가 읽히는
    조건(같은 초 · 같은 길이)을 시계에 기대지 않고 만든다."""
    target.write_text(text, encoding="utf-8")


def resolve_jobs(jobs: int) -> int:
    """`-j` 값 → 동시에 돌릴 변이 수. 0 은 CPU 수의 절반(최소 1) — 나머지 절반은 시험이 띄우는
    서버 · 워커 · Chrome 몫이다."""
    if jobs > 0:
        return jobs
    return max(1, (os.cpu_count() or 2) // 2)


#: Chrome 변이는 한 번에 하나 — 부하 속 여러 Chrome 은 CDP 마감을 넘긴다(모듈 docstring).
_CHROME_LOCK = threading.Lock()


def _run_one(m: Mutant, keep: bool) -> tuple[bool | None, str]:
    if m.needs_chrome:
        with _CHROME_LOCK:
            return run_mutant(m, keep)
    return run_mutant(m, keep)


def run_all(
    mutants: list[Mutant],
    jobs: int,
    keep: bool,
    report: Callable[[Mutant, bool | None, str], None],
) -> list[bool | None]:
    """변이를 `jobs` 개씩 동시에 돌리고, 결과는 끝난 순서가 아니라 `mutants` 순서로 `report` 에
    넘긴다. 앞의 것이 끝나는 대로 이어지는 만큼 바로 넘기므로 직렬(`jobs=1`)은 예전과 같다."""
    results: list[tuple[bool | None, str] | None] = [None] * len(mutants)
    done = threading.Condition()

    def work(index: int) -> None:
        try:
            outcome = _run_one(mutants[index], keep)
        except Exception as error:  # 한 변이가 터져도 나머지는 끝난다 — 실패로 센다
            outcome = (False, f"mutcheck crashed on this mutant: {error!r}")
        with done:
            results[index] = outcome
            done.notify_all()

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for index in range(len(mutants)):
            pool.submit(work, index)
        for index, m in enumerate(mutants):
            with done:
                done.wait_for(lambda index=index: results[index] is not None)
            ok, info = results[index]  # type: ignore[misc]
            report(m, ok, info)
    return [r[0] for r in results]  # type: ignore[index]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check that tests catch known mutations.")
    ap.add_argument("--keep", action="store_true", help="keep the mutated copies")
    ap.add_argument("--only", help="run one mutant by name")
    ap.add_argument("--list", action="store_true", help="list the mutants and their count")
    ap.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="run N mutants at once (default 1; 0 = half the CPUs). Chrome mutants still run "
        "one at a time",
    )
    args = ap.parse_args(argv)
    if args.list:
        return list_mutants()
    if args.jobs < 0:
        print(f"--jobs must be 0 or more (got {args.jobs})", file=sys.stderr)
        return 2
    mutants = [m for m in MUTANTS if not args.only or m.name == args.only]
    if not mutants:
        print(f"no mutant named {args.only!r}", file=sys.stderr)
        return 2
    jobs = min(resolve_jobs(args.jobs), len(mutants))
    if jobs > 1:
        print(f"mutcheck: {len(mutants)} mutants, {jobs} at a time", flush=True)

    def report(m: Mutant, ok: bool | None, info: str) -> None:
        mark = "SKIP" if ok is None else "OK " if ok else "FAIL"
        print(f"[{mark}] {m.name}: {info}", flush=True)

    outcomes = run_all(mutants, jobs, args.keep, report)
    failures = sum(1 for ok in outcomes if ok is False)
    skipped = sum(1 for ok in outcomes if ok is None)
    if failures:
        print(f"mutcheck: {failures} of {len(mutants)} mutants NOT caught", file=sys.stderr)
        return 1
    note = f" ({skipped} skipped: no Chrome)" if skipped else ""
    print(f"mutcheck: all {len(mutants) - skipped} mutants caught{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
