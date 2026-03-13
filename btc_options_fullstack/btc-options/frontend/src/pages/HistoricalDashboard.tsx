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
    if (!simDate || !dataRange) return [];
    
    const base = new Date(simDate);
    const maxDateStr = new Date(dataRange.max_ts * 1000).toISOString().split('T')[0];
    const maxDate = new Date(maxDateStr);
    
    const expList: {date: string, label: string}[] = [];

    const addIfValid = (dateObj: Date, labelPrefix: string) => {
      const dateStr = dateObj.toISOString().split('T')[0];
      if (dateObj <= maxDate) {
        expList.push({ date: dateStr, label: `${labelPrefix} (${dateStr})` });
      }
    };

    // 1. Current
    addIfValid(new Date(base), 'Current');

    // 2. Next
    const next = new Date(base); next.setDate(base.getDate() + 1);
    addIfValid(next, 'Next');

    // 3. Next-to-Next
    const ntn = new Date(base); ntn.setDate(base.getDate() + 2);
    addIfValid(ntn, 'Next-to-Next');

    // 4. Weekly (Find next Friday that is at least 3 days away)
    let weekly = new Date(base);
    weekly.setDate(base.getDate() + 3); 
    while (weekly.getDay() !== 5) { 
      weekly.setDate(weekly.getDate() + 1);
    }
    addIfValid(weekly, 'Weekly');

    return expList;
  }, [dataRange]);

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

  // Auto-select ATM call on chain load + expiry change, auto-scroll to ATM
  useEffect(() => {
    if (chain.length > 0) {
      const atmRow = chain.find(r => r.is_atm);
      if (atmRow) {
        setSelectedOption({ strike: atmRow.strike, type: 'CE' });
      }
      setTimeout(() => {
        const atmEl = document.querySelector('.atm-row');
        if (atmEl) atmEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
    }
  }, [chain]);

  // Reset selected option when expiry changes
  useEffect(() => {
    setSelectedOption(null);
    setChain([]);
  }, [selectedExpiry]);

  // 3. Fetch Option Chain with AbortController + debounce
  useEffect(() => {
    const isValidExpiry = expiries.some(e => e.date === selectedExpiry);
    if (!selectedExpiry || !simulationDate || !simulationTime || !isValidExpiry) return;

    const timer = setTimeout(() => {
      if (chainAbortController.current) chainAbortController.current.abort();
      chainAbortController.current = new AbortController();

      const timestamp = Math.floor(new Date(`${simulationDate}T${simulationTime}:00+05:30`).getTime() / 1000);

      historicalApi.getOptionChain(selectedExpiry, timestamp, chainAbortController.current.signal).then(res => {
        setChain(res.chain);
        setSpot((res as any).spot_actual || 0);
      }).catch(err => {
        if (err.name === 'AbortError') return;
        console.error("Option chain fetch failed", err);
        setChain([]);
        setSpot(0);
      });
    }, 300);

    return () => { clearTimeout(timer); if (chainAbortController.current) chainAbortController.current.abort(); };
  }, [selectedExpiry, simulationDate, simulationTime, expiries]);

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
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <div className="chart-title">{selectedOption.strike} — {selectedExpiry}</div>
                  <div className="tf-group">
                    {(['CE', 'PE'] as const).map(t => (
                      <button
                        key={t}
                        className={`tf-btn ${selectedOption.type === t ? 'active' : ''}`}
                        onClick={() => setSelectedOption({ strike: selectedOption.strike, type: t })}
                      >
                        {t}
                      </button>
                    ))}
                  </div>
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
