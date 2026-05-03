# Current Project State

## Active Projects
- **Short-strangle backtest stack (M1–M5v2 + live recorder all live as of
  2026-05-03; LiveSignal page + M6 dashboard pending):** Plan at
  `/home/abhis/.claude/plans/sparkling-pondering-plum.md`, spec at
  `UI ss/new feature/SHORT_STRANGLE_INDICATORS_SPEC.md`.
  - **M1** ✅ — `spot_enriched.parquet` (246k 5m rows × 246 cols, 151 MB).
  - **M2** ✅ — 859 expiries backfilled (4.6h with per-expiry checkpoint).
    Output: 4 grids (`options_enriched_{1m,5m,15m,30m}.parquet`).
  - **M3** ✅ — joined backfill (30s). Output: 4 grids
    (`full_enriched_{1m,5m,15m,30m}.parquet`, 316 cols).
  - **M5 v1 calibration** ✅ — `calibration_raw.parquet` (806k snapshot rows),
    `calibration.parquet` (600 buckets), `calibration_universal.parquet`.
    Captures entry-side richness only.
  - **M4 batch backtester** ✅ NEW (`m4_batch_backtester.py`, ~430 LOC).
    Friday 23:00 IST × 858 live expiries × 6 deltas × 100 lots/leg, exit
    Sat 10:00 IST or earlier on per-leg 100% loss SL. Full historical
    backfill: **5,274 trades, 49,475 hourly path snapshots, win rate 58.2%**.
    Outputs `m4_trades.parquet` + `m4_paths.parquet`. Reuses extracted
    `simulate_trade_path()` from `trade_simulator.py` (also new).
  - **M5 v2 enrichment** ✅ NEW (`backfill_attribution.py`, ~155 LOC).
    Aggregates M4 outcomes per (DTE × spot × Δ × IVP) bucket; computes
    `pattern_winrate` (per pattern, JSON), `z_winners_mean/std`, expectancy,
    sl_hit_rate. Writes `calibration_v2.parquet` as left-join superset of v1
    (450/600 buckets have M4 data).
  - **Strangle analytics layer** ✅ — auto-detects v2 calibration
    (`compute_trade_analytics` returns `quality_source='calibrated_v2'` when
    available, formula `0.25·z_all + 0.30·z_winners + 0.30·IVP +
    0.15·pattern_winrate`). Falls back to v1 (`'calibrated'`), then to
    `'fallback_ivp_credit'`.
  - **Live WS recorder + nightly merge** ✅ NOW RUNNING. Backend rebuilt
    today, recorder subscribed 488 symbols (MARK + OI), 507 parquet files
    written to `data_live/` within 35s of restart. Nightly merge scheduled
    background loop (first run after 20h).
  - **LiveSignal page** — design locked, NOT built. Hybrid read: latest
    M3 row for slow-moving cols + `ticker_store` live chain for fast-moving
    values. Reuses existing `<StrangleAnalyticsPanel />`. ~1000 LOC.
  - **M6 Batch Results dashboard** — frontend page with credit% × IVP
    scatter, win-rate heatmap, MFE/MAE distribution. Pending.

### Pipeline flow when running fresh
```
1. python -m app.analytics.enrich_spot --rebuild              # ~16s, M1 output
2. python -m app.analytics.enrich_options --rebuild           # ~4h, M2 outputs
3. python -m app.analytics.enrich_derived --rebuild           # ~5-10 min, M3 outputs
4. python -m app.analytics.calibration_builder --rebuild      # ~15 min, calibration parquets
```

### Live data flow (NEW this session, pending backend restart)
```
[Delta WS candlestick_1m]                  [btc-collector REST]
     │                                          │
     ▼                                          ▼
backend live_recorder         /mnt/c/Users/Abhis/btc-collector/
     │                                          │
     ▼                                          ▼
data_live/{spot,options}/        ~/btc-data/data/{spot,options}/
     │                                          │
     ▼ nightly merge (folds live → main)        │
     └──────────────────────────────────────────┘
                          │
                          ▼
                   M1/M2/M3 enrichment
                          │
                          ▼
              full_enriched_5m.parquet
                          │
                          ▼
              /api/v1/live-signal endpoint (TBD)
                  + StrangleAnalyticsPanel
```

### Pipeline flow when running fresh
```
1. python -m app.analytics.enrich_spot --rebuild              # ~16s, M1 output
2. python -m app.analytics.enrich_options --rebuild           # ~4h, M2 outputs
3. python -m app.analytics.enrich_derived --rebuild           # ~5-10 min, M3 outputs
```
Incremental runs (default mode without --rebuild) are fast: append + overwrite-last-1-day.

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
