import React, { useState, useEffect, useCallback } from 'react';
import { historicalApi } from '../services/historical_api';
import { ReplayController } from '../components/historical/ReplayController';
import { HistoricalOptionChain } from '../components/historical/HistoricalOptionChain';
import { HistoricalChart } from '../components/historical/HistoricalChart';
import type { HistoricalChainRow, OHLCData } from '../types/historical';

export const HistoricalDashboard: React.FC = () => {
  const [dataRange, setDataRange] = useState<{ min_ts: number, max_ts: number } | null>(null);
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
    Promise.all([
      historicalApi.getLatestAvailableData(),
      historicalApi.getDataRange()
    ]).then(([latest, range]) => {
      setDataRange(range);
      setSimulationDate(latest.latestDate);
      setSimulationTime(latest.latestTime);
    }).catch(console.error);
  }, []);

  // Helper to generate expiries locally based on simulation date
  const generateExpiries = useCallback((simDate: string) => {
    if (!simDate) return [];
    
    const base = new Date(simDate);
    const expList: {date: string, label: string}[] = [];

    // 1. Current
    const current = new Date(base);
    const currentStr = current.toISOString().split('T')[0];
    expList.push({ date: currentStr, label: `Current (${currentStr})` });

    // 2. Next
    const next = new Date(base);
    next.setDate(base.getDate() + 1);
    const nextStr = next.toISOString().split('T')[0];
    expList.push({ date: nextStr, label: `Next (${nextStr})` });

    // 3. Next-to-Next
    const ntn = new Date(base);
    ntn.setDate(base.getDate() + 2);
    const ntnStr = ntn.toISOString().split('T')[0];
    expList.push({ date: ntnStr, label: `Next-to-Next (${ntnStr})` });

    // 4. Weekly (Find next Friday that is at least 3 days away)
    let weekly = new Date(base);
    weekly.setDate(base.getDate() + 3); // Start looking from 3 days ahead
    while (weekly.getDay() !== 5) { // 5 is Friday
      weekly.setDate(weekly.getDate() + 1);
    }
    const weeklyStr = weekly.toISOString().split('T')[0];
    expList.push({ date: weeklyStr, label: `Weekly (${weeklyStr})` });

    return expList;
  }, []);

  // Update Expiries when Date changes
  useEffect(() => {
    if (simulationDate) {
      const newList = generateExpiries(simulationDate);
      setExpiries(newList);
      
      // If current selected isn't in the new list, default to Current
      if (newList.length > 0 && !newList.find(e => e.date === selectedExpiry)) {
        setSelectedExpiry(newList[0].date);
      }
    } else {
      setExpiries([]);
    }
  }, [simulationDate, generateExpiries]);

  // 2. Adjust Time Logic (The Stepper) with Rollover
  const adjustSimulationTime = useCallback((minutesToAdd: number) => {
    if (!simulationDate || !simulationTime) return;

    const current = new Date(`${simulationDate}T${simulationTime}:00+05:30`);
    current.setUTCMinutes(current.getUTCMinutes() + minutesToAdd);

    const newDate = current.toISOString().split('T')[0];
    const newTime = current.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });

    setSimulationDate(newDate);
    setSimulationTime(newTime);
  }, [simulationDate, simulationTime]);

  // 3. Side Effect: Fetch new chain when strict parameters change
  useEffect(() => {
    if (selectedExpiry && simulationDate && simulationTime) {
      const istString = `${simulationDate}T${simulationTime}:00+05:30`;
      const timestamp = Math.floor(new Date(istString).getTime() / 1000);
      
      historicalApi.getOptionChain(selectedExpiry, timestamp).then(res => {
        setChain(res.chain);
        setSpot((res as any).spot_actual || 0);
      }).catch(err => {
        console.error("Option chain fetch failed", err);
        setChain([]);
        setSpot(0);
      });
    }
  }, [selectedExpiry, simulationDate, simulationTime]);

  // 4. Fetch Chart Data
  useEffect(() => {
    if (selectedExpiry && selectedOption && simulationDate && simulationTime) {
      const istString = `${simulationDate}T${simulationTime}:00+05:30`;
      const startOfDay = Math.floor(new Date(`${simulationDate}T00:00:00+05:30`).getTime() / 1000);
      
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
  }, [selectedExpiry, selectedOption, timeframe, simulationDate, simulationTime]);

  return (
    <div className="historical-container">
      <div className="replay-wrapper">
        <ReplayController 
          simulationDate={simulationDate}
          simulationTime={simulationTime}
          expiries={expiries}
          selectedExpiry={selectedExpiry}
          minDate={dataRange ? new Date(dataRange.min_ts * 1000).toISOString().split('T')[0] : ''}
          maxDate={dataRange ? new Date(dataRange.max_ts * 1000).toISOString().split('T')[0] : ''}
          onDateChange={setSimulationDate}
          onTimeChange={setSimulationTime}
          onExpiryChange={setSelectedExpiry}
          onStep={adjustSimulationTime}
        />
      </div>

      <div className="historical-toolbar-secondary">
        <div className="ctrl-group">
          <label className="ctrl-label">Actual Spot</label>
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
