// Missed Fridays for the Best Combo picker.
//
// Different from M7MissedFridaysTable (which uses the headline /missed_fridays
// endpoint with simpler ranking): this one calls the new
// /iv_band_best_combo/missed_fridays endpoint and passes ALL the user's
// current sizing + filter state so the picks computed match exactly what the
// Best Combo table is showing.
import React, { useEffect, useMemo, useState } from 'react';
import { InfoIcon } from './InfoIcon';
import { ExcelButton, exportRowsAsXlsx } from './exportXlsx';
import {
  fetchM7BestComboMissedFridays,
  type M7MissedFridaysForceFitResponse,
  type FetchBestComboArgs,
} from '../../services/m7_api';

export function M7BestComboMissedFridaysTable({ args }: { args: FetchBestComboArgs }) {
  const [resp, setResp] = useState<M7MissedFridaysForceFitResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7BestComboMissedFridays(args, ac.signal)
      .then(r => { if (active) setResp(r); })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(args)]);

  const rows = resp?.rows ?? [];
  const picks = resp?.picks ?? [];
  const stats = resp
    ? { n_missed: resp.n_missed ?? 0, n_total: resp.n_total_fridays ?? 0, n_matched: resp.n_matched ?? 0 }
    : null;

  const th: React.CSSProperties = { padding: '6px 8px', color: '#7a9bb5', whiteSpace: 'nowrap' };
  const thR: React.CSSProperties = { ...th, textAlign: 'right' };
  const td: React.CSSProperties = { padding: '5px 8px', whiteSpace: 'nowrap' };
  const tdR: React.CSSProperties = { ...td, textAlign: 'right' };

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1f6feb', borderRadius: 6,
      padding: 12, marginTop: 10, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8, cursor: 'pointer',
      }} onClick={() => setCollapsed(c => !c)}>
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          {collapsed ? '▸' : '▾'} Missed Fridays — Best Combo picker
          <InfoIcon text="Fridays NOT covered by any of the 10 IV-band Best Combo picks under your CURRENT sizing + filter settings (Capital, DD cap, Hit %, Min n, Max loss %, etc.). Different from the Missed Fridays table above which uses the simpler headline picker — this one reflects your actual Best Combo state. Even when a Friday is missed by the band-aware picker, the (hour, expiry, Δ) trade may exist — it just landed in a different IV band. The ✓/✗ matrix shows for each pick: would the trade have existed on that Friday at this (hour, expiry, Δ)?" />
          {stats && (
            <span style={{ fontWeight: 400, color: '#7a9bb5', marginLeft: 8, fontSize: 12 }}>
              ({stats.n_missed} of {stats.n_total} Fridays not covered • {stats.n_matched} matched)
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ fontSize: 11, color: '#7a9bb5' }}>
            {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span> : `${rows.length} rows`}
          </div>
          <span onClick={e => e.stopPropagation()}>
            <ExcelButton
              disabled={rows.length === 0}
              onClick={() => exportRowsAsXlsx('m7_best_combo_missed_fridays.xlsx', 'MissedFridays', rows.map(r => ({
                friday_date_ist: r.friday_date_ist,
                n_total_trades: r.n_total_trades,
                bands_touched: r.bands_touched.join(', '),
                fits_picks: r.pick_availability.filter(p => p.fits).length,
                ...Object.fromEntries(r.pick_availability.map(p => [`fit_${p.pick_band}`, p.fits ? 'Y' : 'N'])),
              })))} />
          </span>
        </div>
      </div>
      {!collapsed && !err && (
        <div style={{ overflowX: 'auto' }}>
          {rows.length === 0 && !loading && (
            <div style={{ color: '#7a9bb5', fontSize: 12, padding: '10px 0' }}>
              {resp?.status === 'no_picks'
                ? 'No picks under current filters — nothing to compute missed Fridays against.'
                : stats?.n_missed === 0
                  ? <span style={{ color: '#3fb950' }}>✓ All {stats.n_total} Fridays covered by the 10 picks.</span>
                  : 'No data.'}
            </div>
          )}
          {rows.length > 0 && (
            <table style={{
              borderCollapse: 'collapse', fontSize: 12,
              fontVariantNumeric: 'tabular-nums', color: '#cfd9e3',
              opacity: loading ? 0.4 : 1, transition: 'opacity 120ms',
            }}>
              <thead>
                <tr style={{ textAlign: 'left' }}>
                  <th style={th}>Friday <InfoIcon text="Friday date (IST) — present in dataset but not covered by any Best Combo pick under your settings." /></th>
                  <th style={thR}>n trades <InfoIcon text="Total trades in the enriched dataset for this Friday across ALL hour×expiry×Δ combos." /></th>
                  <th style={th}>Bands touched <InfoIcon text="Which IV bands this Friday's IV was in across its hourly entries (most Fridays span 2-7 bands)." /></th>
                  <th style={thR}>Fits picks <InfoIcon text="Of the 10 Best Combo picks, how many have a trade at their (hour, expiry, Δ) for this Friday. The trade may exist even though the Friday's IV landed in a different band." /></th>
                  {picks.map(p => (
                    <th key={`th-${p.band}`} style={th}
                        title={`Pick for band ${p.band}: ${p.entry_hour_ist}:00 / ${p.expiry_bucket} / Δ=${p.delta_target.toFixed(2)} / ${p.rule_label}. ✓ = trade exists for this Friday at the (hour, expiry, Δ); ✗ = no such trade.`}>
                      {p.band}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={`${r.friday_date_ist}-${i}`} style={{ borderTop: '1px solid #1a2d42' }}>
                    <td style={{ ...td, fontWeight: 600 }}>{r.friday_date_ist}</td>
                    <td style={tdR}>{r.n_total_trades}</td>
                    <td style={{ ...td, color: '#7a9bb5', fontSize: 11 }}>{r.bands_touched.join(', ')}</td>
                    <td style={tdR}>{(() => {
                      const fits = r.pick_availability.filter(p => p.fits).length;
                      const total = r.pick_availability.length;
                      const color = fits === total ? '#3fb950' : fits >= total / 2 ? '#f0b300' : '#f85149';
                      return <span style={{ color }}>{fits}/{total}</span>;
                    })()}</td>
                    {r.pick_availability.map((p, idx) => (
                      <td key={`row-${i}-${idx}`} style={{ ...td, textAlign: 'center' }}>
                        {p.fits ? <span style={{ color: '#3fb950', fontWeight: 700 }}>✓</span>
                                : <span style={{ color: '#f85149' }}>✗</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
