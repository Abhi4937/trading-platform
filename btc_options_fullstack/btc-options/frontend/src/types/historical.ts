export interface HistoricalExpiryListResponse {
  expiries: string[]; // YYYY-MM-DD
}

export interface HistoricalOptionLeg {
  strike: number;
  last_price: number;
  iv_pct: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  open_interest?: number;
  oi_usd?: number;
}

export interface HistoricalChainRow {
  strike: number;
  is_atm: boolean;
  call: HistoricalOptionLeg;
  put: HistoricalOptionLeg;
}

export interface HistoricalOptionChainResponse {
  expiry: string;
  timestamp: number;
  atm_strike: number;
  spot_inferred: number;
  chain: HistoricalChainRow[];
}

export interface OHLCData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface HistoricalChartResponse {
  data: OHLCData[];
}

export interface OHLCWithGreeks extends OHLCData {
  spot: number;
  iv: number;
  rv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export interface ChartDataWithGreeksResponse {
  data: OHLCWithGreeks[];
}

export interface AtmIvPoint {
  time: number;
  atm_strike: number;
  atm_iv: number;        // percent
  rv: number;            // percent
  iv_minus_rv: number;   // percent (0 when rv unavailable)
}

export interface AtmIvSeriesResponse {
  data: AtmIvPoint[];
}

// ── Spot/leg OHLC for indicator chart ──────────────────────────────────────

export interface SpotOhlcBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface SpotOhlcResponse {
  data: SpotOhlcBar[];
}

// ── Indicator config + response ────────────────────────────────────────────

export type IndicatorType = 'sma' | 'ema' | 'rsi' | 'macd' | 'bbands' | 'atr' | 'vwap';

export interface IndicatorConfig {
  type: IndicatorType;
  params: Record<string, number>;
  color?: string;
}

export interface IndicatorValuePoint    { time: number; value: number; }
export interface IndicatorMacdPoint     { time: number; macd: number | null; signal: number | null; hist: number | null; }
export interface IndicatorBbandsPoint   { time: number; upper: number | null; mid: number | null; lower: number | null; }

export type IndicatorPoint = IndicatorValuePoint | IndicatorMacdPoint | IndicatorBbandsPoint;

export interface IndicatorsResponse {
  indicators: Record<string, IndicatorPoint[]>;
}

// ============================================================
// Vol Analytics panel — mirrors backend VolAnalyticsResponse.
// All vol numbers are PERCENT; ratios are bare numbers; pct fields are decimals.
// ============================================================
export interface VolSignal {
  level: string;   // rich | mildly_rich | fair | mildly_cheap | cheap | unknown
  action: string;
  color: string;   // red | orange | yellow | light_green | green | gray
}

export interface VolHeader {
  spot: number;
  atm_strike: number;
  atm_iv_call: number;   // %
  atm_iv_put: number;    // %
  atm_iv_avg: number;    // %
  dte_hours: number;
  primary_lookback: number;
  primary_estimator: string;
  primary_ratio: number;
  signal: VolSignal;
  regime_label: string;
  ts_used: number;     // unix secs the figures actually come from
  snapped: boolean;    // true when ts_used != requested timestamp (nearest-IV fallback)
}

// One row of the 5-estimator grid. RV grid: values are %. Ratio grid: bare IV/RV.
export interface VolGridRow {
  lookback: number;
  cc: number | null;
  co: number | null;
  parkinson: number | null;
  gk: number | null;
  rs: number | null;
}

export interface VolRegime {
  chop_ratio: number;
  trend_intensity: number;   // decimal (0.32 = 32%)
  chop_label: string;
  trend_label: string;
  recommended_estimator: string;
  rationale: string;
}

export interface VolTermStructure {
  shape: string;
  short_rv: number | null;   // %
  long_rv: number | null;    // %
  diff_pct: number | null;   // decimal
  interpretation: string;
}

export interface VolFriSatWindow {
  week_of: string;        // Friday date (YYYY-MM-DD)
  entry_ts: number;       // unix sec (Fri entry)
  exit_ts: number;        // unix sec (Sat exit)
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  n_candles: number;
  range_pct: number | null;   // decimal (high-low)/open
  co_pct: number | null;      // decimal |close-open|/open
  move_usd: number | null;    // signed close-open in $
}

export interface VolFriSat {
  window_count: number;
  median_range_pct: number | null;   // decimal
  median_co_pct: number | null;      // decimal
  median_move_usd: number | null;
  annualized_range_vol: number | null;  // %
  annualized_co_vol: number | null;      // %
  window_iv_rv: number | null;
  hold_hours: number;
  entry_hour_utc?: number;
  exit_hour_utc?: number;
  n_weeks?: number;
  windows?: VolFriSatWindow[];
}

export interface VolSLTier {
  pct: number;       // decimal
  dollars: number;
}

export interface VolSL {
  tight: VolSLTier;
  moderate: VolSLTier;
  conservative: VolSLTier;
  one_sigma_pct: number | null;
  one_sigma_dollars: number | null;
  min_reasonable_sl_pct: number | null;
  min_reasonable_sl_dollars: number | null;
  hold_hours: number;
}

export interface VolGammaTheta {
  gamma: number;
  theta_per_day: number;
  sigma_realized: number;        // %
  expected_daily_move_1sd: number;
  gamma_pnl_per_day_1sd: number;
  theta_pnl_per_day: number;
  ratio: number;
  verdict: string;
  breakeven_move_per_day: number;
}

export interface VolAnalyticsResponse {
  available: boolean;
  reason: string | null;
  expiry: string;
  timestamp: number;
  header: VolHeader;
  rv_grid: VolGridRow[];
  ratio_grid: VolGridRow[];
  regime: VolRegime | null;
  term_structure: VolTermStructure | null;
  fri_sat: VolFriSat | null;
  sl: VolSL | null;
  gamma_theta: VolGammaTheta | null;
}
