import React from 'react';
import type { M7ExitRule, M7Filters, M7Meta } from '../../types/m7';

interface Props {
  meta: M7Meta | null;
  filters: M7Filters;
  setFilters: (f: M7Filters) => void;
  exitRule: M7ExitRule;
  setExitRule: (r: M7ExitRule) => void;
}

export function M7FilterBar({ meta, filters, setFilters, exitRule, setExitRule }: Props) {
  const update = (k: keyof M7Filters, v: string) => {
    setFilters({ ...filters, [k]: v || undefined });
  };
  const updateRule = (k: keyof M7ExitRule, v: string) => {
    const num = v === '' ? null : Number(v);
    setExitRule({ ...exitRule, [k]: num });
  };
  const sty: React.CSSProperties = {
    background: '#0d1421', border: '1px solid #1a2d42', color: '#cfd9e3',
    padding: '4px 8px', borderRadius: 4, fontSize: 12, minWidth: 80,
  };
  const lab: React.CSSProperties = {
    fontSize: 11, color: '#7a9bb5', marginRight: 6, fontWeight: 600,
    textTransform: 'uppercase', letterSpacing: 0.4,
  };
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: 12, padding: '10px 14px',
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      marginBottom: 10, alignItems: 'center',
    }}>
      <div>
        <span style={lab}>IV band</span>
        <select style={sty} value={filters.entry_atm_iv_band || ''}
                onChange={e => update('entry_atm_iv_band', e.target.value)}>
          <option value="">All</option>
          {meta?.iv_bands.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>
      <div>
        <span style={lab}>Δ target</span>
        <select style={sty} value={filters.delta_target || ''}
                onChange={e => update('delta_target', e.target.value)}>
          <option value="">All</option>
          {meta?.deltas.map(d => <option key={d} value={d}>{d.toFixed(2)}</option>)}
        </select>
      </div>
      <div>
        <span style={lab}>Entry hour</span>
        <select style={sty} value={filters.entry_hour_ist || ''}
                onChange={e => update('entry_hour_ist', e.target.value)}>
          <option value="">All</option>
          {meta && [...meta.entry_hours]
            .sort((a, b) => {
              // Chronological order: Fri 21,22,23 → Sat 00,01,02,03
              const ord = (h: number) => h >= 21 ? h - 21 : h + 3;
              return ord(a) - ord(b);
            })
            .map(h => {
              const day = h >= 21 ? 'Fri' : 'Sat';
              return <option key={h} value={h}>{day} {String(h).padStart(2, '0')}:00 IST</option>;
            })}
        </select>
      </div>
      <div>
        <span style={lab}>Expiry</span>
        <select style={sty} value={filters.expiry_bucket || ''}
                onChange={e => update('expiry_bucket', e.target.value)}>
          <option value="">All</option>
          {meta?.expiry_buckets.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>
      <div>
        <span style={lab}>DTE bucket</span>
        <select style={sty} value={filters.dte_bucket || ''}
                onChange={e => update('dte_bucket', e.target.value)}>
          <option value="">All</option>
          {meta?.dte_buckets.map(b => <option key={b} value={b}>{b}d</option>)}
        </select>
      </div>
      <div>
        <span style={lab}>IVP bucket</span>
        <select style={sty} value={filters.ivp_bucket || ''}
                onChange={e => update('ivp_bucket', e.target.value)}>
          <option value="">All</option>
          {meta?.ivp_buckets.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>
      <div>
        <span style={lab}>Friday</span>
        <select style={{ ...sty, minWidth: 130 }} value={filters.friday_date_ist || ''}
                onChange={e => update('friday_date_ist', e.target.value)}>
          <option value="">All</option>
          {meta?.fridays.map(f => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>

      {/* Chunk 1 — leg attribution skew filters */}
      <div>
        <span style={lab}>IV skew</span>
        <select style={sty} value={filters.iv_skew_bucket || ''}
                onChange={e => update('iv_skew_bucket', e.target.value)}>
          <option value="">All</option>
          {meta?.iv_skew_buckets?.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>
      <div>
        <span style={lab}>Δ skew</span>
        <select style={sty} value={filters.delta_skew_bucket || ''}
                onChange={e => update('delta_skew_bucket', e.target.value)}>
          <option value="">All</option>
          {meta?.delta_skew_buckets?.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>
      <div>
        <span style={lab}>Leg winner</span>
        <select style={sty} value={filters.leg_winner || ''}
                onChange={e => update('leg_winner', e.target.value)}>
          <option value="">All</option>
          {meta?.leg_winners?.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>

      <div style={{ borderLeft: '1px solid #1a2d42', paddingLeft: 12, marginLeft: 4 }}>
        <span style={lab}>Exit Rule</span>
      </div>
      <div>
        <span style={lab}>Exit hour</span>
        <select style={sty}
                value={exitRule.fixed_exit_hour_ist ?? ''}
                onChange={e => {
                  const v = e.target.value;
                  setExitRule({ ...exitRule, fixed_exit_hour_ist: v === '' ? null : Number(v) });
                }}>
          <option value="">Rule-based</option>
          {([5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 17.5] as const).map(h => (
            <option key={h} value={h}>
              {h === 17.5 ? 'Sat 17:30 IST' : `Sat ${String(h).padStart(2, '0')}:00 IST`}
            </option>
          ))}
        </select>
      </div>
      <div>
        <span style={lab}>Max profit %</span>
        <input style={{ ...sty, width: 60 }} type="number" placeholder="—"
               value={exitRule.max_profit_pct ?? ''}
               onChange={e => updateRule('max_profit_pct', e.target.value)} />
      </div>
      <div>
        <span style={lab}>Margin %</span>
        <input style={{ ...sty, width: 60 }} type="number" placeholder="—"
               value={exitRule.margin_target_pct ?? ''}
               onChange={e => updateRule('margin_target_pct', e.target.value)} />
      </div>
      <div>
        <span style={lab}>Premium SL %</span>
        <input style={{ ...sty, width: 60 }} type="number" placeholder="—"
               value={exitRule.premium_sl_pct ?? ''}
               onChange={e => updateRule('premium_sl_pct', e.target.value)} />
      </div>

      <button onClick={() => { setFilters({}); setExitRule({}); }}
              style={{ ...sty, cursor: 'pointer', background: '#1a2d42', marginLeft: 'auto' }}>
        Clear all
      </button>
    </div>
  );
}
