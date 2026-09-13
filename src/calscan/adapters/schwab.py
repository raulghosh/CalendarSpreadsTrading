"""Thin Schwab adapter. Read-only: only quote/chain/price-history calls are ever imported here —
no place_order/submit_order, by design (see build plan §0 hard constraint).

[VERIFY] on first live run: exact index symbols ($SPX/$XSP), whether $VIX9D/$VIX3M/$VIX6M quote,
and whether `volatility` in the chain payload is a percent (14.2) or a decimal (0.142).
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime
from typing import Any

from schwab.auth import easy_client
from schwab.client import Client

from calscan.domain.models import Chain, Expiry, OptionContract, Quote, Side
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


def _third_friday(year: int, month: int) -> date:
    fridays = [
        d
        for d in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, d).weekday() == 4
    ]
    return date(year, month, fridays[2])


def _is_am_settled(expiry: date, option_root: str) -> bool:
    """[VERIFY]: heuristic — standard monthly SPX (root "SPX", 3rd-Friday expiry) settles AM;
    weeklies (root "SPXW") and XSP/SPY settle PM. Confirm against the real settlementType
    field on first live pull; adapt this function, not the domain layer, if it's wrong."""
    return option_root == "SPX" and expiry == _third_friday(expiry.year, expiry.month)


def parse_chain(raw: dict[str, Any], product: str) -> Chain:
    """Translates a raw Schwab/TDA-format chain response into domain models.

    [VERIFY] on first live run: exact field names below, and whether `volatility` is a
    percent (14.2, assumed here) or a decimal (0.142) — this is the classic TDA chain shape
    that Schwab's Trader API inherited, not yet confirmed against a live response.
    """
    underlying_price = float(raw["underlyingPrice"])
    contracts: list[OptionContract] = []
    expiries: dict[date, Expiry] = {}

    for side, exp_map_key in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
        side_typed: Side = side  # type: ignore[assignment]
        for exp_key, strikes in raw.get(exp_map_key, {}).items():
            expiry_date = date.fromisoformat(exp_key.split(":")[0])
            for contracts_at_strike in strikes.values():
                for c in contracts_at_strike:
                    dte = int(c["daysToExpiration"])
                    option_root = c.get("optionRoot", product.lstrip("$"))
                    expiries.setdefault(
                        expiry_date,
                        Expiry(
                            date=expiry_date,
                            dte=dte,
                            am_settled=_is_am_settled(expiry_date, option_root),
                        ),
                    )
                    volatility = c.get("volatility")
                    iv = volatility / 100 if volatility is not None and volatility > 0 else None
                    contracts.append(
                        OptionContract(
                            symbol=c["symbol"],
                            underlying_symbol=product,
                            strike=float(c["strikePrice"]),
                            side=side_typed,
                            expiry=expiry_date,
                            dte=dte,
                            bid=c["bid"],
                            ask=c["ask"],
                            iv=iv,
                            delta=c.get("delta"),
                            gamma=c.get("gamma"),
                            theta=c.get("theta"),
                            vega=c.get("vega"),
                            open_interest=c.get("openInterest"),
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


def get_chain(client: Client, product: str, symbol: str, days_to_expiration: int = 130) -> Chain:
    resp = client.get_option_chain(symbol, days_to_expiration=days_to_expiration)
    resp.raise_for_status()
    return parse_chain(resp.json(), product)
