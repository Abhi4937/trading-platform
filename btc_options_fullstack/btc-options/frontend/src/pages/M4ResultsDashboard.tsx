/**
 * M6 Batch Results dashboard. Visualizes m4_trades.parquet via /api/v1/m4/*.
 *
 * Sections:
 *   1. Header strip — overall counts/win-rate/total P&L/SL hit rate
 *   2. Filter bar  — delta, DTE, pattern, hard-filter flags
 *   3. Win-rate heatmap (DTE × Δ)
 *   4. Pattern bars
 *   5. Credit% × Net P&L scatter
 *   6. Quality calibration curve
 */
import { useEffect, useState } from 'react';
import { fetchSummary, type M4Filters, type M4Summary } from '../services/m4_api';
import { M4WinrateHeatmap } from '../components/m4/M4WinrateHeatmap';
import { M4PatternBars } from '../components/m4/M4PatternBars';
import { M4ScatterChart } from '../components/m4/M4ScatterChart';
import { M4QualityCalibrationCurve } from '../components/m4/M4QualityCalibrationCurve';
import { M4ExpiryGridTable } from '../components/m4/M4ExpiryGridTable';
import { M4WinFrequency } from '../components/m4/M4WinFrequency';
import { M4WinnersVsLosers } from '../components/m4/M4WinnersVsLosers';
import { M4PerFridayBest } from '../components/m4/M4PerFridayBest';
import { usePersistedState } from '../hooks/usePersistedState';

const DTE_ORDER  = ['0-3', '3-7', '7-14', '14-30', '30-60'];
const DELTA_OPTS = ['0.05', '0.10', '0.15', '0.25', '0.30', '0.50'];
const PATTERN_OPTS = ['A', 'B', 'C', 'D', 'Other'];

export function M4ResultsDashboard() {
  const [filters, setFilters] = useState<M4Filters>({});
  const [summary, setSummary] = useState<M4Summary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSummary(filters)
      .then(d => { if (!cancelled) { setSummary(d); setError(null); } })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [JSON.stringify(filters)]);

  const setFilter = (k: keyof M4Filters, v: string | undefined) => {
    setFilters(f => {
      const next = { ...f };
      if (v === undefined || v === '') delete next[k];
      else (next as any)[k] = v;
      return next;
    });
  };

  const fmtUsd = (v: number) => `$${v.toLocaleString('en-US',
    { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

  return (
    <div style={{
      padding: 16, color: '#cdd6e0', fontSize: 13,
      height: '100%', overflowY: 'auto', width: '100%',
    }}>
      {/* Header strip */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', gap: 12,
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        padding: 16, marginBottom: 16,
      }}>
        <Stat label="Trades"       value={summary ? summary.n_trades.toLocaleString() : '—'} />
        <Stat label="Winners"      value={summary ? summary.n_winners.toLocaleString() : '—'} />
        <Stat label="Win Rate"     value={summary ? `${(summary.win_rate * 100).toFixed(1)}%` : '—'}
              color={summary && summary.win_rate >= 0.55 ? '#10b981' : '#f0b429'} />
        <Stat label="SL Hit"       value={summary ? `${(summary.sl_hit_rate * 100).toFixed(1)}%` : '—'}
              color="#f0b429" />
        <Stat label="Total Net P&L"
              value={summary ? fmtUsd(summary.total_net_pnl_usd) : '—'}
              color={summary && summary.total_net_pnl_usd >= 0 ? '#10b981' : '#ef4444'} />
        <Stat label="Avg Net"
              value={summary ? `$${summary.avg_net_pnl_usd.toFixed(2)}` : '—'}
              color={summary && summary.avg_net_pnl_usd >= 0 ? '#10b981' : '#ef4444'} />
        <Stat label="Avg Credit %"
              value={summary ? `${(summary.avg_credit_pct * 100).toFixed(2)}%` : '—'} />
        <Stat label="Avg MFE / MAE"
              value={summary ? `${fmtUsd(summary.avg_max_mtm_usd)} / ${fmtUsd(summary.avg_min_mtm_usd)}` : '—'} />
      </div>

      {/* Filter bar */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center',
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        padding: 12, marginBottom: 16,
      }}>
        <FilterSelect label="Δ"     value={filters.delta_target ?? ''} options={DELTA_OPTS}
                      onChange={v => setFilter('delta_target', v)} />
        <FilterSelect label="DTE"   value={filters.dte_bucket ?? ''} options={DTE_ORDER}
                      onChange={v => setFilter('dte_bucket', v)} />
        <FilterSelect label="Pattern" value={filters.ctx_pattern ?? ''} options={PATTERN_OPTS}
                      onChange={v => setFilter('ctx_pattern', v)} />
        <FilterSelect label="Outcome" value={filters.outcome ?? ''} options={['win', 'loss']}
                      onChange={v => setFilter('outcome', v as 'win' | 'loss' | undefined)} />
        <FilterSelect label="Exit Reason" value={filters.exit_reason ?? ''}
                      options={['TimeStop', 'LegSL']}
                      onChange={v => setFilter('exit_reason', v)} />
        <label style={{ fontSize: 12 }}>
          <input type="checkbox" checked={filters.flt_dte_5_14 === 'true'}
                 onChange={e => setFilter('flt_dte_5_14', e.target.checked ? 'true' : undefined)} />
          {' '}Hard-filter pass: DTE 5–14
        </label>
        <button
          onClick={() => setFilters({})}
          style={{
            padding: '4px 10px', fontSize: 12, background: '#1a2d42',
            color: '#cdd6e0', border: '1px solid #243a52', borderRadius: 4,
            cursor: 'pointer',
          }}
        >Clear</button>
        {loading && <span style={{ color: '#7a9bb5', fontSize: 11 }}>refreshing…</span>}
        {error && <span style={{ color: '#fca5a5', fontSize: 11 }}>{error}</span>}
      </div>

      {/* Charts */}
      <div style={{
        display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16, marginBottom: 16,
      }}>
        <M4WinrateHeatmap
          rowDim="dte_bucket" colDim="delta_target"
          rowOrder={DTE_ORDER} colOrder={DELTA_OPTS}
          metric="win_rate" filters={filters}
          title="Win-rate heatmap (DTE × Δ)"
          format={v => `${(v * 100).toFixed(0)}%`}
        />
        <M4PatternBars filters={filters} />
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16,
      }}>
        <M4ScatterChart
          x="credit_pct" y="net_pnl_usd"
          xLabel="Credit %" yLabel="Net P&L ($)"
          xFormatter={v => `${(v * 100).toFixed(1)}%`}
          yFormatter={v => `$${v.toFixed(0)}`}
          filters={filters}
          title="Credit% × Net P&L (color: outcome)"
        />
        <M4QualityCalibrationCurve nBuckets={10} />
      </div>

      {/* Per-contract-type × IV × Δ grid (expiry-classified) */}
      <M4ExpiryGridTable />

      {/* Attribution: per-Friday best, winners-vs-losers, win frequency */}
      <AttributionSection />
    </div>
  );
}

function AttributionSection() {
  const [attrDelta, setAttrDelta] = usePersistedState<number>('m6:attr_delta', 0.30);
  const DELTA_CHIPS = [0.05, 0.10, 0.15, 0.25, 0.30, 0.50];

  return (
    <div style={{ marginTop: 24 }}>
      <div style={{
        fontSize: 14, fontWeight: 700, color: '#cdd6e0',
        marginBottom: 8, display: 'flex', alignItems: 'baseline', gap: 12,
      }}>
        <span>Attribution analysis</span>
        <span style={{ fontSize: 11, fontWeight: 400, color: '#7a9bb5' }}>
          Per-Friday winning contract + indicator drivers. Reads from
          m4_trades_enriched.parquet (IV-premium decomposition + per-leg θ/ν).
        </span>
      </div>

      {/* Δ chip selector — shared across the 3 components below */}
      <div style={{
        display: 'flex', gap: 6, marginBottom: 12, alignItems: 'center',
        background: '#0d1421', border: '1px solid #1a2d42',
        borderRadius: 6, padding: 10,
      }}>
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>Δ:</span>
        {DELTA_CHIPS.map(d => (
          <button
            key={d}
            onClick={() => setAttrDelta(d)}
            style={{
              background: attrDelta === d ? '#1f6feb' : '#0a1018',
              color: attrDelta === d ? '#fff' : '#cdd6e0',
              border: `1px solid ${attrDelta === d ? '#1f6feb' : '#1a2d42'}`,
              padding: '4px 10px', fontSize: 11, borderRadius: 4,
              cursor: 'pointer', fontWeight: attrDelta === d ? 700 : 500,
            }}
          >
            {d.toFixed(2)}
          </button>
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 10, color: '#7a9bb5' }}>
          Δ choice persists across sessions (key m6:attr_delta).
        </span>
      </div>

      <M4WinFrequency      delta={attrDelta} />
      <M4WinnersVsLosers   delta={attrDelta} />
      <M4PerFridayBest     delta={attrDelta} />
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div style={{
        fontSize: 10, color: '#7a9bb5', textTransform: 'uppercase', letterSpacing: 0.5,
      }}>{label}</div>
      <div style={{
        fontSize: 18, fontWeight: 700, color: color ?? '#cdd6e0',
      }}>{value}</div>
    </div>
  );
}

function FilterSelect({ label, value, options, onChange }: {
  label: string; value: string; options: string[];
  onChange: (v: string | undefined) => void;
}) {
  return (
    <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12 }}>
      <span style={{ color: '#7a9bb5' }}>{label}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value || undefined)}
        style={{
          background: '#0a1018', color: '#cdd6e0', border: '1px solid #1a2d42',
          borderRadius: 4, padding: '3px 6px', fontSize: 12,
        }}
      >
        <option value="">All</option>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

export default M4ResultsDashboard;
