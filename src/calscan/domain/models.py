"""Pure domain models. No network, no I/O — adapters translate raw API JSON into these."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Side = Literal["call", "put"]


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    price: float
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class OptionContract:
    symbol: str
    underlying_symbol: str
    strike: float
    side: Side
    expiry: date
    dte: int
    bid: float
    ask: float
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    open_interest: int | None  # Alpaca's data-only snapshot omits OI — see adapters/alpaca.py
    underlying_price: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class Expiry:
    date: date
    dte: int
    am_settled: bool


@dataclass(frozen=True, slots=True)
class Chain:
    product: str
    snapshot_ts: datetime
    underlying_price: float
    expiries: tuple[Expiry, ...]
    contracts: tuple[OptionContract, ...] = field(default_factory=tuple)

    def contracts_for(self, expiry: date) -> tuple[OptionContract, ...]:
        return tuple(c for c in self.contracts if c.expiry == expiry)


@dataclass(frozen=True, slots=True)
class ATMPoint:
    product: str
    expiry: date
    dte: int
    atm_iv: float
    fwd: float
    strike_lo: float
    strike_hi: float
    method: str


@dataclass(frozen=True, slots=True)
class Calendar:
    product: str
    side: Side
    strike: float
    front: OptionContract
    back: OptionContract
