"""Hard spending ceiling.

Policy C only makes sense if the downside is capped, so the cap lives in code:
`generate` asks permission before every call and refuses once the month's
budget is gone.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from . import db
from .config import load_settings


class BudgetExceeded(RuntimeError):
    """Raised instead of spending money."""


def month_start(reference: datetime | None = None) -> str:
    ref = reference or datetime.now(UTC)
    return ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )


class BudgetGuard:
    """Tracks spend for one `generate` run against both ceilings."""

    def __init__(self, conn: sqlite3.Connection, settings: dict | None = None) -> None:
        cfg = (settings or load_settings()).get("budget", {})
        self.monthly_limit = float(cfg.get("monthly_usd", 0.0))
        self.per_run_limit = float(cfg.get("per_run_usd", 0.0))
        self.month_to_date = db.spend_since(conn, month_start())
        self.run_total = 0.0

    @property
    def monthly_remaining(self) -> float:
        return max(0.0, self.monthly_limit - self.month_to_date - self.run_total)

    @property
    def run_remaining(self) -> float:
        return max(0.0, self.per_run_limit - self.run_total)

    def check(self, cost_usd: float) -> None:
        """Raise BudgetExceeded if this charge would break either ceiling."""
        if self.month_to_date + self.run_total + cost_usd > self.monthly_limit:
            raise BudgetExceeded(
                f"monthly budget exhausted: spent ${self.month_to_date + self.run_total:.2f} "
                f"+ ${cost_usd:.2f} would exceed ${self.monthly_limit:.2f} "
                "(raise budget.monthly_usd in config/settings.yaml to continue)"
            )
        if self.run_total + cost_usd > self.per_run_limit:
            raise BudgetExceeded(
                f"per-run budget exhausted: ${self.run_total:.2f} + ${cost_usd:.2f} "
                f"would exceed ${self.per_run_limit:.2f} (budget.per_run_usd)"
            )

    def commit(self, cost_usd: float) -> None:
        """Record a charge that actually happened."""
        self.run_total += cost_usd

    def summary(self) -> str:
        return (
            f"spent this run ${self.run_total:.2f} / ${self.per_run_limit:.2f}, "
            f"month to date ${self.month_to_date + self.run_total:.2f} / ${self.monthly_limit:.2f}"
        )
