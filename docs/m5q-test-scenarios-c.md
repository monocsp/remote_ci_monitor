# M5q 테스트 시나리오 C — 서버 쪽 변경 · wheel · 표시 · 문서 · 실기 (2026-11-19)

`docs/m5q-workplan.md` 가운데 서버 쪽 작은 변경(§2.10 · §5.3), 사람이 보는 표시(§2.9), 보안 문서(§4), 문서
잠금(§9 의 PR A 문서 목록), mutcheck 변이(§6), 실기 확인 절차(§7)를 테스트와 확인 절차로 옮긴 것이다. 결정은
**166 · 167 · 168 · 169 · 170** 이 중심이다. 한 줄로 줄이면 이렇다. **서버가 주는 wheel 은 릴리스 wheel 과 같은
파일을 담고, 서버는 인증한 제출에서만 클라이언트 버전을 한 줄 남기며, 사람은 `rcm version` · `rcm check` · 문서에서
따라가기가 켜졌는지와 그 위험을 읽는다.**

이 문서가 다루지 않는 것은 따라가기의 판단과 받기 · 풀기 · 재실행(§2.1~§2.8, §6 의 F · R 표), 그리고 원격 워커의
계기와 드레인(§3, W 표)이다. 다만 그 결과가 사람에게 보이는 문구(처음 받을 때의 stderr 한 줄, 실패 경고 한 줄,
`rcm version` 둘째 줄, `rcm check` 의 `client` 행 꼬리)와 §7 의 워커 단계는 여기서 다룬다. dolomood-renew 쪽(§10)은
이 마일스톤 밖이라 시험으로 만들지 않고 확인 항목으로만 둔다.

줄 번호의 머리 글자는 묶음을 가른다. `CB` 는 베스트(의도한 길), `CE` 는 에러(거절 · 실패), `CG` 는 엣지(경계 ·
경쟁 · 옛 서버)다. `L` 은 실기 단계다.

## 테스트 파일

| 약칭 | 파일 | 무엇 | PR |
|---|---|---|---|
| wheel | `tests/test_client_wheel.py` | 서버 wheel 의 파일 목록 잠금(S1)과 새 venv 에 깐 뒤의 `rcm skills`(S2). 기존 파일에 더한다 | A0 |
| packaging | `tests/test_packaging.py` | 릴리스 wheel(`pip wheel .`, 기존 `wheel` 픽스처)과 서버 wheel 의 내용 비교. 기존 파일에 더한다 | A0 |
| serverlog | `tests/test_server_client_version_log.py` | 제출 경로의 `client <토큰 이름> runs v<X>` 한 줄(S3 · S4)과 health 계약 | A |
| display | `tests/test_cli_m5q_display.py` | `rcm version` · `rcm --version` · `rcm version --json` · `rcm check` 의 `client` 행 · stderr 한 줄의 문구 | A |
| wire | `tests/test_worker_wire_m5q.py` | 등록 409 본문과 heartbeat 응답의 `server_version`. B 문서와 겹치면 그쪽을 따른다 | B1 · B2 |
| docs | `tests/test_docs_m5q.py` | 문서 잠금. configuration · usage 두 언어 · operating 두 절 · README 두 언어 · CHANGELOG · mutcheck 변이 이름 | A0 · A |
| 실기 | 없음(사람이나 격리 에이전트가 손으로 돈다) | §7 을 단계로 나눈 L0~L11 | A · B1 · B2 |

`tests/test_docs_m5q.py` 는 mutcheck 의 복사본(`src` · `tests` · `pyproject.toml` 만)에 없는 파일을 읽으므로 코드
시험과 나눈다. `tests/test_docs_client_wheel.py` 와 같은 이유다.

## 공통 준비

- **격리.** PR A 가 새로 두는 `tests/conftest.py` 가 `XDG_CACHE_HOME` 과 `XDG_CONFIG_HOME` 을 `tmp_path` 아래로 돌린다
  (§6 머리말). 이 문서의 시험은 그 위에서 돈다. 사용자의 `~/.cache/rcm` 과 한 줄 파일을 읽지도 쓰지도 않는다.
- **서버.** `test_server.Server(tmp_path, workers=False)` 와 그 `req` 를 쓴다. 제출이 필요한 시험은
  `workers=True` 와 기본 프리셋 `ok` 를 쓴다.
- **서버 로그 받기.** `server.log_line` 은 호출할 때마다 `sys.stderr` 를 찾아 `print` 한다. 그래서 서버 스레드가 쓴
  줄도 `capsys` 로 받힌다. 줄은 `^\[rcm\] client (\S+) runs v(\S+)$` 로 고른다.
- **User-Agent 넣기.** 손으로 정한 `User-Agent` 를 보내는 도우미 `submit_as(srv, token, ua, body)` 를 둔다.
  `urllib.request.Request` 에 헤더를 직접 넣는다. 진짜 CLI 경로(CB11)는 이 도우미를 쓰지 않고 `cli.main` 이 실제로 보낸
  헤더를 쓴다. 손으로 흉내 낸 헤더만 시험하면 클라이언트 쪽 헤더 이름이 바뀌어도 시험이 초록으로 남는다.
- **기록과 캐시 만들기.** `follow.json` 은 손으로 JSON 을 쓰지 않고 진짜 `follow.save_record` 로 쓴다(§5.1). 캐시
  폴더는 진짜 `follow.fetch` 를 가짜 서버(`tests/follow_helpers.py` 의 `fake_wheel` 과 `ThreadingHTTPServer`)에 대고
  만든다. 마지막 사용 시각은 표지 `.rcm-client.json` 의 mtime 을 `os.utime` 으로 맞춘다. 표시 코드는 실제 생산자가
  만든 기록을 읽어야 한다. 손으로 쓴 기록은 두 쪽이 같이 틀려도 초록이다.
- **따라간 자식 흉내.** `remote_ci_monitor._follow_boot = {"RCM_FOLLOWED": "0.8.1", "RCM_FOLLOWED_PATH": "…",
  "RCM_FOLLOWED_FROM": "0.8.0", "loaded": True}` 를 두고 `cli.__version__` 을 `0.8.1` 로 바꾼다. 다른 상태(실패 ·
  고정 · editable)는 `cli._main(argv, follow_enabled=True, execve=fake)` 에 가짜 서버를 대고 만든다. `main(argv)` 는
  따라가기를 하지 않으므로(결정 162) 꼬리 시험에 쓸 수 없다.
- **시각.** 지금을 `2026-11-19T06:23:00Z` 로 고정하고 기록의 `checked_at` 을 그 180초 앞(`06:20:00Z`)으로 둔다.
  그러면 `checked 3m ago` 가 나온다. 나이 문구는 `render_text._coarse`(`12s` · `57m` · `3h`)를 쓴다고 가정한다.
- **네트워크 막기.** `rcm version` 시험은 `refuse_network`(`tests/test_cli_m5e.py`)로 모든 소켓 연결을 실패로 만든다.
- **서버 wheel 에 새로 실리는 15개 파일**(§1.4). `.py` 템플릿 4개는 오늘도 실린다.

  | 종류 | 파일(`remote_ci_monitor/skills/` 아래) |
  |---|---|
  | `SKILL.md` 5 | `rcm-connect/` · `rcm-gate-connect/` · `rcm-qa-connect/` · `rcm-release-driver/` · `rcm-store-connect/` |
  | `.sh` 8 | `rcm-gate-connect/templates/gate_wrapper.sh` · `progress_snippets.sh`, `rcm-qa-connect/templates/qa_job.sh`, `rcm-release-driver/templates/release_driver.sh`, `rcm-store-connect/templates/release_plan.sh` · `release_review.sh` · `release_upload.sh` · `release_version.sh` |
  | `.toml` 2 | `rcm-store-connect/templates/presets.release.toml` · `profile.toml` |

  저장소에서 실행 비트(100755)가 켜진 파일은 넷이다. `qa_job.sh` · `release_check.py` · `release_driver.sh` ·
  `release_version.sh` 다. 서버 wheel 은 모든 항목을 0o644 로 쓴다(`clientwheel._zip`). 「명세에 묻는 것」 15번.

- **CLI 문구 위치.** `rcm check` 의 행은 stdout 이고 모양은 오늘의 코드 그대로 `f"{label}  {name:<13} {detail}"` 이다.
  `label` 은 `ok ` · `warn` · `FAIL` 이라 `ok` 행은 `ok   client        …`(ok 뒤 공백 셋)이 된다. 따라가기의 한 줄들은
  stderr 이고 머리는 `rcm: ` 다. 아래 표의 문구 가운데 명세에 글자가 없는 것은 제안이고 「명세에 묻는 것」에 번호를 달았다.

## 베스트

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| CB1 | 서버 wheel 의 파일 목록이 패키지 파일 목록과 같다 | `build_wheel(__version__)` 과 `importlib.resources.files("remote_ci_monitor")` 를 걷는 목록 | `remote_ci_monitor/` 아래 이름 집합이 패키지 폴더의 파일 집합과 같다. 제외는 `__pycache__` · `.pyc` · `.pyo` · 점으로 시작하는 이름뿐이다. 「`.py` 는 어디든 + 두 폴더」 같은 규칙을 시험에 다시 적지 않는다. 그래야 앞으로 새 자산 폴더가 생겨도 이 시험이 빨개진다 | §2.10 · 167 · S1 | wheel |
| CB2 | 15개 파일이 실리고 바이트가 같다 | CB1 의 wheel | 공통 준비의 15개가 이름 그대로 있고 `zf.read(name)` 이 패키지 파일의 바이트와 같다. 다섯 `SKILL.md` 는 이름을 시험에 적어 둔다(목록 계산이 통째로 비어도 잡히게) | §1.4 · 167 | wheel |
| CB3 | 새 파일도 RECORD 에 맞게 오르고 결정적이다 | CB1 의 wheel 두 번 | 기존 `test_the_record_hashes_match_the_zip_contents` 와 `test_the_wheel_is_deterministic` 이 그대로 통과한다. RECORD 의 행 수가 이름 수와 같고 `skills/` 행마다 해시와 크기가 맞다 | 결정 81 | wheel |
| CB4 | 서버 wheel 을 새 venv 에 깔면 스킬 다섯이 보인다 | 조립한 wheel 을 `pip install`(pip 가 없으면 skip) | `rcm skills list` 가 다섯 줄이고 줄마다 `rcm-…` 이름 뒤에 빈 칸이 아닌 설명이 붙는다. `rcm skills install --into P` 가 종료 0 이고 `P/.claude/skills/` 아래에 19개 파일을 쓴다. 각 파일의 바이트가 패키지 파일과 같다 | §1.4 · S2 | wheel |
| CB5 | 릴리스 wheel 과 서버 wheel 이 같은 파일을 담는다 | `test_packaging` 의 `wheel` 픽스처(`pip wheel .`)와 `build_wheel(__version__)` | `remote_ci_monitor/` 아래 이름 집합이 같고 이름마다 바이트가 같다. `*.dist-info/` 는 비교하지 않는다(RECORD · WHEEL 은 서버가 새로 쓴다, 결정 81) | §2.10 · 167 · `clientwheel` 머리말 「같아야 하는 것은 설치 결과다」 | packaging |
| CB6 | 두 wheel 의 설치 결과가 같다 | 새 venv 둘에 릴리스 wheel 과 서버 wheel 을 하나씩 | 두 venv 의 `rcm skills list` 출력이 글자까지 같다. 두 `rcm skills install` 이 만든 트리가 상대 경로와 바이트까지 같다. pip 나 hatchling 을 못 받으면 skip | 결정 81 · 167 | packaging |
| CB7 | 진짜 health 를 진짜 `parse_health` 가 받는다 | `Server` 의 `GET /api/health` 본문과 `GET <client_wheel.path>` 의 바이트 | `follow.parse_health(server, body, now=…)` 가 예외 없이 `ServerInfo` 를 낸다. `version == __version__`, `wheel_path == "/client/" + wheel_filename(__version__)`, `sha256 == sha256(받은 바이트)`, `bytes == len(받은 바이트) <= MAX_WHEEL_BYTES`, `wheel_error is None`. skills 가 실려 커진 wheel 도 상한 안이다 | §2.4 · §5.3 | serverlog |
| CB8 | health 503 과 `client_wheel: null` 도 계약을 지킨다 | janitor stale 로 503(`test_client_wheel._make_health_503`), 그리고 `build_wheel` 이 `WheelBuildError("metadata_missing")` 를 내는 서버 | 503 본문도 `parse_health` 를 통과한다. 조립 실패 서버의 본문은 `wheel_path is None` · `wheel_error == "metadata_missing"` 인 `ServerInfo` 가 된다(`bad_health` 가 아니다) | §2.4 · §2.7 · F16 · F18 | serverlog |
| CB9 | 같은 쌍은 한 번, 새 버전은 또 한 번 | 토큰 `macbook`, `User-Agent: rcm/0.8.1` 로 제출 셋, 이어서 `rcm/0.8.2` 로 하나 | 서버 stderr 에 정확히 두 줄이 이 순서로 있다. `[rcm] client macbook runs v0.8.1`, `[rcm] client macbook runs v0.8.2` | §2.10 · 168 · S3 | serverlog |
| CB10 | 같은 버전, 다른 토큰 | `macbook` 과 `macstudio` 가 같은 `rcm/0.8.1` 로 제출 | 줄이 토큰마다 하나씩, 모두 둘 | 168 | serverlog |
| CB11 | 진짜 CLI 의 제출이 로그를 남긴다 | `cli.main(["run", "ok", "--no-wait"])`, 토큰 `alice` | `[rcm] client alice runs v<__version__>` 한 줄. 헤더는 `Client` 가 실제로 붙인 것이다 | 168 | serverlog |
| CB12 | 서버 쪽 변경이 DB 를 바꾸지 않는다 | CB9 앞뒤 | `PRAGMA user_version` 이 같고 `jobs` · `submissions` 의 열 목록이 같다. `/api/status` 의 `server.last_error` 가 그대로다(로그 한 줄은 오류가 아니다) | 168 · §8 「DB 에 클라이언트 버전 기록」 안 함 | serverlog |
| CB13 | 정보가 없으면 `rcm version` 은 한 줄이다 | 설정 기본, 기록 없음 | 기존 `test_version_prints_python_and_platform_on_one_line` 이 그대로 통과한다. 출력 한 줄, stderr 비어 있음 | 166 · F44 | display |
| CB14 | 기록이 있으면 둘째 줄 | 설치 종류 `wheel`, `client.toml` 의 `server = "http://127.0.0.1:8791"`, 그 서버의 기록 `0.8.1`(180초 전), 캐시 `0.8.1`(어제 씀) · `0.8.0`(지난주 씀) | 둘째 줄이 정확히 `follow: on (default) · server http://127.0.0.1:8791 v0.8.1 checked 3m ago · cached 0.8.1, 0.8.0`. 소켓 연결 0번 | §2.9 · 166 · F45 | display |
| CB15 | editable 설치 | 설치 종류 `editable`, 설정 없음 | 둘째 줄 `follow: off (editable install)`. 기록이 있어도 서버 토막은 붙이지 않는다고 가정한다(「명세에 묻는 것」 8번) | §2.9 · 156 · F10 | display |
| CB16 | 고정 둘 | 한 줄 파일 `0.8.0` / `RCM_CLIENT_VERSION=0.8.0`, 캐시 `0.8.0` | 파일이면 `follow: pinned 0.8.0 (~/.config/rcm/client-version) · cached 0.8.0`(명세 예). 환경 변수면 제안 `follow: pinned 0.8.0 (RCM_CLIENT_VERSION) · cached 0.8.0`. 파일 경로는 홈 아래면 `~` 로 줄인다 | §2.9 · 155 | display |
| CB17 | `rcm --version` 은 언제나 한 줄 | CB14 · CB16 의 상태 각각 | 출력이 `rcm <설치본 버전>` 한 줄. dolomood 의 `rcm --version \| awk '{print $2}'` 가 설치본 버전을 읽는다 | §2.9 · 166 | display |
| CB18 | `rcm version --json` 의 `follow` | CB14 | 기존 다섯 키(`version` `python` `platform` `machine` `schema_version`)의 값이 그대로이고 `follow` 가 더해진다. `follow` 의 키는 `mode` `pin` `source` `install` `server` `server_version` `checked_at` `cached` 여덟. 값은 `"follow"`, `null`, `"default"`, `"wheel"`, `"http://127.0.0.1:8791"`, `"0.8.1"`, `"2026-11-19T06:20:00Z"`, `["0.8.1", "0.8.0"]`. 맨 위에 `server` 키는 없다(`test_version_json_stays_server_free`). 기존 `test_version_json` 의 키 집합 잠금은 여섯 키로 고친다 | §2.9 · 166 · F45 | display |
| CB19 | `rcm check` 의 `client` 행 네 상태 | 따라간 자식 / sha 불일치로 실패 / `RCM_CLIENT_VERSION` 고정 / editable | 명세 §2.9 의 네 줄이 오늘의 칸 모양으로 나온다. `ok   client        v0.8.1 · same as server · following (installed v0.8.0)`, `warn  client        v0.8.0 · server v0.8.1 · older · not following: sha256 mismatch — pip install http://127.0.0.1:<port>/client/remote_ci_monitor-0.8.1-py3-none-any.whl`, `warn  client        v0.8.0 · server v0.8.1 · older · pinned by RCM_CLIENT_VERSION`, `ok   client        v0.8.1 · same as server · not following (editable install)` | §2.9 · 166 · F46 | display |
| CB20 | 처음 받을 때의 stderr 한 줄 | 설치본 0.8.0 · 서버 0.8.1 · 캐시 빔, wheel 707,673 바이트 | stderr 가 정확히 한 줄 `rcm: following server v0.8.1 (installed v0.8.0) — fetched 707.7 KB`. 크기는 `render_text._bytes` 의 십진 눈금이다(「명세에 묻는 것」 12번). stdout 에는 아무것도 더해지지 않는다 | §2.5 · 166 | display |
| CB21 | `--no-follow` 가 도움말에 있다 | `rcm --help`, `rcm run --help`, `rcm worker --help`(B1 뒤) | 셋 다 `--no-follow` 와 한 줄 설명을 보인다. `rcm --help` 는 따라가지 않으므로 설치본의 도움말이다 | §2.2 · §5.2 | display |
| CB22 | 문서 잠금 | 구현 뒤의 문서 | 아래 「문서 잠금」 표의 글자가 전부 있다 | §4 · §9 | docs |

## 에러

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| CE1 | 제출이 아닌 경로는 로그를 남기지 않는다 | `User-Agent: rcm/0.8.1` 과 유효한 토큰으로 `GET /api/status` · `GET /api/health` · `GET /client/<이름>` · `GET /jobs/<N>` · `POST /jobs/<N>/cancel` | `client … runs` 줄이 0 | §2.10 · S4 | serverlog |
| CE2 | 토큰 없는 제출 · 틀린 토큰 | `POST /jobs` 에 Authorization 없음 / 엉터리 Bearer | 401, 줄 0 | §2.10 「인증하지 않는 경로의 헤더로 로그를 채울 수 없게」 · S4 | serverlog |
| CE3 | 워커 토큰으로 제출 | 워커 토큰의 `POST /jobs` | 403 `worker tokens cannot use the client API`, 줄 0 | `require_client_token` | serverlog |
| CE4 | 인증은 됐지만 본문이 틀린 제출 | 유효한 토큰, 없는 프리셋 이름 | 400. 줄은 「명세에 묻는 것」 13번의 답을 따른다. 제안은 토큰 검사 바로 뒤에 남기는 것이라 줄 하나 | §2.10 | serverlog |
| CE5 | 이상한 User-Agent | 유효한 토큰으로 `rcm/`(빈 버전), `rcm/0.8.1 extra`, `rcm/\x1b[2J`, `rcm/` + 65자, `curl/8.4.0`, 헤더 없음 | 어느 경우에도 서버 stderr 에 ESC(`\x1b`) 바이트가 없고 65자 버전이 찍히지 않는다. 버전은 `follow.VERSION_RE`(64자 이하)로 거른다. 걸러진 값의 처리는 「명세에 묻는 것」 13번. 제안은 줄을 쓰지 않는 것 | 168 · §2.4 의 같은 정규식 | serverlog |
| CE6 | 실패 경고에 서버가 보낸 값을 그대로 찍지 않는다 | `bad_health` 를 낳는 health 셋: `version` 이 `"\x1b]0;evil\x07"`, `"../x"`, 65자 | stderr 의 경고 한 줄에 ESC · BEL 바이트와 그 문자열이 없다. 경고의 모양은 「명세에 묻는 것」 11번. 제안은 `rcm: not following http://127.0.0.1:<port>: the server's health has a malformed version — running installed v0.8.0` | §2.6 · §2.4 | display |
| CE7 | 서버 버전을 모르는 경고 | 서버 꺼짐 · 기록 없음(`unreachable`) | 경고가 `v` 뒤에 빈 값이나 `None` 을 찍지 않는다. 제안은 CE6 과 같은 꼴 `rcm: not following http://127.0.0.1:<port>: cannot reach the server — running installed v0.8.0` | §2.6 · F14 | display |
| CE8 | 따라가기 실패 + 바닥 아래 | 설치본 0.1.9 · 서버 0.8.1 · sha 불일치 | `FAIL  client        v0.1.9 · server v0.8.1 · older · not following: sha256 mismatch — pip install http://…/client/remote_ci_monitor-0.8.1-py3-none-any.whl`, 종료 1. 판정은 오늘처럼 설치본 버전으로 한다 | §2.9 「FAIL 은 min_client_version 아래일 때뿐」 | display |
| CE9 | wheel 이 없는 서버의 꼬리와 힌트가 겹친다 | 서버 0.8.1 이 `client_wheel: null` · `client_wheel_error: "metadata_missing"`, 캐시에 0.8.1 없음 | 이유가 두 번 나오지 않는다. 오늘의 힌트 `— no client wheel from this server (metadata_missing)` 와 꼬리 `not following: the server has no client wheel (metadata_missing)` 가 겹친다. 「명세에 묻는 것」 7번. 제안은 `warn  client        v0.8.0 · server v0.8.1 · older · not following: the server has no client wheel (metadata_missing)` 하나 | §2.6 `no_wheel` · `_upgrade_hint` | display |
| CE10 | 옛 서버 꼬리 | health 에 `client_wheel` 키가 없는 서버(0.2.5 흉내) | 꼬리 `not following: the server is too old to hand out its client`, 힌트는 CE9 와 같은 규칙으로 한 번만 | §2.6 `old_server` | display |
| CE11 | `rcm version` 과 틀린 설정 | `RCM_CLIENT_VERSION=lates` / 한 줄 파일에 `lates` | 「명세에 묻는 것」 10번의 답을 따른다. 제안은 첫 줄을 그대로 찍고 둘째 줄에 `follow: invalid RCM_CLIENT_VERSION 'lates' (use follow, installed or a version)`, 종료 0. 파일이면 괄호 안에 경로. 따라가는 명령(`rcm jobs`)은 명세대로 종료 2 | §2.2 · F13 | display |
| CE12 | 깨진 기록 파일 | `follow.json` 이 JSON 이 아님 / 값의 타입이 틀림(`"version": 7`) | `rcm version` 은 트레이스백 없이 종료 0. 기록이 없을 때와 같은 출력. `--json` 의 `follow.server_version` 은 `null` | §2.4 「다시 만들 수 있는 캐시」 | display |
| CE13 | 문서에서 한 문구가 빠짐 | 문서 잠금의 각 행 | 시험 실패 메시지가 빠진 문구와 파일 이름을 말한다(`test_docs_m5n` 과 같은 꼴) | 집안 규칙 | docs |

## 엣지

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| CG1 | 쌍의 기억은 프로세스 하나 | CB9 뒤 같은 `data_dir` 로 서버를 새로 띄워 같은 쌍으로 제출 | 줄이 다시 한 번 나온다 | §5.3 「프로세스당 쌍마다 한 번」 | serverlog |
| CG2 | 같은 쌍이 동시에 처음 온다 | 두 스레드가 장벽을 넘자마자 같은 토큰 · 같은 UA 로 제출 | 줄이 정확히 하나 | 168 | serverlog |
| CG3 | 합류한 제출 | 같은 입력으로 두 번 제출해 둘째가 `joined` | 첫 제출에서 한 줄, 합류에서는 더 없다. 다른 토큰의 합류면 그 토큰의 줄이 하나 | 168 | serverlog |
| CG4 | admin 토큰 | admin 토큰 `owner` 의 제출 | `[rcm] client owner runs v…`. admin 도 클라이언트 종류다 | `require_client_token` | serverlog |
| CG5 | 개발 버전 문자열 | `User-Agent: rcm/0.7.0.dev0` | `[rcm] client <이름> runs v0.7.0.dev0`. 실기 L9 가 이 줄을 본다 | §7 | serverlog |
| CG6 | git_ref 제출 | `--source git_ref` 프리셋(기존 `test_cli_gitref` 의 준비) | 같은 `POST /jobs` 이므로 한 줄 | §5.3 | serverlog |
| CG7 | health 에 키가 늘지 않는다 | `GET /api/health` | `requires_python` 키가 없고 `follow` 로 시작하는 키가 없다. `schema_version` 이 M5q 앞과 같다. 키 집합 전체는 잠그지 않는다(M5p 가 `shelf` 를 더한다). 잠긴 키 집합은 생산자와 소비자가 같이 틀려도 초록이라 CB7 의 진짜 소비자 시험을 함께 둔다 | §5.3 · P2-10 | serverlog |
| CG8 | 숨김 파일과 찌꺼기 | `importlib.resources.files` 를 패키지 사본(tmp)으로 바꾸고 `skills/rcm-connect/.DS_Store` · `skills/rcm-qa-connect/templates/__pycache__/qa_report.cpython-311.pyc` 를 더함 | 둘 다 wheel 에 없고 CB1 의 비교도 둘을 빼고 같다. `rcm skills install` 도 둘을 복사하지 않는다 | `_package_files` · `_skill_files` | wheel |
| CG9 | 실행 비트 | CB5 의 두 wheel | 「명세에 묻는 것」 15번의 답을 따른다. 제안은 서버 wheel 도 실행 비트가 있는 파일을 0o755 로 싣고, 시험은 네 파일의 `external_attr >> 16` 이 두 wheel 에서 같은지 본다 | 결정 81 「같아야 하는 것은 설치 결과다」 | packaging |
| CG10 | 옛 서버를 따라가면 스킬이 줄어든다 | A0 이전 서버(0.6.1) 를 가리키는 `rcm skills list` | 실기 L5 에서 확인한다. 0.6.1 의 wheel 에는 `.py` 템플릿만 있어 세 줄이 설명 없이 나온다. 받아들일지는 「명세에 묻는 것」 16번 | §2.3 `skills` · 167 | 실기 |
| CG11 | 다른 서버의 기록만 있다 | 기록은 8790 것뿐, 기본 서버는 8791 | `rcm version` 둘째 줄이 없다(설정이 기본이고 기본 서버 항목이 없으므로) | §2.9 「기본 서버의 항목이 있을 때」 | display |
| CG12 | 기본 서버가 `auto` | `client.toml` 의 `server = "auto"`, 기록 있음 | mDNS 질의 0번(`version` 은 서버에 묻지 않는다). 출력은 「명세에 묻는 것」 8번의 답을 따른다 | §2.9 · F44 | display |
| CG13 | 나이와 실패 기록 | 기록 `checked_at` 이 5초 · 2시간 전 / `failed_at` 이 30초 전 | `checked 5s ago` · `checked 2h ago`. `failed_at` 이 있을 때의 문구는 「명세에 묻는 것」 8번. 제안은 `· unreachable 30s ago (last seen v0.8.1 2h ago)` | §2.4 · 166 | display |
| CG14 | 캐시 목록에 들어가는 것 | 캐시에 `0.8.1-aaaa…` · `0.8.1-bbbb…`(같은 버전 다른 sha) · `.tmp-x` · `.trash-y` · 표지 없는 `0.7.9-cccc…` | `cached 0.8.1, …` 에서 같은 버전은 한 번만, 마지막 사용이 최근인 순. `.tmp-*` · `.trash-*` · 표지 없는 폴더는 세지 않는다. 비어 있으면 `· cached` 토막이 없다(「명세에 묻는 것」 8번) | §2.5 「표지가 없는 폴더는 완성본이 아니다」 | display |
| CG15 | 꼬리의 나머지 갈래 | `--no-follow` / `RCM_CLIENT_VERSION=installed` / 한 줄 파일 `installed` / source 설치 / 파일로 고정 | 제안 문구. `· not following (--no-follow)`, `· not following (RCM_CLIENT_VERSION=installed)`, `· not following (~/.config/rcm/client-version)`, `· not following (source install)`, `· pinned by ~/.config/rcm/client-version`. 「명세에 묻는 것」 7번 | §2.9 | display |
| CG16 | 따라가서 바닥 문제가 사라진다 | 설치본 0.1.9 · 서버 0.8.1 · 따라간 자식 | `ok   client        v0.8.1 · same as server · following (installed v0.1.9)`, 종료 0. 도는 코드가 서버 버전이므로 FAIL 이 아니다 | §2.9 | display |
| CG17 | 서버보다 새 설치본이 내려간다 | 설치본 0.8.2 · 서버 0.8.1 · 따라간 자식 | `ok   client        v0.8.1 · same as server · following (installed v0.8.2)` | 150 | display |
| CG18 | 내려간 자식이 M5q 이전 | 실기 L2 | 0.6.1 의 옛 행 `ok   client        v0.6.1 · same as server` 가 꼬리 없이 나온다 | §2.9 끝 문단 | 실기 |
| CG19 | `main(argv)` 경로에는 꼬리가 없다 | 기존 `test_check_says_the_client_is_the_same_as_the_server`(`$` 로 끝을 잠근다) | 그대로 통과한다. `state=None` 이면 오늘과 같은 글자 | §5.2 `_client_row` · 162 | display |
| CG20 | `--no-follow` 의 두 자리 | `rcm --no-follow jobs`, `rcm jobs --no-follow`, `rcm --no-follow skills list`, `rcm skills list --no-follow` | 앞의 셋은 설치본 파서가 받는다. `skills` 에는 `client_opts` 가 없어 넷째가 지금 계획대로면 사용법 오류(종료 2)다. 「명세에 묻는 것」 17번 | §2.2 · F7 | display |
| CG21 | `check` 는 경고와 꼬리를 둘 다 낸다 | `rcm check`, sha 불일치 | stderr 경고 한 줄과 행의 꼬리가 같은 이유 문구를 쓴다(`sha256 mismatch`). 경고는 한 번 | §2.6 「한 명령에 한 번」 · §2.9 | display |
| CG22 | 등록 409 본문 | 버전이 다른 워커의 `POST /worker/register` | 409 본문에 `server_version` 이 서버의 `version` 과 같은 문자열로 있고 기존 문구 `install the same release` 가 그대로다 | §5.3 · B1 | wire |
| CG23 | heartbeat 응답 | 등록된 워커의 `POST /worker/heartbeat` | 응답에 `server_version` 이 있다. 옛 워커 클라이언트(키를 모름)의 heartbeat 처리가 그대로다 | §5.3 · B2 | wire |

## 실기 확인 절차 (§7 을 단계로)

운영 설정(`~/.config/rcm/server.toml`), 운영 데이터(`~/.local/share/rcm`), 포트 8787 은 쓰지 않는다. 8788 은 다른
세션의 자리다. 아래 명령은 zsh 와 bash 에서 같다. 기대 출력에서 `<…>` 는 그때 바뀌는 값이다. 명세 §7 과 다른 곳은
그 단계에 적었고 「명세에 묻는 것」 1~5번에 모았다.

### L0 준비

**L0a 운영을 가리키는 변수가 없어야 한다.** `HOME` 만 바꾸면 격리되지 않는다. `XDG_*` 와 `RCM_*`(특히
`RCM_CONFIG` · `RCM_SERVER_DATA_DIR`)가 이긴다.

```sh
D=$HOME/.local/share/rcm-devtest-m5q
env | grep -E '^(RCM_|XDG_)'; echo "grep-exit=$?"
lsof -nP -iTCP:8790 -iTCP:8791 -iTCP:8792 -sTCP:LISTEN
ls -la ~/.cache/rcm ~/.config/rcm >"$D.before.txt" 2>&1   # L11 에서 비교한다. $D 를 지워도 남는 옆 파일
```

기대: `grep-exit=1`(아무 줄도 없다), `lsof` 출력 없음. 변수가 있으면 이 셸에서 `unset` 한다. 실기 중에 다른 세션이 진짜
`rcm` 을 쓰면 `~/.cache/rcm` 의 시각이 바뀔 수 있다. L11 의 비교가 어긋나면 그 바뀐 줄이 이 실기의 것인지부터 본다.

**L0b 빌드 넷.** `REPO` 는 PR A(실기 10 을 하려면 B1 · B2 까지)가 다 들어간 체크아웃이다. `git archive` 는 커밋된
것만 담는다.

```sh
PY=python3.11
REPO=<M5q 빌드 체크아웃>
mkdir -p "$D"/a "$D"/b "$D"/home "$D"/proj "$D"/out

mkdir -p "$D/src-a" && git -C "$REPO" archive v0.6.1 | tar -x -C "$D/src-a"
$PY -m pip wheel --no-deps -w "$D/dist-a" "$D/src-a"
$PY -m venv "$D/venv-a" && "$D/venv-a/bin/pip" install -q "$D"/dist-a/remote_ci_monitor-0.6.1-*.whl

for v in dev0 dev1; do
  mkdir -p "$D/src-$v" && git -C "$REPO" archive HEAD | tar -x -C "$D/src-$v"
  sed -i '' "s/^__version__ = .*/__version__ = \"0.7.0.$v\"/" "$D/src-$v/src/remote_ci_monitor/__init__.py"
  $PY -m pip wheel --no-deps -w "$D/dist-$v" "$D/src-$v"
  $PY -m venv "$D/venv-$v" && "$D/venv-$v/bin/pip" install -q "$D"/dist-$v/*.whl
done
$PY -m venv "$D/venv-c" && "$D/venv-c/bin/pip" install -q "$D"/dist-dev0/*.whl

for v in a dev0 dev1 c; do "$D/venv-$v/bin/rcm" --version; done
"$D/venv-c/bin/python" -c 'import remote_ci_monitor as m; print(m.__file__)'
```

기대: `rcm 0.6.1`, `rcm 0.7.0.dev0`, `rcm 0.7.0.dev1`, `rcm 0.7.0.dev0` 네 줄. 마지막 줄의 경로가
`$D/venv-c/lib/python3.11/site-packages/` 아래다(editable 이 아니다). `pip wheel` 은 PyPI 에서 hatchling 을 받는다.

**L0c 서버 설정과 토큰.** 두 서버 모두 `advertise = false`, 알림 훅 없음이다.

```sh
cat >"$D/a/server.toml" <<EOF
[server]
bind = "127.0.0.1"
port = 8790
data_dir = "$D/a/data"
advertise = false

[[presets]]
name = "demo"
argv = ["sh", "-c", "echo ::rcm::step::demo; sleep 2; echo ok"]
timeout_seconds = 120
EOF
sed -e 's/8790/8791/' -e "s#$D/a/data#$D/b/data#" "$D/a/server.toml" >"$D/b/server.toml"
cat >>"$D/b/server.toml" <<'EOF'

[[presets]]
name = "slow"
argv = ["sh", "-c", "echo ::rcm::step::slow; sleep 40; echo ok"]
timeout_seconds = 300
pool = "m5q"
EOF
TA=$("$D/venv-a/bin/rcm" token add c-a --config "$D/a/server.toml")
TB=$("$D/venv-dev0/bin/rcm" token add c-b --config "$D/b/server.toml")
TW=$("$D/venv-dev0/bin/rcm" token add w-m5q --worker --config "$D/b/server.toml")
```

**L0d 서버 둘을 `--debug` 로 띄운다.** 접근 로그로 `/client/` 요청을 센다.

```sh
nohup "$D/venv-a/bin/rcm" serve --config "$D/a/server.toml" --debug >"$D/a/server.log" 2>&1 & echo $! >"$D/a/pid"
nohup "$D/venv-dev0/bin/rcm" serve --config "$D/b/server.toml" --debug >"$D/b/server.log" 2>&1 & echo $! >"$D/b/pid"
sleep 2
curl -s http://127.0.0.1:8790/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["version"], d["client_wheel"]["path"])'
curl -s http://127.0.0.1:8791/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["version"], d["client_wheel"]["path"])'
```

기대: `0.6.1 /client/remote_ci_monitor-0.6.1-py3-none-any.whl` 와
`0.7.0.dev0 /client/remote_ci_monitor-0.7.0.dev0-py3-none-any.whl`.

**L0e 클라이언트 C.** 명세의 `C$` 를 실행 파일 하나로 둔다. 셸 함수는 `RCM_TOKEN=… C …` 꼴에서 셸마다 동작이 달라
쓰지 않는다. 기본 서버는 B 다.

```sh
cat >"$D/c" <<EOF
#!/bin/sh
exec env -u XDG_CACHE_HOME -u XDG_CONFIG_HOME -u XDG_STATE_HOME HOME="$D/home" "$D/venv-c/bin/rcm" "\$@"
EOF
chmod +x "$D/c"
mkdir -p "$D/home/.config/rcm"
printf 'server = "http://127.0.0.1:8791"\ntoken = "%s"\nlabel = "m5q-c"\n' "$TB" >"$D/home/.config/rcm/client.toml"
chmod 600 "$D/home/.config/rcm/client.toml"
git -C "$D/proj" init -q && echo hi >"$D/proj/a.txt" && git -C "$D/proj" add a.txt \
  && git -C "$D/proj" -c user.name=m5q -c user.email=m5q@example.invalid commit -qm init
"$D/c" version
```

기대: `rcm 0.7.0.dev0 (Python 3.11.<z>, darwin <arch>)` 한 줄(기록이 아직 없다, CB13).

### L1 같은 버전 (§7 의 1)

```sh
"$D/c" jobs --server http://127.0.0.1:8791; echo "exit=$?"
"$D/c" version
ls "$D/home/.cache/rcm/clients" 2>&1
```

기대:
- `jobs` 가 종료 0 이고 stderr 에 `following` · `not following` 이 없다.
- `version` 둘째 줄이 `follow: on (default) · server http://127.0.0.1:8791 v0.7.0.dev0 checked <n>s ago`(캐시가 비어
  `· cached` 토막이 없다는 제안, 「명세에 묻는 것」 8번).
- `clients` 가 없거나 비어 있다. B 의 `server.log` 에 `"GET /client/` 가 없다.

### L2 0.6.1 로 내려가기 (§7 의 2)

```sh
RCM_TOKEN=$TA "$D/c" check --server http://127.0.0.1:8790; echo "exit=$?"
ls -d "$D"/home/.cache/rcm/clients/0.6.1-*
cat "$D"/home/.cache/rcm/clients/0.6.1-*/.rcm-client.json; echo
stat -f '%Lp %Su' "$D/home/.cache/rcm" "$D/home/.cache/rcm/clients"
grep -c '"GET /client/remote_ci_monitor-0.6.1-py3-none-any.whl' "$D/a/server.log"
```

기대:
- stderr 첫 줄 `rcm: following server v0.6.1 (installed v0.7.0.dev0) — fetched <크기>`. 그 밖의 `rcm:` 경고 없음.
- stdout 에 0.6.1 의 옛 행 `ok   client        v0.6.1 · same as server` 가 꼬리 없이 있다(CG18).
- 폴더가 하나이고 이름의 뒤 8자가 L0d 서버 A health 의 `sha256` 앞 8자와 같다. 표지에 `version` `0.6.1`, `server`
  `http://127.0.0.1:8790`, 그 `sha256` 이 있다.
- 두 폴더가 `700 <사용자>`. `/client/` 요청 수가 1.

### L3 두 번째는 조용하다 (§7 의 3)

```sh
RCM_TOKEN=$TA "$D/c" check --server http://127.0.0.1:8790 2>"$D/out/l3.err"; wc -c <"$D/out/l3.err"
grep -c '"GET /client/' "$D/a/server.log"
h0=$(grep -c '"GET /api/health' "$D/a/server.log"); "$D/c" jobs --server http://127.0.0.1:8790 >/dev/null
h1=$(grep -c '"GET /api/health' "$D/a/server.log"); echo "health+$((h1-h0))"
```

기대: stderr 0 바이트, `/client/` 요청 수가 그대로 1. `check` 는 10분 기록을 쓰지 않고 health 를 새로 읽으므로(§2.3)
health 줄은 늘어도 맞다. 이어서 친 `jobs` 는 10분 기록을 쓰므로 `health+0` 이다(F1 · F3 의 실물).

### L4 0.6.1 코드가 제출한다 (§7 의 4)

```sh
cd "$D/proj" && RCM_TOKEN=$TA "$D/c" run demo --server http://127.0.0.1:8790 >"$D/out/l4.out"; echo "exit=$?"
JOB_A=$(tail -1 "$D/out/l4.out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'); echo "$JOB_A"
```

기대: 종료 0, 마지막 stdout 줄이 0.6.1 의 JSON 이고 `job_id` 와 `"wait_exit_code":0` 을 담는다. 서버 A 는 M5q 이전이라
`client … runs` 줄이 없다. 이것도 맞는 결과다.

### L5 다시 B, 그리고 스킬 (§7 의 5, A0 확인을 더함)

```sh
"$D/c" jobs; echo "exit=$?"
python3 -c 'import json,sys; print(sorted(json.load(open(sys.argv[1]))))' "$D/home/.cache/rcm/follow.json"
"$D/c" skills list
RCM_SERVER=http://127.0.0.1:8790 "$D/c" skills list
curl -s -o "$D/out/b.whl" http://127.0.0.1:8791/client/remote_ci_monitor-0.7.0.dev0-py3-none-any.whl
python3 -m zipfile -l "$D/out/b.whl" | grep -c 'remote_ci_monitor/skills/'
curl -s -o "$D/out/a.whl" http://127.0.0.1:8790/client/remote_ci_monitor-0.6.1-py3-none-any.whl
python3 -m zipfile -l "$D/out/a.whl" | grep -c 'remote_ci_monitor/skills/'
```

기대:
- `jobs` 가 설치본으로 돌고 stderr 가 비어 있다. `clients/0.7.0.dev0-*` 가 생기지 않는다.
- 기록의 키가 `['http://127.0.0.1:8790', 'http://127.0.0.1:8791']`.
- 기본 서버(B)의 `skills list` 가 다섯 줄, 줄마다 설명이 있다.
- A 를 가리킨 `skills list` 는 0.6.1 로 내려가 `rcm-qa-connect` · `rcm-release-driver` · `rcm-store-connect` 세 줄만
  설명 없이 나온다(CG10, 「명세에 묻는 것」 16번). `skills` 에는 `--server` 플래그가 없어 `RCM_SERVER` 로 가리킨다.
- B wheel 의 `skills/` 항목이 19, A wheel 이 4.

### L6 고정과 끄기 (§7 의 6, 갈래를 나눔)

```sh
before=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["http://127.0.0.1:8791"]["checked_at"])' "$D/home/.cache/rcm/follow.json")
RCM_CLIENT_VERSION=0.6.1 "$D/c" check --server http://127.0.0.1:8791; echo "exit=$?"            # L6a
after=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["http://127.0.0.1:8791"]["checked_at"])' "$D/home/.cache/rcm/follow.json")
[ "$before" = "$after" ] && echo record-untouched
RCM_TOKEN=$TA "$D/c" check --server http://127.0.0.1:8790 --no-follow; echo "exit=$?"            # L6b
RCM_TOKEN=$TA "$D/c" --no-follow check --server http://127.0.0.1:8790 >/dev/null; echo "exit=$?"
printf '# m5q\n\ninstalled\n' >"$D/home/.config/rcm/client-version"
RCM_TOKEN=$TA "$D/c" check --server http://127.0.0.1:8790; "$D/c" version                       # L6c
RCM_CLIENT_VERSION=follow RCM_TOKEN=$TA "$D/c" check --server http://127.0.0.1:8790 2>&1 | grep client   # L6d
printf 'lates\n' >"$D/home/.config/rcm/client-version"
"$D/c" jobs; echo "exit=$?"                                                                       # L6e
rm "$D/home/.config/rcm/client-version"
```

기대:
- **L6a** 캐시의 0.6.1 로 돈다. stderr 가 비어 있고 `record-untouched` 가 찍힌다(고정 경로는 서버에 묻지 않는다,
  §2.7 표 2). 0.6.1 의 옛 행 `warn  client        v0.6.1 · server v0.7.0.dev0 · older — pip install http://127.0.0.1:8791/client/remote_ci_monitor-0.7.0.dev0-py3-none-any.whl`, 종료 0(다른 행이 다 ok 일 때).
- **L6b** 두 자리 모두 설치본 dev0 으로 돈다. 행은 제안 `warn  client        v0.7.0.dev0 · server v0.6.1 · newer · not following (--no-follow)`.
- **L6c** 주석 줄과 빈 줄을 건너뛰고 `installed` 를 읽는다. 행 꼬리는 제안 `not following (~/.config/rcm/client-version)`,
  `version` 둘째 줄은 「명세에 묻는 것」 8번의 답대로.
- **L6d** 환경 변수가 파일을 이긴다. 캐시의 0.6.1 로 돌아 옛 행 `ok   client        v0.6.1 · same as server` 가 나온다.
  처음 받는 것이 아니므로 `following server` 줄은 없다.
- **L6e** 종료 2. stderr 에 `$D/home/.config/rcm/client-version` 경로와 `lates` 가 있다. 두 서버의 접근 로그에 이 명령의
  요청이 없다.

### L7 동시에 열 개 (§7 의 7)

```sh
rm -rf "$D/home/.cache/rcm"
pids=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  "$D/c" jobs --server http://127.0.0.1:8790 >"$D/out/j$i" 2>"$D/out/e$i" & pids="$pids $!"
done
fail=0; for p in $pids; do wait "$p" || fail=$((fail+1)); done; echo "failed=$fail"
ls -d "$D"/home/.cache/rcm/clients/0.6.1-* | wc -l
ls -A "$D/home/.cache/rcm/clients" | grep -cE '^\.(tmp|trash)-'
grep -l 'following server v0.6.1' "$D"/out/e* | wc -l
cat "$D"/out/e* | grep -v 'following server v0.6.1'
python3 -m json.tool "$D/home/.cache/rcm/follow.json" >/dev/null && echo json-ok
```

기대: `failed=0`, 폴더 1, 찌꺼기 0, 받은 줄을 찍은 프로세스는 1~10개(동시에 받은 프로세스는 각자 줄을 찍는다. 늦게
rename 한 쪽도 받기는 했다), 다른 stderr 없음, `json-ok`. 명세의 「폴더가 하나만 생기고」는 폴더 수이지 받은 횟수가
아니다.

### L8 서버가 꺼졌을 때 (§7 의 8, 바꿈)

명세대로 `rcm wait` 를 그냥 치면 그 명령이 연결 실패를 60초 동안 폴링한 뒤에 3 으로 끝난다(`client._poll_for_job`).
그러면 60초 음성 기록이 이미 지나 「60초 안에 한 번 더」를 볼 수 없다. 루프백에서 꺼진 포트는 연결이 바로 거절되므로
「3초를 기다리지 않는다」도 시간으로는 안 보인다. 그래서 `--timeout 5` 를 붙이고 기록의 `failed_at` 으로 본다. 3초
제한은 응답하지 않는 리스너(L8c)로 잰다.

L7 의 기록이 10분 안이면 따라가기는 health 를 부르지 않고 기록을 쓴다. 그러면 실패 경로를 지나지 않는다. 10분을
기다리거나 기록의 시각을 11분 앞으로 돌린다. 기록은 다시 만들 수 있는 캐시라 손대도 된다(§2.4).

```sh
kill "$(cat "$D/a/pid")"; sleep 1
python3 - "$D/home/.cache/rcm/follow.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["http://127.0.0.1:8790"]["checked_at"] -= 660
json.dump(d, open(p, "w"))
EOF
m0=$(stat -f %m "$D"/home/.cache/rcm/clients/0.6.1-*/.rcm-client.json)
"$D/c" wait --job "$JOB_A" --server http://127.0.0.1:8790 --timeout 5; echo "exit=$?"        # L8a
m1=$(stat -f %m "$D"/home/.cache/rcm/clients/0.6.1-*/.rcm-client.json); [ "$m1" -gt "$m0" ] && echo marker-touched
f1=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["http://127.0.0.1:8790"]["failed_at"])' "$D/home/.cache/rcm/follow.json")
"$D/c" wait --job "$JOB_A" --server http://127.0.0.1:8790 --timeout 5 2>/dev/null               # L8b, 60초 안
f2=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["http://127.0.0.1:8790"]["failed_at"])' "$D/home/.cache/rcm/follow.json")
[ "$f1" = "$f2" ] && echo no-second-health-attempt
```

기대:
- **L8a** stderr 에 `not following` 이 없다(기록과 sha 가 맞는 캐시로 조용히 돈다, §2.4 · F15). 0.6.1 의 `wait` 가 자기
  문장으로 서버에 닿지 못했다고 말하고 종료 3. `marker-touched`(exec 직전 utime, F49). `f1` 이 `null` 이 아니다.
- **L8b** `no-second-health-attempt`. 61초 뒤에 같은 명령을 치면 `failed_at` 이 바뀐다.

**L8c 3초 제한.** 연결을 받기만 하고 답하지 않는 리스너를 8792 에 둔다. 기록이 없는 주소다.

```sh
python3 -c 'import socket,time; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(("127.0.0.1",8792)); s.listen(64); time.sleep(900)' & BH=$!
time "$D/c" jobs --server http://127.0.0.1:8792
time "$D/c" jobs --server http://127.0.0.1:8792
kill "$BH"
```

기대: 두 번 모두 stderr 에 `unreachable` 경고 한 줄(R12, 문구는 CE7)과 `jobs` 자신의 실패. 첫 번째가 두 번째보다
약 3초 길다. 둘 다 `jobs` 자신의 제한(`Client` 기본 15초)이 들어 있다.

### L9 서버 로그 한 줄 (§7 의 9, 제출을 더함)

명세의 1~8 에는 B 로 가는 제출이 없어서 그대로는 줄이 생기지 않는다. 제출을 셋 더한다.

```sh
cd "$D/proj"
"$D/c" run demo >/dev/null; echo "exit=$?"
"$D/c" run demo >/dev/null
RCM_CLIENT_VERSION=0.6.1 "$D/c" run demo >/dev/null; echo "exit=$?"
curl -s -A 'rcm/9.9.9' -H "Authorization: Bearer $TB" http://127.0.0.1:8791/api/status >/dev/null
grep -F '[rcm] client ' "$D/b/server.log"
```

기대: 정확히 두 줄이 이 순서로 있다.

```
[rcm] client c-b runs v0.7.0.dev0
[rcm] client c-b runs v0.6.1
```

두 번째 dev0 제출은 줄을 늘리지 않고, `9.9.9` 는 제출이 아니라 줄이 없다(S4). 0.6.1 줄은 캐시의 0.6.1 코드가 자기
`User-Agent` 를 실었다는 증거다.

### L10 워커 (§7 의 10, PR B1 · B2)

워커는 dev1 venv 로 띄우고 `HOME` 은 C 와 같다. 워커의 `data_dir` 기본값도 그 `HOME` 아래로 간다.

```sh
cat >"$D/w" <<EOF
#!/bin/sh
exec env -u XDG_CACHE_HOME -u XDG_CONFIG_HOME -u XDG_STATE_HOME HOME="$D/home" RCM_WORKER_TOKEN="$TW" \
  "$D/venv-dev1/bin/rcm" worker --server http://127.0.0.1:8791 --pool m5q "\$@"
EOF
chmod +x "$D/w"
nohup "$D/w" >"$D/out/worker.log" 2>&1 & WPID=$!; sleep 5
ps -p "$WPID" -o command=
"$D/c" machines
```

**L10.1 기동 따라가기(①).** 기대: `worker.log` 첫머리에 아래 한 줄이 있다.

```
rcm: following server v0.7.0.dev0 (installed v0.7.0.dev1) — fetched <크기>
```

`ps` 의 명령 줄이 `-P -c` 로 바뀌었고 PID 는 `$WPID` 그대로다. `machines` 의 그 워커 버전이 `0.7.0.dev0`.
B 의 `server.log` 에 등록 409 가 없다(health 를 먼저 읽고 따라갔다).

**L10.2 서버 업그레이드 흉내.**

```sh
cd "$D/proj" && "$D/c" run slow --no-wait >"$D/out/l10.out"; echo "exit=$?"
JOB_W=$(tail -1 "$D/out/l10.out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
sleep 5; "$D/c" jobs | grep "#$JOB_W"            # 워커에서 running
kill "$(cat "$D/b/pid")"; sleep 2
nohup "$D/venv-dev1/bin/rcm" serve --config "$D/b/server.toml" --debug >>"$D/b/server.log" 2>&1 & echo $! >"$D/b/pid"
```

기대: 잡이 그 워커에서 `running` 이다. 새 서버의 health 가 `0.7.0.dev1` 이다. DB 는 같은 빌드라 마이그레이션이 없다.
명세의 「DB v23 그대로」는 M5p 위에 쌓은 빌드라면 v24 다(「명세에 묻는 것」 23번).

**L10.3 드레인과 재실행(③).**

```sh
"$D/c" wait --job "$JOB_W"; echo "exit=$?"
sleep 10; ps -p "$WPID" -o command=; "$D/c" machines; tail -n 20 "$D/out/worker.log"
```

기대:
- `wait` 가 종료 0 이다. 잡은 lost 가 아니라 `succeeded` 다(W6). C 는 이제 서버 dev1 을 따라가므로 이 `wait` 의 stderr
  에 `following server v0.7.0.dev1 (installed v0.7.0.dev0)` 이 한 번 나오는 것도 맞다.
- `worker.log` 에 드레인 시작, 잡 보고, 재실행의 순서가 보인다(문구는 B2 구현). 재실행 뒤 `following` 줄은 없다(설치본
  dev1 이 서버와 같다).
- PID 가 `$WPID` 그대로이고 명령 줄은 `restart_via_installed` 의 꼴이다. `machines` 의 워커 버전이 `0.7.0.dev1`.

**L10.4 되돌리기.** 명세 순서(서버를 dev0 으로 되돌린 뒤 워커 재시작)로 하면 기동 때 health 를 새로 읽는 ① 이 먼저
따라가므로 등록 409 의 ② 는 일어나지 않는다. 그래서 두 갈래로 나눈다.

```sh
# L10.4a 명세 순서: ① 이 따라간다
kill "$WPID"; sleep 3
kill "$(cat "$D/b/pid")"; sleep 2
nohup "$D/venv-dev0/bin/rcm" serve --config "$D/b/server.toml" --debug >>"$D/b/server.log" 2>&1 & echo $! >"$D/b/pid"; sleep 2
n0=$(grep -c 'POST /worker/register HTTP/1.1" 409' "$D/b/server.log")
nohup "$D/w" >>"$D/out/worker.log" 2>&1 & WPID=$!; sleep 5
n1=$(grep -c 'POST /worker/register HTTP/1.1" 409' "$D/b/server.log"); echo "409+$((n1-n0))"; "$D/c" machines

# L10.4b ② 를 일부러: 서버가 꺼진 채 워커를 띄우고 그다음에 서버를 띄운다
kill "$WPID"; sleep 3
kill "$(cat "$D/b/pid")"; sleep 2
nohup "$D/w" >>"$D/out/worker.log" 2>&1 & WPID=$!; sleep 5
nohup "$D/venv-dev0/bin/rcm" serve --config "$D/b/server.toml" --debug >>"$D/b/server.log" 2>&1 & echo $! >"$D/b/pid"
sleep 20; n2=$(grep -c 'POST /worker/register HTTP/1.1" 409' "$D/b/server.log"); echo "409+$((n2-n1))"; "$D/c" machines
```

기대:
- **L10.4a** `409+0`, 워커 버전 `0.7.0.dev0`. 캐시에 dev0 이 있으므로 `following` 줄 없이 따라간다.
- **L10.4b** 워커가 기동 때 서버에 닿지 못해 `register: … retrying in <n>s` 를 되풀이한다. 서버가 뜨면 `409+1` 이 되고 워커가
  `server_version` 을 보고 dev0 으로 따라가 다시 등록한다. 워커 버전 `0.7.0.dev0`. 종료 2 로 끝나지 않는다(W2).
  기동 때 health 를 못 읽은 워커가 기록의 캐시(dev0)로 바로 exec 하면 이 갈래는 409 없이 끝난다. 「명세에 묻는 것」 3번.

### L11 정리 (§7 의 11)

```sh
kill "$WPID" "$(cat "$D/b/pid")" 2>/dev/null; sleep 3
lsof -nP -iTCP:8790 -iTCP:8791 -iTCP:8792 -sTCP:LISTEN
rm -rf "$D"
ls -la ~/.cache/rcm ~/.config/rcm 2>&1 | diff "$D.before.txt" - && echo user-dirs-untouched
rm "$D.before.txt"
```

기대: `lsof` 출력 없음, `user-dirs-untouched`. 사용자의 진짜 `~/.cache/rcm` 과 `~/.config/rcm` 이 실기 앞과 같다.

## 문서 잠금 (CB22 의 글자)

`tests/test_docs_m5.py` 의 `read` · `has` · `paragraphs` · `unreleased` 를 쓴다. 문구는 제안이다. 구현이 다른 낱말을
고르면 이 표와 시험을 같이 고친다.

| 파일 · 자리 | 있어야 하는 글자 | PR |
|---|---|---|
| `docs/operating.md` 「Keeping clients on the server's version」 | 기존 잠금(`test_docs_client_wheel.py`)의 여덟 낱말 그대로: `client_wheel` · `min_client_version` · `/client/remote_ci_monitor-` · `sha256` · `pip install` · `rcm check` · `examples/session/update-client.sh` · `read_auth` | A |
| 같은 절, 따라가기 | `RCM_CLIENT_VERSION` · `--no-follow` · `client-version` · `~/.cache/rcm` · `following server v` · `not following`. `rcm version` 둘째 줄 예 `follow: on (default)` 와 `rcm check` 행 예 `following (installed v` | A |
| 같은 절, editable | `editable` 과 `installed` 가 한 문단(개발 체크아웃과 빌드 머신의 서비스 venv 는 기본으로 따라가지 않는다) | A |
| 같은 절, 설치본의 역할 | `examples/session/update-client.sh` 와 `bootstrap`(또는 `bootloader`)이 한 문단. 각 세션 머신이 한 번은 `0.8.0` 이상이어야 한다는 문장(§9 완료 기준 4) | A |
| 같은 절, 내려가기 | `usage error` 와 `--no-supersede` 가 한 문단(§2.7 끝 문단) | A |
| 같은 절, 도움말 | `--help` 와 `installed` 가 한 문장(§2.1 의 2) | A |
| 같은 절, 위험 | 한 문단에 `~/.ssh` 와 `gh`(또는 `GitHub token`). 한 문단에 `sha256` 과 `man-in-the-middle`(sha256 은 같은 통로로 와서 위조를 막지 못한다). 한 문단에 `.local` 과 `mDNS`(`auto` 를 쓰지 않아도 `.local` 이름은 mDNS 로 푼다) | A · 169 |
| 같은 절, 줄이는 법 · 끄는 법 | `Tailscale` 이 첫 권고로 나온다(절 안에서 `export RCM_CLIENT_VERSION=installed` 보다 앞). 끄는 법 셋 `export RCM_CLIENT_VERSION=installed` · `~/.config/rcm/client-version` · `--no-follow` 이 한 문단 | A · 169 |
| `docs/operating.md` 「Security notes」 | 따라가기를 말하는 불릿 하나에 `RCM_CLIENT_VERSION` 과 링크 `#keeping-clients-on-the-servers-version`. 첫 불릿의 「exposes nothing new」 는 내려받기에 대한 말이므로 그 불릿이나 새 불릿이 「세션 머신에서 실행된다」를 말한다(`runs` 또는 `executes`) | A |
| `docs/configuration.md` 「Client and worker files」 | `RCM_CLIENT_VERSION` 과 값 `follow` · `installed`. 경로 `$XDG_CONFIG_HOME/rcm/client-version` · `~/.config/rcm/client-version`. 우선순위 한 문장(`--no-follow`, `RCM_CLIENT_VERSION`, `client-version` 이 이 순서로 나온다). `client.toml` 에 이 키를 두지 않는다는 문장. `$XDG_CACHE_HOME/rcm` | A |
| `docs/usage.md` | `RCM_CLIENT_VERSION` · `--no-follow`. `--help` 와 `installed` 가 한 문장. 기존 링크 `operating.md#keeping-clients-on-the-servers-version` 그대로 | A |
| `docs/usage.ko.md` | 같은 내용의 한국어. `RCM_CLIENT_VERSION` · `--no-follow`. `--help` 와 `설치` 가 한 문장 | A |
| `README.md` | 「Keep the client current」 단계(지금 183줄)가 따라가기를 말한다. `follows the server` 와 `RCM_CLIENT_VERSION`. `update-client.sh` 는 처음 설치와 설치본 올리기에 쓴다는 말 | A(「명세에 묻는 것」 19번) |
| `README.ko.md` | 같은 단계의 한국어. `서버 버전을 따라` 와 `RCM_CLIENT_VERSION` | A |
| `CHANGELOG.md` | `unreleased()` 범위에 A0 의 `rcm skills` 와 `client wheel`(`### Fixed`). A 의 `RCM_CLIENT_VERSION` · `--no-follow` · `client-version` · `~/.cache/rcm` · `runs v`(서버 로그 한 줄) | A0 · A |
| `scripts/mutcheck.py` | 아래 「변이」 표의 이름이 `name="…"` 으로 있고 각 `tests=` 가 표의 파일을 가리킨다(`test_mutcheck_lists_the_exact_name_mutant` 와 같은 꼴) | A0 · A · B2 |

## 변이(mutcheck)

mutcheck 의 복사본에는 `docs/` · `examples/` 가 없다. 아래 변이를 잡는 시험은 모두 코드 시험이다. 이름은 제안이고
`m5o-…` 처럼 마일스톤을 앞에 붙였다. `old` 를 글자로 적을 수 있는 것은 A0 하나다. 나머지는 구현의 글자에 맞춘다.

| 이름 | 파일 | 변이 | 잡는 시험 | PR |
|---|---|---|---|---|
| `m5q-wheel-drops-skills` | `clientwheel.py` | `ASSET_DIRS = ("web", "templates", "skills")` 를 `("web", "templates")` 로 | CB1 · CB2(`tests/test_client_wheel.py`) | A0 |
| `m5q-follow-sha-unchecked` | `follow.py` | sha256 비교를 지운다 | F20 | A |
| `m5q-follow-editable-default-flipped` | `follow.py` | 기본값의 editable 판정을 뒤집는다 | F10 | A |
| `m5q-follow-boot-loop` | `follow.py` | `_follow_boot` 고리 방지를 지운다 | F31 | A |
| `m5q-follow-unpack-in-place` | `follow.py` | 임시 폴더 없이 대상 폴더에 바로 푼다 | F22 · F30 | A |
| `m5q-follow-serve-follows` | `follow.py` | `NO_FOLLOW_COMMANDS` 에서 `serve` 를 뺀다 | F37 | A |
| `m5q-follow-health-path-unchecked` | `follow.py` | health 의 `client_wheel.path` 검사를 지운다 | R ② · ⑦ | A |
| `m5q-worker-drain-skips-running-job` | `remote_worker.py` | 드레인이 도는 잡을 기다리지 않는다 | W6 | B2 |
| `m5q-client-log-before-auth` | `server.py` | 로그 한 줄을 `require_client_token` 앞으로 옮긴다 | CE2 · CE3 | A |
| `m5q-client-log-every-submit` | `server.py` | 본 쌍의 기억을 지운다 | CB9 · CG3 | A |
| `m5q-client-log-unchecked-agent` | `server.py` | User-Agent 의 버전 형식 검사를 지운다 | CE5 | A |
| `m5q-version-second-line-always` | `cli.py` | 정보가 없어도 `rcm version` 둘째 줄을 쓴다 | CB13 | A |
| `m5q-check-row-drops-follow-tail` | `cli.py` | `_client_row` 가 `state` 를 무시한다 | CB19 | A |

위 여덟 줄은 명세 §6 의 표 그대로이고 아래 다섯 줄은 이 문서가 더한 것이다. F · R · W 시험은 A · B 시나리오 문서의
몫이다.

## dolomood-renew 확인 항목 (§10, 시험 아님)

고치는 일은 그쪽 세션이 한다. 이 레포에서 그쪽 파일을 고치지 않는다. 아래는 v0.8.0 이 운영에 반영된 뒤 그쪽 세션이
볼 것이다. 두 번째 줄은 그쪽 `scripts/remote_ci.sh`(origin/dev)를 읽기만 해서 확인했다.

| # | 확인 | 어떻게 보나 | 기대 |
|---|---|---|---|
| D1 | `RCM_MIN_VERSION` · `RCM_WHEEL` 이 부트스트랩 전용이 된다 | 바닥을 한 번 0.8.0 으로 올린 뒤 서버를 0.8.1 로 올리고 `scripts/remote_ci.sh` 로 잡을 낸다 | 스크립트를 고치지 않아도 `rcm run` 이 `following server v0.8.1` 을 한 번 찍고 그 코드로 돈다 |
| D2 | `client_drift_hint` 가 조용해지나 | 그 함수는 `"${RCM}" --version` 으로 설치본 버전을 읽는다(origin/dev 736~748줄) | 명세 §10 은 「조용해진다」고 적었지만 `--version` 은 언제나 설치본이라(결정 166) 따라가는 중에도 「rcm 0.8.0 < 서버 0.8.1 — `--update-client`」 를 계속 찍는다. 「명세에 묻는 것」 20번 |
| D3 | `may_reinstall` · `venv_is_editable` | 맥미니의 운영 editable venv 에서 `rcm version` | 둘째 줄 `follow: off (editable install)`. 두 규칙이 그대로 맞물린다 |
| D4 | 맥미니의 `~/.local/bin/rcm` | 같은 링크로 `rcm version` | D3 과 같다. 운영 체크아웃 폴더를 바꾸지 않는다 |
| D5 | selftest ⑨-b | 바닥을 0.8.0 으로 올릴 때 | 가짜 rcm 의 `--version` 도 0.8.0 이상으로 함께 올린다 |
| D6 | `rcm-*-connect` 스킬이 부르는 플래그 | 서버를 옛 버전으로 내린 상태에서 스킬의 명령 | 새 플래그는 그 버전 파서의 사용법 오류(종료 2)가 된다(§2.7). 스킬 문서가 이 경우를 말하는지 본다 |
| D7 | 클라이언트 venv(`~/.local/share/rcm-client-venv`)는 wheel 설치다 | 세션 머신에서 `rcm version` | `follow: on (default)`. `~/.cache/rcm/clients/` 에 버전이 최대 셋 남는다 |
| D8 | 고정하고 싶은 세션 | 그쪽이 `RCM_CLIENT_VERSION` 을 `remote_ci.sh` 에 넣을지 | 그쪽 판단이다. 넣으면 D1 의 이득이 사라진다 |

## 명세에 묻는 것

명세는 고치지 않았다. 아래는 코드를 읽다가 명세와 어긋나거나 답이 없던 자리다. 각 줄의 「제안」은 위 시나리오가
일단 가정한 답이다.

1. **§7 의 9 는 제출이 없어서 성립하지 않는다.** 1~8 에서 서버 B 로 가는 것은 `jobs` · `check` 뿐이고 로그 한 줄은
   `POST /jobs` 에서만 남는다. 제안: L9 처럼 B 에 `run demo` 를 둘, 고정 0.6.1 로 하나 더 낸다.
2. **§7 의 8 은 관찰할 수 없다.** `rcm wait` 는 연결 실패를 60초 폴링한 뒤 3 으로 끝나서 두 번째 호출이 60초 음성
   기록 밖에 떨어진다. 루프백의 꺼진 포트는 연결이 바로 거절돼 3초 제한도 시간으로 안 보인다. 제안: `--timeout 5` 와
   `follow.json` 의 `failed_at` 으로 보고(L8a · L8b), 3초는 응답하지 않는 리스너로 잰다(L8c).
3. **§7 의 10.4 는 ② 가 아니라 ① 을 본다.** 워커를 다시 띄우면 기동 때 health 를 새로 읽는 ① 이 먼저 따라간다.
   ② 를 보려면 서버가 꺼진 채 워커를 띄워야 한다(L10.4b). 그런데 그때 ① 이 health 를 못 읽으면 §2.4 의 「기록과 sha 가
   맞는 캐시로 돈다」가 워커 기동에도 걸리는가. 걸리면 서버가 꺼진 동안 워커가 옛 캐시 버전으로 exec 하고, 이 갈래는
   409 없이 끝난다. 제안: 워커 기동(fresh)은 기록 대체를 쓰지 않고 설치본으로 등록을 기다린다.
4. **`HOME` 만으로는 격리되지 않는다.** `XDG_CACHE_HOME` · `XDG_CONFIG_HOME` · `XDG_STATE_HOME` 과 `RCM_*`(운영 가드가
   보는 `RCM_CONFIG` · `RCM_SERVER_DATA_DIR` 포함)가 `HOME` 을 이긴다. 제안: L0a 의 검사와 `env -u` 를 쓰는 실행 파일
   (L0e).
5. **dev0 과 dev1 은 오늘의 버전 비교에서 같다.** `cli._version_key` 는 숫자가 아닌 마디에서 끊어 `0.7.0.dev0` 과
   `0.7.0.dev1` 이 둘 다 `(0, 7, 0)` 이다. 따라가기는 문자열로 비교하므로(결정 150) dev0 을 dev1 서버에 `--no-follow` 로
   붙이면 `rcm check` 가 `same as server` 라고 틀리게 말한다. `rcm run` 의 옛 클라이언트 경고도 같다. 제안: `_client_row`
   의 「같다」는 문자열 비교로 하고 `_version_key` 는 바닥 비교에만 쓴다. 아니면 실기 빌드를 `0.7.1` · `0.7.2` 처럼
   숫자로 가른다.
6. **`rcm check` 행의 칸.** §2.9 는 `ok    client`(ok 뒤 공백 넷)로 적었지만 코드는 `ok   client`(셋)를 찍고 기존 시험이
   그것을 잠근다. 제안: 코드 모양을 따른다.
7. **`client` 행 꼬리의 빠진 갈래.** 명세의 예는 넷뿐이다. `--no-follow`, `installed` 를 환경 변수나 파일로 준 경우,
   source 설치, 파일로 고정, 실패 코드마다의 꼬리가 없다. `no_wheel` · `old_server` 는 오늘의 힌트
   `— no client wheel from this server (…)` 와 이유가 겹친다. 제안: CG15 의 문구, 그리고 두 코드에서는 힌트를 빼고 꼬리만
   남긴다(CE9 · CE10).
8. **`rcm version` 둘째 줄의 나머지.** 기본 서버가 비었거나 `auto` 일 때(발견하지 않으므로 서버를 모른다), `failed_at`
   이 있을 때, 캐시가 비었을 때, 같은 버전 폴더가 둘일 때, `installed` 를 환경 변수나 파일로 준 때의 문구가 없다.
   editable 인데 기록이 있을 때 서버 토막을 붙이는지도 없다. 제안: 서버를 모르면 `follow: on (default) · no server
   configured`, 실패는 CG13, 빈 캐시는 토막 생략, 버전은 한 번만, `installed` 는 `follow: off (RCM_CLIENT_VERSION)` ·
   `follow: off (~/.config/rcm/client-version)`, editable 은 서버 토막 없음.
9. **`--json` 의 `follow` 는 늘 있는가.** 기존 `test_version_json` 이 키 집합을 잠그므로 어느 쪽이든 그 시험을 고친다.
   제안: 늘 있고 모르는 값은 `null`, `cached` 는 버전 문자열 목록(같은 버전은 한 번).
10. **`rcm version` 과 틀린 설정.** `version` 은 따라가지 않는 명령인데 §2.2 는 틀린 값을 종료 2 라고 했다. 사람이 무엇이
    잘못됐는지 보려고 치는 명령이 2 로 끝나면 진단을 못 한다. 제안: CE11 의 둘째 줄과 종료 0.
11. **실패 경고의 `v<Y>`.** `unreachable`(기록 없음)이면 서버 버전을 모르고, `bad_health` 면 서버가 보낸 값이 형식 검사에
    걸린 값이다. 그 값을 찍으면 터미널 제어 문자가 그대로 나간다. 제안: 두 코드는 `rcm: not following <서버 주소>: …`
    꼴로 쓰고 서버가 보낸 값은 찍지 않는다(CE6 · CE7).
12. **받은 크기의 눈금.** §2.5 의 예 `fetched 707 KB` 는 707,673 바이트를 버림으로 적었다. 레포의 `render_text._bytes`
    는 `707.7 KB` 를 낸다. 제안: `_bytes`.
13. **로그 한 줄의 자리와 거르기.** 토큰 검사 바로 뒤인가 제출이 받아들여진 뒤인가(CE4). `User-Agent` 가 `rcm/` 이
    아니거나 버전 형식이 틀리면 줄을 안 쓰는가 `unknown` 으로 쓰는가(CE5). 유효한 토큰 하나가 서로 다른 버전 문자열을
    계속 보내면 기억과 로그가 끝없이 는다. 상한을 두는가. 제안: 토큰 검사 바로 뒤, `VERSION_RE` 를 통과한 값만, 쌍
    1,000개를 넘으면 한 줄로 알리고 더 쓰지 않는다.
14. **health 는 그대로라고 읽었다.** §5.3 은 `/api/health` 에 키를 더하지 않는다고 적었다. 이 문서는 키가 늘지 않았는지와
    진짜 health 가 진짜 `parse_health` 를 통과하는지만 본다(CB7 · CG7). 다른 뜻이었으면 알려 달라.
15. **실행 비트.** 저장소에서 100755 인 템플릿이 넷이다. hatchling 이 만든 릴리스 wheel 은 그 비트를 싣고 서버 wheel 은
    모두 0o644 로 싣는다. pip 는 zip 의 실행 비트를 설치에 옮기므로 두 설치 결과가 파일 모드에서 다르다. 「같아야 하는
    것은 설치 결과다」에 모드가 드는가. 제안: 서버 wheel 도 실행 비트를 옮기고 CG9 가 잠근다. 모드를 빼기로 하면 그 사실을
    시험 주석에 적는다. `rcm skills install` 이 `write_bytes` 로 모드를 버리는 것은 오늘도 있는 별개의 일이다.
16. **A0 이전 서버를 따라가면 `rcm skills` 가 줄어든다.** 0.6.1 서버를 가리키면 따라간 자식의 `skills list` 가 세 줄만
    설명 없이 보인다(L5). 설치본에는 다섯이 있다. 제안: 받아들이고 operating 의 따라가기 절에 한 줄로 적는다. 버전 크기를
    비교하지 않는 규칙(결정 150) 때문에 「A0 이상일 때만 따라간다」는 만들 수 없다.
17. **`skills` 뒤의 `--no-follow`.** `skills` 하위 파서에는 `client_opts` 가 없어서 `rcm skills list --no-follow` 는 설치본
    파서의 사용법 오류다. F7 은 두 자리를 다 받는다고 했다. 제안: `--no-follow` 를 따라가는 모든 명령의 하위 파서에 둔다.
18. **설치 스모크와 시험의 하위 프로세스가 진짜 캐시에 쓴다.** `scripts/smoke_install.sh` 는 새 venv(wheel 설치라 기본
    `follow`)로 `rcm check` · `rcm run ok` 을 사용자의 `HOME` 에서 돈다. 같은 버전이라 받지는 않지만 `~/.cache/rcm/follow.json`
    을 쓴다. CI 러너도 같다. 제안: 스모크가 `XDG_CACHE_HOME="$WORK/cache"` 를 건다. 새 venv 의 `rcm` 을 하위 프로세스로
    부르는 시험(`test_client_wheel` · `test_docs_client_wheel`)은 conftest 의 환경을 물려받는지 확인한다.
19. **README 는 어느 PR 인가.** §9 는 README 의 한 줄을 PR C 에 두었다. 그런데 사용자에게 보이는 변경은 같은 PR 에서
    문서를 고친다는 집안 규칙이 있고 README 183줄은 지금 「`update-client.sh` 로 올린다」고 말한다. 제안: README 두 언어도
    PR A 가 고치고 `tests/test_docs_m5q.py` 가 잠근다.
20. **dolomood 의 `client_drift_hint` 는 조용해지지 않는다.** §10 표는 「따라가면 차이가 없으니 조용해진다」고 적었다.
    그 함수는 `rcm --version` 을 읽고 `--version` 은 언제나 설치본 버전이다(결정 166). 제안: §10 표를 「따라가는 중에도
    설치본 버전으로 계속 알린다. 그쪽이 `rcm version --json` 의 `follow` 를 읽거나 함수를 뺀다」로 고친다.
21. **`check` 의 경고와 꼬리.** `check` 는 따라가기 실패를 stderr 한 줄과 행 꼬리로 두 번 말한다. 제안: 둘 다 두되 같은
    이유 문구를 쓴다(CG21).
22. **처음 받을 때의 줄이 동시 실행에서 여럿.** §2.5 는 「처음 받을 때만」 한 줄이라 했다. 열 프로세스가 동시에 받으면
    받은 프로세스마다 한 줄이다(L7). 늦게 rename 한 쪽은 자기 것을 버리고 남의 폴더를 쓰는데 그래도 `fetched` 라고 찍는가.
    제안: 받은 바이트가 있으면 찍는다.
23. **§7 표의 「DB v23 그대로」.** A 는 M5p P3 위로 rebase 하므로(결정 173) 실기 빌드의 DB 는 v24 다. 같은 빌드끼리라
    마이그레이션이 없다는 뜻은 그대로이니 숫자만 고치면 된다.
