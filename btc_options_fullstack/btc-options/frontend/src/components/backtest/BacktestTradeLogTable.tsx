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
  | 'credit_pct' | 'quality_score' | 'pattern'
  | 'iv_regime_premium_pct' | 'excess_over_fair_pct';

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

  // Aggregate calibration baselines across all non-skipped trades.
  // Same-expiry trades typically share a calibration bucket so the baseline
  // values are stable; we average them so the banner is meaningful even when
  // multiple buckets are present.
  const baselines = useMemo(() => {
    const xs = trades.filter(t => !t.skipped);
    const avg = (key: keyof BacktestTrade) => {
      const vs = xs.map(t => t[key]).filter((v): v is number => typeof v === 'number');
      if (!vs.length) return null;
      return vs.reduce((a, b) => a + b, 0) / vs.length;
    };
    return {
      n: xs.length,
      credit_pct:           avg('credit_pct'),
      structural_pct:       avg('structural_credit_pct'),
      fair_at_ivp_pct:      avg('fair_credit_at_ivp'),
      iv_regime_pct:        avg('iv_regime_premium_pct'),
      excess_pct:           avg('excess_over_fair_pct'),
      atm_iv:               avg('entry_atm_iv'),
      hv:                   avg('entry_hv'),
      ivp:                  avg('ivp_atm_7d_90d_at_entry'),
    };
  }, [trades]);

  const exportXlsx = () => {
    const activeTrades = trades.filter(t => !t.skipped);
    const tradeRows = activeTrades.map(t => {
      const ce = legSummary(t.legs, 'CE');
      const pe = legSummary(t.legs, 'PE');
      return {
        date: t.date,
        exit_date: t.exit_date ?? '',
        entry_time: t.entry_time ?? '',
        exit_time: t.exit_time ?? '',
        exit_reason: t.exit_reason ?? '',
        credit_pct: t.credit_pct != null ? t.credit_pct * 100 : '',
        structural_credit_pct: t.structural_credit_pct != null ? t.structural_credit_pct * 100 : '',
        fair_credit_at_ivp_pct: t.fair_credit_at_ivp != null ? t.fair_credit_at_ivp * 100 : '',
        iv_regime_premium_pct: t.iv_regime_premium_pct != null ? t.iv_regime_premium_pct * 100 : '',
        excess_over_fair_pct: t.excess_over_fair_pct != null ? t.excess_over_fair_pct * 100 : '',
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

  const Th: React.FC<{k: SortKey, children: React.ReactNode, align?: 'left' | 'right' | 'center', title?: string}> =
    ({ k, children, align, title }) => (
      <th
        onClick={() => onSort(k)}
        title={title}
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

      {/* Calibration baselines banner — averages across all non-skipped trades */}
      {baselines.n > 0 && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center',
          padding: '8px 12px', borderBottom: '1px solid #1a2d42',
          background: '#0a1019', fontSize: 11,
        }}>
          <span style={{ color: '#7a9bb5' }}>
            <strong style={{ color: '#c9d1d9' }}>Baselines</strong> (avg of {baselines.n} trades):
          </span>
          <BL label="Actual Credit%" value={baselines.credit_pct} fmt="pct3" tone="neutral"
              tip="Avg actual credit % captured at entry" />
          <BL label="Struct%"        value={baselines.structural_pct} fmt="pct3" tone="neutral"
              tip="Structural baseline credit% from calibration (DTE × Δ × spot, IV-neutral)" />
          <BL label="Fair@IVP"       value={baselines.fair_at_ivp_pct} fmt="pct3" tone="neutral"
              tip="Fair credit% at current IVP (calibration bucket median, includes IV regime)" />
          <BL label="IV-Prem"        value={baselines.iv_regime_pct} fmt="pct3" tone="signed"
              tip="Avg IV regime premium = fair − structural" />
          <BL label="Excess"         value={baselines.excess_pct} fmt="pct3" tone="signed"
              tip="Avg excess over fair = actual − fair" />
          <span style={{ color: '#3a4d63' }}>|</span>
          <BL label="ATM IV"         value={baselines.atm_iv} fmt="pct1" tone="neutral"
              tip="Avg ATM IV at entry (already in %)" />
          <BL label="HV"             value={baselines.hv} fmt="pct1" tone="neutral"
              tip="Avg 7d realized vol at entry (annualized %)" />
          <BL label="IVP"            value={baselines.ivp} fmt="pct1" tone="neutral"
              tip="Avg IV percentile (90d rank of 7d ATM IV)" />
        </div>
      )}

      <div style={{ overflow: 'auto', maxHeight: 460 }}>
        <table style={{
          width: '100%', borderCollapse: 'collapse',
          fontSize: 12, color: '#c9d1d9',
        }}>
          <thead style={{ background: '#080e16', position: 'sticky', top: 0, zIndex: 1 }}>
            <tr>
              <Th k="date">Date</Th>
              <th style={thStyle} title="Date the trade exited (IST)">Exit Date</th>
              <th style={thStyle}>Entry</th>
              <th style={thStyle}>Exit</th>
              <th style={thStyle}>Reason</th>
              <Th k="pattern" align="center">Patt</Th>
              <Th k="credit_pct" align="right">Credit%</Th>
              <th style={{ ...thStyle, textAlign: 'right' }} title="Structural baseline credit% from calibration (DTE × Δ × spot)">Struct%</th>
              <Th k="iv_regime_premium_pct" align="right">IV-Prem%</Th>
              <Th k="excess_over_fair_pct" align="right">Excess%</Th>
              <Th k="quality_score" align="right" title="Calibration quality score (0-100) + size band (S=Strong / S=Standard / M=Marginal / K=Skip)">Score</Th>
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
              <th style={{ ...thStyle, textAlign: 'right' }} title="MTM at actual exit time (gross − entry slip only; no brokerage, no exit slip)">Exit MTM</th>
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
                    <td style={{ ...tdStyle, background: bg, borderBottom: tradeBorder }} colSpan={39}>
                      <span style={{ color: '#4a6a85', fontStyle: 'italic' }}>
                        skipped — {t.skip_reason}
                      </span>
                    </td>
                  </tr>
                );
              }

              const tone = t.net_pnl >= 0 ? '#00e5a0' : '#ff4d6a';
              // Net delta across all legs (sums raw deltas — CE +, PE −, cancels for delta-neutral)
              const absD = Math.abs(t.legs.reduce((sum, l) => sum + (l.entry_delta ?? 0), 0));
              const N = Math.max(1, t.legs.length);

              const s  = (extra?: React.CSSProperties): React.CSSProperties =>
                ({ ...tdStyle,  background: bg, ...extra });
              const sr = (extra?: React.CSSProperties): React.CSSProperties =>
                ({ ...tdRight, background: bg, ...extra });

              const fmtLegCell = (l: BacktestTrade['legs'][number]) => (
                <>
                  <span style={{
                    color: l.type === 'CE' ? '#58a6ff' : '#ffa940',
                    fontSize: 9, fontWeight: 700, marginRight: 4,
                  }}>{l.type}</span>
                  {`${l.action === 'BUY' ? '+' : '-'}${l.qty} ${l.strike}`}
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
                  {t.legs.map((leg, legIdx) => {
                    const isFirst = legIdx === 0;
                    const isLast  = legIdx === t.legs.length - 1;
                    const border  = isLast ? tradeBorder : legBorder;
                    // Match peak/trough marks+deltas by (strike, type) so multi-CE/PE legs disambiguate
                    const maxM = t.max_leg_marks?.find(m => m.type === leg.type && m.strike === leg.strike);
                    const minM = t.min_leg_marks?.find(m => m.type === leg.type && m.strike === leg.strike);
                    const maxD = t.max_leg_deltas?.find(d => d.type === leg.type && d.strike === leg.strike);
                    const minD = t.min_leg_deltas?.find(d => d.type === leg.type && d.strike === leg.strike);

                    return (
                      <tr key={legIdx} {...rowClickProps}>
                        {isFirst && (
                          <>
                            <td rowSpan={N} style={s({ borderBottom: tradeBorder, ...selStyle })}>
                              {t.date}{t.is_reentry && <span style={{ color: '#ffa940', marginLeft: 4 }}>↻</span>}
                            </td>
                            <td rowSpan={N} style={s({ borderBottom: tradeBorder, color: t.exit_date && t.exit_date !== t.date ? '#ffa940' : '#7a9bb5' })}>
                              {t.exit_date ?? '—'}
                            </td>
                            <td rowSpan={N} style={s({ borderBottom: tradeBorder })}>{t.entry_time}</td>
                            <td rowSpan={N} style={s({ borderBottom: tradeBorder })}>{t.exit_time}</td>
                            <td rowSpan={N} style={s({ borderBottom: tradeBorder })}>{t.exit_reason}</td>
                            <td rowSpan={N} style={s({ borderBottom: tradeBorder, textAlign: 'center' })}>
                              {t.pattern ? (
                                <span style={{
                                  display: 'inline-block', padding: '2px 6px', fontSize: 10,
                                  fontWeight: 700, borderRadius: 3, color: 'white',
                                  background: PATTERN_COLOR[t.pattern] ?? '#475569',
                                }}>{t.pattern}</span>
                              ) : '—'}
                            </td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder, color: '#c9d1d9' })}
                                title={t.fair_credit_at_ivp != null ? `Fair (incl. IV regime): ${(t.fair_credit_at_ivp * 100).toFixed(3)}%` : ''}>
                              {t.credit_pct != null ? `${(t.credit_pct * 100).toFixed(3)}%` : '—'}
                            </td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder, color: '#7a9bb5' })}
                                title="Structural baseline credit% from calibration (DTE × Δ × spot, IV-neutral)">
                              {t.structural_credit_pct != null ? `${(t.structural_credit_pct * 100).toFixed(3)}%` : '—'}
                            </td>
                            <td rowSpan={N} style={sr({
                              borderBottom: tradeBorder,
                              color: t.iv_regime_premium_pct == null ? '#7a9bb5'
                                    : t.iv_regime_premium_pct > 0 ? '#00e5a0' : '#ff4d6a',
                            })} title="Extra credit% from current IV vs structural baseline (fair − structural)">
                              {t.iv_regime_premium_pct != null ? `${t.iv_regime_premium_pct >= 0 ? '+' : ''}${(t.iv_regime_premium_pct * 100).toFixed(3)}%` : '—'}
                            </td>
                            <td rowSpan={N} style={sr({
                              borderBottom: tradeBorder,
                              color: t.excess_over_fair_pct == null ? '#7a9bb5'
                                    : t.excess_over_fair_pct > 0 ? '#00e5a0' : '#ff4d6a',
                            })} title="Actual credit% − fair credit. Positive = market paying more than historical fair.">
                              {t.excess_over_fair_pct != null ? `${t.excess_over_fair_pct >= 0 ? '+' : ''}${(t.excess_over_fair_pct * 100).toFixed(3)}%` : '—'}
                            </td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>
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
                          </>
                        )}
                        {/* Per-leg cells — one row per leg */}
                        <td style={s({ fontFamily: 'monospace', fontSize: 11, color: '#7a9bb5', borderBottom: border })}>{fmtLegCell(leg)}</td>
                        <td style={s({ fontSize: 11, color: '#7a9bb5', borderBottom: border })} title={leg.expiry}>{fmtExpiry(leg.expiry)}</td>
                        <td style={sr({ borderBottom: border })}>{leg.entry_mark.toFixed(2)}</td>
                        <td style={sr({ borderBottom: border })}>{leg.exit_mark.toFixed(2)}</td>
                        <td style={sr({ color: '#7a9bb5', borderBottom: border })}>{fmtNum(maxM?.mark)}</td>
                        <td style={sr({ color: '#7a9bb5', borderBottom: border })}>{fmtNum(maxD?.delta, 3)}</td>
                        <td style={sr({ color: '#7a9bb5', borderBottom: border })}>{fmtNum(minM?.mark)}</td>
                        <td style={sr({ color: '#7a9bb5', borderBottom: border })}>{fmtNum(minD?.delta, 3)}</td>
                        <td style={sr({ color: '#7a9bb5', borderBottom: border })}>{leg.entry_delta != null ? leg.entry_delta.toFixed(3) : '—'}</td>
                        <td style={sr({ color: '#7a9bb5', borderBottom: border })}>{leg.entry_iv != null && leg.entry_iv > 0 ? leg.entry_iv.toFixed(1) : '—'}</td>
                        {isFirst && (
                          <>
                            <td rowSpan={N} style={sr({ color: absD < 0.05 ? '#00e5a0' : '#c9d1d9', borderBottom: tradeBorder })}>
                              {t.legs.some(l => l.entry_delta != null) ? absD.toFixed(3) : '—'}
                            </td>
                            <td rowSpan={N} style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>
                              {t.entry_atm_iv != null && t.entry_atm_iv > 0 ? t.entry_atm_iv.toFixed(1) : '—'}
                            </td>
                            <td rowSpan={N} style={sr({ color: '#7a9bb5', borderBottom: tradeBorder })}>
                              {t.entry_hv != null && t.entry_hv > 0 ? t.entry_hv.toFixed(1) : '—'}
                            </td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.spot_at_entry, 0)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.spot_at_exit, 0)}</td>
                            <td rowSpan={N} style={sr({ color: t.gross_pnl >= 0 ? '#00e5a0' : '#ff4d6a', borderBottom: tradeBorder })}>{fmtSigned(t.gross_pnl)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.entry_slip)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.exit_slip)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.peak_exit_slip)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.entry_brk)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.exit_brk)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>{fmtNum(t.peak_exit_brk)}</td>
                            <td rowSpan={N} style={sr({ color: tone, fontWeight: 700, borderBottom: tradeBorder })}>{fmtSigned(t.net_pnl)}</td>
                            <td rowSpan={N} style={sr({
                              color: (t.gross_pnl - t.entry_slip) >= 0 ? '#00e5a0' : '#ff4d6a',
                              borderBottom: tradeBorder,
                            })} title="gross_pnl − entry_slip">
                              {fmtSigned(t.gross_pnl - t.entry_slip)}
                            </td>
                            <td rowSpan={N} style={sr({ color: '#00e5a0', borderBottom: tradeBorder })}>
                              {fmtSigned(t.max_mtm)}
                              {t.max_mtm_time && <div style={{ color: '#7a9bb5', fontSize: 10 }}>{t.max_mtm_time}</div>}
                            </td>
                            <td rowSpan={N} style={sr({ color: t.max_pnl_net >= 0 ? '#00e5a0' : '#ff4d6a', borderBottom: tradeBorder })}>{fmtSigned(t.max_pnl_net)}</td>
                            <td rowSpan={N} style={sr({ color: '#ff4d6a', borderBottom: tradeBorder })}>
                              {fmtSigned(t.min_mtm)}
                              {t.min_mtm_time && <div style={{ color: '#7a9bb5', fontSize: 10 }}>{t.min_mtm_time}</div>}
                            </td>
                            <td rowSpan={N} style={sr({ color: t.min_pnl_net >= 0 ? '#00e5a0' : '#ff4d6a', borderBottom: tradeBorder })}>{fmtSigned(t.min_pnl_net)}</td>
                            <td rowSpan={N} style={sr({ borderBottom: tradeBorder })}>
                              {t.margin_used != null ? `$${t.margin_used.toFixed(0)}` : '—'}
                            </td>
                          </>
                        )}
                      </tr>
                    );
                  })}
                </React.Fragment>
              );
            })}
            {sorted.length === 0 && (
              <tr><td style={tdStyle} colSpan={40}>
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

// Baseline-banner cell: label + value with sign-aware coloring.
const BL: React.FC<{
  label: string;
  value: number | null;
  fmt: 'pct1' | 'pct3';
  tone: 'neutral' | 'signed';
  tip?: string;
}> = ({ label, value, fmt, tone, tip }) => {
  const text = (() => {
    if (value == null) return '—';
    if (fmt === 'pct1') return `${value.toFixed(1)}%`;
    // pct3: incoming value is decimal (0.0042 = 0.420%)
    const pct = value * 100;
    return `${tone === 'signed' && pct >= 0 ? '+' : ''}${pct.toFixed(3)}%`;
  })();
  const color =
    value == null ? '#7a9bb5'
    : tone === 'signed'
      ? (value >= 0 ? '#00e5a0' : '#ff4d6a')
      : '#c9d1d9';
  return (
    <span title={tip} style={{ display: 'inline-flex', gap: 4, alignItems: 'baseline' }}>
      <span style={{ color: '#7a9bb5' }}>{label}:</span>
      <strong style={{ color, fontVariantNumeric: 'tabular-nums' }}>{text}</strong>
    </span>
  );
};
