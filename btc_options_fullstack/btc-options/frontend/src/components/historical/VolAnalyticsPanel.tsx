import React, { useMemo } from 'react';
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Legend,
} from 'recharts';
import { usePersistedState } from '../../hooks/usePersistedState';
import type { AtmIvPoint, VolAnalyticsResponse, VolGridRow } from '../../types/historical';

// Panel-local timeframe options (drives the lifetime IV/RV mini-chart only).
const TIMEFRAMES = ['5m', '15m', '30m', '1h', '4h', '1d'];

const COL = {
  bg: 'var(--bg1, #080e16)',
  bg2: 'var(--bg2, #0c1420)',
  bg3: 'var(--bg3, #101c2c)',
  border: 'var(--border, #1a2d42)',
  text: 'var(--text, #e2eaf4)',
  text2: 'var(--text2, #7a9bb5)',
  text3: 'var(--text3, #4a6a85)',
  accent: 'var(--accent, #00d4ff)',
  green: 'var(--green, #00e5a0)',
  red: 'var(--red, #ff4d6a)',
  gold: 'var(--gold, #f0b429)',
};

const EST_COLS: { key: keyof VolGridRow; label: string; full: string }[] = [
  { key: 'cc', label: 'CC', full: 'close_to_close' },
  { key: 'co', label: 'CO', full: 'close_open' },
  { key: 'parkinson', label: 'Park', full: 'parkinson' },
  { key: 'gk', label: 'GK', full: 'garman_klass' },
  { key: 'rs', label: 'RS', full: 'rogers_satchell' },
];

function signalColor(level: string): string {
  switch (level) {
    case 'rich': return COL.red;
    case 'mildly_rich': return '#f59e0b';
    case 'fair': return COL.gold;
    case 'mildly_cheap': return '#86efac';
    case 'cheap': return COL.green;
    default: return COL.text3;
  }
}

function ratioColor(r: number | null): string {
  if (r == null || !isFinite(r)) return COL.text3;
  if (r >= 1.5) return COL.red;
  if (r >= 1.3) return '#f59e0b';
  if (r >= 0.9) return COL.gold;
  if (r >= 0.8) return '#86efac';
  return COL.green;
}

const pct = (x: number | null | undefined, d = 1) =>
  x == null || !isFinite(x as number) ? '—' : `${(x as number).toFixed(d)}%`;
const num = (x: number | null | undefined, d = 2) =>
  x == null || !isFinite(x as number) ? '—' : (x as number).toFixed(d);
const usd = (x: number | null | undefined, d = 0) =>
  x == null || !isFinite(x as number) ? '—' : `$${(x as number).toLocaleString(undefined, { maximumFractionDigits: d })}`;
const fmtDte = (h: number | null | undefined) =>
  h == null ? '—' : h >= 24 ? `${(h / 24).toFixed(1)}d` : `${h.toFixed(1)}h`;

interface Props {
  volData: VolAnalyticsResponse | null;
  volLoading: boolean;
  ivSeries: AtmIvPoint[];
  ivLoading: boolean;
  nowTs: number;
  panelTimeframe: string;
  setPanelTimeframe: (tf: string) => void;
}

const Card: React.FC<{ title: string; children: React.ReactNode; style?: React.CSSProperties }> = ({ title, children, style }) => (
  <div style={{ background: COL.bg2, border: `1px solid ${COL.border}`, borderRadius: 6, padding: 10, ...style }}>
    <div style={{ color: COL.text2, fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 8 }}>{title}</div>
    {children}
  </div>
);

const Badge: React.FC<{ color: string; children: React.ReactNode }> = ({ color, children }) => (
  <span style={{ color, border: `1px solid ${color}`, borderRadius: 4, padding: '1px 6px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>{children}</span>
);

export const VolAnalyticsPanel: React.FC<Props> = ({
  volData, volLoading, ivSeries, ivLoading, nowTs, panelTimeframe, setPanelTimeframe,
}) => {
  const [expanded, setExpanded] = usePersistedState<boolean>('historical:volPanelExpanded', false);

  const header = volData?.header;
  const sig = header?.signal;
  const available = volData?.available ?? false;

  const chartData = useMemo(
    () => (ivSeries || []).filter(p => p.atm_iv > 0).map(p => ({ time: p.time, iv: p.atm_iv, rv: p.rv > 0 ? p.rv : null })),
    [ivSeries],
  );

  // Summary chips shown in the collapsed header bar (live, update as you scrub).
  const summary = (
    <span style={{ display: 'inline-flex', gap: 10, alignItems: 'center', fontSize: 12 }}>
      {volLoading && <span style={{ color: COL.text3 }}>…</span>}
      {header && (
        <>
          <span style={{ color: COL.text2 }}>IV <b style={{ color: COL.accent }}>{header.snapped ? '≈' : ''}{pct(header.atm_iv_avg)}</b></span>
          {available && sig ? (
            <>
              <span style={{ color: COL.text2 }}>{(header.regime_label || '').toUpperCase()}</span>
              <span style={{ color: COL.text2 }}>{num(header.primary_ratio)} <Badge color={signalColor(sig.level)}>{sig.level.replace('_', ' ').toUpperCase()}</Badge></span>
            </>
          ) : (
            <span style={{ color: COL.text3 }}>{volData?.reason || 'no data'}</span>
          )}
        </>
      )}
    </span>
  );

  return (
    <div style={{ margin: '4px 12px 12px', border: `1px solid ${COL.border}`, borderRadius: 8, background: COL.bg, overflow: 'hidden' }}>
      {/* Collapsed/clickable header bar */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', cursor: 'pointer', userSelect: 'none', background: COL.bg3 }}
      >
        <span style={{ color: COL.accent, fontSize: 12, width: 12 }}>{expanded ? '▾' : '▸'}</span>
        <span style={{ color: COL.text, fontWeight: 700, fontSize: 13, letterSpacing: 0.3 }}>Vol Analytics</span>
        <span style={{ flex: 1 }}>{summary}</span>
        <span style={{ color: COL.text3, fontSize: 11 }}>{expanded ? 'click to collapse' : 'click to expand'}</span>
      </div>

      {expanded && (
        <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {!available && (
            <div style={{ color: COL.text3, fontSize: 13, padding: '12px 4px' }}>
              {volLoading ? 'Computing…' : (volData?.reason || 'No vol analytics for this expiry at this time.')}
            </div>
          )}

          {available && header && (
            <>
              {/* Header strip */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', background: COL.bg2, border: `1px solid ${COL.border}`, borderRadius: 6, padding: '8px 12px', fontSize: 12 }}>
                <span style={{ color: COL.text2 }}>Spot <b style={{ color: COL.text }}>{usd(header.spot)}</b></span>
                <span style={{ color: COL.text2 }}>ATM <b style={{ color: COL.text }}>{header.atm_strike.toLocaleString()}</b></span>
                <span style={{ color: COL.text2 }}>IV C/P/avg <b style={{ color: COL.accent }}>{pct(header.atm_iv_call)} / {pct(header.atm_iv_put)} / {pct(header.atm_iv_avg)}</b></span>
                <span style={{ color: COL.text2 }}>DTE <b style={{ color: COL.text }}>{fmtDte(header.dte_hours)}</b></span>
                <span style={{ color: COL.text2 }}>IV/RV({header.primary_lookback}d {header.primary_estimator.replace(/_/g, '-')}) <b style={{ color: ratioColor(header.primary_ratio) }}>{num(header.primary_ratio)}</b></span>
                {sig && <Badge color={signalColor(sig.level)}>{sig.level.replace('_', ' ').toUpperCase()}</Badge>}
                <span style={{ color: COL.text2 }}>Regime <b style={{ color: COL.gold }}>{(header.regime_label || '').toUpperCase()}</b></span>
                {header.snapped && header.ts_used > 0 && (
                  <span style={{ color: COL.gold, fontSize: 11 }} title="No data at the exact sim time — showing the nearest available bar">
                    ⓘ as of {new Date(header.ts_used * 1000).toLocaleString()}
                  </span>
                )}
              </div>

              {/* Mini-chart + RV grid row */}
              <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 10 }}>
                <Card title="IV vs RV — contract life">
                  <div style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
                    {TIMEFRAMES.map(tf => (
                      <button
                        key={tf}
                        onClick={() => setPanelTimeframe(tf)}
                        style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 4, cursor: 'pointer',
                          border: `1px solid ${panelTimeframe === tf ? COL.accent : COL.border}`,
                          background: panelTimeframe === tf ? COL.bg3 : 'transparent',
                          color: panelTimeframe === tf ? COL.accent : COL.text2,
                        }}
                      >{tf}</button>
                    ))}
                  </div>
                  <ResponsiveContainer width="100%" height={180}>
                    <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -10 }}>
                      <CartesianGrid stroke={COL.border} strokeDasharray="2 4" />
                      <XAxis
                        dataKey="time" type="number" domain={['dataMin', 'dataMax']} scale="time"
                        tickFormatter={(t) => new Date(t * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                        tick={{ fill: COL.text3, fontSize: 10 }} stroke={COL.border} minTickGap={40}
                      />
                      <YAxis tick={{ fill: COL.text3, fontSize: 10 }} stroke={COL.border} width={38}
                        tickFormatter={(v) => `${v}`} />
                      <Tooltip
                        contentStyle={{ background: COL.bg2, border: `1px solid ${COL.border}`, fontSize: 11 }}
                        labelStyle={{ color: COL.text2 }}
                        labelFormatter={(t) => new Date((t as number) * 1000).toLocaleString()}
                        formatter={(v: any, name: string) => [v == null ? '—' : `${(v as number).toFixed(1)}%`, name.toUpperCase()]}
                      />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Line type="monotone" dataKey="iv" name="ATM IV" stroke={COL.accent} dot={false} strokeWidth={1.6} isAnimationActive={false} connectNulls />
                      <Line type="monotone" dataKey="rv" name="RV" stroke={COL.text2} dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
                      {nowTs > 0 && <ReferenceLine x={nowTs} stroke={COL.gold} strokeDasharray="3 3" label={{ value: 'now', fill: COL.gold, fontSize: 10, position: 'top' }} />}
                    </ComposedChart>
                  </ResponsiveContainer>
                  {ivLoading && <div style={{ color: COL.text3, fontSize: 11 }}>loading series…</div>}
                </Card>

                <Card title="RV term structure (daily)">
                  <GridTable rows={volData!.rv_grid} kind="rv" header={header} />
                </Card>
              </div>

              {/* Ratio grid + regime + term structure */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <Card title="IV / RV ratio grid">
                  <GridTable rows={volData!.ratio_grid} kind="ratio" header={header} />
                </Card>
                <Card title="Regime">
                  {volData!.regime ? (
                    <div style={{ fontSize: 12, color: COL.text2, lineHeight: 1.6 }}>
                      <div>Chop ratio <b style={{ color: COL.text }}>{num(volData!.regime.chop_ratio)}</b> ({volData!.regime.chop_label})</div>
                      <div>Trend intensity <b style={{ color: COL.text }}>{pct(volData!.regime.trend_intensity * 100)}</b> ({volData!.regime.trend_label})</div>
                      <div>Recommended <b style={{ color: COL.gold }}>{volData!.regime.recommended_estimator.replace(/_/g, '-')}</b></div>
                      <div style={{ marginTop: 6, color: COL.text3, fontStyle: 'italic' }}>{volData!.regime.rationale}</div>
                    </div>
                  ) : <span style={{ color: COL.text3 }}>—</span>}
                </Card>
              </div>

              {/* Term structure + Fri-Sat */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <Card title={`Term structure: ${(volData!.term_structure?.shape || 'unknown').toUpperCase()}`}>
                  {volData!.term_structure ? (
                    <div style={{ fontSize: 12, color: COL.text2, lineHeight: 1.6 }}>
                      <div>Short ({header.primary_lookback >= 4 ? '4d' : ''}) <b style={{ color: COL.text }}>{pct(volData!.term_structure.short_rv)}</b> → Long (30d) <b style={{ color: COL.text }}>{pct(volData!.term_structure.long_rv)}</b></div>
                      <div>Diff <b style={{ color: COL.text }}>{pct((volData!.term_structure.diff_pct ?? 0) * 100)}</b></div>
                      <div style={{ marginTop: 6, color: COL.text3, fontStyle: 'italic' }}>{volData!.term_structure.interpretation}</div>
                    </div>
                  ) : <span style={{ color: COL.text3 }}>—</span>}
                </Card>
                <Card title="Fri-Sat weekend window (12wk, σ-fixed)">
                  {volData!.fri_sat ? (
                    <div style={{ fontSize: 12, color: COL.text2, lineHeight: 1.6 }}>
                      <div>Windows <b style={{ color: COL.text }}>{volData!.fri_sat.window_count}</b> · Median move <b style={{ color: COL.text }}>{usd(volData!.fri_sat.median_move_usd)}</b> ({pct((volData!.fri_sat.median_co_pct ?? 0) * 100)})</div>
                      <div>Annualized window σ <b style={{ color: COL.accent }}>{pct(volData!.fri_sat.annualized_co_vol)}</b></div>
                      <div>Window IV/RV <b style={{ color: ratioColor(volData!.fri_sat.window_iv_rv) }}>{num(volData!.fri_sat.window_iv_rv)}</b></div>
                      <div style={{ marginTop: 4, color: COL.text3, fontSize: 11, fontStyle: 'italic' }}>General weekend-gap σ — most actionable for weekend/short-expiry trades.</div>
                    </div>
                  ) : <span style={{ color: COL.text3 }}>No complete weekend windows in range.</span>}
                </Card>
              </div>

              {/* Gamma-Theta + SL */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <Card title="Gamma vs Theta">
                  {volData!.gamma_theta ? (
                    <div style={{ fontSize: 12, color: COL.text2, lineHeight: 1.6 }}>
                      <div style={{ marginBottom: 4 }}>
                        <Badge color={volData!.gamma_theta.ratio > 1 ? COL.green : COL.red}>{volData!.gamma_theta.verdict}</Badge>
                      </div>
                      <div>γ-PnL/day (1σ) <b style={{ color: COL.green }}>{usd(volData!.gamma_theta.gamma_pnl_per_day_1sd, 2)}</b> vs θ/day <b style={{ color: COL.red }}>{usd(volData!.gamma_theta.theta_pnl_per_day, 2)}</b></div>
                      <div>Ratio <b style={{ color: COL.text }}>{num(volData!.gamma_theta.ratio)}</b> · using RV {pct(volData!.gamma_theta.sigma_realized)}</div>
                      <div>Break-even move <b style={{ color: COL.text }}>{usd(volData!.gamma_theta.breakeven_move_per_day)}/day</b></div>
                    </div>
                  ) : <span style={{ color: COL.text3 }}>—</span>}
                </Card>
                <Card title="Stop-loss candidates (12h hold)">
                  {volData!.sl ? (
                    <div style={{ fontSize: 12, color: COL.text2, lineHeight: 1.7 }}>
                      <div>Tight <b style={{ color: COL.text }}>{usd(volData!.sl.tight.dollars)}</b> ({pct(volData!.sl.tight.pct * 100)})</div>
                      <div>Moderate <b style={{ color: COL.text }}>{usd(volData!.sl.moderate.dollars)}</b> ({pct(volData!.sl.moderate.pct * 100)})</div>
                      <div>Conservative <b style={{ color: COL.text }}>{usd(volData!.sl.conservative.dollars)}</b> ({pct(volData!.sl.conservative.pct * 100)})</div>
                      <div style={{ marginTop: 4, color: COL.text3 }}>1σ move <b style={{ color: COL.text2 }}>{usd(volData!.sl.one_sigma_dollars)}</b> · min-reasonable <b style={{ color: COL.text2 }}>{usd(volData!.sl.min_reasonable_sl_dollars)}</b></div>
                    </div>
                  ) : <span style={{ color: COL.text3 }}>—</span>}
                </Card>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

const GridTable: React.FC<{ rows: VolGridRow[]; kind: 'rv' | 'ratio'; header: VolAnalyticsResponse['header'] }> = ({ rows, kind, header }) => {
  const recKey = EST_COLS.find(c => c.full === header.primary_estimator)?.key;
  const cell: React.CSSProperties = { padding: '3px 6px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' };
  const th: React.CSSProperties = { ...cell, color: COL.text3, fontWeight: 600, fontSize: 11 };
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
        <tr>
          <th style={{ ...th, textAlign: 'left' }}>lkbk</th>
          {EST_COLS.map(c => <th key={c.key} style={th}>{c.label}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map(row => {
          const isPrimaryRow = row.lookback === header.primary_lookback;
          return (
            <tr key={row.lookback} style={{ background: isPrimaryRow ? COL.bg3 : 'transparent' }}>
              <td style={{ ...cell, textAlign: 'left', color: COL.text2 }}>{row.lookback}d</td>
              {EST_COLS.map(c => {
                const v = row[c.key] as number | null;
                const isRec = isPrimaryRow && c.key === recKey;
                const color = kind === 'ratio' ? ratioColor(v) : COL.text;
                return (
                  <td key={c.key} style={{ ...cell, color, fontWeight: isRec ? 700 : 400, outline: isRec ? `1px solid ${COL.accent}` : 'none' }}>
                    {v == null ? '—' : kind === 'rv' ? v.toFixed(0) : v.toFixed(2)}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};
