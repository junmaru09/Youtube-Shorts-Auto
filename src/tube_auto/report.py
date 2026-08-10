"""The expand-or-stop report.

This exists to answer one question with numbers instead of hope: is this worth
continuing? Everything else in the project is machinery for producing the data
this reads.

Four things it refuses to do, each because the obvious alternative misleads:

- **Guess at RPM once real figures exist.** The starting range is 150-450 JPY
  per thousand views, which is wide enough to move the required view count by a
  factor of three. The moment YouTube reports actual revenue, that replaces the
  assumption and the whole forecast is recomputed.
- **Report views when the gate is watch hours.** Monetisation needs 4,000 hours
  in twelve months, and a video's contribution is its length times its retention
  times its views. A channel can be ahead on views and far behind on the gate.
- **Give a verdict before there is evidence for one.** A confident
  recommendation off three videos is worse than none.
- **Count only what published.** Rejected and failed work cost the same money.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import config, db, scoring
from .models import Arm

SCORING_WINDOW = "72h"


@dataclass(slots=True)
class ArmRow:
    arm: Arm
    views: list[int] = field(default_factory=list)
    retention: list[float] = field(default_factory=list)
    watch_hours: float = 0.0
    cost_usd: float = 0.0
    ideas: int = 0
    live: int = 0
    rejected: int = 0
    failed: int = 0
    share: float = 0.0

    @property
    def measured(self) -> int:
        return len(self.views)

    @property
    def total_views(self) -> int:
        return sum(self.views)

    @property
    def mean_views(self) -> float:
        return statistics.fmean(self.views) if self.views else 0.0

    @property
    def median_views(self) -> float:
        return statistics.median(self.views) if self.views else 0.0

    @property
    def best_views(self) -> int:
        return max(self.views) if self.views else 0

    @property
    def mean_retention(self) -> float | None:
        return statistics.fmean(self.retention) if self.retention else None

    @property
    def cost_per_1k_views(self) -> float | None:
        if self.total_views <= 0:
            return None
        return self.cost_usd / (self.total_views / 1000)


@dataclass(slots=True)
class Report:
    arms: list[ArmRow]
    jpy_per_usd: float
    target_gross_jpy: float
    # The monetisation gate.
    watch_hours: float
    watch_hours_target: float
    videos_live: int
    videos_to_gate: int
    days_since_first: float
    # Money.
    cost_usd: float
    llm_cost_usd: float
    revenue_jpy: float | None      # None until YouTube reports real revenue
    assumed_rpm: dict[str, float]
    measured_rpm_jpy: float | None
    total_views: int
    measured_videos: int
    retention_floor: float
    min_samples: int
    private_waiting: int
    pending_review: int

    @property
    def cost_jpy(self) -> float:
        return self.cost_usd * self.jpy_per_usd

    @property
    def rpm_jpy(self) -> dict[str, float]:
        """Measured RPM if there is one, otherwise the assumed range."""
        if self.measured_rpm_jpy is not None:
            return {"low": self.measured_rpm_jpy, "mid": self.measured_rpm_jpy,
                    "high": self.measured_rpm_jpy}
        return self.assumed_rpm

    @property
    def gate_progress(self) -> float:
        if self.watch_hours_target <= 0:
            return 0.0
        return self.watch_hours / self.watch_hours_target

    @property
    def hours_per_video(self) -> float:
        return self.watch_hours / self.videos_live if self.videos_live else 0.0

    @property
    def videos_still_needed(self) -> float:
        """At the observed hours-per-video, how many more before the gate."""
        if self.hours_per_video <= 0:
            return float("inf")
        return max(0.0, (self.watch_hours_target - self.watch_hours) / self.hours_per_video)

    @property
    def views_needed_monthly(self) -> dict[str, float]:
        """Views per month the target implies, at each RPM assumption."""
        return {
            key: self.target_gross_jpy / rpm * 1000 if rpm > 0 else float("inf")
            for key, rpm in self.rpm_jpy.items()
        }

    @property
    def mean_retention(self) -> float | None:
        samples = [r for row in self.arms for r in row.retention]
        return statistics.fmean(samples) if samples else None

    @property
    def has_enough_data(self) -> bool:
        return bool(self.arms) and all(row.measured >= self.min_samples for row in self.arms)


def collect() -> Report:
    cfg = config.load_settings().get("report", {})
    rows: dict[str, ArmRow] = {
        arm.key: ArmRow(arm=arm) for arm in config.arms(include_disabled=True)
    }

    with db.session() as conn:
        # Watch hours are the gate, so they are computed from what actually
        # drives them: how long the video is, how much of it people watched, and
        # how many watched. Views alone cannot answer it.
        for record in conn.execute(
            """
            SELECT i.series_id, i.lang, s.views, s.avg_view_pct, r.duration_s
            FROM stats s
            JOIN posts   p ON p.id = s.post_id
            JOIN ideas   i ON i.id = p.idea_id
            LEFT JOIN renders r ON r.idea_id = i.id
            WHERE s.window = ?
            """,
            (SCORING_WINDOW,),
        ):
            key = Arm(series_id=record["series_id"], lang=record["lang"]).key
            row = rows.setdefault(key, ArmRow(arm=Arm.parse(key)))
            views = int(record["views"])
            row.views.append(views)
            if record["avg_view_pct"] is not None:
                row.retention.append(float(record["avg_view_pct"]))
            duration = float(record["duration_s"] or 0.0)
            retention = float(record["avg_view_pct"] or 0.0)
            row.watch_hours += views * duration * retention / 3600

        for (series_id, lang), spent in db.series_costs(conn).items():
            rows.setdefault(
                Arm(series_id=series_id, lang=lang).key,
                ArmRow(arm=Arm(series_id=series_id, lang=lang)),
            ).cost_usd = spent

        for record in conn.execute(
            "SELECT series_id, lang, status, COUNT(*) AS n FROM ideas GROUP BY series_id, lang, status"
        ):
            key = Arm(series_id=record["series_id"], lang=record["lang"]).key
            row = rows.setdefault(key, ArmRow(arm=Arm.parse(key)))
            count = int(record["n"])
            row.ideas += count
            if record["status"] == "live":
                row.live += count
            elif record["status"] == "rejected":
                row.rejected += count
            elif record["status"] == "failed":
                row.failed += count

        adhoc = db.adhoc_spend(conn)
        llm = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS t FROM llm_calls").fetchone()["t"]
        live_posts = db.measurable_posts(conn)
        private_waiting = len(db.private_posts(conn))
        pending_review = len(db.pending_reviews(conn))

    shares = scoring.allocation(window=SCORING_WINDOW)
    for key, row in rows.items():
        row.share = shares.get(key, 0.0)

    days = 0.0
    if live_posts:
        earliest = min(post["went_public_at"] for post in live_posts)
        days = max(
            1.0, (datetime.now(UTC) - datetime.fromisoformat(earliest)).total_seconds() / 86400
        )

    assumed = cfg.get("assumed_rpm_jpy", {})
    return Report(
        arms=sorted(rows.values(), key=lambda r: (-r.mean_views, r.arm.key)),
        jpy_per_usd=float(cfg.get("jpy_per_usd", 150)),
        target_gross_jpy=float(cfg.get("target_gross_jpy", 35000)),
        watch_hours=sum(row.watch_hours for row in rows.values()),
        watch_hours_target=float(cfg.get("monetization_watch_hours", 4000)),
        videos_live=len(live_posts),
        videos_to_gate=int(cfg.get("videos_to_gate", 85)),
        days_since_first=days,
        cost_usd=sum(row.cost_usd for row in rows.values()) + float(adhoc) + float(llm),
        llm_cost_usd=float(llm),
        revenue_jpy=None,  # YouTube Analytics revenue needs the monetary scope
        assumed_rpm={
            "low": float(assumed.get("low", 150)),
            "mid": float(assumed.get("mid", 300)),
            "high": float(assumed.get("high", 450)),
        },
        measured_rpm_jpy=None,
        total_views=sum(row.total_views for row in rows.values()),
        measured_videos=sum(row.measured for row in rows.values()),
        retention_floor=float(cfg.get("retention_floor", 0.30)),
        min_samples=int(cfg.get("min_samples_before_weighting", 8)),
        private_waiting=private_waiting,
        pending_review=pending_review,
    )


def _verdict(report: Report) -> list[str]:
    """What to do next, or an honest refusal to say."""
    if report.pending_review:
        return [f"レビュー待ちが {report.pending_review} 件あります。"
                "`streamlit run review_app.py` で承認してください。"]

    if report.private_waiting and not report.measured_videos:
        return [
            f"判定不能: {report.private_waiting} 本が private のままです。",
            "`tube-auto go-live` で公開してください。private の動画は再生されないので、",
            "このままではいつまでも計測値が集まりません。",
        ]

    if not report.measured_videos:
        return [
            "判定不能: まだ計測データがありません。",
            "`tube-auto publish` → `go-live` → `sync-stats` の順に実行してください。",
        ]

    lines: list[str] = []

    # Retention is the earliest honest signal. It shows up long before the gate
    # and says whether the format works at all.
    retention = report.mean_retention
    if retention is not None:
        if retention < report.retention_floor:
            lines.append(
                f"視聴維持率が {retention:.0%} と目安の {report.retention_floor:.0%} を下回っています。"
                "冒頭5秒・章構成・尺のどれかに問題があります。ここを直さない限り本数を増やしても伸びません。"
            )
        else:
            lines.append(f"視聴維持率は {retention:.0%}。目安（{report.retention_floor:.0%}）を超えています。")

    if not report.has_enough_data:
        short = [row for row in report.arms if row.measured < report.min_samples]
        worst = ", ".join(f"{r.arm.key} ({r.measured}/{report.min_samples})" for r in short[:4])
        lines.append(
            f"腕ごとの比較はまだできません。各腕 {report.min_samples} 本の計測が必要です（不足: {worst}）。"
        )
        return lines

    # The gate, not the money, is what decides the first few months.
    progress = report.gate_progress
    if progress >= 1.0:
        lines.append("収益化ラインの4,000時間を超えています。申請できます。")
    else:
        remaining = report.videos_still_needed
        lines.append(
            f"収益化ラインまで {report.watch_hours:,.0f} / {report.watch_hours_target:,.0f} 時間"
            f"（{progress:.0%}）。この調子ならあと約 {remaining:.0f} 本必要です。"
        )
        if report.days_since_first >= 90 and progress < 0.5:
            lines.append(
                "3ヶ月で半分に届いていません。撤退基準に該当します。"
                "本数を増やすか、中止を検討してください。"
            )

    best = report.arms[0] if report.arms else None
    worst_arm = next((r for r in reversed(report.arms) if r.measured), None)
    if best and worst_arm and worst_arm is not best and worst_arm.mean_views > 0:
        ratio = best.mean_views / worst_arm.mean_views
        if ratio >= 3:
            lines.append(
                f"腕の差が明確です（{best.arm.key} は {worst_arm.arm.key} の {ratio:.1f}倍）。"
                f"{worst_arm.arm.key} を止めて配分を寄せる価値があります。"
            )
    return lines


def render_report() -> str:
    report = collect()
    yen = report.jpy_per_usd
    out: list[str] = []

    out.append("=" * 96)
    out.append(f"腕別実績（計測窓: 公開後 {SCORING_WINDOW}）")
    out.append("=" * 96)
    out.append(
        f"{'arm':<24}{'計測':>5}{'公開':>5}{'却下':>5}"
        f"{'平均再生':>10}{'中央値':>8}{'最大':>9}{'維持率':>8}"
        f"{'視聴時間':>10}{'コスト':>9}{'次回配分':>9}"
    )
    for row in report.arms:
        retention = row.mean_retention
        out.append(
            f"{row.arm.key:<24}{row.measured:>5}{row.live:>5}{row.rejected:>5}"
            f"{row.mean_views:>10,.0f}{row.median_views:>8,.0f}{row.best_views:>9,}"
            f"{(format(retention, '.0%') if retention is not None else '—'):>8}"
            f"{row.watch_hours:>9,.0f}h"
            f"{'¥' + format(row.cost_usd * yen, ',.0f'):>9}"
            f"{format(row.share, '.0%'):>9}"
        )

    out.append("")
    out.append("-" * 96)
    out.append("収益化ゲート（直近12ヶ月で総再生時間4,000時間）")
    out.append("-" * 96)
    out.append(f"  公開済み            {report.videos_live} 本（目安 {report.videos_to_gate} 本）")
    out.append(f"  総再生時間          {report.watch_hours:,.0f} / {report.watch_hours_target:,.0f} 時間"
               f"  （{report.gate_progress:.1%}）")
    if report.videos_live:
        out.append(f"  1本あたり           {report.hours_per_video:,.1f} 時間")
        remaining = report.videos_still_needed
        out.append(
            "  残り必要本数        "
            + (f"約 {remaining:,.0f} 本" if remaining != float("inf") else "—")
        )
    out.append(f"  公開からの日数      {report.days_since_first:.0f} 日")
    if report.private_waiting:
        out.append(f"  ⚠ private のまま    {report.private_waiting} 本（再生されません）")

    out.append("")
    out.append("-" * 96)
    out.append("採算（却下・失敗した制作のコストも含む）")
    out.append("-" * 96)
    out.append(f"  累積コスト          ¥{report.cost_jpy:,.0f}  (${report.cost_usd:,.2f})")
    out.append(f"    うちLLM           ¥{report.llm_cost_usd * yen:,.0f}")
    out.append(f"  累積再生数          {report.total_views:,}（計測済み {report.measured_videos} 本）")

    source = "実測" if report.measured_rpm_jpy is not None else "想定"
    rpm = report.rpm_jpy
    out.append(f"  RPM（{source}）        ¥{rpm['low']:,.0f} 〜 ¥{rpm['high']:,.0f} / 1,000再生")
    if report.measured_rpm_jpy is None:
        out.append("                      ← 収益が出たら実測値に置き換わります")

    needed = report.views_needed_monthly
    out.append(f"  目標粗収益          ¥{report.target_gross_jpy:,.0f}/月")
    out.append(
        f"  必要な月間再生数    {needed['high']:,.0f} 〜 {needed['low']:,.0f} 回"
        f"（RPMが高いほど少なくて済む）"
    )

    out.append("")
    out.append("-" * 96)
    out.append("判定")
    out.append("-" * 96)
    for line in _verdict(report):
        out.append(f"  {line}")
    out.append("")
    return "\n".join(out)
