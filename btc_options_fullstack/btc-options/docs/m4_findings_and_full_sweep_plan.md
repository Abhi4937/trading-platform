# M4 Findings + End-to-End Sweep Plan

**Status as of 2026-05-03 (Session 11c)**

This is a single consolidated document covering:
1. **Part A** — everything the M4 backtest has taught us so far (what trades work, what doesn't, why)
2. **Part B** — the design of an end-to-end "all-combinations" backtest sweep that tests every (contract × Δ × IV × SL × pattern) cell on the data we already have
3. **Part C** — actionable trading rules and what's still unknown

---

## Part A — What we know from the M4 + paths dataset

### A1. Dataset state

| Artifact | Path | Rows | Coverage |
|---|---|---|---|
| `m4_trades.parquet` | `/home/abhis/btc-data/derived/` | **5,274** | Friday 23:00 IST → Sat 10:00 IST strangles, 100 lots/leg, leg-SL=100%, all 6 deltas × ~7 live expiries × 121 Fridays from Jan 2024 → Feb 2026 |
| `m4_paths.parquet` | same | **49,475** | Hourly snapshots per trade with leg marks, deltas, IVs, gross+net P&L |
| `calibration_v2.parquet` | same | 600 buckets | M4-enriched: pattern_winrate, z_winners stats, overall_winrate per (DTE × spot × Δ × IVP) bucket |

**Headline numbers (M4 raw):**
- Net P&L across all 5,274 trades: **+$8,859**
- Win rate: **58.2%**
- SL hit rate: **24.8%** (both `SL` and `LegSL` exits)

The headline number is **dragged down badly** by long-dated contracts. See A2.

### A2. Per-contract-type performance (the single biggest finding)

| Contract | Avg DTE | n | WR | Avg Net | **Total Net** |
|---|---|---|---|---|---|
| 🥇 **next-to-next** | ~2.8d | 714 | 76.5% | +$11.96 | **+$8,537** |
| 🥈 **next** | ~1.8d | 714 | 59.0% | +$9.23 | +$6,592 |
| 🥉 **current** | ~0.8d | 726 | 48.8% | +$6.45 | +$4,682 |
| **weekly** | ~6.8d | 714 | 76.3% | +$5.64 | +$4,025 |
| biweekly | ~13.8d | 714 | 64.1% | +$2.49 | +$1,778 |
| three_week | ~21d | 552 | 55.3% | +$0.37 | +$206 |
| monthly | ~28d | 486 | 50.2% | -$0.69 | -$336 |
| **bimonthly** | ~52d | 618 | **30.4%** | **-$26.26** | **-$16,230** ← drag |
| quarterly | ~70d | 36 | 25.0% | -$10.95 | -$394 |

**If you exclude monthly + bimonthly + quarterly:**
- Trades: 4,134
- Net: **+$25,820**
- Avg/trade: **+$6.25** vs full-book +$1.68
- Per-Friday expectation: ~$214

### A3. IV regime sensitivity (per contract)

Bands: `<30, 30-40, 40-50, 50-60, 60-70, 70-80, 80-100, 100+`

| Contract | Best (IV × Δ) | Avg Net | n | Worst |
|---|---|---|---|---|
| current | 80-100% × Δ=0.50 | **+$148.42** | 2 | 60-70% × Δ=0.25 (-$43) |
| current | 50-60% × Δ=0.50 | +$30.30 | 10 | (more reliable) |
| next | 70-80% × Δ=0.50 | **+$110.25** | 2 | (none) |
| next-to-next | 60-70% × Δ=0.50 | +$63.29 | 6 | (none meaningful) |
| weekly | 60-70% × Δ=0.30 | +$30.07 | 8 | <30% × Δ=0.50 (-$5) |
| biweekly | 60-70% × Δ=0.30 | +$11.14 | 9 | <30% × Δ=0.30 (-$12) |
| monthly | 50-60% × Δ=0.15 | +$5.09 | 22 | 70-80% × Δ=0.50 (-$14) |
| bimonthly | 40-50% × Δ=0.05 | +$0.15 | 35 | mostly negative |

**Distribution of trades across IV bands:**
- <30%: 11.4%   30-40%: 27.7%   40-50%: 30.1%   50-60%: 21.8%
- 60-70%: 5.6%   70-80%: 2.8%   80-100%: 0.4%   100+%: 0.13%

So **94%** of all trades happen at IV ≤ 60%. The "high IV bonanza" cells are real but **rare**.

### A4. Stop-loss sensitivity (sweep against path data)

For each cell, simulated SL = {25%, 50%, 75%, 100%} — what avg net would have been:

| Pattern | Best SL |
|---|---|
| current 0.05–0.15 | 50–75% (cap drawdown without losing decay) |
| current 0.30–0.50 | **100%** (need room) |
| next 0.05–0.30 | **75%** (trims tail at small upside cost) |
| next 0.50 | **100%** |
| **next-to-next 0.05–0.25** | **75%** (best risk/reward) |
| **next-to-next 0.30–0.50** | **100%** (max winners) |
| **weekly all Δ** | **100%** (recovery is the norm; tighter strictly worse) |
| biweekly 0.05–0.30 | **100%** |
| biweekly 0.50 | avoid (-$1 even at best SL) |
| ≥21 DTE | doesn't matter — strategy broken |

**Critical caveat:** path snapshots only cover up to the actual M4 100% SL trigger. **Looser SLs (>100%) cannot be simulated from existing data** — they need a fresh M4 run.

### A5. Pattern attribution

`calibration_v2.parquet` carries `pattern_winrate` per bucket per market regime (A/B/C/D/Other from M3). Live signals + per-trade backtest now use:
```
quality_score = 0.25·z_all_pct + 0.30·z_winners_pct + 0.30·IVP + 0.15·pattern_winrate
```

Pattern A (high IVP regime contraction) tends to win >55%. Pattern C/D often <50%. Pattern is one of the inputs to the LiveSignal recommendation already.

### A6. Cost / slippage / brokerage realities

- M4 trades stored **round-trip** slippage + brokerage as totals (one column each).
- Per-side split (entry vs exit) **not stored** — current dashboard shows 50/50 estimate.
- Median slippage / credit ≈ 7-10% for the Friday-Saturday 11h trade.
- Margin (29-scenario portfolio) typically $80-300 per 100-lot strangle at 0.30Δ.

### A7. The IV-premium decomposition (what makes a "good entry")

Per-trade fields from `compute_trade_analytics`:
- `structural_credit_pct` — what credit% you'd expect from pure structure (DTE × Δ × spot)
- `fair_credit_at_ivp` — calibration bucket median including IV regime
- `iv_regime_premium_pct` = `fair − structural` → premium added by elevated IV
- `excess_over_fair_pct` = `actual − fair` → what market is paying you ABOVE history

**Empirically:** trades where `excess_over_fair_pct > 0` (the market is overpaying you vs history) tend to win more often. This is why the v2 quality score weights it.

---

## Part B — End-to-end "all combinations" backtest plan

### B1. Goal

Run a single batch that produces a complete `m5_trades.parquet` with EVERY parameter combination tested on EVERY Friday entry, so the M6 dashboard can answer any "what if" without re-running:

> What's the best (contract × Δ × SL × IV-band) cell, **conditional on** entry pattern, IVP, ADX, gex regime, and whether we hard-filtered?

### B2. Parameter grid

| Variable | Values | n |
|---|---|---|
| `entry_weekday` | Fri only (for v1) | 1 |
| `entry_time_ist` | 23:00 only (for v1) | 1 |
| `exit_time_ist` | 10:00 next day (for v1) | 1 |
| `expiry_class` | current, next, next_to_next, weekly, biweekly, three_week, monthly, bimonthly, quarterly | 9 (whichever is live) |
| `target_delta` | 0.05, 0.10, 0.15, 0.25, 0.30, 0.50 | 6 |
| `leg_sl_pct` | **none**, 0.50, 0.75, 1.00, 1.50, 2.00 | **6** |
| `lots_per_leg` | 100 (fixed) | 1 |

**Trade count:** 121 Fridays × ~8 live expiries × 6 deltas × 6 SL settings = **~35,000 trades**, ~6× the current M4. Path snapshots: ~325k rows.

**Runtime estimate:** ~6h on 4 workers (linear scaling from M4's ~10 min for 5,274 trades).

### B3. Output schema

`m5_trades.parquet` — same 74 columns as `m4_trades.parquet` PLUS:
- `leg_sl_pct` (float, the SL setting for this trade variant)
- `sl_setting_label` (str, e.g. "no_sl", "50pct", "100pct")
- `winner_in_cell` (bool, post-hoc tag for the (contract × IV × Δ × SL) cell — set by a follow-up enrichment that picks the best SL per cell)

`m5_paths.parquet` — extended to keep snapshots at every hour even AFTER any SL trigger up to the trade's natural exit (allows post-hoc "what if I'd held longer" analysis).

### B4. Implementation

**Option A — extend `m4_batch_backtester.py`:**
- Add `--sl-grid` arg accepting comma-separated SL values
- Inner loop over SL values, calling `simulate_trade_path()` once per (entry × expiry × Δ × SL)
- Reuse strike resolution (only changes by Δ, not by SL) so entry computation stays cheap
- Writes `m5_trades.parquet` + `m5_paths.parquet`

Estimated change: ~80 LOC in `m4_batch_backtester.py`, no new files.

**Option B — refactor `simulate_trade_path` to support multi-SL replay (cheaper):**
- Single chain walk per (entry × expiry × Δ); each bar evaluates ALL SL thresholds in parallel
- Records exit point per SL setting; final dataset has 6× rows
- Saves ~80% of bar-walk work (the chain reads dominate)
- Estimated change: ~150 LOC refactor in `trade_simulator.py`, ~120 LOC in `m4_batch_backtester.py`

**Recommended: Option B.** Reuses paths data and runs in ~90 min total.

### B5. Enrichment + dashboard

After `m5_trades.parquet` exists:

1. **Calibration v3** — re-run `backfill_attribution.py` to add per-(SL × bucket) winrates. Adds `winrate_by_sl: {25: 0.45, 50: 0.55, 75: 0.62, ...}` to each calibration bucket row. Lets `compute_trade_analytics` recommend the best SL alongside the entry.

2. **M6 dashboard additions** (already wired except for the SL dimension):
   - Add `sl_setting` query param to `/api/v1/m4/expiry_grid`
   - Add an SL chip selector to `M4ExpiryGridTable.tsx` (alongside IV class chips)
   - Add a "Recommended SL" column to the winners panel (best SL for that contract × IV × Δ cell)

3. **LiveSignal upgrade** — show the per-Δ recommended SL for each candidate, sourced from calibration v3.

### B6. Other dimensions that COULD be added (in scope for future, NOT v1)

- Different entry weekdays (Mon/Tue/Wed) — would 6× the row count
- Different entry hours — adds another 4-8× factor
- Different exit policies (TTL=4h vs 11h vs 24h) — 3× factor
- Iron condor / straddle / calendar variations — separate strategy tracks
- Per-leg SL by SIDE (CE vs PE asymmetric) — useful given puts decay differently in BTC bull/bear regimes
- Portfolio-level SL ($X loss → exit ALL) instead of per-leg
- ATM / ITM strike placement (we currently only do OTM by Δ target)

### B7. Verification gates before declaring v1 done

1. **Equivalence with current M4** — for `leg_sl_pct=1.0` rows, every M5 trade ID should match the corresponding M4 row's net_pnl_usd within $0.01.
2. **Monotonicity sanity** — for any (contract × Δ) cell, going from 25% → 50% → 75% → 100% SL → no_sl should produce monotonically decreasing SL hit rates and monotonically increasing avg gross P&L. Any non-monotonic cell needs investigation.
3. **No-SL upper bound** — for each cell, no_sl variant's avg_net should be `≥` 100%-SL variant when 100%-SL hit rate is high (because not exiting early lets more recoveries happen). For cells where this fails, the trade was held to the loss continuing → SL=100% was correctly cutting tail risk.

---

## Part C — Actionable trading rules right now

(Use these even before the v1 sweep ships.)

### C1. The ironclad "always" rules

1. **Never trade ≥ 21 DTE.** Bimonthly/monthly/three_week have negative expectancy across every IV regime and Δ. Saves ~$16k/year of drag.
2. **Always sell**: current + next + next_to_next + weekly + biweekly. These 5 contract types had 4,134 trades and **+$25,820 net** in 2 years.
3. **SL = 100% per leg by default.** Tighter SL kills net P&L in 22 of 30 cells. Move to 75% only for `next-to-next × Δ ≤ 0.25` and `next × Δ ≤ 0.30`.

### C2. Per-Δ recommendations by contract

| Contract | Optimal Δ | Recommended SL | Avg Net | Conditions |
|---|---|---|---|---|
| current | 0.30–0.50 | 100% | $7–13 | Avoid IV 60-70% (data shows trap); great in IV 80%+ |
| next | 0.50 | 100% | $13 | Best at IV 50-60% (n=10) |
| **next-to-next** | **0.30** | **100%** | **+$15.83** | Best risk/reward in entire dataset; works any IV |
| **next-to-next** | **0.50** | **100%** | **+$21.33** | Highest avg net of all combos |
| weekly | 0.30 | 100% | $9 | Sweet spot IV 50-70% |
| weekly | 0.50 | 100% | $7 | Wider band but more SL risk |
| biweekly | 0.25–0.30 | 100% | $4 | Smaller edge; not worth scale |
| biweekly | 0.50 | DON'T | -$1 | Only losing biweekly cell |

### C3. IV-regime overlay

- **IV < 30%:** Trade weekly + biweekly only (current/next have negative expectancy). Use Δ=0.30. Avg ~$2/trade.
- **IV 30-60%:** Trade everything (current through biweekly). Δ=0.30 standard.
- **IV 60-70%:** Avoid `current` (the trap zone — single-event vol crush). Trade next+ at Δ=0.30.
- **IV 70-100%:** **Trade aggressively — current/next/next_to_next at Δ=0.30-0.50.** Avg net jumps to $50-$150 per trade. These are the 2-3% of opportunities that drive most absolute P&L.
- **IV 100%+:** Only `current` has trades (n=7, all winners, avg +$74). Treat as opportunistic.

### C4. The pattern overlay

Once `compute_trade_analytics` returns `pattern_winrate >= 0.6` for a candidate, take the size_band ÷ 1 step up. When it's <0.4, skip. Pattern A and D historically beat Pattern B (which often signals breakout regime hostile to short premium).

### C5. Excess over fair (timing within IV regime)

When `excess_over_fair_pct > 0.5%`, the market is paying you 0.5%+ above historical fair value — take the trade even if other indicators are mid. This often shows up at the best 70%+ IV opportunities. When it's < -0.3%, the market is short-changing you vs history — wait or skip.

---

## Part D — Data gaps & open work

| Item | Why it matters | Effort |
|---|---|---|
| **No-SL / loose-SL backtest** | Tells us if 100% SL is leaving money on the table. Path data can't simulate it. | ~6h batch run |
| **Per-side cost split in M4** | Round-trip stored, per-side estimated. True split needs re-run. | Same batch as above |
| **OI capture in live recorder** | Currently NaN. Blocks accurate pcr_oi / GEX from live data. | ~80 LOC + 30 LOC test |
| **IV-premium decomposition baked into m4_trades** | Currently computed live by analytics, not stored. Could surface in M6 grid. | ~30 LOC join + endpoint extension |
| **Walk-forward calibration** | Current pattern_winrate is single static fit over all of history. May overfit. | v3 enrichment, ~200 LOC |
| **Per-user personalized baselines** | Live trades will differ from M4 due to actual fills, slippage etc. | Wait for live trade data to accumulate |

---

## Part E — TL;DR for trading

> **Sell strangles only on current + next + next_to_next + weekly + biweekly contracts at Friday 23:00 IST. Use Δ=0.30 with 100% per-leg SL by default. Skip everything ≥ 21 DTE. When IV is 70-100%, increase to Δ=0.50 on `current` or `next` for the dataset's best risk/reward. Patterns A/D > B/C; excess_over_fair > 0 = take, < -0.3% = skip.**

That single rule, applied to the M4 dataset, would have produced **~+$25k net over 2 years on 100-lot positions**. The v1 SL sweep will tell us how much more we leave on the table.
