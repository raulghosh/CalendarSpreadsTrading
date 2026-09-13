from __future__ import annotations

import streamlit as st

from calscan.app.data import (
    build_ranked_candidates,
    load_fixture_chain,
    load_live_chain,
    load_market_snapshot,
    open_position_from_candidate,
)
from calscan.domain.scanner import spread_width_pct
from calscan.settings import Settings
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Scanner — Calendar Scanner", page_icon="🔎", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg = settings.config
session_factory = _session_factory()

st.title("Scanner")
st.caption("Decision support only — no order placement. Row-detail scenario grid lands in Phase 3.")

has_live_creds = bool(settings.secrets.schwab_app_key or settings.secrets.alpaca_api_key)
use_fixtures = st.toggle("Use bundled fixture data", value=not has_live_creds)
if use_fixtures:
    st.caption("Demo mode: SPX/XSP/SPY chains loaded from tests/fixtures/, not live quotes.")

with session_factory() as session:  # type: ignore[operator]
    snapshot = load_market_snapshot(session, cfg)

tabs = st.tabs(list(cfg.products.keys()))
for product, tab in zip(cfg.products.keys(), tabs, strict=True):
    with tab:
        if use_fixtures:
            chain = load_fixture_chain(product)
        else:
            try:
                chain = load_live_chain(settings, cfg, product)
            except Exception as exc:  # best-effort live fetch; surfaced in the UI, not raised
                st.warning(f"Live fetch failed for {product}: {exc}")
                chain = None
        if chain is None:
            st.warning(f"No chain data available for {product}.")
            continue

        ranked = build_ranked_candidates(chain, product, cfg, snapshot)
        if not ranked:
            st.info("No candidates from the current expiry/strike grid.")
            continue

        st.caption(f"Spot: {chain.underlying_price:,.2f} · {len(ranked)} candidates")
        rows = [
            {
                "✓": "✅" if c.tradeable else "—",
                "side": c.side,
                "strike": c.strike,
                "front exp": c.front.expiry,
                "back exp": c.back.expiry,
                "dte1": c.front.dte,
                "dte2": c.back.dte,
                "R": round(c.ratio, 2),
                "iv1": round(c.front.iv, 4) if c.front.iv is not None else None,
                "iv2": round(c.back.iv, 4) if c.back.iv is not None else None,
                "fwd vol": round(c.fwd_vol, 4) if c.fwd_vol == c.fwd_vol else None,  # NaN check
                "debit": round(c.debit_mid, 2),
                "spread1 %": round(spread_width_pct(c.front) * 100, 2),
                "spread2 %": round(spread_width_pct(c.back) * 100, 2),
                "oi1": c.front.open_interest,
                "oi2": c.back.open_interest,
                "net θ": round(c.greeks.net_theta, 2),
                "raw vega": round(c.greeks.raw_vega, 2),
                "wvega .35": round(c.wvega.get(0.35, float("nan")), 2),
                "wvega .5": round(c.wvega.get(0.5, float("nan")), 2),
                "wvega .7": round(c.wvega.get(0.7, float("nan")), 2),
                "daily BE pts": round(c.greeks.daily_breakeven_pts, 2),
                "gates": ", ".join(k for k, v in c.gates.as_dict().items() if not v) or "all pass",
                "score": c.soft_score,
            }
            for c in ranked
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.subheader("Add to positions")
        st.caption("Records it as opened — no order is sent.")
        labels = [
            f"{c.side} {c.strike:g}  {c.front.expiry}/{c.back.expiry}  "
            f"({'tradeable' if c.tradeable else 'gated'}, score {c.soft_score})"
            for c in ranked
        ]
        def _label(i: int, labels: list[str] = labels) -> str:
            return labels[i]

        with st.form(key=f"add_position_{product}"):
            choice = st.selectbox("Candidate", options=list(range(len(ranked))), format_func=_label)
            contracts = st.number_input("Contracts", min_value=1, value=1, step=1)
            thesis = st.text_input("Thesis (optional)")
            submitted = st.form_submit_button("Add to positions")
        if submitted and choice is not None:
            playbook = snapshot.regime.playbook if snapshot else "A"
            with session_factory() as session:  # type: ignore[operator]
                position_id = open_position_from_candidate(
                    session,
                    ranked[choice],
                    product,
                    chain.underlying_price,
                    playbook,
                    contracts,
                    thesis,
                )
            st.success(f"Opened position #{position_id}.")
