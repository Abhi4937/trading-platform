// M7 — Universe Losses Explorer (Chunk 6 + scope toggles).
// Mounted at the bottom of M7SweepDashboard. Surfaces:
//   - Two scope toggles: Full Coverage list / Best Combo per IV band
//     (mutually exclusive; off = universe). Best Combo has a credit/margin
//     sub-pill and an "exit rule overridden" banner.
//   - Headline strip (loss rate, avg/total/worst loser)
//   - Loss-cause distribution as horizontal bars
//   - By-band distribution as horizontal bars
//   - Faceted breakdown table (dimensions configurable)
//
// All data flows through /api/v1/m7/losses_distribution. Filters in the
// dashboard's filter bar always pass through; under scope=full_coverage they
// reshape the candidate pool which reshapes the best cells.

import React, { useEffect, useMemo, useState } from 'react';
import {
  fetchM7LossesDistribution,
  fetchM7FridayBandLossesDistribution,
  type M7Dataset,
  type M7LossesCell,
  type M7LossesDistResponse,
  type M7LossesRanking,
  type M7LossesScope,
  type M7LossesTradesSort,
} from '../../services/m7_api';
import type { M7ExitRule, M7Filters } from '../../types/m7';
import { InfoIcon } from './InfoIcon';
import { M7TradeDiagnosticModal } from './M7TradeDiagnosticModal';
import { dimLabels } from './dimLabels';

interface Props {
  filters: M7Filters;
  exitRule: M7ExitRule;
  metric?: string;  // dashboard's PnL-Analytics dropdown — drives FC best-cell pick
  useFridayBand?: boolean;
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
  // When `cells` is set, the explorer mirrors the dashboard's Best Combo
  // table 1:1 — the scope toggle row + credit/margin pills are hidden, and
  // losses are pulled from exactly these per-band selections. Used by
  // M7SweepDashboard.
  cells?: M7LossesCell[];
  // Joint Δ+Price-match dataset toggle. Default = today's pure-Δ behavior.
  dataset?: M7Dataset;
}

const CAUSE_COLORS: Record<string, string> = {
  directional:    '#f85149',
  vol_expansion:  '#d29922',
  path_dependent: '#bf6fde',
  gamma_squeeze:  '#ff7b72',
  skew_flip:      '#79c0ff',
  unclassified:   '#586e7e',
};

const DIMENSION_OPTIONS = [
  { v: 'loss_cause',          l: 'Loss cause' },
  { v: 'entry_atm_iv_band',   l: 'IV band' },
  { v: 'delta_target',        l: 'Δ target' },
  { v: 'expiry_bucket',       l: 'Expiry bucket' },
  { v: 'entry_hour_ist',      l: 'Entry hour' },
  { v: 'leg_winner',          l: 'Leg winner' },
];

function fmtUsd(v: number): string {
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000)     return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

export function M7LossesExplorer({ filters, exitRule, metric,
                                   useFridayBand = false, bandMode, d1Tiebreakers,
                                   cells, dataset }: Props) {
  const L = dimLabels(dataset);
  // "cells mode" — the dashboard passed an explicit per-band selection list.
  // When active, scope/ranking UI is hidden and the explorer mirrors those
  // cells exactly. Empty array still counts as cells-mode (means the table
  // hasn't loaded yet — show 0 trades rather than universe).
  const cellsMode = cells !== undefined;
  const [collapsed, setCollapsed] = useState(false);
  const [data, setData] = useState<M7LossesDistResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Calendar trades carry no loss_cause (the exit classifier is short-circuited),
  // so default the faceted breakdown to gap bucket × Δ instead of loss_cause × band.
  const [primaryDim, setPrimaryDim] = useState(dataset === 'calendar' ? 'entry_atm_iv_band' : 'loss_cause');
  const [secondaryDim, setSecondaryDim] = useState<string | ''>(dataset === 'calendar' ? 'delta_target' : 'entry_atm_iv_band');
  const [scope, setScope] = useState<M7LossesScope>(null);
  const [ranking, setRanking] = useState<M7LossesRanking>('credit');
  const [rulesExpanded, setRulesExpanded] = useState(false);
  // Individual-trades section state
  const [tradesExpanded, setTradesExpanded] = useState(false);
  const [tradesSort, setTradesSort] = useState<M7LossesTradesSort>('pnl_asc');
  const [onlySlHits, setOnlySlHits] = useState(false);
  const [tradesPage, setTradesPage] = useState(0);
  const TRADES_PER_PAGE = 50;
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  // Drives the cells-mode warming poll (see effect below).
  const [warmingTick, setWarmingTick] = useState(0);

  useEffect(() => {
    if (collapsed) return;
    const ac = new AbortController();
    setLoading(true); setErr(null);
    const dimensions = secondaryDim ? `${primaryDim},${secondaryDim}` : primaryDim;
    const fbScope = scope === 'full_coverage' ? null : scope;  // full_coverage not supported in FB mode
    const p = useFridayBand
      ? fetchM7FridayBandLossesDistribution({
          ...filters,
          dimensions,
          exit_rule: exitRule,
          scope: (fbScope === 'best_combo' ? 'best_combo' : null) as 'best_combo' | null,
          metric,
          include_trades: tradesExpanded,
          trades_limit: TRADES_PER_PAGE,
          trades_offset: tradesPage * TRADES_PER_PAGE,
          trades_sort: tradesSort,
          only_sl_hits: onlySlHits,
        }, bandMode, d1Tiebreakers, ac.signal, dataset)
      : fetchM7LossesDistribution(cellsMode
          ? {
              // cells-mode: ignore the scope/ranking choice entirely; the
              // dashboard's Best Combo selection is the source of truth.
              ...filters,
              dimensions,
              exit_rule: exitRule,
              cells: cells ?? [],
              include_trades: tradesExpanded,
              trades_limit: TRADES_PER_PAGE,
              trades_offset: tradesPage * TRADES_PER_PAGE,
              trades_sort: tradesSort,
              only_sl_hits: onlySlHits,
            }
          : {
              ...filters,
              dimensions,
              exit_rule: exitRule,
              scope: scope ?? undefined,
              ranking: scope === 'best_combo' ? ranking : undefined,
              metric,
              include_trades: tradesExpanded,
              trades_limit: TRADES_PER_PAGE,
              trades_offset: tradesPage * TRADES_PER_PAGE,
              trades_sort: tradesSort,
              only_sl_hits: onlySlHits,
            }, ac.signal, dataset);
    p.then(setData)
     .catch(e => { if ((e as Error).name !== 'AbortError') setErr(String(e)); })
     .finally(() => setLoading(false));
    return () => ac.abort();
  }, [collapsed, JSON.stringify(filters), JSON.stringify(exitRule),
      primaryDim, secondaryDim, scope, ranking, metric,
      tradesExpanded, tradesSort, onlySlHits, tradesPage,
      useFridayBand, bandMode, JSON.stringify(d1Tiebreakers ?? []),
      cellsMode, JSON.stringify(cells ?? []),
      dataset,
      // warmingTick re-fires the fetch on each poll while cells-mode is
      // warming (see the warming useEffect below). It's a no-op when not
      // warming because the response will come back warming=false and the
      // poll effect exits.
      warmingTick]);

  // Reset pagination when filters/scope/sort change.
  useEffect(() => { setTradesPage(0); }, [
    JSON.stringify(filters), JSON.stringify(exitRule),
    scope, ranking, tradesSort, onlySlHits,
  ]);

  // Auto-poll while the backend warms the cells-mode exit-rule cache.
  // The backend returns `scope_summary.warming=true` immediately when any
  // unique rule_dict in the cells payload is cold; daemon threads compute
  // each rule in parallel. Bumping `warmingTick` re-triggers the main fetch
  // effect every 3s until the response stops setting warming=true. This
  // keeps the rules_done/rules_total counter live without hammering the API
  // and survives backend restarts because each request is short.
  const isWarming = data?.scope_summary?.warming === true;
  useEffect(() => {
    if (!isWarming || collapsed) return;
    const id = window.setTimeout(() => setWarmingTick(t => t + 1), 3000);
    return () => window.clearTimeout(id);
  }, [isWarming, collapsed]);

  const scopeLabel = cellsMode
    ? (cells && cells.length > 0
        ? `Best Combo ${L.perBand} (${cells.length} cells)`
        : `Best Combo ${L.perBand} — waiting for table`)
    : scope === 'full_coverage'
      ? 'Full Coverage best cells'
      : scope === 'best_combo'
        ? `Best Combo (% ${ranking})`
        : 'Universe';

  const causeTotal = useMemo(() => {
    if (!data) return 0;
    return Object.values(data.by_cause).reduce((a, b) => a + b, 0);
  }, [data]);

  return (
    <div style={{
      marginTop: 24, background: '#0a0e17', border: '1px solid #1a2d42',
      borderRadius: 8, padding: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                    cursor: 'pointer' }}
           onClick={() => setCollapsed(c => !c)}>
        <span style={{ color: '#7a9bb5', fontSize: 11 }}>
          {collapsed ? '▶' : '▼'}
        </span>
        <div style={{ fontSize: 14, fontWeight: 700, color: '#cfd9e3' }}>
          Losses Explorer
        </div>
        <div style={{ marginLeft: 'auto', fontSize: 11, color: '#7a9bb5' }}>
          {data
            ? `${scopeLabel} — ${data.n_losses.toLocaleString()} losers / ${data.n_total.toLocaleString()} trades`
            : '…'}
        </div>
      </div>

      {!collapsed && (
        <>
          {/* Scope toggle row — hidden in cells-mode (M7SweepDashboard
              passes a `cells` array down, so there's no choice to make).
              Friday-Band and any other consumer without a `cells` prop
              still see the legacy toggles. */}
          {!cellsMode && (
          <div style={{
            display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8,
            marginTop: 10, paddingTop: 8, paddingBottom: 4,
            borderTop: '1px solid #1a2d42',
          }}
               onClick={(e) => e.stopPropagation()}>
            <span style={{ color: '#7a9bb5', fontSize: 11, marginRight: 4 }}>
              Scope:
            </span>
            <ScopeToggle
              label="Full Coverage list"
              active={scope === 'full_coverage'}
              onClick={() => setScope(s => s === 'full_coverage' ? null : 'full_coverage')}
              activeColor="#1f6feb"
            />
            <ScopeToggle
              label={`Best Combo ${L.perBand}`}
              active={scope === 'best_combo'}
              onClick={() => setScope(s => s === 'best_combo' ? null : 'best_combo')}
              activeColor="#bf6fde"
            />
            {scope === 'best_combo' && (
              <div style={{ display: 'flex', gap: 4, marginLeft: 4,
                            paddingLeft: 8, borderLeft: '1px solid #1a2d42' }}>
                <RankingPill label="% credit" active={ranking === 'credit'}
                             onClick={() => setRanking('credit')} />
                <RankingPill label="% margin" active={ranking === 'margin'}
                             onClick={() => setRanking('margin')} />
              </div>
            )}
            {scope && (
              <button onClick={() => setScope(null)}
                      style={{ marginLeft: 'auto', background: 'transparent',
                               color: '#7a9bb5', border: 'none', fontSize: 11,
                               cursor: 'pointer', padding: '2px 6px' }}
                      title="Clear scope (back to universe)">
                Clear ✕
              </button>
            )}
          </div>
          )}

          {/* Cells-mode info banner — replaces the override banner. Shows
              the user that the explorer is mirroring the dashboard's
              Best Combo table and that exit rules are per-band. */}
          {cellsMode && cells && cells.length > 0 && (
            <div style={{
              marginTop: 8, padding: '6px 10px',
              background: '#0d1f33', border: '1px solid #1a3d5e',
              borderRadius: 4, fontSize: 11, color: '#79c0ff',
            }} onClick={(e) => e.stopPropagation()}>
              ⛓ Mirroring Best Combo {L.perBand} — {cells.length} cells, each
              band's own exit rule. Change any control on the table above
              and the losses recompute.
            </div>
          )}

          {/* Override banner — only when scope=best_combo. */}
          {scope === 'best_combo' && data?.scope_summary?.exit_rule_overridden && (
            <div style={{
              marginTop: 8, padding: '6px 10px',
              background: '#3d2e0f', border: '1px solid #6b5108',
              borderRadius: 4, fontSize: 11, color: '#e8c25b',
            }}
                 onClick={(e) => e.stopPropagation()}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>⚠ Exit rule overridden — analysis uses each band's own best rule.</span>
                <button onClick={() => setRulesExpanded(x => !x)}
                        style={{ background: 'transparent', color: '#e8c25b',
                                 border: 'none', fontSize: 11, cursor: 'pointer',
                                 textDecoration: 'underline' }}>
                  {rulesExpanded ? 'Hide rules ▴' : 'View per-band rules ▾'}
                </button>
              </div>
              {rulesExpanded && data.scope_summary.per_band_rules.length > 0 && (
                <table style={{ marginTop: 6, fontSize: 10, color: '#cfd9e3',
                                fontFamily: 'ui-monospace, monospace',
                                borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: '#7a9bb5' }}>
                      <th style={{ textAlign: 'left', padding: '2px 8px' }}>Band <InfoIcon text="ATM IV bucket." /></th>
                      <th style={{ textAlign: 'left', padding: '2px 8px' }}>Expiry <InfoIcon text="Expiry bucket of the picked combo." /></th>
                      <th style={{ textAlign: 'right', padding: '2px 8px' }}>Δ <InfoIcon text="Picked combo's per-leg delta target." /></th>
                      <th style={{ textAlign: 'left', padding: '2px 8px' }}>Rule <InfoIcon text="Exit rule of the picked combo for this band (e.g. sl75_max_profit_25)." /></th>
                      <th style={{ textAlign: 'right', padding: '2px 8px' }}>n <InfoIcon text="Number of Friday trades that hit this rule on this band." /></th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.scope_summary.per_band_rules.map((r, i) => (
                      <tr key={i}>
                        <td style={{ padding: '2px 8px' }}>{r.band ?? '—'}</td>
                        <td style={{ padding: '2px 8px' }}>{r.expiry_bucket ?? '—'}</td>
                        <td style={{ padding: '2px 8px', textAlign: 'right' }}>
                          {r.delta_target != null ? r.delta_target.toFixed(2) : '—'}
                        </td>
                        <td style={{ padding: '2px 8px' }}>{r.rule_label ?? '—'}</td>
                        <td style={{ padding: '2px 8px', textAlign: 'right' }}>{r.n_trades}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* Warming notice — fires for either scope=best_combo (grid build)
              or cells-mode (per-rule _derive_exits cache fill). In cells-mode
              the warming response includes rules_done/rules_total counters
              that update every 3s via the auto-poll effect. */}
          {data?.scope_summary?.warming && (scope === 'best_combo' || cellsMode) && (
            <div style={{
              marginTop: 8, padding: '6px 10px', fontSize: 11, color: '#7a9bb5',
              background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 4,
            }}>
              {cellsMode
                ? <>⏳ Warming the per-rule exit cache…{' '}
                    {data.scope_summary.rules_done ?? 0}
                    {' / '}{data.scope_summary.rules_total ?? cells?.length ?? 0}
                    {' rules ready. Auto-refreshing every 3s.'}</>
                : <>Best Combo grid warming up… {data.scope_summary.rules_done ?? 0}
                    {' / '}{data.scope_summary.rules_total ?? 96} rules processed.
                    Try again in a few seconds.</>}
            </div>
          )}

          {loading && <div style={{ color: '#7a9bb5', fontSize: 11, padding: 8 }}>Loading…</div>}
          {err && <div style={{ color: '#f85149', fontSize: 11, padding: 8 }}>{err}</div>}
          {data && (
            <>
              {/* Headline strip */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                gap: 8, marginTop: 12,
              }}>
                <KPI label="Loss rate"
                     value={`${(data.loss_rate * 100).toFixed(1)}%`}
                     color="#cfd9e3"
                     info="n_losses ÷ n_trades across the current scope (rule-matched, force-fit, or all trades depending on the scope toggle)." />
                <KPI label="Avg loser"
                     value={fmtUsd(data.avg_loss_usd)}
                     color="#f85149"
                     info="Mean net P&L across losing trades only (negative)." />
                <KPI label="Total loss"
                     value={fmtUsd(data.total_loss_usd)}
                     color="#f85149"
                     info="Sum of net P&L across all losing trades in scope." />
                <KPI label="Worst single loss"
                     value={fmtUsd(data.worst_loss_usd)}
                     color="#f85149"
                     info="Most negative net P&L of any single trade in scope." />
              </div>

              {/* By-cause distribution bars */}
              <div style={{ marginTop: 14 }}>
                <div style={{ fontSize: 11, color: '#7a9bb5', marginBottom: 4 }}>
                  Losses by cause (n)
                </div>
                {Object.entries(data.by_cause)
                  .sort((a, b) => b[1] - a[1])
                  .map(([cause, n]) => {
                    const pct = causeTotal > 0 ? n / causeTotal : 0;
                    return (
                      <div key={cause} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        marginBottom: 3, fontSize: 11,
                      }}>
                        <span style={{ width: 130, color: '#cfd9e3' }}>{cause}</span>
                        <div style={{ flex: 1, background: '#0d1421',
                                      border: '1px solid #1a2d42', borderRadius: 3,
                                      height: 16, position: 'relative', overflow: 'hidden' }}>
                          <div style={{
                            background: CAUSE_COLORS[cause] || '#7a9bb5',
                            height: '100%', width: `${pct * 100}%`,
                          }} />
                        </div>
                        <span style={{ width: 80, textAlign: 'right',
                                       color: '#7a9bb5', fontFamily: 'ui-monospace, monospace' }}>
                          {n.toLocaleString()} ({(pct * 100).toFixed(1)}%)
                        </span>
                      </div>
                    );
                  })}
              </div>

              {/* By-band distribution bars */}
              <div style={{ marginTop: 14 }}>
                <div style={{ fontSize: 11, color: '#7a9bb5', marginBottom: 4 }}>
                  Losses {L.perBand} (n)
                </div>
                {Object.entries(data.by_band)
                  .sort((a, b) => {
                    const score = (k: string): number => {
                      if (k === '100+') return 999;
                      const m = k.match(/^(\d+)/);
                      return m ? parseInt(m[1], 10) : 0;
                    };
                    return score(a[0]) - score(b[0]);
                  })
                  .map(([band, n]) => {
                    const max = Math.max(...Object.values(data.by_band));
                    const pct = max > 0 ? n / max : 0;
                    return (
                      <div key={band} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        marginBottom: 3, fontSize: 11,
                      }}>
                        <span style={{ width: 130, color: '#cfd9e3' }}>{band}</span>
                        <div style={{ flex: 1, background: '#0d1421',
                                      border: '1px solid #1a2d42', borderRadius: 3,
                                      height: 14, position: 'relative', overflow: 'hidden' }}>
                          <div style={{ background: '#1f6feb',
                                        height: '100%', width: `${pct * 100}%` }} />
                        </div>
                        <span style={{ width: 60, textAlign: 'right',
                                       color: '#7a9bb5', fontFamily: 'ui-monospace, monospace' }}>
                          {n.toLocaleString()}
                        </span>
                      </div>
                    );
                  })}
              </div>

              {/* Per-IV-band loss stats — same shape as the Full Coverage
                  table's loss columns, scoped to the currently-active scope. */}
              {data.by_band_stats && data.by_band_stats.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 11, color: '#7a9bb5', marginBottom: 4 }}>
                    Loss stats {L.perBand} (scoped to {scopeLabel.toLowerCase()})
                  </div>
                  <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: 11,
                                    color: '#cfd9e3', minWidth: 1100,
                                    fontFamily: 'ui-monospace, monospace' }}>
                      <thead style={{ background: '#0d1421' }}>
                        <tr style={{ color: '#7a9bb5' }}>
                          <th style={{ padding: 4, textAlign: 'left', borderBottom: '1px solid #1a2d42' }}>IV band <InfoIcon text="ATM IV bucket at entry hour." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>n <InfoIcon text="Number of trades in this band." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>n loss <InfoIcon text="Number of losing trades (net P&L ≤ 0)." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>SL hits <InfoIcon text="Trades exited because any rule fired (premium_sl OR max_profit OR margin_target). For take-profit families this includes profit-take fires." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Hard cap <InfoIcon text="Trades that ran to Sat 17:30 IST (settlement) without any rule firing." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Avg loss <InfoIcon text="Mean net P&L across losers (negative number)." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Total loss <InfoIcon text="Sum of net P&L across losers." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Largest loss <InfoIcon text="Most negative net P&L of any single loser." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Avg exit MTM (L) <InfoIcon text="Mean exit-time MTM across losers (entry slippage only)." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Total exit MTM (L) <InfoIcon text="Sum of exit-time MTM across all losers." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Largest exit MTM (L) <InfoIcon text="Min exit-time MTM among losers." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Avg max MTM (L) <InfoIcon text="Mean peak MTM across losers — how high they went before turning losing." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Avg min MTM (L) <InfoIcon text="Mean trough MTM across losers." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Max MTM (L) <InfoIcon text="Highest peak MTM observed across all losers." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>Min MTM (L) <InfoIcon text="Worst trough MTM across all losers." /></th>
                          <th style={{ padding: 4, textAlign: 'right', borderBottom: '1px solid #1a2d42' }}>L &gt; avg max MTM <InfoIcon text="Losers whose max MTM rose above the band's avg-max-MTM (losers) — missed exit opportunity." /></th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.by_band_stats.map((b, i) => {
                          const usd = (v: number | null) => v == null ? '—' : fmtUsd(v);
                          const num = (v: number | null | undefined) =>
                            v == null ? '—' : v.toString();
                          const lossColor = '#f85149';
                          return (
                            <tr key={i} style={{ borderBottom: '1px solid #0d1421' }}>
                              <td style={{ padding: 4, color: '#cfd9e3', fontWeight: 600 }}>
                                {b.entry_atm_iv_band ?? '—'}
                              </td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{b.n_band_total}</td>
                              <td style={{ padding: 4, textAlign: 'right', color: lossColor }}>{b.n_loss}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{num(b.n_rule_trigger)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{num(b.n_hard_cap)}</td>
                              <td style={{ padding: 4, textAlign: 'right', color: lossColor }}>{usd(b.avg_loss_usd)}</td>
                              <td style={{ padding: 4, textAlign: 'right', color: lossColor }}>{usd(b.total_loss_usd)}</td>
                              <td style={{ padding: 4, textAlign: 'right', color: lossColor }}>{usd(b.largest_loss_usd)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{usd(b.avg_loss_mtm)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{usd(b.total_loss_mtm)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{usd(b.largest_loss_mtm)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{usd(b.avg_max_mtm_losers)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{usd(b.avg_min_mtm_losers)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{usd(b.max_mtm_losers)}</td>
                              <td style={{ padding: 4, textAlign: 'right' }}>{usd(b.min_mtm_losers)}</td>
                              <td style={{ padding: 4, textAlign: 'right', color: '#7a9bb5' }}>
                                {num(b.n_losers_above_avg_max_mtm)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div style={{ fontSize: 9, color: '#5b7894', marginTop: 4,
                                fontStyle: 'italic' }}>
                    "Loss MTM" cols use exit MTM (entry slip only); USD cols use net P&amp;L
                    (after all costs). MTM(L) cols are path extremes during losing trades.
                  </div>
                </div>
              )}

              {/* Individual losing trades — drill down into one trade.
                  Lazy: rows only fetch when section is expanded. Click a
                  row to open M7TradeDiagnosticModal with all sections. */}
              <div style={{ marginTop: 14, borderTop: '1px solid #1a2d42',
                            paddingTop: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                              cursor: 'pointer', fontSize: 12, color: '#cfd9e3',
                              fontWeight: 600 }}
                     onClick={() => setTradesExpanded(x => !x)}>
                  <span style={{ color: '#7a9bb5', fontSize: 11 }}>
                    {tradesExpanded ? '▼' : '▶'}
                  </span>
                  Individual losing trades (in scope)
                  {data.scope_summary && (
                    <span style={{ color: '#7a9bb5', fontWeight: 400,
                                   fontSize: 11 }}>
                      — {data.n_losses.toLocaleString()} losers
                    </span>
                  )}
                </div>

                {tradesExpanded && (
                  <>
                    {/* Controls */}
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                                  marginTop: 8, fontSize: 11, color: '#7a9bb5',
                                  flexWrap: 'wrap' }}>
                      <span>Sort:</span>
                      <select value={tradesSort}
                              onChange={e => setTradesSort(e.target.value as M7LossesTradesSort)}
                              style={selectStyle}>
                        <option value="pnl_asc">Net P&amp;L (worst first)</option>
                        <option value="pnl_desc">Net P&amp;L (best first)</option>
                        <option value="friday_asc">Friday (oldest)</option>
                        <option value="friday_desc">Friday (newest)</option>
                        <option value="band">{L.band}</option>
                      </select>
                      <label style={{ display: 'flex', alignItems: 'center',
                                      gap: 4, cursor: 'pointer' }}>
                        <input type="checkbox" checked={onlySlHits}
                               onChange={e => setOnlySlHits(e.target.checked)} />
                        Only SL hits (rule_trigger)
                      </label>
                      {data.losers_sample_total != null && (
                        <span style={{ marginLeft: 'auto' }}>
                          Showing {(tradesPage * TRADES_PER_PAGE) + 1}–
                          {Math.min((tradesPage + 1) * TRADES_PER_PAGE,
                                    data.losers_sample_total)}
                          {' of '}{data.losers_sample_total.toLocaleString()}
                        </span>
                      )}
                    </div>

                    {/* Table */}
                    {data.losers_sample && data.losers_sample.length > 0 ? (
                      <div style={{ overflowX: 'auto', marginTop: 8 }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse',
                                        fontSize: 11, color: '#cfd9e3' }}>
                          <thead style={{ background: '#0d1421' }}>
                            <tr style={{ color: '#7a9bb5' }}>
                              <th style={th}>Friday <InfoIcon text="Friday date (IST) of the trade." /></th>
                              <th style={th}>Band <InfoIcon text="ATM IV bucket at entry hour." /></th>
                              <th style={th}>Hour <InfoIcon text="Hour-of-day IST the trade opened." /></th>
                              <th style={th}>Expiry <InfoIcon text="Expiry bucket (current/next/next_to_next/weekly/biweekly/monthly)." /></th>
                              <th style={{...th, textAlign: 'right'}}>Δ <InfoIcon text="Per-leg target delta at entry." /></th>
                              <th style={th}>Exit <InfoIcon text="How the trade exited: rule_trigger (premium_sl/max_profit/margin_target fired), fixed_hour_ist (timed exit), or hard_cap (Sat 17:30 settlement)." /></th>
                              <th style={th}>Cause <InfoIcon text="Auto-classified loss cause: directional (large spot move), vol_expansion (IV jumped at the worst-MTM bar), gamma_squeeze (early SL hit + net delta blew past entry), skew_flip (both legs lost + directional balance inverted), path_dependent (gave back >30% peak), or unclassified." /></th>
                              <th style={{...th, textAlign: 'right'}}>Net P&amp;L <InfoIcon text="Realised net P&L (all four cost components subtracted)." /></th>
                              <th style={{...th, textAlign: 'right'}}>Largest swing <InfoIcon text="Max(peak MTM) − min(trough MTM) during the trade's hold — how volatile the P&L path was before settling at the exit number." /></th>
                              <th style={th}></th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.losers_sample.map((t, i) => (
                              <tr key={t.trade_id ?? i}
                                  style={{ borderBottom: '1px solid #0d1421',
                                           cursor: 'pointer' }}
                                  onClick={() => t.trade_id && setSelectedTradeId(t.trade_id)}
                                  onMouseEnter={e => (e.currentTarget.style.background = '#0d1421')}
                                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                <td style={td}>{t.friday_date_ist ?? '—'}</td>
                                <td style={td}>{t.entry_atm_iv_band ?? '—'}</td>
                                <td style={td}>{t.entry_hour_ist != null ? `${t.entry_hour_ist}:00` : '—'}</td>
                                <td style={td}>{t.expiry_bucket ?? '—'}</td>
                                <td style={{...td, textAlign: 'right'}}>
                                  {t.delta_target != null ? t.delta_target.toFixed(2) : '—'}
                                </td>
                                <td style={td}>
                                  <span style={{
                                    fontSize: 10, padding: '1px 6px', borderRadius: 8,
                                    background: t.exit_reason === 'rule_trigger' ? '#3d2e0f' : '#0d1421',
                                    color: t.exit_reason === 'rule_trigger' ? '#e8c25b' : '#cfd9e3',
                                    border: '1px solid #1a2d42',
                                  }}>
                                    {t.exit_reason ?? '—'}
                                  </span>
                                </td>
                                <td style={td}>
                                  <span style={{ fontSize: 10, color: '#7a9bb5' }}>
                                    {t.loss_cause ?? '—'}
                                  </span>
                                </td>
                                <td style={{...td, textAlign: 'right',
                                            color: '#f85149',
                                            fontFamily: 'ui-monospace, monospace'}}>
                                  {fmtUsd(Number(t.net_pnl_estimate_usd))}
                                </td>
                                <td style={{...td, textAlign: 'right', color: '#7a9bb5',
                                            fontFamily: 'ui-monospace, monospace'}}>
                                  {fmtUsd(Number(t.largest_swing_usd))}
                                </td>
                                <td style={{...td, textAlign: 'right'}}>
                                  <span style={{ color: '#1f6feb', fontSize: 11 }}>
                                    Diagnose ▸
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div style={{ color: '#7a9bb5', fontSize: 11, padding: 12,
                                    textAlign: 'center' }}>
                        No losing trades in scope.
                      </div>
                    )}

                    {/* Pagination */}
                    {data.losers_sample_total != null
                      && data.losers_sample_total > TRADES_PER_PAGE && (
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center',
                                    marginTop: 6, fontSize: 11 }}>
                        <button disabled={tradesPage === 0}
                                onClick={() => setTradesPage(p => Math.max(0, p - 1))}
                                style={pagBtn(tradesPage === 0)}>
                          ← Prev
                        </button>
                        <span style={{ color: '#7a9bb5' }}>
                          Page {tradesPage + 1} / {Math.ceil(data.losers_sample_total / TRADES_PER_PAGE)}
                        </span>
                        <button disabled={(tradesPage + 1) * TRADES_PER_PAGE >= data.losers_sample_total}
                                onClick={() => setTradesPage(p => p + 1)}
                                style={pagBtn((tradesPage + 1) * TRADES_PER_PAGE >= data.losers_sample_total)}>
                          Next →
                        </button>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Faceted breakdown table */}
              <div style={{ marginTop: 14 }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                              marginBottom: 6, fontSize: 11, color: '#7a9bb5' }}>
                  <span>Faceted breakdown:</span>
                  <select value={primaryDim}
                          onChange={e => setPrimaryDim(e.target.value)}
                          style={selectStyle}>
                    {DIMENSION_OPTIONS.map(o => (
                      <option key={o.v} value={o.v}>{L.isCal && o.v === 'entry_atm_iv_band' ? L.band : L.isCal && o.v === 'expiry_bucket' ? L.expiry : o.l}</option>
                    ))}
                  </select>
                  <span>×</span>
                  <select value={secondaryDim}
                          onChange={e => setSecondaryDim(e.target.value)}
                          style={selectStyle}>
                    <option value="">(none)</option>
                    {DIMENSION_OPTIONS.filter(o => o.v !== primaryDim).map(o => (
                      <option key={o.v} value={o.v}>{L.isCal && o.v === 'entry_atm_iv_band' ? L.band : L.isCal && o.v === 'expiry_bucket' ? L.expiry : o.l}</option>
                    ))}
                  </select>
                </div>
                {data.rows.length > 0 ? (
                  <div style={{ maxHeight: 400, overflowY: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse',
                                    fontSize: 11, color: '#cfd9e3' }}>
                      <thead style={{ position: 'sticky', top: 0, background: '#0a0e17',
                                      zIndex: 1 }}>
                        <tr style={{ borderBottom: '1px solid #1a2d42', color: '#7a9bb5' }}>
                          <th style={{ padding: 4, textAlign: 'left' }}>{primaryDim}</th>
                          {secondaryDim && <th style={{ padding: 4, textAlign: 'left' }}>{secondaryDim}</th>}
                          <th style={{ padding: 4, textAlign: 'right' }}>n</th>
                          <th style={{ padding: 4, textAlign: 'right' }}>Avg loss</th>
                          <th style={{ padding: 4, textAlign: 'right' }}>Total loss</th>
                          <th style={{ padding: 4, textAlign: 'right' }}>Share</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.rows.slice(0, 100).map((r, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid #0a0e17' }}>
                            <td style={{ padding: 4 }}>{String(r[primaryDim] ?? '—')}</td>
                            {secondaryDim && (
                              <td style={{ padding: 4 }}>{String(r[secondaryDim] ?? '—')}</td>
                            )}
                            <td style={{ padding: 4, textAlign: 'right',
                                         fontFamily: 'ui-monospace, monospace' }}>
                              {Number(r.n).toLocaleString()}
                            </td>
                            <td style={{ padding: 4, textAlign: 'right',
                                         color: '#f85149',
                                         fontFamily: 'ui-monospace, monospace' }}>
                              {fmtUsd(Number(r.avg_loss_usd))}
                            </td>
                            <td style={{ padding: 4, textAlign: 'right',
                                         color: '#f85149',
                                         fontFamily: 'ui-monospace, monospace' }}>
                              {fmtUsd(Number(r.total_loss_usd))}
                            </td>
                            <td style={{ padding: 4, textAlign: 'right',
                                         color: '#7a9bb5',
                                         fontFamily: 'ui-monospace, monospace' }}>
                              {(Number(r.share) * 100).toFixed(2)}%
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div style={{ color: '#7a9bb5', fontSize: 11, padding: 12,
                                textAlign: 'center' }}>
                    No data for selected dimensions.
                  </div>
                )}
              </div>
            </>
          )}
        </>
      )}

      {/* Single-trade diagnostic — opens when a row is clicked. */}
      {selectedTradeId && (
        <M7TradeDiagnosticModal
          tradeId={selectedTradeId}
          exitRule={exitRule}
          dataset={dataset}
          onClose={() => setSelectedTradeId(null)}
        />
      )}
    </div>
  );
}

const th: React.CSSProperties = {
  padding: 4, textAlign: 'left',
  borderBottom: '1px solid #1a2d42',
};
const td: React.CSSProperties = { padding: 4 };
const pagBtn = (disabled: boolean): React.CSSProperties => ({
  padding: '3px 8px', fontSize: 10,
  background: '#0d1421', color: disabled ? '#5b7894' : '#cfd9e3',
  border: '1px solid #1a2d42', borderRadius: 3,
  cursor: disabled ? 'not-allowed' : 'pointer',
});

function KPI({ label, value, color, info }: { label: string; value: string; color: string; info?: string }) {
  return (
    <div style={{
      padding: '8px 12px', background: '#0d1421', border: '1px solid #1a2d42',
      borderRadius: 4,
    }}>
      <div style={{ fontSize: 9, color: '#7a9bb5',
                    textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {label}{info && <InfoIcon text={info} />}
      </div>
      <div style={{ fontSize: 16, color, fontWeight: 600, marginTop: 4,
                    fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  );
}

function ScopeToggle({ label, active, onClick, activeColor }: {
  label: string; active: boolean; onClick: () => void; activeColor: string;
}) {
  return (
    <button onClick={onClick}
            style={{
              padding: '4px 10px', fontSize: 11,
              background: active ? activeColor : '#0d1421',
              color: active ? '#0a0e17' : '#cfd9e3',
              border: `1px solid ${active ? activeColor : '#1a2d42'}`,
              borderRadius: 4, cursor: 'pointer',
              fontWeight: active ? 700 : 400,
            }}>
      {label}
    </button>
  );
}

function RankingPill({ label, active, onClick }: {
  label: string; active: boolean; onClick: () => void;
}) {
  return (
    <button onClick={onClick}
            style={{
              padding: '3px 8px', fontSize: 10,
              background: active ? '#1f6feb' : '#0d1421',
              color: active ? '#0a0e17' : '#7a9bb5',
              border: `1px solid ${active ? '#1f6feb' : '#1a2d42'}`,
              borderRadius: 3, cursor: 'pointer',
              fontWeight: active ? 700 : 400,
            }}>
      {label}
    </button>
  );
}

const selectStyle: React.CSSProperties = {
  background: '#0d1421', color: '#cfd9e3', border: '1px solid #1a2d42',
  borderRadius: 3, padding: '2px 6px', fontSize: 11,
};
