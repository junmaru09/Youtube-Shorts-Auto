"""Fetch a set of everyday illustrations from Noto Emoji and rasterise them.

    python tools/fetch_illustrations.py            # writes assets/illustrations/<name>.png

The script model can only draw what the library holds. Without a ruler it
writes the word "ruler" in a box; with one it places the ruler. Noto Emoji
(Apache-2.0, googlefonts/noto-emoji) covers the things a science explainer
keeps reaching for — rulers, houses, cars, apples, thermometers, rockets —
in one flat, outlined style that sits well on the whiteboard.

Names are what the script writes (`place element=ruler`); codepoints are
Noto's file names. Add a line here, run it, commit the PNG.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import cairosvg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "illustrations"
SRC = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/svg/emoji_u{code}.svg"
SIZE = 512

from tube_auto.canvas.illustrations import ILLUSTRATIONS  # noqa: E402


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    done, failed = 0, []
    for name, (code, _) in ILLUSTRATIONS.items():
        target = OUT / f"{name}.png"
        if target.exists():
            done += 1
            continue
        url = SRC.format(code=code)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                svg = response.read()
            cairosvg.svg2png(bytestring=svg, write_to=str(target), output_width=SIZE, output_height=SIZE)
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name} ({code}): {exc}")
    print(f"{done} illustrations in {OUT}")
    for f in failed:
        print("  failed:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
