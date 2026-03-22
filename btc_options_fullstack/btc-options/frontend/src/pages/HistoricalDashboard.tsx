import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { historicalApi } from '../services/historical_api';
import { ReplayController } from '../components/historical/ReplayController';
import { HistoricalOptionChain } from '../components/historical/HistoricalOptionChain';
import { HistoricalChart } from '../components/historical/HistoricalChart';
import { StrategyPanel } from '../components/historical/StrategyPanel';
import type { HistoricalChainRow, OHLCData } from '../types/historical';
import type { StrategyLeg } from '../types/strategy';

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

  // Strategy Builder
  const [strategyMode, setStrategyMode] = useState(false);
  const [strategyLegs, setStrategyLegs] = useState<StrategyLeg[]>([]);

  // AbortControllers to prevent race conditions
  const chainAbortController = useRef<AbortController | null>(null);
  const chartAbortController = useRef<AbortController | null>(null);

  // Computed simulation timestamp (IST) — as reactive value for margin engine
  const currentSimTimestamp = useMemo(() => {
    if (!simulationDate || !simulationTime) return 0;
    return Math.floor(new Date(`${simulationDate}T${simulationTime}:00+05:30`).getTime() / 1000);
  }, [simulationDate, simulationTime]);

  // Also as callback for addLeg (needs latest value at click time)
  const simTimestamp = useCallback(() => currentSimTimestamp, [currentSimTimestamp]);

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

    // Use UTC throughout to avoid local-timezone date shifts
    const base = new Date(simDate + 'T00:00:00Z');
    const maxDate = new Date(
      new Date(dataRange.max_ts * 1000).toISOString().split('T')[0] + 'T00:00:00Z'
    );

    const addedDates = new Set<string>();
    const expList: {date: string, label: string}[] = [];

    const addIfValid = (dateObj: Date, label: string) => {
      const dateStr = dateObj.toISOString().split('T')[0];
      if (dateObj <= maxDate && !addedDates.has(dateStr)) {
        addedDates.add(dateStr);
        expList.push({ date: dateStr, label: `${label} (${dateStr})` });
      }
    };

    // Next Friday strictly after `from`
    const nextFridayAfter = (from: Date): Date => {
      const d = new Date(from);
      d.setUTCDate(d.getUTCDate() + 1);
      while (d.getUTCDay() !== 5) d.setUTCDate(d.getUTCDate() + 1);
      return d;
    };

    // Last Friday of a calendar month (0-indexed month, handles overflow)
    const lastFridayOfMonth = (year: number, month: number): Date => {
      const d = new Date(Date.UTC(year, month + 1, 0)); // last day of month
      while (d.getUTCDay() !== 5) d.setUTCDate(d.getUTCDate() - 1);
      return d;
    };

    // Daily
    addIfValid(base, 'Current');
    const next = new Date(base); next.setUTCDate(base.getUTCDate() + 1);
    addIfValid(next, 'Next');
    const ntn = new Date(base); ntn.setUTCDate(base.getUTCDate() + 2);
    addIfValid(ntn, 'Next-to-Next');

    // Monthly first so "Monthly" label wins when it coincides with a weekly
    const baseYear = base.getUTCFullYear();
    const baseMonth = base.getUTCMonth();
    let monthly = lastFridayOfMonth(baseYear, baseMonth);
    if (monthly <= base) monthly = lastFridayOfMonth(baseYear, baseMonth + 1);
    addIfValid(monthly, 'Monthly');

    const nextMonthly = lastFridayOfMonth(monthly.getUTCFullYear(), monthly.getUTCMonth() + 1);
    addIfValid(nextMonthly, 'Next Monthly');

    // Weekly (deduped against dailies and monthlies above)
    const w1 = nextFridayAfter(base);
    addIfValid(w1, 'Next Weekly');

    const w2 = new Date(w1);
    w2.setUTCDate(w1.getUTCDate() + 7);
    addIfValid(w2, 'Next-to-Next Weekly');

    expList.sort((a, b) => a.date.localeCompare(b.date));
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
    if (chain.length > 0 && !strategyMode) {
      const atmRow = chain.find(r => r.is_atm);
      if (atmRow) {
        setSelectedOption({ strike: atmRow.strike, type: 'CE' });
      }
      setTimeout(() => {
        const atmEl = document.querySelector('.atm-row');
        if (atmEl) atmEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
    }
  }, [chain, strategyMode]);

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

      // Pin strikes for any strategy legs on this expiry so they stay in the chain even after BTC moves
      const pinStrikes = strategyLegs
        .filter(l => l.expiry === selectedExpiry)
        .map(l => l.strike);

      historicalApi.getOptionChain(selectedExpiry, timestamp, chainAbortController.current.signal, pinStrikes.length ? pinStrikes : undefined).then(res => {
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
  }, [selectedExpiry, simulationDate, simulationTime, expiries, strategyLegs]);

  // 4. Fetch Chart Data with AbortController
  useEffect(() => {
    if (selectedExpiry && selectedOption && simulationDate && !strategyMode) {
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
  }, [selectedExpiry, selectedOption, timeframe, strategyMode]);

  // Strategy: add a leg
  const addLeg = useCallback((strike: number, type: 'CE' | 'PE', action: 'BUY' | 'SELL', premium: number) => {
    const ts = simTimestamp();
    setStrategyLegs(prev => [...prev, {
      id: `${Date.now()}-${strike}-${type}-${action}`,
      expiry: selectedExpiry,
      strike,
      type,
      action,
      qty: 1,
      entryPremium: premium,
      entryTimestamp: ts,
    }]);
  }, [selectedExpiry, simTimestamp]);

  const removeLeg = useCallback((id: string) => {
    setStrategyLegs(prev => prev.filter(l => l.id !== id));
  }, []);

  const updateLegQty = useCallback((id: string, qty: number) => {
    setStrategyLegs(prev => prev.map(l => l.id === id ? { ...l, qty } : l));
  }, []);

  const clearLegs = useCallback(() => setStrategyLegs([]), []);

  const handleStrategyToggle = () => {
    setStrategyMode(prev => !prev);
  };

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
          spot={spot}
          strikeFilter={strikeFilter}
          onDateChange={setSimulationDate}
          onTimeChange={setSimulationTime}
          onExpiryChange={setSelectedExpiry}
          onStep={adjustSimulationTime}
          onStrikeFilterChange={setStrikeFilter}
        />
      </div>

      <div className="historical-main">
        <div className="historical-chain-panel">
          <HistoricalOptionChain
            chain={strikeFilter ? chain.filter(r => r.strike.toString().includes(strikeFilter)) : chain}
            strategyMode={strategyMode}
            legMap={(() => {
              const m = new Map<number, { ce?: 'BUY'|'SELL'; pe?: 'BUY'|'SELL' }>();
              strategyLegs.filter(l => l.expiry === selectedExpiry).forEach(l => {
                const entry = m.get(l.strike) ?? {};
                if (l.type === 'CE') entry.ce = l.action;
                else entry.pe = l.action;
                m.set(l.strike, entry);
              });
              return m;
            })()}
            onSelectOption={(s, t) => setSelectedOption({ strike: s, type: t })}
            onAddLeg={addLeg}
          />
        </div>

        <div className="historical-chart-panel" style={{ width: strategyMode ? 'clamp(300px, 40vw, 560px)' : 'clamp(280px, 35vw, 500px)' }}>
          {/* Panel mode toggle tabs */}
          <div className="chart-mode-bar">
            <button
              className={`chart-mode-tab${!strategyMode ? ' active' : ''}`}
              onClick={() => { setStrategyMode(false); }}
            >
              Chart
            </button>
            <button
              className={`chart-mode-tab strategy${strategyMode ? ' active' : ''}`}
              onClick={() => { setStrategyMode(true); }}
            >
              Strategy Builder
              {strategyLegs.length > 0 && (
                <span className="strategy-leg-count">{strategyLegs.length}</span>
              )}
            </button>
          </div>

          {strategyMode ? (
            <StrategyPanel
              legs={strategyLegs}
              chain={chain}
              spot={spot}
              selectedExpiry={selectedExpiry}
              simulationTimestamp={currentSimTimestamp}
              onRemoveLeg={removeLeg}
              onUpdateQty={updateLegQty}
              onClearAll={clearLegs}
            />
          ) : (
            <>
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
                  <div className="chart-body" style={{ flex: 1, padding: 0, minHeight: 0 }}>
                    <HistoricalChart data={chartData} title="" />
                  </div>
                </div>
              ) : (
                <div className="chart-card" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)' }}>
                  Select an option from the chain to view chart
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
