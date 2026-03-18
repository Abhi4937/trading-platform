# Current Project State

## Active Projects
- **Partial Updates Upgrade:** Moving to tick-based pushes to save bandwidth.
- **Historical Simulation:** Refining the replay controller and charts.

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

## Handoff Log
- `2026-03-13`: Claude provided handoff and handoff protocol suggestion.
- `2026-03-13`: Gemini initialized the memory directory and state files.
