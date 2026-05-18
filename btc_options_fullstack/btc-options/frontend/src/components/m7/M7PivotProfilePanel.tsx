import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7PivotProfileCells, type M7Dataset, type M7LossesCell,
         type M7PivotProfileResponse } from '../../services/m7_api';
import { usePersistedState } from '../../hooks/usePersistedState';
import { M7PivotProfileChart } from './M7PivotProfileChart';
import { M7PivotProfileTable } from './M7PivotProfileTable';

interface Props {
  dataset: M7Dataset;
  cells: M7LossesCell[];
}

export function M7PivotProfilePanel({ dataset, cells }: Props) {
  const [data, setData] = useState<M7PivotProfileResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = usePersistedState<boolean>(
    'm7:pivotProfile:collapsed', false);

  // Derive the stable serialized scope so React only re-runs when the actual
  // (band, hour, expiry, delta) set changes (not on every reference update).
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
      }));
  }, [cells]);
  const cellsKey = useMemo(() => JSON.stringify(
    scopedCells.map(c => [c.entry_atm_iv_band, c.entry_hour_ist,
                          c.expiry_bucket, c.delta_target]).sort()),
    [scopedCells]);

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

  // Render a compact header summary of which (band, hour, expiry, delta)
  // cells are driving the analysis. Truncates if > 6 cells.
  const cellSummary = scopedCells.length === 0
    ? null
    : scopedCells.slice(0, 8).map(c =>
        `${c.entry_atm_iv_band}@${String(c.entry_hour_ist).padStart(2, '0')}IST/${c.expiry_bucket}/Δ${c.delta_target}`,
      );

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
      {!collapsed && !err && data && data.status === 'ready' && data.result && (
        <>
          <div style={{ fontSize: 11, color: '#586e7e', marginBottom: 8 }}>
            Aggregated over{' '}
            {data.result.params.n_after_filter.toLocaleString()}{' '}
            trades matching the {scopedCells.length} selected cells.
          </div>
          <M7PivotProfileChart data={data} />
          <M7PivotProfileTable data={data} />
        </>
      )}
    </section>
  );
}
