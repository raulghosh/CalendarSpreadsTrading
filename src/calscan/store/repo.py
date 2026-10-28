"""Read/write helpers. Callers pass plain dicts/dataclasses in — no ORM objects leak upward."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from calscan.store.schema import (
    AtmCurve,
    Candidate,
    Journal,
    Position,
    PositionMark,
    SnapshotRun,
    SpyDaily,
    TermStructure,
    VixFamilyDaily,
)


def upsert_vix_family_daily(session: Session, rows: list[dict[str, object]]) -> None:
    """Each row: {date, vix?, vix9d?, vix3m?, vix6m?}. A missing or null value never overwrites a
    stored one: CBOE's per-index files publish at different times, so a refresh can carry
    VIX3M for a date whose VIX hasn't landed yet — that must not erase the live VIX stored
    for it."""
    if not rows:
        return
    stmt = insert(VixFamilyDaily)
    update_cols = {
        col: func.coalesce(stmt.excluded[col], getattr(VixFamilyDaily, col))
        for col in ("vix", "vix9d", "vix3m", "vix6m")
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


def upsert_atm_curve(session: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = insert(AtmCurve)
    stmt = stmt.on_conflict_do_update(
        index_elements=["snapshot_ts", "product", "expiry"],
        set_={
            c: stmt.excluded[c]
            for c in ("dte", "atm_iv", "fwd", "atm_strike_lo", "atm_strike_hi", "method")
        },
    )
    session.execute(stmt, rows)
    session.commit()


def upsert_term_structure(session: Session, row: dict[str, Any]) -> None:
    stmt = insert(TermStructure).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["snapshot_ts", "product"],
        set_={c: stmt.excluded[c] for c in row if c not in ("snapshot_ts", "product")},
    )
    session.execute(stmt)
    session.commit()


def replace_candidates(
    session: Session, snapshot_ts: datetime, product: str, rows: list[dict[str, Any]]
) -> None:
    """Delete-then-insert per (snapshot, product): re-running a snapshot never duplicates."""
    session.execute(
        delete(Candidate).where(
            Candidate.snapshot_ts == snapshot_ts, Candidate.product == product
        )
    )
    session.add_all(Candidate(**row) for row in rows)
    session.commit()


def upsert_snapshot_run(
    session: Session, ts: datetime, ok: bool, report: dict[str, Any]
) -> None:
    stmt = insert(SnapshotRun).values(ts=ts, ok=ok, report=report)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts"], set_={"ok": stmt.excluded.ok, "report": stmt.excluded.report}
    )
    session.execute(stmt)
    session.commit()


def get_latest_snapshot_run(session: Session) -> dict[str, Any] | None:
    run = session.scalars(select(SnapshotRun).order_by(SnapshotRun.ts.desc()).limit(1)).first()
    return _row_to_dict(run) if run else None


def get_latest_term_structure(
    session: Session, before: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """Most recent term-structure row per product (strictly before `before`, if given)."""
    stmt = select(TermStructure).order_by(TermStructure.snapshot_ts)
    if before is not None:
        stmt = stmt.where(TermStructure.snapshot_ts < before)
    latest: dict[str, dict[str, Any]] = {}
    for row in session.scalars(stmt).all():
        latest[row.product] = _row_to_dict(row)
    return latest


def get_term_structure_at(
    session: Session, snapshot_ts: datetime, product: str
) -> dict[str, Any] | None:
    row = session.get(TermStructure, (snapshot_ts, product))
    return _row_to_dict(row) if row else None


def get_candidates(
    session: Session, product: str, before: datetime | None = None
) -> tuple[datetime | None, list[dict[str, Any]]]:
    """The latest snapshot's candidates for a product (strictly before `before`, if given),
    in rank order."""
    latest = select(func.max(Candidate.snapshot_ts)).where(Candidate.product == product)
    if before is not None:
        latest = latest.where(Candidate.snapshot_ts < before)
    ts = session.scalar(latest)
    if ts is None:
        return None, []
    stmt = (
        select(Candidate)
        .where(Candidate.product == product, Candidate.snapshot_ts == ts)
        .order_by(Candidate.rank)
    )
    return ts, [_row_to_dict(c) for c in session.scalars(stmt).all()]


def get_latest_atm_curves(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Each product's ATM curve from its most recent snapshot, ordered by DTE."""
    latest_ts = (
        select(AtmCurve.product, func.max(AtmCurve.snapshot_ts).label("ts"))
        .group_by(AtmCurve.product)
        .subquery()
    )
    stmt = (
        select(AtmCurve)
        .join(
            latest_ts,
            (AtmCurve.product == latest_ts.c.product) & (AtmCurve.snapshot_ts == latest_ts.c.ts),
        )
        .order_by(AtmCurve.product, AtmCurve.dte)
    )
    curves: dict[str, list[dict[str, Any]]] = {}
    for row in session.scalars(stmt).all():
        curves.setdefault(row.product, []).append(_row_to_dict(row))
    return curves


def get_atm_curve_history(session: Session, product: str) -> pd.DataFrame:
    query = select(AtmCurve.snapshot_ts, AtmCurve.dte, AtmCurve.atm_iv).where(
        AtmCurve.product == product
    )
    return pd.read_sql(query, session.connection())


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
