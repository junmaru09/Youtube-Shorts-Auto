"""Stage 3: turn the script into audio, and find out how long the video is.

Narration comes before visuals on purpose. The audio decides the real duration
of every chapter, so cutting footage to it is exact; guessing durations from
character counts first and then trying to make the audio fit is how narration
ends up clipped mid-sentence.

The output is one WAV plus a timeline: every line's start and end. That single
structure drives the chapter markers, the subtitle timings, and how much footage
each chapter needs.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import brand as brand_mod
from .. import config, db, ffmpeg, paths, tts
from ..budget import month_start

log = logging.getLogger(__name__)

# Silence inserted around speech. Long enough to feel deliberate, short enough
# that eighteen minutes does not become twenty-one.
PAUSE_AFTER_LINE = 0.35
PAUSE_AFTER_CHAPTER = 1.1
PAUSE_ON_SPEAKER_CHANGE = 0.55


@dataclass(slots=True)
class NarrateResult:
    narrated: int = 0
    failed: int = 0
    chars: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _silence(seconds: float, sample_rate: int, output: Path) -> Path:
    ffmpeg._run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(output),
    ])
    return output


def _concat(parts: list[Path], output: Path) -> Path:
    """Join the pieces losslessly, then normalise once at the end."""
    listing = output.parent / f"{output.stem}_parts.txt"
    listing.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in parts) + "\n", encoding="utf-8"
    )
    try:
        ffmpeg._run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:a", "pcm_s16le", str(output),
        ])
    finally:
        listing.unlink(missing_ok=True)
    return output


def synthesize_script(
    chapters: list[dict[str, Any]],
    backend: tts.TTSBackend,
    brand: brand_mod.Brand,
    workdir: Path,
    sample_rate: int = 24000,
) -> tuple[Path, list[dict[str, Any]], int]:
    """Speak every line and stitch the result.

    Returns the combined audio, a per-line timeline, and the character count
    charged against the TTS free tier.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    total_chars = 0
    previous_speaker: str | None = None

    for chapter_index, chapter in enumerate(chapters):
        for line_index, line in enumerate(chapter.get("lines", [])):
            speaker = line["speaker"]
            navigator = brand.navigators.get(speaker) or brand.explainer

            if previous_speaker is not None:
                gap = (
                    PAUSE_ON_SPEAKER_CHANGE
                    if speaker != previous_speaker
                    else PAUSE_AFTER_LINE
                )
                pause = _silence(gap, sample_rate, workdir / f"gap_{chapter_index}_{line_index}.wav")
                parts.append(pause)
                cursor += gap

            piece = workdir / f"line_{chapter_index:02d}_{line_index:03d}.wav"
            utterance = backend.synthesize(
                line["spoken"], navigator.voice, piece, navigator.speaking_rate
            )
            spoken_seconds = _duration(utterance.path)

            timeline.append({
                "chapter": chapter_index,
                "chapter_title": chapter.get("title", ""),
                "line": line_index,
                "speaker": speaker,
                "display": line["display"],
                "start_s": round(cursor, 3),
                "end_s": round(cursor + spoken_seconds, 3),
            })

            parts.append(utterance.path)
            cursor += spoken_seconds
            total_chars += utterance.chars
            previous_speaker = speaker

        if chapter_index < len(chapters) - 1:
            pause = _silence(PAUSE_AFTER_CHAPTER, sample_rate, workdir / f"gap_end_{chapter_index}.wav")
            parts.append(pause)
            cursor += PAUSE_AFTER_CHAPTER

    combined = _concat(parts, workdir / "narration.wav")
    return combined, timeline, total_chars


def chapter_spans(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the per-line timeline into one span per chapter."""
    spans: dict[int, dict[str, Any]] = {}
    for entry in timeline:
        index = entry["chapter"]
        span = spans.setdefault(
            index,
            {
                "chapter": index,
                "title": entry["chapter_title"],
                "start_s": entry["start_s"],
                "end_s": entry["end_s"],
            },
        )
        span["start_s"] = min(span["start_s"], entry["start_s"])
        span["end_s"] = max(span["end_s"], entry["end_s"])
    return [spans[k] for k in sorted(spans)]


def run(limit: int = 1, idea_id: int | None = None, provider: str | None = None) -> NarrateResult:
    """Narrate scripts that have no audio yet."""
    settings = config.load_settings()
    tts_cfg = settings.get("tts", {})
    sample_rate = int(tts_cfg.get("sample_rate", 24000))
    allowance = int(tts_cfg.get("free_tier_chars_per_month", 1_000_000))
    brand = brand_mod.load_brand()
    backend = tts.get_backend(
        provider or tts_cfg.get("provider", "google"),
        language_code=tts_cfg.get("language_code", "ja-JP"),
        sample_rate=sample_rate,
    )

    result = NarrateResult()
    with db.session() as conn:
        if idea_id is not None:
            row = db.get_idea(conn, idea_id)
            ideas = [row] if row else []
        else:
            ideas = db.ideas_by_status(conn, "scripted", limit=limit)

        if not ideas:
            log.info("no ideas with status 'scripted'")
            return result

        for idea in ideas:
            current_id = int(idea["id"])
            script_row = db.get_script(conn, current_id)
            if script_row is None:
                result.failed += 1
                result.errors.append(f"idea {current_id}: no script on record")
                continue

            chapters = json.loads(script_row["chapters_json"])
            workdir = paths.AUDIO_DIR / f"idea_{current_id:05d}"

            # Refuse to cross the free tier. Google does not fail past the
            # allowance — it starts charging — and a budget alert on their side
            # only sends an email. This is the one place the crossing can
            # actually be prevented, so it is a hard stop, not a warning.
            if backend.metered:
                needed = int(script_row["char_count"])
                used = db.tts_chars_since(conn, month_start())
                if used + needed > allowance:
                    result.failed += 1
                    result.errors.append(
                        f"idea {current_id}: 今月のTTS無料枠を超えます"
                        f"（使用 {used:,} + 必要 {needed:,} > 枠 {allowance:,} 文字）。"
                        "来月まで待つか、tts.free_tier_chars_per_month を確認してください。"
                        "課金を避けるため合成しません"
                    )
                    log.error("TTS free tier would be exceeded for idea %d; refusing", current_id)
                    continue

            try:
                audio, timeline, chars = synthesize_script(
                    chapters, backend, brand, workdir, sample_rate
                )
            except (tts.TTSError, ffmpeg.FFmpegError, ffmpeg.FFmpegMissing, OSError) as exc:
                log.error("narration failed for idea %d: %s", current_id, exc)
                result.failed += 1
                result.errors.append(f"idea {current_id}: {exc}")
                continue

            duration = _duration(audio)
            db.upsert_narration(
                conn,
                idea_id=current_id,
                path=str(audio),
                duration_s=duration,
                chars=chars,
                timeline=timeline,
                voices=brand.voice_map(),
            )
            db.set_idea_status(conn, current_id, "narrated")
            conn.commit()

            result.narrated += 1
            result.chars += chars
            result.details.append(
                {"idea_id": current_id, "duration_s": duration, "chars": chars}
            )
            log.info(
                "idea %d narrated: %.1f min, %d chars, %d chapters",
                current_id, duration / 60, chars, len(chapter_spans(timeline)),
            )

    return result
