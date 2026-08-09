"""Stage 4: fill the timeline with NASA material.

The library is lopsided. There are 166,793 stills but only 7,559 videos, and the
videos cluster around missions: Artemis has 1,892, Mars 1,619, while black holes
have 51, supernovae 20 and gravitational waves 3. A theory-heavy episode would
exhaust its footage in one video.

So footage is an accent, not the substrate. This stage takes what NASA actually
has for each chapter, lays it against the narration timeline, and stops. Whatever
time it could not cover is left for `diagrams`, which generates rather than
searches and therefore never runs out.

Nothing without a clean rights verdict is downloaded.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .. import brand as brand_mod
from .. import config, db, nasa, paths
from ..models import MediaAsset

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Slot:
    """One cut: a span of the timeline that needs something on screen."""

    chapter: int
    start_s: float
    end_s: float
    kind: str  # footage | still | diagram

    @property
    def seconds(self) -> float:
        return self.end_s - self.start_s


@dataclass(slots=True)
class FootageResult:
    sourced: int = 0
    failed: int = 0
    clips: int = 0
    stills: int = 0
    covered_ratio: float = 0.0
    errors: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)


def plan_cuts(
    spans: list[dict[str, Any]],
    composition: dict[str, float],
    cut_seconds: dict[str, int],
    footage_heavy: frozenset[str],
    chapter_keys: list[str],
) -> list[Slot]:
    """Divide each chapter into cuts and decide what kind of visual each wants.

    Every cut gets a kind here, including the diagram ones. Planning all three
    together is what keeps the mix honest: if footage and stills were planned
    over the whole timeline, diagrams would only ever appear where NASA happened
    to come up short, rather than where the explanation actually needs a figure.

    Cuts are sized from the middle of the configured range so a chapter never
    ends with a two-second orphan.
    """
    low = int(cut_seconds.get("min", 6))
    high = int(cut_seconds.get("max", 14))
    nominal = (low + high) / 2

    weights = {
        "footage": float(composition.get("footage", 0.30)),
        "still": float(composition.get("still", 0.40)),
        "diagram": float(composition.get("diagram", 0.25)),
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return []

    # Cut the whole timeline first, then assign kinds across all of it. Deciding
    # per chapter starves the smallest kind wherever a chapter yields only one
    # or two cuts — a seven-chapter test video ended up with no diagrams at all.
    spans_with_cuts: list[tuple[int, str, float, float]] = []
    for span in spans:
        index = span["chapter"]
        key = chapter_keys[index] if index < len(chapter_keys) else ""
        duration = span["end_s"] - span["start_s"]
        if duration <= 0:
            continue
        count = max(1, round(duration / nominal))
        length = duration / count
        for i in range(count):
            start = span["start_s"] + i * length
            spans_with_cuts.append((index, key, start, start + length))

    if not spans_with_cuts:
        return []

    kinds = _interleave(weights, len(spans_with_cuts))

    slots = [
        Slot(chapter=index, start_s=start, end_s=end, kind=kind)
        for (index, _key, start, end), kind in zip(spans_with_cuts, kinds)
    ]

    # Mission chapters can lean on real footage; theory chapters cannot, because
    # the library does not hold it. Every other still in those chapters becomes
    # footage — converting all of them would both starve the chapter of variety
    # and burn through a topic's small stock of clips in one video.
    swapped = 0
    for slot, (_index, key, _start, _end) in zip(slots, spans_with_cuts):
        if key in footage_heavy and slot.kind == "still":
            swapped += 1
            if swapped % 2:
                slot.kind = "footage"

    return slots


def _interleave(weights: dict[str, float], count: int) -> list[str]:
    """Spread `count` cuts across kinds in proportion, without clustering.

    Emits whichever kind is furthest behind its share — the same idea the theme
    sampler uses, and for the same reason: three stills in a row is three stills
    too many.
    """
    total = sum(weights.values())
    if total <= 0 or count <= 0:
        return []

    emitted = dict.fromkeys(weights, 0)
    order: list[str] = []
    for step in range(1, count + 1):
        kind = max(
            weights,
            key=lambda k: (weights[k] / total * step - emitted[k], weights[k], k),
        )
        order.append(kind)
        emitted[kind] += 1
    return order


def _queries(chapter_titles: list[str], intents: list[str], theme) -> list[str]:
    """Search terms, most specific first, falling back to the theme's own."""
    queries = [i.strip() for i in intents if i.strip()]
    queries += [q for q in theme.nasa_queries if q not in queries]
    return queries or list(theme.nasa_queries)


def gather_material(
    queries: list[str], media_type: str, wanted: int, page_size: int = 40
) -> list[nasa.NasaItem]:
    """Collect distinct cleared items, widening the search until there are enough."""
    seen: set[str] = set()
    found: list[nasa.NasaItem] = []
    for query in queries:
        if len(found) >= wanted:
            break
        try:
            items = nasa.search(query, media_type, page_size=page_size)
        except nasa.NasaError as exc:
            log.warning("NASA search for %r failed: %s", query, exc)
            continue
        for item in items:
            if item.nasa_id in seen:
                continue
            seen.add(item.nasa_id)
            found.append(item)
            if len(found) >= wanted:
                break
    return found


def _download(item: nasa.NasaItem, media_type: str, index: int) -> MediaAsset | None:
    """Fetch one item, returning None rather than raising on a single failure."""
    verdict = nasa.assess_rights(item)
    if not verdict.ok:
        log.debug("refusing %s: %s", item.nasa_id, verdict.reason)
        return None

    try:
        urls = nasa.asset_urls(item.nasa_id)
    except nasa.NasaError as exc:
        log.warning("could not list assets for %s: %s", item.nasa_id, exc)
        return None

    url = nasa.pick_rendition(urls, media_type)
    if not url:
        return None

    directory = paths.FOOTAGE_DIR if media_type == "video" else paths.STILLS_DIR
    suffix = ".mp4" if media_type == "video" else url.rsplit(".", 1)[-1].split("?")[0][:4]
    destination = directory / f"{item.nasa_id.replace('/', '_')[:80]}.{suffix.lstrip('.')}"

    try:
        nasa.download(url, destination)
    except (nasa.NasaError, OSError) as exc:
        # One missing asset is normal; the caller falls back to diagrams.
        log.warning("download failed for %s: %s", item.nasa_id, exc)
        return None

    return MediaAsset(
        kind="footage" if media_type == "video" else "still",
        path=str(destination),
        order_idx=index,
        source_url=item.page_url,
        nasa_id=item.nasa_id,
        credit=verdict.credit,
        license_ok=True,
        meta={
            "title": item.title,
            "center": item.center,
            "date": item.date_created,
            "asset_url": url,
        },
    )


def run(limit: int = 1, idea_id: int | None = None) -> FootageResult:
    """Source visuals for narrated ideas."""
    from .narrate import chapter_spans

    settings = config.load_settings()
    video_cfg = settings.get("video", {})
    composition = video_cfg.get("composition", {})
    cut_seconds = video_cfg.get("cut_seconds", {})
    chapter_keys = [c.key for c in brand_mod.EPISODE_PLAN]

    result = FootageResult()
    with db.session() as conn:
        if idea_id is not None:
            row = db.get_idea(conn, idea_id)
            ideas = [row] if row else []
        else:
            ideas = db.ideas_by_status(conn, "narrated", limit=limit)

        if not ideas:
            log.info("no ideas with status 'narrated'")
            return result

        for idea in ideas:
            current_id = int(idea["id"])
            narration = db.get_narration(conn, current_id)
            script_row = db.get_script(conn, current_id)
            if narration is None or script_row is None:
                result.failed += 1
                result.errors.append(f"idea {current_id}: narration or script missing")
                continue

            timeline = json.loads(narration["timeline_json"])
            chapters = json.loads(script_row["chapters_json"])
            spans = chapter_spans(timeline)
            theme = config.theme_by_id(idea["series_id"])

            slots = plan_cuts(
                spans, composition, cut_seconds, brand_mod.FOOTAGE_HEAVY, chapter_keys
            )
            if not slots:
                result.failed += 1
                result.errors.append(f"idea {current_id}: timeline produced no cuts")
                continue

            assets = _fill(slots, chapters, theme, result)
            db.replace_assets(
                conn, current_id, "footage", [a.as_dict() for a in assets if a.kind == "footage"]
            )
            db.replace_assets(
                conn, current_id, "still", [a.as_dict() for a in assets if a.kind == "still"]
            )
            # Reserve the diagram slots now, while the visual mix is being
            # decided, and let `diagrams` render into them afterwards.
            db.replace_assets(
                conn, current_id, "diagram", [_reserve(s, i) for i, s in
                                              enumerate(s for s in slots if s.kind == "diagram")]
            )
            db.set_idea_status(conn, current_id, "sourced")
            conn.commit()

            covered = sum(a.duration_s for a in assets)
            total = spans[-1]["end_s"] if spans else 0.0
            ratio = covered / total if total else 0.0

            result.sourced += 1
            result.clips += sum(1 for a in assets if a.kind == "footage")
            result.stills += sum(1 for a in assets if a.kind == "still")
            result.covered_ratio = ratio
            result.details.append(
                {"idea_id": current_id, "assets": len(assets), "covered": ratio}
            )
            log.info(
                "idea %d sourced: %d clips, %d stills, %.0f%% of the timeline covered "
                "(diagrams fill the rest)",
                current_id,
                sum(1 for a in assets if a.kind == "footage"),
                sum(1 for a in assets if a.kind == "still"),
                ratio * 100,
            )

    return result


def _reserve(slot: Slot, order: int) -> dict[str, Any]:
    """An empty diagram slot, waiting for `diagrams` to render into it."""
    return MediaAsset(
        kind="diagram",
        path="",
        chapter=slot.chapter,
        order_idx=order,
        duration_s=slot.seconds,
        credit="",
        license_ok=True,  # generated in-house; nothing to clear
        meta={"start_s": round(slot.start_s, 3), "end_s": round(slot.end_s, 3)},
    ).as_dict()


def _fill(slots: list[Slot], chapters: list[dict], theme, result: FootageResult) -> list[MediaAsset]:
    """Attach real material to as many slots as NASA can actually supply."""
    by_chapter: dict[int, list[Slot]] = {}
    for slot in slots:
        by_chapter.setdefault(slot.chapter, []).append(slot)

    assets: list[MediaAsset] = []
    for chapter_index, chapter_slots in sorted(by_chapter.items()):
        chapter = chapters[chapter_index] if chapter_index < len(chapters) else {}
        queries = _queries(
            [chapter.get("title", "")], [chapter.get("visual_intent", "")], theme
        )

        for media_type, kind in (("video", "footage"), ("image", "still")):
            wanted = sum(1 for s in chapter_slots if s.kind == kind)
            if wanted == 0:
                continue

            items = gather_material(queries, media_type, wanted)
            if not items:
                log.info(
                    "no %s available for chapter %d (%s); diagrams will cover it",
                    kind, chapter_index, queries[0] if queries else "?",
                )
                continue

            targets = [s for s in chapter_slots if s.kind == kind]
            for index, slot in enumerate(targets):
                item = items[index % len(items)]
                asset = _download(item, media_type, index)
                if asset is None:
                    continue
                asset.chapter = chapter_index
                asset.duration_s = slot.seconds
                asset.meta["start_s"] = round(slot.start_s, 3)
                asset.meta["end_s"] = round(slot.end_s, 3)
                assets.append(asset)

    return assets
