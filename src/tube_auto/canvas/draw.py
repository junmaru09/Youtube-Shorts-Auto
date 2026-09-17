"""Drawing primitives in the whiteboard style.

Everything here takes a PIL ImageDraw (or Image) and draws with the outline
convention from `style.py`. Nothing here knows about the stage, slots, or
operations — that is `canvas.py`'s job. These are the pencils.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import style as S

Point = tuple[float, float]
RGB = tuple[int, int, int]


@lru_cache(maxsize=64)
def font(size: int, heavy: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(S.font_path(heavy), size)


@lru_cache(maxsize=32)
def symbol_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(S.FONT_FALLBACK), size)


# Three characters are not typeset at all but drawn as shapes, because that is
# what the reference does: "質量あり ➡ 重力 ◎" is text, a white arrow, text,
# and a blue ring. A font glyph would be thin and the wrong colour.
MARKS = {"➡": "arrow", "→": "arrow", "⇒": "arrow",
         "◎": "ring", "○": "ring", "◯": "ring",
         "×": "cross", "❌": "cross", "✕": "cross", "✖": "cross"}

# Codepoints M PLUS 2 does not carry and Noto does: arrows, geometric shapes,
# dingbats, and the fullwidth forms the reference uses (＋ ％).
_SYMBOL_RANGES = ((0x2190, 0x2BFF), (0x25A0, 0x25FF), (0x2700, 0x27BF), (0xFF00, 0xFF0F),
                  (0xFF1A, 0xFF20), (0xFF3B, 0xFF40), (0xFF5B, 0xFF65))


def _is_symbol(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _SYMBOL_RANGES)


def _runs(text) -> list[tuple[str, str]]:
    """Split text into (run, kind): kind is "text", "symbol", or a mark name."""
    runs: list[tuple[str, str]] = []
    for ch in str(text):          # `timeline start=1929` arrives as an int
        kind = MARKS.get(ch) or ("symbol" if _is_symbol(ch) else "text")
        if kind in MARKS.values():
            runs.append((ch, kind))
        elif runs and runs[-1][1] == kind:
            runs[-1] = (runs[-1][0] + ch, kind)
        else:
            runs.append((ch, kind))
    return runs


def _mark_width(kind: str, size: int) -> float:
    return {"arrow": size * 1.35, "ring": size * 1.05, "cross": size * 0.95}[kind]


def _draw_mark(draw: ImageDraw.ImageDraw, kind: str, left: float, cy: float, size: int,
               outline: RGB, ow: int) -> None:
    w = _mark_width(kind, size)
    if kind == "arrow":
        arrow(draw, (left + w * 0.06, cy), (left + w * 0.94, cy), S.WHITE,
              shaft=size * 0.32, head=size * 0.60, outline=outline, width=max(2, ow // 2))
    elif kind == "ring":
        r = size * 0.44
        cx = left + w / 2
        ring = max(6, round(size * 0.19))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=outline, width=ring + ow * 2)
        draw.ellipse((cx - r + ow, cy - r + ow, cx + r - ow, cy + r - ow), outline=S.MARK_RING, width=ring)
    elif kind == "cross":
        r = size * 0.32
        cx = left + w / 2
        for colour, lw in ((outline, size * 0.22 + ow * 2), (S.RED, size * 0.22)):
            draw.line((cx - r, cy - r, cx + r, cy + r), fill=colour, width=int(lw))
            draw.line((cx - r, cy + r, cx + r, cy - r), fill=colour, width=int(lw))


# --- text --------------------------------------------------------------------


def _run_widths(runs: list[tuple[str, str]], size: int, heavy: bool) -> list[float]:
    widths = []
    for run, kind in runs:
        if kind == "text":
            widths.append(font(size, heavy).getlength(run))
        elif kind == "symbol":
            widths.append(symbol_font(size).getlength(run))
        else:
            widths.append(_mark_width(kind, size))
    return widths


def text_size(text: str, size: int, heavy: bool = True) -> tuple[int, int]:
    runs = _runs(text)
    width = sum(_run_widths(runs, size, heavy))
    return round(width), round(size * 1.05)


def outlined_text(
    draw: ImageDraw.ImageDraw,
    xy: Point,
    text: str,
    size: int,
    fill: RGB = S.WHITE,
    *,
    anchor: str = "mm",
    outline: RGB = S.INK,
    width: int | None = None,
    heavy: bool = True,
) -> None:
    """Text with a solid outline, with symbol runs in Noto and marks drawn.

    PIL's stroke_width does the outline natively and far faster than the ring
    of offset draws the prototype used. Fonts are switched per run because
    M PLUS 2 has no ◎ → ↑ ％ and PIL does no fallback itself.
    """
    ow = width if width is not None else max(3, round(size * 0.11))
    text = str(text)
    runs = _runs(text)
    if len(runs) == 1 and runs[0][1] == "text":
        draw.text(xy, text, font=font(size, heavy), fill=fill, anchor=anchor,
                  stroke_width=ow, stroke_fill=outline)
        return

    # measure, then lay the runs out left to right from the anchored origin
    widths = _run_widths(runs, size, heavy)
    total = sum(widths)
    x, y = xy
    h_anchor, v_anchor = (anchor + "m")[:2]
    left = x - total / 2 if h_anchor == "m" else x - total if h_anchor == "r" else x
    # marks are centred on the visual middle of the text, whatever the anchor
    f = font(size, heavy)
    asc, desc = f.getmetrics()
    mid_y = {"m": y, "a": y + asc * 0.62, "s": y - asc * 0.42, "t": y + asc * 0.62, "d": y - asc * 0.42,
             "b": y - asc * 0.42}.get(v_anchor, y)
    for (run, kind), w in zip(runs, widths):
        if kind == "text":
            draw.text((left, y), run, font=f, fill=fill, anchor="l" + v_anchor,
                      stroke_width=ow, stroke_fill=outline)
        elif kind == "symbol":
            draw.text((left, y), run, font=symbol_font(size), fill=fill, anchor="l" + v_anchor,
                      stroke_width=ow, stroke_fill=outline)
        else:
            _draw_mark(draw, kind, left, mid_y, size, outline, ow)
        left += w


def outlined_lines(
    draw: ImageDraw.ImageDraw,
    centre: Point,
    lines: list[str],
    size: int,
    fill: RGB = S.WHITE,
    *,
    spacing: float = 1.25,
    **kw,
) -> None:
    """Several lines, centred as a block on `centre`."""
    step = size * spacing
    top = centre[1] - step * (len(lines) - 1) / 2
    for index, line in enumerate(lines):
        outlined_text(draw, (centre[0], top + index * step), line, size, fill, **kw)


# --- shapes -------------------------------------------------------------------


def filled_outlined(
    draw: ImageDraw.ImageDraw,
    shape: str,
    box: tuple[float, float, float, float],
    fill: RGB,
    *,
    outline: RGB = S.INK,
    width: int = S.OUTLINE_SHAPE,
    radius: int = S.BOX_RADIUS,
) -> None:
    """A rectangle, rounded rectangle, or ellipse with the standard outline."""
    x0, y0, x1, y1 = box
    if shape == "ellipse":
        draw.ellipse(box, fill=fill, outline=outline, width=width)
    elif shape == "rounded":
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    else:
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def polygon_outlined(
    draw: ImageDraw.ImageDraw, points: list[Point], fill: RGB,
    *, outline: RGB = S.INK, width: int = S.OUTLINE_SHAPE,
) -> None:
    draw.polygon(points, fill=fill, outline=outline, width=width)


# --- arrows -----------------------------------------------------------------------


def _arrow_polygon(p0: Point, p1: Point, shaft: float, head_len: float, head_w: float) -> list[Point]:
    """A single closed polygon for a straight arrow. One polygon means one
    clean outline, where a line-plus-triangle leaves a seam."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length          # unit along
    nx, ny = -uy, ux                           # unit normal
    head_len = min(head_len, length * 0.6)
    bx, by = x1 - ux * head_len, y1 - uy * head_len   # base of the head
    hs, hw = shaft / 2, head_w / 2
    return [
        (x0 + nx * hs, y0 + ny * hs),
        (bx + nx * hs, by + ny * hs),
        (bx + nx * hw, by + ny * hw),
        (x1, y1),
        (bx - nx * hw, by - ny * hw),
        (bx - nx * hs, by - ny * hs),
        (x0 - nx * hs, y0 - ny * hs),
    ]


def arrow(
    draw: ImageDraw.ImageDraw,
    p0: Point,
    p1: Point,
    fill: RGB = S.WHITE,
    *,
    shaft: float = S.ARROW_WIDTH,
    head: float = S.ARROW_HEAD,
    outline: RGB = S.INK,
    width: int = S.ARROW_OUTLINE,
) -> None:
    """A thick, outlined, straight arrow — the reference's workhorse.

    Two polygons, not one with an outline: PIL strokes a polygon's outline
    inwards, so a 12px shaft with a 7px stroke each side had no fill left and
    every arrow came out as a thin black line.
    """
    if width:
        (x0, y0), (x1, y1) = p0, p1
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        outer = _arrow_polygon((x0 - ux * width, y0 - uy * width), (x1 + ux * width * 1.4, y1 + uy * width * 1.4),
                               shaft + width * 2, head + width * 1.6, head * 0.95 + width * 2)
        draw.polygon(outer, fill=outline)
    draw.polygon(_arrow_polygon(p0, p1, shaft, head, head * 0.95), fill=fill)


def double_arrow(
    draw: ImageDraw.ImageDraw,
    p0: Point,
    p1: Point,
    fill: RGB = S.WHITE,
    *,
    shaft: float = S.ARROW_WIDTH,
    head: float = S.ARROW_HEAD,
    outline: RGB = S.INK,
    width: int = S.ARROW_OUTLINE,
) -> None:
    """Heads at both ends — "観測 4ナノ秒/24時間" between two heights."""
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    # two half arrows meeting in the middle, overlapping so no seam shows
    for colour, w in ((outline, width), (fill, 0)):
        for start, end in (((mx - ux * 8, my - uy * 8), p1), ((mx + ux * 8, my + uy * 8), p0)):
            if w:
                sx, sy = start
                ex, ey = end
                vx, vy = (ex - sx), (ey - sy)
                vl = math.hypot(vx, vy) or 1.0
                vx, vy = vx / vl, vy / vl
                outer = _arrow_polygon((sx - vx * w, sy - vy * w), (ex + vx * w * 1.4, ey + vy * w * 1.4),
                                       shaft + w * 2, head + w * 1.6, head * 0.95 + w * 2)
                draw.polygon(outer, fill=colour)
            else:
                draw.polygon(_arrow_polygon(start, end, shaft, head, head * 0.95), fill=colour)


def dashed_line(draw: ImageDraw.ImageDraw, p0: Point, p1: Point, fill: RGB = S.WHITE,
                *, width: int = 6, dash: float = 22.0, gap: float = 14.0) -> None:
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    t = 0.0
    while t < length:
        e = min(t + dash, length)
        draw.line((x0 + ux * t, y0 + uy * t, x0 + ux * e, y0 + uy * e), fill=fill, width=width)
        t += dash + gap


def polyline_arrow(
    draw: ImageDraw.ImageDraw,
    points: list[Point],
    fill: RGB = S.YELLOW,
    *,
    shaft: float = S.ARROW_WIDTH,
    head: float = S.ARROW_HEAD,
    outline: RGB = S.INK,
    width: int = S.ARROW_OUTLINE,
) -> None:
    """An arrow with corners — sunlight bouncing off ice is one of these.

    Outline pass first (thick lines with round joints, plus the head grown
    by the outline), then the fill pass on top, so the joints and the seam
    under the head never show.
    """
    if len(points) < 2:
        return
    if len(points) == 2:
        arrow(draw, points[0], points[1], fill, shaft=shaft, head=head, outline=outline, width=width)
        return
    (x0, y0), (x1, y1) = points[-2], points[-1]
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    head_len = min(head, length * 0.6)
    base = (x1 - ux * head_len, y1 - uy * head_len)
    body = points[:-1] + [base]
    head_poly = _arrow_polygon(points[-2], points[-1], shaft, head_len, head * 0.95)[2:5]
    grown = _arrow_polygon((x0, y0), (x1 + ux * width * 1.4, y1 + uy * width * 1.4),
                           shaft, head_len + width * 1.6, head * 0.95 + width * 2)[2:5]
    for colour, w, poly in ((outline, shaft + width * 2, grown), (fill, shaft, head_poly)):
        draw.line(body, fill=colour, width=int(w), joint="curve")
        r = w / 2
        for px, py in body[:-1]:
            draw.ellipse((px - r, py - r, px + r, py + r), fill=colour)
        draw.polygon(poly, fill=colour)


def curved_arrow(
    draw: ImageDraw.ImageDraw,
    p0: Point,
    p1: Point,
    bulge: float,
    fill: RGB = S.YELLOW,
    *,
    shaft: float = S.ARROW_WIDTH,
    head: float = S.ARROW_HEAD,
    outline: RGB = S.INK,
    width: int = S.ARROW_OUTLINE,
    steps: int = 24,
) -> None:
    """A quadratic-curve arrow. `bulge` is the control-point offset from the
    chord midpoint, along the normal; positive bends to the left of travel."""
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    cx, cy = mx + nx * bulge, my + ny * bulge
    pts = [
        ((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1,
         (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1)
        for t in (i / steps for i in range(steps + 1))
    ]
    # outline pass, then fill pass, along the polyline; then the head
    for colour, w in ((outline, shaft + width * 2), (fill, shaft)):
        draw.line(pts, fill=colour, width=int(w), joint="curve")
    tail = pts[-3]
    arrow(draw, tail, p1, fill, shaft=shaft, head=head, outline=outline, width=width)


# --- decorations ----------------------------------------------------------------


def strike(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
           colour: RGB = S.INK, width: int = 8) -> None:
    """A horizontal line through the middle of a box — the reference's
    reinterpretation mark."""
    x0, y0, x1, y1 = box
    y = (y0 + y1) / 2
    draw.line((x0 - 10, y, x1 + 10, y), fill=colour, width=width)


def highlight(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
              colour: RGB = S.YELLOW, width: int = S.HIGHLIGHT_WIDTH, pad: int = 22) -> None:
    """A yellow ellipse around a box — "look here"."""
    x0, y0, x1, y1 = box
    draw.ellipse((x0 - pad, y0 - pad, x1 + pad, y1 + pad), outline=colour, width=width)


def dim(image: Image.Image, alpha: int = S.DIM_ALPHA,
        region: tuple[int, int, int, int] | None = None) -> None:
    """Darken (part of) the image in place, for "title over the figure"."""
    region = region or (0, 0, image.width, image.height)
    overlay = Image.new("RGBA", (region[2] - region[0], region[3] - region[1]), (0, 0, 0, alpha))
    image.paste(overlay, region[:2], overlay)


# --- backgrounds ------------------------------------------------------------------


def parchment(width: int = S.WIDTH, height: int = S.HEIGHT, seed: int = 0) -> Image.Image:
    """Mottled warm paper. Neutral enough for any figure to sit on.

    Two noise layers at different scales — fine grain and broad mottling —
    plus a vignette. Sampled against the reference: mid-tan base, darker
    corners, visible blotching rather than a flat wash.
    """
    base = Image.new("RGB", (width, height), S.PARCHMENT_BASE)

    # broad mottling: low-res noise, blurred and upscaled
    small = Image.effect_noise((width // 24, height // 24), 64 + seed % 7).convert("L")
    mottle = small.resize((width, height), Image.BICUBIC).filter(ImageFilter.GaussianBlur(28))
    light = Image.new("RGB", (width, height), S.PARCHMENT_LIGHT)
    dark = Image.new("RGB", (width, height), S.PARCHMENT_DARK)
    paper = Image.composite(light, dark, mottle.point(lambda v: 60 + v * 0.55))
    paper = Image.blend(base, paper, 0.75)

    # fine grain
    grain = Image.effect_noise((width, height), 14).convert("L")
    paper = Image.blend(paper, Image.merge("RGB", (grain, grain, grain)).point(lambda v: 150 + v // 3), 0.12)

    # vignette
    vig = Image.new("L", (width, height), 0)
    ImageDraw.Draw(vig).ellipse((-width * 0.25, -height * 0.45, width * 1.25, height * 1.45), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(220))
    edge = Image.new("RGB", (width, height), S.PARCHMENT_DARK)
    return Image.composite(paper, edge, vig)


def starfield(width: int = S.WIDTH, height: int = S.HEIGHT, seed: int = 0, stars: int = 900) -> Image.Image:
    """A dark sky with faint stars: the stand-in for a space photo until the
    asset library has one. Dark enough for white grid lines to read."""
    import random

    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), (8, 10, 22))
    d = ImageDraw.Draw(img)
    # a soft band of milky-way haze across the middle
    haze = Image.new("L", (width, height), 0)
    ImageDraw.Draw(haze).ellipse((-width * 0.2, height * 0.25, width * 1.2, height * 0.85), fill=70)
    haze = haze.filter(ImageFilter.GaussianBlur(160))
    img = Image.composite(Image.new("RGB", (width, height), (40, 44, 80)), img, haze)
    d = ImageDraw.Draw(img)
    for _ in range(stars):
        x, y = rng.randrange(width), rng.randrange(height)
        r = rng.choice((0.6, 0.8, 1.0, 1.0, 1.4, 2.0))
        v = rng.randint(120, 255)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(v, v, min(255, v + 12)))
    return img


def dimmed_photo(photo: Image.Image, width: int = S.WIDTH, height: int = S.HEIGHT,
                 factor: float = S.PHOTO_DIM) -> Image.Image:
    """A photo, filled to the frame and darkened so figures read on top."""
    im = photo.convert("RGB")
    scale = max(width / im.width, height / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left, top = (im.width - width) // 2, (im.height - height) // 2
    im = im.crop((left, top, left + width, top + height))
    return im.point(lambda v: int(v * factor))
