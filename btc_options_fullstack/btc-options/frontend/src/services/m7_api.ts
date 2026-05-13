// Typed wrappers for the M7 sweep endpoints (mounted at /api/v1/m7).

import type {
  M7AggregateResponse, M7BestComboMarkersResponse, M7BestComboRow,
  M7CostBreakdown, M7ExitRule, M7Filters,
  M7IvBandSummaryRow, M7LegAttributionResponse, M7LegSkewHeatmapResponse,
  M7Meta, M7MissedFridaysResponse, M7PathResponse,
  M7Summary, M7TradesResponse,
} from '../types/m7';

const BASE = '/api/v1/m7';

function buildQuery(filters: Record<string, unknown>,
                    exit_rule?: M7ExitRule): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v === null || v === undefined || v === '') continue;
    params.append(k, String(v));
  }
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    // Strip null/undefined keys
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v === null || v === undefined) continue;
      cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0) {
      params.append('exit_rule', JSON.stringify(cleaned));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

async function jsonFetch<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, signal ? { signal } : undefined);
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  return (await r.json()) as T;
}

export function fetchM7Summary(filters: M7Filters = {}, exit_rule?: M7ExitRule, signal?: AbortSignal): Promise<M7Summary> {
  return jsonFetch<M7Summary>(`${BASE}/summary${buildQuery(filters as Record<string, unknown>, exit_rule)}`, signal);
}

export function fetchM7Trades(opts: M7Filters & {
  limit?: number; offset?: number; sort_by?: string; sort_dir?: 'asc' | 'desc';
} = {}): Promise<M7TradesResponse> {
  return jsonFetch<M7TradesResponse>(`${BASE}/trades${buildQuery(opts as Record<string, unknown>)}`);
}

export function fetchM7Path(trade_id: string): Promise<M7PathResponse> {
  return jsonFetch<M7PathResponse>(`${BASE}/path?trade_id=${encodeURIComponent(trade_id)}`);
}

export function fetchM7Aggregate(opts: M7Filters & {
  dimensions: string;
  metric?: string;
}, exit_rule?: M7ExitRule): Promise<M7AggregateResponse> {
  return jsonFetch<M7AggregateResponse>(`${BASE}/aggregate${buildQuery(opts as unknown as Record<string, unknown>, exit_rule)}`);
}

export function fetchM7Heatmap(opts: M7Filters & {
  metric?: string;
} = {}, exit_rule?: M7ExitRule): Promise<M7AggregateResponse> {
  return jsonFetch<M7AggregateResponse>(`${BASE}/heatmap${buildQuery(opts as Record<string, unknown>, exit_rule)}`);
}

export function fetchM7IvBandSummary(opts: M7Filters & { metric?: string } = {},
                                      exit_rule?: M7ExitRule,
                                      signal?: AbortSignal): Promise<{ rows: M7IvBandSummaryRow[]; metric: string }> {
  return jsonFetch(`${BASE}/iv_band_summary${buildQuery(opts as Record<string, unknown>, exit_rule)}`, signal);
}

export function fetchM7BestCombo(opts: M7Filters & {
  metric?: string;
  top_n?: number;
} = {}, exit_rule?: M7ExitRule): Promise<{ rows: M7BestComboRow[]; metric: string }> {
  return jsonFetch(`${BASE}/best_combo${buildQuery(opts as Record<string, unknown>, exit_rule)}`);
}

export function fetchM7CostBreakdown(trade_id: string): Promise<M7CostBreakdown> {
  return jsonFetch<M7CostBreakdown>(`${BASE}/cost_breakdown?trade_id=${encodeURIComponent(trade_id)}`);
}

export function fetchM7Meta(): Promise<M7Meta> {
  return jsonFetch<M7Meta>(`${BASE}/meta`);
}

export function fetchM7MissedFridays(opts: M7Filters & { metric?: string } = {},
                                      exit_rule?: M7ExitRule,
                                      signal?: AbortSignal): Promise<M7MissedFridaysResponse> {
  return jsonFetch<M7MissedFridaysResponse>(
    `${BASE}/missed_fridays${buildQuery(opts as Record<string, unknown>, exit_rule)}`, signal);
}

export function fetchM7BestComboMarkers(
  opts: M7Filters & { metric?: string } = {},
  exit_rule?: M7ExitRule,
  signal?: AbortSignal,
): Promise<M7BestComboMarkersResponse> {
  return jsonFetch<M7BestComboMarkersResponse>(
    `${BASE}/best_combo_markers${buildQuery(opts as Record<string, unknown>, exit_rule)}`, signal);
}

// ── Chunk 1: Per-leg attribution endpoints ──────────────────────────────────

export function fetchM7LegAttribution(
  opts: M7Filters & {
    sort_by?: string;
    sort_dir?: 'asc' | 'desc';
    limit?: number;
    offset?: number;
  } = {},
  exit_rule?: M7ExitRule,
  signal?: AbortSignal,
): Promise<M7LegAttributionResponse> {
  return jsonFetch<M7LegAttributionResponse>(
    `${BASE}/leg_attribution${buildQuery(opts as Record<string, unknown>, exit_rule)}`, signal);
}

export function fetchM7LegSkewHeatmap(
  opts: M7Filters & {
    metric?: string;
    row_key?: string;
    col_key?: string;
  } = {},
  exit_rule?: M7ExitRule,
  signal?: AbortSignal,
): Promise<M7LegSkewHeatmapResponse> {
  return jsonFetch<M7LegSkewHeatmapResponse>(
    `${BASE}/leg_skew_heatmap${buildQuery(opts as Record<string, unknown>, exit_rule)}`, signal);
}

// ── Best combo per IV band (max % credit / margin) ───────────────────────────

export interface M7IvBandBestComboRow {
  iv_band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist?: number | null;
  rule_label: string;
  rule: {
    premium_sl_pct?: number;
    max_profit_pct?: number;
    margin_target_pct?: number;
    fixed_exit_hour_ist?: number;
  };
  score: number | null;
  secondary_score?: number | null;
  n_trades: number;
  // Headline metric set (subset of EXTRA_METRICS — null when N/A for a cell)
  win_rate: number | null;
  avg_net_pnl: number | null; avg_exit_mtm: number | null;
  avg_win_usd: number | null; avg_loss_usd: number | null;
  max_win_usd: number | null; max_loss_usd: number | null;
  avg_win_mtm: number | null; largest_win_mtm: number | null;
  total_win_mtm: number | null;
  avg_loss_mtm: number | null; largest_loss_mtm: number | null;
  total_loss_mtm: number | null;
  avg_credit: number | null; avg_margin: number | null;
  avg_pct_return_on_credit: number | null;
  avg_pct_return_on_margin: number | null;
  avg_pct_return_on_credit_winners: number | null;
  avg_pct_return_on_margin_winners: number | null;
  avg_pct_max_mtm_on_credit: number | null;
  avg_pct_min_mtm_on_credit: number | null;
  avg_max_mtm_winners: number | null; avg_min_mtm_winners: number | null;
  max_mtm_winners: number | null;     min_mtm_winners: number | null;
  avg_max_mtm_losers: number | null;  avg_min_mtm_losers: number | null;
  max_mtm_losers: number | null;      min_mtm_losers: number | null;
  n_rule_trigger: number | null; n_premium_sl_hit: number | null; n_hard_cap: number | null;
  n_wins: number | null; n_losses: number | null;
  max_consec_wins: number | null; max_consec_losses: number | null;
  max_consec_sl_hits: number | null;
  max_consec_premium_sl_hits: number | null;
  n_winners_below_avg_min_mtm: number | null;
  n_losers_above_avg_max_mtm: number | null;
  // Exit-time means (NEW)
  avg_exit_offset_minutes: number | null;
  avg_winner_exit_offset_minutes: number | null;
  avg_loser_exit_offset_minutes: number | null;
  // Capital sizing — server-computed lots given total_capital / pct_deploy / DD constraint.
  // 100 (= backtester baseline) when no sizing params were sent.
  lots?: number | null;
  // v6 — composite + overall-MTM (grid-load enrichments, available even on v4 fallback)
  composite_score?: number | null;
  avg_min_mtm?: number | null;
  avg_max_mtm?: number | null;
  min_mtm?: number | null;
  max_mtm?: number | null;
  // v6 — path peak-trough-peak (NaN on v4 fallback)
  avg_peak_before_trough?: number | null;
  avg_peak_after_trough?: number | null;
  avg_rel_time_peak_before?: number | null;
  avg_rel_time_peak_after?: number | null;
  avg_rel_time_trough?: number | null;
  avg_rel_time_peak?: number | null;
  avg_pct_drop_peak_to_trough?: number | null;
  avg_pct_recovery_trough_to_peak?: number | null;
  avg_alt_net_if_exit_at_peak1?: number | null;
  // v6 — risk-adjusted (grid-load from stdev cols, NaN on v4)
  stdev_net_pnl?: number | null;
  stdev_losses_only?: number | null;
  sharpe_per_trade?: number | null;
  sortino_per_trade?: number | null;
  calmar_like?: number | null;
  // v6 — tail risk
  worst_5_avg_net?: number | null;
  var_95_net?: number | null;
  cvar_95_net?: number | null;
  // v6 — drawdown sequence
  max_consec_loss_dollars?: number | null;
  // v6 — edge stability
  avg_net_pnl_last_26w?: number | null;
  win_rate_last_26w?: number | null;
  // v6 — fixed-hour exit counter (separate from rule_trigger / hard_cap)
  n_fixed_hour_ist?: number | null;
}

// Any of the metric keys backend exposes via _METRIC_DIRECTIONS, plus
// the legacy short names 'credit' / 'margin'. Kept loose because new metrics
// can be added backend-side without breaking the frontend.
export type M7Ranking = string;

export interface M7IvBandBestComboResponse {
  ranking: M7Ranking;
  secondary?: M7Ranking | null;
  tolerance_pct?: number;
  status: 'warming' | 'ready';
  rules_done?: number;
  rules_total?: number;
  started_at?: number | null;
  rows: M7IvBandBestComboRow[];
  n_rules?: number;
  n_cells?: number;
}

export type M7RuleFamily = 'all' | 'max_profit' | 'margin_target';

export interface FetchBestComboArgs {
  ranking?: M7Ranking;
  secondary?: M7Ranking | null;
  tolerance_pct?: number;
  rule_family?: M7RuleFamily;
  total_capital_usd?: number | null;
  pct_deploy?: number;
  dd_metric?: string | null;
  dd_threshold?: number | null;
  // Phase 0/1 — picker filters
  min_hit_pct?: number | null;            // default 50; 0 disables
  max_loss_cap_pct?: number | null;       // drop cells where scaled |max_loss| > cap%
  max_drop_peak_to_trough_pct?: number | null;  // drop cells where avg drop > cap (v6 only)
}

export function fetchM7IvBandBestCombo(
  args: FetchBestComboArgs | M7Ranking = {},
  signal?: AbortSignal,
): Promise<M7IvBandBestComboResponse> {
  // Legacy: callers passing a bare 'credit' | 'margin' string still work.
  const opts: FetchBestComboArgs =
    typeof args === 'string' ? { ranking: args } : args;
  const params = new URLSearchParams();
  params.set('ranking', opts.ranking ?? 'avg_net_pnl');
  if (opts.secondary) {
    params.set('secondary', opts.secondary);
    if (opts.tolerance_pct != null) {
      params.set('tolerance_pct', String(opts.tolerance_pct));
    }
  }
  if (opts.rule_family && opts.rule_family !== 'all') {
    params.set('rule_family', opts.rule_family);
  }
  if (opts.total_capital_usd != null && opts.total_capital_usd > 0) {
    params.set('total_capital_usd', String(opts.total_capital_usd));
    if (opts.pct_deploy != null) {
      params.set('pct_deploy', String(opts.pct_deploy));
    }
    if (opts.dd_metric && opts.dd_threshold != null) {
      params.set('dd_metric', opts.dd_metric);
      params.set('dd_threshold', String(opts.dd_threshold));
    }
  }
  // Picker filters
  if (opts.min_hit_pct != null) {
    params.set('min_hit_pct', String(opts.min_hit_pct));
  }
  if (opts.max_loss_cap_pct != null) {
    params.set('max_loss_cap_pct', String(opts.max_loss_cap_pct));
  }
  if (opts.max_drop_peak_to_trough_pct != null) {
    params.set('max_drop_peak_to_trough_pct', String(opts.max_drop_peak_to_trough_pct));
  }
  return jsonFetch<M7IvBandBestComboResponse>(
    `${BASE}/iv_band_best_combo?${params.toString()}`, signal);
}

// ── New diagnostic endpoints (Phase 1) ────────────────────────────────────────

export interface M7RuleComparisonRow extends M7IvBandBestComboRow {
  hit_pct?: number | null;  // (n_trades - n_hard_cap) / n_trades
}

export interface M7RuleComparisonResponse {
  rows: M7RuleComparisonRow[];
  status: string;
  band?: string;
  expiry_bucket?: string;
  delta_target?: number;
  entry_hour_ist?: number;
  n_rules?: number;
}

export function fetchM7RuleComparison(args: {
  band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
}, signal?: AbortSignal): Promise<M7RuleComparisonResponse> {
  const p = new URLSearchParams({
    band: args.band,
    expiry_bucket: args.expiry_bucket,
    delta_target: String(args.delta_target),
    entry_hour_ist: String(args.entry_hour_ist),
  });
  return jsonFetch<M7RuleComparisonResponse>(
    `${BASE}/iv_band_best_combo/rule_comparison?${p}`, signal);
}

export interface M7CrossBandCheckResponse {
  rows: M7IvBandBestComboRow[];
  status: string;
  picked_band?: string;
  expiry_bucket?: string;
  delta_target?: number;
  entry_hour_ist?: number;
  rule_label?: string;
}

export function fetchM7CrossBandCheck(args: {
  band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
  rule_label: string;
}, signal?: AbortSignal): Promise<M7CrossBandCheckResponse> {
  const p = new URLSearchParams({
    band: args.band,
    expiry_bucket: args.expiry_bucket,
    delta_target: String(args.delta_target),
    entry_hour_ist: String(args.entry_hour_ist),
    rule_label: args.rule_label,
  });
  return jsonFetch<M7CrossBandCheckResponse>(
    `${BASE}/iv_band_best_combo/cross_band_check?${p}`, signal);
}

export interface M7SingleComboSummary {
  n_trades: number;
  n_wins: number | null;
  n_losses: number | null;
  win_rate: number | null;
  avg_net_pnl: number | null;
  total_net_pnl: number | null;
  avg_credit: number | null;
  avg_margin: number | null;
  max_loss_usd: number | null;
  n_rule_trigger: number | null;
  n_hard_cap: number | null;
  avg_pct_return_on_credit: number | null;
  composite_score: number | null;
  sharpe_per_trade: number | null;
  n_bands_covered: number;
  lots?: number;
  scaled_avg_net_pnl?: number | null;
  scaled_total_net_pnl?: number | null;
  scaled_max_loss_usd?: number | null;
}

export interface M7SingleComboSimulationResponse {
  status: string;
  summary: M7SingleComboSummary | null;
  per_band_breakdown?: M7IvBandBestComboRow[];
  expiry_bucket?: string;
  delta_target?: number;
  entry_hour_ist?: number;
  rule_label?: string;
  total_capital_usd?: number | null;
  pct_deploy?: number;
}

export function fetchM7SingleComboSimulation(args: {
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
  rule_label: string;
  total_capital_usd?: number | null;
  pct_deploy?: number;
}, signal?: AbortSignal): Promise<M7SingleComboSimulationResponse> {
  const p = new URLSearchParams({
    expiry_bucket: args.expiry_bucket,
    delta_target: String(args.delta_target),
    entry_hour_ist: String(args.entry_hour_ist),
    rule_label: args.rule_label,
  });
  if (args.total_capital_usd != null && args.total_capital_usd > 0) {
    p.set('total_capital_usd', String(args.total_capital_usd));
    if (args.pct_deploy != null) {
      p.set('pct_deploy', String(args.pct_deploy));
    }
  }
  return jsonFetch<M7SingleComboSimulationResponse>(
    `${BASE}/iv_band_best_combo/single_combo_simulation?${p}`, signal);
}

// Missed-Friday force-fit drilldown (Feature A)

export interface M7MissedFridayForceFitPick {
  pick_band: string;
  rule_label: string;
  fits: boolean;
  actual_iv_band_on_this_friday: string | null;
}

export interface M7MissedFridayForceFitRow {
  friday_date_ist: string;
  n_total_trades: number;
  bands_touched: string[];
  pick_availability: M7MissedFridayForceFitPick[];
}

export interface M7MissedFridayPickInfo {
  band: string;
  entry_hour_ist: number | null;
  expiry_bucket: string;
  delta_target: number;
  rule_label: string;
}

export interface M7MissedFridaysForceFitResponse {
  rows: M7MissedFridayForceFitRow[];
  status: string;
  n_missed?: number;
  n_total_fridays?: number;
  n_matched?: number;
  picks?: M7MissedFridayPickInfo[];
}

export function fetchM7MissedFridaysForceFit(signal?: AbortSignal): Promise<M7MissedFridaysForceFitResponse> {
  return jsonFetch<M7MissedFridaysForceFitResponse>(
    `${BASE}/iv_band_best_combo/missed_fridays_force_fit`, signal);
}

// ── Loss-anatomy: Chunk 3 — per-cell winners-vs-losers ─────────────────────
export interface M7CellWvlIndicatorRow {
  indicator: string;
  label: string;
  category: string;
  avg_win: number | null;
  avg_loss: number | null;
  gap: number | null;
  sigma: number;
  discriminating: boolean;
  p_value_t: number | null;
  n_win: number;
  n_loss: number;
}

export interface M7Cell {
  entry_atm_iv_band: string;
  entry_hour_ist: number;
  expiry_bucket: string;
  delta_target: number;
}

export interface M7CellWvlResponse {
  cell: M7Cell;
  n_trades: number;
  n_win: number;
  n_loss: number;
  win_rate: number;
  low_confidence: boolean;
  pool_suggestions: string[];
  rows: M7CellWvlIndicatorRow[];
}

export function fetchM7CellWinnersVsLosers(
  cell: M7Cell,
  opts?: { discriminate_sigma?: number; min_n_per_side?: number; exit_rule?: M7ExitRule },
  signal?: AbortSignal,
): Promise<M7CellWvlResponse> {
  const params = new URLSearchParams();
  params.append('cell', JSON.stringify(cell));
  if (opts?.discriminate_sigma != null)
    params.append('discriminate_sigma', String(opts.discriminate_sigma));
  if (opts?.min_n_per_side != null)
    params.append('min_n_per_side', String(opts.min_n_per_side));
  if (opts?.exit_rule && Object.keys(opts.exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(opts.exit_rule)) {
      if (v != null) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0)
      params.append('exit_rule', JSON.stringify(cleaned));
  }
  return jsonFetch<M7CellWvlResponse>(
    `${BASE}/cell_winners_vs_losers?${params.toString()}`, signal);
}

// ── Loss-anatomy: Chunk 5 — worst-N Fridays per cell ───────────────────────
export interface M7CellSpecialCol {
  col: string;
  label: string;
  category: string;
  value: number;
  cell_median: number;
  z: number;
}

export interface M7CellWorstFridayRow {
  friday_date_ist: string;
  trade_id: string;
  net_pnl_estimate_usd: number;
  gross_pnl_usd: number;
  credit_usd: number;
  loss_cause: string | null;
  is_win: boolean;
  exit_reason: string;
  entry_atm_iv_pct: number | null;
  spot_move_pct: number | null;
  max_iv_jump_pct: number | null;
  rel_time_min_mtm: number | null;
  max_mtm_usd: number | null;
  min_mtm_usd: number | null;
  what_made_it_special: M7CellSpecialCol[];
}

export interface M7CellWorstFridaysResponse {
  cell: M7Cell;
  n_total_fridays: number;
  n_total_trades: number;
  n_returned: number;
  rows: M7CellWorstFridayRow[];
}

export function fetchM7CellWorstFridays(
  cell: M7Cell,
  opts?: { n?: number; n_special?: number; exit_rule?: M7ExitRule },
  signal?: AbortSignal,
): Promise<M7CellWorstFridaysResponse> {
  const params = new URLSearchParams();
  params.append('cell', JSON.stringify(cell));
  if (opts?.n != null) params.append('n', String(opts.n));
  if (opts?.n_special != null) params.append('n_special', String(opts.n_special));
  if (opts?.exit_rule && Object.keys(opts.exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(opts.exit_rule)) {
      if (v != null) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0)
      params.append('exit_rule', JSON.stringify(cleaned));
  }
  return jsonFetch<M7CellWorstFridaysResponse>(
    `${BASE}/cell_worst_fridays?${params.toString()}`, signal);
}

// ── Loss-anatomy: Chunk 6 — universe loss distribution ─────────────────────
export interface M7LossesDistRow {
  [dim: string]: string | number | null;  // includes n, avg_loss_usd, total_loss_usd, share
}

export type M7LossesScope = 'full_coverage' | 'best_combo' | null;
export type M7LossesRanking = 'credit' | 'margin';

export interface M7LossesPerBandRule {
  band: string | null;
  rule_label: string | null;
  rule_dict: Record<string, number>;
  expiry_bucket: string | null;
  delta_target: number | null;
  n_trades: number;
}

export interface M7LossesScopeSummary {
  scope: M7LossesScope;
  ranking: M7LossesRanking | null;
  metric: string | null;
  n_in_scope: number;
  exit_rule_overridden: boolean;
  per_band_rules: M7LossesPerBandRule[];
  warming?: boolean;
  rules_done?: number;
  rules_total?: number;
}

export interface M7LossesBandStats {
  entry_atm_iv_band: string | null;
  n_band_total: number;
  n_loss: number;
  avg_loss_usd: number | null;
  total_loss_usd: number | null;
  largest_loss_usd: number | null;
  avg_loss_mtm: number | null;
  total_loss_mtm: number | null;
  largest_loss_mtm: number | null;
  avg_max_mtm_losers: number | null;
  avg_min_mtm_losers: number | null;
  max_mtm_losers: number | null;
  min_mtm_losers: number | null;
  n_losers_above_avg_max_mtm: number;
  n_rule_trigger: number;
  n_hard_cap: number;
}

export type M7LossesTradesSort = 'pnl_asc' | 'pnl_desc' | 'friday_asc' | 'friday_desc' | 'band';

export interface M7LossesSampleRow {
  trade_id: string | null;
  friday_date_ist: string | null;
  entry_atm_iv_band: string | null;
  entry_hour_ist: number | null;
  expiry_bucket: string | null;
  delta_target: number | null;
  exit_reason: string | null;
  loss_cause: string | null;
  net_pnl_estimate_usd: number | null;
  max_mtm_usd: number | null;
  min_mtm_usd: number | null;
  largest_swing_usd: number | null;
}

export interface M7LossesDistResponse {
  n_losses: number;
  n_total: number;
  loss_rate: number;
  avg_loss_usd: number;
  total_loss_usd: number;
  worst_loss_usd: number;
  by_cause: Record<string, number>;
  by_band: Record<string, number>;
  by_band_stats?: M7LossesBandStats[];
  rows: M7LossesDistRow[];
  scope_summary?: M7LossesScopeSummary;
  losers_sample?: M7LossesSampleRow[];
  losers_sample_total?: number;
  losers_sample_offset?: number;
  losers_sample_limit?: number;
}

export function fetchM7LossesDistribution(
  opts: M7Filters & {
    dimensions?: string;
    exit_rule?: M7ExitRule;
    scope?: M7LossesScope;
    ranking?: M7LossesRanking;
    metric?: string;
    include_trades?: boolean;
    trades_limit?: number;
    trades_offset?: number;
    trades_sort?: M7LossesTradesSort;
    only_sl_hits?: boolean;
  } = {},
  signal?: AbortSignal,
): Promise<M7LossesDistResponse> {
  const { dimensions, exit_rule, scope, ranking, metric,
          include_trades, trades_limit, trades_offset, trades_sort, only_sl_hits,
          ...filters } = opts;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v == null || v === '') continue;
    params.append(k, String(v));
  }
  if (include_trades) params.append('include_trades', 'true');
  if (trades_limit != null)  params.append('trades_limit', String(trades_limit));
  if (trades_offset != null) params.append('trades_offset', String(trades_offset));
  if (trades_sort)           params.append('trades_sort', trades_sort);
  if (only_sl_hits)          params.append('only_sl_hits', 'true');
  if (dimensions) params.append('dimensions', dimensions);
  if (scope) params.append('scope', scope);
  if (ranking) params.append('ranking', ranking);
  if (metric) params.append('metric', metric);
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v != null) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0)
      params.append('exit_rule', JSON.stringify(cleaned));
  }
  return jsonFetch<M7LossesDistResponse>(
    `${BASE}/losses_distribution?${params.toString()}`, signal);
}

// ── Single-trade diagnostic (used by Losses Explorer drill-down modal) ───────

export interface M7TradeHypothesis {
  flag: string;          // 'iv_driven' | 'directional' | 'gamma_squeezed' | 'path_dependent' | 'skew_flipped'
  fired: boolean;
  trigger: string;       // human-readable explanation of fired/not-fired state
  value: number | null;  // primary numeric driver of the predicate
}

export interface M7TradeLeg {
  strike: number | null;
  entry_iv: number | null;
  entry_delta: number | null;
  entry_gamma: number | null;
  entry_theta: number | null;
  entry_vega:  number | null;
  entry_mark:  number | null;
  exit_mark:   number | null;
  leg_pnl_usd: number | null;
  leg_max_mtm_usd: number | null;
  leg_min_mtm_usd: number | null;
}

export interface M7TradeSkew {
  delta_skew: number | null;
  iv_skew_pct: number | null;
  premium_skew_usd: number | null;
  premium_skew_pct: number | null;
  iv_skew_bucket: string | null;
  delta_skew_bucket: string | null;
  premium_skew_bucket: string | null;
}

export interface M7TradeDiagnosticResponse {
  identity: {
    trade_id: string | null;
    friday_date_ist: string | null;
    entry_ts_utc: number | null;
    exit_ts: number | null;
    duration_minutes: number | null;
    entry_hour_ist: number | null;
    expiry_bucket: string | null;
    expiry_date: string | null;
    delta_target: number | null;
    is_straddle: boolean | null;
    exit_reason: string | null;
    loss_cause: string | null;
    leg_winner: string | null;
    entry_atm_iv_band: string | null;
  };
  pnl: {
    credit_usd: number | null;
    margin_used_usd_at_entry: number | null;
    gross_pnl_usd: number | null;
    net_pnl_estimate_usd: number | null;
    is_win: boolean | null;
    max_mtm_usd: number | null;
    min_mtm_usd: number | null;
    exit_mtm_usd: number | null;
    rel_time_max_mtm: number | null;
    rel_time_min_mtm: number | null;
    pct_return_on_credit: number | null;
    pct_return_on_margin: number | null;
    leg_pnl_diff_usd: number | null;
  };
  costs: Record<string, number | null>;
  per_leg: { call: M7TradeLeg; put: M7TradeLeg; skew: M7TradeSkew };
  vol_regime: Record<string, number | null>;
  skew_smile: Record<string, number | null>;
  spot_regime: Record<string, number | null>;
  expected_move: {
    expected_move_1sigma_7d: number | null;
    expected_move_1sigma_14d: number | null;
    expected_move_1sigma_30d: number | null;
    actual_move_usd: number | null;
    actual_vs_1sigma_7d_ratio: number | null;
    exceeded_1sigma_7d: boolean | null;
  };
  greeks_ratios: Record<string, number | null>;
  context_premium: Record<string, number | null>;
  hypotheses: M7TradeHypothesis[];
}

export function fetchM7TradeDiagnostic(
  trade_id: string,
  exit_rule?: M7ExitRule,
  signal?: AbortSignal,
): Promise<M7TradeDiagnosticResponse> {
  const params = new URLSearchParams({ trade_id });
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v != null) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0)
      params.append('exit_rule', JSON.stringify(cleaned));
  }
  return jsonFetch<M7TradeDiagnosticResponse>(
    `${BASE}/trade_diagnostic?${params.toString()}`, signal);
}
