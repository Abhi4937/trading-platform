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
  { label: '1 min',  value: 1 },
  { label: '5 min',  value: 5 },
  { label: '15 min', value: 15 },
  { label: '30 min', value: 30 },
  { label: '1 hr',   value: 60 },
];

const SPEEDS = [
  { label: '1×',  value: 1000 },
  { label: '2×',  value: 500 },
  { label: '5×',  value: 200 },
  { label: '10×', value: 100 },
];

function fmtTs(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString('en-IN', {
      hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata',
    });
  } catch { return iso; }
}

function fmtExpiry(iso: string, refDate: string) {
  try {
    const exp  = new Date(iso + 'T00:00:00Z');
    const base = new Date(refDate + 'T00:00:00Z');
    const diff = Math.round((exp.getTime() - base.getTime()) / 86400000);
    const label = exp.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit', timeZone: 'UTC' });
    if (diff === 0) return `${label} (Same day)`;
    if (diff === 1) return `${label} (Next day)`;
    if (diff === 2) return `${label} (Day after)`;
    if (diff === 3) return `${label} (+3d)`;
    if (diff <= 7)  return `${label} (Weekly)`;
    if (diff <= 14) return `${label} (Next weekly)`;
    return `${label} (${diff}d)`;
  } catch { return iso; }
}

export default function SimulationPage() {
  const [dates, setDates]               = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [expiries, setExpiries]         = useState<string[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState('');
  const [interval, setInterval_]        = useState(5);
  const [timestamps, setTimestamps]     = useState<string[]>([]);
  const [tsIndex, setTsIndex]           = useState(0);
  const [chain, setChain]               = useState<HistoricalChain | null>(null);
  const [loading, setLoading]           = useState(false);
  const [playing, setPlaying]           = useState(false);
  const [speed, setSpeed]               = useState(1000);
  const [error, setError]               = useState('');
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Backfill state
  const [bfDate, setBfDate]   = useState('');
  const [bfRes,  setBfRes]    = useState('1h');
  const [bfStatus, setBfStatus] = useState<{
    running: boolean; done: number; total: number; status: string; errors: number;
  } | null>(null);
  const bfPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Load available dates ──────────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/v1/historical/dates')
      .then(r => r.json())
      .then(d => {
        const list: string[] = d.dates || [];
        setDates(list);
        if (list.length) setSelectedDate(list[0]);
      })
      .catch(() => setError('TimescaleDB not connected — start recording first'));
  }, []);

  // ── Load expiries when date changes ───────────────────────────────────────
  useEffect(() => {
    if (!selectedDate) return;
    setExpiries([]);
    setSelectedExpiry('');
    setTimestamps([]);
    setChain(null);
    fetch(`/api/v1/historical/expiries?date=${selectedDate}`)
      .then(r => r.json())
      .then(d => {
        const list: string[] = d.expiries || [];
        setExpiries(list);
        if (list.length) setSelectedExpiry(list[0]);
      });
  }, [selectedDate]);

  // ── Load timestamps when date / expiry / interval changes ────────────────
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

  // ── Fetch chain snapshot ──────────────────────────────────────────────────
  const fetchChain = useCallback(async (idx: number) => {
    if (!timestamps[idx] || !selectedExpiry) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(
        `/api/v1/historical/chain?expiry=${selectedExpiry}&ts=${encodeURIComponent(timestamps[idx])}`
      );
      if (!res.ok) throw new Error(await res.text());
      setChain(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load chain');
    } finally {
      setLoading(false);
    }
  }, [timestamps, selectedExpiry]);

  useEffect(() => {
    if (timestamps.length) fetchChain(tsIndex);
  }, [tsIndex, fetchChain]);

  // ── Playback timer ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!playing) {
      if (timerRef.current) clearTimeout(timerRef.current);
      return;
    }
    timerRef.current = setTimeout(() => {
      setTsIndex(prev => {
        if (prev >= timestamps.length - 1) { setPlaying(false); return prev; }
        return prev + 1;
      });
    }, speed);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [playing, tsIndex, timestamps.length, speed]);

  // ── Backfill ──────────────────────────────────────────────────────────────
  const startBackfill = async () => {
    if (!bfDate) return;
    const res = await fetch('/api/v1/historical/backfill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: bfDate, resolution: bfRes, strike_count: 20, strike_interval: 200 }),
    });
    if (!res.ok) { alert(await res.text()); return; }
    if (bfPollRef.current) clearInterval(bfPollRef.current);
    bfPollRef.current = setInterval(async () => {
      const s = await fetch('/api/v1/historical/backfill/status').then(r => r.json());
      setBfStatus(s);
      if (!s.running) {
        if (bfPollRef.current) clearInterval(bfPollRef.current);
        fetch('/api/v1/historical/dates').then(r => r.json()).then(d => {
          const list: string[] = d.dates || [];
          setDates(list);
        });
      }
    }, 1000);
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#010409', color: '#c9d1d9' }}>

      {/* ── Controls bar ── */}
      <div style={{
        display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12,
        padding: '10px 16px', background: '#0d1117', borderBottom: '1px solid #1a2d42',
      }}>

        {/* Date — dropdown of recorded dates only */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={labelStyle}>DATE</span>
          <select
            value={selectedDate}
            onChange={e => setSelectedDate(e.target.value)}
            style={selStyle}
          >
            {dates.length === 0 && <option value="">— no data —</option>}
            {dates.map(d => (
              <option key={d} value={d}>
                {new Date(d + 'T00:00:00Z').toLocaleDateString('en-IN', {
                  day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC',
                })}
              </option>
            ))}
          </select>
        </div>

        {/* Expiry — with relative labels */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={labelStyle}>EXPIRY</span>
          <select
            value={selectedExpiry}
            onChange={e => setSelectedExpiry(e.target.value)}
            style={{ ...selStyle, minWidth: 180 }}
          >
            {expiries.length === 0 && <option value="">— no data —</option>}
            {expiries.map(e => (
              <option key={e} value={e}>{fmtExpiry(e, selectedDate)}</option>
            ))}
          </select>
        </div>

        {/* Interval */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={labelStyle}>INTERVAL</span>
          <div style={{ display: 'flex', gap: 3 }}>
            {INTERVALS.map(iv => (
              <button key={iv.value} onClick={() => setInterval_(iv.value)} style={{
                ...btnStyle,
                background: interval === iv.value ? '#1f6feb' : '#161b22',
                border: `1px solid ${interval === iv.value ? '#1f6feb' : '#30363d'}`,
                fontSize: 11, padding: '2px 8px',
              }}>{iv.label}</button>
            ))}
          </div>
        </div>

        <div style={{ width: 1, height: 24, background: '#30363d' }} />

        {/* Playback */}
        <button onClick={() => { setTsIndex(0); setPlaying(false); }} disabled={!timestamps.length} style={{ ...btnStyle, fontSize: 15 }} title="Restart">⏮</button>
        <button onClick={() => setTsIndex(p => Math.max(0, p - 1))} disabled={tsIndex === 0} style={{ ...btnStyle, fontSize: 15 }}>⏪</button>
        <button
          onClick={() => setPlaying(p => !p)}
          disabled={!timestamps.length || tsIndex >= timestamps.length - 1}
          style={{ ...btnStyle, fontSize: 15, minWidth: 38, background: playing ? '#388bfd22' : undefined }}
        >{playing ? '⏸' : '▶'}</button>
        <button onClick={() => setTsIndex(p => Math.min(timestamps.length - 1, p + 1))} disabled={tsIndex >= timestamps.length - 1} style={{ ...btnStyle, fontSize: 15 }}>⏩</button>

        {/* Speed */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <span style={labelStyle}>SPEED</span>
          {SPEEDS.map(s => (
            <button key={s.value} onClick={() => setSpeed(s.value)} style={{
              ...btnStyle, fontSize: 11, padding: '2px 7px',
              background: speed === s.value ? '#238636' : '#161b22',
              border: `1px solid ${speed === s.value ? '#238636' : '#30363d'}`,
            }}>{s.label}</button>
          ))}
        </div>

        {/* Current timestamp */}
        {timestamps.length > 0 && (
          <div style={{ marginLeft: 'auto', fontSize: 13, color: '#58a6ff', fontWeight: 600, fontFamily: 'monospace' }}>
            {fmtTs(timestamps[tsIndex])} IST
            <span style={{ marginLeft: 8, color: '#8b949e', fontWeight: 400 }}>
              [{tsIndex + 1}/{timestamps.length}]
            </span>
          </div>
        )}
      </div>

      {/* ── Scrubber ── */}
      {timestamps.length > 1 && (
        <div style={{ padding: '6px 16px', background: '#0d1117', borderBottom: '1px solid #1a2d42' }}>
          <input
            type="range" min={0} max={timestamps.length - 1} value={tsIndex}
            onChange={e => { setPlaying(false); setTsIndex(Number(e.target.value)); }}
            style={{ width: '100%', accentColor: '#1f6feb' }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#8b949e', marginTop: 2 }}>
            <span>{fmtTs(timestamps[0])}</span>
            <span>{fmtTs(timestamps[timestamps.length - 1])}</span>
          </div>
        </div>
      )}

      {/* ── Chain meta ── */}
      {chain && (
        <div style={{
          padding: '5px 16px', background: '#010409', borderBottom: '1px solid #1a2d42',
          display: 'flex', gap: 16, fontSize: 12, color: '#8b949e', alignItems: 'center',
        }}>
          <span>Spot: <strong style={{ color: '#c9d1d9' }}>${chain.spot_price.toLocaleString()}</strong></span>
          <span>ATM: <strong style={{ color: '#c9d1d9' }}>${chain.atm_strike.toLocaleString()}</strong></span>
          <span>IV Call: <strong style={{ color: '#3fb950' }}>{chain.atm_iv_call.toFixed(1)}%</strong></span>
          <span>IV Put: <strong style={{ color: '#f85149' }}>{chain.atm_iv_put.toFixed(1)}%</strong></span>
          <span>DTE: <strong style={{ color: '#c9d1d9' }}>{chain.days_to_expiry.toFixed(1)}d</strong></span>
          <span style={{ marginLeft: 'auto', color: '#58a6ff', fontFamily: 'monospace' }}>
            {new Date(chain.snapshot_time).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })} IST
          </span>
        </div>
      )}

      {/* ── Main content ── */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {error && (
          <div style={{ padding: 12, color: '#f85149', background: '#160d0d', borderBottom: '1px solid #6e2020', fontSize: 13 }}>
            {error}
          </div>
        )}
        {!selectedDate && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8, color: '#8b949e' }}>
            <div style={{ fontSize: 36 }}>📊</div>
            <div>No historical data yet</div>
            <div style={{ fontSize: 12 }}>Use the Backfill panel below to fetch past data from Delta</div>
          </div>
        )}
        {selectedDate && timestamps.length === 0 && !loading && !error && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#8b949e' }}>
            No snapshots for this date / expiry / interval
          </div>
        )}
        {loading && !chain && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8 }}>
            <Spinner /><span>Loading snapshot...</span>
          </div>
        )}
        {chain && (
          <div style={{ height: '100%', overflow: 'auto' }}>
            <OptionChainTable chain={chain.chain} spotPrice={chain.spot_price} atmStrike={chain.atm_strike} />
          </div>
        )}
      </div>

      {/* ── Backfill panel ── */}
      <div style={{
        borderTop: '1px solid #1a2d42', padding: '7px 16px',
        background: '#0d1117', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      }}>
        <span style={{ fontSize: 11, color: '#8b949e', fontWeight: 600, letterSpacing: 1 }}>BACKFILL</span>
        <input
          type="date" value={bfDate} onChange={e => setBfDate(e.target.value)}
          style={{ ...selStyle, fontSize: 12 }}
        />
        <select value={bfRes} onChange={e => setBfRes(e.target.value)} style={{ ...selStyle, fontSize: 12 }}>
          {['1m','5m','15m','30m','1h','4h','1d'].map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <button
          onClick={startBackfill}
          disabled={!bfDate || (bfStatus?.running ?? false)}
          style={{ ...btnStyle, background: '#238636', border: '1px solid #238636', fontSize: 12, padding: '3px 14px' }}
        >{bfStatus?.running ? `Fetching... ${bfStatus.done}/${bfStatus.total}` : 'Fetch & Store'}</button>
        {bfStatus && !bfStatus.running && (
          <span style={{ fontSize: 12, color: '#3fb950' }}>{bfStatus.status}</span>
        )}
      </div>
    </div>
  );
}

const labelStyle: React.CSSProperties = { fontSize: 11, color: '#8b949e' };
const selStyle: React.CSSProperties = {
  background: '#161b22', border: '1px solid #30363d', color: '#c9d1d9',
  borderRadius: 4, padding: '3px 8px', fontSize: 13,
};
const btnStyle: React.CSSProperties = {
  background: '#161b22', border: '1px solid #30363d', color: '#c9d1d9',
  borderRadius: 4, padding: '3px 10px', cursor: 'pointer',
};
