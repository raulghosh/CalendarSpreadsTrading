import math
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calscan.app.data import load_fixture_chain, load_market_snapshot
from calscan.domain.models import Chain
from calscan.events import EventsCalendar, load_events
from calscan.jobs.snapshot import (
    ET,
    SnapshotReport,
    Sources,
    alert_sender,
    latest_expected_run,
    run_snapshot,
    snapshot_is_stale,
)
from calscan.settings import AppConfig, Secrets
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory
from calscan.store.schema import (
    AtmCurve,
    Base,
    Candidate,
    PositionMark,
    SnapshotRun,
    SpyDaily,
    TermStructure,
    VixFamilyDaily,
)

CFG = AppConfig.load()
NO_EVENTS = EventsCalendar(events=())
NOW = datetime(2026, 9, 9, 14, 0, tzinfo=UTC)  # Wed 10:00 ET, the fixtures' market date
LAST_CLOSE = date(2026, 9, 8)


def _business_days(end: date, n: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _vix_csv(symbol: str) -> str:
    level = {"VIX": 15.0, "VIX9D": 14.5, "VIX3M": 17.5, "VIX6M": 18.5}[symbol]
    wave = math.cos if symbol == "VIX3M" else math.sin  # so VIX3M − VIX (slope_z) varies
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
    for i, d in enumerate(_business_days(LAST_CLOSE, 300)):
        v = level + 0.5 * wave(i / 7)
        lines.append(f"{d:%m/%d/%Y},{v},{v},{v},{v}")
    return "\n".join(lines)


def _spy_bars() -> list[dict[str, object]]:
    price, rows = 590.0, []
    for i, d in enumerate(_business_days(LAST_CLOSE, 60)):
        price *= 1.003 if i % 2 else 0.997  # ±0.3%/day → RV10 ≈ 5%, below the fixtures' 14% IV
        rows.append({"date": d, "open": price, "high": price, "low": price, "close": price,
                     "volume": 1_000_000})
    return rows


def _fixture_chain(product: str) -> Chain:
    chain = load_fixture_chain(product)
    assert chain is not None
    return chain


def _sources(**overrides: object) -> Sources:
    base: dict[str, object] = dict(
        vix_csv=_vix_csv,
        vix_live=lambda: {"vix": 15.2, "vix9d": 14.8, "vix3m": 17.6, "vix6m": 18.6},
        spy_bars=_spy_bars,
        chain=_fixture_chain,
    )
    base.update(overrides)
    return Sources(**base)  # type: ignore[arg-type]


class Recorder:
    def __init__(self) -> None:
        self.sent: list[list[str]] = []

    def __call__(self, alerts: list[str]) -> str:
        self.sent.append(alerts)
        return f"{len(alerts)} recorded"


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    with get_session_factory(get_engine(tmp_path / "test.db"))() as s:
        yield s


def _counts(session: Session) -> dict[str, int]:
    models: list[type[Base]] = [VixFamilyDaily, SpyDaily, AtmCurve, TermStructure, Candidate,
                                PositionMark, SnapshotRun]
    return {
        m.__tablename__: session.scalar(select(func.count()).select_from(m)) or 0 for m in models
    }


def _run(
    session: Session, now: datetime = NOW, notify: Recorder | None = None,
    **source_overrides: object,
) -> SnapshotReport:
    return run_snapshot(
        session, CFG, _sources(**source_overrides), NO_EVENTS, notify or Recorder(), now=now
    )


def test_two_runs_in_the_same_minute_do_not_duplicate_rows(session: Session) -> None:
    first = _run(session)
    after_first = _counts(session)
    second = _run(session)
    assert first.ok and second.ok, [s for s in second.steps if not s.ok]
    assert _counts(session) == after_first  # build plan Phase 5 acceptance


def test_later_run_adds_a_snapshot_but_not_daily_rows(session: Session) -> None:
    _run(session)
    before = _counts(session)
    _run(session, now=NOW + timedelta(hours=3))
    after = _counts(session)
    assert after["vix_family_daily"] == before["vix_family_daily"]
    assert after["spy_daily"] == before["spy_daily"]
    assert after["atm_curve"] == 2 * before["atm_curve"]
    assert after["term_structure"] == 2 * before["term_structure"]
    assert after["snapshot_runs"] == 2


def test_run_stores_curves_term_structure_and_ranked_candidates(session: Session) -> None:
    report = _run(session)
    assert report.ok, [s for s in report.steps if not s.ok]

    term = repo.get_latest_term_structure(session)
    assert set(term) == {"SPX", "XSP", "SPY"}
    assert term["SPX"]["spot"] == 6000.0 and term["XSP"]["spot"] == 600.0
    assert term["SPX"]["fwd_vol_30_60"] is not None  # fixture curve brackets 30 and 60 DTE

    ts, xsp = repo.get_candidates(session, "XSP")
    assert ts == report.ts
    assert [r["rank"] for r in xsp] == list(range(1, len(xsp) + 1))
    assert any(all(r["gates_passed"].values()) for r in xsp)  # XSP clears every gate
    _, spy = repo.get_candidates(session, "SPY")
    assert spy and all(r["oi1"] is None for r in spy)  # stored despite Alpaca's missing OI


@pytest.mark.parametrize("scale", [20, 100])  # e.g. per-contract prices read as per-share
def test_implausible_iv_rejects_the_chain_and_stores_nothing_for_it(
    session: Session, scale: float
) -> None:
    def quotes_off(product: str) -> Chain:  # a units slip in the feed's prices
        chain = _fixture_chain(product)
        if product != "XSP":
            return chain
        return replace(chain, contracts=tuple(
            replace(c, bid=c.bid * scale, ask=c.ask * scale) for c in chain.contracts
        ))

    report = _run(session, chain=quotes_off)
    xsp_step = next(s for s in report.steps if s.name == "chain:XSP")
    assert not xsp_step.ok and "sanity range" in xsp_step.detail
    assert not report.ok
    assert "XSP" not in repo.get_latest_atm_curves(session)
    assert {"SPX", "SPY"} <= set(repo.get_latest_atm_curves(session))
    assert repo.get_latest_snapshot_run(session) is not None  # the failure itself is recorded


def test_a_failing_source_is_isolated(session: Session) -> None:
    def broken() -> list[dict[str, object]]:
        raise ConnectionError("alpaca down")

    report = _run(session, spy_bars=broken)
    spy_step = next(s for s in report.steps if s.name == "spy_daily")
    assert not spy_step.ok and "alpaca down" in spy_step.detail
    assert all(s.ok for s in report.steps if s.name.startswith("chain:"))
    stored = repo.get_latest_snapshot_run(session)
    assert stored is not None and stored["ok"] is False


def test_live_vix_row_is_not_written_on_a_non_trading_day(session: Session) -> None:
    saturday = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    report = _run(session, now=saturday)
    step = next(s for s in report.steps if s.name == "vix_live")
    assert step.ok and "not a trading day" in step.detail
    assert date(2026, 9, 12) not in {d.date() for d in repo.get_vix_family_daily(session).index}


def test_alerts_for_new_tradeable_candidates_fire_once(session: Session) -> None:
    first, second = Recorder(), Recorder()
    _run(session, notify=first)
    assert any(a.startswith("New tradeable XSP") for a in first.sent[0])
    _run(session, now=NOW + timedelta(hours=3), notify=second)
    assert not any(a.startswith("New tradeable") for batch in second.sent for a in batch)


def test_alert_on_regime_change(session: Session) -> None:
    repo.upsert_term_structure(session, {
        "snapshot_ts": datetime(2026, 9, 8, 19, 30), "product": "SPX", "spot": 6000.0,
        "regime": "BACKWARDATION", "playbook": "B",
    })
    notify = Recorder()
    _run(session, notify=notify)
    assert any(a.startswith("Regime change: BACKWARDATION →") for a in notify.sent[0])


def test_open_positions_are_marked_and_non_hold_verdicts_alert(session: Session) -> None:
    position_id = repo.insert_position(session, {
        "product": "XSP", "side": "call", "strike": 600.0,
        "front_expiry": date(2026, 10, 9), "back_expiry": date(2026, 11, 13), "contracts": 1,
        "debit_paid": 5.0,  # fixture mark ≈ 6.88 → +37% ≥ playbook A's 25% target
        "iv1_entry": 0.14, "iv2_entry": 0.155, "sigma_entry_pts": 24.0, "playbook": "A",
    })
    notify = Recorder()
    report = _run(session, notify=notify)
    marks = repo.get_position_marks(session, position_id)
    assert len(marks) == 1 and marks[0]["ts"] == report.ts
    assert marks[0]["verdict"] == "CLOSE_OR_SCALE"
    assert f"Position #{position_id}: CLOSE_OR_SCALE — target hit" in notify.sent[0]


def test_alert_sender_respects_config() -> None:
    def cfg_with(**alerts: object) -> AppConfig:
        return CFG.model_copy(update={"alerts": CFG.alerts.model_copy(update=alerts)})

    assert "not sent" in alert_sender(cfg_with(enabled=False), Secrets())(["x"])
    with pytest.raises(ValueError, match="SLACK_WEBHOOK_URL"):
        alert_sender(cfg_with(enabled=True, channel="slack"), Secrets(slack_webhook_url=""))(["x"])
    with pytest.raises(NotImplementedError):
        alert_sender(cfg_with(enabled=True, channel="email"), Secrets())(["x"])


@pytest.mark.parametrize(
    ("now_et", "expected_et"),
    [
        (datetime(2026, 9, 22, 13, 0), datetime(2026, 9, 22, 12, 30)),  # Tue after the 12:30 slot
        (datetime(2026, 9, 21, 8, 0), datetime(2026, 9, 18, 15, 30)),  # Mon pre-open → Fri close
        (datetime(2026, 9, 26, 11, 0), datetime(2026, 9, 25, 15, 30)),  # Saturday → Friday
        (datetime(2026, 11, 26, 10, 0), datetime(2026, 11, 25, 15, 30)),  # Thanksgiving → Wed
    ],
)
def test_latest_expected_run(now_et: datetime, expected_et: datetime) -> None:
    expected = latest_expected_run(now_et.replace(tzinfo=ET), CFG.schedule.run_times_et,
                                   load_events())
    assert expected == expected_et.replace(tzinfo=ET)


def test_snapshot_is_stale_after_a_missed_slot_plus_grace() -> None:
    events = load_events()

    def utc(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
        return datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(UTC).replace(tzinfo=None)

    tuesday_1pm = datetime(2026, 9, 22, 13, 0, tzinfo=ET)
    assert snapshot_is_stale(utc(2026, 9, 22, 9, 45), tuesday_1pm, CFG, events)  # missed 12:30
    assert not snapshot_is_stale(utc(2026, 9, 22, 12, 31), tuesday_1pm, CFG, events)
    within_grace = datetime(2026, 9, 22, 12, 40, tzinfo=ET)
    assert not snapshot_is_stale(utc(2026, 9, 22, 9, 45), within_grace, CFG, events)
    assert snapshot_is_stale(None, tuesday_1pm, CFG, events)


def test_partial_cboe_refresh_keeps_the_stored_vix_and_the_regime_uses_complete_rows(
    session: Session,
) -> None:
    """2026-10-07 for real: CBOE had published VIX9D/VIX3M/VIX6M for the day but not VIX."""
    _run(session)  # history + today's live row
    today = NOW.date()
    live_vix = repo.get_vix_family_daily(session).loc[str(today), "vix"]

    repo.upsert_vix_family_daily(session, [{"date": today, "vix9d": 11.8, "vix3m": 17.7}])
    assert repo.get_vix_family_daily(session).loc[str(today), "vix"] == live_vix

    tomorrow = today + timedelta(days=1)  # a newest row still missing VIX
    repo.upsert_vix_family_daily(session, [{"date": tomorrow, "vix9d": 11.8, "vix3m": 17.7}])
    snapshot = load_market_snapshot(session, AppConfig.load())
    assert snapshot is not None
    assert snapshot.as_of == today  # last complete row, not None
    assert math.isfinite(snapshot.slope_z)
