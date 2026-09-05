# Codex 크로스리뷰 — M3 운영 명세 (2026-09-05)

- 대상: `docs/m3-workplan.md`(git_ref · 보존 정리 · 신호 e2e · 서비스 파일 · `read_auth = basic`)
- 실행: `codex exec --sandbox read-only`(gpt-5.5). 프롬프트는 설계·보안 질문 A~E.
- 반영: 「반드시 고칠 것」 10건 전부 — ① 제출 시 sha 확정 유지 + `BoundedSemaphore(2)` ② 미러별 프로세스 내 Lock ③ `--shared` 폐기 → 로컬 clone(하드링크) + 미러 `gc.auto=0` ④ ref 하나 먼저 fetch, 전체 fetch 는 폴백 ⑤ `checkout --detach <sha>`(`--` 없음) ⑥ `[[repos]].url` 허용 목록(`https://` · `ssh://` · `git://` · `file://` · scp 형 · 절대 경로) ⑦ Basic 은 읽기 라우트만, 쓰기는 Bearer 만 ⑧ `metadata_retention_days`(180) + `store.delete_old_jobs` ⑨ janitor 의 symlink · data_dir 밖 방어 ⑩ `Janitor` 객체 + `/api/health` 503(dead · stale). 「고치면 좋은 것」 8건도 반영(launchd 절대 경로 · systemd `ProtectSystem=strict`+`ReadWritePaths` · README 로그아웃 안내 등).
- 오너에게: (1) 배포 프리셋이 워크스페이스의 `.git` 을 쓰는가 — clone 방식이라 `.git`·`git describe` 는 되고 submodule 은 스크립트 몫(결정 24) (2) 잡 메타데이터 보존 일수 — 180일 기본(결정 25).

---

결론: M3 방향은 맞지만, 그대로 구현하면 가장 큰 위험은 `clone --shared` + 미러 prune/gc, Basic 인증을 쓰기까지 허용하는 CSRF 면, 그리고 janitor/DB 무한 성장이다. `git_ref`의 sha 확정은 제출 시점이 맞다. 다만 `ls-remote`만으로 커밋을 “핀”하지는 못하므로 워커가 해당 sha를 못 찾으면 실패로 닫는 계약을 명확히 두고, 미러 접근은 반드시 repo별 직렬화해야 한다.

**반드시 고칠 것**

1. §1.4 `git_ref` 확정 · 제출 시 확정 유지 · `POST /jobs`에서 `validate_ref` 후 DB 락 밖에서 `resolve_ref`를 돌리고 sha로 `join_key`를 만들라. 자재화 시 확정은 합류·감사·응답 `sha`가 늦게 정해져서 틀린 설계다. 대신 `_git_resolve_sem = BoundedSemaphore(2)` 같은 별도 제한을 둬서 핸들러 32개가 20초씩 원격 호출에 묶이지 않게 하라.

2. §1.3 미러 동시성 · repo별 lock 추가 · `<data_dir>/mirrors/<repo>.lock` 또는 프로세스 내부 `Lock`으로 `ensure_mirror/fetch/worktree-or-clone` 전체를 직렬화하라. 같은 bare mirror에 fetch 두 개가 동시에 들어가면 lock 실패/부분 갱신/불필요한 128이 난다.

3. §1.3 checkout · `clone --shared` 폐기 · 기본은 `git clone --no-local --no-checkout -- <mirror> <workspace>` 후 `git -C <workspace> checkout --detach <sha>`로 바꾸라. `--shared` alternates는 mirror가 gc/prune할 때 워크스페이스 객체가 사라질 수 있다. 속도가 필요하면 `--reference-if-able <mirror> --dissociate`까지만 허용하라.

4. §1.3 fetch 전략 · 전체 fetch를 기본으로 두지 말 것 · `resolve_ref`가 `sha`뿐 아니라 매칭된 `refname`도 돌려주게 하고, 먼저 `git fetch --no-tags -- <url> +<refname>:<refname>`를 시도하라. 실패 또는 tag/peel 애매할 때만 전체 `+refs/heads/*`·`+refs/tags/*` fetch로 fallback하라. 대형 레포에서 매 잡 전체 fetch는 운영 비용이 크다.

5. §1.2/1.3 git argv · checkout 명령 수정 · `git checkout --detach -- <sha>` 형태는 `--` 뒤가 pathspec으로 해석될 수 있다. sha는 40 hex로 검증된 값이므로 `git -C <workspace> checkout --detach <sha>` 또는 `git switch --detach <sha>`를 써라.

6. §1.1/1.2 URL·ref 검증 · 더 조일 것 · `[[repos]].url`은 빈 값/`-` 시작 외에 `ext::`, 개행/제어문자, `--upload-pack=`, `-c`류 값을 거부하라. 허용 목록은 `https://`, `ssh://`, scp-like `user@host:path`, 테스트용 절대 로컬 경로 정도로 제한하라. ref 규칙은 현재 `core/gitref.py`처럼 `//`, component leading `.`, trailing `.`, 단독 `@`까지 문서에 반영하라.

7. §5 Basic 인증 · 쓰기 Basic 금지 · `authenticate_read()`는 Bearer 또는 Basic을 받고, `require_token()`/쓰기 라우트는 Bearer만 받게 분리하라. Basic은 브라우저가 자동 첨부하므로 `/jobs`, `/cancel`, `/pause`, `/resume`에 허용하면 내부망 CSRF로 잡 실행/취소가 가능해진다.

8. §2 janitor DB · 행 영구 보존 금지 · `artifacts_purged_at`은 추가하되, 별도로 `[server] metadata_retention_days = 180` 같은 키와 `store.delete_old_jobs(cutoff)`를 추가하라. `jobs`, `joiners`, `events`를 함께 지우고, 중앙값에 필요한 `sample_days`보다 짧게 설정하면 ConfigError를 내라.

9. §2 janitor 삭제 안전 · symlink 방어 추가 · 삭제 전 `lstat()`으로 `<data_dir>/jobs/<id>`와 `<data_dir>/workspaces/<id>`가 symlink면 `unlink()`만 하고 `rmtree()`를 호출하지 말라. 디렉터리면 `resolve()`가 data_dir 하위인지 확인하고 지워라. 프리셋 스크립트가 workspace를 symlink로 바꿀 수 있다.

10. §2 health · janitor 죽음 표면화 · 현재 `App.health()`는 worker만 본다. M3에서는 raw thread 대신 `Janitor` 객체를 보관하고, `not janitor.is_alive()` 또는 `last_sweep_at > 2 * interval`이면 `/api/health`를 503 `janitor thread dead` 또는 `janitor stale`로 내려라.

**고치면 좋은 것**

1. §2 마이그레이션 · 파일 존재 검사보다 컬럼 우선 · `list_unpurged_finished`는 `artifacts_purged_at IS NULL` 기준으로 하고, 파일이 이미 없으면 성공으로 간주해 `mark_artifacts_purged`하라. 수동 삭제 뒤 매 sweep마다 같은 잡을 재시도하지 않게 하라.

2. §2 활성 보호 · DB 업데이트에도 조건 추가 · `mark_artifacts_purged(job_ids)`는 `WHERE state IN terminal AND artifacts_purged_at IS NULL` 조건을 넣어라. 순수 규칙 + janitor 재확인 + DB 조건까지 3중 가드가 된다.

3. §2 시계 역행 · 미래 finished_at 처리 · `now < finished_at`이면 purge 제외하고 `last_error`에는 올리지 말라. wall clock 보정 중 정상적으로 생길 수 있다.

4. §3 e2e 신호 · shell/date 의존 줄이기 · 시작/끝 시각은 `date +%s.%N` 대신 `python3 -c 'import time; print(time.time())'`만 써라. macOS `date`는 `%N`이 portable하지 않다.

5. §3 grandchild kill 테스트 · zombie race 처리 · `os.kill(pid, 0)` 단독 판정은 flaky하다. 5초 poll로 `/bin/ps -o stat= -p <pid>`를 보고 없거나 `Z`면 통과로 처리하라.

6. §4 launchd · `~` 제거 · `StandardOutPath`/`StandardErrorPath`에 `~`를 쓰지 말고 `/Users/rcm/Library/Logs/rcm/server.log`처럼 절대 경로를 넣어라. launchd plist 값에서 tilde 확장은 기대하지 말라.

7. §4 systemd · hardening 보강 · `WorkingDirectory=/home/rcm`, `LimitNOFILE=4096`, `ProtectSystem=strict`, `ReadWritePaths=/home/rcm/.local/share/rcm /home/rcm/.config/rcm`를 추가하라. `ProtectHome=true`는 현재 기본 data/config 경로와 충돌하므로 쓰지 말라.

8. §5 Basic UX · 로그아웃 문서화 · Basic은 브라우저 로그아웃이 사실상 안 된다. README에 “탭 닫기가 아니라 브라우저 종료/프로필 분리/토큰 revoke가 필요”를 명시하고, 401은 항상 `WWW-Authenticate: Basic realm="rcm", charset="UTF-8"`로 재프롬프트되게 하라.

**그대로 둘 것**

1. §1.4 sha를 제출 시점에 확정하는 결정은 유지하라. 합류 신원과 감사 기록이 이 지점에서 결정되어야 한다.

2. §1.2 ref 검증을 `git check-ref-format`의 부분집합으로 두는 것은 맞다. 애매한 ref는 내부 도구라도 거부하는 편이 낫다.

3. §2 `artifacts_purged_at` 컬럼 추가는 맞다. 파일 존재 여부만으로 purge 상태를 판단하지 말라.

4. §5 `/api/health` 무인증은 그대로 둬라. 현재 내용이면 모니터링용으로 충분하고 비밀이 없다.

5. §5 CSP `default-src 'none'`, `connect-src 'self'` 방향은 그대로 둬라. Basic/EventSource와 충돌하지 않는다.

**오너에게 물어야 할 것**

1. 배포 프리셋이 워크스페이스에서 `.git`, `git describe`, submodule을 필요로 하는가? 필요 없으면 `git archive`가 가장 단순하고 안전하다. 필요하면 `--shared` 없는 clone 또는 worktree 계열로 가야 한다.

2. 잡/이벤트 DB 메타데이터를 몇 일 보존할 것인가? 기술적으로는 `max(sample_days, retention_days_failure) + 여유` 이상이면 되지만, 감사 요구가 있으면 180일/365일 같은 정책 결정이 필요하다.