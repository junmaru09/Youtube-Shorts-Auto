"""YouTube Data API v3 and Analytics access.

Uploads always set `status.containsSyntheticMedia = True`. That is YouTube's
altered-or-synthetic disclosure field (added 2024-10-30) and it is not
optional here: every frame this pipeline produces is generated, and the EU AI
Act's labelling obligation took effect in August 2026.
"""

from __future__ import annotations

import logging
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


class YouTubeAuthError(RuntimeError):
    """No usable credentials for a channel."""


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
            f"Run `shorts-auto auth --channel {channel_id}` on a machine with a browser "
            "and copy the resulting JSON here (see README)."
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise YouTubeAuthError(
                f"token for '{channel_id}' is invalid and cannot be refreshed; re-run auth"
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


def upload_video(
    channel_id: str,
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy: str,
    category_id: str = "24",
    contains_synthetic_media: bool = True,
    thumbnail_path: Path | None = None,
) -> str:
    """Upload one video. Returns the YouTube video id."""
    from googleapiclient.http import MediaFileUpload

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    service = data_client(channel_id)
    body: dict[str, Any] = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            # Required disclosure — see the module docstring.
            "containsSyntheticMedia": contains_synthetic_media,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response["id"]
    log.info("uploaded %s to channel '%s' as %s", video_path.name, channel_id, video_id)

    if thumbnail_path and thumbnail_path.exists():
        try:
            service.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))
            ).execute()
        except Exception as exc:  # noqa: BLE001 - a missing thumbnail must not fail the upload
            log.warning("thumbnail upload failed for %s: %s", video_id, exc)

    return video_id


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


def fetch_retention(channel_id: str, video_ids: list[str], start_date: str, end_date: str) -> dict[str, float]:
    """Average view percentage per video. Requires the Analytics scope."""
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
