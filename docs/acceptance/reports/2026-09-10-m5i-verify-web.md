# M5i 검증 보고 — 웹 렌더 · 호스트 표본 (V1) · CHANGELOG (V7.2)

- 날짜: 2026-09-10
- 검증한 dev: `ee22379` (`fix(gc): 오프라인 dry-run 이 운영 DB 를 마이그레이션하던 것 — 임시 사본 위에서 돈다 (#87)`)
- 명세: `docs/acceptance/m5i-verification-scenarios.md` §1 V1 · V7.2 (§0 규칙대로)
- 워크트리: `remote_ci_monitor-verify-web-render-smoke` (`docs/acceptance-m5i-verify-web-render` — 룰셋의 타입 집합에 `verify` 가 없어 `docs` 로) · 자기 `.venv` (Python 3.11)
- 시험 서버: 127.0.0.1:**8794** · 설정 파일은 세션 스크래치 · `data_dir = "~/.local/share/rcm-verify-web"` (`~` 그대로) · `advertise = false` · 알림 훅 없음 · 프리셋 `ok` 하나. 끝나고 서버를 내리고(`kill 15403`) `data_dir`(212 KB) 을 지웠다.
- Chrome: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` — `tests/test_web_browser.py` 의 `Chrome` 클래스(CDP 파이프)와 `page_errors()` 를 그대로 썼다.

## 판정표

| ID | 판정 | 실측 | 명령/비고 |
|---|---|---|---|
| V1.1 | **통과** | `pools[0].hosts[0]`: `source == "local"`, `disk == {"used_bytes": 340411105280, "free_bytes": 654251479040, "total_bytes": 994662584320, "path": "/Users/fmmentalcare/.local/share/rcm-verify-web"}` — `path` 가 `~` 를 푼 절대경로. 기동 로그도 `data /Users/fmmentalcare/.local/share/rcm-verify-web` | `rcm serve --config <scratch>/verify-web.toml` → `curl -s http://127.0.0.1:8794/api/status` (첫 표본까지 5초 폴링). `server.job_storage` 도 있었다(`volume_bytes 0`, `measured_at 03:57:28Z`, `next_sweep_at 04:57:28Z`) |
| V1.2 | **통과** | 두 언어 모두: 서버 카드의 `.meter[data-metric="disk"]` **1개** · 회계 줄 en `rcm data 0 KB of 107 GB · measured 53s ago · next sweep 59m 07s` / ko `rcm 데이터 0 KB / 107 GB · 54s 전 측정 · 다음 청소 59m 06s` · Recent 에 `#2`·`#1`(둘 다 `succeeded`/`성공`) · `page_errors() == []` · 본문에 `undefined`·`NaN` 없음 | 잡: `rcm token --config … add verify` 뒤 `rcm run ok --server http://127.0.0.1:8794 --token … --dir <파일 하나> --no-join --poll` 두 번 → #1·#2 `succeeded` exit 0. 페이지: `/?poll=1&lang=en`·`lang=ko` 를 `Chrome` 으로 열고 `#host .hostcard .substat` 과 `#recent [data-job]` 이 있을 때까지 대기 |
| V1.3 | **통과** | 카드 둘 `["epeuem-ui-Macmini", "build-02 · pool linux"]`(ko `build-02 · 풀 linux`) · 디스크 막대 카드마다 1개(합 2) · `.substat` 이 있는 카드는 `["epeuem-ui-Macmini"]` 뿐(회계 줄은 서버 카드에만) · `page_errors() == []` · `undefined`·`NaN` 없음 | `rcm token --config … add --worker build-02` → `POST /worker/register` `{pool: linux, lanes: 1, host_name: build-02.local}` 200 → `POST /worker/heartbeat` `{host_sample: tests/test_worker_api.SAMPLE + disk(path /var/lib/rcm)}` 200 → `/api/status` 의 `pools[1].hosts[0].source == "worker"` 확인 → 같은 두 페이지를 다시 열어 읽음 |
| V1.4 | **통과** | 종료 코드 **1**(skip 아님). 메시지: `RCM_CHROME='/nonexistent' is neither a file nor a name on PATH — Chrome is required where RCM_CHROME is set, so this is a failure, not a skip`. pytest 는 이것을 autouse 픽스처의 setup **ERROR** 로 보고한다(`FAILED` 가 아니라 `ERROR`) — 잡은 빨갛고 skip 이 아니므로 명세의 뜻은 지켜진다 | `RCM_CHROME=/nonexistent ./.venv/bin/pytest tests/test_web_browser.py -q -k local_host_card` |
| V1.5 | **통과** | `ci.yml`: ubuntu 행 둘 `{ os: ubuntu-latest, python: "3.11"/"3.13", chrome: google-chrome }` · pytest 스텝 `env: RCM_CHROME: ${{ matrix.chrome }}` · macOS 행은 비움. dev 마지막 완료 CI(run 34434788630, c3982e8): `unit (ubuntu-latest, 3.11, google-chrome)` **3313 passed in 298.45s** · `unit (ubuntu-latest, 3.13, google-chrome)` **3313 passed in 411.26s** — 요약 줄에 `skipped` 없음(로그에서 `skipped` 가 걸리는 유일한 줄은 `node --test` 의 `# skipped 0`) | `gh run list --branch dev --limit 3` · `gh run view 34434788630 --json jobs` · `gh run view --job 102737495462 --log \| grep -E "[0-9]+ passed"` (3.13 은 job 102737495439). ee22379 의 run 34435104918 은 확인 시점에 아직 `in_progress` |
| V1.6 | **통과** | `?poll=1&lang=ko` 로 **44.8초** 열어 둠. 헤더를 2.7s · 37.7s · 44.7s 에 읽음: `폴링 · 0s 전 갱신` → `폴링 · 5s 전 갱신` → `폴링 · 2s 전 갱신`(계속 갱신) · 세 번 모두 본문에 `연결이 끊겼습니다`/`lost connection` 없음 · `page_errors() == []` · 끝에도 Recent 에 `#2`·`#1` | `tests/test_web_browser.Chrome` 으로 열고 `time.sleep(35)` + `time.sleep(7)` 사이에 `header.innerText` 와 `document.body.innerText` 를 읽음. 헤더의 `build-02/1 끊김` 은 V1.3 의 가짜 워커가 heartbeat 를 그친 것(worker_timeout 60초) — 기대대로 |
| V7.2 | **오류 → 수정(PR #91)** / 일부 확인 못 함 | PR 1 #82(「The web page came up broken on any server with a disk sample」) · 1b #84(「… when `data_dir` was written with `~`」) · 2 #87(오프라인 dry-run · 백업 · v16 라벨 정정, Fixed 세 항목) · 3 #88(Changed 둘 + Fixed 셋) 의 항목이 있고 B1·B2·v16 은 기계·잡 번호 없이 일반화돼 있다. **오류:** PR 3 의 Changed 항목 둘(31·37행)이 `[#84](…/pull/88)` — 글자는 #84, 주소는 #88. **확인 못 함:** PR 6 의 항목 — PR 6(#90, 서버가 클라이언트 wheel 을 준다)이 검증 시점에 아직 **열려 있어** dev 에 없다 | `python3` 로 `\[#(\d+)\]\(…/pull/(\d+)\)` 44개를 훑어 불일치 2개. 수정은 아래 「오류와 수정」 |

## 오류와 수정

| ID | 무엇 | 수정 PR |
|---|---|---|
| V7.2 | CHANGELOG `[Unreleased]` Changed 의 회계 항목 둘(「`rcm gc --dry-run` says what deleting would actually give back」 · 「The storage line says how old its number is」)의 링크 글자가 `#84`(PR 1b) 인데 주소는 `pull/88`(PR 3). 글자를 `#88` 로 맞추고, `tests/test_changelog_pr_links.py` 가 CHANGELOG 의 모든 PR 링크에서 글자 번호 == 주소 번호를 잠근다(고치기 전 31·37행으로 빨갛다) | [#91](https://github.com/monocsp/remote_ci_monitor/pull/91) — 머지 커밋 483d130 |

코드(서버·웹) 오류는 없었다 — V1.1~V1.6 전부 실제 모양의 입력으로 실제 경로를 지났다.

## 확인 못 함

| ID | 무엇 | 이유 |
|---|---|---|
| V7.2 (PR 6 항목) | `[Unreleased]` 에 PR 6 의 항목 | 검증 시점(dev `ee22379`)에는 [#90](https://github.com/monocsp/remote_ci_monitor/pull/90) 이 미머지였다. 머지(41ce480) 뒤 다시 봤다: 「The server hands out its own client.」 항목이 Added 에 있고 링크 글자·주소가 `#90` 으로 일치한다 — **통과**(코디네이터 세션이 확인) |

## 비고

- V1.2·V1.3 의 회계 줄이 `0 KB` 인 것은 청소기가 기동 직후(잡 전에) 한 번 잰 값이라서다 — 측정된 0 이지 `—` 가 아니다. 다음 sweep(1시간) 전까지는 그대로다. 명세 밖이라 판정에 넣지 않았다.
- V1.4 의 `ERROR`(setup) 대 `FAILED`: 「skip 이 아니라 실패」의 뜻은 지켜지고 종료 코드도 1 이다. `FAILED` 를 꼭 원하면 픽스처가 아니라 테스트 본문에서 `pytest.fail` 을 부르도록 바꾸면 되지만, 그러면 모듈의 다른 테스트마다 같은 검사를 되풀이해야 해서 지금 모양이 낫다고 본다 — 오너 판단.
