import React, { useState, useCallback, useMemo } from 'react';
import { historicalApi } from '../../services/historical_api';
import { MtmChart } from './MtmChart';
import { computePortfolioMargin, buildMarginLegs } from '../../utils/marginEngine';
import type { StrategyLeg, MtmPoint } from '../../types/strategy';
import type { HistoricalChainRow } from '../../types/historical';

interface Props {
  legs: StrategyLeg[];
  chain: HistoricalChainRow[];
  spot: number;
  selectedExpiry: string;
  simulationTimestamp: number;
  onRemoveLeg: (id: string) => void;
  onUpdateQty: (id: string, qty: number) => void;
  onClearAll: () => void;
}

const fmt = (n: number, d = 2) => n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

function leverageColor(lev: number): string {
  if (lev < 5)  return 'var(--green)';
  if (lev < 15) return 'var(--gold)';
  return 'var(--red)';
}

export const StrategyPanel: React.FC<Props> = ({
  legs, chain, spot, selectedExpiry, simulationTimestamp,
  onRemoveLeg, onUpdateQty, onClearAll
}) => {
  const [mtmData, setMtmData] = useState<MtmPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [marginExpanded, setMarginExpanded] = useState(false);

  // ─── Live P&L helpers ─────────────────────────────────────────────────────
  const getCurrentPrice = (leg: StrategyLeg) => {
    const row = chain.find(r => r.strike === leg.strike);
    if (!row) return leg.entryPremium;
    return leg.type === 'CE' ? row.call.last_price : row.put.last_price;
  };

  const getLegPnl = (leg: StrategyLeg) => {
    const dir = leg.action === 'BUY' ? 1 : -1;
    // Parquet prices are USDT/BTC; multiply by 0.001 (contract size) to get USDT/contract
    return (getCurrentPrice(leg) - leg.entryPremium) * leg.qty * dir * 0.001;
  };

  const totalPnl = legs.reduce((s, l) => s + getLegPnl(l), 0);

  // ─── Margin computation (memoized) ────────────────────────────────────────
  const marginResult = useMemo(() => {
    if (!legs.length || spot <= 0 || !selectedExpiry || !simulationTimestamp) return null;
    const { marginLegs, skippedLegs } = buildMarginLegs(legs, chain, spot, selectedExpiry, simulationTimestamp);
    if (!marginLegs.length) return null;
    return computePortfolioMargin(marginLegs, spot, undefined, skippedLegs);
  }, [legs, chain, spot, selectedExpiry, simulationTimestamp]);

  // ─── MTM run ──────────────────────────────────────────────────────────────
  const runMtm = useCallback(async () => {
    if (!legs.length) return;
    setLoading(true);
    setError('');
    try {
      const entryTs = Math.min(...legs.map(l => l.entryTimestamp));

      const seriesResults = await Promise.all(
        legs.map(leg =>
          historicalApi.getChartData(
            leg.expiry, leg.strike, leg.type, entryTs, '5m'
          ).then(res => ({ leg, data: res.data.filter(d => d.time >= entryTs) }))
        )
      );

      const timeSet = new Set<number>();
      seriesResults.forEach(({ data }) => data.forEach(d => timeSet.add(d.time)));
      const sortedTimes = Array.from(timeSet).sort((a, b) => a - b);

      const mtmPoints: MtmPoint[] = sortedTimes.map(t => {
        let total = 0;
        seriesResults.forEach(({ leg, data }) => {
          const pts = data.filter(d => d.time <= t);
          if (pts.length) {
            const pt = pts[pts.length - 1];
            const dir = leg.action === 'BUY' ? 1 : -1;
            // Parquet close prices are USDT/BTC; × 0.001 converts to USDT/contract
            total += (pt.close - leg.entryPremium) * leg.qty * dir * 0.001;
          }
        });
        return { time: t, pnl: total };
      });

      setMtmData(mtmPoints);
    } catch {
      setError('Failed to calculate MTM. Check console.');
    } finally {
      setLoading(false);
    }
  }, [legs]);

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: 0 }}>

      {/* ── Legs card ── */}
      <div className="strategy-legs-card">
        <div className="strategy-header">
          <span className="strategy-title">Strategy Legs</span>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            {legs.length > 0 && (
              <span style={{
                fontSize: '13px', fontWeight: 700,
                color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)'
              }}>
                {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USD
              </span>
            )}
            {legs.length > 0 && (
              <button className="strategy-btn-secondary" onClick={onClearAll}>Clear</button>
            )}
            <button
              className={`strategy-btn-run${loading ? ' loading' : ''}`}
              onClick={runMtm}
              disabled={loading || !legs.length}
            >
              {loading ? 'Calculating…' : 'Run MTM ▶'}
            </button>
          </div>
        </div>

        {legs.length === 0 ? (
          <div className="strategy-empty">
            Click <span className="strategy-badge buy">B</span> or <span className="strategy-badge sell">S</span> on any strike in the chain to add a leg
          </div>
        ) : (
          <div className="strategy-table-wrap">
            <table className="strategy-table">
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Strike</th>
                  <th>Type</th>
                  <th title="1 lot = 0.001 BTC · 1000 lots = 1 BTC">Lots</th>
                  <th>BTC</th>
                  <th>Entry</th>
                  <th>Current</th>
                  <th>P&amp;L</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {legs.map(leg => {
                  const curr = getCurrentPrice(leg);
                  const pnl = getLegPnl(leg);
                  const btcSize = leg.qty * 0.001;
                  return (
                    <tr key={leg.id}>
                      <td>
                        <span className={`strategy-badge ${leg.action === 'BUY' ? 'buy' : 'sell'}`}>
                          {leg.action}
                        </span>
                      </td>
                      <td style={{ fontWeight: 700 }}>{leg.strike.toLocaleString()}</td>
                      <td>
                        <span className={`strategy-badge ${leg.type === 'CE' ? 'ce' : 'pe'}`}>
                          {leg.type}
                        </span>
                      </td>
                      <td>
                        <input
                          type="number"
                          className="strategy-qty-input"
                          value={leg.qty}
                          min={1}
                          onChange={e => onUpdateQty(leg.id, Math.max(1, parseInt(e.target.value) || 1))}
                        />
                      </td>
                      <td style={{ color: 'var(--text3)', fontSize: '11px' }}>
                        {btcSize < 0.01
                          ? btcSize.toFixed(3)
                          : btcSize.toFixed(2)}
                      </td>
                      <td style={{ color: 'var(--text2)' }}>{leg.entryPremium.toFixed(2)}</td>
                      <td>{curr.toFixed(2)}</td>
                      <td style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                      </td>
                      <td>
                        <button className="strategy-btn-remove" onClick={() => onRemoveLeg(leg.id)}>×</button>
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

      {/* ── Portfolio Margin card ── */}
      {marginResult && legs.length > 0 && (
        <div className="margin-card">
          {/* Compact always-visible row */}
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

          {/* Skipped legs warning */}
          {marginResult.skippedLegs > 0 && (
            <div className="margin-skip-warning">
              ⚠ {marginResult.skippedLegs} leg{marginResult.skippedLegs > 1 ? 's' : ''} excluded — mark price is 0 at this timestamp (no IV available)
            </div>
          )}

          {/* Expandable detail */}
          {marginExpanded && (
            <div className="margin-detail">
              <div className="margin-detail-grid">
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Risk Margin</span>
                  <span className="margin-detail-val">{fmt(marginResult.riskMargin)} USDT</span>
                </div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Margin Floor</span>
                  <span className="margin-detail-val">{fmt(marginResult.marginFloor)} USDT</span>
                </div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Total Notional</span>
                  <span className="margin-detail-val">
                    {fmt(marginResult.totalNotional)} USDT
                    <span style={{ color: 'var(--text3)', fontWeight: 400, marginLeft: '6px', fontSize: '10px' }}>
                      ({(marginResult.totalNotional / spot).toFixed(3)} BTC)
                    </span>
                  </span>
                </div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Premium Collected</span>
                  <span className="margin-detail-val" style={{ color: 'var(--green)' }}>
                    {fmt(marginResult.totalPremiumCollected)} USDT
                  </span>
                </div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Margin / Notional</span>
                  <span className="margin-detail-val">
                    {((marginResult.portfolioMargin / marginResult.totalNotional) * 100).toFixed(2)}%
                  </span>
                </div>
                <div className="margin-detail-row">
                  <span className="margin-detail-label">Margin per Lot</span>
                  <span className="margin-detail-val">{fmt(marginResult.marginPerLot)} USDT</span>
                </div>
              </div>

              <div className="margin-scenarios">
                <div className="margin-scenario-row worst">
                  <span className="margin-scenario-icon">▼</span>
                  <span className="margin-scenario-label">Worst</span>
                  <span className="margin-scenario-desc">
                    Price {marginResult.worstScenario.priceShockPct >= 0 ? '+' : ''}{marginResult.worstScenario.priceShockPct.toFixed(1)}%
                    &nbsp;·&nbsp;
                    Vol {marginResult.worstScenario.volShockPts >= 0 ? '+' : ''}{marginResult.worstScenario.volShockPts.toFixed(0)}pp
                  </span>
                  <span className="margin-scenario-pnl" style={{ color: 'var(--red)' }}>
                    −{fmt(Math.abs(marginResult.worstScenario.pnl))} USDT
                  </span>
                </div>
                <div className="margin-scenario-row best">
                  <span className="margin-scenario-icon">▲</span>
                  <span className="margin-scenario-label">Best</span>
                  <span className="margin-scenario-desc">
                    Price {marginResult.bestScenario.priceShockPct >= 0 ? '+' : ''}{marginResult.bestScenario.priceShockPct.toFixed(1)}%
                    &nbsp;·&nbsp;
                    Vol {marginResult.bestScenario.volShockPts >= 0 ? '+' : ''}{marginResult.bestScenario.volShockPts.toFixed(0)}pp
                  </span>
                  <span className="margin-scenario-pnl" style={{ color: 'var(--green)' }}>
                    +{fmt(marginResult.bestScenario.pnl)} USDT
                  </span>
                </div>
              </div>

              <div className="margin-tier-info">
                Stress tier: ±{(marginResult.priceShockApplied * 100).toFixed(0)}% price ·
                +{marginResult.volUpApplied}pp / −{marginResult.volDownApplied}pp vol
                <span
                  className="margin-disclaimer-icon"
                  title="Estimated margin based on published Delta Exchange methodology. Actual margin may differ by 10–15%."
                >
                  &nbsp;ⓘ
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── MTM chart card ── */}
      <div className="strategy-chart-card">
        <div className="strategy-chart-header">
          <span className="strategy-chart-label">MTM P&amp;L Over Time</span>
          {mtmData.length > 0 && (
            <span style={{ fontSize: '10px', color: 'var(--text3)' }}>
              {mtmData.length} candles · 5m resolution
            </span>
          )}
        </div>
        {mtmData.length > 0 ? (
          <MtmChart data={mtmData} />
        ) : (
          <div className="strategy-chart-empty">
            {legs.length > 0
              ? 'Click "Run MTM ▶" to generate P&L curve'
              : 'Add legs to the strategy first'}
          </div>
        )}
      </div>
    </div>
  );
};
