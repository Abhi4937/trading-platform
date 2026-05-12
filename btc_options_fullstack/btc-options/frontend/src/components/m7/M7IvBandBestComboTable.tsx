// Best combo per IV band — for each band, the (expiry, delta, exit-rule)
// that scores best on the chosen primary metric. Optional Tiebreak mode lets
// the user filter to within tolerance of the per-band primary best, then pick
// by a secondary metric (e.g., lowest min MTM among cells with similar net P&L).
//
// Sweep: 96 rule variants — premium_sl ∈ {50, 75, 100} × {baseline, 10
// max_profit, 10 margin_target, 11 fixed_hour} = 32 per SL × 3 = 96.
//
// Backend: GET /api/v1/m7/iv_band_best_combo
//   ?ranking=<primary>           default avg_net_pnl
//   &secondary=<metric>          optional, enables tiebreak
//   &tolerance_pct=<n>           default 5, only used when secondary set
//
// First request after backend restart triggers a ~45 min background warmup;
// while warming the API returns 202-style {status:"warming", rules_done…}.

import React, { useEffect, useState } from 'react';
import { InfoIcon } from './InfoIcon';
import {
  fetchM7IvBandBestCombo,
  type FetchBestComboArgs,
  type M7IvBandBestComboResponse,
  type M7IvBandBestComboRow,
  type M7Ranking,
  type M7RuleFamily,
} from '../../services/m7_api';

// ── Metric catalog ──────────────────────────────────────────────────────────

type MetricFmt = 'usd' | 'usd0' | 'pct' | 'count';

interface MetricDef {
  key: string;
  label: string;
  fmt: MetricFmt;
  // Whether higher is "good" — used only for the score-cell color (green/red).
  goodIsHigh: boolean;
}

const PRIMARY_GROUPS: { label: string; metrics: MetricDef[] }[] = [
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
    label: 'Drawdown depth',
    metrics: [
      { key: 'avg_min_mtm_losers',        label: 'Avg min MTM (losers)', fmt: 'usd', goodIsHigh: true },
      { key: 'min_mtm_losers',            label: 'Min MTM (losers)',     fmt: 'usd', goodIsHigh: true },
      { key: 'avg_max_mtm_losers',        label: 'Avg max MTM (losers)', fmt: 'usd', goodIsHigh: true },
      { key: 'avg_pct_min_mtm_on_credit', label: 'Trough %',             fmt: 'pct', goodIsHigh: true },
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

function fmtByType(v: number | null | undefined, t: MetricFmt): string {
  switch (t) {
    case 'usd':   return usd(v);
    case 'usd0':  return usd0(v);
    case 'pct':   return pct(v);
    case 'count': return cnt(v);
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

export function M7IvBandBestComboTable() {
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

  const [resp, setResp] = useState<M7IvBandBestComboResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Persist state changes
  useEffect(() => { saveLS('primary',      primary);      }, [primary]);
  useEffect(() => { saveLS('family',       family);       }, [family]);
  useEffect(() => { saveLS('modeByFamily', modeByFamily); }, [modeByFamily]);
  useEffect(() => { saveLS('secondary',    secondary);    }, [secondary]);
  useEffect(() => { saveLS('tolerance',    tolerancePct); }, [tolerancePct]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();

    const tick = () => {
      if (!active) return;
      setLoading(true);
      setErr(null);
      const args: FetchBestComboArgs = { ranking: primary, rule_family: family };
      if (mode === 'tiebreak') {
        args.secondary = secondary;
        args.tolerance_pct = tolerancePct;
      }
      fetchM7IvBandBestCombo(args, ac.signal)
        .then(r => {
          if (!active) return;
          setResp(r);
          if (r.status === 'warming') {
            window.setTimeout(tick, 5000);
          }
        })
        .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
        .finally(() => { if (active) setLoading(false); });
    };
    tick();

    return () => { active = false; ac.abort(); };
  }, [primary, family, mode, secondary, tolerancePct]);

  const isWarming = resp?.status === 'warming';
  const rows: M7IvBandBestComboRow[] = resp?.rows ?? [];
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
      {/* Header — controls + summary */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 8, gap: 12, flexWrap: 'wrap',
      }}>
        <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700 }}>
          Best combo per IV band — premium SL ∈ &#123;50, 75, 100&#125;
          <span style={{
            fontSize: 11, fontWeight: 400, color: '#7a9bb5', marginLeft: 10,
          }}>
            96 rule variants × 7 expiries × 8 deltas. Pick the (expiry · Δ ·
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
          <div style={{ fontSize: 11, color: '#7a9bb5' }}>
            {err ? <span style={{ color: '#f85149' }}>{err}</span>
              : isWarming ? `Warming ${resp?.rules_done ?? 0}/${resp?.rules_total ?? 96} rules…`
              : loading ? 'Loading…'
              : `${rows.length} bands`}
          </div>
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
                <th style={th}>IV band <InfoIcon text="ATM IV bucket at entry hour. 0-20 = annualized IV in [0%, 20%); 20-30 = [20%, 30%); …; 100+ = ≥ 100%." /></th>
                <th style={th}>Best entry hr <InfoIcon text="Hour-of-day IST when the trade was opened (Friday). The grid is swept per entry hour; this column shows the hour that won for this band." /></th>
                <th style={th}>Best expiry <InfoIcon text="Expiry bucket: current = same Saturday settlement; next = following Sunday; next_to_next = following Monday; weekly/biweekly/monthly = standard Delta expiries." /></th>
                <th style={th}>Best Δ <InfoIcon text="Target delta of each strangle/straddle leg at entry. 0.50 = ATM, 0.10 = OTM." /></th>
                <th style={th}>Exit rule <InfoIcon text="Combined exit predicate (OR-of-clauses). SL{X} = premium stop-loss at X% (one leg's mark ≥ entry × (1+X/100)). MaxProfit Y% = exit when total MTM ≥ Y% of credit. MarginTgt Y% = exit when total MTM ≥ Y% of margin (take-profit). Exit @HH:MM = fixed Saturday IST exit. Whichever fires first wins; if none fires the trade rides to Sat 17:30 settlement (hard cap)." /></th>
                <th style={thR}>{primaryDef.label} <InfoIcon text={`Primary score (currently '${primaryDef.label}'). The cell shown per band is the one that maxes (or mins) this metric across the grid.`} /></th>
                {showTiebreakChip && (
                  <th style={thR}>
                    Tiebreak ({secondaryDef.label}) <InfoIcon text={`Tiebreak: among cells within ±${tolerancePct}% of the per-band best on ${primaryDef.label}, the cell with best ${secondaryDef.label} is picked.`} />
                  </th>
                )}
                <th style={thR}>n <InfoIcon text="Number of Friday trades in this cell." /></th>
                <th style={thR}>n wins <InfoIcon text="Count of trades that ended with net P&L > 0 (after entry slip, entry brokerage, exit slip, exit brokerage)." /></th>
                <th style={thR}>n loss <InfoIcon text="Count of trades that ended with net P&L ≤ 0." /></th>
                <th style={thR}>Rule hits <InfoIcon text="Trades that exited because ANY rule fired (premium_sl OR max_profit OR margin_target). For take-profit families this INCLUDES profit-take fires, so 'Rule hits' can exceed losses — it's a 'rule triggered an exit' count, not a loss-cut count." /></th>
                <th style={thR}>SL hits <InfoIcon text="Trades where the premium-SL specifically fired (one leg's mark crossed entry × (1 + premium_sl_pct/100)). Strict loss-cut count — excludes take-profit fires. Populates after the v4 grid finishes building." /></th>
                <th style={thR}>Hard cap <InfoIcon text="Trades that ran past all rules and exited at Saturday 17:30 IST (settlement)." /></th>
                <th style={thR}>Max losing streak <InfoIcon text="Longest run of consecutive losing trades when Fridays are ordered chronologically." /></th>
                <th style={thR}>Max winning streak <InfoIcon text="Longest run of consecutive winning trades when Fridays are ordered chronologically." /></th>
                <th style={thR}>Max rule streak <InfoIcon text="Longest run of consecutive trades that exited via ANY rule fire (premium_sl + max_profit + margin_target). Companion of 'Rule hits' — same caveat (includes take-profit fires)." /></th>
                <th style={thR}>Max SL streak <InfoIcon text="Longest run of consecutive trades that exited via real premium-SL (excludes take-profit fires). Populates after the v4 grid finishes building." /></th>
                <th style={thR}>Win % <InfoIcon text="n_wins / n_trades." /></th>
                <th style={thR}>Avg net <InfoIcon text="Mean net P&L per trade (entry slip + entry brokerage + exit slip + exit brokerage all subtracted)." /></th>
                <th style={thR}>Avg exit MTM <InfoIcon text="Mean exit-time gross P&L with entry costs (slip + brokerage) only subtracted. This is the on-screen P&L at the moment of exit — NOT the realized number (exit costs not deducted)." /></th>
                <th style={thR}>Avg win <InfoIcon text="Mean net P&L across winners only (all 4 cost components subtracted)." /></th>
                <th style={thR}>Total win MTM <InfoIcon text="Sum of exit-time MTM across all winning trades (entry costs only, like Avg exit MTM)." /></th>
                <th style={thR}>Avg win MTM <InfoIcon text="Mean exit-time MTM across winners (entry costs only)." /></th>
                <th style={thR}>Largest win MTM <InfoIcon text="Max exit-time MTM among winners." /></th>
                <th style={thR}>Avg max MTM (W) <InfoIcon text="Mean peak MTM across winners (best unrealized point during the hold)." /></th>
                <th style={thR}>Avg min MTM (W) <InfoIcon text="Mean trough MTM across winners — how deep winners dipped before recovering." /></th>
                <th style={thR}>Max MTM (W) <InfoIcon text="Highest peak MTM observed across all winners." /></th>
                <th style={thR}>Min MTM (W) <InfoIcon text="Worst trough MTM observed across all winners." /></th>
                <th style={thR}>W &lt; avg min MTM <InfoIcon text="Count of winners whose min MTM dipped below the cell's avg-min-MTM-winners — winners that endured a worse-than-typical drawdown before recovering." /></th>
                <th style={thR}>Avg loss <InfoIcon text="Mean net P&L across losers only (negative number)." /></th>
                <th style={thR}>Total loss MTM <InfoIcon text="Sum of exit-time MTM across all losing trades (entry costs only)." /></th>
                <th style={thR}>Avg loss MTM <InfoIcon text="Mean exit-time MTM across losers (entry costs only)." /></th>
                <th style={thR}>Largest loss MTM <InfoIcon text="Min exit-time MTM among losers." /></th>
                <th style={thR}>Avg max MTM (L) <InfoIcon text="Mean peak MTM across losers — how high they went before turning losing." /></th>
                <th style={thR}>Avg min MTM (L) <InfoIcon text="Mean trough MTM across losers (worst point in the hold)." /></th>
                <th style={thR}>Max MTM (L) <InfoIcon text="Highest peak MTM observed across all losers." /></th>
                <th style={thR}>Min MTM (L) <InfoIcon text="Worst trough MTM observed across all losers." /></th>
                <th style={thR}>L &gt; avg max MTM <InfoIcon text="Count of losers whose max MTM rose above the cell's avg-max-MTM-losers — losers that showed a better-than-typical peak before turning into losses (missed exit opportunity)." /></th>
                <th style={thR}>Largest win <InfoIcon text="Max net P&L of any single trade in the cell." /></th>
                <th style={thR}>Largest loss <InfoIcon text="Min net P&L of any single trade in the cell (most negative)." /></th>
                <th style={thR}>Avg credit <InfoIcon text="Mean upfront credit collected per trade (call_entry_mark + put_entry_mark × qty × 0.001 BTC)." /></th>
                <th style={thR}>Avg margin <InfoIcon text="Mean Delta Exchange portfolio margin required at entry (29-scenario engine)." /></th>
                <th style={thR}>Ret / margin <InfoIcon text="Mean per-trade ratio: net_pnl ÷ margin_at_entry. Capital-efficiency view of the strategy." /></th>
                <th style={thR}>Ret / credit <InfoIcon text="Mean per-trade ratio: net_pnl ÷ credit_collected. ROI on premium captured." /></th>
                <th style={thR}>Ret/margin (W) <InfoIcon text="Ret/margin restricted to winning trades only." /></th>
                <th style={thR}>Ret/credit (W) <InfoIcon text="Ret/credit restricted to winning trades only." /></th>
                <th style={thR}>Peak % <InfoIcon text="Mean of per-trade max_mtm ÷ credit. Average peak unrealized return as % of credit — shows what was theoretically achievable before exit. For time-based / take-profit exits, the gap to actual exit reveals what was 'left on the table'." /></th>
                <th style={thR}>Trough % <InfoIcon text="Mean of per-trade min_mtm ÷ credit. Average trough unrealized return as % of credit. Negative — shows how deep the trade dipped below water at any point." /></th>
                <th style={thR}>Avg exit time <InfoIcon text="Mean hold time (entry → exit) across all trades, in hours and minutes." /></th>
                <th style={thR}>Avg winner exit <InfoIcon text="Mean hold time restricted to winning trades." /></th>
                <th style={thR}>Avg loser exit <InfoIcon text="Mean hold time restricted to losing trades." /></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={`${r.iv_band}-${r.expiry_bucket}-${r.delta_target}-${r.entry_hour_ist}-${r.rule_label}`}
                    style={{ borderTop: '1px solid #1a2d42' }}>
                  <td style={{ ...td, fontWeight: 600 }}>{r.iv_band}</td>
                  <td style={td}>{r.entry_hour_ist == null ? '—' : `${String(r.entry_hour_ist).padStart(2, '0')}:00`}</td>
                  <td style={td}>{r.expiry_bucket}</td>
                  <td style={td}>{r.delta_target.toFixed(2)}</td>
                  <td style={{ ...td, color: '#f0b300' }}>{fmtRuleLabel(r.rule_label)}</td>
                  <td style={{
                    ...tdR,
                    color: primaryDef.goodIsHigh ? pnlColor(r.score) : pnlColor(r.score == null ? null : -r.score),
                    fontWeight: 600,
                  }}>
                    {fmtByType(r.score, primaryDef.fmt)}
                  </td>
                  {showTiebreakChip && (
                    <td style={{
                      ...tdR,
                      color: secondaryDef.goodIsHigh ? pnlColor(r.secondary_score) : pnlColor(r.secondary_score == null ? null : -r.secondary_score),
                    }}>
                      {fmtByType(r.secondary_score, secondaryDef.fmt)}
                    </td>
                  )}
                  <td style={tdR}>{r.n_trades}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.n_wins ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.n_losses ?? '—'}</td>
                  <td style={tdR}>{r.n_rule_trigger ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.n_premium_sl_hit ?? '—'}</td>
                  <td style={tdR}>{r.n_hard_cap ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.max_consec_losses ?? '—'}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.max_consec_wins ?? '—'}</td>
                  <td style={tdR}>{r.max_consec_sl_hits ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.max_consec_premium_sl_hits ?? '—'}</td>
                  <td style={tdR}>{pct(r.win_rate)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_net_pnl) }}>{usd(r.avg_net_pnl)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_exit_mtm) }}>{usd(r.avg_exit_mtm)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.avg_win_usd)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.total_win_mtm)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.avg_win_mtm)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.largest_win_mtm)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.avg_max_mtm_winners)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.avg_min_mtm_winners)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.max_mtm_winners)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.min_mtm_winners)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.n_winners_below_avg_min_mtm ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.avg_loss_usd)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.total_loss_mtm)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.avg_loss_mtm)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.largest_loss_mtm)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.avg_max_mtm_losers)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.avg_min_mtm_losers)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.max_mtm_losers)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.min_mtm_losers)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.n_losers_above_avg_max_mtm ?? '—'}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.max_win_usd)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.max_loss_usd)}</td>
                  <td style={tdR}>{usd(r.avg_credit)}</td>
                  <td style={tdR}>{usd0(r.avg_margin)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_margin) }}>
                    {pct(r.avg_pct_return_on_margin)}
                  </td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_pct_return_on_credit) }}>
                    {pct(r.avg_pct_return_on_credit)}
                  </td>
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {pct(r.avg_pct_return_on_margin_winners)}
                  </td>
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {pct(r.avg_pct_return_on_credit_winners)}
                  </td>
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {pct(r.avg_pct_max_mtm_on_credit)}
                  </td>
                  <td style={{ ...tdR, color: '#f85149' }}>
                    {pct(r.avg_pct_min_mtm_on_credit)}
                  </td>
                  <td style={{ ...tdR, color: '#f0b300' }}>
                    {fmtMinutes(r.avg_exit_offset_minutes)}
                  </td>
                  <td style={{ ...tdR, color: '#3fb950' }}>
                    {fmtMinutes(r.avg_winner_exit_offset_minutes)}
                  </td>
                  <td style={{ ...tdR, color: '#f85149' }}>
                    {fmtMinutes(r.avg_loser_exit_offset_minutes)}
                  </td>
                </tr>
              ))}
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
    </div>
  );
}
