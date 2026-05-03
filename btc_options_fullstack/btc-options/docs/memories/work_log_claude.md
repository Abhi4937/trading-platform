# Claude's Work Log

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
