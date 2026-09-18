"""Hard spending ceiling.

Policy C only makes sense if the downside is capped, so the cap lives in code.
Three things make it actually hold:

- Spend is read from append-only ledgers (`generations`, `llm_calls`), so no UI
  action can make past spend invisible.
- Every charge is committed before the next one is attempted, so a crash cannot
  roll back a record of money already gone.
- Concurrent runs are excluded by a lock file, because two processes that each
  read the month-to-date total before either writes would both see the full
  remaining budget.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import db, paths
from .config import load_settings


class BudgetExceeded(RuntimeError):
    """Raised instead of spending money."""


class AlreadyRunning(RuntimeError):
    """Another process holds the generation lock."""


def month_start(reference: datetime | None = None) -> str:
    """First instant of the current UTC month, in the same format as `db.now()`.

    Both sides are UTC ISO-8601 with an explicit +00:00 offset and second
    precision, so the string comparison the ledger query does is equivalent to a
    timestamp comparison.
    """
    ref = reference or datetime.now(UTC)
    return ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )


@contextmanager
def exclusive_run(name: str = "generate") -> Iterator[None]:
    """Refuse to start if another spending run is in progress."""
    paths.ensure_work_dirs()
    lock_path = paths.WORK_DIR / f".{name}.lock"
    try:
        handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AlreadyRunning(
            f"another `{name}` run holds {lock_path}. Wait for it to finish, or delete the "
            "file if you are certain no other run is active."
        ) from None
    try:
        os.write(handle, f"{os.getpid()} {db.now()}\n".encode())
        os.close(handle)
        yield
    finally:
        Path(lock_path).unlink(missing_ok=True)


class BudgetGuard:
    """Tracks spend for one run against the monthly and per-run ceilings."""

    def __init__(self, conn: sqlite3.Connection, settings: dict | None = None) -> None:
        cfg = (settings or load_settings()).get("budget", {})
        self.monthly_limit = float(cfg.get("monthly_usd", 0.0))
        self.per_run_limit = float(cfg.get("per_run_usd", 0.0))
        self._conn = conn
        self.run_total = 0.0

    @property
    def month_to_date(self) -> float:
        """Read from the ledger every time, so committed spend is never stale."""
        return db.spend_since(self._conn, month_start())

    @property
    def monthly_remaining(self) -> float:
        return max(0.0, self.monthly_limit - self.month_to_date)

    @property
    def run_remaining(self) -> float:
        return max(0.0, self.per_run_limit - self.run_total)

    def check(self, cost_usd: float) -> None:
        """Raise BudgetExceeded if this charge would break either ceiling."""
        spent = self.month_to_date
        if spent + cost_usd > self.monthly_limit:
            raise BudgetExceeded(
                f"monthly budget exhausted: ${spent:.2f} spent + ${cost_usd:.2f} would exceed "
                f"${self.monthly_limit:.2f} (raise budget.monthly_usd in config/settings.yaml "
                "to continue)"
            )
        if self.run_total + cost_usd > self.per_run_limit:
            raise BudgetExceeded(
                f"per-run budget exhausted: ${self.run_total:.2f} + ${cost_usd:.2f} would exceed "
                f"${self.per_run_limit:.2f} (budget.per_run_usd)"
            )

    def record(self, cost_usd: float) -> None:
        """Note a charge against this run's ceiling.

        The monthly figure needs no bookkeeping here — it is re-read from the
        committed ledger on every check.
        """
        self.run_total += cost_usd

    def summary(self) -> str:
        return (
            f"spent this run ${self.run_total:.2f} / ${self.per_run_limit:.2f}, "
            f"month to date ${self.month_to_date:.2f} / ${self.monthly_limit:.2f}"
        )
