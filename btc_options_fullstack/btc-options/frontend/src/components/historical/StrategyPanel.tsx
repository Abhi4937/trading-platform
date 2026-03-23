import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import * as XLSX from 'xlsx';
import { historicalApi } from '../../services/historical_api';
import { MtmChart } from './MtmChart';
import { CompareChart } from './CompareChart';
import { computePortfolioMargin, buildMarginLegs } from '../../utils/marginEngine';
import type { Strategy, StrategyLeg, MtmPoint } from '../../types/strategy';
import type { HistoricalChainRow } from '../../types/historical';

interface Props {
  strategies: Strategy[];
  activeStrategyId: string;
  chain: HistoricalChainRow[];
  legChains: Map<string, HistoricalChainRow[]>;
  spot: number;
  selectedExpiry: string;
  simulationTimestamp: number;
  onSetActiveStrategy: (id: string) => void;
  onAddStrategy: () => void;
  onRemoveStrategy: (stratId: string) => void;
  onRemoveLeg: (stratId: string, legId: string) => void;
  onUpdateQty: (stratId: string, legId: string, qty: number) => void;
  onClearLegs: (stratId: string) => void;
}

const fmt = (n: number, d = 2) => n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const STRAT_COLORS = ['#00d4ff', '#f0b429', '#a78bfa', '#fb923c', '#34d399', '#f472b6', '#60a5fa', '#ff6b6b'];

const LegQtyInput: React.FC<{
  stratId: string; id: string; qty: number;
  onUpdateQty: (stratId: string, id: string, qty: number) => void;
}> = ({ stratId, id, qty, onUpdateQty }) => {
  const [val, setVal] = useState(String(qty));
  useEffect(() => { setVal(String(qty)); }, [qty]);
  const commit = (n: number) => { const v = Math.max(1, n); setVal(String(v)); onUpdateQty(stratId, id, v); };
  return (
    <div className="qty-stepper">
      <button className="qty-btn" onClick={() => commit(Math.max(1, (parseInt(val) || 1) - 1))}>−</button>
      <input
        type="text" inputMode="numeric" className="strategy-qty-input" value={val}
        onChange={e => { setVal(e.target.value); const n = parseInt(e.target.value); if (!isNaN(n) && n >= 1) onUpdateQty(stratId, id, n); }}
        onBlur={() => commit(parseInt(val) || 1)}
      />
      <button className="qty-btn" onClick={() => commit((parseInt(val) || 1) + 1)}>+</button>
    </div>
  );
};

function leverageColor(lev: number): string {
  if (lev < 5)  return 'var(--green)';
  if (lev < 15) return 'var(--gold)';
  return 'var(--red)';
}

export const StrategyPanel: React.FC<Props> = ({
  strategies, activeStrategyId, chain, legChains, spot, selectedExpiry, simulationTimestamp,
  onSetActiveStrategy, onAddStrategy, onRemoveStrategy, onRemoveLeg, onUpdateQty, onClearLegs,
}) => {
  const [strategyMtmData, setStrategyMtmData] = useState<Map<string, MtmPoint[]>>(new Map());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [marginExpanded, setMarginExpanded] = useState(false);
  const [timeframe, setTimeframe] = useState<'1m'|'5m'|'15m'|'30m'|'1h'>('5m');
  const [endDate, setEndDate] = useState(selectedExpiry);
  const [endTime, setEndTime] = useState('17:30');

  // Per-leg compare for active strategy
  const [compareMode, setCompareMode] = useState(false);
  const [legMtmData, setLegMtmData] = useState<Map<string, MtmPoint[]>>(new Map());
  const [compareLoading, setCompareLoading] = useState(false);

  const prevStrategiesRef = useRef<Strategy[]>(strategies);
  useEffect(() => { prevStrategiesRef.current = strategies; }, [strategies]);

  // Clear MTM when strategies change (legs added/removed)
  useEffect(() => {
    setStrategyMtmData(new Map());
    setLegMtmData(new Map());
  }, [strategies]);

  useEffect(() => { setEndDate(selectedExpiry); }, [selectedExpiry]);

  const activeStrategy = useMemo(() => strategies.find(s => s.id === activeStrategyId) ?? strategies[0], [strategies, activeStrategyId]);
  const activeLegs = activeStrategy?.legs ?? [];

  // ─── Live P&L helpers (active strategy only) ──────────────────────────────
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
    return {
      iv_pct: opt.iv_pct,
      delta: opt.delta * leg.qty * dir,
      theta: opt.theta * leg.qty * dir,
      vega: opt.vega * leg.qty * dir,
    };
  };

  const getLegPnl = (leg: StrategyLeg) => {
    const dir = leg.action === 'BUY' ? 1 : -1;
    return (getCurrentPrice(leg) - leg.entryPremium) * leg.qty * dir * 0.001;
  };

  const totalPnl = activeLegs.reduce((s, l) => s + getLegPnl(l), 0);

  // ─── Margin (active strategy) ──────────────────────────────────────────────
  const marginResult = useMemo(() => {
    if (!activeLegs.length || spot <= 0 || !selectedExpiry || !simulationTimestamp) return null;
    const { marginLegs, skippedLegs } = buildMarginLegs(activeLegs, chain, spot, selectedExpiry, simulationTimestamp);
    if (!marginLegs.length) return null;
    return computePortfolioMargin(marginLegs, spot, undefined, skippedLegs);
  }, [activeLegs, chain, spot, selectedExpiry, simulationTimestamp]);

  // ─── Download (active strategy) ────────────────────────────────────────────
  const toIst = (ts: number) => {
    const d = new Date((ts + 5.5 * 3600) * 1000);
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' IST';
  };

  const downloadExcel = () => {
    const wb = XLSX.utils.book_new();
    const legsRows = activeLegs.map(leg => ({
      Action: leg.action, Expiry: leg.expiry, Strike: leg.strike, Type: leg.type,
      Lots: leg.qty, 'Entry Premium': leg.entryPremium,
      'Current Price': getCurrentPrice(leg),
      'P&L (USD)': +getLegPnl(leg).toFixed(2),
    }));
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(legsRows), activeStrategy.label);

    // Add MTM sheet if available
    const mtmPoints = strategyMtmData.get(activeStrategyId);
    if (mtmPoints?.length) {
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(
        mtmPoints.map(d => ({ 'Time (IST)': toIst(d.time), 'P&L (USD)': +d.pnl.toFixed(2) }))
      ), 'MTM P&L');
    }
    XLSX.writeFile(wb, `strategy_${selectedExpiry}.xlsx`);
  };

  // ─── Run MTM for ALL strategies ────────────────────────────────────────────
  const runMtm = useCallback(async () => {
    const nonEmpty = strategies.filter(s => s.legs.length > 0);
    if (!nonEmpty.length) return;
    setLoading(true);
    setError('');
    setCompareMode(false);
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

      setStrategyMtmData(map);
    } catch {
      setError('Failed to calculate MTM. Check console.');
    } finally {
      setLoading(false);
    }
  }, [strategies, simulationTimestamp, endDate, endTime, timeframe]);

  // ─── Per-leg compare (active strategy) ────────────────────────────────────
  const runCompare = useCallback(async () => {
    if (!activeLegs.length) return;
    setCompareLoading(true);
    setCompareMode(true);
    try {
      const startTs = simulationTimestamp;
      const endTs = Math.floor(new Date(`${endDate}T${endTime}:00+05:30`).getTime() / 1000);
      const results = await Promise.all(
        activeLegs.map(leg =>
          historicalApi.getChartData(leg.expiry, leg.strike, leg.type, startTs, timeframe)
            .then(res => ({ leg, data: res.data.filter(d => d.time >= startTs && d.time <= endTs) }))
        )
      );
      const legMap = new Map<string, MtmPoint[]>();
      results.forEach(({ leg, data }) => {
        const dir = leg.action === 'BUY' ? 1 : -1;
        legMap.set(leg.id, data.map(d => ({
          time: d.time,
          pnl: (d.close - leg.entryPremium) * leg.qty * dir * 0.001,
        })));
      });
      setLegMtmData(legMap);
    } catch {
      setCompareMode(false);
    } finally {
      setCompareLoading(false);
    }
  }, [activeLegs, simulationTimestamp, endDate, endTime, timeframe]);

  // Memoized series for charts
  const stratCompareSeries = useMemo(() =>
    strategies.filter(s => s.legs.length > 0).map((s, i) => ({
      id: s.id,
      label: s.label,
      color: STRAT_COLORS[i % STRAT_COLORS.length],
      data: strategyMtmData.get(s.id) ?? [],
    })),
  [strategies, strategyMtmData]);

  const legCompareSeries = useMemo(() =>
    activeLegs.map((leg, i) => ({
      id: leg.id,
      label: `${leg.action[0]} ${leg.strike} ${leg.type}`,
      color: STRAT_COLORS[i % STRAT_COLORS.length],
      data: legMtmData.get(leg.id) ?? [],
    })),
  [activeLegs, legMtmData]);

  const allLegs = useMemo(() => strategies.flatMap(s => s.legs), [strategies]);
  const hasAnyLegs = allLegs.length > 0;
  const minEntryTs = hasAnyLegs ? Math.min(...allLegs.map(l => l.entryTimestamp)) : undefined;

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: 0 }}>

      {/* ── Strategy Tabs ── */}
      <div className="strat-tabs">
        {strategies.map((s, i) => (
          <button
            key={s.id}
            className={`strat-tab${s.id === activeStrategyId ? ' active' : ''}`}
            style={{ '--strat-color': STRAT_COLORS[i % STRAT_COLORS.length] } as React.CSSProperties}
            onClick={() => onSetActiveStrategy(s.id)}
          >
            <span className="strat-tab-dot" style={{ background: STRAT_COLORS[i % STRAT_COLORS.length] }} />
            {s.label}
            {s.legs.length > 0 && <span className="strategy-leg-count">{s.legs.length}</span>}
            {strategies.length > 1 && (
              <span className="strat-tab-close" onClick={e => { e.stopPropagation(); onRemoveStrategy(s.id); }}>×</span>
            )}
          </button>
        ))}
        <button className="strat-tab-add" onClick={onAddStrategy}>＋</button>
      </div>

      {/* ── Active Strategy Legs card ── */}
      <div className="strategy-legs-card">
        <div className="strategy-header">
          <span className="strategy-title">{activeStrategy?.label}</span>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            {activeLegs.length > 0 && (
              <span style={{ fontSize: '12px', fontWeight: 700, color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USD
              </span>
            )}
            {activeLegs.length > 0 && (
              <button className="strategy-btn-secondary" onClick={() => onClearLegs(activeStrategyId)}>Clear</button>
            )}
            <button
              className={`strategy-btn-secondary${compareMode ? ' active-mode' : ''}`}
              onClick={runCompare}
              disabled={compareLoading || !activeLegs.length}
              title="Per-leg P&L curves for this strategy"
            >
              {compareLoading ? '…' : 'Compare ⊞'}
            </button>
            <button
              className={`strategy-btn-run${loading ? ' loading' : ''}`}
              onClick={() => { setCompareMode(false); runMtm(); }}
              disabled={loading || !hasAnyLegs}
            >
              {loading ? 'Calculating…' : 'Run MTM ▶'}
            </button>
            <button className="strategy-btn-secondary" disabled={!activeLegs.length} onClick={downloadExcel}>
              Download ↓
            </button>
          </div>
        </div>

        {activeLegs.length === 0 ? (
          <div className="strategy-empty">
            Click <span className="strategy-badge buy">B</span> or <span className="strategy-badge sell">S</span> on any strike to add a leg to {activeStrategy?.label}
          </div>
        ) : (
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
                  <th title="Net Theta">Theta</th>
                  <th title="Net Vega">Vega</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {activeLegs.map(leg => {
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
                        <LegQtyInput stratId={activeStrategyId} id={leg.id} qty={leg.qty} onUpdateQty={onUpdateQty} />
                      </td>
                      <td style={{ color: 'var(--text3)', fontSize: '11px' }}>{btcSize < 0.01 ? btcSize.toFixed(3) : btcSize.toFixed(2)}</td>
                      <td style={{ color: 'var(--text2)' }}>{leg.entryPremium.toFixed(2)}</td>
                      <td>{curr.toFixed(2)}</td>
                      <td style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                      </td>
                      <td style={{ color: 'var(--gold)' }}>{greeks ? greeks.iv_pct.toFixed(1) : '-'}</td>
                      <td style={{ color: greeks && greeks.delta >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {greeks ? (greeks.delta >= 0 ? '+' : '') + greeks.delta.toFixed(3) : '-'}
                      </td>
                      <td style={{ color: greeks && greeks.theta < 0 ? 'var(--red)' : 'var(--green)' }}>
                        {greeks ? (greeks.theta >= 0 ? '+' : '') + greeks.theta.toFixed(2) : '-'}
                      </td>
                      <td style={{ color: greeks && greeks.vega >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {greeks ? (greeks.vega >= 0 ? '+' : '') + greeks.vega.toFixed(2) : '-'}
                      </td>
                      <td>
                        <button className="strategy-btn-remove" onClick={() => onRemoveLeg(activeStrategyId, leg.id)}>×</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {error && <div style={{ color: 'var(--red)', fontSize: '11px', padding: '4px 10px' }}>{error}</div>}
      </div>

      {/* ── Portfolio Margin (active strategy) ── */}
      {marginResult && activeLegs.length > 0 && (
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

      {/* ── MTM chart card ── */}
      <div className="strategy-chart-card">
        <div className="strategy-chart-header">
          <span className="strategy-chart-label">
            {compareMode ? `${activeStrategy?.label} — Per-Leg` : strategies.length > 1 ? 'Strategy Comparison' : 'MTM P&L'}
          </span>
          <div style={{ display: 'flex', gap: '3px' }}>
            {(['1m','5m','15m','30m','1h'] as const).map(tf => (
              <button key={tf} className={`tf-btn${timeframe === tf ? ' active' : ''}`} onClick={() => setTimeframe(tf)}>{tf}</button>
            ))}
          </div>
        </div>
        <div className="mtm-range-row">
          <span className="mtm-range-label">Show until</span>
          <input type="date" className="mtm-range-input" value={endDate}
            min={minEntryTs ? new Date(minEntryTs * 1000).toISOString().split('T')[0] : undefined}
            max={selectedExpiry} onChange={e => setEndDate(e.target.value)} />
          <input type="time" className="mtm-range-input" value={endTime} onChange={e => setEndTime(e.target.value)} />
        </div>

        {/* Per-leg compare (active strategy) */}
        {compareMode && legMtmData.size > 0 ? (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', padding: '4px 10px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
              {activeLegs.map((leg, i) => (
                <span key={leg.id} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px' }}>
                  <span style={{ width: '10px', height: '2px', background: STRAT_COLORS[i % STRAT_COLORS.length], display: 'inline-block', borderRadius: '1px' }} />
                  <span style={{ color: 'var(--text2)' }}>{leg.action[0]} {leg.strike} {leg.type} ({leg.expiry})</span>
                </span>
              ))}
            </div>
            <CompareChart series={legCompareSeries} />
          </>
        /* Strategy MTM comparison */
        ) : !compareMode && strategyMtmData.size > 0 ? (
          strategies.length === 1 ? (
            // Single strategy → show baseline chart
            <MtmChart data={strategyMtmData.get(strategies[0].id) ?? []} />
          ) : (
            // Multiple strategies → show compare chart with legend
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', padding: '4px 10px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
                {strategies.filter(s => s.legs.length > 0).map((s, i) => (
                  <span key={s.id} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px' }}>
                    <span style={{ width: '10px', height: '2px', background: STRAT_COLORS[i % STRAT_COLORS.length], display: 'inline-block', borderRadius: '1px' }} />
                    <span style={{ color: 'var(--text2)' }}>{s.label} ({s.legs.length} legs)</span>
                  </span>
                ))}
              </div>
              <CompareChart series={stratCompareSeries} />
            </>
          )
        ) : (
          <div className="strategy-chart-empty">
            {hasAnyLegs
              ? <>Click <b>Run MTM ▶</b> to compare strategies, or <b>Compare ⊞</b> for per-leg breakdown</>
              : 'Add legs to a strategy first'}
          </div>
        )}
      </div>
    </div>
  );
};
