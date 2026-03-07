import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useExpiries, useOptionChain } from './hooks/useOptionChain';
import { OptionChainTable } from './components/chain/OptionChainTable';
import { LogViewer } from './components/logs/LogViewer';
import { Spinner } from './components/ui/Spinner';
import SimulationPage from './pages/SimulationPage';
import './App.css';

export default function App() {
  const [page, setPage] = useState<'live' | 'simulation'>('live');
  const { expiries, spot, loading: expLoading } = useExpiries();
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');
  const [showLogs, setShowLogs] = useState(false);
  const [logHeight, setLogHeight] = useState(window.innerHeight / 3);
  const dragging = useRef(false);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    dragging.current = true;
    e.preventDefault();
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const newHeight = window.innerHeight - e.clientY;
      setLogHeight(Math.min(Math.max(newHeight, 100), window.innerHeight * 0.8));
    };
    const onUp = () => { dragging.current = false; };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, []);

  const { data: chain, loading: chainLoading, error, refetch } = useOptionChain(selectedExpiry, true);

  useEffect(() => {
    if (expiries.length > 0 && !selectedExpiry) {
      setSelectedExpiry(expiries[0].date);
    }
  }, [expiries]);


  return (
    <div className="app">
      {/* Top Bar */}
      <header className="topbar">
        <div className="logo">DELTA <span>BTC Options</span></div>

        {/* Page nav */}
        <div style={{ display: 'flex', gap: 4, marginRight: 8 }}>
          {(['live', 'simulation'] as const).map(p => (
            <button
              key={p}
              onClick={() => setPage(p)}
              style={{
                padding: '4px 14px', fontSize: 12, borderRadius: 4, cursor: 'pointer',
                background: page === p ? '#1f6feb' : 'transparent',
                border: `1px solid ${page === p ? '#1f6feb' : '#30363d'}`,
                color: page === p ? '#fff' : '#8b949e', textTransform: 'capitalize',
              }}
            >{p === 'simulation' ? 'Simulation' : 'Live Chain'}</button>
          ))}
        </div>

        <div className="spot-block">
          <div className="spot-price">${spot.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
          <div className="spot-label">BTC/USD</div>
        </div>

        <div className="sep" />

        <div className="ctrl-group">
          <label className="ctrl-label">Expiry</label>
          <select
            className="sel-input"
            value={selectedExpiry}
            onChange={e => setSelectedExpiry(e.target.value)}
          >
            {expLoading && <option>Loading...</option>}
            {expiries.map(e => (
              <option key={e.date} value={e.date}>{e.label}</option>
            ))}
          </select>
        </div>

        <button
          className="btn-refresh"
          onClick={() => setShowLogs(v => !v)}
          style={{ background: showLogs ? '#1f6feb' : undefined }}
        >
          {showLogs ? '✕ Logs' : '📋 Logs'}
        </button>

        <button className="btn-refresh" onClick={refetch} disabled={chainLoading}>
          {chainLoading ? <Spinner size={14} /> : '↻ Refresh'}
        </button>


        <div className="status">
          <div className={`status-dot ${chainLoading ? 'loading' : 'live'}`} />
          <span>{chainLoading ? 'Loading...' : 'Live'}</span>
        </div>

        {chain && (
          <div className="chain-meta">
            ATM: ${chain.atm_strike.toLocaleString()} ·
            IV: {chain.atm_iv_call.toFixed(1)}%C / {chain.atm_iv_put.toFixed(1)}%P ·
            DTE: {chain.days_to_expiry.toFixed(0)}d
          </div>
        )}
      </header>


      {/* Log Viewer — fixed bottom, resizable */}
      {showLogs && (
        <div style={{
          position: 'fixed', bottom: 0, left: 0, right: 0,
          height: logHeight, background: '#010409',
          borderTop: '1px solid #1a2d42', zIndex: 100,
          display: 'flex', flexDirection: 'column',
        }}>
          {/* Drag handle */}
          <div
            onMouseDown={onMouseDown}
            style={{
              height: 6, cursor: 'ns-resize', flexShrink: 0,
              background: 'linear-gradient(to bottom, #1a2d42, transparent)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <div style={{ width: 40, height: 3, borderRadius: 2, background: '#243a52' }} />
          </div>
          <div style={{ flex: 1, overflow: 'hidden', padding: '4px 12px 8px' }}>
            <LogViewer />
          </div>
        </div>
      )}

      {/* Main */}
      <main className="main" style={{ paddingBottom: showLogs ? logHeight : 0 }}>
        {page === 'simulation' ? (
          <SimulationPage />
        ) : (
          <section className="chain-panel" style={{ width: '100%' }}>
            {error && <div className="error-bar">{error} — showing demo data</div>}

            {chainLoading && !chain
              ? <div className="loading-center"><Spinner /><span>Loading chain...</span></div>
              : chain
                ? <OptionChainTable
                    chain={chain.chain}
                    spotPrice={chain.spot_price}
                    atmStrike={chain.atm_strike}
                  />
                : <div className="loading-center">Select an expiry</div>
            }
          </section>
        )}
      </main>
    </div>
  );
}
