import React from 'react';
import type { HistoricalChainRow } from '../../types/historical';

interface Props {
  chain: HistoricalChainRow[];
  onSelectOption: (strike: number, type: 'CE' | 'PE') => void;
}

const f = (n: number, d = 2) => n.toFixed(d);

export const HistoricalOptionChain: React.FC<Props> = ({ chain, onSelectOption }) => {
  return (
    <div className="w-full overflow-x-auto overflow-y-auto h-[600px] border border-[#1a2d42] rounded-md bg-[#0c1420]">
      <table className="w-full text-xs text-right border-collapse whitespace-nowrap">
        <thead className="sticky top-0 bg-[#080e16] z-10 border-b border-[#1a2d42]">
          <tr>
            {/* Call Headers */}
            <th className="p-2 text-[#60a5fa] border-b-2 border-[#60a5fa]/40">IV %</th>
            <th className="p-2 text-[#60a5fa] border-b-2 border-[#60a5fa]/40">Delta</th>
            <th className="p-2 text-[#60a5fa] border-b-2 border-[#60a5fa]/40">Gamma</th>
            <th className="p-2 text-[#60a5fa] border-b-2 border-[#60a5fa]/40">Theta</th>
            <th className="p-2 text-[#60a5fa] border-b-2 border-[#60a5fa]/40">Vega</th>
            <th className="p-2 text-[#60a5fa] border-b-2 border-[#60a5fa]/40">Mark</th>
            {/* Strike Header */}
            <th className="p-2 text-center bg-[#101c2c] text-[#7a9bb5]">Strike</th>
            {/* Put Headers */}
            <th className="p-2 text-[#f87171] border-b-2 border-[#f87171]/40">Mark</th>
            <th className="p-2 text-[#f87171] border-b-2 border-[#f87171]/40">Vega</th>
            <th className="p-2 text-[#f87171] border-b-2 border-[#f87171]/40">Theta</th>
            <th className="p-2 text-[#f87171] border-b-2 border-[#f87171]/40">Gamma</th>
            <th className="p-2 text-[#f87171] border-b-2 border-[#f87171]/40">Delta</th>
            <th className="p-2 text-[#f87171] border-b-2 border-[#f87171]/40">IV %</th>
          </tr>
        </thead>
        <tbody>
          {chain.map((row) => (
            <tr 
              key={row.strike} 
              className={`border-b border-[#1a2d42]/40 hover:bg-[#00d4ff]/10 ${row.is_atm ? 'bg-[#00d4ff]/5' : ''}`}
            >
              {/* Call Data */}
              <td className="p-2 text-[#f0b429]">{f(row.call.iv_pct, 1)}</td>
              <td className="p-2 text-[#00e5a0]">{f(row.call.delta, 3)}</td>
              <td className="p-2 text-[#a0c4ff]">{f(row.call.gamma, 4)}</td>
              <td className="p-2 text-[#a0c4ff]">{f(row.call.theta, 2)}</td>
              <td className="p-2 text-[#a0c4ff]">{f(row.call.vega, 2)}</td>
              <td 
                className="p-2 font-bold text-[#a0c4ff] cursor-pointer hover:text-[#00d4ff] hover:underline"
                onClick={() => onSelectOption(row.strike, 'CE')}
              >
                {f(row.call.last_price, 2)}
              </td>
              
              {/* Strike */}
              <td className={`p-2 text-center font-bold bg-[#0c1420] border-l border-r border-[#1a2d42] ${row.is_atm ? 'bg-[#00d4ff]/10 text-[#00d4ff]' : 'text-[#e2eaf4]'}`}>
                {row.strike.toLocaleString()}
                {row.is_atm && <span className="ml-1 text-[8px] bg-[#00d4ff] text-black px-1 rounded">ATM</span>}
              </td>

              {/* Put Data */}
              <td 
                className="p-2 font-bold text-[#ffa0b0] cursor-pointer hover:text-[#00d4ff] hover:underline"
                onClick={() => onSelectOption(row.strike, 'PE')}
              >
                {f(row.put.last_price, 2)}
              </td>
              <td className="p-2 text-[#ffa0b0]">{f(row.put.vega, 2)}</td>
              <td className="p-2 text-[#ffa0b0]">{f(row.put.theta, 2)}</td>
              <td className="p-2 text-[#ffa0b0]">{f(row.put.gamma, 4)}</td>
              <td className="p-2 text-[#ff4d6a]">{f(row.put.delta, 3)}</td>
              <td className="p-2 text-[#f0b429]">{f(row.put.iv_pct, 1)}</td>
            </tr>
          ))}
          {chain.length === 0 && (
            <tr>
              <td colSpan={13} className="p-8 text-center text-[#7a9bb5]">
                No data available for this minute. Scrub timeline to find data.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
};
