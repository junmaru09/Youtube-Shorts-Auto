"""Background music, chosen per chapter and ducked under the narration.

One track looped for eighteen minutes is the clearest audio tell of an
automated channel — a two-minute loop repeats nine times and the ear catches it
somewhere around the third. Three things fix that, and all three matter:

- **A different track per chapter.** The episode already has a seven-part
  structure; the music follows it instead of ignoring it.
- **Silence at the chapter seams.** Each bed fades out and the next fades in, so
  the boundary is audible. It lands under the chapter card, which turns a
  transition the viewer only saw into one they also hear.
- **Ducking.** A fixed −22 dB bed is either too loud under speech or inaudible
  in the gaps. Sidechain compression keyed to the narration makes it dip when
  someone is talking and swell when nobody is.

The library is `work/bgm/<mood>/*.mp3`, downloaded by hand from the YouTube
Audio Library — that needs a login and has no API, so it cannot be automated.
A flat `work/bgm/*.mp3` still works and is used for every mood; no library at
all means no music, which is a quiet video rather than a broken one.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ffmpeg

log = logging.getLogger(__name__)

AUDIO_SUFFIXES = (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus")

# Chapter key -> the mood directory its bed comes from. The episode plan is
# fixed, so this mapping is too: the same beat gets the same feel every episode,
# which is what makes a channel recognisable.
CHAPTER_MOOD: dict[str, str] = {
    "hook": "tension",      # a question you do not yet have the answer to
    "intro": "wonder",
    "basis": "calm",        # groundwork; the music should stay out of the way
    "main": "wonder",       # the observation itself
    "detail": "calm",       # numbers, and a lot of them
    "meaning": "reflective",  # what this overturned
    "outro": "resolve",
}
DEFAULT_MOOD = "calm"

FADE_IN_SECONDS = 2.0
FADE_OUT_SECONDS = 2.5
# Below this a chapter is too short to establish anything; silence reads better
# than a two-second sting.
MIN_BED_SECONDS = 12.0


@dataclass(slots=True)
class Bed:
    """One chapter's music."""

    chapter: int
    mood: str
    track: Path | None
    seconds: float


class BgmLibrary:
    """The local music library, indexed by mood."""

    def __init__(self, root: Path):
        self.root = root
        self.by_mood: dict[str, list[Path]] = {}
        self.flat: list[Path] = []
        self._scan()

    def _scan(self) -> None:
        if not self.root.exists():
            return
        for path in sorted(self.root.iterdir()):
            if path.is_dir():
                tracks = sorted(
                    p for p in path.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES
                )
                if tracks:
                    self.by_mood[path.name] = tracks
            elif path.suffix.lower() in AUDIO_SUFFIXES:
                self.flat.append(path)

    @property
    def empty(self) -> bool:
        return not self.by_mood and not self.flat

    def pool(self, mood: str) -> list[Path]:
        """Tracks for a mood, falling back to anything available.

        A missing mood directory is not an error. An operator who has downloaded
        six tracks and not sorted them should still get music.
        """
        return self.by_mood.get(mood) or self.flat or [
            track for tracks in self.by_mood.values() for track in tracks
        ]

    def summary(self) -> str:
        if self.empty:
            return f"no music in {self.root}"
        parts = [f"{mood}: {len(t)}" for mood, t in sorted(self.by_mood.items())]
        if self.flat:
            parts.append(f"unsorted: {len(self.flat)}")
        return ", ".join(parts)


def _pick(pool: list[Path], seed: str, avoid: Path | None) -> Path | None:
    """Deterministic choice, avoiding whatever played last.

    Deterministic so a re-render after a rejected review produces the same
    episode rather than a different-sounding one; avoiding the previous track so
    two chapters in a row never share a bed.
    """
    if not pool:
        return None
    candidates = [p for p in pool if p != avoid] or pool
    index = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return candidates[index % len(candidates)]


def plan_beds(
    spans: list[dict[str, Any]],
    chapter_keys: list[str],
    library: BgmLibrary,
    seed: str = "",
) -> list[Bed]:
    """Choose a track for each chapter."""
    beds: list[Bed] = []
    previous: Path | None = None
    for index, span in enumerate(spans):
        seconds = float(span["end_s"]) - float(span["start_s"])
        key = chapter_keys[index] if index < len(chapter_keys) else "detail"
        mood = CHAPTER_MOOD.get(key, DEFAULT_MOOD)

        if seconds < MIN_BED_SECONDS:
            beds.append(Bed(index, mood, None, seconds))
            continue

        track = _pick(library.pool(mood), f"{seed}:{index}:{mood}", previous)
        if track is not None:
            previous = track
        beds.append(Bed(index, mood, track, seconds))
    return beds


def _render_bed(bed: Bed, output: Path) -> Path:
    """One chapter's bed, faded at both ends and cut to length."""
    fade_out_at = max(0.0, bed.seconds - FADE_OUT_SECONDS)

    if bed.track is None:
        ffmpeg._run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={bed.seconds:.3f}",
            "-c:a", "pcm_s16le", str(output),
        ], timeout=120)
        return output

    # A track shorter than the chapter still loops, but starting each chapter at
    # a different offset keeps two chapters from the same track sounding like a
    # repeat of one another.
    #
    # The offset is taken with `atrim` after the loop, not with `-ss` before the
    # input. `-ss` combined with `-stream_loop` produces the wrong length —
    # measured: a 13-second request came back 25 seconds long — because the seek
    # and the loop disagree about where timestamps restart.
    offset = (bed.chapter * 17) % 45
    ffmpeg._run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-stream_loop", "-1", "-i", str(bed.track),
        "-af",
        f"aresample=48000,aformat=channel_layouts=stereo,"
        f"atrim=start={offset}:end={offset + bed.seconds:.3f},asetpts=N/SR/TB,"
        f"afade=t=in:st=0:d={FADE_IN_SECONDS},"
        f"afade=t=out:st={fade_out_at:.3f}:d={FADE_OUT_SECONDS}",
        "-t", f"{bed.seconds:.3f}",
        "-c:a", "pcm_s16le", str(output),
    ], timeout=300)
    return output


def build_bed(
    spans: list[dict[str, Any]],
    chapter_keys: list[str],
    workdir: Path,
    seed: str = "",
    bgm_dir: Path | None = None,
) -> tuple[Path | None, list[Bed]]:
    """Build the whole episode's music bed. Returns (path, the plan).

    Returns `(None, [])` when there is no library — the caller then mixes
    narration alone.
    """
    library = BgmLibrary(bgm_dir or Path("work/bgm"))
    if library.empty:
        log.info("no BGM library at %s; the episode will have narration only", library.root)
        return None, []

    beds = plan_beds(spans, chapter_keys, library, seed)
    if not any(bed.track for bed in beds):
        return None, beds

    workdir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for bed in beds:
        part = workdir / f"bed_{bed.chapter:02d}.wav"
        try:
            parts.append(_render_bed(bed, part))
        except ffmpeg.FFmpegError as exc:
            log.warning("bed for chapter %d failed, using silence: %s", bed.chapter, exc)
            parts.append(_render_bed(Bed(bed.chapter, bed.mood, None, bed.seconds), part))

    listing = workdir / "beds.txt"
    listing.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in parts) + "\n", encoding="utf-8"
    )
    combined = workdir / "bed.wav"
    try:
        ffmpeg._run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:a", "pcm_s16le", str(combined),
        ], timeout=600)
    finally:
        listing.unlink(missing_ok=True)
        for part in parts:
            part.unlink(missing_ok=True)

    log.info(
        "music bed: %s",
        ", ".join(
            f"{b.mood}={b.track.stem if b.track else 'silence'}" for b in beds
        ),
    )
    return combined, beds


def _tail(loudness: float, pad_seconds: float) -> str:
    """Normalise, then hold silence for anything the narration does not cover.

    The closing card outlives the narration; without the pad, `-shortest` cuts
    the video at the last spoken word and the card never appears.
    """
    pad = f",apad=pad_dur={pad_seconds:.3f}" if pad_seconds > 0 else ""
    return f"loudnorm=I={loudness}:TP=-1.5:LRA=11{pad}[a]"


def mix_filter(gain_db: float, loudness: float, pad_seconds: float = 0.0) -> str:
    """The audio graph: duck the bed under the narration, then normalise.

    `normalize=0` on the mix matters — amix's default scales every input by
    1/n, so adding a music bed quietly drops the narration 6 dB before loudnorm
    ever sees it.
    """
    return (
        "[1:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[nar][key];"
        f"[2:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain_db}dB[bed];"
        # Keyed to the narration: the bed dips while someone is talking and comes
        # back in the gaps. A fixed level cannot do both.
        #
        # These numbers were measured on an isolated bed, not guessed:
        # threshold 0.03 / ratio 8 gives 7.7 dB of duck, inside the 6-10 dB band
        # broadcast uses for music under speech. Softer (0.05/6) gave 3.6 dB and
        # the bed still fought the narration; harder (0.02/12) gave 11.3 dB and
        # the music vanished rather than receded.
        "[bed][key]sidechaincompress="
        "threshold=0.03:ratio=8:attack=20:release=400:makeup=1[ducked];"
        "[nar][ducked]amix=inputs=2:normalize=0:duration=first[mixed];"
        "[mixed]" + _tail(loudness, pad_seconds)
    )


def narration_only_filter(loudness: float, pad_seconds: float = 0.0) -> str:
    return "[1:a]aresample=48000," + _tail(loudness, pad_seconds)
