from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from calscan.app.data import load_fixture_chain, load_live_chain, load_market_snapshot
from calscan.domain.models import Chain
from calscan.events import load_events
from calscan.jobs.marks import mark_position
from calscan.settings import Settings
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Positions — Calendar Scanner", page_icon="📋", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg = settings.config
session_factory = _session_factory()

st.title("Positions")
st.caption("Decision support only — no order placement.")

has_live_creds = bool(settings.secrets.schwab_app_key or settings.secrets.alpaca_api_key)
use_fixtures = st.toggle("Use bundled fixture data for marking", value=not has_live_creds)

with session_factory() as session:  # type: ignore[operator]
    open_positions = repo.get_positions(session, status="open")
    latest_marks = repo.get_latest_marks(session)
    snapshot = load_market_snapshot(session, cfg)

if not open_positions:
    st.info("No open positions. Add one from the Scanner page.")
else:
    if st.button("Refresh marks now"):
        events = load_events()
        ivts_now = snapshot.ivts if snapshot else float("nan")
        chains: dict[str, Chain | None] = {}
        with session_factory() as session:  # type: ignore[operator]
            for position in open_positions:
                product = position["product"]
                if product not in chains:
                    if use_fixtures:
                        chains[product] = load_fixture_chain(product)
                    else:
                        try:
                            chains[product] = load_live_chain(settings, cfg, product)
                        except Exception as exc:
                            st.warning(f"Live fetch failed for {product}: {exc}")
                            chains[product] = None
                chain = chains[product]
                if chain is None:
                    continue
                try:
                    mark = mark_position(position, chain, ivts_now, events, cfg, as_of=date.today())
                except ValueError as exc:
                    st.warning(f"Position #{position['id']}: {exc}")
                    continue
                repo.insert_position_mark(session, mark)
        st.rerun()

    for position in open_positions:
        latest_mark = latest_marks.get(position["id"])
        header = (
            f"#{position['id']}  {position['product']} {position['side']} {position['strike']:g}  "
            f"{position['front_expiry']}/{position['back_expiry']}  "
            f"({position['contracts']}x, playbook {position['playbook']})"
        )
        with st.expander(header, expanded=True):
            if latest_mark is None:
                st.info("Not marked yet — click “Refresh marks now” above.")
            else:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("P&L %", f"{latest_mark['pnl_pct'] * 100:.1f}%")
                col2.metric("Dist σ", f"{latest_mark['dist_sigma']:.2f}")
                col3.metric("DTE1", latest_mark["dte1"])
                col4.metric(latest_mark["verdict"], latest_mark["verdict_reason"])

            st.caption(
                f"Debit paid: {position['debit_paid']:.2f} · "
                f"Entry σ: {position['sigma_entry_pts']:.2f} · "
                f"IV1/IV2 @ entry: {position['iv1_entry']:.4f}/{position['iv2_entry']:.4f}"
            )
            if position["thesis"]:
                st.caption(f"Thesis: {position['thesis']}")
            if position["hedge_pnl"]:
                st.caption(f"Hedge P&L to date: {position['hedge_pnl']:.2f}")

            close_col, hedge_col = st.columns(2)
            with close_col, st.form(key=f"close_{position['id']}"):
                exit_price = st.number_input("Exit price (debit received)", min_value=0.0, step=0.5)
                exit_reason = st.text_input(
                    "Exit reason", value=latest_mark["verdict_reason"] if latest_mark else ""
                )
                if st.form_submit_button("Mark closed"):
                    multiplier = cfg.products[position["product"]].multiplier
                    realized_pnl = (
                        (exit_price - position["debit_paid"]) * multiplier * position["contracts"]
                    )
                    with session_factory() as session:  # type: ignore[operator]
                        repo.close_position(
                            session,
                            position["id"],
                            datetime.utcnow(),
                            exit_price,
                            exit_reason,
                            realized_pnl,
                        )
                        repo.insert_journal_entry(
                            session,
                            ts=datetime.utcnow(),
                            event_type="closed",
                            position_id=position["id"],
                            payload={
                                "exit_price": exit_price,
                                "exit_reason": exit_reason,
                                "realized_pnl": realized_pnl,
                            },
                        )
                    st.success("Closed.")
                    st.rerun()

            with hedge_col, st.form(key=f"hedge_{position['id']}"):
                hedge_pnl = st.number_input("Hedge P&L to record", step=1.0)
                if st.form_submit_button("Record hedge P&L"):
                    with session_factory() as session:  # type: ignore[operator]
                        repo.record_hedge_pnl(session, position["id"], hedge_pnl)
                        repo.insert_journal_entry(
                            session,
                            ts=datetime.utcnow(),
                            event_type="hedge",
                            position_id=position["id"],
                            payload={"hedge_pnl": hedge_pnl},
                        )
                    st.success("Recorded.")
                    st.rerun()

st.divider()
st.subheader("Closed positions")
with session_factory() as session:  # type: ignore[operator]
    closed_positions = repo.get_positions(session, status="closed")
if closed_positions:
    st.dataframe(
        [
            {
                "id": p["id"],
                "product": p["product"],
                "side": p["side"],
                "strike": p["strike"],
                "opened": p["opened_ts"],
                "closed": p["closed_ts"],
                "debit paid": p["debit_paid"],
                "exit price": p["exit_price"],
                "realized P&L": p["realized_pnl"],
                "hedge P&L": p["hedge_pnl"],
                "exit reason": p["exit_reason"],
            }
            for p in closed_positions
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("None yet.")
