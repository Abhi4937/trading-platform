import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7PivotProfileCells, type M7Dataset, type M7LossesCell,
         type M7PivotByBand,
         type M7PivotProfileResponse,
         type M7TradeRecord } from '../../services/m7_api';
import type { M7ExitRule } from '../../types/m7';
import { usePersistedState } from '../../hooks/usePersistedState';
import { M7PivotProfileChart } from './M7PivotProfileChart';
import { M7PivotProfileTable } from './M7PivotProfileTable';
import { M7TradeDiagnosticModal } from './M7TradeDiagnosticModal';

interface Props {
  dataset: M7Dataset;
  cells: M7LossesCell[];
}

type View = 'all' | 'winners' | 'losers';

export function M7PivotProfilePanel({ dataset, cells }: Props) {
  const [data, setData] = useState<M7PivotProfileResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = usePersistedState<boolean>(
    'm7:pivotProfile:collapsed', false);
  const [view, setView] = usePersistedState<View>(
    'm7:pivotProfile:view', 'all');
  const [selectedTrade, setSelectedTrade] = useState<
    { tradeId: string; rule?: M7ExitRule } | null>(null);
  const [showAllLosers, setShowAllLosers] = useState<boolean>(false);

  // Derive stable serialized scope so React only re-runs when the actual
  // (band, hour, expiry, delta, rule) set changes.
  const scopedCells = useMemo(() => {
    return cells
      .filter(c =>
        c.entry_atm_iv_band && c.entry_hour_ist != null
        && c.expiry_bucket && c.delta_target != null)
      .map(c => ({
        entry_atm_iv_band: c.entry_atm_iv_band,
        entry_hour_ist: c.entry_hour_ist as number,
        expiry_bucket: c.expiry_bucket,
        delta_target: c.delta_target,
        rule: c.rule as Record<string, number> | undefined,
        lots: c.lots ?? null,
      }));
  }, [cells]);
  const cellsKey = useMemo(() => JSON.stringify(
    scopedCells.map(c => [
      c.entry_atm_iv_band, c.entry_hour_ist,
      c.expiry_bucket, c.delta_target,
      JSON.stringify(c.rule || {}),
      c.lots,
    ]).sort()), [scopedCells]);

  useEffect(() => {
    if (collapsed) return;
    setErr(null);
    if (scopedCells.length === 0) {
      setData(null);
      return;
    }
    let active = true;
    let timerId: number | null = null;
    const tick = () => {
      fetchM7PivotProfileCells(scopedCells, dataset)
        .then(resp => {
          if (!active) return;
          setData(resp);
          if (resp.status === 'warming') {
            timerId = window.setTimeout(tick, 2000);
          } else if (resp.status === 'error') {
            setErr(resp.error ?? 'unknown error');
          }
        })
        .catch(e => {
          if (active) setErr(String(e));
        });
    };
    tick();
    return () => {
      active = false;
      if (timerId != null) window.clearTimeout(timerId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cellsKey, dataset, collapsed]);

  const cellSummary = scopedCells.length === 0
    ? null
    : scopedCells.slice(0, 8).map(c => {
        const lotsTag = c.lots != null && c.lots > 0
          ? `·${c.lots}lots`
          : '';
        return `${c.entry_atm_iv_band}@${String(c.entry_hour_ist).padStart(2, '0')}IST/${c.expiry_bucket}/Δ${c.delta_target}${lotsTag}`;
      });

  const minN = data?.min_trades_per_band_cell ?? 5;
  const result = data?.status === 'ready' ? data.result : null;

  // Pick the visible by_band block based on the view toggle.
  const visibleByBand: M7PivotByBand = useMemo(() => {
    if (!result) return {};
    if (view === 'winners' && result.by_band_winners) return result.by_band_winners;
    if (view === 'losers' && result.by_band_losers) return result.by_band_losers;
    return result.by_band;
  }, [result, view]);

  const nVisible = useMemo(() => {
    let total = 0;
    for (const segs of Object.values(visibleByBand)) {
      const s = segs.Seg1;
      if (s?.n_trades) total = Math.max(total, s.n_trades);
    }
    return total;
  }, [visibleByBand]);

  // Find the cell rule for a given (band, hour) so the diagnostic modal can
  // replay the actual exit logic that produced the trade record.
  const ruleFor = (band: string, hour: number): M7ExitRule | undefined => {
    const c = scopedCells.find(
      x => x.entry_atm_iv_band === band && x.entry_hour_ist === hour);
    return c?.rule as M7ExitRule | undefined;
  };
  const openTrade = (r: M7TradeRecord) =>
    setSelectedTrade({ tradeId: r.trade_id,
                        rule: ruleFor(r.band, r.entry_hour_ist) });

  return (
    <section style={{ marginTop: 18, background: '#0d1421',
                       border: '1px solid #1a2d42', borderRadius: 6,
                       padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                     flexWrap: 'wrap', marginBottom: 10 }}>
        <button onClick={() => setCollapsed(!collapsed)}
                style={{ background: 'transparent', border: 'none',
                         color: '#cfd9e3', cursor: 'pointer',
                         fontSize: 14, fontWeight: 600, padding: 0 }}>
          {collapsed ? '▶' : '▼'} Pivot Profile by IV Band (5 IST segments)
        </button>
        <div style={{ flex: 1 }} />
        <ViewToggle view={view} setView={setView}
                     enabled={result != null && result.by_band_winners != null} />
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>
          Scoped to Best Combo per IV Band selection above ·{' '}
          <strong style={{ color: '#cfd9e3' }}>
            {scopedCells.length} cells
          </strong>
        </span>
      </div>
      {!collapsed && cellSummary && (
        <div style={{ fontSize: 10, color: '#586e7e', marginBottom: 8,
                       fontFamily: 'monospace' }}>
          {cellSummary.join(' · ')}
          {scopedCells.length > 8 && ` · … (+${scopedCells.length - 8} more)`}
        </div>
      )}
      {!collapsed && scopedCells.length > 0 && (
        <div style={{ fontSize: 10, color: '#586e7e', marginBottom: 8 }}>
          Values are <strong style={{ color: '#7a9bb5' }}>gross MTM</strong>{' '}
          (no exit slippage/brokerage) from the 1m path parquet, scaled by{' '}
          lots / 100 to match each band's Best-Combo lot count.
        </div>
      )}
      {collapsed && (
        <div style={{ color: '#586e7e', fontSize: 11 }}>
          Click ▶ above to expand.
        </div>
      )}
      {!collapsed && scopedCells.length === 0 && (
        <div style={{ color: '#7a9bb5', fontSize: 12, padding: 8 }}>
          Pick best-combo cells in the IV-Band Best Combo table above to scope
          this panel.
        </div>
      )}
      {!collapsed && err && (
        <div style={{ color: '#f85149', fontSize: 12, padding: 8 }}>
          Failed: {err}
        </div>
      )}
      {!collapsed && !err && data && data.status === 'warming' && (
        <div style={{ color: '#7a9bb5', fontSize: 12, padding: 8 }}>
          Building pivot profile…
          {data.progress != null && ` ${Math.round(data.progress * 100)}%`}
        </div>
      )}
      {!collapsed && !err && result && (
        <>
          <div style={{ fontSize: 11, color: '#586e7e', marginBottom: 8 }}>
            {view === 'all' && <>
              Aggregated over{' '}
              {result.params.n_after_filter.toLocaleString()}{' '}
              trades matching the {scopedCells.length} selected cells.
            </>}
            {view === 'winners' && <>
              <span style={{ color: '#3fb950' }}>Winners</span>{' '}
              ({(result.params.n_winners ?? 0).toLocaleString()} trades net&nbsp;P&amp;L&nbsp;&gt;&nbsp;$0
              ){' — '}same {scopedCells.length} cell scope.
            </>}
            {view === 'losers' && <>
              <span style={{ color: '#f85149' }}>Losers</span>{' '}
              ({(result.params.n_losers ?? 0).toLocaleString()} trades net&nbsp;P&amp;L&nbsp;≤&nbsp;$0
              ){' — '}same {scopedCells.length} cell scope.
            </>}
          </div>
          <SpotlightStrip
            bestWinner={result.best_winner}
            worstDrawdownWinner={result.winner_worst_drawdown}
            onOpen={openTrade} />
          <M7PivotProfileChart byBand={visibleByBand} minTrades={minN} />
          <M7PivotProfileTable byBand={visibleByBand} minN={minN} />
          {view === 'losers' && result.losers_list && (
            <LosersListTable
              rows={result.losers_list}
              showAll={showAllLosers}
              setShowAll={setShowAllLosers}
              onOpen={openTrade} />
          )}
        </>
      )}
      {selectedTrade && (
        <M7TradeDiagnosticModal
          tradeId={selectedTrade.tradeId}
          exitRule={selectedTrade.rule}
          onClose={() => setSelectedTrade(null)} />
      )}
    </section>
  );
}

function fmtUsd(v: number | null | undefined, sign = true): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const s = sign ? (v < 0 ? '-' : v > 0 ? '+' : '') : '';
  return `${s}$${Math.abs(v).toFixed(2)}`;
}

function SpotlightStrip({
  bestWinner, worstDrawdownWinner, onOpen,
}: {
  bestWinner: M7TradeRecord | null;
  worstDrawdownWinner: M7TradeRecord | null;
  onOpen: (r: M7TradeRecord) => void;
}) {
  if (!bestWinner && !worstDrawdownWinner) return null;
  return (
    <div style={{ display: 'grid', gap: 10, marginBottom: 10,
                   gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
      {bestWinner && (
        <SpotlightCard
          title="Best Winner"
          accent="#3fb950"
          headline={fmtUsd(bestWinner.net_pnl_usd)}
          headlineLabel="net P&L"
          record={bestWinner}
          onOpen={onOpen} />
      )}
      {worstDrawdownWinner && (
        <SpotlightCard
          title="Winner w/ Worst min-MTM"
          accent="#d29922"
          headline={fmtUsd(worstDrawdownWinner.min_mtm_usd)}
          headlineLabel="min MTM"
          subline={`net ${fmtUsd(worstDrawdownWinner.net_pnl_usd)}`}
          record={worstDrawdownWinner}
          onOpen={onOpen} />
      )}
    </div>
  );
}

function SpotlightCard({
  title, accent, headline, headlineLabel, subline, record, onOpen,
}: {
  title: string;
  accent: string;
  headline: string;
  headlineLabel: string;
  subline?: string;
  record: M7TradeRecord;
  onOpen: (r: M7TradeRecord) => void;
}) {
  const hhmm = String(record.entry_hour_ist).padStart(2, '0') + 'IST';
  return (
    <button
      onClick={() => onOpen(record)}
      style={{ textAlign: 'left', background: '#0a0e17',
               border: `1px solid ${accent}`, borderRadius: 6, padding: 10,
               cursor: 'pointer', color: '#cfd9e3' }}>
      <div style={{ fontSize: 10, color: '#7a9bb5', textTransform: 'uppercase',
                     letterSpacing: 0.4, marginBottom: 4 }}>
        {title}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 22, fontWeight: 700, color: accent,
                        fontFamily: 'monospace' }}>{headline}</span>
        <span style={{ fontSize: 10, color: '#7a9bb5' }}>{headlineLabel}</span>
        {subline && <span style={{ fontSize: 11, color: '#7a9bb5',
                                     marginLeft: 'auto' }}>{subline}</span>}
      </div>
      <div style={{ fontSize: 11, color: '#7a9bb5', marginTop: 4,
                     fontFamily: 'monospace' }}>
        {record.friday_date_ist || '—'} · {record.band} · {hhmm}
        {' · '}min {fmtUsd(record.min_mtm_usd, false)}
        {' · '}max {fmtUsd(record.max_mtm_usd, false)}
        {record.lots > 0 && ` · ${record.lots} lots`}
      </div>
    </button>
  );
}

const COMPACT_LIMIT = 25;

function LosersListTable({
  rows, showAll, setShowAll, onOpen,
}: {
  rows: M7TradeRecord[];
  showAll: boolean;
  setShowAll: (b: boolean) => void;
  onOpen: (r: M7TradeRecord) => void;
}) {
  if (rows.length === 0) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 11, marginTop: 10 }}>
        No losers in the current scope.
      </div>
    );
  }
  const shown = showAll ? rows : rows.slice(0, COMPACT_LIMIT);
  return (
    <div style={{ marginTop: 14, border: '1px solid #1a2d42',
                   borderRadius: 4 }}>
      <div style={{ padding: '6px 10px', borderBottom: '1px solid #1a2d42',
                     fontSize: 11, color: '#7a9bb5',
                     display: 'flex', justifyContent: 'space-between',
                     alignItems: 'center' }}>
        <span>Individual losers — {rows.length}
          {!showAll && rows.length > COMPACT_LIMIT
            && ` (showing worst ${COMPACT_LIMIT})`}
        </span>
        {rows.length > COMPACT_LIMIT && (
          <button
            onClick={() => setShowAll(!showAll)}
            style={{ background: 'transparent', border: '1px solid #1a2d42',
                     color: '#cfd9e3', cursor: 'pointer', fontSize: 11,
                     padding: '2px 8px', borderRadius: 3 }}>
            {showAll ? 'Show worst 25' : `Show all ${rows.length}`}
          </button>
        )}
      </div>
      <div style={{ overflowX: 'auto', maxHeight: 360, overflowY: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%',
                         fontSize: 11, fontFamily: 'monospace' }}>
          <thead style={{ position: 'sticky', top: 0, background: '#0d1421',
                           zIndex: 1 }}>
            <tr>
              <th style={lTH}>Friday</th>
              <th style={lTH}>Band</th>
              <th style={lTH}>Hour</th>
              <th style={{ ...lTH, textAlign: 'right' }}>Net P&amp;L</th>
              <th style={{ ...lTH, textAlign: 'right' }}>Min MTM</th>
              <th style={{ ...lTH, textAlign: 'right' }}>Max MTM</th>
              <th style={{ ...lTH, textAlign: 'right' }}>Lots</th>
            </tr>
          </thead>
          <tbody>
            {shown.map(r => (
              <tr key={r.trade_id}
                  onClick={() => onOpen(r)}
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#131c28')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                <td style={lTD}>{r.friday_date_ist || '—'}</td>
                <td style={lTD}>{r.band}</td>
                <td style={lTD}>
                  {String(r.entry_hour_ist).padStart(2, '0')}IST
                </td>
                <td style={{ ...lTD, textAlign: 'right', color: '#f85149' }}>
                  {fmtUsd(r.net_pnl_usd)}
                </td>
                <td style={{ ...lTD, textAlign: 'right' }}>
                  {fmtUsd(r.min_mtm_usd, false)}
                </td>
                <td style={{ ...lTD, textAlign: 'right' }}>
                  {fmtUsd(r.max_mtm_usd, false)}
                </td>
                <td style={{ ...lTD, textAlign: 'right' }}>{r.lots || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const lTH: React.CSSProperties = {
  padding: '6px 8px', textAlign: 'left',
  borderBottom: '1px solid #1a2d42', color: '#7a9bb5',
  fontWeight: 600, whiteSpace: 'nowrap',
};
const lTD: React.CSSProperties = {
  padding: '4px 8px', borderBottom: '1px solid #131c28',
  color: '#cfd9e3', whiteSpace: 'nowrap',
};

function ViewToggle({ view, setView, enabled }: {
  view: View; setView: (v: View) => void; enabled: boolean;
}) {
  const btn = (active: boolean, color: string): React.CSSProperties => ({
    padding: '4px 10px',
    background: active ? color : '#0d1421',
    color: active ? '#ffffff' : '#7a9bb5',
    border: `1px solid ${active ? color : '#1a2d42'}`,
    borderRadius: 4,
    cursor: enabled ? 'pointer' : 'not-allowed',
    fontSize: 11, fontWeight: 600,
    opacity: enabled ? 1 : 0.5,
  });
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
      <button style={btn(view === 'all', '#1f6feb')}
              disabled={!enabled}
              onClick={() => setView('all')}>
        All
      </button>
      <button style={btn(view === 'winners', '#1f8a47')}
              disabled={!enabled}
              onClick={() => setView('winners')}>
        Winners
      </button>
      <button style={btn(view === 'losers', '#a32621')}
              disabled={!enabled}
              onClick={() => setView('losers')}>
        Losers
      </button>
    </div>
  );
}
