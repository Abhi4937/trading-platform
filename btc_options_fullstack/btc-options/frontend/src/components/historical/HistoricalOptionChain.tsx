import React from 'react';
import type { HistoricalChainRow } from '../../types/historical';

interface Props {
  chain: HistoricalChainRow[];
  strategyMode: boolean;
  onSelectOption: (strike: number, type: 'CE' | 'PE') => void;
  onAddLeg: (strike: number, type: 'CE' | 'PE', action: 'BUY' | 'SELL', premium: number) => void;
}

const f = (n: number, d = 2) => n.toFixed(d);

export const HistoricalOptionChain: React.FC<Props> = ({
  chain, strategyMode, onSelectOption, onAddLeg
}) => {
  return (
    <div className="table-scroll">
      <table className="chain-table">
        <thead>
          <tr>
            <th className="call-header">IV %</th>
            <th className="call-header">Delta</th>
            <th className="call-header">Gamma</th>
            <th className="call-header">Theta</th>
            <th className="call-header">Vega</th>
            <th className="call-header">Mark</th>
            <th className="strike-header">Strike</th>
            <th className="put-header">Mark</th>
            <th className="put-header">Vega</th>
            <th className="put-header">Theta</th>
            <th className="put-header">Gamma</th>
            <th className="put-header">Delta</th>
            <th className="put-header">IV %</th>
          </tr>
        </thead>
        <tbody>
          {chain.map((row) => (
            <tr
              key={row.strike}
              className={row.is_atm ? 'atm-row' : ''}
              data-itm-call={row.call.delta > 0.5 ? 'true' : 'false'}
              data-itm-put={Math.abs(row.put.delta) > 0.5 ? 'true' : 'false'}
            >
              <td className="call-cell" style={{ color: 'var(--gold)' }}>{f(row.call.iv_pct, 1)}</td>
              <td className="call-cell call-delta">{f(row.call.delta, 3)}</td>
              <td className="call-cell">{f(row.call.gamma, 8)}</td>
              <td className="call-cell">{f(row.call.theta, 2)}</td>
              <td className="call-cell">{f(row.call.vega, 2)}</td>

              {/* CE Mark — chart click OR strategy B/S */}
              <td className="call-cell ltp">
                {strategyMode ? (
                  <div className="chain-bs-group">
                    <button className="chain-bs-btn buy" onClick={() => onAddLeg(row.strike, 'CE', 'BUY', row.call.last_price)}>B</button>
                    <span className="chain-bs-price">{f(row.call.last_price, 2)}</span>
                    <button className="chain-bs-btn sell" onClick={() => onAddLeg(row.strike, 'CE', 'SELL', row.call.last_price)}>S</button>
                  </div>
                ) : (
                  <span className="clickable" onClick={() => onSelectOption(row.strike, 'CE')}>
                    {f(row.call.last_price, 2)}
                  </span>
                )}
              </td>

              <td className={`strike-cell ${row.is_atm ? 'atm-strike' : ''}`}>
                {row.strike.toLocaleString()}
                {row.is_atm && <span className="atm-badge">ATM</span>}
              </td>

              {/* PE Mark — chart click OR strategy B/S */}
              <td className="put-cell ltp">
                {strategyMode ? (
                  <div className="chain-bs-group">
                    <button className="chain-bs-btn buy" onClick={() => onAddLeg(row.strike, 'PE', 'BUY', row.put.last_price)}>B</button>
                    <span className="chain-bs-price">{f(row.put.last_price, 2)}</span>
                    <button className="chain-bs-btn sell" onClick={() => onAddLeg(row.strike, 'PE', 'SELL', row.put.last_price)}>S</button>
                  </div>
                ) : (
                  <span className="clickable" onClick={() => onSelectOption(row.strike, 'PE')}>
                    {f(row.put.last_price, 2)}
                  </span>
                )}
              </td>

              <td className="put-cell">{f(row.put.vega, 2)}</td>
              <td className="put-cell">{f(row.put.theta, 2)}</td>
              <td className="put-cell">{f(row.put.gamma, 8)}</td>
              <td className="put-cell put-delta">{f(row.put.delta, 3)}</td>
              <td className="put-cell" style={{ color: 'var(--gold)' }}>{f(row.put.iv_pct, 1)}</td>
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
