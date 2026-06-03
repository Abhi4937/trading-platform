import React, { useEffect, useRef, useState } from 'react';
import {
  createChart, IChartApi, LineSeries, BaselineSeries, CandlestickSeries,
} from 'lightweight-charts';
import { fetchM7Path, type M7Dataset } from '../../services/m7_api';
import type { M7PathResponse } from '../../types/m7';
import {
  emaSeries, rsiSeries, bbandsSeries, atrSeries, macdSeries,
} from '../../utils/indicators';

interface TradeContextResp {
  trade_id: string;
  entry_ts: number;
  exit_ts: number;
  spot_at_entry: number;
  loss_cause: string | null;
  expected_move_1sigma_7d_at_entry: number | null;
  expected_move_1sigma_14d_at_entry: number | null;
  expected_move_1sigma_30d_at_entry: number | null;
  ohlc: { time: number; open: number; high: number; low: number; close: number; volume: number }[];
  iv_series: { time: number; atm_iv: number | null; call_iv: number | null; put_iv: number | null }[];
  greeks_series: { time: number; net_delta: number | null; theta_per_vega: number | null }[];
}

type IndicatorKey = 'ema_20' | 'ema_50' | 'rsi_14' | 'macd' | 'bbands_20_2' | 'atr_14';

function lossCauseColor(c: string | null | undefined): string {
  if (!c) return '#1a2d42';
  return ({
    directional:    '#f85149',
    vol_expansion:  '#d29922',
    path_dependent: '#bf6fde',
    gamma_squeeze:  '#ff7b72',
    skew_flip:      '#79c0ff',
    unclassified:   '#586e7e',
  } as Record<string, string>)[c] || '#7a9bb5';
}

export function M7TradePathChart({ tradeId, onClose, dataset }: {
  tradeId: string; onClose: () => void; dataset?: M7Dataset;
}) {
  const isCal = dataset === 'calendar';
  const wrap = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [data, setData] = useState<M7PathResponse | null>(null);
  const [ctx, setCtx] = useState<TradeContextResp | null>(null);
  const [view, setView] = useState<'pnl' | 'premium' | 'iv' | 'delta' | 'spot'>('pnl');
  const [indicators, setIndicators] = useState<Set<IndicatorKey>>(
    () => new Set<IndicatorKey>(['ema_20', 'rsi_14', 'bbands_20_2']),
  );

  useEffect(() => {
    fetchM7Path(tradeId, dataset).then(setData).catch(e => console.error(e));
    fetch(`/api/v1/m7/trade_context_ohlc?trade_id=${encodeURIComponent(tradeId)}` +
          `&pad_minutes_before=120&pad_minutes_after=30`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setCtx).catch(e => console.error('trade_context_ohlc:', e));
  }, [tradeId, dataset]);

  useEffect(() => {
    if (!wrap.current) return;
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

    const chart = createChart(wrap.current, {
      width: wrap.current.clientWidth, height: 420,
      layout: { background: { color: '#0a0e17' }, textColor: '#cfd9e3' },
      grid: { vertLines: { color: '#131c28' }, horzLines: { color: '#131c28' } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    if (view === 'spot' && ctx && ctx.ohlc.length > 0) {
      // Candlesticks
      const candle = chart.addSeries(CandlestickSeries, {
        upColor: '#3fb950', downColor: '#f85149',
        borderUpColor: '#3fb950', borderDownColor: '#f85149',
        wickUpColor: '#3fb950', wickDownColor: '#f85149',
      });
      candle.setData(ctx.ohlc.map(b => ({
        time: b.time as any, open: b.open, high: b.high, low: b.low, close: b.close,
      })));

      // Vertical entry/exit markers via priceLines on the candle series.
      // Lightweight-charts doesn't have a true vertical line primitive, so
      // we use the entry-spot horizontal line with axis label as a visual anchor.
      candle.createPriceLine({
        price: ctx.spot_at_entry,
        color: '#cfd9e3', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: true, title: `entry $${ctx.spot_at_entry.toFixed(0)}`,
      });

      // Expected-move 1σ envelope (7d tenor) drawn as ± bands around entry spot.
      const em7 = ctx.expected_move_1sigma_7d_at_entry;
      if (em7 != null && Number.isFinite(em7)) {
        candle.createPriceLine({
          price: ctx.spot_at_entry + em7,
          color: '#56d364', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: `+1σ 7d`,
        });
        candle.createPriceLine({
          price: ctx.spot_at_entry - em7,
          color: '#f97583', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: `-1σ 7d`,
        });
      }

      // Indicator overlays on the price pane (EMA, BBands).
      if (indicators.has('ema_20')) {
        const series = chart.addSeries(LineSeries, { color: '#79c0ff', lineWidth: 1 });
        series.setData(emaSeries(ctx.ohlc, 20).map(p => ({ time: p.time as any, value: p.value })));
      }
      if (indicators.has('ema_50')) {
        const series = chart.addSeries(LineSeries, { color: '#bf6fde', lineWidth: 1 });
        series.setData(emaSeries(ctx.ohlc, 50).map(p => ({ time: p.time as any, value: p.value })));
      }
      if (indicators.has('bbands_20_2')) {
        const bb = bbandsSeries(ctx.ohlc, 20, 2);
        const upper = chart.addSeries(LineSeries, { color: 'rgba(127,127,127,0.4)', lineWidth: 1 });
        const lower = chart.addSeries(LineSeries, { color: 'rgba(127,127,127,0.4)', lineWidth: 1 });
        upper.setData(bb.map(p => ({ time: p.time as any, value: p.upper })));
        lower.setData(bb.map(p => ({ time: p.time as any, value: p.lower })));
      }

      // RSI / MACD / ATR each get their own pane.
      if (indicators.has('rsi_14')) {
        const rsiData = rsiSeries(ctx.ohlc, 14).map(p => ({ time: p.time as any, value: p.value }));
        const rsi = chart.addSeries(LineSeries, {
          color: '#d29922', lineWidth: 1, priceScaleId: 'rsi',
        });
        rsi.setData(rsiData);
        chart.priceScale('rsi').applyOptions({
          scaleMargins: { top: 0.75, bottom: 0.0 },
          borderVisible: false,
        });
      }
      if (indicators.has('macd')) {
        const md = macdSeries(ctx.ohlc, 12, 26, 9);
        const macdLine = chart.addSeries(LineSeries, {
          color: '#79c0ff', lineWidth: 1, priceScaleId: 'macd',
        });
        const sigLine = chart.addSeries(LineSeries, {
          color: '#f0a040', lineWidth: 1, priceScaleId: 'macd',
        });
        macdLine.setData(md.map(p => ({ time: p.time as any, value: p.macd })));
        sigLine.setData(md.map(p => ({ time: p.time as any, value: p.signal })));
        chart.priceScale('macd').applyOptions({
          scaleMargins: { top: 0.85, bottom: 0.0 },
          borderVisible: false,
        });
      }
      if (indicators.has('atr_14')) {
        const atrLine = chart.addSeries(LineSeries, {
          color: '#fb8c34', lineWidth: 1, priceScaleId: 'atr',
        });
        atrLine.setData(atrSeries(ctx.ohlc, 14).map(p => ({ time: p.time as any, value: p.value })));
        chart.priceScale('atr').applyOptions({
          scaleMargins: { top: 0.92, bottom: 0.0 },
          borderVisible: false,
        });
      }
    } else if (view === 'pnl' && data) {
      const s = chart.addSeries(BaselineSeries, {
        baseValue: { type: 'price', price: 0 },
        topFillColor1: 'rgba(63,185,80,0.4)', topFillColor2: 'rgba(63,185,80,0)',
        topLineColor: '#3fb950',
        bottomFillColor1: 'rgba(248,81,73,0)', bottomFillColor2: 'rgba(248,81,73,0.4)',
        bottomLineColor: '#f85149', lineWidth: 2,
      });
      s.setData(data.rows.map(r => ({ time: r.ts as any, value: r.gross_pnl_usd })));
    } else if (view === 'premium' && data) {
      const c = chart.addSeries(LineSeries, { color: '#3fb950', lineWidth: 1 });
      const p = chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1 });
      // Calendar paths don't carry per-leg marks — skip missing points cleanly.
      c.setData(data.rows.filter(r => r.call_mark != null).map(r => ({ time: r.ts as any, value: r.call_mark })));
      p.setData(data.rows.filter(r => r.put_mark != null).map(r => ({ time: r.ts as any, value: r.put_mark })));
    } else if (view === 'iv' && data) {
      if (isCal) {
        // Calendar double calendar: overlay near-IV, far-IV, and the gap (near − far).
        const near = chart.addSeries(LineSeries, { color: '#f0b300', lineWidth: 2 });
        const far = chart.addSeries(LineSeries, { color: '#1f6feb', lineWidth: 2 });
        const gap = chart.addSeries(LineSeries, { color: '#7d4cdb', lineWidth: 1, lineStyle: 2 });
        near.setData(data.rows.filter(r => r.near_iv != null).map(r => ({ time: r.ts as any, value: (r.near_iv as number) * 100 })));
        far.setData(data.rows.filter(r => r.far_iv != null).map(r => ({ time: r.ts as any, value: (r.far_iv as number) * 100 })));
        gap.setData(data.rows.filter(r => r.iv_gap != null).map(r => ({ time: r.ts as any, value: (r.iv_gap as number) * 100 })));
      } else {
        const c = chart.addSeries(LineSeries, { color: '#3fb950', lineWidth: 1 });
        const p = chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1 });
        const a = chart.addSeries(LineSeries, { color: '#1f6feb', lineWidth: 2 });
        c.setData(data.rows.map(r => ({ time: r.ts as any, value: r.call_iv * 100 })));
        p.setData(data.rows.map(r => ({ time: r.ts as any, value: r.put_iv * 100 })));
        a.setData(data.rows.map(r => ({ time: r.ts as any, value: r.atm_iv_now * 100 })));
      }
    } else if (view === 'delta' && data) {
      const c = chart.addSeries(LineSeries, { color: '#3fb950', lineWidth: 1 });
      const p = chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1 });
      const n = chart.addSeries(LineSeries, { color: '#1f6feb', lineWidth: 2 });
      // Calendar paths don't carry per-leg greeks — skip missing points cleanly.
      c.setData(data.rows.filter(r => r.call_delta != null).map(r => ({ time: r.ts as any, value: r.call_delta })));
      p.setData(data.rows.filter(r => r.put_delta != null).map(r => ({ time: r.ts as any, value: r.put_delta })));
      n.setData(data.rows.filter(r => r.net_delta != null).map(r => ({ time: r.ts as any, value: r.net_delta })));
    }
    chart.timeScale().fitContent();
    return () => { chart.remove(); chartRef.current = null; };
  }, [data, ctx, view, indicators]);

  const btn = (key: typeof view): React.CSSProperties => ({
    padding: '4px 10px', fontSize: 11, cursor: 'pointer',
    background: view === key ? '#1f6feb' : '#0d1421',
    color: view === key ? '#fff' : '#7a9bb5',
    border: '1px solid #1a2d42', borderRadius: 4,
  });
  const indBtn = (k: IndicatorKey, l: string): React.CSSProperties => ({
    padding: '3px 8px', fontSize: 10, cursor: 'pointer',
    background: indicators.has(k) ? '#1f6feb' : '#0d1421',
    color: indicators.has(k) ? '#fff' : '#7a9bb5',
    border: '1px solid #1a2d42', borderRadius: 3,
  });
  const toggleIndicator = (k: IndicatorKey) => {
    const next = new Set(indicators);
    if (next.has(k)) next.delete(k); else next.add(k);
    setIndicators(next);
  };

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
          <div style={{ fontSize: 14, fontWeight: 700, color: '#cfd9e3', display: 'flex', alignItems: 'center', gap: 10 }}>
            <span>Trade {tradeId} — 1m path ({data?.n_rows ?? '…'} bars)</span>
            {ctx?.loss_cause && (
              <span style={{
                background: lossCauseColor(ctx.loss_cause), color: '#0a0e17',
                padding: '2px 8px', borderRadius: 3, fontSize: 11, fontWeight: 700,
              }}>
                LOSS: {ctx.loss_cause}
              </span>
            )}
          </div>
          <button onClick={onClose} style={{
            padding: '4px 10px', background: '#1a2d42', color: '#cfd9e3',
            border: 'none', borderRadius: 4, cursor: 'pointer',
          }}>Close ✕</button>
        </div>

        <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
          <button style={btn('pnl')} onClick={() => setView('pnl')}>Gross P&L</button>
          {/* Calendar paths record only near/far IV + gap + spot — no per-leg
              premium/Δ series, and the OHLC context fetch isn't dataset-aware —
              so hide those tabs for calendar (they'd render empty). */}
          {!isCal && <button style={btn('premium')} onClick={() => setView('premium')}>Premium (CE/PE)</button>}
          <button style={btn('iv')} onClick={() => setView('iv')}>{isCal ? 'IV (near/far/gap)' : 'IV (CE/PE/ATM)'}</button>
          {!isCal && <button style={btn('delta')} onClick={() => setView('delta')}>Δ (CE/PE/Net)</button>}
          {!isCal && <button style={btn('spot')} onClick={() => setView('spot')} title="Spot candlesticks + indicators">Spot + Indicators</button>}
        </div>

        {view === 'spot' && (
          <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
            <span style={{ color: '#7a9bb5', fontSize: 10, alignSelf: 'center' }}>Indicators:</span>
            <button style={indBtn('ema_20',     'EMA 20')} onClick={() => toggleIndicator('ema_20')}>EMA 20</button>
            <button style={indBtn('ema_50',     'EMA 50')} onClick={() => toggleIndicator('ema_50')}>EMA 50</button>
            <button style={indBtn('rsi_14',     'RSI 14')} onClick={() => toggleIndicator('rsi_14')}>RSI 14</button>
            <button style={indBtn('macd',       'MACD')}   onClick={() => toggleIndicator('macd')}>MACD</button>
            <button style={indBtn('bbands_20_2','BB 20/2')} onClick={() => toggleIndicator('bbands_20_2')}>BB 20/2</button>
            <button style={indBtn('atr_14',     'ATR 14')} onClick={() => toggleIndicator('atr_14')}>ATR 14</button>
          </div>
        )}

        <div ref={wrap} style={{ width: '100%', height: 420 }} />
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
            {isCal ? (
              <Stat label="Gap change (entry → exit)"
                    value={`${(((data.rows[data.rows.length - 1].iv_gap ?? 0) - (data.rows[0].iv_gap ?? 0)) * 100).toFixed(2)} pts`} />
            ) : (
              <Stat label="ATM IV change (entry → exit)"
                    value={`${((data.rows[data.rows.length - 1].atm_iv_now - data.rows[0].atm_iv_now) * 100).toFixed(2)} pts`} />
            )}
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
