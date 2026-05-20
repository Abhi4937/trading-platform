# Architecture & Features Reference

This document records WHAT the codebase does. For rules on HOW Claude should
behave (workflow, safety invariants, handoff protocol), see `CLAUDE.md` in
the project root.

Sections below describe modules as they existed when each feature was built.
Code may have evolved since — if anything here contradicts the source,
trust the source. Update this doc when adding major new modules.

---

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

## Historical Option Chain — OI display (added 2026-05-01)

The historical chain endpoint (`GET /historical/option-chain`) now returns
`open_interest` and `oi_usd` per call/put leg. Both fields drop into the
existing `OptionChainTable.tsx`'s `oi_usd` column (already wired) plus the
historical-specific `HistoricalOptionChain.tsx` which mirrors the Delta
Exchange layout: OI columns are the **outermost** (call left, put right) with
a horizontal bar fill proportional to the strike's OI vs the visible chain
max — call bar fills right→left toward the strike (blue tint), put bar fills
left→right toward the strike (orange tint).

### IMPORTANT — OI USD scale convention
Live-chain OI USD comes from Delta's pre-computed `oi_value_usd`.
Historical-chain parquet stores `oi_close` already as **BTC notional**
(contract count × contract size 0.001). To match the live magnitude:

```python
oi_usd = oi_close * spot   # NOT oi_close * spot * 0.001
```

USD formatter is shared with the live chain: `$5.3M`, `$200K`, etc.

## Backtest — Strike Criteria (expanded 2026-05-01 → 2026-05-02)

Seven strike-selection modes now wired end-to-end (form → backend → resolver):

| Frontend label | Backend criteria | Selector function in `option_data.py` | Notes |
|---|---|---|---|
| Strike Type | `strike_type` | `strike_for_strike_type` | ATM / ITMn / OTMn |
| Closest Premium | `closest_premium` | `strike_for_closest_premium` | Target $ premium |
| Closest Delta | `closest_delta` | `strike_for_closest_delta` | Nearest abs(delta) — over OR under |
| Delta ≤ | `closest_delta_below` | `strike_for_closest_delta_below` | Highest delta still ≤ target (never overshoot) |
| Delta ≤ Match | `closest_delta_prem_match` | `strikes_pool_for_delta_below` + cross-leg adjustment in `backtest.py` | Two-phase: each leg picks delta ≤ target, then re-pick from pool to align all legs to the cheapest leg's premium |
| Delta ≤ Align | `closest_delta_align` | `strikes_pool_for_delta_below` + delta-align adjustment in `backtest.py` | Each leg picks delta ≤ target; lower-delta leg re-runs with the higher leg's resolved delta as the new ceiling |
| Highest OI | `highest_oi` | `strike_for_highest_oi` | Among OTM strikes (CE > spot, PE < spot) with abs(delta) ≤ cap, pick the one with the largest `oi_close`. Skips day with `no_otm_strike_for_oi_delta_<cap>` if nothing qualifies. |

Pattern when adding new criteria — touches **5 files**:
1. `backend/app/services/option_data.py` — selector function
2. `backend/app/services/backtest.py` — route in resolution loop + import
3. `backend/app/api/backtest.py` — `Literal[...]` for `strike_criteria`
4. `frontend/src/types/backtest.ts` — `AlgoStrikeCriteria` union + `ALGO_STRIKE_CRITERIA`
5. `frontend/src/components/backtest/BacktestForm.tsx` — `ENABLED_CRITERIA`, submit mapping, UI block

## Backtest — Per-leg Stop Loss + delta tracking (added 2026-05-01)

`LegSlConfig.type` supports three modes: `pct` (% of entry premium), `points`
(absolute USD), and **`delta`** (abs(current delta) ≥ threshold). The
delta-SL check runs every bar; when it (or any pct/points SL) trips, the
**whole position exits** at that bar's marks.

The bar-walk also tracks **per-leg delta at the max-MTM and min-MTM bars**.
These flow through to the trade log as `max_leg_deltas` / `min_leg_deltas`
(one entry per leg, `{type, strike, delta}`). Computed via the same
`implied_vol` + `compute_greeks` path as entry deltas, lazy-cached per bar
to avoid double work when delta-SL is also enabled.

## Backtest — Trade Log (heavy redesign 2026-05-01 → 2026-05-02)

Each trade renders as **two stacked rows** (CE on row 1, PE on row 2) with
shared columns spanning both via `rowSpan=2`. Layout:

- Date (rs2) | Entry (rs2) | Exit (rs2) | Reason (rs2)
- **Row 1**: `CE` badge + strike | Expiry | Entry | Exit | @Max | Δ@Max | @Min | Δ@Min | Δ | IV %
- **Row 2**: `PE` badge + strike | Expiry | Entry | Exit | @Max | Δ@Max | @Min | Δ@Min | Δ | IV %
- Shared cells (rs2): Abs Δ | ATM IV | HV | Spot In | Spot Out | Gross | E.Slip | X.Slip | Pk.Slip | E.Brk | X.Brk | Pk.Brk | Net | Max MTM | Max Net | Min MTM | Min Net | Margin

### Backend computes at entry
- `entry_delta` per leg (BS), `entry_iv` per leg (BS, %)
- `entry_atm_iv` (computed via `atm_iv_at()` on the earliest-expiry leg)
- `entry_hv` (7-day rolling realized vol on 5m spot bars via `hv_at()` —
  reuses the same formula as the historical chart's RV pane)

### XLSX export
- "Export XLSX" button (was CSV) writes a workbook with two sheets:
  **Trades** (skipped days excluded) and **Stats** (mirrors `BacktestStatsPanel` KPIs)
- Filename: `backtest_<first_date>_<last_date>.xlsx`

## Technical Indicators on Spot / Leg Premium (added 2026-05-02)

A multi-pane lightweight-charts component renders BTC spot (or a leg's
premium series) with overlaid technical indicators. v1 = visualize only;
indicators do NOT yet drive backtest entry/exit — the `spot_trigger` /
`iv_trigger` Pydantic stubs in `BacktestRequest` are reserved for v2.

### Indicator computation
- `backend/app/services/indicators.py` — pure-Python pandas/numpy. Hand-rolled
  to avoid pandas-ta's known numpy-2.x ABI break (it imports `numpy.NaN`).
- Public API: `compute_indicators(df, configs) -> {indicator_id: [points]}`
- Supported (v1): SMA, EMA, RSI, MACD, Bollinger Bands, ATR, VWAP
- Stable IDs: `rsi_14`, `ema_20`, `macd_12_26_9`, `bbands_20_2`, etc.
- Each indicator's series drops NaN warm-up bars before serializing

### Backend endpoints (in `historical.py`)
- `GET /historical/spot-ohlc?start_ts&end_ts&timeframe`
- `GET /historical/leg-ohlc?expiry&strike&type&start_ts&end_ts&timeframe`
- `GET /historical/spot-indicators?...&indicators=<json>`
- `GET /historical/leg-indicators?...&indicators=<json>`

`indicators` query param is URL-encoded JSON: `[{"type":"rsi","params":{"period":14}}, ...]`.
Internally each indicator endpoint extends the start backwards by
`max_window × 1.5` bars (warm-up) so the first visible bar has a stable
rolling value, then slices the result back to the requested window —
copies the warm-up trick from the existing RV computation in
`/chart-data-with-greeks`.

### Frontend components
- `frontend/src/components/historical/SpotChart.tsx` — multi-pane chart.
  Pane 0 = candlesticks + overlay lines (EMA, SMA, BB upper/mid/lower).
  Each non-overlay indicator gets its own pane (RSI with 30/70 reference
  lines; MACD with line + dashed signal + green/red histogram + zero-line;
  ATR; VWAP).
- `frontend/src/components/historical/IndicatorConfigPanel.tsx` — Spot/Leg
  toggle, "+ Add indicator" dropdown, per-indicator chip with parameter
  steppers and remove button. VWAP is grayed out in leg-mode (option
  parquets don't carry trade volume; `oi_close` isn't a true volume proxy).
- Mounted in **both** `HistoricalDashboard` and `BacktestDashboard`. The
  indicator config is persisted under shared key `historical:indicators` so
  configuring once shows in both views.
- In `HistoricalDashboard`, the spot chart's timeframe is **shared** with
  the existing premium leg chart's `timeframe` state — switching the
  premium chart's dropdown also retimeframes the spot chart.

### Why parquet (not TradingView Charting Library)
Indicators must run server-side so the same Python function feeds both the
chart UI and the backtester's day-loop (v2 work). With TradingView, every
indicator would have to be implemented twice (Pine in TV + Python in
backtest) with risk of drift. Parquet + DuckDB also matches the rest of
the platform's data path — single source of truth.

### Layout note
Adding the spot chart pushed the option chain off-screen because
`.historical-container` was `height: 100%; overflow: hidden`. Override
applied: container is now `overflowY: auto`, `historical-main` is locked
to `height: calc(100vh - 120px); flexShrink: 0` so chain+chart panel
keep their viewport sizing while the spot chart pushes the page below
into a scrollable region. A floating "↓ Spot Chart" button (bottom-right,
fixed) smooth-scrolls to the spot chart section.
