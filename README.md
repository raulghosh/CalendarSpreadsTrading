# Calendar Spread Regime & Scanner

Decision-support tool for SPX/XSP/SPY calendar spreads: vol-regime classification, a hard-gated
candidate scanner, an exit-tree for open positions, a sigma/delta calculator, a scenario grid, and
a trade journal. **No order placement anywhere in this codebase** — every API client is read-only,
and `alpaca.trading` (the client that carries `submit_order`) is never imported.

## Status (2026-10-07)

- **Built:** phases 0–5 of the build plan — regime pipeline, scanner, calculators, positions with
  exit tree and journal, and scheduled snapshots.
- **Live data verified end to end** on 2026-10-07 against Schwab, Alpaca, and CBOE; the first
  live pull led to several fixes, all reflected in "What to know before trusting a number".
- **Open question:** whether Schwab reports open interest during market hours — if not, the OI
  gate can't pass for any product (see below).
- **Not built:** Phase 6 (HAR-RV forecast, vol-shock α estimation, event-variance stripping,
  Playbook B confirmation signals) and short calendars (long calendars only for now).

## Pages

- **Home** — snapshot health: each step of the last run, per-source data freshness, Schwab token
  age, the regime per product, and a **Run snapshot now** button.
- **Regime** — VIX term-structure curve, IVTS, slope_z history, realized vol vs 30-day ATM IV,
  ATM IV curve per product, upcoming events. The classifier picks a playbook:
  **A** (carry: IVTS ≤ 0.92 and slope_z below its top decile), **B** (backwardation: IVTS ≥ 1.00),
  otherwise **NONE**.
- **Scanner** — ranked calendar candidates from the latest snapshot: front 20–40 DTE, back at
  1.7–2.4× that, OTM strikes at 50/38/31/25 delta, hard gates (spread, open interest, debit vs
  NAV, RV10 vs breakeven, forward variance, ex-dividend) and a 0–5 soft score.
- **Sigma Calculator** — 1σ move, daily breakeven, strikes by σ distance, delta → strike;
  needs no data connection.
- **Scenario Grid** — reprices a candidate across ±2σ spot moves and paired front/back IV shocks.
- **Positions** — record a position (no order is sent), marks, exit-tree verdict, close and hedge.
- **Journal** — filled in automatically on open, close, and hedge, plus free-text notes.

## Setup

```bash
uv sync
cp .env.example .env                                  # fill in Schwab + Alpaca credentials
uv run python scripts/schwab_login.py                 # browser OAuth → data/schwab_token.json
uv run python scripts/run_backfill.py                 # 3y of CBOE VIX-family + Alpaca SPY bars
uv run python scripts/install_schedule.py --install   # snapshots on weekdays (see below)
uv run streamlit run src/calscan/app/Home.py
```

- **`.env` names must match `.env.example` exactly** (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, …);
  any other spelling loads as empty. `SCHWAB_CALLBACK_URL` must match the callback registered
  on your Schwab developer app, character for character.
- **Schwab login** always opens the browser and writes a fresh token. Schwab refresh tokens
  expire after 7 days; Home warns from day 6 — re-run the login then. It runs a local HTTPS
  server on the callback port to catch Schwab's redirect: stop anything else on that port, and
  click through the self-signed-certificate warning.

## Snapshots — how data gets in

The UI only reads the database. `scripts/run_snapshot.py` (build plan §7) does all fetching, and
each run stores one timestamped snapshot:

1. VIX-family history from CBOE, plus today's live values from Schwab
2. SPY daily bars from Alpaca (the realized-vol proxy for all three products)
3. per product: option chain → IV and greeks from each quote's mid → ATM IV curve out to 120 DTE
4. regime and term structure → scanner candidates → marks + exit-tree verdicts for open positions
5. alerts: regime change, new tradeable candidates (score ≥ 4), any position verdict ≠ HOLD

Each step is isolated — a failure is recorded and later steps still run. Home shows every step's
result, per-source freshness against the trading calendar, and flags a missed scheduled run.
Re-running within the same minute never duplicates rows. A full run takes about 80 seconds,
almost all of it fetching chains (Schwab caps a response at ~10 MB, so chains come in date
slices and are merged).

Run one on demand with **Home → Run snapshot now**, or schedule it (macOS launchd, weekdays at
`schedule.run_times_et` — 09:45, 12:30, 15:30 ET; holidays are skipped by the script, missed
slots run when the Mac wakes):

```bash
uv run python scripts/install_schedule.py              # print the plist, install nothing
uv run python scripts/install_schedule.py --install    # write ~/Library/LaunchAgents + load
uv run python scripts/install_schedule.py --uninstall
```

Output goes to `data/snapshot.log`. Alerts are off by default; set `alerts.enabled: true` and
`alerts.channel: slack` in `config.yaml` plus `SLACK_WEBHOOK_URL` in `.env` to receive them.

## Demo mode

Without credentials, the Scanner and Scenario Grid have a **demo mode** toggle that runs on the
bundled `tests/fixtures/` chains (frozen at the market of 2026-09-09). Demo rows are never stored
and can't be opened as positions. To exercise every page end-to-end, point the app at a throwaway
DB — `CALSCAN_DB=/tmp/demo.db uv run streamlit run src/calscan/app/Home.py` — rather than
writing demo data into the real history. The Sigma Calculator needs nothing at all.

## Commands

```bash
uv run pytest                                      # tests, no network required
uv run ruff check .                                # lint
uv run mypy src scripts tests                      # strict type check
uv run python scripts/run_backfill.py --dry-run    # print the planned history backfill
uv run python scripts/run_snapshot.py --force      # snapshot even on a weekend/holiday
uv run python scripts/schwab_login.py              # renew the Schwab token (every ≤7 days)
```

## What to know before trusting a number

- **IV and greeks are computed from each quote's mid**, with the same Black-Scholes model and
  `rates` the Scenario Grid reprices with — not taken from the broker. On the first live pull
  Schwab's IV field sat up to 2.4 vol points below what its own quotes implied (a calendar priced
  on it showed −20% at the grid's no-change cell), and it rounds gamma to 3 decimals. Greeks are
  per share with theta per calendar day and vega per vol point; SPY's are approximate (American).
- **Chains are sanity-checked before storing.** If any ATM IV falls outside
  `snapshot.atm_iv_sanity` (2%–150%; a price-unit slip, a broken feed), nothing is stored for
  that product and the run says why.
- **Open interest may be missing.** After the close on 2026-10-07 Schwab reported open interest
  0 on all but one of ~28k SPX/XSP contracts; each chain step's detail line counts how many
  carry OI. If that holds intraday, the OI gate can't pass for any product.
- **The regime uses the latest complete VIX day.** CBOE publishes each index's file at a
  different time, so the newest date is often partial, and the VIX file has values on market
  holidays that VIX3M's lacks. The regime reads the last day with VIX, VIX9D, and VIX3M all
  present; slope_z skips one-sided days; a refresh never blanks a stored value. If slope_z still
  can't be computed (under a year of history), the regime is `CONTANGO_UNVERIFIED` — playbook
  NONE, never mislabelled "too steep".
- **CBOE's daily files can lag a day or two.** Each snapshot writes today's VIX-family row from
  live Schwab quotes; if that step fails, the regime runs on the last CBOE close — Home's
  freshness table flags it.
- **RV10 gate fails closed.** With no SPY history in the DB, `rv10` is unknown and no candidate
  can be marked tradeable.
- **SPY is never "tradeable".** Alpaca's data-only feed has no open interest; the OI gate can't
  pass. SPY rows are there for comparison against SPX/XSP.
- **SPX rarely clears the debit gate at the default $100k NAV** — an SPX calendar costs several
  thousand dollars per contract against a $1,500 cap (1.5% of NAV). That is the tool working,
  and the reason XSP exists.
- `events.yaml` is maintained by hand — FOMC/CPI/NFP/OpEx and SPY ex-dividend dates go stale
  every January.

## Layout

- `src/calscan/domain/` — pure functions, fully unit-tested, no network/DB access.
- `src/calscan/adapters/` — thin wrappers around Schwab, Alpaca, and CBOE; translate raw API JSON
  into `domain/models.py` dataclasses. Business logic never touches raw API JSON.
- `src/calscan/store/` — SQLAlchemy engine/schema/repo for `data/calendar.db`.
- `src/calscan/jobs/` — backfill, the snapshot job, and position marking.
- `src/calscan/app/` — Streamlit pages; `app/data.py` is the glue shared by pages and the job.
- `scripts/` — login, backfill, snapshot, and schedule installer entry points.

All thresholds live in `config.yaml`; `exit_tree.delta_band` is the one value the build plan did
not specify and is documented inline.
