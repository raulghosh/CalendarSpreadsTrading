"""History backfill: CBOE VIX-family + SPY daily bars. See build plan Phase 1.

Both writes are upserts by date, so re-running (or running on every snapshot) never
duplicates rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from calscan.adapters import alpaca as alpaca_adapter
from calscan.adapters.cboe import VixSymbol, fetch_csv, parse_daily_closes
from calscan.settings import Settings
from calscan.store import repo

VIX_SYMBOLS: tuple[VixSymbol, ...] = ("VIX", "VIX9D", "VIX3M", "VIX6M")
VIX_COLUMN = {"VIX": "vix", "VIX9D": "vix9d", "VIX3M": "vix3m", "VIX6M": "vix6m"}
BACKFILL_YEARS = 3


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    cboe_symbols: tuple[VixSymbol, ...]
    spy_years: int


def plan() -> BackfillPlan:
    return BackfillPlan(cboe_symbols=VIX_SYMBOLS, spy_years=BACKFILL_YEARS)


def backfill_vix_family(
    session: Session,
    years: int = BACKFILL_YEARS,
    fetch: Callable[[VixSymbol], str] = fetch_csv,
) -> int:
    cutoff = date.today() - timedelta(days=365 * years)
    by_date: dict[date, dict[str, object]] = {}
    for symbol in VIX_SYMBOLS:
        column = VIX_COLUMN[symbol]
        for day, close in parse_daily_closes(fetch(symbol)):
            if day < cutoff:
                continue
            by_date.setdefault(day, {"date": day})[column] = close
    rows = list(by_date.values())
    repo.upsert_vix_family_daily(session, rows)
    return len(rows)


def run(session: Session, settings: Settings) -> None:
    n_vix = backfill_vix_family(session)
    secrets = settings.secrets
    client = alpaca_adapter.get_client(secrets)
    start = datetime.now(UTC) - timedelta(days=365 * BACKFILL_YEARS)
    rows = alpaca_adapter.get_daily_bars(client, "SPY", start, secrets.alpaca_data_feed)
    repo.upsert_spy_daily(session, rows)
    print(f"Backfilled {n_vix} VIX-family rows and {len(rows)} SPY daily bars.")
