"""보관함(M5p) 서버 HTML 의 사람이 읽는 문구 — 한국어 기본 · `?lang=en` · 시간대 · 사용량 · 날짜.

시나리오 docs/m5p-test-scenarios-c.md 의 CB17~CB20 · CG35~CG39 와 b 의 B24 다. 명세 §15.6 이 이
문구를 P2(`shelf_pages.py`, ko · en 카탈로그)에 두었다. 시각은 `[display] timezone` 으로 보이고,
날짜는 그 시간대의 달력 날짜다 — 자정 근처(서울 01:30 = UTC 16:30)를 골라 UTC 날짜를 그대로 찍는
버그를 잡는다. 바이트는 `rcm check` · `rcm shelf` 와 같은 십진 눈금(`render_text._bytes`)이다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from shelfkit import (
    DAY,
    PNG_1PX,
    finished_linux_job,
    make_server,
    publish_payload,
    shelve_bundle,
    stop_copier,
    tar_bytes,
)

HANGUL = re.compile(r"[가-힣]")
LATE = datetime(2026, 11, 19, 16, 30, tzinfo=UTC)  # 서울 2026-11-20 01:30


@pytest.fixture
def srv(tmp_path):
    s = make_server(tmp_path, shelf_max_bytes=50 * 1024**3)
    s.cfg.display.timezone = "Asia/Seoul"
    s.clock.now = LATE
    yield s
    s.close()


def html(srv, path: str) -> tuple[int, str]:
    status, _h, body = srv.req("GET", path, raw=True)
    return status, body.decode()


def three(srv) -> tuple[int, int, int]:
    """보관 중 하나 · 정리 대기 하나 · 최근 지움 하나."""
    old = shelve_bundle(srv, {"out/home.png": PNG_1PX})
    srv.clock.advance(183 * DAY)
    kept = shelve_bundle(srv, {"out/home.png": PNG_1PX})
    gone = shelve_bundle(srv, {"out/b.txt": b"b"})
    assert srv.req("DELETE", f"/shelf/{gone}", token="admin")[0] == 200
    return kept, old, gone


def test_cb17_the_list_is_korean_by_default(srv):
    three(srv)
    status, page = html(srv, "/shelf/")
    assert status == 200 and '<html lang="ko">' in page
    for word in ("보관함", "보관 중", "정리 대기", "최근 지움(30일)", "되살리기"):
        assert word in page, word
    assert re.search(r"\d+(\.\d)? (B|KB|MB|GB) / 53\.7 GB", page), page[:400]


def test_cb18_the_english_list_has_no_korean_at_all(srv):
    three(srv)
    status, page = html(srv, "/shelf/?lang=en")
    assert status == 200 and '<html lang="en">' in page
    for word in ("Shelf", "Kept", "Cleanup pending", "Recently removed (30 days)", "Restore"):
        assert word in page, word
    assert HANGUL.findall(page) == []


def test_cb19_times_and_the_cleanup_date_are_in_the_display_timezone(srv):
    jid = shelve_bundle(srv, {"out/home.png": PNG_1PX})
    status, page = html(srv, f"/shelf/{jid}/")
    assert status == 200
    assert "2026-11-20 01:30" in page  # 끝난 시각(서울)
    assert "2027-05-21" in page  # 정리 대기 예정(182일 뒤, 서울)
    assert "2027-05-20" not in page


def test_cb20_a_stale_page_has_no_pictures_and_says_when_it_goes(srv):
    jid = shelve_bundle(srv, {"out/home.png": PNG_1PX})
    status, active = html(srv, f"/shelf/{jid}/")
    assert f'<img src="/shelf/{jid}/f/out/home.png"' in active
    srv.clock.advance(183 * DAY)
    status, page = html(srv, f"/shelf/{jid}/")
    assert status == 200 and "<img" not in page
    assert f'action="/shelf/{jid}/restore"' in page and "<table" in page
    assert "2027-11-20 에 지워집니다" in page
    status, page = html(srv, f"/shelf/{jid}/?lang=en")
    assert "Removed on 2027-11-20" in page and HANGUL.findall(page) == []


@pytest.mark.parametrize("query", ["?lang=fr", "?lang=EN", "?lang="])
def test_cg35_anything_but_lang_en_is_korean(srv, query):
    shelve_bundle(srv, {"out/home.png": PNG_1PX})
    status, page = html(srv, f"/shelf/{query}")
    assert status == 200 and '<html lang="ko">' in page and "보관함" in page


def test_cg36_english_links_and_forms_keep_the_language(srv):
    kept, old, _gone = three(srv)
    _s, page = html(srv, "/shelf/?lang=en")
    assert f'href="/shelf/{kept}/?lang=en"' in page
    assert f'action="/shelf/{old}/restore?lang=en"' in page
    _s, item = html(srv, f"/shelf/{kept}/?lang=en")
    assert f'href="/shelf/{kept}/f/out/home.png"' in item
    assert "/f/out/home.png?lang=en" not in item
    assert f'href="/shelf/{kept}.zip"' in item


def test_cg37_usage_is_decimal_like_rcm_check(srv):
    jid = shelve_bundle(srv, {"out/home.png": PNG_1PX})
    srv.store.mark_shelf(jid, "active", srv.clock.now, total_bytes=12_300_000_000)
    _s, page = html(srv, "/shelf/")
    assert "12.3 GB / 53.7 GB" in page


def test_cg38_a_pending_item_says_it_is_being_copied(srv):
    stop_copier(srv)  # 보관 행이 pending 에 머문다
    jid = finished_linux_job(srv)
    publish_payload(srv, jid, tar_bytes([("out/a.txt", b"a")]))
    status, page = html(srv, f"/shelf/{jid}/")
    assert status == 200 and "보관하는 중" in page and "<table" not in page
    _s, page = html(srv, f"/shelf/{jid}/?lang=en")
    assert "Copying to the shelf…" in page


def test_cg39_a_tombstone_says_when_it_was_removed(srv):
    jid = shelve_bundle(srv, {"out/home.png": PNG_1PX})
    assert srv.req("DELETE", f"/shelf/{jid}", token="admin")[0] == 200
    status, page = html(srv, f"/shelf/{jid}/")
    assert status == 410 and "2026-11-20 에 지웠습니다" in page
    status, page = html(srv, f"/shelf/{jid}/?lang=en")
    assert status == 410 and "Removed on 2026-11-20" in page


def test_b24_language_and_timezone_on_the_item_page(srv):
    srv.clock.now = datetime(2026, 9, 6, 15, 30, tzinfo=UTC)
    jid = shelve_bundle(srv, {"out/home.png": PNG_1PX})
    _s, page = html(srv, f"/shelf/{jid}/")
    assert '<html lang="ko">' in page and "2026-09-07 00:30" in page
    _s, page = html(srv, f"/shelf/{jid}/?lang=en")
    assert '<html lang="en">' in page and "Finished" in page


def test_a_failed_item_page_names_the_reason(srv):
    srv.cfg.server.shelf_max_bytes = 1
    jid = shelve_bundle(srv, {"out/a.txt": b"aa"}, states=("failed",))
    status, page = html(srv, f"/shelf/{jid}/")
    assert status == 200 and "shelf_full" in page
    assert srv.req("GET", f"/shelf/{jid}/f/out/a.txt")[0] == 404
