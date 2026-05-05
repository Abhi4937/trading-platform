import React from 'react';
import type { M7Summary } from '../../types/m7';

export function M7HeadlineStrip({ summary, loading }: { summary: M7Summary | null; loading: boolean }) {
  const cell: React.CSSProperties = {
    flex: 1, padding: '10px 14px', background: '#0d1421',
    border: '1px solid #1a2d42', borderRadius: 6,
  };
  const lab: React.CSSProperties = {
    fontSize: 10, color: '#7a9bb5', textTransform: 'uppercase', letterSpacing: 0.5,
    fontWeight: 600,
  };
  const val: React.CSSProperties = {
    fontSize: 18, color: '#cfd9e3', marginTop: 4, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
  };
  const fmtUsd = (v: number | null | undefined) => {
    if (v == null || isNaN(v)) return '—';
    const sign = v < 0 ? '-' : '';
    return `${sign}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  };
  const winColor = summary && summary.win_rate >= 0.5 ? '#3fb950' : '#f85149';
  const pnlColor = summary && summary.avg_net_pnl_usd >= 0 ? '#3fb950' : '#f85149';
  return (
    <div style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
      <div style={cell}><div style={lab}>Trades</div>
        <div style={val}>{loading ? '…' : (summary?.n_trades ?? 0).toLocaleString()}</div></div>
      <div style={cell}><div style={lab}>Wins</div>
        <div style={val}>{loading ? '…' : (summary?.n_wins ?? 0).toLocaleString()}</div></div>
      <div style={cell}><div style={lab}>Win rate</div>
        <div style={{ ...val, color: winColor }}>
          {loading ? '…' : summary ? `${(summary.win_rate * 100).toFixed(1)}%` : '—'}</div></div>
      <div style={cell}><div style={lab}>Avg net P&L</div>
        <div style={{ ...val, color: pnlColor }}>
          {loading ? '…' : fmtUsd(summary?.avg_net_pnl_usd)}</div></div>
      <div style={cell}><div style={lab}>Total net P&L</div>
        <div style={{ ...val, color: pnlColor }}>{loading ? '…' : fmtUsd(summary?.total_net_pnl_usd)}</div></div>
      <div style={cell}><div style={lab}>Avg credit</div>
        <div style={val}>{loading ? '…' : fmtUsd(summary?.avg_credit_usd)}</div></div>
      <div style={cell}><div style={lab}>Avg margin</div>
        <div style={val}>{loading ? '…' : fmtUsd(summary?.avg_margin_usd)}</div></div>
    </div>
  );
}
