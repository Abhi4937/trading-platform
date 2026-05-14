// Friday-locked-band best combo per IV band — the NEW variant.
//
// Each Friday is pinned to ONE IV band (computed from Sat-daily-expiry ATM IV)
// instead of per-trade entry_atm_iv. Every Friday lands in exactly one band →
// no skip / no duplicate across expiry-delta-hour combos.
//
// Mode toggle (A1 / B1 / D1) + tiebreaker priority list (D1 only) at the top.
// Backend: GET /api/v1/m7/friday_band_best_combo
//
// MVP scope: A1 default, headline table only. B1/D1, modals, sizing, filters
// arrive in later phases.

import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7IvBandBestCombo, type M7IvBandBestComboRow } from '../../services/m7_api';
import { InfoIcon } from './InfoIcon';
import { M7RuleComparisonModal } from './M7RuleComparisonModal';
import { M7CellAnalysisModal } from './M7CellAnalysisModal';

type BandMode = 'A1' | 'B1' | 'D1';

const D1_TIEBREAKER_CATEGORIES: { label: string; keys: { key: string; label: string; lookahead: boolean }[] }[] = [
  {
    label: 'Time-of-day (no lookahead)',
    keys: [
      { key: 'earliest_hour_band', label: 'Earliest hour band', lookahead: false },
      { key: 'latest_hour_band',   label: 'Latest hour band',   lookahead: false },
      { key: 'modal_band',         label: 'Modal band (most hours)', lookahead: false },
      { key: 'median_hour_band',   label: 'Median hour band',   lookahead: false },
    ],
  },
  {
    label: 'P&L outcome (lookahead)',
    keys: [
      { key: 'best_avg_net_pnl',   label: 'Best avg net P&L',   lookahead: true },
      { key: 'best_total_net_pnl', label: 'Best total net P&L', lookahead: true },
      { key: 'best_gross_pnl',     label: 'Best gross P&L',     lookahead: true },
      { key: 'best_win_rate',      label: 'Best win rate',      lookahead: true },
    ],
  },
  {
    label: 'Drawdown / MTM (lookahead)',
    keys: [
      { key: 'best_min_mtm',         label: 'Best min MTM (shallowest drawdown)', lookahead: true },
      { key: 'worst_min_mtm',        label: 'Worst min MTM (deepest drawdown)',   lookahead: true },
      { key: 'best_max_mtm',         label: 'Best max MTM (highest peak)',        lookahead: true },
      { key: 'smallest_mtm_range',   label: 'Smallest MTM range (most stable)',   lookahead: true },
    ],
  },
  {
    label: 'Loss (lookahead)',
    keys: [
      { key: 'best_max_loss',  label: 'Best max loss (smallest worst-case)', lookahead: true },
      { key: 'best_avg_loss',  label: 'Best avg loss',                       lookahead: true },
      { key: 'worst_max_loss', label: 'Worst max loss',                       lookahead: true },
    ],
  },
  {
    label: 'Setup (no lookahead)',
    keys: [
      { key: 'highest_credit',    label: 'Highest credit collected', lookahead: false },
      { key: 'lowest_margin',     label: 'Lowest margin used',       lookahead: false },
      { key: 'highest_entry_iv',  label: 'Highest entry IV touched', lookahead: false },
      { key: 'lowest_entry_iv',   label: 'Lowest entry IV touched',  lookahead: false },
    ],
  },
];

const LS_PREFIX = 'm7:fbbestcombo:';
function loadLS<T>(key: string, fallback: T): T {
  try {
    const v = window.localStorage.getItem(LS_PREFIX + key);
    if (v == null) return fallback;
    return JSON.parse(v) as T;
  } catch { return fallback; }
}
function saveLS(key: string, val: unknown) {
  try { window.localStorage.setItem(LS_PREFIX + key, JSON.stringify(val)); } catch {}
}

function bandSort(b: string): number {
  if (b === '100+') return 100;
  const n = parseInt(b.split('-')[0], 10);
  return isNaN(n) ? 9999 : n;
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return `$${v.toFixed(1)}`;
}
function fmtPct(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}
function fmtInt(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v}`;
}

export interface M7FridayBandBestComboTableProps {
  // Optional controlled state from a parent dashboard. When provided, the
  // component reads these instead of its own internal state and HIDES its
  // header (the parent renders shared controls instead).
  controlled?: {
    bandMode: BandMode;
    d1Tiebreakers: string[];
    pickMode: 'by_hour' | 'aggregate_hours';
    ranking: string;
  };
}

export function M7FridayBandBestComboTable({ controlled }: M7FridayBandBestComboTableProps = {}) {
  const [internalBandMode, setInternalBandMode] = useState<BandMode>(() => loadLS('band_mode', 'A1'));
  const [internalD1Tiebreakers, setInternalD1Tiebreakers] = useState<string[]>(
    () => loadLS('d1_tiebreakers', ['best_avg_net_pnl']));
  const [tieAdd, setTieAdd] = useState<string>('');
  const [internalPickMode, setInternalPickMode] = useState<'by_hour' | 'aggregate_hours'>(
    () => loadLS('pick_mode', 'by_hour'));
  // Ranking metric — sum_net_pnl by default since it auto-penalises low-coverage
  // combos (combos that aren't tradeable on every Friday in the band).
  const [internalRanking, setInternalRanking] = useState<string>(() => loadLS('ranking', 'sum_net_pnl'));

  const bandMode = controlled?.bandMode ?? internalBandMode;
  const d1Tiebreakers = controlled?.d1Tiebreakers ?? internalD1Tiebreakers;
  const pickMode = controlled?.pickMode ?? internalPickMode;
  const ranking = controlled?.ranking ?? internalRanking;
  const setBandMode = controlled ? (() => {}) : setInternalBandMode;
  const setD1Tiebreakers = controlled ? (() => {}) : setInternalD1Tiebreakers;
  const setPickMode = controlled ? (() => {}) : setInternalPickMode;
  const setRanking = controlled ? (() => {}) : setInternalRanking;

  const [rows, setRows] = useState<M7IvBandBestComboRow[]>([]);
  const [status, setStatus] = useState<string>('loading');
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Drilldown modals
  const [ruleModal, setRuleModal] = useState<null | {
    band: string; expiry_bucket: string; delta_target: number;
    entry_hour_ist: number; rule_label: string; lots: number;
  }>(null);
  const [cellModal, setCellModal] = useState<null | {
    band: string; expiry_bucket: string; delta_target: number;
    entry_hour_ist: number; rule_label: string; lots: number;
  }>(null);

  // Only persist when running standalone — in controlled mode the parent
  // dashboard owns persistence and writing here would clobber the
  // standalone-mode defaults.
  useEffect(() => { if (!controlled) saveLS('band_mode', bandMode); }, [bandMode, controlled]);
  useEffect(() => { if (!controlled) saveLS('d1_tiebreakers', d1Tiebreakers); }, [d1Tiebreakers, controlled]);
  useEffect(() => { if (!controlled) saveLS('pick_mode', pickMode); }, [pickMode, controlled]);
  useEffect(() => { if (!controlled) saveLS('ranking', ranking); }, [ranking, controlled]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7IvBandBestCombo(
      { ranking: ranking as any, min_n_trades: 5, min_hit_pct: 50, pick_mode: pickMode },
      ac.signal,
      '/friday_band_best_combo',
      bandMode,
      bandMode === 'D1' ? d1Tiebreakers : undefined,
    )
      .then((r: any) => {
        if (!active) return;
        setStatus(r.status ?? 'unknown');
        setStatusMsg(r.message ?? null);
        setRows(r.rows ?? []);
      })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [bandMode, d1Tiebreakers.join(','), pickMode, ranking]);

  const sortedRows = useMemo(() =>
    [...rows].sort((a, b) => bandSort(String(a.iv_band)) - bandSort(String(b.iv_band))),
    [rows]
  );

  // Tiebreaker priority list controls (D1 only)
  const addTie = () => {
    if (!tieAdd) return;
    if (d1Tiebreakers.includes(tieAdd)) return;
    // Always keep earliest_hour_band as last fallback
    const without = d1Tiebreakers.filter(t => t !== 'earliest_hour_band');
    const next = [...without, tieAdd, 'earliest_hour_band'];
    // Deduplicate while preserving order
    const seen = new Set<string>();
    setD1Tiebreakers(next.filter(t => (seen.has(t) ? false : (seen.add(t), true))));
    setTieAdd('');
  };
  const removeTie = (key: string) => {
    if (key === 'earliest_hour_band' && d1Tiebreakers.length === 1) return;
    setD1Tiebreakers(d1Tiebreakers.filter(t => t !== key));
  };
  const moveTie = (idx: number, dir: -1 | 1) => {
    const next = [...d1Tiebreakers];
    const target = idx + dir;
    if (target < 0 || target >= next.length) return;
    // Don't allow moving earliest_hour_band off the last spot if it's there
    [next[idx], next[target]] = [next[target], next[idx]];
    setD1Tiebreakers(next);
  };

  const tieLookup = useMemo(() => {
    const m: Record<string, { label: string; lookahead: boolean }> = {};
    D1_TIEBREAKER_CATEGORIES.forEach(c => c.keys.forEach(k => { m[k.key] = { label: k.label, lookahead: k.lookahead }; }));
    return m;
  }, []);

  return (
    <div style={{ marginTop: 12, marginBottom: 12, padding: 12, background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6 }}>
      {!controlled && (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <span style={{ fontWeight: 700, color: '#cfd9e3', fontSize: 14, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Friday-locked IV-band Best Combo
        </span>
        <InfoIcon text="Each Friday is pinned to ONE IV band (computed from Saturday daily-expiry ATM IV) instead of per-trade entry IV. Every Friday lands in exactly one band — no skip, no duplicate across expiry/delta combos." />
        <span style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 8 }}>Mode:</span>
        {(['A1', 'B1', 'D1'] as BandMode[]).map(m => (
          <button key={m}
            onClick={() => setBandMode(m)}
            style={{
              padding: '4px 10px', borderRadius: 4, fontSize: 12,
              background: bandMode === m ? '#1f6feb' : '#0a1018',
              color: bandMode === m ? '#fff' : '#cfd9e3',
              border: bandMode === m ? '1px solid #2f7feb' : '1px solid #1a2d42',
              cursor: 'pointer', fontWeight: bandMode === m ? 700 : 400,
            }}
          >
            {m === 'A1' ? 'A1 — 21:00 snapshot' : m === 'B1' ? 'B1 — Modal band' : 'D1 — Tiebreaker chain'}
          </button>
        ))}
        <span style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 12 }}>Pick:</span>
        <button onClick={() => setPickMode('by_hour')}
          title="One pick per (band, hour) — most precise"
          style={{ padding: '4px 10px', borderRadius: 4, fontSize: 12,
            background: pickMode === 'by_hour' ? '#1f6feb' : '#0a1018',
            color: pickMode === 'by_hour' ? '#fff' : '#cfd9e3',
            border: pickMode === 'by_hour' ? '1px solid #2f7feb' : '1px solid #1a2d42',
            cursor: 'pointer', fontWeight: pickMode === 'by_hour' ? 700 : 400 }}>
          ⏱ Per hour
        </button>
        <button onClick={() => setPickMode('aggregate_hours')}
          title="Collapse hours so every Friday in this band is tested — broader coverage"
          style={{ padding: '4px 10px', borderRadius: 4, fontSize: 12,
            background: pickMode === 'aggregate_hours' ? '#1f6feb' : '#0a1018',
            color: pickMode === 'aggregate_hours' ? '#fff' : '#cfd9e3',
            border: pickMode === 'aggregate_hours' ? '1px solid #2f7feb' : '1px solid #1a2d42',
            cursor: 'pointer', fontWeight: pickMode === 'aggregate_hours' ? 700 : 400 }}>
          ∑ All hours
        </button>
        <span style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 12 }}>Rank by:</span>
        <select value={ranking} onChange={e => setRanking(e.target.value)}
          title="Total net P&L favours combos that cover all the band's Fridays. Avg net P&L is per-trade — can favour low-sample niche combos."
          style={{ background: '#0d1421', color: '#cfd9e3', border: '1px solid #1a2d42',
            borderRadius: 4, padding: '4px 8px', fontSize: 12, minWidth: 180 }}>
          <option value="sum_net_pnl">Total net P&L (recommended)</option>
          <option value="avg_net_pnl">Avg net P&L</option>
          <option value="composite_score">Composite score</option>
          <option value="avg_pct_return_on_credit">% return on credit</option>
          <option value="avg_pct_return_on_margin">% return on margin</option>
          <option value="win_rate">Win rate</option>
          <option value="sharpe_per_trade">Sharpe (per trade)</option>
        </select>
      </div>

      )}
      {!controlled && bandMode === 'D1' && (
        <div style={{ marginBottom: 10, padding: 8, background: '#0a1018', border: '1px solid #1a2d42', borderRadius: 4 }}>
          <div style={{ color: '#7a9bb5', fontSize: 11, marginBottom: 6 }}>
            D1 Tiebreaker priority (applied top-to-bottom; <span style={{ color: '#cfd9e3' }}>earliest_hour_band</span> is the fixed final fallback):
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {d1Tiebreakers.map((tb, idx) => {
              const info = tieLookup[tb] ?? { label: tb, lookahead: false };
              return (
                <div key={tb} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px', background: '#0d1421', borderRadius: 3 }}>
                  <span style={{ color: '#5b7894', fontSize: 11, minWidth: 18 }}>#{idx + 1}</span>
                  <button onClick={() => moveTie(idx, -1)} disabled={idx === 0} style={{ background: 'transparent', border: 'none', color: idx === 0 ? '#3a4a5a' : '#7a9bb5', cursor: idx === 0 ? 'default' : 'pointer', padding: '0 4px' }}>▲</button>
                  <button onClick={() => moveTie(idx, 1)} disabled={idx === d1Tiebreakers.length - 1} style={{ background: 'transparent', border: 'none', color: idx === d1Tiebreakers.length - 1 ? '#3a4a5a' : '#7a9bb5', cursor: idx === d1Tiebreakers.length - 1 ? 'default' : 'pointer', padding: '0 4px' }}>▼</button>
                  <span style={{ color: '#cfd9e3', fontSize: 12, flex: 1 }}>{info.label}</span>
                  {info.lookahead && (
                    <span title="Uses trade outcomes — lookahead bias for diagnostics only" style={{ color: '#e3b341', fontSize: 10 }}>⚠ lookahead</span>
                  )}
                  <button onClick={() => removeTie(tb)} disabled={tb === 'earliest_hour_band' && d1Tiebreakers.length === 1} style={{ background: 'transparent', border: 'none', color: '#e07579', cursor: 'pointer', padding: '0 4px' }}>×</button>
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
            <select value={tieAdd} onChange={e => setTieAdd(e.target.value)} style={{ background: '#0d1421', color: '#cfd9e3', border: '1px solid #1a2d42', borderRadius: 4, padding: '4px 8px', fontSize: 12 }}>
              <option value="">Add tiebreaker…</option>
              {D1_TIEBREAKER_CATEGORIES.map(cat => (
                <optgroup key={cat.label} label={cat.label}>
                  {cat.keys.filter(k => !d1Tiebreakers.includes(k.key)).map(k => (
                    <option key={k.key} value={k.key}>{k.label}{k.lookahead ? ' ⚠' : ''}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <button onClick={addTie} disabled={!tieAdd} style={{ padding: '4px 10px', borderRadius: 4, fontSize: 12, background: tieAdd ? '#1f6feb' : '#0a1018', color: tieAdd ? '#fff' : '#3a4a5a', border: '1px solid #1a2d42', cursor: tieAdd ? 'pointer' : 'default' }}>
              + Add
            </button>
          </div>
        </div>
      )}

      {loading && <div style={{ color: '#7a9bb5', fontSize: 12, padding: 6 }}>Loading…</div>}
      {err && <div style={{ color: '#f85149', fontSize: 12, padding: 6 }}>Error: {err}</div>}
      {!loading && !err && status === 'not_built' && (
        <div style={{ color: '#e3b341', fontSize: 12, padding: 6 }}>
          {statusMsg ?? 'Friday-band grid not yet built. Builder runs in container m7-friday-band-grid-builder (~4-5h).'}
        </div>
      )}
      {!loading && !err && status === 'ready' && rows.length === 0 && bandMode !== 'A1' && (
        <div style={{ color: '#e3b341', fontSize: 12, padding: 6 }}>
          Mode {bandMode} not yet implemented (MVP = A1 only). Coming in subsequent phases.
        </div>
      )}
      {!loading && !err && status === 'ready' && rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: 12, color: '#cfd9e3', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1a2d42', color: '#7a9bb5', textTransform: 'uppercase', letterSpacing: 0.4 }}>
                <th style={{ textAlign: 'left',  padding: '6px 8px' }}>Band</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }} title="Fridays in this band under the current mode">#Fri</th>
                <th style={{ textAlign: 'left',  padding: '6px 8px' }}>Expiry</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }}>Δ</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }}>Hour</th>
                <th style={{ textAlign: 'left',  padding: '6px 8px' }}>Rule</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }} title="Trades in this picked cell. coverage = n / #Fri">n</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }} title="n / #Fri — how many of the band's Fridays this picked combo covered">Cov%</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }}>Win %</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }}>Avg net</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }}>Sum net</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }}>Max loss</th>
                <th style={{ textAlign: 'right', padding: '6px 8px' }}>Composite</th>
                <th style={{ textAlign: 'center', padding: '6px 8px' }}>Drill</th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((r, i) => {
                const lots = (r as any).lots ?? 100;
                const openRule = () => setRuleModal({
                  band: String(r.iv_band), expiry_bucket: String(r.expiry_bucket),
                  delta_target: Number(r.delta_target), entry_hour_ist: Number(r.entry_hour_ist),
                  rule_label: String(r.rule_label), lots,
                });
                const openCell = () => setCellModal({
                  band: String(r.iv_band), expiry_bucket: String(r.expiry_bucket),
                  delta_target: Number(r.delta_target), entry_hour_ist: Number(r.entry_hour_ist),
                  rule_label: String(r.rule_label), lots,
                });
                const nFridays = (r as any).n_fridays_in_band ?? null;
                const cov = (r.n_trades != null && nFridays && nFridays > 0)
                  ? (r.n_trades / nFridays) : null;
                const covColor = cov == null ? '#7a9bb5'
                  : cov >= 0.85 ? '#3fb950'
                  : cov >= 0.5  ? '#e3b341'
                  : '#f85149';
                return (
                  <tr key={`${r.iv_band}-${i}`} style={{ borderBottom: '1px solid #0d1421', cursor: 'pointer' }}
                      onClick={openRule}>
                    <td style={{ padding: '6px 8px', fontWeight: 600 }}>{r.iv_band}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: '#7a9bb5' }}>{nFridays ?? '—'}</td>
                    <td style={{ padding: '6px 8px' }}>{r.expiry_bucket}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{r.delta_target?.toFixed(2)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{r.entry_hour_ist ?? '—'}</td>
                    <td style={{ padding: '6px 8px', color: '#7a9bb5', fontSize: 11 }}>{r.rule_label}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmtInt(r.n_trades)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: covColor, fontWeight: 600 }}
                        title={cov != null && nFridays ? `${r.n_trades} of ${nFridays} Fridays` : 'coverage unavailable'}>
                      {cov == null ? '—' : `${(cov * 100).toFixed(0)}%`}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmtPct(r.win_rate)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: (r.avg_net_pnl ?? 0) >= 0 ? '#3fb950' : '#f85149' }}>
                      {fmtUsd(r.avg_net_pnl)}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: (r.sum_net_pnl ?? 0) >= 0 ? '#3fb950' : '#f85149' }}>
                      {fmtUsd(r.sum_net_pnl)}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: '#f85149' }}>{fmtUsd(r.max_loss_usd)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{r.composite_score?.toFixed(3) ?? '—'}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                      <button onClick={(e) => { e.stopPropagation(); openCell(); }}
                        title="Cell analysis — cross-band + single-combo simulation"
                        style={{ background: 'transparent', border: '1px solid #1a2d42', borderRadius: 4,
                                 color: '#7a9bb5', padding: '2px 6px', fontSize: 11, cursor: 'pointer' }}>
                        🔍
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ color: '#5b7894', fontSize: 10, marginTop: 6 }}>
            {rows.length} rows · band_mode={bandMode}{bandMode === 'D1' && ` · tiebreakers=${d1Tiebreakers.join(',')}`}
            <span style={{ marginLeft: 8 }}>· Click row → rule comparison · 🔍 → cell analysis</span>
          </div>
        </div>
      )}

      {ruleModal && (
        <M7RuleComparisonModal
          band={ruleModal.band}
          expiry_bucket={ruleModal.expiry_bucket}
          delta_target={ruleModal.delta_target}
          entry_hour_ist={ruleModal.entry_hour_ist}
          pickedRuleLabel={ruleModal.rule_label}
          minHitPct={50}
          minNTrades={5}
          ruleFamily="all"
          endpointPrefix="/friday_band_best_combo"
          bandMode={bandMode}
          d1Tiebreakers={bandMode === 'D1' ? d1Tiebreakers : undefined}
          onClose={() => setRuleModal(null)}
        />
      )}
      {cellModal && (
        <M7CellAnalysisModal
          band={cellModal.band}
          expiry_bucket={cellModal.expiry_bucket}
          delta_target={cellModal.delta_target}
          entry_hour_ist={cellModal.entry_hour_ist}
          rule_label={cellModal.rule_label}
          lots={cellModal.lots}
          endpointPrefix="/friday_band_best_combo"
          bandMode={bandMode}
          d1Tiebreakers={bandMode === 'D1' ? d1Tiebreakers : undefined}
          onClose={() => setCellModal(null)}
        />
      )}
    </div>
  );
}
