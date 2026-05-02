# Short Strangle — Complete Conversation Summary

**Purpose:** Single-page summary of every concept discussed across this conversation. Use as a study reference and platform-design map.

---

## 1. How spot price affects premium

**Premium = Intrinsic + Extrinsic**
- ITM call intrinsic = max(spot − strike, 0)
- OTM options have ZERO intrinsic; 100% extrinsic (time + IV value)

**Spot scales premium linearly** when everything else (IV, DTE, moneyness %) is held constant:
```
Premium($) = (IV-determined %) × Spot
```
At BTC $100k, ATM 7DTE 25% IV → ~$870 premium. Same setup at $70k → ~$610. The 0.87% credit % stays constant.

**You CANNOT cleanly isolate spot's "contribution"** to premium — spot is the baseline that scales every other factor. What you can do:
- Express everything as % of spot to remove scaling
- Use IVP (already a percentile) to remove regime drift
- Decompose premium *changes* via Greeks (delta, gamma, vega, theta)

---

## 2. Why use Credit % (not absolute $)

Same dollar credit at different spot levels = different yield on dollar-risk:
- $500 at BTC $70k = 0.71% credit
- $500 at BTC $100k = 0.50% credit

Credit % normalizes this. Treat it as your "yield" on the position's notional.

**Always use spot at entry for the calc** (not winners' spot, not average — that introduces survivorship bias).

---

## 3. The two-baseline approach

From your entry log compute:
- **Median credit % across ALL entries** → your typical entry richness
- **Median credit % across WINNING entries** → your edge threshold

Gap between them = your edge signal. Your winners' median becomes the entry threshold.

---

## 4. Z-score and IVP — and why both matter

**Z-score** = how many standard deviations a value sits from the mean.
```
z = (today_value − mean) / std
```
- z=+1 → top 16% (rich)
- z=+2 → top 2.5% (very rich)

**IVP (IV percentile)** = where current IV sits in its own recent history (already 0-100, regime-self-normalizing).

Use both, plus pattern win rate, in a composite quality score:
```
Quality = 0.25·z_all_pct + 0.30·z_winner_pct + 0.30·IVP + 0.15·pattern_winrate
```
- ≥75 → strong entry, full size
- 60-75 → standard
- 45-60 → marginal, half size
- <45 → skip

**Avoid the circular-logic trap**: "median IV across all entries" mixes regimes. Solutions:
1. Use IVP (already self-normalizing)
2. Use rolling median (last 10-15 entries)
3. Bucket by spot regime

---

## 5. The IV regime contribution

You CAN decompose premium into structural vs IV-regime components.

**Method:** Build an IVP→credit% calibration curve from your entries:
| IVP bucket | Median credit % |
|------------|-----------------|
| 0-20 | 0.32% |
| 20-40 | 0.41% |
| 40-60 | 0.48% (= structural baseline at IVP=50) |
| 60-80 | 0.58% |
| 80-100 | 0.71% |

For today's trade:
- **Structural** = baseline_pct(IVP=50) × spot
- **IV regime uplift** = fair_pct(today's IVP) − baseline_pct
- **Excess** = actual − fair (captures skew, term, noise)

Example: $645 credit at IVP=76 might decompose as $480 structural + $100 IV regime + $65 excess. The $100 is "vega-vulnerable" — it disappears if IV crushes back to neutral.

---

## 6. Why curves must be per-DTE

Different DTEs have different premium magnitudes:
- 7DTE: ~$1,470 ATM at IV=60%
- 14DTE: ~$2,080 (≈ √(14/7) × 7DTE ≈ 1.41×)

**Solution: √DTE normalization.** Express all credits as `credit_pct / sqrt(dte)`. Build ONE universal curve. Scale back to actual DTE for trade-specific output.

```
fair_credit_pct(DTE) = universal_curve(IVP) × sqrt(DTE)
```

This sidesteps the small-sample problem of per-DTE bucketing.

---

## 7. Indicators inventory

### Spot-based
Returns (5m/1h/4h/24h/7d), realized vol (close/Parkinson/Garman-Klass at multiple windows), ATR, ADX, RSI, distance from MAs, Bollinger width/%B, RVP at multiple timeframes, day-of-week, hour.

### Options-based
ATM IV at constant maturity (7d/14d/30d/60d), IVP per tenor, multi-timeframe IVP windows, .25Δ and .10Δ IVs (skew), risk reversal, butterfly, term structure slopes, OI distribution, PCR, OI walls, dealer GEX, strangle-specific synthetic IV.

### Derived (both)
IV-RV spread/ratio at multiple windows, VRP percentile, expected move (1σ, 2σ), vol-of-vol.

---

## 8. Master ratio table

### Vol
- IV/RV ratio (>1.15 sell, <1.00 skip)
- Term ratio (front/back IV)
- Skew ratio (call IV / put IV)
- Wing/ATM ratio (smile steepness)

### Premium/risk
- Credit % of spot
- Credit / margin (ROC)
- Annualized credit %
- Credit / daily theta

### Greeks
- **Theta/Vega** — >1.0 theta-dominant, <0.6 vega-dominant
- **Gamma/Theta** (in $ terms via gamma×spot²/100) — <1.5 manageable, >2.5 dangerous
- Vega/credit, theta/credit, delta/credit

### Distance
- Strike/σ, strike/ATR, touch probability ≈ 2×|delta|

### Regime
- RVP/IVP (<0.7 = sell signal)
- Short/Long IVP (recent spike)
- ATR compression

### Personal
- Win rate, avg win/avg loss, profit factor, Sharpe per trade

---

## 9. Long vs Short Gamma (positional)

Rule: **You buy options → you're long gamma. You sell → short gamma.**

A short strangle is ALWAYS short gamma. Both legs have positive gamma; selling them creates negative position gamma.

**Short gamma consequences:**
- Delta moves against you: spot up → delta drops, spot down → delta rises
- Gamma INCREASES as DTE shrinks and as spot approaches strikes
- You're paid theta to compensate for gamma risk

**Diagnostic in dollars:**
```
Dollar gamma = Position_gamma × Spot² × 0.0001
```
A position with -$120 dollar gamma loses $120 per 1% spot move *just from gamma curvature* — and gamma grows as spot moves further.

---

## 10. Dealer Gamma (market-level)

When you sell a strangle, a market maker takes the other side. Aggregating across the whole market, dealers end up either net long or net short gamma — call this **GEX**.

**Long-gamma dealers stabilize:**
- Sell spot into rallies → caps moves
- Buy spot into dips → cushions
- Markets pin/mean-revert → good for your strangle

**Short-gamma dealers destabilize:**
- Buy into rallies → fuel
- Sell into dips → panic
- Markets trend/cascade → bad for your strangle

**Critical clarification:**
- GEX does NOT predict direction. It predicts *amplification* of moves that happen for other reasons.
- "Hedger vs speculator" doesn't matter — dealer hedging activity itself moves the market regardless of why customers traded options.
- GEX is a **risk multiplier**, not a directional signal.

**For your platform:** add GEX as an additional filter. Strong negative GEX + approaching catalyst = highest danger zone for short strangles.

---

## 11. Diagnosing Gamma vs IV-driven moves

Decompose every premium change:
```
ΔPremium ≈ Delta·ΔSpot + ½·Gamma·(ΔSpot)² + Vega·ΔIV + Theta·Δt
```

### Quick visual diagnosis
| Pattern | Likely driver |
|---------|--------------|
| Both legs move SAME direction | VEGA (IV expansion or crush) |
| One leg explodes, other shrinks | GAMMA (spot moved toward strike) |
| Steady daily decay | THETA |
| Premium changes faster than spot explains | VEGA-driven |
| Premium drops fast with stable spot | IV CRUSH (favorable vega) |
| Premium loss with spot moving and IV flat | GAMMA-driven loss |

### Quantitative attribution
For each interval, compute:
```python
delta_pnl = delta · ΔSpot
gamma_pnl = ½ · gamma · ΔSpot²
vega_pnl  = vega · ΔIV
theta_pnl = theta · Δt
```
Largest absolute component is the "dominant Greek." That's the move's character.

### Time-of-trade pattern
- Days 0-1: vega often dominant (post-entry IV shifts)
- Days 2-4: theta dominant (mid-life)
- Days 5-7: gamma starts dominating (acceleration into expiry)

---

## 12. Win/Loss types

### Wins
- **Theta-driven win**: Spot range-bound, IV stable, daily P&L ≈ theta. Boring and ideal.
- **IV-crush win**: IV drops 5+ points after entry, premium collapses fast, exit early. Often event-driven.

### Losses
- **Gamma blowup**: Spot moves toward strike, delta explodes, threatened leg's premium spikes. Loss is asymmetric.
- **Vega expansion**: IV spikes, both legs reprice up symmetrically, spot may not have moved much.
- **Mixed**: Both gamma and vega contribute (common during crash + IV spike combos).

---

## 13. Strike selection

- **Default: .10 delta both sides** → ~1.28σ OTM, ~10% touch probability, balanced credit/safety.
- **Adjust for skew**: BTC has put skew, so consider asymmetric like .08Δ put / .12Δ call.
- **Avoid**: round-number strikes, major OI walls, recent swing highs/lows.
- **Verify**: minimum credit threshold (≥0.4% of spot for 7DTE).

---

## 14. Expiry selection

| DTE | Best for | Risk |
|-----|----------|------|
| 0-3 | Pure theta scalping | Gamma kills you |
| **5-10** | **Standard short strangle** | **Sweet spot** |
| 10-21 | Vega plays, IV crush | Slow theta |
| 21-45 | Pure vega carry | Capital-heavy |
| 45+ | Long-vol carry | Inefficient |

**For your weekend Saturday cadence:** 7DTE is ideal — captures weekend theta + Monday IV crush.

---

## 15. Exit rules

- **Profit target:** 50% of credit (60% can work in BTC)
- **Stop loss:** 2× credit
- **Strike threat:** exit if either delta > 0.30
- **IV expansion:** exit if IV up >30% from entry
- **Trend flip:** exit if ADX_4h > 30 after entry
- **Time stop:** close at 1-2 DTE to avoid pin risk

---

## 16. The whole framework in one decision flow

```
Pre-entry filter chain:

1. Hard filters (must all pass)
   ✓ IVP_4H > 50
   ✓ IV-RV spread > 0
   ✓ ADX_4H < 30
   ✓ DTE in 5-14 range
   ✓ No event in next 24h
   ✓ GEX not extremely negative
   ✓ OI walls allow clean strikes
   
2. Quality score
   Compute composite (vol regime + trend + skew + personal richness)
   ≥75 full size, 60-75 standard, 45-60 half, <45 skip
   
3. Strike selection
   .10Δ default, adjust for skew, avoid walls/round numbers
   Verify min credit % threshold
   
4. Expiry selection
   7-10 DTE for theta-dominant
   10-14 DTE for vega-dominant (IV crush plays)
   
5. Entry execution
   Log full row to backtest/entry log:
   ~110 columns covering identity, structure, spot context, vol context,
   IVP/RVP, OI/dealer context, Greeks, ratios, attribution, scores

Live management:
6. Daily P&L attribution
   Decompose into delta/gamma/vega/theta
   Identify dominant Greek
   
7. Exit triggers
   Profit target / stop / strike threat / IV expansion / trend flip / time
```

---

## 17. What gets stored where

| Data | Source | Where computed |
|------|--------|---------------|
| Spot indicators (Section 3) | spot 5m parquet | `enrich_spot.py` |
| Options indicators (Section 4) | chain snapshots | `enrich_options.py` |
| Derived metrics (Section 5) | both | `enrich_derived.py` |
| Per-trade ratios | both + entry strikes | `backtest.py` |
| Calibration (curves, baselines) | entry log results | `calibration.py` |
| Quality score | all the above | `backfill_attribution.py` |
| Path arrays / hourly attribution | chain snapshots from entry to exit | `backtest.py` |

Master enriched table: `data/derived/full_enriched.parquet` (one row per 5-min snapshot, all indicators).

Backtest output: `data/backtests/<config>.parquet` (one row per trade, ~110 columns).

---

## 18. Implementation priority

**Phase 1 — Spot enrichment** (independent, can run today)  
**Phase 2 — Options enrichment** (uses chain data you already have)  
**Phase 3 — Derived metrics** (joins phases 1+2)  
**Phase 4 — Backtest engine** (consumes phase 3)  
**Phase 5 — Calibration** (consumes phase 4 outputs, feeds back into attribution)  
**Phase 6 — Backtest dashboard** (frontend, displays everything)  
**Phase 7 — Live signal panel** (latest snapshot from phase 3, current-state view)

Each phase is independently testable and useful.

---

## 19. Key honest caveats

1. **Median/z-score baselines are unreliable below 30 entries.** Use 0.50% credit % as a placeholder threshold until you have real data.

2. **Per-DTE bucketing fragments small samples.** Use √DTE normalization to keep one universal curve.

3. **GEX assumes customer-net-long sign convention.** It's an approximation. Direction-sign disputes exist in academic literature.

4. **IVP at the wrong tenor is misleading.** A 30DTE-based IVP can hide event premium in 7DTE IV. Build per-tenor IVPs for accuracy.

5. **Theoretical vs empirical attribution can differ.** Greeks-based attribution leaves a residual; for stressed markets, use full repricing instead of Greeks.

6. **No system guarantees outcomes.** All these indicators shift probabilities, they don't determine outcomes. Position sizing matters more than entry filters.

---

## 20. Two artifacts produced

1. **`SHORT_STRANGLE_INDICATORS_SPEC.md`** — full data spec with every column, formula, and architecture decision. Drop in `docs/` of your repo.

2. **`CLAUDE_CODE_PROMPT.md`** — six-module build prompt for Claude Code. Hands off implementation in clean phases with stop-and-review gates.

---

End of summary.
