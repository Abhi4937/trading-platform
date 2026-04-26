/**
 * MultiPaneChart — single lightweight-charts instance, up to 3 panes.
 * All panes share one time scale → crosshair syncs automatically.
 *
 * Pane 0 — MTM P&L
 *   Build mode:   BaselineSeries (single strategy, green/red fill)
 *   Compare mode: LineSeries per strategy (mtmSeries prop)
 * Pane 1 — IV%   (LineSeries per leg, toggleable)
 * Pane 2 — Delta (LineSeries per leg, toggleable)
 *
 * Anti-flicker: chart structure is rebuilt only when pane layout changes
 * (toggle, leg count, first data). Data is updated in-place via setData().
 */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, ColorType, BaselineSeries, LineSeries, UTCTimestamp } from 'lightweight-charts';
import type { MtmPoint } from '../../types/strategy';

export interface PaneSeries {
  id: string;
  label: string;
  color: string;
  data: { time: number; value: number }[];
}

interface Props {
  mtmData?: MtmPoint[];      // build mode: single baseline
  mtmSeries?: PaneSeries[];  // compare mode: multiple coloured lines
  ivSeries: PaneSeries[];
  deltaSeries: PaneSeries[];
  chartsOnly?: boolean;
  onToggleChartsOnly?: () => void;
}

const IST      = 5.5 * 3600;
const PANE_MIN = 60;
const DEF_MTM  = 220;
const DEF_GRK  = 140;
const HDR_H    = 24;

export const MultiPaneChart: React.FC<Props> = ({
  mtmData, mtmSeries,
  ivSeries, deltaSeries,
  chartsOnly = false,
  onToggleChartsOnly,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef   = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<any>(null);

  // Series refs — read in crosshair callback and data effects without re-renders
  const mtmBaselineRef = useRef<any>(null);
  const mtmLineRefs    = useRef<Map<string, any>>(new Map());
  const ivRefsMap      = useRef<Map<string, any>>(new Map());
  const deltaRefsMap   = useRef<Map<string, any>>(new Map());

  // Info refs — kept in sync for use inside stable closures
  const mtmSeriesInfo  = useRef<PaneSeries[]>([]);
  const ivSeriesInfo   = useRef<PaneSeries[]>([]);
  const deltaSeriesInfo= useRef<PaneSeries[]>([]);
  useEffect(() => { mtmSeriesInfo.current  = mtmSeries  ?? []; }, [mtmSeries]);
  useEffect(() => { ivSeriesInfo.current   = ivSeries;         }, [ivSeries]);
  useEffect(() => { deltaSeriesInfo.current= deltaSeries;      }, [deltaSeries]);

  const [showIv,    setShowIv]    = useState(false);
  const [showDelta, setShowDelta] = useState(false);
  const [mtmH,   setMtmH]   = useState(DEF_MTM);
  const [ivH,    setIvH]    = useState(DEF_GRK);
  const [deltaH, setDeltaH] = useState(DEF_GRK);

  // Height refs — captured at mousedown to prevent cumulative drag bug
  const mtmHRef   = useRef(DEF_MTM);
  const ivHRef    = useRef(DEF_GRK);
  const deltaHRef = useRef(DEF_GRK);
  mtmHRef.current   = mtmH;
  ivHRef.current    = ivH;
  deltaHRef.current = deltaH;

  // Derived flags used as effect deps (primitives → no reference instability)
  const hasMtmData   = (mtmData?.length   ?? 0) > 0;
  const hasMtmSeries = (mtmSeries?.length ?? 0) > 0;
  const hasMtm       = hasMtmData || hasMtmSeries;
  const nIv          = ivSeries.length;
  const nDelta       = deltaSeries.length;

  // ── STRUCTURE EFFECT ──────────────────────────────────────────────────────
  // Rebuilds only when pane layout changes (toggles, leg count, first data).
  // Data is injected separately to avoid flickering on every parent re-render.
  useEffect(() => {
    if (!containerRef.current) return;
    if (!hasMtm && nIv === 0 && nDelta === 0) return;

    // Tear down previous instance
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    mtmBaselineRef.current = null;
    mtmLineRefs.current    = new Map();
    ivRefsMap.current      = new Map();
    deltaRefsMap.current   = new Map();

    const visibleIv    = showIv    && nIv    > 0;
    const visibleDelta = showDelta && nDelta > 0;
    const totalH = (hasMtm ? mtmH : 0) + (visibleIv ? ivH : 0) + (visibleDelta ? deltaH : 0);
    if (totalH <= 0) return;

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: '#080e16' }, textColor: '#7a9bb5' },
      grid: { vertLines: { color: '#131f2e' }, horzLines: { color: '#131f2e' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      width: containerRef.current.clientWidth || 400,
      height: totalH,
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: 0,
        horzLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
        vertLine: { width: 1, color: '#4a6a85', style: 3, labelVisible: true, labelBackgroundColor: '#1a2a3a' },
      },
    });
    chartRef.current = chart;
    let nextPane = 0;

    // ── MTM pane ──────────────────────────────────────────────────────────
    if (hasMtm) {
      if (hasMtmData && mtmData) {
        // Build mode: single BaselineSeries
        const sr = chart.addSeries(BaselineSeries, {
          baseValue: { type: 'price', price: 0 },
          topLineColor: '#00e5a0', topFillColor1: 'rgba(0,229,160,0.22)', topFillColor2: 'rgba(0,229,160,0.04)',
          bottomLineColor: '#ff4d6a', bottomFillColor1: 'rgba(255,77,106,0.04)', bottomFillColor2: 'rgba(255,77,106,0.22)',
          lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
        }, 0);
        sr.setData(mtmData.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.pnl })));
        mtmBaselineRef.current = sr;
      } else if (hasMtmSeries && mtmSeries) {
        // Compare mode: one LineSeries per strategy
        mtmSeries.forEach(s => {
          const sr = chart.addSeries(LineSeries, {
            color: s.color, lineWidth: 2, priceLineVisible: false,
            lastValueVisible: true, crosshairMarkerVisible: true, crosshairMarkerRadius: 3,
          }, 0);
          if (s.data.length) sr.setData(s.data.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.value })));
          mtmLineRefs.current.set(s.id, sr);
        });
      }
      nextPane = 1;
    }

    // ── IV% pane ──────────────────────────────────────────────────────────
    if (visibleIv) {
      if (nextPane > 0) chart.addPane();
      ivSeries.forEach(s => {
        const sr = chart.addSeries(LineSeries, {
          color: s.color, lineWidth: 2, priceLineVisible: false,
          lastValueVisible: true, crosshairMarkerVisible: true, crosshairMarkerRadius: 3,
        }, nextPane);
        if (s.data.length) sr.setData(s.data.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.value })));
        ivRefsMap.current.set(s.id, sr);
      });
      nextPane++;
    }

    // ── Delta pane ────────────────────────────────────────────────────────
    if (visibleDelta) {
      if (nextPane > 0) chart.addPane();
      deltaSeries.forEach(s => {
        const sr = chart.addSeries(LineSeries, {
          color: s.color, lineWidth: 2, priceLineVisible: false,
          lastValueVisible: true, crosshairMarkerVisible: true, crosshairMarkerRadius: 3,
        }, nextPane);
        if (s.data.length) sr.setData(s.data.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.value })));
        deltaRefsMap.current.set(s.id, sr);
      });
    }

    // Fit time scale only — pane heights are set by the height update effect
    // after one layout frame, avoiding redistribution from addPane() calls.
    requestAnimationFrame(() => {
      chart.timeScale().fitContent();
    });

    // ── Crosshair tooltip — all panes ─────────────────────────────────────
    chart.subscribeCrosshairMove(param => {
      const tooltip = tooltipRef.current;
      if (!tooltip) return;
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tooltip.style.display = 'none'; return;
      }
      const ts = param.time as number;
      const d  = new Date(ts * 1000);
      const timeStr = `${String(d.getUTCDate()).padStart(2,'0')}/${String(d.getUTCMonth()+1).padStart(2,'0')} `
                    + `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;

      let html = `<div style="font-size:9px;color:#4a6a85;margin-bottom:3px">${timeStr} IST</div>`;
      let hasAny = false;

      // MTM — baseline
      if (mtmBaselineRef.current) {
        const val = (param.seriesData.get(mtmBaselineRef.current) as any)?.value as number | undefined;
        if (val !== undefined) {
          const pos = val >= 0;
          html += `<div style="font-size:12px;font-weight:700;color:${pos ? '#00e5a0' : '#ff4d6a'};margin-bottom:3px">`
                + `${pos ? '+' : ''}${val.toFixed(2)} <span style="font-size:9px;color:#7a9bb5;font-weight:400">USD P&L</span></div>`;
          hasAny = true;
        }
      }

      // MTM — compare lines
      const mInfo = mtmSeriesInfo.current;
      if (mInfo.length > 0) {
        const rows = mInfo.map(s => {
          const sr  = mtmLineRefs.current.get(s.id);
          const val = sr ? (param.seriesData.get(sr) as any)?.value as number | undefined : undefined;
          if (val === undefined) return '';
          const pos = val >= 0;
          return `<div style="display:flex;justify-content:space-between;gap:12px;font-size:10px;line-height:1.7">
            <span style="color:${s.color}">${s.label}</span>
            <span style="color:${pos ? '#00e5a0' : '#ff4d6a'};font-weight:700">${pos ? '+' : ''}${val.toFixed(2)} USD</span>
          </div>`;
        }).filter(Boolean).join('');
        if (rows) { html += rows; hasAny = true; }
      }

      // IV values
      const ivInfo = ivSeriesInfo.current;
      if (ivInfo.length > 0) {
        const rows = ivInfo.map(s => {
          const sr  = ivRefsMap.current.get(s.id);
          const val = sr ? (param.seriesData.get(sr) as any)?.value as number | undefined : undefined;
          if (val === undefined) return '';
          return `<div style="display:flex;justify-content:space-between;gap:12px;font-size:10px;line-height:1.7">
            <span style="color:${s.color}">${s.label} IV%</span>
            <span style="color:#e2e8f0;font-weight:700">${val.toFixed(2)}%</span>
          </div>`;
        }).filter(Boolean).join('');
        if (rows) { html += rows; hasAny = true; }
      }

      // Delta values
      const dInfo = deltaSeriesInfo.current;
      if (dInfo.length > 0) {
        const rows = dInfo.map(s => {
          const sr  = deltaRefsMap.current.get(s.id);
          const val = sr ? (param.seriesData.get(sr) as any)?.value as number | undefined : undefined;
          if (val === undefined) return '';
          return `<div style="display:flex;justify-content:space-between;gap:12px;font-size:10px;line-height:1.7">
            <span style="color:${s.color}">${s.label} Δ</span>
            <span style="color:#e2e8f0;font-weight:700">${val >= 0 ? '+' : ''}${val.toFixed(4)}</span>
          </div>`;
        }).filter(Boolean).join('');
        if (rows) { html += rows; hasAny = true; }
      }

      if (!hasAny) { tooltip.style.display = 'none'; return; }
      tooltip.innerHTML = html;
      const cw = containerRef.current?.clientWidth ?? 400;
      tooltip.style.display = 'block';
      tooltip.style.left = `${param.point.x < cw / 2 ? param.point.x + 14 : param.point.x - 210}px`;
      tooltip.style.top  = `${Math.max(0, param.point.y - 30)}px`;
    });

    const ro = new ResizeObserver(entries => {
      if (!entries.length) return;
      const { width } = entries[0].contentRect;
      if (width > 0) chartRef.current?.applyOptions({ width });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current     = null;
      mtmBaselineRef.current = null;
    };
  // Structural deps only — primitives, stable across reference changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasMtm, hasMtmData, hasMtmSeries, showIv, showDelta, nIv, nDelta]);

  // ── DATA EFFECTS — update in-place, no chart rebuild ──────────────────────
  useEffect(() => {
    const sr = mtmBaselineRef.current;
    if (!sr || !mtmData?.length) return;
    sr.setData(mtmData.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.pnl })));
    requestAnimationFrame(() => chartRef.current?.timeScale().fitContent());
  }, [mtmData]);

  useEffect(() => {
    if (!mtmSeries?.length || mtmLineRefs.current.size === 0) return;
    mtmSeries.forEach(s => {
      const sr = mtmLineRefs.current.get(s.id);
      if (sr && s.data.length) sr.setData(s.data.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.value })));
    });
    requestAnimationFrame(() => chartRef.current?.timeScale().fitContent());
  }, [mtmSeries]);

  useEffect(() => {
    if (ivRefsMap.current.size === 0) return;
    ivSeries.forEach(s => {
      const sr = ivRefsMap.current.get(s.id);
      if (sr && s.data.length) sr.setData(s.data.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.value })));
    });
    requestAnimationFrame(() => chartRef.current?.timeScale().fitContent());
  }, [ivSeries]);

  useEffect(() => {
    if (deltaRefsMap.current.size === 0) return;
    deltaSeries.forEach(s => {
      const sr = deltaRefsMap.current.get(s.id);
      if (sr && s.data.length) sr.setData(s.data.map(d => ({ time: (d.time + IST) as UTCTimestamp, value: d.value })));
    });
    requestAnimationFrame(() => chartRef.current?.timeScale().fitContent());
  }, [deltaSeries]);

  // ── Height update effect ───────────────────────────────────────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const visibleIv    = showIv    && nIv    > 0;
    const visibleDelta = showDelta && nDelta > 0;
    const total = (hasMtm ? mtmH : 0) + (visibleIv ? ivH : 0) + (visibleDelta ? deltaH : 0);
    if (total <= 0) return;
    // Apply total height synchronously so the container div matches the chart.
    // Defer individual pane setHeight to a RAF so the chart has had one layout
    // frame to settle internal pane distribution after addPane() calls — prevents
    // lightweight-charts from redistributing IV height when Delta pane is added.
    chart.applyOptions({ height: total });
    requestAnimationFrame(() => {
      const c = chartRef.current;
      if (!c) return;
      let p = 0;
      if (hasMtm)      { c.panes()[p]?.setHeight(mtmH);   p++; }
      if (visibleIv)   { c.panes()[p]?.setHeight(ivH);    p++; }
      if (visibleDelta){ c.panes()[p]?.setHeight(deltaH); }
    });
  }, [mtmH, ivH, deltaH, showIv, showDelta, hasMtm, nIv, nDelta]);

  // ── Drag handle ────────────────────────────────────────────────────────────
  const makeDragHandle = useCallback((
    startRef: React.MutableRefObject<number>,
    setH: (v: number) => void,
  ) => (e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = startRef.current;
    const onMove = (ev: MouseEvent) => setH(Math.max(PANE_MIN, startH + (ev.clientY - startY)));
    const onUp   = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, []);

  const visibleIv    = showIv    && nIv    > 0;
  const visibleDelta = showDelta && nDelta > 0;
  const totalH = (hasMtm ? mtmH : 0) + (visibleIv ? ivH : 0) + (visibleDelta ? deltaH : 0);

  const legendRow = (label: string, series: PaneSeries[], visible: boolean, toggle: () => void) => (
    <div onClick={toggle} style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '3px 10px', cursor: 'pointer', flexShrink: 0,
      borderTop: '1px solid var(--border)', background: 'var(--bg1)', height: HDR_H,
    }}>
      <span style={{ fontSize: '11px', color: 'var(--text2)', fontWeight: 600 }}>{label}</span>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        {series.map(s => (
          <span key={s.id} style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '10px' }}>
            <span style={{ width: '8px', height: '2px', background: s.color, display: 'inline-block', borderRadius: '1px' }} />
            <span style={{ color: 'var(--text3)' }}>{s.label}</span>
          </span>
        ))}
        <span style={{ color: 'var(--text3)', fontSize: '10px' }}>{visible ? '▲' : '▼'}</span>
      </div>
    </div>
  );

  if (!hasMtm && nIv === 0 && nDelta === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flexShrink: 0 }}>

      {/* Expand toggle */}
      {onToggleChartsOnly && (
        <div style={{
          display: 'flex', justifyContent: 'flex-end', padding: '2px 8px',
          borderTop: '1px solid var(--border)', background: 'var(--bg1)', flexShrink: 0,
        }}>
          <button
            onClick={onToggleChartsOnly}
            title={chartsOnly ? 'Exit charts-only view' : 'Charts only — hide all panels'}
            style={{
              background: chartsOnly ? 'var(--accent)' : 'transparent',
              border: '1px solid var(--border)',
              color: chartsOnly ? '#080e16' : 'var(--text2)',
              borderRadius: '3px', fontSize: '10px', padding: '2px 7px', cursor: 'pointer',
            }}
          >
            {chartsOnly ? '⊡ Exit' : '⤢ Charts only'}
          </button>
        </div>
      )}

      {/* Chart canvas */}
      <div style={{ position: 'relative', height: totalH, overflow: 'hidden' }}>
        <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
        <div ref={tooltipRef} style={{
          display: 'none', position: 'absolute', pointerEvents: 'none',
          background: 'rgba(8,14,22,0.92)', border: '1px solid #1e3048',
          borderRadius: '4px', padding: '5px 8px', whiteSpace: 'nowrap', zIndex: 10,
          minWidth: '160px',
        }} />
      </div>

      {/* MTM resize handle */}
      {hasMtm && (
        <div className="chart-resize-handle" onMouseDown={makeDragHandle(mtmHRef, setMtmH)} />
      )}

      {/* IV% toggle + resize */}
      {nIv > 0 && legendRow('IV% per Leg', ivSeries, showIv, () => setShowIv(v => !v))}
      {showIv && <div className="chart-resize-handle" onMouseDown={makeDragHandle(ivHRef, setIvH)} />}

      {/* Delta toggle + resize */}
      {nDelta > 0 && legendRow('Delta per Leg', deltaSeries, showDelta, () => setShowDelta(v => !v))}
      {showDelta && <div className="chart-resize-handle" onMouseDown={makeDragHandle(deltaHRef, setDeltaH)} />}
    </div>
  );
};
