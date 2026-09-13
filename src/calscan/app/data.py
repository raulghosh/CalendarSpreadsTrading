"""Shared DB-backed snapshot loading for Streamlit pages. Thin glue: repo + domain, no logic
of its own beyond wiring. Domain functions stay pure/testable; this module is what's allowed
to touch the DB and the clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from calscan.domain import realized, regime, termstructure
from calscan.events import EventsCalendar, load_events
from calscan.settings import AppConfig
from calscan.store import repo


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    as_of: date
    vix: float
    vix9d: float
    vix3m: float
    vix6m: float
    ivts: float
    near_slope: float
    slope_z: float
    slope_z_top: float
    rv10: float
    rv20: float
    regime: regime.RegimeResult
    vix_family_df: pd.DataFrame
    spy_df: pd.DataFrame


def load_market_snapshot(
    session: Session, cfg: AppConfig, events: EventsCalendar | None = None
) -> MarketSnapshot | None:
    """VIX-family + SPY-derived regime. Product-specific breakeven awaits Phase 2 chain data."""
    vix_df = repo.get_vix_family_daily(session)
    spy_df = repo.get_spy_daily(session)
    if vix_df.empty or vix_df[["vix", "vix9d", "vix3m"]].iloc[-1].isna().any():
        return None

    latest = vix_df.iloc[-1]
    as_of = vix_df.index[-1].date()

    ivts_val = termstructure.ivts(latest["vix"], latest["vix3m"])
    near_slope_val = termstructure.near_slope(latest["vix"], latest["vix9d"])

    slope_z_ser = termstructure.slope_z_series(
        vix_df["vix"], vix_df["vix3m"], window=cfg.regime.slope_z_window_days
    )
    slope_z_val = float(slope_z_ser.iloc[-1])
    slope_z_valid = slope_z_ser.dropna()
    slope_z_top = (
        termstructure.slope_z_top_threshold(slope_z_valid, cfg.regime.slope_z_avoid_top_pct)
        if len(slope_z_valid) >= 2
        else float("nan")
    )

    rv_short = cfg.regime.rv_window_short
    rv_long = cfg.regime.rv_window_long
    rv10 = (
        realized.realized_vol(spy_df["close"], rv_short) if len(spy_df) > rv_short else float("nan")
    )
    rv20 = (
        realized.realized_vol(spy_df["close"], rv_long) if len(spy_df) > rv_long else float("nan")
    )

    lookback = cfg.regime.vix_lookback_median_days
    vix_median_60d = float(vix_df["vix"].iloc[-lookback:].median())

    events = events if events is not None else load_events()
    window_start = as_of + timedelta(days=cfg.expiry_selection.front_dte_min)
    window_end = as_of + timedelta(days=cfg.expiry_selection.front_dte_max)
    events_in_window = bool(events.in_window(window_start, window_end))

    inputs = regime.RegimeInputs(
        ivts=ivts_val,
        slope_z=slope_z_val,
        slope_z_top=slope_z_top if not math.isnan(slope_z_top) else float("inf"),
        vix=float(latest["vix"]),
        vix_median_60d=vix_median_60d,
        rv10=rv10,
        breakeven_daily_sigma=float("nan"),  # needs product ATM IV — lands with Phase 2 chains
        events_in_window=events_in_window,
    )
    result = regime.classify(inputs, cfg.regime)

    return MarketSnapshot(
        as_of=as_of,
        vix=float(latest["vix"]),
        vix9d=float(latest["vix9d"]),
        vix3m=float(latest["vix3m"]),
        vix6m=float(latest["vix6m"]) if not pd.isna(latest["vix6m"]) else float("nan"),
        ivts=ivts_val,
        near_slope=near_slope_val,
        slope_z=slope_z_val,
        slope_z_top=slope_z_top,
        rv10=rv10,
        rv20=rv20,
        regime=result,
        vix_family_df=vix_df,
        spy_df=spy_df,
    )
