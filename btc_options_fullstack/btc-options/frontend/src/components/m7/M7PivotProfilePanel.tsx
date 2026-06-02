import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7PivotProfileCells, type M7Dataset, type M7LossesCell,
         type M7PivotByBand,
         type M7LoserSegmentBlob,
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

type View = 'all' | 'winners' | 'losers'
          | 'best_win' | 'worst_dd_win' | 'losers_per_band';

export function M7PivotProfilePanel({ dataset, cells }: Props) {
  const [data, setData] = useState<M7PivotProfileResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = usePersistedState<boolean>(
    'm7:pivotProfile:collapsed', false);
  const [view, setView] = usePersistedState<View>(
    'm7:pivotProfile:view', 'all');
  const [selectedTrade, setSelectedTrade] = useState<
    { tradeId: string; rule?: M7ExitRule } | null>(null);

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
  // best_win / worst_dd_win render the existing aggregate chart over a
  // single trade per band (degenerate 1-trade aggregate). losers_per_band
  // uses a different layout altogether and doesn't go through this block.
  const visibleByBand: M7PivotByBand = useMemo(() => {
    if (!result) return {};
    if (view === 'winners' && result.by_band_winners) return result.by_band_winners;
    if (view === 'losers' && result.by_band_losers) return result.by_band_losers;
    if (view === 'best_win' && result.by_band_best_winner) return result.by_band_best_winner;
    if (view === 'worst_dd_win' && result.by_band_worst_drawdown_winner) return result.by_band_worst_drawdown_winner;
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
            {view === 'best_win' && <>
              <span style={{ color: '#3fb950' }}>Best Winner per Band</span>{' '}
              — each band's panel = the band's single best-net-PnL winner.
            </>}
            {view === 'worst_dd_win' && <>
              <span style={{ color: '#d29922' }}>Winner w/ Worst min-MTM per Band</span>{' '}
              — each band's panel = the band's deepest-drawdown winner.
            </>}
            {view === 'losers_per_band' && <>
              <span style={{ color: '#f85149' }}>All Losers per Band</span>{' '}
              — one mini-chart per losing trade, grouped by band.
            </>}
          </div>

          {/* Per-band Spotlight grids — visible across all chart views so
              the user always sees the band-level cards even while inspecting
              a different chart layout. */}
          <PerBandSpotlight
            title="Best Winner per Band"
            accent="#3fb950"
            metric="net"
            byBand={result.best_winners_by_band}
            onOpen={openTrade} />
          <PerBandSpotlight
            title="Winner w/ Worst min-MTM per Band"
            accent="#d29922"
            metric="min_mtm"
            byBand={result.worst_dd_winners_by_band}
            onOpen={openTrade} />

          {view === 'losers_per_band' ? (
            <LosersStackedMiniCharts
              byBand={result.by_band_losers_individual}
              minN={minN}
              onOpen={(tid, band, hour) => setSelectedTrade({
                tradeId: tid,
                rule: ruleFor(band, hour),
              })} />
          ) : (
            <>
              <M7PivotProfileChart byBand={visibleByBand} minTrades={minN} />
              <M7PivotProfileTable byBand={visibleByBand} minN={minN} />
            </>
          )}

          {view === 'losers' && result.losers_list_by_band && (
            <LosersListGrouped
              byBand={result.losers_list_by_band}
              onOpen={openTrade} />
          )}
        </>
      )}
      {selectedTrade && (
        <M7TradeDiagnosticModal
          tradeId={selectedTrade.tradeId}
          exitRule={selectedTrade.rule}
          dataset={dataset}
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
  const opts: { key: View; label: string; color: string }[] = [
    { key: 'all',             label: 'All',          color: '#1f6feb' },
    { key: 'winners',         label: 'Winners',      color: '#1f8a47' },
    { key: 'losers',          label: 'Losers',       color: '#a32621' },
    { key: 'best_win',        label: 'Best Win',     color: '#1f8a47' },
    { key: 'worst_dd_win',    label: 'Worst-DD Win', color: '#d29922' },
    { key: 'losers_per_band', label: 'All Lose',     color: '#a32621' },
  ];
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center',
                   flexWrap: 'wrap' }}>
      {opts.map(o => (
        <button key={o.key} style={btn(view === o.key, o.color)}
                disabled={!enabled}
                onClick={() => setView(o.key)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function PerBandSpotlight({
  title, accent, metric, byBand, onOpen,
}: {
  title: string;
  accent: string;
  metric: 'net' | 'min_mtm';
  byBand: Record<string, M7TradeRecord> | null | undefined;
  onOpen: (r: M7TradeRecord) => void;
}) {
  if (!byBand) return null;
  const bands = Object.keys(byBand).sort((a, b) =>
    (parseInt(a, 10) || 0) - (parseInt(b, 10) || 0));
  if (bands.length === 0) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 10, color: '#7a9bb5',
                     textTransform: 'uppercase', letterSpacing: 0.4,
                     marginBottom: 6, paddingLeft: 2 }}>
        {title}
      </div>
      <div style={{ display: 'grid', gap: 8,
                     gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))' }}>
        {bands.map(band => {
          const r = byBand[band];
          if (!r) return null;
          const headline = metric === 'net'
            ? fmtUsd(r.net_pnl_usd)
            : fmtUsd(r.min_mtm_usd);
          const headlineLabel = metric === 'net' ? 'net P&L' : 'min MTM';
          const subline = metric === 'min_mtm'
            ? `net ${fmtUsd(r.net_pnl_usd)}`
            : undefined;
          const hhmm = String(r.entry_hour_ist).padStart(2, '0') + 'IST';
          return (
            <button key={band}
                    onClick={() => onOpen(r)}
                    style={{ textAlign: 'left', background: '#0a0e17',
                             border: `1px solid ${accent}`, borderRadius: 6,
                             padding: 8, cursor: 'pointer',
                             color: '#cfd9e3' }}>
              <div style={{ fontSize: 10, color: '#7a9bb5',
                             fontFamily: 'monospace', marginBottom: 2 }}>
                Band {band}
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline',
                             gap: 6 }}>
                <span style={{ fontSize: 16, fontWeight: 700,
                                color: accent, fontFamily: 'monospace' }}>
                  {headline}
                </span>
                <span style={{ fontSize: 9, color: '#7a9bb5' }}>
                  {headlineLabel}
                </span>
                {subline && <span style={{ fontSize: 10, color: '#7a9bb5',
                                             marginLeft: 'auto' }}>
                  {subline}</span>}
              </div>
              <div style={{ fontSize: 10, color: '#7a9bb5', marginTop: 3,
                             fontFamily: 'monospace' }}>
                {r.friday_date_ist || '—'} · {hhmm}
                {' · '}min {fmtUsd(r.min_mtm_usd)}
                {' · '}max {fmtUsd(r.max_mtm_usd)}
                {r.lots > 0 && ` · ${r.lots} lots`}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function LosersListGrouped({
  byBand, onOpen,
}: {
  byBand: Record<string, M7TradeRecord[]>;
  onOpen: (r: M7TradeRecord) => void;
}) {
  const bands = Object.keys(byBand)
    .filter(b => (byBand[b] || []).length > 0)
    .sort((a, b) => (parseInt(a, 10) || 0) - (parseInt(b, 10) || 0));
  if (bands.length === 0) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 11, marginTop: 10 }}>
        No losers in the current scope.
      </div>
    );
  }
  return (
    <div style={{ marginTop: 14, border: '1px solid #1a2d42',
                   borderRadius: 4 }}>
      {bands.map((band, idx) => {
        const rows = byBand[band];
        return (
          <div key={band} style={{
            borderTop: idx === 0 ? 'none' : '1px solid #1a2d42' }}>
            <div style={{ padding: '6px 10px', background: '#0a0e17',
                           fontSize: 11, color: '#7a9bb5',
                           fontFamily: 'monospace' }}>
              Band {band} — {rows.length} loser{rows.length !== 1 ? 's' : ''}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%',
                               fontSize: 11, fontFamily: 'monospace' }}>
                <thead>
                  <tr>
                    <th style={lTH}>Friday</th>
                    <th style={lTH}>Hour</th>
                    <th style={{ ...lTH, textAlign: 'right' }}>Net P&amp;L</th>
                    <th style={{ ...lTH, textAlign: 'right' }}>Min MTM</th>
                    <th style={{ ...lTH, textAlign: 'right' }}>Max MTM</th>
                    <th style={{ ...lTH, textAlign: 'right' }}>Lots</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.trade_id}
                        onClick={() => onOpen(r)}
                        style={{ cursor: 'pointer' }}
                        onMouseEnter={e => (e.currentTarget.style.background = '#131c28')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                      <td style={lTD}>{r.friday_date_ist || '—'}</td>
                      <td style={lTD}>
                        {String(r.entry_hour_ist).padStart(2, '0')}IST
                      </td>
                      <td style={{ ...lTD, textAlign: 'right', color: '#f85149' }}>
                        {fmtUsd(r.net_pnl_usd)}
                      </td>
                      <td style={{ ...lTD, textAlign: 'right',
                                    color: (r.min_mtm_usd ?? 0) < 0
                                      ? '#f85149' : '#cfd9e3' }}>
                        {fmtUsd(r.min_mtm_usd)}
                      </td>
                      <td style={{ ...lTD, textAlign: 'right',
                                    color: (r.max_mtm_usd ?? 0) > 0
                                      ? '#3fb950' : '#cfd9e3' }}>
                        {fmtUsd(r.max_mtm_usd)}
                      </td>
                      <td style={{ ...lTD, textAlign: 'right' }}>
                        {r.lots || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function LosersStackedMiniCharts({
  byBand, minN, onOpen,
}: {
  byBand: Record<string, M7LoserSegmentBlob[]> | null | undefined;
  minN: number;
  onOpen: (tradeId: string, band: string, hour: number) => void;
}) {
  if (!byBand) return null;
  const bands = Object.keys(byBand)
    .filter(b => (byBand[b] || []).length > 0)
    .sort((a, b) => (parseInt(a, 10) || 0) - (parseInt(b, 10) || 0));
  if (bands.length === 0) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 12, padding: 12,
                     marginTop: 10, border: '1px solid #1a2d42',
                     borderRadius: 4 }}>
        No losers in any band for the current scope.
      </div>
    );
  }
  return (
    <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column',
                   gap: 14 }}>
      {bands.map(band => {
        const losers = byBand[band] || [];
        return (
          <div key={band} style={{ border: '1px solid #1a2d42',
                                     borderRadius: 4 }}>
            <div style={{ padding: '6px 10px', background: '#0a0e17',
                           fontSize: 12, color: '#cfd9e3',
                           fontFamily: 'monospace' }}>
              <strong style={{ color: '#f85149' }}>Band {band}</strong>{' '}
              — {losers.length} loser{losers.length !== 1 ? 's' : ''}
            </div>
            <div style={{ display: 'grid',
                           gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
                           gap: 6, padding: 6 }}>
              {losers.map(l => {
                // Build a band-shaped object so we can reuse M7PivotProfileChart
                // for the single-trade mini-chart.
                const singleBand: M7PivotByBand = {
                  [band]: l.segments,
                };
                return (
                  <button
                    key={l.trade_id}
                    onClick={() => {
                      // Hour is band-level; we don't carry it per-trade in
                      // the loser blob. Pass -1 → ruleFor returns undefined →
                      // diagnostic modal queries with the trade's natural exit.
                      onOpen(l.trade_id, band, -1);
                    }}
                    style={{ background: '#0a0e17', border: '1px solid #1a2d42',
                             borderRadius: 4, padding: 4, cursor: 'pointer',
                             textAlign: 'left' }}>
                    <div style={{ fontSize: 10, color: '#7a9bb5',
                                   fontFamily: 'monospace',
                                   padding: '2px 4px' }}>
                      {l.friday_date_ist || '—'} · net{' '}
                      <span style={{ color: '#f85149' }}>
                        {fmtUsd(l.net_pnl_usd)}
                      </span>
                      {' · '}min{' '}
                      <span style={{ color: (l.min_mtm_usd ?? 0) < 0
                                       ? '#f85149' : '#cfd9e3' }}>
                        {fmtUsd(l.min_mtm_usd)}
                      </span>
                      {' · '}max{' '}
                      <span style={{ color: (l.max_mtm_usd ?? 0) > 0
                                       ? '#3fb950' : '#cfd9e3' }}>
                        {fmtUsd(l.max_mtm_usd)}
                      </span>
                      {l.lots > 0 && ` · ${l.lots} lots`}
                    </div>
                    <div style={{ transform: 'scale(0.55)',
                                   transformOrigin: 'top left',
                                   width: '182%' }}>
                      <M7PivotProfileChart byBand={singleBand}
                                            minTrades={minN} />
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
