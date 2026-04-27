/**
 * Slippage model — per-side entry slippage in $/contract for MARKET ORDERS
 * on Delta India BTC options. Apply on BOTH entry and exit for round-trip.
 *
 * Calibrated against ~25 fills (Feb-Apr 2026) plus the 7 short-strangle entries below.
 * Expected per-trade error: ±50%. Use the multiplier slider in the UI to scenario-test.
 *
 * If/when the backend gains a server-side simulation, mirror this logic in
 * backend/app/services/slippage.py and keep both in sync (or refactor to
 * fetch via an /estimate-slippage endpoint).
 *
 * ─── Calibration table (7 short-strangle trades, observed UPL@offer per side) ─
 *
 *   #  Date        Time   DTE  Spot     Strike   OTM%    Side   Observed
 *   1  2026-03-07  12:20    6  68,200   PE 61k   10.6%   put    -0.25
 *                                       CE 75k   10.0%   call   -0.70
 *   2  2026-02-23  21:15    2  67,100   PE 64k    4.6%   put    -0.49
 *                                       CE 67.6k  0.7%   call   -0.70
 *   3  2026-02-21  01:45    2  69,600   PE 65.2k  6.3%   put    -0.79
 *                                       CE 67.6k  2.9%   call   -1.20
 *   4  2026-02-28  00:06    3  ~67k     PE 62k    7.5%*  put    -1.17  (split fill)
 *                                       CE 69.5k  3.7%*  call   -0.45  (split fill)
 *   5  same entry as #4, larger chunk          put    -1.56  call   -0.65
 *   6  same entry as #4, smaller chunk         put    -0.47  call   -0.59
 *   7  2026-04-03  23:00    7  ~65k     PE 59k    9.2%*  put    -0.53
 *                                       CE 74k   13.8%*  call   -0.60
 *
 *   * spot estimated from market context (not stored at fill time).
 *
 * Known weak spots (to fix in future calibration):
 *   - 0–1 DTE OTM puts can be wider than predicted
 *   - Specific weekly expiries (e.g., May 8) have thinner liquidity than average
 *   - Round-number strikes during peak liquidity can be tighter than predicted
 */

export type SlippageMode = 'flat' | 'smart';

export interface SlippageInputs {
  spot: number;        // BTC spot at entry
  markClose: number;   // mark price of the option at entry (the leg's entryPremium)
  dte: number;         // days to expiry, rounded
  strike: number;
  isCall: boolean;
  hourIst: number;     // 0..23, IST hour-of-day at entry
  oiClose?: number;    // open interest in BTC contracts at entry (optional)
}

/**
 * Raw heuristic slippage estimate in $/contract — multiplicative model only,
 * NO floor/cap applied. The bounds are applied by computeSlippagePerSide so
 * they scale with the user's sensitivity multiplier (see below).
 *
 * Apply on BOTH entry and exit for round-trip cost.
 * Returned value is always positive.
 */
export function estimateSlippage(inp: SlippageInputs): number {
  const { spot, dte, strike, isCall, hourIst, oiClose } = inp;
  const moneyness = Math.abs(strike - spot) / spot;

  // ── DTE base (recalibrated 2026-04 against 7-trade table above) ──
  let base: number;
  if      (dte <= 2)  base = 0.5;
  else if (dte <= 7)  base = 0.6;
  else if (dte <= 14) base = 1.0;
  else if (dte <= 21) base = 1.5;
  else                base = 2.5;

  // ── Moneyness multiplier (DTE-dependent) ──
  let moneyMult: number;
  if (dte <= 2) {
    // Short-DTE: near-ATM has gamma penalty
    if      (moneyness < 0.02) moneyMult = 2.5;
    else if (moneyness < 0.05) moneyMult = 2.0;
    else if (moneyness < 0.10) moneyMult = 1.3;
    else                       moneyMult = 1.5;
  } else {
    if      (moneyness < 0.02) moneyMult = 0.7;
    else if (moneyness < 0.05) moneyMult = 1.0;
    else if (moneyness < 0.10) moneyMult = 1.3;
    else if (moneyness < 0.15) moneyMult = 1.6;
    else if (moneyness < 0.20) moneyMult = 2.0;
    else                       moneyMult = 2.5;
  }

  // ── Hour multiplier (IST). Dead hours 00:00–09:00 IST are slightly wider. ──
  const hourMult = (hourIst >= 0 && hourIst < 9) ? 1.10 : 0.95;

  // ── OI multiplier (if provided) ──
  let oiMult = 1.0;
  if (oiClose !== undefined) {
    if      (oiClose > 200) oiMult = 1.0;
    else if (oiClose > 50)  oiMult = 1.2;
    else if (oiClose > 10)  oiMult = 1.5;
    else                    oiMult = 2.0;
  }

  // Calls run slightly wider in the observed sample.
  const sideMult = isCall ? 1.05 : 1.0;

  return base * moneyMult * hourMult * oiMult * sideMult;
}

/**
 * UI helper — resolves the per-side slippage in $/contract given the user's
 * mode selection. Returns 0 when slippage is disabled.
 *
 * The multiplier scales BOTH the raw estimate AND the floor/cap bounds, so
 * dragging the slider produces linear response across the whole range
 * (otherwise legs sitting on the floor wouldn't move with the slider).
 */
export function computeSlippagePerSide(
  enabled: boolean,
  mode: SlippageMode,
  flatValue: number,
  multiplier: number,
  smartInputs: SlippageInputs | null,
): number {
  if (!enabled) return 0;
  if (mode === 'flat') return Math.max(0, flatValue * multiplier);
  if (!smartInputs) return 0;

  const scaled = estimateSlippage(smartInputs) * multiplier;
  const floor  = Math.max(0.05, 0.005 * smartInputs.markClose) * multiplier;
  const cap    = Math.max(50,   0.10  * smartInputs.markClose) * multiplier;
  return Math.min(cap, Math.max(floor, scaled));
}

/**
 * Convenience — round-trip cost in $ (per contract, both legs of the cost).
 * Multiply by qty * 0.001 (BTC per contract) for actual P&L impact.
 */
export function roundTripCost(perSide: number): number {
  return 2 * perSide;
}

/**
 * Convert a unix timestamp (seconds, UTC) to its IST hour-of-day (0..23).
 */
export function istHour(unixSec: number): number {
  const ist = new Date((unixSec + 5.5 * 3600) * 1000);
  return ist.getUTCHours();
}

/**
 * Days to expiry (rounded) from entry time to settlement (12:00 UTC = 5:30 PM IST).
 */
export function dteFromEntry(entryUnixSec: number, expiryYmd: string): number {
  const [y, m, d] = expiryYmd.split('-').map(Number);
  const settleUtc = Date.UTC(y, (m ?? 1) - 1, d ?? 1, 12, 0) / 1000;
  return Math.max(0, Math.round((settleUtc - entryUnixSec) / 86400));
}
