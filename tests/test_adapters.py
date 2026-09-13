import json
from datetime import date
from pathlib import Path

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
