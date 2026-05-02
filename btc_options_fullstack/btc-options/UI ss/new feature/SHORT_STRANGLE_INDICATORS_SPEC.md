# Short Strangle Indicator & Backtest Specification

**Purpose:** Complete specification of every indicator, ratio, and data field needed for backtesting and (later) live trading short strangles on BTC. Built on top of the existing `btc-options` platform.

---

## 1. Document map

```
Section 1 — This map
Section 2 — Architecture overview
Section 3 — Indicators on SPOT data (no options needed)
Section 4 — Indicators on OPTIONS data (chain required)
Section 5 — Derived metrics (computed from spot + options)
Section 6 — All ratios (master table)
Section 7 — Backtest sheet schema (every column explained)
Section 8 — Live "current state" panel (dashboard section)
Section 9 — Calibration data (rolling baselines)
Section 10 — Per-trade attribution output
Section 11 — Implementation roadmap
```

---

## 2. Architecture overview

```
┌────────────────────────────────────────────────────────────┐
│  COLLECTOR (existing)                                      │
│  Writes: spot_5m.parquet, chain snapshots                  │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│  ENRICHMENT LAYER (new)                                    │
│  ─ Spot indicators (Section 3)                             │
│  ─ Options-derived metrics (Sections 4-5)                  │
│  ─ Writes enriched parquet alongside raw data              │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│  CALIBRATION LAYER (new, refreshed weekly)                 │
│  ─ Personal baselines from entry log (Section 9)           │
│  ─ Universal IVP→credit% curve                             │
│  ─ Pattern win-rate stats                                  │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│  BACKTEST ENGINE (new)                                     │
│  ─ Reads enriched data + calibration                       │
│  ─ Simulates trades with full attribution                  │
│  ─ Writes per-trade rows (Section 7) to backtest.parquet   │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│  DASHBOARD                                                 │
│  ─ Backtest results table (every trade, every metric)      │
│  ─ Current state panel (Section 8) — for live use later    │
│  ─ Per-trade attribution drill-down (Section 10)           │
└────────────────────────────────────────────────────────────┘
```

---

## 3. Indicators on SPOT data

These need ONLY the BTC spot price time series. They are computed once for every 5-minute bar in history.

### 3.1 Price returns
| Field | Formula | Use |
|-------|---------|-----|
| `spot_ret_5m` | (close − close_5m_ago) / close_5m_ago | velocity check |
| `spot_ret_1h` | (close − close_1h_ago) / close_1h_ago | velocity check |
| `spot_ret_4h` | (close − close_4h_ago) / close_4h_ago | trend check |
| `spot_ret_24h` | (close − close_24h_ago) / close_24h_ago | trend check |
| `spot_ret_7d` | (close − close_7d_ago) / close_7d_ago | regime context |

### 3.2 Realized volatility (multiple windows)
Use Parkinson / Garman-Klass estimators (more accurate than close-to-close for crypto):

| Field | Formula | Use |
|-------|---------|-----|
| `rv_close_24h` | stdev(log_ret_5m) × √(288 × 365) | classic RV, 24h window |
| `rv_parkinson_24h` | √( (1/(4·ln2)) × Σ(ln(H/L))² ) annualized | more accurate RV |
| `rv_gk_24h` | Garman-Klass formula | most accurate intraday |
| `rv_7d` | stdev annualized over 7 days | medium-term RV |
| `rv_14d` | over 14 days | for IV-RV spread |
| `rv_30d` | over 30 days | regime baseline |

### 3.3 ATR family
| Field | Formula | Use |
|-------|---------|-----|
| `atr_14_4h` | Wilder ATR(14) on 4H bars | typical ATR |
| `atr_pct_4h` | atr_14_4h / spot × 100 | normalized ATR |
| `atr_compression_ratio` | atr_5d / atr_30d | <0.7 = compression, >1.3 = expansion |

### 3.4 Trend / momentum
| Field | Formula | Use |
|-------|---------|-----|
| `adx_4h` | standard ADX(14) on 4H | <25 = no trend (sell), >30 = trend (avoid) |
| `+di_4h`, `-di_4h` | DI components | direction of trend if ADX high |
| `rsi_4h` | RSI(14) on 4H | 40-60 = neutral (good for selling) |
| `rsi_1d` | RSI(14) on daily | regime context |
| `dist_from_ma20_pct` | (spot − ma20) / spot × 100 | mean-reversion proximity |
| `dist_from_ma50_pct` | (spot − ma50) / spot × 100 | trend context |
| `dist_from_ma200_pct` | (spot − ma200) / spot × 100 | macro regime |

### 3.5 Bollinger
| Field | Formula | Use |
|-------|---------|-----|
| `bb_width_4h` | (upper − lower) / mid | vol regime |
| `bb_pct_b_4h` | (close − lower) / (upper − lower) | position in bands |
| `bb_squeeze_flag` | bb_width < 20-day percentile 10 | breakout pending warning |

### 3.6 Realized Volatility Percentile (RVP)
This is your existing indicator concept. For each timeframe, compute percentile rank of current RV vs trailing 90-day RV history.

| Field | Computation |
|-------|-------------|
| `rvp_30m` | percentile rank of 30m-window RV vs last 90d |
| `rvp_1h` | percentile rank of 1h-window RV vs last 90d |
| `rvp_4h` | percentile rank of 4h-window RV vs last 90d |
| `rvp_1d` | percentile rank of 1d-window RV vs last 90d |

### 3.7 Day/time context
| Field | Use |
|-------|-----|
| `day_of_week` | Saturday-entry validation |
| `hour_of_day_ist` | intraday timing |
| `is_weekend` | weekend vol behavior |
| `days_to_next_event` | populated from event calendar |

---

## 4. Indicators on OPTIONS data

These need the full options chain at each snapshot.

### 4.1 ATM IV at constant maturity
For each snapshot, interpolate to fixed DTEs so IVs are comparable across time:

| Field | Computation |
|-------|-------------|
| `atm_iv_7d` | linearly interpolate ATM IV between bracketing expiries to exactly 7d |
| `atm_iv_14d` | same for 14d |
| `atm_iv_30d` | same for 30d (industry standard) |
| `atm_iv_60d` | same for 60d |

### 4.2 IVP (IV Percentile) per tenor
Percentile rank of `atm_iv_Xd` vs trailing 90-day history:

| Field | Use |
|-------|-----|
| `ivp_atm_7d` | for 7DTE strangle decisions |
| `ivp_atm_14d` | for 14DTE strangle decisions |
| `ivp_atm_30d` | the "headline" IVP, market-standard |

### 4.3 Multi-timeframe IVP (your existing indicator)
Same idea but applied to short windows of ATM IV time series:

| Field | Window |
|-------|--------|
| `ivp_30m` | last 30 minutes of atm_iv_30d |
| `ivp_1h` | last 1 hour |
| `ivp_4h` | last 4 hours |
| `ivp_1d` | last 24 hours |

### 4.4 Skew / smile metrics
| Field | Formula | Use |
|-------|---------|-----|
| `iv_25d_call` | IV at .25 delta call | skew computation |
| `iv_25d_put` | IV at .25 delta put | skew computation |
| `iv_10d_call` | IV at .10 delta call | wing pricing |
| `iv_10d_put` | IV at .10 delta put | wing pricing |
| `risk_reversal_25d` | iv_25d_call − iv_25d_put | negative = put skew (BTC normal) |
| `butterfly_25d` | (iv_25d_call + iv_25d_put)/2 − atm_iv | smile steepness |
| `wing_atm_ratio` | (iv_10d_call + iv_10d_put)/2 / atm_iv | rich wings = strangle-favored |

### 4.5 Term structure
| Field | Formula | Interpretation |
|-------|---------|---------------|
| `term_slope_7_30` | atm_iv_30d − atm_iv_7d | positive = contango, negative = backwardation |
| `term_ratio_7_30` | atm_iv_7d / atm_iv_30d | >1.05 = backwardation |
| `term_slope_30_60` | atm_iv_60d − atm_iv_30d | longer-end shape |

### 4.6 Open Interest analysis
| Field | Computation |
|-------|-------------|
| `total_call_oi` | Σ call OI across all expiries |
| `total_put_oi` | Σ put OI across all expiries |
| `pcr_oi` | total_put_oi / total_call_oi |
| `pcr_volume` | put_volume / call_volume (24h) |
| `max_oi_call_strike` | strike with highest call OI (call wall) |
| `max_oi_put_strike` | strike with highest put OI (put wall) |
| `max_oi_call_pct_of_total` | concentration at the call wall |
| `max_oi_put_pct_of_total` | concentration at the put wall |
| `dist_to_call_wall_pct` | (call_wall − spot) / spot × 100 |
| `dist_to_put_wall_pct` | (spot − put_wall) / spot × 100 |

### 4.7 Dealer Gamma Exposure (GEX)
Approximation assuming customer net long, dealer net short:

| Field | Formula |
|-------|---------|
| `gex_per_strike` | -(call_OI × call_gamma + put_OI × put_gamma) × spot² × 0.01 |
| `total_gex` | Σ across strikes (signed) |
| `gex_flip_level` | spot at which cumulative GEX changes sign |
| `gex_regime` | "POSITIVE" / "NEGATIVE" / "NEAR_FLIP" |
| `dist_to_flip_pct` | (spot − gex_flip_level) / spot × 100 |

### 4.8 Strangle-specific synthetic IV
For your .10Δ strangle structure specifically:

| Field | Formula |
|-------|---------|
| `strangle_iv_avg_7d` | (iv_10d_call_7d + iv_10d_put_7d) / 2 |
| `strangle_iv_avg_14d` | same for 14DTE |
| `strangle_ivp_7d` | percentile of strangle_iv_avg_7d vs 90d history |
| `strangle_ivp_14d` | same for 14d |

This is more accurate than ATM IVP because it captures the actual IVs at strikes you'd sell.

---

## 5. Derived metrics (spot + options combined)

### 5.1 Volatility Risk Premium (VRP) family
| Field | Formula | Interpretation |
|-------|---------|---------------|
| `iv_rv_spread_7d` | atm_iv_7d − rv_7d | positive = sellable edge |
| `iv_rv_spread_14d` | atm_iv_14d − rv_14d | medium-term VRP |
| `iv_rv_spread_30d` | atm_iv_30d − rv_30d | long-term VRP |
| `iv_rv_ratio_7d` | atm_iv_7d / rv_7d | >1.15 = strong edge |
| `vrp_pct_7d` | percentile rank of iv_rv_spread_7d over 90d | regime-self-normalizing |
| `vrp_pct_14d` | same for 14d | |

### 5.2 Expected move
| Field | Formula |
|-------|---------|
| `expected_move_1sigma_7d` | spot × atm_iv_7d × √(7/365) |
| `expected_move_1sigma_14d` | spot × atm_iv_14d × √(14/365) |
| `expected_move_2sigma_7d` | 2 × above |

### 5.3 Vol-of-vol
| Field | Formula | Use |
|-------|---------|-----|
| `iv_change_stdev_7d` | stdev of daily ΔIV over last 7 days | how stable is IV |
| `vov_ratio` | iv_change_stdev_7d / atm_iv_30d | normalized vol-of-vol |

---

## 6. Master ratio table

These are computed at trade entry and stored on every backtest row.

### 6.1 Volatility ratios
| Ratio | Formula | Target | Risk Zone |
|-------|---------|--------|-----------|
| IV/RV ratio | iv_7d / rv_7d | > 1.15 | < 1.00 |
| Term ratio | iv_7d / iv_30d | (varies) | extreme either way |
| Skew ratio | iv_25d_call / iv_25d_put | (varies) | <0.85 or >1.05 |
| Wing/ATM ratio | (iv_10d wings avg) / atm_iv | > 1.10 | < 1.05 |
| Strangle/ATM IV | strangle_iv / atm_iv | > 1.05 | ~1.00 |

### 6.2 Premium/risk ratios
| Ratio | Formula | Target | Risk Zone |
|-------|---------|--------|-----------|
| Credit % | credit / spot × 100 | > 0.5% (7DTE) | < 0.4% |
| Credit/width | credit / (call_K − put_K) | (compare) | low |
| ROC | credit / margin | > 8% | < 5% |
| Annualized credit | (credit% / DTE) × 365 | > 25% | < 20% |
| Credit/daily theta | credit / theta_per_day | ≈ DTE | mismatch = mispriced |

### 6.3 Greek ratios
| Ratio | Formula | Target | Risk Zone |
|-------|---------|--------|-----------|
| Theta/Vega | \|theta\| / \|vega\| | > 1.0 (theta-dominant) | < 0.6 (vega-dominant) |
| Gamma/Theta | \|gamma·spot²/100\| / \|theta\| | < 1.5 | > 2.5 (dangerous) |
| Vega/Credit | \|vega\| / credit | < 0.10 | > 0.15 |
| Theta/Credit | theta / credit × 100 | ≈ 100/DTE | mismatch |
| Delta/Credit | \|delta·spot\| / credit | < 5% | > 10% |

### 6.4 Distance ratios
| Ratio | Formula | Target | Risk Zone |
|-------|---------|--------|-----------|
| Strike/σ (call) | (call_K − spot) / expected_move_1sigma | > 1.2σ | < 1.0σ |
| Strike/σ (put) | (spot − put_K) / expected_move_1sigma | > 1.2σ | < 1.0σ |
| Strike/ATR | (strike − spot) / atr_daily | > 5 ATRs | < 3 ATRs |
| Touch probability | ≈ 2 × \|delta\| | < 0.25 | > 0.35 |

### 6.5 Regime ratios
| Ratio | Formula | Interpretation |
|-------|---------|---------------|
| RVP/IVP | rvp_4h / ivp_4h | <0.7 = sell signal |
| Short/Long IVP | ivp_30m / ivp_1d | >1.2 = recent spike |
| ATR compression | atr_5d / atr_30d | 0.85-1.15 = stable |

### 6.6 Personal performance ratios
| Ratio | Formula | Refresh |
|-------|---------|---------|
| Win rate | wins / total | weekly |
| Avg win / avg loss | mean(win pnl) / \|mean(loss pnl)\| | weekly |
| Profit factor | Σ wins / Σ \|losses\| | weekly |
| Sharpe per trade | mean(pnl) / stdev(pnl) | weekly |
| Max DD / avg win | worst cumulative loss / avg winning pnl | weekly |

---

## 7. Backtest sheet schema (per-trade row)

Every backtested trade produces ONE row with all of the following.

### 7.1 Identity & timing (8 columns)
```
entry_id           uuid
entry_ts_ist       timestamp at entry
exit_ts_ist        timestamp at exit
holding_hours      float
day_of_week_entry  string (Mon-Sun)
hour_of_day_entry  int
dte_at_entry       int
expiry_ts_ist      timestamp of option expiry
```

### 7.2 Position structure (12 columns)
```
call_strike
call_iv_at_entry
call_delta_at_entry
call_gamma_at_entry
call_theta_at_entry
call_vega_at_entry
call_credit
put_strike
put_iv_at_entry
put_delta_at_entry
put_gamma_at_entry
put_theta_at_entry
put_vega_at_entry
put_credit
total_credit
strangle_width_pct        (call_K − put_K) / spot × 100
call_otm_pct
put_otm_pct
```

### 7.3 Spot context at entry (8 columns)
```
spot_at_entry
spot_ret_1h_at_entry
spot_ret_4h_at_entry
spot_ret_24h_at_entry
spot_ret_7d_at_entry
atr_pct_4h_at_entry
adx_4h_at_entry
rsi_4h_at_entry
```

### 7.4 IV/Vol context at entry (12 columns) — sourced from Section 4
```
atm_iv_7d_at_entry
atm_iv_14d_at_entry
atm_iv_30d_at_entry
strangle_iv_avg_at_entry
risk_reversal_25d_at_entry
butterfly_25d_at_entry
wing_atm_ratio_at_entry
term_slope_7_30_at_entry
rv_7d_at_entry
rv_14d_at_entry
iv_rv_spread_7d_at_entry
iv_rv_ratio_7d_at_entry
```

### 7.5 IVP / RVP context (10 columns)
```
ivp_atm_7d_at_entry
ivp_atm_30d_at_entry
ivp_30m_at_entry
ivp_1h_at_entry
ivp_4h_at_entry
ivp_1d_at_entry
rvp_30m_at_entry
rvp_4h_at_entry
rvp_1d_at_entry
vrp_pct_7d_at_entry
```

### 7.6 OI / dealer context (8 columns)
```
pcr_oi_at_entry
max_oi_call_strike_at_entry
max_oi_put_strike_at_entry
dist_to_call_wall_pct
dist_to_put_wall_pct
total_gex_at_entry
gex_regime_at_entry        ("POSITIVE" / "NEGATIVE" / "NEAR_FLIP")
dist_to_flip_pct
```

### 7.7 Position-level Greeks at entry (4 columns)
```
position_delta_at_entry
position_gamma_at_entry
position_theta_at_entry    (positive — you collect)
position_vega_at_entry     (negative — short vol)
```

### 7.8 Computed ratios at entry (15 columns) — from Section 6
```
credit_pct                          credit / spot × 100
credit_pct_normalized               credit_pct / sqrt(dte)
credit_per_day                      total_credit / dte
annualized_credit_pct               credit_pct × 365 / dte
roc_estimate                        total_credit / margin_required
theta_vega_ratio                    |theta| / |vega|
gamma_theta_ratio                   |gamma·spot²/100| / |theta|
vega_credit_ratio                   |vega| / total_credit
delta_credit_ratio                  |delta·spot| / total_credit
call_strike_sigma_dist              (call_K − spot) / 1σ_move
put_strike_sigma_dist               (spot − put_K) / 1σ_move
call_strike_atr_dist                (call_K − spot) / atr_daily
put_strike_atr_dist                 (spot − put_K) / atr_daily
rvp_ivp_ratio                       rvp_4h / ivp_4h
short_long_ivp_ratio                ivp_30m / ivp_1d
```

### 7.9 Attribution (computed from calibration) — 10 columns
```
fair_credit_at_ivp                  from universal IVP→credit% curve
structural_credit                   credit at IVP=50 (neutral)
iv_regime_premium                   fair − structural
excess_over_fair                    actual − fair
pct_from_structural                 structural / total × 100
pct_from_iv_regime                  iv_regime / total × 100
pct_from_excess                     excess / total × 100
z_score_vs_all                      (credit_pct − μ_all) / σ_all
z_score_vs_winners                  (credit_pct − μ_win) / σ_win
quality_score                       0-100 composite (Section 9.4)
pattern                             "A" / "B" / "C" / "D"
pattern_winrate                     historical win rate of pattern
```

### 7.10 Trade journey arrays — for path analysis
Stored as nested lists on the row (or separate parquet keyed by entry_id):
```
hourly_ts                array of timestamps
hourly_spot              array of spot prices
hourly_atm_iv_7d         array of IVs
hourly_premium           array of MTM premium values
hourly_position_pnl      array of unrealized P&L
hourly_position_delta    array of running delta
hourly_position_gamma    array
hourly_position_vega     array
hourly_position_theta    array
```

### 7.11 P&L attribution path (one row per hour, separate parquet)
```
entry_id           join key
hour_offset        hours since entry
spot_change        cumulative ΔSpot
iv_change          cumulative ΔIV
delta_pnl          cumulative delta contribution
gamma_pnl          cumulative gamma contribution (always negative for short)
vega_pnl           cumulative vega contribution
theta_pnl          cumulative theta collected
residual           non-Greek explainable
total_pnl          actual
dominant_greek     "delta"/"gamma"/"vega"/"theta"
dominance_pct      0-100
```

### 7.12 Outcome (10 columns)
```
exit_reason                         "expiry"/"profit_target"/"stop_loss"/"strike_threat"/"time_stop"
exit_credit                         cost to close (0 if expired worthless)
exit_spot
exit_atm_iv_7d
gross_pnl                           total_credit − exit_credit
fees_estimate
net_pnl
return_on_credit                    net_pnl / total_credit
return_on_margin                    net_pnl / margin_required
outcome                             "win" / "loss" / "breakeven"
max_adverse_excursion               worst MTM during trade
mae_ts
max_favorable_excursion             best MTM during trade
mfe_ts
breach_flag                         True if either strike was touched
breach_side                         "call" / "put" / null
breach_ts                           when first breached
```

### 7.13 Diagnosis (computed at exit)
```
win_type                  "theta_driven" / "iv_crush" / "mixed_win" / null
loss_type                 "gamma_blowup" / "vega_expansion" / "mixed_loss" / null
dominant_pnl_greek        biggest absolute contributor over trade life
notes                     auto-generated explanation
```

**TOTAL: ~110 columns per trade row.**

---

## 8. Live "current state" panel

This is what you see at any moment to decide if NOW is a good entry. Sourced from the same enriched data:

### 8.1 Top — decision summary
```
QUALITY SCORE: 78/100              ✓ ENTER (full size)
PATTERN: A (Fresh Spike)
RECOMMENDED: 7DTE, .10Δ both sides
EXPECTED CREDIT: $645 (0.63% of spot)
DOMINANT EDGE: 60% theta / 25% vega / 15% structural
```

### 8.2 Hard filter checklist
```
[✓] IVP_atm_7d = 76 (>50)
[✓] IV-RV spread = +9.1% (>0)
[✓] ADX_4h = 18 (<30)
[✓] DTE candidate = 7 (5-14 range)
[✓] No events in next 24h
[✓] OI walls give clean strike room
[✓] GEX regime = POSITIVE
```

### 8.3 Vol regime detail
```
ATM IV 7D:         47.2%
RV 7D:             38.1%
IV-RV spread:      +9.1%
IV-RV ratio:       1.24
VRP percentile:    78th
─────────────────────────
IVP_30M:           71
IVP_4H:            76
IVP_1D:            72
RVP_4H:            42
RVP/IVP ratio:     0.55  (sell signal)
─────────────────────────
Term slope 7-30:   -2.1   (backwardation)
Skew 25Δ:          -3.2   (puts richer)
Butterfly:         +2.4
Wing/ATM ratio:    1.12
```

### 8.4 Strike candidates (for selected DTE)
```
SHORT CALL OPTIONS:
  .15Δ at 108k → credit $360, sigma_dist=1.05, ATR=4.7
  .10Δ at 110k → credit $290, sigma_dist=1.32, ATR=5.8  ← suggested
  .07Δ at 113k → credit $210, sigma_dist=1.65, ATR=7.2

SHORT PUT OPTIONS:
  .15Δ at 96k  → credit $410, sigma_dist=1.02, ATR=4.5
  .10Δ at 95k  → credit $355, sigma_dist=1.28, ATR=5.5  ← suggested
  .07Δ at 93k  → credit $250, sigma_dist=1.61, ATR=6.9

Suggested strangle: 95k / 110k
  Total credit: $645 (0.630% of spot)
  Width: 14.6%
  Skew check: put_credit/call_credit = 1.22 (typical BTC put skew)
```

### 8.5 Greek profile
```
Position Delta:  -0.05 (near-neutral)
Position Gamma:  -0.012
Position Theta:  +52/day
Position Vega:   -38

Theta/Vega ratio:     1.37  (theta-dominant)
Gamma/Theta ratio:    1.85  (manageable)
Theta as % credit:    8.0%/day  (matches 1/DTE)
Vega exposure %:      5.9% per 1pt IV
```

### 8.6 Dealer gamma context
```
Total GEX:             +$2.3B (POSITIVE)
GEX flip level:        $98,200
Distance to flip:      +4.2% (above flip — stabilizing)
Call wall:             $115,000 (5.0% above)
Put wall:              $92,000 (8.2% below)
Regime:                STABLE / pinning bias
```

### 8.7 Risk preview
```
1σ expected move (7d):    ±$3,400 (3.3%)
2σ expected move (7d):    ±$6,800 (6.6%)

Strike sigma distance:
  Call (110k): 1.32σ → ~9% touch probability
  Put (95k):   1.28σ → ~10% touch probability

Stress scenarios:
  BTC +3%:  ~-$200 P&L (delta+gamma)
  BTC +5%:  ~-$650 P&L (gamma accelerating)
  BTC +7%:  call breach likely
  BTC -3%:  ~-$220 P&L
  BTC -5%:  ~-$700 P&L
  BTC -7%:  put breach likely
  IV +5pt:  ~-$190 P&L (vega)
  IV -5pt:  ~+$190 P&L (vega favorable)
```

### 8.8 Recommended exit plan
```
Profit target:     50% of credit ($323)
Stop loss:         2× credit ($1,290)
Time stop:         Day 5 of 7
Strike threat:     exit if either delta > 0.30
IV expansion:      exit if atm_iv > 60%
Trend flip:        exit if ADX_4h crosses 30
```

---

## 9. Calibration data (rolling baselines)

### 9.1 Universal IVP→credit% curve (DTE-normalized)

Computed weekly from your entry log:
```
ivp_bucket    median_credit_pct_norm   n_entries   last_updated
0-20          0.105                    4           2026-04-28
20-40         0.135                    7
40-60         0.165                    12
60-80         0.205                    9
80-100        0.245                    5
```

Where `credit_pct_norm = credit_pct / sqrt(dte)`.

To get DTE-specific fair credit:
```
fair_pct(IVP, DTE) = curve(IVP) × sqrt(DTE)
```

### 9.2 Personal baselines (z-score parameters)
```
mean_credit_pct_all
std_credit_pct_all
mean_credit_pct_winners
std_credit_pct_winners
mean_ivp_at_winners
last_updated
n_total_entries
n_winning_entries
```

### 9.3 Pattern statistics
```
pattern   n_trades   n_wins   win_rate   median_credit_pct   median_pnl   avg_holding_hrs
A         12          10       0.83       0.62%               $385         48
B         8           6        0.75       0.71%               $310         52
C         6           3        0.50       0.46%               $80          70
D         4           1        0.25       0.52%               -$280        38
```

### 9.4 Quality score formula

```python
def quality_score(row, baselines):
    # Component 1: Vol regime (40%)
    ivp_score = row.ivp_atm_7d_at_entry  # already 0-100
    iv_rv_score = clamp((row.iv_rv_spread_7d / 0.10) * 100, 0, 100)
    term_score = 50 + (-row.term_slope_7_30) * 10  # backwardation positive
    vol_score = 0.4*ivp_score + 0.4*iv_rv_score + 0.2*term_score
    
    # Component 2: Trend regime (25%)
    adx_score = max(0, 100 - row.adx_4h_at_entry * 2.5)
    rsi_score = 100 - abs(row.rsi_4h_at_entry - 50) * 2
    trend_score = 0.6*adx_score + 0.4*rsi_score
    
    # Component 3: Skew/structure (15%)
    skew_score = clamp(50 + row.risk_reversal_25d_at_entry * -3, 0, 100)
    wing_score = clamp((row.wing_atm_ratio_at_entry - 1.0) * 500, 0, 100)
    structure_score = 0.5*skew_score + 0.5*wing_score
    
    # Component 4: Personal richness (20%)
    z_winner_pct = norm.cdf(row.z_score_vs_winners) * 100
    pattern_score = baselines.pattern_winrate.get(row.pattern, 50)
    personal_score = 0.6*z_winner_pct + 0.4*pattern_score
    
    return 0.40*vol_score + 0.25*trend_score + 0.15*structure_score + 0.20*personal_score
```

---

## 10. Per-trade attribution output

After each backtested trade, generate a "trade card" like this:

```
═══════════════════════════════════════════════════════════
  TRADE #142 — 2025-04-12 Sat 10:00 IST
═══════════════════════════════════════════════════════════
  ENTRY CONTEXT
    Spot:     $102,400        Pattern: A
    DTE:      7                Day:    Saturday 10:00
    Strikes:  95k / 110k       Width:  14.6%
  
  PREMIUM RECEIVED                $645 (0.630%)
  Normalized (per √DTE):          0.238
  
  ATTRIBUTION
    Structural baseline      $480  ████████████░░░░  74%
    IV regime uplift         $100  ███░░░░░░░░░░░░░  16%
    Excess over IVP-fair      $65  ██░░░░░░░░░░░░░░  10%
  
  RICHNESS SCORES
    vs Your History          91   ████████████████░░
    vs Your Winners          76   ████████████░░░░░░
    Market IVP_4H            76   ████████████░░░░░░
    Pattern A win rate       80   █████████████░░░░░
    ──────────────────────────────────────────
    Quality Score            81   STRONG ENTRY
  
  GREEKS AT ENTRY
    Delta:    -0.05    Theta:   +52/day
    Gamma:    -0.012   Vega:    -38
    Theta/Vega: 1.37  (theta-dominant)
    Gamma/Theta: 1.85 (manageable)
  
  VOL CONTEXT
    ATM IV 7D:    47.2%      RV 7D:    38.1%
    IV-RV spread: +9.1%      IV-RV %ile: 78
    Skew 25Δ:     -3.2       Term slope: -2.1
    GEX:          POSITIVE   Flip dist: +4.2%
  
  PATH SUMMARY (7-day journey)
    Max favorable: +$430 at hour 84
    Max adverse:   -$120 at hour 36
    Breached:      No
    Dominant Greek (over life): theta (61%)
  
  OUTCOME
    Exit:    2025-04-19 10:00 (expired worthless)
    P&L:     +$645 (100% of credit)
    Type:    THETA-DRIVEN WIN
    
  DIAGNOSIS
    Boring expiry win. Spot range-bound (±2.5%),
    IV stable. Theta collected as expected.
═══════════════════════════════════════════════════════════
```

---

## 11. Implementation roadmap

### Phase 1 — Spot indicators (Section 3)
**Deliverable:** `enrich_spot.py` reads `spot_5m.parquet`, computes all spot-side indicators, writes `spot_enriched.parquet`.

### Phase 2 — Options indicators (Sections 4-5)
**Deliverable:** `enrich_options.py` reads chain snapshots, computes constant-maturity IV, IVP, skew, term, GEX, writes `options_enriched.parquet`.

### Phase 3 — Backtest engine
**Deliverable:** `backtest.py` reads enriched parquets, simulates strangle entries on a schedule (e.g., Saturdays), computes per-trade attribution, writes `backtest_results.parquet` with all 110 columns.

### Phase 4 — Calibration module
**Deliverable:** `calibration.py` reads completed entries, computes universal curve, personal baselines, pattern stats, writes `calibration.parquet`.

### Phase 5 — Backtest dashboard
**Deliverable:** Frontend page that:
- Lists all backtested trades (sortable, filterable)
- Click a trade → show full attribution card (Section 10)
- Aggregate stats panel (win rate by pattern, by IVP bucket, etc.)

### Phase 6 — Live "current state" panel
**Deliverable:** Real-time version of Section 8 panel that polls latest enriched data and displays the same indicators on current market state. Used for live entry decisions.

---

## 12. Data source map

| Indicator | Source | Computed where |
|-----------|--------|---------------|
| All Section 3 (spot) | `spot_5m.parquet` | `enrich_spot.py` |
| All Section 4 (options) | chain snapshots | `enrich_options.py` |
| All Section 5 (derived) | both above | `enrich_derived.py` |
| All Section 6 ratios | both above + entry strikes | `backtest.py` per-trade |
| Calibration baselines | `entry_log.parquet` (after backtest) | `calibration.py` |
| Quality score | all the above | `backtest.py` |
| Attribution | calibration + entry data | `backtest.py` |
| Path arrays | chain snapshots from entry to exit | `backtest.py` |

---

## End of spec
