import type {
  ExpiryListResponse, OptionChainResponse, PremiumChartResponse,
  IVSmileResponse, IVRVResponse, SpotResponse,
} from '../types/api';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

async function fetcher<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(BASE + path);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url.toString());
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  getSpot: () =>
    fetcher<SpotResponse>('/spot'),

  getExpiries: () =>
    fetcher<ExpiryListResponse>('/expiries'),

  getOptionChain: (expiry: string) =>
    fetcher<OptionChainResponse>('/options', { expiry }),

  getPremiumChart: (expiry: string, strike: number, optionType: string, timeframe: string) =>
    fetcher<PremiumChartResponse>('/plot-data/premium', {
      expiry, strike: String(strike), option_type: optionType, timeframe,
    }),

  getIVSmile: (expiry: string) =>
    fetcher<IVSmileResponse>('/plot-data/iv-smile', { expiry }),

  getIVRV: (expiry: string, window: number) =>
    fetcher<IVRVResponse>('/plot-data/iv-rv', { expiry, window: String(window) }),

  getLogs: (file: 'api' | 'errors', lines: number) =>
    fetcher<{ file: string; lines: string[]; total: number }>('/logs', { file, lines: String(lines) }),
};
