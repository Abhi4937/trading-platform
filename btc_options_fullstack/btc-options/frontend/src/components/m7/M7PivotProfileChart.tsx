import React, { useMemo } from 'react';
import type { M7PivotProfileResponse, M7PivotProfileSegment } from '../../services/m7_api';

const SEG_NAMES = ['Seg1', 'Seg2', 'Seg3', 'Seg4', 'Seg5'] as const;
const SEG_BOUND_MIN = [300, 480, 720, 900, 1050];  // IST minute-of-day at each boundary
const SEG_LABELS = ['entry→5am', '5–8am', '8am–12pm', '12–3pm', '3–5:30pm'];

// Render a single zigzag chart for one IV band.
function BandPanel({
  band, segs, minIstMod, maxIstMod, minTrades,
}: {
  band: string;
  segs: Record<string, M7PivotProfileSegment>;
  minIstMod: number;
  maxIstMod: number;
  minTrades: number;
}) {
  // Build the point list: entry anchor (avg of all earliest peak times - we
  // approximate the anchor by using the leftmost segment's avg trough/peak
  // start minus some padding) and per-segment peak + trough.
  const W = 520;
  const H = 240;
  const PAD_L = 38;
  const PAD_R = 14;
  const PAD_T = 18;
  const PAD_B = 36;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  type Pt = {
    seg: string;
    kind: 'peak' | 'trough';
    istMod: number;
    mtm: number;
    avgOffset: number;
    n: number;
    ddUsd: number;
    ddPct: number | null;
    nDdPct: number;
  };

  const pts: Pt[] = useMemo(() => {
    const list: Pt[] = [];
    for (const name of SEG_NAMES) {
      const s = segs[name];
      if (!s || s.n_trades === 0) continue;
      if (s.avg_peak_mtm_usd != null && s.avg_peak_minute_offset != null
          && s.avg_peak_ts_ist) {
        const mod = istModFromHHMM(s.avg_peak_ts_ist);
        if (mod != null) {
          list.push({
            seg: name, kind: 'peak', istMod: mod,
            mtm: s.avg_peak_mtm_usd,
            avgOffset: s.avg_peak_minute_offset,
            n: s.n_trades,
            ddUsd: s.avg_dd_usd ?? 0,
            ddPct: s.avg_dd_pct_from_peak,
            nDdPct: s.n_trades_for_dd_pct,
          });
        }
      }
      if (s.avg_trough_mtm_usd != null && s.avg_trough_minute_offset != null
          && s.avg_trough_ts_ist) {
        const mod = istModFromHHMM(s.avg_trough_ts_ist);
        if (mod != null) {
          list.push({
            seg: name, kind: 'trough', istMod: mod,
            mtm: s.avg_trough_mtm_usd,
            avgOffset: s.avg_trough_minute_offset,
            n: s.n_trades,
            ddUsd: s.avg_dd_usd ?? 0,
            ddPct: s.avg_dd_pct_from_peak,
            nDdPct: s.n_trades_for_dd_pct,
          });
        }
      }
    }
    // Sort by time-of-day, accounting for wrap (entry is late Fri so big
    // mod values come BEFORE small mod values). We unify by mapping each
    // mod into a linear axis using the panel's [minIstMod..maxIstMod]
    // domain — see the linear axis below.
    list.sort((a, b) => {
      const aa = linearizeIst(a.istMod, minIstMod);
      const bb = linearizeIst(b.istMod, minIstMod);
      return aa - bb;
    });
    return list;
  }, [segs, minIstMod]);

  if (pts.length === 0) {
    return (
      <div style={{ width: W, height: H, padding: 14, background: '#0a0e17',
                     color: '#7a9bb5', fontSize: 12, border: '1px solid #1a2d42',
                     borderRadius: 4 }}>
        <strong style={{ color: '#cfd9e3' }}>{band} IV</strong><br/>
        No data for selected entry hours.
      </div>
    );
  }

  const minLinear = linearizeIst(minIstMod, minIstMod);
  const maxLinear = linearizeIst(maxIstMod, minIstMod);
  const linRange = Math.max(1, maxLinear - minLinear);

  let yMin = Math.min(0, ...pts.map(p => p.mtm));
  let yMax = Math.max(0, ...pts.map(p => p.mtm));
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const yPad = (yMax - yMin) * 0.18;
  yMin -= yPad;
  yMax += yPad;
  const yRange = yMax - yMin;

  const xOf = (mod: number) => PAD_L + ((linearizeIst(mod, minIstMod) - minLinear) / linRange) * innerW;
  const yOf = (mtm: number) => PAD_T + (1 - (mtm - yMin) / yRange) * innerH;

  // Build line path through entry anchor (0$, at leftmost time) + all points.
  const entryX = xOf(minIstMod);
  const entryY = yOf(0);
  const linePoints: string[] = [`${entryX},${entryY}`];
  for (const p of pts) linePoints.push(`${xOf(p.istMod)},${yOf(p.mtm)}`);

  // Compute total n across segments (max, since each trade contributes to
  // multiple segments — we show the largest segment's n_trades as the
  // headline).
  const headlineN = Math.max(...Object.values(segs).map(s => s?.n_trades ?? 0));

  // Y-axis ticks: ~5 nice ticks.
  const yTicks = niceTicks(yMin, yMax, 5);

  // Vertical guides at segment boundaries (5/8/12/15 IST = mods 300,480,720,900).
  const boundaryMods = SEG_BOUND_MIN.slice(0, 4);  // 5/8/12/15
  const boundaryLines = boundaryMods
    .filter(m => isInWindow(m, minIstMod, maxIstMod))
    .map(m => xOf(m));

  // Label positioning to avoid overlap with the marker: peaks above, troughs below.
  // Use 3-line text per marker: time | $ value | DD info.
  return (
    <div style={{ background: '#0a0e17', border: '1px solid #1a2d42',
                   borderRadius: 4, padding: '8px 10px 6px 10px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline',
                     justifyContent: 'space-between',
                     marginBottom: 4 }}>
        <strong style={{ color: '#cfd9e3', fontSize: 13 }}>
          {band} IV
        </strong>
        <span style={{ color: '#7a9bb5', fontSize: 11 }}>
          n_max = {headlineN}
        </span>
      </div>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        {/* Y gridlines + labels */}
        {yTicks.map(t => (
          <g key={`yt-${t}`}>
            <line x1={PAD_L} x2={W - PAD_R} y1={yOf(t)} y2={yOf(t)}
                  stroke={t === 0 ? '#2a3d52' : '#131c28'}
                  strokeDasharray={t === 0 ? '' : '2,3'}
                  strokeWidth={t === 0 ? 1 : 1} />
            <text x={PAD_L - 5} y={yOf(t) + 3.5} textAnchor="end"
                  fill="#586e7e" fontSize={10} fontFamily="monospace">
              {fmtMoney(t)}
            </text>
          </g>
        ))}
        {/* Segment boundary lines */}
        {boundaryLines.map((x, i) => (
          <line key={`b-${i}`} x1={x} x2={x} y1={PAD_T} y2={H - PAD_B}
                stroke="#1a2d42" strokeDasharray="3,4" strokeWidth={1}/>
        ))}
        {/* Zigzag line */}
        <polyline points={linePoints.join(' ')}
                  fill="none" stroke="#7a9bb5" strokeWidth={1.6} />
        {/* Entry anchor */}
        <circle cx={entryX} cy={entryY} r={4}
                fill="#cfd9e3" stroke="#0a0e17" strokeWidth={1.5}/>
        <text x={entryX} y={entryY + 18} textAnchor="middle"
              fill="#7a9bb5" fontSize={9} fontFamily="monospace">
          Entry
        </text>
        <text x={entryX} y={entryY + 30} textAnchor="middle"
              fill="#7a9bb5" fontSize={9} fontFamily="monospace">
          {fmtIstMod(minIstMod)} IST
        </text>
        {/* Markers — peaks above, troughs below */}
        {pts.map((p, i) => {
          const x = xOf(p.istMod);
          const y = yOf(p.mtm);
          const isPeak = p.kind === 'peak';
          const triFill = isPeak ? '#3fb950' : '#f85149';
          const triPath = isPeak
            ? `M ${x},${y - 7} L ${x - 5},${y + 1} L ${x + 5},${y + 1} Z`
            : `M ${x},${y + 7} L ${x - 5},${y - 1} L ${x + 5},${y - 1} Z`;
          const labelY = isPeak ? y - 14 : y + 18;
          const labelDy = isPeak ? -10 : 10;
          const ddPctStr = p.ddPct == null
            ? '—'
            : `${p.ddPct >= 0 ? '-' : '+'}${Math.abs(p.ddPct).toFixed(0)}%`;
          const segLabel = isPeak ? `P${segOrdinal(p.seg)}` : `D${segOrdinal(p.seg)}`;
          return (
            <g key={`pt-${i}`}>
              <path d={triPath} fill={triFill} stroke="#0a0e17" strokeWidth={1}/>
              {/* Time + value label */}
              <text x={x} y={labelY} textAnchor="middle"
                    fill={triFill} fontSize={10} fontFamily="monospace"
                    fontWeight={600}>
                {segLabel} {fmtIstMod(p.istMod)}
              </text>
              <text x={x} y={labelY + labelDy} textAnchor="middle"
                    fill="#cfd9e3" fontSize={10} fontFamily="monospace">
                {fmtMoney(p.mtm)}
              </text>
              {!isPeak && p.ddUsd > 0 && (
                <text x={x} y={labelY + labelDy + 11} textAnchor="middle"
                      fill="#d29922" fontSize={9} fontFamily="monospace">
                  DD {fmtMoneyAbs(p.ddUsd)} ({ddPctStr})
                </text>
              )}
            </g>
          );
        })}
        {/* X-axis time labels at segment boundaries */}
        {boundaryMods.filter(m => isInWindow(m, minIstMod, maxIstMod))
          .map((m, i) => (
            <text key={`xt-${i}`} x={xOf(m)} y={H - 10}
                  textAnchor="middle" fill="#586e7e" fontSize={9}
                  fontFamily="monospace">
              {fmtIstMod(m)}
            </text>
          ))}
        {/* X-axis end label */}
        <text x={W - PAD_R} y={H - 10} textAnchor="end"
              fill="#586e7e" fontSize={9} fontFamily="monospace">
          17:30
        </text>
      </svg>
      <div style={{ marginTop: 2, fontSize: 10, color: '#7a9bb5',
                     display: 'flex', justifyContent: 'space-between' }}>
        <span>▲ peak ▼ trough · DD% = drop from peak</span>
        <span>Min-n cell threshold: {minTrades}</span>
      </div>
    </div>
  );
}

export function M7PivotProfileChart({ data }: { data: M7PivotProfileResponse }) {
  const result = data.result;
  const minTrades = data.min_trades_per_band_cell ?? 5;
  // Build the linear-time domain shared across all panels so the visual
  // x-scale is consistent (every panel runs entry-hour → 17:30 IST).
  const earliestEntryMod = useMemo(() => {
    if (!result) return 1380;
    let earliest = 1050; // 17:30 IST default
    for (const [, segs] of Object.entries(result.by_band)) {
      const s = segs.Seg1;
      if (!s) continue;
      if (s.avg_peak_minute_offset != null && s.avg_peak_ts_ist) {
        const mod = istModFromHHMM(s.avg_peak_ts_ist);
        // Reconstruct approximate entry mod from (peak_ts_ist − peak_minute_offset)
        if (mod != null) {
          const entryMod = ((mod - Math.round(s.avg_peak_minute_offset))
                            % 1440 + 1440) % 1440;
          // Pick the *latest* among Fri-evening hours so Seg1 covers the
          // widest possible range across bands.
          if (entryMod >= 1260 || entryMod < earliest) {
            earliest = entryMod;
          }
        }
      }
    }
    return earliest;
  }, [result]);

  if (!result) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 12, padding: 12 }}>
        No result yet.
      </div>
    );
  }
  const bands = Object.keys(result.by_band).sort(bandSortCmp);
  if (bands.length === 0) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 12, padding: 12 }}>
        No IV bands populated for selected entry hours.
      </div>
    );
  }
  // Shared x-domain endpoints: earliest entry (could be ~1380 for 23 IST)
  // → 1050 (17:30 IST). We pass these to each panel so they all share scale.
  const minIstMod = earliestEntryMod;
  const maxIstMod = 1050;
  return (
    <div style={{ display: 'grid',
                   gridTemplateColumns: 'repeat(auto-fit, minmax(520px, 1fr))',
                   gap: 10 }}>
      {bands.map(band => (
        <BandPanel key={band} band={band}
                    segs={result.by_band[band]}
                    minIstMod={minIstMod}
                    maxIstMod={maxIstMod}
                    minTrades={minTrades} />
      ))}
    </div>
  );
}


// ── helpers ────────────────────────────────────────────────────────────────

function istModFromHHMM(s: string | null | undefined): number | null {
  if (!s || typeof s !== 'string') return null;
  const m = s.match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  const h = parseInt(m[1], 10);
  const mi = parseInt(m[2], 10);
  if (!Number.isFinite(h) || !Number.isFinite(mi)) return null;
  return (h * 60 + mi) % 1440;
}

// Linear time axis: trades start in the Fri 21:00-23:59 IST range
// (mods 1260..1439) and run through Sat 00:00-17:30 IST (mods 0..1050).
// Convert a raw IST mod into a monotone axis starting at minIstMod.
function linearizeIst(mod: number, minIstMod: number): number {
  let v = mod - minIstMod;
  if (v < 0) v += 1440;
  return v;
}

function isInWindow(mod: number, minIstMod: number, maxIstMod: number): boolean {
  const a = linearizeIst(mod, minIstMod);
  const b = linearizeIst(maxIstMod, minIstMod);
  return a >= 0 && a <= b;
}

function fmtIstMod(m: number): string {
  const mm = ((Math.round(m) % 1440) + 1440) % 1440;
  return `${String(Math.floor(mm / 60)).padStart(2, '0')}:${String(mm % 60).padStart(2, '0')}`;
}

function fmtMoney(v: number): string {
  if (!Number.isFinite(v)) return '—';
  const sign = v < 0 ? '-' : v > 0 ? '+' : '';
  const abs = Math.abs(v);
  if (abs >= 100) return `${sign}$${abs.toFixed(0)}`;
  return `${sign}$${abs.toFixed(1)}`;
}

function fmtMoneyAbs(v: number): string {
  if (!Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 100) return `$${abs.toFixed(0)}`;
  return `$${abs.toFixed(1)}`;
}

function niceTicks(lo: number, hi: number, count: number): number[] {
  const range = hi - lo;
  if (range <= 0) return [lo];
  const step = niceStep(range / count);
  const start = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= hi + 1e-9; v += step) {
    out.push(Math.round(v * 100) / 100);
  }
  return out;
}

function niceStep(rough: number): number {
  if (rough <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  let step: number;
  if (norm < 1.5) step = 1;
  else if (norm < 3.5) step = 2;
  else if (norm < 7.5) step = 5;
  else step = 10;
  return step * mag;
}

function segOrdinal(name: string): number {
  const m = name.match(/(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

function bandSortCmp(a: string, b: string): number {
  // "0-20", "20-30", ..., "100+"
  const av = parseInt(a, 10) || 0;
  const bv = parseInt(b, 10) || 0;
  return av - bv;
}
