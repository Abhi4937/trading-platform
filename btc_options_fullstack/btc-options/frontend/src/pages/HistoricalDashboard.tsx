import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { historicalApi } from '../services/historical_api';
import { ReplayController } from '../components/historical/ReplayController';
import { HistoricalOptionChain } from '../components/historical/HistoricalOptionChain';
import { HistoricalChart } from '../components/historical/HistoricalChart';
import { StrategyPanel } from '../components/historical/StrategyPanel';
import type { HistoricalChainRow, OHLCData } from '../types/historical';
import type { Strategy, StrategyLeg } from '../types/strategy';

export const HistoricalDashboard: React.FC = () => {
  const [dataRange, setDataRange] = useState<{ min_ts: number, max_ts: number } | null>(null);
  const [simulationDate, setSimulationDate] = useState<string>('');
  const [simulationTime, setSimulationTime] = useState<string>('');
  const [expiries, setExpiries] = useState<{date: string, label: string}[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');

  const [chain, setChain] = useState<HistoricalChainRow[]>([]);
  const [spot, setSpot] = useState<number>(0);
  const [strikeFilter, setStrikeFilter] = useState<string>('');

  const [selectedOption, setSelectedOption] = useState<{strike: number, type: 'CE' | 'PE'} | null>(null);
  const [timeframe, setTimeframe] = useState<string>('5m');
  const [chartData, setChartData] = useState<OHLCData[]>([]);

  // Panel mode
  const [strategyMode, setStrategyMode] = useState(false);

  // Build mode — single strategy legs
  const [strategyLegs, setStrategyLegs] = useState<StrategyLeg[]>([]);

  // Compare mode
  const [panelMode, setPanelMode] = useState<'build' | 'compare'>('build');
  const [compareStrategies, setCompareStrategies] = useState<Strategy[]>([]);
  const [activeCompareStratId, setActiveCompareStratId] = useState<string>('');

  // All legs (for pin strikes and legChains fetch)
  const allLegs = useMemo(() => {
    if (panelMode === 'compare') return compareStrategies.flatMap(s => s.legs);
    return strategyLegs;
  }, [panelMode, strategyLegs, compareStrategies]);

  // Active legs for chain highlighting
  const activeLegSet = useMemo(() => {
    if (panelMode === 'compare') {
      return compareStrategies.find(s => s.id === activeCompareStratId)?.legs ?? [];
    }
    return strategyLegs;
  }, [panelMode, strategyLegs, compareStrategies, activeCompareStratId]);

  const chainAbortController = useRef<AbortController | null>(null);
  const chartAbortController = useRef<AbortController | null>(null);
  const legChainsAbortController = useRef<AbortController | null>(null);

  const [legChains, setLegChains] = useState<Map<string, HistoricalChainRow[]>>(new Map());

  const currentSimTimestamp = useMemo(() => {
    if (!simulationDate || !simulationTime) return 0;
    return Math.floor(new Date(`${simulationDate}T${simulationTime}:00+05:30`).getTime() / 1000);
  }, [simulationDate, simulationTime]);

  const simTimestamp = useCallback(() => currentSimTimestamp, [currentSimTimestamp]);

  const generateExpiries = useCallback((simDate: string) => {
    if (!simDate || !dataRange) return [];

    const DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    const base = new Date(simDate + 'T00:00:00Z');
    const dateStr = (d: Date) => d.toISOString().split('T')[0];
    const addedDates = new Set<string>();
    const expList: {date: string, label: string}[] = [];

    const add = (d: Date, label: string) => {
      const s = dateStr(d);
      if (!addedDates.has(s)) {
        addedDates.add(s);
        expList.push({ date: s, label: `${label} (${s})` });
      }
    };

    const lastFridayOfMonth = (year: number, month: number): Date => {
      const d = new Date(Date.UTC(year, month + 1, 0));
      while (d.getUTCDay() !== 5) d.setUTCDate(d.getUTCDate() - 1);
      return d;
    };

    const thisWeekFriday = new Date(base);
    while (thisWeekFriday.getUTCDay() !== 5) thisWeekFriday.setUTCDate(thisWeekFriday.getUTCDate() + 1);

    const baseYear = base.getUTCFullYear(), baseMonth = base.getUTCMonth();
    let monthly = lastFridayOfMonth(baseYear, baseMonth);
    if (dateStr(monthly) <= dateStr(base)) monthly = lastFridayOfMonth(baseYear, baseMonth + 1);
    const nextMonthly = lastFridayOfMonth(monthly.getUTCFullYear(), monthly.getUTCMonth() + 1);

    const dailyLabel = (d: Date, fallback: string): string => {
      const s = dateStr(d);
      if (s === dateStr(monthly)) return 'Monthly';
      if (d.getUTCDay() === 5) return 'Weekly';
      return fallback;
    };

    const day0Label = dailyLabel(base, `Current ${DAYS[base.getUTCDay()]}`);
    add(base, day0Label);

    const d1 = new Date(base); d1.setUTCDate(base.getUTCDate() + 1);
    add(d1, dailyLabel(d1, `Next ${DAYS[d1.getUTCDay()]}`));

    const d2 = new Date(base); d2.setUTCDate(base.getUTCDate() + 2);
    add(d2, dailyLabel(d2, `Next-to-Next ${DAYS[d2.getUTCDay()]}`));

    add(thisWeekFriday, dateStr(thisWeekFriday) === dateStr(monthly) ? 'Monthly' : 'Weekly');

    const nextWeekly = new Date(thisWeekFriday); nextWeekly.setUTCDate(thisWeekFriday.getUTCDate() + 7);
    add(nextWeekly, dateStr(nextWeekly) === dateStr(monthly) ? 'Monthly' : 'Next Weekly');

    const ntnWeekly = new Date(nextWeekly); ntnWeekly.setUTCDate(nextWeekly.getUTCDate() + 7);
    add(ntnWeekly, dateStr(ntnWeekly) === dateStr(monthly) ? 'Monthly' : 'Next-to-Next Weekly');

    add(monthly, 'Monthly');
    add(nextMonthly, 'Next Monthly');

    expList.sort((a, b) => a.date.localeCompare(b.date));
    return expList;
  }, [dataRange]);

  useEffect(() => {
    historicalApi.getDataRange().then(range => {
      setDataRange(range);
      const defaultDate = new Date(range.max_ts * 1000).toISOString().split('T')[0];
      setSimulationDate(defaultDate);
      setSimulationTime('00:00');
    }).catch(console.error);
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

  useEffect(() => {
    if (chain.length > 0 && !strategyMode) {
      const atmRow = chain.find(r => r.is_atm);
      if (atmRow) setSelectedOption({ strike: atmRow.strike, type: 'CE' });
      setTimeout(() => {
        const atmEl = document.querySelector('.atm-row');
        if (atmEl) atmEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
    }
  }, [chain, strategyMode]);

  useEffect(() => {
    setSelectedOption(null);
    setChain([]);
  }, [selectedExpiry]);

  useEffect(() => {
    const isValidExpiry = expiries.some(e => e.date === selectedExpiry);
    if (!selectedExpiry || !simulationDate || !simulationTime || !isValidExpiry) return;

    const timer = setTimeout(() => {
      if (chainAbortController.current) chainAbortController.current.abort();
      chainAbortController.current = new AbortController();

      const timestamp = Math.floor(new Date(`${simulationDate}T${simulationTime}:00+05:30`).getTime() / 1000);
      const pinStrikes = allLegs.filter(l => l.expiry === selectedExpiry).map(l => l.strike);

      historicalApi.getOptionChain(selectedExpiry, timestamp, chainAbortController.current.signal, pinStrikes.length ? pinStrikes : undefined).then(res => {
        setChain(res.chain);
        setSpot((res as any).spot_actual || 0);
      }).catch(err => {
        if (err.name === 'AbortError') return;
        setChain([]);
        setSpot(0);
      });
    }, 300);

    return () => { clearTimeout(timer); if (chainAbortController.current) chainAbortController.current.abort(); };
  }, [selectedExpiry, simulationDate, simulationTime, expiries, allLegs]);

  useEffect(() => {
    if (!strategyMode || !simulationDate || !simulationTime || !allLegs.length) return;

    const otherExpiries = [...new Set(allLegs.map(l => l.expiry).filter(e => e !== selectedExpiry))];
    if (!otherExpiries.length) return;

    const timer = setTimeout(() => {
      if (legChainsAbortController.current) legChainsAbortController.current.abort();
      legChainsAbortController.current = new AbortController();
      const signal = legChainsAbortController.current.signal;
      const timestamp = Math.floor(new Date(`${simulationDate}T${simulationTime}:00+05:30`).getTime() / 1000);

      Promise.all(
        otherExpiries.map(expiry => {
          const pinStrikes = allLegs.filter(l => l.expiry === expiry).map(l => l.strike);
          return historicalApi.getOptionChain(expiry, timestamp, signal, pinStrikes)
            .then(res => ({ expiry, chain: res.chain }));
        })
      ).then(results => {
        if (signal.aborted) return;
        setLegChains(prev => {
          const next = new Map(prev);
          const activeExpiries = new Set(allLegs.map(l => l.expiry));
          for (const key of next.keys()) if (!activeExpiries.has(key)) next.delete(key);
          results.forEach(({ expiry, chain }) => next.set(expiry, chain));
          return next;
        });
      }).catch(err => { if (err.name !== 'AbortError') console.error('leg-chain fetch failed', err); });
    }, 300);

    return () => { clearTimeout(timer); if (legChainsAbortController.current) legChainsAbortController.current.abort(); };
  }, [simulationDate, simulationTime, allLegs, selectedExpiry, strategyMode]);

  useEffect(() => {
    if (selectedExpiry && selectedOption && simulationDate && !strategyMode) {
      if (chartAbortController.current) chartAbortController.current.abort();
      chartAbortController.current = new AbortController();

      historicalApi.getChartData(selectedExpiry, selectedOption.strike, selectedOption.type, 0, timeframe, chartAbortController.current.signal)
        .then(res => setChartData(res.data))
        .catch(err => { if (err.name !== 'AbortError') console.error('Chart data fetch failed', err); });
    }
    return () => { if (chartAbortController.current) chartAbortController.current.abort(); };
  }, [selectedExpiry, selectedOption, timeframe, strategyMode]);

  // ── Build mode callbacks ───────────────────────────────────────────────────
  const addLeg = useCallback((strike: number, type: 'CE' | 'PE', action: 'BUY' | 'SELL', premium: number) => {
    const ts = simTimestamp();
    const leg: StrategyLeg = {
      id: `${Date.now()}-${strike}-${type}-${action}`,
      expiry: selectedExpiry, strike, type, action, qty: 1,
      entryPremium: premium, entryTimestamp: ts,
    };
    if (panelMode === 'compare') {
      setCompareStrategies(prev => prev.map(s =>
        s.id !== activeCompareStratId ? s : { ...s, legs: [...s.legs, leg] }
      ));
    } else {
      setStrategyLegs(prev => [...prev, leg]);
    }
  }, [panelMode, activeCompareStratId, selectedExpiry, simTimestamp]);

  const removeLeg = useCallback((legId: string) => {
    setStrategyLegs(prev => prev.filter(l => l.id !== legId));
  }, []);

  const updateLegQty = useCallback((legId: string, qty: number) => {
    setStrategyLegs(prev => prev.map(l => l.id === legId ? { ...l, qty } : l));
  }, []);

  const clearLegs = useCallback(() => setStrategyLegs([]), []);

  // ── Compare mode callbacks ─────────────────────────────────────────────────
  const enterCompare = useCallback(() => {
    const s1Id = `cs1-${Date.now()}`;
    const s1: Strategy = { id: s1Id, label: 'Strategy 1', legs: [...strategyLegs] };
    setCompareStrategies([s1]);
    setActiveCompareStratId(s1Id);
    setPanelMode('compare');
  }, [strategyLegs]);

  const exitCompare = useCallback(() => {
    setPanelMode('build');
  }, []);

  const addCompareStrategy = useCallback(() => {
    const newId = `cs${Date.now()}`;
    setCompareStrategies(prev => {
      const n = prev.length + 1;
      return [...prev, { id: newId, label: `Strategy ${n}`, legs: [] }];
    });
    setActiveCompareStratId(newId);
  }, []);

  const removeCompareStrategy = useCallback((stratId: string) => {
    setCompareStrategies(prev => {
      if (prev.length <= 1) return prev;
      return prev.filter(s => s.id !== stratId);
    });
    setActiveCompareStratId(prev => {
      if (prev !== stratId) return prev;
      return compareStrategies.find(s => s.id !== stratId)?.id ?? '';
    });
  }, [compareStrategies]);

  const removeCompareLeg = useCallback((stratId: string, legId: string) => {
    setCompareStrategies(prev => prev.map(s =>
      s.id !== stratId ? s : { ...s, legs: s.legs.filter(l => l.id !== legId) }
    ));
  }, []);

  const updateCompareLegQty = useCallback((stratId: string, legId: string, qty: number) => {
    setCompareStrategies(prev => prev.map(s =>
      s.id !== stratId ? s : { ...s, legs: s.legs.map(l => l.id === legId ? { ...l, qty } : l) }
    ));
  }, []);

  const clearCompareLegs = useCallback((stratId: string) => {
    setCompareStrategies(prev => prev.map(s =>
      s.id !== stratId ? s : { ...s, legs: [] }
    ));
  }, []);

  // Leg count badge
  const legCount = panelMode === 'compare'
    ? compareStrategies.reduce((n, s) => n + s.legs.length, 0)
    : strategyLegs.length;

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
              activeLegSet.filter(l => l.expiry === selectedExpiry).forEach(l => {
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

        <div className="historical-chart-panel" style={{ width: strategyMode ? 'clamp(500px, 55vw, 900px)' : 'clamp(360px, 44vw, 640px)' }}>
          <div className="chart-mode-bar">
            <button
              className={`chart-mode-tab${!strategyMode ? ' active' : ''}`}
              onClick={() => setStrategyMode(false)}
            >
              Chart
            </button>
            <button
              className={`chart-mode-tab strategy${strategyMode ? ' active' : ''}`}
              onClick={() => setStrategyMode(true)}
            >
              Strategy Builder
              {legCount > 0 && <span className="strategy-leg-count">{legCount}</span>}
            </button>
          </div>

          {strategyMode ? (
            <StrategyPanel
              // Build mode
              legs={strategyLegs}
              onRemoveLeg={removeLeg}
              onUpdateQty={updateLegQty}
              onClearLegs={clearLegs}
              // Mode
              compareMode={panelMode === 'compare'}
              onEnterCompare={enterCompare}
              onExitCompare={exitCompare}
              // Compare mode
              compareStrategies={compareStrategies}
              activeCompareStratId={activeCompareStratId}
              onSetActiveCompareStrat={setActiveCompareStratId}
              onAddCompareStrategy={addCompareStrategy}
              onRemoveCompareStrategy={removeCompareStrategy}
              onRemoveCompareLeg={removeCompareLeg}
              onUpdateCompareLegQty={updateCompareLegQty}
              onClearCompareLegs={clearCompareLegs}
              // Shared
              chain={chain}
              legChains={legChains}
              spot={spot}
              selectedExpiry={selectedExpiry}
              simulationTimestamp={currentSimTimestamp}
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
