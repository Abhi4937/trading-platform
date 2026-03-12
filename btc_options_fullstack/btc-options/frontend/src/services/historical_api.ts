import type { 
  HistoricalExpiryListResponse, 
  HistoricalOptionChainResponse,
  HistoricalChartResponse
} from '../types/historical';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

export const historicalApi = {
  async getLatestAvailableData(): Promise<{ latestDate: string, latestTime: string, latestExpiry: string }> {
    const res = await fetch(`${API_BASE}/historical/latest-available-data`);
    if (!res.ok) throw new Error('Failed to fetch latest data');
    return res.json();
  },

  async getExpiries(date: string): Promise<HistoricalExpiryListResponse> {
    const res = await fetch(`${API_BASE}/historical/expiries?date=${date}`);
    if (!res.ok) throw new Error('Failed to fetch expiries');
    return res.json();
  },

  async getDataRange(): Promise<{ min_ts: number, max_ts: number }> {
    const res = await fetch(`${API_BASE}/historical/data-range`);
    if (!res.ok) throw new Error('Failed to fetch data range');
    return res.json();
  },

  async getOptionChain(date: string, timestamp: number): Promise<HistoricalOptionChainResponse> {
    const res = await fetch(`${API_BASE}/historical/option-chain?date=${date}&timestamp=${timestamp}`);
    if (!res.ok) throw new Error('Failed to fetch option chain');
    return res.json();
  },

  async getChartData(expiry: string, strike: number, type: 'CE' | 'PE', startTime: number, timeframe: string): Promise<HistoricalChartResponse> {
    const res = await fetch(`${API_BASE}/historical/chart-data?expiry=${expiry}&strike=${strike}&type=${type}&start_time=${startTime}&timeframe=${timeframe}`);
    if (!res.ok) throw new Error('Failed to fetch chart data');
    return res.json();
  }
};