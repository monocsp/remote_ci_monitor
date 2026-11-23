#!/usr/bin/env python3
"""뮤테이션 확인 — 테스트가 실제로 빨개지는지 본다(PLAN.md 「테스트·품질」).

`src/` + `tests/` + `tools/` + `pyproject.toml` 을 임시 디렉터리에 복사하고 변이 하나를 넣은 뒤 그
복사본에서 pytest 를 돌린다. **pytest 가 실패해야 통과**다. 원본은 건드리지 않는다. 변이 패턴을
못 찾으면 그 자체로 실패다(코드가 바뀌어 감시가 풀린 것).

변이 목록(개수는 `MUTANTS` 가 정본이다 — 문서의 숫자는 머지마다 썩는다):
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
  M5s A — `m5s-*` 다섯: 승격이 `not_before` 를 무시함 · 만료가 시작 마감을 무시함 · 즉시 실행이
     예약에 합류함 · 합류 쿼리가 시작 마감을 무시함 · 시각을 준 제출이 합류함
     (`docs/m5s-workplan.md` §4.1 · §7)
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
        old="    return [name for name in requires if shutil.which(name, path=path) is None]\n",
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
        old='    {"serve", "worker", "token", "init", "discover", "version", "skills"}',
        new='    {"worker", "token", "init", "discover", "version", "skills"}',
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
        old="    server_v = follow.clean(raw)\n",
        new="    server_v = raw\n",
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
    Mutant(
        # 예약을 합류 대상에 넣는다 — 같은 입력의 즉시 실행이 15:00 예약에 붙어 그때까지 기다린다
        # (결정 206 · S8)
        name="m5s-immediate-run-joins-a-schedule",
        path="src/remote_ci_monitor/store.py",
        old="JOINABLE_STATES = ACTIVE_STATES - {CANCELLING}\n",
        new="JOINABLE_STATES = (ACTIVE_STATES | SCHEDULED_STATES) - {CANCELLING}\n",
        # 시작 마감 필터(아래 변이)만으로는 못 막는다 — 반복 시도는 마감 없는 예약이다(결정 231)
        tests=(
            "tests/test_schedule_m5s.py::test_nothing_joins_a_schedule_that_has_no_start_deadline",
            "tests/test_server_schedule_m5s.py::test_s8_an_immediate_run_does_not_join_a_schedule",
        ),
    ),
    Mutant(
        # 합류 쿼리가 시작 마감을 보지 않는다 — 즉시 실행이 승격된 예약에 붙어 그 마감을 물려받고
        # 레인이 늦으면 `expired` 로 끝난다(#253 격리 검증 P1)
        name="m5s-join-ignores-start-deadline",
        path="src/remote_ci_monitor/store.py",
        old='f"AND state IN ({marks}) AND start_deadline IS NULL ORDER BY id LIMIT 1",',
        new='f"AND state IN ({marks}) ORDER BY id LIMIT 1",',
        tests=(
            "tests/test_server_schedule_m5s.py::"
            "test_s8_an_immediate_run_does_not_join_a_promoted_schedule",
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
    _write_mutant(target, text.replace(m.old, m.new))
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
