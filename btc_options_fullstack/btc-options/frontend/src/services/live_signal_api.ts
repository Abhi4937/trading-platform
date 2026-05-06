/**
 * LiveSignal API client.
 *
 * Mirrors the backend `LiveSignalCandidate` dataclass field-for-field. Polling
 * is handled by a small hook; cache is server-side (5s TTL) so a 7s client
 * cadence won't re-trigger work for every poll.
 */
import { useEffect, useRef, useState } from 'react';

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) || 'http://localhost:8000';

export interface LiveSignalCandidate {
  ts: number;
  expiry: string;             // YYYY-MM-DD
  expiry_ts: number;
  dte_days: number;
  target_delta: number;
  call_strike: number;
  put_strike: number;
  call_mark: number;
  put_mark: number;
  call_iv: number;            // decimal
  put_iv: number;
  call_delta: number;
  put_delta: number;
  spot: number;
  total_credit: number;
  credit_usd: number;
  credit_pct: number;
  margin_used_usd: number | null;
  // Analytics
  quality_score: number | null;
  quality_source: 'calibrated_v2' | 'calibrated' | 'fallback_ivp_credit' | null;
  size_band: 'strong' | 'standard' | 'marginal' | 'skip' | null;
  pattern: string | null;
  fair_credit_at_ivp: number | null;
  structural_credit_pct: number | null;
  iv_regime_premium_pct: number | null;
  excess_over_fair_pct: number | null;
  z_credit_pct: number | null;
  theta_vega_ratio: number | null;
  gamma_theta_dollar: number | null;
  // v2 calibration
  overall_winrate: number | null;
  pattern_winrate: number | null;
  n_trades_in_bucket: number | null;
  // Hard filters
  flt_ivp_gt50: boolean;
  flt_iv_rv_spread_pos: boolean;
  flt_adx_lt30: boolean;
  flt_dte_5_14: boolean;
  flt_gex_ok: boolean;
  flt_all_pass: boolean;
}

export interface ScanResponse {
  ts: number;
  count: number;
  total_scanned: number;
  candidates: LiveSignalCandidate[];
}

export async function fetchScan(opts: {
  topN?: number; onlyPassing?: boolean;
} = {}): Promise<ScanResponse> {
  const params = new URLSearchParams();
  if (opts.topN) params.set('top_n', String(opts.topN));
  if (opts.onlyPassing) params.set('only_passing', 'true');
  const url = `${API_BASE}/api/v1/live-signal/scan${params.toString() ? `?${params}` : ''}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`live-signal scan ${r.status}`);
  return r.json();
}

/** Polling hook — refetches every `intervalMs` (default 7s). Returns the
 * most recent payload, loading flag, and error. */
export function useLiveSignalScan(opts: {
  intervalMs?: number; topN?: number; onlyPassing?: boolean; enabled?: boolean;
} = {}): { data: ScanResponse | null; loading: boolean; error: string | null; refetch: () => void } {
  const { intervalMs = 7000, topN, onlyPassing = false, enabled = true } = opts;
  const [data, setData] = useState<ScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelled = useRef(false);

  const tick = async () => {
    if (cancelled.current) return;
    setLoading(true);
    try {
      const resp = await fetchScan({ topN, onlyPassing });
      if (cancelled.current) return;
      setData(resp);
      setError(null);
    } catch (e: any) {
      if (cancelled.current) return;
      setError(String(e?.message ?? e));
    } finally {
      if (!cancelled.current) setLoading(false);
    }
  };

  useEffect(() => {
    cancelled.current = false;
    if (!enabled) return;
    tick();
    const id = window.setInterval(tick, intervalMs);
    return () => { cancelled.current = true; window.clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, topN, onlyPassing]);

  return { data, loading, error, refetch: tick };
}
