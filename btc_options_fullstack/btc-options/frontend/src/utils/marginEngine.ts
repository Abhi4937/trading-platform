/**
 * Delta Exchange India — Portfolio Margin Engine
 *
 * Implements the official 29-scenario stress-test + margin-floor methodology.
 *   Portfolio Margin   = max(Risk Margin, Margin Floor)
 *   Initial Margin     = Portfolio Margin − UCF  (UCF = 0 in simulator)
 *   Maintenance Margin = 0.80 × Initial Margin   (UCF = 0)
 *
 * Reference: guides.delta.exchange/delta-exchange-india-user-guide/
 *            trading-guide/margin-explainer/portfolio-margin
 */

export const CONTRACT_VALUE = 0.001; // BTC per BTC-options contract on Delta India

// ─── Black-Scholes ────────────────────────────────────────────────────────────

/** Cumulative standard normal distribution (Abramowitz & Stegun, error < 7.5e-8) */
function normCDF(x: number): number {
  const a1 =  0.254829592;
  const a2 = -0.284496736;
  const a3 =  1.421413741;
  const a4 = -1.453152027;
  const a5 =  1.061405429;
  const p  =  0.3275911;
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1.0 / (1.0 + p * ax);
  const y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
  return 0.5 * (1.0 + sign * y);
}

/**
 * Black-Scholes option price.
 * Returns USDT per BTC (same units as S and K).
 * Multiply by CONTRACT_VALUE to get USDT per contract.
 */
export function bsPrice(
  S: number, K: number, T: number, r: number, sigma: number, isCall: boolean
): number {
  if (T <= 0 || sigma <= 0) {
    return Math.max(0, isCall ? S - K : K - S);
  }
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const df = Math.exp(-r * T);
  return isCall
    ? S * normCDF(d1) - K * df * normCDF(d2)
    : K * df * normCDF(-d2) - S * normCDF(-d1);
}

export function bsDelta(S: number, K: number, T: number, r: number, sigma: number, isCall: boolean): number {
  if (T <= 0 || sigma <= 0) return isCall ? (S >= K ? 1 : 0) : (S >= K ? 0 : -1);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  return isCall ? normCDF(d1) : normCDF(d1) - 1;
}

export function bsGamma(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0 || sigma <= 0) return 0;
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const pdf = Math.exp(-0.5 * d1 * d1) / Math.sqrt(2 * Math.PI);
  return pdf / (S * sigma * sqrtT);
}

export function bsTheta(S: number, K: number, T: number, r: number, sigma: number, isCall: boolean): number {
  if (T <= 0 || sigma <= 0) return 0;
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const pdf = Math.exp(-0.5 * d1 * d1) / Math.sqrt(2 * Math.PI);
  const df  = Math.exp(-r * T);
  const common = -(S * pdf * sigma) / (2 * sqrtT);
  return isCall
    ? (common - r * K * df * normCDF(d2)) / 365
    : (common + r * K * df * normCDF(-d2)) / 365;
}

export function bsVega(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0 || sigma <= 0) return 0;
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const pdf = Math.exp(-0.5 * d1 * d1) / Math.sqrt(2 * Math.PI);
  return S * pdf * sqrtT / 100;
}

// ─── Delta Exchange India — shock-span parameters ────────────────────────────
// All formulas from: guides.delta.exchange portfolio-margin docs (India, BTC).
// Spans are piecewise-linear in portfolio notional; DTE scales the vol spans.

const NOTIONAL_FLOOR       = 100_000;       // USD — ramp starts above this

const PRICE_SHOCK_MIN      = 0.01;          // 1%
const PRICE_SHOCK_MAX      = 0.10;          // 10%
const PRICE_SHOCK_CAP_NOT  = 2_350_000;     // USD — capped above this
const PRICE_SHOCK_SLOPE    = 0.00000004;    // per $ over floor

const VOL_DOWN_MIN         = 0.06;          // 6%
const VOL_DOWN_MAX         = 0.30;          // 30%
const VOL_CAP_NOT          = 2_100_000;     // USD
const VOL_DOWN_SLOPE       = 0.00000012;

const VOL_UP_MIN           = 0.09;          // 9%
const VOL_UP_MAX           = 0.45;          // 45%
const VOL_UP_SLOPE         = 0.00000018;

// DTE adjustment: IV_shock = span × min(DTE_MULT_CAP, (30 / DTE)^0.30)
// Cap prevents vol shock blowing up at very-near-expiry. Empirically Delta's
// basket engine doesn't track unbounded growth past ~2.5×.
const DTE_REF              = 30;            // days (reference point — no adjustment at 30 DTE)
const DTE_EXPONENT         = 0.30;
const DTE_MULT_CAP         = 2.5;
const DTE_FLOOR_DAYS       = 1 / 24;        // clamp to 1 hour to prevent div-by-zero

// 9-point price grid (fractions of priceShock span)
const PRICE_STEPS = [-1, -2 / 3, -0.5, -1 / 3, 0, 1 / 3, 0.5, 2 / 3, 1] as const;
const EXTREME_PRICE_MULT   = 3.0;           // 300% of span for tail scenarios
const EXTREME_WEIGHT       = 1 / 3;         // weight applied to tail scenario loss

// Options Margin % (OM%) — used for the per-leg margin floor
const OM_PCT_FLOOR_NOT     = 200_000;       // USD
const OM_PCT_MIN           = 0.0055;        // 0.55% — empirical (docs say 0.5%)
const OM_PCT_MAX           = 0.02;          // 2%
const OM_PCT_SLOPE         = 5e-9;          // 0.0000005% per $1 above floor notional

// Floor coefficient on premium.
// Docs say 5%, empirically calibrated against the TRUE basket-margin endpoint
// /v2/orders/estimate_margin/basket. ~65% of premium for ATM/near-ATM positions
// where premium dominates. (Earlier 0.90 was fit against single-leg endpoint
// which over-states basket margin because it ignores cross-leg netting.)
const FLOOR_PREMIUM_COEF   = 0.65;

// Empirical DTE scaling on portfolio_margin.
// Our BS scenario engine systematically over-estimates Delta's actual margin
// as DTE grows (their engine isn't pure BS — likely smile-aware + path-dependent).
// Calibrated against the TRUE basket-margin endpoint
// /v2/orders/estimate_margin/basket across DTE × moneyness × qty grid (see
// scripts/calibrate_margin.py). Re-fit via fit_margin_scale.py.
//
//   scale(DTE) = min(1.0, (DTE_SCALE_T0 / DTE_days)^DTE_SCALE_EXP)
//
// Median residual after applying: ±7% across DTE 0.4d-60d, all moneyness.
const DTE_SCALE_T0         = 0.49;
const DTE_SCALE_EXP        = 0.234;

// ─── Shock-span helpers ───────────────────────────────────────────────────────

function clamp(x: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, x));
}

function ramp(notional: number, lo: number, hi: number,
              floorN: number, capN: number, slope: number): number {
  if (notional <= floorN) return lo;
  if (notional >= capN)   return hi;
  return lo + slope * (notional - floorN);
}

interface ShockParams {
  priceShock: number;
  volUp:      number;
  volDown:    number;
}

function getShockParams(totalNotional: number): ShockParams {
  return {
    priceShock: ramp(totalNotional, PRICE_SHOCK_MIN, PRICE_SHOCK_MAX, NOTIONAL_FLOOR, PRICE_SHOCK_CAP_NOT, PRICE_SHOCK_SLOPE),
    volUp:      ramp(totalNotional, VOL_UP_MIN,      VOL_UP_MAX,      NOTIONAL_FLOOR, VOL_CAP_NOT,          VOL_UP_SLOPE),
    volDown:    ramp(totalNotional, VOL_DOWN_MIN,     VOL_DOWN_MAX,    NOTIONAL_FLOOR, VOL_CAP_NOT,          VOL_DOWN_SLOPE),
  };
}

function getOmPct(shortOptionsNotional: number): number {
  return clamp(
    OM_PCT_MIN + OM_PCT_SLOPE * Math.max(0, shortOptionsNotional - OM_PCT_FLOOR_NOT),
    OM_PCT_MIN, OM_PCT_MAX,
  );
}

// IV shock adjusted for DTE: short-dated options get a larger vol shock
// (vol variance scales sub-linearly with time → (30/DTE)^0.30).
// Portfolio uses minimum DTE (most conservative — worst-case leg drives gamma/vega risk).
function dteAdjust(span: number, dteDays: number): number {
  const mult = Math.pow(DTE_REF / Math.max(DTE_FLOOR_DAYS, dteDays), DTE_EXPONENT);
  return span * Math.min(DTE_MULT_CAP, mult);
}

function portfolioMinDteDays(legs: MarginLeg[]): number {
  return Math.max(DTE_FLOOR_DAYS, Math.min(...legs.map(l => l.T * 365)));
}

// ─── Public interfaces ────────────────────────────────────────────────────────

/** One leg as required by the margin engine. Built from StrategyLeg + chain data. */
export interface MarginLeg {
  strike: number;
  isCall: boolean;
  isBuy: boolean;
  qty: number;
  currentPrice: number; // USDT per contract (mark price from chain)
  iv: number;           // decimal, e.g. 0.60 for 60%
  T: number;            // years to expiry
  forward?: number;     // implied futures price (USDT/BTC) for this expiry,
                        // derived via put-call parity F = C − P + K.
                        // If absent or 0, BS scenarios fall back to spot.
}

export interface ScenarioInfo {
  priceShockPct: number; // e.g. -4.0 means spot −4%
  volShockPts: number;   // percentage points, e.g. +13.86 means IV +13.86pp
  pnl: number;           // USDT (negative = loss)
}

export interface MarginResult {
  portfolioMargin: number;           // USDT — max(riskMargin, marginFloor) × dteScaleApplied
  initialMargin: number;             // USDT — portfolioMargin (order-margin context)
  maintenanceMargin: number;         // USDT — 0.80 × initialMargin
  riskMargin: number;                // USDT — worst scenario loss (raw, pre-scale)
  marginFloor: number;               // USDT — minimum statutory floor (raw, pre-scale)
  preScaleMargin: number;            // USDT — max(risk, floor) before DTE scale
  dteScaleApplied: number;           // min(1, (T0/DTE)^p)
  ucf: number;                       // USDT — Unsettled Cashflows (signed: long bias > 0)
  bindingConstraint: 'risk_margin' | 'margin_floor';
  effectiveLeverage: number;         // totalNotional / portfolioMargin
  totalNotional: number;             // USDT — gross sum of all legs
  shortOptionsNotional: number;      // USDT — short legs only (drives OM%)
  omPctApplied: number;              // decimal, e.g. 0.005 for 0.5%
  totalPremiumCollected: number;     // USDT — net premium from SELL legs
  netDeltaBtc: number;               // portfolio delta in BTC
  marginPerLot: number;              // USDT — initialMargin / total lots
  worstScenario: ScenarioInfo;
  bestScenario: ScenarioInfo;
  priceShockApplied: number;         // decimal, e.g. 0.01 for 1%
  volUpApplied: number;              // DTE-adjusted IV up span, decimal
  volDownApplied: number;            // DTE-adjusted IV down span, decimal
  minDteDaysApplied: number;         // DTE used for vol adjustment
  skippedLegs: number;               // legs excluded because mark price = 0 (no IV data)
}

// ─── Main function ────────────────────────────────────────────────────────────

export function computePortfolioMargin(
  legs: MarginLeg[],
  spot: number,
  contractValue: number = CONTRACT_VALUE,
  skippedLegs = 0
): MarginResult | null {
  if (!legs.length || spot <= 0) return null;

  // Gross notional (all legs) — drives shock-span tier
  const totalNotional = legs.reduce(
    (sum, l) => sum + l.qty * contractValue * spot, 0
  );

  // Short-only notional — drives OM% for margin floor
  const shortOptionsNotional = legs.reduce(
    (sum, l) => sum + (l.isBuy ? 0 : l.qty * contractValue * spot), 0
  );

  // Continuous shock spans
  const { priceShock, volUp, volDown } = getShockParams(totalNotional);

  // DTE adjustment — portfolio-level using minimum DTE (most conservative)
  const minDteDays = portfolioMinDteDays(legs);
  const ivUp   = dteAdjust(volUp,   minDteDays);
  const ivDown = dteAdjust(volDown, minDteDays);

  // 29 scenarios:
  //   27 standard = 9 price steps × 3 vol scenarios (weight 1.0)
  //   2 extreme   = ±3× span with vol-up shock       (weight 1/3)
  const VOL_DELTAS = [-ivDown, 0, ivUp];
  const scenarios: { priceStep: number; volDelta: number; weight: number }[] = [];
  for (const ps of PRICE_STEPS) {
    for (const vd of VOL_DELTAS) {
      scenarios.push({ priceStep: ps, volDelta: vd, weight: 1.0 });
    }
  }
  scenarios.push({ priceStep: -EXTREME_PRICE_MULT, volDelta: ivUp, weight: EXTREME_WEIGHT });
  scenarios.push({ priceStep:  EXTREME_PRICE_MULT, volDelta: ivUp, weight: EXTREME_WEIGHT });

  let worstLoss = 0;
  let bestPnl = -Infinity;
  let worstScenario: ScenarioInfo = { priceShockPct: 0, volShockPts: 0, pnl: 0 };
  let bestScenario: ScenarioInfo  = { priceShockPct: 0, volShockPts: 0, pnl: 0 };

  for (const sc of scenarios) {
    let scenarioPnl = 0;
    for (const leg of legs) {
      // Use per-leg forward (PCP-derived) when available; else fall back to spot.
      const baseUnderlying = (leg.forward && leg.forward > 0) ? leg.forward : spot;
      const sUnder = baseUnderlying * (1 + sc.priceStep * priceShock);
      const ivSc   = Math.max(0.01, leg.iv + sc.volDelta);
      const scPrice = bsPrice(sUnder, leg.strike, leg.T, 0, ivSc, leg.isCall) * contractValue;
      const dir = leg.isBuy ? 1 : -1;
      scenarioPnl += dir * leg.qty * (scPrice - leg.currentPrice);
    }

    const weightedLoss = -scenarioPnl * sc.weight;

    if (weightedLoss > worstLoss) {
      worstLoss = weightedLoss;
      worstScenario = {
        priceShockPct: sc.priceStep * priceShock * 100,
        volShockPts:   sc.volDelta * 100,
        pnl:           scenarioPnl,
      };
    }
    if (scenarioPnl > bestPnl) {
      bestPnl = scenarioPnl;
      bestScenario = {
        priceShockPct: sc.priceStep * priceShock * 100,
        volShockPts:   sc.volDelta * 100,
        pnl:           scenarioPnl,
      };
    }
  }

  const riskMargin = Math.max(0, worstLoss);

  // Margin floor — different treatment for short vs long
  //   Short: max(5% × premium, OM% × notional)          — no cap
  //   Long:  min(premium, max(5% × premium, OM% × notional)) — capped at premium paid
  const omPct = getOmPct(shortOptionsNotional);
  let marginFloor = 0;
  let totalPremiumCollected = 0;

  for (const leg of legs) {
    const legNotional     = leg.qty * contractValue * spot;
    const legPremium      = leg.qty * leg.currentPrice;
    const baseFloor       = Math.max(FLOOR_PREMIUM_COEF * legPremium, omPct * legNotional);
    if (leg.isBuy) {
      marginFloor += Math.min(legPremium, baseFloor);
    } else {
      marginFloor += baseFloor;
      totalPremiumCollected += legPremium;
    }
  }

  // Order margin (what Delta charges when placing a trade) is just max(Risk, Floor).
  // The doc's `IM = max(Risk, Floor) − UCF` formula applies to the ONGOING portfolio
  // margin, where UCF is the running unrealised P&L. For a new order UCF = 0
  // (cash hasn't moved yet). Empirical validation against Delta's live order-margin
  // for OTM/ATM short strangles confirms: matches `max(Risk, Floor)` within ~10%
  // (Delta has a small commission/buffer on top), NOT `max(Risk, Floor) + premium`.
  //
  // We still surface a `ucf` field so callers can compute account-level IM/MM
  // for an existing portfolio if they want.
  let ucf = 0;
  for (const leg of legs) {
    const sign = leg.isBuy ? 1 : -1;
    ucf += sign * leg.qty * leg.currentPrice;
  }

  const preScaleMargin = Math.max(riskMargin, marginFloor);

  // Empirical DTE scale — corrects long-DTE over-estimate (see constants block).
  // The scale was calibrated against short strangles. Apply less aggressively
  // for other strategies:
  //   • Pure short multi-leg (strangle, straddle): full scale (calibrated)
  //   • Single short leg: 0.8× factor (no cross-leg netting → less reduction)
  //   • Protected (any long leg present): 0.5× factor (longs already cut risk)
  const nShort = legs.reduce((s, l) => s + (l.isBuy ? 0 : 1), 0);
  const nLong  = legs.reduce((s, l) => s + (l.isBuy ? 1 : 0), 0);
  let strategyFactor: number;
  if (nLong > 0)         strategyFactor = 0.7;
  else if (nShort === 1) strategyFactor = 0.9;
  else                   strategyFactor = 1.0;
  const rawDteScale   = Math.min(
    1.0,
    Math.pow(DTE_SCALE_T0 / Math.max(minDteDays, DTE_FLOOR_DAYS), DTE_SCALE_EXP)
  );
  const dteScaleApplied = 1.0 + (rawDteScale - 1.0) * strategyFactor;
  const portfolioMargin    = preScaleMargin * dteScaleApplied;
  const initialMargin      = portfolioMargin;          // order margin = pre-UCF
  const maintenanceMargin  = 0.80 * initialMargin;
  const bindingConstraint: 'risk_margin' | 'margin_floor' =
    riskMargin >= marginFloor ? 'risk_margin' : 'margin_floor';
  const effectiveLeverage  = portfolioMargin > 0 ? totalNotional / portfolioMargin : 0;

  // Portfolio delta in BTC (use per-leg forward when provided)
  const netDeltaBtc = legs.reduce((sum, leg) => {
    const u = (leg.forward && leg.forward > 0) ? leg.forward : spot;
    const delta = bsDelta(u, leg.strike, leg.T, 0, leg.iv > 0 ? leg.iv : 0.5, leg.isCall);
    return sum + (leg.isBuy ? 1 : -1) * leg.qty * contractValue * delta;
  }, 0);

  const totalLots   = legs.reduce((s, l) => s + l.qty, 0);
  const marginPerLot = totalLots > 0 ? portfolioMargin / totalLots : 0;

  return {
    portfolioMargin,
    initialMargin,
    maintenanceMargin,
    riskMargin,
    marginFloor,
    preScaleMargin,
    dteScaleApplied,
    ucf,
    bindingConstraint,
    effectiveLeverage,
    totalNotional,
    shortOptionsNotional,
    omPctApplied: omPct,
    totalPremiumCollected,
    netDeltaBtc,
    marginPerLot,
    worstScenario,
    bestScenario,
    priceShockApplied: priceShock,
    volUpApplied:   ivUp,
    volDownApplied: ivDown,
    minDteDaysApplied: minDteDays,
    skippedLegs,
  };
}

/**
 * Implied forward via put-call parity: F = C − P + K.
 * Both inputs in USDT/BTC. Returns 0 if either input is invalid.
 */
export function impliedForwardPCP(callMarkPerBtc: number, putMarkPerBtc: number,
                                   strike: number): number {
  if (!(callMarkPerBtc > 0) || !(putMarkPerBtc > 0)) return 0;
  return callMarkPerBtc - putMarkPerBtc + strike;
}

/**
 * Walk a chain and find the strike closest to spot where BOTH call and put
 * have positive marks. Compute F via PCP. Returns 0 if no usable strike.
 */
export function bestForwardFromChain(
  chain: { strike: number; call: { last_price: number }; put: { last_price: number } }[],
  spot: number,
): number {
  const candidates = chain.filter(
    r => (r.call?.last_price ?? 0) > 0 && (r.put?.last_price ?? 0) > 0,
  );
  if (!candidates.length) return 0;
  candidates.sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot));
  const best = candidates[0];
  return impliedForwardPCP(best.call.last_price, best.put.last_price, best.strike);
}

type ChainRow = { strike: number; call: { last_price: number; iv_pct: number }; put: { last_price: number; iv_pct: number } };

/**
 * Build MarginLeg[] from the strategy legs + per-expiry chain snapshots.
 * Each leg is looked up in the chain matching its OWN expiry, with its own T
 * and forward. This is essential when a strategy spans multiple expiries or
 * when the dashboard's selectedExpiry differs from the legs' expiry.
 *
 * Legs with mark_price = 0 (and therefore iv_pct = 0) are excluded — using a
 * fake IV fallback would produce meaningless margin numbers.
 */
export function buildMarginLegs(
  legs: { strike: number; type: 'CE' | 'PE'; action: 'BUY' | 'SELL'; qty: number; expiry: string }[],
  chainsByExpiry: Map<string, ChainRow[]>,
  spot: number,
  simulationTimestamp: number
): { marginLegs: MarginLeg[]; skippedLegs: number } {
  // Cache T and forward per expiry so repeated lookups are cheap.
  const tCache = new Map<string, number>();
  const fwdCache = new Map<string, number>();
  const tFor = (expiry: string): number => {
    const cached = tCache.get(expiry);
    if (cached !== undefined) return cached;
    const ts = expiry ? new Date(`${expiry}T12:00:00Z`).getTime() / 1000 : 0;
    const t = ts > simulationTimestamp ? (ts - simulationTimestamp) / (365 * 24 * 3600) : 0.00001;
    tCache.set(expiry, t);
    return t;
  };
  const fwdFor = (expiry: string, ch: ChainRow[]): number => {
    const cached = fwdCache.get(expiry);
    if (cached !== undefined) return cached;
    const f = bestForwardFromChain(ch, spot) || spot;
    fwdCache.set(expiry, f);
    return f;
  };

  let skippedLegs = 0;
  const marginLegs: MarginLeg[] = [];

  for (const leg of legs) {
    const ch = chainsByExpiry.get(leg.expiry) ?? [];
    const row = ch.find(r => r.strike === leg.strike);
    const side = leg.type === 'CE' ? row?.call : row?.put;
    const iv = (side?.iv_pct ?? 0) / 100;

    if (iv <= 0) {
      // mark_price = 0 at this timestamp (or wrong expiry chain) — skip rather
      // than fabricate an IV.
      skippedLegs++;
      continue;
    }

    marginLegs.push({
      strike: leg.strike,
      isCall: leg.type === 'CE',
      isBuy:  leg.action === 'BUY',
      qty:    leg.qty,
      // Parquet prices are USDT/BTC; convert to USDT/contract (= × 0.001)
      // so stress-test comparison with bsPrice(…) × contractValue is consistent
      currentPrice: (side?.last_price ?? 0) * CONTRACT_VALUE,
      iv,
      T:        tFor(leg.expiry),
      forward:  fwdFor(leg.expiry, ch),
    });
  }

  return { marginLegs, skippedLegs };
}
