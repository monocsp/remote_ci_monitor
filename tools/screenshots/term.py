"""터미널 출력 → PNG. 어두운 배경 · Menlo 13px · 이모지는 Apple Color Emoji 로 그린다."""

from __future__ import annotations

import re
from pathlib import Path

from annotate import draw_boxes, save_png
from PIL import Image, ImageDraw, ImageFont

MENLO = "/System/Library/Fonts/Menlo.ttc"
EMOJI = "/System/Library/Fonts/Apple Color Emoji.ttc"
SIZE = 13
LINE = 19
PAD_X, PAD_Y = 18, 16
BG = (24, 27, 31)
FG = (230, 237, 243)
DIM = (139, 148, 158)
PROMPT = (126, 231, 135)
JSON_C = (210, 168, 255)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
EMOJI_CHARS = {"✅", "❌", "⚠️", "⏳", "🔑", "⚠"}


def _font(size: int = SIZE) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(MENLO, size)


def _emoji_font() -> ImageFont.FreeTypeFont | None:
    for sz in (32, 40, 48, 64, 96, 160, 20, 26):
        try:
            return ImageFont.truetype(EMOJI, sz)
        except OSError:
            continue
    return None


def render(
    lines: list[str],
    dst: Path,
    *,
    boxes: list[tuple[int, int, int]] | None = None,
    width_chars: int | None = None,
    title: str | None = None,
) -> int:
    """lines 를 그린다. boxes: [(번호, 시작 줄, 끝 줄)] (0 기반, 끝 포함) → 그 줄 범위를 상자로."""
    lines = [ANSI.sub("", ln).rstrip("\n") for ln in lines]
    font = _font()
    adv = font.getlength("M")
    ncols = width_chars or max(len(ln) for ln in lines)
    ncols = max(ncols, 40)
    W = int(PAD_X * 2 + adv * ncols)
    top = PAD_Y + (28 if title else 0)
    H = int(top + LINE * len(lines) + PAD_Y)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if title:
        # 창 제목 줄 — 점 셋 + 제목
        for i, c in enumerate(((255, 95, 87), (255, 189, 46), (39, 201, 63))):
            d.ellipse((PAD_X + i * 18, PAD_Y - 4, PAD_X + 12 + i * 18, PAD_Y + 8), fill=c)
        d.text((PAD_X + 64, PAD_Y - 5), title, font=font, fill=DIM)
    efont = _emoji_font()
    for i, ln in enumerate(lines):
        y = top + i * LINE
        x = PAD_X
        color = FG
        if ln.startswith("$ "):
            color = PROMPT
        elif ln.startswith("{") and ln.rstrip().endswith("}"):
            color = JSON_C
        for ch in ln:
            if ch in EMOJI_CHARS and efont is not None:
                bb = efont.getbbox(ch)
                sz = (bb[2] - bb[0], bb[3] - bb[1])
                tile = Image.new("RGBA", (sz[0] + 4, sz[1] + 4), (0, 0, 0, 0))
                ImageDraw.Draw(tile).text(
                    (2 - bb[0], 2 - bb[1]), ch, font=efont, embedded_color=True
                )
                target = int(LINE - 4)
                tile = tile.resize((target, target), Image.Resampling.LANCZOS)
                img.paste(tile, (int(x), int(y + 1)), tile)
                x += adv * 2  # 이모지는 두 칸
            else:
                d.text((x, y), ch, font=font, fill=color)
                x += adv
    out = img
    if boxes:
        # 테두리는 줄과 줄 사이 빈 틈에 놓는다 — pad 를 주면 옆 줄 글자의
        # 윗획을 덮어 7 이 / 처럼 보인다.
        px = [
            (n, (PAD_X - 2, top + a * LINE + 1, adv * ncols + 4, (b - a + 1) * LINE - 3))
            for n, a, b in boxes
        ]
        out = draw_boxes(img, px, pad=0, width=2, badge_at="tr")
    return save_png(out, dst)
