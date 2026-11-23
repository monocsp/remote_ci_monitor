# M5s 실기 확인 (S1~S6, 표식, 웹 첫 화면)

- 날짜: 2026-11-23
- 대상: `dev` 의 `5a32f62`(feat(m5s): 프리셋이 선언하면 종료코드 75 를 반복한다 (#256)). 예약 실행(#253),
  사람이 할 일 표식(#251), 종료코드 75 반복(#256), 검토 패널의 심사 상태(#254)가 들어 있다. golive(#255)와
  웹 화면(#257)은 아직 `dev` 에 없다.
- 명세: `docs/m5s-workplan.md` §7 의 「실기」 칸. 요청서 13절 4번의 시험 여섯 개가 S1~S6 이다. 여기에 S12 의
  일부(표식과 `--return-on-action`)와 웹 `/` 의 응답 코드를 더했다. 화면 확인과 스크린샷은 #257 이 머지된 뒤에
  따로 한다.
- 다시 돌리는 법: `scripts/acceptance/m5s_live.sh`. 한 번에 5분 10초쯤 걸린다. S1 이 실제로 5분을 기다리기
  때문이다. 포트는 `M5S_PORT`(기본 8796)로, 설정 폴더는 `M5S_CONF_DIR` 로 바꾼다. 판정 줄은
  `out/summary.txt` 에, 모든 잡의 마지막 행은 `out/jobs.json` 에, 훅 기록은 `out/notify.log` 에 남는다.
  테스트 스위트는 이 스크립트를 부르지 않는다.

결과는 여덟 건 모두 통과다. 서버 동작에서 어긋난 것은 없었다. CLI 의 종료 코드 하나가 문서의 뜻과 맞지
않아 발견 F1 로 적었다.

## 차림

이 워크트리의 `.venv/bin/rcm`(0.9.0) 하나로 `rcm serve` 를 띄웠다. 포트는 8796, bind 는 127.0.0.1 이고
lanes 3, `admission = "always"`, `advertise = false`, `[display] timezone = "UTC"` 로 두었다. data_dir 은
`~/.local/share/rcm-devtest-m5s/data` 다. 스크립트가 시작할 때 그 폴더를 지우고 새로 만든다. 설정 파일은 세션
임시 폴더에 두었다.

운영 서버(8787, `~/.config/rcm`, `~/.local/share/rcm`, 서비스 venv)는 건드리지 않았다. 스크립트는 HOME 을
시험 폴더 안의 `home` 으로 바꾸고 `RCM_SERVER` 를 시험 서버로 고정한다. `RCM_SERVER` 없이 rcm 을 부르면
같은 네트워크의 운영 서버를 mDNS 로 찾기 때문이다. 처음 둘러볼 때 `rcm check` 가 실제로 운영 서버를 찾았다.
그때는 health 를 읽기만 했고 아무것도 제출하지 않았다.

토큰은 `rcm token --config <시험 설정> add live-client` 와 `add live-admin --admin` 으로 시험 서버의
data_dir 에만 만들었다. 제출할 때는 `--by acceptance` 를 붙여 요청자 표식에 사용자 이름과 호스트 이름이
들어가지 않게 했다. 저장소는 시험 폴더 안에 만든 커밋 하나짜리 bare 저장소이고, 모든 프리셋은 `git_ref` 로
`main` 을 받는다.

| 프리셋 | 하는 일 |
|---|---|
| `s1-ok` `s2-ok` `s3-ok` | 실행되면 `ran.log` 에 「프리셋 잡번호 시각」을 한 줄 쓰고 0 |
| `s4-down` | 같은 일. `start_deadline_seconds = 15` |
| `s4-hold` | `sleep 60`. `concurrency_group = "s4g"` |
| `s4-lane` | `ran.log` 한 줄. 그룹 `s4g`, `start_deadline_seconds = 15` |
| `s5-later` | `::rcm::summary::not yet` 을 찍고 exit 75. `[presets.retry]` 간격 20초, 상한 3 |
| `x-login` | `::rcm::action::login::sign in to the store again` 을 찍고 8초 뒤 exit 75. `[presets.retry]` 간격 30초, 상한 2 |

알림 규칙은 둘이다. `file-hook` 은 `on` 이 없어 기본 상태 집합을 받고, `retried-hook` 은
`on = ["retried"]` 다. 둘 다 같은 sh 스크립트를 불러 `RCM_JOB_ID`, `RCM_STATE`, `RCM_PRESET`, `RCM_NOTIFY`,
`RCM_EXIT_CODE`, `RCM_ACTION_CODE`, `RCM_ACTION_NOTE`, `RCM_SUMMARY` 를 `notify.log` 에 한 줄로 쓴다.

## 판정

시각은 모두 UTC 다. 상태 전이는 시험 서버 DB 의 `events` 표와 `GET /jobs/<id>` 에서 읽었다. 명령의 공통
부분 `--source git_ref --ref main --by acceptance --no-wait` 는 표에서 `…` 로 줄였다.

| ID | 한 일 | 명령 | 잡 | 상태 전이와 시각 | 기대와 결과 | 판정 |
|---|---|---|---|---|---|---|
| S1 | 5분 뒤로 예약을 걸고 실제로 기다린다 | `rcm run s1-ok … --in 5m`, `rcm wait --job 1` | #1 | scheduled 08:45:08 → queued·running 08:50:09 → succeeded 08:50:09. not_before 08:50:08, 마감 09:05:08 | 정한 시각에 돈다. 시각 뒤 1초에 시작했고 `rcm wait` 는 0, 훅은 `succeeded` 한 줄. 중간에 서버를 한 번 껐다 켰는데도 제시각에 돌았다 | 통과 |
| S2 | 예약을 건 뒤 바로 취소한다 | `rcm run s2-ok … --in 90s`, `rcm cancel 2` | #2 | scheduled 08:45:08 → cancelled 08:45:08. started_at 없음 | 돌지 않고 cancelled. not_before 08:46:38 이 지나도 `ran.log` 에 줄이 없었다. 훅 `cancelled before start` | 통과 |
| S3 | reload 와 재시작 뒤에도 예약이 남는다 | `rcm run s3-ok … --at 2026-11-23T08:49:08Z`, 설정에 프리셋 하나를 더하고 `rcm reload`(admin), 서버 SIGTERM 뒤 다시 `rcm serve` | #3, #1 | #3 scheduled 08:45:08 → queued·running 08:49:09 → succeeded 08:49:09 | reload 는 `preset added s3-added-by-reload` 로 0. reload 뒤와 재시작 뒤 모두 `rcm jobs --state scheduled` 에 #1 과 #3 이 있었다. #3 은 정한 시각에 돌았고 `rcm wait` 는 0 | 통과 |
| S4a | 서버를 끈 채 마감이 지나게 한다 | `rcm run s4-down … --in 20s`, 바로 서버 SIGTERM, 마감 5초 뒤에 다시 띄움 | #4 | scheduled 08:45:09 → expired 08:45:49(queued 없음). not_before 08:45:29, 마감 08:45:44, 서버는 08:45:09~08:45:49 꺼짐 | expired, 훅이 기록. 시작 로그 `recovery · lost 0 · cancelled uploads 0 · expired 1 · promoted 0`, 요약 `did not start by its start deadline 2026-11-23 08:45Z`, 훅 `job=4 state=expired` 정확히 한 줄 | 통과 |
| S4b | 같은 그룹을 다른 잡이 잡고 있어 마감이 지나게 한다(명세 S4 의 둘째 경우) | `rcm run s4-hold …`(지금), `rcm run s4-lane … --in 10s` | #6, #5 | #6 scheduled 08:45:51 → queued 08:46:04 → expired 08:46:19. not_before 08:46:01, 마감 08:46:16. #5 는 그동안 running | expired, 훅이 기록. 대기열에 오른 뒤 마감을 넘겨 닫혔고 started_at 은 없다. 훅 한 줄. #5 는 판정 뒤 `rcm cancel 5` 로 치웠다 | 통과 |
| S5 | 75 로 끝나는 반복 프리셋을 예약한다 | `rcm run s5-later … --in 10s`, `rcm wait --job 7` | #7 → #10 → #11 | #7 queued·running·retried 08:46:04. #10 scheduled 08:46:04(not_before 08:46:24, 마감 없음) → running·retried 08:46:29. #11 scheduled 08:46:29(not_before 08:46:49) → running·exhausted 08:46:54 | 정한 간격으로 돌다 상한에서 exhausted. 앞 시도가 끝나고 다음 시도가 시작하기까지 25초씩 걸렸다. not_before 는 정확히 20초 뒤이고 나머지 5초는 5초 주기 승격 판정이다. `rcm wait` 는 사슬을 따라가 1 로 끝났고 JSON 에 `chain_id: 7` 과 `attempts[]` 세 개가 있다 | 통과 |
| S6 | 같은 예약을 한 번 더 건다 | S1 직후 같은 `rcm run s1-ok … --in 5m`, 그리고 `curl -X POST /jobs`(`start_in_seconds: 300`) | #1 | 새 잡 없음 | 409 `schedule_exists`. HTTP 409 에 `code: schedule_exists`, `job_id: 1` 이 왔고 예약 목록의 `s1-ok` 는 하나였다. CLI 는 거절 문구를 냈지만 종료 3 이다(F1) | 통과 |
| X1 | 표식을 찍고 75 로 끝나는 프리셋에서 `--return-on-action` 을 본다 | `rcm run x-login …`(지금), `rcm wait --job 8 --return-on-action --timeout 60`, `GET /api/status` 두 번 | #8 → #9 | #8 queued·running 08:45:52 → retried 08:46:00. #9 scheduled 08:46:00 → running 08:46:34 → exhausted 08:46:42 | 4 로 돌아오고 status 에 `needs_action`. `rcm wait` 는 08:45:52 에 바로 4. 실행 중 큐 행 #8 과 시도 사이 예약 행 #9 모두 `reason: needs_action`, `action.code: login`. 훅 둘(retried, exhausted)에 `RCM_ACTION_CODE=login` 과 note 가 갔다 | 통과 |
| X2 | 웹 첫 화면이 뜨는지만 본다 | `curl http://127.0.0.1:8796/` | — | — | 200 | 통과 |

S5 의 `rcm wait` stderr 는 이렇게 나왔다. 시각은 이 머신의 시간대로 그려진다(`_scheduled_for` 가 정한
동작이다).

```text
#7 scheduled · scheduled for 17:46 · start by 18:01
#7 running · preparing workspace · elapsed 0s · eta 17:56
#7 retried · not yet
#7 exit 75 · attempt 1 of 3 · retrying as #10 — waiting for it
#10 running · preparing workspace · elapsed 0s · eta 17:56
#10 retried · not yet
#10 exit 75 · attempt 2 of 3 · retrying as #11 — waiting for it
```

X1 의 `rcm wait --return-on-action` stderr 는 이렇다. 받은 JSON 의 `state` 는 `running`, `reason` 은
`needs_action` 이었다.

```text
#8 needs action · login · sign in to the store again
#8 running · elapsed 0s · eta 17:55
job #8 keeps running — after the person acts, resume with `rcm wait --job 8`
```

훅 기록 `notify.log` 는 열한 줄이다. 기본 규칙 `file-hook` 은 `retried` 를 받지 않았고 `expired` 와
`exhausted` 는 받았다. 문서가 말한 기본 상태 집합과 같다. 아래에는 빈 칸을 빼고 옮겼다.

```text
08:45:08 job=2 state=cancelled preset=s2-ok notify=file-hook summary=cancelled before start
08:45:49 job=4 state=expired preset=s4-down notify=file-hook summary=did not start by its start deadline 2026-11-23 08:45Z
08:46:00 job=8 state=retried preset=x-login notify=retried-hook exit=75 action=login note=sign in to the store again
08:46:04 job=7 state=retried preset=s5-later notify=retried-hook exit=75 summary=not yet
08:46:19 job=6 state=expired preset=s4-lane notify=file-hook summary=did not start by its start deadline 2026-11-23 08:46Z
08:46:25 job=5 state=cancelled preset=s4-hold notify=file-hook exit=-15 summary=cancelled by live-client
08:46:29 job=10 state=retried preset=s5-later notify=retried-hook exit=75 summary=not yet
08:46:42 job=9 state=exhausted preset=x-login notify=file-hook exit=75 action=login note=sign in to the store again summary=exit 75 on all 2 attempts
08:46:54 job=11 state=exhausted preset=s5-later notify=file-hook exit=75 summary=not yet
08:49:09 job=3 state=succeeded preset=s3-ok notify=file-hook exit=0
08:50:09 job=1 state=succeeded preset=s1-ok notify=file-hook exit=0
```

반복 프리셋이 있으면 서버는 health 의 `min_client_version` 을 0.10.0 으로 올린다. 패키지는 아직 0.9.0 이라
`rcm check` 는 종료 1 과 함께 client 행에 FAIL 을 냈다. `rcm run` 은 제출할 때마다 경고 한 줄만 내고
제출은 받아들여졌다. 두 안내 모두 서버의 0.9.0 휠을 설치하라고 하는데 그 휠도 0.10.0 아래다. 버전을 올리지
않은 개발 판이라서 생기는 일이고, 0.10.0 을 출시하면 휠도 0.10.0 이 되어 사라진다. 지시대로 버전은
올리지 않았다.

```text
FAIL  client        v0.9.0 · server v0.9.0 · below min client v0.10.0 · not following (--no-follow) — pip install http://127.0.0.1:8796/client/remote_ci_monitor-0.9.0-py3-none-any.whl
rcm: warning: this client v0.9.0 is older than the server accepts (min_client_version 0.10.0) — pip install http://127.0.0.1:8796/client/remote_ci_monitor-0.9.0-py3-none-any.whl
```

## 발견과 정리

F1(P3). 같은 예약을 두 번 걸면 `rcm run` 이 종료 3 으로 끝난다. 서버는 409 `schedule_exists` 와 첫 잡의
`job_id` 를 돌려주므로 잡이 생기지 않은 것이 확실하다. 그런데 README 의 종료 코드 표에서 3 은 「unknown」이고
「Never treated as a failure」다. 이 코드를 받은 스크립트는 결과를 모른다고 보고 다시 기다리거나 다시 제출할
수 있다. 같은 예약 제출이라도 400 거절(`start_at_past` 등)은 종료 2 다. 원인은 `src/remote_ci_monitor/cli.py`
의 `_submit_refused` 에 있다. 이 함수는 400·401·403·413·0 만 확실한 거절로 보고 409 는 `--machine` 이 있을
때만 넣는다. git_ref 경로에서 확인했고, tree 경로도 같은 함수를 쓰는 것으로 보이지만 실기로는 보지 않았다.
`docs/configuration.md` 「Running a job later」는 409 만 말하고 `rcm run` 의 종료 코드는 말하지 않는다. 고치지
않았다.

```sh
rcm run s1-ok --source git_ref --ref main --no-wait --in 5m   # 0 · submitted job #1 scheduled
rcm run s1-ok --source git_ref --ref main --no-wait --in 5m   # 3 · submit failed: job #1 is already scheduled …
```

버그가 아니라고 본 관찰도 적어 둔다. 반복 시도의 간격은 `interval_seconds` 에 승격 판정 주기 5초가 더해진
값이다. 첫 시도는 일반 예약이라 시작 마감이 있고 반복 시도에는 없다. 둘 다 문서와 설계대로다.

스크립트는 마지막에 서버를 SIGTERM 으로 멈췄다(08:50:13). 그 뒤 8796 포트를 듣는 프로세스는 없었고, 시험
폴더나 스크립트 이름으로 찾은 프로세스도 없었다. `rcm serve`·`worker`·`wait` 가운데 남은 것은 운영 서비스의
serve 와 worker 둘뿐이고 손대지 않았다. 시험 데이터는 `~/.local/share/rcm-devtest-m5s` 에 남겨 두었다. 다음
실행이 지우고 새로 만든다.

이 보고서 밖에 남은 일은 둘이다. 웹 화면 확인과 스크린샷은 #257 이 머지된 뒤에, golive 의 실기는 #255 가
머지된 뒤에 한다.
