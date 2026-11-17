# M5n 검증 보고 — 계약 대조 (2026-11-16)

- 대상: `feat/m5n-capable-worker-placement` HEAD **`84cde79`**(`feat(m5n): 머신과 배치를 사람에게 보인다 …`)
- 명세: `docs/m5n-workplan.md` §3~§13 · 결정 88~104(§17) · 구현 노트 §19, 시나리오 문서
  `docs/m5n-test-scenarios-{a,b,c}.md` 의 「가정」
- 방법: 명세를 한 줄씩 코드와 맞대고(테스트는 믿지 않는다), 어긋나거나 fail-open 인 곳마다
  **빨간 시험을 먼저** 쓰고 고쳤다. 시험은 전부 `tests/test_m5n_verify_contract.py` 에 있다
  (in-process 서버 `test_server_m5n.M5nServer` · 원격 워커 대역 · CLI 가짜 서버).
- 격리: 자기 워크트리와 `.venv` 만 썼다. 운영(8787 · `~/.config/rcm` · `~/.local/share/rcm` ·
  LaunchAgents)에는 닿지 않았다. v22 → v23 시험 DB 는 스크래치 폴더에서 만들고 열었다.
- 판정 요약: **결함 6건을 고쳤다**(높음 1 · 중간 4 · 낮음 1). 고치지 않고 남긴 것 5건(명세 결정이
  필요하거나 영향이 작다). 나머지 대조 항목은 통과.

## 1. 고친 결함

| # | 심각도 | 무엇 | 근거(고치기 전) | 고친 커밋 |
|---|---|---|---|---|
| F1 | **높음** | 원격 워커가 세대가 바뀐/주기가 된 heartbeat 에서 프로브를 **heartbeat 스레드에서** 돌렸다. 설정은 프로브 64개 × 최대 60초를 허용하고, 측정이 `worker_timeout_seconds`(기본 60초)를 넘는 동안 heartbeat 이 끊기면 서버는 워커를 down 으로 보고 **도는 잡을 lost** 로 만든다. 첫 `flutter --version` · `xcodebuild -version` 몇 개면 닿는다 | `remote_worker.py` `heartbeat_once` 안의 `self.measure_and_report()` 직접 호출 · `remote_workers.py` 머리 주석(「down 이고 그 워커의 running·cancelling 잡은 lost」) | `cfa594c` |
| F2 | 중간 | 다시 등록하면 서버가 보고를 지우는데(§19-5) 워커는 등록 응답의 세대를 제 것으로 삼았다 → 다음 heartbeat 이 「세대 같음」이라 `probe_interval_seconds`(10분) 동안 다시 재지 않고, 그동안 any-capable 잡을 못 받고 `checks` 가진 pool 잡도 막힌다(fail-closed 지만 §19-5 의 「등록 직후 재서 보낸다」 위반) | `remote_worker.py` `register()` 의 `self.generation = gen` · 레인의 「not registered → register()」 경로 | `cfa594c` |
| F3 | 중간 | reload 로 프리셋의 `placement = "any-capable"` 을 떼면 그 프리셋의 **대기 capable 잡이 영원히 선다**: claim 의 `capable` 목록은 「지금 설정의 placement 가 any-capable 인 이름」만 싣는데 잡 행은 `placement = 'capable'` 이다. 같은 때 `placement_for` 는 잡의 placement 로 판정해 **시각을 약속**했다(§12 위반 fail-open). reload 로 프리셋이 사라진 capable 잡은 pool 갈래로 떨어져 표시 풀의 레인에 시각을 매겼다 | `capabilities.py` `claimant_for` 의 `presets[name].placement == PLACEMENT_CAPABLE` 필터 · `placement_for` 의 `preset is not None` 조건 | `b7c3871` |
| F4 | 중간 | `check_needed` 를 언제나 `check_pending`(「스스로 풀린다」 — 확인이 필요한 작업에 안 올라감)으로 셌다. 자동 점검은 (워커, 점검, 지문)마다 한 번이라 그 점검이 **취소 · 유실**됐거나 `auto_checks = false` 면 아무도 다시 안 건다 → `checks` 를 가진 pool 잡(scenario-qa)이 조용히 영원히 기다린다. S3 가 막으려던 「열흘 동안 조용히」 그대로 | `capabilities.py` `_why_none` 의 `_CHECK_CODES_PENDING` 처리 · 엣지 E3(취소 · 유실은 다시 안 건다) | `db13195` |
| F5 | 중간 | 웹의 **다시 점검** 버튼은 `encodeURIComponent("@local")` = `/machines/%40local/recheck` 로 부르는데 서버는 경로를 풀지 않고 `_RECHECK_RE` 는 `@local` 만 받아 **로컬 머신(S3 의 Mac mini)에서만 언제나 404** 였다. 시험의 가짜 서버(`tests/fake_rcm_http.py`)는 경로를 퍼센트 디코딩해 이것을 가렸다 | `server.py:229` `_RECHECK_RE` · `web/app.js` `recheckMachine` | `1a6ce21` |
| F6 | 낮음 | ref 를 못 풀어 잡을 못 세운 다시 점검을 `rcm recheck` 는 `#None  sim-check  on mbp` 로 찍고 0 으로 끝났고, 웹은 「다시 점검을 넣었습니다」 토스트를 띄웠다 | `cli.py` `cmd_recheck` · `capabilities.py` `recheck` 의 `{"preset", "job_id": None}` | `7a42c20` |

고친 모양:

- F1: `heartbeat_once` 는 측정을 따로 띄운 스레드(`_measure_quietly`)에 넘기고 곧바로 돌아온다.
  겹침은 이미 있던 `_measure_lock` 이 막는다. 기동 때(레인 전)는 그대로 동기로 잰다.
- F2: `register()` 는 세대를 `None` 으로 둔다 — 「아직 이 세대로 보고하지 않았다」. 다음 heartbeat 이 잰다.
- F3: `claimant_for` 의 `capable` = eligible 인 프리셋 전부(SQL 이 `placement = 'capable'` 로 고르므로
  pool 잡에는 영향 없다). 사라진 프리셋의 capable 잡은 후보 없음 · `no_capable_machine`.
- F4: `_stalled_checks` — `check_needed` 인데 자동 점검이 꺼졌거나 지문이 없거나 그 지문에 이미
  스스로 건 워커는 pending 에서 뺀다. 남으면 `no_capable_machine`(확인이 필요한 작업). 판정 코드
  (§4.3)는 그대로 `check_needed` 이고 `placement_detail` 에 보인다.
- F5: `%40local` 하나만 더 받아 `@local` 로 푼다(다른 인코딩은 여전히 거절 — `a%2Fb` 시험).
- F6: 응답 항목에 `error_code`(`machines[].checks[].error_code` 와 같은 코드), CLI 는 stderr 에 이유를
  찍고 1, 웹은 실패 토스트.

## 2. 고치지 않은 것 (명세 결정이 필요하거나 영향이 작다)

| # | 심각도 | 무엇 | 근거 | 왜 안 고쳤나 · 권고 |
|---|---|---|---|---|
| N1 | 중간 | 자동 점검이 **그 프리셋을 받을 수 없는 워커에도** 점검을 건다. 판정이 (워커, 프리셋) 전부에 대해 나오므로 `scenario-qa`(pool default, `checks = ["sim-check"]`)의 `sim-check` 가 `mac2-worker`(풀 mac2 · **같은 호스트**)와 `mbp` 에서도 돈다. 시뮬레이터를 같이 쓰는 같은 호스트에서 쓸데없이 돌고, 그 실패가 `rcm check` 의 `machines` 행을 FAIL 로 만든다 | `capabilities.py` `sweep_checks` 가 `verdicts_for(view.id)` 전체에서 `check_needed` 를 모음 · 명세 §4.4 「`check_needed` 인 (워커, 점검) 쌍마다」 | 명세 문면 그대로라 오너 결정. 권고: 「그 워커가 후보인 프리셋(any-capable 이고 `accept_any` · 또는 pool 이 `pool`/`pools` 에 드는 것)의 `checks` 만」 |
| N2 | 낮음 | 보고하지 않은 원격 워커(옛 버전)의 claimant 는 `host_key = None` 이라 `concurrency_scope = "host"` 배제가 그 워커에는 안 걸리고, 그 워커가 집은 잡의 `host_key` 도 NULL 이라 다른 워커도 그 잡에 안 막힌다. 화면은 도는 잡의 호스트를 레인에서 채워 막힌 것으로 보일 수 있다(ETA 가 늦게 나오는 쪽 — fail-open 은 아님) | `capabilities.py:389` · `store.py` `_claimable` 의 `b.host_key IS NOT NULL` | 옛 워커의 물리 호스트는 알 수 없다. §3 의 「못 읽으면 `worker:<id>`」를 claimant 에도 쓰면 §7.1 의 읽기 규칙과 같아진다 — 결정만 하면 한 줄 |
| N3 | 낮음 | `rcm run … --machine X` 를 **옛 서버**(v0.4.4)에 보내면 서버가 모르는 키를 무시해 지정 없이 돈다. 사용자는 지정됐다고 믿는다 | `cli.py` 585 · 702 — 응답에 `placement` 가 없는지 안 본다 | 응답에 `placement` 가 없으면 stderr 한 줄(「server ignored --machine (older rcm)」)이 적당 — 잡은 이미 섰다 |
| N4 | 낮음 | 능력 스레드에서 로컬 측정이 예외를 내면 같은 회차의 `sweep_checks` 도 건너뛴다(같은 `try`). 측정이 계속 실패하면 자동 점검도 계속 멈춘다. 스레드는 죽지 않는다(`record_error`) | `capabilities.py` `_capabilities_loop` | `try` 를 둘로 나누면 된다 — 영향이 작아 남김 |
| N5 | 낮음 | 사람의 `rcm recheck` 는 down 워커에도 지정 잡을 세운다(제출 경로의 `--machine` 은 down 이면 409) | `capabilities.py` `recheck` | 명세(§8.1)가 말하지 않는다. 워커가 돌아오면 돈다 — 그대로 두어도 해롭지 않다 |

## 3. 통과한 대조

1. **판정 표 §4.3**(`core/capability.verdict`): 1~9행의 조건 · 상태 · 코드 · 차례가 명세와 같다.
   `missing_passthrough` 는 판정에 안 들어간다(결정 92). 작업 공간 도구는 `unknown workspace_tool`
   (결정 93, `runner.measure_tools` 가 절대 항목만 보고 상대 항목이 있으면 `workspace`). 가정 A-12
   (세대는 맞는데 그 프리셋이 보고에 없음 → `report_missing`) · A-15(지문 None 이면 9행) 그대로.
2. **claim §5.2**(`store._claimable`): 세 갈래(지정 · pool − `gated` · capable)와 `(priority DESC, id)`.
   `gated` 는 `checks` 가 있고 eligible 이 아니며 도구 코드가 아닌 것(결정 98 — 도구 불능 pool 잡은
   집혀서 `tool_missing`). `accept_any` 가 거짓이면 capable 갈래가 빠진다. 로컬의 `accept_any` 는
   보고를 만들 때 `[server] local_accept_any` 를 쓴다(`measure_capabilities`). 그룹 배제는 **후보 잡의
   범위**(`c.concurrency_scope`). capable claim 은 같은 UPDATE 에서 `display_pool=COALESCE(display_pool,
   pool), pool=?` (SQLite 는 오른쪽을 옛 값으로 읽는다). 지정 잡 · pool 잡에도 `host_key` ·
   `fingerprint_hash` 를 박는다. 지정 점검 잡은 제출 때 대상의 풀(`view.pool`)로 선다.
3. **ETA 그리디 §7.2 ↔ claim**: `placement_for` 와 `claimant_for` 가 같은 판정 · 같은 `accept_any`
   (로컬은 `local_accept_any`) · 같은 `gated` 규칙을 쓴다. down 워커의 레인은 `live` 에서, 보류(held)
   레인은 `open_cands` 에서 빠진다. 그룹 범위는 대기 잡의 범위로(`_scope_matches`). 어긋난 곳은 F3 하나
   였고 고쳤다. host 범위는 도는 잡의 `host_key` 가 NULL 이면 화면이 더 보수적이다(N2 — 늦게 말하는 쪽).
4. **자동 점검 §4.4**: (워커, 점검, 지문)마다 한 번(`auto_check_done`) · down · 보고 없음 · 지문 None 은
   건너뜀 · ref 실패는 `_submit_check` 가 잡아서 그 회차만 건너뛰고 `error_code = ref_unresolved` 이며
   한 번 기록을 쓰지 않는다. `repo` 없는 git_ref 프리셋은 설정 로더가 저장소 하나면 채우고 여럿이면
   기동을 거절하므로 스위퍼까지 오지 않는다(와도 `GitError` 로 잡힌다). 스위프의 예외는
   `_capabilities_loop` 가 `record_error` 로 남기고 루프는 산다(N4 의 단서만).
5. **세대 · reload**: `[[probes]]` 는 `RELOADABLE_NOT` 밖이라 reload 된다. reload 는 세대를 올리고 로컬을
   바로 다시 잰다. 옛 세대 보고는 409 `generation_mismatch`, 워커는 한 번 다시 받는다. 원격 워커는 heartbeat
   의 세대가 다르면 다시 잰다(F1 · F2 를 고친 뒤 heartbeat 밖에서).
6. **보안 · 사생활**: 하드웨어 UUID 원문은 `host_key_from` 으로 해시되어서만 나간다(워커 · 서버 둘 다,
   로그에도 없음). 서버는 프로브 값을 120자에서 거절하고(`_probe_from_json`), 프리셋 사실 목록은 64개 ×
   200자로 자른다. `GET /api/machines` 는 `_read_only_ok` + 워커 토큰 거절(`/api/repos` 와 같다),
   `POST /machines/{id}/recheck` 는 `require_admin`, `/worker/spec` · `/worker/capabilities` 는 워커 토큰
   (후자는 등록된 워커만). 클라이언트 토큰의 `machine` 은 점검 프리셋에만(그 밖은 403 — 결정 96).
7. **하위 호환**: 옛 워커(보고 없음)는 claimant 의 `capable` 이 비고 `checks` 있는 프리셋만 `gated` —
   `checks` 없는 pool 잡은 그대로 집는다. 이 워커 + 옛 서버: 등록 응답에 세대가 없고, 기동 때의
   `GET /worker/spec` 이 404 → `ClientError` 로 잡혀 로그 한 줄, heartbeat 에 세대가 없으니 다시 안 잰다.
   pool 잡은 그대로 돈다. (N3 만 남는다.)
8. **DB v22 → v23**: `origin/dev`(`1c1f8fc`, v0.4.4)의 소스로 v22 파일을 만들고(로컬 · `mac2-worker`
   종료 잡 8개 + 대기 잡 1개) 이 빌드로 열었다 → `user_version 23` · `backup/rcm.sqlite3.v22.bak`
   (`user_version 22`) · 옛 행 전부 읽힘(`placement pool` · `concurrency_scope pool` · `sample True` ·
   `host_key`/`fingerprint_hash`/`display_pool`/`target_worker` None) · `list_sample_rows` 8행 —
   `resolve_host` 로 로컬 4 · `worker:mac2-worker` 4 로 갈라져 중앙값이 난다(`test_legacy_local_rows_*`
   와 같은 결론).

## 4. 명세에서 바꿀 문면

- §4.2 보고 경로: 원격 워커의 다시 재기는 **heartbeat 과 다른 스레드**에서 한다고 적는다(F1). 등록 직후
  워커의 세대는 「아직 보고 안 함」이다(F2) — §19-5 에 한 줄.
- §5.2 claim: `capable` 은 「지금 eligible 인 프리셋 이름」이고 **설정의 현재 placement 로 거르지 않는다**
  — 잡은 제출 때의 placement 를 지닌다(F3). reload 로 사라진 프리셋의 capable 잡은 `no_capable_machine`.
- §7.3 `check_pending` 의 정의: 「점검 잡이 돌고 있거나 기다리고 있거나, **스위퍼가 아직 한 번 걸 수 있는**
  `check_needed`」. 자동 점검이 꺼졌거나 그 지문에 이미 건 `check_needed` 는 `no_capable_machine` (F4).
- §8.1 `POST /machines/{id}/recheck`: 응답 항목 `{preset, job_id, error_code?}` — 못 건 점검은 `job_id`
  null 과 `error_code`(F6). 머신 id `@local` 은 `%40local` 로도 받는다(F5).
- §4.4 자동 점검의 대상(N1)과 §6 의 「호스트를 모르는 워커」(N2)는 결정이 필요하다.

## 5. 검사

- 새 시험: `tests/test_m5n_verify_contract.py` 11개 — 고치기 전 9개 빨강(F1~F6), 2개는 회귀 방어
  (`a%2Fb` 거절 · 한 번 남은 `check_needed` 는 여전히 `check_pending`).
- 전체: `ruff check .` · `ruff format --check .` 초록, `pytest -p no:cacheprovider -q` **4911 통과 · 3 건너뜀 · 1 실패**(542초 — 실패는 아래 기준선과
  같은 흔들리는 시험이고, 혼자 세 번 돌리면 세 번 다 통과한다),
  `node --test tests/web/*.test.js` **929 통과 · 0 실패**.
- 기준선(고치기 전 `84cde79`): pytest 실패 1 — `tests/test_cancel_capability_m5l.py::test_s6_the_same_
  leave_secret_sent_twice_at_once_succeeds_exactly_once`(동시 6요청 중 하나가 `BrokenPipeError` 로 결과를
  못 남김 — 부하 탓의 흔들림, M5n 과 무관).
