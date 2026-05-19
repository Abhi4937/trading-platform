import React from 'react';
import type { M7PivotByBand, M7PivotProfileSegment } from '../../services/m7_api';

const SEG_NAMES = ['Seg1', 'Seg2', 'Seg3', 'Seg4', 'Seg5'] as const;
const SEG_HEADERS = [
  { name: 'Seg1', label: 'entry → 5am' },
  { name: 'Seg2', label: '5 – 8am' },
  { name: 'Seg3', label: '8am – 12pm' },
  { name: 'Seg4', label: '12 – 3pm' },
  { name: 'Seg5', label: '3 – 5:30pm' },
] as const;

const TH: React.CSSProperties = {
  padding: '6px 8px', textAlign: 'left', borderBottom: '1px solid #1a2d42',
  fontSize: 11, color: '#7a9bb5', fontWeight: 600, whiteSpace: 'nowrap',
  position: 'sticky', top: 0, background: '#0d1421', zIndex: 1,
};
const TD: React.CSSProperties = {
  padding: '8px 8px', borderBottom: '1px solid #131c28', verticalAlign: 'top',
  fontSize: 11, color: '#cfd9e3', fontFamily: 'monospace', whiteSpace: 'nowrap',
};

function fmtMoney(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v < 0 ? '-' : v > 0 ? '+' : '';
  const abs = Math.abs(v);
  if (abs >= 100) return `${sign}$${abs.toFixed(0)}`;
  return `${sign}$${abs.toFixed(1)}`;
}

function fmtMoneyAbs(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 100) return `$${abs.toFixed(0)}`;
  return `$${abs.toFixed(1)}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '-' : '+'}${Math.abs(v).toFixed(0)}%`;
}

function fmtDelta(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(0)}%`;
}

interface CellDeltas {
  peakPctVsPrev: number | null;
  peakUsdVsPrev: number | null;
  troughPctVsPrev: number | null;
  troughUsdVsPrev: number | null;
  ddPctVsPrev: number | null;
  ddUsdVsPrev: number | null;
}

function computeDeltas(
  segs: Record<typeof SEG_NAMES[number], M7PivotProfileSegment>,
): Record<typeof SEG_NAMES[number], CellDeltas> {
  const out = {} as Record<typeof SEG_NAMES[number], CellDeltas>;
  let prevPeak: number | null = null;
  let prevTrough: number | null = null;
  let prevDd: number | null = null;
  for (const name of SEG_NAMES) {
    const s = segs[name];
    let peakPct: number | null = null;
    let peakUsd: number | null = null;
    let troughPct: number | null = null;
    let troughUsd: number | null = null;
    let ddPct: number | null = null;
    let ddUsd: number | null = null;
    if (s && s.avg_peak_mtm_usd != null && prevPeak != null) {
      peakUsd = s.avg_peak_mtm_usd - prevPeak;
      if (prevPeak !== 0) {
        peakPct = (peakUsd / Math.abs(prevPeak)) * 100;
      }
    }
    if (s && s.avg_trough_mtm_usd != null && prevTrough != null) {
      troughUsd = s.avg_trough_mtm_usd - prevTrough;
      if (prevTrough !== 0) {
        troughPct = (troughUsd / Math.abs(prevTrough)) * 100;
      }
    }
    if (s && s.avg_dd_usd != null && prevDd != null) {
      ddUsd = s.avg_dd_usd - prevDd;
      if (prevDd !== 0) {
        ddPct = (ddUsd / Math.abs(prevDd)) * 100;
      }
    }
    out[name] = {
      peakPctVsPrev: peakPct, peakUsdVsPrev: peakUsd,
      troughPctVsPrev: troughPct, troughUsdVsPrev: troughUsd,
      ddPctVsPrev: ddPct, ddUsdVsPrev: ddUsd,
    };
    if (s?.avg_peak_mtm_usd != null) prevPeak = s.avg_peak_mtm_usd;
    if (s?.avg_trough_mtm_usd != null) prevTrough = s.avg_trough_mtm_usd;
    if (s?.avg_dd_usd != null) prevDd = s.avg_dd_usd;
  }
  return out;
}

function fmtDeltaDual(pct: number | null | undefined,
                       usd: number | null | undefined): React.ReactNode {
  if (pct == null && usd == null) return '—';
  const pctNode = pct == null ? null : (
    <span style={{ color: pct >= 0 ? '#3fb950' : '#f85149' }}>
      {fmtDelta(pct)}
    </span>
  );
  const usdNode = usd == null ? null : (
    <span style={{ color: '#586e7e' }}>
      {' '}({usd >= 0 ? '+' : '-'}{fmtMoneyAbs(usd)})
    </span>
  );
  return <>{pctNode}{usdNode}</>;
}

function SegmentCell({ s, minN, deltas }: {
  s: M7PivotProfileSegment | undefined;
  minN: number;
  deltas: CellDeltas;
}) {
  if (!s || s.n_trades === 0) {
    return (
      <div style={{ color: '#586e7e' }}>
        no data
      </div>
    );
  }
  const lowN = s.n_trades < minN;
  const lowDdN = s.n_trades_for_dd_pct < minN;
  const muted = lowN ? { opacity: 0.45 } : {};
  const peakSd = s.std_peak_mtm_usd ?? null;
  const troughSd = s.std_trough_mtm_usd ?? null;
  const ddSd = s.std_dd_usd ?? null;
  // ±1σ explicit range strings: e.g. "[+$103, +$131]" for peak.
  const peakLo = (s.avg_peak_mtm_usd != null && peakSd != null)
    ? s.avg_peak_mtm_usd - peakSd : null;
  const peakHi = (s.avg_peak_mtm_usd != null && peakSd != null)
    ? s.avg_peak_mtm_usd + peakSd : null;
  const troughLo = (s.avg_trough_mtm_usd != null && troughSd != null)
    ? s.avg_trough_mtm_usd - troughSd : null;
  const troughHi = (s.avg_trough_mtm_usd != null && troughSd != null)
    ? s.avg_trough_mtm_usd + troughSd : null;
  return (
    <div style={muted}
         title={`median peak ${fmtMoney(s.median_peak_mtm_usd)}, ` +
                `p25/p75 ${fmtMoney(s.p25_peak_mtm_usd)}/${fmtMoney(s.p75_peak_mtm_usd)} | ` +
                `median trough ${fmtMoney(s.median_trough_mtm_usd)} | ` +
                `median DD ${fmtMoneyAbs(s.median_dd_usd)} (${fmtPct(s.median_dd_pct_from_peak)})`}>
      <div>
        <span style={{ color: '#3fb950' }}>Peak  </span>
        {s.avg_peak_ts_ist}  {fmtMoney(s.avg_peak_mtm_usd)}
        {peakSd != null && (
          <span style={{ color: '#586e7e' }}> ± {fmtMoneyAbs(peakSd)}</span>
        )}
        {' '}<span style={{ color: '#7a9bb5' }}>
          {s.n_above_avg_peak}↑/{s.n_below_avg_peak}↓
        </span>
      </div>
      {peakLo != null && peakHi != null && (
        <div style={{ color: '#586e7e', fontSize: 10, paddingLeft: 14 }}>
          ±1σ [{fmtMoney(peakLo)}, {fmtMoney(peakHi)}] · {s.n_within_1sd_peak}/{s.n_trades} in
        </div>
      )}
      <div style={{ color: '#586e7e', fontSize: 10, paddingLeft: 14 }}>
        Δ vs prev P: {fmtDeltaDual(deltas.peakPctVsPrev, deltas.peakUsdVsPrev)}
      </div>
      <div style={{ marginTop: 4 }}>
        <span style={{ color: '#f85149' }}>Trough</span>
        {' '}{s.avg_trough_ts_ist}  {fmtMoney(s.avg_trough_mtm_usd)}
        {troughSd != null && (
          <span style={{ color: '#586e7e' }}> ± {fmtMoneyAbs(troughSd)}</span>
        )}
        {' '}<span style={{ color: '#7a9bb5' }}>
          {s.n_above_avg_trough}↑/{s.n_below_avg_trough}↓
        </span>
      </div>
      {troughLo != null && troughHi != null && (
        <div style={{ color: '#586e7e', fontSize: 10, paddingLeft: 14 }}>
          ±1σ [{fmtMoney(troughLo)}, {fmtMoney(troughHi)}] · {s.n_within_1sd_trough}/{s.n_trades} in
        </div>
      )}
      <div style={{ color: '#586e7e', fontSize: 10, paddingLeft: 14 }}>
        Δ vs prev T: {fmtDeltaDual(deltas.troughPctVsPrev, deltas.troughUsdVsPrev)}
      </div>
      <div style={{ marginTop: 4 }}>
        <span style={{ color: '#d29922' }}>DD    </span>
        {fmtMoneyAbs(s.avg_dd_usd)}
        {ddSd != null && (
          <span style={{ color: '#586e7e' }}> ± {fmtMoneyAbs(ddSd)}</span>
        )}{' '}
        <span style={{ color: lowDdN ? '#586e7e' : '#d29922' }}>
          ({lowDdN ? '—' : fmtPct(s.avg_dd_pct_from_peak)})
        </span>
        {' '}<span style={{ color: '#7a9bb5' }}>
          {s.n_above_avg_dd}↑/{s.n_below_avg_dd}↓
        </span>
      </div>
      <div style={{ color: '#586e7e', fontSize: 10, paddingLeft: 14 }}>
        Δ vs prev DD: {fmtDeltaDual(deltas.ddPctVsPrev, deltas.ddUsdVsPrev)}
        {' · '}{s.n_within_1sd_dd}/{s.n_trades} in
      </div>
      <div style={{ color: '#586e7e', fontSize: 10, marginTop: 2 }}>
        n={s.n_trades}{' '}
        {lowDdN ? `(% from <${minN})` : `(% from ${s.n_trades_for_dd_pct})`}
      </div>
    </div>
  );
}

export function M7PivotProfileTable(
  { byBand, minN = 5 }:
  { byBand: M7PivotByBand; minN?: number },
) {
  const bands = Object.keys(byBand).sort((a, b) => {
    const av = parseInt(a, 10) || 0;
    const bv = parseInt(b, 10) || 0;
    return av - bv;
  });
  if (bands.length === 0) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 12 }}>
        No IV bands populated for the current filter.
      </div>
    );
  }
  return (
    <div style={{ overflowX: 'auto', border: '1px solid #1a2d42',
                   borderRadius: 4, marginTop: 10 }}>
      <table style={{ borderCollapse: 'collapse', width: '100%',
                      minWidth: 1500 }}>
        <thead>
          <tr>
            <th style={{ ...TH, minWidth: 75 }}>IV Band</th>
            {SEG_HEADERS.map(h => (
              <th key={h.name} style={{ ...TH, minWidth: 240 }}>
                <div>{h.name}</div>
                <div style={{ color: '#586e7e', fontWeight: 400 }}>
                  {h.label}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bands.map(band => {
            const deltas = computeDeltas(byBand[band]);
            return (
              <tr key={band}>
                <td style={{ ...TD, fontWeight: 600 }}>{band}</td>
                {SEG_NAMES.map(seg => (
                  <td key={seg} style={TD}>
                    <SegmentCell s={byBand[band][seg]}
                                  minN={minN}
                                  deltas={deltas[seg]} />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
