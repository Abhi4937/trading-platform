import React, { useState, useEffect, useCallback, useRef } from 'react';
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

  // AbortControllers to prevent race conditions
  const chainAbortController = useRef<AbortController | null>(null);
  const chartAbortController = useRef<AbortController | null>(null);
  const expiryAbortController = useRef<AbortController | null>(null);

  // 1. Initial State Initialization
  useEffect(() => {
    Promise.all([
      historicalApi.getLatestAvailableData(),
      historicalApi.getDataRange()
    ]).then(([latest, range]) => {
      setDataRange(range);
      setSimulationDate(latest.latestDate);
      setSimulationTime('00:00'); 
    }).catch(console.error);
  }, []);

  // Helper to generate expiries locally
  const generateExpiries = useCallback((simDate: string) => {
    if (!simDate) return [];
    const base = new Date(simDate);
    const expList: {date: string, label: string}[] = [];
    const currentStr = new Date(base).toISOString().split('T')[0];
    expList.push({ date: currentStr, label: `Current (${currentStr})` });
    const next = new Date(base); next.setDate(base.getDate() + 1);
    const nextStr = next.toISOString().split('T')[0];
    expList.push({ date: nextStr, label: `Next (${nextStr})` });
    const ntn = new Date(base); ntn.setDate(base.getDate() + 2);
    const ntnStr = ntn.toISOString().split('T')[0];
    expList.push({ date: ntnStr, label: `Next-to-Next (${ntnStr})` });
    let weekly = new Date(base); weekly.setDate(base.getDate() + 3);
    while (weekly.getDay() !== 5) { weekly.setDate(weekly.getDate() + 1); }
    const weeklyStr = weekly.toISOString().split('T')[0];
    expList.push({ date: weeklyStr, label: `Weekly (${weeklyStr})` });
    return expList;
  }, []);

  useEffect(() => {
    if (simulationDate) {
      const newList = generateExpiries(simulationDate);
      setExpiries(newList);
      if (newList.length > 0 && !newList.find(e => e.date === selectedExpiry)) {
        setSelectedExpiry(newList[0].date);
      }
    } else {
      setExpiries([]);
    }
  }, [simulationDate, generateExpiries]);

  // 2. Adjust Time Logic
  const adjustSimulationTime = useCallback((minutesToAdd: number) => {
    if (!simulationDate || !simulationTime) return;
    const current = new Date(`${simulationDate}T${simulationTime}:00+05:30`);
    current.setUTCMinutes(current.getUTCMinutes() + minutesToAdd);
    const formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false
    });
    const parts = formatter.formatToParts(current);
    const year = parts.find(p => p.type === 'year')?.value;
    const month = parts.find(p => p.type === 'month')?.value;
    const day = parts.find(p => p.type === 'day')?.value;
    const hour = parts.find(p => p.type === 'hour')?.value;
    const minute = parts.find(p => p.type === 'minute')?.value;
    setSimulationDate(`${year}-${month}-${day}`);
    setSimulationTime(`${hour}:${minute}`);
  }, [simulationDate, simulationTime]);

  // Auto-scroll to ATM
  useEffect(() => {
    if (chain.length > 0) {
      setTimeout(() => {
        const atmRow = document.querySelector('.atm-row');
        if (atmRow) atmRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
    }
  }, [chain]);

  // 3. Fetch Option Chain with AbortController
  useEffect(() => {
    if (selectedExpiry && simulationDate && simulationTime) {
      // Cancel previous request
      if (chainAbortController.current) chainAbortController.current.abort();
      chainAbortController.current = new AbortController();

      const istString = `${simulationDate}T${simulationTime}:00+05:30`;
      const timestamp = Math.floor(new Date(istString).getTime() / 1000);
      
      historicalApi.getOptionChain(selectedExpiry, timestamp, chainAbortController.current.signal).then(res => {
        setChain(res.chain);
        setSpot((res as any).spot_actual || 0);
      }).catch(err => {
        if (err.name === 'AbortError') return;
        console.error("Option chain fetch failed", err);
        setChain([]);
        setSpot(0);
      });
    }
    return () => { if (chainAbortController.current) chainAbortController.current.abort(); };
  }, [selectedExpiry, simulationDate, simulationTime]);

  // 4. Fetch Chart Data with AbortController
  useEffect(() => {
    if (selectedExpiry && selectedOption && simulationDate) {
      // Cancel previous request
      if (chartAbortController.current) chartAbortController.current.abort();
      chartAbortController.current = new AbortController();

      historicalApi.getChartData(
        selectedExpiry, 
        selectedOption.strike, 
        selectedOption.type, 
        0, 
        timeframe,
        chartAbortController.current.signal
      ).then(res => {
        setChartData(res.data);
      }).catch(err => {
        if (err.name === 'AbortError') return;
        console.error("Chart data fetch failed", err);
      });
    }
    return () => { if (chartAbortController.current) chartAbortController.current.abort(); };
  }, [selectedExpiry, selectedOption, timeframe]);

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
