// Best combo per IV band — for each band, the (expiry, delta, exit-rule)
// that scores best on the chosen primary metric. Optional Tiebreak mode lets
// the user filter to within tolerance of the per-band primary best, then pick
// by a secondary metric (e.g., lowest min MTM among cells with similar net P&L).
//
// Sweep: 105 rule variants — premium_sl ∈ {50, 75, 100} × {baseline, 10
// max_profit, 10 margin_target, 14 fixed_hour (05:00..17:00 + 17:29)} = 35 per SL × 3 = 105.
//
// Backend: GET /api/v1/m7/iv_band_best_combo
//   ?ranking=<primary>           default avg_net_pnl
//   &secondary=<metric>          optional, enables tiebreak
//   &tolerance_pct=<n>           default 5, only used when secondary set
//
// First request after backend restart triggers a ~45 min background warmup;
// while warming the API returns 202-style {status:"warming", rules_done…}.

import React, { useEffect, useMemo, useState } from 'react';
import { M7RuleComparisonModal } from './M7RuleComparisonModal';
import { M7CellAnalysisModal } from './M7CellAnalysisModal';
import { M7BestComboMissedFridaysTable } from './M7BestComboMissedFridaysTable';
import { InfoIcon } from './InfoIcon';
import { ExcelButton, exportRowsAsXlsx } from './exportXlsx';
import {
  fetchM7IvBandBestCombo,
  type FetchBestComboArgs,
  type M7IvBandBestComboResponse,
  type M7IvBandBestComboRow,
  type M7Ranking,
  type M7RuleFamily,
  type Stage1PerBandRule,
} from '../../services/m7_api';

// ── Metric catalog ──────────────────────────────────────────────────────────

type MetricFmt = 'usd' | 'usd0' | 'pct' | 'count' | 'num';

interface MetricDef {
  key: string;
  label: string;
  fmt: MetricFmt;
  // Whether higher is "good" — used only for the score-cell color (green/red).
  goodIsHigh: boolean;
}

const PRIMARY_GROUPS: { label: string; metrics: MetricDef[] }[] = [
  {
    label: 'Composite',
    metrics: [
      { key: 'composite_score_v2', label: 'Composite v2 (5-comp, filtered)', fmt: 'num', goodIsHigh: true },
      { key: 'composite_score', label: 'Composite score (v1)', fmt: 'num',  goodIsHigh: true },
      { key: 'sharpe_per_trade', label: 'Sharpe (per trade)', fmt: 'num', goodIsHigh: true },
      { key: 'sortino_per_trade', label: 'Sortino (per trade)', fmt: 'num', goodIsHigh: true },
      { key: 'calmar_like',     label: 'Calmar (avg_net/|max_loss|)', fmt: 'num', goodIsHigh: true },
    ],
  },
  {
    label: 'P&L (net of all costs)',
    metrics: [
      { key: 'avg_net_pnl',     label: 'Avg net P&L',     fmt: 'usd',  goodIsHigh: true },
      { key: 'sum_net_pnl',     label: 'Total net P&L',   fmt: 'usd',  goodIsHigh: true },
      { key: 'avg_win_usd',     label: 'Avg win',         fmt: 'usd',  goodIsHigh: true },
      { key: 'avg_loss_usd',    label: 'Avg loss',        fmt: 'usd',  goodIsHigh: true },
      { key: 'max_win_usd',     label: 'Largest win',     fmt: 'usd',  goodIsHigh: true },
      { key: 'max_loss_usd',    label: 'Largest loss',    fmt: 'usd',  goodIsHigh: true },
      { key: 'total_win_mtm',   label: 'Total win MTM',   fmt: 'usd',  goodIsHigh: true },
      { key: 'total_loss_mtm',  label: 'Total loss MTM',  fmt: 'usd',  goodIsHigh: true },
    ],
  },
  {
    label: '% return',
    metrics: [
      { key: 'avg_pct_return_on_credit',         label: '% Return / Credit',           fmt: 'pct', goodIsHigh: true },
      { key: 'avg_pct_return_on_margin',         label: '% Return / Margin',           fmt: 'pct', goodIsHigh: true },
      { key: 'avg_pct_return_on_credit_winners', label: '% Return / Credit (winners)', fmt: 'pct', goodIsHigh: true },
      { key: 'avg_pct_return_on_margin_winners', label: '% Return / Margin (winners)', fmt: 'pct', goodIsHigh: true },
      { key: 'avg_pct_max_mtm_on_credit',        label: 'Peak % / Credit',             fmt: 'pct', goodIsHigh: true },
      { key: 'avg_pct_min_mtm_on_credit',        label: 'Trough % / Credit',           fmt: 'pct', goodIsHigh: true },
    ],
  },
  {
    label: 'Risk',
    metrics: [
      { key: 'avg_min_mtm_losers',  label: 'Avg min MTM (losers)',  fmt: 'usd',   goodIsHigh: true },
      { key: 'avg_min_mtm_winners', label: 'Avg min MTM (winners)', fmt: 'usd',   goodIsHigh: true },
      { key: 'max_consec_losses',   label: 'Max losing streak',     fmt: 'count', goodIsHigh: false },
      { key: 'max_consec_sl_hits',  label: 'Max SL streak',         fmt: 'count', goodIsHigh: false },
    ],
  },
  {
    label: 'Win counts',
    metrics: [
      { key: 'win_rate',  label: 'Win rate',  fmt: 'pct',   goodIsHigh: true },
      { key: 'n_wins',    label: '# wins',    fmt: 'count', goodIsHigh: true },
      { key: 'n_losses',  label: '# losses',  fmt: 'count', goodIsHigh: false },
      { key: 'n_trades',  label: '# trades',  fmt: 'count', goodIsHigh: true },
    ],
  },
];

// Loss-side parameters available as tiebreakers, grouped by what aspect of
// "losing" they measure. Same MetricDef shape as PRIMARY_GROUPS.
const SECONDARY_GROUPS: { label: string; metrics: MetricDef[] }[] = [
  {
    label: 'Loss magnitude (USD)',
    metrics: [
      { key: 'avg_loss_usd',     label: 'Avg loss',         fmt: 'usd', goodIsHigh: true },
      { key: 'max_loss_usd',     label: 'Largest loss',     fmt: 'usd', goodIsHigh: true },
      { key: 'total_loss_mtm',   label: 'Total loss MTM',   fmt: 'usd', goodIsHigh: true },
      { key: 'avg_loss_mtm',     label: 'Avg loss MTM',     fmt: 'usd', goodIsHigh: true },
      { key: 'largest_loss_mtm', label: 'Largest loss MTM', fmt: 'usd', goodIsHigh: true },
    ],
  },
  {
    label: 'Drawdown depth (losers)',
    metrics: [
      { key: 'avg_min_mtm_losers',        label: 'Avg min MTM (losers)', fmt: 'usd', goodIsHigh: true },
      { key: 'min_mtm_losers',            label: 'Min MTM (losers)',     fmt: 'usd', goodIsHigh: true },
      { key: 'avg_max_mtm_losers',        label: 'Avg max MTM (losers)', fmt: 'usd', goodIsHigh: true },
      { key: 'avg_pct_min_mtm_on_credit', label: 'Trough %',             fmt: 'pct', goodIsHigh: true },
    ],
  },
  {
    label: 'Drawdown depth (winners — pain endured before recovering)',
    metrics: [
      { key: 'avg_min_mtm_winners',       label: 'Avg min MTM (winners)', fmt: 'usd', goodIsHigh: true },
      { key: 'min_mtm_winners',           label: 'Min MTM (winners)',     fmt: 'usd', goodIsHigh: true },
    ],
  },
  {
    label: 'Drawdown depth (overall — across all trades)',
    metrics: [
      { key: 'avg_min_mtm',                label: 'Avg min MTM (overall)', fmt: 'usd', goodIsHigh: true },
      { key: 'min_mtm',                    label: 'Min MTM (overall)',     fmt: 'usd', goodIsHigh: true },
    ],
  },
  {
    label: 'Frequency',
    metrics: [
      { key: 'n_losses',          label: '# losses',    fmt: 'count', goodIsHigh: false },
      { key: 'n_premium_sl_hit',  label: '# SL hits',   fmt: 'count', goodIsHigh: false },
      { key: 'n_rule_trigger',    label: '# Rule hits', fmt: 'count', goodIsHigh: false },
      { key: 'n_hard_cap',        label: '# Hard cap',  fmt: 'count', goodIsHigh: false },
    ],
  },
  {
    label: 'Streaks',
    metrics: [
      { key: 'max_consec_losses',          label: 'Max losing streak', fmt: 'count', goodIsHigh: false },
      { key: 'max_consec_sl_hits',         label: 'Max rule streak',   fmt: 'count', goodIsHigh: false },
      { key: 'max_consec_premium_sl_hits', label: 'Max SL streak',     fmt: 'count', goodIsHigh: false },
    ],
  },
  {
    label: 'Behavioral',
    metrics: [
      { key: 'n_losers_above_avg_max_mtm',    label: 'L > avg max MTM',      fmt: 'count', goodIsHigh: false },
      { key: 'avg_loser_exit_offset_minutes', label: 'Avg loser exit (min)', fmt: 'count', goodIsHigh: false },
    ],
  },
  {
    label: 'Pro metrics (loss tail — scales linearly with lots)',
    metrics: [
      { key: 'var_95_net',          label: 'VaR 95 (5th %ile net)',  fmt: 'usd', goodIsHigh: true },
      { key: 'cvar_95_net',         label: 'CVaR 95 (mean of tail)', fmt: 'usd', goodIsHigh: true },
      { key: 'worst_5_avg_net',     label: 'Worst-5 avg net',        fmt: 'usd', goodIsHigh: true },
      { key: 'avg_min_mtm',         label: 'Trough (avg min MTM)',   fmt: 'usd', goodIsHigh: true },
    ],
  },
];

const SECONDARY_OPTIONS: MetricDef[] =
  SECONDARY_GROUPS.flatMap(g => g.metrics);

const ALL_METRICS: Record<string, MetricDef> = (() => {
  const m: Record<string, MetricDef> = {};
  for (const g of PRIMARY_GROUPS) for (const md of g.metrics) m[md.key] = md;
  for (const md of SECONDARY_OPTIONS) m[md.key] = md;
  return m;
})();

// ── Formatting helpers ─────────────────────────────────────────────────────

const usd = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `$${(v as number).toFixed(dp)}`;
const usd0 = (v: number | null | undefined) => usd(v, 0);
const pct = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `${((v as number) * 100).toFixed(dp)}%`;
const cnt = (v: number | null | undefined) =>
  v == null || isNaN(v as number) ? '—' : String(Math.round(v as number));

const num = (v: number | null | undefined, dp = 3) =>
  v == null || isNaN(v as number) ? '—' : (v as number).toFixed(dp);

function fmtByType(v: number | null | undefined, t: MetricFmt): string {
  switch (t) {
    case 'usd':   return usd(v);
    case 'usd0':  return usd0(v);
    case 'pct':   return pct(v);
    case 'count': return cnt(v);
    case 'num':   return num(v);
  }
}

const pnlColor = (v: number | null | undefined) =>
  v == null ? '#7a9bb5' : v >= 0 ? '#3fb950' : '#f85149';

function fmtMinutes(min: number | null | undefined): string {
  if (min == null || isNaN(min as number)) return '—';
  const m = Math.round(min as number);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m - h * 60;
  return rem === 0 ? `${h}h` : `${h}h ${rem}m`;
}

// Format avg exit time as IST clock + hold duration in brackets.
// Trade entered at `entry_hour_ist:00` IST on Friday; after `durationMin`
// minutes of hold, IST clock at exit = (entry_hour_ist*60 + durationMin) mod 1440.
function fmtExitClock(
  entryHourIst: number | null | undefined,
  durationMin: number | null | undefined,
): string {
  if (entryHourIst == null || durationMin == null || isNaN(durationMin as number)) return '—';
  const totalMin = (entryHourIst as number) * 60 + (durationMin as number);
  // Normalize into 0–1440 (clock minutes within a day), wrapping at day boundary.
  const mod = ((Math.round(totalMin) % 1440) + 1440) % 1440;
  const hh = Math.floor(mod / 60);
  const mm = mod - hh * 60;
  const clock = `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
  return `${clock} (${fmtMinutes(durationMin)})`;
}

// Rule labels follow the format `sl{X}_{family}_{Y}`.
//   sl100_baseline           → "SL100 only"
//   sl75_max_profit_40       → "SL75 + MaxProfit 40%"
//   sl50_margin_target_25    → "SL50 + MarginTgt 25%"
//   sl100_exit_hr_10         → "SL100 + Exit @10:00"
//   sl100_exit_hr_1729       → "SL100 + Exit @17:29"
function fmtRuleLabel(label: string): string {
  // Strip leading sl{X}_
  const m = label.match(/^sl(\d+)_(.+)$/);
  if (!m) return label;
  const sl = m[1];
  const rest = m[2];
  if (rest === 'baseline') return `SL${sl} only`;
  if (rest.startsWith('max_profit_')) return `SL${sl} + MaxProfit ${rest.slice(11)}%`;
  if (rest.startsWith('margin_target_')) return `SL${sl} + MarginTgt ${rest.slice(14)}%`;
  if (rest.startsWith('exit_hr_')) {
    const hsfx = rest.slice(8);
    let display = hsfx;
    if (hsfx.length >= 3) {
      // e.g. "1729" → "17:29"
      const h = hsfx.slice(0, hsfx.length - 2);
      const mm = hsfx.slice(-2);
      display = `${h}:${mm}`;
    } else {
      display = `${hsfx}:00`;
    }
    return `SL${sl} + Exit @${display}`;
  }
  return label;
}

// ── Premium-SL sweep dimension ──────────────────────────────────────────────
// Mirrors `_PREMIUM_SL_PCTS` in backend/app/api/m7_best_combo.py — the union of
// SL pct values across datasets. delta_match uses all five; price_match uses
// the first three. Chip toggles in/out of `slFilter` below; empty selection =
// no filter (server sweeps every SL).
const SL_OPTIONS = [50, 75, 100, 150, 200] as const;
// Per-SL variant count = 1 baseline + 7 margin_target + 14 exit_hr = 22.
// Family filter narrows what's counted: max_profit is 0 because the family
// was removed from the 110-rule grid (kept in the UI for backward compat).
const FAMILY_VARIANTS_PER_SL: Record<M7RuleFamily, number> = {
  all: 22, max_profit: 0, margin_target: 7,
};

// ── localStorage persistence ────────────────────────────────────────────────

const LS_PREFIX = 'm7:bestcombo:';
function loadLS<T>(key: string, fallback: T): T {
  try {
    const v = window.localStorage.getItem(LS_PREFIX + key);
    if (v == null) return fallback;
    return JSON.parse(v) as T;
  } catch {
    return fallback;
  }
}
function saveLS(key: string, val: unknown) {
  try { window.localStorage.setItem(LS_PREFIX + key, JSON.stringify(val)); }
  catch { /* ignore */ }
}

// ── Loading bar ──────────────────────────────────────────────────────────────

function LoadingBar({ visible }: { visible: boolean }) {
  return (
    <>
      <style>{`
        @keyframes m7slide_iv_bc {
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
          animation: 'm7slide_iv_bc 1.1s ease-in-out infinite',
        }} />
      </div>
    </>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

interface Props {
  // Called whenever the per-band rows change. Lets a parent (M7SweepDashboard)
  // lift the current per-band best-combo selection up so it can pass it to
  // the Losses Explorer's `cells` param.
  onSelectionsChange?: (rows: M7IvBandBestComboRow[]) => void;
  // Called with the resolved per-band rules dict (band → Stage1PerBandRule)
  // after each fetch settles. Used by M7Stage1Panel to POST the exact rules.
  onResolvedRulesChange?: (rules: Record<string, Stage1PerBandRule>, dataset: string) => void;
  dataset?: string;
}

export function M7IvBandBestComboTable({ onSelectionsChange, onResolvedRulesChange, dataset: datasetProp }: Props = {}) {
  const [primary, setPrimary] = useState<M7Ranking>(
    () => loadLS('primary', 'avg_net_pnl'));
  const [family, setFamily] = useState<M7RuleFamily>(
    () => loadLS('family', 'all'));
  // Per-family Pure/Tiebreak preference — flipping families recalls the mode
  // that was last active for that family. Stored in one object so the
  // localStorage round-trip is atomic.
  const [modeByFamily, setModeByFamily] = useState<Record<M7RuleFamily, 'pure' | 'tiebreak'>>(
    () => loadLS('modeByFamily', { all: 'pure', max_profit: 'pure', margin_target: 'pure' }));
  const mode = modeByFamily[family] ?? 'pure';
  const setMode = (m: 'pure' | 'tiebreak') =>
    setModeByFamily(prev => ({ ...prev, [family]: m }));
  const [secondary, setSecondary] = useState<M7Ranking>(
    () => loadLS('secondary', 'avg_min_mtm_losers'));
  const [tolerancePct, setTolerancePct] = useState<number>(
    () => loadLS('tolerance', 5));
  // Sizing mode: "capital" (default) lets capital + deploy% + optional DD cap
  // drive per-band lot count (varies). "lots" pins a fixed lot count for all
  // bands (no re-rank — constant scale doesn't change ranking).
  const [sizingMode, setSizingMode] = useState<'capital' | 'lots'>(
    () => loadLS('sizing_mode', 'capital'));
  // Fixed lot count used when sizingMode === 'lots'. Default 100 (backtester baseline).
  const [fixedLots, setFixedLots] = useState<number>(
    () => loadLS('fixed_lots', 100));
  // Total deployable capital (USD). Per band, max lots = floor(capital * 100 / avg_margin)
  // since the backtester sized every trade at QTY_LOTS = 100. Same capital across
  // bands lets you compare what lot count each combo gets, then mentally multiply
  // the per-100-lot P&L numbers by lots/100.
  const [totalCapitalUsd, setTotalCapitalUsd] = useState<number>(
    () => loadLS('total_capital_usd', 1000));
  // % of total_capital actually deployed (default 100%).
  const [pctDeploy, setPctDeploy] = useState<number>(
    () => loadLS('pct_deploy', 100));
  // Drawdown constraint metric (null = no DD constraint).
  const [ddMetric, setDdMetric] = useState<string | null>(
    () => loadLS('dd_metric', null));
  // Threshold for the DD metric. The cell's per-100-lot value is scaled by
  // lots/100; we cap lots so |scaled value| ≤ |dd_threshold|.
  const [ddThreshold, setDdThreshold] = useState<number>(
    () => loadLS('dd_threshold', 100));
  // Multi-DD-cap (added 2026-05-14): additional (metric, threshold) constraints
  // applied alongside the legacy single cap. Final per-band lots =
  // min(margin-cap, legacy DD cap, …each item in this list…). Order in the
  // list doesn't change the math (min() is commutative) but is preserved for UI.
  const [ddCaps, setDdCaps] = useState<Array<{ metric: string; threshold: number }>>(
    () => loadLS('dd_caps', []) as Array<{ metric: string; threshold: number }>);
  // Phase 0/1 picker filters
  // min_hit_pct: drop cells where (n_trades - n_hard_cap) / n_trades < X%.
  // Default 50 — set to 0 to disable.
  const [minHitPct, setMinHitPct] = useState<number>(
    () => loadLS('min_hit_pct', 50));
  // max_loss_cap_pct: drop cells where scaled |max_loss| × lots/100 > capital × X%.
  const [maxLossCapPct, setMaxLossCapPct] = useState<number | null>(
    () => loadLS('max_loss_cap_pct', null));
  // max_drop_peak_to_trough_pct: drop cells where avg peak→trough drop > X%.
  // Effective after v6 grid lands (column exists). Pre-v6 the filter is a no-op.
  const [maxDropPct, setMaxDropPct] = useState<number | null>(
    () => loadLS('max_drop_pct', null));
  // min_n_trades: drop cells with too-small sample. Default 5 = "single-digit
  // samples can't generalise". Per-band fallback so high-IV bands with only
  // n=1 cells still show up with a warning chip.
  const [minNTrades, setMinNTrades] = useState<number>(
    () => loadLS('min_n_trades', 5));
  // min_win_rate: filter cells whose win_rate < X%. 0/null disables.
  const [minWinRate, setMinWinRate] = useState<number | null>(
    () => loadLS('min_win_rate', null));
  // max_losing_streak: drop cells whose max_consec_losses > X. null disables.
  const [maxLosingStreak, setMaxLosingStreak] = useState<number | null>(
    () => loadLS('max_losing_streak', null));
  // pick_mode: 'by_hour' (one cell per band+hour) vs 'aggregate_hours'
  // (collapse hours so every Friday with this band's IV is tested).
  const [pickMode, setPickMode] = useState<'by_hour' | 'aggregate_hours'>(
    () => loadLS('pick_mode', 'by_hour') as 'by_hour' | 'aggregate_hours');
  // Dimension whitelists — constrain the picker's search space. Empty = no filter.
  const [expiryFilter, setExpiryFilter] = useState<string[]>(
    () => {
      const saved = (loadLS('expiry_filter', []) as string[]) || [];
      // Drop legacy entries (biweekly/monthly/quarterly) that were removed
      // from the picker on 2026-05-14 — keeps stale localStorage harmless.
      const allowed = new Set(['current (Sat)', 'next (Sun)', 'next_to_next (Mon)', 'weekly (7d)']);
      return saved.filter(x => allowed.has(x));
    });
  const [deltaFilter, setDeltaFilter] = useState<number[]>(
    () => loadLS('delta_filter', []) as number[]);
  const [hourFilter, setHourFilter] = useState<number[]>(
    () => loadLS('hour_filter', []) as number[]);
  const [bandFilter, setBandFilter] = useState<string[]>(
    () => loadLS('band_filter', []) as string[]);
  // Exit-time filter — restricts grid to fixed-hour rules (sl{X}_exit_hr_{h})
  // whose hour-suffix is in the selection. Empty = no restriction. When set,
  // non-fixed-hour rules (baseline, max_profit, margin_target) are dropped.
  const [exitHourFilter, setExitHourFilter] = useState<string[]>(
    () => loadLS('exit_hour_filter', []) as string[]);
  // Pro Metrics columns toggle — Sharpe/Sortino/VaR/CVaR/Worst-5 + path peak-trough-peak.
  // Off by default to keep the table manageable; persisted to localStorage.
  const [showProMetrics, setShowProMetrics] = useState<boolean>(
    () => loadLS('show_pro_metrics', false));
  // Phase B: multi-dim bucketing tab. 'band' = legacy single-grid; others
  // lazy-build on first request. Persisted under 'm7:bestcombo:tab'.
  type BucketTab = 'band' | 'band_ivrv' | 'band_ivrv_slope_cn'
                 | 'band_ivrv_slope_nn' | 'band_ivrv_slope_cnn'
                 | 'band_ivrv_ts_legacy';
  const [bucketTab, setBucketTab] = useState<BucketTab>(
    () => loadLS('bucket_tab', 'band') as BucketTab);
  const [ivrvBucket, setIvrvBucket] = useState<'rich' | 'fair' | 'cheap' | ''>(
    () => loadLS('ivrv_bucket_filter', '') as 'rich' | 'fair' | 'cheap' | '');
  const [slopeBucket, setSlopeBucket] = useState<'backwardation' | 'neutral' | 'contango' | ''>(
    () => loadLS('slope_bucket_filter', '') as 'backwardation' | 'neutral' | 'contango' | '');
  // Premium-SL whitelist — chip group below the Family selector. Empty array
  // means "all SLs" (server-side default). Values must be ⊆ SL_OPTIONS;
  // legacy/unknown entries are filtered out on load.
  const [slFilter, setSlFilter] = useState<number[]>(
    () => {
      const saved = (loadLS('sl_filter', []) as number[]) || [];
      const allowed = new Set<number>(SL_OPTIONS);
      return saved.filter(x => allowed.has(x));
    });

  const [resp, setResp] = useState<M7IvBandBestComboResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Rule comparison modal — opens on row click
  const [drilldown, setDrilldown] = useState<{
    band: string; expiry_bucket: string; delta_target: number;
    entry_hour_ist: number; rule_label: string; lots: number;
  } | null>(null);
  // Cell analysis modal — opens via the dedicated 🔍 button on each row
  const [analysis, setAnalysis] = useState<{
    band: string; expiry_bucket: string; delta_target: number;
    entry_hour_ist: number; rule_label: string; lots: number;
  } | null>(null);

  // Persist state changes
  useEffect(() => { saveLS('primary',           primary);         }, [primary]);
  useEffect(() => { saveLS('family',            family);          }, [family]);
  useEffect(() => { saveLS('modeByFamily',      modeByFamily);    }, [modeByFamily]);
  useEffect(() => { saveLS('secondary',         secondary);       }, [secondary]);
  useEffect(() => { saveLS('tolerance',         tolerancePct);    }, [tolerancePct]);
  useEffect(() => { saveLS('total_capital_usd', totalCapitalUsd); }, [totalCapitalUsd]);
  useEffect(() => { saveLS('pct_deploy',        pctDeploy);       }, [pctDeploy]);
  useEffect(() => { saveLS('dd_metric',         ddMetric);        }, [ddMetric]);
  useEffect(() => { saveLS('dd_threshold',      ddThreshold);     }, [ddThreshold]);
  useEffect(() => { saveLS('dd_caps',           ddCaps);          }, [ddCaps]);
  useEffect(() => { saveLS('sizing_mode',       sizingMode);      }, [sizingMode]);
  useEffect(() => { saveLS('fixed_lots',        fixedLots);       }, [fixedLots]);
  useEffect(() => { saveLS('min_hit_pct',       minHitPct);       }, [minHitPct]);
  useEffect(() => { saveLS('max_loss_cap_pct',  maxLossCapPct);   }, [maxLossCapPct]);
  useEffect(() => { saveLS('max_drop_pct',      maxDropPct);      }, [maxDropPct]);
  useEffect(() => { saveLS('show_pro_metrics',  showProMetrics);  }, [showProMetrics]);
  useEffect(() => { saveLS('min_n_trades',      minNTrades);      }, [minNTrades]);
  useEffect(() => { saveLS('min_win_rate',      minWinRate);      }, [minWinRate]);
  useEffect(() => { saveLS('max_losing_streak', maxLosingStreak); }, [maxLosingStreak]);
  useEffect(() => { saveLS('pick_mode',         pickMode);        }, [pickMode]);
  useEffect(() => { saveLS('expiry_filter',     expiryFilter);    }, [expiryFilter]);
  useEffect(() => { saveLS('delta_filter',      deltaFilter);     }, [deltaFilter]);
  useEffect(() => { saveLS('hour_filter',       hourFilter);      }, [hourFilter]);
  useEffect(() => { saveLS('band_filter',       bandFilter);      }, [bandFilter]);
  useEffect(() => { saveLS('exit_hour_filter',  exitHourFilter);  }, [exitHourFilter]);
  useEffect(() => { saveLS('bucket_tab',        bucketTab);       }, [bucketTab]);
  useEffect(() => { saveLS('ivrv_bucket_filter', ivrvBucket);     }, [ivrvBucket]);
  useEffect(() => { saveLS('slope_bucket_filter', slopeBucket);   }, [slopeBucket]);
  useEffect(() => { saveLS('sl_filter',           slFilter);      }, [slFilter]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    // 300ms debounce: rapid filter changes restart the timer; only the last stable
    // state actually triggers a fetch. Prevents 10 requests during a slider drag.
    const DEBOUNCE_MS = 300;

    const tick = () => {
      if (!active) return;
      setLoading(true);
      setErr(null);
      const args: FetchBestComboArgs = { ranking: primary, rule_family: family };
      if (mode === 'tiebreak') {
        args.secondary = secondary;
        args.tolerance_pct = tolerancePct;
      }
      // Send capital params only in 'capital' mode. In 'lots' mode we keep
      // the backend's 100-lot baseline pick (constant scale = same ranking)
      // and apply the fixed-lot multiplier client-side.
      if (sizingMode === 'capital' && totalCapitalUsd > 0) {
        args.total_capital_usd = totalCapitalUsd;
        args.pct_deploy = pctDeploy;
        if (ddMetric && ddThreshold > 0) {
          args.dd_metric = ddMetric;
          args.dd_threshold = ddThreshold;
        }
        const validCaps = ddCaps.filter(c => c.metric && c.threshold > 0);
        if (validCaps.length > 0) args.dd_caps = validCaps;
      }
      // Picker filters — always send (server defaults to 50 if absent, but we
      // want explicit control from UI state, including "disabled" = 0).
      args.min_hit_pct = minHitPct;
      if (maxLossCapPct != null && maxLossCapPct > 0 && sizingMode === 'capital') {
        args.max_loss_cap_pct = maxLossCapPct;
      }
      if (maxDropPct != null && maxDropPct > 0) {
        args.max_drop_peak_to_trough_pct = maxDropPct;
      }
      args.min_n_trades = minNTrades;
      if (minWinRate != null && minWinRate > 0) {
        args.min_win_rate = minWinRate;
      }
      if (maxLosingStreak != null && maxLosingStreak > 0) {
        args.max_losing_streak = maxLosingStreak;
      }
      args.pick_mode = pickMode;
      if (expiryFilter.length > 0) args.expiry_buckets = expiryFilter;
      if (deltaFilter.length > 0) args.delta_targets = deltaFilter;
      if (hourFilter.length > 0) args.entry_hours = hourFilter;
      if (bandFilter.length > 0) args.iv_bands = bandFilter;
      if (exitHourFilter.length > 0) args.exit_hours = exitHourFilter;
      if (slFilter.length > 0) args.premium_sl_pcts = slFilter;
      // Phase B — bucketed tabs. 'band' is the default no-tab path.
      args.tab = bucketTab;
      if (ivrvBucket) args.ivrv_bucket = ivrvBucket;
      if (slopeBucket && bucketTab !== 'band' && bucketTab !== 'band_ivrv') {
        args.slope_bucket = slopeBucket;
      }
      fetchM7IvBandBestCombo(args, ac.signal)
        .then(r => {
          if (!active) return;
          setResp(r);
          if (r.status === 'warming') {
            window.setTimeout(tick, 5000);
          }
        })
        .catch(e => {
          if (!active || e?.name === 'AbortError') return;
          // Transient backend-restart races throw "500 Internal Server Error"
          // or "Failed to fetch" / "NetworkError" briefly. Auto-retry once
          // after 2s before surfacing the error to the user.
          const msg = String(e ?? '');
          const isTransient = /\b500\b|NetworkError|Failed to fetch|ECONNRESET|fetch failed/i.test(msg);
          if (isTransient) {
            window.setTimeout(() => { if (active) tick(); }, 2000);
          } else {
            setErr(msg);
          }
        })
        .finally(() => { if (active) setLoading(false); });
    };
    // Debounce: wait 300ms; if deps change again within that window, clearTimeout
    // and start over (no fetch fires until filters settle).
    const debounceTimer = window.setTimeout(tick, DEBOUNCE_MS);

    return () => {
      active = false;
      window.clearTimeout(debounceTimer);
      ac.abort();
    };
  }, [primary, family, mode, secondary, tolerancePct,
      sizingMode, fixedLots,
      totalCapitalUsd, pctDeploy, ddMetric, ddThreshold,
      JSON.stringify(ddCaps),
      minHitPct, maxLossCapPct, maxDropPct, minNTrades, minWinRate, maxLosingStreak, pickMode,
      JSON.stringify(expiryFilter), JSON.stringify(deltaFilter), JSON.stringify(hourFilter), JSON.stringify(bandFilter),
      JSON.stringify(exitHourFilter), JSON.stringify(slFilter),
      bucketTab, ivrvBucket, slopeBucket]);

  // Same args we send to /iv_band_best_combo so the missed-Fridays panel
  // below computes its picks against the user's exact filter/sizing state.
  const missedFridaysArgs = useMemo<FetchBestComboArgs>(() => {
    const a: FetchBestComboArgs = { ranking: primary, rule_family: family };
    if (mode === 'tiebreak') { a.secondary = secondary; a.tolerance_pct = tolerancePct; }
    if (sizingMode === 'capital' && totalCapitalUsd > 0) {
      a.total_capital_usd = totalCapitalUsd;
      a.pct_deploy = pctDeploy;
      if (ddMetric && ddThreshold > 0) { a.dd_metric = ddMetric; a.dd_threshold = ddThreshold; }
      const validCaps = ddCaps.filter(c => c.metric && c.threshold > 0);
      if (validCaps.length > 0) a.dd_caps = validCaps;
    }
    a.min_hit_pct = minHitPct;
    if (maxLossCapPct != null && maxLossCapPct > 0 && sizingMode === 'capital') a.max_loss_cap_pct = maxLossCapPct;
    if (maxDropPct != null && maxDropPct > 0) a.max_drop_peak_to_trough_pct = maxDropPct;
    a.min_n_trades = minNTrades;
    if (minWinRate != null && minWinRate > 0) a.min_win_rate = minWinRate;
    if (maxLosingStreak != null && maxLosingStreak > 0) a.max_losing_streak = maxLosingStreak;
    a.pick_mode = pickMode;
    if (expiryFilter.length > 0) a.expiry_buckets = expiryFilter;
    if (deltaFilter.length > 0) a.delta_targets = deltaFilter;
    if (hourFilter.length > 0) a.entry_hours = hourFilter;
    if (bandFilter.length > 0) a.iv_bands = bandFilter;
    if (exitHourFilter.length > 0) a.exit_hours = exitHourFilter;
    if (slFilter.length > 0) a.premium_sl_pcts = slFilter;
    return a;
  }, [primary, family, mode, secondary, tolerancePct, sizingMode,
      totalCapitalUsd, pctDeploy, ddMetric, ddThreshold,
      JSON.stringify(ddCaps),
      minHitPct, maxLossCapPct, maxDropPct, minNTrades, minWinRate, maxLosingStreak, pickMode,
      JSON.stringify(expiryFilter), JSON.stringify(deltaFilter), JSON.stringify(hourFilter), JSON.stringify(bandFilter),
      JSON.stringify(exitHourFilter), JSON.stringify(slFilter)]);

  const isWarming = resp?.status === 'warming';
  const rows: M7IvBandBestComboRow[] = resp?.rows ?? [];

  // Notify the parent whenever the per-band rows change. Keyed on the rows'
  // (iv_band, expiry, delta, rule_label) signature so we don't churn the
  // callback on every re-render — only when the selection actually moves.
  // Rows with the effective per-band lot count baked in. In 'lots' sizing mode
  // the backend returns the 100-lot baseline; the displayed lots come from
  // `fixedLots` (see render below). Downstream consumers (M7LossesExplorer,
  // M7PivotProfilePanel) scope by `lots` so we must hand them the same
  // effective value the table is showing, not the raw backend `lots`.
  const rowsWithEffectiveLots = useMemo(() => rows.map(r => ({
    ...r,
    lots: sizingMode === 'lots' ? fixedLots : (r.lots ?? 100),
  })), [rows, sizingMode, fixedLots]);
  const rowsSignature = useMemo(() => rowsWithEffectiveLots.map(r =>
    `${r.iv_band}|${r.entry_hour_ist}|${r.expiry_bucket}|${r.delta_target}|${r.rule_label}|${r.lots}`
  ).join('||'), [rowsWithEffectiveLots]);
  useEffect(() => {
    if (onSelectionsChange) onSelectionsChange(rowsWithEffectiveLots);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowsSignature, onSelectionsChange]);

  // Hoist resolved per-band rules to parent for Stage-1 analysis.
  // Builds a Stage1PerBandRule per band from the settled rows.
  useEffect(() => {
    if (!onResolvedRulesChange) return;
    const resolved: Record<string, Stage1PerBandRule> = {};
    for (const r of rowsWithEffectiveLots) {
      if (!r.iv_band) continue;
      const rule_dict: Stage1PerBandRule['rule_dict'] = {};
      if (r.rule.premium_sl_pct != null) rule_dict.premium_sl_pct = r.rule.premium_sl_pct;
      if (r.rule.max_profit_pct != null) rule_dict.max_profit_pct = r.rule.max_profit_pct;
      if (r.rule.margin_target_pct != null) rule_dict.margin_target_pct = r.rule.margin_target_pct;
      if (r.rule.fixed_exit_hour_ist != null) rule_dict.fixed_exit_hour_ist = r.rule.fixed_exit_hour_ist;
      resolved[r.iv_band] = {
        rule_label: r.rule_label,
        rule_dict,
        expiry_bucket: r.expiry_bucket ?? null,
        delta_target: r.delta_target ?? null,
        entry_hour_ist: r.entry_hour_ist ?? null,
        lots: r.lots ?? 100,
        baseline_metrics: r,
      };
    }
    onResolvedRulesChange(resolved, datasetProp ?? 'delta_match');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowsSignature, onResolvedRulesChange, datasetProp]);
  const primaryDef = ALL_METRICS[primary] ?? PRIMARY_GROUPS[0].metrics[0];
  const secondaryDef = ALL_METRICS[secondary];
  const showTiebreakChip = mode === 'tiebreak' && secondaryDef != null;

  // ── Styles ──────────────────────────────────────────────────────────────────
  const th: React.CSSProperties = { padding: '6px 8px', color: '#7a9bb5', whiteSpace: 'nowrap' };
  const thR: React.CSSProperties = { ...th, textAlign: 'right' };
  const td: React.CSSProperties = { padding: '6px 8px', whiteSpace: 'nowrap' };
  const tdR: React.CSSProperties = { ...td, textAlign: 'right' };
  const selectStyle: React.CSSProperties = {
    background: '#0d1421', color: '#cfd9e3',
    border: '1px solid #1a2d42', borderRadius: 4,
    padding: '4px 8px', fontSize: 11,
  };
  const inputStyle: React.CSSProperties = {
    ...selectStyle, width: 60, textAlign: 'right',
  };

  return (
    <div style={{
      background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6,
      padding: 12, marginBottom: 10,
    }}>
      {/* Phase B — bucketed-tab strip. Default 'band' = legacy single-grid behavior.
          Other tabs lazy-build their grid on first click (~5-15s on warm exit cache;
          longer on cold). State persisted to localStorage. */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 8, flexWrap: 'wrap',
                    alignItems: 'center' }}>
        <span style={{ fontSize: 11, color: '#7a9bb5', marginRight: 6 }}>Bucket:</span>
        {([
          ['band',                  'Bands',           ''],
          ['band_ivrv',             '× IVRV',          'Split each band by IVRV regime (rich / fair / cheap).'],
          ['band_ivrv_slope_cn',    '× slope (cur↔next)',
            'Add IV-curve slope between current Sat and next Sun.'],
          ['band_ivrv_slope_nn',    '× slope (next↔n2n)',
            'Add IV-curve slope between next Sun and next_to_next Mon.'],
          ['band_ivrv_slope_cnn',   '× slope (cur↔n2n)',
            'Add IV-curve slope between current Sat and next_to_next Mon.'],
          ['band_ivrv_ts_legacy',   '× 7d-30d (control)',
            'Control axis using the legacy 7d-30d IV term-structure slope.'],
        ] as const).map(([tab, label, title]) => (
          <button key={tab}
                  onClick={() => setBucketTab(tab as BucketTab)}
                  title={title}
                  style={{
                    padding: '4px 10px', fontSize: 11,
                    background: bucketTab === tab ? '#1f6feb' : '#0a1018',
                    color: bucketTab === tab ? '#fff' : '#cfd9e3',
                    border: bucketTab === tab ? '1px solid #2f7feb' : '1px solid #1a2d42',
                    borderRadius: 4, cursor: 'pointer',
                    fontWeight: bucketTab === tab ? 700 : 400,
                  }}>
            {label}
          </button>
        ))}
        {/* IVRV chip-filter — only meaningful on bucketed tabs */}
        {bucketTab !== 'band' && (
          <>
            <span style={{ fontSize: 11, color: '#7a9bb5', marginLeft: 10, marginRight: 4 }}>
              IVRV:
            </span>
            {(['', 'rich', 'fair', 'cheap'] as const).map(b => (
              <button key={`ivrv-${b}`}
                      onClick={() => setIvrvBucket(b)}
                      style={{
                        padding: '3px 8px', fontSize: 10,
                        background: ivrvBucket === b ? '#7d4cdb' : '#0a1018',
                        color: ivrvBucket === b ? '#fff' : '#7a9bb5',
                        border: '1px solid #1a2d42', borderRadius: 3, cursor: 'pointer',
                      }}>
                {b || 'all'}
              </button>
            ))}
          </>
        )}
        {/* Slope chip-filter — only on slope tabs */}
        {bucketTab !== 'band' && bucketTab !== 'band_ivrv' && (
          <>
            <span style={{ fontSize: 11, color: '#7a9bb5', marginLeft: 10, marginRight: 4 }}>
              Slope:
            </span>
            {(['', 'backwardation', 'neutral', 'contango'] as const).map(b => (
              <button key={`slope-${b}`}
                      onClick={() => setSlopeBucket(b)}
                      style={{
                        padding: '3px 8px', fontSize: 10,
                        background: slopeBucket === b ? '#d29922' : '#0a1018',
                        color: slopeBucket === b ? '#0a0e17' : '#7a9bb5',
                        border: '1px solid #1a2d42', borderRadius: 3, cursor: 'pointer',
                      }}>
                {b || 'all'}
              </button>
            ))}
          </>
        )}
      </div>

      {/* Header — controls + summary */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8, gap: 12, flexWrap: 'wrap',
      }}>
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          Best combo per IV band — premium SL ∈ &#123;{(slFilter.length > 0 ? slFilter : [...SL_OPTIONS]).join(', ')}&#125;
          <span style={{
            fontSize: 11, fontWeight: 400, color: '#7a9bb5', marginLeft: 10,
          }}>
            {((slFilter.length > 0 ? slFilter.length : SL_OPTIONS.length) * FAMILY_VARIANTS_PER_SL[family])} rule
            variants × 7 expiries × 8 deltas. Pick the (expiry · Δ ·
            exit rule) that wins per IV band on the chosen score.
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {/* Score (primary) dropdown */}
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Score:</span>
          <select
            value={primary}
            onChange={e => setPrimary(e.target.value)}
            style={{ ...selectStyle, minWidth: 200 }}>
            {PRIMARY_GROUPS.map(g => (
              <optgroup key={g.label} label={g.label}>
                {g.metrics.map(md => (
                  <option key={md.key} value={md.key}>{md.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          {/* Rule-family filter */}
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Family:</span>
          <div style={{
            display: 'inline-flex', border: '1px solid #1a2d42', borderRadius: 4,
            overflow: 'hidden',
          }}
            title="Restrict the rule space to one take-profit family. Each family inherits the Pure/Tiebreak toggle.">
            {([
              { v: 'all', label: 'All' },
              { v: 'max_profit', label: 'MaxProfit %' },
              { v: 'margin_target', label: 'Margin %' },
            ] as const).map(({ v, label }) => (
              <button key={v}
                onClick={() => setFamily(v)}
                style={{
                  padding: '4px 10px', fontSize: 11, cursor: 'pointer',
                  background: family === v ? '#1f6feb' : 'transparent',
                  color: family === v ? '#fff' : '#cfd9e3',
                  border: 'none',
                }}>
                {label}
              </button>
            ))}
          </div>
          {/* SL whitelist — multi-select chips. Empty selection = no filter
              (server sweeps all five SLs). Click "All" to clear; click an
              SL value to toggle it in/out. */}
          <span style={{ fontSize: 11, color: '#7a9bb5' }}
                title="Restrict the picker to a subset of premium-SL pct values. Each chip toggles independently — empty selection means all five SLs are in play.">
            SL %:
          </span>
          <div style={{
            display: 'inline-flex', border: '1px solid #1a2d42', borderRadius: 4,
            overflow: 'hidden',
          }}>
            <button key="sl-all"
              onClick={() => setSlFilter([])}
              style={{
                padding: '4px 10px', fontSize: 11, cursor: 'pointer',
                background: slFilter.length === 0 ? '#1f6feb' : 'transparent',
                color: slFilter.length === 0 ? '#fff' : '#cfd9e3',
                border: 'none',
              }}>
              All
            </button>
            {SL_OPTIONS.map(sl => {
              const active = slFilter.includes(sl);
              return (
                <button key={`sl-${sl}`}
                  onClick={() => {
                    setSlFilter(prev =>
                      prev.includes(sl)
                        ? prev.filter(x => x !== sl)
                        : [...prev, sl].sort((a, b) => a - b),
                    );
                  }}
                  style={{
                    padding: '4px 10px', fontSize: 11, cursor: 'pointer',
                    background: active ? '#1f6feb' : 'transparent',
                    color: active ? '#fff' : '#cfd9e3',
                    border: 'none',
                  }}>
                  {sl}
                </button>
              );
            })}
          </div>
          {/* Pure ⇄ Tiebreak toggle */}
          <div style={{
            display: 'inline-flex', border: '1px solid #1a2d42', borderRadius: 4,
            overflow: 'hidden',
          }}>
            {(['pure', 'tiebreak'] as const).map(m => (
              <button key={m}
                onClick={() => setMode(m)}
                style={{
                  padding: '4px 10px', fontSize: 11, cursor: 'pointer',
                  background: mode === m ? '#1f6feb' : 'transparent',
                  color: mode === m ? '#fff' : '#cfd9e3',
                  border: 'none',
                }}>
                {m === 'pure' ? 'Pure' : 'Tiebreak'}
              </button>
            ))}
          </div>
          {/* Tiebreak controls */}
          {mode === 'tiebreak' && (
            <>
              <span style={{ fontSize: 11, color: '#7a9bb5' }}>±</span>
              <input
                type="number" min={0} max={100} step={0.5}
                value={tolerancePct}
                onChange={e => setTolerancePct(Number(e.target.value) || 0)}
                style={inputStyle} />
              <span style={{ fontSize: 11, color: '#7a9bb5' }}>%, then by:</span>
              <select
                value={secondary}
                onChange={e => setSecondary(e.target.value)}
                style={{ ...selectStyle, minWidth: 200 }}>
                {SECONDARY_GROUPS.map(g => (
                  <optgroup key={g.label} label={g.label}>
                    {g.metrics.map(md => (
                      <option key={md.key} value={md.key}>{md.label}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </>
          )}
          {/* Sizing-mode toggle: Capital → backend re-ranks on scaled total,
              Lots → fixed lot count for all bands, no re-rank (constant scale). */}
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Size by:</span>
          <div style={{
            display: 'inline-flex', border: '1px solid #1a2d42', borderRadius: 4,
            overflow: 'hidden',
          }}
            title="Capital → enter $ capital and the backend picks the best combo per band that maximises scaled total at that capital (lot count varies per band, low-margin combos win). Lots → enter a fixed lot count; ranking stays at the 100-lot baseline and all $ values scale by lots/100.">
            {(['capital', 'lots'] as const).map(m => (
              <button key={m}
                onClick={() => setSizingMode(m)}
                style={{
                  padding: '4px 10px', fontSize: 11, cursor: 'pointer',
                  background: sizingMode === m ? '#1f6feb' : 'transparent',
                  color: sizingMode === m ? '#fff' : '#cfd9e3',
                  border: 'none',
                }}>
                {m === 'capital' ? 'Capital' : 'Lots'}
              </button>
            ))}
          </div>
          {/* Capital-mode inputs (greyed when sizing by Lots). */}
          <span style={{ fontSize: 11, color: sizingMode === 'lots' ? '#3a4a5a' : '#7a9bb5' }}>Capital $</span>
          <input
            type="number" min={0} step={50}
            value={totalCapitalUsd}
            disabled={sizingMode === 'lots'}
            onChange={e => setTotalCapitalUsd(Math.max(0, Number(e.target.value) || 0))}
            style={{ ...inputStyle, width: 80, opacity: sizingMode === 'lots' ? 0.4 : 1 }}
            title="Total USD capital. Disabled when sizing by Lots." />
          <span style={{ fontSize: 11, color: sizingMode === 'lots' ? '#3a4a5a' : '#7a9bb5' }}>× Deploy</span>
          <input
            type="number" min={0} max={100} step={5}
            value={pctDeploy}
            disabled={sizingMode === 'lots'}
            onChange={e => setPctDeploy(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
            style={{ ...inputStyle, width: 55, opacity: sizingMode === 'lots' ? 0.4 : 1 }}
            title="Percent of total capital to deploy. Disabled when sizing by Lots." />
          <span style={{ fontSize: 11, color: sizingMode === 'lots' ? '#3a4a5a' : '#7a9bb5' }}>%, DD cap</span>
          <select
            value={ddMetric ?? ''}
            disabled={sizingMode === 'lots'}
            onChange={e => setDdMetric(e.target.value || null)}
            style={{ ...selectStyle, minWidth: 130, opacity: sizingMode === 'lots' ? 0.4 : 1 }}
            title="Optional drawdown-cap metric. Disabled when sizing by Lots.">
            <option value="">— none —</option>
            {SECONDARY_GROUPS.map(g => (
              <optgroup key={g.label} label={g.label}>
                {g.metrics.map(md => (
                  <option key={md.key} value={md.key}>{md.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          {ddMetric && sizingMode === 'capital' && (
            <input
              type="number" min={0} step={10}
              value={ddThreshold}
              onChange={e => setDdThreshold(Math.max(0, Number(e.target.value) || 0))}
              style={{ ...inputStyle, width: 70 }}
              title="DD threshold (absolute USD or %)." />
          )}
          {/* Multi-DD-cap: additional (metric, threshold) constraints. lots
              = min(margin-cap, legacy DD cap, …all caps here…). */}
          {sizingMode === 'capital' && ddCaps.map((cap, idx) => (
            <span key={idx} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 11, color: '#7a9bb5' }}>∧</span>
              <select
                value={cap.metric}
                onChange={e => {
                  const next = ddCaps.slice();
                  next[idx] = { ...next[idx], metric: e.target.value };
                  setDdCaps(next);
                }}
                style={{ ...selectStyle, minWidth: 130 }}
                title="Additional DD-cap metric. All caps combine via min().">
                <option value="">— pick —</option>
                {SECONDARY_GROUPS.map(g => (
                  <optgroup key={g.label} label={g.label}>
                    {g.metrics.map(md => (
                      <option key={md.key} value={md.key}>{md.label}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <input
                type="number" min={0} step={10}
                value={cap.threshold}
                onChange={e => {
                  const next = ddCaps.slice();
                  next[idx] = { ...next[idx], threshold: Math.max(0, Number(e.target.value) || 0) };
                  setDdCaps(next);
                }}
                style={{ ...inputStyle, width: 70 }}
                title="Threshold for this DD cap (absolute USD)." />
              <button
                onClick={() => setDdCaps(ddCaps.filter((_, i) => i !== idx))}
                style={{
                  background: 'transparent', color: '#7a9bb5',
                  border: '1px solid #1a2d42', borderRadius: 4,
                  padding: '2px 6px', fontSize: 11, cursor: 'pointer',
                }}
                title="Remove this DD cap">✕</button>
            </span>
          ))}
          {sizingMode === 'capital' && (
            <button
              onClick={() => setDdCaps([...ddCaps, { metric: '', threshold: 100 }])}
              style={{
                background: '#0d1421', color: '#7a9bb5',
                border: '1px solid #1a2d42', borderRadius: 4,
                padding: '2px 8px', fontSize: 11, cursor: 'pointer',
              }}
              title="Add another drawdown cap. Final per-band lots = min(margin-cap, all DD caps).">
              + Cap
            </button>
          )}
          {/* Lots-mode input (greyed when sizing by Capital). */}
          <span style={{ fontSize: 11, color: sizingMode === 'capital' ? '#3a4a5a' : '#7a9bb5' }}>Lots</span>
          <input
            type="number" min={1} step={10}
            value={fixedLots}
            disabled={sizingMode === 'capital'}
            onChange={e => setFixedLots(Math.max(1, Number(e.target.value) || 1))}
            style={{ ...inputStyle, width: 70, opacity: sizingMode === 'capital' ? 0.4 : 1 }}
            title="Fixed lot count applied to every band. All $ cells scale by lots/100. Best-combo ranking stays at the per-100-lot baseline (constant scale doesn't change which cell wins). Default 100 (backtester baseline)." />
          {/* Phase 0/1 picker filters */}
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Hit % ≥</span>
          <input
            type="number" min={0} max={100} step={5}
            value={minHitPct}
            onChange={e => setMinHitPct(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
            style={{ ...inputStyle, width: 55 }}
            title="Drop cells where the labelled rule fires on less than X% of trades. Hit % = (n_trades − n_hard_cap) / n_trades. Counts ANY non-hard-cap exit (rule_trigger, premium_sl, max_profit, margin_target, fixed_hour) as effective. Default 50 — set to 0 to disable." />
          <span style={{ fontSize: 11, color: sizingMode === 'lots' ? '#3a4a5a' : '#7a9bb5' }}>Max loss %</span>
          <input
            type="number" min={0} max={100} step={5}
            placeholder="off"
            value={maxLossCapPct ?? ''}
            disabled={sizingMode === 'lots'}
            onChange={e => {
              const v = e.target.value;
              setMaxLossCapPct(v === '' ? null : Math.max(0, Math.min(100, Number(v) || 0)));
            }}
            style={{ ...inputStyle, width: 55, opacity: sizingMode === 'lots' ? 0.4 : 1 }}
            title="Drop cells where scaled |max_loss| × lots/100 exceeds X% of deployable capital. Effective only with Capital sizing on. Leave blank to disable." />
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Max drop %</span>
          <input
            type="number" min={0} max={100} step={5}
            placeholder="off"
            value={maxDropPct ?? ''}
            onChange={e => {
              const v = e.target.value;
              setMaxDropPct(v === '' ? null : Math.max(0, Math.min(100, Number(v) || 0)));
            }}
            style={{ ...inputStyle, width: 55 }}
            title="Drop cells where avg pct_drop_peak_to_trough > X%. Effective after v6 grid lands (column exists). Leave blank to disable." />
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Min n</span>
          <input
            type="number" min={0} max={50} step={1}
            value={minNTrades}
            onChange={e => setMinNTrades(Math.max(0, Number(e.target.value) || 0))}
            style={{ ...inputStyle, width: 50 }}
            title="Minimum number of trades for a cell to be statistically credible. Per-band fallback: if NO cells in a band meet this threshold (e.g. high-IV bands with only n=1), the band still surfaces but tagged with a ⚠ low-sample warning. Default 5; set to 0 to disable." />
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Win % ≥</span>
          <input
            type="number" min={0} max={100} step={5}
            placeholder="off"
            value={minWinRate ?? ''}
            onChange={e => {
              const v = e.target.value;
              setMinWinRate(v === '' ? null : Math.max(0, Math.min(100, Number(v) || 0)));
            }}
            style={{ ...inputStyle, width: 55 }}
            title="Drop cells whose win_rate is below this percentage. Leave blank to disable." />
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>Max losing streak ≤</span>
          <input
            type="number" min={1} max={50} step={1}
            placeholder="off"
            value={maxLosingStreak ?? ''}
            onChange={e => {
              const v = e.target.value;
              setMaxLosingStreak(v === '' ? null : Math.max(1, Number(v) || 0));
            }}
            style={{ ...inputStyle, width: 55 }}
            title="Drop cells whose max_consec_losses (longest run of consecutive losing trades) exceeds this. Leave blank to disable." />
          <button
            onClick={() => setPickMode(m => m === 'by_hour' ? 'aggregate_hours' : 'by_hour')}
            style={{
              padding: '4px 10px', fontSize: 11, cursor: 'pointer',
              background: pickMode === 'aggregate_hours' ? '#1f6feb' : 'transparent',
              color: pickMode === 'aggregate_hours' ? '#fff' : '#cfd9e3',
              border: '1px solid #1f6feb', borderRadius: 4,
              fontWeight: 600,
            }}
            title="When ON, the picker collapses the entry_hour_ist dimension so every Friday whose IV landed in a band — across ALL entry hours — is tested for that band's combo. n_trades reflects every hour the band touched. When OFF (default), one cell per (band, hour) and only the picked hour's Fridays are counted.">
            {pickMode === 'aggregate_hours' ? '∑ All hours' : '⏱ Per hour'}
          </button>
          {/* Dimension whitelists — restrict the picker's search space.
              Empty = "All" (no filter on that dimension). "✓ All" buttons
              explicitly select every option. */}
          {(() => {
            // Excluded by policy 2026-05-14: biweekly / monthly / quarterly
            // carry too few historical Fridays — backend drops them from
            // the in-memory grid at load time.
            const ALL_EXPIRIES = ['current (Sat)', 'next (Sun)', 'next_to_next (Mon)', 'weekly (7d)'];
            const ALL_DELTAS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50];
            const ALL_HOURS = [0, 1, 2, 3, 21, 22, 23];
            const ALL_BANDS = ['0-20', '20-30', '30-40', '40-50', '50-60', '60-70', '70-80', '80-90', '90-100', '100+'];
            // Fixed-exit hours from backend _FIXED_HOURS. Suffix '1729' = 17:29 (just before settlement).
            const ALL_EXIT_HOURS: { value: string; label: string }[] = [
              { value: '5',    label: '05:00' },
              { value: '6',    label: '06:00' },
              { value: '7',    label: '07:00' },
              { value: '8',    label: '08:00' },
              { value: '9',    label: '09:00' },
              { value: '10',   label: '10:00' },
              { value: '11',   label: '11:00' },
              { value: '12',   label: '12:00' },
              { value: '13',   label: '13:00' },
              { value: '14',   label: '14:00' },
              { value: '15',   label: '15:00' },
              { value: '16',   label: '16:00' },
              { value: '17',   label: '17:00' },
              { value: '1729', label: '17:29' },
            ];
            const exp_all = expiryFilter.length === ALL_EXPIRIES.length;
            const dlt_all = deltaFilter.length === ALL_DELTAS.length;
            const hr_all  = hourFilter.length === ALL_HOURS.length;
            const bnd_all = bandFilter.length === ALL_BANDS.length;
            const eh_all  = exitHourFilter.length === ALL_EXIT_HOURS.length;
            const allBtnStyle = (on: boolean): React.CSSProperties => ({
              ...inputStyle,
              padding: '0 6px',
              cursor: 'pointer',
              color: on ? '#3fb950' : '#7a9bb5',
              border: `1px solid ${on ? '#3fb950' : '#1a2d42'}`,
            });
            return (<>
          <span style={{ fontSize: 11, color: expiryFilter.length ? '#1f6feb' : '#3a4a5a', fontWeight: expiryFilter.length ? 700 : 400 }}>
            Expiry {expiryFilter.length ? `(${expiryFilter.length})` : '(All)'}
          </span>
          <select
            multiple
            value={expiryFilter}
            onChange={e => setExpiryFilter(Array.from(e.target.selectedOptions, o => o.value))}
            style={{ ...inputStyle, width: 130, height: 22, opacity: expiryFilter.length ? 1 : 0.55 }}
            title="Optional — Ctrl/Cmd-click to whitelist expiry buckets. Empty selection (default) = all expiries are considered by the picker. Select 1+ to restrict the search space.">
            {ALL_EXPIRIES.map(e => (
              <option key={e} value={e}>{e}</option>
            ))}
          </select>
          <button onClick={() => setExpiryFilter(ALL_EXPIRIES)} style={allBtnStyle(exp_all)} title="Select every expiry bucket (same effect as empty for the picker; just makes the selection explicit).">
            ✓ All
          </button>
          {expiryFilter.length > 0 && (
            <button onClick={() => setExpiryFilter([])} style={{ ...inputStyle, padding: '0 6px', cursor: 'pointer', color: '#f0b300' }} title="Clear expiry filter (revert to All)">×</button>
          )}
          <span style={{ fontSize: 11, color: deltaFilter.length ? '#1f6feb' : '#3a4a5a', fontWeight: deltaFilter.length ? 700 : 400 }}>
            Δ {deltaFilter.length ? `(${deltaFilter.length})` : '(All)'}
          </span>
          <select
            multiple
            value={deltaFilter.map(String)}
            onChange={e => setDeltaFilter(Array.from(e.target.selectedOptions, o => parseFloat(o.value)))}
            style={{ ...inputStyle, width: 70, height: 22, opacity: deltaFilter.length ? 1 : 0.55 }}
            title="Optional — Ctrl/Cmd-click to whitelist delta targets. Empty selection (default) = all 8 deltas are considered. Select 1+ to restrict.">
            {[0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50].map(d => (
              <option key={d} value={d}>{d.toFixed(2)}</option>
            ))}
          </select>
          <button onClick={() => setDeltaFilter(ALL_DELTAS)} style={allBtnStyle(dlt_all)} title="Select every delta target.">
            ✓ All
          </button>
          {deltaFilter.length > 0 && (
            <button onClick={() => setDeltaFilter([])} style={{ ...inputStyle, padding: '0 6px', cursor: 'pointer', color: '#f0b300' }} title="Clear delta filter (revert to All)">×</button>
          )}
          <span style={{ fontSize: 11, color: hourFilter.length ? '#1f6feb' : '#3a4a5a', fontWeight: hourFilter.length ? 700 : 400 }}>
            Hour {hourFilter.length ? `(${hourFilter.length})` : '(All)'}
          </span>
          <select
            multiple
            value={hourFilter.map(String)}
            onChange={e => setHourFilter(Array.from(e.target.selectedOptions, o => parseInt(o.value, 10)))}
            style={{ ...inputStyle, width: 60, height: 22, opacity: hourFilter.length ? 1 : 0.55 }}
            title="Optional — Ctrl/Cmd-click to whitelist entry hours (IST). Empty selection (default) = all hours are considered. Select 1+ to restrict.">
            {[0, 1, 2, 3, 21, 22, 23].map(h => (
              <option key={h} value={h}>{String(h).padStart(2, '0')}</option>
            ))}
          </select>
          <button onClick={() => setHourFilter(ALL_HOURS)} style={allBtnStyle(hr_all)} title="Select every entry hour.">
            ✓ All
          </button>
          {hourFilter.length > 0 && (
            <button onClick={() => setHourFilter([])} style={{ ...inputStyle, padding: '0 6px', cursor: 'pointer', color: '#f0b300' }} title="Clear hour filter (revert to All)">×</button>
          )}
          <span style={{ fontSize: 11, color: bandFilter.length ? '#1f6feb' : '#3a4a5a', fontWeight: bandFilter.length ? 700 : 400 }}>
            IV band {bandFilter.length ? `(${bandFilter.length})` : '(All)'}
          </span>
          <select
            multiple
            value={bandFilter}
            onChange={e => setBandFilter(Array.from(e.target.selectedOptions, o => o.value))}
            style={{ ...inputStyle, width: 80, height: 22, opacity: bandFilter.length ? 1 : 0.55 }}
            title="Optional — Ctrl/Cmd-click to whitelist IV bands. Empty selection (default) = all 10 bands. Pick one band to isolate its best-fit; pick a few to compare side-by-side without the noise of the others.">
            {ALL_BANDS.map(b => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
          <button onClick={() => setBandFilter(ALL_BANDS)} style={allBtnStyle(bnd_all)} title="Select every IV band.">
            ✓ All
          </button>
          {bandFilter.length > 0 && (
            <button onClick={() => setBandFilter([])} style={{ ...inputStyle, padding: '0 6px', cursor: 'pointer', color: '#f0b300' }} title="Clear IV band filter (revert to All)">×</button>
          )}
          <span style={{ fontSize: 11, color: exitHourFilter.length ? '#1f6feb' : '#3a4a5a', fontWeight: exitHourFilter.length ? 700 : 400 }}>
            Exit time {exitHourFilter.length ? `(${exitHourFilter.length})` : '(All)'}
          </span>
          <select
            multiple
            value={exitHourFilter}
            onChange={e => setExitHourFilter(Array.from(e.target.selectedOptions, o => o.value))}
            style={{ ...inputStyle, width: 80, height: 22, opacity: exitHourFilter.length ? 1 : 0.55 }}
            title="Optional — Ctrl/Cmd-click to whitelist fixed exit hours (IST). When set, the picker restricts to sl{X}_exit_hr_{h} rules whose hour matches; non-fixed-hour rule families (baseline, max_profit, margin_target) are excluded.">
            {ALL_EXIT_HOURS.map(h => (
              <option key={h.value} value={h.value}>{h.label}</option>
            ))}
          </select>
          <button onClick={() => setExitHourFilter(ALL_EXIT_HOURS.map(h => h.value))} style={allBtnStyle(eh_all)} title="Select every fixed exit hour.">
            ✓ All
          </button>
          {exitHourFilter.length > 0 && (
            <button onClick={() => setExitHourFilter([])} style={{ ...inputStyle, padding: '0 6px', cursor: 'pointer', color: '#f0b300' }} title="Clear exit-time filter (revert to All)">×</button>
          )}
            </>);
          })()}
          {/* Conservative preset */}
          <button
            onClick={() => {
              setSizingMode('capital');
              setTotalCapitalUsd(600);
              setPctDeploy(100);
              setPrimary('composite_score');
              setDdMetric('avg_min_mtm');
              setDdThreshold(30);
              setMinHitPct(50);
              setMaxLossCapPct(25);
              setMaxDropPct(30);
              setMinNTrades(10);
              setModeByFamily(prev => ({ ...prev, [family]: 'pure' }));
            }}
            style={{
              padding: '4px 10px', fontSize: 11, cursor: 'pointer',
              background: '#1a3d2a', color: '#3fb950',
              border: '1px solid #3fb950', borderRadius: 4,
              fontWeight: 700,
            }}
            title="Sets: Capital $600, Deploy 100%, Composite score primary, DD cap avg_min_mtm @ $30, Max loss 25%, Max drop 30%, Hit % ≥ 50, Min n trades = 10 (drops single-digit-sample 'lucky' cells), Pure mode. Picker chooses bounded-drawdown, rule-effective, statistically-credible combos.">
            ◇ Conservative
          </button>
          <button
            onClick={() => setShowProMetrics(v => !v)}
            style={{
              padding: '4px 10px', fontSize: 11, cursor: 'pointer',
              background: showProMetrics ? '#1f6feb' : 'transparent',
              color: showProMetrics ? '#fff' : '#cfd9e3',
              border: '1px solid #1f6feb', borderRadius: 4,
              fontWeight: 600,
            }}
            title="Toggle the Pro Metrics column group: Sharpe / Sortino / Calmar / VaR 95 / CVaR 95 / Worst-5 + path peak-trough-peak ($s, times, drop %). All populated by the v6 grid.">
            {showProMetrics ? '◆' : '◇'} Pro metrics
          </button>
          <div style={{ fontSize: 11, color: '#7a9bb5' }}>
            {err ? <span style={{ color: '#f85149' }}>{err}</span>
              : isWarming ? `Warming ${resp?.rules_done ?? 0}/${resp?.rules_total ?? 96} rules…`
              : loading ? 'Loading…'
              : `${rows.length} bands`}
          </div>
          <ExcelButton
            disabled={rows.length === 0}
            title="Download the rows shown above as an .xlsx file. Numbers are exported in their raw (per-100-lot) form plus a 'lots' and 'scale_factor' column for downstream scaling in Excel."
            onClick={() => {
              const tag = `${primary}_${family}${mode === 'tiebreak' ? `_tie_${secondary}` : ''}`;
              const filename = `m7_best_combo_${tag}.xlsx`;
              // Flatten the rule dict into separate columns; otherwise pass-through.
              const flat = rows.map(r => ({
                ...r,
                rule_premium_sl_pct: r.rule?.premium_sl_pct ?? null,
                rule_max_profit_pct: r.rule?.max_profit_pct ?? null,
                rule_margin_target_pct: r.rule?.margin_target_pct ?? null,
                rule_fixed_exit_hour_ist: r.rule?.fixed_exit_hour_ist ?? null,
                scale_factor: (r.lots ?? 100) / 100,
                rule: undefined,  // drop nested object — xlsx can't serialize it
              }));
              exportRowsAsXlsx(filename, 'BestCombo', flat);
            }} />
        </div>
      </div>

      <LoadingBar visible={loading || isWarming} />

      {/* Warming state — progress info, no table yet */}
      {isWarming && rows.length === 0 && (
        <div style={{
          padding: '24px 12px', textAlign: 'center', color: '#7a9bb5',
          fontSize: 12, lineHeight: 1.6,
        }}>
          <div style={{ fontWeight: 600, color: '#cfd9e3', marginBottom: 4 }}>
            Computing the full sweep ({resp?.rules_done ?? 0} / {resp?.rules_total ?? 96} rules done)
          </div>
          <div>
            96 exit-rule variants × 10 IV bands × 7 expiries × 8 deltas.
            <br />
            First load after backend restart takes ~45 minutes; subsequent loads are instant.
          </div>
        </div>
      )}

      {!isWarming && !err && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{
            borderCollapse: 'collapse', fontSize: 12,
            fontVariantNumeric: 'tabular-nums', color: '#cfd9e3',
            opacity: loading ? 0.4 : 1, transition: 'opacity 120ms',
          }}>
            <thead>
              <tr style={{ textAlign: 'left' }}>
                {/* — Identity — */}
                <th style={th}>{datasetProp === 'calendar' ? 'Gap bucket' : 'IV band'} <InfoIcon text={datasetProp === 'calendar' ? "Backwardation gap bucket = near-expiry ATM IV − far-expiry ATM IV, in percentage points. [5,10) = gap in [5pp,10pp); the <5pp 'minimal' bucket is excluded." : "ATM IV bucket at entry hour. 0-20 = annualized IV in [0%, 20%); 20-30 = [20%, 30%); …; 100+ = ≥ 100%."} /></th>
                <th style={th}>Best entry hr <InfoIcon text={datasetProp === 'calendar' ? "Aggregated for the calendar sweep (∑ all hours) — best combo is per gap bucket × pair × Δ, not per entry hour." : "Hour-of-day IST when the trade was opened (Friday). The grid is swept per entry hour; this column shows the hour that won for this band."} /></th>
                <th style={th}>{datasetProp === 'calendar' ? 'Best pair' : 'Best expiry'} <InfoIcon text={datasetProp === 'calendar' ? "Expiry pair (near-far selector pair) that wins for this gap bucket, e.g. current-next, weekly-next_weekly. Same strike across both expiries." : "Expiry bucket: current = same Saturday settlement; next = following Sunday; next_to_next = following Monday; weekly/biweekly/monthly = standard Delta expiries."} /></th>
                <th style={th}>Best Δ <InfoIcon text="Target delta of each strangle/straddle leg at entry. 0.50 = ATM, 0.10 = OTM." /></th>
                <th style={th}>Exit rule <InfoIcon text="Combined exit predicate (OR-of-clauses). SL{X} = premium stop-loss at X% (one leg's mark ≥ entry × (1+X/100)). MaxProfit Y% = exit when total MTM ≥ Y% of credit. MarginTgt Y% = exit when total MTM ≥ Y% of margin (take-profit). Exit @HH:MM = fixed Saturday IST exit. Whichever fires first wins; if none fires the trade rides to Sat 17:30 settlement (hard cap)." /></th>

                {/* — Score — */}
                <th style={thR}>{primaryDef.label} <InfoIcon text={`Primary score (currently '${primaryDef.label}'). The cell shown per band is the one that maxes (or mins) this metric across the grid.`} /></th>
                {showTiebreakChip && (
                  <th style={thR}>
                    Tiebreak ({secondaryDef.label}) <InfoIcon text={`Tiebreak: among cells within ±${tolerancePct}% of the per-band best on ${primaryDef.label}, the cell with best ${secondaryDef.label} is picked.`} />
                  </th>
                )}

                {/* — Trade counts — */}
                <th style={thR}>n <InfoIcon text="Number of Friday trades in this cell." /></th>
                <th style={thR}>n wins <InfoIcon text="Count of trades that ended with net P&L > 0 (after entry slip, entry brokerage, exit slip, exit brokerage)." /></th>
                <th style={thR}>n loss <InfoIcon text="Count of trades that ended with net P&L ≤ 0." /></th>
                <th style={thR}>Win % <InfoIcon text="n_wins / n_trades." /></th>
                <th style={thR}>Hard cap <InfoIcon text="Trades that ran past all rules and exited at Saturday 17:30 IST (settlement)." /></th>
                <th style={thR}>Hit % <InfoIcon text="(n_trades − n_hard_cap) / n_trades — fraction of trades where some deterministic exit fired (rule_trigger, premium_sl, max_profit, margin_target, fixed_hour) BEFORE hard cap. Phase 0B picker filter drops cells below the Hit % threshold by default (50%)." /></th>
                <th style={thR}>Best fallback exit <InfoIcon text="When the picked rule doesn't fire, the trade rides to Sat 17:30 IST hard cap. This column shows the best-net-P&L `exit_hr_X` rule at the SAME (band, expiry, Δ, hour) — i.e. if instead of waiting for hard cap, the trade exited at this fixed time, what hour would yield the highest avg net P&L? Lets you see whether a hybrid 'primary rule + fallback exit time' would improve the outcome." /></th>
                <th style={th}>Analyze <InfoIcon text="🔍 button: opens a 2-tab analysis modal. Cross-band check shows how this rule performs across ALL 10 IV bands (regime fragility). Single-combo simulation shows the counterfactual P&L if you always traded this combo regardless of IV regime." /></th>

                {/* — Rule mechanics — */}
                <th style={thR}>Rule hits <InfoIcon text="Trades that exited because ANY rule fired (premium_sl OR max_profit OR margin_target). For take-profit families this INCLUDES profit-take fires, so 'Rule hits' can exceed losses — it's a 'rule triggered an exit' count, not a loss-cut count." /></th>
                <th style={thR}>SL hits <InfoIcon text="Trades where the premium-SL specifically fired (one leg's mark crossed entry × (1 + premium_sl_pct/100)). Strict loss-cut count — excludes take-profit fires." /></th>
                <th style={thR}>Max rule streak <InfoIcon text="Longest run of consecutive trades that exited via ANY rule fire (premium_sl + max_profit + margin_target). Companion of 'Rule hits' — same caveat (includes take-profit fires)." /></th>
                <th style={thR}>Max SL streak <InfoIcon text="Longest run of consecutive trades that exited via real premium-SL (excludes take-profit fires)." /></th>

                {/* — Outcome streaks — */}
                <th style={thR}>Max winning streak <InfoIcon text="Longest run of consecutive winning trades when Fridays are ordered chronologically." /></th>
                <th style={thR}>Max losing streak <InfoIcon text="Longest run of consecutive losing trades when Fridays are ordered chronologically." /></th>

                {/* — Net P&L — */}
                <th style={thR}>Avg net <InfoIcon text="Mean net P&L per trade (entry slip + entry brokerage + exit slip + exit brokerage all subtracted)." /></th>
                <th style={thR}>Total net <InfoIcon text="Sum of net P&L across all trades in the cell = n_trades × Avg net. Bottom-line cumulative P&L the cell produced (per 100-lot backtester baseline)." /></th>
                <th style={thR}>Avg win <InfoIcon text="Mean net P&L across winners only (all 4 cost components subtracted)." /></th>
                <th style={thR}>Total win <InfoIcon text="Sum of net P&L across winners only = avg_win × n_wins (scaled to sized lots). Bottom-line cumulative gain from winning trades alone." /></th>
                <th style={thR}>Avg loss <InfoIcon text="Mean net P&L across losers only (negative number)." /></th>
                <th style={thR}>Total loss <InfoIcon text="Sum of net P&L across losers only = avg_loss × n_losses (scaled to sized lots). Bottom-line cumulative loss from losing trades alone (negative)." /></th>
                <th style={thR}>Largest win <InfoIcon text="Max net P&L of any single trade in the cell." /></th>
                <th style={thR}>Largest loss <InfoIcon text="Min net P&L of any single trade in the cell (most negative)." /></th>

                {/* — Exit MTM (gross − entry slip only) — winners then losers — */}
                <th style={thR}>Avg exit MTM <InfoIcon text="Mean exit-time gross P&L with entry slippage only subtracted. This is the on-screen P&L at the moment of exit — NOT the realized number (entry brokerage + exit slip + exit brokerage not deducted)." /></th>
                <th style={thR}>Avg exit MTM (W) <InfoIcon text="Mean exit-time MTM across winners (entry slip only)." /></th>
                <th style={thR}>Largest exit MTM (W) <InfoIcon text="Max exit-time MTM among winners." /></th>
                <th style={thR}>Total exit MTM (W) <InfoIcon text="Sum of exit-time MTM across all winning trades (entry slip only)." /></th>
                <th style={thR}>Avg exit MTM (L) <InfoIcon text="Mean exit-time MTM across losers (entry slip only)." /></th>
                <th style={thR}>Largest exit MTM (L) <InfoIcon text="Min exit-time MTM among losers." /></th>
                <th style={thR}>Total exit MTM (L) <InfoIcon text="Sum of exit-time MTM across all losing trades (entry slip only)." /></th>

                {/* — Winners path MTM — */}
                <th style={thR}>Avg max MTM (W) <InfoIcon text="Mean peak MTM across winners (best unrealized point during the hold)." /></th>
                <th style={thR}>Avg min MTM (W) <InfoIcon text="Mean trough MTM across winners — how deep winners dipped before recovering." /></th>
                <th style={thR}>Max MTM (W) <InfoIcon text="Highest peak MTM observed across all winners." /></th>
                <th style={thR}>Min MTM (W) <InfoIcon text="Worst trough MTM observed across all winners." /></th>
                <th style={thR}>W &lt; avg min MTM <InfoIcon text="Count of winners whose min MTM dipped below the cell's avg-min-MTM-winners — winners that endured a worse-than-typical drawdown before recovering." /></th>

                {/* — Losers path MTM — */}
                <th style={thR}>Avg max MTM (L) <InfoIcon text="Mean peak MTM across losers — how high they went before turning losing." /></th>
                <th style={thR}>Avg min MTM (L) <InfoIcon text="Mean trough MTM across losers (worst point in the hold)." /></th>
                <th style={thR}>Max MTM (L) <InfoIcon text="Highest peak MTM observed across all losers." /></th>
                <th style={thR}>Min MTM (L) <InfoIcon text="Worst trough MTM observed across all losers." /></th>
                <th style={thR}>L &gt; avg max MTM <InfoIcon text="Count of losers whose max MTM rose above the cell's avg-max-MTM-losers — losers that showed a better-than-typical peak before turning into losses (missed exit opportunity)." /></th>

                {/* — % returns — */}
                <th style={thR}>Ret / margin <InfoIcon text="Mean per-trade ratio: net_pnl ÷ margin_at_entry. Capital-efficiency view of the strategy." /></th>
                <th style={thR}>Ret / credit <InfoIcon text="Mean per-trade ratio: net_pnl ÷ credit_collected. ROI on premium captured." /></th>
                <th style={thR}>Ret/margin (W) <InfoIcon text="Ret/margin restricted to winning trades only." /></th>
                <th style={thR}>Ret/credit (W) <InfoIcon text="Ret/credit restricted to winning trades only." /></th>
                <th style={thR}>Peak % <InfoIcon text="Mean of per-trade max_mtm ÷ credit. Average peak unrealized return as % of credit — shows what was theoretically achievable before exit. For time-based / take-profit exits, the gap to actual exit reveals what was 'left on the table'." /></th>
                <th style={thR}>Trough % <InfoIcon text="Mean of per-trade min_mtm ÷ credit. Average trough unrealized return as % of credit. Negative — shows how deep the trade dipped below water at any point." /></th>

                {/* — Capital (all values are scaled to the sized lot count when sizing is on) — */}
                <th style={thR}>Avg credit <InfoIcon text="Mean upfront credit collected per trade at the sized lot count (scales linearly with lots vs the 100-lot backtester baseline)." /></th>
                <th style={thR}>Avg margin <InfoIcon text="Mean portfolio margin required at the sized lot count (29-scenario engine, linear in qty). When sizing is off this falls back to the 100-lot baseline." /></th>
                <th style={thR}>Lots <InfoIcon text="Lot count chosen by the sizing engine for this band's picked combo. lots = floor(min(deployable_capital × 100 / avg_margin, |DD threshold| × 100 / |DD metric per 100 lots|)). When no capital is set, lots = 100 (backtester baseline)." /></th>

                {/* — Time — */}
                <th style={thR}>Avg exit time <InfoIcon text="Mean exit IST clock time across all trades = (entry hour + avg hold duration) mod 24h. Format: HH:MM (Xh Ym) where the bracketed value is the average hold duration." /></th>
                <th style={thR}>Avg winner exit <InfoIcon text="Mean exit IST clock time restricted to winning trades, with avg winners' hold duration in brackets." /></th>
                <th style={thR}>Avg loser exit <InfoIcon text="Mean exit IST clock time restricted to losing trades, with avg losers' hold duration in brackets." /></th>

                {/* — Pro Metrics column group (v6 grid) — */}
                {showProMetrics && (
                  <>
                    <th style={thR}>Sharpe <InfoIcon text="avg_net / stdev_net_pnl. Higher = more consistent edge. — when sample size is too small for stdev." /></th>
                    <th style={thR}>Sortino <InfoIcon text="avg_net / stdev_losses_only. Downside-vol-only ratio. — when no losses to compute stdev." /></th>
                    <th style={thR}>Calmar <InfoIcon text="avg_net / |max_loss|. Expected return per unit of worst-case loss." /></th>
                    <th style={thR}>VaR 95 <InfoIcon text="5th-percentile net P&L distribution — at sized lots, this is your 1-in-20 trade outcome." /></th>
                    <th style={thR}>CVaR 95 <InfoIcon text="Mean of trades below the 5th percentile. Expected loss conditional on tail event." /></th>
                    <th style={thR}>Worst-5 <InfoIcon text="Mean of the 5 worst-net-P&L trades. Fat-left-tail indicator." /></th>
                    <th style={thR}>Peak-1 <InfoIcon text="Avg max gross_pnl_usd seen BEFORE the trough across all trades in cell (entry-slip-only). Negative when the typical trade never went positive." /></th>
                    <th style={thR}>Trough <InfoIcon text="Avg min MTM across all trades — worst unrealized point during the hold." /></th>
                    <th style={thR}>Peak-2 <InfoIcon text="Avg max gross_pnl_usd seen AFTER the trough (recovery peak). High = trades typically bounce after dipping." /></th>
                    <th style={thR}>Δ P1→T <InfoIcon text="Avg (peak-1 − trough) ÷ max(peak-1, |trough|, $0.01). What fraction of the peak (or absolute trough) the trade typically gives back. Lower = less stress." /></th>
                    <th style={thR}>t(Peak-1) <InfoIcon text="Avg relative time of peak-1 within the hold (0=entry, 1=exit). Earlier means the trade typically tops out then turns." /></th>
                    <th style={thR}>t(Trough) <InfoIcon text="Avg relative time of trough within the hold (0=entry, 1=exit). Late means losers crater near the end." /></th>
                    <th style={thR}>t(Peak-2) <InfoIcon text="Avg relative time of the post-trough recovery peak." /></th>
                    <th style={thR}>Δ T→P2 <InfoIcon text="Avg (peak-2 − trough) ÷ |trough|. How much the trade typically recovers after the trough. Higher = better mean reversion." /></th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                // Sizing-mode resolution. In 'lots' mode every band uses the
                // same user-fixed lot count → backend's r.lots (= 100 baseline)
                // is overridden. In 'capital' mode we use server-computed lots.
                const lots = sizingMode === 'lots' ? fixedLots : (r.lots ?? 100);
                const k = lots / 100;
                const sk = (v: number | null | undefined) =>
                  v == null || isNaN(v as number) ? null : (v as number) * k;
                // Score formatter — scale only USD-type primary metrics. The
                // backend returns the original per-100-lot value as `score`;
                // we multiply by k for display so it matches the ranking used.
                const scaledScore = (primaryDef.fmt === 'usd' || primaryDef.fmt === 'usd0')
                  ? sk(r.score) : r.score;
                const scaledSecondary = (showTiebreakChip
                  && (secondaryDef.fmt === 'usd' || secondaryDef.fmt === 'usd0'))
                  ? sk(r.secondary_score) : r.secondary_score;
                return (
                <tr key={`${r.iv_band}-${r.expiry_bucket}-${r.delta_target}-${r.entry_hour_ist}-${r.rule_label}`}
                    onClick={() => {
                      if (r.entry_hour_ist == null) return;
                      setDrilldown({
                        band: r.iv_band,
                        expiry_bucket: r.expiry_bucket,
                        delta_target: r.delta_target,
                        entry_hour_ist: r.entry_hour_ist,
                        rule_label: r.rule_label,
                        lots,
                      });
                    }}
                    style={{ borderTop: '1px solid #1a2d42', cursor: 'pointer' }}
                    title="Click to compare all 96 rules at this (band, expiry, Δ, hour) combo.">
                  {/* — Identity — */}
                  <td style={{ ...td, fontWeight: 600 }}>
                    {r.iv_band}
                    {r._low_sample_warning && (
                      <span
                        style={{
                          marginLeft: 6, padding: '1px 5px', fontSize: 10,
                          color: '#f0b300', border: '1px solid #f0b300',
                          borderRadius: 3, fontWeight: 600, verticalAlign: 'middle',
                        }}
                        title="Low-sample warning: no cells in this band met the Min n threshold. Picker fell back to the full grid for this band — results are not statistically credible. Lower the Min n input or accept that high-IV regimes happen rarely enough that we have very few historical trades.">
                        ⚠ low-n
                      </span>
                    )}
                    {r.rank_status && r.rank_status !== 'ranked' && (
                      <span
                        style={{
                          marginLeft: 6, padding: '1px 5px', fontSize: 10,
                          color: r.rank_status === 'filtered' ? '#f85149' : '#f0b300',
                          border: `1px solid ${r.rank_status === 'filtered' ? '#f85149' : '#f0b300'}`,
                          borderRadius: 3, fontWeight: 600, verticalAlign: 'middle',
                        }}
                        title={r.filter_reason
                          ? `Composite-v2 status: ${r.rank_status}\nFailed gates: ${r.filter_reason}`
                          : `Composite-v2 status: ${r.rank_status}`}>
                        {r.rank_status === 'filtered' ? '⊘ filtered' : '⚠ low_n (v2)'}
                      </span>
                    )}
                    {r.rank_in_band != null && (
                      <span style={{
                        marginLeft: 6, fontSize: 10, color: '#7a9bb5',
                        fontWeight: 400,
                      }}
                            title="Rank within IV band under composite_score_v2 (lower = better).">
                        #{r.rank_in_band}
                      </span>
                    )}
                    {/* Phase B — IVRV / slope chip badges, visible on bucketed tabs. */}
                    {(r as any).ivrv_bucket && (
                      <span style={{
                        marginLeft: 6, padding: '1px 5px', fontSize: 9,
                        color: '#fff', background: '#7d4cdb',
                        borderRadius: 3, fontWeight: 600, verticalAlign: 'middle',
                      }} title="IVRV regime at trade entry">
                        {(r as any).ivrv_bucket}
                      </span>
                    )}
                    {((r as any).slope_cn_bucket
                      || (r as any).slope_nn_bucket
                      || (r as any).slope_cnn_bucket
                      || (r as any).ts_legacy_bucket) && (
                      <span style={{
                        marginLeft: 4, padding: '1px 5px', fontSize: 9,
                        color: '#0a0e17', background: '#d29922',
                        borderRadius: 3, fontWeight: 600, verticalAlign: 'middle',
                      }} title="Active slope-regime bucket">
                        {(r as any).slope_cn_bucket
                          || (r as any).slope_nn_bucket
                          || (r as any).slope_cnn_bucket
                          || (r as any).ts_legacy_bucket}
                      </span>
                    )}
                  </td>
                  <td style={td}>{r.entry_hour_ist == null
                    ? <span style={{ color: '#9c27b0', fontWeight: 600 }} title="Aggregate-hours mode: this row is the weighted aggregate across every entry hour where this band's IV appeared.">All hours</span>
                    : `${String(r.entry_hour_ist).padStart(2, '0')}:00`}</td>
                  <td style={td}>{r.expiry_bucket}</td>
                  <td style={td}>{r.delta_target.toFixed(2)}</td>
                  <td style={{ ...td, color: '#f0b300' }}>{fmtRuleLabel(r.rule_label)}</td>

                  {/* — Score (scaled when sizing is active) — */}
                  <td style={{
                    ...tdR,
                    color: primaryDef.goodIsHigh ? pnlColor(scaledScore) : pnlColor(scaledScore == null ? null : -scaledScore),
                    fontWeight: 600,
                  }}>
                    {fmtByType(scaledScore, primaryDef.fmt)}
                  </td>
                  {showTiebreakChip && (
                    <td style={{
                      ...tdR,
                      color: secondaryDef.goodIsHigh ? pnlColor(scaledSecondary) : pnlColor(scaledSecondary == null ? null : -scaledSecondary),
                    }}>
                      {fmtByType(scaledSecondary, secondaryDef.fmt)}
                    </td>
                  )}

                  {/* — Trade counts — */}
                  <td style={tdR}>{r.n_trades}</td>
                  <td style={tdR}>{r.n_wins ?? '—'}</td>
                  <td style={tdR}>{r.n_losses ?? '—'}</td>
                  <td style={tdR}>{pct(r.win_rate)}</td>
                  <td style={tdR}>{r.n_hard_cap ?? '—'}</td>
                  <td style={tdR}>{(() => {
                    const n = r.n_trades;
                    const hc = r.n_hard_cap;
                    if (n == null || hc == null || n === 0) return '—';
                    const hit = (n - hc) / n;
                    const color = hit >= 0.5 ? '#3fb950' : hit >= 0.25 ? '#f0b300' : '#f85149';
                    return <span style={{ color }}>{(hit * 100).toFixed(0)}%</span>;
                  })()}</td>
                  <td style={tdR}>{(() => {
                    const h = r.fallback_exit_hour;
                    const n = r.fallback_exit_avg_net;
                    if (h == null || n == null) return '—';
                    // Scaled by current lots so it's apples-to-apples with the picked Avg net P&L cell.
                    const scaled = (n as number) * k;
                    return <span title={r.fallback_exit_rule_label ?? ''}>
                      {`${String(h).padStart(2, '0')}:00 (${usd(scaled)})`}
                    </span>;
                  })()}</td>
                  <td style={td}>
                    <button
                      onClick={e => {
                        e.stopPropagation();  // don't trigger row click → rule-comparison modal
                        if (r.entry_hour_ist == null) return;
                        setAnalysis({
                          band: r.iv_band,
                          expiry_bucket: r.expiry_bucket,
                          delta_target: r.delta_target,
                          entry_hour_ist: r.entry_hour_ist,
                          rule_label: r.rule_label,
                          lots,
                        });
                      }}
                      style={{
                        background: 'transparent', border: '1px solid #1f6feb',
                        color: '#1f6feb', borderRadius: 4, padding: '2px 6px',
                        cursor: 'pointer', fontSize: 11,
                      }}
                      title="Open cross-band check + single-combo simulation modal for this row.">
                      🔍
                    </button>
                  </td>

                  {/* — Rule mechanics — */}
                  <td style={tdR}>{r.n_rule_trigger ?? '—'}</td>
                  <td style={tdR}>{r.n_premium_sl_hit ?? '—'}</td>
                  <td style={tdR}>{r.max_consec_sl_hits ?? '—'}</td>
                  <td style={tdR}>{r.max_consec_premium_sl_hits ?? '—'}</td>

                  {/* — Outcome streaks — */}
                  <td style={tdR}>{r.max_consec_wins ?? '—'}</td>
                  <td style={tdR}>{r.max_consec_losses ?? '—'}</td>

                  {/* — Net P&L (scaled by lots/100, sign-coloured) — */}
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_net_pnl)) }}>{usd(sk(r.avg_net_pnl))}</td>
                  <td style={{
                    ...tdR,
                    color: pnlColor(r.avg_net_pnl == null ? null : (r.avg_net_pnl * r.n_trades) * k),
                  }}>
                    {r.avg_net_pnl == null ? '—' : usd((r.avg_net_pnl * r.n_trades) * k)}
                  </td>
                  {(() => {
                    const totalWin = (r.avg_win_usd != null && r.n_wins != null)
                      ? r.avg_win_usd * r.n_wins * k : null;
                    const totalLoss = (r.avg_loss_usd != null && r.n_losses != null)
                      ? r.avg_loss_usd * r.n_losses * k : null;
                    return (
                      <>
                        <td style={{ ...tdR, color: pnlColor(sk(r.avg_win_usd)) }}>{usd(sk(r.avg_win_usd))}</td>
                        <td style={{ ...tdR, color: pnlColor(totalWin) }}>{usd(totalWin)}</td>
                        <td style={{ ...tdR, color: pnlColor(sk(r.avg_loss_usd)) }}>{usd(sk(r.avg_loss_usd))}</td>
                        <td style={{ ...tdR, color: pnlColor(totalLoss) }}>{usd(totalLoss)}</td>
                      </>
                    );
                  })()}
                  <td style={{ ...tdR, color: pnlColor(sk(r.max_win_usd)) }}>{usd(sk(r.max_win_usd))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.max_loss_usd)) }}>{usd(sk(r.max_loss_usd))}</td>

                  {/* — Exit MTM (scaled, winners then losers) — */}
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_exit_mtm)) }}>{usd(sk(r.avg_exit_mtm))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_win_mtm)) }}>{usd(sk(r.avg_win_mtm))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.largest_win_mtm)) }}>{usd(sk(r.largest_win_mtm))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.total_win_mtm)) }}>{usd(sk(r.total_win_mtm))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_loss_mtm)) }}>{usd(sk(r.avg_loss_mtm))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.largest_loss_mtm)) }}>{usd(sk(r.largest_loss_mtm))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.total_loss_mtm)) }}>{usd(sk(r.total_loss_mtm))}</td>

                  {/* — Winners path MTM (scaled) — */}
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_max_mtm_winners)) }}>{usd(sk(r.avg_max_mtm_winners))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_min_mtm_winners)) }}>{usd(sk(r.avg_min_mtm_winners))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.max_mtm_winners)) }}>{usd(sk(r.max_mtm_winners))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.min_mtm_winners)) }}>{usd(sk(r.min_mtm_winners))}</td>
                  <td style={tdR}>{r.n_winners_below_avg_min_mtm ?? '—'}</td>

                  {/* — Losers path MTM (scaled) — */}
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_max_mtm_losers)) }}>{usd(sk(r.avg_max_mtm_losers))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.avg_min_mtm_losers)) }}>{usd(sk(r.avg_min_mtm_losers))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.max_mtm_losers)) }}>{usd(sk(r.max_mtm_losers))}</td>
                  <td style={{ ...tdR, color: pnlColor(sk(r.min_mtm_losers)) }}>{usd(sk(r.min_mtm_losers))}</td>
                  <td style={tdR}>{r.n_losers_above_avg_max_mtm ?? '—'}</td>

                  {/* — % returns (sign-coloured) — */}
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_margin) }}>{pct(r.avg_pct_return_on_margin)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_credit) }}>{pct(r.avg_pct_return_on_credit)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_margin_winners) }}>{pct(r.avg_pct_return_on_margin_winners)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_credit_winners) }}>{pct(r.avg_pct_return_on_credit_winners)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_max_mtm_on_credit) }}>{pct(r.avg_pct_max_mtm_on_credit)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_min_mtm_on_credit) }}>{pct(r.avg_pct_min_mtm_on_credit)}</td>

                  {/* — Capital (scaled to the sized lots) — */}
                  <td style={tdR}>{usd(sk(r.avg_credit))}</td>
                  <td style={tdR}>{usd0(sk(r.avg_margin))}</td>
                  <td style={tdR}>{lots.toLocaleString()}</td>

                  {/* — Time (IST clock with hold duration in brackets) — */}
                  <td style={tdR}>{fmtExitClock(r.entry_hour_ist, r.avg_exit_offset_minutes)}</td>
                  <td style={tdR}>{fmtExitClock(r.entry_hour_ist, r.avg_winner_exit_offset_minutes)}</td>
                  <td style={tdR}>{fmtExitClock(r.entry_hour_ist, r.avg_loser_exit_offset_minutes)}</td>

                  {/* — Pro Metrics cells (scaled by lots where applicable) — */}
                  {showProMetrics && (() => {
                    const dur = r.avg_exit_offset_minutes ?? null;
                    const relTime = (relT: number | null | undefined) =>
                      relT == null || dur == null ? '—' :
                        fmtExitClock(r.entry_hour_ist, dur * relT);
                    const dropColor = (v: number | null | undefined) => {
                      if (v == null || isNaN(v as number)) return '#7a9bb5';
                      const x = v as number;
                      return x <= 0.2 ? '#3fb950' : x <= 0.5 ? '#f0b300' : '#f85149';
                    };
                    const numFmt = (v: number | null | undefined, dp = 2) =>
                      v == null || isNaN(v as number) ? '—' : (v as number).toFixed(dp);
                    // Pre-v6 fallback: if avg_pct_drop_peak_to_trough is null,
                    // recompute from peak/trough means using the bounded formula
                    // (peak − trough) / max(peak, |trough|, 0.01).
                    const dropPct = (() => {
                      if (r.avg_pct_drop_peak_to_trough != null) return r.avg_pct_drop_peak_to_trough;
                      const pb = r.avg_peak_before_trough;
                      const tr = r.avg_min_mtm;
                      if (pb == null || tr == null) return null;
                      const denom = Math.max(pb, Math.abs(tr), 0.01);
                      return (pb - tr) / denom;
                    })();
                    const recoverPct = (() => {
                      if (r.avg_pct_recovery_trough_to_peak != null) return r.avg_pct_recovery_trough_to_peak;
                      const pa = r.avg_peak_after_trough;
                      const tr = r.avg_min_mtm;
                      if (pa == null || tr == null) return null;
                      const denom = Math.max(Math.abs(tr), 0.01);
                      return (pa - tr) / denom;
                    })();
                    // v7 — 5-landmark zigzag intermediate landmarks + derived
                    // comparison vs the exit peak.
                    const p3exit   = sk(r.avg_peak_after_trough);
                    return (
                      <>
                        <td style={tdR}>{numFmt(r.sharpe_per_trade)}</td>
                        <td style={tdR}>{numFmt(r.sortino_per_trade)}</td>
                        <td style={tdR}>{numFmt(r.calmar_like)}</td>
                        <td style={{ ...tdR, color: pnlColor(sk(r.var_95_net)) }}>{usd(sk(r.var_95_net))}</td>
                        <td style={{ ...tdR, color: pnlColor(sk(r.cvar_95_net)) }}>{usd(sk(r.cvar_95_net))}</td>
                        <td style={{ ...tdR, color: pnlColor(sk(r.worst_5_avg_net)) }}>{usd(sk(r.worst_5_avg_net))}</td>
                        <td style={{ ...tdR, color: pnlColor(sk(r.avg_peak_before_trough)) }}>{usd(sk(r.avg_peak_before_trough))}</td>
                        <td style={{ ...tdR, color: pnlColor(sk(r.avg_min_mtm)) }}>{usd(sk(r.avg_min_mtm))}</td>
                        <td style={{ ...tdR, color: pnlColor(p3exit) }}>{usd(p3exit)}</td>
                        <td style={{ ...tdR, color: dropColor(dropPct) }}>
                          {dropPct == null ? '—' : `${(dropPct * 100).toFixed(0)}%`}
                        </td>
                        <td style={tdR}>{relTime(r.avg_rel_time_peak_before)}</td>
                        <td style={tdR}>{relTime(r.avg_rel_time_trough)}</td>
                        <td style={tdR}>{relTime(r.avg_rel_time_peak_after)}</td>
                        <td style={tdR}>
                          {recoverPct == null ? '—' : `${(recoverPct * 100).toFixed(0)}%`}
                        </td>
                      </>
                    );
                  })()}
                </tr>
                );
              })}
            </tbody>
          </table>
          {!loading && rows.length === 0 && resp?.status === 'ready' && (
            <div style={{
              padding: '20px 12px', textAlign: 'center', color: '#7a9bb5', fontSize: 12,
            }}>
              No data — the sweep returned an empty grid.
            </div>
          )}
        </div>
      )}

      {/* Footer — sweep size info */}
      {resp?.status === 'ready' && (
        <div style={{
          marginTop: 8, fontSize: 11, color: '#7a9bb5',
          borderTop: '1px solid #1a2d42', paddingTop: 6,
        }}>
          Swept {resp.n_cells ?? '—'} cells across {resp.n_rules ?? '—'} rule variants
          {' '}(premium_sl ∈ &#123;50,75,100&#125; × &#123;baseline + 10 max_profit + 10 margin_target + 11 fixed_hour&#125;).
          {family !== 'all' && (
            <>
              {' '}<strong>Family:</strong>{' '}
              {family === 'max_profit' ? 'max_profit only' : 'margin_target only'}.
            </>
          )}
          {mode === 'tiebreak' && (
            <>
              {' '}<strong>Tiebreak:</strong> within ±{tolerancePct}% of best {primaryDef.label}, pick by {secondaryDef?.label ?? secondary}.
            </>
          )}
        </div>
      )}
      <M7BestComboMissedFridaysTable args={missedFridaysArgs} />
      {drilldown && (
        <M7RuleComparisonModal
          band={drilldown.band}
          expiry_bucket={drilldown.expiry_bucket}
          delta_target={drilldown.delta_target}
          entry_hour_ist={drilldown.entry_hour_ist}
          pickedRuleLabel={drilldown.rule_label}
          totalCapitalUsd={sizingMode === 'capital' ? totalCapitalUsd : null}
          pctDeploy={pctDeploy}
          ddMetric={sizingMode === 'capital' ? ddMetric : null}
          ddThreshold={sizingMode === 'capital' && ddMetric ? ddThreshold : null}
          ddCaps={sizingMode === 'capital' ? ddCaps.filter(c => c.metric && c.threshold > 0) : []}
          minHitPct={minHitPct}
          maxLossCapPct={sizingMode === 'capital' ? maxLossCapPct : null}
          maxDropPct={maxDropPct}
          minNTrades={minNTrades}
          minWinRate={minWinRate}
          ruleFamily={family}
          primaryMetric={primary}
          primaryLabel={primaryDef.label}
          onClose={() => setDrilldown(null)} />
      )}
      {analysis && (
        <M7CellAnalysisModal
          band={analysis.band}
          expiry_bucket={analysis.expiry_bucket}
          delta_target={analysis.delta_target}
          entry_hour_ist={analysis.entry_hour_ist}
          rule_label={analysis.rule_label}
          totalCapitalUsd={sizingMode === 'capital' ? totalCapitalUsd : null}
          pctDeploy={pctDeploy}
          lots={analysis.lots}
          onClose={() => setAnalysis(null)} />
      )}
    </div>
  );
}
