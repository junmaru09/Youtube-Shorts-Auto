"""The whiteboard: a persistent canvas edited one operation per narration line.

Six reference videos, 239 minutes: the whole frame cuts seven times, the
stage changes 1,286 times. The picture is edited, not cut. This module is
that editing model.

    canvas = Canvas()
    canvas.apply({"op": "background", "name": "parchment"})
    canvas.apply({"op": "place", "element": "earth_arc", "slot": "floor", "name": "earth"})
    canvas.apply({"op": "add", "element": "ice_block", "near": "earth", "name": "ice1"})
    canvas.apply({"op": "label", "text": "正のフィードバック", "at": "top"})
    image = canvas.render(subtitle="…", speaker="explainer")

Every operation mutates the state; `render` draws the current state. The
script stage emits the operations, the assemble stage renders one frame per
narration line and holds it for that line's duration.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from . import draw as D
from . import elements as E
from . import style as S

Box = tuple[float, float, float, float]


class CanvasError(ValueError):
    """An operation that cannot be applied: unknown element, missing target."""


# --- slots ----------------------------------------------------------------------
#
# Named regions of the stage. The script never gives pixels; it names a slot,
# and the renderer resolves it. Sized against the reference: a "center" figure
# takes most of the stage, quadrants take a little under half.

def _slots() -> dict[str, Box]:
    L, T, R, B = S.STAGE_LEFT, S.STAGE_TOP, S.STAGE_RIGHT, S.STAGE_BOTTOM
    FL, FR = S.STAGE_FULL_LEFT, S.STAGE_FULL_RIGHT
    W, H = S.STAGE_W, S.STAGE_H
    cx, cy = (L + R) / 2, (T + B) / 2
    return {
        "center":       (cx - W * 0.38, T + 40, cx + W * 0.38, B - 30),
        "center-small": (cx - W * 0.20, cy - H * 0.26, cx + W * 0.20, cy + H * 0.26),
        "left":         (L, T + 60, cx - 40, B - 60),
        "right":        (cx + 40, T + 60, R, B - 60),
        "top":          (FL, T, FR, T + 340),                      # full width: nothing is beside it
        "bottom":       (L + W * 0.08, B - H * 0.42, R - W * 0.08, B),
        "top-left":     (FL, T, cx - 40, S.SPRITE_TOP - 140),
        "top-right":    (cx + 40, T, FR, S.SPRITE_TOP - 140),
        "bottom-left":  (L, cy + 20, cx - 40, B),
        "bottom-right": (cx + 40, cy + 20, R, B),
        "sky":          (-40, -50, 300, 290),                     # sun, cut by the corner as in the reference
        "floor":        (FL - 20, 750, FR + 20, S.HEIGHT),        # earth arc: apex at y=750
        "wide":         (L, 180, R, 720),                         # timeline, number line
        "left-third":   (L, T + H * 0.15, L + W * 0.31, B - H * 0.15),
        "mid-third":    (L + W * 0.345, T + H * 0.15, L + W * 0.655, B - H * 0.15),
        "right-third":  (R - W * 0.31, T + H * 0.15, R, B - H * 0.15),
    }


SLOTS = _slots()

# How big an illustration is drawn, by the `size` a script gives it.
ILLUSTRATION_SIZES = {"small": 240, "normal": 400, "large": 560, "huge": 700}

# Label sizes by name, for scripts that say `size=large`.
NAMED_SIZES = {"small": S.SIZE_LABEL_SMALL, "normal": S.SIZE_LABEL, "medium": S.SIZE_LABEL,
               "large": 66, "big": 66, "huge": S.SIZE_TITLE, "title": S.SIZE_TITLE}

# An arrow's `to` may be a compass direction instead of a target: "bounce
# up-right off the ice". Unit vectors, screen coordinates (y down).
DIRECTIONS = {
    "up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0),
    "up-right": (0.7071, -0.7071), "up-left": (-0.7071, -0.7071),
    "down-right": (0.7071, 0.7071), "down-left": (-0.7071, 0.7071),
}

# Where a new element goes when added "near" an existing one. Elements that
# stand on a surface line up along it; everything else steps to the right.
ATTACH = {
    "ice_block": "on_surface",
    "charge": "beside",
    "moon": "orbit",
}


@dataclass
class Item:
    name: str
    kind: str                 # "element" | "label" | "arrow" | "highlight" | "strike" | "compare"
    box: Box                  # where it is drawn (a slot, for elements)
    props: dict[str, Any] = field(default_factory=dict)
    parts: dict[str, Box] = field(default_factory=dict)
    extent: Box | None = None # what it actually covers, for references and collisions

    @property
    def bounds(self) -> Box:
        return self.extent or self.box


@dataclass
class State:
    background: str = "parchment"
    background_photo: Path | None = None
    heading: str | None = None
    heading_style: dict[str, str] = field(default_factory=lambda: {"colour": "white", "align": "center"})
    title: str | None = None
    list_items: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    expression: dict[str, str] = field(default_factory=lambda: {"explainer": "normal", "listener": "normal"})

    def find(self, name: str) -> Item:
        for item in self.items:
            if item.name == name:
                return item
        # "arrow", "label", "chain", "table": the most recent item of that
        # kind or element, which is what a script means when it named none
        for item in reversed(self.items):
            if item.kind == name or item.props.get("element") == name:
                return item
            if item.name.startswith(name) and item.name[len(name):].isdigit():
                return item
        raise CanvasError(f"no item named {name!r} on the stage (items: {[i.name for i in self.items][-8:]})")

    def part_owner(self, part: str) -> tuple[Item, Box] | None:
        """A bare part name ("row1", "nucleus"): the one item that has it."""
        owners = [(item, item.parts[part]) for item in self.items if part in item.parts and part != "self"]
        if len(owners) == 1:
            return owners[0]
        return None

    def box_of(self, ref: str) -> Box:
        """An item name, item.part, a bare part name, the title / heading,
        or a slot name — items first, because a script that names its sun
        "sky" means the sun."""
        if ref in SLOTS and not any(i.name == ref for i in self.items):
            return SLOTS[ref]
        if ref == "title" and self.title and not any(i.name == "title" for i in self.items):
            cx, cy = (S.STAGE_LEFT + S.STAGE_RIGHT) / 2, (S.STAGE_TOP + S.STAGE_BOTTOM) / 2
            lines = self.title.split("\n")
            w = max(D.text_size(line, S.SIZE_TITLE)[0] for line in lines) + 16
            h = S.SIZE_TITLE * 1.25 * len(lines)
            return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        if ref == "heading" and self.heading and not any(i.name == "heading" for i in self.items):
            w = D.text_size(self.heading, S.SIZE_HEADING)[0] + 80
            if S.THEME == "notebook":
                return (S.STAGE_LEFT, 0, S.STAGE_LEFT + w, S.HEADING_Y + S.SIZE_HEADING / 2 + 14)
            cx = (S.STAGE_LEFT + S.STAGE_RIGHT) / 2
            return (cx - w / 2, S.HEADING_Y - S.SIZE_HEADING / 2, cx + w / 2, S.HEADING_Y + S.SIZE_HEADING / 2)
        if "." in ref:
            item_name, part = ref.split(".", 1)
            item = self.find(item_name)
            if part in item.parts:
                return item.parts[part]
            edge = _edge_part(item.bounds, part)
            if edge is not None:
                return edge
            raise CanvasError(f"{item_name!r} has no part {part!r} (has: {sorted(item.parts)}, "
                              "plus top/bottom/left/right/centre on anything)")
        try:
            return self.find(ref).bounds
        except CanvasError:
            owner = self.part_owner(ref)
            if owner is None:
                raise
            return owner[1]


OPS = (
    "hold", "clear", "place", "add", "label", "arrow", "strike", "highlight",
    "list_add", "title", "heading", "background", "compare", "tiles", "table",
    "chain", "zoom", "panel", "expression", "dim", "columns",
)


class Canvas:
    def __init__(self, assets: "AssetLibrary | None" = None, seed: int = 0):
        self.state = State()
        self.assets = assets
        self.seed = seed
        # A photo the current chapter brought (footage's still). Used when the
        # script asks for `background name=space`; parchment and room stay
        # drawn, because a photo under a parchment figure looks like a slide.
        self.chapter_photo: Path | None = None
        self._counter = 0
        self._bg_cache: dict[str, Image.Image] = {}

    # --- operations ---------------------------------------------------------------

    def apply(self, op: dict[str, Any]) -> None:
        kind = op.get("op")
        if kind not in OPS:
            raise CanvasError(f"unknown op {kind!r}; one of {OPS}")
        try:
            getattr(self, f"_op_{kind}")(op)
        except KeyError as exc:
            # a missing argument, or an element given a prop it does not take
            raise CanvasError(f"{kind}: missing {exc}") from exc
        except (TypeError, ValueError, AttributeError, IndexError) as exc:
            # a wrong-typed argument; the script stage turns this into a
            # rejection with the line, rather than a crash without one
            raise CanvasError(f"{kind}: {exc}") from exc

    def _register(self, item: Item) -> None:
        """Add an element to the stage with its parts already known.

        Parts come from a throwaway draw. Later operations in the same line
        ("arrow to ice1.top") need them before anything is rendered.
        """
        if item.kind == "element":
            scratch = Image.new("RGB", (S.WIDTH, S.HEIGHT))
            item.parts = self._draw_element(scratch, ImageDraw.Draw(scratch), item)
            # the element's own extent, not the slot it was offered: a label
            # "below" a boxed word goes under the box, not under the slot.
            # The drawing box stays the slot, because chains and graphs lay
            # themselves out inside it.
            own = item.parts.get("self")
            if own and own[2] - own[0] > 1 and own[3] - own[1] > 1:
                item.extent = tuple(own)
        self.state.items.append(item)

    def _name(self, op: dict[str, Any], prefix: str) -> str:
        if op.get("name"):
            return str(op["name"])
        self._counter += 1
        return f"{prefix}{self._counter}"

    def _op_hold(self, op: dict[str, Any]) -> None:
        pass

    def _op_clear(self, op: dict[str, Any]) -> None:
        keep_heading = op.get("keep_heading", False)
        heading = self.state.heading if keep_heading else None
        self.state = State(background=self.state.background,
                           background_photo=self.state.background_photo,
                           heading=heading, expression=self.state.expression)

    def _op_expression(self, op: dict[str, Any]) -> None:
        for who in ("explainer", "listener"):
            if who in op:
                self.state.expression[who] = op[who]

    def _op_background(self, op: dict[str, Any]) -> None:
        name = op.get("name", "parchment")
        self.state.background = name
        self.state.background_photo = Path(op["photo"]) if op.get("photo") else None

    def _op_heading(self, op: dict[str, Any]) -> None:
        self.state.heading = op.get("text") or None
        colour = op.get("colour", "white")
        if colour not in S.PALETTE:
            raise CanvasError(f"unknown colour {colour!r}; one of {sorted(S.PALETTE)}")
        self.state.heading_style = {"colour": colour, "align": op.get("align", "center")}

    def _op_columns(self, op: dict[str, Any]) -> None:
        box = (S.STAGE_FULL_LEFT, S.STAGE_TOP + 40, S.STAGE_FULL_RIGHT, S.SPRITE_TOP - 100)
        self._register(Item(self._name(op, "columns"), "element", box,
                            {"element": "columns", "items": op["items"]}))

    def _op_title(self, op: dict[str, Any]) -> None:
        self.state.title = op.get("text") or None
        # a big title over a figure dims the figure, as the reference does;
        # `dim=false` keeps the stage bright (a title alone on a page)
        if self.state.title and op.get("dim", True) and any(i.kind != "dim" for i in self.state.items):
            self._op_dim(op)

    def _op_dim(self, op: dict[str, Any]) -> None:
        """Darken everything drawn so far; later items draw on top at full
        brightness. The reference's "dim the figure, write over it"."""
        self.state.items.append(Item(self._name(op, "dim"), "dim", (0, 0, S.WIDTH, S.HEIGHT),
                                     {"alpha": int(op.get("alpha", S.DIM_ALPHA))}))

    def _op_list_add(self, op: dict[str, Any]) -> None:
        self.state.list_items.append(str(op["text"]))

    def _op_place(self, op: dict[str, Any]) -> None:
        element = op["element"]
        if element not in E.REGISTRY and not (self.assets and self.assets.has(element)):
            raise CanvasError(f"unknown element {element!r}")
        slot = op.get("slot", "center")
        box = self._box_from(op) if "box" in op else self.state.box_of(slot) if slot in SLOTS else self._box_from(op)
        props = {k: v for k, v in op.items() if k not in ("op", "element", "slot", "name", "box")}
        name = self._name(op, element)
        existing = next((i for i in self.state.items if i.name == name and i.kind == "element"), None)
        if existing is not None and "slot" not in op and "box" not in op:
            # the same thing placed again (a balance re-tilted, a pie with a
            # new slice): change it where it stands rather than add a twin
            existing.props = {"element": element, **props}
            scratch = Image.new("RGB", (S.WIDTH, S.HEIGHT))
            existing.parts = self._draw_element(scratch, ImageDraw.Draw(scratch), existing)
            own = existing.parts.get("self")
            existing.extent = tuple(own) if own else existing.extent
            return
        if existing is not None:
            self.state.items.remove(existing)
        item = Item(name, "element", box, {"element": element, **props})
        self._register(item)
        if slot not in ("sky", "floor") and "box" not in op:
            self._nudge_element(item, box)

    def _box_from(self, op: dict[str, Any]) -> Box:
        if "box" in op:
            x0, y0, x1, y1 = op["box"]  # fractions of the stage
            return (S.STAGE_LEFT + x0 * S.STAGE_W, S.STAGE_TOP + y0 * S.STAGE_H,
                    S.STAGE_LEFT + x1 * S.STAGE_W, S.STAGE_TOP + y1 * S.STAGE_H)
        raise CanvasError(f"place needs a slot from {sorted(SLOTS)} or a box")

    def _op_add(self, op: dict[str, Any]) -> None:
        """A new element positioned relative to an existing one."""
        element = op["element"]
        if element not in E.REGISTRY and not (self.assets and self.assets.has(element)):
            raise CanvasError(f"unknown element {element!r}; one of {sorted(E.REGISTRY)}")
        near = self.state.find(op["near"])
        siblings = [i for i in self.state.items if i.props.get("element") == element and i.props.get("near") == near.name]
        rule = ATTACH.get(element, "beside")
        nx0, ny0, nx1, ny1 = near.bounds
        size = op.get("size", 260 if rule == "on_surface" else round((nx1 - nx0) * 0.32) if rule == "orbit" else 170)

        if rule == "on_surface" and "surface" in near.parts:
            sx0, apex_y, sx1, edge_y = near.parts["surface"]
            # spread along the surface from the centre outward: c, c+1, c-1, c+2 …
            n = len(siblings)
            offsets = [0] + [s * k for k in range(1, 8) for s in (1, -1)]
            spread = size * 1.02
            scx = (sx0 + sx1) / 2
            cx = scx + offsets[n] * spread
            # the surface is an arc; rest the block where the arc is at this x
            sy = apex_y + (edge_y - apex_y) * ((cx - scx) / ((sx1 - sx0) / 2)) ** 2
            box = (cx - size / 2, sy - size * 0.87 + 10, cx + size / 2, sy + 10)
        elif rule == "orbit":
            cx, cy = nx1 + size * 0.55, ny0 + size * 0.15
            box = (cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2)
        else:
            nx0, ny0, nx1, ny1 = near.bounds
            n = len(siblings)
            cx = nx1 + size * 0.75 + n * size * 1.2
            cy = (ny0 + ny1) / 2
            box = (cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2)

        props = {k: v for k, v in op.items() if k not in ("op", "element", "near", "name", "size")}
        self._register(Item(self._name(op, element), "element", box, {"element": element, "near": near.name, **props}))

    def _op_label(self, op: dict[str, Any]) -> None:
        text = str(op["text"])
        at = str(op.get("at", "center"))
        if " " in at and at.rsplit(" ", 1)[1] in ("above", "below", "left", "right", "on"):
            at, op = at.rsplit(" ", 1)[0], {**op, "side": at.rsplit(" ", 1)[1]}   # `at=dense below`
        size = op.get("size", S.SIZE_LABEL)
        if isinstance(size, str):
            size = NAMED_SIZES.get(size.lower())
            if size is None:
                raise CanvasError(f"unknown size {op['size']!r}; a number or one of {sorted(NAMED_SIZES)}")
        size = int(size)
        colour = op.get("colour", "white")
        if colour not in S.PALETTE:
            raise CanvasError(f"unknown colour {colour!r}; one of {sorted(S.PALETTE)}")
        target = self.state.box_of(at)
        tx0, ty0, tx1, ty1 = target
        tw, th = D.text_size(text, size)
        side = op.get("side", "auto")
        if side == "auto":
            side = "below" if ty0 < S.STAGE_TOP + S.STAGE_H * 0.25 else "above"
        is_slot = at in SLOTS and not any(i.name == at for i in self.state.items)
        pointer = bool(op.get("pointer", False)) and not is_slot
        gap = 110 if pointer else 36          # room for the little arrow

        def at_side(which: str) -> Box:
            if is_slot or which == "on":
                cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
            elif which == "above":
                cx, cy = (tx0 + tx1) / 2, ty0 - th / 2 - gap
            elif which == "below":
                cx, cy = (tx0 + tx1) / 2, ty1 + th / 2 + gap
            elif which == "left":
                cx, cy = tx0 - tw / 2 - gap, (ty0 + ty1) / 2
            else:
                cx, cy = tx1 + tw / 2 + gap, (ty0 + ty1) / 2
            return (cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2)

        # stay off everything except the thing this label is attached to
        target_item = None
        if not is_slot:
            try:
                target_item = self.state.find(at.split(".", 1)[0])
            except CanvasError:
                target_item = None
        avoid = self._occupied(except_item=target_item if side == "on" else None)
        if target_item is not None and side != "on":
            # the target's own text parts stay avoided; its body does not
            avoid = [b for b in avoid if b != target_item.bounds]

        # the asked-for side, then the opposite, then the other two: a label
        # "below" something that sits on the band goes above it instead of
        # across the stage
        box = at_side(side)
        if not is_slot and side != "on" and (not _inside_stage(box) or any(_overlaps(box, o) for o in avoid)):
            opposite = {"above": "below", "below": "above", "left": "right", "right": "left"}
            for which in (opposite.get(side, "above"), *[s for s in ("above", "below", "right", "left") if s not in (side, opposite.get(side))]):
                candidate = at_side(which)
                if _inside_stage(candidate) and not any(_overlaps(candidate, o) for o in avoid):
                    box, side = candidate, which
                    break
        box = self._nudge_label(box, side, avoid)
        props: dict[str, Any] = {"text": text, "size": size, "colour": colour, "at": at}
        if pointer:
            # a short arrow from the label's edge to the target's edge
            p0, p1 = _edge_points(box, target, pad=8)
            props["pointer"] = (p0, p1)
        self.state.items.append(Item(self._name(op, "label"), "label", box, props))

    # Elements that are mostly text: a label must not land on them. Figures
    # (pie, timeline, earth) take labels on top by design.
    TEXT_ELEMENTS = {"concept", "box_row", "table", "columns", "chain", "ladder"}

    def _occupied(self, except_item: Item | None = None) -> list[Box]:
        """Boxes a new label must stay off: every label, every text-sized
        part of every element, and the whole of text-bearing elements."""
        boxes: list[Box] = []
        if self.state.heading:
            boxes.append(self.state.box_of("heading"))
        if self.state.title:
            boxes.append(self.state.box_of("title"))
        for item in self.state.items:
            if item is except_item:
                continue
            if item.kind == "label":
                boxes.append(item.box)
            elif item.kind == "element":
                if item.props.get("element") in self.TEXT_ELEMENTS or item.props.get("element") not in E.REGISTRY:
                    boxes.append(item.bounds)        # text, or an illustration
                for name, part in item.parts.items():
                    if name != "self" and part[3] - part[1] <= 140 and part[2] - part[0] >= 20:
                        boxes.append(part)
        return boxes

    def _nudge_label(self, box: Box, side: str, avoid: list[Box] | None = None) -> Box:
        """Move a label off anything already there.

        Two bands labelled "氷期" and "間氷期" land on the same spot when both
        are put above the timeline; the reference sets them side by side.
        Slide sideways first (the pointer still reaches), then stack, and
        never leave the stage.
        """
        others = self._occupied() if avoid is None else avoid
        w, h = box[2] - box[0], box[3] - box[1]
        if _inside_stage(box) and not any(_overlaps(box, o) for o in others):
            return box
        if not _inside_stage(box):
            # off the stage (under the band, past an edge): pull it back first
            box = (min(max(box[0], S.STAGE_FULL_LEFT), S.STAGE_FULL_RIGHT - w), min(max(box[1], 4), S.STAGE_BOTTOM - h),
                   0, 0)
            box = (box[0], box[1], box[0] + w, box[1] + h)
            if not any(_overlaps(box, o) for o in others):
                return box
        pushed = _push_out(box, others)
        if pushed is not None:
            return pushed
        # candidate moves, nearest first: sideways for above/below labels,
        # up/down for left/right ones, then the other axis, then diagonals
        steps = []
        for k in (1, 2, 3):
            dx, dy = (w + 28) * k, (h + 16) * k
            primary = [(dx, 0), (-dx, 0)] if side in ("above", "below", "on", "auto") else [(0, -dy), (0, dy)]
            secondary = [(0, -dy), (0, dy)] if side in ("above", "below", "on", "auto") else [(dx, 0), (-dx, 0)]
            steps += primary + secondary
        steps += [(dx, -dy), (-dx, -dy), (dx, dy), (-dx, dy)]
        for dx, dy in steps:
            moved = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
            if not _inside_stage(moved):
                continue
            if not any(_overlaps(moved, o) for o in others):
                return moved
        return box

    def _nudge_element(self, item: Item, slot_box: Box) -> None:
        """Shift a freshly placed element off anything already there.

        Two concepts both put at "center" should end up side by side, not on
        top of each other. Candidates are grid positions over the stage,
        nearest first, at full size, then 72%, then 50%; the extent at a
        candidate is the current extent translated (drawing is translation-
        equivariant), so only the winner is redrawn. If nothing is free, the
        least-overlapping candidate wins.
        """
        others = [i for i in self.state.items if i.kind in ("element", "label") and i is not item]
        fixed = [self.state.box_of(ref) for ref in ("heading", "title")
                 if getattr(self.state, ref) and not any(i.name == ref for i in self.state.items)]
        obstacles = [o.bounds for o in others] + fixed
        mine = item.bounds
        if _inside_stage(mine) and not any(_overlaps(mine, o, 0) for o in obstacles):
            return

        def extent_at(scale: float) -> tuple[Box, Box]:
            """(slot box, extent) for the element drawn at `scale` of its slot."""
            sw, sh = (slot_box[2] - slot_box[0]) * scale, (slot_box[3] - slot_box[1]) * scale
            scx, scy = (slot_box[0] + slot_box[2]) / 2, (slot_box[1] + slot_box[3]) / 2
            base = (scx - sw / 2, scy - sh / 2, scx + sw / 2, scy + sh / 2)
            if scale == 1.0:
                return base, mine
            item.box = base
            scratch = Image.new("RGB", (S.WIDTH, S.HEIGHT))
            parts = self._draw_element(scratch, ImageDraw.Draw(scratch), item)
            return base, tuple(parts.get("self", base))

        ocx, ocy = (mine[0] + mine[2]) / 2, (mine[1] + mine[3]) / 2
        grid = [(gx, gy) for gx in range(S.STAGE_FULL_LEFT, S.STAGE_FULL_RIGHT, 60)
                for gy in range(40, S.STAGE_BOTTOM, 50)]
        grid.sort(key=lambda g: (g[0] - ocx) ** 2 + (g[1] - ocy) ** 2)
        best: tuple[float, Box, float] | None = None       # (overlap area, slot box, scale)
        for scale in (1.0, 0.72, 0.5, 0.36):
            base, ext = extent_at(scale)
            ew, eh = ext[2] - ext[0], ext[3] - ext[1]
            ecx, ecy = (ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2
            for gx, gy in grid:
                dx, dy = gx - ecx, gy - ecy
                moved = (ext[0] + dx, ext[1] + dy, ext[2] + dx, ext[3] + dy)
                if not _inside_stage(moved):
                    continue
                area = sum(_overlap_area(moved, o) for o in obstacles)
                if area == 0:
                    self._settle(item, (base[0] + dx, base[1] + dy, base[2] + dx, base[3] + dy))
                    return
                if best is None or area < best[0]:
                    best = (area, (base[0] + dx, base[1] + dy, base[2] + dx, base[3] + dy), scale)
        self._settle(item, best[1] if best else slot_box)

    def _settle(self, item: Item, box: Box) -> None:
        item.box = box
        scratch = Image.new("RGB", (S.WIDTH, S.HEIGHT))
        item.parts = self._draw_element(scratch, ImageDraw.Draw(scratch), item)
        own = item.parts.get("self")
        item.extent = tuple(own) if own else None

    def _target(self, ref: str) -> Box:
        """An arrow endpoint. Slots count as their centre point, not their
        box, or an arrow "to top-right" would stop the moment it left the
        source."""
        if ref in SLOTS and not any(i.name == ref for i in self.state.items):
            x0, y0, x1, y1 = SLOTS[ref]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            return (cx, cy, cx, cy)
        return self.state.box_of(ref)

    def _op_arrow(self, op: dict[str, Any]) -> None:
        """From one thing to another, optionally bent at `via`, or from one
        thing off in a compass `to` direction for `length` px."""
        colour = op.get("colour", "white")
        if colour not in S.PALETTE:
            raise CanvasError(f"unknown colour {colour!r}")
        pad = float(op.get("pad", 14))
        a = self._target(op["from"])
        if op["to"] in DIRECTIONS:
            dx, dy = DIRECTIONS[op["to"]]
            length = float(op.get("length", 320))
            if "via" in op:
                v = self._target(op["via"])
                p0, corner = _edge_points(a, v, pad=pad)
                points = [p0, corner, (corner[0] + dx * length, corner[1] + dy * length)]
            else:
                ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
                far = (ax + dx * 4000, ay + dy * 4000)
                start = _edge_points(a, (*far, *far), pad=pad)[0]
                points = [start, (start[0] + dx * length, start[1] + dy * length)]
        else:
            b = self._target(op["to"])
            if "via" in op:
                v = self._target(op["via"])
                p0, corner = _edge_points(a, v, pad=pad)
                p1 = _edge_points(v, b, pad=pad)[1]
                points = [p0, corner, p1]
            else:
                points = list(_edge_points(a, b, pad=pad))
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        self.state.items.append(Item(self._name(op, "arrow"), "arrow", (min(xs), min(ys), max(xs), max(ys)),
                                     {"points": points, "colour": colour, "bulge": float(op.get("bulge", 0)),
                                      "heads": int(op.get("heads", 1)), "dashed": bool(op.get("dashed", False))}))

    def _op_strike(self, op: dict[str, Any]) -> None:
        target = self.state.find(op["target"])
        self.state.items.append(Item(self._name(op, "strike"), "strike", target.bounds, {"target": target.name}))

    def _op_highlight(self, op: dict[str, Any]) -> None:
        box = self.state.box_of(op["target"])
        self.state.items.append(Item(self._name(op, "highlight"), "highlight", box, {"target": op["target"]}))

    def _op_compare(self, op: dict[str, Any]) -> None:
        """Two or three elements side by side, each with a caption."""
        elements = op["elements"]
        labels = op.get("labels", [""] * len(elements))
        thirds = ("left-third", "mid-third", "right-third") if len(elements) == 3 else ("left", "right")
        for element, label, slot in zip(elements, labels, thirds):
            box = SLOTS[slot]
            drawable = element in E.REGISTRY or (self.assets is not None and self.assets.has(element))
            # comparing two ideas rather than two things: a boxed word each
            props = {"element": element} if drawable else {"element": "concept", "text": label or element}
            name = element if not any(i.name == element for i in self.state.items) else f"{op.get('name', 'cmp')}_{slot.split('-')[0]}"
            self._register(Item(name, "element", box, props))
            if label and drawable:
                self.apply({"op": "label", "text": label, "at": name, "side": "above", "size": S.SIZE_LABEL_SMALL})

    def _op_tiles(self, op: dict[str, Any]) -> None:
        images, captions = op["images"], op.get("captions", [])
        n = len(images)
        x0, y0, x1, y1 = SLOTS["wide"]
        w = (x1 - x0) / n
        for i, image in enumerate(images):
            box = (x0 + i * w + 12, y0 - 40, x0 + (i + 1) * w - 12, y1 + 40)
            self.state.items.append(Item(f"tile{i + 1}", "tile", box,
                                         {"image": image, "caption": captions[i] if i < len(captions) else ""}))

    def _op_table(self, op: dict[str, Any]) -> None:
        box = (S.STAGE_FULL_LEFT, 90, S.STAGE_FULL_RIGHT, S.STAGE_BOTTOM)
        self._register(Item(self._name(op, "table"), "element", box,
                            {"element": "table", "columns": op["columns"], "rows": op.get("rows", []),
                             "highlight": op.get("highlight")}))

    def _op_chain(self, op: dict[str, Any]) -> None:
        self._register(Item(self._name(op, "chain"), "element", SLOTS[op.get("slot", "center")],
                            {"element": "chain", "nodes": op["nodes"], "edges": op.get("edges", [])}))

    def _op_zoom(self, op: dict[str, Any]) -> None:
        """A magnifier circle showing `element` enlarged, tethered to `from`."""
        src = self.state.box_of(op["from"])
        target = SLOTS[op.get("slot", "right")]
        self.state.items.append(Item(self._name(op, "zoom"), "zoom", target,
                                     {"element": op["element"], "from": src}))

    def _op_panel(self, op: dict[str, Any]) -> None:
        self._register(Item(self._name(op, "panel"), "element", SLOTS[op.get("slot", "center")],
                            {"element": "grid_panel", "x_label": op.get("x", ""), "y_label": op.get("y", ""),
                             "warp": float(op.get("warp", 0.0))}))

    # --- rendering ----------------------------------------------------------------

    def background(self) -> Image.Image:
        st = self.state
        photo = st.background_photo or (self.chapter_photo if st.background == "space" else None)
        key = f"{st.background}:{photo}"
        if key not in self._bg_cache:
            if photo and photo.exists():
                img = D.dimmed_photo(Image.open(photo))
            elif st.background == "space" and not (self.assets and self.assets.background("space")):
                img = D.starfield(seed=self.seed)
            elif st.background == "room" and not (self.assets and self.assets.background("room")):
                img = D.room()
            elif st.background in ("parchment", "paper", "notebook") or not self.assets or not self.assets.background(st.background):
                img = D.notebook(seed=self.seed) if S.THEME == "notebook" else D.parchment(seed=self.seed)
            else:
                img = D.dimmed_photo(Image.open(self.assets.background(st.background)),
                                     factor=1.0 if st.background == "room" else S.PHOTO_DIM)
            self._bg_cache[key] = img
        return self._bg_cache[key].copy()

    def render_stage(self, halo: list[Item] | None = None) -> Image.Image:
        """Everything except sprites and subtitles. `halo` names items to
        draw a soft yellow glow behind — what this line just added."""
        img = self.background()
        st = self.state
        if halo:
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            for item in halo:
                if item.kind in ("dim",):
                    continue
                x0, y0, x1, y1 = item.bounds
                pad = 22
                # a glowing edge, not a filled block: a big figure would
                # otherwise sit in a brown slab for half a second
                gd.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=30,
                                     outline=S.APPEAR_HALO[:3] + (200,), width=18)
            glow = glow.filter(ImageFilter.GaussianBlur(16))
            img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
        d = ImageDraw.Draw(img)

        for item in st.items:
            if item.kind == "element":
                item.parts = self._draw_element(img, d, item)
            elif item.kind == "label":
                colour = S.PALETTE[item.props["colour"]]
                D.outlined_text(d, ((item.box[0] + item.box[2]) / 2, (item.box[1] + item.box[3]) / 2),
                                item.props["text"], item.props["size"], colour)
                if "pointer" in item.props:
                    p0, p1 = item.props["pointer"]
                    D.arrow(d, p0, p1, colour, shaft=12, head=34)
            elif item.kind == "arrow":
                points, colour = item.props["points"], S.PALETTE[item.props["colour"]]
                if item.props.get("dashed") and len(points) == 2:
                    D.dashed_line(d, points[0], points[1], colour)
                elif item.props.get("heads", 1) == 2 and len(points) == 2:
                    D.double_arrow(d, points[0], points[1], colour)
                elif item.props["bulge"] and len(points) == 2:
                    D.curved_arrow(d, points[0], points[1], item.props["bulge"], colour)
                else:
                    D.polyline_arrow(d, points, colour)
            elif item.kind == "dim":
                D.dim(img, item.props["alpha"])
                d = ImageDraw.Draw(img)
            elif item.kind == "strike":
                D.strike(d, st.find(item.props["target"]).bounds)
            elif item.kind == "highlight":
                D.highlight(d, st.box_of(item.props["target"]))
            elif item.kind == "tile":
                self._draw_tile(img, d, item)
            elif item.kind == "zoom":
                self._draw_zoom(img, d, item)

        if st.heading:
            colour = S.PALETTE[st.heading_style.get("colour", "white")]
            if S.THEME == "notebook":
                # an index tab: yellow, dark text, hanging from the page's top edge
                tw, th = D.text_size(st.heading, S.SIZE_HEADING)
                x0 = S.STAGE_LEFT          # clear of the sun's corner
                tab = (x0, 0, x0 + tw + 80, S.HEADING_Y + th / 2 + 14)
                d.rounded_rectangle(tab, radius=16, fill=S.INK)
                d.rounded_rectangle((tab[0] + 4, tab[1] - 20, tab[2] - 4, tab[3] - 4), radius=14,
                                    fill=S.TAB_FILL if colour == S.WHITE else colour)
                D.outlined_text(d, (x0 + 40, S.HEADING_Y), st.heading, S.SIZE_HEADING, S.TAB_INK,
                                anchor="lm", outline=S.TAB_FILL if colour == S.WHITE else colour, width=0)
            elif st.heading_style.get("align") == "left":
                D.outlined_text(d, (S.STAGE_FULL_LEFT + 40, S.HEADING_Y), st.heading, S.SIZE_HEADING, colour, anchor="lm")
            else:
                D.outlined_text(d, ((S.STAGE_LEFT + S.STAGE_RIGHT) / 2, S.HEADING_Y), st.heading, S.SIZE_HEADING, colour)
        if st.list_items:
            E.numbered_list(d, st.list_items)
        if st.title:
            D.outlined_lines(d, ((S.STAGE_LEFT + S.STAGE_RIGHT) / 2, (S.STAGE_TOP + S.STAGE_BOTTOM) / 2),
                             st.title.split("\n"), S.SIZE_TITLE, S.WHITE, width=8)
        return img

    def _draw_element(self, img: Image.Image, d: ImageDraw.ImageDraw, item: Item) -> dict[str, Box]:
        name = item.props["element"]
        props = {k: v for k, v in item.props.items() if k not in ("element", "near")}
        if name in E.REGISTRY:
            return E.REGISTRY[name](d, item.box, **props)
        if self.assets and self.assets.has(name):
            sprite = self.assets.image(name)
            box = E._fit(item.box, sprite.width / sprite.height)
            # an illustration is a thing on the page, not a poster: cap it
            cap = ILLUSTRATION_SIZES.get(str(props.get("size", "normal")), ILLUSTRATION_SIZES["normal"])
            w, h = box[2] - box[0], box[3] - box[1]
            if max(w, h) > cap:
                scale = cap / max(w, h)
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                w, h = w * scale, h * scale
                box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            resized = sprite.resize((max(1, round(box[2] - box[0])), max(1, round(box[3] - box[1]))), Image.LANCZOS)
            img.paste(resized, (round(box[0]), round(box[1])), resized if resized.mode == "RGBA" else None)
            return {"self": box}
        raise CanvasError(f"cannot draw {name!r}")

    def _draw_tile(self, img: Image.Image, d: ImageDraw.ImageDraw, item: Item) -> None:
        x0, y0, x1, y1 = item.box
        path = Path(item.props["image"])
        if path.exists():
            photo = Image.open(path).convert("RGB")
            scale = max((x1 - x0) / photo.width, (y1 - y0 - 70) / photo.height)
            photo = photo.resize((round(photo.width * scale), round(photo.height * scale)), Image.LANCZOS)
            photo = photo.crop((0, 0, round(x1 - x0), round(y1 - y0 - 70)))
            img.paste(photo, (round(x0), round(y0)))
        d.rectangle((x0, y0, x1, y1 - 70), outline=S.INK, width=5)
        if item.props.get("caption"):
            D.outlined_text(d, ((x0 + x1) / 2, y1 - 30), item.props["caption"], S.SIZE_LABEL, S.WHITE)

    def _draw_zoom(self, img: Image.Image, d: ImageDraw.ImageDraw, item: Item) -> None:
        x0, y0, x1, y1 = E._fit(item.box, 1.0)
        fx0, fy0, fx1, fy1 = item.props["from"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # tether lines from the source box corners to the circle
        d.line(((fx1, fy0), (cx - (x1 - x0) / 2 * 0.7, cy - (y1 - y0) / 2 * 0.7)), fill=S.INK, width=4)
        d.line(((fx1, fy1), (cx - (x1 - x0) / 2 * 0.7, cy + (y1 - y0) / 2 * 0.7)), fill=S.INK, width=4)
        d.ellipse((x0, y0, x1, y1), fill=(250, 250, 250), outline=S.INK, width=S.OUTLINE_SHAPE)
        inner = (x0 + 40, y0 + 40, x1 - 40, y1 - 40)
        element = item.props["element"]
        if element in E.REGISTRY:
            E.REGISTRY[element](d, inner)
        elif self.assets and self.assets.has(element):
            sprite = self.assets.image(element)
            box = E._fit(inner, sprite.width / sprite.height)
            resized = sprite.resize((round(box[2] - box[0]), round(box[3] - box[1])), Image.LANCZOS)
            img.paste(resized, (round(box[0]), round(box[1])), resized if resized.mode == "RGBA" else None)

    def finish(self, img: Image.Image, subtitle: str, speaker: str,
               sprites: "SpriteSet | None" = None, talking: bool = True, mouth_open: bool = False) -> Image.Image:
        """Sprites and the subtitle band on top of a rendered stage."""
        if sprites is not None:
            sprites.paste(img, self.state.expression, speaker if talking else None, mouth_open)
        else:
            _placeholder_sprites(img, speaker)
        _subtitle_band(img, subtitle, speaker)
        return img

    def render(self, subtitle: str = "", speaker: str = "explainer",
               sprites: "SpriteSet | None" = None, talking: bool = True, mouth_open: bool = False) -> Image.Image:
        return self.finish(self.render_stage(), subtitle, speaker, sprites, talking, mouth_open)

    def snapshot(self) -> State:
        return copy.deepcopy(self.state)


# --- helpers ---------------------------------------------------------------------


def _push_out(box: Box, others: list[Box], margin: float = 10, rounds: int = 8) -> Box | None:
    """Move `box` by the smallest translation that clears every box in
    `others`, a few rounds over. A five-pixel corner touch becomes a
    fifteen-pixel slide, not a jump across the stage. None if it cannot."""
    for _ in range(rounds):
        hit = next((o for o in others if _overlaps(box, o, margin)), None)
        if hit is None:
            return box if _inside_stage(box) else None
        # the four ways out, cheapest first (a pixel past the margin, so the
        # strict overlap test below sees clear air)
        m = margin + 2
        moves = sorted([
            (hit[2] + m - box[0], (hit[2] + m - box[0], 0)),      # right
            (box[2] - hit[0] + m, (-(box[2] - hit[0] + m), 0)),   # left
            (hit[3] + m - box[1], (0, hit[3] + m - box[1])),      # down
            (box[3] - hit[1] + m, (0, -(box[3] - hit[1] + m))),   # up
        ], key=lambda mv: mv[0])
        for _, (dx, dy) in moves:
            moved = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
            if _inside_stage(moved):
                box = moved
                break
        else:
            return None
    return box if not any(_overlaps(box, o, margin) for o in others) and _inside_stage(box) else None


def _overlap_area(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _overlaps(a: Box, b: Box, margin: float = 8) -> bool:
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin or a[3] < b[1] - margin or a[1] > b[3] + margin)


def _inside_stage(box: Box) -> bool:
    """On the page, above the band, and not behind a character: the full
    width is free above the sprites' heads, only the middle beside them."""
    if box[1] < 0 or box[3] > S.STAGE_BOTTOM + 10:
        return False
    if box[3] > S.SPRITE_TOP - 20:
        return box[0] >= S.STAGE_LEFT and box[2] <= S.STAGE_RIGHT
    return box[0] >= S.STAGE_FULL_LEFT - 20 and box[2] <= S.STAGE_FULL_RIGHT + 20


def _edge_part(box: Box, part: str) -> Box | None:
    """`.top` and friends on any item: a point on that edge, or the centre.
    Elements name their meaningful parts; these are the ones every box has."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    points = {"top": (cx, y0), "bottom": (cx, y1), "left": (x0, cy), "right": (x1, cy),
              "centre": (cx, cy), "center": (cx, cy)}
    if part not in points:
        return None
    px, py = points[part]
    return (px - 8, py - 8, px + 8, py + 8)


def _edge_points(a: Box, b: Box, pad: float = 14.0) -> tuple[tuple[float, float], tuple[float, float]]:
    """Start and end of an arrow between two boxes, on their facing edges."""
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    dx, dy = bx - ax, by - ay

    def exit_point(box: Box, cx: float, cy: float, dirx: float, diry: float) -> tuple[float, float]:
        hw, hh = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
        if hw < 1 or hh < 1:
            return (cx, cy)
        tx = hw / abs(dirx) if dirx else float("inf")
        ty = hh / abs(diry) if diry else float("inf")
        t = min(tx, ty)
        return (cx + dirx * t + (pad * dirx / (abs(dirx) + abs(diry) or 1)),
                cy + diry * t + (pad * diry / (abs(dirx) + abs(diry) or 1)))

    return exit_point(a, ax, ay, dx, dy), exit_point(b, bx, by, -dx, -dy)


def _placeholder_sprites(img: Image.Image, speaker: str, only: str | None = None) -> None:
    """Coloured circles until real sprite assets exist."""
    d = ImageDraw.Draw(img)
    for who, colour, label in (("explainer", (120, 200, 120), "ずんだ"), ("listener", (200, 140, 220), "めたん")):
        if only and who != only:
            continue
        x, y = S.SPRITE_RIGHT if S.SPRITE_SIDE.get(who) == "right" else S.SPRITE_LEFT
        x += (S.SPRITE_WIDTH - S.SPRITE_SIZE) // 2
        shade = colour if who == speaker else tuple(int(c * 0.72) for c in colour)
        d.ellipse((x + 20, y + 20, x + S.SPRITE_SIZE - 20, y + S.SPRITE_SIZE - 20), fill=shade, outline=S.INK, width=S.OUTLINE_SHAPE)
        D.outlined_text(d, (x + S.SPRITE_SIZE / 2, y + S.SPRITE_SIZE / 2), label, 44, S.WHITE)


# Characters a wrapped line must not start or end with (禁則処理).
_NO_LINE_START = "。、）」』】〕》〉！？ー・…‥,.!?)]}"
_NO_LINE_END = "（「『【〔《〈([{"


def wrap_subtitle(text: str, width: int = S.SUBTITLE_WRAP) -> list[str]:
    """Break a subtitle into lines of at most `width` characters.

    Japanese has no spaces, so wrapping is by count, but a break that puts a
    closing mark at the start of a line reads as a typesetting error.
    Explicit newlines in the text are kept.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        remaining = paragraph
        while len(remaining) > width:
            cut = width
            while cut > 1 and (remaining[cut] in _NO_LINE_START or remaining[cut - 1] in _NO_LINE_END):
                cut -= 1
            if cut <= 1:
                cut = width
            lines.append(remaining[:cut])
            remaining = remaining[cut:]
        if remaining or not paragraph:
            lines.append(remaining)
    return lines


def _subtitle_band(img: Image.Image, text: str, speaker: str) -> None:
    if S.THEME == "notebook":
        band = Image.new("RGBA", (S.WIDTH, S.HEIGHT - S.BAND_TOP), (*S.BAND_NAVY, S.BAND_ALPHA_NAVY))
        img.paste(band, (0, S.BAND_TOP), band)
        ImageDraw.Draw(img).line((0, S.BAND_TOP, S.WIDTH, S.BAND_TOP), fill=S.TAB_FILL, width=4)
    else:
        band = Image.new("RGBA", (S.WIDTH, S.HEIGHT - S.BAND_TOP), (255, 255, 255, 150))
        img.paste(band, (0, S.BAND_TOP), band)
    if not text:
        return
    d = ImageDraw.Draw(img)
    colour = S.SPEAKER_COLOURS.get(speaker, S.WHITE)
    lines = wrap_subtitle(text)
    # two lines fit the band at full size; a third only at a smaller one
    size = S.SIZE_SUBTITLE if len(lines) <= 2 else S.SIZE_SUBTITLE_SMALL
    D.outlined_lines(d, (S.WIDTH / 2, S.BAND_TOP + (S.HEIGHT - S.BAND_TOP) / 2),
                     lines, size, colour, width=S.OUTLINE_SUBTITLE, spacing=1.25)


class AssetLibrary:
    """Illustrations and backgrounds on disk, indexed by name.

    Layout:  assets/illustrations/<name>.png   assets/backgrounds/<name>.(png|jpg)
    A `library.yaml` beside them records source and licence per file; that is
    read by the publish stage for credits, not here.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, Image.Image] = {}

    def _find(self, kind: str, name: str) -> Path | None:
        for ext in (".png", ".webp", ".jpg", ".jpeg"):
            p = self.root / kind / f"{name}{ext}"
            if p.exists():
                return p
        return None

    def has(self, name: str) -> bool:
        return self._find("illustrations", name) is not None

    def image(self, name: str) -> Image.Image:
        if name not in self._cache:
            path = self._find("illustrations", name)
            if path is None:
                raise CanvasError(f"no illustration {name!r} under {self.root / 'illustrations'}")
            self._cache[name] = Image.open(path).convert("RGBA")
        return self._cache[name]

    def background(self, name: str) -> Path | None:
        return self._find("backgrounds", name)

    def names(self) -> list[str]:
        folder = self.root / "illustrations"
        if not folder.exists():
            return []
        return sorted({p.stem for p in folder.iterdir() if p.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg")})


class SpriteSet:
    """Character sprites: <root>/<character>/<expression>_<open|closed>.png"""

    def __init__(self, root: Path, characters: dict[str, str]):
        self.root = Path(root)
        self.characters = characters  # role -> folder name
        self._cache: dict[str, Image.Image] = {}

    def _load(self, folder: str, expression: str, mouth_open: bool) -> Image.Image | None:
        key = f"{folder}/{expression}_{'open' if mouth_open else 'closed'}"
        if key in self._cache:
            return self._cache[key]
        for candidate in (key, f"{folder}/{expression}", f"{folder}/normal_closed", f"{folder}/normal"):
            p = self.root / f"{candidate}.png"
            if p.exists():
                im = Image.open(p).convert("RGBA")
                self._cache[key] = im
                return im
        return None

    def has(self, role: str) -> bool:
        folder = self.characters.get(role)
        return bool(folder) and self._load(folder, "normal", False) is not None

    def paste(self, img: Image.Image, expressions: dict[str, str], speaker: str | None, mouth_open: bool) -> None:
        for role in ("explainer", "listener"):
            xy = S.SPRITE_RIGHT if S.SPRITE_SIDE.get(role) == "right" else S.SPRITE_LEFT
            folder = self.characters.get(role)
            talking = role == speaker
            sprite = self._load(folder, expressions.get(role, "normal"), mouth_open and talking) if folder else None
            if sprite is None:
                _placeholder_sprites(img, speaker or "", only=role)
                continue
            x, y = xy
            scale = S.SPRITE_WIDTH / sprite.width
            visible = min(round(sprite.height * scale), S.BAND_TOP + S.SPRITE_CUT - y)
            sprite = sprite.resize((S.SPRITE_WIDTH, round(sprite.height * scale)), Image.LANCZOS)
            sprite = sprite.crop((0, 0, S.SPRITE_WIDTH, visible))
            if role in S.SPRITE_FLIP:
                # both 立ち絵 face the viewer's right; the one on the right is
                # mirrored so the two look at each other across the stage
                sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
            if not talking:
                r, g, b, a = sprite.split()
                sprite = Image.merge("RGBA", (r.point(lambda v: int(v * 0.82)), g.point(lambda v: int(v * 0.82)),
                                              b.point(lambda v: int(v * 0.82)), a))
            img.paste(sprite, (x, y), sprite)
