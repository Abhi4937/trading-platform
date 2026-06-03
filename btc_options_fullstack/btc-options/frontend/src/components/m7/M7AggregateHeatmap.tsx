import React, { useEffect, useState } from 'react';
import { fetchM7Aggregate, type M7Dataset } from '../../services/m7_api';
import type { M7AggregateRow, M7ExitRule, M7Filters } from '../../types/m7';

interface Props {
  title: string;
  rowKey: string;
  colKey: string;
  metric: string;       // e.g. 'avg_net_pnl', 'win_rate', 'count'
  filters: M7Filters;
  exitRule: M7ExitRule;
  fmt?: (v: number) => string;
  // When set, the /aggregate call is parameterized to this dataset. Without it
  // the request hits the default (delta_match) parquet — wrong for calendar.
  dataset?: M7Dataset;
  // Optional scope: aggregate by a THIRD dimension (scopeKey) and keep only the
  // rows where scopeKey === scopeVal, client-side. Used to scope a heatmap to one
  // gap bucket WITHOUT a backend filter — gap labels like "[5,10)" contain commas
  // that the /aggregate filter parser splits on (it would never match).
  scopeKey?: string;
  scopeVal?: string;
  // Optional friendly axis labels for the corner cell (default to the raw keys).
  rowLabel?: string;
  colLabel?: string;
}

export function M7AggregateHeatmap({
  title, rowKey, colKey, metric, filters, exitRule, fmt, dataset, scopeKey, scopeVal,
  rowLabel, colLabel,
}: Props) {
  const [rows, setRows] = useState<M7AggregateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    const dims = scopeKey ? `${rowKey},${colKey},${scopeKey}` : `${rowKey},${colKey}`;
    fetchM7Aggregate({ ...filters, dimensions: dims, metric }, exitRule, dataset)
      .then(r => {
        const all = r.rows;
        setRows(scopeKey && scopeVal != null
          ? all.filter(x => String(x[scopeKey]) === scopeVal)
          : all);
      })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [JSON.stringify(filters), JSON.stringify(exitRule), rowKey, colKey, metric, dataset, scopeKey, scopeVal]);

  // Build pivot — smart sort that handles numeric values, IV bands ("100+" → last),
  // and expiry buckets in chronological order.
  const sortValues = (vals: string[]): string[] => {
    const score = (v: string): number => {
      // IV band like "100+" → max
      if (v === '100+') return 1000;
      // Gap bucket like "[5,10)" / "[10,15)" → lower bound (calendar)
      const gapMatch = v.match(/^\[(\d+),/);
      if (gapMatch) return Number(gapMatch[1]);
      // IV band like "30-40" → use lower bound
      const ivMatch = v.match(/^(\d+)-(\d+)$/);
      if (ivMatch) return Number(ivMatch[1]);
      // Expiry bucket — chronological
      const expOrder: Record<string, number> = {
        'current (Sat)': 1, 'next (Sun)': 2, 'next_to_next (Mon)': 3,
        'weekly (7d)': 4, 'biweekly (14d)': 5, 'monthly (30d)': 6, 'quarterly': 7,
      };
      if (v in expOrder) return expOrder[v];
      // DTE bucket like "0-3", "3-7", "7-14"
      const dteMatch = v.match(/^(\d+)-(\d+)$/);
      if (dteMatch) return Number(dteMatch[1]);
      // Pure number
      const n = Number(v);
      if (isFinite(n)) return n;
      return Number.MAX_SAFE_INTEGER;
    };
    return [...vals].sort((a, b) => {
      const d = score(a) - score(b);
      // Tie (e.g. two non-numeric pair labels) → stable alphabetical order.
      return d !== 0 ? d : a.localeCompare(b);
    });
  };
  const rowVals = sortValues(Array.from(new Set(rows.map(r => String(r[rowKey])))));
  const colVals = sortValues(Array.from(new Set(rows.map(r => String(r[colKey])))));
  const lookup = new Map<string, M7AggregateRow>();
  for (const r of rows) lookup.set(`${r[rowKey]}|${r[colKey]}`, r);
  const values = rows.map(r => Number(r.value)).filter(v => !isNaN(v));
  const minV = values.length ? Math.min(...values) : 0;
  const maxV = values.length ? Math.max(...values) : 0;
  const span = Math.max(Math.abs(minV), Math.abs(maxV)) || 1;

  const colorFor = (v: number | null) => {
    if (v == null || isNaN(v)) return '#0d1421';
    // P&L and return/margin metrics are signed → diverging green/red ramp
    // centered on zero. win_rate / count use a sequential blue ramp.
    const isDiverging = metric.includes('pnl') || metric.includes('return');
    if (isDiverging) {
      const t = Math.min(1, Math.abs(v) / span);
      return v >= 0
        ? `rgba(63, 185, 80, ${0.15 + 0.55 * t})`
        : `rgba(248, 81, 73, ${0.15 + 0.55 * t})`;
    }
    // win_rate or count
    const t = (v - minV) / Math.max(1e-9, maxV - minV);
    return `rgba(31, 111, 235, ${0.10 + 0.55 * t})`;
  };

  const f = fmt || ((v: number) => v.toFixed(2));

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 10, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8,
      }}>
        <div style={{ fontSize: 13, color: '#cfd9e3', fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 11, color: '#7a9bb5' }}>
          {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span>
            : `${rows.length} cells`}
        </div>
      </div>
      {!loading && !err && rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
            <thead>
              <tr>
                <th style={{ padding: '4px 8px', color: '#7a9bb5', textAlign: 'left' }}>{rowLabel ?? rowKey} \ {colLabel ?? colKey}</th>
                {colVals.map(c => (
                  <th key={c} style={{
                    padding: '4px 8px', color: '#7a9bb5', textAlign: 'center',
                    minWidth: 60,
                  }}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rowVals.map(rv => (
                <tr key={rv}>
                  <td style={{ padding: '4px 8px', color: '#cfd9e3', fontWeight: 600 }}>{rv}</td>
                  {colVals.map(cv => {
                    const r = lookup.get(`${rv}|${cv}`);
                    const v = r ? Number(r.value) : null;
                    return (
                      <td key={cv} style={{
                        padding: '4px 6px', textAlign: 'center', color: '#cfd9e3',
                        background: colorFor(v),
                        border: '1px solid #1a2d42',
                      }}>
                        {v == null ? '—' : f(v)}
                        {r && <div style={{ fontSize: 9, color: '#7a9bb5' }}>n={r.n_trades}</div>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
