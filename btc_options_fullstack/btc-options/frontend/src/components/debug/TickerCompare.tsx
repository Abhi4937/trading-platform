import React, { useState } from 'react';

interface CompareResult {
  symbol: string;
  ws_store: Record<string, any>;
  rest_live: Record<string, any>;
}

const FIELDS = ['mark_price', 'close', 'bid', 'ask', 'mark_iv', 'volume', 'oi', 'timestamp'];

export const TickerCompare: React.FC = () => {
  const [symbol, setSymbol] = useState('C-BTC-68000-080326');
  const [result, setResult] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const compare = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/debug/compare/${symbol}`);
      const data = await res.json();
      setResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const diff = (a: any, b: any) => {
    if (a == null || b == null) return false;
    const na = parseFloat(a), nb = parseFloat(b);
    if (isNaN(na) || isNaN(nb)) return a !== b;
    return Math.abs(na - nb) / Math.max(Math.abs(nb), 1) > 0.01;
  };

  return (
    <div style={{ padding: 24, fontFamily: 'JetBrains Mono, monospace', color: '#e2eaf4', background: '#050a0f', minHeight: '100vh' }}>
      <h2 style={{ color: '#00d4ff', marginBottom: 16 }}>WS Store vs REST Live Comparison</h2>

      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        <input
          value={symbol}
          onChange={e => setSymbol(e.target.value)}
          style={{ flex: 1, background: '#0c1420', border: '1px solid #1a2d42', color: '#e2eaf4', padding: '6px 12px', borderRadius: 6, fontFamily: 'inherit', fontSize: 13 }}
          placeholder="e.g. C-BTC-68000-080326"
        />
        <button
          onClick={compare}
          disabled={loading}
          style={{ padding: '6px 20px', background: '#1f6feb', border: 'none', borderRadius: 6, color: '#fff', cursor: 'pointer', fontFamily: 'inherit', fontSize: 13 }}
        >
          {loading ? 'Fetching...' : 'Compare'}
        </button>
      </div>

      {error && <div style={{ color: '#ff4d6a', marginBottom: 12 }}>{error}</div>}

      {result && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '8px 12px', background: '#0c1420', color: '#4a6a85', borderBottom: '1px solid #1a2d42' }}>Field</th>
              <th style={{ textAlign: 'right', padding: '8px 12px', background: '#0c1420', color: '#60a5fa', borderBottom: '1px solid #1a2d42' }}>WS Store</th>
              <th style={{ textAlign: 'right', padding: '8px 12px', background: '#0c1420', color: '#00e5a0', borderBottom: '1px solid #1a2d42' }}>REST Live</th>
              <th style={{ textAlign: 'center', padding: '8px 12px', background: '#0c1420', color: '#f0b429', borderBottom: '1px solid #1a2d42' }}>Match?</th>
            </tr>
          </thead>
          <tbody>
            {FIELDS.map(field => {
              const ws = result.ws_store[field];
              const rest = result.rest_live[field];
              const isDiff = diff(ws, rest);
              return (
                <tr key={field} style={{ background: isDiff ? 'rgba(255,77,106,0.08)' : 'transparent' }}>
                  <td style={{ padding: '6px 12px', borderBottom: '1px solid #0c1420', color: '#7a9bb5' }}>{field}</td>
                  <td style={{ padding: '6px 12px', borderBottom: '1px solid #0c1420', textAlign: 'right', color: '#60a5fa' }}>{ws ?? '—'}</td>
                  <td style={{ padding: '6px 12px', borderBottom: '1px solid #0c1420', textAlign: 'right', color: '#00e5a0' }}>{rest ?? '—'}</td>
                  <td style={{ padding: '6px 12px', borderBottom: '1px solid #0c1420', textAlign: 'center' }}>
                    {isDiff ? <span style={{ color: '#ff4d6a' }}>DIFF</span> : <span style={{ color: '#00e5a0' }}>OK</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
};
