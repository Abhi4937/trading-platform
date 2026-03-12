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
    <div className="historical-container">
      <TimeSlider date={selectedExpiry || new Date().toISOString().split('T')[0]} onTimeChange={setCurrentTimestamp} />

      <div className="historical-main">
        <div className="historical-chain-panel">
          <div className="chain-toolbar">
            <div className="ctrl-group">
              <label className="ctrl-label">Inferred Spot</label>
              <div className="spot-price" style={{ fontSize: '16px', color: 'var(--green)' }}>
                ${spot.toLocaleString()}
              </div>
            </div>
            <div className="sep" />
            <div className="ctrl-group">
              <label className="ctrl-label">Expiry Date</label>
              <select 
                className="sel-input"
                value={selectedExpiry}
                onChange={e => setSelectedExpiry(e.target.value)}
              >
                {expiries.map(exp => <option key={exp} value={exp}>{exp}</option>)}
              </select>
            </div>
          </div>
          <HistoricalOptionChain chain={chain} onSelectOption={(s, t) => setSelectedOption({strike: s, type: t})} />
        </div>

        <div className="historical-chart-panel">
          {selectedOption ? (
            <div className="chart-card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <div className="chart-header">
                <div className="chart-title">
                  {selectedOption.strike} {selectedOption.type}
                  <div className="chart-sub">{timeframe} OHLC</div>
                </div>
                <div className="tf-group">
                  {['1m', '5m', '15m', '30m', '1h'].map(tf => (
                    <button 
                      key={tf} 
                      className={`tf-btn ${timeframe === tf ? 'active' : ''}`}
                      onClick={() => setTimeframe(tf)}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
              </div>
              <div className="chart-body" style={{ flex: 1, padding: '12px' }}>
                <HistoricalChart 
                  data={chartData} 
                  title="" 
                />
              </div>
            </div>
          ) : (
            <div className="chart-card" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)' }}>
              Select an option from the chain to view chart
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
