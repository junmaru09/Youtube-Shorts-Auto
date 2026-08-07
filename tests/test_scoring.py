import pytest

from shorts_auto.scoring import compute_allocation

IDS = ["a", "b", "c"]


def test_uniform_while_any_arm_is_undersampled():
    """Three data points per genre is noise; do not let it steer the budget."""
    scores = {"a": [5.0] * 10, "b": [1.0] * 10, "c": [3.0] * 2}
    alloc = compute_allocation(IDS, scores, min_samples=10, min_share=0.15)
    assert alloc == {"a": pytest.approx(1 / 3), "b": pytest.approx(1 / 3), "c": pytest.approx(1 / 3)}


def test_weights_follow_performance_once_sampled():
    scores = {"a": [6.0] * 10, "b": [3.0] * 10, "c": [1.0] * 10}
    alloc = compute_allocation(IDS, scores, min_samples=10, min_share=0.15)
    assert alloc["a"] > alloc["b"] > alloc["c"]
    assert sum(alloc.values()) == pytest.approx(1.0)


def test_every_arm_keeps_a_floor():
    """A losing genre must keep getting samples, or the experiment stops learning."""
    scores = {"a": [100.0] * 10, "b": [0.01] * 10, "c": [0.01] * 10}
    alloc = compute_allocation(IDS, scores, min_samples=10, min_share=0.15)
    assert all(share >= 0.15 - 1e-9 for share in alloc.values())


def test_the_floor_never_locks_up_more_than_half_the_budget():
    """With many arms a flat 15% floor would reserve everything and steer nothing."""
    ids = [f"s{i}" for i in range(5)]
    scores = {sid: [1.0] * 10 for sid in ids}
    scores["s0"] = [1000.0] * 10
    alloc = compute_allocation(ids, scores, min_samples=10, min_share=0.15)
    assert sum(alloc.values()) == pytest.approx(1.0)
    # 0.15 x 5 = 0.75 would leave the winner barely ahead; the cap must free it up.
    assert alloc["s0"] > 0.5


def test_a_clear_winner_gets_a_clear_majority():
    """Linear scoring must let a genre that wins on views actually take budget."""
    scores = {"a": [30000.0] * 10, "b": [400.0] * 10, "c": [300.0] * 10}
    alloc = compute_allocation(IDS, scores, min_samples=10, min_share=0.15)
    assert alloc["a"] > 0.6


def test_all_zero_scores_fall_back_to_uniform():
    scores = {sid: [0.0] * 10 for sid in IDS}
    alloc = compute_allocation(IDS, scores, min_samples=10, min_share=0.15)
    assert alloc == {sid: pytest.approx(1 / 3) for sid in IDS}


def test_impossible_floor_cannot_produce_negative_shares():
    """0.4 x 3 arms > 1.0 — a misconfiguration must be clamped, not overflow."""
    scores = {"a": [50.0] * 10, "b": [5.0] * 10, "c": [1.0] * 10}
    alloc = compute_allocation(IDS, scores, min_samples=10, min_share=0.4)
    assert all(share > 0 for share in alloc.values())
    assert sum(alloc.values()) == pytest.approx(1.0)


def test_no_series_returns_empty():
    assert compute_allocation([], {}, min_samples=10, min_share=0.15) == {}
