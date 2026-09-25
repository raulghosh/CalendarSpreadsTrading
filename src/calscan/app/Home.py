from __future__ import annotations

import streamlit as st

from calscan.app.data import load_market_snapshot
from calscan.settings import Settings
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Calendar Scanner", page_icon="📅", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
session_factory = _session_factory()

st.title("Calendar Spread Regime & Scanner")
st.caption("Decision support only — no order placement.")

with session_factory() as session:  # type: ignore[operator]
    snapshot = load_market_snapshot(session, settings.config)

col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("Data freshness")
    if snapshot is None:
        st.warning("No history yet — run `scripts/run_backfill.py`.")
    else:
        st.write(f"VIX-family (CBOE): **{snapshot.as_of}**")
        spy_last = snapshot.spy_df.index[-1].date() if not snapshot.spy_df.empty else "—"
        st.write(f"SPY daily bars (Alpaca): **{spy_last}**")
        if snapshot.spy_df.empty:
            st.warning("No SPY history — RV10 is unknown, so the scanner's RV10 gate fails closed.")
        st.write("Option chains are fetched live per page (or from bundled fixtures in demo mode).")

with col2:
    st.subheader("Auth status")
    if settings.secrets.schwab_token_file.exists():
        st.success("Schwab: token file present")
    else:
        st.error("Schwab: not authenticated — run the OAuth flow to create a token file")
    if settings.secrets.alpaca_api_key:
        st.success("Alpaca: API key configured")
    else:
        st.error("Alpaca: ALPACA_API_KEY not set in .env")

with col3:
    st.subheader("Market regime")
    if snapshot is None:
        st.write("—")
    else:
        st.write(f"**{snapshot.regime.regime}** → playbook {snapshot.regime.playbook}")
        if snapshot.regime.note:
            st.caption(snapshot.regime.note)
        st.caption("VIX-family read (CBOE); one regime applies to SPX/XSP/SPY alike.")

st.divider()
st.page_link("pages/1_Regime.py", label="Regime →")
