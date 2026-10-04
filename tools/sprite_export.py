"""Cut character sprites out of a PSDTool-style 立ち絵 PSD.

The community 立ち絵 (坂本アヒル's ずんだもん / 四国めたん) are PSDs whose layer
groups hold the alternatives: 目/通常, 目/にっこり, 口/むふ, 口/ほあー … One
sprite is one choice per group. The renderer needs a PNG per expression and
mouth state, all cut to the same box so the mouth flap does not jitter, and
that is a job for a script, not for toggling layers by hand twelve times.

    python tools/sprite_export.py list  zundamon.psd          # the layer tree, with the paths this tool uses
    python tools/sprite_export.py guess zundamon.psd          # a draft recipe from the layer names, to edit
    python tools/sprite_export.py export zundamon.psd assets/sprites/zundamon --recipe config/sprites/zundamon.yaml

Recipe (YAML):

    size: 900                     # longest side of each PNG
    base: [体/服, 顔/肌]           # visible in every frame
    expressions:
      normal:    [目/通常, 眉/通常]
      happy:     [目/にっこり, 眉/通常]
      surprised: [目/見開き, 眉/上]
      thinking:  [目/半目, 眉/困り]
      sad:       [目/伏せ, 眉/困り]
    mouth:
      closed: 口/むふ
      open:   口/ほあー

Layer paths are "/"-joined names from the root. PSDTool's "!" (radio) and
"*" (default) prefixes are ignored when matching, and a path may be given by
its unique tail ("むふ" alone) when that is unambiguous.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tube_auto.models import EXPRESSIONS  # noqa: E402

MARGIN = 12


def _clean(name: str) -> str:
    return name.lstrip("!*").strip()


def _paths(psd) -> dict[int, tuple[Any, str]]:
    """id(layer) -> (layer, cleaned "/"-joined path), for every layer and group.

    Keyed by object identity, not `layer_id`: files not saved by Photoshop
    (and psd-tools' own output) carry no layer-id block, and then every
    layer_id is -1.
    """
    out: dict[int, tuple[Any, str]] = {}

    def walk(group, prefix: str) -> None:
        for layer in group:
            path = f"{prefix}/{_clean(layer.name)}" if prefix else _clean(layer.name)
            out[id(layer)] = (layer, path)
            if layer.is_group():
                walk(layer, path)

    walk(psd, "")
    return out


def _resolve(paths: dict[int, tuple[Any, str]], wanted: str) -> int:
    """A recipe entry to a layer key: exact path, else a unique tail match."""
    wanted = _clean(wanted)
    exact = [key for key, (_, p) in paths.items() if p == wanted]
    if len(exact) == 1:
        return exact[0]
    tails = [key for key, (_, p) in paths.items() if p.endswith("/" + wanted) or p == wanted]
    if len(tails) == 1:
        return tails[0]
    if not tails:
        raise SystemExit(f"no layer matches {wanted!r}; run `list` and copy a path from there")
    raise SystemExit(f"{wanted!r} is ambiguous: " + ", ".join(paths[k][1] for k in tails))


def _with_ancestors(psd, paths: dict[int, tuple[Any, str]], keys: set[int]) -> set[int]:
    out = set(keys)
    for key in keys:
        parent = paths[key][0].parent
        while parent is not None and parent is not psd:
            out.add(id(parent))
            parent = parent.parent
    return out


def _render(psd, paths: dict[int, tuple[Any, str]], keys: set[int]) -> Image.Image:
    keep = _with_ancestors(psd, paths, keys)
    return psd.composite(layer_filter=lambda layer: id(layer) in keep, force=True).convert("RGBA")


def cmd_list(args: argparse.Namespace) -> int:
    from psd_tools import PSDImage

    psd = PSDImage.open(args.psd)
    print(f"{args.psd}: {psd.width}x{psd.height}, {len(list(psd.descendants()))} layers")

    def walk(group, depth: int) -> None:
        for layer in group:
            mark = "▸" if layer.is_group() else "·"
            vis = " " if layer.visible else "×"
            print(f"{'  ' * depth}{mark}{vis} {layer.name}")
            if layer.is_group():
                walk(layer, depth + 1)

    walk(psd, 0)
    print("\n(▸ group  · layer  × hidden in the file)  paths are group/layer names joined with '/'")
    return 0


# Words that usually mark each alternative in these PSDs. A draft, not a rule:
# the operator edits the recipe after `list`.
_GUESS = {
    "mouth_open": ("ほあ", "あ", "開", "わ", "お"),
    "mouth_closed": ("むふ", "む", "閉", "通常", "普通", "基本"),
    "normal": ("通常", "普通", "基本", "ノーマル"),
    "happy": ("にっこり", "にこ", "笑", "喜", "嬉", "ニコ"),
    "surprised": ("驚", "見開", "びっくり", "ビックリ"),
    "thinking": ("半目", "ジト", "考", "疑", "横目"),
    "sad": ("泣", "困", "悲", "涙", "しょんぼり"),
}


def cmd_guess(args: argparse.Namespace) -> int:
    from psd_tools import PSDImage

    psd = PSDImage.open(args.psd)
    paths = _paths(psd)
    leaves = {key: p for key, (layer, p) in paths.items() if not layer.is_group()}

    def pick(group_word: str, words: tuple[str, ...]) -> str | None:
        for p in leaves.values():
            top = p.split("/")[0]
            leaf = p.split("/")[-1]
            if group_word in top and any(w in leaf for w in words):
                return p
        return None

    base = sorted({p.split("/")[0] for p in leaves.values()
                   if not any(g in p.split("/")[0] for g in ("目", "口", "眉"))})
    recipe: dict[str, Any] = {
        "size": 900,
        "base": base,
        "expressions": {
            expr: [x for x in (pick("目", _GUESS[expr]), pick("眉", _GUESS[expr] if expr != "normal" else _GUESS["normal"]))
                   if x]
            for expr in EXPRESSIONS
        },
        "mouth": {"closed": pick("口", _GUESS["mouth_closed"]), "open": pick("口", _GUESS["mouth_open"])},
    }
    print("# draft — check every line against `list`; groups in `base` show ALL their layers,")
    print("# so replace a group with the one layer you want (体/服1, not 体)")
    print(yaml.safe_dump(recipe, allow_unicode=True, sort_keys=False))
    return 0


def export(psd, recipe: dict[str, Any], outdir: Path) -> list[Path]:
    paths = _paths(psd)
    base = {_resolve(paths, p) for p in recipe.get("base", [])}
    mouth = {state: _resolve(paths, p) for state, p in recipe["mouth"].items()}
    expressions = {name: {_resolve(paths, p) for p in layers} for name, layers in recipe["expressions"].items()}
    if "normal" not in expressions:
        raise SystemExit("the recipe needs at least a `normal` expression")

    frames: dict[str, Image.Image] = {}
    for expr, layers in expressions.items():
        for state, mouth_key in mouth.items():
            frames[f"{expr}_{state}"] = _render(psd, paths, base | layers | {mouth_key})

    # one crop for every frame, so nothing shifts between open and closed
    boxes = [im.getbbox() for im in frames.values() if im.getbbox()]
    if not boxes:
        raise SystemExit("every frame came out empty; the recipe's layers are probably all hidden groups")
    x0 = max(0, min(b[0] for b in boxes) - MARGIN)
    y0 = max(0, min(b[1] for b in boxes) - MARGIN)
    x1 = min(psd.width, max(b[2] for b in boxes) + MARGIN)
    y1 = min(psd.height, max(b[3] for b in boxes) + MARGIN)
    size = int(recipe.get("size", 900))
    scale = size / max(x1 - x0, y1 - y0)

    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, im in frames.items():
        cut = im.crop((x0, y0, x1, y1))
        if scale < 1:
            cut = cut.resize((round(cut.width * scale), round(cut.height * scale)), Image.LANCZOS)
        path = outdir / f"{name}.png"
        cut.save(path)
        written.append(path)
    return written


def cmd_export(args: argparse.Namespace) -> int:
    from psd_tools import PSDImage

    recipe = yaml.safe_load(Path(args.recipe).read_text(encoding="utf-8"))
    written = export(PSDImage.open(args.psd), recipe, Path(args.outdir))
    for path in written:
        print(path)
    print(f"\n{len(written)} sprites. Add the PSD's source and licence to assets/library.yaml.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list"); p.add_argument("psd"); p.set_defaults(func=cmd_list)
    p = sub.add_parser("guess"); p.add_argument("psd"); p.set_defaults(func=cmd_guess)
    p = sub.add_parser("export"); p.add_argument("psd"); p.add_argument("outdir")
    p.add_argument("--recipe", required=True); p.set_defaults(func=cmd_export)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
