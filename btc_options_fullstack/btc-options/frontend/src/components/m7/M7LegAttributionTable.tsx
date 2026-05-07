import React, { useEffect, useState } from 'react';
import { fetchM7LegAttribution } from '../../services/m7_api';
import type {
  M7ExitRule, M7Filters, M7LegAttributionResponse, M7LegAttributionRow,
} from '../../types/m7';

// Per-trade table with CE/PE stacked rows (rowSpan=2 on shared cells).
// Conventions match the existing BacktestTradeLogTable: leg badge in col 1,
// per-leg numerics in cols 2-7, shared cells (rs2) on the right.

interface Props {
  filters: M7Filters;
  exitRule: M7ExitRule;
  onSelectTrade?: (tradeId: string) => void;
}

const PAGE_SIZE = 25;

const SORT_OPTIONS: { v: string; l: string }[] = [
  { v: 'friday_date_ist',     l: 'Friday (date)' },
  { v: 'net_pnl_estimate_usd',l: 'Net P&L' },
  { v: 'leg_pnl_diff_usd',    l: 'Leg P&L diff (call − put)' },
  { v: 'gross_pnl_usd',       l: 'Gross P&L' },
  { v: 'iv_skew_pct',         l: 'IV skew (CE − PE)' },
  { v: 'delta_skew',          l: 'Delta skew (|CE| − |PE|)' },
  { v: 'premium_skew_usd',    l: 'Premium skew (CE − PE)' },
  { v: 'min_mtm_usd',         l: 'Worst MTM' },
  { v: 'max_mtm_usd',         l: 'Best MTM' },
];

// Color a numeric cell green/red around 0.
function pnlColor(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '#7a9bb5';
  return v > 0 ? '#3fb950' : v < 0 ? '#f85149' : '#cfd9e3';
}

// Format USD with 2 decimals + $ sign.
function fmtUsd(v: number | null | undefined): string {
  if (v == null || isNaN(Number(v))) return '—';
  return `$${Number(v).toFixed(2)}`;
}

// Format percent with one decimal (e.g. iv_skew_pct).
function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(Number(v))) return '—';
  return `${Number(v).toFixed(digits)}%`;
}

// Format IV (decimal fraction) as % with 1 decimal.
function fmtIv(v: number | null | undefined): string {
  if (v == null || isNaN(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(1)}%`;
}

// Color the leg_winner badge.
function legWinnerColor(w: string): string {
  return ({
    both: '#3fb950',
    call_only: '#79b8ff',
    put_only: '#f97583',
    neither: '#7a9bb5',
  } as Record<string, string>)[w] || '#7a9bb5';
}

// Color the loss-cause badge. Distinct hue per cause so the eye picks them
// out at a glance in the trade log; null (winners) → no badge.
function lossCauseColor(c: string | null | undefined): string {
  if (!c) return '#1a2d42';
  return ({
    directional:    '#f85149',  // red — spot blew through
    vol_expansion:  '#d29922',  // amber — IV jumped
    path_dependent: '#bf6fde',  // purple — gave back profit
    gamma_squeeze:  '#ff7b72',  // hot red — early SL
    skew_flip:      '#79c0ff',  // blue — direction inverted
    unclassified:   '#586e7e',  // grey — "didn't fit any bucket"
  } as Record<string, string>)[c] || '#7a9bb5';
}

export function M7LegAttributionTable({ filters, exitRule, onSelectTrade }: Props) {
  const [data, setData] = useState<M7LegAttributionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [sortBy, setSortBy] = useState('friday_date_ist');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Reset to page 0 whenever filters / exit rule / sort changes
  useEffect(() => { setPage(0); }, [JSON.stringify(filters), JSON.stringify(exitRule), sortBy, sortDir]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7LegAttribution({
      ...filters,
      sort_by: sortBy,
      sort_dir: sortDir,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }, exitRule, ac.signal)
      .then(d => { if (active) setData(d); })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(filters), JSON.stringify(exitRule), sortBy, sortDir, page]);

  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const sty = {
    bg: '#0a0e17', bdr: '1px solid #1a2d42',
    head: '#7a9bb5', text: '#cfd9e3',
  };
  const cellPad = '6px 8px';

  return (
    <div style={{
      background: sty.bg, border: sty.bdr, borderRadius: 6,
      padding: 10, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 10, gap: 12, flexWrap: 'wrap',
      }}>
        <div style={{ fontSize: 13, color: sty.text, fontWeight: 600 }}>
          Leg Attribution — per-trade CE/PE breakdown
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11 }}>
          <span style={{ color: sty.head }}>Sort:</span>
          <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                  style={{ background: '#0d1421', color: sty.text, border: sty.bdr, padding: '3px 6px' }}>
            {SORT_OPTIONS.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
          </select>
          <button onClick={() => setSortDir(d => d === 'asc' ? 'desc' : 'asc')}
                  style={{ background: '#0d1421', color: sty.text, border: sty.bdr, padding: '3px 8px', cursor: 'pointer' }}>
            {sortDir === 'asc' ? '↑ asc' : '↓ desc'}
          </button>
          <span style={{ color: sty.head, marginLeft: 8 }}>
            {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span>
              : `${total} trades`}
          </span>
        </div>
      </div>
      <div style={{ overflowX: 'auto', maxHeight: 600, overflowY: 'auto' }}>
        <table style={{
          borderCollapse: 'collapse', fontSize: 11, width: '100%',
          fontVariantNumeric: 'tabular-nums', minWidth: 1700,
        }}>
          <thead style={{ position: 'sticky', top: 0, background: '#0d1421', zIndex: 1 }}>
            <tr style={{ color: sty.head }}>
              <th style={{ padding: cellPad, textAlign: 'left' }}>Friday</th>
              <th style={{ padding: cellPad, textAlign: 'left' }}>Hour</th>
              <th style={{ padding: cellPad, textAlign: 'left' }}>Expiry</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Δ</th>
              <th style={{ padding: cellPad, textAlign: 'left' }}>IV band</th>
              <th style={{ padding: cellPad, textAlign: 'left' }}>Leg</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Strike</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Entry mark</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Exit mark</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Entry Δ</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Entry IV</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Leg P&L</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Leg max MTM</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Leg min MTM</th>
              {/* Shared (rowSpan=2) cells start here */}
              <th style={{ padding: cellPad, textAlign: 'left' }}>Winner</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Δ skew</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>IV skew</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Prem skew</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Credit</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Margin</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Gross P&L</th>
              <th style={{ padding: cellPad, textAlign: 'right' }}>Net P&L</th>
              <th style={{ padding: cellPad, textAlign: 'left' }} title="What caused the loss (winners blank)">Cause</th>
              <th style={{ padding: cellPad, textAlign: 'left' }}>Exit</th>
            </tr>
          </thead>
          <tbody>
            {data?.rows.map(r => <TradePair key={r.trade_id} r={r} onSelectTrade={onSelectTrade} />)}
          </tbody>
        </table>
      </div>
      {/* Pagination */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginTop: 8, color: sty.head, fontSize: 11,
      }}>
        <span>Page {page + 1} / {totalPages}</span>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => setPage(p => Math.max(0, p - 1))}
                  disabled={page === 0}
                  style={{ background: '#0d1421', color: sty.text, border: sty.bdr,
                           padding: '3px 10px', cursor: page === 0 ? 'default' : 'pointer',
                           opacity: page === 0 ? 0.5 : 1 }}>← Prev</button>
          <button onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  style={{ background: '#0d1421', color: sty.text, border: sty.bdr,
                           padding: '3px 10px',
                           cursor: page >= totalPages - 1 ? 'default' : 'pointer',
                           opacity: page >= totalPages - 1 ? 0.5 : 1 }}>Next →</button>
        </div>
      </div>
    </div>
  );
}

interface PairProps {
  r: M7LegAttributionRow;
  onSelectTrade?: (tradeId: string) => void;
}

// Renders a single trade as two stacked rows. Per-leg cells (CE/PE specific)
// vary across the two rows; shared cells (winner, skew, totals) span both
// via rowSpan={2} on the first row's cell.
function TradePair({ r, onSelectTrade }: PairProps) {
  const cell = '5px 8px';
  const tdR = { padding: cell, textAlign: 'right' as const };
  const tdL = { padding: cell, textAlign: 'left' as const };
  const sharedTop = { ...tdR, borderTop: '1px solid #1a2d42' };
  const sharedTopL = { ...tdL, borderTop: '1px solid #1a2d42' };
  const click = onSelectTrade
    ? { cursor: 'pointer' as const, onClick: () => onSelectTrade(r.trade_id) }
    : { onClick: undefined };

  return (
    <>
      {/* Row 1 — CE leg + first pass of shared cells (these get rowSpan=2) */}
      <tr style={{ color: '#cfd9e3' }} {...click}>
        <td style={{ ...tdL, borderTop: '1px solid #1a2d42' }} rowSpan={2}>{r.friday_date_ist}</td>
        <td style={{ ...tdL, borderTop: '1px solid #1a2d42' }} rowSpan={2}>{r.entry_hour_ist}h</td>
        <td style={{ ...tdL, borderTop: '1px solid #1a2d42' }} rowSpan={2}>
          <div style={{ fontSize: 10 }}>{r.expiry_bucket}</div>
          <div style={{ fontSize: 9, color: '#7a9bb5' }}>{r.expiry_date}</div>
        </td>
        <td style={{ ...tdR, borderTop: '1px solid #1a2d42' }} rowSpan={2}>{r.delta_target}</td>
        <td style={{ ...tdL, borderTop: '1px solid #1a2d42' }} rowSpan={2}>{r.entry_atm_iv_band}</td>
        {/* Per-leg cells (CE) */}
        <td style={tdL}>
          <span style={{
            background: '#1f6feb', color: '#fff', padding: '1px 6px',
            borderRadius: 3, fontSize: 9, fontWeight: 700,
          }}>CE</span>
        </td>
        <td style={tdR}>{r.call_strike.toLocaleString()}</td>
        <td style={tdR}>{fmtUsd(r.call_entry_mark)}</td>
        <td style={tdR}>{fmtUsd(r.exit_call_mark)}</td>
        <td style={tdR}>{r.call_entry_delta?.toFixed(3)}</td>
        <td style={tdR}>{fmtIv(r.call_entry_iv)}</td>
        <td style={{ ...tdR, color: pnlColor(r.call_leg_pnl_usd) }}>
          {fmtUsd(r.call_leg_pnl_usd)}
        </td>
        <td style={tdR}>{fmtUsd(r.call_leg_max_mtm_usd)}</td>
        <td style={tdR}>{fmtUsd(r.call_leg_min_mtm_usd)}</td>
        {/* Shared cells (rowSpan=2) */}
        <td style={sharedTopL} rowSpan={2}>
          <span style={{
            background: legWinnerColor(r.leg_winner), color: '#0a0e17',
            padding: '1px 6px', borderRadius: 3, fontSize: 9, fontWeight: 700,
          }}>{r.leg_winner}</span>
        </td>
        <td style={sharedTop} rowSpan={2}>{r.delta_skew?.toFixed(3)}</td>
        <td style={sharedTop} rowSpan={2}>{fmtPct(r.iv_skew_pct, 2)}</td>
        <td style={sharedTop} rowSpan={2}>{fmtUsd(r.premium_skew_usd)}</td>
        <td style={sharedTop} rowSpan={2}>{fmtUsd(r.credit_usd)}</td>
        <td style={sharedTop} rowSpan={2}>{fmtUsd(r.margin_used_usd_at_entry)}</td>
        <td style={{ ...sharedTop, color: pnlColor(r.gross_pnl_usd) }} rowSpan={2}>
          {fmtUsd(r.gross_pnl_usd)}
        </td>
        <td style={{ ...sharedTop, color: pnlColor(r.net_pnl_estimate_usd), fontWeight: 600 }} rowSpan={2}>
          {fmtUsd(r.net_pnl_estimate_usd)}
        </td>
        <td style={sharedTopL} rowSpan={2}>
          {r.loss_cause ? (
            <span style={{
              background: lossCauseColor(r.loss_cause),
              color: '#0a0e17',
              padding: '1px 6px', borderRadius: 3,
              fontSize: 9, fontWeight: 700, whiteSpace: 'nowrap',
            }}>
              {r.loss_cause}
            </span>
          ) : (
            <span style={{ color: '#586e7e', fontSize: 10 }}>—</span>
          )}
        </td>
        <td style={sharedTopL} rowSpan={2}>
          <div style={{ fontSize: 10 }}>{r.exit_reason}</div>
          <div style={{ fontSize: 9, color: r.is_win ? '#3fb950' : '#f85149' }}>
            {r.is_win ? 'WIN' : 'LOSS'}
          </div>
        </td>
      </tr>
      {/* Row 2 — PE leg */}
      <tr style={{ color: '#cfd9e3' }} {...click}>
        <td style={tdL}>
          <span style={{
            background: '#bf6fde', color: '#fff', padding: '1px 6px',
            borderRadius: 3, fontSize: 9, fontWeight: 700,
          }}>PE</span>
        </td>
        <td style={tdR}>{r.put_strike.toLocaleString()}</td>
        <td style={tdR}>{fmtUsd(r.put_entry_mark)}</td>
        <td style={tdR}>{fmtUsd(r.exit_put_mark)}</td>
        <td style={tdR}>{r.put_entry_delta?.toFixed(3)}</td>
        <td style={tdR}>{fmtIv(r.put_entry_iv)}</td>
        <td style={{ ...tdR, color: pnlColor(r.put_leg_pnl_usd) }}>
          {fmtUsd(r.put_leg_pnl_usd)}
        </td>
        <td style={tdR}>{fmtUsd(r.put_leg_max_mtm_usd)}</td>
        <td style={tdR}>{fmtUsd(r.put_leg_min_mtm_usd)}</td>
      </tr>
    </>
  );
}
