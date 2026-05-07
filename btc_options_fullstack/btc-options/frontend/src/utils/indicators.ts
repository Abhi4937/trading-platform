// TS port of `backend/app/services/indicators.py` for client-side indicator
// computation on M7TradePathChart spot OHLC.
//
// Parity with backend:
//   - `_sma`, `_ema`, `_rsi` (Wilder), `_macd`, `_bbands`, `_atr` (Wilder)
//   - Same warm-up (min_periods semantics → NaN for first N-1 bars)
//   - EMA uses the recursive form with `alpha = 2 / (period + 1)`,
//     seeded with the first available close (matches pandas
//     `ewm(span=N, adjust=False, min_periods=N).mean()` from period N onward).
//   - RSI uses Wilder's smoothing: `alpha = 1 / period` on gains/losses.
//   - ATR uses Wilder's smoothing on True Range.
//
// The parity test in `indicators.test.ts` confirms RSI/MACD/BB/ATR match the
// backend within 1e-6 on a fixed 60-bar synthetic series.

export interface OhlcBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface ValuePoint { time: number; value: number; }
export interface MacdPoint { time: number; macd: number; signal: number; hist: number; }
export interface BBandsPoint { time: number; upper: number; mid: number; lower: number; }

// ── Primitives ──────────────────────────────────────────────────────────────

/** Simple moving average — first `period - 1` bars are NaN. */
export function sma(close: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(close.length).fill(null);
  if (close.length < period) return out;
  let sum = 0;
  for (let i = 0; i < period; i++) sum += close[i];
  out[period - 1] = sum / period;
  for (let i = period; i < close.length; i++) {
    sum += close[i] - close[i - period];
    out[i] = sum / period;
  }
  return out;
}

/** Exponential moving average — recursive form. Seeded at index period-1
 *  with SMA over the first `period` values, NaN elsewhere. Matches pandas
 *  `ewm(span=N, adjust=False, min_periods=N).mean()`. */
export function ema(close: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(close.length).fill(null);
  if (close.length < period) return out;
  const alpha = 2 / (period + 1);
  // Seed with SMA of first N values
  let seed = 0;
  for (let i = 0; i < period; i++) seed += close[i];
  out[period - 1] = seed / period;
  for (let i = period; i < close.length; i++) {
    out[i] = alpha * close[i] + (1 - alpha) * (out[i - 1] as number);
  }
  return out;
}

/** Wilder's RSI. Output is in [0, 100]; NaN until period bars elapsed. */
export function rsi(close: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(close.length).fill(null);
  if (close.length <= period) return out;
  const alpha = 1 / period;
  let avgGain = 0, avgLoss = 0;
  // Wilder seed: average gain/loss over first `period` deltas
  for (let i = 1; i <= period; i++) {
    const d = close[i] - close[i - 1];
    avgGain += d > 0 ? d : 0;
    avgLoss += d < 0 ? -d : 0;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
  for (let i = period + 1; i < close.length; i++) {
    const d = close[i] - close[i - 1];
    const gain = d > 0 ? d : 0;
    const loss = d < 0 ? -d : 0;
    avgGain = alpha * gain + (1 - alpha) * avgGain;
    avgLoss = alpha * loss + (1 - alpha) * avgLoss;
    out[i] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
  }
  return out;
}

/** MACD = EMA(fast) − EMA(slow); signal = EMA(MACD, signal); hist = MACD − signal. */
export function macd(
  close: number[],
  fast = 12, slow = 26, signal = 9,
): { macd: (number | null)[]; signal: (number | null)[]; hist: (number | null)[]; } {
  const fastEma = ema(close, fast);
  const slowEma = ema(close, slow);
  const macdLine: (number | null)[] = close.map((_, i) =>
    (fastEma[i] != null && slowEma[i] != null)
      ? (fastEma[i] as number) - (slowEma[i] as number) : null);

  // Signal = EMA over the macd line, but we have to handle the head NaNs.
  const sigLine: (number | null)[] = new Array(close.length).fill(null);
  const macdNumeric = macdLine.map(v => v as number | null);
  let firstValidIdx = -1;
  for (let i = 0; i < macdNumeric.length; i++) {
    if (macdNumeric[i] != null) { firstValidIdx = i; break; }
  }
  if (firstValidIdx >= 0 && (macdNumeric.length - firstValidIdx) >= signal) {
    const alpha = 2 / (signal + 1);
    let seed = 0;
    for (let i = 0; i < signal; i++) seed += macdNumeric[firstValidIdx + i] as number;
    sigLine[firstValidIdx + signal - 1] = seed / signal;
    for (let i = firstValidIdx + signal; i < close.length; i++) {
      sigLine[i] = alpha * (macdNumeric[i] as number) +
                   (1 - alpha) * (sigLine[i - 1] as number);
    }
  }

  const hist: (number | null)[] = macdLine.map((v, i) =>
    (v != null && sigLine[i] != null)
      ? (v as number) - (sigLine[i] as number) : null);
  return { macd: macdLine, signal: sigLine, hist };
}

/** Bollinger Bands: mid = SMA, upper = mid + k·σ, lower = mid − k·σ.
 *  σ uses ddof=0 (population std). */
export function bbands(
  close: number[], period = 20, stdev = 2,
): { upper: (number | null)[]; mid: (number | null)[]; lower: (number | null)[]; } {
  const mid = sma(close, period);
  const upper: (number | null)[] = new Array(close.length).fill(null);
  const lower: (number | null)[] = new Array(close.length).fill(null);
  for (let i = period - 1; i < close.length; i++) {
    if (mid[i] == null) continue;
    let sumSq = 0;
    const m = mid[i] as number;
    for (let j = i - period + 1; j <= i; j++) sumSq += (close[j] - m) ** 2;
    const sigma = Math.sqrt(sumSq / period);
    upper[i] = m + stdev * sigma;
    lower[i] = m - stdev * sigma;
  }
  return { upper, mid, lower };
}

/** Wilder's ATR (Average True Range). */
export function atr(
  high: number[], low: number[], close: number[], period = 14,
): (number | null)[] {
  const out: (number | null)[] = new Array(close.length).fill(null);
  if (close.length <= period) return out;
  const tr: number[] = [0]; // First bar has no prior close → TR=high-low only
  tr[0] = high[0] - low[0];
  for (let i = 1; i < close.length; i++) {
    const a = high[i] - low[i];
    const b = Math.abs(high[i] - close[i - 1]);
    const c = Math.abs(low[i] - close[i - 1]);
    tr.push(Math.max(a, b, c));
  }
  // Wilder smoothing seeded with average of first `period` TRs.
  let avg = 0;
  for (let i = 0; i < period; i++) avg += tr[i];
  avg /= period;
  out[period - 1] = avg;
  const alpha = 1 / period;
  for (let i = period; i < close.length; i++) {
    avg = alpha * tr[i] + (1 - alpha) * avg;
    out[i] = avg;
  }
  return out;
}

// ── Convenience wrappers that emit time-aligned points ──────────────────────

export function smaSeries(bars: OhlcBar[], period: number): ValuePoint[] {
  return _toValuePoints(bars, sma(bars.map(b => b.close), period));
}
export function emaSeries(bars: OhlcBar[], period: number): ValuePoint[] {
  return _toValuePoints(bars, ema(bars.map(b => b.close), period));
}
export function rsiSeries(bars: OhlcBar[], period: number): ValuePoint[] {
  return _toValuePoints(bars, rsi(bars.map(b => b.close), period));
}
export function atrSeries(bars: OhlcBar[], period: number): ValuePoint[] {
  return _toValuePoints(bars,
    atr(bars.map(b => b.high), bars.map(b => b.low), bars.map(b => b.close), period));
}
export function macdSeries(bars: OhlcBar[], fast = 12, slow = 26, signal = 9): MacdPoint[] {
  const close = bars.map(b => b.close);
  const r = macd(close, fast, slow, signal);
  const out: MacdPoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    if (r.macd[i] == null || r.signal[i] == null || r.hist[i] == null) continue;
    out.push({ time: bars[i].time,
               macd:   r.macd[i]   as number,
               signal: r.signal[i] as number,
               hist:   r.hist[i]   as number });
  }
  return out;
}
export function bbandsSeries(bars: OhlcBar[], period = 20, stdev = 2): BBandsPoint[] {
  const r = bbands(bars.map(b => b.close), period, stdev);
  const out: BBandsPoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    if (r.upper[i] == null || r.mid[i] == null || r.lower[i] == null) continue;
    out.push({ time: bars[i].time,
               upper: r.upper[i] as number,
               mid:   r.mid[i]   as number,
               lower: r.lower[i] as number });
  }
  return out;
}

// ── Internal ─────────────────────────────────────────────────────────────────

function _toValuePoints(bars: OhlcBar[], series: (number | null)[]): ValuePoint[] {
  const out: ValuePoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    const v = series[i];
    if (v == null || !Number.isFinite(v)) continue;
    out.push({ time: bars[i].time, value: v });
  }
  return out;
}
