"""A/B allocation across series.

Each series is one arm. Until every arm has enough samples the split stays
uniform — with three data points per genre any weighting is noise, and the
whole point of policy C is to buy a trustworthy read, not to converge fast.

Scoring is linear in views on purpose. Shorts revenue is linear in views and
the view distribution is long-tailed, so the genre worth backing is the one
that produces occasional huge hits — not the one with the best median. A log
score would compress exactly the signal that decides the outcome.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from . import config, db


def _report_cfg() -> dict:
    return config.load_settings().get("report", {})


def series_scores(conn: sqlite3.Connection, window: str = "72h") -> dict[str, list[float]]:
    """Per-series list of per-video scores: views discounted by retention.

    Retention is a straight multiplier — views that people swipe away from
    still count for revenue, but they are worth less to the recommender, so a
    video is scored on views it held rather than views it was served.
    """
    scores: dict[str, list[float]] = defaultdict(list)
    for row in db.series_stats(conn, window=window):
        # Missing retention (Analytics scope unavailable) must not zero the arm.
        retention = float(row["avg_view_pct"]) or 1.0
        scores[row["series_id"]].append(float(row["views"]) * retention)
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

    # Explore first: any under-sampled arm keeps the whole split uniform.
    if any(len(scores.get(sid, [])) < min_samples for sid in series_ids):
        return uniform

    means = {sid: sum(scores[sid]) / len(scores[sid]) for sid in series_ids}
    total = sum(means.values())
    if total <= 0:
        return uniform

    # Cap the floor so at most half the budget is locked up regardless of how
    # many arms exist. With five series a naive 15% floor would reserve 75%,
    # leaving the results almost no room to steer anything.
    effective_floor = min(min_share, 0.5 / n)
    free = 1.0 - effective_floor * n
    return {sid: effective_floor + free * (means[sid] / total) for sid in series_ids}


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
