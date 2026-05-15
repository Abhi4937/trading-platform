// Best Combo with Friday dedup overlay ("Full Coverage (Best Combo)").
//
// Backend: GET /api/v1/m7/iv_band_best_combo/coverage?coverage_mode=...
//   Reuses the same 96-rule picker as /iv_band_best_combo, then runs
//   `_classify_fridays_to_cells` so each of the 121 Fridays lands in
//   exactly one of the 10 picks (or counts as uncovered).
//
// Mode toggle persists to localStorage. localStorage key:
//   `m7:bestcombo:coverage_mode`  (default 'force_fit')
//
// Mounted below M7IvBandBestComboTable (which embeds the existing Missed
// Fridays sub-table). When future columns are added to the Best Combo
// response, they appear here automatically because rows are typed as
// M7BestComboCoverageRow (extends M7IvBandBestComboRow).

import React, { useEffect, useMemo, useState } from 'react';
import {
  fetchM7IvBandBestComboCoverage,
  type M7BestComboCoverageResponse,
  type M7BestComboCoverageRow,
  type M7CoverageMode,
} from '../../services/m7_api';

const LS_KEY = 'm7:bestcombo:coverage_mode';

// ── Tiny formatters (compatible with the existing Best Combo table) ──────────

const fmtUsd = (v: number | null | undefined): string => {
  if (v == null || !isFinite(v)) return '—';
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  return `${sign}$${abs.toFixed(2)}`;
};
const fmtPct = (v: number | null | undefined): string => {
  if (v == null || !isFinite(v)) return '—';
  const pct = v <= 1.0 ? v * 100 : v; // accept 0.91 or 91.0
  return `${pct.toFixed(1)}%`;
};
const fmtNum = (v: number | null | undefined): string => {
  if (v == null || !isFinite(v)) return '—';
  return String(Math.round(v));
};

const HOUR_LABEL = (h?: number | null) =>
  h == null ? '—' : `${String(h).padStart(2, '0')}:00`;

const BAND_ORDER: Record<string, number> = {
  '0-20': 0, '20-30': 1, '30-40': 2, '40-50': 3, '50-60': 4,
  '60-70': 5, '70-80': 6, '80-90': 7, '90-100': 8, '100+': 9,
};

interface Props {
  // Inherit picker params from the parent Best Combo table when a wrapper
  // wants both views aligned. Defaults match /iv_band_best_combo defaults.
  ranking?: string;
  rule_family?: 'all' | 'max_profit' | 'margin_target';
  pick_mode?: 'by_hour' | 'aggregate_hours';
  min_hit_pct?: number;
  min_n_trades?: number;
}

export function M7BestComboCoverageTable(props: Props = {}) {
  const [mode, setMode] = useState<M7CoverageMode>(() => {
    const stored = window.localStorage.getItem(LS_KEY);
    return stored === 'touched_band' ? 'touched_band' : 'force_fit';
  });
  const [resp, setResp] = useState<M7BestComboCoverageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    window.localStorage.setItem(LS_KEY, mode);
  }, [mode]);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setError(null);
    fetchM7IvBandBestComboCoverage(
      {
        ranking: (props.ranking ?? 'avg_net_pnl') as never,
        rule_family: props.rule_family ?? 'all',
        pick_mode: props.pick_mode ?? 'by_hour',
        min_hit_pct: props.min_hit_pct ?? 50,
        min_n_trades: props.min_n_trades ?? 5,
        coverage_mode: mode,
      },
      ac.signal,
    )
      .then(r => setResp(r))
      .catch(e => {
        if (e?.name === 'AbortError') return;
        setError(String(e?.message ?? e));
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [mode, props.ranking, props.rule_family, props.pick_mode, props.min_hit_pct, props.min_n_trades]);

  const sortedRows = useMemo(() => {
    if (!resp?.rows) return [] as M7BestComboCoverageRow[];
    return [...resp.rows].sort((a, b) =>
      (BAND_ORDER[a.iv_band] ?? 99) - (BAND_ORDER[b.iv_band] ?? 99));
  }, [resp]);

  const summary = resp?.coverage_summary;

  return (
    <section style={{ margin: '24px 0', color: '#cfd9e3' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>
          Full Coverage (Best Combo)
        </h3>
        <span style={{ fontSize: 12, opacity: 0.7 }}>
          Same 10 picks as Best Combo, but each of 121 Fridays attributed to
          exactly one band.
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, opacity: 0.7 }}>Mode:</span>
          <ModeButton active={mode === 'force_fit'} onClick={() => setMode('force_fit')} label="Force-fit" />
          <ModeButton active={mode === 'touched_band'} onClick={() => setMode('touched_band')} label="Touched-band" />
        </div>
      </header>

      {error && (
        <div style={{ padding: 12, background: '#3a1212', border: '1px solid #832929',
                       borderRadius: 6, fontSize: 13 }}>
          Failed to load: {error}
        </div>
      )}

      {!error && (
        <div style={{ overflowX: 'auto', border: '1px solid #2a3340', borderRadius: 8 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead style={{ background: '#1c252f' }}>
              <tr>
                <th style={th}>Band</th>
                <th style={th}>Hour</th>
                <th style={th}>Expiry</th>
                <th style={th}>Δ</th>
                <th style={th}>Rule</th>
                <th style={thNum}>n_trades</th>
                <th style={thNum}>Avg net P&L</th>
                <th style={thNum}>Win %</th>
                <th style={thNum}>Lots</th>
                <th style={{...thNum, borderLeft: '2px solid #3a4654'}}>n_assigned</th>
                <th style={thNum}>n_rule</th>
                <th style={thNum}>{mode === 'touched_band' ? 'n_touched' : 'n_force_fit'}</th>
                <th style={thNum}>n_closest_fb</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={13} style={{ padding: 16, textAlign: 'center', opacity: 0.6 }}>
                  Loading…
                </td></tr>
              )}
              {!loading && sortedRows.length === 0 && (
                <tr><td colSpan={13} style={{ padding: 16, textAlign: 'center', opacity: 0.6 }}>
                  No picks (grid not built yet, or all bands filtered out).
                </td></tr>
              )}
              {!loading && sortedRows.map((r, i) => (
                <tr key={`${r.iv_band}-${i}`} style={{ borderTop: '1px solid #283340' }}>
                  <td style={td}>{r.iv_band}</td>
                  <td style={td}>{HOUR_LABEL(r.entry_hour_ist)}</td>
                  <td style={td}>{r.expiry_bucket}</td>
                  <td style={td}>{r.delta_target?.toFixed(2) ?? '—'}</td>
                  <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>
                    {r.rule_label}
                  </td>
                  <td style={tdNum}>{r.n_trades}</td>
                  <td style={{ ...tdNum,
                                color: (r.avg_net_pnl ?? 0) >= 0 ? '#5bc97a' : '#e26060' }}>
                    {fmtUsd(r.avg_net_pnl)}
                  </td>
                  <td style={tdNum}>{fmtPct(r.win_rate)}</td>
                  <td style={tdNum}>{r.lots ?? 100}</td>
                  <td style={{ ...tdNum, borderLeft: '2px solid #3a4654',
                                fontWeight: 600 }}>
                    {r.n_assigned ?? 0}
                  </td>
                  <td style={tdNum}>{r.n_rule ?? 0}</td>
                  <td style={tdNum}>
                    {mode === 'touched_band' ? (r.n_touched_band ?? 0) : (r.n_force_fit ?? 0)}
                  </td>
                  <td style={tdNum}>{r.n_closest_fallback ?? 0}</td>
                </tr>
              ))}
            </tbody>
            {summary && (
              <tfoot>
                <tr style={{ background: '#1c252f', borderTop: '2px solid #3a4654' }}>
                  <td colSpan={9} style={{ ...td, fontWeight: 600 }}>
                    Coverage ({mode === 'force_fit' ? 'Force-fit' : 'Touched-band'} mode)
                  </td>
                  <td style={{ ...tdNum, fontWeight: 600 }}>{summary.n_assigned}</td>
                  <td style={tdNum}>{summary.n_rule}</td>
                  <td style={tdNum}>
                    {mode === 'touched_band' ? summary.n_touched_band : summary.n_force_fit}
                  </td>
                  <td style={tdNum}>{summary.n_closest_fallback}</td>
                </tr>
                <tr style={{ background: '#1c252f' }}>
                  <td colSpan={13} style={{ ...td, fontSize: 12, opacity: 0.85, padding: '6px 12px' }}>
                    Total Fridays: <strong>{summary.total_fridays}</strong>
                    {' • '}Assigned: <strong>{summary.n_assigned}</strong>
                    {' (rule '}<strong>{summary.n_rule}</strong>
                    {' + '}{mode === 'touched_band' ? 'touched-band' : 'force-fit'}{' '}
                    <strong>
                      {mode === 'touched_band' ? summary.n_touched_band : summary.n_force_fit}
                    </strong>
                    {mode === 'force_fit' && summary.n_closest_fallback > 0
                      ? ` + closest-fallback ${summary.n_closest_fallback}` : ''}
                    {')'}
                    {' • '}Uncovered: <strong style={{ color: summary.n_uncovered > 0 ? '#e2a45c' : '#5bc97a' }}>
                      {summary.n_uncovered}
                    </strong>
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </section>
  );
}

function ModeButton({ active, onClick, label }: {
  active: boolean; onClick: () => void; label: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '4px 10px',
        background: active ? '#2c5b8f' : '#2a3340',
        border: '1px solid ' + (active ? '#4a7fb4' : '#3a4654'),
        color: active ? '#fff' : '#cfd9e3',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 12,
        fontWeight: active ? 600 : 400,
      }}
    >
      {label}
    </button>
  );
}

const th: React.CSSProperties = {
  padding: '8px 10px', textAlign: 'left', fontWeight: 600,
  fontSize: 12, color: '#a8b3c0', borderBottom: '1px solid #3a4654',
  whiteSpace: 'nowrap',
};
const thNum: React.CSSProperties = { ...th, textAlign: 'right' };
const td: React.CSSProperties = { padding: '6px 10px', fontSize: 13, color: '#cfd9e3' };
const tdNum: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' };
