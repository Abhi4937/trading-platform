/**
 * Delta Exchange Portfolio Margin Engine
 * Implements the 29-scenario stress test + margin floor approach.
 * All values in USDT. Pure computation — no I/O, no React dependencies.
 *
 * Approximate margin (within ~10-15% of Delta Exchange actual).
 * Exact tier parameters are not fully public.
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

function bsDelta(S: number, K: number, T: number, r: number, sigma: number, isCall: boolean): number {
  if (T <= 0 || sigma <= 0) return isCall ? (S >= K ? 1 : 0) : (S >= K ? 0 : -1);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  return isCall ? normCDF(d1) : normCDF(d1) - 1;
}

// ─── Notional tiers ───────────────────────────────────────────────────────────

interface Tier {
  maxNotional: number;
  priceShock: number; // decimal, e.g. 0.04 = ±4%
  volUp: number;      // percentage points added to IV, e.g. 15 = +15pp
  volDown: number;    // percentage points subtracted, e.g. 10 = -10pp
}

const TIERS: Tier[] = [
  { maxNotional:    50_000, priceShock: 0.04, volUp: 15, volDown: 10 },
  { maxNotional:   100_000, priceShock: 0.05, volUp: 18, volDown: 12 },
  { maxNotional:   250_000, priceShock: 0.06, volUp: 22, volDown: 15 },
  { maxNotional:   500_000, priceShock: 0.07, volUp: 27, volDown: 18 },
  { maxNotional: 1_000_000, priceShock: 0.08, volUp: 33, volDown: 22 },
  { maxNotional: 2_500_000, priceShock: 0.09, volUp: 38, volDown: 25 },
  { maxNotional:   Infinity, priceShock: 0.10, volUp: 45, volDown: 30 },
];

function getTier(totalNotional: number): Tier {
  return TIERS.find(t => totalNotional <= t.maxNotional) ?? TIERS[TIERS.length - 1];
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
}

export interface ScenarioInfo {
  priceShockPct: number; // e.g. -4.0 means spot −4%
  volShockPts: number;   // e.g. +15 means IV +15pp
  pnl: number;           // USDT (negative = loss)
}

export interface MarginResult {
  portfolioMargin: number;           // USDT — max(riskMargin, marginFloor)
  riskMargin: number;                // USDT — worst scenario loss
  marginFloor: number;               // USDT — minimum statutory floor
  bindingConstraint: 'risk_margin' | 'margin_floor';
  effectiveLeverage: number;         // totalNotional / portfolioMargin
  totalNotional: number;             // USDT — sum of abs(qty × cv × spot)
  totalPremiumCollected: number;     // USDT — net premium from SELL legs
  netDeltaBtc: number;               // portfolio delta in BTC
  marginPerLot: number;              // USDT — portfolioMargin / total lots
  worstScenario: ScenarioInfo;
  bestScenario: ScenarioInfo;
  priceShockApplied: number;         // tier price shock, e.g. 0.04
  volUpApplied: number;              // tier vol up, pp
  volDownApplied: number;            // tier vol down, pp
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

  // Total notional
  const totalNotional = legs.reduce(
    (sum, l) => sum + l.qty * contractValue * spot, 0
  );

  const tier = getTier(totalNotional);
  const { priceShock, volUp, volDown } = tier;

  // 29 scenarios:
  // 7 price steps × 3 vol scenarios = 21 standard
  // + 2 extreme (full shock ±, worst vol, weighted 35%)
  const PRICE_STEPS = [-1, -2 / 3, -1 / 3, 0, 1 / 3, 2 / 3, 1] as const;
  type VolScenario = { deltaVol: number };
  const VOL_SCENARIOS: VolScenario[] = [
    { deltaVol: -volDown / 100 },
    { deltaVol: 0 },
    { deltaVol:  volUp  / 100 },
  ];

  const scenarios: { priceStep: number; volDelta: number; weight: number }[] = [];
  for (const ps of PRICE_STEPS) {
    for (const vs of VOL_SCENARIOS) {
      scenarios.push({ priceStep: ps, volDelta: vs.deltaVol, weight: 1.0 });
    }
  }
  // Extreme scenarios — full shock in both directions with vol up, 35% weight
  scenarios.push({ priceStep: -1, volDelta: volUp / 100, weight: 0.35 });
  scenarios.push({ priceStep:  1, volDelta: volUp / 100, weight: 0.35 });

  let worstLoss = 0;
  let bestPnl = -Infinity;
  let worstScenario: ScenarioInfo = { priceShockPct: 0, volShockPts: 0, pnl: 0 };
  let bestScenario: ScenarioInfo  = { priceShockPct: 0, volShockPts: 0, pnl: 0 };

  for (const sc of scenarios) {
    const sSpot = spot * (1 + sc.priceStep * priceShock);

    let scenarioPnl = 0;
    for (const leg of legs) {
      const ivSc = Math.max(0.01, leg.iv + sc.volDelta);
      // bsPrice returns USDT/BTC; multiply by contractValue to get USDT/contract
      const scPrice = bsPrice(sSpot, leg.strike, leg.T, 0, ivSc, leg.isCall) * contractValue;
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

  // Margin floor: max(5% × premium, 1% × notional) per leg, summed across all legs
  const OM_PCT = 0.01;
  let marginFloor = 0;
  let totalPremiumCollected = 0;

  for (const leg of legs) {
    const legNotional = leg.qty * contractValue * spot;
    const legPremiumTotal = leg.qty * leg.currentPrice;
    marginFloor += Math.max(0.05 * legPremiumTotal, OM_PCT * legNotional);
    if (!leg.isBuy) totalPremiumCollected += legPremiumTotal;
  }

  const portfolioMargin = Math.max(riskMargin, marginFloor);
  const bindingConstraint: 'risk_margin' | 'margin_floor' =
    riskMargin >= marginFloor ? 'risk_margin' : 'margin_floor';
  const effectiveLeverage = portfolioMargin > 0 ? totalNotional / portfolioMargin : 0;

  // Portfolio delta in BTC
  const netDeltaBtc = legs.reduce((sum, leg) => {
    const delta = bsDelta(spot, leg.strike, leg.T, 0, leg.iv > 0 ? leg.iv : 0.5, leg.isCall);
    return sum + (leg.isBuy ? 1 : -1) * leg.qty * contractValue * delta;
  }, 0);

  const totalLots = legs.reduce((s, l) => s + l.qty, 0);
  const marginPerLot = totalLots > 0 ? portfolioMargin / totalLots : 0;

  return {
    portfolioMargin,
    riskMargin,
    marginFloor,
    bindingConstraint,
    effectiveLeverage,
    totalNotional,
    totalPremiumCollected,
    netDeltaBtc,
    marginPerLot,
    worstScenario,
    bestScenario,
    priceShockApplied: priceShock,
    volUpApplied: volUp,
    volDownApplied: volDown,
    skippedLegs,
  };
}

/**
 * Build MarginLeg[] from the strategy legs + current chain snapshot.
 * Legs with mark_price = 0 (and therefore iv_pct = 0) are excluded — using a
 * fake IV fallback would produce meaningless margin numbers.
 * Returns both the valid legs and a count of how many were skipped.
 */
export function buildMarginLegs(
  legs: { strike: number; type: 'CE' | 'PE'; action: 'BUY' | 'SELL'; qty: number; expiry: string }[],
  chain: { strike: number; call: { last_price: number; iv_pct: number }; put: { last_price: number; iv_pct: number } }[],
  spot: number,
  selectedExpiry: string,
  simulationTimestamp: number
): { marginLegs: MarginLeg[]; skippedLegs: number } {
  // Expiry settlement: 12:00 UTC on expiry date (= 5:30 PM IST)
  const expiryTs = selectedExpiry
    ? new Date(`${selectedExpiry}T12:00:00Z`).getTime() / 1000
    : 0;
  const T = expiryTs > simulationTimestamp
    ? (expiryTs - simulationTimestamp) / (365 * 24 * 3600)
    : 0.00001;

  let skippedLegs = 0;
  const marginLegs: MarginLeg[] = [];

  for (const leg of legs) {
    const row = chain.find(r => r.strike === leg.strike);
    const side = leg.type === 'CE' ? row?.call : row?.put;
    const iv = (side?.iv_pct ?? 0) / 100;

    if (iv <= 0) {
      // mark_price = 0 at this timestamp — no valid IV, skip rather than fabricate
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
      T,
    });
  }

  return { marginLegs, skippedLegs };
}
