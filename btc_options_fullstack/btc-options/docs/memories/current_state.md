# Current Project State

## Active Projects
- **Short-strangle backtest stack (Module 1 done 2026-05-02; Modules 2-6 pending):**
  Plan at `/home/abhis/.claude/plans/sparkling-pondering-plum.md`, spec at
  `UI ss/new feature/SHORT_STRANGLE_INDICATORS_SPEC.md`. Module 1 = spot enrichment
  pipeline, builds `/home/abhis/btc-data/derived/spot_enriched.parquet` (246k 5m
  rows × 245 cols, ~150 MB). Run via `python -m app.analytics.enrich_spot
  [--rebuild]`. Modules 2-6 (options enrichment → derived → backtest engine →
  calibration → dashboard) to be planned in fresh sessions per spec gates.

## Active Projects (older)
- **Margin model calibration (active 2026-04-30 → 2026-05-01):** v2 grid running every
  15 min for 24h to compare our `compute_portfolio_margin` against Delta's actual
  ARM (`additional_required_margin` field, NOT `portfolio_margin` — that's the gross
  field). Data lands in `scripts/calibration_v2_history.csv`. Plan: refit shock-span +
  DTE constants from full 24h dataset once available.
- **Multi-day Backtester (Phase 2 done; Phase 3 + 4 pending):** AlgoTest-style backtester
  is wired end-to-end (form → async job → 1Hz polling → equity curve + stats + trade log).
  Phase 3 (SL/TG/Trailing/Per-leg/Re-entry/Spot trigger/IV trigger) and Phase 4 (capital
  sizing `max_at_capital` mode, cost-sensitivity strip) not yet implemented — many form
  fields exist but aren't sent to backend yet.
- **Persistence layer:** Live. localStorage-backed state across mode switches; backend
  session ID resets auto-state on container restart while preserving named saves.
- **Partial Updates Upgrade:** Still pending — paused while backtester was built.

## Margin model — safety-bias rule (CRITICAL invariant)
The margin model in `scripts/margin_engine.py` and `frontend/src/utils/marginEngine.ts`
**must always over-estimate, never under-estimate** Delta's actual ARM (the "Order Margin"
shown in UI). A flat `SAFETY_BUFFER_PCT = 0.20` is applied as the final multiplier on
`portfolio_margin` to enforce this. Verified 2026-04-30 against UI for 8-May δ=0.10
strangle: 5/6 lot sizes safely above UI; 500-lot edge case is 2.9% under (acceptable).
DO NOT remove or reduce the buffer without re-verifying against fresh UI numbers.

## Calibration loop (running)
Background process running `scripts/calibrate_loop_v2.sh` every 15 min for 24h.
- PID file: `/tmp/calib_v2_loop.pid`
- Live log: `/tmp/calib_v2_loop.log`
- Output CSV: `scripts/calibration_v2_history.csv` (29 columns including `our_pm`,
  `delta_pm` (gross), `delta_arm` (charged))
- **Started 2026-04-30 ~21:44 IST → ends ~21:44 IST 2026-05-01.**
- ⚠️ Delta API IP whitelist may need refreshing — current WSL IP changes. If
  `delta_arm` column is empty in new rows, the API is rejecting calls and
  user must update the IP on Delta's API key dashboard.

## Known Issues / Open Topics
- **Historical Auto-Play:** Not yet built (discussed SSE / setInterval / WebSocket).
- **Spot Price:** Still REST-polled (could subscribe via existing Delta WS).
- **Throttling:** Need to decide on backend vs frontend throttling for high-frequency ticks.
- **Long-DTE far-OTM margin under-charge (structural):** model still under-charges by
  up to 60% on bimonthly δ=0.10 strangles before buffer; the 20% buffer reduces this
  but doesn't fully cover the worst tail. Refit pending 24h calibration completion.
- **Margin engine — zero-IV leg skipping:** `buildMarginLegs()` in `marginEngine.ts` previously
  fell back to `iv = 0.5` (50%) when a leg's mark price was 0 (no trade data at that timestamp).
  This produced fake margin numbers for zero-price strikes. Fixed 2026-03-18: legs with `iv_pct = 0`
  are now excluded from the margin computation entirely. The UI shows a warning count when legs
  are skipped. Underlying cause: backend correctly returns `iv_pct = 0` when `last_price = 0`
  (fixed in `historical.py` same session).

## Slippage model (CRITICAL invariant)
`frontend/src/utils/slippage.ts` is canonical. `backend/app/services/costs.py` is a
Python port. **They MUST stay in sync.** Already de-synced once (2026-04-30): the
moneyness multiplier was 1.0 in TS but 1.6 for ~13% OTM in Python, producing $4
backtest slip vs $2 historical for the same strangle. Fixed by zeroing out the
moneyness mult in Python. When recalibrating, change ONE side and mirror in the
other; verify with a one-day backtest matching the historical MTM panel.

## Slippage v2 file (uncommitted, abandoned for now)
`frontend/src/utils/slippage_v2.ts` (built earlier 2026-04-30) is sitting unused.
The 2026-04-30 fix to remove the moneyness mult from `slippage.ts` superseded the
v2 integration question. Decide whether to delete or keep for future fits.

## Recently Added — Excel Download (2026-04-30)
Both Strategy Builder downloads now include an **Exit & Peak Marks** sheet:
- Build mode: per-leg row with Entry/Exit/Peak-MTM mark + spot + leg P&L.
- Compare mode: same with leading Strategy column.
Implementation in `frontend/src/components/historical/StrategyPanel.tsx`
(`downloadExcel` + `downloadCompareExcel`). Reuses existing exit/peak state.

## Handoff Log
- `2026-03-13`: Claude provided handoff and handoff protocol suggestion.
- `2026-03-13`: Gemini initialized the memory directory and state files.
- `2026-04-30`: Claude built slippage v2 + Exit & Peak Marks sheet; updated HANDOFF.md.
- `2026-04-30` (later): Claude built multi-day backtester end-to-end + persistence layer +
  session-ID reset mechanism. Removed moneyness multiplier from `costs.py` to align with
  `slippage.ts`. Pushed to origin.
- `2026-04-30 → 2026-05-01` (overnight): Claude added flat 20% safety buffer to both
  margin engines. Discovered `additional_required_margin` (ARM) is the correct
  calibration target, not `portfolio_margin`. v2 calibration loop restarted with
  fresh 24h window. UNCOMMITTED: `scripts/margin_engine.py`, `frontend/src/utils/marginEngine.ts`.
