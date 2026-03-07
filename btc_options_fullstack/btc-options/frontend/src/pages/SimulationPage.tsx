import React, { useState, useEffect, useRef, useCallback } from 'react';
import { OptionChainTable } from '../components/chain/OptionChainTable';
import { Spinner } from '../components/ui/Spinner';
import type { ChainRow } from '../types/api';

interface HistoricalChain {
  expiry: string;
  snapshot_time: string;
  spot_price: number;
  atm_strike: number;
  days_to_expiry: number;
  atm_iv_call: number;
  atm_iv_put: number;
  chain: ChainRow[];
}

const INTERVALS = [
  { label: '1 min', value: 1 },
  { label: '5 min', value: 5 },
  { label: '10 min', value: 10 },
];

const SPEEDS = [
  { label: '1×', value: 1000 },
  { label: '2×', value: 500 },
  { label: '5×', value: 200 },
  { label: '10×', value: 100 },
];

export default function SimulationPage() {
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [expiries, setExpiries] = useState<string[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState('');
  const [interval, setInterval_] = useState(5);
  const [timestamps, setTimestamps] = useState<string[]>([]);
  const [tsIndex, setTsIndex] = useState(0);
  const [chain, setChain] = useState<HistoricalChain | null>(null);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1000);
  const [error, setError] = useState('');
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load available dates
  useEffect(() => {
    fetch('/api/v1/historical/dates')
      .then(r => r.json())
      .then(d => {
        setDates(d.dates || []);
        if (d.dates?.length) setSelectedDate(d.dates[0]);
      })
      .catch(() => setError('TimescaleDB not connected — start recording first'));
  }, []);

  // Load expiries for selected date
  useEffect(() => {
    if (!selectedDate) return;
    setExpiries([]);
    setSelectedExpiry('');
    setTimestamps([]);
    setChain(null);
    fetch(`/api/v1/historical/expiries?date=${selectedDate}`)
      .then(r => r.json())
      .then(d => {
        setExpiries(d.expiries || []);
        if (d.expiries?.length) setSelectedExpiry(d.expiries[0]);
      });
  }, [selectedDate]);

  // Load timestamps for selected date + expiry + interval
  useEffect(() => {
    if (!selectedDate || !selectedExpiry) return;
    setTimestamps([]);
    setTsIndex(0);
    setChain(null);
    setPlaying(false);
    fetch(`/api/v1/historical/times?date=${selectedDate}&expiry=${selectedExpiry}&interval=${interval}`)
      .then(r => r.json())
      .then(d => {
        setTimestamps(d.timestamps || []);
        setTsIndex(0);
      });
  }, [selectedDate, selectedExpiry, interval]);

  const fetchChain = useCallback(async (idx: number) => {
    if (!timestamps[idx] || !selectedExpiry) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(
        `/api/v1/historical/chain?expiry=${selectedExpiry}&ts=${encodeURIComponent(timestamps[idx])}`
      );
      if (!res.ok) throw new Error(await res.text());
      const data: HistoricalChain = await res.json();
      setChain(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load chain');
    } finally {
      setLoading(false);
    }
  }, [timestamps, selectedExpiry]);

  // Fetch chain when index changes
  useEffect(() => {
    if (timestamps.length) fetchChain(tsIndex);
  }, [tsIndex, fetchChain]);

  // Playback timer
  useEffect(() => {
    if (!playing) {
      if (timerRef.current) clearTimeout(timerRef.current);
      return;
    }
    timerRef.current = setTimeout(() => {
      setTsIndex(prev => {
        if (prev >= timestamps.length - 1) {
          setPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, speed);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [playing, tsIndex, timestamps.length, speed]);

  const fmtTs = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
    } catch { return iso; }
  };

  const fmtDate = (iso: string) => {
    try { return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }); }
    catch { return iso; }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#010409', color: '#c9d1d9' }}>

      {/* Controls bar */}
      <div style={{
        display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12,
        padding: '10px 16px', background: '#0d1117', borderBottom: '1px solid #1a2d42',
      }}>
        {/* Date picker */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: '#8b949e' }}>DATE</span>
          <input
            type="date"
            value={selectedDate}
            onChange={e => setSelectedDate(e.target.value)}
            style={{
              background: '#161b22', border: '1px solid #30363d', color: '#c9d1d9',
              borderRadius: 4, padding: '3px 8px', fontSize: 13,
            }}
          />
        </div>

        {/* Expiry */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: '#8b949e' }}>EXPIRY</span>
          <select
            value={selectedExpiry}
            onChange={e => setSelectedExpiry(e.target.value)}
            style={{
              background: '#161b22', border: '1px solid #30363d', color: '#c9d1d9',
              borderRadius: 4, padding: '3px 8px', fontSize: 13,
            }}
          >
            {expiries.length === 0 && <option value="">— no data —</option>}
            {expiries.map(e => <option key={e} value={e}>{fmtDate(e)}</option>)}
          </select>
        </div>

        {/* Interval */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: '#8b949e' }}>INTERVAL</span>
          <div style={{ display: 'flex', gap: 4 }}>
            {INTERVALS.map(iv => (
              <button
                key={iv.value}
                onClick={() => setInterval_(iv.value)}
                style={{
                  padding: '3px 10px', fontSize: 12, borderRadius: 4, cursor: 'pointer',
                  background: interval === iv.value ? '#1f6feb' : '#161b22',
                  border: `1px solid ${interval === iv.value ? '#1f6feb' : '#30363d'}`,
                  color: '#c9d1d9',
                }}
              >{iv.label}</button>
            ))}
          </div>
        </div>

        <div style={{ width: 1, height: 24, background: '#30363d' }} />

        {/* Playback controls */}
        <button
          onClick={() => { setTsIndex(0); setPlaying(false); }}
          disabled={!timestamps.length}
          title="Restart"
          style={{ ...btnStyle, fontSize: 16 }}
        >⏮</button>

        <button
          onClick={() => setTsIndex(p => Math.max(0, p - 1))}
          disabled={tsIndex === 0}
          style={{ ...btnStyle, fontSize: 16 }}
        >⏪</button>

        <button
          onClick={() => setPlaying(p => !p)}
          disabled={!timestamps.length || tsIndex >= timestamps.length - 1}
          style={{ ...btnStyle, fontSize: 16, minWidth: 40, background: playing ? '#388bfd22' : undefined }}
        >{playing ? '⏸' : '▶'}</button>

        <button
          onClick={() => setTsIndex(p => Math.min(timestamps.length - 1, p + 1))}
          disabled={tsIndex >= timestamps.length - 1}
          style={{ ...btnStyle, fontSize: 16 }}
        >⏩</button>

        {/* Speed */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ fontSize: 11, color: '#8b949e' }}>SPEED</span>
          {SPEEDS.map(s => (
            <button
              key={s.value}
              onClick={() => setSpeed(s.value)}
              style={{
                padding: '2px 8px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
                background: speed === s.value ? '#238636' : '#161b22',
                border: `1px solid ${speed === s.value ? '#238636' : '#30363d'}`,
                color: '#c9d1d9',
              }}
            >{s.label}</button>
          ))}
        </div>

        {/* Current time display */}
        {timestamps.length > 0 && (
          <div style={{ marginLeft: 'auto', fontSize: 13, color: '#58a6ff', fontWeight: 600, fontFamily: 'monospace' }}>
            {fmtTs(timestamps[tsIndex])} IST
            <span style={{ marginLeft: 8, color: '#8b949e', fontWeight: 400 }}>
              [{tsIndex + 1}/{timestamps.length}]
            </span>
          </div>
        )}
      </div>

      {/* Scrubber */}
      {timestamps.length > 1 && (
        <div style={{ padding: '6px 16px', background: '#0d1117', borderBottom: '1px solid #1a2d42' }}>
          <input
            type="range"
            min={0}
            max={timestamps.length - 1}
            value={tsIndex}
            onChange={e => { setPlaying(false); setTsIndex(Number(e.target.value)); }}
            style={{ width: '100%', accentColor: '#1f6feb' }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#8b949e', marginTop: 2 }}>
            <span>{fmtTs(timestamps[0])}</span>
            <span>{fmtTs(timestamps[timestamps.length - 1])}</span>
          </div>
        </div>
      )}

      {/* Chain meta */}
      {chain && (
        <div style={{
          padding: '6px 16px', background: '#010409', borderBottom: '1px solid #1a2d42',
          display: 'flex', gap: 16, fontSize: 12, color: '#8b949e',
        }}>
          <span>Spot: <strong style={{ color: '#c9d1d9' }}>${chain.spot_price.toLocaleString()}</strong></span>
          <span>ATM: <strong style={{ color: '#c9d1d9' }}>${chain.atm_strike.toLocaleString()}</strong></span>
          <span>IV: <strong style={{ color: '#c9d1d9' }}>{chain.atm_iv_call.toFixed(1)}%C / {chain.atm_iv_put.toFixed(1)}%P</strong></span>
          <span>DTE: <strong style={{ color: '#c9d1d9' }}>{chain.days_to_expiry.toFixed(1)}d</strong></span>
          <span style={{ marginLeft: 'auto', color: '#58a6ff' }}>
            {new Date(chain.snapshot_time).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })} IST
          </span>
        </div>
      )}

      {/* Main content */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {error && (
          <div style={{ padding: 16, color: '#f85149', background: '#160d0d', borderBottom: '1px solid #6e2020', fontSize: 13 }}>
            {error}
          </div>
        )}

        {!selectedDate && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8, color: '#8b949e' }}>
            <div style={{ fontSize: 40 }}>📊</div>
            <div>Select a date to load historical data</div>
            <div style={{ fontSize: 12 }}>Data is recorded automatically from live WS feed</div>
          </div>
        )}

        {selectedDate && timestamps.length === 0 && !loading && !error && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#8b949e' }}>
            No data recorded for this date / expiry
          </div>
        )}

        {loading && !chain && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8 }}>
            <Spinner /><span>Loading snapshot...</span>
          </div>
        )}

        {chain && (
          <div style={{ height: '100%', overflow: 'auto' }}>
            <OptionChainTable
              chain={chain.chain}
              spotPrice={chain.spot_price}
              atmStrike={chain.atm_strike}
            />
          </div>
        )}
      </div>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #30363d',
  color: '#c9d1d9',
  borderRadius: 4,
  padding: '3px 10px',
  cursor: 'pointer',
};
