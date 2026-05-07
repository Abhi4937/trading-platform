// M7 — Per-cell drilldown modal. Surfaces 4 tabs of analysis for a single
// best-combo cell (band × hour × expiry × delta):
//
//   Losers          — paginated leg-attribution table filtered to is_win=false
//   Winners-vs-Losers — Chunk 3 indicator gap analysis
//   Worst Fridays   — Chunk 5 tail-risk view with z-score diagnostics
//   Path Replay     — Chunk 4 spot+indicators chart for one trade
//                     (defaults to the cell's worst loser)
//
// Used by M7IvBandFullCoverageTable's "Rule" / "All (n)" buttons (Chunk 7
// integration). The modal is self-contained — pass it the cell descriptor
// and it does the rest.

import React, { useEffect, useState } from 'react';
import { fetchM7CellWorstFridays, type M7Cell } from '../../services/m7_api';
import type { M7ExitRule } from '../../types/m7';
import { M7CellWinnersVsLosersTable } from './M7CellWinnersVsLosersTable';
import { M7CellWorstFridaysTable } from './M7CellWorstFridaysTable';
import { M7TradePathChart } from './M7TradePathChart';

interface Props {
  cell: M7Cell;
  exitRule?: M7ExitRule;
  setLabel?: string;          // "Rule" / "All (n)" — shown in modal title
  onClose: () => void;
}

type Tab = 'wvl' | 'worst' | 'path';

export function M7CellDrilldownModal({ cell, exitRule, setLabel, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('worst');
  const [tradeForPath, setTradeForPath] = useState<string | null>(null);

  // Auto-load the worst-loser trade_id for the Path tab so it has a sensible default.
  const [defaultTradeId, setDefaultTradeId] = useState<string | null>(null);
  useEffect(() => {
    fetchM7CellWorstFridays(cell, { n: 1, exit_rule: exitRule })
      .then(d => {
        if (d.rows.length > 0) setDefaultTradeId(d.rows[0].trade_id);
      })
      .catch(e => console.error('worst-friday lookup:', e));
  }, [JSON.stringify(cell), JSON.stringify(exitRule)]);

  // ESC to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const tabBtn = (key: Tab, label: string): React.CSSProperties => ({
    padding: '6px 12px', fontSize: 12, cursor: 'pointer',
    background: tab === key ? '#1f6feb' : '#0d1421',
    color: tab === key ? '#fff' : '#7a9bb5',
    border: '1px solid #1a2d42', borderRadius: 4, fontWeight: tab === key ? 600 : 400,
  });

  const cellTitle = `${cell.entry_atm_iv_band}  ·  hour ${cell.entry_hour_ist}  ·  ${cell.expiry_bucket}  ·  Δ=${cell.delta_target}`;

  // The path-replay chart already has its own modal layer with onClose. When
  // we open it we leave the drilldown modal mounted underneath for fast switching.
  if (tradeForPath) {
    return (
      <>
        {/* Drilldown modal (background) */}
        <DrilldownShell title={cellTitle} setLabel={setLabel} onClose={onClose}>
          <div style={{ color: '#7a9bb5', padding: 16, fontSize: 12 }}>
            (Path replay open over this modal)
          </div>
        </DrilldownShell>
        <M7TradePathChart tradeId={tradeForPath} onClose={() => setTradeForPath(null)} />
      </>
    );
  }

  return (
    <DrilldownShell title={cellTitle} setLabel={setLabel} onClose={onClose}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        <button style={tabBtn('worst', 'Worst Fridays')}
                onClick={() => setTab('worst')}>Worst Fridays</button>
        <button style={tabBtn('wvl', 'Winners vs Losers')}
                onClick={() => setTab('wvl')}>Winners vs Losers</button>
        <button style={tabBtn('path', 'Path Replay')}
                onClick={() => setTab('path')}>Path Replay</button>
      </div>

      {tab === 'worst' && (
        <M7CellWorstFridaysTable
          cell={cell}
          exitRule={exitRule}
          n={10}
          onSelectTrade={setTradeForPath}
        />
      )}

      {tab === 'wvl' && (
        <M7CellWinnersVsLosersTable
          cell={cell}
          exitRule={exitRule}
        />
      )}

      {tab === 'path' && (
        <div style={{ background: '#0d1421', border: '1px solid #1a2d42',
                      borderRadius: 4, padding: 10, color: '#7a9bb5', fontSize: 12 }}>
          {defaultTradeId ? (
            <button
              onClick={() => setTradeForPath(defaultTradeId)}
              style={{
                padding: '8px 14px', background: '#1f6feb', color: '#fff',
                border: 'none', borderRadius: 4, fontSize: 12, cursor: 'pointer',
              }}>
              Open path replay for worst loser ({defaultTradeId})
            </button>
          ) : (
            <span>No trades to replay.</span>
          )}
          <div style={{ marginTop: 10, fontSize: 10 }}>
            Tip: also click any row in the "Worst Fridays" tab to open its path replay.
          </div>
        </div>
      )}
    </DrilldownShell>
  );
}

function DrilldownShell({
  title, setLabel, onClose, children,
}: {
  title: string;
  setLabel?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 180,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        background: '#0a0e17', border: '1px solid #1a2d42', borderRadius: 8,
        padding: 16, width: 'min(96vw, 1300px)', maxHeight: '92vh', overflowY: 'auto',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', marginBottom: 10, gap: 12 }}>
          <div style={{ fontSize: 13, color: '#cfd9e3', display: 'flex',
                        alignItems: 'center', gap: 10 }}>
            <span style={{ color: '#7a9bb5', fontSize: 11, textTransform: 'uppercase',
                           letterSpacing: 0.5 }}>Cell drill-down</span>
            {setLabel && (
              <span style={{
                background: '#1f6feb', color: '#fff', padding: '2px 8px',
                borderRadius: 3, fontSize: 10, fontWeight: 700,
              }}>{setLabel}</span>
            )}
            <strong>{title}</strong>
          </div>
          <button onClick={onClose} style={{
            padding: '4px 10px', background: '#1a2d42', color: '#cfd9e3',
            border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 11,
          }}>Close ✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
