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
  expiry_bucket?: string;         // e.g. "next_to_next", "weekly"
  entry_atm_iv_band?: string;     // e.g. "20-30,30-40"
  entry_hour_ist?: string;
  dte_bucket?: string;
  spot_bucket?: string;
  ivp_bucket?: string;
  ctx_pattern?: string;
  ctx_gex_regime?: string;
  friday_date_ist?: string;
  // Chunk 1 — leg attribution skew filters
  iv_skew_bucket?: string;
  delta_skew_bucket?: string;
  premium_skew_bucket?: string;
  leg_winner?: string;            // both | call_only | put_only | neither
  // Loss-anatomy: filter trades by what caused the loss (winners → null/empty)
  loss_cause?: string;            // directional | vol_expansion | path_dependent | gamma_squeeze | skew_flip | unclassified
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
  // Calendar-sweep path columns (dataset='calendar'); absent for delta/price match.
  near_iv?: number; far_iv?: number; iv_gap?: number;
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
  expiry_bucket: string;
  delta_target: number;
  score: number;
  n_trades: number;
  // Extra metrics for the chosen combo (returned regardless of active metric)
  avg_net_pnl?: number | null;
  win_rate?: number | null;
  avg_loss_usd?: number | null;
  avg_win_usd?: number | null;
  avg_credit?: number | null;
  avg_margin?: number | null;
  avg_pct_return_on_margin?: number | null;
  avg_pct_return_on_credit?: number | null;
  // Winners-only ROI (per-trade ratio, then mean over winners)
  avg_pct_return_on_margin_winners?: number | null;
  avg_pct_return_on_credit_winners?: number | null;
  // Winners-only MTM stats
  avg_max_mtm_winners?: number | null;
  avg_min_mtm_winners?: number | null;
  max_mtm_winners?: number | null;
  min_mtm_winners?: number | null;
  // Losers-only MTM stats
  avg_max_mtm_losers?: number | null;
  avg_min_mtm_losers?: number | null;
  max_mtm_losers?: number | null;
  min_mtm_losers?: number | null;
  // Net P&L extremes (after entry + exit costs)
  max_loss_usd?: number | null;
  max_win_usd?: number | null;
  // Exit-time MTM (gross at exit minus entry costs only — what shows on screen)
  avg_exit_mtm?: number | null;
  avg_win_mtm?: number | null;
  avg_loss_mtm?: number | null;
  largest_win_mtm?: number | null;
  largest_loss_mtm?: number | null;
  total_win_mtm?: number | null;
  total_loss_mtm?: number | null;
  // Counts
  n_rule_trigger?: number | null;
  n_premium_sl_hit?: number | null;
  n_hard_cap?: number | null;
  n_losses?: number | null;
  n_wins?: number | null;
  // Streaks (chronological)
  max_consec_losses?: number | null;
  max_consec_wins?: number | null;
  max_consec_sl_hits?: number | null;
  max_consec_premium_sl_hits?: number | null;
  // Outlier counts vs group-average MTM
  n_winners_below_avg_min_mtm?: number | null;
  n_losers_above_avg_max_mtm?: number | null;
  // Peak / trough unrealized as % of credit
  avg_pct_max_mtm_on_credit?: number | null;
  avg_pct_min_mtm_on_credit?: number | null;
  // Exit-time means
  avg_exit_offset_minutes?: number | null;
  avg_winner_exit_offset_minutes?: number | null;
  avg_loser_exit_offset_minutes?: number | null;
}

export interface M7BestComboRow {
  entry_hour_ist: number;
  expiry_bucket: string;
  delta_target: number;
  score: number;
  n_trades: number;
}

export interface M7BestComboMarker {
  friday_date_ist: string;
  rel_time_max_mtm: number | null;
  rel_time_min_mtm: number | null;
  max_mtm_usd: number | null;
  min_mtm_usd: number | null;
  exit_mtm_usd: number | null;
  net_pnl_estimate_usd: number | null;
  is_win: boolean | null;
  exit_reason: string | null;
}
export interface M7BestComboMarkersBand {
  entry_atm_iv_band: string;
  entry_hour_ist: number;
  expiry_bucket: string;
  delta_target: number;
  n_trades: number;
  n_wins: number;
  n_losses: number;
  trades: M7BestComboMarker[];
}
export interface M7BestComboMarkersResponse {
  metric: string;
  bands: M7BestComboMarkersBand[];
}

export interface M7MtmPickPath {
  trade_id: string;
  friday_date_ist: string;
  net_pnl_estimate_usd: number | null;
  max_mtm_usd?: number | null;
  min_mtm_usd?: number | null;
  minutes: number[];
  pnl: number[];
}

export interface M7MtmAvgCurve {
  minutes: number[];
  mean_pnl: number[];
  n_trades_alive: number[];
}

export interface M7BandOverlay {
  friday_band: string;
  entry_hour_ist: number;
  expiry_bucket: string;
  delta_target: number;
  n_trades: number;
  picks: {
    best:          M7MtmPickPath | null;
    worst:         M7MtmPickPath | null;
    best_max_mtm:  M7MtmPickPath | null;
    worst_min_mtm: M7MtmPickPath | null;
  };
  avg_curve: M7MtmAvgCurve;
}

export interface M7FridayBandMtmOverlayResponse {
  band_mode: string;
  bands: M7BandOverlay[];
}

export interface M7MissedFridayRow {
  friday_date_ist: string;
  entry_hour_ist: number;
  expiry_bucket: string;
  delta_target: number;
  entry_atm_iv_band: string;
  entry_atm_iv_pct?: number | null;
  credit_usd?: number | null;
  margin_used_usd_at_entry?: number | null;
  net_pnl_estimate_usd?: number | null;
  gross_pnl_usd?: number | null;
  max_mtm_usd?: number | null;
  min_mtm_usd?: number | null;
  exit_mtm_usd?: number | null;
  is_win?: boolean | null;
  exit_reason?: string | null;
}
export interface M7MissedFridaysResponse {
  rows: M7MissedFridayRow[];
  n_missed: number;
  n_total_fridays: number;
  n_matched: number;
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
  expiry_buckets: string[];
  deltas: number[];
  entry_hours: number[];
  iv_bands: string[];
  dte_buckets: string[];
  ivp_buckets: string[];
  patterns: string[];
  gex_regimes: string[];
  // Chunk 1 — skew bucket universes (sorted put-leaning → call-leaning)
  delta_skew_buckets: string[];
  iv_skew_buckets: string[];
  premium_skew_buckets: string[];
  leg_winners: string[];          // ["both", "call_only", "put_only", "neither"]
  // Loss-cause classifier (Chunk 1 of loss-anatomy plan); winners get null
  loss_causes: string[];          // ["directional", "vol_expansion", "path_dependent", "gamma_squeeze", "skew_flip", "unclassified"]
}

// ── Chunk 1: Per-leg attribution ─────────────────────────────────────────────

export interface M7LegAttributionRow {
  trade_id: string;
  friday_date_ist: string;
  entry_hour_ist: number;
  entry_time_label?: string | null;
  expiry_date: string;
  expiry_bucket: string;
  delta_target: number;
  is_straddle: boolean;
  entry_atm_iv_band: string;
  entry_atm_iv_pct?: number | null;
  // Per-leg entry context
  call_strike: number;
  put_strike: number;
  quantity_lots: number;
  call_entry_mark: number;
  put_entry_mark: number;
  call_entry_iv: number;
  put_entry_iv: number;
  call_entry_delta: number;
  put_entry_delta: number;
  // Per-leg exit context
  exit_call_mark: number;
  exit_put_mark: number;
  exit_spot: number;
  // Per-leg outcomes
  call_leg_pnl_usd: number;
  put_leg_pnl_usd: number;
  leg_pnl_diff_usd: number;
  call_leg_max_mtm_usd?: number | null;
  call_leg_min_mtm_usd?: number | null;
  put_leg_max_mtm_usd?: number | null;
  put_leg_min_mtm_usd?: number | null;
  leg_winner: 'both' | 'call_only' | 'put_only' | 'neither';
  // Skew at entry
  delta_skew: number;
  iv_skew_pct: number;
  premium_skew_usd: number;
  premium_skew_pct?: number | null;
  iv_skew_bucket: string;
  delta_skew_bucket: string;
  premium_skew_bucket: string;
  // Position-level outcomes
  credit_usd: number;
  margin_used_usd_at_entry?: number | null;
  total_entry_cost_usd: number;
  total_exit_cost_usd: number;
  gross_pnl_usd: number;
  net_pnl_estimate_usd: number;
  max_mtm_usd?: number | null;
  min_mtm_usd?: number | null;
  exit_mtm_usd?: number | null;
  is_win: boolean;
  exit_reason: string;
  exit_ts: number;
  // Loss-cause classifier — null for winners
  loss_cause?: string | null;
}

export interface M7LegAttributionResponse {
  total: number;
  offset: number;
  limit: number;
  rows: M7LegAttributionRow[];
}

export interface M7LegSkewHeatmapCell {
  // The two grouping cols are dynamic; their names match `row_key` and `col_key`.
  // Component reads them generically.
  [key: string]: string | number | null;
}

export interface M7LegSkewHeatmapResponse {
  rows: M7LegSkewHeatmapCell[];
  metric: string;
  row_key: string;
  col_key: string;
}
