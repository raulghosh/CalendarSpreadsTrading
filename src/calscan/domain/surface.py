"""ATM IV per expiry from a chain. Build plan §5.1."""

from __future__ import annotations

import math
from collections.abc import Sequence

from calscan.domain.models import ATMPoint, OptionContract, Side


def forward_price(spot: float, r: float, q: float, t: float) -> float:
    return spot * math.exp((r - q) * t)


def is_usable_quote(contract: OptionContract) -> bool:
    """Excludes bid == 0 or ask/bid > 3, per build plan §5.1."""
    return contract.bid > 0 and contract.ask / contract.bid <= 3


def _group_by_strike(
    contracts: Sequence[OptionContract],
) -> dict[float, dict[Side, OptionContract]]:
    grouped: dict[float, dict[Side, OptionContract]] = {}
    for c in contracts:
        grouped.setdefault(c.strike, {})[c.side] = c
    return grouped


def iv_mid_at_strike(by_side: dict[Side, OptionContract]) -> float | None:
    """mean(call_iv, put_iv) if both quoted and usable; else whichever exists."""
    usable = {
        side: c for side, c in by_side.items() if is_usable_quote(c) and c.iv is not None
    }
    if not usable:
        return None
    if len(usable) == 2:
        return (usable["call"].iv + usable["put"].iv) / 2  # type: ignore[operator]
    return next(iter(usable.values())).iv


def atm_iv_for_expiry(
    contracts: Sequence[OptionContract], spot: float, r: float, q: float
) -> ATMPoint:
    """Linear interpolation of iv_mid between the bracketing strikes at the forward.

    Falls back to the strike whose |delta| is closest to 0.50 if either bracketing strike
    lacks a usable quote.
    """
    if not contracts:
        raise ValueError("no contracts for this expiry")

    product = contracts[0].underlying_symbol
    expiry = contracts[0].expiry
    dte = contracts[0].dte
    t = dte / 365
    fwd = forward_price(spot, r, q, t)

    by_strike = _group_by_strike(contracts)
    strikes = sorted(by_strike)
    lo_candidates = [k for k in strikes if k <= fwd]
    hi_candidates = [k for k in strikes if k >= fwd]
    k_lo = lo_candidates[-1] if lo_candidates else strikes[0]
    k_hi = hi_candidates[0] if hi_candidates else strikes[-1]

    iv_lo = iv_mid_at_strike(by_strike[k_lo])
    iv_hi = iv_mid_at_strike(by_strike[k_hi])

    if k_lo == k_hi and iv_lo is not None:
        atm_iv, method = iv_lo, "exact_strike"
    elif iv_lo is not None and iv_hi is not None:
        weight = (fwd - k_lo) / (k_hi - k_lo)
        atm_iv, method = iv_lo + weight * (iv_hi - iv_lo), "interpolated"
    else:
        best_strike, best_map, best_dist = None, None, math.inf
        for strike, side_map in by_strike.items():
            for c in side_map.values():
                if not is_usable_quote(c) or c.delta is None:
                    continue
                dist = abs(abs(c.delta) - 0.50)
                if dist < best_dist:
                    best_strike, best_map, best_dist = strike, side_map, dist
        if best_strike is None or best_map is None:
            raise ValueError("no usable quotes to determine ATM IV for this expiry")
        fallback_iv = iv_mid_at_strike(best_map)
        if fallback_iv is None:
            fallback_iv = next(c.iv for c in best_map.values() if c.iv is not None)
        atm_iv, method = fallback_iv, "delta_fallback"
        k_lo = k_hi = best_strike

    return ATMPoint(
        product=product,
        expiry=expiry,
        dte=dte,
        atm_iv=atm_iv,
        fwd=fwd,
        strike_lo=k_lo,
        strike_hi=k_hi,
        method=method,
    )
