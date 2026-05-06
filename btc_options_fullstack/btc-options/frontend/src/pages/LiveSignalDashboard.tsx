/**
 * LiveSignalDashboard — scans every live expiry × 6 deltas and surfaces
 * the highest-quality short-strangle candidate right now.
 *
 * Data source: /api/v1/live-signal/scan (polled every 7s).
 *
 * Layout:
 *   • Header strip:    spot, last-refresh, scan stats, refresh button
 *   • Best-now card:   top-1 candidate with full quality decomposition
 *   • Candidates table: sortable grid of all (expiry × Δ) combos
 */

import { useMemo, useState } from 'react';
import {
  useLiveSignalScan,
  type LiveSignalCandidate,
} from '../services/live_signal_api';

type SortKey =
  | 'quality_score' | 'target_delta' | 'dte_days' | 'credit_pct'
  | 'overall_winrate' | 'pattern_winrate' | 'expiry';

const fmt = (v: number | null | undefined, d = 2): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : v.toFixed(d);
const fmtPct = (v: number | null | undefined, d = 2): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(d)}%`;
const fmtUsd = (v: number | null | undefined, d = 0): string =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : `$${v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;

const bandColor = (band: string | null | undefined): string => {
  switch (band) {
    case 'strong': return '#10b981';
    case 'standard': return '#f0b429';
    case 'marginal': return '#d97706';
    case 'skip': return '#ef4444';
    default: return '#475569';
  }
};
const sourceColor = (src: string | null | undefined): string => {
  if (src === 'calibrated_v2') return '#10b981';
  if (src === 'calibrated') return '#f0b429';
  if (src === 'fallback_ivp_credit') return '#6b7280';
  return '#374151';
};
const flagColor = (b: boolean): string => (b ? '#10b981' : '#ef4444');

const ageSec = (ts: number): number => Math.max(0, Math.floor(Date.now() / 1000 - ts));

export function LiveSignalDashboard() {
  const { data, loading, error, refetch } = useLiveSignalScan({ intervalMs: 7000 });
  const [sortKey, setSortKey] = useState<SortKey>('quality_score');
  const [sortDesc, setSortDesc] = useState(true);
  const [onlyPassing, setOnlyPassing] = useState(false);

  const sorted = useMemo<LiveSignalCandidate[]>(() => {
    if (!data) return [];
    let rows = data.candidates.slice();
    if (onlyPassing) rows = rows.filter(c => c.flt_all_pass);
    rows.sort((a, b) => {
      const av = a[sortKey] as number | string | null | undefined;
      const bv = b[sortKey] as number | string | null | undefined;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      const cmp = typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av).localeCompare(String(bv));
      return sortDesc ? -cmp : cmp;
    });
    return rows;
  }, [data, sortKey, sortDesc, onlyPassing]);

  const onSort = (k: SortKey) => {
    if (k === sortKey) setSortDesc(d => !d);
    else { setSortKey(k); setSortDesc(true); }
  };

  const best = sorted[0] ?? null;

  return (
    <div style={{
      padding: 16, color: '#cdd6e0', fontSize: 13,
      height: '100%', overflowY: 'auto', width: '100%',
    }}>
      {/* Header strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 24, padding: '12px 16px',
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        marginBottom: 16,
      }}>
        <div>
          <div style={{ fontSize: 11, color: '#7a9bb5', textTransform: 'uppercase' }}>BTC Spot</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#fff' }}>
            {best ? `$${best.spot.toLocaleString()}` : '—'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: '#7a9bb5', textTransform: 'uppercase' }}>Scan</div>
          <div style={{ fontSize: 14 }}>
            {data ? `${data.total_scanned} candidates · ${ageSec(data.ts)}s ago` : 'loading...'}
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          <input
            type="checkbox" checked={onlyPassing}
            onChange={e => setOnlyPassing(e.target.checked)}
          />
          Only show all-filters-pass
        </label>
        <button
          onClick={refetch}
          style={{
            padding: '6px 14px', fontSize: 12, fontWeight: 600,
            background: '#1f6feb', color: '#fff',
            border: 'none', borderRadius: 4, cursor: 'pointer',
          }}
          disabled={loading}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div style={{
          padding: '10px 14px', background: '#3b1d1d', color: '#fca5a5',
          border: '1px solid #7f1d1d', borderRadius: 6, marginBottom: 12,
        }}>{error}</div>
      )}

      {/* Best now card */}
      {best && (
        <div style={{
          background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
          padding: 16, marginBottom: 16,
        }}>
          <div style={{
            display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 12,
          }}>
            <span style={{ fontSize: 14, color: '#7a9bb5' }}>⭐ Best now</span>
            <span style={{ fontSize: 18, fontWeight: 700, color: '#fff' }}>
              Δ{best.target_delta.toFixed(2)} × {best.expiry}
            </span>
            <span style={{
              padding: '3px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              background: bandColor(best.size_band), color: '#000',
            }}>{best.size_band ?? '—'}</span>
            <span style={{
              padding: '3px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              background: sourceColor(best.quality_source), color: '#000',
            }}>{best.quality_source ?? '—'}</span>
          </div>

          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 16,
          }}>
            <Metric label="Quality" value={fmt(best.quality_score, 1)} />
            <Metric label="Credit %" value={fmtPct(best.credit_pct, 3)} />
            <Metric label="Credit USD" value={fmtUsd(best.credit_usd)} />
            <Metric label="DTE" value={fmt(best.dte_days, 2)} />
            <Metric label="Strikes (CE/PE)"
                   value={`${best.call_strike.toLocaleString()} / ${best.put_strike.toLocaleString()}`} />
            <Metric label="Pattern" value={best.pattern ?? '—'} />
            <Metric label="Overall WR" value={fmtPct(best.overall_winrate, 1)} />
            <Metric label="Pattern WR" value={fmtPct(best.pattern_winrate, 1)} />
            <Metric label="N trades" value={best.n_trades_in_bucket?.toString() ?? '—'} />
            <Metric label="Z-credit" value={fmt(best.z_credit_pct, 2)} />
            <Metric label="θ/V ratio" value={fmt(best.theta_vega_ratio, 3)} />
            <Metric label="Excess vs fair %" value={fmtPct(best.excess_over_fair_pct, 3)} />
          </div>

          {/* Hard-filter chips */}
          <div style={{
            display: 'flex', gap: 6, marginTop: 14, flexWrap: 'wrap',
          }}>
            <Chip label="IVP > 50" pass={best.flt_ivp_gt50} />
            <Chip label="IV > RV" pass={best.flt_iv_rv_spread_pos} />
            <Chip label="ADX < 30" pass={best.flt_adx_lt30} />
            <Chip label="DTE 5–14" pass={best.flt_dte_5_14} />
            <Chip label="GEX OK" pass={best.flt_gex_ok} />
            <span style={{
              marginLeft: 8, padding: '3px 10px', borderRadius: 12,
              fontSize: 11, fontWeight: 700,
              background: best.flt_all_pass ? '#064e3b' : '#3b1d1d',
              color: best.flt_all_pass ? '#10b981' : '#ef4444',
              border: `1px solid ${best.flt_all_pass ? '#10b981' : '#ef4444'}`,
            }}>
              {best.flt_all_pass ? 'ALL PASS' : 'INCOMPLETE'}
            </span>
          </div>
        </div>
      )}

      {/* Candidates table */}
      <div style={{
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        overflow: 'auto',
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: '#0a1018' }}>
              <Th onClick={() => onSort('expiry')}    sortKey={sortKey} mine="expiry"    desc={sortDesc}>Expiry</Th>
              <Th onClick={() => onSort('dte_days')}  sortKey={sortKey} mine="dte_days"  desc={sortDesc}>DTE</Th>
              <Th onClick={() => onSort('target_delta')} sortKey={sortKey} mine="target_delta" desc={sortDesc}>Δ</Th>
              <th style={thStyle}>CE Strike</th>
              <th style={thStyle}>PE Strike</th>
              <th style={thStyle}>Total Credit</th>
              <Th onClick={() => onSort('credit_pct')} sortKey={sortKey} mine="credit_pct" desc={sortDesc}>Credit %</Th>
              <Th onClick={() => onSort('quality_score')} sortKey={sortKey} mine="quality_score" desc={sortDesc}>Quality</Th>
              <th style={thStyle}>Source</th>
              <th style={thStyle}>Band</th>
              <th style={thStyle}>Pattern</th>
              <Th onClick={() => onSort('pattern_winrate')} sortKey={sortKey} mine="pattern_winrate" desc={sortDesc}>Pat WR</Th>
              <Th onClick={() => onSort('overall_winrate')} sortKey={sortKey} mine="overall_winrate" desc={sortDesc}>Overall WR</Th>
              <th style={thStyle}>n</th>
              <th style={thStyle}>Filters</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr><td colSpan={15} style={{ padding: 18, textAlign: 'center', color: '#7a9bb5' }}>
                {loading ? 'Loading…' : 'No candidates available.'}
              </td></tr>
            )}
            {sorted.map((c, i) => (
              <tr key={`${c.expiry}-${c.target_delta}`}
                  style={{
                    background: i % 2 === 0 ? '#0d1421' : '#0a1018',
                    borderTop: '1px solid #1a2d42',
                  }}>
                <td style={tdStyle}>{c.expiry}</td>
                <td style={tdStyle}>{fmt(c.dte_days, 2)}</td>
                <td style={tdStyle}>{c.target_delta.toFixed(2)}</td>
                <td style={tdStyle}>{c.call_strike.toLocaleString()}</td>
                <td style={tdStyle}>{c.put_strike.toLocaleString()}</td>
                <td style={tdStyle}>{fmt(c.total_credit, 1)}</td>
                <td style={tdStyle}>{fmtPct(c.credit_pct, 3)}</td>
                <td style={{ ...tdStyle, fontWeight: 700, color: '#fff' }}>{fmt(c.quality_score, 1)}</td>
                <td style={tdStyle}>
                  <span style={{
                    padding: '2px 6px', borderRadius: 8, fontSize: 10, fontWeight: 600,
                    background: sourceColor(c.quality_source), color: '#000',
                  }}>{c.quality_source ?? '—'}</span>
                </td>
                <td style={tdStyle}>
                  <span style={{
                    padding: '2px 6px', borderRadius: 8, fontSize: 10, fontWeight: 600,
                    background: bandColor(c.size_band), color: '#000',
                  }}>{c.size_band ?? '—'}</span>
                </td>
                <td style={tdStyle}>{c.pattern ?? '—'}</td>
                <td style={tdStyle}>{fmtPct(c.pattern_winrate, 1)}</td>
                <td style={tdStyle}>{fmtPct(c.overall_winrate, 1)}</td>
                <td style={tdStyle}>{c.n_trades_in_bucket ?? '—'}</td>
                <td style={tdStyle}>
                  <span style={{
                    color: c.flt_all_pass ? '#10b981' : '#ef4444', fontWeight: 700,
                  }}>
                    {[c.flt_ivp_gt50, c.flt_iv_rv_spread_pos, c.flt_adx_lt30,
                      c.flt_dte_5_14, c.flt_gex_ok].filter(Boolean).length}/5
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Bits ──────────────────────────────────────────────────────────────────────

const thStyle: React.CSSProperties = {
  padding: '8px 10px', textAlign: 'left',
  fontSize: 11, fontWeight: 600, color: '#7a9bb5',
  textTransform: 'uppercase', letterSpacing: 0.4,
  borderBottom: '1px solid #1a2d42',
};
const tdStyle: React.CSSProperties = { padding: '7px 10px' };

function Th({ children, onClick, sortKey, mine, desc }: {
  children: React.ReactNode; onClick: () => void;
  sortKey: SortKey; mine: SortKey; desc: boolean;
}) {
  const active = sortKey === mine;
  return (
    <th onClick={onClick} style={{
      ...thStyle, cursor: 'pointer',
      background: active ? '#142537' : undefined,
      color: active ? '#fff' : '#7a9bb5',
    }}>
      {children}{active ? (desc ? ' ▼' : ' ▲') : ''}
    </th>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: '#7a9bb5', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 600, color: '#cdd6e0' }}>{value}</div>
    </div>
  );
}

function Chip({ label, pass }: { label: string; pass: boolean }) {
  return (
    <span style={{
      padding: '3px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600,
      background: pass ? '#064e3b' : '#3b1d1d',
      color: flagColor(pass),
      border: `1px solid ${flagColor(pass)}`,
    }}>{pass ? '✓' : '✗'} {label}</span>
  );
}

export default LiveSignalDashboard;
