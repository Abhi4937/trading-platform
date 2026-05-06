/**
 * IndicatorConfigPanel — UI to add/configure technical indicators.
 *
 *  - Source toggle: Spot vs Leg premium (caller wires the leg picker if used)
 *  - Add-indicator dropdown
 *  - Per-indicator chip with parameter steppers + remove button
 *
 *  Persists via the parent's controlled state (parent uses usePersistedState).
 */

import React from 'react';
import type { IndicatorConfig, IndicatorType } from '../../types/historical';

interface Props {
  configs: IndicatorConfig[];
  setConfigs: (next: IndicatorConfig[]) => void;
  source: 'spot' | 'leg';
  setSource: (s: 'spot' | 'leg') => void;
  vwapAvailable?: boolean;     // false → grays out VWAP option
}

const SUPPORTED: { type: IndicatorType; label: string; defaults: Record<string, number> }[] = [
  { type: 'sma',    label: 'SMA',     defaults: { period: 20 } },
  { type: 'ema',    label: 'EMA',     defaults: { period: 20 } },
  { type: 'rsi',    label: 'RSI',     defaults: { period: 14 } },
  { type: 'macd',   label: 'MACD',    defaults: { fast: 12, slow: 26, signal: 9 } },
  { type: 'bbands', label: 'BBands',  defaults: { period: 20, stdev: 2 } },
  { type: 'atr',    label: 'ATR',     defaults: { period: 14 } },
  { type: 'vwap',   label: 'VWAP',    defaults: {} },
];

export const IndicatorConfigPanel: React.FC<Props> = ({
  configs, setConfigs, source, setSource, vwapAvailable = true,
}) => {
  const addIndicator = (t: IndicatorType) => {
    const def = SUPPORTED.find(s => s.type === t);
    if (!def) return;
    setConfigs([...configs, { type: t, params: { ...def.defaults } }]);
  };

  const removeAt = (idx: number) => setConfigs(configs.filter((_, i) => i !== idx));

  const updateParam = (idx: number, key: string, val: number) => {
    setConfigs(configs.map((c, i) => i === idx ? { ...c, params: { ...c.params, [key]: val } } : c));
  };

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 10, color: '#c9d1d9', fontSize: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600 }}>Chart</span>

        {/* Source toggle */}
        <div style={{ display: 'flex', gap: 0, border: '1px solid #1a2d42', borderRadius: 4, overflow: 'hidden' }}>
          {(['spot', 'leg'] as const).map(s => (
            <button
              key={s}
              onClick={() => setSource(s)}
              style={{
                background: source === s ? '#1f6feb' : '#080e16',
                color: source === s ? '#fff' : '#7a9bb5',
                border: 'none', padding: '4px 10px', fontSize: 11, fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {s === 'spot' ? 'Spot' : 'Leg Prem'}
            </button>
          ))}
        </div>

        {/* Add indicator */}
        <select
          value=""
          onChange={e => { if (e.target.value) addIndicator(e.target.value as IndicatorType); e.target.value = ''; }}
          style={{
            appearance: 'none', border: '1px solid #1a2d42',
            background: '#080e16', color: '#c9d1d9',
            borderRadius: 4, fontSize: 11, padding: '4px 8px',
            cursor: 'pointer', fontFamily: 'inherit',
          }}
        >
          <option value="">+ Add indicator</option>
          {SUPPORTED.map(s => {
            const disabled = s.type === 'vwap' && !vwapAvailable;
            return (
              <option key={s.type} value={s.type} disabled={disabled}>
                {s.label}{disabled ? ' (no volume)' : ''}
              </option>
            );
          })}
        </select>

        {configs.length === 0 && (
          <span style={{ color: '#4a6a85', fontStyle: 'italic' }}>
            No indicators — pick from the dropdown.
          </span>
        )}
      </div>

      {/* Per-indicator chips */}
      {configs.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
          {configs.map((c, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 6,
              background: '#080e16', border: '1px solid #1a2d42',
              borderRadius: 4, padding: '4px 8px',
              fontSize: 11,
            }}>
              <span style={{ color: '#c9d1d9', fontWeight: 600 }}>{c.type.toUpperCase()}</span>
              {/* Param steppers — type-aware */}
              {(c.type === 'sma' || c.type === 'ema' || c.type === 'rsi' || c.type === 'atr') && (
                <ParamStepper label="period" value={c.params.period ?? 20} min={2} max={500}
                  onChange={n => updateParam(i, 'period', n)} />
              )}
              {c.type === 'bbands' && (
                <>
                  <ParamStepper label="period" value={c.params.period ?? 20} min={2} max={500}
                    onChange={n => updateParam(i, 'period', n)} />
                  <ParamStepper label="stdev" value={c.params.stdev ?? 2} min={0.5} max={5} step={0.5} decimals={1}
                    onChange={n => updateParam(i, 'stdev', n)} />
                </>
              )}
              {c.type === 'macd' && (
                <>
                  <ParamStepper label="fast" value={c.params.fast ?? 12} min={2} max={100}
                    onChange={n => updateParam(i, 'fast', n)} />
                  <ParamStepper label="slow" value={c.params.slow ?? 26} min={2} max={200}
                    onChange={n => updateParam(i, 'slow', n)} />
                  <ParamStepper label="signal" value={c.params.signal ?? 9} min={1} max={50}
                    onChange={n => updateParam(i, 'signal', n)} />
                </>
              )}
              <button
                onClick={() => removeAt(i)}
                title="Remove indicator"
                style={{
                  background: 'transparent', border: 'none', color: '#ff4d6a',
                  cursor: 'pointer', fontSize: 14, padding: '0 2px',
                }}
              >×</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Tiny inline stepper ────────────────────────────────────────────────────

interface StepperProps {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  decimals?: number;
  onChange: (n: number) => void;
}

const ParamStepper: React.FC<StepperProps> = ({
  label, value, min = 1, max = 999, step = 1, decimals = 0, onChange,
}) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
    <span style={{ color: '#7a9bb5' }}>{label}</span>
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={e => {
        const n = Number(e.target.value);
        if (!isNaN(n)) onChange(Math.min(max, Math.max(min, parseFloat(n.toFixed(decimals)))));
      }}
      style={{
        width: 50, background: '#0d1421', border: '1px solid #1a2d42',
        color: '#c9d1d9', borderRadius: 3, padding: '2px 4px',
        fontSize: 11, fontFamily: 'inherit',
      }}
    />
  </span>
);
