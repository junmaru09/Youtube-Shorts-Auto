"""Stage 5: upload approved renders.

Uploads land as `private`. They only become visible via `shorts-auto go-live`,
after the operator has seen the video on YouTube itself.

Two ceilings apply. The API allows 6 uploads/day on the default quota
(videos.insert costs 1600 of 10,000 units); settings.yaml sets a lower
self-imposed pace, because steady output is part of what separates a channel from
a content farm.

Each upload is committed immediately. An upload cannot be undone, so a lost
record means a second upload of the same video — and duplicate uploads are how a
channel gets flagged for reused content.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import config, db, youtube

log = logging.getLogger(__name__)

# Prepended to every description. Not configurable: the disclosure is a legal
# obligation under the EU AI Act, and burying or omitting it is not an option
# the operator should have.
DISCLOSURE = {
    "ja": "※この動画は生成AIで制作しています。",
    "en": "Note: this video was created with generative AI.",
}


@dataclass(slots=True)
class PublishResult:
    published: int = 0
    skipped: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)


def _day_start() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")


def build_description(hook: str, lang: str, channel: dict, tags: list[str]) -> str:
    """Body text, with the AI disclosure first.

    Placement matters: YouTube truncates the description in most surfaces, so a
    disclosure at the bottom is a disclosure nobody reads.
    """
    parts = [DISCLOSURE.get(lang, DISCLOSURE["en"]), "", hook]
    if tags:
        parts += ["", " ".join(tags)]
    footer = (channel.get("description_footer") or "").strip()
    if footer:
        parts += ["", footer]
    return "\n".join(parts).strip()


def run(
    limit: int | None = None,
    privacy: str | None = None,
    dry_run: bool = False,
) -> PublishResult:
    settings = config.load_settings()
    pub_cfg = settings.get("publish", {})
    daily_cap = int(settings.get("pipeline", {}).get("publish_per_day", 3))
    if daily_cap > youtube.MAX_UPLOADS_PER_DAY:
        raise config.ConfigError(
            f"pipeline.publish_per_day is {daily_cap} but the default YouTube Data API quota "
            f"allows {youtube.MAX_UPLOADS_PER_DAY} uploads/day"
        )

    limit = daily_cap if limit is None else limit
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    privacy = privacy or pub_cfg.get("initial_privacy", "private")
    category_id = str(pub_cfg.get("category_id", "24"))
    max_failures = int(pub_cfg.get("max_upload_failures", 3))

    result = PublishResult()
    with db.session() as conn:
        uploaded_today = db.posts_created_since(conn, _day_start())
        remaining = min(limit, max(0, daily_cap - uploaded_today))
        if remaining <= 0:
            result.notes.append(
                f"daily cap reached: {uploaded_today}/{daily_cap} uploaded in the last 24h"
            )
            return result

        candidates = db.publishable_ideas(conn)
        if not candidates:
            result.notes.append("nothing approved and waiting")
            return result

        if len(candidates) > remaining:
            result.skipped = len(candidates) - remaining
            result.notes.append(
                f"{result.skipped} idea(s) held back by the daily cap ({daily_cap}/day; "
                f"the API hard limit is {youtube.MAX_UPLOADS_PER_DAY}/day)"
            )

        for idea in candidates[:remaining]:
            idea_id = int(idea["id"])
            lang = idea["lang"]

            # A candidate that keeps failing would otherwise sit at the head of the
            # queue and eat a daily slot on every run.
            if int(idea["attempts"]) >= max_failures:
                db.set_idea_status(conn, idea_id, "failed")
                conn.commit()
                result.failed += 1
                result.notes.append(
                    f"idea {idea_id}: {idea['attempts']} upload failures, giving up"
                )
                continue

            try:
                channel = config.channel(lang)
            except config.ConfigError as exc:
                result.failed += 1
                result.notes.append(f"idea {idea_id}: {exc}")
                continue

            title = (idea["hook"] or "").strip()
            if not title:
                result.failed += 1
                result.notes.append(f"idea {idea_id}: empty title")
                continue

            series = config.series_by_id(idea["series_id"])
            tags = json.loads(idea["tags_json"] or "[]")
            all_tags = [*channel.get("default_tags", []), *[t.lstrip("#") for t in tags]]
            description = build_description(title, lang, channel, tags)
            video_path = Path(idea["render_path"])
            thumb = Path(idea["thumb_path"]) if idea["thumb_path"] else None

            if dry_run:
                result.notes.append(
                    f"would upload idea {idea_id} -> {channel['id']} ({privacy}): {title}"
                )
                result.published += 1
                continue

            try:
                video_id = youtube.upload_video(
                    channel["id"],
                    video_path,
                    title=title,
                    description=description,
                    tags=all_tags,
                    privacy=privacy,
                    category_id=category_id,
                    made_for_kids=series.made_for_kids,
                    thumbnail_path=thumb,
                )
            except Exception as exc:  # noqa: BLE001 - one bad upload must not kill the run
                log.error("upload failed for idea %d: %s", idea_id, exc)
                db.bump_attempts(conn, idea_id)
                conn.commit()
                result.failed += 1
                result.notes.append(f"idea {idea_id}: {exc}")
                continue

            # Commit immediately: the upload is already irreversible.
            db.insert_post(
                conn,
                idea_id=idea_id,
                channel_id=channel["id"],
                youtube_video_id=video_id,
                title=title,
                path=str(video_path),
                privacy=privacy,
            )
            db.set_idea_status(conn, idea_id, "published")
            db.reset_attempts(conn, idea_id)
            conn.commit()

            result.published += 1
            result.notes.append(
                f"idea {idea_id} -> https://youtube.com/shorts/{video_id} ({privacy})"
            )

        if result.published and privacy != "public":
            result.notes.append(
                "these are not visible yet. Check them in YouTube Studio, then run "
                "`shorts-auto go-live` — until they are public they earn no views and "
                "the report has nothing to measure."
            )

    return result
