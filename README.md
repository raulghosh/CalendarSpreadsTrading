# Calendar Spread Regime & Scanner

Decision-support tool for SPX/XSP/SPY calendar spreads: vol-regime classification, a hard-gated
candidate scanner, an exit-tree for open positions, a sigma/delta calculator, a scenario grid, and
a trade journal. **No order placement anywhere in this codebase** — every API client is read-only,
and `alpaca.trading` (the client that carries `submit_order`) is never imported.

## Setup

```bash
uv sync
cp .env.example .env   # fill in Schwab + Alpaca credentials
uv run python scripts/run_backfill.py   # 3y of CBOE VIX-family + Alpaca SPY daily bars
uv run streamlit run src/calscan/app/Home.py
```

Without credentials the Scanner, Scenario Grid and Positions pages run in **demo mode** off the
bundled `tests/fixtures/` chains (a toggle at the top of each page). The Sigma Calculator needs
nothing at all.

## Commands

```bash
uv run pytest                                      # domain tests, no network required
uv run ruff check .                                # lint
uv run mypy src scripts tests                      # strict type check
uv run python scripts/run_backfill.py --dry-run    # print the planned history backfill
```

## What to know before trusting a number

- **RV10 gate fails closed.** With no SPY history in the DB, `rv10` is unknown and no candidate
  can be marked tradeable. Run the backfill (needs Alpaca keys) first.
- **SPY is never "tradeable".** Alpaca's data-only feed has no open interest; the OI gate can't
  pass. SPY rows are there for comparison against SPX/XSP.
- **SPX rarely clears the debit gate at the default $100k NAV** — a 100x-multiplier calendar on
  a ~$6000 underlying costs $5–7k/contract against a 1.5% cap. That is the tool working, and
  the reason XSP exists.
- **`[VERIFY]` markers** in `adapters/schwab.py` flag assumptions about the live Schwab payload
  (field names, IV as a percent, AM/PM settlement via option root) that have not yet been
  checked against a real response. `events.yaml` is maintained by hand — FOMC/CPI/NFP/OpEx
  dates and SPY ex-dividend dates go stale every January.
- **Not built yet:** the scheduled snapshot job (`jobs/snapshot.py`), alerts, persisted ATM
  curves / per-product regime rows, HAR-RV. Marks are on-demand from the Positions page.

## Layout

- `src/calscan/domain/` — pure functions, fully unit-tested, no network/DB access.
- `src/calscan/adapters/` — thin wrappers around Schwab, Alpaca, and CBOE; translate raw API JSON
  into `domain/models.py` dataclasses. Business logic never touches raw API JSON.
- `src/calscan/store/` — SQLAlchemy engine/schema/repo for `data/calendar.db`.
- `src/calscan/jobs/` — backfill and position marking (reused by the future snapshot job).
- `src/calscan/app/` — Streamlit pages; `app/data.py` is the only glue between DB, adapters and
  domain.

All thresholds live in `config.yaml`; `exit_tree.delta_band` is the one value the build plan did
not specify and is documented inline.
