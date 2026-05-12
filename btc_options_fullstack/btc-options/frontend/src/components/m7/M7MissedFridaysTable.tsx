import React, { useEffect, useState } from 'react';
import { InfoIcon } from './InfoIcon';
import { ExcelButton, exportRowsAsXlsx } from './exportXlsx';
import { fetchM7MissedFridays } from '../../services/m7_api';
import type { M7ExitRule, M7Filters, M7MissedFridayRow } from '../../types/m7';

const usd = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `$${(v as number).toFixed(dp)}`;
const usd0 = (v: number | null | undefined) => usd(v, 0);
const pct = (v: number | null | undefined, dp = 1) =>
  v == null || isNaN(v as number) ? '—' : `${(v as number).toFixed(dp)}%`;
const pnlColor = (v: number | null | undefined) =>
  v == null ? '#7a9bb5' : v >= 0 ? '#3fb950' : '#f85149';

function LoadingBar({ visible }: { visible: boolean }) {
  return (
    <>
      <style>{`@keyframes m7slide_mf { 0%{transform:translateX(-100%)} 100%{transform:translateX(400%)} }`}</style>
      <div style={{
        height: 2, width: '100%', background: '#0d1421', overflow: 'hidden',
        borderRadius: 2, marginBottom: 8,
        visibility: visible ? 'visible' : 'hidden',
      }}>
        <div style={{
          height: '100%', width: '25%', background: '#1f6feb',
          animation: 'm7slide_mf 1.1s ease-in-out infinite',
        }} />
      </div>
    </>
  );
}

export function M7MissedFridaysTable({ filters, exitRule, metric = 'avg_net_pnl' }: {
  filters: M7Filters; exitRule: M7ExitRule; metric?: string;
}) {
  const [rows, setRows] = useState<M7MissedFridayRow[]>([]);
  const [stats, setStats] = useState<{ n_missed: number; n_total_fridays: number; n_matched: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7MissedFridays({ ...filters, metric }, exitRule, ac.signal)
      .then(r => {
        if (!active) return;
        setRows(r.rows);
        setStats({ n_missed: r.n_missed, n_total_fridays: r.n_total_fridays, n_matched: r.n_matched });
      })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(filters), JSON.stringify(exitRule), metric]);

  const th: React.CSSProperties = { padding: '6px 8px', color: '#7a9bb5', whiteSpace: 'nowrap' };
  const thR: React.CSSProperties = { ...th, textAlign: 'right' };
  const td: React.CSSProperties = { padding: '5px 8px', whiteSpace: 'nowrap' };
  const tdR: React.CSSProperties = { ...td, textAlign: 'right' };

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 12, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8, cursor: 'pointer',
      }} onClick={() => setCollapsed(c => !c)}>
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          {collapsed ? '▸' : '▾'} Missed Fridays — best combo per orphan Friday
          {stats && (
            <span style={{ fontWeight: 400, color: '#7a9bb5', marginLeft: 8, fontSize: 12 }}>
              ({stats.n_missed} of {stats.n_total_fridays} Fridays not covered by the 10 IV-band best cells)
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ fontSize: 11, color: '#7a9bb5' }}>
            {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span> : `${rows.length} rows`}
          </div>
          <span onClick={e => e.stopPropagation()}>
            <ExcelButton
              disabled={rows.length === 0}
              onClick={() => exportRowsAsXlsx('m7_missed_fridays.xlsx', 'MissedFridays', rows)} />
          </span>
        </div>
      </div>
      {!collapsed && (
        <>
          <LoadingBar visible={loading} />
          {!err && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{
                borderCollapse: 'collapse', fontSize: 12,
                fontVariantNumeric: 'tabular-nums', color: '#cfd9e3',
                opacity: loading ? 0.4 : 1, transition: 'opacity 120ms',
              }}>
                <thead>
                  <tr style={{ textAlign: 'left' }}>
                    <th style={th}>Friday <InfoIcon text="Friday date (IST) of the trade." /></th>
                    <th style={th}>Best entry hr <InfoIcon text="Hour-of-day IST the trade was opened." /></th>
                    <th style={th}>Best expiry <InfoIcon text="Expiry bucket of the strangle/straddle." /></th>
                    <th style={th}>Best Δ <InfoIcon text="Per-leg delta target at entry." /></th>
                    <th style={th}>IV band <InfoIcon text="ATM IV bucket the trade actually belongs to (based on entry IV)." /></th>
                    <th style={thR}>ATM IV % <InfoIcon text="Annualised ATM IV at entry, in percent." /></th>
                    <th style={thR}>Net P&L <InfoIcon text="Realised net P&L (entry slip + entry brokerage + exit slip + exit brokerage all subtracted)." /></th>
                    <th style={thR}>Exit MTM <InfoIcon text="On-screen P&L at the moment of exit (gross − entry slippage only)." /></th>
                    <th style={thR}>Max MTM <InfoIcon text="Peak unrealized P&L during the hold (entry slippage only)." /></th>
                    <th style={thR}>Min MTM <InfoIcon text="Trough unrealized P&L during the hold (entry slippage only)." /></th>
                    <th style={thR}>Credit <InfoIcon text="Upfront credit collected at entry (call + put marks × qty × 0.001 BTC)." /></th>
                    <th style={thR}>Margin <InfoIcon text="Delta Exchange portfolio margin required at entry (29-scenario engine)." /></th>
                    <th style={th}>Win? <InfoIcon text="✓ if net P&L > 0, ✗ otherwise." /></th>
                    <th style={th}>Exit reason <InfoIcon text="rule_trigger (premium_sl / max_profit / margin_target fired), fixed_hour_ist (timed exit), or hard_cap (Sat 17:30 settlement)." /></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid #1a2d42' }}>
                      <td style={{ ...td, fontWeight: 600 }}>{r.friday_date_ist}</td>
                      <td style={td}>{String(r.entry_hour_ist).padStart(2, '0')}:00</td>
                      <td style={td}>{r.expiry_bucket}</td>
                      <td style={td}>{Number(r.delta_target).toFixed(2)}</td>
                      <td style={td}>{r.entry_atm_iv_band}</td>
                      <td style={tdR}>{pct(r.entry_atm_iv_pct)}</td>
                      <td style={{ ...tdR, color: pnlColor(r.net_pnl_estimate_usd), fontWeight: 600 }}>{usd(r.net_pnl_estimate_usd)}</td>
                      <td style={{ ...tdR, color: pnlColor(r.exit_mtm_usd) }}>{usd(r.exit_mtm_usd)}</td>
                      <td style={{ ...tdR, color: pnlColor(r.max_mtm_usd) }}>{usd(r.max_mtm_usd)}</td>
                      <td style={{ ...tdR, color: pnlColor(r.min_mtm_usd) }}>{usd(r.min_mtm_usd)}</td>
                      <td style={tdR}>{usd(r.credit_usd)}</td>
                      <td style={tdR}>{usd0(r.margin_used_usd_at_entry)}</td>
                      <td style={{ ...td, color: r.is_win ? '#3fb950' : '#f85149' }}>
                        {r.is_win == null ? '—' : (r.is_win ? 'W' : 'L')}
                      </td>
                      <td style={{ ...td, color: '#7a9bb5' }}>{r.exit_reason ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
