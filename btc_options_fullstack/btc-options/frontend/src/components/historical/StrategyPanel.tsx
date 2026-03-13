import React, { useState, useCallback } from 'react';
import { historicalApi } from '../../services/historical_api';
import { MtmChart } from './MtmChart';
import type { StrategyLeg, MtmPoint } from '../../types/strategy';
import type { HistoricalChainRow } from '../../types/historical';

interface Props {
  legs: StrategyLeg[];
  chain: HistoricalChainRow[];
  onRemoveLeg: (id: string) => void;
  onUpdateQty: (id: string, qty: number) => void;
  onClearAll: () => void;
}

export const StrategyPanel: React.FC<Props> = ({
  legs, chain, onRemoveLeg, onUpdateQty, onClearAll
}) => {
  const [mtmData, setMtmData] = useState<MtmPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const getCurrentPrice = (leg: StrategyLeg) => {
    const row = chain.find(r => r.strike === leg.strike);
    if (!row) return leg.entryPremium;
    return leg.type === 'CE' ? row.call.last_price : row.put.last_price;
  };

  const getLegPnl = (leg: StrategyLeg) => {
    const dir = leg.action === 'BUY' ? 1 : -1;
    return (getCurrentPrice(leg) - leg.entryPremium) * leg.qty * dir;
  };

  const totalPnl = legs.reduce((s, l) => s + getLegPnl(l), 0);

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

      // Union of all timestamps from entry onward
      const timeSet = new Set<number>();
      seriesResults.forEach(({ data }) => data.forEach(d => timeSet.add(d.time)));
      const sortedTimes = Array.from(timeSet).sort((a, b) => a - b);

      const mtmPoints: MtmPoint[] = sortedTimes.map(t => {
        let total = 0;
        seriesResults.forEach(({ leg, data }) => {
          // carry-forward: use the last candle at or before this time
          const pts = data.filter(d => d.time <= t);
          if (pts.length) {
            const pt = pts[pts.length - 1];
            const dir = leg.action === 'BUY' ? 1 : -1;
            total += (pt.close - leg.entryPremium) * leg.qty * dir;
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: '8px' }}>

      {/* Legs card */}
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
                  <th>Qty</th>
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

      {/* MTM chart card */}
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
