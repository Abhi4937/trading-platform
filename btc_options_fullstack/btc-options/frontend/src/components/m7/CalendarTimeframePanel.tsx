import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7Aggregate, fetchM7Meta } from '../../services/m7_api';
import { M7AggregateHeatmap } from './M7AggregateHeatmap';
import type { M7ExitRule, M7Filters } from '../../types/m7';

// Calendar "best time frame" + combination panels. Two heatmaps grounded on a
// selected gap bucket:
//   • Entry-hour heatmap — entry hour (0–23 IST) × (Δ or pair), colored by metric.
//     Answers "best time of day to enter" for a backwardation calendar.
//   • Pair × Δ heatmap — all 10 expiry pairs × 3 deltas, colored by metric.
// Both are scoped to one gap bucket via a THIRD aggregate dimension filtered
// client-side (gap labels like "[5,10)" contain commas the /aggregate filter
// parser would shred — see M7AggregateHeatmap.scopeKey).
const DATASET = 'calendar' as const;

type Metric = 'avg_pct_return_on_margin' | 'avg_net_pnl' | 'win_rate' | 'count';

const METRIC_OPTS: { key: Metric; label: string }[] = [
  { key: 'avg_pct_return_on_margin', label: '% Return / Margin' },
  { key: 'avg_net_pnl', label: 'Avg net P&L' },
  { key: 'win_rate', label: 'Win rate' },
  { key: 'count', label: 'Trade count' },
];

function fmtFor(metric: Metric): (v: number) => string {
  if (metric === 'win_rate') return (v) => `${(v * 100).toFixed(1)}%`;
  if (metric === 'avg_pct_return_on_margin') return (v) => `${v.toFixed(2)}%`;
  if (metric === 'count') return (v) => String(Math.round(v));
  return (v) => `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(1)}`; // avg_net_pnl
}

// Sort gap buckets low→high by their lower bound ("[5,10)" → 5).
function sortGaps(vals: string[]): string[] {
  const lb = (v: string) => {
    const m = v.match(/^\[(\d+),/);
    return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
  };
  return [...vals].sort((a, b) => lb(a) - lb(b) || a.localeCompare(b));
}

// ── Leg entry-exposure ────────────────────────────────────────────────────────
// True per-leg realized P&L is NOT stored on the calendar trades parquet, so
// this shows where the EXPOSURE sits at ENTRY: mean per-leg entry premium and
// delta (near CE/PE, far CE/PE) per pair, for the selected gap bucket. It is
// NOT a P&L attribution — labeled as such.
const LEG_METRICS = [
  'avg_call_entry_mark', 'avg_put_entry_mark',
  'avg_far_call_entry_mark', 'avg_far_put_entry_mark',
  'avg_call_entry_delta', 'avg_put_entry_delta',
] as const;

interface LegRow {
  pair: string;
  nearCE: number | null; nearPE: number | null;
  farCE: number | null; farPE: number | null;
  nearCEdelta: number | null; nearPEdelta: number | null;
  n: number;
}

function LegExposureTable({ gap, filters, exitRule }: {
  gap: string; filters: M7Filters; exitRule: M7ExitRule;
}) {
  const [rows, setRows] = useState<LegRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!gap) { setRows([]); return; }
    setLoading(true); setErr(null);
    Promise.all(LEG_METRICS.map(m =>
      fetchM7Aggregate(
        { ...filters, dimensions: 'expiry_bucket,entry_atm_iv_band', metric: m },
        exitRule, DATASET,
      ).then(r => ({ metric: m, rows: r.rows })),
    ))
      .then(results => {
        // pair -> { metric -> value, n }
        const byPair = new Map<string, LegRow>();
        for (const { metric, rows: rws } of results) {
          for (const x of rws) {
            if (String(x.entry_atm_iv_band) !== gap) continue;
            const pair = String(x.expiry_bucket);
            const cur = byPair.get(pair) ?? {
              pair, nearCE: null, nearPE: null, farCE: null, farPE: null,
              nearCEdelta: null, nearPEdelta: null, n: 0,
            };
            const v = x.value == null ? null : Number(x.value);
            if (metric === 'avg_call_entry_mark') cur.nearCE = v;
            else if (metric === 'avg_put_entry_mark') cur.nearPE = v;
            else if (metric === 'avg_far_call_entry_mark') cur.farCE = v;
            else if (metric === 'avg_far_put_entry_mark') cur.farPE = v;
            else if (metric === 'avg_call_entry_delta') cur.nearCEdelta = v;
            else if (metric === 'avg_put_entry_delta') cur.nearPEdelta = v;
            cur.n = Math.max(cur.n, Number(x.n_trades) || 0);
            byPair.set(pair, cur);
          }
        }
        setRows([...byPair.values()].sort((a, b) => a.pair.localeCompare(b.pair)));
      })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [gap, JSON.stringify(filters), JSON.stringify(exitRule)]);

  const usd = (v: number | null) => v == null ? '—' : `$${v.toFixed(1)}`;
  // Premium tilt of the SOLD near pair: (CE − PE)/(CE + PE). >0 → CE richer.
  const tilt = (ce: number | null, pe: number | null) => {
    if (ce == null || pe == null || (ce + pe) === 0) return null;
    return (ce - pe) / (ce + pe);
  };
  const tiltStr = (t: number | null) =>
    t == null ? '—' : `${t > 0 ? 'CE' : 'PE'} +${Math.abs(t * 100).toFixed(0)}%`;

  const thS: React.CSSProperties = { padding: '4px 8px', color: '#7a9bb5', textAlign: 'right', whiteSpace: 'nowrap' };
  const tdS: React.CSSProperties = { padding: '4px 8px', textAlign: 'right', color: '#cfd9e3' };

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ fontSize: 12, color: '#cfd9e3', fontWeight: 600, marginBottom: 4 }}>
        Leg entry-exposure — gap {gap || '—'}{' '}
        <span style={{ fontSize: 10, color: '#d29922', fontWeight: 400 }}>
          (entry premium &amp; delta per leg — NOT realized per-leg P&amp;L)
        </span>
        <span style={{ fontSize: 11, color: '#7a9bb5', fontWeight: 400, marginLeft: 8 }}>
          {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span> : `${rows.length} pairs`}
        </span>
      </div>
      {!loading && !err && rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1a2d42' }}>
                <th style={{ ...thS, textAlign: 'left' }}>Pair</th>
                <th style={thS}>Near CE prem</th>
                <th style={thS}>Near PE prem</th>
                <th style={thS}>Far CE prem</th>
                <th style={thS}>Far PE prem</th>
                <th style={thS}>Near tilt</th>
                <th style={thS}>Near |Δ| CE/PE</th>
                <th style={thS}>n</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.pair} style={{ borderBottom: '1px solid #131c28' }}>
                  <td style={{ ...tdS, textAlign: 'left', color: '#cfd9e3', fontWeight: 600 }}>{r.pair}</td>
                  <td style={tdS}>{usd(r.nearCE)}</td>
                  <td style={tdS}>{usd(r.nearPE)}</td>
                  <td style={tdS}>{usd(r.farCE)}</td>
                  <td style={tdS}>{usd(r.farPE)}</td>
                  <td style={tdS}>{tiltStr(tilt(r.nearCE, r.nearPE))}</td>
                  <td style={tdS}>
                    {r.nearCEdelta == null || r.nearPEdelta == null ? '—'
                      : `${Math.abs(r.nearCEdelta).toFixed(2)}/${Math.abs(r.nearPEdelta).toFixed(2)}`}
                  </td>
                  <td style={{ ...tdS, color: '#7a9bb5' }}>{r.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const ctl: React.CSSProperties = {
  background: '#0d1421', color: '#cfd9e3', border: '1px solid #1a2d42',
  borderRadius: 4, padding: '3px 8px', fontSize: 12,
};
const lab: React.CSSProperties = { fontSize: 11, color: '#7a9bb5', marginRight: 4 };

export function CalendarTimeframePanel({ filters, exitRule }: {
  filters: M7Filters; exitRule: M7ExitRule;
}) {
  const [gapBuckets, setGapBuckets] = useState<string[]>([]);
  const [gap, setGap] = useState<string>('');
  const [metric, setMetric] = useState<Metric>('avg_pct_return_on_margin');
  const [hourCol, setHourCol] = useState<'delta_target' | 'expiry_bucket'>('delta_target');

  useEffect(() => {
    fetchM7Meta(DATASET)
      .then(m => {
        const gaps = sortGaps(m.iv_bands ?? []);
        setGapBuckets(gaps);
        // Default to the largest-sample narrow band if present, else first.
        setGap(prev => prev || (gaps.includes('[5,10)') ? '[5,10)' : (gaps[0] ?? '')));
      })
      .catch(() => { /* selector simply stays empty; heatmaps show 0 cells */ });
  }, []);

  const fmt = useMemo(() => fmtFor(metric), [metric]);

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 10, marginBottom: 10,
    }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', marginBottom: 10 }}>
        <div style={{ fontSize: 13, color: '#cfd9e3', fontWeight: 600 }}>
          Best time frame &amp; combinations
        </div>
        <div>
          <span style={lab}>Gap bucket</span>
          <select style={ctl} value={gap} onChange={e => setGap(e.target.value)}>
            {gapBuckets.length === 0 && <option value="">…</option>}
            {gapBuckets.map(g => <option key={g} value={g}>{g}</option>)}
          </select>
        </div>
        <div>
          <span style={lab}>Metric</span>
          <select style={ctl} value={metric} onChange={e => setMetric(e.target.value as Metric)}>
            {METRIC_OPTS.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </div>
        <div>
          <span style={lab}>Hour heatmap by</span>
          <select style={ctl} value={hourCol} onChange={e => setHourCol(e.target.value as typeof hourCol)}>
            <option value="delta_target">Δ target</option>
            <option value="expiry_bucket">Pair</option>
          </select>
        </div>
      </div>

      <M7AggregateHeatmap
        title={`Entry hour (IST) × ${hourCol === 'delta_target' ? 'Δ' : 'pair'} — gap ${gap || '—'}`}
        rowKey="entry_hour_actual"
        colKey={hourCol}
        rowLabel="Entry hour (IST)"
        colLabel={hourCol === 'delta_target' ? 'Δ' : 'Pair'}
        metric={metric}
        fmt={fmt}
        filters={filters}
        exitRule={exitRule}
        dataset={DATASET}
        scopeKey="entry_atm_iv_band"
        scopeVal={gap}
      />

      <M7AggregateHeatmap
        title={`Pair × Δ — gap ${gap || '—'}`}
        rowKey="expiry_bucket"
        colKey="delta_target"
        rowLabel="Pair"
        colLabel="Δ"
        metric={metric}
        fmt={fmt}
        filters={filters}
        exitRule={exitRule}
        dataset={DATASET}
        scopeKey="entry_atm_iv_band"
        scopeVal={gap}
      />

      <LegExposureTable gap={gap} filters={filters} exitRule={exitRule} />
    </div>
  );
}
