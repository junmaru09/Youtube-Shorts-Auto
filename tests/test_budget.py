import pytest

from shorts_auto import db
from shorts_auto.budget import BudgetExceeded, BudgetGuard

SETTINGS = {"budget": {"monthly_usd": 10.0, "per_run_usd": 2.0}}


def test_allows_a_charge_within_both_ceilings(temp_db):
    guard = BudgetGuard(temp_db, SETTINGS)
    guard.check(1.0)  # does not raise


def test_per_run_ceiling_stops_the_run(temp_db):
    guard = BudgetGuard(temp_db, SETTINGS)
    guard.commit(1.5)
    with pytest.raises(BudgetExceeded, match="per-run"):
        guard.check(1.0)


def test_monthly_ceiling_counts_spend_already_in_the_ledger(temp_db):
    """The guard must see money spent by earlier runs, not just this one."""
    idea_id = db.insert_idea(
        temp_db,
        series_id="s",
        hook={"ja": "t"},
        video_prompt="p",
        scene_summary="scene",
        tags={},
        dedup_key="k1",
    )
    db.insert_asset(
        temp_db,
        idea_id=idea_id,
        path="/tmp/a.mp4",
        backend="veo",
        model="m",
        duration_s=8,
        cost_usd=9.5,
    )
    temp_db.commit()

    guard = BudgetGuard(temp_db, SETTINGS)
    assert guard.month_to_date == pytest.approx(9.5)
    with pytest.raises(BudgetExceeded, match="monthly"):
        guard.check(1.0)


def test_adhoc_generations_are_billed_too(temp_db):
    """Ad-hoc runs have no idea_id; they must still count against the cap."""
    db.insert_asset(
        temp_db,
        idea_id=None,
        path="/tmp/adhoc.mp4",
        backend="veo",
        model="m",
        duration_s=8,
        cost_usd=9.9,
    )
    temp_db.commit()

    guard = BudgetGuard(temp_db, SETTINGS)
    with pytest.raises(BudgetExceeded, match="monthly"):
        guard.check(0.5)


def test_zero_budget_blocks_everything(temp_db):
    guard = BudgetGuard(temp_db, {"budget": {"monthly_usd": 0.0, "per_run_usd": 0.0}})
    with pytest.raises(BudgetExceeded):
        guard.check(0.01)


def test_remaining_reflects_committed_spend(temp_db):
    guard = BudgetGuard(temp_db, SETTINGS)
    guard.commit(0.5)
    assert guard.run_remaining == pytest.approx(1.5)
    assert guard.monthly_remaining == pytest.approx(9.5)
