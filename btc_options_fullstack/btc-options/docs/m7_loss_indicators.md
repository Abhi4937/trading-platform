# M7 Loss Indicators — entry-time features used to predict winners vs. losers

**Date written:** 2026-05-08
**Source of truth:** `backend/app/api/m7_results.py:2320-2378` — `_M7_LOSS_INDICATORS` list
**Used by:** `/cell_winners_vs_losers` endpoint and the M7 dashboard's per-cell drilldown

Every row in `m7_trades_enriched.parquet` has each of these columns measured
**at the moment of trade entry**. The winners-vs-losers analysis groups trades
by win/loss outcome under a chosen exit rule, then compares the mean of each
indicator between groups. Indicators where `|gap|/σ > threshold` are flagged
as **discriminating** and surface as candidate entry filters.

Total: **46 indicators across 11 categories**.

---

## IV term structure (8)

| Column | Label | What it measures |
|---|---|---|
| `ctx_atm_iv_7d` | ATM IV 7d | Average ATM implied vol over the last 7 days |
| `ctx_atm_iv_14d` | ATM IV 14d | Average over 14 days |
| `ctx_atm_iv_30d` | ATM IV 30d | Average over 30 days |
| `ctx_atm_iv_60d` | ATM IV 60d | Average over 60 days |
| `ctx_ivp_atm_7d_90d` | IVP 7d/90d | Where current IV ranks within last 90 days, using 7d window |
| `ctx_ivp_atm_14d_90d` | IVP 14d/90d | Same, 14d window |
| `ctx_ivp_atm_30d_90d` | IVP 30d/90d | Same, 30d window |
| `ctx_ivp_4h` | IVP 4h | Short-window IV percentile (last 4h vs. recent history) |

## IV velocity / vol-of-vol (4)

| Column | Label | What it measures |
|---|---|---|
| `ivp_4h_delta_24h` | IVP Δ 24h | Change in 4h IVP over the last 24 hours |
| `ivp_4h_delta_48h` | IVP Δ 48h | Change over 48 hours |
| `iv_change_stdev_7d` | IV change σ 7d | Std-dev of IV changes over last 7 days |
| `vov_ratio` | Vol-of-vol ratio | How volatile IV itself has been |

## Realized vol & vol risk premium (8)

| Column | Label | What it measures |
|---|---|---|
| `ctx_rv_7d` | RV 7d | Annualized realized vol from 7-day spot returns |
| `ctx_rv_14d` | RV 14d | Same, 14-day window |
| `ctx_rv_30d` | RV 30d | Same, 30-day window |
| `ctx_iv_rv_spread_7d` | IV-RV spread 7d | Current IV minus 7d RV (positive = premium) |
| `ctx_iv_rv_spread_30d` | IV-RV spread 30d | Same, 30d window |
| `ctx_iv_rv_ratio_7d` | IV/RV ratio 7d | IV divided by 7d RV (>1 = premium) |
| `ctx_vrp_pct_7d` | VRP % 7d | VRP expressed as % of credit |
| `ctx_rvp_4h` | RVP 4h | Realized-vol percentile over short window |

## Skew / smile / term (4)

| Column | Label | What it measures |
|---|---|---|
| `ctx_risk_reversal_25d` | RR 25-delta | 25-delta call IV minus 25-delta put IV |
| `ctx_butterfly_25d` | Butterfly 25-delta | Wing IV minus ATM IV |
| `ctx_wing_atm_ratio` | Wing/ATM ratio | Out-of-the-money IV vs. ATM IV |
| `ctx_term_slope_7_30` | Term slope 7→30 | 7d-to-30d IV term-structure slope |

## Spot regime (2)

| Column | Label | What it measures |
|---|---|---|
| `ctx_adx_14_4h` | ADX-14 on 4h | Trend strength on 4h candles |
| `ctx_atr_pct_4h` | ATR% on 4h | Recent volatility envelope on 4h candles |

## Spot technicals at entry (24 — 4 indicators × 6 timeframes)

Added 2026-05-08: extended from the original 5m+4h coverage to span 6 timeframes:

For each timeframe (`5m`, `15m`, `30m`, `1h`, `4h`, `1d`):

| Indicator pattern | What it measures |
|---|---|
| `entry_rsi_14_<tf>` | RSI(14) at entry on this timeframe |
| `entry_macd_hist_<tf>` | MACD histogram (12, 26, 9) at entry |
| `entry_bb_pct_b_<tf>` | Bollinger %B (20-period, 2σ) — position within bands |
| `entry_atr_pct_<tf>` | ATR(14) as % of price |

Total: 4 × 6 = **24 columns**. Pre-computed in source bars parquet, joined
into trades via ASOF on entry timestamp.

**Recently observed discriminators on the 30-40 cell** (showing the new
timeframes' real signal):

| Indicator | Effect size | Direction |
|---|---:|---|
| `entry_atr_pct_1d` | 0.63σ | Higher daily ATR% → losses |
| `entry_rsi_14_1d` | 0.60σ | Slightly bullish daily RSI → wins |
| `entry_bb_pct_b_1d` | 0.51σ | Higher position in daily BB → wins |
| `entry_atr_pct_30m`, `_1h` | ~0.39σ | Higher = losses |

## Expected move (USD) (3)

| Column | Label | What it measures |
|---|---|---|
| `expected_move_1sigma_7d` | 1σ expected move 7d | Implied dollar range over 7 days |
| `expected_move_1sigma_14d` | 1σ expected move 14d | Same, 14 days |
| `expected_move_1sigma_30d` | 1σ expected move 30d | Same, 30 days |

## Order book / GEX / flow (2)

| Column | Label | What it measures |
|---|---|---|
| `ctx_pcr_oi` | PCR OI | Put/Call open-interest ratio |
| `ctx_total_gex` | Total GEX | Estimated dealer net gamma exposure |

## Premium structure (4)

| Column | Label | What it measures |
|---|---|---|
| `fair_credit_at_ivp` | Fair credit @ IVP | Theoretical credit at current IV percentile |
| `structural_credit_pct` | Structural credit % | Credit collected vs. its structural baseline |
| `iv_regime_premium_pct` | IV regime premium % | Whether you're getting paid more than typical for this regime |
| `excess_over_fair_pct` | Excess over fair % | Credit excess above fair-value baseline |

## Greeks ratios at entry (3)

| Column | Label | What it measures |
|---|---|---|
| `theta_per_vega_call` | θ/ν call | Call leg's theta-to-vega ratio (decay vs. vol risk) |
| `theta_per_vega_put` | θ/ν put | Put leg's theta-to-vega ratio |
| `theta_per_vega_combined` | θ/ν combined | Position-level combined ratio |

## Per-leg skew at entry (3)

| Column | Label | What it measures |
|---|---|---|
| `delta_skew` | Δ skew (call − put) | Call delta minus put delta at entry |
| `iv_skew_pct` | IV skew % (call − put) | Call IV minus put IV |
| `premium_skew_pct` | Premium skew % | Call premium minus put premium |

---

## Why these timeframes — and what's missing

The current set spans **two distinct timeframe families**:

| Family | Timeframes | Purpose |
|---|---|---|
| Macro/regime context | 7d, 14d, 30d, 60d, 90d | "Where are we in the broader vol regime?" |
| Entry-precision context | 5m, 4h, 24h, 48h | "What's the market doing right now at entry?" |

### Gaps relative to a fuller technical-analysis spec

The user's intuition that **15m, 30m, 1h, 1d** would add value is correct. The current set is missing:

| Missing timeframe | Useful for |
|---|---|
| **15m** | Mean-reversion / breakout confirmation just before entry |
| **30m** | Intraday momentum dynamics ahead of Friday→Saturday window |
| **1h** | Hourly momentum/exhaustion (between 5m and 4h) |
| **1d** | Daily regime — "calm day" vs. "volatile day" classification |

### Why they aren't there yet

1. **Iteration order** — Chunk-1 set up the macro/regime context (7d-90d). Chunk-2
   added 5m + 4h entry technicals to bracket the timing. Mid-range timeframes
   (15m, 30m, 1h) and 1d were left out to keep the indicator count manageable
   for the initial winners-vs-losers analysis.
2. **Correlation between adjacent timeframes** — RSI(5m) and RSI(15m) tell
   highly correlated stories. Adding both can double feature count without
   doubling signal.
3. **Risk of overfitting** — with only 121 Fridays in the dataset, every
   additional indicator increases the false-discovery risk. The current 46
   already test the limits of what's safely informative.
4. **Compute & storage** — each new timeframe adds columns to
   `m7_trades_enriched.parquet` and CPU time during the enrichment script.

### When to add them

Adding 15m / 30m / 1h / 1d indicators is straightforward — they extend the
existing 5m and 4h pipelines. Worth doing if:

- An existing 5m or 4h indicator shows discriminating power, and you want to
  test whether adjacent timeframes confirm or refine the signal.
- You're investigating a specific loss-cause (e.g. `directional`) where
  daily-regime context would help (1d).
- You have a hypothesis that mid-range exhaustion (RSI on 1h) precedes the
  Friday→Saturday move on certain Fridays.

Implementation cost: ~30-60 min per new timeframe (extend the indicator
computation in the M7 enrichment script, rerun enrichment over all trades,
add to `_M7_LOSS_INDICATORS`). Backend grid would need rebuilding only if
the indicator becomes part of a sweep dimension; for winners-vs-losers
analysis alone, no rebuild needed.

---

## How to use this list

1. Open the M7 dashboard's **Best Combo per IV band** or **Full Coverage** table
2. Click any cell's drilldown button → opens the cell winners-vs-losers panel
3. Sort the indicator table by `discriminating: true` and `|gap|/σ` desc
4. The top 3-5 indicators are the strongest predictors for that specific cell
5. Use those thresholds as candidate entry filters — re-run a filtered
   backtest to measure win-rate / avg-net-P&L lift

Different cells will surface different top discriminators. There is no
universal "best filter" — the discrimination is per-cell.
