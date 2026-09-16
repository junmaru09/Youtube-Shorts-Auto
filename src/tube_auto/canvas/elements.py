"""Figure elements, drawn in code.

Each function draws one element into a box and returns the boxes of its
named parts, so later operations can attach to them ("label near the 27%
slice", "arrow from the sun"). The parts dict always contains "self".

These are the elements the corpus showed being drawn rather than pasted:
the celestial bodies, the ice blocks, and every structural figure — pie, box
row, timeline, table, chain, list, number line, grid panel. Illustrated
objects (a magnet, a person, a house) come from the asset library instead.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import ImageDraw

from . import style as S
from .draw import (
    RGB, Point, arrow, filled_outlined, font, outlined_lines, outlined_text,
    polygon_outlined, text_size,
)

Box = tuple[float, float, float, float]
Parts = dict[str, Box]


def _centre(box: Box) -> Point:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _fit(box: Box, aspect: float) -> Box:
    """The largest box of the given aspect (w/h) centred in `box`."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w / h > aspect:
        w = h * aspect
    else:
        h = w / aspect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


# --- celestial ---------------------------------------------------------------------


def sun(d: ImageDraw.ImageDraw, box: Box, **_: Any) -> Parts:
    """A spiky sun: outlined star polygon, two-tone fill."""
    box = _fit(box, 1.0)
    cx, cy = _centre(box)
    r = (box[2] - box[0]) / 2
    pts = []
    for i in range(24):
        a = i * math.pi / 12 - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.70
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    polygon_outlined(d, pts, S.ORANGE)
    d.ellipse((cx - r * 0.58, cy - r * 0.58, cx + r * 0.58, cy + r * 0.58), fill=(255, 205, 70))
    return {"self": box, "centre": (cx - 4, cy - 4, cx + 4, cy + 4)}


def earth_globe(d: ImageDraw.ImageDraw, box: Box, ice: bool = False, **_: Any) -> Parts:
    """A round earth: blue sea, green continents, optional ice cap."""
    box = _fit(box, 1.0)
    cx, cy = _centre(box)
    r = (box[2] - box[0]) / 2
    filled_outlined(d, "ellipse", box, S.BLUE)
    # continents: a few blobs, clipped by drawing them inside a smaller radius
    for ox, oy, rx, ry in ((-0.30, -0.25, 0.32, 0.22), (0.15, 0.05, 0.36, 0.30), (-0.20, 0.42, 0.24, 0.16)):
        d.ellipse((cx + (ox - rx) * r, cy + (oy - ry) * r, cx + (ox + rx) * r, cy + (oy + ry) * r), fill=S.GREEN)
    if ice:
        d.ellipse((cx - r * 0.55, cy - r * 0.95, cx + r * 0.55, cy - r * 0.35), fill=(235, 248, 255))
    # redraw the outline so continents do not spill over it
    d.ellipse(box, outline=S.INK, width=S.OUTLINE_SHAPE)
    return {"self": box, "top": (cx - 8, box[1], cx + 8, box[1] + 16),
            "north": (cx - 8, box[1] - 8, cx + 8, box[1] + 8), "south": (cx - 8, box[3] - 8, cx + 8, box[3] + 8)}



def earth_arc(d: ImageDraw.ImageDraw, box: Box, colour: str = "green", **_: Any) -> Parts:
    """The planet as a wide, nearly flat arc rising from the bottom of the
    box — the reference's stage floor for anything "on the ground".

    Measured: the apex sits at y=750 and the rim drops ~90px by the frame
    edge, a radius of ~4700px. The "surface" part encodes that curve as
    (x0, apex_y, x1, edge_y) so `add` can rest things on it at any x.
    """
    x0, y0, x1, y1 = box
    w = x1 - x0
    r = w * 2.6
    cx, cy = (x0 + x1) / 2, y0 + r
    fill = S.PALETTE.get(colour, S.GREEN)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=S.INK, width=S.OUTLINE_SHAPE)
    if colour == "green":
        # seas along the visible rim, flattened ellipses
        for fx, fy, fw in ((0.16, 0.30, 0.13), (0.50, 0.24, 0.16), (0.80, 0.34, 0.10)):
            bx, bw = x0 + fx * w, fw * w
            by = y0 + fy * (y1 - y0) + ((bx + bw / 2 - cx) / (w / 2)) ** 2 * 60
            d.ellipse((bx, by, bx + bw, by + bw * 0.34), fill=S.BLUE)
    edge_y = cy - math.sqrt(max(r * r - (w / 2) ** 2, 0))
    return {"self": box, "surface": (x0, y0, x1, edge_y)}


def moon(d: ImageDraw.ImageDraw, box: Box, **_: Any) -> Parts:
    box = _fit(box, 1.0)
    filled_outlined(d, "ellipse", box, (255, 225, 110))
    return {"self": box}


def star_dots(d: ImageDraw.ImageDraw, box: Box, count: int = 14, seed: int = 3, **_: Any) -> Parts:
    """Yellow dots scattered over a region — "たくさんの恒星" on a galaxy photo."""
    import random
    rng = random.Random(seed)
    x0, y0, x1, y1 = box
    for _ in range(count):
        x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
        d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=S.YELLOW, outline=S.INK, width=2)
    return {"self": box}


def galaxy(d: ImageDraw.ImageDraw, box: Box, **_: Any) -> Parts:
    """A flat spiral galaxy: blue-white disc, two arms, bright core."""
    box = _fit(box, 1.6)
    cx, cy = _centre(box)
    rx, ry = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    d.ellipse(box, fill=(60, 90, 200), outline=S.INK, width=S.OUTLINE_SHAPE)
    for phase in (0.0, math.pi):
        pts = [(cx + rx * (0.15 + 0.85 * t) * math.cos(phase + t * 2.6),
                cy + ry * (0.15 + 0.85 * t) * math.sin(phase + t * 2.6)) for t in (i / 30 for i in range(31))]
        d.line(pts, fill=(170, 200, 255), width=int(ry * 0.22), joint="curve")
    d.ellipse((cx - rx * 0.28, cy - ry * 0.40, cx + rx * 0.28, cy + ry * 0.40), fill=(240, 245, 255))
    return {"self": box, "centre": (cx - 6, cy - 6, cx + 6, cy + 6)}


def black_blob(d: ImageDraw.ImageDraw, box: Box, **_: Any) -> Parts:
    """Dark matter as the reference drew it: a soft black blob, no outline."""
    box = _fit(box, 1.0)
    cx, cy = _centre(box)
    r = (box[2] - box[0]) / 2
    for i in range(6, 0, -1):
        rr = r * i / 6
        shade = int(10 + (6 - i) * 6)
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=(shade, shade, shade + 4))
    return {"self": box}


# --- physical -------------------------------------------------------------------------



def ice_block(d: ImageDraw.ImageDraw, box: Box, **_: Any) -> Parts:
    """An iceberg: a squat faceted polygon, pale blue with a lighter face and
    a white cap, rather than a rounded square — closer to the illustration
    the reference pastes."""
    box = _fit(box, 1.15)
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    pts = [(x0 + w * fx, y0 + h * fy) for fx, fy in
           ((0.06, 0.30), (0.20, 0.06), (0.45, 0.12), (0.62, 0.0), (0.88, 0.10), (1.0, 0.36),
            (0.96, 1.0), (0.04, 1.0))]
    polygon_outlined(d, pts, (196, 226, 246))
    # lighter front face and a white cap
    d.polygon([(x0 + w * 0.06, y0 + h * 0.30), (x0 + w * 0.45, y0 + h * 0.12), (x0 + w * 0.62, y0),
               (x0 + w * 0.88, y0 + h * 0.10), (x0 + w * 0.84, y0 + h * 0.42), (x0 + w * 0.12, y0 + h * 0.46)],
              fill=(232, 244, 252))
    d.polygon([(x0 + w * 0.36, y0 + h * 0.46), (x0 + w * 0.50, y0 + h * 0.98), (x0 + w * 0.26, y0 + h * 0.98)],
              fill=(214, 236, 250))
    return {"self": box, "top": ((x0 + x1) / 2 - 8, y0 - 8, (x0 + x1) / 2 + 8, y0 + 8)}


def cloud(d: ImageDraw.ImageDraw, box: Box, **_: Any) -> Parts:
    box = _fit(box, 1.7)
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lobes = [(0.28, 0.62, 0.30), (0.50, 0.42, 0.36), (0.72, 0.60, 0.30), (0.50, 0.72, 0.42)]
    for pass_fill, pad in ((S.INK, S.OUTLINE_SHAPE), (S.WHITE, 0)):
        for fx, fy, fr in lobes:
            r = fr * h / 2 + pad
            d.ellipse((x0 + fx * w - r, y0 + fy * h - r, x0 + fx * w + r, y0 + fy * h + r), fill=pass_fill)
    return {"self": box, "bottom": ((x0 + x1) / 2 - 8, y1 - 8, (x0 + x1) / 2 + 8, y1 + 8)}


def atom(d: ImageDraw.ImageDraw, box: Box, electrons: int = 2, **_: Any) -> Parts:
    """Nucleus with an orbit ring and electrons — the reference's atom."""
    box = _fit(box, 1.0)
    cx, cy = _centre(box)
    r = (box[2] - box[0]) / 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=S.INK, width=S.OUTLINE_SHAPE - 2)
    filled_outlined(d, "ellipse", (cx - r * 0.28, cy - r * 0.28, cx + r * 0.28, cy + r * 0.28), S.PINK)
    outlined_text(d, (cx, cy), "+", int(r * 0.42), S.WHITE, width=3)
    parts: Parts = {"self": box, "nucleus": (cx - r * 0.28, cy - r * 0.28, cx + r * 0.28, cy + r * 0.28)}
    for i in range(electrons):
        a = -math.pi / 2 + i * 2 * math.pi / electrons
        ex, ey = cx + r * math.cos(a), cy + r * math.sin(a)
        er = r * 0.16
        filled_outlined(d, "ellipse", (ex - er, ey - er, ex + er, ey + er), S.BLUE, width=4)
        outlined_text(d, (ex, ey), "−", int(er * 1.4), S.WHITE, width=2)
        parts[f"electron{i + 1}"] = (ex - er, ey - er, ex + er, ey + er)
    return parts


def charge(d: ImageDraw.ImageDraw, box: Box, sign: str = "+", **_: Any) -> Parts:
    box = _fit(box, 1.0)
    cx, cy = _centre(box)
    r = (box[2] - box[0]) / 2
    filled_outlined(d, "ellipse", box, S.PINK if sign == "+" else S.BLUE, width=5)
    outlined_text(d, (cx, cy), "+" if sign == "+" else "−", int(r * 1.3), S.WHITE, width=3)
    return {"self": box}


# --- structural ---------------------------------------------------------------------



def pie(d: ImageDraw.ImageDraw, box: Box, slices: list[dict[str, Any]], **_: Any) -> Parts:
    """A pie chart. Each slice: {"label": str, "value": float, "colour": str}.

    Percentages go inside the slice in white; the slice name goes outside in
    the slice's colour, with an optional white description beneath — exactly
    the reference's three-tier labelling.
    """
    box = _fit(box, 1.0)
    cx, cy = _centre(box)
    r = (box[2] - box[0]) / 2 * 0.70      # leave room for the outside labels
    inner = (cx - r, cy - r, cx + r, cy + r)
    total = sum(s["value"] for s in slices) or 1.0
    start = -90.0
    parts: Parts = {"self": box}

    for s in slices:
        sweep = 360.0 * s["value"] / total
        colour = S.PALETTE.get(s.get("colour", "white"), S.WHITE)
        d.pieslice(inner, start, start + sweep, fill=colour, outline=S.INK, width=S.OUTLINE_SHAPE)
        mid = math.radians(start + sweep / 2)
        cos, sin = math.cos(mid), math.sin(mid)
        # percentage inside: big slices near the middle, slivers near the rim
        pct = f"{round(100 * s['value'] / total)}%"
        if sweep >= 40:
            outlined_text(d, (cx + r * 0.58 * cos, cy + r * 0.58 * sin), pct, S.SIZE_LABEL_SMALL, S.WHITE)
        elif sweep >= 8:
            outlined_text(d, (cx + r * 0.76 * cos, cy + r * 0.76 * sin), pct, 30, S.WHITE)
        # name outside: put the label box just past the rim, pushed away
        # along the radial so its near edge (or corner) touches the gap point
        tw, th = text_size(s["label"], S.SIZE_LABEL)
        gap = 26
        px, py = cx + (r + gap) * cos, cy + (r + gap) * sin
        lx, ly = px + tw / 2 * cos, py + th / 2 * sin
        outlined_text(d, (lx, ly), s["label"], S.SIZE_LABEL, colour)
        parts[s["label"]] = (lx - tw / 2, ly - th / 2, lx + tw / 2, ly + th / 2)
        if s.get("note"):
            lines = s["note"].split("\n")
            step = S.SIZE_NOTE * 1.2
            top = ly + th / 2 + step / 2 + 4
            for i, line in enumerate(lines):
                outlined_text(d, (lx, top + i * step), line, S.SIZE_NOTE, S.WHITE, heavy=False)
        start += sweep

    if _.get("centre_label"):
        outlined_text(d, (cx, cy), _["centre_label"], S.SIZE_LABEL_SMALL, S.WHITE)
    return parts



def box_row(d: ImageDraw.ImageDraw, box: Box, items: list[str], colour: str | None = None,
            **_: Any) -> Parts:
    """Boxed words in a row — "この世界に存在する力: 重力 電磁気力 …".

    As in the reference: no fill, a white outline, white text, generous
    padding, and near-uniform widths (measured 315-345px at 1080p).
    """
    x0, y0, x1, y1 = box
    n = len(items)
    gap = 90
    size = S.SIZE_TABLE
    parts: Parts = {"self": box}
    widths = [max(text_size(t, size)[0] + 150, 300) for t in items]
    total = sum(widths) + gap * (n - 1)
    if total > x1 - x0:                      # too many words: shrink evenly
        scale = (x1 - x0) / total
        widths = [w * scale for w in widths]
        gap *= scale
        total = x1 - x0
    x = (x0 + x1) / 2 - total / 2
    h = size * 2
    cy = (y0 + y1) / 2
    for text, w in zip(items, widths):
        b = (x, cy - h / 2, x + w, cy + h / 2)
        if colour:
            filled_outlined(d, "rect", b, S.PALETTE.get(colour, S.WHITE), width=6)
            outlined_text(d, (x + w / 2, cy), text, size, S.INK, outline=S.PALETTE.get(colour, S.WHITE), width=0)
        else:
            d.rectangle(b, outline=S.INK, width=9)
            d.rectangle((b[0] + 2, b[1] + 2, b[2] - 2, b[3] - 2), outline=S.WHITE, width=5)
            outlined_text(d, (x + w / 2, cy), text, size, S.WHITE)
        parts[text] = b
        x += w + gap
    return parts



def timeline(d: ImageDraw.ImageDraw, box: Box, start: str, end: str,
             bands: list[dict[str, Any]] | None = None, ticks: int = 10, **_: Any) -> Parts:
    """A left-to-right black arrow through a tall coloured band, with tick
    marks, a start bar, and the end labels underneath (氷期 / 間氷期).

    Bands are {"from", "to", "colour", "name"} in 0..1 of the axis and are
    drawn in order, so a full-width band with narrow strips over it is fine.
    """
    x0, y0, x1, y1 = box
    lx, rx = x0 + 90, x1 - 90
    top, bottom = y0 + 70, y1 - 90
    y = (top + bottom) / 2
    parts: Parts = {"self": box}
    for b in bands or []:
        bx0 = lx + (rx - lx) * b["from"]
        bx1 = lx + (rx - lx) * b["to"]
        colour = S.PALETTE.get(b.get("colour", "cyan"), S.CYAN)
        d.rectangle((bx0, top, bx1, bottom), fill=colour)
        parts[b.get("name", f"band{len(parts)}")] = (bx0, top, bx1, bottom)
    if bands:
        d.rectangle((lx, top, rx, bottom), outline=S.INK, width=4)
    arrow(d, (lx, y), (rx + 50, y), S.INK, shaft=14, head=54, width=0)
    for i in range(ticks + 1):
        tx = lx + (rx - lx) * i / ticks
        d.line((tx, y - 22, tx, y + 22), fill=S.INK, width=7)
    d.line((lx, top - 24, lx, bottom + 24), fill=S.INK, width=12)    # the start bar
    outlined_text(d, (lx - 20, bottom + 50), start, S.SIZE_TABLE, S.WHITE, anchor="lm")
    outlined_text(d, (rx + 20, bottom + 50), end, S.SIZE_TABLE, S.WHITE, anchor="rm")
    parts["axis"] = (lx, y - 8, rx, y + 8)
    parts["band"] = (lx, top, rx, bottom)
    return parts



def number_line(d: ImageDraw.ImageDraw, box: Box, low: str = "小", high: str = "大",
                markers: list[dict[str, Any]] | None = None, vertical: bool = False,
                span: list[float] | None = None, span_label: str = "", ticks: int = 24,
                **_: Any) -> Parts:
    """A scale with 小/大 ends, dense tick marks, pointer-labelled markers and
    an optional bracketed span — the multiverse video's energy-density scale.

    Markers: {"at": 0..1, "label", "colour", "side"}. Horizontal lines label
    above with a down-pointer; vertical ones label to the right with a left
    pointer, alternating sides when asked. Parts: each marker's label box,
    "line", and "span" when given.
    """
    x0, y0, x1, y1 = box
    parts: Parts = {"self": box}
    small = S.SIZE_LABEL_SMALL
    if vertical:
        x = (x0 + x1) / 2
        top, bottom = y0 + 60, y1 - 60
        d.line((x, bottom, x, top), fill=S.INK, width=9)
        for i in range(ticks + 1):
            ty = bottom - (bottom - top) * i / ticks
            d.line((x - 14, ty, x + 14, ty), fill=S.INK, width=4)
        outlined_text(d, (x, y0 + 22), high, small, S.WHITE)
        outlined_text(d, (x, y1 - 22), low, small, S.WHITE)
        parts["line"] = (x - 4, top, x + 4, bottom)
        for m in markers or []:
            my = bottom - (bottom - top) * m["at"]
            colour = S.PALETTE.get(m.get("colour", "white"), S.WHITE)
            left = m.get("side") == "left"
            sign = -1 if left else 1
            arrow(d, (x + sign * 78, my), (x + sign * 22, my), colour, shaft=10, head=30)
            tw, th = text_size(m["label"], small)
            lx = x + sign * 96
            outlined_text(d, (lx, my), m["label"], small, colour, anchor="rm" if left else "lm")
            parts[m["label"]] = (lx - tw, my - th / 2, lx, my + th / 2) if left else (lx, my - th / 2, lx + tw, my + th / 2)
        if span:
            sy0, sy1 = (bottom - (bottom - top) * s for s in span)
            bx = x - 56
            d.line((bx, sy0, bx, sy1), fill=S.WHITE, width=6)
            d.line((bx - 14, sy0, bx + 14, sy0), fill=S.WHITE, width=6)
            d.line((bx - 14, sy1, bx + 14, sy1), fill=S.WHITE, width=6)
            parts["span"] = (bx - 14, min(sy0, sy1), bx + 14, max(sy0, sy1))
    else:
        y = (y0 + y1) / 2
        left, right = x0 + 70, x1 - 70
        d.line((left, y, right, y), fill=S.INK, width=9)
        for i in range(ticks + 1):
            tx = left + (right - left) * i / ticks
            d.line((tx, y - 14, tx, y + 14), fill=S.INK, width=4)
        outlined_text(d, (left - 30, y), low, small, S.WHITE, anchor="rm")
        outlined_text(d, (right + 30, y), high, small, S.WHITE, anchor="lm")
        parts["line"] = (left, y - 4, right, y + 4)
        for m in markers or []:
            mx = left + (right - left) * m["at"]
            colour = S.PALETTE.get(m.get("colour", "white"), S.WHITE)
            below = m.get("side") == "below"
            sign = 1 if below else -1
            arrow(d, (mx, y + sign * 84), (mx, y + sign * 24), colour, shaft=10, head=30)
            tw, th = text_size(m["label"], small)
            ly = y + sign * 118
            outlined_text(d, (mx, ly), m["label"], small, colour)
            parts[m["label"]] = (mx - tw / 2, ly - th / 2, mx + tw / 2, ly + th / 2)
        if span:
            sx0, sx1 = (left + (right - left) * s for s in span)
            by = y + 56
            d.line((sx0, by, sx1, by), fill=S.WHITE, width=6)
            d.line((sx0, by - 14, sx0, by + 14), fill=S.WHITE, width=6)
            d.line((sx1, by - 14, sx1, by + 14), fill=S.WHITE, width=6)
            parts["span"] = (min(sx0, sx1), by - 14, max(sx0, sx1), by + 14)
            if span_label:
                outlined_text(d, ((sx0 + sx1) / 2, by + 44), span_label, small, S.WHITE)
    return parts


def table(d: ImageDraw.ImageDraw, box: Box, columns: list[str], rows: list[list[str]],
          highlight: str | None = None, **_: Any) -> Parts:
    """Property table: no rules, left-aligned columns, one line per row.

    Measured off the reference: headers 54px, rows 48px at a 135px pitch,
    columns starting at x=120 and x=990. ➡ ◎ × in a cell are drawn as marks
    by `outlined_text`. `highlight` names one cell text to show in yellow —
    the reference's "the row being talked about right now".
    """
    x0, y0, x1, y1 = box
    ncol = max(len(columns), 1)
    col_w = (x1 - x0) / ncol
    parts: Parts = {"self": box}
    for ci, name in enumerate(columns):
        outlined_text(d, (x0 + col_w * ci + 30, y0 + 40), name, S.SIZE_HEADING - 2, S.WHITE, anchor="lm")
    for ri, row in enumerate(rows):
        y = y0 + 40 + S.TABLE_ROW * (ri + 1)
        for ci, cell in enumerate(row):
            if not cell:
                continue
            colour = S.YELLOW if highlight and cell == highlight else S.WHITE
            outlined_text(d, (x0 + col_w * ci + 30, y), cell, S.SIZE_TABLE, colour, anchor="lm")
        parts[f"row{ri + 1}"] = (x0, y - S.TABLE_ROW / 2, x1, y + S.TABLE_ROW / 2)
    return parts


def chain(d: ImageDraw.ImageDraw, box: Box, nodes: list[dict[str, Any]],
          edges: list[tuple[int, int]] | None = None, **_: Any) -> Parts:
    """Text nodes joined by arrows — the reference's logic diagram.

    Nodes: {"text", "x", "y", "colour"} with x/y in 0..1 of the box. Edges are
    index pairs. Nothing is auto-laid-out: the script says where things go.
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    parts: Parts = {"self": box}
    boxes: list[Box] = []
    for n in nodes:
        cx, cy = x0 + n["x"] * w, y0 + n["y"] * h
        size = n.get("size", S.SIZE_LABEL_SMALL)
        lines = n["text"].split("\n")
        tw = max(text_size(line, size)[0] for line in lines)
        th = size * 1.25 * len(lines)
        b = (cx - tw / 2 - 14, cy - th / 2 - 8, cx + tw / 2 + 14, cy + th / 2 + 8)
        if n.get("boxed"):
            filled_outlined(d, "rounded", b, S.WHITE, radius=8, width=5)
            outlined_lines(d, (cx, cy), lines, size, S.INK, width=0)
        else:
            outlined_lines(d, (cx, cy), lines, size, S.PALETTE.get(n.get("colour", "white"), S.WHITE))
        boxes.append(b)
        parts[n["text"].split("\n")[0]] = b
    for a, b in edges or []:
        pa, pb = _centre(boxes[a]), _centre(boxes[b])
        # start/end at the box edges rather than the centres
        ax0, ay0, ax1, ay1 = boxes[a]
        bx0, by0, bx1, by1 = boxes[b]
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        if abs(dx) > abs(dy):
            start = (ax1 + 10, pa[1]) if dx > 0 else (ax0 - 10, pa[1])
            end = (bx0 - 10, pb[1]) if dx > 0 else (bx1 + 10, pb[1])
        else:
            start = (pa[0], ay1 + 10) if dy > 0 else (pa[0], ay0 - 10)
            end = (pb[0], by0 - 10) if dy > 0 else (pb[0], by1 + 10)
        arrow(d, start, end, S.WHITE)
    return parts


def numbered_list(d: ImageDraw.ImageDraw, items: list[str], x: float = S.LIST_X,
                  y: float = S.LIST_Y, **_: Any) -> Parts:
    parts: Parts = {}
    for i, text in enumerate(items):
        line = f"{i + 1}. {text}"
        outlined_text(d, (x, y + i * S.LIST_LINE), line, S.SIZE_LIST, S.WHITE, anchor="la", heavy=False)
        tw, th = text_size(line, S.SIZE_LIST)
        parts[text] = (x, y + i * S.LIST_LINE, x + tw, y + i * S.LIST_LINE + th)
    parts["self"] = (x, y, x + 480, y + len(items) * S.LIST_LINE)
    return parts



def grid_panel(d: ImageDraw.ImageDraw, box: Box, x_label: str = "", y_label: str = "",
               warp: float = 0.0, cols: int = 12, rows: int = 10, **_: Any) -> Parts:
    """A spacetime diagram as the reference draws it: white grid lines
    straight on the (dark) background, white arrow axes, "高さ（空間）"
    stacked up the left and "時間" under the bottom.

    `warp` (0..1) bends the grid into a fan — the curved-spacetime version
    — by rotating each column about a pivot far below the panel. Parts:
    "origin", "plot", and "cell_c_r" for the corners so an apple can be
    placed at a grid point.
    """
    x0, y0, x1, y1 = box
    ox, oy = x0 + 90, y1 - 90
    px1, py0 = x1 - 30, y0 + 30
    w, h = px1 - ox, oy - py0
    parts: Parts = {"self": box, "origin": (ox - 4, oy - 4, ox + 4, oy + 4), "plot": (ox, py0, px1, oy)}

    def point(c: float, r: float) -> Point:
        """Grid coordinates (0..cols, 0..rows from the origin) to pixels."""
        if warp <= 0:
            return (ox + w * c / cols, oy - h * r / rows)
        # fan: columns become spokes from a pivot below the panel
        pivot_y = oy + h * (1.0 / warp)
        angle = math.radians((c / cols - 0.5) * 60 * warp)
        radius = pivot_y - (oy - h * r / rows)
        return (ox + w / 2 + radius * math.sin(angle), pivot_y - radius * math.cos(angle))

    for c in range(cols + 1):
        pts = [point(c, r) for r in range(rows + 1)]
        d.line(pts, fill=S.WHITE, width=2, joint="curve")
    for r in range(rows + 1):
        pts = [point(c, r) for c in range(cols + 1)]
        d.line(pts, fill=S.WHITE, width=2, joint="curve")
    for c in range(cols + 1):
        for r in range(rows + 1):
            gx, gy = point(c, r)
            parts[f"cell_{c}_{r}"] = (gx - 6, gy - 6, gx + 6, gy + 6)

    if x_label:
        arrow(d, (ox + 20, oy + 46), (ox + 220, oy + 46), S.WHITE, shaft=10, head=32)
        outlined_text(d, (ox + 250, oy + 46), x_label, S.SIZE_LABEL_SMALL, S.WHITE, anchor="lm")
    if y_label:
        arrow(d, (ox - 46, oy - 20), (ox - 46, oy - 220), S.WHITE, shaft=10, head=32)
        # vertical presentation forms so （ ） read as ︵ ︶ when stacked
        chars = [{"（": "︵", "）": "︶", "(": "︵", ")": "︶"}.get(c, c) for c in y_label]
        top = oy - 250
        for i, ch in enumerate(chars):
            outlined_text(d, (ox - 46, top - (len(chars) - 1 - i) * 40), ch, S.SIZE_LABEL_SMALL - 4, S.WHITE)
    return parts


def columns(d: ImageDraw.ImageDraw, box: Box, items: list[dict[str, Any]], **_: Any) -> Parts:
    """Two or three headed columns: a coloured title, white description
    lines beneath. The multiverse video's "量子力学 / 一般相対性理論".

    Items: {"title", "colour", "text"} — text may hold newlines. Parts: each
    title's box, and "<title>.text" for the description block, so arrows can
    start under a column.
    """
    x0, y0, x1, y1 = box
    n = max(len(items), 1)
    col_w = (x1 - x0) / n
    parts: Parts = {"self": box}
    for i, item in enumerate(items):
        cx = x0 + col_w * (i + 0.5)
        colour = S.PALETTE.get(item.get("colour", "white"), S.WHITE)
        ty = y0 + 40
        outlined_text(d, (cx, ty), item["title"], S.SIZE_LABEL, colour)
        tw, th = text_size(item["title"], S.SIZE_LABEL)
        parts[item["title"]] = (cx - tw / 2, ty - th / 2, cx + tw / 2, ty + th / 2)
        lines = str(item.get("text", "")).split("\n") if item.get("text") else []
        if lines:
            step = S.SIZE_LABEL_SMALL * 1.3
            top = ty + th / 2 + 34
            for j, line in enumerate(lines):
                outlined_text(d, (cx, top + j * step), line, S.SIZE_LABEL_SMALL - 2, S.WHITE)
            bw = max(text_size(line, S.SIZE_LABEL_SMALL - 2)[0] for line in lines)
            parts[f"{item['title']}.text"] = (cx - bw / 2, top - step / 2, cx + bw / 2, top + (len(lines) - 0.5) * step)
    return parts


REGISTRY = {
    "sun": sun, "earth_globe": earth_globe, "earth_arc": earth_arc, "moon": moon,
    "star_dots": star_dots, "galaxy": galaxy, "black_blob": black_blob,
    "ice_block": ice_block, "cloud": cloud, "atom": atom, "charge": charge,
    "pie": pie, "box_row": box_row, "timeline": timeline, "number_line": number_line,
    "table": table, "chain": chain, "grid_panel": grid_panel, "columns": columns,
}
