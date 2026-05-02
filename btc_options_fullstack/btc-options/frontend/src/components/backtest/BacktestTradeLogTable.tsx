import React, { useMemo, useState } from 'react';
import * as XLSX from 'xlsx';
import type { BacktestTrade, BacktestSummary } from '../../types/backtest';

interface Props {
  trades: BacktestTrade[];
  summary?: BacktestSummary;
  onSelectTrade?: (trade: BacktestTrade | null) => void;
  selectedTradeKey?: string | null;   // `${date}|${entry_time}` of currently-selected row
}

type SortKey =
  | 'date' | 'gross_pnl' | 'slippage_cost' | 'brokerage_cost'
  | 'net_pnl' | 'spot_at_entry' | 'spot_at_exit'
  | 'credit_pct' | 'quality_score' | 'pattern';

const PATTERN_COLOR: Record<string, string> = {
  A: '#3b82f6', B: '#ef4444', C: '#6b7280', D: '#f0b429', Other: '#475569',
};

const BAND_COLOR: Record<string, string> = {
  strong: '#10b981', standard: '#f0b429', marginal: '#d97706', skip: '#ef4444',
};

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null) return '—';
  return n.toFixed(digits);
}

function fmtSigned(n: number): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
}

// "2026-05-08" → "May 08"
function fmtExpiry(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00Z');
  if (isNaN(d.getTime())) return iso;
  const m = d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${m} ${dd}`;
}

function legsToString(legs: BacktestTrade['legs']): string {
  return legs.map(l =>
    `${l.action === 'BUY' ? '+' : '-'}${l.qty} ${l.strike}${l.type} ` +
    `${l.entry_mark.toFixed(2)}→${l.exit_mark.toFixed(2)} (exp ${l.expiry})`,
  ).join(' / ');
}

/**
 * Pull the first leg of the requested type and surface its key/value cell as a
 * compact two-line "strike / qty action" + "entry → exit" display. If the
 * trade has multiple CE or PE legs, only the first is shown — full detail is
 * still available in the CSV export.
 */
function legSummary(legs: BacktestTrade['legs'], type: 'CE' | 'PE') {
  return legs.find(l => l.type === type) ?? null;
}

export const BacktestTradeLogTable: React.FC<Props> = ({ trades, summary, onSelectTrade, selectedTradeKey }) => {
  const [sortKey, setSortKey] = useState<SortKey>('date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [showSkipped, setShowSkipped] = useState(false);

  const filtered = useMemo(() => {
    return showSkipped ? trades : trades.filter(t => !t.skipped);
  }, [trades, showSkipped]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const av: any = (a as any)[sortKey];
      const bv: any = (b as any)[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  const onSort = (k: SortKey) => {
    if (k === sortKey) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    else { setSortKey(k); setSortDir(k === 'date' ? 'asc' : 'desc'); }
  };

  const exportXlsx = () => {
    const activeTrades = trades.filter(t => !t.skipped);
    const tradeRows = activeTrades.map(t => {
      const ce = legSummary(t.legs, 'CE');
      const pe = legSummary(t.legs, 'PE');
      return {
        date: t.date,
        entry_time: t.entry_time ?? '',
        exit_time: t.exit_time ?? '',
        exit_reason: t.exit_reason ?? '',
        legs: legsToString(t.legs),
        ce_strike:     ce?.strike ?? '',
        ce_expiry:     ce?.expiry ?? '',
        ce_qty:        ce?.qty ?? '',
        ce_action:     ce?.action ?? '',
        ce_entry:      ce?.entry_mark ?? '',
        ce_exit:       ce?.exit_mark ?? '',
        ce_at_max_mtm:    t.max_leg_marks?.find(m => m.type === 'CE')?.mark ?? '',
        ce_delta_at_max:  t.max_leg_deltas?.find(d => d.type === 'CE')?.delta ?? '',
        ce_at_min_mtm:    t.min_leg_marks?.find(m => m.type === 'CE')?.mark ?? '',
        ce_delta_at_min:  t.min_leg_deltas?.find(d => d.type === 'CE')?.delta ?? '',
        ce_delta:         ce?.entry_delta ?? '',
        ce_iv:            ce?.entry_iv ?? '',
        pe_strike:     pe?.strike ?? '',
        pe_expiry:     pe?.expiry ?? '',
        pe_qty:        pe?.qty ?? '',
        pe_action:     pe?.action ?? '',
        pe_entry:      pe?.entry_mark ?? '',
        pe_exit:       pe?.exit_mark ?? '',
        pe_at_max_mtm:    t.max_leg_marks?.find(m => m.type === 'PE')?.mark ?? '',
        pe_delta_at_max:  t.max_leg_deltas?.find(d => d.type === 'PE')?.delta ?? '',
        pe_at_min_mtm:    t.min_leg_marks?.find(m => m.type === 'PE')?.mark ?? '',
        pe_delta_at_min:  t.min_leg_deltas?.find(d => d.type === 'PE')?.delta ?? '',
        pe_delta:         pe?.entry_delta ?? '',
        pe_iv:            pe?.entry_iv ?? '',
        atm_iv:           t.entry_atm_iv ?? '',
        hv:               t.entry_hv ?? '',
        abs_delta:     (ce?.entry_delta != null || pe?.entry_delta != null)
                         ? Math.abs((ce?.entry_delta ?? 0) + (pe?.entry_delta ?? 0))
                         : '',
        spot_at_entry:  t.spot_at_entry ?? '',
        spot_at_exit:   t.spot_at_exit ?? '',
        gross_pnl:      t.gross_pnl,
        entry_slip:     t.entry_slip,
        exit_slip:      t.exit_slip,
        peak_exit_slip: t.peak_exit_slip,
        entry_brk:      t.entry_brk,
        exit_brk:       t.exit_brk,
        peak_exit_brk:  t.peak_exit_brk,
        net_pnl:        t.net_pnl,
        max_mtm:        t.max_mtm,
        max_mtm_time:   t.max_mtm_time ?? '',
        max_pnl_net:    t.max_pnl_net,
        min_mtm:        t.min_mtm,
        min_mtm_time:   t.min_mtm_time ?? '',
        min_pnl_net:    t.min_pnl_net,
        margin_used:    t.margin_used ?? '',
        is_reentry:     t.is_reentry ? 'yes' : '',
      };
    });

    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(tradeRows), 'Trades');

    if (summary) {
      const statsRows = [
        { metric: 'Net P&L',              value: summary.net_pnl },
        { metric: 'Gross P&L',            value: summary.gross_pnl },
        { metric: 'Total Costs',           value: summary.total_costs },
        { metric: 'Total Trades',          value: summary.total_trades },
        { metric: 'Wins',                  value: summary.wins },
        { metric: 'Losses',               value: summary.losses },
        { metric: 'Breakevens',           value: summary.breakevens },
        { metric: 'Win Rate %',           value: summary.win_rate_pct },
        { metric: 'Avg Win',              value: summary.avg_win },
        { metric: 'Avg Loss',             value: summary.avg_loss },
        { metric: 'Total Win P&L',        value: summary.total_wins_pnl },
        { metric: 'Total Loss P&L',       value: summary.total_losses_pnl },
        { metric: 'Expectancy / Trade',   value: summary.expectancy },
        { metric: 'Sharpe (daily, ann.)', value: summary.sharpe_daily },
        { metric: 'Max Drawdown',         value: summary.max_drawdown },
        { metric: 'Max Drawdown %',       value: summary.max_drawdown_pct },
        { metric: 'Max DD Peak Date',     value: summary.max_dd_peak_date ?? '' },
        { metric: 'Max DD Trough Date',   value: summary.max_dd_trough_date ?? '' },
        { metric: 'Avg Margin Used',      value: summary.avg_margin_used },
        { metric: 'Return on Margin %',   value: summary.return_on_margin_pct },
        { metric: 'Avg Max MTM',          value: summary.avg_max_mtm },
        { metric: 'Avg Max Net',          value: summary.avg_max_pnl_net },
        { metric: 'Avg Min MTM',          value: summary.avg_min_mtm },
        { metric: 'Avg Min Net',          value: summary.avg_min_pnl_net },
        { metric: 'Best Max MTM',         value: summary.best_max_mtm },
        { metric: 'Best Max MTM Date',    value: summary.best_max_mtm_date ?? '' },
        { metric: 'Best Max Net',         value: summary.best_max_pnl_net },
        { metric: 'Best Max Net Date',    value: summary.best_max_pnl_net_date ?? '' },
        { metric: 'Worst Min MTM',        value: summary.worst_min_mtm },
        { metric: 'Worst Min MTM Date',   value: summary.worst_min_mtm_date ?? '' },
        { metric: 'Worst Min Net',        value: summary.worst_min_pnl_net },
        { metric: 'Worst Min Net Date',   value: summary.worst_min_pnl_net_date ?? '' },
      ];
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(statsRows), 'Stats');
    }

    const firstName = activeTrades[0]?.date ?? 'export';
    const lastName  = activeTrades[activeTrades.length - 1]?.date ?? '';
    XLSX.writeFile(wb, `backtest_${firstName}_${lastName}.xlsx`);
  };

  const Th: React.FC<{k: SortKey, children: React.ReactNode, align?: 'left' | 'right' | 'center'}> =
    ({ k, children, align }) => (
      <th
        onClick={() => onSort(k)}
        style={{
          padding: '6px 10px', textAlign: align ?? 'left',
          cursor: 'pointer', userSelect: 'none',
          color: sortKey === k ? '#e6edf3' : '#7a9bb5',
          fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap',
          borderBottom: '1px solid #1a2d42',
        }}
      >
        {children}{sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
      </th>
    );

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42',
      borderRadius: 6, overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '8px 12px', borderBottom: '1px solid #1a2d42',
      }}>
        <span style={{ color: '#c9d1d9', fontSize: 13, fontWeight: 600 }}>
          Trade Log
        </span>
        <span style={{ color: '#7a9bb5', fontSize: 11 }}>
          {filtered.length} {showSkipped ? 'rows' : 'trades'}
        </span>
        <label style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 'auto', cursor: 'pointer' }}>
          <input
            type="checkbox" checked={showSkipped}
            onChange={e => setShowSkipped(e.target.checked)}
            style={{ marginRight: 6, verticalAlign: 'middle' }}
          />
          Show skipped days
        </label>
        <button
          onClick={exportXlsx}
          style={{
            background: '#1f6feb', border: 'none', color: '#fff',
            padding: '4px 12px', borderRadius: 4, fontSize: 11,
            fontWeight: 600, cursor: 'pointer',
          }}
        >
          Export XLSX
        </button>
      </div>

      <div style={{ overflow: 'auto', maxHeight: 460 }}>
        <table style={{
          width: '100%', borderCollapse: 'collapse',
          fontSize: 12, color: '#c9d1d9',
        }}>
          <thead style={{ background: '#080e16', position: 'sticky', top: 0, zIndex: 1 }}>
            <tr>
              <Th k="date">Date</Th>
              <th style={thStyle}>Entry</th>
              <th style={thStyle}>Exit</th>
              <th style={thStyle}>Reason</th>
              <Th k="pattern" align="center">Patt</Th>
              <Th k="credit_pct" align="right">Credit%</Th>
              <Th k="quality_score" align="right">Qty</Th>
              <th style={thStyle}>Leg</th>
              <th style={thStyle} title="Expiry of this leg">Expiry</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Entry</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Exit</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Premium at Max MTM moment">@Max</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Delta at Max MTM moment">Δ@Max</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Premium at Min MTM moment">@Min</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Delta at Min MTM moment">Δ@Min</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Delta at entry">Δ</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Implied volatility at entry (%)">IV %</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Absolute net delta = |CE Δ + PE Δ|; 0 = delta-neutral">Abs Δ</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="ATM IV at entry (%)">ATM IV</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Realized volatility at entry — 7d rolling, 5m bars, annualized (%)">HV</th>
              <Th k="spot_at_entry" align="right">Spot In</Th>
              <Th k="spot_at_exit"  align="right">Spot Out</Th>
              <Th k="gross_pnl"     align="right">Gross</Th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Entry slippage">E.Slip</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Exit slippage at end-of-trade marks">X.Slip</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Exit slippage at peak-MTM marks">Pk.Slip</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Entry brokerage">E.Brk</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Exit brokerage at end-of-trade marks">X.Brk</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Exit brokerage at peak-MTM marks">Pk.Brk</th>
              <Th k="net_pnl"       align="right">Net</Th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Max MTM (gross − entry slip)">Max MTM</th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Max MTM − entry brk − peak exit slip − peak exit brk">Max Net</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Min MTM</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Min Net</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Margin</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t, i) => {
              const bg = i % 2 ? '#0a1019' : 'transparent';
              const tradeBorder = '1px solid #1a2d42';
              const legBorder   = '1px solid #0d1825';

              if (t.skipped) {
                return (
                  <tr key={i} style={{ background: bg }}>
                    <td style={{ ...tdStyle, background: bg, borderBottom: tradeBorder }}>{t.date}</td>
                    <td style={{ ...tdStyle, background: bg, borderBottom: tradeBorder }} colSpan={34}>
                      <span style={{ color: '#4a6a85', fontStyle: 'italic' }}>
                        skipped — {t.skip_reason}
                      </span>
                    </td>
                  </tr>
                );
              }

              const tone = t.net_pnl >= 0 ? '#00e5a0' : '#ff4d6a';
              const ce   = legSummary(t.legs, 'CE');
              const pe   = legSummary(t.legs, 'PE');
              const absD = Math.abs((ce?.entry_delta ?? 0) + (pe?.entry_delta ?? 0));

              const s  = (extra?: React.CSSProperties): React.CSSProperties =>
                ({ ...tdStyle,  background: bg, ...extra });
              const sr = (extra?: React.CSSProperties): React.CSSProperties =>
                ({ ...tdRight, background: bg, ...extra });

              const fmtLeg = (l: typeof ce, type: 'CE' | 'PE') => (
                <>
                  <span style={{
                    color: type === 'CE' ? '#58a6ff' : '#ffa940',
                    fontSize: 9, fontWeight: 700, marginRight: 4,
                  }}>{type}</span>
                  {l ? `${l.action === 'BUY' ? '+' : '-'}${l.qty} ${l.strike}` : '—'}
                </>
              );

              const tKey = `${t.date}|${t.entry_time ?? ''}`;
              const isSelected = selectedTradeKey === tKey;
              const rowClickProps = onSelectTrade ? {
                onClick: () => onSelectTrade(isSelected ? null : t),
                style: { cursor: 'pointer' as const },
              } : {};
              const selStyle: React.CSSProperties = isSelected
                ? { boxShadow: 'inset 3px 0 0 #00d4ff' } : {};

              return (
                <React.Fragment key={i}>
                  {/* Row 1 — CE + all shared columns (rowSpan=2) */}
                  <tr {...rowClickProps}>
                    <td rowSpan={2} style={s({ borderBottom: tradeBorder, ...selStyle })}>
                      {t.date}{t.is_reentry && <span style={{ color: '#ffa940', marginLeft: 4 }}>↻</span>}
                    </td>
                    <td rowSpan={2} style={s({ borderBottom: tradeBorder })}>{t.entry_time}</td>
                    <td rowSpan={2} style={s({ borderBottom: tradeBorder })}>{t.exit_time}</td>
                    <td rowSpan={2} style={s({ borderBottom: tradeBorder })}>{t.exit_reason}</td>
                    {/* Strangle analytics chips (rowSpan=2) */}
                    <td rowSpan={2} style={s({ borderBottom: tradeBorder, textAlign: 'center' })}>
                      {t.pattern ? (
                        <span style={{
                          display: 'inline-block', padding: '2px 6px', fontSize: 10,
                          fontWeight: 700, borderRadius: 3, color: 'white',
                          background: PATTERN_COLOR[t.pattern] ?? '#475569',
                        }}>{t.pattern}</span>
                      ) : '—'}
                    </td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder, color: '#c9d1d9' })}>
                      {t.credit_pct != null ? `${(t.credit_pct * 100).toFixed(3)}%` : '—'}
                    </td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>
                      {t.quality_score != null ? (
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4 }}>
                          <span style={{ color: '#c9d1d9' }}>{t.quality_score.toFixed(0)}</span>
                          {t.size_band && (
                            <span style={{
                              padding: '1px 5px', fontSize: 9, fontWeight: 700,
                              borderRadius: 3, color: 'white',
                              background: BAND_COLOR[t.size_band] ?? '#475569',
                            }}>{t.size_band[0].toUpperCase()}</span>
                          )}
                        </div>
                      ) : '—'}
                    </td>
                    {/* CE-specific cells */}
                    <td style={s({ fontFamily: 'monospace', fontSize: 11, color: '#7a9bb5', borderBottom: legBorder })}>{fmtLeg(ce, 'CE')}</td>
                    <td style={s({ fontSize: 11, color: '#7a9bb5', borderBottom: legBorder })} title={ce?.expiry}>{fmtExpiry(ce?.expiry)}</td>
                    <td style={sr({ borderBottom: legBorder })}>{ce ? ce.entry_mark.toFixed(2) : '—'}</td>
                    <td style={sr({ borderBottom: legBorder })}>{ce ? ce.exit_mark.toFixed(2) : '—'}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: legBorder })}>{fmtNum(t.max_leg_marks?.find(m => m.type === 'CE')?.mark)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: legBorder })}>{fmtNum(t.max_leg_deltas?.find(d => d.type === 'CE')?.delta, 3)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: legBorder })}>{fmtNum(t.min_leg_marks?.find(m => m.type === 'CE')?.mark)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: legBorder })}>{fmtNum(t.min_leg_deltas?.find(d => d.type === 'CE')?.delta, 3)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: legBorder })}>{ce?.entry_delta != null ? ce.entry_delta.toFixed(3) : '—'}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: legBorder })}>{ce?.entry_iv != null && ce.entry_iv > 0 ? ce.entry_iv.toFixed(1) : '—'}</td>
                    {/* Shared cells — rowSpan=2 */}
                    <td rowSpan={2} style={sr({ color: absD < 0.05 ? '#00e5a0' : '#c9d1d9', borderBottom: tradeBorder })}>
                      {(ce?.entry_delta != null || pe?.entry_delta != null) ? absD.toFixed(3) : '—'}
                    </td>
                    <td rowSpan={2} style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>
                      {t.entry_atm_iv != null && t.entry_atm_iv > 0 ? t.entry_atm_iv.toFixed(1) : '—'}
                    </td>
                    <td rowSpan={2} style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>
                      {t.entry_hv != null && t.entry_hv > 0 ? t.entry_hv.toFixed(1) : '—'}
                    </td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.spot_at_entry, 0)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.spot_at_exit, 0)}</td>
                    <td rowSpan={2} style={sr({ color: t.gross_pnl >= 0 ? '#00e5a0' : '#ff4d6a', borderBottom: tradeBorder })}>{fmtSigned(t.gross_pnl)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.entry_slip)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.exit_slip)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.peak_exit_slip)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.entry_brk)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.exit_brk)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.peak_exit_brk)}</td>
                    <td rowSpan={2} style={sr({ color: tone, fontWeight: 700, borderBottom: tradeBorder })}>{fmtSigned(t.net_pnl)}</td>
                    <td rowSpan={2} style={sr({ color: '#00e5a0', borderBottom: tradeBorder })}>
                      {fmtSigned(t.max_mtm)}
                      {t.max_mtm_time && <div style={{ color: '#7a9bb5', fontSize: 10 }}>{t.max_mtm_time}</div>}
                    </td>
                    <td rowSpan={2} style={sr({ color: t.max_pnl_net >= 0 ? '#00e5a0' : '#ff4d6a', borderBottom: tradeBorder })}>{fmtSigned(t.max_pnl_net)}</td>
                    <td rowSpan={2} style={sr({ color: '#ff4d6a', borderBottom: tradeBorder })}>
                      {fmtSigned(t.min_mtm)}
                      {t.min_mtm_time && <div style={{ color: '#7a9bb5', fontSize: 10 }}>{t.min_mtm_time}</div>}
                    </td>
                    <td rowSpan={2} style={sr({ color: t.min_pnl_net >= 0 ? '#00e5a0' : '#ff4d6a', borderBottom: tradeBorder })}>{fmtSigned(t.min_pnl_net)}</td>
                    <td rowSpan={2} style={sr({ borderBottom: tradeBorder })}>
                      {t.margin_used != null ? `$${t.margin_used.toFixed(0)}` : '—'}
                    </td>
                  </tr>
                  {/* Row 2 — PE leg only */}
                  <tr>
                    <td style={s({ fontFamily: 'monospace', fontSize: 11, color: '#7a9bb5', borderBottom: tradeBorder })}>{fmtLeg(pe, 'PE')}</td>
                    <td style={s({ fontSize: 11, color: '#7a9bb5', borderBottom: tradeBorder })} title={pe?.expiry}>{fmtExpiry(pe?.expiry)}</td>
                    <td style={sr({ borderBottom: tradeBorder })}>{pe ? pe.entry_mark.toFixed(2) : '—'}</td>
                    <td style={sr({ borderBottom: tradeBorder })}>{pe ? pe.exit_mark.toFixed(2) : '—'}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>{fmtNum(t.max_leg_marks?.find(m => m.type === 'PE')?.mark)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>{fmtNum(t.max_leg_deltas?.find(d => d.type === 'PE')?.delta, 3)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>{fmtNum(t.min_leg_marks?.find(m => m.type === 'PE')?.mark)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>{fmtNum(t.min_leg_deltas?.find(d => d.type === 'PE')?.delta, 3)}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>{pe?.entry_delta != null ? pe.entry_delta.toFixed(3) : '—'}</td>
                    <td style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>{pe?.entry_iv != null && pe.entry_iv > 0 ? pe.entry_iv.toFixed(1) : '—'}</td>
                  </tr>
                </React.Fragment>
              );
            })}
            {sorted.length === 0 && (
              <tr><td style={tdStyle} colSpan={35}>
                <div style={{ textAlign: 'center', color: '#4a6a85', padding: 24 }}>No rows</div>
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const thStyle: React.CSSProperties = {
  padding: '6px 10px', textAlign: 'left', color: '#7a9bb5',
  fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap',
  borderBottom: '1px solid #1a2d42',
};
const tdStyle: React.CSSProperties = {
  padding: '6px 10px', whiteSpace: 'nowrap',
};
const tdRight: React.CSSProperties = {
  padding: '6px 10px', whiteSpace: 'nowrap', textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
};
