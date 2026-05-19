// M7 Joint Δ+Price Match — KPI strip + per-dimension breakdowns.
//
// Mounted on M7SweepDashboard between the dataset toggle and the existing
// best-combo panels. Renders null when the dataset toggle is on Δ-match
// (today's behavior). When toggled to Δ+Price match, fetches
// /api/v1/m7/joint_match_stats and surfaces:
//   - KPI row: total trades, joint-match %, fallback %, joint vs fallback
//     win rate, median + P95 price-diff %.
//   - Three collapsible breakdowns: per-Δ, per-IV-band, per-Δ×band.
//
// Handles the {status: "no_data"} response (price-matched parquet not yet
// built) with a clear message instead of an error.

import React, { useEffect, useState } from 'react';
import {
  fetchM7JointMatchStats,
  type M7Dataset,
  type M7JointMatchStatsResponse,
} from '../../services/m7_api';

interface Props {
  dataset: M7Dataset;
}

// ── Tiny formatters (match the rest of the M7 dashboard's style) ─────────────

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !isFinite(v)) return '—';
  const pct = v <= 1.0 ? v * 100 : v;  // accept 0.91 or 91.0
  return `${pct.toFixed(digits)}%`;
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  const sign = v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function fmtInt(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return Math.round(v).toLocaleString();
}

// ── Styles (mirrors M7HeadlineStrip + M7BestComboCoverageTable) ──────────────

const CARD: React.CSSProperties = {
  flex: 1, padding: '10px 14px', background: '#0d1421',
  border: '1px solid #1a2d42', borderRadius: 6, minWidth: 120,
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#7a9bb5', textTransform: 'uppercase', letterSpacing: 0.5,
  fontWeight: 600,
};
const VALUE: React.CSSProperties = {
  fontSize: 18, color: '#cfd9e3', marginTop: 4, fontWeight: 600,
  fontVariantNumeric: 'tabular-nums',
};
const SUBVALUE: React.CSSProperties = {
  fontSize: 11, color: '#7a9bb5', marginTop: 2,
};
const PANEL: React.CSSProperties = {
  background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
  padding: 12, marginBottom: 10,
};
const HEADER_ROW: React.CSSProperties = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  cursor: 'pointer',
};
const HEADER_LABEL: React.CSSProperties = {
  fontSize: 14, color: '#cfd9e3', fontWeight: 700,
};
const TABLE: React.CSSProperties = {
  width: '100%', borderCollapse: 'collapse', fontSize: 12,
  fontVariantNumeric: 'tabular-nums', color: '#cfd9e3',
  marginTop: 8,
};
const TH: React.CSSProperties = {
  padding: '6px 8px', color: '#7a9bb5', whiteSpace: 'nowrap',
  textAlign: 'left', borderBottom: '1px solid #1a2d42',
};
const THR: React.CSSProperties = { ...TH, textAlign: 'right' };
const TD: React.CSSProperties = { padding: '5px 8px', whiteSpace: 'nowrap' };
const TDR: React.CSSProperties = { ...TD, textAlign: 'right' };

// ── Collapsible section ──────────────────────────────────────────────────────

function Section({
  title, defaultOpen = false, children,
}: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={PANEL}>
      <div style={HEADER_ROW} onClick={() => setOpen(o => !o)}>
        <div style={HEADER_LABEL}>{open ? '▾' : '▸'} {title}</div>
      </div>
      {open && <div>{children}</div>}
    </div>
  );
}

// ── Win-rate cell (color green/red around 50%) ───────────────────────────────

function WinRateCell({ v }: { v: number | null }) {
  if (v == null) return <span style={{ color: '#586e7e' }}>—</span>;
  const pct = v <= 1.0 ? v * 100 : v;
  const color = pct >= 50 ? '#3fb950' : '#f85149';
  return <span style={{ color }}>{pct.toFixed(1)}%</span>;
}

function PnlCell({ v }: { v: number | null }) {
  if (v == null) return <span style={{ color: '#586e7e' }}>—</span>;
  const color = v >= 0 ? '#3fb950' : '#f85149';
  return <span style={{ color }}>{fmtUsd(v)}</span>;
}

// ── Per-dimension table ──────────────────────────────────────────────────────

interface BreakdownRow {
  key: string;          // composite label (e.g. delta=0.30 / band=30-40)
  joint: number;
  fallback: number;
  joint_winrate: number | null;
  fallback_winrate: number | null;
  joint_avg_pnl: number | null;
  fallback_avg_pnl: number | null;
}

function BreakdownTable({
  keyLabel, rows,
}: { keyLabel: string; rows: BreakdownRow[] }) {
  if (!rows.length) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 12, padding: '10px 0' }}>
        No breakdown rows.
      </div>
    );
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={TABLE}>
        <thead>
          <tr>
            <th style={TH}>{keyLabel}</th>
            <th style={THR}>Joint n</th>
            <th style={THR}>Fallback n</th>
            <th style={THR}>Joint WR</th>
            <th style={THR}>Fallback WR</th>
            <th style={THR}>Joint avg P&amp;L</th>
            <th style={THR}>Fallback avg P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.key} style={{ borderTop: '1px solid #1a2d42' }}>
              <td style={{ ...TD, fontWeight: 600 }}>{r.key}</td>
              <td style={TDR}>{fmtInt(r.joint)}</td>
              <td style={TDR}>{fmtInt(r.fallback)}</td>
              <td style={TDR}><WinRateCell v={r.joint_winrate} /></td>
              <td style={TDR}><WinRateCell v={r.fallback_winrate} /></td>
              <td style={TDR}><PnlCell v={r.joint_avg_pnl} /></td>
              <td style={TDR}><PnlCell v={r.fallback_avg_pnl} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export function M7JointMatchStats({ dataset }: Props) {
  const [resp, setResp] = useState<M7JointMatchStatsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Only fetch when on a non-default dataset. Render null otherwise.
  const enabled = dataset !== 'delta_match';

  useEffect(() => {
    if (!enabled) {
      setResp(null);
      setErr(null);
      setLoading(false);
      return;
    }
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7JointMatchStats(dataset, ac.signal)
      .then(r => setResp(r))
      .catch(e => {
        if ((e as Error).name === 'AbortError') return;
        setErr(String((e as Error).message ?? e));
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [dataset, enabled]);

  if (!enabled) return null;

  if (loading && !resp) {
    return (
      <div style={{ ...PANEL, color: '#7a9bb5', fontSize: 12 }}>
        Loading joint-match stats…
      </div>
    );
  }

  if (err) {
    return (
      <div style={{ ...PANEL, color: '#f85149', fontSize: 12 }}>
        Failed to load joint-match stats: {err}
      </div>
    );
  }

  if (!resp) return null;

  if (resp.status === 'no_data') {
    return (
      <div style={{ ...PANEL, color: '#d29922', fontSize: 12 }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>
          No price-matched data yet.
        </div>
        <div style={{ color: '#7a9bb5' }}>
          {resp.message ?? (
            <>
              Run{' '}
              <code style={{ color: '#cfd9e3' }}>
                python -m app.analytics.m7_batch_backtester_joint
              </code>{' '}
              to generate the price-matched parquet, then refresh.
            </>
          )}
        </div>
      </div>
    );
  }

  const total = resp.total_trades ?? 0;
  const joint = resp.joint_match_count ?? 0;
  const fb = resp.delta_fallback_count ?? 0;
  const jointPct = resp.joint_match_pct ?? (total > 0 ? joint / total : null);
  const fbPct = resp.fallback_pct ?? (total > 0 ? fb / total : null);
  const jointWR = resp.joint_outcomes?.win_rate ?? null;
  const fbWR = resp.fallback_outcomes?.win_rate ?? null;
  const diff = resp.price_diff_distribution;

  const perDelta: BreakdownRow[] = (resp.per_delta_target ?? []).map(r => ({
    key: `Δ ${r.delta_target.toFixed(2)}`,
    joint: r.joint, fallback: r.fallback,
    joint_winrate: r.joint_winrate, fallback_winrate: r.fallback_winrate,
    joint_avg_pnl: r.joint_avg_pnl, fallback_avg_pnl: r.fallback_avg_pnl,
  }));
  const perBand: BreakdownRow[] = (resp.per_iv_band ?? []).map(r => ({
    key: r.iv_band,
    joint: r.joint, fallback: r.fallback,
    joint_winrate: r.joint_winrate, fallback_winrate: r.fallback_winrate,
    joint_avg_pnl: r.joint_avg_pnl, fallback_avg_pnl: r.fallback_avg_pnl,
  }));
  const perDeltaBand: BreakdownRow[] = (resp.per_delta_x_band ?? []).map(r => ({
    key: `Δ ${r.delta_target.toFixed(2)} / ${r.iv_band}`,
    joint: r.joint, fallback: r.fallback,
    joint_winrate: r.joint_winrate, fallback_winrate: r.fallback_winrate,
    joint_avg_pnl: r.joint_avg_pnl, fallback_avg_pnl: r.fallback_avg_pnl,
  }));

  return (
    <div>
      {/* KPI row */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <div style={CARD}>
          <div style={LABEL}>Total trades</div>
          <div style={VALUE}>{fmtInt(total)}</div>
        </div>
        <div style={CARD}>
          <div style={LABEL}>Joint match</div>
          <div style={VALUE}>{fmtInt(joint)}</div>
          <div style={SUBVALUE}>{fmtPct(jointPct)}</div>
        </div>
        <div style={CARD}>
          <div style={LABEL}>Δ fallback</div>
          <div style={VALUE}>{fmtInt(fb)}</div>
          <div style={SUBVALUE}>{fmtPct(fbPct)}</div>
        </div>
        <div style={CARD}>
          <div style={LABEL}>Joint win rate</div>
          <div style={{ ...VALUE, color: jointWR != null && (jointWR <= 1 ? jointWR : jointWR / 100) >= 0.5 ? '#3fb950' : '#f85149' }}>
            {fmtPct(jointWR)}
          </div>
        </div>
        <div style={CARD}>
          <div style={LABEL}>Fallback win rate</div>
          <div style={{ ...VALUE, color: fbWR != null && (fbWR <= 1 ? fbWR : fbWR / 100) >= 0.5 ? '#3fb950' : '#f85149' }}>
            {fmtPct(fbWR)}
          </div>
        </div>
        <div style={CARD}>
          <div style={LABEL}>Price-diff median</div>
          <div style={VALUE}>{fmtPct(diff?.median)}</div>
          <div style={SUBVALUE}>P95 {fmtPct(diff?.p95)}</div>
        </div>
      </div>

      {/* Breakdowns */}
      <Section title="Per-Δ breakdown">
        <BreakdownTable keyLabel="Δ target" rows={perDelta} />
      </Section>
      <Section title="Per-IV-band breakdown">
        <BreakdownTable keyLabel="IV band" rows={perBand} />
      </Section>
      <Section title="Per-Δ × band breakdown">
        <BreakdownTable keyLabel="Δ / band" rows={perDeltaBand} />
      </Section>
    </div>
  );
}
