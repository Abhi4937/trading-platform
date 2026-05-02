# Claude Code Prompt — Build Short Strangle Indicator & Backtest System

> Paste this into Claude Code in your `btc-options` repo. The full specification is in `docs/SHORT_STRANGLE_INDICATORS_SPEC.md` — **read it first** before writing code.

---

## CONTEXT

I trade short strangles on BTC options on Deribit India (Delta Exchange). I have an existing platform that:
- Collects spot + options chain data into Parquet (DuckDB-queryable)
- Has a Historical Dashboard with strategy builder, MTM chart, Greeks
- Has a backend (FastAPI, single uvicorn worker) and a frontend (Vite + React)

I want to add an **indicator + backtest layer** that scores every potential entry and produces full per-trade attribution. The full spec is in `docs/SHORT_STRANGLE_INDICATORS_SPEC.md`. Please read it carefully before starting.

## WHAT I NEED YOU TO BUILD

Six modules, in order. **Do not skip ahead.** Confirm completion of each before starting the next.

### Module 1 — `backend/app/analytics/enrich_spot.py`

Reads `spot_5m.parquet`, computes every indicator in **Spec Section 3** (spot-only indicators), writes `data/derived/spot_enriched.parquet`.

Required columns (exact names from spec):
- Returns: `spot_ret_5m`, `spot_ret_1h`, `spot_ret_4h`, `spot_ret_24h`, `spot_ret_7d`
- Realized vol: `rv_close_24h`, `rv_parkinson_24h`, `rv_gk_24h`, `rv_7d`, `rv_14d`, `rv_30d`
- ATR: `atr_14_4h`, `atr_pct_4h`, `atr_compression_ratio`
- Trend: `adx_4h`, `+di_4h`, `-di_4h`, `rsi_4h`, `rsi_1d`
- MA distance: `dist_from_ma20_pct`, `dist_from_ma50_pct`, `dist_from_ma200_pct`
- Bollinger: `bb_width_4h`, `bb_pct_b_4h`, `bb_squeeze_flag`
- RVP: `rvp_30m`, `rvp_1h`, `rvp_4h`, `rvp_1d`
- Time: `day_of_week`, `hour_of_day_ist`, `is_weekend`

Implementation rules:
- Use Polars (not pandas)
- All percentile ranks computed over a trailing 90-day window
- Output is a 5-minute bar series with all original spot columns + enriched columns
- Idempotent: re-running just appends new bars, doesn't recompute history

**Stop here. Show me the file. I will review and run it before you continue.**

---

### Module 2 — `backend/app/analytics/enrich_options.py`

Reads chain snapshots (existing parquet structure), computes every indicator in **Spec Section 4**, writes `data/derived/options_enriched.parquet`.

Required outputs at each snapshot:
- Constant-maturity ATM IV: `atm_iv_7d`, `atm_iv_14d`, `atm_iv_30d`, `atm_iv_60d` (linear interpolation between bracketing expiries)
- IVP per tenor: `ivp_atm_7d`, `ivp_atm_14d`, `ivp_atm_30d` (90-day percentile)
- Multi-timeframe IVP (windows of `atm_iv_30d`): `ivp_30m`, `ivp_1h`, `ivp_4h`, `ivp_1d`
- Skew: `iv_25d_call`, `iv_25d_put`, `iv_10d_call`, `iv_10d_put`, `risk_reversal_25d`, `butterfly_25d`, `wing_atm_ratio`
- Term: `term_slope_7_30`, `term_ratio_7_30`, `term_slope_30_60`
- OI: `total_call_oi`, `total_put_oi`, `pcr_oi`, `pcr_volume`, `max_oi_call_strike`, `max_oi_put_strike`, `max_oi_call_pct_of_total`, `max_oi_put_pct_of_total`, `dist_to_call_wall_pct`, `dist_to_put_wall_pct`
- GEX: `gex_per_strike` (nested), `total_gex`, `gex_flip_level`, `gex_regime`, `dist_to_flip_pct`
- Strangle-specific: `strangle_iv_avg_7d`, `strangle_iv_avg_14d`, `strangle_ivp_7d`, `strangle_ivp_14d`

Implementation rules:
- Reuse existing `app/core/greeks.py` for any Greek recomputation needed
- Linear interpolation for constant-maturity IVs between the two bracketing expiries
- For .25Δ and .10Δ IVs at a given DTE: scan the chain for the strike with delta closest to target, return its IV. Do this per side (call/put).
- GEX assumes customer-net-long sign convention: `gex_strike = -(call_oi × call_gamma + put_oi × put_gamma) × spot² × 0.01`
- Same idempotency rule as Module 1

**Stop here. Show me the file. I will review and run it.**

---

### Module 3 — `backend/app/analytics/enrich_derived.py`

Reads both enriched files, joins on timestamp, computes **Spec Section 5** (derived metrics that need both spot and options):

- VRP family: `iv_rv_spread_7d`, `iv_rv_spread_14d`, `iv_rv_spread_30d`, `iv_rv_ratio_7d`, `vrp_pct_7d`, `vrp_pct_14d`
- Expected move: `expected_move_1sigma_7d`, `expected_move_1sigma_14d`, `expected_move_2sigma_7d`
- Vol-of-vol: `iv_change_stdev_7d`, `vov_ratio`

Writes `data/derived/full_enriched.parquet` (the joined, fully enriched table — this is the master snapshot table for everything downstream).

**Stop here. Show me the file.**

---

### Module 4 — `backend/app/analytics/backtest.py`

The backtest engine. Reads `full_enriched.parquet`, simulates strangle entries.

#### CLI signature
```
python -m app.analytics.backtest \
  --start 2025-01-01 \
  --end 2026-04-30 \
  --entry-schedule "saturday-10:00-IST" \
  --target-dte 7 \
  --target-delta 0.10 \
  --exit-rule "50pct-or-2x-stop-or-strike-threat" \
  --output data/backtests/sat_7dte_10delta.parquet
```

#### Per-trade simulation
For each scheduled entry timestamp:
1. Load market snapshot from `full_enriched.parquet`
2. Find available expiry closest to `target_dte`
3. From that expiry's chain: pick call strike with delta closest to `+target_delta`, put strike with delta closest to `-target_delta`
4. Record entry credits, all Section 7.1-7.7 fields
5. Compute all Section 7.8 ratios
6. Compute Section 7.9 attribution (uses calibration data — see Module 5; if calibration isn't built yet, leave these columns NaN and fill them in a second pass)
7. Walk forward bar-by-bar, recording Section 7.10 path arrays at hourly resolution
8. At each path bar, compute P&L attribution (Δspot×delta + ½gamma×Δspot² + vega×ΔIV + theta×Δt). Write to `data/backtests/sat_7dte_10delta_paths.parquet` keyed by `entry_id`.
9. Apply exit rules; record outcome (Section 7.12)
10. Diagnose win/loss type (Section 7.13)
11. Write one row per trade to the output parquet

#### Output
`backtest_results.parquet` — ~110 columns per trade as specified in Section 7.

#### Implementation notes
- Use Polars throughout
- Trade IDs are UUIDs
- Path data goes in a separate parquet (millions of rows otherwise)
- Margin estimate: use a simple SPAN-style approximation (or call into the existing `marginEngine.ts` logic if you can port it; otherwise approximate as `max(call_strike − spot, spot − put_strike) × 0.20`)
- Honor the existing IST convention from `ist_utils.py`

**Stop here. Show me the file. I will run a small backtest (3 months) before you continue.**

---

### Module 5 — `backend/app/analytics/calibration.py`

Reads `backtest_results.parquet`, computes the calibration data in **Spec Section 9**:

- Universal IVP→credit% curve (DTE-normalized using `credit_pct / sqrt(dte)`)
- Personal baselines (mean, std for all entries and for winners only)
- Per-pattern statistics
- Writes `data/derived/calibration.parquet`

#### Pattern detection (rule-based, simple version)
Until I refine this, use these rules to assign A/B/C/D:

- Pattern A "Fresh Spike": `ivp_4h_at_entry > 70` AND `ivp_4h delta over last 24h > +10`
- Pattern B "Post-Crash": `spot_ret_24h_at_entry < -3%` AND `ivp_4h_at_entry > 65`
- Pattern C "Stale": `ivp_4h delta over last 48h < +3` AND `ivp_4h_at_entry < 60`
- Pattern D "Active Trend": `adx_4h_at_entry > 25`
- Pattern fallback: "Other" if none match

This is computed in `enrich_derived.py` as a column on the enriched data, then read by backtest. **Move pattern detection there**, not into calibration.

#### Quality score
Implement the formula from Spec Section 9.4. Run it on every backtest row in a second-pass script `backfill_attribution.py` that:
1. Reads `backtest_results.parquet`
2. Reads `calibration.parquet`
3. Computes Section 7.9 attribution columns (fair_credit, structural, iv_regime_premium, excess, z-scores, quality_score)
4. Writes back to `backtest_results.parquet`

**Stop here. Show me both files.**

---

### Module 6 — Backtest Dashboard frontend

Add a new page `frontend/src/pages/BacktestDashboard.tsx` that:

#### Top section — backtest selector
- Dropdown: list of `data/backtests/*.parquet` files
- Show summary stats: total trades, win rate, total P&L, profit factor, max DD, Sharpe per trade

#### Filters bar
- Pattern (multi-select A/B/C/D)
- Outcome (win/loss)
- IVP bucket
- DTE bucket
- Date range

#### Trades table
- Sortable columns: entry_ts, pattern, credit_pct, ivp_4h, quality_score, net_pnl, outcome
- Click row → opens detail card

#### Detail card (per-trade attribution)
Render the Section 10 trade card layout:
- Entry context block
- Premium received with attribution stacked bar (Structural / IV regime / Excess)
- Richness scores as horizontal bars (vs history, vs winners, IVP, pattern winrate, composite)
- Greeks at entry with Theta/Vega and Gamma/Theta ratios
- Vol context table
- Path summary: max favorable, max adverse, breached?, dominant Greek over life
- Outcome with diagnosis

#### Path chart panel
For the selected trade, show:
- Stacked area chart of cumulative P&L attribution: delta_pnl, gamma_pnl, vega_pnl, theta_pnl over the trade's lifetime
- Overlay actual P&L line
- Spot price line on secondary axis

Use the existing `lightweight-charts` setup from MultiPaneChart.tsx as the pattern.

#### Aggregate analysis section
- Win rate × IVP bucket heatmap
- Win rate × pattern bar chart  
- P&L distribution histogram
- Quality score vs P&L scatter (validates the score)

#### "Live current state" panel — placeholder for now
Add a sibling page `frontend/src/pages/LiveSignal.tsx` with the Section 8 layout but read from the LATEST row of `full_enriched.parquet`. This becomes the live entry-decision panel later. For now, just the data display — no auto-refresh, no order execution.

**Stop here. Show me the files. Do not commit until I confirm.**

---

## RULES

1. **Read `docs/SHORT_STRANGLE_INDICATORS_SPEC.md` first.** It is the source of truth. If anything in this prompt contradicts it, follow the spec and ask me.

2. **Follow `CLAUDE.md` rules.**
   - Ask before editing files
   - Update `HANDOFF.md` at the end
   - Check `git status` before starting
   - Don't start new work on uncommitted changes

3. **One module at a time.** Stop after each. Do not chain.

4. **Polars not pandas.** DuckDB for SQL queries against parquet.

5. **IST is the timezone.** Use existing `ist_utils.py`. All timestamps stored as IST-aware.

6. **No look-ahead bias.** Every value used at time T must be computable from data ≤ T.

7. **Idempotency.** Re-running a module on already-enriched data should not corrupt or duplicate.

8. **No mocking.** If a calculation needs data we don't have, stop and tell me — don't fabricate.

9. **Tests.** For each module, add at least one unit test in `backend/tests/test_<module>.py` with synthetic data covering the main paths.

10. **Match existing conventions.** Look at `app/core/greeks.py`, `app/api/historical.py`, and existing parquet readers before designing new code.

---

## DELIVERABLE ORDER

1. Module 1 (`enrich_spot.py`) → STOP, await review
2. Module 2 (`enrich_options.py`) → STOP, await review
3. Module 3 (`enrich_derived.py`) + pattern detection → STOP, await review
4. Module 4 (`backtest.py`) → STOP, await review (I'll run 3-month backtest)
5. Module 5 (`calibration.py` + `backfill_attribution.py`) → STOP, await review
6. Module 6 (frontend pages) → STOP, await review

---

## OPEN QUESTIONS TO ASK ME

Before starting Module 1, ask me about anything ambiguous. Likely candidates:
- Which 5-minute spot parquet file is the canonical source?
- Where exactly are chain snapshots stored (path / partitioning scheme)?
- What's the existing schema of those snapshots?
- Are there gaps in historical data I should be aware of?
- Should output go to `data/derived/` or somewhere else?

Don't guess. Ask. Then build.
