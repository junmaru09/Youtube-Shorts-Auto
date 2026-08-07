import pytest

from shorts_auto import config
from shorts_auto.config import ConfigError

MINIMAL = {"id": "x", "prompt_template": "shot of {scene}"}


def test_every_shipped_series_parses():
    series = config.load_series(include_disabled=True)
    assert {s.id for s in series} == {
        "survival_moment",
        "micro_camera_doc",
        "miniature_rescue",
        "ai_asmr",
        "retro_japan",
    }


def test_every_shipped_series_has_a_banned_list():
    """The banned list is the policy guardrail injected into every prompt."""
    for entry in config.load_series(include_disabled=True):
        assert entry.banned, f"{entry.id} has no banned list"


def test_every_shipped_series_has_titles_for_its_languages():
    for entry in config.load_series(include_disabled=True):
        for lang in entry.languages:
            assert entry.title_patterns.get(lang), f"{entry.id} has no {lang} title patterns"
            assert entry.hashtags.get(lang), f"{entry.id} has no {lang} hashtags"


def test_language_dependent_series_is_single_language():
    """retro_japan is Japanese-only; shipping it to the English channel is a bug."""
    retro = config.series_by_id("retro_japan")
    assert retro.language_independent is False
    assert retro.languages == ["ja"]


def test_prompt_template_must_have_a_scene_slot():
    with pytest.raises(ConfigError, match="scene"):
        config.parse_series({"id": "x", "prompt_template": "no placeholder here"})


def test_missing_required_key_is_rejected():
    with pytest.raises(ConfigError, match="prompt_template"):
        config.parse_series({"id": "x"})


def test_typo_in_a_key_is_rejected_rather_than_ignored():
    """A silently dropped 'banned' list would ship an unguarded series."""
    with pytest.raises(ConfigError, match="unknown series keys"):
        config.parse_series({**MINIMAL, "bannned": ["oops"]})


def test_channels_cover_every_language_used_by_a_series():
    channel_ids = {c["id"] for c in config.load_channels()["channels"]}
    for entry in config.load_series(include_disabled=True):
        for lang in entry.languages:
            assert lang in channel_ids, f"{entry.id} targets '{lang}' with no such channel"
