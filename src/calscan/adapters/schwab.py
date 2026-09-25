"""Thin Schwab adapter. Read-only: only quote/chain/price-history calls are ever imported here —
no place_order/submit_order, by design (see build plan §0 hard constraint).

[VERIFY] on first live run: exact index symbols ($SPX/$XSP), whether $VIX9D/$VIX3M/$VIX6M quote,
and whether `volatility` in the chain payload is a percent (14.2) or a decimal (0.142).
"""

from __future__ import annotations

import calendar
import math
from datetime import UTC, date, datetime, timedelta
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
        token_path=str(secrets.schwab_token_file),
    )


def _num(value: Any) -> float | None:
    """Schwab/TDA emits "NaN" strings (and nulls) for greeks/IV on illiquid contracts."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


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

    On a 3rd-Friday SPX date the AM-settled monthly (root "SPX") and the PM-settled weekly
    (root "SPXW") share one expiry key. We keep the PM contract whenever both exist at the
    same (expiry, strike, side), and flag the expiry AM-settled only if it has no PM contracts
    at all — so a mixed expiry is still usable as a PM front leg via its SPXW contracts.

    [VERIFY] on first live run: exact field names below, and whether `volatility` is a
    percent (14.2, assumed here) or a decimal (0.142) — this is the classic TDA chain shape
    that Schwab's Trader API inherited, not yet confirmed against a live response.
    """
    underlying_price = float(raw["underlyingPrice"])
    by_key: dict[tuple[date, float, str], tuple[bool, OptionContract]] = {}
    expiry_has_pm: dict[date, bool] = {}
    expiry_dte: dict[date, int] = {}

    for side, exp_map_key in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
        side_typed: Side = side  # type: ignore[assignment]
        for exp_key, strikes in raw.get(exp_map_key, {}).items():
            expiry_date = date.fromisoformat(exp_key.split(":")[0])
            for contracts_at_strike in strikes.values():
                for c in contracts_at_strike:
                    dte = int(c["daysToExpiration"])
                    option_root = c.get("optionRoot", product.lstrip("$"))
                    am = _is_am_settled(expiry_date, option_root)
                    expiry_dte.setdefault(expiry_date, dte)
                    expiry_has_pm[expiry_date] = expiry_has_pm.get(expiry_date, False) or not am

                    volatility = _num(c.get("volatility"))
                    iv = volatility / 100 if volatility is not None and volatility > 0 else None
                    contract = OptionContract(
                        symbol=c["symbol"],
                        underlying_symbol=product,
                        strike=float(c["strikePrice"]),
                        side=side_typed,
                        expiry=expiry_date,
                        dte=dte,
                        bid=_num(c.get("bid")) or 0.0,
                        ask=_num(c.get("ask")) or 0.0,
                        iv=iv,
                        delta=_num(c.get("delta")),
                        gamma=_num(c.get("gamma")),
                        theta=_num(c.get("theta")),
                        vega=_num(c.get("vega")),
                        open_interest=(
                            int(oi) if (oi := _num(c.get("openInterest"))) is not None else None
                        ),
                        underlying_price=underlying_price,
                    )
                    key = (expiry_date, contract.strike, side)
                    existing = by_key.get(key)
                    if existing is None or (existing[0] and not am):  # PM replaces AM
                        by_key[key] = (am, contract)

    expiries = tuple(
        Expiry(date=d, dte=expiry_dte[d], am_settled=not expiry_has_pm[d])
        for d in sorted(expiry_dte)
    )
    return Chain(
        product=product,
        snapshot_ts=datetime.now(UTC),
        underlying_price=underlying_price,
        expiries=expiries,
        contracts=tuple(contract for _, contract in by_key.values()),
    )


def get_chain(client: Client, product: str, symbol: str, max_dte: int = 130) -> Chain:
    """`from_date`/`to_date` are the chain endpoint's actual date filter — `days_to_expiration`
    is an analytical-model input there, not a filter, so using it would pull every expiry."""
    today = date.today()
    resp = client.get_option_chain(
        symbol, from_date=today, to_date=today + timedelta(days=max_dte)
    )
    resp.raise_for_status()
    return parse_chain(resp.json(), product)
