/**
 * Slippage model — % of premium with moneyness, hour, and weekend multipliers.
 *
 * Per-side entry slippage in $/contract for MARKET ORDERS on Delta India BTC
 * options. Apply on BOTH entry and exit for round-trip cost.
 *
 *   slippage = base_pct × mark × moneyness_mult × hour_mult × weekend_mult
 *
 * Why %-of-premium (not the previous DTE/moneyness heuristic):
 *   1. Auto-scales with mark — deep-OTM cheap legs naturally pay less absolute slip,
 *      near-ATM expensive legs naturally pay more. Captures liquidity without bucket math.
 *   2. One number to recalibrate (`BASE_PCT`); just take the median of `slip / mark`
 *      across new fills.
 *   3. IV regime is implicit — if IV doubles, both mark and slip double in tandem.
 *
 * Calibrated against ~25 fills + the 7 short-strangle entries below + a 280-lot
 * ATM straddle round-trip on 2025-12-27 (round-trip slip 0.7%/mark). The observed
 * `slip / mark` ratio across the sample clusters in the 0.3%–1.6% range. We use
 * 0.5% as the active-hour baseline, then multipliers stretch up to ~2% for the
 * extreme case (deep wings + dead-hour + weekend). The original 1.2%/2.5×/1.3×
 * over-predicted by ~4× for short-dated ATM trades because BTC books are global
 * 24/7 — IST-centric dead-hour penalty doesn't really apply.
 *
 * Per-trade error remains ±50%. The sensitivity table in the UI shows P&L at
 * 0.5×/1×/1.5×/2×/3× to make that uncertainty explicit.
 *
 * If/when a backend simulation is added, mirror this in
 * backend/app/services/slippage.py — keep both in sync.
 *
 * ─── Calibration table (7 short-strangle trades, observed UPL@offer per side) ──
 *
 *   #  Date        Time   DTE  Spot     Strike   OTM%    Side  Slip $  slip/mark
 *   1  2026-03-07  12:20    6  68,200   PE 61k   10.6%   put   −0.25   ~0.27%
 *                                       CE 75k   10.0%   call  −0.70   ~0.78%
 *   2  2026-02-23  21:15    2  67,100   PE 64k    4.6%   put   −0.49   ~0.82%
 *                                       CE 67.6k  0.7%   call  −0.70   ~0.28%
 *   3  2026-02-21  01:45    2  69,600   PE 65.2k  6.3%   put   −0.79   ~1.58%
 *                                       CE 67.6k  2.9%   call  −1.20   ~0.60%
 *   4  2026-02-28  00:06    3  ~67k     PE 62k   ~7.5%   put   −1.17   (dead+wknd)
 *                                       CE 69.5k ~3.7%   call  −0.45
 *   5  same entry as #4 (split fill)             put   −1.56          call  −0.65
 *   6  same entry as #4 (split fill)             put   −0.47          call  −0.59
 *   7  2026-04-03  23:00    7  ~65k     PE 59k   ~9.2%   put   −0.53
 *                                       CE 74k  ~13.8%   call  −0.60
 *
 * Cross-check vs user's reported 1000-lot real-fill ~$10 entry:
 *   strangle premium ~$600/BTC → BASE_PCT 1.2% × $600 × 1 BTC = $7.20
 *   with moneyness multipliers (~1.2 avg) → ~$8.64
 *   with hour mult 1.0 (active) → ~$8.64
 *   ≈ user's $10 (within ±20%). ✓
 */

export type SlippageMode = 'flat' | 'smart';

export interface SlippageInputs {
  spot: number;        // BTC spot at entry
  markClose: number;   // mark price of the option at entry (the leg's entryPremium)
  dte: number;         // kept for API compat — not used in %-of-premium model
  strike: number;
  isCall: boolean;     // kept for API compat — skew handled by mark price itself
  hourIst: number;     // 0..23, IST hour-of-day at entry
  isWeekend?: boolean; // Sat or Sun by IST date
  oiClose?: number;    // kept for API compat — chain OI not currently surfaced
  qty?: number;        // contracts (lots) — used for impact-cost scaling. Default 200 (impact size).
}

// ── Tunable constants ──────────────────────────────────────────────────────
// Recalibrated 2026-04-28 with sub-linear quantity scaling, motivated by
// Delta India's Impact Size = 200 contracts framework. Slip has a fixed
// "spread crossing" component plus a linear per-BTC term, so the per-BTC
// rate decreases with qty (matches user's observed sub-linear cost):
//
//   slip_$ = (MIN_SPREAD + PER_BTC × qty_btc) × moneyness × hour × weekend
//
// Calibration: linear $1 per 100 lots NET (PE+CE strangle), plus a one-tick
// fixed crossing cost — Delta India BTC option tick size is $0.10, which is the
// minimum spread you can cross. This adds ~$0.26 to a strangle's net slip across
// all qty (small perturbation off pure linear scaling, but more realistic for
// very small trades).
//   At 7%OTM active-hours weekday with MIN=$0.10, PER=$3.85:
//     100-lot strangle NET ≈ $1.26
//     700-lot strangle NET ≈ $7.27
//     1000-lot strangle NET ≈ $10.27
const MIN_SPREAD_USD = 0.10;    // fixed cost to cross one tick of spread, per side
const PER_BTC_BASE   = 3.85;    // marginal slip per BTC of size, active hours
const BASE_PCT       = 0.012;   // 1.2% of mark — overrides floor for very expensive legs
const FLOOR_USD        = 0.05;  // minimum $/contract (avoids hard zero on cheap legs)
const CAP_FRAC         = 0.30;  // hard ceiling at 30% of mark (tail-risk bound)
const CAP_USD          = 50;    // absolute floor on the cap

/**
 * Raw heuristic slippage estimate in USDT/BTC — multiplicative model only,
 * NO floor/cap applied. Bounds are applied by computeSlippagePerSide so they
 * scale with the user's stress-test multiplier.
 */
export function estimateSlippage(inp: SlippageInputs): number {
  const { markClose, hourIst, isWeekend, qty } = inp;
  if (markClose <= 0) return 0;

  // ── Moneyness multiplier — REMOVED 2026-04-30 per user calibration.
  // Wings DON'T get extra slip in user's experience: deep-OTM CE 78.5k @ 700 lots
  // observed $2 actual vs old model (mult 2.0) producing $5.59. Mark-proportional
  // BASE_PCT term still differentiates expensive ATM legs from cheap wings naturally.
  const moneynessMult = 1.0;

  // ── Hour multiplier (IST) — softened 2026-04-28: BTC books are global, so
  // 00–06 IST is US afternoon, not "dead". Penalty kept but reduced.
  let hourMult: number;
  if      (hourIst >= 0 && hourIst < 6) hourMult = 1.1;  // overnight IST = US afternoon
  else if (hourIst >= 6 && hourIst < 9) hourMult = 1.05; // Asia waking up
  else                                  hourMult = 1.0;  // active (incl. evening US/EU)

  // ── Weekend multiplier — thin books on Sat/Sun across all hours ──
  const weekendMult = isWeekend ? 1.05 : 1.0;

  // Convert per-side fixed + linear cost to a per-BTC equivalent so the
  // outer caller's `* qty * 0.001` arithmetic still works:
  //   total_slip_$ = (MIN_SPREAD + PER_BTC × qty_btc) × multipliers
  //   per_btc_slip = total_slip_$ / qty_btc
  //                = (MIN_SPREAD/qty_btc + PER_BTC) × multipliers
  // For the high-mark cap, use the legacy mark% as an alternative lower bound.
  const qtyBtc = Math.max(0.001, (qty ?? 200) * 0.001);
  const additivePerBtc = MIN_SPREAD_USD / qtyBtc + PER_BTC_BASE;
  const markPctPerBtc  = BASE_PCT * markClose;
  const baseSlipPerBtc = Math.max(additivePerBtc, markPctPerBtc);

  return baseSlipPerBtc * moneynessMult * hourMult * weekendMult;
}

/**
 * UI helper — resolves the per-side slippage in $/contract given the user's
 * mode selection. Returns 0 when slippage is disabled.
 *
 * The multiplier scales BOTH the raw estimate AND the floor/cap bounds, so the
 * sensitivity stress test produces linear response across the whole range.
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
  const floor  = FLOOR_USD * multiplier;
  const cap    = Math.max(CAP_USD, CAP_FRAC * smartInputs.markClose) * multiplier;
  return Math.min(cap, Math.max(floor, scaled));
}

/** Convenience — round-trip cost in $/contract (entry + exit). */
export function roundTripCost(perSide: number): number {
  return 2 * perSide;
}

/** Unix seconds (UTC) → IST hour-of-day (0..23). */
export function istHour(unixSec: number): number {
  const ist = new Date((unixSec + 5.5 * 3600) * 1000);
  return ist.getUTCHours();
}

/** Unix seconds (UTC) → true if the IST calendar date falls on Sat or Sun. */
export function isWeekendIst(unixSec: number): boolean {
  const ist = new Date((unixSec + 5.5 * 3600) * 1000);
  const dow = ist.getUTCDay(); // 0=Sun .. 6=Sat
  return dow === 0 || dow === 6;
}

/** Days to expiry (rounded) from entry to settlement (12:00 UTC = 5:30 PM IST). */
export function dteFromEntry(entryUnixSec: number, expiryYmd: string): number {
  const [y, m, d] = expiryYmd.split('-').map(Number);
  const settleUtc = Date.UTC(y, (m ?? 1) - 1, d ?? 1, 12, 0) / 1000;
  return Math.max(0, Math.round((settleUtc - entryUnixSec) / 86400));
}
