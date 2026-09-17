"""Whether a finished video is good enough to show anyone.

The numbers that separated the reference videos from the first render:
how often the stage changes, how long it ever sits still, whether the
subtitles fit, how many figures the script asked for that could not be
drawn. All of it is known from the narration timeline and the script
before ffmpeg runs, so the score is cheap and deterministic; the contact
sheet from tools/analyze_reference.py is for eyes, this is for the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .canvas import wrap_subtitle

# Targets, overridable from settings.yaml `quality:`. The reference channel
# runs 5-13 stage changes a minute with a longest hold of 67-133 s (mostly
# the room at the start); the first render had 1 change in 21 minutes.
DEFAULTS = {
    "min_changes_per_minute": 4.0,
    "max_static_seconds": 40.0,
    "max_three_row_subtitles": 0.05,     # share of lines wrapping to three rows
    "max_dropped_visuals": 6,
    "min_minutes": 8.0,
}


@dataclass(slots=True)
class QualityReport:
    minutes: float
    changes_per_minute: float
    max_static_seconds: float
    static_at: float                       # where the longest hold starts (s)
    three_row_share: float
    dropped_visuals: int
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def describe(self) -> str:
        lines = [
            f"- 長さ {self.minutes:.1f} 分",
            f"- ステージ変化 {self.changes_per_minute:.1f} 回/分",
            f"- 最長の静止 {self.max_static_seconds:.0f} 秒（{self.static_at / 60:.1f} 分あたり）",
            f"- 3行になる字幕 {self.three_row_share:.0%}",
            f"- 描けずに落とした板書 {self.dropped_visuals} 行",
        ]
        if self.problems:
            lines += ["", "基準未満:"] + [f"- {p}" for p in self.problems]
        else:
            lines += ["", "基準を満たしている"]
        return "\n".join(lines)


def _changes(ops: list[dict[str, Any]]) -> bool:
    return any(op.get("op") not in ("hold", "expression") for op in ops or [])


def score(timeline: list[dict[str, Any]], dropped_visuals: int = 0,
          thresholds: dict[str, Any] | None = None) -> QualityReport:
    """Score a narrated timeline: each entry has start_s, end_s, display, ops."""
    t = {**DEFAULTS, **(thresholds or {})}
    if not timeline:
        return QualityReport(0, 0, 0, 0, 0, dropped_visuals, ["タイムラインが空"])

    duration = float(timeline[-1]["end_s"])
    minutes = duration / 60
    changes = sum(1 for e in timeline if _changes(e.get("ops") or []))
    per_minute = changes / max(minutes, 0.01)

    # longest stretch with no stage change, in seconds of audio
    longest, longest_at = 0.0, 0.0
    run_start = float(timeline[0]["start_s"])
    for entry in timeline:
        if _changes(entry.get("ops") or []):
            run_start = float(entry["start_s"])
        held = float(entry["end_s"]) - run_start
        if held > longest:
            longest, longest_at = held, run_start

    three = sum(1 for e in timeline if len(wrap_subtitle(e.get("display", ""))) >= 3)
    three_share = three / len(timeline)

    problems = []
    if minutes < t["min_minutes"]:
        problems.append(f"短すぎる: {minutes:.1f} 分（{t['min_minutes']:.0f} 分以上）")
    if per_minute < t["min_changes_per_minute"]:
        problems.append(f"図が動かなすぎる: {per_minute:.1f} 回/分（{t['min_changes_per_minute']:.0f} 回/分以上）")
    if longest > t["max_static_seconds"]:
        problems.append(f"{longest_at / 60:.1f} 分あたりで {longest:.0f} 秒静止（{t['max_static_seconds']:.0f} 秒まで）")
    if three_share > t["max_three_row_subtitles"]:
        problems.append(f"字幕が3行になる行が {three_share:.0%}（{t['max_three_row_subtitles']:.0%} まで）")
    if dropped_visuals > t["max_dropped_visuals"]:
        problems.append(f"描けなかった板書が {dropped_visuals} 行（{t['max_dropped_visuals']} 行まで）")
    return QualityReport(minutes, per_minute, longest, longest_at, three_share, dropped_visuals, problems)
