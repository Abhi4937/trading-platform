import type {
  HistoricalExpiryListResponse,
  HistoricalOptionChainResponse,
  HistoricalChartResponse,
  ChartDataWithGreeksResponse,
} from '../types/historical';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

export const historicalApi = {
  async getLatestAvailableData(signal?: AbortSignal): Promise<{ latestDate: string, latestTime: string, latestExpiry: string }> {
    const res = await fetch(`${API_BASE}/historical/latest-available-data`, { signal });
    if (!res.ok) throw new Error('Failed to fetch latest data');
    return res.json();
  },

  async getExpiries(date: string, timestamp?: number, signal?: AbortSignal): Promise<HistoricalExpiryListResponse> {
    const url = timestamp 
      ? `${API_BASE}/historical/expiries?date=${date}&timestamp=${timestamp}`
      : `${API_BASE}/historical/expiries?date=${date}`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error('Failed to fetch expiries');
    return res.json();
  },

  async getDataRange(signal?: AbortSignal): Promise<{ min_ts: number, max_ts: number }> {
    const res = await fetch(`${API_BASE}/historical/data-range`, { signal });
    if (!res.ok) throw new Error('Failed to fetch data range');
    return res.json();
  },

  async getOptionChain(date: string, timestamp: number, signal?: AbortSignal, pinStrikes?: number[]): Promise<HistoricalOptionChainResponse> {
    let url = `${API_BASE}/historical/option-chain?date=${date}&timestamp=${timestamp}`;
    if (pinStrikes && pinStrikes.length > 0) url += `&pin_strikes=${pinStrikes.join(',')}`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error('Failed to fetch option chain');
    return res.json();
  },

  async getChartData(expiry: string, strike: number, type: 'CE' | 'PE', startTime: number, timeframe: string, signal?: AbortSignal): Promise<HistoricalChartResponse> {
    const res = await fetch(`${API_BASE}/historical/chart-data?expiry=${expiry}&strike=${strike}&type=${type}&start_time=${startTime}&timeframe=${timeframe}`, { signal });
    if (!res.ok) throw new Error('Failed to fetch chart data');
    return res.json();
  },

  async getChartDataWithGreeks(expiry: string, strike: number, type: 'CE' | 'PE', startTime: number, timeframe: string, rvWindowDays: number = 7, signal?: AbortSignal): Promise<ChartDataWithGreeksResponse> {
    const res = await fetch(`${API_BASE}/historical/chart-data-with-greeks?expiry=${expiry}&strike=${strike}&type=${type}&start_time=${startTime}&timeframe=${timeframe}&rv_window_days=${rvWindowDays}`, { signal });
    if (!res.ok) throw new Error('Failed to fetch chart data with greeks');
    return res.json();
  },
};