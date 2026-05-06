/**
 * Recharts ScatterChart wrapper. Plots m4_trades against any two numeric
 * columns. Color-coded by `outcome` by default.
 */
import { useEffect, useState } from 'react';
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip,
  XAxis, YAxis, ZAxis,
} from 'recharts';
import { fetchScatter, type M4Filters, type ScatterPoint } from '../../services/m4_api';

interface Props {
  x: string;
  y: string;
  colorBy?: string;
  filters?: M4Filters;
  title?: string;
  xLabel?: string;
  yLabel?: string;
  xFormatter?: (v: number) => string;
  yFormatter?: (v: number) => string;
}

const winColor = '#10b981';
const lossColor = '#ef4444';

export function M4ScatterChart({
  x, y, colorBy = 'outcome', filters, title,
  xLabel = x, yLabel = y, xFormatter, yFormatter,
}: Props) {
  const [points, setPoints] = useState<ScatterPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchScatter({ x, y, colorBy, filters, limit: 2000 })
      .then(d => { if (!cancelled) { setPoints(d.points); setError(null); } })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [x, y, colorBy, JSON.stringify(filters ?? {})]);

  const wins = points.filter(p => p[colorBy] === 'win');
  const losses = points.filter(p => p[colorBy] === 'loss');

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 16, height: 360,
    }}>
      <div style={{
        fontSize: 13, fontWeight: 600, color: '#cdd6e0', marginBottom: 8,
        display: 'flex', justifyContent: 'space-between',
      }}>
        <span>{title ?? `${yLabel} vs ${xLabel}`}</span>
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>n={points.length}</span>
      </div>
      {loading && <div style={{ color: '#7a9bb5' }}>Loading…</div>}
      {error && <div style={{ color: '#fca5a5' }}>{error}</div>}
      {!loading && !error && (
        <ResponsiveContainer width="100%" height={290}>
          <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1a2d42" />
            <XAxis
              type="number" dataKey={x}
              name={xLabel}
              stroke="#7a9bb5" tick={{ fontSize: 11 }}
              tickFormatter={xFormatter}
              label={{ value: xLabel, position: 'insideBottom', offset: -8,
                       fill: '#7a9bb5', fontSize: 11 }}
            />
            <YAxis
              type="number" dataKey={y}
              name={yLabel}
              stroke="#7a9bb5" tick={{ fontSize: 11 }}
              tickFormatter={yFormatter}
              label={{ value: yLabel, angle: -90, position: 'insideLeft',
                       fill: '#7a9bb5', fontSize: 11 }}
            />
            <ZAxis range={[16, 16]} />
            <Tooltip cursor={{ strokeDasharray: '3 3' }}
                     contentStyle={{ background: '#0a1018', border: '1px solid #1a2d42', color: '#cdd6e0' }} />
            <Scatter data={wins}   fill={winColor}  fillOpacity={0.55} />
            <Scatter data={losses} fill={lossColor} fillOpacity={0.55} />
          </ScatterChart>
        </ResponsiveContainer>
      )}
      <div style={{ display: 'flex', gap: 16, marginTop: 4, fontSize: 11, color: '#7a9bb5' }}>
        <span>● <span style={{ color: winColor }}>win</span></span>
        <span>● <span style={{ color: lossColor }}>loss</span></span>
      </div>
    </div>
  );
}

export default M4ScatterChart;
