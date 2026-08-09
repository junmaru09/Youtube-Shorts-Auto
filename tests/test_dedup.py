from tube_auto import dedup


def test_key_ignores_punctuation_and_case():
    a = dedup.make_key("s", "A Leafcutter Ant, carrying a leaf!")
    b = dedup.make_key("s", "a leafcutter ant carrying a leaf")
    assert a == b


def test_key_is_series_scoped():
    assert dedup.make_key("a", "same scene") != dedup.make_key("b", "same scene")


def test_identical_scenes_are_maximally_similar():
    assert dedup.similarity("a mole tunnelling through soil", "a mole tunnelling through soil") == 1.0


def test_unrelated_scenes_are_not_similar():
    score = dedup.similarity(
        "a mole tunnelling through wet soil",
        "a fishing harbour at dawn with wooden boats",
    )
    assert score < 0.2


def test_reworded_same_shot_is_caught():
    """The failure mode that matters: a paraphrase of an existing video."""
    existing = ["a leafcutter ant carrying a leaf fragment into the colony entrance"]
    reworded = "a leafcutter ant carrying leaf fragment into colony entrance"
    caught, collided = dedup.is_too_similar(reworded, existing)
    assert caught
    assert collided == existing[0]


def test_genuinely_new_scene_passes():
    existing = ["a leafcutter ant carrying a leaf fragment into the colony entrance"]
    caught, _ = dedup.is_too_similar("a trapdoor spider waiting at its burrow lid", existing)
    assert not caught


def test_filler_words_do_not_create_false_collisions():
    """Both are 'close-up shot' scenes; only the subject should decide."""
    left = "macro close-up shot of a scorpion under a rock"
    right = "macro close-up shot of a hermit crab in a shell"
    assert dedup.similarity(left, right) < dedup.SIMILARITY_THRESHOLD


def test_empty_input_is_not_similar():
    assert dedup.similarity("", "anything at all") == 0.0
