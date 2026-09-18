"""The frame renderer: one stage per line, two mouths, a playable listing."""

import struct
import wave

from tube_auto import brand as brand_mod
from tube_auto.stages import whiteboard


def _wav(path, seconds, loud_from=0.0, loud_to=None, rate=24000):
    """A WAV that is silent except between loud_from and loud_to."""
    loud_to = seconds if loud_to is None else loud_to
    n = int(seconds * rate)
    samples = []
    for i in range(n):
        t = i / rate
        samples.append(12000 if loud_from <= t < loud_to and (i // 40) % 2 == 0 else 0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{n}h", *samples))
    return path


def _timeline(tmp_path):
    a = _wav(tmp_path / "a.wav", 1.2, 0.0, 0.6)      # speech in the first half only
    b = _wav(tmp_path / "b.wav", 0.8, 9.0, 9.0)      # silent throughout
    return [
        {"chapter": 0, "line": 0, "speaker": "listener", "display": "最近暑いじゃない？",
         "start_s": 0.0, "end_s": 1.2, "audio": str(a), "expression": "normal",
         "ops": [{"op": "clear"}, {"op": "label", "text": "暑い", "at": "top"}]},
        {"chapter": 1, "line": 0, "speaker": "explainer", "display": "実は違うのだ",
         "start_s": 1.75, "end_s": 2.55, "audio": str(b), "expression": "happy",
         "ops": [{"op": "clear"}, {"op": "place", "element": "sun", "slot": "sky", "name": "sun"}]},
    ]


def test_frames_per_line_and_gaps_are_held(tmp_path):
    frames = whiteboard.render_frames(_timeline(tmp_path), [], brand_mod.load_brand(), tmp_path / "frames")
    # line 0: clear + label → 2 plain, 2 glow (the label appeared)
    # line 1: clear + sun → 2 plain, 2 glow, 3 crossfade frames from line 0
    assert frames.frames == 4 + 4 + 3
    assert not frames.problems
    assert abs(frames.seconds - 2.55) < 1e-6
    text = frames.path.read_text(encoding="utf-8")
    assert "f0000_open.png" in text and "f0000_closed.png" in text
    # the 0.55 s gap between the lines holds the first line's closed mouth
    assert "duration 0.550" in text
    # the listing ends by naming the last file again, as the demuxer needs
    assert text.rstrip().splitlines()[-1].startswith("file ") and "f0001_closed" in text.rstrip().splitlines()[-1]


def test_mouth_opens_only_while_the_audio_is_loud(tmp_path):
    frames = whiteboard.render_frames(_timeline(tmp_path), [], brand_mod.load_brand(), tmp_path / "frames")
    import os

    names = [os.path.basename(line.split("'")[1]) for line in frames.path.read_text(encoding="utf-8").splitlines()
             if line.startswith("file ")]
    first = [n for n in names if n.startswith("f0000_")]
    assert first[0].startswith("f0000_new_")            # the glow shows first
    assert any(not n.startswith("f0000_new_") for n in first)   # then fades
    opens = [i for i, n in enumerate(first) if n.endswith("_open.png")]
    assert opens, "no open-mouth frames during speech"
    assert max(opens) < len(first) * 0.6      # nothing opens in the silent half
    assert opens != list(range(len(opens)))   # and it alternates, not a held-open mouth
    assert all(n.endswith("_closed.png") or "_x" in n for n in names if n.startswith("f0001_"))   # a silent line stays closed


def test_a_broken_op_is_reported_not_fatal(tmp_path):
    timeline = _timeline(tmp_path)
    timeline[1]["ops"] = [{"op": "arrow", "from": "ghost", "to": "center"}]
    frames = whiteboard.render_frames(timeline, [], brand_mod.load_brand(), tmp_path / "frames")
    assert frames.frames == 4 + 2          # line 0 with its glow, line 1 plain (nothing appeared)
    assert frames.problems and "ghost" in frames.problems[0]


def test_loudness_windows_are_normalised(tmp_path):
    levels = whiteboard._loudness_windows(_wav(tmp_path / "c.wav", 1.0, 0.0, 0.5), 0.1)
    assert len(levels) == 10
    assert max(levels) == 1.0 and levels[-1] == 0.0
    assert whiteboard._loudness_windows(tmp_path / "missing.wav", 0.1) == []
