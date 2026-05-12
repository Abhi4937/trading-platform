import React, { useEffect, useState } from 'react';
import { InfoIcon } from './InfoIcon';
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
function fmtMinutes(min: number | null | undefined): string {
  if (min == null || isNaN(min as number)) return '—';
  const m = Math.round(min as number);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m - h * 60;
  return rem === 0 ? `${h}h` : `${h}h ${rem}m`;
}

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
                <th style={th}>IV band <InfoIcon text="ATM IV bucket at entry hour (e.g., 0-20 = annualised IV in [0%, 20%); 100+ = ≥100%)." /></th>
                <th style={th}>Best entry hr <InfoIcon text="Hour-of-day IST at which the cell's trades were opened (Friday). Picked per band by the chosen score." /></th>
                <th style={th}>Best expiry <InfoIcon text="Expiry bucket — current (same Sat), next (Sun), next_to_next (Mon), weekly/biweekly/monthly." /></th>
                <th style={th}>Best Δ <InfoIcon text="Per-leg target delta at entry. 0.50 = ATM straddle, 0.10 = far-OTM strangle." /></th>
                <th style={thR}>Score <InfoIcon text="Cell's value of the chosen ranking metric (see metric dropdown above). The picked cell per band maxes (or mins) this." /></th>
                <th style={thR}>n <InfoIcon text="Number of Friday trades in this cell." /></th>
                <th style={thR}>n wins <InfoIcon text="Trades with net P&L > 0 (entry slip + brokerage + exit slip + brokerage all subtracted)." /></th>
                <th style={thR}>n loss <InfoIcon text="Trades with net P&L ≤ 0." /></th>
                <th style={thR}>Rule hits <InfoIcon text="Trades that exited because ANY rule fired (premium_sl OR max_profit OR margin_target). For take-profit families this INCLUDES profit-take fires — it's a 'rule triggered an exit' count, not a loss-cut count." /></th>
                <th style={thR}>SL hits <InfoIcon text="Trades where premium-SL specifically fired (one leg's mark crossed entry × (1 + premium_sl_pct/100)). Strict loss-cut count — excludes take-profit fires." /></th>
                <th style={thR}>Hard cap <InfoIcon text="Trades that ran past all rules and exited at Saturday 17:30 IST (settlement)." /></th>
                <th style={thR}>Max losing streak <InfoIcon text="Longest run of consecutive losing trades when Fridays are ordered chronologically." /></th>
                <th style={thR}>Max winning streak <InfoIcon text="Longest run of consecutive winning trades when Fridays are ordered chronologically." /></th>
                <th style={thR}>Max rule streak <InfoIcon text="Longest run of consecutive rule-trigger exits (any rule). Companion of 'Rule hits' — includes profit-take fires." /></th>
                <th style={thR}>Max SL streak <InfoIcon text="Longest run of consecutive trades that exited via real premium-SL (excludes take-profit fires)." /></th>
                <th style={thR}>Win % <InfoIcon text="n_wins / n_trades." /></th>
                <th style={thR}>Avg net <InfoIcon text="Mean net P&L per trade (all four cost components subtracted)." /></th>
                <th style={thR}>Avg exit MTM <InfoIcon text="Mean exit-time MTM = mean(gross_pnl − entry costs). The on-screen P&L at the moment of exit (exit costs not deducted)." /></th>
                <th style={thR}>Avg win (W) <InfoIcon text="Mean net P&L across winners only." /></th>
                <th style={thR}>Avg loss (L) <InfoIcon text="Mean net P&L across losers only (negative)." /></th>
                <th style={thR}>Largest win (W) <InfoIcon text="Max net P&L of any single winner." /></th>
                <th style={thR}>Largest loss (L) <InfoIcon text="Min net P&L of any single loser (most negative)." /></th>
                {/* Winners-only — exit MTM + path MTM (peak/trough) */}
                <th style={thR}>Avg win MTM <InfoIcon text="Mean exit-time MTM across winners (entry costs only)." /></th>
                <th style={thR}>Total win MTM <InfoIcon text="Sum of exit-time MTM across all winning trades (entry costs only)." /></th>
                <th style={thR}>Largest win MTM <InfoIcon text="Max exit-time MTM among winners." /></th>
                <th style={thR}>Avg max MTM (W) <InfoIcon text="Mean peak MTM across winners (best unrealized point during the hold)." /></th>
                <th style={thR}>Avg min MTM (W) <InfoIcon text="Mean trough MTM across winners — how deep winners dipped before recovering." /></th>
                <th style={thR}>Max MTM (W) <InfoIcon text="Highest peak MTM observed across all winners." /></th>
                <th style={thR}>Min MTM (W) <InfoIcon text="Worst trough MTM observed across all winners." /></th>
                <th style={thR}>W &lt; avg min MTM <InfoIcon text="Count of winners whose min MTM dipped below the cell's avg min-MTM-winners — i.e. winners that endured a worse-than-typical drawdown before recovering." /></th>
                {/* Losers-only — exit MTM + path MTM (peak/trough) */}
                <th style={thR}>Avg loss MTM <InfoIcon text="Mean exit-time MTM across losers (entry costs only)." /></th>
                <th style={thR}>Total loss MTM <InfoIcon text="Sum of exit-time MTM across all losing trades (entry costs only)." /></th>
                <th style={thR}>Largest loss MTM <InfoIcon text="Min exit-time MTM among losers." /></th>
                <th style={thR}>Avg max MTM (L) <InfoIcon text="Mean peak MTM across losers — how high losers went before turning losing." /></th>
                <th style={thR}>Avg min MTM (L) <InfoIcon text="Mean trough MTM across losers (worst point in the hold)." /></th>
                <th style={thR}>Max MTM (L) <InfoIcon text="Highest peak MTM observed across all losers." /></th>
                <th style={thR}>Min MTM (L) <InfoIcon text="Worst trough MTM observed across all losers." /></th>
                <th style={thR}>L &gt; avg max MTM <InfoIcon text="Count of losers whose max MTM rose above the cell's avg max-MTM-losers — i.e. losers that showed a better-than-typical peak before turning into losses (missed exit opportunity)." /></th>
                <th style={thR}>Avg credit <InfoIcon text="Mean upfront credit collected per trade." /></th>
                <th style={thR}>Avg margin <InfoIcon text="Mean Delta Exchange portfolio margin required at entry." /></th>
                <th style={thR}>Ret/margin <InfoIcon text="Mean per-trade net_pnl ÷ margin_at_entry. Capital efficiency view." /></th>
                <th style={thR}>Ret/credit <InfoIcon text="Mean per-trade net_pnl ÷ credit_collected. ROI on premium captured." /></th>
                <th style={thR}>Ret/margin (W) <InfoIcon text="Ret/margin restricted to winning trades only." /></th>
                <th style={thR}>Ret/credit (W) <InfoIcon text="Ret/credit restricted to winning trades only." /></th>
                <th style={thR}>Peak % <InfoIcon text="Mean of per-trade max_mtm ÷ credit. Average peak unrealized return as % of credit — shows what was theoretically achievable before exit." /></th>
                <th style={thR}>Trough % <InfoIcon text="Mean of per-trade min_mtm ÷ credit. Average trough unrealized return as % of credit. Negative — how deep the trade dipped below water at any point." /></th>
                <th style={thR}>Avg exit time <InfoIcon text="Mean hold time (entry → exit) across all trades, in hours and minutes." /></th>
                <th style={thR}>Avg winner exit <InfoIcon text="Mean hold time restricted to winning trades." /></th>
                <th style={thR}>Avg loser exit <InfoIcon text="Mean hold time restricted to losing trades." /></th>
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
                  <td style={{ ...tdR, color: '#f85149' }}>{r.n_premium_sl_hit ?? '—'}</td>
                  <td style={{ ...tdR, color: '#7a9bb5' }}>{r.n_hard_cap ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.max_consec_losses ?? '—'}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.max_consec_wins ?? '—'}</td>
                  <td style={tdR}>{r.max_consec_sl_hits ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.max_consec_premium_sl_hits ?? '—'}</td>
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
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {pct(r.avg_pct_max_mtm_on_credit)}
                  </td>
                  <td style={{ ...tdR, color: '#f85149' }}>
                    {pct(r.avg_pct_min_mtm_on_credit)}
                  </td>
                  <td style={{ ...tdR, color: '#f0b300' }}>
                    {fmtMinutes(r.avg_exit_offset_minutes)}
                  </td>
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {fmtMinutes(r.avg_winner_exit_offset_minutes)}
                  </td>
                  <td style={{ ...tdR, color: '#f85149' }}>
                    {fmtMinutes(r.avg_loser_exit_offset_minutes)}
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
