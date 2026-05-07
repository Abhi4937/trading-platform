// M7 — Tail-risk view: worst-N Fridays in a best-combo cell, with
// per-Friday "what made it special" diff (top |z| context cols).
// Click a row → opens the Trade Path Chart for that Friday's trade.

import React, { useEffect, useState } from 'react';
import {
  fetchM7CellWorstFridays,
  type M7Cell,
  type M7CellWorstFridaysResponse,
  type M7CellWorstFridayRow,
} from '../../services/m7_api';
import type { M7ExitRule } from '../../types/m7';

interface Props {
  cell: M7Cell;
  exitRule?: M7ExitRule;
  n?: number;
  onSelectTrade?: (tradeId: string) => void;
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null || isNaN(Number(v))) return '—';
  const sign = v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(Number(v))) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${Number(v).toFixed(digits)}%`;
}

function lossCauseColor(c: string | null | undefined): string {
  if (!c) return '#1a2d42';
  return ({
    directional:    '#f85149',
    vol_expansion:  '#d29922',
    path_dependent: '#bf6fde',
    gamma_squeeze:  '#ff7b72',
    skew_flip:      '#79c0ff',
    unclassified:   '#586e7e',
  } as Record<string, string>)[c] || '#7a9bb5';
}

function pnlColor(v: number): string {
  if (v > 0) return '#3fb950';
  if (v < 0) return '#f85149';
  return '#cfd9e3';
}

export function M7CellWorstFridaysTable({
  cell, exitRule, n = 5, onSelectTrade,
}: Props) {
  const [data, setData] = useState<M7CellWorstFridaysResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true); setErr(null);
    fetchM7CellWorstFridays(cell, { n, exit_rule: exitRule }, ac.signal)
      .then(setData)
      .catch(e => { if ((e as Error).name !== 'AbortError') setErr(String(e)); })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [JSON.stringify(cell), JSON.stringify(exitRule), n]);

  if (loading && !data) {
    return <div style={{ color: '#7a9bb5', fontSize: 11, padding: 8 }}>Loading…</div>;
  }
  if (err) {
    return <div style={{ color: '#f85149', fontSize: 11, padding: 8 }}>Error: {err}</div>;
  }
  if (!data) return null;

  const toggleRow = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id); else next.add(id);
    setExpanded(next);
  };

  return (
    <div style={{ background: '#0d1421', border: '1px solid #1a2d42',
                  borderRadius: 4, padding: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                    marginBottom: 8, fontSize: 11 }}>
        <strong style={{ color: '#cfd9e3' }}>Worst {data.n_returned} Fridays</strong>
        <span style={{ color: '#7a9bb5' }}>
          (cell has {data.n_total_fridays} Fridays, {data.n_total_trades} trades)
        </span>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse',
                      fontSize: 11, color: '#cfd9e3' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #1a2d42', color: '#7a9bb5' }}>
            <th style={{ padding: 4, textAlign: 'left' }}>Friday</th>
            <th style={{ padding: 4, textAlign: 'left' }}>Cause</th>
            <th style={{ padding: 4, textAlign: 'right' }}>Net P&L</th>
            <th style={{ padding: 4, textAlign: 'right' }}>Spot move</th>
            <th style={{ padding: 4, textAlign: 'right' }} title="Peak ATM IV vs entry">IV peak</th>
            <th style={{ padding: 4, textAlign: 'right' }}
                title="Relative time (0=entry, 1=exit) when MTM hit its trough">t(min MTM)</th>
            <th style={{ padding: 4, textAlign: 'right' }}>Max MTM</th>
            <th style={{ padding: 4, textAlign: 'right' }}>Min MTM</th>
            <th style={{ padding: 4 }}></th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r: M7CellWorstFridayRow) => (
            <React.Fragment key={r.trade_id}>
              <tr style={{
                    borderBottom: '1px solid #0a0e17',
                    cursor: onSelectTrade ? 'pointer' : 'default',
                  }}
                  onClick={() => { if (onSelectTrade) onSelectTrade(r.trade_id); }}>
                <td style={{ padding: 4, fontFamily: 'ui-monospace, monospace' }}>
                  {r.friday_date_ist}
                </td>
                <td style={{ padding: 4 }}>
                  {r.loss_cause ? (
                    <span style={{
                      background: lossCauseColor(r.loss_cause), color: '#0a0e17',
                      padding: '1px 6px', borderRadius: 3, fontSize: 9, fontWeight: 700,
                    }}>{r.loss_cause}</span>
                  ) : '—'}
                </td>
                <td style={{ padding: 4, textAlign: 'right', fontWeight: 600,
                             color: pnlColor(r.net_pnl_estimate_usd) }}>
                  {fmtUsd(r.net_pnl_estimate_usd)}
                </td>
                <td style={{ padding: 4, textAlign: 'right' }}>
                  {fmtPct(r.spot_move_pct, 2)}
                </td>
                <td style={{ padding: 4, textAlign: 'right' }}>
                  {fmtPct(r.max_iv_jump_pct, 1)}
                </td>
                <td style={{ padding: 4, textAlign: 'right', color: '#7a9bb5' }}>
                  {r.rel_time_min_mtm == null ? '—' : r.rel_time_min_mtm.toFixed(2)}
                </td>
                <td style={{ padding: 4, textAlign: 'right' }}>
                  {fmtUsd(r.max_mtm_usd)}
                </td>
                <td style={{ padding: 4, textAlign: 'right' }}>
                  {fmtUsd(r.min_mtm_usd)}
                </td>
                <td style={{ padding: 4 }}>
                  <button
                    onClick={e => { e.stopPropagation(); toggleRow(r.trade_id); }}
                    style={{
                      background: '#1a2d42', color: '#cfd9e3', border: 'none',
                      borderRadius: 3, fontSize: 9, padding: '2px 6px', cursor: 'pointer',
                    }}>
                    {expanded.has(r.trade_id) ? 'hide' : 'why?'}
                  </button>
                </td>
              </tr>
              {expanded.has(r.trade_id) && (
                <tr>
                  <td colSpan={9} style={{
                    padding: '6px 12px', background: '#0a0e17',
                    borderBottom: '1px solid #1a2d42',
                  }}>
                    <div style={{ fontSize: 10, color: '#7a9bb5', marginBottom: 4 }}>
                      What made <strong>{r.friday_date_ist}</strong> special vs the
                      cell median (top |z| context cols, IQR-normalized):
                    </div>
                    <div style={{ display: 'grid',
                                  gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                                  gap: 4 }}>
                      {r.what_made_it_special.map(s => (
                        <div key={s.col} style={{
                          fontSize: 10, padding: '3px 6px', background: '#0d1421',
                          border: '1px solid #1a2d42', borderRadius: 3,
                          display: 'flex', justifyContent: 'space-between', gap: 8,
                        }}>
                          <span style={{ color: '#cfd9e3' }}>{s.label}</span>
                          <span style={{ fontFamily: 'ui-monospace, monospace',
                                         color: Math.abs(s.z) > 1.5 ? '#f85149'
                                              : Math.abs(s.z) > 1   ? '#d29922'
                                              : '#7a9bb5' }}>
                            {s.value.toFixed(3)} vs {s.cell_median.toFixed(3)} (z={s.z >= 0 ? '+' : ''}{s.z.toFixed(2)})
                          </span>
                        </div>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
          {data.rows.length === 0 && (
            <tr><td colSpan={9} style={{ color: '#7a9bb5', padding: 8,
                                          textAlign: 'center', fontSize: 11 }}>
              No trades in this cell.
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
