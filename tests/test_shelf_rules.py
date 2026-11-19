"""보관함(M5p) 순수 규칙 — `core/shelf.py` 의 함수 전부 · 상태 이름 · `ShelfItem` · `Job.keep`.

명세는 docs/m5p-workplan.md §4.3(시계) · §4.4(펼치기) · §4.6(경로 · 이름 · Host · 출처) · §6(계약) ·
§15(답 — 앞 절보다 이긴다). 시나리오 표는 docs/m5p-test-scenarios-a.md 의 A1~A15 · A47~A58 ·
A69~A73 · A84 · A87 · A123 · A127 · A128 · A139~A146.

- 아직 없는 모듈은 테스트 **안에서** 부른다 — 파일 하나가 통째로 수집 오류가 되지 않고 시나리오마다
  따로 빨개진다.
- 시각은 고정 `T` 기준 float(UTC 초)다. sleep 은 없다.
"""

from __future__ import annotations

import dataclasses
import random
import time
import unicodedata
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import pytest

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
T = NOW.timestamp()
D = 86400
H = 3600
M = 60


def shelf() -> Any:
    import remote_ci_monitor.core.shelf as mod

    return mod


def item(job_id: int, state: str, **kw: Any) -> Any:
    """`ShelfItem` 하나. 필요한 열만 주고 나머지는 비운다."""
    from remote_ci_monitor.core.model import ShelfItem

    names = [f.name for f in dataclasses.fields(ShelfItem)]
    values: dict[str, Any] = dict.fromkeys(names)
    values.update(job_id=job_id, state=state, preset="golden-update", requested_at=T - 400 * D)
    values.update(kw)
    return ShelfItem(**values)


# ── 상태 이름 (A1 · §15.1) ───────────────────────────────────────────────────


def test_a1_state_names():
    s = shelf()
    assert s.SHELF_STATES == ("pending", "copying", "active", "stale", "failed", "deleted")
    # §15.1 — 공개 상태에는 `waiting`(아직 묶음이 안 됨)과 `none`(묶음이 ready 가 아닌 채 끝남)
    assert s.PUBLIC_SHELF_STATES == (
        "waiting",
        "pending",
        "copying",
        "active",
        "stale",
        "failed",
        "deleted",
        "none",
    )


def test_reason_codes_cover_every_code_the_store_and_janitor_write():
    """§15.6 — SPA · CLI · 페이지 문구가 이 튜플로 잠긴다. 실패와 `none` 의 이유가 다 있다."""
    codes = shelf().SHELF_REASON_CODES
    assert isinstance(codes, tuple) and len(codes) == len(set(codes))
    for code in (
        "disabled",
        "unavailable",
        "shelf_full",
        "disk_low",
        "name_collision",
        "dest_exists",
        "timeout",
        "missing",
        "bundle_gone",
        "copy_error",
        "no_safe_files",
        "not_collected_on_success",
        "no_artifacts",
        "forgotten",
        # P2 격리 검증 — 멤버 수 폭탄 · 업로드 도중 유실 · 묶음 없이 유실
        "too_many_files",
        "interrupted",
        "job_lost",
    ):
        assert code in codes, code


# ── 시계 (A2 · A3 · A69 · A70 · A128) ────────────────────────────────────────


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(0, "active"), (100 * D, "active"), (200 * D, "stale"), (400 * D, "delete")],
)
def test_a2_three_stages(elapsed, expected):
    assert shelf().stage(T, T + elapsed, 182, 365) == expected


def test_a3_due_dates_are_where_stage_turns():
    s = shelf()
    stale_at, delete_at = s.due_dates(T, 182, 365)
    assert (stale_at, delete_at) == (T + 182 * D, T + 365 * D)
    assert s.stage(T, stale_at, 182, 365) == "stale"
    assert s.stage(T, delete_at, 182, 365) == "delete"


def test_a69_the_182_day_boundary():
    s = shelf()
    assert s.stage(T, T + 182 * D - 1, 182, 365) == "active"
    assert s.stage(T, T + 182 * D, 182, 365) == "stale"


def test_a70_the_365_day_boundary():
    s = shelf()
    assert s.stage(T, T + 365 * D - 1, 182, 365) == "stale"
    assert s.stage(T, T + 365 * D, 182, 365) == "delete"


def test_a128_a_future_last_use_is_active():
    """§15.3 — 시계가 뒤로 가면 경과를 0 으로 본다."""
    assert shelf().stage(T + H, T, 182, 365) == "active"


# ── 쓰기 줄이기 (A4 · A71 · A72 · A146) ──────────────────────────────────────


def test_a4_first_touch_and_after_an_hour_write():
    s = shelf()
    assert s.should_touch(None, "active", T) is True
    assert s.should_touch(T, "active", T + 61 * M) is True


def test_a71_the_one_hour_boundary():
    s = shelf()
    assert s.should_touch(T, "active", T + 59 * M) is False
    assert s.should_touch(T, "active", T + 60 * M) is True  # 「1시간 이상」
    assert s.should_touch(T, "active", T + 61 * M) is True


def test_a72_a_stale_item_writes_a_minute_later():
    assert shelf().should_touch(T, "stale", T + M) is True


@pytest.mark.parametrize("state", ["pending", "copying", "failed", "deleted", "bogus"])
def test_a146_touch_only_active_and_stale(state):
    assert shelf().should_touch(T - 2 * H, state, T) is False


def test_a146_a_clock_that_went_back_does_not_write():
    assert shelf().should_touch(T + 60, "active", T) is False


# ── 「쓴다」 표 (A5 · A73 · §15.3 · §15.5) ─────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "route", "state"),
    [
        ("GET", "item", "active"),
        ("GET", "file", "active"),
        ("GET", "file", "stale"),
        ("GET", "zip", "active"),
        ("GET", "zip", "stale"),
        ("POST", "restore", "stale"),
        ("POST", "restore", "active"),
        ("POST", "keep", "active"),
        ("POST", "keep", "stale"),
        # §15.5 — 삭제 단계가 지났는데 아직 안 지운 항목은 stale 로 대한다
        ("GET", "file", "delete"),
        ("POST", "restore", "delete"),
    ],
)
def test_a5_routes_that_use_the_item(method, route, state):
    assert shelf().touches(method, route, state) is True


@pytest.mark.parametrize("route", ["item", "file", "zip", "restore", "keep", "list", "latest"])
@pytest.mark.parametrize("state", ["active", "stale"])
def test_a73_head_never_uses(route, state):
    assert shelf().touches("HEAD", route, state) is False


@pytest.mark.parametrize(
    ("method", "route", "state"),
    [
        ("GET", "item", "stale"),  # 정리 대기 페이지는 지울지 판단하려고 보는 것이다
        ("GET", "item", "delete"),
        ("GET", "list", "active"),
        ("GET", "latest", "active"),
        ("GET", "api", "active"),
        ("GET", "bogus", "active"),  # §15.3 — 모르는 route 는 안 쓴다
        ("GET", "file", "pending"),
        ("GET", "file", "failed"),
        ("GET", "file", "deleted"),
        ("GET", "restore", "stale"),  # 되살리기는 POST 다
    ],
)
def test_a73_routes_that_do_not_use(method, route, state):
    assert shelf().touches(method, route, state) is False


# ── 정리 계획 (A6 · A123 · A125 · A127 · A77) ────────────────────────────────


def ids(items: Any) -> list[int]:
    return [i.job_id for i in items]


def test_a6_sweep_plan():
    s = shelf()
    a = item(1, "active", last_used_at=T - 200 * D)
    b = item(2, "stale", last_used_at=T - 400 * D, stale_since=T - 218 * D)
    c = item(3, "deleted", deleted_at=T - 31 * D)
    d = item(4, "active", last_used_at=T - 10 * D)
    plan = s.plan_sweep([a, b, c, d], T, 182, 365)
    assert ids(plan.to_stale) == [1]
    assert ids(plan.to_delete) == [2]
    assert ids(plan.to_forget) == [3]
    assert ids(plan.to_active) == []
    assert [f.name for f in dataclasses.fields(plan)] == [
        "to_stale",
        "to_active",
        "to_delete",
        "to_forget",
    ]


def test_a123_a_server_off_for_a_year_deletes_in_one_round():
    plan = shelf().plan_sweep([item(1, "active", last_used_at=T - 400 * D)], T, 182, 365)
    assert ids(plan.to_delete) == [1]
    assert ids(plan.to_stale) == [] and ids(plan.to_active) == []


def test_a125_a_longer_stale_setting_turns_stale_back_to_active():
    """§15.3 — `to_active` 는 설정을 늘려 재시작한 경우의 되돌림이다."""
    plan = shelf().plan_sweep([item(1, "stale", last_used_at=T - 200 * D)], T, 300, 365)
    assert ids(plan.to_active) == [1]
    assert ids(plan.to_stale) == [] and ids(plan.to_delete) == []


def test_a127_the_plan_never_touches_rows_in_flight():
    s = shelf()
    rows = [
        item(1, "pending", last_used_at=T - 900 * D),
        item(2, "copying", last_used_at=T - 900 * D),
        item(3, "failed", last_used_at=T - 900 * D, failed_at=T - 5 * D),
    ]
    plan = s.plan_sweep(rows, T, 182, 365)
    assert ids(plan.to_stale) == [] and ids(plan.to_delete) == [] and ids(plan.to_active) == []
    assert ids(plan.to_forget) == []
    old = item(3, "failed", failed_at=T - 30 * D)
    assert ids(s.plan_sweep([old], T, 182, 365).to_forget) == [3]


def test_a77_the_tombstone_boundary():
    s = shelf()
    keep = item(1, "deleted", deleted_at=T - 30 * D + 1)
    gone = item(2, "deleted", deleted_at=T - 30 * D)
    assert ids(s.plan_sweep([keep, gone], T, 182, 365).to_forget) == [2]


def test_a_failed_row_without_failed_at_falls_back_to_requested_at():
    """옛 행이나 손으로 만든 행 — 실패 시각이 없으면 요청 시각에서 잰다(영원히 남지 않게)."""
    row = item(1, "failed", failed_at=None, requested_at=T - 31 * D)
    assert ids(shelf().plan_sweep([row], T, 182, 365).to_forget) == [1]


# ── 인라인 · 내려받기 (A7 · A54 · A143) ──────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "ctype"),
    [
        ("home.png", "image/png"),
        ("a.jpg", "image/jpeg"),
        ("a.jpeg", "image/jpeg"),
        ("a.gif", "image/gif"),
        ("a.webp", "image/webp"),
        ("run.log", "text/plain; charset=utf-8"),
        ("n.txt", "text/plain; charset=utf-8"),
        ("m.json", "text/plain; charset=utf-8"),
        ("r.md", "text/plain; charset=utf-8"),
        ("t.csv", "text/plain; charset=utf-8"),
        ("dir/sub/home.png", "image/png"),
        ("A.PNG", "image/png"),  # §15.4 — 대소문자를 가리지 않는다
        ("B.Jpeg", "image/jpeg"),
    ],
)
def test_a7_inline_allow_list(path, ctype):
    assert shelf().inline_disposition(path) == (ctype, True)


@pytest.mark.parametrize(
    "path", ["x.html", "x.htm", "x.xhtml", "x.svg", "x.js", "x.pdf", "x", "x.png.html", "x.SVG"]
)
def test_a54_everything_else_downloads(path):
    assert shelf().inline_disposition(path) == ("application/octet-stream", False)


# ── Content-Disposition (A8 · A55 · A142) ────────────────────────────────────


def test_a8_ascii_name():
    s = shelf()
    inline = s.content_disposition("test/home/home.png", True)
    assert inline.startswith("inline;")
    assert 'filename="home.png"' in inline
    assert "filename*=UTF-8''home.png" in inline
    assert "test/home" not in inline  # basename 만(§15.4)
    assert s.content_disposition("test/home/home.png", False).startswith("attachment;")


def _star(value: str) -> str:
    return value.split("filename*=UTF-8''", 1)[1].split(";", 1)[0].strip()


def _plain(value: str) -> str:
    return value.split('filename="', 1)[1].split('"', 1)[0]


@pytest.mark.parametrize("name", ["결과.png", "a\r\nX-Evil: 1.txt", 'a"b\\c.txt'])
def test_a55_dangerous_names(name):
    value = shelf().content_disposition(name, False)
    value.encode("latin-1")  # 예외가 나면 send_header 가 500 을 낸다
    assert "\r" not in value and "\n" not in value
    plain = _plain(value)
    assert all(0x20 <= ord(c) < 0x7F and c not in '"\\' for c in plain), plain
    assert urllib.parse.unquote(_star(value)) == name


def test_a55_the_ascii_fallback_replaces_with_underscores():
    assert _plain(shelf().content_disposition("결과.png", True)) == "__.png"


def test_a142_percent_and_a_long_name():
    s = shelf()
    value = s.content_disposition("100%.png", True)
    assert "%25" in _star(value)
    long_name = "가" * 85  # UTF-8 255 바이트
    assert len(long_name.encode()) == 255
    out = s.content_disposition(f"dir/{long_name}", False)
    out.encode("latin-1")
    assert urllib.parse.unquote(_star(out)) == long_name


# ── safe_member (A9 · A52 · A53 · A144) ──────────────────────────────────────


@pytest.mark.parametrize(
    "path", ["build/.golden-update/goldens.tar.gz", "test/골든/홈.png", "goldens.tar.gz.d/home.png"]
)
def test_a9_normal_members(path):
    assert shelf().safe_member(path) is True


@pytest.mark.parametrize(
    "path", ["a\x01b", "a\x1fb", "a\x7fb", "line\nbreak.png", "x/.git/config", ".git"]
)
def test_a52_control_characters_and_git(path):
    assert shelf().safe_member(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../x",
        "a/../b",
        "a//b",
        "./a",
        "a\\b",
        "a\x00b",
        None,
        3,
        b"x",
        "x" * 256,
        "/".join(["a"] * 2049),  # 4097자
    ],
)
def test_a53_other_bad_paths(path):
    assert shelf().safe_member(path) is False


@pytest.mark.parametrize("path", [".GIT/config", "x/.Git/HEAD"])
def test_a144_git_in_any_case(path):
    """§15.4 — APFS 에서 `.GIT` 은 `.git` 과 같은 이름이다."""
    assert shelf().safe_member(path) is False


def test_normalize_member_drops_the_leading_dot_slash():
    """§15.4 — `tar -C dir .` 모양의 `./` 는 떼고 판정한다(copier 가 쓴다)."""
    s = shelf()
    assert s.normalize_member("./out/a.png") == "out/a.png"
    assert s.normalize_member("././a") == "a"
    assert s.normalize_member("out/") == "out"
    assert s.normalize_member("a") == "a"


# ── 펼칠 후보 (A10 · A139 · A143) ────────────────────────────────────────────


def test_a10_expand_candidates():
    paths = ["a.tar", "b.tar.gz", "c.tgz", "d.zip", "e.gz", "f.tar.bz2", "g.png"]
    assert shelf().expand_candidates(paths) == ["a.tar", "b.tar.gz", "c.tgz"]


def test_a143_extension_case_does_not_matter():
    assert shelf().expand_candidates(["A.PNG", "X.TAR.GZ", "Y.TGZ", "z.Tar"]) == [
        "X.TAR.GZ",
        "Y.TGZ",
        "z.Tar",
    ]


def test_a139_a_name_whose_dot_d_is_too_long_is_not_expanded():
    s = shelf()
    long = "x" * 249 + ".tar"  # 253 바이트 → `.d` 를 붙이면 255 바이트 — 경계 안
    longer = "x" * 250 + ".tar"  # 254 바이트 → 256 바이트
    assert s.expand_candidates([f"dir/{long}"]) == [f"dir/{long}"]
    assert s.expand_candidates([f"dir/{longer}"]) == []


# ── 이름 충돌 (A11 · A84 · A140) ─────────────────────────────────────────────


def test_a11_case_and_nfc():
    nfc = unicodedata.normalize("NFC", "x/é.png")
    nfd = unicodedata.normalize("NFD", "x/é.png")
    assert nfc != nfd
    got = shelf().name_collisions(["out/A.png", "out/a.png", nfc, nfd, "ok.png"])
    assert got == sorted([tuple(sorted(("out/A.png", "out/a.png"))), tuple(sorted((nfc, nfd)))])
    assert all(p != "ok.png" for pair in got for p in pair)


def test_a140_shapes():
    s = shelf()
    assert s.name_collisions(["a", "a-b", "a/b"]) == [("a", "a/b")]
    assert s.name_collisions(["x.png", "x.png"]) == [("x.png", "x.png")]
    assert len(s.name_collisions(["A.png", "a.png", "a.PNG"])) == 3


def test_a140_agrees_with_the_m5e_rule_on_random_sets():
    from remote_ci_monitor.core.artifacts import collisions

    rng = random.Random(5)
    parts = ["a", "A", "b", "é", unicodedata.normalize("NFD", "é"), "c.png", "C.PNG"]
    for _ in range(200):
        paths = [
            "/".join(rng.choice(parts) for _ in range(rng.randint(1, 3)))
            for _ in range(rng.randint(0, 8))
        ]
        assert shelf().name_collisions(paths) == collisions(paths), paths


def test_a84_fifty_thousand_paths_in_under_two_seconds():
    paths = [f"dir{i % 97}/file{i}.png" for i in range(50_000)]
    paths.append("dir3/FILE3.png")
    started = time.monotonic()
    got = shelf().name_collisions(paths)
    assert time.monotonic() - started < 2.0
    assert got == [("dir3/FILE3.png", "dir3/file3.png")]


# ── 상한 (A12 · A56 · A87 · A145) ────────────────────────────────────────────


def test_a12_inside_the_limits():
    assert shelf().capacity_verdict(10, 5, 100, 10**12, 10) is None


@pytest.mark.parametrize(
    ("stored", "actual", "limit", "free", "min_free", "expected"),
    [
        (0, 10, 0, 10**12, 10, "disabled"),
        (95, 10, 100, 10**12, 10, "shelf_full"),
        (0, 10, 100, 15, 10, "disk_low"),
        (95, 10, 100, 15, 10, "shelf_full"),
        (95, 10, 0, 15, 10, "disabled"),
    ],
)
def test_a56_verdict_order(stored, actual, limit, free, min_free, expected):
    assert shelf().capacity_verdict(stored, actual, limit, free, min_free) == expected


def test_a87_boundaries():
    s = shelf()
    assert s.capacity_verdict(90, 10, 100, 10**12, 0) is None
    assert s.capacity_verdict(91, 10, 100, 10**12, 0) == "shelf_full"
    assert s.capacity_verdict(0, 10, 100, 20, 10) is None
    assert s.capacity_verdict(0, 10, 100, 19, 10) == "disk_low"


def test_a145_unknown_free_space_skips_the_floor():
    """§15.3 — 못 쟀으면 바닥 검사를 건너뛴다(M5g 와 같다)."""
    assert shelf().capacity_verdict(0, 10, 100, None, 10**12) is None


# ── Host 허용 목록 (A13 · A57 · A141 · §15.5) ────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8787",
        "macmini.local:8787",
        "10.1.2.3",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.0.10:8787",
        "100.64.0.1",
        "100.127.255.255",
        "127.0.0.1:8787",
        "[fd00::1]:8787",
        "[::1]:8787",
        "MacMini.LOCAL:8787",
        "macmini.local.",
        "box.tail1234.ts.net",
    ],
)
def test_a13_allowed_hosts(host):
    assert shelf().host_allowed(host, public_host=None, hostname="macmini") is True


def test_a13_public_host_and_hostname():
    s = shelf()
    assert s.host_allowed("ci.example.org", public_host="ci.example.org", hostname="m") is True
    assert s.host_allowed("macmini:8787", public_host=None, hostname="macmini") is True
    assert s.host_allowed("MACMINI", public_host=None, hostname="macmini") is True
    assert s.host_allowed("builder", public_host=None, hostname="m", also=("builder",)) is True


@pytest.mark.parametrize(
    "host",
    [
        "evil.com",
        "evil.com:8787",
        "10.0.0.1.evil.com",
        "localhost.evil.com",
        "macmini.local.evil.com",
        "172.32.0.1",
        "172.15.255.255",
        "100.128.0.1",
        "100.63.255.255",
        "8.8.8.8",
        "",
        "[2001:db8::1]",
        "169.254.1.1",
        "0.0.0.0",
        "::1",  # 괄호 없는 IPv6 는 Host 헤더가 아니다
        "localhost:abc",
        ".local",
        "[fd00::1",
    ],
)
def test_a57_refused_hosts(host):
    assert shelf().host_allowed(host, public_host=None, hostname="macmini") is False


# ── 같은 출처 (A14 · A58 · A141) ─────────────────────────────────────────────

HOST = "macmini.local:8787"


@pytest.mark.parametrize(
    ("origin", "referer"),
    [
        ("http://macmini.local:8787", None),
        ("null", "http://macmini.local:8787/shelf/412/"),
        (None, "http://macmini.local:8787/shelf/412/"),
        ("http://MacMini.Local:8787", None),
    ],
)
def test_a14_same_origin(origin, referer):
    assert shelf().same_origin(HOST, origin, referer) is True


@pytest.mark.parametrize(
    ("origin", "referer"),
    [
        ("http://evil.com", None),
        (None, None),
        ("null", None),
        (
            "http://evil.com",
            "http://macmini.local:8787/shelf/1/",
        ),  # Origin 이 있으면 물러서지 않는다
        (None, "http://macmini.local:8787.evil.com/"),
        (None, "http://macmini.local:8787@evil.com/"),
        ("http://macmini.local:9999", None),
        ("http://macmini.local", None),
        ("https://macmini.local:8787", None),  # 앞단 TLS 가 없으면 https 는 다른 출처다
        ("ftp://macmini.local:8787", None),
        ("http://user@macmini.local:8787", None),
    ],
)
def test_a58_other_origins(origin, referer):
    assert shelf().same_origin(HOST, origin, referer) is False


def test_a141_https_origin_when_the_public_url_is_https():
    s = shelf()
    assert s.same_origin(HOST, "https://macmini.local:8787", None, schemes=("http", "https"))


# ── 모델 (A15) ───────────────────────────────────────────────────────────────


SHELF_COLUMNS = [
    "job_id",
    "state",
    "preset",
    "inputs_json",
    "repo",
    "branch",
    "sha",
    "source_mode",
    "requester",
    "job_state",
    "job_finished_at",
    "requested_at",
    "shelved_at",
    "last_used_at",
    "stale_since",
    "deleted_at",
    "file_count",
    "total_bytes",
    "reason_code",
    "reason_args",
    "error_code",
    "failed_at",  # §15.3 — 실패 30일은 여기서 잰다
]


def test_a15_models():
    from jobfactory import job
    from remote_ci_monitor.core.model import ShelfItem

    assert job(1).keep is False
    assert [f.name for f in dataclasses.fields(ShelfItem)] == SHELF_COLUMNS


# ── 격리 검증이 찾은 것 (PR #217 · I5) ───────────────────────────────────────


@pytest.mark.parametrize(
    ("origin", "schemes", "expected"),
    [
        ("http://macmini.local", ("http",), True),
        ("http://macmini.local:80", ("http",), True),
        ("http://macmini.local:443", ("http",), False),  # 포트 없는 Host 는 그 scheme 의 기본 포트
        ("https://macmini.local", ("http", "https"), True),
        ("https://macmini.local:80", ("http", "https"), False),
    ],
)
def test_i5_a_host_without_a_port_means_the_schemes_default_port(origin, schemes, expected):
    assert shelf().same_origin("macmini.local", origin, None, schemes=schemes) is expected
