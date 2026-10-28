from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from calscan.app.data import load_market_snapshot, product_view
from calscan.domain.scanner import ROW_WVEGA_ALPHAS
from calscan.domain.scenario import default_horizon_days, scenario_grid
from calscan.settings import Settings
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Scenario Grid — Calendar Scanner", page_icon="🌡️", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg = settings.config
session_factory = _session_factory()

st.title("Scenario Grid")
st.caption("Reprices a scanner candidate across spot moves and paired front/back IV shocks.")

with session_factory() as session:  # type: ignore[operator]
    snapshot = load_market_snapshot(session, cfg)
    has_snapshot = repo.get_latest_snapshot_run(session) is not None

demo = st.toggle("Demo mode (bundled fixture chains)", value=not has_snapshot)
product = st.selectbox("Product", list(cfg.products.keys()))

with session_factory() as session:  # type: ignore[operator]
    view = product_view(session, cfg, product, demo, snapshot)
if not view.rows or view.spot is None:
    st.info(f"No {product} candidates — {view.source}.")
    st.stop()
spot = view.spot
st.caption(f"{view.source} · spot {spot:,.2f}")

labels = [
    f"#{r['rank']} {r['side']} {r['strike']:g}  {r['front_expiry']}/{r['back_expiry']}  "
    f"({'tradeable' if all(r['gates_passed'].values()) else 'gated'}, score {r['soft_score']:g})"
    for r in view.rows
]
choice = st.selectbox("Candidate", range(len(view.rows)), format_func=lambda i: labels[i])
row = view.rows[choice]

horizon = st.slider(
    "Horizon (days)",
    min_value=1,
    max_value=max(row["dte1"] - 1, 1),
    value=default_horizon_days(row["dte1"]),
)

grid = scenario_grid(
    spot=spot,
    front_strike=row["strike"],
    front_side=row["side"],
    front_iv=row["iv1"],
    front_dte=row["dte1"],
    back_strike=row["strike"],
    back_side=row["side"],
    back_iv=row["iv2"],
    back_dte=row["dte2"],
    debit=row["debit_mid"],
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
        text=[[f"{v:.1f}%" for v in row_values] for row_values in z],
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

st.subheader("Vol-shock weighted vega (per contract)")
st.caption(
    "weighted_vega(α) = vega_back·(T1/T2)^α − vega_front — how the position's vega exposure "
    "reads if back-month vol moves only (T1/T2)^α as much as front-month vol (§5.5)."
)
st.table(
    [
        {"α": alpha, "weighted vega": round(row[column], 2)}
        for column, alpha in ROW_WVEGA_ALPHAS.items()
    ]
    + [{"α": "raw (α=0)", "weighted vega": round(row["raw_vega"], 2)}]
)
