# M5o 테스트 시나리오 A — 같은 브랜치의 옛 잡을 치운다(`supersede`) (2026-11-18)

`docs/m5o-workplan.md` §2(설정 §2.1 · 대상 §2.2 · 남기는 것 §2.3 · 제출 쪽 끄기 §2.4 · 인터페이스 §2.5)와
§5 의 A 표(A1~A21)를 옮기고, 코드를 읽으며 찾은 경우(A22~)를 더한 것이다(test-first). 결정은 109~116.

| 파일 | 무엇을 잠그나 |
|---|---|
| `tests/test_supersede.py` | 순수 규칙 `core/supersede.py` · 설정 키 `Preset.supersede` · 요약 코드 `superseded` |
| `tests/test_store_supersede.py` | `Store.supersede` 한 트랜잭션 — 대기/실행 갈래 · 우선순위 · 트랜잭션 안에서 다시 판정 |
| `tests/test_server_supersede.py` | `POST /jobs` → 업로드 → `queued` 의 두 자리 · 원격 워커 · 상태 JSON · CLI `wait`/`run` |
| 기존 잠금 | `test_status_schema` · `test_status_outcome` 의 키 집합에 `superseded_by` · `test_outcome` 표에 `superseded` |
| `tests/web/supersede.test.js` | 요약의 `#N` 을 그 잡으로 가는 링크로 |

## 공통 — 픽스처

- 순수 시험의 잡은 `jobfactory.job(...)` 에 `source=Source(mode="tree", repo="org/app", branch="feat/x",
  tree_hash=...)` 를 준다. 새 잡 N 은 id 가 가장 크다.
- 서버 시험은 `test_worker_api.WorkerServer`(주입 시계 · 로컬 워커 스레드 없음)에 `supersede` 를 켠
  프리셋을 끼운다. tree 잡은 `branch` 를 실어 제출하고 `PUT /jobs/{id}/tree` 로 `queued` 가 된다.
  git_ref 잡은 브랜치 이름이 ref 여야 하므로 `resolve_ref` 를 시험 안에서 바꿔 끼워 해석마다 새
  sha 를 준다(같은 브랜치에 push 가 또 온 것).
- 로컬 레인 실행(A2)은 `test_server.Server(workers=True)` 의 `slow` 프리셋.

## 표

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A1 | 베스트: `all` · 같은 브랜치 · 옛 잡 queued | O tree `feat/x` queued, N 같은 프리셋·브랜치·입력, 다른 트리 → 업로드 끝 | O `cancelled`, `cancelled_by = "superseded"`, `summary_code = "superseded"`, `summary_args = {"by": N}`, 요약 `superseded by #N`. N `queued` |
| A2 | 베스트: `all` · 옛 잡 running(로컬 레인) | `slow` 프리셋 O 가 도는 중, N 업로드 끝 | O 가 `cancelling` → 평소 취소 길(SIGTERM)로 `cancelled`, 요약 `superseded by #N` |
| A3 | `waiting` · 옛 잡 running | O running(원격 claim), N queued | O 그대로 `running`, 반환 id 없음 |
| A4 | 옛 잡 running 이 **원격 워커** | 워커가 O 를 claim, N queued | O `cancelling`, 그 워커의 heartbeat 응답 `cancel` 에 O. 워커가 `cancelled` 를 보고하면 요약 `superseded by #N` |
| A5 | 입력이 다르다 | O `scope=full`, N `scope=fast` | 둘 다 남는다 |
| A6 | 브랜치가 비었다(detached tree) | O·N 의 `branch` 가 None(또는 빈 글자) | 둘 다 남는다 — 한쪽만 비어도 같다 |
| A7 | tree 와 git_ref, 같은 이름의 브랜치 | O tree `branch=main`, N git_ref `ref=main` | 둘 다 남는다(모드가 다르다) |
| A8 | 같은 신원 | 같은 tree_hash 로 다시 제출 | 합류(`joined: true`) — 새 잡이 없어 supersede 가 끼어들 자리가 없다. `join: false` 로 같은 신원의 새 잡을 세워도 규칙 5 로 남는다 |
| A9 | 옛 잡에 다른 요청자의 합류자 | O 에 bob 이 합류, N 은 alice | O 남는다. 합류자가 alice 자신(요청자 이름과 같음)이면 치운다 |
| A10 | 새 잡의 업로드가 실패 | N 업로드 중 거절(`snapshot_rejected` · 끊김 · blob 누락) | O 그대로 queued |
| A11 | 새 잡 업로드 중 서버 재시작 | N uploading 인 채 `recover_on_start` | N `cancelled`(server restarted during upload), O 그대로 |
| A12 | `--no-supersede` / `"supersede": false` | 프리셋 `all`, N 제출 본문에 `supersede: false` | O 남는다. tree 잡은 업로드가 끝날 때까지 이 뜻을 기억한다 |
| A13 | 프리셋 `off`(기본) | 키 없음 | 남는다 — 오늘과 같다 |
| A14 | 옛 잡이 셋(queued 둘 + running 하나) | `all` | 셋 다 처리, `Store.supersede` 반환 id 셋(오름차순) |
| A15 | 옛 잡 `high` · 새 잡 `normal` | | N `high`. 반대(옛 `low` · 새 `normal`)면 N 그대로 `normal` — 낮아지지 않는다 |
| A16 | 옛 잡이 `target_worker` 점검 잡 / `--machine` 잡 | O 에 `target_worker` | 남는다 |
| A17 | 같은 순간에 두 새 잡이 queued (경쟁) | O < N1 < N2, 두 스레드가 동시에 `_supersede_after_queue` | N2 만 남고 O · N1 이 치워진다. N1 이 N2 를 치우는 일은 없다(id 규칙). `Store.supersede` 가 트랜잭션 안에서 다시 판정한다 — 이미 치워진 N1 이 새 잡이면 아무것도 안 한다 |
| A18 | 옛 잡이 이미 `cancelling` | 사람이 먼저 `rcm cancel` | 다시 취소 요청하지 않는다(`cancel.by`·`kill_at` 그대로), 요약도 바꾸지 않는다, 반환 id 에 없다 |
| A19 | `rcm wait O` | O 가 superseded | 종료 2, stderr 에 `#O superseded by #N — rcm wait --job N`, JSON `superseded_by: N` |
| A20 | reload 로 프리셋이 `off` → `all` | O queued(off 시절) 뒤 reload | 이미 들어온 잡은 아무 일 없다. 다음에 queued 가 되는 잡이 O 를 치운다 |
| A21 | 설정 `supersede = "yes"` / `true` / `1` | | `ConfigError`(값 셋을 말한다) |

### 코드를 읽으며 더한 경우

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A22 | 옛 잡이 **업로드 중** | `all`·`waiting` 어느 쪽이든, O uploading 인데 N 이 먼저 queued | O `cancelled`(superseded). O 의 업로드는 409 로 끝나고 그 본문에 `state` · `superseded_by`. `rcm run` 은 다시 조회하지 않고(업로드 실패 자리의 약속) 그 본문으로 `superseded by` 한 줄 + 종료 2 |
| A23 | 새 잡이 queued 직후 이미 끝났다 | N 이 `_supersede_after_queue` 전에 취소됨 | 아무것도 안 치운다 |
| A24 | 풀이 다르다 | O `--pool linux`, N 기본 풀 | 둘 다 남는다 — 다른 머신에서 묻는 다른 질문이다(명세 §2.2 에 없던 규칙, §10 에 남긴다) |
| A25 | 치운 잡은 추정 표본이 아니다 | superseded 잡(시작했다가 취소) | `medians_from` 이 세지 않는다 — 평소 취소와 같은 `cancelled` 상태다 |
| A26 | 원격 워커가 취소를 확인하지 않는다 | O cancelling, `kill_at + 2 × heartbeat` 지남 | 서버가 닫는다. 요약은 `superseded by #N`(cancel_unconfirmed 대신 — §10) |
| A27 | 치운 잡이 kill 전에 스스로 끝났다 | 원격 워커가 `succeeded` 보고 | `succeeded`, `superseded_by` 는 null(실제로 끝까지 돌았다) |
| A28 | API `supersede` 가 bool 이 아니다 | `"supersede": "no"` | 400 |
| A29 | 프리셋 `off` 에 `supersede: true` | | 아무것도 안 한다 — 제출 쪽은 켤 수 없다 |
| A30 | 큐 행 · 잡 상세 | O cancelling(superseded) · O cancelled | 큐 행과 `GET /jobs/O` 에 `superseded_by: N`. 평범한 잡은 `superseded_by: null` |
| A31 | 요청자가 다르다 | O alice, N bob | 남는다 |
| A32 | 프리셋이 다르다 | O `gate`, N `gate-fast` | 남는다 |
| A33 | repo 가 다르다 | O `org/app`, N `org/other` | 남는다 |
| A34 | 옛 잡의 id 가 더 크다 | 규칙의 입력에 N 보다 큰 id | 그 잡은 대상이 아니다 |
| A35 | 웹 요약 | 최근 행 `summary_code = "superseded"` | `#N` 이 `data-goto="N"` 버튼으로 그려지고 나머지 글자는 escape 된다. 시작 전에 치운 잡도 「시작 전 취소 · superseded」가 아니라 이 문장 |
| A36 | `rcm run --no-supersede` | CLI | 본문에 `"supersede": false`. 플래그가 없으면 키도 없다(옛 서버 호환) |
