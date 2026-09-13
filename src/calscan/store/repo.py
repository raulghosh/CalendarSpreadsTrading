"""Read/write helpers. Callers pass plain dicts/dataclasses in — no ORM objects leak upward."""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from calscan.store.schema import SpyDaily, VixFamilyDaily


def upsert_vix_family_daily(session: Session, rows: list[dict[str, object]]) -> None:
    """Each row: {date, vix?, vix9d?, vix3m?, vix6m?}. Missing keys leave existing values alone."""
    if not rows:
        return
    stmt = insert(VixFamilyDaily)
    update_cols = {
        col: stmt.excluded[col]
        for col in ("vix", "vix9d", "vix3m", "vix6m")
        if any(col in row for row in rows)
    }
    stmt = stmt.on_conflict_do_update(index_elements=["date"], set_=update_cols)
    session.execute(stmt, rows)
    session.commit()


def upsert_spy_daily(session: Session, rows: list[dict[str, object]]) -> None:
    """Each row: {date, open, high, low, close, volume}."""
    if not rows:
        return
    stmt = insert(SpyDaily)
    update_cols = {c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume")}
    stmt = stmt.on_conflict_do_update(index_elements=["date"], set_=update_cols)
    session.execute(stmt, rows)
    session.commit()


def get_vix_family_daily(
    session: Session, start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    query = "SELECT date, vix, vix9d, vix3m, vix6m FROM vix_family_daily"
    df = pd.read_sql(query, session.connection(), parse_dates=["date"])
    df = df.set_index("date").sort_index()
    if start is not None:
        df = df.loc[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df.loc[df.index <= pd.Timestamp(end)]
    return df


def get_spy_daily(
    session: Session, start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    query = "SELECT date, open, high, low, close, volume FROM spy_daily"
    df = pd.read_sql(query, session.connection(), parse_dates=["date"])
    df = df.set_index("date").sort_index()
    if start is not None:
        df = df.loc[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df.loc[df.index <= pd.Timestamp(end)]
    return df
