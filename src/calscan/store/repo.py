"""Read/write helpers. Callers pass plain dicts/dataclasses in — no ORM objects leak upward."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from calscan.store.schema import Journal, Position, PositionMark, SpyDaily, VixFamilyDaily


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


def insert_position(session: Session, fields: dict[str, Any]) -> int:
    """`fields` matches the `positions` table (see store/schema.py); `status` defaults to
    "open" and `opened_ts` to now if omitted."""
    row = {"status": "open", "opened_ts": datetime.utcnow(), **fields}
    position = Position(**row)
    session.add(position)
    session.commit()
    session.refresh(position)
    return position.id


def get_positions(session: Session, status: str | None = None) -> list[dict[str, Any]]:
    stmt = select(Position).order_by(Position.opened_ts.desc())
    if status is not None:
        stmt = stmt.where(Position.status == status)
    return [_row_to_dict(p) for p in session.scalars(stmt).all()]


def get_position(session: Session, position_id: int) -> dict[str, Any] | None:
    position = session.get(Position, position_id)
    return _row_to_dict(position) if position else None


def close_position(
    session: Session,
    position_id: int,
    closed_ts: datetime,
    exit_price: float,
    exit_reason: str,
    realized_pnl: float,
) -> None:
    position = session.get(Position, position_id)
    if position is None:
        raise ValueError(f"no position with id {position_id}")
    position.status = "closed"
    position.closed_ts = closed_ts
    position.exit_price = exit_price
    position.exit_reason = exit_reason
    position.realized_pnl = realized_pnl
    session.commit()


def record_hedge_pnl(session: Session, position_id: int, hedge_pnl: float) -> None:
    position = session.get(Position, position_id)
    if position is None:
        raise ValueError(f"no position with id {position_id}")
    position.hedge_pnl = (position.hedge_pnl or 0.0) + hedge_pnl
    session.commit()


def insert_position_mark(session: Session, fields: dict[str, Any]) -> None:
    """`fields` matches the `position_marks` table (ts, position_id, spot, mark_mid, pnl_pct,
    dist_sigma, dte1, ivts_now, verdict, verdict_reason)."""
    stmt = insert(PositionMark).values(**fields)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts", "position_id"],
        set_={c: stmt.excluded[c] for c in fields if c not in ("ts", "position_id")},
    )
    session.execute(stmt)
    session.commit()


def get_latest_marks(session: Session) -> dict[int, dict[str, Any]]:
    """Most recent mark per position_id."""
    stmt = select(PositionMark).order_by(PositionMark.position_id, PositionMark.ts)
    latest: dict[int, dict[str, Any]] = {}
    for mark in session.scalars(stmt).all():
        latest[mark.position_id] = _row_to_dict(mark)
    return latest


def get_position_marks(session: Session, position_id: int) -> list[dict[str, Any]]:
    stmt = (
        select(PositionMark)
        .where(PositionMark.position_id == position_id)
        .order_by(PositionMark.ts)
    )
    return [_row_to_dict(m) for m in session.scalars(stmt).all()]


def insert_journal_entry(
    session: Session,
    ts: datetime,
    event_type: str,
    payload: dict[str, Any],
    position_id: int | None = None,
) -> None:
    session.add(
        Journal(ts=ts, position_id=position_id, event_type=event_type, payload=payload)
    )
    session.commit()


def get_journal_entries(session: Session) -> list[dict[str, Any]]:
    stmt = select(Journal).order_by(Journal.ts.desc())
    return [_row_to_dict(j) for j in session.scalars(stmt).all()]


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


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
