import pytest

from shorts_auto import db
from shorts_auto.budget import AlreadyRunning, BudgetExceeded, BudgetGuard, exclusive_run

SETTINGS = {"budget": {"monthly_usd": 10.0, "per_run_usd": 2.0}}


def _idea(conn, key="k1"):
    return db.insert_idea(
        conn, series_id="s", lang="ja", hook="t", video_prompt="p",
        scene_summary="scene", tags=[], dedup_key=key,
    )


def test_allows_a_charge_within_both_ceilings(temp_db):
    BudgetGuard(temp_db, SETTINGS).check(1.0)  # does not raise


def test_per_run_ceiling_stops_the_run(temp_db):
    guard = BudgetGuard(temp_db, SETTINGS)
    guard.record(1.5)
    with pytest.raises(BudgetExceeded, match="per-run"):
        guard.check(1.0)


def test_monthly_ceiling_counts_spend_already_in_the_ledger(temp_db):
    idea_id = _idea(temp_db)
    db.insert_generation(
        temp_db, idea_id=idea_id, path="/tmp/a.mp4", backend="veo", model="m",
        duration_s=8, cost_usd=9.5,
    )
    temp_db.commit()

    guard = BudgetGuard(temp_db, SETTINGS)
    assert guard.month_to_date == pytest.approx(9.5)
    with pytest.raises(BudgetExceeded, match="monthly"):
        guard.check(1.0)


def test_llm_spend_counts_against_the_same_ceiling(temp_db):
    """Ideation is cheap but not free; leaving it out understates the total."""
    db.insert_llm_call(
        temp_db, purpose="ideate", model="claude-sonnet-5",
        input_tokens=1000, output_tokens=1000, cost_usd=9.9,
    )
    temp_db.commit()
    with pytest.raises(BudgetExceeded, match="monthly"):
        BudgetGuard(temp_db, SETTINGS).check(0.5)


def test_adhoc_generations_are_billed_too(temp_db):
    """Ad-hoc runs have no idea_id; they must still count against the cap."""
    db.insert_generation(
        temp_db, idea_id=None, path="/tmp/adhoc.mp4", backend="veo", model="m",
        duration_s=8, cost_usd=9.9,
    )
    temp_db.commit()
    with pytest.raises(BudgetExceeded, match="monthly"):
        BudgetGuard(temp_db, SETTINGS).check(0.5)


def test_the_guard_rereads_committed_spend(temp_db):
    """A stale in-memory total is how two runs each spend the full budget."""
    guard = BudgetGuard(temp_db, SETTINGS)
    assert guard.month_to_date == 0.0

    idea_id = _idea(temp_db)
    db.insert_generation(
        temp_db, idea_id=idea_id, path="/tmp/a.mp4", backend="veo", model="m",
        duration_s=8, cost_usd=9.0,
    )
    temp_db.commit()

    assert guard.month_to_date == pytest.approx(9.0)
    with pytest.raises(BudgetExceeded):
        guard.check(2.0)


def test_regenerating_can_never_erase_recorded_spend(temp_db, tmp_path):
    """The defect that made the cap meaningless: the review UI's regenerate
    button deleted the row that recorded money already spent."""
    from shorts_auto import paths

    idea_id = _idea(temp_db)
    really_spent = 0.0
    for round_ in range(20):
        db.insert_generation(
            temp_db, idea_id=idea_id, path=f"/tmp/{round_}.mp4", backend="veo",
            model="veo-3.1-lite-generate-preview", duration_s=8, cost_usd=0.40,
        )
        really_spent += 0.40
        # What the regenerate button does now: drop the derived render only.
        db.upsert_render(
            temp_db, idea_id=idea_id, generation_id=1, path=str(tmp_path / "r.mp4"),
            thumb_path=None, burned_hook="t",
        )
        db.delete_render(temp_db, idea_id)
        temp_db.commit()

    assert paths  # imported for the fixture's side effects
    guard = BudgetGuard(temp_db, {"budget": {"monthly_usd": 75.0, "per_run_usd": 5.0}})
    assert guard.month_to_date == pytest.approx(really_spent)
    assert guard.month_to_date == pytest.approx(8.0)


def test_a_posted_idea_cannot_have_its_render_replaced(temp_db, tmp_path):
    """Re-rendering after upload would leave the file and the published video
    disagreeing, with no way to tell which the audience saw."""
    idea_id = _idea(temp_db)
    gen_id = db.insert_generation(
        temp_db, idea_id=idea_id, path="/tmp/a.mp4", backend="veo", model="m",
        duration_s=8, cost_usd=0.4,
    )
    db.upsert_render(
        temp_db, idea_id=idea_id, generation_id=gen_id, path=str(tmp_path / "r.mp4"),
        thumb_path=None, burned_hook="t",
    )
    db.insert_post(
        temp_db, idea_id=idea_id, channel_id="ja", youtube_video_id="abc",
        title="t", path="r.mp4", privacy="private",
    )
    with pytest.raises(ValueError, match="already been uploaded"):
        db.delete_render(temp_db, idea_id)


def test_zero_budget_blocks_everything(temp_db):
    guard = BudgetGuard(temp_db, {"budget": {"monthly_usd": 0.0, "per_run_usd": 0.0}})
    with pytest.raises(BudgetExceeded):
        guard.check(0.01)


def test_a_second_concurrent_run_is_refused(tmp_path, monkeypatch):
    """Two runs that each read the ledger before either writes would both see
    the full remaining budget."""
    from shorts_auto import paths

    monkeypatch.setattr(paths, "WORK_DIR", tmp_path)
    with exclusive_run("generate"):
        with pytest.raises(AlreadyRunning):
            with exclusive_run("generate"):
                pass


def test_the_lock_is_released_after_a_failure(tmp_path, monkeypatch):
    from shorts_auto import paths

    monkeypatch.setattr(paths, "WORK_DIR", tmp_path)
    with pytest.raises(RuntimeError):
        with exclusive_run("generate"):
            raise RuntimeError("boom")
    with exclusive_run("generate"):
        pass  # acquiring again must succeed
