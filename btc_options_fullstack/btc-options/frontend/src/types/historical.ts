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
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export interface ChartDataWithGreeksResponse {
  data: OHLCWithGreeks[];
}
