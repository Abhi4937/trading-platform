import React, { useMemo, useState } from 'react';
import * as XLSX from 'xlsx';
import type { BacktestTrade } from '../../types/backtest';

interface Props {
  trades: BacktestTrade[];
}

type SortKey =
  | 'date' | 'gross_pnl' | 'slippage_cost' | 'brokerage_cost'
  | 'net_pnl' | 'spot_at_entry' | 'spot_at_exit';

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null) return '—';
  return n.toFixed(digits);
}

function fmtSigned(n: number): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
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

export const BacktestTradeLogTable: React.FC<Props> = ({ trades }) => {
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

  const exportCsv = () => {
    const rows = trades.map(t => {
      const ce = legSummary(t.legs, 'CE');
      const pe = legSummary(t.legs, 'PE');
      return {
        date: t.date,
        entry_time: t.entry_time ?? '',
        exit_time: t.exit_time ?? '',
        exit_reason: t.exit_reason ?? '',
        legs: legsToString(t.legs),
        ce_strike: ce?.strike ?? '',
        ce_qty:    ce?.qty ?? '',
        ce_action: ce?.action ?? '',
        ce_entry:  ce?.entry_mark ?? '',
        ce_exit:   ce?.exit_mark ?? '',
        pe_strike: pe?.strike ?? '',
        pe_qty:    pe?.qty ?? '',
        pe_action: pe?.action ?? '',
        pe_entry:  pe?.entry_mark ?? '',
        pe_exit:   pe?.exit_mark ?? '',
        spot_at_entry: t.spot_at_entry ?? '',
        spot_at_exit:  t.spot_at_exit ?? '',
        gross_pnl: t.gross_pnl,
        slippage_cost: t.slippage_cost,
        brokerage_cost: t.brokerage_cost,
        net_pnl: t.net_pnl,
        max_mtm: t.max_mtm,
        max_mtm_time: t.max_mtm_time ?? '',
        max_pnl_net: t.max_pnl_net,
        min_mtm: t.min_mtm,
        min_mtm_time: t.min_mtm_time ?? '',
        min_pnl_net: t.min_pnl_net,
        margin_used: t.margin_used ?? '',
        is_reentry: t.is_reentry ? 'yes' : '',
        skipped: t.skipped ? 'yes' : '',
        skip_reason: t.skip_reason ?? '',
      };
    });
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.json_to_sheet(rows);
    XLSX.utils.book_append_sheet(wb, ws, 'Trades');
    XLSX.writeFile(wb, `backtest_trades_${trades[0]?.date ?? 'export'}.csv`,
                   { bookType: 'csv' });
  };

  const Th: React.FC<{k: SortKey, children: React.ReactNode, align?: 'left' | 'right'}> =
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
          onClick={exportCsv}
          style={{
            background: '#1f6feb', border: 'none', color: '#fff',
            padding: '4px 12px', borderRadius: 4, fontSize: 11,
            fontWeight: 600, cursor: 'pointer',
          }}
        >
          Export CSV
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
              <th style={thStyle}>CE Leg</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>CE Entry</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>CE Exit</th>
              <th style={thStyle}>PE Leg</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>PE Entry</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>PE Exit</th>
              <Th k="spot_at_entry" align="right">Spot In</Th>
              <Th k="spot_at_exit"  align="right">Spot Out</Th>
              <Th k="gross_pnl"     align="right">Gross</Th>
              <Th k="slippage_cost" align="right">Slip</Th>
              <Th k="brokerage_cost" align="right">Brk</Th>
              <Th k="net_pnl"       align="right">Net</Th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Max MTM</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Max Net</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Min MTM</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Min Net</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Margin</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t, i) => {
              if (t.skipped) {
                return (
                  <tr key={i} style={{ background: i % 2 ? '#0a1019' : 'transparent' }}>
                    <td style={tdStyle}>{t.date}</td>
                    <td style={tdStyle} colSpan={20}>
                      <span style={{ color: '#4a6a85', fontStyle: 'italic' }}>
                        skipped — {t.skip_reason}
                      </span>
                    </td>
                  </tr>
                );
              }
              const tone = t.net_pnl >= 0 ? '#00e5a0' : '#ff4d6a';
              const ce = legSummary(t.legs, 'CE');
              const pe = legSummary(t.legs, 'PE');
              const legCellStyle = { ...tdStyle, fontFamily: 'monospace', color: '#7a9bb5', fontSize: 11 } as React.CSSProperties;
              const fmtLeg = (l: typeof ce) => l
                ? `${l.action === 'BUY' ? '+' : '-'}${l.qty} ${l.strike}${l.type}`
                : '—';
              return (
                <tr key={i} style={{ background: i % 2 ? '#0a1019' : 'transparent' }}>
                  <td style={tdStyle}>
                    {t.date}{t.is_reentry && <span style={{ color: '#ffa940', marginLeft: 4 }}>↻</span>}
                  </td>
                  <td style={tdStyle}>{t.entry_time}</td>
                  <td style={tdStyle}>{t.exit_time}</td>
                  <td style={tdStyle}>{t.exit_reason}</td>
                  <td style={legCellStyle}>{fmtLeg(ce)}</td>
                  <td style={tdRight}>{ce ? ce.entry_mark.toFixed(2) : '—'}</td>
                  <td style={tdRight}>{ce ? ce.exit_mark.toFixed(2) : '—'}</td>
                  <td style={legCellStyle}>{fmtLeg(pe)}</td>
                  <td style={tdRight}>{pe ? pe.entry_mark.toFixed(2) : '—'}</td>
                  <td style={tdRight}>{pe ? pe.exit_mark.toFixed(2) : '—'}</td>
                  <td style={tdRight}>{fmtNum(t.spot_at_entry, 0)}</td>
                  <td style={tdRight}>{fmtNum(t.spot_at_exit, 0)}</td>
                  <td style={{ ...tdRight, color: t.gross_pnl >= 0 ? '#00e5a0' : '#ff4d6a' }}>
                    {fmtSigned(t.gross_pnl)}
                  </td>
                  <td style={tdRight}>{fmtNum(t.slippage_cost)}</td>
                  <td style={tdRight}>{fmtNum(t.brokerage_cost)}</td>
                  <td style={{ ...tdRight, color: tone, fontWeight: 700 }}>
                    {fmtSigned(t.net_pnl)}
                  </td>
                  <td style={{ ...tdRight, color: '#00e5a0' }}>
                    {fmtSigned(t.max_mtm)}
                    {t.max_mtm_time && (
                      <div style={{ color: '#7a9bb5', fontSize: 10 }}>{t.max_mtm_time}</div>
                    )}
                  </td>
                  <td style={{ ...tdRight, color: t.max_pnl_net >= 0 ? '#00e5a0' : '#ff4d6a' }}>
                    {fmtSigned(t.max_pnl_net)}
                  </td>
                  <td style={{ ...tdRight, color: '#ff4d6a' }}>
                    {fmtSigned(t.min_mtm)}
                    {t.min_mtm_time && (
                      <div style={{ color: '#7a9bb5', fontSize: 10 }}>{t.min_mtm_time}</div>
                    )}
                  </td>
                  <td style={{ ...tdRight, color: t.min_pnl_net >= 0 ? '#00e5a0' : '#ff4d6a' }}>
                    {fmtSigned(t.min_pnl_net)}
                  </td>
                  <td style={tdRight}>
                    {t.margin_used != null ? `$${t.margin_used.toFixed(0)}` : '—'}
                  </td>
                </tr>
              );
            })}
            {sorted.length === 0 && (
              <tr><td style={tdStyle} colSpan={21}>
                <div style={{ textAlign: 'center', color: '#4a6a85', padding: 24 }}>
                  No rows
                </div>
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
