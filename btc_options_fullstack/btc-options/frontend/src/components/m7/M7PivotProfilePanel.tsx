import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7PivotProfileCells, type M7Dataset, type M7LossesCell,
         type M7PivotByBand,
         type M7PivotProfileResponse } from '../../services/m7_api';
import { usePersistedState } from '../../hooks/usePersistedState';
import { M7PivotProfileChart } from './M7PivotProfileChart';
import { M7PivotProfileTable } from './M7PivotProfileTable';

interface Props {
  dataset: M7Dataset;
  cells: M7LossesCell[];
}

type View = 'all' | 'winners' | 'losers';

export function M7PivotProfilePanel({ dataset, cells }: Props) {
  const [data, setData] = useState<M7PivotProfileResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = usePersistedState<boolean>(
    'm7:pivotProfile:collapsed', false);
  const [view, setView] = usePersistedState<View>(
    'm7:pivotProfile:view', 'all');

  // Derive stable serialized scope so React only re-runs when the actual
  // (band, hour, expiry, delta, rule) set changes.
  const scopedCells = useMemo(() => {
    return cells
      .filter(c =>
        c.entry_atm_iv_band && c.entry_hour_ist != null
        && c.expiry_bucket && c.delta_target != null)
      .map(c => ({
        entry_atm_iv_band: c.entry_atm_iv_band,
        entry_hour_ist: c.entry_hour_ist as number,
        expiry_bucket: c.expiry_bucket,
        delta_target: c.delta_target,
        rule: c.rule as Record<string, number> | undefined,
        lots: c.lots ?? null,
      }));
  }, [cells]);
  const cellsKey = useMemo(() => JSON.stringify(
    scopedCells.map(c => [
      c.entry_atm_iv_band, c.entry_hour_ist,
      c.expiry_bucket, c.delta_target,
      JSON.stringify(c.rule || {}),
      c.lots,
    ]).sort()), [scopedCells]);

  useEffect(() => {
    if (collapsed) return;
    setErr(null);
    if (scopedCells.length === 0) {
      setData(null);
      return;
    }
    let active = true;
    let timerId: number | null = null;
    const tick = () => {
      fetchM7PivotProfileCells(scopedCells, dataset)
        .then(resp => {
          if (!active) return;
          setData(resp);
          if (resp.status === 'warming') {
            timerId = window.setTimeout(tick, 2000);
          } else if (resp.status === 'error') {
            setErr(resp.error ?? 'unknown error');
          }
        })
        .catch(e => {
          if (active) setErr(String(e));
        });
    };
    tick();
    return () => {
      active = false;
      if (timerId != null) window.clearTimeout(timerId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cellsKey, dataset, collapsed]);

  const cellSummary = scopedCells.length === 0
    ? null
    : scopedCells.slice(0, 8).map(c => {
        const lotsTag = c.lots != null && c.lots > 0
          ? `·${c.lots}lots`
          : '';
        return `${c.entry_atm_iv_band}@${String(c.entry_hour_ist).padStart(2, '0')}IST/${c.expiry_bucket}/Δ${c.delta_target}${lotsTag}`;
      });

  const minN = data?.min_trades_per_band_cell ?? 5;
  const result = data?.status === 'ready' ? data.result : null;

  // Pick the visible by_band block based on the view toggle.
  const visibleByBand: M7PivotByBand = useMemo(() => {
    if (!result) return {};
    if (view === 'winners' && result.by_band_winners) return result.by_band_winners;
    if (view === 'losers' && result.by_band_losers) return result.by_band_losers;
    return result.by_band;
  }, [result, view]);

  const nVisible = useMemo(() => {
    let total = 0;
    for (const segs of Object.values(visibleByBand)) {
      const s = segs.Seg1;
      if (s?.n_trades) total = Math.max(total, s.n_trades);
    }
    return total;
  }, [visibleByBand]);

  return (
    <section style={{ marginTop: 18, background: '#0d1421',
                       border: '1px solid #1a2d42', borderRadius: 6,
                       padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                     flexWrap: 'wrap', marginBottom: 10 }}>
        <button onClick={() => setCollapsed(!collapsed)}
                style={{ background: 'transparent', border: 'none',
                         color: '#cfd9e3', cursor: 'pointer',
                         fontSize: 14, fontWeight: 600, padding: 0 }}>
          {collapsed ? '▶' : '▼'} Pivot Profile by IV Band (5 IST segments)
        </button>
        <div style={{ flex: 1 }} />
        <ViewToggle view={view} setView={setView}
                     enabled={result != null && result.by_band_winners != null} />
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>
          Scoped to Best Combo per IV Band selection above ·{' '}
          <strong style={{ color: '#cfd9e3' }}>
            {scopedCells.length} cells
          </strong>
        </span>
      </div>
      {!collapsed && cellSummary && (
        <div style={{ fontSize: 10, color: '#586e7e', marginBottom: 8,
                       fontFamily: 'monospace' }}>
          {cellSummary.join(' · ')}
          {scopedCells.length > 8 && ` · … (+${scopedCells.length - 8} more)`}
        </div>
      )}
      {!collapsed && scopedCells.length > 0 && (
        <div style={{ fontSize: 10, color: '#586e7e', marginBottom: 8 }}>
          Values are <strong style={{ color: '#7a9bb5' }}>gross MTM</strong>{' '}
          (no exit slippage/brokerage) from the 1m path parquet, scaled by{' '}
          lots / 100 to match each band's Best-Combo lot count.
        </div>
      )}
      {collapsed && (
        <div style={{ color: '#586e7e', fontSize: 11 }}>
          Click ▶ above to expand.
        </div>
      )}
      {!collapsed && scopedCells.length === 0 && (
        <div style={{ color: '#7a9bb5', fontSize: 12, padding: 8 }}>
          Pick best-combo cells in the IV-Band Best Combo table above to scope
          this panel.
        </div>
      )}
      {!collapsed && err && (
        <div style={{ color: '#f85149', fontSize: 12, padding: 8 }}>
          Failed: {err}
        </div>
      )}
      {!collapsed && !err && data && data.status === 'warming' && (
        <div style={{ color: '#7a9bb5', fontSize: 12, padding: 8 }}>
          Building pivot profile…
          {data.progress != null && ` ${Math.round(data.progress * 100)}%`}
        </div>
      )}
      {!collapsed && !err && result && (
        <>
          <div style={{ fontSize: 11, color: '#586e7e', marginBottom: 8 }}>
            {view === 'all' && <>
              Aggregated over{' '}
              {result.params.n_after_filter.toLocaleString()}{' '}
              trades matching the {scopedCells.length} selected cells.
            </>}
            {view === 'winners' && <>
              <span style={{ color: '#3fb950' }}>Winners</span>{' '}
              ({(result.params.n_winners ?? 0).toLocaleString()} trades net&nbsp;P&amp;L&nbsp;&gt;&nbsp;$0
              ){' — '}same {scopedCells.length} cell scope.
            </>}
            {view === 'losers' && <>
              <span style={{ color: '#f85149' }}>Losers</span>{' '}
              ({(result.params.n_losers ?? 0).toLocaleString()} trades net&nbsp;P&amp;L&nbsp;≤&nbsp;$0
              ){' — '}same {scopedCells.length} cell scope.
            </>}
          </div>
          <M7PivotProfileChart byBand={visibleByBand} minTrades={minN} />
          <M7PivotProfileTable byBand={visibleByBand} minN={minN} />
        </>
      )}
    </section>
  );
}

function ViewToggle({ view, setView, enabled }: {
  view: View; setView: (v: View) => void; enabled: boolean;
}) {
  const btn = (active: boolean, color: string): React.CSSProperties => ({
    padding: '4px 10px',
    background: active ? color : '#0d1421',
    color: active ? '#ffffff' : '#7a9bb5',
    border: `1px solid ${active ? color : '#1a2d42'}`,
    borderRadius: 4,
    cursor: enabled ? 'pointer' : 'not-allowed',
    fontSize: 11, fontWeight: 600,
    opacity: enabled ? 1 : 0.5,
  });
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
      <button style={btn(view === 'all', '#1f6feb')}
              disabled={!enabled}
              onClick={() => setView('all')}>
        All
      </button>
      <button style={btn(view === 'winners', '#1f8a47')}
              disabled={!enabled}
              onClick={() => setView('winners')}>
        Winners
      </button>
      <button style={btn(view === 'losers', '#a32621')}
              disabled={!enabled}
              onClick={() => setView('losers')}>
        Losers
      </button>
    </div>
  );
}
