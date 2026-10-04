"""Checking that a script's facts trace back to its sources.

The plan said "numbers come from the sources". A sentence in a plan does not
stop a language model inventing a figure, so the requirement is enforced here:
every line carrying a number must name at least one source ref, every ref must
exist, and a script that fails is rejected before anything is narrated or paid for.

Being strict is the point. The channel's one durable advantage over the
summary-rewriting channels is that its numbers can be checked, and that only
holds if a line without a citation cannot ship.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Digits that carry a factual claim. Deliberately narrow: chapter numbers and
# ordinary counting words should not demand a citation.
NUMBER_PATTERN = re.compile(
    r"""
      \d+(?:\.\d+)?\s*(?:億|兆|万|京)?\s*
      (?:光年|天文単位|パーセク|キロメートル|メートル|キログラム|トン
        |ケルビン|度|パーセント|倍|年|日|時間|分|秒|個|回|%)
    | \d+(?:\.\d+)?\s*かける\s*10の\d+乗
    | \d{4}\s*年
    | \d+(?:\.\d+)?\s*[×x]\s*10
    """,
    re.VERBOSE,
)

REF_PATTERN = re.compile(r"\bS\d+\b")

# Hedges that mark a sentence as explicitly uncertain. A line that says "we do
# not know yet" is not making a factual claim and does not need a source.
HEDGES = (
    "かもしれない",
    "とされる説",
    "仮説",
    "分かっていない",
    "わかっていない",
    "議論が続いて",
    "確認されていない",
    "推測",
)


@dataclass(slots=True)
class CitationReport:
    uncited: list[str] = field(default_factory=list)
    unknown_refs: list[str] = field(default_factory=list)
    unused_refs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.uncited and not self.unknown_refs

    def describe(self) -> str:
        parts = []
        if self.uncited:
            shown = "; ".join(line[:70] for line in self.uncited[:3])
            parts.append(f"{len(self.uncited)} 行が数値を出典なしで述べている: {shown}")
        if self.unknown_refs:
            parts.append(f"存在しない出典ID: {sorted(set(self.unknown_refs))}")
        if self.unused_refs:
            parts.append(f"一度も引用されなかった出典: {sorted(set(self.unused_refs))}")
        return " / ".join(parts)


# A number that is a decade ("1920年代") or explicitly rough ("100年近く",
# "3倍ほど") is textbook context, not a figure a viewer would check.
APPROXIMATE_AFTER = ("代", "近く", "ほど", "くらい", "ぐらい", "以来", "あまり", "前後")


def _checkable_numbers(text: str) -> list[str]:
    found = []
    for match in NUMBER_PATTERN.finditer(text):
        tail = text[match.end():match.end() + 2]
        if any(tail.startswith(a) for a in APPROXIMATE_AFTER):
            continue
        found.append(match.group(0))
    return found


def carries_a_claim(text: str) -> bool:
    """Whether this line states a number that a viewer could check."""
    if any(hedge in text for hedge in HEDGES):
        return False
    return bool(_checkable_numbers(text))


def check(chapters: list[dict], known_refs: set[str]) -> CitationReport:
    """Validate a script's citations against the refs research collected."""
    report = CitationReport()
    used: set[str] = set()
    # numbers stated with a citation in the last two lines; the listener
    # repeating "たった5パーセント？" is dialogue, not a new claim
    recent_cited: list[set[str]] = []

    for chapter in chapters:
        for line in chapter.get("lines", []):
            display = line.get("display", "")
            refs = [r for r in line.get("refs", []) if r]
            used.update(refs)

            for ref in refs:
                if ref not in known_refs:
                    report.unknown_refs.append(ref)

            numbers = {n.replace(" ", "") for n in _checkable_numbers(display)}
            if carries_a_claim(display) and not refs:
                echoed = numbers and numbers <= set().union(*recent_cited[-2:]) if recent_cited else False
                if not echoed:
                    report.uncited.append(display)
            recent_cited.append(numbers if refs else set())

    report.unused_refs = sorted(known_refs - used)
    return report


# Handed to the script model. Kept beside the checker so the rule the model is
# told and the rule that is enforced cannot drift apart.
CITATION_RULES = """\
数値を述べる行には、必ず refs に出典ID（S1, S2, ...）を入れること。

- 出典IDは与えられたソース一覧にあるものだけを使う。存在しないIDは書かない。
- 距離・質量・温度・年代・割合・個数など、視聴者が確かめられる数字を含む行が対象。
- 「まだ分かっていない」「〜という仮説がある」のように不確かさを明示した行は、
  事実の主張ではないので出典は不要。
- 与えられたソースに書かれていない数値は**書かないこと**。記憶から補わない。
- すべてのソースを最低一度は引用すること。使わないソースがあるなら選び直す。"""
