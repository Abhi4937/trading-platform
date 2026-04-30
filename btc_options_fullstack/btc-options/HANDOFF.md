# Handoff Log

## Last Session
**Who:** Claude
**Date:** 2026-04-30 (later session)
**Branch:** `mainbranch-gemini_claude`
**GitHub:** Pushed at end of session — see latest commit on origin

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
