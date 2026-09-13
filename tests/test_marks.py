from datetime import date, datetime

import pytest

from calscan.domain.models import Chain, Expiry, OptionContract
from calscan.events import Event, EventsCalendar
from calscan.jobs.marks import mark_position
from calscan.settings import AppConfig

CFG = AppConfig.load()
FRONT_EXPIRY = date(2026, 10, 9)
BACK_EXPIRY = date(2026, 11, 13)
AS_OF = date(2026, 9, 20)
EMPTY_EVENTS = EventsCalendar(events=())


def _contract(
    expiry: date, dte: int, strike: float, side: str, bid: float, ask: float, **kw: object
) -> OptionContract:
    base: dict[str, object] = dict(
        symbol=f"TEST{expiry}{side}",
        underlying_symbol="SPX",
        strike=strike,
        side=side,
        expiry=expiry,
        dte=dte,
        bid=bid,
        ask=ask,
        iv=0.14,
        delta=0.5 if side == "call" else -0.5,
        gamma=0.001,
        theta=-1.0,
        vega=300.0,
        open_interest=1000,
        underlying_price=6000.0,
    )
    base.update(kw)
    return OptionContract(**base)  # type: ignore[arg-type]


def _chain(
    spot: float, front_bid_ask: tuple[float, float], back_bid_ask: tuple[float, float]
) -> Chain:
    return Chain(
        product="SPX",
        snapshot_ts=datetime(2026, 9, 20),
        underlying_price=spot,
        expiries=(
            Expiry(date=FRONT_EXPIRY, dte=19, am_settled=False),
            Expiry(date=BACK_EXPIRY, dte=54, am_settled=False),
        ),
        contracts=(
            _contract(FRONT_EXPIRY, 19, 6000.0, "call", *front_bid_ask),
            _contract(BACK_EXPIRY, 54, 6000.0, "call", *back_bid_ask),
        ),
    )


def _position(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        id=1,
        product="SPX",
        side="call",
        strike=6000.0,
        front_expiry=FRONT_EXPIRY,
        back_expiry=BACK_EXPIRY,
        contracts=1,
        debit_paid=65.0,
        sigma_entry_pts=240.0,
        playbook="A",
    )
    base.update(overrides)
    return base


def test_mark_position_computes_pnl_and_verdict() -> None:
    chain = _chain(spot=6000.0, front_bid_ask=(59.0, 60.0), back_bid_ask=(129.0, 130.0))
    result = mark_position(
        _position(), chain, ivts_now=0.85, events=EMPTY_EVENTS, cfg=CFG, as_of=AS_OF
    )
    assert result["mark_mid"] == pytest.approx(129.5 - 59.5)
    assert result["pnl_pct"] == pytest.approx((70.0 - 65.0) / 65.0)
    assert result["dist_sigma"] == pytest.approx(0.0)
    assert result["dte1"] == 19
    assert result["verdict"] == "HOLD"


def test_mark_position_time_stop_when_front_dte_at_floor() -> None:
    chain = _chain(spot=6000.0, front_bid_ask=(59.0, 60.0), back_bid_ask=(129.0, 130.0))
    position = _position(front_expiry=date(2026, 9, 29))  # dte from as_of=2026-09-20 is 9 <= 10
    contracts = (
        _contract(date(2026, 9, 29), 9, 6000.0, "call", 20.0, 21.0),
        chain.contracts[1],
    )
    chain = Chain(
        product="SPX",
        snapshot_ts=chain.snapshot_ts,
        underlying_price=chain.underlying_price,
        expiries=(Expiry(date(2026, 9, 29), 9, False), chain.expiries[1]),
        contracts=contracts,
    )
    result = mark_position(
        position, chain, ivts_now=0.85, events=EMPTY_EVENTS, cfg=CFG, as_of=AS_OF
    )
    assert result["verdict"] == "CLOSE"
    assert result["verdict_reason"] == "time stop"


def test_mark_position_event_in_window_triggers_wing_or_reduce() -> None:
    chain = _chain(spot=6000.0, front_bid_ask=(59.0, 60.0), back_bid_ask=(129.0, 130.0))
    events = EventsCalendar(events=(Event(date=date(2026, 9, 22), category="cpi", label="CPI"),))
    result = mark_position(_position(), chain, ivts_now=0.85, events=events, cfg=CFG, as_of=AS_OF)
    assert result["verdict"] == "WING_OR_REDUCE"


def test_mark_position_raises_when_chain_missing_a_leg() -> None:
    chain = _chain(spot=6000.0, front_bid_ask=(59.0, 60.0), back_bid_ask=(129.0, 130.0))
    with pytest.raises(ValueError):
        mark_position(
            _position(strike=9999.0),
            chain,
            ivts_now=0.85,
            events=EMPTY_EVENTS,
            cfg=CFG,
            as_of=AS_OF,
        )
