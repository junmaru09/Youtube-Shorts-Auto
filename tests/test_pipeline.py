"""Stage behaviour that does not need the network.

The parts covered here are the ones where being wrong is expensive and quiet:
spend that escapes the ledger, uploads that happen twice, a publishing pace that
looks like spam, and a report that gives a confident answer it has no evidence
for.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from tube_auto import config, db, dedup, youtube
from tube_auto.budget import AlreadyRunning, BudgetExceeded, BudgetGuard, exclusive_run
from tube_auto.stages import analytics, assemble, footage, publish

THEME = "black_holes"


def _idea(conn, key="k1", status="assembled", **fields):
    idea_id = db.insert_idea(
        conn, series_id=fields.get("series_id", THEME), lang="ja",
        hook=fields.get("hook", "タイトル"), scene_summary=f"angle {key}",
        tags=["#宇宙"], dedup_key=dedup.make_key(THEME, key),
    )
    if idea_id and status:
        db.set_idea_status(conn, idea_id, status)
    return idea_id


def _ready_to_publish(conn, tmp_path, key="k1"):
    idea_id = _idea(conn, key, status="approved")
    render = tmp_path / f"{key}.mp4"
    render.write_bytes(b"x")
    db.upsert_render(
        conn, idea_id=idea_id, path=str(render), thumb_path=None,
        duration_s=1080, chapters_text="00:00 つかみ",
    )
    conn.commit()
    return idea_id


# --- the spend ledger ---------------------------------------------------------


SETTINGS = {"budget": {"monthly_usd": 10.0, "per_run_usd": 2.0}}


def test_llm_spend_counts_against_the_ceiling(temp_db):
    """Research and script are the only paid calls in this pipeline; leaving
    them out of the ledger would leave the cap watching nothing."""
    db.insert_llm_call(
        temp_db, purpose="script", model="claude-sonnet-5",
        input_tokens=30000, output_tokens=13000, cost_usd=9.9,
    )
    temp_db.commit()
    with pytest.raises(BudgetExceeded, match="monthly"):
        BudgetGuard(temp_db, SETTINGS).check(0.5)


def test_the_guard_rereads_committed_spend(temp_db):
    """A stale in-memory total is how two runs each spend the full budget."""
    guard = BudgetGuard(temp_db, SETTINGS)
    assert guard.month_to_date == 0.0
    db.insert_llm_call(
        temp_db, purpose="script", model="m", input_tokens=1, output_tokens=1, cost_usd=9.0
    )
    temp_db.commit()
    assert guard.month_to_date == pytest.approx(9.0)


def test_a_second_concurrent_run_is_refused(temp_work):
    with exclusive_run("generate"):
        with pytest.raises(AlreadyRunning):
            with exclusive_run("generate"):
                pass


def test_the_lock_is_released_after_a_failure(temp_work):
    with pytest.raises(RuntimeError):
        with exclusive_run("generate"):
            raise RuntimeError("boom")
    with exclusive_run("generate"):
        pass


def test_tts_characters_are_tracked_against_the_free_tier(temp_db):
    """Going over does not fail, it starts charging — a silent switch from free
    to paid is exactly what the ledger exists to make visible."""
    idea_id = _idea(temp_db)
    db.upsert_narration(
        temp_db, idea_id=idea_id, path="/tmp/a.wav", duration_s=1080,
        chars=7200, timeline=[], voices={},
    )
    temp_db.commit()
    assert db.tts_chars_since(temp_db, "2000-01-01T00:00:00+00:00") == 7200


# --- uploading ----------------------------------------------------------------


def _fake_upload(monkeypatch, fail=False):
    calls: list[dict] = []

    def _upload(channel_id, video_path, **kwargs):
        calls.append({"channel": channel_id, **kwargs})
        if fail:
            raise RuntimeError("upload exploded")
        return f"vid{len(calls)}"

    monkeypatch.setattr(youtube, "upload_video", _upload)
    return calls


def test_the_same_idea_can_never_be_uploaded_twice(temp_db, temp_work, monkeypatch):
    """A crash between "uploaded" and "recorded" would otherwise cause a second
    upload — which is how a channel gets flagged for reused content."""
    _fake_upload(monkeypatch)
    idea_id = _ready_to_publish(temp_db, temp_work)

    publish.run(limit=3)
    assert publish.run(limit=3).published == 0

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_post(
                conn, idea_id=idea_id, channel_id="ja", youtube_video_id="other",
                title="t", path="p", privacy="private",
            )


def test_an_asset_without_rights_clearance_blocks_the_upload(temp_db, temp_work, monkeypatch):
    calls = _fake_upload(monkeypatch)
    idea_id = _ready_to_publish(temp_db, temp_work)
    with db.session() as conn:
        db.replace_assets(conn, idea_id, "still", [
            {"path": "/tmp/x.jpg", "license_ok": False, "credit": "unknown"}
        ])
        conn.commit()

    result = publish.run(limit=3)
    assert result.published == 0
    assert calls == []
    assert any("権利未確認" in note for note in result.notes)


def test_uploads_land_private(temp_db, temp_work, monkeypatch):
    """Private until a human has seen it on YouTube itself."""
    calls = _fake_upload(monkeypatch)
    _ready_to_publish(temp_db, temp_work)
    publish.run(limit=1)
    assert calls[0]["privacy"] == "private"

    with db.session() as conn:
        post = conn.execute("SELECT * FROM posts").fetchone()
        assert post["went_public_at"] is None
        assert db.measurable_posts(conn) == []


def test_the_description_leads_with_the_disclosure_and_credit(temp_db, temp_work):
    """YouTube truncates descriptions everywhere but the watch page, so a credit
    at the bottom is a credit nobody sees."""
    from tube_auto import brand as brand_mod

    idea_id = _ready_to_publish(temp_db, temp_work)
    with db.session() as conn:
        db.replace_sources(conn, idea_id, [
            {"ref": "S1", "kind": "arxiv", "url": "http://arxiv.org/abs/1",
             "title": "A paper", "summary": ""}
        ])
        conn.commit()
        idea = db.publishable_ideas(conn)[0]
        sources = [dict(s) for s in db.get_sources(conn, idea_id)]

    brand = brand_mod.load_brand()
    body = publish.build_description(idea, sources, "00:00 つかみ", {"default_tags": []}, brand)

    assert body.startswith(brand.disclosure)
    assert "NASA" in body.splitlines()[1]
    assert "[S1]" in body and "arxiv.org" in body


# --- the publishing ramp --------------------------------------------------------


@pytest.fixture
def launched_on(monkeypatch):
    """Pin the channel's launch date so the ramp is deterministic."""
    def _set(day: date):
        settings = dict(config.load_settings())
        settings["publish"] = {**settings["publish"], "channel_started_on": day.isoformat()}
        monkeypatch.setattr(config, "load_settings", lambda: settings)
    return _set


def test_the_first_fortnight_is_three_a_week(launched_on):
    """A new channel with no viewing history that starts posting daily reads as
    a spam account."""
    start = date(2026, 1, 1)
    launched_on(start)
    total = sum(config.uploads_allowed_today(start + timedelta(days=d)) for d in range(14))
    assert total == 6


def test_the_pace_reaches_daily_in_the_second_month(launched_on):
    start = date(2026, 1, 1)
    launched_on(start)
    assert config.allowed_uploads_per_week(40) == 7
    assert config.uploads_allowed_today(start + timedelta(days=40)) == 1


def test_twelve_weeks_lands_near_the_gate(launched_on):
    """About 85 videos is the measured requirement for 4,000 watch hours."""
    start = date(2026, 1, 1)
    launched_on(start)
    total = sum(config.uploads_allowed_today(start + timedelta(days=d)) for d in range(84))
    assert 65 <= total <= 80


def test_nothing_publishes_before_launch_day(launched_on):
    start = date(2026, 6, 1)
    launched_on(start)
    assert config.uploads_allowed_today(start - timedelta(days=1)) == 0


def test_an_unset_launch_date_stays_conservative(monkeypatch):
    settings = dict(config.load_settings())
    settings["publish"] = {**settings["publish"], "channel_started_on": None}
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    assert config.uploads_allowed_today() == 1


# --- playlists ----------------------------------------------------------------


class _FakePlaylists:
    """Enough of the playlists resource to exercise the find-or-create path."""

    def __init__(self, existing: list[tuple[str, str]]):
        self.existing = [{"id": pid, "snippet": {"title": t}} for pid, t in existing]
        self.inserted: list[dict] = []
        self.added: list[dict] = []

    # playlists()
    def list(self, **kwargs):
        return _Executable({"items": self.existing})

    def list_next(self, request, response):
        return None

    def insert(self, *, part, body):
        self.inserted.append(body)
        return _Executable({"id": f"PL{len(self.inserted)}"})


class _Executable:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeService:
    def __init__(self, playlists: _FakePlaylists):
        self._playlists = playlists

    def playlists(self):
        return self._playlists

    def playlistItems(self):  # noqa: N802 - mirrors the Google client
        return self

    def insert(self, *, part, body):
        self._playlists.added.append(body["snippet"])
        return _Executable({"id": "PLI1"})


@pytest.fixture
def fake_playlists(monkeypatch):
    def _install(existing=()):
        playlists = _FakePlaylists(list(existing))
        monkeypatch.setattr(youtube, "data_client", lambda channel_id: _FakeService(playlists))
        return playlists
    return _install


def test_an_existing_playlist_is_reused(fake_playlists):
    """Creating one per upload would leave a channel with ninety playlists of
    one video each."""
    playlists = fake_playlists([("PLexisting", "ブラックホールと重力がつくる現象")])
    found = youtube.ensure_playlist("ja", "ブラックホールと重力がつくる現象")
    assert found == "PLexisting"
    assert playlists.inserted == []


def test_a_missing_playlist_is_created_public(fake_playlists):
    playlists = fake_playlists()
    created = youtube.ensure_playlist("ja", "新テーマ", "説明")
    assert created == "PL1"
    assert playlists.inserted[0]["status"]["privacyStatus"] == "public"


def test_going_public_files_the_video_under_its_theme(temp_db, temp_work, monkeypatch):
    from tube_auto.stages import golive

    _fake_upload(monkeypatch)
    _ready_to_publish(temp_db, temp_work)
    publish.run(limit=1)

    monkeypatch.setattr(youtube, "set_privacy", lambda *a, **k: None)
    monkeypatch.setattr(youtube, "verify_disclosure", lambda c, v: dict.fromkeys(v, True))
    playlists = _FakePlaylists([])
    monkeypatch.setattr(youtube, "data_client", lambda channel_id: _FakeService(playlists))

    result = golive.run()
    assert result.went_public == 1
    assert playlists.inserted[0]["snippet"]["title"] == config.theme_by_id(THEME).description
    assert playlists.added[0]["resourceId"]["videoId"] == "vid1"


def test_a_playlist_failure_leaves_the_video_public(temp_db, temp_work, monkeypatch):
    """The playlist is a nice-to-have; reverting a successful go-live over one
    would be strictly worse than a video filed nowhere."""
    from tube_auto.stages import golive

    _fake_upload(monkeypatch)
    _ready_to_publish(temp_db, temp_work)
    publish.run(limit=1)

    monkeypatch.setattr(youtube, "set_privacy", lambda *a, **k: None)
    monkeypatch.setattr(youtube, "verify_disclosure", lambda c, v: dict.fromkeys(v, True))
    monkeypatch.setattr(
        youtube, "ensure_playlist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota"))
    )

    result = golive.run()
    assert result.went_public == 1
    assert result.failed == 0
    assert any("プレイリスト" in note for note in result.notes)

    with db.session() as conn:
        assert db.private_posts(conn) == []
        assert len(db.measurable_posts(conn)) == 1


# --- measurement windows ---------------------------------------------------------


def _ago(**kwargs) -> str:
    return (datetime.now(UTC) - timedelta(**kwargs)).isoformat(timespec="seconds")


def test_a_window_opens_only_near_its_target_age():
    assert "24h" in analytics.due_windows(_ago(hours=26), set())
    assert "24h" not in analytics.due_windows(_ago(hours=2), set())


def test_a_long_overdue_window_is_reported_missed_not_backfilled():
    """Filing ten days of views under "24h" makes the arms incomparable."""
    assert "24h" not in analytics.due_windows(_ago(days=10), set())
    assert "24h" in analytics.missed_windows(_ago(days=10), set())


def test_a_recorded_window_is_never_rewritten():
    assert analytics.due_windows(_ago(hours=26), {"24h"}) == ["latest"]


# --- the visual plan --------------------------------------------------------------


SPANS = [
    {"chapter": 0, "start_s": 0, "end_s": 20},
    {"chapter": 1, "start_s": 20, "end_s": 60},
    {"chapter": 2, "start_s": 60, "end_s": 250},
    {"chapter": 3, "start_s": 250, "end_s": 520},
    {"chapter": 4, "start_s": 520, "end_s": 820},
    {"chapter": 5, "start_s": 820, "end_s": 1060},
    {"chapter": 6, "start_s": 1060, "end_s": 1140},
]
COMPOSITION = {"footage": 0.30, "still": 0.40, "diagram": 0.25, "title": 0.05}


def _plan(allow_footage: bool):
    from tube_auto import brand as brand_mod

    return footage.plan_cuts(
        SPANS, COMPOSITION, {"min": 6, "max": 14},
        brand_mod.FOOTAGE_HEAVY, [c.key for c in brand_mod.EPISODE_PLAN],
        allow_footage=allow_footage,
    )


def test_every_slot_gets_a_kind_including_diagrams():
    """Planning footage and stills over the whole timeline first would leave
    diagrams only where NASA came up short, rather than where a figure helps."""
    kinds = {slot.kind for slot in _plan(True)}
    assert kinds == {"footage", "still", "diagram"}


def test_a_theme_without_footage_gets_stills_not_diagrams():
    """The freed slots went to diagrams once, and the result was two thirds
    generated figures cycling three shapes."""
    slots = _plan(False)
    assert not any(slot.kind == "footage" for slot in slots)
    stills = sum(1 for s in slots if s.kind == "still")
    diagrams = sum(1 for s in slots if s.kind == "diagram")
    assert stills > diagrams * 2


def test_the_same_kind_never_runs_three_deep():
    run, previous = 0, None
    for slot in _plan(True):
        run = run + 1 if slot.kind == previous else 1
        previous = slot.kind
        assert run < 3


def test_short_chapters_still_get_diagrams():
    """Deciding per chapter starved the smallest kind wherever a chapter yielded
    one cut — a seven-chapter test video came out with none at all."""
    short = [{"chapter": i, "start_s": i * 6.6, "end_s": (i + 1) * 6.6} for i in range(7)]
    from tube_auto import brand as brand_mod

    slots = footage.plan_cuts(
        short, COMPOSITION, {"min": 6, "max": 14},
        brand_mod.FOOTAGE_HEAVY, [c.key for c in brand_mod.EPISODE_PLAN],
    )
    assert any(slot.kind == "diagram" for slot in slots)


def test_cuts_cover_the_whole_timeline():
    slots = _plan(True)
    covered = sum(slot.seconds for slot in slots)
    assert covered == pytest.approx(SPANS[-1]["end_s"], rel=0.02)


# --- subtitles and chapters --------------------------------------------------------


def test_japanese_never_breaks_before_a_closing_mark():
    wrapped = assemble.wrap_japanese("観測は数日おきに1件のペースで積み上がっています。", 24)
    assert not any(line.startswith("。") for line in wrapped.split("\n"))


def test_japanese_never_breaks_after_an_opening_bracket():
    wrapped = assemble.wrap_japanese("これは重要です「ここから引用がはじまります」という話", 16)
    assert not any(line.endswith("「") for line in wrapped.split("\n"))


def test_short_lines_are_left_alone():
    assert assemble.wrap_japanese("短い一文です。", 24) == "短い一文です。"


def test_chapters_start_at_zero():
    """YouTube ignores the whole list unless the first timestamp is 00:00."""
    text = assemble.format_chapters([
        {"start_s": 3.2, "title": "つかみ"},
        {"start_s": 64.0, "title": "本題"},
    ])
    assert text.splitlines()[0].startswith("00:00")
    assert "01:04 本題" in text


# --- byte limits -------------------------------------------------------------------


def test_description_is_truncated_by_bytes_not_characters():
    """A Japanese character is three bytes; 5,000 of them is 15,000 bytes and
    the API rejects it."""
    cut = youtube.truncate_bytes("あ" * 5000, youtube.DESCRIPTION_MAX_BYTES)
    assert len(cut.encode("utf-8")) <= youtube.DESCRIPTION_MAX_BYTES
    assert cut


def test_tags_are_dropped_from_the_end_to_fit():
    tags = ["最初", "次", *["padding" * 10] * 20]
    kept = youtube.fit_tags(tags)
    assert kept[0] == "最初"
    assert len(",".join(kept).encode("utf-8")) <= youtube.TAGS_MAX_BYTES


# --- schema ---------------------------------------------------------------------------


def test_a_pre_release_database_with_data_is_refused(temp_work):
    """Earlier versions modelled eight-second generated clips. There is no
    honest way to map that onto narrated long-form."""
    from tube_auto import paths

    raw = sqlite3.connect(paths.db_path())
    raw.execute("CREATE TABLE ideas (id INTEGER PRIMARY KEY, hook TEXT)")
    raw.execute("INSERT INTO ideas (hook) VALUES ('old')")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    with pytest.raises(db.MigrationRequired, match="schema version 1"):
        db.init_db()


def test_an_empty_older_database_is_rebuilt(temp_work):
    from tube_auto import paths

    raw = sqlite3.connect(paths.db_path())
    raw.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY)")
    raw.execute("PRAGMA user_version = 0")
    raw.commit()
    raw.close()

    db.init_db()
    with db.session() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert db._table_exists(conn, "sources")


def test_an_unknown_status_is_rejected(temp_db):
    idea_id = _idea(temp_db, status=None)
    with pytest.raises(ValueError, match="unknown idea status"):
        db.set_idea_status(temp_db, idea_id, "totally-made-up")


def test_an_assembled_video_reaches_the_review_queue(temp_db, temp_work):
    """This looked for a `post_processed` status that no longer exists, so the
    queue was permanently empty and nothing could ever be approved — the whole
    pipeline was blocked at the gate with no error anywhere."""
    idea_id = _idea(temp_db, status="assembled")
    render = temp_work / "r.mp4"
    render.write_bytes(b"x")
    db.upsert_render(
        temp_db, idea_id=idea_id, path=str(render), thumb_path=None,
        duration_s=1080, chapters_text="",
    )
    temp_db.commit()

    pending = db.pending_reviews(temp_db)
    assert [row["id"] for row in pending] == [idea_id]

    db.insert_review(temp_db, idea_id=idea_id, decision="approve",
                     reason_tag=None, note=None, checks={})
    assert db.pending_reviews(temp_db) == []


def test_a_render_without_a_thumbnail_is_queued_for_one(temp_db, temp_work):
    idea_id = _idea(temp_db, status="assembled")
    render = temp_work / "r.mp4"
    render.write_bytes(b"x")
    db.upsert_render(
        temp_db, idea_id=idea_id, path=str(render), thumb_path=None,
        duration_s=1080, chapters_text="",
    )
    temp_db.commit()
    assert [r["id"] for r in db.ideas_needing_thumbnails(temp_db)] == [idea_id]

    db.set_render_thumb(temp_db, idea_id, "/tmp/t.jpg")
    assert db.ideas_needing_thumbnails(temp_db) == []


def test_sources_are_replaced_not_appended(temp_db):
    idea_id = _idea(temp_db)
    for _ in range(2):
        db.replace_sources(temp_db, idea_id, [
            {"ref": "S1", "kind": "arxiv", "url": "u", "title": "t", "summary": ""}
        ])
    assert len(db.get_sources(temp_db, idea_id)) == 1


# --- the report -------------------------------------------------------------------------


def test_the_report_refuses_a_verdict_with_no_data(temp_db):
    from tube_auto.report import render_report

    assert "判定不能" in render_report()


def test_the_report_calls_out_videos_stranded_in_private(temp_db, temp_work, monkeypatch):
    from tube_auto.report import render_report

    _fake_upload(monkeypatch)
    _ready_to_publish(temp_db, temp_work)
    publish.run(limit=1)

    text = render_report()
    assert "private" in text
    assert "go-live" in text


def test_watch_hours_come_from_length_and_retention_not_views(temp_db, temp_work, monkeypatch):
    """The gate is 4,000 hours. A channel can be ahead on views and far behind
    on the gate, so views alone cannot answer it."""
    from tube_auto.report import collect

    _fake_upload(monkeypatch)
    idea_id = _ready_to_publish(temp_db, temp_work)
    publish.run(limit=1)

    with db.session() as conn:
        post = conn.execute("SELECT * FROM posts").fetchone()
        db.mark_public(conn, int(post["id"]))
        db.upsert_stats(
            conn, post_id=int(post["id"]), window="72h",
            views=1000, likes=10, avg_view_pct=0.30,
        )
        conn.commit()

    report = collect()
    # 1000 views x 1080 s x 0.30 / 3600 = 90 hours
    assert report.watch_hours == pytest.approx(90.0, rel=0.01)
    assert report.videos_live == 1
