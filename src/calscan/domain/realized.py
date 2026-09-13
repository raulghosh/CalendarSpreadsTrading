"""Realized vol from daily closes. Build plan §5.3 — RV only in Phase 1; HAR-RV is Phase 6."""

from __future__ import annotations

import numpy as np
import pandas as pd


def log_returns(closes: pd.Series) -> pd.Series:
    ratio = closes / closes.shift(1)
    logged = pd.Series(np.log(ratio.to_numpy()), index=ratio.index)
    return logged.dropna()


def realized_vol(closes: pd.Series, n: int) -> float:
    """Zero-mean annualized RV over the trailing n days: sqrt(252 * mean(r^2))."""
    r = log_returns(closes).iloc[-n:]
    return float(np.sqrt(252 * (r**2).mean()))


def realized_vol_series(closes: pd.Series, n: int) -> pd.Series:
    r = log_returns(closes)
    result: pd.Series = np.sqrt(252 * (r**2).rolling(n).mean())
    return result
