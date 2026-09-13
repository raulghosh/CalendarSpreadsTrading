import math

import numpy as np
import pandas as pd

from calscan.domain.realized import realized_vol


def test_realized_vol_constant_daily_return() -> None:
    # constant daily log return r => RV_n = |r| * sqrt(252)
    r = 0.01
    closes = pd.Series(100 * np.exp(np.arange(30) * r))
    result = realized_vol(closes, n=10)
    assert math.isclose(result, r * math.sqrt(252), rel_tol=1e-9)
