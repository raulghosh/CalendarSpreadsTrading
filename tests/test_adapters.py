import json
from datetime import date
from pathlib import Path
from typing import Any

from calscan.adapters import alpaca, schwab

FIXTURES = Path(__file__).parent / "fixtures"


def test_schwab_parse_chain_spx() -> None:
    raw = json.loads((FIXTURES / "schwab_spx_chain.json").read_text())
    chain = schwab.parse_chain(raw, product="SPX")

    assert chain.underlying_price == 6000.0
    assert len(chain.contracts) == 44
    assert {e.dte for e in chain.expiries} == {30, 65}
    # weekly root (SPXW) fixture contracts are never AM-settled, even on our chosen Fridays
    assert all(not e.am_settled for e in chain.expiries)

    sample = next(c for c in chain.contracts if c.strike == 6000.0 and c.side == "call")
    assert sample.iv is not None and 0.10 < sample.iv < 0.20  # normalized from percent to decimal


def _schwab_contract(root: str, side: str, strike: float, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "putCall": side.upper(),
        "symbol": f"{root} 260918{side[0].upper()}{int(strike * 1000):08d}",
        "bid": 10.0,
        "ask": 10.2,
        "volatility": 15.0,
        "delta": 0.5 if side == "call" else -0.5,
        "gamma": 0.001,
        "theta": -1.0,
        "vega": 300.0,
        "openInterest": 1000,
        "strikePrice": strike,
        "expirationDate": "2026-09-18T00:00:00.000+00:00",
        "daysToExpiration": 5,
        "optionRoot": root,
    }
    base.update(overrides)
    return base


def test_schwab_parse_chain_prefers_pm_root_on_shared_third_friday_expiry() -> None:
    # 2026-09-18 is a 3rd Friday: AM-settled SPX monthly and PM-settled SPXW weekly share it
    raw: dict[str, Any] = {
        "underlyingPrice": 6000.0,
        "callExpDateMap": {
            "2026-09-18:5": {
                "6000.0": [
                    _schwab_contract("SPX", "call", 6000.0, bid=99.0, ask=99.5),
                    _schwab_contract("SPXW", "call", 6000.0, bid=10.0, ask=10.2),
                ]
            }
        },
        "putExpDateMap": {},
    }
    chain = schwab.parse_chain(raw, product="SPX")
    assert len(chain.contracts) == 1
    assert chain.contracts[0].bid == 10.0  # the SPXW (PM) contract survived
    assert chain.expiries[0].am_settled is False  # PM contracts exist -> usable front leg

    raw["callExpDateMap"]["2026-09-18:5"]["6000.0"] = [_schwab_contract("SPX", "call", 6000.0)]
    chain = schwab.parse_chain(raw, product="SPX")
    assert chain.expiries[0].am_settled is True  # AM-only expiry


def test_schwab_parse_chain_coerces_nan_strings_and_nulls() -> None:
    raw = {
        "underlyingPrice": 6000.0,
        "callExpDateMap": {
            "2026-10-09:26": {
                "6000.0": [
                    _schwab_contract(
                        "SPXW", "call", 6000.0, delta="NaN", vega=None, volatility="NaN"
                    )
                ]
            }
        },
        "putExpDateMap": {},
    }
    c = schwab.parse_chain(raw, product="SPX").contracts[0]
    assert c.delta is None
    assert c.vega is None
    assert c.iv is None
    assert c.gamma == 0.001  # untouched numerics pass through


def test_schwab_is_am_settled_heuristic() -> None:
    # a genuine 3rd-Friday SPX-root monthly should be flagged AM-settled
    assert schwab._is_am_settled(date(2026, 9, 18), "SPX") is True
    assert schwab._is_am_settled(date(2026, 9, 18), "SPXW") is False
    assert schwab._is_am_settled(date(2026, 9, 11), "SPX") is False  # not the 3rd Friday


def test_alpaca_parse_chain_spy() -> None:
    raw = json.loads((FIXTURES / "alpaca_spy_chain.json").read_text())
    from alpaca.data.models.snapshots import OptionsSnapshot

    snapshots = {
        symbol: OptionsSnapshot(symbol, data) for symbol, data in raw["snapshots"].items()
    }
    chain = alpaca.parse_chain(
        snapshots, product="SPY", underlying_price=raw["underlying_price"], as_of=date(2026, 9, 13)
    )

    assert chain.underlying_price == 600.0
    assert len(chain.contracts) == 44
    assert all(c.open_interest is None for c in chain.contracts)
    assert all(not e.am_settled for e in chain.expiries)

    sample = next(c for c in chain.contracts if c.strike == 600.0 and c.side == "put")
    assert sample.delta is not None and sample.delta < 0
