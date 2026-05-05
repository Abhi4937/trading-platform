import React, { useEffect, useState } from 'react';
import { fetchM7Aggregate } from '../../services/m7_api';
import type { M7AggregateRow, M7ExitRule, M7Filters } from '../../types/m7';

interface Props {
  title: string;
  rowKey: string;
  colKey: string;
  metric: string;       // e.g. 'avg_net_pnl', 'win_rate', 'count'
  filters: M7Filters;
  exitRule: M7ExitRule;
  fmt?: (v: number) => string;
}

export function M7AggregateHeatmap({
  title, rowKey, colKey, metric, filters, exitRule, fmt,
}: Props) {
  const [rows, setRows] = useState<M7AggregateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    fetchM7Aggregate({ ...filters, dimensions: `${rowKey},${colKey}`, metric }, exitRule)
      .then(r => setRows(r.rows))
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [JSON.stringify(filters), JSON.stringify(exitRule), rowKey, colKey, metric]);

  // Build pivot
  const rowVals = Array.from(new Set(rows.map(r => String(r[rowKey])))).sort();
  const colVals = Array.from(new Set(rows.map(r => String(r[colKey])))).sort();
  const lookup = new Map<string, M7AggregateRow>();
  for (const r of rows) lookup.set(`${r[rowKey]}|${r[colKey]}`, r);
  const values = rows.map(r => Number(r.value)).filter(v => !isNaN(v));
  const minV = values.length ? Math.min(...values) : 0;
  const maxV = values.length ? Math.max(...values) : 0;
  const span = Math.max(Math.abs(minV), Math.abs(maxV)) || 1;

  const colorFor = (v: number | null) => {
    if (v == null || isNaN(v)) return '#0d1421';
    const isPnl = metric.includes('pnl');
    if (isPnl) {
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
                <th style={{ padding: '4px 8px', color: '#7a9bb5', textAlign: 'left' }}>{rowKey} \ {colKey}</th>
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
