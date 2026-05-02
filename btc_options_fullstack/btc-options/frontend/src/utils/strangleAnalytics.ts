/**
 * Pure compute layer for strangle analytics — credit metrics, master ratios,
 * IVP-based decomposition, z-scores, quality score, hard-filter checks, and
 * Greek-based path attribution.
 *
 * No I/O. Same module is imported from HistoricalDashboard's StrategyPanel
 * and BacktestDashboard's per-trade detail panel — keeps the math in one
 * place and guarantees both views show identical numbers.
 *
 * Spec references: §7.8 (per-trade ratios), §8 (master ratio table), §9
 * (calibration / quality), §10 (trade card layout).
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface AnalyticsLeg {
  type: 'CE' | 'PE';
  side: 'BUY' | 'SELL';
  strike: number;
  qty: number;
  /** Mark in USD per contract (Delta India options). */
  mark: number;
  /** Decimal IV, e.g. 0.55 = 55%. */
  iv: number;
  delta: number;
  gamma: number;
  /** Per 1% IV move. */
  vega: number;
  /** Per calendar day. */
  theta: number;
}

export interface AnalyticsContext {
  ts_unix: number;
  spot: number;
  /** Days to expiry (fractional ok). */
  dte: number;
  expiry_ts: number;
  // M3 snapshot context (NaN when M3 row missing fields):
  atm_iv_7d: number;
  atm_iv_14d: number;
  atm_iv_30d: number;
  ivp_atm_7d_90d: number;
  ivp_4h: number;
  rv_7d: number;
  rv_14d: number;
  risk_reversal_25d: number;
  butterfly_25d: number;
  wing_atm_ratio: number;
  term_slope_7_30: number;
  rvp_4h: number;
  vrp_pct_7d: number;
  adx_14_4h: number;
  atr_pct_4h: number;
  pcr_oi: number;
  total_gex: number;
  gex_regime: string;
  pattern: 'A' | 'B' | 'C' | 'D' | 'Other' | string;
  expected_move_1sigma_7d: number;
}

export interface CalibrationBucket {
  source: 'specific_bucket' | 'universal_fallback';
  n_samples: number;
  credit_pct_median: number;
  credit_pct_mean: number;
  credit_pct_std: number;
  credit_pct_p25: number;
  credit_pct_p75: number;
  credit_pct_normalized_median: number;
  atm_iv_median: number | null;
  atm_iv_mean: number | null;
  atm_iv_std: number | null;
  structural_baseline: number | null;
  bucket: {
    dte_bucket: string;
    spot_bucket: string;
    delta_target: string;
    ivp_bucket: string;
  };
  pattern_distribution?: Record<string, number> | null;
}

export interface CreditMetrics {
  total_credit_usd: number;
  credit_pct: number;
  credit_pct_normalized: number;
  credit_per_day: number;
  annualized_credit_pct: number;
  /** Whether the position is net SHORT premium (we collected credit). */
  is_short_premium: boolean;
}

export interface MasterRatios {
  // Vol
  iv_rv_ratio_7d: number;
  term_ratio_7_30: number;
  skew_ratio_call_put: number;
  wing_atm_ratio: number;
  // Premium / risk
  credit_pct: number;
  credit_per_daily_theta: number;
  annualized_credit_pct: number;
  // Greeks
  theta_vega_ratio: number;
  gamma_theta_dollar: number;
  vega_credit_ratio: number;
  theta_credit_ratio: number;
  delta_credit_ratio: number;
  dollar_gamma: number;
  // Distance
  call_strike_sigma_dist: number;
  put_strike_sigma_dist: number;
  call_strike_atr_dist: number;
  put_strike_atr_dist: number;
  touch_prob_call: number;
  touch_prob_put: number;
  // Regime
  rvp_ivp_ratio: number;
  atr_compression: number;
  // Position-level Greeks
  position_delta: number;
  position_gamma: number;
  position_vega: number;
  position_theta: number;
}

export interface Decomposition {
  fair_credit_at_ivp_pct: number;
  structural_credit_pct: number;
  iv_regime_premium_pct: number;
  excess_over_fair_pct: number;
  // USD-denominated equivalents (× spot)
  fair_credit_at_ivp_usd: number;
  structural_credit_usd: number;
  iv_regime_premium_usd: number;
  excess_over_fair_usd: number;
  // Composition fractions of actual credit
  pct_from_structural: number;
  pct_from_iv_regime: number;
  pct_from_excess: number;
}

export interface ZScores {
  z_credit_pct: number;
  z_atm_iv: number;
  z_credit_pct_pct: number; // 0–100 percentile
  z_atm_iv_pct: number;     // 0–100 percentile
}

export type QualityBand = 'strong' | 'standard' | 'marginal' | 'skip';
export interface QualityScore {
  quality_score: number;        // 0–100
  size_band: QualityBand;
}

export interface HardFilters {
  ivp_above_50: boolean;
  iv_rv_spread_pos: boolean;
  adx_below_30: boolean;
  dte_in_range: boolean;
  gex_not_extreme: boolean;
  all_pass: boolean;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const CONTRACT_SIZE_BTC = 0.001;

const sumLegs = (legs: AnalyticsLeg[], f: (l: AnalyticsLeg) => number): number =>
  legs.reduce((acc, l) => acc + f(l), 0);

const dirSign = (l: AnalyticsLeg): number => (l.side === 'SELL' ? -1 : 1);

/** Standard normal CDF (Abramowitz-Stegun). */
function normCdf(x: number): number {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.sqrt(2);
  const t = 1 / (1 + p * z);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1 + sign * y);
}

/** Convert a z-score to a 0–100 percentile via the normal CDF. */
export function zToPercentile(z: number): number {
  if (!Number.isFinite(z)) return 50;
  return Math.max(0, Math.min(100, normCdf(z) * 100));
}

const safeDiv = (a: number, b: number): number =>
  Math.abs(b) < 1e-12 ? NaN : a / b;

// ── Core calculations ────────────────────────────────────────────────────────

/**
 * Detect whether `legs` form a recognizable strangle (1 CE + 1 PE on the same
 * side). Returns true for both short (sell+sell) and long (buy+buy).
 */
export function isStrangleLikeLegs(legs: AnalyticsLeg[]): boolean {
  if (legs.length !== 2) return false;
  const ce = legs.find(l => l.type === 'CE');
  const pe = legs.find(l => l.type === 'PE');
  if (!ce || !pe) return false;
  return ce.side === pe.side;
}

/**
 * Total premium (USD) and credit-% metrics. For SELL legs the credit is
 * positive (we receive); for BUY legs it's a debit (negative).
 */
export function computeCreditMetrics(
  legs: AnalyticsLeg[], ctx: AnalyticsContext,
): CreditMetrics {
  // Per-leg sign: SELL = +credit, BUY = -credit (we paid)
  const totalUsd = sumLegs(legs, l =>
    -dirSign(l) * l.mark * l.qty * CONTRACT_SIZE_BTC,
  );
  const credit_pct = ctx.spot > 0 ? totalUsd / ctx.spot : 0;
  const dteSafe = Math.max(ctx.dte, 1e-6);
  return {
    total_credit_usd: totalUsd,
    credit_pct,
    credit_pct_normalized: credit_pct / Math.sqrt(dteSafe),
    credit_per_day: credit_pct / dteSafe,
    annualized_credit_pct: credit_pct * (365 / dteSafe),
    is_short_premium: totalUsd > 0,
  };
}

function distInSigma(strike: number, ctx: AnalyticsContext): number {
  if (!Number.isFinite(ctx.expected_move_1sigma_7d) || ctx.expected_move_1sigma_7d <= 0) {
    return NaN;
  }
  return (strike - ctx.spot) / ctx.expected_move_1sigma_7d;
}

function distInAtr(strike: number, ctx: AnalyticsContext): number {
  // atr_pct_4h is in decimal fraction; ATR in $ ≈ spot * atr_pct
  if (!Number.isFinite(ctx.atr_pct_4h) || ctx.atr_pct_4h <= 0) return NaN;
  const atrUsd = ctx.spot * ctx.atr_pct_4h;
  return (strike - ctx.spot) / atrUsd;
}

/**
 * Spec §8 master ratio table. All "ratios" computed from the legs + Greeks +
 * M3 context. Position-level Greeks are signed (SELL legs contribute negatively).
 */
export function computeMasterRatios(
  legs: AnalyticsLeg[], ctx: AnalyticsContext, credit: CreditMetrics,
): MasterRatios {
  const pos_delta = sumLegs(legs, l => l.delta * dirSign(l) * l.qty);
  const pos_gamma = sumLegs(legs, l => l.gamma * dirSign(l) * l.qty);
  const pos_vega  = sumLegs(legs, l => l.vega  * dirSign(l) * l.qty);
  const pos_theta = sumLegs(legs, l => l.theta * dirSign(l) * l.qty);

  const ce = legs.find(l => l.type === 'CE');
  const pe = legs.find(l => l.type === 'PE');
  const credUsd = Math.abs(credit.total_credit_usd) > 1e-9
    ? Math.abs(credit.total_credit_usd) : 1e-9;

  return {
    // Vol
    iv_rv_ratio_7d: safeDiv(ctx.atm_iv_7d, ctx.rv_7d / 100),
    term_ratio_7_30: safeDiv(ctx.atm_iv_7d, ctx.atm_iv_30d),
    skew_ratio_call_put: ce && pe ? safeDiv(ce.iv, pe.iv) : NaN,
    wing_atm_ratio: ctx.wing_atm_ratio,
    // Premium / risk
    credit_pct: credit.credit_pct,
    credit_per_daily_theta: safeDiv(credUsd, Math.abs(pos_theta)),
    annualized_credit_pct: credit.annualized_credit_pct,
    // Greeks
    theta_vega_ratio: safeDiv(Math.abs(pos_theta), Math.abs(pos_vega)),
    gamma_theta_dollar: safeDiv(pos_gamma * ctx.spot * ctx.spot / 100, Math.abs(pos_theta)),
    vega_credit_ratio: safeDiv(pos_vega, credUsd),
    theta_credit_ratio: safeDiv(pos_theta, credUsd),
    delta_credit_ratio: safeDiv(pos_delta, credUsd),
    dollar_gamma: pos_gamma * ctx.spot * ctx.spot * 0.0001,
    // Distance
    call_strike_sigma_dist: ce ? distInSigma(ce.strike, ctx) : NaN,
    put_strike_sigma_dist:  pe ? distInSigma(pe.strike, ctx) : NaN,
    call_strike_atr_dist:   ce ? distInAtr(ce.strike, ctx)   : NaN,
    put_strike_atr_dist:    pe ? distInAtr(pe.strike, ctx)   : NaN,
    touch_prob_call: ce ? 2 * Math.abs(ce.delta) : NaN,
    touch_prob_put:  pe ? 2 * Math.abs(pe.delta) : NaN,
    // Regime
    rvp_ivp_ratio: safeDiv(ctx.rvp_4h, ctx.ivp_4h),
    atr_compression: ctx.atr_pct_4h,
    // Position Greeks
    position_delta: pos_delta,
    position_gamma: pos_gamma,
    position_vega:  pos_vega,
    position_theta: pos_theta,
  };
}

/**
 * Decompose actual credit% into structural + IV-regime uplift + excess
 * (Spec §9). All three pieces use the bucket's calibration data.
 */
export function computeDecomposition(
  credit: CreditMetrics, ctx: AnalyticsContext, calib: CalibrationBucket,
): Decomposition {
  const fair_pct = calib.credit_pct_median;
  const structural_pct = calib.structural_baseline ?? fair_pct * 0.7;
  const iv_regime_pct = fair_pct - structural_pct;
  const excess_pct = credit.credit_pct - fair_pct;

  const total = credit.credit_pct;

  return {
    fair_credit_at_ivp_pct: fair_pct,
    structural_credit_pct: structural_pct,
    iv_regime_premium_pct: iv_regime_pct,
    excess_over_fair_pct: excess_pct,
    fair_credit_at_ivp_usd: fair_pct * ctx.spot,
    structural_credit_usd: structural_pct * ctx.spot,
    iv_regime_premium_usd: iv_regime_pct * ctx.spot,
    excess_over_fair_usd: excess_pct * ctx.spot,
    pct_from_structural: safeDiv(structural_pct, total),
    pct_from_iv_regime: safeDiv(iv_regime_pct, total),
    pct_from_excess: safeDiv(excess_pct, total),
  };
}

/**
 * Z-scores for the actual credit_pct vs the bucket's mean/std, and for
 * ATM IV vs the bucket's median/std.
 */
export function computeZScores(
  credit: CreditMetrics, ctx: AnalyticsContext, calib: CalibrationBucket,
): ZScores {
  const z_credit_pct = calib.credit_pct_std > 0
    ? (credit.credit_pct - calib.credit_pct_mean) / calib.credit_pct_std : 0;
  const ivStd = calib.atm_iv_std ?? 0;
  const ivMed = calib.atm_iv_median ?? ctx.atm_iv_7d;
  const z_atm_iv = ivStd > 0 ? (ctx.atm_iv_7d - ivMed) / ivStd : 0;
  return {
    z_credit_pct,
    z_atm_iv,
    z_credit_pct_pct: zToPercentile(z_credit_pct),
    z_atm_iv_pct: zToPercentile(z_atm_iv),
  };
}

/**
 * Composite quality score (0–100) and size band.
 *
 * v1 formula: `0.40·z_credit_pct_pct + 0.60·IVP`. Drops the winner-z and
 * pattern-winrate terms from the convo's full formula because we don't yet
 * have trade outcomes to base them on — they get added in M5 once we ramp.
 */
export function computeQualityScore(
  z: ZScores, ctx: AnalyticsContext,
): QualityScore {
  const ivp = Number.isFinite(ctx.ivp_atm_7d_90d) ? ctx.ivp_atm_7d_90d : 50;
  const score = 0.40 * z.z_credit_pct_pct + 0.60 * ivp;
  let band: QualityBand;
  if (score >= 75) band = 'strong';
  else if (score >= 60) band = 'standard';
  else if (score >= 45) band = 'marginal';
  else band = 'skip';
  return { quality_score: score, size_band: band };
}

/**
 * Hard pre-entry filters from convo §16. All must pass for an A-grade entry.
 */
export function checkHardFilters(ctx: AnalyticsContext): HardFilters {
  const ivp_above_50 = Number.isFinite(ctx.ivp_4h) && ctx.ivp_4h > 50;
  const iv_rv_spread_pos = Number.isFinite(ctx.atm_iv_7d)
    && Number.isFinite(ctx.rv_7d)
    && ctx.atm_iv_7d > ctx.rv_7d / 100;
  const adx_below_30 = Number.isFinite(ctx.adx_14_4h) && ctx.adx_14_4h < 30;
  const dte_in_range = Number.isFinite(ctx.dte) && ctx.dte >= 5 && ctx.dte <= 14;
  const gex_not_extreme = ctx.gex_regime !== 'NEAR_FLIP';
  const all_pass = ivp_above_50 && iv_rv_spread_pos && adx_below_30
    && dte_in_range && gex_not_extreme;
  return { ivp_above_50, iv_rv_spread_pos, adx_below_30, dte_in_range,
           gex_not_extreme, all_pass };
}

// ── Path attribution (Greeks decomposition over a trade lifetime) ────────────

export interface PathPoint {
  ts: number;
  spot: number;
  atm_iv: number;
  position_delta: number;
  position_gamma: number;
  position_vega: number;
  position_theta: number;
  /** Realized P&L at this bar. Optional for the chart's ground truth line. */
  position_pnl?: number;
}

export interface PathAttribution {
  ts: number;
  delta_pnl: number;
  gamma_pnl: number;
  vega_pnl: number;
  theta_pnl: number;
}

/** Greeks-based PnL attribution between two consecutive path points. */
export function attributeBetween(prev: PathPoint, cur: PathPoint): PathAttribution {
  const dSpot = cur.spot - prev.spot;
  const dIv = cur.atm_iv - prev.atm_iv;
  const dT = (cur.ts - prev.ts) / 86400;
  return {
    ts: cur.ts,
    delta_pnl: prev.position_delta * dSpot,
    gamma_pnl: 0.5 * prev.position_gamma * dSpot * dSpot,
    vega_pnl:  prev.position_vega * dIv * 100,
    theta_pnl: prev.position_theta * dT,
  };
}

/** Cumulative attribution series, suitable for stacked-area charts. */
export function attributeCumulative(path: PathPoint[]): PathAttribution[] {
  if (path.length < 2) return [];
  const out: PathAttribution[] = [];
  let cdelta = 0, cgamma = 0, cvega = 0, ctheta = 0;
  for (let i = 1; i < path.length; i++) {
    const a = attributeBetween(path[i - 1], path[i]);
    cdelta += a.delta_pnl;
    cgamma += a.gamma_pnl;
    cvega  += a.vega_pnl;
    ctheta += a.theta_pnl;
    out.push({ ts: a.ts, delta_pnl: cdelta, gamma_pnl: cgamma,
               vega_pnl: cvega, theta_pnl: ctheta });
  }
  return out;
}

/** Identify the dominant Greek over the trade's lifetime (largest |cumulative|). */
export function dominantGreek(path: PathPoint[]): 'delta' | 'gamma' | 'vega' | 'theta' | null {
  const cum = attributeCumulative(path);
  if (!cum.length) return null;
  const last = cum[cum.length - 1];
  const items: [string, number][] = [
    ['delta', Math.abs(last.delta_pnl)],
    ['gamma', Math.abs(last.gamma_pnl)],
    ['vega',  Math.abs(last.vega_pnl)],
    ['theta', Math.abs(last.theta_pnl)],
  ];
  items.sort((a, b) => b[1] - a[1]);
  return items[0][0] as any;
}

// ── Combined façade for callers ──────────────────────────────────────────────

export interface FullAnalytics {
  credit: CreditMetrics;
  ratios: MasterRatios;
  decomposition: Decomposition | null;
  z: ZScores | null;
  quality: QualityScore | null;
  filters: HardFilters;
}

/**
 * One-call convenience: returns every metric for the panel. `calibration`
 * may be null/undefined — in that case decomposition / z / quality are
 * omitted (the panel renders a placeholder).
 */
export function computeAll(
  legs: AnalyticsLeg[],
  ctx: AnalyticsContext,
  calibration: CalibrationBucket | null | undefined,
): FullAnalytics {
  const credit = computeCreditMetrics(legs, ctx);
  const ratios = computeMasterRatios(legs, ctx, credit);
  const filters = checkHardFilters(ctx);
  if (!calibration) {
    return { credit, ratios, decomposition: null, z: null,
             quality: null, filters };
  }
  const decomposition = computeDecomposition(credit, ctx, calibration);
  const z = computeZScores(credit, ctx, calibration);
  const quality = computeQualityScore(z, ctx);
  return { credit, ratios, decomposition, z, quality, filters };
}
