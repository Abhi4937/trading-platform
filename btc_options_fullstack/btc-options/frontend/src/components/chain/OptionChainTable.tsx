import React, { useState, useMemo, useRef, useEffect } from 'react';
import type { ChainRow, OptionLeg } from '../../types/api';

interface Props {
  chain: ChainRow[];
  spotPrice: number;
  atmStrike: number;
  onSelectLeg?: (leg: OptionLeg) => void;
}

const f = (n: number | undefined, d = 2) =>
  n == null || isNaN(n) ? '—' : n.toFixed(d);

const COLUMNS = [
  { key: 'delta',      label: 'Δ Delta',  decimals: 3 },
  { key: 'iv_pct',    label: 'IV %',     decimals: 1 },
  { key: 'vega',      label: 'Vega',     decimals: 2 },
  { key: 'gamma',     label: 'Gamma',    decimals: 4 },
  { key: 'theta',     label: 'Theta',    decimals: 2 },
  { key: 'bid',       label: 'Bid',      decimals: 2 },
  { key: 'ask',       label: 'Ask',      decimals: 2 },
  { key: 'last_price', label: 'Mark',    decimals: 2 },
  { key: 'price_bs',  label: 'BS Price', decimals: 2 },
];

export const OptionChainTable: React.FC<Props> = ({ chain, spotPrice, atmStrike, onSelectLeg }) => {
  const [filter, setFilter] = useState('');
  const [showStrikes, setShowStrikes] = useState(20);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      const el = scrollRef.current;
      el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
    }
  }, [atmStrike]);

  const filtered = useMemo(() => {
    let rows = chain;
    if (filter) {
      const f = parseFloat(filter);
      if (!isNaN(f)) {
        rows = rows.filter(r => Math.abs(r.strike - f) < 1000);
      }
    }
    const atmIdx = rows.findIndex(r => r.strike === atmStrike);
    if (atmIdx >= 0) {
      const half = Math.floor(showStrikes / 2);
      const start = Math.max(0, atmIdx - half);
      const end = Math.min(rows.length, start + showStrikes);
      return rows.slice(start, end);
    }
    return rows.slice(0, showStrikes);
  }, [chain, filter, atmStrike, showStrikes]);

  return (
    <div className="chain-table-wrap">
      <div className="chain-toolbar">
        <input
          className="search-input"
          placeholder="Filter by strike..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
        <select
          className="sel-input"
          value={showStrikes}
          onChange={e => setShowStrikes(Number(e.target.value))}
        >
          <option value={10}>±5 strikes</option>
          <option value={20}>±10 strikes</option>
          <option value={40}>±20 strikes</option>
          <option value={999}>All</option>
        </select>
      </div>

      <div className="table-scroll" ref={scrollRef}>
        <table className="chain-table">
          <thead>
            <tr>
              {COLUMNS.map(c => (
                <th key={c.key} className="call-header">{c.label}</th>
              ))}
              <th className="strike-header">Strike</th>
              {[...COLUMNS].reverse().map(c => (
                <th key={c.key + '_p'} className="put-header">{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map(row => (
              <tr
                key={row.strike}
                className={row.is_atm ? 'atm-row' : ''}
                data-itm-call={row.call && row.call.delta > 0.5 ? 'true' : 'false'}
                data-itm-put={row.put && Math.abs(row.put.delta) > 0.5 ? 'true' : 'false'}
              >
                {COLUMNS.map(c => {
                  const val = row.call ? (row.call as any)[c.key] : undefined;
                  return (
                    <td
                      key={c.key}
                      className={`call-cell ${c.key === 'last_price' ? 'ltp clickable' : ''} ${c.key === 'delta' ? 'call-delta' : ''} ${c.key === 'iv_pct' ? 'iv-cell' : ''}`}
                      onClick={c.key === 'last_price' && row.call ? () => onSelectLeg?.(row.call!) : undefined}
                    >
                      {f(val, c.decimals)}
                    </td>
                  );
                })}
                <td className={`strike-cell ${row.is_atm ? 'atm-strike' : ''}`}>
                  {row.strike.toLocaleString()}
                  {row.is_atm && <span className="atm-badge">ATM</span>}
                </td>
                {[...COLUMNS].reverse().map(c => {
                  const val = row.put ? (row.put as any)[c.key] : undefined;
                  return (
                    <td
                      key={c.key + '_p'}
                      className={`put-cell ${c.key === 'last_price' ? 'ltp clickable' : ''} ${c.key === 'delta' ? 'put-delta' : ''} ${c.key === 'iv_pct' ? 'iv-cell' : ''}`}
                      onClick={c.key === 'last_price' && row.put ? () => onSelectLeg?.(row.put!) : undefined}
                    >
                      {f(val, c.decimals)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
