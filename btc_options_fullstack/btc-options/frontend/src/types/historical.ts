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
