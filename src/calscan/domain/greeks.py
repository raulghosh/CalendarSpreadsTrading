"""Calendar net greeks. Build plan §5.5.

Per-contract greeks from the API are per share; the caller scales by multiplier · contracts.
Net = back − front, i.e. long the back leg, short the front leg (a long calendar).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from calscan.domain.models import OptionContract
from calscan.domain.sigma import daily_breakeven_pts


@dataclass(frozen=True, slots=True)
class CalendarGreeks:
    net_delta: float
    net_gamma: float
    net_theta: float
    raw_vega: float
    daily_breakeven_pts: float
    dollar_gamma_per_1pct: float


def _net(front: float, back: float, multiplier: int, contracts: int) -> float:
    return (back - front) * multiplier * contracts


def calendar_greeks(
    front: OptionContract, back: OptionContract, multiplier: int, contracts: int, spot: float
) -> CalendarGreeks:
    if None in (front.delta, back.delta, front.gamma, back.gamma, front.theta, back.theta):
        raise ValueError("both legs need delta/gamma/theta to compute calendar greeks")
    if front.vega is None or back.vega is None:
        raise ValueError("both legs need vega to compute calendar greeks")
    if front.iv is None:
        raise ValueError("front leg needs an IV to compute daily breakeven")

    net_delta = _net(front.delta, back.delta, multiplier, contracts)  # type: ignore[arg-type]
    net_gamma = _net(front.gamma, back.gamma, multiplier, contracts)  # type: ignore[arg-type]
    net_theta = _net(front.theta, back.theta, multiplier, contracts)  # type: ignore[arg-type]
    raw_vega = _net(front.vega, back.vega, multiplier, contracts)
    dollar_gamma_per_1pct = 0.5 * net_gamma * (0.01 * spot) ** 2

    return CalendarGreeks(
        net_delta=net_delta,
        net_gamma=net_gamma,
        net_theta=net_theta,
        raw_vega=raw_vega,
        daily_breakeven_pts=daily_breakeven_pts(spot, front.iv),
        dollar_gamma_per_1pct=dollar_gamma_per_1pct,
    )


def weighted_vega(
    vega_front: float,
    vega_back: float,
    t1: float,
    t2: float,
    alpha: float,
    multiplier: int = 1,
    contracts: int = 1,
) -> float:
    ratio: float = (t1 / t2) ** alpha
    return (vega_back * ratio - vega_front) * multiplier * contracts


def atm_approx_vega(spot: float, t: float) -> float:
    """Black-Scholes ATM approximation: Vega ≈ 0.4·S·√T (independent of σ near d1≈0)."""
    return 0.4 * spot * math.sqrt(t)


def atm_approx_theta(spot: float, sigma: float, t: float) -> float:
    """Black-Scholes ATM approximation: Theta ≈ −0.2·S·σ/√T (annualized)."""
    return -0.2 * spot * sigma / math.sqrt(t)


def approx_disagreement(api_value: float, approx_value: float) -> float:
    """Relative disagreement between an API greek and its ATM approximation.

    A large value (the caller decides the threshold) flags a bad quote rather than a real
    skew/term effect the flat-vol ATM approximation simply doesn't capture.
    """
    if approx_value == 0:
        return math.inf if api_value != 0 else 0.0
    return abs(api_value - approx_value) / abs(approx_value)
