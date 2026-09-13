"""CBOE public daily-price CSV adapter (VIX-family history, fallback spot source).

URL pattern and CSV shape (`DATE,OPEN,HIGH,LOW,CLOSE`, dates as MM/DD/YYYY) confirmed live
2026-09-13 for VIX, VIX9D, VIX3M and VIX6M.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Literal

import requests

VixSymbol = Literal["VIX", "VIX9D", "VIX3M", "VIX6M"]

_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"


def fetch_csv(symbol: VixSymbol) -> str:
    resp = requests.get(_URL.format(symbol=symbol), timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_daily_closes(csv_text: str) -> list[tuple[date, float]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    return [
        (datetime.strptime(row["DATE"], "%m/%d/%Y").date(), float(row["CLOSE"]))
        for row in reader
    ]
