# Claude's Work Log

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
