/**
 * Per-contract count of Fridays it produced the best net P&L at the
 * given delta. Horizontal bar chart + companion table.
 */
import { useEffect, useState } from 'react';
import { fetchWinFrequency, type WinFrequencyRow } from '../../services/m4_api';

const CONTRACT_LABEL: Record<string, string> = {
  current: 'Current (~0.8d)', next: 'Next (~1.8d)',
  next_to_next: 'Next-to-next (~2.8d)', weekly: 'Weekly (~7d)',
  biweekly: 'Biweekly (~14d)', three_week: 'Three-week (~21d)',
  monthly: 'Monthly (~28d)', bimonthly: 'Bimonthly (~52d)',
  quarterly: 'Quarterly (~70d)',
};

const fmtUsdSigned = (v: number) => {
  if (!Number.isFinite(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}$${v.toFixed(2)}`;
};

const pnlColor = (v: number): string => {
  if (!Number.isFinite(v)) return '#7a9bb5';
  if (v >= 5) return '#10b981';
  if (v > 0) return '#22c55e';
  if (v >= -2) return '#cdd6e0';
  return '#ef4444';
};

export function M4WinFrequency({ delta }: { delta: number }) {
  const [rows, setRows] = useState<WinFrequencyRow[]>([]);
  const [nFridays, setNFridays] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchWinFrequency(delta)
      .then(d => {
        if (cancelled) return;
        setRows(d.rows); setNFridays(d.n_fridays); setError(null);
      })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [delta]);

  const maxWins = Math.max(1, ...rows.map(r => r.n_wins));

  return (
    <div style={containerStyle}>
      <div style={headerStyle}>
        <span style={{ fontWeight: 700 }}>Win-frequency by contract type</span>
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>
          Δ={delta} · {nFridays} Fridays · which contract delivered the
          highest net P&L per Friday
          {loading && ' · loading…'}
        </span>
      </div>
      {error && <div style={{ color: '#fca5a5', fontSize: 11 }}>{error}</div>}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #1a2d42' }}>
            <th style={thStyle}>Contract</th>
            <th style={thStyle} title="Fridays this contract was THE best performer">Wins</th>
            <th style={thStyle} title="Fridays this contract was even live">Live</th>
            <th style={thStyle}>Win freq</th>
            <th style={thStyle} title="Avg net P&L on Fridays it won">Avg net (when winning)</th>
            <th style={thStyle}>Bar</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.contract_type} style={{ borderTop: '1px solid #11202c' }}>
              <td style={{ ...tdStyle, fontWeight: 600 }}>
                {CONTRACT_LABEL[r.contract_type] || r.contract_type}
              </td>
              <td style={tdStyle}>{r.n_wins}</td>
              <td style={tdStyle}>{r.n_appears}</td>
              <td style={tdStyle}>{(r.win_frequency * 100).toFixed(1)}%</td>
              <td style={{ ...tdStyle, color: pnlColor(r.avg_net_when_winning), fontWeight: 700 }}>
                {fmtUsdSigned(r.avg_net_when_winning)}
              </td>
              <td style={tdStyle}>
                <div style={{
                  height: 10, background: '#11202c', borderRadius: 2,
                  width: 200, position: 'relative', overflow: 'hidden',
                }}>
                  <div style={{
                    position: 'absolute', top: 0, left: 0, height: '100%',
                    width: `${(r.n_wins / maxWins) * 100}%`,
                    background: 'linear-gradient(90deg, #1f6feb 0%, #58a6ff 100%)',
                  }} />
                </div>
              </td>
            </tr>
          ))}
          {!loading && rows.length === 0 && (
            <tr><td colSpan={6} style={{ ...tdStyle, color: '#7a9bb5', textAlign: 'center', padding: 16 }}>
              No data for Δ={delta}.
            </td></tr>
          )}
        </tbody>
      </table>
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
  padding: '6px 10px', textAlign: 'left',
  fontSize: 10, fontWeight: 600, color: '#7a9bb5',
  textTransform: 'uppercase', letterSpacing: 0.4, whiteSpace: 'nowrap',
};
const tdStyle: React.CSSProperties = {
  padding: '5px 10px', fontSize: 12, color: '#cdd6e0', whiteSpace: 'nowrap',
};

export default M4WinFrequency;
