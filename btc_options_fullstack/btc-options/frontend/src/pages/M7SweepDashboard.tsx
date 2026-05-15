import React, { useCallback, useEffect, useState } from 'react';
import { fetchM7Summary, type M7LossesCell } from '../services/m7_api';
import type { M7ExitRule, M7Filters, M7Summary } from '../types/m7';
import { M7BestComboPathMarkers } from '../components/m7/M7BestComboPathMarkers';
import { M7HeadlineStrip } from '../components/m7/M7HeadlineStrip';
import { M7IvBandBestComboTable } from '../components/m7/M7IvBandBestComboTable';
import { M7BestComboCoverageTable } from '../components/m7/M7BestComboCoverageTable';
import type { M7IvBandBestComboRow } from '../services/m7_api';
import { M7LossesExplorer } from '../components/m7/M7LossesExplorer';

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

export function M7SweepDashboard() {
  const [filters] = useState<M7Filters>({});
  const [exitRule] = useState<M7ExitRule>({});
  const [summary, setSummary] = useState<M7Summary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [metric] = useState<string>('avg_net_pnl');
  const [error, setError] = useState<string | null>(null);

  // Debounce filter / exit-rule / metric so rapid changes coalesce into a
  // single backend round-trip instead of firing on every keystroke. The
  // FilterBar still sees raw filters/exitRule (instant UI feedback); only
  // the panels and summary fetch use the debounced versions.
  const dFilters  = useDebouncedValue(filters, 250);
  const dExitRule = useDebouncedValue(exitRule, 250);
  const dMetric   = useDebouncedValue(metric, 150);

  // Per-band best-combo selections lifted up from M7IvBandBestComboTable —
  // M7LossesExplorer uses them as its `cells` source so loss analysis tracks
  // exactly what the table is currently displaying (after all dashboard
  // filters). Debounced so rapid filter edits coalesce.
  const [bestCells, setBestCells] = useState<M7LossesCell[]>([]);
  const dBestCells = useDebouncedValue(bestCells, 250);
  const handleSelectionsChange = useCallback(
    (rows: M7IvBandBestComboRow[]) => {
      const cells: M7LossesCell[] = rows
        .filter(r => r.iv_band && r.expiry_bucket
          && r.delta_target != null && r.rule)
        .map(r => ({
          entry_atm_iv_band: r.iv_band,
          entry_hour_ist: r.entry_hour_ist ?? null,
          expiry_bucket: r.expiry_bucket,
          delta_target: r.delta_target,
          rule: r.rule,
          rule_label: r.rule_label,
        }));
      setBestCells(cells);
    },
    [],
  );

  useEffect(() => {
    let active = true;  // discards results from out-of-date requests
    const ac = new AbortController();
    setSummaryLoading(true);
    setError(null);
    const tick = () => {
      if (!active) return;
      fetchM7Summary(dFilters, dExitRule, ac.signal)
        .then(s => { if (active) { setSummary(s); setSummaryLoading(false); } })
        .catch(e => {
          if (!active || e?.name === 'AbortError') return;
          const msg = String(e ?? '');
          // Transient backend-warmup errors: retry. Surface only persistent ones.
          if (/\b500\b|\b502\b|\b503\b|\b504\b|NetworkError|Failed to fetch|ECONNRESET|fetch failed/i.test(msg)) {
            window.setTimeout(() => { if (active) tick(); }, 2000);
          } else {
            setError(msg);
            setSummaryLoading(false);
          }
        });
    };
    tick();
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(dFilters), JSON.stringify(dExitRule)]);

  if (error) {
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

      <M7IvBandBestComboTable onSelectionsChange={handleSelectionsChange} />
      <M7BestComboCoverageTable />
      <M7BestComboPathMarkers filters={dFilters} exitRule={dExitRule} metric={dMetric} />

      <M7LossesExplorer filters={dFilters} exitRule={dExitRule} metric={dMetric}
                        cells={dBestCells} />
    </div>
  );
}
