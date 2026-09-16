"""Speech synthesis, behind an interface thin enough to swap.

Google Cloud TTS is the default because Chirp 3 HD reads Japanese naturally and
the first million characters each month are free — an 18-minute script is about
7,200 characters, so a daily upload uses roughly a quarter of the allowance.

Every request goes through `reading.apply_readings` first. The script's spoken
text and the dictionary are two halves of one mechanism: the script model opens
what needs context, the dictionary opens what does not, and skipping the second
half would mean the checks in `stages/script.py` passed on text the voice never
actually receives.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import reading

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    """Synthesis failed."""


@dataclass(slots=True)
class Utterance:
    """One synthesised line, with what it cost in free-tier characters."""

    path: Path
    chars: int
    voice: str


@runtime_checkable
class TTSBackend(Protocol):
    name: str
    # Whether usage counts against a metered allowance. The narrate stage
    # refuses to cross the free tier for metered backends and ignores the
    # question for local ones.
    metered: bool

    def synthesize(self, text: str, voice: str, output: Path, speaking_rate: float) -> Utterance:
        """Write one line of speech to `output`."""
        ...


class GoogleTTS:
    """Google Cloud Text-to-Speech.

    Authenticates through Application Default Credentials, so the operator runs
    `gcloud auth application-default login` once (or points
    GOOGLE_APPLICATION_CREDENTIALS at a service-account key) rather than pasting
    another key into .env.
    """

    name = "google"
    metered = True

    def __init__(self, language_code: str = "ja-JP", sample_rate: int = 24000) -> None:
        self.language_code = language_code
        self.sample_rate = sample_rate
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from google.cloud import texttospeech
            except ImportError as exc:
                raise TTSError(
                    "google-cloud-texttospeech is not installed. "
                    'Run: pip install -e ".[tts]"'
                ) from exc
            try:
                self._client = texttospeech.TextToSpeechClient()
            except Exception as exc:  # noqa: BLE001 - credential errors vary
                raise TTSError(
                    f"could not authenticate to Google Cloud TTS: {exc}. Run "
                    "`gcloud auth application-default login`, or set "
                    "GOOGLE_APPLICATION_CREDENTIALS to a service-account key."
                ) from exc
        return self._client

    def synthesize(
        self, text: str, voice: str, output: Path, speaking_rate: float = 1.0
    ) -> Utterance:
        from google.cloud import texttospeech

        spoken = reading.apply_readings(text)
        output.parent.mkdir(parents=True, exist_ok=True)

        response = self.client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=spoken),
            voice=texttospeech.VoiceSelectionParams(
                language_code=self.language_code, name=voice
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.sample_rate,
                speaking_rate=speaking_rate,
            ),
        )
        output.write_bytes(response.audio_content)
        return Utterance(path=output, chars=len(spoken), voice=voice)


class SilentTTS:
    """Offline stand-in: real WAV files of plausible length, no network, no cost.

    Duration follows the same characters-per-minute figure the script stage
    plans against, so a dry run produces a timeline with realistic shape.
    """

    name = "silent"
    metered = False
    CHARS_PER_SECOND = 400 / 60

    def __init__(self, sample_rate: int = 24000) -> None:
        self.sample_rate = sample_rate

    def synthesize(
        self, text: str, voice: str, output: Path, speaking_rate: float = 1.0
    ) -> Utterance:
        import struct
        import wave

        spoken = reading.apply_readings(text)
        seconds = max(0.4, len(spoken) / (self.CHARS_PER_SECOND * speaking_rate))
        frames = int(seconds * self.sample_rate)

        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(struct.pack("<h", 0) * frames)

        return Utterance(path=output, chars=len(spoken), voice=voice)


def get_backend(name: str, **kwargs) -> TTSBackend:
    if name == "google":
        return GoogleTTS(**kwargs)
    if name in ("silent", "fake"):
        return SilentTTS(sample_rate=kwargs.get("sample_rate", 24000))
    raise KeyError(f"unknown TTS provider: {name!r} (known: google, silent)")


def free_tier_state(chars_used: int, allowance: int, warn_at: float) -> tuple[bool, str]:
    """Whether this month's synthesis is still free, and how close to the edge.

    Worth watching: going over does not fail, it just starts charging, and a
    silent switch from free to paid is exactly the kind of drift the budget
    guard exists to make visible.
    """
    if allowance <= 0:
        return True, "no free-tier allowance configured"
    ratio = chars_used / allowance
    message = f"{chars_used:,} / {allowance:,} chars this month ({ratio:.0%})"
    return ratio < warn_at, message
