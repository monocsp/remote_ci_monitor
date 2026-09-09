"""캡처한 원본 → 주석(빨간 사각형 + 번호) 이미지. 결과는 `docs/images/ui/`.

    python tools/screenshots/capture.py all     # 원본을 먼저 만든다
    python tools/screenshots/build.py           # 주석을 그려 docs/images/ui/ 에 넣는다

번호는 사용법 가이드(`docs/usage.md` · `docs/usage.ko.md`)의 ①②③ 과 짝을 이룬다. 번호를
바꾸면 두 언어의 문서도 같이 고쳐야 한다. 이미지 하나는 400 KB 를 넘지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

CAP = Path(__file__).resolve().parent
ROOT = CAP.parents[1]
WORK = Path(os.environ.get("RCM_SHOT_WORK") or Path(tempfile.gettempdir()) / "rcm-screenshots")
RAW = WORK / "raw"
OUT = ROOT / "docs" / "images" / "ui"

sys.path.insert(0, str(CAP))
from annotate import annotate_file, save_png  # noqa: E402
from PIL import Image  # noqa: E402
from term import render  # noqa: E402

Rect = tuple[float, float, float, float]
sizes: dict[str, int] = {}


# ── 원본 좌표 도우미 ─────────────────────────────────────────────────────────


def rects(shot: str) -> dict:
    return json.loads((RAW / f"{shot}.json").read_text())


def pick(
    d: dict, sel: str, *, idx: int = 0, job: str | None = None, text: str | None = None
) -> Rect:
    items = [e for e in (d.get(sel) or []) if e["w"] > 0 and e["h"] > 0]
    if job is not None:
        items = [e for e in items if e["job"] == job]
    if text is not None:
        items = [e for e in items if text in (e["text"] or "")]
    assert items, f"{sel} (job={job}, text={text}) not found"
    return (items[idx]["x"], items[idx]["y"], items[idx]["w"], items[idx]["h"])


def union(*rs: Rect) -> Rect:
    x0 = min(r[0] for r in rs)
    y0 = min(r[1] for r in rs)
    x1 = max(r[0] + r[2] for r in rs)
    y1 = max(r[1] + r[3] for r in rs)
    return (x0, y0, x1 - x0, y1 - y0)


def sub(r: Rect, *, h: float | None = None, w: float | None = None) -> Rect:
    return (r[0], r[1], w if w is not None else r[2], h if h is not None else r[3])


def on_row(d: dict, sel: str, r: Rect) -> Rect:
    """`r` 과 세로로 겹치는 `sel` 중 가장 왼쪽 것. 같은 줄의 이웃(예: 결과 필 옆의 잡 번호)을
    고를 때 쓴다 — 문서 순서로 idx 를 세면 위아래 줄을 잘못 집는다."""
    items = [
        e
        for e in (d.get(sel) or [])
        if e["w"] > 0 and e["h"] > 0 and e["y"] < r[1] + r[3] and e["y"] + e["h"] > r[1]
    ]
    assert items, f"{sel} on the row at y={r[1]} not found"
    e = min(items, key=lambda e: e["x"])
    return (e["x"], e["y"], e["w"], e["h"])


def out(name: str, src: str, boxes, crop=None, **kw) -> None:
    sizes[name] = annotate_file(RAW / f"{src}.png", OUT / name, boxes, crop=crop, **kw)


# ── 터미널 도우미 ────────────────────────────────────────────────────────────


def lines(name: str, *, wrap: int | None = None) -> list[str]:
    raw = (RAW / f"{name}.txt").read_text().rstrip("\n").split("\n")
    if wrap is None:
        return raw
    out_lines: list[str] = []
    for ln in raw:
        if len(ln) > wrap:
            out_lines.extend(
                textwrap.wrap(ln, width=wrap, break_long_words=True, break_on_hyphens=False)
            )
        else:
            out_lines.append(ln)
    return out_lines


def at(ls: list[str], start: str, *, after: int = 0) -> int:
    """`start` 로 시작하는 첫 줄의 번호(0 기반). 없으면 KeyError — 캡처가 달라졌다는 뜻이다."""
    for i, ln in enumerate(ls):
        if i >= after and ln.lstrip().startswith(start):
            return i
    raise KeyError(f"{start!r} not in capture")


def term(name: str, ls: list[str], boxes, *, title: str) -> None:
    sizes[name] = render(ls, OUT / name, boxes=boxes, title=title)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # ── 웹 ──────────────────────────────────────────────────────────────────
    d = rects("queue")
    # 3 은 행 자체, 4 는 그 아래 막대, 5 는 펼친 스텝 목록 — 상자를 겹쳐 그리지 않는다
    row_a = pick(d, "tr[data-job]", job="4", idx=0)
    bar_a = pick(d, "tr.qbar", job="4")
    steps_a = union(
        pick(d, ".prog .head", idx=0), pick(d, ".minibar", idx=0), pick(d, ".steps", idx=0)
    )
    pool = union(
        pick(d, ".pool-h", job="mac2"),
        pick(d, "tr[data-job]", job="6", idx=0),
        pick(d, "tr.qbar", job="6"),
    )
    # 히어로: 주석 없는 큐 화면(README 맨 위)
    hero = Image.open(RAW / "queue.png").crop((0, 0, 1280, 745))
    sizes["hero-queue.png"] = save_png(hero, OUT / "hero-queue.png")

    out(
        "web-queue.png",
        "queue",
        [
            (1, pick(d, "#summary")),
            (2, pick(d, "tr.qgroup.running"), "l"),
            (3, row_a),
            (4, bar_a, "r"),  # 막대 상자는 얇다 — 번호를 오른쪽 밖에 둬야 5 와 안 겹친다
            (5, steps_a, "l"),
            (6, union(pick(d, "tr.qgroup.waiting"), pick(d, "tr[data-job]", job="5")), "l"),
            (7, pick(d, "td.eta", idx=1)),
            (8, pool),
        ],
        crop=(0, 0, 1280, 745),
    )
    out(
        "web-header.png",
        "queue",
        [
            (1, pick(d, ".wk", idx=0)),
            (2, pick(d, ".wk", idx=1)),
            (3, pick(d, "#live-btn")),
            (4, pick(d, "#tok-btn")),
        ],
        crop=(0, 0, 1280, 150),
    )

    y = rects("yours")
    out(
        "web-your-jobs.png",
        "yours",
        [
            (1, pick(y, "#tok-btn")),
            (2, pick(y, ".sum-yours"), "l"),
            (3, pick(y, ".you", idx=0), "tr"),
            (4, pick(y, ".tail", idx=0), "l"),
            (5, union(pick(y, ".actions .log", job="4"), pick(y, ".actions .cancel", idx=0)), "l"),
        ],
        crop=(0, 0, 1280, 600),
    )

    lg = rects("log")
    out(
        "web-log.png",
        "log",
        [
            (1, pick(lg, "[data-drawer-title]"), "l"),
            (2, sub(pick(lg, "[data-drawer-log]"), h=130), "l"),
            (3, pick(lg, "[data-drawer-close]"), "l"),
        ],
        crop=(0, 0, 1280, 520),
    )

    r = rects("recent")
    failed_pill = pick(r, ".pill", text="failed")
    out(
        "web-recent.png",
        "recent",
        [
            (1, union(on_row(r, ".rrow .id", failed_pill), failed_pill), "l"),
            (2, pick(r, ".rdetail"), "l"),
            (3, pick(r, ".rerun"), "tr"),
            (4, pick(r, ".chip", text="pool"), "tr"),
            (5, union(pick(r, "[data-more-recent]"), pick(r, "#estimates")), "l"),
        ],
        crop=(0, 300, 1280, 700),
    )

    h = rects("host")
    out(
        "web-host.png",
        "host",
        [
            (1, pick(h, ".hostcard .hn", idx=0), "l"),
            (2, pick(h, '[data-metric="cpu"]', idx=0), "t"),
            (3, pick(h, '[data-metric="mem"]', idx=0), "t"),
            (4, pick(h, '[data-metric="disk"]', idx=0), "t"),
            (5, pick(h, '[data-metric="gpu"]', idx=0), "t"),
            (6, pick(h, ".hostcard .top", idx=0), "l"),
            (7, pick(h, ".hostcard", idx=1), "l"),
        ],
        # 카드가 디스크 미터만큼 자랐다(M5d-2) — `#host` 절 전체를 담게 잘라 낸다
        crop=(0, 55, 1280, 486),
    )

    dn = rects("down")
    out(
        "web-worker-down.png",
        "down",
        [
            (1, pick(dn, "#banner-note")),
            (2, pick(dn, ".wk", text="down")),
            (3, pick(dn, ".pool-h", job="mac2"), "l"),
        ],
        crop=(0, 0, 1280, 330),
    )

    p = rects("phone")
    out(
        "web-phone.png",
        "phone",
        [
            (1, union(pick(p, ".wk", idx=0), pick(p, ".wk", idx=1)), "l"),
            (2, pick(p, "#summary"), "tr"),
            (3, pick(p, "tr[data-job]", job="4", idx=0), "tr"),
            (4, pick(p, ".steps", idx=0), "l"),
            (5, pick(p, ".actions", idx=0), "tr"),
        ],
    )

    # ── 터미널 ──────────────────────────────────────────────────────────────
    ls = lines("x-setup")
    i_init, i_add, i_list = (
        at(ls, "$ rcm init server"),
        at(ls, "$ rcm token add"),
        at(ls, "$ rcm token list"),
    )
    term(
        "cli-setup.png",
        ls,
        [(1, i_init, i_add - 1), (2, i_add + 1, i_add + 2), (3, i_list + 1, len(ls) - 1)],
        title="rcm init server · rcm token add",
    )

    if (RAW / "x-discover.txt").is_file():
        ls = lines("x-discover")
        term(
            "cli-discover.png",
            ls,
            [(1, at(ls, "name"), at(ls, "name")), (2, at(ls, "name") + 1, len(ls) - 1)],
            title="rcm discover",
        )

    ls = lines("x-check")
    boxes = [
        (1, at(ls, "ok   server"), at(ls, "ok   server")),
        (2, at(ls, "ok   token"), at(ls, "ok   token")),
    ]
    boxes.append((3, at(ls, "ok   presets"), at(ls, "ok   pools")))
    if ls[1].startswith("server: found"):
        boxes = [(1, 1, 1), *[(n + 1, a, b) for n, a, b in boxes]]
    term("cli-check.png", ls, boxes, title="rcm check")

    ls = lines("x-run", wrap=96)
    i_snap, i_sub = at(ls, "snapshot:"), at(ls, "submitted job")
    i_run, i_done = at(ls, "#", after=i_sub + 1), at(ls, "$ echo")
    i_json = at(ls, "{")
    term(
        "cli-run.png",
        ls,
        [
            (1, i_snap, i_sub - 1),
            (2, i_sub, i_sub),
            (3, i_run, i_json - 2),
            (4, i_json - 1, i_json - 1),
            (5, i_json, i_done - 1),
            (6, i_done + 1, i_done + 1),
        ],
        title="rcm run demo -f speed=fast",
    )

    ls = lines("x-nowait", wrap=96)
    i_sub, i_jobs = at(ls, "submitted job"), at(ls, "$ rcm jobs")
    i_wait, i_rc = at(ls, "$ rcm wait"), at(ls, "$ echo")
    term(
        "cli-nowait.png",
        ls,
        [
            (1, i_sub, i_sub + 1),
            (2, i_jobs + 1, i_jobs + 2),
            (3, i_wait + 1, i_wait + 1),
            (4, i_rc + 1, i_rc + 1),
        ],
        title="rcm run --no-wait · rcm jobs · rcm wait",
    )

    ls = lines("x-fail", wrap=96)
    i_fail = at(ls, "#", after=at(ls, "submitted job") + 1)
    i_json, i_rc = at(ls, "{"), at(ls, "$ echo")
    term(
        "cli-fail.png",
        ls,
        [(1, i_json - 1, i_json - 1), (2, i_json, i_rc - 1), (3, i_rc + 1, i_rc + 1)],
        title="rcm run fail-demo",
    )
    assert i_fail < i_json

    ls = lines("x-join")
    i_second = at(ls, "$ rcm run demo --no-wait -f speed=fast   #")
    term(
        "cli-join.png",
        ls,
        [(1, 0, i_second - 1), (2, at(ls, "joined job"), at(ls, "joined job"))],
        title="같은 트리를 두 세션이 낸다",
    )

    ls = lines("x-top")
    i_head = at(ls, "━")
    i_q = at(ls, "queue —")
    i_recent, i_med = at(ls, "recent"), at(ls, "medians")
    i_host = at(ls, "host ")
    i_pool = at(ls, "queue —", after=i_host)
    term(
        "cli-top.png",
        ls,
        [
            (1, i_head, i_head),
            (2, i_q + 1, i_recent - 1),
            (3, i_recent, i_med),
            (4, i_host, i_pool - 1),
            (5, i_pool, len(ls) - 1),
        ],
        title="rcm top",
    )

    for k, v in sorted(sizes.items()):
        print(f"{k:24s} {v / 1024:7.1f} KB")
    too_big = {k: v for k, v in sizes.items() if v > 400 * 1024}
    assert not too_big, too_big
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
