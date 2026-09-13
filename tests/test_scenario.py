import math

import pytest

from calscan.domain.scenario import (
    black_scholes_price,
    default_horizon_days,
    implied_vol,
    scenario_grid,
)


def test_black_scholes_matches_hull_textbook_reference() -> None:
    # Hull, "Options, Futures and Other Derivatives": S=42, K=40, r=10%, sigma=20%, T=0.5, q=0
    price = black_scholes_price(spot=42, strike=40, t=0.5, r=0.10, q=0.0, sigma=0.20, side="call")
    assert price == pytest.approx(4.7594, abs=1e-4)


def test_put_call_parity() -> None:
    kwargs = dict(spot=100.0, strike=95.0, t=0.25, r=0.03, q=0.01, sigma=0.18)
    call = black_scholes_price(side="call", **kwargs)
    put = black_scholes_price(side="put", **kwargs)
    lhs = call - put
    rhs = 100.0 * math.exp(-0.01 * 0.25) - 95.0 * math.exp(-0.03 * 0.25)
    assert lhs == pytest.approx(rhs, abs=1e-8)


def test_black_scholes_at_expiry_is_intrinsic_value() -> None:
    assert black_scholes_price(105, 100, 0, 0.05, 0.0, 0.2, "call") == pytest.approx(5.0)
    assert black_scholes_price(95, 100, 0, 0.05, 0.0, 0.2, "call") == pytest.approx(0.0)
    assert black_scholes_price(95, 100, 0, 0.05, 0.0, 0.2, "put") == pytest.approx(5.0)


def test_implied_vol_round_trips_through_price() -> None:
    spot, strike, t, r, q = 6000.0, 6050.0, 30 / 365, 0.043, 0.013
    price = black_scholes_price(spot, strike, t, r, q, sigma=0.145, side="call")
    recovered = implied_vol(price, spot, strike, t, r, q, side="call")
    assert recovered == pytest.approx(0.145, abs=1e-4)


def test_default_horizon_days() -> None:
    assert default_horizon_days(30) == 7  # capped at 7
    assert default_horizon_days(5) == 4  # dte-1 when shorter than 7
    assert default_horizon_days(1) == 1  # floored at 1, never 0


def test_scenario_grid_shape_and_highlighted_cells() -> None:
    grid = scenario_grid(
        spot=6000,
        front_strike=6050,
        front_side="call",
        front_iv=0.14,
        front_dte=30,
        back_strike=6050,
        back_side="call",
        back_iv=0.155,
        back_dte=65,
        debit=65.0,
        r=0.043,
        q=0.013,
        spot_moves_sigma=[-2, -1, -0.5, 0, 0.5, 1, 2],
        iv_shifts_front=[-4, 0, 4, 10],
        iv_shifts_back=[-2, 0, 2, 5],
    )
    assert grid.horizon_days == 7
    assert len(grid.cells) == 7 * 4  # spot moves x paired shock scenarios

    assert grid.worst_cell.pnl_pct_of_debit == min(c.pnl_pct_of_debit for c in grid.cells)
    assert grid.inversion_cell.scenario.iv_shift_front == 10  # steepest front-vs-back shift
    assert grid.inversion_cell.scenario.iv_shift_back == 5
    assert grid.inversion_cell.spot_move_sigma == -1  # closest configured move to -1sigma


def test_scenario_grid_respects_explicit_horizon_and_dte_floor() -> None:
    grid = scenario_grid(
        spot=100,
        front_strike=100,
        front_side="put",
        front_iv=0.20,
        front_dte=10,
        back_strike=100,
        back_side="put",
        back_iv=0.22,
        back_dte=40,
        debit=2.0,
        r=0.02,
        q=0.0,
        spot_moves_sigma=[0],
        iv_shifts_front=[0],
        iv_shifts_back=[0],
        horizon_days=100,  # way beyond front_dte — must be clamped
    )
    assert grid.horizon_days == 9  # clamped to front_dte - 1
