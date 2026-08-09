"""Stage 7: pull view counts back in.

Snapshots are keyed to how old a video was **when measured**, and only recorded
inside a tolerance around each target age. The first version wrote the current
cumulative count into every window a post had aged past, so running `sync-stats`
after a week's gap filed a week of views under "24h" and the comparison between
arms became meaningless.

Age is measured from `went_public_at`, not from upload: a video sitting private
accumulates nothing, so counting that time would understate every rate.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .. import db, youtube

log = logging.getLogger(__name__)

# window label -> (target age, how far off that age a snapshot may still be filed)
WINDOWS: dict[str, tuple[timedelta, timedelta]] = {
    "24h": (timedelta(hours=24), timedelta(hours=12)),
    "72h": (timedelta(hours=72), timedelta(hours=24)),
    "7d": (timedelta(days=7), timedelta(days=2)),
}

# Always refreshed, so the dashboard is never stale.
ROLLING_WINDOW = "latest"


@dataclass(slots=True)
class AnalyticsResult:
    posts: int = 0
    updated: int = 0
    missed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def due_windows(
    went_public_at: str, existing: set[str], now: datetime | None = None
) -> list[str]:
    """Windows this post is currently close enough to for a valid snapshot."""
    reference = now or datetime.now(UTC)
    age = reference - datetime.fromisoformat(went_public_at)
    due = [
        name
        for name, (target, tolerance) in WINDOWS.items()
        if name not in existing and abs(age - target) <= tolerance
    ]
    return [*due, ROLLING_WINDOW]


def missed_windows(
    went_public_at: str, existing: set[str], now: datetime | None = None
) -> list[str]:
    """Windows whose measurement opportunity has passed unrecorded.

    Surfaced rather than silently backfilled: a 24h number captured on day 10 is
    not a 24h number, and pretending otherwise corrupts the comparison.
    """
    reference = now or datetime.now(UTC)
    age = reference - datetime.fromisoformat(went_public_at)
    return [
        name
        for name, (target, tolerance) in WINDOWS.items()
        if name not in existing and age > target + tolerance
    ]


def run() -> AnalyticsResult:
    result = AnalyticsResult()

    with db.session() as conn:
        posts = db.measurable_posts(conn)
        result.posts = len(posts)
        if not posts:
            log.info("no public posts to measure (run `tube-auto go-live` first)")
            db.record_run(conn, "sync-stats", ok=True, detail="no public posts")
            return result

        recorded = db.recorded_windows(conn)
        by_channel: dict[str, list] = defaultdict(list)
        for post in posts:
            by_channel[post["channel_id"]].append(post)

        for channel_id, channel_posts in by_channel.items():
            video_ids = [p["youtube_video_id"] for p in channel_posts]
            try:
                counters = youtube.fetch_stats(channel_id, video_ids)
            except Exception as exc:  # noqa: BLE001 - one channel must not kill the run
                log.error("stats fetch failed for channel %s: %s", channel_id, exc)
                result.errors.append(f"{channel_id}: {exc}")
                continue

            earliest = min(p["went_public_at"] for p in channel_posts)[:10]
            today = datetime.now(UTC).date().isoformat()
            try:
                retention = youtube.fetch_retention(channel_id, video_ids, earliest, today)
            except Exception as exc:  # noqa: BLE001 - retention is a nice-to-have
                log.warning("retention unavailable for channel %s: %s", channel_id, exc)
                result.errors.append(f"{channel_id} retention: {exc}")
                retention = {}

            for post in channel_posts:
                video_id = post["youtube_video_id"]
                stats = counters.get(video_id)
                if stats is None:
                    log.warning("no statistics returned for %s", video_id)
                    result.errors.append(f"no statistics for {video_id}")
                    continue

                post_id = int(post["id"])
                seen = recorded.get(post_id, set())
                # None, not 0.0: an absent measurement must not read as
                # "measured, nobody watched" — nor as perfect retention.
                measured = retention.get(video_id)

                for window in due_windows(post["went_public_at"], seen):
                    db.upsert_stats(
                        conn,
                        post_id=post_id,
                        window=window,
                        views=stats["views"],
                        likes=stats["likes"],
                        avg_view_pct=measured,
                    )
                    result.updated += 1

                for window in missed_windows(post["went_public_at"], seen):
                    result.missed.append(f"{video_id}:{window}")

        if result.missed:
            log.warning(
                "%d measurement window(s) passed unrecorded; run sync-stats daily "
                "(see `tube-auto doctor`)", len(result.missed)
            )
        conn.commit()
        db.record_run(
            conn,
            "sync-stats",
            ok=not result.errors,
            detail=f"{result.updated} rows, {len(result.missed)} missed",
        )

    return result
