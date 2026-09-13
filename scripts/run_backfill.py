#!/usr/bin/env python3
"""Backfill VIX-family + SPY history. `--dry-run` prints the plan without touching network/DB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calscan.jobs import backfill  # noqa: E402
from calscan.settings import Settings  # noqa: E402
from calscan.store.db import get_engine, get_session_factory  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="print the planned fetches and exit"
    )
    args = parser.parse_args()

    p = backfill.plan()
    if args.dry_run:
        print("Planned backfill:")
        print(f"  CBOE daily closes ({p.spy_years}y): {', '.join(p.cboe_symbols)}")
        print(f"  Alpaca SPY daily bars ({p.spy_years}y)")
        return

    settings = Settings.load()
    session_factory = get_session_factory(get_engine())
    with session_factory() as session:
        backfill.run(session, settings)


if __name__ == "__main__":
    main()
