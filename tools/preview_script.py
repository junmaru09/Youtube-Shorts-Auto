"""Render a script's visuals to a short silent video, to see the stage before
spending anything.

    python tools/preview_script.py                     # the built-in sample
    python tools/preview_script.py script.json         # {"chapters": [...]} as the script stage stores it

Writes work/preview/preview.mp4 and work/preview/sheet.png (one frame per
line). Narration is the silent backend, so timings are estimated from the
character count and no engine is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from tube_auto import brand as brand_mod  # noqa: E402
from tube_auto import tts  # noqa: E402
from tube_auto.models import Chapter, Line, Script  # noqa: E402
from tube_auto.stages import narrate, whiteboard  # noqa: E402
from tube_auto.stages import script as script_stage  # noqa: E402

OUT = Path("work/preview")

SAMPLE = [
    Chapter(key="opener", title="雑談", lines=[
        Line("listener", "いや、なんだか最近ずっと暑いじゃない？", "いや、なんだか最近ずっと暑いじゃない？",
             visual=["clear", "background name=room"]),
        Line("explainer", "地球の長い歴史から見れば、意外と暑いわけじゃないのだ", "地球の長い歴史から見れば、意外と暑いわけじゃないのだ",
             visual=["label text=今は寒い時代 at=top colour=yellow size=70"], expression="happy"),
        Line("listener", "え、どういうことかしら？", "え、どういうことかしら？", visual=["hold"], expression="surprised"),
    ]),
    Chapter(key="context", title="背景", lines=[
        Line("explainer", "45万年の間、氷期と間氷期を繰り返してきたのだ", "45万年の間、氷期と間氷期を繰り返してきたのだ",
             visual=["clear", "background name=space",
                     "timeline slot=wide name=tl start=45万年前 end=現在 "
                     "bands=0:1:cyan:cold|0.14:0.18:pink:warm1|0.33:0.37:pink:warm2|0.52:0.56:pink:warm3|0.71:0.75:pink:warm4|0.965:1:pink:warm5"]),
        Line("explainer", "寒い時期が氷期", "寒い時期が氷期", visual=["label text=氷期 at=tl.cold side=above colour=cyan pointer=true"]),
        Line("explainer", "暖かい時期が間氷期なのだ", "暖かい時期が間氷期なのだ", visual=["label text=間氷期 at=tl.warm3 side=above colour=pink pointer=true"]),
        Line("listener", "なるほど、今は氷河時代の中でも暖かい時期なのね", "なるほど、今は氷河時代の中でも暖かい時期なのね",
             visual=["highlight target=tl.warm5"]),
        Line("explainer", "太陽の光が氷に当たると", "太陽の光が氷に当たると",
             visual=["clear", "sun slot=sky name=sun", "earth_arc slot=floor name=earth", "add element=ice_block near=earth name=ice1"]),
        Line("explainer", "そのまま跳ね返されてしまうのだ", "そのまま跳ね返されてしまうのだ",
             visual=["arrow from=sun via=ice1.top to=up-right colour=yellow"]),
        Line("explainer", "これを正のフィードバックと呼ぶのだ", "これを正のフィードバックと呼ぶのだ",
             visual=["title text=正のフィードバック dim=true"]),
    ]),
]


def main(path: str | None) -> int:
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        chapters = [Chapter.from_dict(c) for c in data["chapters"]]
    else:
        chapters = SAMPLE
    script = Script(chapters=chapters, hooks=["", "", ""])
    problems = script_stage.check_visuals(script)
    for problem in problems:
        print("visual:", problem)
    if problems:
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    brand = brand_mod.load_brand()
    _, timeline, _ = narrate.synthesize_script(script.as_dicts(), tts.get_backend("silent"), brand, OUT / "audio")
    frames = whiteboard.render_frames(timeline, [], brand, OUT / "frames")
    for problem in frames.problems:
        print("render:", problem)
    whiteboard.frames_to_video(frames.path, OUT / "preview.mp4", 30, frames.seconds)

    stills = sorted((OUT / "frames").glob("f*_closed.png"))
    thumbs = [Image.open(f).resize((640, 360), Image.LANCZOS) for f in stills]
    cols = 3
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 644 + 4, rows * 364 + 4), (40, 40, 40))
    for i, im in enumerate(thumbs):
        sheet.paste(im, (4 + (i % cols) * 644, 4 + (i // cols) * 364))
    sheet.save(OUT / "sheet.png")
    print(f"{len(stills)} lines, {frames.seconds:.1f}s -> {OUT / 'preview.mp4'} and {OUT / 'sheet.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
