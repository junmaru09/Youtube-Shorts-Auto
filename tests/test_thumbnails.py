"""Thumbnail copy and layout.

The drawing is checked by looking at the output; that is not something a test
can do. What is tested here is the text selection, which is where every mistake
so far has come from — a truncated word, a fraction read as a fact, a line too
long to survive being shrunk to feed size.
"""

from __future__ import annotations

import pytest

from tube_auto.stages import thumbnails as T
from tests.conftest import needs_ffmpeg

TITLE = "ブラックホールの常識が変わった話"
SCRIPT = (
    "いて座エースターの質量は太陽のおよそ400万倍です。"
    "事象の地平線の半径は約1200万キロメートル。"
    "環の質量は月の3000分の1しかありません。"
)
HOOKS = ["ブラックホールは、本当に「穴」なのでしょうか？"]


# --- the character budget ----------------------------------------------------


@pytest.mark.parametrize(
    "title,hooks,script",
    [
        (TITLE, HOOKS, SCRIPT),
        ("観測でわかった系外惑星の正体", ["地球によく似た惑星が、40光年先で見つかりました。"], "約290ケルビンです。"),
        ("いま分かっている範囲での暗黒物質の正体", [], "宇宙の27パーセントを占めます。"),
        ("生命はどこから来たのか", [], "20種類以上のアミノ酸。"),
        ("土星の環はなぜ消えるのか", ["環はいつ生まれたのでしょうか？"], "毎秒10トンの氷。"),
        ("はやぶさ2が持ち帰ったもの", [], "5.4グラムの試料。"),
    ],
)
def test_no_line_ever_exceeds_the_readable_width(title, hooks, script):
    """The whole design rests on this. A line that has to be shrunk to fit is a
    line nobody reads in a feed, which is the only place the thumbnail is seen."""
    for text in T.choose_text(title, hooks, script).values():
        lines = T.wrap_for_thumbnail(text)
        assert lines
        assert len(lines) <= T.MAX_LINES
        for line in lines:
            assert len(line) <= T.MAX_CHARS_PER_LINE, (text, lines)


def test_all_three_variants_always_exist():
    """Test & Compare takes three. A dropped variant silently turns the
    experiment into a two-way test."""
    copy = T.choose_text("宇宙", [], "")
    assert set(copy) == set(T.VARIANTS)
    assert all(text.strip() for text in copy.values())


# --- picking the number ------------------------------------------------------


def test_the_biggest_figure_wins_by_magnitude_not_digit_count():
    """Counting digits made 1.1倍 tie with 400万倍."""
    assert T._biggest_number("地球の1.1倍で、太陽の400万倍です。") == "400万倍"


def test_a_fraction_is_not_a_fact():
    """「3000分の1」 was read as 「3000分」 — a number that means nothing and a unit
    that is not the one in the sentence."""
    assert T._biggest_number("環の質量は月の3000分の1です。") is None


def test_small_true_numbers_are_skipped():
    assert T._biggest_number("わずか3個の望遠鏡が使われました。") is None


def test_a_bare_number_with_no_unit_is_skipped():
    assert T._biggest_number("これには3つの理由があります。") is None


def test_a_figure_too_long_to_read_is_skipped():
    assert T._biggest_number("距離は123456789キロメートルです。") is None


# --- the question ------------------------------------------------------------


def test_the_question_comes_from_the_end_of_the_hook():
    """The question lives in the last clause; the first one carries the subject,
    which the other two variants already say."""
    assert T._question(HOOKS, TITLE) == "本当に「穴」？"


def test_the_polite_ending_is_stripped_completely():
    """「なのでしょうか」 left a trailing 「な」 when only 「のでしょう」 was matched."""
    assert T._question(["それは本当に正しいのでしょうか？"], "") == "それは本当に正しい？"


def test_a_title_with_no_question_still_yields_one():
    copy = T.choose_text("観測でわかった系外惑星の正体", [], "")
    assert copy["question"].endswith("？")


# --- trimming ----------------------------------------------------------------


def test_shortening_drops_the_modifier_not_the_head_noun():
    """Cutting the tail off 「観測でわかった系外惑星の正体」 gives 「…の正」."""
    assert T._trim_head("観測でわかった系外惑星の正体", 10) == "系外惑星の正体"


def test_the_topic_is_the_longest_noun_run_not_the_first():
    """「いま分かっている範囲での暗黒物質の正体」 leads with 範囲, and the episode is
    not about 範囲."""
    assert T._topic("いま分かっている範囲での暗黒物質の正体", 13) == "暗黒物質"
    assert T._topic(TITLE, 13) == "ブラックホール"


def test_a_title_that_opens_with_a_particle_character_is_not_cut_to_nothing():
    """「はやぶさ」 starts with 「は」, and splitting on particles from index 0 left
    an empty string."""
    assert T._topic("はやぶさ2が持ち帰ったもの", 13) == "はやぶさ2"


# --- line breaking -----------------------------------------------------------


def test_a_break_prefers_a_particle_boundary():
    assert T.wrap_for_thumbnail("土星の環はなぜ消えるのか") == ["土星の環は", "なぜ消えるのか"]


def test_a_break_avoids_splitting_a_kanji_compound():
    """Breaking after 系 in 系外惑星 reads as a typo."""
    lines = T.wrap_for_thumbnail("観測でわかった系外惑星の正体")
    assert not lines[0].endswith("系")


def test_short_copy_is_left_on_one_line():
    assert T.wrap_for_thumbnail("1200万キロ") == ["1200万キロ"]


# --- drawing -----------------------------------------------------------------


@needs_ffmpeg
def test_three_files_come_out_at_the_size_youtube_wants(temp_work):
    from tube_auto import ffmpeg

    made = T.build(7, TITLE, HOOKS, SCRIPT, assets=[])
    assert len(made) == 3
    for path in made:
        assert path.exists()
        assert path.stat().st_size <= T.MAX_BYTES
        info = ffmpeg.video_info(path)
        assert (info["width"], info["height"]) == (T.WIDTH, T.HEIGHT)


@needs_ffmpeg
def test_it_still_draws_when_there_is_no_usable_still(temp_work):
    """Theory themes can end up with nothing but generated diagrams, and a
    thumbnail is worth more than a matching background."""
    made = T.build(8, "重力波とは何か", [], "", assets=[
        {"kind": "still", "path": "/nonexistent.jpg", "license_ok": 1},
        {"kind": "still", "path": "/also-missing.jpg", "license_ok": 0},
    ])
    assert all(path.exists() for path in made)


def test_an_uncleared_still_is_never_used_as_a_background(tmp_path):
    """The thumbnail is the most-published frame of the whole video."""
    blocked = tmp_path / "blocked.jpg"
    blocked.write_bytes(b"x")
    assets = [{"kind": "still", "path": str(blocked), "license_ok": 0}]
    assert T._pick_background(assets, 0) is None
