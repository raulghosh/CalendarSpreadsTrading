"""Thin Alpaca adapter. Data only — this module never imports alpaca.trading (the client class
that exposes submit_order/replace_order/cancel_order), so there is no order-placement surface
to accidentally call, per build plan §0's hard constraint.

Confirmed live 2026-09-13: alpaca-py's OptionsSnapshot carries implied_volatility and greeks
directly — no BS inversion needed. One real gap: the data-only snapshot has no open_interest
field (that only exists on alpaca.trading's contract reference data, which we don't import for
the reason above) — SPY candidates' OI gate is treated as failing when it's unavailable
rather than pulling in a client with order-placement methods to get it.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.models.snapshots import OptionsSnapshot
from alpaca.data.requests import OptionChainRequest, StockLatestTradeRequest

from calscan.domain.models import Chain, Expiry, OptionContract, Quote, Side
from calscan.settings import Secrets

_OCC_RE = re.compile(
    r"^(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<side>[CP])(?P<strike>\d{8})$"
)


def get_client(secrets: Secrets) -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=secrets.alpaca_api_key, secret_key=secrets.alpaca_secret_key
    )


def get_option_client(secrets: Secrets) -> OptionHistoricalDataClient:
    return OptionHistoricalDataClient(
        api_key=secrets.alpaca_api_key, secret_key=secrets.alpaca_secret_key
    )


def get_quote(client: StockHistoricalDataClient, symbol: str) -> Quote:
    trade = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))[
        symbol
    ]
    return Quote(symbol=symbol, price=trade.price, timestamp=trade.timestamp)


def _parse_occ_symbol(symbol: str) -> tuple[date, Side, float]:
    m = _OCC_RE.match(symbol)
    if not m:
        raise ValueError(f"unrecognized OCC option symbol: {symbol}")
    expiry = date(2000 + int(m["yy"]), int(m["mm"]), int(m["dd"]))
    side: Side = "call" if m["side"] == "C" else "put"
    strike = int(m["strike"]) / 1000
    return expiry, side, strike


def parse_chain(
    snapshots: dict[str, OptionsSnapshot], product: str, underlying_price: float, as_of: date
) -> Chain:
    contracts: list[OptionContract] = []
    expiries: dict[date, Expiry] = {}

    for symbol, snap in snapshots.items():
        expiry, side, strike = _parse_occ_symbol(symbol)
        dte = (expiry - as_of).days
        expiries.setdefault(  # SPY is always PM-settled
            expiry, Expiry(date=expiry, dte=dte, am_settled=False)
        )

        quote = snap.latest_quote
        greeks = snap.greeks
        contracts.append(
            OptionContract(
                symbol=symbol,
                underlying_symbol=product,
                strike=strike,
                side=side,
                expiry=expiry,
                dte=dte,
                bid=quote.bid_price if quote else 0.0,
                ask=quote.ask_price if quote else 0.0,
                iv=snap.implied_volatility,
                delta=greeks.delta if greeks else None,
                gamma=greeks.gamma if greeks else None,
                theta=greeks.theta if greeks else None,
                vega=greeks.vega if greeks else None,
                open_interest=None,  # not exposed by the data-only snapshot — see module docstring
                underlying_price=underlying_price,
            )
        )

    return Chain(
        product=product,
        snapshot_ts=datetime.now(UTC),
        underlying_price=underlying_price,
        expiries=tuple(expiries.values()),
        contracts=tuple(contracts),
    )


def get_chain(
    option_client: OptionHistoricalDataClient,
    symbol: str,
    product: str,
    underlying_price: float,
    as_of: date | None = None,
) -> Chain:
    as_of = as_of or datetime.now(UTC).date()
    snapshots = option_client.get_option_chain(OptionChainRequest(underlying_symbol=symbol))
    return parse_chain(snapshots, product, underlying_price, as_of)
