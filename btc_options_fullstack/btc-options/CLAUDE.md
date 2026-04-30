# Claude Instructions — BTC Options Platform

## RULE #1 — Always Ask Before Making Changes
Before editing ANY file (code, config, docs, anything):
1. Describe what you plan to change and why
2. Ask "shall I proceed?" and wait for confirmation
Do NOT assume a question about behavior is a request to fix it.

## Session Start Checklist (do this first, every session)
1. Read `HANDOFF.md` — who worked last, what changed, what's pending
2. Read `docs/memories/current_state.md` — active tasks and open issues
3. Read `docs/memories/work_log_gemini.md` — what Gemini did (avoid conflicts)
4. Then ask the user what they want to work on

## Handoff Protocol (do this at end of every session)
1. Update `HANDOFF.md` — fill in: who worked, what files changed, what's pending
2. Update `docs/memories/work_log_claude.md` — append what was done
3. Update `docs/memories/current_state.md` — if anything changed
4. Tell the user: "Ready to hand off to Gemini — HANDOFF.md is updated"

## AI Collaboration
- Claude and Gemini take turns on this codebase
- Gemini reads the same `HANDOFF.md` and `docs/memories/` files
- Never overwrite a file Gemini touched without reading it first
- Check `git status` before starting work to see Gemini's uncommitted changes


## Endpoints
- Delta REST: `https://api.india.delta.exchange`
- Delta WebSocket: `wss://socket.india.delta.exchange`
- Always use India region endpoints — matches user's trading account

## Architecture Rules
- Backend runs with `--workers 1` (single uvicorn worker) — required for in-memory ticker_store
- Never increase workers without switching to Redis-backed ticker store
- Frontend is Vite dev server on port 3000 (not containerised)

## Branch Strategy
- `main` — full featured (IV chart, Premium chart)
- `feature/chain-only-no-charts` — clean chain-only for testing/new development

## After Any Code Change

### Frontend change
1. Kill and restart frontend: `fuser -k 3000/tcp && cd frontend && npm run dev`

### Backend change
1. Rebuild and restart backend: `cd docker && docker compose up --build -d backend`
2. Then kill and restart frontend: `fuser -k 3000/tcp && cd frontend && npm run dev`

- Always restart/rebuild immediately after making changes — do not wait for user to ask
- Once the user confirms the change works, commit and push to current branch immediately

## RULE #2 — Commit Before Starting New Work
Before making any new change, check `git status`.
If there are uncommitted changes, ask the user: "There are uncommitted changes — shall I commit and push first?"
Do NOT start new work on top of uncommitted changes.

## Key Facts
- Greeks computed with Black-Scholes server-side (verified match with Delta's live greeks)
- OI and Volume displayed in USD using Delta's `oi_value_usd` and `turnover_usd` fields
- WS product list refreshed every 1 hour (new expiries added weekly)
- Settlement time: 5:30 PM IST = 12:00 UTC
- Contract size: 0.001 BTC per contract

## Historical Dashboard — Features Built

### Architecture
- `frontend/src/pages/HistoricalDashboard.tsx` — main page, date/time/expiry pickers, option chain table
- `frontend/src/components/historical/StrategyPanel.tsx` — strategy builder + compare mode + MTM + Greeks
- `frontend/src/components/historical/MultiPaneChart.tsx` — single lightweight-charts instance, multi-pane
- `frontend/src/components/historical/CompareChart.tsx` — multi-strategy P&L overlay (separate chart)
- `backend/app/api/historical.py` — FastAPI router with DuckDB parquet queries

### MultiPaneChart (lightweight-charts v5)
- Single chart instance: all panes share one time scale → crosshair syncs automatically
- Pane 0: MTM P&L (BaselineSeries, green above / red below zero)
- Pane 1: IV% per leg (LineSeries, toggleable, collapsed by default)
- Pane 2: Delta per leg (LineSeries, toggleable, collapsed by default)
- Each pane resizable via drag handle using `IPaneApi.setHeight()`
- `hideMtm` prop for compare mode — skips MTM pane, renders IV/Delta only
- Toggle headers rendered OUTSIDE the chart canvas div to prevent overlap
- `PANE_MIN = 60px` prevents panes collapsing to 0

### Backend Endpoint
- `GET /historical/chart-data-with-greeks` — bucketed OHLC joined with spot, returns `{time, open, high, low, close, spot, iv, delta, gamma, theta, vega}` per bar
- Reuses existing `implied_vol` + `compute_greeks` from `app/core/greeks.py`
- Greeks NOT recomputed for option chain — chain uses server-side BS at snapshot time

### Excel Download
- Build mode: MTM sheet has `Time | BTC Spot | [leg IV% | Delta | Gamma | Theta | Vega] per leg | Net Delta/Gamma/Theta/Vega | P&L`
- Compare mode: per-strategy sheets with same columns + MTM Comparison sheet + MTM Stats sheet

### MTM Stats Panel
- Max/Min P&L with timestamps, Final P&L, Max Drawdown
- Expandable drawdown table — all drawdown periods chronological

### Compare Mode
- Multiple named strategies (Strategy 1, 2, …), tabs to switch active strategy
- CompareChart: P&L lines per strategy on shared chart
- MultiPaneChart below with `hideMtm` — IV% and Delta for all legs across all strategies

## Backtest Dashboard — Features Built (2026-04-30)

### Architecture (3-way mode toggle: Live / Historical / Backtest in `App.tsx`)
- `frontend/src/pages/BacktestDashboard.tsx` — top-level page, owns jobId/status/polling
- `frontend/src/components/backtest/BacktestForm.tsx` — AlgoTest-style form (legs, entry/exit times, SL/TG stubs, costs, save-strategy snapshots). Dark theme, custom Stepper component.
- `frontend/src/components/backtest/BacktestEquityChart.tsx` — lightweight-charts BaselineSeries
- `frontend/src/components/backtest/BacktestDailyPnlBars.tsx` — recharts BarChart, green/red
- `frontend/src/components/backtest/BacktestStatsPanel.tsx` — KPI grid (win rate, max DD, expectancy, etc.)
- `frontend/src/components/backtest/BacktestTradeLogTable.tsx` — sortable table with CE/PE entry/exit columns + Max/Min MTM with timestamps + Max/Min Net (P&L if exited at peak/trough). CSV export.
- `frontend/src/components/backtest/BacktestProgressBar.tsx` — progress + cancel
- `backend/app/api/backtest.py` — POST submit / GET status / DELETE cancel
- `backend/app/services/backtest.py` — day-loop simulator, samples 1m bars for max/min MTM tracking
- `backend/app/services/backtest_jobs.py` — in-memory job registry, asyncio cancel events
- `backend/app/services/option_data.py` — DuckDB helpers: spot lookup, strike resolvers (Strike Type / Closest Premium / Closest Delta), expiry resolver
- `backend/app/services/costs.py` — Python port of `frontend/src/utils/slippage.ts` + brokerage. Slippage matches the historical viewer's `slipRoundTripUsd` exactly.
- `backend/app/services/margin_v2.py` + `margin_engine_v2.py` + `margin_engine_v2_constants.json` — 29-scenario portfolio margin, used per trade

### Async-job + 1Hz polling pattern
- `POST /api/v1/historical/backtest` → `{ job_id, status: "queued" }` immediately
- Backend runs day-loop in `asyncio.create_task` writing to in-process `_backtest_jobs` registry
- `GET /api/v1/historical/backtest/{job_id}` → `{ status, progress, result? }`
- `DELETE /api/v1/historical/backtest/{job_id}` → cancels via `asyncio.Event`
- Single uvicorn worker constraint: registry is in-process, jobs lost on restart

### State persistence (`frontend/src/hooks/usePersistedState.ts`)
- localStorage-backed `useState` so date/time/expiry/strategy/MTM survive mode switches
- Keys: `historical:*` (auto-state) and `historical:strategy:<name>` (named saves)
- Same for `backtest:lastResult` (only persisted on `status === "done"`, never during running)
- Named saved strategies survive backend restarts; auto-state does not (see below)

### Backend session ID — auto-state reset on restart (`backend/app/main.py` + `frontend/src/utils/sessionGuard.ts`)
- Backend generates `SESSION_ID = uuid.uuid4().hex` at process start
- Frontend `main.tsx` blocks React mount on `GET /api/v1/session-id`
- If stored ID differs → wipes all `historical:*` + `backtest:*` localStorage keys (excluding named saves)
- This means: rebuilding the docker container resets all auto-persisted UI state on the next page load

### Slippage model (CRITICAL invariant — keep in sync)
- Frontend: `frontend/src/utils/slippage.ts`
- Backend port: `backend/app/services/costs.py`
- Calibration: `MIN_SPREAD_USD=0.10`, `PER_BTC_BASE=3.85`, `BASE_PCT=0.012`, no moneyness multiplier (removed 2026-04-30)
- Round-trip slip = `2 × entry_slip` (matches `slipRoundTripUsd` in StrategyPanel.tsx)
- If calibrating: change ONE place and mirror in the other. Verify with a single-day backtest matching the historical MTM panel within $0.10.
