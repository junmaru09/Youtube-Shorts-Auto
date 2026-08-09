import pytest

from shorts_auto import config
from shorts_auto.config import ConfigError

MINIMAL = {
    "id": "x",
    "prompt_template": "{cinematography}. {subject}, {action}, {context}.\n{style}\nAudio: {audio}",
    "negative_prompt": "watermark",
    "languages": ["ja"],
    "title_patterns": {"ja": ["何かの話"]},
    "hashtags": {"ja": ["#shorts"]},
}

# Phrases that assert the operator personally filmed something that was in fact
# generated. YouTube treats that as misleading metadata, and two of the shipped
# series carried it in their own title patterns while banning it in prose.
FIRST_PERSON_CLAIMS = (
    "撮影した",
    "撮ってみた",
    "入れてみた",
    "付けてみた",
    "設置した",
    "I filmed",
    "I mounted",
    "I installed",
    "I recorded",
    "real footage",
)


def test_every_shipped_series_parses():
    series = config.load_series(include_disabled=True)
    assert {s.id for s in series} == {
        "survival_moment",
        "micro_camera_doc",
        "miniature_rescue",
        "ai_asmr",
        "retro_japan",
    }


def test_every_shipped_series_has_a_banned_list_and_a_negative_prompt():
    """`banned` guides the ideation model in Japanese; the video model only ever
    sees `negative_prompt`, in English. Both are needed."""
    for entry in config.load_series(include_disabled=True):
        assert entry.banned, f"{entry.id} has no banned list"
        assert entry.negative_prompt.strip(), f"{entry.id} has no negative_prompt"


def test_titles_never_claim_the_footage_is_real():
    """The defect this locks down: micro_camera_doc banned factual assertions in
    prose while its own titles said "I mounted a micro camera on a ...", about
    footage that is entirely synthetic."""
    for entry in config.load_series(include_disabled=True):
        for lang, patterns in entry.title_patterns.items():
            for pattern in patterns:
                for claim in FIRST_PERSON_CLAIMS:
                    assert claim not in pattern, (
                        f"{entry.id} [{lang}] title {pattern!r} asserts real footage"
                    )


def test_negative_prompts_suppress_speech():
    """Veo generates dialogue as readily as ambience, and a video with English
    speech cannot be posted to the Japanese channel."""
    merged = config.load_settings()["video"]["negative_prompt"]
    for term in ("speech", "dialogue", "narration"):
        assert term in merged


def test_series_negative_prompt_is_merged_with_the_global_one():
    series = config.series_by_id("ai_asmr")
    merged = config.negative_prompt_for(series)
    assert "watermark" in merged        # from settings.yaml
    assert "glass fruit" in merged      # from the series
    assert merged.count("blood") == 1, "duplicate terms should collapse"


def test_every_shipped_series_has_titles_and_tags_for_its_languages():
    for entry in config.load_series(include_disabled=True):
        for lang in entry.languages:
            assert entry.title_patterns.get(lang), f"{entry.id} has no {lang} title patterns"
            assert entry.hashtags.get(lang), f"{entry.id} has no {lang} hashtags"


def test_arms_are_series_language_pairs():
    arms = config.arms(include_disabled=True)
    keys = {arm.key for arm in arms}
    assert "micro_camera_doc:ja" in keys
    assert "micro_camera_doc:en" in keys
    # Japanese-only series must not produce an English arm.
    assert "retro_japan:en" not in keys
    assert "survival_moment:en" not in keys


def test_prompt_template_must_have_every_slot():
    broken = {**MINIMAL, "prompt_template": "{subject} does {action}"}
    with pytest.raises(ConfigError, match="missing slots"):
        config.parse_series(broken)


def test_missing_required_key_is_rejected():
    with pytest.raises(ConfigError, match="negative_prompt"):
        config.parse_series({k: v for k, v in MINIMAL.items() if k != "negative_prompt"})


def test_typo_in_a_key_is_rejected_rather_than_ignored():
    """A silently dropped 'banned' list would ship an unguarded series."""
    with pytest.raises(ConfigError, match="unknown series keys"):
        config.parse_series({**MINIMAL, "bannned": ["oops"]})


def test_a_language_without_title_patterns_is_rejected():
    broken = {**MINIMAL, "languages": ["ja", "en"]}
    with pytest.raises(ConfigError, match="no title_patterns for language 'en'"):
        config.parse_series(broken)


def test_shipped_config_is_internally_consistent():
    assert config.validate_all() == []


def test_validate_all_catches_an_unpublishable_backlog(monkeypatch):
    """Planning more per day than can be uploaded silently accumulates videos
    that were paid for and never seen."""
    settings = dict(config.load_settings())
    settings["pipeline"] = {"ideas_per_day": 6, "publish_per_day": 3}
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    problems = config.validate_all()
    assert any("exceeds publish_per_day" in p for p in problems)


def test_validate_all_catches_a_quota_busting_publish_rate(monkeypatch):
    settings = dict(config.load_settings())
    settings["pipeline"] = {"ideas_per_day": 3, "publish_per_day": 10}
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    problems = config.validate_all()
    assert any("6/day" in p for p in problems)
