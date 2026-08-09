"""The expand-or-stop report.

Policy C exists to buy one thing: a number that says whether this is worth
continuing. So the honesty of this file matters more than the elegance of any
other.

Four choices, each replacing something that misled:

- **One window throughout.** The table and the allocation column now come from
  the same measurement window, so the numbers in a row can be reconciled with
  each other.
- **The pace figure is a rate.** It is built from per-video views at a fixed age
  times the observed publishing rate — not from dividing a growing stock of
  lifetime views by days elapsed, which is not a rate at all.
- **Cost includes what was thrown away.** Rejected and failed generations cost
  the same as published ones, so a genre with a poor hit rate is charged for it.
- **No verdict without data.** A confident recommendation off three videos is
  worse than no recommendation.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import config, db, scoring
from .models import Arm

SCORING_WINDOW = "72h"
# Views at this age are what the pace projection is built from.
WINDOW_AGE_DAYS = 3.0


@dataclass(slots=True)
class ArmRow:
    arm: Arm
    views: list[int] = field(default_factory=list)
    cost_usd: float = 0.0
    ideas: int = 0
    published: int = 0
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
    def top2_mean(self) -> float:
        """Mean of the two best videos — a readable stand-in for the tail.

        A true p90 needs far more samples than this project will produce, and
        printing max() under a p90 heading was misleading.
        """
        if not self.views:
            return 0.0
        top = sorted(self.views, reverse=True)[:2]
        return statistics.fmean(top)

    @property
    def cost_per_1k_views(self) -> float | None:
        if self.total_views <= 0:
            return None
        return self.cost_usd / (self.total_views / 1000)


@dataclass(slots=True)
class Report:
    arms: list[ArmRow]
    window: str
    total_cost_usd: float
    adhoc_cost_usd: float
    llm_cost_usd: float
    total_views: int
    measured_videos: int
    publish_rate_per_day: float
    days_live: float
    monetization_target: int
    rpm_low: float
    rpm_high: float
    min_samples: int
    private_waiting: int

    @property
    def revenue_low(self) -> float:
        return self.total_views / 1000 * self.rpm_low

    @property
    def revenue_high(self) -> float:
        return self.total_views / 1000 * self.rpm_high

    @property
    def breakeven_rpm(self) -> float | None:
        """USD per 1,000 views needed just to cover what has been spent."""
        if self.total_views <= 0:
            return None
        return self.total_cost_usd / (self.total_views / 1000)

    @property
    def projected_90d_views(self) -> float:
        """A rate times a horizon, not a stock divided by elapsed time.

        mean views per video at a fixed age x videos published per day x 90 days.
        Deliberately ignores the long tail after that age, so it reads low rather
        than flattering.
        """
        if not self.measured_videos:
            return 0.0
        mean_per_video = self.total_views / self.measured_videos
        return mean_per_video * self.publish_rate_per_day * 90

    @property
    def monetization_pace(self) -> float:
        if self.monetization_target <= 0:
            return 0.0
        return self.projected_90d_views / self.monetization_target

    @property
    def has_enough_data(self) -> bool:
        """Every arm sampled enough, and enough elapsed time to see a tail."""
        return bool(self.arms) and all(row.measured >= self.min_samples for row in self.arms)


def collect() -> Report:
    cfg = config.load_settings().get("report", {})
    rows: dict[str, ArmRow] = {
        arm.key: ArmRow(arm=arm) for arm in config.arms(include_disabled=True)
    }

    with db.session() as conn:
        for stat in db.arm_stats(conn, window=SCORING_WINDOW):
            key = Arm(series_id=stat["series_id"], lang=stat["lang"]).key
            row = rows.setdefault(key, ArmRow(arm=Arm.parse(key)))
            row.views.append(int(stat["views"]))

        for (series_id, lang), spent in db.series_costs(conn).items():
            key = Arm(series_id=series_id, lang=lang).key
            rows.setdefault(key, ArmRow(arm=Arm.parse(key))).cost_usd = spent

        for record in conn.execute(
            "SELECT series_id, lang, status, COUNT(*) AS n FROM ideas GROUP BY series_id, lang, status"
        ):
            key = Arm(series_id=record["series_id"], lang=record["lang"]).key
            row = rows.setdefault(key, ArmRow(arm=Arm.parse(key)))
            row.ideas += int(record["n"])
            if record["status"] in ("published", "live"):
                row.published += int(record["n"])
            elif record["status"] == "rejected":
                row.rejected += int(record["n"])
            elif record["status"] == "failed":
                row.failed += int(record["n"])

        adhoc = db.adhoc_spend(conn)
        llm = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS t FROM llm_calls").fetchone()["t"]
        live = db.measurable_posts(conn)
        private_waiting = len(db.private_posts(conn))

    shares = scoring.allocation(window=SCORING_WINDOW)
    for key, row in rows.items():
        row.share = shares.get(key, 0.0)

    days_live = 0.0
    if live:
        earliest = min(post["went_public_at"] for post in live)
        days_live = max(
            1.0, (datetime.now(UTC) - datetime.fromisoformat(earliest)).total_seconds() / 86400
        )
    publish_rate = len(live) / days_live if days_live else 0.0

    total_views = sum(row.total_views for row in rows.values())
    measured = sum(row.measured for row in rows.values())

    return Report(
        arms=sorted(rows.values(), key=lambda r: (-r.mean_views, r.arm.key)),
        window=SCORING_WINDOW,
        total_cost_usd=sum(row.cost_usd for row in rows.values()) + float(adhoc) + float(llm),
        adhoc_cost_usd=float(adhoc),
        llm_cost_usd=float(llm),
        total_views=total_views,
        measured_videos=measured,
        publish_rate_per_day=publish_rate,
        days_live=days_live,
        monetization_target=int(cfg.get("monetization_views_90d", 10_000_000)),
        rpm_low=float(cfg.get("rpm_low", 0.03)),
        rpm_high=float(cfg.get("rpm_high", 0.10)),
        min_samples=int(cfg.get("min_samples_before_weighting", 10)),
        private_waiting=private_waiting,
    )


def _verdict(report: Report) -> list[str]:
    if report.private_waiting and not report.measured_videos:
        return [
            f"判定不能: {report.private_waiting} 本が private のままです。",
            "`tube-auto go-live` で公開してください。private の動画は再生されないので、",
            "このままではいつまでも計測値が集まりません。",
        ]

    if not report.measured_videos:
        return [
            "判定不能: まだ計測データがありません。",
            "`tube-auto publish` → `tube-auto go-live` → `tube-auto sync-stats` の順に実行してください。",
        ]

    if not report.has_enough_data:
        short = [row for row in report.arms if row.measured < report.min_samples]
        worst = ", ".join(f"{r.arm.key} ({r.measured}/{report.min_samples})" for r in short[:4])
        return [
            f"データ不足: 判定には各腕 {report.min_samples} 本の計測が必要です。",
            f"不足している腕: {worst}",
            "この段階で結論を出すと、実力ではなく偶然を読むことになります。",
        ]

    lines: list[str] = []
    breakeven = report.breakeven_rpm or 0.0
    if breakeven <= report.rpm_low:
        lines.append(
            f"生成コストは回収圏内です（損益分岐 RPM ${breakeven:.3f} ≤ 想定下限 ${report.rpm_low:.2f}）。"
        )
    elif breakeven <= report.rpm_high:
        lines.append(
            f"損益分岐 RPM ${breakeven:.3f} は想定レンジ内ですが下限を超えています。"
            "1本あたりコストを下げるか、伸びている腕に配分を寄せる判断が要ります。"
        )
    else:
        lines.append(
            f"損益分岐 RPM ${breakeven:.3f} が想定上限 ${report.rpm_high:.2f} を超えています。"
            "現状の再生数では広告収益で生成コストを回収できません。"
        )

    pace = report.monetization_pace
    if pace >= 1.0:
        lines.append(f"収益化ラインに対する進捗は {pace:.0%}。到達ペースです。")
    elif pace >= 0.3:
        lines.append(
            f"収益化ライン（90日 {report.monetization_target:,} 再生）に対して {pace:.0%} のペース。"
            "伸びている腕に寄せれば射程内です。"
        )
    else:
        lines.append(
            f"収益化ラインに対して {pace:.0%} のペース。このままでは収益ゼロのまま推移します。"
            "中止か方針転換の検討時期です。"
        )

    best = report.arms[0] if report.arms else None
    worst_arm = report.arms[-1] if len(report.arms) > 1 else None
    if best and worst_arm and worst_arm.mean_views > 0:
        ratio = best.mean_views / worst_arm.mean_views
        if ratio >= 3:
            lines.append(
                f"腕の差は明確です（{best.arm.key} は {worst_arm.arm.key} の {ratio:.1f}倍）。"
                f"{worst_arm.arm.key} を停止して配分を寄せる価値があります。"
            )
    return lines


def render_report() -> str:
    report = collect()
    out: list[str] = []

    out.append("=" * 92)
    out.append(f"腕別実績（計測窓: 公開後 {report.window}／再生数は long-tail なので中央値と最大を併記）")
    out.append("=" * 92)
    out.append(
        f"{'arm':<26}{'計測':>5}{'企画':>5}{'却下':>5}"
        f"{'平均':>9}{'中央値':>8}{'最大':>9}{'上位2平均':>10}"
        f"{'コスト':>9}{'$/1k再生':>10}{'次回配分':>9}"
    )
    for row in report.arms:
        cost_per_1k = row.cost_per_1k_views
        out.append(
            f"{row.arm.key:<26}{row.measured:>5}{row.ideas:>5}{row.rejected:>5}"
            f"{row.mean_views:>9,.0f}{row.median_views:>8,.0f}{row.best_views:>9,}"
            f"{row.top2_mean:>10,.0f}"
            f"{'$' + format(row.cost_usd, '.2f'):>9}"
            f"{('$' + format(cost_per_1k, '.3f')) if cost_per_1k is not None else '—':>10}"
            f"{format(row.share, '.0%'):>9}"
        )

    out.append("")
    out.append("-" * 92)
    out.append("採算（却下・失敗した生成のコストも含む）")
    out.append("-" * 92)
    out.append(f"  累積コスト          ${report.total_cost_usd:,.2f}")
    out.append(f"    うち企画LLM       ${report.llm_cost_usd:,.2f}")
    out.append(f"    うち単発テスト    ${report.adhoc_cost_usd:,.2f}")
    out.append(f"  累積再生数          {report.total_views:,}（計測済み {report.measured_videos} 本）")
    breakeven = report.breakeven_rpm
    out.append(
        "  損益分岐 RPM        "
        + (f"${breakeven:.3f} / 1,000再生" if breakeven is not None else "—")
    )
    out.append(
        f"  想定広告収益        ${report.revenue_low:,.2f} 〜 ${report.revenue_high:,.2f} "
        f"(RPM ${report.rpm_low:.2f}〜${report.rpm_high:.2f})"
    )
    out.append(
        f"  想定損益            ${report.revenue_low - report.total_cost_usd:,.2f} 〜 "
        f"${report.revenue_high - report.total_cost_usd:,.2f}"
    )
    out.append("  ※ 収益化ライン未達の間は実収益は $0 です")

    out.append("")
    out.append("-" * 92)
    out.append("収益化ラインへのペース")
    out.append("-" * 92)
    out.append(f"  公開からの日数      {report.days_live:.1f} 日")
    out.append(f"  実測の投稿ペース    {report.publish_rate_per_day:.2f} 本/日")
    out.append(
        f"  90日換算の再生見込み {report.projected_90d_views:,.0f}"
    )
    out.append(
        f"                      （公開後{report.window}時点の1本平均 × 実測ペース × 90日。"
        "それ以降の伸びは含めていないので控えめな見積もりです）"
    )
    out.append(f"  収益化ライン        {report.monetization_target:,} 再生 / 90日")
    out.append(f"  進捗                {report.monetization_pace:.1%}")
    if report.private_waiting:
        out.append(f"  ⚠ private のまま    {report.private_waiting} 本（再生されません）")

    out.append("")
    out.append("-" * 92)
    out.append("判定")
    out.append("-" * 92)
    for line in _verdict(report):
        out.append(f"  {line}")
    out.append("")
    return "\n".join(out)
