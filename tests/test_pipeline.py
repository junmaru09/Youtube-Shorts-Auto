"""Stage-level behaviour, and a regression test for each defect that was
reproduced during the design review.

Nothing here touches the network. The backends and the YouTube client are
substituted, which is enough because every defect being locked down is in the
bookkeeping around those calls, not in the calls themselves.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shorts_auto import backends, db, dedup, pricing, youtube
from shorts_auto.backends.veo import BilledFailure, SafetyBlocked, VeoBackend
from shorts_auto.models import GeneratedVideo
from shorts_auto.stages import analytics, generate, golive, postprocess, publish

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

SERIES = "micro_camera_doc"


def _seed_idea(conn, scene="a mole tunnelling through soil", lang="ja", hook="モグラの巣の中"):
    return db.insert_idea(
        conn,
        series_id=SERIES,
        lang=lang,
        hook=hook,
        video_prompt="Extreme macro close-up. A mole, tunnelling, underground.\nAudio: soil.",
        scene_summary=scene,
        tags=["#生き物"],
        dedup_key=dedup.make_key(SERIES, scene),
    )


def _seed_render(conn, idea_id, tmp_path, status="approved"):
    gen_id = db.insert_generation(
        conn, idea_id=idea_id, path=str(tmp_path / f"src_{idea_id}.mp4"), backend="fake",
        model="veo-3.1-lite-generate-preview", duration_s=8, cost_usd=0.40,
    )
    render = tmp_path / f"render_{idea_id}.mp4"
    render.write_bytes(b"x")
    db.upsert_render(
        conn, idea_id=idea_id, generation_id=gen_id, path=str(render),
        thumb_path=None, burned_hook="t",
    )
    db.set_idea_status(conn, idea_id, status)
    conn.commit()
    return render


# --- dedup at the database boundary -----------------------------------------


def test_duplicate_dedup_key_is_rejected_by_the_database(temp_db):
    assert _seed_idea(temp_db) is not None
    assert _seed_idea(temp_db) is None, "the UNIQUE constraint should reject the second insert"


# --- generate ----------------------------------------------------------------


def test_generate_records_a_generation_and_advances_status(temp_db):
    _seed_idea(temp_db)
    temp_db.commit()

    result = generate.run(limit=5, backend_name="fake")
    assert (result.generated, result.failed) == (1, 0)

    with db.session() as conn:
        idea = conn.execute("SELECT * FROM ideas").fetchone()
        assert idea["status"] == "generated"
        assert db.latest_generation(conn, int(idea["id"])) is not None


def test_generate_is_idempotent(temp_db):
    _seed_idea(temp_db)
    temp_db.commit()

    generate.run(limit=5, backend_name="fake")
    assert generate.run(limit=5, backend_name="fake").generated == 0

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM generations").fetchone()["n"] == 1


def test_generate_stops_when_the_budget_is_gone(temp_db, monkeypatch):
    from shorts_auto import config

    _seed_idea(temp_db, scene="a mole tunnelling through soil")
    _seed_idea(temp_db, scene="a hedgehog settling into a leaf nest")
    temp_db.commit()

    settings = {**config.load_settings(), "budget": {"monthly_usd": 0.0, "per_run_usd": 0.0}}
    monkeypatch.setattr(config, "load_settings", lambda: settings)

    # Real list prices apply even to the fake backend, so a zero budget bites.
    result = generate.run(limit=5, backend_name="veo")
    assert result.generated == 0
    assert result.skipped_budget == 2
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM generations").fetchone()["n"] == 0


def test_dry_run_reports_the_real_price_but_charges_nothing(temp_db):
    _seed_idea(temp_db)
    temp_db.commit()

    result = generate.run(limit=1, dry_run=True)
    assert result.spent_usd == 0.0

    with db.session() as conn:
        row = conn.execute("SELECT * FROM generations").fetchone()
        assert row["cost_usd"] == 0.0
        assert row["backend"] == "fake"


def test_dry_run_overrides_an_explicit_paid_backend(temp_db):
    """--dry-run exists to make a command safe; another flag must not undo that."""
    _seed_idea(temp_db)
    temp_db.commit()

    generate.run(limit=1, backend_name="veo", dry_run=True)
    with db.session() as conn:
        assert conn.execute("SELECT backend FROM generations").fetchone()["backend"] == "fake"


def test_adhoc_generate_honours_dry_run(temp_db, temp_work):
    """`generate --prompt X --dry-run` used to ignore the flag entirely and bill
    for a real generation."""
    cost = generate.generate_one(
        "a test prompt", temp_work / "adhoc.mp4", backend_name="veo", dry_run=True
    )
    assert cost == 0.0
    with db.session() as conn:
        assert conn.execute("SELECT backend FROM generations").fetchone()["backend"] == "fake"


class _FailAfterFirst:
    """Succeeds once, then raises — a stand-in for a crash mid-run."""

    name = "flaky"
    calls = 0

    def estimate_cost(self, request):
        return pricing.estimate_cost(request.model, request.resolution, request.duration_seconds)

    def generate(self, request):
        type(self).calls += 1
        if type(self).calls > 1:
            raise RuntimeError("simulated crash")
        Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(request.output_path).write_bytes(b"ok")
        return GeneratedVideo(
            path=request.output_path, backend=self.name, model=request.model,
            duration_s=request.duration_seconds, cost_usd=self.estimate_cost(request),
        )


def test_a_crash_mid_run_does_not_erase_the_spend_already_recorded(temp_db):
    """The whole run used to be one transaction, so a crash after two paid calls
    rolled back the ledger while the money was already gone."""
    _FailAfterFirst.calls = 0
    backends.register("flaky", _FailAfterFirst)

    _seed_idea(temp_db, scene="a mole tunnelling through soil")
    _seed_idea(temp_db, scene="a hedgehog settling into a leaf nest")
    temp_db.commit()

    result = generate.run(limit=2, backend_name="flaky")
    assert result.generated == 1
    assert result.failed == 1

    with db.session() as conn:
        rows = conn.execute("SELECT outcome, cost_usd FROM generations ORDER BY id").fetchall()
        assert [r["outcome"] for r in rows] == ["ok", "error"]
        # An unclassified failure is assumed billed: over-counting is recoverable,
        # under-counting is how a cap silently fails.
        assert sum(r["cost_usd"] for r in rows) == pytest.approx(0.80)


class _SafetyBlockedBackend(_FailAfterFirst):
    name = "blocked"

    def generate(self, request):
        raise SafetyBlocked("filtered")


class _BilledFailureBackend(_FailAfterFirst):
    name = "billedfail"

    def generate(self, request):
        raise BilledFailure("download died after generation succeeded")


def test_a_safety_block_is_recorded_at_zero_cost(temp_db):
    """Google does not charge for blocked generations."""
    backends.register("blocked", _SafetyBlockedBackend)
    _seed_idea(temp_db)
    temp_db.commit()

    generate.run(limit=1, backend_name="blocked")
    with db.session() as conn:
        row = conn.execute("SELECT * FROM generations").fetchone()
        assert row["outcome"] == "safety_blocked"
        assert row["cost_usd"] == 0.0


def test_a_failure_after_billing_is_charged_in_full(temp_db):
    backends.register("billedfail", _BilledFailureBackend)
    _seed_idea(temp_db)
    temp_db.commit()

    generate.run(limit=1, backend_name="billedfail")
    with db.session() as conn:
        row = conn.execute("SELECT * FROM generations").fetchone()
        assert row["outcome"] == "billed_failure"
        assert row["cost_usd"] == pytest.approx(0.40)


# --- publish -----------------------------------------------------------------


def _fake_upload(monkeypatch, video_id="vid123", fail=False):
    calls: list[dict] = []

    def _upload(channel_id, video_path, **kwargs):
        calls.append({"channel": channel_id, **kwargs})
        if fail:
            raise RuntimeError("upload exploded")
        return f"{video_id}_{len(calls)}"

    monkeypatch.setattr(youtube, "upload_video", _upload)
    return calls


def test_publish_uploads_once_and_records_it(temp_db, temp_work, monkeypatch):
    calls = _fake_upload(monkeypatch)
    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)

    result = publish.run(limit=3)
    assert result.published == 1
    assert calls[0]["privacy"] == "private"

    with db.session() as conn:
        post = conn.execute("SELECT * FROM posts").fetchone()
        assert post["privacy"] == "private"
        assert post["went_public_at"] is None
        assert db.get_idea(conn, idea_id)["status"] == "published"


def test_the_same_idea_can_never_be_uploaded_twice(temp_db, temp_work, monkeypatch):
    """A crash between "uploaded" and "recorded" used to cause a second upload of
    the same video on the next run, which is exactly what gets a channel flagged
    for reused content."""
    _fake_upload(monkeypatch)
    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)

    publish.run(limit=3)
    assert publish.run(limit=3).published == 0

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"] == 1
        # And the constraint holds even against a direct insert.
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_post(
                conn, idea_id=idea_id, channel_id="ja", youtube_video_id="other",
                title="t", path="p", privacy="private",
            )


def test_the_disclosure_leads_the_description():
    """YouTube truncates descriptions in most surfaces, so a disclosure at the
    bottom is a disclosure nobody reads."""
    body = publish.build_description("面白い動画", "ja", {"default_tags": []}, ["#shorts"])
    assert body.startswith(publish.DISCLOSURE["ja"])


def test_a_repeatedly_failing_upload_stops_blocking_the_queue(temp_db, temp_work, monkeypatch):
    """An unpublishable candidate sat at the head of the queue and ate a daily
    upload slot on every run."""
    _fake_upload(monkeypatch, fail=True)
    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)

    for _ in range(3):
        publish.run(limit=1)
    result = publish.run(limit=1)

    assert result.failed == 1
    with db.session() as conn:
        assert db.get_idea(conn, idea_id)["status"] == "failed"
        assert not db.publishable_ideas(conn)


def test_publish_dry_run_uploads_nothing(temp_db, temp_work, monkeypatch):
    calls = _fake_upload(monkeypatch)
    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)

    result = publish.run(limit=3, dry_run=True)
    assert result.published == 1
    assert calls == []
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"] == 0


# --- go-live -----------------------------------------------------------------


def test_go_live_makes_a_post_public_and_stamps_the_clock(temp_db, temp_work, monkeypatch):
    """Without this stage every video stayed private, earned no views, and the
    report had nothing to measure."""
    _fake_upload(monkeypatch)
    flipped: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        youtube, "set_privacy", lambda c, v, p: flipped.append((c, v, p))
    )
    monkeypatch.setattr(youtube, "verify_disclosure", lambda c, v: dict.fromkeys(v, True))

    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)
    publish.run(limit=1)

    result = golive.run()
    assert result.went_public == 1
    assert flipped[0][2] == "public"

    with db.session() as conn:
        post = conn.execute("SELECT * FROM posts").fetchone()
        assert post["privacy"] == "public"
        assert post["went_public_at"] is not None
        assert len(db.measurable_posts(conn)) == 1


def test_go_live_warns_when_youtube_did_not_record_the_disclosure(
    temp_db, temp_work, monkeypatch
):
    _fake_upload(monkeypatch)
    monkeypatch.setattr(youtube, "set_privacy", lambda c, v, p: None)
    monkeypatch.setattr(youtube, "verify_disclosure", lambda c, v: dict.fromkeys(v, False))

    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)
    publish.run(limit=1)

    result = golive.run()
    assert any("WARNING" in note for note in result.notes)


def test_private_posts_are_not_measurable(temp_db, temp_work, monkeypatch):
    _fake_upload(monkeypatch)
    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)
    publish.run(limit=1)

    with db.session() as conn:
        assert db.measurable_posts(conn) == []
        assert len(db.private_posts(conn)) == 1


# --- analytics windows -------------------------------------------------------


def _ago(**kwargs) -> str:
    return (datetime.now(UTC) - timedelta(**kwargs)).isoformat(timespec="seconds")


def test_a_fresh_post_only_gets_the_rolling_snapshot():
    assert analytics.due_windows(_ago(hours=2), set()) == ["latest"]


def test_a_window_opens_only_near_its_target_age():
    assert "24h" in analytics.due_windows(_ago(hours=26), set())
    assert "24h" not in analytics.due_windows(_ago(hours=2), set())


def test_a_long_overdue_window_is_reported_missed_not_backfilled():
    """Filing 10 days of views under "24h" made the arms incomparable."""
    assert "24h" not in analytics.due_windows(_ago(days=10), set())
    assert "24h" in analytics.missed_windows(_ago(days=10), set())


def test_recorded_windows_are_never_overwritten():
    due = analytics.due_windows(_ago(hours=26), {"24h"})
    assert due == ["latest"]


# --- youtube text limits -----------------------------------------------------


def test_description_is_truncated_by_bytes_not_characters():
    """A Japanese character is 3 bytes; 5,000 of them is 15,000 bytes and the
    API rejects it."""
    japanese = "あ" * 5000
    cut = youtube.truncate_bytes(japanese, youtube.DESCRIPTION_MAX_BYTES)
    assert len(cut.encode("utf-8")) <= youtube.DESCRIPTION_MAX_BYTES
    assert cut  # and it is still valid UTF-8, not a split character


def test_truncation_leaves_short_text_alone():
    assert youtube.truncate_bytes("短い", 100) == "短い"


def test_tags_are_dropped_from_the_end_to_fit_the_byte_budget():
    tags = ["first", "second", *["padding" * 10] * 20]
    kept = youtube.fit_tags(tags)
    assert kept[0] == "first"
    assert len(",".join(kept).encode("utf-8")) <= youtube.TAGS_MAX_BYTES
    assert len(kept) < len(tags)


# --- pricing and request validation ------------------------------------------


def test_lite_720p_eight_seconds_costs_forty_cents():
    assert pricing.estimate_cost("veo-3.1-lite-generate-preview", "720p", 8) == pytest.approx(0.40)


def test_unknown_model_is_an_error_not_a_silent_zero():
    with pytest.raises(pricing.UnknownPriceError):
        pricing.estimate_cost("veo-9-imaginary", "720p", 8)


def test_an_unknown_llm_model_is_charged_at_the_highest_rate():
    """Charging zero for an unrecognised model would let it slip past the cap."""
    known = pricing.estimate_llm_cost("claude-opus-5", 1_000_000, 1_000_000)
    unknown = pricing.estimate_llm_cost("some-new-model", 1_000_000, 1_000_000)
    assert unknown == pytest.approx(known)


@pytest.mark.parametrize("resolution,duration", [("1080p", 4), ("1080p", 6), ("4k", 6)])
def test_high_resolution_requires_eight_seconds(resolution, duration):
    with pytest.raises(pricing.InvalidVideoRequest, match="requires duration_seconds=8"):
        pricing.validate_video_request("veo-3.1-generate-preview", resolution, duration)


def test_an_unsupported_duration_is_rejected_before_the_api_call():
    with pytest.raises(pricing.InvalidVideoRequest, match="must be one of"):
        pricing.validate_video_request("veo-3.1-lite-generate-preview", "720p", 5)


def test_lite_has_no_4k_tier():
    with pytest.raises(pricing.InvalidVideoRequest, match="does not support resolution"):
        pricing.validate_video_request("veo-3.1-lite-generate-preview", "4k", 8)


def test_an_unsupported_sdk_field_fails_before_spending(monkeypatch):
    """Dropping a field silently still bills full price while producing something
    other than what was asked for — e.g. a 16:9 video billed as a Short."""
    from google.genai import types

    from shorts_auto.models import VideoRequest

    # Simulate an SDK version that no longer knows about aspect_ratio.
    monkeypatch.setattr(
        types.GenerateVideosConfig,
        "model_fields",
        {k: v for k, v in types.GenerateVideosConfig.model_fields.items() if k != "aspect_ratio"},
    )
    request = VideoRequest(
        prompt="p", model="veo-3.1-lite-generate-preview", output_path="/tmp/x.mp4",
        aspect_ratio="9:16", resolution="720p", duration_seconds=8,
    )
    with pytest.raises(RuntimeError, match="does not support"):
        VeoBackend()._build_config(request)


def test_a_fully_supported_request_builds_a_config():
    from shorts_auto.models import VideoRequest

    request = VideoRequest(
        prompt="p", model="veo-3.1-lite-generate-preview", output_path="/tmp/x.mp4",
        aspect_ratio="9:16", resolution="720p", duration_seconds=8,
        negative_prompt="watermark", person_generation="allow_all",
    )
    config_obj = VeoBackend()._build_config(request)
    assert config_obj.aspect_ratio == "9:16"
    assert config_obj.negative_prompt == "watermark"


# --- schema migration --------------------------------------------------------


def test_a_pre_release_database_with_data_is_refused(temp_work):
    """The old rows do not record which channel a render targeted, so there is no
    honest mapping forward. Refuse loudly rather than invent history."""
    from shorts_auto import paths

    raw = sqlite3.connect(paths.db_path())
    raw.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY, cost_usd REAL)")
    raw.execute("INSERT INTO assets (cost_usd) VALUES (0.4)")
    raw.execute("PRAGMA user_version = 0")
    raw.commit()
    raw.close()

    with pytest.raises(db.MigrationRequired, match="pre-release schema"):
        db.init_db()


def test_an_empty_pre_release_database_migrates_cleanly(temp_work):
    from shorts_auto import paths

    raw = sqlite3.connect(paths.db_path())
    raw.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY, cost_usd REAL)")
    raw.execute("PRAGMA user_version = 0")
    raw.commit()
    raw.close()

    db.init_db()
    with db.session() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert not db._table_exists(conn, "assets")


def test_an_unknown_status_is_rejected(temp_db):
    idea_id = _seed_idea(temp_db)
    with pytest.raises(ValueError, match="unknown idea status"):
        db.set_idea_status(temp_db, idea_id, "totally-made-up")


# --- report ------------------------------------------------------------------


def test_report_says_nothing_when_there_is_nothing_to_say(temp_db):
    from shorts_auto.report import render_report

    assert "判定不能" in render_report()


def test_report_calls_out_videos_stranded_in_private(temp_db, temp_work, monkeypatch):
    from shorts_auto.report import render_report

    _fake_upload(monkeypatch)
    idea_id = _seed_idea(temp_db)
    _seed_render(temp_db, idea_id, temp_work)
    publish.run(limit=1)

    text = render_report()
    assert "private" in text
    assert "go-live" in text


def test_report_refuses_a_verdict_on_thin_data(temp_db, temp_work, monkeypatch):
    """A confident recommendation off three videos is worse than none."""
    from shorts_auto.report import render_report

    _fake_upload(monkeypatch)
    monkeypatch.setattr(youtube, "set_privacy", lambda c, v, p: None)
    monkeypatch.setattr(youtube, "verify_disclosure", lambda c, v: {})

    for n in range(2):
        idea_id = _seed_idea(temp_db, scene=f"scene number {n}", hook=f"タイトル{n}")
        _seed_render(temp_db, idea_id, temp_work)
    publish.run(limit=2)
    golive.run()

    with db.session() as conn:
        for post in db.measurable_posts(conn):
            db.upsert_stats(
                conn, post_id=int(post["id"]), window="72h", views=1000, likes=5,
                avg_view_pct=0.5,
            )
        conn.commit()

    text = render_report()
    assert "データ不足" in text


# --- postprocess -------------------------------------------------------------


def test_japanese_titles_wrap_by_character_count():
    wrapped = postprocess.wrap_title("アリの巣の中はこうなっている", "ja")
    assert all(len(line) <= 12 for line in wrapped.split("\n"))
    assert wrapped.replace("\n", "") == "アリの巣の中はこうなっている"


def test_english_titles_wrap_on_word_boundaries():
    wrapped = postprocess.wrap_title("Inside a leafcutter ant nest, recreated with AI", "en")
    assert "\n" in wrapped
    assert not any(line.startswith(" ") for line in wrapped.split("\n"))


def test_short_title_is_left_alone():
    assert postprocess.wrap_title("短い", "ja") == "短い"


@needs_ffmpeg
def test_postprocess_produces_a_shorts_sized_render(temp_db, temp_work):
    from shorts_auto import ffmpeg

    _seed_idea(temp_db)
    temp_db.commit()
    generate.run(limit=1, backend_name="fake")

    result = postprocess.run(limit=1)
    assert (result.processed, result.failed) == (1, 0), result.errors

    with db.session() as conn:
        render = db.get_render(conn, 1)
        assert db.get_idea(conn, 1)["status"] == "post_processed"

    info = ffmpeg.video_info(Path(render["path"]))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["has_audio"]


@needs_ffmpeg
def test_postprocess_replaces_rather_than_duplicates_a_render(temp_db, temp_work):
    """Without UNIQUE(idea_id) a retried postprocess produced two rows, and the
    publisher uploaded both to the same channel."""
    _seed_idea(temp_db)
    temp_db.commit()
    generate.run(limit=1, backend_name="fake")

    postprocess.run(limit=1)
    with db.session() as conn:
        db.set_idea_status(conn, 1, "generated")
        conn.commit()
    postprocess.run(limit=1)

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM renders").fetchone()["n"] == 1


@needs_ffmpeg
def test_a_title_containing_a_percent_expansion_is_not_rewritten(temp_work):
    """drawtext expands %{...} by default: 'A%{eif:100:d}B' rendered as 'A100B'."""
    from shorts_auto import ffmpeg

    source = temp_work / "src.mp4"
    ffmpeg._run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=black:s=320x568:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ])

    title_file = temp_work / "title.txt"
    title_file.write_text("A%{eif:100:d}B", encoding="utf-8")
    plain = temp_work / "plain.txt"
    plain.write_text("A100B", encoding="utf-8")

    expanded = ffmpeg.extract_thumbnail(
        ffmpeg.render_short(source, temp_work / "a.mp4", title_file=title_file, has_audio=False),
        temp_work / "a.jpg", at_seconds=0,
    ).read_bytes()
    literal = ffmpeg.extract_thumbnail(
        ffmpeg.render_short(source, temp_work / "b.mp4", title_file=plain, has_audio=False),
        temp_work / "b.jpg", at_seconds=0,
    ).read_bytes()

    # If %{...} were still expanded, both frames would read "A100B" and match.
    assert expanded != literal


@needs_ffmpeg
def test_a_latin_only_font_is_refused_rather_than_burning_tofu(monkeypatch):
    from shorts_auto import ffmpeg

    monkeypatch.setattr(ffmpeg, "CJK_FONT_CANDIDATES", ("/nonexistent/font.ttf",))
    with pytest.raises(ffmpeg.FontMissing, match="CJK"):
        ffmpeg.find_font()
