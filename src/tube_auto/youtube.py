"""YouTube Data API v3 and Analytics access.

Uploads always set `status.containsSyntheticMedia = True`. That is YouTube's
altered-or-synthetic disclosure field (added 2024-10-30) and it is not optional
here: every frame this pipeline produces is generated, and the EU AI Act's
labelling obligation took effect in August 2026.

Text limits are enforced in **bytes**, not characters. YouTube's 5,000-limit on
descriptions and 500-limit on tags are byte limits, and a Japanese character is
three bytes in UTF-8 — so a character-based truncation passes locally and is
rejected by the API.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any

from . import config, paths

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# videos.insert costs 1600 of the default 10,000 units/day => 6 uploads/day.
UPLOAD_QUOTA_UNITS = 1600
DEFAULT_DAILY_QUOTA_UNITS = 10000
MAX_UPLOADS_PER_DAY = DEFAULT_DAILY_QUOTA_UNITS // UPLOAD_QUOTA_UNITS

TITLE_MAX_BYTES = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_BYTES = 500

UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_UPLOAD_ATTEMPTS = 5


class YouTubeAuthError(RuntimeError):
    """No usable credentials for a channel."""


def truncate_bytes(text: str, max_bytes: int) -> str:
    """Cut a string so its UTF-8 encoding fits, without splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def fit_tags(tags: list[str], max_bytes: int = TAGS_MAX_BYTES) -> list[str]:
    """Keep as many leading tags as fit the combined byte budget.

    YouTube counts the joined length, so dropping from the end preserves the
    tags the caller considered most important.
    """
    kept: list[str] = []
    used = 0
    for tag in tags:
        cost = len(tag.encode("utf-8")) + (1 if kept else 0)
        if used + cost > max_bytes:
            break
        kept.append(tag)
        used += cost
    return kept


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else paths.ROOT / path


def credentials(channel_id: str):
    """Load and refresh a channel's stored OAuth token."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = _resolve(config.channel(channel_id)["token_path"])
    if not token_path.exists():
        raise YouTubeAuthError(
            f"no token for channel '{channel_id}' at {token_path}. "
            f"Run `tube-auto auth --channel {channel_id}` on a machine with a browser "
            "and copy the resulting JSON here (see README)."
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise YouTubeAuthError(
                f"token for '{channel_id}' is invalid and cannot be refreshed; re-run "
                f"`tube-auto auth --channel {channel_id}`"
            )
    return creds


def authorize(channel_id: str) -> Path:
    """Interactive OAuth flow. Needs a browser, so run this locally."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    channels = config.load_channels()
    client_secret = _resolve(channels["client_secret_path"])
    if not client_secret.exists():
        raise YouTubeAuthError(
            f"client secret not found at {client_secret}. Download it from Google Cloud "
            "Console (APIs & Services > Credentials > OAuth client ID, type 'Desktop app')."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = _resolve(config.channel(channel_id)["token_path"])
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return token_path


def data_client(channel_id: str):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=credentials(channel_id), cache_discovery=False)


def analytics_client(channel_id: str):
    from googleapiclient.discovery import build

    return build(
        "youtubeAnalytics", "v2", credentials=credentials(channel_id), cache_discovery=False
    )


def _is_retriable(exc: Exception) -> bool:
    from googleapiclient.errors import HttpError

    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None) in RETRIABLE_STATUS
    # Socket-level interruptions during a resumable upload are safe to retry:
    # the API resumes from the last acknowledged byte.
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _upload_with_retries(request) -> dict[str, Any]:
    """Drive a resumable upload, retrying transient failures with backoff."""
    response = None
    attempt = 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except Exception as exc:  # noqa: BLE001 - classified by _is_retriable
            attempt += 1
            if attempt >= MAX_UPLOAD_ATTEMPTS or not _is_retriable(exc):
                raise
            delay = min(2**attempt + random.random(), 64)
            log.warning(
                "upload chunk failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt, MAX_UPLOAD_ATTEMPTS, delay, exc,
            )
            time.sleep(delay)
    return response


def upload_video(
    channel_id: str,
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy: str,
    category_id: str = "24",
    made_for_kids: bool = False,
    thumbnail_path: Path | None = None,
) -> str:
    """Upload one video. Returns the YouTube video id."""
    from googleapiclient.http import MediaFileUpload

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    service = data_client(channel_id)
    body: dict[str, Any] = {
        "snippet": {
            "title": truncate_bytes(title, TITLE_MAX_BYTES),
            "description": truncate_bytes(description, DESCRIPTION_MAX_BYTES),
            "tags": fit_tags(tags),
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
            # Required disclosure — see the module docstring. Never parameterised.
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(
        str(video_path), chunksize=UPLOAD_CHUNK_BYTES, resumable=True, mimetype="video/mp4"
    )
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = _upload_with_retries(request)

    video_id = response["id"]
    log.info("uploaded %s to channel '%s' as %s", video_path.name, channel_id, video_id)

    if thumbnail_path and thumbnail_path.exists():
        try:
            service.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))
            ).execute()
        except Exception as exc:  # noqa: BLE001 - a missing thumbnail must not fail the upload
            log.warning(
                "thumbnail upload failed for %s (custom thumbnails need a verified "
                "channel): %s", video_id, exc
            )

    return video_id


def set_privacy(channel_id: str, video_id: str, privacy: str) -> None:
    """Flip an uploaded video's visibility.

    Without this the pipeline has no way to make anything visible, so every video
    stays private, earns no views, and the whole measurement loop reports zeros.
    """
    service = data_client(channel_id)
    service.videos().update(
        part="status", body={"id": video_id, "status": {"privacyStatus": privacy}}
    ).execute()
    log.info("video %s on channel '%s' is now %s", video_id, channel_id, privacy)


def ensure_playlist(channel_id: str, title: str, description: str = "") -> str:
    """Find or create a playlist by title, returning its id.

    One playlist per theme. Sessions are what YouTube rewards, and a viewer who
    finishes an episode is far more likely to start another from a playlist than
    from the home page.
    """
    service = data_client(channel_id)
    request = service.playlists().list(part="snippet", mine=True, maxResults=50)
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            if item["snippet"]["title"] == title:
                return item["id"]
        request = service.playlists().list_next(request, response)

    created = (
        service.playlists()
        .insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": "public"},
            },
        )
        .execute()
    )
    log.info("created playlist %r on channel '%s'", title, channel_id)
    return created["id"]


def add_to_playlist(channel_id: str, playlist_id: str, video_id: str) -> None:
    service = data_client(channel_id)
    service.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def verify_disclosure(channel_id: str, video_ids: list[str]) -> dict[str, bool | None]:
    """Read back what YouTube recorded for the synthetic-media disclosure.

    Setting a field in the insert body is not proof it was stored; this closes
    the loop on the one compliance claim the project makes.
    """
    if not video_ids:
        return {}
    service = data_client(channel_id)
    out: dict[str, bool | None] = {}
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start : start + 50]
        response = service.videos().list(part="status", id=",".join(batch)).execute()
        for item in response.get("items", []):
            out[item["id"]] = item.get("status", {}).get("containsSyntheticMedia")
    return out


def fetch_stats(channel_id: str, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Public counters via videos.list (1 unit per call, up to 50 ids)."""
    if not video_ids:
        return {}
    service = data_client(channel_id)
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start : start + 50]
        response = service.videos().list(part="statistics", id=",".join(batch)).execute()
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            out[item["id"]] = {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
            }
    return out


def fetch_retention(
    channel_id: str, video_ids: list[str], start_date: str, end_date: str
) -> dict[str, float]:
    """Average view percentage per video. Requires the Analytics scope.

    The video dimension accepts up to 500 ids per filter, so batches stay well
    inside that.
    """
    if not video_ids:
        return {}
    service = analytics_client(channel_id)
    out: dict[str, float] = {}
    for start in range(0, len(video_ids), 200):
        batch = video_ids[start : start + 200]
        response = (
            service.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="averageViewPercentage",
                dimensions="video",
                filters=f"video=={','.join(batch)}",
            )
            .execute()
        )
        for row in response.get("rows", []):
            out[row[0]] = float(row[1]) / 100.0
    return out
