"""Reproduce reference scenes with the canvas, for visual comparison.

    python tools/canvas_demo.py            # writes work/canvas_demo/*.png + sheet.png

Each scene is a list of operations — the same shape the script stage will
emit — so this doubles as the specification the prompt examples are drawn
from. Compare `sheet.png` against the reference frames by eye; that is the
only test that matters for a style.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from tube_auto.canvas import Canvas  # noqa: E402

OUT = Path("work/canvas_demo")

SCENES: dict[str, list[dict]] = {
    # snowball 12:45-13:30 — the positive-feedback build, final state
    "ice_feedback": [
        {"op": "place", "element": "sun", "slot": "sky", "name": "sun"},
        {"op": "place", "element": "earth_arc", "slot": "floor", "name": "earth"},
        {"op": "add", "element": "ice_block", "near": "earth", "name": "ice1"},
        {"op": "add", "element": "ice_block", "near": "earth", "name": "ice2"},
        {"op": "add", "element": "ice_block", "near": "earth", "name": "ice3"},
        {"op": "add", "element": "ice_block", "near": "earth", "name": "ice4"},
        {"op": "add", "element": "ice_block", "near": "earth", "name": "ice5"},
        {"op": "arrow", "from": "sun", "via": "ice3.top", "to": "up-right", "length": 300, "colour": "yellow"},
        {"op": "arrow", "from": "sun", "via": "ice5.top", "to": "up-right", "length": 300, "colour": "yellow"},
        {"op": "label", "text": "正のフィードバック", "at": "top-right", "size": 84},
    ],
    # dark matter 1:45 — the pie
    "pie": [
        {"op": "place", "element": "pie", "slot": "center", "name": "pie", "centre_label": "宇宙全体",
         "slices": [
             {"label": "普通の物質", "value": 5, "colour": "white"},
             {"label": "ダークマター", "value": 27, "colour": "cyan", "note": "見えないし触れない\n謎の物質"},
             {"label": "ダークエネルギー", "value": 68, "colour": "magenta", "note": "宇宙を膨張させる\n謎のエネルギー"},
         ]},
    ],
    # dark matter 6:45 — the four forces, narrowing
    "box_row": [
        {"op": "heading", "text": "この世界に存在する力"},
        {"op": "place", "element": "box_row", "slot": "top", "name": "forces",
         "items": ["重力", "電磁気力", "強い核力", "弱い核力"]},
        {"op": "label", "text": "ミクロな世界の力", "at": "bottom-right", "size": 50, "name": "micro"},
        {"op": "arrow", "from": "forces.強い核力", "to": "micro", "colour": "white"},
        {"op": "arrow", "from": "forces.弱い核力", "to": "micro", "colour": "white"},
    ],
    # dark matter 11:45 — the property table, being built
    "table": [
        {"op": "table", "name": "t", "columns": ["普通の物質", "ダークマター"],
         "rows": [["質量あり ➡ 重力 ◎", "質量あり ➡ 重力 ◎"],
                  ["電気あり ➡ 電磁気力 ◎", "電気なし ➡ 電磁気力 ❌"]],
         "highlight": "電気なし ➡ 電磁気力 ❌"},
    ],
    # dark matter 4:30 — the logic chain over a dimmed galaxy
    "chain": [
        {"op": "place", "element": "galaxy", "slot": "center", "name": "gal"},
        {"op": "dim"},
        {"op": "chain", "name": "logic", "slot": "center", "nodes": [
            {"text": "恒星の速度", "x": 0.15, "y": 0.10},
            {"text": "あるべき銀河の質量 重", "x": 0.15, "y": 0.40, "colour": "pink"},
            {"text": "銀河を観察", "x": 0.72, "y": 0.10},
            {"text": "見える銀河の質量 軽", "x": 0.72, "y": 0.40, "colour": "cyan"},
            {"text": "＋", "x": 0.72, "y": 0.58, "size": 60},
            {"text": "見えない物質の質量", "x": 0.72, "y": 0.76, "colour": "yellow"},
        ], "edges": [(0, 1), (2, 3), (1, 5)]},
    ],
    # snowball 1:45 — the glacial/interglacial timeline
    "timeline": [
        {"op": "place", "element": "timeline", "slot": "wide", "name": "tl", "start": "45万年前", "end": "現在",
         "bands": [{"from": 0, "to": 1, "colour": "cyan", "name": "glacial"}]
                  + [{"from": f, "to": f + 0.035, "colour": "pink", "name": f"warm{i}"}
                     for i, f in enumerate((0.14, 0.33, 0.52, 0.71, 0.965))]},
        {"op": "label", "text": "氷期", "at": "tl.glacial", "colour": "cyan", "side": "above", "pointer": True},
        {"op": "label", "text": "間氷期", "at": "tl.warm3", "colour": "pink", "side": "above", "pointer": True},
    ],
    # dark matter 8:00 — atom, then 3:15 — earth/moon compare
    "atom_orbit": [
        {"op": "place", "element": "atom", "slot": "left", "name": "atom", "electrons": 2},
        {"op": "label", "text": "原子核", "at": "atom.nucleus", "colour": "pink", "side": "below", "size": 40},
        {"op": "label", "text": "電子", "at": "atom.electron1", "colour": "blue", "side": "right", "size": 40},
        {"op": "place", "element": "earth_globe", "slot": "right", "name": "earth"},
        {"op": "add", "element": "moon", "near": "earth", "name": "moon"},
        {"op": "arrow", "from": "moon", "to": "earth", "colour": "white"},
    ],
    # black hole 12:45 — the spacetime grid with an apple drifting along time
    "spacetime": [
        {"op": "background", "name": "space"},
        {"op": "heading", "text": "重力の原因 ＝ 質量による時間の遅れ", "colour": "yellow", "align": "left"},
        {"op": "panel", "name": "st", "slot": "center", "x": "時間", "y": "高さ（空間）"},
        {"op": "place", "element": "charge", "slot": "center", "name": "apple", "box": [0.3, 0.2, 0.36, 0.28]},
        {"op": "arrow", "from": "st.cell_1_9", "to": "st.cell_11_9", "colour": "yellow"},
    ],
    # black hole 13:15 — the same grid, warped
    "spacetime_warp": [
        {"op": "background", "name": "space"},
        {"op": "panel", "name": "st", "slot": "center", "x": "時間", "y": "高さ（空間）", "warp": 0.6},
        {"op": "arrow", "from": "st.cell_1_9", "to": "st.cell_11_1", "colour": "white", "bulge": -120},
    ],
    # multiverse 5:15 — the vertical energy-density scale
    "scale": [
        {"op": "place", "element": "number_line", "slot": "center", "name": "nl", "vertical": True,
         "markers": [{"at": 0.92, "label": "理想的な空間のエネルギー密度"},
                     {"at": 0.12, "label": "実際の空間のエネルギー密度"}]},
        {"op": "arrow", "from": "nl.理想的な空間のエネルギー密度", "to": "nl.実際の空間のエネルギー密度", "colour": "yellow", "pad": 40},
        {"op": "label", "text": "120桁小さい", "at": "right", "colour": "yellow"},
    ],
    # multiverse 7:45 — two theories, with a clash between them
    "columns": [
        {"op": "columns", "name": "cols", "items": [
            {"title": "量子力学", "colour": "yellow", "text": "素粒子などの\n小さな粒子の運動を説明"},
            {"title": "一般相対性理論", "colour": "cyan", "text": "大きな世界の重力を\n時空の歪みで説明"}]},
        {"op": "arrow", "from": "cols.量子力学", "to": "cols.一般相対性理論", "heads": 2, "pad": 30},
        {"op": "label", "text": "ミクロな世界", "at": "cols.量子力学.text", "side": "below", "colour": "yellow", "pointer": True, "name": "micro"},
        {"op": "label", "text": "マクロな世界", "at": "cols.一般相対性理論.text", "side": "below", "colour": "cyan", "pointer": True},
    ],
    # multiverse 20:15 — the "negative feedback" title over a dimmed figure, with list
    "title_dim": [
        {"op": "place", "element": "earth_globe", "slot": "center-small", "name": "e", "ice": True},
        {"op": "list_add", "text": "太陽光の強さ"},
        {"op": "list_add", "text": "地球の光吸収率"},
        {"op": "list_add", "text": "大気の温室効果"},
        {"op": "title", "text": "負のフィードバック", "dim": True},
    ],
}

SUBTITLES = {
    "ice_feedback": ("ちなみにこのように、ある出来事自体がさらにその出来事を\n加速させるような現象のことを、正のフィードバックというのだ", "explainer"),
    "pie": ("この2つは一体どんなものなのかしら？", "listener"),
    "box_row": ("この2つの力はとてもミクロな世界で影響する力で、\n私たちの身近に感じたり見たりできる力ではないのだ", "explainer"),
    "table": ("なるほどね", "listener"),
    "chain": ("「この銀河の中には、質量をもった\n目には見えない物質が存在するのではないか」と", "explainer"),
    "timeline": ("なるほど、今は氷河時代の中でも\n暖かい時期なのね", "listener"),
    "atom_orbit": ("原子核の周りを電子がぐるぐると回っているのだ", "explainer"),
    "title_dim": ("このような、ある出来事自体がその出来事を\n食い止めるように働く効果を、負のフィードバック効果と呼ぶのだ", "explainer"),
    "spacetime": ("重力がなくてずっと浮いているから、\n時間がたっても空間的な位置は変わらないってことね", "listener"),
    "spacetime_warp": ("歪んだ時空ではまっすぐだったのに、\nこれだと物体が落ちていくように見えるわね", "listener"),
    "scale": ("理想的な値と比べると\n120桁も小さい値であることが分かっているんだ", "explainer"),
    "columns": ("しかし、この2つの理論は欠陥を抱えており、\nお互いに相いれない理論になっていた", "explainer"),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for name, ops in SCENES.items():
        canvas = Canvas()
        for op in ops:
            canvas.apply(op)
        subtitle, speaker = SUBTITLES.get(name, ("", "explainer"))
        img = canvas.render(subtitle=subtitle, speaker=speaker)
        img.save(OUT / f"{name}.png")
        frames.append(img)
        print(f"  {name}")

    cols = 2
    rows = (len(frames) + cols - 1) // cols
    tw, th = 960, 540
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 6, rows * th + (rows + 1) * 6), (40, 40, 40))
    for i, f in enumerate(frames):
        sheet.paste(f.resize((tw, th), Image.LANCZOS), (6 + (i % cols) * (tw + 6), 6 + (i // cols) * (th + 6)))
    sheet.save(OUT / "sheet.png")
    print(OUT / "sheet.png")


if __name__ == "__main__":
    main()
