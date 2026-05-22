// Typed wrappers for the M7 sweep endpoints (mounted at /api/v1/m7).

import type {
  M7AggregateResponse, M7BestComboMarkersResponse, M7BestComboRow,
  M7CostBreakdown, M7ExitRule, M7Filters,
  M7FridayBandMtmOverlayResponse,
  M7IvBandSummaryRow, M7LegAttributionResponse, M7LegSkewHeatmapResponse,
  M7Meta, M7MissedFridaysResponse, M7PathResponse,
  M7Summary, M7TradesResponse,
} from '../types/m7';

const BASE = '/api/v1/m7';

// Joint Δ+Price-match dataset toggle. Default = today's pure-Δ behavior;
// 'price_match' reads the parallel parquet built by m7_batch_backtester_joint.
export type M7Dataset = 'delta_match' | 'price_match';

// Append the dataset query param IFF it's a non-default value. Used by every
// M7 endpoint that reads trades/paths/grid so the toggle threads through.
function appendDatasetParam(params: URLSearchParams, dataset?: M7Dataset): void {
  if (dataset && dataset !== 'delta_match') {
    params.set('dataset', dataset);
  }
}

function buildQuery(filters: Record<string, unknown>,
                    exit_rule?: M7ExitRule,
                    dataset?: M7Dataset): string {
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
  appendDatasetParam(params, dataset);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

// Friday-band endpoints crash uvicorn under 5 concurrent calls. Throttle
// them to 2 concurrent so the dashboard's burst doesn't tip the backend
// over. Each friday_band endpoint is ~2-15s; serial 2-at-a-time costs
// ~6-15s total which is acceptable while a proper backend fix is pending.
const _FB_THROTTLE_MAX = 2;
let _fbInflight = 0;
const _fbWaiters: Array<() => void> = [];

function _fbAcquire(): Promise<void> {
  return new Promise(resolve => {
    if (_fbInflight < _FB_THROTTLE_MAX) {
      _fbInflight++; resolve();
    } else {
      _fbWaiters.push(() => { _fbInflight++; resolve(); });
    }
  });
}

function _fbRelease(): void {
  _fbInflight--;
  const next = _fbWaiters.shift();
  if (next) next();
}

async function jsonFetch<T>(url: string, signal?: AbortSignal): Promise<T> {
  // Throttle friday_band endpoints (5-concurrent crashes the backend).
  const needsThrottle = url.includes('/friday_band_');
  if (needsThrottle) await _fbAcquire();
  try {
    return await _jsonFetchInner<T>(url, signal);
  } finally {
    if (needsThrottle) _fbRelease();
  }
}

async function _jsonFetchInner<T>(url: string, signal?: AbortSignal): Promise<T> {
  // Auto-retry on transient 500/502/503/504 (typically Vite proxy giving up
  // while backend is mid-build on a cold cache). Up to 3 attempts with
  // 1.5s / 3s backoff so the dashboard's 5-concurrent burst on a restart
  // doesn't permanently 500. Don't retry 4xx — those are real errors.
  const maxAttempts = 4;
  let lastErr: unknown = null;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const r = await fetch(url, signal ? { signal } : undefined);
      if (r.ok) return (await r.json()) as T;
      // Retry on 5xx; bail on 4xx (caller's problem, not transient).
      if (r.status >= 500 && r.status < 600 && attempt < maxAttempts - 1) {
        await new Promise(res => setTimeout(res, 1500 * (attempt + 1)));
        continue;
      }
      const text = await r.text().catch(() => '');
      throw new Error(`${r.status} ${r.statusText}: ${text}`);
    } catch (e: any) {
      if (e?.name === 'AbortError') throw e;
      lastErr = e;
      if (attempt < maxAttempts - 1) {
        await new Promise(res => setTimeout(res, 1500 * (attempt + 1)));
        continue;
      }
      throw e;
    }
  }
  throw lastErr ?? new Error('jsonFetch: exhausted retries');
}

export function fetchM7Summary(filters: M7Filters = {}, exit_rule?: M7ExitRule, signal?: AbortSignal, dataset?: M7Dataset): Promise<M7Summary> {
  return jsonFetch<M7Summary>(`${BASE}/summary${buildQuery(filters as Record<string, unknown>, exit_rule, dataset)}`, signal);
}

export function fetchM7Trades(opts: M7Filters & {
  limit?: number; offset?: number; sort_by?: string; sort_dir?: 'asc' | 'desc';
} = {}, dataset?: M7Dataset): Promise<M7TradesResponse> {
  return jsonFetch<M7TradesResponse>(`${BASE}/trades${buildQuery(opts as Record<string, unknown>, undefined, dataset)}`);
}

export function fetchM7Path(trade_id: string, dataset?: M7Dataset): Promise<M7PathResponse> {
  const params = new URLSearchParams({ trade_id });
  appendDatasetParam(params, dataset);
  return jsonFetch<M7PathResponse>(`${BASE}/path?${params.toString()}`);
}

export function fetchM7Aggregate(opts: M7Filters & {
  dimensions: string;
  metric?: string;
}, exit_rule?: M7ExitRule, dataset?: M7Dataset): Promise<M7AggregateResponse> {
  return jsonFetch<M7AggregateResponse>(`${BASE}/aggregate${buildQuery(opts as unknown as Record<string, unknown>, exit_rule, dataset)}`);
}

export function fetchM7Heatmap(opts: M7Filters & {
  metric?: string;
} = {}, exit_rule?: M7ExitRule, dataset?: M7Dataset): Promise<M7AggregateResponse> {
  return jsonFetch<M7AggregateResponse>(`${BASE}/heatmap${buildQuery(opts as Record<string, unknown>, exit_rule, dataset)}`);
}

export function fetchM7IvBandSummary(opts: M7Filters & { metric?: string } = {},
                                      exit_rule?: M7ExitRule,
                                      signal?: AbortSignal,
                                      dataset?: M7Dataset): Promise<{ rows: M7IvBandSummaryRow[]; metric: string }> {
  return jsonFetch(`${BASE}/iv_band_summary${buildQuery(opts as Record<string, unknown>, exit_rule, dataset)}`, signal);
}

export function fetchM7BestCombo(opts: M7Filters & {
  metric?: string;
  top_n?: number;
} = {}, exit_rule?: M7ExitRule, dataset?: M7Dataset): Promise<{ rows: M7BestComboRow[]; metric: string }> {
  return jsonFetch(`${BASE}/best_combo${buildQuery(opts as Record<string, unknown>, exit_rule, dataset)}`);
}

export function fetchM7CostBreakdown(trade_id: string, dataset?: M7Dataset): Promise<M7CostBreakdown> {
  const params = new URLSearchParams({ trade_id });
  appendDatasetParam(params, dataset);
  return jsonFetch<M7CostBreakdown>(`${BASE}/cost_breakdown?${params.toString()}`);
}

export function fetchM7Meta(dataset?: M7Dataset): Promise<M7Meta> {
  const params = new URLSearchParams();
  appendDatasetParam(params, dataset);
  const qs = params.toString();
  return jsonFetch<M7Meta>(`${BASE}/meta${qs ? `?${qs}` : ''}`);
}

export function fetchM7MissedFridays(opts: M7Filters & { metric?: string } = {},
                                      exit_rule?: M7ExitRule,
                                      signal?: AbortSignal,
                                      dataset?: M7Dataset): Promise<M7MissedFridaysResponse> {
  return jsonFetch<M7MissedFridaysResponse>(
    `${BASE}/missed_fridays${buildQuery(opts as Record<string, unknown>, exit_rule, dataset)}`, signal);
}

export function fetchM7BestComboMarkers(
  opts: M7Filters & { metric?: string } = {},
  exit_rule?: M7ExitRule,
  signal?: AbortSignal,
  dataset?: M7Dataset,
): Promise<M7BestComboMarkersResponse> {
  return jsonFetch<M7BestComboMarkersResponse>(
    `${BASE}/best_combo_markers${buildQuery(opts as Record<string, unknown>, exit_rule, dataset)}`, signal);
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
  dataset?: M7Dataset,
): Promise<M7LegAttributionResponse> {
  return jsonFetch<M7LegAttributionResponse>(
    `${BASE}/leg_attribution${buildQuery(opts as Record<string, unknown>, exit_rule, dataset)}`, signal);
}

export function fetchM7LegSkewHeatmap(
  opts: M7Filters & {
    metric?: string;
    row_key?: string;
    col_key?: string;
  } = {},
  exit_rule?: M7ExitRule,
  signal?: AbortSignal,
  dataset?: M7Dataset,
): Promise<M7LegSkewHeatmapResponse> {
  return jsonFetch<M7LegSkewHeatmapResponse>(
    `${BASE}/leg_skew_heatmap${buildQuery(opts as Record<string, unknown>, exit_rule, dataset)}`, signal);
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
  // v2 composite — 5-component normalised score, hard-filtered cells flagged.
  // See m7_ranking_config.py for weights and gate thresholds.
  composite_score_v2?: number | null;
  composite_score_v2_components_used?: number | null;
  rank_in_band?: number | null;
  rank_status?: 'ranked' | 'low_n' | 'filtered' | null;
  filter_reason?: string | null;
  score_components?: string | null;  // JSON-string blob
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
  // Picker-tag: true when the picker had to fall back below the min_n_trades
  // threshold for this band (no cells in that band met the credibility floor).
  _low_sample_warning?: boolean | null;
  // Backend-computed: at the SAME (band, expiry, Δ, hour) as the picked
  // cell, the highest-avg_net `sl{X}_exit_hr_*` rule. Lets the table show a
  // "best deterministic fallback exit time" column — useful when the picked
  // rule sometimes hard-caps.
  fallback_exit_hour?: number | null;
  fallback_exit_avg_net?: number | null;
  fallback_exit_rule_label?: string | null;
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

export interface DDCap {
  metric: string;
  threshold: number;
}

export interface FetchBestComboArgs {
  ranking?: M7Ranking;
  secondary?: M7Ranking | null;
  tolerance_pct?: number;
  rule_family?: M7RuleFamily;
  total_capital_usd?: number | null;
  pct_deploy?: number;
  // Legacy single-DD-cap (kept for backward compat; combined with dd_caps via min()).
  dd_metric?: string | null;
  dd_threshold?: number | null;
  // Multi-DD-cap: each (metric, threshold) caps lots independently; final
  // per-band lots = min(margin-cap, …all DD caps…).
  dd_caps?: DDCap[];
  // Phase 0/1 — picker filters
  min_hit_pct?: number | null;            // default 50; 0 disables
  max_loss_cap_pct?: number | null;       // drop cells where scaled |max_loss| > cap%
  max_drop_peak_to_trough_pct?: number | null;  // drop cells where avg drop > cap (v6 only)
  min_n_trades?: number | null;           // default 5; drops cells with too-small sample
  min_win_rate?: number | null;           // 0–100; drops cells whose win_rate is below this
  max_losing_streak?: number | null;      // drop cells whose max_consec_losses exceeds this
  pick_mode?: 'by_hour' | 'aggregate_hours';  // 'aggregate_hours' collapses entry_hour dimension
  // Dimension whitelists — constrain the picker's search space.
  // Empty array / undefined = no filter on that dimension.
  expiry_buckets?: string[];              // e.g. ['current (Sat)', 'next (Sun)']
  delta_targets?: number[];               // e.g. [0.1, 0.2, 0.5]
  entry_hours?: number[];                 // e.g. [21, 22, 23]
  iv_bands?: string[];                    // e.g. ['30-40', '40-50']
  // Exit-hour suffixes from rule_label (sl{X}_exit_hr_{h}). Values match the
  // backend's _hour_label output: '8'..'17' and '1729' for 17:29.
  exit_hours?: string[];                  // e.g. ['14', '15', '1729']
  // Multi-dim bucketing tab (Phase B). Default 'band' = legacy single grid.
  tab?: 'band' | 'band_ivrv' | 'band_ivrv_slope_cn' | 'band_ivrv_slope_nn'
      | 'band_ivrv_slope_cnn' | 'band_ivrv_ts_legacy';
  ivrv_bucket?: 'rich' | 'fair' | 'cheap' | null;
  slope_bucket?: 'backwardation' | 'neutral' | 'contango' | null;
}

export function fetchM7IvBandBestCombo(
  args: FetchBestComboArgs | M7Ranking = {},
  signal?: AbortSignal,
  endpointPrefix: string = '/iv_band_best_combo',
  bandMode?: 'A1' | 'B1' | 'D1',
  d1Tiebreakers?: string[],
  dataset?: M7Dataset,
): Promise<M7IvBandBestComboResponse> {
  // Legacy: callers passing a bare 'credit' | 'margin' string still work.
  const opts: FetchBestComboArgs =
    typeof args === 'string' ? { ranking: args } : args;
  const params = new URLSearchParams();
  if (bandMode) params.set('band_mode', bandMode);
  if (d1Tiebreakers && d1Tiebreakers.length > 0) {
    params.set('d1_tiebreakers', d1Tiebreakers.join(','));
  }
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
    // Multi-DD-cap → CSVs in matching order.
    if (opts.dd_caps && opts.dd_caps.length > 0) {
      const valid = opts.dd_caps.filter(c => c.metric && c.threshold > 0);
      if (valid.length > 0) {
        params.set('dd_metrics', valid.map(c => c.metric).join(','));
        params.set('dd_thresholds', valid.map(c => String(c.threshold)).join(','));
      }
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
  if (opts.min_n_trades != null) {
    params.set('min_n_trades', String(opts.min_n_trades));
  }
  if (opts.min_win_rate != null) {
    params.set('min_win_rate', String(opts.min_win_rate));
  }
  if (opts.max_losing_streak != null) {
    params.set('max_losing_streak', String(opts.max_losing_streak));
  }
  if (opts.pick_mode && opts.pick_mode !== 'by_hour') {
    params.set('pick_mode', opts.pick_mode);
  }
  if (opts.expiry_buckets && opts.expiry_buckets.length > 0) {
    params.set('expiry_buckets', opts.expiry_buckets.join(','));
  }
  if (opts.delta_targets && opts.delta_targets.length > 0) {
    params.set('delta_targets', opts.delta_targets.map(d => String(d)).join(','));
  }
  if (opts.entry_hours && opts.entry_hours.length > 0) {
    params.set('entry_hours', opts.entry_hours.map(h => String(h)).join(','));
  }
  if (opts.iv_bands && opts.iv_bands.length > 0) {
    params.set('iv_bands', opts.iv_bands.join(','));
  }
  if (opts.exit_hours && opts.exit_hours.length > 0) {
    params.set('exit_hours', opts.exit_hours.join(','));
  }
  if (opts.tab && opts.tab !== 'band') {
    params.set('tab', opts.tab);
  }
  if (opts.ivrv_bucket) {
    params.set('ivrv_bucket', opts.ivrv_bucket);
  }
  if (opts.slope_bucket) {
    params.set('slope_bucket', opts.slope_bucket);
  }
  appendDatasetParam(params, dataset);
  return jsonFetch<M7IvBandBestComboResponse>(
    `${BASE}${endpointPrefix}?${params.toString()}`, signal);
}

// ── Best Combo + Full Coverage (deduped Friday attribution) ───────────────────

export type M7CoverageMode = 'force_fit' | 'touched_band';

export interface M7BestComboCoverageRow extends M7IvBandBestComboRow {
  // Per-cell Friday assignment counts (NEW)
  n_assigned?: number | null;
  n_rule?: number | null;
  n_force_fit?: number | null;
  n_touched_band?: number | null;
  n_closest_fallback?: number | null;
}

export interface M7BestComboCoverageSummary {
  total_fridays: number;
  n_assigned: number;
  n_uncovered: number;
  n_rule: number;
  n_force_fit: number;
  n_touched_band: number;
  n_closest_fallback: number;
}

export interface M7BestComboCoverageResponse extends Omit<M7IvBandBestComboResponse, 'rows'> {
  coverage_mode: M7CoverageMode;
  rows: M7BestComboCoverageRow[];
  coverage_summary: M7BestComboCoverageSummary;
}

export function fetchM7IvBandBestComboCoverage(
  args: FetchBestComboArgs & { coverage_mode?: M7CoverageMode } = {},
  signal?: AbortSignal,
  dataset?: M7Dataset,
): Promise<M7BestComboCoverageResponse> {
  const params = new URLSearchParams();
  params.set('ranking', args.ranking ?? 'avg_net_pnl');
  if (args.secondary) {
    params.set('secondary', args.secondary);
    if (args.tolerance_pct != null) params.set('tolerance_pct', String(args.tolerance_pct));
  }
  if (args.rule_family && args.rule_family !== 'all') params.set('rule_family', args.rule_family);
  if (args.total_capital_usd != null && args.total_capital_usd > 0) {
    params.set('total_capital_usd', String(args.total_capital_usd));
    if (args.pct_deploy != null) params.set('pct_deploy', String(args.pct_deploy));
    if (args.dd_metric && args.dd_threshold != null) {
      params.set('dd_metric', args.dd_metric);
      params.set('dd_threshold', String(args.dd_threshold));
    }
    if (args.dd_caps && args.dd_caps.length > 0) {
      const valid = args.dd_caps.filter(c => c.metric && c.threshold > 0);
      if (valid.length > 0) {
        params.set('dd_metrics', valid.map(c => c.metric).join(','));
        params.set('dd_thresholds', valid.map(c => String(c.threshold)).join(','));
      }
    }
  }
  if (args.min_hit_pct != null) params.set('min_hit_pct', String(args.min_hit_pct));
  if (args.max_loss_cap_pct != null) params.set('max_loss_cap_pct', String(args.max_loss_cap_pct));
  if (args.max_drop_peak_to_trough_pct != null) params.set('max_drop_peak_to_trough_pct', String(args.max_drop_peak_to_trough_pct));
  if (args.min_n_trades != null) params.set('min_n_trades', String(args.min_n_trades));
  if (args.min_win_rate != null) params.set('min_win_rate', String(args.min_win_rate));
  if (args.max_losing_streak != null) params.set('max_losing_streak', String(args.max_losing_streak));
  if (args.pick_mode && args.pick_mode !== 'by_hour') params.set('pick_mode', args.pick_mode);
  if (args.expiry_buckets && args.expiry_buckets.length > 0) params.set('expiry_buckets', args.expiry_buckets.join(','));
  if (args.delta_targets && args.delta_targets.length > 0) params.set('delta_targets', args.delta_targets.map(d => String(d)).join(','));
  if (args.entry_hours && args.entry_hours.length > 0) params.set('entry_hours', args.entry_hours.map(h => String(h)).join(','));
  params.set('coverage_mode', args.coverage_mode ?? 'force_fit');
  appendDatasetParam(params, dataset);
  return jsonFetch<M7BestComboCoverageResponse>(
    `${BASE}/iv_band_best_combo/coverage?${params.toString()}`,
    signal,
  );
}

// ── New diagnostic endpoints (Phase 1) ────────────────────────────────────────

export interface M7RuleComparisonRow extends M7IvBandBestComboRow {
  hit_pct?: number | null;            // (n_trades - n_hard_cap) / n_trades
  // Per-rule sizing — mirrors what the picker actually optimises on
  // when capital + DD-cap are active. Each rule has its own lots
  // because each has its own avg_margin and per-100 dd_metric value.
  lots?: number | null;
  scaled_avg_net_pnl?: number | null;
  scaled_max_loss_usd?: number | null;
  // Tagged when this rule would have been excluded by one of the picker's
  // hard filters (min_hit_pct / max_loss_cap / etc.). Reasons are
  // ";"-separated; empty when not filtered.
  filtered_out?: boolean | null;
  filter_reasons?: string | null;
}

export interface M7RuleComparisonResponse {
  rows: M7RuleComparisonRow[];
  status: string;
  band?: string;
  expiry_bucket?: string;
  delta_target?: number;
  entry_hour_ist?: number;
  n_rules?: number;
  sizing_active?: boolean;
  total_capital_usd?: number | null;
  pct_deploy?: number;
  dd_metric?: string | null;
  dd_threshold?: number | null;
}

export function fetchM7RuleComparison(args: {
  band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
  total_capital_usd?: number | null;
  pct_deploy?: number;
  dd_metric?: string | null;
  dd_threshold?: number | null;
  dd_caps?: DDCap[];
  min_hit_pct?: number | null;
  max_loss_cap_pct?: number | null;
  max_drop_peak_to_trough_pct?: number | null;
  min_n_trades?: number | null;
  min_win_rate?: number | null;
  rule_family?: M7RuleFamily;
  endpointPrefix?: string;
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
  dataset?: M7Dataset;
}, signal?: AbortSignal): Promise<M7RuleComparisonResponse> {
  const p = new URLSearchParams({
    band: args.band,
    expiry_bucket: args.expiry_bucket,
    delta_target: String(args.delta_target),
    entry_hour_ist: String(args.entry_hour_ist),
  });
  if (args.total_capital_usd != null && args.total_capital_usd > 0) {
    p.set('total_capital_usd', String(args.total_capital_usd));
    if (args.pct_deploy != null) p.set('pct_deploy', String(args.pct_deploy));
    if (args.dd_metric && args.dd_threshold != null) {
      p.set('dd_metric', args.dd_metric);
      p.set('dd_threshold', String(args.dd_threshold));
    }
    if (args.dd_caps && args.dd_caps.length > 0) {
      const valid = args.dd_caps.filter(c => c.metric && c.threshold > 0);
      if (valid.length > 0) {
        p.set('dd_metrics', valid.map(c => c.metric).join(','));
        p.set('dd_thresholds', valid.map(c => String(c.threshold)).join(','));
      }
    }
  }
  if (args.min_hit_pct != null) p.set('min_hit_pct', String(args.min_hit_pct));
  if (args.max_loss_cap_pct != null) p.set('max_loss_cap_pct', String(args.max_loss_cap_pct));
  if (args.max_drop_peak_to_trough_pct != null) p.set('max_drop_peak_to_trough_pct', String(args.max_drop_peak_to_trough_pct));
  if (args.min_n_trades != null) p.set('min_n_trades', String(args.min_n_trades));
  if (args.min_win_rate != null) p.set('min_win_rate', String(args.min_win_rate));
  if (args.rule_family && args.rule_family !== 'all') p.set('rule_family', args.rule_family);
  if (args.bandMode) p.set('band_mode', args.bandMode);
  if (args.d1Tiebreakers && args.d1Tiebreakers.length > 0) {
    p.set('d1_tiebreakers', args.d1Tiebreakers.join(','));
  }
  appendDatasetParam(p, args.dataset);
  const prefix = args.endpointPrefix ?? '/iv_band_best_combo';
  return jsonFetch<M7RuleComparisonResponse>(
    `${BASE}${prefix}/rule_comparison?${p}`, signal);
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
  endpointPrefix?: string;
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
  dataset?: M7Dataset;
}, signal?: AbortSignal): Promise<M7CrossBandCheckResponse> {
  const p = new URLSearchParams({
    band: args.band,
    expiry_bucket: args.expiry_bucket,
    delta_target: String(args.delta_target),
    entry_hour_ist: String(args.entry_hour_ist),
    rule_label: args.rule_label,
  });
  if (args.bandMode) p.set('band_mode', args.bandMode);
  if (args.d1Tiebreakers && args.d1Tiebreakers.length > 0) {
    p.set('d1_tiebreakers', args.d1Tiebreakers.join(','));
  }
  appendDatasetParam(p, args.dataset);
  const prefix = args.endpointPrefix ?? '/iv_band_best_combo';
  return jsonFetch<M7CrossBandCheckResponse>(
    `${BASE}${prefix}/cross_band_check?${p}`, signal);
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
  endpointPrefix?: string;
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
  dataset?: M7Dataset;
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
  if (args.bandMode) p.set('band_mode', args.bandMode);
  if (args.d1Tiebreakers && args.d1Tiebreakers.length > 0) {
    p.set('d1_tiebreakers', args.d1Tiebreakers.join(','));
  }
  appendDatasetParam(p, args.dataset);
  const prefix = args.endpointPrefix ?? '/iv_band_best_combo';
  return jsonFetch<M7SingleComboSimulationResponse>(
    `${BASE}${prefix}/single_combo_simulation?${p}`, signal);
}

// Friday-level drilldown for one Best Combo cell — surfaces the Fridays
// behind the cell's `Largest win`, `Min MTM (W)`, `W < avg min MTM` aggregates
// + the list of all losing Fridays. Backed by /iv_band_best_combo/cell_friday_detail
// (or /friday_band_best_combo/cell_friday_detail for the Friday-locked variant).

export interface M7CellFridayDetailRow {
  trade_id: string;
  friday_date_ist: string;
  net_pnl_estimate_usd: number | null;
  min_mtm_usd: number | null;
  max_mtm_usd: number | null;
  exit_reason: string;
}

export interface M7CellFridayDetailCell {
  band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
  rule_label: string;
  n_trades?: number;
  n_wins?: number;
  n_losses: number;
  n_winners_below_avg_min_mtm: number;
  max_win_usd: number | null;
  min_mtm_winners: number | null;
  avg_min_mtm_winners: number | null;
}

export interface M7CellFridayDetailResponse {
  status: 'ok' | 'unknown_rule' | 'no_trades' | 'not_built' | string;
  message?: string;
  cell: M7CellFridayDetailCell | null;
  losers: M7CellFridayDetailRow[];
  worst_winner: M7CellFridayDetailRow | null;
  largest_win: M7CellFridayDetailRow | null;
  winners_below_avg_min_mtm: M7CellFridayDetailRow[];
  band_mode?: string;
  d1_tiebreakers?: string[];
}

export function fetchM7CellFridayDetail(args: {
  band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
  rule_label: string;
  endpointPrefix?: string;
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
  dataset?: M7Dataset;
}, signal?: AbortSignal): Promise<M7CellFridayDetailResponse> {
  const p = new URLSearchParams({
    band: args.band,
    expiry_bucket: args.expiry_bucket,
    delta_target: String(args.delta_target),
    entry_hour_ist: String(args.entry_hour_ist),
    rule_label: args.rule_label,
  });
  if (args.bandMode) p.set('band_mode', args.bandMode);
  if (args.d1Tiebreakers && args.d1Tiebreakers.length > 0) {
    p.set('d1_tiebreakers', args.d1Tiebreakers.join(','));
  }
  appendDatasetParam(p, args.dataset);
  const prefix = args.endpointPrefix ?? '/iv_band_best_combo';
  return jsonFetch<M7CellFridayDetailResponse>(
    `${BASE}${prefix}/cell_friday_detail?${p}`, signal);
}

// Missed-Friday force-fit drilldown (Feature A)

export interface M7MissedFridayForceFitPick {
  pick_band: string;
  rule_label: string;
  fits: boolean;
  actual_iv_band_on_this_friday: string | null;
  // Backend rescue extension: realised net P&L if this pick had absorbed the
  // missed Friday (i.e. its rule applied at its hour/expiry/Δ on that Friday).
  rule_net_pnl?: number | null;
  rule_is_win?: boolean | null;
  rule_exit_reason?: string | null;
}

export interface M7MissedFridayRescue {
  rescued_band: string;
  rescued_rule_label: string;
  rescued_net_pnl: number;
  rescued_is_win: boolean;
  rescued_exit_reason: string;
}

export interface M7MissedFridayForceFitRow {
  friday_date_ist: string;
  n_total_trades: number;
  bands_touched: string[];
  pick_availability: M7MissedFridayForceFitPick[];
  rescue?: M7MissedFridayRescue | null;
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
  n_rescuable?: number;
  picks?: M7MissedFridayPickInfo[];
}

export function fetchM7MissedFridaysForceFit(signal?: AbortSignal, dataset?: M7Dataset): Promise<M7MissedFridaysForceFitResponse> {
  const params = new URLSearchParams();
  appendDatasetParam(params, dataset);
  const qs = params.toString();
  return jsonFetch<M7MissedFridaysForceFitResponse>(
    `${BASE}/iv_band_best_combo/missed_fridays_force_fit${qs ? `?${qs}` : ''}`, signal);
}

// Best Combo picker's own missed-Fridays endpoint. Accepts ALL the same
// sizing + filter params as /iv_band_best_combo so the picks computed here
// match the user's current Best Combo table state (NOT the simpler headline
// picker).
export function fetchM7BestComboMissedFridays(
  args: Partial<FetchBestComboArgs>,
  signal?: AbortSignal,
  dataset?: M7Dataset,
): Promise<M7MissedFridaysForceFitResponse> {
  const p = new URLSearchParams();
  p.set('ranking', args.ranking ?? 'avg_net_pnl');
  if (args.rule_family && args.rule_family !== 'all') p.set('rule_family', args.rule_family);
  if (args.total_capital_usd != null && args.total_capital_usd > 0) {
    p.set('total_capital_usd', String(args.total_capital_usd));
    if (args.pct_deploy != null) p.set('pct_deploy', String(args.pct_deploy));
    if (args.dd_metric && args.dd_threshold != null) {
      p.set('dd_metric', args.dd_metric);
      p.set('dd_threshold', String(args.dd_threshold));
    }
    if (args.dd_caps && args.dd_caps.length > 0) {
      const valid = args.dd_caps.filter(c => c.metric && c.threshold > 0);
      if (valid.length > 0) {
        p.set('dd_metrics', valid.map(c => c.metric).join(','));
        p.set('dd_thresholds', valid.map(c => String(c.threshold)).join(','));
      }
    }
  }
  if (args.min_hit_pct != null) p.set('min_hit_pct', String(args.min_hit_pct));
  if (args.max_loss_cap_pct != null) p.set('max_loss_cap_pct', String(args.max_loss_cap_pct));
  if (args.max_drop_peak_to_trough_pct != null) p.set('max_drop_peak_to_trough_pct', String(args.max_drop_peak_to_trough_pct));
  if (args.min_n_trades != null) p.set('min_n_trades', String(args.min_n_trades));
  if (args.min_win_rate != null) p.set('min_win_rate', String(args.min_win_rate));
  if (args.pick_mode && args.pick_mode !== 'by_hour') p.set('pick_mode', args.pick_mode);
  if (args.expiry_buckets && args.expiry_buckets.length > 0) {
    p.set('expiry_buckets', args.expiry_buckets.join(','));
  }
  if (args.delta_targets && args.delta_targets.length > 0) {
    p.set('delta_targets', args.delta_targets.map(d => String(d)).join(','));
  }
  if (args.entry_hours && args.entry_hours.length > 0) {
    p.set('entry_hours', args.entry_hours.map(h => String(h)).join(','));
  }
  appendDatasetParam(p, dataset);
  return jsonFetch<M7MissedFridaysForceFitResponse>(
    `${BASE}/iv_band_best_combo/missed_fridays?${p.toString()}`, signal);
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
  dataset?: M7Dataset,
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
  appendDatasetParam(params, dataset);
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
  dataset?: M7Dataset,
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
  appendDatasetParam(params, dataset);
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

// One per-band cell selection — what M7IvBandBestComboTable picks for each
// IV band, after all dashboard-level filters. The Losses Explorer sends a
// list of these to /losses_distribution so it analyses ONLY the dashboard's
// currently-displayed best-combo trade set.
export interface M7LossesCell {
  entry_atm_iv_band: string;
  entry_hour_ist?: number | null;
  expiry_bucket: string;
  delta_target: number;
  rule: {
    premium_sl_pct?: number;
    max_profit_pct?: number;
    margin_target_pct?: number;
    fixed_exit_hour_ist?: number;
  };
  rule_label?: string;
  // Per-band capital-sized lot count from the Best Combo table; used by
  // downstream panels (e.g. pivot profile) to scale $ values away from the
  // 100-lot backtester baseline.
  lots?: number | null;
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
    cells?: M7LossesCell[];
  } = {},
  signal?: AbortSignal,
  dataset?: M7Dataset,
): Promise<M7LossesDistResponse> {
  const { dimensions, exit_rule, scope, ranking, metric,
          include_trades, trades_limit, trades_offset, trades_sort, only_sl_hits,
          cells, ...filters } = opts;
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
  if (cells && cells.length > 0) {
    params.append('cells', JSON.stringify(cells));
  }
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
  appendDatasetParam(params, dataset);
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

// ── M7 Friday-Band build progress (D1 multi-tiebreaker chains) ────────────

export interface M7FridayBandBuildProgress {
  phase: 'idle' | 'loading_disk_cache' | 'loading_per_trade_archive'
       | 'building_band_map' | 'aggregating_grid' | 'saving_to_disk'
       | 'done' | 'error';
  progress: number;             // 0.0 – 1.0
  message: string | null;
  ts: number | null;
  ready: boolean;
  cache_key?: string;
  source?: string;              // a1_disk_grid | named_disk_grid | memory_cache
  disk_cache_path?: string | null;
  disk_cache_exists?: boolean;
}

export function fetchM7FridayBandBuildProgress(
  bandMode: 'A1' | 'B1' | 'D1',
  d1Tiebreakers?: string[],
  signal?: AbortSignal,
): Promise<M7FridayBandBuildProgress> {
  const params = new URLSearchParams();
  params.set('band_mode', bandMode);
  if (d1Tiebreakers && d1Tiebreakers.length > 0) {
    params.set('d1_tiebreakers', d1Tiebreakers.join(','));
  }
  return jsonFetch<M7FridayBandBuildProgress>(
    `${BASE}/friday_band_best_combo/build_progress?${params.toString()}`, signal);
}


// ── M7 Friday-Band parallel dashboard (new) ──────────────────────────────────

export interface M7FridayBandSummary extends M7Summary {
  band_mode: 'A1' | 'B1' | 'D1';
  n_fridays_per_band: Record<string, number>;
  n_fridays_total: number;
}

function appendFbParams(params: URLSearchParams,
                       bandMode?: 'A1' | 'B1' | 'D1',
                       d1Tiebreakers?: string[]) {
  if (bandMode) params.set('band_mode', bandMode);
  if (d1Tiebreakers && d1Tiebreakers.length > 0) {
    params.set('d1_tiebreakers', d1Tiebreakers.join(','));
  }
}

export function fetchM7FridayBandSummary(
  filters: M7Filters = {},
  exit_rule?: M7ExitRule,
  bandMode?: 'A1' | 'B1' | 'D1',
  d1Tiebreakers?: string[],
  signal?: AbortSignal,
): Promise<M7FridayBandSummary> {
  const params = new URLSearchParams();
  appendFbParams(params, bandMode, d1Tiebreakers);
  for (const [k, v] of Object.entries(filters)) {
    if (v === null || v === undefined || v === '') continue;
    params.set(k, String(v));
  }
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v !== null && v !== undefined) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0) {
      params.set('exit_rule', JSON.stringify(cleaned));
    }
  }
  return jsonFetch<M7FridayBandSummary>(
    `${BASE}/friday_band_summary?${params.toString()}`, signal);
}

export function fetchM7FridayBandSummaryTable(
  filters: M7Filters & { metric?: string } = {},
  exit_rule?: M7ExitRule,
  bandMode?: 'A1' | 'B1' | 'D1',
  d1Tiebreakers?: string[],
  signal?: AbortSignal,
): Promise<{ rows: M7IvBandSummaryRow[]; metric: string; band_mode: string;
            n_fridays_per_band: Record<string, number> }> {
  const params = new URLSearchParams();
  appendFbParams(params, bandMode, d1Tiebreakers);
  for (const [k, v] of Object.entries(filters)) {
    if (v === null || v === undefined || v === '') continue;
    params.set(k, String(v));
  }
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v !== null && v !== undefined) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0) {
      params.set('exit_rule', JSON.stringify(cleaned));
    }
  }
  return jsonFetch(`${BASE}/friday_band_summary_table?${params.toString()}`, signal);
}

export function fetchM7FridayBandBestComboMarkers(
  filters: M7Filters & { metric?: string } = {},
  exit_rule?: M7ExitRule,
  bandMode?: 'A1' | 'B1' | 'D1',
  d1Tiebreakers?: string[],
  signal?: AbortSignal,
): Promise<M7BestComboMarkersResponse> {
  const params = new URLSearchParams();
  appendFbParams(params, bandMode, d1Tiebreakers);
  for (const [k, v] of Object.entries(filters)) {
    if (v === null || v === undefined || v === '') continue;
    params.set(k, String(v));
  }
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v !== null && v !== undefined) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0) {
      params.set('exit_rule', JSON.stringify(cleaned));
    }
  }
  return jsonFetch<M7BestComboMarkersResponse>(
    `${BASE}/friday_band_best_combo_markers?${params.toString()}`, signal);
}

export function fetchM7FridayBandMtmOverlay(
  filters: M7Filters & {
    metric?: string;
    expiry_buckets?: string[];
    max_minutes?: number;
    min_n_trades_in_avg?: number;
  } = {},
  exit_rule?: M7ExitRule,
  bandMode?: 'A1' | 'B1' | 'D1',
  d1Tiebreakers?: string[],
  signal?: AbortSignal,
): Promise<M7FridayBandMtmOverlayResponse> {
  const { max_minutes, min_n_trades_in_avg, expiry_buckets, ...rest } = filters;
  const params = new URLSearchParams();
  appendFbParams(params, bandMode, d1Tiebreakers);
  for (const [k, v] of Object.entries(rest)) {
    if (v === null || v === undefined || v === '') continue;
    params.set(k, String(v));
  }
  if (expiry_buckets && expiry_buckets.length > 0) {
    params.set('expiry_buckets', expiry_buckets.join(','));
  }
  if (max_minutes != null) params.set('max_minutes', String(max_minutes));
  if (min_n_trades_in_avg != null) params.set('min_n_trades_in_avg', String(min_n_trades_in_avg));
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v !== null && v !== undefined) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0) {
      params.set('exit_rule', JSON.stringify(cleaned));
    }
  }
  return jsonFetch<M7FridayBandMtmOverlayResponse>(
    `${BASE}/friday_band_mtm_overlay?${params.toString()}`, signal);
}

export function fetchM7FridayBandLossesDistribution(
  opts: M7Filters & {
    dimensions?: string;
    exit_rule?: M7ExitRule;
    scope?: 'best_combo' | null;
    metric?: string;
    include_trades?: boolean;
    trades_limit?: number;
    trades_offset?: number;
    trades_sort?: M7LossesTradesSort;
    only_sl_hits?: boolean;
  } = {},
  bandMode?: 'A1' | 'B1' | 'D1',
  d1Tiebreakers?: string[],
  signal?: AbortSignal,
  dataset?: M7Dataset,
): Promise<M7LossesDistResponse> {
  const { dimensions, exit_rule, scope, metric,
          include_trades, trades_limit, trades_offset, trades_sort, only_sl_hits,
          ...filters } = opts;
  const params = new URLSearchParams();
  appendFbParams(params, bandMode, d1Tiebreakers);
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
  if (metric) params.append('metric', metric);
  if (exit_rule && Object.keys(exit_rule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exit_rule)) {
      if (v != null) cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0)
      params.append('exit_rule', JSON.stringify(cleaned));
  }
  appendDatasetParam(params, dataset);
  return jsonFetch<M7LossesDistResponse>(
    `${BASE}/friday_band_losses_distribution?${params.toString()}`, signal);
}

// ── M7 Joint Δ+Price Match stats endpoint ─────────────────────────────────

export interface M7JointMatchOutcomes {
  win_count: number;
  loss_count: number;
  win_rate: number | null;
  avg_pnl_usd: number | null;
  sl_count: number;
  profit_count: number;
  neutral_count: number;
}

export interface M7JointMatchPerDelta {
  delta_target: number;
  joint: number;
  fallback: number;
  joint_winrate: number | null;
  fallback_winrate: number | null;
  joint_avg_pnl: number | null;
  fallback_avg_pnl: number | null;
}

export interface M7JointMatchPerBand {
  iv_band: string;
  joint: number;
  fallback: number;
  joint_winrate: number | null;
  fallback_winrate: number | null;
  joint_avg_pnl: number | null;
  fallback_avg_pnl: number | null;
}

export interface M7JointMatchPerDeltaBand {
  delta_target: number;
  iv_band: string;
  joint: number;
  fallback: number;
  joint_winrate: number | null;
  fallback_winrate: number | null;
  joint_avg_pnl: number | null;
  fallback_avg_pnl: number | null;
}

export interface M7JointMatchStatsResponse {
  status: 'ok' | 'no_data';
  message?: string;
  total_trades?: number;
  joint_match_count?: number;
  delta_fallback_count?: number;
  joint_match_pct?: number;
  fallback_pct?: number;
  joint_outcomes?: M7JointMatchOutcomes;
  fallback_outcomes?: M7JointMatchOutcomes;
  per_delta_target?: M7JointMatchPerDelta[];
  per_iv_band?: M7JointMatchPerBand[];
  per_delta_x_band?: M7JointMatchPerDeltaBand[];
  price_diff_distribution?: {
    p25: number;
    median: number;
    p75: number;
    p95: number;
  };
}

export function fetchM7JointMatchStats(
  dataset: M7Dataset = 'price_match',
  signal?: AbortSignal,
): Promise<M7JointMatchStatsResponse> {
  const params = new URLSearchParams();
  appendDatasetParam(params, dataset);
  const qs = params.toString();
  return jsonFetch<M7JointMatchStatsResponse>(
    `${BASE}/joint_match_stats${qs ? `?${qs}` : ''}`, signal);
}

// ── M7 Pivot Profile (segment-based per-trade peak/trough/DD per IV band) ───

export interface M7PivotProfileSegment {
  n_trades: number;
  n_trades_for_dd_pct: number;
  avg_peak_ts_ist: string | null;
  avg_peak_minute_offset: number | null;
  avg_peak_mtm_usd: number | null;
  median_peak_mtm_usd: number | null;
  p25_peak_mtm_usd: number | null;
  p75_peak_mtm_usd: number | null;
  std_peak_mtm_usd: number | null;
  n_within_1sd_peak: number;
  n_above_avg_peak: number;
  n_below_avg_peak: number;
  avg_trough_ts_ist: string | null;
  avg_trough_minute_offset: number | null;
  avg_trough_mtm_usd: number | null;
  median_trough_mtm_usd: number | null;
  p25_trough_mtm_usd: number | null;
  p75_trough_mtm_usd: number | null;
  std_trough_mtm_usd: number | null;
  n_within_1sd_trough: number;
  n_above_avg_trough: number;
  n_below_avg_trough: number;
  avg_dd_usd: number | null;
  median_dd_usd: number | null;
  std_dd_usd: number | null;
  n_within_1sd_dd: number;
  n_above_avg_dd: number;
  n_below_avg_dd: number;
  avg_dd_pct_from_peak: number | null;
  median_dd_pct_from_peak: number | null;
}

export type M7PivotByBand = Record<
  string,
  Record<'Seg1' | 'Seg2' | 'Seg3' | 'Seg4' | 'Seg5', M7PivotProfileSegment>
>;

export interface M7TradeRecord {
  trade_id: string;
  band: string;
  entry_hour_ist: number;
  friday_date_ist: string;
  net_pnl_usd: number | null;
  min_mtm_usd: number | null;
  max_mtm_usd: number | null;
  lots: number;
}

export interface M7PivotProfileResult {
  by_band: M7PivotByBand;
  by_band_winners: M7PivotByBand | null;
  by_band_losers: M7PivotByBand | null;
  losers_list: M7TradeRecord[] | null;
  best_winner: M7TradeRecord | null;
  winner_worst_drawdown: M7TradeRecord | null;
  params: {
    entry_hours: number[];
    n_total_trades: number;
    n_after_filter: number;
    n_processed?: number;
    n_winners?: number | null;
    n_losers?: number | null;
    min_trades_per_band_cell: number;
  };
}

export interface M7PivotProfileResponse {
  status: 'warming' | 'ready' | 'error';
  progress: number;
  result: M7PivotProfileResult | null;
  error: string | null;
  min_trades_per_band_cell: number;
  params_echo: { dataset: string; entry_hours: number[] };
}

export function fetchM7PivotProfile(
  entry_hours: string,
  dataset?: M7Dataset,
  signal?: AbortSignal,
): Promise<M7PivotProfileResponse> {
  const params = new URLSearchParams({ entry_hours });
  appendDatasetParam(params, dataset);
  return jsonFetch<M7PivotProfileResponse>(
    `${BASE}/pivot_profile?${params.toString()}`, signal);
}

export interface M7PivotCell {
  entry_atm_iv_band: string;
  entry_hour_ist: number;
  expiry_bucket: string;
  delta_target: number;
  rule?: Record<string, number>;
  lots?: number | null;
}

export function fetchM7PivotProfileCells(
  cells: M7PivotCell[],
  dataset?: M7Dataset,
  signal?: AbortSignal,
): Promise<M7PivotProfileResponse> {
  const params = new URLSearchParams();
  params.set('cells', JSON.stringify(cells));
  appendDatasetParam(params, dataset);
  return jsonFetch<M7PivotProfileResponse>(
    `${BASE}/pivot_profile?${params.toString()}`, signal);
}


export function fetchM7TradeDiagnostic(
  trade_id: string,
  exit_rule?: M7ExitRule,
  signal?: AbortSignal,
  dataset?: M7Dataset,
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
  appendDatasetParam(params, dataset);
  return jsonFetch<M7TradeDiagnosticResponse>(
    `${BASE}/trade_diagnostic?${params.toString()}`, signal);
}
