/**
 * Per-contract-type winners-vs-losers table. For each contract, shows
 * avg(indicator) on winning trades vs avg(indicator) on losing trades,
 * highlighting the indicators with the largest gap (>0.5σ).
 */
import * as React from 'react';
import { useEffect, useMemo, useState } from 'react';
import {
  fetchWinnersVsLosers,
  type IndicatorMeta, type WinnersVsLosersRow,
} from '../../services/m4_api';

const CONTRACT_LABEL: Record<string, string> = {
  current: 'Current (~0.8d)', next: 'Next (~1.8d)',
  next_to_next: 'Next-to-next (~2.8d)', weekly: 'Weekly (~7d)',
  biweekly: 'Biweekly (~14d)', three_week: 'Three-week (~21d)',
  monthly: 'Monthly (~28d)', bimonthly: 'Bimonthly (~52d)',
  quarterly: 'Quarterly (~70d)',
};
const CONTRACT_ORDER = [
  'current', 'next', 'next_to_next', 'weekly', 'biweekly',
  'three_week', 'monthly', 'bimonthly', 'quarterly',
];

const fmtNum = (v: number | null, d = 4): string => {
  if (v === null || !Number.isFinite(v)) return '—';
  return v.toFixed(d);
};
const fmtUsdSigned = (v: number, d = 2) => {
  if (!Number.isFinite(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}$${v.toFixed(d)}`;
};

export function M4WinnersVsLosers({ delta }: { delta: number }) {
  const [indicatorMeta, setIndicatorMeta] = useState<IndicatorMeta[]>([]);
  const [rows, setRows] = useState<WinnersVsLosersRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(
    new Set(['current', 'next', 'next_to_next', 'weekly', 'biweekly']),
  );
  const [showOnlyDiscriminating, setShowOnlyDiscriminating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchWinnersVsLosers({ delta })
      .then(d => {
        if (cancelled) return;
        setIndicatorMeta(d.indicators);
        setRows(d.rows);
        setError(null);
      })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [delta]);

  const orderedRows = useMemo(
    () => [...rows].sort((a, b) =>
      CONTRACT_ORDER.indexOf(a.contract_type) - CONTRACT_ORDER.indexOf(b.contract_type),
    ),
    [rows],
  );

  // Group indicators by category for the table sub-headers
  const indicatorsByCategory = useMemo(() => {
    const m = new Map<string, IndicatorMeta[]>();
    for (const ind of indicatorMeta) {
      if (!m.has(ind.category)) m.set(ind.category, []);
      m.get(ind.category)!.push(ind);
    }
    return m;
  }, [indicatorMeta]);

  const toggle = (ct: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(ct)) next.delete(ct); else next.add(ct);
      return next;
    });
  };

  return (
    <div style={containerStyle}>
      <div style={headerStyle}>
        <span style={{ fontWeight: 700 }}>Winners vs losers per contract</span>
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>
          Δ={delta} · per indicator: avg on winning trades vs avg on losing
          trades. Highlighted = |gap| &gt; 0.5σ of overall std.
          {loading && ' · loading…'}
        </span>
        <label style={{ marginLeft: 'auto', fontSize: 11, color: '#7a9bb5' }}>
          <input type="checkbox" checked={showOnlyDiscriminating}
                 onChange={e => setShowOnlyDiscriminating(e.target.checked)} />
          {' '}Only discriminating
        </label>
      </div>
      {error && <div style={{ color: '#fca5a5', fontSize: 11 }}>{error}</div>}

      {orderedRows.map(row => {
        const isOpen = expanded.has(row.contract_type);
        return (
          <div key={row.contract_type} style={{
            borderBottom: '1px solid #11202c', marginBottom: 4, paddingBottom: 6,
          }}>
            <div onClick={() => toggle(row.contract_type)}
                 style={{
                   cursor: 'pointer', padding: '6px 4px',
                   display: 'flex', alignItems: 'baseline', gap: 12,
                 }}>
              <span style={{ fontSize: 12, color: '#7a9bb5' }}>{isOpen ? '▼' : '▶'}</span>
              <span style={{ fontWeight: 700, color: '#cdd6e0', fontSize: 13 }}>
                {CONTRACT_LABEL[row.contract_type] || row.contract_type}
              </span>
              <span style={{ fontSize: 11, color: '#7a9bb5' }}>
                {row.n_win} wins · {row.n_loss} losses · WR {(row.win_rate * 100).toFixed(1)}%
              </span>
              <span style={{ fontSize: 11, color: '#10b981' }}>
                avg net (win) {fmtUsdSigned(row.avg_net_win)}
              </span>
              <span style={{ fontSize: 11, color: '#ef4444' }}>
                avg net (loss) {fmtUsdSigned(row.avg_net_loss)}
              </span>
            </div>

            {isOpen && (
              <div style={{ overflowX: 'auto', paddingLeft: 16 }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 11.5, minWidth: 700 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #1a2d42' }}>
                      <th style={thStyle}>Indicator</th>
                      <th style={thStyle}>Avg (Win)</th>
                      <th style={thStyle}>Avg (Loss)</th>
                      <th style={thStyle}>Gap (W − L)</th>
                      <th style={thStyle}>Gap / σ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from(indicatorsByCategory.entries()).map(([cat, inds]) => (
                      <React.Fragment key={cat}>
                        <tr><td colSpan={5} style={categoryRow}>{cat}</td></tr>
                        {inds.map(ind => {
                          const c = row.indicators[ind.column];
                          if (!c) return null;
                          if (showOnlyDiscriminating && !c.discriminating) return null;
                          const gapSigma = (c.gap !== null && c.sigma > 0)
                            ? c.gap / c.sigma : null;
                          const arrow = (c.gap === null) ? '' : (c.gap > 0 ? '↑' : '↓');
                          return (
                            <tr key={ind.column}
                                style={{
                                  borderTop: '1px solid #11202c',
                                  background: c.discriminating ? '#142537' : undefined,
                                }}>
                              <td style={{ ...tdStyle, color: '#cdd6e0' }}>
                                {ind.label}{' '}
                                <span style={{ color: '#475569', fontSize: 9 }}>
                                  {ind.column}
                                </span>
                              </td>
                              <td style={{ ...tdStyle, color: '#10b981' }}>{fmtNum(c.avg_win)}</td>
                              <td style={{ ...tdStyle, color: '#ef4444' }}>{fmtNum(c.avg_loss)}</td>
                              <td style={{
                                ...tdStyle,
                                color: c.gap === null ? '#475569'
                                  : (c.gap > 0 ? '#10b981' : '#ef4444'),
                                fontWeight: c.discriminating ? 700 : 400,
                              }}>
                                {c.gap !== null ? `${arrow} ${fmtNum(c.gap)}` : '—'}
                              </td>
                              <td style={{
                                ...tdStyle,
                                color: c.discriminating ? '#fff' : '#7a9bb5',
                                fontWeight: c.discriminating ? 700 : 400,
                              }}>
                                {gapSigma !== null ? fmtNum(gapSigma, 2) : '—'}
                              </td>
                            </tr>
                          );
                        })}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}

      {!loading && orderedRows.length === 0 && (
        <div style={{ color: '#7a9bb5', textAlign: 'center', padding: 16 }}>
          No data for Δ={delta}.
        </div>
      )}
    </div>
  );
}

const containerStyle: React.CSSProperties = {
  background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
  padding: 12, marginBottom: 12,
};
const headerStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 8,
  fontSize: 13, color: '#cdd6e0',
};
const thStyle: React.CSSProperties = {
  padding: '5px 8px', textAlign: 'left',
  fontSize: 10, fontWeight: 600, color: '#7a9bb5',
  textTransform: 'uppercase', letterSpacing: 0.4, whiteSpace: 'nowrap',
};
const tdStyle: React.CSSProperties = {
  padding: '4px 8px', whiteSpace: 'nowrap',
};
const categoryRow: React.CSSProperties = {
  padding: '4px 8px', fontSize: 9.5, fontWeight: 700,
  color: '#7a9bb5', textTransform: 'uppercase',
  letterSpacing: 0.6, background: '#0a1018',
};

export default M4WinnersVsLosers;
