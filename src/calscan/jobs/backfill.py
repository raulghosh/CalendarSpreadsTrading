"""One-time history backfill: CBOE VIX-family + SPY daily bars. See build plan Phase 1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from sqlalchemy.orm import Session

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


def backfill_vix_family(session: Session, years: int = BACKFILL_YEARS) -> int:
    cutoff = date.today() - timedelta(days=365 * years)
    by_date: dict[date, dict[str, object]] = {}
    for symbol in VIX_SYMBOLS:
        column = VIX_COLUMN[symbol]
        for day, close in parse_daily_closes(fetch_csv(symbol)):
            if day < cutoff:
                continue
            by_date.setdefault(day, {"date": day})[column] = close
    rows = list(by_date.values())
    repo.upsert_vix_family_daily(session, rows)
    return len(rows)


def backfill_spy_daily(
    session: Session, client: StockHistoricalDataClient, years: int = BACKFILL_YEARS
) -> int:
    start = datetime.now(UTC) - timedelta(days=365 * years)
    request = StockBarsRequest(symbol_or_symbols="SPY", timeframe=TimeFrame.Day, start=start)
    bars = client.get_stock_bars(request)["SPY"]
    rows = [
        {
            "date": bar.timestamp.date(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": int(bar.volume),
        }
        for bar in bars
    ]
    repo.upsert_spy_daily(session, rows)
    return len(rows)


def run(session: Session, settings: Settings) -> None:
    n_vix = backfill_vix_family(session)
    alpaca_client = StockHistoricalDataClient(
        api_key=settings.secrets.alpaca_api_key, secret_key=settings.secrets.alpaca_secret_key
    )
    n_spy = backfill_spy_daily(session, alpaca_client)
    print(f"Backfilled {n_vix} VIX-family rows and {n_spy} SPY daily bars.")
