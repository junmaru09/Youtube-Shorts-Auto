"""Making the narrator say what the script means.

Google's Japanese TTS mis-reads exponents, unit symbols and catalogue names —
exactly what a science channel is made of. These tests pin down which forms the
dictionary resolves and which ones the script model must write out itself,
because the boundary is what the prompt promises the model.
"""

import pytest

from tube_auto import reading


@pytest.fixture(autouse=True)
def _fresh_dictionary():
    reading.reset_cache()
    yield
    reading.reset_cache()


# --- forms the dictionary resolves -------------------------------------------


def test_an_exponent_is_opened_into_words():
    assert "かける10の24" in reading.apply_readings("5.97×10^24 kg")


def test_unit_symbols_become_readable():
    spoken = reading.apply_readings("直径12742 km、速度は7.9 km/s")
    assert "キロメートル" in spoken
    assert "キロメートル毎秒" in spoken


def test_a_percentage_is_spoken():
    assert "パーセント" in reading.apply_readings("大気の約78%は窒素です")


def test_ambiguous_kanji_are_opened():
    """These have more than one reading, and the wrong one is audible."""
    spoken = reading.apply_readings("木星と天王星の離心率")
    assert "もくせい" in spoken
    assert "てんのうせい" in spoken
    assert "りしんりつ" in spoken


def test_catalogue_names_from_the_config_file_are_applied():
    assert "ジェイムズウェッブ" in reading.apply_readings("JWSTの観測で")


def test_a_resolved_sentence_has_nothing_left_to_flag():
    assert reading.check_spoken("地球の質量は約5.97×10^24 kgです。") == []


# --- forms the script model must handle itself --------------------------------


def test_a_fraction_is_left_for_the_model():
    """2/3 could be a fraction, a ratio or a date; the dictionary cannot know."""
    assert "2/3" in reading.check_spoken("太陽質量の約2/3しかない")


def test_an_unknown_latin_word_is_flagged():
    assert reading.check_spoken("Betelgeuse-like stars") != []


def test_plain_japanese_is_never_flagged():
    assert reading.check_spoken("この現象は長いあいだ説明がつきませんでした。") == []


# --- the strict path ----------------------------------------------------------


def test_strict_mode_refuses_rather_than_mispronouncing():
    """Used where nobody will hear the result before it is published."""
    with pytest.raises(reading.UnreadableText, match="2/3"):
        reading.prepare_spoken("太陽質量の約2/3しかない", strict=True)


def test_strict_mode_passes_clean_text_through():
    assert reading.prepare_spoken("直径は約12742 kmです", strict=True).endswith("キロメートルです")


def test_the_error_says_how_to_fix_it():
    with pytest.raises(reading.UnreadableText, match="terms.yaml"):
        reading.prepare_spoken("ratio of 2/3", strict=True)


# --- the dictionary itself ----------------------------------------------------


def test_the_shipped_dictionary_loads():
    terms = reading.load_terms()
    assert terms["kg"] == "キログラム"
    assert "JWST" in terms


def test_longer_keys_win_over_shorter_ones():
    """'光年' must not be resolved as '年' plus a stray character."""
    assert reading.apply_readings("4.2光年") == "4.2こうねん"
