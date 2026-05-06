// Typed wrappers for the M7 sweep endpoints (mounted at /api/v1/m7).

import type {
  M7AggregateResponse, M7BestComboMarkersResponse, M7BestComboRow,
  M7CostBreakdown, M7ExitRule, M7Filters,
  M7IvBandSummaryRow, M7Meta, M7MissedFridaysResponse, M7PathResponse,
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
