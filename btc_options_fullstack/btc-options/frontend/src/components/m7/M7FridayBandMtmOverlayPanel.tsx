import React, { useEffect, useRef, useState } from 'react';
import { createChart, LineSeries, LineStyle } from 'lightweight-charts';
import type { IChartApi, TickMarkType, UTCTimestamp } from 'lightweight-charts';
import { fetchM7FridayBandMtmOverlay } from '../../services/m7_api';
import type {
  M7BandOverlay, M7ExitRule, M7Filters, M7FridayBandMtmOverlayResponse, M7MtmPickPath,
} from '../../types/m7';

// ── Loading bar (mirrors M7BestComboPathMarkers style) ───────────────────────

function LoadingBar({ visible }: { visible: boolean }) {
  return (
    <>
      <style>{`@keyframes m7slide_ovl { 0%{transform:translateX(-100%)} 100%{transform:translateX(400%)} }`}</style>
      <div style={{
        height: 2, width: '100%', background: '#0d1421', overflow: 'hidden',
        borderRadius: 2, marginBottom: 8,
        visibility: visible ? 'visible' : 'hidden',
      }}>
        <div style={{
          height: '100%', width: '25%', background: '#1f6feb',
          animation: 'm7slide_ovl 1.1s ease-in-out infinite',
        }} />
      </div>
    </>
  );
}

// ── Color palette for the 5 traces ───────────────────────────────────────────

const SERIES_CONFIG = {
  avg:           { label: 'Avg MTM',       color: '#22c55e', lineWidth: 3 as const, dashed: false },
  best:          { label: 'Best',          color: '#16a34a', lineWidth: 2 as const, dashed: false },
  worst:         { label: 'Worst',         color: '#dc2626', lineWidth: 2 as const, dashed: false },
  best_max_mtm:  { label: 'Best Max-MTM',  color: '#eab308', lineWidth: 2 as const, dashed: true },
  worst_min_mtm: { label: 'Worst Min-MTM', color: '#ec4899', lineWidth: 2 as const, dashed: true },
} as const;

type SeriesKey = keyof typeof SERIES_CONFIG;

// ── Dedup pick labels when the same trade_id appears in multiple slots ────────

function buildLegendRows(picks: M7BandOverlay['picks']): Array<{
  label: string;
  color: string;
  pick: M7MtmPickPath;
}> {
  const slots: Array<{ key: SeriesKey; pick: M7MtmPickPath | null }> = [
    { key: 'avg',           pick: null },   // avg has no pick — handled separately
    { key: 'best',          pick: picks.best },
    { key: 'worst',         pick: picks.worst },
    { key: 'best_max_mtm',  pick: picks.best_max_mtm },
    { key: 'worst_min_mtm', pick: picks.worst_min_mtm },
  ];

  // Group non-null picks by trade_id
  const grouped = new Map<string, { labels: string[]; color: string; pick: M7MtmPickPath }>();
  for (const { key, pick } of slots) {
    if (!pick) continue;
    if (grouped.has(pick.trade_id)) {
      grouped.get(pick.trade_id)!.labels.push(SERIES_CONFIG[key].label);
    } else {
      grouped.set(pick.trade_id, {
        labels: [SERIES_CONFIG[key].label],
        color: SERIES_CONFIG[key].color,
        pick,
      });
    }
  }

  return Array.from(grouped.values()).map(({ labels, color, pick }) => ({
    label: labels.join(' · '),
    color,
    pick,
  }));
}

// ── Per-band chart ────────────────────────────────────────────────────────────

function MtmOverlayChart({
  band,
  onPickClick,
}: {
  band: M7BandOverlay;
  onPickClick: (tradeId: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 280,
      layout: { background: { color: '#0a0e17' }, textColor: '#cfd9e3' },
      grid: { vertLines: { color: '#131c28' }, horzLines: { color: '#131c28' } },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (t: UTCTimestamp, _type: TickMarkType, _locale: string) =>
          `${Math.round(Number(t) / 60)}m`,
      },
    });
    chartRef.current = chart;

    // Helper: build setData array from a pick's minute+pnl columns
    const pickData = (pick: M7MtmPickPath | null) => {
      if (!pick) return [];
      return pick.minutes.map((m, i) => ({
        time: (m * 60) as UTCTimestamp,
        value: pick.pnl[i],
      }));
    };

    // Avg MTM (from avg_curve)
    const avgSeries = chart.addSeries(LineSeries, {
      color: SERIES_CONFIG.avg.color,
      lineWidth: SERIES_CONFIG.avg.lineWidth,
    });
    avgSeries.setData(
      band.avg_curve.minutes.map((m, i) => ({
        time: (m * 60) as UTCTimestamp,
        value: band.avg_curve.mean_pnl[i],
      })),
    );

    // Best
    const bestSeries = chart.addSeries(LineSeries, {
      color: SERIES_CONFIG.best.color,
      lineWidth: SERIES_CONFIG.best.lineWidth,
    });
    bestSeries.setData(pickData(band.picks.best));

    // Worst
    const worstSeries = chart.addSeries(LineSeries, {
      color: SERIES_CONFIG.worst.color,
      lineWidth: SERIES_CONFIG.worst.lineWidth,
    });
    worstSeries.setData(pickData(band.picks.worst));

    // Best Max-MTM (dashed)
    const bestMaxSeries = chart.addSeries(LineSeries, {
      color: SERIES_CONFIG.best_max_mtm.color,
      lineWidth: SERIES_CONFIG.best_max_mtm.lineWidth,
      lineStyle: LineStyle.Dashed,
    });
    bestMaxSeries.setData(pickData(band.picks.best_max_mtm));

    // Worst Min-MTM (dashed)
    const worstMinSeries = chart.addSeries(LineSeries, {
      color: SERIES_CONFIG.worst_min_mtm.color,
      lineWidth: SERIES_CONFIG.worst_min_mtm.lineWidth,
      lineStyle: LineStyle.Dashed,
    });
    worstMinSeries.setData(pickData(band.picks.worst_min_mtm));

    // n_trades_alive faint trace on separate price scale
    const nAliveSeries = chart.addSeries(LineSeries, {
      color: 'rgba(150,150,150,0.45)',
      lineWidth: 1,
      priceScaleId: 'n_alive',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    nAliveSeries.setData(
      band.avg_curve.minutes.map((m, i) => ({
        time: (m * 60) as UTCTimestamp,
        value: band.avg_curve.n_trades_alive[i],
      })),
    );
    chart.priceScale('n_alive').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
      visible: false,
    });

    chart.timeScale().fitContent();

    // ResizeObserver — mirrors M7TradePathChart pattern
    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [band]);

  const legendRows = buildLegendRows(band.picks);

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 12, marginBottom: 12,
    }}>
      {/* Header */}
      <div style={{
        fontSize: 12, color: '#cfd9e3', fontWeight: 600, marginBottom: 8,
      }}>
        Band:{' '}
        <span style={{ color: '#1f6feb' }}>{band.friday_band}</span>
        {' · '}
        <span style={{ color: '#7a9bb5' }}>n={band.n_trades}</span>
        {' · '}
        entry_hour={String(band.entry_hour_ist).padStart(2, '0')}:00 IST
        {' · '}
        expiry={band.expiry_bucket}
        {' · '}
        Δ={band.delta_target.toFixed(2)}
      </div>

      {/* Chart canvas */}
      <div ref={containerRef} style={{ width: '100%', height: 280 }} />

      {/* Legend */}
      <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
        {/* Avg MTM static row — no clickable trade */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: '#7a9bb5' }}>
          <span style={{
            display: 'inline-block', width: 12, height: 3,
            background: SERIES_CONFIG.avg.color, borderRadius: 1, flexShrink: 0,
          }} />
          <span style={{ color: '#cfd9e3' }}>Avg MTM</span>
          <span>(n={band.n_trades} trades)</span>
        </div>

        {/* Per-pick rows — clickable */}
        {legendRows.map(({ label, color, pick }) => {
          const netFmt = pick.net_pnl_estimate_usd == null
            ? '—'
            : `$${pick.net_pnl_estimate_usd.toFixed(2)}`;
          const finalGross = pick.pnl.length > 0
            ? `$${pick.pnl[pick.pnl.length - 1].toFixed(2)}`
            : '—';
          return (
            <div
              key={pick.trade_id}
              onClick={() => onPickClick(pick.trade_id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                fontSize: 11, cursor: 'pointer',
                padding: '3px 6px', borderRadius: 4,
                background: 'transparent',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = '#0d1421')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              title={`Click to open trade ${pick.trade_id}`}
            >
              <span style={{
                display: 'inline-block', width: 12, height: 3,
                background: color, borderRadius: 1, flexShrink: 0,
              }} />
              <span style={{ color: '#cfd9e3', fontWeight: 600 }}>{label}</span>
              <span style={{ color: '#7a9bb5' }}>{pick.friday_date_ist}</span>
              <span style={{ color: '#5b7894' }}>
                gross exit {finalGross} · net {netFmt}
              </span>
            </div>
          );
        })}
      </div>

      {/* Disclaimer */}
      <div style={{
        marginTop: 8, fontSize: 10, color: '#5b7894', fontStyle: 'italic',
      }}>
        Curves are gross P&L; legend shows net after slippage + brokerage.
      </div>
    </div>
  );
}

// ── Top-level panel ───────────────────────────────────────────────────────────

export function M7FridayBandMtmOverlayPanel({
  filters,
  exitRule,
  metric,
  bandMode,
  d1Tiebreakers,
  onPickClick,
}: {
  filters: M7Filters;
  exitRule: M7ExitRule;
  metric?: string;
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
  onPickClick: (tradeId: string) => void;
}) {
  const [data, setData] = useState<M7FridayBandMtmOverlayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(true);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7FridayBandMtmOverlay(
      { ...filters, metric },
      exitRule,
      bandMode,
      d1Tiebreakers,
      ac.signal,
    )
      .then(r => { if (active) setData(r); })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [
    JSON.stringify(filters),
    JSON.stringify(exitRule),
    metric,
    bandMode,
    JSON.stringify(d1Tiebreakers ?? []),
  ]);

  const bands = data?.bands ?? [];

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 12, marginBottom: 10,
    }}>
      <div
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          marginBottom: 8, cursor: 'pointer',
        }}
        onClick={() => setCollapsed(c => !c)}
      >
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          {collapsed ? '▸' : '▾'} MTM Overlay — avg + pick paths per band
          <span style={{ fontWeight: 400, color: '#7a9bb5', marginLeft: 8, fontSize: 12 }}>
            (avg curve + best/worst/best-max/worst-min pick traces · click legend row to open trade)
          </span>
        </div>
        <div style={{ fontSize: 11, color: '#7a9bb5' }}>
          {loading ? 'Loading…' : err
            ? <span style={{ color: '#f85149' }}>{err}</span>
            : `${bands.length} bands`}
        </div>
      </div>

      {!collapsed && (
        <>
          <LoadingBar visible={loading} />
          {!err && !loading && bands.length === 0 && (
            <div style={{ color: '#7a9bb5', fontSize: 12, padding: 16 }}>
              No trades match the current filters.
            </div>
          )}
          {!err && bands.map((b, i) => (
            <MtmOverlayChart
              key={`${b.friday_band}-${b.entry_hour_ist}-${b.expiry_bucket}-${b.delta_target}-${i}`}
              band={b}
              onPickClick={onPickClick}
            />
          ))}
        </>
      )}
    </div>
  );
}
