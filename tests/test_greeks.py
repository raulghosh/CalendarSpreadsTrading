import math
from datetime import date

import pytest

from calscan.domain.greeks import atm_approx_theta, atm_approx_vega, calendar_greeks, weighted_vega
from calscan.domain.models import OptionContract


def _contract(**overrides: object) -> OptionContract:
    base: dict[str, object] = dict(
        symbol="TEST",
        underlying_symbol="TEST",
        strike=100.0,
        side="call",
        expiry=date(2026, 1, 1),
        dte=30,
        bid=1.0,
        ask=1.1,
        iv=0.14,
        delta=0.5,
        gamma=0.01,
        theta=-0.05,
        vega=0.2,
        open_interest=1000,
        underlying_price=100.0,
    )
    base.update(overrides)
    return OptionContract(**base)  # type: ignore[arg-type]


def test_calendar_greek_ratios_with_atm_approximations() -> None:
    # build plan §5.5 worked test: T2 = 2*T1
    spot = 6000.0
    sigma = 0.14
    t1 = 30 / 365
    t2 = 2 * t1

    vega_front = atm_approx_vega(spot, t1)
    vega_back = atm_approx_vega(spot, t2)
    raw_vega = vega_back - vega_front
    assert raw_vega / vega_front == pytest.approx(0.414, abs=0.001)

    theta_front = atm_approx_theta(spot, sigma, t1)
    theta_back = atm_approx_theta(spot, sigma, t2)
    net_theta = theta_back - theta_front
    assert net_theta / abs(theta_front) == pytest.approx(0.293, abs=0.001)

    wv = weighted_vega(vega_front, vega_back, t1, t2, alpha=0.5)
    assert wv == pytest.approx(0.0, abs=1e-9)


def test_weighted_vega_scales_by_multiplier_and_contracts() -> None:
    unscaled = weighted_vega(1.0, 2.0, 30 / 365, 60 / 365, alpha=0.5)
    scaled = weighted_vega(1.0, 2.0, 30 / 365, 60 / 365, alpha=0.5, multiplier=100, contracts=3)
    assert scaled == pytest.approx(unscaled * 300)


def test_calendar_greeks_net_is_back_minus_front_scaled() -> None:
    front = _contract(delta=0.5, gamma=0.01, theta=-0.08, vega=0.20, iv=0.14, dte=30)
    back = _contract(delta=0.45, gamma=0.008, theta=-0.05, vega=0.28, dte=60)

    result = calendar_greeks(front, back, multiplier=100, contracts=2, spot=100.0)

    assert result.net_delta == pytest.approx((0.45 - 0.5) * 100 * 2)
    assert result.net_gamma == pytest.approx((0.008 - 0.01) * 100 * 2)
    assert result.net_theta == pytest.approx((-0.05 - -0.08) * 100 * 2)
    assert result.raw_vega == pytest.approx((0.28 - 0.20) * 100 * 2)
    assert result.daily_breakeven_pts == pytest.approx(100.0 * 0.14 / math.sqrt(252))
    assert result.dollar_gamma_per_1pct == pytest.approx(0.5 * result.net_gamma * 1.0**2)


def test_calendar_greeks_requires_greeks_on_both_legs() -> None:
    front = _contract(delta=None)
    back = _contract()
    with pytest.raises(ValueError):
        calendar_greeks(front, back, multiplier=100, contracts=1, spot=100.0)


def test_approx_disagreement() -> None:
    from calscan.domain.greeks import approx_disagreement

    assert approx_disagreement(1.1, 1.0) == pytest.approx(0.1)
    assert math.isinf(approx_disagreement(1.0, 0.0))
    assert approx_disagreement(0.0, 0.0) == 0.0
