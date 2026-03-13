import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, BaselineSeries } from 'lightweight-charts';
import type { MtmPoint } from '../../types/strategy';

interface Props {
  data: MtmPoint[];
}

export const MtmChart: React.FC<Props> = ({ data }) => {
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
      height: containerRef.current.clientHeight || 220,
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 0,
        horzLine: { width: 1, color: '#4a6a85', style: 3, labelBackgroundColor: '#0c1420' },
        vertLine: { width: 1, color: '#4a6a85', style: 3, labelBackgroundColor: '#0c1420' },
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
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(entries => {
      if (!entries.length || !entries[0].contentRect) return;
      const { width, height } = entries[0].contentRect;
      chart.applyOptions({ width, height });
    });
    ro.observe(containerRef.current);

    return () => { ro.disconnect(); chart.remove(); };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !data.length) return;
    const IST_OFFSET = 5.5 * 3600;
    const istData = data.map(d => ({ time: d.time + IST_OFFSET, value: d.pnl }));
    seriesRef.current.setData(istData);
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return (
    <div ref={containerRef} style={{ flex: 1, minHeight: 0, height: 0 }} />
  );
};
