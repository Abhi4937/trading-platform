import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, BaselineSeries } from 'lightweight-charts';
import type { BacktestEquityPoint } from '../../types/backtest';

interface Props {
  data: BacktestEquityPoint[];
  height?: number;
}

export const BacktestEquityChart: React.FC<Props> = ({ data, height = 280 }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#080e16' },
        textColor: '#7a9bb5',
      },
      grid: {
        vertLines: { color: '#131f2e' },
        horzLines: { color: '#131f2e' },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      width: containerRef.current.clientWidth,
      height,
      timeScale: {
        borderVisible: false,
        timeVisible: false,
        secondsVisible: false,
      },
      crosshair: {
        mode: 0,
        horzLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
        vertLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
      },
    });

    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#00e5a0',
      topFillColor1: 'rgba(0, 229, 160, 0.25)',
      topFillColor2: 'rgba(0, 229, 160, 0.04)',
      bottomLineColor: '#ff4d6a',
      bottomFillColor1: 'rgba(255, 77, 106, 0.04)',
      bottomFillColor2: 'rgba(255, 77, 106, 0.25)',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const onResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', onResize);

    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    // lightweight-charts BusinessDay 'time' is best for daily bars
    const points = data.map(p => ({ time: p.date as any, value: p.cum_pnl }));
    seriesRef.current.setData(points);
    if (chartRef.current && points.length) chartRef.current.timeScale().fitContent();
  }, [data]);

  return (
    <div style={{ position: 'relative', width: '100%', height }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {data.length === 0 && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          color: '#4a6a85', fontSize: 13, pointerEvents: 'none',
        }}>
          No equity points yet
        </div>
      )}
    </div>
  );
};
