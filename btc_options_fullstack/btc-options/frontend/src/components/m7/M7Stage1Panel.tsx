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
  onClose,
}: {
  band: string;
  ruleDict: Record<string, unknown>;
  ruleLabel: string;
  filters: { expiry_bucket?: string; delta_target?: number; entry_hour_ist?: number };
  dataset: M7Dataset;
  onClose: () => void;
}) {
  const [data, setData] = useState<Stage1BandTradesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  // User controls
  const [triggerChoice, setTriggerChoice] = useState<'sl_avg' | 'l_avg' | 'w_avg' | 'custom'>('l_avg');
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
  const tlLabel = (v: number | null) => v != null ? `$${v.toFixed(0)}` : '—';

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
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <span style={{ color: '#7a9bb5' }}>Trigger:</span>
            {(['sl_avg', 'l_avg', 'w_avg'] as const).map(k => {
              const val = tl?.[k];
              const labels = { sl_avg: `SL-avg (${tlLabel(val ?? null)})`, l_avg: `L-avg (${tlLabel(val ?? null)})`, w_avg: `W-avg (${tlLabel(val ?? null)})` };
              return (
                <button key={k} onClick={() => setTriggerChoice(k)} style={{
                  background: triggerChoice === k ? '#1f6feb' : '#0d1421',
                  color: triggerChoice === k ? '#fff' : '#7a9bb5',
                  border: '1px solid #1a2d42', borderRadius: 3,
                  padding: '2px 7px', fontSize: 10, cursor: 'pointer',
                }}>
                  {labels[k]}
                </button>
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
              MTM ≤ {triggerMtm.toFixed(0)}
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
                  <th style={thStyle}>Trigger fires?</th>
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
                      <td style={{ ...tdStyle, color: '#f87171', fontWeight: 600 }}>{t.min_mtm_usd != null ? `$${t.min_mtm_usd.toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: '#4ade80' }}>{t.max_mtm_usd != null ? `$${t.max_mtm_usd.toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: netColor, fontWeight: 600 }}>{t.net_pnl_estimate_usd != null ? fmt$(t.net_pnl_estimate_usd, 0) : '—'}</td>
                      <td style={{ ...tdStyle, color: t.is_premium_sl_hit ? '#f87171' : '#556' }}>{t.is_premium_sl_hit ? 'SL' : '—'}</td>
                      <td style={{ ...tdStyle, color: CASE_COLOR[t.caseTag], fontFamily: 'monospace', fontSize: 10, fontWeight: 700 }}>{t.caseTag}</td>
                      <td style={{ ...tdStyle, color: fired ? '#fbbf24' : '#556', textAlign: 'center' }}>{fired ? '✓' : '—'}</td>
                      <td style={{ ...tdStyle, color: netColor }}>{fmt$(t.hypothetical, 0)}</td>
                      <td style={{ ...tdStyle, color: deltaColor, fontWeight: 700 }}>{fmt$(t.delta, 0)}</td>
                    </tr>
                  );
                })}
                {sorted.length === 0 && (
                  <tr><td colSpan={12} style={{ padding: 20, color: '#556', textAlign: 'center' }}>No trades match filter</td></tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer legend */}
        <div style={{ padding: '6px 14px', borderTop: '1px solid #1a2d42', fontSize: 10, color: '#556', display: 'flex', gap: 14 }}>
          <span><span style={{ color: CASE_COLOR.A }}>A</span> = didn't fire</span>
          <span><span style={{ color: CASE_COLOR.B_reliable }}>B</span> = winner that dipped (gave up profit)</span>
          <span><span style={{ color: CASE_COLOR.C_deeper }}>C_deeper</span> = loser saved</span>
          <span><span style={{ color: CASE_COLOR.C_recovered }}>C_recovered</span> = loser that recovered (hurt)</span>
          <span>Hyp P&L = exit_frac × trigger + (1−frac) × actual</span>
        </div>
      </div>
    </div>
  );
}

// ─── Stage1AllTradesModal ─────────────────────────────────────────────────────

type AllTradeFilter = 'all' | 'losses' | 'best_win' | 'worst_dd_win';

function classifyAllTrade(
  trade: Stage1AllTradeRow,
  triggerChoice: 'sl_avg' | 'l_avg' | 'w_avg' | 'custom',
  customValue: number,
  exitFrac: number,
): { caseTag: CaseTag; hypothetical: number; delta: number; triggerMtm: number | null } {
  const bandTrigger =
    triggerChoice === 'sl_avg' ? trade.band_sl_avg
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

  const [triggerChoice, setTriggerChoice] = useState<'sl_avg' | 'l_avg' | 'w_avg' | 'custom'>('l_avg');
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
    return data.trades.map(t => ({
      ...t,
      ...classifyAllTrade(t, triggerChoice, customValue, exitFrac),
    }));
  }, [data, triggerChoice, customValue, exitFrac]);

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
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <span style={{ color: '#7a9bb5' }}>Trigger (per band):</span>
            {([['sl_avg', 'SL-avg'], ['l_avg', 'L-avg'], ['w_avg', 'W-avg']] as [typeof triggerChoice, string][])
              .filter(([k]) => k !== 'custom')
              .map(([k, lbl]) => (
              <button key={k} onClick={() => setTriggerChoice(k as typeof triggerChoice)} style={{
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
                  <SortTh col="min_mtm_usd" label="Min MTM" />
                  <th style={thStyle}>Max MTM</th>
                  <SortTh col="net_pnl_estimate_usd" label="Actual P&L" />
                  <th style={thStyle}>SL?</th>
                  <th style={thStyle}>Case</th>
                  <th style={thStyle}>Trigger $</th>
                  <th style={thStyle}>Fires?</th>
                  <SortTh col="delta" label="Hyp P&L" />
                  <SortTh col="delta" label="Δ P&L" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((t, i) => {
                  const fired = t.caseTag !== 'A';
                  const net = t.net_pnl_estimate_usd ?? 0;
                  const netColor = net >= 0 ? '#4ade80' : '#f87171';
                  const deltaColor = t.delta > 0.5 ? '#4ade80' : t.delta < -0.5 ? '#f87171' : '#7a9bb5';
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
                      <td style={{ ...tdStyle, color: '#f87171', fontWeight: 700 }}>{t.min_mtm_usd != null ? `$${t.min_mtm_usd.toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: '#4ade80' }}>{t.max_mtm_usd != null ? `$${t.max_mtm_usd.toFixed(0)}` : '—'}</td>
                      <td style={{ ...tdStyle, color: netColor, fontWeight: 700 }}>{fmt$(net, 0)}</td>
                      <td style={{ ...tdStyle, color: t.is_premium_sl_hit ? '#f87171' : '#556' }}>{t.is_premium_sl_hit ? 'SL' : '—'}</td>
                      <td style={{ ...tdStyle, color: CASE_COLOR[t.caseTag], fontFamily: 'monospace', fontSize: 10, fontWeight: 700 }}>{t.caseTag}</td>
                      <td style={{ ...tdStyle, color: '#7a9bb5', fontSize: 10 }}>
                        {t.triggerMtm != null ? `$${t.triggerMtm.toFixed(0)}` : '—'}
                      </td>
                      <td style={{ ...tdStyle, color: fired ? '#fbbf24' : '#444', textAlign: 'center' }}>{fired ? '✓' : '—'}</td>
                      <td style={{ ...tdStyle, color: netColor }}>{fmt$(t.hypothetical, 0)}</td>
                      <td style={{ ...tdStyle, color: deltaColor, fontWeight: 700 }}>{fmt$(t.delta, 0)}</td>
                    </tr>
                  );
                })}
                {sorted.length === 0 && (
                  <tr><td colSpan={15} style={{ padding: 24, color: '#556', textAlign: 'center' }}>
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
  onViewTrades,
}: {
  band: string;
  cells: Stage1Cell[];
  bestCell: Stage1BestCell | null;
  summary: Stage1BandSummary | null;
  onViewTrades?: () => void;
}) {
  const [selector, setSelector] = useState<'avg_pnl' | 'composite'>('avg_pnl');
  const [expandedCell, setExpandedCell] = useState<string | null>(null);

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
            SL_avg: {fmtAbs$(summary.sl_avg)} L_avg: {fmtAbs$(summary.l_avg)} W_avg: {fmtAbs$(summary.w_avg)}
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
              {fmt$(highlightedCell.delta_avg_pnl, 1)}
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
              {fmt$(highlightedCell.ev_per_trade, 1)}
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
                  const delta = c?.delta_avg_pnl ?? null;
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
        <CellDetail cell={cellMap[expandedCell]} />
      )}
    </div>
  );
}

// ─── CellDetail ───────────────────────────────────────────────────────────────

function CellDetail({ cell }: { cell: Stage1Cell }) {
  const rows: [string, string][] = [
    ['Status', cell.status],
    ['Exit frac', `${(cell.exit_frac * 100).toFixed(0)}%`],
    ['Trigger', TRIGGER_LABELS[cell.trigger_level] ?? cell.trigger_level],
    ['Trigger MTM', fmtAbs$(cell.trigger_mtm)],
    ['n_total', fmtN(cell.n_total)],
    ['n_A (no fire)', fmtN(cell.n_A)],
    ['n_B_reliable', fmtN(cell.n_B_reliable)],
    ['n_B_unreliable', fmtN(cell.n_B_unreliable)],
    ['n_C (fires)', fmtN(cell.n_C)],
    ['n_C_deeper (saved)', fmtN(cell.n_C_deeper)],
    ['n_C_recovered (hurt)', fmtN(cell.n_C_recovered)],
    ['C_recovered share', fmtPct(cell.c_recovered_share)],
    ['avg saved/trade (C_deeper)', fmt$(cell.avg_save_per_c_deeper, 1)],
    ['avg hurt/trade (C_recovered)', fmt$(cell.avg_hurt_per_c_recovered, 1)],
    ['pct_B_unreliable', fmtPct(cell.pct_B_unreliable)],
    ['EV/trade', fmt$(cell.ev_per_trade, 1)],
    ['Δ avg P&L', fmt$(cell.delta_avg_pnl, 1)],
    ['Δ win rate', fmtPct(cell.delta_win_rate)],
    ['Δ CVaR-95', fmt$(cell.delta_cvar_95, 1)],
    ['Δ max loss', fmt$(cell.delta_max_loss, 1)],
    ['Δ max consec losses', fmtN(cell.delta_max_consec_losses)],
    ['Hyp avg P&L', fmt$(cell.avg_hyp_pnl, 1)],
    ['Hyp win rate', fmtPct(cell.win_rate_hyp)],
    ['Composite v2 (hyp)', cell.composite_score_v2?.toFixed(4) ?? '—'],
  ];

  return (
    <div style={{
      marginTop: 8, background: '#0d1421', borderRadius: 4, padding: 10,
      display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '4px 16px',
      fontSize: 11,
    }}>
      {rows.map(([label, val]) => (
        <div key={label} style={{ display: 'flex', gap: 4 }}>
          <span style={{ color: '#7a9bb5', minWidth: 130 }}>{label}:</span>
          <span style={{ color: '#cfd9e3', fontWeight: 500 }}>{val}</span>
        </div>
      ))}
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
          {bands.map(band => (
            <Stage1BandCard
              key={band}
              band={band}
              cells={cellsByBand[band] ?? []}
              bestCell={bestByBand[band] ?? null}
              summary={summaryByBand[band] ?? null}
              onViewTrades={() => setTradesModal(band)}
            />
          ))}
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
          onClose={() => setTradesModal(null)}
        />
      )}
    </div>
  );
}
