"""Music beds and the audio mix.

The failure this exists to prevent is not a crash — it is an eighteen-minute
video with one two-minute loop under it, which sounds exactly like what it is.
"""

from __future__ import annotations

import subprocess

import pytest

from tube_auto import bgm, brand as brand_mod
from tests.conftest import needs_ffmpeg

CHAPTER_KEYS = [c.key for c in brand_mod.EPISODE_PLAN]
SPANS = [
    {"start_s": 0, "end_s": 86},          # opener
    {"start_s": 86, "end_s": 216},        # context
    {"start_s": 216, "end_s": 224},       # name — a few lines, below the minimum bed length
    {"start_s": 224, "end_s": 386},       # history
    {"start_s": 386, "end_s": 602},       # mechanism1
    {"start_s": 602, "end_s": 818},       # mechanism2
    {"start_s": 818, "end_s": 894},       # replay
    {"start_s": 894, "end_s": 1024},      # open
    {"start_s": 1024, "end_s": 1056},     # close
]


@pytest.fixture
def library(tmp_path):
    """A library with several tracks per mood."""
    root = tmp_path / "bgm"
    for mood in ("tension", "wonder", "calm", "reflective", "resolve"):
        directory = root / mood
        directory.mkdir(parents=True)
        for name in ("a", "b"):
            (directory / f"{name}.mp3").write_bytes(b"not really audio")
    return bgm.BgmLibrary(root)


def _duration(path) -> float:
    return float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ], text=True))


# --- choosing tracks ----------------------------------------------------------


def test_each_chapter_gets_its_own_track(library):
    """A single track stretched over the whole episode loops nine times, and the
    ear catches it around the third."""
    beds = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="1")
    tracks = [b.track for b in beds if b.track]
    assert len(set(tracks)) > 1


def test_two_chapters_in_a_row_never_share_a_track(library):
    beds = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="1")
    tracks = [b.track for b in beds if b.track]
    assert all(a != b for a, b in zip(tracks, tracks[1:]))


def test_the_same_episode_gets_the_same_music_twice(library):
    """A re-render after a rejected review should produce the same episode, not
    a differently-scored one."""
    first = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="7")
    second = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="7")
    assert [b.track for b in first] == [b.track for b in second]


def test_different_episodes_get_different_music(library):
    a = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="1")
    b = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="2")
    assert [x.track for x in a] != [x.track for x in b]


def test_the_mood_follows_the_chapter(library):
    beds = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="1")
    assert [b.mood for b in beds] == [
        bgm.CHAPTER_MOOD[key] for key in CHAPTER_KEYS
    ]


def test_a_chapter_too_short_to_establish_anything_gets_silence(library):
    """Naming the topic takes a few lines. An eight-second sting there is
    worse than nothing."""
    beds = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="1")
    short = CHAPTER_KEYS.index("name")
    assert beds[short].track is None
    assert beds[short].seconds < bgm.MIN_BED_SECONDS


# --- degrading gracefully -----------------------------------------------------


def test_an_unsorted_library_still_works(tmp_path):
    """Six tracks dropped in a folder without mood subdirectories is what most
    operators will actually have on day one."""
    root = tmp_path / "bgm"
    root.mkdir()
    for name in "abcdef":
        (root / f"{name}.mp3").write_bytes(b"x")

    library = bgm.BgmLibrary(root)
    assert not library.empty
    beds = bgm.plan_beds(SPANS, CHAPTER_KEYS, library, seed="1")
    assert all(b.track is not None for b in beds if b.seconds >= bgm.MIN_BED_SECONDS)


def test_a_missing_mood_falls_back_rather_than_failing(tmp_path):
    root = tmp_path / "bgm"
    (root / "calm").mkdir(parents=True)
    (root / "calm" / "only.mp3").write_bytes(b"x")

    library = bgm.BgmLibrary(root)
    assert library.pool("tension") == library.pool("calm")


def test_no_library_means_no_music_not_a_failure(tmp_path):
    bed, beds = bgm.build_bed(SPANS, CHAPTER_KEYS, tmp_path / "w", bgm_dir=tmp_path / "nope")
    assert bed is None
    assert beds == []


# --- the mix ------------------------------------------------------------------


def test_the_mix_does_not_quietly_attenuate_the_narration():
    """amix's default scales every input by 1/n, so adding a music bed drops the
    narration 6 dB before loudnorm ever sees it."""
    assert "normalize=0" in bgm.mix_filter(-22.0, -14.0)


def test_the_bed_is_keyed_to_the_narration():
    """A fixed level is either too loud under speech or inaudible in the gaps."""
    graph = bgm.mix_filter(-22.0, -14.0)
    assert "sidechaincompress" in graph
    assert "asplit" in graph  # the narration is both the mix input and the key


def test_the_audio_is_padded_when_a_closing_card_follows():
    """Without the pad, `-shortest` ends the video at the last spoken word and
    the closing card never appears."""
    assert "apad=pad_dur=11.000" in bgm.mix_filter(-22.0, -14.0, pad_seconds=11.0)
    assert "apad=pad_dur=11.000" in bgm.narration_only_filter(-14.0, pad_seconds=11.0)


def test_no_pad_is_added_when_there_is_no_card():
    assert "apad" not in bgm.mix_filter(-22.0, -14.0)
    assert "apad" not in bgm.narration_only_filter(-14.0)


# --- rendering ------------------------------------------------------------------


@needs_ffmpeg
def test_the_bed_is_exactly_as_long_as_the_episode(tmp_path):
    """`-ss` before the input combined with `-stream_loop` returned a 25-second
    bed for a 13-second chapter, which desynchronised everything after it."""
    root = tmp_path / "bgm" / "calm"
    root.mkdir(parents=True)
    track = root / "tone.wav"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=20", str(track),
    ], check=True)

    spans = [{"start_s": 0, "end_s": 30}, {"start_s": 30, "end_s": 75}]
    bed, plan = bgm.build_bed(
        spans, CHAPTER_KEYS, tmp_path / "w", seed="1", bgm_dir=tmp_path / "bgm"
    )
    assert bed is not None
    # Chapters are longer than the track, so this also covers the looping path.
    assert _duration(bed) == pytest.approx(75.0, abs=0.1)
    assert all(b.track is not None for b in plan)


@needs_ffmpeg
def test_a_bed_shorter_than_its_chapter_is_looped_not_truncated(tmp_path):
    root = tmp_path / "bgm" / "calm"
    root.mkdir(parents=True)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=5", str(root / "short.wav"),
    ], check=True)

    bed = bgm.Bed(chapter=1, mood="calm", track=root / "short.wav", seconds=22.0)
    out = bgm._render_bed(bed, tmp_path / "one.wav")
    assert _duration(out) == pytest.approx(22.0, abs=0.1)
