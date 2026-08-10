"""Stage 6: make uploaded videos visible.

The pipeline uploads as `private` so a bad generation cannot reach an audience
before a human has seen it on YouTube itself. That safety only works if there is
a way out of private — without this stage every video stays hidden, earns no
views, and the report has nothing to measure.

Going public also stamps `went_public_at`, which is the clock the analytics
windows run on. Measuring from upload time instead would credit a video with
hours of "exposure" it spent invisible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .. import db, youtube

log = logging.getLogger(__name__)


@dataclass(slots=True)
class GoLiveResult:
    went_public: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)


def run(limit: int | None = None, dry_run: bool = False, verify: bool = True) -> GoLiveResult:
    """Flip private posts to public, oldest first."""
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    result = GoLiveResult()
    with db.session() as conn:
        pending = db.private_posts(conn)
        if not pending:
            result.notes.append("no private posts waiting")
            return result

        batch = pending if limit is None else pending[:limit]
        if len(pending) > len(batch):
            result.notes.append(f"{len(pending) - len(batch)} more still private")

        for post in batch:
            video_id = post["youtube_video_id"]
            channel_id = post["channel_id"]

            if dry_run:
                result.notes.append(f"would publish {video_id} on channel '{channel_id}'")
                result.went_public += 1
                continue

            try:
                youtube.set_privacy(channel_id, video_id, "public")
            except Exception as exc:  # noqa: BLE001 - one failure must not stop the rest
                log.error("could not publish %s: %s", video_id, exc)
                result.failed += 1
                result.notes.append(f"{video_id}: {exc}")
                continue

            db.mark_public(conn, int(post["id"]))
            conn.commit()
            result.went_public += 1
            result.notes.append(f"https://youtube.com/watch?v={video_id} is now public")

            _add_to_theme_playlist(conn, post, video_id, result)

        if verify and not dry_run and result.went_public:
            _verify_disclosure(conn, batch, result)

        db.record_run(conn, "go-live", ok=result.failed == 0)
    return result


def _add_to_theme_playlist(conn, post, video_id: str, result: GoLiveResult) -> None:
    """File the video under its theme.

    Advisory: a playlist that could not be created is a lost session, not a lost
    video, and there is no reason to leave something public but unrecorded over it.
    """
    from .. import config

    try:
        theme = config.theme_by_id(post["series_id"])
        playlist_id = youtube.ensure_playlist(
            post["channel_id"], theme.description or theme.id
        )
        youtube.add_to_playlist(post["channel_id"], playlist_id, video_id)
    except Exception as exc:  # noqa: BLE001 - never block going public
        log.warning("could not add %s to its playlist: %s", video_id, exc)
        result.notes.append(f"{video_id}: プレイリストへの追加に失敗（動画は公開済み）: {exc}")


def _verify_disclosure(conn, posts, result: GoLiveResult) -> None:
    """Confirm YouTube actually stored the synthetic-media disclosure.

    Setting the field on upload is not proof it was recorded, and this is the one
    compliance claim the project makes about every video it publishes.
    """
    by_channel: dict[str, list[str]] = {}
    for post in posts:
        by_channel.setdefault(post["channel_id"], []).append(post["youtube_video_id"])

    for channel_id, video_ids in by_channel.items():
        try:
            flags = youtube.verify_disclosure(channel_id, video_ids)
        except Exception as exc:  # noqa: BLE001 - advisory check
            log.warning("could not verify disclosure on channel %s: %s", channel_id, exc)
            continue
        undisclosed = [vid for vid in video_ids if flags.get(vid) is not True]
        if undisclosed:
            result.notes.append(
                f"WARNING: YouTube does not report the AI disclosure on {undisclosed}. "
                "Set it by hand in YouTube Studio (Altered or synthetic content)."
            )


def channels_with_private_posts() -> dict[str, int]:
    with db.session() as conn:
        counts: dict[str, int] = {}
        for post in db.private_posts(conn):
            counts[post["channel_id"]] = counts.get(post["channel_id"], 0) + 1
    return counts


def summary_line() -> str:
    counts = channels_with_private_posts()
    if not counts:
        return "no private posts"
    detail = ", ".join(f"{channel}: {n}" for channel, n in sorted(counts.items()))
    return f"private posts awaiting go-live ({detail})"
