import React, { useEffect, useRef } from 'react';
import type { HistoricalChainRow } from '../../types/historical';

interface Props {
  chain: HistoricalChainRow[];
  strategyMode: boolean;
  onSelectOption: (strike: number, type: 'CE' | 'PE') => void;
  onAddLeg: (strike: number, type: 'CE' | 'PE', action: 'BUY' | 'SELL', premium: number) => void;
}

const f = (n: number, d = 2) => n.toFixed(d);
// Show '-' for greeks/mark only when mark price is 0 (no data at that timestamp)
const fd = (mark: number, n: number, d = 2) => mark === 0 ? '-' : n.toFixed(d);

export const HistoricalOptionChain: React.FC<Props> = ({
  chain, strategyMode, onSelectOption, onAddLeg
}) => {
  const atmRef = useRef<HTMLTableRowElement>(null);
  const strikeCellRef = useRef<HTMLTableCellElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to center ATM row vertically AND Strike column horizontally
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      if (atmRef.current && strikeCellRef.current && scrollRef.current) {
        const container = scrollRef.current;
        const row = atmRef.current;
        const cell = strikeCellRef.current;
        // Vertical: center ATM row
        container.scrollTop = row.offsetTop - container.clientHeight / 2 + row.offsetHeight / 2;
        // Horizontal: center Strike column
        container.scrollLeft = cell.offsetLeft - container.clientWidth / 2 + cell.offsetWidth / 2;
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [chain, strategyMode]);

  return (
    <div className="table-scroll" ref={scrollRef}>
      <table className="chain-table">
        <thead>
          <tr>
            <th className="call-header">Vega</th>
            <th className="call-header">Theta</th>
            <th className="call-header">Gamma</th>
            <th className="call-header">IV %</th>
            <th className="call-header">Delta</th>
            <th className="call-header">Mark</th>
            <th className="strike-header">Strike</th>
            <th className="put-header">Mark</th>
            <th className="put-header">Delta</th>
            <th className="put-header">IV %</th>
            <th className="put-header">Gamma</th>
            <th className="put-header">Theta</th>
            <th className="put-header">Vega</th>
          </tr>
        </thead>
        <tbody>
          {chain.map((row) => (
            <tr
              key={row.strike}
              ref={row.is_atm ? atmRef : undefined}
              className={row.is_atm ? 'atm-row' : ''}
              data-itm-call={row.call.delta > 0.5 ? 'true' : 'false'}
              data-itm-put={Math.abs(row.put.delta) > 0.5 ? 'true' : 'false'}
            >
              <td className="call-cell">{fd(row.call.last_price, row.call.vega, 2)}</td>
              <td className="call-cell">{fd(row.call.last_price, row.call.theta, 2)}</td>
              <td className="call-cell">{fd(row.call.last_price, row.call.gamma, 8)}</td>
              <td className="call-cell" style={{ color: 'var(--gold)' }}>{fd(row.call.last_price, row.call.iv_pct, 1)}</td>
              <td className="call-cell call-delta">{fd(row.call.last_price, row.call.delta, 3)}</td>

              {/* CE Mark — chart click OR strategy B/S */}
              <td className="call-cell ltp">
                {strategyMode ? (
                  <div className="chain-bs-group">
                    <button className="chain-bs-btn buy" disabled={row.call.last_price === 0} onClick={() => onAddLeg(row.strike, 'CE', 'BUY', row.call.last_price)}>B</button>
                    <span className="chain-bs-price">{row.call.last_price === 0 ? '-' : f(row.call.last_price, 2)}</span>
                    <button className="chain-bs-btn sell" disabled={row.call.last_price === 0} onClick={() => onAddLeg(row.strike, 'CE', 'SELL', row.call.last_price)}>S</button>
                  </div>
                ) : (
                  <span className="clickable" onClick={() => onSelectOption(row.strike, 'CE')}>
                    {row.call.last_price === 0 ? '-' : f(row.call.last_price, 2)}
                  </span>
                )}
              </td>

              <td
                ref={row.is_atm ? strikeCellRef : undefined}
                className={`strike-cell ${row.is_atm ? 'atm-strike' : ''}`}
              >
                {row.strike.toLocaleString()}
                {row.is_atm && <span className="atm-badge">ATM</span>}
              </td>

              {/* PE Mark — chart click OR strategy B/S */}
              <td className="put-cell ltp">
                {strategyMode ? (
                  <div className="chain-bs-group">
                    <button className="chain-bs-btn buy" disabled={row.put.last_price === 0} onClick={() => onAddLeg(row.strike, 'PE', 'BUY', row.put.last_price)}>B</button>
                    <span className="chain-bs-price">{row.put.last_price === 0 ? '-' : f(row.put.last_price, 2)}</span>
                    <button className="chain-bs-btn sell" disabled={row.put.last_price === 0} onClick={() => onAddLeg(row.strike, 'PE', 'SELL', row.put.last_price)}>S</button>
                  </div>
                ) : (
                  <span className="clickable" onClick={() => onSelectOption(row.strike, 'PE')}>
                    {row.put.last_price === 0 ? '-' : f(row.put.last_price, 2)}
                  </span>
                )}
              </td>

              <td className="put-cell put-delta">{fd(row.put.last_price, row.put.delta, 3)}</td>
              <td className="put-cell" style={{ color: 'var(--gold)' }}>{fd(row.put.last_price, row.put.iv_pct, 1)}</td>
              <td className="put-cell">{fd(row.put.last_price, row.put.gamma, 8)}</td>
              <td className="put-cell">{fd(row.put.last_price, row.put.theta, 2)}</td>
              <td className="put-cell">{fd(row.put.last_price, row.put.vega, 2)}</td>
            </tr>
          ))}
          {chain.length === 0 && (
            <tr>
              <td colSpan={13} className="loading-center">
                No data available for this minute. Scrub timeline to find data.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
};
