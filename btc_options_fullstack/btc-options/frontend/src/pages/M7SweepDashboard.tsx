import React, { useEffect, useState } from 'react';
import { fetchM7Meta, fetchM7Summary } from '../services/m7_api';
import type { M7ExitRule, M7Filters, M7Meta, M7Summary } from '../types/m7';
import { M7BestComboPathMarkers } from '../components/m7/M7BestComboPathMarkers';
import { M7FilterBar } from '../components/m7/M7FilterBar';
import { M7HeadlineStrip } from '../components/m7/M7HeadlineStrip';
import { M7IvBandBestComboTable } from '../components/m7/M7IvBandBestComboTable';
import { M7IvBandFullCoverageTable } from '../components/m7/M7IvBandFullCoverageTable';
import { M7IvBandSummaryTable } from '../components/m7/M7IvBandSummaryTable';
import { M7LegAttributionTable } from '../components/m7/M7LegAttributionTable';
import { M7LegSkewHeatmap } from '../components/m7/M7LegSkewHeatmap';
import { M7LossesExplorer } from '../components/m7/M7LossesExplorer';
import { M7MissedFridaysTable } from '../components/m7/M7MissedFridaysTable';
import { M7TradePathChart } from '../components/m7/M7TradePathChart';

// Returns a value that only updates after `delay` ms of no change.
// Used so rapid filter / exit-rule edits don't fire a request on every keystroke.
function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [key, delay]);
  return debounced;
}

// Pick the right number formatter for each metric.
//   * win_rate is a 0..1 fraction → "%"
//   * pct_return_on_* are decimal returns → "%" with 2 decimals
//   * count is an integer
//   * everything else (P&L, MTM, credit, margin) is USD
function fmtFor(metric: string): (v: number) => string {
  if (metric === 'win_rate') return v => `${(v * 100).toFixed(0)}%`;
  if (metric === 'avg_pct_return_on_margin' || metric === 'avg_pct_return_on_credit' ||
      metric === 'avg_pct_return_on_margin_winners' || metric === 'avg_pct_return_on_credit_winners') {
    return v => `${(v * 100).toFixed(2)}%`;
  }
  if (metric === 'count' ||
      metric === 'n_wins' || metric === 'n_losses' ||
      metric === 'n_rule_trigger' || metric === 'n_hard_cap' ||
      metric === 'max_consec_losses' || metric === 'max_consec_wins' ||
      metric === 'max_consec_sl_hits' ||
      metric === 'n_winners_below_avg_min_mtm' ||
      metric === 'n_losers_above_avg_max_mtm') return v => `${v}`;
  return v => `$${v.toFixed(1)}`;
}

export function M7SweepDashboard() {
  const [meta, setMeta] = useState<M7Meta | null>(null);
  const [filters, setFilters] = useState<M7Filters>({});
  const [exitRule, setExitRule] = useState<M7ExitRule>({});
  const [summary, setSummary] = useState<M7Summary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [metric, setMetric] = useState<string>('avg_net_pnl');
  const [selectedTrade, setSelectedTrade] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Debounce filter / exit-rule / metric so rapid changes coalesce into a
  // single backend round-trip instead of firing on every keystroke. The
  // FilterBar still sees raw filters/exitRule (instant UI feedback); only
  // the panels and summary fetch use the debounced versions.
  const dFilters  = useDebouncedValue(filters, 250);
  const dExitRule = useDebouncedValue(exitRule, 250);
  const dMetric   = useDebouncedValue(metric, 150);

  useEffect(() => {
    fetchM7Meta().then(setMeta).catch(e => setError(String(e)));
  }, []);

  useEffect(() => {
    let active = true;  // discards results from out-of-date requests
    const ac = new AbortController();
    setSummaryLoading(true);
    setError(null);
    fetchM7Summary(dFilters, dExitRule, ac.signal)
      .then(s => { if (active) setSummary(s); })
      .catch(e => { if (active && e?.name !== 'AbortError') setError(String(e)); })
      .finally(() => { if (active) setSummaryLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(dFilters), JSON.stringify(dExitRule)]);

  if (error && !meta) {
    return (
      <div style={{ padding: 20, color: '#f85149', fontSize: 13 }}>
        Failed to load M7 data: {error}
        <div style={{ color: '#7a9bb5', marginTop: 6, fontSize: 11 }}>
          Hint: run <code style={{ color: '#cfd9e3' }}>python3 -m app.analytics.m7_batch_backtester</code>
          {' '}first to produce the parquet outputs.
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 14, color: '#cfd9e3', minHeight: '100vh', overflowY: 'auto', height: '100%' }}>
      <M7HeadlineStrip summary={summary} loading={summaryLoading} />

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
          <optgroup label="Exit MTM (entry costs only — what's on screen)">
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
            <option value="avg_pct_return_on_margin_winners">% return on margin (winners only)</option>
            <option value="avg_pct_return_on_credit_winners">% return on credit (winners only)</option>
          </optgroup>
          <optgroup label="Setup">
            <option value="avg_credit">Average credit</option>
            <option value="avg_margin">Average margin</option>
            <option value="count">Trade count</option>
          </optgroup>
          <optgroup label="Counts">
            <option value="n_wins">Number of wins</option>
            <option value="n_losses">Number of losses</option>
            <option value="n_rule_trigger">Number of SL hits (rule trigger)</option>
            <option value="n_hard_cap">Number of hard-cap exits</option>
            <option value="n_winners_below_avg_min_mtm">Winners with worse-than-avg drawdown</option>
            <option value="n_losers_above_avg_max_mtm">Losers with better-than-avg peak</option>
          </optgroup>
          <optgroup label="Streaks (chronological)">
            <option value="max_consec_losses">Max consecutive losses</option>
            <option value="max_consec_wins">Max consecutive wins</option>
            <option value="max_consec_sl_hits">Max consecutive SL hits</option>
          </optgroup>
        </select>
        {summary && (
          <span style={{ marginLeft: 'auto', fontSize: 10, color: '#5b7894' }}>
            Exit reasons: {Object.entries(summary.exit_reason_counts).map(([k, v]) => `${k}=${v}`).join(' · ')}
          </span>
        )}
      </div>

      <M7IvBandSummaryTable filters={dFilters} exitRule={dExitRule} metric={dMetric} />
      <M7IvBandFullCoverageTable filters={dFilters} exitRule={dExitRule} metric={dMetric} />
      <M7IvBandBestComboTable />
      <M7MissedFridaysTable filters={dFilters} exitRule={dExitRule} metric={dMetric} />
      <M7BestComboPathMarkers filters={dFilters} exitRule={dExitRule} metric={dMetric} />

      {/* Chunk 1 — Per-leg Attribution: skew heatmap + per-trade table.
          Section divider + heading match the visual rhythm of the rest of the dashboard. */}
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
            Why is one leg in standby while the other prints? Δ / IV / premium skew at entry +
            per-leg P&L decomposition at exit.
          </span>
        </div>
        <M7LegSkewHeatmap filters={dFilters} exitRule={dExitRule} />
        <M7LegAttributionTable filters={dFilters} exitRule={dExitRule}
                               onSelectTrade={setSelectedTrade} />
      </div>

      {/* Loss-anatomy Chunk 6 — universe-wide loss distribution + drilldown.
          Pass `dMetric` so scope=full_coverage best-cell selection matches
          what the table above shows. */}
      <M7LossesExplorer filters={dFilters} exitRule={dExitRule} metric={dMetric} />

      {selectedTrade && (
        <M7TradePathChart tradeId={selectedTrade} onClose={() => setSelectedTrade(null)} />
      )}
    </div>
  );
}
