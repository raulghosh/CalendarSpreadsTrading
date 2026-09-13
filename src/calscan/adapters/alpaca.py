"""Thin Alpaca adapter. Data only — this module never imports alpaca.trading, so there is no
order-placement surface to accidentally call (see build plan §0 hard constraint).

[VERIFY] on first live run: whether the options chain snapshot endpoint returns IV/Greeks or only
quotes — if only quotes, implied_vol() (own BS inversion, scenario.py) fills the gap.
"""

from __future__ import annotations

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

from calscan.domain.models import Quote
from calscan.settings import Secrets


def get_client(secrets: Secrets) -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=secrets.alpaca_api_key, secret_key=secrets.alpaca_secret_key
    )


def get_quote(client: StockHistoricalDataClient, symbol: str) -> Quote:
    trade = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))[
        symbol
    ]
    return Quote(symbol=symbol, price=trade.price, timestamp=trade.timestamp)
