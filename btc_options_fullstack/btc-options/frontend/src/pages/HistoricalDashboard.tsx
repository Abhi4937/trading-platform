import React, { useState, useEffect, useCallback } from 'react';
import { historicalApi } from '../services/historical_api';
import { ReplayController } from '../components/historical/ReplayController';
import { HistoricalOptionChain } from '../components/historical/HistoricalOptionChain';
import { HistoricalChart } from '../components/historical/HistoricalChart';
import type { HistoricalChainRow, OHLCData } from '../types/historical';

export const HistoricalDashboard: React.FC = () => {
  const [simulationDate, setSimulationDate] = useState<string>(''); // YYYY-MM-DD
  const [simulationTime, setSimulationTime] = useState<string>(''); // HH:mm
  const [expiries, setExpiries] = useState<{date: string, label: string}[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');
  
  const [chain, setChain] = useState<HistoricalChainRow[]>([]);
  const [spot, setSpot] = useState<number>(0);
  const [strikeFilter, setStrikeFilter] = useState<string>('');
  
  const [selectedOption, setSelectedOption] = useState<{strike: number, type: 'CE' | 'PE'} | null>(null);
  const [timeframe, setTimeframe] = useState<string>('5m');
  const [chartData, setChartData] = useState<OHLCData[]>([]);

  // 1. Initial State Initialization from Backend
  useEffect(() => {
    historicalApi.getLatestAvailableData().then(data => {
      setSimulationDate(data.latestDate);
      setSimulationTime(data.latestTime);
      setExpiries(data.expiries);
      if (data.expiries.length > 0) {
        setSelectedExpiry(data.expiries[0].date);
      }
    }).catch(console.error);
  }, []);

  // 2. Adjust Time Logic (The Stepper) with Rollover
  const adjustSimulationTime = useCallback((minutesToAdd: number) => {
    if (!simulationDate || !simulationTime) return;

    // Use a Date object to handle overflows correctly
    // We treat state as UTC to avoid local timezone interference during calculations
    const current = new Date(`${simulationDate}T${simulationTime}:00Z`);
    current.setUTCMinutes(current.getUTCMinutes() + minutesToAdd);

    const newDate = current.toISOString().split('T')[0];
    const newTime = current.toISOString().split('T')[1].substring(0, 5);

    setSimulationDate(newDate);
    setSimulationTime(newTime);
  }, [simulationDate, simulationTime]);

  // 3. Side Effect: Fetch new chain when strict parameters change
  // simulationDate is always YYYY-MM-DD
  // simulationTime is always HH:mm
  useEffect(() => {
    if (selectedExpiry && simulationDate && simulationTime) {
      const timestamp = Math.floor(new Date(`${simulationDate}T${simulationTime}:00Z`).getTime() / 1000);
      
      // LOG for verification
      console.log(`Fetching Chain for: Date=${simulationDate}, Time=${simulationTime}, TS=${timestamp}`);
      
      historicalApi.getOptionChain(selectedExpiry, timestamp).then(res => {
        setChain(res.chain);
        setSpot(res.spot_inferred);
      }).catch(err => {
        console.error("Option chain fetch failed", err);
        setChain([]);
        setSpot(0);
      });
    }
  }, [selectedExpiry, simulationDate, simulationTime]);

  // 4. Fetch Chart Data
  useEffect(() => {
    if (selectedExpiry && selectedOption && simulationDate) {
      const startOfDay = new Date(`${simulationDate}T00:00:00Z`).getTime() / 1000;
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
  }, [selectedExpiry, selectedOption, timeframe, simulationDate]);

  return (
    <div className="historical-container">
      <div className="replay-wrapper">
        <ReplayController 
          simulationDate={simulationDate}
          simulationTime={simulationTime}
          expiries={expiries}
          selectedExpiry={selectedExpiry}
          onDateChange={setSimulationDate}
          onTimeChange={setSimulationTime}
          onExpiryChange={setSelectedExpiry}
          onStep={adjustSimulationTime}
        />
      </div>

      <div className="historical-toolbar-secondary">
        <div className="ctrl-group">
          <label className="ctrl-label">Inferred Spot</label>
          <div className="spot-price" style={{ fontSize: '18px', color: 'var(--green)' }}>
            ${spot.toLocaleString()}
          </div>
        </div>
        <div className="sep" />
        <div className="ctrl-group" style={{ flex: 1 }}>
          <label className="ctrl-label">Search Strike</label>
          <input
            className="search-input"
            style={{ width: '100%' }}
            placeholder="Type strike..."
            value={strikeFilter}
            onChange={e => setStrikeFilter(e.target.value)}
          />
        </div>
      </div>

      <div className="historical-main">
        <div className="historical-chain-panel">
          <HistoricalOptionChain 
            chain={strikeFilter ? chain.filter(r => r.strike.toString().includes(strikeFilter)) : chain} 
            onSelectOption={(s, t) => setSelectedOption({strike: s, type: t})} 
          />
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
