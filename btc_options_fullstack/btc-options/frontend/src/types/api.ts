export interface OptionLeg {
  strike: number;
  expiry: string;
  option_type: 'call' | 'put';
  symbol: string;
  last_price: number;
  bid: number;
  ask: number;
  mid: number;
  volume: number;
  volume_usd: number;
  open_interest: number;
  oi_usd: number;
  iv: number;
  iv_pct: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
  price_bs: number;
  underlying_price: number;
  days_to_expiry: number;
  is_atm: boolean;
}

export interface ChainRow {
  strike: number;
  call: OptionLeg | null;
  put: OptionLeg | null;
  is_atm: boolean;
}

export interface OptionChainResponse {
  expiry: string;
  underlying: string;
  spot_price: number;
  atm_strike: number;
  days_to_expiry: number;
  atm_iv_call: number;
  atm_iv_put: number;
  chain: ChainRow[];
  fetched_at: string;
}

export interface ExpiryInfo {
  date: string;
  label: string;
  days: number;
}

export interface ExpiryListResponse {
  underlying: string;
  spot_price: number;
  expiries: ExpiryInfo[];
}

export interface SpotResponse {
  symbol: string;
  price: number;
  fetched_at: string;
}

export interface CandleBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PremiumChartResponse {
  symbol: string;
  strike: number;
  option_type: string;
  expiry: string;
  timeframe: string;
  candles: CandleBar[];
}

export interface IVSmilePoint {
  strike: number;
  call_iv: number;
  put_iv: number;
  delta: number;
  moneyness: number;
}

export interface IVSmileResponse {
  expiry: string;
  spot_price: number;
  atm_strike: number;
  atm_iv: number;
  points: IVSmilePoint[];
}

export interface IVRVPoint {
  label: string;
  implied_vol: number;
  realised_vol: number;
}

export interface IVRVResponse {
  expiry: string;
  window_days: number;
  atm_strike: number;
  series: IVRVPoint[];
}
