"""Shared DB-backed snapshot loading for Streamlit pages. Thin glue: repo + domain, no logic
of its own beyond wiring. Domain functions stay pure/testable; this module is what's allowed
to touch the DB and the clock.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from alpaca.data.models.snapshots import OptionsSnapshot
from sqlalchemy.orm import Session

from calscan.adapters import alpaca as alpaca_adapter
from calscan.adapters import schwab as schwab_adapter
from calscan.domain import realized, regime, termstructure
from calscan.domain.models import Chain
from calscan.domain.scanner import Candidate, build_candidates, rank_candidates
from calscan.events import EventsCalendar, load_events
from calscan.settings import AppConfig, Settings
from calscan.store import repo

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


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


def load_fixture_chain(product: str) -> Chain | None:
    """Bundled tests/fixtures/ chain JSON — lets the Scanner/Scenario Grid pages work with no
    live credentials configured (also exercised by the domain test suite)."""
    if product in ("SPX", "XSP"):
        path = FIXTURES_DIR / f"schwab_{product.lower()}_chain.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        return schwab_adapter.parse_chain(raw, product=product)
    if product == "SPY":
        path = FIXTURES_DIR / "alpaca_spy_chain.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        snapshots = {s: OptionsSnapshot(s, d) for s, d in raw["snapshots"].items()}
        return alpaca_adapter.parse_chain(
            snapshots, product="SPY", underlying_price=raw["underlying_price"], as_of=date.today()
        )
    return None


def load_live_chain(settings: Settings, cfg: AppConfig, product: str) -> Chain:
    """Raises on adapter/auth failure — callers decide how to surface that in the UI."""
    pcfg = cfg.products[product]
    if pcfg.source == "schwab":
        client = schwab_adapter.get_client(settings.secrets)
        return schwab_adapter.get_chain(client, product, pcfg.symbol)
    stock_client = alpaca_adapter.get_client(settings.secrets)
    quote = alpaca_adapter.get_quote(stock_client, pcfg.symbol)
    option_client = alpaca_adapter.get_option_client(settings.secrets)
    return alpaca_adapter.get_chain(option_client, pcfg.symbol, product, quote.price)


def build_ranked_candidates(
    chain: Chain, product: str, cfg: AppConfig, snapshot: MarketSnapshot | None
) -> list[Candidate]:
    """Same candidate build + rank used by both the Scanner and Scenario Grid pages."""
    pcfg = cfg.products[product]
    playbook = snapshot.regime.playbook if snapshot else "A"
    candidates = build_candidates(
        product=product,
        contracts=chain.contracts,
        expiries=chain.expiries,
        spot=chain.underlying_price,
        multiplier=pcfg.multiplier,
        style=pcfg.style,
        r=cfg.rates.risk_free,
        q=cfg.rates.dividend_yield,
        expiry_cfg=cfg.expiry_selection,
        strikes_cfg=cfg.strikes,
        gates_cfg=cfg.gates,
        wvega_alphas=[cfg.vol_shock_alpha.baseline, *cfg.vol_shock_alpha.stress_cases],
        stress_alpha=max(cfg.vol_shock_alpha.stress_cases),
        rv10=snapshot.rv10 if snapshot else float("nan"),
        nav=cfg.nav,
        slope_z_favourable=playbook == "A",
    )
    return rank_candidates(candidates, playbook, snapshot.slope_z if snapshot else 0.0)
