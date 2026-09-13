"""Calendar candidate generation, hard gates, and soft scoring. Build plan §5.7."""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from calscan.domain import surface
from calscan.domain.greeks import CalendarGreeks, calendar_greeks, weighted_vega
from calscan.domain.models import Expiry, OptionContract, Side
from calscan.domain.sigma import skew_delta_adjust, strike_from_delta
from calscan.domain.termstructure import fwd_var, fwd_vol
from calscan.settings import ExpirySelectionConfig, GatesConfig, StrikesConfig


@dataclass(frozen=True, slots=True)
class Gates:
    spread_ok: bool
    oi_ok: bool
    fwd_var_ok: bool
    rv10_ok: bool
    debit_ok: bool
    ex_div_ok: bool

    @property
    def all_pass(self) -> bool:
        return all(
            (
                self.spread_ok,
                self.oi_ok,
                self.fwd_var_ok,
                self.rv10_ok,
                self.debit_ok,
                self.ex_div_ok,
            )
        )

    def as_dict(self) -> dict[str, bool]:
        return {
            "spread": self.spread_ok,
            "open_interest": self.oi_ok,
            "forward_var": self.fwd_var_ok,
            "rv10_below_front_iv": self.rv10_ok,
            "debit_within_nav": self.debit_ok,
            "ex_dividend": self.ex_div_ok,
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    product: str
    side: Side
    strike: float
    front: OptionContract
    back: OptionContract
    ratio: float
    fwd_vol: float
    debit_mid: float
    greeks: CalendarGreeks
    wvega: dict[float, float]  # stress alpha -> weighted vega (per 1 contract)
    gates: Gates
    soft_score: int

    @property
    def tradeable(self) -> bool:
        return self.gates.all_pass


def spread_width_pct(contract: OptionContract) -> float:
    mid = contract.mid
    return (contract.ask - contract.bid) / mid if mid > 0 else math.inf


def enumerate_expiry_pairs(
    expiries: Sequence[Expiry], cfg: ExpirySelectionConfig
) -> list[tuple[Expiry, Expiry]]:
    fronts = [
        e
        for e in expiries
        if cfg.front_dte_min <= e.dte <= cfg.front_dte_max
        and not (cfg.prefer_pm_settled and e.am_settled)
    ]
    pairs = []
    for front in fronts:
        for back in expiries:
            if back.dte <= front.dte:
                continue
            ratio = back.dte / front.dte
            if cfg.ratio_min <= ratio <= cfg.ratio_max:
                pairs.append((front, back))
    return pairs


def _by_strike(contracts: Sequence[OptionContract], side: Side) -> dict[float, OptionContract]:
    return {c.strike: c for c in contracts if c.side == side}


def _closest_contract(
    by_strike: dict[float, OptionContract], target_strike: float
) -> OptionContract | None:
    if not by_strike:
        return None
    return by_strike[min(by_strike, key=lambda k: abs(k - target_strike))]


def evaluate_gates(
    front: OptionContract,
    back: OptionContract,
    fwd_var: float,
    rv10: float,
    front_iv: float,
    debit: float,
    multiplier: int,
    nav: float,
    gates_cfg: GatesConfig,
    european_or_no_ex_div: bool,
) -> Gates:
    """`rv10 < daily_breakeven` (§5.7) reduces to `rv10 < front_iv`: both sides of that
    inequality share the same S/√252 scaling once expressed in the same units."""
    spread_ok = (
        spread_width_pct(front) <= gates_cfg.max_spread_pct_of_mid
        and spread_width_pct(back) <= gates_cfg.max_spread_pct_of_mid
    )
    oi_ok = (
        front.open_interest is not None
        and back.open_interest is not None
        and front.open_interest >= gates_cfg.min_open_interest
        and back.open_interest >= gates_cfg.min_open_interest
    )
    fwd_var_ok = fwd_var > 0
    rv10_ok = (not gates_cfg.rv10_must_be_below_breakeven) or (rv10 < front_iv)
    debit_ok = debit * multiplier <= gates_cfg.max_debit_pct_of_nav * nav
    return Gates(
        spread_ok=spread_ok,
        oi_ok=oi_ok,
        fwd_var_ok=fwd_var_ok,
        rv10_ok=rv10_ok,
        debit_ok=debit_ok,
        ex_div_ok=european_or_no_ex_div,
    )


def soft_score(
    *,
    slope_z_favourable: bool,
    front: OptionContract,
    back: OptionContract,
    front_expiry: date,
    back_expiry: date,
    raw_vega: float,
    weighted_vega_worst_case: float,
    quad_witching_dates: Collection[date],
) -> int:
    """0-5, per build plan §5.7: favourable slope_z, tight spread, deep OI, no quad-witching,
    and weighted_vega at the worst-case vol-shock alpha staying within the position's own
    raw-vega budget (`weighted_vega_worst_case` is computed by the caller at the highest
    configured stress alpha, e.g. 0.70)."""
    tight_spread = (
        spread_width_pct(front) < 0.01 and spread_width_pct(back) < 0.01
    )
    deep_oi = (
        front.open_interest is not None
        and back.open_interest is not None
        and front.open_interest > 500
        and back.open_interest > 500
    )
    no_quad_witching = (
        front_expiry not in quad_witching_dates and back_expiry not in quad_witching_dates
    )
    vega_within_budget = abs(weighted_vega_worst_case) <= abs(raw_vega)

    return sum(
        (
            slope_z_favourable,
            tight_spread,
            deep_oi,
            no_quad_witching,
            vega_within_budget,
        )
    )


def build_candidates(
    product: str,
    contracts: Sequence[OptionContract],
    expiries: Sequence[Expiry],
    spot: float,
    multiplier: int,
    style: Literal["european", "american"],
    r: float,
    q: float,
    expiry_cfg: ExpirySelectionConfig,
    strikes_cfg: StrikesConfig,
    gates_cfg: GatesConfig,
    wvega_alphas: Sequence[float],
    stress_alpha: float,
    rv10: float,
    nav: float,
    slope_z_favourable: bool,
    quad_witching_dates: Collection[date] = (),
    ex_div_dates: Collection[date] = (),
    as_of: date | None = None,
) -> list[Candidate]:
    as_of = as_of or date.today()
    candidates: list[Candidate] = []

    for front_exp, back_exp in enumerate_expiry_pairs(expiries, expiry_cfg):
        front_contracts = [c for c in contracts if c.expiry == front_exp.date]
        back_contracts = [c for c in contracts if c.expiry == back_exp.date]
        if not front_contracts or not back_contracts:
            continue

        try:
            seed_iv = surface.atm_iv_for_expiry(front_contracts, spot, r, q).atm_iv
        except ValueError:
            continue

        t1, t2 = front_exp.dte / 365, back_exp.dte / 365
        ex_div_in_window = any(as_of <= d <= front_exp.date for d in ex_div_dates)
        european_or_no_ex_div = style == "european" or not ex_div_in_window

        for side in strikes_cfg.sides:
            front_by_strike = _by_strike(front_contracts, side)
            back_by_strike = _by_strike(back_contracts, side)
            if not front_by_strike or not back_by_strike:
                continue

            for target_delta_pct in strikes_cfg.target_deltas:
                delta = target_delta_pct / 100 + skew_delta_adjust(side)
                target_strike = strike_from_delta(spot, seed_iv, front_exp.dte, delta, side)
                front = _closest_contract(front_by_strike, target_strike)
                back = _closest_contract(back_by_strike, target_strike)
                if front is None or back is None or front.strike != back.strike:
                    continue
                if front.iv is None or back.iv is None:
                    continue
                if None in (front.vega, back.vega):
                    continue

                fv = fwd_vol(front.iv, t1, back.iv, t2)
                fv_var = fwd_var(front.iv, t1, back.iv, t2)
                debit = back.mid - front.mid
                greeks = calendar_greeks(front, back, multiplier, contracts=1, spot=spot)
                wvega = {
                    alpha: weighted_vega(front.vega, back.vega, t1, t2, alpha)  # type: ignore[arg-type]
                    for alpha in wvega_alphas
                }
                weighted_vega_worst_case = weighted_vega(
                    front.vega, back.vega, t1, t2, stress_alpha  # type: ignore[arg-type]
                )

                gates = evaluate_gates(
                    front,
                    back,
                    fv_var,
                    rv10,
                    front.iv,
                    debit,
                    multiplier,
                    nav,
                    gates_cfg,
                    european_or_no_ex_div,
                )
                score = soft_score(
                    slope_z_favourable=slope_z_favourable,
                    front=front,
                    back=back,
                    front_expiry=front_exp.date,
                    back_expiry=back_exp.date,
                    raw_vega=greeks.raw_vega,
                    weighted_vega_worst_case=weighted_vega_worst_case,
                    quad_witching_dates=quad_witching_dates,
                )

                candidates.append(
                    Candidate(
                        product=product,
                        side=side,
                        strike=front.strike,
                        front=front,
                        back=back,
                        ratio=back_exp.dte / front_exp.dte,
                        fwd_vol=fv,
                        debit_mid=debit,
                        greeks=greeks,
                        wvega=wvega,
                        gates=gates,
                        soft_score=score,
                    )
                )

    return candidates


def rank_candidates(
    candidates: Sequence[Candidate], playbook: str, slope_z: float
) -> list[Candidate]:
    """Tradeable first, then by soft score, tiebreak by lower forward vol (A) / higher
    slope_z (B) — slope_z is constant across one product's candidates in a snapshot, so for
    playbook B this tiebreak is a no-op by construction; kept faithful to build plan §5.7."""

    def key(c: Candidate) -> tuple[bool, int, float]:
        tiebreak = c.fwd_vol if playbook == "A" else -slope_z
        return (not c.tradeable, -c.soft_score, tiebreak)

    return sorted(candidates, key=key)
