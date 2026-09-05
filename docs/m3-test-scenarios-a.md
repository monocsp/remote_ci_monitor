# M3 테스트 시나리오 A — git_ref 순수 규칙 · gitops · 보존 규칙 · 워커 자재화 (2026-09-05)

`docs/m3-workplan.md` §1.2 · §1.3 · §1.4(워커) · §2.1 · §6 · §7 을 테스트로 옮긴 것이다. 구현보다 먼저 썼다
(test-first). `src/` 는 건드리지 않았다. 원격을 부르는 테스트는 없다 — 모든 URL 은 pytest tmp 안의 bare 레포
**파일 경로**다. `git` 이 PATH 에 없으면 I/O 두 파일은 모듈 전체 skip.

| 파일 | 대상 | 테스트 함수 / 수집 건수 |
|---|---|---|
| `tests/test_gitref.py` | `core/gitref.py` — `validate_ref` · `is_full_sha` · `pick_sha` · `short_sha` | 17 / 61 |
| `tests/test_retention.py` | `core/retention.py` — `RetentionPolicy` · `retention_seconds` · `due_for_purge` | 13 / 27 |
| `tests/test_gitops.py` | `gitops.py` — `resolve_ref` · `ensure_mirror` · `fetch_ref` · `has_commit` · `checkout` · `GitError` | 19 / 21 |
| `tests/test_worker_gitref.py` | `worker.py` + `materialize.prepare_git_ref` — git_ref 잡 끝까지 | 8 / 8 |
| `tests/gitrepo.py` | 공용 헬퍼(테스트 아님) — bare 원격 레포 · 격리 git 환경 · 멈추는 가짜 git | — |

공용 헬퍼 `tests/gitrepo.py` 는 `jobfactory.py` 와 같은 자리의 모듈이다. `build_remote(tmp)` 가 만드는 모양:

```
main : c1(hello.txt) → c2(second.txt) → c3(third.txt)     annotated 태그 v1.0.0 = c3 (태그 객체 sha 는 따로 기록)
dev  : c3 → c4(dev.txt)                                    lightweight 태그 lw = c2
```

`work/` 는 `origin → remote.git` 인 작업 클론이라 `push_commit()` · `rewind_main()` + `force=True` 로 ref 이동과
강제 push 를 만든다. 헬퍼의 git 호출은 `GIT_CONFIG_GLOBAL=/dev/null` · `GIT_CONFIG_NOSYSTEM=1` · `-c user.* ·
commit.gpgsign=false · tag.gpgsign=false · init.defaultBranch=main` 으로 개발자 환경을 격리한다. gitops 가 넘기는
`HOME` 도 `isolate_git_env()` 가 빈 디렉터리로 바꾼다(`~/.gitconfig` 의 hooksPath · insteadOf 가 끼어들지 않게).

## 1. `tests/test_gitref.py` — 순수 규칙

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `MAX_REF_LEN` | 200 |
| 2 | 통과(8건) | `main` · `feature/x-1` · `refs/heads/main` · `refs/tags/v1.2.3` · `v1.2.3` · `release_2026.09` · `HEAD` · 200자 → 입력 그대로 |
| 3 | 대소문자 보존 | `Feature/X` 는 그대로(브랜치 이름은 대소문자 구분). 40 hex 만 소문자로 정규화 |
| 4 | sha 정규화 | 대문자 40 hex → 소문자. 소문자는 그대로 |
| 5 | 거부(21건, `ValueError`, 사유 한 줄) | 빈 문자열 · `..` · `@{` · `\` · `^` · `:` · `?` · `*` · `[` · `~` · 공백(가운데·앞·뒤) · 탭 · 개행 · `\x01` · `\x7f`(DEL) · `/` 시작 · `/` 끝 · `.lock` 끝 · 201자 |
| 6 | **argv 주입 가드**(5건) `test_validate_ref_guards_argv_injection_leading_dash` | `-x` · `--upload-pack=x` · `-` · `--` · `--output=x` — 다른 금지 문자 없이 `-` 규칙만 어기는 값들이라 mutcheck ⑧ 이 정확히 이 테스트로 빨개진다 |
| 7 | `is_full_sha`(9건) | 소문자 40 hex 만 True. **대문자 · 39자 · 41자 · `g` 포함 · 7자 · `main` · 빈 문자열은 False** |
| 8 | `pick_sha` 우선순위 ① | 같은 이름 `v1` 의 head · tag · tag^{} 가 다 있으면 head. 태그 줄이 먼저 와도(출력 순서 무관) |
| 9 | ② | `refs/tags/v1^{}` 가 `refs/tags/v1` 을 이긴다 |
| 10 | ③ | lightweight 태그만 있으면 그 sha |
| 11 | ④ 완전한 refname | `refs/heads/main` · `refs/tags/main` 각각 정확히 그 줄 |
| 12 | ④ + peeled | `refs/tags/v1` 을 줬는데 `^{}` 변형이 있으면 그것 |
| 13 | `HEAD` | `<sha>\tHEAD` 한 줄 → 그 sha(원격 기본 브랜치) |
| 14 | 무관한 줄(6건) → None | 빈 출력 · `refs/heads/dev` · `refs/heads/main2` · `refs/heads/feature/main` · `refs/remotes/origin/main` · `refs/heads/mai` |
| 15 | 40 hex | 출력이 비어 있어도, 다른 sha 줄이 있어도 입력 sha 그대로 |
| 16 | 출력 모양 | 앞뒤 빈 줄 · 마지막 개행 없음 을 견딘다 |
| 17 | `short_sha` | 앞 7자 · None → `—` · 7자보다 짧으면 그대로 |

## 2. `tests/test_retention.py` — 순수 규칙

잡은 `jobfactory.job()` 으로 만들고 `dataclasses.replace` 로 `finished_at`·`created_at`·`artifacts_purged_at` 을
고정 `NOW` 기준으로 놓는다(`finished(id, state, age=…)` 헬퍼). 정책은 `RetentionPolicy(14, 30)`.

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `retention_seconds`(9건) | succeeded → 14·86400 · failed/timed_out/cancelled/lost → 30·86400 · uploading/queued/running/cancelling → None |
| 2 | 필드 순서 | `RetentionPolicy(14, 30) == RetentionPolicy(success_days=14, failure_days=30)` |
| 3 | 경계 `>=` | 정확히 14일 → 대상. 돌려주는 것은 **같은 Job 객체** |
| 4 | 1초 전 | 14일 − 1초 → 대상 아님 |
| 5 | 실패 상태는 실패 기준(4건) | 20일 → 아님, 30일 → 대상 |
| 6 | days 0 | 끝난 직후(age 0) 성공·실패 모두 대상 |
| 7 | 이미 지운 잡 | `artifacts_purged_at` 있으면 제외 |
| 8 | **활성 보호**(4건) `test_active_states_are_never_due_even_with_bogus_finished_at` | `finished_at` 이 400일 전으로 찍혀 있어도, 정책이 0일이어도 제외 — mutcheck ⑦ 의 표적 |
| 9 | `finished_at` None 인 종료 잡 | `created_at` 기준(14일 → 대상, 13일 → 아님) |
| 10 | 정렬 | 섞어 넣어도 `finished_at` 오름차순, 아직 안 된 잡은 빠진다 |
| 11 | 빈 입력 | `[]` |
| 12 | 임의 iterable | 제너레이터도 받는다 |
| 13 | `now` 가 기준 | 벽시계가 아니라 넘긴 `now` 로 판정 |

## 3. `tests/test_gitops.py` — I/O

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | resolve(3건) | `main` · `dev` · `lw`(lightweight) → 커밋 sha |
| 2 | resolve annotated 태그 | `v1.0.0` → **커밋** sha(`git rev-parse v1.0.0^{commit}` 과 같고 태그 객체 sha 와 다르다) |
| 3 | resolve 완전한 refname | `refs/heads/dev` → dev · `refs/tags/v1.0.0` → 커밋 |
| 4 | resolve 40 hex | `run` 스텁이 호출되면 실패 — git 을 부르지 않고 그대로 돌려준다 |
| 5 | 없는 ref | `GitError`, 문구에 tmp 경로 · URL 없음 |
| 6 | 없는 원격 | `GitError`, 문구에 경로 · `missing.git` 없음 |
| 7 | 타임아웃 | `run` 스텁이 `TimeoutExpired` → `GitError`, 문구에 `timed out` |
| 8 | argv · env | `git … ls-remote … -- <url> <ref>[ <ref>^{}]` — `--` 바로 뒤가 url, 그 뒤엔 ref 패턴만 · `GIT_TERMINAL_PROMPT=0` · `LC_ALL=C` · `PATH` 있음 · `stdin=DEVNULL` · `timeout=20` · `start_new_session=True` · `shell` 아님. 스텁은 `text` 여부에 맞춰 str/bytes 를 돌려준다 |
| 9 | ensure_mirror | 부모 없는 경로에 bare 를 만든다(`HEAD` 파일 · `--is-bare-repository`). 두 번째 호출은 받아 둔 객체를 지우지 않는다 |
| 10 | fetch → has_commit | fetch 전엔 False, 후엔 True · 미러의 `refs/heads/main` 이 그 sha · 없는 sha 는 False · `v1.0.0` fetch 뒤 태그 커밋 존재 |
| 11 | **미러 전체 갱신**(스펙 §1.3 문면) | `main` 만 fetch 해도 dev · lw · v1.0.0 커밋이 미러에 있다 |
| 12 | **로그 흔적** | 성공한 fetch 도 `log` 에 ref 이름이 들어간 줄을 남긴다 |
| 13 | 없는 원격 fetch | `git fetch failed` 로 시작 · 경로 없음 · git 의 `fatal:` 줄은 `log` 에 간다 |
| 14 | **fetch 타임아웃**(가짜 git) | PATH 앞에 `fetch` 면 `exec sleep 30` 하는 가짜 git. `timeout=0.5` → 5초 안에 `GitError(timed out)` · **fetch 호출이 정확히 1번**(타임아웃을 다른 refspec 으로 재시도하면 실제 상한이 N배가 된다) |
| 15 | checkout | main sha → hello/second/third 있고 dev.txt 없음 · `rev-parse HEAD` == sha · detached · `.git` 존재. lw sha → third.txt 없음 |
| 16 | 없는 sha checkout | `GitError`, 경로 없음, 워크스페이스에 파일 없음 |
| 17 | ref 이동 | resolve → fetch → 새 커밋 push → resolve 는 새 sha, fetch 뒤 옛 sha 도 있고 옛 sha 로 checkout 하면 new.txt 없음 |
| 18 | 강제 push | 되감고 다른 커밋을 `--force` → resolve 는 새 sha. 옛 커밋 존재 여부는 단정하지 않는다(객체가 남을 수 있다). 애초에 없던 sha 는 checkout 실패 + 경로 없음 |
| 19 | 문구 누출 | `GitError` 에 tmp 경로 · URL · `$HOME` 이 없다 |

## 4. `tests/test_worker_gitref.py` — 워커 끝까지

설정은 `ServerConfig()` 직접 조립: `repos=(RepoConfig("app", <bare 경로>),)` · `Preset(name="deploy",
source_modes=("git_ref",), repo="app", argv=["sh","-c",…])` · `git_fetch_timeout_seconds=30`. 잡은
`store.create_job(source=Source(mode="git_ref", repo="app", ref, sha, base_sha=sha, dirty=False), state="queued")`
— 서버의 git_ref 분기가 만드는 모양 그대로(트리 업로드 없음). 스크립트는 `hello.txt` · `RCM_REF` · `RCM_BASE_SHA`
· `RCM_DIRTY` · `RCM_SOURCE_MODE` · `git rev-parse HEAD` · `ls` 를 찍는다.

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 성공 | succeeded · exit 0 · phase/lane None · 전이 queued→running→succeeded · 로그에 파일 내용 · `RCM_REF=main` · `RCM_BASE_SHA=<sha>` · `RCM_DIRTY=0` · `RCM_SOURCE_MODE=git_ref` · `HEAD=<sha>` · **순서** `[rcm] fetching main from app` < `[rcm] checked out <sha7>` < 스크립트 출력 · 워크스페이스 삭제됨 · `<data_dir>/mirrors/app` 이 bare |
| 2 | 태그 | `lw`(c2) → `HEAD=<c2>` · `FILES=hello.txt second.txt`(third.txt 없음) · `[rcm] fetching lw from app` |
| 3 | 미러 재사용 | 잡 두 개(main · dev) 연달아 — 둘 다 성공, 미러는 남는다, 두 번째 워크스페이스도 삭제 |
| 4 | phase 순서 | `on_change` 콜백 스냅샷에 `(running, materializing)` 이 `(running, executing)` 보다 먼저, 마지막은 `(succeeded, None)` |
| 5 | repo 소멸 | 잡 생성 뒤 `cfg.repos = ()` → failed · exit None · summary `repo 'app' is no longer configured` · 워크스페이스 없음 |
| 6 | sha 없음 | 가짜 40 hex → failed · summary 에 `not found after fetch` 와 sha7 · 경로·URL 없음 · 로그에 fetching 줄 |
| 7 | 원격 불가 | `gone.git` → failed · summary 는 `git fetch failed` 로 시작, URL·경로·`gone.git` 없음 · 로그엔 `fatal` |
| 8 | fetch 타임아웃 | `git_fetch_timeout_seconds=1` + 멈추는 가짜 git → 10초 안에 failed · exit None · summary **`git fetch timed out after 1s`** · 워크스페이스 없음 |

실제 git 을 쓰는 테스트는 전부 0.3초 안팎, 타임아웃 테스트 둘만 1.5초 · 3.5초(아래 §6-4 참고).

## 5. 가정 (스펙이 애매한 곳)

1. **`HEAD` 는 유효한 ref 다.** `validate_ref` 규칙을 다 통과하고 `git ls-remote -- <url> HEAD` 가 `<sha>\tHEAD`
   를 주므로 ④(정확히 같은 refname) 로 원격 기본 브랜치를 가리킨다고 봤다. 막고 싶으면 §1.2 에 명시해야 한다.
2. **DEL(`\x7f`) 도 제어문자다.** `git check-ref-format` 이 거부한다. `ord < 0x20` 만 보는 구현이면 빨개진다.
3. **`is_full_sha` 는 소문자만 True.** 스펙 문면(「40 자리 소문자 hex」) 그대로. 대문자는 `validate_ref` 가 먼저
   정규화하므로 이후 단계에서 대문자를 True 로 볼 이유가 없다는 해석. 현재 구현은 대소문자 무관 → 1건 빨강
   (§6-1).
4. **`short_sha("abc") == "abc"`**, 빈 문자열의 결과는 단정하지 않았다(`—` 인지 `""` 인지 스펙 없음).
5. **`resolve_ref` 의 타임아웃 문구는 `timed out` 만 확인.** `git fetch timed out after Ns` 는 fetch 용 문구라
   ls-remote 는 `git ls-remote timed out after 20s` 같은 변형을 허용했다. 워커 쪽(summary) 은 스펙 문구 그대로
   `git fetch timed out after 1s` 를 정확히 본다 — `600 → "10m"` 처럼 h/m/s 단위 표기라고 가정.
6. **`run` 스텁 계약**: `run(argv, **kwargs)` 로 부르고 `argv[0]` 은 `git`(절대 경로여도 basename 이 git),
   `kwargs` 에 `env`·`stdin`·`timeout`·`start_new_session` 이 온다. `text=` 가 없으면 bytes 를 돌려준다.
7. **fetch 타임아웃·워커 타임아웃 테스트는 gitops 가 `argv[0]="git"` 을 PATH 로 찾는다는 전제**다(스펙 §1.3 의
   env 에 `PATH`). import 시점에 `shutil.which("git")` 을 굳혀 두는 구현이면 가짜 git 이 안 잡혀 두 테스트가
   「타임아웃이 안 났다」로 빨개진다 — 그때는 gitops 에 git 실행 파일 주입점을 두는 편이 맞다.
8. **`due_for_purge` 는 같은 Job 객체를 돌려준다**(복사 아님) — janitor 가 `job.is_terminal` 을 한 번 더 볼 때
   같은 객체여야 자연스럽다.
9. **워커 실패 시 워크스페이스는 없다**(자재화 실패 = 워크스페이스를 만들다 만 것이므로 지운다). 스펙엔 없지만
   `keep_workspace_on_failure` 는 「실행 실패」의 보존이지 fetch 실패의 잔해 보존이 아니라고 봤다.
10. `ensure_mirror` 는 `mirrors/` 부모가 없어도 만든다(경로를 부모 없는 곳으로 줬다).
11. `checkout` 은 워크스페이스에 `.git` 을 남긴다(진짜 clone) — 프리셋이 `git submodule update --init` ·
    `git describe` 를 쓸 수 있어야 한다는 스펙 §1.3 의 README 문구에서 유도.

## 6. 스펙이 틀렸거나 빠진 것 (구현과 맞춰 본 결과 포함)

작성 중 `src/` 에 구현이 병행 착지해 네 파일을 돌려 봤다(총 117건 중 **111 통과 · 6 빨강**). 빨강은 전부 아래
스펙-구현 불일치 하나씩에 대응한다. 테스트는 스펙을 따랐고, 결정은 오너 몫이다.

1. **`is_full_sha` 대문자** — 스펙 §1.2 「40 자리 소문자 hex」 vs 구현은 대소문자 무관(`[0-9a-fA-F]{40}`).
   빨강 1건(`test_is_full_sha[…ABCDEF…-False]`). 구현 쪽이 더 관대하고 해롭지 않다 — 스펙을 「40 hex(대소문자
   무관), 정규화는 validate_ref」로 고치고 그 케이스를 `True` 로 뒤집는 것을 권한다.
2. **annotated 태그가 태그 객체 sha 로 확정된다(스펙 버그).** §1.2 는 `git ls-remote -- <url> <ref>` 출력에
   `refs/tags/<ref>^{}` 줄이 있다고 전제하지만, **패턴을 주면 peeled 줄은 안 나온다**(git 2.54 실측: 패턴 `v1.0.0`
   은 `refs/tags/v1.0.0` 만 출력. `^{}` 줄은 패턴 없는 전체 목록에만). ls-remote 는 패턴을 `*/<pattern>` 으로
   refname 꼬리에 맞추므로 `refs/tags/v1.0.0^{}` 는 `v1.0.0` 에 안 맞는다. 결과: `resolve_ref(url, "v1.0.0")` 이
   **태그 객체 sha** 를 돌려주고, `has_commit`(`^{commit}` peel) 과 checkout 은 그걸로도 되니 잡은 「성공」하지만
   `source.sha`·`RCM_BASE_SHA`·합류 키·`checked out <sha7>` 이 전부 커밋이 아닌 태그 객체다. 빨강 2건(§3-2 ·
   §3-3). **고침**: `git ls-remote -- <url> <ref> '<ref>^{}'` 처럼 패턴을 둘 주면 peeled 줄이 같이 온다
   (`refs/tags/v1.0.0` 완전한 이름도 `refs/tags/v1.0.0^{}` 가 꼬리 매치). §3-8 argv 테스트는 이 모양을 허용한다.
3. **`fetch_ref` 가 미러 전체를 갱신하지 않는다(설계 변경).** 스펙 §1.3 은 `+refs/heads/*:refs/heads/*
   +refs/tags/*:refs/tags/*` 로 전체 갱신, 구현은 「ref 하나(heads → tags 순) 먼저, `want_sha` 가 안 오면 전체」.
   빨강 1건(§3-11). 구현의 접근이 대형 레포에 유리하고 「옛 sha 를 못 받는다」는 스펙의 우려도 fallback 이
   덮으므로(§3-17 · §4-1 통과) **스펙을 구현 쪽으로 고치고 §3-11 테스트를 지우는 것**을 권한다. 단 아래 4 · 5 는
   그 설계의 부작용이라 같이 봐야 한다.
4. **타임아웃을 refspec 마다 다시 시도한다(구현 버그).** 타깃 fetch 루프가 `GitError` 를 통째로 삼키므로 멈춘
   원격에서는 heads 시도 → tags 시도 → 전체 fetch 가 각각 `git_fetch_timeout_seconds` 를 다 쓴다. 실측: gitops
   0.5초 상한에 fetch 2회(§3-14 빨강, `assert 2 == 1`), 워커 1초 상한에 잡이 3.5초 뒤 실패(§4-8 은 `< 10` 이라
   통과). 「couldn't find remote ref」 만 다음 후보로 넘기고 타임아웃·그 밖의 실패는 즉시 올려야 한다.
5. **태그 잡의 로그에 `fatal:` 이 찍힌다(구현 부작용).** heads 후보를 먼저 시도하므로 `lw` 잡 로그에
   `[git] fatal: couldn't find remote ref refs/heads/lw` 가 남는다(§4-2 는 성공하지만 로그를 읽는 사람이 놀란다).
   `resolve_ref` 시점에 어느 refname 이 맞았는지(`refs/heads/…` 인지 `refs/tags/…` 인지) 알 수 있으니 그 완전한
   이름을 `Source.ref` 나 별도 필드로 넘겨 한 번에 맞는 refspec 을 쓰는 편이 낫다. 아니면 후보 시도의 stderr 는
   로그에 남기지 않는다.
6. **성공한 fetch 가 로그에 흔적을 안 남긴다.** 구현이 `fetch -q` 라 stderr 가 비고 `log` 는 아무것도 못 받는다.
   빨강 1건(§3-12). 스펙 §1.3 「git 의 stderr 마지막 20줄을 잡 로그에」의 취지는 「무엇을 받았는지 잡 로그가
   유일한 기록」이다 — `-q` 를 빼면 git 이 ` * [new branch] main -> main` · `(forced update)` 같은 줄을 주고 그것이
   ref 이동·강제 push 를 사후에 확인하는 유일한 증거다. `-q` 를 빼는 것을 권한다(clone/checkout 의 `-q` 는 무방).
7. **§1.3 `git checkout --detach -- <sha>` 는 틀린 문법.** `--` 뒤는 pathspec 이라 sha 가 경로로 읽혀
   `pathspec 'abc…' did not match` 로 실패한다. sha 는 40 hex 로 검증돼 `-` 로 시작할 수 없으니 `--` 없이
   `git checkout --detach <sha>` 가 맞다(구현은 이미 그렇게 한다). 스펙 문구만 고치면 된다.
8. **§1.3 fetch 문구와 §1.2 `pick_sha` 의 `HEAD`**: `validate_ref("HEAD")` 가 통과하고 ④ 로 원격 기본 브랜치에
   합류한다(§5-1). 의도한 기능이면 CLI 도움말에 「`--ref HEAD` = 원격 기본 브랜치」를 적고, 아니면 금지 목록에
   `HEAD` 를 넣어야 한다.
9. **`fetch_ref` 시그니처에 `want_sha` 가 생겼다**(구현). 스펙 §1.3 시그니처에는 없다. 3 을 받아들이면 스펙의
   시그니처도 `fetch_ref(mirror, url, ref, *, timeout, log, want_sha=None)` 로 갱신해야 한다. 테스트는 키워드
   없이 불러 두 모양 다 통과한다.
10. **워커 실패 시 워크스페이스 처리**가 스펙에 없다(§5-9). 구현은 `GitError` 에서 `rmtree` 한다 — 스펙 §1.4 에
    한 줄 적는 것을 권한다.
11. **`ensure_mirror` 가 `gc.auto=0` 을 건다**(구현). `--shared` clone 이 미러 객체를 가리키므로 미러 gc 가
    워크스페이스를 깨뜨릴 수 있어 타당하다 — 스펙 §1.3 에 적어 두는 것이 좋다(janitor 가 `mirrors/` 를 안 지우는
    것과 같은 이유).
