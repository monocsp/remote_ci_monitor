# Codex 크로스리뷰 — M1 작업 명세 (2026-09-05 오전)

- 리뷰어: Codex CLI · `codex exec --sandbox read-only` (프롬프트는 아래 그대로)
- 대상: `docs/m1-workplan.md`(결정 A~G · hostparse/hostsample · 이벤트 버스/SSE · CLI · 테스트 계획) · PLAN.md v2.1 · M0 코드
- 결론(Codex): 방향은 맞지만 A(캐시 「항상 최신」) · E(SSE 폴백 상태기계) · F(`rcm wait` 재조회 폭주) · macOS 메모리 의미는 구현 전에 문장을 고쳐야 한다. fail-open 금지를 CLI 출력까지 더 세게.

## 반영

| # | Codex 지적 | 판정 | 한 일 |
|---|---|---|---|
| 필수 1 | `/api/status` 가 0.2초 낡을 수 있음 | 동의 | `dirty` 면 요청에서 **즉시** DB 재조회, 0.2초 TTL 은 dirty 가 아닐 때만(이벤트가 없는 변경 — 업로드 진행·last_output_at — 을 잡기 위한 상한). `server.App._snapshot` |
| 필수 2 | 발행 지점 누락 | 동의 | 발행 지점 표를 명세 3절에 고정: 제출 · 합류 · 업로드 완료/413/중단 · 취소/합류자 이탈 · pause/resume · janitor 포기 · **worker claim/phase/finish** · marker · **recover_on_start**. 업로드 진행(`received_bytes`)은 이벤트 없이 TTL 로 |
| 필수 3 | SSE 503 폴백이 웹 UI 4절과 다름 | 동의 | 명세 0-E: 브라우저는 503 뒤에도 `Retry-After` + 2→30s 백오프로 SSE 재시도, 그 사이 10초 폴링. CLI `wait` 는 2초 폴링(별도) |
| 필수 4 | `rcm wait` 가 이벤트마다 재조회 → 폭주 | 동의 | 클라이언트 재조회를 **초당 1회**로 제한(`job_finished` 는 즉시). `client.wait_for_job` |
| 필수 5 | reset/lag 의미 | 동의 | reset = 「현재 last_id 부터 새 이벤트만, 클라이언트는 전체 재조회」. overflow 는 **큐를 비우고 lag 하나 + 방금 이벤트**만(lag 폭주 방지). `events.Subscription._offer` |
| 필수 6 | SSE `settimeout(None)` · shutdown 정리 | 동의 | 소켓 타임아웃 30초(쓰기가 막히면 끊긴다), `EventBus.shutdown()` 이 모든 구독자에게 `server {shutdown: true}` 를 넣어 루프를 깨운다 |
| 필수 7 | macOS used 정의 | 오너 확인 | `active + wired + compressor`(Activity Monitor 의 「Memory Used」에 가까움) 유지. `top PhysMem used` 와 다르다는 것을 README 「why the numbers can be wrong」에 적음. **결정 항목 19(오너 확인 대기)** |
| 필수 8 | Apple Silicon `ioreg` 키 의존 | 동의 | PerformanceStatistics 가 있는 항목만 모아 util 은 max, 메모리는 합. 키가 없으면 `gpu: null` + 구체 `gpu_note` |
| 필수 9 | CLI fail-open | 동의 | `or 0` 패턴 제거(`eta --job` 의 ahead), 모르는 값은 전부 `—`. `--mine` 은 토큰 없으면 종료 2 |
| 필수 10 | 실기 검증 체크리스트 | 동의 | README 「Verify on the real build machine」 체크리스트 8단계 |
| 좋음 1·2 | 링 버퍼 500 · 구독 큐 256 | 동의 | 기본 2048, 구독 큐는 링 버퍼와 같은 크기 |
| 좋음 3 | `/proc/stat` 차분 예외 | 동의 | 카운터 감소 → None, guest 제외, 0~100 clamp |
| 좋음 4 | 전부 실패 판정 | 동의 | load·cpu·memory 셋 다 None 이면 `hosts_error`, ps/gpu 는 보조(이미 그렇게 구현) |
| 좋음 5 | 샘플러 sleep | 동의 | 기본 sleep 을 `stop.wait()` 로 |
| 좋음 6 | ghost job inputs | 동의 | `eta_for_new(inputs=…)` |
| 좋음 7 | PLAN 「신뢰도」 문장 | 동의 | PLAN 「큐 규칙」 신뢰도 bullet 을 「서버가 `estimate.confidence` 로 싣는다」로 |
| 좋음 8 | `rcm logs` 종료 코드 | 동의 | 403 등 서버 거부 2 · 네트워크 불명 3 — 명세 5절 표에 한 줄 |

### 오너에게 물을 것 (추천값으로 구현, 확인 대기 — PLAN 결정 항목 19·20)

1. **macOS 메모리 「used」**: `active + wired + compressor`(지금) vs `top` 의 `PhysMem used`. 추천: 지금 값 — Activity Monitor 「Memory Used」와 같은 의미라 사용자가 익숙하다.
2. **GPU 를 못 읽는 머신의 M1 완료 판정**: `GPU — unavailable <note>` 를 통과로 본다(추천). 지원 머신(Apple Silicon `ioreg` · NVIDIA `nvidia-smi`)에서만 숫자가 보인다.

## 프롬프트

```
(scratchpad/codex/m1-design-prompt.md 원문 — 검토 항목 A~E: 결정 A~G 의 PLAN 충돌·함정 / EventBus API 와 SSE 스레드 함정 /
hostparse·hostsample 의 macOS·Linux 계산 / CLI fail-open / M1 완료 기준을 채우는 데 빠진 것. 답 형식: 결론 · 반드시 고칠 것(≤10) ·
고치면 좋은 것(≤8) · 그대로 둘 것(≤5) · 오너에게 물어야 할 것)
```

## 리뷰 원문

결론: M1 명세는 방향은 맞지만, 지금 그대로 구현하면 `/api/status`의 "항상 최신", 웹 UI의 SSE/폴링 상태기계, `rcm wait` 부하 제어, macOS 자원 수치 의미가 PLAN보다 약해진다. 특히 A/E/F는 구현 전에 문장을 고쳐야 한다. 나머지는 대부분 M1 범위에 맞지만, fail-open 금지 원칙을 CLI 출력까지 더 세게 박아야 한다.

**반드시 고칠 것**

1. 0-A · 상태 캐시 · `/api/status`가 0.2초 낡을 수 있음 · `dirty=True`면 `/api/status` 요청에서 즉시 DB를 다시 읽고, 0.2초 디바운스는 SSE/내부 재계산 합치기에만 써라.
2. 0-A/3 · 이벤트 발행 누락 · App 바깥에서 DB가 바뀌면 캐시가 안 더러워짐 · 제출, 업로드 진행/완료, 합류자 추가/삭제, 취소, pause/resume, janitor, worker phase, marker, finish, recover_on_start를 발행 지점 표로 고정하라.
3. 0-E/웹 UI 4절 · SSE 503 폴백 · "폴링으로 폴백"만 쓰면 웹의 "SSE 재연결 2→30s + 폴링 10s"와 다름 · 브라우저는 503 뒤에도 `Retry-After`와 백오프로 SSE 재시도하고, 그 사이 10초 폴링을 유지한다고 써라. CLI wait는 별도 2초 폴링으로 명시하라.
4. 0-F/5 · `rcm wait` 재조회 폭주 · marker가 몰리면 대기 세션 수 × 이벤트 수만큼 `GET /jobs/{id}`가 DB를 친다 · `/jobs/{id}/events`가 0.2~1초 단위로 coalesce한 job snapshot을 직접 보내게 하거나, 클라이언트 재조회는 최대 1초 1회로 제한하라.
5. 3 · EventBus reset/lag · 의미가 부족함 · Last-Event-ID가 링 밖이면 `reset` 1개를 보내고 "현재 last_id부터 새 이벤트만 구독, 클라이언트는 즉시 전체 재조회"로 못 박아라. 큐 overflow는 큐를 비우고 `lag` 1개만 넣어 중복 lag 폭주를 막아라.
6. 3 · SSE 스레드/소켓 · `settimeout(None)`은 느린 클라이언트 write에서 shutdown이 걸릴 수 있음 · 읽기 타임아웃만 없애고 write는 30초 같은 유한 timeout으로 끊어라. shutdown 때 모든 구독 큐에 sentinel을 넣고 unsubscribe되게 하라.
7. 1~2 · macOS 메모리 · `active+wired+compressor`는 `top`의 `PhysMem used`와 다름 · UI의 "Memory 14.0 / 24 GB"가 OS used인지 pressure성 used인지 정하라. 전자면 `top PhysMem used`를 파싱하고, 후자면 라벨을 `active+wired+comp` 의미로 맞춰라.
8. 1~2 · Apple Silicon GPU · `ioreg PerformanceStatistics` 키는 OS/SoC별로 깨질 수 있음 · 첫 번째 가속기 고정 대신 PerformanceStatistics가 있는 엔트리를 고르고, 여러 개면 util은 max, mem은 sum으로 집계하라. 키가 없으면 `gpu: null` + 구체 `gpu_note`로 끝내라.
9. 5 · CLI fail-open · 모르는 값을 0처럼 그릴 위험 · `eta`, `jobs`, `top`, `wait` 출력에서 `None`은 전부 `—`로만 표시하고, `elapsed or 0`, `expected or 0`, `ahead or 0` 패턴을 금지하라. `--mine`에서 토큰이 없으면 빈 목록이 아니라 종료 2로 실패하라.
10. 8 · M1 완료 기준 · 실기 검증 항목이 구현 작업으로 덜 쪼개짐 · README 절차에 `serve --bind <tailscale-ip>`, 토큰 생성, 원격 `rcm check`, 원격 `rcm run`, 별도 세션 `rcm top`, 두 번째 세션 합류 확인, GPU unavailable 판정 기준까지 체크리스트로 넣어라.

**고치면 좋은 것**

1. 3 · 링 버퍼 500 · 호스트 5초 표본만 보면 충분하지만 marker burst에는 작을 수 있음 · 기본 2048 또는 `[server] event_history` 설정으로 빼라.
2. 3 · 구독 큐 maxsize 256 · history보다 작아 lag가 자주 날 수 있음 · 기본을 history와 맞추거나 "느린 구독자는 lag 후 재조회"를 정상 경로로 테스트하라.
3. 1 · Linux `/proc/stat` · 차분 계산 예외가 덜 잠김 · total delta ≤ 0, counter 감소, guest/guest_nice 처리, 0~100 clamp를 테스트에 넣어라.
4. 2 · 전부 실패 판정 · gpu/ps만 성공해도 표본을 만들면 Host pressure가 가짜가 됨 · load/cpu/memory가 모두 없으면 hosts_error, ps/gpu는 보조 정보로만 취급하라.
5. 2 · 샘플러 stop · Linux CPU 1초 차분 sleep이 shutdown을 늦춤 · `time.sleep(1)` 대신 `stop.wait(1)`로 끊기게 하라.
6. 0-G · `/api/eta` · 현재 `eta_for_new`는 ghost job inputs가 비어 있음 · normalized inputs를 ghost row에 넣어 `--json`과 출력 key가 일치하게 하라.
7. 4 · `estimate.confidence` · PLAN에는 원래 UI/rcm top 계산이라고 되어 있음 · M1 결정대로 서버가 싣는다면 PLAN의 해당 문장을 같이 바꿔 충돌을 없애라.
8. 5 · `rcm logs --follow` · 403은 2로 되어 있으나 네트워크 불명은 3이어야 함 · 상태별 종료 코드 표를 명세에 한 줄 추가하라.

**그대로 둘 것**

1. 1 · macOS `top -l 2` 마지막 CPU 표본 사용 · 맞다. 첫 표본은 부팅 이후 누적이라 버려야 한다.
2. 1~2 · 값 없음은 `None/null` · 맞다. 0으로 그리면 fail-open 위반이다.
3. 0-C · `hosts[].history[]` 메모리 보관 · 맞다. 5분 sparkline 용도라 DB 영속화는 과하다.
4. 3 · `store`는 이벤트 버스를 모르게 두는 구조 · 맞다. 저장 계층을 순수하게 유지하고 App이 발행 책임을 갖는 편이 낫다.
5. 3 · M1에서 `ThreadingHTTPServer` 유지 · 상한과 shutdown 정리만 보강하면 한 빌드 머신용으로 충분하다.

**오너에게 물어야 할 것**

1. macOS 메모리의 "used"를 무엇으로 볼지: `top PhysMem used`에 맞출지, `active+wired+compressor` 압력성 수치로 갈지.
2. GPU가 `ioreg`/`nvidia-smi`에서 안 잡히는 머신의 M1 완료 판정: `GPU — unavailable <note>`를 통과로 볼지, 지원 머신에서만 완료로 볼지.
