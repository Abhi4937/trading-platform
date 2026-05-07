# Handoff Log

## Last Session
**Who:** Claude
**Date:** 2026-05-07 (Session 17 — M8 current-expiry IV / ATM Δ / 25Δ skew analytics shipped)
**Branch:** `mainbranch-gemini_claude`

### Session 17 highlights
- **M8 SHIPPED (UNCOMMITTED)** — new analytics module
  `backend/app/analytics/m8_current_expiry_skew.py` (~330 LOC). Per-minute
  current-expiry IV / ATM Δ / 25Δ skew across the full 1m spot history
  (Dec 2023 → 2026-05-06, 1,247,263 rows × 20 cols).
  - **What's different from `options_enriched_*.parquet`**: that one carries
    constant-maturity (7d/14d/30d/60d) ATM IV + 25Δ skew via interpolation
    across all live expiries. M8 captures the *actual nearest-expiry surface*
    (the soonest expiry whose 12:00 UTC settle is strictly after each
    minute) — which dominates short-dated decisions.
  - **Per-minute output columns**: `ts_unix/ts_utc/ts_ist`,
    `spot/spot_ret_1m_pct/spot_move_15m_pct`, `current_expiry/dte_minutes`,
    `atm_strike/atm_call_mark/atm_put_mark`, `atm_iv_pct/atm_call_delta/atm_put_delta`,
    `call_25d_strike/call_25d_iv_pct`, `put_25d_strike/put_25d_iv_pct`,
    `rr_25/bf_25` (in IV pts).
  - **Algorithm**: walks each expiry window `(prev_settle, this_settle]`,
    pre-pivots the chain into `ts × strike × {CE,PE}` matrices, then
    per-minute (a) picks ATM strike with both legs marked, (b) vectorized
    `implied_vol_vec` + analytic `_delta_vec` across ATM±25 strikes, (c)
    picks call strike with Δ closest to +0.25 and put strike with Δ closest
    to −0.25, (d) computes RR_25 / BF_25 in IV %.
  - **Reuses (no edits)**: `backend/app/analytics/enrich_options.py` —
    `implied_vol_vec`, `_norm_cdf_vec`, `expiry_dt_unix`, `list_expiries`,
    `load_chain_for_expiry`. `backend/app/core/greeks.py` (compute_greeks
    fallback path).
  - **CLI flags**: `--since/--through ISO` (subset window),
    `--xlsx-months N` (default 6), `--xlsx-only` (skip backfill, just
    rebuild xlsx from existing parquet — useful when xlsx fails or you
    want a different window).
  - **Outputs on disk** (NEW, Session 17):
    - `/home/abhis/btc-data/derived/m8_current_expiry_skew.parquet`
      — 110 MB, 1.25M rows × 20 cols, full history.
    - `/home/abhis/btc-data/derived/m8_current_expiry_skew.xlsx`
      — 49 MB, 255,518 rows (last 6 months: 2025-11-06 → 2026-05-06).
  - **Sanity checks (verified)**: ATM call Δ median = +0.502, put Δ ≈ −0.498
    (centered as expected). 25Δ call IV ≈ ATM IV + 1.4% on avg (sensible
    smile). `current_expiry` rotates cleanly at each 12:00 UTC settle.
    ~25,000 minutes (~2%) have NaN `atm_iv_pct` (chain gaps / boundary
    minutes) — expected.
  - **No backend or frontend changes**. M8 is a standalone analytics
    artifact for now; ad-hoc analysis is via parquet/xlsx. Dashboard
    integration is a separate session if desired.

### Session 16 highlights
- **M7 Full-Coverage IV-band table SHIPPED (UNCOMMITTED)** — every one of the
  121 Fridays now assigned to exactly one of the 10 best-cell rules so the
  headline summary covers the full universe (no orphans).
  - New backend endpoint `GET /api/v1/m7/iv_band_full_coverage` in
    `backend/app/api/m7_full_coverage.py` (~340 LOC). Same params as
    `/iv_band_summary` plus a classifier that partitions Fridays into
    `rule` (strict 4-dim match) / `force_fit` (matches hour+expiry+delta
    of some best cell, different IV band) / `closest_fallback` (no
    hour-expiry-delta match, picked by distance
    `D = 100·|Δ| + 10·|expiry_idx| + |hour|`) / `uncovered`. Each row
    returns two metric blocks: `rule_only` (today's strict numbers) and
    `all_fridays` (rule + force-fit + closest-fallback).
  - **Option Y classification** (chosen after iteration with user): the
    `assigned_band` is always the trade's ACTUAL `entry_atm_iv_band`, NOT
    the cell's nominal band. Cell rule is used only to FIND the right
    trade for each Friday; the band label tracks where the trade's
    entry IV actually sits. Reasoning: makes "band 30-40" mean "entry IV
    was 30-40%" (which is what the user expected) instead of "the rule
    that came from band 30-40's best cell was used" (which was the
    initial Option X behavior). Verified: Oct 10 2025 lands in band
    30-40 (its actual entry-IV band for the (hour=23, next-to-next Mon,
    Δ=0.50) trade), not 0-20 (the cell's nominal band).
  - New frontend component
    `frontend/src/components/m7/M7IvBandFullCoverageTable.tsx` (~395 LOC).
    Two stacked sub-rows per band ("Rule" / "All (n)") with full Headline
    column set: 33 metrics including winners-only and losers-only MTM
    splits (avg/largest win MTM, avg max/min MTM (W), max/min MTM (W),
    n-winners-below-avg-min, mirror set for losers, ret/credit (W),
    ret/margin (W)).
  - Mounted in `backend/app/main.py` (1-line router include) and
    `frontend/src/pages/M7SweepDashboard.tsx` (1-line import + 1-line
    render below the existing `M7IvBandSummaryTable`).
  - Tests: 10 passing in `backend/tests/test_m7_full_coverage.py`
    (rule-strict-match, force-fit-best-pnl, closest-fallback-distance,
    distance-by-delta-first, rule-beats-force-fit, universe-counts-
    partition, etc.).
  - Verified live: at Δ=0.30, all 121 Fridays partition cleanly
    (88 rule + 33 force-fit + 0 closest-fallback + 0 uncovered). At
    Δ=0.05: 115 total (no Δ=0.05 sim for 6 Fridays) → 48 rule + 59
    force-fit + 8 closest-fallback + 0 uncovered.

- **Exit-rule sweep script SHIPPED (UNCOMMITTED)** —
  `scripts/m7_exit_rule_sweep.py` (~205 LOC). Sweeps 25 exit-rule variants
  (baseline / max_profit_{10,20,25,30}% / margin_target_{10,20,25,30}% /
  premium_sl_{50,75,100}% / fixed_exit_hr_{05,08,10,12,15,17:30} IST /
  common max-profit + premium-SL combos) across all 7 expiries × 8 deltas.
  Output: `scripts/m7_exit_rule_sweep.xlsx` (135 KB, 8 sheets including
  pivot_net, pivot_winr, pivot_minmtm, pivot_n).
  - **Top by avg net P&L** (gated WR ≥ 60%, n ≥ 20): `current (Sat) Δ=0.50
    baseline` +$25.73/73% WR, n=847. `next_to_next Δ=0.50 baseline`
    +$23.81/84% WR, n=833 (best risk-adjusted — cleanest contract).
  - **Top by lowest drawdown:** `monthly Δ=0.10 exit_hr_12` +$0.59/60% WR,
    min MTM −$4.57. `current Δ=0.05 max_profit_30` +$0.33/68% WR, min MTM
    −$5.41.
  - **Key insight:** most exit rules ≈ baseline at the high-Δ tier —
    triggers rarely fire before the Sat 17:30 IST hard cap. `max_profit_30`
    mildly improves WR on next_to_next Δ=0.50 (84.27% vs 84.03%) without
    sacrificing return.
  - Skip everything ≥30 DTE — quarterly didn't make any list.

### What's on disk
```
/home/abhis/btc-data/derived/m7/m7_trades.parquet                 8.5 MB  (34,166 trades, 82 cols)
/home/abhis/btc-data/derived/m7/m7_trades_enriched.parquet        8.76 MB (34,166 trades, 91 cols)
/home/abhis/btc-data/derived/m7/m7_paths/                         121 partitions (friday_date=YYYY-MM-DD)
/home/abhis/btc-data/derived/m8_current_expiry_skew.parquet       110 MB  (1.25M rows × 20 cols, full history)  ← NEW Session 17
/home/abhis/btc-data/derived/m8_current_expiry_skew.xlsx          49 MB   (last 6 months, 255,518 rows)         ← NEW Session 17
```

### Pending follow-ups
- **Active focus (Session 18+):** Refining M7 loss-trade analysis. The
  loss-classifier / losses-explorer / cell-winners-vs-losers components
  from Session 16 (currently UNCOMMITTED) are the active work surface.
- **Future — proposed M7×M8 join (not started):** point-in-time merge
  of M8's per-minute RR_25/BF_25/ATM IV onto M7 trades by `entry_ts`.
  Adds `entry_rr_25` + `entry_bf_25` columns to `m7_trades_enriched.parquet`,
  surfaced as filter chips/columns on the existing M7 page (no new
  page). Discussed Session 17 — answers "what was the entry-time skew
  for the worst losers". **Defer until current M7 loss-trade work
  is shipped.**
- **Future — proposed M8 page (only if time-series view needed):** would
  warrant a dedicated page if you want per-minute RR_25 across all
  expiries with M7 trade markers overlaid. Skip until M7×M8 column
  enrichment proves insufficient.
- **COMMIT** Session 16 work (UNCOMMITTED currently). 4 new files +
  2 one-line edits:
    - `backend/app/api/m7_full_coverage.py` (new)
    - `backend/tests/test_m7_full_coverage.py` (new)
    - `frontend/src/components/m7/M7IvBandFullCoverageTable.tsx` (new)
    - `scripts/m7_exit_rule_sweep.py` (new)
    - `backend/app/main.py` (M)  — 1-line router include
    - `frontend/src/pages/M7SweepDashboard.tsx` (M, untracked-? actually
      wired) — 1 import + 1 render line for the new table
- **COMMIT** Session 17 (M8) work (UNCOMMITTED). 1 new file:
    - `backend/app/analytics/m8_current_expiry_skew.py` (new, ~330 LOC)
    - Output parquet+xlsx live in `/home/abhis/btc-data/derived/` (untracked
      data path — same convention as M2/M4/M7).
- **Open question (cut off by usage limit at end of session):** "what is
  the best percentage it can get" for % based exits — i.e. extend the
  exit-rule sweep with a finer % grid (e.g. 5/10/15/20/25/30/35/40/50/75
  across max-profit %, margin-target %, premium-SL %, plus 2-way and
  3-way combos) to find the genuine optimum per (expiry, Δ) cell.
- Chunks 2–10 of the Trade Copilot plan still pending
  (`/home/abhis/.claude/plans/phase-1-defining-the-witty-dawn.md`).

### Notable findings exposed by Session 16
- **Mar 7, 2025 is the only Friday with entry ATM IV ≥ 100%** (102.36% at
  hour 22 IST). It's the sole rule trade in band 100+. The 100+ band's
  "All Fridays" set under Option Y has only 1 trade (Mar 7) because
  force-fit Fridays whose best-rule trade matches the 100+ cell rule
  have actual entry IVs in lower bands → they get assigned to those
  lower bands instead.
- **Oct 10, 2025 — the BTC flash crash** is fully captured in the path
  data (1m bars, peak ATM IV ~73% mid-trade) but enters in IV band 30-40
  for its best-rule trade because M7 buckets by entry IV, not peak IV
  during hold. Path peak captured separately in `max_min_mtm` cols.
- **next_to_next (Mon)** is the cleanest contract — 84% WR at Δ=0.50,
  +$23.81 avg net, drawdown comparable to current Sat. Validates the
  Session 12 finding that next_to_next is the strongest workhorse.

---

## Previous Session (15)
**Who:** Claude
**Date:** 2026-05-06 (Session 15 — Trade Copilot plan + M7 enrichment commit + Chunk 1 per-leg attribution shipped)
**Branch:** `mainbranch-gemini_claude`

### Session 15 highlights
- **10-chunk Trade Copilot plan** designed and approved.
  See `/home/abhis/.claude/plans/phase-1-defining-the-witty-dawn.md`.
  Maps the user's 6-phase trading-system vision onto the existing M7 + LiveSignal
  pages. Each chunk has a quantitative pass/fail bar against the 34,166-trade
  dataset; failure policy = "block recommendations, ship visualizations".
- **Pre-existing M7 enrichment baseline COMMITTED**: `0aa0c96` (14 files,
  1,803 insertions). Exit-derivation cache, per-trade peak/trough MTM during
  hold (not full path), exact exit costs, 30+ named metrics, missed-Fridays
  endpoint, best-combo path-markers endpoint.
- **Chunk 1 — Per-leg attribution SHIPPED**: `d6a9ec5` (10 files, 1,322 insertions).
  Backend: `_add_entry_skew_columns()` (delta_skew/iv_skew_pct/premium_skew_usd
  + 5-bucket cuts), per-leg PnL + per-leg max/min MTM in `_compute_all_exits`,
  leg_winner classification (both/call_only/put_only/neither), 8 new metrics +
  4 share metrics, 2 new endpoints (`/leg_attribution`, `/leg_skew_heatmap`),
  /meta enriched. Frontend: 2 new components (M7LegSkewHeatmap +
  M7LegAttributionTable), filter bar chips, dashboard mount. Tests: 5 new unit +
  5 new historical validation against full 34k dataset.
- **All 170 unit tests + 5 slow historical-validation tests pass.** Slow tests
  opt in with `pytest -m slow` (registered in new `tests/conftest.py`).
- **Hand-verified one trade**: `(call_entry_mark − exit_call_mark) × qty × 0.001`
  + put-side equivalent sums to `gross_pnl_usd` to 6 decimals (formula matches
  `m7_batch_backtester` line 680).
- **Headline finding** from new view: at Δ=0.30, the worst single-trade losers
  are ALL `leg_winner=call_only` outcomes — i.e. trades where BTC moved sharply
  down, the put leg ran hard against us while the call leg decayed. Validates
  the diagnostic value of the per-leg view.

### What's on disk
```
/home/abhis/btc-data/derived/m7/m7_trades.parquet          8.5 MB  (34,166 trades, 82 cols)
/home/abhis/btc-data/derived/m7/m7_trades_enriched.parquet 8.76 MB (34,166 trades, 91 cols)
/home/abhis/btc-data/derived/m7/m7_paths/                  121 partitions (friday_date=YYYY-MM-DD)
```

### Notes for Gemini
- I left your in-progress `m7_full_coverage` work UNTOUCHED in the working
  tree (`backend/app/api/m7_full_coverage.py`, `backend/tests/test_m7_full_coverage.py`,
  `frontend/src/components/m7/M7IvBandFullCoverageTable.tsx`, plus the
  `main.py` router registration). Specifically I temporarily reverted the
  `M7IvBandFullCoverageTable` import + mount from `M7SweepDashboard.tsx`
  during my commit so my Chunk 1 was self-contained, but those references
  should be re-added when your full_coverage work commits. Not a conflict —
  just something to put back.
- The plan's chunks 2–10 are the natural next pieces. Chunk 2 (Baseline +
  Theta/Vega) is the easiest from here because the enriched parquet already
  has `theta_per_vega_combined`, `excess_over_fair_pct`,
  `iv_regime_premium_pct`, `structural_credit_pct`.

### Pending follow-ups
- Re-add `M7IvBandFullCoverageTable` import + mount to `M7SweepDashboard.tsx`
  when Gemini's `m7_full_coverage` work is committed.
- Chunks 2–10 of the Trade Copilot plan.

---

## Previous Session (14)
**Who:** Claude
**Date:** 2026-05-06 (Session 14 — M7 backfill completed + enrichment + exit-hour UI + commit)
**Branch:** `mainbranch-gemini_claude`

### Session 14 highlights
- **M7 backfill COMPLETE**: 121/121 Fridays processed (Dec 2023 → Apr 2026).
  34,166 trades in `m7_trades.parquet`, 121 path partitions in `m7_paths/`.
  Runtime: ~5.3 hours (was still in progress at session start).
- **Enrichment run**: `scripts/backfill_m7_enriched.py` executed.
  29,966/34,166 trades matched a calibration bucket. Output:
  `m7_trades_enriched.parquet` (34,166 rows × 91 cols, 8.76 MB).
  API auto-prefers enriched parquet (existing code, no change needed).
- **Exit-hour UI** (`M7FilterBar.tsx`): Added "Exit hour" dropdown with
  13 Saturday IST options (Sat 05:00 → Sat 17:30). Uses `fixed_exit_hour_ist`
  field in `M7ExitRule` (both TS type and Python API updated in Session 13).
- **All 155 tests passing** (confirmed at session start).
- **Committed**: `9211594` — 65 files changed, 18,542 insertions.

### What's on disk
```
/home/abhis/btc-data/derived/m7/m7_trades.parquet          8.5 MB  (34,166 trades, 82 cols)
/home/abhis/btc-data/derived/m7/m7_trades_enriched.parquet 8.76 MB (34,166 trades, 91 cols)
/home/abhis/btc-data/derived/m7/m7_paths/                  121 partitions (friday_date=YYYY-MM-DD)
```

### Pending follow-ups
- No blockers. M7 pipeline is complete end-to-end.
- Optional future: add per-friday multiprocessing to cut re-run time
- Optional future: add UI rule sliders for premium-SL presets
- Optional future: M8 — what's the next analysis direction?

---

## Previous Session (13)
**Who:** Claude
**Date:** 2026-05-05 (Session 13 — M7 Friday→Saturday strangle/straddle sweep with rich 1m path + rule-based exit derivation)
**Branch:** `mainbranch-gemini_claude`

### Session 13 highlights
- **New M7 batch backtester** (`backend/app/analytics/m7_batch_backtester.py`,
  ~660 LOC). For every Friday × expiry × entry_hour (Fri 21:00 → Sat 03:00 IST,
  7 slots) × delta_target (8 values: 0.05–0.50), simulates a SHORT strangle/
  straddle held until Sat 17:30 IST. NO exit logic in the simulator — full 1m
  path is recorded so any exit rule (fixed-time, max_profit %, margin %,
  premium SL, or any future predicate) can be derived as a query against the
  saved path.

- **Strike-selection policy**:
  - delta_target = 0.50 → true straddle (single strike closest to spot, both legs)
  - delta_target < 0.50 → closest-from-below per leg (highest |delta| ≤ target)
  - No qualifying strike → trade skipped (logged, not faked)

- **Outputs** (`/home/abhis/btc-data/derived/m7/`):
  - `m7_trades.parquet` — entry-context only (one row per trade), ~80 cols
    incl. cost decomposition per leg, entry greeks, ATM IV, RV/IVP/M3 ctx
  - `m7_paths/friday_date=YYYY-MM-DD/part.parquet` — 1m path Hive-partitioned;
    35 cols per row (spot OHLCV+OI, leg marks/IV/OI, ATM IV, all greeks per leg,
    net greeks, theta/vega ratio, gross PnL, pct of credit, pct of margin)

- **New API** (`backend/app/api/m7_results.py`, ~430 LOC) under `/api/v1/m7/`:
  - `/summary`, `/trades`, `/path`, `/aggregate`, `/heatmap`, `/best_combo`,
    `/iv_band_summary`, `/cost_breakdown`, `/meta`
  - Exit rule passed as JSON query param: `exit_rule={"max_profit_pct":30,"premium_sl_pct":50}`
  - DuckDB walks the path parquet, finds first-trigger ts per trade, fetches the
    P&L at that ts, returns aggregated outcomes. Hard cap = Sat 17:30 IST.
  - Net P&L estimate = gross − 2× entry costs (round-trip approximation;
    /cost_breakdown returns exact entry-leg costs).

- **New frontend** under `frontend/src/components/m7/` and `pages/M7SweepDashboard.tsx`:
  - `M7FilterBar`, `M7HeadlineStrip`, `M7AggregateHeatmap`, `M7IvBandSummaryTable`,
    `M7BestComboTable`, `M7TradeLogTable`, `M7TradePathChart`
  - New "M7 Sweep" mode added to App.tsx (6th mode after M6 Results)

- **New script** `scripts/backfill_m7_enriched.py` — joins `m7_trades.parquet`
  with `calibration_v2.parquet` on `[dte_bucket, spot_bucket, delta_target_bucket,
  ivp_bucket]` to add `fair_credit_at_ivp`, `structural_credit_pct`,
  `iv_regime_premium_pct`, `excess_over_fair_pct`, `pattern_winrate`,
  `expectancy_per_credit_pct`, `n_trades_in_bucket`. Loader in `m7_results.py`
  prefers the enriched parquet when present.

- **Tests**: `backend/tests/test_m7_batch.py` (22 tests) +
  `backend/tests/test_m7_api.py` (9 tests) — all 31 passing.

- **Backfill running in background** (PID at /tmp/m7_backtest.pid, log at
  /tmp/m7_backtest.log). 121 Fridays Dec 2023 → Apr 2026 × ~7 expiries each
  × 7 entries × 8 deltas. Takes ~3 min/Friday → ETA ~5h. Trades-parquet
  written incrementally every 5 Fridays so dashboard works during backfill.

### Verified end-to-end (with partial data, 5 fridays):
- 988 trades, 590 wins (59%), avg net -$3.98
- With max_profit_pct=30 rule: 299/988 trades trigger early
- Cost decomposition matches `costs.py` to the cent
- Path endpoint returns 1230 1m rows per trade

### OI data coverage (verified 2026-05-05)
- **Spot OI / volume**: empty before 2024-01-26; ~92% populated through 2024;
  100% populated from 2025 onwards.
- **Option OI**: sparse pre-Feb 2024; progressively richer; by April 2024
  ~75% of CE/PE rows have OI > 0; near-100% for recent expiries.
- Net: trades from **March 2024 onwards have reliable OI data** in the M7
  path (~115 of 121 Fridays in the backfill). Pre-Feb 2024 trades show 0
  OI which reflects source-parquet reality, not a M7 bug.

### Pending follow-ups
- Wait for backfill to complete (~5h), then run `scripts/backfill_m7_enriched.py`
- Add per-friday parallelism (multiprocessing) to cut backfill time
- Add UI rule sliders for premium-SL preset (currently typed as numbers)

---

## Previous Session (12)
**Date:** 2026-05-04 (M6 Attribution: per-Friday best expiry + winners-vs-losers per contract + IV-premium decomposition + expanded summary strip + 80-90/90-100/100+ IV bands)

### Session 12 highlights
- **New `m4_trades_enriched.parquet`** (5,274 rows × 87 cols, 1.48 MB) —
  produced by `scripts/backfill_m4_enriched.py` (~150 LOC). Joins
  `m4_trades` with `calibration_v2` to add `fair_credit_at_ivp`,
  `structural_credit_pct`, `iv_regime_premium_pct`, `excess_over_fair_pct`
  per trade, and recomputes per-leg `theta`/`vega`/`gamma` via BS
  (`app.core.greeks.compute_greeks`) plus `theta_per_vega_{call,put,combined}`
  ratios. Loader in `m4_results.py` now prefers the enriched parquet,
  falls back to plain `m4_trades.parquet`. 4,548 / 5,274 trades matched
  a calibration bucket; 726 left null (their `dte_bucket` or `ivp_bucket`
  was 'nan').

- **3 new endpoints under `/api/v1/m4/`** (in `m4_results.py`):
  - `GET /winners_vs_losers?delta=` — per-contract avg(win) vs avg(loss)
    for **31 indicators** in 7 categories (IV / RV-VRP / Skew / Spot
    regime / GEX-Flow / Premium / Greeks). Flags |gap| > 0.5σ as
    "discriminating".
  - `GET /per_friday_best?delta=` — 121-row Friday view: winner /
    runner-up / loser contract + top 3 deciding indicators (ranked by
    |winner − loser| / σ).
  - `GET /win_frequency?delta=` — per-contract count of Fridays it was
    the best performer.

- **3 new frontend components in `frontend/src/components/m4/`**:
  - `M4WinFrequency.tsx` — bar chart + table of % Fridays each contract
    won
  - `M4WinnersVsLosers.tsx` — collapsible per-contract sections with 31
    indicators grouped by category; "Only discriminating" toggle
  - `M4PerFridayBest.tsx` — sortable 121-row table with deciding
    indicators per Friday; min-winner-net filter

- **Wired** as new "Attribution analysis" section in
  `M4ResultsDashboard.tsx` with shared Δ chip selector
  (`0.05/0.10/0.15/0.25/0.30/0.50`, default 0.30, persisted under
  `m6:attr_delta`).

- **Contract type summary strip extended** with 6 new columns:
  Avg Win, Avg Loss, Best Net, Worst Net, Best MFE, Worst MAE. Backend
  `/contract_type_summary` now returns `n_wins`, `n_losses`,
  `avg_net_win`, `avg_net_loss`, `best_net_pnl`, `worst_net_pnl`,
  `best_max_mtm`, `worst_min_mtm`.

- **IV bands split** in `_IV_BANDS` from `[…, 80, 100, 999]` to
  `[…, 80, 90, 100, 999]`. New labels: `80-90`, `90-100`, `100+`. The
  `100+` band exists but is **permanently empty** (max ATM IV in
  dataset = 98.65%).

- **Expiry-class filter cleaned up** — removed the search-text input
  from `M4ExpiryGridTable.tsx`; kept only the click-to-toggle chips.

### Notable findings exposed by Session 12
- **Tail risk**: bimonthly's worst single trade is **-$1,143** with
  -$1,121 MAE; avg loss per losing trade is **-$42.71** (3-4× any
  other contract). Workhorse contracts (current → biweekly) cap at
  -$103 thanks to the 100% per-leg SL.
- **Cleanest contract = next_to_next**: avg win +$22.25 vs avg loss
  -$21.49 (near-symmetric), 76% WR.
- **Win-frequency at Δ=0.30**: `current` wins outright on 28% of
  Fridays, `next_to_next` 27%, `next` 21%, `weekly` 12%. (Different
  from "highest avg P&L" — current's smaller avg makes it less
  attractive even though it wins more often.)
- **At Δ=0.30 the workhorse contracts have ZERO discriminating
  indicators at the 0.5σ threshold.** Translation: within a single Δ
  at one contract, entry conditions for winners look very similar to
  losers — the alpha is in *which contract you pick on which Friday*,
  not in pre-trade indicator filtering.
- 2025-03-07 anchor verified: at Δ=0.50, `current` wins at +$169 with
  the top deciding indicator being `theta_per_vega_put` (7.19σ
  separation vs the bimonthly loser).

---

## Prior session (kept for context)
**Date:** 2026-05-03 (Session 11 — LiveSignal page + M6 dashboard + expiry × IV × Δ grid + scroll fix + cleanup)
**Branch:** `mainbranch-gemini_claude`
**Status:** Platform now M1–M6 complete with 5 dashboard modes:
**Live | Historical | Backtest | Live Signal | M6 Results**.

**LiveSignal** scans every live expiry × 6 deltas in real time and
recommends the highest-quality (Δ, expiry) strangle using the calibrated_v2
quality formula. **M6 Results** visualizes the 5,274-trade M4 batch
backtest: DTE×Δ heatmap, pattern bars, credit×P&L scatter, quality
calibration curve, **plus a per-contract-type expiry × IV × Δ grid table**
showing MFE/MAE, gross/net P&L, slippage + brokerage, margin, and credit %
per cell. `/historical/calibration` surfaces v2 fields (`pattern_winrate`,
`overall_winrate`, `n_trades`, `expectancy_per_credit_pct`, etc.).

### What's new since prior handoff
- **Frontend**
  - `frontend/src/pages/{LiveSignalDashboard,M4ResultsDashboard}.tsx` —
    both now scrollable (`height: 100%; overflowY: auto`).
  - `frontend/src/components/m4/M4ExpiryGridTable.tsx` (NEW, ~330 LOC) —
    contract-type summary strip + 20-column sortable table:
    `Contract | IV % | Δ | n | WR | SL | Avg/Best MFE | Avg/Worst MAE |
    Avg Gross | Avg Net | Total Net | Slip RT | Slip ½ | Brk RT | Brk ½ |
    Cost RT | Credit % | Margin`. Mounted at the bottom of M4ResultsDashboard.
- **Backend**
  - `backend/app/api/m4_results.py` — added `/api/v1/m4/expiry_grid` and
    `/api/v1/m4/contract_type_summary`. Classifies each trade's expiry by
    Delta contract type (current/next/next_to_next/weekly/biweekly/
    three_week/monthly/bimonthly/quarterly) using `(entry_ts, expiry_date)`
    + last-Friday-of-month detector. IV bands keyed on the **specific
    expiry's own ATM IV** at entry (avg of CE+PE leg IVs from the Δ=0.50
    trade for that entry × expiry pair) — not the constant-maturity 7d.
  - `backend/app/api/m4_results.py` — `sl_rate` metric in `/aggregate`
    + `sl_hit_rate` in `/summary` updated to count `LegSL` (the actual
    parquet value) in addition to `SL`.

### Reusable analysis snippets (this session)
- `python3 /tmp/m4_per_expiry_iv_vs_delta.py` (re-runnable from work_log) —
  exports per-(entry × expiry) IV vs Δ table to
  `/home/abhis/btc-data/derived/m4_per_expiry_iv_vs_delta.{csv,xlsx}`.

### Headline M4 findings (5,274 trades, Friday 23:00 → Sat 10:00 IST)
| Contract | n | WR | Avg Net | Total Net |
|---|---|---|---|---|
| **next-to-next** (~2.8d) | 714 | **76.5%** | **+$11.96** | **+$8,537** |
| next (~1.8d) | 714 | 59.0% | +$9.23 | +$6,592 |
| current (~0.8d) | 726 | 48.8% | +$6.45 | +$4,682 |
| weekly (~7d) | 714 | 76.3% | +$5.64 | +$4,025 |
| biweekly (~14d) | 714 | 64.1% | +$2.49 | +$1,778 |
| monthly (~28d) | 486 | 50.2% | -$0.69 | -$336 |
| **bimonthly** (~52d) | 618 | **30.4%** | **-$26.26** | **-$16,230** |
| quarterly (~70d) | 36 | 25.0% | -$10.95 | -$394 |

**Skip everything ≥30 DTE.** Bimonthly alone bleeds –$16k and is dragging
the otherwise-+$25.8k book down to +$8.9k. Sweet spot = next-to-next +
weekly + Δ 0.30 in IV 50–70%.

### Known limitations carried over
- **OI capture** in live_recorder still NaN: instrumented (`mark_msgs` / `oi_msgs`
  counters added) and documented but not refactored. Delta's `candlestick_1m`
  channel only emits MARK bars; populating OI requires a parallel `v2/ticker`
  subscription that buckets `oi_contracts` updates into 1m bars. Needs a
  design pass; recorder is live and the change shouldn't be hacked in mid-stream.
- **Cost split in M4 trade rows.** `slippage_usd` / `brokerage_usd` in
  `m4_trades.parquet` are **round-trip totals** (entry + exit summed). The
  expiry-grid table shows a 50/50 estimate (`Slip ½`, `Brk ½`). True per-side
  capture requires re-running the M4 batch backtester with the trade_simulator's
  per-side fields written through (~6h on 4 workers). Not blocking — the per-job
  backtester (Backtest mode) already records true entry/exit splits.
- **IV-premium decomposition** (`fair_credit_at_ivp`, `structural_credit_pct`,
  `excess_over_fair_pct`) is computed live by `compute_trade_analytics` for the
  LiveSignal page, but is **not baked into m4_trades**. Could be added to the
  expiry-grid endpoint via a join to `calibration_v2.parquet`. Pending.
- `_simulate_day` → `simulate_trade_path` refactor still deferred (both paths
  working independently).

---

## What Was Done — 2026-05-03

### Pipeline backfills run
- **M2** options_enriched (resumable per-expiry checkpoint): 859 expiries, 4.6h total. Output: `options_enriched_{1m,5m,15m,30m}.parquet` (49–104 MB each).
- **M3** full_enriched: 30s. Output: `full_enriched_{1m,5m,15m,30m}.parquet` (65–367 MB each, 316 cols).
- **M5 v1 calibration**: 25 min. Output: `calibration_raw.parquet` (806k rows), `calibration.parquet` (600 buckets), `calibration_universal.parquet` (30 rows).
- **M4 batch backtester**: 6.4h. Output: `m4_trades.parquet` (5,274 trades, 1.1 MB), `m4_paths.parquet` (49,475 hourly snapshots, 3.3 MB). **Win rate 58.2%**, SL hit rate 17.8%, net P&L sum +$8,859.
- **M5 v2 enrichment**: 2.1s. Output: `calibration_v2.parquet` (600 buckets, 450 with M4 data, 38 cols including `pattern_winrate`, `z_winners_mean/std`, `overall_winrate`).

### Code shipped (5 new files, ~2,200 LOC)
- `backend/app/services/trade_simulator.py` — extracted `simulate_trade_path()` from `_simulate_day` so M4 reuses the bar-walk + per-leg SL + cost + margin logic. New: optional path snapshot recording at hourly cadence.
- `backend/app/analytics/m4_batch_backtester.py` — Friday 23:00 IST × all live expiries × 6 deltas (0.05/0.10/0.15/0.25/0.30/0.50) × 100 lots/leg. Exit Sat 10:00 IST or earlier on per-leg 100% loss SL. Records hourly path snapshots. Costs (slippage + brokerage) + margin (29-scenario portfolio stress) tracked per trade. Outputs `m4_trades.parquet` + `m4_paths.parquet`.
- `backend/app/analytics/backfill_attribution.py` — M5 v2: aggregates M4 outcomes per `(DTE × spot × Δ × IVP)` bucket. Computes `pattern_winrate` per pattern (JSON-encoded), `z_winners_mean/std` (winners-only credit_pct distribution), `expectancy_per_credit_pct`, `expectancy_per_margin_pct`, `sl_hit_rate`. Writes `calibration_v2.parquet` as left-join superset of v1.
- `backend/tests/test_trade_simulator.py` — 7 tests (synthetic data, monkey-patched data accessors). Covers SL trigger, snapshot cadence, cost application, MFE/MAE coherence, breaching-leg identification.
- `backend/tests/test_backfill_attribution.py` — 4 tests (synthetic m4_trades + v1 calibration → v2 parquet round-trip).

### Code modified
- `backend/app/analytics/enrich_options.py` — M2 per-expiry Stage A checkpoint (atomic .tmp + rename). Survives container restarts. `--clear-checkpoint` CLI flag. Allowed M2 backfill to recover from a SessionStart-hook-triggered kill at 53% without losing work.
- `backend/app/services/strangle_analytics.py` — auto-detect v2 calibration. `_load_calibration` prefers `calibration_v2.parquet` when present. `lookup_calibration` surfaces v2 columns (`z_winners_mean/std`, `pattern_winrate`, `overall_winrate`, `n_trades`) when available. `compute_trade_analytics` adds v2 quality formula path before falling back to v1, then to `fallback_ivp_credit`. `quality_source` field reflects which path was taken.
- `frontend/src/types/backtest.ts` — `BacktestTrade.quality_source` enum gains `'calibrated_v2'`.

### Verified end-to-end
- `_load_calibration()` reads from V2 (38 cols incl. v2 fields) ✅
- `lookup_calibration(dte=7, spot=100k, td=0.10, ivp=70)` returns `pattern_winrate={"C":1.0,"D":1.0,"Other":0.5}`, `overall_winrate=0.83`, `n_trades=6`, `z_winners_mean=0.023`, `z_winners_std=0.0065` ✅
- `compute_trade_analytics()` returns `quality_source: 'calibrated_v2'`, `quality_score: 40.77`, `size_band: 'skip'` for the synthesized test trade ✅
- Live recorder running: `recorder: WS connected — subscribing 488 symbols` and 507 parquet files written to `data_live/` within 35s of restart ✅
- Calibration endpoint `/api/v1/historical/calibration?dte=7&spot=100000&delta_target=0.10&ivp=70` returns rich bucket (n_samples=1033) ✅
- M3 snapshot endpoint `/api/v1/historical/snapshot-context?ts=...` returns 89 fields ✅

### Pipeline outputs on disk
```
/home/abhis/btc-data/derived/spot_enriched.parquet              151 MB  (M1)
/home/abhis/btc-data/derived/options_enriched_5m.parquet         49 MB  (M2)
/home/abhis/btc-data/derived/full_enriched_5m.parquet           232 MB  (M3, 316 cols)
/home/abhis/btc-data/derived/calibration.parquet                 82 KB  (M5 v1, 600 buckets)
/home/abhis/btc-data/derived/calibration_universal.parquet      6.5 KB  (M5 v1 fallback)
/home/abhis/btc-data/derived/calibration_raw.parquet                    (M5 v1 raw snapshots)
/home/abhis/btc-data/derived/m4_trades.parquet                  1.1 MB  (M4, 5,274 trades)
/home/abhis/btc-data/derived/m4_paths.parquet                   3.3 MB  (M4, 49,475 path snapshots)
/home/abhis/btc-data/derived/calibration_v2.parquet                     (M5 v2, 600 buckets, 450 with M4 data)
/home/abhis/btc-data/data_live/                                         (live recorder, growing)
```

### Quick stats from M4 trades (cross-trade winrate sanity)
| Δ      | n   | win_rate | sl_rate | avg_pnl |
|--------|-----|----------|---------|---------|
| 0.05   | 879 | 59.2%    | 17.6%   | -$0.58  |
| 0.10   | 879 | 60.4%    | 18.5%   | +$0.16  |
| 0.15   | 879 | 61.3%    | 19.2%   | +$1.24  |
| 0.25   | 879 | 60.1%    | 18.7%   | +$3.18  |
| 0.30   | 879 | 58.0%    | 18.2%   | +$3.54  |
| 0.50   | 879 | 50.3%    | 14.8%   | +$2.54  |

By DTE: 3-7d = 76% win rate (sweet spot); 30-60d = 36.5% (long-dated entries bad); 0-3d = 61% (high gamma); 7-14d = 64%; 14-30d = 54%.

### Commits today
- `58d67c2` — M2 per-expiry checkpoint to survive kills
- `847da38` — M4 + M5 v2 + analytics auto-detect (1,566 lines, 5 files)
- `bd05f94` — backfill_attribution unit tests (154 lines)
- `d9e3772` — frontend `calibrated_v2` enum addition

### Pending / next-session candidates
- **LiveSignal page (separate plan)**: hybrid backend (slow cols from M3 row + fast cols from `ticker_store`) + new `LiveSignalDashboard.tsx`. Reuses existing `StrangleAnalyticsPanel`. ~1000 LOC.
- **Refactor `_simulate_day` to call `simulate_trade_path()`**: deferred from M4 plan step 1. Needs equivalence test vs old code first. Low priority — both paths working independently.
- **Surface v2 cols in `/historical/calibration` endpoint response**: currently the endpoint returns v1-shape JSON; frontend doesn't yet see `pattern_winrate`/`z_winners_*`. The backend `compute_trade_analytics` uses v2 internally so trade rows are correct; only the standalone endpoint shape needs updating.
- **Run nightly merge sanity check**: `merge_live_to_main` is scheduled, first run will fire after 20h of recorder collecting. Inspect at next session start.

---

## Architecture (LiveSignal — design locked in, not yet built)
For LiveSignal, do NOT build incremental enrichment. Use a hybrid read:
- **Slow-moving cols** (IVP_90d, RV_7d/14d/30d, ADX_4h, pattern, vrp_pct_90d) read from latest M3 row in `full_enriched_5m.parquet`. These don't shift minute-to-minute — staleness of even hours is fine.
- **Fast-moving values** (spot, ATM IV @7/14/30d, skew RR/BF, GEX, current strangle leg marks) computed on-the-fly from `ticker_store` (already populated tick-by-tick by `delta_ws_client.py`). BS solver runs server-side.
- Merge them, run existing `strangle_analytics.compute_trade_analytics`, return JSON.
- Reuses existing `<StrangleAnalyticsPanel />` on a new `LiveSignalDashboard.tsx`. ~1000 LOC total. No incremental enrichment loop. No 5-min scheduler.

The recommendation panel scans ALL live expiries × 6 deltas, ranks by `quality_score` (now v2-calibrated thanks to today's M4+M5v2 work), and shows the best (Δ, expiry) combo with full analytics for each of the top N candidates.
