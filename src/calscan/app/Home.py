from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import streamlit as st

from calscan.adapters.schwab import REFRESH_TOKEN_DAYS, token_age_days
from calscan.app.data import load_market_snapshot
from calscan.events import load_events, previous_trading_day
from calscan.jobs.snapshot import (
    ET,
    alert_sender,
    latest_expected_run,
    live_sources,
    run_snapshot,
    snapshot_is_stale,
)
from calscan.settings import Settings
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Calendar Scanner", page_icon="📅", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
cfg, secrets = settings.config, settings.secrets
session_factory = _session_factory()
events = load_events()
now = datetime.now(UTC)
today = now.astimezone(ET).date()


def _et(ts: datetime) -> str:
    return f"{ts.replace(tzinfo=UTC).astimezone(ET):%a %Y-%m-%d %H:%M} ET"


st.title("Calendar Spread Regime & Scanner")
st.caption("Decision support only — no order placement.")

# --- credentials: fail loudly before the job does ---
token_age = token_age_days(secrets)
if token_age is None:
    st.error("Schwab: not authenticated — run `uv run python scripts/schwab_login.py` once.")
elif token_age >= REFRESH_TOKEN_DAYS:
    st.error(
        f"Schwab token is {token_age:.1f} days old and has likely expired — "
        "re-run `scripts/schwab_login.py`."
    )
elif token_age >= REFRESH_TOKEN_DAYS - 1:
    st.warning(
        f"Schwab token expires in ~{REFRESH_TOKEN_DAYS - token_age:.1f} days — "
        "re-run `scripts/schwab_login.py` soon."
    )
if not (secrets.alpaca_api_key and secrets.alpaca_secret_key):
    st.error("Alpaca: ALPACA_API_KEY / ALPACA_SECRET_KEY not set — no SPY history or SPY chain.")

if st.button("Run snapshot now", type="primary"):
    with st.spinner("Running snapshot — fetching CBOE, Alpaca and Schwab data…"):
        with session_factory() as session:  # type: ignore[operator]
            run_snapshot(
                session, cfg, live_sources(settings), events, notify=alert_sender(cfg, secrets)
            )
    st.rerun()

with session_factory() as session:  # type: ignore[operator]
    last_run = repo.get_latest_snapshot_run(session)
    term = repo.get_latest_term_structure(session)
    curves = repo.get_latest_atm_curves(session)
    marks = repo.get_latest_marks(session)
    open_ids = [p["id"] for p in repo.get_positions(session, status="open")]
    vix_df = repo.get_vix_family_daily(session)
    spy_df = repo.get_spy_daily(session)
    market = load_market_snapshot(session, cfg, events)

# --- last run ---
st.subheader("Last snapshot run")
if last_run is None:
    st.info(
        "No snapshot has run yet — click **Run snapshot now**, or install the schedule with "
        "`uv run python scripts/install_schedule.py --install`."
    )
else:
    failed = [s for s in last_run["report"]["steps"] if not s["ok"]]
    status = "all steps ok" if last_run["ok"] else f"{len(failed)} step(s) failed"
    (st.success if last_run["ok"] else st.warning)(f"{_et(last_run['ts'])} — {status}")
    if snapshot_is_stale(last_run["ts"], now, cfg, events):
        expected = latest_expected_run(now, cfg.schedule.run_times_et, events)
        st.error(
            f"Stale: the {expected:%a %H:%M} ET run hasn't happened. Is the schedule installed "
            "(`scripts/install_schedule.py --install`) and the Mac awake? See data/snapshot.log."
        )
    with st.expander("Step details", expanded=not last_run["ok"]):
        st.dataframe(
            [
                {"step": s["name"], "ok": "✅" if s["ok"] else "❌", "detail": s["detail"]}
                for s in last_run["report"]["steps"]
            ],
            use_container_width=True,
            hide_index=True,
        )
    alerts = last_run["report"]["alerts"]
    if alerts:
        st.markdown("**Alerts from this run**")
        for alert in alerts:
            st.write(f"• {alert}")

# --- freshness per source, judged against the trading calendar ---
st.subheader("Data freshness")
expected_close = previous_trading_day(today, events)


def _daily(label: str, last: date | None) -> dict[str, Any]:
    fresh = last is not None and last >= expected_close
    note = "" if fresh else f"expected a close from {expected_close}"
    return {"source": label, "latest": str(last or "none"), "status": "✅" if fresh else "⚠️",
            "note": note}


def _snapshot(label: str, ts: datetime | None) -> dict[str, Any]:
    fresh = ts is not None and not snapshot_is_stale(ts, now, cfg, events)
    return {"source": label, "latest": _et(ts) if ts else "none", "status": "✅" if fresh else "⚠️",
            "note": "" if fresh else "missed the latest scheduled run"}


freshness = [
    _daily("VIX family — CBOE / Schwab live", vix_df.index[-1].date() if len(vix_df) else None),
    _daily("SPY daily bars — Alpaca", spy_df.index[-1].date() if len(spy_df) else None),
]
freshness += [
    _snapshot(f"{product} chain / ATM curve", curves[product][0]["snapshot_ts"] if product in curves
              else None)
    for product in cfg.products
]
if open_ids:  # the least recently marked open position sets the status
    oldest = [marks[i]["ts"] if i in marks else None for i in open_ids]
    freshness.append(_snapshot(
        f"Marks for {len(open_ids)} open position(s)",
        None if None in oldest else min(ts for ts in oldest if ts is not None),
    ))
st.dataframe(freshness, use_container_width=True, hide_index=True)

# --- regime per product ---
st.subheader("Regime")
if term:
    st.dataframe(
        [
            {
                "product": product,
                "regime": row["regime"],
                "playbook": row["playbook"],
                "IVTS": round(row["ivts"], 3) if row["ivts"] is not None else None,
                "spot": round(row["spot"], 2),
                "notes": row["notes"],
                "as of": _et(row["snapshot_ts"]),
            }
            for product, row in sorted(term.items())
        ],
        use_container_width=True,
        hide_index=True,
    )
elif market is not None:
    st.write(f"**{market.regime.regime}** → playbook {market.regime.playbook}")
    st.caption(f"From VIX-family closes as of {market.as_of}; no product snapshot stored yet.")
else:
    st.write("— (no VIX-family history yet)")
