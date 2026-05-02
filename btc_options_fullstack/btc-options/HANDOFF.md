# Handoff Log

## Last Session
**Who:** Claude
**Date:** 2026-05-02 (Live WS recorder + nightly merge — code-only, not yet running)
**Branch:** `mainbranch-gemini_claude`
**Status:** Recorder + merge code complete + 16 tests passing. Backend NOT yet
restarted because M2 backfill (Session 7's leftover) is still mid-run. Once M2
finishes, restart backend → recorder begins capturing 1-min mark+OI bars to
`/home/abhis/btc-data/data_live/`.

---

## What Was Done — 2026-05-02 (Session 9: live WS recorder + nightly merge)

### Files created
- `backend/app/services/live_recorder.py` — ~400 LOC. WS subscriber that
  captures **1-min OHLC for both MARK and OI** for every option in the
  ATM±40 band across all live expiries, plus spot. Architecture:
  - **Discovery** loop: REST `/v2/products` every 5 min (heartbeat) AND
    immediately on `|spot_atm − last_atm| ≥ $2k` AND every 1h for new-expiry
    refresh. Diffs target subscription set against current → sub new, unsub
    expired.
  - **WS subscriber** uses `candlestick_1m` channel against
    `wss://socket.india.delta.exchange`. Subscribes to both `MARK:` and
    `OI:` prefixes for every option. Reconnect-with-backoff loop.
  - **Bar-close detection**: per-symbol in-flight bar tracked; when a newer
    `candle_start_time` arrives, the previous bar is closed and pushed to
    the writer queue. Prevents persisting in-progress bars.
  - **Writer**: in-memory ts→row buckets, flushes every 30s to parquet via
    append+dedupe-on-`timestamp_unix`+sort. Uses `pq.ParquetFile().read()`
    to bypass hive-partition column auto-detection from the
    `expiry=.../strike=.../` directory names.
  - Toggle via env `BTC_LIVE_RECORDER=0`.

- `backend/app/services/merge_live_to_main.py` — ~250 LOC. Nightly job that
  consolidates `data_live/ → data/` so the existing pipeline (M1/M2/M3,
  backtester, historical chain) sees fresh data. Idempotent: dedupe on
  `timestamp_unix`, sort, atomic write+rename. Archives merged live files
  under `data_live/archive/<YYYY-MM-DD>/` for 7-day rollback. Self-scheduled
  via `schedule_loop()` background task (runs if last merge >20h ago,
  re-checks hourly). Also runnable as CLI:
  ```
  docker compose exec backend python -m app.services.merge_live_to_main
  docker compose exec backend python -m app.services.merge_live_to_main --status
  docker compose exec backend python -m app.services.merge_live_to_main --dry-run
  ```

- `backend/tests/test_live_recorder.py` — 10 tests (symbol parsing,
  ATM rounding, writer append+dedupe, recorder bar-roll close logic, spot
  handling). All passing.
- `backend/tests/test_merge_live_to_main.py` — 6 tests (create-when-main-
  missing, dedupe-overlap-keeps-live, dry-run safety, archive-subtree-skip,
  state-file-records-last-run, archive-pruning-7d). All passing.

### Files modified
- `backend/app/main.py` — `lifespan` now starts `live_recorder` +
  `merge_schedule_loop` background tasks alongside `run_delta_ws`. Stops
  cleanly on shutdown.
- `docker/docker-compose.yml` —
  - `data` mount switched from `:ro` → writable (so the nightly merge can
    write into it).
  - New mount: `/home/abhis/btc-data/data_live:/home/abhis/btc-data/data_live`
  - New mount: `/home/abhis/btc-data/logs:/home/abhis/btc-data/logs`

### New output dirs (pre-created)
```
/home/abhis/btc-data/data_live/spot/
/home/abhis/btc-data/data_live/options/
/home/abhis/btc-data/data_live/archive/
```
Schema matches `btc-collector/parquet_writer.py` exactly:
- spot: `timestamp_ist + timestamp_unix + mark_o/h/l/c + ltp_volume + oi_o/h/l/c`
- options: `timestamp_ist + timestamp_unix + mark_o/h/l/c + oi_o/h/l/c`

### Coexistence with `btc-collector/`
The btc-collector at `/mnt/c/Users/Abhis/btc-collector/` is REST-based
(historical fetcher). It writes to `~/btc-data/data/`. The live recorder
writes to `~/btc-data/data_live/` to avoid concurrent-write conflicts. The
nightly merge folds live into main. **`backfill.py` only handles
early-lifetime gaps**, NOT tail gaps. The Apr 22 → today tail gap won't
auto-fill via `python main.py resume` (workers skip on registry
status=`done`). User chose to handle the tail-fill manually.

### Architecture (LiveSignal — not yet built; design locked in)
For LiveSignal, do NOT build incremental enrichment. Use a hybrid read:
- **Slow-moving cols** (IVP_90d, RV_7d/14d/30d, ADX_4h, pattern, vrp_pct_90d)
  read from latest M3 row in `full_enriched_5m.parquet`. These don't shift
  minute-to-minute — staleness of even hours is fine.
- **Fast-moving values** (spot, ATM IV @7/14/30d, skew RR/BF, GEX, current
  strangle leg marks) computed on-the-fly from `ticker_store` (already
  populated tick-by-tick by existing `delta_ws_client.py`). BS solver runs
  server-side.
- Merge them, run existing `strangle_analytics.compute_trade_analytics`,
  return JSON.
- Reuses existing `<StrangleAnalyticsPanel />` on a new
  `LiveSignalDashboard.tsx`. ~1000 LOC total. No incremental enrichment
  loop. No 5-min scheduler.

### Open / next
- **Wait for M2 backfill to finish** (~225/849 at session pause; ETA ~4h).
- **Restart backend** with `docker compose up --build -d backend`. Recorder
  begins capturing live bars within seconds; first parquet writes ~60s
  later in `data_live/`.
- **Verify**: `ls -la /home/abhis/btc-data/data_live/options/expiry=*/strike=*/`
  should show files growing minute-by-minute. `docker compose exec backend
  python -m app.services.merge_live_to_main --status` shows merge state.
- **Build LiveSignal** (Layer 1 + Layer 2 dropped per the hybrid design above).
- **Build M4 batch backtester** (`strangle_backtest.py` — original spec, biggest
  remaining piece).

### Commit
Pushed strangle analytics + recorder + merge selectively. See `git log` for
the exact commit hash on the recorder/merge work.

---

## What Was Done — 2026-05-02 (Session 8: strangle analytics layer)
*(Earlier session, still relevant — kept intact in commits 7377822/etc.)*

Implemented per-trade ratios from spec §7.8 (credit%, ROC, theta/vega, gamma/theta_dollar,
annualized_credit, etc.), §8 master ratio table, §7.9 decomposition (structural / IV-regime /
excess), §M5 calibration (`calibration_builder.py` from chain snapshots, NOT trade outcomes —
DTE × spot × Δ × IVP buckets, universal fallback curve), z-scores, quality_score
v1 = 0.40·z_credit_pct + 0.60·IVP. Same numbers in Strategy Builder + Backtest Dashboard
because both call the same util. Backend bakes analytics into `BacktestTrade` rows;
frontend shows new Pattern/Credit%/Quality columns + click-to-expand `<StrangleAnalyticsPanel />`.

---

## What Was Done — 2026-05-02 (Module 3: derived metrics + pattern detection)

Implemented Module 3. Plan: `/home/abhis/.claude/plans/sparkling-pondering-plum.md`.

### Files created
- `backend/app/analytics/enrich_derived.py` — ~400 LOC pipeline:
  - Stage A: inner-join M1's spot_enriched.parquet + M2's options_enriched_5m.parquet
    on `timestamp_unix`
  - Stage B: VRP family (iv_rv_spread/ratio @ 7d/14d/30d, vrp_pct @ 90d),
    expected move (1σ + 2σ at 7d/14d/30d), vol-of-vol
    (iv_change_stdev @ 7d/14d/30d, vov_ratio)
  - Stage C: pattern detection — A "Fresh Spike", B "Post-Crash",
    C "Stale", D "Active Trend", Other (priority A→B→C→D→Other,
    vectorized via boolean masks)
  - Stage D: write 4 grids (1m via ffill, 5m native, 15m/30m end-of-bucket)
  - CLI: `python -m app.analytics.enrich_derived [--rebuild] [--since/--through] [--grids]`
- `backend/tests/test_enrich_derived.py` — 13 unit tests all passing:
  VRP formula correctness, expected-move math, pattern A/B/C/D detection,
  pattern priority (A overrides D), vol-of-vol smoke, threshold constants

### Output schema
~310 cols per row (M1 246 + M2 50 - 1 dup + ~16 new). 4 output grids:
- `full_enriched_1m.parquet` (~1.2 GB est)
- `full_enriched_5m.parquet` (~250 MB)
- `full_enriched_15m.parquet` (~85 MB)
- `full_enriched_30m.parquet` (~45 MB)

### Open
- **M2 backfill still running** at ~50/849 expiries (~6%) when M3 work
  finished. M3 cannot run E2E verification until M2 produces
  `options_enriched_5m.parquet`. Wait ~4 hours for the M2 backfill, then run
  `python -m app.analytics.enrich_derived --rebuild` for M3 backfill (~5–10 min).
- M3 code + 13 unit tests committed-ready but uncommitted (per RULE #1,
  awaiting user "commit and push" instruction).

---

## What Was Done — 2026-05-02 (Module 2: options enrichment)

Implemented Module 2 of the short-strangle backtest spec. Plan at
`/home/abhis/.claude/plans/sparkling-pondering-plum.md`.

### Files created
- `backend/app/analytics/enrich_options.py` — ~900 LOC pipeline:
  - Per-expiry chain bulk loader via DuckDB hive partitioning
  - Per-snapshot summary computation (ATM IV, OI sums, max-OI strikes,
    skew at .25/.15/.10Δ, GEX with vectorized Greek compute)
  - Cross-expiry aggregation: constant-maturity ATM IV interp (7/14/30/60d),
    term structure, OI walls, total GEX, strangle synthetic IV
  - Rolling IVP at 90-day window (per tenor) + multi-TF (1m/5m/15m/30m/1h/4h/1d)
  - 4-grid parquet writer (1m via ffill, 5m as-is, 15m/30m end-of-bucket)
  - Vectorized BS price + IV solver + gamma functions (numpy)
  - CLI: `python -m app.analytics.enrich_options [--rebuild] [--since/--through] [--grids]`
- `backend/tests/test_enrich_options.py` — 15 unit tests (BS round-trip,
  vectorized gamma ATM-max, constant-maturity interp, GEX regime,
  IST timestamp), all passing.

### Architecture
Computes natively at **5-minute granularity** (matches M1's pattern; options
metrics don't move meaningfully at 1m). Outputs 4 grids:
- `options_enriched_1m.parquet` (5m values forward-filled to 1m)
- `options_enriched_5m.parquet` (native compute granularity)
- `options_enriched_15m.parquet` (end-of-bucket from 5m)
- `options_enriched_30m.parquet` (end-of-bucket from 5m)

### Smoke verified (2-day window)
- 4 grids written with consistent values
- Sample (BTC@$74k, 2026-04-20): ATM IV 7d/14d/30d/60d ≈ 40-44%,
  RR_25d = -0.07 (put skew ✓), BF_25d = +0.04 (smile ✓), Wing/ATM = 1.13,
  PCR_OI = 0.42, walls at ±2-2.5%
- GEX: -7M to -610M USD (NEGATIVE regime — BTC short-gamma, expected)
- Cross-grid consistency verified

### Open
- **Full backfill running in background** (~7h for 880 days × 849 expiries).
  Watch `/tmp/m2_backfill.log` or use the Monitor tool on the bash background task.
- `gex_flip_level` deferred to v2 (would need 21-point spot grid recompute).
- `gex_per_strike` nested column dropped per plan.

---

## What Was Done — 2026-05-02 (Module 1: spot enrichment)

Implemented Module 1 of the short-strangle backtest spec (`UI ss/new feature/SHORT_STRANGLE_INDICATORS_SPEC.md`).

### Files created
- `backend/app/analytics/__init__.py` — new package marker
- `backend/app/analytics/enrich_spot.py` — ~700 LOC pipeline:
  - 16 indicator primitives in pure pandas+numpy (Returns, RV close/Parkinson/GK,
    Wilder ATR/RSI/ADX, MACD, Bollinger, Stochastic, CCI, Williams %R, ROC,
    Donchian, Keltner, SuperTrend, Aroon)
  - Multi-timeframe layer: per-TF compute @ {1m, 5m, 15m, 30m, 1h, 4h, 1d}
    with merge_asof forward-fill onto the 5m grid (1m point-sampled at end-of-bucket)
  - Cross-TF metrics: 6 RV variants, 5 RVP windows (vs 90-day), `atr_compression_ratio`
    (Wilder ATR(30, 4H)/Wilder ATR(180, 4H)), 3 daily-MA distance %, time-of-day cols
  - Idempotent append-with-tail-overwrite (recompute last 1 day + new bars,
    35-day warm-up read for rolling state)
  - CLI: `python -m app.analytics.enrich_spot [--rebuild] [--through YYYY-MM-DD]`
- `backend/tests/test_enrich_spot.py` — 21 unit tests on synthetic 5m bars
  (flat / step / random walk fixtures), all green

### Files modified
- `backend/requirements.txt` — added `pyarrow`, `pytest`
- `docker/docker-compose.yml` — split mount into `data:ro` + `derived` (writable),
  added `tests:ro` and `app:ro` mounts so live source edits land in the container
  without rebuild (faster iteration loop)

### Output verified
- `/home/abhis/btc-data/derived/spot_enriched.parquet`
- 246,171 5-minute rows × 245 columns ≈ 150 MB compressed
- Time range 2023-12-18 13:10 IST → 2026-04-21 07:20 IST
- Sanity-checked sample row (mid-2025): RSI 55, ATR 132 ($), RV 30%, RVP 34,
  BTC@$96k, MA200 dist +16.6%
- Idempotent re-run: identical row count, +0 rows added (no new source bars)

### Important notes
- Used **pandas + DuckDB** instead of Polars (despite spec) — matches rest of project
- All IVP / ATM IV / skew / GEX / OI **NOT** in this module — those are Module 2
- 1m timeframe only computes Returns/ATR/RSI/ROC (slow smoothers skipped — too noisy)
- IST timestamps stored naive (no tz-aware) so they merge cleanly with raw 1m parquet
- Plan file: `/home/abhis/.claude/plans/sparkling-pondering-plum.md`

### Remaining for the broader 6-module feature
- Module 2 — `enrich_options.py` — chain-based metrics (IVP, ATM IV interp,
  skew, term, OI walls, GEX, strangle synthetic IV)
- Module 3 — `enrich_derived.py` — joined VRP/expected-move/vol-of-vol +
  pattern A/B/C/D detection
- Module 4 — `strangle_backtest.py` — 110-col per-trade output + path attribution
- Module 5 — `calibration.py` + `backfill_attribution.py` — universal IVP→credit
  curve, personal baselines, quality scoring
- Module 6 — Backtest dashboard + Live Signal frontend pages

---

## What Was Done — 2026-04-30 → 2026-05-01 (margin calibration)

### Margin engines: 20% safety buffer in effect (both Python + TS)
**Hard rule established** (saved as memory, see `feedback_margin_safety_bias.md`):
margin model output **must never be below** Delta's actual ARM (`additional_required_margin`,
shown as "Order Margin" in UI). Slight over-estimation acceptable; under-estimation breaks
orders at placement.

**State at end of session:**
- `frontend/src/utils/marginEngine.ts` — already had `SAFETY_BUFFER_PCT = 0.20`
  committed in HEAD (line 152, applied line 381). No diff vs HEAD this session.
- `scripts/margin_engine.py` — has same constant + application site. Lives in
  the untracked `scripts/` dir; never been in git.
- A conditional/per-trade buffer was tried mid-session then reverted; engine is
  flat 20% now. Both files in sync.

### v2 calibration grid (running)
- `scripts/calibrate_v2.py` — full grid: 7 expiry buckets × 6 deltas × 13 lot sizes
  = 546 scenarios per run. Compares our `our_pm` vs Delta's `delta_arm` (the actual
  Order Margin charged).
- `scripts/calibrate_loop_v2.sh` — runs every 15 min for 24h.
- `scripts/calibration_v2_history.csv` — 1638 rows from 3 successful runs at start
  of session. Loop restarted at ~21:44 IST 2026-04-30 with fresh 24h window
  (PID stored in `/tmp/calib_v2_loop.pid`).
- Crashed for ~12h between earlier successful runs and restart (failed conditional-buffer
  revert left stale function refs); now fixed.

### Key calibration finding (against ARM, the right field)
Field discovery: `/v2/orders/estimate_margin/basket` returns BOTH `portfolio_margin`
(gross) and `additional_required_margin` (ARM, what's charged). **ARM is the right
target** — UI's "Order Margin" = ARM. We were earlier comparing against the wrong field.

| Bucket | Median \|err\| pre-buffer | Verdict |
|---|---|---|
| current/weekly/biweekly | 5–10% | Excellent |
| next/next_to_next | 14–17% | Slight over |
| monthly far-OTM (δ≤0.15) | 25–40% under | Structural gap |
| bimonthly far-OTM (δ≤0.15) | 35–60% under | Structural gap |

### UI verification on 8-May δ=0.10 strangle (with 20% buffer)
| Qty | Buffered | UI | Δ% |
|---:|---:|---:|---:|
| 100 | $75.13 | $71 | +5.8% ✓ |
| 200 | $150.25 | $150 | +0.2% ✓ |
| 500 | $375.64 | $387 | **−2.9% ✗** |
| 1000 | $851.06 | $838 | +1.6% ✓ |
| 1500 | $1,503.62 | $1,459 | +3.1% ✓ |
| 2000 | $2,321.35 | $2,303 | +0.8% ✓ |

**5/6 safe.** 500-lot edge case: 2.9% under (tiny absolute, $11). User accepted as-is.

### Friday-overnight backtest Excel
- `scripts/friday_overnight_pnl.py` + `scripts/friday_overnight_pnl.xlsx` — 13 Fridays
  Jan-Mar 2026 × 4 lot sizes, with full cost model (slippage + brokerage + margin engine
  + IV via `app/core/greeks.py::implied_vol`). One Summary sheet + 13 per-Friday
  per-minute MTM detail sheets.

---

## ⚠️ Open / Needs Attention

1. **CSV file lock (BLOCKER)** — `scripts/calibration_v2_history.csv` returns
   PermissionError when Python tries to append. The file shows perms `777` in WSL
   but Windows-side something is holding it (likely Excel from `friday_overnight_pnl.xlsx`
   open earlier, or OneDrive mid-sync). Until resolved, the calibration loop's
   every run will fail at the write step. User action: close Excel, let OneDrive
   finish syncing, or copy the CSV to a non-OneDrive path and update HISTORY_CSV.

2. **Delta API IP whitelist** — current WSL IP `103.121.72.88` doesn't match
   whitelisted IP. ARM API calls return `ip_not_whitelisted_for_api_key`.
   Even if the CSV lock is resolved, rows will lack `delta_arm` until user
   updates whitelist on the Delta dashboard.

3. **24h calibration plan** — once loop is healthy and 24h of data accumulates,
   refit shock-span ramp slopes + DTE constants to close the long-DTE far-OTM
   structural gap (currently bandaged by +20% global buffer).

4. **Uncommitted/untracked changes** — git status shows:
   - **M** `CLAUDE.md`, `HANDOFF.md`, `docs/memories/*` (handoff updates this session)
   - **M** `frontend/src/components/historical/StrategyPanel.tsx` (carried from prior session)
   - **NOT changed:** `frontend/src/utils/marginEngine.ts` (the SAFETY_BUFFER_PCT
     constant is already in HEAD from commit `d79686c` — my edits this session
     were no-ops because the change was already there).
   - **??** the entire `scripts/` directory — Python engine (`margin_engine.py`),
     calibration scripts (`calibrate_v2.py`, `calibrate_loop_v2.sh`), output
     CSV/xlsx (`calibration_v2_history.csv`, `friday_overnight_pnl.xlsx`), fit
     script (`fit_margin_scale.py`), and standalone backtest (`friday_overnight_pnl.py`).
     **None of this is in git.** Needs `git add scripts/` before commit.
   - **??** at repo root: `margin_check.py`, `margin-calculator.jsx`.

---

## Earlier Session — 2026-04-30 (backtester build)
*(content below preserved as-is from earlier handoff)*

---

## What Was Done This Session

### Built: Multi-day Backtester (AlgoTest-style) — committed end-to-end
Pick a date window + strategy template → equity curve + per-trade table.

**Backend (new):**
- `backend/app/services/backtest.py` — day-loop simulator. Resolves expiry per leg, picks strike (Strike Type / Closest Premium / Closest Delta), reads exact mark at entry/exit timestamps from parquet (no bucket drift), samples 1m bars between entry & exit for max/min MTM tracking.
- `backend/app/services/backtest_jobs.py` — in-memory async job registry with `asyncio.Event` cancellation
- `backend/app/api/backtest.py` — POST submit / GET status / DELETE cancel + Pydantic models
- `backend/app/services/option_data.py` — DuckDB helpers extracted from `historical.py`. Strike resolvers, `resolve_expiry()` with Friday-of-week date matching, `get_mark_at_or_before()` for exact-timestamp pricing
- `backend/app/services/costs.py` — Python port of `frontend/src/utils/slippage.ts` + `brokerage.ts`. Round-trip slip = `2 × entry_slip` (matches historical viewer). **Moneyness multiplier removed 2026-04-30** to align with frontend recalibration
- `backend/app/services/margin_v2.py` + `margin_engine_v2.py` + `margin_engine_v2_constants.json` — copies from `scripts/` so docker has them. Used per-trade for portfolio margin
- `backend/app/main.py` — `SESSION_ID` UUID at startup + `GET /api/v1/session-id`

**Frontend (new):**
- `frontend/src/pages/BacktestDashboard.tsx` — top-level page, polling loop
- `frontend/src/components/backtest/` — BacktestForm, BacktestEquityChart, BacktestDailyPnlBars, BacktestStatsPanel, BacktestTradeLogTable, BacktestProgressBar
- `frontend/src/services/backtest_api.ts` — submit/poll/cancel
- `frontend/src/types/backtest.ts` — request/result/trade types + AlgoTest enum maps
- `frontend/src/hooks/usePersistedState.ts` — localStorage-backed `useState`
- `frontend/src/utils/sessionGuard.ts` — checks backend session ID on mount, wipes auto-persisted state if backend restarted

**Frontend (modified):**
- `frontend/src/App.tsx` — 3-way mode toggle: Live / Historical / Backtest
- `frontend/src/main.tsx` — runs sessionGuard before React mount
- `frontend/src/pages/HistoricalDashboard.tsx` — date/time/expiry/strategyMode now persisted; named save/load/delete strategy UI floating top-right
- `frontend/src/components/historical/StrategyPanel.tsx` — MTM data persisted (buildMtmData, buildLegGreeks, buildExitLegData, buildMaxPnlExitData, buildAtmData); reset-on-legs-change skips first render

### Trade log enhancements
- Backend tracks `max_mtm` + `min_mtm` with timestamps; computes `max_pnl_net` / `min_pnl_net` (net P&L if exited at peak/trough)
- Frontend split CE/PE into separate columns: `CE Leg / CE Entry / CE Exit / PE Leg / PE Entry / PE Exit` plus `Max MTM @ time / Max Net / Min MTM @ time / Min Net`
- CSV export updated with all new fields

### Slippage alignment with historical viewer ($4 → $2 fix)
- Root cause: moneyness multiplier in `costs.py` was stale (returned 1.6 for ~13% OTM). Frontend `slippage.ts` had it removed on 2026-04-30 per real-fill calibration but Python port wasn't updated.
- Fix: `_moneyness_mult()` in `costs.py` now always returns 1.0. Backtest matches historical viewer to the cent.

### Auto-state reset on backend restart
- Backend generates `SESSION_ID` at process start; frontend wipes `historical:*` + `backtest:*` localStorage keys (preserves named saves) when ID changes
- Effect: `docker compose up --build -d backend` + browser reload = clean slate. Mode switches still preserve state within a session.

---

## Current Architecture State
- **Live Dashboard:** unchanged
- **Historical Dashboard:** date/time/expiry/strategyMode/MTM all persist across mode switches
- **Backtest Dashboard:** new top-level mode. Async-job + 1Hz polling. Form persists across switches/reloads. Result persists only on `status === "done"`. AlgoTest-aligned UI.
- **Backend:** still single uvicorn worker, in-memory job registry. Backtest jobs lost on backend restart (acceptable; jobs typically <60s).

---

## Pending / Next Up
- [ ] **Phase 3 of plan** — wire SL/TG/Trailing/Per-leg SL/Re-entry/Spot trigger/IV trigger into the day loop. Form fields exist but aren't sent to backend yet.
- [ ] **Phase 4 of plan** — capital sizing (`max_at_capital` mode), cost-sensitivity strip in stats panel
- [ ] **Compare-mode MTM persistence** — only build-mode MTM is persisted in StrategyPanel. Compare mode still wipes on remount.
- [ ] **Slippage v2 integration** (from earlier session — see prior section below). User decided to keep current model + remove moneyness mult. v2 file still sitting unused.
- [ ] (Pre-existing) Partial updates implementation, Historical auto-play, Spot via WS

---

## Key Decisions Made
- **Slippage canonical model**: `frontend/src/utils/slippage.ts` is source of truth. `backend/app/services/costs.py` is a port. **Keep them in sync** — change one, mirror the other, verify with one-day backtest.
- **Backend session ID** clears auto-persisted state on container restart but preserves explicit named saves
- **Backtest strikes use `get_mark_at_or_before`** (exact timestamp), NOT bucketed `last()` — eliminates up-to-4-minute drift
- **Round-trip slip = `2 × entry_slip`** — matches historical viewer's `slipRoundTripUsd` formula
- 3-way App mode toggle is preferable to bolting backtest into StrategyPanel.tsx (already 2100+ lines)

---

## Note for Gemini
- Backtest mode is BIG and new. Read `frontend/src/pages/BacktestDashboard.tsx` and `backend/app/services/backtest.py` first.
- The frontend slippage model and Python port MUST stay in sync. The moneyness multiplier was already de-synced once — re-aligning fixed a $4 vs $2 user-visible discrepancy.
- `backend/app/main.py` has new `SESSION_ID` and `/api/v1/session-id` endpoint. Don't remove these — `frontend/src/utils/sessionGuard.ts` depends on them.
- localStorage keys to know:
  - Auto-state (wiped on backend restart): `historical:simulationDate/Time/selectedExpiry/strategyMode/strategyLegs/panelMode/compareStrategies/activeCompareStratId/buildMtmData/buildLegGreeks/buildExitLegData/buildMaxPnlExitData/buildAtmData`, `backtest:lastResult`, `backtest_v1:*`
  - Preserved across restarts: `historical:savedStrategies`, `historical:strategy:<name>`
  - Tracking: `app:backendSessionId`

---

## Quick Commands
```bash
# Submit a backtest from CLI
curl -XPOST http://localhost:8000/api/v1/historical/backtest \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2026-02-06","end_date":"2026-02-06",
       "legs":[{"strike_offset":0,"type":"PE","action":"SELL","qty":100,"expiry_selector":"weekly","strike_criteria":"closest_delta","strike_value":0.10},
               {"strike_offset":0,"type":"CE","action":"SELL","qty":100,"expiry_selector":"weekly","strike_criteria":"closest_delta","strike_value":0.10}],
       "entry_time_ist":"23:30","weekday_mask":[4],
       "forced_exit_time_ist":"10:00","exit_day_offset":1,"timeframe":"5m",
       "slippage":{"enabled":true,"mode":"smart","mult":1.0,"flat_value":5},
       "brokerage":{"enabled":true,"rate":"offer","referral":false}}'

# Verify session ID changes after rebuild
curl -s http://localhost:8000/api/v1/session-id

# Rebuild backend after any backend change
cd docker && docker compose up --build -d backend

# Restart frontend
fuser -k 3000/tcp && cd frontend && npm run dev
```

---

# Earlier Session — 2026-04-30 (slippage v2 fit, archived)

### A. Data-driven slippage model (built side-by-side)
Fit a new model from actual historical fills.

**New files (in `Back Testing/` repo-external folder)**:
- `extract_fills.py`, `fit_slippage.py`, `slippage_comparison.py`, plus generated CSVs and `slippage_calibration.json`

**New file (in repo)**: `frontend/src/utils/slippage_v2.ts` — parallel implementation, NOT imported. Lives side-by-side for A/B.

**Status:** This session superseded the v2 question by removing the moneyness multiplier from the existing model (fixed a real $4 vs $2 user-visible discrepancy). `slippage_v2.ts` is still uncommitted; it can be deleted or revisited if a future fit is desired.

### B. Excel download — Exit & Peak Marks sheet
Added a per-leg summary sheet to both download buttons in `StrategyPanel.tsx` (Build + Compare modes). One row per leg with Entry/Exit/Peak-MTM mark+spot+P&L. Reuses existing `buildExitLegData` / `buildMaxPnlExitData`.
