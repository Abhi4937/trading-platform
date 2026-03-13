import React, { useEffect, useRef, useState } from 'react';
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
  const [legendData, setLegendData] = useState<any>(null);
  const [rulerData, setRulerData] = useState<any>(null);
  const isShiftPressed = useRef(false);
  const rulerStartPoint = useRef<any>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => { if (e.key === 'Shift') isShiftPressed.current = true; };
    const handleKeyUp = (e: KeyboardEvent) => { 
      if (e.key === 'Shift') {
        isShiftPressed.current = false;
        rulerStartPoint.current = null;
        setRulerData(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, []);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#080e16' },
        textColor: '#7a9bb5',
        padding: { left: 0, right: 0, top: 2, bottom: 0 },
      },
      grid: {
        vertLines: { color: '#131f2e' },
        horzLines: { color: '#131f2e' },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.06, bottom: 0.04 },
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 400,
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 0,
        horzLine: {
          width: 1,
          color: '#4a6a85',
          style: 3,
          labelBackgroundColor: '#0c1420',
        },
        vertLine: {
          width: 1,
          color: '#4a6a85',
          style: 3,
          labelBackgroundColor: '#0c1420',
        },
      },
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

    // Legend & Ruler Logic
    chart.subscribeCrosshairMove(param => {
      if (
        param.point === undefined ||
        !param.time ||
        param.point.x < 0 ||
        param.point.x > chartContainerRef.current!.clientWidth ||
        param.point.y < 0 ||
        param.point.y > chartContainerRef.current!.clientHeight
      ) {
        if (!rulerStartPoint.current && data.length > 0) {
          const last = data[data.length - 1];
          const pc = ((last.close - last.open) / last.open) * 100;
          setLegendData({ ...last, pc });
        }
      } else {
        const d = param.seriesData.get(series);
        if (d) {
          const o = (d as any).open;
          const c = (d as any).close;
          const h = (d as any).high;
          const l = (d as any).low;
          const pc = ((c - o) / o) * 100;
          setLegendData({ open: o, high: h, low: l, close: c, pc });

          // Ruler Logic
          if (isShiftPressed.current) {
            const price = series.coordinateToPrice(param.point.y);
            if (!rulerStartPoint.current) {
              rulerStartPoint.current = { time: param.time, price, x: param.point.x, y: param.point.y };
            } else {
              const deltaPrice = price! - rulerStartPoint.current.price;
              const deltaPct = (deltaPrice / rulerStartPoint.current.price) * 100;
              const bars = Math.abs(Number(param.time) - Number(rulerStartPoint.current.time)) / 60; 
              
              setRulerData({
                startX: rulerStartPoint.current.x,
                startY: rulerStartPoint.current.y,
                endX: param.point.x,
                endY: param.point.y,
                deltaPrice,
                deltaPct,
                bars: Math.round(bars)
              });
            }
          }
        }
      }
    });

    const resizeObserver = new ResizeObserver(entries => {
      if (entries.length === 0 || !entries[0].contentRect) return;
      const { width, height } = entries[0].contentRect;
      chart.applyOptions({ width, height });
    });

    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [data]);

  useEffect(() => {
    if (seriesRef.current && data && data.length > 0) {
      // Shift data to IST for display
      const istData = data.map(d => ({
        ...d,
        time: d.time + (5.5 * 3600)
      }));
      
      seriesRef.current.setData(istData);
      chartRef.current?.timeScale().fitContent();
      
      const last = istData[istData.length - 1];
      const pc = ((last.close - last.open) / last.open) * 100;
      setLegendData({ ...last, pc });
    }
  }, [data]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, position: 'relative' }}>
      <div ref={chartContainerRef} style={{ flex: 1, minHeight: 0, height: 0, position: 'relative' }}>
        {legendData && (
          <div style={{
            position: 'absolute', top: '8px', left: '8px', zIndex: 10,
            display: 'flex', gap: '10px', fontSize: '11px', fontFamily: 'var(--mono)',
            background: 'rgba(8,14,22,0.75)', padding: '4px 8px', borderRadius: '4px',
            pointerEvents: 'none',
          }}>
            <span style={{ color: 'var(--text3)' }}>O <span style={{ color: '#fff' }}>{legendData.open.toFixed(2)}</span></span>
            <span style={{ color: 'var(--text3)' }}>H <span style={{ color: '#00e5a0' }}>{legendData.high.toFixed(2)}</span></span>
            <span style={{ color: 'var(--text3)' }}>L <span style={{ color: '#ff4d6a' }}>{legendData.low.toFixed(2)}</span></span>
            <span style={{ color: 'var(--text3)' }}>C <span style={{ color: '#fff' }}>{legendData.close.toFixed(2)}</span></span>
            <span style={{ color: legendData.pc >= 0 ? '#00e5a0' : '#ff4d6a' }}>
              {legendData.pc >= 0 ? '+' : ''}{legendData.pc.toFixed(2)}%
            </span>
          </div>
        )}
        {rulerData && (
          <div style={{
            position: 'absolute',
            left: Math.min(rulerData.startX, rulerData.endX),
            top: Math.min(rulerData.startY, rulerData.endY),
            width: Math.abs(rulerData.endX - rulerData.startX),
            height: Math.abs(rulerData.endY - rulerData.startY),
            border: '1px dashed var(--accent)',
            background: 'rgba(0, 212, 255, 0.1)',
            pointerEvents: 'none',
            zIndex: 10,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontSize: '10px',
            fontFamily: 'var(--mono)',
            textAlign: 'center',
            padding: '4px',
            borderRadius: '2px'
          }}>
            <div>
              {rulerData.deltaPrice.toFixed(2)} ({rulerData.deltaPct.toFixed(2)}%)<br/>
              {rulerData.bars} mins
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
