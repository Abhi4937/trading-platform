// ── Mirrors backend Pydantic models in app/api/backtest.py ──────────────────

export type ExpirySelector =
  | 'current' | 'next' | 'next_to_next'
  | 'weekly'  | 'next_weekly'
  | 'monthly' | 'next_monthly'
  | 'current_plus_n';

// ── AlgoTest-aligned enums (form-side, translated to backend schema on submit)

export type AlgoStrategyType = 'Intraday' | 'BTST' | 'Positional';
export type AlgoUnderlyingFrom = 'Cash' | 'Futures';
export type AlgoSquareOff = 'Partial' | 'Complete';
export type AlgoPosition = 'Buy' | 'Sell';
export type AlgoOptionType = 'Call' | 'Put';
export type AlgoSegment = 'Futures' | 'Options';
export type AlgoExpiryType =
  | 'Current' | 'Next' | 'Next to Next'
  | 'Weekly' | 'Next Weekly' | 'Monthly' | 'Next Monthly';

export type AlgoStrikeCriteria =
  | 'Strike Type' | 'Premium Range' | 'Closest Premium'
  | 'Premium >=' | 'Premium <='
  | 'Straddle Width' | '% of ATM' | 'Synthetic Future'
  | 'ATM Straddle Premium %' | 'Closest Delta' | 'Delta ≤' | 'Delta ≤ Match' | 'Delta ≤ Align' | 'Highest OI' | 'Delta Range';

// ITM20..ITM1, ATM, OTM1..OTM30 — 51 entries
export type AlgoStrikeType = string;

export const ALGO_STRIKE_OPTIONS: AlgoStrikeType[] = [
  ...Array.from({ length: 20 }, (_, i) => `ITM${20 - i}`),
  'ATM',
  ...Array.from({ length: 30 }, (_, i) => `OTM${i + 1}`),
];

export const ALGO_STRIKE_CRITERIA: AlgoStrikeCriteria[] = [
  'Strike Type', 'Premium Range', 'Closest Premium',
  'Premium >=', 'Premium <=', 'Straddle Width', '% of ATM',
  'Synthetic Future', 'ATM Straddle Premium %', 'Closest Delta', 'Delta ≤', 'Delta ≤ Match', 'Delta ≤ Align', 'Highest OI', 'Delta Range',
];

export const ALGO_EXPIRY_OPTS: AlgoExpiryType[] = [
  'Current', 'Next', 'Next to Next',
  'Weekly', 'Next Weekly', 'Monthly', 'Next Monthly',
];

export const ALGO_TRAILING_OPTS = ['Lock', 'Lock and Trail', 'Overall Trail SL'] as const;
export type AlgoTrailingOption = (typeof ALGO_TRAILING_OPTS)[number];

export const ALGO_OVERALL_SL_OPTS = ['Max Loss', 'Total Premium %'] as const;
export type AlgoOverallSlType = (typeof ALGO_OVERALL_SL_OPTS)[number];

export const ALGO_OVERALL_TGT_OPTS = ['Max Profit', 'Total Premium %'] as const;
export type AlgoOverallTgtType = (typeof ALGO_OVERALL_TGT_OPTS)[number];

export const ALGO_REENTRY_OPTS = ['RE ASAP', 'RE ASAP ↩', 'RE MOMENTUM', 'RE MOMENTUM ↩'] as const;
export type AlgoReentryType = (typeof ALGO_REENTRY_OPTS)[number];

export const ALGO_OVERALL_MOMENTUM_OPTS = [
  'Points (Pts) ↑', 'Points (Pts) ↓', 'Percent (%) ↑', 'Percent (%) ↓',
] as const;
export type AlgoOverallMomentum = (typeof ALGO_OVERALL_MOMENTUM_OPTS)[number];

// ── Helpers: AlgoTest enums → backend schema ─────────────────────────────────

/** "ITM5" → -5, "ATM" → 0, "OTM3" → 3 */
export function strikeTypeToOffset(s: AlgoStrikeType): number {
  if (s === 'ATM') return 0;
  const itm = /^ITM(\d+)$/.exec(s); if (itm) return -Number(itm[1]);
  const otm = /^OTM(\d+)$/.exec(s); if (otm) return  Number(otm[1]);
  return 0;
}

export function expiryTypeToSelector(e: AlgoExpiryType): ExpirySelector {
  switch (e) {
    case 'Current':      return 'current';
    case 'Next':         return 'next';
    case 'Next to Next': return 'next_to_next';
    case 'Weekly':       return 'weekly';
    case 'Next Weekly':  return 'next_weekly';
    case 'Monthly':      return 'monthly';
    case 'Next Monthly': return 'next_monthly';
  }
}

export interface LegSlConfig {
  type: 'pct' | 'points' | 'delta';
  value: number;
}

export interface BacktestLegTemplate {
  strike_offset: number;            // ATM-relative
  type: 'CE' | 'PE';
  action: 'BUY' | 'SELL';
  qty: number;
  expiry_selector: ExpirySelector;
  expiry_offset?: number;
  leg_sl?: LegSlConfig | null;
}

export interface SlippageConfig {
  enabled: boolean;
  mode: 'flat' | 'smart';
  flat_value?: number;
  mult: number;
}

export interface BrokerageConfig {
  enabled: boolean;
  rate: 'standard' | 'offer';
  referral: boolean;
}

export interface BacktestRequest {
  start_date: string;               // YYYY-MM-DD
  end_date: string;                 // YYYY-MM-DD
  legs: BacktestLegTemplate[];
  entry_time_ist: string;           // "09:20"
  weekday_mask: number[];           // subset of [0..6] (Mon..Sun)
  forced_exit_time_ist: string;     // "15:25"
  // 0 = same day (intraday), 1 = next day (BTST), N = positional N days
  exit_day_offset?: number;
  timeframe?: '1m' | '5m' | '15m' | '30m' | '1h';
  slippage: SlippageConfig;
  brokerage: BrokerageConfig;
  // phase 3+ hooks (optional, ignored in phase 1)
  stop_loss?: any;
  target?: any;
  trail_sl?: any;
  dte_force_close?: number;
  per_leg_exits?: any[];
  reentry_after_sl?: number;
  spot_trigger?: any;
  iv_trigger?: any;
  capital?: any;
}

// ── Response shapes ──────────────────────────────────────────────────────────

export interface BacktestSubmitResponse {
  job_id: string;
  status: string;
}

export interface BacktestProgress {
  days_done: number;
  days_total: number;
  current_date: string | null;
  eta_seconds: number | null;
}

export interface BacktestLegFill {
  expiry: string;
  strike: number;
  type: 'CE' | 'PE';
  action: 'BUY' | 'SELL';
  qty: number;
  entry_mark: number;
  exit_mark: number;
  leg_pnl: number;
  entry_delta?: number;
  entry_iv?: number;
}

export interface BacktestTrade {
  date: string;
  entry_time: string | null;
  exit_time: string | null;
  exit_reason: string | null;
  spot_at_entry: number | null;
  spot_at_exit:  number | null;
  legs: BacktestLegFill[];
  gross_pnl: number;
  slippage_cost: number;       // entry_slip + exit_slip (lump sum, kept for back-compat)
  brokerage_cost: number;      // entry_brk + exit_brk (lump sum, kept for back-compat)
  entry_slip: number;          // entry-side slippage at entry conditions
  exit_slip: number;           // exit-side slippage at end-of-trade conditions
  peak_exit_slip: number;      // exit-side slippage recomputed at peak-MTM marks/spot/hour
  entry_brk: number;           // entry brokerage
  exit_brk: number;            // exit brokerage at end-of-trade
  peak_exit_brk: number;       // exit brokerage at peak-MTM marks
  net_pnl: number;
  margin_used: number | null;
  max_mtm: number;
  max_mtm_time: string | null;
  max_pnl_net: number;
  max_leg_marks?: { type: 'CE' | 'PE'; strike: number; mark: number }[];
  max_leg_deltas?: { type: 'CE' | 'PE'; strike: number; delta: number }[];
  min_mtm: number;
  min_mtm_time: string | null;
  min_pnl_net: number;
  min_leg_marks?: { type: 'CE' | 'PE'; strike: number; mark: number }[];
  min_leg_deltas?: { type: 'CE' | 'PE'; strike: number; delta: number }[];
  entry_atm_iv?: number;
  entry_hv?: number;
  is_reentry: boolean;
  skipped: boolean;
  skip_reason?: string;
  // ── Strangle analytics fields (added 2026-05-02) ──
  credit_pct?: number | null;
  credit_pct_normalized?: number | null;
  credit_per_day?: number | null;
  annualized_credit_pct?: number | null;
  fair_credit_at_ivp?: number | null;
  structural_credit_pct?: number | null;
  iv_regime_premium_pct?: number | null;
  excess_over_fair_pct?: number | null;
  z_credit_pct?: number | null;
  z_atm_iv?: number | null;
  quality_score?: number | null;
  size_band?: 'strong' | 'standard' | 'marginal' | 'skip' | null;
  pattern?: 'A' | 'B' | 'C' | 'D' | 'Other' | null;
  ivp_atm_7d_90d_at_entry?: number | null;
  atm_iv_7d_at_entry?: number | null;
  theta_vega_ratio?: number | null;
  gamma_theta_dollar?: number | null;
  position_delta?: number | null;
  position_gamma?: number | null;
  position_vega?: number | null;
  position_theta?: number | null;
  calibration_source?: 'specific_bucket' | 'universal_fallback' | null;
  // Set when calibration parquet not built — quality_score uses IVP+credit
  // fallback so the column isn't blank.
  quality_source?: 'calibrated' | 'fallback_ivp_credit' | null;
  // Wide M1+M2+M3 snapshot at trade entry. Sparse — any col not in the
  // current M3 schema is silently absent. Used by MarketContextPanel in
  // the trade-log detail panel.
  market_context?: Record<string, number | string | null>;
}

export interface BacktestSummary {
  total_trades: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate_pct: number;
  gross_pnl: number;
  total_costs: number;
  net_pnl: number;
  avg_win: number;
  avg_loss: number;
  total_wins_pnl: number;
  total_losses_pnl: number;
  expectancy: number;
  sharpe_daily: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  max_dd_peak_date: string | null;
  max_dd_trough_date: string | null;
  avg_margin_used: number;
  return_on_margin_pct: number;
  // Per-trade Max/Min MTM aggregates (added 2026-04-30).
  // *_mtm has entry slip baked in. *_pnl_net additionally subtracts
  // entry brk + peak-time exit slip + peak-time exit brk.
  avg_max_mtm: number;
  avg_max_pnl_net: number;
  avg_min_mtm: number;
  avg_min_pnl_net: number;
  best_max_mtm: number;
  best_max_mtm_date: string | null;
  best_max_pnl_net: number;
  best_max_pnl_net_date: string | null;
  worst_min_mtm: number;
  worst_min_mtm_date: string | null;
  worst_min_pnl_net: number;
  worst_min_pnl_net_date: string | null;
}

export interface BacktestEquityPoint {
  date: string;
  cum_pnl: number;
  daily_pnl: number;
}

export interface BacktestResult {
  summary: BacktestSummary;
  equity_curve: BacktestEquityPoint[];
  trades: BacktestTrade[];
}

export type BacktestStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled';

export interface BacktestStatusResponse {
  job_id: string;
  status: BacktestStatus;
  progress: BacktestProgress;
  error: string | null;
  result: BacktestResult | null;
}
