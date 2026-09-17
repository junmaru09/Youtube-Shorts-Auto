"""Chapter-by-chapter script generation against a fake model: retries with
feedback, degradation instead of rejection, and the preview sheet."""

from dataclasses import dataclass, field

import pytest

from tube_auto.stages import script as script_stage


@dataclass
class FakeResponse:
    payload: dict
    model: str = "fake"
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.01
    stop_reason: str = "tool_use"
    raw_text: str = ""


@dataclass
class FakeClient:
    """Answers each call from a queue keyed by chapter key; remembers prompts."""

    answers: dict[str, list[dict]]
    model: str = "fake"
    calls: list[str] = field(default_factory=list)

    def call_tool(self, *, system, user, tool, max_tokens=None, temperature=None):
        self.calls.append(user)
        key = user.split("いま書く章: ")[1].split("「")[0]
        queue = self.answers[key]
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse(payload=payload)


def _line(speaker, display, visual, refs=()):
    return {"speaker": speaker, "display": display, "spoken": display, "refs": list(refs),
            "visual": visual, "expression": "normal"}


def _chapter(key, lines, hooks=()):
    return {"key": key, "title": key, "visual_intent": "", "hooks": list(hooks), "lines": lines}


PLAN = [
    {"key": "opener", "title": "雑談", "covers": "c", "visual": "room", "lines": 3, "chars": 100, "ends_with_question": True},
    {"key": "context", "title": "背景", "covers": "c", "visual": "", "lines": 3, "chars": 100, "ends_with_question": False},
]

GOOD_OPENER = _chapter("opener", [
    _line("listener", "海って青いわよね", ["clear", "background room"]),
    _line("explainer", "水は透明なのだ", ["label 透明 at=top"]),
    _line("listener", "じゃあなぜ青いの？", ["hold"]),
], hooks=["a", "b", "c"])
GOOD_CONTEXT = _chapter("context", [
    _line("explainer", "太陽の光は", ["clear", "sun slot=sky name=sun"]),
    _line("explainer", "地面に当たるのだ", ["earth_arc slot=floor name=earth"]),
    _line("listener", "なるほどね", ["arrow from=sun to=earth.top"]),
])


def test_clean_chapters_are_accepted_first_time():
    client = FakeClient({"opener": [GOOD_OPENER], "context": [GOOD_CONTEXT]})
    script, notes, spent = script_stage.write_chapters(client, ["sys"], PLAN, set(), idea_id=1)
    assert script is not None and notes == []
    assert [c.key for c in script.chapters] == ["opener", "context"]
    assert script.hooks == ["a", "b", "c"]
    assert len(client.calls) == 2
    assert script.chapters[1].lines[2].ops[0]["op"] == "arrow"


def test_a_bad_chapter_is_retried_with_feedback_then_fixed():
    bad = _chapter("context", [
        _line("explainer", "太陽の光は", ["clear", "sun slot=sky name=sun"]),
        _line("explainer", "地面に", ["arrow from=sun to=ghost"]),      # ghost was never placed
        _line("listener", "なるほど", ["hold"]),
    ])
    client = FakeClient({"opener": [GOOD_OPENER], "context": [bad, GOOD_CONTEXT]})
    script, notes, _ = script_stage.write_chapters(client, ["sys"], PLAN, set(), idea_id=1, rounds=2)
    assert script is not None and notes == []
    assert len(client.calls) == 3
    assert "ghost" in client.calls[2]            # the problem was fed back


def test_out_of_rounds_degrades_the_broken_line_and_keeps_the_chapter():
    bad = _chapter("context", [
        _line("explainer", "太陽の光は", ["clear", "sun slot=sky name=sun"]),
        _line("explainer", "地面に", ["arrow from=sun to=ghost"]),
        _line("listener", "なるほど", ["label text=x at=top"]),
    ])
    client = FakeClient({"opener": [GOOD_OPENER], "context": [bad]})
    script, notes, _ = script_stage.write_chapters(client, ["sys"], PLAN, set(), idea_id=1, rounds=2)
    assert script is not None
    assert notes and "context" in notes[0] and "1" in notes[0]
    lines = script.chapters[1].lines
    assert lines[1].ops == [{"op": "hold"}]            # the broken visual was dropped
    assert lines[2].ops[0]["op"] == "label"            # the good one survived


def test_a_chapter_with_no_lines_fails_the_script():
    client = FakeClient({"opener": [GOOD_OPENER], "context": [_chapter("context", [])]})
    script, notes, _ = script_stage.write_chapters(client, ["sys"], PLAN, set(), idea_id=1, rounds=1)
    assert script is None and "context" in notes[0]


def test_the_stage_persists_across_chapters():
    """Chapter two may point at what chapter one left standing."""
    context = _chapter("context", [
        _line("explainer", "さっきの太陽だが", ["clear keep_heading=true", "sun slot=sky name=sun"]),
        _line("explainer", "ここに", ["earth_arc slot=floor name=earth"]),
        _line("listener", "ええ", ["arrow from=sun to=earth"]),
    ])
    client = FakeClient({"opener": [GOOD_OPENER], "context": [context]})
    script, notes, _ = script_stage.write_chapters(client, ["sys"], PLAN, set(), idea_id=1)
    assert script is not None and notes == []
    assert "clear する前の板にあるもの" not in client.calls[0]   # nothing on stage at the start


def test_preview_sheet_is_written(tmp_path, monkeypatch):
    from tube_auto import paths

    monkeypatch.setattr(paths, "WORK_DIR", tmp_path)
    client = FakeClient({"opener": [GOOD_OPENER], "context": [GOOD_CONTEXT]})
    script, _, _ = script_stage.write_chapters(client, ["sys"], PLAN, set(), idea_id=7)
    path = script_stage.write_preview(script, 7)
    assert path is not None and path.exists() and path.name == "script_idea00007_preview.png"


def test_opener_must_start_plain_and_with_the_listener():
    from tube_auto.canvas import Canvas
    from tube_auto.models import Chapter, Line

    def ch(first_speaker, first_text):
        return Chapter(key="opener", title="t", lines=[
            Line(first_speaker, first_text, first_text, visual=["clear"]),
            Line("explainer", "実は違うのだ", "実は違うのだ", visual=["label x at=top"]),
            Line("listener", "どういうこと？", "どういうこと？", visual=["hold"]),
        ])
    brief = {"key": "opener", "lines": 3, "ends_with_question": True}
    assert not script_stage.check_chapter(ch("listener", "昨日、海に行ったの"), brief, Canvas(), set())
    problems = script_stage.check_chapter(ch("explainer", "ハッブル定数の話なのだ"), brief, Canvas(), set())
    assert any("listener" in p for p in problems)
    problems = script_stage.check_chapter(ch("listener", "ダークエネルギーって知ってる？"), brief, Canvas(), set())
    assert any("専門語" in p for p in problems)
    assert not script_stage.check_chapter(ch("listener", "コンビニでアイスクリーム買ったの"), brief, Canvas(), set())


def test_each_chapter_needs_its_kind_of_figure_and_the_persona_holds():
    from tube_auto.canvas import Canvas
    from tube_auto.models import Chapter, Line

    def ch(visuals, endings="のだ"):
        lines = [Line("explainer", f"説明{i}{endings}", "", visual=v) for i, v in enumerate(visuals)]
        lines[0].visual = ["clear"] + lines[0].visual
        return Chapter(key="mechanism1", title="t", lines=lines)
    brief = {"key": "mechanism1", "lines": 5}
    only_labels = ch([["label a at=top"], ["label b at=bottom"], ["arrow from=a to=b"], ["label c at=left"], ["hold"]])
    problems = script_stage.check_chapter(only_labels, brief, Canvas(), set())
    assert any("図の型" in p for p in problems)
    with_chain = ch([["chain name=c nodes=甲:0.2:0.2|乙:0.8:0.8 edges=0-1"], ["label a at=top"], ["hold"], ["label c at=left"], ["hold"]])
    assert not script_stage.check_chapter(with_chain, brief, Canvas(), set())
    plain = ch([["chain name=c nodes=甲:0.2:0.2|乙:0.8:0.8 edges=0-1"], ["label a at=top"], ["hold"], ["label c at=left"], ["hold"]], endings="です")
    problems = script_stage.check_chapter(plain, brief, Canvas(), set())
    assert any("語尾" in p for p in problems)


def test_the_best_attempt_is_kept_not_the_last():
    """Round one: 27 good lines with one bad reference. Round two: a stub.
    Keep round one, degraded."""
    from tube_auto.canvas import Canvas

    plan = [{"key": "context", "title": "背景", "covers": "c", "visual": "", "lines": 6, "chars": 100}]
    good = _chapter("context", [
        _line("explainer", "太陽の光は", ["clear", "sun slot=sky name=sun"]),
        _line("explainer", "地面に当たる", ["earth_arc slot=floor name=earth"]),
        _line("listener", "それで？", ["arrow from=sun to=ghost"]),
        _line("explainer", "跳ね返るのだ", ["arrow from=sun to=earth.top"]),
        _line("explainer", "つまり寒くなるのだ", ["label 寒い at=top"]),
        _line("listener", "なるほど", ["hold"]),
    ])
    stub = _chapter("context", [_line("explainer", "placeholder", ["clear"])])
    client = FakeClient({"context": [good, stub, stub]})
    chapter, problems, _ = script_stage.write_chapter(client, ["sys"], plan, 0, [], Canvas(), set(), idea_id=1, rounds=2)
    assert chapter is not None and len(chapter.lines) == 6
    assert any("ghost" in p for p in problems)
    assert chapter.lines[2].broken and not chapter.lines[3].broken
    assert len(client.calls) == 3            # the stub bought one extra round


def test_model_habits_resolve():
    from tube_auto.canvas import Canvas, dsl

    c = Canvas()
    for l in ["chain nodes=甲:0.2:0.2|乙:0.8:0.8 edges=0-1", "highlight target=chain",
              "table columns=A|B rows=速い|遅い;近い|遠い", "highlight target=table",
              "label text=x at=遅い", "label text=y at=r2c2 side=below",
              "scatter slot=left name=sc points=0.1:0.2|0.9:0.8", "label text=近い at=sc.p1",
              "concept slot=right text=密 name=dense", "label text=濃い at=dense below"]:
        c.apply(dsl.parse(l))
    assert c.state.items[-1].props["at"] == "dense"


def test_illustrations_place_by_name_and_shadow_slot_names():
    from tube_auto import paths
    from tube_auto.canvas import AssetLibrary, Canvas, dsl

    c = Canvas(assets=AssetLibrary(paths.ASSETS_DIR))
    for l in ["background room", "ruler slot=left name=r1", "label text=物差し at=r1 side=below",
              "sun_icon slot=top-right name=sky size=small", "label text=空 at=sky side=below"]:
        c.apply(dsl.parse(l))
    ruler = c.state.find("r1")
    assert ruler.props["element"] == "ruler" and ruler.bounds[2] - ruler.bounds[0] <= 400
    sky, label = c.state.find("sky"), c.state.items[-1]
    assert label.box[1] > sky.bounds[3]                  # under the sun, not under the sky slot
    assert abs((label.box[0] + label.box[2]) / 2 - (sky.bounds[0] + sky.bounds[2]) / 2) < 2


def test_concept_does_not_count_as_a_placed_figure():
    from tube_auto.canvas import Canvas
    from tube_auto.models import Chapter, Line

    lines = [Line("explainer", f"説明{i}のだ", "", visual=["concept slot=center text=言葉"]) for i in range(12)]
    lines[0].visual = ["clear"] + lines[0].visual
    chapter = Chapter(key="mechanism1", title="t", lines=lines)
    problems = script_stage.check_chapter(chapter, {"key": "mechanism1", "lines": 12}, Canvas(), set())
    assert any("要素を置く操作" in p for p in problems)
