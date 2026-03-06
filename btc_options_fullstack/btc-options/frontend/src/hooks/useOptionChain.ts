import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../services/api';
import type { ExpiryInfo, OptionChainResponse } from '../types/api';

export function useExpiries() {
  const [expiries, setExpiries] = useState<ExpiryInfo[]>([]);
  const [spot, setSpot] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getExpiries()
      .then(r => { setExpiries(r.expiries); setSpot(r.spot_price); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return { expiries, spot, loading, error };
}

export function useOptionChain(expiry: string | null, _autoRefresh = false) {
  const [data, setData] = useState<OptionChainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Manual refetch is a no-op — WS pushes continuously
  const refetch = useCallback(() => {}, []);

  useEffect(() => {
    if (!expiry) return;

    setLoading(true);
    setError(null);

    const wsBase = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = import.meta.env.VITE_WS_HOST ?? 'localhost:8000';
    let ws = new WebSocket(`${wsBase}//${host}/ws/chain?expiry=${expiry}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        setData(JSON.parse(e.data));
        setLoading(false);
        setError(null);
      } catch {
        // ignore parse errors
      }
    };

    ws.onerror = () => {
      setError('WebSocket connection failed — retrying...');
      setLoading(false);
    };

    ws.onclose = (e) => {
      // Reconnect unless the component unmounted (wsRef cleared)
      if (wsRef.current && !e.wasClean) {
        setTimeout(() => {
          if (!wsRef.current) return;
          const newWs = new WebSocket(`${wsBase}//${host}/ws/chain?expiry=${expiry}`);
          wsRef.current = newWs;
          newWs.onmessage = ws.onmessage;
          newWs.onerror = ws.onerror;
          newWs.onclose = ws.onclose;
        }, 2000);
      }
    };

    return () => {
      wsRef.current = null;
      ws.close();
    };
  }, [expiry]);

  return { data, loading, error, refetch };
}
