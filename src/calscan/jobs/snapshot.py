"""Scheduled snapshot job — build plan §7.

One run = one `snapshot_ts` (UTC, truncated to the minute) shared by every row it writes.
Daily tables upsert by date and snapshot tables are keyed on snapshot_ts, so re-running the
same minute never duplicates rows. Each step is isolated: a failure is recorded in the run
report (stored in `snapshot_runs`, shown on Home) and later steps still run on whatever the
DB already holds.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.orm import Session

from calscan.adapters import alpaca as alpaca_adapter
from calscan.adapters import schwab as schwab_adapter
from calscan.adapters.cboe import VixSymbol, fetch_csv
from calscan.app.data import MarketSnapshot, candidate_rows, load_market_snapshot
from calscan.domain import surface, termstructure
from calscan.domain.models import ATMPoint, Chain
from calscan.domain.scenario import reprice_chain
from calscan.events import EventsCalendar, is_trading_day
from calscan.jobs.backfill import backfill_vix_family
from calscan.jobs.marks import mark_position
from calscan.settings import AppConfig, Secrets, Settings
from calscan.store import repo

ET = ZoneInfo("America/New_York")
REGIME_VIX_COLUMNS = {"vix", "vix9d", "vix3m"}  # load_market_snapshot needs all three


@dataclass(frozen=True)
class Sources:
    """Where a run's data comes from: live adapters in production, fixtures in tests."""

    vix_csv: Callable[[VixSymbol], str]
    vix_live: Callable[[], dict[str, float]]
    spy_bars: Callable[[], list[dict[str, object]]]
    chain: Callable[[str], Chain]


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str


@dataclass
class SnapshotReport:
    ts: datetime
    steps: list[StepResult] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def to_json(self) -> dict[str, Any]:
        return {"steps": [asdict(s) for s in self.steps], "alerts": self.alerts}


def run_snapshot(
    session: Session,
    cfg: AppConfig,
    sources: Sources,
    events: EventsCalendar,
    notify: Callable[[list[str]], str],
    now: datetime | None = None,
) -> SnapshotReport:
    now = now or datetime.now(UTC)
    ts = now.astimezone(UTC).replace(tzinfo=None, second=0, microsecond=0)
    today = now.astimezone(ET).date()
    report = SnapshotReport(ts=ts)
    chains: dict[str, Chain] = {}
    curves: dict[str, list[ATMPoint]] = {}
    rows: dict[str, list[dict[str, Any]]] = {}
    marks: list[dict[str, Any]] = []
    snapshot: MarketSnapshot | None = None

    def step(name: str, fn: Callable[[], str]) -> None:
        try:
            report.steps.append(StepResult(name, True, fn()))
        except Exception as exc:  # isolate: record it, keep going
            session.rollback()
            report.steps.append(StepResult(name, False, f"{type(exc).__name__}: {exc}"))

    def regime() -> str:
        nonlocal snapshot
        snapshot = load_market_snapshot(session, cfg, events)
        if snapshot is None:
            raise ValueError("no VIX-family history in the DB")
        return (
            f"{snapshot.regime.regime} → playbook {snapshot.regime.playbook} "
            f"(IVTS {snapshot.ivts:.3f}, data as of {snapshot.as_of})"
        )

    def alerts() -> str:
        report.alerts = _alerts(session, cfg, ts, snapshot, rows, marks)
        return notify(report.alerts) if report.alerts else "none"

    step("vix_history", lambda: f"{backfill_vix_family(session, fetch=sources.vix_csv)} "
         "daily rows refreshed from CBOE")
    step("vix_live", partial(_store_live_vix, session, sources.vix_live, today, events))
    step("spy_daily", partial(_store_spy, session, sources.spy_bars))
    for product in cfg.products:
        step(f"chain:{product}",
             partial(_store_chain, session, cfg, sources, product, ts, chains, curves))
    step("regime", regime)
    step("term_structure",
         lambda: _store_term_structure(session, ts, snapshot, chains, curves))
    step("candidates",
         lambda: _store_candidates(session, cfg, ts, snapshot, chains, events, today, rows))
    step("marks", lambda: _store_marks(session, cfg, ts, snapshot, chains, events, today, marks))
    step("alerts", alerts)

    repo.upsert_snapshot_run(session, ts, report.ok, report.to_json())
    return report


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None


def _store_live_vix(
    session: Session, fetch: Callable[[], dict[str, float]], today: date, events: EventsCalendar
) -> str:
    """Today's provisional row; the next CBOE refresh overwrites it with the official close."""
    if not is_trading_day(today, events):
        return f"skipped — {today} is not a trading day"
    quotes = fetch()
    missing = REGIME_VIX_COLUMNS - quotes.keys()
    if missing:
        raise ValueError(
            f"live quotes missing {sorted(missing)} — today's row not stored; the regime "
            "uses the last CBOE close"
        )
    repo.upsert_vix_family_daily(session, [{"date": today, **quotes}])
    return f"today's row from live quotes ({', '.join(sorted(quotes))})"


def _store_spy(session: Session, fetch: Callable[[], list[dict[str, object]]]) -> str:
    bars = fetch()
    if not bars:
        raise ValueError("no SPY bars returned")
    repo.upsert_spy_daily(session, bars)
    return f"{len(bars)} bars upserted, latest {max(b['date'] for b in bars)}"  # type: ignore[type-var]


def _store_chain(
    session: Session,
    cfg: AppConfig,
    sources: Sources,
    product: str,
    ts: datetime,
    chains: dict[str, Chain],
    curves: dict[str, list[ATMPoint]],
) -> str:
    chain = reprice_chain(sources.chain(product), cfg.rates.risk_free, cfg.rates.dividend_yield)
    lo, hi = cfg.snapshot.atm_iv_sanity
    points: list[ATMPoint] = []
    for exp in chain.expiries:
        if not 0 < exp.dte <= cfg.snapshot.max_dte:
            continue
        try:
            points.append(
                surface.atm_iv_for_expiry(
                    chain.contracts_for(exp.date),
                    chain.underlying_price,
                    cfg.rates.risk_free,
                    cfg.rates.dividend_yield,
                )
            )
        except ValueError:
            continue  # no usable quotes at this expiry
    if not points:
        raise ValueError(f"no expiry within {cfg.snapshot.max_dte} DTE produced an ATM IV")
    bad = next((p for p in points if not lo <= p.atm_iv <= hi), None)
    if bad is not None:
        raise ValueError(
            f"ATM IV {bad.atm_iv:.4f} at {bad.expiry} is outside the sanity range {lo}–{hi} — "
            f"likely a unit error in the feed; nothing stored for {product}"
        )
    repo.upsert_atm_curve(
        session,
        [
            {
                "snapshot_ts": ts,
                "product": product,
                "expiry": p.expiry,
                "dte": p.dte,
                "atm_iv": p.atm_iv,
                "fwd": p.fwd,
                "atm_strike_lo": p.strike_lo,
                "atm_strike_hi": p.strike_hi,
                "method": p.method,
            }
            for p in points
        ],
    )
    chains[product] = chain
    curves[product] = points
    with_oi = sum(1 for c in chain.contracts if c.open_interest)
    return (
        f"spot {chain.underlying_price:,.2f}, ATM IV at {len(points)} expiries, "
        f"open interest on {with_oi:,}/{len(chain.contracts):,} contracts"
    )


def _store_term_structure(
    session: Session,
    ts: datetime,
    snapshot: MarketSnapshot | None,
    chains: dict[str, Chain],
    curves: dict[str, list[ATMPoint]],
) -> str:
    if snapshot is None:
        return "skipped — no market regime this run (see the regime step)"
    if not chains:
        return "skipped — no chains this run"
    notes = "; ".join(filter(None, [snapshot.regime.note, *snapshot.regime.tags]))
    for product, chain in chains.items():
        slope: float | None
        norm_slope: float | None
        fwd_vol: float | None
        try:
            slope, norm_slope, fwd_vol, _ = termstructure.product_30_60_slope(
                [(p.dte, p.atm_iv) for p in curves[product]]
            )
        except ValueError:  # curve doesn't bracket both 30 and 60 DTE
            slope = norm_slope = fwd_vol = None
        repo.upsert_term_structure(
            session,
            {
                "snapshot_ts": ts,
                "product": product,
                "spot": chain.underlying_price,
                "ivts": _finite(snapshot.ivts),
                "near_slope": _finite(snapshot.near_slope),
                "vix": _finite(snapshot.vix),
                "vix9d": _finite(snapshot.vix9d),
                "vix3m": _finite(snapshot.vix3m),
                "vix6m": _finite(snapshot.vix6m),
                "slope_30_60": _finite(slope),
                "norm_slope_30_60": _finite(norm_slope),
                "fwd_vol_30_60": _finite(fwd_vol),
                "slope_z": _finite(snapshot.slope_z),
                "rv10": _finite(snapshot.rv10),
                "rv20": _finite(snapshot.rv20),
                "har_fcst": None,  # Phase 6
                "regime": snapshot.regime.regime,
                "playbook": snapshot.regime.playbook,
                "notes": notes or None,
            },
        )
    return f"{len(chains)} product row(s)"


def _store_candidates(
    session: Session,
    cfg: AppConfig,
    ts: datetime,
    snapshot: MarketSnapshot | None,
    chains: dict[str, Chain],
    events: EventsCalendar,
    today: date,
    rows: dict[str, list[dict[str, Any]]],
) -> str:
    # stored only alongside this snapshot's term-structure rows — pages read spot from there
    if snapshot is None:
        return "skipped — no market regime this run (see the regime step)"
    if not chains:
        return "skipped — no chains this run"
    summary = []
    for product, chain in chains.items():
        product_rows = candidate_rows(chain, product, cfg, snapshot, events, as_of=today)
        repo.replace_candidates(
            session, ts, product, [{**r, "snapshot_ts": ts} for r in product_rows]
        )
        rows[product] = product_rows
        tradeable = sum(all(r["gates_passed"].values()) for r in product_rows)
        summary.append(f"{product} {tradeable}/{len(product_rows)} tradeable")
    return ", ".join(summary)


def _store_marks(
    session: Session,
    cfg: AppConfig,
    ts: datetime,
    snapshot: MarketSnapshot | None,
    chains: dict[str, Chain],
    events: EventsCalendar,
    today: date,
    marks: list[dict[str, Any]],
) -> str:
    positions = repo.get_positions(session, status="open")
    if not positions:
        return "no open positions"
    ivts_now = snapshot.ivts if snapshot else float("nan")
    errors = []
    for position in positions:
        chain = chains.get(position["product"])
        if chain is None:
            errors.append(f"#{position['id']}: no {position['product']} chain this run")
            continue
        try:
            mark = mark_position(position, chain, ivts_now, events, cfg, as_of=today)
        except ValueError as exc:
            errors.append(f"#{position['id']}: {exc}")
            continue
        mark["ts"] = ts
        mark["ivts_now"] = _finite(mark["ivts_now"])
        repo.insert_position_mark(session, mark)
        marks.append(mark)
    detail = f"{len(marks)}/{len(positions)} open position(s) marked"
    if errors:
        raise ValueError(f"{detail} — " + "; ".join(errors))
    return detail


def _alerts(
    session: Session,
    cfg: AppConfig,
    ts: datetime,
    snapshot: MarketSnapshot | None,
    rows: dict[str, list[dict[str, Any]]],
    marks: list[dict[str, Any]],
) -> list[str]:
    """§7: regime change since the last snapshot, new tradeable candidates at or above the
    alert score, and any open position whose verdict isn't HOLD."""
    alerts: list[str] = []
    previous = repo.get_latest_term_structure(session, before=ts)
    if snapshot is not None and previous:
        prev_regime = max(previous.values(), key=lambda r: r["snapshot_ts"])["regime"]
        if prev_regime != snapshot.regime.regime:
            alerts.append(
                f"Regime change: {prev_regime} → {snapshot.regime.regime} "
                f"(playbook {snapshot.regime.playbook})"
            )
    for product, product_rows in rows.items():
        _, previous_rows = repo.get_candidates(session, product, before=ts)
        seen = {_key(r) for r in previous_rows}
        for r in product_rows:
            if (
                all(r["gates_passed"].values())
                and r["soft_score"] >= cfg.alerts.min_candidate_score
                and _key(r) not in seen
            ):
                alerts.append(
                    f"New tradeable {product}: {r['side']} {r['strike']:g} "
                    f"{r['front_expiry']}/{r['back_expiry']}, debit {r['debit_mid']:.2f}, "
                    f"score {r['soft_score']}"
                )
    for m in marks:
        if m["verdict"] != "HOLD":
            alerts.append(f"Position #{m['position_id']}: {m['verdict']} — {m['verdict_reason']}")
    return alerts


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["side"], row["strike"], row["front_expiry"], row["back_expiry"])


def alert_sender(cfg: AppConfig, secrets: Secrets) -> Callable[[list[str]], str]:
    def send(alerts: list[str]) -> str:
        if not cfg.alerts.enabled or cfg.alerts.channel == "none":
            return f"{len(alerts)} alert(s), not sent (alerts disabled in config.yaml)"
        if cfg.alerts.channel != "slack":
            raise NotImplementedError(
                f"alert channel {cfg.alerts.channel!r} isn't implemented — use slack or none"
            )
        if not secrets.slack_webhook_url:
            raise ValueError("alerts.channel is slack but SLACK_WEBHOOK_URL is empty")
        resp = requests.post(
            secrets.slack_webhook_url, json={"text": "\n".join(alerts)}, timeout=15
        )
        resp.raise_for_status()
        return f"{len(alerts)} alert(s) sent to Slack"

    return send


def live_sources(settings: Settings) -> Sources:
    cfg, secrets = settings.config, settings.secrets

    def alpaca_ready() -> None:
        if not (secrets.alpaca_api_key and secrets.alpaca_secret_key):
            raise ValueError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")

    def vix_live() -> dict[str, float]:
        return schwab_adapter.get_vix_family_quotes(schwab_adapter.get_client(secrets))

    def spy_bars() -> list[dict[str, object]]:
        alpaca_ready()
        start = datetime.now(UTC) - timedelta(days=cfg.regime.rv_window_long * 3)
        client = alpaca_adapter.get_client(secrets)
        return alpaca_adapter.get_daily_bars(client, "SPY", start, secrets.alpaca_data_feed)

    def chain(product: str) -> Chain:
        pcfg = cfg.products[product]
        if pcfg.source == "schwab":
            client = schwab_adapter.get_client(secrets)
            return schwab_adapter.get_chain(client, product, pcfg.symbol, cfg.snapshot.max_dte)
        alpaca_ready()
        feed = secrets.alpaca_data_feed
        spot = alpaca_adapter.get_quote(alpaca_adapter.get_client(secrets), pcfg.symbol, feed)
        return alpaca_adapter.get_chain(
            alpaca_adapter.get_option_client(secrets),
            pcfg.symbol,
            product,
            spot.price,
            max_dte=cfg.snapshot.max_dte,
        )

    return Sources(vix_csv=fetch_csv, vix_live=vix_live, spy_bars=spy_bars, chain=chain)


def latest_expected_run(
    now: datetime, run_times_et: list[str], events: EventsCalendar
) -> datetime | None:
    """The most recent scheduled slot at or before `now`, on a trading day (aware, ET)."""
    local = now.astimezone(ET)
    slots = sorted(time.fromisoformat(t) for t in run_times_et)
    for days_back in range(10):
        day = local.date() - timedelta(days=days_back)
        if not is_trading_day(day, events):
            continue
        for slot in reversed(slots):
            scheduled = datetime.combine(day, slot, tzinfo=ET)
            if scheduled <= local:
                return scheduled
    return None


def snapshot_is_stale(
    last_run_utc: datetime | None, now: datetime, cfg: AppConfig, events: EventsCalendar
) -> bool:
    """True once a scheduled slot has passed (plus grace) with no run at or after it."""
    expected = latest_expected_run(now, cfg.schedule.run_times_et, events)
    if expected is None or now < expected + timedelta(minutes=cfg.schedule.stale_after_minutes):
        return False
    return last_run_utc is None or last_run_utc.replace(tzinfo=UTC) < expected
