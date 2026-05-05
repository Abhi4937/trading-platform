# Handoff Log

## Last Session
**Who:** Claude
**Date:** 2026-05-05 (Session 13 — M7 Friday→Saturday strangle/straddle sweep with rich 1m path + rule-based exit derivation)
**Branch:** `mainbranch-gemini_claude`

### Session 13 highlights
- **New M7 batch backtester** (`backend/app/analytics/m7_batch_backtester.py`,
  ~660 LOC). For every Friday × expiry × entry_hour (Fri 21:00 → Sat 03:00 IST,
  7 slots) × delta_target (8 values: 0.05–0.50), simulates a SHORT strangle/
  straddle held until Sat 17:30 IST. NO exit logic in the simulator — full 1m
  path is recorded so any exit rule (fixed-time, max_profit %, margin %,
  premium SL, or any future predicate) can be derived as a query against the
  saved path.

- **Strike-selection policy**:
  - delta_target = 0.50 → true straddle (single strike closest to spot, both legs)
  - delta_target < 0.50 → closest-from-below per leg (highest |delta| ≤ target)
  - No qualifying strike → trade skipped (logged, not faked)

- **Outputs** (`/home/abhis/btc-data/derived/m7/`):
  - `m7_trades.parquet` — entry-context only (one row per trade), ~80 cols
    incl. cost decomposition per leg, entry greeks, ATM IV, RV/IVP/M3 ctx
  - `m7_paths/friday_date=YYYY-MM-DD/part.parquet` — 1m path Hive-partitioned;
    35 cols per row (spot OHLCV+OI, leg marks/IV/OI, ATM IV, all greeks per leg,
    net greeks, theta/vega ratio, gross PnL, pct of credit, pct of margin)

- **New API** (`backend/app/api/m7_results.py`, ~430 LOC) under `/api/v1/m7/`:
  - `/summary`, `/trades`, `/path`, `/aggregate`, `/heatmap`, `/best_combo`,
    `/iv_band_summary`, `/cost_breakdown`, `/meta`
  - Exit rule passed as JSON query param: `exit_rule={"max_profit_pct":30,"premium_sl_pct":50}`
  - DuckDB walks the path parquet, finds first-trigger ts per trade, fetches the
    P&L at that ts, returns aggregated outcomes. Hard cap = Sat 17:30 IST.
  - Net P&L estimate = gross − 2× entry costs (round-trip approximation;
    /cost_breakdown returns exact entry-leg costs).

- **New frontend** under `frontend/src/components/m7/` and `pages/M7SweepDashboard.tsx`:
  - `M7FilterBar`, `M7HeadlineStrip`, `M7AggregateHeatmap`, `M7IvBandSummaryTable`,
    `M7BestComboTable`, `M7TradeLogTable`, `M7TradePathChart`
  - New "M7 Sweep" mode added to App.tsx (6th mode after M6 Results)

- **New script** `scripts/backfill_m7_enriched.py` — joins `m7_trades.parquet`
  with `calibration_v2.parquet` on `[dte_bucket, spot_bucket, delta_target_bucket,
  ivp_bucket]` to add `fair_credit_at_ivp`, `structural_credit_pct`,
  `iv_regime_premium_pct`, `excess_over_fair_pct`, `pattern_winrate`,
  `expectancy_per_credit_pct`, `n_trades_in_bucket`. Loader in `m7_results.py`
  prefers the enriched parquet when present.

- **Tests**: `backend/tests/test_m7_batch.py` (22 tests) +
  `backend/tests/test_m7_api.py` (9 tests) — all 31 passing.

- **Backfill running in background** (PID at /tmp/m7_backtest.pid, log at
  /tmp/m7_backtest.log). 121 Fridays Dec 2023 → Apr 2026 × ~7 expiries each
  × 7 entries × 8 deltas. Takes ~3 min/Friday → ETA ~5h. Trades-parquet
  written incrementally every 5 Fridays so dashboard works during backfill.

### Verified end-to-end (with partial data, 5 fridays):
- 988 trades, 590 wins (59%), avg net -$3.98
- With max_profit_pct=30 rule: 299/988 trades trigger early
- Cost decomposition matches `costs.py` to the cent
- Path endpoint returns 1230 1m rows per trade

### Known data limitations
- Spot OI / volume is NaN in historical spot parquet (only live recorder
  populates these). Code handles gracefully (defaults to 0).
- Option OI is 0 for trades older than the live recorder start. Code captures
  whatever's there.

### Pending follow-ups
- Wait for backfill to complete (~5h), then run `scripts/backfill_m7_enriched.py`
- Add per-friday parallelism (multiprocessing) to cut backfill time
- Add UI rule sliders for premium-SL preset (currently typed as numbers)

---

## Previous Session (12)
**Date:** 2026-05-04 (M6 Attribution: per-Friday best expiry + winners-vs-losers per contract + IV-premium decomposition + expanded summary strip + 80-90/90-100/100+ IV bands)

### Session 12 highlights
- **New `m4_trades_enriched.parquet`** (5,274 rows × 87 cols, 1.48 MB) —
  produced by `scripts/backfill_m4_enriched.py` (~150 LOC). Joins
  `m4_trades` with `calibration_v2` to add `fair_credit_at_ivp`,
  `structural_credit_pct`, `iv_regime_premium_pct`, `excess_over_fair_pct`
  per trade, and recomputes per-leg `theta`/`vega`/`gamma` via BS
  (`app.core.greeks.compute_greeks`) plus `theta_per_vega_{call,put,combined}`
  ratios. Loader in `m4_results.py` now prefers the enriched parquet,
  falls back to plain `m4_trades.parquet`. 4,548 / 5,274 trades matched
  a calibration bucket; 726 left null (their `dte_bucket` or `ivp_bucket`
  was 'nan').

- **3 new endpoints under `/api/v1/m4/`** (in `m4_results.py`):
  - `GET /winners_vs_losers?delta=` — per-contract avg(win) vs avg(loss)
    for **31 indicators** in 7 categories (IV / RV-VRP / Skew / Spot
    regime / GEX-Flow / Premium / Greeks). Flags |gap| > 0.5σ as
    "discriminating".
  - `GET /per_friday_best?delta=` — 121-row Friday view: winner /
    runner-up / loser contract + top 3 deciding indicators (ranked by
    |winner − loser| / σ).
  - `GET /win_frequency?delta=` — per-contract count of Fridays it was
    the best performer.

- **3 new frontend components in `frontend/src/components/m4/`**:
  - `M4WinFrequency.tsx` — bar chart + table of % Fridays each contract
    won
  - `M4WinnersVsLosers.tsx` — collapsible per-contract sections with 31
    indicators grouped by category; "Only discriminating" toggle
  - `M4PerFridayBest.tsx` — sortable 121-row table with deciding
    indicators per Friday; min-winner-net filter

- **Wired** as new "Attribution analysis" section in
  `M4ResultsDashboard.tsx` with shared Δ chip selector
  (`0.05/0.10/0.15/0.25/0.30/0.50`, default 0.30, persisted under
  `m6:attr_delta`).

- **Contract type summary strip extended** with 6 new columns:
  Avg Win, Avg Loss, Best Net, Worst Net, Best MFE, Worst MAE. Backend
  `/contract_type_summary` now returns `n_wins`, `n_losses`,
  `avg_net_win`, `avg_net_loss`, `best_net_pnl`, `worst_net_pnl`,
  `best_max_mtm`, `worst_min_mtm`.

- **IV bands split** in `_IV_BANDS` from `[…, 80, 100, 999]` to
  `[…, 80, 90, 100, 999]`. New labels: `80-90`, `90-100`, `100+`. The
  `100+` band exists but is **permanently empty** (max ATM IV in
  dataset = 98.65%).

- **Expiry-class filter cleaned up** — removed the search-text input
  from `M4ExpiryGridTable.tsx`; kept only the click-to-toggle chips.

### Notable findings exposed by Session 12
- **Tail risk**: bimonthly's worst single trade is **-$1,143** with
  -$1,121 MAE; avg loss per losing trade is **-$42.71** (3-4× any
  other contract). Workhorse contracts (current → biweekly) cap at
  -$103 thanks to the 100% per-leg SL.
- **Cleanest contract = next_to_next**: avg win +$22.25 vs avg loss
  -$21.49 (near-symmetric), 76% WR.
- **Win-frequency at Δ=0.30**: `current` wins outright on 28% of
  Fridays, `next_to_next` 27%, `next` 21%, `weekly` 12%. (Different
  from "highest avg P&L" — current's smaller avg makes it less
  attractive even though it wins more often.)
- **At Δ=0.30 the workhorse contracts have ZERO discriminating
  indicators at the 0.5σ threshold.** Translation: within a single Δ
  at one contract, entry conditions for winners look very similar to
  losers — the alpha is in *which contract you pick on which Friday*,
  not in pre-trade indicator filtering.
- 2025-03-07 anchor verified: at Δ=0.50, `current` wins at +$169 with
  the top deciding indicator being `theta_per_vega_put` (7.19σ
  separation vs the bimonthly loser).

---

## Prior session (kept for context)
**Date:** 2026-05-03 (Session 11 — LiveSignal page + M6 dashboard + expiry × IV × Δ grid + scroll fix + cleanup)
**Branch:** `mainbranch-gemini_claude`
**Status:** Platform now M1–M6 complete with 5 dashboard modes:
**Live | Historical | Backtest | Live Signal | M6 Results**.

**LiveSignal** scans every live expiry × 6 deltas in real time and
recommends the highest-quality (Δ, expiry) strangle using the calibrated_v2
quality formula. **M6 Results** visualizes the 5,274-trade M4 batch
backtest: DTE×Δ heatmap, pattern bars, credit×P&L scatter, quality
calibration curve, **plus a per-contract-type expiry × IV × Δ grid table**
showing MFE/MAE, gross/net P&L, slippage + brokerage, margin, and credit %
per cell. `/historical/calibration` surfaces v2 fields (`pattern_winrate`,
`overall_winrate`, `n_trades`, `expectancy_per_credit_pct`, etc.).

### What's new since prior handoff
- **Frontend**
  - `frontend/src/pages/{LiveSignalDashboard,M4ResultsDashboard}.tsx` —
    both now scrollable (`height: 100%; overflowY: auto`).
  - `frontend/src/components/m4/M4ExpiryGridTable.tsx` (NEW, ~330 LOC) —
    contract-type summary strip + 20-column sortable table:
    `Contract | IV % | Δ | n | WR | SL | Avg/Best MFE | Avg/Worst MAE |
    Avg Gross | Avg Net | Total Net | Slip RT | Slip ½ | Brk RT | Brk ½ |
    Cost RT | Credit % | Margin`. Mounted at the bottom of M4ResultsDashboard.
- **Backend**
  - `backend/app/api/m4_results.py` — added `/api/v1/m4/expiry_grid` and
    `/api/v1/m4/contract_type_summary`. Classifies each trade's expiry by
    Delta contract type (current/next/next_to_next/weekly/biweekly/
    three_week/monthly/bimonthly/quarterly) using `(entry_ts, expiry_date)`
    + last-Friday-of-month detector. IV bands keyed on the **specific
    expiry's own ATM IV** at entry (avg of CE+PE leg IVs from the Δ=0.50
    trade for that entry × expiry pair) — not the constant-maturity 7d.
  - `backend/app/api/m4_results.py` — `sl_rate` metric in `/aggregate`
    + `sl_hit_rate` in `/summary` updated to count `LegSL` (the actual
    parquet value) in addition to `SL`.

### Reusable analysis snippets (this session)
- `python3 /tmp/m4_per_expiry_iv_vs_delta.py` (re-runnable from work_log) —
  exports per-(entry × expiry) IV vs Δ table to
  `/home/abhis/btc-data/derived/m4_per_expiry_iv_vs_delta.{csv,xlsx}`.

### Headline M4 findings (5,274 trades, Friday 23:00 → Sat 10:00 IST)
| Contract | n | WR | Avg Net | Total Net |
|---|---|---|---|---|
| **next-to-next** (~2.8d) | 714 | **76.5%** | **+$11.96** | **+$8,537** |
| next (~1.8d) | 714 | 59.0% | +$9.23 | +$6,592 |
| current (~0.8d) | 726 | 48.8% | +$6.45 | +$4,682 |
| weekly (~7d) | 714 | 76.3% | +$5.64 | +$4,025 |
| biweekly (~14d) | 714 | 64.1% | +$2.49 | +$1,778 |
| monthly (~28d) | 486 | 50.2% | -$0.69 | -$336 |
| **bimonthly** (~52d) | 618 | **30.4%** | **-$26.26** | **-$16,230** |
| quarterly (~70d) | 36 | 25.0% | -$10.95 | -$394 |

**Skip everything ≥30 DTE.** Bimonthly alone bleeds –$16k and is dragging
the otherwise-+$25.8k book down to +$8.9k. Sweet spot = next-to-next +
weekly + Δ 0.30 in IV 50–70%.

### Known limitations carried over
- **OI capture** in live_recorder still NaN: instrumented (`mark_msgs` / `oi_msgs`
  counters added) and documented but not refactored. Delta's `candlestick_1m`
  channel only emits MARK bars; populating OI requires a parallel `v2/ticker`
  subscription that buckets `oi_contracts` updates into 1m bars. Needs a
  design pass; recorder is live and the change shouldn't be hacked in mid-stream.
- **Cost split in M4 trade rows.** `slippage_usd` / `brokerage_usd` in
  `m4_trades.parquet` are **round-trip totals** (entry + exit summed). The
  expiry-grid table shows a 50/50 estimate (`Slip ½`, `Brk ½`). True per-side
  capture requires re-running the M4 batch backtester with the trade_simulator's
  per-side fields written through (~6h on 4 workers). Not blocking — the per-job
  backtester (Backtest mode) already records true entry/exit splits.
- **IV-premium decomposition** (`fair_credit_at_ivp`, `structural_credit_pct`,
  `excess_over_fair_pct`) is computed live by `compute_trade_analytics` for the
  LiveSignal page, but is **not baked into m4_trades**. Could be added to the
  expiry-grid endpoint via a join to `calibration_v2.parquet`. Pending.
- `_simulate_day` → `simulate_trade_path` refactor still deferred (both paths
  working independently).

---

## What Was Done — 2026-05-03

### Pipeline backfills run
- **M2** options_enriched (resumable per-expiry checkpoint): 859 expiries, 4.6h total. Output: `options_enriched_{1m,5m,15m,30m}.parquet` (49–104 MB each).
- **M3** full_enriched: 30s. Output: `full_enriched_{1m,5m,15m,30m}.parquet` (65–367 MB each, 316 cols).
- **M5 v1 calibration**: 25 min. Output: `calibration_raw.parquet` (806k rows), `calibration.parquet` (600 buckets), `calibration_universal.parquet` (30 rows).
- **M4 batch backtester**: 6.4h. Output: `m4_trades.parquet` (5,274 trades, 1.1 MB), `m4_paths.parquet` (49,475 hourly snapshots, 3.3 MB). **Win rate 58.2%**, SL hit rate 17.8%, net P&L sum +$8,859.
- **M5 v2 enrichment**: 2.1s. Output: `calibration_v2.parquet` (600 buckets, 450 with M4 data, 38 cols including `pattern_winrate`, `z_winners_mean/std`, `overall_winrate`).

### Code shipped (5 new files, ~2,200 LOC)
- `backend/app/services/trade_simulator.py` — extracted `simulate_trade_path()` from `_simulate_day` so M4 reuses the bar-walk + per-leg SL + cost + margin logic. New: optional path snapshot recording at hourly cadence.
- `backend/app/analytics/m4_batch_backtester.py` — Friday 23:00 IST × all live expiries × 6 deltas (0.05/0.10/0.15/0.25/0.30/0.50) × 100 lots/leg. Exit Sat 10:00 IST or earlier on per-leg 100% loss SL. Records hourly path snapshots. Costs (slippage + brokerage) + margin (29-scenario portfolio stress) tracked per trade. Outputs `m4_trades.parquet` + `m4_paths.parquet`.
- `backend/app/analytics/backfill_attribution.py` — M5 v2: aggregates M4 outcomes per `(DTE × spot × Δ × IVP)` bucket. Computes `pattern_winrate` per pattern (JSON-encoded), `z_winners_mean/std` (winners-only credit_pct distribution), `expectancy_per_credit_pct`, `expectancy_per_margin_pct`, `sl_hit_rate`. Writes `calibration_v2.parquet` as left-join superset of v1.
- `backend/tests/test_trade_simulator.py` — 7 tests (synthetic data, monkey-patched data accessors). Covers SL trigger, snapshot cadence, cost application, MFE/MAE coherence, breaching-leg identification.
- `backend/tests/test_backfill_attribution.py` — 4 tests (synthetic m4_trades + v1 calibration → v2 parquet round-trip).

### Code modified
- `backend/app/analytics/enrich_options.py` — M2 per-expiry Stage A checkpoint (atomic .tmp + rename). Survives container restarts. `--clear-checkpoint` CLI flag. Allowed M2 backfill to recover from a SessionStart-hook-triggered kill at 53% without losing work.
- `backend/app/services/strangle_analytics.py` — auto-detect v2 calibration. `_load_calibration` prefers `calibration_v2.parquet` when present. `lookup_calibration` surfaces v2 columns (`z_winners_mean/std`, `pattern_winrate`, `overall_winrate`, `n_trades`) when available. `compute_trade_analytics` adds v2 quality formula path before falling back to v1, then to `fallback_ivp_credit`. `quality_source` field reflects which path was taken.
- `frontend/src/types/backtest.ts` — `BacktestTrade.quality_source` enum gains `'calibrated_v2'`.

### Verified end-to-end
- `_load_calibration()` reads from V2 (38 cols incl. v2 fields) ✅
- `lookup_calibration(dte=7, spot=100k, td=0.10, ivp=70)` returns `pattern_winrate={"C":1.0,"D":1.0,"Other":0.5}`, `overall_winrate=0.83`, `n_trades=6`, `z_winners_mean=0.023`, `z_winners_std=0.0065` ✅
- `compute_trade_analytics()` returns `quality_source: 'calibrated_v2'`, `quality_score: 40.77`, `size_band: 'skip'` for the synthesized test trade ✅
- Live recorder running: `recorder: WS connected — subscribing 488 symbols` and 507 parquet files written to `data_live/` within 35s of restart ✅
- Calibration endpoint `/api/v1/historical/calibration?dte=7&spot=100000&delta_target=0.10&ivp=70` returns rich bucket (n_samples=1033) ✅
- M3 snapshot endpoint `/api/v1/historical/snapshot-context?ts=...` returns 89 fields ✅

### Pipeline outputs on disk
```
/home/abhis/btc-data/derived/spot_enriched.parquet              151 MB  (M1)
/home/abhis/btc-data/derived/options_enriched_5m.parquet         49 MB  (M2)
/home/abhis/btc-data/derived/full_enriched_5m.parquet           232 MB  (M3, 316 cols)
/home/abhis/btc-data/derived/calibration.parquet                 82 KB  (M5 v1, 600 buckets)
/home/abhis/btc-data/derived/calibration_universal.parquet      6.5 KB  (M5 v1 fallback)
/home/abhis/btc-data/derived/calibration_raw.parquet                    (M5 v1 raw snapshots)
/home/abhis/btc-data/derived/m4_trades.parquet                  1.1 MB  (M4, 5,274 trades)
/home/abhis/btc-data/derived/m4_paths.parquet                   3.3 MB  (M4, 49,475 path snapshots)
/home/abhis/btc-data/derived/calibration_v2.parquet                     (M5 v2, 600 buckets, 450 with M4 data)
/home/abhis/btc-data/data_live/                                         (live recorder, growing)
```

### Quick stats from M4 trades (cross-trade winrate sanity)
| Δ      | n   | win_rate | sl_rate | avg_pnl |
|--------|-----|----------|---------|---------|
| 0.05   | 879 | 59.2%    | 17.6%   | -$0.58  |
| 0.10   | 879 | 60.4%    | 18.5%   | +$0.16  |
| 0.15   | 879 | 61.3%    | 19.2%   | +$1.24  |
| 0.25   | 879 | 60.1%    | 18.7%   | +$3.18  |
| 0.30   | 879 | 58.0%    | 18.2%   | +$3.54  |
| 0.50   | 879 | 50.3%    | 14.8%   | +$2.54  |

By DTE: 3-7d = 76% win rate (sweet spot); 30-60d = 36.5% (long-dated entries bad); 0-3d = 61% (high gamma); 7-14d = 64%; 14-30d = 54%.

### Commits today
- `58d67c2` — M2 per-expiry checkpoint to survive kills
- `847da38` — M4 + M5 v2 + analytics auto-detect (1,566 lines, 5 files)
- `bd05f94` — backfill_attribution unit tests (154 lines)
- `d9e3772` — frontend `calibrated_v2` enum addition

### Pending / next-session candidates
- **LiveSignal page (separate plan)**: hybrid backend (slow cols from M3 row + fast cols from `ticker_store`) + new `LiveSignalDashboard.tsx`. Reuses existing `StrangleAnalyticsPanel`. ~1000 LOC.
- **Refactor `_simulate_day` to call `simulate_trade_path()`**: deferred from M4 plan step 1. Needs equivalence test vs old code first. Low priority — both paths working independently.
- **Surface v2 cols in `/historical/calibration` endpoint response**: currently the endpoint returns v1-shape JSON; frontend doesn't yet see `pattern_winrate`/`z_winners_*`. The backend `compute_trade_analytics` uses v2 internally so trade rows are correct; only the standalone endpoint shape needs updating.
- **Run nightly merge sanity check**: `merge_live_to_main` is scheduled, first run will fire after 20h of recorder collecting. Inspect at next session start.

---

## Architecture (LiveSignal — design locked in, not yet built)
For LiveSignal, do NOT build incremental enrichment. Use a hybrid read:
- **Slow-moving cols** (IVP_90d, RV_7d/14d/30d, ADX_4h, pattern, vrp_pct_90d) read from latest M3 row in `full_enriched_5m.parquet`. These don't shift minute-to-minute — staleness of even hours is fine.
- **Fast-moving values** (spot, ATM IV @7/14/30d, skew RR/BF, GEX, current strangle leg marks) computed on-the-fly from `ticker_store` (already populated tick-by-tick by `delta_ws_client.py`). BS solver runs server-side.
- Merge them, run existing `strangle_analytics.compute_trade_analytics`, return JSON.
- Reuses existing `<StrangleAnalyticsPanel />` on a new `LiveSignalDashboard.tsx`. ~1000 LOC total. No incremental enrichment loop. No 5-min scheduler.

The recommendation panel scans ALL live expiries × 6 deltas, ranks by `quality_score` (now v2-calibrated thanks to today's M4+M5v2 work), and shows the best (Δ, expiry) combo with full analytics for each of the top N candidates.
