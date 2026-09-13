# Calendar Spread Regime & Scanner

Decision-support tool for SPX/XSP/SPY calendar spreads: vol-regime classification, a hard-gated
candidate scanner, an exit-tree for open positions, a sigma/delta calculator, a scenario grid, and
a trade journal. **No order placement anywhere in this codebase** — every API client is read-only.

## Setup

```bash
uv sync
cp .env.example .env   # fill in Schwab + Alpaca credentials
```

## Commands

```bash
uv run pytest                              # domain tests, no network required
uv run ruff check .                        # lint
uv run mypy src scripts tests              # strict type check
uv run python scripts/run_backfill.py --dry-run   # print the planned history backfill
uv run streamlit run src/calscan/app/Home.py       # UI (once Phase 2+ lands)
```

## Layout

- `src/calscan/domain/` — pure functions, fully unit-tested, no network/DB access.
- `src/calscan/adapters/` — thin wrappers around Schwab, Alpaca, and CBOE; translate raw API JSON
  into `domain/models.py` dataclasses. Business logic never touches raw API JSON.
- `src/calscan/store/` — SQLAlchemy engine/schema/repo for `data/calendar.db`.
- `src/calscan/jobs/` — scheduled backfill/snapshot/alert jobs.
- `src/calscan/app/` — Streamlit pages.

See the build plan for the full phase breakdown, formulas, and acceptance tests.
