// M9 dashboard API — Friday-entry weekly + biweekly. Sibling to m_month_api.ts.

export type M9ExpiryType = 'weekly' | 'biweekly' | 'all';

export type M9Meta = {
  ready: boolean;
  expiry_types: string[];
  deltas: number[];
  iv_bands: string[];
  entry_yyyymms: string[];
  entry_hours: number[];
  entry_minutes: number[];
  entry_time_labels: string[];
  entry_fridays: string[];
  expiry_dates: string[];
  dte_buckets?: string[];
  ivp_buckets?: string[];
  n_trades: number;
  first_friday: string | null;
  last_friday: string | null;
};

export type M9Summary = {
  ready: boolean;
  expiry_type: string;
  n_trades: number;
  n_fridays: number;
  n_expiry_types: number;
  avg_credit_usd: number;
  avg_margin_usd: number;
  avg_dte_days: number;
};

export type M9BestComboRow = {
  expiry_type: string;
  iv_band: string;
  delta_target: number;
  entry_hour_ist: number;
  entry_minute_ist: number;
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
  avg_wait_minutes: number | null;
  avg_match_quality: number | null;
  n_premium_sl: number | null;
  n_max_profit: number | null;
  n_margin_target: number | null;
  n_fixed_hold: number | null;
  n_natural: number | null;
};

export type M9BestComboResponse = {
  primary: string;
  secondary: string | null;
  expiry_type: string;
  hold_duration: string;
  premium_sl_pct: number | null;
  max_profit_pct: number | null;
  margin_target_pct: number | null;
  n_trades_after_filters: number;
  n_cells_in_grid: number;
  best_per_band: M9BestComboRow[];
  grid?: M9BestComboRow[];
};

export type M9IvBandSummaryRow = {
  iv_band: string;
  n_trades: number;
  avg_credit_usd: number;
  avg_margin_usd: number;
  avg_dte_days: number;
};

export type M9MissedSession = {
  entry_friday_ist: string;
  reason: string;
};

const BASE = '/api/v1/m9';

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

export const m9_api = {
  fetchMeta: () => _get<M9Meta>('/meta'),
  fetchSummary: (expiry_type?: string) =>
    _get<M9Summary>('/summary', { expiry_type }),
  fetchIvBandSummary: (expiry_type?: string) =>
    _get<{ rows: M9IvBandSummaryRow[]; metric: string }>(
      '/iv_band_summary', { expiry_type }),
  fetchBestCombo: (params: {
    expiry_type?: string;
    hold_duration?: string;
    premium_sl_pct?: number;
    max_profit_pct?: number;
    margin_target_pct?: number;
    iv_band?: string;
    delta_target?: string;
    entry_hour?: string;
    entry_minute?: string;
    entry_friday?: string;
    dte_bucket?: string;
    ivp_bucket?: string;
    primary?: string;
    secondary?: string;
    include_grid?: boolean;
  }) =>
    _get<M9BestComboResponse>('/iv_band_best_combo', {
      expiry_type: params.expiry_type,
      hold_duration: params.hold_duration,
      premium_sl_pct: params.premium_sl_pct,
      max_profit_pct: params.max_profit_pct,
      margin_target_pct: params.margin_target_pct,
      iv_band: params.iv_band,
      delta_target: params.delta_target,
      entry_hour: params.entry_hour,
      entry_minute: params.entry_minute,
      entry_friday: params.entry_friday,
      dte_bucket: params.dte_bucket,
      ivp_bucket: params.ivp_bucket,
      primary: params.primary,
      secondary: params.secondary,
      include_grid: params.include_grid ? 'true' : undefined,
    }),
  fetchMissedSessions: (expiry_type?: string) =>
    _get<{ missed: M9MissedSession[]; total_fridays_expected: number;
           total_fridays_with_trades: number; }>(
      '/missed_sessions', { expiry_type }),
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
