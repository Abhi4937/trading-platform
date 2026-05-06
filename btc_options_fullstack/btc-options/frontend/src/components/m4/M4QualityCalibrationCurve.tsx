/**
 * Empirical win-rate per credit_pct decile. Validates whether higher credit
 * actually correlates with higher win rate (proxy for quality calibration
 * since quality_score isn't stored in the parquet).
 */
import { useEffect, useState } from 'react';
import {
  CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts';
import { fetchQualityCalibration, type CalibrationBucket } from '../../services/m4_api';

interface Props { nBuckets?: number; title?: string; }

export function M4QualityCalibrationCurve({
  nBuckets = 10, title = 'Quality calibration — win-rate per credit_pct decile',
}: Props) {
  const [buckets, setBuckets] = useState<CalibrationBucket[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchQualityCalibration(nBuckets)
      .then(d => { if (!cancelled) { setBuckets(d.buckets); setError(null); } })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [nBuckets]);

  const data = buckets.map(b => ({
    bucket: b.bucket + 1,           // 1-indexed for display
    range: `${(b.credit_pct_min * 100).toFixed(2)}–${(b.credit_pct_max * 100).toFixed(2)}%`,
    win_rate_pct: Number((b.win_rate * 100).toFixed(2)),
    avg_pnl: Number(b.avg_net_pnl.toFixed(2)),
    n: b.n,
  }));

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 16, height: 320,
    }}>
      <div style={{
        fontSize: 13, fontWeight: 600, color: '#cdd6e0', marginBottom: 8,
      }}>{title}</div>
      {loading && <div style={{ color: '#7a9bb5' }}>Loading…</div>}
      {error && <div style={{ color: '#fca5a5' }}>{error}</div>}
      {!loading && !error && (
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1a2d42" />
            <XAxis dataKey="bucket" stroke="#7a9bb5" tick={{ fontSize: 11 }}
                   label={{ value: 'Credit % decile (low→high)',
                            position: 'insideBottom', offset: -8,
                            fill: '#7a9bb5', fontSize: 11 }} />
            <YAxis yAxisId="wr" stroke="#10b981" tick={{ fontSize: 11 }}
                   tickFormatter={v => `${v}%`} domain={[0, 100]} />
            <Tooltip
              contentStyle={{ background: '#0a1018', border: '1px solid #1a2d42', color: '#cdd6e0' }}
              labelFormatter={(label) => `Decile ${label}`}
              formatter={(value: number, name, item: any) => {
                const p = item?.payload;
                if (!p) return [value, name];
                return [
                  `${value.toFixed(1)}%  (n=${p.n}, range=${p.range})`,
                  'win rate',
                ];
              }} />
            <Line yAxisId="wr" type="monotone" dataKey="win_rate_pct"
                  stroke="#10b981" strokeWidth={2}
                  dot={{ r: 4, fill: '#10b981' }} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
      <div style={{ marginTop: 6, fontSize: 11, color: '#7a9bb5' }}>
        Monotonic increase = credit% predicts win rate. Drops at the highest
        decile usually mean ATM trades that frequently breach SL.
      </div>
    </div>
  );
}

export default M4QualityCalibrationCurve;
