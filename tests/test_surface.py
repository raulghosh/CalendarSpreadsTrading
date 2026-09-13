from datetime import date

import pytest

from calscan.domain.models import OptionContract
from calscan.domain.surface import atm_iv_for_expiry, forward_price, is_usable_quote


def _contract(strike: float, side: str, iv: float | None, **overrides: object) -> OptionContract:
    base: dict[str, object] = dict(
        symbol=f"TEST{strike}{side}",
        underlying_symbol="TEST",
        strike=strike,
        side=side,
        expiry=date(2026, 1, 1),
        dte=30,
        bid=1.0,
        ask=1.05,
        iv=iv,
        delta=0.5 if side == "call" else -0.5,
        gamma=0.01,
        theta=-0.05,
        vega=0.2,
        open_interest=1000,
        underlying_price=100.0,
    )
    base.update(overrides)
    return OptionContract(**base)  # type: ignore[arg-type]


def test_forward_price() -> None:
    fwd = forward_price(spot=100, r=0.05, q=0.02, t=0.25)
    assert fwd == pytest.approx(100 * 2.718281828 ** (0.03 * 0.25), rel=1e-6)


def test_is_usable_quote_excludes_zero_bid_and_wide_spread() -> None:
    assert not is_usable_quote(_contract(100, "call", 0.14, bid=0.0, ask=1.0))
    assert not is_usable_quote(_contract(100, "call", 0.14, bid=1.0, ask=3.5))
    assert is_usable_quote(_contract(100, "call", 0.14, bid=1.0, ask=1.05))


def test_atm_iv_interpolates_between_bracketing_strikes() -> None:
    # forward ~= 100 (r=q=0), strikes at 95/100/105 bracket it exactly at 100
    contracts = [
        _contract(95, "call", 0.20),
        _contract(95, "put", 0.20),
        _contract(100, "call", 0.14),
        _contract(100, "put", 0.16),
        _contract(105, "call", 0.10),
        _contract(105, "put", 0.10),
    ]
    point = atm_iv_for_expiry(contracts, spot=100, r=0.0, q=0.0)
    assert point.strike_lo == 100
    assert point.strike_hi == 100
    assert point.atm_iv == pytest.approx(0.15)  # mean(0.14, 0.16)
    assert point.method == "exact_strike"


def test_atm_iv_falls_back_to_closest_delta_when_atm_strike_unusable() -> None:
    contracts = [
        _contract(95, "call", 0.20, delta=0.6),  # |0.6-0.5| = 0.1, closest to ATM
        _contract(100, "call", None, bid=0.0),  # unusable: bid == 0
        _contract(100, "put", None, bid=0.0),
        _contract(105, "call", 0.10, delta=0.3),  # |0.3-0.5| = 0.2
    ]
    point = atm_iv_for_expiry(contracts, spot=100, r=0.0, q=0.0)
    assert point.method == "delta_fallback"
    assert point.atm_iv == pytest.approx(0.20)
    assert point.strike_lo == point.strike_hi == 95
