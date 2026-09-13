"""Thin Schwab adapter. Read-only: only quote/chain/price-history calls are ever imported here —
no place_order/submit_order, by design (see build plan §0 hard constraint).

[VERIFY] on first live run: exact index symbols ($SPX/$XSP), whether $VIX9D/$VIX3M/$VIX6M quote,
and whether `volatility` in the chain payload is a percent (14.2) or a decimal (0.142).
"""

from __future__ import annotations

from datetime import UTC, datetime

from schwab.auth import easy_client
from schwab.client import Client

from calscan.domain.models import Quote
from calscan.settings import Secrets


def get_client(secrets: Secrets) -> Client:
    return easy_client(
        api_key=secrets.schwab_app_key,
        app_secret=secrets.schwab_app_secret,
        callback_url=secrets.schwab_callback_url,
        token_path=secrets.schwab_token_path,
    )


def get_quote(client: Client, symbol: str) -> Quote:
    resp = client.get_quote(symbol)
    resp.raise_for_status()
    payload = resp.json()[symbol]
    # [VERIFY] field name/shape for index quotes — confirmed for equities as below.
    price = payload["quote"]["lastPrice"]
    return Quote(symbol=symbol, price=price, timestamp=datetime.now(UTC))
