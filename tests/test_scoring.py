from collections import Counter

import pytest

from shorts_auto.scoring import compute_allocation, compute_scores, pick_by_deficit

ARMS = ["a:ja", "b:ja", "c:ja"]
SAMPLED = {key: 10 for key in ARMS}


# --- allocation ---------------------------------------------------------------


def test_uniform_while_any_arm_is_undersampled():
    """Three data points per genre is noise; do not let it steer the budget."""
    scores = {"a:ja": 5.0, "b:ja": 1.0, "c:ja": 3.0}
    counts = {"a:ja": 10, "b:ja": 10, "c:ja": 2}
    alloc = compute_allocation(ARMS, scores, counts, min_samples=10, min_share=0.15)
    assert alloc == {key: pytest.approx(1 / 3) for key in ARMS}


def test_weights_follow_performance_once_sampled():
    scores = {"a:ja": 6.0, "b:ja": 3.0, "c:ja": 1.0}
    alloc = compute_allocation(ARMS, scores, SAMPLED, min_samples=10, min_share=0.15)
    assert alloc["a:ja"] > alloc["b:ja"] > alloc["c:ja"]
    assert sum(alloc.values()) == pytest.approx(1.0)


def test_every_arm_keeps_a_floor():
    """A losing genre must keep getting samples, or the experiment stops learning."""
    scores = {"a:ja": 100.0, "b:ja": 0.01, "c:ja": 0.01}
    alloc = compute_allocation(ARMS, scores, SAMPLED, min_samples=10, min_share=0.15)
    assert all(share >= 0.15 - 1e-9 for share in alloc.values())


def test_the_floor_never_locks_up_more_than_half_the_budget():
    """With many arms a flat 15% floor would reserve everything and steer nothing."""
    arms = [f"s{i}:ja" for i in range(6)]
    scores = {key: 1.0 for key in arms}
    scores["s0:ja"] = 1000.0
    alloc = compute_allocation(
        arms, scores, {key: 10 for key in arms}, min_samples=10, min_share=0.15
    )
    assert sum(alloc.values()) == pytest.approx(1.0)
    assert alloc["s0:ja"] > 0.5


def test_a_clear_winner_gets_a_clear_majority():
    scores = {"a:ja": 30000.0, "b:ja": 400.0, "c:ja": 300.0}
    alloc = compute_allocation(ARMS, scores, SAMPLED, min_samples=10, min_share=0.15)
    assert alloc["a:ja"] > 0.6


def test_all_zero_scores_fall_back_to_uniform():
    alloc = compute_allocation(
        ARMS, dict.fromkeys(ARMS, 0.0), SAMPLED, min_samples=10, min_share=0.15
    )
    assert alloc == {key: pytest.approx(1 / 3) for key in ARMS}


def test_impossible_floor_cannot_produce_negative_shares():
    scores = {"a:ja": 50.0, "b:ja": 5.0, "c:ja": 1.0}
    alloc = compute_allocation(ARMS, scores, SAMPLED, min_samples=10, min_share=0.4)
    assert all(share > 0 for share in alloc.values())
    assert sum(alloc.values()) == pytest.approx(1.0)


# --- retention handling -------------------------------------------------------


def test_missing_retention_does_not_read_as_perfect():
    """An unmeasured arm scored as 100% retention outranked measured arms with
    identical views — a measurement outage became a budget distortion."""
    views = {"measured:ja": [1000.0] * 3, "unmeasured:ja": [1000.0] * 3}
    retention = {"measured:ja": [0.5, 0.5, 0.5]}
    scores = compute_scores(views, retention)
    assert scores["unmeasured:ja"] == pytest.approx(scores["measured:ja"])


def test_measured_low_retention_is_penalised():
    views = {"good:ja": [1000.0], "bad:ja": [1000.0]}
    retention = {"good:ja": [0.8], "bad:ja": [0.2]}
    scores = compute_scores(views, retention)
    assert scores["good:ja"] > scores["bad:ja"]


def test_scoring_is_linear_in_views():
    """Shorts revenue is linear in views and the distribution is long-tailed, so
    a log score would compress exactly the signal that decides the outcome."""
    views = {"rare_hit:ja": [0.0, 0.0, 300_000.0], "steady:ja": [1000.0, 1000.0, 1000.0]}
    scores = compute_scores(views, {})
    assert scores["rare_hit:ja"] > scores["steady:ja"] * 10


def test_no_measurements_anywhere_yields_no_scores():
    assert compute_scores({}, {}) == {}


# --- sampling -----------------------------------------------------------------


SHARES = {"micro:ja": 0.35, "mini:ja": 0.27, "asmr:ja": 0.14, "surv:ja": 0.14, "retro:ja": 0.10}


def _simulate(days: int, per_day: int) -> Counter:
    counts: Counter = Counter()
    for _ in range(days):
        counts.update(pick_by_deficit(SHARES, dict(counts), per_day))
    return counts


def test_low_share_arms_are_actually_sampled():
    """The stateless sampler gave the bottom two arms zero draws forever, which
    silently cancelled the exploration floor they depended on."""
    counts = _simulate(days=10, per_day=3)
    assert all(counts[key] > 0 for key in SHARES), dict(counts)


def test_sampling_converges_to_the_target_shares():
    counts = _simulate(days=30, per_day=3)
    total = sum(counts.values())
    for key, target in SHARES.items():
        assert counts[key] / total == pytest.approx(target, abs=0.02)


def test_sampling_respects_history_across_runs():
    """Each run must see what earlier runs already made, or it restarts the
    quota and only ever picks the top arms."""
    counts = {"micro:ja": 100, "mini:ja": 0, "asmr:ja": 0, "surv:ja": 0, "retro:ja": 0}
    picks = pick_by_deficit(SHARES, counts, 3)
    assert "micro:ja" not in picks


def test_single_pick_goes_to_the_biggest_deficit():
    counts = {"micro:ja": 10, "mini:ja": 10, "asmr:ja": 0, "surv:ja": 10, "retro:ja": 10}
    assert pick_by_deficit(SHARES, counts, 1) == ["asmr:ja"]


def test_no_arms_returns_nothing():
    assert pick_by_deficit({}, {}, 3) == []
    assert pick_by_deficit(SHARES, {}, 0) == []
