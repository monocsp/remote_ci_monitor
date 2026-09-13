# Codex 크로스리뷰 — 게이트 최적화 요청 다섯(M5j) 설계 초안 (2026-09-10)

> 대상: `docs/gate-optimization-workplan.md` 초안 v0.1 · 오너 지시로 실행 · `codex exec --sandbox read-only`
> (`gpt-5.6-sol`, reasoning high) · 한 라운드, 327,706 토큰. 판정: G1·G3·G4 **수정해서 채택**, G2·G5 **보류**(모델을 다시
> 쓴다). 초안 §6 의 「적대적 조합은 G2↔PR3 뿐」은 틀렸다 — 아래 §3 의 13개. 전부 v0.2 에 반영했다.

## 1. 준 프롬프트 (전문)

```
이 레포(remote_ci_monitor)는 빌드 머신 한 대의 로컬 잡 서버다. Python 3.11+, 런타임 의존성 0, 표준 라이브러리만.
`PLAN.md` 가 정본이고 `AGENTS.md` 에 집안 규칙이 있다. 지금 체크아웃은 dev(a7aaea8) 이고 M5i(docs/gate-replay-fixes-workplan.md)의
PR 여덟이 전부 들어가 있다(미출시 · 곧 v0.2.6).

`docs/gate-optimization-workplan.md` 는 다른 머신의 팀(맥미니 10코어·24GB 에서 Flutter 게이트를 rcm 으로 돌린다)이 오늘 보낸
요청 다섯(G1~G5)을 코드에 대고 파악한 **설계 초안(M5j v0.1)** 이다. 이것을 **설계 크로스리뷰** 해 달라. 항목마다 「채택 ·
수정해서 채택 · 보류 · 기각」을 판정하고, 반대하면 근거와 대안을 구체적으로 적어라. 동의하면 짧게.

특히 두 가지를 봐 달라:
(A) 초안 §6 「M5i 와의 겹침·충돌」이 맞는가 — 빠진 충돌이 있는가. 특히 G2(프리셋별 워크스페이스 슬롯)와 M5i PR 3 의 회계
    (charged/reclaimable · 인벤토리 `workspaces/<id>` · 고아 · 바닥·예산), PR 2 의 오프라인 dry-run(임시 사본 + 실제
    workspaces/ 계획), M5h 의 대장 불변식(선언 라벨 ⇔ `job_failures` 행 — v16 이 이것으로 옛 라벨을 가른다)과 G4 의
    `tool_missing`, G5 의 v17 열 추가와 PR 2 의 migrate() 경계 백업.
(B) 초안이 「서버는 임의 출력을 파싱하지 않는다 · fail-open 금지 · 스키마 v1 키 추가만 · 옛 클라이언트 유지」를 어기는 곳이
    있는가. G3 ③(heavy 구간 선언)을 보류한 판단, G5 를 기본 off 로 둔 판단, G2 를 별도 명세로 뺀 판단이 맞는가.

읽을 것: docs/gate-optimization-workplan.md(전부) · docs/gate-replay-fixes-workplan.md(§0 · §3 B2 B4 I5 · §7 결정 73~83) ·
PLAN.md(「큐 규칙」·「진행 — 스텝 마커 프로토콜」·「fail-open 금지」·「/api/status 스키마 v1」·결정 51~83) ·
src/remote_ci_monitor/core/progress.py(progress_for_job) · core/status.py(job_json 근처 195~235행) · core/queue.py(레인 배정 250~330행) ·
core/admission.py(decide) · core/retention.py(VolumeItem · workspaces_to_purge) · janitor.py(inventory · plan) ·
materialize.py(prepare_git_ref) · gitops.py(checkout) · worker.py(outcome_for 130~200행 · _materialize · 환경 구성 440~470행) ·
server.py(App.cancel 1769~1790행 · GET /jobs/<id>) · store.py(_MIGRATIONS 12~16 · add_marker · markers · migrate 의 백업) ·
cli.py(cmd_run 의 KeyboardInterrupt 취소 630~645행 · cmd_cancel · cmd_check 의 행들) · config.py(PresetSection · _validate_server ·
admission 키) · docs/configuration.md(「Admission」·「Making a failure explain itself」·「Naming what failed」).

실측(다시 재지 마라, 초안 §0 에 있다). 코드를 고치지 마라 — 판정과 근거만. 출력은 한국어로, 항목별 판정표 → 빠진 충돌 → 규칙 위반
→ 대안 순으로, 항목마다 근거가 되는 파일·행을 적어라.
```

## 2. Codex 의 답 (전문 · 파일 경로는 리뷰 워크트리 기준)

## 항목별 판정표

| 항목 | 판정 | 근거 |
|---|---|---|
| G1 종료 잡 스텝 시간 | **수정해서 채택** | 저장된 마커와 `progress_for_job()`을 재사용하는 방향은 맞다. 종료 시각으로 마지막 스텝을 닫는 동작도 이미 테스트돼 있다([progress.py:222](src/remote_ci_monitor/core/progress.py:222), [test_progress.py:211](tests/test_progress.py:211)). 다만 `없음=[] / 삭제됨=null`은 현재 저장 구조로 구별할 수 없고, `rcm wait` JSON을 불변으로 둔다는 주장도 실제 흐름과 맞지 않는다. |
| G2 프리셋별 워크스페이스 슬롯 | **보류** | 별도 M5k로 빼는 판단은 맞다. 그러나 초안의 충돌 분석보다 범위가 훨씬 크다. 현재 인벤토리는 숫자 디렉터리만 읽으므로 비숫자 슬롯은 고아로 세는 게 아니라 **통째로 누락**된다([janitor.py:311](src/remote_ci_monitor/janitor.py:311)). `VolumeItem`·삭제·회계 전부 `job_id: int`와 종료 잡 상태에 묶여 있다([retention.py:157](src/remote_ci_monitor/core/retention.py:157), [retention.py:191](src/remote_ci_monitor/core/retention.py:191)). |
| G3 병렬 light/heavy | **수정해서 채택** | ①은 heavy 구간에 이미 별도의 권위 있는 락이 있을 때만 실험적으로 채택, ② 메모리 admission은 **기각**, ③ heavy 마커 방식의 보류는 **채택**한다. 메모리 게이트는 PLAN 결정 42가 명시적으로 배제했다([PLAN.md:635](PLAN.md:635)). |
| G4 `requires` | **수정해서 채택** | 잡의 최종 환경으로 실행 전 검사하고 `tool_missing`으로 실패시키는 핵심은 맞다. 임의 출력 파싱도 아니다. 다만 PATH 공개, 원격 워커의 구조화된 실패 전달, `rcm check`의 환경 차이를 고쳐야 한다. |
| G5 제출 비밀 취소 | **보류** | 기본 off 판단은 맞지만, “잡당 해시 열 하나”로는 요청자와 여러 합류자에게 서로 다른 권한을 발급할 수 없다. `--force`가 같은 공유 토큰의 옛 권한을 되살리는 것도 기능 목적을 무력화한다. v17의 모델부터 다시 써야 한다. |

## 빠진 충돌과 잘못 적은 충돌

초안 §6의 “적대적 조합은 G2↔PR3뿐”이라는 결론은 맞지 않는다.

1. **G1: 마커 없음과 마커 삭제를 구분할 수 없다.**

   `Store.markers()`는 행이 없으면 언제나 빈 목록만 돌려준다([store.py:2003](src/remote_ci_monitor/store.py:2003)). 메타데이터 만료 때는 이벤트와 잡 행을 같은 트랜잭션에서 함께 지우므로, “잡은 조회되지만 마커만 180일 지나 삭제됨” 상태가 없다([store.py:1002](src/remote_ci_monitor/store.py:1002)). 따라서 초안의 `삭제됨 → steps:null` 테스트는 현재 모델에서는 만들 수 없다. 삭제 뒤에는 잡 자체가 404다.

2. **G1: `ok`는 “마커가 말한 것 그대로”만은 아니다.**

   다음 `step`이 오면 앞 스텝을 암묵적으로 `ok=True`로 닫고, 성공 종료의 마지막 열린 스텝도 `True`로 추론한다([progress.py:128](src/remote_ci_monitor/core/progress.py:128), [progress.py:151](src/remote_ci_monitor/core/progress.py:151)). 실패를 `False`로 추론하지 않는다는 점은 안전하지만, §6의 설명은 고쳐야 한다.

3. **G1: `rcm wait` JSON 불변과 충돌한다.**

   `rcm wait`는 `GET /jobs/<id>`에서 받은 잡 문서를 그대로 복사해 출력한다([cli.py:625](src/remote_ci_monitor/cli.py:625), [cli.py:662](src/remote_ci_monitor/cli.py:662)). 따라서 종료 문서에 `steps`를 넣으면 새 클라이언트의 wait JSON에도 자동으로 들어간다. 이는 호환 가능한 키 추가지만, “wait 키 불변”과 “실을지는 보류”를 동시에 만족시킬 수는 없다.

4. **G1↔M5h: 재구성한 실패 라벨을 다시 노출하면 안 된다.**

   v16은 `failed_step`이 있으면서 대장 행이 없는 잡을 옛 추론 라벨로 판정한다([store.py:319](src/remote_ci_monitor/store.py:319)). 종료 타임라인을 계산하더라도 재구성된 `progress.failed_step`을 새 최상위 라벨로 싣지 말아야 한다. `steps[].ok`와 지속 시간만 타임라인 사실로 내고, 저장된 `failed_step`·`job_failures` 관계는 그대로 유지해야 한다. 새 코드의 결과와 대장은 같은 트랜잭션에 기록된다([store.py:1893](src/remote_ci_monitor/store.py:1893), [store.py:1910](src/remote_ci_monitor/store.py:1910)).

5. **G2: 슬롯은 현재 고아로도 잡히지 않는다.**

   초안 §6은 “슬롯이 고아 N GB로 나온다”고 했지만, `_scan_ids()`는 이름이 숫자인 항목만 포함한다. `workspaces/gate-a` 같은 슬롯은 총량·고아·예산·바닥·dry-run에서 모두 사라진다([janitor.py:311](src/remote_ci_monitor/janitor.py:311), [janitor.py:435](src/remote_ci_monitor/janitor.py:435)). 반대로 숫자 이름을 쓰면 실제 잡 ID와 충돌한다.

6. **G2: 원격 워커 회계가 빠졌다.**

   서버의 PR2 오프라인 dry-run은 서버의 실제 `data_dir`만 계획한다. 원격 워커 워크스페이스는 자체 7일 mtime 청소만 있고 서버의 charged/reclaimable·예산·바닥·latch 대상이 아니다([remote_worker.py:596](src/remote_ci_monitor/remote_worker.py:596)). 슬롯을 원격 워커에도 줄 것인지, 로컬 전용인지 먼저 정해야 한다.

7. **G2: promote의 충돌·크래시 상태가 빠졌다.**

   성공 캐시를 원자적으로 promote하려면 `staging`, 현재 세대, 이전 세대, 실행 중 lease를 회계해야 한다. promote 직전·직후 크래시에서 어느 디렉터리가 활성·회수 가능·고아인지도 정해야 한다. 현재 `PurgeItem`과 `apply()`는 종료 잡 ID만 재확인하고 삭제한다([janitor.py:513](src/remote_ci_monitor/janitor.py:513)).

8. **G3②는 M5i가 아니라 PLAN 결정 42와 직접 충돌한다.**

   macOS와 Linux의 `used` 정의가 달라 메모리 게이트를 넣지 않기로 이미 확정했다([PLAN.md:635](PLAN.md:635)). 더구나 macOS `used_bytes`에는 compressor가 이미 포함되므로 초안의 “`used_bytes`/`total_bytes` (+ `compressed_bytes`)”는 압축 메모리를 이중 계산한다([hostparse.py:78](src/remote_ci_monitor/core/hostparse.py:78)).

9. **G3①은 heavy 구간 상호배제를 제공하지 않는다.**

   admission은 잡을 claim하기 직전에 한 번 판단한다([server.py:510](src/remote_ci_monitor/server.py:510)). 이미 두 잡이 시작된 뒤 둘 다 heavy 구간으로 진입하는 것은 막지 못한다. `concurrency_group`을 제거하면 서버 쪽 상호배제는 없어지므로, 초안의 “heavy는 머신 락으로 묶인다”가 실제 프리셋의 별도 락을 뜻하지 않는다면 잘못된 보장이다.

10. **G4: 원격 워커 경로가 구조적으로 빠졌다.**

    claim 응답의 preset 문서에는 현재 `argv/env/env_passthrough`만 있고 `requires`가 없다([remote_workers.py:373](src/remote_ci_monitor/remote_workers.py:373)). 또한 원격 finish는 문자열 `summary`만 받고, 그것을 코드 없는 워커 문장으로 처리한다([remote_workers.py:674](src/remote_ci_monitor/remote_workers.py:674), [remote_workers.py:686](src/remote_ci_monitor/remote_workers.py:686)). `tool_missing`을 로컬과 동일한 `summary_code`로 남기려면 claim과 finish 프로토콜 모두 구조화된 키 추가가 필요하다.

11. **G5: 현재 Ctrl-C 의미와 충돌한다.**

    현재 `rcm run`/`wait`의 Ctrl-C는 요청자에게는 detach이고 잡을 취소하지 않는다. 합류자일 때만 `cancel()`을 호출해 join list에서 빠진다([cli.py:634](src/remote_ci_monitor/cli.py:634)). 초안의 “Ctrl-C는 cancel token을 보냄”은 요청자 취소로 읽히므로 PLAN의 detach 계약을 보존한다고 명시해야 한다.

12. **G5: 잡당 v17 열 하나는 합류 모델과 맞지 않는다.**

    `joiners`의 기본키는 `(job_id, name)`이고 동일 토큰 이름의 여러 세션을 구분하지 않는다([store.py:157](src/remote_ci_monitor/store.py:157)). 재제출 횟수는 늘지만 joiner 행은 합쳐진다([store.py:1493](src/remote_ci_monitor/store.py:1493)). 하나의 해시 열을 매 합류 때 덮으면 앞 비밀이 무효화되고, 기존 비밀을 돌려주려 해도 평문은 저장하지 않으므로 불가능하다.

13. **G5↔PR2 마이그레이션은 “충돌 없음”보다 강한 완료 조건이 필요하다.**

    v17은 실제 버전 상승이므로 v16 백업이 DDL보다 먼저 생성돼야 한다. 이 경계 자체는 이미 맞게 구현돼 있다([store.py:627](src/remote_ci_monitor/store.py:627)). 다만 완료 기준에 “v16 DB → v17 시작 전 v16 백업 생성·검증 실패 시 열 추가 없음”을 반드시 넣어야 한다.

## 규칙 위반 여부

| 규칙 | 판정 |
|---|---|
| 서버는 임의 출력을 파싱하지 않는다 | G1은 마커만 읽고 G4는 환경을 검사하므로 준수한다. G3의 새 마커도 프로토콜 확장이므로 그 자체는 임의 출력 파싱이 아니다. 다만 G2의 “툴체인 버전”을 `fvm --version` 같은 출력 파싱으로 자동 산출하면 위반이다. 정적 namespace와 선언된 파일 해시만 써야 한다. |
| fail-open 금지 | G1에서 마커 조회 실패를 `steps: []`로 만들면 위반이다. G3에서 CPU admission이 heavy 상호배제를 보장한다고 문서화하면 위반이다. G4의 셸에서 실행한 `rcm check`가 서비스 환경도 정상이라고 보이게 하면 위반이다. G5가 강제 모드에서도 공유 토큰의 `--force`로 남의 잡을 취소하게 하면 권한 경계가 사실상 열린다. |
| 스키마 v1 키 추가만 | G1의 종료 문서 키, G4의 preset/worker 프로토콜 키, G5의 POST 응답 키는 모두 추가형으로 만들 수 있다. G3에서 기존 `cpu_busy`의 뜻을 메모리까지 넓히면 의미 변경이므로 안 된다. 필요하다면 새 `hold_code`를 추가하고 옛 소비자가 모르는 값을 안전하게 일반 `held_by_load`로 그리는지 잠가야 한다. |
| 옛 클라이언트 유지 | G5 기본 off는 맞다. 새 응답 키를 옛 클라이언트가 무시하므로 제출·취소가 그대로 돈다. 다만 운영자가 강제 옵션을 켠 순간 옛 클라이언트의 취소는 깨진다. 그때는 `/api/health.min_client_version`과 `rcm check`가 **설정의 유효 최소 버전**을 알려야 한다. |
| M5h 대장 불변식 | G4의 `tool_missing`에 `failed_step`과 대장 행을 만들지 않는 판단은 정확하다. `tool_missing`은 서버 생성 outcome 코드이지 스크립트가 선언한 실패 이름이 아니다([worker.py:176](src/remote_ci_monitor/worker.py:176)). G1도 재구성한 `failed_step`을 별도 노출하지 않는 조건에서 안전하다. |
| 비밀·경로 노출 금지 | G4의 `summary_args.path=<PATH>`는 기각해야 한다. `summary_args`는 종료 문서와 공개 가능한 `/api/status.recent`에 실린다([status.py:214](src/remote_ci_monitor/core/status.py:214)). PLAN도 오류 응답에 경로를 싣지 말라고 한다([PLAN.md:231](PLAN.md:231)). G5의 cancel 비밀 역시 “토큰만큼 민감하지 않다”가 아니라 실제 취소 권한을 가진 비밀로 취급해야 한다. |

## 권고 대안

### G1

종료 `GET /jobs/<id>`에 평평한 `steps` 여러 키보다 다음과 같은 단일 추가 객체를 권한다.

- `step_timeline`: `timing`, `steps_total`, `steps_total_partial`, `steps[]`.
- 마커 조회 성공·마커 없음: `steps: []`, `steps_total: null`.
- DB 조회 실패: `step_timeline: null` + `step_timeline_error_code`.
- 종료 전 실패로 `started_at`이 없는 잡: 성공적으로 조회한 빈 타임라인.
- 재구성한 `failed_step`은 싣지 않고 저장된 `failed_step`·`last_step`을 그대로 유지.
- `rcm wait` 최종 JSON에도 이 추가 키가 들어가는 것을 정식 계약으로 인정.
- 계산 결과를 `_with_failures()`에도 넘겨 `failures[].step` 판정에는 모든 스텝 이름을 사용한다. 현재는 `failed_step`과 `last_step` 두 이름만 본다([server.py:1207](src/remote_ci_monitor/server.py:1207)).

### G2

“고정 실행 슬롯”보다 **잡별 워크스페이스 + 별도 캐시 세대**가 기존 불변식을 덜 흔든다.

- 실행은 계속 `workspaces/<job_id>`에서 한다. 실패 워크스페이스 증거와 janitor 규칙을 보존한다.
- 캐시는 `cache/<preset>/<pool-or-worker>/<key>/generations/...`처럼 별도 종류로 둔다.
- 시작할 때 허용된 `cache_paths`만 잡 워크스페이스로 복원하고, 성공 뒤 staging에 복사한 다음 rename으로 promote한다.
- 키는 명시적 `cache_namespace`와 `cache_key_files`의 내용 해시로 만든다. 도구 출력은 파싱하지 않는다.
- 경로는 상대 경로만, `..`·절대경로·`.git`·워크스페이스 밖 symlink를 거부한다.
- 회계 모델에 `kind=workspace|snapshot|cache|cache_staging`, 안정된 문자열 ID, `active/idle`, `last_used_at`을 추가한다.
- idle cache는 증거가 아니므로 예산·바닥에서 회수 가능하게 하고, 실행 중 lease와 promote staging은 보호한다.
- charged/shared/reclaimable을 동일하게 측정하고, 오프라인 dry-run도 같은 planner로 실제 cache 루트를 포함한다.
- 원격 워커 지원을 별도 결정한다. 지원한다면 서버 dry-run으로는 볼 수 없으므로 워커 자체 회계·GC·보고가 필요하다.

### G3

- 실제 게이트 스크립트가 heavy 구간을 OS 락으로 이미 직렬화한다면 `lanes=2 + admission=load + concurrency_group 제거`를 운영 실험으로 허용한다.
- 그런 락이 없다면 `concurrency_group`을 유지한다. CPU admission은 잡 시작 제어일 뿐 구간 락이 아니다.
- M5j에서는 메모리 admission을 넣지 않는다. G1 스텝 시간과 `concurrent_at_start` 표본으로 두 레인 효과부터 본다.
- 향후 구간 상호배제가 필요하면 stdout의 `exclusive::begin`만으로 구현하지 않는다. 마커는 버퍼링될 수 있어 heavy 명령이 먼저 시작될 수 있다([PLAN.md:183](PLAN.md:183)). 스크립트가 서버의 승인 응답을 받은 뒤에만 진입하는 lease/helper나, 프리셋 내부의 권위 있는 락이 필요하다. 따라서 ③ 보류는 정확하다.

### G4

- `requires`는 이름 또는 절대경로만 허용하고 상대 경로·빈 값·중복은 설정 오류로 막는다.
- `build_env()`로 만든 **정확한 최종 환경**을 사용한다. 현재 잡 환경은 전체 `os.environ`이 아니라 `env_passthrough` allowlist → preset env → RCM/input 순이다([runner.py:93](src/remote_ci_monitor/runner.py:93)).
- PATH가 없을 때 `shutil.which`가 검사 프로세스의 PATH로 물러나지 않도록 빈 PATH를 명시한다.
- 공개 결과는 `summary_code="tool_missing"`, `summary_args={"tool": "fvm"}`까지만 둔다. PATH와 절대 도구 경로는 상태·오류에 싣지 않는다.
- 잡 로그에도 전체 PATH는 찍지 말고 `required tool fvm: missing`처럼 선언된 이름과 판정만 남긴다.
- `rcm check --config` 행은 `local preset tools`라고 명명한다. 셸 환경 검사는 launchd 서비스의 정본 검사가 아니다.
- 원격 claim에 `requires`를 추가하고, 원격 finish에도 검증된 `summary_code/summary_args` 또는 구조화된 preflight failure를 추가한다. 문자열 summary로 우회하지 않는다.
- 누락 도구 실패에는 `failed_step=None`, `last_step=None`, 대장 행 0개를 유지한다.

### G5

v17은 `jobs.cancel_token_hash` 한 열이 아니라 **제출 참여자별 capability 표**로 설계해야 한다.

- 각 `POST /jobs` 시도마다 `submission_id`와 서로 다른 cancel 비밀을 한 번 발급한다.
- 서버에는 해시만 저장한다.
- 최초 요청자 capability는 `cancel_job`, 합류 capability는 `leave_submission` 권한만 가진다.
- 동일한 인증 토큰 이름으로 여러 세션이 제출해도 서로 다른 참여자로 남는다.
- 강제 모드에서는 capability 또는 admin만 허용한다. 비-admin `--force`로 옛 공유 토큰 권한을 되살리지 않는다.
- Ctrl-C는 기존 계약대로 요청자는 detach, 합류자는 자기 participation만 제거한다. 요청자 잡 취소는 명시적 `rcm cancel`만 한다.
- `rcm cancel`이 나중에도 작동하도록 새 클라이언트는 capability를 권한 0600의 로컬 상태에 보관하거나 명시적 비밀 입력 경로를 제공한다. 상태·로그·오류·URL에는 절대 싣지 않는다.
- `cancel_requires_submission_token=false`는 v0.2.x 호환을 위해 유지한다. `true`일 때는 health/check가 유효 최소 클라이언트 버전을 알려야 한다.
- 메타데이터 삭제 시 participation/capability 행도 같은 트랜잭션에서 삭제한다.
- v16→v17 테스트에는 M5i 경계 백업의 생성·검증·실패 중단을 포함한다.

결론적으로, 초안의 세 가지 주요 판단 중 **G3③ 보류와 G2 별도 명세는 맞고, G5 기본 off도 호환성 관점에서는 맞다.** 다만 G2 충돌 범위와 G5 권한 모델은 현재 초안보다 크게 다시 써야 하며, G3②는 이미 확정된 PLAN 결정 42에 반하므로 이번 범위에서 제외해야 한다. 코드와 문서는 변경하지 않았다.
