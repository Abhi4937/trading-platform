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
import {
  fetchM7IvBandBestCombo,
  type FetchBestComboArgs,
  type M7IvBandBestComboResponse,
  type M7IvBandBestComboRow,
  type M7Ranking,
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

const SECONDARY_OPTIONS: MetricDef[] = [
  { key: 'avg_min_mtm_losers', label: 'Avg min MTM (losers)', fmt: 'usd',   goodIsHigh: true  },
  { key: 'max_loss_usd',       label: 'Largest loss',         fmt: 'usd',   goodIsHigh: true  },
  { key: 'max_consec_losses',  label: 'Max losing streak',    fmt: 'count', goodIsHigh: false },
  { key: 'avg_loss_usd',       label: 'Avg loss',             fmt: 'usd',   goodIsHigh: true  },
];

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
  const [mode, setMode] = useState<'pure' | 'tiebreak'>(
    () => loadLS('mode', 'pure'));
  const [secondary, setSecondary] = useState<M7Ranking>(
    () => loadLS('secondary', 'avg_min_mtm_losers'));
  const [tolerancePct, setTolerancePct] = useState<number>(
    () => loadLS('tolerance', 5));

  const [resp, setResp] = useState<M7IvBandBestComboResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Persist state changes
  useEffect(() => { saveLS('primary',   primary);      }, [primary]);
  useEffect(() => { saveLS('mode',      mode);         }, [mode]);
  useEffect(() => { saveLS('secondary', secondary);    }, [secondary]);
  useEffect(() => { saveLS('tolerance', tolerancePct); }, [tolerancePct]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();

    const tick = () => {
      if (!active) return;
      setLoading(true);
      setErr(null);
      const args: FetchBestComboArgs = { ranking: primary };
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
  }, [primary, mode, secondary, tolerancePct]);

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
                style={{ ...selectStyle, minWidth: 180 }}>
                {SECONDARY_OPTIONS.map(md => (
                  <option key={md.key} value={md.key}>{md.label}</option>
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
                <th style={th}>IV band</th>
                <th style={th}>Best entry hr</th>
                <th style={th}>Best expiry</th>
                <th style={th}>Best Δ</th>
                <th style={th}>Exit rule</th>
                <th style={thR}>{primaryDef.label}</th>
                {showTiebreakChip && (
                  <th style={thR}
                      title={`Tiebreak: among cells within ±${tolerancePct}% of the per-band best on ${primaryDef.label}, picks the cell with best ${secondaryDef.label}.`}>
                    Tiebreak ({secondaryDef.label})
                  </th>
                )}
                <th style={thR}>n</th>
                <th style={{ ...thR, color: '#3fb950' }}>n wins</th>
                <th style={{ ...thR, color: '#f85149' }}>n loss</th>
                <th style={thR}>SL hits</th>
                <th style={thR}>Hard cap</th>
                <th style={{ ...thR, color: '#f85149' }}>Max losing streak</th>
                <th style={{ ...thR, color: '#3fb950' }}>Max winning streak</th>
                <th style={thR}>Max SL streak</th>
                <th style={thR}>Win %</th>
                <th style={thR}>Avg net</th>
                <th style={thR}>Avg exit MTM</th>
                <th style={{ ...thR, color: '#3fb950' }}>Avg win</th>
                <th style={{ ...thR, color: '#3fb950' }}
                    title="Sum of exit-time MTM across all winning trades (entry costs only).">
                  Total win MTM
                </th>
                <th style={{ ...thR, color: '#f85149' }}>Avg loss</th>
                <th style={{ ...thR, color: '#f85149' }}
                    title="Sum of exit-time MTM across all losing trades (entry costs only).">
                  Total loss MTM
                </th>
                <th style={{ ...thR, color: '#3fb950' }}>Largest win</th>
                <th style={{ ...thR, color: '#f85149' }}>Largest loss</th>
                <th style={thR}>Avg credit</th>
                <th style={thR}>Avg margin</th>
                <th style={thR}>Ret / margin</th>
                <th style={thR}>Ret / credit</th>
                <th style={{ ...thR, color: '#3fb950' }}
                    title="Average peak unrealized return as % of credit. Shows how high the trade went before exit — for time-based or take-profit exits, this reveals what was 'left on the table'.">
                  Peak %
                </th>
                <th style={{ ...thR, color: '#f85149' }}
                    title="Average trough unrealized return as % of credit. Negative — shows how deep the trade dipped below water before exit.">
                  Trough %
                </th>
                <th style={{ ...thR, color: '#f0b300' }}
                    title="Avg minutes from entry to exit, across all trades">
                  Avg exit time
                </th>
                <th style={{ ...thR, color: '#3fb950' }}
                    title="Avg exit time restricted to winning trades only">
                  Avg winner exit
                </th>
                <th style={{ ...thR, color: '#f85149' }}
                    title="Avg exit time restricted to losing trades only">
                  Avg loser exit
                </th>
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
                  <td style={tdR}>{r.n_hard_cap ?? '—'}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{r.max_consec_losses ?? '—'}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{r.max_consec_wins ?? '—'}</td>
                  <td style={tdR}>{r.max_consec_sl_hits ?? '—'}</td>
                  <td style={tdR}>{pct(r.win_rate)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_net_pnl) }}>{usd(r.avg_net_pnl)}</td>
                  <td style={{ ...tdR, color: pnlColor(r.avg_exit_mtm) }}>{usd(r.avg_exit_mtm)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.avg_win_usd)}</td>
                  <td style={{ ...tdR, color: '#3fb950' }}>{usd(r.total_win_mtm)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.avg_loss_usd)}</td>
                  <td style={{ ...tdR, color: '#f85149' }}>{usd(r.total_loss_mtm)}</td>
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
