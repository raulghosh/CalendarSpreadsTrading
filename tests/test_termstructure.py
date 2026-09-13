import math

import pandas as pd
import pytest

from calscan.domain.termstructure import (
    fwd_vol,
    interpolate_atm_iv_at_dte,
    ivts,
    near_slope,
    norm_slope,
    slope,
    slope_z_series,
    slope_z_top_threshold,
)


def test_fwd_vol_worked_example() -> None:
    # build plan §5.2 worked test
    result = fwd_vol(sigma1=0.14, t1=30 / 365, sigma2=0.16, t2=60 / 365)
    assert math.isclose(result, 0.1778, abs_tol=0.0005)


def test_fwd_vol_negative_forward_var_is_nan() -> None:
    # steeply inverted curve: back leg vol far below front leg vol
    result = fwd_vol(sigma1=0.30, t1=10 / 365, sigma2=0.14, t2=20 / 365)
    assert math.isnan(result)


def test_ivts_near_slope_slope_norm_slope() -> None:
    assert ivts(vix=18.0, vix3m=20.0) == 0.9
    assert math.isclose(near_slope(vix=18.0, vix9d=19.0), (18.0 - 19.0) / 19.0)
    assert slope(sigma1=0.14, sigma2=0.16) == pytest.approx(0.02)
    ns = norm_slope(sigma1=0.14, t1=30 / 365, sigma2=0.16, t2=60 / 365)
    assert ns > 0


def test_interpolate_atm_iv_at_dte_between_two_points() -> None:
    # 30d at 0.14 and 60d at 0.16 should reproduce the fwd_vol worked example when queried at 60.
    points = [(30, 0.14), (60, 0.16)]
    assert interpolate_atm_iv_at_dte(points, 30) == 0.14
    assert interpolate_atm_iv_at_dte(points, 60) == 0.16
    mid = interpolate_atm_iv_at_dte(points, 45)
    assert 0.14 < mid < 0.16


def test_slope_z_series_flags_extremes() -> None:
    dates = pd.date_range("2024-01-01", periods=300, freq="D")
    vix = pd.Series([15.0] * 300, index=dates)
    # mildly oscillating proxy so the rolling window has real variance, then a spike on day 300
    wobble = [1.0 + 0.3 * math.sin(i) for i in range(299)]
    vix3m = pd.Series([15.0 + w for w in wobble] + [25.0], index=dates)
    z = slope_z_series(vix, vix3m, window=252)
    assert z.iloc[-1] > 2.0

    top = slope_z_top_threshold(z.dropna(), 0.90)
    assert top < z.iloc[-1]
