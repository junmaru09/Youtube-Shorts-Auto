"""NASA Image and Video Library, with the rights checks the licence requires.

NASA's own content carries no US copyright and may be used commercially. Three
things in the library are not that, and all three would be a rights problem on a
monetised channel:

- **Third-party work.** NASA hosts material it licensed from others. The
  guidelines say such items are "identified as copyright protected with the name
  of the copyright holder", so anything whose metadata names a rights holder is
  dropped.
- **The NASA insignia and logotype.** Explicitly not public domain.
- **Recognisable people.** Using someone's likeness can infringe privacy or
  publicity rights regardless of who owns the footage, and NASA singles out
  current employees.

Two further conditions are handled elsewhere but recorded here: the channel must
not imply NASA endorses it (`brand.credit_line`), and NASA must be credited
somewhere a viewer can see (`stages/publish.py` puts it in the description head).

The API needs no key and no account.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://images-api.nasa.gov"
TIMEOUT = 60
USER_AGENT = "tube-auto/0.1 (educational science channel)"

# Preferred rendition order. `orig` is often 200MB+ for a two-minute clip, which
# is bandwidth spent on detail that a 1080p timeline throws away.
VIDEO_RENDITIONS = ("large", "medium", "orig", "mobile", "small")
IMAGE_RENDITIONS = ("orig", "large", "medium", "small")

# Metadata that means someone other than NASA holds rights.
RIGHTS_MARKERS = re.compile(
    r"""\b(
        copyright | \(c\) | ©
      | courtesy\s+of
      | used\s+with\s+permission
      | all\s+rights\s+reserved
      | getty | reuters | associated\s+press | shutterstock | adobe\s+stock
      | ESA/Hubble | STScI | AURA | ESO\b | JAXA | Roscosmos
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# The insignia and logotype are trademarked and not public domain.
LOGO_MARKERS = re.compile(r"\b(insignia|meatball|worm\s+logo|nasa\s+logo|logotype)\b", re.IGNORECASE)

# Signals that a person is the subject rather than incidental scenery.
PERSON_MARKERS = re.compile(
    r"""\b(
        astronaut | crew\s+member | portrait | headshot | administrator
      | engineer | scientist(?:s)? \s+ (?:pose|poses|posing)
      | employees | interview | press\s+conference | award | ceremony
      | speaks | speaking | visits | visitors | students
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


class NasaError(RuntimeError):
    """The library could not be queried."""


@dataclass(slots=True)
class NasaItem:
    nasa_id: str
    media_type: str  # image | video
    title: str
    description: str
    keywords: list[str] = field(default_factory=list)
    center: str = ""
    date_created: str = ""
    secondary_creator: str = ""
    page_url: str = ""

    @property
    def haystack(self) -> str:
        return " ".join(
            [self.title, self.description, self.secondary_creator, " ".join(self.keywords)]
        )


@dataclass(slots=True)
class RightsVerdict:
    ok: bool
    reason: str = ""
    credit: str = "NASA"


def assess_rights(item: NasaItem) -> RightsVerdict:
    """Whether this item may be used on a monetised channel."""
    text = item.haystack

    if item.secondary_creator and "nasa" not in item.secondary_creator.lower():
        return RightsVerdict(
            False, f"third-party creator: {item.secondary_creator}", item.secondary_creator
        )

    match = RIGHTS_MARKERS.search(text)
    if match:
        return RightsVerdict(False, f"rights marker in metadata: {match.group(0)!r}")

    match = LOGO_MARKERS.search(text)
    if match:
        return RightsVerdict(False, f"NASA insignia or logotype: {match.group(0)!r}")

    match = PERSON_MARKERS.search(text)
    if match:
        return RightsVerdict(False, f"a person is the subject: {match.group(0)!r}")

    credit = f"NASA/{item.center}" if item.center else "NASA"
    return RightsVerdict(True, credit=credit)


def _safe_url(url: str) -> str:
    """Percent-encode the path, and only the path.

    Asset URLs are built from the item's title, so plenty of them contain spaces
    that http.client rejects outright. The query string is already encoded by
    whoever built it — re-encoding it turns the `+` that means "space" into a
    literal plus and quietly returns zero results.
    """
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(parts.path, safe="/~%"),
            parts.query,
            parts.fragment,
        )
    )


def _get(url: str) -> bytes:
    request = urllib.request.Request(_safe_url(url), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise NasaError(f"NASA request failed for {url}: {exc}") from exc


def _parse_items(payload: dict) -> list[NasaItem]:
    items: list[NasaItem] = []
    for entry in payload.get("collection", {}).get("items", []):
        data = (entry.get("data") or [{}])[0]
        nasa_id = data.get("nasa_id")
        if not nasa_id:
            continue
        items.append(
            NasaItem(
                nasa_id=nasa_id,
                media_type=data.get("media_type", ""),
                title=data.get("title", ""),
                description=data.get("description", "") or data.get("description_508", "") or "",
                keywords=list(data.get("keywords") or []),
                center=data.get("center", "") or "",
                date_created=(data.get("date_created") or "")[:10],
                secondary_creator=data.get("secondary_creator", "") or "",
                page_url=f"https://images.nasa.gov/details/{urllib.parse.quote(nasa_id)}",
            )
        )
    return items


def search(
    query: str,
    media_type: str = "image",
    *,
    page_size: int = 40,
    cleared_only: bool = True,
) -> list[NasaItem]:
    """Search the library, dropping anything that fails the rights checks."""
    url = f"{API}/search?" + urllib.parse.urlencode(
        {"q": query, "media_type": media_type, "page_size": page_size}
    )
    payload = json.loads(_get(url))
    items = _parse_items(payload)

    if not cleared_only:
        return items

    kept: list[NasaItem] = []
    for item in items:
        verdict = assess_rights(item)
        if verdict.ok:
            kept.append(item)
        else:
            log.debug("skipping %s: %s", item.nasa_id, verdict.reason)

    if items and not kept:
        log.warning("every result for %r (%s) failed the rights check", query, media_type)
    return kept


def asset_urls(nasa_id: str) -> list[str]:
    """Every file behind one library item."""
    url = f"{API}/asset/{urllib.parse.quote(nasa_id)}"
    payload = json.loads(_get(url))
    return [entry["href"] for entry in payload.get("collection", {}).get("items", [])]


def pick_rendition(urls: list[str], media_type: str) -> str | None:
    """The smallest rendition good enough for a 1080p timeline."""
    suffix = ".mp4" if media_type == "video" else (".jpg", ".png")
    candidates = [u for u in urls if u.lower().endswith(suffix)]
    if not candidates:
        return None

    order = VIDEO_RENDITIONS if media_type == "video" else IMAGE_RENDITIONS
    for label in order:
        for url in candidates:
            if f"~{label}." in url:
                return url
    return candidates[0]


def download(url: str, destination: Path) -> Path:
    """Fetch one asset, skipping the work if it is already cached."""
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = _get(url)
    destination.write_bytes(data)
    return destination


def captions_url(urls: list[str]) -> str | None:
    """The SRT track, when there is one.

    Worth having: it says what happens *inside* a clip, which the title and
    description often do not, and that is what lets footage be matched to a
    specific chapter rather than to a topic.
    """
    for url in urls:
        if url.lower().endswith(".srt"):
            return url
    return None
