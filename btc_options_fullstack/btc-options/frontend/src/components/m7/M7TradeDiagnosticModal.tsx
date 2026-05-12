// Per-losing-trade diagnostic modal — opens from the Losses Explorer.
// 7 tabs (Overview / Per-leg / Vol / Skew / Spot / Greeks / Path) covering
// every indicator + Greek + ratio at entry, plus path-derived during-trade
// stats and hypothesis flags so the user can diagnose IV-driven vs
// theta-driven vs gamma-driven vs directional losses.

import React, { useEffect, useState } from 'react';
import { InfoIcon } from './InfoIcon';
import {
  fetchM7TradeDiagnostic,
  type M7TradeDiagnosticResponse,
  type M7TradeHypothesis,
} from '../../services/m7_api';
import type { M7ExitRule } from '../../types/m7';
import { M7TradePathChart } from './M7TradePathChart';

interface Props {
  tradeId: string;
  exitRule?: M7ExitRule;
  onClose: () => void;
}

type Tab = 'overview' | 'per_leg' | 'vol' | 'skew' | 'spot' | 'greeks' | 'path';

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'per_leg',  label: 'Per-leg' },
  { id: 'vol',      label: 'Vol regime' },
  { id: 'skew',     label: 'Skew' },
  { id: 'spot',     label: 'Spot regime' },
  { id: 'greeks',   label: 'Greeks & ratios' },
  { id: 'path',     label: 'Path' },
];

const HYPOTHESIS_COLOR: Record<string, string> = {
  iv_driven:      '#d29922',
  directional:    '#f85149',
  gamma_squeezed: '#ff7b72',
  path_dependent: '#bf6fde',
  skew_flipped:   '#79c0ff',
};

function fmt(v: number | null | undefined, digits = 2, suffix = ''): string {
  if (v == null) return '—';
  return `${v.toFixed(digits)}${suffix}`;
}
function fmtUsd(v: number | null | undefined): string {
  if (v == null) return '—';
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 10_000)    return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}
function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

export function M7TradeDiagnosticModal({ tradeId, exitRule, onClose }: Props) {
  const [data, setData] = useState<M7TradeDiagnosticResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('overview');

  useEffect(() => {
    const ac = new AbortController();
    setErr(null); setData(null);
    fetchM7TradeDiagnostic(tradeId, exitRule, ac.signal)
      .then(setData)
      .catch(e => { if ((e as Error).name !== 'AbortError') setErr(String(e)); });
    return () => ac.abort();
  }, [tradeId, JSON.stringify(exitRule)]);

  // ESC closes modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const isLoss = data && data.pnl.is_win === false;

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={shellStyle} onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start',
                      gap: 12, padding: '12px 16px',
                      borderBottom: '1px solid #1a2d42' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11, color: '#7a9bb5',
                          textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Trade Diagnostic
            </div>
            <div style={{ fontSize: 14, color: '#cfd9e3', fontWeight: 700,
                          fontFamily: 'ui-monospace, monospace',
                          marginTop: 2 }}>
              {tradeId}
            </div>
            {data && (
              <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap',
                            gap: 6, fontSize: 11, color: '#cfd9e3' }}>
                <Chip>{data.identity.friday_date_ist ?? '—'}</Chip>
                <Chip>{data.identity.entry_atm_iv_band ?? '—'}</Chip>
                <Chip>{data.identity.entry_hour_ist}:00 IST</Chip>
                <Chip>{data.identity.expiry_bucket ?? '—'}</Chip>
                <Chip>Δ={fmt(data.identity.delta_target, 2)}</Chip>
                <Chip>{data.identity.exit_reason ?? '—'}</Chip>
                {data.identity.loss_cause && (
                  <Chip color={HYPOTHESIS_COLOR[
                    data.identity.loss_cause === 'vol_expansion' ? 'iv_driven' :
                    data.identity.loss_cause === 'gamma_squeeze' ? 'gamma_squeezed' :
                    data.identity.loss_cause === 'skew_flip'     ? 'skew_flipped' :
                    data.identity.loss_cause]}>
                    cause: {data.identity.loss_cause}
                  </Chip>
                )}
                {data.hypotheses.filter(h => h.fired).map(h => (
                  <Chip key={h.flag} color={HYPOTHESIS_COLOR[h.flag] || '#7a9bb5'}>
                    {h.flag}
                  </Chip>
                ))}
              </div>
            )}
          </div>
          {data && (
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 9, color: '#7a9bb5',
                            textTransform: 'uppercase', letterSpacing: 0.5 }}>
                Net P&L
              </div>
              <div style={{ fontSize: 20, fontWeight: 700,
                            color: isLoss ? '#f85149' : '#3fb950',
                            fontVariantNumeric: 'tabular-nums', marginTop: 2 }}>
                {fmtUsd(data.pnl.net_pnl_estimate_usd)}
              </div>
            </div>
          )}
          <button onClick={onClose} style={closeBtn} title="Close (Esc)">×</button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', flexWrap: 'wrap',
                      borderBottom: '1px solid #1a2d42', padding: '0 8px' }}>
          {TABS.map(t => (
            <button key={t.id}
                    onClick={() => setTab(t.id)}
                    style={{
                      ...tabBtn,
                      color: tab === t.id ? '#cfd9e3' : '#7a9bb5',
                      borderBottom: tab === t.id ? '2px solid #1f6feb' : '2px solid transparent',
                    }}>
              {t.label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div style={{ padding: 14, overflowY: 'auto', flex: 1 }}>
          {!data && !err && <div style={{ color: '#7a9bb5', fontSize: 11 }}>Loading…</div>}
          {err && <div style={{ color: '#f85149', fontSize: 11 }}>{err}</div>}

          {data && tab === 'overview'  && <OverviewTab d={data} />}
          {data && tab === 'per_leg'   && <PerLegTab d={data} />}
          {data && tab === 'vol'       && <VolTab d={data} />}
          {data && tab === 'skew'      && <SkewTab d={data} />}
          {data && tab === 'spot'      && <SpotTab d={data} />}
          {data && tab === 'greeks'    && <GreeksTab d={data} />}
          {data && tab === 'path'      && (
            <div style={{ marginTop: -12 }}>
              {/* M7TradePathChart is its own modal; we render it inline by
                  passing a noop onClose. Keeps the chart canvas inside this
                  tab pane. */}
              <PathTabEmbed tradeId={tradeId} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Tab body components ──────────────────────────────────────────────────────

function OverviewTab({ d }: { d: M7TradeDiagnosticResponse }) {
  return (
    <>
      <SectionTitle>P&amp;L</SectionTitle>
      <Grid>
        <KV label="Net P&L"      value={fmtUsd(d.pnl.net_pnl_estimate_usd)} />
        <KV label="Gross P&L"    value={fmtUsd(d.pnl.gross_pnl_usd)} />
        <KV label="Credit"       value={fmtUsd(d.pnl.credit_usd)} />
        <KV label="Margin"       value={fmtUsd(d.pnl.margin_used_usd_at_entry)} />
        <KV label="Max MTM"      value={fmtUsd(d.pnl.max_mtm_usd)} />
        <KV label="Min MTM"      value={fmtUsd(d.pnl.min_mtm_usd)} />
        <KV label="Exit MTM"     value={fmtUsd(d.pnl.exit_mtm_usd)} />
        <KV label="Duration"     value={d.identity.duration_minutes != null
                                          ? `${(d.identity.duration_minutes/60).toFixed(1)}h`
                                          : '—'} />
        <KV label="rel time @ peak"   value={fmt(d.pnl.rel_time_max_mtm, 2)} />
        <KV label="rel time @ trough" value={fmt(d.pnl.rel_time_min_mtm, 2)} />
        <KV label="% return / credit" value={fmtPct(d.pnl.pct_return_on_credit)} />
        <KV label="% return / margin" value={fmtPct(d.pnl.pct_return_on_margin)} />
      </Grid>

      <SectionTitle>Hypotheses</SectionTitle>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {d.hypotheses.map(h => <HypothesisRow key={h.flag} h={h} />)}
      </div>
    </>
  );
}

function PerLegTab({ d }: { d: M7TradeDiagnosticResponse }) {
  return (
    <>
      <SectionTitle>Per-leg</SectionTitle>
      <div style={{ display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                    gap: 12 }}>
        <LegCard label="CALL" leg={d.per_leg.call} />
        <LegCard label="PUT"  leg={d.per_leg.put} />
      </div>

      <SectionTitle>Skew (entry)</SectionTitle>
      <Grid>
        <KV label="Δ skew"          value={fmt(d.per_leg.skew.delta_skew, 4)} />
        <KV label="IV skew %"        value={fmtPct(d.per_leg.skew.iv_skew_pct)} />
        <KV label="Premium skew $"   value={fmtUsd(d.per_leg.skew.premium_skew_usd)} />
        <KV label="Premium skew %"   value={fmtPct(d.per_leg.skew.premium_skew_pct)} />
        <KV label="IV bucket"        value={d.per_leg.skew.iv_skew_bucket ?? '—'} />
        <KV label="Δ bucket"         value={d.per_leg.skew.delta_skew_bucket ?? '—'} />
        <KV label="Premium bucket"   value={d.per_leg.skew.premium_skew_bucket ?? '—'} />
        <KV label="Leg P&L diff"     value={fmtUsd(d.pnl.leg_pnl_diff_usd)} />
      </Grid>
    </>
  );
}

function VolTab({ d }: { d: M7TradeDiagnosticResponse }) {
  const v = d.vol_regime;
  return (
    <>
      <SectionTitle>ATM IV term structure (decimal)</SectionTitle>
      <Grid>
        <KV label="ATM IV (entry)" value={fmt(v.entry_atm_iv as number, 4)} />
        <KV label="ATM IV 7d"  value={fmt(v.ctx_atm_iv_7d as number, 4)} />
        <KV label="ATM IV 14d" value={fmt(v.ctx_atm_iv_14d as number, 4)} />
        <KV label="ATM IV 30d" value={fmt(v.ctx_atm_iv_30d as number, 4)} />
        <KV label="ATM IV 60d" value={fmt(v.ctx_atm_iv_60d as number, 4)} />
      </Grid>

      <SectionTitle>IVP (in 90-day percentile)</SectionTitle>
      <Grid>
        <KV label="IVP 4h"     value={fmt(v.ctx_ivp_4h as number, 1)} />
        <KV label="IVP 7d/90"  value={fmt(v.ctx_ivp_atm_7d_90d as number, 1)} />
        <KV label="IVP 14d/90" value={fmt(v.ctx_ivp_atm_14d_90d as number, 1)} />
        <KV label="IVP 30d/90" value={fmt(v.ctx_ivp_atm_30d_90d as number, 1)} />
        <KV label="ΔIVP 4h vs 24h" value={fmt(v.ivp_4h_delta_24h as number, 2)} />
        <KV label="ΔIVP 4h vs 48h" value={fmt(v.ivp_4h_delta_48h as number, 2)} />
        <KV label="iv_change σ 7d" value={fmt(v.iv_change_stdev_7d as number, 4)} />
        <KV label="vov_ratio"    value={fmt(v.vov_ratio as number, 4)} />
      </Grid>

      <SectionTitle>Realized vol &amp; spread</SectionTitle>
      <Grid>
        <KV label="RV 7d"  value={fmt(v.ctx_rv_7d as number, 1)} />
        <KV label="RV 14d" value={fmt(v.ctx_rv_14d as number, 1)} />
        <KV label="RV 30d" value={fmt(v.ctx_rv_30d as number, 1)} />
        <KV label="IV-RV spread 7d"  value={fmt(v.ctx_iv_rv_spread_7d as number, 4)} />
        <KV label="IV-RV spread 30d" value={fmt(v.ctx_iv_rv_spread_30d as number, 4)} />
        <KV label="IV/RV ratio 7d"   value={fmt(v.ctx_iv_rv_ratio_7d as number, 3)} />
        <KV label="VRP % 7d"   value={fmtPct(v.ctx_vrp_pct_7d as number)} />
        <KV label="RVP 4h"     value={fmt(v.ctx_rvp_4h as number, 1)} />
      </Grid>

      <SectionTitle>During trade</SectionTitle>
      <Grid>
        <KV label="ATM IV @ trough"   value={fmt(v.atm_iv_at_min_mtm as number, 4)} />
        <KV label="Min ATM IV in window" value={fmt(v.min_atm_iv_in_window as number, 4)} />
        <KV label="Max ATM IV in window" value={fmt(v.max_atm_iv_in_window as number, 4)} />
        <KV label="IV jump %" value={fmtPct(v.iv_jump_pct as number)}
            color={(v.iv_jump_pct as number) > 0.10 ? '#d29922' : undefined} />
      </Grid>
    </>
  );
}

function SkewTab({ d }: { d: M7TradeDiagnosticResponse }) {
  const s = d.skew_smile;
  return (
    <>
      <SectionTitle>Skew &amp; smile (entry context)</SectionTitle>
      <Grid>
        <KV label="R_25 (risk reversal)"
            value={fmt(s.ctx_risk_reversal_25d as number, 4)}
            color={
              (s.ctx_risk_reversal_25d as number) > 0 ? '#3fb950' :
              (s.ctx_risk_reversal_25d as number) < 0 ? '#f85149' : undefined
            } />
        <KV label="BF_25 (butterfly)"     value={fmt(s.ctx_butterfly_25d as number, 4)} />
        <KV label="Wing/ATM ratio"        value={fmt(s.ctx_wing_atm_ratio as number, 3)} />
        <KV label="Term slope 7→30"       value={fmt(s.ctx_term_slope_7_30 as number, 4)} />
      </Grid>
      <div style={{ fontSize: 10, color: '#5b7894', marginTop: 6,
                    fontStyle: 'italic' }}>
        R_25 &gt; 0 = call premium &gt; put premium (call wing rich); negative = put rich.
        BF_25 measures both-wing convexity vs ATM. Term slope &lt; 0 means front
        IV richer than back.
      </div>
    </>
  );
}

function SpotTab({ d }: { d: M7TradeDiagnosticResponse }) {
  const s = d.spot_regime;
  const em = d.expected_move;
  return (
    <>
      <SectionTitle>Spot price (entry &amp; during trade)</SectionTitle>
      <Grid>
        <KV label="Spot @ entry"   value={fmtUsd(s.spot_at_entry as number)} />
        <KV label="Spot @ exit"    value={fmtUsd(s.exit_spot as number)} />
        <KV label="Spot @ trough"  value={fmtUsd(s.spot_at_min_mtm as number)} />
        <KV label="Min spot in window" value={fmtUsd(s.min_spot_in_window as number)} />
        <KV label="Max spot in window" value={fmtUsd(s.max_spot_in_window as number)} />
        <KV label="Move @ trough %"
            value={fmtPct(s.spot_move_pct as number)}
            color={Math.abs((s.spot_move_pct as number) ?? 0) > 0.02 ? '#d29922' : undefined} />
        <KV label="Range %"        value={fmtPct(s.spot_range_pct as number)} />
      </Grid>

      <SectionTitle>Spot indicators (entry)</SectionTitle>
      <Grid>
        <KV label="RSI 5m"  value={fmt(s.entry_rsi_14_5m as number, 1)} />
        <KV label="RSI 4h"  value={fmt(s.entry_rsi_14_4h as number, 1)} />
        <KV label="MACD hist 5m" value={fmt(s.entry_macd_hist_5m as number, 2)} />
        <KV label="BB %b 5m"  value={fmt(s.entry_bb_pct_b_5m as number, 3)} />
        <KV label="ATR % 5m"  value={fmt(s.entry_atr_pct_5m as number, 3)} />
        <KV label="ATR % 4h"  value={fmt(s.ctx_atr_pct_4h as number, 3)} />
        <KV label="ADX 14 4h" value={fmt(s.ctx_adx_14_4h as number, 1)} />
        <KV label="PCR OI"    value={fmt(s.ctx_pcr_oi as number, 2)} />
        <KV label="Total GEX" value={fmtUsd(s.ctx_total_gex as number)} />
      </Grid>

      <SectionTitle>Expected move vs actual</SectionTitle>
      <Grid>
        <KV label="1σ 7d"   value={fmtUsd(em.expected_move_1sigma_7d)} />
        <KV label="1σ 14d"  value={fmtUsd(em.expected_move_1sigma_14d)} />
        <KV label="1σ 30d"  value={fmtUsd(em.expected_move_1sigma_30d)} />
        <KV label="Actual move $" value={fmtUsd(em.actual_move_usd)} />
        <KV label="Actual / 1σ 7d" value={fmt(em.actual_vs_1sigma_7d_ratio, 2)}
            color={em.exceeded_1sigma_7d ? '#f85149' : undefined} />
        <KV label="Exceeded 1σ 7d?" value={em.exceeded_1sigma_7d == null ? '—' : (em.exceeded_1sigma_7d ? 'YES' : 'NO')}
            color={em.exceeded_1sigma_7d ? '#f85149' : undefined} />
      </Grid>
    </>
  );
}

function GreeksTab({ d }: { d: M7TradeDiagnosticResponse }) {
  const g = d.greeks_ratios;
  return (
    <>
      <SectionTitle>Net Greeks (entry)</SectionTitle>
      <Grid>
        <KV label="Net Δ"    value={fmt(g.net_delta_at_entry as number, 4)} />
        <KV label="Net Γ"    value={fmt(g.net_gamma_at_entry as number, 6)} />
        <KV label="Net Θ"    value={fmt(g.net_theta_at_entry as number, 2)} />
        <KV label="Net ν"    value={fmt(g.net_vega_at_entry as number, 2)} />
      </Grid>

      <SectionTitle>Diagnostic ratios (loss-driver intuition)</SectionTitle>
      <Grid>
        <KV label="Θ/ν call"      value={fmt(g.theta_per_vega_call as number, 4)} />
        <KV label="Θ/ν put"       value={fmt(g.theta_per_vega_put as number, 4)} />
        <KV label="Θ/ν combined"  value={fmt(g.theta_per_vega_combined as number, 4)} />
        <KV label="|Θ| / |Γ|"     value={fmt(g.abs_theta_per_gamma as number, 1)} />
        <KV label="|Γ| / |ν|"     value={fmt(g.abs_gamma_per_vega as number, 5)} />
      </Grid>

      <SectionTitle>Δ drift during trade</SectionTitle>
      <Grid>
        <KV label="Net Δ @ entry"  value={fmt(g.net_delta_at_entry as number, 4)} />
        <KV label="Net Δ @ trough" value={fmt(g.net_delta_at_min_mtm as number, 4)} />
        <KV label="Δ drift"        value={fmt(g.delta_drift as number, 4)} />
        <KV label="|drift| × max-leg-Δ"
            value={fmt(g.delta_drift_x_max_leg as number, 2)}
            color={(g.delta_drift_x_max_leg as number) > 2.0 ? '#ff7b72' : undefined} />
        <KV label="Θ/ν @ trough"   value={fmt(g.theta_per_vega_at_min_mtm as number, 4)} />
      </Grid>
      <div style={{ fontSize: 10, color: '#5b7894', marginTop: 6,
                    fontStyle: 'italic' }}>
        |drift| / max-leg-|Δ| &gt; 2× is the threshold the gamma-squeeze classifier uses.
        Θ/ν measures time-decay efficiency — higher (less negative) = trade earned
        theta with less vega exposure.
      </div>
    </>
  );
}

function PathTabEmbed({ tradeId }: { tradeId: string }) {
  // M7TradePathChart is itself a modal; render with a noop onClose
  // to embed the chart canvas inside this tab.
  // We override the position to fill the tab pane.
  return (
    <div style={{ position: 'relative', minHeight: 540 }}>
      <M7TradePathChart tradeId={tradeId} onClose={() => { /* no-op inside tab */ }} />
    </div>
  );
}

// ── Building blocks ──────────────────────────────────────────────────────────

function LegCard({ label, leg }: { label: string;
                                    leg: M7TradeDiagnosticResponse['per_leg']['call'] }) {
  return (
    <div style={{ background: '#0d1421', border: '1px solid #1a2d42',
                  borderRadius: 6, padding: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#cfd9e3',
                    marginBottom: 8, letterSpacing: 0.5 }}>{label}</div>
      <Grid>
        <KV label="Strike"      value={leg.strike != null ? `$${leg.strike.toLocaleString()}` : '—'} />
        <KV label="Entry IV"    value={fmt(leg.entry_iv, 4)} />
        <KV label="Entry Δ"     value={fmt(leg.entry_delta, 4)} />
        <KV label="Entry Γ"     value={fmt(leg.entry_gamma, 6)} />
        <KV label="Entry Θ"     value={fmt(leg.entry_theta, 2)} />
        <KV label="Entry ν"     value={fmt(leg.entry_vega, 2)} />
        <KV label="Entry mark"  value={fmt(leg.entry_mark, 2, ' BTC')} />
        <KV label="Exit mark"   value={fmt(leg.exit_mark, 2, ' BTC')} />
        <KV label="Leg P&L"     value={fmtUsd(leg.leg_pnl_usd)}
            color={leg.leg_pnl_usd != null && leg.leg_pnl_usd < 0 ? '#f85149' : '#3fb950'} />
        <KV label="Leg max MTM" value={fmtUsd(leg.leg_max_mtm_usd)} />
        <KV label="Leg min MTM" value={fmtUsd(leg.leg_min_mtm_usd)} />
      </Grid>
    </div>
  );
}

function HypothesisRow({ h }: { h: M7TradeHypothesis }) {
  const color = HYPOTHESIS_COLOR[h.flag] || '#7a9bb5';
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '6px 10px', background: '#0d1421',
      border: `1px solid ${h.fired ? color : '#1a2d42'}`,
      borderRadius: 4, fontSize: 11,
    }}>
      <span style={{
        width: 100, fontWeight: 700,
        color: h.fired ? color : '#7a9bb5',
        fontFamily: 'ui-monospace, monospace',
      }}>
        {h.fired ? '☑' : '☐'} {h.flag}
      </span>
      <span style={{ flex: 1, color: h.fired ? '#cfd9e3' : '#7a9bb5' }}>
        {h.trigger}
      </span>
      {h.value != null && (
        <span style={{ color: '#7a9bb5', fontFamily: 'ui-monospace, monospace',
                       fontSize: 10 }}>
          v={h.value}
        </span>
      )}
    </div>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                  gap: 6, marginTop: 6 }}>
      {children}
    </div>
  );
}

function KV({ label, value, color, info }: { label: string; value: React.ReactNode; color?: string; info?: string }) {
  // info lookup — applied automatically when explicit info isn't passed,
  // so callers don't have to thread descriptions to every existing <KV>.
  const auto = info ?? KV_INFO[label.toLowerCase()];
  return (
    <div style={{
      padding: '6px 10px', background: '#0d1421', border: '1px solid #1a2d42',
      borderRadius: 4,
    }}>
      <div style={{ fontSize: 9, color: '#7a9bb5',
                    textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {label}{auto && <InfoIcon text={auto} />}
      </div>
      <div style={{ fontSize: 12, color: color || '#cfd9e3', fontWeight: 600,
                    marginTop: 3, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
    </div>
  );
}

// Centralized info-text lookup for the trade-diagnostic <KV> cells. Keys are
// the lowercased KV label. Add new entries here rather than threading info=
// to every call site.
const KV_INFO: Record<string, string> = {
  // Overview / P&L
  'net p&l': 'Realised net P&L (entry slip + entry brokerage + exit slip + exit brokerage all subtracted).',
  'gross p&l': 'Path-engine gross P&L at exit (no costs subtracted). gross = credit − (exit_call_mark + exit_put_mark) × qty × 0.001.',
  'credit': 'Upfront credit collected at entry: (call_entry_mark + put_entry_mark) × qty × 0.001 BTC.',
  'margin': 'Delta Exchange portfolio margin required at entry (29-scenario engine).',
  'max mtm': 'Peak unrealized P&L during the hold (entry costs only — what you would have seen on screen at the best moment).',
  'min mtm': 'Trough unrealized P&L during the hold (entry costs only — worst moment on screen).',
  'exit mtm': 'On-screen P&L at the moment of exit (gross − entry costs only). NOT the realised number (exit costs not yet deducted).',
  'duration': 'Time from entry to exit, in minutes / hours.',
  'rel time @ peak': 'Fraction of the entry→exit window where peak MTM occurred. 0 = at entry, 1 = at exit. Half a window = 0.5.',
  'rel time @ trough': 'Fraction of the entry→exit window where trough MTM occurred.',
  '% return / credit': 'net_pnl ÷ credit_collected. ROI on premium captured.',
  '% return / margin': 'net_pnl ÷ margin_at_entry. Capital efficiency.',
  // Skew
  'δ skew': 'call_entry_delta + put_entry_delta. ±0 = perfectly hedged at entry; +ve = net long delta; −ve = net short delta.',
  'iv skew %': '(call_iv − put_iv) ÷ atm_iv. >0 = call side richer; <0 = put side richer.',
  'premium skew $': 'call_entry_mark − put_entry_mark. >0 = call premium > put premium.',
  'premium skew %': 'premium skew normalised by total credit.',
  'iv bucket': 'Categorical IV skew bucket: put_richer_strong / put_richer / balanced / call_richer / call_richer_strong.',
  'δ bucket': 'Categorical delta skew bucket.',
  'premium bucket': 'Categorical premium skew bucket.',
  'leg p&l diff': 'call_leg_pnl − put_leg_pnl. Reveals which leg carried the trade.',
  // Vol regime
  'atm iv (entry)': 'Annualised ATM IV at entry (decimal: 0.45 = 45%).',
  'atm iv 7d': 'Mean ATM IV across the 7d tenor at entry.',
  'atm iv 14d': 'Mean ATM IV across the 14d tenor at entry.',
  'atm iv 30d': 'Mean ATM IV across the 30d tenor at entry.',
  'atm iv 60d': 'Mean ATM IV across the 60d tenor at entry.',
  'ivp 4h': 'IV percentile of the entry 4h-window ATM IV vs the last 4h.',
  'ivp 7d/90': '7d-tenor ATM IV percentile rank vs 90d history (0-100).',
  'ivp 14d/90': '14d-tenor ATM IV percentile rank vs 90d history.',
  'ivp 30d/90': '30d-tenor ATM IV percentile rank vs 90d history.',
  'δivp 4h vs 24h': 'Change in 4h-IVP over the last 24h.',
  'δivp 4h vs 48h': 'Change in 4h-IVP over the last 48h.',
  'iv_change σ 7d': 'Stdev of hourly IV changes over the last 7d.',
  'vov_ratio': 'Vol-of-vol ratio: how variable IV itself has been recently.',
  'rv 7d': 'Realised volatility over last 7d (annualised).',
  'rv 14d': 'Realised volatility over last 14d.',
  'rv 30d': 'Realised volatility over last 30d.',
};

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 11, color: '#7a9bb5',
                  textTransform: 'uppercase', letterSpacing: 0.6,
                  fontWeight: 700, marginTop: 12, marginBottom: 4 }}>
      {children}
    </div>
  );
}

function Chip({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{
      padding: '2px 8px', fontSize: 10, fontWeight: 600,
      background: color ? `${color}22` : '#1a2d42',
      color: color || '#cfd9e3',
      border: `1px solid ${color || '#1a2d42'}`,
      borderRadius: 12,
      fontFamily: 'ui-monospace, monospace',
    }}>
      {children}
    </span>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const overlayStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
  zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center',
  padding: 24,
};
const shellStyle: React.CSSProperties = {
  width: '100%', maxWidth: 1200, height: '92vh',
  background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 8,
  display: 'flex', flexDirection: 'column',
  color: '#cfd9e3',
};
const closeBtn: React.CSSProperties = {
  background: 'transparent', color: '#cfd9e3', fontSize: 22,
  border: 'none', cursor: 'pointer', padding: '0 8px', lineHeight: 1,
};
const tabBtn: React.CSSProperties = {
  background: 'transparent', border: 'none', cursor: 'pointer',
  padding: '8px 14px', fontSize: 11, fontWeight: 600,
  borderBottom: '2px solid transparent',
};
