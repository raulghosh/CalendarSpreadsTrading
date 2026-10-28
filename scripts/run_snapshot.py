#!/usr/bin/env python3
"""Run one snapshot (build plan §7). Skips weekends and market holidays unless --force.
Exits non-zero if any step failed, so a scheduler log shows it."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calscan.events import is_trading_day, load_events  # noqa: E402
from calscan.jobs.snapshot import ET, alert_sender, live_sources, run_snapshot  # noqa: E402
from calscan.settings import Settings  # noqa: E402
from calscan.store.db import get_engine, get_session_factory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="run even on a non-trading day")
    args = parser.parse_args()

    settings = Settings.load()
    events = load_events()
    now = datetime.now(ET)
    if not args.force and not is_trading_day(now.date(), events):
        print(f"{now:%Y-%m-%d %H:%M} ET: not a trading day — skipped (use --force to run anyway)")
        return 0

    with get_session_factory(get_engine())() as session:
        report = run_snapshot(
            session,
            settings.config,
            live_sources(settings),
            events,
            notify=alert_sender(settings.config, settings.secrets),
        )
    print(f"snapshot {report.ts:%Y-%m-%d %H:%M} UTC — {'ok' if report.ok else 'FAILED STEPS'}")
    for s in report.steps:
        print(f"  {'ok  ' if s.ok else 'FAIL'} {s.name}: {s.detail}")
    for alert in report.alerts:
        print(f"  ALERT {alert}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
