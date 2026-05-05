# Claude's Work Log

## Session 13 (2026-05-05) — M7 Friday→Saturday strangle/straddle sweep

### Headline
Built a new "M7" pipeline + dashboard that sweeps every (entry hour × expiry ×
delta target) for short strangles/straddles on every Friday→Saturday window in
the Dec 2023 → present dataset. The simulator records the full 1m path (no exit
logic) so any exit rule (fixed time, % of max profit, % of margin, premium SL %,
or anything else) is derived as a query against the saved path. Designed for
"simulate once, query forever".

### What was built
- `backend/app/analytics/m7_batch_backtester.py` (~660 LOC) — the new sweep
  backtester. Strike picker: same-strike for Δ=0.50 (true straddle), closest-
  from-below for Δ<0.50. Walks 1m bars from entry → Sat 17:30 IST, records
  rich path rows (35 cols incl. spot OHLCV+OI, leg marks/IV/OI, ATM IV at this
  minute, all greeks per leg, net greeks, theta/vega ratio, gross PnL,
  pnl_pct_of_credit, pnl_pct_of_margin). Atomic writes (incremental every
  5 fridays) to `m7_trades.parquet` + `m7_paths/friday_date=*/part.parquet`.
- `scripts/backfill_m7_enriched.py` — joins m7_trades with calibration_v2 on
  bucket keys to add fair_credit_at_ivp, structural_credit_pct,
  iv_regime_premium_pct, excess_over_fair_pct, pattern_winrate, etc.
- `backend/app/api/m7_results.py` (~430 LOC) — new API under /api/v1/m7/.
  Endpoints: /summary, /trades, /path, /aggregate (with optional exit_rule),
  /heatmap, /best_combo, /iv_band_summary, /cost_breakdown, /meta.
  Exit rule derivation done via DuckDB SQL: find first-trigger ts per trade,
  fetch P&L at that ts, hard cap = Sat 17:30 IST.
- `frontend/src/types/m7.ts` + `services/m7_api.ts` — typed clients.
- `frontend/src/pages/M7SweepDashboard.tsx` + `components/m7/` (7 components):
  FilterBar with exit-rule inputs, HeadlineStrip, AggregateHeatmap (reusable),
  IvBandSummaryTable (the "answer" headline), BestComboTable, TradeLogTable,
  TradePathChart (1m path viewer with PnL/Premium/IV/Δ tabs).
- New "M7 Sweep" mode added to App.tsx (6th mode).
- Tests: 22 in test_m7_batch.py + 9 in test_m7_api.py = 31 total, all passing.

### Verified end-to-end (with partial backfill data, 5 fridays = 988 trades)
- 59% win rate at gross P&L
- 30% max-profit rule triggered on 299/988 trades
- Cost decomposition matches `costs.py` to the cent (entry_slip_call=$0.485,
  entry_brk_call=$0.430 verified by hand)
- Path endpoint returns 1230 1m rows for a full Fri 21:00 → Sat 17:30 trade

### Known data limitations
- Spot OI / volume are NaN in the historical spot parquet (only the live
  recorder populates them); code defaults to 0 and continues.
- Option OI is 0 for any history pre-dating the live recorder.

### Files added (untracked, ready to commit)
- `backend/app/analytics/m7_batch_backtester.py`
- `backend/app/api/m7_results.py`
- `backend/tests/test_m7_batch.py`
- `backend/tests/test_m7_api.py`
- `scripts/backfill_m7_enriched.py`
- `frontend/src/pages/M7SweepDashboard.tsx`
- `frontend/src/services/m7_api.ts`
- `frontend/src/types/m7.ts`
- `frontend/src/components/m7/M7AggregateHeatmap.tsx`
- `frontend/src/components/m7/M7BestComboTable.tsx`
- `frontend/src/components/m7/M7FilterBar.tsx`
- `frontend/src/components/m7/M7HeadlineStrip.tsx`
- `frontend/src/components/m7/M7IvBandSummaryTable.tsx`
- `frontend/src/components/m7/M7TradeLogTable.tsx`
- `frontend/src/components/m7/M7TradePathChart.tsx`

### Files modified
- `backend/app/main.py` — mounts /api/v1/m7 router
- `frontend/src/App.tsx` — adds M7_SWEEP mode + nav button

### Long-running backfill
Full backfill is launched in the background (PID at /tmp/m7_backtest.pid,
log at /tmp/m7_backtest.log). 121 Fridays × ~7 expiries × 7 entries × 8 deltas.
~3 min/Friday → ETA ~5h. Trades-parquet written incrementally every 5 fridays
so the dashboard shows progressively more data. After completion, run
`python3 scripts/backfill_m7_enriched.py` to add the calibration_v2 join
columns.

---

## Session 12 (2026-05-04) — M6 Attribution + summary strip extensions + IV bands

### Headline
Added a full attribution layer to the M6 dashboard so the user can see
*per-Friday which expiry won and why*, plus *per-contract winners-vs-losers
across 31 indicators*. Also extended the contract summary strip with
Avg Win / Avg Loss / Best Net / Worst Net / Best MFE / Worst MAE columns.
Split the 80-100 IV band into 80-90, 90-100, and a (always-empty) 100+
band so the high-IV regimes are visible at granularity.

### Files shipped
- **NEW: `scripts/backfill_m4_enriched.py`** (~150 LOC) — joins
  `m4_trades.parquet` with `calibration_v2.parquet` on
  (`dte_bucket`,`spot_bucket`,`delta_target`,`ivp_bucket`) to add:
  - `fair_credit_at_ivp`, `structural_credit_pct`,
    `iv_regime_premium_pct`, `excess_over_fair_pct`
  - per-leg `theta`/`vega`/`gamma` recomputed via
    `app.core.greeks.compute_greeks` (T = `dte_days/365`, r = 0)
  - `theta_per_vega_{call,put,combined}` ratios
  Output: `/home/abhis/btc-data/derived/m4_trades_enriched.parquet`
  (5,274 rows × 87 cols, 1.48 MB). 4,548 trades matched a calibration
  bucket; 726 left null (ivp/dte = nan). excess_over_fair_pct mean
  +0.0026 (near zero — sanity gate). theta_per_vega_combined median
  3.05 (positive — short strangles get more decay than vol-risk).

  **How to run:** `docker exec -i docker-backend-1 python3 - <
  scripts/backfill_m4_enriched.py` (scripts/ is not container-mounted,
  pipe via stdin).

- **MOD: `backend/app/api/m4_results.py`**
  - `_load_trades` now prefers `m4_trades_enriched.parquet` over
    plain `m4_trades.parquet`
  - 3 new endpoints (described above in HANDOFF.md)
  - `_IV_BANDS` extended from `[…,80,100,999]` to `[…,80,90,100,999]`
    so the dashboard can show 80-90, 90-100, 100+ separately
  - `/contract_type_summary` returns 6 new fields:
    `n_wins`, `n_losses`, `avg_net_win`, `avg_net_loss`,
    `best_net_pnl`, `worst_net_pnl`, `best_max_mtm`, `worst_min_mtm`

- **MOD: `frontend/src/services/m4_api.ts`** — added types
  `IndicatorMeta`, `IndicatorComparison`, `WinnersVsLosersRow`,
  `FridayTradeSummary`, `DecidingIndicator`, `PerFridayBestRow`,
  `WinFrequencyRow` and 3 fetch helpers
  (`fetchWinnersVsLosers`, `fetchPerFridayBest`, `fetchWinFrequency`).
  Extended `ContractTypeSummaryRow` with the new fields.

- **NEW: `frontend/src/components/m4/M4WinFrequency.tsx`** (~120 LOC)
- **NEW: `frontend/src/components/m4/M4WinnersVsLosers.tsx`** (~250 LOC)
  — collapsible per-contract tables grouping 31 indicators into 7
  categories (IV / RV-VRP / Skew/Term / Spot regime / GEX-Flow /
  Premium / Greeks). Discriminating rows highlighted (|gap| > 0.5σ).
- **NEW: `frontend/src/components/m4/M4PerFridayBest.tsx`** (~200 LOC)
  — sortable 121-row table with date / winner / net / runner-up /
  loser / top-3 deciding indicators per Friday.

- **MOD: `frontend/src/pages/M4ResultsDashboard.tsx`** — added new
  `<AttributionSection />` mounting the 3 components below the
  existing expiry grid; Δ chip selector lifted via
  `usePersistedState('m6:attr_delta', 0.30)`.

- **MOD: `frontend/src/components/m4/M4ExpiryGridTable.tsx`** —
  - Removed the search-text input from the expiry-class filter (per
    user feedback "search box not working"); kept just clickable
    chips that toggle the entire class on/off.
  - Added 6 new columns to the contract summary strip:
    `Avg Win | Avg Loss | Best Net | Worst Net | Best MFE | Worst MAE`
    with proper coloring + tooltips that show `n_wins`/`n_losses`
    counts.
  - Updated footer band list to mention `80-90, 90-100, 100+`.

### Backfill verification (one-time run output)
```
reading m4_trades.parquet      → 5274 rows × 74 cols
reading calibration_v2.parquet → 600 buckets × 38 cols
  → 4548/5274 trades matched a calibration bucket
  excess_over_fair_pct  mean=0.002562  median=0.001092
  theta_per_vega_combined median=3.0462
writing m4_trades_enriched.parquet
  → 5274 rows × 87 cols, 1.48 MB
```

### Sanity checks ran via curl post-deploy
- `/win_frequency?delta=0.30` → 9 contract rows summing to 121 wins ✓
- `/per_friday_best?delta=0.50` row for `2025-03-07`:
  winner=`current` $169.00, runner_up=`next` $160.48, loser=`bimonthly`
  $39.04, top decider = `theta_per_vega_put` (7.19σ) ✓
- `/winners_vs_losers?delta=0.30` returns 31 indicators × 9 rows.
  At Δ=0.30 the workhorse contracts (current/next/next_to_next/weekly/
  biweekly) show **0 discriminating indicators** at 0.5σ — the alpha is
  in cross-contract selection, not single-indicator filtering.
- `/contract_type_summary` now returns Avg Win, Avg Loss, Best/Worst
  Net, Best MFE, Worst MAE for each of the 9 contracts.
- `/expiry_grid?min_n=1` returns `iv_bands = ['<30','30-40','40-50',
  '50-60','60-70','70-80','80-90','90-100','100+']`. Cells:
  `80-90: 12 cells / 12 trades`, `90-100: 6 cells / 6 trades`,
  `100+: 0 cells` ✓

### Notable per-contract findings (now visible at-a-glance in the strip)
| Contract     | Avg Win | Avg Loss | Best | Worst   | W:L     |
|---           |---      |---       |---   |---      |---      |
| current      | +$26.15 | -$12.30  | +169 | -$58    | 354:372 |
| next         | +$24.70 | -$12.99  | +160 | -$81    | 421:293 |
| next_to_next | +$22.25 | -$21.49  | +143 | -$102   | 546:168 |
| weekly       | +$12.70 | -$17.15  | +119 | -$103   | 545:169 |
| biweekly     | +$11.14 | -$12.97  |  +84 | -$250   | 458:256 |
| three_week   | +$10.17 | -$11.73  |  +61 | -$69    | 305:247 |
| monthly      | +$10.78 | -$12.26  |  +71 | -$224   | 244:242 |
| **bimonthly**| +$11.36 | **-$42.71** | +69 | **-$1,143** | 188:430 |
| quarterly    | +$8.29  | -$17.36  |  +14 | -$31    | 9:27    |

Bimonthly's avg loss is 3-4× any other contract; tail loss -$1,143.
Workhorse contracts capped at -$103 by the 100% per-leg SL.

### Verification done in browser
Frontend + backend rebuilt and restarted. M6 page renders all new
sections; Δ chip selector responds; new IV-band rows appear; summary
strip shows all 6 new columns. No TypeScript errors in any new files.

### Known gaps carried into next session
- **Per-trade IV-premium fields not yet shown in per_friday_best
  deciding indicators by default.** They are sent when present but
  the trade itself may have null `fair_credit_at_ivp` if its
  ivp_bucket was 'nan' at entry (~14% of trades).
- **Discriminating threshold is fixed at 0.5σ** — too strict for
  workhorse contracts (0 discriminators at Δ=0.30 across 5 of them).
  Possible follow-up: surface a chip selector on the frontend so user
  can dial it down to 0.3σ or 0.25σ, or replace with Cohen's d / t-test.
- **`pattern` and `gex_regime` are categorical**, not in the 31
  numeric-indicator comparison. Could add a separate "regime
  distribution per outcome" panel later.
- **Per-Friday `deciding_indicators` are correlations only**, not
  causal — flagged in the panel footer but worth saying out loud.

---

## Session 11b (2026-05-03 evening) — M6 expiry × IV × Δ grid table

### What shipped (additive on top of Session 11)
- **Backend**: `backend/app/api/m4_results.py` — added two endpoints:
  - `GET /api/v1/m4/expiry_grid?contract_types=...&min_n=N` — returns
    flat rows of (contract_type × IV band × Δ) cells with: n, win_rate,
    sl_rate, max/min MTM (avg + extreme), gross/net P&L (avg + sum),
    slippage and brokerage round-trip avg + 50/50 per-side estimate,
    margin avg, credit_pct avg, this-expiry-ATM-IV avg.
  - `GET /api/v1/m4/contract_type_summary` — one-row-per-contract-type
    aggregation for the dashboard summary strip.
  - Classification: `_classify_contract_type(entry_ts, expiry_date)` maps
    each trade to current/next/next_to_next/weekly/biweekly/three_week/
    monthly/bimonthly/quarterly using DTE bucketing + last-Friday-of-month
    detector. IV bucketing uses the **specific expiry's own ATM IV** at
    entry, computed as avg(call_entry_iv, put_entry_iv) of the Δ=0.50 row
    for that (entry_ts, expiry_date) pair.
  - Also fixed `sl_rate` / `sl_hit_rate` to count `LegSL` (the actual
    parquet value) in addition to `SL`.
- **Frontend**:
  - `frontend/src/services/m4_api.ts` — added `fetchContractTypeSummary()`,
    `fetchExpiryGrid()`, dataclass types.
  - `frontend/src/components/m4/M4ExpiryGridTable.tsx` (NEW, ~330 LOC) —
    contract-type summary strip with per-row WR/avg-net/total-net/MFE/MAE/cost
    + checkboxes to toggle each contract in the detail table; 20-column
    sortable detail table; sticky header; "min n per cell" + "show losing
    cells" filters.
  - `frontend/src/pages/M4ResultsDashboard.tsx` — mounted `<M4ExpiryGridTable />`
    at the bottom (below the existing 4 charts). Added `height: 100%; overflowY: auto`
    so the page scrolls.
  - `frontend/src/pages/LiveSignalDashboard.tsx` — same scroll fix.

### Per-contract-type findings logged
- next-to-next (~2.8d): 76.5% WR, +$11.96/trade, **+$8,537 total** — best contract
- next (~1.8d): 59.0% WR, +$9.23/trade, +$6,592
- current (~0.8d): 48.8% WR, +$6.45/trade, +$4,682
- weekly (~7d): 76.3% WR, +$5.64/trade, +$4,025
- biweekly (~14d): 64.1% WR, +$2.49/trade, +$1,778
- three-week / monthly: marginal (+$0.37 / -$0.69 avg)
- **bimonthly (~52d): 30.4% WR, -$26.26/trade, -$16,230 — drags the book down**
- quarterly: -$10.95/trade, only 36 trades

Action rule: skip all expiries ≥30 DTE; trade next-to-next + weekly with
Δ 0.30 in IV 50-70%.

### Known limitations
- M4 cost columns are **round-trip totals only** (no entry/exit split). The
  `Slip ½` / `Brk ½` columns in the new table are 50/50 estimates. True split
  needs re-running the M4 batch backtester with per-side capture from
  trade_simulator. Per-job backtester (Backtest mode) already has true splits.
- IV-premium decomposition (`fair_credit_at_ivp`, `excess_over_fair_pct`)
  not baked into m4_trades — would need a calibration_v2 join in the
  expiry_grid endpoint to surface in this view.

### Files modified / added
- backend: `app/api/m4_results.py`
- frontend (new): `components/m4/M4ExpiryGridTable.tsx`
- frontend (modified): `pages/M4ResultsDashboard.tsx`, `pages/LiveSignalDashboard.tsx`,
  `services/m4_api.ts`

---

## Session 11 (2026-05-03) — LiveSignal page + M6 batch results dashboard + cleanup

### What shipped
- **Phase 1 — LiveSignal backend**
  - `backend/app/services/live_signal_compute.py` (NEW, ~280 LOC). `scan_live_candidates()` enumerates all live expiries × 6 deltas, picks closest-Δ CE+PE legs from the in-memory `ticker_store` chain, builds SELL strangles (qty=100), runs them through `compute_trade_analytics`, stitches v2 calibration `pattern_winrate`/`overall_winrate`/`n_trades`, and tags hard-filter flags (IVP>50, IV-RV>0, ADX<30, DTE 5–14, GEX OK). Returns ranked list by quality_score desc.
  - `backend/app/api/live_signal.py` (NEW, ~90 LOC). `GET /api/v1/live-signal/scan` with 5s server-side response cache. Curl-tested: 54 candidates, top quality_score 21.31, source `calibrated_v2`.
  - `backend/tests/test_live_signal.py` (NEW, 15 tests). Synthetic ticker_store + mocked analytics + calibration. Verifies enumeration, ranking, hard-filter flags, v2 fields, "Other" fallback for unknown patterns, max_expiries cap, JSON serialization.
- **Phase 2 — LiveSignal frontend**
  - `frontend/src/services/live_signal_api.ts` (NEW, ~95 LOC). Fetch helper + `useLiveSignalScan` polling hook (7s default).
  - `frontend/src/pages/LiveSignalDashboard.tsx` (NEW, ~290 LOC). Header strip (spot, scan stats, refresh), "Best now" card with full quality decomposition + hard-filter chips, sortable candidates table, only-passing toggle.
  - `frontend/src/App.tsx` — `LIVESIGNAL` mode + 4th toggle button.
- **Phase 3 — M6 backend**
  - `backend/app/api/m4_results.py` (NEW, ~290 LOC). 6 endpoints: `/summary`, `/trades` (paginated, sortable, filterable), `/aggregate` (multi-dim group-by, 9 metrics), `/scatter` (any 2 numeric cols), `/path` (per-trade hourly snapshots), `/quality_calibration` (per-credit-pct decile win rate). Filters cover delta, DTE, spot, IVP, pattern, outcome, exit reason, hard-filter flags. Module-level cache for parquets.
  - `backend/tests/test_m4_api.py` (NEW, 13 tests). Synthetic m4_trades + paths injected into module cache. Confirms: filter, pagination, sort, multi-dim aggregate, scatter, path fetch + 404, quality calibration. trade_id round-trips as string (avoids JS uint64 precision loss).
- **Phase 4 — M6 frontend**
  - `frontend/src/services/m4_api.ts` (NEW, ~120 LOC). Fetch helpers for all 6 endpoints.
  - `frontend/src/components/m4/M4WinrateHeatmap.tsx` (NEW, ~140 LOC). CSS-grid 2D heatmap, red→amber→green scale, hover tooltip (n=).
  - `frontend/src/components/m4/M4PatternBars.tsx` (NEW, ~75 LOC). Recharts BarChart, color-coded by pattern letter.
  - `frontend/src/components/m4/M4ScatterChart.tsx` (NEW, ~85 LOC). Recharts ScatterChart, color = win/loss.
  - `frontend/src/components/m4/M4QualityCalibrationCurve.tsx` (NEW, ~80 LOC). Recharts ComposedChart, win-rate per credit_pct decile.
  - `frontend/src/pages/M4ResultsDashboard.tsx` (NEW, ~165 LOC). Header strip (8 KPIs), filter bar (Δ, DTE bucket, pattern, outcome, exit reason, DTE 5-14 hard filter), 4 charts in 2-column grid, all reactive to filters.
  - `frontend/src/App.tsx` — `M4_RESULTS` mode + 5th toggle button.
- **Phase 5 — Cleanup**
  - `backend/app/api/historical.py` `/calibration` endpoint now prefers v2 parquet and surfaces `overall_winrate`, `n_trades`, `z_winners_mean/std`, `pattern_winrate` (parsed JSON), `expectancy_per_credit_pct`, `sl_hit_rate` when v2 has data for the bucket. v1 keys still present so the legacy shape is unchanged.
  - `backend/tests/test_calibration_api.py` updated to also patch the new `CALIBRATION_V2_PATH` (otherwise it picks up the real v2 file on disk and skips the v1 stub).
  - `backend/app/services/live_recorder.py` — added `_mark_msgs` / `_oi_msgs` counters and clarifying comment on why `oi_*` columns are NaN. Delta's `candlestick_1m` channel only emits MARK candles even when OI symbols are subscribed; populating OI requires a separate `v2/ticker` subscription that buckets `oi_contracts` updates into 1m bars. Documented for follow-up; no rewrite this session because the recorder is live and the scope warrants its own design pass.

### Verified end-to-end (this session)
- `/api/v1/live-signal/scan?top_n=3` → 200, returns ranked candidates with `quality_source='calibrated_v2'`, `pattern_winrate`, `overall_winrate`, `n_trades_in_bucket`, `flt_*` flags
- `/api/v1/m4/summary` → 5274 trades, 58.21% win rate, $8,859 net
- `/api/v1/m4/aggregate?dimension=dte_bucket&dimension=delta_target&metric=win_rate` → 36 cells; sweet spot 3-7d × 0.15-0.25Δ at 78–81% win rate (matches expectation)
- `/api/v1/m4/quality_calibration?n_buckets=5` → monotonic increase 56% → 66% then dips at the top decile (likely ATM SL-heavy trades)
- `/api/v1/historical/calibration?dte=7&spot=100000&delta_target=0.10&ivp=70` → now returns `overall_winrate=0.83`, `pattern_winrate={"C":1.0,"D":1.0,"Other":0.5}`, `n_trades=6`
- All 43 tests across the affected suites pass (live_signal 15 + m4_api 13 + calibration_api 7 + backfill 5 + trade_simulator 7 — was 0/15 before regress fixes; added v2-path patches to calibration_api tests)
- `npx tsc --noEmit` green for all new TS (existing BacktestForm errors pre-date this session, not mine)

### Files created (10 backend / 9 frontend)
- backend: live_signal_compute.py, api/live_signal.py, api/m4_results.py, tests/test_live_signal.py, tests/test_m4_api.py
- frontend: services/live_signal_api.ts, services/m4_api.ts, pages/LiveSignalDashboard.tsx, pages/M4ResultsDashboard.tsx, components/m4/{M4WinrateHeatmap,M4PatternBars,M4ScatterChart,M4QualityCalibrationCurve}.tsx

### Files modified
- backend: main.py (router registrations), api/historical.py (v2 in /calibration), services/live_recorder.py (counters/comment), tests/test_calibration_api.py (v2 path patch)
- frontend: App.tsx (2 new modes)

### Pending / future work
- C1 OI capture rewrite: needs separate `v2/ticker` subscription that aggregates `oi_contracts` updates into 1m bars. Decision pending: minimum-viable patch in recorder vs. larger refactor splitting MARK and OI flows.
- C3 `_simulate_day` → `simulate_trade_path` refactor: still pending. Both paths working independently. Low priority.
- Live recorder OI streaming + nightly merge sanity check.
- v3 walk-forward / time-decayed `pattern_winrate`.

---

## Session 10 (2026-05-03) — M2/M3/M5v1 backfill + M4 batch backtester + M5 v2 enrichment

### Pipeline data built
- M2 backfill: 859 expiries / 4.6h (with per-expiry checkpoint to survive container restarts). Output: 4 grids 49–104 MB.
- M3 backfill: 30s. Output: 4 grids 65–367 MB, 316 cols.
- M5 v1 calibration: 25 min. 600 buckets, 30 universal.
- M4 batch backtester: 6.4h. 5,274 trades, 49,475 path snapshots. Win rate 58.2%.
- M5 v2 enrichment: 2.1s. 600 buckets v2 (450 with M4 data).

### Code shipped
- `backend/app/services/trade_simulator.py` (NEW, ~430 LOC) — extracted `simulate_trade_path()` from `_simulate_day`. Reusable bar-walk + per-leg SL + cost + margin + optional path snapshots. Used by both M4 and (planned) future per-job refactor.
- `backend/app/analytics/m4_batch_backtester.py` (NEW, ~430 LOC) — Friday 23:00 IST × all live expiries × 6 deltas × 100 lots/leg. Exit Sat 10:00 IST or earlier on per-leg 100% loss. Outputs `m4_trades.parquet` + `m4_paths.parquet`.
- `backend/app/analytics/backfill_attribution.py` (NEW, ~155 LOC) — M5 v2 enricher. Per-bucket `pattern_winrate` (JSON), `z_winners_mean/std`, `expectancy_per_credit_pct`, `sl_hit_rate`. Writes `calibration_v2.parquet` as left-join superset of v1.
- `backend/tests/test_trade_simulator.py` (NEW, 7 tests).
- `backend/tests/test_backfill_attribution.py` (NEW, 4 tests).
- `backend/app/analytics/enrich_options.py` — M2 per-expiry checkpoint (atomic .tmp+rename, `--clear-checkpoint` flag). Allowed M2 to recover from a SessionStart-hook-triggered kill at 53% without losing work.
- `backend/app/services/strangle_analytics.py` — auto-detect v2 calibration. `_load_calibration` prefers `calibration_v2.parquet`. `lookup_calibration` surfaces v2 cols. `compute_trade_analytics` adds v2 quality formula path (`0.25·z_all + 0.30·z_winners + 0.30·IVP + 0.15·pattern_winrate`) before falling back to v1, then to fallback. `quality_source` reflects the path.
- `frontend/src/types/backtest.ts` — `quality_source` enum gains `'calibrated_v2'`.

### Verified end-to-end
After backend rebuild: calibration loaded from V2 (38 cols), `lookup_calibration` returns v2 fields including `pattern_winrate` and `n_trades`, `compute_trade_analytics` returns `quality_source: 'calibrated_v2'`. Live recorder running and writing 488 symbols × MARK + OI to `data_live/` (507 files within 35s of restart).

### Win-rate observations from M4 (cross-trade pattern detection working)
- By delta: 0.05Δ=59%, 0.10Δ=60%, 0.15Δ=61%, 0.25Δ=60%, 0.30Δ=58%, 0.50Δ=50%. Sweet spot 0.10–0.25Δ; ATM has highest gamma risk.
- By DTE: 3–7d=76% (sweet), 7–14d=64%, 0–3d=61%, 14–30d=54%, 30–60d=37%.
- 0.05Δ wings have negative avg P&L due to cost/credit ratio — confirms "selling tiny wings is bad economics".

### Pending / next session
- LiveSignal page (separate plan): hybrid backend (slow cols from M3 + fast from ticker_store) + new dashboard. ~1000 LOC.
- Refactor `_simulate_day` to call `simulate_trade_path()` (deferred from plan step 1; needs equivalence test first).
- `/historical/calibration` endpoint hasn't been updated to surface v2 cols in the response shape; backend `compute_trade_analytics` uses v2 internally so trade rows correct.

### Commits
- `58d67c2` — M2 per-expiry checkpoint
- `847da38` — M4 + M5 v2 + analytics auto-detect
- `bd05f94` — backfill_attribution unit tests
- `d9e3772` — frontend `calibrated_v2` enum

---

## Session 9 (2026-05-02 PM) — Live WS recorder + nightly merge

### What was done
Built end-to-end live data capture from Delta's WS, separate from the existing
REST collector at `/mnt/c/Users/Abhis/btc-collector/`. Goal: 1-min OHLC for
both **MARK** and **OI** for every option in ATM±40 across all live expiries
(plus spot), plus a nightly merge that folds live writes into the main data
tree so the rest of the platform sees fresh data with no other changes.

**New files:**
- `backend/app/services/live_recorder.py` — ~400 LOC. `candlestick_1m`
  WS subscriber (both MARK: and OI: prefixes) + per-symbol bar-close detector
  + 30s flush-cadence parquet writer. Discovery loop refreshes subscriptions
  on (a) 5-min heartbeat, (b) immediate when |Δspot| > $2k, (c) 1h full
  product refresh for new expiries.
- `backend/app/services/merge_live_to_main.py` — ~250 LOC. Idempotent
  consolidator (dedupe on `timestamp_unix`, sort, atomic write+rename).
  Archives live files under `data_live/archive/<YYYY-MM-DD>/` for 7-day
  rollback. Self-scheduled background loop (runs if last-merge >20h ago,
  rechecks hourly). Also CLI: `python -m app.services.merge_live_to_main
  [--dry-run|--status]`.
- `backend/tests/test_live_recorder.py` — 10 unit tests passing.
- `backend/tests/test_merge_live_to_main.py` — 6 unit tests passing.

**Files modified:**
- `backend/app/main.py` — `lifespan` launches recorder + merge scheduler
  alongside `run_delta_ws`, stops cleanly.
- `docker/docker-compose.yml` — `data` mount made writable; new `data_live`
  + `logs` mounts.

**Output dirs (pre-created):**
- `/home/abhis/btc-data/data_live/{spot,options,archive}/`
- Schema matches `btc-collector/parquet_writer.py` byte-for-byte so
  pa.concat_tables works in the merge.

### Key decisions
- **One-file recorder** (~400 LOC vs split into 3 modules). Keeps WS handler,
  writer, and discovery cohesive; aligns with project's CLAUDE.md "concise"
  preference.
- **Buffer = 0**: ATM±40 is what we subscribe to AND what we persist. Sharp
  moves handled by the spot-triggered re-discovery (`|Δspot|>$2k`), not by a
  wider subscribe band. Keeps WS subs at minimum.
- **`pq.ParquetFile(path).read()` vs `pq.read_table(path)`**: bypasses
  pyarrow's hive-partition column auto-detection from the
  `expiry=.../strike=.../` directory names. Without this fix, every read
  injected phantom `expiry`/`strike` columns into the schema and broke
  `pa.concat_tables`.
- **Bar-close detection**: only persist bars where a NEWER `candle_start_time`
  has been seen — prevents writing in-progress bars.
- **`data_live/` separate from `data/`**: avoids concurrent-write conflicts
  with the REST collector. Nightly merge folds live → main.
- **`ticker_store`-based LiveSignal architecture (locked in, not built yet)**:
  use the latest M3 row for slow-moving cols (IVP, RV, ADX, pattern, vrp_pct);
  recompute fast-moving values (spot, ATM IV, skew, GEX) on-the-fly from the
  existing live chain in `ticker_store`. No incremental enrichment loop
  needed — saves ~400 LOC.

### Open / next
- **M2 backfill still running** (~225/849 expiries at session pause; ETA ~4h).
  Restart of backend container is blocked on this — restart kills the M2
  process and forces re-running ~1.5h of work. User chose to wait.
- After M2: `docker compose up --build -d backend` to restart with recorder
  active. First bars in `data_live/` ~60s later.
- **Tail gap (Apr 22 → today)**: `python main.py resume` exits without
  fetching because workers skip blindly on registry `done` status; `backfill-
  all` only handles early-lifetime gaps. User said they'll handle the tail
  fill themselves; no `tail_fill.py` written.
- LiveSignal page still pending (~1000 LOC plan locked in).
- M4 batch backtester still pending (the original spec; ~1500 LOC).

### Lessons
- Always use `ParquetFile(...).read()` for files inside hive-partitioned
  trees when the partition cols are NOT actual stored columns. The default
  `pq.read_table` triggers dataset discovery that adds them.
- Python's `round()` is banker's rounding (round half to even); avoid testing
  the .5 boundary in unit tests for ATM rounding helpers.

---

## Session 8 (2026-05-02) — Module 3: derived metrics + pattern detection

### What was done
Implemented Module 3. Plan: `/home/abhis/.claude/plans/sparkling-pondering-plum.md`.

**New files:**
- `backend/app/analytics/enrich_derived.py` — ~400 LOC pipeline. Joins M1's
  spot_enriched.parquet + M2's options_enriched_5m.parquet on
  timestamp_unix; computes Spec Section 5 derived metrics (VRP family,
  expected move, vol-of-vol); applies pattern detection (A/B/C/D/Other)
  per build prompt's "move pattern detection here" directive. Writes 4
  output grids at 1m/5m/15m/30m matching M2's pattern.
- `backend/tests/test_enrich_derived.py` — 13 unit tests, all passing.

**Output**: `/home/abhis/btc-data/derived/full_enriched_{1m,5m,15m,30m}.parquet`
- ~310 columns per row (M1 + M2 + new derived + pattern)
- Sizes: 1m ~1.2 GB, 5m ~250 MB, 15m ~85 MB, 30m ~45 MB
- IV stays decimal fractions; RV from M1 stays percent (converted on the
  fly in the spread/ratio formulas)

### Key decisions
- 4 grids (1m/5m/15m/30m) matches M2's pattern; native compute at 5m
- Pattern detection uses M1+M2 columns directly (`ivp_4h`, `spot_ret_1d`,
  `adx_14_4h`); priority order A→B→C→D→Other
- pandas + pd.read_parquet (no DuckDB needed for the join — both inputs
  are parquets, in-memory join is fine at 240k × 310-col scale)
- Idempotent append + overwrite-last-1-day (95-day warm-up read for VRP
  90d percentile)

### Open / next
- E2E verification blocked on M2 backfill (was ~6% at end of session;
  ETA 4h). Once M2 finishes, run `python -m app.analytics.enrich_derived
  --rebuild` for M3 backfill (~5-10 min).
- Commit + push (awaiting user approval per CLAUDE.md Rule #1).
- Plan Module 4 (strangle backtest engine) in fresh session after M3 is verified.

---

## Session 7 (2026-05-02) — Module 2: options enrichment pipeline

### What was done
Implemented Module 2. Plan: `/home/abhis/.claude/plans/sparkling-pondering-plum.md`.

**New files:**
- `backend/app/analytics/enrich_options.py` — ~900 LOC pipeline computing
  Spec Section 4 metrics from the 1-min options parquets. Stages:
  - A) per-expiry chain bulk-load + per-5m-bar summary (ATM IV, OI, max-OI
       strikes, top-30 skew/GEX strikes with vectorized Greek compute)
  - B) cross-expiry aggregation per snapshot (const-maturity interp, term,
       OI walls, GEX sum, strangle synthetic IV)
  - C) rolling 90d IVP per tenor + multi-TF IVP (1m/5m/15m/30m/1h/4h/1d)
  - D) write 4-grid parquets (1m via ffill, 5m native, 15m/30m end-of-bucket)
  - Vectorized BS price + IV solver + gamma helpers (numpy bisection)
- `backend/tests/test_enrich_options.py` — 15 tests, all passing.

**Output**: `/home/abhis/btc-data/derived/options_enriched_{1m,5m,15m,30m}.parquet`
- 4 grids at 1m / 5m / 15m / 30m sampling
- ~50 columns per row (constant-maturity ATM IV, IVP per tenor + multi-TF,
  skew + RR/BF/wing-atm, term slopes, OI walls + PCR, total GEX + regime,
  strangle synthetic IV + IVP)
- IV stored as decimal fractions (0.55 = 55%) for greeks.py consistency

### Key decisions (locked in plan)
- Compute natively at 5m, output 4 grids via ffill/end-of-bucket
- pandas + DuckDB (matches M1)
- IV/IVP/skew/GEX live HERE; spot indicators live in M1 (M3 will join)
- 1m granularity not actually computed — options metrics don't move at 1m
- gex_flip_level: NaN in v1 (proper computation = 21-point grid, v2 work)
- gex_per_strike nested column dropped (summary cols sufficient for backtest)
- pcr_volume: NaN (no volume column in options parquets)
- Constant-maturity outside expiry range: NaN (no extrapolation)

### Performance
- Smoke run (2 days, 10 expiries): 60s
- Full backfill: kicked off as background task ~11:25 UTC; estimated ~7 hours
  for 880 days × 849 expiries. Watch `/tmp/m2_backfill.log`.

### Open / next
- Full backfill verification once it completes
- Commit + push (only after user reviews backfill results)
- Plan Module 3 (joins M1+M2, adds VRP family / expected move / pattern detection)

---

## Session 6 (2026-05-02) — Module 1: spot enrichment pipeline

### What was done
Implemented Module 1 of the short-strangle backtest spec
(`UI ss/new feature/SHORT_STRANGLE_INDICATORS_SPEC.md`, plan at
`/home/abhis/.claude/plans/sparkling-pondering-plum.md`).

**New files:**
- `backend/app/analytics/__init__.py` — package marker for the new analytics layer
- `backend/app/analytics/enrich_spot.py` — pipeline reading 1m spot parquet,
  bucketing to 5m, computing ~245 columns of price-only indicators across
  7 timeframes (1m/5m/15m/30m/1h/4h/1d). Hand-rolled in pandas+numpy:
  Returns, RV (close/Parkinson/Garman-Klass), Wilder ATR/RSI/ADX, MACD,
  Bollinger, Stochastic, CCI, Williams %R, ROC, Donchian, Keltner, SuperTrend,
  Aroon. 1m timeframe computes only Returns/ATR/RSI/ROC (slow smoothers skipped
  as too noisy on 1m bars). Cross-TF metrics: RV at 24h/7d/14d/30d windows,
  RVP at 15m/30m/1h/4h/1d (90-day percentile rank), atr_compression_ratio
  (Wilder ATR(30, 4H)/ATR(180, 4H)), MA20/50/200 distance % (daily MAs
  forward-filled), day_of_week/hour_of_day_ist/is_weekend.
- `backend/tests/test_enrich_spot.py` — 21 unit tests on synthetic flat/step/
  random-walk fixtures, all passing.

**Modified files:**
- `backend/requirements.txt` — added `pyarrow`, `pytest`
- `docker/docker-compose.yml` — split data mount: `data:ro` for raw,
  `derived/` writable for the pipeline output. Added `tests:ro` mount
  and live `app:ro` mount so editing source files inside backend/app/
  reflects in the container without rebuild.

**Output:** `/home/abhis/btc-data/derived/spot_enriched.parquet`
- 246,171 5m rows × 245 cols ≈ 150 MB
- Time range 2023-12-18 → 2026-04-21
- Pipeline runtime: ~16s full rebuild, ~10s incremental

### Idempotency
Re-running incrementally re-reads warm-up of last 35 days, recomputes the
last 1 day + any new bars, drops overlapping rows from the existing output,
appends fresh tail. Verified: row count stable across runs.

### Key decisions (locked in plan)
- pandas + DuckDB (not Polars); matches rest of project
- All IVP/ATM IV/skew/GEX/OI NOT in this module — Module 2
- IST timestamps naive (matches raw 1m parquet) — no tz arithmetic in joins
- Output path: `/home/abhis/btc-data/derived/` (alongside raw, out of git)

### Remaining (Modules 2-6)
- M2: `enrich_options.py` — chain-based per-snapshot metrics
- M3: `enrich_derived.py` — joined VRP/expected-move + pattern detection
- M4: `strangle_backtest.py` — 110-col per-trade engine
- M5: calibration + attribution backfill
- M6: backtest dashboard + live signal frontend

---

## Session 5 (2026-04-30 → 2026-05-01, overnight) — Margin model calibration & safety buffer

### What was done
- **Established hard rule:** margin model output must NEVER be below Delta's actual ARM
  (the "Order Margin" in UI). Saved as `feedback_margin_safety_bias.md` in user memory.
- **Added flat 20% safety buffer** to both engines:
  - `scripts/margin_engine.py` — `SAFETY_BUFFER_PCT = 0.20` constant + applied on
    final `portfolio_margin` line ~313.
  - `frontend/src/utils/marginEngine.ts` — same constant + same application site.
- **Built v2 calibration grid:** `scripts/calibrate_v2.py` runs 7 expiry buckets
  × 6 deltas × 13 lot sizes (546 scenarios per run), comparing `our_pm` against
  Delta's `delta_arm` (the field that matches UI charge — NOT `portfolio_margin`
  which is gross). Output: `scripts/calibration_v2_history.csv`.
- **Calibration loop:** `scripts/calibrate_loop_v2.sh` runs every 15 min for 24h.
  Restarted at 21:44 IST 2026-04-30 (PID `/tmp/calib_v2_loop.pid`).
- **Friday-overnight backtest:** `scripts/friday_overnight_pnl.py` produces
  `friday_overnight_pnl.xlsx` — 13 Fridays Jan-Mar 2026 × 4 lot sizes, full cost
  model (slippage + brokerage + margin engine + Greeks via `app/core/greeks.py`).
  Summary sheet + 13 per-Friday per-minute MTM detail sheets.

### Key discovery — wrong calibration target was being used
Earlier calibration measured against `portfolio_margin` (gross field) which gave
median |error| ~12% with 91% within ±30%. After UI verification, switched to
`additional_required_margin` (ARM) which is what Delta actually charges. Same
underlying model now shows median |error| 11.6%, mean signed error +1.4%.
Pre-buffer "ratio" column in CSV is delta_pm/our_pm; the right ratio is
delta_arm/our_pm (computed at analysis time).

### UI verification (2026-04-30, 8-May δ=0.10 strangle)
With 20% buffer applied, 5 of 6 lot sizes are at-or-above Delta's UI charge.
Only edge case: 500 lots is 2.9% under ($11 absolute). User explicitly accepted
this as acceptable.

### What needs doing next
- Wait for 24h calibration to complete (~21:44 IST 2026-05-01) to get full dataset.
- Refit shock-span ramp slopes + DTE constants from full grid to close the
  long-DTE far-OTM structural gap (currently bandaged by +20% global buffer).
- ⚠️ **CSV file lock** — `scripts/calibration_v2_history.csv` is throwing
  PermissionError on write. Probably held open by Windows side (Excel/OneDrive sync).
  Until resolved, every calibration run will fail at the write step.
- ⚠️ **Delta API IP whitelist** — current WSL IP is 103.121.72.88, the whitelisted
  IP is different. Live ARM calls fail with `ip_not_whitelisted_for_api_key`.
  User must update IP on Delta's dashboard for the loop to collect live ARM data.
- **TS engine already has the safety buffer in HEAD** (commit `d79686c`). My edits
  this session were no-ops. The Python engine + scripts/ dir is the untracked work
  that needs `git add scripts/` before any commit.

---

## Session 4 (2026-04-30, later) — Multi-day Backtester end-to-end
- **Status:** Major feature delivery + persistence layer + slippage parity fix.
- **Built:** AlgoTest-style multi-day backtester
  - Backend: `backtest.py` (day-loop simulator), `backtest_jobs.py` (asyncio cancel events),
    `api/backtest.py` (POST/GET/DELETE), `option_data.py` (DuckDB helpers + strike resolvers
    for Strike Type / Closest Premium / Closest Delta), `costs.py` (slippage + brokerage,
    Python port of `slippage.ts`/`brokerage.ts`), `margin_v2.py` + `margin_engine_v2.py` +
    `margin_engine_v2_constants.json` (in-container portfolio margin, used per trade)
  - Frontend: 3-way App mode toggle (Live/Historical/Backtest), `BacktestDashboard`,
    `components/backtest/*` (Form, EquityChart, DailyPnlBars, StatsPanel, TradeLogTable,
    ProgressBar), `services/backtest_api.ts`, `types/backtest.ts`
  - Pattern: async-job submit + 1Hz polling, in-memory job registry, in-process cancel
- **Built:** Persistence layer
  - `hooks/usePersistedState.ts` — localStorage-backed `useState`
  - Historical: `simulationDate/Time`, `selectedExpiry`, `strategyMode`, MTM data
    (`buildMtmData/LegGreeks/ExitLegData/MaxPnlExitData/AtmData`) all persist across mode
    switches & reloads. Reset-on-legs-change effect skips first render.
  - Backtest: form state (legs, entry/exit times, weekday mask, costs) + completed
    `status` persist; result NOT written during running (`status === 'done'` only).
  - Named save/load/delete strategy UI in both Historical and Backtest pages.
- **Built:** Auto-state reset on backend restart
  - `backend/app/main.py` — `SESSION_ID = uuid.uuid4().hex` at startup, `GET /api/v1/session-id`
  - `frontend/src/utils/sessionGuard.ts` — fetched in `main.tsx` BEFORE React mount;
    wipes `historical:*` + `backtest:*` localStorage keys when ID changes (preserves
    named saves like `historical:strategy:<name>`)
- **Fixed:** $4 backtest slippage vs $2 historical slippage for same strangle
  - Root cause: `_moneyness_mult()` in `backend/app/services/costs.py` returned 1.6 for
    ~13% OTM strikes; frontend `slippage.ts` had this multiplier removed on 2026-04-30
    per real-fill calibration but the Python port wasn't synced.
  - Fix: `_moneyness_mult()` now always returns 1.0 (no-op stub). Backtest matches
    historical viewer to the cent ($2.00 vs $2.00).
- **Fixed:** Closest-delta strike picker uses `get_mark_at_or_before` (exact timestamp),
  eliminating up-to-4-minute drift between picker and entry mark.
- **Fixed:** `resolve_expiry("weekly")` was returning None when next Friday equaled the
  monthly expiry (skipped 16/60 days silently). Now uses Friday-of-week date matching.
- **Enhanced:** Trade log table
  - Split CE/PE into separate columns: `CE Leg / CE Entry / CE Exit / PE Leg / PE Entry / PE Exit`
  - Added `Max MTM @ time / Max Net / Min MTM @ time / Min Net` (Max/Min Net = net P&L if
    exited at peak/trough, with brokerage recomputed at those marks)
  - Backend samples 1m bars across the hold to track per-leg marks at peak/trough
- **Files Touched:**
  - Backend (new): `app/api/backtest.py`, `app/services/{backtest,backtest_jobs,costs,option_data,margin_v2,margin_engine_v2}.py`, `app/services/margin_engine_v2_constants.json`
  - Backend (modified): `app/main.py` (session ID + endpoint)
  - Frontend (new): `pages/BacktestDashboard.tsx`, `components/backtest/*` (5 files), `hooks/usePersistedState.ts`, `utils/sessionGuard.ts`, `services/backtest_api.ts`, `types/backtest.ts`
  - Frontend (modified): `App.tsx`, `main.tsx`, `pages/HistoricalDashboard.tsx`, `components/historical/StrategyPanel.tsx`
  - Docs: `CLAUDE.md`, `HANDOFF.md`, `docs/memories/work_log_claude.md`, `docs/memories/current_state.md`
- **Restart:** Both backend (rebuilt) and frontend restarted multiple times during session; ended in running state.

## Session 3 (2026-04-30)
- **Status:** Built data-driven slippage v2 (parallel, NOT integrated) + added
  Exit & Peak Marks sheet to MTM downloads.
- **Slippage v2:**
  - Extracted 386 fills from 3 Delta-TransactionLog CSVs, joined to 1-min
    parquet → `Back Testing/fills_with_features.csv`.
  - Fit additive model `(FIXED/qty_btc + LINEAR × dte_factor) × hour × weekend`
    on 159 clean SELL fills (median-absolute-residual objective). Output:
    `Back Testing/slippage_calibration.json`.
  - Wrote `frontend/src/utils/slippage_v2.ts` (mirror of slippage.ts API,
    NOT imported anywhere). Per-fill bias improves 5× over current
    ($2.53/BTC over → $0.50/BTC over); per-fill win rate is ~52/48.
  - Side-by-side comparison: `Back Testing/slippage_comparison.csv`.
  - **Awaiting user decision** on whether to integrate. When approved, swap
    import in `StrategyPanel.tsx` from `./slippage` → `./slippage_v2`.
- **Excel download:** Added "Exit & Peak Marks" sheet to both `downloadExcel`
  (build) and `downloadCompareExcel` (compare). One row per leg with entry/exit/
  peak-strategy-MTM mark + spot + per-leg P&L at each point. Uses existing
  `buildExitLegData` + `buildMaxPnlExitData` (and compare equivalents).
- **Files Touched:** `frontend/src/components/historical/StrategyPanel.tsx` (M);
  `frontend/src/utils/slippage_v2.ts` (new); `Back Testing/*.py` (new, outside repo).
- **`slippage.ts` was NOT modified.**
- **Restart:** Frontend restarted on port 3000 at session end.

## Cumulative Work (from git log)
- **f292686:** Prioritize live Greeks from Delta API, BS as fallback.
- **2fa6207:** Gamma precision 8 decimal places (live + historical).
- **6df18d3:** Gamma precision 5 decimal places (historical chain).
- **d280dde:** Hybrid Bisection-Newton IV solver for OTM Greeks.
- **19eaae3:** Dynamic expiry filtering from actual parquet data.

## Session 2 (2026-03-13)
- **Status:** Bug fix + handoff system setup.
- **Fixed:** Reverted Gemini's broken mid-refactor of `HistoricalDashboard.tsx` — removed init logic had broken historical simulation
- **Setup:** Created full AI handoff system (CLAUDE.md checklist, HANDOFF.md, docs/memories/)
- **Committed:** `9e6020e`, `c7bf66d`
- **Files Touched:** CLAUDE.md, HANDOFF.md, docs/memories/* (all new)

## Session 1 (2026-03-13)
- **Status:** Analysis only, zero code changes.
- **Explored:** Full architecture (live data, historical simulation).
- **Discussed:** Options for historical auto-play (SSE / setInterval / WebSocket).
- **Files Touched:** None.
