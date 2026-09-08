"""스크린샷 주석 — 빨간 둥근 사각형 + 번호 원. README.ko.md 의 ①②③ 과 짝을 이룬다."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED = (220, 38, 38, 255)
WHITE = (255, 255, 255, 255)
MAX_BYTES = 400 * 1024


def _bold(size: int) -> ImageFont.FreeTypeFont:
    for idx in (1, 0):
        try:
            f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size, index=idx)
            if "Bold" in f.getname()[1] or idx == 0:
                return f
        except OSError:
            continue
    return ImageFont.load_default()


def draw_boxes(
    img: Image.Image,
    boxes: list,
    *,
    pad: int = 4,
    width: int = 3,
    radius: int = 7,
    badge: int = 26,
    badge_at: str = "tl",
) -> Image.Image:
    """빨간 사각형 + 번호 원을 그린다.

    boxes: `[(번호, (x, y, w, h)[, 위치]), …]` — 이미지 픽셀 좌표.
    위치: `tl`(기본) · `tr` · `l`(왼쪽 바깥) · `t`(위 바깥).
    """
    img = img.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _bold(15)
    boxes = [(b[0], b[1], b[2] if len(b) > 2 else badge_at) for b in boxes]
    for _n, (x, y, w, h), _pos in boxes:
        x0, y0, x1, y1 = x - pad, y - pad, x + w + pad, y + h + pad
        x0, y0 = max(1, x0), max(1, y0)
        x1, y1 = min(img.width - 2, x1), min(img.height - 2, y1)
        d.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=RED, width=width)
    # 번호 원은 사각형 위에 그린다(겹쳐도 번호가 보이게)
    for n, (x, y, w, _h), pos in boxes:
        x0, y0 = max(1, x - pad), max(1, y - pad)
        x1 = min(img.width - 2, x + w + pad)
        r = badge / 2
        if pos == "tr":
            cx, cy = x1, y0
        elif pos == "l":
            cx, cy = x0 - r + 2, y0 + r - 4
        elif pos == "t":
            cx, cy = x0 + r - 2, y0 - r + 2
        else:
            cx, cy = x0, y0
        cx = min(max(cx, r + 1), img.width - r - 1)
        cy = min(max(cy, r + 1), img.height - r - 1)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED, outline=WHITE, width=2)
        t = str(n)
        bb = font.getbbox(t)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        d.text((cx - tw / 2 - bb[0], cy - th / 2 - bb[1]), t, font=font, fill=WHITE)
    return Image.alpha_composite(img, layer)


def save_png(img: Image.Image, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    out = img.convert("RGB")
    out.save(dst, "PNG", optimize=True)
    size = dst.stat().st_size
    if size > MAX_BYTES:
        out.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(dst, "PNG", optimize=True)
        size = dst.stat().st_size
    return size


def annotate_file(
    src: Path, dst: Path, boxes, *, crop: tuple[int, int, int, int] | None = None, **kw
) -> int:
    img = Image.open(src)
    if crop:
        img = img.crop(crop)
        boxes = [
            (b[0], (b[1][0] - crop[0], b[1][1] - crop[1], b[1][2], b[1][3]), *b[2:]) for b in boxes
        ]
    return save_png(draw_boxes(img, boxes, **kw), dst)
