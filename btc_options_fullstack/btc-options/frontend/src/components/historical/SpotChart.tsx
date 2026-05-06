/**
 * SpotChart — multi-pane lightweight-charts.
 *
 *  Pane 0 (candles): OHLC + overlay indicators (EMA, SMA, Bollinger Bands)
 *  Pane 1+         : one pane per non-overlay indicator (RSI, MACD, ATR, VWAP)
 *
 *  All panes share one time scale; crosshair syncs automatically.
 *  IST offset is added to each timestamp so the chart renders in IST.
 */

import React, { useEffect, useMemo, useRef } from 'react';
import {
  createChart, ColorType, CandlestickSeries, LineSeries, HistogramSeries,
  UTCTimestamp,
} from 'lightweight-charts';
import type {
  SpotOhlcBar, IndicatorConfig, IndicatorPoint,
  IndicatorMacdPoint, IndicatorBbandsPoint,
} from '../../types/historical';

const IST = 5.5 * 3600;

export type IndicatorSeriesMap = Record<string, IndicatorPoint[]>;

interface Props {
  ohlc: SpotOhlcBar[];
  configs: IndicatorConfig[];                // active indicator configs (drives panes)
  indicators: IndicatorSeriesMap;            // backend response keyed by stable id
  height?: number;                           // total chart height
  /** When true, use the candlestick price for "premium" labeling (cosmetic). */
  premiumMode?: boolean;
}

type IndicatorClass = 'overlay' | 'pane';

function classifyIndicator(t: string): IndicatorClass {
  // Overlays sit on the price pane. Everything else gets its own pane.
  if (t === 'sma' || t === 'ema' || t === 'bbands' || t === 'vwap') return 'overlay';
  return 'pane';
}

function indicatorIdFor(cfg: IndicatorConfig): string {
  const t = cfg.type;
  const p = cfg.params || {};
  if (t === 'sma' || t === 'ema' || t === 'rsi' || t === 'atr') return `${t}_${p.period ?? 0}`;
  if (t === 'bbands') return `bbands_${p.period ?? 20}_${p.stdev ?? 2}`;
  if (t === 'macd') return `macd_${p.fast ?? 12}_${p.slow ?? 26}_${p.signal ?? 9}`;
  return t;
}

function indicatorLabel(cfg: IndicatorConfig): string {
  const t = cfg.type.toUpperCase();
  const p = cfg.params || {};
  if (cfg.type === 'sma' || cfg.type === 'ema' || cfg.type === 'rsi' || cfg.type === 'atr')
    return `${t}(${p.period ?? 0})`;
  if (cfg.type === 'bbands') return `BB(${p.period ?? 20}, ${p.stdev ?? 2})`;
  if (cfg.type === 'macd')   return `MACD(${p.fast ?? 12}, ${p.slow ?? 26}, ${p.signal ?? 9})`;
  return t;
}

const PALETTE = ['#58a6ff', '#ffa940', '#9d7cd8', '#36c5a8', '#f87171', '#facc15', '#ec4899', '#22d3ee'];

export const SpotChart: React.FC<Props> = ({
  ohlc, configs, indicators, height = 480, premiumMode = false,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);

  // Stable color assignment by config index, unless cfg.color overrides
  const configColors = useMemo(
    () => configs.map((c, i) => c.color ?? PALETTE[i % PALETTE.length]),
    [configs],
  );

  // Build / rebuild the chart whenever inputs change.
  useEffect(() => {
    if (!containerRef.current) return;
    containerRef.current.innerHTML = '';

    const overlayConfigs = configs.filter(c => classifyIndicator(c.type) === 'overlay');
    const paneConfigs    = configs.filter(c => classifyIndicator(c.type) === 'pane');

    const candleH = Math.max(180, height - paneConfigs.length * 110);
    const paneH = 110;

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: '#080e16' }, textColor: '#7a9bb5' },
      grid: { vertLines: { color: '#131f2e' }, horzLines: { color: '#131f2e' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      width: containerRef.current.clientWidth || 600,
      height,
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: 0,
        horzLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
        vertLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
      },
    });
    chartRef.current = chart;

    // Pane 0 — Candlesticks
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: '#00e5a0', downColor: '#ff4d6a',
      borderUpColor: '#00e5a0', borderDownColor: '#ff4d6a',
      wickUpColor: '#00e5a0', wickDownColor: '#ff4d6a',
      priceLineVisible: false, lastValueVisible: true,
    }, 0);
    candle.setData(ohlc.map(b => ({
      time: (b.time + IST) as UTCTimestamp,
      open: b.open, high: b.high, low: b.low, close: b.close,
    })));

    // Overlay indicators on pane 0
    overlayConfigs.forEach((cfg, idx) => {
      const id = indicatorIdFor(cfg);
      const cfgIdxInAll = configs.indexOf(cfg);
      const color = configColors[cfgIdxInAll] ?? PALETTE[idx % PALETTE.length];
      const data = indicators[id] ?? [];
      if (cfg.type === 'bbands') {
        const points = data as IndicatorBbandsPoint[];
        const upper = chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: 2, priceLineVisible: false }, 0);
        const mid   = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false }, 0);
        const lower = chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: 2, priceLineVisible: false }, 0);
        upper.setData(points.filter(p => p.upper != null).map(p => ({ time: (p.time + IST) as UTCTimestamp, value: p.upper! })));
        mid.setData(  points.filter(p => p.mid   != null).map(p => ({ time: (p.time + IST) as UTCTimestamp, value: p.mid! })));
        lower.setData(points.filter(p => p.lower != null).map(p => ({ time: (p.time + IST) as UTCTimestamp, value: p.lower! })));
      } else {
        const s = chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false }, 0);
        s.setData((data as { time: number; value: number }[]).map(p => ({
          time: (p.time + IST) as UTCTimestamp, value: p.value,
        })));
      }
    });

    // Pane 1+ — non-overlay indicators
    paneConfigs.forEach((cfg, paneIdx) => {
      const id = indicatorIdFor(cfg);
      const cfgIdxInAll = configs.indexOf(cfg);
      const color = configColors[cfgIdxInAll] ?? PALETTE[paneIdx % PALETTE.length];
      const paneNo = paneIdx + 1;
      chart.addPane();
      const data = indicators[id] ?? [];

      if (cfg.type === 'macd') {
        const points = data as IndicatorMacdPoint[];
        const macdLine = chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false }, paneNo);
        const sigLine  = chart.addSeries(LineSeries, { color: '#ffa940', lineWidth: 1, lineStyle: 2, priceLineVisible: false }, paneNo);
        const hist     = chart.addSeries(HistogramSeries, { color: 'rgba(80,160,200,0.5)', priceLineVisible: false }, paneNo);
        macdLine.setData(points.filter(p => p.macd   != null).map(p => ({ time: (p.time + IST) as UTCTimestamp, value: p.macd! })));
        sigLine.setData( points.filter(p => p.signal != null).map(p => ({ time: (p.time + IST) as UTCTimestamp, value: p.signal! })));
        hist.setData(    points.filter(p => p.hist   != null).map(p => ({
          time: (p.time + IST) as UTCTimestamp,
          value: p.hist!,
          color: (p.hist! >= 0) ? 'rgba(0,229,160,0.55)' : 'rgba(255,77,106,0.55)',
        })));
        // Zero line
        macdLine.createPriceLine({ price: 0, color: 'rgba(150,150,150,0.35)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });
      } else if (cfg.type === 'rsi') {
        const points = data as { time: number; value: number }[];
        const s = chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false }, paneNo);
        s.setData(points.map(p => ({ time: (p.time + IST) as UTCTimestamp, value: p.value })));
        // Reference lines at 30/70
        s.createPriceLine({ price: 30, color: 'rgba(255,77,106,0.4)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' });
        s.createPriceLine({ price: 70, color: 'rgba(0,229,160,0.4)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' });
      } else {
        const points = data as { time: number; value: number }[];
        const s = chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false }, paneNo);
        s.setData(points.map(p => ({ time: (p.time + IST) as UTCTimestamp, value: p.value })));
      }
    });

    chart.timeScale().fitContent();

    // Pane sizing — defer to next frame so the chart has laid out.
    requestAnimationFrame(() => {
      try {
        chart.panes()[0]?.setHeight(candleH);
        for (let i = 0; i < paneConfigs.length; i++) {
          chart.panes()[i + 1]?.setHeight(paneH);
        }
      } catch {}
    });

    // Resize observer
    const ro = new ResizeObserver(() => {
      const w = containerRef.current?.clientWidth ?? 600;
      chart.applyOptions({ width: w });
    });
    ro.observe(containerRef.current);

    return () => { ro.disconnect(); chart.remove(); };
  }, [ohlc, configs, indicators, configColors, height]);

  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42',
      borderRadius: 6, padding: 8,
    }}>
      {/* Legend strip — shows active indicators */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 6,
        fontSize: 11, color: '#7a9bb5',
      }}>
        <span style={{ color: '#c9d1d9', fontWeight: 600 }}>
          {premiumMode ? 'Premium' : 'BTC Spot'}
        </span>
        {configs.map((c, i) => (
          <span key={indicatorIdFor(c) + '_' + i} style={{
            color: configColors[i],
            background: '#080e16', borderRadius: 3, padding: '2px 6px',
            border: `1px solid ${configColors[i]}40`,
          }}>
            {indicatorLabel(c)}
          </span>
        ))}
      </div>
      <div ref={containerRef} style={{ width: '100%', height }} />
    </div>
  );
};
