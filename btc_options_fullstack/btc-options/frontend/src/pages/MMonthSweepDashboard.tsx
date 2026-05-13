import React, { useEffect, useState } from 'react';
import {
  m_month_api,
  MMonthMeta,
  MMonthSummary,
  MMonthBestComboRow,
  MMonthBestComboResponse,
  MMonthTradeCycle,
} from '../services/m_month_api';

const CYCLE_LABELS: Record<MMonthTradeCycle, string> = {
  monthly: 'Monthly',
  bimonthly: 'Bimonthly',
  lastfri_monthly: 'Last-Fri Monthly',
  lastfri_bimonthly: 'Last-Fri Bimonthly',
  all: 'All cycles',
};

const CYCLE_DESCRIPTIONS: Record<MMonthTradeCycle, string> = {
  monthly: 'Enter first Mon 23:00 IST → exit same-month last-Fri 11:00 IST. Sells current-month expiry. ~28 DTE entry → 0 DTE exit.',
  bimonthly: 'Enter first Mon 23:00 IST → exit same-month last-Fri 11:00 IST. Sells next-month expiry. ~58 DTE entry → ~30 DTE exit.',
  lastfri_monthly: 'Enter last-Fri 10:00 IST → exit next last-Fri 11:00 IST. Sells next-month expiry (= exit-day). ~28 DTE entry → 0 DTE exit. Back-to-back monthly cycles.',
  lastfri_bimonthly: 'Enter last-Fri 10:00 IST → exit next last-Fri 11:00 IST. Sells month-after-next expiry. ~58 DTE entry → ~30 DTE exit.',
  all: 'All four cycles in one view.',
};

const PRIMARY_METRIC_LABELS: Record<string, string> = {
  avg_net_pnl: 'Avg Net P&L ($)',
  total_net_pnl: 'Total Net P&L ($)',
  win_rate: 'Win Rate',
  avg_credit_usd: 'Avg Credit ($)',
  avg_margin_usd: 'Avg Margin ($)',
  avg_pct_return_on_credit: 'Avg % Return on Credit',
  avg_pct_return_on_margin: 'Avg % Return on Margin',
  n_trades: '# Trades',
};

function pnlColor(v: number | null | undefined): string | undefined {
  if (v === null || v === undefined || isNaN(v as number)) return undefined;
  if (v > 0) return '#3fb950';
  if (v < 0) return '#f85149';
  return undefined;
}

function fmtUsd(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || isNaN(v as number)) return '—';
  return `$${(v as number).toFixed(decimals)}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || isNaN(v as number)) return '—';
  return `${(v as number).toFixed(1)}%`;
}

function fmtRate(v: number | null | undefined): string {
  if (v === null || v === undefined || isNaN(v as number)) return '—';
  return `${((v as number) * 100).toFixed(0)}%`;
}

const HOLD_DURATION_LABELS: Record<string, string> = {
  natural: 'Natural (cycle exit)',
  '3d': '3 days',
  '5d': '5 days',
  '1w': '1 week',
  '2w': '2 weeks',
  '3w': '3 weeks',
  '4w': '4 weeks',
  '5w': '5 weeks',
  '6w': '6 weeks',
  '7w': '7 weeks',
  '8w': '8 weeks',
  all: 'All durations',
};

const dropdownStyle: React.CSSProperties = {
  marginLeft: 6, background: '#0d1421', color: '#c8d6e5',
  border: '1px solid #1a2d42', borderRadius: 4, padding: '3px 8px',
};

function exitRuleLabel(bc: MMonthBestComboResponse): string {
  const parts: string[] = [];
  if (bc.premium_sl_pct != null) parts.push(`SL ${bc.premium_sl_pct}%`);
  if (bc.max_profit_pct != null) parts.push(`MaxP ${bc.max_profit_pct}%`);
  if (bc.margin_target_pct != null) parts.push(`Mgn ${bc.margin_target_pct}%`);
  if (bc.hold_duration && bc.hold_duration !== 'natural') parts.push(`hold ${bc.hold_duration}`);
  if (!parts.length) return 'natural';
  return parts.join(' + ');
}

export function MMonthSweepDashboard() {
  const [meta, setMeta] = useState<MMonthMeta | null>(null);
  const [summary, setSummary] = useState<MMonthSummary | null>(null);
  const [bestCombo, setBestCombo] = useState<MMonthBestComboResponse | null>(null);
  const [cycle, setCycle] = useState<MMonthTradeCycle>('monthly');
  const [holdDuration, setHoldDuration] = useState<string>('natural');
  const [premiumSlPct, setPremiumSlPct] = useState<number | ''>('');
  const [maxProfitPct, setMaxProfitPct] = useState<number | ''>('');
  const [marginTargetPct, setMarginTargetPct] = useState<number | ''>('');
  const [primary, setPrimary] = useState<string>('avg_net_pnl');
  const [secondary, setSecondary] = useState<string>('');
  const [showGrid, setShowGrid] = useState<boolean>(false);
  const [primaries, setPrimaries] = useState<string[]>([]);
  const [holdDurationsAvailable, setHoldDurationsAvailable] = useState<string[]>([]);
  const [slMenu, setSlMenu] = useState<number[]>([]);
  const [maxProfitMenu, setMaxProfitMenu] = useState<number[]>([]);
  const [marginMenu, setMarginMenu] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // Initial meta + primaries
  useEffect(() => {
    (async () => {
      try {
        const [m, p] = await Promise.all([
          m_month_api.fetchMeta(),
          m_month_api.fetchAvailablePrimaryMetrics(),
        ]);
        setMeta(m);
        setPrimaries(p.primaries);
        setHoldDurationsAvailable(p.hold_durations || []);
        setSlMenu(p.premium_sl_pct_menu || []);
        setMaxProfitMenu(p.max_profit_pct_menu || []);
        setMarginMenu(p.margin_target_pct_menu || []);
      } catch (e: any) {
        setError(String(e?.message || e));
      }
    })();
  }, []);

  // Re-fetch summary + best combo when any rule param changes
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const [s, bc] = await Promise.all([
          m_month_api.fetchSummary(cycle),
          m_month_api.fetchBestCombo({
            trade_cycle: cycle,
            hold_duration: holdDuration,
            premium_sl_pct: premiumSlPct === '' ? undefined : premiumSlPct,
            max_profit_pct: maxProfitPct === '' ? undefined : maxProfitPct,
            margin_target_pct: marginTargetPct === '' ? undefined : marginTargetPct,
            primary,
            secondary: secondary || undefined,
            include_grid: showGrid,
          }),
        ]);
        if (cancelled) return;
        setSummary(s);
        setBestCombo(bc);
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(String(e?.message || e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [cycle, holdDuration, premiumSlPct, maxProfitPct, marginTargetPct,
      primary, secondary, showGrid]);

  return (
    <div style={{ padding: 20, color: '#c8d6e5', fontSize: 13 }}>
      <h2 style={{ marginTop: 0, color: '#fff' }}>
        M-Month — Monthly + Bimonthly + Last-Fri-Rolling Sweep
      </h2>

      {/* Cycle toggle */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 6, color: '#7a9bb5', fontSize: 12 }}>Trade cycle</div>
        <div style={{ display: 'inline-flex', background: '#0d1421',
                      border: '1px solid #1a2d42', borderRadius: 6, overflow: 'hidden' }}>
          {(['monthly', 'bimonthly', 'lastfri_monthly', 'lastfri_bimonthly', 'all'] as MMonthTradeCycle[]).map(c => (
            <button
              key={c}
              onClick={() => setCycle(c)}
              style={{
                padding: '6px 14px', fontSize: 12, fontWeight: 600,
                background: cycle === c ? '#1f6feb' : 'transparent',
                color: cycle === c ? '#fff' : '#7a9bb5',
                border: 'none', cursor: 'pointer',
              }}
            >
              {CYCLE_LABELS[c]}
            </button>
          ))}
        </div>
        <div style={{ marginTop: 8, fontSize: 11, color: '#7a9bb5', maxWidth: 720 }}>
          {CYCLE_DESCRIPTIONS[cycle]}
        </div>
      </div>

      {error && (
        <div style={{ padding: 12, background: '#2d0d0d', border: '1px solid #5a1f1f',
                      borderRadius: 6, marginBottom: 12, color: '#f88' }}>
          {error}
        </div>
      )}

      {/* Headline strip */}
      {summary && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
          <Kpi label="# Trades" value={summary.n_trades.toLocaleString()} />
          <Kpi label="# Anchors" value={summary.n_anchors.toLocaleString()} />
          <Kpi label="# Cycles in view" value={summary.n_cycles.toLocaleString()} />
          <Kpi label="Avg Credit (per trade)" value={fmtUsd(summary.avg_credit_usd, 0)} />
          <Kpi label="Avg Margin (per trade)" value={fmtUsd(summary.avg_margin_usd, 0)} />
          <Kpi label="Avg DTE (days)" value={summary.avg_dte_days.toFixed(1)} />
        </div>
      )}

      {/* Meta info */}
      {meta && (
        <div style={{ fontSize: 11, color: '#7a9bb5', marginBottom: 16 }}>
          Data range: {meta.first_anchor_date} → {meta.last_anchor_date} ·
          {' '}Total trades on disk: {meta.n_trades.toLocaleString()} ·
          {' '}IV bands: {meta.iv_bands.join(', ') || '—'} ·
          {' '}Deltas: {meta.deltas.map(d => d.toFixed(2)).join(', ')} ·
          {' '}Months: {meta.entry_months.length}
        </div>
      )}

      {/* Exit-rule controls (Phase B — 96-rule menu) */}
      <div style={{
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        padding: '12px 14px', marginBottom: 12, display: 'flex',
        gap: 16, alignItems: 'center', flexWrap: 'wrap',
      }}>
        <strong style={{ color: '#fff', fontSize: 12 }}>Exit rules:</strong>
        <label style={{ fontSize: 11, color: '#7a9bb5' }}>
          Hold duration:
          <select value={holdDuration} onChange={e => setHoldDuration(e.target.value)}
                  style={dropdownStyle}>
            {holdDurationsAvailable.map(d => (
              <option key={d} value={d}>{d === 'natural' ? 'Natural (cycle exit)' : d}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 11, color: '#7a9bb5' }}>
          Premium SL %:
          <select value={premiumSlPct} onChange={e =>
                    setPremiumSlPct(e.target.value === '' ? '' : Number(e.target.value))}
                  style={dropdownStyle}>
            <option value="">— off —</option>
            {slMenu.map(v => <option key={v} value={v}>{v}%</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11, color: '#7a9bb5' }}>
          Max profit %:
          <select value={maxProfitPct} onChange={e =>
                    setMaxProfitPct(e.target.value === '' ? '' : Number(e.target.value))}
                  style={dropdownStyle}>
            <option value="">— off —</option>
            {maxProfitMenu.map(v => <option key={v} value={v}>{v}%</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11, color: '#7a9bb5' }}>
          Margin target %:
          <select value={marginTargetPct} onChange={e =>
                    setMarginTargetPct(e.target.value === '' ? '' : Number(e.target.value))}
                  style={dropdownStyle}>
            <option value="">— off —</option>
            {marginMenu.map(v => <option key={v} value={v}>{v}%</option>)}
          </select>
        </label>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: '#5a7a9a' }}>
          Whichever rule fires first wins. Multiple can be active.
        </span>
      </div>

      {/* Best combo table */}
      <div style={{ marginBottom: 8, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, color: '#fff' }}>Best combo per IV band</h3>
        <span style={{ fontSize: 11, color: '#7a9bb5' }}>
          (rule: {bestCombo ? exitRuleLabel(bestCombo) : '...'})
        </span>
        <span style={{ flex: 1 }} />
        <label style={{ fontSize: 11, color: '#7a9bb5' }}>
          Primary:
          <select value={primary} onChange={e => setPrimary(e.target.value)}
                  style={dropdownStyle}>
            {primaries.map(p => (
              <option key={p} value={p}>{PRIMARY_METRIC_LABELS[p] || p}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 11, color: '#7a9bb5' }}>
          Tiebreak:
          <select value={secondary} onChange={e => setSecondary(e.target.value)}
                  style={dropdownStyle}>
            <option value="">— none —</option>
            {primaries.filter(p => p !== primary).map(p => (
              <option key={p} value={p}>{PRIMARY_METRIC_LABELS[p] || p}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 11, color: '#7a9bb5' }}>
          <input type="checkbox" checked={showGrid} onChange={e => setShowGrid(e.target.checked)} />
          {' '}Show full grid
        </label>
      </div>

      <BestComboTable rows={bestCombo?.best_per_band || []} primary={primary} loading={loading} />

      {showGrid && bestCombo?.grid && (
        <>
          <h3 style={{ color: '#fff', marginTop: 24 }}>Full grid ({bestCombo.grid.length} cells)</h3>
          <BestComboTable rows={bestCombo.grid} primary={primary} loading={loading} />
        </>
      )}

      <div style={{ marginTop: 32, fontSize: 11, color: '#5a7a9a' }}>
        <strong>Stage 1 scope:</strong> on-the-fly aggregation from m_month_trades.parquet
        with hard-cap exit (hold to last path bar). Stage 2 will land: 96-rule exit menu
        (3 SL × 32 rule variants including the new 11-slot fixed_hold_duration sub-family),
        composite score, capital sizing, drilldowns, IV-band coverage table.
        Stage 3 will add the adjustment engine.
      </div>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
      padding: '8px 14px', minWidth: 120,
    }}>
      <div style={{ fontSize: 11, color: '#7a9bb5', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 16, color: '#fff', fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function BestComboTable({ rows, primary, loading }: {
  rows: MMonthBestComboRow[]; primary: string; loading: boolean;
}) {
  return (
    <div style={{
      background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
      overflowX: 'auto',
    }}>
      {loading && rows.length === 0 ? (
        <div style={{ padding: 20, color: '#7a9bb5' }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: 20, color: '#7a9bb5' }}>
          No data. Run the backtester first:{' '}
          <code style={{ color: '#c8d6e5' }}>
            docker exec docker-backend-1 python -m app.analytics.m_month_batch_backtester
          </code>
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: '#0a1018', color: '#7a9bb5' }}>
              <Th>Cycle</Th><Th>IV band</Th><Th>Δ</Th><Th>Dow</Th><Th>Hour</Th>
              <Th right>#Trd</Th><Th right>Win%</Th>
              <Th right>Avg Net</Th><Th right>Total Net</Th>
              <Th right>Avg Cred</Th><Th right>Avg Margin</Th>
              <Th right>Avg %Cr</Th><Th right>Avg %Mr</Th>
              <Th right>DTE</Th>
              <Th right>Avg Max MTM</Th><Th right>Avg Min MTM</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ borderTop: '1px solid #1a2d42' }}>
                <Td>{r.trade_cycle}</Td>
                <Td>{r.iv_band}</Td>
                <Td>{r.delta_target.toFixed(2)}</Td>
                <Td>{r.entry_dow}</Td>
                <Td>{String(r.entry_hour_ist).padStart(2, '0')}:00</Td>
                <Td right>{r.n_trades}</Td>
                <Td right>{fmtRate(r.win_rate)}</Td>
                <Td right color={pnlColor(r.avg_net_pnl)} bold={primary === 'avg_net_pnl'}>
                  {fmtUsd(r.avg_net_pnl)}
                </Td>
                <Td right color={pnlColor(r.total_net_pnl)} bold={primary === 'total_net_pnl'}>
                  {fmtUsd(r.total_net_pnl)}
                </Td>
                <Td right>{fmtUsd(r.avg_credit_usd, 0)}</Td>
                <Td right>{fmtUsd(r.avg_margin_usd, 0)}</Td>
                <Td right color={pnlColor(r.avg_pct_return_on_credit)}>
                  {fmtPct(r.avg_pct_return_on_credit)}
                </Td>
                <Td right color={pnlColor(r.avg_pct_return_on_margin)}>
                  {fmtPct(r.avg_pct_return_on_margin)}
                </Td>
                <Td right>{r.avg_dte_days ? r.avg_dte_days.toFixed(1) : '—'}</Td>
                <Td right color={pnlColor(r.avg_max_mtm)}>{fmtUsd(r.avg_max_mtm)}</Td>
                <Td right color={pnlColor(r.avg_min_mtm)}>{fmtUsd(r.avg_min_mtm)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th style={{
      padding: '8px 10px', textAlign: right ? 'right' : 'left',
      fontWeight: 600, whiteSpace: 'nowrap', borderBottom: '1px solid #1a2d42',
    }}>{children}</th>
  );
}

function Td({ children, right, color, bold }: {
  children: React.ReactNode; right?: boolean; color?: string; bold?: boolean;
}) {
  return (
    <td style={{
      padding: '6px 10px',
      textAlign: right ? 'right' : 'left',
      color: color || '#c8d6e5',
      fontWeight: bold ? 700 : undefined,
      whiteSpace: 'nowrap',
    }}>{children}</td>
  );
}
