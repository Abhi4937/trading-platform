// M7 — Friday→Saturday strangle/straddle sweep types.
// Mirror of backend/app/api/m7_results.py response shapes.

export interface M7ExitRule {
  fixed_exit_ts?: number | null;
  fixed_exit_hour_ist?: number | null;  // e.g. 10 = Sat 10:00 IST, 17.5 = Sat 17:30 IST
  max_profit_pct?: number | null;
  margin_target_pct?: number | null;
  premium_sl_pct?: number | null;
}

export interface M7Filters {
  delta_target?: string;          // comma-separated values
  is_straddle?: string;           // "true" / "false"
  expiry_date?: string;
  entry_atm_iv_band?: string;     // e.g. "20-30,30-40"
  entry_hour_ist?: string;
  dte_bucket?: string;
  spot_bucket?: string;
  ivp_bucket?: string;
  ctx_pattern?: string;
  ctx_gex_regime?: string;
  friday_date_ist?: string;
}

export interface M7Summary {
  n_trades: number;
  n_wins: number;
  win_rate: number;
  avg_net_pnl_usd: number;
  total_net_pnl_usd: number;
  avg_gross_pnl_usd: number;
  avg_credit_usd: number;
  avg_margin_usd: number;
  exit_reason_counts: Record<string, number>;
}

export interface M7TradeRow {
  trade_id: string;
  friday_date_ist: string;
  entry_ts_utc: number;
  entry_hour_ist: number;
  entry_time_label: string;
  expiry_date: string;
  expiry_unix: number;
  dte_days: number;
  delta_target: number;
  is_straddle: boolean;
  call_strike: number;
  put_strike: number;
  call_entry_mark: number;
  put_entry_mark: number;
  call_entry_iv: number;
  put_entry_iv: number;
  call_entry_delta: number;
  put_entry_delta: number;
  total_credit_usd_per_btc: number;
  credit_usd: number;
  credit_pct_of_spot: number;
  spot_at_entry: number;
  entry_atm_iv: number;
  entry_atm_iv_pct: number;
  entry_atm_iv_band: string;
  entry_slippage_call_usd: number;
  entry_slippage_put_usd: number;
  entry_brokerage_call_usd: number;
  entry_brokerage_put_usd: number;
  total_entry_cost_usd: number;
  margin_used_usd_at_entry: number | null;
  dte_bucket: string;
  spot_bucket: string;
  delta_target_bucket: string;
  ivp_bucket: string;
  fair_credit_at_ivp?: number | null;
  structural_credit_pct?: number | null;
  iv_regime_premium_pct?: number | null;
  excess_over_fair_pct?: number | null;
  ctx_pattern?: string | null;
  ctx_gex_regime?: string | null;
}

export interface M7TradesResponse {
  total: number;
  offset: number;
  limit: number;
  rows: M7TradeRow[];
}

export interface M7PathRow {
  trade_id: string;
  ts: number;
  minute_offset: number;
  spot: number; spot_open: number; spot_high: number; spot_low: number;
  spot_volume: number; spot_oi: number;
  call_mark: number; put_mark: number; total_premium: number;
  call_oi: number; put_oi: number;
  call_iv: number; put_iv: number;
  atm_iv_now: number;
  call_delta: number; call_gamma: number; call_theta: number; call_vega: number;
  put_delta: number; put_gamma: number; put_theta: number; put_vega: number;
  net_delta: number; net_gamma: number; net_theta: number; net_vega: number;
  theta_per_vega_combined: number;
  gross_pnl_usd: number; net_pnl_unwind_usd: number;
  pnl_pct_of_credit: number; pnl_pct_of_margin: number;
}

export interface M7PathResponse {
  trade_id: string;
  n_rows: number;
  rows: M7PathRow[];
}

export interface M7AggregateRow {
  [dim: string]: string | number | null | boolean;
  value: number;
  n_trades: number;
}

export interface M7AggregateResponse {
  rows: M7AggregateRow[];
  metric: string;
  dimensions: string[];
}

export interface M7IvBandSummaryRow {
  entry_atm_iv_band: string;
  entry_hour_ist: number;
  expiry_date: string;
  delta_target: number;
  score: number;
  n_trades: number;
}

export interface M7BestComboRow {
  entry_hour_ist: number;
  expiry_date: string;
  delta_target: number;
  score: number;
  n_trades: number;
}

export interface M7CostBreakdown {
  trade_id: string;
  entry_slippage_call_usd: number;
  entry_slippage_put_usd: number;
  entry_brokerage_call_usd: number;
  entry_brokerage_put_usd: number;
  total_entry_cost_usd: number;
  credit_usd: number;
  margin_used_usd_at_entry: number | null;
}

export interface M7Meta {
  n_trades_total: number;
  fridays: string[];
  expiries: string[];
  deltas: number[];
  entry_hours: number[];
  iv_bands: string[];
  dte_buckets: string[];
  ivp_buckets: string[];
  patterns: string[];
  gex_regimes: string[];
}
