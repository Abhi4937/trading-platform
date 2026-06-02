import React, { useCallback, useEffect, useState } from 'react';
import { fetchM7Summary, type M7Dataset, type M7LossesCell, type Stage1PerBandRule } from '../services/m7_api';
import type { M7ExitRule, M7Filters, M7Summary } from '../types/m7';
import { M7BestComboPathMarkers } from '../components/m7/M7BestComboPathMarkers';
import { M7HeadlineStrip } from '../components/m7/M7HeadlineStrip';
import { M7IvBandBestComboTable } from '../components/m7/M7IvBandBestComboTable';
import { M7BestComboCoverageTable } from '../components/m7/M7BestComboCoverageTable';
import { M7JointMatchStats } from '../components/m7/M7JointMatchStats';
import type { M7IvBandBestComboRow } from '../services/m7_api';
import { M7LossesExplorer } from '../components/m7/M7LossesExplorer';
import { M7PivotProfilePanel } from '../components/m7/M7PivotProfilePanel';
import { M7Stage1Panel } from '../components/m7/M7Stage1Panel';
import { usePersistedState } from '../hooks/usePersistedState';

class JointStatsBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error('M7JointMatchStats crashed:', error); }
  render() {
    if (this.state.error) {
      return (
        <div style={{ background: '#3a1f1f', border: '1px solid #b94a4a',
                      color: '#ffb4b4', padding: 10, borderRadius: 4,
                      margin: '8px 0', fontSize: 12 }}>
          Joint-match stats panel failed to render ({this.state.error.message}).
          Toggle back to Δ-match to recover.
        </div>
      );
    }
    return this.props.children;
  }
}

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

// Allowed dataset values. Used to validate the persisted localStorage value
// on first mount — falls back to delta_match if the stored entry is stale or
// not in this whitelist.
const DATASET_VALUES: M7Dataset[] = ['delta_match', 'price_match', 'calendar'];

function isValidDataset(v: unknown): v is M7Dataset {
  return typeof v === 'string' && (DATASET_VALUES as string[]).includes(v);
}

export function M7SweepDashboard() {
  const [filters] = useState<M7Filters>({});
  const [exitRule] = useState<M7ExitRule>({});
  // Resolved per-band rules hoisted from M7IvBandBestComboTable after each fetch.
  const [resolvedRules, setResolvedRules] = useState<Record<string, Stage1PerBandRule>>({});
  const [resolvedDataset, setResolvedDataset] = useState<M7Dataset>('delta_match');
  const handleResolvedRulesChange = useCallback(
    (rules: Record<string, Stage1PerBandRule>, ds: string) => {
      setResolvedRules(rules);
      setResolvedDataset(ds as M7Dataset);
    },
    [],
  );
  const [summary, setSummary] = useState<M7Summary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [metric] = useState<string>('avg_net_pnl');
  const [error, setError] = useState<string | null>(null);

  // Joint Δ+Price-match dataset toggle. Persisted to localStorage so the
  // choice survives mode switches and reloads. Defensive: validate stored
  // value on every read so stale entries can't break the UI.
  const [datasetRaw, setDataset] = usePersistedState<M7Dataset>(
    'm7:dataset', 'delta_match');
  const dataset: M7Dataset = isValidDataset(datasetRaw) ? datasetRaw : 'delta_match';

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
          lots: r.lots ?? null,
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
      fetchM7Summary(dFilters, dExitRule, ac.signal, dataset)
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
  }, [JSON.stringify(dFilters), JSON.stringify(dExitRule), dataset]);

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
      <DatasetToggle dataset={dataset} onChange={setDataset} />

      <M7HeadlineStrip summary={summary} loading={summaryLoading} />

      <JointStatsBoundary>
        <M7JointMatchStats dataset={dataset} />
      </JointStatsBoundary>

      <M7IvBandBestComboTable
        onSelectionsChange={handleSelectionsChange}
        onResolvedRulesChange={handleResolvedRulesChange}
        dataset={dataset}
      />
      <M7BestComboCoverageTable dataset={dataset} />
      <M7BestComboPathMarkers filters={dFilters} exitRule={dExitRule} metric={dMetric}
                              dataset={dataset} />

      <M7LossesExplorer filters={dFilters} exitRule={dExitRule} metric={dMetric}
                        cells={dBestCells} dataset={dataset} />

      <M7PivotProfilePanel dataset={dataset} cells={dBestCells} />

      <M7Stage1Panel resolvedRules={resolvedRules} dataset={resolvedDataset} />

    </div>
  );
}

// ── Dataset toggle (2-button group) ──────────────────────────────────────────

function DatasetToggle({
  dataset, onChange,
}: { dataset: M7Dataset; onChange: (v: M7Dataset) => void }) {
  const btn = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px',
    background: active ? '#1f6feb' : '#0d1421',
    color: active ? '#ffffff' : '#cfd9e3',
    border: `1px solid ${active ? '#1f6feb' : '#1a2d42'}`,
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 600,
  });
  return (
    <div style={{
      display: 'flex', gap: 6, alignItems: 'center',
      marginBottom: 10, padding: '6px 0',
    }}>
      <span style={{ fontSize: 11, color: '#7a9bb5',
                     textTransform: 'uppercase', letterSpacing: 0.5,
                     fontWeight: 600, marginRight: 4 }}>
        Dataset:
      </span>
      <button style={btn(dataset === 'delta_match')}
              onClick={() => onChange('delta_match')}
              title="Today's behavior — strikes picked by closest |Δ| from below, independently per leg.">
        ◆ Δ-match
      </button>
      <button style={btn(dataset === 'price_match')}
              onClick={() => onChange('price_match')}
              title="Strikes jointly optimized to minimize both Δ deviation AND premium asymmetry, with fallback to Δ-only when no joint match fits within tolerance.">
        ◆ Δ+Price match
      </button>
      <button style={btn(dataset === 'calendar')}
              onClick={() => onChange('calendar')}
              title="Backwardation double calendar (gap ≥ 5pp): SELL near CE+PE, BUY far CE+PE; strike per side by near-leg nearest |Δ| (0.30/0.40/0.50), far reuses the same strike. Hold to near-expiry settlement. Best combo = best expiry pair per gap bucket (hours aggregated). IV band → Gap bucket, Expiry → Pair.">
        ◆ Calendar sweep
      </button>
    </div>
  );
}
