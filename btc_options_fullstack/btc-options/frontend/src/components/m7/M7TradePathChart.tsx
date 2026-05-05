import React, { useEffect, useRef, useState } from 'react';
import { createChart, IChartApi, LineSeries, BaselineSeries } from 'lightweight-charts';
import { fetchM7Path } from '../../services/m7_api';
import type { M7PathResponse } from '../../types/m7';

export function M7TradePathChart({ tradeId, onClose }: {
  tradeId: string; onClose: () => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [data, setData] = useState<M7PathResponse | null>(null);
  const [view, setView] = useState<'pnl' | 'premium' | 'iv' | 'delta'>('pnl');

  useEffect(() => {
    fetchM7Path(tradeId).then(setData).catch(e => console.error(e));
  }, [tradeId]);

  useEffect(() => {
    if (!wrap.current || !data) return;
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    const chart = createChart(wrap.current, {
      width: wrap.current.clientWidth, height: 360,
      layout: { background: { color: '#0a0e17' }, textColor: '#cfd9e3' },
      grid: { vertLines: { color: '#131c28' }, horzLines: { color: '#131c28' } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    if (view === 'pnl') {
      const s = chart.addSeries(BaselineSeries, {
        baseValue: { type: 'price', price: 0 },
        topFillColor1: 'rgba(63,185,80,0.4)', topFillColor2: 'rgba(63,185,80,0)',
        topLineColor: '#3fb950',
        bottomFillColor1: 'rgba(248,81,73,0)', bottomFillColor2: 'rgba(248,81,73,0.4)',
        bottomLineColor: '#f85149', lineWidth: 2,
      });
      s.setData(data.rows.map(r => ({ time: r.ts as any, value: r.gross_pnl_usd })));
    } else if (view === 'premium') {
      const c = chart.addSeries(LineSeries, { color: '#3fb950', lineWidth: 1 });
      const p = chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1 });
      c.setData(data.rows.map(r => ({ time: r.ts as any, value: r.call_mark })));
      p.setData(data.rows.map(r => ({ time: r.ts as any, value: r.put_mark })));
    } else if (view === 'iv') {
      const c = chart.addSeries(LineSeries, { color: '#3fb950', lineWidth: 1 });
      const p = chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1 });
      const a = chart.addSeries(LineSeries, { color: '#1f6feb', lineWidth: 2 });
      c.setData(data.rows.map(r => ({ time: r.ts as any, value: r.call_iv * 100 })));
      p.setData(data.rows.map(r => ({ time: r.ts as any, value: r.put_iv * 100 })));
      a.setData(data.rows.map(r => ({ time: r.ts as any, value: r.atm_iv_now * 100 })));
    } else {
      const c = chart.addSeries(LineSeries, { color: '#3fb950', lineWidth: 1 });
      const p = chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1 });
      const n = chart.addSeries(LineSeries, { color: '#1f6feb', lineWidth: 2 });
      c.setData(data.rows.map(r => ({ time: r.ts as any, value: r.call_delta })));
      p.setData(data.rows.map(r => ({ time: r.ts as any, value: r.put_delta })));
      n.setData(data.rows.map(r => ({ time: r.ts as any, value: r.net_delta })));
    }
    chart.timeScale().fitContent();
    return () => { chart.remove(); chartRef.current = null; };
  }, [data, view]);

  const btn = (key: typeof view, label: string): React.CSSProperties => ({
    padding: '4px 10px', fontSize: 11, cursor: 'pointer',
    background: view === key ? '#1f6feb' : '#0d1421',
    color: view === key ? '#fff' : '#7a9bb5',
    border: '1px solid #1a2d42', borderRadius: 4,
  });

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 200,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 8,
        padding: 16, width: 'min(96vw, 1100px)', maxHeight: '92vh', overflowY: 'auto',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#cfd9e3' }}>
            Trade {tradeId} — 1m path ({data?.n_rows ?? '…'} bars)
          </div>
          <button onClick={onClose} style={{
            padding: '4px 10px', background: '#1a2d42', color: '#cfd9e3',
            border: 'none', borderRadius: 4, cursor: 'pointer',
          }}>Close ✕</button>
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <button style={btn('pnl', 'P&L')} onClick={() => setView('pnl')}>Gross P&L</button>
          <button style={btn('premium', 'Premium')} onClick={() => setView('premium')}>Premium (CE/PE)</button>
          <button style={btn('iv', 'IV')} onClick={() => setView('iv')}>IV (CE/PE/ATM)</button>
          <button style={btn('delta', 'Delta')} onClick={() => setView('delta')}>Δ (CE/PE/Net)</button>
        </div>
        <div ref={wrap} style={{ width: '100%', height: 360 }} />
        {data && data.rows.length > 0 && (
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginTop: 12,
            fontSize: 11, color: '#cfd9e3', fontVariantNumeric: 'tabular-nums',
          }}>
            <Stat label="Final gross P&L"
                  value={`$${data.rows[data.rows.length - 1].gross_pnl_usd.toFixed(2)}`} />
            <Stat label="Max gross P&L"
                  value={`$${Math.max(...data.rows.map(r => r.gross_pnl_usd)).toFixed(2)}`} />
            <Stat label="Min gross P&L"
                  value={`$${Math.min(...data.rows.map(r => r.gross_pnl_usd)).toFixed(2)}`} />
            <Stat label="ATM IV change (entry → exit)"
                  value={`${((data.rows[data.rows.length - 1].atm_iv_now - data.rows[0].atm_iv_now) * 100).toFixed(2)} pts`} />
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      padding: '6px 10px', background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 4,
    }}>
      <div style={{ fontSize: 9, color: '#7a9bb5', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 13, color: '#cfd9e3', fontWeight: 600, marginTop: 2 }}>{value}</div>
    </div>
  );
}
