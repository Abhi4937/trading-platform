/**
 * StrangleAnalyticsPanel — single shared display for the §10 trade card layout.
 *
 * Mounted in:
 *   - HistoricalDashboard's StrategyPanel (build mode)
 *   - BacktestDashboard's per-trade detail
 *
 * All math runs through `computeAll()` from utils/strangleAnalytics.ts so the
 * two dashboards always show identical numbers.
 */

import { useMemo, useState } from 'react';
import {
  computeAll,
  isStrangleLikeLegs,
  type AnalyticsContext,
  type AnalyticsLeg,
  type CalibrationBucket,
  type FullAnalytics,
  type QualityBand,
} from '../../utils/strangleAnalytics';
import MarketContextPanel from './MarketContextPanel';

interface Props {
  legs: AnalyticsLeg[];
  ctx: AnalyticsContext | null;
  calibration: CalibrationBucket | null | undefined;
  loading?: boolean;
  /** Title shown in the panel header. */
  title?: string;
  /** Wide M1+M2+M3 snapshot at entry. Strategy Builder fetches via
   * /historical/snapshot-context; Backtest receives via trade.market_context.
   * When provided, mounts the MarketContextPanel section at the bottom. */
  marketContext?: Record<string, number | string | null> | null;
}

const fmtPct = (v: number, d = 2) =>
  Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '–';
const fmtNum = (v: number, d = 2) =>
  Number.isFinite(v) ? v.toFixed(d) : '–';
const fmtUsd = (v: number, d = 0) =>
  Number.isFinite(v)
    ? `$${v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`
    : '–';

// Color tags from spec §8 thresholds
const ratioColor = (
  value: number, goodAbove?: number, goodBelow?: number, badAbove?: number, badBelow?: number,
): string => {
  if (!Number.isFinite(value)) return 'var(--text-muted, #888)';
  if (goodAbove !== undefined && value >= goodAbove) return 'var(--green, #10b981)';
  if (goodBelow !== undefined && value <= goodBelow) return 'var(--green, #10b981)';
  if (badAbove !== undefined && value >= badAbove) return 'var(--red, #ef4444)';
  if (badBelow !== undefined && value <= badBelow) return 'var(--red, #ef4444)';
  return 'var(--gold, #f0b429)';
};

const bandColor = (band: QualityBand): string => {
  if (band === 'strong') return 'var(--green, #10b981)';
  if (band === 'standard') return 'var(--gold, #f0b429)';
  if (band === 'marginal') return 'var(--gold-faded, #d97706)';
  return 'var(--red, #ef4444)';
};

const patternColor = (p: string): string => {
  switch (p) {
    case 'A': return '#3b82f6';   // blue (Fresh Spike)
    case 'B': return '#ef4444';   // red (Post-Crash)
    case 'C': return '#6b7280';   // grey (Stale)
    case 'D': return '#f0b429';   // gold (Active Trend)
    default:  return '#475569';   // slate (Other)
  }
};

const patternLabel = (p: string): string => ({
  A: 'A — Fresh Spike',
  B: 'B — Post-Crash',
  C: 'C — Stale',
  D: 'D — Active Trend',
} as Record<string, string>)[p] ?? `${p} — Other`;

// ── Sub-components ──────────────────────────────────────────────────────────

const HeaderRow: React.FC<{ ctx: AnalyticsContext }> = ({ ctx }) => (
  <div style={{
    display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8,
    padding: '8px 12px', background: 'var(--surface-2, #1f2937)',
    borderRadius: 6, marginBottom: 10,
  }}>
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>Spot</div>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{fmtUsd(ctx.spot)}</div>
    </div>
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>DTE</div>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{fmtNum(ctx.dte, 1)}</div>
    </div>
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>IVP 7d</div>
      <div style={{ fontSize: 13, fontWeight: 600 }}>
        {Number.isFinite(ctx.ivp_atm_7d_90d) ? `${ctx.ivp_atm_7d_90d.toFixed(0)}` : '–'}
      </div>
    </div>
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>ATM IV 7d</div>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{fmtPct(ctx.atm_iv_7d, 1)}</div>
    </div>
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>Pattern</div>
      <span style={{
        display: 'inline-block', padding: '2px 8px', fontSize: 11, fontWeight: 600,
        borderRadius: 4, background: patternColor(ctx.pattern), color: 'white',
      }}>{patternLabel(ctx.pattern)}</span>
    </div>
  </div>
);

const HardFiltersRow: React.FC<{ a: FullAnalytics }> = ({ a }) => {
  const f = a.filters;
  const Tick: React.FC<{ ok: boolean; label: string }> = ({ ok, label }) => (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
      color: ok ? 'var(--green, #10b981)' : 'var(--red, #ef4444)',
    }}>
      <span style={{ fontSize: 14 }}>{ok ? '✓' : '✗'}</span>
      <span style={{ color: 'var(--text-secondary, #cbd5e1)' }}>{label}</span>
    </div>
  );
  return (
    <div style={{
      display: 'flex', gap: 16, padding: '8px 12px', flexWrap: 'wrap',
      background: 'var(--surface-2, #1f2937)', borderRadius: 6, marginBottom: 10,
    }}>
      <Tick ok={f.ivp_above_50} label="IVP_4h > 50" />
      <Tick ok={f.iv_rv_spread_pos} label="IV − RV > 0" />
      <Tick ok={f.adx_below_30} label="ADX_4h < 30" />
      <Tick ok={f.dte_in_range} label="DTE 5–14" />
      <Tick ok={f.gex_not_extreme} label="GEX not at flip" />
      <span style={{
        marginLeft: 'auto', fontSize: 11, fontWeight: 600,
        color: f.all_pass ? 'var(--green, #10b981)' : 'var(--gold, #f0b429)',
      }}>
        {f.all_pass ? 'ALL PASS' : 'CHECK'}
      </span>
    </div>
  );
};

const PremiumDecomposition: React.FC<{ a: FullAnalytics; spot: number }> = ({ a, spot }) => {
  if (!a.decomposition) {
    return (
      <div style={{ padding: 12, fontSize: 12, color: 'var(--text-muted, #94a3b8)' }}>
        Calibration not loaded — decomposition unavailable.
      </div>
    );
  }
  const d = a.decomposition;
  const totalPct = a.credit.credit_pct;
  const totalUsd = a.credit.total_credit_usd;
  // Normalize bar widths to magnitudes (handle negative excess)
  const sUsd = Math.abs(d.structural_credit_usd);
  const iUsd = Math.abs(d.iv_regime_premium_usd);
  const eUsd = Math.abs(d.excess_over_fair_usd);
  const total = sUsd + iUsd + eUsd || 1;
  const sW = (sUsd / total) * 100;
  const iW = (iUsd / total) * 100;
  const eW = (eUsd / total) * 100;
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                    fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: 'var(--text-muted, #94a3b8)' }}>Premium received</span>
        <span style={{ fontSize: 14, fontWeight: 700 }}>
          {fmtUsd(totalUsd, 2)} ({fmtPct(totalPct)})
        </span>
      </div>
      <div style={{ display: 'flex', height: 22, borderRadius: 4, overflow: 'hidden',
                    background: 'var(--surface-2, #1f2937)' }}>
        <div style={{
          width: `${sW}%`, background: '#3b82f6',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, color: 'white', fontWeight: 600,
        }} title={`Structural: ${fmtUsd(d.structural_credit_usd)}`}>
          {sW > 12 ? `S ${(d.pct_from_structural * 100).toFixed(0)}%` : ''}
        </div>
        <div style={{
          width: `${iW}%`, background: '#f0b429',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, color: 'white', fontWeight: 600,
        }} title={`IV regime uplift: ${fmtUsd(d.iv_regime_premium_usd)}`}>
          {iW > 12 ? `IV ${(d.pct_from_iv_regime * 100).toFixed(0)}%` : ''}
        </div>
        <div style={{
          width: `${eW}%`,
          background: d.excess_over_fair_pct >= 0 ? '#10b981' : '#ef4444',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, color: 'white', fontWeight: 600,
        }} title={`Excess vs fair: ${fmtUsd(d.excess_over_fair_usd)}`}>
          {eW > 12 ? `E ${(d.pct_from_excess * 100).toFixed(0)}%` : ''}
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8,
                    marginTop: 6, fontSize: 11 }}>
        <div>
          <span style={{ color: '#3b82f6' }}>● Structural</span>
          <div>{fmtUsd(d.structural_credit_usd)} · {fmtPct(d.structural_credit_pct, 3)}</div>
        </div>
        <div>
          <span style={{ color: '#f0b429' }}>● IV regime</span>
          <div>{fmtUsd(d.iv_regime_premium_usd)} · {fmtPct(d.iv_regime_premium_pct, 3)}</div>
        </div>
        <div>
          <span style={{ color: d.excess_over_fair_pct >= 0 ? '#10b981' : '#ef4444' }}>● Excess</span>
          <div>{fmtUsd(d.excess_over_fair_usd)} · {fmtPct(d.excess_over_fair_pct, 3)}</div>
        </div>
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-muted, #94a3b8)', marginTop: 4 }}>
        Fair (at IVP) = {fmtPct(d.fair_credit_at_ivp_pct, 3)} · Spot ref = {fmtUsd(spot)}
      </div>
    </div>
  );
};

const RichnessBars: React.FC<{ a: FullAnalytics; ctx: AnalyticsContext }> = ({ a, ctx }) => {
  if (!a.z || !a.quality) return null;
  const items = [
    { label: 'vs all entries (z%)', value: a.z.z_credit_pct_pct, color: '#60a5fa' },
    { label: 'IVP percentile', value: ctx.ivp_atm_7d_90d, color: '#a78bfa' },
    { label: 'IV z%', value: a.z.z_atm_iv_pct, color: '#34d399' },
    { label: 'Quality', value: a.quality.quality_score, color: bandColor(a.quality.size_band) },
  ];
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', marginBottom: 6 }}>
        Richness (0 = poor, 100 = strong)
      </div>
      {items.map((it, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '120px 1fr 50px',
                              alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: 11 }}>{it.label}</span>
          <div style={{ height: 10, background: 'var(--surface-2, #1f2937)',
                        borderRadius: 4, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${Math.max(0, Math.min(100, it.value))}%`,
                          background: it.color }} />
          </div>
          <span style={{ fontSize: 11, fontWeight: 600, textAlign: 'right' }}>
            {fmtNum(it.value, 0)}
          </span>
        </div>
      ))}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 4 }}>
        <span style={{
          padding: '2px 10px', fontSize: 11, fontWeight: 700, borderRadius: 4,
          background: bandColor(a.quality.size_band), color: 'white',
        }}>
          {a.quality.size_band.toUpperCase()}
        </span>
      </div>
    </div>
  );
};

const RatioRow: React.FC<{
  label: string; value: number; format?: 'num' | 'pct' | 'usd' | 'ratio';
  decimals?: number;
  goodAbove?: number; goodBelow?: number; badAbove?: number; badBelow?: number;
}> = ({ label, value, format = 'num', decimals = 2,
       goodAbove, goodBelow, badAbove, badBelow }) => {
  const color = ratioColor(value, goodAbove, goodBelow, badAbove, badBelow);
  let text: string;
  if (format === 'pct') text = fmtPct(value, decimals);
  else if (format === 'usd') text = fmtUsd(value, decimals);
  else text = fmtNum(value, decimals);
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between',
                  fontSize: 11, padding: '3px 0',
                  borderBottom: '1px dashed rgba(148, 163, 184, 0.15)' }}>
      <span style={{ color: 'var(--text-muted, #94a3b8)' }}>{label}</span>
      <span style={{ color, fontWeight: 600 }}>{text}</span>
    </div>
  );
};

const RatioGroup: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div style={{ marginBottom: 8 }}>
    <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                  letterSpacing: 0.5, color: 'var(--text-muted, #94a3b8)',
                  marginBottom: 4, marginTop: 6 }}>
      {title}
    </div>
    {children}
  </div>
);

const MasterRatioTable: React.FC<{ a: FullAnalytics; ctx: AnalyticsContext }> = ({ a, ctx }) => {
  const r = a.ratios;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)',
                  gap: 12, marginBottom: 14 }}>
      <div>
        <RatioGroup title="Vol">
          <RatioRow label="IV / RV (7d)" value={r.iv_rv_ratio_7d}
                    goodAbove={1.15} badBelow={1.0} decimals={2} />
          <RatioRow label="Term ratio (7/30)" value={r.term_ratio_7_30} decimals={2} />
          <RatioRow label="Skew (CE/PE IV)" value={r.skew_ratio_call_put} decimals={2} />
          <RatioRow label="Wing/ATM" value={r.wing_atm_ratio} decimals={2} />
        </RatioGroup>
        <RatioGroup title="Premium / risk">
          <RatioRow label="Credit %" value={r.credit_pct} format="pct" decimals={3} />
          <RatioRow label="Annualized %" value={r.annualized_credit_pct} format="pct" decimals={1} />
          <RatioRow label="Credit / daily theta" value={r.credit_per_daily_theta} decimals={2} />
        </RatioGroup>
        <RatioGroup title="Distance">
          <RatioRow label="CE / σ" value={r.call_strike_sigma_dist} decimals={2} />
          <RatioRow label="PE / σ" value={r.put_strike_sigma_dist} decimals={2} />
          <RatioRow label="CE / ATR" value={r.call_strike_atr_dist} decimals={2} />
          <RatioRow label="PE / ATR" value={r.put_strike_atr_dist} decimals={2} />
          <RatioRow label="Touch prob CE" value={r.touch_prob_call} format="pct" decimals={1} />
          <RatioRow label="Touch prob PE" value={r.touch_prob_put} format="pct" decimals={1} />
        </RatioGroup>
      </div>
      <div>
        <RatioGroup title="Greeks">
          <RatioRow label="Theta / Vega" value={r.theta_vega_ratio}
                    goodAbove={1.0} badBelow={0.6} decimals={2} />
          <RatioRow label="Gamma / Theta ($)" value={r.gamma_theta_dollar}
                    goodBelow={1.5} badAbove={2.5} decimals={2} />
          <RatioRow label="Vega / credit" value={r.vega_credit_ratio} decimals={3} />
          <RatioRow label="Theta / credit" value={r.theta_credit_ratio} decimals={3} />
          <RatioRow label="Delta / credit" value={r.delta_credit_ratio} decimals={3} />
          <RatioRow label="Dollar gamma" value={r.dollar_gamma} format="usd" decimals={2} />
        </RatioGroup>
        <RatioGroup title="Position-level Greeks">
          <RatioRow label="Position Δ" value={r.position_delta} decimals={3} />
          <RatioRow label="Position Γ" value={r.position_gamma} decimals={5} />
          <RatioRow label="Position Vega" value={r.position_vega} decimals={2} />
          <RatioRow label="Position Θ" value={r.position_theta} decimals={2} />
        </RatioGroup>
        <RatioGroup title="Regime">
          <RatioRow label="RVP / IVP" value={r.rvp_ivp_ratio}
                    badBelow={0.7} decimals={2} />
          <RatioRow label="ATR % (4h)" value={r.atr_compression} format="pct" decimals={2} />
          <RatioRow label="ADX (4h)" value={ctx.adx_14_4h} decimals={1} />
          <RatioRow label="VRP %ile (7d)" value={ctx.vrp_pct_7d} decimals={0} />
        </RatioGroup>
      </div>
    </div>
  );
};

const VolContextTable: React.FC<{ ctx: AnalyticsContext }> = ({ ctx }) => (
  <div style={{ marginBottom: 14 }}>
    <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                  letterSpacing: 0.5, color: 'var(--text-muted, #94a3b8)', marginBottom: 6 }}>
      Vol context
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: 6, fontSize: 11 }}>
      <div><span style={{ color: 'var(--text-muted)' }}>ATM IV 7d</span><br />
        <b>{fmtPct(ctx.atm_iv_7d, 1)}</b></div>
      <div><span style={{ color: 'var(--text-muted)' }}>ATM IV 14d</span><br />
        <b>{fmtPct(ctx.atm_iv_14d, 1)}</b></div>
      <div><span style={{ color: 'var(--text-muted)' }}>ATM IV 30d</span><br />
        <b>{fmtPct(ctx.atm_iv_30d, 1)}</b></div>
      <div><span style={{ color: 'var(--text-muted)' }}>Term slope 7→30</span><br />
        <b>{fmtNum(ctx.term_slope_7_30, 3)}</b></div>
      <div><span style={{ color: 'var(--text-muted)' }}>RR 25Δ</span><br />
        <b>{fmtNum(ctx.risk_reversal_25d, 3)}</b></div>
      <div><span style={{ color: 'var(--text-muted)' }}>Butterfly 25Δ</span><br />
        <b>{fmtNum(ctx.butterfly_25d, 3)}</b></div>
      <div><span style={{ color: 'var(--text-muted)' }}>RV 7d</span><br />
        <b>{Number.isFinite(ctx.rv_7d) ? `${ctx.rv_7d.toFixed(1)}%` : '–'}</b></div>
      <div><span style={{ color: 'var(--text-muted)' }}>Exp move ±1σ 7d</span><br />
        <b>{fmtUsd(ctx.expected_move_1sigma_7d)}</b></div>
    </div>
  </div>
);

const Skeleton: React.FC<{ msg: string }> = ({ msg }) => (
  <div style={{ padding: 24, textAlign: 'center', fontSize: 13,
                color: 'var(--text-muted, #94a3b8)' }}>
    {msg}
  </div>
);

// ── Main panel ───────────────────────────────────────────────────────────────

export const StrangleAnalyticsPanel: React.FC<Props> = ({
  legs, ctx, calibration, loading, title = 'Strangle Analytics',
  marketContext,
}) => {
  const [collapsed, setCollapsed] = useState(false);

  const isStrangle = useMemo(() => isStrangleLikeLegs(legs), [legs]);

  const analytics = useMemo<FullAnalytics | null>(() => {
    if (!ctx || !isStrangle) return null;
    return computeAll(legs, ctx, calibration);
  }, [legs, ctx, calibration, isStrangle]);

  return (
    <div style={{
      background: 'var(--surface, #0f172a)', border: '1px solid var(--border, #1e293b)',
      borderRadius: 8, padding: 12, margin: '12px 0',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    marginBottom: 10, cursor: 'pointer' }}
           onClick={() => setCollapsed(c => !c)}>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary, #f1f5f9)' }}>
          {title}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>
          {collapsed ? '▸ expand' : '▾ collapse'}
        </span>
      </div>
      {!collapsed && (
        <div>
          {loading && <Skeleton msg="Loading context & calibration…" />}
          {!loading && !isStrangle && (
            <Skeleton msg="Build a strangle (1 CE + 1 PE on the same side) to see analytics." />
          )}
          {!loading && isStrangle && !ctx && (
            <Skeleton msg="No M3 snapshot for this timestamp — verify the enriched table covers this date." />
          )}
          {!loading && isStrangle && ctx && analytics && (
            <>
              <HeaderRow ctx={ctx} />
              <HardFiltersRow a={analytics} />
              <PremiumDecomposition a={analytics} spot={ctx.spot} />
              <RichnessBars a={analytics} ctx={ctx} />
              <MasterRatioTable a={analytics} ctx={ctx} />
              <VolContextTable ctx={ctx} />
              {!calibration && (
                <div style={{ fontSize: 11, color: 'var(--gold, #f0b429)',
                              padding: 8, background: 'rgba(240, 180, 41, 0.1)',
                              borderRadius: 4 }}>
                  Calibration unavailable — decomposition / z-scores hidden;
                  Quality column uses an IVP+credit fallback formula until
                  calibration runs. Build with
                  <code style={{ marginLeft: 4 }}>python -m app.analytics.calibration_builder --rebuild</code>.
                </div>
              )}
              {marketContext && (
                <MarketContextPanel context={marketContext} defaultOpen={false} />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};
