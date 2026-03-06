import React, { useEffect, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  ResponsiveContainer, Legend,
} from 'recharts';
import { api } from '../../services/api';
import { Spinner } from '../ui/Spinner';
import type { OptionLeg } from '../../types/api';

const TIMEFRAMES = ['1m','5m','15m','1h','4h','1d'] as const;

interface Props {
  expiry: string;
  selectedLeg: OptionLeg | null;
}

export const PremiumChart: React.FC<Props> = ({ expiry, selectedLeg }) => {
  const [tf, setTf] = useState<string>('1h');
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedLeg && !expiry) return;
    const strike = selectedLeg?.strike ?? 0;
    const type = selectedLeg?.option_type ?? 'call';
    if (!strike) return;
    setLoading(true);
    setError(null);
    api.getPremiumChart(expiry, strike, type, tf)
      .then(res => {
        setData(res.candles.map(c => ({
          time: new Date(c.timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
          close: c.close,
          high: c.high,
          low: c.low,
          volume: c.volume,
        })));
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [selectedLeg, expiry, tf]);

  return (
    <div className="chart-card">
      <div className="chart-header">
        <div>
          <div className="chart-title">Premium Chart</div>
          <div className="chart-sub">
            {selectedLeg ? `${selectedLeg.symbol} · ${selectedLeg.option_type.toUpperCase()}` : 'Select a strike'}
          </div>
        </div>
        <div className="tf-group">
          {TIMEFRAMES.map(t => (
            <button
              key={t}
              className={`tf-btn ${tf === t ? 'active' : ''}`}
              onClick={() => setTf(t)}
            >{t}</button>
          ))}
        </div>
      </div>
      <div className="chart-body">
        {loading && <div className="chart-loading"><Spinner /></div>}
        {error && <div className="chart-error">{error}</div>}
        {!loading && !error && data.length > 0 && (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1a2d42" strokeDasharray="3 3" />
              <XAxis dataKey="time" tick={{ fill: '#4a6a85', fontSize: 10, fontFamily: 'JetBrains Mono' }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: '#4a6a85', fontSize: 10, fontFamily: 'JetBrains Mono' }} width={60} />
              <Tooltip
                contentStyle={{ background: '#0c1420', border: '1px solid #1a2d42', borderRadius: 6 }}
                labelStyle={{ color: '#e2eaf4', fontSize: 11 }}
                itemStyle={{ color: '#7a9bb5', fontSize: 11 }}
              />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono' }} />
              <Line type="monotone" dataKey="close" stroke="#00d4ff" dot={false} strokeWidth={2} name="Close" />
              <Line type="monotone" dataKey="high"  stroke="#00e5a0" dot={false} strokeWidth={1} strokeDasharray="3 3" name="High" />
              <Line type="monotone" dataKey="low"   stroke="#ff4d6a" dot={false} strokeWidth={1} strokeDasharray="3 3" name="Low" />
            </LineChart>
          </ResponsiveContainer>
        )}
        {!loading && !error && data.length === 0 && (
          <div className="chart-empty">Select a strike to view premium chart</div>
        )}
      </div>
    </div>
  );
};
