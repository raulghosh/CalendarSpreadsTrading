"""Term structure: IVTS, slope, forward vol, and slope z-score. Build plan §5.2."""

from __future__ import annotations

import math
from collections.abc import Sequence

import pandas as pd


def ivts(vix: float, vix3m: float) -> float:
    return vix / vix3m


def near_slope(vix: float, vix9d: float) -> float:
    return (vix - vix9d) / vix9d


def slope(sigma1: float, sigma2: float) -> float:
    return sigma2 - sigma1


def norm_slope(sigma1: float, t1: float, sigma2: float, t2: float) -> float:
    return (sigma2 - sigma1) / (math.sqrt(t2) - math.sqrt(t1))


def fwd_var(sigma1: float, t1: float, sigma2: float, t2: float) -> float:
    return (sigma2**2 * t2 - sigma1**2 * t1) / (t2 - t1)


def fwd_vol(sigma1: float, t1: float, sigma2: float, t2: float) -> float:
    variance = fwd_var(sigma1, t1, sigma2, t2)
    return math.sqrt(variance) if variance >= 0 else float("nan")


def slope_z_series(vix: pd.Series, vix3m: pd.Series, window: int = 252) -> pd.Series:
    """Daily z-score history of the CBOE slope proxy VIX3M − VIX."""
    proxy = vix3m - vix
    mean = proxy.rolling(window).mean()
    std = proxy.rolling(window).std()
    return (proxy - mean) / std


def slope_z_top_threshold(slope_z: pd.Series, pct: float) -> float:
    """z-score at the given historical percentile — "top decile of contango" per config."""
    return float(slope_z.quantile(pct))


def total_variance(atm_iv: float, dte: int) -> float:
    return atm_iv**2 * (dte / 365)


def interpolate_atm_iv_at_dte(points: Sequence[tuple[int, float]], target_dte: int) -> float:
    """Interpolate an ATM curve (dte, atm_iv) in total variance to an exact target DTE.

    `points` must contain at least two points bracketing `target_dte` (or be sorted so the
    nearest two straddle it); raises ValueError otherwise.
    """
    pts = sorted(points)
    if len(pts) < 2:
        raise ValueError("need at least two ATM curve points to interpolate")

    lo = max((p for p in pts if p[0] <= target_dte), key=lambda p: p[0], default=None)
    hi = min((p for p in pts if p[0] >= target_dte), key=lambda p: p[0], default=None)
    if lo is None or hi is None:
        raise ValueError(f"target_dte {target_dte} is outside the ATM curve's range")
    if lo[0] == hi[0]:
        return lo[1]

    tv_lo, tv_hi = total_variance(lo[1], lo[0]), total_variance(hi[1], hi[0])
    weight = (target_dte - lo[0]) / (hi[0] - lo[0])
    tv_target = tv_lo + weight * (tv_hi - tv_lo)
    return math.sqrt(tv_target / (target_dte / 365))


def product_30_60_slope(
    points: Sequence[tuple[int, float]],
) -> tuple[float, float, float, float]:
    """Returns (slope_30_60, norm_slope_30_60, fwd_vol_30_60, sigma_30) from a product ATM curve."""
    sigma1 = interpolate_atm_iv_at_dte(points, 30)
    sigma2 = interpolate_atm_iv_at_dte(points, 60)
    t1, t2 = 30 / 365, 60 / 365
    return (
        slope(sigma1, sigma2),
        norm_slope(sigma1, t1, sigma2, t2),
        fwd_vol(sigma1, t1, sigma2, t2),
        sigma1,
    )
