import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { OHLCData } from '../../types/historical';

interface Props {
  data: OHLCData[];
  title: string;
}

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
      },
      grid: {
        vertLines: { color: '#1a2d42' },
        horzLines: { color: '#1a2d42' },
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 400,
      timeScale: {
        borderColor: '#1a2d42',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 0,
      }
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
              const bars = Math.abs(Number(param.time) - Number(rulerStartPoint.current.time)) / 60; // Assuming 1m bars for simplicity
              
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
      seriesRef.current.setData(data);
      chartRef.current?.timeScale().fitContent();
      const last = data[data.length - 1];
      const pc = ((last.close - last.open) / last.open) * 100;
      setLegendData({ ...last, pc });
    }
  }, [data]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, position: 'relative' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
        {title && <h3 style={{ fontSize: '12px', fontWeight: 'bold', color: 'var(--accent)' }}>{title}</h3>}
        
        {legendData && (
          <div style={{ display: 'flex', gap: '8px', fontSize: '11px', fontFamily: 'var(--mono)' }}>
            <span>O <span style={{ color: '#fff' }}>{legendData.open.toFixed(2)}</span></span>
            <span>H <span style={{ color: '#fff' }}>{legendData.high.toFixed(2)}</span></span>
            <span>L <span style={{ color: '#fff' }}>{legendData.low.toFixed(2)}</span></span>
            <span>C <span style={{ color: '#fff' }}>{legendData.close.toFixed(2)}</span></span>
            <span style={{ color: legendData.pc >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {legendData.pc >= 0 ? '+' : ''}{legendData.pc.toFixed(2)}%
            </span>
          </div>
        )}
      </div>

      <div ref={chartContainerRef} style={{ flex: 1, minHeight: '300px', position: 'relative' }}>
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
