"""The VOICEVOX backend against a fake engine, so the request shape is
tested without the real one installed."""

import json
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest

from tube_auto import tts


class FakeEngine(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict, dict | None]] = []

    def log_message(self, *args):  # quiet
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/version":
            self._json("0.22.0")
        elif path == "/speakers":
            self._json([
                {"name": "四国めたん", "styles": [{"name": "ノーマル", "id": 2}]},
                {"name": "ずんだもん", "styles": [{"name": "ノーマル", "id": 3}]},
            ])
        else:
            self._json({"error": "no"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        FakeEngine.seen.append((parsed.path, query, body))
        if parsed.path == "/audio_query":
            self._json({"accent_phrases": [], "speedScale": 1.0, "kana": query["text"]})
        elif parsed.path == "/synthesis":
            buf = BytesIO()
            with wave.open(buf, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(int(body["outputSamplingRate"]))
                handle.writeframes(struct.pack("<h", 0) * int(body["outputSamplingRate"] // 2))
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({"error": "no"}, 404)


@pytest.fixture
def engine():
    server = HTTPServer(("127.0.0.1", 0), FakeEngine)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    FakeEngine.seen.clear()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_synthesis_sends_query_then_audio(engine, tmp_path):
    backend = tts.get_backend("voicevox", base_url=engine, sample_rate=24000)
    out = tmp_path / "line.wav"
    utterance = backend.synthesize("こんにちは", "3", out, speaking_rate=1.05)
    assert out.exists() and utterance.chars == len("こんにちは")
    (q_path, q_query, _), (s_path, s_query, s_body) = FakeEngine.seen
    assert q_path == "/audio_query" and q_query["speaker"] == "3" and q_query["text"] == "こんにちは"
    assert s_path == "/synthesis" and s_query["speaker"] == "3"
    assert s_body["speedScale"] == 1.05 and s_body["outputSamplingRate"] == 24000
    with wave.open(str(out)) as handle:
        assert handle.getframerate() == 24000


def test_voice_id_must_be_numeric(engine, tmp_path):
    backend = tts.get_backend("voicevox", base_url=engine)
    with pytest.raises(tts.TTSError):
        backend.synthesize("x", "ja-JP-Chirp3-HD-Charon", tmp_path / "x.wav")


def test_unreachable_engine_without_a_binary_is_a_clear_error(tmp_path):
    backend = tts.get_backend("voicevox", base_url="http://127.0.0.1:1")
    with pytest.raises(tts.TTSError, match="not answering"):
        backend.synthesize("x", "3", tmp_path / "x.wav")


def test_voice_for_picks_the_speaker_id_for_voicevox(engine):
    from tube_auto.brand import load_brand

    brand = load_brand()
    assert tts.voice_for(brand.explainer, tts.get_backend("voicevox", base_url=engine)) == "3"
    assert tts.voice_for(brand.explainer, tts.get_backend("silent")) == brand.explainer.voice


def test_speakers_lists_ids(engine):
    backend = tts.get_backend("voicevox", base_url=engine)
    assert backend.speakers() == {2: "四国めたん ノーマル", 3: "ずんだもん ノーマル"}
    assert backend.version() == "0.22.0"
