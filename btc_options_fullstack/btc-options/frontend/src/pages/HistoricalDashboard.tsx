import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { historicalApi } from '../services/historical_api';
import { usePersistedState, writePersisted, readPersisted, clearPersisted } from '../hooks/usePersistedState';
import { ReplayController } from '../components/historical/ReplayController';
import { HistoricalOptionChain } from '../components/historical/HistoricalOptionChain';
import { HistoricalChart } from '../components/historical/HistoricalChart';
import { StrategyPanel } from '../components/historical/StrategyPanel';
import { SpotChart } from '../components/historical/SpotChart';
import { IndicatorConfigPanel } from '../components/historical/IndicatorConfigPanel';
import { VolAnalyticsPanel } from '../components/historical/VolAnalyticsPanel';
import type { HistoricalChainRow, OHLCData, SpotOhlcBar, IndicatorConfig, IndicatorPoint, VolAnalyticsResponse, AtmIvPoint } from '../types/historical';
import type { Strategy, StrategyLeg } from '../types/strategy';

export const HistoricalDashboard: React.FC = () => {
  const [dataRange, setDataRange] = useState<{ min_ts: number, max_ts: number } | null>(null);
  // Persist date/time/expiry across mode switches & reloads.
  const [simulationDate, setSimulationDate] = usePersistedState<string>('historical:simulationDate', '');
  const [simulationTime, setSimulationTime] = usePersistedState<string>('historical:simulationTime', '');
  const [expiries, setExpiries] = useState<{date: string, label: string}[]>([]);
  const [selectedExpiry, setSelectedExpiry] = usePersistedState<string>('historical:selectedExpiry', '');

  const [chain, setChain] = useState<HistoricalChainRow[]>([]);
  const [spot, setSpot] = useState<number>(0);
  const [strikeFilter, setStrikeFilter] = useState<string>('');

  const [selectedOption, setSelectedOption] = useState<{strike: number, type: 'CE' | 'PE'} | null>(null);
  const [timeframe, setTimeframe] = useState<string>('5m');
  const [chartData, setChartData] = useState<OHLCData[]>([]);

  // Panel mode (persisted so MTM view comes back after navigating away)
  const [strategyMode, setStrategyMode] = usePersistedState<boolean>('historical:strategyMode', false);
  const [maximized, setMaximized] = useState(false);
  const [chartsOnly, setChartsOnly] = useState(false);

  // Horizontal panel resize
  const [chartPanelWidth, setChartPanelWidth] = useState<number | null>(null);
  const panelDragRef = useRef<{ startX: number; startW: number } | null>(null);
  const chartPanelRef = useRef<HTMLDivElement>(null);
  const onPanelDividerMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const currentW = chartPanelRef.current?.offsetWidth ?? chartPanelWidth ?? 500;
    panelDragRef.current = { startX: e.clientX, startW: currentW };
    const onMove = (ev: MouseEvent) => {
      if (!panelDragRef.current) return;
      const delta = panelDragRef.current.startX - ev.clientX;
      setChartPanelWidth(Math.max(320, panelDragRef.current.startW + delta));
    };
    const onUp = () => {
      panelDragRef.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [chartPanelWidth]);

  // Build mode — single strategy legs (persisted across mode switches & reloads)
  const [strategyLegs, setStrategyLegs] = usePersistedState<StrategyLeg[]>(
    'historical:strategyLegs', []
  );

  // Compare mode
  const [panelMode, setPanelMode] = usePersistedState<'build' | 'compare'>(
    'historical:panelMode', 'build'
  );
  const [compareStrategies, setCompareStrategies] = usePersistedState<Strategy[]>(
    'historical:compareStrategies', []
  );
  const [activeCompareStratId, setActiveCompareStratId] = usePersistedState<string>(
    'historical:activeCompareStratId', ''
  );

  // Saved-strategy snapshots (named, separate from auto-persistence)
  const HIST_SAVED_LIST = 'historical:savedStrategies';
  const [savedNames, setSavedNames] = useState<string[]>(
    () => readPersisted<string[]>(HIST_SAVED_LIST) ?? []
  );
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveName, setSaveName] = useState('');

  const saveCurrentStrategy = (name: string) => {
    const trimmed = name.trim(); if (!trimmed) return;
    writePersisted(`historical:strategy:${trimmed}`, {
      strategyLegs, panelMode, compareStrategies, activeCompareStratId,
    });
    const list = Array.from(new Set([...savedNames, trimmed])).sort();
    writePersisted(HIST_SAVED_LIST, list);
    setSavedNames(list);
    setShowSaveDialog(false); setSaveName('');
  };
  const loadSavedStrategy = (name: string) => {
    const s = readPersisted<any>(`historical:strategy:${name}`);
    if (!s) return;
    setStrategyLegs(s.strategyLegs ?? []);
    setPanelMode(s.panelMode ?? 'build');
    setCompareStrategies(s.compareStrategies ?? []);
    setActiveCompareStratId(s.activeCompareStratId ?? '');
  };
  const deleteSavedStrategy = (name: string) => {
    clearPersisted(`historical:strategy:${name}`);
    const list = savedNames.filter(n => n !== name);
    writePersisted(HIST_SAVED_LIST, list);
    setSavedNames(list);
  };

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

  // ── Vol Analytics panel state ─────────────────────────────────────────────
  const [volData, setVolData] = useState<VolAnalyticsResponse | null>(null);
  const [volLoading, setVolLoading] = useState(false);
  const [atmIvSeries, setAtmIvSeries] = useState<AtmIvPoint[]>([]);
  const [ivSeriesLoading, setIvSeriesLoading] = useState(false);
  // Panel-local timeframe for the lifetime IV/RV mini-chart (5m/15m/30m/1h/4h/1d).
  const [volPanelTimeframe, setVolPanelTimeframe] = usePersistedState<string>('historical:volPanelTimeframe', '1h');
  const [volPanelRvWindow, setVolPanelRvWindow] = usePersistedState<number>('historical:volPanelRvWindow', 14);
  const [volPanelRvEstimator, setVolPanelRvEstimator] = usePersistedState<string>('historical:volPanelRvEstimator', 'cc');
  // Expanded state is lifted here so the (expensive) vol fetches are skipped
  // entirely while the panel is collapsed — collapsed is the default, so by
  // default the panel adds zero backend load and never competes with the chain.
  const [volPanelExpanded, setVolPanelExpanded] = usePersistedState<boolean>('historical:volPanelExpanded', false);
  const volAbortController = useRef<AbortController | null>(null);
  const ivSeriesAbortController = useRef<AbortController | null>(null);

  const currentSimTimestamp = useMemo(() => {
    if (!simulationDate || !simulationTime) return 0;
    return Math.floor(new Date(`${simulationDate}T${simulationTime}:00+05:30`).getTime() / 1000);
  }, [simulationDate, simulationTime]);

  const simTimestamp = useCallback(() => currentSimTimestamp, [currentSimTimestamp]);

  // ── Spot/leg chart with technical indicators ──────────────────────────────
  // Reuses the existing `timeframe` state (the premium chart's timeframe) so
  // changing it on the premium chart also drives the spot chart.
  const [indicatorConfigs, setIndicatorConfigs] = usePersistedState<IndicatorConfig[]>('historical:indicators', []);
  const [chartSource, setChartSource] = usePersistedState<'spot' | 'leg'>('historical:chartSource', 'spot');
  const [spotOhlc, setSpotOhlc] = useState<SpotOhlcBar[]>([]);
  const [indicatorData, setIndicatorData] = useState<Record<string, IndicatorPoint[]>>({});
  const indicatorAbortRef = useRef<AbortController | null>(null);
  const spotChartRef = useRef<HTMLDivElement | null>(null);

  const scrollToSpot = useCallback(() => {
    spotChartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  // Window: full simulation day [00:00 IST, 23:59 IST] for the spot chart.
  const chartWindow = useMemo(() => {
    if (!simulationDate) return null;
    const start = Math.floor(new Date(simulationDate + 'T00:00:00+05:30').getTime() / 1000);
    const end   = start + 24 * 3600 - 60;
    return { start, end };
  }, [simulationDate]);

  // Fetch spot OHLC + indicators when window/configs/source change.
  useEffect(() => {
    if (!chartWindow) return;
    if (indicatorAbortRef.current) indicatorAbortRef.current.abort();
    indicatorAbortRef.current = new AbortController();
    const sig = indicatorAbortRef.current.signal;

    const useLeg = chartSource === 'leg' && selectedOption && selectedExpiry;

    const ohlcP = useLeg
      ? historicalApi.getLegOhlc(selectedExpiry!, selectedOption!.strike, selectedOption!.type,
                                  chartWindow.start, chartWindow.end, timeframe, sig)
      : historicalApi.getSpotOhlc(chartWindow.start, chartWindow.end, timeframe, sig);

    const indP = indicatorConfigs.length === 0
      ? Promise.resolve({ indicators: {} as Record<string, IndicatorPoint[]> })
      : (useLeg
          ? historicalApi.getLegIndicators(selectedExpiry!, selectedOption!.strike, selectedOption!.type,
              chartWindow.start, chartWindow.end, timeframe, indicatorConfigs, sig)
          : historicalApi.getSpotIndicators(chartWindow.start, chartWindow.end, timeframe, indicatorConfigs, sig));

    Promise.all([ohlcP, indP])
      .then(([ohlcRes, indRes]) => {
        setSpotOhlc(ohlcRes.data ?? []);
        setIndicatorData(indRes.indicators ?? {});
      })
      .catch(err => {
        if (err?.name !== 'AbortError') console.error('spot chart fetch failed', err);
      });

    return () => indicatorAbortRef.current?.abort();
  }, [chartWindow, chartSource, timeframe, indicatorConfigs, selectedExpiry, selectedOption]);

  const generateExpiries = useCallback((simDate: string, simTime: string) => {
    if (!simDate || !dataRange) return [];

    const DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    // After 17:30 IST (settlement), today's contracts have expired — advance base to next day
    const base = new Date(simDate + 'T00:00:00Z');
    if (simTime) {
      const [h, m] = simTime.split(':').map(Number);
      if (h * 60 + m >= 17 * 60 + 30) base.setUTCDate(base.getUTCDate() + 1);
    }
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
    // getDataRange gates the whole dashboard (no dataRange → no expiries → no
    // chain). On a cold/just-restarted single-worker backend this one-shot fetch
    // can be dropped (the Live-mode WS reconnect burst competes for the worker),
    // which used to leave the dashboard permanently empty. Retry with backoff so
    // a transient failure self-heals instead of wedging the UI.
    let cancelled = false;
    const load = (attempt: number) => {
      historicalApi.getDataRange().then(range => {
        if (cancelled) return;
        setDataRange(range);
        // Only seed defaults if no persisted value exists. Without this guard,
        // every mode switch / reload would clobber the user's last selection.
        if (!simulationDate) {
          setSimulationDate(new Date(range.max_ts * 1000).toISOString().split('T')[0]);
        }
        if (!simulationTime) setSimulationTime('00:00');
      }).catch(err => {
        if (cancelled) return;
        if (attempt < 6) {
          setTimeout(() => load(attempt + 1), Math.min(1000 * (attempt + 1), 4000));
        } else {
          console.error('getDataRange failed after retries', err);
        }
      });
    };
    load(0);
    return () => { cancelled = true; };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (simulationDate) {
      const newList = generateExpiries(simulationDate, simulationTime);
      setExpiries(newList);
      if (newList.length > 0 && !newList.find(e => e.date === selectedExpiry)) {
        setSelectedExpiry(newList[0].date);
      }
    } else {
      setExpiries([]);
    }
  }, [simulationDate, simulationTime, generateExpiries]);

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

  // ── Vol Analytics snapshot — recomputes on date/time/expiry change ────────
  // Only runs while the panel is EXPANDED — collapsed (default) does no work.
  useEffect(() => {
    const isValidExpiry = expiries.some(e => e.date === selectedExpiry);
    if (!volPanelExpanded || !selectedExpiry || !simulationDate || !simulationTime || !isValidExpiry) {
      setVolData(null);
      return;
    }
    const timer = setTimeout(() => {
      if (volAbortController.current) volAbortController.current.abort();
      volAbortController.current = new AbortController();
      const timestamp = Math.floor(new Date(`${simulationDate}T${simulationTime}:00+05:30`).getTime() / 1000);
      setVolLoading(true);
      historicalApi.getVolAnalytics(selectedExpiry, timestamp, volAbortController.current.signal)
        .then(res => { setVolData(res); })
        .catch(err => { if (err.name !== 'AbortError') setVolData(null); })
        .finally(() => setVolLoading(false));
    }, 300);
    return () => { clearTimeout(timer); if (volAbortController.current) volAbortController.current.abort(); };
  }, [selectedExpiry, simulationDate, simulationTime, expiries, volPanelExpanded]);

  // ── Lifetime ATM IV/RV series — refetches only on expiry / panel-timeframe ─
  // Also gated on expanded so the contract-life series isn't fetched when collapsed.
  useEffect(() => {
    if (!volPanelExpanded || !selectedExpiry) { setAtmIvSeries([]); return; }
    if (ivSeriesAbortController.current) ivSeriesAbortController.current.abort();
    ivSeriesAbortController.current = new AbortController();
    setIvSeriesLoading(true);
    historicalApi.getAtmIvSeries(selectedExpiry, volPanelTimeframe, volPanelRvWindow, ivSeriesAbortController.current.signal, volPanelRvEstimator)
      .then(res => { setAtmIvSeries(res.data || []); })
      .catch(err => { if (err.name !== 'AbortError') setAtmIvSeries([]); })
      .finally(() => setIvSeriesLoading(false));
    return () => { if (ivSeriesAbortController.current) ivSeriesAbortController.current.abort(); };
  }, [selectedExpiry, volPanelTimeframe, volPanelExpanded, volPanelRvWindow, volPanelRvEstimator]);

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
      entrySpot: spot,
    };
    if (panelMode === 'compare') {
      setCompareStrategies(prev => prev.map(s =>
        s.id !== activeCompareStratId ? s : { ...s, legs: [...s.legs, leg] }
      ));
    } else {
      setStrategyLegs(prev => [...prev, leg]);
    }
  }, [panelMode, activeCompareStratId, selectedExpiry, simTimestamp, spot]);

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
    <div
      className="historical-container"
      style={{ overflowY: 'auto', overflowX: 'hidden', position: 'relative' }}
    >
      {/* Floating "Scroll to Spot Chart" button */}
      {!maximized && simulationDate && (
        <button
          onClick={scrollToSpot}
          title="Scroll to spot chart"
          style={{
            position: 'fixed', right: 16, bottom: 16, zIndex: 20,
            background: '#1f6feb', color: '#fff', border: 'none',
            padding: '8px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600,
            cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
          }}
        >
          ↓ Spot Chart
        </button>
      )}
      {/* Save/Load strategy bar — minimal floating UI, top-right */}
      <div style={{
        position: 'absolute', top: 6, right: 12, zIndex: 5,
        display: 'flex', gap: 8, alignItems: 'center', fontSize: 11,
      }}>
        {savedNames.length > 0 && (
          <select
            defaultValue=""
            onChange={e => { if (e.target.value) { loadSavedStrategy(e.target.value); e.target.value = ''; } }}
            style={{
              background: '#0d1421', border: '1px solid #1a2d42',
              color: '#c9d1d9', borderRadius: 4, padding: '4px 8px',
              fontSize: 11, cursor: 'pointer',
            }}>
            <option value="">📂 Load saved…</option>
            {savedNames.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        )}
        {savedNames.length > 0 && (
          <select
            defaultValue=""
            onChange={e => {
              if (e.target.value && confirm(`Delete saved strategy "${e.target.value}"?`)) {
                deleteSavedStrategy(e.target.value);
              }
              e.target.value = '';
            }}
            style={{
              background: '#0d1421', border: '1px solid #1a2d42',
              color: '#ff4d6a', borderRadius: 4, padding: '4px 8px',
              fontSize: 11, cursor: 'pointer',
            }}>
            <option value="">🗑 Delete saved…</option>
            {savedNames.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        )}
        <button onClick={() => setShowSaveDialog(true)} style={{
          background: '#1f6feb', color: '#fff', border: 'none',
          borderRadius: 4, padding: '4px 12px', fontSize: 11,
          fontWeight: 600, cursor: 'pointer',
        }}>💾 Save Strategy</button>
      </div>

      {showSaveDialog && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
        }} onClick={() => setShowSaveDialog(false)}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#0d1421', border: '1px solid #1a2d42',
            borderRadius: 8, padding: 20, minWidth: 320,
          }}>
            <h3 style={{ margin: 0, marginBottom: 12, fontSize: 14, color: '#e6edf3' }}>Save Strategy</h3>
            <input autoFocus type="text" placeholder="e.g. ATM short straddle"
              value={saveName} onChange={e => setSaveName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveCurrentStrategy(saveName); }}
              style={{
                width: '100%', padding: '6px 10px', fontSize: 12,
                background: '#080e16', border: '1px solid #1a2d42',
                color: '#c9d1d9', borderRadius: 4, marginBottom: 12,
              }} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setShowSaveDialog(false)} style={{
                background: 'transparent', border: '1px solid #1a2d42',
                color: '#c9d1d9', borderRadius: 4, padding: '5px 14px',
                fontSize: 11, cursor: 'pointer',
              }}>Cancel</button>
              <button onClick={() => saveCurrentStrategy(saveName)} style={{
                background: '#1f6feb', border: 'none', color: '#fff',
                borderRadius: 4, padding: '5px 14px', fontSize: 11,
                fontWeight: 600, cursor: 'pointer',
              }}>Save</button>
            </div>
          </div>
        </div>
      )}

      {!chartsOnly && !maximized && <div className="replay-wrapper">
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
      </div>}

      <div
        className="historical-main"
        style={{ flexShrink: 0, height: 'calc(100vh - 120px)', minHeight: 480 }}
      >
        {!maximized && !chartsOnly && <div className="historical-chain-panel" style={{ flex: 1, minWidth: 0 }}>
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
        </div>}

        {!maximized && (
          <div className="panel-divider" onMouseDown={onPanelDividerMouseDown} />
        )}

        <div ref={chartPanelRef} className="historical-chart-panel" style={{ width: maximized ? '100%' : chartPanelWidth ? `${chartPanelWidth}px` : strategyMode ? 'clamp(500px, 55vw, 900px)' : 'clamp(360px, 44vw, 640px)', flexShrink: 0 }}>
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
              maximized={maximized}
              onToggleMaximize={() => setMaximized(m => !m)}
              chartsOnly={chartsOnly}
              onToggleChartsOnly={() => setChartsOnly(c => !c)}
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

      {/* Vol Analytics — placed ABOVE the spot chart for prominence; visible in
          both Chart & Strategy modes */}
      {!maximized && simulationDate && (
        <VolAnalyticsPanel
          volData={volData}
          volLoading={volLoading}
          ivSeries={atmIvSeries}
          ivLoading={ivSeriesLoading}
          nowTs={currentSimTimestamp}
          panelTimeframe={volPanelTimeframe}
          setPanelTimeframe={setVolPanelTimeframe}
          rvWindow={volPanelRvWindow}
          setRvWindow={setVolPanelRvWindow}
          rvEstimator={volPanelRvEstimator}
          setRvEstimator={setVolPanelRvEstimator}
          expanded={volPanelExpanded}
          setExpanded={setVolPanelExpanded}
        />
      )}

      {/* Spot/leg chart with technical indicators */}
      {!maximized && simulationDate && (
        <div ref={spotChartRef} style={{ padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <IndicatorConfigPanel
            configs={indicatorConfigs}
            setConfigs={setIndicatorConfigs}
            source={chartSource}
            setSource={setChartSource}
            vwapAvailable={chartSource === 'spot'}
          />
          <SpotChart
            ohlc={spotOhlc}
            configs={indicatorConfigs}
            indicators={indicatorData}
            height={420}
            premiumMode={chartSource === 'leg'}
          />
        </div>
      )}
    </div>
  );
};
