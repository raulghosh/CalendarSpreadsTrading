import math

import pytest

from calscan.domain.sigma import (
    d_from_delta,
    delta_from_d,
    expected_move_convention_check,
    sigma_pts,
    strike_from_delta,
)


def test_sigma_pts_worked_example() -> None:
    assert sigma_pts(spot=6000, iv=0.14, dte=30) == pytest.approx(240.8, abs=0.5)


def test_delta_from_d_worked_examples() -> None:
    assert delta_from_d(0.5) == pytest.approx(0.3085, abs=0.0005)
    assert delta_from_d(1.0) == pytest.approx(0.1587, abs=0.0005)


def test_d_from_delta_worked_example() -> None:
    assert d_from_delta(0.25) == pytest.approx(0.6745, abs=0.0005)


def test_strike_from_delta_call_above_spot_put_below() -> None:
    call_strike = strike_from_delta(spot=6000, iv=0.14, dte=30, delta=0.25, side="call")
    put_strike = strike_from_delta(spot=6000, iv=0.14, dte=30, delta=0.25, side="put")
    assert call_strike > 6000
    assert put_strike < 6000
    assert math.isclose(call_strike - 6000, 6000 - put_strike)


def test_expected_move_convention_check() -> None:
    sigma = sigma_pts(spot=6000, iv=0.14, dte=30)
    assert expected_move_convention_check(sigma * 1.0, 6000, 0.14, 30) == "iv_based"
    assert expected_move_convention_check(sigma * 0.8, 6000, 0.14, 30) == "straddle_based"
    assert expected_move_convention_check(sigma * 0.5, 6000, 0.14, 30) == "unknown"
