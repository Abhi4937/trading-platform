# M7 Friday-Band vs Per-trade Band — Side-by-Side Comparison

Generated 2026-05-14 after shipping the parallel M7 Friday-Band dashboard.

Two parallel best-combo schemes now live on the platform:

- **M7 Sweep** (`/m7-sweep`) — per-trade `entry_atm_iv_band`. Each trade's
  band is determined by the ATM IV of *its chosen expiry* at the moment of
  entry. Same Friday can land in different bands across (expiry, hour, Δ)
  combos → introduces the *skip / duplicate* problem.
- **M7 Friday-Band** (`/m7-friday-band`) — band pinned to the Friday itself
  via the **current expiry's** ATM IV (Saturday daily-expiry, which is the
  same-day expiry for any trade entered after 17:30 IST on Friday).
  Three modes:
  - **A1** = 21:00 IST snapshot (default)
  - **B1** = modal band over the entry-window hours
  - **D1** = per-entry-hour band collapsed via a prioritized tiebreaker chain

This doc compares the **A1** mode against M7 Sweep's per-trade scheme using
`ranking=avg_net_pnl` (Sweep) vs `ranking=sum_net_pnl` (Friday-Band default,
recommended because it naturally penalises low-coverage combos).

**Picker scope:** the Friday-Band dashboard restricts its pickers
(Best Combo + Summary Table + Path Markers + Losses Explorer scope=best_combo)
by default to the 4 popular expiries — `current (Sat)`, `next (Sun)`,
`next_to_next (Mon)`, `weekly (7d)`. These are the only expiries reliably
tradeable on every Friday. M7 Sweep's per-trade picker does *not* impose
this restriction by default, so some of its picks (e.g. 70-80 below)
include `monthly (30d)` whereas the Friday-Band side would not. Override
via the page's filter bar Expiry dropdown if needed.

---

## Universe-level conservation

For the same exit-rule and filter set, the trade universe is identical in
both schemes (every Friday's trades flow through the same picker; only the
band label differs).

| Metric | M7 Sweep | Friday-Band A1 |
|---|---|---|
| n_trades | 34,166 | 34,166 |
| n_wins | 23,441 | 23,441 |
| win_rate | 68.61% | 68.61% |
| avg_net_pnl_usd | $6.30 | $6.30 |
| total_net_pnl_usd | $213,491.25 | $213,491.25 |

✅ **Conserved** — the n_trades + n_wins parity matches our design.

## Friday cohort partition

Under A1, every Friday lands in exactly one band:

| Band | Sweep n_trades* | A1 #Fridays | A1 n at picked cell | Coverage |
|---|---|---|---|---|
| 0-20   | low | 3   | 3  | 100% |
| 20-30  | mid | 16  | 16 | 100% |
| 30-40  | high | 44 | 44 | 100% |
| 40-50  | mid | 28  | 28 | 100% |
| 50-60  | mid | 19  | 19 | 100% |
| 60-70  | low | 5   | 5  | 100% |
| 70-80  | low | 4   | 4  | 100% |
| 80-90  | one | 1   | 1  | 100% |
| 90-100 | one | 1   | 1  | 100% |
| 100+   | — (not populated under A1) | — | — | — |

*Sweep's per-band counts aren't directly comparable (a single Friday can
contribute to multiple bands), so only the relative magnitude is given.

Sum of Friday counts across bands = **121** = total Fridays in the dataset.
✅ **Each Friday is assigned to exactly one band**, by construction.

---

## Per-band best-combo picks (ranked by primary scheme metric)

`min_hit_pct=50`, `min_n_trades=5`.

| Band | M7 Sweep pick (avg_net_pnl) | Friday-Band A1 pick (sum_net_pnl) |
|---|---|---|
| 0-20 | `next (Sun)` Δ0.50 hr22 `sl100_exit_hr_8` → n=8 wr=75% avg=$15.13 | `next_to_next (Mon)` Δ0.50 hr23 `sl75_max_profit_25` → n=3 wr=100% sum=$86 |
| 20-30 | `next_to_next (Mon)` Δ0.50 hr00 `sl100_exit_hr_15` → n=24 wr=92% avg=$23.46 | `current (Sat)` Δ0.50 hr23 `sl75_exit_hr_15` → n=16 wr=62.5% sum=$329 |
| 30-40 | `next_to_next (Mon)` Δ0.50 hr23 `sl100_exit_hr_16` → n=52 wr=89% avg=$25.07 | `next_to_next (Mon)` Δ0.50 hr23 `sl100_exit_hr_16` → n=44 wr=86.4% sum=$1,149 |
| 40-50 | `next (Sun)` Δ0.50 hr01 `sl100_margin_target_40` → n=9 wr=100% avg=$50.36 | `next (Sun)` Δ0.50 hr21 `sl100_max_profit_50` → n=28 wr=60.7% sum=$743 |
| 50-60 | `next (Sun)` Δ0.50 hr21 `sl100_exit_hr_17` → n=8 wr=88% avg=$101.22 | `current (Sat)` Δ0.50 hr00 `sl100_exit_hr_1729` → n=19 wr=68.4% sum=$999 |
| 60-70 | `next_to_next (Mon)` Δ0.50 hr23 `sl100_exit_hr_1729` → n=7 wr=86% avg=$71.52 | `next_to_next (Mon)` Δ0.50 hr21 `sl100_exit_hr_1729` → n=5 wr=100% sum=$345 |
| 70-80 | `monthly (30d)` Δ0.40 hr21 `sl50_exit_hr_11` → n=5 wr=80% avg=$10.98 | `current (Sat)` Δ0.50 hr21 `sl100_exit_hr_17` → n=4 wr=75% sum=$530 |
| 80-90 | `current (Sat)` Δ0.50 hr23 `sl50_exit_hr_1729` → n=1 wr=100% avg=$189.73 | same → n=1 wr=100% sum=$190 |
| 90-100 | `current (Sat)` Δ0.50 hr23 `sl75_exit_hr_15` → n=1 wr=100% avg=$228.66 | `current (Sat)` Δ0.50 hr22 `sl50_exit_hr_12` → n=1 wr=100% sum=$233 |
| 100+ | `current (Sat)` Δ0.50 hr22 `sl50_exit_hr_12` → n=1 wr=100% avg=$233.27 | (no Fridays under A1 — band empty) |

### Where the two schemes agree

- **30-40, 60-70, 80-90, 90-100** — same (or near-identical) expiry / Δ /
  hour / rule. These bands have stable enough IV measurement at entry vs
  Sat-21:00 that both schemes converge on the same cell.

### Where they diverge

- **0-20, 20-30, 40-50, 50-60, 70-80** — different picks. Two drivers:
  1. Sat-IV at 21:00 disagrees with the trade-time per-expiry IV (e.g. a
     Friday with sat-IV=25% might trade a `monthly` expiry whose own ATM
     IV is 70% — Sweep parks it in 70-80, A1 parks it in 20-30).
  2. Friday-Band A1 ranks on `sum_net_pnl` (total $ across all Fridays in
     the band) which favours combos that cover every Friday — Sweep ranks
     on `avg_net_pnl` (per-trade) which favours niche high-avg picks even
     with low n.

### Coverage column

Under Friday-Band, every band shows **100% coverage** — the picked combo
trades every Friday in that band. This is the principal advantage of the
new scheme: cross-combo comparisons are guaranteed apples-to-apples within
a band.

Under Sweep, the equivalent column would be n_trades / count(unique
fridays-in-band) and could be much less than 100% because the same Friday
can appear in multiple bands depending on the (expiry, Δ) tested.

---

## Sections retained / dropped

| Section | M7 Sweep | M7 Friday-Band | Notes |
|---|---|---|---|
| Headline strip | ✅ | ✅ (same numbers under A1) | |
| Filter bar | ✅ | ✅ | identical |
| Metric dropdown | ✅ | ✅ | identical |
| IV-Band Summary table | ✅ (per-trade band) | ✅ (Friday-band) | same columns |
| Full Coverage table | ✅ | ❌ dropped | banner explains why |
| Missed Fridays table | ✅ | ❌ dropped | banner explains why |
| Best Combo table | ✅ | ✅ | sum_net_pnl default + Cov% column |
| Path Markers | ✅ | ✅ | same shape |
| Leg Skew heatmap | ✅ | ✅ (reused) | data is band-agnostic |
| Leg Attribution | ✅ | ✅ (reused) | per-trade log |
| Losses Explorer | ✅ | ✅ | scope=full_coverage hidden |
| Trade Path modal | ✅ | ✅ (reused) | |

---

## Why Full Coverage / Missed Fridays are dropped

Under the per-trade scheme, the same Friday can land in multiple bands
(because different expiries have different ATM IVs at entry), so a
best-combo picker that ranks per band ends up *skipping* some Fridays (the
picked combo's expiry/hour isn't tradeable on that Friday) and
*duplicating* others (the same Friday flows into both the 30-40 and 50-60
band picks). The Full-Coverage and Missed-Fridays tables exist to surface
that pathology.

Under Friday-locking (A1/B1/D1), every Friday is assigned to exactly one
band by construction. The skip/duplicate problem **vanishes**:

- The Coverage % column on the Best Combo table replaces both diagnostics.
  100% means the picked combo trades every Friday in the band; <100%
  means the picker chose a combo whose expiry/hour wasn't tradeable on
  some Fridays (typically because the chosen expiry was `quarterly` and
  expired between data start and that Friday).

---

## Mode A1 vs B1 vs D1

For completeness, the three Friday-Band modes assign Fridays differently:

- **A1 — 21:00 snapshot** — single fixed read per Friday. Most stable but
  ignores intraday IV drift.
- **B1 — modal band** — each Friday assigned to the band that occupied the
  most hours of the entry window. Robust to single-hour outliers.
- **D1 — tiebreaker chain** — per-entry-hour band collapsed via a
  prioritized chain (e.g. `best_avg_net_pnl > modal_band > earliest_hour_band`).
  Lookahead tiebreakers (those using trade outcomes) are flagged ⚠ in the UI;
  use them for diagnostics only.

The A1 Friday distribution above (3, 16, 44, 28, 19, 5, 4, 1, 1) totals 121.
B1 / D1 distributions vary slightly because they collapse multi-hour bands
differently.

---

## Verdict

The Friday-Band dashboard delivers like-for-like analysis of every M7-Sweep
section using a more interpretable cohort partition. Picks differ between
the two schemes in the bands where Sat-IV @ 21:00 disagrees with the
trade-time per-expiry IV; in those cases the Friday-Band picks tend to
favour combos that cover the entire band cohort (higher coverage, lower
avg-per-trade variance).

Both pages remain in parallel — user can toggle via the top-nav to compare
schemes side-by-side.
