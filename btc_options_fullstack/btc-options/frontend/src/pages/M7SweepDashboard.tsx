import React, { useEffect, useState } from 'react';
import { fetchM7Meta, fetchM7Summary } from '../services/m7_api';
import type { M7ExitRule, M7Filters, M7Meta, M7Summary } from '../types/m7';
import { M7AggregateHeatmap } from '../components/m7/M7AggregateHeatmap';
import { M7BestComboTable } from '../components/m7/M7BestComboTable';
import { M7FilterBar } from '../components/m7/M7FilterBar';
import { M7HeadlineStrip } from '../components/m7/M7HeadlineStrip';
import { M7IvBandSummaryTable } from '../components/m7/M7IvBandSummaryTable';
import { M7TradeLogTable } from '../components/m7/M7TradeLogTable';
import { M7TradePathChart } from '../components/m7/M7TradePathChart';

export function M7SweepDashboard() {
  const [meta, setMeta] = useState<M7Meta | null>(null);
  const [filters, setFilters] = useState<M7Filters>({});
  const [exitRule, setExitRule] = useState<M7ExitRule>({});
  const [summary, setSummary] = useState<M7Summary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [metric, setMetric] = useState<string>('avg_net_pnl');
  const [selectedTrade, setSelectedTrade] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchM7Meta().then(setMeta).catch(e => setError(String(e)));
  }, []);

  useEffect(() => {
    setSummaryLoading(true);
    setError(null);
    fetchM7Summary(filters, exitRule)
      .then(setSummary)
      .catch(e => setError(String(e)))
      .finally(() => setSummaryLoading(false));
  }, [JSON.stringify(filters), JSON.stringify(exitRule)]);

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
    <div style={{ padding: 14, color: '#cfd9e3', minHeight: '100vh' }}>
      <M7HeadlineStrip summary={summary} loading={summaryLoading} />

      <M7FilterBar
        meta={meta}
        filters={filters} setFilters={setFilters}
        exitRule={exitRule} setExitRule={setExitRule}
      />

      <div style={{
        display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8,
        fontSize: 11, color: '#7a9bb5',
      }}>
        <span style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4 }}>Metric</span>
        {(['avg_net_pnl', 'win_rate', 'sum_net_pnl', 'count'] as const).map(m => (
          <button key={m} onClick={() => setMetric(m)}
                  style={{
                    padding: '3px 8px', fontSize: 11, cursor: 'pointer',
                    background: metric === m ? '#1f6feb' : '#0d1421',
                    color: metric === m ? '#fff' : '#7a9bb5',
                    border: '1px solid #1a2d42', borderRadius: 4,
                  }}>{m}</button>
        ))}
        {summary && (
          <span style={{ marginLeft: 'auto' }}>
            Exit reasons: {Object.entries(summary.exit_reason_counts).map(([k, v]) => `${k}=${v}`).join(' · ')}
          </span>
        )}
      </div>

      <M7IvBandSummaryTable exitRule={exitRule} metric={metric} />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <M7AggregateHeatmap
          title={`Δ × IV-band (${metric})`}
          rowKey="delta_target" colKey="entry_atm_iv_band"
          metric={metric} filters={filters} exitRule={exitRule}
          fmt={metric.includes('pnl') ? v => `$${v.toFixed(1)}`
               : metric === 'win_rate' ? v => `${(v * 100).toFixed(0)}%`
               : v => `${v}`}
        />
        <M7AggregateHeatmap
          title={`Entry-hour × IV-band (${metric})`}
          rowKey="entry_hour_ist" colKey="entry_atm_iv_band"
          metric={metric} filters={filters} exitRule={exitRule}
          fmt={metric.includes('pnl') ? v => `$${v.toFixed(1)}`
               : metric === 'win_rate' ? v => `${(v * 100).toFixed(0)}%`
               : v => `${v}`}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <M7AggregateHeatmap
          title={`DTE × Δ (${metric})`}
          rowKey="dte_bucket" colKey="delta_target"
          metric={metric} filters={filters} exitRule={exitRule}
          fmt={metric.includes('pnl') ? v => `$${v.toFixed(1)}`
               : metric === 'win_rate' ? v => `${(v * 100).toFixed(0)}%`
               : v => `${v}`}
        />
        <M7BestComboTable filters={filters} exitRule={exitRule} metric={metric} topN={20} />
      </div>

      <div style={{ marginTop: 10 }}>
        <M7TradeLogTable filters={filters} onSelect={setSelectedTrade} />
      </div>

      {selectedTrade && (
        <M7TradePathChart tradeId={selectedTrade} onClose={() => setSelectedTrade(null)} />
      )}
    </div>
  );
}
