import React, { useEffect, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  ResponsiveContainer, ReferenceLine, Legend,
} from 'recharts';
import { api } from '../../services/api';
import { Spinner } from '../ui/Spinner';

interface Props { expiry: string; atmStrike: number; }

const WINDOWS = [7, 14, 30] as const;

export const IVChart: React.FC<Props> = ({ expiry, atmStrike }) => {
  const [smileData, setSmileData] = useState<any[]>([]);
  const [rvData, setRvData] = useState<any[]>([]);
  const [rvWindow, setRvWindow] = useState<number>(30);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<'smile' | 'ivrv'>('smile');

  useEffect(() => {
    if (!expiry) return;
    setLoading(true);
    Promise.all([
      api.getIVSmile(expiry),
      api.getIVRV(expiry, rvWindow),
    ]).then(([smile, ivrv]) => {
      setSmileData(smile.points.map(p => ({
        strike: p.strike.toLocaleString(),
        callIV: p.call_iv,
        putIV: p.put_iv,
        moneyness: p.moneyness,
      })));
      setRvData(ivrv.series.map(p => ({
        label: p.label,
        iv: p.implied_vol,
        rv: p.realised_vol,
      })));
    }).catch(console.error).finally(() => setLoading(false));
  }, [expiry, rvWindow]);

  return (
    <div className="chart-card">
      <div className="chart-header">
        <div className="tab-group">
          <button className={`tab-btn ${tab==='smile'?'active':''}`} onClick={() => setTab('smile')}>IV Smile</button>
          <button className={`tab-btn ${tab==='ivrv'?'active':''}`} onClick={() => setTab('ivrv')}>IV vs RV</button>
        </div>
        {tab === 'ivrv' && (
          <div className="tf-group">
            {WINDOWS.map(w => (
              <button key={w} className={`tf-btn ${rvWindow===w?'active':''}`} onClick={() => setRvWindow(w)}>{w}d</button>
            ))}
          </div>
        )}
      </div>
      <div className="chart-body">
        {loading && <div className="chart-loading"><Spinner /></div>}
        {!loading && tab === 'smile' && (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={smileData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1a2d42" strokeDasharray="3 3" />
              <XAxis dataKey="strike" tick={{ fill: '#4a6a85', fontSize: 9, fontFamily: 'JetBrains Mono' }} interval={2} />
              <YAxis tick={{ fill: '#4a6a85', fontSize: 10, fontFamily: 'JetBrains Mono' }} unit="%" width={48} />
              <Tooltip
                contentStyle={{ background: '#0c1420', border: '1px solid #1a2d42', borderRadius: 6 }}
                labelStyle={{ color: '#e2eaf4', fontSize: 11 }}
                itemStyle={{ color: '#7a9bb5', fontSize: 11 }}
                formatter={(v: number) => [`${v.toFixed(1)}%`]}
              />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono' }} />
              <Line type="monotone" dataKey="callIV" stroke="#60a5fa" dot={false} strokeWidth={2} name="Call IV" />
              <Line type="monotone" dataKey="putIV"  stroke="#f87171" dot={false} strokeWidth={2} name="Put IV" />
              <ReferenceLine x={atmStrike.toLocaleString()} stroke="#00d4ff" strokeDasharray="4 2" label={{ value:'ATM', fill:'#00d4ff', fontSize:10 }} />
            </LineChart>
          </ResponsiveContainer>
        )}
        {!loading && tab === 'ivrv' && (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={rvData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1a2d42" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: '#4a6a85', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
              <YAxis tick={{ fill: '#4a6a85', fontSize: 10, fontFamily: 'JetBrains Mono' }} unit="%" width={48} />
              <Tooltip
                contentStyle={{ background: '#0c1420', border: '1px solid #1a2d42', borderRadius: 6 }}
                labelStyle={{ color: '#e2eaf4', fontSize: 11 }}
                itemStyle={{ color: '#7a9bb5', fontSize: 11 }}
                formatter={(v: number) => [`${v.toFixed(1)}%`]}
              />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono' }} />
              <Line type="monotone" dataKey="iv" stroke="#f0b429" dot={false} strokeWidth={2} name="Implied Vol" />
              <Line type="monotone" dataKey="rv" stroke="#00e5a0" dot={false} strokeWidth={2} strokeDasharray="4 2" name="Realised Vol" />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
};
