import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7LegSkewHeatmap } from '../../services/m7_api';
import type {
  M7ExitRule, M7Filters, M7LegSkewHeatmapResponse,
} from '../../types/m7';

// Axis options offered to the user. Restricted to fields that bucket cleanly
// (avoid friday_date_ist with 121 distinct cells; that's a different chart).
const AXIS_OPTIONS: { value: string; label: string }[] = [
  { value: 'iv_skew_bucket',      label: 'IV skew' },
  { value: 'delta_skew_bucket',   label: 'Delta skew' },
  { value: 'premium_skew_bucket', label: 'Premium skew' },
  { value: 'leg_winner',          label: 'Leg winner' },
  { value: 'loss_cause',          label: 'Loss cause' },
  { value: 'delta_target',        label: 'Δ target' },
  { value: 'entry_atm_iv_band',   label: 'IV band' },
  { value: 'entry_hour_ist',      label: 'Entry hour' },
  { value: 'expiry_bucket',       label: 'Expiry bucket' },
  { value: 'dte_bucket',          label: 'DTE bucket' },
];

// Metrics specific to leg attribution. Each item is { value, label, group }.
// Reuses the metric name vocabulary from m7_results.py.
const METRIC_OPTIONS = [
  { v: 'win_rate',                  l: 'Win rate (overall)',          g: 'Outcome' },
  { v: 'avg_net_pnl',               l: 'Avg net P&L',                 g: 'Outcome' },
  { v: 'count',                     l: 'Trade count',                 g: 'Outcome' },
  { v: 'both_share',                l: 'Both legs win (share)',       g: 'Leg outcome' },
  { v: 'call_only_share',           l: 'Call-only wins (share)',      g: 'Leg outcome' },
  { v: 'put_only_share',            l: 'Put-only wins (share)',       g: 'Leg outcome' },
  { v: 'neither_share',             l: 'Neither leg wins (share)',    g: 'Leg outcome' },
  { v: 'avg_call_leg_pnl',          l: 'Avg call-leg P&L',            g: 'Per-leg P&L' },
  { v: 'avg_put_leg_pnl',           l: 'Avg put-leg P&L',             g: 'Per-leg P&L' },
  { v: 'avg_leg_pnl_diff',          l: 'Avg call − put P&L',          g: 'Per-leg P&L' },
  { v: 'avg_call_leg_max_mtm',      l: 'Avg call-leg max MTM',        g: 'Per-leg MTM' },
  { v: 'avg_call_leg_min_mtm',      l: 'Avg call-leg min MTM',        g: 'Per-leg MTM' },
  { v: 'avg_put_leg_max_mtm',       l: 'Avg put-leg max MTM',         g: 'Per-leg MTM' },
  { v: 'avg_put_leg_min_mtm',       l: 'Avg put-leg min MTM',         g: 'Per-leg MTM' },
  // Loss-cause shares (Chunk 1 of loss-anatomy plan)
  { v: 'share_directional',         l: 'Directional losses (share)',  g: 'Loss cause' },
  { v: 'share_vol_expansion',       l: 'Vol-expansion losses (share)',g: 'Loss cause' },
  { v: 'share_path_dependent',      l: 'Path-dependent losses (share)',g: 'Loss cause' },
  { v: 'share_gamma_squeeze',       l: 'Gamma-squeeze losses (share)',g: 'Loss cause' },
  { v: 'share_skew_flip',           l: 'Skew-flip losses (share)',    g: 'Loss cause' },
  { v: 'share_unclassified',        l: 'Unclassified losses (share)', g: 'Loss cause' },
];

// Stable sort order: put-leaning → balanced → call-leaning. For numeric or
// IV-band axes, falls back to natural ordering.
const SKEW_ORDER: Record<string, string[]> = {
  iv_skew_bucket: ['put_iv_strong', 'put_iv', 'balanced', 'call_iv', 'call_iv_strong'],
  delta_skew_bucket: ['put_richer_strong', 'put_richer', 'balanced', 'call_richer', 'call_richer_strong'],
  premium_skew_bucket: ['put_premium_strong', 'put_premium', 'balanced', 'call_premium', 'call_premium_strong'],
  leg_winner: ['both', 'call_only', 'put_only', 'neither'],
  loss_cause: ['directional', 'vol_expansion', 'path_dependent',
               'gamma_squeeze', 'skew_flip', 'unclassified'],
};

function sortValues(axis: string, vals: string[]): string[] {
  if (axis in SKEW_ORDER) {
    const order = SKEW_ORDER[axis];
    return [...vals].sort((a, b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      if (ia >= 0 && ib >= 0) return ia - ib;
      if (ia >= 0) return -1;
      if (ib >= 0) return 1;
      return a.localeCompare(b);
    });
  }
  // IV-band ("100+" → max; "20-30" → lower bound)
  return [...vals].sort((a, b) => {
    const score = (v: string): number => {
      if (v === '100+') return 1000;
      const m = v.match(/^(\d+)-(\d+)$/);
      if (m) return Number(m[1]);
      const n = Number(v);
      return isFinite(n) ? n : Number.MAX_SAFE_INTEGER;
    };
    return score(a) - score(b);
  });
}

// Pick a color scale appropriate for the metric.
function colorScale(metric: string, v: number | null, minV: number, maxV: number): string {
  if (v == null || isNaN(v)) return '#0d1421';
  const isPnl = /pnl|mtm/.test(metric) && metric !== 'win_rate';
  const span = Math.max(Math.abs(minV), Math.abs(maxV)) || 1;
  if (isPnl) {
    // Diverging green/red centered at 0
    const t = Math.min(1, Math.abs(v) / span);
    return v >= 0
      ? `rgba(63, 185, 80, ${0.15 + 0.55 * t})`
      : `rgba(248, 81, 73, ${0.15 + 0.55 * t})`;
  }
  // Sequential blue for shares, win rate, count
  const t = (v - minV) / Math.max(1e-9, maxV - minV);
  return `rgba(31, 111, 235, ${0.10 + 0.55 * t})`;
}

// Format a metric value for display.
function formatValue(metric: string, v: number | null): string {
  if (v == null || isNaN(v)) return '—';
  if (metric.endsWith('_share') || metric === 'win_rate') {
    return `${(v * 100).toFixed(0)}%`;
  }
  if (metric === 'count') return String(Math.round(v));
  return `$${v.toFixed(1)}`;
}

interface Props {
  filters: M7Filters;
  exitRule: M7ExitRule;
}

export function M7LegSkewHeatmap({ filters, exitRule }: Props) {
  const [rowKey, setRowKey] = useState('iv_skew_bucket');
  const [colKey, setColKey] = useState('delta_skew_bucket');
  const [metric, setMetric] = useState('win_rate');
  const [data, setData] = useState<M7LegSkewHeatmapResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7LegSkewHeatmap({ ...filters, row_key: rowKey, col_key: colKey, metric }, exitRule, ac.signal)
      .then(d => { if (active) setData(d); })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(filters), JSON.stringify(exitRule), rowKey, colKey, metric]);

  const { rowVals, colVals, lookup, minV, maxV } = useMemo(() => {
    const rows = data?.rows || [];
    const rv = sortValues(rowKey, Array.from(new Set(rows.map(r => String(r[rowKey])))));
    const cv = sortValues(colKey, Array.from(new Set(rows.map(r => String(r[colKey])))));
    const m = new Map<string, { value: number | null; n: number }>();
    let lo = Infinity, hi = -Infinity;
    for (const r of rows) {
      const v = r.value == null ? null : Number(r.value);
      m.set(`${r[rowKey]}|${r[colKey]}`, { value: v, n: Number(r.n_trades) });
      if (v != null && !isNaN(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    if (!isFinite(lo)) lo = 0;
    if (!isFinite(hi)) hi = 0;
    return { rowVals: rv, colVals: cv, lookup: m, minV: lo, maxV: hi };
  }, [data, rowKey, colKey]);

  const sty = {
    bg: '#0d1421', bd: '1px solid #1a2d42', tx: '#cfd9e3', mu: '#7a9bb5',
  };

  // Group metric options for the <optgroup>s
  const metricGroups = useMemo(() => {
    const grp: Record<string, typeof METRIC_OPTIONS> = {};
    for (const o of METRIC_OPTIONS) (grp[o.g] ||= []).push(o);
    return grp;
  }, []);

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 10, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 10, gap: 12, flexWrap: 'wrap',
      }}>
        <div style={{ fontSize: 13, color: sty.tx, fontWeight: 600 }}>
          Leg Attribution — skew × outcome heatmap
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11 }}>
          <span style={{ color: sty.mu }}>Rows:</span>
          <select value={rowKey} onChange={e => setRowKey(e.target.value)}
                  style={{ background: sty.bg, color: sty.tx, border: sty.bd, padding: '3px 6px' }}>
            {AXIS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <span style={{ color: sty.mu }}>Cols:</span>
          <select value={colKey} onChange={e => setColKey(e.target.value)}
                  style={{ background: sty.bg, color: sty.tx, border: sty.bd, padding: '3px 6px' }}>
            {AXIS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <span style={{ color: sty.mu }}>Metric:</span>
          <select value={metric} onChange={e => setMetric(e.target.value)}
                  style={{ background: sty.bg, color: sty.tx, border: sty.bd, padding: '3px 6px', minWidth: 200 }}>
            {Object.entries(metricGroups).map(([g, opts]) => (
              <optgroup key={g} label={g}>
                {opts.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
              </optgroup>
            ))}
          </select>
          <span style={{ color: sty.mu, marginLeft: 8 }}>
            {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span>
              : `${data?.rows.length || 0} cells`}
          </span>
        </div>
      </div>
      {!loading && !err && data && data.rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{
            borderCollapse: 'collapse', fontSize: 11,
            fontVariantNumeric: 'tabular-nums',
          }}>
            <thead>
              <tr>
                <th style={{ padding: '4px 8px', color: sty.mu, textAlign: 'left' }}>
                  {rowKey} \ {colKey}
                </th>
                {colVals.map(c => (
                  <th key={c} style={{
                    padding: '4px 8px', color: sty.mu, textAlign: 'center', minWidth: 80,
                  }}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rowVals.map(rv => (
                <tr key={rv}>
                  <td style={{ padding: '4px 8px', color: sty.tx, fontWeight: 600 }}>{rv}</td>
                  {colVals.map(cv => {
                    const cell = lookup.get(`${rv}|${cv}`);
                    const v = cell?.value ?? null;
                    return (
                      <td key={cv} style={{
                        padding: '4px 6px', textAlign: 'center', color: sty.tx,
                        background: colorScale(metric, v, minV, maxV),
                        border: sty.bd,
                      }}>
                        {formatValue(metric, v)}
                        {cell && <div style={{ fontSize: 9, color: sty.mu }}>n={cell.n}</div>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ fontSize: 10, color: sty.mu, marginTop: 8 }}>
        Skew sign convention: <strong>call − put</strong>. "call_iv_strong" = call IV ≥ 5 pct points
        above put IV. "call_richer_strong" = |call Δ| ≥ |put Δ| + 0.05.
      </div>
    </div>
  );
}
