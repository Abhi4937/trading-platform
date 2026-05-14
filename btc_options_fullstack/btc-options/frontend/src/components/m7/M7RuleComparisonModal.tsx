import React, { useEffect, useMemo, useState } from 'react';
import { fetchM7RuleComparison, type M7RuleComparisonRow } from '../../services/m7_api';
import { InfoIcon } from './InfoIcon';

const usd = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `$${(v as number).toFixed(dp)}`;
const pct = (v: number | null | undefined, dp = 1) =>
  v == null || isNaN(v as number) ? '—' : `${((v as number) * 100).toFixed(dp)}%`;
const num = (v: number | null | undefined, dp = 3) =>
  v == null || isNaN(v as number) ? '—' : (v as number).toFixed(dp);
const pnlColor = (v: number | null | undefined) =>
  v == null ? '#7a9bb5' : v >= 0 ? '#3fb950' : '#f85149';
const hitColor = (hit: number | null | undefined) =>
  hit == null ? '#7a9bb5'
    : hit >= 0.5 ? '#3fb950'
    : hit >= 0.25 ? '#f0b300' : '#f85149';

type SortKey = 'rule_label' | 'hit_pct' | 'avg_net_pnl' | 'win_rate' | 'max_loss_usd' | 'composite_score' | 'n_trades' | 'scaled_avg_net_pnl' | 'lots' | 'scaled_primary';
type SortDir = 'asc' | 'desc';

export function M7RuleComparisonModal({
  band, expiry_bucket, delta_target, entry_hour_ist, pickedRuleLabel,
  totalCapitalUsd, pctDeploy, ddMetric, ddThreshold,
  minHitPct, maxLossCapPct, maxDropPct, minNTrades, minWinRate,
  ruleFamily,
  primaryMetric = 'avg_net_pnl', primaryLabel = 'Avg net',
  endpointPrefix, bandMode, d1Tiebreakers,
  onClose,
}: {
  band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
  pickedRuleLabel?: string;
  totalCapitalUsd?: number | null;
  pctDeploy?: number;
  ddMetric?: string | null;
  ddThreshold?: number | null;
  minHitPct?: number | null;
  maxLossCapPct?: number | null;
  maxDropPct?: number | null;
  minNTrades?: number | null;
  minWinRate?: number | null;
  ruleFamily?: 'all' | 'max_profit' | 'margin_target';
  primaryMetric?: string;  // which column the parent picker ranks on
  primaryLabel?: string;   // display label, e.g. "Composite score"
  endpointPrefix?: string;             // '/iv_band_best_combo' (default) or '/friday_band_best_combo'
  bandMode?: 'A1' | 'B1' | 'D1';       // Friday-band variant mode (passes band_mode query param)
  d1Tiebreakers?: string[];            // Friday-band D1 priority chain
  onClose: () => void;
}) {
  const sizingActive = totalCapitalUsd != null && totalCapitalUsd > 0;
  const [rows, setRows] = useState<M7RuleComparisonRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // When sizing is active, default-sort by the scaled PRIMARY metric (the column
  // the picker actually ranks on). For non-sizing, sort by primary itself.
  const [sortKey, setSortKey] = useState<SortKey>(
    sizingActive ? 'scaled_primary' : 'hit_pct',
  );
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // Per-row scaled primary = row[primaryMetric] × row.lots / 100. Reflects what
  // the picker actually maxes on, regardless of which primary the user chose.
  const rowsWithScaled = useMemo(() => rows.map(r => {
    const base = (r as any)[primaryMetric];
    const lots = r.lots ?? 100;
    const scaled_primary = (base == null || isNaN(base as number))
      ? null
      : (base as number) * lots / 100;
    return { ...r, scaled_primary };
  }), [rows, primaryMetric]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchM7RuleComparison({
      band, expiry_bucket, delta_target, entry_hour_ist,
      total_capital_usd: totalCapitalUsd ?? null,
      pct_deploy: pctDeploy,
      dd_metric: ddMetric ?? null,
      dd_threshold: ddThreshold ?? null,
      min_hit_pct: minHitPct ?? null,
      max_loss_cap_pct: maxLossCapPct ?? null,
      max_drop_peak_to_trough_pct: maxDropPct ?? null,
      min_n_trades: minNTrades ?? null,
      min_win_rate: minWinRate ?? null,
      rule_family: ruleFamily,
      endpointPrefix, bandMode, d1Tiebreakers,
    }, ac.signal)
      .then(r => { if (active) setRows(r.rows ?? []); })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [band, expiry_bucket, delta_target, entry_hour_ist,
      totalCapitalUsd, pctDeploy, ddMetric, ddThreshold,
      minHitPct, maxLossCapPct, maxDropPct, minNTrades, minWinRate, ruleFamily,
      endpointPrefix, bandMode, d1Tiebreakers?.join(',')]);

  // ESC closes
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const sorted = useMemo(() => {
    const arr = [...rowsWithScaled];
    const sign = sortDir === 'desc' ? -1 : 1;
    arr.sort((a, b) => {
      const av = (a as any)[sortKey];
      const bv = (b as any)[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;  // nulls last regardless of direction
      if (bv == null) return -1;
      if (typeof av === 'string') return sign * av.localeCompare(bv);
      return sign * (av - bv);
    });
    return arr;
  }, [rows, sortKey, sortDir]);

  const setSort = (k: SortKey) => {
    if (k === sortKey) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    else { setSortKey(k); setSortDir('desc'); }
  };

  const th: React.CSSProperties = {
    padding: '6px 8px', color: '#7a9bb5', whiteSpace: 'nowrap', cursor: 'pointer',
    fontWeight: 600, userSelect: 'none',
  };
  const thR: React.CSSProperties = { ...th, textAlign: 'right' };
  const td: React.CSSProperties = { padding: '5px 8px', whiteSpace: 'nowrap' };
  const tdR: React.CSSProperties = { ...td, textAlign: 'right' };
  const arrow = (k: SortKey) => sortKey === k ? (sortDir === 'desc' ? ' ▼' : ' ▲') : '';

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0, 0, 0, 0.75)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, padding: 24,
      }}>
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#0a0e17', border: '1px solid #1a2d42',
          borderRadius: 8, padding: 16, maxHeight: '90vh', overflow: 'auto',
          maxWidth: '95vw', minWidth: 800,
        }}>
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', marginBottom: 10 }}>
          <div style={{ color: '#cfd9e3', fontWeight: 700, fontSize: 14 }}>
            Rule comparison — band <span style={{ color: '#f0b300' }}>{band}</span>,{' '}
            <span style={{ color: '#f0b300' }}>{expiry_bucket}</span>,{' '}
            Δ={delta_target.toFixed(2)},{' '}
            entry {String(entry_hour_ist).padStart(2, '0')}:00 IST
            <InfoIcon text="All 96 rule variants tested at this fixed (band, expiry, delta, hour). Lets you see what the picker chose vs alternatives. Click any column header to sort. Default: by Hit % desc, then Avg net desc. Hit % = (n_trades − n_hard_cap) / n_trades — counts any deterministic non-hard-cap exit (premium_sl, max_profit, margin_target, fixed_hour) as effective." />
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent', border: '1px solid #1a2d42',
              borderRadius: 4, color: '#cfd9e3', padding: '4px 10px',
              cursor: 'pointer', fontSize: 11,
            }}>
            Close (ESC)
          </button>
        </div>
        <div style={{ color: '#7a9bb5', fontSize: 10, marginBottom: 8 }}>
          {sizingActive
            ? <>Each rule has its own <strong style={{ color: '#cfd9e3' }}>lots</strong> under the same Capital ${totalCapitalUsd?.toFixed(0)} / Deploy {pctDeploy ?? 100}%{ddMetric ? <> / DD cap {ddMetric}@${ddThreshold}</> : null}{ruleFamily && ruleFamily !== 'all' ? <> / Family <strong style={{ color: '#cfd9e3' }}>{ruleFamily}</strong></> : null} constraints. Picker maxes <strong style={{ color: '#cfd9e3' }}>Scaled {primaryLabel}</strong>. Rows tagged <span style={{ color: '#f0b300' }}>⊘</span> were excluded (rule family, Hit %, Min n, Max loss %, Max drop %, Win %) — that's why the ★ may not be the visible top.</>
            : <>No capital sizing active — values shown at per-100-lot baseline. Picker ranks by {primaryLabel}.</>}
        </div>
        {loading && <div style={{ color: '#7a9bb5', fontSize: 12 }}>Loading…</div>}
        {err && <div style={{ color: '#f85149', fontSize: 12 }}>{err}</div>}
        {!loading && !err && sorted.length === 0 && (
          <div style={{ color: '#7a9bb5', fontSize: 12 }}>No rows.</div>
        )}
        {sorted.length > 0 && (
          <table style={{
            borderCollapse: 'collapse', fontSize: 12,
            fontVariantNumeric: 'tabular-nums', color: '#cfd9e3',
            minWidth: '100%',
          }}>
            <thead>
              <tr style={{ textAlign: 'left' }}>
                <th style={th} onClick={() => setSort('rule_label')}>Rule{arrow('rule_label')}</th>
                <th style={thR} onClick={() => setSort('hit_pct')}>Hit %{arrow('hit_pct')}</th>
                <th style={thR} onClick={() => setSort('n_trades')}>n{arrow('n_trades')}</th>
                <th style={thR} onClick={() => setSort('win_rate')}>Win %{arrow('win_rate')}</th>
                <th style={thR} onClick={() => setSort('avg_net_pnl')}>Avg net (/100){arrow('avg_net_pnl')}</th>
                {sizingActive && <th style={thR} onClick={() => setSort('lots')}>Lots{arrow('lots')}</th>}
                {sizingActive && <th style={thR} onClick={() => setSort('scaled_primary')}>Scaled {primaryLabel}{arrow('scaled_primary')}</th>}
                <th style={thR} onClick={() => setSort('max_loss_usd')}>Max loss (/100){arrow('max_loss_usd')}</th>
                <th style={thR} onClick={() => setSort('composite_score')}>Composite{arrow('composite_score')}</th>
                <th style={thR}>n_hard_cap</th>
                <th style={thR}>n_rule_trigger</th>
                <th style={thR}>n_premium_sl_hit</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(r => {
                const isPicked = r.rule_label === pickedRuleLabel;
                const isFiltered = !!r.filtered_out;
                return (
                  <tr key={r.rule_label} style={{
                    borderTop: '1px solid #1a2d42',
                    background: isPicked ? '#0d2747' : 'transparent',
                    opacity: isFiltered ? 0.5 : 1,
                  }}>
                    <td style={{ ...td, color: isPicked ? '#1f6feb' : '#cfd9e3',
                                 fontWeight: isPicked ? 700 : 400 }}>
                      {isPicked ? '★ ' : ''}{r.rule_label}
                      {isFiltered && (
                        <span
                          style={{
                            marginLeft: 6, padding: '1px 5px', fontSize: 9,
                            color: '#f0b300', border: '1px solid #f0b300',
                            borderRadius: 3, fontWeight: 600, verticalAlign: 'middle',
                          }}
                          title={`Excluded by picker filter: ${r.filter_reasons}`}>
                          ⊘ {r.filter_reasons}
                        </span>
                      )}
                    </td>
                    <td style={{ ...tdR, color: hitColor(r.hit_pct) }}>
                      {r.hit_pct == null ? '—' : `${(r.hit_pct * 100).toFixed(0)}%`}
                    </td>
                    <td style={tdR}>{r.n_trades}</td>
                    <td style={tdR}>{pct(r.win_rate)}</td>
                    <td style={{ ...tdR, color: pnlColor(r.avg_net_pnl) }}>{usd(r.avg_net_pnl)}</td>
                    {sizingActive && <td style={tdR}>{r.lots ?? '—'}</td>}
                    {sizingActive && (() => {
                      const sp = (r as any).scaled_primary;
                      // Format $ for usd-like primaries, plain number for ratios.
                      const isDollar = primaryMetric.includes('pnl') || primaryMetric.includes('usd')
                        || primaryMetric.includes('mtm') || primaryMetric.includes('credit')
                        || primaryMetric.includes('margin') || primaryMetric.includes('loss');
                      return (
                        <td style={{ ...tdR, color: pnlColor(sp), fontWeight: isPicked ? 700 : 400 }}>
                          {sp == null ? '—' : (isDollar ? usd(sp) : num(sp, 3))}
                        </td>
                      );
                    })()}
                    <td style={{ ...tdR, color: pnlColor(r.max_loss_usd) }}>{usd(r.max_loss_usd)}</td>
                    <td style={tdR}>{num(r.composite_score, 3)}</td>
                    <td style={tdR}>{r.n_hard_cap ?? '—'}</td>
                    <td style={tdR}>{r.n_rule_trigger ?? '—'}</td>
                    <td style={tdR}>{r.n_premium_sl_hit ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
