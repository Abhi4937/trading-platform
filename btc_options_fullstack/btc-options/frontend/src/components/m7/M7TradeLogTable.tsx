import React, { useEffect, useState } from 'react';
import { fetchM7Trades, type M7Dataset } from '../../services/m7_api';
import type { M7Filters, M7TradeRow } from '../../types/m7';

export function M7TradeLogTable({ filters, onSelect, dataset }: {
  filters: M7Filters; onSelect: (tradeId: string) => void; dataset?: M7Dataset;
}) {
  const isCal = dataset === 'calendar';
  const [rows, setRows] = useState<M7TradeRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [sortBy, setSortBy] = useState<string>('friday_date_ist');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const limit = 50;
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    // dataset must be threaded — without it the log hits the delta_match parquet.
    fetchM7Trades({ ...filters, limit, offset, sort_by: sortBy, sort_dir: sortDir }, dataset)
      .then(r => { setRows(r.rows); setTotal(r.total); })
      .finally(() => setLoading(false));
  }, [JSON.stringify(filters), offset, sortBy, sortDir, dataset]);

  const click = (col: string) => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(col); setSortDir('desc'); }
  };

  const th = (col: string, label: string, align: 'left' | 'right' = 'left') => (
    <th style={{ padding: 6, color: '#7a9bb5', textAlign: align, cursor: 'pointer' }} onClick={() => click(col)}>
      {label}{sortBy === col && (sortDir === 'asc' ? ' ▲' : ' ▼')}
    </th>
  );

  return (
    <div style={{ background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 6, padding: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontSize: 13, color: '#cfd9e3', fontWeight: 700 }}>
          Trade log ({total.toLocaleString()} matching · click row → path chart)
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0}
                  style={{ padding: '4px 10px', background: '#0d1421', color: '#cfd9e3',
                           border: '1px solid #1a2d42', borderRadius: 4, cursor: 'pointer' }}>‹ Prev</button>
          <span style={{ fontSize: 11, color: '#7a9bb5', alignSelf: 'center' }}>
            {offset + 1}–{Math.min(offset + limit, total)}
          </span>
          <button onClick={() => setOffset(offset + limit)} disabled={offset + limit >= total}
                  style={{ padding: '4px 10px', background: '#0d1421', color: '#cfd9e3',
                           border: '1px solid #1a2d42', borderRadius: 4, cursor: 'pointer' }}>Next ›</button>
        </div>
      </div>
      {loading && <div style={{ color: '#7a9bb5', fontSize: 12 }}>Loading…</div>}
      {!loading && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11,
                          fontVariantNumeric: 'tabular-nums', color: '#cfd9e3' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1a2d42' }}>
                {th('friday_date_ist', 'Friday')}
                {th(isCal ? 'entry_hour_actual' : 'entry_hour_ist', 'Entry hr')}
                {th('expiry_date', 'Expiry')}
                {th('delta_target', 'Δ', 'right')}
                {th('call_strike', 'CE strike', 'right')}
                {th('put_strike', 'PE strike', 'right')}
                {th('credit_usd', isCal ? 'Debit $' : 'Credit $', 'right')}
                {th('entry_atm_iv_band', isCal ? 'Gap bucket' : 'IV band')}
                {th('total_entry_cost_usd', 'Cost $', 'right')}
                {th('margin_used_usd_at_entry', 'Margin $', 'right')}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.trade_id}
                    onClick={() => onSelect(r.trade_id)}
                    style={{ borderBottom: '1px solid #131c28', cursor: 'pointer' }}
                    onMouseEnter={e => (e.currentTarget.style.background = '#0d1421')}
                    onMouseLeave={e => (e.currentTarget.style.background = '')}>
                  <td style={{ padding: 5 }}>{r.friday_date_ist}</td>
                  <td style={{ padding: 5 }}>{String(isCal ? (r.entry_hour_actual ?? 0) : r.entry_hour_ist).padStart(2, '0')}:00</td>
                  <td style={{ padding: 5 }}>{r.expiry_date}</td>
                  <td style={{ padding: 5, textAlign: 'right' }}>
                    {r.delta_target != null ? r.delta_target.toFixed(2) : '—'}
                    {r.is_straddle && <span style={{ color: '#7a9bb5' }}> ⊕</span>}
                  </td>
                  <td style={{ padding: 5, textAlign: 'right' }}>{r.call_strike != null ? r.call_strike.toLocaleString() : '—'}</td>
                  <td style={{ padding: 5, textAlign: 'right' }}>{r.put_strike != null ? r.put_strike.toLocaleString() : '—'}</td>
                  <td style={{ padding: 5, textAlign: 'right' }}>{r.credit_usd != null ? `$${(isCal ? -r.credit_usd : r.credit_usd).toFixed(2)}` : '—'}</td>
                  <td style={{ padding: 5 }}>{r.entry_atm_iv_band ?? '—'}</td>
                  <td style={{ padding: 5, textAlign: 'right' }}>{r.total_entry_cost_usd != null ? `$${r.total_entry_cost_usd.toFixed(2)}` : '—'}</td>
                  <td style={{ padding: 5, textAlign: 'right' }}>
                    {r.margin_used_usd_at_entry != null ? `$${r.margin_used_usd_at_entry.toFixed(0)}` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
