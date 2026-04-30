import React, { useState, useMemo, useEffect } from 'react';
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ReferenceDot, ResponsiveContainer, Legend,
} from 'recharts';
import { bsPrice, bsDelta, bsGamma, bsTheta, bsVega, CONTRACT_VALUE } from '../../utils/marginEngine';
import type { StrategyLeg } from '../../types/strategy';
import type { HistoricalChainRow } from '../../types/historical';

// ─── helpers ──────────────────────────────────────────────────────────────────

const SECS_PER_YEAR = 365 * 24 * 3600;
const NUM_POINTS = 101;
const SPOT_RANGE = 0.30; // ±30%

function expiryTs(expiry: string): number {
  return new Date(`${expiry}T12:00:00Z`).getTime() / 1000;
}
function tsToIstInput(ts: number): string {
  return new Date((ts + 5.5 * 3600) * 1000).toISOString().slice(0, 16);
}
function istInputToTs(val: string): number {
  return Math.floor(new Date(val + ':00+05:30').getTime() / 1000);
}
function fmt(n: number, decimals = 2): string {
  return (n >= 0 ? '+' : '') + n.toFixed(decimals);
}

// ─── types ────────────────────────────────────────────────────────────────────

interface LegIv {
  iv: number;   // decimal (e.g. 0.60)
  missing: boolean;
}

interface PayoffPoint {
  spot: number;
  atExpiry: number;
  today: number | null;
}

interface Breakeven {
  spot: number;
  curve: 'atExpiry' | 'today';
}

// ─── component ────────────────────────────────────────────────────────────────

interface Props {
  legs: StrategyLeg[];
  chain: HistoricalChainRow[];
  legChains: Map<string, HistoricalChainRow[]>;
  spot: number;
  selectedExpiry: string;
  simulationTimestamp: number;
}

export function PayoffGraph({ legs, chain, legChains, spot, selectedExpiry, simulationTimestamp }: Props) {
  const [targetTimestamp, setTargetTimestamp] = useState(simulationTimestamp);
  const [targetSpot, setTargetSpot] = useState(spot);
  const [ivAdjPct, setIvAdjPct] = useState(0);

  // Reset sliders when spot or sim time changes (user navigated to new timestamp)
  useEffect(() => {
    setTargetSpot(spot);
    setTargetTimestamp(simulationTimestamp);
  }, [spot, simulationTimestamp]);

  // Guard: no spot yet
  if (spot <= 0) {
    return <div className="strategy-chart-empty">Waiting for spot price…</div>;
  }

  return (
    <PayoffGraphInner
      legs={legs}
      chain={chain}
      legChains={legChains}
      spot={spot}
      selectedExpiry={selectedExpiry}
      simulationTimestamp={simulationTimestamp}
      targetTimestamp={targetTimestamp}
      setTargetTimestamp={setTargetTimestamp}
      targetSpot={targetSpot}
      setTargetSpot={setTargetSpot}
      ivAdjPct={ivAdjPct}
      setIvAdjPct={setIvAdjPct}
    />
  );
}

// Inner split to keep hooks-before-return valid even with the guard above
function PayoffGraphInner({
  legs, chain, legChains, spot, selectedExpiry, simulationTimestamp,
  targetTimestamp, setTargetTimestamp,
  targetSpot, setTargetSpot,
  ivAdjPct, setIvAdjPct,
}: Props & {
  targetTimestamp: number; setTargetTimestamp: (t: number) => void;
  targetSpot: number; setTargetSpot: (s: number) => void;
  ivAdjPct: number; setIvAdjPct: (v: number) => void;
}) {
  // Latest expiry among all legs (for datetime slider max)
  const latestExpiryTs = useMemo(
    () => legs.length ? Math.max(...legs.map(l => expiryTs(l.expiry))) : expiryTs(selectedExpiry),
    [legs, selectedExpiry],
  );

  // Per-leg IV resolved from chain at simulationTimestamp
  const legIvs = useMemo((): LegIv[] =>
    legs.map(leg => {
      const c = leg.expiry === selectedExpiry ? chain : (legChains.get(leg.expiry) ?? []);
      const row = c.find(r => r.strike === leg.strike);
      const side = leg.type === 'CE' ? row?.call : row?.put;
      const iv = (side?.iv_pct ?? 0) / 100;
      return { iv, missing: iv <= 0 };
    }),
    [legs, chain, legChains, selectedExpiry, simulationTimestamp], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const anyMissing = legIvs.some(l => l.missing);

  // Multi-expiry label
  const uniqueExpiries = useMemo(() => [...new Set(legs.map(l => l.expiry))], [legs]);
  const atExpiryLabel = uniqueExpiries.length > 1 ? 'At Final Expiry' : 'At Expiry';

  // 101-point spot range
  const spotMin = spot * (1 - SPOT_RANGE);
  const spotMax = spot * (1 + SPOT_RANGE);
  const spotStep = (spotMax - spotMin) / (NUM_POINTS - 1);
  const spotRange = useMemo(
    () => Array.from({ length: NUM_POINTS }, (_, i) => spotMin + i * spotStep),
    [spotMin, spotStep], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Payoff curves
  const payoffData = useMemo((): PayoffPoint[] => {
    const ivScale = 1 + ivAdjPct / 100;

    return spotRange.map(S => {
      // At-expiry P&L (intrinsic)
      const atExpiry = legs.reduce((sum, leg) => {
        const intrinsic = leg.type === 'CE' ? Math.max(0, S - leg.strike) : Math.max(0, leg.strike - S);
        const dir = leg.action === 'BUY' ? 1 : -1;
        return sum + (intrinsic - leg.entryPremium) * leg.qty * dir * CONTRACT_VALUE;
      }, 0);

      // Today P&L (BS theoretical)
      let today: number | null = null;
      if (!anyMissing) {
        let sum = 0;
        for (let i = 0; i < legs.length; i++) {
          const leg = legs[i];
          const { iv } = legIvs[i];
          const sigma = Math.max(0.001, iv * ivScale);
          const T_target = Math.max(0, (expiryTs(leg.expiry) - targetTimestamp) / SECS_PER_YEAR);
          const price = bsPrice(S, leg.strike, T_target, 0, sigma, leg.type === 'CE');
          const dir = leg.action === 'BUY' ? 1 : -1;
          sum += (price - leg.entryPremium) * leg.qty * dir * CONTRACT_VALUE;
        }
        today = sum;
      }

      return { spot: Math.round(S), atExpiry, today };
    });
  }, [legs, legIvs, targetTimestamp, ivAdjPct, spotRange, anyMissing]);

  // Breakeven zero-crossings (linear interpolation between adjacent points)
  const breakevenPoints = useMemo((): Breakeven[] => {
    const bps: Breakeven[] = [];
    for (let i = 1; i < payoffData.length; i++) {
      const p = payoffData[i - 1];
      const c = payoffData[i];
      if ((p.atExpiry < 0) !== (c.atExpiry < 0)) {
        const t = -p.atExpiry / (c.atExpiry - p.atExpiry);
        bps.push({ spot: Math.round(p.spot + t * (c.spot - p.spot)), curve: 'atExpiry' });
      }
      if (p.today !== null && c.today !== null && (p.today < 0) !== (c.today < 0)) {
        const t = -p.today / (c.today - p.today);
        bps.push({ spot: Math.round(p.spot + t * (c.spot - p.spot)), curve: 'today' });
      }
    }
    return bps;
  }, [payoffData]);

  // P&L interpolated at targetSpot
  const pnlAtTarget = useMemo(() => {
    const idx = payoffData.findIndex(p => p.spot >= targetSpot);
    if (idx <= 0) {
      const p = payoffData[0] ?? { atExpiry: 0, today: null };
      return { atExpiry: p.atExpiry, today: p.today };
    }
    if (idx >= payoffData.length) {
      const p = payoffData[payoffData.length - 1] ?? { atExpiry: 0, today: null };
      return { atExpiry: p.atExpiry, today: p.today };
    }
    const prev = payoffData[idx - 1];
    const curr = payoffData[idx];
    const t = (targetSpot - prev.spot) / (curr.spot - prev.spot);
    return {
      atExpiry: prev.atExpiry + t * (curr.atExpiry - prev.atExpiry),
      today: (prev.today !== null && curr.today !== null)
        ? prev.today + t * (curr.today - prev.today)
        : null,
    };
  }, [payoffData, targetSpot]);

  // Max/min P&L
  const maxProfit = useMemo(() => Math.max(...payoffData.map(p => p.atExpiry)), [payoffData]);
  const maxLoss   = useMemo(() => Math.min(...payoffData.map(p => p.atExpiry)), [payoffData]);

  // Net Greeks at targetSpot + targetTimestamp + IV adj
  const netGreeks = useMemo(() => {
    if (anyMissing) return null;
    const ivScale = 1 + ivAdjPct / 100;
    let delta = 0, gamma = 0, theta = 0, vega = 0;
    for (let i = 0; i < legs.length; i++) {
      const leg = legs[i];
      const { iv } = legIvs[i];
      const sigma = Math.max(0.001, iv * ivScale);
      const T = Math.max(0, (expiryTs(leg.expiry) - targetTimestamp) / SECS_PER_YEAR);
      const dir = leg.action === 'BUY' ? 1 : -1;
      const scale = leg.qty * CONTRACT_VALUE * dir;
      const isCall = leg.type === 'CE';
      delta += bsDelta(targetSpot, leg.strike, T, 0, sigma, isCall) * scale;
      gamma += bsGamma(targetSpot, leg.strike, T, 0, sigma) * scale;
      theta += bsTheta(targetSpot, leg.strike, T, 0, sigma, isCall) * scale;
      vega  += bsVega(targetSpot, leg.strike, T, 0, sigma) * scale;
    }
    return { delta, gamma, theta, vega };
  }, [legs, legIvs, targetSpot, targetTimestamp, ivAdjPct, anyMissing]);

  // T display for first leg's expiry
  const daysToExpiry = Math.max(0, (expiryTs(legs[0]?.expiry ?? selectedExpiry) - targetTimestamp) / 86400);

  const sliderMin = Math.round(spot * (1 - SPOT_RANGE));
  const sliderMax = Math.round(spot * (1 + SPOT_RANGE));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>

      {/* ── Sliders ── */}
      <div style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', background: 'var(--bg2)', display: 'flex', flexDirection: 'column', gap: '5px', flexShrink: 0 }}>

        {/* Target Date */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '9px', color: 'var(--text3)', textTransform: 'uppercase', width: '76px', flexShrink: 0 }}>Target Date</span>
          <input
            type="datetime-local"
            value={tsToIstInput(targetTimestamp)}
            min={tsToIstInput(simulationTimestamp)}
            max={tsToIstInput(latestExpiryTs)}
            onChange={e => e.target.value && setTargetTimestamp(istInputToTs(e.target.value))}
            style={{ background: 'var(--bg3)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 4, padding: '2px 6px', fontSize: '10px', fontFamily: 'var(--mono)' }}
          />
          <span style={{ fontSize: '10px', color: 'var(--text3)', whiteSpace: 'nowrap' }}>
            T = {daysToExpiry.toFixed(1)}d
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '9px', color: 'var(--text3)', textTransform: 'uppercase', width: '76px', flexShrink: 0 }} />
          <input
            type="range"
            className="payoff-slider"
            min={simulationTimestamp}
            max={latestExpiryTs}
            step={3600}
            value={targetTimestamp}
            onChange={e => setTargetTimestamp(Number(e.target.value))}
          />
          <span style={{ fontSize: '10px', color: 'var(--accent)', fontWeight: 600, width: '90px', textAlign: 'right', flexShrink: 0 }}>
            {tsToIstInput(targetTimestamp).replace('T', ' ')}
          </span>
          {targetTimestamp !== simulationTimestamp && (
            <button
              onClick={() => setTargetTimestamp(simulationTimestamp)}
              style={{ fontSize: '9px', color: 'var(--text3)', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 3, padding: '1px 5px', cursor: 'pointer', flexShrink: 0 }}
            >Reset</button>
          )}
        </div>

        {/* Target Spot */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '9px', color: 'var(--text3)', textTransform: 'uppercase', width: '76px', flexShrink: 0 }}>Target Spot</span>
          <input
            type="range"
            className="payoff-slider"
            min={sliderMin}
            max={sliderMax}
            step={10}
            value={Math.round(targetSpot)}
            onChange={e => setTargetSpot(Number(e.target.value))}
          />
          <span style={{ fontSize: '11px', color: 'var(--gold)', fontWeight: 600, width: '90px', textAlign: 'right', flexShrink: 0 }}>
            ${Math.round(targetSpot).toLocaleString()}
          </span>
          {Math.round(targetSpot) !== Math.round(spot) && (
            <button
              onClick={() => setTargetSpot(spot)}
              style={{ fontSize: '9px', color: 'var(--text3)', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 3, padding: '1px 5px', cursor: 'pointer', flexShrink: 0 }}
            >Reset</button>
          )}
        </div>

        {/* IV Adjustment */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '9px', color: 'var(--text3)', textTransform: 'uppercase', width: '76px', flexShrink: 0 }}>IV Adj</span>
          <input
            type="range"
            className="payoff-slider"
            min={-50}
            max={50}
            step={1}
            value={ivAdjPct}
            onChange={e => setIvAdjPct(Number(e.target.value))}
          />
          <span style={{ fontSize: '11px', fontWeight: 600, width: '60px', textAlign: 'right', flexShrink: 0, color: ivAdjPct > 0 ? 'var(--green)' : ivAdjPct < 0 ? 'var(--red)' : 'var(--text3)' }}>
            {ivAdjPct > 0 ? '+' : ''}{ivAdjPct}%
          </span>
          {ivAdjPct !== 0 && (
            <button
              onClick={() => setIvAdjPct(0)}
              style={{ fontSize: '9px', color: 'var(--text3)', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 3, padding: '1px 5px', cursor: 'pointer', flexShrink: 0 }}
            >Reset</button>
          )}
        </div>
      </div>

      {/* ── P&L Summary ── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0', borderBottom: '1px solid var(--border)', flexShrink: 0, background: 'var(--bg1)' }}>
        <StatCell label={`P&L @ Expiry`} value={`${fmt(pnlAtTarget.atExpiry)} USD`} color={pnlAtTarget.atExpiry >= 0 ? 'var(--green)' : 'var(--red)'} />
        {pnlAtTarget.today !== null && (
          <StatCell label="P&L Today" value={`${fmt(pnlAtTarget.today)} USD`} color={pnlAtTarget.today >= 0 ? 'var(--green)' : 'var(--red)'} />
        )}
        <Divider />
        <StatCell label="Max Profit" value={maxProfit > 1e9 ? '∞' : `${fmt(maxProfit)} USD`} color="var(--green)" />
        <StatCell label="Max Loss" value={maxLoss < -1e9 ? '-∞' : `${fmt(maxLoss)} USD`} color="var(--red)" />
        {breakevenPoints.filter(b => b.curve === 'atExpiry').length > 0 && (
          <>
            <Divider />
            <StatCell
              label="Breakevens"
              value={breakevenPoints.filter(b => b.curve === 'atExpiry').map(b => `$${b.spot.toLocaleString()}`).join(', ')}
              color="var(--text)"
            />
          </>
        )}
      </div>

      {/* ── IV warning ── */}
      {anyMissing && (
        <div style={{ padding: '4px 10px', fontSize: '10px', color: 'var(--gold)', background: 'rgba(240,180,41,0.08)', borderBottom: '1px solid rgba(240,180,41,0.2)', flexShrink: 0 }}>
          ⚠ {legIvs.filter(l => l.missing).length} leg(s) have no IV at this timestamp — "Today" curve hidden
        </div>
      )}

      {/* ── Chart ── */}
      <div style={{ flex: 1, minHeight: 180 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={payoffData} margin={{ top: 8, right: 24, left: 0, bottom: 4 }}>
            <CartesianGrid stroke="#1a2d42" strokeDasharray="3 3" />

            <XAxis
              dataKey="spot"
              type="number"
              scale="linear"
              domain={[Math.round(spotMin), Math.round(spotMax)]}
              tickFormatter={v => `${(Number(v) / 1000).toFixed(0)}k`}
              tick={{ fill: '#4a6a85', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
              tickCount={7}
              label={{ value: 'BTC Spot (USDT)', position: 'insideBottom', offset: -2, fill: '#4a6a85', fontSize: 9 }}
            />

            <YAxis
              tick={{ fill: '#4a6a85', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
              tickFormatter={v => {
                const n = Number(v);
                if (Math.abs(n) >= 1000) return `${n >= 0 ? '+' : ''}${(n / 1000).toFixed(1)}k`;
                return `${n >= 0 ? '+' : ''}${n.toFixed(0)}`;
              }}
              width={50}
            />

            {/* Zero reference */}
            <ReferenceLine y={0} stroke="#4a6a85" strokeDasharray="3 3" strokeWidth={1} />

            {/* Target spot */}
            <ReferenceLine
              x={Math.round(targetSpot)}
              stroke="#f0b429"
              strokeDasharray="4 2"
              strokeWidth={1.5}
              label={{ value: `$${Math.round(targetSpot).toLocaleString()}`, position: 'insideTopRight', fill: '#f0b429', fontSize: 9 }}
            />

            {/* Breakeven markers (limit labels to 4) */}
            {breakevenPoints.slice(0, 6).map((bp, i) => (
              <ReferenceDot
                key={`be-${i}`}
                x={bp.spot}
                y={0}
                r={4}
                fill={bp.curve === 'atExpiry' ? '#00e5a0' : '#00d4ff'}
                stroke="#050a0f"
                strokeWidth={1}
                label={i < 4 ? { value: `${(bp.spot / 1000).toFixed(1)}k`, position: 'top', fill: bp.curve === 'atExpiry' ? '#00e5a0' : '#00d4ff', fontSize: 8 } : undefined}
              />
            ))}

            {/* At Expiry line */}
            <Line
              type="monotone"
              dataKey="atExpiry"
              stroke="#00e5a0"
              strokeWidth={2}
              dot={false}
              name={atExpiryLabel}
              connectNulls
              isAnimationActive={false}
            />

            {/* Today line */}
            {!anyMissing && (
              <Line
                type="monotone"
                dataKey="today"
                stroke="#00d4ff"
                strokeWidth={2}
                strokeDasharray="5 2"
                dot={false}
                name="Today"
                connectNulls={false}
                isAnimationActive={false}
              />
            )}

            <Legend
              wrapperStyle={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace', paddingTop: 2 }}
              formatter={(value) => <span style={{ color: value === 'Today' ? '#00d4ff' : '#00e5a0' }}>{value}</span>}
            />

            <Tooltip
              contentStyle={{ background: '#0c1420', border: '1px solid #1a2d42', borderRadius: 6, fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}
              labelStyle={{ color: '#7a9bb5' }}
              labelFormatter={v => `BTC $${Number(v).toLocaleString()}`}
              formatter={(value: unknown, name: string) => {
                const n = Number(value);
                return [`${fmt(n)} USD`, name];
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* ── Greeks ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0', padding: '4px 10px', borderTop: '1px solid var(--border)', flexShrink: 0, flexWrap: 'wrap', background: 'var(--bg2)' }}>
        <span style={{ fontSize: '9px', color: 'var(--text3)', textTransform: 'uppercase', marginRight: '10px', letterSpacing: '0.6px' }}>
          Greeks @ ${Math.round(targetSpot).toLocaleString()}
        </span>
        {netGreeks ? (
          <>
            <GreekCell label="Δ" value={netGreeks.delta} unit="BTC" decimals={4} />
            <GreekCell label="Γ" value={netGreeks.gamma} unit="" decimals={5} />
            <GreekCell label="θ/day" value={netGreeks.theta} unit="USD" decimals={2} />
            <GreekCell label="ν/1%" value={netGreeks.vega} unit="USD" decimals={2} />
          </>
        ) : (
          <span style={{ fontSize: '10px', color: 'var(--text3)' }}>
            IV missing — Greeks unavailable
          </span>
        )}
      </div>
    </div>
  );
}

// ─── small display helpers ────────────────────────────────────────────────────

function StatCell({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', padding: '4px 10px', gap: '1px' }}>
      <span style={{ fontSize: '9px', color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</span>
      <span style={{ fontSize: '11px', fontWeight: 600, color, fontFamily: 'var(--mono)' }}>{value}</span>
    </div>
  );
}

function Divider() {
  return <div style={{ width: 1, background: 'var(--border)', margin: '4px 0', flexShrink: 0 }} />;
}

function GreekCell({ label, value, unit, decimals }: { label: string; value: number; unit: string; decimals: number }) {
  const positive = value >= 0;
  const color = label === 'θ/day'
    ? (value < 0 ? 'var(--red)' : 'var(--green)')
    : (positive ? 'var(--green)' : 'var(--red)');
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '3px', marginRight: '14px' }}>
      <span style={{ fontSize: '10px', color: 'var(--text3)' }}>{label}</span>
      <span style={{ fontSize: '11px', fontWeight: 600, color, fontFamily: 'var(--mono)' }}>
        {fmt(value, decimals)}{unit ? ` ${unit}` : ''}
      </span>
    </div>
  );
}
