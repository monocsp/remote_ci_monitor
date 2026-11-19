# M5p 테스트 시나리오 C — 보관함의 CLI · 웹 · 알림 · 문서 (2026-11-19)

`docs/m5p-workplan.md` 의 PR P3 범위를 테스트로 옮긴 것이다. 근거는 §4.7(페이지) · §4.8(CLI · 알림 · 상태) ·
§3.1 · §5.3 · §7 P3 · §10 이고 결정은 **126 · 141 · 142 · 143 · 144 · 146** 이 중심이다. 한 줄 요약: **세션은
`--keep` 하나로 링크를 받고, 옛 서버에는 보내지 않으며, 사람은 웹 · 알림 · `rcm check` 에서 같은 링크와 같은
상태를 본다.**

이 문서가 안 건드리는 것은 시계 · 저장 · 청소(역할 A, P1)와 copier · HTTP 경로 · 보안 헤더(역할 B, P2)다.
다만 서버 HTML 페이지에서 **사람이 읽는 문구**(언어 · 시각 · 사용량 · 정리 예정일)와 끝단 시험은 여기서 다룬다.
끝단 시험은 P2 가 머지된 뒤 그 위에서 돈다(§7 P3 완료 조건).

줄 번호의 머리 글자는 묶음을 가른다. `CB` 는 베스트(의도한 길), `CE` 는 에러(거절 · 실패), `CG` 는
엣지(경계 · 경쟁 · 옛 서버)다.

## 테스트 파일

| 약칭 | 파일 | 무엇 |
|---|---|---|
| cli | `tests/test_cli_m5p.py` | `rcm run --keep` · `rcm keep` · `rcm shelf` · `rcm shelf rm`. 진짜 `test_server.Server` 에 `cli.main([...])` 를 in-process 로 부른다 |
| check | `tests/test_cli_m5p_check.py` | `rcm check` 의 `shelf` 행. 순수 함수 `render_text.shelf_row(doc, url=…)` 와 `--config` 의 로컬 행 |
| notify | `tests/test_notify_shelf_m5p.py` | `RCM_SHELF_URL` · 웹훅 `shelf_url`. 그리고 `tests/test_notify_rules.py` 의 env 키 집합에 `RCM_SHELF_URL` 을 더한다 |
| pages | `tests/test_shelf_pages_text_m5p.py` | 서버 HTML 의 사람이 읽는 문구. 한국어 기본 · `?lang=en` · 시간대 · 사용량 · 정리 예정일 · 정리 대기 페이지 |
| web | `tests/web/shelf.test.js` | SPA 의 순수 함수(보관함 줄 · 지우기 버튼 · nav 모델). `tests/web/i18n.test.js` 의 인자 표에 새 인자 이름을 더한다 |
| i18n | `tests/test_web_shelf_catalogue.py` | `web/i18n.js` 의 두 로케일이 보관함 이유 코드를 하나씩 **정의**했는지(`tests/test_outcome.py` 의 `_catalogue` 방식) |
| docs | `tests/test_docs_m5p.py` | 문서 잠금. configuration · usage 두 언어 · README 두 언어 · `examples/server.toml` · CHANGELOG |
| e2e | `tests/test_e2e_m5p_shelf.py` | P2 위의 끝단 시험. 진짜 서버 · 진짜 로컬 워커 · 진짜 CLI · 진짜 HTTP. 응답 모양을 손으로 흉내 내지 않는다 |

## 공통 준비

픽스처는 `tests/test_cli_m5e.py` 의 `live` · `env` · `tree` · `run` · `refuse_network` 를 그대로 가져다 쓴다.
서버는 `Server(tmp_path, workers=True, max_snapshot_bytes=200_000)` 이고, 프리셋은 `s.cfg.presets` 를
갈아 끼운다. 서버 키는 생성자 인자로 덮는다(`shelf_max_bytes=1` 처럼). `shelf_dir` 은 기본값
`<data_dir>/shelf` 를 쓴다.

잡이 실제 PNG 와 tar.gz 를 만들게 한다. 셸 `printf` 로 PNG 바이트를 쓰면 이스케이프가 플랫폼마다 달라서,
테스트를 돌리는 파이썬으로 만든다.

```python
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
MAKE = (
    "import io,tarfile,base64,pathlib;"
    "p=pathlib.Path('out');p.mkdir(exist_ok=True);"
    f"png=base64.b64decode('{PNG_B64}');"
    "(p/'home.png').write_bytes(png);"
    "b=io.BytesIO();t=tarfile.open(fileobj=b,mode='w:gz');"
    "i=tarfile.TarInfo('goldens/home.png');i.size=len(png);t.addfile(i,io.BytesIO(png));t.close();"
    "(p/'goldens.tar.gz').write_bytes(b.getvalue());"
    "(p/'run.log').write_text('ok\\n')"
)

def py(name, code, **extra):
    return {"name": name, "argv": [sys.executable, "-c", code], "timeout_seconds": 60, **extra}

SHELF_PRESETS = [
    *PRESETS,
    py("shelfgold", MAKE, artifacts=["out/*"], keep=True),          # 늘 보관
    py("shelfopt", MAKE, artifacts=["out/*"]),                      # --keep · rcm keep 으로만
    py("shelfslow", "import time;time.sleep(20);" + MAKE, artifacts=["out/*"]),
    py("shelfonfail", MAKE, artifacts=["out/*"], artifacts_on="failure", keep=True),  # 초록이면 안 모음
    sh("plain", "echo nothing to collect; exit 0"),                 # 글롭 없음
]
```

- **옛 서버**: `tests/test_reload_dry_run.py::test_c22_…` 처럼 `s.app.health` 를 감싸 본문에서 `shelf` 를
  `pop` 한다. 서버의 나머지는 진짜다.
- **보관이 안 끝나게 붙잡기**: `monkeypatch.setattr(Store, "claim_shelf_item", lambda self, now: None)`.
  항목이 `pending` 에 머문다(P1 인터페이스, 명세 §6).
- **60초를 기다리지 않기**: `cli.SHELF_WAIT_SECONDS`(60)와 `cli.SHELF_POLL_SECONDS`(1)를 모듈 상수로 두고
  시험은 0.5 · 0.05 로 바꾼다. 상수 이름은 제안이다.
- **상한 · 꺼짐**: `shelf_max_bytes=1` 이면 `failed(shelf_full)`, `0` 이면 `failed(disabled)` 다. 둘 다 진짜
  copier 가 낸 결과다.
- **시간대 · 날짜**: 서버 페이지 시험은 `[display] timezone = "Asia/Seoul"` 이고, 항목의 `last_used_at` 을
  `2026-11-19T16:30:00Z` 로 맞춘다(서울 2026-11-20 01:30). 182일 뒤는 UTC `2027-05-20T16:30Z`, 서울
  **2027-05-21** 이다. 365일 뒤는 UTC `2027-11-19T16:30Z`, 서울 **2027-11-20** 이다. 자정 근처를 고른 것은
  UTC 날짜를 그대로 찍는 버그를 잡으려는 것이다.
- **알림**: `tests/test_notify.py` 의 `notifier_for` · `FakeRun` · `FakeOpener` 를 쓴다. 끝단 알림은 진짜 서버에
  `[[notify]] argv = [sys.executable, "-c", "env 를 파일로 쓰는 코드"]` 를 건다.
- **CLI 문구 위치**: `rcm run` 의 사람용 줄은 stderr(`_info`, 머리 없음), 오류는 stderr(`_err`, 머리 `rcm: `),
  마지막 JSON 한 줄은 stdout 이다. `rcm keep` · `rcm shelf` 의 사람용 출력은 stdout, `--json` 은 본문 그대로다.
  아래 표의 문구는 영어 CLI 문구 그대로의 제안이고 `<url>` 은 `http://127.0.0.1:<port>/shelf/<id>/` 다.

## 베스트

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| CB1 | `rcm run --keep` 이 끝나고 보관도 끝났다 | `shelfopt`, `--keep`, copier 그대로 | stderr 마지막 사람용 줄이 `shelf: <url>`. 종료 0. stdout JSON 에 `"shelf":{"state":"active","url":"<url>","reason_code":null}` | §4.8 · 143 | cli |
| CB2 | 프리셋 `keep = true` 를 플래그 없이 | `shelfgold`, `--keep` 없음 | CB1 과 같은 줄과 JSON. health 감지는 하지 않는다(플래그가 없으므로) | §2 · 126 | cli |
| CB3 | `--keep --no-wait` | `shelfopt` | stderr 에 `submitted job #N … · <잡 url>` 다음 줄 `shelf: <url> (once the job finishes)`. stdout JSON 에 `"shelf_url":"<url>"`(제출 응답 값 그대로). 종료 0 | §4.8 · 143 | cli |
| CB4 | `rcm keep N`, 아직 도는 잡 | `shelfslow` 를 `--no-wait` 로 내고 바로 | stdout `#N will go on the shelf when it finishes · <url>`, 종료 0. 잡이 끝난 뒤 `GET /api/shelf/N` 이 `active` | §4.1 표 `armed` · 126 | cli |
| CB5 | `rcm keep N`, 끝났고 묶음이 살아 있다 | `shelfopt` 를 `--keep` 없이 돌려 끝낸 뒤 | stdout `#N is being copied to the shelf · <url>`, 종료 0. 곧 `active` | §4.1 `requested` | cli |
| CB6 | `rcm keep N`, 이미 보관 중 | CB1 뒤 같은 잡 | stdout `#N is on the shelf; its clock starts again (cleanup pending from <YYYY-MM-DD>) · <url>`, 종료 0. `last_used_at` 이 바뀐다(1시간 규칙은 P1 몫이라 여기서는 `s.app.now_fn` 을 2시간 뒤로 바꾼다) | §4.1 `retouched` · §4.3 · 132 | cli |
| CB7 | `rcm keep N --json` | CB5 와 같음 | stdout 한 줄 JSON 이 서버 본문 그대로이고 `result == "requested"`. 사람용 줄은 없다 | §4.8 | cli |
| CB8 | `rcm shelf` | 보관 항목 둘(active 하나 · stale 하나는 `last_used_at` 을 183일 전으로) | 행마다 `#412  shelfopt  main  active  <n> files  <크기>  used 2026-11-19  stale from 2027-05-20` 꼴. 마지막 줄 `2 items · <크기> / <상한> · http://127.0.0.1:<port>/shelf/`. 종료 0 | §4.8 · 141 | cli |
| CB9 | `rcm shelf --state stale --preset shelfopt --json` | CB8 과 같음 | `GET /api/shelf?state=stale&preset=shelfopt` 의 본문 그대로, 항목 하나 | §4.8 | cli |
| CB10 | `rcm shelf rm N`, admin | CB1 뒤, admin 토큰 | stdout `removed #N from the shelf`, 종료 0. 그 뒤 `GET /shelf/N/` 은 410 | §4.5 · 146 | cli |
| CB11 | `rcm check` 의 `shelf` 행, 여유 있음 | 보관 항목 active 3 · stale 1, 사용량이 상한의 절반 | `ok   shelf         3 active · 1 stale · <사용량> / <상한> · http://127.0.0.1:<port>/shelf/` | §4.8 · 142 | check |
| CB12 | 알림 env, keep 잡 | `notifier_for` 로 keep 잡 하나 전달 | `RCM_SHELF_URL == "<base>/shelf/<id>/"`. `<base>` 는 `RCM_URL` 과 같은 앞부분이다. 보관이 아직 `pending` 이어도 같다 | §4.8 · 144 · 142 | notify |
| CB13 | 웹훅 본문, keep 잡 | `FakeOpener` 로 url 규칙 | 본문 맨 위 키 `shelf_url` 이 CB12 와 같은 값 | §4.8 · 144 | notify |
| CB14 | SPA 최근 행의 보관함 줄 | `rcm.shelfLine({id:412, keep:true, shelf:{state:"active", url:"http://mac.local:8787/shelf/412/"}}, "ko")` | `{text:"보관함 ↗", href:"/shelf/412/", cls:"active"}`. 영어는 `"shelf ↗"`. `art` 줄 바로 아래에 그린다 | §4.7 · 141 | web |
| CB15 | SPA 지우기 버튼 | 같은 행, `state.token` 있음 · `state.admin === true` | `<button data-shelf-delete="412">` 하나. 누르면 `confirm` 뒤 `DELETE /shelf/412` 에 `Authorization: Bearer <token>`, 200 이면 상태를 다시 읽는다 | §4.5 · §4.7 · 146 | web |
| CB16 | SPA nav | `/api/status` 의 `server.shelf_storage.enabled === true`, 릴리스 저장소 0개 | nav 가 보이고 `<a href="/shelf/" data-nav="shelf">보관함</a>` 이 있다. 오늘의 `renderNav` 는 저장소가 없으면 nav 를 숨기므로 이 갈래가 새로 생긴다 | §4.7 | web |
| CB17 | 서버 목록 페이지, 한국어 기본 | active 1 · stale 1 · 최근 지움 1, 시간대 서울 | 본문에 `보관함` · `보관 중` · `정리 대기` · `최근 지움(30일)` · `되살리기` 와 사용량 `<사용량> / <상한>`. `<html lang="ko">` | §4.7 · 141 | pages |
| CB18 | 같은 페이지 `?lang=en` | 같음 | `Shelf` · `Kept` · `Cleanup pending` · `Recently removed (30 days)` · `Restore`, `<html lang="en">`. 한국어 문구가 하나도 없다(한글 정규식 `[가-힣]` 이 프리셋 · 입력값 밖에서 0건) | §4.7 · 141 | pages |
| CB19 | 항목 페이지의 시각과 정리 예정일 | 공통 준비의 서울 · 2026-11-19T16:30Z 항목 | 끝난 시각이 서울 시각으로, 정리 예정이 `2027-05-21` 로 보인다. `2027-05-20` 은 어디에도 없다 | §4.3 · §4.7 · 141 | pages |
| CB20 | 정리 대기 항목 페이지 | stale 항목, PNG 가 든 묶음 | `<img` 0건, 파일 표와 「되살리기」 form, `2027-11-20 에 지워집니다`(영어 `Removed on 2027-11-20`). 같은 묶음의 보관 중 페이지에는 `<img src="/shelf/<id>/f/out/home.png"` 가 있다 | §4.3 · §4.7 · 132 | pages |
| CB21 | 문서 잠금 | 구현 뒤의 문서 | 아래 「문서 잠금」 표의 글자가 전부 있다 | §7 P3 · §3 · 138 · 148 | docs |
| CB22 | 끝단: `rcm run --keep` 이 준 링크가 실제로 열린다 | `live` + `shelfopt` + `tree`, P2 머지 뒤 | stderr 에서 `shelf: (\S+)` 를 뽑아 그 URL 을 `urllib` 로 연다. 200 HTML 에 `out/home.png` 와 `out/goldens.tar.gz.d/goldens/home.png` 가 있고, 그 파일 링크의 바이트가 잡이 만든 PNG 와 같다 | §7 P3 완료 조건 · 143 · 133 | e2e |
| CB23 | 끝단: 뒤늦게 `rcm keep` → `rcm shelf` → `rcm shelf rm` | `shelfopt` 를 플래그 없이 끝낸 뒤 셋을 차례로 | `requested` → (기다림) → `rcm shelf --json` 에 그 잡이 `active` → admin `rm` 종료 0 → 같은 URL 410 | §4.1 · §4.5 · 146 | e2e |
| CB24 | 끝단: `--keep --fetch-artifacts` 를 같이 | `shelfopt`, `tree` | 트리에 `out/home.png` 가 써지고(종료 0), 보관도 `active`. 보관이 끝난 뒤 `GET /jobs/N/artifacts` 가 `purged`(ack 미루기가 끝까지 돌았다). stderr 순서는 `artifacts: wrote …` 다음에 `shelf: <url>` | §4.2 · 130 · 143 | e2e |
| CB25 | 끝단: 알림 훅이 받은 링크 = CLI 가 찍은 링크 | 진짜 서버에 env 를 파일로 쓰는 `[[notify]]` argv, `public_url = "http://127.0.0.1:<port>"` | 파일의 `RCM_SHELF_URL` 이 CB22 의 stderr URL 과 글자까지 같다 | §4.8 · 142 · 144 | e2e |

## 에러

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| CE1 | 옛 서버에 `--keep` | health 에서 `shelf` 를 뺌, `refuse_network` | stderr `rcm: run --keep: server does not support --keep (rcm ≥ 0.7.0) — nothing was sent`, 종료 2. 스냅샷 0번 · 제출 0번 | §4.1 · 143 · M5o 124 | cli |
| CE2 | 옛 서버에 git_ref 프리셋 `--keep` | 같음, `--ref main` | CE1 과 같은 줄과 2. git_ref 길에서도 제출하지 않는다 | §4.1 · 143 | cli |
| CE3 | `--keep` 인데 health 를 못 읽는다 | `client.health` 가 `ClientError(0, …)` | stderr `rcm: run --keep: cannot reach the server (<사유>) — nothing was sent`, 종료 3. 제출 0번 | §4.1 · M5o 124 | cli |
| CE4 | `--keep` 인데 프리셋에 `artifacts` 가 없다 | `plain --keep` | 서버 400 `keep_without_artifacts`. stderr `rcm: submit failed: <서버 문구>`, 종료 2. 잡은 생기지 않는다 | §4.1 · 126 | cli |
| CE5 | 끝났는데 보관이 실패 | `shelf_max_bytes=1`, `shelfopt --keep` | stderr `shelf: not kept — shelf_full`. 종료 코드는 잡의 것(0). JSON `shelf.reason_code == "shelf_full"` | §4.8 · 136 · 143 | cli |
| CE6 | 보관함이 꺼진 서버 | `shelf_max_bytes=0`, `shelfopt --keep` | stderr `shelf: not kept — disabled`, 종료 0 | §3.1 · 136 | cli |
| CE7 | 묶음이 없는 채로 끝남 | `shelfonfail` 이 초록 | stderr `shelf: nothing to keep — <묶음 상태>`, 종료 0, 기다리지 않는다(60초 상수를 30으로 둬도 1초 안에 끝난다). 묶음 상태 글자는 「명세에 묻는 것」 3번의 답을 따른다 | §4.8 · 143 · §3.2 | cli |
| CE8 | 잡이 실패, 보관은 성공 | `shelfopt` 의 코드에 `;raise SystemExit(2)` | `shelf: <url>` 이 찍히고 종료 1(실패의 코드). 보관이 종료 코드를 바꾸지 않는다 | §4.2 · §4.8 | cli |
| CE9 | `rcm keep N`, 묶음이 처음부터 없다 | `shelfopt` 대신 `shelfonfail` 초록 잡 | stderr `rcm: keep: #N has nothing to keep — <묶음 상태>`, 종료 2. 서버는 409 | §4.1 `no_bundle` | cli |
| CE10 | `rcm keep N`, 이미 받아 가서 지웠다 | `shelfopt --fetch-artifacts` 로 끝낸 잡(ack 로 purged) | stderr `rcm: keep: #N cannot be kept — its artifacts are gone (purged); next time use rcm run --keep`, 종료 2. 서버는 410 | §2 · §4.1 `gone` | cli |
| CE11 | `rcm keep N`, 남의 잡 | alice 가 낸 잡에 bob 토큰 | stderr `rcm: keep: #N — only its requester, a joiner or an admin token can keep it`, 종료 2. 서버 403. admin 토큰이면 통과 | §4.1 · §4.6 | cli |
| CE12 | `rcm keep N`, 없는 잡 · 토큰 없음 | `rcm keep 99999` · `RCM_TOKEN` 없음 | `rcm: keep: no job #99999` 종료 2 · 오늘의 토큰 안내(`_no_token_hint`) 종료 2 | §4.8 | cli |
| CE13 | `rcm shelf rm N`, admin 아님 | alice 토큰 | stderr `rcm: shelf rm: needs an admin token`, 종료 2. 항목은 그대로 `active` | §4.5 · 146 | cli |
| CE14 | `rcm shelf rm N`, 없는 항목 | 보관한 적 없는 번호 | stderr `rcm: shelf rm: #N is not on the shelf`, 종료 2 | §4.6 | cli |
| CE15 | `rcm shelf rm N`, zip 을 받는 중 | zip 응답을 열어 둔 채(hold) | 서버 409 `shelf_item_busy`. stderr `rcm: shelf rm: #N is being downloaded right now — try again in a minute`, 종료 2 | §4.5 | cli |
| CE16 | `rcm check`, 폴더가 안 붙었다 | `shelf_storage.error_code = "shelf_dir_missing"` 인 문서 | `FAIL shelf         unavailable (shelf_dir_missing) — the shelf folder has no .rcm-shelf marker; is its disk mounted?` 그리고 종료 1 | §5.3 · §4.8 | check |
| CE17 | `rcm check`, copier 가 죽었다 | `error_code = "copier_dead"` | `FAIL shelf         the copy thread stopped (copier_dead) — restart the server`, 종료 1 | §4.2 · §4.8 | check |
| CE18 | `rcm check`, 90% 를 넘었다 | `stored_bytes = 901`, `limit_bytes = 1000` | `warn shelf         … · 901 B / 1.0 KB (90%) · new keeps fail at the limit · …`, 종료 0. `900` 이면 `ok`(「넘으면」이므로) | §4.8 | check |
| CE19 | SPA 지우기가 거절됨 | 버튼을 누르고 `fetch` 가 403 · 409 · 410 | 403: `shelf.delete.denied` 문구, 행 그대로. 409: `shelf.delete.busy`. 410 · 404: 오류 없이 상태를 다시 읽는다 | §4.5 · 146 | web |
| CE20 | 보관이 실패한 행의 SPA 줄 | `shelf:{state:"failed", reason_code:"shelf_full"}` | `보관 못 함 · 보관함이 가득 참`(영어 `not kept · the shelf is full`), 링크 없음, 지우기 버튼 없음 | §4.7 | web |
| CE21 | 끝단: 옛 서버 감지가 진짜 서버에서도 돈다 | 진짜 `Server` 의 health 만 감쌈 | CE1 과 같고, 서버 DB 의 잡 수가 시험 전후로 같다 | §7 P3 · 143 | e2e |

## 엣지

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| CG1 | 보관이 60초 안에 안 끝남 | 공통 준비의 붙잡기, 상수 0.5초 | stderr `shelf: <url> (still copying)`, 종료 0. JSON `shelf.state == "pending"`. 0.5초를 넘겨 기다리지 않는다 | §4.8 · 143 | cli |
| CG2 | 기다리는 동안 끝남 | 붙잡기를 0.2초 뒤 풂, 상수 2초 | `shelf: <url>`(꼬리 없음). JSON 의 `shelf.state` 는 **마지막으로 본 값**(`active`)이지 잡이 끝난 순간의 `pending` 이 아니다 | §4.8 | cli |
| CG3 | 기다리는 중 Ctrl-C | 보관 대기 루프에서 `KeyboardInterrupt` | `shelf: <url> (still copying)` 을 찍고 잡의 종료 코드로 끝난다. detach(3)가 아니다. 잡은 이미 끝났다 | §4.8 | cli |
| CG4 | health 의 `shelf` 가 `null` | 키는 있고 값만 `null` | 감지는 **키가 있는지**로 한다. 제출한다 | §4.8 「이 키가 있다는 것」 | cli |
| CG5 | health 가 503 이지만 본문에 `shelf` 가 있다 | janitor stale 로 503 | `client.health` 가 본문을 돌려주므로(M5l L3) 제출한다 | §4.1 | cli |
| CG6 | health 를 두 번 부르지 않는다 | `_warn_if_client_too_old` 와 감지가 둘 다 health 를 읽음 | `--keep` 한 번에 `GET /api/health` 가 한 번만 간다(요청 수 셈) | §4.1 | cli |
| CG7 | 감지는 스냅샷보다 먼저 | 옛 서버 + 10 MB 트리 | `make_snapshot` 이 불리지 않는다(`refuse_network`) | §4.1 · M5h 의 「큰 트리를 다 싼 뒤에 거절하지 않는다」 | cli |
| CG8 | 합류한 제출의 `--keep` | `shelfslow` 를 플래그 없이 낸 뒤 같은 입력으로 `--keep` | 둘째 CLI 가 `joined job #N` 을 찍고 끝에 `shelf: <url>`. 첫째 CLI 도 끝에서 같은 줄을 찍는다(잡이 keep 이 되었으므로) | §4.1 · 128 | cli |
| CG9 | 새 잡에 밀린 `--keep` 잡 | supersede 프리셋으로 `--keep` 잡을 낸 뒤 같은 브랜치 새 잡 | 옛 CLI 는 `#O superseded by #N — rcm wait --job N` 을 찍고 종료 2. 보관 줄은 「명세에 묻는 것」 9번의 답을 따른다. 새 잡 `GET /jobs/N` 의 `keep` 은 참 | §4.1 · 128 · M5o 114 | cli |
| CG10 | `--timeout` 으로 끝난 대기 | `shelfslow --keep --timeout 1` | 종료 3, 보관 대기를 하지 않는다. 보관 줄은 「명세에 묻는 것」 9번의 답을 따른다 | §4.8 | cli |
| CG11 | 원격 잡의 묶음이 아직 올라오는 중 | `GET /jobs/N` 이 terminal 인데 `artifacts.state == "uploading"` 인 순간 | CLI 가 「nothing to keep」 이라고 말하지 않는다. 기다림 안에서 다시 본다 | §4.2 「transit」 · 143 | cli |
| CG12 | `--keep` 과 `--no-wait` 와 합류 | 합류가 되는 제출 | stdout JSON 에 `joined: true` 와 `shelf_url`. stderr 에 `shelf: <url> (once the job finishes)` | §4.8 | cli |
| CG13 | keep 이 아닌 잡의 `--no-wait` | `shelfopt` 플래그 없음 | JSON 에 `shelf_url` 키가 없다(`null` 도 아니다). `shelf:` 줄도 없다 | §4.8 | cli |
| CG14 | `rcm keep N` 두 번, 도는 잡 | 같은 잡에 연달아 | 두 번 다 `armed`, 종료 0. 잡의 `keep` 은 참 하나 | §4.1 | cli |
| CG15 | `rcm keep N`, 보관이 실패했던 잡 | CE5 의 잡에서 `shelf_max_bytes` 를 키우고 재시작 없이는 못 바꾸므로 새 서버 인스턴스를 같은 data_dir 로 | stdout `#N: the last copy failed (shelf_full); trying again · <url>`, 종료 0. 곧 `active` | §4.1 `retried` | cli |
| CG16 | `rcm keep N`, 보관 중(pending) | 붙잡기 상태에서 | 표에 갈래가 없다. 「명세에 묻는 것」 10번의 답을 따른다. 제안은 `requested` 와 같은 줄 · 종료 0 | §4.1 | cli |
| CG17 | 옛 서버에 `rcm keep` · `rcm shelf` | 옛 서버(경로 없음 → 404) | 404 를 「없는 잡」 으로 말하지 않는다. health 에 `shelf` 가 없으면 `rcm: keep: server does not support rcm keep (rcm ≥ 0.7.0)`, 종료 2. `rcm shelf` 도 같은 꼴 | §4.1 · 143 | cli |
| CG18 | 보관함이 빈 서버의 `rcm shelf` | 항목 0 | stdout `the shelf is empty · http://127.0.0.1:<port>/shelf/`, 종료 0 | §4.8 | cli |
| CG19 | `rcm shelf rm` 이 이미 지운 항목 | CB10 뒤 한 번 더 | 「명세에 묻는 것」 14번의 답을 따른다. 제안은 stdout `#N was already removed on <YYYY-MM-DD>`, 종료 0 | §4.5 · 135 | cli |
| CG20 | `rcm check`, 보관함이 꺼짐 | `enabled: false`, 남은 항목 2 | 행이 있고 실패가 아니다. 제안은 `ok   shelf         off (shelf_max_bytes = 0) · 2 kept items stay until their clock runs out` | §3.1 · §4.8 | check |
| CG21 | `rcm check`, 옛 서버 | health · status 에 `shelf` 없음 | `shelf` 행이 없다(오늘의 `cancel` 행과 같은 규칙) | §4.8 | check |
| CG22 | `rcm check --config`, 행 없는 폴더 | `shelf_dir` 에 숫자 폴더 `77`, `78` 과 `.staging`, DB 에는 행 없음 | 로컬 행에 `shelf_dir` 경로와 `orphans 2`. `.staging` · `.rcm-shelf` 는 세지 않는다 | §4.8 · §5.3 | check |
| CG23 | `rcm check --config` 가 DB 를 고치지 않는다 | v23 DB(`shelf_items` 없음)와 v24 DB 둘 | 시험 전후로 `PRAGMA user_version` · 파일 해시가 같고 `backup/` 이 생기지 않는다. v23 이면 `orphans ?` 와 이유 | §5.3 · 운영 가드(CLAUDE.md) | check |
| CG24 | `rcm check`, keep 프리셋이 `artifacts_on = "failure"` | 로컬 설정에 `shelfonfail` | 로컬 행에 `shelfonfail keeps failed jobs only (artifacts_on = "failure")` | §3.2 | check |
| CG25 | 알림, keep 이 아닌 잡 | 일반 잡 | env 에 `RCM_SHELF_URL` 키가 있고 값은 `""`. 웹훅 본문 맨 위에 `shelf_url` 키가 없다 | §4.8 · 144 | notify |
| CG26 | 알림 링크에 `0.0.0.0` 이 없다 | `bind = "0.0.0.0"`, `advertise = false`, `hostname = "macmini"` | `RCM_SHELF_URL == "http://macmini:8787/shelf/7/"` | §4.8 · M5o 120 | notify |
| CG27 | `public_url` 끝의 `/` | `public_url = "http://mac.local:8787/"` | `http://mac.local:8787/shelf/7/`, `//shelf` 없음 | 142 | notify |
| CG28 | 서버가 꺼진 사이 끝난 keep 잡 | 기동 스캔(`Notifier.scan`) | 기동 스캔이 보낸 env 에도 같은 `RCM_SHELF_URL` | §4.8 | notify |
| CG29 | SPA 줄의 상태별 갈래 | `stale` · `pending` · `copying` · `deleted` · `none` · `keep:false` · `shelf` 키 없음 | stale: 링크 + `정리 대기`. pending · copying: `보관하는 중…` 링크 없음. deleted: `보관함에서 지움`. none: `보관할 파일 없음`. keep 거짓 · 키 없음(옛 서버): `null`, 예외 없음 | §4.7 · §4.8 | web |
| CG30 | SPA 링크는 같은 출처 경로 | `shelf.url` 의 호스트가 `public_url` 이라 브라우저가 연 호스트와 다름 | `href` 는 `/shelf/412/`(경로만). `shelf.url` 을 그대로 쓰지 않는다 | §4.7 · 148 | web |
| CG31 | SPA 지우기 버튼의 조건 | 토큰 없음 · admin 아닌 토큰 · admin + stale · admin + pending | 버튼은 admin 이고 active · stale 일 때만. 「명세에 묻는 것」 23번 참고 | §4.7 · 146 | web |
| CG32 | SPA nav 와 언어 | `rcm.lang = "en"` | 링크가 `/shelf/?lang=en`. 꺼진 서버(`enabled: false`)면 링크 없음, 저장소도 없으면 nav 가 숨는다 | §4.7 | web |
| CG33 | i18n 키 집합 | 새 키 `nav.shelf` · `shelf.*` | 두 언어 키 집합이 같다(기존 잠금). 새 인자 이름(`date` 등)을 `i18n.test.js` 의 인자 표에 더해 자리표시자가 남지 않는다 | §7 P3 | web |
| CG34 | 이유 코드마다 두 언어 정의 | 이유 코드 상수(「명세에 묻는 것」 20번) | `shelf.reason.<code>` 가 en · ko 영역에 **정의**로 한 번씩. 주석에 문자열만 남긴 것은 세지 않는다 | §7 P3 | i18n |
| CG35 | 서버 페이지의 언어 갈래 | `?lang=fr` · `?lang=EN` · `?lang=` | 셋 다 한국어(기본). 대소문자는 「명세에 묻는 것」 26번 | §4.7 · 141 | pages |
| CG36 | 영어 페이지의 링크가 언어를 잃지 않는다 | `/shelf/?lang=en` 의 항목 링크, 되살리기 form `action` | 항목 링크와 form 이 `?lang=en` 을 싣는다. 파일 링크 `/shelf/N/f/…` 는 싣지 않는다 | §4.7 | pages |
| CG37 | 사용량 머리 | `stored_bytes = 12_300_000_000`, 상한 기본값(50 GiB) | 「명세에 묻는 것」 16번의 답에 따라 `12.3 GB / 53.7 GB`(십진, 오늘의 `_bytes`) 또는 `11.5 GB / 50 GB`(GiB). 페이지 · `rcm check` · `rcm shelf` 셋이 같은 눈금 | §4.7 · §4.8 | pages · check |
| CG38 | 보관하는 중인 항목 페이지 | `pending` 항목 | `보관하는 중` · `Copying to the shelf…`, 파일 표 없음 | §4.6 | pages |
| CG39 | 묘비 페이지 | `deleted` 항목, 지운 날 서울 2026-11-20 | 410 본문에 `2026-11-20 에 지웠습니다` · `Removed on 2026-11-20`(`?lang=en`) | §4.5 · 135 | pages |
| CG40 | 보관 링크가 Host 허용 목록을 통과한다 | `server_base_url` 의 네 갈래(public_url · 특정 bind · `.local` · hostname) | 각 갈래의 링크 호스트로 `host_allowed` 가 참. 공인 IP 를 bind 한 갈래는 「명세에 묻는 것」 28번 | 142 · 148 | notify |
| CG41 | 끝단: 감지 뒤 진짜 서버에 `keep: true` 가 갔다 | `Client.submit` 을 감싸 본문을 기록(보내는 것은 진짜) | 본문에 `"keep": true`. `--keep` 이 없으면 키가 없다(옛 서버에 무해) | §4.1 · 143 | e2e |
| CG42 | 끝단: 링크를 연 것이 시계를 돌린다 | CB22 뒤 `last_used_at` 을 2시간 전으로 당기고 링크의 PNG 를 받음 | `GET /api/shelf/N` 의 `last_used_at` 이 지금으로. CLI 가 찍은 URL 이 실제로 「쓰는」 경로라는 확인 | §4.3 · 132 | e2e |

## 문서 잠금 (CB21 의 글자)

`flat()`(연속 공백 하나로)을 거친 본문에서 찾는다. 문구는 제안이다. 구현이 다른 낱말을 고르면 이 표와
시험을 같이 고친다.

| 파일 | 있어야 하는 글자 |
|---|---|
| `docs/configuration.md` | 절 머리 `## Keeping a job's files: the shelf`. 서버 키 넷과 기본값 `shelf_dir`, `shelf_max_bytes = 53687091200`, `shelf_stale_days = 182`, `shelf_delete_days = 365`. 프리셋 `keep = true` 와 `keep needs artifacts`. `artifacts_on = "failure"` 와 함께면 실패한 잡만 보관된다는 문장 |
| 같은 파일, 보안 문단 | `anyone on that network, and anyone who reaches this machine over Tailscale, can open them` · `Do not put secrets in artifacts` · `read_auth = "basic"` 이 보관함에도 걸린다는 문장 |
| 같은 파일, Host 허용 목록 | `421` · `localhost` · `.local` · `100.64.0.0/10` · `public_url` 이 한 문단에 |
| 같은 파일, 업그레이드 경고 | `only after the server runs v0.7.0` (프리셋은 모르는 키를 거절한다) |
| 같은 파일, 알림 문단 | `RCM_SHELF_URL` 과 웹훅의 `shelf_url` |
| 같은 파일, 다른 디스크 | `.rcm-shelf` 표식과, 같은 디스크면 워크스페이스가 더 일찍 지워진다는 문장 |
| `docs/usage.md` · `docs/usage.ko.md` | 둘 다 `rcm run golden-update --keep` · `rcm keep` · `/shelf/latest/golden-update?branch=dev`. 영어판 `use --keep from the start`, 한국어판 `처음부터 --keep` (`--fetch-artifacts` 로 받은 잡은 뒤늦게 넣을 수 없다) |
| `README.md` · `README.ko.md` | 명령 표에 `` `rcm keep N [--json]` `` 와 `` `rcm shelf [--state active\|stale\|all] [--preset P] [--json]` ``, `rcm run` 행에 `--keep` |
| `examples/server.toml` | 주석으로 `# shelf_max_bytes = 53687091200` · `# shelf_stale_days = 182` · `# shelf_delete_days = 365`. 프리셋 예시에 `# keep = true`. 주석을 풀어도 `tests/test_examples.py` 가 통과한다 |
| `CHANGELOG.md` | `## [Unreleased]` 부터 `## [0.6.1]` 앞까지에 `--keep` · `rcm keep` · `/shelf/` · `RCM_SHELF_URL` · `v24` |

## 변이(mutcheck)

| 이름 | 변이 | 잡는 시험 |
|---|---|---|
| `shelf-health-detection-removed` | `--keep` 의 health 감지를 건너뜀 | CE1 · CE21 |
| `notify-shelf-url-dropped` | `notify_env` 에서 `RCM_SHELF_URL` 을 뺌 | CB12 · CB25 |
| `shelf-wait-skipped` | 보관 대기를 0초로 | CG2(`active` 대신 `still copying`) |
| `spa-shelf-delete-without-admin` | 지우기 버튼 조건에서 admin 검사를 뺌 | CG31 |
| `shelf-page-utc-date` | 정리 예정일을 UTC 날짜로 찍음 | CB19 |

`shelf-health-detection-removed` 와 `notify-shelf-url-dropped` 는 명세 §10 이 P3 에 요구한 둘이다.

## 명세에 묻는 것

명세는 고치지 않았다. 아래는 코드를 읽다가 명세와 어긋나거나 답이 없던 자리다. 각 줄의 「제안」은 위
시나리오가 일단 가정한 답이다.

1. **`rcm run` 에는 `--json` 이 없다.** §4.8 은 「`--json` 은 `shelf{…}` 를 싣는다」 고 적었지만 `rcm run` 은
   플래그 없이 늘 마지막에 JSON 한 줄을 stdout 에 찍는다(`cli._wait`). 제안: 그 JSON 에 늘 싣는다(CB1).
2. **원격 묶음이 아직 올라오는 중에 잡이 끝나면 `shelf.state` 는 무엇인가.** §4.8 은 「keep 인데 묶음이 ready
   가 아닌 채 끝났으면 `none`」 이라 했다. 그런데 §4.2 가 말하듯 원격 업로드는 종료 뒤에 끝날 수 있다. 그
   순간 `none` 을 받은 CLI 는 「nothing to keep」 이라고 거짓말을 한다. 제안: 묶음이 `pending` · `collecting`
   · `uploading` 이면 `shelf.state` 도 `pending` 으로 싣는다(CG11).
3. **`artifacts_on = "failure"` 인 초록 잡의 묶음 상태는 `unknown` 이다.** 워커가 묶음 행을 아예 만들지 않아서
   `App.artifact_state` 가 `unknown` 을 낸다. 그러면 §4.8 의 예시가 `shelf: nothing to keep — unknown` 이 된다.
   제안: 이 경우의 이유 코드를 `not_collected` 처럼 따로 두고 문구를 `nothing to keep — not collected for a
   green job (artifacts_on = "failure")` 로 한다(CE7). `rcm keep` 의 `no_bundle` 조건 목록에도 이 `unknown`
   (묶음 행 없음)이 없어서, 같은 잡에 `rcm keep` 을 걸면 여섯 갈래 어디에 떨어질지 정해지지 않았다(CE9).
4. **`--keep` 없이 프리셋이 `keep = true` 일 때** CLI 가 보관 줄을 찍고 60초까지 기다리는가. `rcm wait --job N`
   도 같은가. 제안: 잡 문서의 `keep` 이 참이면 둘 다 같은 줄을 찍고 기다린다(CB2).
5. **health 의 `shelf` 가 있지만 꺼져 있을 때**(`shelf_max_bytes = 0`) `--keep` 을 미리 거절하는가. 제안:
   보내고 끝에 `shelf: not kept — disabled` 를 찍는다(CE6). 미리 거절하면 「keep 이 표시된 기록」 이 남지 않는다.
6. **`--keep` 인데 health 를 못 읽으면** 종료 3 인가. 제안: `rcm reload --dry-run` 과 같이 3 과
   `nothing was sent`(CE3).
7. **프리셋에 `artifacts` 가 없는지 클라이언트가 미리 알 길이 없다.** `preset_json` 에 `artifacts` 도 `keep` 도
   없어서 400 `keep_without_artifacts` 는 트리 스냅샷을 다 만든 뒤에 온다. 제안: `preset_json` 에
   `keep: bool` 과 `artifacts: bool`(글롭이 있는가)을 더하고 CLI 가 스냅샷 전에 종료 2 로 거절한다. 그러면
   `rcm presets` 와 SPA 도 keep 프리셋을 표시할 수 있다.
8. **`--keep --fetch-artifacts` 의 순서.** 받기가 먼저인가, 보관 대기가 먼저인가. 제안: 받기 먼저(세션이
   기다리는 것은 파일이다), 보관 줄은 그 뒤(CB24).
9. **보관 줄을 찍지 않는 끝들.** `--timeout` 으로 끝난 3, Ctrl-C detach, supersede 로 취소된 옛 잡. 옛 잡은
   keep 이 새 잡으로 넘어가는데(결정 128) 옛 잡의 묶음 상태로 「nothing to keep」 을 찍으면 틀린 말이다.
   제안: 3 과 detach 에는 보관 줄이 없다. supersede 는 `shelf: passed to #N with the job` 한 줄.
10. **`rcm keep` 여섯 갈래에 빠진 자리.** 보관 항목이 `pending` · `copying` 일 때, 묘비(`deleted`)인데 묶음이
    아직 살아 있을 때, 아직 도는 잡의 프리셋에 `artifacts` 가 없을 때(`armed` 인가 `no_bundle(disabled)`
    인가). 제안: 차례로 `requested`(202), `requested`(새로 보관), `no_bundle`(409).
11. **`POST /jobs/N/keep` 의 응답 본문 모양**이 명세에 없다. CLI 는 URL 과, `retouched` 이면 새 정리 예정일이
    있어야 CB6 의 줄을 그린다. 제안: `{result, job_id, keep, shelf_url, shelf: {state, reason_code},
    stale_at, delete_at}`.
12. **`rcm keep` · `rcm shelf` 의 종료 코드.** 409 · 410 을 2 로 볼지 1 로 볼지, 그리고 옛 서버의 경로 404 를
    「없는 잡」 404 와 어떻게 가를지. 제안: 2, 그리고 404 면 health 의 `shelf` 키로 다시 확인(CG17).
13. **`rcm shelf` 의 기본 `--state` 와 범위.** `all` 이 `pending` · `failed` · `deleted` 도 포함하는가. 날짜는
    클라이언트 시간대인가 서버의 `[display] timezone` 인가. 제안: 기본은 active + stale, `all` 은 여섯 상태
    전부, 날짜는 서버 시간대(페이지와 같은 날짜가 보이도록).
14. **`rcm shelf rm` 의 남은 갈래.** 이미 지운 항목(410)은 성공(0)인가. `pending` · `copying` 항목을 지울 수
    있는가. 409 `shelf_item_busy` 의 종료 코드. 제안: 410 은 0(같은 결과), 보관하는 중이면 409 로 거절,
    busy 는 2.
15. **`rcm check` 행의 모양.** 명세는 `shelf  ok  3 active …` 라 썼지만 코드는 `ok   shelf         …`(등급이
    먼저, 이름은 13칸)이다. `shelf  off` 의 `off` 는 등급 칸에 올 수 없다(ok · warn · FAIL 뿐). 제안: 오늘의
    모양을 따르고 `off` 는 설명 칸으로(CG20).
16. **바이트 눈금.** 명세의 `12.3 GB / 50 GB` 는 상한 50 GiB 를 `50 GB` 로 적었다. 오늘의 `render_text._bytes`
    는 십진이라 같은 값이 `53.7 GB` 로 보인다. 페이지 · `rcm check` · `rcm shelf` 가 어느 눈금을 쓰는가(CG37).
17. **`rcm check` 의 보관함 URL 은 어디서 오는가.** health · status 의 `shelf_storage` 에는 URL 이 없다(폴더
    경로를 빼라는 규칙은 URL 과 다르다). 제안: `shelf_storage.url` 을 `base_url + "/shelf/"` 로 싣는다.
    클라이언트가 `client.server` 로 만들면 `public_url` 과 다를 수 있다.
18. **`rcm check --config` 의 `orphans` 를 세려면 DB 를 읽어야 한다.** `Store` 로 열면 마이그레이션한다(운영
    DB 를 v24 로 올려 버린 2026-09-10 사고와 같다). 제안: `mode=ro` sqlite 로 `shelf_items` 만 읽고, 표가 없으면
    `orphans ?`. 행 이름은 서버 행과 섞이지 않게 `local shelf` 로(M5i I4 의 `local data dir` 선례)(CG22 · CG23).
19. **`rcm check` 의 「폴더에 못 씀」 FAIL 의 `error_code`** 가 정해지지 않았다. `shelf_dir_missing` ·
    `copier_dead` 말고 이 경우의 코드 이름이 필요하다.
20. **보관 실패 이유 코드의 목록 상수가 없다.** 명세 곳곳에 `disabled` · `shelf_full` · `disk_low` ·
    `name_collision` · `dest_exists` · `timeout` · `bundle_gone` · `missing` 이 흩어져 있고, 제어문자 멤버 거절과
    「예외면 failed」 의 코드는 이름이 없다. SPA · CLI · 페이지 문구를 잠그려면 `core/shelf.py` 에
    `SHELF_REASON_CODES` 같은 상수가 있어야 한다(CG34).
21. **웹훅의 `shelf_url` 은 어디에 있나.** 맨 위 키인지 `job` 행 안인지. P2 가 `recent_json` 에 `keep` 과
    `shelf`(keep 이 아니면 `null`)를 더하므로, 웹훅의 `job` 에는 keep 이 아닌 잡에도 `shelf: null` 이 실린다.
    「keep 이 아닌 잡은 웹훅에 키가 없다」 는 맨 위 `shelf_url` 에 대한 말로 읽었다(CB13 · CG25).
22. **알림 때 보관이 `none` · `failed` 로 확정이면** `RCM_SHELF_URL` 을 그래도 싣는가. 그 링크는 404 다.
    제안: `pending` · `copying` · `active` 일 때만 싣고, 확정 실패면 빈 값.
23. **SPA 지우기 버튼의 조건.** §4.5 · §4.7 은 「토큰이 있으면」, 결정 146 은 「admin 토큰만」 이다. admin 이
    아닌 토큰으로 보이는 버튼은 늘 403 이다. 제안: `state.admin === true` 일 때만(CG31).
24. **SPA 링크의 `href`.** 행의 `shelf.url` 은 `base_url` 로 만든 절대 URL 이라, `public_url` 이 브라우저가 연
    호스트와 다르면 다른 출처로 간다. 제안: 경로 `/shelf/<id>/` 만 쓴다(CG30).
25. **SPA nav.** 오늘의 `renderNav` 는 릴리스 저장소가 없으면 nav 를 숨긴다. 보관함 링크만 있을 때도 보여야
    한다. 꺼졌지만 남은 항목이 있을 때 링크를 둘지, SPA 언어를 `?lang=` 으로 넘길지도 정해야 한다. 제안: 켜져
    있을 때만, 언어는 넘긴다(CB16 · CG32).
26. **서버 페이지 문구의 주인과 자리.** §6 은 서버 HTML 을 P2 에 두었는데 이 역할은 그 문구를 잠근다.
    카탈로그가 어디 있는지(파이썬 쪽 사전인가), 두 언어 키 집합을 무엇으로 잠그는지, `?lang=EN` 을 받는지,
    영어 페이지의 항목 링크 · 되살리기 form · 그 뒤의 303 이 `?lang=en` 을 이어 가는지가 정해져야 한다
    (CG35 · CG36).
27. **목록의 「정리 예정일」 한 칸**은 무엇인가. 보관 중 항목에는 `stale_at`(정리 대기로 옮길 날), 정리 대기
    항목에는 `delete_at`(지울 날)으로 읽었다. 두 날짜가 한 칸에 섞이면 머리말이 달라야 한다(CB8 · CB19).
28. **공인 IP 를 bind 하면 링크가 421 이다.** `server_base_url` 의 「특정 bind」 갈래는 `http://203.0.113.5:8787`
    을 만들지만 그 호스트는 Host 허용 목록(결정 148) 밖이다. CLI · 알림이 찍은 보관 링크가 열리지 않는다.
    문서에 적을지, 허용 목록에 bind 주소를 더할지(CG40).
29. **`rcm gc --dry-run` 의 보관함 한 줄**(§5.4)은 어느 PR 의 몫인가. P3 의 CLI 범위에도, P1 의 청소 범위에도
    적혀 있지 않다.
