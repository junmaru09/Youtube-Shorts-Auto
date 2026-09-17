"""The script's visual DSL and the structural rules that reject a script
before narration is paid for."""

import pytest

from tube_auto.canvas import Canvas, CanvasError, dsl
from tube_auto.models import Chapter, Line, Script
from tube_auto.stages import script as script_stage


def _line(speaker="explainer", display="…なのだ", visual=None, refs=()):
    return Line(speaker=speaker, display=display, spoken=display, refs=list(refs), visual=visual or ["hold"])


def _script(*chapters):
    return Script(chapters=list(chapters), hooks=["a", "b", "c"])


# --- DSL ------------------------------------------------------------------------


def test_element_name_is_shorthand_for_place():
    assert dsl.parse("sun slot=sky name=sun") == {"op": "place", "element": "sun", "slot": "sky", "name": "sun"}


def test_lists_items_and_rows_parse():
    op = dsl.parse("pie slot=center name=p slices=A:5:white|B:95:cyan:見えない/謎")
    assert op["slices"][1] == {"label": "B", "value": 95, "colour": "cyan", "note": "見えない\n謎"}
    op = dsl.parse("table name=t columns=X|Y rows=a|b;c|d")
    assert op["rows"] == [["a", "b"], ["c", "d"]]
    op = dsl.parse("chain name=c nodes=甲:0.1:0.2|乙:0.8:0.9:pink edges=0-1")
    assert op["edges"] == [(0, 1)] and op["nodes"][1]["colour"] == "pink"
    op = dsl.parse("number_line name=n span=0.2:0.8")
    assert op["span"] == [0.2, 0.8]


def test_text_values_keep_spaces_and_symbols():
    op = dsl.parse("heading text=重力の原因 ＝ 質量による時間の遅れ colour=yellow align=left")
    assert op["text"] == "重力の原因 ＝ 質量による時間の遅れ"
    assert op["colour"] == "yellow"


def test_unknown_op_and_bad_syntax_are_dsl_errors():
    with pytest.raises(dsl.DSLError):
        dsl.parse("frobnicate x=1")
    with pytest.raises(dsl.DSLError):
        dsl.parse("arrow sun")                    # arrow has no positional argument
    with pytest.raises(dsl.DSLError):
        dsl.parse("chain nodes=a:0:0 edges=x-y")


def test_the_forms_the_model_actually_writes_parse():
    """Seen in a rejected script: the op given as a key, a bare value, and
    the value repeated as its own key."""
    assert dsl.parse("background=space") == {"op": "background", "name": "space"}
    assert dsl.parse("background space") == {"op": "background", "name": "space"}
    assert dsl.parse("background room=room") == {"op": "background", "name": "room"}
    assert dsl.parse("label 正のフィードバック at=top") == {"op": "label", "text": "正のフィードバック", "at": "top"}


def test_every_item_has_edge_parts():
    c = Canvas()
    c.apply(dsl.parse("galaxy slot=center name=g1"))
    x0, y0, x1, y1 = c.state.find("g1").box
    top = c.state.box_of("g1.top")
    assert abs((top[0] + top[2]) / 2 - (x0 + x1) / 2) < 1 and abs(top[1] + 8 - y0) < 1
    with pytest.raises(CanvasError):
        c.state.box_of("g1.nucleus")


def test_missing_arguments_become_canvas_errors():
    with pytest.raises(CanvasError):
        Canvas().apply(dsl.parse("arrow to=center"))


# --- visuals check -----------------------------------------------------------------


def test_broken_reference_names_the_line():
    chapter = Chapter(key="mechanism1", title="t", lines=[
        _line(visual=["clear", "sun slot=sky name=sun"]),
        _line(visual=["arrow from=sun to=ice3.top"]),
    ])
    problems = script_stage.check_visuals(_script(chapter))
    assert len(problems) == 1
    assert "mechanism1:2" in problems[0] and "ice3" in problems[0]


def test_ops_are_populated_for_storage():
    chapter = Chapter(key="k", title="t", lines=[_line(visual=["clear", "sun slot=sky name=sun"])])
    assert not script_stage.check_visuals(_script(chapter))
    assert chapter.lines[0].ops[1]["element"] == "sun"


def test_four_static_lines_are_rejected_outside_the_room():
    chapter = Chapter(key="k", title="t", lines=[
        _line(visual=["clear"]), _line(), _line(), _line(), _line(),
    ])
    problems = script_stage.check_visuals(_script(chapter))
    assert any("動いていない" in p for p in problems)


def test_three_static_lines_are_fine_and_the_room_allows_five():
    chapter = Chapter(key="k", title="t", lines=[
        _line(visual=["clear"]), _line(), _line(), _line(), _line(visual=["label text=x"]), _line(), _line(),
    ])
    assert not script_stage.check_visuals(_script(chapter))
    room = Chapter(key="opener", title="t", lines=[_line(visual=["clear"])] + [_line()] * 5)
    assert not script_stage.check_visuals(_script(room))
    room = Chapter(key="opener", title="t", lines=[_line(visual=["clear"])] + [_line()] * 6)
    assert script_stage.check_visuals(_script(room))


# --- structure check ---------------------------------------------------------------

PLAN = [{"key": "opener", "ends_with_question": True}, {"key": "context", "ends_with_question": False}]


def _balanced(key, n=8, question=False):
    lines = [_line(visual=["clear"])]
    for i in range(1, n):
        lines.append(_line(speaker="listener" if i % 3 == 0 else "explainer", visual=["label text=x"]))
    if question:
        lines[-1].display = "本当なの？"
    return Chapter(key=key, title="t", lines=lines)


def test_listener_share_is_bounded():
    mono = Chapter(key="context", title="t", lines=[_line(visual=["clear"])] + [_line(visual=["label text=x"])] * 9)
    problems = script_stage.check_structure(_script(mono), PLAN)
    assert any("listener" in p for p in problems)
    duet = Chapter(key="context", title="t", lines=[_line(visual=["clear"])] + [_line(speaker="listener")] * 9)
    problems = script_stage.check_structure(_script(duet), PLAN)
    assert any("多すぎる" in p for p in problems)


def test_flagged_chapters_must_end_with_a_question():
    s = _script(_balanced("opener", question=False), _balanced("context"))
    script_stage.check_visuals(s)
    problems = script_stage.check_structure(s, PLAN)
    assert any("opener" in p and "疑問" in p for p in problems)
    s = _script(_balanced("opener", question=True), _balanced("context"))
    script_stage.check_visuals(s)
    assert not script_stage.check_structure(s, PLAN)


def test_chapters_start_with_clear():
    chapter = _balanced("context")
    chapter.lines[0].visual = ["label text=x"]
    s = _script(_balanced("opener", question=True), chapter)
    script_stage.check_visuals(s)
    problems = script_stage.check_structure(s, PLAN)
    assert any("clear" in p for p in problems)


def test_chapter_plan_carries_visual_hints():
    plan = script_stage._chapter_plan(None, 7200)
    assert [c["key"] for c in plan][:3] == ["opener", "context", "name"]
    assert plan[0]["ends_with_question"] is True
    assert "room" in plan[0]["visual"]
    assert abs(sum(c["chars"] for c in plan) - 7200) < 20
