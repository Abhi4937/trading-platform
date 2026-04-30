# Current Project State

## Active Projects
- **Multi-day Backtester (Phase 2 done; Phase 3 + 4 pending):** AlgoTest-style backtester
  is wired end-to-end (form → async job → 1Hz polling → equity curve + stats + trade log).
  Phase 3 (SL/TG/Trailing/Per-leg/Re-entry/Spot trigger/IV trigger) and Phase 4 (capital
  sizing `max_at_capital` mode, cost-sensitivity strip) not yet implemented — many form
  fields exist but aren't sent to backend yet.
- **Persistence layer:** Live. localStorage-backed state across mode switches; backend
  session ID resets auto-state on container restart while preserving named saves.
- **Partial Updates Upgrade:** Still pending — paused while backtester was built.

## Known Issues / Open Topics
- **Historical Auto-Play:** Not yet built (discussed SSE / setInterval / WebSocket).
- **Spot Price:** Still REST-polled (could subscribe via existing Delta WS).
- **Throttling:** Need to decide on backend vs frontend throttling for high-frequency ticks.
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
