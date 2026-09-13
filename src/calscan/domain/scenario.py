"""Own Black-Scholes pricer + the spot×IV×twist scenario grid. Build plan §5.8."""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq
from scipy.stats import norm

from calscan.domain.models import Side
from calscan.domain.sigma import sigma_pts


def black_scholes_price(
    spot: float, strike: float, t: float, r: float, q: float, sigma: float, side: Side
) -> float:
    """Black-Scholes-Merton price with a continuous dividend yield q."""
    if t <= 0 or sigma <= 0:
        intrinsic = spot - strike if side == "call" else strike - spot
        return max(intrinsic, 0.0)

    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    disc_q = math.exp(-q * t)
    disc_r = math.exp(-r * t)

    if side == "call":
        return float(spot * disc_q * norm.cdf(d1) - strike * disc_r * norm.cdf(d2))
    return float(strike * disc_r * norm.cdf(-d2) - spot * disc_q * norm.cdf(-d1))


def implied_vol(
    price: float, spot: float, strike: float, t: float, r: float, q: float, side: Side
) -> float:
    """Own BS inversion — fills the gap if an adapter only returns quotes, not IV (§1)."""

    def diff(sigma: float) -> float:
        return black_scholes_price(spot, strike, t, r, q, sigma, side) - price

    return float(brentq(diff, 1e-4, 5.0, xtol=1e-6))


@dataclass(frozen=True, slots=True)
class ShockScenario:
    label: str
    iv_shift_front: float  # vol points, e.g. 4.0 == +4 vol points == +0.04
    iv_shift_back: float


def build_shock_scenarios(
    iv_shifts_front: list[float], iv_shifts_back: list[float]
) -> list[ShockScenario]:
    """iv_shifts_front/back are paired by index (equal-length config lists), not a cross
    product — back typically shifts less than front, matching real term-structure reactions."""
    return [
        ShockScenario(
            label=f"front{f:+g}/back{b:+g}",
            iv_shift_front=f,
            iv_shift_back=b,
        )
        for f, b in zip(iv_shifts_front, iv_shifts_back, strict=True)
    ]


@dataclass(frozen=True, slots=True)
class ScenarioCell:
    spot_move_sigma: float
    scenario: ShockScenario
    new_spot: float
    front_price: float
    back_price: float
    pnl_dollars: float
    pnl_pct_of_debit: float


@dataclass(frozen=True, slots=True)
class ScenarioGrid:
    horizon_days: int
    debit: float
    cells: tuple[ScenarioCell, ...]
    worst_cell: ScenarioCell
    inversion_cell: ScenarioCell


def default_horizon_days(front_dte: int) -> int:
    return max(1, min(7, front_dte - 1))


def scenario_grid(
    *,
    spot: float,
    front_strike: float,
    front_side: Side,
    front_iv: float,
    front_dte: int,
    back_strike: float,
    back_side: Side,
    back_iv: float,
    back_dte: int,
    debit: float,
    r: float,
    q: float,
    spot_moves_sigma: list[float],
    iv_shifts_front: list[float],
    iv_shifts_back: list[float],
    horizon_days: int | None = None,
) -> ScenarioGrid:
    """Reprices both legs at `spot·(1 + move·σ1_daily·√horizon)` (== spot + move·sigma_pts(spot,
    front_iv, horizon)) for each spot move, with front/back IV shifted by paired shock
    scenarios, at the given horizon (defaults to min(7, front_dte − 1))."""
    horizon = horizon_days if horizon_days is not None else default_horizon_days(front_dte)
    horizon = min(horizon, front_dte - 1, back_dte - 1)
    horizon = max(horizon, 0)

    t1 = (front_dte - horizon) / 365
    t2 = (back_dte - horizon) / 365
    scenarios = build_shock_scenarios(iv_shifts_front, iv_shifts_back)
    move_sigma_pts = sigma_pts(spot, front_iv, horizon)

    cells = []
    for move in spot_moves_sigma:
        new_spot = spot + move * move_sigma_pts
        for scen in scenarios:
            iv1 = max(front_iv + scen.iv_shift_front / 100, 1e-4)
            iv2 = max(back_iv + scen.iv_shift_back / 100, 1e-4)
            front_price = black_scholes_price(new_spot, front_strike, t1, r, q, iv1, front_side)
            back_price = black_scholes_price(new_spot, back_strike, t2, r, q, iv2, back_side)
            new_value = back_price - front_price
            pnl = new_value - debit
            cells.append(
                ScenarioCell(
                    spot_move_sigma=move,
                    scenario=scen,
                    new_spot=new_spot,
                    front_price=front_price,
                    back_price=back_price,
                    pnl_dollars=pnl,
                    pnl_pct_of_debit=pnl / debit if debit else float("nan"),
                )
            )

    worst_cell = min(cells, key=lambda c: c.pnl_pct_of_debit)
    # "inversion" cell: the steepest front-outpaces-back shift (curve inverting against the
    # short-front/long-back position), at the spot move closest to -1σ.
    steepest = max(scenarios, key=lambda s: s.iv_shift_front - s.iv_shift_back)
    closest_move = min(spot_moves_sigma, key=lambda m: abs(m - (-1.0)))
    inversion_cell = next(
        c for c in cells if c.scenario is steepest and c.spot_move_sigma == closest_move
    )

    return ScenarioGrid(
        horizon_days=horizon,
        debit=debit,
        cells=tuple(cells),
        worst_cell=worst_cell,
        inversion_cell=inversion_cell,
    )
