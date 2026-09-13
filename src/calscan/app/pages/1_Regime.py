from __future__ import annotations

from datetime import timedelta

import plotly.graph_objects as go
import streamlit as st

from calscan.domain import realized, termstructure
from calscan.events import load_events
from calscan.settings import Settings
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Regime — Calendar Scanner", page_icon="📈", layout="wide")

TENOR_DAYS = {"vix9d": 9, "vix": 30, "vix3m": 91, "vix6m": 182}


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg = settings.config
session_factory = _session_factory()

st.title("Regime")

with session_factory() as session:  # type: ignore[operator]
    vix_df = repo.get_vix_family_daily(session)
    spy_df = repo.get_spy_daily(session)

if vix_df.empty:
    st.warning("No VIX-family history yet — run `scripts/run_backfill.py`.")
    st.stop()

latest_date = vix_df.index[-1]
as_of = latest_date.date()

# --- IVTS + curve snapshot (today vs 5d ago vs 20d ago) ---
st.subheader("Term structure curve")
curve_fig = go.Figure()
for label, offset in [("today", 0), ("5d ago", 5), ("20d ago", 20)]:
    idx = len(vix_df) - 1 - offset
    if idx < 0:
        continue
    row = vix_df.iloc[idx]
    xs = [TENOR_DAYS[k] for k in ("vix9d", "vix", "vix3m", "vix6m")]
    ys = [row.get(k) for k in ("vix9d", "vix", "vix3m", "vix6m")]
    curve_fig.add_trace(
        go.Scatter(x=xs, y=ys, mode="lines+markers", name=f"{label} ({vix_df.index[idx].date()})")
    )
curve_fig.update_layout(
    xaxis_title="tenor (calendar days)", yaxis_title="level", height=400
)
st.plotly_chart(curve_fig, use_container_width=True)

latest = vix_df.iloc[-1]
if latest[["vix", "vix3m"]].notna().all():
    ivts_val = termstructure.ivts(latest["vix"], latest["vix3m"])
    st.metric("IVTS (VIX / VIX3M)", f"{ivts_val:.3f}")

# --- slope_z history ---
st.subheader("Slope z-score (VIX3M − VIX, 252d window)")
slope_z = termstructure.slope_z_series(
    vix_df["vix"], vix_df["vix3m"], window=cfg.regime.slope_z_window_days
)
window_1y = slope_z.loc[slope_z.index >= (latest_date - timedelta(days=365))]
z_fig = go.Figure()
z_fig.add_trace(go.Scatter(x=window_1y.index, y=window_1y.values, mode="lines", name="slope_z"))
for band, dash in [(1, "dot"), (-1, "dot"), (2, "dash"), (-2, "dash")]:
    z_fig.add_hline(y=band, line_dash=dash, line_color="gray", opacity=0.5)
z_fig.update_layout(height=350)
st.plotly_chart(z_fig, use_container_width=True)

# --- RV10/RV20 (front IV overlay lands with Phase 2 chains) ---
st.subheader("Realized vol (SPY)")
if len(spy_df) > cfg.regime.rv_window_long:
    rv10_series = realized.realized_vol_series(spy_df["close"], cfg.regime.rv_window_short)
    rv20_series = realized.realized_vol_series(spy_df["close"], cfg.regime.rv_window_long)
    rv_fig = go.Figure()
    rv_fig.add_trace(go.Scatter(x=rv10_series.index, y=rv10_series.values, name="RV10"))
    rv_fig.add_trace(go.Scatter(x=rv20_series.index, y=rv20_series.values, name="RV20"))
    rv_fig.update_layout(height=350, yaxis_tickformat=".0%")
    st.plotly_chart(rv_fig, use_container_width=True)
    st.caption("Front-IV overlay lands with Phase 2 (needs a product ATM curve from live chains).")
else:
    st.info("Not enough SPY history for RV20 yet.")

# --- upcoming events ---
st.subheader("Upcoming events (next 45 days)")
events = load_events()
upcoming = sorted(events.in_window(as_of, as_of + timedelta(days=45)), key=lambda e: e.date)
if upcoming:
    st.table(
        [
            {
                "date": e.date,
                "days_until": (e.date - as_of).days,
                "category": e.category,
                "label": e.label,
            }
            for e in upcoming
        ]
    )
else:
    st.write("None in window.")

st.subheader("ATM curve per product (out to 120 DTE)")
st.info("Needs live option chains — lands with Phase 2.")
