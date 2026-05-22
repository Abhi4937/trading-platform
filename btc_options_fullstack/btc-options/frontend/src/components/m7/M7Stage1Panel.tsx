/**
 * Stage-1 Partial Exit Sweep panel.
 *
 * Mounted in M7SweepDashboard below M7PivotProfilePanel.
 * Collapsed by default; user clicks "Compute" to trigger the POST.
 * Polls every 2s while status=warming; renders verdict + per-band cards on ready.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchM7Stage1,
  fetchM7Stage1AllTrades,
  fetchM7Stage1BandTrades,
  fetchM7Stage1Precheck,
  type M7Dataset,
  type Stage1AllTradeRow,
  type Stage1AllTradesResponse,
  type Stage1BandSummary,
  type Stage1BandTradesResponse,
  type Stage1BestCell,
  type Stage1Cell,
  type Stage1PerBandRule,
  type Stage1PrecheckResponse,
  type Stage1Response,
  type Stage1Result,
  type Stage1TradeRow,
} from '../../services/m7_api';
import { usePersistedState } from '../../hooks/usePersistedState';

interface Props {
  resolvedRules: Record<string, Stage1PerBandRule>;
  dataset: M7Dataset;
}

// ─── Formatters ───────────────────────────────────────────────────────────────

function fmt$(v: number | null | undefined, decimals = 0): string {
  if (v == null || isNaN(v)) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(decimals);
}
function fmtAbs$(v: number | null | undefined, decimals = 0): string {
  if (v == null || isNaN(v)) return '—';
  return '$' + v.toFixed(decimals);
}
function fmtPct(v: number | null | undefined, decimals = 1): string {
  if (v == null || isNaN(v)) return '—';
  return (v * 100).toFixed(decimals) + '%';
}
function fmtN(v: number | null | undefined): string {
  if (v == null) return '—';
  return String(v);
}

// ─── Verdict chip ─────────────────────────────────────────────────────────────

type VerdictLabel = 'WORTH_IT' | 'SKIP_TIGHTER_SL_WINS' | 'SKIP_NEGATIVE' | 'SKIP_INSUFFICIENT' | 'MARGINAL' | string;

function VerdictChip({ verdict }: { verdict: VerdictLabel }) {
  const config: Record<string, { label: string; bg: string; color: string }> = {
    WORTH_IT:             { label: 'Worth it',        bg: '#1a4a2a', color: '#4ade80' },
    MARGINAL:             { label: 'Marginal',         bg: '#1e2d3d', color: '#7a9bb5' },
    SKIP_TIGHTER_SL_WINS: { label: 'Tighter SL wins', bg: '#1a2d4a', color: '#60a5fa' },
    SKIP_NEGATIVE:        { label: 'Stage-1 hurts',   bg: '#4a1a1a', color: '#f87171' },
    SKIP_INSUFFICIENT:    { label: 'Insufficient data',bg: '#252525', color: '#777' },
  };
  const c = config[verdict] ?? { label: verdict, bg: '#1e2d3d', color: '#7a9bb5' };
  return (
    <span style={{
      background: c.bg, color: c.color, border: `1px solid ${c.color}55`,
      borderRadius: 3, fontSize: 10, fontWeight: 700,
      padding: '1px 6px', letterSpacing: 0.3,
    }}>
      {c.label}
    </span>
  );
}

// ─── C_recovered warning chip ─────────────────────────────────────────────────

function CRecoveredChip({ level, share }: { level: 'none' | 'yellow' | 'red'; share: number | null }) {
  if (level === 'none' || share == null) return null;
  const isRed = level === 'red';
  return (
    <span
      title={`Stage-1 fires on ${(share * 100).toFixed(0)}% of loser trades that would have partially recovered — consider tighter trigger or different exit_frac`}
      style={{
        background: isRed ? '#4a1a1a' : '#3a2e10',
        color: isRed ? '#f87171' : '#fbbf24',
        border: `1px solid ${isRed ? '#f8717155' : '#fbbf2455'}`,
        borderRadius: 3, fontSize: 10, fontWeight: 700,
        padding: '1px 6px', cursor: 'help',
      }}>
      ⚠ {(share * 100).toFixed(0)}% recovered
    </span>
  );
}

// ─── FilterSummary ────────────────────────────────────────────────────────────

function FilterSummary({
  rules, dataset, precheck,
}: {
  rules: Record<string, Stage1PerBandRule>;
  dataset: string;
  precheck: Stage1PrecheckResponse | null;
}) {
  const bands = Object.keys(rules).sort();
  if (bands.length === 0) return (
    <span style={{ fontSize: 11, color: '#7a9bb5' }}>
      No bands selected — run best-combo picker first.
    </span>
  );
  return (
    <div style={{ marginTop: 8, fontSize: 11 }}>
      <div style={{ color: '#7a9bb5', marginBottom: 4 }}>
        Dataset: <strong style={{ color: '#cfd9e3' }}>{dataset}</strong>
        {precheck && (
          <span style={{ marginLeft: 10 }}>
            {precheck.n_cold > 0 ? (
              <span style={{ color: '#fbbf24' }}>
                ~{precheck.estimated_compute_seconds}s ({precheck.n_cold} cold band{precheck.n_cold > 1 ? 's' : ''})
              </span>
            ) : (
              <span style={{ color: '#4ade80' }}>All warm — instant</span>
            )}
          </span>
        )}
      </div>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead>
          <tr style={{ color: '#7a9bb5' }}>
            <th style={{ textAlign: 'left', padding: '2px 6px', fontWeight: 500 }}>Band</th>
            <th style={{ textAlign: 'left', padding: '2px 6px', fontWeight: 500 }}>Rule</th>
            <th style={{ textAlign: 'left', padding: '2px 6px', fontWeight: 500 }}>Cache</th>
          </tr>
        </thead>
        <tbody>
          {bands.map(band => {
            const r = rules[band];
            const cs = precheck?.per_band_cache_status?.[band];
            return (
              <tr key={band}>
                <td style={{ padding: '2px 6px', color: '#cfd9e3' }}>{band}</td>
                <td style={{ padding: '2px 6px', color: '#a0bbd5', fontFamily: 'monospace', fontSize: 10 }}>
                  {r.rule_label}
                </td>
                <td style={{ padding: '2px 6px' }}>
                  {cs === 'warm' && <span style={{ color: '#4ade80' }}>● warm</span>}
                  {cs === 'cold' && <span style={{ color: '#fbbf24' }}>● cold</span>}
                  {!cs && <span style={{ color: '#555' }}>—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Heatmap cell ─────────────────────────────────────────────────────────────

const EXIT_FRACS = [0.25, 0.5, 0.75, 1.0];
const TRIGGER_NAMES = ['25_of_sl', '50_of_sl', '75_of_sl', 'L_avg', 'W_avg'];
const TRIGGER_LABELS: Record<string, string> = {
  '25_of_sl': '25% SL', '50_of_sl': '50% SL', '75_of_sl': '75% SL',
  'L_avg': 'L-avg', 'W_avg': 'W-avg',
};

function heatColor(delta: number | null, isTighterSl: boolean): string {
  if (isTighterSl) return '#1a2d4a';
  if (delta == null || isNaN(delta)) return '#111';
  if (delta > 50) return '#1a4a1a';
  if (delta > 10) return '#1a3a1a';
  if (delta > 0) return '#152a15';
  if (delta > -10) return '#3a1515';
  return '#4a1a1a';
}

function deltaBadge(delta: number | null, isTighterSl: boolean): React.ReactNode {
  if (isTighterSl) return <span style={{ color: '#60a5fa', fontSize: 9 }}>SL</span>;
  if (delta == null || isNaN(delta)) return <span style={{ color: '#555', fontSize: 9 }}>—</span>;
  const color = delta > 0 ? '#4ade80' : delta < 0 ? '#f87171' : '#7a9bb5';
  return <span style={{ color, fontSize: 10, fontWeight: 700 }}>{fmt$(delta, 0)}</span>;
}

// ─── Stage1BandTradesModal ────────────────────────────────────────────────────

type CaseTag = 'A' | 'B_reliable' | 'B_unreliable' | 'C_deeper' | 'C_recovered';
type TradeFilter = 'all' | 'losers' | 'sl_hit' | 'fired';

function classifyTrade(
  trade: Stage1TradeRow,
  triggerMtm: number,
  exitFrac: number,
): { caseTag: CaseTag; hypothetical: number; delta: number } {
  const net = trade.net_pnl_estimate_usd ?? 0;
  const minMtm = trade.min_mtm_usd ?? 0;
  const fired = minMtm <= triggerMtm;
  const hypothetical = fired
    ? exitFrac * triggerMtm + (1 - exitFrac) * net
    : net;
  const delta = hypothetical - net;

  let caseTag: CaseTag;
  if (!fired) {
    caseTag = 'A';
  } else if (net >= 0) {
    const relMin = trade.rel_time_min_mtm ?? 1;
    const relMax = trade.rel_time_max_mtm ?? 0;
    caseTag = relMax <= relMin ? 'B_unreliable' : 'B_reliable';
  } else {
    caseTag = net < triggerMtm ? 'C_deeper' : 'C_recovered';
  }
  return { caseTag, hypothetical, delta };
}

const CASE_COLOR: Record<CaseTag, string> = {
  A: '#556',
  B_reliable: '#f87171',
  B_unreliable: '#f87171',
  C_deeper: '#4ade80',
  C_recovered: '#fbbf24',
};

function Stage1BandTradesModal({
  band,
  ruleDict,
  ruleLabel,
  filters,
  dataset,
  lots,
  onClose,
}: {
  band: string;
  ruleDict: Record<string, unknown>;
  ruleLabel: string;
  filters: { expiry_bucket?: string; delta_target?: number; entry_hour_ist?: number };
  dataset: M7Dataset;
  lots: number;
  onClose: () => void;
}) {
  const [data, setData] = useState<Stage1BandTradesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  // User controls
  const [triggerChoice, setTriggerChoice] = useState<'25_of_sl' | '50_of_sl' | '75_of_sl' | 'sl_avg' | 'l_avg' | 'w_avg' | 'custom'>('l_avg');
  const [customTrigger, setCustomTrigger] = useState<string>('-500');
  const [exitFrac, setExitFrac] = useState<number>(0.5);
  const [tradeFilter, setTradeFilter] = useState<TradeFilter>('all');
  const [sortCol, setSortCol] = useState<'min_mtm_usd' | 'net_pnl_estimate_usd' | 'delta'>('min_mtm_usd');
  const [sortAsc, setSortAsc] = useState(true);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setFetchErr(null);
    fetchM7Stage1BandTrades(band, ruleDict, filters, dataset, ac.signal)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { if (!ac.signal.aborted) { setFetchErr(String(e)); setLoading(false); } });
    return () => ac.abort();
  }, [band, dataset]); // eslint-disable-line react-hooks/exhaustive-deps

  const triggerMtm = useMemo(() => {
    if (!data) return -500;
    if (triggerChoice === 'custom') return parseFloat(customTrigger) || 0;
    if (triggerChoice === '25_of_sl') return (data.trigger_levels.sl_avg ?? 0) * 0.25;
    if (triggerChoice === '50_of_sl') return (data.trigger_levels.sl_avg ?? 0) * 0.50;
    if (triggerChoice === '75_of_sl') return (data.trigger_levels.sl_avg ?? 0) * 0.75;
    return data.trigger_levels[triggerChoice] ?? -500;
  }, [data, triggerChoice, customTrigger]);

  const enriched = useMemo(() => {
    if (!data) return [];
    return data.trades.map(t => ({
      ...t,
      ...classifyTrade(t, triggerMtm, exitFrac),
    }));
  }, [data, triggerMtm, exitFrac]);

  const filtered = useMemo(() => {
    return enriched.filter(t => {
      if (tradeFilter === 'losers') return (t.net_pnl_estimate_usd ?? 0) < 0;
      if (tradeFilter === 'sl_hit') return !!t.is_premium_sl_hit;
      if (tradeFilter === 'fired') return t.caseTag !== 'A';
      return true;
    });
  }, [enriched, tradeFilter]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const av = sortCol === 'delta' ? a.delta : (a[sortCol] ?? 0);
      const bv = sortCol === 'delta' ? b.delta : (b[sortCol] ?? 0);
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
  }, [filtered, sortCol, sortAsc]);

  const tl = data?.trigger_levels;
  const lotsScale = lots / 100;
  // Scale raw per-100-lot values to actual lot count for display only.
  // Classification logic stays at per-100-lot scale so trigger comparison is consistent.
  const sc = (v: number | null | undefined) => v != null ? v * lotsScale : v;
  const tlLabel = (v: number | null) => v != null ? `$${(v * lotsScale).toFixed(0)}` : '—';

  const thStyle: React.CSSProperties = {
    padding: '4px 8px', color: '#7a9bb5', fontWeight: 500,
    fontSize: 11, textAlign: 'left', borderBottom: '1px solid #1a2d42',
    whiteSpace: 'nowrap', cursor: 'pointer',
  };
  const tdStyle: React.CSSProperties = { padding: '3px 8px', fontSize: 11, borderBottom: '1px solid #111' };

  const SortTh = ({ col, label }: { col: typeof sortCol; label: string }) => (
    <th style={thStyle} onClick={() => { if (sortCol === col) setSortAsc(a => !a); else { setSortCol(col); setSortAsc(true); } }}>
      {label}{sortCol === col ? (sortAsc ? ' ↑' : ' ↓') : ''}
    </th>
  );

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9000,
      background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 8,
        width: '92vw', maxWidth: 1100, maxHeight: '88vh',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        {/* Modal header */}
        <div style={{
          padding: '10px 14px', borderBottom: '1px solid #1a2d42',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontWeight: 700, fontSize: 13, color: '#e6edf3' }}>
            Trades — band {band}
          </span>
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>{ruleLabel}</span>
          {data && (
            <span style={{ fontSize: 11, color: '#7a9bb5', marginLeft: 4 }}>
              n={data.n_total} · losers={data.n_losers} · SL-hit={data.n_sl_hit} · winners={data.n_winners}
            </span>
          )}
          <span style={{ fontSize: 10, color: '#3a5a3a', marginLeft: 4 }}>
            ×{lots} lots (÷100 baseline)
          </span>
          <button onClick={onClose} style={{
            marginLeft: 'auto', background: 'none', border: 'none',
            color: '#7a9bb5', cursor: 'pointer', fontSize: 16,
          }}>✕</button>
        </div>

        {/* Controls */}
        <div style={{
          padding: '8px 14px', borderBottom: '1px solid #1a2d42',
          display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center',
        }}>
          {/* Trigger level */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, flexWrap: 'wrap' }}>
            <span style={{ color: '#7a9bb5' }}>Trigger:</span>
            {/* Fractional SL buttons */}
            {([
              ['25_of_sl', `25% SL (${tlLabel(tl?.sl_avg != null ? tl.sl_avg * 0.25 : null)})`],
              ['50_of_sl', `50% SL (${tlLabel(tl?.sl_avg != null ? tl.sl_avg * 0.50 : null)})`],
              ['75_of_sl', `75% SL (${tlLabel(tl?.sl_avg != null ? tl.sl_avg * 0.75 : null)})`],
            ] as const).map(([k, lbl]) => (
              <button key={k} onClick={() => setTriggerChoice(k)} style={{
                background: triggerChoice === k ? '#5a3a00' : '#0d1421',
                color: triggerChoice === k ? '#fbbf24' : '#7a9bb5',
                border: `1px solid ${triggerChoice === k ? '#fbbf2488' : '#1a2d42'}`,
                borderRadius: 3, padding: '2px 7px', fontSize: 10, cursor: 'pointer',
              }}>{lbl}</button>
            ))}
            <span style={{ color: '#2a3a4a', fontSize: 10 }}>|</span>
            {/* Reference levels */}
            {(['sl_avg', 'l_avg', 'w_avg'] as const).map(k => {
              const val = tl?.[k];
              const lbl = { sl_avg: `SL-avg (${tlLabel(val ?? null)})`, l_avg: `L-avg (${tlLabel(val ?? null)})`, w_avg: `W-avg (${tlLabel(val ?? null)})` }[k];
              return (
                <button key={k} onClick={() => setTriggerChoice(k)} style={{
                  background: triggerChoice === k ? '#1f6feb' : '#0d1421',
                  color: triggerChoice === k ? '#fff' : '#7a9bb5',
                  border: '1px solid #1a2d42', borderRadius: 3,
                  padding: '2px 7px', fontSize: 10, cursor: 'pointer',
                }}>{lbl}</button>
              );
            })}
            <button onClick={() => setTriggerChoice('custom')} style={{
              background: triggerChoice === 'custom' ? '#1f6feb' : '#0d1421',
              color: triggerChoice === 'custom' ? '#fff' : '#7a9bb5',
              border: '1px solid #1a2d42', borderRadius: 3,
              padding: '2px 7px', fontSize: 10, cursor: 'pointer',
            }}>Custom</button>
            {triggerChoice === 'custom' && (
              <input
                type="number"
                value={customTrigger}
                onChange={e => setCustomTrigger(e.target.value)}
                style={{
                  width: 70, background: '#0d1421', border: '1px solid #1a2d42',
                  color: '#cfd9e3', borderRadius: 3, padding: '2px 4px', fontSize: 10,
                }}
              />
            )}
            <span style={{ color: '#4ade80', fontSize: 10, marginLeft: 4 }}>
              MTM ≤ ${(triggerMtm * lotsScale).toFixed(0)}
            </span>
          </div>

          {/* Exit fraction */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <span style={{ color: '#7a9bb5' }}>Exit frac:</span>
            {[0.25, 0.5, 0.75, 1.0].map(f => (
              <button key={f} onClick={() => setExitFrac(f)} style={{
                background: exitFrac === f ? '#1f6feb' : '#0d1421',
                color: exitFrac === f ? '#fff' : '#7a9bb5',
                border: '1px solid #1a2d42', borderRadius: 3,
                padding: '2px 7px', fontSize: 10, cursor: 'pointer',
              }}>
                {(f * 100).toFixed(0)}%{f === 1.0 ? ' (SL)' : ''}
              </button>
            ))}
          </div>

          {/* Trade filter */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <span style={{ color: '#7a9bb5' }}>Show:</span>
            {([['all', 'All'], ['losers', 'Losers'], ['sl_hit', 'SL-hit'], ['fired', 'Fired']] as [TradeFilter, string][]).map(([f, lbl]) => (
              <button key={f} onClick={() => setTradeFilter(f)} style={{
                background: tradeFilter === f ? '#1f6feb' : '#0d1421',
                color: tradeFilter === f ? '#fff' : '#7a9bb5',
                border: '1px solid #1a2d42', borderRadius: 3,
                padding: '2px 7px', fontSize: 10, cursor: 'pointer',
              }}>{lbl} {f === 'all' ? `(${data?.n_total ?? '?'})` : ''}</button>
            ))}
            <span style={{ color: '#7a9bb5', fontSize: 10 }}>showing {sorted.length}</span>
          </div>
        </div>

        {/* Table body */}
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {loading && <div style={{ padding: 20, color: '#7a9bb5', textAlign: 'center' }}>Loading trades…</div>}
          {fetchErr && <div style={{ padding: 20, color: '#f87171' }}>{fetchErr}</div>}
          {!loading && !fetchErr && (
            <table style={{ borderCollapse: 'collapse', width: '100%' }}>
              <thead style={{ position: 'sticky', top: 0, background: '#0a0e17' }}>
                <tr>
                  <th style={thStyle}>Date</th>
                  <th style={thStyle}>Expiry</th>
                  <th style={thStyle}>Δ</th>
                  <th style={thStyle}>Hr</th>
                  <SortTh col="min_mtm_usd" label="Min MTM" />
                  <th style={thStyle}>Max MTM</th>
                  <SortTh col="net_pnl_estimate_usd" label="Net P&L" />
                  <th style={thStyle}>SL?</th>
                  <th style={thStyle}>Case</th>
                  <th style={thStyle}>Fires?</th>
                  <th style={{...thStyle, color: '#fbbf24'}} title={`When stage-1 fires, this fraction of lots (${(exitFrac*100).toFixed(0)}%) closes at the trigger MTM`}>
                    Partial @ ({(exitFrac*100).toFixed(0)}%)
                  </th>
                  <th style={{...thStyle, color: '#4ade80'}} title={`The remaining ${((1-exitFrac)*100).toFixed(0)}% of lots rides to the rule's normal exit and ends at the actual net P&L`}>
                    Rest @ ({((1-exitFrac)*100).toFixed(0)}%)
                  </th>
                  <SortTh col="delta" label="Hyp P&L" />
                  <SortTh col="delta" label="Δ P&L" />
                </tr>
              </thead>
              <tbody>
                {sorted.map(t => {
                  const fired = t.caseTag !== 'A';
                  const deltaColor = t.delta > 0.5 ? '#4ade80' : t.delta < -0.5 ? '#f87171' : '#7a9bb5';
                  const netColor = (t.net_pnl_estimate_usd ?? 0) >= 0 ? '#4ade80' : '#f87171';
                  return (
                    <tr key={t.trade_id} style={{ background: fired ? '#0d1421' : 'transparent' }}>
                      <td style={{ ...tdStyle, color: '#cfd9e3' }}>{t.friday_date_ist ?? '—'}</td>
                      <td style={{ ...tdStyle, color: '#a0bbd5', fontFamily: 'monospace', fontSize: 10 }}>{t.expiry_bucket ?? '—'}</td>
                      <td style={{ ...tdStyle, color: '#a0bbd5' }}>{t.delta_target != null ? t.delta_target.toFixed(2) : '—'}</td>
                      <td style={{ ...tdStyle, color: '#a0bbd5' }}>{t.entry_hour_ist ?? '—'}</td>
                      <td style={{ ...tdStyle, color: '#f87171', fontWeight: 600 }}>{sc(t.min_mtm_usd) != null ? `$${(sc(t.min_mtm_usd) as number).toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: '#4ade80' }}>{sc(t.max_mtm_usd) != null ? `$${(sc(t.max_mtm_usd) as number).toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: netColor, fontWeight: 600 }}>{sc(t.net_pnl_estimate_usd) != null ? fmt$(sc(t.net_pnl_estimate_usd) as number, 0) : '—'}</td>
                      <td style={{ ...tdStyle, color: t.is_premium_sl_hit ? '#f87171' : '#556' }}>{t.is_premium_sl_hit ? 'SL' : '—'}</td>
                      <td style={{ ...tdStyle, color: CASE_COLOR[t.caseTag], fontFamily: 'monospace', fontSize: 10, fontWeight: 700 }}>{t.caseTag}</td>
                      <td style={{ ...tdStyle, color: fired ? '#fbbf24' : '#556', textAlign: 'center' }}>{fired ? '✓' : '—'}</td>
                      {/* Partial exit cell: triggers MTM when fired, else dash */}
                      <td style={{ ...tdStyle, color: fired ? '#fbbf24' : '#444', textAlign: 'right' }}>
                        {fired ? `$${(triggerMtm * lotsScale).toFixed(0)}` : '—'}
                      </td>
                      {/* Rest exit cell: original net_pnl (the actual exit price for the unchanged portion) */}
                      <td style={{ ...tdStyle, color: (t.net_pnl_estimate_usd ?? 0) >= 0 ? '#4ade80' : '#f87171', textAlign: 'right' }}>
                        {fired ? fmt$(sc(t.net_pnl_estimate_usd) as number, 0) : '—'}
                      </td>
                      <td style={{ ...tdStyle, color: netColor }}>{fmt$(sc(t.hypothetical) as number, 0)}</td>
                      <td style={{ ...tdStyle, color: deltaColor, fontWeight: 700 }}>{fmt$(sc(t.delta) as number, 0)}</td>
                    </tr>
                  );
                })}
                {sorted.length === 0 && (
                  <tr><td colSpan={14} style={{ padding: 20, color: '#556', textAlign: 'center' }}>No trades match filter</td></tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer legend */}
        <div style={{ padding: '6px 14px', borderTop: '1px solid #1a2d42', fontSize: 10, color: '#556', display: 'flex', gap: 14, flexWrap: 'wrap' }}>
          <span><span style={{ color: CASE_COLOR.A }}>A</span> = didn't fire</span>
          <span><span style={{ color: CASE_COLOR.B_reliable }}>B</span> = winner that dipped (gave up profit)</span>
          <span><span style={{ color: CASE_COLOR.C_deeper }}>C_deeper</span> = loser saved</span>
          <span><span style={{ color: CASE_COLOR.C_recovered }}>C_recovered</span> = loser that recovered (hurt)</span>
          <span style={{ marginLeft: 'auto' }}>
            <span style={{ color: '#fbbf24' }}>Partial @</span> + <span style={{ color: '#4ade80' }}>Rest @</span> = <strong>Hyp P&L</strong>
            <span style={{ marginLeft: 4 }}>= {(exitFrac*100).toFixed(0)}% × trigger + {((1-exitFrac)*100).toFixed(0)}% × actual</span>
          </span>
        </div>
      </div>
    </div>
  );
}

// ─── Stage1AllTradesModal ─────────────────────────────────────────────────────

type AllTradeFilter = 'all' | 'losses' | 'best_win' | 'worst_dd_win';

function classifyAllTrade(
  trade: Stage1AllTradeRow,
  triggerChoice: '25_of_sl' | '50_of_sl' | '75_of_sl' | 'sl_avg' | 'l_avg' | 'w_avg' | 'custom',
  customValue: number,
  exitFrac: number,
): { caseTag: CaseTag; hypothetical: number; delta: number; triggerMtm: number | null } {
  const sl = trade.band_sl_avg;
  const bandTrigger =
    triggerChoice === '25_of_sl' ? (sl != null ? sl * 0.25 : null)
    : triggerChoice === '50_of_sl' ? (sl != null ? sl * 0.50 : null)
    : triggerChoice === '75_of_sl' ? (sl != null ? sl * 0.75 : null)
    : triggerChoice === 'sl_avg' ? sl
    : triggerChoice === 'l_avg' ? trade.band_l_avg
    : triggerChoice === 'w_avg' ? trade.band_w_avg
    : null;
  const triggerMtm = triggerChoice === 'custom' ? customValue : bandTrigger;

  if (triggerMtm == null) {
    return { caseTag: 'A', hypothetical: trade.net_pnl_estimate_usd ?? 0, delta: 0, triggerMtm: null };
  }
  return { ...classifyTrade(trade as Stage1TradeRow, triggerMtm, exitFrac), triggerMtm };
}

function Stage1AllTradesModal({
  resolvedRules,
  dataset,
  onClose,
}: {
  resolvedRules: Record<string, Stage1PerBandRule>;
  dataset: M7Dataset;
  onClose: () => void;
}) {
  const [data, setData] = useState<Stage1AllTradesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  const [triggerChoice, setTriggerChoice] = useState<'25_of_sl' | '50_of_sl' | '75_of_sl' | 'sl_avg' | 'l_avg' | 'w_avg' | 'custom'>('l_avg');
  const [customTrigger, setCustomTrigger] = useState<string>('-500');
  const [exitFrac, setExitFrac] = useState<number>(0.5);
  const [tradeFilter, setTradeFilter] = useState<AllTradeFilter>('all');
  const [sortCol, setSortCol] = useState<'min_mtm_usd' | 'net_pnl_estimate_usd' | 'delta' | 'band'>('min_mtm_usd');
  const [sortAsc, setSortAsc] = useState(true);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setFetchErr(null);
    fetchM7Stage1AllTrades(resolvedRules, dataset, ac.signal)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { if (!ac.signal.aborted) { setFetchErr(String(e)); setLoading(false); } });
    return () => ac.abort();
  }, [dataset]); // eslint-disable-line react-hooks/exhaustive-deps

  const customValue = parseFloat(customTrigger) || 0;

  const enriched = useMemo(() => {
    if (!data) return [];
    return data.trades.map(t => {
      const tLots = resolvedRules[t.band]?.lots ?? 100;
      const tScale = tLots / 100;
      const classified = classifyAllTrade(t, triggerChoice, customValue, exitFrac);
      return {
        ...t,
        ...classified,
        // Scaled display values (classification stays at raw 100-lot scale)
        min_mtm_usd_scaled: (t.min_mtm_usd ?? 0) * tScale,
        max_mtm_usd_scaled: (t.max_mtm_usd ?? 0) * tScale,
        net_pnl_scaled: (t.net_pnl_estimate_usd ?? 0) * tScale,
        hypothetical_scaled: classified.hypothetical * tScale,
        delta_scaled: classified.delta * tScale,
        triggerMtm_scaled: classified.triggerMtm != null ? classified.triggerMtm * tScale : null,
        lots: tLots,
      };
    });
  }, [data, triggerChoice, customValue, exitFrac, resolvedRules]);

  const filtered = useMemo(() => {
    if (tradeFilter === 'losses') return enriched.filter(t => (t.net_pnl_estimate_usd ?? 0) < 0);
    if (tradeFilter === 'best_win') {
      const winners = enriched.filter(t => (t.net_pnl_estimate_usd ?? 0) >= 0);
      if (!winners.length) return [];
      const max = Math.max(...winners.map(t => t.net_pnl_estimate_usd ?? 0));
      return winners.filter(t => t.net_pnl_estimate_usd === max);
    }
    if (tradeFilter === 'worst_dd_win') {
      const winners = enriched.filter(t => (t.net_pnl_estimate_usd ?? 0) >= 0);
      if (!winners.length) return [];
      const minMtm = Math.min(...winners.map(t => t.min_mtm_usd ?? 0));
      return winners.filter(t => t.min_mtm_usd === minMtm);
    }
    return enriched;
  }, [enriched, tradeFilter]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let av: number, bv: number;
      if (sortCol === 'delta') { av = a.delta; bv = b.delta; }
      else if (sortCol === 'band') { av = 0; bv = 0; } // handled below
      else { av = (a[sortCol] ?? 0) as number; bv = (b[sortCol] ?? 0) as number; }
      if (sortCol === 'band') return sortAsc ? a.band.localeCompare(b.band) : b.band.localeCompare(a.band);
      return sortAsc ? av - bv : bv - av;
    });
  }, [filtered, sortCol, sortAsc]);

  const nTotal = data?.n_total ?? 0;
  const nLosers = enriched.filter(t => (t.net_pnl_estimate_usd ?? 0) < 0).length;
  const nWinners = enriched.filter(t => (t.net_pnl_estimate_usd ?? 0) >= 0).length;
  const nFired = enriched.filter(t => t.caseTag !== 'A').length;

  const thStyle: React.CSSProperties = {
    padding: '4px 8px', color: '#7a9bb5', fontWeight: 500,
    fontSize: 11, textAlign: 'left', borderBottom: '1px solid #1a2d42',
    whiteSpace: 'nowrap', cursor: 'pointer',
  };
  const tdStyle: React.CSSProperties = { padding: '3px 8px', fontSize: 11, borderBottom: '1px solid #111' };

  type SortableCol = typeof sortCol;
  const SortTh = ({ col, label }: { col: SortableCol; label: string }) => (
    <th style={thStyle} onClick={() => {
      if (sortCol === col) setSortAsc(a => !a);
      else { setSortCol(col); setSortAsc(true); }
    }}>
      {label}{sortCol === col ? (sortAsc ? ' ↑' : ' ↓') : ''}
    </th>
  );

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9000,
      background: 'rgba(0,0,0,0.75)', display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 8,
        width: '96vw', maxWidth: 1300, maxHeight: '90vh',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid #1a2d42',
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
        }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: '#e6edf3' }}>
            All trades — best-combo selection
          </span>
          {data && (
            <span style={{ fontSize: 11, color: '#7a9bb5' }}>
              {nTotal} total · {nWinners} winners · {nLosers} losers · {nFired} fired stage-1
            </span>
          )}
          <span style={{ fontSize: 10, color: '#556', marginLeft: 4 }}>
            each trade uses its own band's trigger level
          </span>
          <button onClick={onClose} style={{
            marginLeft: 'auto', background: 'none', border: 'none',
            color: '#7a9bb5', cursor: 'pointer', fontSize: 18, lineHeight: 1,
          }}>✕</button>
        </div>

        {/* Controls */}
        <div style={{
          padding: '8px 16px', borderBottom: '1px solid #1a2d42',
          display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'center',
        }}>
          {/* Trigger */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, flexWrap: 'wrap' }}>
            <span style={{ color: '#7a9bb5' }}>Trigger (per band):</span>
            {/* Fractional SL options */}
            {([
              ['25_of_sl', '25% SL'],
              ['50_of_sl', '50% SL'],
              ['75_of_sl', '75% SL'],
            ] as const).map(([k, lbl]) => (
              <button key={k} onClick={() => setTriggerChoice(k)} style={{
                background: triggerChoice === k ? '#5a3a00' : '#0d1421',
                color: triggerChoice === k ? '#fbbf24' : '#7a9bb5',
                border: `1px solid ${triggerChoice === k ? '#fbbf2488' : '#1a2d42'}`,
                borderRadius: 3, padding: '2px 8px', fontSize: 10, cursor: 'pointer',
              }}>{lbl}</button>
            ))}
            <span style={{ color: '#2a3a4a', fontSize: 10 }}>|</span>
            {/* Reference levels */}
            {([['sl_avg', 'SL-avg'], ['l_avg', 'L-avg'], ['w_avg', 'W-avg']] as const).map(([k, lbl]) => (
              <button key={k} onClick={() => setTriggerChoice(k)} style={{
                background: triggerChoice === k ? '#1f6feb' : '#0d1421',
                color: triggerChoice === k ? '#fff' : '#7a9bb5',
                border: '1px solid #1a2d42', borderRadius: 3,
                padding: '2px 8px', fontSize: 10, cursor: 'pointer',
              }}>{lbl}</button>
            ))}
            <button onClick={() => setTriggerChoice('custom')} style={{
              background: triggerChoice === 'custom' ? '#1f6feb' : '#0d1421',
              color: triggerChoice === 'custom' ? '#fff' : '#7a9bb5',
              border: '1px solid #1a2d42', borderRadius: 3,
              padding: '2px 8px', fontSize: 10, cursor: 'pointer',
            }}>Custom $</button>
            {triggerChoice === 'custom' && (
              <input type="number" value={customTrigger} onChange={e => setCustomTrigger(e.target.value)}
                style={{ width: 70, background: '#0d1421', border: '1px solid #1a2d42', color: '#cfd9e3', borderRadius: 3, padding: '2px 4px', fontSize: 10 }} />
            )}
          </div>

          {/* Exit frac */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <span style={{ color: '#7a9bb5' }}>Exit frac:</span>
            {[0.25, 0.5, 0.75, 1.0].map(f => (
              <button key={f} onClick={() => setExitFrac(f)} style={{
                background: exitFrac === f ? '#1f6feb' : '#0d1421',
                color: exitFrac === f ? '#fff' : '#7a9bb5',
                border: '1px solid #1a2d42', borderRadius: 3,
                padding: '2px 8px', fontSize: 10, cursor: 'pointer',
              }}>{(f * 100).toFixed(0)}%{f === 1.0 ? ' (SL)' : ''}</button>
            ))}
          </div>

          {/* Filter */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <span style={{ color: '#7a9bb5' }}>Show:</span>
            {([
              ['all',          `All (${nTotal})`],
              ['losses',       `Losses (${nLosers})`],
              ['best_win',     'Best win'],
              ['worst_dd_win', 'Worst-DD win'],
            ] as [AllTradeFilter, string][]).map(([f, lbl]) => (
              <button key={f} onClick={() => setTradeFilter(f)} style={{
                background: tradeFilter === f ? '#1f6feb' : '#0d1421',
                color: tradeFilter === f ? '#fff' : '#7a9bb5',
                border: '1px solid #1a2d42', borderRadius: 3,
                padding: '2px 8px', fontSize: 10, cursor: 'pointer',
              }}>{lbl}</button>
            ))}
            <span style={{ color: '#7a9bb5', fontSize: 10 }}>· {sorted.length} showing</span>
          </div>
        </div>

        {/* Table */}
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {loading && <div style={{ padding: 24, color: '#7a9bb5', textAlign: 'center' }}>Loading trades…</div>}
          {fetchErr && <div style={{ padding: 24, color: '#f87171' }}>{fetchErr}</div>}
          {!loading && !fetchErr && (
            <table style={{ borderCollapse: 'collapse', width: '100%' }}>
              <thead style={{ position: 'sticky', top: 0, background: '#0a0e17', zIndex: 1 }}>
                <tr>
                  <th style={thStyle}>Date</th>
                  <SortTh col="band" label="Band" />
                  <th style={thStyle}>Rule</th>
                  <th style={thStyle}>Expiry</th>
                  <th style={thStyle}>Δ</th>
                  <th style={thStyle}>Hr</th>
                  <th style={thStyle}>Lots</th>
                  <SortTh col="min_mtm_usd" label="Min MTM" />
                  <th style={thStyle}>Max MTM</th>
                  <SortTh col="net_pnl_estimate_usd" label="Actual P&L" />
                  <th style={thStyle}>SL?</th>
                  <th style={thStyle}>Case</th>
                  <th style={thStyle}>Fires?</th>
                  <th style={{...thStyle, color: '#fbbf24'}} title={`When stage-1 fires, ${(exitFrac*100).toFixed(0)}% of lots close at this MTM`}>
                    Partial @ ({(exitFrac*100).toFixed(0)}%)
                  </th>
                  <th style={{...thStyle, color: '#4ade80'}} title={`The remaining ${((1-exitFrac)*100).toFixed(0)}% of lots rides to actual exit`}>
                    Rest @ ({((1-exitFrac)*100).toFixed(0)}%)
                  </th>
                  <SortTh col="delta" label="Hyp P&L" />
                  <SortTh col="delta" label="Δ P&L" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((t, i) => {
                  const fired = t.caseTag !== 'A';
                  const net = t.net_pnl_estimate_usd ?? 0;
                  const netColor = net >= 0 ? '#4ade80' : '#f87171';
                  const deltaColor = (t.delta_scaled ?? t.delta) > 0.5 ? '#4ade80' : (t.delta_scaled ?? t.delta) < -0.5 ? '#f87171' : '#7a9bb5';
                  const isHighlight = tradeFilter === 'best_win' || tradeFilter === 'worst_dd_win';
                  return (
                    <tr key={`${t.trade_id}-${i}`} style={{
                      background: isHighlight ? '#0f1e0f' : fired ? '#0d1421' : 'transparent',
                    }}>
                      <td style={{ ...tdStyle, color: '#cfd9e3' }}>{t.friday_date_ist ?? '—'}</td>
                      <td style={{ ...tdStyle, color: '#a0bbd5', fontWeight: 600 }}>{t.band}</td>
                      <td style={{ ...tdStyle, color: '#556', fontFamily: 'monospace', fontSize: 9 }}>{t.rule_label}</td>
                      <td style={{ ...tdStyle, color: '#a0bbd5', fontSize: 10 }}>{t.expiry_bucket ?? '—'}</td>
                      <td style={{ ...tdStyle, color: '#a0bbd5' }}>{t.delta_target != null ? t.delta_target.toFixed(2) : '—'}</td>
                      <td style={{ ...tdStyle, color: '#a0bbd5' }}>{t.entry_hour_ist ?? '—'}</td>
                      <td style={{ ...tdStyle, color: '#7a9bb5', fontSize: 9 }}>×{t.lots}</td>
                      <td style={{ ...tdStyle, color: '#f87171', fontWeight: 700 }}>{t.min_mtm_usd_scaled != null ? `$${t.min_mtm_usd_scaled.toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: '#4ade80' }}>{t.max_mtm_usd_scaled != null ? `$${t.max_mtm_usd_scaled.toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: netColor, fontWeight: 700 }}>{fmt$(t.net_pnl_scaled, 0)}</td>
                      <td style={{ ...tdStyle, color: t.is_premium_sl_hit ? '#f87171' : '#556' }}>{t.is_premium_sl_hit ? 'SL' : '—'}</td>
                      <td style={{ ...tdStyle, color: CASE_COLOR[t.caseTag], fontFamily: 'monospace', fontSize: 10, fontWeight: 700 }}>{t.caseTag}</td>
                      <td style={{ ...tdStyle, color: fired ? '#fbbf24' : '#444', textAlign: 'center' }}>{fired ? '✓' : '—'}</td>
                      {/* Partial @ : trigger MTM (scaled) when fired */}
                      <td style={{ ...tdStyle, color: fired ? '#fbbf24' : '#444', textAlign: 'right' }}>
                        {fired && t.triggerMtm_scaled != null ? `$${t.triggerMtm_scaled.toFixed(0)}` : '—'}
                      </td>
                      {/* Rest @ : actual net_pnl_scaled, only meaningful when fired */}
                      <td style={{ ...tdStyle, color: (t.net_pnl_estimate_usd ?? 0) >= 0 ? '#4ade80' : '#f87171', textAlign: 'right' }}>
                        {fired ? fmt$(t.net_pnl_scaled, 0) : '—'}
                      </td>
                      <td style={{ ...tdStyle, color: netColor }}>{fmt$(t.hypothetical_scaled, 0)}</td>
                      <td style={{ ...tdStyle, color: deltaColor, fontWeight: 700 }}>{fmt$(t.delta_scaled, 0)}</td>
                    </tr>
                  );
                })}
                {sorted.length === 0 && (
                  <tr><td colSpan={17} style={{ padding: 24, color: '#556', textAlign: 'center' }}>
                    {loading ? '' : 'No trades match filter'}
                  </td></tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer legend */}
        <div style={{
          padding: '6px 16px', borderTop: '1px solid #1a2d42',
          fontSize: 10, color: '#556', display: 'flex', gap: 16, flexWrap: 'wrap',
        }}>
          <span><span style={{ color: CASE_COLOR.A }}>A</span> no fire</span>
          <span><span style={{ color: CASE_COLOR.B_reliable }}>B</span> winner dipped — gave up profit</span>
          <span><span style={{ color: CASE_COLOR.C_deeper }}>C_deeper</span> loser saved</span>
          <span><span style={{ color: CASE_COLOR.C_recovered }}>C_recovered</span> loser that recovered (stage-1 hurts)</span>
          <span>Hyp = exit_frac × trigger + (1−frac) × actual · trigger $ = each band's own {triggerChoice === 'custom' ? 'custom value' : triggerChoice}</span>
          <span style={{ marginLeft: 'auto', color: '#3a4a5a' }}>
            Future: per-cell pivot graph (trough distribution winners vs losers)
          </span>
        </div>
      </div>
    </div>
  );
}

// ─── Stage1BandCard ───────────────────────────────────────────────────────────

function Stage1BandCard({
  band,
  cells,
  bestCell,
  summary,
  lots,
  baselineMetrics,
  ruleDict,
  ruleFilters,
  dataset,
  onViewTrades,
}: {
  band: string;
  cells: Stage1Cell[];
  bestCell: Stage1BestCell | null;
  summary: Stage1BandSummary | null;
  lots: number;
  baselineMetrics?: Stage1PerBandRule['baseline_metrics'];
  ruleDict?: Record<string, unknown>;
  ruleFilters?: { expiry_bucket?: string; delta_target?: number; entry_hour_ist?: number };
  dataset?: M7Dataset;
  onViewTrades?: () => void;
}) {
  const [selector, setSelector] = useState<'avg_pnl' | 'composite'>('avg_pnl');
  const [expandedCell, setExpandedCell] = useState<string | null>(null);

  // Lazy-fetched trades for this band — used to compute hyp MTM aggregates per cell
  // (F14 linear approximation, client-side).
  const [bandTrades, setBandTrades] = useState<Stage1TradeRow[] | null>(null);
  useEffect(() => {
    if (expandedCell && !bandTrades && ruleDict && dataset) {
      const ac = new AbortController();
      fetchM7Stage1BandTrades(band, ruleDict, ruleFilters ?? {}, dataset, ac.signal)
        .then(d => setBandTrades(d.trades))
        .catch(() => {});
      return () => ac.abort();
    }
  }, [expandedCell, bandTrades, band, dataset]); // eslint-disable-line react-hooks/exhaustive-deps

  const lotsScale = lots / 100;
  const sc = (v: number | null | undefined) => v != null ? v * lotsScale : v;

  // Derive band's baseline (no-stage-1) aggregates by finding any cell where
  // both `*_hyp` and `delta_*` are non-null, then computing actual = hyp - delta.
  // The baseline is the same across all cells in the band, so this is safe.
  const baseline = useMemo(() => {
    let actualAvg: number | null = null;
    let actualWinRate: number | null = null;
    let actualMaxLoss: number | null = null;
    let actualCvar: number | null = null;
    for (const c of cells) {
      if (actualAvg == null && c.avg_hyp_pnl != null && c.delta_avg_pnl != null) {
        actualAvg = c.avg_hyp_pnl - c.delta_avg_pnl;
      }
      if (actualWinRate == null && c.win_rate_hyp != null && c.delta_win_rate != null) {
        actualWinRate = c.win_rate_hyp - c.delta_win_rate;
      }
      if (actualMaxLoss == null && c.max_loss_hyp != null && c.delta_max_loss != null) {
        actualMaxLoss = c.max_loss_hyp - c.delta_max_loss;
      }
      if (actualCvar == null && c.cvar_95_net_hyp != null && c.delta_cvar_95 != null) {
        actualCvar = c.cvar_95_net_hyp - c.delta_cvar_95;
      }
      if (actualAvg != null && actualWinRate != null && actualMaxLoss != null && actualCvar != null) break;
    }
    return { actualAvg, actualWinRate, actualMaxLoss, actualCvar };
  }, [cells]);

  const bandVerdict = bestCell?.band_verdict ?? '';
  const nTotal = bestCell?.n_total ?? summary?.n_total ?? 0;
  const ruleLabel = bestCell?.rule_label ?? summary?.rule_label ?? '';

  // Build cell lookup: key = `${exit_frac}|${trigger_level}`
  const cellMap = useMemo(() => {
    const m: Record<string, Stage1Cell> = {};
    for (const c of cells) m[`${c.exit_frac}|${c.trigger_level}`] = c;
    return m;
  }, [cells]);

  // Find the highlighted best cell
  const highlightKey = useMemo(() => {
    if (!bestCell) return null;
    if (selector === 'avg_pnl') {
      return bestCell.best_avg_pnl_exit_frac != null && bestCell.best_avg_pnl_trigger_level
        ? `${bestCell.best_avg_pnl_exit_frac}|${bestCell.best_avg_pnl_trigger_level}`
        : null;
    }
    return bestCell.best_composite_exit_frac != null && bestCell.best_composite_trigger_level
      ? `${bestCell.best_composite_exit_frac}|${bestCell.best_composite_trigger_level}`
      : null;
  }, [bestCell, selector]);

  const highlightedCell = highlightKey ? cellMap[highlightKey] : null;
  const disagreement = bestCell && !bestCell.cells_agree;

  const cardBorder = bandVerdict === 'WORTH_IT' ? '#2a5a3a'
    : bandVerdict === 'SKIP_NEGATIVE' ? '#5a2a2a'
    : '#1a2d42';

  return (
    <div style={{
      background: '#0a0e17', border: `1px solid ${cardBorder}`, borderRadius: 6,
      padding: 12, marginBottom: 8,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: '#e6edf3' }}>{band}</span>
        <VerdictChip verdict={bandVerdict} />
        {highlightedCell && (
          <CRecoveredChip
            level={highlightedCell.c_recovered_warn_level}
            share={highlightedCell.c_recovered_share}
          />
        )}
        <span style={{ fontSize: 11, color: '#7a9bb5', marginLeft: 4 }}>
          {ruleLabel} · n={nTotal}
        </span>
        {summary && (
          <span style={{ fontSize: 10, color: '#556', marginLeft: 'auto' }}>
            SL_avg: {fmtAbs$(sc(summary.sl_avg))} L_avg: {fmtAbs$(sc(summary.l_avg))} W_avg: {fmtAbs$(sc(summary.w_avg))}
            <span style={{ color: '#2a3a2a', marginLeft: 4 }}>×{lots}L</span>
          </span>
        )}
        {onViewTrades && (
          <button
            onClick={onViewTrades}
            style={{
              background: '#0d1421', color: '#7a9bb5',
              border: '1px solid #1a2d42', borderRadius: 3,
              padding: '2px 8px', fontSize: 10, cursor: 'pointer',
              marginLeft: summary ? 8 : 'auto',
            }}
          >
            View {nTotal} trades →
          </button>
        )}
      </div>

      {/* Selector toggle + disagreement */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        {(['avg_pnl', 'composite'] as const).map(s => (
          <button
            key={s}
            onClick={() => setSelector(s)}
            style={{
              background: selector === s ? '#1f6feb' : '#0d1421',
              color: selector === s ? '#fff' : '#7a9bb5',
              border: '1px solid #1a2d42', borderRadius: 4,
              padding: '3px 8px', fontSize: 10, cursor: 'pointer',
            }}
          >
            {s === 'avg_pnl' ? 'Avg P&L' : 'Composite'}
          </button>
        ))}
        {disagreement && bestCell && (
          <span style={{ fontSize: 10, color: '#fbbf24', marginLeft: 6 }}>
            ⚡ Selectors disagree — toggle to compare
          </span>
        )}
      </div>

      {/* Best cell highlight row */}
      {highlightedCell && (
        <div style={{
          background: '#0d1421', borderRadius: 4, padding: '6px 10px',
          marginBottom: 8, fontSize: 11, display: 'flex', gap: 16, flexWrap: 'wrap',
        }}>
          <span>
            <span style={{ color: '#7a9bb5' }}>Exit: </span>
            <strong style={{ color: '#cfd9e3' }}>{(highlightedCell.exit_frac * 100).toFixed(0)}%</strong>
            <span style={{ color: '#7a9bb5' }}> @ </span>
            <strong style={{ color: '#cfd9e3' }}>{TRIGGER_LABELS[highlightedCell.trigger_level] ?? highlightedCell.trigger_level}</strong>
          </span>
          <span>
            <span style={{ color: '#7a9bb5' }}>Δ avg P&L: </span>
            <strong style={{ color: (highlightedCell.delta_avg_pnl ?? 0) > 0 ? '#4ade80' : '#f87171' }}>
              {fmt$(sc(highlightedCell.delta_avg_pnl), 1)}
            </strong>
          </span>
          <span>
            <span style={{ color: '#7a9bb5' }}>Δ win rate: </span>
            <strong style={{ color: (highlightedCell.delta_win_rate ?? 0) > 0 ? '#4ade80' : '#f87171' }}>
              {fmtPct(highlightedCell.delta_win_rate)}
            </strong>
          </span>
          <span>
            <span style={{ color: '#7a9bb5' }}>EV/trade: </span>
            <strong style={{ color: (highlightedCell.ev_per_trade ?? 0) > 0 ? '#4ade80' : '#f87171' }}>
              {fmt$(sc(highlightedCell.ev_per_trade), 1)}
            </strong>
          </span>
          <span>
            <span style={{ color: '#7a9bb5' }}>C_saved: </span>
            <strong style={{ color: '#cfd9e3' }}>{fmtN(highlightedCell.n_C_deeper)}</strong>
            <span style={{ color: '#7a9bb5' }}> / C_hurt: </span>
            <strong style={{ color: '#cfd9e3' }}>{fmtN(highlightedCell.n_C_recovered)}</strong>
          </span>
        </div>
      )}

      {/* 4×5 heatmap */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 10 }}>
          <thead>
            <tr>
              <th style={{ padding: '3px 8px', color: '#7a9bb5', textAlign: 'left' }}>Exit %</th>
              {TRIGGER_NAMES.map(t => (
                <th key={t} style={{ padding: '3px 8px', color: '#7a9bb5', textAlign: 'center' }}>
                  {TRIGGER_LABELS[t]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {EXIT_FRACS.map(ef => (
              <tr key={ef}>
                <td style={{
                  padding: '3px 8px', color: ef === 1.0 ? '#60a5fa' : '#cfd9e3',
                  fontWeight: 600,
                }}>
                  {(ef * 100).toFixed(0)}%{ef === 1.0 ? ' (SL)' : ''}
                </td>
                {TRIGGER_NAMES.map(tl => {
                  const key = `${ef}|${tl}`;
                  const c = cellMap[key];
                  const isHighlight = key === highlightKey;
                  const isTighterSl = ef === 1.0;
                  // Apply lotsScale so heatmap matches CellDetail dollar values
                  const delta = c?.delta_avg_pnl != null ? c.delta_avg_pnl * lotsScale : null;
                  return (
                    <td
                      key={tl}
                      onClick={() => setExpandedCell(expandedCell === key ? null : key)}
                      style={{
                        padding: '4px 10px', textAlign: 'center', cursor: 'pointer',
                        background: isHighlight ? (isTighterSl ? '#1f3a5a' : '#1a3a2a') : heatColor(delta, isTighterSl),
                        border: isHighlight ? '1px solid #ffffff44' : '1px solid #1a2d42',
                        borderRadius: 2,
                      }}
                    >
                      {c?.status === 'trigger_undefined'
                        ? <span style={{ color: '#555', fontSize: 9 }}>n/a</span>
                        : deltaBadge(delta, isTighterSl)
                      }
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Expanded cell detail */}
      {expandedCell && cellMap[expandedCell] && (
        <CellDetail
          cell={cellMap[expandedCell]}
          lotsScale={lotsScale}
          baseline={baseline}
          baselineMetrics={baselineMetrics}
          lots={lots}
          bandTrades={bandTrades}
        />
      )}
    </div>
  );
}

// ─── CellDetail ───────────────────────────────────────────────────────────────

function CellDetail({
  cell, lotsScale = 1, baseline, baselineMetrics, lots, bandTrades,
}: {
  cell: Stage1Cell;
  lotsScale?: number;
  baseline?: { actualAvg: number | null; actualWinRate: number | null; actualMaxLoss: number | null; actualCvar: number | null };
  baselineMetrics?: Stage1PerBandRule['baseline_metrics'];
  lots?: number;
  bandTrades?: Stage1TradeRow[] | null;
}) {
  const sc = (v: number | null | undefined) => v != null ? v * lotsScale : v;
  const triggerStr = `${fmtAbs$(sc(cell.trigger_mtm))}`;

  // Derive missing hyp values from delta + baseline (algebra: hyp = actual + delta).
  // Prefer baselineMetrics (always available from best-combo); fall back to cells-recovered baseline.
  const bmAvg = baselineMetrics?.avg_net_pnl ?? null;
  const bmWR = baselineMetrics?.win_rate ?? null;
  const actualAvgPnl = bmAvg ?? baseline?.actualAvg ?? null;
  const actualWinRate = bmWR ?? baseline?.actualWinRate ?? null;
  const derivedHypAvg = cell.avg_hyp_pnl != null
    ? cell.avg_hyp_pnl
    : (actualAvgPnl != null && cell.delta_avg_pnl != null
        ? actualAvgPnl + cell.delta_avg_pnl
        : null);
  const derivedHypWinRate = cell.win_rate_hyp != null
    ? cell.win_rate_hyp
    : (actualWinRate != null && cell.delta_win_rate != null
        ? actualWinRate + cell.delta_win_rate
        : null);
  const hypAvgIsDerived = cell.avg_hyp_pnl == null && derivedHypAvg != null;
  const hypWinIsDerived = cell.win_rate_hyp == null && derivedHypWinRate != null;
  const nA = cell.n_A ?? 0;
  const nBr = cell.n_B_reliable ?? 0;
  const nBu = cell.n_B_unreliable ?? 0;
  const nC = cell.n_C ?? 0;
  const nCd = cell.n_C_deeper ?? 0;
  const nCr = cell.n_C_recovered ?? 0;
  const efPct = `${(cell.exit_frac * 100).toFixed(0)}%`;
  const savedStr = fmt$(sc(cell.avg_save_per_c_deeper), 1);
  const hurtStr = fmt$(sc(cell.avg_hurt_per_c_recovered), 1);

  type RowDef = { label: string; value: string; explain: string; section?: string };
  const rows: RowDef[] = [
    // ── Setup ──────────────────────────────────────────────────────────────────
    {
      section: 'Setup',
      label: 'Status', value: cell.status,
      explain: '"ok" = reference level defined, enough trades. "trigger_undefined" = no trades in the subgroup that defines this trigger (e.g. no SL-hit trades for an SL-avg trigger).',
    },
    {
      label: 'Exit frac', value: efPct,
      explain: `Close ${efPct} of lots at the trigger. The other ${(100 - cell.exit_frac * 100).toFixed(0)}% stay on and exit normally at the original rule's end time / SL.`,
    },
    {
      label: 'Trigger', value: TRIGGER_LABELS[cell.trigger_level] ?? cell.trigger_level,
      explain: 'The reference level that sets the MTM threshold. "25% SL" = 25% of the average SL-hit trough. "L-avg" = average trough of losing trades. "W-avg" = average trough of winning trades.',
    },
    {
      label: 'Trigger MTM', value: triggerStr,
      explain: `Stage-1 fires when the position's mark-to-market (cumulative USD change on both legs) dips TO or BELOW ${triggerStr}. This is the moment you close ${efPct} of lots.`,
    },
    // ── Trade counts ───────────────────────────────────────────────────────────
    {
      section: 'Trade counts (out of n_total)',
      label: 'n_total', value: fmtN(cell.n_total),
      explain: 'All trades for this band + rule + optional filters. Every metric is computed over these trades.',
    },
    {
      label: 'n_A  (no fire)', value: fmtN(nA),
      explain: `${nA} trades whose intraday trough never reached ${triggerStr}. Stage-1 does nothing — the full position exits at the original rule. P&L is unchanged. Example: a winner that only dipped to -$20 when trigger is -$29.`,
    },
    {
      label: 'n_B_reliable  (winner dipped)', value: fmtN(nBr),
      explain: `${nBr} winning trades (final P&L ≥ 0) that DID dip below ${triggerStr}, and whose peak came AFTER their trough (reliable timing). Stage-1 fires — you close ${efPct} at ${triggerStr}, then the remaining ${(100 - cell.exit_frac * 100).toFixed(0)}% rides to the positive final exit. You gave up some recovery profit on the exited fraction. Avg cost: ${hurtStr !== '—' ? hurtStr : 'see EV'} per trade.`,
    },
    {
      label: 'n_B_unreliable  (peak before trough)', value: fmtN(nBu),
      explain: `${nBu} winner trades where the best price came BEFORE the worst — meaning the position peaked before it troughed. The timing of the exit matters here. These are excluded from the headline EV (included in "audited EV" only) because the fill timing assumption is unreliable.`,
    },
    {
      label: 'n_C  (fires on losers)', value: fmtN(nC),
      explain: `${nC} losing trades (final P&L < 0) where stage-1 fired. Each gets split into C_deeper or C_recovered depending on whether the trade got worse or partially recovered after the trigger.`,
    },
    {
      label: 'n_C_deeper  (saved)', value: fmtN(nCd),
      explain: `${nCd} losing trades that kept moving AGAINST you after the trigger (final loss deeper than ${triggerStr}). Stage-1 helped — you closed ${efPct} early at ${triggerStr}, avoiding part of the further loss. Avg saving: ${savedStr} per trade. Example: trigger fires at -$29, trade ends at -$152 → you saved on the ${efPct} of lots that exited at -$29 instead of -$152.`,
    },
    {
      label: 'n_C_recovered  (stage-1 hurts)', value: fmtN(nCr),
      explain: `${nCr} losing trades that RECOVERED above ${triggerStr} after the trigger fired but still ended negative. Stage-1 hurt here — you closed ${efPct} at the worst point, but the position partially recovered. The exited fraction missed the recovery. Avg cost: ${hurtStr} per trade.`,
    },
    {
      label: 'C_recovered share', value: fmtPct(cell.c_recovered_share),
      explain: `${fmtPct(cell.c_recovered_share)} of loser-fired trades (n_C) fell into the C_recovered bucket. High values (≥30%) mean stage-1 frequently fires on losers that would have partially recovered — consider a tighter trigger or smaller exit_frac.`,
    },
    // ── Magnitudes ─────────────────────────────────────────────────────────────
    {
      section: 'Magnitudes',
      label: 'avg saved/trade  (C_deeper)', value: savedStr,
      explain: `Average $ saved per C_deeper trade = exit_frac × |trigger − final_net_pnl|. For each saved trade: you closed ${efPct} of lots at ${triggerStr} instead of at the deeper final loss. Positive = good. Computed over ${nCd} trade(s).`,
    },
    {
      label: 'avg given-up/trade  (B_reliable)', value: fmt$(sc(cell.avg_given_up), 1),
      explain: cell.avg_given_up == null
        ? `Shows "—" because n_B_reliable = 0 — no winners dipped to the trigger, so nothing to average.`
        : `Average $ given UP per B_reliable trade = exit_frac × (final_net_pnl − trigger). These are the ${nBr} winners that dipped to the trigger but recovered — you closed ${efPct} of lots at ${triggerStr} (a loss for that fraction), and missed the recovery on that fraction. Negative sign = bad (you paid this cost on every B_reliable trade).`,
    },
    {
      label: 'avg hurt/trade  (C_recovered)', value: hurtStr,
      explain: hurtStr === '—'
        ? `Shows "—" because n_C_recovered = 0 — no loser trades recovered above the trigger, so there's nothing to average.`
        : `Average $ given up per C_recovered trade = exit_frac × |final_net_pnl − trigger|. Different from "given-up" above: these are LOSER trades that still ended negative but recovered above the trigger after firing. Stage-1 locked in trigger MTM for ${efPct} of lots, but those lots would have ended at a higher (less negative) final value. Computed over ${nCr} trade(s).`,
    },
    {
      label: 'pct_B_unreliable', value: fmtPct(cell.pct_B_unreliable),
      explain: `${fmtPct(cell.pct_B_unreliable)} of all B trades (winner-fired) had their peak BEFORE their trough. High % means the timing assumption is shaky for this trigger level — fill might differ from modeled.`,
    },
    // ── P&L impact ─────────────────────────────────────────────────────────────
    {
      section: 'P&L impact vs baseline (no stage-1)',
      label: 'EV/trade', value: fmt$(sc(cell.ev_per_trade), 1),
      explain: `Expected value per trade = (n_C_deeper/n) × avg_saved − (n_B_reliable/n) × avg_given_up. This is the net benefit of stage-1 averaged across ALL trades (most don't fire). Here: (${nCd}/${cell.n_total}) saves minus (${nBr}/${cell.n_total}) giveaways = ${fmt$(sc(cell.ev_per_trade), 1)}.`,
    },
    {
      label: 'Δ avg P&L', value: fmt$(sc(cell.delta_avg_pnl), 1),
      explain: `Average P&L per trade WITH stage-1 minus WITHOUT. Equivalent to EV/trade when B_unreliable = 0. ${(cell.delta_avg_pnl ?? 0) < 0 ? 'Negative here — stage-1 costs more from B trades than it saves from C trades.' : 'Positive — stage-1 adds value on average.'}`,
    },
    {
      label: 'Δ win rate', value: fmtPct(cell.delta_win_rate),
      explain: 'Change in win rate (trades ending positive ÷ total). B_reliable trades can flip from winner to loser if stage-1 locks in more than their final profit. Positive = more winners with stage-1.',
    },
    {
      label: 'Δ CVaR-95', value: fmt$(sc(cell.delta_cvar_95), 1),
      explain: 'Change in conditional value-at-risk (average of worst 5% of trades). Positive = worst-case trades are less bad with stage-1. Even when EV is negative, CVaR improvement can still justify stage-1 for risk reduction.',
    },
    {
      label: 'Δ max loss', value: fmt$(sc(cell.delta_max_loss), 1),
      explain: 'Change in single worst trade P&L. Positive = the worst trade is less bad with stage-1 (because stage-1 exited part of the position before it hit full depth).',
    },
    {
      label: 'Δ max consec losses', value: fmtN(cell.delta_max_consec_losses),
      explain: 'Change in maximum consecutive losing trades. Negative would mean stage-1 converts some winners to losers (B_reliable flipping), increasing streaks. Positive means fewer consecutive losses.',
    },
    {
      label: 'Hyp avg P&L', value: fmt$(sc(derivedHypAvg), 1) + (hypAvgIsDerived ? ' †' : ''),
      explain: derivedHypAvg == null
        ? `Cannot derive — no baseline available (best-combo row missing) and no cell in this band has a non-null avg_hyp_pnl. Δ avg P&L above still shows the change vs baseline.`
        : hypAvgIsDerived
          ? `† Derived from baseline: actual_avg_P&L = ${fmt$(sc(actualAvgPnl), 1)} (from best-combo row), then this cell's hyp = baseline + Δ avg P&L = ${fmt$(sc(actualAvgPnl), 1)} + ${fmt$(sc(cell.delta_avg_pnl), 1)} = ${fmt$(sc(derivedHypAvg), 1)}. Same as: exit_frac × trigger + (1 − exit_frac) × actual_net_pnl, averaged.`
          : `Average P&L WITH stage-1 applied. Formula per trade: exit_frac × trigger + (1 − exit_frac) × actual_net_pnl. The exited fraction locks in trigger MTM; the rest exits normally.`,
    },
    {
      label: 'Hyp win rate', value: fmtPct(derivedHypWinRate) + (hypWinIsDerived ? ' †' : ''),
      explain: derivedHypWinRate == null
        ? `Cannot derive — no baseline available and no cell in this band has a non-null win_rate_hyp.`
        : hypWinIsDerived
          ? `† Derived from baseline: actual_win_rate = ${fmtPct(actualWinRate)} (from best-combo row), hyp = baseline + Δ win rate = ${fmtPct(actualWinRate)} + ${fmtPct(cell.delta_win_rate)} = ${fmtPct(derivedHypWinRate)}.`
          : `Win rate on hypothetical P&L. Some B_reliable trades may flip to losers if stage-1 locks in more loss than the final gain.`,
    },
    {
      label: 'Composite v2 (hyp)', value: cell.composite_score_v2?.toFixed(4) ?? '—',
      explain: 'Normalised composite score (0–1) across all 20 cells in this band. Ranks cells by a blend of sortino, calmar, and CVaR. Higher = better risk-adjusted profile. Normalised within this band only — not comparable across bands.',
    },
  ];

  // Baseline metrics from best-combo (per-trade values are scaled by lots/100 for "scaled" display)
  const bm = baselineMetrics;
  const totalScale = (lots ?? 100) / 100;

  // ── Comparison rows: baseline → stage-1 → Δ ──
  // direction: 'pos' = positive Δ is better (green ↑), 'neg' = negative Δ is better (green ↓), 'neutral' = no preference
  type CmpRow = { label: string; baseline: string; stage1: string; delta: string; deltaColor: string; arrow: string; note?: string; section?: string };
  const cmpRows: CmpRow[] = [];

  if (bm) {
    // Helper to format a comparison row
    const fmtDelta = (raw: number | null, isPositiveBetter: boolean, asPct = false): { delta: string; color: string; arrow: string } => {
      if (raw == null || isNaN(raw)) return { delta: '—', color: '#7a9bb5', arrow: '' };
      const better = isPositiveBetter ? raw > 0 : raw < 0;
      const same = Math.abs(raw) < 1e-9;
      const arrow = same ? '−' : (raw > 0 ? '↑' : '↓');
      const color = same ? '#7a9bb5' : (better ? '#4ade80' : '#f87171');
      const s = asPct ? `${(raw * 100 >= 0 ? '+' : '')}${(raw * 100).toFixed(2)}%` : `${raw >= 0 ? '+' : ''}$${raw.toFixed(2)}`;
      return { delta: s, color, arrow };
    };

    // 1) Avg net P&L
    const baseAvg = bm.avg_net_pnl != null ? bm.avg_net_pnl * totalScale : null;
    const deltaAvg = cell.delta_avg_pnl != null ? cell.delta_avg_pnl * lotsScale : null;
    const stageAvg = baseAvg != null && deltaAvg != null ? baseAvg + deltaAvg : null;
    const dA = fmtDelta(deltaAvg, true);
    cmpRows.push({
      label: 'Avg net P&L',
      baseline: baseAvg != null ? `$${baseAvg.toFixed(2)}` : '—',
      stage1: stageAvg != null ? `$${stageAvg.toFixed(2)}` : '—',
      delta: dA.delta, deltaColor: dA.color, arrow: dA.arrow,
    });

    // 2) Total net P&L
    const baseTot = bm.avg_net_pnl != null && bm.n_trades ? bm.avg_net_pnl * bm.n_trades * totalScale : null;
    const stageTot = stageAvg != null && bm.n_trades ? stageAvg * bm.n_trades : null;
    const deltaTot = baseTot != null && stageTot != null ? stageTot - baseTot : null;
    const dT = fmtDelta(deltaTot, true);
    cmpRows.push({
      label: 'Total net P&L',
      baseline: baseTot != null ? `$${baseTot.toFixed(2)}` : '—',
      stage1: stageTot != null ? `$${stageTot.toFixed(2)}` : '—',
      delta: dT.delta, deltaColor: dT.color, arrow: dT.arrow,
    });

    // 3) Win rate
    const baseWR = bm.win_rate;
    const stageWR = baseWR != null && cell.delta_win_rate != null ? baseWR + cell.delta_win_rate : null;
    const dW = fmtDelta(cell.delta_win_rate, true, true);
    cmpRows.push({
      label: 'Win rate',
      baseline: baseWR != null ? `${(baseWR * 100).toFixed(2)}%` : '—',
      stage1: stageWR != null ? `${(stageWR * 100).toFixed(2)}%` : '—',
      delta: dW.delta, deltaColor: dW.color, arrow: dW.arrow,
    });

    // 4) Largest loss (less-bad = better)
    const baseML = bm.max_loss_usd != null ? bm.max_loss_usd * totalScale : null;
    const deltaML = cell.delta_max_loss != null ? cell.delta_max_loss * lotsScale : null;
    const stageML = baseML != null && deltaML != null ? baseML + deltaML : null;
    const dML = fmtDelta(deltaML, true);
    cmpRows.push({
      label: 'Largest loss',
      baseline: baseML != null ? `$${baseML.toFixed(2)}` : '—',
      stage1: stageML != null ? `$${stageML.toFixed(2)}` : '—',
      delta: dML.delta, deltaColor: dML.color, arrow: dML.arrow,
      note: 'positive Δ = less negative = better',
    });

    // 5) Max consec losses (lower = better)
    const baseMCL = bm.max_consec_losses;
    const stageMCL = baseMCL != null && cell.delta_max_consec_losses != null ? baseMCL + cell.delta_max_consec_losses : null;
    const dMCL = cell.delta_max_consec_losses != null
      ? { delta: `${cell.delta_max_consec_losses > 0 ? '+' : ''}${cell.delta_max_consec_losses}`,
          color: cell.delta_max_consec_losses < 0 ? '#4ade80' : cell.delta_max_consec_losses > 0 ? '#f87171' : '#7a9bb5',
          arrow: cell.delta_max_consec_losses < 0 ? '↓' : cell.delta_max_consec_losses > 0 ? '↑' : '−' }
      : { delta: '—', color: '#7a9bb5', arrow: '' };
    cmpRows.push({
      label: 'Max consec losses',
      baseline: baseMCL != null ? String(baseMCL) : '—',
      stage1: stageMCL != null ? String(stageMCL) : '—',
      delta: dMCL.delta, deltaColor: dMCL.color, arrow: dMCL.arrow,
      note: 'lower = better',
    });

    // 6) n wins / n losses (derived from win-rate change)
    if (bm.n_trades && bm.n_wins != null && bm.n_losses != null && stageWR != null) {
      const stageWins = Math.round(stageWR * bm.n_trades);
      const stageLosses = bm.n_trades - stageWins;
      const dwins = stageWins - bm.n_wins;
      cmpRows.push({
        label: 'n wins / n losses',
        baseline: `${bm.n_wins} / ${bm.n_losses}`,
        stage1: `${stageWins} / ${stageLosses}`,
        delta: `${dwins >= 0 ? '+' : ''}${dwins} / ${dwins >= 0 ? '−' : '+'}${Math.abs(dwins)}`,
        deltaColor: dwins > 0 ? '#4ade80' : dwins < 0 ? '#f87171' : '#7a9bb5',
        arrow: dwins > 0 ? '↑' : dwins < 0 ? '↓' : '−',
      });
    }

    // 7) Composite v2 (baseline composite vs stage-1 within-band composite)
    if (bm.composite_score != null && cell.composite_score_v2 != null) {
      const dCmp = cell.composite_score_v2 - bm.composite_score;
      cmpRows.push({
        label: 'Composite v2',
        baseline: bm.composite_score.toFixed(4),
        stage1: cell.composite_score_v2.toFixed(4),
        delta: `${dCmp >= 0 ? '+' : ''}${dCmp.toFixed(4)}`,
        deltaColor: dCmp > 0 ? '#4ade80' : dCmp < 0 ? '#f87171' : '#7a9bb5',
        arrow: dCmp > 0 ? '↑' : dCmp < 0 ? '↓' : '−',
        note: 'baseline = full-grid normalised; stage-1 = within-band normalised — not strictly apples-to-apples',
      });
    }
  }

  // Tag first cmpRow with "Core P&L" section
  if (cmpRows.length > 0) cmpRows[0].section = 'Core P&L';

  // ── Baseline reference data, split by reason it lacks a stage-1 equivalent ──
  const sx = (v: number | null | undefined) => v != null ? `$${(v * totalScale).toFixed(2)}` : '—';
  const noteMtmApprox = 'F14: linear approx (exit_frac × trigger + (1−exit_frac) × actual)';
  const noteEntryUnchanged = 'entry-time, unchanged';
  const noteNoHyp = 'F-tracked (F2–F5): backend doesn\'t aggregate hyp version yet';
  const notePathBlocked = 'no stage-1 equivalent: requires dual-exit modeling';
  const noteStage1Only = 'stage-1 specific (no baseline analog)';
  const noteRuleAmbig = 'stage-1 partial exit doesn\'t change rule firing — surviving portion still triggers original rule';

  // A. TRULY UNCHANGED — set at trade entry, before stage-1 can fire
  const baselineEntryOnly: [string, string][] = bm ? [
    ['Avg credit / margin', `${bm.avg_credit != null ? '$' + bm.avg_credit.toFixed(0) : '—'} / ${bm.avg_margin != null ? '$' + bm.avg_margin.toFixed(0) : '—'}`],
    ['Lots', String(bm.lots ?? lots ?? '—')],
  ] : [];

  // B. WOULD CHANGE — backend doesn't compute the hyp version yet (F-tracked: F2-F5).
  //    Listing these as BASELINE values for context; stage-1 versions are computable but missing.
  const baselineDerivable: [string, string][] = bm ? [
    ['Composite v2 (baseline)', bm.composite_score?.toFixed(4) ?? '—'],
    ['Avg win / Avg loss', `${sx(bm.avg_win_usd)} / ${sx(bm.avg_loss_usd)}`],
    ['Largest win', sx(bm.max_win_usd)],
    ['Ret / margin', bm.avg_pct_return_on_margin != null ? `${(bm.avg_pct_return_on_margin * 100).toFixed(2)}%` : '—'],
    ['Ret / credit', bm.avg_pct_return_on_credit != null ? `${(bm.avg_pct_return_on_credit * 100).toFixed(2)}%` : '—'],
    ['Hit % (rule)', bm.n_rule_trigger != null && bm.n_trades ? `${((bm.n_rule_trigger / bm.n_trades) * 100).toFixed(0)}%` : '—'],
    ['SL hits', String(bm.n_premium_sl_hit ?? '—')],
    ['Avg winner exit', bm.avg_winner_exit_offset_minutes != null ? `${Math.floor(bm.avg_winner_exit_offset_minutes/60)}h ${Math.round(bm.avg_winner_exit_offset_minutes%60)}m` : '—'],
    ['Avg loser exit', bm.avg_loser_exit_offset_minutes != null ? `${Math.floor(bm.avg_loser_exit_offset_minutes/60)}h ${Math.round(bm.avg_loser_exit_offset_minutes%60)}m` : '—'],
  ] : [];

  // ── F14: compute hyp MTM aggregates from bandTrades using linear approximation ──
  // hyp_min_mtm = exit_frac × trigger + (1-exit_frac) × actual_min_mtm  (for fired trades)
  // hyp_max_mtm = exit_frac × trigger + (1-exit_frac) × actual_max_mtm  (for fired trades)
  // Classify hyp-winner/hyp-loser by hyp_net_pnl.
  const mtmCmpRows: CmpRow[] = [];
  // Hyp aggregates derived from bandTrades — used to fill in client-side computable rows
  // for the P&L-splits section. Null until trades load.
  let hypAggs: {
    n_winners_hyp: number; n_losers_hyp: number;
    avg_win_hyp: number | null; avg_loss_hyp: number | null;
    max_win_hyp: number | null;
    avg_net_hyp: number | null;
    avg_net_winners_hyp: number | null;
    n_w_below_avg_min_hyp: number;
    n_l_above_avg_max_hyp: number;
    n_w_below_avg_min_base: number;
    n_l_above_avg_max_base: number;
  } | null = null;
  if (bandTrades && bandTrades.length > 0 && cell.trigger_mtm != null && bm) {
    const trig = cell.trigger_mtm;
    const ef = cell.exit_frac;
    const enr = bandTrades.map(t => {
      const minM = t.min_mtm_usd ?? 0;
      const maxM = t.max_mtm_usd ?? 0;
      const net = t.net_pnl_estimate_usd ?? 0;
      const fired = minM <= trig;
      const hyp_min = fired ? ef * trig + (1 - ef) * minM : minM;
      const hyp_max = fired ? ef * trig + (1 - ef) * maxM : maxM;
      const hyp_net = fired ? ef * trig + (1 - ef) * net : net;
      return { minM, maxM, net, hyp_min, hyp_max, hyp_net };
    });
    const winnersHyp = enr.filter(t => t.hyp_net >= 0);
    const losersHyp = enr.filter(t => t.hyp_net < 0);
    const winnersBase = enr.filter(t => t.net >= 0);
    const losersBase = enr.filter(t => t.net < 0);
    const mean = (arr: number[]) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
    const minOf = (arr: number[]) => arr.length ? Math.min(...arr) : null;
    const maxOf = (arr: number[]) => arr.length ? Math.max(...arr) : null;

    // Populate hypAggs for the P&L-splits section below
    const avgMinW_hyp = mean(winnersHyp.map(t => t.hyp_min)) ?? 0;
    const avgMaxL_hyp = mean(losersHyp.map(t => t.hyp_max)) ?? 0;
    const avgMinW_base = mean(winnersBase.map(t => t.minM)) ?? 0;
    const avgMaxL_base = mean(losersBase.map(t => t.maxM)) ?? 0;
    hypAggs = {
      n_winners_hyp: winnersHyp.length,
      n_losers_hyp: losersHyp.length,
      avg_win_hyp: mean(winnersHyp.map(t => t.hyp_net)),
      avg_loss_hyp: mean(losersHyp.map(t => t.hyp_net)),
      max_win_hyp: maxOf(winnersHyp.map(t => t.hyp_net)),
      avg_net_hyp: mean(enr.map(t => t.hyp_net)),
      avg_net_winners_hyp: mean(winnersHyp.map(t => t.hyp_net)),
      n_w_below_avg_min_hyp: winnersHyp.filter(t => t.hyp_min < avgMinW_hyp).length,
      n_l_above_avg_max_hyp: losersHyp.filter(t => t.hyp_max > avgMaxL_hyp).length,
      n_w_below_avg_min_base: winnersBase.filter(t => t.minM < avgMinW_base).length,
      n_l_above_avg_max_base: losersBase.filter(t => t.maxM > avgMaxL_base).length,
    };

    const mtmRow = (label: string, baseVal: number | null, stageVal: number | null, isPositiveBetter: boolean) => {
      if (baseVal == null || stageVal == null) return;
      const baseScaled = baseVal * totalScale;
      const stageScaled = stageVal * totalScale;
      const delta = stageScaled - baseScaled;
      const same = Math.abs(delta) < 1e-9;
      const better = isPositiveBetter ? delta > 0 : delta < 0;
      mtmCmpRows.push({
        label,
        baseline: `$${baseScaled.toFixed(2)}`,
        stage1: `$${stageScaled.toFixed(2)}`,
        delta: `${delta >= 0 ? '+' : ''}$${delta.toFixed(2)}`,
        deltaColor: same ? '#7a9bb5' : (better ? '#4ade80' : '#f87171'),
        arrow: same ? '−' : (delta > 0 ? '↑' : '↓'),
      });
    };

    // Winners side — positive Δ on max_mtm = bigger peak = better; positive Δ on min_mtm = less negative trough = better
    mtmRow('Avg max MTM (Winners)', mean(winnersBase.map(t => t.maxM)), mean(winnersHyp.map(t => t.hyp_max)), true);
    mtmRow('Avg min MTM (Winners)', mean(winnersBase.map(t => t.minM)), mean(winnersHyp.map(t => t.hyp_min)), true);
    mtmRow('Max MTM (Winners)',     maxOf(winnersBase.map(t => t.maxM)), maxOf(winnersHyp.map(t => t.hyp_max)), true);
    mtmRow('Min MTM (Winners)',     minOf(winnersBase.map(t => t.minM)), minOf(winnersHyp.map(t => t.hyp_min)), true);

    // Losers side
    mtmRow('Avg max MTM (Losers)', mean(losersBase.map(t => t.maxM)), mean(losersHyp.map(t => t.hyp_max)), true);
    mtmRow('Avg min MTM (Losers)', mean(losersBase.map(t => t.minM)), mean(losersHyp.map(t => t.hyp_min)), true);
    mtmRow('Max MTM (Losers)',     maxOf(losersBase.map(t => t.maxM)), maxOf(losersHyp.map(t => t.hyp_max)), true);
    mtmRow('Min MTM (Losers)',     minOf(losersBase.map(t => t.minM)), minOf(losersHyp.map(t => t.hyp_min)), true);

    // Largest loss MTM (most negative trough among losers)
    mtmRow('Largest loss MTM', minOf(losersBase.map(t => t.minM)), minOf(losersHyp.map(t => t.hyp_min)), true);
    // Largest win MTM (highest peak among winners)
    mtmRow('Largest win MTM', maxOf(winnersBase.map(t => t.maxM)), maxOf(winnersHyp.map(t => t.hyp_max)), true);
  }

  // ── Build UNIFIED row list: cmpRows + MTM + baseline-only + entry-time + stage-1-only ──
  const unifiedRows: CmpRow[] = [...cmpRows];

  // MTM trajectory (F14) — only if trades loaded
  if (mtmCmpRows.length > 0) {
    mtmCmpRows.forEach((r, i) => {
      unifiedRows.push({ ...r, section: i === 0 ? 'MTM trajectory (F14 linear approx)' : undefined, note: r.note ?? noteMtmApprox });
    });
  } else if (bm) {
    // bandTrades not yet loaded — show baseline only with placeholder note
    const mtmStub = (label: string, baseVal: number | null) => {
      unifiedRows.push({
        label, baseline: baseVal != null ? `$${(baseVal * totalScale).toFixed(2)}` : '—',
        stage1: '⋯ loading', delta: '—', arrow: '', deltaColor: '#7a9bb5',
        note: 'stage-1 hyp computes when trades load',
        section: label === 'Avg max MTM (Winners)' ? 'MTM trajectory (F14 — loading)' : undefined,
      });
    };
    mtmStub('Avg max MTM (Winners)', bm.avg_max_mtm_winners);
    mtmStub('Avg min MTM (Winners)', bm.avg_min_mtm_winners);
    mtmStub('Max MTM (Winners)', bm.max_mtm_winners);
    mtmStub('Min MTM (Winners)', bm.min_mtm_winners);
    mtmStub('Avg max MTM (Losers)', bm.avg_max_mtm_losers);
    mtmStub('Avg min MTM (Losers)', bm.avg_min_mtm_losers);
    mtmStub('Max MTM (Losers)', bm.max_mtm_losers);
    mtmStub('Min MTM (Losers)', bm.min_mtm_losers);
    mtmStub('Largest loss MTM', bm.largest_loss_mtm);
    mtmStub('Largest win MTM', bm.largest_win_mtm);
  }

  // Exit MTMs — baseline-only (no F14 equivalent; the "exit" under stage-1 is dual)
  if (bm) {
    const exitStub = (label: string, baseVal: number | null, isFirst = false) => {
      unifiedRows.push({
        label, baseline: baseVal != null ? `$${(baseVal * totalScale).toFixed(2)}` : '—',
        stage1: '—', delta: '—', arrow: '', deltaColor: '#7a9bb5',
        note: notePathBlocked,
        section: isFirst ? 'Exit MTMs (no stage-1 equivalent — dual-exit semantics)' : undefined,
      });
    };
    exitStub('Avg exit MTM (overall)', bm.avg_exit_mtm, true);
    exitStub('Avg exit MTM (Winners)', bm.avg_win_mtm);
    exitStub('Largest exit MTM (Winners)', bm.largest_win_mtm);
    exitStub('Total exit MTM (Winners)', bm.total_win_mtm);
    exitStub('Avg exit MTM (Losers)', bm.avg_loss_mtm);
    exitStub('Largest exit MTM (Losers)', bm.largest_loss_mtm);
    exitStub('Total exit MTM (Losers)', bm.total_loss_mtm);

    // W<avg min, L>avg max — counts. Hyp versions computed client-side from bandTrades (F14).
    const fmtArrowCount = (baseN: number, hypN: number | null, lowerIsBetter: boolean) => {
      if (hypN == null) return { delta: '—', arrow: '', color: '#7a9bb5' };
      const d = hypN - baseN;
      if (d === 0) return { delta: '+0', arrow: '−', color: '#7a9bb5' };
      const better = lowerIsBetter ? d < 0 : d > 0;
      return { delta: `${d > 0 ? '+' : ''}${d}`, arrow: d > 0 ? '↑' : '↓', color: better ? '#4ade80' : '#f87171' };
    };
    const baseWBelow = bm.n_winners_below_avg_min_mtm ?? 0;
    const baseLAbove = bm.n_losers_above_avg_max_mtm ?? 0;
    const wAr = fmtArrowCount(baseWBelow, hypAggs?.n_w_below_avg_min_hyp ?? null, true);  // fewer = better
    const lAr = fmtArrowCount(baseLAbove, hypAggs?.n_l_above_avg_max_hyp ?? null, true);  // fewer = better
    unifiedRows.push({
      label: 'W < avg min MTM',
      baseline: String(bm.n_winners_below_avg_min_mtm ?? '—'),
      stage1: hypAggs ? String(hypAggs.n_w_below_avg_min_hyp) : (bandTrades ? '⋯' : '—'),
      delta: wAr.delta, arrow: wAr.arrow, deltaColor: wAr.color,
      note: hypAggs ? 'F14: count using hyp_min_mtm vs hyp-winners\' avg' : 'loads when band trades load',
    });
    unifiedRows.push({
      label: 'L > avg max MTM',
      baseline: String(bm.n_losers_above_avg_max_mtm ?? '—'),
      stage1: hypAggs ? String(hypAggs.n_l_above_avg_max_hyp) : (bandTrades ? '⋯' : '—'),
      delta: lAr.delta, arrow: lAr.arrow, deltaColor: lAr.color,
      note: hypAggs ? 'F14: count using hyp_max_mtm vs hyp-losers\' avg' : 'loads when band trades load',
    });
    unifiedRows.push({
      label: 'Peak %',
      baseline: bm.avg_pct_max_mtm_on_credit != null ? `${(bm.avg_pct_max_mtm_on_credit * 100).toFixed(2)}%` : '—',
      stage1: '—', delta: '—', arrow: '', deltaColor: '#7a9bb5',
      note: 'F-tracked: needs per-trade entry_credit_usd in trades API (~30 min backend)',
    });
    unifiedRows.push({
      label: 'Trough %',
      baseline: bm.avg_pct_min_mtm_on_credit != null ? `${(bm.avg_pct_min_mtm_on_credit * 100).toFixed(2)}%` : '—',
      stage1: '—', delta: '—', arrow: '', deltaColor: '#7a9bb5',
      note: 'F-tracked: needs per-trade entry_credit_usd in trades API',
    });
  }

  // P&L splits — most now F14-computable from bandTrades
  if (bm) {
    // Helper: build a row with baseline → stage-1 → Δ when hyp value is available
    const pnlRow = (
      label: string,
      baseVal: number | null,
      hypVal: number | null,
      isPositiveBetter: boolean,
      isPct = false,
      isFirst = false,
    ) => {
      const baseStr = baseVal != null ? (isPct ? `${(baseVal * 100).toFixed(2)}%` : `$${(baseVal * totalScale).toFixed(2)}`) : '—';
      if (hypVal == null) {
        unifiedRows.push({
          label, baseline: baseStr, stage1: bandTrades ? '⋯' : '—',
          delta: '—', arrow: '', deltaColor: '#7a9bb5',
          note: bandTrades ? 'computing…' : 'F14: stage-1 hyp from band trades (loads on cell expand)',
          section: isFirst ? 'P&L splits · F14 hyp (computed from band trades)' : undefined,
        });
        return;
      }
      const baseScaled = isPct ? baseVal! * 100 : baseVal! * totalScale;
      const hypScaled = isPct ? hypVal * 100 : hypVal * totalScale;
      const delta = hypScaled - baseScaled;
      const same = Math.abs(delta) < 1e-6;
      const better = isPositiveBetter ? delta > 0 : delta < 0;
      const stageStr = isPct ? `${hypScaled.toFixed(2)}%` : `$${hypScaled.toFixed(2)}`;
      const deltaStr = isPct
        ? `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}%`
        : `${delta >= 0 ? '+' : ''}$${delta.toFixed(2)}`;
      unifiedRows.push({
        label, baseline: baseStr, stage1: stageStr,
        delta: deltaStr,
        arrow: same ? '−' : (delta > 0 ? '↑' : '↓'),
        deltaColor: same ? '#7a9bb5' : (better ? '#4ade80' : '#f87171'),
        note: 'F14: hyp values from bandTrades (linear approx)',
        section: isFirst ? 'P&L splits · F14 hyp (computed from band trades)' : undefined,
      });
    };

    pnlRow('Avg win (per trade)', bm.avg_win_usd, hypAggs?.avg_win_hyp ?? null, true, false, true);
    pnlRow('Avg loss (per trade)', bm.avg_loss_usd, hypAggs?.avg_loss_hyp ?? null, true);  // less-negative=better still positive-direction
    pnlRow('Largest win', bm.max_win_usd, hypAggs?.max_win_hyp ?? null, true);

    // Ret/margin and Ret/credit — use band-level denominators (per-trade not exposed)
    const margin = bm.avg_margin ?? null;
    const credit = bm.avg_credit ?? null;
    const retMarginHyp = margin && hypAggs?.avg_net_hyp != null ? (hypAggs.avg_net_hyp * totalScale) / margin : null;
    const retCreditHyp = credit && hypAggs?.avg_net_hyp != null ? (hypAggs.avg_net_hyp * totalScale) / credit : null;
    const retMarginWHyp = margin && hypAggs?.avg_net_winners_hyp != null ? (hypAggs.avg_net_winners_hyp * totalScale) / margin : null;
    const retCreditWHyp = credit && hypAggs?.avg_net_winners_hyp != null ? (hypAggs.avg_net_winners_hyp * totalScale) / credit : null;

    pnlRow('Ret / margin', bm.avg_pct_return_on_margin, retMarginHyp, true, true);
    pnlRow('Ret / credit', bm.avg_pct_return_on_credit, retCreditHyp, true, true);
    pnlRow('Ret / margin (W)', bm.avg_pct_return_on_margin_winners, retMarginWHyp, true, true);
    pnlRow('Ret / credit (W)', bm.avg_pct_return_on_credit_winners, retCreditWHyp, true, true);
  }

  // Rule firing stats — baseline only (rule still fires for surviving portion; semantics ambiguous)
  if (bm) {
    const ruleStub = (label: string, val: string, isFirst = false) =>
      unifiedRows.push({
        label, baseline: val, stage1: '—', delta: '—', arrow: '', deltaColor: '#7a9bb5',
        note: noteRuleAmbig,
        section: isFirst ? 'Rule firing (no clean stage-1 equivalent)' : undefined,
      });
    ruleStub('Hit % (rule)', bm.n_rule_trigger != null && bm.n_trades ? `${((bm.n_rule_trigger / bm.n_trades) * 100).toFixed(0)}%` : '—', true);
    ruleStub('Rule hits', String(bm.n_rule_trigger ?? '—'));
    ruleStub('SL hits', String(bm.n_premium_sl_hit ?? '—'));
    ruleStub('Hard cap', String(bm.n_hard_cap ?? '—'));
    ruleStub('Max winning streak', String(bm.max_consec_wins ?? '—'));
    ruleStub('Max losing streak', String(bm.max_consec_losses ?? '—'));
  }

  // Exit timing — baseline only
  if (bm) {
    const timing = (m: number | null | undefined) => m != null ? `${Math.floor(m/60)}h ${Math.round(m%60)}m` : '—';
    unifiedRows.push({
      section: 'Exit timing (no stage-1 equivalent — dual exit)',
      label: 'Avg exit time',
      baseline: timing(bm.avg_exit_offset_minutes), stage1: '—', delta: '—', arrow: '', deltaColor: '#7a9bb5',
      note: notePathBlocked,
    });
    unifiedRows.push({ label: 'Avg winner exit', baseline: timing(bm.avg_winner_exit_offset_minutes), stage1: '—', delta: '—', arrow: '', deltaColor: '#7a9bb5', note: notePathBlocked });
    unifiedRows.push({ label: 'Avg loser exit', baseline: timing(bm.avg_loser_exit_offset_minutes), stage1: '—', delta: '—', arrow: '', deltaColor: '#7a9bb5', note: notePathBlocked });
  }

  // Entry-time values (truly unchanged: same in both columns, Δ = 0)
  if (bm) {
    const entryRow = (label: string, val: string, isFirst = false) =>
      unifiedRows.push({
        label, baseline: val, stage1: val, delta: '0', arrow: '−', deltaColor: '#7a9bb5',
        note: noteEntryUnchanged,
        section: isFirst ? 'Entry-time values (truly unchanged)' : undefined,
      });
    entryRow('Avg credit', bm.avg_credit != null ? `$${bm.avg_credit.toFixed(2)}` : '—', true);
    entryRow('Avg margin', bm.avg_margin != null ? `$${bm.avg_margin.toFixed(2)}` : '—');
    entryRow('Lots', String(bm.lots ?? lots ?? '—'));
  }

  // Stage-1 cell-specific config + case breakdown + magnitudes + delta outcomes
  // (no baseline column — these only exist under stage-1)
  unifiedRows.push({
    section: 'Stage-1 cell config (no baseline analog)',
    label: 'Trigger level', baseline: '—', stage1: TRIGGER_LABELS[cell.trigger_level] ?? cell.trigger_level,
    delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only,
  });
  unifiedRows.push({ label: 'Exit frac', baseline: '—', stage1: `${(cell.exit_frac * 100).toFixed(0)}%`, delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });
  unifiedRows.push({ label: 'Trigger MTM', baseline: '—', stage1: triggerStr, delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });
  unifiedRows.push({ label: 'Status', baseline: '—', stage1: cell.status, delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });

  unifiedRows.push({
    section: 'Stage-1 trade classification (no baseline analog)',
    label: 'n_A (no fire)', baseline: '—', stage1: String(nA), delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only,
  });
  unifiedRows.push({ label: 'n_B_reliable (winner dipped)', baseline: '—', stage1: String(nBr), delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });
  unifiedRows.push({ label: 'n_B_unreliable (peak before trough)', baseline: '—', stage1: String(nBu), delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });
  unifiedRows.push({ label: 'n_C (fires on losers)', baseline: '—', stage1: String(nC), delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });
  unifiedRows.push({ label: 'n_C_deeper (saved)', baseline: '—', stage1: String(nCd), delta: '—', arrow: '', deltaColor: '#4ade80', note: noteStage1Only });
  unifiedRows.push({ label: 'n_C_recovered (hurt)', baseline: '—', stage1: String(nCr), delta: '—', arrow: '', deltaColor: '#f87171', note: noteStage1Only });
  unifiedRows.push({ label: 'C_recovered share', baseline: '—', stage1: fmtPct(cell.c_recovered_share), delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });

  unifiedRows.push({
    section: 'Stage-1 magnitudes (no baseline analog)',
    label: 'avg saved/trade (C_deeper)', baseline: '—', stage1: savedStr, delta: '—', arrow: '', deltaColor: '#4ade80', note: noteStage1Only,
  });
  unifiedRows.push({ label: 'avg given-up/trade (B_reliable)', baseline: '—', stage1: fmt$(sc(cell.avg_given_up), 1), delta: '—', arrow: '', deltaColor: '#f87171', note: noteStage1Only });
  unifiedRows.push({ label: 'avg hurt/trade (C_recovered)', baseline: '—', stage1: hurtStr, delta: '—', arrow: '', deltaColor: '#f87171', note: noteStage1Only });
  unifiedRows.push({ label: 'pct_B_unreliable', baseline: '—', stage1: fmtPct(cell.pct_B_unreliable), delta: '—', arrow: '', deltaColor: '#7a9bb5', note: noteStage1Only });

  // Stage-1 outcomes (delta + hyp)
  const stageOutcome = (label: string, val: string, delta: string, color: string, arrow: string, isFirst = false) =>
    unifiedRows.push({
      label, baseline: '—', stage1: val, delta, arrow, deltaColor: color, note: 'stage-1 outcome metric',
      section: isFirst ? 'Stage-1 outcomes (Δ from baseline)' : undefined,
    });
  const evScaled = sc(cell.ev_per_trade) as number | null;
  stageOutcome('EV/trade', evScaled != null ? fmt$(evScaled, 1) : '—', evScaled != null ? fmt$(evScaled, 1) : '—',
    (evScaled ?? 0) > 0 ? '#4ade80' : (evScaled ?? 0) < 0 ? '#f87171' : '#7a9bb5',
    (evScaled ?? 0) > 0 ? '↑' : (evScaled ?? 0) < 0 ? '↓' : '−', true);
  const dCvar = sc(cell.delta_cvar_95) as number | null;
  stageOutcome('Δ CVaR-95', dCvar != null ? fmt$(dCvar, 1) : '—', dCvar != null ? fmt$(dCvar, 1) : '—',
    (dCvar ?? 0) > 0 ? '#4ade80' : (dCvar ?? 0) < 0 ? '#f87171' : '#7a9bb5',
    (dCvar ?? 0) > 0 ? '↑' : (dCvar ?? 0) < 0 ? '↓' : '−');

  // C. WOULD CHANGE — but stage-1 version requires path-walking (X1 blocker)
  //    The surviving (1-exit_frac) portion has its OWN MTM trajectory after trigger.
  //    Listed as BASELINE only; cannot compute hyp without per-minute leg price walking.
  const baselineMtmTrajectory: [string, string][] = bm ? [
    ['Avg max MTM (Winners)', sx(bm.avg_max_mtm_winners)],
    ['Avg min MTM (Winners)', sx(bm.avg_min_mtm_winners)],
    ['Max MTM (Winners)', sx(bm.max_mtm_winners)],
    ['Min MTM (Winners)', sx(bm.min_mtm_winners)],
    ['Avg max MTM (Losers)', sx(bm.avg_max_mtm_losers)],
    ['Avg min MTM (Losers)', sx(bm.avg_min_mtm_losers)],
    ['Max MTM (Losers)', sx(bm.max_mtm_losers)],
    ['Min MTM (Losers)', sx(bm.min_mtm_losers)],
    ['Avg exit MTM', sx(bm.avg_exit_mtm)],
    ['Avg win MTM', sx(bm.avg_win_mtm)],
    ['Largest win MTM', sx(bm.largest_win_mtm)],
    ['Avg loss MTM', sx(bm.avg_loss_mtm)],
    ['Largest loss MTM', sx(bm.largest_loss_mtm)],
    ['W < avg min MTM', String(bm.n_winners_below_avg_min_mtm ?? '—')],
    ['L > avg max MTM', String(bm.n_losers_above_avg_max_mtm ?? '—')],
    ['Peak %', bm.avg_pct_max_mtm_on_credit != null ? `${(bm.avg_pct_max_mtm_on_credit * 100).toFixed(2)}%` : '—'],
    ['Trough %', bm.avg_pct_min_mtm_on_credit != null ? `${(bm.avg_pct_min_mtm_on_credit * 100).toFixed(2)}%` : '—'],
  ] : [];

  // Group stage-1 rows by section
  const sections = new Map<string, RowDef[]>();
  let currentSection = 'Setup';
  for (const r of rows) {
    if (r.section) currentSection = r.section;
    if (!sections.has(currentSection)) sections.set(currentSection, []);
    sections.get(currentSection)!.push(r);
  }

  const sectionHeader = (text: string, color = '#1f6feb'): React.ReactNode => (
    <div style={{
      gridColumn: '1 / -1', marginTop: 6, paddingBottom: 3, marginBottom: 4,
      borderBottom: `1px solid ${color}44`,
      color, fontWeight: 700, fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase',
    }}>{text}</div>
  );

  const compactItem = (
    label: string,
    value: string,
    arrow?: { arrow: string; color: string },
    context?: string,
  ): React.ReactNode => (
    <div style={{ display: 'flex', flexDirection: 'column', padding: '2px 0', gap: 1 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ color: '#7a9bb5', fontSize: 10 }}>{label}:</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{
            color: arrow?.color ?? '#cfd9e3',
            fontWeight: 600, fontSize: 11, fontFamily: 'monospace', textAlign: 'right',
          }}>{value}</span>
          {arrow?.arrow && (
            <span style={{ color: arrow.color, fontSize: 13, fontWeight: 700, lineHeight: 1 }}>
              {arrow.arrow}
            </span>
          )}
        </span>
      </div>
      {context && (
        <span style={{ color: '#4a5d6e', fontSize: 9, textAlign: 'right', fontFamily: 'monospace' }}>
          {context}
        </span>
      )}
    </div>
  );

  // ── Build arrow map for P&L impact metrics (Block 2 highlighting) ─────────
  // sgn returns up/down arrow + green/red color. For metrics where "lower is better",
  // pass isBetterUp=false (e.g. consec losses).
  const sgn = (v: number | null | undefined, isBetterUp = true): { arrow: string; color: string } => {
    if (v == null || isNaN(v)) return { arrow: '', color: '#cfd9e3' };
    if (Math.abs(v) < 1e-9) return { arrow: '−', color: '#7a9bb5' };
    const better = isBetterUp ? v > 0 : v < 0;
    return { arrow: v > 0 ? '↑' : '↓', color: better ? '#4ade80' : '#f87171' };
  };
  const arrowMap: Record<string, { arrow: string; color: string }> = {
    'EV/trade':            sgn(cell.ev_per_trade, true),
    'Δ avg P&L':           sgn(cell.delta_avg_pnl, true),
    'Δ win rate':          sgn(cell.delta_win_rate, true),
    'Δ CVaR-95':           sgn(cell.delta_cvar_95, true),
    'Δ max loss':          sgn(cell.delta_max_loss, true),
    'Δ max consec losses': sgn(cell.delta_max_consec_losses, false),  // lower is better
  };
  // Hyp metrics vs baseline (best-combo) — change in hyp from baseline
  if (bmAvg != null && derivedHypAvg != null) {
    arrowMap['Hyp avg P&L'] = sgn(derivedHypAvg - bmAvg, true);
  }
  if (bmWR != null && derivedHypWinRate != null) {
    arrowMap['Hyp win rate'] = sgn(derivedHypWinRate - bmWR, true);
  }
  if (bm?.composite_score != null && cell.composite_score_v2 != null) {
    arrowMap['Composite v2 (hyp)'] = sgn(cell.composite_score_v2 - bm.composite_score, true);
  }

  // ── Build "baseline → stage-1" context strings for P&L impact metrics ──
  // Shows the actual before-and-after values so the delta makes sense.
  const ctxMap: Record<string, string> = {};
  const baseAvgScaled = bmAvg != null ? bmAvg * totalScale : null;
  if (baseAvgScaled != null && derivedHypAvg != null) {
    const hypScaled = sc(derivedHypAvg) as number;
    ctxMap['EV/trade'] = `${fmt$(baseAvgScaled, 1)} → ${fmt$(hypScaled, 1)}`;
    ctxMap['Δ avg P&L'] = `${fmt$(baseAvgScaled, 1)} → ${fmt$(hypScaled, 1)}`;
    ctxMap['Hyp avg P&L'] = `baseline ${fmt$(baseAvgScaled, 1)} · Δ ${fmt$(sc(cell.delta_avg_pnl), 1)}`;
  }
  if (bmWR != null && derivedHypWinRate != null) {
    ctxMap['Δ win rate'] = `${fmtPct(bmWR)} → ${fmtPct(derivedHypWinRate)}`;
    ctxMap['Hyp win rate'] = `baseline ${fmtPct(bmWR)} · Δ ${fmtPct(cell.delta_win_rate)}`;
  }
  if (bm?.max_loss_usd != null && cell.delta_max_loss != null) {
    const baseML = bm.max_loss_usd * totalScale;
    const stageML = baseML + (cell.delta_max_loss * lotsScale);
    ctxMap['Δ max loss'] = `${fmt$(baseML, 1)} → ${fmt$(stageML, 1)}`;
  }
  if (bm?.max_consec_losses != null && cell.delta_max_consec_losses != null) {
    const stageMCL = bm.max_consec_losses + cell.delta_max_consec_losses;
    ctxMap['Δ max consec losses'] = `${bm.max_consec_losses} → ${stageMCL}`;
  }
  // For CVaR-95 we don't have a direct baseline cvar in best-combo; show delta only
  if (bm?.composite_score != null && cell.composite_score_v2 != null) {
    ctxMap['Composite v2 (hyp)'] = `baseline ${bm.composite_score.toFixed(4)} · Δ ${(cell.composite_score_v2 - bm.composite_score >= 0 ? '+' : '')}${(cell.composite_score_v2 - bm.composite_score).toFixed(4)}`;
  }

  return (
    <div style={{
      marginTop: 8, background: '#0d1421', borderRadius: 4, padding: 12, fontSize: 11,
    }}>
      {/* ── UNIFIED CONTINUOUS COMPARISON TABLE ─────────────────────────────────
            Single table with section header rows. Replaces Blocks 1A-1D and 2.
            Rows where stage-1 has no equivalent show "—" with a Note column. */}
      <div style={{
        color: '#fbbf24', fontWeight: 700, fontSize: 11, letterSpacing: 0.5,
        textTransform: 'uppercase', paddingBottom: 4, borderBottom: '1px solid #fbbf2444',
        marginBottom: 8,
      }}>
        Continuous comparison · Baseline → Stage-1 ({(cell.exit_frac * 100).toFixed(0)}% @ {TRIGGER_LABELS[cell.trigger_level] ?? cell.trigger_level})
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 14, fontSize: 11 }}>
        <thead style={{ position: 'sticky', top: 0, background: '#0d1421' }}>
          <tr>
            <th style={{ textAlign: 'left', color: '#7a9bb5', padding: '4px 8px', fontWeight: 500, fontSize: 10 }}>Metric</th>
            <th style={{ textAlign: 'right', color: '#7a9bb5', padding: '4px 8px', fontWeight: 500, fontSize: 10 }}>Baseline</th>
            <th style={{ textAlign: 'right', color: '#4ade80', padding: '4px 8px', fontWeight: 500, fontSize: 10 }}>Stage-1</th>
            <th style={{ textAlign: 'right', color: '#7a9bb5', padding: '4px 8px', fontWeight: 500, fontSize: 10 }}>Δ</th>
            <th style={{ textAlign: 'left', color: '#7a9bb5', padding: '4px 8px', fontWeight: 500, fontSize: 10, width: '32%' }}>Note / Status</th>
          </tr>
        </thead>
        <tbody>
          {unifiedRows.map((r, i) => (
            <React.Fragment key={i}>
              {r.section && (
                <tr>
                  <td colSpan={5} style={{
                    padding: '8px 8px 3px', color: '#fbbf24', fontWeight: 700,
                    fontSize: 10, letterSpacing: 0.5, textTransform: 'uppercase',
                    borderTop: i > 0 ? '2px solid #1a2d42' : undefined,
                    background: '#0a1019',
                  }}>{r.section}</td>
                </tr>
              )}
              <tr style={{ borderTop: '1px solid #14202e' }}>
                <td style={{ padding: '3px 8px', color: '#cfd9e3', fontSize: 11 }}>{r.label}</td>
                <td style={{ padding: '3px 8px', textAlign: 'right', color: '#cfd9e3', fontFamily: 'monospace', fontSize: 11 }}>{r.baseline}</td>
                <td style={{ padding: '3px 8px', textAlign: 'right', color: '#cfd9e3', fontFamily: 'monospace', fontSize: 11, fontWeight: 600 }}>{r.stage1}</td>
                <td style={{ padding: '3px 8px', textAlign: 'right', color: r.deltaColor, fontFamily: 'monospace', fontSize: 11, fontWeight: 700 }}>
                  {r.delta} {r.arrow && <span style={{ fontSize: 12 }}>{r.arrow}</span>}
                </td>
                <td style={{ padding: '3px 8px', color: '#6a8294', fontSize: 9, fontStyle: 'italic', lineHeight: 1.3 }}>
                  {r.note ?? ''}
                </td>
              </tr>
            </React.Fragment>
          ))}
        </tbody>
      </table>

      {/* ── BLOCK 3: What each metric means ─────────────────────────────────────── */}
      <div style={{
        color: '#60a5fa', fontWeight: 700, fontSize: 11, letterSpacing: 0.5,
        textTransform: 'uppercase', paddingBottom: 4, borderBottom: '1px solid #60a5fa44',
        marginBottom: 8,
      }}>
        What each metric means
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {rows.map((r, i) => (
          <div key={`def-${i}`} style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 12 }}>
            <span style={{ color: '#7a9bb5', fontSize: 10, fontFamily: 'monospace', paddingTop: 2 }}>
              {r.label}
            </span>
            <span style={{ color: '#8fa8c0', fontSize: 10, lineHeight: 1.5 }}>
              {r.explain}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── VerdictSummary ───────────────────────────────────────────────────────────

function VerdictSummary({ result }: { result: Stage1Result }) {
  const best = result.best_cells ?? [];
  const nWorthIt = best.filter(b => b.band_verdict === 'WORTH_IT').length;
  const nTighterSl = best.filter(b => b.band_verdict === 'SKIP_TIGHTER_SL_WINS').length;
  const nNegative = best.filter(b => b.band_verdict === 'SKIP_NEGATIVE').length;
  const nInsufficient = best.filter(b => b.band_verdict === 'SKIP_INSUFFICIENT').length;
  const nMarginal = best.filter(b => b.band_verdict === 'MARGINAL').length;
  const nBands = best.length;

  // Find globally best combo (present in ≥50% of bands)
  const comboCount: Record<string, number> = {};
  for (const b of best) {
    if (b.best_avg_pnl_exit_frac != null && b.best_avg_pnl_trigger_level) {
      const k = `${(b.best_avg_pnl_exit_frac * 100).toFixed(0)}% @ ${TRIGGER_LABELS[b.best_avg_pnl_trigger_level] ?? b.best_avg_pnl_trigger_level}`;
      comboCount[k] = (comboCount[k] ?? 0) + 1;
    }
  }
  const globalBest = Object.entries(comboCount).find(([, n]) => n >= nBands / 2);

  return (
    <div style={{
      background: '#0d1421', borderRadius: 6, padding: '10px 14px', marginBottom: 10,
      display: 'flex', gap: 20, flexWrap: 'wrap', fontSize: 12,
    }}>
      <span>
        <span style={{ color: '#4ade80', fontWeight: 700 }}>{nWorthIt}</span>
        <span style={{ color: '#7a9bb5' }}>/{nBands} bands worth it</span>
      </span>
      {nTighterSl > 0 && (
        <span>
          <span style={{ color: '#60a5fa', fontWeight: 700 }}>{nTighterSl}</span>
          <span style={{ color: '#7a9bb5' }}> tighter-SL wins</span>
        </span>
      )}
      {nNegative > 0 && (
        <span>
          <span style={{ color: '#f87171', fontWeight: 700 }}>{nNegative}</span>
          <span style={{ color: '#7a9bb5' }}> stage-1 hurts</span>
        </span>
      )}
      {nMarginal > 0 && (
        <span>
          <span style={{ color: '#7a9bb5', fontWeight: 700 }}>{nMarginal}</span>
          <span style={{ color: '#7a9bb5' }}> marginal</span>
        </span>
      )}
      {nInsufficient > 0 && (
        <span>
          <span style={{ color: '#555', fontWeight: 700 }}>{nInsufficient}</span>
          <span style={{ color: '#555' }}> insufficient</span>
        </span>
      )}
      {globalBest && (
        <span style={{ marginLeft: 'auto', color: '#fbbf24', fontWeight: 700 }}>
          ★ {globalBest[0]} wins ≥50% of bands
        </span>
      )}
    </div>
  );
}

// ─── Progress bar ─────────────────────────────────────────────────────────────

function ProgressBar({ progress }: { progress: number }) {
  return (
    <div style={{ background: '#1a2d42', borderRadius: 4, height: 6, margin: '8px 0' }}>
      <div style={{
        width: `${Math.round(progress * 100)}%`, height: '100%',
        background: '#1f6feb', borderRadius: 4, transition: 'width 0.3s',
      }} />
    </div>
  );
}

// ─── Main Panel ───────────────────────────────────────────────────────────────

export function M7Stage1Panel({
  resolvedRules,
  dataset,
}: Props) {
  const [collapsed, setCollapsed] = usePersistedState<boolean>(
    'm7:stage1:collapsed', true);
  const [resp, setResp] = useState<Stage1Response | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [precheck, setPrecheck] = useState<Stage1PrecheckResponse | null>(null);
  const [computing, setComputing] = useState(false);
  const [showFilter, setShowFilter] = usePersistedState<boolean>(
    'm7:stage1:showFilter', false);
  // 'all' = cross-band modal; band string = per-band modal; null = closed
  const [tradesModal, setTradesModal] = useState<'all' | string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const rulesKey = JSON.stringify([resolvedRules, dataset]);
  const nBands = Object.keys(resolvedRules).length;

  // Whenever resolved rules change, reset result and fetch precheck (fast, <100ms).
  useEffect(() => {
    setResp(null);
    setErr(null);
    setPrecheck(null);
    if (nBands === 0) return;
    const ac = new AbortController();
    fetchM7Stage1Precheck(resolvedRules, dataset, ac.signal)
      .then(p => setPrecheck(p))
      .catch(() => {}); // best-effort; precheck failure doesn't block compute
    return () => ac.abort();
  }, [rulesKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  const handleCompute = useCallback(() => {
    if (nBands === 0) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setErr(null);
    setComputing(true);
    setCollapsed(false);

    const poll = () => {
      fetchM7Stage1(resolvedRules, dataset, ac.signal)
        .then(r => {
          if (ac.signal.aborted) return;
          setResp(r);
          if (r.status === 'warming') {
            window.setTimeout(poll, 2000);
          } else {
            setComputing(false);
          }
        })
        .catch(e => {
          if (ac.signal.aborted) return;
          setErr(String(e));
          setComputing(false);
        });
    };
    poll();
  }, [rulesKey, nBands]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);

  // Organise result by band
  const { cellsByBand, bestByBand, summaryByBand } = useMemo(() => {
    const result = resp?.result;
    if (!result) return { cellsByBand: {}, bestByBand: {}, summaryByBand: {} };
    const cbMap: Record<string, Stage1Cell[]> = {};
    const bbMap: Record<string, Stage1BestCell> = {};
    const sbMap: Record<string, Stage1BandSummary> = {};
    for (const c of result.all_cells ?? []) {
      cbMap[c.band] = cbMap[c.band] ?? [];
      cbMap[c.band].push(c);
    }
    for (const b of result.best_cells ?? []) bbMap[b.band] = b;
    for (const s of result.band_summaries ?? []) sbMap[s.band] = s;
    return { cellsByBand: cbMap, bestByBand: bbMap, summaryByBand: sbMap };
  }, [resp]);

  const bands = useMemo(() => {
    const seen = new Set<string>();
    for (const c of resp?.result?.all_cells ?? []) seen.add(c.band);
    return [...seen].sort();
  }, [resp]);

  const sectionStyle: React.CSSProperties = {
    background: '#080c14', border: '1px solid #1a2d42', borderRadius: 6,
    padding: 12, marginBottom: 10,
  };

  return (
    <div style={sectionStyle}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <button
          onClick={() => setCollapsed(c => !c)}
          style={{
            background: 'none', border: 'none', color: '#cfd9e3',
            cursor: 'pointer', fontSize: 13, fontWeight: 700, padding: 0,
          }}
        >
          {collapsed ? '▶' : '▼'} Stage-1 Partial Exit Sweep
        </button>

        <button
          onClick={handleCompute}
          disabled={nBands === 0 || computing}
          style={{
            background: nBands === 0 ? '#1a2d42' : '#1f6feb',
            color: nBands === 0 ? '#555' : '#fff',
            border: 'none', borderRadius: 4, padding: '4px 12px',
            fontSize: 11, fontWeight: 600, cursor: nBands === 0 ? 'default' : 'pointer',
          }}
        >
          {computing ? 'Computing…' : 'Compute'}
        </button>

        <button
          onClick={() => setShowFilter(f => !f)}
          style={{
            background: '#0d1421', color: '#7a9bb5',
            border: '1px solid #1a2d42', borderRadius: 4,
            padding: '4px 8px', fontSize: 10, cursor: 'pointer',
          }}
        >
          {showFilter ? 'Hide filters' : `${nBands} band${nBands !== 1 ? 's' : ''}`}
        </button>

        {resp?.status === 'warming' && (
          <span style={{ fontSize: 11, color: '#fbbf24' }}>
            Warming… {Math.round((resp.progress ?? 0) * 100)}%
          </span>
        )}
        {resp?.status === 'ready' && (
          <>
            <span style={{ fontSize: 11, color: '#4ade80' }}>Ready</span>
            <button
              onClick={() => setTradesModal('all')}
              style={{
                background: '#0d1421', color: '#7a9bb5',
                border: '1px solid #1a2d42', borderRadius: 4,
                padding: '4px 10px', fontSize: 10, cursor: 'pointer',
              }}
            >
              View all trades
            </button>
          </>
        )}
        {nBands === 0 && (
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>
            Run best-combo picker to populate bands
          </span>
        )}
      </div>

      {/* Filter summary */}
      {showFilter && (
        <FilterSummary rules={resolvedRules} dataset={dataset} precheck={precheck} />
      )}

      {/* Progress */}
      {computing && resp?.status === 'warming' && (
        <ProgressBar progress={resp.progress ?? 0} />
      )}

      {/* Error */}
      {err && (
        <div style={{ color: '#f87171', fontSize: 12, marginTop: 8 }}>{err}</div>
      )}

      {/* Results */}
      {!collapsed && resp?.status === 'ready' && resp.result && (
        <>
          <VerdictSummary result={resp.result} />
          {bands.map(band => {
            const rr = resolvedRules[band];
            return (
              <Stage1BandCard
                key={band}
                band={band}
                cells={cellsByBand[band] ?? []}
                bestCell={bestByBand[band] ?? null}
                summary={summaryByBand[band] ?? null}
                lots={rr?.lots ?? 100}
                baselineMetrics={rr?.baseline_metrics ?? null}
                ruleDict={rr?.rule_dict as Record<string, unknown> | undefined}
                ruleFilters={{
                  expiry_bucket: rr?.expiry_bucket ?? undefined,
                  delta_target: rr?.delta_target ?? undefined,
                  entry_hour_ist: rr?.entry_hour_ist ?? undefined,
                }}
                dataset={dataset}
                onViewTrades={() => setTradesModal(band)}
              />
            );
          })}
          {resp.result.caveats.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 10, color: '#7a9bb5' }}>
              <strong>Caveats:</strong>
              <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                {resp.result.caveats.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}
        </>
      )}

      {/* Cross-band all-trades modal */}
      {tradesModal === 'all' && (
        <Stage1AllTradesModal
          resolvedRules={resolvedRules}
          dataset={dataset}
          onClose={() => setTradesModal(null)}
        />
      )}

      {/* Per-band trades modal */}
      {tradesModal && tradesModal !== 'all' && resolvedRules[tradesModal] && (
        <Stage1BandTradesModal
          band={tradesModal}
          ruleDict={resolvedRules[tradesModal].rule_dict as Record<string, unknown>}
          ruleLabel={resolvedRules[tradesModal].rule_label}
          filters={{
            expiry_bucket: resolvedRules[tradesModal].expiry_bucket ?? undefined,
            delta_target: resolvedRules[tradesModal].delta_target ?? undefined,
            entry_hour_ist: resolvedRules[tradesModal].entry_hour_ist ?? undefined,
          }}
          dataset={dataset}
          lots={resolvedRules[tradesModal].lots ?? 100}
          onClose={() => setTradesModal(null)}
        />
      )}
    </div>
  );
}
