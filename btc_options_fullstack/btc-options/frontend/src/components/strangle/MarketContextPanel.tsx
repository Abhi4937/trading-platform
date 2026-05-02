/**
 * MarketContextPanel — renders the wide M1+M2+M3 snapshot at a strangle's
 * entry timestamp. Used inside StrangleAnalyticsPanel as a collapsible
 * "Market Context Snapshot" section. Same data shape on:
 *   • Strategy Builder (fetched from /historical/snapshot-context)
 *   • Backtest trade detail (baked into BacktestTrade.market_context)
 *
 * Sections:
 *   1. Spot Indicators (M1) — RSI/ATR/ADX × 7 timeframes, RV/RVP, MA dist, etc.
 *   2. Options Metrics (M2) — ATM IV term, IVP per tenor + per TF, skew, GEX, walls.
 *   3. Derived (M3) — VRP family, vol-of-vol, expected move, pattern.
 *
 * Behaviour:
 *   • If a column doesn't exist in the snapshot → cell shows "—".
 *   • "Expand all" reveals raw key-value table of every populated field.
 *   • "Export CSV" downloads a one-row CSV of the snapshot.
 */
import React, { useMemo, useState } from 'react';

type Ctx = Record<string, number | string | null>;
type CellTone = 'good' | 'warn' | 'bad' | 'neutral';

interface Props {
  context: Ctx | null | undefined;
  title?: string;
  defaultOpen?: boolean;
}

// ── Formatters ───────────────────────────────────────────────────────────────

const f = (v: number | string | null | undefined, decimals = 2): string => {
  if (v === null || v === undefined) return '—';
  const n = typeof v === 'string' ? parseFloat(v) : v;
  if (typeof v === 'string' && Number.isNaN(n)) return v;
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(decimals);
};
const fpct = (v: any, d = 1): string => v == null ? '—' : `${(typeof v === 'number' ? v : parseFloat(v)).toFixed(d)}%`;
const fivp = (v: any): string => v == null ? '—' : f(v, 0);
const fK   = (v: any): string => {
  if (v == null) return '—';
  const n = typeof v === 'number' ? v : parseFloat(v);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toFixed(0);
};

const colorRsi = (v: any): CellTone => {
  if (v == null) return 'neutral';
  const n = typeof v === 'number' ? v : parseFloat(v);
  if (!Number.isFinite(n)) return 'neutral';
  if (n >= 70) return 'bad';   // overbought
  if (n <= 30) return 'good';  // oversold (mean-reverts up)
  return 'neutral';
};
const colorAdx = (v: any): CellTone => {
  if (v == null) return 'neutral';
  const n = typeof v === 'number' ? v : parseFloat(v);
  if (!Number.isFinite(n)) return 'neutral';
  if (n < 20) return 'good';   // weak trend (good for short-vol)
  if (n > 30) return 'bad';    // trending (avoid)
  return 'warn';
};
const colorIvp = (v: any): CellTone => {
  if (v == null) return 'neutral';
  const n = typeof v === 'number' ? v : parseFloat(v);
  if (!Number.isFinite(n)) return 'neutral';
  if (n >= 60) return 'good';
  if (n <= 40) return 'bad';
  return 'warn';
};

const tonePalette: Record<CellTone, string> = {
  good: 'var(--green, #22c55e)',
  warn: 'var(--gold, #eab308)',
  bad:  'var(--red, #ef4444)',
  neutral: 'var(--text-secondary, #888)',
};

// ── Pattern descriptions ────────────────────────────────────────────────────

const PATTERN_DESC: Record<string, string> = {
  A: 'Fresh Spike',
  B: 'Post-Crash',
  C: 'Stale',
  D: 'Active Trend',
  Other: 'Other',
};
const PATTERN_TONE: Record<string, CellTone> = {
  A: 'good', B: 'warn', C: 'neutral', D: 'bad', Other: 'neutral',
};

// ── Multi-TF row helper ──────────────────────────────────────────────────────

interface MtfRowProps {
  label: string;
  cols: string[];
  context: Ctx;
  format?: (v: any) => string;
  tone?: (v: any) => CellTone;
  units?: string;
}
const MtfRow: React.FC<MtfRowProps> = ({ label, cols, context, format = (v) => f(v, 0), tone, units = '' }) => (
  <tr>
    <td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)', fontSize: '0.85em', whiteSpace: 'nowrap' }}>
      {label}
    </td>
    {cols.map((c) => {
      const v = context[c];
      const t = tone ? tone(v) : 'neutral';
      const display = v == null ? '—' : (format(v) + units);
      return (
        <td key={c} style={{ padding: '4px 6px', textAlign: 'center', fontFamily: 'monospace',
                              fontSize: '0.85em', color: tonePalette[t] }}>
          {display}
        </td>
      );
    })}
  </tr>
);

// ── Main component ──────────────────────────────────────────────────────────

const TF_HEADERS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];
const TF_HEADERS_SHORT = ['5m', '15m', '30m', '1h', '4h', '1d'];

const RSI_COLS = ['rsi_14_1m', 'rsi_14_5m', 'rsi_14_15m', 'rsi_14_30m', 'rsi_14_1h', 'rsi_14_4h', 'rsi_14_1d'];
const ADX_COLS = ['adx_14_5m', 'adx_14_15m', 'adx_14_30m', 'adx_14_1h', 'adx_14_4h', 'adx_14_1d'];
const ATR_COLS = ['atr_pct_5m', 'atr_pct_15m', 'atr_pct_30m', 'atr_pct_1h', 'atr_pct_4h', 'atr_pct_1d'];
const IVP_TF_COLS = ['ivp_1m', 'ivp_5m', 'ivp_15m', 'ivp_30m', 'ivp_1h', 'ivp_4h', 'ivp_1d'];

const TENORS = ['7d', '14d', '30d', '60d'];
const ATM_IV_COLS = ['atm_iv_7d', 'atm_iv_14d', 'atm_iv_30d', 'atm_iv_60d'];
const IVP_TENOR_COLS = ['ivp_atm_7d_90d', 'ivp_atm_14d_90d', 'ivp_atm_30d_90d', 'ivp_atm_60d_90d'];
const RV_COLS = ['rv_7d', 'rv_14d', 'rv_30d'];

export default function MarketContextPanel({ context, title = 'Market Context Snapshot', defaultOpen = true }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const [showAll, setShowAll] = useState(false);

  if (!context) {
    return (
      <div style={{ padding: 12, color: 'var(--text-secondary, #888)', fontSize: '0.9em' }}>
        Market context not available — needs M3 row to be built.
      </div>
    );
  }

  const populated = useMemo(() => {
    return Object.entries(context).filter(([_, v]) => v !== null && v !== undefined);
  }, [context]);

  const onExportCsv = () => {
    const rows = populated.map(([k, v]) => `"${k}","${v ?? ''}"`).join('\n');
    const csv = `key,value\n${rows}`;
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `market_context_${context.timestamp_unix ?? Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const pattern = (context.pattern as string) || 'Other';

  const sectionHdr = (label: string): React.CSSProperties => ({
    fontSize: '0.78em', fontWeight: 600, letterSpacing: '0.5px',
    color: 'var(--text-secondary, #aaa)', textTransform: 'uppercase',
    margin: '12px 0 6px',
  });

  return (
    <div style={{
      border: '1px solid var(--border, #333)', borderRadius: 6,
      background: 'var(--bg-elevated, rgba(255,255,255,0.02))',
      marginTop: 12,
    }}>
      <div onClick={() => setOpen(!open)}
           style={{ padding: '10px 14px', cursor: 'pointer',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    borderBottom: open ? '1px solid var(--border, #333)' : 'none' }}>
        <span style={{ fontWeight: 600, fontSize: '0.95em' }}>{title}</span>
        <span style={{ fontSize: '0.85em', color: 'var(--text-secondary, #888)' }}>
          {open ? '▼' : '▶'}  {populated.length} fields
        </span>
      </div>

      {open && (
        <div style={{ padding: '10px 14px 14px' }}>

          {/* Pattern badge + actions row */}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 8 }}>
            <span style={{
              padding: '2px 10px', borderRadius: 12, fontSize: '0.85em',
              background: tonePalette[PATTERN_TONE[pattern] || 'neutral'],
              color: '#000', fontWeight: 600,
            }}>
              {pattern} · {PATTERN_DESC[pattern] || pattern}
            </span>
            <button onClick={onExportCsv}
                    style={{ marginLeft: 'auto', padding: '4px 10px', fontSize: '0.8em',
                              background: 'transparent', color: 'var(--text-secondary, #888)',
                              border: '1px solid var(--border, #333)', borderRadius: 4, cursor: 'pointer' }}>
              ⬇ Export CSV
            </button>
            <button onClick={() => setShowAll(!showAll)}
                    style={{ padding: '4px 10px', fontSize: '0.8em',
                              background: 'transparent', color: 'var(--text-secondary, #888)',
                              border: '1px solid var(--border, #333)', borderRadius: 4, cursor: 'pointer' }}>
              {showAll ? '▲ Hide raw' : '▼ Expand all'}
            </button>
          </div>

          {/* M1 — Spot Indicators */}
          <div style={sectionHdr('M1 — Spot Indicators')}>M1 — Spot Indicators</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85em' }}>
            <thead>
              <tr><th style={{ padding: '4px 8px', textAlign: 'left', color: 'var(--text-secondary, #888)' }}>Indicator</th>
                  {TF_HEADERS.map(h => <th key={h} style={{ padding: '4px 6px', textAlign: 'center', color: 'var(--text-secondary, #888)' }}>{h}</th>)}</tr>
            </thead>
            <tbody>
              <MtfRow label="RSI(14)"  cols={RSI_COLS} context={context} tone={colorRsi} />
              {/* ADX/ATR are 6-TF (no 1m) — pad with empty first cell */}
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)', fontSize: '0.85em', whiteSpace: 'nowrap' }}>ADX(14)</td>
                  <td style={{ textAlign: 'center', color: 'var(--text-secondary, #555)' }}>—</td>
                  {ADX_COLS.map(c => {
                    const v = context[c]; const t = colorAdx(v);
                    return <td key={c} style={{ padding: '4px 6px', textAlign: 'center', fontFamily: 'monospace', fontSize: '0.85em', color: tonePalette[t] }}>{v == null ? '—' : f(v, 0)}</td>;
                  })}
              </tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)', fontSize: '0.85em', whiteSpace: 'nowrap' }}>ATR%</td>
                  <td style={{ textAlign: 'center', color: 'var(--text-secondary, #555)' }}>—</td>
                  {ATR_COLS.map(c => {
                    const v = context[c];
                    return <td key={c} style={{ padding: '4px 6px', textAlign: 'center', fontFamily: 'monospace', fontSize: '0.85em', color: 'var(--text-secondary, #ccc)' }}>{v == null ? '—' : f(v, 2)}</td>;
                  })}
              </tr>
              <MtfRow label="IVP" cols={IVP_TF_COLS} context={context} tone={colorIvp} format={(v) => f(v, 0)} />
            </tbody>
          </table>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 12 }}>
            <div>
              <div style={sectionHdr('RV / RVP')}>Realized Vol</div>
              <table style={{ width: '100%', fontSize: '0.85em' }}>
                <tbody>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>RV close</td>
                      <td style={{ fontFamily: 'monospace' }}>{fpct(context.rv_7d, 1)} / {fpct(context.rv_14d, 1)} / {fpct(context.rv_30d, 1)}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>RV Parkinson 7d</td>
                      <td style={{ fontFamily: 'monospace' }}>{fpct(context.rv_parkinson_7d, 1)}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>RVP percentile</td>
                      <td style={{ fontFamily: 'monospace' }}>7d={fivp(context.rvp_7d)} · 14d={fivp(context.rvp_14d)} · 30d={fivp(context.rvp_30d)}</td></tr>
                </tbody>
              </table>
            </div>
            <div>
              <div style={sectionHdr('Trend / MA')}>Trend / MA</div>
              <table style={{ width: '100%', fontSize: '0.85em' }}>
                <tbody>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>MA50 dist</td><td style={{ fontFamily: 'monospace' }}>{fpct(context.ma50_distance_pct, 2)}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>MA100 dist</td><td style={{ fontFamily: 'monospace' }}>{fpct(context.ma100_distance_pct, 2)}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>MA200 dist</td><td style={{ fontFamily: 'monospace' }}>{fpct(context.ma200_distance_pct, 2)}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>ATR comp 4h</td><td style={{ fontFamily: 'monospace' }}>{f(context.atr_compression_ratio, 2)}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary, #aaa)' }}>SuperTrend 4h</td><td style={{ fontFamily: 'monospace' }}>{(context.supertrend_signal_4h as number) > 0 ? '↑ bull' : (context.supertrend_signal_4h as number) < 0 ? '↓ bear' : '—'}</td></tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* M2 — Options Metrics */}
          <div style={sectionHdr('M2 — Options Metrics')}>M2 — Options Metrics</div>
          <table style={{ width: '100%', fontSize: '0.85em', borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)', whiteSpace: 'nowrap' }}>ATM IV (const-mat)</td>
                  <td style={{ fontFamily: 'monospace' }}>
                    {TENORS.map((t, i) => `${t}=${fpct(context[ATM_IV_COLS[i]] != null ? (context[ATM_IV_COLS[i]] as number) * 100 : null, 1)}`).join(' · ')}
                  </td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>IVP (90d percentile)</td>
                  <td style={{ fontFamily: 'monospace' }}>
                    {TENORS.map((t, i) => `${t}=${fivp(context[IVP_TENOR_COLS[i]])}`).join(' · ')}
                  </td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>Strangle synth IV</td>
                  <td style={{ fontFamily: 'monospace' }}>{fpct(context.strangle_synth_iv != null ? (context.strangle_synth_iv as number) * 100 : null, 1)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>Skew</td>
                  <td style={{ fontFamily: 'monospace' }}>RR_25d={f(context.risk_reversal_25d, 3)} · BF_25d={f(context.butterfly_25d, 3)} · wing/atm={f(context.wing_atm_ratio, 2)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>Term slope</td>
                  <td style={{ fontFamily: 'monospace' }}>7→30={f(context.term_slope_7_30, 3)} · 14→60={f(context.term_slope_14_60, 3)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>OI walls</td>
                  <td style={{ fontFamily: 'monospace' }}>call={fK(context.max_oi_call_strike)} · put={fK(context.max_oi_put_strike)} · PCR={f(context.pcr_oi, 2)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>GEX</td>
                  <td style={{ fontFamily: 'monospace' }}>total={fK(context.total_gex)} · regime={(context.gex_regime ?? '—') as string} · dist_to_flip={fpct(context.dist_to_flip_pct, 2)}</td></tr>
            </tbody>
          </table>

          {/* M3 — Derived */}
          <div style={sectionHdr('M3 — Derived')}>M3 — Derived</div>
          <table style={{ width: '100%', fontSize: '0.85em' }}>
            <tbody>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>VRP — IV-RV spread</td>
                  <td style={{ fontFamily: 'monospace' }}>7d={fpct(context.iv_rv_spread_7d, 1)} · 14d={fpct(context.iv_rv_spread_14d, 1)} · 30d={fpct(context.iv_rv_spread_30d, 1)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>VRP percentile</td>
                  <td style={{ fontFamily: 'monospace' }}>7d={fivp(context.vrp_pct_7d)} · 30d={fivp(context.vrp_pct_30d)} · 90d={fivp(context.vrp_pct_90d)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>Vol-of-vol</td>
                  <td style={{ fontFamily: 'monospace' }}>iv_change_stdev_7d={f(context.iv_change_stdev_7d, 4)} · vov_ratio={f(context.vov_ratio, 2)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>Expected move 1σ</td>
                  <td style={{ fontFamily: 'monospace' }}>7d=±${fK(context.expected_move_1sigma_7d)} · 14d=±${fK(context.expected_move_1sigma_14d)} · 30d=±${fK(context.expected_move_1sigma_30d)}</td></tr>
              <tr><td style={{ padding: '4px 8px', color: 'var(--text-secondary, #aaa)' }}>Returns</td>
                  <td style={{ fontFamily: 'monospace' }}>1h={fpct(context.spot_ret_1h ? (context.spot_ret_1h as number) * 100 : null, 2)} · 4h={fpct(context.spot_ret_4h ? (context.spot_ret_4h as number) * 100 : null, 2)} · 1d={fpct(context.spot_ret_1d ? (context.spot_ret_1d as number) * 100 : null, 2)}</td></tr>
            </tbody>
          </table>

          {/* Raw expand-all dump */}
          {showAll && (
            <div style={{ marginTop: 12, padding: 8, border: '1px solid var(--border, #333)', borderRadius: 4 }}>
              <div style={{ fontSize: '0.8em', color: 'var(--text-secondary, #888)', marginBottom: 6 }}>
                All {populated.length} populated fields:
              </div>
              <table style={{ width: '100%', fontSize: '0.78em', borderCollapse: 'collapse' }}>
                <tbody>
                  {populated.map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ padding: '2px 8px', color: 'var(--text-secondary, #888)', fontFamily: 'monospace' }}>{k}</td>
                      <td style={{ padding: '2px 8px', fontFamily: 'monospace', textAlign: 'right' }}>
                        {typeof v === 'number' ? Number(v).toPrecision(6) : String(v)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
