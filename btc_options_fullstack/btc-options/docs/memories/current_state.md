# Current Project State

## Active Projects

- **M7 243-rule hybrid scaffolding (Session 32, 2026-05-18, COMMITTED `501699b`):**
  - 4 new files + scoped `main.py` router include. 243 rules =
    105 existing singles + 3 standalone cap_sl + 9 cap_sl×premium_sl
    2-way + 126 cap_sl×premium_sl×exit_hr 3-way. `rule_category` tag
    on each cell so the same grid serves as 105/108/117/231/243 views
    via the `?categories=` CSV filter on
    `/api/v1/m7_hybrid/iv_band_best_combo`.
  - **Backend**: `backend/app/api/m7_best_combo_hybrid.py` (rule menu +
    grid builder + parquet I/O, 3-tuple cache key matching canonical
    `_EXIT_CACHE` shape) + `m7_hybrid_results.py` (router) +
    `build_m7_best_combo_grid_hybrid.py` (one-shot script per RULE #5)
    + 16 tests, all green. Reuses canonical `_pick_best_per_band` /
    `_compute_all_exits` (cap_sl SQL already in canonical at
    `m7_results.py:445-454`). No canonical files modified.
  - **Frontend pending**: NEW `M7HybridRuleCategoryFilter.tsx` +
    `m7_hybrid_api.ts` + mount in M7 dashboard.
  - **Grid build pending** (~7h serial): gated on `m7_trades.parquet`
    reaching 125 fridays. Currently 123. The 2 missing fridays
    (05-08, 05-15) blocked on parallel 108-rule session's Phase E2,
    which is blocked on btc-collector spot 1m middle-gap fill
    (2026-05-02 → 05-15).
  - **Parallel session contract**: 108-rule session writes only NEW
    files: `m7_best_combo_108.py`, `m7_108_results.py`,
    `build_m7_best_combo_grid_108.py`, `test_m7_best_combo_108.py`,
    output `m7_best_combo_grid_v6_108.parquet`, mounted at
    `/api/v1/m7_108`. No canonical edits. Both grids will coexist.
  - **Plans**: `/home/abhis/.claude/plans/hybrid-build-current-session.md`
    + `/home/abhis/.claude/plans/108-rule-build-next-session.md`,
    both rewritten for strict separation this session.

- **M7 joint Δ+price-matched strangle variant (Session 30, 2026-05-17, UNCOMMITTED):**
  - Parallel strangle-construction policy alongside today's pure-delta
    sweep. For each (Friday × hour × expiry × delta_target), the joint
    picker walks both CE and PE strike candidates inside a `±0.05Δ`
    window (auto-widened to `max(0.05, 0.5×target)` when target ≤ 0.10
    for the sparse OTM tail) and picks the (CE_strike, PE_strike) pair
    minimising `|call_mark − put_mark|`. Accepted iff
    `gap / mean(mark) ≤ 0.15` AND `mean_mark ≥ $1` (deep-wing-dust
    guard). Else fall back to today's pure-delta closest-from-below
    picker. `match_mode` column distinguishes joint vs delta_fallback.
  - **Code**: `m7_strike_picker_joint.py` + `m7_batch_backtester_joint.py`
    (with `--append` / `--joint-delta-tol` / `--joint-price-tol-pct`) +
    `m7_joint_match_stats.py` + `build_m7_best_combo_grid_price_matched.py`
    + 9-test pytest module. `dataset: Literal["delta_match","price_match"]
    = "delta_match"` query param threaded through every M7 endpoint.
    All caches (`_TRADES_BY_DATASET`, `_EXIT_CACHE`, `_COVERAGE_CACHE`,
    `_GRID_STATE_BY_DATASET`, `_BUCKETED_GRIDS`) keyed by dataset.
  - **Frontend**: 2-button toggle `◆ Δ-match / ◆ Δ+Price match` at top
    of M7SweepDashboard. `M7JointMatchStats` KPI panel + 3 expandable
    breakdowns (per-Δ, per-IV-band, per-Δ×band) wrapped in a
    `JointStatsBoundary` error boundary. `dataset` prop threaded into
    `M7IvBandBestComboTable`, `M7BestComboCoverageTable`,
    `M7BestComboPathMarkers`, `M7LossesExplorer`,
    `M7BestComboMissedFridaysTable`.
  - **Data on disk**:
    - `~/btc-data/derived/m7/m7_trades_price_matched.parquet` — populated
      by detached container `m7-joint-full-backfill-1779044018` running
      at session end (log
      `/home/abhis/btc-data/logs/m7_joint_full_1779044018.log`).
      Expected ~25-30 min for 121 Fridays.
    - `~/btc-data/derived/m7/m7_paths_price_matched/friday_date=YYYY-MM-DD/part.parquet`
      Hive-partitioned, append-safe.
    - `~/btc-data/derived/m7/m7_best_combo_grid_v6_price_matched.parquet`
      NOT yet built — run via detached
      `docker compose run -d --rm` per RULE #5 (~4-5h). Until then,
      `dataset=price_match` on `/iv_band_best_combo` returns
      `status:no_data`.
  - **Verified live**: 9/9 pytest passing; Playwright toggle round-trip
    (Δ-match 34,166 trades ↔ Δ+Price match 216 trades on test backfill,
    206 joint / 10 fallback, no crash); stats endpoint shape verified
    flat; frontend TS build clean (no new errors).
  - **Deferred**: compare side-by-side mode, M7FridayBandDashboard
    toggle, M7 modal `dataset` propagation
    (`M7RuleComparisonModal`, `M7CellAnalysisModal`,
    `M7TradeDiagnosticModal`).
  - **⚠️ Pre-existing RULE #3 concern** in working tree (NOT from this
    feature): `scripts/margin_engine.py:124` and
    `frontend/src/utils/marginEngine.ts:152` both reduce
    `SAFETY_BUFFER_PCT` 0.20 → 0.10. Uncommitted. Must be reverted OR
    empirically re-verified against Delta UI before any commit touches
    those files.

- **M7 Friday-Band MTM Overlay panel (Session 29, 2026-05-17, UNCOMMITTED):**
  - New `GET /api/v1/m7/friday_band_mtm_overlay` endpoint at
    `backend/app/api/m7_friday_band_results.py:1187` (+512 LoC). Operates
    on each band's winning-combo trade universe (parity with markers
    endpoint). Single combined DuckDB query with `friday_date IN` +
    `trade_id IN` predicates under `_EXIT_COMPUTE_LOCK`. Carry-forward
    avg via `pivot_table.ffill()` + constant n_trades denominator.
    Columnar wire format. `trade_id` as string for JS Number precision
    safety.
  - New frontend panel `M7FridayBandMtmOverlayPanel.tsx` (~370 LoC)
    mounted at `M7FridayBandDashboard.tsx:227`. One chart per band with
    5 overlaid `LineSeries` (avg + best/worst/best-max/worst-min) +
    faint `n_trades_alive` on secondary scale. lightweight-charts v5
    with `time = minute*60` + `tickMarkFormatter` for minute-offset
    display. Legend de-dups slots sharing a trade_id; click opens the
    existing `M7TradePathChart` modal.
  - Backend rebuilt + frontend restarted; Playwright verified panel
    mounts cleanly and calls the new endpoint. Screenshot:
    `m7-fb-mtm-overlay-panel-mounted.png`.
  - 14 backend tests in `test_m7_friday_band_mtm_overlay.py`. 4
    synthetic-mock tests pass; 10 `@pytest.mark.slow` integration tests
    skip until grid is fresh.
  - **OPEN — Friday-band grid is stale** (May 13 file vs May 15 trades
    parquet). Every Friday-band endpoint returns 503, not just the new
    one. User needs to rebuild via:
    `docker compose run -d --rm --name m7-grid-rebuild_$(date +%s) backend python -m app.scripts.build_m7_friday_band_grid`
    Then re-run integration tests + Playwright visual verification.
  - Plan: `/home/abhis/.claude/plans/i-want-average-1-memoized-valley.md`
    (v2 — review-incorporated). Two fresh-context review agents caught
    8 blockers before coding.

- **M7 Loss Explorer redesign + warming patterns (Session 28, 2026-05-16, UNCOMMITTED):**
  - Losses Explorer now mirrors the dashboard's Best Combo per IV band
    table 1:1 via a new `cells` JSON param on `/losses_distribution`.
    Scope/ranking toggles removed in M7Sweep mode; universe view dropped.
    Friday-Band dashboard still uses the legacy scope toggle path.
  - Diagnostic modal closed the 24-cell spot-technicals gap (5m / 15m /
    30m / 1h / 4h / 1d × RSI / MACD hist / BB %b / ATR %) and added the
    9-field premium calibration block + cost summary + trade context.
  - Best-combo grid validator now also checks `rule_label` cardinality
    (was silently loading 21-rule grids after expansion to 96).
  - **Cells-mode warming pattern**: `/losses_distribution?cells=...`
    pre-checks `_EXIT_CACHE`; cold rules trigger async daemon threads
    (`_warmup_rule_async`) and the endpoint returns `warming=true`
    immediately. Frontend polls every 3s.
  - **Coverage warming pattern**: same applied to
    `/iv_band_best_combo/coverage` — picker+classifier moved to
    `_compute_coverage_payload`, daemon thread per cache_key writes to
    `_COVERAGE_CACHE`, endpoint returns `status='warming'` on cold key.
    Frontend polls every 2s. Solves the "500 Internal Server Error" the
    UI showed when the cold call exceeded browser/proxy timeouts.
  - **Tests**: 117 m7 passed (incl. 4 new: cells cold-cache warming,
    coverage endpoint warming, coverage cache hit short-circuit,
    coverage warmup idempotency).
  - **Open**: backend rebuild pending (waited per RULE #4 — multiple
    other Claude sessions active). Run `cd docker && docker compose up
    --build -d backend` when safe.

- **M-Month module — Phase A + B + B+ SHIPPED (Session 24, 2026-05-13, commit `864cd32`):**
  Plan: `/home/abhis/.claude/plans/i-want-to-do-wiggly-planet.md`.

  **Trade cycles (4 — split from previous 3):**
  - `monthly`: first-Mon 23:00 IST → same-month last-Fri 11:00 IST, current-month expiry
  - `bimonthly`: first-Mon entry/same-month exit, **next-month** expiry
  - `lastfri_monthly`: last-Fri 10:00 IST → next last-Fri 11:00 IST, next-month expiry
  - `lastfri_bimonthly`: last-Fri entry, **month-after-next** expiry (NEW per user clarification 2026-05-13)

  **Strike-matching entry policy**: retries every 5 min for up to 60 min
  until both legs land within tolerance of target Δ. CLI flags
  `--no-match` / `--resume`. New schema: `entry_ts_requested_utc`,
  `entry_ts_actual_utc`, `wait_minutes`, `match_quality`, `skipped_reason`.

  **96-rule exit menu derivation**: `/iv_band_best_combo` accepts
  `premium_sl_pct`, `max_profit_pct`, `margin_target_pct`, `hold_duration`.
  Composite "whichever fires first" via DuckDB CTE with
  `arg_min(triggered_by, ts)`. 11 hold-duration slots
  (natural/3d/5d/1w/2w/3w/4w/5w/6w/7w/8w).

  **Greeks per-trade endpoint**: `/trade_diagnostic?trade_id=…&bar_step=N`
  returns per-bar greeks trajectory (call/put δγθν + net + ratios).

  **Data on disk** (`~/btc-data/derived/m_month/`, ~2.3 GB):
  - 420 trades, 27 entry-month partitions (2024-02 → 2026-04)
  - monthly 174 trades, bimonthly 102, lastfri_monthly 134, lastfri_bimonthly 10

  **Verified live (Playwright)**: 4-cycle toggle + rule-config dropdowns
  re-rank cells correctly. Max profit 25% example: per-band winner
  flips from Δ0.45 ($826 natural) → Δ0.50 ($238 / 100% WR).

  **Tests**: 7 pytest cases in `backend/tests/test_m_month_strike_matching.py`,
  all passing.

  **Reviewer-flagged fixes applied** (code-reviewer agent):
  `arg_min` (was `ANY_VALUE` — exit_reason was unattributable),
  cache key normalisation, distinct `expiry_variant` per cycle.

  **Phase C (full M7 dashboard surface port) — DEFERRED to next 4–5 sessions:**
  Headline / Full Coverage / Missed Sessions / 52-col Best Combo / Filter
  bar / Capital sizing / Conservative preset / Excel / Trade Diagnostic
  modal / Leg Attribution / Losses Explorer / Cell Winners-vs-Losers /
  Cell Worst Anchors.

  **Phase E (adjustment engine) — DEFERRED.** Roll-untested / close-tested
  / spot-distance triggers. Per-bar replay engine needed.

  **Stage-2 tuning** (not blocking):
  - Lower strike-matching tolerance to widen lastfri_bimonthly coverage
    (currently only 10 trades / 3 anchors)
  - Pre-compute grid parquet for cross-rule ranking
  - Composite score + capital sizing port from m7_best_combo

- **M-Month stage-1 (archived in Session 22, superseded by Session 24):**
  Plan: `/home/abhis/.claude/plans/i-want-to-do-wiggly-planet.md`.
  All 3 trade cycles enumerated; backend backtester + 2 routers + frontend
  dashboard live. Background backtest running for Feb-Jun 2024 (all 3
  cycles, 16 work items, ~30 min total). Log: `/tmp/m_month_backtest.log`.

  **Trade cycles:**
  - Monthly: first-Mon 23:00 IST → same-month last-Fri 11:00 IST, current-month expiry (~28→0 DTE)
  - Bimonthly: same entry/exit, next-month expiry (~58→30 DTE)
  - Last-Fri rolling: last-Fri 10:00 IST → next last-Fri 11:00 IST, next-month expiry (~28→0 DTE)

  **Stage 1 limits (acknowledged):**
  - Hold-to-hard-cap exit only (no 96-rule menu yet)
  - Single anchor per cycle (no Mon/Tue/Wed × hours sweep)
  - No adjustments (delta-rebalance / roll-untested / spot-distance)
  - No composite score, capital sizing, drilldowns
  - Self-contained dashboard (no shared M7 components)

  **Stage 2 work (highest priority next):**
  - Land the 96-rule exit menu including new 11-slot `fixed_hold_duration`
    family (`3d/5d/1w/2w/3w/4w/5w/6w/7w/8w/last-Fri`, clipped to cycle expiry).
  - Pre-computed grid parquet for faster picker queries.
  - Composite score + capital sizing port from m7_best_combo.

  **Files added:**
  - `backend/app/analytics/m_month_batch_backtester.py`
  - `backend/app/api/m_month_results.py`
  - `backend/app/api/m_month_best_combo.py`
  - `frontend/src/services/m_month_api.ts`
  - `frontend/src/pages/MMonthSweepDashboard.tsx`
  - `backend/app/main.py` (router registration)
  - `frontend/src/App.tsx` (M_MONTH_SWEEP mode)

  Verified live with Playwright: M-Month tab visible, cycle toggle works,
  Best Combo table renders Feb 2024 monthly data, M7 dashboard intact.

- **M7 Capital-Preservation Strategy Explorer — Phase 0/1/2 COMPLETE (Sessions 21+23, 2026-05-12 + 2026-05-13):**
  Plan: `/home/abhis/.claude/plans/now-for-best-combo-lively-creek.md`.
  Commits: `4405d0b` (Phase 0+1 backend + Conservative preset + rule-comparison
  modal), `1d83f2b` (Friday Coverage drilldown UI Features A/B/C).
  Session 23 (today) closes Phase 1 polish: pct_drop formula fix
  (`(peak − trough) / max(peak, |trough|, 0.01)` — bounded even when
  peak ≤ 0) and Pro Metrics column group toggle.

  **v6 grid build COMPLETED 2026-05-13 01:12 IST** (4h 20m runtime,
  206,016 cells, 28 MB). 2,016 cells fewer than v4 — those were the
  NaN-tainted low-Δ cells correctly dropped by the Phase 0A fix.
  Output: `/home/abhis/btc-data/derived/m7/m7_best_combo_grid_v6.parquet`.
  Backend serves v6 directly; v4 stays as fallback path.

  **Shipped this session:**
  - Phase 0A — NaN-gross trades dropped at all aggregation sites
    (`_build_grid`, `_best_cells_for_metric`, `/iv_band_summary`,
    `/missed_fridays`, `/iv_band_full_coverage`). Root cause: 0.10Δ
    trades with `put_entry_mark = NaN` (unpriced put leg) cascaded NaN
    through gross/net/MTM → counted as losers but mean = NaN → "Avg
    loss" displayed `—`.
  - Phase 0B — `_pick_best_per_band` filters: `min_hit_pct` (default 50;
    drops cells where (n_trades − n_hard_cap)/n_trades < X%);
    `max_loss_cap_pct`; `max_drop_peak_to_trough_pct`.
  - Phase 1 — Path peak-trough-peak SQL (CTE in `mtm_sql`). Pandas
    columns: peak/trough means, rel_time fields, drop/recovery, alt-net.
  - Phase 1 — Pro-trader metrics: `composite_score`, `sharpe_per_trade`,
    `sortino_per_trade`, `calmar_like` (grid-load enrichments from
    existing cell columns + new stdev fields). VaR/CVaR, worst-5,
    max_consec_loss_dollars, last-26w avg-net & win-rate (built into v6
    grid).
  - Phase 1 — 3 new diagnostic endpoints:
      - `GET /iv_band_best_combo/rule_comparison` — all 96 rules at a
        fixed (band, expiry, Δ, hour). Sortable. Verified with curl.
      - `GET /iv_band_best_combo/cross_band_check` — same rule across
        all 10 bands (regime-fragility check). Verified: e.g.
        `sl100_exit_hr_15 @ 0.5Δ / 00:00` is robust 75-100% WR in all
        6 covered bands.
      - `GET /iv_band_best_combo/single_combo_simulation` — "what if I
        always traded this combo?" counterfactual. At $600 cap, that
        combo: n=119, win=81.5%, avg=$20.38, scaled=$77.44 per Friday.
  - Phase 1 frontend (BestComboTable):
      - "◇ Conservative" preset button (Capital $600, deploy 100%,
        composite_score primary, DD cap avg_min_mtm@30, max_loss 25%,
        max_drop 30%, min_hit 50, pure mode).
      - New inputs: Hit % ≥, Max loss %, Max drop %.
      - Composite/Sharpe/Sortino/Calmar entries in PRIMARY_GROUPS.
      - Hit % column with green/amber/red coloring.
      - Click any row → `M7RuleComparisonModal` (NEW component) shows
        all 96 rules sortable, picked rule starred, ESC closes.
  - Grid path bumped: `m7_best_combo_grid_v6.parquet` (v4 fallback).

  **Behavior change confirmed live (Playwright):**
  - 20-30 default pick (no Conservative): `22:00 / next_to_next (Mon) /
    Δ=0.5 / n=22 / 87% win` (was `23:00 / 0.10Δ / n=3` NaN-tainted).
  - 20-30 Conservative pick: `23:00 / Δ=0.15 / SL100+Exit_15:00 /
    n=16 / 87.5% win / Hit %=100` (composite_score=0.223).

  **Session 23 additions (Phase 1 closeout):**
  - Pro Metrics column group toggle (◇ Pro metrics button next to
    Conservative). When on, 14 v6-only columns: Sharpe / Sortino /
    Calmar / VaR 95 / CVaR 95 / Worst-5 / Peak-1 $ / Trough $ /
    Peak-2 $ / Δ P1→T % / t(Peak-1) / t(Trough) / t(Peak-2) /
    Δ T→P2 %. All scaled by lots/100 when sizing is on.
  - pct_drop formula fix: `(peak − trough) / max(peak, |trough|, 0.01)`.
    FE falls back to recomputing from peak/trough means when v6 grid's
    pre-fix value is null.
  - Friday Coverage Features A/B/C all shipped + verified live.

  **All Phase 1 items COMPLETE.** No follow-ups blocking.

  **Deferred per plan (not blocking):**
  - Weight tuning UI for composite (locked at 1,1,1).
  - Pareto frontier per band.
  - Composite/sizing port to Backtest / Historical dashboards.
  - 5-layer / 34-test full protocol — partial regression done; full
    run is a "nice to have" before next refactor.

## Active Projects (prior)
- **M7 capital-deployment analysis — DELIVERED (Session 20, 2026-05-12):**
  Comprehensive 5-scenario per-cell comparison for
  `20-30 IV × next_to_next (Mon) × Δ=0.5`. Winner identified:
  **Setup 5 = 12am IST entry + SL100% + Fixed Exit @15:00 IST**
  (91.7% WR, $23.46 avg P&L, smallest worst-case loss −$30.17).
  Recommended deployment for ₹1 lakh wallet: 40% (~$480 margin / 225 lots
  scaled from 100-lot historical) → worst-Friday loss ≈ −5.7% wallet,
  expected ~100% wallet return over 6 months at this size. Analysis
  output `scripts/m7_4setup_comparison.xlsx` (9 sheets, untracked).
  No code changes — this was an analysis-only session driven by
  `m7_results._derive_exits()` + per-trade path walks.

- **M7 Full Coverage — Force-fit ⇄ Touched-band toggle SHIPPED (Session 19, 2026-05-11, committed in `cc6f313`):**
  New `coverage_mode` toggle on the M7 Full Coverage table:
  - `force_fit` (default — today's behavior): missed Fridays go into any
    cell whose `(hour × expiry × delta)` matches; band is ignored;
    tiebreak by trade P&L; closest-fallback for non-matchers.
  - `touched_band` (new): missed Fridays go only into cells whose band
    the Friday's IV touched at SOME hour during the day; tiebreak by
    cell historical avg net P&L; no closest-fallback (unmatched =
    uncovered).
  Backend: `m7_full_coverage.py` (`_classify_fridays_to_cells` takes
  `coverage_mode` param, endpoint exposes it as query string).
  Frontend: `M7IvBandFullCoverageTable.tsx` (button group, localStorage
  persistence, mode-aware footer + tooltips). Verified live in Playwright.
  Recovery comparison on 32 missed Fridays:
  - Force-fit: 31 recovered / 61% WR / +$447 / $14.43 avg
  - Touched-band: 29 recovered / 41.4% WR / +$275 / $9.47 avg
  Touched-band is the disciplined view for live trading; force-fit is
  the upper-bound retail estimate.

- **M7 best-combo grid v3 + standalone CLI builder SHIPPED (Session 18, committed in `cc6f313`):**
  Schema bumped to v3 to include `entry_hour_ist` as a sweep dimension.
  208,032 cells across 96 rule variants × 7 expiries × 8 deltas × 10 IV
  bands × entry hours. Persisted at
  `/home/abhis/btc-data/derived/m7/m7_best_combo_grid_v3.parquet`.
  Build now runs out-of-process via
  `python -m app.scripts.build_m7_best_combo_grid` (the previous
  in-FastAPI thread was starving the event loop and causing "Failed to
  fetch" during warmups). Persists across restarts in ~50ms cold load.

- **M7 loss-anatomy panel — 65 indicators across 11 categories (Session 18, committed in `cc6f313`):**
  Extended from 46 → 65 by adding RSI(14), MACD histogram, Bollinger %B,
  and ATR% across 4 new timeframes (15m, 30m, 1h, 1d) on top of the
  existing 5m+4h. Full list in `docs/m7_loss_indicators.md`.
  **Critical fix**: `_compute_all_exits` keep-list was silently dropping
  every `entry_*_<tf>` indicator before the cell-winners-vs-losers
  analysis. The same latent bug had been hiding the original 5m+4h
  indicators all along. Now fixed — all 65 indicators reach the analysis
  panel.

- **M7 loss-trade analysis refinement — IN PROGRESS (Session 18+):**
  Active work surface is the Session-16 loss-classifier + losses-explorer
  + cell-winners-vs-losers components (UNCOMMITTED in branch). Goal:
  perfect the loss attribution before considering M8 enrichment.

- **Future — M7×M8 skew enrichment (not started, proposed Session 17):**
  Point-in-time join of M8 per-minute RR_25/BF_25/ATM IV onto M7 trades
  by `entry_ts`, adding `entry_rr_25` + `entry_bf_25` columns to
  `m7_trades_enriched.parquet` and exposing them as filter chips/columns
  on the existing M7 page. **Defer until current M7 loss-trade work
  is shipped.**

- **M8 — Per-minute current-expiry IV / ATM Δ / 25Δ skew analytics — SHIPPED (UNCOMMITTED) (Session 17, 2026-05-07):**
  New analytics module `backend/app/analytics/m8_current_expiry_skew.py`
  (~330 LOC) producing the first per-minute *nearest-expiry* IV/skew
  dataset across the platform's full 1m spot history (Dec 2023 →
  2026-05-06, 1,247,263 rows × 20 cols, 857 expiries). Distinct from
  `options_enriched_*.parquet`, which carries constant-maturity (7d/14d/
  30d/60d) IV via interpolation across all live expiries — M8 captures
  the actual nearest-expiry surface that dominates short-dated decisions.

  Per-minute outputs: ATM strike, ATM call+put marks, ATM IV (avg of
  CE/PE solved IVs), ATM call/put delta, nearest-25Δ call strike + IV,
  nearest-25Δ put strike + IV, RR_25 (call_iv − put_iv), BF_25
  ((call_iv + put_iv)/2 − atm_iv), plus spot/spot_ret_1m_pct/
  spot_move_15m_pct context. Algorithm: walks each expiry window
  `(prev_settle, this_settle]`, pre-pivots chain into ts × strike ×
  {CE,PE} matrices, then per-minute does vectorized `implied_vol_vec` +
  analytic `_delta_vec` across ATM±25 strikes.

  Outputs on disk:
  - `/home/abhis/btc-data/derived/m8_current_expiry_skew.parquet` (110 MB)
  - `/home/abhis/btc-data/derived/m8_current_expiry_skew.xlsx` (49 MB,
    last 6 months)

  CLI flags: `--since/--through ISO`, `--xlsx-months N` (default 6),
  `--xlsx-only` (rebuild xlsx from existing parquet without re-running
  the ~80 min backfill).

  Sanity: ATM call Δ median +0.502, put Δ ≈ −0.498. 25Δ call IV ≈ ATM IV
  + 1.4% on avg. `current_expiry` rotates cleanly at each 12:00 UTC.
  ~25k minutes (~2%) NaN ATM IV (chain gaps / boundary minutes).

  No backend/frontend integration yet — ad-hoc parquet for analysis.

  **UNCOMMITTED — file pending commit:**
  - `backend/app/analytics/m8_current_expiry_skew.py` (new)
  - Outputs in `~/btc-data/derived/` are untracked data (matches
    M2/M4/M7 convention).

- **M7 Full-Coverage IV-band table + Option Y classifier — SHIPPED (UNCOMMITTED) (Session 16, 2026-05-07):**
  Every one of the 121 Fridays in the M7 dataset is now attributed to one of
  the 10 best-cell rules so the headline view covers the full universe — no
  orphan/missed Fridays. New endpoint
  `GET /api/v1/m7/iv_band_full_coverage` (~340 LOC, in
  `backend/app/api/m7_full_coverage.py`) classifies each Friday into
  `rule` (strict 4-dim match: band, hour, expiry, delta) /
  `force_fit` (matches some cell on hour+expiry+delta but in a different IV
  band, picked by best PnL) /
  `closest_fallback` (no hour-expiry-delta match anywhere; picked by
  distance `D = 100·|Δ| + 10·|expiry_idx| + |hour|`, ties on PnL) /
  `uncovered` (filters wipe Friday out). **Option Y semantics**: the
  `assigned_band` is the trade's ACTUAL `entry_atm_iv_band`, NOT the cell's
  nominal band — the cell rule is used only to FIND the right trade per
  Friday, and the band label tracks where the trade's entry IV actually
  sits. Rationale: this matches the user's intuition that "band 30-40"
  means "entry IV was 30-40%" rather than "the rule from band 30-40's best
  cell was used".

  Frontend: `M7IvBandFullCoverageTable.tsx` (~395 LOC) with two stacked
  sub-rows per band ("Rule" / "All (n)") and full Headline column set
  (33 metrics including winners-only and losers-only MTM splits). Mounted
  in `M7SweepDashboard.tsx` directly below the existing
  `M7IvBandSummaryTable`.

  Tests: 10 unit tests passing in `backend/tests/test_m7_full_coverage.py`
  (rule-strict-match, force-fit-best-pnl, closest-fallback-distance,
  distance-by-Δ-first, rule-beats-force-fit, universe-counts-partition,
  bucket-cardinality, etc.).

  Verified live: at Δ=0.30, 121 Fridays partition cleanly (88 rule + 33
  force-fit + 0 closest-fallback + 0 uncovered). At Δ=0.05: 115 trades
  (6 Fridays had no Δ=0.05 sim) → 48 rule + 59 force-fit + 8
  closest-fallback. Spot-check Oct 10 2025 → assigned to band 30-40 as
  force-fit (its actual entry IV 31.66% for the hour=23 next-to-next-Mon
  Δ=0.50 trade), confirming Option Y behavior.

  **UNCOMMITTED — files pending commit:**
  - `backend/app/api/m7_full_coverage.py` (new)
  - `backend/tests/test_m7_full_coverage.py` (new)
  - `frontend/src/components/m7/M7IvBandFullCoverageTable.tsx` (new)
  - `scripts/m7_exit_rule_sweep.py` (new) +
    `scripts/m7_exit_rule_sweep.xlsx` (output)
  - `backend/app/main.py` (M, 1-line router include)
  - `frontend/src/pages/M7SweepDashboard.tsx` (1-line import + 1-line render)

- **M7 25-variant exit-rule sweep — SHIPPED (UNCOMMITTED) (Session 16):**
  `scripts/m7_exit_rule_sweep.py` calls `/aggregate` 175× (25 rules × 7
  metrics) across all 7 expiries × 8 deltas. Output:
  `m7_exit_rule_sweep.xlsx` (8 sheets: raw long table + pivots
  pivot_net / pivot_winr / pivot_minmtm / pivot_n).
  - Rules: baseline / max_profit_{10,20,25,30}% / margin_target_{10,20,25,30}% /
    premium_sl_{50,75,100}% / fixed_exit_hr_{05,08,10,12,15,17:30} IST /
    common max-profit + premium-SL combos (max20_sl50, max20_sl75,
    max30_sl50, max30_sl75).
  - **Top by avg net P&L (gated WR ≥ 60%, n ≥ 20):**
    `current Sat Δ=0.50 baseline` +$25.73/73% WR (n=847);
    `next_to_next Mon Δ=0.50 baseline` +$23.81/84% WR (n=833) —
    cleanest contract, best risk-adjusted setup.
  - **Top by lowest drawdown:** `monthly Δ=0.10 exit_hr_12` +$0.59/60% WR,
    min MTM -$4.57.
  - **Insight:** most exit rules ≈ baseline at high-Δ tier — triggers
    rarely fire before Sat 17:30 IST hard cap on a 1-day hold.
    `max_profit_30` mildly improves WR on next_to_next Δ=0.50 (84.27% vs
    84.03%) without sacrificing return. Skip everything ≥30 DTE —
    quarterly didn't make any list.
  - **Open follow-up (cut off by usage limit):** extend sweep with finer
    % grid (e.g. max_profit % at 5/10/15/20/25/30/35/40/50/75, same for
    margin_target and premium_sl, plus 2-way and 3-way crosses) to find
    the genuine optimum % per cell.

- **BTC Trade Copilot — Chunk 1 (Per-leg attribution) SHIPPED (Session 15, 2026-05-06):**
  10-chunk plan at `/home/abhis/.claude/plans/phase-1-defining-the-witty-dawn.md`
  evolves the platform from backtest viewer into a trade copilot covering the
  user's 6-phase vision (Phase 6 = M7, already built; Phases 1–5 = chunks 1–10).
  Chunk 1 (per-leg attribution) shipped as `d6a9ec5`: per-leg PnL with sum
  identity ≡ gross_pnl_usd to 6 decimals, leg_winner classification,
  delta/iv/premium skew at entry + 5-bucket cuts, 2 new endpoints
  (`/leg_attribution`, `/leg_skew_heatmap`), 2 new dashboard components,
  5 unit tests + 5 historical-validation tests against the full 34k dataset
  all passing. Failure policy: block recommendations, ship visualizations.
  Chunks 2–10 are the natural follow-ons (Chunk 2 = baseline + theta/vega is
  the easiest next step since the enriched parquet already has those cols).

- **M7 Friday→Saturday strangle/straddle sweep — COMPLETE (Session 13-14, 2026-05-05/06):**
  Plan at `/home/abhis/.claude/plans/go-through-the-project-linked-dragonfly.md`.
  - Backend: `m7_batch_backtester.py` + `m7_results.py` API. 34,166 trades,
    121 Fridays, 121 path partitions. Enriched with calibration_v2 join.
  - Frontend: `M7SweepDashboard.tsx` + 7 components. Exit-hour dropdown
    (Sat 05:00→17:30 IST) added. Filter bar, headline strip, heatmaps,
    best-combo table, trade log, 1m path chart.
  - Tests: 155 passing.
  - Status: ✅ FULLY COMPLETE. Committed as `9211594`.

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
backend live_recorder         C:/Users/Abhis/btc-collector/
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
- `2026-05-07 (Session 16)`: M7 Full-Coverage IV-band table + Option Y
  classifier + 25-variant exit-rule sweep. New endpoint
  `/api/v1/m7/iv_band_full_coverage` partitions all 121 Fridays across the
  10 best-cell rules (rule / force_fit / closest_fallback / uncovered).
  Option Y: assigned_band tracks each trade's actual entry IV band
  (chosen after iteration with user — Option X used cell's nominal band,
  felt counter-intuitive for Oct 10 2025 case). Frontend
  `M7IvBandFullCoverageTable.tsx` with 2 stacked sub-rows per band
  (Rule / All) and full 33-metric column set including winners-only and
  losers-only MTM splits. 10 unit tests passing. Sweep script ranks 25
  exit-rule variants × 7 expiries × 8 deltas; outputs xlsx with 4 pivots.
  Headline: `next_to_next Δ=0.50 baseline` is the cleanest setup (84% WR,
  +$23.81 avg net). UNCOMMITTED — 4 new files + 2 one-line edits pending
  commit. Open follow-up: finer % grid sweep cut off by usage limit.
