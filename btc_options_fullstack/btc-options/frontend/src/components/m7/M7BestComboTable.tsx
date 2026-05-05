import React, { useEffect, useState } from 'react';
import { fetchM7BestCombo } from '../../services/m7_api';
import type { M7BestComboRow, M7ExitRule, M7Filters } from '../../types/m7';

export function M7BestComboTable({ filters, exitRule, metric = 'avg_net_pnl', topN = 20 }: {
  filters: M7Filters; exitRule: M7ExitRule; metric?: string; topN?: number;
}) {
  const [rows, setRows] = useState<M7BestComboRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchM7BestCombo({ ...filters, metric, top_n: topN }, exitRule)
      .then(r => setRows(r.rows))
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [JSON.stringify(filters), JSON.stringify(exitRule), metric, topN]);

  const fmt = (v: number) => metric.includes('pnl') ? `$${v.toFixed(2)}` : `${(v * 100).toFixed(1)}%`;

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 12, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8,
      }}>
        <div style={{ fontSize: 13, color: '#cfd9e3', fontWeight: 700 }}>
          Top {topN} (entry_hour × expiry × Δ) combos by {metric}
        </div>
        <div style={{ fontSize: 11, color: '#7a9bb5' }}>
          {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span> : `${rows.length} rows`}
        </div>
      </div>
      {!loading && !err && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12,
                        fontVariantNumeric: 'tabular-nums', color: '#cfd9e3' }}>
          <thead>
            <tr style={{ color: '#7a9bb5', textAlign: 'left' }}>
              <th style={{ padding: 6 }}>Rank</th>
              <th style={{ padding: 6 }}>Entry hr</th>
              <th style={{ padding: 6 }}>Expiry</th>
              <th style={{ padding: 6 }}>Δ</th>
              <th style={{ padding: 6, textAlign: 'right' }}>Score</th>
              <th style={{ padding: 6, textAlign: 'right' }}>n</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ borderTop: '1px solid #1a2d42' }}>
                <td style={{ padding: 6, color: '#7a9bb5' }}>{i + 1}</td>
                <td style={{ padding: 6 }}>{String(r.entry_hour_ist).padStart(2, '0')}:00</td>
                <td style={{ padding: 6 }}>{r.expiry_date}</td>
                <td style={{ padding: 6 }}>{Number(r.delta_target).toFixed(2)}</td>
                <td style={{
                  padding: 6, textAlign: 'right',
                  color: r.score >= 0 ? '#3fb950' : '#f85149', fontWeight: 600,
                }}>{fmt(r.score)}</td>
                <td style={{ padding: 6, textAlign: 'right', color: '#7a9bb5' }}>{r.n_trades}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
