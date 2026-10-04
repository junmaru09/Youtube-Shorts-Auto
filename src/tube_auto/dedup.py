"""Novelty gate.

Two layers, because they catch different failures:
  1. `dedup_key` — an exact hash, enforced by a UNIQUE constraint, so a retry
     or a double-run can never insert the same idea twice.
  2. Jaccard similarity — catches a reworded version of a shot we already made.
     YouTube's inauthentic-content rules key on template repetition, so the
     default threshold mirrors the commonly cited "under 70% similarity" bar.
"""

from __future__ import annotations

import hashlib
import re

SIMILARITY_THRESHOLD = 0.70

# Words too common to signal that two scenes are the same shot.
_STOPWORDS = frozenset(
    """
    a an and as at by for from in into of on onto or over the through to with
    its it is are be being been that this these those his her their
    shot footage camera vertical close closeup macro view angle scene video
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(_TOKEN_RE.findall(text.lower()))


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in _STOPWORDS and len(t) > 2}


def make_key(series_id: str, scene_summary: str) -> str:
    """Stable hash of the normalised scene. Same wording -> same key."""
    payload = f"{series_id}|{normalize(scene_summary)}".encode()
    return hashlib.sha1(payload).hexdigest()


def similarity(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_too_similar(
    candidate: str,
    existing: list[str],
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[bool, str | None]:
    """Return (verdict, the existing scene it collided with)."""
    for scene in existing:
        if similarity(candidate, scene) >= threshold:
            return True, scene
    return False, None
