"""Every picture the script may place, whatever it came from.

Two sources today. Noto Emoji ships with the repository and covers things
— rulers, bells, temples, planets — at no licence cost and no count limit.
いらすとや covers what emoji cannot: people in situations, with faces and
posture ("困っている人", "望遠鏡をのぞく人"), which is what a Japanese
explainer channel actually looks like. Its terms allow free use of **up to
20 illustrations per work**, so those are counted per video and the count
is enforced at script time; beyond that the licence is commercial and the
pipeline must not cross it silently.

A pack is a YAML file under assets/packs/ listing `name: [file, 日本語]`
plus the licence to credit. Adding a pack is adding a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .illustrations import ILLUSTRATIONS


@dataclass(slots=True)
class Pack:
    """One source of pictures, with what its licence demands."""

    id: str
    title: str
    source: str
    licence: str
    credit: str
    # None means no limit; いらすとや's own terms say 20 per work.
    max_per_video: int | None = None
    # name -> (file stem under assets/illustrations, 日本語)
    entries: dict[str, tuple[str, str]] = field(default_factory=dict)


NOTO = Pack(
    id="noto",
    title="Noto Emoji (Google)",
    source="https://github.com/googlefonts/noto-emoji",
    licence="Apache License 2.0",
    credit="Illustrations: Noto Emoji (Apache-2.0)",
    max_per_video=None,
    entries={name: (name, jp) for name, (_, jp) in ILLUSTRATIONS.items()},
)


def _packs_dir(root: Path) -> Path:
    return root / "packs"


@lru_cache(maxsize=4)
def load_packs(root: Path) -> tuple[Pack, ...]:
    """Noto plus every pack file under assets/packs/*.yaml."""
    packs = [NOTO]
    folder = _packs_dir(Path(root))
    if folder.exists():
        for path in sorted(folder.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entries = {}
            for name, value in (data.get("pictures") or {}).items():
                if isinstance(value, (list, tuple)):
                    stem, jp = (list(value) + ["", ""])[:2]
                else:
                    stem, jp = name, str(value)
                entries[str(name)] = (str(stem or name), str(jp))
            packs.append(Pack(
                id=str(data.get("id") or path.stem),
                title=str(data.get("title") or path.stem),
                source=str(data.get("source") or ""),
                licence=str(data.get("licence") or ""),
                credit=str(data.get("credit") or ""),
                max_per_video=data.get("max_per_video"),
                entries=entries,
            ))
    return tuple(packs)


def pictures(root: Path) -> dict[str, tuple[str, str, Pack]]:
    """name -> (file stem, 日本語, pack). Later packs win a name clash."""
    out: dict[str, tuple[str, str, Pack]] = {}
    for pack in load_packs(Path(root)):
        for name, (stem, jp) in pack.entries.items():
            out[name] = (stem, jp, pack)
    return out


def japanese_index(root: Path) -> list[tuple[str, str]]:
    """(日本語, name) for every picture, longest word first — what the
    script stage matches a chapter's lines against."""
    pairs = [(jp, name) for name, (_, jp, _) in pictures(Path(root)).items() if jp]
    return sorted(pairs, key=lambda pair: -len(pair[0]))


def limited(root: Path) -> dict[str, int]:
    """pack id -> per-video limit, for packs that have one."""
    return {p.id: p.max_per_video for p in load_packs(Path(root)) if p.max_per_video}


def pack_of(root: Path, name: str) -> Pack | None:
    entry = pictures(Path(root)).get(name)
    return entry[2] if entry else None


def count_by_pack(root: Path, names: list[str]) -> dict[str, int]:
    """How many *distinct* pictures from each limited pack a video uses.

    いらすとや counts illustrations, not placements: the same picture used
    in three chapters is one illustration.
    """
    index = pictures(Path(root))
    seen: dict[str, set[str]] = {}
    for name in names:
        entry = index.get(name)
        if entry and entry[2].max_per_video:
            seen.setdefault(entry[2].id, set()).add(name)
    return {pack_id: len(names) for pack_id, names in seen.items()}


def credits(root: Path, names: list[str]) -> list[str]:
    """The credit lines for the packs a video actually drew from."""
    index = pictures(Path(root))
    used = {index[name][2].id: index[name][2] for name in names if name in index}
    return [pack.credit for pack in used.values() if pack.credit]


def manual_lines(root: Path) -> str:
    """The catalogue as the prompt shows it: 日本語=名前, twelve to a line."""
    items = [f"{jp}={name}" for name, (_, jp, _) in sorted(pictures(Path(root)).items()) if jp]
    return "\n".join("  " + " ".join(items[i:i + 12]) for i in range(0, len(items), 12))
