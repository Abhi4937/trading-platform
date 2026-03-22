import React, { useEffect, useRef } from 'react';
import type { HistoricalChainRow } from '../../types/historical';

interface LegIndicator {
  ce?: 'BUY' | 'SELL';
  pe?: 'BUY' | 'SELL';
}

interface Props {
  chain: HistoricalChainRow[];
  strategyMode: boolean;
  legMap?: Map<number, LegIndicator>;
  onSelectOption: (strike: number, type: 'CE' | 'PE') => void;
  onAddLeg: (strike: number, type: 'CE' | 'PE', action: 'BUY' | 'SELL', premium: number) => void;
}

const f = (n: number, d = 2) => n.toFixed(d);
// Show '-' for greeks/mark only when mark price is 0 (no data at that timestamp)
const fd = (mark: number, n: number, d = 2) => mark === 0 ? '-' : n.toFixed(d);

export const HistoricalOptionChain: React.FC<Props> = ({
  chain, strategyMode, legMap, onSelectOption, onAddLeg
}) => {
  const atmRef = useRef<HTMLTableRowElement>(null);
  const strikeCellRef = useRef<HTMLTableCellElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Center ATM row vertically and Strike column horizontally
  const centerAtm = () => {
    if (atmRef.current && strikeCellRef.current && scrollRef.current) {
      const container = scrollRef.current;
      const row = atmRef.current;
      const cell = strikeCellRef.current;
      container.scrollTop = row.offsetTop - container.clientHeight / 2 + row.offsetHeight / 2;
      container.scrollLeft = cell.offsetLeft - container.clientWidth / 2 + cell.offsetWidth / 2;
    }
  };

  // Run on chain/strategyMode change
  useEffect(() => {
    const raf = requestAnimationFrame(centerAtm);
    return () => cancelAnimationFrame(raf);
  }, [chain, strategyMode]);

  // Re-center whenever the container is resized (e.g. Strategy Builder panel opens/closes)
  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => requestAnimationFrame(centerAtm));
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

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
              className={[
                row.is_atm ? 'atm-row' : '',
                legMap?.has(row.strike) ? 'leg-selected-row' : '',
                row.call.last_price === 0 && row.put.last_price === 0 ? 'no-price-row' : ''
              ].filter(Boolean).join(' ')}
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
                  row.call.last_price === 0 ? (
                    <span className="chain-bs-price">-</span>
                  ) : (
                    <div className="chain-bs-group">
                      <button className="chain-bs-btn buy" onClick={() => onAddLeg(row.strike, 'CE', 'BUY', row.call.last_price)}>B</button>
                      <span className="chain-bs-price">{f(row.call.last_price, 2)}</span>
                      <button className="chain-bs-btn sell" onClick={() => onAddLeg(row.strike, 'CE', 'SELL', row.call.last_price)}>S</button>
                    </div>
                  )
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
                {legMap?.get(row.strike)?.ce && (
                  <span className={`leg-side-badge ${legMap.get(row.strike)!.ce === 'BUY' ? 'buy' : 'sell'}`}>
                    {legMap.get(row.strike)!.ce === 'BUY' ? 'B' : 'S'}
                  </span>
                )}
                {row.strike.toLocaleString()}
                {row.is_atm && <span className="atm-badge">ATM</span>}
                {legMap?.get(row.strike)?.pe && (
                  <span className={`leg-side-badge ${legMap.get(row.strike)!.pe === 'BUY' ? 'buy' : 'sell'}`}>
                    {legMap.get(row.strike)!.pe === 'BUY' ? 'B' : 'S'}
                  </span>
                )}
              </td>

              {/* PE Mark — chart click OR strategy B/S */}
              <td className="put-cell ltp">
                {strategyMode ? (
                  row.put.last_price === 0 ? (
                    <span className="chain-bs-price">-</span>
                  ) : (
                    <div className="chain-bs-group">
                      <button className="chain-bs-btn buy" onClick={() => onAddLeg(row.strike, 'PE', 'BUY', row.put.last_price)}>B</button>
                      <span className="chain-bs-price">{f(row.put.last_price, 2)}</span>
                      <button className="chain-bs-btn sell" onClick={() => onAddLeg(row.strike, 'PE', 'SELL', row.put.last_price)}>S</button>
                    </div>
                  )
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
