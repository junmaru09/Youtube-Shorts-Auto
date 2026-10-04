"""Generated concept art.

This is the only part of the pipeline whose cost scales with how much of it you
use, so most of what is tested here is about money: that a picture cannot be
bought without the guard seeing it, that a bought picture reaches the ledger even
if the step after it fails, and that a failure never leaves a hole in the
timeline.

The API itself is never called.
"""

from __future__ import annotations

import json

import pytest

from tube_auto import db, dedup, imagegen, pricing
from tube_auto.stages import diagrams

MODEL = "gemini-2.5-flash-image"


@pytest.fixture
def images_on(monkeypatch):
    """Turn concept art on, with a fake key so availability checks pass."""
    from tube_auto import config

    def _set(per_video: int = 2, **overrides):
        settings = json.loads(json.dumps(config.load_settings()))
        settings["images"] = {
            "enabled": True, "provider": "gemini", "model": MODEL,
            "concepts_per_video": per_video, **overrides,
        }
        # A 1080p figure is ~120 rendered frames per slot. These tests are about
        # what gets bought and recorded, not about how the picture looks, and at
        # full size the suite takes minutes.
        settings["video"] = {**settings["video"], "width": 160, "height": 90}
        monkeypatch.setattr(config, "load_settings", lambda: settings)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        return settings

    return _set


def _idea_with_diagram_slots(conn, count: int = 4) -> int:
    idea_id = db.insert_idea(
        conn, series_id="black_holes", lang="ja", hook="事象の地平線の内側",
        scene_summary="", tags=[], dedup_key=dedup.make_key("black_holes", "eh"),
    )
    db.set_idea_status(conn, idea_id, "sourced")
    db.upsert_script(
        conn, idea_id=idea_id, char_count=100, model="m", hooks=[],
        chapters=[{"title": f"第{i}章", "visual_intent": "event horizon", "lines": []}
                  for i in range(count)],
    )
    db.replace_assets(conn, idea_id, "diagram", [
        {"path": "", "chapter": i, "order_idx": i, "duration_s": 0.4, "license_ok": True}
        for i in range(count)
    ])
    conn.commit()
    return idea_id


# --- pricing -----------------------------------------------------------------


def test_an_unpriced_model_is_an_error_not_a_free_one():
    """Defaulting to zero would let an unlisted model spend the whole budget
    while the guard reported nothing."""
    with pytest.raises(pricing.UnknownPriceError, match="no price listed"):
        pricing.price_per_image("some-new-image-model")


def test_a_batch_is_priced_before_it_is_spent():
    assert pricing.estimate_image_cost(MODEL, 6) == pytest.approx(0.234)
    assert pricing.estimate_image_cost(MODEL, 0) == 0.0


# --- the prompt --------------------------------------------------------------


def test_every_prompt_forbids_text_logos_and_people():
    """Generated lettering is garbled, and the rights work done on NASA material
    would be pointless if the generated frames reintroduced insignia and faces."""
    prompt = imagegen.build_prompt("accretion disk", {"bg": "#000", "accent": "#FFF"})
    lowered = prompt.lower()
    assert "no text" in lowered
    assert "no logos" in lowered
    assert "no people" in lowered


def test_the_palette_reaches_the_prompt():
    prompt = imagegen.build_prompt("nebula", {"bg": "#05070F", "accent": "#FFD34D"})
    assert "#05070F" in prompt and "#FFD34D" in prompt


def test_a_refusal_is_reported_as_one():
    """A refusal arrives as a normal 200 with text instead of an image."""
    payload = {"candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"text": "no"}]}}]}
    with pytest.raises(imagegen.ImageGenError, match="no image"):
        imagegen._first_image(payload)


def test_a_missing_key_is_its_own_error(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not imagegen.available()
    with pytest.raises(imagegen.ImageGenUnavailable, match="GEMINI_API_KEY"):
        imagegen.generate("x", MODEL, tmp_path / "x.png")


# --- which slots ---------------------------------------------------------------


def test_the_chosen_slots_are_spread_and_stable():
    """Re-rendering after a rejection must ask for the same images, not buy new
    ones."""
    first = diagrams.concept_slots(12, 3)
    assert first == diagrams.concept_slots(12, 3)
    assert len(first) == 3
    assert max(first) - min(first) >= 6


def test_the_budget_caps_how_many_are_bought():
    assert len(diagrams.concept_slots(20, 6)) == 6
    assert len(diagrams.concept_slots(3, 6)) == 3
    assert diagrams.concept_slots(10, 0) == set()


# --- spending ------------------------------------------------------------------


def test_nothing_is_generated_without_a_key(temp_db, temp_work, images_on, monkeypatch):
    """Falling back silently is right: an episode of drawn figures is a complete
    episode."""
    images_on(2)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(imagegen, "generate", lambda *a, **k: called.append(a))
    _idea_with_diagram_slots(temp_db, 2)

    result = diagrams.run(limit=1)
    assert called == []
    assert result.generated == 0
    assert result.spent_usd == 0.0


def test_a_generated_image_reaches_the_ledger(temp_db, temp_work, images_on, monkeypatch):
    images_on(2)
    idea_id = _idea_with_diagram_slots(temp_db, 4)

    def _fake(prompt, model, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_PNG)
        return destination

    monkeypatch.setattr(imagegen, "generate", _fake)
    result = diagrams.run(limit=1)

    assert result.generated == 2
    assert result.spent_usd == pytest.approx(0.078)

    with db.session() as conn:
        rows = conn.execute("SELECT * FROM generations WHERE idea_id = ?", (idea_id,)).fetchall()
        assert len(rows) == 2
        assert sum(r["cost_usd"] for r in rows) == pytest.approx(0.078)
        assert all(r["backend"] == "gemini-image" for r in rows)
        assert all("prompt" in json.loads(r["meta_json"]) for r in rows)


def test_the_monthly_cap_stops_the_spending_not_the_episode(
    temp_db, temp_work, images_on, monkeypatch
):
    """An episode of drawn figures beats no episode, so an exhausted budget
    downgrades rather than fails."""
    settings = images_on(4)
    settings["budget"] = {"monthly_usd": 0.01, "per_run_usd": 0.01}
    _idea_with_diagram_slots(temp_db, 4)

    monkeypatch.setattr(imagegen, "generate", lambda *a, **k: pytest.fail("should not spend"))
    result = diagrams.run(limit=1)

    assert result.generated == 0
    assert result.drawn == 4  # every slot still has a figure
    assert result.failed == 0
    assert any("予算" in note for note in result.errors)


def test_a_generation_failure_falls_back_to_a_drawn_figure(
    temp_db, temp_work, images_on, monkeypatch
):
    images_on(2)
    _idea_with_diagram_slots(temp_db, 4)

    def _boom(*args, **kwargs):
        raise imagegen.ImageGenError("API is down")

    monkeypatch.setattr(imagegen, "generate", _boom)
    result = diagrams.run(limit=1)

    assert result.generated == 0
    assert result.drawn == 4
    assert result.spent_usd == 0.0

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 0
        assert db.unfilled_assets(conn, 1, "diagram") == []


# A real 64x36 PNG. It has to actually decode: ffmpeg fed a corrupt image with
# `-loop 1` retries the broken frame until something kills it, which is what
# `_decodable` upstream now prevents.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000040000000240802000000b66eab"
    "c500000009704859730000000100000001004f25c4d60000004c49444154789c"
    "edcf010900401084c06d7ed53f86c80b0618b79d3c5ed0803c5ed0803c5ed080"
    "3c5ed0803c5ed0803c5ed0803c5ed0803c5ed0803c5ed0803c5ed0803c5ed080"
    "3c5ef0f7c0030eee773d5b6b4d580000000049454e44ae426082"
)
