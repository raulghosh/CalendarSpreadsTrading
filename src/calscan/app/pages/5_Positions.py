from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import streamlit as st

from calscan.settings import Settings
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Positions — Calendar Scanner", page_icon="📋", layout="wide")
ET = ZoneInfo("America/New_York")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg = settings.config
session_factory = _session_factory()

st.title("Positions")
st.caption(
    "Decision support only — no order placement. Marks and exit-tree verdicts update with each "
    "snapshot (Home → Run snapshot now)."
)

with session_factory() as session:  # type: ignore[operator]
    open_positions = repo.get_positions(session, status="open")
    latest_marks = repo.get_latest_marks(session)

if not open_positions:
    st.info("No open positions. Add one from the Scanner page.")
else:
    for position in open_positions:
        latest_mark = latest_marks.get(position["id"])
        header = (
            f"#{position['id']}  {position['product']} {position['side']} {position['strike']:g}  "
            f"{position['front_expiry']}/{position['back_expiry']}  "
            f"({position['contracts']}x, playbook {position['playbook']})"
        )
        with st.expander(header, expanded=True):
            if latest_mark is None:
                st.info("Not marked yet — it will be on the next snapshot.")
            else:
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("P&L %", f"{latest_mark['pnl_pct'] * 100:.1f}%")
                col2.metric("Dist σ", f"{latest_mark['dist_sigma']:.2f}")
                col3.metric("DTE1", latest_mark["dte1"])
                ivts_now = latest_mark["ivts_now"]
                col4.metric(
                    "IVTS", f"{ivts_now:.3f}" if ivts_now is not None else "n/a"
                )  # missing → the curve-based exit branch was skipped
                col5.metric(latest_mark["verdict"], latest_mark["verdict_reason"])
                marked = latest_mark["ts"].replace(tzinfo=UTC).astimezone(ET)
                st.caption(f"Marked {marked:%Y-%m-%d %H:%M} ET")

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
