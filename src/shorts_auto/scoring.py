"""A/B allocation across series.

Each series is one arm. Until every arm has enough samples the split stays
uniform — with three data points per genre any weighting is noise, and the
whole point of policy C is to buy a trustworthy read, not to converge fast.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict

from . import config, db


def _report_cfg() -> dict:
    return config.load_settings().get("report", {})


def series_scores(conn: sqlite3.Connection, window: str = "72h") -> dict[str, list[float]]:
    """Per-series list of per-video scores: log1p(views) * retention."""
    scores: dict[str, list[float]] = defaultdict(list)
    for row in db.series_stats(conn, window=window):
        retention = float(row["avg_view_pct"]) or 1.0
        scores[row["series_id"]].append(math.log1p(float(row["views"])) * retention)
    return dict(scores)


def compute_allocation(
    series_ids: list[str],
    scores: dict[str, list[float]],
    *,
    min_samples: int,
    min_share: float,
) -> dict[str, float]:
    """Turn raw scores into shares that sum to 1.

    Pure function so the weighting logic is testable without a database.
    """
    if not series_ids:
        return {}

    n = len(series_ids)
    uniform = {sid: 1.0 / n for sid in series_ids}

    # A floor that cannot fit means the caller misconfigured min_weight_share.
    if min_share * n >= 1.0:
        return uniform

    # Explore first: any under-sampled arm keeps the whole split uniform.
    if any(len(scores.get(sid, [])) < min_samples for sid in series_ids):
        return uniform

    means = {sid: sum(scores[sid]) / len(scores[sid]) for sid in series_ids}
    total = sum(means.values())
    if total <= 0:
        return uniform

    free = 1.0 - min_share * n
    return {sid: min_share + free * (means[sid] / total) for sid in series_ids}


def allocation(window: str = "72h") -> dict[str, float]:
    """Current share per enabled series, read from the live database."""
    series_ids = [s.id for s in config.load_series()]
    cfg = _report_cfg()
    with db.session() as conn:
        scores = series_scores(conn, window=window)
    return compute_allocation(
        series_ids,
        scores,
        min_samples=int(cfg.get("min_samples_before_weighting", 10)),
        min_share=float(cfg.get("min_weight_share", 0.15)),
    )


def sample_series(count: int, rng=None) -> list[str]:
    """Pick `count` series ids according to the current allocation.

    Deterministic round-robin over the weighted shares rather than random
    draws, so a run of 3 never accidentally spends everything on one arm.
    """
    shares = allocation()
    if not shares:
        return []

    picks: list[str] = []
    remaining = dict(shares)
    quota = {sid: share * count for sid, share in shares.items()}
    for _ in range(count):
        chosen = max(quota, key=lambda sid: (quota[sid], remaining[sid]))
        picks.append(chosen)
        quota[chosen] -= 1.0
    return picks
