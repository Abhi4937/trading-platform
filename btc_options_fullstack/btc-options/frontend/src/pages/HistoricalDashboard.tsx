import React, { useState, useEffect } from 'react';
import { historicalApi } from '../services/historical_api';
import { TimeSlider } from '../components/historical/TimeSlider';
import { HistoricalOptionChain } from '../components/historical/HistoricalOptionChain';
import { HistoricalChart } from '../components/historical/HistoricalChart';
import type { HistoricalChainRow, OHLCData } from '../types/historical';

export const HistoricalDashboard: React.FC = () => {
  const [expiries, setExpiries] = useState<string[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');
  const [currentTimestamp, setCurrentTimestamp] = useState<number>(0);
  
  const [chain, setChain] = useState<HistoricalChainRow[]>([]);
  const [spot, setSpot] = useState<number>(0);
  
  const [selectedOption, setSelectedOption] = useState<{strike: number, type: 'CE' | 'PE'} | null>(null);
  const [timeframe, setTimeframe] = useState<string>('5m');
  const [chartData, setChartData] = useState<OHLCData[]>([]);

  useEffect(() => {
    historicalApi.getExpiries().then(res => {
      if (res.expiries.length > 0) {
        setExpiries(res.expiries);
        setSelectedExpiry(res.expiries[0]);
      }
    }).catch(console.error);
  }, []);

  useEffect(() => {
    if (selectedExpiry && currentTimestamp > 0) {
      historicalApi.getOptionChain(selectedExpiry, currentTimestamp).then(res => {
        setChain(res.chain);
        setSpot(res.spot_inferred);
      }).catch(console.error);
    }
  }, [selectedExpiry, currentTimestamp]);

  useEffect(() => {
    if (selectedExpiry && selectedOption && currentTimestamp > 0) {
      // Start of day for chart
      const startOfDay = new Date(`${selectedExpiry}T00:00:00Z`).getTime() / 1000;
      historicalApi.getChartData(
        selectedExpiry, 
        selectedOption.strike, 
        selectedOption.type, 
        startOfDay, 
        timeframe
      ).then(res => {
        setChartData(res.data);
      }).catch(console.error);
    }
  }, [selectedExpiry, selectedOption, timeframe]);

  return (
    <div className="flex flex-col h-screen w-full bg-[#050a0f] text-[#e2eaf4] p-4 font-mono">
      {/* Header */}
      <div className="flex justify-between items-center mb-4 bg-[#080e16] p-4 rounded-lg border border-[#1a2d42]">
        <h1 className="text-xl font-bold text-[#00d4ff]">Historical Options Dashboard</h1>
        
        <div className="flex gap-4 items-center">
          <div className="flex flex-col">
            <label className="text-xs text-[#7a9bb5] uppercase tracking-wider">Expiry</label>
            <select 
              className="bg-[#0c1420] border border-[#1a2d42] rounded p-1 outline-none focus:border-[#00d4ff]"
              value={selectedExpiry}
              onChange={e => setSelectedExpiry(e.target.value)}
            >
              {expiries.map(exp => <option key={exp} value={exp}>{exp}</option>)}
            </select>
          </div>
          
          <div className="flex flex-col">
            <span className="text-xs text-[#7a9bb5] uppercase tracking-wider">Inferred Spot</span>
            <span className="text-lg font-bold text-[#00e5a0]">{spot.toLocaleString()}</span>
          </div>
        </div>
      </div>

      <TimeSlider date={selectedExpiry || new Date().toISOString().split('T')[0]} onTimeChange={setCurrentTimestamp} />

      <div className="flex flex-1 gap-4 min-h-0">
        {/* Chain Panel */}
        <div className="flex-1 flex flex-col min-w-0">
          <HistoricalOptionChain chain={chain} onSelectOption={(s, t) => setSelectedOption({strike: s, type: t})} />
        </div>

        {/* Chart Panel */}
        <div className="w-[500px] flex flex-col gap-4">
          {selectedOption ? (
            <div className="bg-[#080e16] p-4 rounded-lg border border-[#1a2d42] flex flex-col flex-1">
              <div className="flex justify-between mb-2">
                <select 
                  className="bg-[#0c1420] border border-[#1a2d42] text-xs rounded p-1 outline-none"
                  value={timeframe}
                  onChange={e => setTimeframe(e.target.value)}
                >
                  {['1m', '5m', '15m', '30m', '1h'].map(tf => <option key={tf} value={tf}>{tf}</option>)}
                </select>
              </div>
              <HistoricalChart 
                data={chartData} 
                title={`${selectedOption.strike} ${selectedOption.type} (${timeframe})`} 
              />
            </div>
          ) : (
            <div className="bg-[#080e16] p-4 rounded-lg border border-[#1a2d42] flex-1 flex items-center justify-center text-[#7a9bb5]">
              Select an option from the chain to view chart
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
