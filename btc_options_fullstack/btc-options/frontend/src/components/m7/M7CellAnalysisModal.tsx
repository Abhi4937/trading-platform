// Click "🔍 Analyze" on a Best Combo row → modal shows two analysis tabs:
//   - Cross-band check (Feature B): does this combo work in other IV regimes?
//   - Single-combo simulation (Feature C): what if you always traded just this?

import React, { useEffect, useState } from 'react';
import {
  fetchM7CrossBandCheck, fetchM7SingleComboSimulation,
  fetchM7CellFridayDetail,
  type M7CrossBandCheckResponse, type M7SingleComboSimulationResponse,
  type M7CellFridayDetailResponse, type M7CellFridayDetailRow,
  type M7IvBandBestComboRow,
} from '../../services/m7_api';
import { InfoIcon } from './InfoIcon';

const usd = (v: number | null | undefined, dp = 2) =>
  v == null || isNaN(v as number) ? '—' : `$${(v as number).toFixed(dp)}`;
const pct = (v: number | null | undefined, dp = 1) =>
  v == null || isNaN(v as number) ? '—' : `${((v as number) * 100).toFixed(dp)}%`;
const pnlColor = (v: number | null | undefined) =>
  v == null ? '#7a9bb5' : v >= 0 ? '#3fb950' : '#f85149';

type Tab = 'cross_band' | 'single_combo' | 'friday_detail';

export function M7CellAnalysisModal({
  band, expiry_bucket, delta_target, entry_hour_ist, rule_label,
  totalCapitalUsd, pctDeploy, lots = 100,
  endpointPrefix, bandMode, d1Tiebreakers,
  onClose,
}: {
  band: string;
  expiry_bucket: string;
  delta_target: number;
  entry_hour_ist: number;
  rule_label: string;
  totalCapitalUsd?: number | null;
  pctDeploy?: number;
  lots?: number;  // sized-lots from the parent table row; defaults to 100 (baseline)
  endpointPrefix?: string;             // '/iv_band_best_combo' (default) or '/friday_band_best_combo'
  bandMode?: 'A1' | 'B1' | 'D1';
  d1Tiebreakers?: string[];
  onClose: () => void;
}) {
  const k = (lots > 0 ? lots : 100) / 100;
  const sk = (v: number | null | undefined): number | null =>
    v == null || isNaN(v as number) ? null : (v as number) * k;
  const [tab, setTab] = useState<Tab>('cross_band');
  const [crossBand, setCrossBand] = useState<M7CrossBandCheckResponse | null>(null);
  const [singleCombo, setSingleCombo] = useState<M7SingleComboSimulationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Friday-detail tab is lazy-loaded the first time the user opens it so the
  // common modal-open path stays fast (cold rule cache miss = 5–15 s).
  const [fridayDetail, setFridayDetail] = useState<M7CellFridayDetailResponse | null>(null);
  const [fridayLoading, setFridayLoading] = useState(false);
  const [fridayErr, setFridayErr] = useState<string | null>(null);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    Promise.all([
      fetchM7CrossBandCheck({
        band, expiry_bucket, delta_target, entry_hour_ist, rule_label,
        endpointPrefix, bandMode, d1Tiebreakers,
      }, ac.signal),
      fetchM7SingleComboSimulation({
        expiry_bucket, delta_target, entry_hour_ist, rule_label,
        total_capital_usd: totalCapitalUsd ?? null, pct_deploy: pctDeploy,
        endpointPrefix, bandMode, d1Tiebreakers,
      }, ac.signal),
    ])
      .then(([cb, sc]) => { if (active) { setCrossBand(cb); setSingleCombo(sc); } })
      .catch(e => { if (active && e?.name !== 'AbortError') setErr(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [band, expiry_bucket, delta_target, entry_hour_ist, rule_label, totalCapitalUsd, pctDeploy,
      endpointPrefix, bandMode, d1Tiebreakers?.join(',')]);

  useEffect(() => {
    if (tab !== 'friday_detail' || fridayDetail !== null) return;
    let active = true;
    const ac = new AbortController();
    setFridayLoading(true);
    setFridayErr(null);
    fetchM7CellFridayDetail({
      band, expiry_bucket, delta_target, entry_hour_ist, rule_label,
      endpointPrefix, bandMode, d1Tiebreakers,
    }, ac.signal)
      .then(r => { if (active) setFridayDetail(r); })
      .catch(e => { if (active && e?.name !== 'AbortError') setFridayErr(String(e)); })
      .finally(() => { if (active) setFridayLoading(false); });
    return () => { active = false; ac.abort(); };
  }, [tab, band, expiry_bucket, delta_target, entry_hour_ist, rule_label,
      endpointPrefix, bandMode, d1Tiebreakers?.join(',')]);

  // Reset the Friday-detail cache whenever the cell coordinates change so the
  // tab re-fetches for the new cell when reopened.
  useEffect(() => {
    setFridayDetail(null);
    setFridayErr(null);
  }, [band, expiry_bucket, delta_target, entry_hour_ist, rule_label,
      endpointPrefix, bandMode, d1Tiebreakers?.join(',')]);

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px', cursor: 'pointer', fontSize: 12,
    background: active ? '#1f6feb' : 'transparent',
    color: active ? '#fff' : '#cfd9e3',
    border: '1px solid #1f6feb',
    fontWeight: active ? 700 : 400,
  });
  const th: React.CSSProperties = { padding: '6px 8px', color: '#7a9bb5', whiteSpace: 'nowrap' };
  const thR: React.CSSProperties = { ...th, textAlign: 'right' };
  const td: React.CSSProperties = { padding: '5px 8px', whiteSpace: 'nowrap' };
  const tdR: React.CSSProperties = { ...td, textAlign: 'right' };

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0, 0, 0, 0.75)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000, padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: '#0a0e17', border: '1px solid #1a2d42',
        borderRadius: 8, padding: 16, maxHeight: '90vh', overflow: 'auto',
        maxWidth: '95vw', minWidth: 760,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <div style={{ color: '#cfd9e3', fontWeight: 700, fontSize: 14 }}>
            Cell analysis — <span style={{ color: '#f0b300' }}>{band}</span>,{' '}
            <span style={{ color: '#f0b300' }}>{rule_label}</span>,{' '}
            <span style={{ color: '#7a9bb5' }}>{expiry_bucket} · Δ={delta_target.toFixed(2)} · {String(entry_hour_ist).padStart(2, '0')}:00 IST</span>
          </div>
          <button onClick={onClose} style={{
            background: 'transparent', border: '1px solid #1a2d42',
            borderRadius: 4, color: '#cfd9e3', padding: '4px 10px',
            cursor: 'pointer', fontSize: 11,
          }}>Close (ESC)</button>
        </div>
        <div style={{ color: '#7a9bb5', fontSize: 10, marginBottom: 8 }}>
          $ values scaled to <strong style={{ color: '#cfd9e3' }}>{lots} lots</strong> (matches the picked cell's sized lots in the table).
        </div>

        <div style={{ display: 'flex', marginBottom: 10 }}>
          <button onClick={() => setTab('cross_band')} style={{ ...tabStyle(tab === 'cross_band'), borderTopLeftRadius: 4, borderBottomLeftRadius: 4 }}>
            Cross-band check
            <InfoIcon text="Same combo applied across all 10 IV bands. Answers: does this rule generalise across IV regimes, or is it band-specific? Bands where the combo has no trades are absent." />
          </button>
          <button onClick={() => setTab('single_combo')} style={{ ...tabStyle(tab === 'single_combo'), borderLeft: 'none' }}>
            Single-combo simulation
            <InfoIcon text="Counterfactual: what if every Friday traded this single combo regardless of IV regime? Weighted aggregate across all bands. Compare to band-aware (regime-switching) P&L to decide if regime detection adds value." />
          </button>
          <button onClick={() => setTab('friday_detail')} style={{ ...tabStyle(tab === 'friday_detail'), borderLeft: 'none', borderTopRightRadius: 4, borderBottomRightRadius: 4 }}>
            Friday detail
            <InfoIcon text="For this exact cell: which Friday each losing trade was on, the Friday behind the worst-MTM winner (Min MTM (W)), the Friday of the largest win, and the Fridays of winners that dipped below the cell's avg min MTM (W < avg min MTM). Loads lazily on first open of this tab." />
          </button>
        </div>

        {loading && <div style={{ color: '#7a9bb5', fontSize: 12 }}>Loading…</div>}
        {err && <div style={{ color: '#f85149', fontSize: 12 }}>{err}</div>}

        {tab === 'cross_band' && crossBand && (
          <>
            <div style={{ color: '#7a9bb5', fontSize: 11, marginBottom: 8 }}>
              How this exact rule + (hour, expiry, Δ) performs when Fridays' actual entry IV lands in different bands.
              Picked band <span style={{ color: '#f0b300' }}>{band}</span> highlighted.
            </div>
            <table style={{ borderCollapse: 'collapse', fontSize: 12, fontVariantNumeric: 'tabular-nums', color: '#cfd9e3', width: '100%' }}>
              <thead><tr style={{ textAlign: 'left' }}>
                <th style={th}>IV band</th>
                <th style={thR}>n</th>
                <th style={thR}>Win %</th>
                <th style={thR}>Avg net</th>
                <th style={thR}>Max loss</th>
                <th style={thR}>Composite</th>
                <th style={thR}>Hit %</th>
              </tr></thead>
              <tbody>
                {crossBand.rows.map((r: M7IvBandBestComboRow) => {
                  const n = r.n_trades;
                  const hc = r.n_hard_cap;
                  const hit = n && hc != null ? (n - hc) / n : null;
                  const isPicked = r.iv_band === band;
                  return (
                    <tr key={r.iv_band} style={{ borderTop: '1px solid #1a2d42', background: isPicked ? '#0d2747' : 'transparent' }}>
                      <td style={{ ...td, fontWeight: isPicked ? 700 : 400, color: isPicked ? '#1f6feb' : '#cfd9e3' }}>
                        {isPicked ? '★ ' : ''}{r.iv_band}
                      </td>
                      <td style={tdR}>{r.n_trades}</td>
                      <td style={tdR}>{pct(r.win_rate)}</td>
                      <td style={{ ...tdR, color: pnlColor(r.avg_net_pnl) }}>{usd(sk(r.avg_net_pnl))}</td>
                      <td style={{ ...tdR, color: pnlColor(r.max_loss_usd) }}>{usd(sk(r.max_loss_usd))}</td>
                      <td style={tdR}>{r.composite_score == null ? '—' : r.composite_score.toFixed(3)}</td>
                      <td style={{ ...tdR, color: hit == null ? '#7a9bb5' : hit >= 0.5 ? '#3fb950' : hit >= 0.25 ? '#f0b300' : '#f85149' }}>
                        {hit == null ? '—' : `${(hit * 100).toFixed(0)}%`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}

        {tab === 'single_combo' && singleCombo?.summary && (() => {
          const s = singleCombo.summary;
          return (
            <>
              <div style={{ color: '#7a9bb5', fontSize: 11, marginBottom: 8 }}>
                What if every Friday traded this combo regardless of IV regime?{' '}
                <strong>{s.n_trades}</strong> trades across{' '}
                <strong>{s.n_bands_covered}</strong> IV bands.
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                <KPI label="Avg net per trade" value={usd(sk(s.avg_net_pnl))} color={pnlColor(s.avg_net_pnl)} info={`Mean net P&L per trade across ALL Fridays where this combo has any data — weighted by n_trades per band. Scaled to ${lots} lots.`} />
                <KPI label={`Total net (at ${lots} lots)`} value={usd(sk(s.total_net_pnl))} color={pnlColor(s.total_net_pnl)} />
                <KPI label="Win rate" value={pct(s.win_rate)} color="#cfd9e3" />
                <KPI label="Max loss (any single)" value={usd(sk(s.max_loss_usd))} color={pnlColor(s.max_loss_usd)} />
                <KPI label="Composite (weighted)" value={s.composite_score == null ? '—' : s.composite_score.toFixed(3)} color="#cfd9e3" info="win_rate × ret_on_credit ÷ (1 + |avg_min_mtm|/avg_credit). Aggregated across bands." />
                <KPI label="Sharpe (weighted)" value={s.sharpe_per_trade == null ? '—' : s.sharpe_per_trade.toFixed(2)} color="#cfd9e3" info="avg_net / stdev_net. Higher = more consistent." />
              </div>
              {s.lots != null && (
                <div style={{ background: '#0d2747', border: '1px solid #1f6feb', borderRadius: 6, padding: 12, marginBottom: 16 }}>
                  <div style={{ color: '#1f6feb', fontWeight: 700, marginBottom: 6, fontSize: 12 }}>
                    Scaled at ${singleCombo.total_capital_usd?.toFixed(0) ?? 0} capital ({singleCombo.pct_deploy?.toFixed(0)}% deploy)
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8 }}>
                    <KPI label="Lots" value={String(s.lots)} color="#cfd9e3" />
                    <KPI label="Scaled avg/Fri" value={usd(s.scaled_avg_net_pnl)} color={pnlColor(s.scaled_avg_net_pnl)} />
                    <KPI label="Scaled total" value={usd(s.scaled_total_net_pnl)} color={pnlColor(s.scaled_total_net_pnl)} />
                    <KPI label="Scaled max loss" value={usd(s.scaled_max_loss_usd)} color={pnlColor(s.scaled_max_loss_usd)} />
                  </div>
                </div>
              )}
              {singleCombo.per_band_breakdown && singleCombo.per_band_breakdown.length > 0 && (
                <>
                  <div style={{ color: '#cfd9e3', fontWeight: 700, marginBottom: 6, fontSize: 12 }}>
                    Per-band breakdown
                  </div>
                  <table style={{ borderCollapse: 'collapse', fontSize: 11, fontVariantNumeric: 'tabular-nums', color: '#cfd9e3', width: '100%' }}>
                    <thead><tr style={{ textAlign: 'left' }}>
                      <th style={th}>Band</th><th style={thR}>n</th>
                      <th style={thR}>Win %</th><th style={thR}>Avg net</th>
                      <th style={thR}>Max loss</th>
                    </tr></thead>
                    <tbody>
                      {singleCombo.per_band_breakdown.map(r => (
                        <tr key={r.iv_band} style={{ borderTop: '1px solid #1a2d42' }}>
                          <td style={td}>{r.iv_band}</td>
                          <td style={tdR}>{r.n_trades}</td>
                          <td style={tdR}>{pct(r.win_rate)}</td>
                          <td style={{ ...tdR, color: pnlColor(r.avg_net_pnl) }}>{usd(sk(r.avg_net_pnl))}</td>
                          <td style={{ ...tdR, color: pnlColor(r.max_loss_usd) }}>{usd(sk(r.max_loss_usd))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          );
        })()}
        {tab === 'single_combo' && !loading && !singleCombo?.summary && (
          <div style={{ color: '#7a9bb5', fontSize: 12 }}>No data for this combo across all bands.</div>
        )}

        {tab === 'friday_detail' && (
          <FridayDetailTab
            data={fridayDetail}
            loading={fridayLoading}
            err={fridayErr}
            th={th}
            thR={thR}
            td={td}
            tdR={tdR}
            sk={sk}
            lots={lots}
          />
        )}
      </div>
    </div>
  );
}

function FridayDetailRowsTable({
  rows, columns, th, thR, td, tdR, sk,
}: {
  rows: M7CellFridayDetailRow[];
  columns: Array<'friday' | 'net' | 'min' | 'max' | 'exit'>;
  th: React.CSSProperties; thR: React.CSSProperties;
  td: React.CSSProperties; tdR: React.CSSProperties;
  sk: (v: number | null | undefined) => number | null;
}) {
  if (rows.length === 0) {
    return <div style={{ color: '#7a9bb5', fontSize: 11, padding: '6px 0' }}>—</div>;
  }
  return (
    <table style={{ borderCollapse: 'collapse', fontSize: 11, fontVariantNumeric: 'tabular-nums', color: '#cfd9e3', width: '100%' }}>
      <thead><tr style={{ textAlign: 'left' }}>
        {columns.includes('friday') && <th style={th}>Friday</th>}
        {columns.includes('net') && <th style={thR}>Net P&L</th>}
        {columns.includes('min') && <th style={thR}>Min MTM</th>}
        {columns.includes('max') && <th style={thR}>Max MTM</th>}
        {columns.includes('exit') && <th style={th}>Exit</th>}
      </tr></thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.trade_id || `${r.friday_date_ist}-${r.net_pnl_estimate_usd}`} style={{ borderTop: '1px solid #1a2d42' }}>
            {columns.includes('friday') && <td style={{ ...td, fontWeight: 600 }}>{r.friday_date_ist}</td>}
            {columns.includes('net') && (
              <td style={{ ...tdR, color: (r.net_pnl_estimate_usd ?? 0) >= 0 ? '#3fb950' : '#f85149' }}>
                {usd(sk(r.net_pnl_estimate_usd))}
              </td>
            )}
            {columns.includes('min') && (
              <td style={{ ...tdR, color: '#f85149' }}>{usd(sk(r.min_mtm_usd))}</td>
            )}
            {columns.includes('max') && (
              <td style={{ ...tdR, color: '#3fb950' }}>{usd(sk(r.max_mtm_usd))}</td>
            )}
            {columns.includes('exit') && (
              <td style={{ ...td, color: '#7a9bb5', fontSize: 10 }}>{r.exit_reason || '—'}</td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FridayDetailTab({
  data, loading, err, th, thR, td, tdR, sk, lots,
}: {
  data: M7CellFridayDetailResponse | null;
  loading: boolean;
  err: string | null;
  th: React.CSSProperties; thR: React.CSSProperties;
  td: React.CSSProperties; tdR: React.CSSProperties;
  sk: (v: number | null | undefined) => number | null;
  lots: number;
}) {
  if (err) return <div style={{ color: '#f85149', fontSize: 12 }}>{err}</div>;
  if (loading) {
    return (
      <div style={{ color: '#7a9bb5', fontSize: 12 }}>
        Loading Friday detail… (first request per rule warms a per-trade cache — typically 5–15 s)
      </div>
    );
  }
  if (!data) return null;
  if (data.status === 'unknown_rule') {
    return <div style={{ color: '#f85149', fontSize: 12 }}>Unknown rule label — cannot resolve trade detail.</div>;
  }
  if (data.status === 'no_trades' || !data.cell) {
    return <div style={{ color: '#7a9bb5', fontSize: 12 }}>No trades in this cell.</div>;
  }
  const c = data.cell;
  return (
    <div>
      <div style={{ color: '#7a9bb5', fontSize: 11, marginBottom: 10 }}>
        Friday-level breakdown for this cell. Numbers scaled to <strong style={{ color: '#cfd9e3' }}>{lots} lots</strong>.
      </div>

      <Section
        title={`Losing Fridays (n=${c.n_losses})`}
        info="Every Friday this cell took a loss, sorted by net P&L ascending (worst first)."
      >
        <FridayDetailRowsTable
          rows={data.losers}
          columns={['friday', 'net', 'min', 'max', 'exit']}
          th={th} thR={thR} td={td} tdR={tdR} sk={sk}
        />
      </Section>

      <Section
        title="Worst-MTM winner"
        info="The single winning trade that dipped deepest into drawdown before recovering. This is where the cell's Min MTM (W) aggregate comes from."
      >
        {data.worst_winner ? (
          <div style={{ color: '#cfd9e3', fontSize: 12 }}>
            <strong>{data.worst_winner.friday_date_ist}</strong>
            <span style={{ color: '#7a9bb5', marginLeft: 8 }}>
              · Net <span style={{ color: '#3fb950' }}>{usd(sk(data.worst_winner.net_pnl_estimate_usd))}</span>
              · MinMTM <span style={{ color: '#f85149' }}>{usd(sk(data.worst_winner.min_mtm_usd))}</span>
              · MaxMTM <span style={{ color: '#3fb950' }}>{usd(sk(data.worst_winner.max_mtm_usd))}</span>
              {data.worst_winner.exit_reason && <> · exit: {data.worst_winner.exit_reason}</>}
            </span>
          </div>
        ) : (
          <div style={{ color: '#7a9bb5', fontSize: 11 }}>No winners in this cell.</div>
        )}
      </Section>

      <Section
        title="Largest win"
        info="The winning trade with the highest net P&L. This is where the cell's Largest win aggregate comes from."
      >
        {data.largest_win ? (
          <div style={{ color: '#cfd9e3', fontSize: 12 }}>
            <strong>{data.largest_win.friday_date_ist}</strong>
            <span style={{ color: '#7a9bb5', marginLeft: 8 }}>
              · Net <span style={{ color: '#3fb950' }}>{usd(sk(data.largest_win.net_pnl_estimate_usd))}</span>
              · MinMTM <span style={{ color: '#f85149' }}>{usd(sk(data.largest_win.min_mtm_usd))}</span>
              · MaxMTM <span style={{ color: '#3fb950' }}>{usd(sk(data.largest_win.max_mtm_usd))}</span>
              {data.largest_win.exit_reason && <> · exit: {data.largest_win.exit_reason}</>}
            </span>
          </div>
        ) : (
          <div style={{ color: '#7a9bb5', fontSize: 11 }}>No winners in this cell.</div>
        )}
      </Section>

      <Section
        title={`Winners below avg MinMTM (n=${c.n_winners_below_avg_min_mtm})`}
        info="Winners whose min MTM dipped below the cell's avg_min_mtm_winners. The cell's W < avg min MTM count. Sorted by min MTM ascending (deepest dip first)."
      >
        <div style={{ color: '#7a9bb5', fontSize: 10, marginBottom: 6 }}>
          Threshold (avg min MTM among winners): <strong style={{ color: '#f85149' }}>{usd(sk(c.avg_min_mtm_winners))}</strong>
        </div>
        <FridayDetailRowsTable
          rows={data.winners_below_avg_min_mtm}
          columns={['friday', 'min', 'net', 'max']}
          th={th} thR={thR} td={td} tdR={tdR} sk={sk}
        />
      </Section>
    </div>
  );
}

function Section({ title, info, children }: { title: string; info?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6, padding: 10, marginBottom: 10 }}>
      <div style={{ color: '#cfd9e3', fontWeight: 700, fontSize: 12, marginBottom: 6 }}>
        {title}{info && <InfoIcon text={info} />}
      </div>
      {children}
    </div>
  );
}

function KPI({ label, value, color, info }: { label: string; value: string; color: string; info?: string }) {
  return (
    <div style={{ background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6, padding: 10 }}>
      <div style={{ color: '#7a9bb5', fontSize: 10, marginBottom: 4 }}>
        {label}{info && <InfoIcon text={info} />}
      </div>
      <div style={{ color, fontSize: 16, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  );
}
