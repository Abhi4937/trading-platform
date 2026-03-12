import React from 'react';
import type { HistoricalChainRow } from '../../types/historical';

interface Props {
  chain: HistoricalChainRow[];
  onSelectOption: (strike: number, type: 'CE' | 'PE') => void;
}

const f = (n: number, d = 2) => n.toFixed(d);

export const HistoricalOptionChain: React.FC<Props> = ({ chain, onSelectOption }) => {
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
              <td className="call-cell">{f(row.call.gamma, 4)}</td>
              <td className="call-cell">{f(row.call.theta, 2)}</td>
              <td className="call-cell">{f(row.call.vega, 2)}</td>
              <td 
                className="call-cell ltp clickable"
                onClick={() => onSelectOption(row.strike, 'CE')}
              >
                {f(row.call.last_price, 2)}
              </td>
              
              <td className={`strike-cell ${row.is_atm ? 'atm-strike' : ''}`}>
                {row.strike.toLocaleString()}
                {row.is_atm && <span className="atm-badge">ATM</span>}
              </td>

              <td 
                className="put-cell ltp clickable"
                onClick={() => onSelectOption(row.strike, 'PE')}
              >
                {f(row.put.last_price, 2)}
              </td>
              <td className="put-cell">{f(row.put.vega, 2)}</td>
              <td className="put-cell">{f(row.put.theta, 2)}</td>
              <td className="put-cell">{f(row.put.gamma, 4)}</td>
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
