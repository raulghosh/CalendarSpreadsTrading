import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from calscan.adapters import alpaca, schwab
from calscan.domain.scenario import reprice_chain
from calscan.domain.surface import atm_iv_for_expiry

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


def test_schwab_is_am_settled_reads_field_then_falls_back_to_root_rule() -> None:
    third_friday, other_friday = date(2026, 9, 18), date(2026, 9, 11)
    assert schwab._is_am_settled(other_friday, {"settlementType": "A"}, "SPX") is True
    pm_on_spx_root = {"settlementType": "P", "optionRoot": "SPX"}
    assert schwab._is_am_settled(third_friday, pm_on_spx_root, "SPX") is False
    # no settlementType: a 3rd-Friday SPX-root monthly is AM, everything else PM
    assert schwab._is_am_settled(third_friday, {"optionRoot": "SPX"}, "SPX") is True
    assert schwab._is_am_settled(third_friday, {"optionRoot": "SPXW"}, "SPX") is False
    assert schwab._is_am_settled(other_friday, {"optionRoot": "SPX"}, "SPX") is False


def test_schwab_live_payload() -> None:
    """Trimmed real $SPX chain (close of 2026-10-07): 3rd Fridays carry both the AM SPX
    monthly and the PM SPXW contract at each strike."""
    raw = json.loads((FIXTURES / "schwab_spx_chain_live.json").read_text())
    chain = reprice_chain(schwab.parse_chain(raw, product="SPX"), r=0.043, q=0.013)

    by_date = {e.date: e for e in chain.expiries}
    third_fridays = (date(2026, 10, 16), date(2026, 11, 20))
    assert set(by_date) == {*third_fridays, date(2026, 11, 5), date(2026, 12, 31)}
    assert not any(e.am_settled for e in chain.expiries)  # every date has PM contracts
    assert all(c.symbol.startswith("SPXW") for c in chain.contracts if c.expiry in third_fridays)
    assert len(chain.contracts) == 32  # 4 expiries x 4 strikes x 2 sides, AM duplicates dropped

    for e in chain.expiries:
        point = atm_iv_for_expiry(chain.contracts_for(e.date), chain.underlying_price, 0.043, 0.013)
        assert 0.05 < point.atm_iv < 0.40


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status_code = status
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


class _SizeCappedClient:
    """One expiry per day; refuses any window wider than `max_days`, like Schwab's gateway."""

    def __init__(self, max_days: int) -> None:
        self.max_days = max_days
        self.calls = 0

    def get_option_chain(self, symbol: str, from_date: date, to_date: date) -> _FakeResponse:
        self.calls += 1
        if (to_date - from_date).days + 1 > self.max_days:
            fault = {"fault": {"detail": {"errorcode": "protocol.http.TooBigBody"}}}
            return _FakeResponse(502, fault)
        days = [from_date + timedelta(days=i) for i in range((to_date - from_date).days + 1)]
        exp_map: dict[str, Any] = {f"{d}:{(d - from_date).days}": {"100.0": []} for d in days}
        return _FakeResponse(
            200, {"underlyingPrice": 100.0, "callExpDateMap": exp_map, "putExpDateMap": exp_map}
        )


def test_fetch_chain_halves_the_window_until_each_piece_fits() -> None:
    client = _SizeCappedClient(max_days=10)
    start, end = date(2026, 10, 7), date(2026, 10, 7) + timedelta(days=120)
    raw = schwab._fetch_chain(client, "$SPX", start, end)

    assert len(raw["callExpDateMap"]) == 121  # every day, no gaps or overlaps
    assert len(raw["putExpDateMap"]) == 121
    assert raw["underlyingPrice"] == 100.0

    with pytest.raises(RuntimeError, match="502"):  # a single day that still overflows
        schwab._fetch_chain(_SizeCappedClient(max_days=0), "$SPX", start, start)


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
