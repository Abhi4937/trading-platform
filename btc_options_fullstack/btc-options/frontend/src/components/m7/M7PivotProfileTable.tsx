import React from 'react';
import type { M7PivotProfileResponse, M7PivotProfileSegment } from '../../services/m7_api';

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

function SegmentCell({ s, minN }: {
  s: M7PivotProfileSegment | undefined;
  minN: number;
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
  return (
    <div style={muted}
         title={`median peak ${fmtMoney(s.median_peak_mtm_usd)}, ` +
                `p25/p75 ${fmtMoney(s.p25_peak_mtm_usd)}/${fmtMoney(s.p75_peak_mtm_usd)} | ` +
                `median trough ${fmtMoney(s.median_trough_mtm_usd)} | ` +
                `median DD ${fmtMoneyAbs(s.median_dd_usd)} (${fmtPct(s.median_dd_pct_from_peak)})`}>
      <div>
        <span style={{ color: '#3fb950' }}>Peak  </span>
        {s.avg_peak_ts_ist}  {fmtMoney(s.avg_peak_mtm_usd)}
      </div>
      <div>
        <span style={{ color: '#f85149' }}>Trough</span>
        {' '}{s.avg_trough_ts_ist}  {fmtMoney(s.avg_trough_mtm_usd)}
      </div>
      <div>
        <span style={{ color: '#d29922' }}>DD    </span>
        {fmtMoneyAbs(s.avg_dd_usd)}{' '}
        <span style={{ color: lowDdN ? '#586e7e' : '#d29922' }}>
          ({lowDdN ? '—' : fmtPct(s.avg_dd_pct_from_peak)})
        </span>
      </div>
      <div style={{ color: '#586e7e', fontSize: 10 }}>
        n={s.n_trades}{' '}
        {lowDdN ? `(% from <${minN})` : `(% from ${s.n_trades_for_dd_pct})`}
      </div>
    </div>
  );
}

export function M7PivotProfileTable({ data }: { data: M7PivotProfileResponse }) {
  const result = data.result;
  const minN = data.min_trades_per_band_cell ?? 5;
  if (!result) {
    return <div style={{ color: '#7a9bb5', fontSize: 12 }}>No data yet.</div>;
  }
  const bands = Object.keys(result.by_band).sort((a, b) => {
    const av = parseInt(a, 10) || 0;
    const bv = parseInt(b, 10) || 0;
    return av - bv;
  });
  if (bands.length === 0) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 12 }}>
        No IV bands populated for the selected entry hours.
      </div>
    );
  }
  return (
    <div style={{ overflowX: 'auto', border: '1px solid #1a2d42',
                   borderRadius: 4, marginTop: 10 }}>
      <table style={{ borderCollapse: 'collapse', width: '100%',
                      minWidth: 1100 }}>
        <thead>
          <tr>
            <th style={{ ...TH, minWidth: 75 }}>IV Band</th>
            {SEG_HEADERS.map(h => (
              <th key={h.name} style={{ ...TH, minWidth: 180 }}>
                <div>{h.name}</div>
                <div style={{ color: '#586e7e', fontWeight: 400 }}>
                  {h.label}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bands.map(band => (
            <tr key={band}>
              <td style={{ ...TD, fontWeight: 600 }}>{band}</td>
              {SEG_NAMES.map(seg => (
                <td key={seg} style={TD}>
                  <SegmentCell s={result.by_band[band][seg]} minN={minN} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
