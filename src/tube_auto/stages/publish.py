"""Stage 7: upload approved videos.

Uploads land as `private`. They only become visible via `tube-auto go-live`,
after the operator has seen the video on YouTube itself.

The pace ramps rather than starting at one a day. A brand-new channel with no
viewing history that begins posting daily looks like a spam account, and mass
uploading without improvement is counterproductive on its own terms. The ramp
costs about three weeks of progress toward the watch-hour gate and buys the
channel not being flagged in its first fortnight.

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

from .. import brand as brand_mod
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


def build_description(idea, sources, chapters_text: str, channel: dict, brand) -> str:
    """The description.

    Order is deliberate. The AI disclosure and the NASA credit lead, because
    YouTube truncates descriptions everywhere except the watch page and a credit
    nobody sees is not a credit. Chapters come next so the timestamps are
    clickable. The sources come last but they always come — that is the promise
    the channel is built on, and the thing a summary-rewriting channel will not
    copy.
    """
    parts = [brand_mod.description_header(brand), ""]

    why_now = (idea["why_now"] or "").strip()
    if why_now:
        parts += [why_now, ""]

    if chapters_text:
        parts += ["■ 目次", chapters_text, ""]

    if sources:
        parts.append("■ 参考にした一次ソース")
        for source in sources:
            when = (source["published_at"] or "")[:10]
            label = f"[{source['ref']}] {source['title']}"
            parts.append(f"{label}{f' ({when})' if when else ''}")
            parts.append(source["url"])
        parts.append("")

    tags = json.loads(idea["tags_json"] or "[]")
    if tags:
        parts += [" ".join(tags), ""]

    footer = (channel.get("description_footer") or "").strip()
    if footer:
        parts.append(footer)

    return "\n".join(parts).strip()


def run(
    limit: int | None = None,
    privacy: str | None = None,
    dry_run: bool = False,
) -> PublishResult:
    settings = config.load_settings()
    pub_cfg = settings.get("publish", {})
    brand = brand_mod.load_brand()

    hard_cap = int(pub_cfg.get("hard_daily_cap", 6))
    if hard_cap > youtube.MAX_UPLOADS_PER_DAY:
        raise config.ConfigError(
            f"publish.hard_daily_cap is {hard_cap} but the default YouTube Data API quota "
            f"allows {youtube.MAX_UPLOADS_PER_DAY} uploads/day"
        )

    allowed = config.uploads_allowed_today()
    limit = allowed if limit is None else min(limit, allowed)
    privacy = privacy or pub_cfg.get("initial_privacy", "private")
    category_id = str(pub_cfg.get("category_id", "28"))
    max_failures = int(pub_cfg.get("max_upload_failures", 3))

    result = PublishResult()
    with db.session() as conn:
        if limit <= 0:
            start = config.channel_start_date()
            result.notes.append(
                "今日の投稿枠はありません"
                + (f"（開設 {start} からのランプアップ設定による）" if start
                   else "（publish.channel_started_on が未設定のため保守的に1本/日）")
            )
            return result

        uploaded_today = db.posts_created_since(conn, _day_start())
        remaining = max(0, limit - uploaded_today)
        if remaining <= 0:
            result.notes.append(f"本日はすでに {uploaded_today} 本投稿済みです（上限 {limit}）")
            return result

        candidates = db.publishable_ideas(conn)
        if not candidates:
            result.notes.append("承認済みで投稿待ちのものはありません")
            return result

        if len(candidates) > remaining:
            result.skipped = len(candidates) - remaining
            result.notes.append(
                f"{result.skipped} 本をランプアップ制限で保留（本日の上限 {limit} 本）"
            )

        for idea in candidates[:remaining]:
            idea_id = int(idea["id"])
            lang = idea["lang"]

            if int(idea["attempts"]) >= max_failures:
                db.set_idea_status(conn, idea_id, "failed")
                conn.commit()
                result.failed += 1
                result.notes.append(
                    f"idea {idea_id}: アップロードが {idea['attempts']} 回失敗、諦めます"
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
                result.notes.append(f"idea {idea_id}: タイトルが空です")
                continue

            blocked = db.unlicensed_assets(conn, idea_id)
            if blocked:
                result.failed += 1
                result.notes.append(
                    f"idea {idea_id}: 権利未確認の素材が {len(blocked)} 件あります。投稿しません"
                )
                continue

            theme = config.theme_by_id(idea["series_id"])
            sources = [dict(s) for s in db.get_sources(conn, idea_id)]
            description = build_description(
                idea, sources, idea["chapters_text"] or "", channel, brand
            )
            tags = json.loads(idea["tags_json"] or "[]")
            all_tags = [*channel.get("default_tags", []), *[t.lstrip("#") for t in tags]]
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
                    made_for_kids=theme.made_for_kids,
                    thumbnail_path=thumb,
                )
            except Exception as exc:  # noqa: BLE001 - one bad upload must not kill the run
                log.error("upload failed for idea %d: %s", idea_id, exc)
                db.bump_attempts(conn, idea_id)
                conn.commit()
                result.failed += 1
                result.notes.append(f"idea {idea_id}: {exc}")
                continue

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
                f"idea {idea_id} -> https://youtube.com/watch?v={video_id} ({privacy})"
            )

        if result.published and privacy != "public":
            result.notes.append(
                "まだ公開されていません。YouTube Studio で確認してから `tube-auto go-live` を実行してください。"
                "private のあいだは再生されず、計測も始まりません。"
            )

    return result
