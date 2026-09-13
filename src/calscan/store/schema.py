"""SQLAlchemy 2.0 ORM tables — one-to-one with build plan §4. No business logic here."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class VixFamilyDaily(Base):
    __tablename__ = "vix_family_daily"

    date: Mapped[date] = mapped_column(primary_key=True)
    vix: Mapped[float | None]
    vix9d: Mapped[float | None]
    vix3m: Mapped[float | None]
    vix6m: Mapped[float | None]


class SpyDaily(Base):
    __tablename__ = "spy_daily"

    date: Mapped[date] = mapped_column(primary_key=True)
    open: Mapped[float]
    high: Mapped[float]
    low: Mapped[float]
    close: Mapped[float]
    volume: Mapped[int]


class AtmCurve(Base):
    __tablename__ = "atm_curve"

    snapshot_ts: Mapped[datetime] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(primary_key=True)
    expiry: Mapped[date] = mapped_column(primary_key=True)
    dte: Mapped[int]
    atm_iv: Mapped[float]
    fwd: Mapped[float]
    atm_strike_lo: Mapped[float]
    atm_strike_hi: Mapped[float]
    method: Mapped[str]


class TermStructure(Base):
    __tablename__ = "term_structure"

    snapshot_ts: Mapped[datetime] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(primary_key=True)
    spot: Mapped[float]
    ivts: Mapped[float | None]
    near_slope: Mapped[float | None]
    vix: Mapped[float | None]
    vix9d: Mapped[float | None]
    vix3m: Mapped[float | None]
    vix6m: Mapped[float | None]
    slope_30_60: Mapped[float | None]
    norm_slope_30_60: Mapped[float | None]
    fwd_vol_30_60: Mapped[float | None]
    slope_z: Mapped[float | None]
    rv10: Mapped[float | None]
    rv20: Mapped[float | None]
    har_fcst: Mapped[float | None]
    regime: Mapped[str]
    playbook: Mapped[str]
    notes: Mapped[str | None]


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_ts: Mapped[datetime]
    product: Mapped[str]
    side: Mapped[str]
    strike: Mapped[float]
    front_expiry: Mapped[date]
    back_expiry: Mapped[date]
    dte1: Mapped[int]
    dte2: Mapped[int]
    ratio: Mapped[float]
    iv1: Mapped[float]
    iv2: Mapped[float]
    fwd_vol: Mapped[float | None]
    debit_mid: Mapped[float]
    spread_width_pct: Mapped[float]
    oi1: Mapped[int]
    oi2: Mapped[int]
    net_delta: Mapped[float]
    net_gamma: Mapped[float]
    net_theta: Mapped[float]
    raw_vega: Mapped[float]
    wvega_050: Mapped[float]
    wvega_035: Mapped[float]
    wvega_070: Mapped[float]
    daily_breakeven_pts: Mapped[float]
    d_sigma: Mapped[float]
    delta_at_strike: Mapped[float]
    gates_passed: Mapped[dict[str, bool]] = mapped_column(JSON)
    soft_score: Mapped[float]
    rank: Mapped[int]
    playbook: Mapped[str]


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    opened_ts: Mapped[datetime]
    product: Mapped[str]
    side: Mapped[str]
    strike: Mapped[float]
    front_expiry: Mapped[date]
    back_expiry: Mapped[date]
    contracts: Mapped[int]
    debit_paid: Mapped[float]
    iv1_entry: Mapped[float]
    iv2_entry: Mapped[float]
    sigma_entry_pts: Mapped[float]
    playbook: Mapped[str]
    thesis: Mapped[str | None]
    status: Mapped[str]
    closed_ts: Mapped[datetime | None]
    exit_price: Mapped[float | None]
    exit_reason: Mapped[str | None]
    realized_pnl: Mapped[float | None]
    hedge_pnl: Mapped[float | None]


class PositionMark(Base):
    __tablename__ = "position_marks"

    ts: Mapped[datetime] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), primary_key=True
    )
    spot: Mapped[float]
    mark_mid: Mapped[float]
    pnl_pct: Mapped[float]
    dist_sigma: Mapped[float]
    dte1: Mapped[int]
    ivts_now: Mapped[float | None]
    verdict: Mapped[str]
    verdict_reason: Mapped[str]


class Journal(Base):
    __tablename__ = "journal"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime]
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    event_type: Mapped[str]
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
