/**
 * M6 (m4_*) batch results API client. Backed by /api/v1/m4/* endpoints.
 */
const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) || 'http://localhost:8000';

export interface M4Summary {
  n_trades: number;
  n_winners: number;
  win_rate: number;
  total_net_pnl_usd: number;
  avg_net_pnl_usd: number;
  sl_hit_rate: number;
  avg_credit_pct: number;
  avg_max_mtm_usd: number;
  avg_min_mtm_usd: number;
}

export interface M4Filters {
  delta_target?: string;
  dte_bucket?: string;
  spot_bucket?: string;
  ivp_bucket?: string;
  ctx_pattern?: string;
  outcome?: 'win' | 'loss';
  exit_reason?: string;
  flt_dte_5_14?: 'true' | 'false';
}

const filtersToQuery = (f?: M4Filters): string => {
  if (!f) return '';
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v) u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `&${s}` : '';
};

export async function fetchSummary(f?: M4Filters): Promise<M4Summary> {
  const r = await fetch(`${API_BASE}/api/v1/m4/summary?_=1${filtersToQuery(f)}`);
  if (!r.ok) throw new Error(`m4 summary ${r.status}`);
  return r.json();
}

export interface AggregateRow {
  [dim: string]: any;
  n: number;
}
export interface AggregateResp {
  dimension: string[];
  metric: string;
  rows: AggregateRow[];
}

export async function fetchAggregate(opts: {
  dimension: string[]; metric: string; filters?: M4Filters;
}): Promise<AggregateResp> {
  const params = new URLSearchParams();
  opts.dimension.forEach(d => params.append('dimension', d));
  params.set('metric', opts.metric);
  if (opts.filters) {
    for (const [k, v] of Object.entries(opts.filters)) {
      if (v) params.set(k, String(v));
    }
  }
  const r = await fetch(`${API_BASE}/api/v1/m4/aggregate?${params}`);
  if (!r.ok) throw new Error(`m4 aggregate ${r.status}`);
  return r.json();
}

export interface ScatterPoint {
  trade_id: string;
  [k: string]: any;
}
export interface ScatterResp {
  x: string; y: string; color_by: string | null;
  n: number; points: ScatterPoint[];
}

export async function fetchScatter(opts: {
  x: string; y: string; colorBy?: string; limit?: number; filters?: M4Filters;
}): Promise<ScatterResp> {
  const params = new URLSearchParams({
    x: opts.x, y: opts.y,
    limit: String(opts.limit ?? 2000),
  });
  if (opts.colorBy) params.set('color_by', opts.colorBy);
  if (opts.filters) {
    for (const [k, v] of Object.entries(opts.filters)) {
      if (v) params.set(k, String(v));
    }
  }
  const r = await fetch(`${API_BASE}/api/v1/m4/scatter?${params}`);
  if (!r.ok) throw new Error(`m4 scatter ${r.status}`);
  return r.json();
}

export interface CalibrationBucket {
  bucket: number; n: number;
  credit_pct_min: number; credit_pct_max: number;
  win_rate: number; avg_net_pnl: number;
}
export async function fetchQualityCalibration(nBuckets = 10):
  Promise<{ buckets: CalibrationBucket[] }> {
  const r = await fetch(`${API_BASE}/api/v1/m4/quality_calibration?n_buckets=${nBuckets}`);
  if (!r.ok) throw new Error(`m4 quality_calibration ${r.status}`);
  return r.json();
}

export interface M4PathSnapshot {
  trade_id: string; ts: number; spot: number;
  call_mark: number; put_mark: number;
  call_delta: number; put_delta: number;
  call_iv: number; put_iv: number;
  pnl_gross_usd: number; pnl_net_usd: number;
}
export async function fetchPath(tradeId: string):
  Promise<{ trade_id: string; n: number; snapshots: M4PathSnapshot[] }> {
  const r = await fetch(`${API_BASE}/api/v1/m4/path?trade_id=${tradeId}`);
  if (!r.ok) throw new Error(`m4 path ${r.status}`);
  return r.json();
}

// ── Expiry × IV × Δ grid ─────────────────────────────────────────────────────

export interface ContractTypeSummaryRow {
  contract_type: string;
  n_trades: number; n_fridays: number; n_expiries: number;
  avg_dte: number;
  win_rate: number; sl_rate: number;
  n_wins: number; n_losses: number;
  // SL-only stats
  n_sl: number;
  sl_mtm_avg: number; sl_mtm_total: number;
  sl_net_avg: number; sl_net_total: number;
  // P&L: overall avg, conditional avgs (winners-only / losers-only),
  // single-trade extremes, and total
  avg_net_pnl: number;
  avg_net_win: number; avg_net_loss: number;
  total_net_pnl: number;
  best_net_pnl: number; worst_net_pnl: number;
  avg_gross_pnl: number;
  avg_max_mtm: number; best_max_mtm: number;
  avg_min_mtm: number; worst_min_mtm: number;
  avg_cost: number;
}

export interface ExpiryGridCell {
  contract_type: string;
  iv_band: string;
  delta_target: number;
  n: number;
  win_rate: number; sl_rate: number;
  // SL-only stats: when SL hit, what was the MTM at trigger and the net P&L
  n_sl: number;
  sl_mtm_avg: number;  sl_mtm_total: number;
  sl_net_avg: number;  sl_net_total: number;
  // Winners-only split (deepest dip, best/avg/lowest win)
  n_wins: number;
  win_min_mtm_worst: number;  win_min_mtm_avg: number;
  win_net_best: number;       win_net_avg: number;       win_net_lowest: number;
  // Losers-only split (highest peak, worst/avg/lowest loss + MAE)
  n_losses: number;
  loss_max_mtm_best: number;  loss_max_mtm_avg: number;
  loss_net_worst: number;     loss_net_avg: number;      loss_net_lowest: number;
  loss_min_mtm_worst: number; loss_min_mtm_avg: number;
  // MTM
  max_mtm_avg: number; max_mtm_best: number;
  min_mtm_avg: number; min_mtm_worst: number;
  // P&L
  gross_pnl_avg: number; gross_pnl_sum: number;
  net_pnl_avg: number;   net_pnl_sum: number;
  // Costs (round-trip totals; per-side = total/2 estimate)
  slippage_avg_total: number;  slippage_avg_per_side: number;
  brokerage_avg_total: number; brokerage_avg_per_side: number;
  cost_avg_total: number;
  // Other
  credit_pct_avg: number; credit_usd_avg: number; margin_avg: number;
  this_expiry_atm_iv_avg_pct: number; dte_avg: number;
}

export async function fetchContractTypeSummary():
  Promise<{ rows: ContractTypeSummaryRow[] }> {
  const r = await fetch(`${API_BASE}/api/v1/m4/contract_type_summary`);
  if (!r.ok) throw new Error(`m4 contract_type_summary ${r.status}`);
  return r.json();
}

export async function fetchExpiryGrid(opts: {
  contractTypes?: string[]; expiries?: string[]; minN?: number;
} = {}):
  Promise<{ n_rows: number; contract_order: string[]; iv_bands: string[]; rows: ExpiryGridCell[] }> {
  const params = new URLSearchParams();
  if (opts.contractTypes && opts.contractTypes.length)
    params.set('contract_types', opts.contractTypes.join(','));
  if (opts.expiries && opts.expiries.length)
    params.set('expiries', opts.expiries.join(','));
  if (opts.minN !== undefined) params.set('min_n', String(opts.minN));
  const url = `${API_BASE}/api/v1/m4/expiry_grid${params.toString() ? `?${params}` : ''}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`m4 expiry_grid ${r.status}`);
  return r.json();
}

// ── Expiry list (for search dropdown) ────────────────────────────────────────

export interface ExpiryListEntry {
  expiry: string;
  contract_type: string;
  n_trades: number;
  n_entries: number;
  atm_iv_avg_pct: number | null;
}

export async function fetchExpiryList(): Promise<{ expiries: ExpiryListEntry[] }> {
  const r = await fetch(`${API_BASE}/api/v1/m4/expiry_list`);
  if (!r.ok) throw new Error(`m4 expiry_list ${r.status}`);
  return r.json();
}

// ── Best-Δ winners per (contract_type × IV band) ─────────────────────────────

export interface ExpiryWinnerCell {
  delta_target: number;
  n: number;
  avg_net: number;
  win_rate: number;
  total_net: number;
}

export interface ExpiryWinnerRow {
  contract_type: string;
  iv_band: string;
  n_cells: number;
  best: ExpiryWinnerCell;
  worst: ExpiryWinnerCell;
}

export async function fetchExpiryWinners(minN = 2):
  Promise<{ rows: ExpiryWinnerRow[] }> {
  const r = await fetch(`${API_BASE}/api/v1/m4/expiry_winners?min_n=${minN}`);
  if (!r.ok) throw new Error(`m4 expiry_winners ${r.status}`);
  return r.json();
}

// ── Attribution: winners-vs-losers, per-Friday best, win-frequency ──────────

export interface IndicatorMeta {
  column: string;
  label: string;
  category: string;
}

export interface IndicatorComparison {
  avg_win: number | null;
  avg_loss: number | null;
  gap: number | null;
  sigma: number;
  discriminating: boolean;
}

export interface WinnersVsLosersRow {
  contract_type: string;
  n_win: number; n_loss: number;
  win_rate: number;
  avg_net_win: number;
  avg_net_loss: number;
  indicators: Record<string, IndicatorComparison>;
}

export async function fetchWinnersVsLosers(opts: {
  delta: number; contractTypes?: string[]; discriminateSigma?: number;
}): Promise<{
  delta: number;
  indicators: IndicatorMeta[];
  rows: WinnersVsLosersRow[];
}> {
  const params = new URLSearchParams({ delta: String(opts.delta) });
  if (opts.contractTypes?.length)
    params.set('contract_types', opts.contractTypes.join(','));
  if (opts.discriminateSigma !== undefined)
    params.set('discriminate_sigma', String(opts.discriminateSigma));
  const r = await fetch(`${API_BASE}/api/v1/m4/winners_vs_losers?${params}`);
  if (!r.ok) throw new Error(`m4 winners_vs_losers ${r.status}`);
  return r.json();
}

export interface FridayTradeSummary {
  contract_type: string;
  expiry_date: string;
  net_pnl_usd: number;
  outcome: string;
  credit_pct: number | null;
  indicators: Record<string, number>;
}

export interface DecidingIndicator {
  column: string;
  winner_value: number;
  loser_value: number;
  gap: number;
  score_sigma: number;
}

export interface PerFridayBestRow {
  entry_date_ist: string;
  n_contracts: number;
  winner: FridayTradeSummary;
  runner_up: FridayTradeSummary | null;
  loser: FridayTradeSummary | null;
  deciding_indicators: DecidingIndicator[];
}

export async function fetchPerFridayBest(opts: {
  delta: number; nDeciding?: number;
}): Promise<{
  delta: number;
  n_fridays: number;
  rows: PerFridayBestRow[];
}> {
  const params = new URLSearchParams({ delta: String(opts.delta) });
  if (opts.nDeciding !== undefined) params.set('n_deciding', String(opts.nDeciding));
  const r = await fetch(`${API_BASE}/api/v1/m4/per_friday_best?${params}`);
  if (!r.ok) throw new Error(`m4 per_friday_best ${r.status}`);
  return r.json();
}

export interface WinFrequencyRow {
  contract_type: string;
  n_wins: number;
  n_appears: number;
  win_frequency: number;
  appear_frequency: number;
  avg_net_when_winning: number;
}

export async function fetchWinFrequency(delta: number):
  Promise<{ delta: number; n_fridays: number; rows: WinFrequencyRow[] }> {
  const r = await fetch(`${API_BASE}/api/v1/m4/win_frequency?delta=${delta}`);
  if (!r.ok) throw new Error(`m4 win_frequency ${r.status}`);
  return r.json();
}
