import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { OHLCData } from '../../types/historical';

interface Props {
  data: OHLCData[];
  title: string;
}

export const HistoricalChart: React.FC<Props> = ({ data, title }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0c1420' },
        textColor: '#e2eaf4',
      },
      grid: {
        vertLines: { color: '#1a2d42' },
        horzLines: { color: '#1a2d42' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 400,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#00e5a0',
      downColor: '#ff4d6a',
      borderVisible: false,
      wickUpColor: '#00e5a0',
      wickDownColor: '#ff4d6a',
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (seriesRef.current && data.length > 0) {
      seriesRef.current.setData(data);
      chartRef.current?.timeScale().fitContent();
    }
  }, [data]);

  return (
    <div className="w-full flex flex-col gap-2">
      <h3 className="text-lg font-bold text-[#00d4ff]">{title}</h3>
      <div ref={chartContainerRef} className="w-full h-[400px]" />
    </div>
  );
};
