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
import { usePersistedState } from '../../hooks/usePersistedState';
import {
  fetchM7BestComboMissedFridays,
  type M7Dataset,
  type M7MissedFridaysForceFitResponse,
  type FetchBestComboArgs,
} from '../../services/m7_api';

export function M7BestComboMissedFridaysTable({ args, dataset }: { args: FetchBestComboArgs; dataset?: M7Dataset }) {
  const [resp, setResp] = useState<M7MissedFridaysForceFitResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  // Manual compute mode: this endpoint takes ~25s on cold cache, so by
  // default we don't auto-fire on filter change. User clicks Compute to
  // opt in. Mode is persisted across reloads.
  const [autoMode, setAutoMode] = usePersistedState<boolean>(
    'm7:best_combo_missed_fridays:auto', false);
  // Increments when user clicks Compute — used to force a re-fetch even when
  // args haven't changed (e.g. backend cache was warmed externally).
  const [computeCounter, setComputeCounter] = useState(0);
  // Marks whether the user has issued at least one compute click in this
  // session — controls the placeholder vs. data view.
  const [hasComputed, setHasComputed] = useState(false);

  useEffect(() => {
    // In manual mode (default), skip the fetch unless the user clicked
    // Compute. In auto mode, fetch on every filter change with debounce.
    if (!autoMode && computeCounter === 0) return;

    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    // Debounce: missed_fridays is an expensive backend endpoint (~25s on
    // cold cache, even after parallelization). Rapid filter changes used
    // to fire one request per change and pile them up in the backend
    // semaphore queue. Wait 300ms after the last change before firing.
    const DEBOUNCE_MS = 300;
    const tick = () => {
      if (!active) return;
      fetchM7BestComboMissedFridays(args, ac.signal, dataset)
        .then(r => { if (active) { setResp(r); setHasComputed(true); } })
        .catch(e => {
          if (!active || e?.name === 'AbortError') return;
          const msg = String(e ?? '');
          if (/\b500\b|NetworkError|Failed to fetch|ECONNRESET|fetch failed/i.test(msg)) {
            window.setTimeout(() => { if (active) tick(); }, 2000);
          } else {
            setErr(msg);
          }
        })
        .finally(() => { if (active) setLoading(false); });
    };
    const debounceTimer = window.setTimeout(tick, DEBOUNCE_MS);
    return () => {
      active = false;
      window.clearTimeout(debounceTimer);
      ac.abort();
    };
  }, [JSON.stringify(args), dataset, autoMode, computeCounter]);

  const rows = resp?.rows ?? [];
  const picks = resp?.picks ?? [];
  const stats = resp
    ? {
        n_missed: resp.n_missed ?? 0,
        n_total: resp.n_total_fridays ?? 0,
        n_matched: resp.n_matched ?? 0,
        n_rescuable: resp.n_rescuable ?? 0,
      }
    : null;

  const fmtUsd = (v: number): string => {
    const sign = v < 0 ? '-' : '';
    const abs = Math.abs(v);
    if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(1)}K`;
    return `${sign}$${abs.toFixed(0)}`;
  };

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
              ({stats.n_missed} of {stats.n_total} Fridays not covered • {stats.n_matched} matched
              {stats.n_rescuable > 0 && (
                <> • <span style={{ color: '#3fb950' }}>{stats.n_rescuable} rescuable</span></>
              )})
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Manual compute controls: this endpoint takes ~25s on cold cache, so
              default to manual mode. Compute button fires the fetch once; Auto
              toggle enables auto-fetch on every filter change (with 300ms debounce). */}
          <span onClick={e => e.stopPropagation()}>
            <button
              onClick={() => setComputeCounter(c => c + 1)}
              disabled={loading}
              style={{
                background: loading ? '#1a2d42' : '#1f6feb',
                color: loading ? '#7a9bb5' : '#fff',
                border: 'none', borderRadius: 3, padding: '3px 10px',
                fontSize: 11, cursor: loading ? 'default' : 'pointer',
              }}
              title="Run missed_fridays now with current filters">
              {loading ? 'Computing…' : hasComputed ? 'Recompute' : 'Compute'}
            </button>
          </span>
          <span onClick={e => e.stopPropagation()} style={{ fontSize: 10, color: '#7a9bb5', display: 'flex', alignItems: 'center', gap: 4 }}
                title="When on: auto-fetch on every filter change (with 300ms debounce). When off: only the Compute button fires the request.">
            <input type="checkbox" checked={autoMode} onChange={e => setAutoMode(e.target.checked)} />
            auto
          </span>
          <div style={{ fontSize: 11, color: '#7a9bb5' }}>
            {loading ? 'Loading…' : err ? <span style={{ color: '#f85149' }}>{err}</span>
              : !hasComputed ? <span style={{ color: '#7a9bb5' }}>click Compute</span>
              : `${rows.length} rows`}
          </div>
          <span onClick={e => e.stopPropagation()}>
            <ExcelButton
              disabled={rows.length === 0}
              onClick={() => exportRowsAsXlsx('m7_best_combo_missed_fridays.xlsx', 'MissedFridays', rows.map(r => ({
                friday_date_ist: r.friday_date_ist,
                n_total_trades: r.n_total_trades,
                bands_touched: r.bands_touched.join(', '),
                fits_picks: r.pick_availability.filter(p => p.fits).length,
                rescued_band: r.rescue?.rescued_band ?? '',
                rescued_rule: r.rescue?.rescued_rule_label ?? '',
                rescued_net_pnl: r.rescue?.rescued_net_pnl ?? '',
                rescued_outcome: r.rescue ? (r.rescue.rescued_is_win ? 'win' : 'loss') : '',
                ...Object.fromEntries(r.pick_availability.map(p => [`fit_${p.pick_band}`, p.fits ? 'Y' : 'N'])),
              })))} />
          </span>
        </div>
      </div>
      {!collapsed && !err && (
        <div style={{ overflowX: 'auto' }}>
          {!hasComputed && !loading && (
            <div style={{ color: '#7a9bb5', fontSize: 12, padding: '10px 0' }}>
              Click <strong style={{ color: '#1f6feb' }}>Compute</strong> to run missed-Fridays for the current filter combo
              (~5-25s cold cache, instant if previously cached).
              {' '}Or enable <strong>auto</strong> to fetch on every filter change.
            </div>
          )}
          {hasComputed && rows.length === 0 && !loading && (
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
                  <th style={th}>Rescue to <InfoIcon text="Best fitting pick — the one whose RULE-derived net P&L on this Friday is highest. Shown band • rule label. If you relaxed the band-match constraint, this Friday would naturally be absorbed by that pick." /></th>
                  <th style={thR}>Rescue P&L <InfoIcon text="Net P&L if that pick's rule had been applied to this Friday at its (hour, expiry, Δ). Green = win, red = loss." /></th>
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
                    <td style={td}>{r.rescue ? (
                      <span title={r.rescue.rescued_rule_label}>
                        <span style={{ color: '#3fb950', fontWeight: 600 }}>{r.rescue.rescued_band}</span>
                        <span style={{ color: '#7a9bb5', fontSize: 11, marginLeft: 4 }}>
                          • {r.rescue.rescued_rule_label}
                        </span>
                      </span>
                    ) : <span style={{ color: '#586e7e' }}>—</span>}</td>
                    <td style={tdR}>{r.rescue ? (
                      <span style={{ color: r.rescue.rescued_is_win ? '#3fb950' : '#f85149', fontWeight: 600 }}>
                        {fmtUsd(r.rescue.rescued_net_pnl)}
                      </span>
                    ) : <span style={{ color: '#586e7e' }}>—</span>}</td>
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
