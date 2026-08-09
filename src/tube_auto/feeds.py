"""Primary-source feeds: NASA/JPL news and arXiv.

These are the only inputs allowed to supply a fact. The model chooses what to
talk about and how to explain it, but every number in the finished script traces
back to a row this module produced. That is the concrete difference between
this and the summary-rewriting channels YouTube demonetised in early 2026.

Both endpoints are public and free; neither needs a key.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

USER_AGENT = "tube-auto/0.1 (educational science channel; contact via YouTube)"
TIMEOUT = 40

# Verified reachable 2026-08. JPL's own feed returns 403 to non-browser agents,
# so it is left out rather than retried behind a spoofed User-Agent.
NASA_FEEDS = {
    "nasa": "https://www.nasa.gov/feeds/iotd-feed/",
    "nasa_news": "https://www.nasa.gov/news-release/feed/",
    "nasa_science": "https://science.nasa.gov/feed/",
}
ARXIV_API = "http://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(slots=True)
class FeedItem:
    kind: str  # nasa | jpl | arxiv
    url: str
    title: str
    summary: str
    published_at: str | None = None
    authors: str = ""

    @property
    def age_days(self) -> float | None:
        if not self.published_at:
            return None
        try:
            when = datetime.fromisoformat(self.published_at)
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return (datetime.now(UTC) - when).total_seconds() / 86400


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(raw: str, limit: int = 900) -> str:
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw or "")).strip()
    return text[:limit]


def _rss_date(raw: str | None) -> str | None:
    """RSS dates come in several shapes; return ISO or None rather than guess."""
    if not raw:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw.strip(), fmt).astimezone(UTC).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def fetch_nasa_news(max_age_days: int = 365, limit: int = 40) -> list[FeedItem]:
    """Recent NASA and JPL releases. Failures on one feed do not stop the rest."""
    items: list[FeedItem] = []
    for kind, url in NASA_FEEDS.items():
        try:
            root = ET.fromstring(_fetch(url))
        except (urllib.error.URLError, ET.ParseError, OSError) as exc:
            log.warning("feed %s unavailable: %s", kind, exc)
            continue

        for entry in root.iter("item"):
            link = (entry.findtext("link") or "").strip()
            title = clean_text(entry.findtext("title") or "", 300)
            if not link or not title:
                continue
            item = FeedItem(
                kind="nasa",
                url=link,
                title=title,
                summary=clean_text(entry.findtext("description") or ""),
                published_at=_rss_date(entry.findtext("pubDate")),
            )
            age = item.age_days
            if age is not None and age > max_age_days:
                continue
            items.append(item)

    items.sort(key=lambda i: i.published_at or "", reverse=True)
    return items[:limit]


def fetch_arxiv(
    categories: list[str], max_age_days: int = 30, limit: int = 40
) -> list[FeedItem]:
    """Recent preprints in the given categories, newest first."""
    if not categories:
        return []

    query = " OR ".join(f"cat:{c}" for c in categories)
    url = f"{ARXIV_API}?" + urllib.parse.urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    try:
        root = ET.fromstring(_fetch(url))
    except (urllib.error.URLError, ET.ParseError, OSError) as exc:
        log.warning("arXiv query failed: %s", exc)
        return []

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    items: list[FeedItem] = []
    for entry in root.iter(f"{ATOM}entry"):
        link = (entry.findtext(f"{ATOM}id") or "").strip()
        title = clean_text(entry.findtext(f"{ATOM}title") or "", 300)
        published = (entry.findtext(f"{ATOM}published") or "").strip()
        if not link or not title:
            continue
        try:
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            when = None
        if when and when < cutoff:
            continue

        authors = ", ".join(
            (a.findtext(f"{ATOM}name") or "").strip()
            for a in entry.findall(f"{ATOM}author")[:4]
        )
        items.append(
            FeedItem(
                kind="arxiv",
                url=link,
                title=title,
                summary=clean_text(entry.findtext(f"{ATOM}summary") or ""),
                published_at=when.astimezone(UTC).isoformat(timespec="seconds") if when else None,
                authors=authors,
            )
        )
    return items


def gather(
    nasa_queries: list[str],
    arxiv_categories: list[str],
    *,
    exclude_urls: set[str] | None = None,
    nasa_max_age_days: int = 365,
    arxiv_max_age_days: int = 30,
) -> list[FeedItem]:
    """Everything a theme could talk about today, minus what it already covered.

    `nasa_queries` filters the news feed by keyword rather than issuing separate
    searches: the feeds are small enough to fetch whole, and one fetch is kinder
    to a free public endpoint than six.
    """
    seen = exclude_urls or set()
    keywords = [q.lower() for q in nasa_queries]

    news = [
        item
        for item in fetch_nasa_news(max_age_days=nasa_max_age_days)
        if item.url not in seen
        and (
            not keywords
            or any(k in f"{item.title} {item.summary}".lower() for k in keywords)
        )
    ]
    papers = [
        item
        for item in fetch_arxiv(arxiv_categories, max_age_days=arxiv_max_age_days)
        if item.url not in seen
    ]

    log.info("gathered %d news items and %d preprints", len(news), len(papers))
    return news + papers
