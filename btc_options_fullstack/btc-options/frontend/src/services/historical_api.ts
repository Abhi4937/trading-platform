import type { 
  HistoricalExpiryListResponse, 
  HistoricalOptionChainResponse,
  HistoricalChartResponse
} from '../types/historical';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

export const historicalApi = {
  async getExpiries(): Promise<HistoricalExpiryListResponse> {
    const res = await fetch(`${API_BASE}/historical/expiries`);
    if (!res.ok) throw new Error('Failed to fetch expiries');
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