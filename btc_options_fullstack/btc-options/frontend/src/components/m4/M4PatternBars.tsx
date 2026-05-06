/**
 * Pattern → win-rate bar chart. Recharts BarChart wrapper.
 */
import { useEffect, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { fetchAggregate, type AggregateRow, type M4Filters } from '../../services/m4_api';

interface Props {
  filters?: M4Filters;
  title?: string;
}

const PATTERN_COLOR: Record<string, string> = {
  A: '#3b82f6', B: '#ef4444', C: '#6b7280', D: '#f0b429', Other: '#475569',
};

export function M4PatternBars({ filters, title = 'Pattern → Win Rate' }: Props) {
  const [rows, setRows] = useState<AggregateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchAggregate({ dimension: ['ctx_pattern'], metric: 'win_rate', filters })
      .then(d => { if (!cancelled) { setRows(d.rows); setError(null); } })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [JSON.stringify(filters ?? {})]);

  // Sort A,B,C,D,Other; drop nulls; convert win_rate to percentage
  const data = rows
    .filter(r => r.ctx_pattern && r.ctx_pattern !== 'nan')
    .map(r => ({
      pattern: String(r.ctx_pattern),
      win_rate_pct: Number(r.win_rate) * 100,
      n: r.n,
    }))
    .sort((a, b) => a.pattern.localeCompare(b.pattern));

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 16, height: 280,
    }}>
      <div style={{
        fontSize: 13, fontWeight: 600, color: '#cdd6e0', marginBottom: 8,
      }}>{title}</div>
      {loading && <div style={{ color: '#7a9bb5' }}>Loading…</div>}
      {error && <div style={{ color: '#fca5a5' }}>{error}</div>}
      {!loading && !error && (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1a2d42" />
            <XAxis dataKey="pattern" stroke="#7a9bb5" tick={{ fontSize: 12 }} />
            <YAxis stroke="#7a9bb5" tick={{ fontSize: 11 }}
                   tickFormatter={v => `${v}%`} domain={[0, 100]} />
            <Tooltip
              contentStyle={{ background: '#0a1018', border: '1px solid #1a2d42', color: '#cdd6e0' }}
              formatter={(value: number, _name, item: any) => [
                `${value.toFixed(1)}%  (n=${item?.payload?.n ?? '?'})`,
                'win rate',
              ]}
            />
            <Bar dataKey="win_rate_pct" fill="#3b82f6"
                 shape={(props: any) => {
                   const { x, y, width, height, payload } = props;
                   const fill = PATTERN_COLOR[payload.pattern] || '#3b82f6';
                   return <rect x={x} y={y} width={width} height={height} fill={fill} rx={2} />;
                 }} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default M4PatternBars;
