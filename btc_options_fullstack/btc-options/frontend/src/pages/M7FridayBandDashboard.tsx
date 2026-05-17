// M7 Friday-Band parallel dashboard.
//
// Mirrors M7SweepDashboard but every section is powered by the
// Friday-locked-band data (modes A1 / B1 / D1). Full-coverage and missed-
// Fridays sections are dropped because Friday-locking guarantees every
// Friday lands in exactly one band — by construction.

import React, { useEffect, useState } from 'react';
import {
  fetchM7FridayBandSummary, fetchM7Meta,
  type M7FridayBandSummary,
} from '../services/m7_api';
import type { M7ExitRule, M7Filters, M7Meta } from '../types/m7';

import { M7BestComboPathMarkers } from '../components/m7/M7BestComboPathMarkers';
import { M7FridayBandMtmOverlayPanel } from '../components/m7/M7FridayBandMtmOverlayPanel';
import { M7FilterBar } from '../components/m7/M7FilterBar';
import { M7HeadlineStrip } from '../components/m7/M7HeadlineStrip';
import { M7FridayBandBestComboTable } from '../components/m7/M7FridayBandBestComboTable';
import {
  M7FridayBandHeaderControls,
  type FridayBandHeaderState,
} from '../components/m7/M7FridayBandHeaderControls';
import { M7IvBandSummaryTable } from '../components/m7/M7IvBandSummaryTable';
import { M7LegAttributionTable } from '../components/m7/M7LegAttributionTable';
import { M7LegSkewHeatmap } from '../components/m7/M7LegSkewHeatmap';
import { M7LossesExplorer } from '../components/m7/M7LossesExplorer';
import { M7TradePathChart } from '../components/m7/M7TradePathChart';

const LS_PREFIX = 'm7:fbdashboard:';
function loadLS<T>(key: string, fallback: T): T {
  try {
    const v = window.localStorage.getItem(LS_PREFIX + key);
    if (v == null) return fallback;
    return JSON.parse(v) as T;
  } catch { return fallback; }
}
function saveLS(key: string, val: unknown) {
  try { window.localStorage.setItem(LS_PREFIX + key, JSON.stringify(val)); } catch {}
}

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [key, delay]);
  return debounced;
}

export function M7FridayBandDashboard() {
  const [meta, setMeta] = useState<M7Meta | null>(null);
  const [filters, setFilters] = useState<M7Filters>(() => loadLS('filters', {} as M7Filters));
  const [exitRule, setExitRule] = useState<M7ExitRule>(() => loadLS('exit_rule', {} as M7ExitRule));
  const [metric, setMetric] = useState<string>(() => loadLS('metric', 'avg_net_pnl'));
  const [summary, setSummary] = useState<M7FridayBandSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [selectedTrade, setSelectedTrade] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [header, setHeader] = useState<FridayBandHeaderState>(() => loadLS('header', {
    bandMode: 'A1',
    d1Tiebreakers: ['best_avg_net_pnl'],
    pickMode: 'by_hour',
    ranking: 'sum_net_pnl',
  } as FridayBandHeaderState));

  useEffect(() => { saveLS('filters', filters); }, [filters]);
  useEffect(() => { saveLS('exit_rule', exitRule); }, [exitRule]);
  useEffect(() => { saveLS('metric', metric); }, [metric]);
  useEffect(() => { saveLS('header', header); }, [header]);

  const dFilters  = useDebouncedValue(filters, 250);
  const dExitRule = useDebouncedValue(exitRule, 250);
  const dMetric   = useDebouncedValue(metric, 150);
  const dHeader   = useDebouncedValue(header, 150);
  const d1Tb      = dHeader.bandMode === 'D1' ? dHeader.d1Tiebreakers : undefined;

  useEffect(() => {
    // jsonFetch handles 5xx auto-retry. FilterBar degrades gracefully on null meta.
    fetchM7Meta().then(setMeta).catch(e => setError(String(e)));
  }, []);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setSummaryLoading(true);
    setSummary(null);  // clear stale numbers while a new fetch is in-flight
    setError(null);
    // jsonFetch handles 5xx auto-retry — no need for per-call retry logic.
    fetchM7FridayBandSummary(dFilters, dExitRule, dHeader.bandMode, d1Tb, ac.signal)
      .then(s => { if (active) setSummary(s); })
      .catch(e => { if (active && e?.name !== 'AbortError') setError(String(e)); })
      .finally(() => { if (active) setSummaryLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(dFilters), JSON.stringify(dExitRule),
      dHeader.bandMode, JSON.stringify(d1Tb ?? [])]);

  // Don't tank the whole page on transient errors — render the dashboard
  // and let per-section error UI surface specifics. The build-progress bar
  // in the best-combo table is the user's signal during long D1 builds.

  return (
    <div style={{ padding: 14, color: '#cfd9e3', minHeight: '100vh', overflowY: 'auto', height: '100%' }}>
      <M7HeadlineStrip summary={summary} loading={summaryLoading} />

      <M7FridayBandHeaderControls value={header} onChange={setHeader} />

      <M7FilterBar
        meta={meta}
        filters={filters} setFilters={setFilters}
        exitRule={exitRule} setExitRule={setExitRule}
      />

      <div style={{
        display: 'flex', gap: 12, alignItems: 'center', marginBottom: 8,
        fontSize: 11, color: '#7a9bb5', flexWrap: 'wrap',
      }}>
        <span style={{
          fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4,
          color: '#cfd9e3',
        }}>PnL Analytics</span>
        <select value={metric} onChange={e => setMetric(e.target.value)}
                style={{
                  background: '#0d1421', color: '#cfd9e3',
                  border: '1px solid #1a2d42', borderRadius: 4,
                  padding: '4px 8px', fontSize: 12, minWidth: 220,
                }}>
          <optgroup label="P&L (net of all costs)">
            <option value="avg_net_pnl">Average net P&L</option>
            <option value="sum_net_pnl">Total net P&L</option>
            <option value="avg_gross_pnl">Average gross P&L</option>
            <option value="avg_win_usd">Average win</option>
            <option value="avg_loss_usd">Average loss</option>
            <option value="max_win_usd">Largest win (extreme)</option>
            <option value="max_loss_usd">Largest loss (extreme)</option>
          </optgroup>
          <optgroup label="Exit MTM (entry costs only)">
            <option value="avg_exit_mtm">Average exit MTM</option>
            <option value="avg_win_mtm">Avg win MTM</option>
            <option value="largest_win_mtm">Largest win MTM</option>
            <option value="avg_loss_mtm">Avg loss MTM</option>
            <option value="largest_loss_mtm">Largest loss MTM</option>
          </optgroup>
          <optgroup label="MTM — Winners only">
            <option value="avg_max_mtm_winners">Avg max MTM (winners)</option>
            <option value="avg_min_mtm_winners">Avg min MTM (winners)</option>
            <option value="max_mtm_winners">Max MTM (winners — extreme)</option>
            <option value="min_mtm_winners">Min MTM (winners — extreme)</option>
          </optgroup>
          <optgroup label="MTM — Losers only">
            <option value="avg_max_mtm_losers">Avg max MTM (losers)</option>
            <option value="avg_min_mtm_losers">Avg min MTM (losers)</option>
            <option value="max_mtm_losers">Max MTM (losers — extreme)</option>
            <option value="min_mtm_losers">Min MTM (losers — extreme)</option>
          </optgroup>
          <optgroup label="Quality">
            <option value="win_rate">Win rate</option>
            <option value="avg_pct_return_on_margin">% return on margin</option>
            <option value="avg_pct_return_on_credit">% return on credit</option>
          </optgroup>
          <optgroup label="Setup">
            <option value="avg_credit">Average credit</option>
            <option value="avg_margin">Average margin</option>
            <option value="count">Trade count</option>
          </optgroup>
        </select>
        {summary && (
          <span style={{ marginLeft: 'auto', fontSize: 10, color: '#5b7894' }}>
            band_mode={summary.band_mode} · #Fridays={summary.n_fridays_total} ·
            Per band: {Object.entries(summary.n_fridays_per_band)
              .sort((a, b) => {
                const ka = a[0] === '100+' ? 100 : parseInt(a[0].split('-')[0], 10);
                const kb = b[0] === '100+' ? 100 : parseInt(b[0].split('-')[0], 10);
                return ka - kb;
              })
              .map(([k, v]) => `${k}=${v}`).join(' · ')}
          </span>
        )}
      </div>

      {/* Scheme description + why-no-FC-banner */}
      <div style={{
        padding: 10, marginBottom: 12,
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        color: '#7a9bb5', fontSize: 11,
      }}>
        <div style={{ marginBottom: 4 }}>
          <span style={{ color: '#3fb950', fontWeight: 600 }}>📌 IV band source:</span>{' '}
          the <strong>current expiry's ATM IV</strong> is used — i.e. the
          Saturday daily-expiry, which is the same-day expiry for any trade
          entered after 17:30 IST on Friday. Mode A1 reads it at 21:00 IST;
          B1 takes the modal band across the 7 entry hours; D1 collapses
          per-hour bands via a prioritized tiebreaker chain.{' '}
          <strong>Picker expiry-bucket default</strong>: current (Sat) / next (Sun)
          / next_to_next (Mon) / weekly (7d) — the 4 popular expiries tradeable
          on every Friday. Use the filter bar's Expiry dropdown to override.
        </div>
        <div>
          <span style={{ color: '#e3b341', fontWeight: 600 }}>ℹ Why no Full Coverage / Missed Fridays?</span>{' '}
          Under Friday-locking every Friday is assigned to <em>exactly one</em> band
          by construction — the per-trade scheme's skip/duplicate problem doesn't
          exist here, so the diagnostic sections that solved it are dropped.
        </div>
      </div>

      <M7IvBandSummaryTable
        filters={dFilters} exitRule={dExitRule} metric={dMetric}
        useFridayBand bandMode={dHeader.bandMode} d1Tiebreakers={d1Tb}
      />

      <M7FridayBandBestComboTable
        controlled={{
          bandMode: dHeader.bandMode,
          d1Tiebreakers: dHeader.d1Tiebreakers,
          pickMode: dHeader.pickMode,
          ranking: dHeader.ranking,
        }}
      />

      <M7BestComboPathMarkers
        filters={dFilters} exitRule={dExitRule} metric={dMetric}
        useFridayBand bandMode={dHeader.bandMode} d1Tiebreakers={d1Tb}
      />

      <M7FridayBandMtmOverlayPanel
        filters={dFilters}
        exitRule={dExitRule}
        metric={dMetric}
        bandMode={dHeader.bandMode}
        d1Tiebreakers={d1Tb}
        onPickClick={(id) => setSelectedTrade(id)}
      />

      <div style={{
        marginTop: 18, paddingTop: 12,
        borderTop: '1px solid #1a2d42',
      }}>
        <div style={{
          fontSize: 14, fontWeight: 700, color: '#cfd9e3',
          textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 10,
        }}>
          Leg Attribution
          <span style={{ fontSize: 11, fontWeight: 400, color: '#7a9bb5', marginLeft: 10 }}>
            Δ / IV / premium skew at entry + per-leg P&L decomposition at exit.
          </span>
        </div>
        <M7LegSkewHeatmap filters={dFilters} exitRule={dExitRule} />
        <M7LegAttributionTable filters={dFilters} exitRule={dExitRule}
                               onSelectTrade={setSelectedTrade} />
      </div>

      <M7LossesExplorer
        filters={dFilters} exitRule={dExitRule} metric={dMetric}
        useFridayBand bandMode={dHeader.bandMode} d1Tiebreakers={d1Tb}
      />

      {selectedTrade && (
        <M7TradePathChart tradeId={selectedTrade} onClose={() => setSelectedTrade(null)} />
      )}
    </div>
  );
}
