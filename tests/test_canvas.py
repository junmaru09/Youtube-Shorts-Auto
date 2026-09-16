"""The whiteboard canvas: operations, slots, parts, and the drawing rules
that were wrong once (arrow fills, missing glyphs, stacking order)."""

import pytest
from PIL import Image, ImageDraw

from tube_auto.canvas import OPS, SLOTS, Canvas, CanvasError
from tube_auto.canvas import draw as D
from tube_auto.canvas import elements as E
from tube_auto.canvas import style as S


def _canvas(*ops):
    c = Canvas()
    for op in ops:
        c.apply(op)
    return c


def _pixels(img, colour, box=None):
    """How many pixels in `box` are exactly `colour`."""
    import numpy as np

    region = (img.crop(box) if box else img).convert("RGB")
    arr = np.asarray(region)
    return int(np.all(arr == np.array(colour, dtype=arr.dtype), axis=-1).sum())


# --- operations ----------------------------------------------------------------


def test_every_op_has_a_handler():
    for op in OPS:
        assert hasattr(Canvas, f"_op_{op}"), op


def test_unknown_element_and_colour_are_refused():
    with pytest.raises(CanvasError):
        _canvas({"op": "place", "element": "unicorn", "slot": "center"})
    with pytest.raises(CanvasError):
        _canvas({"op": "label", "text": "x", "colour": "mauve"})
    with pytest.raises(CanvasError):
        _canvas({"op": "arrow", "from": "nothing", "to": "center"})


def test_parts_are_available_immediately_after_apply():
    c = _canvas({"op": "place", "element": "earth_arc", "slot": "floor", "name": "earth"},
                {"op": "add", "element": "ice_block", "near": "earth", "name": "ice1"})
    assert "top" in c.state.find("ice1").parts
    # a part reference resolves without a render having happened
    assert c.state.box_of("ice1.top")


def test_ice_rests_on_the_arc_wherever_it_is():
    c = _canvas({"op": "place", "element": "earth_arc", "slot": "floor", "name": "earth"},
                *[{"op": "add", "element": "ice_block", "near": "earth", "name": f"ice{i}"} for i in range(1, 6)])
    x0, apex, x1, edge = c.state.find("earth").parts["surface"]
    for i in range(1, 6):
        b = c.state.find(f"ice{i}").box
        assert apex <= b[3] <= edge + 12, (i, b[3], apex, edge)   # bottom sits on the curve


def test_slots_stay_inside_the_frame_and_above_the_band():
    for name, (x0, y0, x1, y1) in SLOTS.items():
        if name in ("sky", "floor"):
            continue
        assert 0 <= x0 < x1 <= S.WIDTH, name
        assert y1 <= S.BAND_TOP, name


def test_full_width_slots_do_not_reach_the_sprites():
    for name in ("top", "top-left", "top-right"):
        assert SLOTS[name][3] <= S.SPRITE_TOP, name


def test_clear_keeps_background_and_optionally_heading():
    c = _canvas({"op": "background", "name": "space"}, {"op": "heading", "text": "H"},
                {"op": "label", "text": "x"}, {"op": "clear", "keep_heading": True})
    assert c.state.background == "space"
    assert c.state.heading == "H"
    assert c.state.items == []


def test_dim_is_an_item_so_later_items_draw_bright():
    c = _canvas({"op": "label", "text": "before"}, {"op": "dim"}, {"op": "label", "text": "after"})
    kinds = [i.kind for i in c.state.items]
    assert kinds == ["label", "dim", "label"]


def test_title_with_dim_inserts_a_dim_item():
    c = _canvas({"op": "title", "text": "T", "dim": True})
    assert [i.kind for i in c.state.items] == ["dim"]
    assert c.state.title == "T"


def test_arrow_to_a_direction_and_via_a_corner():
    c = _canvas({"op": "place", "element": "sun", "slot": "sky", "name": "sun"},
                {"op": "place", "element": "earth_arc", "slot": "floor", "name": "earth"},
                {"op": "add", "element": "ice_block", "near": "earth", "name": "ice"},
                {"op": "arrow", "from": "sun", "via": "ice.top", "to": "up-right", "length": 300})
    pts = c.state.items[-1].props["points"]
    assert len(pts) == 3
    corner, end = pts[1], pts[2]
    assert end[0] > corner[0] and end[1] < corner[1]          # went up and right
    assert abs((end[0] - corner[0]) - 300 * 0.7071) < 1


def test_arrow_to_a_slot_reaches_its_centre():
    c = _canvas({"op": "label", "text": "x", "at": "top-left", "name": "a"},
                {"op": "arrow", "from": "a", "to": "bottom-right"})
    end = c.state.items[-1].props["points"][-1]
    x0, y0, x1, y1 = SLOTS["bottom-right"]
    assert abs(end[0] - (x0 + x1) / 2) < 30 and abs(end[1] - (y0 + y1) / 2) < 30


def test_label_pointer_points_at_the_target():
    c = _canvas({"op": "place", "element": "timeline", "slot": "wide", "name": "tl", "start": "a", "end": "b",
                 "bands": [{"from": 0, "to": 1, "colour": "cyan", "name": "all"}]},
                {"op": "label", "text": "氷期", "at": "tl.all", "side": "above", "pointer": True})
    label = c.state.items[-1]
    p0, p1 = label.props["pointer"]
    band = c.state.find("tl").parts["all"]
    assert label.box[3] < band[1]                # label is above the band
    assert p1[1] > p0[1] and p1[1] <= band[1] + 2   # pointer goes down to the band's top


# --- drawing --------------------------------------------------------------------


def test_arrow_has_a_fill_not_just_an_outline():
    img = Image.new("RGB", (400, 100), S.PARCHMENT_BASE)
    D.arrow(ImageDraw.Draw(img), (20, 50), (380, 50), S.WHITE)
    assert _pixels(img, S.WHITE) > 300 * S.ARROW_WIDTH * 0.8
    assert _pixels(img, S.INK) > 0


def test_double_arrow_has_no_seam():
    img = Image.new("RGB", (600, 100), S.PARCHMENT_BASE)
    D.double_arrow(ImageDraw.Draw(img), (20, 50), (580, 50), S.WHITE)
    # the middle column is all fill
    assert all(img.getpixel((300, y))[:3] == S.WHITE for y in range(45, 56))


def test_marks_are_drawn_not_typeset():
    img = Image.new("RGB", (900, 120), S.PARCHMENT_BASE)
    D.outlined_text(ImageDraw.Draw(img), (20, 60), "質量あり ➡ 重力 ◎ ×", 48, anchor="lm")
    assert _pixels(img, S.MARK_RING) > 100      # the blue ring
    assert _pixels(img, S.RED) > 100            # the red cross
    assert _pixels(img, S.WHITE) > 2000         # text plus the white arrow


def test_missing_glyphs_fall_back_to_noto():
    # a symbol M PLUS 2 lacks must not render as a tofu box: compare against
    # a character we know is missing from both fonts' notdef
    w1, _ = D.text_size("＋", 48)
    w2, _ = D.text_size("あ", 48)
    assert w1 > 0 and w2 > 0
    runs = D._runs("a＋b")
    assert [k for _, k in runs] == ["text", "symbol", "text"]


def test_render_has_band_and_sprites_and_stage():
    c = _canvas({"op": "place", "element": "pie", "slot": "center", "name": "p",
                 "slices": [{"label": "A", "value": 30, "colour": "cyan"}, {"label": "B", "value": 70, "colour": "magenta"}]})
    img = c.render(subtitle="テスト", speaker="listener")
    assert img.size == (S.WIDTH, S.HEIGHT)
    assert _pixels(img, S.CYAN, (S.STAGE_LEFT, S.STAGE_TOP, S.STAGE_RIGHT, S.STAGE_BOTTOM)) > 5000
    assert _pixels(img, S.MAGENTA, (S.STAGE_LEFT, S.STAGE_TOP, S.STAGE_RIGHT, S.STAGE_BOTTOM)) > 10000


def test_list_is_not_dimmed_by_title():
    c = _canvas({"op": "list_add", "text": "太陽光の強さ"}, {"op": "title", "text": "T", "dim": True})
    img = c.render_stage()
    # the list's white text is drawn after the dim, so pure white survives
    assert _pixels(img, S.WHITE, (S.LIST_X, S.LIST_Y, S.LIST_X + 400, S.LIST_Y + 60)) > 200


def test_registry_elements_all_draw_and_return_self():
    img = Image.new("RGB", (S.WIDTH, S.HEIGHT))
    d = ImageDraw.Draw(img)
    box = SLOTS["center"]
    samples = {
        "pie": {"slices": [{"label": "A", "value": 1, "colour": "cyan"}]},
        "box_row": {"items": ["a", "b"]},
        "timeline": {"start": "a", "end": "b"},
        "table": {"columns": ["a"], "rows": [["x ➡ y ◎"]]},
        "chain": {"nodes": [{"text": "a", "x": 0.2, "y": 0.2}, {"text": "b", "x": 0.8, "y": 0.8}], "edges": [(0, 1)]},
        "columns": {"items": [{"title": "a", "colour": "yellow", "text": "b\nc"}]},
        "number_line": {"markers": [{"at": 0.5, "label": "m"}], "span": [0.2, 0.8], "span_label": "s"},
    }
    for name, fn in E.REGISTRY.items():
        parts = fn(d, box, **samples.get(name, {}))
        assert "self" in parts, name
