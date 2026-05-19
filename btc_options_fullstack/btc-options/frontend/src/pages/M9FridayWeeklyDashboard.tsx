import React, { useEffect, useState } from 'react';
import {
  m9_api,
  M9Meta,
  M9Summary,
  M9BestComboRow,
  M9BestComboResponse,
  M9ExpiryType,
} from '../services/m9_api';

const EXPIRY_TYPE_LABELS: Record<M9ExpiryType, string> = {
  weekly: 'Weekly',
  biweekly: 'Biweekly',
  all: 'All',
};

const EXPIRY_TYPE_DESCRIPTIONS: Record<M9ExpiryType, string> = {
  weekly: 'Sell the smallest Friday-expiry with DTE ≥ 7 days at entry. Typically next Friday. ~7 DTE at entry → ~1 DTE after 6-day hold.',
  biweekly: 'Sell the smallest Friday-expiry with DTE ≥ 14 days at entry. Typically next-to-next Friday. ~14 DTE at entry → ~8 DTE after 6-day hold.',
  all: 'Combined view across both weekly and biweekly trades.',
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

const HOLD_DURATION_LABELS: Record<string, string> = {
  natural: 'Natural (full 6-day cap)',
  '1d': '1 day',
  '2d': '2 days',
  '3d': '3 days',
  '4d': '4 days',
  '5d': '5 days',
  '6d': '6 days',
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

function fmtEntryTime(h: number, m: number): string {
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

const dropdownStyle: React.CSSProperties = {
  marginLeft: 6, background: '#0d1421', color: '#c8d6e5',
  border: '1px solid #1a2d42', borderRadius: 4, padding: '3px 8px',
};

function exitRuleLabel(bc: M9BestComboResponse): string {
  const parts: string[] = [];
  if (bc.premium_sl_pct != null) parts.push(`SL ${bc.premium_sl_pct}%`);
  if (bc.max_profit_pct != null) parts.push(`MaxP ${bc.max_profit_pct}%`);
  if (bc.margin_target_pct != null) parts.push(`Mgn ${bc.margin_target_pct}%`);
  if (bc.hold_duration && bc.hold_duration !== 'natural') parts.push(`hold ${bc.hold_duration}`);
  if (!parts.length) return 'natural';
  return parts.join(' + ');
}

export function M9FridayWeeklyDashboard() {
  const [meta, setMeta] = useState<M9Meta | null>(null);
  const [summary, setSummary] = useState<M9Summary | null>(null);
  const [bestCombo, setBestCombo] = useState<M9BestComboResponse | null>(null);
  const [expiryType, setExpiryType] = useState<M9ExpiryType>('weekly');
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
  // Filter bar
  const [fIvBand, setFIvBand] = useState('');
  const [fDelta, setFDelta] = useState('');
  const [fEntryHour, setFEntryHour] = useState('');
  const [fEntryMinute, setFEntryMinute] = useState('');
  const [fEntryFriday, setFEntryFriday] = useState('');
  const [fDteBucket, setFDteBucket] = useState('');
  const [fIvpBucket, setFIvpBucket] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // Initial meta + primaries
  useEffect(() => {
    (async () => {
      try {
        const [m, p] = await Promise.all([
          m9_api.fetchMeta(),
          m9_api.fetchAvailablePrimaryMetrics(),
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

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const [s, bc] = await Promise.all([
          m9_api.fetchSummary(expiryType),
          m9_api.fetchBestCombo({
            expiry_type: expiryType,
            hold_duration: holdDuration,
            premium_sl_pct: premiumSlPct === '' ? undefined : premiumSlPct,
            max_profit_pct: maxProfitPct === '' ? undefined : maxProfitPct,
            margin_target_pct: marginTargetPct === '' ? undefined : marginTargetPct,
            iv_band: fIvBand || undefined,
            delta_target: fDelta || undefined,
            entry_hour: fEntryHour || undefined,
            entry_minute: fEntryMinute || undefined,
            entry_friday: fEntryFriday || undefined,
            dte_bucket: fDteBucket || undefined,
            ivp_bucket: fIvpBucket || undefined,
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
  }, [expiryType, holdDuration, premiumSlPct, maxProfitPct, marginTargetPct,
      fIvBand, fDelta, fEntryHour, fEntryMinute, fEntryFriday,
      fDteBucket, fIvpBucket,
      primary, secondary, showGrid]);

  const clearFilters = () => {
    setFIvBand(''); setFDelta(''); setFEntryHour(''); setFEntryMinute('');
    setFEntryFriday(''); setFDteBucket(''); setFIvpBucket('');
  };

  return (
    <div style={{ padding: 20, color: '#c8d6e5', fontSize: 13 }}>
      <h2 style={{ marginTop: 0, color: '#fff' }}>
        M9 — Friday Weekly / Biweekly Sweep
      </h2>

      {/* Expiry-type toggle */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 6, color: '#7a9bb5', fontSize: 12 }}>Expiry type</div>
        <div style={{ display: 'inline-flex', background: '#0d1421',
                      border: '1px solid #1a2d42', borderRadius: 6, overflow: 'hidden' }}>
          {(['weekly', 'biweekly', 'all'] as M9ExpiryType[]).map(c => (
            <button
              key={c}
              onClick={() => setExpiryType(c)}
              style={{
                padding: '6px 14px', fontSize: 12, fontWeight: 600,
                background: expiryType === c ? '#1f6feb' : 'transparent',
                color: expiryType === c ? '#fff' : '#7a9bb5',
                border: 'none', cursor: 'pointer',
              }}
            >
              {EXPIRY_TYPE_LABELS[c]}
            </button>
          ))}
        </div>
        <div style={{ marginTop: 8, fontSize: 11, color: '#7a9bb5', maxWidth: 720 }}>
          {EXPIRY_TYPE_DESCRIPTIONS[expiryType]}
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
          <Kpi label="# Fridays" value={summary.n_fridays.toLocaleString()} />
          <Kpi label="Avg Credit (per trade)" value={fmtUsd(summary.avg_credit_usd, 0)} />
          <Kpi label="Avg Margin (per trade)" value={fmtUsd(summary.avg_margin_usd, 0)} />
          <Kpi label="Avg DTE (days)" value={summary.avg_dte_days.toFixed(1)} />
        </div>
      )}

      {/* Meta info */}
      {meta && (
        <div style={{ fontSize: 11, color: '#7a9bb5', marginBottom: 16 }}>
          Data range: {meta.first_friday} → {meta.last_friday} ·
          {' '}Total trades on disk: {meta.n_trades.toLocaleString()} ·
          {' '}IV bands: {meta.iv_bands.join(', ') || '—'} ·
          {' '}Deltas: {meta.deltas.map(d => d.toFixed(2)).join(', ')} ·
          {' '}Entry slots: {meta.entry_time_labels.join(', ')}
        </div>
      )}

      {/* Filter bar */}
      <div style={{
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        padding: '10px 14px', marginBottom: 8, display: 'flex',
        gap: 12, alignItems: 'center', flexWrap: 'wrap',
      }}>
        <strong style={{ color: '#fff', fontSize: 12 }}>Filters:</strong>
        <FilterSelect label="IV band" value={fIvBand} setValue={setFIvBand}
          options={meta?.iv_bands || []} />
        <FilterSelect label="Δ target" value={fDelta} setValue={setFDelta}
          options={(meta?.deltas || []).map(d => d.toFixed(2))} />
        <FilterSelect label="Entry hour" value={fEntryHour} setValue={setFEntryHour}
          options={(meta?.entry_hours || []).map(h => `${h}`)}
          render={v => `${String(v).padStart(2,'0')} IST`} />
        <FilterSelect label="Entry minute" value={fEntryMinute} setValue={setFEntryMinute}
          options={(meta?.entry_minutes || []).map(m => `${m}`)} />
        <FilterSelect label="DTE bucket" value={fDteBucket} setValue={setFDteBucket}
          options={meta?.dte_buckets || []} />
        <FilterSelect label="IVP bucket" value={fIvpBucket} setValue={setFIvpBucket}
          options={meta?.ivp_buckets || []} />
        <FilterSelect label="Friday" value={fEntryFriday} setValue={setFEntryFriday}
          options={meta?.entry_fridays || []} />
        <span style={{ flex: 1 }} />
        <button onClick={clearFilters} style={{
          fontSize: 11, padding: '4px 10px', background: '#1a2d42',
          color: '#c8d6e5', border: '1px solid #2a4d62', borderRadius: 4,
          cursor: 'pointer',
        }}>Clear all</button>
      </div>

      {/* Exit-rule controls */}
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
              <option key={d} value={d}>{HOLD_DURATION_LABELS[d] || d}</option>
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

      {/* Best-combo controls */}
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
        <strong>Stage 1 scope:</strong> Friday entries 17:30 / 18 / 19 / 20 / 21 / 22 / 23 / 00 IST,
        deltas 0.10 / 0.20 / 0.30 / 0.40 / 0.50, single 6-day walk per trade, 1d–6d &amp; natural
        exits derived via composite SL / max-profit / margin-target / hold-duration rule.
        Stage 2 will land drilldown modals, Pro Metrics columns, Missed-Fridays table.
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
  rows: M9BestComboRow[]; primary: string; loading: boolean;
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
            docker compose run -d --rm --name m9_$(date +%s) backend
            {' '}python -m app.analytics.m9_friday_weekly_backtester
          </code>
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: '#0a1018', color: '#7a9bb5' }}>
              <Th>Type</Th><Th>IV band</Th><Th>Δ</Th><Th>Entry IST</Th><Th>Hold</Th>
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
                <Td>{r.expiry_type}</Td>
                <Td>{r.iv_band}</Td>
                <Td>{r.delta_target.toFixed(2)}</Td>
                <Td>{fmtEntryTime(r.entry_hour_ist, r.entry_minute_ist)}</Td>
                <Td>{r.hold_duration}</Td>
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

function FilterSelect({
  label, value, setValue, options, render,
}: {
  label: string;
  value: string;
  setValue: (v: string) => void;
  options: (string | number)[];
  render?: (v: string | number) => string;
}) {
  return (
    <label style={{ fontSize: 11, color: '#7a9bb5' }}>
      {label}:
      <select value={value} onChange={e => setValue(e.target.value)}
              style={dropdownStyle}>
        <option value="">All</option>
        {options.map(o => (
          <option key={String(o)} value={String(o)}>
            {render ? render(o) : String(o)}
          </option>
        ))}
      </select>
    </label>
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
