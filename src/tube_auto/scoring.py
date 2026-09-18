"""A/B allocation across arms, where an arm is one (series, language) pair.

Three decisions here, each made because the obvious alternative was wrong:

**Scoring is linear in views.** Shorts revenue is linear in views and the view
distribution is long-tailed, so the genre worth backing is the one that produces
occasional huge hits, not the one with the best median. A log score compressed
exactly the signal that decides the outcome.

**Missing retention falls back to the measured average, never to 1.0.** Treating
an absent Analytics row as "everyone watched to the end" made unmeasured arms
outscore measured ones by roughly 2x, so a measurement outage became a budget
distortion.

**Sampling is stateful.** Recomputing a per-run quota from scratch meant that at
three ideas per day only the top three arms were ever drawn, and the exploration
floor those low-share arms depended on never produced a single video. The sampler
now picks whichever arm is furthest below its target share of everything made so
far, so the floor is real.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from . import config, db
from .models import Arm

# Applied when an arm has no retention measurements at all and neither does
# anything else — a neutral multiplier that cancels out across arms.
NEUTRAL_RETENTION = 0.5


def _report_cfg() -> dict:
    return config.load_settings().get("report", {})


def arm_measurements(
    conn: sqlite3.Connection, window: str = "72h"
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Per-arm views and (separately) per-arm measured retention.

    Kept apart so a video with views but no retention row still contributes its
    views, without inventing a retention figure for it.
    """
    views: dict[str, list[float]] = defaultdict(list)
    retention: dict[str, list[float]] = defaultdict(list)
    for row in db.arm_stats(conn, window=window):
        key = Arm(series_id=row["series_id"], lang=row["lang"]).key
        views[key].append(float(row["views"]))
        if row["avg_view_pct"] is not None:
            retention[key].append(float(row["avg_view_pct"]))
    return dict(views), dict(retention)


def compute_scores(
    views: dict[str, list[float]], retention: dict[str, list[float]]
) -> dict[str, float]:
    """Mean views per video, discounted by the arm's measured retention.

    An arm with no retention data borrows the average across all measured arms,
    so an outage neither rewards nor punishes it.
    """
    measured = [value for values in retention.values() for value in values]
    global_retention = sum(measured) / len(measured) if measured else NEUTRAL_RETENTION

    scores: dict[str, float] = {}
    for key, arm_views in views.items():
        if not arm_views:
            continue
        arm_retention = retention.get(key)
        factor = (
            sum(arm_retention) / len(arm_retention) if arm_retention else global_retention
        )
        scores[key] = (sum(arm_views) / len(arm_views)) * factor
    return scores


def compute_allocation(
    arm_keys: list[str],
    scores: dict[str, float],
    sample_counts: dict[str, int],
    *,
    min_samples: int,
    min_share: float,
) -> dict[str, float]:
    """Turn scores into target shares that sum to 1.

    Pure function so the weighting logic is testable without a database.
    """
    if not arm_keys:
        return {}

    n = len(arm_keys)
    uniform = {key: 1.0 / n for key in arm_keys}

    # Explore first: any under-sampled arm keeps the whole split uniform.
    if any(sample_counts.get(key, 0) < min_samples for key in arm_keys):
        return uniform

    positive = {key: max(0.0, scores.get(key, 0.0)) for key in arm_keys}
    total = sum(positive.values())
    if total <= 0:
        return uniform

    # Cap the floor so at most half the budget is locked up regardless of how many
    # arms exist. With six arms a naive 15% floor would reserve 90%, leaving the
    # results almost no room to steer anything.
    effective_floor = min(min_share, 0.5 / n)
    free = 1.0 - effective_floor * n
    return {key: effective_floor + free * (positive[key] / total) for key in arm_keys}


def allocation(window: str = "72h") -> dict[str, float]:
    """Current target share per arm, read from the live database."""
    arm_keys = [arm.key for arm in config.arms()]
    cfg = _report_cfg()
    with db.session() as conn:
        views, retention = arm_measurements(conn, window=window)
        counts = {
            Arm(series_id=series, lang=lang).key: n
            for (series, lang), n in db.idea_counts_by_arm(conn).items()
        }
    return compute_allocation(
        arm_keys,
        compute_scores(views, retention),
        {key: len(values) for key, values in views.items()},
        min_samples=int(cfg.get("min_samples_before_weighting", 10)),
        min_share=float(cfg.get("min_weight_share", 0.15)),
    ) if arm_keys else {}


def pick_by_deficit(
    shares: dict[str, float], counts: dict[str, int], count: int
) -> list[str]:
    """Choose `count` arms, each time taking the one furthest below its target.

    Deterministic and stateful via `counts`, which is why a 10%-share arm still
    gets made: its deficit grows every time it is skipped until it wins.
    """
    if not shares or count <= 0:
        return []

    running = {key: counts.get(key, 0) for key in shares}
    total = sum(running.values())
    picks: list[str] = []

    for _ in range(count):
        total += 1
        # Ties break on the arm key so runs are reproducible.
        chosen = min(
            shares,
            key=lambda key: (running[key] - shares[key] * total, key),
        )
        picks.append(chosen)
        running[chosen] += 1
    return picks


def sample_arms(count: int) -> list[str]:
    """Pick `count` arm keys according to the current allocation and history."""
    shares = allocation()
    if not shares:
        return []
    with db.session() as conn:
        counts = {
            Arm(series_id=series, lang=lang).key: n
            for (series, lang), n in db.idea_counts_by_arm(conn).items()
        }
    return pick_by_deficit(shares, counts, count)
