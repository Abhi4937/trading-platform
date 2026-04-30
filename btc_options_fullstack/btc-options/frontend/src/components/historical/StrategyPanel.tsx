import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import * as XLSX from 'xlsx';
import { historicalApi } from '../../services/historical_api';
import { MultiPaneChart } from './MultiPaneChart';
import type { PaneSeries } from './MultiPaneChart';
import { PayoffGraph } from './PayoffGraph';
import { computePortfolioMargin, buildMarginLegs } from '../../utils/marginEngine';
import { computeSlippagePerSide, dteFromEntry, istHour, isWeekendIst } from '../../utils/slippage';
import type { SlippageMode, SlippageInputs } from '../../utils/slippage';
import { computeBrokerage, BROKERAGE_RATE_LABELS } from '../../utils/brokerage';
import { usePersistedState } from '../../hooks/usePersistedState';
import type { BrokerageRate } from '../../utils/brokerage';
import type { Strategy, StrategyLeg, MtmPoint } from '../../types/strategy';
import type { HistoricalChainRow, AtmIvPoint } from '../../types/historical';

interface LegGreeksPoint { time: number; spot: number; iv: number; rv: number; delta: number; gamma: number; theta: number; vega: number; }

// IV/Delta panes show 4h before trade entry, capped at 6:00 PM IST of the trade day
// (right after 5:30 PM IST settlement — earlier data belongs to the previous expiry cycle).
function computeIvStartTs(tradeTs: number): number {
  const FOUR_HOURS = 4 * 3600;
  const ist = new Date((tradeTs + 5.5 * 3600) * 1000);
  const sixPmIstTs = Math.floor(Date.UTC(
    ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate(), 12, 30,
  ) / 1000);
  return tradeTs > sixPmIstTs ? sixPmIstTs : tradeTs - FOUR_HOURS;
}

// ── MTM statistics ────────────────────────────────────────────────────────────
interface DrawdownPeriod {
  peakTime: number; peakPnl: number;
  troughTime: number; troughPnl: number;
  drawdown: number; // negative
}
interface MtmStats {
  maxPnl: number; maxPnlTime: number;
  minPnl: number; minPnlTime: number;
  finalPnl: number;
  maxDrawdown: number;
  maxDdPeakTime: number; maxDdPeakPnl: number;
  maxDdTroughTime: number; maxDdTroughPnl: number;
  drawdowns: DrawdownPeriod[];
}

function computeMtmStats(data: MtmPoint[]): MtmStats | null {
  if (data.length < 2) return null;
  let maxPnl = -Infinity, maxPnlTime = data[0].time;
  let minPnl = Infinity, minPnlTime = data[0].time;
  let peakPnl = data[0].pnl, ddPeakPnl = data[0].pnl, ddPeakTime = data[0].time;
  let troughPnl = data[0].pnl, troughTime = data[0].time;
  let inDd = false;
  const drawdowns: DrawdownPeriod[] = [];

  for (const pt of data) {
    if (pt.pnl > maxPnl) { maxPnl = pt.pnl; maxPnlTime = pt.time; }
    if (pt.pnl < minPnl) { minPnl = pt.pnl; minPnlTime = pt.time; }
    if (pt.pnl >= peakPnl) {
      if (inDd) {
        drawdowns.push({ peakTime: ddPeakTime, peakPnl: ddPeakPnl, troughTime, troughPnl, drawdown: troughPnl - ddPeakPnl });
        inDd = false;
      }
      peakPnl = pt.pnl; ddPeakPnl = pt.pnl; ddPeakTime = pt.time;
      troughPnl = pt.pnl; troughTime = pt.time;
    } else {
      inDd = true;
      if (pt.pnl < troughPnl) { troughPnl = pt.pnl; troughTime = pt.time; }
    }
  }
  if (inDd) drawdowns.push({ peakTime: ddPeakTime, peakPnl: ddPeakPnl, troughTime, troughPnl, drawdown: troughPnl - ddPeakPnl });
  drawdowns.sort((a, b) => a.peakTime - b.peakTime); // chronological

  const worst = drawdowns[0];
  return {
    maxPnl, maxPnlTime, minPnl, minPnlTime,
    finalPnl: data[data.length - 1].pnl,
    maxDrawdown: worst?.drawdown ?? 0,
    maxDdPeakTime: worst?.peakTime ?? 0, maxDdPeakPnl: worst?.peakPnl ?? 0,
    maxDdTroughTime: worst?.troughTime ?? 0, maxDdTroughPnl: worst?.troughPnl ?? 0,
    drawdowns,
  };
}

const IST = 5.5 * 3600;
const fmtTs = (ts: number) => {
  const d = new Date((ts + IST) * 1000);
  return `${String(d.getUTCDate()).padStart(2,'0')}/${String(d.getUTCMonth()+1).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
};

interface SensitivityRow { mult: number; finalPnl: number }

const MtmStatsPanel: React.FC<{
  stats: MtmStats;
  color?: string;
  slipEntryUsd?: number;
  slipExitUsd?: number;
  brokerageEntryUsd?: number;
  brokerageExitUsd?: number;
  maxExitPnl?: number;   // peak P&L net of exit costs at that moment
  sensitivity?: SensitivityRow[];
}> = ({ stats, color = 'var(--accent)', slipEntryUsd = 0, slipExitUsd = 0,
        brokerageEntryUsd = 0, brokerageExitUsd = 0, maxExitPnl, sensitivity }) => {
  const [expanded, setExpanded] = useState(false);
  // Chart curve already nets entry slip; brokerage (entry+exit) and exit slip are deducted here.
  const displayFinal = stats.finalPnl - brokerageEntryUsd - slipExitUsd - brokerageExitUsd;
  return (
    <div className="mtm-stats-panel">
      <div className="mtm-stats-row" onClick={() => setExpanded(e => !e)} style={{ cursor: 'pointer' }}>
        <span className="mtm-stats-title" style={{ color }}>MTM Stats</span>
        <div className="mtm-stat">
          <span className="mtm-stat-key">Max P&L</span>
          <span className="mtm-stat-val" style={{ color: 'var(--green)' }}>+{stats.maxPnl.toFixed(2)}</span>
          <span className="mtm-stat-time">{fmtTs(stats.maxPnlTime)}</span>
        </div>
        {maxExitPnl !== undefined && (
          <div className="mtm-stat" title="Net P&L if you had closed at the peak (after exit slip + exit fee at that moment)">
            <span className="mtm-stat-key">If closed @ peak</span>
            <span className="mtm-stat-val" style={{ color: maxExitPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {maxExitPnl >= 0 ? '+' : ''}{maxExitPnl.toFixed(2)}
            </span>
          </div>
        )}
        <div className="mtm-stat">
          <span className="mtm-stat-key">Min P&L</span>
          <span className="mtm-stat-val" style={{ color: stats.minPnl < 0 ? 'var(--red)' : 'var(--green)' }}>{stats.minPnl.toFixed(2)}</span>
          <span className="mtm-stat-time">{fmtTs(stats.minPnlTime)}</span>
        </div>
        <div className="mtm-stat">
          <span className="mtm-stat-key">Max DD</span>
          <span className="mtm-stat-val" style={{ color: 'var(--red)' }}>{stats.maxDrawdown.toFixed(2)}</span>
          <span className="mtm-stat-time">{fmtTs(stats.maxDdPeakTime)} → {fmtTs(stats.maxDdTroughTime)}</span>
        </div>
        <div className="mtm-stat">
          <span className="mtm-stat-key">Final P&L</span>
          <span className="mtm-stat-val" style={{ color: displayFinal >= 0 ? 'var(--green)' : 'var(--red)' }}>
            {displayFinal >= 0 ? '+' : ''}{displayFinal.toFixed(2)}
          </span>
        </div>
        {slipEntryUsd > 0 && (
          <div className="mtm-stat" title="Entry slippage — already deducted in chart curve">
            <span className="mtm-stat-key">Entry slip</span>
            <span className="mtm-stat-val" style={{ color: 'var(--text3)' }}>
              −{slipEntryUsd.toFixed(2)}
            </span>
          </div>
        )}
        {slipExitUsd > 0 && (
          <div className="mtm-stat" title="Exit slippage computed at exit time (hour + mark at close)">
            <span className="mtm-stat-key">Exit slip</span>
            <span className="mtm-stat-val" style={{ color: 'var(--text3)' }}>
              −{slipExitUsd.toFixed(2)}
            </span>
          </div>
        )}
        {brokerageEntryUsd > 0 && (
          <div className="mtm-stat" title="Entry brokerage — taker fee + 18% GST. Deducted from Final P&L (not from chart curve).">
            <span className="mtm-stat-key">Entry fee</span>
            <span className="mtm-stat-val" style={{ color: 'var(--text3)' }}>
              −{brokerageEntryUsd.toFixed(2)}
            </span>
          </div>
        )}
        {brokerageExitUsd > 0 && (
          <div className="mtm-stat" title="Exit brokerage — taker fee + 18% GST at exit mark price">
            <span className="mtm-stat-key">Exit fee</span>
            <span className="mtm-stat-val" style={{ color: 'var(--text3)' }}>
              −{brokerageExitUsd.toFixed(2)}
            </span>
          </div>
        )}
        <span className="mtm-stats-toggle">{expanded ? '▲' : '▼'} {stats.drawdowns.length} DD{stats.drawdowns.length !== 1 ? 's' : ''}</span>
      </div>

      {expanded && (
        <>
          {sensitivity && sensitivity.length > 0 && (
            <div className="mtm-dd-table-wrap" style={{ marginBottom: 8 }}>
              <table className="mtm-dd-table">
                <thead>
                  <tr>
                    <th colSpan={sensitivity.length + 1} style={{ textAlign: 'left', color: 'var(--text2)' }}>
                      Slippage sensitivity — Final P&L at multiplier
                    </th>
                  </tr>
                  <tr>
                    <th>×</th>
                    {sensitivity.map(r => <th key={r.mult}>{r.mult.toFixed(1)}×</th>)}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td style={{ color: 'var(--text3)' }}>P&L</td>
                    {sensitivity.map(r => (
                      <td key={r.mult} style={{
                        color: r.finalPnl >= 0 ? 'var(--green)' : 'var(--red)',
                        fontWeight: 600,
                      }}>
                        {r.finalPnl >= 0 ? '+' : ''}{r.finalPnl.toFixed(2)}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          )}
          {stats.drawdowns.length > 0 && (
            <div className="mtm-dd-table-wrap">
              <table className="mtm-dd-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Drawdown</th>
                    <th>Peak P&L</th>
                    <th>Peak Time</th>
                    <th>Trough P&L</th>
                    <th>Trough Time</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.drawdowns.map((dd, i) => (
                    <tr key={i}>
                      <td style={{ color: 'var(--text3)' }}>{i + 1}</td>
                      <td style={{ color: 'var(--red)', fontWeight: 600 }}>{dd.drawdown.toFixed(2)}</td>
                      <td style={{ color: dd.peakPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{dd.peakPnl >= 0 ? '+' : ''}{dd.peakPnl.toFixed(2)}</td>
                      <td style={{ color: 'var(--text3)' }}>{fmtTs(dd.peakTime)}</td>
                      <td style={{ color: dd.troughPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{dd.troughPnl >= 0 ? '+' : ''}{dd.troughPnl.toFixed(2)}</td>
                      <td style={{ color: 'var(--text3)' }}>{fmtTs(dd.troughTime)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
};

interface Props {
  // Build mode
  legs: StrategyLeg[];
  onRemoveLeg: (legId: string) => void;
  onUpdateQty: (legId: string, qty: number) => void;
  onClearLegs: () => void;
  // Mode
  compareMode: boolean;
  onEnterCompare: () => void;
  onExitCompare: () => void;
  // Compare mode
  compareStrategies: Strategy[];
  activeCompareStratId: string;
  onSetActiveCompareStrat: (id: string) => void;
  onAddCompareStrategy: () => void;
  onRemoveCompareStrategy: (stratId: string) => void;
  onRemoveCompareLeg: (stratId: string, legId: string) => void;
  onUpdateCompareLegQty: (stratId: string, legId: string, qty: number) => void;
  onClearCompareLegs: (stratId: string) => void;
  // Shared
  chain: HistoricalChainRow[];
  legChains: Map<string, HistoricalChainRow[]>;
  spot: number;
  selectedExpiry: string;
  simulationTimestamp: number;
  maximized: boolean;
  onToggleMaximize: () => void;
  chartsOnly: boolean;
  onToggleChartsOnly: () => void;
}

const fmt = (n: number, d = 2) => n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const STRAT_COLORS = ['#00d4ff', '#f0b429', '#a78bfa', '#fb923c', '#34d399', '#f472b6', '#60a5fa', '#ff6b6b'];

const LegQtyInput: React.FC<{
  id: string; qty: number;
  onUpdate: (id: string, qty: number) => void;
}> = ({ id, qty, onUpdate }) => {
  const [val, setVal] = useState(String(qty));
  useEffect(() => { setVal(String(qty)); }, [qty]);
  const commit = (n: number) => { const v = Math.max(1, n); setVal(String(v)); onUpdate(id, v); };
  return (
    <div className="qty-stepper">
      <button className="qty-btn" onClick={() => commit(Math.max(1, (parseInt(val) || 1) - 1))}>−</button>
      <input
        type="text" inputMode="numeric" className="strategy-qty-input" value={val}
        onChange={e => { setVal(e.target.value); const n = parseInt(e.target.value); if (!isNaN(n) && n >= 1) onUpdate(id, n); }}
        onBlur={() => commit(parseInt(val) || 1)}
      />
      <button className="qty-btn" onClick={() => commit((parseInt(val) || 1) + 1)}>+</button>
    </div>
  );
};

function leverageColor(lev: number): string {
  if (lev < 5) return 'var(--green)';
  if (lev < 15) return 'var(--gold)';
  return 'var(--red)';
}

export const StrategyPanel: React.FC<Props> = ({
  legs, onRemoveLeg, onUpdateQty, onClearLegs,
  compareMode, onEnterCompare, onExitCompare,
  compareStrategies, activeCompareStratId, onSetActiveCompareStrat,
  onAddCompareStrategy, onRemoveCompareStrategy,
  onRemoveCompareLeg, onUpdateCompareLegQty, onClearCompareLegs,
  chain, legChains, spot, selectedExpiry, simulationTimestamp,
  maximized, onToggleMaximize,
  chartsOnly, onToggleChartsOnly,
}) => {
  // Build mode MTM — persisted across mode switches & reloads.
  // legGreeks is a Map at use-sites; on disk we store it as a plain Record so
  // JSON.stringify works. Boundary conversion happens at the get/set sites.
  const [buildMtmData, setBuildMtmData] = usePersistedState<MtmPoint[]>('historical:buildMtmData', []);
  const [buildLoading, setBuildLoading] = useState(false);
  const [buildError, setBuildError] = useState('');
  const [buildLegGreeksObj, setBuildLegGreeksObj] = usePersistedState<Record<string, LegGreeksPoint[]>>('historical:buildLegGreeks', {});
  const buildLegGreeks = useMemo(
    () => new Map(Object.entries(buildLegGreeksObj)),
    [buildLegGreeksObj],
  );
  const setBuildLegGreeks = useCallback((m: Map<string, LegGreeksPoint[]>) => {
    setBuildLegGreeksObj(Object.fromEntries(m));
  }, [setBuildLegGreeksObj]);
  const [buildExitLegData,    setBuildExitLegData]    = usePersistedState<{ leg: StrategyLeg; exitTs: number; exitMark: number; exitSpot: number }[]>('historical:buildExitLegData', []);
  const [buildMaxPnlExitData, setBuildMaxPnlExitData] = usePersistedState<{ leg: StrategyLeg; exitTs: number; exitMark: number; exitSpot: number }[]>('historical:buildMaxPnlExitData', []);
  const [buildAtmData, setBuildAtmData] = usePersistedState<AtmIvPoint[]>('historical:buildAtmData', []);

  // Compare mode MTM
  const [compareMtmData, setCompareMtmData] = useState<Map<string, MtmPoint[]>>(new Map());
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState('');
  // stratId → legId → points
  const [compareLegGreeks, setCompareLegGreeks] = useState<Map<string, Map<string, LegGreeksPoint[]>>>(new Map());
  const [compareExitLegData,    setCompareExitLegData]    = useState<Map<string, { leg: StrategyLeg; exitTs: number; exitMark: number; exitSpot: number }[]>>(new Map());
  const [compareMaxPnlExitData, setCompareMaxPnlExitData] = useState<Map<string, { leg: StrategyLeg; exitTs: number; exitMark: number; exitSpot: number }[]>>(new Map());
  // ATM IV/RV per expiry (keyed by expiry YYYY-MM-DD), shared across compare strategies
  const [compareAtmData, setCompareAtmData] = useState<Map<string, AtmIvPoint[]>>(new Map());

  const [marginExpanded, setMarginExpanded] = useState(false);
  const [compareMarginExpanded, setCompareMarginExpanded] = useState<Set<string>>(new Set());
  const [legsExpanded, setLegsExpanded] = useState(true);
  const [compareLegsExpanded, setCompareLegsExpanded] = useState(true);
  const [timeframe, setTimeframe] = useState<'1m'|'5m'|'15m'|'30m'|'1h'>('5m');
  const [rvWindowDays, setRvWindowDays] = useState<7|14|30|60|90>(7);
  const [endDate, setEndDate] = useState(selectedExpiry);
  const [endTime, setEndTime] = useState('17:30');

  // Slippage controls
  const [slipEnabled, setSlipEnabled] = useState(true);
  const [slipMode, setSlipMode]       = useState<SlippageMode>('smart');
  const [slipFlat, setSlipFlat]       = useState(5);
  const [slipMult, setSlipMult]       = useState(1.0);

  // Brokerage controls
  const [brokerageEnabled,  setBrokerageEnabled]  = useState(true);
  const [brokerageRate,     setBrokerageRate]      = useState<BrokerageRate>('offer');
  const [brokerageReferral, setBrokerageReferral] = useState(false);

  useEffect(() => { setEndDate(selectedExpiry); }, [selectedExpiry]);

  // Collapse legs + margin when maximizing, restore when restoring
  useEffect(() => {
    if (maximized) { setLegsExpanded(false); setMarginExpanded(false); setCompareLegsExpanded(false); }
    else { setLegsExpanded(true); setCompareLegsExpanded(true); }
  }, [maximized]);

  // ── Chart / stats resize ───────────────────────────────────────────────────
  const [chartHeightPx, setChartHeightPx] = useState(220);
  // Bottom handle: drag DOWN = stats grow (independent of chart), drag UP = stats shrink
  const [statsHeightPx, setStatsHeightPx] = useState(140);

  const [buildTab, setBuildTab] = useState<'mtm' | 'payoff'>('mtm');
  useEffect(() => { if (legs.length === 0) setBuildTab('mtm'); }, [legs.length]);

  const buildBottomDragRef = useRef<{ startY: number; startChartH: number; startStatsH: number } | null>(null);
  const onBottomDragHandleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    buildBottomDragRef.current = { startY: e.clientY, startChartH: chartHeightPx, startStatsH: statsHeightPx };
    const onMove = (ev: MouseEvent) => {
      if (!buildBottomDragRef.current) return;
      const delta = ev.clientY - buildBottomDragRef.current.startY;
      setChartHeightPx(Math.max(60, buildBottomDragRef.current.startChartH + delta));
      setStatsHeightPx(Math.max(40, buildBottomDragRef.current.startStatsH - delta));
    };
    const onUp = () => {
      buildBottomDragRef.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [chartHeightPx, statsHeightPx]);


  // Clear MTM when legs or compare strategies change.
  // Skip the very first render so persisted MTM data survives the mount —
  // otherwise switching modes and coming back would wipe the chart even though
  // legs are unchanged.
  const buildResetSkipRef = useRef(false);
  useEffect(() => {
    if (!buildResetSkipRef.current) { buildResetSkipRef.current = true; return; }
    setBuildMtmData([]); setBuildError(''); setBuildLegGreeks(new Map()); setBuildExitLegData([]); setBuildMaxPnlExitData([]);
  }, [legs]);  // eslint-disable-line react-hooks/exhaustive-deps
  const compareResetSkipRef = useRef(false);
  useEffect(() => {
    if (!compareResetSkipRef.current) { compareResetSkipRef.current = true; return; }
    setCompareMtmData(new Map()); setCompareError(''); setCompareLegGreeks(new Map()); setCompareExitLegData(new Map()); setCompareMaxPnlExitData(new Map());
  }, [compareStrategies]);

  // ── Chain helpers ──────────────────────────────────────────────────────────
  const getChainForLeg = (leg: StrategyLeg): HistoricalChainRow[] =>
    leg.expiry === selectedExpiry ? chain : (legChains.get(leg.expiry) ?? []);

  const getCurrentPrice = (leg: StrategyLeg) => {
    const row = getChainForLeg(leg).find(r => r.strike === leg.strike);
    if (!row) return leg.entryPremium;
    return leg.type === 'CE' ? row.call.last_price : row.put.last_price;
  };

  const getLegGreeks = (leg: StrategyLeg) => {
    const row = getChainForLeg(leg).find(r => r.strike === leg.strike);
    if (!row) return null;
    const opt = leg.type === 'CE' ? row.call : row.put;
    const dir = leg.action === 'BUY' ? 1 : -1;
    const btc = leg.qty * 0.001; // chain Greeks are per 1 BTC (= 1000 lots)
    return {
      iv_pct: opt.iv_pct,
      delta: opt.delta * btc * dir,
      gamma: opt.gamma * btc * dir,
      theta: opt.theta * btc * dir,
      vega: opt.vega * btc * dir,
    };
  };

  // ── Slippage helpers ─────────────────────────────────────────────────────
  // Per-side slippage in $/contract — same value used by chart (entry only)
  // and stats (round-trip = 2x). Returns 0 when disabled.
  const slipPerSide = useCallback((leg: StrategyLeg, multOverride?: number): number => {
    const inputs: SlippageInputs | null = leg.entrySpot > 0
      ? {
          spot: leg.entrySpot,
          markClose: leg.entryPremium,
          dte: dteFromEntry(leg.entryTimestamp, leg.expiry),
          strike: leg.strike,
          isCall: leg.type === 'CE',
          hourIst: istHour(leg.entryTimestamp),
          isWeekend: isWeekendIst(leg.entryTimestamp),
          oiClose: leg.entryOi,
          qty: leg.qty,
        }
      : null;
    return computeSlippagePerSide(slipEnabled, slipMode, slipFlat, multOverride ?? slipMult, inputs);
  }, [slipEnabled, slipMode, slipFlat, slipMult]);

  // Exit-time slippage: same model but uses exit timestamp + exit mark + exit spot.
  const slipPerSideAt = useCallback((
    leg: StrategyLeg, exitTs: number, exitMark: number, exitSpot: number, multOverride?: number,
  ): number => {
    const inputs: SlippageInputs = {
      spot: exitSpot,
      markClose: exitMark,
      dte: 0,
      strike: leg.strike,
      isCall: leg.type === 'CE',
      hourIst: istHour(exitTs),
      isWeekend: isWeekendIst(exitTs),
      qty: leg.qty,
    };
    return computeSlippagePerSide(slipEnabled, slipMode, slipFlat, multOverride ?? slipMult, inputs);
  }, [slipEnabled, slipMode, slipFlat, slipMult]);

  // Round-trip cost in USD (after qty / contract size). What the stats panel reports.
  const slipRoundTripUsd = useCallback((leg: StrategyLeg, multOverride?: number): number =>
    2 * slipPerSide(leg, multOverride) * leg.qty * 0.001,
  [slipPerSide]);

  // ── Brokerage helpers ────────────────────────────────────────────────────────
  // Entry-time brokerage (uses leg's entry spot + mark).
  const brokeragePerSide = useCallback((leg: StrategyLeg): number => {
    if (!brokerageEnabled) return 0;
    return computeBrokerage(leg.entrySpot, leg.entryPremium, leg.qty, brokerageRate, brokerageReferral);
  }, [brokerageEnabled, brokerageRate, brokerageReferral]);

  // Exit-time brokerage (uses provided exit spot + mark, rate same as entry).
  const brokeragePerSideAt = useCallback((
    leg: StrategyLeg, exitMark: number, exitSpot: number,
  ): number => {
    if (!brokerageEnabled) return 0;
    return computeBrokerage(exitSpot, exitMark, leg.qty, brokerageRate, brokerageReferral);
  }, [brokerageEnabled, brokerageRate, brokerageReferral]);

  const getLegPnl = (leg: StrategyLeg) => {
    const dir = leg.action === 'BUY' ? 1 : -1;
    const gross = (getCurrentPrice(leg) - leg.entryPremium) * leg.qty * dir * 0.001;
    return gross - slipRoundTripUsd(leg);
  };

  const totalPnl = legs.reduce((s, l) => s + getLegPnl(l), 0);

  const netGreeks = useMemo(() => {
    let delta = 0, gamma = 0, theta = 0, vega = 0, hasData = false;
    legs.forEach(leg => {
      const g = getLegGreeks(leg);
      if (g) { delta += g.delta; gamma += g.gamma; theta += g.theta; vega += g.vega; hasData = true; }
    });
    return hasData ? { delta, gamma, theta, vega } : null;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legs, chain, legChains, selectedExpiry]);

  // Combined per-expiry chain map: active expiry's chain + any fetched leg chains.
  const chainsByExpiry = useMemo(() => {
    const m = new Map(legChains);
    if (selectedExpiry) m.set(selectedExpiry, chain);
    return m;
  }, [chain, legChains, selectedExpiry]);

  // ── Margin (build mode) ────────────────────────────────────────────────────
  const marginResult = useMemo(() => {
    if (!legs.length || spot <= 0 || !simulationTimestamp) return null;
    const { marginLegs, skippedLegs } = buildMarginLegs(legs, chainsByExpiry, spot, simulationTimestamp);
    if (!marginLegs.length) return null;
    return computePortfolioMargin(marginLegs, spot, undefined, skippedLegs);
  }, [legs, chainsByExpiry, spot, simulationTimestamp]);

  // ── Margin (compare mode — per-strategy) ───────────────────────────────────
  const compareMarginResults = useMemo(() => {
    const map = new Map<string, ReturnType<typeof computePortfolioMargin> | null>();
    if (spot <= 0 || !simulationTimestamp) return map;
    compareStrategies.forEach(s => {
      if (!s.legs.length) { map.set(s.id, null); return; }
      const { marginLegs, skippedLegs } = buildMarginLegs(s.legs, chainsByExpiry, spot, simulationTimestamp);
      map.set(s.id, marginLegs.length ? computePortfolioMargin(marginLegs, spot, undefined, skippedLegs) : null);
    });
    return map;
  }, [compareStrategies, chainsByExpiry, spot, simulationTimestamp]);

  const minEntryTs = legs.length ? Math.min(...legs.map(l => l.entryTimestamp)) : undefined;

  // ── Download (build mode) ──────────────────────────────────────────────────
  const toIst = (ts: number) => {
    const d = new Date((ts + 5.5 * 3600) * 1000);
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' IST';
  };

  // Convert MtmStats to flat rows for Excel
  const statsToRows = (stats: MtmStats, label?: string): Record<string, unknown>[] => {
    const prefix = label ? `[${label}] ` : '';
    const summary: Record<string, unknown>[] = [
      { Metric: `${prefix}Max P&L`,     Value: +stats.maxPnl.toFixed(4),      Time: fmtTs(stats.maxPnlTime) },
      { Metric: `${prefix}Min P&L`,     Value: +stats.minPnl.toFixed(4),      Time: fmtTs(stats.minPnlTime) },
      { Metric: `${prefix}Final P&L`,   Value: +stats.finalPnl.toFixed(4),    Time: '' },
      { Metric: `${prefix}Max Drawdown`,Value: +stats.maxDrawdown.toFixed(4),
        Time: `${fmtTs(stats.maxDdPeakTime)} → ${fmtTs(stats.maxDdTroughTime)}` },
      {},
      { Metric: `${prefix}# Drawdown`, 'DD Value': 'Peak P&L', 'Peak Time': 'Trough P&L', 'Trough Time': '' },
      ...stats.drawdowns.map((dd, i) => ({
        Metric: `${prefix}DD ${i + 1}`,
        'DD Value': +dd.drawdown.toFixed(4),
        'Peak P&L': +dd.peakPnl.toFixed(4),
        'Peak Time': fmtTs(dd.peakTime),
        'Trough P&L': +dd.troughPnl.toFixed(4),
        'Trough Time': fmtTs(dd.troughTime),
      })),
      {},
    ];
    return summary;
  };

  const downloadExcel = () => {
    const wb = XLSX.utils.book_new();
    const legsRows = legs.map(leg => {
      const slip = slipPerSide(leg);
      return {
        Action: leg.action, Expiry: leg.expiry, Strike: leg.strike, Type: leg.type,
        Lots: leg.qty, 'Entry Premium': leg.entryPremium,
        'Current Price': getCurrentPrice(leg),
        'Est. Slippage Per Side ($/contract)': slipEnabled ? +slip.toFixed(4) : 0,
        'Round-trip Slippage Cost (USD)': slipEnabled ? +(2 * slip * leg.qty * 0.001).toFixed(4) : 0,
        'P&L (USD, after round-trip slippage)': +getLegPnl(leg).toFixed(2),
      };
    });
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(legsRows), 'Strategy');

    // ── Margin Required sheet (29-scenario portfolio margin at entry) ──────
    if (marginResult) {
      const m = marginResult;
      const marginRows: Record<string, unknown>[] = [
        { Metric: 'Portfolio Margin (USDT)',         Value: +m.portfolioMargin.toFixed(2) },
        { Metric: 'Initial Margin (USDT)',           Value: +m.initialMargin.toFixed(2) },
        { Metric: 'Maintenance Margin (USDT)',       Value: +m.maintenanceMargin.toFixed(2) },
        { Metric: 'Margin per Lot (USDT)',           Value: +m.marginPerLot.toFixed(4) },
        {},
        { Metric: 'Risk Margin (worst-scenario loss)',  Value: +m.riskMargin.toFixed(2) },
        { Metric: 'Margin Floor (statutory minimum)',   Value: +m.marginFloor.toFixed(2) },
        { Metric: 'Binding Constraint',                  Value: m.bindingConstraint },
        { Metric: 'DTE Scale Applied',                   Value: +m.dteScaleApplied.toFixed(4) },
        { Metric: 'Pre-scale Margin (USDT)',             Value: +m.preScaleMargin.toFixed(2) },
        {},
        { Metric: 'Total Notional (USDT)',           Value: +m.totalNotional.toFixed(2) },
        { Metric: 'Short Options Notional (USDT)',   Value: +m.shortOptionsNotional.toFixed(2) },
        { Metric: 'Total Premium Collected (USDT)',  Value: +m.totalPremiumCollected.toFixed(2) },
        { Metric: 'Effective Leverage',               Value: +m.effectiveLeverage.toFixed(2) },
        { Metric: 'OM% Applied',                      Value: (m.omPctApplied * 100).toFixed(3) + '%' },
        { Metric: 'Net Delta (BTC)',                  Value: +m.netDeltaBtc.toFixed(4) },
        {},
        { Metric: 'Price Shock Applied',              Value: (m.priceShockApplied * 100).toFixed(2) + '%' },
        { Metric: 'Vol Up Shock (DTE-adjusted)',      Value: (m.volUpApplied * 100).toFixed(2) + ' pts' },
        { Metric: 'Vol Down Shock (DTE-adjusted)',    Value: (m.volDownApplied * 100).toFixed(2) + ' pts' },
        { Metric: 'Min DTE Days Applied',             Value: +m.minDteDaysApplied.toFixed(2) },
        {},
        { Metric: 'Worst Scenario — Spot Shock',      Value: m.worstScenario.priceShockPct.toFixed(2) + '%' },
        { Metric: 'Worst Scenario — Vol Shock (pts)', Value: +m.worstScenario.volShockPts.toFixed(2) },
        { Metric: 'Worst Scenario — PnL (USDT)',      Value: +m.worstScenario.pnl.toFixed(2) },
        {},
        { Metric: 'Best Scenario — Spot Shock',       Value: m.bestScenario.priceShockPct.toFixed(2) + '%' },
        { Metric: 'Best Scenario — Vol Shock (pts)',  Value: +m.bestScenario.volShockPts.toFixed(2) },
        { Metric: 'Best Scenario — PnL (USDT)',       Value: +m.bestScenario.pnl.toFixed(2) },
        {},
        { Metric: 'Skipped Legs (no IV data)',        Value: m.skippedLegs },
      ];
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(marginRows), 'Margin Required');
    }

    if (buildMtmData.length) {
      // Build per-leg lookup: legId → Map<time, LegGreeksPoint>
      const legLabel = (leg: StrategyLeg) => `${leg.action} ${leg.strike}${leg.type}`;
      const legLookup = new Map<string, Map<number, LegGreeksPoint>>();
      legs.forEach(leg => {
        const pts = buildLegGreeks.get(leg.id) ?? [];
        const m = new Map<number, LegGreeksPoint>();
        pts.forEach(p => m.set(p.time, p));
        legLookup.set(leg.id, m);
      });

      const mtmRows = buildMtmData.map(d => {
        const row: Record<string, unknown> = { 'Time (IST)': toIst(d.time) };
        // spot + rv from first leg that has data at this time (both are underlying-only)
        let spot = 0, rv = 0;
        for (const leg of legs) {
          const pt = legLookup.get(leg.id)?.get(d.time);
          if (pt && pt.spot > 0) { spot = pt.spot; rv = pt.rv; break; }
        }
        row['BTC Spot'] = spot > 0 ? +spot.toFixed(2) : '';
        row['RV%']      = rv > 0   ? +rv.toFixed(2)   : '';
        // per-leg Greeks
        let netDelta = 0, netGamma = 0, netTheta = 0, netVega = 0;
        legs.forEach(leg => {
          const label = legLabel(leg);
          const pt = legLookup.get(leg.id)?.get(d.time);
          const dir = leg.action === 'BUY' ? 1 : -1;
          const scale = leg.qty * 0.001 * dir;
          if (pt) {
            row[`${label} IV%`]    = +pt.iv.toFixed(2);
            row[`${label} IV-RV%`] = pt.iv > 0 ? +(pt.iv - pt.rv).toFixed(2) : '';
            row[`${label} Delta`]  = +(pt.delta * scale).toFixed(4);
            row[`${label} Gamma`]  = +(pt.gamma * scale).toFixed(6);
            row[`${label} Theta`]  = +(pt.theta * scale).toFixed(4);
            row[`${label} Vega`]   = +(pt.vega  * scale).toFixed(4);
            netDelta += pt.delta * scale;
            netGamma += pt.gamma * scale;
            netTheta += pt.theta * scale;
            netVega  += pt.vega  * scale;
          } else {
            row[`${label} IV%`] = ''; row[`${label} IV-RV%`] = '';
            row[`${label} Delta`] = '';
            row[`${label} Gamma`] = ''; row[`${label} Theta`] = ''; row[`${label} Vega`] = '';
          }
        });
        row['Net Delta'] = +netDelta.toFixed(4);
        row['Net Gamma'] = +netGamma.toFixed(6);
        row['Net Theta'] = +netTheta.toFixed(4);
        row['Net Vega']  = +netVega.toFixed(4);
        row['Est. Slippage Cost (USD)'] = slipEnabled
          ? +(legs.reduce((s, l) => s + 2 * slipPerSide(l) * l.qty * 0.001, 0)).toFixed(4)
          : 0;
        row['P&L (USD, chart entry-side)'] = +d.pnl.toFixed(4);
        return row;
      });
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(mtmRows), 'MTM P&L');
      const s = computeMtmStats(buildMtmData);
      if (s) {
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(statsToRows(s)), 'MTM Stats');
      }
      // Exit & Peak Marks — per-leg mark/P&L at exit AND at the peak-strategy-MTM bar
      if (buildExitLegData.length || buildMaxPnlExitData.length) {
        const exitPeakRows = legs.map(leg => {
          const dir = leg.action === 'BUY' ? 1 : -1;
          const exitD = buildExitLegData.find(e => e.leg.id === leg.id);
          const peakD = buildMaxPnlExitData.find(e => e.leg.id === leg.id);
          const exitPnl = exitD ? (exitD.exitMark - leg.entryPremium) * leg.qty * dir * 0.001 : null;
          const peakPnl = peakD ? (peakD.exitMark - leg.entryPremium) * leg.qty * dir * 0.001 : null;
          return {
            'Leg':                    `${leg.action} ${leg.strike}${leg.type}`,
            'Action':                 leg.action,
            'Type':                   leg.type,
            'Strike':                 leg.strike,
            'Lots':                   leg.qty,
            'Entry Time (IST)':       toIst(leg.entryTimestamp),
            'Entry Mark':             +leg.entryPremium.toFixed(4),
            'Exit Time (IST)':        exitD ? toIst(exitD.exitTs) : '',
            'Exit Mark':              exitD ? +exitD.exitMark.toFixed(4) : '',
            'Exit Spot':              exitD ? +exitD.exitSpot.toFixed(2) : '',
            'Exit Leg P&L (USD)':     exitPnl !== null ? +exitPnl.toFixed(4) : '',
            'Peak-MTM Time (IST)':    peakD ? toIst(peakD.exitTs) : '',
            'Peak-MTM Mark':          peakD ? +peakD.exitMark.toFixed(4) : '',
            'Peak-MTM Spot':          peakD ? +peakD.exitSpot.toFixed(2) : '',
            'Peak-MTM Leg P&L (USD)': peakPnl !== null ? +peakPnl.toFixed(4) : '',
          };
        });
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(exitPeakRows), 'Exit & Peak Marks');
      }
      // Slippage sensitivity sheet — Final P&L at multipliers 0.5×–3×
      if ((slipEnabled || brokerageEnabled) && s) {
        const slipBase = legs.reduce((acc, l) => acc + slipPerSide(l) * l.qty * 0.001, 0);
        const brkEntry = legs.reduce((acc, l) => acc + brokeragePerSide(l), 0);
        const brkExit  = buildExitLegData.reduce((acc, { leg, exitMark, exitSpot }) =>
          acc + brokeragePerSideAt(leg, exitMark, exitSpot), 0);
        const grossFinal = s.finalPnl + slipBase;
        const sensRows = [0.5, 1.0, 1.5, 2.0, 3.0].map(m => {
          const eSlip = legs.reduce((acc, l) => acc + slipPerSide(l, m) * l.qty * 0.001, 0);
          const xSlip = buildExitLegData.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
            acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot, m) * leg.qty * 0.001, 0);
          return {
            'Slip Mult': `${m.toFixed(1)}×`,
            'Entry Slip (USD)': +eSlip.toFixed(4),
            'Exit Slip (USD)': +xSlip.toFixed(4),
            'Entry Fee (USD)': +brkEntry.toFixed(4),
            'Exit Fee (USD)': +brkExit.toFixed(4),
            'Total Costs (USD)': +(eSlip + xSlip + brkEntry + brkExit).toFixed(4),
            'Final P&L (USD)': +(grossFinal - eSlip - xSlip - brkEntry - brkExit).toFixed(4),
          };
        });
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(sensRows), 'Slippage Sensitivity');
      }
    }
    XLSX.writeFile(wb, `strategy_${selectedExpiry}.xlsx`);
  };

  // ── Run MTM (build mode) ───────────────────────────────────────────────────
  const runBuildMtm = useCallback(async () => {
    if (!legs.length) return;
    setBuildLoading(true);
    setBuildError('');
    try {
      const tradeTs = simulationTimestamp;
      const endTs = Math.floor(new Date(`${endDate}T${endTime}:00+05:30`).getTime() / 1000);

      // Fetch full contract lifetime (start_time=0) so IV/RV/Delta panes show end-to-end.
      // MTM P&L is still anchored to tradeTs via mtmData (the `data` subset).
      // `allData` extends through the contract's lifetime, NOT capped at endTs.
      const seriesResults = await Promise.all(
        legs.map(leg =>
          historicalApi.getChartDataWithGreeks(leg.expiry, leg.strike, leg.type, 0, timeframe, rvWindowDays)
            .then(res => ({
              leg,
              allData: res.data,
              data:    res.data.filter(d => d.time >= tradeTs && d.time <= endTs),
            }))
        )
      );

      // ATM IV/RV time series for the selected expiry (single-shot, contract lifetime)
      historicalApi.getAtmIvSeries(selectedExpiry, timeframe, rvWindowDays)
        .then(res => setBuildAtmData(res.data ?? []))
        .catch(() => setBuildAtmData([]));

      const timeSet = new Set<number>();
      seriesResults.forEach(({ data }) => data.forEach(d => { timeSet.add(d.time); }));
      const sortedTimes = Array.from(timeSet).sort((a, b) => a - b);

      // Entry-side slip baked into every chart point (part of the realized fill price).
      // Brokerage (entry + exit) and exit slip are shown only in the stats panel.
      const entryDeduction = legs.reduce((s, leg) => s + slipPerSide(leg) * leg.qty * 0.001, 0);

      const points: MtmPoint[] = sortedTimes.map(t => {
        let total = 0;
        seriesResults.forEach(({ leg, data }) => {
          const pts = data.filter(d => d.time <= t);
          if (pts.length) {
            const dir = leg.action === 'BUY' ? 1 : -1;
            total += (pts[pts.length - 1].close - leg.entryPremium) * leg.qty * dir * 0.001;
          }
        });
        return { time: t, pnl: total - entryDeduction };
      });

      const greeksMap = new Map<string, LegGreeksPoint[]>();
      seriesResults.forEach(({ leg, allData }) => {
        greeksMap.set(leg.id, allData.map(d => ({
          time: d.time, spot: d.spot, iv: d.iv, rv: d.rv,
          delta: d.delta, gamma: d.gamma, theta: d.theta, vega: d.vega,
        })));
      });

      // Capture exit-time data per leg (last bar) for exit slippage computation
      const exitData = seriesResults.map(({ leg, data }) => {
        const last = data.length ? data[data.length - 1] : null;
        return last ? { leg, exitTs: last.time, exitMark: last.close, exitSpot: last.spot } : null;
      }).filter((x): x is NonNullable<typeof x> => x !== null);

      // Capture per-leg mark/spot at the peak P&L bar (for "if closed @ peak" stat)
      const maxPoint = points.length ? points.reduce((m, p) => p.pnl > m.pnl ? p : m, points[0]) : null;
      const maxExitData = maxPoint ? seriesResults.map(({ leg, data }) => {
        const bar = data.filter(d => d.time <= maxPoint.time);
        const last = bar.length ? bar[bar.length - 1] : null;
        return last ? { leg, exitTs: last.time, exitMark: last.close, exitSpot: last.spot } : null;
      }).filter((x): x is NonNullable<typeof x> => x !== null) : [];

      setBuildMtmData(points);
      setBuildLegGreeks(greeksMap);
      setBuildExitLegData(exitData);
      setBuildMaxPnlExitData(maxExitData);
    } catch {
      setBuildError('Failed to calculate MTM. Check console.');
    } finally {
      setBuildLoading(false);
    }
  }, [legs, simulationTimestamp, endDate, endTime, timeframe, rvWindowDays, slipPerSide, brokeragePerSide]);

  // ── Run MTM (compare mode) ─────────────────────────────────────────────────
  const runCompareMtm = useCallback(async () => {
    const nonEmpty = compareStrategies.filter(s => s.legs.length > 0);
    if (!nonEmpty.length) return;
    setCompareLoading(true);
    setCompareError('');
    try {
      const tradeTs = simulationTimestamp;
      const endTs = Math.floor(new Date(`${endDate}T${endTime}:00+05:30`).getTime() / 1000);
      const map = new Map<string, MtmPoint[]>();
      const greeksMapOuter = new Map<string, Map<string, LegGreeksPoint[]>>();
      const exitMapOuter    = new Map<string, { leg: StrategyLeg; exitTs: number; exitMark: number; exitSpot: number }[]>();
      const maxPnlMapOuter  = new Map<string, { leg: StrategyLeg; exitTs: number; exitMark: number; exitSpot: number }[]>();

      await Promise.all(nonEmpty.map(async strat => {
        const seriesResults = await Promise.all(
          strat.legs.map(leg =>
            historicalApi.getChartDataWithGreeks(leg.expiry, leg.strike, leg.type, 0, timeframe, rvWindowDays)
              .then(res => ({
                leg,
                allData: res.data,
                data:    res.data.filter(d => d.time >= tradeTs && d.time <= endTs),
              }))
          )
        );

        const timeSet = new Set<number>();
        seriesResults.forEach(({ data }) => data.forEach(d => { timeSet.add(d.time); }));
        const sortedTimes = Array.from(timeSet).sort((a, b) => a - b);

        // Entry slip only — brokerage (entry+exit) and exit slip belong to the stats panel.
        const entryDeduction = strat.legs.reduce((s, leg) => s + slipPerSide(leg) * leg.qty * 0.001, 0);

        const points: MtmPoint[] = sortedTimes.map(t => {
          let total = 0;
          seriesResults.forEach(({ leg, data }) => {
            const pts = data.filter(d => d.time <= t);
            if (pts.length) {
              const dir = leg.action === 'BUY' ? 1 : -1;
              total += (pts[pts.length - 1].close - leg.entryPremium) * leg.qty * dir * 0.001;
            }
          });
          return { time: t, pnl: total - entryDeduction };
        });

        const legGreeksMap = new Map<string, LegGreeksPoint[]>();
        seriesResults.forEach(({ leg, allData }) => {
          legGreeksMap.set(leg.id, allData.map(d => ({
            time: d.time, spot: d.spot, iv: d.iv, rv: d.rv,
            delta: d.delta, gamma: d.gamma, theta: d.theta, vega: d.vega,
          })));
        });

        // Capture exit-time data per leg for exit slippage
        const exitData = seriesResults.map(({ leg, data }) => {
          const last = data.length ? data[data.length - 1] : null;
          return last ? { leg, exitTs: last.time, exitMark: last.close, exitSpot: last.spot } : null;
        }).filter((x): x is NonNullable<typeof x> => x !== null);

        // Capture per-leg mark/spot at peak P&L bar
        const maxPoint = points.length ? points.reduce((m, p) => p.pnl > m.pnl ? p : m, points[0]) : null;
        const maxExitData = maxPoint ? seriesResults.map(({ leg, data }) => {
          const bar = data.filter(d => d.time <= maxPoint.time);
          const last = bar.length ? bar[bar.length - 1] : null;
          return last ? { leg, exitTs: last.time, exitMark: last.close, exitSpot: last.spot } : null;
        }).filter((x): x is NonNullable<typeof x> => x !== null) : [];

        map.set(strat.id, points);
        greeksMapOuter.set(strat.id, legGreeksMap);
        exitMapOuter.set(strat.id, exitData);
        maxPnlMapOuter.set(strat.id, maxExitData);
      }));

      setCompareMtmData(map);
      setCompareLegGreeks(greeksMapOuter);
      setCompareExitLegData(exitMapOuter);
      setCompareMaxPnlExitData(maxPnlMapOuter);

      // Fetch ATM IV/RV for each unique expiry across all compare strategies
      const expiries = Array.from(new Set(nonEmpty.flatMap(s => s.legs.map(l => l.expiry))));
      const atmMap = new Map<string, AtmIvPoint[]>();
      await Promise.all(expiries.map(exp =>
        historicalApi.getAtmIvSeries(exp, timeframe, rvWindowDays)
          .then(res => atmMap.set(exp, res.data ?? []))
          .catch(() => atmMap.set(exp, []))
      ));
      setCompareAtmData(atmMap);
    } catch {
      setCompareError('Failed to calculate MTM. Check console.');
    } finally {
      setCompareLoading(false);
    }
  }, [compareStrategies, simulationTimestamp, endDate, endTime, timeframe, rvWindowDays, slipPerSide, brokeragePerSide]);

  // Memoized compare MTM series — PaneSeries format (value = pnl)
  const compareMtmSeries = useMemo((): PaneSeries[] =>
    compareStrategies.filter(s => s.legs.length > 0).map((s, i) => ({
      id: s.id,
      label: s.label,
      color: STRAT_COLORS[i % STRAT_COLORS.length],
      data: (compareMtmData.get(s.id) ?? []).map(p => ({ time: p.time, value: p.pnl })),
    })),
  [compareStrategies, compareMtmData]);

  // Active compare strategy
  const activeCompareStrat = useMemo(() =>
    compareStrategies.find(s => s.id === activeCompareStratId) ?? compareStrategies[0],
  [compareStrategies, activeCompareStratId]);

  // ── Greeks chart series ────────────────────────────────────────────────────
  const buildIvSeries = useMemo((): PaneSeries[] =>
    buildLegGreeks.size === 0 ? [] : legs.map((leg, i) => ({
      id: leg.id,
      label: `${leg.action} ${leg.strike}${leg.type}`,
      color: STRAT_COLORS[i % STRAT_COLORS.length],
      data: (buildLegGreeks.get(leg.id) ?? []).map(p => ({ time: p.time, value: p.iv })),
    })).filter(s => s.data.length > 0),
  [legs, buildLegGreeks]);

  const buildDeltaSeries = useMemo((): PaneSeries[] =>
    buildLegGreeks.size === 0 ? [] : legs.map((leg, i) => {
      const dir = leg.action === 'BUY' ? 1 : -1;
      const scale = leg.qty * 0.001 * dir;
      return {
        id: leg.id,
        label: `${leg.action} ${leg.strike}${leg.type}`,
        color: STRAT_COLORS[i % STRAT_COLORS.length],
        data: (buildLegGreeks.get(leg.id) ?? []).map(p => ({ time: p.time, value: p.delta * scale })),
      };
    }).filter(s => s.data.length > 0),
  [legs, buildLegGreeks]);

  // RV is a property of the underlying — shared across all legs. Take from the first
  // leg with data. Rendered as an extra line in the IV pane.
  const buildRvSeries = useMemo((): PaneSeries | null => {
    if (buildLegGreeks.size === 0) return null;
    for (const leg of legs) {
      const pts = buildLegGreeks.get(leg.id) ?? [];
      if (pts.length) {
        return {
          id: 'rv', label: `BTC RV%`, color: '#f5b14d',
          data: pts.filter(p => p.rv > 0).map(p => ({ time: p.time, value: p.rv })),
        };
      }
    }
    return null;
  }, [legs, buildLegGreeks]);

  // IV − RV spread, per leg. Positive = options expensive vs realized; negative = cheap.
  // Skip points where IV is 0 (no-trade bars) — would otherwise show -RV phantom spikes.
  const buildIvRvSeries = useMemo((): PaneSeries[] =>
    buildLegGreeks.size === 0 ? [] : legs.map((leg, i) => ({
      id: leg.id,
      label: `${leg.action} ${leg.strike}${leg.type}`,
      color: STRAT_COLORS[i % STRAT_COLORS.length],
      data: (buildLegGreeks.get(leg.id) ?? [])
        .filter(p => p.iv > 0 && p.rv > 0)
        .map(p => ({ time: p.time, value: p.iv - p.rv })),
    })).filter(s => s.data.length > 0),
  [legs, buildLegGreeks]);

  // ATM IV (avg of CE+PE at strike closest to spot, per bar) — overlay on IV pane
  const buildAtmIvSeries = useMemo((): PaneSeries | undefined => {
    if (!buildAtmData.length) return undefined;
    return {
      id: 'atm-iv', label: 'ATM IV%', color: '#00d4ff',
      data: buildAtmData.filter(p => p.atm_iv > 0).map(p => ({ time: p.time, value: p.atm_iv })),
    };
  }, [buildAtmData]);

  // ATM IV - RV — overlay on IV-RV pane
  const buildAtmIvRvSeries = useMemo((): PaneSeries | undefined => {
    if (!buildAtmData.length) return undefined;
    const filtered = buildAtmData.filter(p => p.atm_iv > 0 && p.rv > 0);
    if (!filtered.length) return undefined;
    return {
      id: 'atm-iv-rv', label: 'ATM IV − RV%', color: '#00d4ff',
      data: filtered.map(p => ({ time: p.time, value: p.iv_minus_rv })),
    };
  }, [buildAtmData]);

  const compareIvSeries = useMemo((): PaneSeries[] => {
    if (compareLegGreeks.size === 0) return [];
    const result: PaneSeries[] = [];
    let ci = 0;
    compareStrategies.forEach(strat => {
      const legGreeksMap = compareLegGreeks.get(strat.id);
      if (!legGreeksMap) return;
      strat.legs.forEach(leg => {
        const pts = legGreeksMap.get(leg.id) ?? [];
        if (!pts.length) return;
        result.push({
          id: `${strat.id}-${leg.id}`,
          label: `${strat.label}: ${leg.action} ${leg.strike}${leg.type}`,
          color: STRAT_COLORS[ci++ % STRAT_COLORS.length],
          data: pts.map(p => ({ time: p.time, value: p.iv })),
        });
      });
    });
    return result;
  }, [compareStrategies, compareLegGreeks]);

  const compareDeltaSeries = useMemo((): PaneSeries[] => {
    if (compareLegGreeks.size === 0) return [];
    const result: PaneSeries[] = [];
    let ci = 0;
    compareStrategies.forEach(strat => {
      const legGreeksMap = compareLegGreeks.get(strat.id);
      if (!legGreeksMap) return;
      strat.legs.forEach(leg => {
        const pts = legGreeksMap.get(leg.id) ?? [];
        if (!pts.length) return;
        const dir = leg.action === 'BUY' ? 1 : -1;
        const scale = leg.qty * 0.001 * dir;
        result.push({
          id: `${strat.id}-${leg.id}`,
          label: `${strat.label}: ${leg.action} ${leg.strike}${leg.type}`,
          color: STRAT_COLORS[ci++ % STRAT_COLORS.length],
          data: pts.map(p => ({ time: p.time, value: p.delta * scale })),
        });
      });
    });
    return result;
  }, [compareStrategies, compareLegGreeks]);

  const compareRvSeries = useMemo((): PaneSeries | null => {
    if (compareLegGreeks.size === 0) return null;
    for (const strat of compareStrategies) {
      const legGreeksMap = compareLegGreeks.get(strat.id);
      if (!legGreeksMap) continue;
      for (const leg of strat.legs) {
        const pts = legGreeksMap.get(leg.id) ?? [];
        if (pts.length) {
          return {
            id: 'rv', label: 'BTC RV%', color: '#f5b14d',
            data: pts.filter(p => p.rv > 0).map(p => ({ time: p.time, value: p.rv })),
          };
        }
      }
    }
    return null;
  }, [compareStrategies, compareLegGreeks]);

  const compareIvRvSeries = useMemo((): PaneSeries[] => {
    if (compareLegGreeks.size === 0) return [];
    const result: PaneSeries[] = [];
    let ci = 0;
    compareStrategies.forEach(strat => {
      const legGreeksMap = compareLegGreeks.get(strat.id);
      if (!legGreeksMap) return;
      strat.legs.forEach(leg => {
        const pts = legGreeksMap.get(leg.id) ?? [];
        if (!pts.length) return;
        result.push({
          id: `${strat.id}-${leg.id}`,
          label: `${strat.label}: ${leg.action} ${leg.strike}${leg.type}`,
          color: STRAT_COLORS[ci++ % STRAT_COLORS.length],
          data: pts.filter(p => p.iv > 0 && p.rv > 0).map(p => ({ time: p.time, value: p.iv - p.rv })),
        });
      });
    });
    return result;
  }, [compareStrategies, compareLegGreeks]);

  // ATM IV/IV-RV overlays for compare mode (uses selectedExpiry)
  const compareAtmIvSeries = useMemo((): PaneSeries | undefined => {
    const pts = compareAtmData.get(selectedExpiry);
    if (!pts?.length) return undefined;
    const filtered = pts.filter(p => p.atm_iv > 0);
    if (!filtered.length) return undefined;
    return {
      id: 'atm-iv', label: `ATM IV% (${selectedExpiry})`, color: '#00d4ff',
      data: filtered.map(p => ({ time: p.time, value: p.atm_iv })),
    };
  }, [compareAtmData, selectedExpiry]);

  const compareAtmIvRvSeries = useMemo((): PaneSeries | undefined => {
    const pts = compareAtmData.get(selectedExpiry);
    if (!pts?.length) return undefined;
    const filtered = pts.filter(p => p.atm_iv > 0 && p.rv > 0);
    if (!filtered.length) return undefined;
    return {
      id: 'atm-iv-rv', label: `ATM IV − RV% (${selectedExpiry})`, color: '#00d4ff',
      data: filtered.map(p => ({ time: p.time, value: p.iv_minus_rv })),
    };
  }, [compareAtmData, selectedExpiry]);

  // ── Shared chart controls ──────────────────────────────────────────────────
  const chartControls = (
    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
      <div style={{ display: 'flex', gap: '3px' }}>
        {(['1m','5m','15m','30m','1h'] as const).map(tf => (
          <button key={tf} className={`tf-btn${timeframe === tf ? ' active' : ''}`} onClick={() => setTimeframe(tf)}>{tf}</button>
        ))}
      </div>
      <span style={{ fontSize: '11px', color: 'var(--text3)' }}>RV</span>
      <div style={{ display: 'flex', gap: '3px' }}>
        {([7,14,30,60,90] as const).map(d => (
          <button
            key={d}
            className={`tf-btn${rvWindowDays === d ? ' active' : ''}`}
            onClick={() => setRvWindowDays(d)}
            title={`${d}-day realized volatility (daily close-to-close)`}
          >{d}d</button>
        ))}
      </div>
    </div>
  );

  // ── Slippage controls ──────────────────────────────────────────────────────
  const slippageControls = (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center',
      padding: '4px 10px', borderBottom: '1px solid var(--border)',
      background: 'var(--bg1)', fontSize: '11px',
    }}>
      <label style={{ display: 'flex', gap: '4px', alignItems: 'center', cursor: 'pointer', fontWeight: 600 }}>
        <input type="checkbox" checked={slipEnabled} onChange={e => setSlipEnabled(e.target.checked)} />
        <span style={{ color: 'var(--text2)' }}>Apply slippage</span>
      </label>
      <div style={{ display: 'flex', gap: '4px', alignItems: 'center', opacity: slipEnabled ? 1 : 0.4 }}>
        <label style={{ display: 'flex', gap: '3px', alignItems: 'center', cursor: 'pointer' }}>
          <input type="radio" name="slip-mode" checked={slipMode === 'smart'}
                 onChange={() => setSlipMode('smart')} disabled={!slipEnabled} />
          <span>Smart estimate</span>
        </label>
        <label style={{ display: 'flex', gap: '3px', alignItems: 'center', cursor: 'pointer' }}>
          <input type="radio" name="slip-mode" checked={slipMode === 'flat'}
                 onChange={() => setSlipMode('flat')} disabled={!slipEnabled} />
          <span>Flat $</span>
        </label>
        {slipMode === 'flat' && (
          <input type="number" value={slipFlat} step="0.5" min="0"
                 onChange={e => setSlipFlat(Math.max(0, Number(e.target.value) || 0))}
                 disabled={!slipEnabled}
                 style={{ width: '60px', padding: '1px 4px', fontSize: '11px',
                          background: 'var(--bg2)', border: '1px solid var(--border)',
                          color: 'var(--text1)', borderRadius: '3px' }} />
        )}
      </div>
      <span title="Stress scenarios are shown in the MTM Stats sensitivity table"
            style={{ color: 'var(--text3)', fontSize: '10px', marginLeft: 'auto' }}>
        Sensitivity 0.5×–3× → see MTM Stats
      </span>
    </div>
  );

  const brokerageControls = (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center',
      padding: '4px 10px', borderBottom: '1px solid var(--border)',
      background: 'var(--bg1)', fontSize: '11px',
    }}>
      <label style={{ display: 'flex', gap: '4px', alignItems: 'center', cursor: 'pointer', fontWeight: 600 }}>
        <input type="checkbox" checked={brokerageEnabled} onChange={e => setBrokerageEnabled(e.target.checked)} />
        <span style={{ color: 'var(--text2)' }}>Apply brokerage</span>
      </label>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', opacity: brokerageEnabled ? 1 : 0.4 }}>
        {(['standard', 'offer'] as BrokerageRate[]).map(r => (
          <label key={r} style={{ display: 'flex', gap: '3px', alignItems: 'center', cursor: 'pointer' }}>
            <input type="radio" name="brk-rate" checked={brokerageRate === r}
                   onChange={() => setBrokerageRate(r)} disabled={!brokerageEnabled} />
            <span>{BROKERAGE_RATE_LABELS[r].label}</span>
          </label>
        ))}
        <label style={{ display: 'flex', gap: '3px', alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={brokerageReferral} disabled={!brokerageEnabled}
                 onChange={e => setBrokerageReferral(e.target.checked)} />
          <span>Referral −10%</span>
        </label>
      </div>
      <span style={{ color: 'var(--text3)', fontSize: '10px', marginLeft: 'auto' }}>
        Taker fee + 18% GST
      </span>
    </div>
  );

  const endPicker = (
    <div className="mtm-range-row">
      <span className="mtm-range-label">Show until</span>
      <input type="date" className="mtm-range-input" value={endDate}
        min={minEntryTs ? new Date(minEntryTs * 1000).toISOString().split('T')[0] : undefined}
        max={selectedExpiry} onChange={e => setEndDate(e.target.value)} />
      <input type="time" className="mtm-range-input" value={endTime} onChange={e => setEndTime(e.target.value)} />
    </div>
  );

  // ── Legs table (reusable) ──────────────────────────────────────────────────
  const renderLegsTable = (
    legsToShow: StrategyLeg[],
    onRemove: (legId: string) => void,
    onQty: (legId: string, qty: number) => void,
  ) => (
    <div className="strategy-table-wrap">
      <table className="strategy-table">
        <thead>
          <tr>
            <th>Action</th>
            <th>Expiry</th>
            <th>Strike</th>
            <th>Type</th>
            <th title="1 lot = 0.001 BTC">Lots</th>
            <th>BTC</th>
            <th>Entry</th>
            <th>Current</th>
            <th>P&amp;L</th>
            <th title="Estimated per-side slippage in USD for this leg (round-trip cost = 2× this). Net entry slip = sum of these.">Slip $</th>
            <th title="Implied Volatility %">IV%</th>
            <th title="Net Delta">Delta</th>
            <th title="Net Gamma">Gamma</th>
            <th title="Net Theta">Theta</th>
            <th title="Net Vega">Vega</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {legsToShow.map(leg => {
            const curr = getCurrentPrice(leg);
            const pnl = getLegPnl(leg);
            const btcSize = leg.qty * 0.001;
            const greeks = getLegGreeks(leg);
            return (
              <tr key={leg.id}>
                <td><span className={`strategy-badge ${leg.action === 'BUY' ? 'buy' : 'sell'}`}>{leg.action}</span></td>
                <td style={{ color: leg.expiry === selectedExpiry ? 'var(--accent)' : 'var(--text3)', fontSize: '10px', whiteSpace: 'nowrap' }}>{leg.expiry}</td>
                <td style={{ fontWeight: 700 }}>{leg.strike.toLocaleString()}</td>
                <td><span className={`strategy-badge ${leg.type === 'CE' ? 'ce' : 'pe'}`}>{leg.type}</span></td>
                <td style={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                  <LegQtyInput id={leg.id} qty={leg.qty} onUpdate={onQty} />
                </td>
                <td style={{ color: 'var(--text3)', fontSize: '11px' }}>{btcSize < 0.01 ? btcSize.toFixed(3) : btcSize.toFixed(2)}</td>
                <td style={{ color: 'var(--text2)' }}>{leg.entryPremium.toFixed(2)}</td>
                <td>{curr.toFixed(2)}</td>
                <td style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                  {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                </td>
                <td style={{ color: 'var(--text3)', fontSize: '11px' }}>
                  {slipEnabled ? (slipPerSide(leg) * leg.qty * 0.001).toFixed(2) : '-'}
                </td>
                <td style={{ color: 'var(--gold)' }}>{greeks ? greeks.iv_pct.toFixed(1) : '-'}</td>
                <td style={{ color: greeks && greeks.delta >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {greeks ? (greeks.delta >= 0 ? '+' : '') + greeks.delta.toFixed(4) : '-'}
                </td>
                <td style={{ color: greeks && greeks.gamma >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {greeks ? (greeks.gamma >= 0 ? '+' : '') + greeks.gamma.toFixed(5) : '-'}
                </td>
                <td style={{ color: greeks && greeks.theta < 0 ? 'var(--red)' : 'var(--green)' }}>
                  {greeks ? (greeks.theta >= 0 ? '+' : '') + greeks.theta.toFixed(2) : '-'}
                </td>
                <td style={{ color: greeks && greeks.vega >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {greeks ? (greeks.vega >= 0 ? '+' : '') + greeks.vega.toFixed(2) : '-'}
                </td>
                <td>
                  <button className="strategy-btn-remove" onClick={() => onRemove(leg.id)}>×</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  // ── Download (compare mode) ────────────────────────────────────────────────
  const downloadCompareExcel = () => {
    const wb = XLSX.utils.book_new();

    // Sheet 1: Strategy Legs — all strategies, entry details + current Greeks
    const legsRows: Record<string, unknown>[] = [];
    compareStrategies.forEach(strat => {
      strat.legs.forEach(leg => {
        const g = getLegGreeks(leg);
        const slip = slipPerSide(leg);
        legsRows.push({
          'Strategy':      strat.label,
          'Action':        leg.action,
          'Expiry':        leg.expiry,
          'Strike':        leg.strike,
          'Type':          leg.type,
          'Lots':          leg.qty,
          'BTC Size':      +(leg.qty * 0.001).toFixed(3),
          'Entry Time (IST)': toIst(leg.entryTimestamp),
          'Entry Price':   +leg.entryPremium.toFixed(4),
          'Est. Slippage Per Side ($/contract)': slipEnabled ? +slip.toFixed(4) : 0,
          'Round-trip Slippage Cost (USD)': slipEnabled ? +(2 * slip * leg.qty * 0.001).toFixed(4) : 0,
          'IV%':           g ? +g.iv_pct.toFixed(2)  : '',
          'Delta':         g ? +g.delta.toFixed(4)   : '',
          'Gamma':         g ? +g.gamma.toFixed(5)   : '',
          'Theta':         g ? +g.theta.toFixed(2)   : '',
          'Vega':          g ? +g.vega.toFixed(2)    : '',
        });
      });
      legsRows.push({} as Record<string, unknown>); // blank row between strategies
    });
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(legsRows), 'Strategy Legs');

    // Sheet 1b: Margin Required — one row per strategy, computed from each
    // strategy's own legs against the current spot/sim time.
    if (spot > 0 && simulationTimestamp) {
      const marginRows: Record<string, unknown>[] = [];
      compareStrategies.forEach(strat => {
        if (!strat.legs.length) return;
        const { marginLegs, skippedLegs } = buildMarginLegs(
          strat.legs, chainsByExpiry, spot, simulationTimestamp,
        );
        if (!marginLegs.length) return;
        const m = computePortfolioMargin(marginLegs, spot, undefined, skippedLegs);
        if (!m) return;
        marginRows.push({
          Strategy: strat.label,
          'Portfolio Margin (USDT)':       +m.portfolioMargin.toFixed(2),
          'Maintenance Margin (USDT)':     +m.maintenanceMargin.toFixed(2),
          'Margin per Lot (USDT)':         +m.marginPerLot.toFixed(4),
          'Risk Margin (USDT)':            +m.riskMargin.toFixed(2),
          'Margin Floor (USDT)':           +m.marginFloor.toFixed(2),
          'Binding Constraint':            m.bindingConstraint,
          'Total Notional (USDT)':         +m.totalNotional.toFixed(2),
          'Short Notional (USDT)':         +m.shortOptionsNotional.toFixed(2),
          'Premium Collected (USDT)':      +m.totalPremiumCollected.toFixed(2),
          'Effective Leverage':            +m.effectiveLeverage.toFixed(2),
          'Net Delta (BTC)':               +m.netDeltaBtc.toFixed(4),
          'Worst Scenario PnL (USDT)':     +m.worstScenario.pnl.toFixed(2),
          'Worst Scenario Spot Shock':     m.worstScenario.priceShockPct.toFixed(2) + '%',
          'Worst Scenario Vol Shock (pts)':+m.worstScenario.volShockPts.toFixed(2),
          'Skipped Legs (no IV)':          m.skippedLegs,
        });
      });
      if (marginRows.length) {
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(marginRows), 'Margin Required');
      }
    }

    // Sheet 2: MTM Comparison — all timestamps as rows, each strategy as a column
    if (compareMtmData.size > 0) {
      const timeSet = new Set<number>();
      compareMtmData.forEach(pts => pts.forEach(p => timeSet.add(p.time)));
      const sortedTimes = Array.from(timeSet).sort((a, b) => a - b);
      const stratsWithData = compareStrategies.filter(s => (compareMtmData.get(s.id) ?? []).length > 0);

      // Build a quick lookup Map per strategy for O(1) access
      const lookup = new Map<string, Map<number, number>>();
      stratsWithData.forEach(s => {
        const m = new Map<number, number>();
        (compareMtmData.get(s.id) ?? []).forEach(p => m.set(p.time, p.pnl));
        lookup.set(s.id, m);
      });

      const mtmRows = sortedTimes.map(t => {
        const row: Record<string, unknown> = { 'Time (IST)': toIst(t) };
        stratsWithData.forEach(s => {
          const val = lookup.get(s.id)?.get(t);
          row[`${s.label} P&L (USD)`] = val !== undefined ? +val.toFixed(4) : '';
        });
        return row;
      });
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(mtmRows), 'MTM Comparison');
    }

    // Sheet 3: MTM Stats — per strategy summary + drawdown table
    if (compareMtmData.size > 0) {
      const statsRows: Record<string, unknown>[] = [];
      compareStrategies.forEach(s => {
        const pts = compareMtmData.get(s.id);
        if (!pts?.length) return;
        const st = computeMtmStats(pts);
        if (!st) return;
        statsRows.push(...statsToRows(st, s.label));
      });
      if (statsRows.length) {
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(statsRows), 'MTM Stats');
      }
    }

    // Sheet 3b: Slippage Sensitivity — Final P&L per strategy at multipliers
    if (slipEnabled && compareMtmData.size > 0) {
      const sensRows: Record<string, unknown>[] = [];
      const multipliers = [0.5, 1.0, 1.5, 2.0, 3.0];
      compareStrategies.forEach(strat => {
        const pts = compareMtmData.get(strat.id);
        if (!pts?.length) return;
        const st = computeMtmStats(pts);
        if (!st) return;
        const exitLegs = compareExitLegData.get(strat.id) ?? [];
        const baseEntry = strat.legs.reduce((acc, l) => acc + slipPerSide(l) * l.qty * 0.001, 0);
        const grossFinal = st.finalPnl + baseEntry;
        const row: Record<string, unknown> = { 'Strategy': strat.label };
        multipliers.forEach(m => {
          const eSlip = strat.legs.reduce((acc, l) => acc + slipPerSide(l, m) * l.qty * 0.001, 0);
          const xSlip = exitLegs.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
            acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot, m) * leg.qty * 0.001, 0);
          row[`Final P&L @ ${m.toFixed(1)}×`] = +(grossFinal - eSlip - xSlip).toFixed(4);
        });
        sensRows.push(row);
      });
      if (sensRows.length) {
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(sensRows), 'Slippage Sensitivity');
      }
    }

    // Sheet 3c: Exit & Peak Marks — per-leg mark/P&L at exit AND at peak-strategy-MTM
    if (compareExitLegData.size > 0 || compareMaxPnlExitData.size > 0) {
      const exitPeakRows: Record<string, unknown>[] = [];
      compareStrategies.forEach(strat => {
        const exitLegs = compareExitLegData.get(strat.id) ?? [];
        const peakLegs = compareMaxPnlExitData.get(strat.id) ?? [];
        if (!exitLegs.length && !peakLegs.length) return;
        strat.legs.forEach(leg => {
          const dir = leg.action === 'BUY' ? 1 : -1;
          const exitD = exitLegs.find(e => e.leg.id === leg.id);
          const peakD = peakLegs.find(e => e.leg.id === leg.id);
          const exitPnl = exitD ? (exitD.exitMark - leg.entryPremium) * leg.qty * dir * 0.001 : null;
          const peakPnl = peakD ? (peakD.exitMark - leg.entryPremium) * leg.qty * dir * 0.001 : null;
          exitPeakRows.push({
            'Strategy':               strat.label,
            'Leg':                    `${leg.action} ${leg.strike}${leg.type}`,
            'Action':                 leg.action,
            'Type':                   leg.type,
            'Strike':                 leg.strike,
            'Lots':                   leg.qty,
            'Entry Time (IST)':       toIst(leg.entryTimestamp),
            'Entry Mark':             +leg.entryPremium.toFixed(4),
            'Exit Time (IST)':        exitD ? toIst(exitD.exitTs) : '',
            'Exit Mark':              exitD ? +exitD.exitMark.toFixed(4) : '',
            'Exit Spot':              exitD ? +exitD.exitSpot.toFixed(2) : '',
            'Exit Leg P&L (USD)':     exitPnl !== null ? +exitPnl.toFixed(4) : '',
            'Peak-MTM Time (IST)':    peakD ? toIst(peakD.exitTs) : '',
            'Peak-MTM Mark':          peakD ? +peakD.exitMark.toFixed(4) : '',
            'Peak-MTM Spot':          peakD ? +peakD.exitSpot.toFixed(2) : '',
            'Peak-MTM Leg P&L (USD)': peakPnl !== null ? +peakPnl.toFixed(4) : '',
          });
        });
        exitPeakRows.push({} as Record<string, unknown>); // blank row between strategies
      });
      if (exitPeakRows.length) {
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(exitPeakRows), 'Exit & Peak Marks');
      }
    }

    // Sheets 4+: per-strategy MTM + per-leg Greeks
    if (compareLegGreeks.size > 0) {
      const legLabel = (leg: StrategyLeg) => `${leg.action} ${leg.strike}${leg.type}`;
      compareStrategies.forEach(strat => {
        const pts = compareMtmData.get(strat.id);
        const legGreeksMap = compareLegGreeks.get(strat.id);
        if (!pts?.length || !legGreeksMap) return;

        // Build per-leg lookup
        const legLookup = new Map<string, Map<number, LegGreeksPoint>>();
        strat.legs.forEach(leg => {
          const legPts = legGreeksMap.get(leg.id) ?? [];
          const m = new Map<number, LegGreeksPoint>();
          legPts.forEach(p => m.set(p.time, p));
          legLookup.set(leg.id, m);
        });

        const mtmRows = pts.map(d => {
          const row: Record<string, unknown> = { 'Time (IST)': toIst(d.time) };
          let spot = 0, rv = 0;
          for (const leg of strat.legs) {
            const pt = legLookup.get(leg.id)?.get(d.time);
            if (pt && pt.spot > 0) { spot = pt.spot; rv = pt.rv; break; }
          }
          row['BTC Spot'] = spot > 0 ? +spot.toFixed(2) : '';
          row['RV%']      = rv > 0   ? +rv.toFixed(2)   : '';
          let netDelta = 0, netGamma = 0, netTheta = 0, netVega = 0;
          strat.legs.forEach(leg => {
            const label = legLabel(leg);
            const pt = legLookup.get(leg.id)?.get(d.time);
            const dir = leg.action === 'BUY' ? 1 : -1;
            const scale = leg.qty * 0.001 * dir;
            if (pt) {
              row[`${label} IV%`]    = +pt.iv.toFixed(2);
              row[`${label} IV-RV%`] = pt.iv > 0 ? +(pt.iv - pt.rv).toFixed(2) : '';
              row[`${label} Delta`]  = +(pt.delta * scale).toFixed(4);
              row[`${label} Gamma`]  = +(pt.gamma * scale).toFixed(6);
              row[`${label} Theta`]  = +(pt.theta * scale).toFixed(4);
              row[`${label} Vega`]   = +(pt.vega  * scale).toFixed(4);
              netDelta += pt.delta * scale;
              netGamma += pt.gamma * scale;
              netTheta += pt.theta * scale;
              netVega  += pt.vega  * scale;
            } else {
              row[`${label} IV%`] = ''; row[`${label} IV-RV%`] = '';
              row[`${label} Delta`] = '';
              row[`${label} Gamma`] = ''; row[`${label} Theta`] = ''; row[`${label} Vega`] = '';
            }
          });
          row['Net Delta'] = +netDelta.toFixed(4);
          row['Net Gamma'] = +netGamma.toFixed(6);
          row['Net Theta'] = +netTheta.toFixed(4);
          row['Net Vega']  = +netVega.toFixed(4);
          row['P&L (USD)'] = +d.pnl.toFixed(4);
          return row;
        });
        // Sheet name max 31 chars
        const sheetName = `${strat.label} MTM`.slice(0, 31);
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(mtmRows), sheetName);
      });
    }

    XLSX.writeFile(wb, `compare_mtm_${selectedExpiry}.xlsx`);
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // COMPARE MODE
  // ═══════════════════════════════════════════════════════════════════════════
  if (compareMode) {
    const hasAnyLegs = compareStrategies.some(s => s.legs.length > 0);
    const activeLegs = activeCompareStrat?.legs ?? [];

    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflowY: 'auto' }}>

        {/* ── Compare header ── */}
        <div className="strategy-header" style={{ borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              className="strategy-btn-secondary"
              onClick={onExitCompare}
              style={{ fontSize: '11px' }}
            >
              ← Back
            </button>
            <span className="strategy-title" style={{ color: 'var(--accent)' }}>Strategy Compare</span>
            <button
              className="strategy-btn-secondary"
              onClick={onToggleMaximize}
              title={maximized ? 'Restore' : 'Maximise'}
              style={{ fontSize: '13px', padding: '1px 6px', lineHeight: 1 }}
            >
              {maximized ? '⊡' : '⊞'}
            </button>
            <span
              className="mtm-stats-toggle"
              onClick={() => setCompareLegsExpanded(e => !e)}
              style={{ cursor: 'pointer' }}
              title={compareLegsExpanded ? 'Collapse legs' : 'Expand legs'}
            >
              {compareLegsExpanded ? '▲' : '▼'} {activeLegs.length} leg{activeLegs.length !== 1 ? 's' : ''}
            </span>
          </div>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            <button
              className={`strategy-btn-run${compareLoading ? ' loading' : ''}`}
              onClick={runCompareMtm}
              disabled={compareLoading || !hasAnyLegs}
            >
              {compareLoading ? 'Calculating…' : 'Run MTM ▶'}
            </button>
            <button
              className="strategy-btn-secondary"
              disabled={!hasAnyLegs}
              onClick={downloadCompareExcel}
              title="Download strategy legs + MTM comparison"
            >
              Download ↓
            </button>
          </div>
        </div>

        {/* ── Strategy tabs ── */}
        {!chartsOnly && <div className="strat-tabs">
          {compareStrategies.map((s, i) => (
            <button
              key={s.id}
              className={`strat-tab${s.id === activeCompareStratId ? ' active' : ''}`}
              onClick={() => onSetActiveCompareStrat(s.id)}
            >
              <span className="strat-tab-dot" style={{ background: STRAT_COLORS[i % STRAT_COLORS.length] }} />
              {s.label}
              {s.legs.length > 0 && <span className="strategy-leg-count">{s.legs.length}</span>}
              {compareStrategies.length > 1 && (
                <span
                  className="strat-tab-close"
                  onClick={e => { e.stopPropagation(); onRemoveCompareStrategy(s.id); }}
                >×</span>
              )}
            </button>
          ))}
          <button className="strat-tab-add" onClick={onAddCompareStrategy}>＋</button>
        </div>}

        {/* ── Active strategy legs ── */}
        {!chartsOnly && compareLegsExpanded && (
          <div className="strategy-legs-card">
            <div className="strategy-header">
              <span className="strategy-title" style={{ color: STRAT_COLORS[(compareStrategies.findIndex(s => s.id === activeCompareStratId)) % STRAT_COLORS.length] }}>
                {activeCompareStrat?.label}
              </span>
              {activeLegs.length > 0 && (
                <button className="strategy-btn-secondary" onClick={() => onClearCompareLegs(activeCompareStratId)}>Clear</button>
              )}
            </div>
            {activeLegs.length > 0 && slippageControls}
            {activeLegs.length > 0 && brokerageControls}
            {activeLegs.length === 0 ? (
              <div className="strategy-empty">
                Click <span className="strategy-badge buy">B</span> or <span className="strategy-badge sell">S</span> on any strike to add a leg to {activeCompareStrat?.label}
              </div>
            ) : (
              renderLegsTable(
                activeLegs,
                legId => onRemoveCompareLeg(activeCompareStratId, legId),
                (legId, qty) => onUpdateCompareLegQty(activeCompareStratId, legId, qty),
              )
            )}
            {compareError && <div style={{ color: 'var(--red)', fontSize: '11px', padding: '4px 10px' }}>{compareError}</div>}
          </div>
        )}

        {/* Slippage controls visible when maximized (legs panel collapsed) */}
        {!chartsOnly && maximized && activeLegs.length > 0 && slippageControls}
        {!chartsOnly && maximized && activeLegs.length > 0 && brokerageControls}

        {/* ── Compare MTM + Greeks chart (multi-pane) ── */}
        <div className="strategy-chart-card" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
          {!chartsOnly && (
            <>
              <div className="strategy-chart-header">
                <span className="strategy-chart-label">Strategy Comparison</span>
                {chartControls}
              </div>
              {endPicker}
            </>
          )}

          {compareMtmData.size > 0 && compareMtmSeries.some(s => s.data.length > 0) ? (
            <>
              {/* Strategy colour legend */}
              {!chartsOnly && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', padding: '4px 10px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
                  {compareStrategies.filter(s => s.legs.length > 0).map((s, i) => (
                    <span key={s.id} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px' }}>
                      <span style={{ width: '10px', height: '2px', background: STRAT_COLORS[i % STRAT_COLORS.length], display: 'inline-block', borderRadius: '1px' }} />
                      <span style={{ color: 'var(--text2)' }}>{s.label} ({s.legs.length} legs)</span>
                    </span>
                  ))}
                </div>
              )}
              {/* Single chart: MTM (multi-line) + IV + Delta — all time-scale synced */}
              <MultiPaneChart
                mtmSeries={compareMtmSeries}
                ivSeries={compareIvSeries}
                rvSeries={compareRvSeries ?? undefined}
                atmIvSeries={compareAtmIvSeries}
                ivRvSeries={compareIvRvSeries}
                atmIvRvSeries={compareAtmIvRvSeries}
                deltaSeries={compareDeltaSeries}
                chartsOnly={chartsOnly}
                onToggleChartsOnly={onToggleChartsOnly}
              />
              {!chartsOnly && (
                <div style={{ flexShrink: 0, overflowY: 'auto' }}>
                  <div className="compare-stats-section">
                    {compareStrategies.filter(s => s.legs.length > 0).map((s, i) => {
                      const pts = compareMtmData.get(s.id);
                      if (!pts?.length) return null;
                      const stats = computeMtmStats(pts);
                      if (!stats) return null;
                      const exitLegs  = compareExitLegData.get(s.id) ?? [];
                      const slipEntry = s.legs.reduce((acc, l) => acc + slipPerSide(l) * l.qty * 0.001, 0);
                      const slipExit  = exitLegs.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
                        acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot) * leg.qty * 0.001, 0);
                      const brkEntry    = s.legs.reduce((acc, l) => acc + brokeragePerSide(l), 0);
                      const brkExit     = exitLegs.reduce((acc, { leg, exitMark, exitSpot }) =>
                        acc + brokeragePerSideAt(leg, exitMark, exitSpot), 0);
                      const grossFinal  = stats.finalPnl + slipEntry;
                      const maxPnlLegs  = compareMaxPnlExitData.get(s.id) ?? [];
                      const peakExitSlip = maxPnlLegs.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
                        acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot) * leg.qty * 0.001, 0);
                      const peakExitBrk  = maxPnlLegs.reduce((acc, { leg, exitMark, exitSpot }) =>
                        acc + brokeragePerSideAt(leg, exitMark, exitSpot), 0);
                      const maxExitPnl  = maxPnlLegs.length > 0
                        ? stats.maxPnl - peakExitSlip - peakExitBrk
                        : undefined;
                      const sens = [0.5, 1.0, 1.5, 2.0, 3.0].map(m => {
                        const eSlip = s.legs.reduce((acc, l) => acc + slipPerSide(l, m) * l.qty * 0.001, 0);
                        const xSlip = exitLegs.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
                          acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot, m) * leg.qty * 0.001, 0);
                        return { mult: m, finalPnl: grossFinal - eSlip - xSlip - brkEntry - brkExit };
                      });
                      return (
                        <MtmStatsPanel
                          key={s.id} stats={stats} color={STRAT_COLORS[i % STRAT_COLORS.length]}
                          slipEntryUsd={slipEntry} slipExitUsd={slipExit}
                          brokerageEntryUsd={brkEntry} brokerageExitUsd={brkExit}
                          maxExitPnl={maxExitPnl} sensitivity={sens}
                        />
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="strategy-chart-empty">
              {hasAnyLegs
                ? 'Click Run MTM ▶ to compare strategies'
                : 'Add legs to strategies first'}
            </div>
          )}
        </div>

        {/* ── Portfolio Margin (per strategy) ── */}
        {!chartsOnly && hasAnyLegs && compareStrategies.some(s => s.legs.length > 0) && (
          <>
            {compareStrategies.filter(s => s.legs.length > 0).map((s, i) => {
              const m = compareMarginResults.get(s.id);
              const isExpanded = compareMarginExpanded.has(s.id);
              const toggleExpanded = () => {
                if (!m) return;
                setCompareMarginExpanded(prev => {
                  const next = new Set(prev);
                  if (next.has(s.id)) next.delete(s.id); else next.add(s.id);
                  return next;
                });
              };
              const stratColor = STRAT_COLORS[i % STRAT_COLORS.length];
              return (
                <div key={s.id} className="margin-card">
                  <div className="margin-compact-row" onClick={toggleExpanded}>
                    <span className="strat-tab-dot" style={{ background: stratColor, marginRight: 6 }} />
                    <span style={{ fontSize: '11px', fontWeight: 600, color: stratColor, marginRight: 8 }}>{s.label}</span>
                    {m ? (
                      <>
                        <div className="margin-kv">
                          <span className="margin-label">Margin Req.</span>
                          <span className="margin-val">{fmt(m.portfolioMargin)} USDT</span>
                        </div>
                        <div className="margin-kv">
                          <span className="margin-label">Leverage</span>
                          <span className="margin-val" style={{ color: leverageColor(m.effectiveLeverage) }}>
                            {m.effectiveLeverage.toFixed(1)}×
                          </span>
                        </div>
                        <div className="margin-kv">
                          <span className="margin-label">Net Δ</span>
                          <span className="margin-val" style={{ color: m.netDeltaBtc >= 0 ? 'var(--green)' : 'var(--red)' }}>
                            {m.netDeltaBtc >= 0 ? '+' : ''}{m.netDeltaBtc.toFixed(4)} BTC
                          </span>
                        </div>
                        <span className={`margin-binding-badge ${m.bindingConstraint}`}>
                          {m.bindingConstraint === 'risk_margin' ? 'Risk Margin' : 'Margin Floor'}
                        </span>
                        <span className="margin-expand-toggle">{isExpanded ? '▲' : '▼'}</span>
                      </>
                    ) : (
                      <span style={{ fontSize: '11px', color: 'var(--text3)' }}>Margin: N/A — mark price unavailable at this timestamp</span>
                    )}
                  </div>
                  {m && m.skippedLegs > 0 && (
                    <div className="margin-skip-warning">
                      ⚠ {m.skippedLegs} leg{m.skippedLegs > 1 ? 's' : ''} excluded — mark price is 0 at this timestamp
                    </div>
                  )}
                  {m && isExpanded && (
                    <div className="margin-detail">
                      <div className="margin-detail-grid">
                        <div className="margin-detail-row"><span className="margin-detail-label">Risk Margin</span><span className="margin-detail-val">{fmt(m.riskMargin)} USDT</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">Margin Floor</span><span className="margin-detail-val">{fmt(m.marginFloor)} USDT</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">Pre-scale <span style={{ color: 'var(--text3)', fontSize: '10px' }}>max(Risk, Floor)</span></span><span className="margin-detail-val">{fmt(m.preScaleMargin)} USDT</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">DTE scale <span style={{ color: 'var(--text3)', fontSize: '10px' }}>(0.49 / DTE)^0.234</span></span><span className="margin-detail-val">×{m.dteScaleApplied.toFixed(3)}</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">Portfolio Margin <span style={{ color: 'var(--text3)', fontSize: '10px' }}>pre-scale × DTE scale</span></span><span className="margin-detail-val">{fmt(m.portfolioMargin)} USDT</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">UCF <span style={{ color: 'var(--text3)', fontSize: '10px' }}>signed expected payoff</span></span><span className="margin-detail-val" style={{ color: m.ucf >= 0 ? 'var(--green)' : 'var(--red)' }}>{m.ucf >= 0 ? '+' : ''}{fmt(m.ucf)} USDT</span></div>
                        <div className="margin-detail-row">
                          <span className="margin-detail-label">Total Notional</span>
                          <span className="margin-detail-val">{fmt(m.totalNotional)} USDT <span style={{ color: 'var(--text3)', fontSize: '10px' }}>({(m.totalNotional / spot).toFixed(3)} BTC)</span></span>
                        </div>
                        <div className="margin-detail-row"><span className="margin-detail-label">Premium Collected</span><span className="margin-detail-val" style={{ color: 'var(--green)' }}>{fmt(m.totalPremiumCollected)} USDT</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">Margin / Notional</span><span className="margin-detail-val">{((m.initialMargin / m.totalNotional) * 100).toFixed(2)}%</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">Margin per Lot</span><span className="margin-detail-val">{fmt(m.marginPerLot)} USDT</span></div>
                        <div className="margin-detail-row"><span className="margin-detail-label">Maintenance Margin</span><span className="margin-detail-val">{fmt(m.maintenanceMargin)} USDT</span></div>
                        <div className="margin-detail-row">
                          <span className="margin-detail-label">Floor Rate (OM%)</span>
                          <span className="margin-detail-val">{(m.omPctApplied * 100).toFixed(3)}% <span style={{ color: 'var(--text3)', fontSize: '10px' }}>on {fmt(m.shortOptionsNotional)} short</span></span>
                        </div>
                      </div>
                      <div className="margin-scenarios">
                        <div className="margin-scenario-row worst">
                          <span className="margin-scenario-icon">▼</span><span className="margin-scenario-label">Worst</span>
                          <span className="margin-scenario-desc">Price {m.worstScenario.priceShockPct >= 0 ? '+' : ''}{m.worstScenario.priceShockPct.toFixed(1)}% · Vol {m.worstScenario.volShockPts >= 0 ? '+' : ''}{m.worstScenario.volShockPts.toFixed(0)}pp</span>
                          <span className="margin-scenario-pnl" style={{ color: 'var(--red)' }}>−{fmt(Math.abs(m.worstScenario.pnl))} USDT</span>
                        </div>
                        <div className="margin-scenario-row best">
                          <span className="margin-scenario-icon">▲</span><span className="margin-scenario-label">Best</span>
                          <span className="margin-scenario-desc">Price {m.bestScenario.priceShockPct >= 0 ? '+' : ''}{m.bestScenario.priceShockPct.toFixed(1)}% · Vol {m.bestScenario.volShockPts >= 0 ? '+' : ''}{m.bestScenario.volShockPts.toFixed(0)}pp</span>
                          <span className="margin-scenario-pnl" style={{ color: 'var(--green)' }}>+{fmt(m.bestScenario.pnl)} USDT</span>
                        </div>
                      </div>
                      <div className="margin-tier-info">
                        Stress: ±{(m.priceShockApplied * 100).toFixed(1)}% price · IV +{(m.volUpApplied * 100).toFixed(1)}% / −{(m.volDownApplied * 100).toFixed(1)}% @ {m.minDteDaysApplied.toFixed(1)} DTE
                        <span className="margin-disclaimer-icon" title="Matches Delta Exchange India official portfolio-margin methodology (29-scenario stress-test, continuous spans, DTE-adjusted IV shocks).">&nbsp;ⓘ</span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </>
        )}
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // BUILD MODE
  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: 0, overflowY: 'auto' }}>

      {/* ── Legs card ── */}
      {!chartsOnly && <div className="strategy-legs-card">
        <div className="strategy-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span className="strategy-title">Strategy Builder</span>
            <button
              className="strategy-btn-secondary"
              onClick={onToggleMaximize}
              title={maximized ? 'Restore' : 'Maximise'}
              style={{ fontSize: '13px', padding: '1px 6px', lineHeight: 1 }}
            >
              {maximized ? '⊡' : '⊞'}
            </button>
            <span
              className="mtm-stats-toggle"
              onClick={() => setLegsExpanded(e => !e)}
              style={{ cursor: 'pointer' }}
              title={legsExpanded ? 'Collapse legs' : 'Expand legs'}
            >
              {legsExpanded ? '▲' : '▼'} {legs.length} leg{legs.length !== 1 ? 's' : ''}
            </span>
          </div>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            {legs.length > 0 && (
              <span style={{ fontSize: '12px', fontWeight: 700, color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USD
              </span>
            )}
            {legs.length > 0 && slipEnabled && (() => {
              const cost = legs.reduce((s, l) => s + slipRoundTripUsd(l), 0);
              return cost > 0 ? (
                <span title="Round-trip slippage cost across all legs (already deducted from P&L)"
                      style={{ fontSize: '11px', color: 'var(--text3)' }}>
                  slip −{cost.toFixed(2)}
                </span>
              ) : null;
            })()}
            {legs.length > 0 && (
              <button className="strategy-btn-secondary" onClick={onClearLegs}>Clear</button>
            )}
            <button
              className="strategy-btn-secondary"
              onClick={onEnterCompare}
              title="Compare multiple strategies"
            >
              Compare ⊞
            </button>
            <button
              className={`strategy-btn-run${buildLoading ? ' loading' : ''}`}
              onClick={runBuildMtm}
              disabled={buildLoading || !legs.length}
            >
              {buildLoading ? 'Calculating…' : 'Run MTM ▶'}
            </button>
            <button className="strategy-btn-secondary" disabled={!legs.length} onClick={downloadExcel}>
              Download ↓
            </button>
          </div>
        </div>

        {legs.length > 0 && (legsExpanded || maximized) && slippageControls}
        {legs.length > 0 && (legsExpanded || maximized) && brokerageControls}

        {legsExpanded && (legs.length === 0 ? (
          <div className="strategy-empty">
            Click <span className="strategy-badge buy">B</span> or <span className="strategy-badge sell">S</span> on any strike to add a leg
          </div>
        ) : (
          renderLegsTable(legs, onRemoveLeg, onUpdateQty)
        ))}

        {/* ── Net portfolio Greeks summary ── */}
        {legsExpanded && legs.length > 0 && netGreeks && (
          <div className="strategy-net-greeks">
            <span className="net-greeks-label">Portfolio</span>
            <div className="net-greeks-stat">
              <span className="net-greeks-key">P&amp;L</span>
              <span className="net-greeks-val" style={{ color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USD
              </span>
            </div>
            {slipEnabled && (() => {
              const cost = legs.reduce((s, l) => s + slipRoundTripUsd(l), 0);
              return cost > 0 ? (
                <div className="net-greeks-stat" title="Round-trip slippage cost (entry + exit) — already deducted from P&L">
                  <span className="net-greeks-key">Slip</span>
                  <span className="net-greeks-val" style={{ color: 'var(--text3)' }}>
                    −{cost.toFixed(2)} USD
                  </span>
                </div>
              ) : null;
            })()}
            <div className="net-greeks-divider" />
            <div className="net-greeks-stat">
              <span className="net-greeks-key">Net Δ</span>
              <span className="net-greeks-val" style={{ color: netGreeks.delta >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {netGreeks.delta >= 0 ? '+' : ''}{netGreeks.delta.toFixed(4)}
              </span>
            </div>
            <div className="net-greeks-stat">
              <span className="net-greeks-key">Net Γ</span>
              <span className="net-greeks-val" style={{ color: netGreeks.gamma >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {netGreeks.gamma >= 0 ? '+' : ''}{netGreeks.gamma.toFixed(5)}
              </span>
            </div>
            <div className="net-greeks-stat">
              <span className="net-greeks-key">Net θ</span>
              <span className="net-greeks-val" style={{ color: netGreeks.theta < 0 ? 'var(--red)' : 'var(--green)' }}>
                {netGreeks.theta >= 0 ? '+' : ''}{netGreeks.theta.toFixed(2)}
              </span>
            </div>
            <div className="net-greeks-stat">
              <span className="net-greeks-key">Net ν</span>
              <span className="net-greeks-val" style={{ color: netGreeks.vega >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {netGreeks.vega >= 0 ? '+' : ''}{netGreeks.vega.toFixed(2)}
              </span>
            </div>
          </div>
        )}

        {buildError && <div style={{ color: 'var(--red)', fontSize: '11px', padding: '4px 10px' }}>{buildError}</div>}
      </div>}

      {/* ── Portfolio Margin ── */}
      {!chartsOnly && legs.length > 0 && (
        <div className="margin-card">
          <div className="margin-compact-row" onClick={() => marginResult && setMarginExpanded(e => !e)}>
            {marginResult ? (
              <>
                <div className="margin-kv">
                  <span className="margin-label">Margin Req.</span>
                  <span className="margin-val">{fmt(marginResult.portfolioMargin)} USDT</span>
                </div>
                <div className="margin-kv">
                  <span className="margin-label">Leverage</span>
                  <span className="margin-val" style={{ color: leverageColor(marginResult.effectiveLeverage) }}>
                    {marginResult.effectiveLeverage.toFixed(1)}×
                  </span>
                </div>
                <div className="margin-kv">
                  <span className="margin-label">Net Δ</span>
                  <span className="margin-val" style={{ color: marginResult.netDeltaBtc >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {marginResult.netDeltaBtc >= 0 ? '+' : ''}{marginResult.netDeltaBtc.toFixed(4)} BTC
                  </span>
                </div>
                <span className={`margin-binding-badge ${marginResult.bindingConstraint}`}>
                  {marginResult.bindingConstraint === 'risk_margin' ? 'Risk Margin' : 'Margin Floor'}
                </span>
                <span className="margin-expand-toggle">{marginExpanded ? '▲' : '▼'}</span>
              </>
            ) : (
              <span style={{ fontSize: '11px', color: 'var(--text3)' }}>Margin: N/A — mark price unavailable at this timestamp</span>
            )}
          </div>
          {marginResult && marginResult.skippedLegs > 0 && (
            <div className="margin-skip-warning">
              ⚠ {marginResult.skippedLegs} leg{marginResult.skippedLegs > 1 ? 's' : ''} excluded — mark price is 0 at this timestamp
            </div>
          )}
          {marginResult && marginExpanded && (
            <div className="margin-detail">
              <div className="margin-detail-grid">
                <div className="margin-detail-row"><span className="margin-detail-label">Risk Margin</span><span className="margin-detail-val">{fmt(marginResult.riskMargin)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Margin Floor</span><span className="margin-detail-val">{fmt(marginResult.marginFloor)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Pre-scale <span style={{ color: 'var(--text3)', fontSize: '10px' }}>max(Risk, Floor)</span></span><span className="margin-detail-val">{fmt(marginResult.preScaleMargin)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">DTE scale <span style={{ color: 'var(--text3)', fontSize: '10px' }}>(0.49 / DTE)^0.234</span></span><span className="margin-detail-val">×{marginResult.dteScaleApplied.toFixed(3)}</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Portfolio Margin <span style={{ color: 'var(--text3)', fontSize: '10px' }}>pre-scale × DTE scale</span></span><span className="margin-detail-val">{fmt(marginResult.portfolioMargin)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">UCF <span style={{ color: 'var(--text3)', fontSize: '10px' }}>signed expected payoff</span></span><span className="margin-detail-val" style={{ color: marginResult.ucf >= 0 ? 'var(--green)' : 'var(--red)' }}>{marginResult.ucf >= 0 ? '+' : ''}{fmt(marginResult.ucf)} USDT</span></div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Total Notional</span>
                  <span className="margin-detail-val">{fmt(marginResult.totalNotional)} USDT <span style={{ color: 'var(--text3)', fontSize: '10px' }}>({(marginResult.totalNotional / spot).toFixed(3)} BTC)</span></span>
                </div>
                <div className="margin-detail-row"><span className="margin-detail-label">Premium Collected</span><span className="margin-detail-val" style={{ color: 'var(--green)' }}>{fmt(marginResult.totalPremiumCollected)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Margin / Notional</span><span className="margin-detail-val">{((marginResult.initialMargin / marginResult.totalNotional) * 100).toFixed(2)}%</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Margin per Lot</span><span className="margin-detail-val">{fmt(marginResult.marginPerLot)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Maintenance Margin</span><span className="margin-detail-val">{fmt(marginResult.maintenanceMargin)} USDT</span></div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Floor Rate (OM%)</span>
                  <span className="margin-detail-val">{(marginResult.omPctApplied * 100).toFixed(3)}% <span style={{ color: 'var(--text3)', fontSize: '10px' }}>on {fmt(marginResult.shortOptionsNotional)} short</span></span>
                </div>
              </div>
              <div className="margin-scenarios">
                <div className="margin-scenario-row worst">
                  <span className="margin-scenario-icon">▼</span><span className="margin-scenario-label">Worst</span>
                  <span className="margin-scenario-desc">Price {marginResult.worstScenario.priceShockPct >= 0 ? '+' : ''}{marginResult.worstScenario.priceShockPct.toFixed(1)}% · Vol {marginResult.worstScenario.volShockPts >= 0 ? '+' : ''}{marginResult.worstScenario.volShockPts.toFixed(0)}pp</span>
                  <span className="margin-scenario-pnl" style={{ color: 'var(--red)' }}>−{fmt(Math.abs(marginResult.worstScenario.pnl))} USDT</span>
                </div>
                <div className="margin-scenario-row best">
                  <span className="margin-scenario-icon">▲</span><span className="margin-scenario-label">Best</span>
                  <span className="margin-scenario-desc">Price {marginResult.bestScenario.priceShockPct >= 0 ? '+' : ''}{marginResult.bestScenario.priceShockPct.toFixed(1)}% · Vol {marginResult.bestScenario.volShockPts >= 0 ? '+' : ''}{marginResult.bestScenario.volShockPts.toFixed(0)}pp</span>
                  <span className="margin-scenario-pnl" style={{ color: 'var(--green)' }}>+{fmt(marginResult.bestScenario.pnl)} USDT</span>
                </div>
              </div>
              <div className="margin-tier-info">
                Stress: ±{(marginResult.priceShockApplied * 100).toFixed(1)}% price · IV +{(marginResult.volUpApplied * 100).toFixed(1)}% / −{(marginResult.volDownApplied * 100).toFixed(1)}% @ {marginResult.minDteDaysApplied.toFixed(1)} DTE
                <span className="margin-disclaimer-icon" title="Matches Delta Exchange India official portfolio-margin methodology (29-scenario stress-test, continuous spans, DTE-adjusted IV shocks).">&nbsp;ⓘ</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── MTM / Payoff chart tabs ── */}
      <div className="strategy-chart-card" style={{ flex: 1, minHeight: 0, overflowY: buildTab === 'payoff' ? 'hidden' : 'auto', display: 'flex', flexDirection: 'column' }}>
        {!chartsOnly && (
          <div className="strategy-chart-header">
            <div style={{ display: 'flex', gap: '3px' }}>
              <button className={`tf-btn${buildTab === 'mtm' ? ' active' : ''}`} onClick={() => setBuildTab('mtm')}>MTM P&amp;L</button>
              {legs.length > 0 && (
                <button className={`tf-btn${buildTab === 'payoff' ? ' active' : ''}`} onClick={() => setBuildTab('payoff')}>Payoff</button>
              )}
            </div>
            {buildTab === 'mtm' && chartControls}
          </div>
        )}

        {buildTab === 'mtm' && (
          <>
            {!chartsOnly && endPicker}
            {buildMtmData.length > 0 ? (
              <>
                <MultiPaneChart
                  mtmData={buildMtmData}
                  ivSeries={buildIvSeries}
                  rvSeries={buildRvSeries ?? undefined}
                  atmIvSeries={buildAtmIvSeries}
                  ivRvSeries={buildIvRvSeries}
                  atmIvRvSeries={buildAtmIvRvSeries}
                  deltaSeries={buildDeltaSeries}
                  chartsOnly={chartsOnly}
                  onToggleChartsOnly={onToggleChartsOnly}
                />
                {!chartsOnly && (
                  <div style={{ flexShrink: 0, overflowY: 'auto' }}>
                    {(() => {
                      const s = computeMtmStats(buildMtmData);
                      if (!s) return null;
                      const slipEntry = legs.reduce((acc, l) => acc + slipPerSide(l) * l.qty * 0.001, 0);
                      const slipExit  = buildExitLegData.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
                        acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot) * leg.qty * 0.001, 0);
                      const brkEntry  = legs.reduce((acc, l) => acc + brokeragePerSide(l), 0);
                      const brkExit   = buildExitLegData.reduce((acc, { leg, exitMark, exitSpot }) =>
                        acc + brokeragePerSideAt(leg, exitMark, exitSpot), 0);
                      const grossFinal = s.finalPnl + slipEntry;
                      const peakExitSlip = buildMaxPnlExitData.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
                        acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot) * leg.qty * 0.001, 0);
                      const peakExitBrk  = buildMaxPnlExitData.reduce((acc, { leg, exitMark, exitSpot }) =>
                        acc + brokeragePerSideAt(leg, exitMark, exitSpot), 0);
                      const maxExitPnl = buildMaxPnlExitData.length > 0
                        ? s.maxPnl - peakExitSlip - peakExitBrk
                        : undefined;
                      const sens = [0.5, 1.0, 1.5, 2.0, 3.0].map(m => {
                        const eSlip = legs.reduce((acc, l) => acc + slipPerSide(l, m) * l.qty * 0.001, 0);
                        const xSlip = buildExitLegData.reduce((acc, { leg, exitTs, exitMark, exitSpot }) =>
                          acc + slipPerSideAt(leg, exitTs, exitMark, exitSpot, m) * leg.qty * 0.001, 0);
                        return { mult: m, finalPnl: grossFinal - eSlip - xSlip - brkEntry - brkExit };
                      });
                      return <MtmStatsPanel stats={s}
                        slipEntryUsd={slipEntry} slipExitUsd={slipExit}
                        brokerageEntryUsd={brkEntry} brokerageExitUsd={brkExit}
                        maxExitPnl={maxExitPnl} sensitivity={sens} />;
                    })()}
                  </div>
                )}
              </>
            ) : (
              <div className="strategy-chart-empty">
                {legs.length > 0
                  ? 'Click Run MTM ▶ to see P&L chart'
                  : 'Add legs to see MTM chart'}
              </div>
            )}
          </>
        )}

        {buildTab === 'payoff' && legs.length > 0 && (
          <PayoffGraph
            legs={legs}
            chain={chain}
            legChains={legChains}
            spot={spot}
            selectedExpiry={selectedExpiry}
            simulationTimestamp={simulationTimestamp}
          />
        )}
      </div>
    </div>
  );
};
