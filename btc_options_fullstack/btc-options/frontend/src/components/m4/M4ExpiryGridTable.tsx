/**
 * Per-contract-type × IV band × Δ aggregation table.
 *
 * Each row = one (contract_type × IV bucket × Δ target) cell from M4 dataset.
 * Columns: n, WR, max/min MTM, gross/net P&L, slippage + brokerage breakdown,
 * margin, credit %.
 *
 * Backed by /api/v1/m4/{contract_type_summary, expiry_grid}.
 */
import { useEffect, useMemo, useState } from 'react';
import {
  fetchContractTypeSummary, fetchExpiryGrid, fetchExpiryList, fetchExpiryWinners,
  type ContractTypeSummaryRow, type ExpiryGridCell,
  type ExpiryListEntry, type ExpiryWinnerRow,
} from '../../services/m4_api';

const CONTRACT_ORDER = [
  'current', 'next', 'next_to_next', 'weekly', 'biweekly',
  'three_week', 'monthly', 'bimonthly', 'quarterly',
];

const CONTRACT_LABEL: Record<string, string> = {
  current: 'Current (~0.8d)', next: 'Next (~1.8d)',
  next_to_next: 'Next-to-next (~2.8d)', weekly: 'Weekly (~7d)',
  biweekly: 'Biweekly (~14d)', three_week: 'Three-week (~21d)',
  monthly: 'Monthly (~28d)', bimonthly: 'Bimonthly (~52d)',
  quarterly: 'Quarterly (~70d)',
};

type SortKey =
  | 'iv_band' | 'delta_target' | 'n' | 'win_rate'
  | 'net_pnl_avg' | 'net_pnl_sum' | 'gross_pnl_avg'
  | 'max_mtm_avg' | 'max_mtm_best'
  | 'min_mtm_avg' | 'min_mtm_worst'
  | 'slippage_avg_total' | 'brokerage_avg_total' | 'cost_avg_total'
  | 'margin_avg' | 'credit_pct_avg' | 'sl_rate'
  | 'n_sl' | 'sl_mtm_avg' | 'sl_mtm_total' | 'sl_net_avg' | 'sl_net_total'
  // Winner/loser split columns
  | 'n_wins' | 'win_min_mtm_worst' | 'win_net_best' | 'win_net_avg' | 'win_net_lowest'
  | 'n_losses' | 'loss_max_mtm_best' | 'loss_net_worst' | 'loss_net_avg' | 'loss_net_lowest';

const fmt = (v: number, d = 2) =>
  Number.isFinite(v) ? v.toFixed(d) : '—';
const fmtUsd = (v: number, d = 2) =>
  Number.isFinite(v)
    ? `$${v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`
    : '—';
const fmtUsdSigned = (v: number, d = 2) => {
  if (!Number.isFinite(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}$${v.toFixed(d)}`;
};
const fmtPct = (v: number, d = 1) =>
  Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '—';

const pnlColor = (v: number): string => {
  if (!Number.isFinite(v)) return '#7a9bb5';
  if (v >= 5) return '#10b981';
  if (v > 0) return '#22c55e';
  if (v >= -2) return '#cdd6e0';
  return '#ef4444';
};

export function M4ExpiryGridTable() {
  const [summary, setSummary] = useState<ContractTypeSummaryRow[]>([]);
  const [cells, setCells] = useState<ExpiryGridCell[]>([]);
  const [allExpiries, setAllExpiries] = useState<ExpiryListEntry[]>([]);
  const [winners, setWinners] = useState<ExpiryWinnerRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeContracts, setActiveContracts] = useState<Set<string>>(
    new Set(['current', 'next', 'next_to_next', 'weekly', 'biweekly']),
  );
  // Expiry-date filter (multi-select from /expiry_list). Empty = no filter.
  const [activeExpiries, setActiveExpiries] = useState<Set<string>>(new Set());
  const [showWinners, setShowWinners] = useState(true);
  const [minN, setMinN] = useState(1);
  const [sortKey, setSortKey] = useState<SortKey>('net_pnl_avg');
  const [sortDesc, setSortDesc] = useState(true);
  const [showLosers, setShowLosers] = useState(true);

  // Load static endpoints once
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchContractTypeSummary(),
      fetchExpiryList(),
      fetchExpiryWinners(1),
    ])
      .then(([s, e, w]) => {
        if (cancelled) return;
        setSummary(s.rows); setAllExpiries(e.expiries); setWinners(w.rows);
      })
      .catch(err => { if (!cancelled) setError(String(err?.message ?? err)); });
    return () => { cancelled = true; };
  }, []);

  // Load grid (re-fires when filters change)
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const opts: { minN: number; expiries?: string[] } = { minN };
    if (activeExpiries.size > 0) opts.expiries = Array.from(activeExpiries);
    fetchExpiryGrid(opts)
      .then(g => { if (!cancelled) { setCells(g.rows); setError(null); } })
      .catch(e => { if (!cancelled) setError(String(e?.message ?? e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [minN, activeExpiries]);

  const filteredSorted = useMemo(() => {
    let rows = cells.filter(c => activeContracts.has(c.contract_type));
    if (!showLosers) rows = rows.filter(c => c.net_pnl_avg > 0);
    rows.sort((a, b) => {
      const av = a[sortKey] as number | string;
      const bv = b[sortKey] as number | string;
      const cmp = typeof av === 'number' && typeof bv === 'number'
        ? (av as number) - (bv as number)
        : String(av).localeCompare(String(bv));
      return sortDesc ? -cmp : cmp;
    });
    return rows;
  }, [cells, activeContracts, sortKey, sortDesc, showLosers]);

  const onSort = (k: SortKey) => {
    if (k === sortKey) setSortDesc(d => !d);
    else { setSortKey(k); setSortDesc(true); }
  };

  const toggleContract = (ct: string) => {
    setActiveContracts(prev => {
      const next = new Set(prev);
      if (next.has(ct)) next.delete(ct); else next.add(ct);
      return next;
    });
  };

  // Group all expiries by contract_type, with totals per class.
  const expiriesByClass = useMemo(() => {
    const m = new Map<string, ExpiryListEntry[]>();
    for (const e of allExpiries) {
      const ct = e.contract_type;
      if (!m.has(ct)) m.set(ct, []);
      m.get(ct)!.push(e);
    }
    return m;
  }, [allExpiries]);

  // Class-level chip suggestions. Each chip = one expiry-class with aggregate counts.
  const classSuggestions = useMemo(() => {
    return CONTRACT_ORDER
      .map(ct => {
        const list = expiriesByClass.get(ct) ?? [];
        if (!list.length) return null;
        const trades = list.reduce((a, e) => a + e.n_trades, 0);
        const dates = list.map(e => e.expiry);
        return { ct, n_expiries: list.length, n_trades: trades, dates };
      })
      .filter((x): x is { ct: string; n_expiries: number; n_trades: number; dates: string[] } => !!x);
  }, [expiriesByClass]);

  // Selecting/deselecting a CLASS toggles all its expiry dates in the filter set.
  const toggleClass = (ct: string, dates: string[]) => {
    setActiveExpiries(prev => {
      const next = new Set(prev);
      const allOn = dates.every(d => next.has(d));
      if (allOn) dates.forEach(d => next.delete(d));
      else dates.forEach(d => next.add(d));
      return next;
    });
  };

  const isClassActive = (dates: string[]): 'all' | 'some' | 'none' => {
    const on = dates.filter(d => activeExpiries.has(d)).length;
    if (on === 0) return 'none';
    if (on === dates.length) return 'all';
    return 'some';
  };

  // Winners panel: filter to contract types user has selected
  const winnersFiltered = useMemo(
    () => winners.filter(w => activeContracts.has(w.contract_type)),
    [winners, activeContracts],
  );

  return (
    <div style={{ marginTop: 16 }}>
      {/* Section title */}
      <div style={{
        fontSize: 14, fontWeight: 700, color: '#cdd6e0', marginBottom: 8,
        display: 'flex', alignItems: 'baseline', gap: 12,
      }}>
        <span>Expiry × IV × Δ grid</span>
        <span style={{ fontSize: 11, fontWeight: 400, color: '#7a9bb5' }}>
          Per-cell win rate, MFE/MAE, gross/net P&L, slippage + brokerage,
          margin used. Costs split 50/50 entry vs exit (M4 stored round-trip totals).
        </span>
      </div>

      {/* Contract type summary strip */}
      <div style={{
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        padding: 12, marginBottom: 12, overflowX: 'auto',
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1a2d42' }}>
              <th style={summaryThStyle}>Contract</th>
              <th style={summaryThStyle}>n</th>
              <th style={summaryThStyle}>Fri</th>
              <th style={summaryThStyle}>WR</th>
              <th style={summaryThStyle}>SL</th>
              <th style={summaryThStyle} title="SL trigger MTM avg / total ($)">SL MTM</th>
              <th style={summaryThStyle} title="SL realized net P&L avg / total ($)">SL Net</th>
              <th style={summaryThStyle}>Avg Net</th>
              <th style={summaryThStyle} title="Average net P&L across only the winning trades">Avg Win</th>
              <th style={summaryThStyle} title="Average net P&L across only the losing trades">Avg Loss</th>
              <th style={summaryThStyle} title="Single best trade net P&L in this contract">Best Net</th>
              <th style={summaryThStyle} title="Single worst trade net P&L in this contract">Worst Net</th>
              <th style={summaryThStyle}>Total Net</th>
              <th style={summaryThStyle}>Avg Gross</th>
              <th style={summaryThStyle}>Avg MFE</th>
              <th style={summaryThStyle} title="Best single MFE — biggest upside reached during any trade">Best MFE</th>
              <th style={summaryThStyle}>Avg MAE</th>
              <th style={summaryThStyle} title="Worst single MAE — deepest dip during any trade">Worst MAE</th>
              <th style={summaryThStyle}>Avg Cost</th>
              <th style={summaryThStyle}>Show</th>
            </tr>
          </thead>
          <tbody>
            {CONTRACT_ORDER
              .map(ct => summary.find(r => r.contract_type === ct))
              .filter((r): r is ContractTypeSummaryRow => !!r)
              .map(r => (
              <tr key={r.contract_type} style={{ borderTop: '1px solid #11202c' }}>
                <td style={summaryTdStyle}>
                  <span style={{ fontWeight: 600 }}>
                    {CONTRACT_LABEL[r.contract_type] || r.contract_type}
                  </span>
                </td>
                <td style={summaryTdStyle}>{r.n_trades}</td>
                <td style={summaryTdStyle}>{r.n_fridays}</td>
                <td style={{ ...summaryTdStyle, color: r.win_rate >= 0.55 ? '#10b981' : '#f0b429' }}>
                  {fmtPct(r.win_rate)}
                </td>
                <td style={summaryTdStyle}>{fmtPct(r.sl_rate)} ({r.n_sl})</td>
                <td style={summaryTdStyle}>
                  {r.n_sl > 0 ? (
                    <span style={{ color: '#ef4444' }} title={`Σ ${fmtUsdSigned(r.sl_mtm_total, 0)}`}>
                      {fmtUsdSigned(r.sl_mtm_avg)}
                    </span>
                  ) : '—'}
                </td>
                <td style={summaryTdStyle}>
                  {r.n_sl > 0 ? (
                    <span style={{ color: pnlColor(r.sl_net_avg), fontWeight: 700 }}
                          title={`Σ ${fmtUsdSigned(r.sl_net_total, 0)}`}>
                      {fmtUsdSigned(r.sl_net_avg)}
                    </span>
                  ) : '—'}
                </td>
                <td style={{ ...summaryTdStyle, color: pnlColor(r.avg_net_pnl), fontWeight: 700 }}>
                  {fmtUsdSigned(r.avg_net_pnl)}
                </td>
                <td style={{ ...summaryTdStyle, color: '#10b981' }}
                    title={`${r.n_wins} winning trades`}>
                  {r.n_wins > 0 ? fmtUsdSigned(r.avg_net_win) : '—'}
                </td>
                <td style={{ ...summaryTdStyle, color: '#ef4444' }}
                    title={`${r.n_losses} losing trades`}>
                  {r.n_losses > 0 ? fmtUsdSigned(r.avg_net_loss) : '—'}
                </td>
                <td style={{ ...summaryTdStyle, color: '#10b981', fontWeight: 600 }}>
                  {fmtUsdSigned(r.best_net_pnl, 0)}
                </td>
                <td style={{ ...summaryTdStyle, color: '#ef4444', fontWeight: 600 }}>
                  {fmtUsdSigned(r.worst_net_pnl, 0)}
                </td>
                <td style={{ ...summaryTdStyle, color: pnlColor(r.total_net_pnl / 100), fontWeight: 700 }}>
                  {fmtUsdSigned(r.total_net_pnl, 0)}
                </td>
                <td style={summaryTdStyle}>{fmtUsdSigned(r.avg_gross_pnl)}</td>
                <td style={{ ...summaryTdStyle, color: '#10b981' }}>{fmtUsdSigned(r.avg_max_mtm)}</td>
                <td style={{ ...summaryTdStyle, color: '#10b981', fontWeight: 600 }}>
                  {fmtUsdSigned(r.best_max_mtm, 0)}
                </td>
                <td style={{ ...summaryTdStyle, color: '#ef4444' }}>{fmtUsdSigned(r.avg_min_mtm)}</td>
                <td style={{ ...summaryTdStyle, color: '#ef4444', fontWeight: 600 }}>
                  {fmtUsdSigned(r.worst_min_mtm, 0)}
                </td>
                <td style={summaryTdStyle}>{fmtUsd(r.avg_cost)}</td>
                <td style={summaryTdStyle}>
                  <input
                    type="checkbox"
                    checked={activeContracts.has(r.contract_type)}
                    onChange={() => toggleContract(r.contract_type)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Winners panel — best Δ per (contract_type × IV band) */}
      {showWinners && winnersFiltered.length > 0 && (
        <div style={{
          background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
          padding: 12, marginBottom: 12,
        }}>
          <div style={{
            display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 6,
          }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: '#cdd6e0' }}>
              Best Δ per (contract × IV band)
            </span>
            <span style={{ fontSize: 10, color: '#7a9bb5' }}>
              Sorted desc by avg net P&L within each contract type. Cells with n &lt; 2 omitted.
            </span>
            <button
              onClick={() => setShowWinners(false)}
              style={{
                marginLeft: 'auto', background: 'transparent',
                border: '1px solid #1a2d42', color: '#7a9bb5',
                padding: '2px 8px', fontSize: 10, borderRadius: 4, cursor: 'pointer',
              }}
            >Hide</button>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #1a2d42' }}>
                  <th style={summaryThStyle}>Contract</th>
                  <th style={summaryThStyle}>IV %</th>
                  <th style={summaryThStyle}>Best Δ</th>
                  <th style={summaryThStyle}>n</th>
                  <th style={summaryThStyle}>WR</th>
                  <th style={summaryThStyle}>Avg Net</th>
                  <th style={summaryThStyle}>Total Net</th>
                  <th style={summaryThStyle}>Worst Δ in Band</th>
                </tr>
              </thead>
              <tbody>
                {winnersFiltered.map(w => (
                  <tr key={`${w.contract_type}|${w.iv_band}`}
                      style={{ borderTop: '1px solid #11202c' }}>
                    <td style={summaryTdStyle}>
                      <span style={{ fontWeight: 600 }}>
                        {CONTRACT_LABEL[w.contract_type] || w.contract_type}
                      </span>
                    </td>
                    <td style={summaryTdStyle}>{w.iv_band}</td>
                    <td style={{ ...summaryTdStyle, fontWeight: 700, color: '#10b981' }}>
                      {w.best.delta_target.toFixed(2)}
                    </td>
                    <td style={summaryTdStyle}>{w.best.n}</td>
                    <td style={{ ...summaryTdStyle, color: w.best.win_rate >= 0.55 ? '#10b981' : '#f0b429' }}>
                      {fmtPct(w.best.win_rate)}
                    </td>
                    <td style={{ ...summaryTdStyle, color: pnlColor(w.best.avg_net), fontWeight: 700 }}>
                      {fmtUsdSigned(w.best.avg_net)}
                    </td>
                    <td style={{ ...summaryTdStyle, color: pnlColor(w.best.total_net / 50), fontWeight: 700 }}>
                      {fmtUsdSigned(w.best.total_net, 0)}
                    </td>
                    <td style={{ ...summaryTdStyle, color: '#7a9bb5', fontSize: 10.5 }}>
                      {w.worst.delta_target !== w.best.delta_target ? (
                        <span>Δ {w.worst.delta_target.toFixed(2)} → {fmtUsdSigned(w.worst.avg_net)}</span>
                      ) : '— (only 1 Δ)'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {!showWinners && (
        <div style={{ marginBottom: 8 }}>
          <button
            onClick={() => setShowWinners(true)}
            style={{
              background: '#142537', border: '1px solid #1a2d42',
              color: '#cdd6e0', padding: '4px 10px', fontSize: 11,
              borderRadius: 4, cursor: 'pointer',
            }}
          >Show winners panel ↑</button>
        </div>
      )}

      {/* Expiry filter — class-level chips (current/next/weekly/…). Click to toggle. */}
      <div style={{
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        padding: 10, marginBottom: 8,
      }}>
        <div style={{
          display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6,
        }}>
          <span style={{ fontSize: 11, color: '#7a9bb5' }}>
            Filter by expiry class — click any chip to toggle:
          </span>
          <span style={{ fontSize: 10, color: '#7a9bb5' }}>
            {allExpiries.length} expiry dates across {classSuggestions.length} classes
          </span>
          {activeExpiries.size > 0 && (
            <button onClick={() => setActiveExpiries(new Set())}
                    style={{
                      marginLeft: 'auto', background: 'transparent',
                      border: '1px solid #1a2d42', color: '#7a9bb5',
                      padding: '2px 8px', fontSize: 10, borderRadius: 4,
                      cursor: 'pointer',
                    }}>
              Clear filter ({activeExpiries.size} dates)
            </button>
          )}
        </div>
        {/* Class chips — click to toggle the entire class into the filter */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {classSuggestions.map(c => {
            const state = isClassActive(c.dates);
            const bg = state === 'all' ? '#1f6feb'
                     : state === 'some' ? '#142537'
                     : '#0a1018';
            const border = state === 'none' ? '#1a2d42' : '#1f6feb';
            return (
              <button
                key={c.ct}
                onClick={() => toggleClass(c.ct, c.dates)}
                title={
                  `${c.n_expiries} expiry dates · ${c.n_trades} trades total\n` +
                  `Click to ${state === 'all' ? 'deselect' : 'select all'}`
                }
                style={{
                  background: bg,
                  border: `1px solid ${border}`,
                  color: state === 'none' ? '#cdd6e0' : '#fff',
                  padding: '4px 10px', fontSize: 11, borderRadius: 4,
                  cursor: 'pointer', fontWeight: state === 'all' ? 700 : 500,
                  display: 'inline-flex', gap: 6, alignItems: 'center',
                }}
              >
                <span>{CONTRACT_LABEL[c.ct] || c.ct}</span>
                <span style={{
                  fontSize: 9, color: state === 'none' ? '#7a9bb5' : '#cce6ff',
                  background: state === 'none' ? '#11202c' : 'rgba(255,255,255,0.12)',
                  padding: '0 5px', borderRadius: 3,
                }}>
                  {c.n_expiries}d / {c.n_trades}t
                </span>
                {state === 'some' && (
                  <span style={{ fontSize: 9, color: '#cce6ff' }}>
                    ({c.dates.filter(d => activeExpiries.has(d)).length}/{c.n_expiries})
                  </span>
                )}
              </button>
            );
          })}
          {classSuggestions.length === 0 && (
            <span style={{ fontSize: 10, color: '#7a9bb5' }}>
              No expiry classes loaded yet.
            </span>
          )}
        </div>
        {activeExpiries.size > 0 && (
          <div style={{
            fontSize: 10, color: '#7a9bb5', marginTop: 6,
            paddingTop: 6, borderTop: '1px solid #11202c',
          }}>
            Filtering grid to {activeExpiries.size} expiry dates from selected classes.
          </div>
        )}
      </div>

      {/* Filter bar */}
      <div style={{
        display: 'flex', gap: 16, alignItems: 'center', marginBottom: 8,
        fontSize: 12, color: '#7a9bb5',
      }}>
        <label>
          Min n / cell:{' '}
          <input
            type="number" min={1} max={50} value={minN}
            onChange={e => setMinN(Math.max(1, Number(e.target.value) || 1))}
            style={{
              width: 60, background: '#0a1018', color: '#cdd6e0',
              border: '1px solid #1a2d42', padding: '3px 6px',
              borderRadius: 4, fontSize: 12,
            }}
          />
        </label>
        <label>
          <input type="checkbox" checked={showLosers}
                 onChange={e => setShowLosers(e.target.checked)} />
          {' '}Show losing cells
        </label>
        <span style={{ marginLeft: 'auto' }}>
          {filteredSorted.length} cells {loading && '(refreshing…)'}
        </span>
        {error && <span style={{ color: '#fca5a5' }}>{error}</span>}
      </div>

      {/* Big detail table */}
      <div style={{
        background: '#0d1421', border: '1px solid #1a2d42', borderRadius: 6,
        overflow: 'auto', maxHeight: 720,
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
          <thead style={{ position: 'sticky', top: 0, zIndex: 1, background: '#0a1018' }}>
            <tr>
              <th style={thStyle}>Contract</th>
              <Th onClick={() => onSort('iv_band')}     mine="iv_band"     sortKey={sortKey} desc={sortDesc}>IV %</Th>
              <Th onClick={() => onSort('delta_target')} mine="delta_target" sortKey={sortKey} desc={sortDesc}>Δ</Th>
              <Th onClick={() => onSort('n')}            mine="n"            sortKey={sortKey} desc={sortDesc}>n</Th>
              <Th onClick={() => onSort('win_rate')}     mine="win_rate"     sortKey={sortKey} desc={sortDesc}>WR</Th>
              <Th onClick={() => onSort('sl_rate')}      mine="sl_rate"      sortKey={sortKey} desc={sortDesc}>SL</Th>
              <Th onClick={() => onSort('n_sl')}         mine="n_sl"         sortKey={sortKey} desc={sortDesc}>SL n</Th>
              <Th onClick={() => onSort('sl_mtm_avg')}   mine="sl_mtm_avg"   sortKey={sortKey} desc={sortDesc}>SL MTM avg</Th>
              <Th onClick={() => onSort('sl_mtm_total')} mine="sl_mtm_total" sortKey={sortKey} desc={sortDesc}>SL MTM Σ</Th>
              <Th onClick={() => onSort('sl_net_avg')}   mine="sl_net_avg"   sortKey={sortKey} desc={sortDesc}>SL Net avg</Th>
              <Th onClick={() => onSort('sl_net_total')} mine="sl_net_total" sortKey={sortKey} desc={sortDesc}>SL Net Σ</Th>

              {/* Winners-only columns */}
              <Th onClick={() => onSort('n_wins')}            mine="n_wins"            sortKey={sortKey} desc={sortDesc}>Win n</Th>
              <Th onClick={() => onSort('win_min_mtm_worst')} mine="win_min_mtm_worst" sortKey={sortKey} desc={sortDesc}>Win Worst MTM</Th>
              <Th onClick={() => onSort('win_net_best')}      mine="win_net_best"      sortKey={sortKey} desc={sortDesc}>Best Win</Th>
              <Th onClick={() => onSort('win_net_avg')}       mine="win_net_avg"       sortKey={sortKey} desc={sortDesc}>Avg Win</Th>
              <Th onClick={() => onSort('win_net_lowest')}    mine="win_net_lowest"    sortKey={sortKey} desc={sortDesc}>Lowest Win</Th>

              {/* Losers-only columns */}
              <Th onClick={() => onSort('n_losses')}         mine="n_losses"         sortKey={sortKey} desc={sortDesc}>Loss n</Th>
              <Th onClick={() => onSort('loss_max_mtm_best')} mine="loss_max_mtm_best" sortKey={sortKey} desc={sortDesc}>Loss Best MTM</Th>
              <Th onClick={() => onSort('loss_net_worst')}    mine="loss_net_worst"    sortKey={sortKey} desc={sortDesc}>Worst Loss</Th>
              <Th onClick={() => onSort('loss_net_avg')}      mine="loss_net_avg"      sortKey={sortKey} desc={sortDesc}>Avg Loss</Th>
              <Th onClick={() => onSort('loss_net_lowest')}   mine="loss_net_lowest"   sortKey={sortKey} desc={sortDesc}>Lowest Loss</Th>

              <Th onClick={() => onSort('max_mtm_avg')}  mine="max_mtm_avg"  sortKey={sortKey} desc={sortDesc}>Avg MFE</Th>
              <Th onClick={() => onSort('max_mtm_best')} mine="max_mtm_best" sortKey={sortKey} desc={sortDesc}>Best MFE</Th>
              <Th onClick={() => onSort('min_mtm_avg')}  mine="min_mtm_avg"  sortKey={sortKey} desc={sortDesc}>Avg MAE</Th>
              <Th onClick={() => onSort('min_mtm_worst')} mine="min_mtm_worst" sortKey={sortKey} desc={sortDesc}>Worst MAE</Th>
              <Th onClick={() => onSort('gross_pnl_avg')} mine="gross_pnl_avg" sortKey={sortKey} desc={sortDesc}>Avg Gross</Th>
              <Th onClick={() => onSort('net_pnl_avg')}   mine="net_pnl_avg"   sortKey={sortKey} desc={sortDesc}>Avg Net</Th>
              <Th onClick={() => onSort('net_pnl_sum')}   mine="net_pnl_sum"   sortKey={sortKey} desc={sortDesc}>Total Net</Th>
              <Th onClick={() => onSort('slippage_avg_total')} mine="slippage_avg_total" sortKey={sortKey} desc={sortDesc}>Slip RT</Th>
              <th style={thStyle} title="Round-trip / 2 estimate">Slip ½</th>
              <Th onClick={() => onSort('brokerage_avg_total')} mine="brokerage_avg_total" sortKey={sortKey} desc={sortDesc}>Brk RT</Th>
              <th style={thStyle} title="Round-trip / 2 estimate">Brk ½</th>
              <Th onClick={() => onSort('cost_avg_total')} mine="cost_avg_total" sortKey={sortKey} desc={sortDesc}>Cost RT</Th>
              <Th onClick={() => onSort('credit_pct_avg')} mine="credit_pct_avg" sortKey={sortKey} desc={sortDesc}>Credit %</Th>
              <Th onClick={() => onSort('margin_avg')}     mine="margin_avg"     sortKey={sortKey} desc={sortDesc}>Margin</Th>
            </tr>
          </thead>
          <tbody>
            {filteredSorted.length === 0 && (
              <tr><td colSpan={35} style={{
                padding: 24, textAlign: 'center', color: '#7a9bb5',
              }}>
                {loading ? 'Loading…' : 'No cells match your filters.'}
              </td></tr>
            )}
            {filteredSorted.map((c, i) => (
              <tr key={`${c.contract_type}|${c.iv_band}|${c.delta_target}`}
                  style={{
                    background: i % 2 === 0 ? '#0d1421' : '#0a1018',
                    borderTop: '1px solid #11202c',
                  }}>
                <td style={{ ...tdStyle, whiteSpace: 'nowrap', fontWeight: 600 }}>
                  {CONTRACT_LABEL[c.contract_type] || c.contract_type}
                </td>
                <td style={tdStyle}>{c.iv_band}</td>
                <td style={tdStyle}>{c.delta_target.toFixed(2)}</td>
                <td style={tdStyle}>{c.n}</td>
                <td style={{ ...tdStyle, color: c.win_rate >= 0.55 ? '#10b981' : '#f0b429' }}>
                  {fmtPct(c.win_rate)}
                </td>
                <td style={{ ...tdStyle, color: c.sl_rate > 0.20 ? '#ef4444' : '#7a9bb5' }}>
                  {fmtPct(c.sl_rate)}
                </td>
                <td style={{ ...tdStyle, color: c.n_sl > 0 ? '#cdd6e0' : '#475569' }}>{c.n_sl}</td>
                <td style={{ ...tdStyle, color: c.n_sl > 0 ? '#ef4444' : '#475569' }}>
                  {c.n_sl > 0 ? fmtUsdSigned(c.sl_mtm_avg) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_sl > 0 ? '#ef4444' : '#475569' }}>
                  {c.n_sl > 0 ? fmtUsdSigned(c.sl_mtm_total, 0) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_sl === 0 ? '#475569' : pnlColor(c.sl_net_avg), fontWeight: 700 }}>
                  {c.n_sl > 0 ? fmtUsdSigned(c.sl_net_avg) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_sl === 0 ? '#475569' : pnlColor(c.sl_net_total / 50), fontWeight: 700 }}>
                  {c.n_sl > 0 ? fmtUsdSigned(c.sl_net_total, 0) : '—'}
                </td>
                {/* Winners */}
                <td style={{ ...tdStyle, color: c.n_wins > 0 ? '#10b981' : '#475569' }}>{c.n_wins}</td>
                <td style={{ ...tdStyle, color: c.n_wins > 0 ? '#ef4444' : '#475569' }}
                    title="Worst MTM dip among winning trades (deepest a winner went underwater before recovering)">
                  {c.n_wins > 0 ? fmtUsdSigned(c.win_min_mtm_worst) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_wins > 0 ? '#10b981' : '#475569', fontWeight: 700 }}
                    title="Best single winning trade in this cell">
                  {c.n_wins > 0 ? fmtUsdSigned(c.win_net_best) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_wins > 0 ? '#10b981' : '#475569' }}>
                  {c.n_wins > 0 ? fmtUsdSigned(c.win_net_avg) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_wins > 0 ? '#7a9bb5' : '#475569' }}
                    title="Smallest winning trade (barely-positive)">
                  {c.n_wins > 0 ? fmtUsdSigned(c.win_net_lowest) : '—'}
                </td>
                {/* Losers */}
                <td style={{ ...tdStyle, color: c.n_losses > 0 ? '#ef4444' : '#475569' }}>{c.n_losses}</td>
                <td style={{ ...tdStyle, color: c.n_losses > 0 ? '#10b981' : '#475569' }}
                    title="Best MTM peak among losing trades (highest a loser climbed before turning south)">
                  {c.n_losses > 0 ? fmtUsdSigned(c.loss_max_mtm_best) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_losses > 0 ? '#ef4444' : '#475569', fontWeight: 700 }}
                    title="Worst single losing trade in this cell">
                  {c.n_losses > 0 ? fmtUsdSigned(c.loss_net_worst) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_losses > 0 ? '#ef4444' : '#475569' }}>
                  {c.n_losses > 0 ? fmtUsdSigned(c.loss_net_avg) : '—'}
                </td>
                <td style={{ ...tdStyle, color: c.n_losses > 0 ? '#7a9bb5' : '#475569' }}
                    title="Smallest-magnitude loss (closest to break-even)">
                  {c.n_losses > 0 ? fmtUsdSigned(c.loss_net_lowest) : '—'}
                </td>
                <td style={{ ...tdStyle, color: '#10b981' }}>{fmtUsd(c.max_mtm_avg)}</td>
                <td style={{ ...tdStyle, color: '#10b981' }}>{fmtUsd(c.max_mtm_best)}</td>
                <td style={{ ...tdStyle, color: '#ef4444' }}>{fmtUsd(c.min_mtm_avg)}</td>
                <td style={{ ...tdStyle, color: '#ef4444' }}>{fmtUsd(c.min_mtm_worst)}</td>
                <td style={{ ...tdStyle, color: pnlColor(c.gross_pnl_avg) }}>{fmtUsdSigned(c.gross_pnl_avg)}</td>
                <td style={{ ...tdStyle, color: pnlColor(c.net_pnl_avg), fontWeight: 700 }}>
                  {fmtUsdSigned(c.net_pnl_avg)}
                </td>
                <td style={{ ...tdStyle, color: pnlColor(c.net_pnl_sum / 50), fontWeight: 700 }}>
                  {fmtUsdSigned(c.net_pnl_sum, 0)}
                </td>
                <td style={tdStyle}>{fmtUsd(c.slippage_avg_total)}</td>
                <td style={{ ...tdStyle, color: '#7a9bb5' }}>{fmtUsd(c.slippage_avg_per_side)}</td>
                <td style={tdStyle}>{fmtUsd(c.brokerage_avg_total)}</td>
                <td style={{ ...tdStyle, color: '#7a9bb5' }}>{fmtUsd(c.brokerage_avg_per_side)}</td>
                <td style={tdStyle}>{fmtUsd(c.cost_avg_total)}</td>
                <td style={tdStyle}>{(c.credit_pct_avg * 100).toFixed(3)}%</td>
                <td style={tdStyle}>{fmtUsd(c.margin_avg, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 11, color: '#7a9bb5', marginTop: 6 }}>
        Costs are round-trip (entry + exit). "½" columns are entry-or-exit
        estimates (round-trip/2). MFE = avg max-MTM during the trade,
        MAE = avg min-MTM. Margin shown is the 29-scenario portfolio
        margin computed at entry.
        <br />
        IV bands span the full dataset range: <code>&lt;30, 30-40, 40-50, 50-60, 60-70, 70-80, 80-90, 90-100, 100+</code>.
        Above-70% IV is rare in BTC (5,274 trades — only 8.9% above 60%, 0.34% above 80%, 0.11% above 90%, 0% above 100%);
        raise <strong>Min n / cell</strong> to hide sparse cells, lower it to see them.
      </div>
    </div>
  );
}

// ── Bits ─────────────────────────────────────────────────────────────────────

const thStyle: React.CSSProperties = {
  padding: '7px 8px', textAlign: 'left',
  fontSize: 10, fontWeight: 600, color: '#7a9bb5',
  textTransform: 'uppercase', letterSpacing: 0.4,
  borderBottom: '1px solid #1a2d42', whiteSpace: 'nowrap',
};
const tdStyle: React.CSSProperties = { padding: '5px 8px', whiteSpace: 'nowrap' };

const summaryThStyle: React.CSSProperties = {
  padding: '6px 10px', textAlign: 'left',
  fontSize: 10, fontWeight: 600, color: '#7a9bb5',
  textTransform: 'uppercase', letterSpacing: 0.4,
};
const summaryTdStyle: React.CSSProperties = {
  padding: '5px 10px', fontSize: 12, color: '#cdd6e0',
};

function Th({ children, onClick, sortKey, mine, desc }: {
  children: React.ReactNode; onClick: () => void;
  sortKey: SortKey; mine: SortKey; desc: boolean;
}) {
  const active = sortKey === mine;
  return (
    <th onClick={onClick}
        style={{
          ...thStyle, cursor: 'pointer',
          background: active ? '#142537' : undefined,
          color: active ? '#fff' : '#7a9bb5',
        }}>
      {children}{active ? (desc ? ' ▼' : ' ▲') : ''}
    </th>
  );
}

export default M4ExpiryGridTable;
