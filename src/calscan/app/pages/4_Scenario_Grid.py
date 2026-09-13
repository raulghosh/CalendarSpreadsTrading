from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from calscan.app.data import (
    build_ranked_candidates,
    load_fixture_chain,
    load_live_chain,
    load_market_snapshot,
)
from calscan.domain.greeks import weighted_vega
from calscan.domain.scanner import Candidate
from calscan.domain.scenario import default_horizon_days, scenario_grid
from calscan.settings import Settings
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Scenario Grid — Calendar Scanner", page_icon="🌡️", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg = settings.config
session_factory = _session_factory()

st.title("Scenario Grid")
st.caption("Pick a candidate — position support lands in Phase 4.")

has_live_creds = bool(settings.secrets.schwab_app_key or settings.secrets.alpaca_api_key)
use_fixtures = st.toggle("Use bundled fixture data", value=not has_live_creds)

product = st.selectbox("Product", list(cfg.products.keys()))

with session_factory() as session:  # type: ignore[operator]
    snapshot = load_market_snapshot(session, cfg)

if use_fixtures:
    chain = load_fixture_chain(product)
else:
    try:
        chain = load_live_chain(settings, cfg, product)
    except Exception as exc:
        st.warning(f"Live fetch failed for {product}: {exc}")
        chain = None

if chain is None:
    st.warning(f"No chain data available for {product}.")
    st.stop()

ranked: list[Candidate] = build_ranked_candidates(chain, product, cfg, snapshot)
if not ranked:
    st.info("No candidates from the current expiry/strike grid.")
    st.stop()

labels = [
    f"{c.side} {c.strike:g}  {c.front.expiry}/{c.back.expiry}  "
    f"({'tradeable' if c.tradeable else 'gated'}, score {c.soft_score})"
    for c in ranked
]
choice = st.selectbox("Candidate", range(len(ranked)), format_func=lambda i: labels[i])
candidate = ranked[choice]

default_horizon = default_horizon_days(candidate.front.dte)
horizon = st.slider(
    "Horizon (days)", min_value=1, max_value=max(candidate.front.dte - 1, 1), value=default_horizon
)
alpha = st.slider(
    "α (vol-shock exponent, for the weighted-vega readout below)",
    min_value=0.0,
    max_value=1.0,
    value=cfg.vol_shock_alpha.baseline,
    step=0.05,
)

grid = scenario_grid(
    spot=chain.underlying_price,
    front_strike=candidate.front.strike,
    front_side=candidate.front.side,
    front_iv=candidate.front.iv,  # type: ignore[arg-type]
    front_dte=candidate.front.dte,
    back_strike=candidate.back.strike,
    back_side=candidate.back.side,
    back_iv=candidate.back.iv,  # type: ignore[arg-type]
    back_dte=candidate.back.dte,
    debit=candidate.debit_mid,
    r=cfg.rates.risk_free,
    q=cfg.rates.dividend_yield,
    spot_moves_sigma=cfg.scenario_grid.spot_moves_sigma,
    iv_shifts_front=cfg.scenario_grid.iv_shifts_front,
    iv_shifts_back=cfg.scenario_grid.iv_shifts_back,
    horizon_days=horizon,
)

scenario_labels = [s.label for s in {c.scenario.label: c.scenario for c in grid.cells}.values()]
moves = sorted({c.spot_move_sigma for c in grid.cells})
by_key = {(c.spot_move_sigma, c.scenario.label): c for c in grid.cells}
z = [[by_key[(m, lbl)].pnl_pct_of_debit * 100 for lbl in scenario_labels] for m in moves]

fig = go.Figure(
    data=go.Heatmap(
        z=z,
        x=scenario_labels,
        y=[str(m) for m in moves],
        colorscale="RdYlGn",
        zmid=0,
        text=[[f"{v:.1f}%" for v in row] for row in z],
        texttemplate="%{text}",
        colorbar={"title": "P&L % of debit"},
    )
)
fig.update_layout(
    xaxis_title="IV shock scenario (front/back)",
    yaxis_title="spot move (σ)",
    yaxis={"type": "category", "tickmode": "linear"},
    height=520,
)
st.plotly_chart(fig, use_container_width=True)

col1, col2, col3 = st.columns(3)
col1.metric("Debit", f"{grid.debit:,.2f}")
col2.metric(
    "Worst cell",
    f"{grid.worst_cell.pnl_pct_of_debit * 100:.1f}%",
    help=f"{grid.worst_cell.scenario.label} @ {grid.worst_cell.spot_move_sigma}σ",
)
col3.metric(
    "Inversion cell",
    f"{grid.inversion_cell.pnl_pct_of_debit * 100:.1f}%",
    help=f"{grid.inversion_cell.scenario.label} @ {grid.inversion_cell.spot_move_sigma}σ",
)

wv = weighted_vega(
    candidate.front.vega,  # type: ignore[arg-type]
    candidate.back.vega,  # type: ignore[arg-type]
    candidate.front.dte / 365,
    candidate.back.dte / 365,
    alpha,
)
st.caption(f"weighted_vega(α={alpha:.2f}) = {wv:.2f} · raw_vega = {candidate.greeks.raw_vega:.2f}")
