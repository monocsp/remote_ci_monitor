# M5i 검증 보고 — 전체 검사 (V7.1) · README 스모크 (V7.3) · 문서·손질 (V6)

- 날짜: 2026-09-10
- 검증한 dev: `a7aaea8` (`docs(m5i): 실패 이름 마커 예시와 검증된 래퍼 · jobs·wait·check 손질 · PLAN 결정 73~83 (#92)`) — M5i PR #82 #84 #85 #87 #88 #89 #90 #92 와 수정 #91 #93 #94 가 전부 들어간 뒤. 보고서를 쓰는 시점의 `origin/dev` 는 `11586f7`(#98 Chrome CDP 마감 · #100 이 보고서의 수정)
- 명세: `docs/acceptance/m5i-verification-scenarios.md` §1 V6 · V7.1 · V7.3 (§0 규칙대로)
- 워크트리: `remote_ci_monitor-verify-full-suite-and-docs` (`docs/acceptance-m5i-verify-suite` — 룰셋의 타입 집합에 `verify` 가 없어 `docs` 로) · 자기 `.venv` (Python 3.11.15)
- 시험 서버(V6.2~V6.4): 127.0.0.1:**8799** · 설정 파일은 세션 스크래치 · `data_dir = "/Users/fmmentalcare/.local/share/rcm-verify-suite"` · `advertise = false` · 알림 훅 없음 · `[[repos]] app` 은 스크래치의 로컬 bare 레포(커밋 하나 `0b605a69823ff83e14018b733927445a568671bb`) · 프리셋 `gate`(`source_modes = ["git_ref"]`) · `ok`. 끝나고 SIGTERM 으로 내리고(`signal 15: shutting down` → `stopped`) `data_dir` 을 지웠다. 운영 체크아웃·`~/.config/rcm`·`~/.local/share/rcm` 은 열지 않았다 — `--config` 없는 `rcm check` 도 `HOME` 을 스크래치로 바꿔 돌렸다.

## 판정표

| ID | 판정 | 실측 | 명령/비고 |
|---|---|---|---|
| V7.1 ruff | **통과** | `All checks passed!` · `277 files already formatted` | `./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .` |
| V7.1 pytest | **통과** | **`3401 passed, 3 skipped in 329.69s (0:05:29)`** · exit 0 · 재실행한 테스트 없음(타이밍 실패 0) | `./.venv/bin/python -m pytest` (addopts 의 `-q` 그대로). skip 3 은 이 Mac 에 없는 것(Chrome 없이 도는 macOS 잡의 조건과 같다) |
| V7.1 node | **통과** | `# tests 443` · `# pass 443` · `# fail 0` · `# skipped 0` (`# suites 82`, 371 ms) | `node --test tests/web/*.test.js` |
| V7.1 mutcheck | **통과** | **`mutcheck: all 26 mutants caught`** · exit 0. `scripts/mutcheck.py` 의 `name=` 항목 **26**개(마지막 셋: `offline-gc-opens-original` · `v16-without-ledger-check` · `client-wheel-any-name`, 그리고 #94 의 `retention-shared-any-inode`). `AGENTS.md:72`·`CONTRIBUTING.md:13` 둘 다 `26 known mutations` · `tests/test_release_files.py` **86 passed** | `./.venv/bin/python scripts/mutcheck.py` · `grep -c 'name=' scripts/mutcheck.py` · `./.venv/bin/python -m pytest tests/test_release_files.py`. 명세의 22+2+1+1 = 26 과 같다(#92 본문의 25/25 는 #94 머지 전 숫자) |
| V7.3 | **통과** | 빌드 `Successfully built remote_ci_monitor-0.2.5-py3-none-any.whl` · 스모크 꼬리: `submitted job #1 · http://127.0.0.1:59898/#/jobs/1` → `#1 succeeded` → `smoke: rcm top` → `smoke: rcm jobs --json` → `smoke: web UI` → `smoke: SIGTERM stops the server` → **`smoke: ok (rcm 0.2.5, python 3.11.15, Darwin)`** | `./.venv/bin/python -m pip install -q build && ./.venv/bin/python -m build --wheel` → `PYTHON=python3.11 scripts/smoke_install.sh dist/*.whl`. 스크립트가 새 venv·빈 포트·격리 `HOME` 을 스스로 만든다. 뒤에 `dist/`·`build/` 삭제(커밋 안 함) |
| V6.1 | **통과** | 실패 명령(exit 7, `FAIL:` 둘 + 언급 하나 + stderr 하나): stdout 이 `::rcm::step::test` → 원 출력 4줄 그대로 → `::rcm::fail::test/a_test.dart` · `::rcm::fail::test/b_test.dart` → `::rcm::step-end::fail`, 래퍼 **exit 7**. 성공 명령(`FAIL: was fixed` 를 찍고 exit 0): 마커 없음 · `::rcm::step-end::ok` · **exit 0**. `fail_patterns`: `src/` `examples/` `README*.md` `CHANGELOG.md` `docs/configuration.md` 에 **0건**; 남은 언급은 전부 「만들지 않는다/없다」는 문장(PLAN.md 결정 77 · workplan §3 I1 · 이 명세 · Codex 리뷰 · `tests/test_examples.py` 주석) | `bash examples/preset/name-failures.sh --step test -- bash <gate.sh>` — 문서 「Naming what failed」의 `argv` 모양 그대로. `grep -rn fail_patterns` |
| V6.2 | **통과** (제출 줄은 **오류 → 수정 PR #100**) | 잡 #1 `succeeded`, `source == {"mode": "git_ref", "repo": "app", "ref": "0b605a69…71bb", "sha": "0b605a69…71bb"}`. `rcm jobs` 행: `#1     succeeded  gate             fmmentalcare@epeuem-ui-Macmini @0b605a6                         took 0s          17:03` — 40-hex **0회**, `0b605a6` **1회**. `rcm top` 최근 칸도 `@0b605a6`. 그런데 `rcm run` 의 제출 줄은 `submitted job #1 (gate · 0b605a69823ff83e14018b733927445a568671bb @0b605a6)` — I2 가 고친 칸과 같은 모양으로 두 번 | `rcm run gate --ref 0b605a69823ff83e14018b733927445a568671bb` (RCM_SERVER=http://127.0.0.1:8799) → `rcm jobs` · `rcm jobs --json` · `rcm top` · `rcm jobs \| grep -c <40-hex>` |
| V6.3 | **통과** | 8799 에 `wait --job 999999`: stderr `rcm: job 999999 not found on the server`, stdout `{"job_id":999999,"state":null,"wait_exit_code":3}`, **exit 3**, `log:` 줄 **없음** — SSE 경로와 `--poll` 경로 같음. 아무것도 없는 8779 에 `--timeout 5`: `server unreachable (cannot reach http://127.0.0.1:8779: [Errno 61] Connection refused) — reconnecting for up to 60s` → `rcm: --timeout 5s elapsed; server unreachable` → `rcm: log: rcm logs 999999`, **exit 3**, 6.1초. 연결 실패는 잡이 있을 수 있어 로그 길을 남긴다(결정 70 · #92 I3 「확정 404 만 생략」) | `rcm wait --job 999999 --server http://127.0.0.1:8799 --token …` · `… --poll` · `rcm wait --job 999999 --timeout 5 --server http://127.0.0.1:8779 --token …` (`nc -z` 로 8779 가 빈 것을 먼저 확인) |
| V6.4 | **통과** | `--config` 있음: `ok   local data dir /Users/fmmentalcare/.local/share/rcm-verify-suite (writable) · from <scratch>/v62/server.toml` — 행 이름이 `local data dir`, 상세 끝에 `· from <설정 경로>`. `--config` 없음(스크래치 `HOME`, `RCM_SERVER`·`RCM_TOKEN` 만): data dir 행이 **없다** — 서버의 경로라고 말하지 않는다. 둘 다 exit 0, `client v0.2.5 · same as server` | `rcm check --config <scratch>/v62/server.toml` · `HOME=<scratch>/home rcm check` |
| V6.5 | **통과** | `PLAN.md` 결정 표에 **73~83** 열한 행(672~682행: 오프라인 dry-run · 마이그레이션 전 백업 · 가드 · 회계 눈금 둘 · 실패 이름 · v16 · 웹 회귀 · 업그레이드 절차 · 클라 wheel · 자동 태그 · 불일치 표시). 「CLI」절(418행~)의 세션 예시에 `failed_step_guessed` **없음** — `examples/session/ci-gate.sh` 와 같은 `.failed_step // ("last step " + …)` 모양. 남은 `failed_step_guessed` 는 「`/api/status` 스키마 v1」 절의 예시 문서(371·394행 — 그 키는 API 에 실제로 있다, `tests/test_status_outcome.py` `NEW_RECENT`)와 결정 63 을 설명하는 산문(200·213행)뿐 | `grep -n '^\| 7[3-9] \|^\| 8[0-3] ' PLAN.md` · `grep -n failed_step_guessed PLAN.md` |

## 오류와 수정

| 발견 | 원인 | 수정 |
|---|---|---|
| V6.2 — `rcm run gate --ref <40-hex>` 의 제출 줄이 `submitted job #1 (gate · 0b605a69823ff83e14018b733927445a568671bb @0b605a6)` 로 같은 커밋을 두 번 찍는다 | #92(I2)는 `source_ident`·`_source_text`(목록·큐 칸)만 고쳤고, 제출 줄은 `cli.py` 의 다른 문자열 리터럴 둘(`--no-wait` 분기 · 기다리는 분기)이었다 | **PR #100** `fix(cli): --ref <sha> 제출 줄이 같은 커밋을 두 번 찍던 것 — 목록 칸의 규칙으로`. `core/render_text.py` 에 `ref_ident(ref, sha)`(칸과 같은 40-hex 완전 일치 규칙, 자르지 않음, sha 없으면 `—`) · 두 분기 모두 이 함수. 빨간 테스트: `tests/test_render_m5i.py` 순수 함수 셋 · `tests/test_cli_m5i.py` 가 진짜 서버 + bare 레포로 두 경로의 stderr 를 읽어 단언. CHANGELOG 의 #92 I2 항목에 덧붙임. 머지 커밋 `11586f7` |

## 확인 못 함

없음. V6·V7.1·V7.3 의 모든 항목을 실기 또는 자동으로 확인했다.

## 오너 대기

이 영역에는 없다(명세 §2 O1~O5 는 verify-web·verify-client 보고서 참고).
