import type {
  BacktestRequest,
  BacktestSubmitResponse,
  BacktestStatusResponse,
} from '../types/backtest';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

export const backtestApi = {
  async submit(req: BacktestRequest, signal?: AbortSignal): Promise<BacktestSubmitResponse> {
    const res = await fetch(`${API_BASE}/historical/backtest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal,
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => '');
      throw new Error(`submit failed (${res.status}): ${txt || res.statusText}`);
    }
    return res.json();
  },

  async poll(jobId: string, signal?: AbortSignal): Promise<BacktestStatusResponse> {
    const res = await fetch(`${API_BASE}/historical/backtest/${jobId}`, { signal });
    if (!res.ok) throw new Error(`poll failed (${res.status})`);
    return res.json();
  },

  async cancel(jobId: string): Promise<void> {
    const res = await fetch(`${API_BASE}/historical/backtest/${jobId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`cancel failed (${res.status})`);
  },
};
