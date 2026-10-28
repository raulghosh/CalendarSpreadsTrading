"""Thin Schwab adapter. Read-only: only quote/chain/price-history calls are ever imported here —
no place_order/submit_order, by design (see build plan §0 hard constraint).

Checked against the first live pull (2026-10-07): $SPX/$XSP and all four VIX-family symbols quote
(`quote.lastPrice`); chain `volatility` is a percent; each contract carries `settlementType`
("A"/"P"); greeks are per share with vega per vol point. The broker's IV and greeks are replaced
downstream by values implied from the quote mid — see domain.scenario.reprice_from_mid.
"""

from __future__ import annotations

import calendar
import json
import math
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from schwab.auth import client_from_login_flow, client_from_token_file
from schwab.client import Client

from calscan.domain.models import Chain, Expiry, OptionContract, Quote, Side
from calscan.settings import Secrets

VIX_FAMILY = {"$VIX": "vix", "$VIX9D": "vix9d", "$VIX3M": "vix3m", "$VIX6M": "vix6m"}
REFRESH_TOKEN_DAYS = 7  # [VERIFY] Schwab's refresh-token lifetime; re-login before it lapses


class SchwabAuthError(RuntimeError):
    pass


def login(secrets: Secrets) -> Client:
    """Interactive OAuth (opens a browser), always — so re-running it renews the 7-day refresh
    token. (`easy_client` would silently reuse any token younger than 6.5 days.) Run by hand via
    scripts/schwab_login.py — never from the job or the UI, where it would block on a browser."""
    return client_from_login_flow(
        api_key=secrets.schwab_app_key,
        app_secret=secrets.schwab_app_secret,
        callback_url=secrets.schwab_callback_url,
        token_path=str(secrets.schwab_token_file),
    )


def get_client(secrets: Secrets) -> Client:
    """Non-interactive: fails fast if there's no token, instead of starting a login flow."""
    if not secrets.schwab_token_file.exists():
        raise SchwabAuthError(
            "no Schwab token — run `uv run python scripts/schwab_login.py` once"
        )
    return client_from_token_file(
        str(secrets.schwab_token_file), secrets.schwab_app_key, secrets.schwab_app_secret
    )


def token_age_days(secrets: Secrets) -> float | None:
    """Age of the refresh token (Schwab expires it after ~7 days [VERIFY]); None if absent."""
    path = secrets.schwab_token_file
    if not path.exists():
        return None
    created = json.loads(path.read_text()).get("creation_timestamp")
    return None if created is None else (time.time() - float(created)) / 86400


def get_vix_family_quotes(client: Client) -> dict[str, float]:
    """Live VIX / VIX9D / VIX3M / VIX6M, keyed by vix_family_daily column. All four quoted on
    the first live pull; any the account can't quote would simply be absent."""
    resp = client.get_quotes(list(VIX_FAMILY))
    resp.raise_for_status()
    payload = resp.json()
    quotes: dict[str, float] = {}
    for symbol, column in VIX_FAMILY.items():
        price = _num(payload.get(symbol, {}).get("quote", {}).get("lastPrice"))
        if price is not None and price > 0:
            quotes[column] = price
    return quotes


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
    price = payload["quote"]["lastPrice"]  # same field for INDEX and EQUITY quotes
    return Quote(symbol=symbol, price=price, timestamp=datetime.now(UTC))


def _third_friday(year: int, month: int) -> date:
    fridays = [
        d
        for d in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, d).weekday() == 4
    ]
    return date(year, month, fridays[2])


def _is_am_settled(expiry: date, contract: dict[str, Any], product: str) -> bool:
    """Schwab's `settlementType` ("A" = AM, "P" = PM) when present. Without it, the classic
    rule: standard monthly SPX (root "SPX", 3rd Friday) settles AM; SPXW and XSP settle PM —
    which the first live pull matched exactly."""
    settlement = contract.get("settlementType")
    if settlement in ("A", "P"):
        return bool(settlement == "A")
    root = contract.get("optionRoot", product.lstrip("$"))
    return bool(root == "SPX" and expiry == _third_friday(expiry.year, expiry.month))


def parse_chain(raw: dict[str, Any], product: str) -> Chain:
    """Translates a raw Schwab/TDA-format chain response into domain models.

    On a 3rd-Friday SPX date the AM-settled monthly (root "SPX") and the PM-settled weekly
    (root "SPXW") share one expiry key. We keep the PM contract whenever both exist at the
    same (expiry, strike, side), and flag the expiry AM-settled only if it has no PM contracts
    at all — so a mixed expiry is still usable as a PM front leg via its SPXW contracts.
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
                    am = _is_am_settled(expiry_date, c, product)
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


def _fetch_chain(client: Client, symbol: str, start: date, end: date) -> dict[str, Any]:
    """Schwab's gateway rejects bodies over ~10 MB (HTTP 502 `TooBigBody`); a full SPX chain to
    120 DTE is several times that. Halve the date window until each piece fits, then merge."""
    resp = client.get_option_chain(symbol, from_date=start, to_date=end)
    if resp.status_code == 502 and b"TooBigBody" in resp.content and start < end:
        mid = start + timedelta(days=(end - start).days // 2)
        merged = _fetch_chain(client, symbol, start, mid)
        rest = _fetch_chain(client, symbol, mid + timedelta(days=1), end)
        for key in ("callExpDateMap", "putExpDateMap"):
            merged.setdefault(key, {}).update(rest.get(key, {}))
        return merged
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    return payload


def get_chain(client: Client, product: str, symbol: str, max_dte: int = 130) -> Chain:
    """`from_date`/`to_date` are the chain endpoint's actual date filter — `days_to_expiration`
    is an analytical-model input there, not a filter, so using it would pull every expiry."""
    today = date.today()
    raw = _fetch_chain(client, symbol, today, today + timedelta(days=max_dte))
    return parse_chain(raw, product)
