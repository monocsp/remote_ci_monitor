# M5p 테스트 시나리오 B — 보관함의 서버 · HTTP · 보안 (PR P2, 2026-11-19)

`docs/m5p-workplan.md` 의 §4.2(보관 · copier · ack 미루기) · §4.3(시계) · §4.4(tar 펼치기) · §4.6(HTTP) ·
§4.7(페이지) · §4.8(상태 JSON) · §5.3(기동 화해)과 §7 의 P2 목록을 표로 옮겼다. 결정은 125~148 이다.
`server.py` · `remote_workers.py` · `remote_worker.py` · `collect.py` · `client.py` 를 읽으며 찾은 경우는
근거 칸에 「코드」와 위치를 적었다. 구현보다 먼저 쓴다(test-first). P1 의 순수 규칙(`stage` · `touches` ·
`should_touch` 표 전수)은 시나리오 A 가 잠그고, 여기서는 그 규칙이 실제 HTTP 와 스레드를 거쳐 나오는지만 본다.

| 약칭 | 파일 | 무엇을 잠그나 |
|---|---|---|
| srv | `tests/test_server_m5p.py` | `ShelfCopier` · ack 미루기 · 경로 · 시계 · `POST /jobs` keep · `POST /jobs/N/keep` · 상태 JSON |
| sec | `tests/test_server_m5p_security.py` | 경로 디코딩 · 헤더 · 인라인 허용 목록 · Host 421 · 되살리기 Origin · HTML 이스케이프 |
| rem | `tests/test_server_m5p_remote.py` | 실제 `RemoteWorker` → 업로드 → finish 경로 · 전송 슬롯 · 원격 경쟁 창 |
| start | `tests/test_server_m5p_start.py` | `App.start` 의 두 화해 호출과 순서 · 재시작 뒤 입양 |
| web | `tests/test_web_browser_shelf.py` | 실제 Chrome(CDP) 으로 되살리기 버튼 · 교차 출처 form · sandbox 문서 |
| lock | 기존 `tests/test_compat_m5e.py` · `tests/test_status_schema.py` | 공개 키 집합에 `keep` · `shelf` · `shelf_storage` 를 더한다 |

## 공통 준비

- **서버**: `test_worker_api.WorkerServer(tmp_path, workers=True, **SHELF_CFG)`(122행). 주입 시계 `srv.clock`
  이고 `T0 = 2026-09-06 12:00Z` 다. `workers=True` 여야 `App.start` 가 돌아 copier 스레드가 뜬다. 기본값
  (`workers=False`)으로는 copier 가 없으니 보관을 기다리는 시험은 전부 `workers=True` 로 띄운다.
- `SHELF_CFG` 는 `test_server_m5e.ARTIFACT_CONFIG` 를 바탕으로 다음을 바꾼다. `max_artifact_bytes = 1 MiB`,
  `max_artifact_files = 8`, `max_snapshot_bytes = 64 MiB`(트리에 픽스처 tar 를 싣는다. 기본 10 000 이면 413),
  `shelf_max_bytes = 64 MiB`, `min_free_bytes = 0`(CI 디스크가 10 GiB 바닥보다 작을 수 있다),
  `retention_sweep_interval_seconds = 3600`(청소기가 시험 사이에 끼지 않게. 청소 회차를 보는 시험만 1초).
- **프리셋**(`test_server.sh` 로 만들고 P1 의 `keep` 키를 싣는다).
  - `keepgold`: `keep = true`, `artifacts = ["out/*"]`. 스크립트는 아래와 같다. macOS bsdtar 가 `._*`
    AppleDouble 멤버를 끼우지 않게 `COPYFILE_DISABLE=1` 을 둔다.

    ```sh
    set -e; export COPYFILE_DISABLE=1
    mkdir -p out test/feature/home/goldens
    cp fixtures/home.png out/a.png
    cp fixtures/home.png test/feature/home/goldens/home.png
    tar -czf out/goldens.tar.gz test/feature/home/goldens/home.png
    printf '{"changed":1}' > out/manifest.json; echo done > out/run.log
    ```
  - `plaingold`: 같은 스크립트, `keep` 없음. `failkeep`: `keep = true` · `artifacts_on = "failure"` · 끝에
    `exit 3`. `noart`: 글롭 없음. `linkeep`: `keepgold` 와 같고 `pool = "linux"`(원격 전용).
  - `copyfix`: `keep = true`, 스크립트 `mkdir -p out && cp fixtures/* out/`. 악의적 tar 를 **실제 로컬
    레인과 `collect`** 로 묶음에 넣는 통로다. 픽스처는 트리 tar(`test_server.make_tar`)에 싣는다.
- **트리**: `make_tar({"fixtures/home.png": PNG_1PX, ...})` 를 `PUT /jobs/N/tree` 로 올린다. `PNG_1PX` 는
  67바이트짜리 1×1 PNG 상수다.
- **기다리기**: `wait_shelf(srv, jid, states, timeout=5)` 가 `GET /api/shelf/N` 을 50 ms 간격으로 읽는다.
  이 경로는 시계를 돌리지 않는다(§4.3). sleep 으로 맞추지 않는다.
- **copier 붙잡기**: 내부 이름이 정해지지 않았으므로 §6 에 고정된 `Store.claim_shelf_item` 을
  monkeypatch 로 감싸 `threading.Event` 를 기다린 뒤 원래 함수를 부르게 한다.
- **원격 실제 경로**(M5g §13 D: 손으로 만든 claim · finish payload 금지): 같은 프로세스에서
  `RemoteWorker(WorkerConfig(server=url, token=srv.tokens["build-02"], pool="linux", lanes=1,
  data_dir=str(tmp_path / "w")), once=True)` 를 스레드로 `run()` 한다. `client` 를 넘기지 않으니 진짜
  `WorkerClient` 가 claim · tree · 업로드 · finish 를 HTTP 로 한다. 이 배치는 레포에 선례가 없다
  (`test_worker_m5e.py` 는 가짜 클라이언트, `test_artifacts_on.py` 는 claim payload 만 본다). 같은 프로세스로
  안 서면 선례가 있는 `test_e2e_worker.py` 의 `make_stack`(진짜 `rcm serve` + `rcm worker`)에 `linkeep`
  프리셋을 더해 같은 단언을 한다.
- **악의적 워커**(E50 · E53): 바깥 묶음 자체를 망가뜨리는 경우만은 tar 를 손으로 만든다. 그래도 올리는
  길은 진짜다. `WorkerClient.upload_bundle`(`client.py:210`)과 `WorkerClient.finish` 를 그대로 부른다.
- **브라우저**: `test_web_browser.Chrome` 과 `find_chrome` 을 가져오고 그 파일의 `pytestmark`(Chrome 이 없으면
  skip, `RCM_CHROME` 이 있으면 실패)를 그대로 둔다.
- 날짜 기준은 `T0 + 182d = 2027-03-07 12:00Z`(정리 대기), `T0 + 365d = 2027-09-06 12:00Z`(삭제)다.

## 베스트

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| B1 | 로컬 레인 keep 잡이 보관된다 | `keepgold` 제출 · 업로드 → 로컬 레인이 돌고 끝남 | `wait_shelf` 가 `active`. `<shelf_dir>/N/files/out/{a.png,goldens.tar.gz,manifest.json,run.log}` 와 `files/out/goldens.tar.gz.d/test/feature/home/goldens/home.png` 가 있다. `<shelf_dir>/N/manifest.json` 의 `files` 는 다섯 줄이고 각 `sha256` 이 디스크 바이트와 같다. 펼친 줄만 `expanded_from = "out/goldens.tar.gz"`. `.staging` 은 비었다 | §4.2 ①~⑥ · §4.4 · §5.2 · 결정 129·133 | srv |
| B2 | 항목 페이지 | B1 뒤 `GET /shelf/N/` | 200, `text/html; charset=utf-8`. CSP 가 정확히 `default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'`, `Referrer-Policy: same-origin`. 본문에 `<script` 가 없다. 그림 격자에 `src="/shelf/N/f/out/a.png"` 와 펼친 PNG 가 있고 「zip 으로 받기」 링크는 `/shelf/N.zip`. 펼친 줄은 원본 tar 줄 바로 밑에 있다. 지우기 버튼(DELETE · form)은 없다 | §4.7 · 결정 141·146 | srv |
| B3 | 그림 파일 받기 | `GET /shelf/N/f/out/a.png` | 200, `image/png`, `Content-Length: 67`, `ETag` 는 sha256 앞 16자, `Content-Disposition: inline; …`. CSP 가 정확히 `sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: private, max-age=300`. 본문 = `PNG_1PX` | §4.6 · 결정 139 | srv |
| B4 | zip 받기 | `GET /shelf/N.zip` | 200, `Connection: close`. `zipfile` 로 열면 모든 항목이 `ZIP_STORED` 이고 이름 집합 = manifest `files[].path` 집합이며 내용의 sha256 이 manifest 와 같다. 한글 이름 항목은 `flag_bits & 0x800` 이 켜져 있다 | §4.6 · 결정 148 | srv |
| B5 | 304 | B3 의 ETag 로 `If-None-Match` | 304, 본문 없음, `ETag` 같음 | §4.6 크기와 시간 | srv |
| B6 | **원격 워커** 잡이 같은 결과 | `linkeep` 을 공통 준비의 실제 `RemoteWorker(once=True)` 로 돌린다. 서버의 `Store.finish` 를 감싸 부르는 순간 `Store.shelf_item(N)` 을 찍는다 | finish 직전 찍힌 행이 `pending` 이고 잡은 그때 `running` 이다. 끝난 뒤 `active`, 파일 다섯 줄이 B1 과 같다. `job_state = succeeded`, `job_finished_at` 이 워커가 보낸 `finished_at` 이다(수신 시각이 아니다) | §4.2 · §7 P2 · 결정 129 · M5g §13 D | rem |
| B7 | ack 경쟁(로컬) | copier 를 붙잡고 B1 을 돌린다. 묶음이 `ready` 가 되자 alice 가 `POST /jobs/N/artifacts/ack` | ack 200, `purged: false`. `<data_dir>/artifacts/N/bundle.tar` 가 그대로 있다. 붙잡기를 풀면 `active` 가 되고, 그 뒤 묶음이 지워져 `GET /jobs/N/artifacts` 가 `purged` 이고 `artifacts_changed`(`state = purged`) 이벤트가 한 번 나간다 | §4.2 ack 미루기 · 결정 130 | srv |
| B8 | ack 경쟁(원격, 실제 클라이언트) | B6 을 copier 붙잡은 채로 돌리고, `client.Client` 로 `rcm run --fetch-artifacts` 와 같은 순서(아카이브 받기 → ack)를 부른다 | 받은 tar 의 바이트가 업로드한 묶음과 같다. ack 는 미뤄지고, 보관이 끝난 뒤 묶음이 지워진다 | §4.2 · 결정 130 · §1.1 | rem |
| B9 | 전송 슬롯 | `keepgold` 변형으로 4 MiB 무작위 파일 넷을 담은 항목을 만든다(`max_artifact_bytes = 32 MiB`). 연결 넷이 `GET /shelf/N.zip` 을 보내고 헤더만 읽은 채 멈춘다. M5e `max_concurrent_artifact_transfers = 2` 그대로 | zip 넷이 모두 200 으로 시작한다. 다섯 번째 zip 은 503 + `Retry-After: 5`. 그동안 실제 `RemoteWorker` 의 `linkeep` 잡 업로드가 201 이고 묶음 `ready` · 보관 `active` 가 된다(mutcheck: 보관함이 M5e 슬롯을 쓰면 셋째 zip 이나 업로드가 503) | §4.6 전송 슬롯 · 결정 148 · 코드 `server.py:5576` | rem |
| B10 | 제출에 keep | `keepgold` 대신 `plaingold` 에 본문 `"keep": true` | 201, 본문에 `keep: true` · `shelf_url = "http://127.0.0.1:PORT/shelf/N/"`. `GET /jobs/N` 이 `keep: true` | §4.1 · 결정 126·127 | srv |
| B11 | 프리셋 keep | `keepgold` 를 본문 keep 없이 | 201, `keep: true` | §4.1 · 결정 126 | srv |
| B12 | 합류가 keep 을 켠다 | alice 가 `plaingold` 를 keep 없이, bob 이 같은 트리로 `"keep": true` | bob 의 응답은 200 `joined: true` 이고 `keep: true` · `shelf_url` 이 실린다. 잡이 끝나면 `active` | §4.1 합류 · 결정 128 · 코드 `server.py:3704` | srv |
| B13 | `rcm keep` · armed | `plaingold` running 중에 alice 가 `POST /jobs/N/keep` | 200, 결과 `armed`. 끝나면 `active` | §4.1 표 · 결정 126 | srv |
| B14 | `rcm keep` · requested | `plaingold` 가 끝나 묶음 `ready`, 그 뒤 `POST /jobs/N/keep` | 202, 결과 `requested`. 응답 직후 copier 가 깨어 2초 안에 `active`(청소 회차를 기다리지 않는다) | §4.1 표 · §4.2 깨우기 | srv |
| B15 | `rcm keep` · retouched | B1 항목, 시계 +2h 뒤 `POST /jobs/N/keep`. 또 시계 +183d 뒤 다시 | 둘 다 200, 결과 `retouched`. `last_used_at` 이 그때의 `now` 이고 두 번째는 정리 대기에서 `active` 로 돌아온다 | §4.1 표 · §4.3 · 결정 132 | srv |
| B16 | `rcm keep` · retried | `shelf_max_bytes` 를 작게 잡아 `failed(shelf_full)` 를 만든 뒤 다른 항목을 `DELETE` 로 비우고 `POST /jobs/N/keep` | 202, 결과 `retried`. 행이 `pending` 으로 돌아가 `active` 가 된다 | §4.1 표 · 결정 136 | srv |
| B17 | 고정 링크 | `keepgold` 가 dev 에서 #5 · #9, main 에서 #7 로 보관됨 | `GET /shelf/latest/keepgold?branch=dev` 가 303, `Location: /shelf/9/`(호스트 없는 절대 경로). main 은 `/shelf/7/` | §4.6 · 결정 147 | srv |
| B18 | 목록 페이지 | active 둘 · 시계를 당겨 stale 하나 · 묘비 하나 | `GET /shelf/` 200. 「보관 중」 칸에 둘, 「정리 대기」 칸에 하나와 그 행의 `<form method="post" action="/shelf/M/restore">`, 맨 아래 「최근 지움」에 묘비. 머리에 `… / 64 MB` 사용량, 프리셋 필터 링크 | §4.7 목록 · 결정 141 | srv |
| B19 | 되살리기(실제 브라우저) | B1 항목, 시계 +183d. Chrome 이 `http://127.0.0.1:PORT/shelf/N/` 을 열고 「되살리기」 버튼을 누른다 | restore 응답이 303 · `Location: /shelf/N/` 이고(CDP `Network.responseReceived` 로 본다) 도착한 페이지에 그림 격자가 있다. `last_used_at = now`, 상태 `active` | §4.6 · 결정 145 · §7 P2 | web |
| B20 | 손으로 지우기 | admin 이 `DELETE /shelf/N` | 성공 응답, `<shelf_dir>/N/` 이 없다, 행은 `deleted` · 바이트 0. 그 뒤 `/shelf/N/` 은 410 이고 지운 날을 말한다. `shelf_storage.stored_bytes` 가 그만큼 줄었다 | §4.5 손으로 지우기 · 결정 135·146 | srv |
| B21 | `server.shelf_storage` 와 `health.shelf` | 항목 active 둘 · failed 하나 | `/api/status` 의 `server.shelf_storage` 키가 정확히 `{enabled, stored_bytes, limit_bytes, items, last_sweep_at, error_code}` 이고 `items` 가 `{active, stale, pending, failed}`. `/api/health` 의 `shelf` 가 같은 모양이다. 두 응답 본문 문자열에 `str(shelf_dir)` 와 `str(tmp_path)` 가 없다 | §4.8 · 결정 142 | srv |
| B22 | 공개 키 잠금 갱신 | — | `ROW_KEYS` · `RECENT_KEYS` 에 `keep` · `shelf` 가 더해진다. `test_compat_m5e.py` 의 server 블록 `added` 에 `shelf_storage` 가 더해진다. 스키마 버전은 그대로 1 | §7 P2 · 결정 142 | lock |
| B23 | `App.start` 의 두 화해 | `start_workers` · `Janitor` · copier 생성을 감싸 부른 순서를 적는다 | `reconcile_shelf_on_start` 와 `Store.reconcile_bundles_on_start` 가 각각 한 번, 셋보다 **먼저** 불린다 | §5.3 · §7 P2 | start |
| B24 | 언어와 시간대 | `[display] timezone = "Asia/Seoul"`, 끝난 시각 `2026-09-06 15:30Z` | 기본은 `<html lang="ko">` 에 `2026-09-07 00:30`. `?lang=en` 이면 `lang="en"` 과 영어 문구 | §4.7 · 결정 141 | srv |

## 에러

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| E1 | 인코딩한 `..` | B1 항목. `GET /shelf/N/f/%2e%2e/%2e%2e/%2e%2e/rcm.sqlite3` | 400. 본문에 SQLite 머리(`SQLite format 3`)가 없다. 라우터의 `..` 검사(`server.py:5092`)는 디코딩 **전** 문자열을 보므로 이 요청을 통과시킨다. 막는 것은 보관함의 디코딩 뒤 검사다(mutcheck: 디코딩 뒤 검사 제거) | §4.6 경로 · 결정 139 · 코드 `server.py:5092` | sec |
| E2 | 인코딩한 `/` | `GET /shelf/N/f/..%2f..%2f..%2frcm.sqlite3` | 400 | §4.6 경로 | sec |
| E3 | 절대 경로 | `GET /shelf/N/f/%2fetc%2fpasswd` | 400 | §4.6 · `check_path` | sec |
| E4 | NUL | `GET /shelf/N/f/out/a.png%00.txt` | 400 | §4.6 · `safe_member` | sec |
| E5 | CR LF | `GET /shelf/N/f/out/a%0d%0aX-Evil:%201.png` | 400. 응답 헤더에 `X-Evil` 이 없다 | §4.6 · `safe_member` 제어문자 | sec |
| E6 | 역슬래시 | `GET /shelf/N/f/..%5c..%5crcm.sqlite3` | 400 | `check_path` | sec |
| E7 | `.git` | `GET /shelf/N/f/.git/config` 와 `/shelf/N/f/%2egit/config` | 둘 다 400 | §4.6 `safe_member` | sec |
| E8 | 라우터가 먼저 막는 모양 | `/shelf/N/f/out/../a.png`, `/shelf/N/f//etc/passwd` | 400 `bad path`(기존 동작이 보관함 경로에도 그대로 선다) | 코드 `server.py:5092` | sec |
| E9 | 이중 인코딩 | `GET /shelf/N/f/%252e%252e/rcm.sqlite3` | 404. 한 번만 디코딩해 `%2e%2e` 라는 이름으로 보고 manifest 에 없으니 404 다. 두 번 디코딩해 올라가지 않는다 | §4.6 경로 | sec |
| E10 | 깨진 UTF-8 | `GET /shelf/N/f/out/%ff%fe.png` | 400 또는 404. 500 이 아니다 | §4.6 · 코드 `_dispatch_inner` 의 500 갈래 | sec |
| E11 | manifest 밖 | 디스크에 `files/out/planted.txt` 를 손으로 둔다 | `GET /shelf/N/f/out/planted.txt` 는 404 | §4.6 manifest | sec |
| E12 | 심볼릭 링크(파일) | `files/out/a.png` 를 `<data_dir>/rcm.sqlite3` 를 가리키는 링크로 바꾼다 | 404, DB 바이트가 나가지 않는다 | §4.6 · 결정 139 | sec |
| E13 | 심볼릭 링크(디렉터리) | `files/out` 을 `<data_dir>` 를 가리키는 링크로 바꾼다 | `out/a.png` 는 404(`resolve()` 가 `files/` 밖), `out/rcm.sqlite3` 도 404 | §4.6 · 결정 139 | sec |
| E14 | rcm 의 manifest 는 못 받는다 | `GET /shelf/N/manifest.json`, `/shelf/N/f/../manifest.json` | 앞은 404, 뒤는 라우터 400 | §5.2 | sec |
| E15 | `.html` 은 내려받기 | `copyfix` 로 `fixtures/evil.html`(`<script>localStorage.getItem("rcm.token")</script>`) | `GET` 이 `application/octet-stream`, `Content-Disposition: attachment; …`, sandbox CSP, nosniff(mutcheck: sandbox CSP 제거) | §4.6 · 결정 139 | sec |
| E16 | `.svg` · `.pdf` · `.js` | `<svg onload=alert(1)>` 가 든 `x.svg`, `x.pdf`, `x.js` | 셋 다 E15 와 같은 헤더 | §4.6 인라인 허용 목록 | sec |
| E17 | 내용이 HTML 인 `.png` | `fake.png` 의 바이트가 `<html><script>…` | `image/png` 그대로, nosniff, sandbox | §4.6 · 결정 139 | sec |
| E18 | 내용이 HTML 인 `.txt` · `.json` | `note.txt`, `data.json` 에 `<script>` | 둘 다 `text/plain; charset=utf-8`, inline, sandbox, nosniff. `.json` 이 `application/json` 이 아니다 | §4.6 인라인 허용 목록 | sec |
| E19 | 한글 이름 | `out/결과 화면.png` | 200. `Content-Disposition` 이 `inline; filename="__ __.png"; filename*=UTF-8''%EA%B2%B0%EA%B3%BC%20%ED%99%94%EB%A9%B4.png`. 날것 헤더 바이트가 전부 ASCII 다(500 없음). 이름은 basename 으로 본다(Q16) | §4.6 · 결정 139 | sec |
| E20 | 따옴표 · 퍼센트 이름 | `out/q"uote.txt`, `out/100%.txt` | `filename="q_uote.txt"` · `filename*=UTF-8''q%22uote.txt`, `filename*=UTF-8''100%25.txt`. 둘 다 링크로 받으면 200 | §4.6 · 결정 139 | sec |
| E21 | 제어문자 이름이 묶음에 들어온다 | 실제 로컬 잡이 `out/a<LF>b.png` 와 `out/x<CR><LF>Set-Cookie: rcm=1.txt` 를 만든다(`collect` 의 `check_path` 는 개행을 막지 않는다) | 그 두 이름의 파일이 `shelf_dir` 아래에 생기지 않는다. 페이지 · 헤더 어디에도 날것 CR · LF 가 없다. 항목 실패인지 멤버 건너뛰기인지는 Q3 | §4.6 `safe_member` · 코드 `core/artifacts.py:235` | sec |
| E22 | Host 가 밖이다 | 모든 보관함 경로에 `Host: evil.example:PORT`. `GET /shelf/` · `/shelf/N/` · `/shelf/N/f/out/a.png` · `/shelf/N.zip` · `/shelf/latest/keepgold` · `/api/shelf` · `/api/shelf/N`, `POST /shelf/N/restore`(Origin 같음), admin 토큰을 단 `DELETE /shelf/N` | 전부 421. 항목은 그대로이고 시계도 안 돈다. 같은 Host 로 `/api/status` 는 200(보관함 밖은 바뀌지 않는다) | §4.6 Host · 결정 148 | sec |
| E23 | 허용 목록을 속이는 Host | `127.0.0.1.nip.io`, `macmini.local.evil.com`, `evil.com:8787@127.0.0.1`, `172.32.0.1`, `100.128.0.1`, `[2001:db8::1]:8787`, `3232235777`(정수 꼴 192.168.1.1), `0x7f000001`, `127.1`, 빈 `Host:`, Host 없는 HTTP/1.0 요청 | 전부 421 | §4.6 Host · 결정 148 | sec |
| E24 | 허용 목록 안 | `localhost:PORT`, `macmini.local:PORT`, `10.0.0.5`, `172.16.0.1`, `172.31.255.254`, `192.168.0.10:8787`, `100.64.0.1`, `100.127.255.254`, `[fd12::1]:8787`, `public_url = "http://ci.example.com"` 일 때 `ci.example.com`, `socket.gethostname` 을 `"macmini"` 로 바꾼 뒤 `macmini:8787` | 전부 200. `[::1]` 은 Q6 | §4.6 Host · 결정 148 | sec |
| E25 | 421 이 basic 챌린지보다 먼저 | `read_auth = "basic"`, `Host: evil.example` | 421 이고 `WWW-Authenticate` 가 없다(재바인딩 페이지에 로그인 창을 띄우지 않는다. 순서는 Q6) | 결정 138·148 | sec |
| E26 | 되살리기 · 다른 Origin | stale 항목, `POST /shelf/N/restore`, `Origin: http://evil.example` | 403, `last_used_at` · 상태 그대로 | §4.6 · 결정 145 | sec |
| E27 | 되살리기 · 둘 다 없음 | Origin · Referer 없이 | 403 | 결정 145 | sec |
| E28 | 되살리기 · `null` | `Origin: null` + `Referer: http://127.0.0.1:PORT/shelf/N/` 는 303, `Origin: null` + `Referer: http://evil.example/` 는 403 | 앞만 시계가 돈다 | 결정 145 | sec |
| E29 | 되살리기 · scheme · host 가 다르다 | `Origin: https://127.0.0.1:PORT`, Host 가 `127.0.0.1:PORT` 인데 `Origin: http://localhost:PORT` | 둘 다 403 | 결정 145 | sec |
| E30 | 되살리기 · Referer 꾀 | Origin 없이 `Referer: http://127.0.0.1:PORT.evil.com/shelf/N/`, `Referer: http://127.0.0.1:PORT@evil.com/` | 둘 다 403(접두 비교 금지, `urlsplit` 의 hostname 으로 본다) | 결정 145 | sec |
| E31 | Origin 이 다르면 Referer 를 안 본다 | `Origin: http://evil.example` + 같은 출처 Referer | 403 | 결정 145 | sec |
| E32 | 교차 출처 form(실제 브라우저) | 다른 포트의 `http.server` 가 `<form method=post action="http://127.0.0.1:PORT/shelf/N/restore">` 를 열자마자 submit 한다. 또 Chrome 의 `about:blank` 에 같은 form 을 써 넣고 submit 한다 | 둘 다 403, 항목은 stale 그대로. 앞은 Origin 이 다른 포트라서, 뒤는 Origin `null` 에 Referer 가 없어서다 | 결정 145 · §7 P2 | web |
| E33 | restore 는 POST 만 | `GET /shelf/N/restore` | 405 | §4.6 | sec |
| E34 | `POST /jobs` keep 값 | `"keep": "yes"`, `1`, `null` | 셋 다 400, 잡이 생기지 않는다(`supersede` 와 같은 bool 검사) | §4.1 · 코드 `server.py:3656` | srv |
| E35 | keep 인데 글롭 없음 | `noart` 에 `"keep": true` | 400 `keep_without_artifacts`, 잡 수가 그대로 | §4.1 | srv |
| E36 | `rcm keep` 자격 | 남(carol) · 토큰 없음 · 워커 토큰 · `read_auth = basic` 의 Basic · 없는 잡 · `GET /jobs/N/keep` | 403 · 401 · 403 · 401 · 404 · 405. 합류자(bob)와 admin 은 된다 | §4.1 · `can_read_log` | srv |
| E37 | `rcm keep` · no_bundle | 묶음 상태 `empty`(빈 글롭 결과) · `dropped`(B1 의 파일이 1 MiB 를 넘게) · `failed` · `skipped`(시작 전 실패) · 글롭 없는 프리셋 | 다섯 다 409 이고 본문에 이유(묶음 상태)가 있다 | §4.1 표 | srv |
| E38 | `rcm keep` · gone | ack 로 지워진 묶음(합류자 0) · 청소로 `expired` · 청소 전이지만 시계 +25h · ack 됐고 합류자 0 인데 아직 표시만 | 넷 다 410 | §4.1 표 | srv |
| E39 | 상한 넘음 | 이미 60 MiB 를 보관, 새 항목 실측 5 MiB, `shelf_max_bytes = 64 MiB`. 같은 배치에서 실측이 정확히 4 MiB 인 항목 | 앞은 `failed(shelf_full)`, staging 이 비었고 잡 상태 · 묶음 상태가 그대로다. 뒤(합 = 상한)는 `active` | §4.2 ⑤ · 결정 136 | srv |
| E40 | 디스크 바닥 | `min_free_bytes = 1 GiB`, `shutil.disk_usage` 를 바꿔 `free = 1 GiB + actual − 1` | `failed(disk_low)`. `free = 1 GiB + actual` 이면 `active` | §4.2 ⑤ · 결정 137 | srv |
| E41 | 보관함이 꺼짐 | 기존 항목 하나를 둔 채 `shelf_max_bytes = 0` 으로 다시 띄운다. 새 `keepgold` 잡 | 새 행은 처음부터 `failed(disabled)`(`pending` 을 거치지 않는다). 그 잡의 ack 는 미뤄지지 않고 `purged: true`. 기존 항목은 페이지 · 파일 · zip 이 다 200. `health.shelf.enabled = false` 이고 `shelf` 키는 있다 | §3.1 · §4.2 · 결정 136 | srv |
| E42 | 대상 폴더가 이미 있다 | 잡 N 이 끝나기 전에 `<shelf_dir>/N/sentinel` 을 만든다(DB 를 백업으로 되돌린 상황) | `failed(dest_exists)`. `sentinel` 이 그대로이고 staging 이 비었다 | §4.2 ⑥ · §4.5 | srv |
| E43 | 단계 중간의 예외 | `os.replace` 를 `OSError(ENOSPC)` 로 바꾼다. 다음 항목은 정상 | 그 항목 `failed` + `reason_code`(이름은 Q15), `.staging` 비었음, 잡 · 묶음 상태 그대로. 다음 항목은 `active`(스레드가 살아 있다) | §4.2 ⑦ | srv |
| E44 | 묶음 파일이 없다 | 행은 `ready` 인데 `bundle.tar` 를 지운 뒤 `POST /jobs/N/keep` | 202 뒤 `failed`, staging 비었음 | §4.2 ⑦ | srv |
| E45 | copier 스레드 죽음 | copier 루프가 잡지 않는 곳에서 예외가 나게 한다(어디가 그런 곳인지는 Q15) | `server.shelf_storage.error_code = "copier_dead"`, `health.shelf.error_code` 같음, `/api/health` 는 200 · `ok: true` | §4.2 · 결정 142 | srv |
| E46 | tar 폭탄(바이트) | 트리에 `fixtures/bomb.tar.gz`: 0 바이트 64 MiB 파일 하나를 gzip 으로 약 64 KiB. `copyfix` 로 묶음에 들어간다 | `active`. `files/out/bomb.tar.gz` 는 있고 `bomb.tar.gz.d/` 는 없다. manifest `expand_skipped` 에 그 경로와 이유가 있다. 읽은 바이트를 세는 래퍼로 보면 상한 + 64 KiB 안에서 멈췄고, 2초 안에 끝난다(헤더나 ISIZE 를 믿지 않는다) | §4.4 · 결정 133 | srv |
| E47 | tar 폭탄(파일 수) | 빈 멤버 9개(`max_artifact_files = 8`) | 펼치지 않고 이유를 적는다. 원본은 남는다 | §4.4 | srv |
| E48 | 항목 전체 합 | 펼치면 600 KiB 인 tar.gz 둘(상한 1 MiB) | 이름 순서로 앞의 것은 펼치고 뒤의 것은 `expand_skipped` | §4.4 상한은 항목 전체 합 | srv |
| E49 | 나쁜 멤버가 든 tar.gz | 멤버 `/etc/passwd`, `../../escape.txt`, 심볼릭 링크 `out -> /`, 하드 링크 → `/etc/passwd`, 문자 장치, FIFO, `.git/config`, `a<LF>b.png` | `shelf_dir` 를 `os.walk(followlinks=False)` 로 훑으면 링크 · 장치 · FIFO 가 하나도 없고 `shelf_dir` 밖에 새 파일이 없다. 그 멤버들이 manifest 에 없다. 멤버만 버리나 그 tar 펼침 전체를 버리나는 Q3 | §4.4 · 결정 133 | sec |
| E50 | GNU sparse 멤버 | 실제 크기 1 TiB 로 선언한 sparse 멤버(`tarfile` 의 `isreg()` 는 sparse 도 참이다) | 거절된다. 1 TiB 파일이 생기지 않고 시간이 2초 안 | §4.4 | sec |
| E51 | 망가진 tar | 중간에서 자른 `.tar.gz`, 이름만 `.tgz` 인 zip, 0 바이트 `.tar` | 셋 다 펼침 없이 `expand_skipped` 에 이유. 항목은 `active` | §4.4 | srv |
| E52 | 이름 충돌 | (가) 한 tar.gz 안에 `Home.png` 와 `home.png`. (나) 잡이 `out/g.tar.gz.d/A.png` 를 직접 만들고 `out/g.tar.gz` 안에 `a.png`. (다) `한.png` 의 NFC 와 NFD 두 멤버 | 셋 다 `failed(name_collision)`. APFS 에서 펼치는 동안 하나가 다른 것을 덮어도 판정은 경로 이름으로 한다 | §4.2 ④ | srv |
| E53 | 악의적 워커의 바깥 묶음 | 손으로 만든 bundle.tar 에 디렉터리 멤버 `../up`, 심볼릭 링크 `out/x -> /etc/passwd`. 진짜 `WorkerClient.upload_bundle` 로 올리고 `finish` 에 ready 처분 | 업로드는 201 이다(`_manifest_from_tar` 는 일반 파일이 아닌 멤버를 보지 않는다). copier 가 그 멤버를 만들지 않는다. `shelf_dir` 밖에 아무것도 생기지 않는다 | §4.2 ② · 코드 `remote_workers.py:182` | rem |
| E54 | 보관이 실패해도 ack 된 묶음은 지워진다 | E39 의 shelf_full 항목에 copier 붙잡기 + 즉시 ack | ack 는 `purged: false`, 실패가 확정된 뒤 묶음이 `purged` | §4.2 · §7 P2 | srv |
| E55 | 안전판 | copier 를 계속 붙잡는다. `retention_sweep_interval_seconds = 1`, `artifact_retention_hours = 24` | 시계 +25h 회차에는 묶음이 남는다(`bundles_due` 가 뺀다). +48h 를 지난 회차에 보관이 `failed(timeout)` 이 되고 묶음이 `expired` 로 지워진다 | §4.2 TTL 청소 · 결정 130 | srv |
| E56 | 손 삭제 자격 | 비 admin · 토큰 없음 · Basic · 워커 토큰 · 없는 항목 | 403 · 401 · 401 · 403 · 404. 폴더는 그대로 | §4.5 · 결정 146 | srv |
| E57 | zip 도중 삭제 | 큰 항목 zip 을 받다 멈춘 연결 하나. 그 사이 admin `DELETE /shelf/N` | 409 `shelf_item_busy`, 폴더 그대로. zip 을 끝까지 읽고 나면 `DELETE` 가 성공한다. 같은 hold 중에 청소 회차가 오면(시계 +366d) 그 회차는 건너뛰고 다음 회차에 지운다 | §4.5 zip 과 삭제 · §7 P2 | srv |
| E58 | 아직 보관 중 | copier 를 붙잡아 `pending`, 풀어 `copying` 에서 다시 붙잡는다 | 페이지는 200 「보관하는 중」, 파일과 zip 은 409 | §4.6 끝 | srv |
| E59 | 보관함 폴더를 잃었다 | active 행이 있는 DB 로, `.rcm-shelf` 가 없는 빈 `shelf_dir` 를 두고 다시 띄운다 | 페이지 · 파일 · zip 503, `shelf_storage.error_code = "shelf_dir_missing"`, 행 불변, health 200. 새 keep 잡이 어떻게 되나는 Q12 | §5.3 표식 · §4.6 | start |
| E60 | 없는 번호 | `/shelf/999/`, `/shelf/abc/`, `/shelf/99999999999999999999/` | 404 · 404 · 404. 마지막이 SQLite `OverflowError` 로 500 이 되지 않는다 | §4.6 · 코드 `_dispatch_inner` | sec |
| E61 | JSON 목록 인자 | `/api/shelf?state=bogus`, `limit=0`, `limit=abc`, `limit=100000` | 400 | §4.6 | srv |
| E62 | 고정 링크 없음 | 모르는 프리셋, 그 프리셋에 stale 항목만 있음 | 둘 다 404 | 결정 147 | srv |

## 엣지

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| G1 | 시계 · 보관 중 페이지 | 시계 +2h, `GET /shelf/N/` | `last_used_at = now` | §4.3 표 · 결정 132 | srv |
| G2 | 시계 · 파일 200 과 304 | 각각 시계 +2h 뒤 | 둘 다 쓴다 | §4.3 표 | srv |
| G3 | 시계 · zip 과 되살리기와 keep | 각각 시계 +2h 뒤 | 셋 다 쓴다 | §4.3 표 | srv |
| G4 | 시계 · HEAD | `HEAD /shelf/N/`, `HEAD /shelf/N/f/out/a.png`, `HEAD /shelf/N.zip` | 셋 다 200 이고 본문이 없고 시계를 안 돌린다(판정은 HEAD 를 GET 으로 바꾸기 전의 `self.command`) | §4.3 · 코드 `server.py:5096` | srv |
| G5 | 시계 · 목록과 JSON | `/shelf/`, `/shelf/latest/…`, `/api/shelf`, `/api/shelf/N` | 안 쓴다 | §4.3 표 | srv |
| G6 | 시계 · 실패한 요청 | 보관 중 항목에서 manifest 밖 경로 404, 421, 403 된 restore | 안 쓴다 | §4.3 「200 이든 304 든」 | srv |
| G7 | 1시간 규칙을 HTTP 로 | 쓰기 직후 시계 +59m 에 페이지, 그 뒤 +2m 에 다시 | 앞은 `last_used_at` 이 그대로, 뒤는 바뀐다 | §4.3 기록 횟수 | srv |
| G8 | 정리 대기 페이지는 안 쓴다 | 시계 +183d, `GET /shelf/N/` | 200, 그림 격자(`<img`)가 없고 파일 표 · 되살리기 form · `2027-09-06 에 지워집니다` 가 있다. `last_used_at` · 상태 그대로 | §4.3 · §4.7 · 결정 132 | srv |
| G9 | 정리 대기에서 파일을 받는다 | 시계 +183d, `GET /shelf/N/f/out/a.png` | 200, 1시간 규칙 없이 곧바로 `active` · `last_used_at = now` | §4.3 | srv |
| G10 | 청소 회차 사이의 어긋남 | 청소기가 안 돈 채 시계 +183d(열은 아직 `active`) | 페이지 · 목록 · `GET /jobs/N` 의 `shelf.state` 가 모두 `stale` 로 보이고 페이지가 시계를 안 돌린다(어느 값으로 판정하나는 Q14) | §4.3 상태 열 | srv |
| G11 | 삭제 단계인데 아직 안 지움 | 청소기가 안 돈 채 시계 +366d | Q13 의 답대로. 적어도 500 은 없다 | §4.3 · §4.5 | srv |
| G12 | 원격 창 · copier 가 끝나지 않은 잡을 집는다 | `Store.finish` 를 감싸 원격 잡 N 의 finish 를 붙잡는다(묶음은 이미 `ready`, 보관 행 `pending`). 그 사이 다른 로컬 잡이 끝나 copier 를 깨운다 | 보관이 끝난 뒤 N 의 항목 `job_state` 가 `running` 으로 남지 않는다. 기대 동작(건너뛰기 또는 다시 복사)은 Q2 | §4.2 「잡 상태는 active 때 다시 복사」 · 코드 `remote_workers.py:821·828` | rem |
| G13 | 원격 창 · keep 이 늦게 온다 | G12 처럼 붙잡은 사이에 alice 가 `POST /jobs/N/keep`, 또 bob 이 같은 트리를 `"keep": true` 로 합류 | 잡이 끝나면 보관 행이 있고 `active` 가 된다. 오늘 명세대로면 `armed` 를 받고도 행이 영영 안 선다(Q1) | §4.1 · §4.2 · 코드 `remote_workers.py:821·828` | rem |
| G14 | 재시작을 넘긴 원격 keep 잡 | 실제 `RemoteWorker` 의 `client.finish` 를 감싸, 업로드가 끝나고 finish 를 보내기 직전에 서버 App 을 새로 띄운다(같은 `data_dir`, `start()`) | 새 App 의 묶음 화해가 `uploading` 을 `failed(interrupted)` 로 바꾼다. 이어 온 finish 가 `ready` 로 다시 쓰고 보관 행이 서서 `active` 가 된다(Q20) | §5.3 · 코드 `store.py:1743` | start |
| G15 | M5e 화해 회귀 | `artifacts/.staging/7.abcd.part`, 행 없는 `artifacts/123/`, 행 있는 `artifacts/5/`, `shelf/.staging/9.ef01/`, 행 없는 `shelf/77/` 을 두고 `App.start` | 앞의 둘과 넷째가 지워지고 `artifacts/5/` 와 `shelf/77/` 은 남는다. 이전에는 `App.start` 가 묶음 화해를 부르지 않아 앞의 둘이 남았다 | §5.3 기존 문제 · 코드 `server.py:549` | start |
| G16 | copying 입양과 되돌림 | (가) `<shelf_dir>/N/manifest.json` 이 유효한 `copying` 행, (나) 폴더가 없고 묶음이 살아 있는 `copying` 행, (다) 둘 다 없음 | `App.start` 뒤 (가) `active`(다시 풀지 않는다), (나) `pending` 을 거쳐 `active`, (다) `failed(bundle_gone)` | §5.3 · 결정 129 | start |
| G17 | 끄는 중에 보관 중 | copier 가 staging 을 반쯤 썼을 때 `App.shutdown` 뒤 다시 띄운다 | `.staging` 이 비워지고 그 항목이 다시 보관되어 `active` | §4.2 · §5.3 | start |
| G18 | 순서와 하나씩 | `requested_at` 이 #7 < #5 인 pending 둘(원격 #5 가 늦게 끝남). `claim_shelf_item` 을 감싸 동시 실행 수를 잰다 | #7 을 먼저 집는다. 동시 실행 수 최댓값이 1 | §4.2 「pending 가운데 오래된 것부터」 | srv |
| G19 | 깨우기 다섯 곳 | copier 가 다른 깨움 없이 기다리는 상태에서 각각: 로컬 잡 종료, 원격 잡 종료, `POST /jobs/N/keep`, 기동 직후(DB 에 pending 행), 청소 회차(이벤트 없이 store 로 직접 세운 pending) | 다섯 다 2초 안에 `active`(마지막은 `retention_sweep_interval_seconds = 1`) | §4.2 copier 를 깨우는 곳 | srv |
| G20 | 잡 행보다 오래 산다 | 항목을 만든 뒤 잡 행을 `delete_old_jobs` 로 지운다(그 사이 페이지를 열어 시계를 유지) | 페이지 200, 프리셋 · 브랜치 · sha · 요청자가 보이고 `/#/jobs/N` 링크는 없다. `/api/shelf/N` 도 같은 값 | §4.5 · 결정 134 | srv |
| G21 | 실패할 때만 모으는 keep | `failkeep` 를 성공으로 한 번(스크립트 인자로 종료 코드 0), 실패로 한 번 | 성공 잡은 `shelf.state = none` 이고 이유가 실린다(지금 코드로는 `unknown`, Q9). 실패 잡은 `active` 이고 `job_state = failed` | §3.2 · §4.8 · 코드 `worker.py:385` | srv |
| G22 | 취소된 keep 잡 | `keepgold` 변형(파일을 쓴 뒤 `sleep 30`)을 취소. 취소 예산 안에서 모음 | `active`, `job_state = cancelled`. 보관 실패가 잡 상태를 바꾸지 않는다 | §4.2 · M5e 결정 41 | srv |
| G23 | 상태 일곱 갈래가 한 문서에 | 잡 일곱 개를 각각 `pending` · `copying`(붙잡기) · `active` · `stale`(시계) · `failed`(shelf_full) · `deleted` · `none`(empty 묶음)으로, 그리고 keep 아닌 잡 하나 | `/api/status` 의 행과 `GET /jobs/N` 이 같은 `shelf.state` 를 보인다. keep 아닌 잡은 `keep: false` · `shelf: null`. 아직 안 끝난 keep 잡은 Q8 | §4.8 · 결정 142 | srv |
| G24 | 링크의 앞부분 | `Host: macmini.local:8787` 로 `GET /jobs/N`, `Host: 0.0.0.0:8787`, `public_url = "http://ci.example.com"` | `shelf.url` 과 제출 응답 `shelf_url` 이 각각 `http://macmini.local:8787/shelf/N/`, `server_base_url` 의 값, `http://ci.example.com/shelf/N/` | §4.8 · 결정 142 · M5o 결정 120 | srv |
| G25 | HTML 이스케이프 | 파일 이름 `out/<img src=x onerror=alert(1)>.png`, 요청자 라벨 `"><script>alert(1)</script>`, 브랜치 `<b>dev</b>`, 입력값 `'; x="` | 본문에 `&lt;img src=x onerror=alert(1)&gt;` 가 있고 날것 `<img src=x` 와 `<script>` 가 없다. 속성 값의 `"` 는 `&quot;`. 그 파일의 href 는 `/shelf/N/f/out/%3Cimg%20src%3Dx%20onerror%3Dalert%281%29%3E.png` 이고 그 링크로 받으면 200 `image/png` | §4.7 · 결정 141 | sec |
| G26 | `?` · `#` · `+` · 공백 이름 | `out/a?b.png`, `out/c#d.png`, `out/a+b.txt`, `out/x y.txt` | href 가 `%3F` · `%23` · `%2B` · `%20` 으로 인코딩되고 각각 200. `/shelf/N/f/out/a+b.txt`(날것 `+`)도 200 이다(`+` 를 공백으로 바꾸지 않는다) | §4.7 절대 링크 · `quote(path, safe='/')` | sec |
| G27 | 링크는 절대 경로뿐 | `Host: macmini.local:PORT` 로 목록 · 항목 페이지 | 모든 `href` · `src` · `action` 이 `/shelf/` 나 `/#/jobs/` 로 시작한다. 본문에 `macmini.local` 과 `http://` 가 없다 | §4.7 · 결정 141 | sec |
| G28 | 잡 파일 이름이 `manifest.json` | 잡이 묶음 맨 위에 `manifest.json` 을 낸다(글롭 `"manifest.json"`) | `/shelf/N/f/manifest.json` 은 잡의 파일 내용이고 rcm 의 manifest 는 아니다 | §5.2 `files/` 아래만 잡의 것 | sec |
| G29 | NFD 로 온 요청 | manifest 의 이름은 NFC `out/한.png`, 요청은 NFD 를 퍼센트 인코딩 | 404(정규화하지 않는다. APFS 가 NFD 로도 파일을 열어 주는 것을 manifest 검사가 막는다) | §4.6 manifest | sec |
| G30 | 8 MiB 경계 | 보관함 슬롯 넷이 zip 으로 차 있다. 정확히 8 388 608 바이트 파일과 8 388 609 바이트 파일 | 앞은 200(일반 슬롯), 뒤는 503 + `Retry-After: 5` | §4.6 전송 슬롯 | rem |
| G31 | 끊긴 내려받기가 슬롯과 hold 를 돌려준다 | zip 넷이 읽다가 연결을 끊는다 | 2초 안에 다섯 번째 zip 이 200, `DELETE` 가 성공 | §4.6 · §4.5 hold | srv |
| G32 | 느린 독자 | `artifact_transfer_timeout_seconds = 1`, zip 을 받다 읽기를 멈춘다 | 서버가 3초 안에 연결을 닫고 슬롯 · hold 를 돌려준다 | §4.6 쓰기 시한 | srv |
| G33 | 단일 파일 받는 중에 삭제 | 큰 파일 하나를 받다 멈춘 연결, 그 사이 `DELETE` | `DELETE` 는 성공한다(hold 는 zip 만 건다). 멈췄던 내려받기는 마저 읽으면 바이트가 온전하다(열린 파일은 unlink 뒤에도 읽힌다) | §4.5 · 명세에 없던 경우 | srv |
| G34 | HEAD zip | `HEAD /shelf/N.zip` 직후 `DELETE` | HEAD 는 200 · 본문 없음, `DELETE` 는 409 가 아니라 성공(HEAD 는 hold 를 남기지 않는다) | §4.5 · §4.3 | srv |
| G35 | 한 단계만 | tar.gz 안에 `inner.tar`, 묶음에 `x.zip` | `.d/inner.tar` 는 파일로 남고 `inner.tar.d` 는 없다. `x.zip.d` 도 없다 | §4.4 · 결정 133 | srv |
| G36 | `./` 로 시작하는 멤버 | `tar -C dir -czf out/g.tgz .` 로 만든 tar(멤버 `./`, `./test/a.png`) | Q4 의 답대로. 지금 `check_path` 는 `.` 조각을 거절하니 그림이 하나도 안 펼쳐진다 | §4.4 · 코드 `core/artifacts.py:246` | srv |
| G37 | 긴 이름 | 경로 150자 멤버(PAX 헤더) | 펼친다, manifest 경로가 그대로 | §4.4 스트림 | srv |
| G38 | keep 은 제출 때 얼린다 | tree 잡이 `uploading` 인 동안 reload 로 `plaingold` 에 `keep = true` 를 넣는다. 반대로 `keepgold` 의 keep 을 빼는 reload | 앞의 잡은 `keep: false` 로 남고 보관되지 않는다. 뒤의 잡은 `keep: true` 로 남고 보관된다 | §4.1 · 결정 127 | srv |
| G39 | supersede 가 keep 을 넘긴다 | `keepgold` 에 `supersede = "all"`. 옛 잡은 본문 keep 으로, 새 잡은 keep 없이 같은 브랜치 | 새 잡의 `GET /jobs/N` 이 `keep: true` | §4.1 · 결정 128 | srv |
| G40 | git_ref 제출도 keep 을 싣는다 | `deploy` 같은 git_ref 프리셋에 글롭을 더하고 `"keep": true`(합류 갈래 포함) | 201 · 200 응답 둘 다 `keep` 과 `shelf_url` 이 있다. `_submit_git_ref` 는 따로 된 함수라 놓치기 쉽다 | §4.1 · 코드 `server.py:4162·4213` | srv |
| G41 | keep 은 끄지 않는다 | keep 잡에 `"keep": false` 로 합류 | 200, `keep: true` 그대로 | §4.1 · 결정 128 | srv |
| G42 | 연달아 retouch | active 항목에 1분 간격으로 `POST /jobs/N/keep` 두 번 | 둘 다 200 `retouched`, `last_used_at` 쓰기는 한 번 | §4.3 1시간 규칙 | srv |
| G43 | manifest 캐시 | 페이지를 한 번 연 뒤 `manifest.json` 을 새 줄을 더해 바꾸고 mtime 을 올린다 | 새 줄의 파일이 받아진다. mtime 이 그대로면 캐시된 목록을 쓴다 | §4.6 manifest 캐시 | srv |
| G44 | sandbox 문서는 저장소를 못 읽는다(실제 브라우저) | 페이지 출처에 `localStorage["rcm.token"] = "secret"` 을 두고 Chrome 이 `/shelf/N/f/out/note.txt` 를 연다 | 그 문서에서 `window.origin` 이 `"null"` 이고 `localStorage` 접근이 예외다. 항목 페이지의 그림 격자는 sandbox 헤더가 있어도 실제로 뜬다(`naturalWidth > 0`) | 결정 139 | web |
| G45 | 보관 중 표시가 바뀌면 화면이 안다 | copier 가 끝날 때 SSE 를 듣는 연결 | Q18 의 답대로 이벤트를 받거나, 받지 않음을 잠근다 | §4.8 | srv |

## 명세에 묻는 것

1. **원격 잡의 ready 와 finish 사이 창.** 원격은 `_record_disposition`(`remote_workers.py:821`)이 묶음을
   `ready` 로 쓰는 트랜잭션과 `store.finish`(828행)가 따로다. 로컬은 한 트랜잭션이다(`store.py:2720`).
   그 사이에 온 `rcm keep` 은 「아직 안 끝남」이라 `armed` 를 받고, 합류도 keep 만 켠다. 그런데
   `_upsert_bundle` 은 이미 지나가서 보관 행이 영영 서지 않는다. `set_job_keep` 의 `armed` 갈래와 합류가
   「묶음이 이미 ready 면 그 자리에서 pending 행을 세운다」를 해야 하는가(G13).
2. **같은 창에서 copier 가 잡을 집는 경우.** 행은 잡이 running 일 때 서므로 다른 잡의 종료나 청소 회차가
   copier 를 깨우면 끝나지 않은 잡의 행을 집는다. 그러면 `finish_shelf_item` 이 `job_state = running` 과
   빈 끝난 시각을 복사한다. copier 가 잡이 종료 상태가 아닌 행을 건너뛰어야 하는가, 잡이 끝날 때 다시
   복사하는가(G12).
3. **안전하지 않은 멤버의 처분.** 바깥 묶음에 제어문자 이름 · 링크 · 장치 · `..` 멤버가 있으면 항목 전체를
   `failed` 로 두나, 그 멤버만 건너뛰나. 실패라면 `reason_code` 이름은 무엇인가. 펼칠 tar 안에서는 그 tar 의
   펼침 전체를 버리고 `expand_skipped` 에 적나, 멤버만 버리나(E21 · E49 · E53).
4. **`./` 접두 멤버.** `tar -C dir -czf x.tgz .` 가 만드는 `./a.png` 를 `check_path` 가 `.` 조각으로
   거절한다(`core/artifacts.py:246`). 흔한 모양이라 golden-update 의 tar 가 이렇게 만들어졌으면 그림이
   하나도 안 보인다. 앞의 `./` 하나와 `./` 디렉터리 멤버를 떼고 볼지(G36).
5. **400 과 404 의 경계.** §7 은 경로 공격을 「400/404」로 묶었다. 이 문서는 `safe_member` 실패를 400,
   manifest 밖 · 심볼릭 링크를 404 로 적었다. 또 `out%2fa.png` 처럼 디코딩하면 정상 경로가 되는 요청은
   200 인가 400 인가.
6. **Host 허용 목록의 빈칸.** `[::1]` 은 목록에 없다(127/8 만 있다). Tailscale MagicDNS 의
   `macmini.<tailnet>.ts.net` 과 사내 DNS 이름은 421 이 된다. 그런 이름으로 서버를 부르는 CLI 의 `rcm shelf` ·
   `rcm shelf rm` 도 421 이다. Bearer 를 단 요청은 목록을 건너뛸지, `*.ts.net` 을 넣을지 정해야 한다.
   그리고 421 과 basic 의 401 중 무엇이 먼저인가(E25).
7. **같은 출처 비교의 대소문자.** `Origin == "http://" + Host` 를 글자 그대로 비교하면 Host 를 대문자로
   보낸 클라이언트(`MACMINI.local`)는 403 이다. 호스트 부분만 소문자로 맞출지.
8. **아직 안 끝난 keep 잡의 `shelf`.** 일곱 상태 어디에도 「끝나면 보관된다」가 없다. 행이 없는 running
   keep 잡은 `null` 인가 `pending` 인가. 30일 뒤 잊힌(failed 행 삭제) 잡은 `none` 인가(G23).
9. **`none` 의 이유가 `unknown` 이 된다.** `artifacts_on = "failure"` 인 성공 잡은 로컬 · 원격 모두 묶음
   행을 쓰지 않는다(`worker.py:385`). 그래서 `artifact_state` 가 `unknown` 이고 CLI 는
   「nothing to keep — unknown」을 찍는다. §4.8 의 예시와 맞추려면 별도 이유(예 `not_collected`)가 필요하다(G21).
10. **응답 모양이 정해지지 않은 곳.** `POST /jobs/N/keep` 본문의 키 이름(결과 · URL), keep 이 아닌 제출의
    `shelf_url`(null 인가 키가 없나), `keep_without_artifacts` 를 싣는 키, 미뤄진 ack 응답의 `reason_code`
    (`deferred` 인가), `DELETE /shelf/N` 성공 코드(200 본문인가 204 인가), 같은 항목 `DELETE` 를 다시 보낼 때
    (410 인가), `pending` · `failed` 항목의 `DELETE`, 묘비에 대한 `/api/shelf/N`(410 인가 200 `deleted` 인가).
11. **`rcm keep` 표 밖의 상태.** 보관 행이 `pending` · `copying` 일 때(202 로 멱등인가), 묘비가 있는데 묶음이
    아직 살아 있을 때(`INSERT OR IGNORE` 가 새 행을 막는다), 보관함이 꺼졌을 때(`failed(disabled)` 를 세우고
    무엇을 답하나), `unavailable` 일 때.
12. **`unavailable` 동안 새 keep 묶음.** `pending` 으로 세우면 copier 가 안 돌아 ack 미루기가 안전판
    (TTL + 24h)까지 묶음을 붙잡는다. `_request_shelf_tx` 에 `enabled = False` 로 넘겨 곧바로 실패로 세울지.
    그리고 503 은 파일 · zip · 항목만인가, 목록과 JSON 도 503 인가(E59).
13. **삭제 단계인데 아직 안 지운 항목.** 청소기가 죽어 `stage = delete` 인 항목이 남으면 페이지 · 파일 · zip 이
    무엇을 내나. 정리 대기처럼 보이나, 410 인가(G11).
14. **`touches()` 에 넘기는 상태.** 열 값인가 `stage()` 값인가. 열은 `active` 인데 stage 가 `stale` 이면
    화면은 정리 대기 페이지인데, 열 값으로 판정하면 그 페이지가 시계를 돌린다. `shelf_storage.items` 의
    active · stale 집계 기준도 같은 질문이다. `copying` 은 `pending` 에 세나(G10 · B21).
15. **copier 가 죽는 조건과 일반 실패의 이름.** 항목 하나의 예외는 `failed` 로 접고 계속 돈다. 그러면
    `claim_shelf_item` 의 DB 오류(`database is locked`)는 다음 깨움에 다시 하나, 스레드를 죽이나. 단계 ⑦ 의
    일반 실패 `reason_code` 이름은 무엇인가(E43 · E45).
16. **`Content-Disposition` 의 이름.** 마지막 조각(basename)인가 전체 경로인가. 이 문서는 basename 으로 적었다.
17. **zip 의 내용.** rcm 의 `manifest.json` 을 넣나. 상한을 키운 서버에서 항목이 4 GiB 를 넘으면 zip64 를 쓰나.
18. **보관 상태가 바뀔 때의 이벤트.** copier 완료 · 되살리기 · 삭제가 SSE 이벤트를 내지 않으면 웹 잡 상세의
    보관함 줄이 다음 상태 갱신 전까지 옛 값이다. `artifacts_changed` 처럼 하나 낼지(G45).
19. **「transit 을 벗어날 때 깨운다」의 길.** §4.2 는 `publish_artifacts` 가 transit 을 벗어날 때 깨운다고
    했다. 그런데 오늘 원격 업로드는 잡이 끝나기 전에만 받고(`_owned_active`, 끝난 잡은 409), `ready` 는
    `_record_disposition` 이 `publish_artifacts` 없이 쓴다. 이 깨움이 실제로 불리는 길이 있는지, 없으면
    `worker_finish` 의 `_publish_job` 하나로 충분한지 정해야 한다.
20. **기동 때 묶음 화해가 살아 있는 원격 잡을 건드린다.** `App.start` 가 `reconcile_bundles_on_start` 를
    부르면 재시작을 넘겨 살아 있는 원격 잡의 `uploading` 묶음이 `failed(interrupted)` 가 된다
    (`store.py:1743`, `recover_on_start` 는 원격 잡을 건너뛰는데 이 함수는 안 건너뛴다). finish 가 오면 다시
    `ready` 가 되지만 그 사이 화면은 실패를 보인다. `worker_name` 이 있는 running 잡의 묶음은 건너뛸지(G14).
21. **고정 링크의 브랜치와 순서.** `?branch=` 가 없으면 브랜치를 가리지 않는 최신인가, 브랜치가 없는 항목만인가.
    「가장 최근」은 `shelved_at` 순서인가 잡 번호 순서인가(copier 가 오래된 요청부터 집으므로 둘이 다를 수 있다).
22. **실패 항목 페이지.** `failed` 항목의 `/shelf/N/` 은 200 에 이유를 보이나, 404 인가. 그 파일 경로는 404 인가 409 인가.
