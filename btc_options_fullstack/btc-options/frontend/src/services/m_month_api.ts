// M-Month dashboard API — sibling to m7_api.ts.

export type MMonthTradeCycle = 'monthly' | 'bimonthly' | 'lastfri_monthly' | 'lastfri_bimonthly' | 'all';

export type MMonthMeta = {
  ready: boolean;
  cycles: string[];
  deltas: number[];
  iv_bands: string[];
  entry_months: string[];
  entry_hours: number[];
  expiry_dates: string[];
  expiry_variants?: string[];
  anchor_dates?: string[];
  dte_buckets?: string[];
  ivp_buckets?: string[];
  entry_dows?: string[];
  n_trades: number;
  first_anchor_date: string | null;
  last_anchor_date: string | null;
};

export type MMonthSummary = {
  ready: boolean;
  trade_cycle: string;
  n_trades: number;
  n_anchors: number;
  n_cycles: number;
  avg_credit_usd: number;
  avg_margin_usd: number;
  avg_dte_days: number;
};

export type MMonthBestComboRow = {
  trade_cycle: string;
  iv_band: string;
  delta_target: number;
  entry_dow: string;
  entry_hour_ist: number;
  hold_duration: string;
  n_trades: number;
  n_wins: number;
  n_losses: number;
  win_rate: number | null;
  avg_net_pnl: number | null;
  total_net_pnl: number | null;
  avg_credit_usd: number | null;
  avg_margin_usd: number | null;
  avg_dte_days: number | null;
  avg_pct_return_on_credit: number | null;
  avg_pct_return_on_margin: number | null;
  avg_max_mtm: number | null;
  avg_min_mtm: number | null;
  min_mtm_usd: number | null;
  max_mtm_usd: number | null;
  avg_realized_hold_hours: number | null;
  pct_clipped_to_expiry: number | null;
};

export type MMonthBestComboResponse = {
  primary: string;
  secondary: string | null;
  trade_cycle: string;
  hold_duration: string;
  premium_sl_pct: number | null;
  max_profit_pct: number | null;
  margin_target_pct: number | null;
  n_cells_in_grid: number;
  best_per_band: MMonthBestComboRow[];
  grid?: MMonthBestComboRow[];
};

export type MMonthIvBandSummaryRow = {
  iv_band: string;
  n_trades: number;
  avg_credit_usd: number;
  avg_margin_usd: number;
  avg_dte_days: number;
};

export type MMonthMissedSession = {
  anchor_date: string;
  reason: string;
};

const BASE = '/api/v1/m_month';

async function _get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, String(v));
      }
    }
  }
  const resp = await fetch(url.toString());
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`${path} → HTTP ${resp.status}: ${txt}`);
  }
  return resp.json() as Promise<T>;
}

export const m_month_api = {
  fetchMeta: () => _get<MMonthMeta>('/meta'),
  fetchSummary: (trade_cycle?: string) =>
    _get<MMonthSummary>('/summary', { trade_cycle }),
  fetchIvBandSummary: (trade_cycle?: string) =>
    _get<{ rows: MMonthIvBandSummaryRow[]; metric: string }>(
      '/iv_band_summary', { trade_cycle }),
  fetchBestCombo: (params: {
    trade_cycle?: string;
    hold_duration?: string;
    premium_sl_pct?: number;
    max_profit_pct?: number;
    margin_target_pct?: number;
    // Filter-bar params (comma-separated CSV per backend signature)
    iv_band?: string;
    delta_target?: string;
    entry_hour?: string;
    expiry_variant?: string;
    anchor_date?: string;
    dte_bucket?: string;
    ivp_bucket?: string;
    entry_dow?: string;
    primary?: string;
    secondary?: string;
    include_grid?: boolean;
  }) =>
    _get<MMonthBestComboResponse>('/iv_band_best_combo', {
      trade_cycle: params.trade_cycle,
      hold_duration: params.hold_duration,
      premium_sl_pct: params.premium_sl_pct,
      max_profit_pct: params.max_profit_pct,
      margin_target_pct: params.margin_target_pct,
      iv_band: params.iv_band,
      delta_target: params.delta_target,
      entry_hour: params.entry_hour,
      expiry_variant: params.expiry_variant,
      anchor_date: params.anchor_date,
      dte_bucket: params.dte_bucket,
      ivp_bucket: params.ivp_bucket,
      entry_dow: params.entry_dow,
      primary: params.primary,
      secondary: params.secondary,
      include_grid: params.include_grid ? 'true' : undefined,
    }),
  fetchMissedSessions: (trade_cycle?: string) =>
    _get<{ missed: MMonthMissedSession[]; total_anchors_expected: number;
           total_anchors_with_trades: number; }>(
      '/missed_sessions', { trade_cycle }),
  fetchAvailablePrimaryMetrics: () =>
    _get<{
      primaries: string[];
      directions: Record<string, string>;
      hold_durations: string[];
      premium_sl_pct_menu: number[];
      max_profit_pct_menu: number[];
      margin_target_pct_menu: number[];
    }>('/available_primary_metrics'),
};
