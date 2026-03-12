import React, { useState, useEffect, useCallback } from 'react';
import { historicalApi } from '../services/historical_api';
import { TimeSlider } from '../components/historical/TimeSlider';
import { HistoricalOptionChain } from '../components/historical/HistoricalOptionChain';
import { HistoricalChart } from '../components/historical/HistoricalChart';
import type { HistoricalChainRow, OHLCData } from '../types/historical';

export const HistoricalDashboard: React.FC = () => {
  const [dataRange, setDataRange] = useState<{ min_ts: number, max_ts: number } | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [currentTimestamp, setCurrentTimestamp] = useState<number>(0);
  
  const [expiries, setExpiries] = useState<{date: string, label: string}[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');
  
  const [chain, setChain] = useState<HistoricalChainRow[]>([]);
  const [spot, setSpot] = useState<number>(0);
  
  const [selectedOption, setSelectedOption] = useState<{strike: number, type: 'CE' | 'PE'} | null>(null);
  const [timeframe, setTimeframe] = useState<string>('5m');
  const [chartData, setChartData] = useState<OHLCData[]>([]);

  // Initial Data Range
  useEffect(() => {
    historicalApi.getDataRange().then(range => {
      setDataRange(range);
      if (range.max_ts) {
        // Default to the date and time of the latest data
        const latest = new Date(range.max_ts * 1000);
        const dateStr = latest.toISOString().split('T')[0];
        setSelectedDate(dateStr);
        setCurrentTimestamp(range.max_ts);
      }
    }).catch(console.error);
  }, []);

  // Fetch Expiries when Date changes
  useEffect(() => {
    if (selectedDate) {
      historicalApi.getExpiries(selectedDate).then(res => {
        const categorized = (res as any).expiries || [];
        setExpiries(categorized);
        if (categorized.length > 0) {
          setSelectedExpiry(categorized[0].date);
        }
      }).catch(console.error);
    }
  }, [selectedDate]);

  // Fetch Option Chain
  const fetchChain = useCallback(() => {
    if (selectedExpiry && currentTimestamp > 0) {
      historicalApi.getOptionChain(selectedExpiry, currentTimestamp).then(res => {
        setChain(res.chain);
        setSpot(res.spot_inferred);
      }).catch(console.error);
    }
  }, [selectedExpiry, currentTimestamp]);

  useEffect(() => {
    fetchChain();
  }, [fetchChain]);

  // Fetch Chart Data
  useEffect(() => {
    if (selectedExpiry && selectedOption && currentTimestamp > 0) {
      const startOfDay = new Date(`${selectedDate}T00:00:00Z`).getTime() / 1000;
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
  }, [selectedExpiry, selectedOption, timeframe, selectedDate, currentTimestamp]);

  const handleTimeChange = (ts: number) => {
    setCurrentTimestamp(ts);
  };

  const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newDate = e.target.value;
    setSelectedDate(newDate);
    // Reset timestamp to start of that day in UTC/IST
    const ts = Math.floor(new Date(`${newDate}T00:00:00Z`).getTime() / 1000);
    setCurrentTimestamp(ts);
  };

  return (
    <div className="historical-container">
      <div className="time-slider-card" style={{ marginBottom: '8px' }}>
        <div className="chain-toolbar" style={{ borderBottom: 'none', padding: '0 0 12px 0' }}>
           <div className="ctrl-group">
              <label className="ctrl-label">Simulation Date</label>
              <input 
                type="date" 
                className="date-input" 
                value={selectedDate}
                onChange={handleDateChange}
                min={dataRange ? new Date(dataRange.min_ts * 1000).toISOString().split('T')[0] : ''}
                max={dataRange ? new Date(dataRange.max_ts * 1000).toISOString().split('T')[0] : ''}
              />
           </div>
           <div className="sep" />
           <div className="ctrl-group">
              <label className="ctrl-label">Option Expiry</label>
              <select 
                className="sel-input"
                value={selectedExpiry}
                onChange={e => setSelectedExpiry(e.target.value)}
              >
                {expiries.map(exp => <option key={exp.date} value={exp.date}>{exp.label}</option>)}
              </select>
            </div>
            <div className="sep" />
            <div className="ctrl-group">
              <label className="ctrl-label">Inferred Spot</label>
              <div className="spot-price" style={{ fontSize: '18px', color: 'var(--green)' }}>
                ${spot.toLocaleString()}
              </div>
            </div>
        </div>
        
        {selectedDate && (
          <TimeSlider 
            date={selectedDate} 
            initialTimestamp={currentTimestamp}
            onTimeChange={handleTimeChange} 
          />
        )}
      </div>

      <div className="historical-main">
        <div className="historical-chain-panel">
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
