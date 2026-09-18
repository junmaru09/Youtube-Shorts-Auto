"""Speech synthesis, behind an interface thin enough to swap.

VOICEVOX is the default: ずんだもん and 四国めたん are the format, the engine
runs locally on the CPU (no GPU vendor matters — the same on the RX 9070 XT
machine as on the RTX one), and it costs nothing. Google Cloud TTS remains as
a fallback; its first million characters a month are free.

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


class VoicevoxTTS:
    """VOICEVOX ENGINE over its HTTP API.

    `voice` is the speaker (style) id as a string — "3" for ずんだもん ノーマル,
    "2" for 四国めたん ノーマル — because that is the one thing the engine
    needs and Navigator already carries it.

    If the engine is not answering and `engine_dir` points at an unpacked
    Linux engine (the folder with `run` in it), it is started here and
    stopped when the process exits. That is what makes the pipeline one
    command on the daily-use PC: nothing to launch first.
    """

    name = "voicevox"
    metered = False

    def __init__(self, base_url: str = "http://127.0.0.1:50021", engine_dir: str | None = None,
                 sample_rate: int = 24000, start_timeout: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.engine_dir = Path(engine_dir).expanduser() if engine_dir else None
        self.sample_rate = sample_rate
        self.start_timeout = start_timeout
        self._process = None
        self._ready = False

    # --- engine lifecycle ---------------------------------------------------------

    def version(self) -> str | None:
        import json
        import urllib.request

        try:
            with urllib.request.urlopen(f"{self.base_url}/version", timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - not running, or not reachable
            return None

    def speakers(self) -> dict[int, str]:
        """style id -> "character style", for doctor and for error messages."""
        import json
        import urllib.request

        with urllib.request.urlopen(f"{self.base_url}/speakers", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {
            int(style["id"]): f"{speaker['name']} {style['name']}"
            for speaker in data for style in speaker.get("styles", [])
        }

    def ensure_running(self) -> None:
        if self._ready or self.version() is not None:
            self._ready = True
            return
        if self.engine_dir is None:
            raise TTSError(
                f"VOICEVOX ENGINE is not answering at {self.base_url}. Start the VOICEVOX app, "
                "or set tts.voicevox.engine_dir to an unpacked Linux engine so it can be started here."
            )
        runner = self.engine_dir / "run"
        if not runner.exists():
            raise TTSError(f"tts.voicevox.engine_dir has no `run` binary: {self.engine_dir}")
        import atexit
        import subprocess
        import time
        from urllib.parse import urlparse

        parsed = urlparse(self.base_url)
        log.info("starting VOICEVOX ENGINE from %s", self.engine_dir)
        self._process = subprocess.Popen(
            [str(runner), "--host", parsed.hostname or "127.0.0.1", "--port", str(parsed.port or 50021)],
            cwd=str(self.engine_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        atexit.register(self.stop)
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self.version() is not None:
                self._ready = True
                return
            if self._process.poll() is not None:
                raise TTSError(f"VOICEVOX ENGINE exited immediately (code {self._process.returncode})")
            time.sleep(1.0)
        raise TTSError(f"VOICEVOX ENGINE did not answer within {self.start_timeout:.0f}s")

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except Exception:  # noqa: BLE001
                self._process.kill()
        self._process = None

    # --- synthesis -----------------------------------------------------------------

    def synthesize(self, text: str, voice: str, output: Path, speaking_rate: float = 1.0) -> Utterance:
        import json
        import urllib.parse
        import urllib.request

        self.ensure_running()
        spoken = reading.apply_readings(text)
        try:
            speaker = int(voice)
        except (TypeError, ValueError) as exc:
            raise TTSError(f"VOICEVOX needs a numeric speaker id, got {voice!r}; set voicevox_speaker on the navigator") from exc

        query_url = f"{self.base_url}/audio_query?" + urllib.parse.urlencode({"text": spoken, "speaker": speaker})
        try:
            with urllib.request.urlopen(urllib.request.Request(query_url, method="POST"), timeout=30) as response:
                query = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"VOICEVOX audio_query failed for speaker {speaker}: {exc}") from exc

        query["speedScale"] = float(speaking_rate)
        query["outputSamplingRate"] = self.sample_rate
        query["outputStereo"] = False
        # a touch of silence at the ends, so consecutive lines do not collide
        query["prePhonemeLength"] = 0.1
        query["postPhonemeLength"] = 0.1

        synth_url = f"{self.base_url}/synthesis?" + urllib.parse.urlencode({"speaker": speaker})
        request = urllib.request.Request(
            synth_url, data=json.dumps(query).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                audio = response.read()
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"VOICEVOX synthesis failed: {exc}") from exc

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(audio)
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
    if name == "voicevox":
        return VoicevoxTTS(
            base_url=kwargs.get("base_url", "http://127.0.0.1:50021"),
            engine_dir=kwargs.get("engine_dir"),
            sample_rate=kwargs.get("sample_rate", 24000),
        )
    if name == "google":
        return GoogleTTS(language_code=kwargs.get("language_code", "ja-JP"),
                         sample_rate=kwargs.get("sample_rate", 24000))
    if name in ("silent", "fake"):
        return SilentTTS(sample_rate=kwargs.get("sample_rate", 24000))
    raise KeyError(f"unknown TTS provider: {name!r} (known: voicevox, google, silent)")


def voice_for(navigator, backend: TTSBackend) -> str:
    """The voice identifier this backend wants for a navigator."""
    if backend.name == "voicevox" and navigator.voicevox_speaker is not None:
        return str(navigator.voicevox_speaker)
    return navigator.voice


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
