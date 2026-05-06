/**
 * 121-row table: for each Friday, which contract type produced the best
 * net P&L at the given delta + the top deciding indicators (largest
 * |winner − loser| / σ gap).
 */
import { useEffect, useMemo, useState } from 'react';
import {
  fetchPerFridayBest,
  type PerFridayBestRow,
} from '../../services/m4_api';

const CONTRACT_LABEL: Record<string, string> = {
  current: 'Current', next: 'Next', next_to_next: 'Next-to-next',
  weekly: 'Weekly', biweekly: 'Biweekly', three_week: 'Three-week',
  monthly: 'Monthly', bimonthly: 'Bimonthly', quarterly: 'Quarterly',
};

// Pretty labels for indicator columns. Keep in sync with backend
// _ATTR_INDICATORS list. (Frontend doesn't yet receive the meta in this
// payload — keep this map authoritative for now.)
const COLUMN_LABEL: Record<string, string> = {
  ctx_atm_iv_7d:           'ATM IV 7d',
  ctx_atm_iv_14d:          'ATM IV 14d',
  ctx_atm_iv_30d:          'ATM IV 30d',
  ctx_atm_iv_60d:          'ATM IV 60d',
  ctx_ivp_atm_7d_90d:      'IVP 7d/90d',
  ctx_ivp_4h:              'IVP 4h',
  ctx_rv_7d:               'RV 7d',
  ctx_rv_14d:              'RV 14d',
  ctx_rv_30d:              'RV 30d',
  ctx_iv_rv_spread_7d:     'IV-RV spread 7d',
  ctx_iv_rv_spread_30d:    'IV-RV spread 30d',
  ctx_iv_rv_ratio_7d:      'IV/RV ratio 7d',
  ctx_vrp_pct_7d:          'VRP % 7d',
  ctx_rvp_4h:              'RVP 4h',
  ctx_risk_reversal_25d:   'RR 25d',
  ctx_butterfly_25d:       'Butterfly 25d',
  ctx_wing_atm_ratio:      'Wing/ATM',
  ctx_term_slope_7_30:     'Term slope 7→30',
  ctx_adx_14_4h:           'ADX 4h',
  ctx_atr_pct_4h:          'ATR % 4h',
  ctx_pcr_oi:              'PCR OI',
  ctx_total_gex:           'Total GEX',
  credit_pct:              'Credit %',
  credit_pct_normalized:   'Credit % / √DTE',
  fair_credit_at_ivp:      'Fair credit',
  structural_credit_pct:   'Struct. credit %',
  iv_regime_premium_pct:   'IV regime premium',
  excess_over_fair_pct:    'Excess over fair',
  theta_per_vega_call:     'θ/ν call',
  theta_per_vega_put:      'θ/ν put',
  theta_per_vega_combined: 'θ/ν combined',
};

const fmtUsdSigned = (v: number, d = 2) => {
  if (!Number.isFinite(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}$${v.toFixed(d)}`;
};
const fmtSci = (v: number) => {
  if (!Number.isFinite(v)) return '—';
  if (Math.abs(v) >= 100) return v.toFixed(1);
  if (Math.abs(v) >= 1) return v.toFixed(3);
  return v.toFixed(5);
};
const pnlColor = (v: number): string => {
  if (!Number.isFinite(v)) return '#7a9bb5';
  if (v >= 5) return '#10b981';
  if (v > 0) return '#22c55e';
  if (v >= -2) return '#cdd6e0';
  return '#ef4444';
};

export function M4PerFridayBest({ delta }: { delta: number }) {
  const [rows, setRows] = useState<PerFridayBestRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minWinnerNet, setMinWinnerNet] = useState(0);
  const [sortBy, setSortBy] = useState<'date' | 'net'>('date');
  const [sortDesc, setSortDesc] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPerFridayBest({ delta, nDeciding: 3 })
      .then(d => { if (!cancelled) { setRows(d.rows); setError(null); } })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [delta]);

  const filteredSorted = useMemo(() => {
    const filt = rows.filter(r =>
      r.winner.net_pnl_usd >= minWinnerNet,
    );
    filt.sort((a, b) => {
      if (sortBy === 'date') {
        return sortDesc
          ? b.entry_date_ist.localeCompare(a.entry_date_ist)
          : a.entry_date_ist.localeCompare(b.entry_date_ist);
      }
      return sortDesc
        ? b.winner.net_pnl_usd - a.winner.net_pnl_usd
        : a.winner.net_pnl_usd - b.winner.net_pnl_usd;
    });
    return filt;
  }, [rows, minWinnerNet, sortBy, sortDesc]);

  const flipSort = (k: 'date' | 'net') => {
    if (sortBy === k) setSortDesc(d => !d);
    else { setSortBy(k); setSortDesc(true); }
  };

  return (
    <div style={containerStyle}>
      <div style={headerStyle}>
        <span style={{ fontWeight: 700 }}>Per-Friday best expiry</span>
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>
          Δ={delta} · {rows.length} Fridays · winner / runner-up / loser
          + top 3 deciding indicators
          {loading && ' · loading…'}
        </span>
        <label style={{ marginLeft: 'auto', fontSize: 11, color: '#7a9bb5' }}>
          Min winner net $:{' '}
          <input
            type="number" value={minWinnerNet}
            onChange={e => setMinWinnerNet(Number(e.target.value) || 0)}
            style={{
              width: 70, background: '#0a1018', color: '#cdd6e0',
              border: '1px solid #1a2d42', padding: '2px 6px',
              borderRadius: 3, fontSize: 11,
            }}
          />
        </label>
      </div>
      {error && <div style={{ color: '#fca5a5', fontSize: 11 }}>{error}</div>}
      <div style={{ overflowX: 'auto', maxHeight: 560 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
          <thead style={{ position: 'sticky', top: 0, background: '#0a1018', zIndex: 1 }}>
            <tr>
              <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => flipSort('date')}>
                Friday {sortBy === 'date' ? (sortDesc ? '▼' : '▲') : ''}
              </th>
              <th style={thStyle}>Winner</th>
              <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => flipSort('net')}>
                Net P&L {sortBy === 'net' ? (sortDesc ? '▼' : '▲') : ''}
              </th>
              <th style={thStyle}>Runner-up</th>
              <th style={thStyle}>Loser</th>
              <th style={thStyle}>Top 3 deciding indicators (winner vs loser)</th>
            </tr>
          </thead>
          <tbody>
            {filteredSorted.map(r => (
              <tr key={r.entry_date_ist} style={{ borderTop: '1px solid #11202c' }}>
                <td style={{ ...tdStyle, fontWeight: 600 }}>{r.entry_date_ist}</td>
                <td style={{ ...tdStyle, color: '#10b981', fontWeight: 700 }}>
                  {CONTRACT_LABEL[r.winner.contract_type] || r.winner.contract_type}
                </td>
                <td style={{
                  ...tdStyle, color: pnlColor(r.winner.net_pnl_usd), fontWeight: 700,
                }}>
                  {fmtUsdSigned(r.winner.net_pnl_usd)}
                </td>
                <td style={{ ...tdStyle, color: '#7a9bb5' }}>
                  {r.runner_up
                    ? `${CONTRACT_LABEL[r.runner_up.contract_type] || r.runner_up.contract_type}` +
                      ` (${fmtUsdSigned(r.runner_up.net_pnl_usd)})`
                    : '—'}
                </td>
                <td style={{ ...tdStyle, color: '#ef4444' }}>
                  {r.loser
                    ? `${CONTRACT_LABEL[r.loser.contract_type] || r.loser.contract_type}` +
                      ` (${fmtUsdSigned(r.loser.net_pnl_usd)})`
                    : '—'}
                </td>
                <td style={{ ...tdStyle, fontSize: 10.5 }}>
                  {r.deciding_indicators.length === 0 ? (
                    <span style={{ color: '#475569' }}>—</span>
                  ) : (
                    r.deciding_indicators.map((d, i) => (
                      <span key={d.column} style={{ marginRight: 10 }}>
                        <span style={{ color: '#7a9bb5' }}>
                          {COLUMN_LABEL[d.column] || d.column}:
                        </span>
                        {' '}
                        <span style={{ color: '#10b981' }}>{fmtSci(d.winner_value)}</span>
                        <span style={{ color: '#475569' }}> vs </span>
                        <span style={{ color: '#ef4444' }}>{fmtSci(d.loser_value)}</span>
                        <span style={{ color: '#7a9bb5' }}>
                          {' '}({d.score_sigma.toFixed(1)}σ)
                        </span>
                        {i < r.deciding_indicators.length - 1 && (
                          <span style={{ color: '#475569' }}> · </span>
                        )}
                      </span>
                    ))
                  )}
                </td>
              </tr>
            ))}
            {!loading && filteredSorted.length === 0 && (
              <tr><td colSpan={6} style={{
                ...tdStyle, color: '#7a9bb5', textAlign: 'center', padding: 16,
              }}>
                {rows.length === 0
                  ? `No data for Δ=${delta}.`
                  : `No Fridays match filter (min winner net ≥ $${minWinnerNet}).`}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: '#7a9bb5', marginTop: 6 }}>
        Deciding indicators ranked by |winner − loser| / overall std (in σ).
        Higher σ = bigger separation between winner and loser values for that
        Friday. Correlations only — not causal.
      </div>
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
  padding: '6px 8px', textAlign: 'left',
  fontSize: 10, fontWeight: 600, color: '#7a9bb5',
  textTransform: 'uppercase', letterSpacing: 0.4, whiteSpace: 'nowrap',
  borderBottom: '1px solid #1a2d42',
};
const tdStyle: React.CSSProperties = {
  padding: '5px 8px', whiteSpace: 'nowrap',
};

export default M4PerFridayBest;
