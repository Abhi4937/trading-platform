# M7 Friday Classification — Strict, Force-Fit, and the 32 Missed Fridays

**Date written:** 2026-05-08
**Source:** End-to-end conversation analyzing the v3 best-combo grid (208,032 cells across 96 rules × 10 IV bands × 7 expiries × 8 deltas × 7 entry hours).
**Code references:** `backend/app/api/m7_full_coverage.py`, `backend/app/api/m7_best_combo.py`, `backend/app/api/m7_results.py`.

---

## TL;DR

For each Friday in the M7 dataset (121 total), the M7 Full-Coverage logic
classifies it into one of four buckets relative to the 10 picked best-combo
cells (one cell per IV band):

1. **`rule`** — strict 4-dim match to a picked cell (band × hour × expiry × delta)
2. **`force_fit`** — has a (hour × expiry × delta) match but actual IV band differs
3. **`closest_fallback`** — no (hour × expiry × delta) match, picked by minimum distance
4. **`uncovered`** — no trades for this Friday at all in the filtered universe

In the current v3 grid, 89 Fridays land in `rule`, 32 in `force_fit`, 0 in
`closest_fallback`, and 0 in `uncovered` — i.e. **every Friday is accounted
for**, but only ~74% strict-match a picked cell.

The decision rule for live trading:

> **Use the strict (Rule) view as your decision basis.** Force-fit Fridays are
> tradeable but have measurably weaker edge (~61% win rate, $14 avg net) vs.
> strict cells (75-90%+ win rate, $25-50+ avg net). Trading discipline = skip
> the missed regimes; capital preservation > false coverage.

---

## How Friday classification works

The Full-Coverage algorithm runs after the picker selects one best cell per
band. For each Friday in the dataset:

### 1. Rule match (strict)
Friday has a trade where **all four dimensions match** a picked cell exactly:
- `entry_atm_iv_band` ✓
- `entry_hour_ist` ✓
- `expiry_bucket` ✓
- `delta_target` ✓

If multiple picked cells match, tiebreak by `net_pnl_estimate_usd`.

### 2. Force fit (band-mismatched)
Friday has a trade matching `(hour × expiry × delta)` of some picked cell, but
the trade's actual `entry_atm_iv_band` differs from that cell's band.
The Friday is force-fit into the cell's band's "All" group despite the IV
regime mismatch. Tiebreak by `net_pnl_estimate_usd`.

This is a coverage hack — it maximizes per-cell sample size at the cost of
mixing IV regimes within the cell's metric pool.

### 3. Closest fallback
Friday has trades, but **none match `(hour × expiry × delta)` of any picked
cell**. Compute distance to each cell:

```
D = 100·|Δ_diff| + 10·|expiry_idx_diff| + |hour_diff|
```

Delta differences dominate (×100 weight), then expiry-bucket index ordering
(`current Sat → next Sun → next_to_next Mon → weekly → biweekly → monthly →
quarterly`), then entry-hour distance (mapped to a linear `21→0, 22→1, …,
3→6` axis to handle midnight wrap).

Pick the (cell, friday-trade) pair with smallest D.

### 4. Uncovered
Friday has no trades in the derived dataset at all (e.g., universe filters
wiped it out, or the data is genuinely missing for that Friday).

---

## Current state — the 32 force-fit Fridays

In the v3 grid with the 10 picked best-combo cells (default ranking
`avg_net_pnl`, all 10 picked cells use Δ=0.50 except 90-100 band which uses
Δ=0.40):

| Bucket | Count | % of 121 |
|---|---:|---:|
| Rule (strict 4-dim match) | 89 | 73.6% |
| Force-fit | 32 | 26.4% |
| Closest fallback | 0 | 0.0% |
| Uncovered | 0 | 0.0% |

The 32 force-fit Fridays all share the same pattern: their actual IV at the
picked entry hour was NOT in any picked cell's IV band, but they DID have a
trade at one of the picked cells' (hour × expiry × delta) — almost always
the 0-20 band's `(23:00 × next_to_next Mon × Δ=0.50)` cell, which is the
"easiest to match" since it specifies a low-IV regime where most Fridays
genuinely had higher IV.

### Why these Fridays are "missed" by the strict view
At every entry hour swept (00, 01, 02, 03, 21, 22, 23 IST), each of these
Fridays' IV either:
- (a) wasn't in a band whose picked cell uses that hour, OR
- (b) was in a band whose picked cell uses a different hour where this Friday
  had different IV

Result: no strict 4-dim match anywhere in the 10 picked cells.

### Force-fit performance under the nearest cell's rule
When each missed Friday's matching trade was force-fit into the nearest
picked cell's rule (in this dataset, almost always the 0-20 band's
`sl75_max_profit_25`), the aggregate over 31 of the 32 missed Fridays (1
Friday had no `(23:00, next_to_next Mon, Δ=0.50)` trade at all):

| Metric | Force-fit aggregate |
|---|---:|
| Trades attempted | 31 |
| Wins | 19 (61.3%) |
| Losses | 12 (38.7%) |
| **Total net P&L** | **+$447.42** |
| Avg per trade | $14.43 |
| Total winning P&L | +$825.36 |
| Total losing P&L | -$377.93 |
| Biggest single win | +$69.28 |
| Biggest single loss | -$68.65 |

So force-fit IS profitable in aggregate, but with measurably weaker
properties than the strict cells.

### Force-fit under the Friday's OWN actual band's picked rule
A more rigorous test: for each missed Friday, check whether any of its
trades match the picked cell of THAT trade's actual IV band. Result:

> **0 out of 32 Fridays match.**

This is by definition — these Fridays are missed precisely because they
have no strict 4-dim match anywhere. So under disciplined "trade only your
own regime" trading, none of these Fridays would be opened.

---

## Strict vs force-fit comparison

| Compared metric | Strict (rule) | Force-fit |
|---|---|---|
| Sample size per cell | Smaller | Larger |
| Avg per-trade edge | $25-50+ in good cells | $14.43 |
| Win rate | 75-90%+ in good cells | 61% |
| Win/loss asymmetry | Wins notably bigger | Roughly symmetric (worst-case) |
| Regime conditioning | Honors IV band condition | Ignores it |
| Realism for live trading | Matches actual strategy | "What if you ignored discipline" |

---

## Decision framework

### For live trading

**Use the strict (Rule) view.** The decision rule:

1. At entry time, observe current ATM IV → determines the IV band
2. Look up that band's row in the **Best Combo per IV band** table
3. Confirm: are you AT the picked entry hour right now? IV still in the
   picked band? Strikes available at the picked delta? Expiry available?
4. If yes → enter the trade with the picked rule's exit conditions
5. If any of (hour / band / delta / expiry) doesn't match → **skip this
   Friday**. It's not an opportunity for this strategy variant. Discipline.

### Three gotchas to watch for

1. **IV drift between hours** — a Friday's IV can move between bands across
   the entry-hour window. Re-check IV right before entering at the picked
   hour; let *that* hour's IV decide the band.
2. **Hour mismatch = no trade** — the 26.4% "missed" case. Don't force a
   trade just to be in the market.
3. **Tiny n bands are advisory only** — bands 70+ have n ≤ 5 in this
   dataset. The picked combo for those is statistically meaningless until
   more data accumulates.

### When force-fit might be acceptable

A retail trader optimizing for **total absolute return** with a willingness
to absorb noisier outcomes might trade force-fit Fridays too — at the
nearest picked cell's rule, accepting ~$14/trade edge on ~50% additional
opportunities. But:

- Win/loss asymmetry is roughly symmetric (worst-case win ≈ worst-case
  loss), so a bad streak of force-fit losers can erase several strict-cell
  wins
- The aggregate $447 across the 31 force-fit trades equals only ~10
  strict-cell winners — so the marginal value of doing this is modest

Most institutional/disciplined approach: **strict-only**. The clean edge is
the point.

---

## Operational notes

### How to view this in the dashboard
- **Best combo per IV band** (top table): strict, instant from grid, picks
  one cell per band from the 96-rule sweep. Use this for live decisions.
- **Full coverage per IV band** (table below): same picked cell + force_fit
  + closest_fallback. Two side-by-side metric blocks per row ("Rule" /
  "All"). Use this to see how much of the cell's metrics depend on the
  strict-band assumption holding.

### How to query the missed Fridays
The 32 missed Fridays are not exposed via a dedicated UI but can be
identified via Python:

```python
from app.api import m7_results as m7r
trades = m7r._load_trades()
# get picked cells from /iv_band_best_combo response, then:
strict = set()
for c in picked_cells:
    s = trades[(trades['entry_atm_iv_band']==c['iv_band']) &
               (trades['entry_hour_ist']==c['entry_hour_ist']) &
               (trades['expiry_bucket']==c['expiry_bucket']) &
               (trades['delta_target']==c['delta_target'])]
    strict.update(s['friday_date_ist'].astype(str).tolist())
missed = sorted(set(trades['friday_date_ist'].astype(str).unique()) - strict)
```

This gives the 32 missed Friday dates. Their actual band profiles across
the 7 entry hours can then be inspected to understand why they fell outside
the strict picked-cell coverage.

---

## Future work (not done yet)

- Per-trade detail in cell drilldown could include an `actual_band` column
  and a `[force_fit]` tag so users can visually filter strict vs force-fit
  trades.
- A "Live Setup Helper" panel could read current ATM IV from the live
  chain, identify the user's current IV band, show the picked combo for
  that band, and indicate whether the timing matches (go/wait/skip).
- A `MaxProfit hit/miss` per-trade column was discussed but **explicitly
  not implemented** at the user's instruction (see
  `feedback_precompute_in_session_persist_result.md` — schema additions
  require grid rebuild).
