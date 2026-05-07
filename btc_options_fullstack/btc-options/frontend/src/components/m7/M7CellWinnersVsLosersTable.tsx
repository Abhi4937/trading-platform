// M7 — Per-cell winners-vs-losers indicator gap table.
// Mirrors the M4 /winners_vs_losers pattern but for a single best-combo
// cell (band × hour × expiry × delta). Used inside the per-cell drilldown
// modal (Chunk 7) and the universe Losses Explorer (Chunk 6).

import React, { useEffect, useMemo, useState } from 'react';
import {
  fetchM7CellWinnersVsLosers,
  type M7Cell,
  type M7CellWvlIndicatorRow,
  type M7CellWvlResponse,
} from '../../services/m7_api';
import type { M7ExitRule } from '../../types/m7';

interface Props {
  cell: M7Cell;
  exitRule?: M7ExitRule;
  discriminateSigma?: number;
  minNPerSide?: number;
}

function fmt(v: number | null, digits = 4): string {
  if (v == null || !isFinite(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

function effectSize(r: M7CellWvlIndicatorRow): number {
  if (r.gap == null || r.sigma <= 0) return 0;
  return Math.abs(r.gap) / r.sigma;
}

function categoryColor(c: string): string {
  return ({
    'IV': '#79c0ff',
    'IV velocity': '#bf6fde',
    'RV/VRP': '#3fb950',
    'Skew/Term': '#d29922',
    'Spot regime': '#f97583',
    'Spot technicals': '#ff7b72',
    'Expected move': '#a371f7',
    'GEX/Flow': '#7a9bb5',
    'Premium': '#56d364',
    'Greeks': '#e3b341',
    'Skew (entry)': '#fb8c34',
  } as Record<string, string>)[c] || '#7a9bb5';
}

export function M7CellWinnersVsLosersTable({
  cell, exitRule, discriminateSigma = 0.5, minNPerSide = 3,
}: Props) {
  const [data, setData] = useState<M7CellWvlResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [onlyDiscriminating, setOnlyDiscriminating] = useState(true);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7CellWinnersVsLosers(cell,
      { discriminate_sigma: discriminateSigma, min_n_per_side: minNPerSide,
        exit_rule: exitRule }, ac.signal)
      .then(setData)
      .catch(e => { if ((e as Error).name !== 'AbortError') setErr(String(e)); })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [JSON.stringify(cell), JSON.stringify(exitRule),
      discriminateSigma, minNPerSide]);

  const visibleRows = useMemo(() => {
    if (!data) return [];
    return onlyDiscriminating
      ? data.rows.filter(r => r.discriminating)
      : data.rows;
  }, [data, onlyDiscriminating]);

  if (loading && !data) {
    return <div style={{ color: '#7a9bb5', fontSize: 11, padding: 8 }}>Loading…</div>;
  }
  if (err) {
    return <div style={{ color: '#f85149', fontSize: 11, padding: 8 }}>Error: {err}</div>;
  }
  if (!data) return null;

  return (
    <div style={{ background: '#0d1421', border: '1px solid #1a2d42',
                  borderRadius: 4, padding: 10 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                    marginBottom: 8, fontSize: 11 }}>
        <strong style={{ color: '#cfd9e3' }}>Winners vs Losers</strong>
        <span style={{ color: '#7a9bb5' }}>
          n={data.n_trades} (W={data.n_win} L={data.n_loss}, WR={(data.win_rate * 100).toFixed(1)}%)
        </span>
        {data.low_confidence && (
          <span style={{ background: '#553300', color: '#f0a040',
                         padding: '2px 6px', borderRadius: 3, fontSize: 10 }}>
            low confidence (n &lt; {minNPerSide} on one side)
          </span>
        )}
        <label style={{ marginLeft: 'auto', color: '#7a9bb5', fontSize: 11,
                        display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="checkbox"
                 checked={onlyDiscriminating}
                 onChange={e => setOnlyDiscriminating(e.target.checked)} />
          Only discriminating (|gap|/σ &gt; {discriminateSigma})
        </label>
      </div>

      {/* Pool suggestions */}
      {data.low_confidence && data.pool_suggestions.length > 0 && (
        <div style={{ background: '#221a08', border: '1px solid #553300',
                      borderRadius: 3, padding: 6, marginBottom: 8,
                      fontSize: 10, color: '#f0a040' }}>
          <strong>Pool suggestions:</strong>{' '}
          {data.pool_suggestions.join(' · ')}
        </div>
      )}

      {/* Indicator table */}
      <div style={{ maxHeight: 380, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse',
                        fontSize: 11, color: '#cfd9e3' }}>
          <thead style={{ position: 'sticky', top: 0, background: '#0d1421',
                          zIndex: 1 }}>
            <tr style={{ borderBottom: '1px solid #1a2d42', color: '#7a9bb5' }}>
              <th style={{ padding: 4, textAlign: 'left' }}>Indicator</th>
              <th style={{ padding: 4, textAlign: 'left' }}>Category</th>
              <th style={{ padding: 4, textAlign: 'right' }}>Avg win</th>
              <th style={{ padding: 4, textAlign: 'right' }}>Avg loss</th>
              <th style={{ padding: 4, textAlign: 'right' }}>Gap</th>
              <th style={{ padding: 4, textAlign: 'right' }}>σ (universe)</th>
              <th style={{ padding: 4, textAlign: 'right' }} title="Effect size: |gap| / σ">
                |gap|/σ
              </th>
              <th style={{ padding: 4, textAlign: 'right' }}
                  title="Welch's t-test p-value (informational; null when scipy unavailable)">
                p
              </th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.length === 0 && (
              <tr>
                <td colSpan={8} style={{ color: '#7a9bb5', padding: 8,
                                         textAlign: 'center', fontSize: 11 }}>
                  {onlyDiscriminating
                    ? 'No discriminating indicators at this threshold.'
                    : 'No indicators available.'}
                </td>
              </tr>
            )}
            {visibleRows.map(r => (
              <tr key={r.indicator}
                  style={{
                    borderBottom: '1px solid #0a0e17',
                    background: r.discriminating ? '#0a1a2e' : 'transparent',
                  }}>
                <td style={{ padding: 4 }}>
                  <span title={r.indicator}>{r.label}</span>
                </td>
                <td style={{ padding: 4 }}>
                  <span style={{
                    background: categoryColor(r.category), color: '#0a0e17',
                    padding: '1px 4px', borderRadius: 2, fontSize: 9,
                    fontWeight: 600,
                  }}>
                    {r.category}
                  </span>
                </td>
                <td style={{ padding: 4, textAlign: 'right', color: '#3fb950' }}>
                  {fmt(r.avg_win)}
                </td>
                <td style={{ padding: 4, textAlign: 'right', color: '#f85149' }}>
                  {fmt(r.avg_loss)}
                </td>
                <td style={{ padding: 4, textAlign: 'right',
                             color: r.gap == null ? '#7a9bb5'
                                   : r.gap > 0 ? '#3fb950' : '#f85149',
                             fontWeight: 600 }}>
                  {fmt(r.gap)}
                </td>
                <td style={{ padding: 4, textAlign: 'right', color: '#7a9bb5' }}>
                  {fmt(r.sigma)}
                </td>
                <td style={{ padding: 4, textAlign: 'right',
                             fontWeight: r.discriminating ? 700 : 400,
                             color: r.discriminating ? '#79c0ff' : '#cfd9e3' }}>
                  {effectSize(r).toFixed(2)}
                </td>
                <td style={{ padding: 4, textAlign: 'right', color: '#7a9bb5' }}>
                  {r.p_value_t == null ? '—' : r.p_value_t < 0.001
                    ? '<0.001'
                    : r.p_value_t.toFixed(3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
