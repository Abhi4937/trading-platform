import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, LineSeries, UTCTimestamp } from 'lightweight-charts';

export interface GreeksSeriesData {
  id: string;
  label: string;
  color: string;
  data: { time: number; value: number }[];
}

interface Props {
  series: GreeksSeriesData[];
  decimals?: number;
  unit?: string;
}

const IST_OFFSET = 5.5 * 3600;

export const GreeksChart: React.FC<Props> = ({ series, decimals = 2, unit = '' }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRefs = useRef<Map<string, any>>(new Map());
  const seriesInfoRef = useRef<GreeksSeriesData[]>(series);
  const rafRef = useRef<number | null>(null);

  useEffect(() => { seriesInfoRef.current = series; }, [series]);

  useEffect(() => {
    if (!containerRef.current) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
      seriesRefs.current = new Map();
    }
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    if (!series.length || !series.some(s => s.data.length)) return;

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
      width: containerRef.current.clientWidth || 400,
      height: containerRef.current.clientHeight || 140,
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 0,
        horzLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
        vertLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
      },
    });

    chartRef.current = chart;

    series.forEach(s => {
      const sr = chart.addSeries(LineSeries, {
        color: s.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 3,
      });
      seriesRefs.current.set(s.id, sr);
      if (s.data.length) {
        sr.setData(s.data.map(d => ({ time: (d.time + IST_OFFSET) as UTCTimestamp, value: d.value })));
      }
    });

    rafRef.current = requestAnimationFrame(() => { chart.timeScale().fitContent(); });

    chart.subscribeCrosshairMove(param => {
      const tooltip = tooltipRef.current;
      if (!tooltip) return;
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tooltip.style.display = 'none';
        return;
      }
      const ts = param.time as number;
      const d = new Date(ts * 1000);
      const timeStr = `${String(d.getUTCDate()).padStart(2,'0')}/${String(d.getUTCMonth()+1).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;

      const rows = seriesInfoRef.current.map(s => {
        const sr = seriesRefs.current.get(s.id);
        if (!sr) return '';
        const sd = param.seriesData.get(sr) as { value?: number } | undefined;
        if (sd?.value === undefined) return '';
        return `<div style="display:flex;justify-content:space-between;gap:12px;font-size:10px;line-height:1.6">
          <span style="color:${s.color}">${s.label}</span>
          <span style="color:#e2e8f0;font-weight:700">${sd.value.toFixed(decimals)}${unit}</span>
        </div>`;
      }).filter(Boolean).join('');

      if (!rows) { tooltip.style.display = 'none'; return; }
      tooltip.innerHTML = `<div style="font-size:9px;color:#4a6a85;margin-bottom:3px">${timeStr} IST</div>${rows}`;
      const containerWidth = containerRef.current?.clientWidth ?? 400;
      const left = param.point.x < containerWidth / 2 ? param.point.x + 14 : param.point.x - 170;
      tooltip.style.display = 'block';
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${Math.max(0, param.point.y - 30)}px`;
    });

    const ro = new ResizeObserver(entries => {
      if (!entries.length || !entries[0].contentRect) return;
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) {
        chart.applyOptions({ width, height });
        requestAnimationFrame(() => chart.timeScale().fitContent());
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      chart.remove();
      chartRef.current = null;
      seriesRefs.current = new Map();
    };
  }, [series, decimals, unit]);

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <div
        ref={tooltipRef}
        style={{
          display: 'none',
          position: 'absolute',
          pointerEvents: 'none',
          background: 'rgba(8,14,22,0.92)',
          border: '1px solid #1e3048',
          borderRadius: '4px',
          padding: '5px 8px',
          whiteSpace: 'nowrap',
          zIndex: 10,
          minWidth: '160px',
        }}
      />
    </div>
  );
};
