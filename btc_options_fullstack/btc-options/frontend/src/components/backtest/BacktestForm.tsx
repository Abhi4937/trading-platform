import React, { useEffect, useState } from 'react';
import { usePersistedState, writePersisted, readPersisted, clearPersisted } from '../../hooks/usePersistedState';
import type {
  BacktestRequest, BacktestLegTemplate,
  AlgoStrategyType, AlgoUnderlyingFrom, AlgoSquareOff,
  AlgoPosition, AlgoOptionType, AlgoExpiryType,
  AlgoStrikeCriteria, AlgoStrikeType,
  AlgoTrailingOption, AlgoOverallSlType, AlgoOverallTgtType,
  AlgoReentryType, AlgoOverallMomentum,
} from '../../types/backtest';
import {
  ALGO_STRIKE_OPTIONS, ALGO_STRIKE_CRITERIA, ALGO_EXPIRY_OPTS,
  ALGO_TRAILING_OPTS, ALGO_OVERALL_SL_OPTS, ALGO_OVERALL_TGT_OPTS,
  ALGO_REENTRY_OPTS, ALGO_OVERALL_MOMENTUM_OPTS,
  expiryTypeToSelector,
} from '../../types/backtest';

const SAVED_LIST_KEY = 'backtest:savedStrategies';
const K = (name: string) => `backtest:${name}`;

interface Props {
  busy: boolean;
  onSubmit: (req: BacktestRequest) => void;
}

interface AlgoLeg {
  id: string;
  totalLot: number;
  position: AlgoPosition;
  optionType: AlgoOptionType;
  expiry: AlgoExpiryType;
  strikeCriteria: AlgoStrikeCriteria;
  strikeType: AlgoStrikeType;          // when criteria = Strike Type
  premiumTarget: number;               // when criteria = Closest Premium
  deltaTarget: number;                 // when criteria = Closest Delta
}

const newLeg = (overrides: Partial<AlgoLeg> = {}): AlgoLeg => ({
  id: Math.random().toString(36).slice(2, 9),
  totalLot: 1,
  position: 'Sell',
  optionType: 'Call',
  expiry: 'Weekly',
  strikeCriteria: 'Strike Type',
  strikeType: 'ATM',
  premiumTarget: 100,
  deltaTarget: 0.30,
  ...overrides,
});

// Strike criteria currently runnable on backend (Phase 2.5)
const ENABLED_CRITERIA: AlgoStrikeCriteria[] = [
  'Strike Type', 'Closest Premium', 'Closest Delta',
];

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000/api/v1';

export const BacktestForm: React.FC<Props> = ({ busy, onSubmit }) => {
  // ── Top cards state (persisted across mode switches & reloads) ──────────────
  const [underlying,    setUnderlying]    = usePersistedState<AlgoUnderlyingFrom>(K('underlying'), 'Futures');
  const [strategyType,  setStrategyType]  = usePersistedState<AlgoStrategyType>(K('strategyType'), 'Intraday');
  const [entryTime,     setEntryTime]     = usePersistedState<string>(K('entryTime'), '09:20');
  const [exitTime,      setExitTime]      = usePersistedState<string>(K('exitTime'), '15:25');
  const [weekdayMask,   setWeekdayMask]   = usePersistedState<number[]>(K('weekdayMask'), [0, 1, 2, 3, 4, 5, 6]);
  const [exitDayOffset, setExitDayOffset] = usePersistedState<number>(K('exitDayOffset'), 0);
  const [noReentryAfter,     setNoReentryAfter]     = usePersistedState<boolean>(K('noReentryAfter'), false);
  const [noReentryAfterTime, setNoReentryAfterTime] = usePersistedState<string>(K('noReentryAfterTime'), '14:30');
  const [overallMomentum, setOverallMomentum] = usePersistedState<boolean>(K('overallMomentum'), false);
  const [momentumType,   setMomentumType]    = usePersistedState<AlgoOverallMomentum>(K('momentumType'), 'Points (Pts) ↑');
  const [momentumValue,  setMomentumValue]   = usePersistedState<number>(K('momentumValue'), 0);
  const [squareOff,        setSquareOff]        = usePersistedState<AlgoSquareOff>(K('squareOff'), 'Partial');
  const [trailToBreakeven, setTrailToBreakeven] = usePersistedState<boolean>(K('trailToBreakeven'), false);

  const [legs, setLegs] = usePersistedState<AlgoLeg[]>(K('legs'), [
    newLeg({ position: 'Sell', optionType: 'Call' }),
    newLeg({ position: 'Sell', optionType: 'Put'  }),
  ]);

  const [overallSl,        setOverallSl]        = usePersistedState<boolean>(K('overallSl'), false);
  const [overallSlType,    setOverallSlType]    = usePersistedState<AlgoOverallSlType>(K('overallSlType'), 'Max Loss');
  const [overallSlValue,   setOverallSlValue]   = usePersistedState<number>(K('overallSlValue'), 0);
  const [reentrySlEnabled, setReentrySlEnabled] = usePersistedState<boolean>(K('reentrySlEnabled'), false);
  const [reentrySlType,    setReentrySlType]    = usePersistedState<AlgoReentryType>(K('reentrySlType'), 'RE ASAP');
  const [reentrySlCount,   setReentrySlCount]   = usePersistedState<number>(K('reentrySlCount'), 1);

  const [overallTgt,        setOverallTgt]        = usePersistedState<boolean>(K('overallTgt'), false);
  const [overallTgtType,    setOverallTgtType]    = usePersistedState<AlgoOverallTgtType>(K('overallTgtType'), 'Max Profit');
  const [overallTgtValue,   setOverallTgtValue]   = usePersistedState<number>(K('overallTgtValue'), 0);
  const [reentryTgtEnabled, setReentryTgtEnabled] = usePersistedState<boolean>(K('reentryTgtEnabled'), false);
  const [reentryTgtType,    setReentryTgtType]    = usePersistedState<AlgoReentryType>(K('reentryTgtType'), 'RE ASAP');
  const [reentryTgtCount,   setReentryTgtCount]   = usePersistedState<number>(K('reentryTgtCount'), 1);

  const [trailingEnabled, setTrailingEnabled] = usePersistedState<boolean>(K('trailingEnabled'), false);
  const [trailingType,    setTrailingType]    = usePersistedState<AlgoTrailingOption>(K('trailingType'), 'Lock');
  const [profitReaches,   setProfitReaches]   = usePersistedState<number>(K('profitReaches'), 0);
  const [lockProfit,      setLockProfit]      = usePersistedState<number>(K('lockProfit'), 0);

  // Dates: default to last 60 days of *actual* available data, not last 60 days from today
  const [startDate, setStartDate] = usePersistedState<string>(K('startDate'), '');
  const [endDate,   setEndDate]   = usePersistedState<string>(K('endDate'),   '');
  const [dataLatest, setDataLatest] = useState<string | null>(null);

  // Saved-strategy menu
  const [savedNames, setSavedNames] = useState<string[]>(
    () => readPersisted<string[]>(SAVED_LIST_KEY) ?? []
  );
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveName, setSaveName] = useState('');

  useEffect(() => {
    fetch(`${API_BASE}/historical/data-range`).then(r => r.json()).then((r: any) => {
      const maxTs = r?.max_ts;
      if (!maxTs) return;
      const end = new Date(maxTs * 1000);
      const start = new Date(end.getTime() - 60 * 24 * 3600 * 1000);
      const fmt = (d: Date) => d.toISOString().slice(0, 10);
      setDataLatest(fmt(end));
      // Only seed defaults if user hasn't already chosen / had persisted dates
      if (!startDate) setStartDate(fmt(start));
      if (!endDate)   setEndDate(fmt(end));
    }).catch(() => { /* leave blank */ });
    // intentionally only on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Saved strategy snapshots ──────────────────────────────────────────────
  const snapshot = () => ({
    underlying, strategyType, entryTime, exitTime, weekdayMask, exitDayOffset,
    noReentryAfter, noReentryAfterTime, overallMomentum, momentumType, momentumValue,
    squareOff, trailToBreakeven,
    legs,
    overallSl, overallSlType, overallSlValue,
    reentrySlEnabled, reentrySlType, reentrySlCount,
    overallTgt, overallTgtType, overallTgtValue,
    reentryTgtEnabled, reentryTgtType, reentryTgtCount,
    trailingEnabled, trailingType, profitReaches, lockProfit,
    startDate, endDate,
  });

  const restoreSnapshot = (s: any) => {
    if (!s) return;
    setUnderlying(s.underlying); setStrategyType(s.strategyType);
    setEntryTime(s.entryTime); setExitTime(s.exitTime);
    setWeekdayMask(s.weekdayMask); setExitDayOffset(s.exitDayOffset);
    setNoReentryAfter(s.noReentryAfter); setNoReentryAfterTime(s.noReentryAfterTime);
    setOverallMomentum(s.overallMomentum); setMomentumType(s.momentumType); setMomentumValue(s.momentumValue);
    setSquareOff(s.squareOff); setTrailToBreakeven(s.trailToBreakeven);
    setLegs(s.legs);
    setOverallSl(s.overallSl); setOverallSlType(s.overallSlType); setOverallSlValue(s.overallSlValue);
    setReentrySlEnabled(s.reentrySlEnabled); setReentrySlType(s.reentrySlType); setReentrySlCount(s.reentrySlCount);
    setOverallTgt(s.overallTgt); setOverallTgtType(s.overallTgtType); setOverallTgtValue(s.overallTgtValue);
    setReentryTgtEnabled(s.reentryTgtEnabled); setReentryTgtType(s.reentryTgtType); setReentryTgtCount(s.reentryTgtCount);
    setTrailingEnabled(s.trailingEnabled); setTrailingType(s.trailingType);
    setProfitReaches(s.profitReaches); setLockProfit(s.lockProfit);
    setStartDate(s.startDate); setEndDate(s.endDate);
  };

  const saveStrategy = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    writePersisted(K(`strategy:${trimmed}`), snapshot());
    const list = Array.from(new Set([...savedNames, trimmed])).sort();
    writePersisted(SAVED_LIST_KEY, list);
    setSavedNames(list);
    setShowSaveDialog(false);
    setSaveName('');
  };
  const loadStrategy = (name: string) => {
    const s = readPersisted<any>(K(`strategy:${name}`));
    if (s) restoreSnapshot(s);
  };
  const deleteStrategy = (name: string) => {
    clearPersisted(K(`strategy:${name}`));
    const list = savedNames.filter(n => n !== name);
    writePersisted(SAVED_LIST_KEY, list);
    setSavedNames(list);
  };

  // ── Leg actions ─────────────────────────────────────────────────────────────
  const updateLeg = (id: string, patch: Partial<AlgoLeg>) =>
    setLegs(legs.map(l => l.id === id ? { ...l, ...patch } : l));
  const removeLeg = (id: string) => setLegs(legs.filter(l => l.id !== id));
  const dupLeg    = (id: string) => {
    const src = legs.find(l => l.id === id);
    if (!src) return;
    setLegs([...legs, { ...src, id: Math.random().toString(36).slice(2, 9) }]);
  };
  const addLeg    = () => setLegs([...legs, newLeg()]);

  // ── Submit ──────────────────────────────────────────────────────────────────
  const handleSubmit = () => {
    if (!startDate || !endDate || legs.length === 0) return;

    const backendLegs: BacktestLegTemplate[] = legs.map(l => {
      const base: any = {
        type: l.optionType === 'Call' ? 'CE' : 'PE',
        action: l.position === 'Buy' ? 'BUY' : 'SELL',
        qty: Math.max(1, l.totalLot),
        expiry_selector: expiryTypeToSelector(l.expiry),
        strike_offset: 0,
      };
      switch (l.strikeCriteria) {
        case 'Strike Type':
          base.strike_criteria = 'strike_type';
          base.strike_level = l.strikeType;
          break;
        case 'Closest Premium':
          base.strike_criteria = 'closest_premium';
          base.strike_value = l.premiumTarget;
          break;
        case 'Closest Delta':
          base.strike_criteria = 'closest_delta';
          base.strike_value = l.deltaTarget;
          break;
        default:
          base.strike_criteria = 'strike_type';
          base.strike_level = 'ATM';
      }
      return base;
    });

    onSubmit({
      start_date: startDate,
      end_date:   endDate,
      legs:       backendLegs,
      entry_time_ist:        entryTime,
      forced_exit_time_ist:  exitTime,
      weekday_mask:    weekdayMask,
      exit_day_offset: exitDayOffset,
      timeframe:       '5m',
      slippage:  { enabled: true, mode: 'smart', mult: 1.0 },
      brokerage: { enabled: true, rate: 'offer', referral: false },
      stop_loss: overallSl  ? { type: overallSlType,  value: overallSlValue }   : null,
      target:    overallTgt ? { type: overallTgtType, value: overallTgtValue }  : null,
      trail_sl:  trailingEnabled
                  ? { mode: trailingType, profit_reaches: profitReaches, lock_profit: lockProfit }
                  : null,
      reentry_after_sl: reentrySlEnabled ? reentrySlCount : 0,
    });
  };

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={S.page}>
      <div style={S.headerBar}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h1 style={S.pageTitle}>Backtest</h1>
          <span style={S.tabBadge}>BTCUSD · Delta Exchange</span>
        </div>
        <div style={{ display: 'flex', gap: 12, fontSize: 12, alignItems: 'center' }}>
          {savedNames.length > 0 && (
            <select
              defaultValue=""
              onChange={e => { if (e.target.value) { loadStrategy(e.target.value); e.target.value = ''; } }}
              style={{
                background: '#080e16', border: '1px solid #1a2d42',
                color: '#c9d1d9', borderRadius: 4, padding: '5px 8px',
                fontSize: 11, fontFamily: 'inherit', cursor: 'pointer',
              }}
            >
              <option value="">Load saved…</option>
              {savedNames.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          )}
          {savedNames.length > 0 && (
            <select
              defaultValue=""
              onChange={e => {
                if (e.target.value && confirm(`Delete saved strategy "${e.target.value}"?`)) {
                  deleteStrategy(e.target.value);
                }
                e.target.value = '';
              }}
              style={{
                background: '#080e16', border: '1px solid #1a2d42',
                color: '#ff4d6a', borderRadius: 4, padding: '5px 8px',
                fontSize: 11, fontFamily: 'inherit', cursor: 'pointer',
              }}
            >
              <option value="">Delete saved…</option>
              {savedNames.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          )}
          <button onClick={() => setShowSaveDialog(true)}
            style={{
              background: '#1f6feb', color: '#fff', border: 'none',
              borderRadius: 4, padding: '5px 12px', fontSize: 11,
              fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
            }}>
            💾 Save Strategy
          </button>
          <div style={{ borderLeft: '1px solid #1a2d42', paddingLeft: 12 }}>
            <div style={S.statLabel}>Legs</div>
            <div style={S.statVal}>{legs.length}</div>
          </div>
        </div>
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
            <input
              autoFocus
              type="text" placeholder="e.g. Friday Short Strangle"
              value={saveName}
              onChange={e => setSaveName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveStrategy(saveName); }}
              style={{
                width: '100%', padding: '6px 10px', fontSize: 12,
                background: '#080e16', border: '1px solid #1a2d42',
                color: '#c9d1d9', borderRadius: 4, fontFamily: 'inherit',
                marginBottom: 12,
              }}
            />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setShowSaveDialog(false)} style={{
                background: 'transparent', border: '1px solid #1a2d42',
                color: '#c9d1d9', borderRadius: 4, padding: '5px 14px',
                fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
              }}>Cancel</button>
              <button onClick={() => saveStrategy(saveName)} style={{
                background: '#1f6feb', border: 'none', color: '#fff',
                borderRadius: 4, padding: '5px 14px', fontSize: 11,
                fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
              }}>Save</button>
            </div>
          </div>
        </div>
      )}

      <div style={{ padding: 16 }}>
        <div style={S.topGrid}>
          <SectionCard title="Instrument settings">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <FieldRow label="Index">
                <Select value="BTCUSD" options={['BTCUSD']} disabled />
              </FieldRow>
              <FieldRow label="Underlying from">
                <RadioGroup
                  options={['Cash', 'Futures']}
                  value={underlying}
                  onChange={v => setUnderlying(v as AlgoUnderlyingFrom)}
                />
              </FieldRow>
            </div>
          </SectionCard>

          <SectionCard title="Entry settings" style={{ gridColumn: 'span 2' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <FieldRow label="Strategy Type">
                <RadioGroup
                  options={['Intraday', 'BTST', 'Positional']}
                  value={strategyType}
                  onChange={v => setStrategyType(v as AlgoStrategyType)}
                />
              </FieldRow>
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <InlineField label="Entry Time">
                  <input type="time" value={entryTime} onChange={e => setEntryTime(e.target.value)} style={S.input} />
                </InlineField>
                <InlineField label="Exit Time">
                  <input type="time" value={exitTime} onChange={e => setExitTime(e.target.value)} style={S.input} />
                </InlineField>
                <InlineField label="Exit +N days">
                  <Stepper value={exitDayOffset} min={0} max={30} step={1}
                    onChange={setExitDayOffset} width={50} />
                  <span style={{ color: '#7a9bb5', fontSize: 10 }}>
                    {exitDayOffset === 0 ? '(intraday)' : exitDayOffset === 1 ? '(next day)' : `(+${exitDayOffset}d)`}
                  </span>
                </InlineField>
              </div>
              <FieldRow label="Days of Week (entries)">
                <div style={{ display: 'flex', gap: 4 }}>
                  {[
                    { v: 0, label: 'Mon' }, { v: 1, label: 'Tue' }, { v: 2, label: 'Wed' },
                    { v: 3, label: 'Thu' }, { v: 4, label: 'Fri' },
                    { v: 5, label: 'Sat' }, { v: 6, label: 'Sun' },
                  ].map(d => {
                    const on = weekdayMask.includes(d.v);
                    return (
                      <button key={d.v} type="button"
                        onClick={() => setWeekdayMask(
                          on ? weekdayMask.filter(x => x !== d.v)
                             : [...weekdayMask, d.v].sort()
                        )}
                        style={{
                          padding: '4px 10px', fontSize: 11, fontWeight: 600,
                          borderRadius: 14, cursor: 'pointer',
                          border: '1px solid ' + (on ? '#1f6feb' : '#1a2d42'),
                          background:                on ? '#1f6feb' : '#0d1421',
                          color:                     on ? '#fff'    : '#7a9bb5',
                          fontFamily: 'inherit',
                        }}>
                        {d.label}
                      </button>
                    );
                  })}
                </div>
              </FieldRow>
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <Toggle label="No re-entry after" value={noReentryAfter} onChange={setNoReentryAfter} />
                  <input type="time" value={noReentryAfterTime}
                    onChange={e => setNoReentryAfterTime(e.target.value)} disabled={!noReentryAfter}
                    style={{ ...S.input, opacity: noReentryAfter ? 1 : 0.4 }} />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <Toggle label="Overall Momentum" value={overallMomentum} onChange={setOverallMomentum} />
                  <div style={{ display: 'flex', gap: 6, opacity: overallMomentum ? 1 : 0.4 }}>
                    <Select value={momentumType} options={[...ALGO_OVERALL_MOMENTUM_OPTS]}
                      onChange={v => setMomentumType(v as AlgoOverallMomentum)}
                      disabled={!overallMomentum} blueAccent />
                    <Stepper value={momentumValue} min={0} step={1}
                      onChange={setMomentumValue} disabled={!overallMomentum} width={70} />
                  </div>
                </div>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Legwise settings">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <FieldRow label="Square Off">
                <RadioGroup options={['Partial', 'Complete']}
                  value={squareOff} onChange={v => setSquareOff(v as AlgoSquareOff)} />
              </FieldRow>
              <label style={S.checkboxLabel}>
                <input type="checkbox" checked={trailToBreakeven}
                  onChange={e => setTrailToBreakeven(e.target.checked)} />
                Trail SL to Break-even price
              </label>
            </div>
          </SectionCard>
        </div>

        <div style={{ marginTop: 18 }}>
          <div style={S.sectionHeader}>
            <h6 style={S.sectionTitle}>Leg Builder</h6>
            <span style={{ color: '#7a9bb5', fontSize: 11 }}>
              Strike Type · Closest Premium · Closest Delta wired · others Phase 3
            </span>
          </div>

          <div style={{ ...S.card, padding: 16 }}>
            {legs.map((leg, i) => (
              <div key={leg.id} style={{
                ...(i > 0 ? { borderTop: '1px solid #1a2d42', marginTop: 14, paddingTop: 14 } : {}),
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ fontSize: 11, color: '#7a9bb5', fontWeight: 600 }}>Leg #{i + 1}</span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <IconBtn title="Duplicate" onClick={() => dupLeg(leg.id)}>📋</IconBtn>
                    <IconBtn title="Delete" onClick={() => removeLeg(leg.id)} danger>×</IconBtn>
                  </div>
                </div>

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, alignItems: 'flex-start' }}>
                  <Stack label="Total Lot">
                    <Stepper value={leg.totalLot} min={1} step={1}
                      onChange={n => updateLeg(leg.id, { totalLot: Math.max(1, n) })}
                      width={64} />
                  </Stack>
                  <Stack label="Position">
                    <RadioGroup options={['Buy', 'Sell']}
                      value={leg.position} onChange={v => updateLeg(leg.id, { position: v as AlgoPosition })} />
                  </Stack>
                  <Stack label="Option Type">
                    <RadioGroup options={['Call', 'Put']}
                      value={leg.optionType} onChange={v => updateLeg(leg.id, { optionType: v as AlgoOptionType })} />
                  </Stack>
                  <Stack label="Expiry">
                    <Select value={leg.expiry} options={[...ALGO_EXPIRY_OPTS]}
                      onChange={v => updateLeg(leg.id, { expiry: v as AlgoExpiryType })} />
                  </Stack>
                  <Stack label="Strike Criteria">
                    <Select
                      value={leg.strikeCriteria}
                      options={[...ALGO_STRIKE_CRITERIA]}
                      onChange={v => updateLeg(leg.id, { strikeCriteria: v as AlgoStrikeCriteria })}
                      disabledOptions={ALGO_STRIKE_CRITERIA.filter(c => !ENABLED_CRITERIA.includes(c))}
                    />
                  </Stack>
                  {/* Morphing second input */}
                  {leg.strikeCriteria === 'Strike Type' && (
                    <Stack label="Strike Type">
                      <Select value={leg.strikeType} options={ALGO_STRIKE_OPTIONS}
                        onChange={v => updateLeg(leg.id, { strikeType: v })} />
                    </Stack>
                  )}
                  {leg.strikeCriteria === 'Closest Premium' && (
                    <Stack label="Premium ($)">
                      <Stepper value={leg.premiumTarget} min={1} step={10}
                        onChange={n => updateLeg(leg.id, { premiumTarget: Math.max(1, n) })}
                        width={80} />
                    </Stack>
                  )}
                  {leg.strikeCriteria === 'Closest Delta' && (
                    <Stack label="Delta">
                      <Stepper value={leg.deltaTarget} min={0.01} max={0.99} step={0.05}
                        decimals={2}
                        onChange={n => updateLeg(leg.id, { deltaTarget: n })}
                        width={70} />
                    </Stack>
                  )}
                  {!ENABLED_CRITERIA.includes(leg.strikeCriteria) && (
                    <Stack label="Value">
                      <span style={{ fontSize: 11, color: '#7a9bb5', fontStyle: 'italic' }}>
                        Phase 3 — switch criteria to use today
                      </span>
                    </Stack>
                  )}
                </div>
              </div>
            ))}

            <div style={{ marginTop: 18, display: 'flex', justifyContent: 'center' }}>
              <button onClick={addLeg} style={S.addBtn}>+ Add Leg</button>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 20 }}>
          <h6 style={S.sectionTitle}>Overall strategy settings</h6>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8 }}>
            <div style={{ ...S.card, padding: 18, minWidth: 240, flex: 1 }}>
              <Toggle label="Overall Stop Loss" value={overallSl} onChange={setOverallSl} />
              <div style={{ marginTop: 10, display: 'flex', gap: 8, opacity: overallSl ? 1 : 0.4 }}>
                <Select value={overallSlType} options={[...ALGO_OVERALL_SL_OPTS]}
                  onChange={v => setOverallSlType(v as AlgoOverallSlType)} disabled={!overallSl} blueAccent />
                <Stepper value={overallSlValue} min={0} step={1}
                  onChange={setOverallSlValue} disabled={!overallSl} width={80} />
              </div>
              <div style={{ marginTop: 14 }}>
                <Toggle label="Overall Re-entry on SL" value={reentrySlEnabled} onChange={setReentrySlEnabled} />
                <div style={{ marginTop: 6, display: 'flex', gap: 6, opacity: reentrySlEnabled ? 1 : 0.4 }}>
                  <Select value={reentrySlType} options={[...ALGO_REENTRY_OPTS]}
                    onChange={v => setReentrySlType(v as AlgoReentryType)}
                    disabled={!reentrySlEnabled} blueAccent />
                  <Stepper value={reentrySlCount} min={1} step={1}
                    onChange={setReentrySlCount} disabled={!reentrySlEnabled} width={56} />
                </div>
              </div>
            </div>

            <div style={{ ...S.card, padding: 18, minWidth: 240, flex: 1 }}>
              <Toggle label="Overall Target" value={overallTgt} onChange={setOverallTgt} />
              <div style={{ marginTop: 10, display: 'flex', gap: 8, opacity: overallTgt ? 1 : 0.4 }}>
                <Select value={overallTgtType} options={[...ALGO_OVERALL_TGT_OPTS]}
                  onChange={v => setOverallTgtType(v as AlgoOverallTgtType)} disabled={!overallTgt} blueAccent />
                <Stepper value={overallTgtValue} min={0} step={1}
                  onChange={setOverallTgtValue} disabled={!overallTgt} width={80} />
              </div>
              <div style={{ marginTop: 14 }}>
                <Toggle label="Overall Re-entry on Tgt" value={reentryTgtEnabled} onChange={setReentryTgtEnabled} />
                <div style={{ marginTop: 6, display: 'flex', gap: 6, opacity: reentryTgtEnabled ? 1 : 0.4 }}>
                  <Select value={reentryTgtType} options={[...ALGO_REENTRY_OPTS]}
                    onChange={v => setReentryTgtType(v as AlgoReentryType)}
                    disabled={!reentryTgtEnabled} blueAccent />
                  <Stepper value={reentryTgtCount} min={1} step={1}
                    onChange={setReentryTgtCount} disabled={!reentryTgtEnabled} width={56} />
                </div>
              </div>
            </div>

            <div style={{ ...S.card, padding: 18, flex: 2, minWidth: 320 }}>
              <Toggle label="Trailing Options" value={trailingEnabled} onChange={setTrailingEnabled} />
              <div style={{ marginTop: 10, opacity: trailingEnabled ? 1 : 0.4 }}>
                <Select value={trailingType} options={[...ALGO_TRAILING_OPTS]}
                  onChange={v => setTrailingType(v as AlgoTrailingOption)}
                  disabled={!trailingEnabled} blueAccent />
              </div>
              <div style={{
                marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr',
                gap: 8, alignItems: 'center', opacity: trailingEnabled ? 1 : 0.4,
              }}>
                <label style={S.subLabel}>If profit reaches</label>
                <Stepper value={profitReaches} min={0} step={10}
                  onChange={setProfitReaches} disabled={!trailingEnabled} width={80} />
                <label style={{ ...S.subLabel, textAlign: 'center' }}>Lock profit</label>
                <Stepper value={lockProfit} min={0} step={10}
                  onChange={setLockProfit} disabled={!trailingEnabled} width={80} />
              </div>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 20 }}>
          <div style={{
            ...S.card, padding: '14px 18px',
            display: 'flex', alignItems: 'center',
            justifyContent: 'space-between', flexWrap: 'wrap', gap: 14,
          }}>
            <span style={{ fontSize: 13, color: '#c9d1d9' }}>Enter the duration of your backtest</span>
            <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
              <InlineField label="Start Date">
                <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} style={S.dateInput} />
              </InlineField>
              <InlineField label="End Date">
                <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} style={S.dateInput} />
              </InlineField>
            </div>
          </div>
          {dataLatest && (
            <div style={{
              marginTop: 8, padding: '6px 12px',
              background: '#1a2920', border: '1px solid #2a4a36',
              borderRadius: 6,
              color: '#7ad9b5', fontSize: 11, textAlign: 'right',
            }}>
              ⓘ Latest backtest data is available for {dataLatest}
            </div>
          )}
        </div>

        <div style={{ height: 70 }} />
      </div>

      <div style={S.stickyFooter}>
        <button onClick={handleSubmit} disabled={busy}
          style={{ ...S.runBtn, opacity: busy ? 0.6 : 1, cursor: busy ? 'not-allowed' : 'pointer' }}>
          ▶ {busy ? 'Running…' : 'Start Backtest'}
          <span style={{ fontSize: 9, opacity: 0.8, marginLeft: 4 }}>(Shift+Enter)</span>
        </button>
      </div>
    </div>
  );
};

// ── Layout primitives ────────────────────────────────────────────────────────

const SectionCard: React.FC<{
  title: string; children: React.ReactNode; style?: React.CSSProperties;
}> = ({ title, children, style }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, ...style }}>
    <h6 style={S.sectionTitle}>{title}</h6>
    <div style={{ ...S.card, padding: 18 }}>{children}</div>
  </div>
);

const FieldRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    gap: 14, flexWrap: 'wrap',
  }}>
    <label style={{ fontSize: 11, color: '#c9d1d9' }}>{label}</label>
    {children}
  </div>
);

const InlineField: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
    <label style={{ fontSize: 11, color: '#c9d1d9' }}>{label}</label>
    {children}
  </div>
);

const Stack: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
    <label style={{ fontSize: 11, color: '#c9d1d9' }}>{label}</label>
    {children}
  </div>
);

// ── Inputs ───────────────────────────────────────────────────────────────────

const Toggle: React.FC<{ label: string; value: boolean; onChange: (v: boolean) => void }> =
  ({ label, value, onChange }) => (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, cursor: 'pointer', color: '#c9d1d9' }}>
      <span>{label}</span>
      <button type="button" onClick={() => onChange(!value)}
        style={{
          width: 36, height: 20, borderRadius: 10,
          background: value ? '#1f6feb' : '#243a52',
          position: 'relative', border: 'none', cursor: 'pointer',
          transition: 'background 0.2s',
        }}>
        <span style={{
          display: 'block', width: 14, height: 14, borderRadius: 7,
          background: '#fff', position: 'absolute', top: 3,
          left: value ? 19 : 3, transition: 'left 0.2s',
        }} />
      </button>
    </label>
  );

const RadioGroup: React.FC<{
  options: string[]; value: string; onChange: (v: string) => void;
}> = ({ options, value, onChange }) => (
  <div style={{ display: 'flex' }}>
    {options.map((opt, i) => (
      <button key={opt} type="button" onClick={() => onChange(opt)}
        style={{
          padding: '4px 10px', fontSize: 11, cursor: 'pointer',
          border: '1px solid #1f6feb',
          borderLeft: i > 0 ? 'none' : '1px solid #1f6feb',
          borderRadius:
            i === 0 ? '4px 0 0 4px'
            : i === options.length - 1 ? '0 4px 4px 0'
            : '0',
          background: value === opt ? '#1f6feb' : '#0d1421',
          color: value === opt ? '#fff' : '#c9d1d9',
          fontFamily: 'inherit', transition: 'all 0.15s',
        }}>
        {opt}
      </button>
    ))}
  </div>
);

const Select: React.FC<{
  value: string; options: string[];
  onChange?: (v: string) => void;
  disabled?: boolean;
  disabledOptions?: string[];
  blueAccent?: boolean;
}> = ({ value, options, onChange, disabled, disabledOptions, blueAccent }) => (
  <div style={{ position: 'relative', display: 'inline-block' }}>
    <select
      value={value}
      onChange={e => onChange?.(e.target.value)}
      disabled={disabled}
      style={{
        appearance: 'none',
        border: blueAccent ? '1px solid #1f6feb' : '1px solid #1a2d42',
        background: blueAccent && !disabled ? '#1f6feb' : '#080e16',
        color:      blueAccent && !disabled ? '#fff'    : '#c9d1d9',
        opacity:    disabled ? 0.6 : 1,
        borderRadius: 4, fontSize: 11,
        padding: '4px 28px 4px 8px',
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontFamily: 'inherit',
      }}
    >
      {options.map(o => (
        <option key={o} value={o} disabled={disabledOptions?.includes(o)}
          style={{ background: '#080e16', color: '#c9d1d9' }}>
          {o}{disabledOptions?.includes(o) ? '  (Phase 3)' : ''}
        </option>
      ))}
    </select>
    <svg style={{
      position: 'absolute', right: 6, top: '50%',
      transform: 'translateY(-50%)', pointerEvents: 'none',
      width: 12, height: 12,
      color: blueAccent && !disabled ? '#fff' : '#c9d1d9',
    }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M19 9l-7 7-7-7" />
    </svg>
  </div>
);

/**
 * Number stepper — same shape as the qty stepper in historical/StrategyPanel.tsx.
 * Used for any integer/float input where a − / + click feels nicer than browser arrows.
 */
const Stepper: React.FC<{
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  width?: number;
  disabled?: boolean;
  decimals?: number;
}> = ({ value, onChange, min = 0, max, step = 1, width = 64, disabled, decimals = 0 }) => {
  const [text, setText] = useState(String(value));
  useEffect(() => { setText(decimals ? value.toFixed(decimals) : String(value)); }, [value, decimals]);

  const clamp = (n: number) => {
    if (Number.isNaN(n)) return min;
    let v = n;
    if (min != null) v = Math.max(min, v);
    if (max != null) v = Math.min(max, v);
    return decimals ? Number(v.toFixed(decimals)) : v;
  };
  const commit = (n: number) => {
    const v = clamp(n);
    setText(decimals ? v.toFixed(decimals) : String(v));
    onChange(v);
  };

  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center',
      background: '#080e16', border: '1px solid #1a2d42',
      borderRadius: 4, overflow: 'hidden',
      opacity: disabled ? 0.5 : 1,
    }}>
      <button type="button" disabled={disabled}
        onClick={() => commit((parseFloat(text) || 0) - step)}
        style={{
          width: 22, height: 24, background: 'none', border: 'none',
          color: '#7a9bb5', fontSize: 14, cursor: disabled ? 'not-allowed' : 'pointer',
          padding: 0,
        }}>−</button>
      <input
        type="text" inputMode="decimal" value={text} disabled={disabled}
        onChange={e => {
          setText(e.target.value);
          const n = parseFloat(e.target.value);
          if (!Number.isNaN(n)) onChange(clamp(n));
        }}
        onBlur={() => commit(parseFloat(text) || min)}
        style={{
          width, background: 'none', border: 'none',
          borderLeft: '1px solid #1a2d42', borderRight: '1px solid #1a2d42',
          color: '#c9d1d9',
          padding: '2px 4px', fontSize: 11, fontFamily: 'inherit',
          textAlign: 'center', outline: 'none',
        }}
      />
      <button type="button" disabled={disabled}
        onClick={() => commit((parseFloat(text) || 0) + step)}
        style={{
          width: 22, height: 24, background: 'none', border: 'none',
          color: '#7a9bb5', fontSize: 14, cursor: disabled ? 'not-allowed' : 'pointer',
          padding: 0,
        }}>+</button>
    </div>
  );
};

const IconBtn: React.FC<{
  children: React.ReactNode; onClick: () => void; title: string; danger?: boolean;
}> = ({ children, onClick, title, danger }) => (
  <button type="button" onClick={onClick} title={title}
    style={{
      width: 26, height: 26, borderRadius: 4,
      border: `1px solid ${danger ? '#ff4d6a' : '#1a2d42'}`,
      background: '#0d1421',
      color: danger ? '#ff4d6a' : '#c9d1d9',
      fontSize: danger ? 16 : 12, lineHeight: '1',
      cursor: 'pointer', padding: 0,
    }}>
    {children}
  </button>
);

// ── Styles (dark theme matching Live/Historical) ─────────────────────────────

const S = {
  page: {
    fontFamily: 'system-ui, sans-serif',
    background: '#080e16',
    color: '#c9d1d9',
    minHeight: '100%',
    height: '100%',
    overflowY: 'auto' as const,
    position: 'relative' as const,
  },
  headerBar: {
    position: 'sticky' as const, top: 0, zIndex: 11,
    background: '#0d1421', borderBottom: '1px solid #1a2d42',
    padding: '12px 20px',
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    flexWrap: 'wrap' as const, gap: 12,
  },
  pageTitle: { fontSize: 22, fontWeight: 500, margin: 0, color: '#e6edf3' },
  tabBadge: {
    background: '#0a2533', color: '#5dadec',
    border: '1px solid #1f4f6e', borderRadius: 4,
    padding: '3px 10px', fontSize: 11, fontWeight: 600,
  },
  statLabel: { color: '#7a9bb5', marginBottom: 2 },
  statVal:   { fontWeight: 600, color: '#e6edf3' },
  topGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
    gap: 14,
  } as const,
  card: {
    border: '1px solid #1a2d42', borderRadius: 8,
    background: '#0d1421',
  },
  sectionHeader: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 8,
  } as const,
  sectionTitle: {
    fontSize: 14, fontWeight: 500, margin: 0, color: '#e6edf3',
    textTransform: 'uppercase' as const, letterSpacing: 0.4,
  },
  input: {
    fontSize: 11, border: '1px solid #1a2d42',
    borderRadius: 4, padding: '4px 8px',
    background: '#080e16', color: '#c9d1d9',
    fontFamily: 'inherit',
  } as const,
  dateInput: {
    fontSize: 11, border: '1px solid #1f4f6e',
    borderRadius: 4, padding: '3px 8px',
    background: '#080e16', color: '#c9d1d9',
    colorScheme: 'dark' as const,
    fontFamily: 'inherit',
  } as const,
  checkboxLabel: {
    display: 'flex', alignItems: 'center', gap: 6,
    fontSize: 11, cursor: 'pointer', color: '#c9d1d9',
  } as const,
  subLabel: {
    fontSize: 11, fontWeight: 500, color: '#c9d1d9',
  } as const,
  addBtn: {
    background: '#1f6feb', color: '#fff', border: 'none',
    borderRadius: 4, padding: '7px 22px', fontSize: 12,
    cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500,
  },
  stickyFooter: {
    position: 'sticky' as const, bottom: 0,
    background: '#0d1421', borderTop: '1px solid #1a2d42',
    padding: '10px 20px',
    display: 'flex', justifyContent: 'flex-end', gap: 10,
    boxShadow: '0 -2px 8px rgba(0,0,0,0.3)',
  },
  runBtn: {
    background: '#00b87a', border: '1px solid #00925f',
    color: '#fff', borderRadius: 4, padding: '7px 22px',
    fontSize: 12, fontFamily: 'inherit', fontWeight: 500,
    display: 'flex', alignItems: 'center', gap: 6,
  } as const,
};
