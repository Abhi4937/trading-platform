import React, { useState, useEffect } from 'react';
import { useExpiries, useOptionChain } from './hooks/useOptionChain';
import { OptionChainTable } from './components/chain/OptionChainTable';
import { PremiumChart } from './components/charts/PremiumChart';
import { IVChart } from './components/charts/IVChart';
import { LogViewer } from './components/logs/LogViewer';
import { Spinner } from './components/ui/Spinner';
import type { OptionLeg } from './types/api';
import './App.css';

export default function App() {
  const { expiries, spot, loading: expLoading } = useExpiries();
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');
  const [selectedLeg, setSelectedLeg] = useState<OptionLeg | null>(null);
  const [tab, setTab] = useState<'chain' | 'premium' | 'iv'>('chain');
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
            onChange={e => { setSelectedExpiry(e.target.value); setSelectedLeg(null); }}
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

      {/* Log Viewer Panel */}
      {showLogs && (
        <div style={{ height: 380, padding: '0 12px 12px', background: '#010409' }}>
          <LogViewer />
        </div>
      )}

      {/* Main */}
      <main className="main">
        {/* Left: Option Chain */}
        <section className="chain-panel">
          <div className="panel-tabs">
            <button className={`ptab ${tab==='chain'?'active':''}`} onClick={()=>setTab('chain')}>Option Chain</button>
            <button className={`ptab ${tab==='premium'?'active':''}`} onClick={()=>setTab('premium')}>Premium</button>
            <button className={`ptab ${tab==='iv'?'active':''}`} onClick={()=>setTab('iv')}>IV Charts</button>
          </div>

          {error && <div className="error-bar">{error} — showing demo data</div>}

          {tab === 'chain' && (
            chainLoading && !chain
              ? <div className="loading-center"><Spinner /><span>Loading chain...</span></div>
              : chain
                ? <OptionChainTable
                    chain={chain.chain}
                    spotPrice={chain.spot_price}
                    atmStrike={chain.atm_strike}
                    onSelectLeg={leg => { setSelectedLeg(leg); setTab('premium'); }}
                  />
                : <div className="loading-center">Select an expiry</div>
          )}

          {tab === 'premium' && (
            <PremiumChart expiry={selectedExpiry} selectedLeg={selectedLeg} />
          )}

          {tab === 'iv' && chain && (
            <IVChart expiry={selectedExpiry} atmStrike={chain.atm_strike} />
          )}
        </section>

        {/* Right: charts always visible */}
        <aside className="charts-panel">
          {chain && (
            <>
              <PremiumChart expiry={selectedExpiry} selectedLeg={selectedLeg} />
              <IVChart expiry={selectedExpiry} atmStrike={chain.atm_strike} />
            </>
          )}
          {!chain && (
            <div className="loading-center" style={{ height: '100%' }}>
              <Spinner />
              <span>Select an expiry to load charts</span>
            </div>
          )}
        </aside>
      </main>

      {/* Selected option info bar */}
      {selectedLeg && (
        <footer className="info-bar">
          <span className="info-sym">{selectedLeg.symbol}</span>
          <span>LTP: <strong>${selectedLeg.last_price.toFixed(2)}</strong></span>
          <span>IV: <strong>{selectedLeg.iv_pct.toFixed(1)}%</strong></span>
          <span>Δ: <strong>{selectedLeg.delta.toFixed(3)}</strong></span>
          <span>Γ: <strong>{selectedLeg.gamma.toFixed(5)}</strong></span>
          <span>Θ: <strong>{selectedLeg.theta.toFixed(2)}</strong></span>
          <span>V: <strong>{selectedLeg.vega.toFixed(2)}</strong></span>
          <span>OI: <strong>{selectedLeg.open_interest.toLocaleString()}</strong></span>
        </footer>
      )}
    </div>
  );
}
