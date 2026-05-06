/**
 * 2D heatmap of M4 win-rate (or any metric) across two dimensions.
 * CSS-grid implementation, no chart lib. Color scale: red→amber→green.
 */
import { useEffect, useState } from 'react';
import { fetchAggregate, type AggregateRow, type M4Filters } from '../../services/m4_api';

interface Props {
  rowDim: string;
  colDim: string;
  metric?: string;
  rowOrder?: string[];   // optional explicit row order
  colOrder?: string[];   // optional explicit col order
  filters?: M4Filters;
  title?: string;
  format?: (v: number) => string;
}

const defaultFmt = (v: number) => `${(v * 100).toFixed(0)}%`;

const colorFor = (v: number, min: number, max: number): string => {
  if (max === min) return '#3b3f4c';
  const t = (v - min) / (max - min);   // 0..1
  // red(0) → amber(0.5) → green(1)
  if (t < 0.5) {
    const k = t / 0.5;                  // 0..1 over red→amber
    const r = Math.round(239 + (240 - 239) * k);
    const g = Math.round(68 + (180 - 68) * k);
    const b = Math.round(68 + (41 - 68) * k);
    return `rgb(${r},${g},${b})`;
  }
  const k = (t - 0.5) / 0.5;            // 0..1 over amber→green
  const r = Math.round(240 + (16 - 240) * k);
  const g = Math.round(180 + (185 - 180) * k);
  const b = Math.round(41 + (129 - 41) * k);
  return `rgb(${r},${g},${b})`;
};

export function M4WinrateHeatmap({
  rowDim, colDim, metric = 'win_rate',
  rowOrder, colOrder, filters, title, format = defaultFmt,
}: Props) {
  const [rows, setRows] = useState<AggregateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchAggregate({ dimension: [rowDim, colDim], metric, filters })
      .then(d => { if (!cancelled) { setRows(d.rows); setError(null); } })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [rowDim, colDim, metric, JSON.stringify(filters ?? {})]);

  const allRowKeys = rowOrder ?? Array.from(new Set(
    rows.map(r => String(r[rowDim] ?? '')).filter(k => k !== 'nan' && k !== ''),
  )).sort();
  const allColKeys = colOrder ?? Array.from(new Set(
    rows.map(r => String(r[colDim] ?? '')).filter(k => k !== 'nan' && k !== ''),
  )).sort();

  const cellMap = new Map<string, AggregateRow>();
  rows.forEach(r => {
    cellMap.set(`${r[rowDim]}|${r[colDim]}`, r);
  });
  const values = rows
    .map(r => Number(r[metric]))
    .filter(Number.isFinite) as number[];
  const vMin = values.length ? Math.min(...values) : 0;
  const vMax = values.length ? Math.max(...values) : 1;

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 16,
    }}>
      {title && <div style={{
        fontSize: 13, fontWeight: 600, color: '#cdd6e0', marginBottom: 12,
      }}>{title}</div>}
      {loading && <div style={{ color: '#7a9bb5' }}>Loading…</div>}
      {error && <div style={{ color: '#fca5a5' }}>Error: {error}</div>}

      {!loading && !error && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: `auto repeat(${allColKeys.length}, 1fr)`,
          gap: 4, fontSize: 12,
        }}>
          {/* corner cell */}
          <div />
          {/* col headers */}
          {allColKeys.map(c => (
            <div key={c} style={{
              padding: 6, textAlign: 'center', color: '#7a9bb5',
              fontWeight: 600, textTransform: 'uppercase', fontSize: 10,
            }}>{c}</div>
          ))}
          {/* rows */}
          {allRowKeys.map(rKey => (
            <RowFragment
              key={rKey}
              label={rKey}
              cells={allColKeys.map(cKey => {
                const cell = cellMap.get(`${rKey}|${cKey}`);
                return cell;
              })}
              metric={metric}
              vMin={vMin} vMax={vMax} format={format}
            />
          ))}
        </div>
      )}
      <div style={{
        display: 'flex', gap: 12, marginTop: 12, alignItems: 'center',
        fontSize: 11, color: '#7a9bb5',
      }}>
        <span>row: <b>{rowDim}</b></span>
        <span>col: <b>{colDim}</b></span>
        <span>metric: <b>{metric}</b></span>
      </div>
    </div>
  );
}

function RowFragment({ label, cells, metric, vMin, vMax, format }: {
  label: string;
  cells: (AggregateRow | undefined)[];
  metric: string;
  vMin: number; vMax: number;
  format: (v: number) => string;
}) {
  return (
    <>
      <div style={{
        padding: 6, color: '#cdd6e0', fontWeight: 600, fontSize: 11,
        display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
      }}>{label}</div>
      {cells.map((cell, i) => {
        const v = cell ? Number(cell[metric]) : NaN;
        const isFinite = Number.isFinite(v);
        const bg = isFinite ? colorFor(v, vMin, vMax) : '#1a1f2c';
        return (
          <div key={i} style={{
            padding: 6, textAlign: 'center',
            background: bg, color: '#000', fontWeight: 700,
            borderRadius: 3, position: 'relative',
            cursor: cell ? 'help' : 'default',
          }}
          title={cell ? `n=${cell.n}` : 'no data'}
          >
            {isFinite ? format(v) : '–'}
            {cell && <span style={{
              display: 'block', fontSize: 9, fontWeight: 500, color: '#1a1f2c',
            }}>n={cell.n}</span>}
          </div>
        );
      })}
    </>
  );
}

export default M4WinrateHeatmap;
