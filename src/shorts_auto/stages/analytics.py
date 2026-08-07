"""Stage 6: pull view counts back in.

Snapshots are keyed to the post's age, not the fetch time. A video measured at
24h and one measured at 7d are not comparable, and the A/B allocation would be
skewed toward whichever series happened to be posted earlier if they were
lumped together. `72h` is the window scoring uses.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .. import db, youtube

log = logging.getLogger(__name__)

# window label -> minimum age before the snapshot is meaningful
WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "72h": timedelta(hours=72),
    "7d": timedelta(days=7),
}


@dataclass(slots=True)
class AnalyticsResult:
    posts: int = 0
    updated: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def due_windows(published_at: str, existing: set[str], now: datetime | None = None) -> list[str]:
    """Windows this post has aged past but has no snapshot for yet.

    `latest` is always refreshed so the dashboard is never stale.
    """
    reference = now or datetime.now(UTC)
    age = reference - datetime.fromisoformat(published_at)
    due = [name for name, threshold in WINDOWS.items() if age >= threshold and name not in existing]
    return [*due, "latest"]


def run() -> AnalyticsResult:
    result = AnalyticsResult()

    with db.session() as conn:
        posts = db.all_posts(conn)
        result.posts = len(posts)
        if not posts:
            log.info("no posts to measure")
            return result

        recorded: dict[int, set[str]] = defaultdict(set)
        for row in conn.execute("SELECT post_id, window FROM stats"):
            recorded[int(row["post_id"])].add(row["window"])

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

            earliest = min(p["published_at"] for p in channel_posts)[:10]
            today = datetime.now(UTC).date().isoformat()
            try:
                retention = youtube.fetch_retention(channel_id, video_ids, earliest, today)
            except Exception as exc:  # noqa: BLE001 - retention is a nice-to-have
                log.warning("retention unavailable for channel %s: %s", channel_id, exc)
                retention = {}

            for post in channel_posts:
                video_id = post["youtube_video_id"]
                stats = counters.get(video_id)
                if stats is None:
                    log.warning("no statistics returned for %s", video_id)
                    continue

                for window in due_windows(post["published_at"], recorded[int(post["id"])]):
                    db.upsert_stats(
                        conn,
                        post_id=int(post["id"]),
                        window=window,
                        views=stats["views"],
                        likes=stats["likes"],
                        avg_view_pct=retention.get(video_id, 0.0),
                    )
                    result.updated += 1

    return result
