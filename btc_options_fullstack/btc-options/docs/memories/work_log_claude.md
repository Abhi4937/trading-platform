# Claude's Work Log

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
