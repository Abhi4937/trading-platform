# Current Project State

## Active Projects
- **M7 Friday→Saturday strangle/straddle sweep (NEW Session 13, 2026-05-05):**
  Plan at `/home/abhis/.claude/plans/go-through-the-project-linked-dragonfly.md`.
  - Backend: `m7_batch_backtester.py` + `m7_results.py` API. Sweeps every
    (entry_hour × expiry × delta) for Fri 21:00 → Sat 03:00 IST entries with
    Sat 17:30 IST hard cap. NO exit logic in simulator — full 1m path stored
    so any exit rule (max profit %, margin %, premium SL %) is derived as a
    DuckDB query against the path parquet.
  - Frontend: `M7SweepDashboard.tsx` + 7 components. Filter bar with exit-rule
    inputs, headline strip, IV-band-summary headline table, aggregate heatmaps,
    best-combo table, trade log, 1m path chart with PnL/Premium/IV/Δ tabs.
  - Tests: 31 tests passing.
  - Status: BACKFILL RUNNING (PID at /tmp/m7_backtest.pid, log at
    /tmp/m7_backtest.log). 121 Fridays, ~3 min each, ETA ~5h. Trades parquet
    written incrementally every 5 fridays so dashboard works during backfill.
  - After backfill: run `python3 scripts/backfill_m7_enriched.py` to add
    calibration_v2 join columns.

- **Short-strangle backtest stack (M1–M5v2 + live recorder all live as of
  2026-05-03; LiveSignal + M6 dashboards live as of Session 11; M6
  Attribution layer added in Session 12 on 2026-05-04):** Plan at
  `/home/abhis/.claude/plans/sparkling-pondering-plum.md`, spec at
  `UI ss/new feature/SHORT_STRANGLE_INDICATORS_SPEC.md`. Latest M6
  attribution plan at
  `/home/abhis/.claude/plans/go-through-the-claude-logical-naur.md`.
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
  - **LiveSignal page** ✅ NEW (Session 11). Backend
    `live_signal_compute.scan_live_candidates()` + `/api/v1/live-signal/scan`
    (5s response cache). Frontend `LiveSignalDashboard.tsx` mounted as the
    `LIVESIGNAL` mode in App.tsx. Polls every 7s, renders Best-now card +
    sortable candidates table with quality_source / pattern_winrate /
    hard-filter chips. Returns ~50 candidates per scan.
  - **M6 Batch Results dashboard** ✅ NEW (Session 11, extended 11b). Backend
    `/api/v1/m4/{summary,trades,aggregate,scatter,path,quality_calibration,
    expiry_grid,contract_type_summary}` (module-cached parquet). Frontend
    `M4ResultsDashboard.tsx` mounted as the `M4_RESULTS` mode (scrollable).
    Header KPIs, filter bar, win-rate heatmap (DTE × Δ), pattern bars,
    credit×P&L scatter, quality calibration curve, **plus per-contract-type
    expiry × IV × Δ grid table** (`M4ExpiryGridTable.tsx`) showing
    n / WR / SL / Avg+Best MFE / Avg+Worst MAE / Avg Gross / Avg+Total Net /
    slippage RT+½ / brokerage RT+½ / cost RT / credit % / margin per cell.
    Contract types: current/next/next_to_next/weekly/biweekly/three_week/
    monthly/bimonthly/quarterly (classified by DTE + last-Friday-of-month).
    IV bucketing keyed on this-expiry's own ATM IV at entry.

    **Headline findings:** next-to-next (76.5% WR, +$11.96 avg) and weekly
    (76.3% WR, +$5.64) are the strongest contracts. Skip everything ≥30 DTE;
    bimonthly alone bleeds –$16,230. Sweet spot Δ 0.30 in IV 50–70%.
  - **/historical/calibration v2 fields** ✅ NEW (Session 11). Endpoint
    surfaces `overall_winrate`, `n_trades`, `z_winners_mean/std`,
    `pattern_winrate` (parsed JSON), `expectancy_per_credit_pct`,
    `sl_hit_rate` when v2 has data. v1 keys preserved.
  - **m4_trades_enriched.parquet + M6 Attribution layer** ✅ NEW
    (Session 12, 2026-05-04). `scripts/backfill_m4_enriched.py` joins
    `m4_trades` with `calibration_v2` to bake in `fair_credit_at_ivp`,
    `structural_credit_pct`, `iv_regime_premium_pct`,
    `excess_over_fair_pct`, per-leg θ/ν/γ, and `theta_per_vega` ratios
    (5,274 rows × 87 cols, 1.48 MB). Loader in `m4_results.py` prefers
    enriched parquet. Three new endpoints:
    - `GET /api/v1/m4/winners_vs_losers?delta=` — per-contract avg(win)
      vs avg(loss) for 31 indicators, "discriminating" flag for
      |gap| > 0.5σ
    - `GET /api/v1/m4/per_friday_best?delta=` — 121-row Friday view
      with winner/runner-up/loser + top 3 deciding indicators
      (ranked by |Δ| / σ)
    - `GET /api/v1/m4/win_frequency?delta=` — per-contract count of
      Fridays it was the best performer
    Three new frontend components in `frontend/src/components/m4/`:
    `M4WinFrequency.tsx`, `M4WinnersVsLosers.tsx`, `M4PerFridayBest.tsx`,
    mounted in `M4ResultsDashboard.tsx` as a new "Attribution analysis"
    section with shared Δ chip selector
    (`usePersistedState('m6:attr_delta', 0.30)`). Contract summary
    strip extended with Avg Win, Avg Loss, Best Net, Worst Net,
    Best MFE, Worst MAE columns. IV bands split: 80-100 → 80-90 +
    90-100 + 100+ (the latter is permanently empty; max ATM IV in
    dataset = 98.65%). Expiry-class filter cleaned up to chip-only
    (search input removed per user feedback).

    **New attribution findings:** `next_to_next` is the cleanest contract
    (avg win +$22.25 vs avg loss -$21.49 ≈ symmetric, 76% WR).
    `bimonthly` worst single trade -$1,143 (avg loss -$42.71 = 3-4×
    other contracts). `current` wins outright on 28% of Fridays at Δ=0.30
    (more than `next_to_next`'s 27%) but smaller per-trade. **At Δ=0.30
    the workhorse contracts have ZERO discriminating indicators at 0.5σ**
    — meaning the alpha lives in cross-contract selection per Friday,
    not in pre-trade single-indicator filters.

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
- `2026-05-03 (Session 10)`: Claude shipped M2/M3/M5v1 backfills, M4 batch backtester
  (5,274 trades, 58.2% win rate), M5 v2 enrichment (`calibration_v2.parquet` with
  `pattern_winrate`/`z_winners_mean`/`overall_winrate`), and started the live recorder.
- `2026-05-03 (Session 11)`: Claude shipped LiveSignal page + M6 batch results dashboard
  (heatmap + scatter + pattern bars + quality calibration curve), surfaced v2 fields in
  `/historical/calibration`, and added live_recorder OI counters / docs (full OI rewrite
  deferred). 19 new files, 28 new tests, all 43 affected tests green.
- `2026-05-03 (Session 11b)`: Extended M6 page with per-contract-type expiry × IV × Δ
  grid table (`M4ExpiryGridTable.tsx`) + 2 new endpoints (`/api/v1/m4/expiry_grid`,
  `/contract_type_summary`). Added scroll to LiveSignal + M6 pages. Confirmed via
  expiry-classified analysis: next-to-next + weekly are best contracts; bimonthly
  is the dataset's –$16k drag. Cost cols are round-trip totals (50/50 split shown
  as estimate); IV-premium decomposition not yet baked into m4_trades.
- `2026-05-04 (Session 12)`: M6 Attribution layer — produced
  `m4_trades_enriched.parquet` (5,274 × 87 cols, 1.48 MB) via new
  `scripts/backfill_m4_enriched.py` (IV-premium decomposition + per-leg
  Greeks + θ/ν ratios). Added 3 new endpoints (`/winners_vs_losers`,
  `/per_friday_best`, `/win_frequency`) and 3 new components
  (`M4WinFrequency`, `M4WinnersVsLosers`, `M4PerFridayBest`) mounted as
  "Attribution analysis" section with shared Δ chip. Extended
  `/contract_type_summary` and the dashboard summary strip with
  Avg Win / Avg Loss / Best Net / Worst Net / Best MFE / Worst MAE.
  Split IV bands 80-100 → 80-90 + 90-100 + 100+. Cleaned up expiry-class
  filter to chip-only (removed search input). Backend rebuilt + frontend
  restarted; verified in browser. UNCOMMITTED — pending user decision on
  commit grouping.
