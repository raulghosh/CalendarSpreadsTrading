"""Marks an open position against a live chain and runs the exit tree. Shared by the Positions
page (on-demand) and the Phase 5 scheduled snapshot job — see build plan §7 step 6.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from calscan.domain.exit_tree import ExitInputs, decide_exit
from calscan.domain.greeks import calendar_greeks
from calscan.domain.models import Chain
from calscan.events import EventsCalendar
from calscan.settings import AppConfig


def mark_position(
    position: dict[str, Any],
    chain: Chain,
    ivts_now: float,
    events: EventsCalendar,
    cfg: AppConfig,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()

    front = next(
        (
            c
            for c in chain.contracts
            if c.expiry == position["front_expiry"]
            and c.strike == position["strike"]
            and c.side == position["side"]
        ),
        None,
    )
    back = next(
        (
            c
            for c in chain.contracts
            if c.expiry == position["back_expiry"]
            and c.strike == position["strike"]
            and c.side == position["side"]
        ),
        None,
    )
    if front is None or back is None:
        raise ValueError(
            f"chain for {position['product']} is missing the front/back contract "
            f"for position {position['id']}"
        )

    debit_paid = position["debit_paid"]
    mark_mid = back.mid - front.mid
    pnl_pct = (mark_mid - debit_paid) / debit_paid if debit_paid else float("nan")
    dist_sigma = abs(chain.underlying_price - position["strike"]) / position["sigma_entry_pts"]

    multiplier = cfg.products[position["product"]].multiplier
    greeks = calendar_greeks(front, back, multiplier, position["contracts"], chain.underlying_price)

    event_soon = False
    if position["front_expiry"] >= as_of:
        window_end = min(as_of + timedelta(days=3), position["front_expiry"])
        event_soon = bool(events.in_window(as_of, window_end))

    targets = (
        cfg.targets.playbook_A if position["playbook"] == "A" else cfg.targets.playbook_B
    )
    exit_inputs = ExitInputs(
        dte1=front.dte,
        spot=chain.underlying_price,
        strike=position["strike"],
        sigma_entry_pts=position["sigma_entry_pts"],
        playbook=position["playbook"],
        ivts=ivts_now,
        pnl_pct=pnl_pct,
        net_delta=greeks.net_delta,
        event_in_front_window_within_3_days=event_soon,
    )
    verdict = decide_exit(exit_inputs, targets, cfg.regime, cfg.exit_tree.delta_band)

    return {
        "ts": datetime.utcnow(),
        "position_id": position["id"],
        "spot": chain.underlying_price,
        "mark_mid": mark_mid,
        "pnl_pct": pnl_pct,
        "dist_sigma": dist_sigma,
        "dte1": front.dte,
        "ivts_now": ivts_now,
        "verdict": verdict.action,
        "verdict_reason": verdict.reason,
    }
