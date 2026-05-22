# Stage-1 MTM Partial Exit — Future Work

Tracking gaps between the originally-planned Stage-1 dashboard scope
(`C:\Users\Abhis\.claude\plans\stage-1-partial-exit-sharded-corbato.md` +
the consolidated dashboard-integration plan) and what was actually
shipped. None of these items block the current Stage-1 panel from
being usable; they are enhancements deferred for a later session.

Last updated: 2026-05-22.

---

## Status snapshot — what ships TODAY

- Backend POST `/api/v1/m7/stage1_analysis` with L1+L2 SHA1 cache,
  warming thread, polling.
- Helper endpoints: `/precheck`, `/band_trades`, `/all_trades`.
- Per-cell metrics: case counts (A / B_reliable / B_unreliable /
  C_deeper / C_recovered), `avg_saved`, `avg_given_up`,
  `avg_save_per_c_deeper`, `avg_hurt_per_c_recovered`,
  `pct_B_unreliable`, `c_recovered_share`, EV/trade, Δ avg_pnl,
  Δ win_rate, Δ CVaR-95, Δ max_loss, Δ max_consec_losses,
  `avg_hyp_pnl`, `win_rate_hyp`, `max_loss_hyp`, `cvar_95_net_hyp`,
  `max_consec_losses_hyp`, composite_score_v2 (band-normalised).
- Verdicts: WORTH_IT / MARGINAL / SKIP_TIGHTER_SL_WINS / SKIP_NEGATIVE /
  SKIP_INSUFFICIENT (denormalised on every row).
- Best-cell selection: best-by-avg-pnl + best-by-composite + cross-rank
  flags + cells_agree.
- Frontend Stage-1 panel (below `M7PivotProfilePanel`):
  - Verdict summary strip + per-band cards.
  - 4×5 heatmap of delta_avg_pnl (lot-scaled).
  - Click cell → expanded detail with 3 blocks:
    1. Comparison table (Baseline → Stage-1 → Δ with up/down arrows)
       for avg net P&L, total net P&L, win rate, largest loss, max
       consec losses, n wins / n losses.
    2. Baseline-only fields (composite, hit %, SL hits, full MTM
       stats, credit, margin, lots, returns, exit times).
    3. Stage-1 cell metrics (compact: Setup / Trade counts / Magnitudes
       / P&L impact).
    4. "What each metric means" — full definitions for every field.
  - Per-band trades modal + cross-band "View all trades" modal.
  - Partial @ / Rest @ columns showing the stage-1 formula
    decomposed.
  - 25% SL / 50% SL / 75% SL / SL-avg / L-avg / W-avg / Custom
    triggers in modals.
  - Lot scaling applied to every dollar display (toggle visible via
    `×N lots` label).
- 5 CLI CSVs: `all_cells`, `heatmap_ev`, `heatmap_composite`,
  `best_cells`, `distribution`.

---

## ⚠️ Future work — additions that can be made without new infrastructure

Ranked roughly by user-value-per-hour-of-effort.

### F1. Per-band verdict summary table at top of panel
**Effort:** ~2h.
**What:** One row per band, columns = verdict | n_trades | best cell
(exit_frac, trigger_level) | Δ avg_pnl/trade | Δ win rate. Color-coded
chip per verdict. Sortable. Sits above the existing band-card grid so
you can see at-a-glance which bands stage-1 helps without scrolling.
**Why deferred:** the per-band cards already convey the same data; the
summary view is a usability nicety, not a missing capability.

### F2. Exact `n_winners` / `n_losers` from backend (no rounding)
**Effort:** ~30min backend + 15min UI.
**What:** Currently the comparison table derives stage-1 wins as
`round(win_rate_hyp × n_total)`. If `delta_win_rate` rounds to 0 you
see `26 / 2 → 26 / 2` even when a single trade actually flipped. The
backend already has the trade-level `hypothetical_is_win` flag; just
needs to be aggregated to exact counts per cell and returned.
**Why deferred:** the win-rate column already shows the change at the
% level; exact counts are a polish item.

### F3. Sortino / Calmar / Sharpe (actual + hyp + Δ) in comparison
**Effort:** ~1h backend + 30min UI.
**What:** Backend has `_attach_risk_adjusted` from `m7_best_combo.py`;
currently called on actual series only. Run it on the hypothetical
net_pnl series too, expose `sortino_hyp`, `calmar_hyp`, `sharpe_hyp`
plus deltas. Adds 3 more rows to the comparison table.
**Why deferred:** composite_v2 already aggregates Sortino + Calmar +
CVaR; the individual components are useful for analysis but the
composite captures the headline story.

### F4. `stdev_net_pnl` / `stdev_losses_only` actual + hyp + Δ
**Effort:** ~30min.
**What:** Pure aggregation on hypothetical net_pnl. Useful for showing
that stage-1 typically REDUCES dispersion of returns (the whole point
of risk-managing the tail).
**Why deferred:** CVaR-95 already covers tail risk; stdev is a fuller
picture but lower-priority.

### F5. `avg_pct_return_on_credit_hyp` and `avg_pct_return_on_margin_hyp`
**Effort:** ~30min.
**What:** Same as actual, but using hypothetical net_pnl as the
numerator. The credit / margin denominators are entry-time values and
unchanged under stage-1.
**Why deferred:** Δ avg_net_pnl in absolute dollars already shows the
direction; % return is just a unit conversion. Useful for cross-band
comparison since absolute dollar amounts scale with lot count.

### F6. Flat sortable detail table of all 20 cells per band (Plan section D4)
**Effort:** ~2h.
**What:** Currently the band card shows the 4×5 heatmap and you have
to click each cell individually to see its detail. A flat table with
all 20 rows × ~30 columns (sortable by any) would let you compare
across cells without clicking through them one at a time.
**Why deferred:** the heatmap covers the headline story (delta_avg_pnl
direction across all 20 cells); the flat table is for deep analysis.

### F7. Warnings panel per band card (Plan section D5)
**Effort:** ~1h.
**What:** When a band's best cell has `recovery_rate > 30%` OR win
rate drops > 5pp OR `pct_losers_sl_hit > 50%`, render a yellow/red
warning strip on the band card explaining the trade-off. Backend
already computes the data; just needs UI presentation.
**Why deferred:** the `C_recovered chip` already shows the most common
warning; full warnings panel is a polish item.

### F8. `stage1_2d__by_iv_band__per_trade.csv` (Plan section 1.7)
**Effort:** ~1h.
**What:** A flat per-trade CSV with hypothetical_net_pnl and case_tag
for every (trade × cell) combination. Currently the API endpoint
`/band_trades` and `/all_trades` returns this data, but the CLI script
doesn't write it as a CSV. Useful for offline analysis in Excel /
DuckDB without going through the API.
**Why deferred:** the API already serves the same data interactively.

### F9. LRU cache eviction + `meta.json` per cache entry (Plan section 2.5)
**Effort:** ~1h.
**What:** Currently `stage1_cache/` grows unbounded on disk. Add LRU
eviction (keep last 50 entries) and write a `meta.json` next to each
parquet with `created_at`, `filter_state_summary`, `per_band_picks`,
`script_version`. Allows manual invalidation by deleting old entries.
**Why deferred:** disk usage is currently small; will become an issue
only after extended use.

### F10. Admin endpoint `DELETE /api/v1/m7/stage1_analysis/cache`
**Effort:** ~30min.
**What:** Lets the dev clear all cached results without touching the
filesystem. Useful during code changes when `_STAGE1_RESPONSE_VERSION`
isn't bumped.
**Why deferred:** version bump already invalidates cached entries
correctly; admin clear is a dev convenience.

### F11. `avg_rel_time_peak` / `avg_rel_time_trough` aggregation per cell
**Effort:** ~30min.
**What:** Per-cell aggregate of `rel_time_max_mtm` and
`rel_time_min_mtm` (actual-only — hypothetical is not derivable from
aggregates). Surfaces "trough timing" patterns that explain why stage-1
fires when it does.
**Why deferred:** the band-level summary `pct_B_unreliable` already
flags trades where peak-before-trough timing is problematic.

### F12. `avg_pct_drop_peak_to_trough` / `avg_pct_recovery_trough_to_peak` (actual only)
**Effort:** ~1h.
**What:** Per-cell aggregates that quantify how deep the average dip
goes and how much it recovers. Useful for choosing trigger levels.
Computable from `min_mtm`, `max_mtm`, `entry_credit` per trade.
**Why deferred:** the band-level summary `SL_avg / L_avg / W_avg`
already establishes the reference dips.

### F13. Tab navigation state persistence (last viewed band)
**Effort:** ~30min.
**What:** Remember which band card was expanded last across page
reloads. Uses `usePersistedState` like other m7 panels.
**Why deferred:** the band cards aren't currently expandable (they
always show the heatmap); only cell details are. Cell-expansion state
is already per-band in component state.

---

## ❌ Cannot be added without new infrastructure (Phase 10 — out of current scope)

These require capabilities the current architecture lacks. Documenting
here so they're not re-proposed without first building the
infrastructure.

### X1. Hypothetical MTM trajectory aggregates
**Blocked by:** absence of per-minute leg-price path walking.
**What's blocked:**
- `avg_max_mtm_winners_hyp`, `avg_min_mtm_winners_hyp`,
  `worst_min_mtm_winners_hyp`, `best_max_mtm_winners_hyp` and the
  loser-side analogues.
- `avg_pct_drop_peak_to_trough_hyp` / `avg_pct_recovery_trough_to_peak_hyp`.

**Why blocked:** Under stage-1, the surviving (1 - exit_frac) portion
of the position has its OWN MTM trajectory after the trigger fires.
That trajectory is NOT the same as the original trade's trajectory —
it depends on the leg prices at every subsequent bar until the
original rule's exit. To aggregate honest per-trade hyp MTM stats, you
need the actual minute-by-minute leg price path. The current cache only
stores aggregate min_mtm / max_mtm / net_pnl per trade, not the path.

**The single-point approximation we DO use:**
For the per-trade hypothetical NET PNL only (not the MTM trajectory):
```
hyp_net_pnl = exit_frac × trigger_MTM + (1 - exit_frac) × actual_net_pnl
```
This treats the surviving portion as if it exits at the same final
price as the original trade — which is true ONLY if the rule's exit
condition wouldn't have re-triggered differently on a (1 - exit_frac)
sized position. For symmetric short strangles that's usually fine
because the surviving position has the same Greeks and faces the same
exit triggers (premium SL, fixed-time exit, etc.) at the same time.
But it doesn't give us a per-bar MTM trajectory.

**To enable this:** would need a "lite simulator" that walks the option
minute parquet (~40-50GB on disk) for each trade, applies the rule and
stage-1 logic minute-by-minute, and records the resulting MTM
trajectory. Estimated 2-3 days of work plus a new cache layer.

### X2. Delta-trigger stage-1 (fires on absolute leg delta crossing)
**Blocked by:** same minute-by-minute leg path infrastructure as X1.
**What:** Trigger fires when `max(|call_delta|, |put_delta|) ≥ T` instead
of MTM-based. Captures gamma-driven blowups before MTM catches up.
**Why blocked:** per-minute leg delta isn't aggregated; would need
path walking to compute when the threshold is first crossed per trade.

### X3. Stage-2 / re-entry rules
**Blocked by:** absence of position-state machine in the simulator.
**What:** After stage-1 partial exit, re-add lots if MTM recovers
above a re-entry threshold.
**Why blocked:** the current simulator computes outcomes in a single
forward pass; supporting re-entry needs a state machine that tracks
position size over time and re-prices at re-entry.

### X4. Asymmetric leg exits (close one leg at a time)
**Blocked by:** margin model invariants assume symmetric short
strangles.
**What:** Close only the ITM leg when its short delta crosses a
threshold.
**Why blocked:** margin calculation, slippage model, and Greek
calculations all assume the position is a strangle. Breaking that
invariant cascades through `scripts/margin_engine.py`,
`frontend/src/utils/marginEngine.ts`, the slippage model, and the
exit-cache schema.

### X5. Rolling-window trigger calibration
**Blocked by:** the script currently uses full-sample reference levels
(SL_avg / L_avg / W_avg computed across all historical trades).
**What:** Compute reference levels on a rolling window (e.g. last 26
weeks) so the trigger adapts to recent regime changes.
**Why blocked:** would need to re-derive reference levels per trade
based on the trades that preceded it, then re-aggregate. Doable but
expensive in compute and not requested by the current analysis goal.

### X6. Live trading integration / paper-trade simulation
**Out of scope by design.** This is a backtest-only analysis tool.
Trading integration is a separate workstream that involves Delta
Exchange API, order routing, position monitoring, and live margin
calls — none of which the current Stage-1 panel touches.

---

## Reference

- Consolidated dashboard-integration plan (the prompt that defined
  the full scope): see the user's message in the 2026-05-22 session
  transcript.
- Original Stage-1 sweep plan (Phase 1-8 + Phase 9 dashboard + Phase
  10 future work): `C:\Users\Abhis\.claude\plans\stage-1-partial-exit-sharded-corbato.md`.
- Script: `backend/app/scripts/stage1_partial_exit_per_band_2d.py`.
- Backend endpoint: `backend/app/api/m7_stage1_analysis.py`.
- Frontend panel: `frontend/src/components/m7/M7Stage1Panel.tsx`.
- API client: `frontend/src/services/m7_api.ts` (`fetchM7Stage1*`).
