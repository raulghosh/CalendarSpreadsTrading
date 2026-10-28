from __future__ import annotations

from typing import Any

import streamlit as st

from calscan.app.data import load_market_snapshot, open_position_from_row, product_view
from calscan.settings import Settings
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Scanner — Calendar Scanner", page_icon="🔎", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg = settings.config
session_factory = _session_factory()

st.title("Scanner")
st.caption(
    "Decision support only — no order placement. Candidates come from the latest stored "
    "snapshot (refresh via Home → Run snapshot now); reprice any row on the Scenario Grid page."
)

with session_factory() as session:  # type: ignore[operator]
    snapshot = load_market_snapshot(session, cfg)
    has_snapshot = repo.get_latest_snapshot_run(session) is not None

demo = st.toggle("Demo mode (bundled fixture chains)", value=not has_snapshot)
if snapshot is None or snapshot.rv10 != snapshot.rv10:  # NaN check
    st.warning(
        "RV10 is unknown (no SPY price history in the DB) — the RV10 gate fails closed, so "
        "nothing can be marked tradeable until SPY history is loaded."
    )
regime_playbook = snapshot.regime.playbook if snapshot else "NONE"


def _table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "✓": "✅" if all(r["gates_passed"].values()) else "—",
            "side": r["side"],
            "strike": r["strike"],
            "front exp": r["front_expiry"],
            "back exp": r["back_expiry"],
            "dte1": r["dte1"],
            "dte2": r["dte2"],
            "R": round(r["ratio"], 2),
            "iv1": round(r["iv1"], 4),
            "iv2": round(r["iv2"], 4),
            "fwd vol": round(r["fwd_vol"], 4) if r["fwd_vol"] is not None else None,
            "debit": round(r["debit_mid"], 2),
            "spread %": round(r["spread_width_pct"] * 100, 2),
            "oi1": r["oi1"],
            "oi2": r["oi2"],
            "Δ@K": round(r["delta_at_strike"], 3),
            "d σ": round(r["d_sigma"], 2),
            "net θ": round(r["net_theta"], 2),
            "raw vega": round(r["raw_vega"], 2),
            "wvega .35": round(r["wvega_035"], 2),
            "wvega .5": round(r["wvega_050"], 2),
            "wvega .7": round(r["wvega_070"], 2),
            "daily BE pts": round(r["daily_breakeven_pts"], 2),
            "gates": ", ".join(k for k, ok in r["gates_passed"].items() if not ok) or "all pass",
            "score": r["soft_score"],
        }
        for r in rows
    ]


tabs = st.tabs(list(cfg.products.keys()))
for product, tab in zip(cfg.products.keys(), tabs, strict=True):
    with tab:
        with session_factory() as session:  # type: ignore[operator]
            view = product_view(session, cfg, product, demo, snapshot)
        if not view.rows:
            st.info(f"No {product} candidates — {view.source}.")
            continue

        spot = f"spot {view.spot:,.2f} · " if view.spot is not None else ""
        st.caption(f"{view.source} · {spot}{len(view.rows)} candidates")
        if cfg.products[product].source == "alpaca":
            st.caption(
                "Alpaca's data feed carries no open interest, so the OI gate can't pass here — "
                "SPY rows are for comparison, never marked tradeable."
            )
        st.dataframe(_table(view.rows), use_container_width=True, hide_index=True)

        st.subheader("Add to positions")
        if not view.live or view.spot is None:
            st.caption("Demo rows are fixtures and can't be opened as real positions.")
            continue
        st.caption("Records it as opened — no order is sent.")
        labels = [
            f"#{r['rank']} {r['side']} {r['strike']:g}  {r['front_expiry']}/{r['back_expiry']}  "
            f"({'tradeable' if all(r['gates_passed'].values()) else 'gated'}, "
            f"score {r['soft_score']:g})"
            for r in view.rows
        ]

        def _label(i: int, labels: list[str] = labels) -> str:
            return labels[i]

        with st.form(key=f"add_position_{product}"):
            choice = st.selectbox(
                "Candidate", options=list(range(len(view.rows))), format_func=_label
            )
            playbook = st.selectbox(
                "Playbook (sets the exit-tree thresholds)",
                options=["A", "B"],
                index=1 if regime_playbook == "B" else 0,
                help=f"Current regime suggests: {regime_playbook}",
            )
            contracts = st.number_input("Contracts", min_value=1, value=1, step=1)
            thesis = st.text_input("Thesis (optional)")
            submitted = st.form_submit_button("Add to positions")
        if submitted and choice is not None:
            with session_factory() as session:  # type: ignore[operator]
                position_id = open_position_from_row(
                    session, view.rows[choice], view.spot, playbook, contracts, thesis
                )
            st.success(f"Opened position #{position_id}.")
