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

export function useOptionChain(expiry: string | null, autoRefresh = false) {
  const [data, setData] = useState<OptionChainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetch = useCallback(async () => {
    if (!expiry) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getOptionChain(expiry);
      setData(res);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [expiry]);

  useEffect(() => {
    fetch();
    if (autoRefresh) {
      timerRef.current = setInterval(fetch, 15_000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [fetch, autoRefresh]);

  return { data, loading, error, refetch: fetch };
}
