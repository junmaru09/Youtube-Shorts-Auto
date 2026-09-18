"""Register hand-downloaded illustrations as a pack the script can use.

    python tools/add_pictures.py ~/Downloads/irasutoya --pack irasutoya

Every image in the folder becomes a picture: trimmed of its transparent
margin, scaled to 512px, saved as assets/illustrations/<name>.png, and
listed in assets/packs/<pack>.yaml with the Japanese word the script will
recognise. The name and the word come from the filename, so name the files
in Japanese as you save them —

    驚く人.png            → surprised_person / 驚く人
    望遠鏡をのぞく人.png   → telescope_person / 望遠鏡をのぞく人

— or pass `--map file.tsv` with `ファイル名<TAB>英名<TAB>日本語` per line
when the filenames are what the site gave you.

いらすとや's terms allow 20 illustrations per work for free; the pack
records that limit and the script stage refuses a script that crosses it.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "illustrations"
PACKS = ROOT / "assets" / "packs"
SIZE = 512

PRESETS = {
    "irasutoya": {
        "title": "いらすとや",
        "source": "https://www.irasutoya.com/",
        "licence": "いらすとや ご利用規定（1つの作品につき20点まで無料、21点以上は有償）",
        "credit": "イラスト: いらすとや",
        "max_per_video": 20,
    },
}

# Japanese word -> a stable English name, for the common cases. Anything not
# covered falls back to a romanised-ish slug of the filename, which the
# operator can rename in the YAML afterwards.
_WORDS = {
    "人": "person", "男性": "man", "女性": "woman", "子供": "child", "子ども": "child",
    "驚く": "surprised", "考える": "thinking", "喜ぶ": "happy", "困る": "troubled", "泣く": "crying",
    "怒る": "angry", "笑う": "laughing", "寝る": "sleeping", "走る": "running", "歩く": "walking",
    "見る": "looking", "話す": "talking", "聞く": "listening", "書く": "writing", "読む": "reading",
    "科学者": "scientist", "博士": "professor", "先生": "teacher", "学生": "student", "医者": "doctor",
    "実験": "experiment", "研究": "research", "観測": "observation", "計算": "calculation",
    "望遠鏡": "telescope", "顕微鏡": "microscope", "地球": "earth", "宇宙": "space", "星": "star",
    "太陽": "sun", "月": "moon", "鐘": "bell", "寺": "temple", "神社": "shrine", "山": "mountain",
    "海": "sea", "川": "river", "空": "sky", "雨": "rain", "雪": "snow", "雲": "cloud", "風": "wind",
    "時計": "clock", "本": "book", "家": "house", "車": "car", "電車": "train", "飛行機": "airplane",
}


# What the sites append to every filename and the script would never say.
_NOISE = re.compile(r"(のイラスト|イラスト|素材|画像|フリー|無料|かわいい|シンプル)+$")


def _japanese(stem: str) -> str:
    """The word a viewer would say: 驚く人のイラスト（女性） → 驚く人."""
    text = unicodedata.normalize("NFKC", stem).strip()
    text = re.sub(r"[（(\[].*?[)\]）]", "", text)
    text = re.sub(r"[_\-\s]*\d+$", "", text)
    while True:
        trimmed = _NOISE.sub("", text).strip("　 ・")
        if trimmed == text:
            return text
        text = trimmed


def _slug(name: str) -> str:
    """An English-ish name from a Japanese filename, words first."""
    stem = unicodedata.normalize("NFKC", name).strip()
    stem = re.sub(r"[（(\[].*?[)\]）]", "", stem)          # drop "（女性）" and the like
    parts = [en for jp, en in sorted(_WORDS.items(), key=lambda kv: -len(kv[0])) if jp in stem]
    if parts:
        # subject last: 驚く人 -> surprised_person
        subject = [p for p in parts if p in ("person", "man", "woman", "child", "scientist", "professor",
                                             "teacher", "student", "doctor")]
        rest = [p for p in parts if p not in subject]
        ordered = rest + subject
        return "_".join(dict.fromkeys(ordered))[:40]
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    return ascii_only or "picture"


def _trim(image: Image.Image) -> Image.Image:
    """Crop the transparent (or white) margin the sites leave around a PNG."""
    rgba = image.convert("RGBA")
    box = rgba.getbbox()
    if rgba.getextrema()[3][0] == 255:       # fully opaque: treat near-white as background
        grey = rgba.convert("L").point(lambda v: 0 if v > 244 else 255)
        box = grey.getbbox() or box
    return rgba.crop(box) if box else rgba


def _fit(image: Image.Image, size: int = SIZE) -> Image.Image:
    scale = size / max(image.width, image.height)
    if scale >= 1:
        return image
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)


def _mapping(path: Path | None) -> dict[str, tuple[str, str]]:
    if path is None:
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 3:
            out[parts[0]] = (parts[1], parts[2])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="the folder of downloaded images")
    parser.add_argument("--pack", required=True, help="pack id, e.g. irasutoya")
    parser.add_argument("--map", type=Path, default=None, help="optional filename<TAB>name<TAB>日本語 file")
    parser.add_argument("--title"); parser.add_argument("--source")
    parser.add_argument("--licence"); parser.add_argument("--credit")
    parser.add_argument("--max-per-video", type=int, default=None)
    args = parser.parse_args()

    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"no such folder: {folder}", file=sys.stderr)
        return 1
    preset = PRESETS.get(args.pack, {})
    pack_file = PACKS / f"{args.pack}.yaml"
    data = yaml.safe_load(pack_file.read_text(encoding="utf-8")) if pack_file.exists() else {}
    data = data or {}
    data.setdefault("id", args.pack)
    for key, flag in (("title", args.title), ("source", args.source), ("licence", args.licence), ("credit", args.credit)):
        value = flag or data.get(key) or preset.get(key)
        if value:
            data[key] = value
    limit = args.max_per_video if args.max_per_video is not None else data.get("max_per_video", preset.get("max_per_video"))
    if limit:
        data["max_per_video"] = int(limit)
    pictures = dict(data.get("pictures") or {})

    mapping = _mapping(args.map)
    OUT.mkdir(parents=True, exist_ok=True)
    PACKS.mkdir(parents=True, exist_ok=True)
    added, skipped = [], []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            continue
        name, japanese = mapping.get(path.name, (None, None))
        if name is None:
            japanese = _japanese(path.stem)
            name = _slug(path.stem)
        while name in pictures and pictures[name][1] != japanese:
            name += "2"
        try:
            image = _fit(_trim(Image.open(path)))
        except OSError as exc:
            skipped.append(f"{path.name}: {exc}")
            continue
        image.save(OUT / f"{name}.png")
        pictures[name] = [name, japanese]
        added.append(f"{path.name} → {name} / {japanese}")

    data["pictures"] = dict(sorted(pictures.items()))
    pack_file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    for line in added:
        print(" ", line)
    for line in skipped:
        print("  skipped:", line)
    print(f"\n{len(added)} added, {len(pictures)} in {pack_file}")
    if limit:
        print(f"1本あたり{limit}点まで。台本の検証が超過を差し戻します。")
    print("日本語が台詞と合っているか確認してください（台詞の語と一致した絵が置かれます）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
