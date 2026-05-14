import React, { useEffect, useState } from 'react';
import { fetchM7BestComboMarkers, fetchM7FridayBandBestComboMarkers } from '../../services/m7_api';
import type {
  M7BestComboMarker, M7BestComboMarkersBand, M7ExitRule, M7Filters,
} from '../../types/m7';

const COLOR_WIN        = '#3fb950';
const COLOR_LOSS_RULE  = '#f85149';
const COLOR_LOSS_HCAP  = '#d97706';

const ROW_H    = 36;
const LABEL_W  = 120;
const VALUES_W = 180;

// Golden-angle stepping spreads hues for max distinguishability when many
// trades stack in one group.
function pathColor(i: number): string {
  const hue = (i * 137.5) % 360;
  return `hsl(${hue}, 65%, 58%)`;
}

const clamp01 = (v: number | null | undefined) =>
  v == null || isNaN(v) ? 0 : Math.max(0, Math.min(1, v));

function LoadingBar({ visible }: { visible: boolean }) {
  return (
    <>
      <style>{`@keyframes m7slide_pm { 0%{transform:translateX(-100%)} 100%{transform:translateX(400%)} }`}</style>
      <div style={{
        height: 2, width: '100%', background: '#0d1421', overflow: 'hidden',
        borderRadius: 2, marginBottom: 8,
        visibility: visible ? 'visible' : 'hidden',
      }}>
        <div style={{
          height: '100%', width: '25%', background: '#1f6feb',
          animation: 'm7slide_pm 1.1s ease-in-out infinite',
        }} />
      </div>
    </>
  );
}

function Badge({ outcome }: { outcome: 'W' | 'SL' | 'HC' }) {
  const color = outcome === 'W' ? COLOR_WIN
              : outcome === 'SL' ? COLOR_LOSS_RULE : COLOR_LOSS_HCAP;
  return (
    <span style={{
      display: 'inline-block', minWidth: 22, padding: '0 5px',
      fontSize: 9, fontWeight: 700, color,
      border: `1px solid ${color}66`, borderRadius: 3,
      background: color + '14',
      textAlign: 'center', marginLeft: 6,
    }}>
      {outcome}
    </span>
  );
}

interface Anchor { x: number; y: number; }

function TradeSparkline({ t, color }: { t: M7BestComboMarker; color: string }) {
  const xMax  = clamp01(t.rel_time_max_mtm);
  const xMin  = clamp01(t.rel_time_min_mtm);
  const yMax  = t.max_mtm_usd ?? 0;
  const yMin  = t.min_mtm_usd ?? 0;
  // Exit anchor uses exit_mtm_usd (gross at exit − entry slippage only),
  // matching the same MTM convention as max/min on this chart.
  const yExit = t.exit_mtm_usd ?? t.net_pnl_estimate_usd ?? 0;

  // 4 anchor points; sort the middle two by time so the line traces the
  // actual chronological sequence of events.
  const entry: Anchor = { x: 0, y: 0 };
  const peak:  Anchor = { x: xMax, y: yMax };
  const trough:Anchor = { x: xMin, y: yMin };
  const exit:  Anchor = { x: 1, y: yExit };
  const middle = [peak, trough].sort((a, b) => a.x - b.x);
  const ordered: Anchor[] = [entry, middle[0], middle[1], exit];

  // Per-row Y scale: include 0 + the exit value so all anchors fit in the
  // visible track (an exit MTM below the path's min would otherwise clip).
  const ymin = Math.min(0, yMin, yExit);
  const ymax = Math.max(0, yMax, yExit);
  const range = (ymax - ymin) || 1;
  const PAD = 5;
  const trackH = ROW_H - 2 * PAD;
  const yScale = (v: number) => PAD + (1 - (v - ymin) / range) * trackH;

  // Outcome → exit-marker color
  const exitColor = t.is_win ? COLOR_WIN
                   : t.exit_reason === 'rule_trigger' ? COLOR_LOSS_RULE
                   : COLOR_LOSS_HCAP;

  // Marker positions in viewBox coords (X scaled to 0..1000, Y in pixels)
  const Px = peak.x * 1000;     const Py = yScale(peak.y);
  const Tx = trough.x * 1000;   const Ty = yScale(trough.y);
  const Ex = exit.x * 1000;     const Ey = yScale(yExit);

  // Marker fills — peak green if positive, trough red if negative
  const peakFill   = yMax > 0 ? COLOR_WIN       : '#7a3a3a';
  const troughFill = yMin < 0 ? COLOR_LOSS_RULE : '#3a7a3a';

  return (
    <svg width="100%" height={ROW_H} viewBox={`0 0 1000 ${ROW_H}`}
         preserveAspectRatio="none" style={{ display: 'block' }}>
      {/* y=0 baseline */}
      <line x1="0" y1={yScale(0)} x2="1000" y2={yScale(0)}
            stroke="#1a2d42" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
      {/* mid-trade reference */}
      <line x1="500" y1="2" x2="500" y2={ROW_H - 2}
            stroke="#1a2d42" strokeDasharray="1 3" opacity="0.5"
            vectorEffect="non-scaling-stroke" />
      {/* the per-trade colored polyline — slope direction shows event order */}
      <polyline
        fill="none" stroke={color} strokeWidth="1.5"
        points={ordered.map(e => `${e.x * 1000},${yScale(e.y)}`).join(' ')}
        vectorEffect="non-scaling-stroke"
      />
      {/* peak (▲) — non-scaling so it stays a triangle when SVG stretches */}
      <polygon
        points={`${Px - 4},${Py + 4} ${Px + 4},${Py + 4} ${Px},${Py - 4}`}
        fill={peakFill} stroke="#0a0e17" strokeWidth="0.5"
        vectorEffect="non-scaling-stroke"
        style={{ transformBox: 'fill-box' }}
      />
      {/* trough (▼) */}
      <polygon
        points={`${Tx - 4},${Ty - 4} ${Tx + 4},${Ty - 4} ${Tx},${Ty + 4}`}
        fill={troughFill} stroke="#0a0e17" strokeWidth="0.5"
        vectorEffect="non-scaling-stroke"
      />
      {/* exit (⬛) — pinned at right edge */}
      <rect
        x={Ex - 4} y={Ey - 4} width="8" height="8"
        fill={exitColor} stroke="#0a0e17" strokeWidth="0.5"
        vectorEffect="non-scaling-stroke"
      />
      {/* tooltip via native SVG <title> */}
      <title>
        {`${t.friday_date_ist}\nmax $${yMax.toFixed(2)} at ${(xMax * 100).toFixed(0)}%\nmin $${yMin.toFixed(2)} at ${(xMin * 100).toFixed(0)}%\nexit MTM $${yExit.toFixed(2)}  (entry slip only)\nnet (all costs) $${(t.net_pnl_estimate_usd ?? 0).toFixed(2)}\noutcome: ${t.is_win ? 'WIN' : 'LOSS'} · ${t.exit_reason ?? '—'}`}
      </title>
    </svg>
  );
}

function TradeRow({ t, idxInBand }: { t: M7BestComboMarker; idxInBand: number }) {
  const outcome: 'W' | 'SL' | 'HC' = t.is_win ? 'W'
    : t.exit_reason === 'rule_trigger' ? 'SL' : 'HC';
  const tint = outcome === 'W' ? COLOR_WIN
            : outcome === 'SL' ? COLOR_LOSS_RULE : COLOR_LOSS_HCAP;
  const color = pathColor(idxInBand);

  const fmt = (v: number | null | undefined, dp = 0) =>
    v == null || isNaN(v as number) ? '—' : `$${(v as number).toFixed(dp)}`;

  return (
    <div style={{
      display: 'flex', alignItems: 'center', height: ROW_H,
      background: tint + '0d', // ~5% alpha tint by outcome
      borderBottom: '1px solid #11202f',
    }}>
      <div style={{
        width: LABEL_W, fontSize: 11, color: '#cfd9e3',
        paddingLeft: 6, fontVariantNumeric: 'tabular-nums', flexShrink: 0,
      }}>
        {t.friday_date_ist}
        <Badge outcome={outcome} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <TradeSparkline t={t} color={color} />
      </div>
      <div style={{
        width: VALUES_W, paddingLeft: 8, fontSize: 10, color: '#7a9bb5',
        textAlign: 'right', fontVariantNumeric: 'tabular-nums', flexShrink: 0,
      }}>
        max <span style={{ color: COLOR_WIN }}>{fmt(t.max_mtm_usd)}</span>
        {' · '}
        min <span style={{ color: COLOR_LOSS_RULE }}>{fmt(t.min_mtm_usd)}</span>
        {' · '}
        exit <span style={{ color: (t.exit_mtm_usd ?? 0) >= 0 ? COLOR_WIN : COLOR_LOSS_RULE }}>
          {fmt(t.exit_mtm_usd)}
        </span>
      </div>
    </div>
  );
}

function GroupHeader({ label, count, color }: { label: string; count: number; color: string }) {
  if (count === 0) return null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '8px 6px 4px 6px', fontSize: 11,
      color: '#7a9bb5', fontWeight: 600, letterSpacing: 0.3,
      borderBottom: `1px dashed ${color}55`,
    }}>
      <span style={{ color }}>●</span>
      <span style={{ textTransform: 'uppercase' }}>{label}</span>
      <span style={{ color: '#5b7894', fontWeight: 400 }}>({count})</span>
    </div>
  );
}

function TimeAxis() {
  // Renders once per band, aligned to the same viewBox as the sparklines
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start',
      paddingTop: 6, paddingBottom: 6,
    }}>
      <div style={{ width: LABEL_W, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
        <svg width="100%" height={14} viewBox="0 0 1000 14"
             preserveAspectRatio="none" style={{ display: 'block' }}>
          <line x1="0" y1="2" x2="1000" y2="2" stroke="#2a3d52"
                vectorEffect="non-scaling-stroke" />
          {[0, 250, 500, 750, 1000].map(x => (
            <line key={x} x1={x} y1="2" x2={x} y2="7" stroke="#2a3d52"
                  vectorEffect="non-scaling-stroke" />
          ))}
        </svg>
        <div style={{
          position: 'absolute', top: 8, left: 0, right: 0,
          display: 'flex', justifyContent: 'space-between',
          fontSize: 9, color: '#7a9bb5', fontVariantNumeric: 'tabular-nums',
        }}>
          <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
        </div>
        <div style={{
          textAlign: 'center', fontSize: 9, color: '#5b7894',
          marginTop: 14, fontStyle: 'italic',
        }}>
          relative time (entry → exit)
        </div>
      </div>
      <div style={{ width: VALUES_W, flexShrink: 0 }} />
    </div>
  );
}

function BandPanel({ band }: { band: M7BestComboMarkersBand }) {
  // Group + sort: winners first, then SL hits, then hard-cap; chrono within each
  const sortedTrades = [...band.trades].sort((a, b) =>
    a.friday_date_ist.localeCompare(b.friday_date_ist));
  const winners  = sortedTrades.filter(t => t.is_win);
  const slLosses = sortedTrades.filter(t => t.is_win === false && t.exit_reason === 'rule_trigger');
  const hcLosses = sortedTrades.filter(t => t.is_win === false && t.exit_reason !== 'rule_trigger');

  // Assign per-trade color index in the order they're rendered (winners → SL → HC)
  const ordered = [...winners, ...slLosses, ...hcLosses];
  const colorIndex = new Map<M7BestComboMarker, number>();
  ordered.forEach((t, i) => colorIndex.set(t, i));

  return (
    <div style={{
      borderTop: '1px solid #1a2d42', padding: '12px 4px 4px 4px',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        marginBottom: 6, fontSize: 12, padding: '0 4px',
      }}>
        <div style={{ color: '#cfd9e3' }}>
          <span style={{ fontWeight: 700 }}>Band {band.entry_atm_iv_band}</span>
          <span style={{ color: '#7a9bb5' }}>
            {' — '}{band.expiry_bucket} · Δ={band.delta_target.toFixed(2)} · hr=
            {String(band.entry_hour_ist).padStart(2, '0')}:00
          </span>
        </div>
        <div style={{ color: '#7a9bb5' }}>
          {band.n_trades} trades (
          <span style={{ color: COLOR_WIN }}>{band.n_wins}W</span>
          {' / '}
          <span style={{ color: COLOR_LOSS_RULE }}>{band.n_losses}L</span>)
        </div>
      </div>

      <GroupHeader label="Winners" count={winners.length} color={COLOR_WIN} />
      {winners.map(t => (
        <TradeRow key={`w-${t.friday_date_ist}`} t={t} idxInBand={colorIndex.get(t)!} />
      ))}

      <GroupHeader label="SL hits" count={slLosses.length} color={COLOR_LOSS_RULE} />
      {slLosses.map(t => (
        <TradeRow key={`sl-${t.friday_date_ist}`} t={t} idxInBand={colorIndex.get(t)!} />
      ))}

      <GroupHeader label="Hard-cap losses" count={hcLosses.length} color={COLOR_LOSS_HCAP} />
      {hcLosses.map(t => (
        <TradeRow key={`hc-${t.friday_date_ist}`} t={t} idxInBand={colorIndex.get(t)!} />
      ))}

      <TimeAxis />
    </div>
  );
}

export function M7BestComboPathMarkers({ filters, exitRule, metric = 'avg_net_pnl',
                                         useFridayBand = false, bandMode, d1Tiebreakers }: {
  filters: M7Filters; exitRule: M7ExitRule; metric?: string;
  useFridayBand?: boolean;
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
}) {
  const [bands, setBands] = useState<M7BestComboMarkersBand[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(true);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    const p = useFridayBand
      ? fetchM7FridayBandBestComboMarkers({ ...filters, metric }, exitRule, bandMode, d1Tiebreakers, ac.signal)
      : fetchM7BestComboMarkers({ ...filters, metric }, exitRule, ac.signal);
    p.then(r => { if (active) setBands(r.bands); })
     .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
     .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(filters), JSON.stringify(exitRule), metric, useFridayBand, bandMode, JSON.stringify(d1Tiebreakers ?? [])]);

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 12, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8, cursor: 'pointer',
      }} onClick={() => setCollapsed(c => !c)}>
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          {collapsed ? '▸' : '▾'} Trade Path Markers — per-trade event sequence
          <span style={{ fontWeight: 400, color: '#7a9bb5', marginLeft: 8, fontSize: 12 }}>
            (one mini sparkline per trade · X = entry→exit, Y = relative MTM, line color cycles per trade)
          </span>
        </div>
        <div style={{ fontSize: 11, color: '#7a9bb5' }}>
          {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span>
            : `${bands.length} bands`}
        </div>
      </div>
      {!collapsed && (
        <>
          <LoadingBar visible={loading} />
          {!err && (
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: 14, fontSize: 11, color: '#7a9bb5',
              padding: '0 4px 8px 4px',
            }}>
              <span>▲ <span style={{ color: COLOR_WIN }}>peak (max MTM)</span></span>
              <span>▼ <span style={{ color: COLOR_LOSS_RULE }}>trough (min MTM)</span></span>
              <span>⬛ exit:&nbsp;
                <span style={{ color: COLOR_WIN }}>winner</span>
                {' · '}
                <span style={{ color: COLOR_LOSS_RULE }}>SL hit</span>
                {' · '}
                <span style={{ color: COLOR_LOSS_HCAP }}>hard cap</span>
              </span>
              <span style={{ marginLeft: 'auto', color: '#5b7894' }}>
                each line follows entry → first event → second event → exit
              </span>
            </div>
          )}
          {!err && !loading && bands.length === 0 && (
            <div style={{ color: '#7a9bb5', fontSize: 12, padding: 16 }}>
              No trades match the current filters.
            </div>
          )}
          {!err && bands.map((b, i) => (
            <BandPanel key={`${b.entry_atm_iv_band}-${i}`} band={b} />
          ))}
        </>
      )}
    </div>
  );
}
