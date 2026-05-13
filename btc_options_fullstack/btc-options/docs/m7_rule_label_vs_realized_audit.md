# M7 — Rule Label vs Realized % Return Audit

_Background_: A cell labelled `sl{X}_max_profit_{Y}` fires when `(gross_pnl − entry_slip) / credit ≥ Y/100`. But the cell metric `avg_pct_return_on_credit` reports `net_pnl / credit` (also subtracts entry_brokerage + exit_slip + exit_brokerage). So realized < nominal even at 100% hit, by the exit-cost drag.

## Step 1 — Pure-trigger cells (hit% = 100%)
Cells with n_trades > 0 AND 100% rule_trigger AND no premium_sl fires (pure take-profit only): **6,788**

_This filter is critical_: a `sl50_max_profit_60` cell where the SL caught a loser at +50% premium uplift counts as `n_rule_trigger` but the realized return reflects the SL loss, not the take-profit intent. Excluding cells where `n_premium_sl_hit > 0` isolates the true take-profit fires.

### max_profit family vs `avg_pct_return_on_credit`
- N cells: **3,874**
- mean gap: **-6.53pp**, median **-5.90pp**
- 5th/95th pct: -13.74pp / -1.81pp
- worst (most negative): **-21.55pp**

Gap buckets:
```
  ≤ -1pp         98  (  2.5%)
  -1..-3pp      691  ( 17.8%)
  -3..-5pp      844  ( 21.8%)
  -5..-10pp    1508  ( 38.9%)
  < -10pp       733  ( 18.9%)
```

Per-band median gap (pp):
```
  0-20      -9.65pp
  100+      -3.27pp
  20-30     -11.32pp
  30-40     -9.30pp
  40-50     -11.75pp
  50-60     -5.88pp
  60-70     -7.08pp
  70-80     -4.31pp
  80-90     -5.09pp
  90-100    -4.28pp
```

Top 5 worst outliers:
```
  band   expiry                    Δ  hr  rule                          n     nom     real      gap    credit
  0-20   next (Sun)             0.05  21  sl50_max_profit_10            1   10.0%   -11.6%  -21.55pp  $   6.51
  0-20   next (Sun)             0.05  21  sl75_max_profit_10            1   10.0%   -11.6%  -21.55pp  $   6.51
  0-20   next (Sun)             0.05  21  sl100_max_profit_10           1   10.0%   -11.6%  -21.55pp  $   6.51
  20-30  next (Sun)             0.05  21  sl50_max_profit_40            4   40.0%    19.7%  -20.28pp  $   7.61
  20-30  next (Sun)             0.05  21  sl75_max_profit_40            4   40.0%    19.7%  -20.28pp  $   7.61
```

### margin_target family vs `avg_pct_return_on_margin`
- N cells: **2,914**
- mean gap: **-3.46pp**, median **-3.35pp**
- 5th/95th pct: -6.61pp / -1.60pp
- worst (most negative): **-16.59pp**

Gap buckets:
```
  ≤ -1pp         97  (  3.3%)
  -1..-3pp      741  ( 25.4%)
  -3..-5pp     1846  ( 63.3%)
  -5..-10pp     221  (  7.6%)
  < -10pp         9  (  0.3%)
```

Per-band median gap (pp):
```
  0-20      -3.37pp
  100+      -2.69pp
  20-30     -4.36pp
  30-40     -7.07pp
  40-50     -6.13pp
  50-60     -3.35pp
  60-70     -3.81pp
  70-80     -3.41pp
  80-90     -3.10pp
  90-100    -2.99pp
```

Top 5 worst outliers:
```
  band   expiry                    Δ  hr  rule                          n     nom     real      gap    credit
  50-60  monthly (30d)          0.05  23  sl50_margin_target_10         1   10.0%    -6.6%  -16.59pp  $  37.02
  50-60  monthly (30d)          0.05  23  sl75_margin_target_10         1   10.0%    -6.6%  -16.59pp  $  37.02
  50-60  monthly (30d)          0.05  23  sl100_margin_target_10        1   10.0%    -6.6%  -16.59pp  $  37.02
  40-50  biweekly (14d)         0.05   0  sl50_margin_target_10         2   10.0%    -0.5%  -10.45pp  $  21.38
  40-50  biweekly (14d)         0.05   0  sl75_margin_target_10         2   10.0%    -0.5%  -10.45pp  $  21.38
```

## Step 2 — Mixed cells (hit% < 100%) — hard-cap dilution
Cells with n_trades ≥ 5 and partial rule fires: **49,331**

### max_profit vs `avg_pct_return_on_credit`

Gap by hit_pct bucket (cell-wide, hard-cap diluted):
```
  hit%         n cells    median gap   median hit%
  0-25%           9456       -40.65pp          9.1%
  25-50%          3911       -44.96pp         38.9%
  50-75%          3181       -50.79pp         63.2%
  75-99%          3786       -29.45pp         87.5%
```

### margin_target vs `avg_pct_return_on_margin`

Gap by hit_pct bucket (cell-wide, hard-cap diluted):
```
  hit%         n cells    median gap   median hit%
  0-25%          10143       -40.26pp         10.5%
  25-50%          6730       -35.48pp         40.0%
  50-75%          7182       -38.89pp         62.5%
  75-99%          4942       -21.96pp         85.7%
```

### Per-trigger conditional mean — 8 representative cells

Re-queries `m7_trades_enriched.parquet` filtered to `exit_reason == 'rule_trigger'` only, then computes `mean(net_pnl/credit)` on JUST the trades that actually fired the rule. This is apples-to-apples vs the nominal label.

_trades parquet missing required columns — skipped_

## Step 3 — Premium SL cross-check
Cells where ALL trades hit premium_sl: **17,404**

For these cells, the average loss as a fraction of credit should be roughly the SL multiplier of the credit (since the SL fires when the short premium has grown by SL%). Approximate sanity check using `avg_loss_usd` / `avg_credit`:

```
  SL %       n cells      median loss / credit
     50%        8770                   15.7%
     75%        4957                   23.3%
    100%        3677                   34.7%
```

_Interpretation_: a row labelled `sl50_*` should show loss / credit around 50%. Big deviations indicate that the loss leg's gross is going further than the SL threshold before the exit actually settles (e.g. minute-bar gap).

## Verdict

✗ **Unexplained drift** — pure-trigger max_profit cells show median gap `-5.60pp` (more than 5pp below nominal). 56 cells deviate by more than 10pp. This exceeds expected exit-cost drag. Recommend follow-up: either tighten trigger threshold to include exit costs, or expose a `gross_return_on_credit` metric so the rule label is apples-to-apples with the displayed metric.
