/**
 * Slippage model v2 — DATA-FIT from 159 historical SELL fills.
 *
 * NOT YET WIRED INTO THE APP. Lives side-by-side with the production
 * `slippage.ts` so we can compare both models against real fills before
 * deciding whether to swap. See the `Back Testing/` folder:
 *   - extract_fills.py            — built fills_with_features.csv
 *   - fit_slippage.py             — fit constants below
 *   - slippage_calibration.json   — raw output (single source of truth)
 *   - slippage_comparison.py      — side-by-side report vs current model
 *
 * Fitted on 159 SELL fills (Dec 2025 → Apr 2026), after dropping fills whose
 * intra-minute mark range exceeded 2× median (noisy minutes where 1-min mark
 * is unreliable). Negative-slip fills (price improvement) kept as-is; objective
 * was median absolute residual.
 *
 *   slip_$/BTC = ( FIXED_USD / qty_btc                  // one-tick crossing
 *               + LINEAR_PER_BTC × dte_factor(dte) )    // depth-eating term
 *               × hour_factor(hour_ist)                 // session multiplier
 *               × weekend_factor(is_weekend)
 *
 * Fit summary:
 *   median |residual| = $3.58 / BTC
 *   MAE              = $6.05 / BTC
 *
 * Headline differences vs current `slippage.ts`:
 *   - FIXED_USD ≈ $0.10 ✓ (matches Delta India tick size — coincidence-confirmed)
 *   - LINEAR_PER_BTC ≈ $3.81 ✓ (vs current $3.85 — already well-calibrated)
 *   - DTE NOW MATTERS:  intraday 0.53× / weekly 1.13× / far 1.52×
 *   - HOUR is ~flat:    night_us 1.07 / asia 1.04 / india 0.95 / eu_us 0.96
 *   - WEEKEND ≈ 1.0:    no detectable weekend penalty in our trade sample
 *
 * Mirror of the production API: same SlippageInputs, same exported helpers.
 */

export type SlippageMode = 'flat' | 'smart';

export interface SlippageInputs {
  spot: number;
  markClose: number;
  dte: number;
  strike: number;
  isCall: boolean;
  hourIst: number;
  isWeekend?: boolean;
  oiClose?: number;
  qty?: number;
}

// ── Fitted constants (from slippage_calibration.json — DO NOT hand-tune) ───
const FIXED_USD       = 0.0983;
const LINEAR_PER_BTC  = 3.8145;

// DTE buckets
const DTE_INTRADAY    = 0.5255;   // dte < 1 day
const DTE_WEEKLY      = 1.1270;   // 1 <= dte < 7
const DTE_FAR         = 1.5207;   // dte >= 7

// Hour buckets (IST)
const HOUR_NIGHT_US   = 1.0712;   // 0..5  (US afternoon → late evening)
const HOUR_ASIA       = 1.0410;   // 6..11 (Asia waking)
const HOUR_INDIA      = 0.9533;   // 12..16 (India active, light)
const HOUR_EU_US_OPEN = 0.9622;   // 17..23 (London/NY — user's main window)

const WEEKEND_MULT    = 1.0249;

// Bounds — keep loose; the fit handles the dynamic range, but guard against
// pathological inputs and keep the stress-test multiplier well-behaved.
const FLOOR_USD       = 0.05;     // minimum $/contract
const CAP_FRAC        = 0.30;     // hard ceiling at 30% of mark
const CAP_USD         = 50;       // absolute floor on the cap


function dteFactor(dte: number): number {
  if (dte < 1)  return DTE_INTRADAY;
  if (dte < 7)  return DTE_WEEKLY;
  return DTE_FAR;
}

function hourFactor(h: number): number {
  if (h >= 0  && h < 6)  return HOUR_NIGHT_US;
  if (h >= 6  && h < 12) return HOUR_ASIA;
  if (h >= 12 && h < 17) return HOUR_INDIA;
  return HOUR_EU_US_OPEN;
}


/**
 * Raw heuristic slippage estimate in USDT/BTC — multiplicative model only,
 * NO floor/cap applied. Bounds are applied by computeSlippagePerSide so they
 * scale with the user's stress-test multiplier.
 */
export function estimateSlippage(inp: SlippageInputs): number {
  const { markClose, hourIst, isWeekend, qty, dte } = inp;
  if (markClose <= 0) return 0;

  const qtyBtc = Math.max(0.001, (qty ?? 200) * 0.001);
  const fixed  = FIXED_USD / qtyBtc;
  const linear = LINEAR_PER_BTC * dteFactor(dte);
  const base   = fixed + linear;
  const mult   = hourFactor(hourIst) * (isWeekend ? WEEKEND_MULT : 1.0);
  return base * mult;
}

/**
 * UI helper — resolves the per-side slippage in $/contract given the user's
 * mode selection. Returns 0 when slippage is disabled.
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

export function roundTripCost(perSide: number): number {
  return 2 * perSide;
}

export function istHour(unixSec: number): number {
  const ist = new Date((unixSec + 5.5 * 3600) * 1000);
  return ist.getUTCHours();
}

export function isWeekendIst(unixSec: number): boolean {
  const ist = new Date((unixSec + 5.5 * 3600) * 1000);
  const dow = ist.getUTCDay();
  return dow === 0 || dow === 6;
}

export function dteFromEntry(entryUnixSec: number, expiryYmd: string): number {
  const [y, m, d] = expiryYmd.split('-').map(Number);
  const settleUtc = Date.UTC(y, (m ?? 1) - 1, d ?? 1, 12, 0) / 1000;
  return Math.max(0, Math.round((settleUtc - entryUnixSec) / 86400));
}
