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
#
# `©` and `(c)` sit outside the \b group on purpose: neither starts with a word
# character, so a leading \b can never match and the notice would slip through.
RIGHTS_MARKERS = re.compile(
    r"""(
        © | \(c\)\s*\d
      | \b(?:
            copyright
          | courtesy\s+of
          | used\s+with\s+permission
          | all\s+rights\s+reserved
          | getty | reuters | associated\s+press | shutterstock | adobe\s+stock
          | ESA/Hubble | STScI | AURA | ESO | JAXA | Roscosmos
        )\b
    )""",
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
      | gala | celebration | celebrating | anniversary | tribute | memorial
      | honou?rs | reunion | graduation | welcome
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Finished programmes: explainer segments, interviews, briefings, episodes.
#
# These are public domain and pass every rights check, and they are still
# unusable. They arrive with their own title cards, their own narration, a
# presenter on camera, and the NASA insignia burned into the frame — which is
# trademarked and *not* public domain. Dropping a clip of one under a different
# narration produces a video that contradicts itself.
#
# Found by watching the output: a test episode filled every footage slot with
# "What is a Black Hole | We Asked a NASA Expert", logo and presenter included.
PRODUCED_MARKERS = re.compile(
    r"""(
        \bwe\s+asked\b | \bsciencecasts?\b | \bnasa\s+explorers\b
      | \bepisode\b | \bep\.\s*\d | \bpart\s+\d+\b
      | \bexplained\b | \bexplains\b | \bwhat\s+is\s+a?\b
      | \bbriefing\b | \btown\s+hall\b | \bpanel\b | \bq\s*&\s*a\b
      | \blive\b | \bbroadcast\b | \bteleconference\b
      | \btrailer\b | \bpromo\b | \bpsa\b
      | \|\s*NASA\b | \bhosted\s+by\b | \bnarrated\s+by\b
      | \bdigest\s+series\b | \bspace\s+to\s+ground\b | \bthis\s+week\s+at\s+nasa\b
      | \bmedia\s+reel\b | \bhighlights?\b | \brecap\b | \bcoverage\b
      | \bmilestone\s+for\b | \bcountdown\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# A title that is only a catalogue number, e.g. "KSC-05-S-00032".
#
# These are release packages, not clips: they open with a NASA slate — a
# full-frame insignia over a "MISSION FEATURE" card — and the title carries no
# text a filter could match. Three of them reached a finished test video, logo
# and all, which is how this rule was found.
CATALOGUE_ID_TITLE = re.compile(r"[A-Za-z]{2,6}[-_ ]?\d{2,}[-_A-Za-z0-9 ]*", re.ASCII)

# Charts, plots and multi-panel composites.
#
# These are figures from papers and press kits: white backgrounds, axis labels
# sized for a page, several small panels in one frame. On a dark timeline they
# flash, and at video size nothing in them is readable. A test episode opened on
# a four-panel simulation plot with the subtitle sitting on white.
FIGURE_MARKERS = re.compile(
    r"""\b(
        figure\s*\d | fig\.\s*\d | panels? | plot | chart | graph
      | diagram | schematic | infographic | comparison\s+chart
      | side-?by-?side | before\s+and\s+after | montage | collage | composite\s+of
      | light\s*curve | spectrum | spectra | histogram
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Signals that a clip is raw or rendered imagery rather than a finished piece.
# Used as a preference, not a requirement — some good material says none of this.
RAW_IMAGERY_MARKERS = re.compile(
    r"""\b(
        simulation | visualization | visualisation | animation | render
      | flyover | fly-?through | timelapse | time-?lapse | rotation
      | b-?roll | raw | footage\s+of | view\s+(?:from|of) | orbit(?:ing)?
      | data\s+visualization | model | mosaic | panorama
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
    raw_imagery: bool = False


def assess_rights(item: NasaItem) -> RightsVerdict:
    """Whether this item may be used as raw material on a monetised channel.

    Two different questions, answered together because failing either one means
    the item is unusable: may we legally use it, and is it material rather than
    a finished programme.
    """
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

    match = PRODUCED_MARKERS.search(f"{item.title} {item.description[:400]}")
    if match:
        return RightsVerdict(False, f"a finished programme, not material: {match.group(0)!r}")

    if CATALOGUE_ID_TITLE.fullmatch(item.title.strip()):
        return RightsVerdict(
            False, f"an unlabelled release package: {item.title!r}"
        )

    match = FIGURE_MARKERS.search(f"{item.title} {item.description[:300]}")
    if match:
        return RightsVerdict(False, f"a figure, not an image: {match.group(0)!r}")

    credit = f"NASA/{item.center}" if item.center else "NASA"
    return RightsVerdict(True, credit=credit, raw_imagery=bool(RAW_IMAGERY_MARKERS.search(text)))


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

    # Clips that look like raw imagery come first: they cut together, where a
    # produced segment's title cards and presenters do not.
    kept: list[tuple[bool, NasaItem]] = []
    for item in items:
        verdict = assess_rights(item)
        if verdict.ok:
            kept.append((verdict.raw_imagery, item))
        else:
            log.debug("skipping %s: %s", item.nasa_id, verdict.reason)

    if items and not kept:
        log.warning("every result for %r (%s) failed the rights check", query, media_type)
    return [item for _raw, item in sorted(kept, key=lambda pair: not pair[0])]


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
