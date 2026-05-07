import React, { useEffect, useState } from 'react';
import { fetchM7IvBandSummary } from '../../services/m7_api';
import type { M7ExitRule, M7Filters, M7IvBandSummaryRow } from '../../types/m7';

// Indeterminate progress bar — keyframes injected via inline <style>.
function LoadingBar({ visible }: { visible: boolean }) {
  return (
    <>
      <style>{`
        @keyframes m7slide_iv {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(400%); }
        }
      `}</style>
      <div style={{
        height: 2, width: '100%', background: '#0d1421', overflow: 'hidden',
        borderRadius: 2, marginBottom: 8,
        visibility: visible ? 'visible' : 'hidden',
      }}>
        <div style={{
          height: '100%', width: '25%', background: '#1f6feb',
          animation: 'm7slide_iv 1.1s ease-in-out infinite',
        }} />
      </div>
    </>
  );
}

const usd = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `$${(v as number).toFixed(dp)}`;
const usd0 = (v: number | null | undefined) => usd(v, 0);
const pct = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `${((v as number) * 100).toFixed(dp)}%`;
const pnlColor = (v: number | null | undefined) =>
  v == null ? '#7a9bb5' : v >= 0 ? '#3fb950' : '#f85149';

// Format the active-metric "Score" cell using the same rules as the heatmaps.
function fmtScore(metric: string, v: number): string {
  if (metric === 'win_rate') return `${(v * 100).toFixed(1)}%`;
  if (metric === 'avg_pct_return_on_margin' || metric === 'avg_pct_return_on_credit' ||
      metric === 'avg_pct_return_on_margin_winners' || metric === 'avg_pct_return_on_credit_winners') {
    return `${(v * 100).toFixed(2)}%`;
  }
  if (metric === 'count' ||
      metric === 'n_wins' || metric === 'n_losses' ||
      metric === 'n_rule_trigger' || metric === 'n_hard_cap' ||
      metric === 'max_consec_losses' || metric === 'max_consec_wins' ||
      metric === 'max_consec_sl_hits' ||
      metric === 'n_winners_below_avg_min_mtm' ||
      metric === 'n_losers_above_avg_max_mtm') return `${v}`;
  return `$${v.toFixed(2)}`;
}

export function M7IvBandSummaryTable({ filters, exitRule, metric = 'avg_net_pnl' }: {
  filters: M7Filters; exitRule: M7ExitRule; metric?: string;
}) {
  const [rows, setRows] = useState<M7IvBandSummaryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let active = true;  // race-condition guard against stale responses
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7IvBandSummary({ ...filters, metric }, exitRule, ac.signal)
      .then(r => { if (active) setRows(r.rows); })
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
        marginBottom: 8,
      }}>
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          Headline — Best combo per IV band ({metric})
        </div>
        <div style={{ fontSize: 11, color: '#7a9bb5' }}>
          {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span> : `${rows.length} bands`}
        </div>
      </div>
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
                <th style={th}>IV band</th>
                <th style={th}>Best entry hr</th>
                <th style={th}>Best expiry</th>
                <th style={th}>Best Δ</th>
                <th style={thR}>Score</th>
                <th style={thR}>n</th>
                <th style={{ ...thR, color: '#3fb950' }}>n wins</th>
                <th style={{ ...thR, color: '#f85149' }}>n loss</th>
                <th style={thR}>SL hits</th>
                <th style={thR}>Hard cap</th>
                <th style={{ ...thR, color: '#f85149' }}>Max losing streak</th>
                <th style={{ ...thR, color: '#3fb950' }}>Max winning streak</th>
                <th style={thR}>Max SL streak</th>
                <th style={thR}>Win %</th>
                <th style={thR}>Avg net</th>
                <th style={thR}>Avg exit MTM</th>
                <th style={{ ...thR, color: '#3fb950' }}>Avg win (W)</th>
                <th style={{ ...thR, color: '#f85149' }}>Avg loss (L)</th>
                <th style={{ ...thR, color: '#3fb950' }}>Largest win (W)</th>
                <th style={{ ...thR, color: '#f85149' }}>Largest loss (L)</th>
                {/* Winners-only — exit MTM + path MTM (peak/trough) */}
                <th style={{ ...thR, color: '#3fb950' }}>Avg win MTM</th>
                <th style={{ ...thR, color: '#3fb950' }}
                    title="Sum of exit-time MTM across all winning trades (entry costs only).">
                  Total win MTM
                </th>
                <th style={{ ...thR, color: '#3fb950' }}>Largest win MTM</th>
                <th style={{ ...thR, color: '#3fb950' }}>Avg max MTM (W)</th>
                <th style={{ ...thR, color: '#3fb950' }}>Avg min MTM (W)</th>
                <th style={{ ...thR, color: '#3fb950' }}>Max MTM (W)</th>
                <th style={{ ...thR, color: '#3fb950' }}>Min MTM (W)</th>
                <th style={{ ...thR, color: '#3fb950' }} title="Winners whose min MTM dipped below the group's avg min MTM (winners) — i.e. winners with a worse-than-typical drawdown">
                  W &lt; avg min MTM
                </th>
                {/* Losers-only — exit MTM + path MTM (peak/trough) */}
                <th style={{ ...thR, color: '#f85149' }}>Avg loss MTM</th>
                <th style={{ ...thR, color: '#f85149' }}
                    title="Sum of exit-time MTM across all losing trades (entry costs only).">
                  Total loss MTM
                </th>
                <th style={{ ...thR, color: '#f85149' }}>Largest loss MTM</th>
                <th style={{ ...thR, color: '#f85149' }}>Avg max MTM (L)</th>
                <th style={{ ...thR, color: '#f85149' }}>Avg min MTM (L)</th>
                <th style={{ ...thR, color: '#f85149' }}>Max MTM (L)</th>
                <th style={{ ...thR, color: '#f85149' }}>Min MTM (L)</th>
                <th style={{ ...thR, color: '#f85149' }} title="Losers whose max MTM rose above the group's avg max MTM (losers) — i.e. losers that showed a better-than-typical peak before turning into losses">
                  L &gt; avg max MTM
                </th>
                <th style={thR}>Avg credit</th>
                <th style={thR}>Avg margin</th>
                <th style={thR}>Ret/margin</th>
                <th style={thR}>Ret/credit</th>
                <th style={{ ...thR, color: '#3fb950' }}>Ret/margin (W)</th>
                <th style={{ ...thR, color: '#3fb950' }}>Ret/credit (W)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ borderTop: '1px solid #1a2d42' }}>
                  <td style={{ ...td, fontWeight: 600 }}>{r.entry_atm_iv_band}</td>
                  <td style={td}>{String(r.entry_hour_ist).padStart(2, '0')}:00</td>
                  <td style={td}>{r.expiry_bucket}</td>
                  <td style={td}>{Number(r.delta_target).toFixed(2)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.score), fontWeight: 600 }}>
                    {fmtScore(metric, r.score)}
                  </td>
                  <td style={{ ...tdR, color: '#7a9bb5' }}>{r.n_trades}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.n_wins ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.n_losses ?? '—'}</td>
                  <td style={tdR}>{r.n_rule_trigger ?? '—'}</td>
                  <td style={{ ...tdR, color: '#7a9bb5' }}>{r.n_hard_cap ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.max_consec_losses ?? '—'}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.max_consec_wins ?? '—'}</td>
                  <td style={tdR}>{r.max_consec_sl_hits ?? '—'}</td>
                  <td style={tdR}>{pct(r.win_rate, 1)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_net_pnl) }}>{usd(r.avg_net_pnl)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_exit_mtm) }}>{usd(r.avg_exit_mtm)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_win_usd) }}>{usd(r.avg_win_usd)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_loss_usd) }}>{usd(r.avg_loss_usd)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.max_win_usd) }}>{usd(r.max_win_usd)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.max_loss_usd) }}>{usd(r.max_loss_usd)}</td>
                  {/* Winners-only — exit MTM + path MTM */}
                  <td style={{ ...tdR, color: pnlColor(r.avg_win_mtm) }}>{usd(r.avg_win_mtm)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.total_win_mtm) }}>{usd(r.total_win_mtm)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.largest_win_mtm) }}>{usd(r.largest_win_mtm)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_max_mtm_winners) }}>{usd(r.avg_max_mtm_winners)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_min_mtm_winners) }}>{usd(r.avg_min_mtm_winners)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.max_mtm_winners) }}>{usd(r.max_mtm_winners)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.min_mtm_winners) }}>{usd(r.min_mtm_winners)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.n_winners_below_avg_min_mtm ?? '—'}</td>
                  {/* Losers-only — exit MTM + path MTM */}
                  <td style={{ ...tdR, color: pnlColor(r.avg_loss_mtm) }}>{usd(r.avg_loss_mtm)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.total_loss_mtm) }}>{usd(r.total_loss_mtm)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.largest_loss_mtm) }}>{usd(r.largest_loss_mtm)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_max_mtm_losers) }}>{usd(r.avg_max_mtm_losers)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_min_mtm_losers) }}>{usd(r.avg_min_mtm_losers)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.max_mtm_losers) }}>{usd(r.max_mtm_losers)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.min_mtm_losers) }}>{usd(r.min_mtm_losers)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.n_losers_above_avg_max_mtm ?? '—'}</td>
                  <td style={tdR}>{usd(r.avg_credit)}</td>
                  <td style={tdR}>{usd0(r.avg_margin)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_margin) }}>
                    {pct(r.avg_pct_return_on_margin)}
                  </td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_credit) }}>
                    {pct(r.avg_pct_return_on_credit)}
                  </td>
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {pct(r.avg_pct_return_on_margin_winners)}
                  </td>
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {pct(r.avg_pct_return_on_credit_winners)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
