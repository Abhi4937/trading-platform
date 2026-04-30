import React from 'react';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine, Cell,
} from 'recharts';
import type { BacktestEquityPoint } from '../../types/backtest';

interface Props {
  data: BacktestEquityPoint[];
  height?: number;
}

export const BacktestDailyPnlBars: React.FC<Props> = ({ data, height = 220 }) => {
  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#131f2e" />
          <XAxis
            dataKey="date" stroke="#4a6a85" tick={{ fontSize: 10 }}
            tickFormatter={(d: string) => d.slice(5)}    // MM-DD
            angle={-30} textAnchor="end" height={40}
          />
          <YAxis
            stroke="#4a6a85" tick={{ fontSize: 10 }}
            tickFormatter={(v: number) => v.toFixed(0)}
          />
          <Tooltip
            contentStyle={{ background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 4 }}
            labelStyle={{ color: '#c9d1d9' }}
            itemStyle={{ color: '#c9d1d9' }}
            formatter={(v: number) => [`$${v.toFixed(2)}`, 'Daily P&L']}
          />
          <ReferenceLine y={0} stroke="#4a6a85" strokeWidth={1} />
          <Bar dataKey="daily_pnl">
            {data.map((d, i) => (
              <Cell key={i} fill={d.daily_pnl >= 0 ? '#00e5a0' : '#ff4d6a'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};
