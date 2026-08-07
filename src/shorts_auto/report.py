"""The expand-or-stop report.

Policy C exists to buy one thing: a number that says whether this is worth
continuing. Views are a long-tail distribution — a handful of viral hits and a
mass of duds — so the median matters more than the mean, and p90 is what tells
you whether the tail is there at all.

Nothing here is a projection dressed up as a forecast: the monetisation pace is
a linear extrapolation of the observed daily rate and is labelled as such.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import config, db, scoring


@dataclass(slots=True)
class SeriesRow:
    series_id: str
    posts: int = 0
    views: list[int] = field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def total_views(self) -> int:
        return sum(self.views)

    @property
    def median_views(self) -> float:
        return statistics.median(self.views) if self.views else 0.0

    @property
    def p90_views(self) -> float:
        if not self.views:
            return 0.0
        if len(self.views) < 10:
            return float(max(self.views))
        return float(statistics.quantiles(self.views, n=10)[-1])

    @property
    def max_views(self) -> int:
        return max(self.views) if self.views else 0


@dataclass(slots=True)
class Report:
    series: list[SeriesRow]
    total_cost_usd: float
    total_views: int
    days_active: int
    projected_90d_views: float
    monetization_target: int
    rpm_low: float
    rpm_high: float
    allocation: dict[str, float]

    @property
    def revenue_low(self) -> float:
        return self.total_views / 1000 * self.rpm_low

    @property
    def revenue_high(self) -> float:
        return self.total_views / 1000 * self.rpm_high

    @property
    def breakeven_rpm(self) -> float | None:
        """USD per 1,000 views the channel must earn just to cover generation."""
        if self.total_views <= 0:
            return None
        return self.total_cost_usd / (self.total_views / 1000)

    @property
    def monetization_pace(self) -> float:
        if self.monetization_target <= 0:
            return 0.0
        return self.projected_90d_views / self.monetization_target


def collect(window: str = "latest", horizon_days: int = 90) -> Report:
    cfg = config.load_settings().get("report", {})
    rows: dict[str, SeriesRow] = defaultdict(lambda: SeriesRow(series_id=""))

    with db.session() as conn:
        for entry in config.load_series(include_disabled=True):
            rows[entry.id] = SeriesRow(series_id=entry.id)

        for stat in conn.execute(
            """
            SELECT i.series_id, s.views
            FROM stats s
            JOIN posts  p ON p.id = s.post_id
            JOIN assets a ON a.id = p.asset_id
            JOIN ideas  i ON i.id = a.idea_id
            WHERE s.window = ?
            """,
            (window,),
        ):
            row = rows.setdefault(stat["series_id"], SeriesRow(series_id=stat["series_id"]))
            row.posts += 1
            row.views.append(int(stat["views"]))

        for cost in conn.execute(
            """
            SELECT i.series_id, COALESCE(SUM(a.cost_usd), 0) AS spent
            FROM assets a
            JOIN ideas i ON i.id = a.idea_id
            GROUP BY i.series_id
            """
        ):
            rows.setdefault(
                cost["series_id"], SeriesRow(series_id=cost["series_id"])
            ).cost_usd = float(cost["spent"])

        adhoc = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM assets WHERE idea_id IS NULL"
        ).fetchone()["spent"]

        first = conn.execute("SELECT MIN(published_at) AS first FROM posts").fetchone()["first"]

    total_cost = sum(r.cost_usd for r in rows.values()) + float(adhoc)
    total_views = sum(r.total_views for r in rows.values())

    days_active = 1
    if first:
        delta = datetime.now(UTC) - datetime.fromisoformat(first)
        days_active = max(1, delta.days + 1)
    projected = total_views / days_active * horizon_days

    return Report(
        series=sorted(rows.values(), key=lambda r: -r.total_views),
        total_cost_usd=total_cost,
        total_views=total_views,
        days_active=days_active,
        projected_90d_views=projected,
        monetization_target=int(cfg.get("monetization_views_90d", 10_000_000)),
        rpm_low=float(cfg.get("rpm_low", 0.03)),
        rpm_high=float(cfg.get("rpm_high", 0.10)),
        allocation=scoring.allocation(),
    )


def _verdict(report: Report) -> str:
    if report.total_views == 0:
        return "判定不能: まだ計測データがありません。`shorts-auto sync-stats` を実行してください。"

    pace = report.monetization_pace
    breakeven = report.breakeven_rpm or 0.0
    lines = []

    if breakeven <= report.rpm_low:
        lines.append(
            f"生成コストは回収圏内です（損益分岐 RPM ${breakeven:.3f} ≤ 想定下限 ${report.rpm_low:.2f}）。"
        )
    elif breakeven <= report.rpm_high:
        lines.append(
            f"損益分岐 RPM ${breakeven:.3f} は想定レンジ内ですが下限を超えています。"
            "1本あたりコストを下げるか、当たりシリーズに寄せる判断が要ります。"
        )
    else:
        lines.append(
            f"損益分岐 RPM ${breakeven:.3f} が想定上限 ${report.rpm_high:.2f} を超えています。"
            "現状の再生数では広告収益で生成コストを回収できません。"
        )

    if pace >= 1.0:
        lines.append(f"収益化ラインに対する進捗は {pace:.0%}。到達ペースです。")
    elif pace >= 0.3:
        lines.append(
            f"収益化ライン（90日 {report.monetization_target:,} 再生）に対して {pace:.0%} のペース。"
            "伸びているシリーズに配分を寄せれば射程内です。"
        )
    else:
        lines.append(
            f"収益化ラインに対して {pace:.0%} のペース。"
            "このままでは収益ゼロのまま推移します。中止か方針転換の検討時期です。"
        )
    return "\n".join(f"  {line}" for line in lines)


def render_report(window: str = "latest") -> str:
    report = collect(window=window)
    out: list[str] = []

    out.append("=" * 78)
    out.append("シリーズ別実績（再生数は long-tail なので中央値と p90 を見る）")
    out.append("=" * 78)
    out.append(
        f"{'series':<20}{'本数':>5}{'合計':>10}{'中央値':>9}{'p90':>10}{'最大':>10}{'コスト':>9}{'次回配分':>9}"
    )
    for row in report.series:
        share = report.allocation.get(row.series_id)
        out.append(
            f"{row.series_id:<20}{row.posts:>5}{row.total_views:>10,}"
            f"{row.median_views:>9,.0f}{row.p90_views:>10,.0f}{row.max_views:>10,}"
            f"{'$' + format(row.cost_usd, '.2f'):>9}"
            f"{(format(share, '.0%') if share is not None else '—'):>9}"
        )

    out.append("")
    out.append("-" * 78)
    out.append("採算")
    out.append("-" * 78)
    out.append(f"  累積生成コスト      ${report.total_cost_usd:,.2f}")
    out.append(f"  累積再生数          {report.total_views:,}")
    breakeven = report.breakeven_rpm
    out.append(
        f"  損益分岐 RPM        "
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
    out.append("-" * 78)
    out.append("収益化ラインへのペース")
    out.append("-" * 78)
    out.append(f"  計測期間            {report.days_active} 日")
    out.append(
        f"  90日換算の再生見込み {report.projected_90d_views:,.0f} "
        f"（直近の実測日次ペースを線形に伸ばしただけの概算）"
    )
    out.append(f"  収益化ライン        {report.monetization_target:,} 再生 / 90日")
    out.append(f"  進捗                {report.monetization_pace:.1%}")

    out.append("")
    out.append("-" * 78)
    out.append("判定")
    out.append("-" * 78)
    out.append(_verdict(report))
    out.append("")
    return "\n".join(out)
