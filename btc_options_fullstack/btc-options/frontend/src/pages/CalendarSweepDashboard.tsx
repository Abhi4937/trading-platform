import React, { useCallback, useEffect, useState } from 'react';
import { fetchM7Summary, type M7LossesCell } from '../services/m7_api';
import type { M7ExitRule, M7Filters, M7Summary } from '../types/m7';
import type { M7IvBandBestComboRow } from '../services/m7_api';
import { M7BestComboPathMarkers } from '../components/m7/M7BestComboPathMarkers';
import { M7HeadlineStrip } from '../components/m7/M7HeadlineStrip';
import { M7IvBandBestComboTable } from '../components/m7/M7IvBandBestComboTable';
import { M7LossesExplorer } from '../components/m7/M7LossesExplorer';
import { M7PivotProfilePanel } from '../components/m7/M7PivotProfilePanel';

// Backwardation double-calendar sweep — a dedicated, gap-band-grounded dashboard.
// Reuses the dataset-parameterized M7 components hardwired to dataset="calendar":
// the components relabel IV band → Gap bucket / Expiry → Pair and hide the
// single-rule-inapplicable exit-rule controls (see dimLabels + the `isCal` branches).
// No dataset toggle, no Joint-Match (price-match-only) panel, no Stage-1 partial-exit
// sweep (exit-rule-specific — N/A for the single hold_to_settlement rule).
const DATASET = 'calendar' as const;

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [key, delay]);
  return debounced;
}

export function CalendarSweepDashboard() {
  const [filters] = useState<M7Filters>({});
  const [exitRule] = useState<M7ExitRule>({});
  const [summary, setSummary] = useState<M7Summary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [metric] = useState<string>('avg_net_pnl');
  const [error, setError] = useState<string | null>(null);

  const dFilters = useDebouncedValue(filters, 250);
  const dExitRule = useDebouncedValue(exitRule, 250);
  const dMetric = useDebouncedValue(metric, 150);

  // Per-gap-bucket best-combo selections lifted from the table → feed the losses
  // explorer + pivot so they scope to exactly what the table is showing.
  const [bestCells, setBestCells] = useState<M7LossesCell[]>([]);
  const dBestCells = useDebouncedValue(bestCells, 250);
  const handleSelectionsChange = useCallback((rows: M7IvBandBestComboRow[]) => {
    const cells: M7LossesCell[] = rows
      .filter(r => r.iv_band && r.expiry_bucket && r.delta_target != null && r.rule)
      .map(r => ({
        entry_atm_iv_band: r.iv_band,
        entry_hour_ist: r.entry_hour_ist ?? null,
        expiry_bucket: r.expiry_bucket,
        delta_target: r.delta_target,
        rule: r.rule,
        rule_label: r.rule_label,
        lots: r.lots ?? null,
      }));
    setBestCells(cells);
  }, []);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setSummaryLoading(true);
    setError(null);
    const tick = () => {
      if (!active) return;
      fetchM7Summary(dFilters, dExitRule, ac.signal, DATASET)
        .then(s => { if (active) { setSummary(s); setSummaryLoading(false); } })
        .catch(e => {
          if (!active || e?.name === 'AbortError') return;
          const msg = String(e ?? '');
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
        Failed to load Calendar Sweep data: {error}
        <div style={{ color: '#7a9bb5', marginTop: 6, fontSize: 11 }}>
          Hint: run <code style={{ color: '#cfd9e3' }}>python -m app.analytics.calendar_batch_backtester</code>
          {' '}then <code style={{ color: '#cfd9e3' }}>python -m app.scripts.build_m7_best_combo_grid_calendar</code>.
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 14, color: '#cfd9e3', minHeight: '100vh', overflowY: 'auto', height: '100%' }}>
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: '#cfd9e3' }}>
          Calendar Sweep — Backwardation double calendar
        </div>
        <div style={{ fontSize: 11, color: '#7a9bb5', marginTop: 2 }}>
          Every backwardation observation (gap ≥ 5pp) as a delta-matched-near /
          same-strike-far double calendar, held to near-expiry settlement. Best combo
          per gap bucket × pair × Δ (hours aggregated).
        </div>
      </div>

      <M7HeadlineStrip summary={summary} loading={summaryLoading} dataset={DATASET} />

      <M7IvBandBestComboTable
        onSelectionsChange={handleSelectionsChange}
        dataset={DATASET}
      />
      <M7BestComboPathMarkers filters={dFilters} exitRule={dExitRule} metric={dMetric}
                              dataset={DATASET} />

      <M7LossesExplorer filters={dFilters} exitRule={dExitRule} metric={dMetric}
                        cells={dBestCells} dataset={DATASET} />

      <M7PivotProfilePanel dataset={DATASET} cells={dBestCells} />
    </div>
  );
}
