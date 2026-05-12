// Full-coverage IV-band summary — every Friday assigned to one of the 10
// best cells. Each band gets two stacked rows: "Rule" (strict 4-dim match,
// today's headline behavior) and "All Fridays" (rule + force-fit + closest-
// fallback). Backend endpoint: /api/v1/m7/iv_band_full_coverage.

import React, { useEffect, useState } from 'react';
import type { M7ExitRule, M7Filters } from '../../types/m7';
import { InfoIcon } from './InfoIcon';
import { ExcelButton, exportRowsAsXlsx } from './exportXlsx';
import { M7CellDrilldownModal } from './M7CellDrilldownModal';
import type { M7Cell } from '../../services/m7_api';

// ── Inline types (mirror backend response shape) ──────────────────────────────

type CoverageMode = 'force_fit' | 'touched_band';

interface FullCoverageRow {
  entry_atm_iv_band: string;
  entry_hour_ist: number;
  expiry_bucket: string;
  delta_target: number;
  score: number;
  n_trades: number;          // strict-rule subset
  n_all: number;             // rule ∪ (force_fit | touched_band) ∪ (closest_fallback in force-fit mode only)
  n_force_fit: number;
  n_touched_band: number;
  n_closest_fallback: number;
  // Rule-only metrics (flat, like /iv_band_summary)
  avg_net_pnl?: number | null;
  win_rate?: number | null;
  avg_loss_usd?: number | null;
  avg_win_usd?: number | null;
  avg_credit?: number | null;
  avg_margin?: number | null;
  avg_pct_return_on_margin?: number | null;
  avg_pct_return_on_credit?: number | null;
  avg_pct_return_on_margin_winners?: number | null;
  avg_pct_return_on_credit_winners?: number | null;
  avg_max_mtm_winners?: number | null;
  avg_min_mtm_winners?: number | null;
  max_mtm_winners?: number | null;
  min_mtm_winners?: number | null;
  avg_max_mtm_losers?: number | null;
  avg_min_mtm_losers?: number | null;
  max_mtm_losers?: number | null;
  min_mtm_losers?: number | null;
  max_loss_usd?: number | null;
  max_win_usd?: number | null;
  avg_exit_mtm?: number | null;
  avg_win_mtm?: number | null;
  avg_loss_mtm?: number | null;
  largest_win_mtm?: number | null;
  largest_loss_mtm?: number | null;
  total_win_mtm?: number | null;
  total_loss_mtm?: number | null;
  n_rule_trigger?: number | null;
  n_hard_cap?: number | null;
  n_losses?: number | null;
  n_wins?: number | null;
  max_consec_losses?: number | null;
  max_consec_wins?: number | null;
  max_consec_sl_hits?: number | null;
  n_winners_below_avg_min_mtm?: number | null;
  n_losers_above_avg_max_mtm?: number | null;
  // All-Fridays metrics — same shape, prefixed `all_`
  [allKey: `all_${string}`]: number | null | undefined;
}

interface FullCoverageResponse {
  rows: FullCoverageRow[];
  metric: string;
  coverage_mode: CoverageMode;
  n_total_fridays: number;
  n_rule_fridays: number;
  n_force_fit_fridays: number;
  n_touched_band_fridays: number;
  n_closest_fallback_fridays: number;
  n_uncovered_fridays: number;
}

// ── Inline fetcher (avoids touching m7_api.ts in this iteration) ──────────────

function buildQuery(filters: Record<string, unknown>,
                    exitRule?: M7ExitRule): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v === null || v === undefined || v === '') continue;
    params.append(k, String(v));
  }
  if (exitRule && Object.keys(exitRule).length > 0) {
    const cleaned: Record<string, number> = {};
    for (const [k, v] of Object.entries(exitRule)) {
      if (v === null || v === undefined) continue;
      cleaned[k] = v as number;
    }
    if (Object.keys(cleaned).length > 0) {
      params.append('exit_rule', JSON.stringify(cleaned));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

async function fetchFullCoverage(
  filters: M7Filters & { metric?: string; coverage_mode?: CoverageMode },
  exitRule?: M7ExitRule,
  signal?: AbortSignal,
): Promise<FullCoverageResponse> {
  const url = `/api/v1/m7/iv_band_full_coverage${buildQuery(filters as Record<string, unknown>, exitRule)}`;
  const r = await fetch(url, signal ? { signal } : undefined);
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  return (await r.json()) as FullCoverageResponse;
}

const COVERAGE_MODE_LS_KEY = 'm7:fullcoverage:coverage_mode';

function loadCoverageMode(): CoverageMode {
  try {
    const v = localStorage.getItem(COVERAGE_MODE_LS_KEY);
    return v === 'touched_band' ? 'touched_band' : 'force_fit';
  } catch {
    return 'force_fit';
  }
}

// ── Loading bar (mirror of M7IvBandSummaryTable) ──────────────────────────────

function LoadingBar({ visible }: { visible: boolean }) {
  return (
    <>
      <style>{`
        @keyframes m7slide_iv_full {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(400%); }
        }
      `}</style>
      <div style={{
        height: 2, width: '100%', background: '#0d1421', overflow: 'hidden',
        borderRadius: 2, marginBottom: 8,
        visibility: visible ? 'visible' : 'hidden',
      }}>
        <div style={{
          height: '100%', width: '25%', background: '#1f6feb',
          animation: 'm7slide_iv_full 1.1s ease-in-out infinite',
        }} />
      </div>
    </>
  );
}

// ── Formatting helpers ────────────────────────────────────────────────────────

const usd = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `$${(v as number).toFixed(dp)}`;
const usd0 = (v: number | null | undefined) => usd(v, 0);
const pct = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `${((v as number) * 100).toFixed(dp)}%`;
const pnlColor = (v: number | null | undefined) =>
  v == null ? '#7a9bb5' : v >= 0 ? '#3fb950' : '#f85149';

function fmtScore(metric: string, v: number): string {
  if (metric === 'win_rate') return `${(v * 100).toFixed(1)}%`;
  if (metric === 'avg_pct_return_on_margin' || metric === 'avg_pct_return_on_credit' ||
      metric === 'avg_pct_return_on_margin_winners' || metric === 'avg_pct_return_on_credit_winners') {
    return `${(v * 100).toFixed(2)}%`;
  }
  if (metric === 'count' ||
      metric === 'n_wins' || metric === 'n_losses' ||
      metric === 'n_rule_trigger' || metric === 'n_hard_cap' ||
      metric === 'max_consec_losses' || metric === 'max_consec_wins' ||
      metric === 'max_consec_sl_hits' ||
      metric === 'n_winners_below_avg_min_mtm' ||
      metric === 'n_losers_above_avg_max_mtm') return `${v}`;
  return `$${v.toFixed(2)}`;
}

// Per-metric value lookup — returns the rule_only value (flat key) or the
// all_fridays value (`all_<key>` prefix), depending on `block`.
function getMetricValue(
  row: FullCoverageRow,
  metric: keyof FullCoverageRow | string,
  block: 'rule' | 'all',
): number | null | undefined {
  const key = block === 'all' ? `all_${metric}` : (metric as string);
  return (row as unknown as Record<string, number | null | undefined>)[key];
}

// ── Main component ───────────────────────────────────────────────────────────

export function M7IvBandFullCoverageTable({
  filters, exitRule, metric = 'avg_net_pnl',
}: {
  filters: M7Filters; exitRule: M7ExitRule; metric?: string;
}) {
  const [resp, setResp] = useState<FullCoverageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [drilldown, setDrilldown] = useState<{ cell: M7Cell; setLabel: string } | null>(null);
  const [coverageMode, setCoverageMode] = useState<CoverageMode>(loadCoverageMode);

  useEffect(() => {
    try { localStorage.setItem(COVERAGE_MODE_LS_KEY, coverageMode); } catch { /* ignore */ }
  }, [coverageMode]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchFullCoverage({ ...filters, metric, coverage_mode: coverageMode }, exitRule, ac.signal)
      .then(r => { if (active) setResp(r); })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [JSON.stringify(filters), JSON.stringify(exitRule), metric, coverageMode]);

  const rows = resp?.rows ?? [];

  const th: React.CSSProperties = { padding: '6px 8px', color: '#7a9bb5', whiteSpace: 'nowrap' };
  const thR: React.CSSProperties = { ...th, textAlign: 'right' };
  const td: React.CSSProperties = { padding: '5px 8px', whiteSpace: 'nowrap' };
  const tdR: React.CSSProperties = { ...td, textAlign: 'right' };
  // Border-top only on the first sub-row of each band; the second sub-row
  // shares the band visually.
  const tdRule: React.CSSProperties = { ...td, borderTop: '1px solid #1a2d42' };
  const tdRuleR: React.CSSProperties = { ...tdR, borderTop: '1px solid #1a2d42' };
  const tdAll: React.CSSProperties = { ...td };
  const tdAllR: React.CSSProperties = { ...tdR };
  const allLabelStyle: React.CSSProperties = {
    color: '#7a9bb5', fontStyle: 'italic', fontSize: 11,
  };

  function renderMetricCell(
    row: FullCoverageRow,
    metricKey: string,
    block: 'rule' | 'all',
    fmt: 'usd' | 'usd0' | 'pct' | 'count',
    color?: string,
  ) {
    const v = getMetricValue(row, metricKey, block);
    let txt: string;
    if (fmt === 'usd') txt = usd(v);
    else if (fmt === 'usd0') txt = usd0(v);
    else if (fmt === 'pct') txt = pct(v);
    else txt = (v == null) ? '—' : `${v}`;
    const cellStyle = block === 'rule' ? tdRuleR : tdAllR;
    const finalColor = color ?? (fmt === 'usd' || fmt === 'usd0' ? pnlColor(v) : '#cfd9e3');
    return <td style={{ ...cellStyle, color: finalColor }}>{txt}</td>;
  }

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 12, marginBottom: 10,
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8, gap: 12, flexWrap: 'wrap',
      }}>
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          Full coverage — Best combo per IV band, every Friday assigned ({metric})
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Coverage:</span>
          <div style={{
            display: 'inline-flex', border: '1px solid #1a2d42',
            borderRadius: 4, overflow: 'hidden',
          }}>
            {(['force_fit', 'touched_band'] as const).map(mode => {
              const active = coverageMode === mode;
              const label = mode === 'force_fit' ? 'Force-fit' : 'Touched-band';
              const tip = mode === 'force_fit'
                ? 'Relax band entirely — any (hour, expiry, delta) match qualifies. Tiebreak by trade P&L.'
                : "Only allow bands the Friday's IV touched at SOME hour during the day. Tiebreak by cell historical avg. No closest-fallback.";
              return (
                <button key={mode}
                  onClick={() => setCoverageMode(mode)}
                  title={tip}
                  style={{
                    background: active ? '#1f6feb' : '#0d1421',
                    color: active ? '#fff' : '#cfd9e3',
                    border: 'none',
                    padding: '4px 10px',
                    fontSize: 11,
                    cursor: 'pointer',
                    fontWeight: active ? 600 : 400,
                  }}>{label}</button>
              );
            })}
          </div>
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>
            {loading ? 'Loading…'
              : err ? <span style={{ color: '#f85149' }}>{err}</span>
              : `${rows.length} bands`}
          </span>
          <ExcelButton
            disabled={rows.length === 0}
            onClick={() => exportRowsAsXlsx(`m7_iv_band_full_coverage_${coverageMode}_${metric}.xlsx`, 'FullCoverage', rows)} />
        </div>
      </div>
      <LoadingBar visible={loading} />
      {!err && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{
            borderCollapse: 'collapse', fontSize: 12,
            fontVariantNumeric: 'tabular-nums', color: '#cfd9e3',
            opacity: loading ? 0.4 : 1, transition: 'opacity 120ms',
          }}>
            <thead>
              <tr style={{ textAlign: 'left' }}>
                <th style={th}>IV band <InfoIcon text="ATM IV bucket at entry hour (0-20 = annualised IV in [0%, 20%); 100+ = ≥100%)." /></th>
                <th style={th}>Best entry hr <InfoIcon text="Hour-of-day IST the cell's trades opened (Friday). Picked per band by the chosen score." /></th>
                <th style={th}>Best expiry <InfoIcon text="Expiry bucket: current (Sat) / next (Sun) / next_to_next (Mon) / weekly / biweekly / monthly." /></th>
                <th style={th}>Best Δ <InfoIcon text="Per-leg target delta at entry. 0.50 = ATM, 0.10 = far-OTM." /></th>
                <th style={th}>Set <InfoIcon text="Which slice the row belongs to: 'Strict (rule-matched only)' = Fridays exactly matching (band × hour × expiry × delta) of the picked cell. 'Covered (force-fit / touched-band)' = Fridays placed via the coverage mode currently selected (force-fit relaxes the band; touched-band requires the Friday's IV to have visited the cell's band at some hour)." /></th>
                <th style={thR}>Score <InfoIcon text="Cell's value of the chosen metric (default = avg_net_pnl)." /></th>
                <th style={thR}>n <InfoIcon text="Number of Friday trades in this row." /></th>
                <th style={thR}>n wins <InfoIcon text="Trades with net P&L > 0." /></th>
                <th style={thR}>n loss <InfoIcon text="Trades with net P&L ≤ 0." /></th>
                <th style={thR}>SL hits <InfoIcon text="Trades that exited because ANY rule fired (premium_sl OR max_profit OR margin_target). Includes profit-take fires for take-profit families." /></th>
                <th style={thR}>Hard cap <InfoIcon text="Trades that ran to Saturday 17:30 IST without any rule firing." /></th>
                <th style={thR}>Max losing streak <InfoIcon text="Longest run of consecutive losing trades (Fridays in chronological order)." /></th>
                <th style={thR}>Max winning streak <InfoIcon text="Longest run of consecutive winning trades (Fridays in chronological order)." /></th>
                <th style={thR}>Max SL streak <InfoIcon text="Longest run of consecutive rule-trigger exits (any rule). Includes profit-take fires." /></th>
                <th style={thR}>Win % <InfoIcon text="n_wins / n_trades." /></th>
                <th style={thR}>Avg net <InfoIcon text="Mean net P&L per trade (entry slip + entry brokerage + exit slip + exit brokerage all subtracted)." /></th>
                <th style={thR}>Avg exit MTM <InfoIcon text="Mean exit-time MTM = mean(gross_pnl − entry slippage). The on-screen P&L at exit (exit costs not deducted)." /></th>
                <th style={thR}>Avg win (W) <InfoIcon text="Mean net P&L across winners only." /></th>
                <th style={thR}>Avg loss (L) <InfoIcon text="Mean net P&L across losers only." /></th>
                <th style={thR}>Largest win (W) <InfoIcon text="Max net P&L of any single winner." /></th>
                <th style={thR}>Largest loss (L) <InfoIcon text="Min net P&L of any single loser (most negative)." /></th>
                {/* Winners-only — exit MTM + path MTM */}
                <th style={thR}>Avg exit MTM (W) <InfoIcon text="Mean exit-time MTM across winners (entry slippage only)." /></th>
                <th style={thR}>Total exit MTM (W) <InfoIcon text="Sum of exit-time MTM across all winners." /></th>
                <th style={thR}>Largest exit MTM (W) <InfoIcon text="Max exit-time MTM among winners." /></th>
                <th style={thR}>Avg max MTM (W) <InfoIcon text="Mean peak MTM across winners (best unrealized point during hold)." /></th>
                <th style={thR}>Avg min MTM (W) <InfoIcon text="Mean trough MTM across winners — drawdown winners endured before recovering." /></th>
                <th style={thR}>Max MTM (W) <InfoIcon text="Highest peak MTM across all winners." /></th>
                <th style={thR}>Min MTM (W) <InfoIcon text="Worst trough MTM across all winners." /></th>
                <th style={thR}>W &lt; avg min MTM <InfoIcon text="Winners whose min MTM dipped below the cell's avg-min-MTM (winners) — i.e. winners with a worse-than-typical drawdown before recovering." /></th>
                {/* Losers-only — exit MTM + path MTM */}
                <th style={thR}>Avg exit MTM (L) <InfoIcon text="Mean exit-time MTM across losers." /></th>
                <th style={thR}>Total exit MTM (L) <InfoIcon text="Sum of exit-time MTM across all losers." /></th>
                <th style={thR}>Largest exit MTM (L) <InfoIcon text="Min exit-time MTM among losers." /></th>
                <th style={thR}>Avg max MTM (L) <InfoIcon text="Mean peak MTM across losers — how high they went before turning losing." /></th>
                <th style={thR}>Avg min MTM (L) <InfoIcon text="Mean trough MTM across losers." /></th>
                <th style={thR}>Max MTM (L) <InfoIcon text="Highest peak MTM across all losers." /></th>
                <th style={thR}>Min MTM (L) <InfoIcon text="Worst trough MTM across all losers." /></th>
                <th style={thR}>L &gt; avg max MTM <InfoIcon text="Losers whose max MTM rose above the cell's avg-max-MTM (losers) — i.e. losers that showed a better-than-typical peak before turning into losses (missed exit opportunity)." /></th>
                <th style={thR}>Avg credit <InfoIcon text="Mean upfront credit collected per trade." /></th>
                <th style={thR}>Avg margin <InfoIcon text="Mean Delta Exchange portfolio margin required at entry." /></th>
                <th style={thR}>Ret/margin <InfoIcon text="Mean per-trade net_pnl ÷ margin_at_entry." /></th>
                <th style={thR}>Ret/credit <InfoIcon text="Mean per-trade net_pnl ÷ credit_collected." /></th>
                <th style={thR}>Ret/margin (W) <InfoIcon text="Ret/margin restricted to winning trades." /></th>
                <th style={thR}>Ret/credit (W) <InfoIcon text="Ret/credit restricted to winning trades." /></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <React.Fragment key={i}>
                  {/* Sub-row 1 — Rule only */}
                  <tr>
                    <td style={{ ...tdRule, fontWeight: 600 }} rowSpan={2}>
                      {r.entry_atm_iv_band}
                    </td>
                    <td style={tdRule} rowSpan={2}>
                      {String(r.entry_hour_ist).padStart(2, '0')}:00
                    </td>
                    <td style={tdRule} rowSpan={2}>{r.expiry_bucket}</td>
                    <td style={tdRule} rowSpan={2}>
                      {Number(r.delta_target).toFixed(2)}
                    </td>
                    <td style={tdRule}>
                      <button
                        onClick={() => setDrilldown({
                          cell: {
                            entry_atm_iv_band: r.entry_atm_iv_band,
                            entry_hour_ist: r.entry_hour_ist,
                            expiry_bucket: r.expiry_bucket,
                            delta_target: Number(r.delta_target),
                          },
                          setLabel: 'Rule',
                        })}
                        title="Open per-cell drilldown (worst Fridays / W-vs-L / path replay)"
                        style={{
                          background: 'transparent', color: '#79c0ff',
                          border: '1px dashed #1f6feb', borderRadius: 3,
                          padding: '2px 6px', fontSize: 11, cursor: 'pointer',
                          fontWeight: 500,
                        }}>
                        Rule ▸
                      </button>
                    </td>
                    <td style={{ ...tdRuleR, color: pnlColor(r.score), fontWeight: 600 }}
                        rowSpan={2}>
                      {fmtScore(metric, r.score)}
                    </td>
                    <td style={{ ...tdRuleR, color: '#7a9bb5' }}>{r.n_trades}</td>
                    {renderMetricCell(r, 'n_wins', 'rule', 'count', '#3fb950')}
                    {renderMetricCell(r, 'n_losses', 'rule', 'count', '#f85149')}
                    {renderMetricCell(r, 'n_rule_trigger', 'rule', 'count')}
                    {renderMetricCell(r, 'n_hard_cap', 'rule', 'count', '#7a9bb5')}
                    {renderMetricCell(r, 'max_consec_losses', 'rule', 'count', '#f85149')}
                    {renderMetricCell(r, 'max_consec_wins', 'rule', 'count', '#3fb950')}
                    {renderMetricCell(r, 'max_consec_sl_hits', 'rule', 'count')}
                    {renderMetricCell(r, 'win_rate', 'rule', 'pct')}
                    {renderMetricCell(r, 'avg_net_pnl', 'rule', 'usd')}
                    {renderMetricCell(r, 'avg_exit_mtm', 'rule', 'usd')}
                    {renderMetricCell(r, 'avg_win_usd', 'rule', 'usd')}
                    {renderMetricCell(r, 'avg_loss_usd', 'rule', 'usd')}
                    {renderMetricCell(r, 'max_win_usd', 'rule', 'usd')}
                    {renderMetricCell(r, 'max_loss_usd', 'rule', 'usd')}
                    {/* Winners-only MTM */}
                    {renderMetricCell(r, 'avg_win_mtm', 'rule', 'usd')}
                    {renderMetricCell(r, 'total_win_mtm', 'rule', 'usd')}
                    {renderMetricCell(r, 'largest_win_mtm', 'rule', 'usd')}
                    {renderMetricCell(r, 'avg_max_mtm_winners', 'rule', 'usd')}
                    {renderMetricCell(r, 'avg_min_mtm_winners', 'rule', 'usd')}
                    {renderMetricCell(r, 'max_mtm_winners', 'rule', 'usd')}
                    {renderMetricCell(r, 'min_mtm_winners', 'rule', 'usd')}
                    {renderMetricCell(r, 'n_winners_below_avg_min_mtm', 'rule', 'count', '#3fb950')}
                    {/* Losers-only MTM */}
                    {renderMetricCell(r, 'avg_loss_mtm', 'rule', 'usd')}
                    {renderMetricCell(r, 'total_loss_mtm', 'rule', 'usd')}
                    {renderMetricCell(r, 'largest_loss_mtm', 'rule', 'usd')}
                    {renderMetricCell(r, 'avg_max_mtm_losers', 'rule', 'usd')}
                    {renderMetricCell(r, 'avg_min_mtm_losers', 'rule', 'usd')}
                    {renderMetricCell(r, 'max_mtm_losers', 'rule', 'usd')}
                    {renderMetricCell(r, 'min_mtm_losers', 'rule', 'usd')}
                    {renderMetricCell(r, 'n_losers_above_avg_max_mtm', 'rule', 'count', '#f85149')}
                    {renderMetricCell(r, 'avg_credit', 'rule', 'usd', '#cfd9e3')}
                    {renderMetricCell(r, 'avg_margin', 'rule', 'usd0', '#cfd9e3')}
                    {renderMetricCell(r, 'avg_pct_return_on_margin', 'rule', 'pct')}
                    {renderMetricCell(r, 'avg_pct_return_on_credit', 'rule', 'pct')}
                    {renderMetricCell(r, 'avg_pct_return_on_margin_winners', 'rule', 'pct', '#3fb950')}
                    {renderMetricCell(r, 'avg_pct_return_on_credit_winners', 'rule', 'pct', '#3fb950')}
                  </tr>
                  {/* Sub-row 2 — All Fridays */}
                  <tr>
                    <td style={{ ...tdAll, ...allLabelStyle }} title={
                      coverageMode === 'force_fit'
                        ? `Includes ${r.n_force_fit} force-fit + ${r.n_closest_fallback} closest-fallback Fridays`
                        : `Includes ${r.n_touched_band} touched-band Fridays`
                    }>
                      <button
                        onClick={() => setDrilldown({
                          cell: {
                            entry_atm_iv_band: r.entry_atm_iv_band,
                            entry_hour_ist: r.entry_hour_ist,
                            expiry_bucket: r.expiry_bucket,
                            delta_target: Number(r.delta_target),
                          },
                          setLabel: `All (${r.n_all})`,
                        })}
                        title="Open per-cell drilldown — all Fridays in this band"
                        style={{
                          background: 'transparent', color: '#bf6fde',
                          border: '1px dashed #bf6fde', borderRadius: 3,
                          padding: '2px 6px', fontSize: 11, cursor: 'pointer',
                          fontWeight: 500,
                        }}>
                        All ({r.n_all}) ▸
                      </button>
                    </td>
                    <td style={{ ...tdAllR, color: '#7a9bb5' }}>
                      {r.n_all}
                    </td>
                    {renderMetricCell(r, 'n_wins', 'all', 'count', '#3fb950')}
                    {renderMetricCell(r, 'n_losses', 'all', 'count', '#f85149')}
                    {renderMetricCell(r, 'n_rule_trigger', 'all', 'count')}
                    {renderMetricCell(r, 'n_hard_cap', 'all', 'count', '#7a9bb5')}
                    {renderMetricCell(r, 'max_consec_losses', 'all', 'count', '#f85149')}
                    {renderMetricCell(r, 'max_consec_wins', 'all', 'count', '#3fb950')}
                    {renderMetricCell(r, 'max_consec_sl_hits', 'all', 'count')}
                    {renderMetricCell(r, 'win_rate', 'all', 'pct')}
                    {renderMetricCell(r, 'avg_net_pnl', 'all', 'usd')}
                    {renderMetricCell(r, 'avg_exit_mtm', 'all', 'usd')}
                    {renderMetricCell(r, 'avg_win_usd', 'all', 'usd')}
                    {renderMetricCell(r, 'avg_loss_usd', 'all', 'usd')}
                    {renderMetricCell(r, 'max_win_usd', 'all', 'usd')}
                    {renderMetricCell(r, 'max_loss_usd', 'all', 'usd')}
                    {/* Winners-only MTM */}
                    {renderMetricCell(r, 'avg_win_mtm', 'all', 'usd')}
                    {renderMetricCell(r, 'total_win_mtm', 'all', 'usd')}
                    {renderMetricCell(r, 'largest_win_mtm', 'all', 'usd')}
                    {renderMetricCell(r, 'avg_max_mtm_winners', 'all', 'usd')}
                    {renderMetricCell(r, 'avg_min_mtm_winners', 'all', 'usd')}
                    {renderMetricCell(r, 'max_mtm_winners', 'all', 'usd')}
                    {renderMetricCell(r, 'min_mtm_winners', 'all', 'usd')}
                    {renderMetricCell(r, 'n_winners_below_avg_min_mtm', 'all', 'count', '#3fb950')}
                    {/* Losers-only MTM */}
                    {renderMetricCell(r, 'avg_loss_mtm', 'all', 'usd')}
                    {renderMetricCell(r, 'total_loss_mtm', 'all', 'usd')}
                    {renderMetricCell(r, 'largest_loss_mtm', 'all', 'usd')}
                    {renderMetricCell(r, 'avg_max_mtm_losers', 'all', 'usd')}
                    {renderMetricCell(r, 'avg_min_mtm_losers', 'all', 'usd')}
                    {renderMetricCell(r, 'max_mtm_losers', 'all', 'usd')}
                    {renderMetricCell(r, 'min_mtm_losers', 'all', 'usd')}
                    {renderMetricCell(r, 'n_losers_above_avg_max_mtm', 'all', 'count', '#f85149')}
                    {renderMetricCell(r, 'avg_credit', 'all', 'usd', '#cfd9e3')}
                    {renderMetricCell(r, 'avg_margin', 'all', 'usd0', '#cfd9e3')}
                    {renderMetricCell(r, 'avg_pct_return_on_margin', 'all', 'pct')}
                    {renderMetricCell(r, 'avg_pct_return_on_credit', 'all', 'pct')}
                    {renderMetricCell(r, 'avg_pct_return_on_margin_winners', 'all', 'pct', '#3fb950')}
                    {renderMetricCell(r, 'avg_pct_return_on_credit_winners', 'all', 'pct', '#3fb950')}
                  </tr>
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {/* Footer — universe coverage stats */}
      {resp && !loading && !err && (
        <div style={{
          marginTop: 8, fontSize: 11, color: '#7a9bb5',
          borderTop: '1px solid #1a2d42', paddingTop: 6,
        }}>
          <strong>{resp.n_total_fridays}</strong> Fridays total —{' '}
          <span style={{ color: '#3fb950' }}>{resp.n_rule_fridays} rule</span>
          {coverageMode === 'force_fit' ? (
            <>
              {' · '}
              <span style={{ color: '#d29922' }}>{resp.n_force_fit_fridays} force-fit</span>
              {' · '}
              <span style={{ color: '#a371f7' }}>{resp.n_closest_fallback_fridays} closest-fallback</span>
            </>
          ) : (
            <>
              {' · '}
              <span style={{ color: '#d29922' }}>{resp.n_touched_band_fridays} touched-band</span>
            </>
          )}
          {resp.n_uncovered_fridays > 0 && (
            <>
              {' · '}
              <span style={{ color: '#f85149' }}>{resp.n_uncovered_fridays} uncovered</span>
            </>
          )}
        </div>
      )}
      {drilldown && (
        <M7CellDrilldownModal
          cell={drilldown.cell}
          exitRule={exitRule}
          setLabel={drilldown.setLabel}
          onClose={() => setDrilldown(null)}
        />
      )}
    </div>
  );
}
