"""Glue between the DB, adapters and domain, shared by the Streamlit pages and the snapshot job
(no Streamlit imports here). Domain functions stay pure/testable; this module is what's allowed
to touch the DB and the clock.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.models.snapshots import OptionsSnapshot
from sqlalchemy.orm import Session

from calscan.adapters import alpaca as alpaca_adapter
from calscan.adapters import schwab as schwab_adapter
from calscan.domain import realized, regime, termstructure
from calscan.domain.models import Chain
from calscan.domain.scanner import Candidate, build_candidates, rank_candidates, to_row
from calscan.domain.scenario import reprice_chain
from calscan.domain.sigma import sigma_pts
from calscan.events import EventsCalendar, load_events
from calscan.settings import AppConfig
from calscan.store import repo

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
FIXTURE_AS_OF = date(2026, 9, 9)  # front expiry 2026-10-09 minus the fixtures' 30 DTE
ET = ZoneInfo("America/New_York")


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
    """VIX-family + SPY-derived regime, computed from the DB alone (no chain fetch)."""
    # Complete rows only: the newest date is often partial (CBOE's per-index files land at
    # different times), and one gap inside slope_z's 252-day window would make it NaN.
    vix_df = repo.get_vix_family_daily(session).dropna(subset=["vix", "vix9d", "vix3m"])
    spy_df = repo.get_spy_daily(session)
    if vix_df.empty:
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
        breakeven_daily_sigma=float("nan"),  # not used by classify(); needs a product ATM IV
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
    """Bundled tests/fixtures/ chain JSON — demo mode, never stored. The Schwab fixtures bake
    in DTEs of 30/65, i.e. they describe the market as of FIXTURE_AS_OF; SPY's DTE is derived
    from its expiry dates, so it's pinned to the same day (otherwise it ages out of the scan)."""
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
            snapshots, product="SPY", underlying_price=raw["underlying_price"], as_of=FIXTURE_AS_OF
        )
    return None


def build_ranked_candidates(
    chain: Chain,
    product: str,
    cfg: AppConfig,
    snapshot: MarketSnapshot | None,
    events: EventsCalendar | None = None,
    as_of: date | None = None,
) -> list[Candidate]:
    """Candidate build + rank shared by the snapshot job and demo mode."""
    pcfg = cfg.products[product]
    playbook = snapshot.regime.playbook if snapshot else "NONE"  # unknown regime: no favour
    events = events if events is not None else load_events()
    as_of = as_of or date.today()
    horizon_end = as_of + timedelta(days=365)
    quad_witching = {e.date for e in events.in_window(as_of, horizon_end, ["quad_witching"])}
    ex_div = {e.date for e in events.in_window(as_of, horizon_end, ["spy_ex_dividend"])}
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
        stress_alpha=max(cfg.vol_shock_alpha.stress_cases),
        rv10=snapshot.rv10 if snapshot else float("nan"),
        nav=cfg.nav,
        slope_z_favourable=playbook == "A",
        quad_witching_dates=quad_witching,
        ex_div_dates=ex_div if pcfg.style == "american" else (),
        as_of=as_of,
    )
    return rank_candidates(candidates, playbook, snapshot.slope_z if snapshot else 0.0)


def candidate_rows(
    chain: Chain,
    product: str,
    cfg: AppConfig,
    snapshot: MarketSnapshot | None,
    events: EventsCalendar | None = None,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Ranked candidates as `candidates`-table rows — stored by the job, shown by demo mode."""
    playbook = snapshot.regime.playbook if snapshot else "NONE"
    multiplier = cfg.products[product].multiplier
    return [
        to_row(c, rank=i, playbook=playbook, spot=chain.underlying_price, multiplier=multiplier)
        for i, c in enumerate(
            build_ranked_candidates(chain, product, cfg, snapshot, events, as_of), start=1
        )
    ]


@dataclass(frozen=True, slots=True)
class ProductView:
    spot: float | None
    rows: list[dict[str, Any]]
    source: str  # shown to the user: which snapshot (or demo) these rows come from
    live: bool  # demo rows are fixtures — never opened as real positions


def product_view(
    session: Session,
    cfg: AppConfig,
    product: str,
    demo: bool,
    snapshot: MarketSnapshot | None,
) -> ProductView:
    """A product's ranked candidates: the latest stored snapshot, or the bundled fixtures."""
    if demo:
        chain = load_fixture_chain(product)
        if chain is None:
            return ProductView(None, [], "demo fixture missing", live=False)
        # IV and greeks from the quote mids, exactly as the snapshot job does
        chain = reprice_chain(chain, cfg.rates.risk_free, cfg.rates.dividend_yield)
        rows = candidate_rows(chain, product, cfg, snapshot, as_of=FIXTURE_AS_OF)
        return ProductView(
            chain.underlying_price, rows, f"demo fixtures (market as of {FIXTURE_AS_OF})", False
        )
    ts, rows = repo.get_candidates(session, product)
    if ts is None:
        return ProductView(None, [], "no snapshot stored yet", live=True)
    term = repo.get_term_structure_at(session, ts, product)
    spot = term["spot"] if term else None
    local = ts.replace(tzinfo=UTC).astimezone(ET)
    return ProductView(spot, rows, f"snapshot {local:%Y-%m-%d %H:%M} ET", live=True)


def open_position_from_row(
    session: Session,
    row: dict[str, Any],
    spot: float,
    playbook: str,
    contracts: int,
    thesis: str,
) -> int:
    """Records a stored candidate as opened — no order is sent (build plan §6, Scanner page).
    σ_entry is frozen here from the snapshot's spot and front IV."""
    fields: dict[str, Any] = {
        "product": row["product"],
        "side": row["side"],
        "strike": row["strike"],
        "front_expiry": row["front_expiry"],
        "back_expiry": row["back_expiry"],
        "contracts": contracts,
        "debit_paid": row["debit_mid"],
        "iv1_entry": row["iv1"],
        "iv2_entry": row["iv2"],
        "sigma_entry_pts": sigma_pts(spot, row["iv1"], row["dte1"]),
        "playbook": playbook,
        "thesis": thesis or None,
    }
    position_id = repo.insert_position(session, fields)
    payload = {
        **fields,
        "front_expiry": row["front_expiry"].isoformat(),
        "back_expiry": row["back_expiry"].isoformat(),
        "spot_at_entry": spot,
    }
    repo.insert_journal_entry(
        session, ts=datetime.utcnow(), event_type="opened", position_id=position_id, payload=payload
    )
    return position_id
