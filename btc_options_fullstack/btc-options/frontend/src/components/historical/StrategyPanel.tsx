import React, { useState, useCallback, useMemo, useEffect } from 'react';
import * as XLSX from 'xlsx';
import { historicalApi } from '../../services/historical_api';
import { MtmChart } from './MtmChart';
import { CompareChart } from './CompareChart';
import { computePortfolioMargin, buildMarginLegs } from '../../utils/marginEngine';
import type { Strategy, StrategyLeg, MtmPoint } from '../../types/strategy';
import type { HistoricalChainRow } from '../../types/historical';

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
}) => {
  // Build mode MTM
  const [buildMtmData, setBuildMtmData] = useState<MtmPoint[]>([]);
  const [buildLoading, setBuildLoading] = useState(false);
  const [buildError, setBuildError] = useState('');

  // Compare mode MTM
  const [compareMtmData, setCompareMtmData] = useState<Map<string, MtmPoint[]>>(new Map());
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState('');

  const [marginExpanded, setMarginExpanded] = useState(false);
  const [timeframe, setTimeframe] = useState<'1m'|'5m'|'15m'|'30m'|'1h'>('5m');
  const [endDate, setEndDate] = useState(selectedExpiry);
  const [endTime, setEndTime] = useState('17:30');

  useEffect(() => { setEndDate(selectedExpiry); }, [selectedExpiry]);

  // Clear MTM when legs or compare strategies change
  useEffect(() => { setBuildMtmData([]); setBuildError(''); }, [legs]);
  useEffect(() => { setCompareMtmData(new Map()); setCompareError(''); }, [compareStrategies]);

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

  const getLegPnl = (leg: StrategyLeg) => {
    const dir = leg.action === 'BUY' ? 1 : -1;
    return (getCurrentPrice(leg) - leg.entryPremium) * leg.qty * dir * 0.001;
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

  // ── Margin (build mode) ────────────────────────────────────────────────────
  const marginResult = useMemo(() => {
    if (!legs.length || spot <= 0 || !selectedExpiry || !simulationTimestamp) return null;
    const { marginLegs, skippedLegs } = buildMarginLegs(legs, chain, spot, selectedExpiry, simulationTimestamp);
    if (!marginLegs.length) return null;
    return computePortfolioMargin(marginLegs, spot, undefined, skippedLegs);
  }, [legs, chain, spot, selectedExpiry, simulationTimestamp]);

  const minEntryTs = legs.length ? Math.min(...legs.map(l => l.entryTimestamp)) : undefined;

  // ── Download (build mode) ──────────────────────────────────────────────────
  const toIst = (ts: number) => {
    const d = new Date((ts + 5.5 * 3600) * 1000);
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' IST';
  };

  const downloadExcel = () => {
    const wb = XLSX.utils.book_new();
    const legsRows = legs.map(leg => ({
      Action: leg.action, Expiry: leg.expiry, Strike: leg.strike, Type: leg.type,
      Lots: leg.qty, 'Entry Premium': leg.entryPremium,
      'Current Price': getCurrentPrice(leg),
      'P&L (USD)': +getLegPnl(leg).toFixed(2),
    }));
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(legsRows), 'Strategy');
    if (buildMtmData.length) {
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(
        buildMtmData.map(d => ({ 'Time (IST)': toIst(d.time), 'P&L (USD)': +d.pnl.toFixed(2) }))
      ), 'MTM P&L');
    }
    XLSX.writeFile(wb, `strategy_${selectedExpiry}.xlsx`);
  };

  // ── Run MTM (build mode) ───────────────────────────────────────────────────
  const runBuildMtm = useCallback(async () => {
    if (!legs.length) return;
    setBuildLoading(true);
    setBuildError('');
    try {
      const startTs = simulationTimestamp;
      const endTs = Math.floor(new Date(`${endDate}T${endTime}:00+05:30`).getTime() / 1000);

      const seriesResults = await Promise.all(
        legs.map(leg =>
          historicalApi.getChartData(leg.expiry, leg.strike, leg.type, startTs, timeframe)
            .then(res => ({ leg, data: res.data.filter(d => d.time >= startTs && d.time <= endTs) }))
        )
      );

      const timeSet = new Set<number>();
      seriesResults.forEach(({ data }) => data.forEach(d => timeSet.add(d.time)));
      const sortedTimes = Array.from(timeSet).sort((a, b) => a - b);

      const points: MtmPoint[] = sortedTimes.map(t => {
        let total = 0;
        seriesResults.forEach(({ leg, data }) => {
          const pts = data.filter(d => d.time <= t);
          if (pts.length) {
            const dir = leg.action === 'BUY' ? 1 : -1;
            total += (pts[pts.length - 1].close - leg.entryPremium) * leg.qty * dir * 0.001;
          }
        });
        return { time: t, pnl: total };
      });

      setBuildMtmData(points);
    } catch {
      setBuildError('Failed to calculate MTM. Check console.');
    } finally {
      setBuildLoading(false);
    }
  }, [legs, simulationTimestamp, endDate, endTime, timeframe]);

  // ── Run MTM (compare mode) ─────────────────────────────────────────────────
  const runCompareMtm = useCallback(async () => {
    const nonEmpty = compareStrategies.filter(s => s.legs.length > 0);
    if (!nonEmpty.length) return;
    setCompareLoading(true);
    setCompareError('');
    try {
      const startTs = simulationTimestamp;
      const endTs = Math.floor(new Date(`${endDate}T${endTime}:00+05:30`).getTime() / 1000);
      const map = new Map<string, MtmPoint[]>();

      await Promise.all(nonEmpty.map(async strat => {
        const seriesResults = await Promise.all(
          strat.legs.map(leg =>
            historicalApi.getChartData(leg.expiry, leg.strike, leg.type, startTs, timeframe)
              .then(res => ({ leg, data: res.data.filter(d => d.time >= startTs && d.time <= endTs) }))
          )
        );

        const timeSet = new Set<number>();
        seriesResults.forEach(({ data }) => data.forEach(d => timeSet.add(d.time)));
        const sortedTimes = Array.from(timeSet).sort((a, b) => a - b);

        const points: MtmPoint[] = sortedTimes.map(t => {
          let total = 0;
          seriesResults.forEach(({ leg, data }) => {
            const pts = data.filter(d => d.time <= t);
            if (pts.length) {
              const dir = leg.action === 'BUY' ? 1 : -1;
              total += (pts[pts.length - 1].close - leg.entryPremium) * leg.qty * dir * 0.001;
            }
          });
          return { time: t, pnl: total };
        });

        map.set(strat.id, points);
      }));

      setCompareMtmData(map);
    } catch {
      setCompareError('Failed to calculate MTM. Check console.');
    } finally {
      setCompareLoading(false);
    }
  }, [compareStrategies, simulationTimestamp, endDate, endTime, timeframe]);

  // Memoized compare chart series
  const compareChartSeries = useMemo(() =>
    compareStrategies.filter(s => s.legs.length > 0).map((s, i) => ({
      id: s.id,
      label: s.label,
      color: STRAT_COLORS[i % STRAT_COLORS.length],
      data: compareMtmData.get(s.id) ?? [],
    })),
  [compareStrategies, compareMtmData]);

  // Active compare strategy
  const activeCompareStrat = useMemo(() =>
    compareStrategies.find(s => s.id === activeCompareStratId) ?? compareStrategies[0],
  [compareStrategies, activeCompareStratId]);

  // ── Shared chart controls ──────────────────────────────────────────────────
  const chartControls = (
    <div style={{ display: 'flex', gap: '3px' }}>
      {(['1m','5m','15m','30m','1h'] as const).map(tf => (
        <button key={tf} className={`tf-btn${timeframe === tf ? ' active' : ''}`} onClick={() => setTimeframe(tf)}>{tf}</button>
      ))}
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

  // ═══════════════════════════════════════════════════════════════════════════
  // COMPARE MODE
  // ═══════════════════════════════════════════════════════════════════════════
  if (compareMode) {
    const hasAnyLegs = compareStrategies.some(s => s.legs.length > 0);
    const activeLegs = activeCompareStrat?.legs ?? [];

    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>

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
          </div>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            <button
              className={`strategy-btn-run${compareLoading ? ' loading' : ''}`}
              onClick={runCompareMtm}
              disabled={compareLoading || !hasAnyLegs}
            >
              {compareLoading ? 'Calculating…' : 'Run MTM ▶'}
            </button>
          </div>
        </div>

        {/* ── Strategy tabs ── */}
        <div className="strat-tabs">
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
        </div>

        {/* ── Active strategy legs ── */}
        <div className="strategy-legs-card">
          <div className="strategy-header">
            <span className="strategy-title" style={{ color: STRAT_COLORS[(compareStrategies.findIndex(s => s.id === activeCompareStratId)) % STRAT_COLORS.length] }}>
              {activeCompareStrat?.label}
            </span>
            {activeLegs.length > 0 && (
              <button className="strategy-btn-secondary" onClick={() => onClearCompareLegs(activeCompareStratId)}>Clear</button>
            )}
          </div>
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

        {/* ── Compare MTM chart ── */}
        <div className="strategy-chart-card">
          <div className="strategy-chart-header">
            <span className="strategy-chart-label">Strategy Comparison</span>
            {chartControls}
          </div>
          {endPicker}

          {compareMtmData.size > 0 && compareChartSeries.some(s => s.data.length > 0) ? (
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', padding: '4px 10px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
                {compareStrategies.filter(s => s.legs.length > 0).map((s, i) => (
                  <span key={s.id} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px' }}>
                    <span style={{ width: '10px', height: '2px', background: STRAT_COLORS[i % STRAT_COLORS.length], display: 'inline-block', borderRadius: '1px' }} />
                    <span style={{ color: 'var(--text2)' }}>{s.label} ({s.legs.length} legs)</span>
                  </span>
                ))}
              </div>
              <CompareChart series={compareChartSeries} />
            </>
          ) : (
            <div className="strategy-chart-empty">
              {hasAnyLegs
                ? 'Click Run MTM ▶ to compare strategies'
                : 'Add legs to strategies first'}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // BUILD MODE
  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: 0 }}>

      {/* ── Legs card ── */}
      <div className="strategy-legs-card">
        <div className="strategy-header">
          <span className="strategy-title">Strategy Builder</span>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            {legs.length > 0 && (
              <span style={{ fontSize: '12px', fontWeight: 700, color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USD
              </span>
            )}
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

        {legs.length === 0 ? (
          <div className="strategy-empty">
            Click <span className="strategy-badge buy">B</span> or <span className="strategy-badge sell">S</span> on any strike to add a leg
          </div>
        ) : (
          renderLegsTable(legs, onRemoveLeg, onUpdateQty)
        )}

        {/* ── Net portfolio Greeks summary ── */}
        {legs.length > 0 && netGreeks && (
          <div className="strategy-net-greeks">
            <span className="net-greeks-label">Portfolio</span>
            <div className="net-greeks-stat">
              <span className="net-greeks-key">P&amp;L</span>
              <span className="net-greeks-val" style={{ color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USD
              </span>
            </div>
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
      </div>

      {/* ── Portfolio Margin ── */}
      {marginResult && legs.length > 0 && (
        <div className="margin-card">
          <div className="margin-compact-row" onClick={() => setMarginExpanded(e => !e)}>
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
          </div>
          {marginResult.skippedLegs > 0 && (
            <div className="margin-skip-warning">
              ⚠ {marginResult.skippedLegs} leg{marginResult.skippedLegs > 1 ? 's' : ''} excluded — mark price is 0 at this timestamp
            </div>
          )}
          {marginExpanded && (
            <div className="margin-detail">
              <div className="margin-detail-grid">
                <div className="margin-detail-row"><span className="margin-detail-label">Risk Margin</span><span className="margin-detail-val">{fmt(marginResult.riskMargin)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Margin Floor</span><span className="margin-detail-val">{fmt(marginResult.marginFloor)} USDT</span></div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Total Notional</span>
                  <span className="margin-detail-val">{fmt(marginResult.totalNotional)} USDT <span style={{ color: 'var(--text3)', fontSize: '10px' }}>({(marginResult.totalNotional / spot).toFixed(3)} BTC)</span></span>
                </div>
                <div className="margin-detail-row"><span className="margin-detail-label">Premium Collected</span><span className="margin-detail-val" style={{ color: 'var(--green)' }}>{fmt(marginResult.totalPremiumCollected)} USDT</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Margin / Notional</span><span className="margin-detail-val">{((marginResult.portfolioMargin / marginResult.totalNotional) * 100).toFixed(2)}%</span></div>
                <div className="margin-detail-row"><span className="margin-detail-label">Margin per Lot</span><span className="margin-detail-val">{fmt(marginResult.marginPerLot)} USDT</span></div>
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
                Stress tier: ±{(marginResult.priceShockApplied * 100).toFixed(0)}% price · +{marginResult.volUpApplied}pp / −{marginResult.volDownApplied}pp vol
                <span className="margin-disclaimer-icon" title="Estimated margin based on Delta Exchange methodology. Actual may differ 10–15%.">&nbsp;ⓘ</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── MTM chart ── */}
      <div className="strategy-chart-card">
        <div className="strategy-chart-header">
          <span className="strategy-chart-label">MTM P&amp;L</span>
          {chartControls}
        </div>
        {endPicker}

        {buildMtmData.length > 0 ? (
          <MtmChart data={buildMtmData} />
        ) : (
          <div className="strategy-chart-empty">
            {legs.length > 0
              ? 'Click Run MTM ▶ to see P&L chart'
              : 'Add legs to see MTM chart'}
          </div>
        )}
      </div>
    </div>
  );
};
