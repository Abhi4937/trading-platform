import React, { useState, useEffect } from 'react';
import { useExpiries, useOptionChain } from './hooks/useOptionChain';
import { OptionChainTable } from './components/chain/OptionChainTable';
import { LogViewer } from './components/logs/LogViewer';
import { Spinner } from './components/ui/Spinner';
import './App.css';

export default function App() {
  const { expiries, spot, loading: expLoading } = useExpiries();
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');
  const [showLogs, setShowLogs] = useState(false);

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


      {/* Log Viewer — fixed bottom 1/3 */}
      {showLogs && (
        <div style={{
          position: 'fixed', bottom: 0, left: 0, right: 0,
          height: '33vh', background: '#010409',
          borderTop: '1px solid #1a2d42', zIndex: 100,
          padding: '8px 12px',
        }}>
          <LogViewer />
        </div>
      )}

      {/* Main */}
      <main className="main">
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
      </main>
    </div>
  );
}
