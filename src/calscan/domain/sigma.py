"""Sigma-in-points and delta<->d conversions (flat-vol approximation). Build plan §5.4.

Pulled into Phase 2 because scanner.py's strike selection depends on strike_from_delta.
Phase 3 adds scenario.py (the BS pricer) and the Sigma Calculator / Scenario Grid pages.
"""

from __future__ import annotations

import math
from typing import Literal

from scipy.stats import norm

from calscan.domain.models import Side


def sigma_pts(spot: float, iv: float, dte: float) -> float:
    return spot * iv * math.sqrt(dte / 365)


def daily_breakeven_pts(spot: float, iv: float) -> float:
    """S · σ / √252 — build plan §5.5, also surfaced standalone on the Sigma Calculator page."""
    return spot * iv / math.sqrt(252)


def d_from_strike(strike: float, spot: float, iv: float, dte: float) -> float:
    return (strike - spot) / sigma_pts(spot, iv, dte)


def delta_from_d(d: float) -> float:
    """Call-side flat-vol-approximation delta. Put delta = call delta − 1."""
    return float(1 - norm.cdf(d))


def d_from_delta(delta: float) -> float:
    return float(norm.ppf(1 - delta))


def skew_delta_adjust(side: Side) -> float:
    """Rough, configurable correction — see build plan §5.4."""
    return -0.035 if side == "put" else 0.015


def strike_from_delta(spot: float, iv: float, dte: float, delta: float, side: Side) -> float:
    offset = d_from_delta(delta) * sigma_pts(spot, iv, dte)
    return spot + offset if side == "call" else spot - offset


ExpectedMoveConvention = Literal["iv_based", "straddle_based", "unknown"]


def expected_move_convention_check(
    platform_em: float, spot: float, iv: float, dte: float
) -> ExpectedMoveConvention:
    ratio = platform_em / sigma_pts(spot, iv, dte)
    if 0.9 < ratio < 1.1:
        return "iv_based"
    if 0.72 < ratio < 0.88:
        return "straddle_based"
    return "unknown"
