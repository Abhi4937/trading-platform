import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, BaselineSeries } from 'lightweight-charts';
import type { MtmPoint } from '../../types/strategy';

interface Props {
  data: MtmPoint[];
}

export const MtmChart: React.FC<Props> = ({ data }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
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
        horzLine: {
          width: 1, color: '#4a6a85', style: 3,
          labelVisible: true, labelBackgroundColor: '#1a2a3a',
        },
        vertLine: {
          width: 1, color: '#4a6a85', style: 3,
          labelVisible: true, labelBackgroundColor: '#1a2a3a',
        },
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

    // ── Crosshair tooltip ──────────────────────────────────────────────────
    chart.subscribeCrosshairMove(param => {
      const tooltip = tooltipRef.current;
      if (!tooltip) return;

      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tooltip.style.display = 'none';
        return;
      }

      const seriesData = param.seriesData.get(series) as { value?: number } | undefined;
      const pnl = seriesData?.value;
      if (pnl === undefined) { tooltip.style.display = 'none'; return; }

      // Format time (param.time is already IST-offset unix)
      const ts = param.time as number;
      const d = new Date(ts * 1000);
      const hh = String(d.getUTCHours()).padStart(2, '0');
      const mm = String(d.getUTCMinutes()).padStart(2, '0');
      const dd = String(d.getUTCDate()).padStart(2, '0');
      const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
      const timeStr = `${dd}/${mo} ${hh}:${mm}`;

      const isPos = pnl >= 0;
      tooltip.innerHTML = `
        <div style="font-size:10px;color:var(--text3);margin-bottom:2px">${timeStr} IST</div>
        <div style="font-size:13px;font-weight:700;color:${isPos ? '#00e5a0' : '#ff4d6a'}">
          ${isPos ? '+' : ''}${pnl.toFixed(2)} <span style="font-size:9px;font-weight:400">USD</span>
        </div>
      `;

      // Position: right side of chart if cursor is in left half, else left side
      const containerWidth = containerRef.current?.clientWidth ?? 400;
      const left = param.point.x < containerWidth / 2
        ? param.point.x + 14
        : param.point.x - 110;

      tooltip.style.display = 'block';
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${Math.max(0, param.point.y - 30)}px`;
    });

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
    <div style={{ flex: 1, minHeight: 0, height: 0, position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <div
        ref={tooltipRef}
        style={{
          display: 'none',
          position: 'absolute',
          pointerEvents: 'none',
          background: 'rgba(8,14,22,0.9)',
          border: '1px solid #1e3048',
          borderRadius: '4px',
          padding: '5px 8px',
          whiteSpace: 'nowrap',
          zIndex: 10,
        }}
      />
    </div>
  );
};
