"""Design tokens for the whiteboard.

Every number here was measured from the reference videos (640x360, scaled x3)
or chosen against them side by side. Change them here, not in the drawing code.

The one rule that everything else follows from: **every mark on the stage has a
dark outline.** Text, shapes, arrows, pie slices. That is what lets flat fills
sit on a mottled parchment or a starfield photo and stay legible, and it is the
single most recognisable trait of the style.
"""

from __future__ import annotations

from pathlib import Path

# --- frame ---------------------------------------------------------------------

WIDTH, HEIGHT = 1920, 1080

# Subtitle band: bottom 19.5% of the frame, measured from the reference.
BAND_TOP = 870

# Sprites sit in the bottom corners, mostly above the band: measured heads
# span y 615-900 at 1080p, so they overlap the band's top edge by ~30px.
SPRITE_SIZE = 320
SPRITE_TOP = 600
SPRITE_LEFT = (30, SPRITE_TOP)
SPRITE_RIGHT = (WIDTH - SPRITE_SIZE - 30, SPRITE_TOP)

# The stage: where figures go. Above the sprites' heads it is nearly the
# full frame width (the reference puts tables and box rows at x=120); level
# with the sprites it narrows to the gap between them.
STAGE_TOP, STAGE_BOTTOM = 40, 850
STAGE_FULL_LEFT, STAGE_FULL_RIGHT = 120, 1800   # valid while y < SPRITE_TOP
STAGE_LEFT, STAGE_RIGHT = 360, 1560             # valid all the way down
STAGE_W = STAGE_RIGHT - STAGE_LEFT
STAGE_H = STAGE_BOTTOM - STAGE_TOP

# Fixed slots outside the stage proper.
HEADING_Y = 100                   # section / person name, top centre
LIST_X, LIST_Y = 1400, 60         # numbered list, top right, grows downward
LIST_LINE = 52

# --- outline -------------------------------------------------------------------

INK = (24, 20, 16)                # near-black with a touch of warmth
OUTLINE_SHAPE = 7                 # px around filled shapes
OUTLINE_TEXT = 6                  # px around stage text (scaled with size below)
OUTLINE_SUBTITLE = 5

# --- fills ---------------------------------------------------------------------

WHITE = (255, 255, 255)
YELLOW = (255, 222, 64)
MAGENTA = (240, 112, 240)         # sampled from the reference pie
CYAN = (112, 240, 240)
PINK = (255, 140, 150)
GREEN = (96, 200, 96)
BLUE = (80, 150, 230)
GREY = (200, 200, 200)
ORANGE = (255, 140, 40)
RED = (230, 60, 60)

# Named colours the script may ask for. Anything else is refused at validation.
PALETTE = {
    "white": WHITE, "yellow": YELLOW, "magenta": MAGENTA, "cyan": CYAN,
    "pink": PINK, "green": GREEN, "blue": BLUE, "grey": GREY, "orange": ORANGE,
    "red": RED,
}

# Subtitle colour per speaker. The reference pairs green and purple; so do we.
SPEAKER_COLOURS = {
    "explainer": (98, 222, 98),
    "listener": (214, 150, 255),
}

# --- type ----------------------------------------------------------------------

_FONT_DIR = Path("/usr/share/fonts/opentype/mplus")
FONT_HEAVY = _FONT_DIR / "Mplus2-Black.otf"      # labels, titles, subtitles
FONT_BOLD = _FONT_DIR / "Mplus2-ExtraBold.otf"   # descriptions, table cells
FONT_FALLBACK = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")

SIZE_TITLE = 84
SIZE_HEADING = 56
SIZE_LABEL = 50
SIZE_LABEL_SMALL = 40
SIZE_NOTE = 32                     # the white description under a pie label
SIZE_LIST = 40
SIZE_SUBTITLE = 46
SIZE_TABLE = 48                    # table rows are as big as labels
TABLE_ROW = 135                    # row pitch, measured

# Inline marks drawn as shapes inside text (see draw._runs): the ◎ ring is
# this blue in the reference, the ➡ arrow white, the × red.
MARK_RING = (56, 116, 230)

# --- geometry -------------------------------------------------------------------

# Arrows measured off the reference at 360p and tripled: a 7px shaft with a
# 1px outline and an 18px-wide head. Arrows are outlined thinly, unlike text.
ARROW_WIDTH = 22
ARROW_HEAD = 56
ARROW_OUTLINE = 4
BOX_RADIUS = 10
DIM_ALPHA = 130                    # the "title with dim" overlay
HIGHLIGHT_WIDTH = 9

# --- backgrounds ----------------------------------------------------------------

PARCHMENT_BASE = (168, 148, 118)
PARCHMENT_LIGHT = (196, 178, 148)
PARCHMENT_DARK = (128, 108, 84)
PHOTO_DIM = 0.55                   # multiply a photo background by this


def font_path(heavy: bool = True) -> str:
    """The heavy face when it exists, the bold when it does, Noto otherwise."""
    for candidate in ((FONT_HEAVY if heavy else FONT_BOLD), FONT_BOLD, FONT_FALLBACK):
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("no CJK font found; install fonts-mplus or fonts-noto-cjk")
