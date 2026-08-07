"""Stage-level behaviour that does not need network access."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from shorts_auto import db, dedup, pricing
from shorts_auto.stages import analytics, generate, postprocess


def _seed_idea(conn, series_id="micro_camera_doc", scene="a mole tunnelling through soil"):
    return db.insert_idea(
        conn,
        series_id=series_id,
        hook={"ja": "モグラの巣", "en": "Inside a mole tunnel"},
        video_prompt="Raw micro-camera documentary footage. A mole tunnels through soil.",
        scene_summary=scene,
        tags={"ja": ["#生き物"], "en": ["#nature"]},
        dedup_key=dedup.make_key(series_id, scene),
    )


# --- dedup at the database boundary -----------------------------------------


def test_duplicate_dedup_key_is_rejected_by_the_database(temp_db):
    assert _seed_idea(temp_db) is not None
    assert _seed_idea(temp_db) is None, "the UNIQUE constraint should reject the second insert"


# --- generate ----------------------------------------------------------------


def test_generate_writes_an_asset_and_advances_status(temp_db, monkeypatch):
    _seed_idea(temp_db)
    temp_db.commit()

    result = generate.run(limit=5, backend_name="fake")
    assert result.generated == 1
    assert result.failed == 0

    with db.session() as conn:
        idea = conn.execute("SELECT * FROM ideas").fetchone()
        assert idea["status"] == "generated"
        assets = db.assets_for_idea(conn, int(idea["id"]))
        assert len(assets) == 1
        assert assets[0]["lang"] == "src"


def test_generate_is_idempotent(temp_db):
    """Re-running must not re-bill an idea that already has a video."""
    _seed_idea(temp_db)
    temp_db.commit()

    generate.run(limit=5, backend_name="fake")
    second = generate.run(limit=5, backend_name="fake")
    assert second.generated == 0

    with db.session() as conn:
        assert len(conn.execute("SELECT id FROM assets").fetchall()) == 1


def test_generate_stops_when_the_budget_is_gone(temp_db, monkeypatch):
    from shorts_auto import config

    _seed_idea(temp_db, scene="a mole tunnelling through soil")
    _seed_idea(temp_db, scene="a hedgehog entering a leaf nest")
    temp_db.commit()

    settings = dict(config.load_settings())
    settings["budget"] = {"monthly_usd": 0.0, "per_run_usd": 0.0}
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setattr(generate.config, "load_settings", lambda: settings)

    # The real backend prices apply even under --dry-run, so a zero budget bites.
    result = generate.run(limit=5, backend_name="veo")
    assert result.generated == 0
    assert result.skipped_budget == 2
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()["n"] == 0


def test_dry_run_reports_the_real_price_but_charges_nothing(temp_db):
    _seed_idea(temp_db)
    temp_db.commit()

    result = generate.run(limit=1, dry_run=True)
    assert result.spent_usd == 0.0

    with db.session() as conn:
        meta = json.loads(db.assets_for_idea(conn, 1)[0]["meta_json"])
        assert meta["would_have_cost_usd"] == pytest.approx(
            pricing.estimate_cost("veo-3.1-lite-generate-preview", "720p", 8)
        )


# --- pricing -----------------------------------------------------------------


def test_lite_720p_eight_seconds_costs_forty_cents():
    assert pricing.estimate_cost("veo-3.1-lite-generate-preview", "720p", 8) == pytest.approx(0.40)


def test_unknown_model_is_an_error_not_a_silent_zero():
    with pytest.raises(pricing.UnknownPriceError):
        pricing.estimate_cost("veo-9-imaginary", "720p", 8)


# --- title wrapping ----------------------------------------------------------


def test_japanese_titles_wrap_by_character_count():
    wrapped = postprocess.wrap_title("アリの巣の中に超小型カメラを入れてみた", "ja")
    assert "\n" in wrapped
    assert all(len(line) <= 12 for line in wrapped.split("\n"))
    assert wrapped.replace("\n", "") == "アリの巣の中に超小型カメラを入れてみた"


def test_english_titles_wrap_on_word_boundaries():
    wrapped = postprocess.wrap_title("I mounted a micro camera on a leafcutter ant", "en")
    assert "\n" in wrapped
    assert not any(line.startswith(" ") for line in wrapped.split("\n"))


def test_short_title_is_left_alone():
    assert postprocess.wrap_title("短い", "ja") == "短い"


# --- analytics windows -------------------------------------------------------


def _ago(**kwargs) -> str:
    return (datetime.now(UTC) - timedelta(**kwargs)).isoformat(timespec="seconds")


def test_fresh_post_only_gets_the_rolling_snapshot():
    assert analytics.due_windows(_ago(hours=2), set()) == ["latest"]


def test_windows_open_as_the_post_ages():
    due = analytics.due_windows(_ago(days=8), set())
    assert set(due) == {"24h", "72h", "7d", "latest"}


def test_recorded_windows_are_never_overwritten():
    """A 24h snapshot must stay a 24h snapshot, or scoring compares apples to oranges."""
    due = analytics.due_windows(_ago(days=8), {"24h", "72h"})
    assert "24h" not in due
    assert "72h" not in due
    assert set(due) == {"7d", "latest"}
