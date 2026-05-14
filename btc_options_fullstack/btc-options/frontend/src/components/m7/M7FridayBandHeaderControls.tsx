// Page-level header controls for the M7 Friday-Band dashboard.
// Promoted from the internals of M7FridayBandBestComboTable so that all 9
// sections share the same mode / tiebreaker / pick-mode / ranking choices.

import React, { useMemo } from 'react';
import { InfoIcon } from './InfoIcon';

export type BandMode = 'A1' | 'B1' | 'D1';

export const D1_TIEBREAKER_CATEGORIES: {
  label: string;
  keys: { key: string; label: string; lookahead: boolean }[];
}[] = [
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

export interface FridayBandHeaderState {
  bandMode: BandMode;
  d1Tiebreakers: string[];
  pickMode: 'by_hour' | 'aggregate_hours';
  ranking: string;
}

interface Props {
  value: FridayBandHeaderState;
  onChange: (next: FridayBandHeaderState) => void;
}

export function M7FridayBandHeaderControls({ value, onChange }: Props) {
  const { bandMode, d1Tiebreakers, pickMode, ranking } = value;
  const [tieAdd, setTieAdd] = React.useState<string>('');

  const setBandMode = (m: BandMode) => onChange({ ...value, bandMode: m });
  const setPickMode = (m: 'by_hour' | 'aggregate_hours') => onChange({ ...value, pickMode: m });
  const setRanking  = (r: string) => onChange({ ...value, ranking: r });
  const setD1Tiebreakers = (tb: string[]) => onChange({ ...value, d1Tiebreakers: tb });

  const tieLookup = useMemo(() => {
    const m: Record<string, { label: string; lookahead: boolean }> = {};
    D1_TIEBREAKER_CATEGORIES.forEach(c => c.keys.forEach(k => {
      m[k.key] = { label: k.label, lookahead: k.lookahead };
    }));
    return m;
  }, []);

  const addTie = () => {
    if (!tieAdd) return;
    if (d1Tiebreakers.includes(tieAdd)) return;
    const without = d1Tiebreakers.filter(t => t !== 'earliest_hour_band');
    const next = [...without, tieAdd, 'earliest_hour_band'];
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
    [next[idx], next[target]] = [next[target], next[idx]];
    setD1Tiebreakers(next);
  };

  return (
    <div style={{
      marginBottom: 10, padding: 12,
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <span style={{
          fontWeight: 700, color: '#cfd9e3', fontSize: 13,
          textTransform: 'uppercase', letterSpacing: 0.5,
        }}>
          Friday-Band Controls
        </span>
        <InfoIcon text="The IV band is computed from the CURRENT EXPIRY's ATM IV (Saturday daily-expiry — the same-day expiry for any trade entered after 17:30 IST on Friday). Each Friday is pinned to ONE band so every Friday lands in exactly one band — no skip, no duplicate across expiry/delta combos. These controls apply to every section on this page." />
        <span style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 8 }}>Mode:</span>
        {(['A1', 'B1', 'D1'] as BandMode[]).map(m => (
          <button key={m} onClick={() => setBandMode(m)}
            style={{
              padding: '4px 10px', borderRadius: 4, fontSize: 12,
              background: bandMode === m ? '#1f6feb' : '#0a1018',
              color: bandMode === m ? '#fff' : '#cfd9e3',
              border: bandMode === m ? '1px solid #2f7feb' : '1px solid #1a2d42',
              cursor: 'pointer', fontWeight: bandMode === m ? 700 : 400,
            }}>
            {m === 'A1' ? 'A1 — 21:00 snapshot' : m === 'B1' ? 'B1 — Modal band' : 'D1 — Tiebreaker chain'}
          </button>
        ))}
        <span style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 12 }}>Pick:</span>
        <button onClick={() => setPickMode('by_hour')}
          style={{
            padding: '4px 10px', borderRadius: 4, fontSize: 12,
            background: pickMode === 'by_hour' ? '#1f6feb' : '#0a1018',
            color: pickMode === 'by_hour' ? '#fff' : '#cfd9e3',
            border: pickMode === 'by_hour' ? '1px solid #2f7feb' : '1px solid #1a2d42',
            cursor: 'pointer', fontWeight: pickMode === 'by_hour' ? 700 : 400,
          }}>⏱ Per hour</button>
        <button onClick={() => setPickMode('aggregate_hours')}
          style={{
            padding: '4px 10px', borderRadius: 4, fontSize: 12,
            background: pickMode === 'aggregate_hours' ? '#1f6feb' : '#0a1018',
            color: pickMode === 'aggregate_hours' ? '#fff' : '#cfd9e3',
            border: pickMode === 'aggregate_hours' ? '1px solid #2f7feb' : '1px solid #1a2d42',
            cursor: 'pointer', fontWeight: pickMode === 'aggregate_hours' ? 700 : 400,
          }}>∑ All hours</button>
        <span style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 12 }}>Rank by:</span>
        <select value={ranking} onChange={e => setRanking(e.target.value)}
          style={{
            background: '#0d1421', color: '#cfd9e3', border: '1px solid #1a2d42',
            borderRadius: 4, padding: '4px 8px', fontSize: 12, minWidth: 180,
          }}>
          <option value="sum_net_pnl">Total net P&L (recommended)</option>
          <option value="avg_net_pnl">Avg net P&L</option>
          <option value="composite_score">Composite score</option>
          <option value="avg_pct_return_on_credit">% return on credit</option>
          <option value="avg_pct_return_on_margin">% return on margin</option>
          <option value="win_rate">Win rate</option>
          <option value="sharpe_per_trade">Sharpe (per trade)</option>
        </select>
      </div>

      {bandMode === 'D1' && (
        <div style={{
          padding: 8, background: '#0a1018',
          border: '1px solid #1a2d42', borderRadius: 4,
        }}>
          <div style={{ color: '#7a9bb5', fontSize: 11, marginBottom: 6 }}>
            D1 Tiebreaker priority (applied top-to-bottom; <span style={{ color: '#cfd9e3' }}>earliest_hour_band</span> is the fixed final fallback):
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {d1Tiebreakers.map((tb, idx) => {
              const info = tieLookup[tb] ?? { label: tb, lookahead: false };
              return (
                <div key={tb} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '4px 8px', background: '#0d1421', borderRadius: 3,
                }}>
                  <span style={{ color: '#5b7894', fontSize: 11, minWidth: 18 }}>#{idx + 1}</span>
                  <button onClick={() => moveTie(idx, -1)} disabled={idx === 0}
                    style={{
                      background: 'transparent', border: 'none',
                      color: idx === 0 ? '#3a4a5a' : '#7a9bb5',
                      cursor: idx === 0 ? 'default' : 'pointer', padding: '0 4px',
                    }}>▲</button>
                  <button onClick={() => moveTie(idx, 1)} disabled={idx === d1Tiebreakers.length - 1}
                    style={{
                      background: 'transparent', border: 'none',
                      color: idx === d1Tiebreakers.length - 1 ? '#3a4a5a' : '#7a9bb5',
                      cursor: idx === d1Tiebreakers.length - 1 ? 'default' : 'pointer', padding: '0 4px',
                    }}>▼</button>
                  <span style={{ color: '#cfd9e3', fontSize: 12, flex: 1 }}>{info.label}</span>
                  {info.lookahead && (
                    <span title="Uses trade outcomes — lookahead bias for diagnostics only"
                      style={{ color: '#e3b341', fontSize: 10 }}>⚠ lookahead</span>
                  )}
                  <button onClick={() => removeTie(tb)}
                    disabled={tb === 'earliest_hour_band' && d1Tiebreakers.length === 1}
                    style={{
                      background: 'transparent', border: 'none',
                      color: '#e07579', cursor: 'pointer', padding: '0 4px',
                    }}>×</button>
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
            <select value={tieAdd} onChange={e => setTieAdd(e.target.value)}
              style={{
                background: '#0d1421', color: '#cfd9e3', border: '1px solid #1a2d42',
                borderRadius: 4, padding: '4px 8px', fontSize: 12,
              }}>
              <option value="">Add tiebreaker…</option>
              {D1_TIEBREAKER_CATEGORIES.map(cat => (
                <optgroup key={cat.label} label={cat.label}>
                  {cat.keys.filter(k => !d1Tiebreakers.includes(k.key)).map(k => (
                    <option key={k.key} value={k.key}>{k.label}{k.lookahead ? ' ⚠' : ''}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <button onClick={addTie} disabled={!tieAdd}
              style={{
                padding: '4px 10px', borderRadius: 4, fontSize: 12,
                background: tieAdd ? '#1f6feb' : '#0a1018',
                color: tieAdd ? '#fff' : '#3a4a5a',
                border: '1px solid #1a2d42', cursor: tieAdd ? 'pointer' : 'default',
              }}>+ Add</button>
          </div>
        </div>
      )}
    </div>
  );
}
