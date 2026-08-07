"""Stage 5: upload approved renders.

Two ceilings apply. The API allows 6 uploads/day on the default quota
(videos.insert costs 1600 of 10,000 units); settings.yaml sets a lower
self-imposed pace, because steady output is what separates a channel from a
content farm in YouTube's eyes.

Uploads land as `private` by default so a bad generation cannot go public
before a human has seen it on the platform itself.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import config, db, youtube

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PublishResult:
    published: int = 0
    skipped: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)


def _day_start() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")


def build_description(idea_row, lang: str, channel: dict, tags: list[str]) -> str:
    """Body text. The AI disclosure line is not optional — see youtube.py."""
    hook = json.loads(idea_row["hook_json"]).get(lang, "")
    footer = (channel.get("description_footer") or "").strip()
    parts = [hook, "", " ".join(tags)]
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
    limit = limit if limit is not None else daily_cap
    privacy = privacy or pub_cfg.get("initial_privacy", "private")
    category_id = str(pub_cfg.get("category_id", "24"))
    disclose = bool(pub_cfg.get("disclose_synthetic_media", True))

    if not disclose:
        raise config.ConfigError(
            "publish.disclose_synthetic_media must stay true: every video here is "
            "AI-generated, and both YouTube's disclosure policy and the EU AI Act "
            "labelling obligation require it."
        )

    result = PublishResult()
    with db.session() as conn:
        already_today = db.posts_published_since(conn, _day_start())
        remaining = min(limit, max(0, daily_cap - already_today))
        if remaining <= 0:
            result.notes.append(
                f"daily cap reached: {already_today}/{daily_cap} uploaded in the last 24h"
            )
            return result

        candidates = db.publishable_assets(conn)
        if not candidates:
            result.notes.append("nothing approved and waiting")
            return result

        if len(candidates) > remaining:
            result.skipped = len(candidates) - remaining
            result.notes.append(
                f"{result.skipped} asset(s) held back by the daily cap "
                f"({daily_cap}/day; API hard limit is 6/day on the default quota)"
            )

        for asset in candidates[:remaining]:
            lang = asset["lang"]
            idea_id = int(asset["idea_id"])
            try:
                channel = config.channel(lang)
            except config.ConfigError:
                result.failed += 1
                result.notes.append(f"idea {idea_id}: no channel configured for lang '{lang}'")
                continue

            title = json.loads(asset["hook_json"]).get(lang, "").strip()
            if not title:
                result.failed += 1
                result.notes.append(f"idea {idea_id}: empty {lang} title")
                continue

            tags = json.loads(asset["tags_json"]).get(lang, [])
            all_tags = [*channel.get("default_tags", []), *[t.lstrip("#") for t in tags]]
            description = build_description(asset, lang, channel, tags)
            video_path = Path(asset["path"])
            meta = json.loads(asset["meta_json"] or "{}")
            thumb = Path(meta["thumbnail"]) if meta.get("thumbnail") else None

            if dry_run:
                result.notes.append(f"would upload idea {idea_id} [{lang}] -> {channel['id']}: {title}")
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
                    contains_synthetic_media=True,
                    thumbnail_path=thumb,
                )
            except Exception as exc:  # noqa: BLE001 - one bad upload must not kill the run
                log.error("upload failed for idea %d [%s]: %s", idea_id, lang, exc)
                result.failed += 1
                result.notes.append(f"idea {idea_id} [{lang}]: {exc}")
                continue

            db.insert_post(
                conn,
                asset_id=int(asset["id"]),
                channel_id=channel["id"],
                youtube_video_id=video_id,
                title=title,
                privacy=privacy,
            )
            result.published += 1
            result.notes.append(
                f"idea {idea_id} [{lang}] -> https://youtube.com/shorts/{video_id} ({privacy})"
            )

        # An idea is done once every language variant has a post.
        for asset in candidates[:remaining]:
            idea_id = int(asset["idea_id"])
            pending = [
                a for a in db.publishable_assets(conn) if int(a["idea_id"]) == idea_id
            ]
            if not pending:
                db.set_idea_status(conn, idea_id, "published")

    return result
