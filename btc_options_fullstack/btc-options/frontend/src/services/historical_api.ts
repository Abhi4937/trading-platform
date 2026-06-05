import type {
  HistoricalExpiryListResponse,
  HistoricalOptionChainResponse,
  HistoricalChartResponse,
  ChartDataWithGreeksResponse,
  AtmIvSeriesResponse,
  SpotOhlcResponse,
  IndicatorsResponse,
  IndicatorConfig,
  VolAnalyticsResponse,
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

  async getAtmIvSeries(expiry: string, timeframe: string, rvWindowDays: number = 7, signal?: AbortSignal): Promise<AtmIvSeriesResponse> {
    const res = await fetch(`${API_BASE}/historical/atm-iv-series?expiry=${expiry}&timeframe=${timeframe}&rv_window_days=${rvWindowDays}`, { signal });
    if (!res.ok) throw new Error('Failed to fetch ATM IV series');
    return res.json();
  },

  async getVolAnalytics(expiry: string, timestamp: number, signal?: AbortSignal): Promise<VolAnalyticsResponse> {
    const res = await fetch(`${API_BASE}/historical/vol-analytics?expiry=${expiry}&timestamp=${timestamp}`, { signal });
    if (!res.ok) throw new Error('Failed to fetch vol analytics');
    return res.json();
  },

  async getSpotOhlc(startTs: number, endTs: number, timeframe: string, signal?: AbortSignal): Promise<SpotOhlcResponse> {
    const url = `${API_BASE}/historical/spot-ohlc?start_ts=${startTs}&end_ts=${endTs}&timeframe=${timeframe}`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error('Failed to fetch spot OHLC');
    return res.json();
  },

  async getLegOhlc(expiry: string, strike: number, type: 'CE' | 'PE', startTs: number, endTs: number, timeframe: string, signal?: AbortSignal): Promise<SpotOhlcResponse> {
    const url = `${API_BASE}/historical/leg-ohlc?expiry=${expiry}&strike=${strike}&type=${type}&start_ts=${startTs}&end_ts=${endTs}&timeframe=${timeframe}`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error('Failed to fetch leg OHLC');
    return res.json();
  },

  async getSpotIndicators(startTs: number, endTs: number, timeframe: string, indicators: IndicatorConfig[], signal?: AbortSignal): Promise<IndicatorsResponse> {
    const enc = encodeURIComponent(JSON.stringify(indicators));
    const url = `${API_BASE}/historical/spot-indicators?start_ts=${startTs}&end_ts=${endTs}&timeframe=${timeframe}&indicators=${enc}`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error('Failed to fetch spot indicators');
    return res.json();
  },

  async getLegIndicators(expiry: string, strike: number, type: 'CE' | 'PE', startTs: number, endTs: number, timeframe: string, indicators: IndicatorConfig[], signal?: AbortSignal): Promise<IndicatorsResponse> {
    const enc = encodeURIComponent(JSON.stringify(indicators));
    const url = `${API_BASE}/historical/leg-indicators?expiry=${expiry}&strike=${strike}&type=${type}&start_ts=${startTs}&end_ts=${endTs}&timeframe=${timeframe}&indicators=${enc}`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error('Failed to fetch leg indicators');
    return res.json();
  },

  async getSnapshotContext(ts: number, signal?: AbortSignal): Promise<Record<string, number | string | null>> {
    const res = await fetch(`${API_BASE}/historical/snapshot-context?ts=${ts}`, { signal });
    if (!res.ok) {
      if (res.status === 503) throw new Error('Enriched snapshot table not built (run enrich_derived).');
      if (res.status === 404) throw new Error('No snapshot data for this timestamp.');
      throw new Error('Failed to fetch snapshot context');
    }
    return res.json();
  },

  async getCalibration(dte: number, spot: number, deltaTarget: number, ivp: number, signal?: AbortSignal): Promise<any> {
    const url = `${API_BASE}/historical/calibration?dte=${dte}&spot=${spot}&delta_target=${deltaTarget}&ivp=${ivp}`;
    const res = await fetch(url, { signal });
    if (!res.ok) {
      if (res.status === 503) throw new Error('Calibration not built (run calibration_builder).');
      if (res.status === 404) throw new Error('No calibration bucket for these inputs.');
      throw new Error('Failed to fetch calibration');
    }
    return res.json();
  },
};