"""The citation gate.

This is the check that turns "facts come from the sources" from an instruction
in a prompt into something a script cannot ship without satisfying. It is also
the channel's only durable advantage over the summary-rewriting channels, so it
gets tested harder than its size suggests.
"""

import pytest
from tube_auto import citations


def _line(display, refs=()):
    return {"speaker": "explainer", "display": display, "spoken": display, "refs": list(refs)}


def _script(*lines):
    return [{"title": "章", "lines": list(lines)}]


# --- what counts as a claim ---------------------------------------------------


def test_a_measured_distance_needs_a_source():
    assert citations.carries_a_claim("この銀河は地球から約2500万光年の距離にあります。")


def test_a_mass_needs_a_source():
    assert citations.carries_a_claim("質量は太陽の65億倍と推定されています。")


def test_a_year_needs_a_source():
    assert citations.carries_a_claim("2019年に初めて撮像されました。")


def test_a_question_does_not():
    assert not citations.carries_a_claim("それはどうやって測ったんですか。")


def test_prose_without_numbers_does_not():
    assert not citations.carries_a_claim("この現象は長いあいだ説明がつかないままでした。")


def test_an_explicitly_uncertain_line_does_not():
    """A line that says the answer is unknown is not asserting a checkable fact,
    and demanding a citation for it would push the model toward false certainty."""
    assert not citations.carries_a_claim(
        "中心で何が起きているのかは、まだ分かっていない部分が多く残っています。"
    )
    assert not citations.carries_a_claim("約100億年前に形成されたという仮説があります。")


# --- the check itself ---------------------------------------------------------


def test_a_cited_claim_passes():
    report = citations.check(
        _script(_line("直径は約12万光年です。", ["S1"])), {"S1"}
    )
    assert report.ok


def test_an_uncited_claim_fails():
    report = citations.check(_script(_line("直径は約12万光年です。")), {"S1"})
    assert not report.ok
    assert report.uncited == ["直径は約12万光年です。"]


def test_a_reference_to_a_nonexistent_source_fails():
    """The model inventing S9 is exactly the failure the whole gate exists for."""
    report = citations.check(
        _script(_line("直径は約12万光年です。", ["S9"])), {"S1", "S2"}
    )
    assert not report.ok
    assert report.unknown_refs == ["S9"]


def test_an_unused_source_is_reported_but_does_not_fail():
    """Worth flagging — it usually means the bundle was not really one topic —
    but it is not a reason to throw away a correct script."""
    report = citations.check(_script(_line("直径は約12万光年です。", ["S1"])), {"S1", "S2"})
    assert report.ok
    assert report.unused_refs == ["S2"]


def test_a_script_of_pure_narrative_passes_with_no_citations():
    report = citations.check(
        _script(_line("ここからが本題です。"), _line("順を追って見ていきます。")), set()
    )
    assert report.ok


def test_the_message_names_what_went_wrong():
    report = citations.check(_script(_line("直径は約12万光年です。", ["S9"])), {"S1"})
    described = report.describe()
    assert "S9" in described
    assert "S1" in described  # reported as never cited


# --- the length target ---------------------------------------------------------


def test_the_length_target_is_expressed_as_lines_the_model_can_count():
    """Asked only for 7,200 characters the model wrote 2,566 — a third — because
    a running character total is not something it can track while writing. A
    per-chapter line count is."""
    from tube_auto.stages.script import CHARS_PER_LINE, _chapter_plan, _user_prompt

    plan = _chapter_plan(None, 7200)
    assert all(c["lines"] >= 2 for c in plan)
    assert sum(c["lines"] for c in plan) * CHARS_PER_LINE == pytest.approx(7200, rel=0.05)

    prompt = _user_prompt(
        {"hook": "t", "scene_summary": "q"}, [], plan, 7200
    )
    assert "行数は必ず満たす" in prompt
    assert "hooks は必ず3案" in prompt


def test_the_script_tool_is_strict():
    """Without strict mode `minItems: 3` on hooks is advisory, and the model
    returned zero — accepted by the API, rejected by validate() after the call
    had already been paid for."""
    from tube_auto.stages.script import SCRIPT_TOOL

    assert SCRIPT_TOOL["strict"] is True

    def walk(node):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            for child in node.get("properties", {}).values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(SCRIPT_TOOL["input_schema"])
