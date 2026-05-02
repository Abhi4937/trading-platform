"""
Module 1 — Spot enrichment pipeline.

Reads /home/abhis/btc-data/data/spot/BTCUSD_1min.parquet, buckets to 5-min
bars, computes a wide set of price-only indicators across 7 timeframes
(1m, 5m, 15m, 30m, 1h, 4h, 1d) plus longer-window RV/RVP metrics, writes
to /home/abhis/btc-data/derived/spot_enriched.parquet.

Idempotent: re-running computes only the last 1 day + new bars and
overwrites the corresponding tail of the output file.

Run:
    python -m app.analytics.enrich_spot               # incremental append
    python -m app.analytics.enrich_spot --rebuild     # full overwrite
    python -m app.analytics.enrich_spot --through 2026-04-30
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time as _time
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

# ── Paths / constants ────────────────────────────────────────────────────────

SPOT_1M_PATH = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"
DERIVED_DIR = "/home/abhis/btc-data/derived"
OUTPUT_PATH = os.path.join(DERIVED_DIR, "spot_enriched.parquet")

BARS_PER_DAY_5M = 288             # 24h × 60m / 5m
ANN_FACTOR = np.sqrt(288 * 365)   # √(bars/day × days/year) for 5m intraday

# Bars-per-window helpers (for 5-minute base series)
def bars_5m(window_seconds: int) -> int:
    return max(1, int(round(window_seconds / 300)))

# 7-timeframe set (resample target)
TIMEFRAMES_PANDAS = {
    "1m":  "1min",
    "5m":  "5min",
    "15m": "15min",
    "30m": "30min",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1D",
}

# Indicators that compute on every TF
TFS_FULL = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
# Indicators that skip 1m (too noisy for slow smoothers)
TFS_NO_1M = ["5m", "15m", "30m", "1h", "4h", "1d"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── Indicator primitives (pandas + numpy, hand-rolled) ───────────────────────

def _wilder_ema(s: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing = EMA with α=1/period."""
    return s.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

def _ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()

def _sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    pc = close.shift(1)
    return pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)

def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder_ema(true_range(df["high"], df["low"], df["close"]), period)

def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder_ema(gain, period)
    avg_loss = _wilder_ema(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # Standard convention: avg_loss == 0 with positive avg_gain → RSI = 100;
    # both zero → RSI undefined (leave NaN).
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    return rsi

def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Returns columns: adx, plus_di, minus_di."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(high, low, close)
    atr14 = _wilder_ema(tr, period)
    plus_di = 100.0 * _wilder_ema(pd.Series(plus_dm, index=df.index), period) / atr14.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_ema(pd.Series(minus_dm, index=df.index), period) / atr14.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["adx"] = _wilder_ema(dx, period)
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    return out

def bollinger(close: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = _sma(close, period)
    std = close.rolling(period, min_periods=period).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    out = pd.DataFrame(index=close.index)
    out["bb_mid"] = mid
    out["bb_upper"] = upper
    out["bb_lower"] = lower
    out["bb_width"] = width
    out["bb_pct_b"] = pct_b
    return out

def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = _ema(close, fast) - _ema(close, slow)
    sig = _ema(macd_line, signal)
    out = pd.DataFrame(index=close.index)
    out["macd"] = macd_line
    out["macd_signal"] = sig
    out["macd_hist"] = macd_line - sig
    return out

def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth: int = 3) -> pd.DataFrame:
    high, low, close = df["high"], df["low"], df["close"]
    hh = high.rolling(k_period, min_periods=k_period).max()
    ll = low.rolling(k_period, min_periods=k_period).min()
    raw_k = 100.0 * (close - ll) / (hh - ll).replace(0.0, np.nan)
    k = raw_k.rolling(smooth, min_periods=smooth).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    out = pd.DataFrame(index=df.index)
    out["stoch_k"] = k
    out["stoch_d"] = d
    return out

def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    sma_tp = _sma(tp, period)
    mad = (tp - sma_tp).abs().rolling(period, min_periods=period).mean()
    return (tp - sma_tp) / (0.015 * mad).replace(0.0, np.nan)

def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hh = df["high"].rolling(period, min_periods=period).max()
    ll = df["low"].rolling(period, min_periods=period).min()
    return -100.0 * (hh - df["close"]) / (hh - ll).replace(0.0, np.nan)

def roc(close: pd.Series, period: int = 10) -> pd.Series:
    return (close / close.shift(period) - 1.0) * 100.0

def donchian(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    upper = df["high"].rolling(period, min_periods=period).max()
    lower = df["low"].rolling(period, min_periods=period).min()
    mid = (upper + lower) / 2.0
    width = (upper - lower) / df["close"].replace(0.0, np.nan)
    pos = (df["close"] - lower) / (upper - lower).replace(0.0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["donch_upper"] = upper
    out["donch_lower"] = lower
    out["donch_mid"] = mid
    out["donch_width"] = width
    out["donch_pos"] = pos
    return out

def keltner(df: pd.DataFrame, period: int = 20, atr_mult: float = 2.0) -> pd.DataFrame:
    mid = _ema(df["close"], period)
    atr20 = _wilder_ema(true_range(df["high"], df["low"], df["close"]), period)
    upper = mid + atr_mult * atr20
    lower = mid - atr_mult * atr20
    width = (upper - lower) / mid.replace(0.0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["kelt_mid"] = mid
    out["kelt_upper"] = upper
    out["kelt_lower"] = lower
    out["kelt_width"] = width
    return out

def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Standard SuperTrend with trend-flip carry-forward logic."""
    hl2 = (df["high"] + df["low"]) / 2.0
    atr_p = _wilder_ema(true_range(df["high"], df["low"], df["close"]), period)
    basic_upper = hl2 + multiplier * atr_p
    basic_lower = hl2 - multiplier * atr_p

    final_upper = pd.Series(np.nan, index=df.index, dtype=float)
    final_lower = pd.Series(np.nan, index=df.index, dtype=float)
    st = pd.Series(np.nan, index=df.index, dtype=float)
    direction = pd.Series(np.nan, index=df.index, dtype=float)

    close_arr = df["close"].to_numpy()
    bu = basic_upper.to_numpy()
    bl = basic_lower.to_numpy()
    fu = np.full_like(close_arr, np.nan, dtype=float)
    fl = np.full_like(close_arr, np.nan, dtype=float)
    st_arr = np.full_like(close_arr, np.nan, dtype=float)
    dir_arr = np.full_like(close_arr, np.nan, dtype=float)

    started = False
    for i in range(len(close_arr)):
        if not np.isfinite(bu[i]) or not np.isfinite(bl[i]):
            continue
        if not started:
            fu[i] = bu[i]
            fl[i] = bl[i]
            dir_arr[i] = 1.0  # default uptrend
            st_arr[i] = fl[i]
            started = True
            continue
        prev_fu = fu[i - 1] if np.isfinite(fu[i - 1]) else bu[i]
        prev_fl = fl[i - 1] if np.isfinite(fl[i - 1]) else bl[i]
        prev_close = close_arr[i - 1]
        fu[i] = bu[i] if (bu[i] < prev_fu) or (prev_close > prev_fu) else prev_fu
        fl[i] = bl[i] if (bl[i] > prev_fl) or (prev_close < prev_fl) else prev_fl
        prev_dir = dir_arr[i - 1] if np.isfinite(dir_arr[i - 1]) else 1.0
        if prev_dir == 1.0:
            if close_arr[i] < fl[i]:
                dir_arr[i] = -1.0
                st_arr[i] = fu[i]
            else:
                dir_arr[i] = 1.0
                st_arr[i] = fl[i]
        else:
            if close_arr[i] > fu[i]:
                dir_arr[i] = 1.0
                st_arr[i] = fl[i]
            else:
                dir_arr[i] = -1.0
                st_arr[i] = fu[i]

    out = pd.DataFrame(index=df.index)
    out["supertrend"] = st_arr
    out["supertrend_dir"] = dir_arr
    return out

def aroon(df: pd.DataFrame, period: int = 25) -> pd.DataFrame:
    high, low = df["high"], df["low"]
    # periods_since_high: argmax over rolling window from right=0
    def _ps(arr, fn):
        n = len(arr)
        if n < period:
            return np.nan
        idx = fn(arr[-period:])
        return (period - 1 - idx)
    psh = high.rolling(period, min_periods=period).apply(lambda a: _ps(a, np.argmax), raw=True)
    psl = low.rolling(period,  min_periods=period).apply(lambda a: _ps(a, np.argmin), raw=True)
    aroon_up = 100.0 * (period - psh) / period
    aroon_down = 100.0 * (period - psl) / period
    out = pd.DataFrame(index=df.index)
    out["aroon_up"] = aroon_up
    out["aroon_down"] = aroon_down
    out["aroon_osc"] = aroon_up - aroon_down
    return out


# ── RV variants on 5m base series ────────────────────────────────────────────

def rv_close(close: pd.Series, n_bars: int) -> pd.Series:
    """Annualized close-to-close realized vol (×100 → percent)."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(n_bars, min_periods=n_bars).std() * ANN_FACTOR * 100.0

def rv_parkinson(high: pd.Series, low: pd.Series, n_bars: int) -> pd.Series:
    sq = np.log(high / low) ** 2
    var = (1.0 / (4.0 * np.log(2))) * sq.rolling(n_bars, min_periods=n_bars).mean()
    return np.sqrt(var) * ANN_FACTOR * 100.0

def rv_garman_klass(o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series, n_bars: int) -> pd.Series:
    term = 0.5 * (np.log(h / l) ** 2) - (2 * np.log(2) - 1) * (np.log(c / o) ** 2)
    var = term.rolling(n_bars, min_periods=n_bars).mean()
    var = var.clip(lower=0.0)  # GK can go slightly negative on flat bars
    return np.sqrt(var) * ANN_FACTOR * 100.0


# ── Resampling helpers ───────────────────────────────────────────────────────

def resample_ohlc(df_5m: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Bucket 5-min OHLC up to a coarser timeframe via pandas resample."""
    o = df_5m["open"].resample(rule, label="left", closed="left").first()
    h = df_5m["high"].resample(rule, label="left", closed="left").max()
    l = df_5m["low"].resample(rule, label="left", closed="left").min()
    c = df_5m["close"].resample(rule, label="left", closed="left").last()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()
    return out


def merge_tf_indicators(target: pd.DataFrame, tf_df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Forward-fill TF-level indicators onto the 5m index of `target`.

    Expects `target` with DatetimeIndex sorted ascending; `tf_df` indexed by
    the start of each TF bucket. Renames every column with `_<suffix>`.
    """
    if tf_df.empty:
        return target
    renamed = tf_df.rename(columns=lambda c: f"{c}_{suffix}")
    # asof-merge: for each 5m row, take the last tf-row whose ts <= row.ts
    merged = pd.merge_asof(
        target.reset_index().rename(columns={"index": "ts"}),
        renamed.reset_index().rename(columns={"timestamp_ist": "ts", "index": "ts"}),
        on="ts",
        direction="backward",
    ).set_index("ts")
    merged.index.name = target.index.name
    return merged


# ── Per-TF indicator block ───────────────────────────────────────────────────

@dataclass
class TFConfig:
    name: str          # "5m"/"15m"/...
    rule: str | None   # pandas resample rule; None means use 5m base directly
    skip_slow: bool    # skip slow-smoothing indicators (1m only)


def compute_tf_indicators(df_tf: pd.DataFrame, cfg: TFConfig) -> pd.DataFrame:
    """Compute all per-TF indicators on already-bucketed OHLC.

    Returns wide df with columns named without TF suffix; merge_tf_indicators
    adds the suffix when joining onto the 5m grid.
    """
    out = pd.DataFrame(index=df_tf.index)

    # Always-on (Returns, ATR, RSI, ROC)
    out["spot_ret"] = df_tf["close"].pct_change()
    out["atr_14"] = atr_wilder(df_tf, 14)
    out["atr_pct"] = out["atr_14"] / df_tf["close"].replace(0.0, np.nan) * 100.0
    out["rsi_14"] = rsi_wilder(df_tf["close"], 14)
    out["roc_10"] = roc(df_tf["close"], 10)

    if not cfg.skip_slow:
        # ADX
        adx_df = adx(df_tf, 14)
        out["adx_14"] = adx_df["adx"]
        out["plus_di_14"] = adx_df["plus_di"]
        out["minus_di_14"] = adx_df["minus_di"]
        # Bollinger
        bb = bollinger(df_tf["close"], 20, 2.0)
        for c in ("bb_mid", "bb_upper", "bb_lower", "bb_width", "bb_pct_b"):
            out[c] = bb[c]
        # Bollinger squeeze flag: bb_width in bottom 10% of trailing 20-day window.
        # Window in bars = 20 days × bars-per-day at this TF.
        bars_per_day = max(1, int(round(86400 / _tf_seconds(cfg.name))))
        squeeze_window = 20 * bars_per_day
        if len(bb) >= squeeze_window:
            pct_rank = bb["bb_width"].rolling(squeeze_window, min_periods=squeeze_window).rank(pct=True)
            out["bb_squeeze_flag"] = (pct_rank <= 0.10).astype("float32")
        else:
            out["bb_squeeze_flag"] = np.nan
        # MACD
        m = macd(df_tf["close"], 12, 26, 9)
        out["macd"] = m["macd"]
        out["macd_signal"] = m["macd_signal"]
        out["macd_hist"] = m["macd_hist"]
        # Stochastic
        st = stochastic(df_tf, 14, 3, 3)
        out["stoch_k"] = st["stoch_k"]
        out["stoch_d"] = st["stoch_d"]
        # CCI
        out["cci_20"] = cci(df_tf, 20)
        # Williams %R
        out["williams_r_14"] = williams_r(df_tf, 14)
        # Donchian
        dc = donchian(df_tf, 20)
        for c in ("donch_upper", "donch_lower", "donch_mid", "donch_width", "donch_pos"):
            out[c] = dc[c]
        # Keltner
        kc = keltner(df_tf, 20, 2.0)
        for c in ("kelt_mid", "kelt_upper", "kelt_lower", "kelt_width"):
            out[c] = kc[c]
        # SuperTrend
        sup = supertrend(df_tf, 10, 3.0)
        out["supertrend"] = sup["supertrend"]
        out["supertrend_dir"] = sup["supertrend_dir"]
        # Aroon
        ar = aroon(df_tf, 25)
        out["aroon_up"] = ar["aroon_up"]
        out["aroon_down"] = ar["aroon_down"]
        out["aroon_osc"] = ar["aroon_osc"]

    return out


def _tf_seconds(tf: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400}[tf]


# ── Pipeline stages ──────────────────────────────────────────────────────────

def read_1m_bucketed_to_5m(conn: duckdb.DuckDBPyConnection, start_unix: int, end_unix: int) -> pd.DataFrame:
    """Read 1m parquet between [start, end] UNIX seconds, bucket to 5m via DuckDB."""
    q = f"""
    SELECT
        time_bucket(INTERVAL '5 minutes', to_timestamp(timestamp_unix)) AS ts,
        first(mark_open  ORDER BY timestamp_unix) AS open,
        max(mark_high)                            AS high,
        min(mark_low)                             AS low,
        last(mark_close  ORDER BY timestamp_unix) AS close,
        sum(coalesce(ltp_volume, 0))              AS volume,
        last(oi_close    ORDER BY timestamp_unix) AS oi_close
    FROM read_parquet('{SPOT_1M_PATH}')
    WHERE timestamp_unix >= {int(start_unix)} AND timestamp_unix <= {int(end_unix)}
    GROUP BY ts
    ORDER BY ts ASC
    """
    df = conn.execute(q).df()
    # DuckDB to_timestamp() returns tz-aware UTC. Normalize to naive so it
    # aligns with the raw parquet's naive `timestamp_ist` column elsewhere.
    if pd.api.types.is_datetime64_any_dtype(df["ts"]) and df["ts"].dt.tz is not None:
        df["ts"] = df["ts"].dt.tz_convert(None)
    # Use total_seconds() for unit-agnostic conversion (handles both [ns] and
    # [us] datetime resolution that pandas may emit).
    df["timestamp_unix"] = (df["ts"] - pd.Timestamp("1970-01-01")).dt.total_seconds().astype("int64")
    df["timestamp_ist"] = df["ts"]
    df = df.set_index("ts")
    df.index.name = "ts"
    return df


def read_1m_bucketed_to_1m(conn: duckdb.DuckDBPyConnection, start_unix: int, end_unix: int) -> pd.DataFrame:
    """Read raw 1m bars (no bucketing) for the 1m-TF indicator pass."""
    q = f"""
    SELECT
        timestamp_unix,
        timestamp_ist,
        mark_open  AS open,
        mark_high  AS high,
        mark_low   AS low,
        mark_close AS close
    FROM read_parquet('{SPOT_1M_PATH}')
    WHERE timestamp_unix >= {int(start_unix)} AND timestamp_unix <= {int(end_unix)}
    ORDER BY timestamp_unix ASC
    """
    df = conn.execute(q).df()
    # Same normalization as above.
    if pd.api.types.is_datetime64_any_dtype(df["timestamp_ist"]) and df["timestamp_ist"].dt.tz is not None:
        df["timestamp_ist"] = df["timestamp_ist"].dt.tz_convert(None)
    df = df.set_index("timestamp_ist")
    df.index.name = "ts"
    return df


def compute_pipeline(df_5m: pd.DataFrame, df_1m: pd.DataFrame) -> pd.DataFrame:
    """Compute all enrichment columns and return a single wide DataFrame
    indexed on the 5m timestamp."""
    if df_5m.empty:
        return df_5m

    # Base 5m OHLC (renamed for indicator helpers)
    base = df_5m[["open", "high", "low", "close"]].copy()

    # ---------- per-TF indicators ----------
    # 5m TF (always-on): native, no resample
    cfg_5m = TFConfig("5m", None, skip_slow=False)
    out_5m = compute_tf_indicators(base, cfg_5m)
    enriched = base.join(out_5m.add_suffix("_5m"))

    # Coarser TFs
    for tf in ("15m", "30m", "1h", "4h", "1d"):
        rule = TIMEFRAMES_PANDAS[tf]
        df_tf = resample_ohlc(base, rule)
        out_tf = compute_tf_indicators(df_tf, TFConfig(tf, rule, skip_slow=False))
        enriched = merge_tf_indicators(enriched, out_tf, tf)

    # 1m TF — only Returns/ATR/RSI/ROC (skip_slow=True)
    if not df_1m.empty:
        out_1m = compute_tf_indicators(df_1m, TFConfig("1m", None, skip_slow=True))
        # Take the 1m indicator value at the END of each 5m bucket.
        # Use asof merge with direction='backward' so each 5m bar gets the
        # most recent 1m value at-or-before the bar's start (which is the
        # last 1m value of the previous 5m bucket; for end-of-bucket, shift
        # the lookup ts forward by 4 minutes so we capture the last 1m bar
        # within the same 5m bucket).
        target = enriched.copy()
        target_ts = target.index + pd.Timedelta(minutes=4)
        merged = pd.merge_asof(
            pd.DataFrame({"_lookup_ts": target_ts, "_orig_ts": target.index})
                .sort_values("_lookup_ts"),
            out_1m.add_suffix("_1m").reset_index().rename(columns={"ts": "_lookup_ts"})
                .sort_values("_lookup_ts"),
            on="_lookup_ts",
            direction="backward",
        )
        merged = merged.set_index("_orig_ts")
        merged.index.name = "ts"
        merged = merged.drop(columns=["_lookup_ts"])
        # Reattach to enriched, aligning by index
        for col in [c for c in merged.columns if c.endswith("_1m")]:
            enriched[col] = merged[col]

    # ---------- cross-TF metrics ----------
    n_24h = bars_5m(86400)
    n_7d = bars_5m(7 * 86400)
    n_14d = bars_5m(14 * 86400)
    n_30d = bars_5m(30 * 86400)

    enriched["rv_close_24h"] = rv_close(base["close"], n_24h)
    enriched["rv_parkinson_24h"] = rv_parkinson(base["high"], base["low"], n_24h)
    enriched["rv_gk_24h"] = rv_garman_klass(base["open"], base["high"], base["low"], base["close"], n_24h)
    enriched["rv_7d"] = rv_close(base["close"], n_7d)
    enriched["rv_14d"] = rv_close(base["close"], n_14d)
    enriched["rv_30d"] = rv_close(base["close"], n_30d)

    # RVP — percentile rank of trailing-window RV vs trailing 90 days of that-window RV
    rvp_window_5m_bars = 90 * BARS_PER_DAY_5M

    rv_30m = rv_close(base["close"], bars_5m(30 * 60))
    enriched["rvp_30m"] = rv_30m.rolling(rvp_window_5m_bars, min_periods=rvp_window_5m_bars).rank(pct=True) * 100.0
    rv_15m = rv_close(base["close"], bars_5m(15 * 60))
    enriched["rvp_15m"] = rv_15m.rolling(rvp_window_5m_bars, min_periods=rvp_window_5m_bars).rank(pct=True) * 100.0
    rv_1h_w = rv_close(base["close"], bars_5m(3600))
    enriched["rvp_1h"] = rv_1h_w.rolling(rvp_window_5m_bars, min_periods=rvp_window_5m_bars).rank(pct=True) * 100.0
    rv_4h_w = rv_close(base["close"], bars_5m(4 * 3600))
    enriched["rvp_4h"] = rv_4h_w.rolling(rvp_window_5m_bars, min_periods=rvp_window_5m_bars).rank(pct=True) * 100.0
    rv_1d_w = rv_close(base["close"], n_24h)
    enriched["rvp_1d"] = rv_1d_w.rolling(rvp_window_5m_bars, min_periods=rvp_window_5m_bars).rank(pct=True) * 100.0

    # ATR compression ratio — Wilder ATR(30, 4H) / Wilder ATR(180, 4H)
    df_4h = resample_ohlc(base, "4h")
    atr_30_4h = _wilder_ema(true_range(df_4h["high"], df_4h["low"], df_4h["close"]), 30)
    atr_180_4h = _wilder_ema(true_range(df_4h["high"], df_4h["low"], df_4h["close"]), 180)
    atr_comp = atr_30_4h / atr_180_4h.replace(0.0, np.nan)
    atr_comp.name = "atr_compression_ratio"
    enriched = pd.merge_asof(
        enriched.reset_index(),
        atr_comp.reset_index().rename(columns={"ts": "ts"}),
        on="ts",
        direction="backward",
    ).set_index("ts")

    # Daily MA distances (MA20/50/200 on daily close)
    df_1d = resample_ohlc(base, "1D")
    ma20 = _sma(df_1d["close"], 20)
    ma50 = _sma(df_1d["close"], 50)
    ma200 = _sma(df_1d["close"], 200)
    ma_df = pd.DataFrame({"ma20": ma20, "ma50": ma50, "ma200": ma200}).dropna(how="all")
    enriched = pd.merge_asof(
        enriched.reset_index(),
        ma_df.reset_index().rename(columns={"ts": "ts"}),
        on="ts",
        direction="backward",
    ).set_index("ts")
    enriched["dist_from_ma20_pct"]  = (enriched["close"] - enriched["ma20"])  / enriched["close"].replace(0.0, np.nan) * 100.0
    enriched["dist_from_ma50_pct"]  = (enriched["close"] - enriched["ma50"])  / enriched["close"].replace(0.0, np.nan) * 100.0
    enriched["dist_from_ma200_pct"] = (enriched["close"] - enriched["ma200"]) / enriched["close"].replace(0.0, np.nan) * 100.0

    # Time/calendar columns
    idx = enriched.index
    enriched["day_of_week"]   = idx.dayofweek.astype("int8")            # Mon=0..Sun=6
    enriched["hour_of_day_ist"] = idx.hour.astype("int8")
    enriched["is_weekend"]    = (idx.dayofweek >= 5).astype("int8")
    enriched["days_to_next_event"] = np.nan  # v2

    # Add longer-window 5m return: 7d (24h is already covered as spot_ret_1d)
    enriched["spot_ret_7d_5m_base"] = base["close"].pct_change(periods=n_7d)

    # Promote raw spot columns to canonical names + keep bookkeeping columns
    # (open/high/low/close already in enriched as the base)
    enriched["timestamp_unix"] = df_5m["timestamp_unix"].values
    if "volume" in df_5m.columns:
        enriched["volume"] = df_5m["volume"].values
    if "oi_close" in df_5m.columns:
        enriched["oi_close"] = df_5m["oi_close"].values

    return enriched


# ── I/O orchestration ────────────────────────────────────────────────────────

def get_existing_watermark(conn: duckdb.DuckDBPyConnection) -> int | None:
    """Return max(timestamp_unix) of existing output, or None if file missing."""
    if not os.path.exists(OUTPUT_PATH):
        return None
    try:
        res = conn.execute(
            f"SELECT max(timestamp_unix) FROM read_parquet('{OUTPUT_PATH}')"
        ).fetchone()
        return int(res[0]) if res and res[0] is not None else None
    except Exception as e:
        log.warning(f"Could not read watermark from existing parquet: {e}")
        return None


def get_source_max_unix(conn: duckdb.DuckDBPyConnection) -> int:
    res = conn.execute(
        f"SELECT max(timestamp_unix) FROM read_parquet('{SPOT_1M_PATH}')"
    ).fetchone()
    if not res or res[0] is None:
        raise RuntimeError(f"No data in {SPOT_1M_PATH}")
    return int(res[0])


def get_source_min_unix(conn: duckdb.DuckDBPyConnection) -> int:
    res = conn.execute(
        f"SELECT min(timestamp_unix) FROM read_parquet('{SPOT_1M_PATH}')"
    ).fetchone()
    return int(res[0])


def write_output(conn: duckdb.DuckDBPyConnection, new_df: pd.DataFrame, *, overwrite_from_unix: int | None) -> None:
    """Write `new_df` rows to OUTPUT_PATH; if file exists, drop rows with
    timestamp_unix >= overwrite_from_unix and append new_df."""
    os.makedirs(DERIVED_DIR, exist_ok=True)
    # Promote the 5m timestamp index → `timestamp_ist` column (IST-naive).
    new_df = new_df.reset_index().rename(columns={"ts": "timestamp_ist"})

    # Register new_df with DuckDB
    conn.register("_new_df", new_df)

    if os.path.exists(OUTPUT_PATH) and overwrite_from_unix is not None:
        # Build a combined relation: existing rows where ts < overwrite_from_unix
        # PLUS new rows. Write to a tmp file, atomic-rename.
        tmp_path = OUTPUT_PATH + ".tmp"
        conn.execute(f"""
            COPY (
                SELECT * FROM read_parquet('{OUTPUT_PATH}')
                WHERE timestamp_unix < {int(overwrite_from_unix)}
                UNION ALL BY NAME
                SELECT * FROM _new_df
                ORDER BY timestamp_unix
            ) TO '{tmp_path}' (FORMAT PARQUET, COMPRESSION SNAPPY)
        """)
        os.replace(tmp_path, OUTPUT_PATH)
    else:
        # Fresh write
        conn.execute(f"""
            COPY (SELECT * FROM _new_df ORDER BY timestamp_unix)
            TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION SNAPPY)
        """)
    conn.unregister("_new_df")


def run_pipeline(*, rebuild: bool = False, through_unix: int | None = None) -> None:
    t0 = _time.time()
    conn = duckdb.connect(database=":memory:")

    src_min = get_source_min_unix(conn)
    src_max = get_source_max_unix(conn)
    if through_unix is not None:
        src_max = min(src_max, int(through_unix))

    if rebuild:
        watermark = None
    else:
        watermark = get_existing_watermark(conn)

    # Choose [warmup_start, overlap_start] window
    if watermark is None:
        warmup_start = src_min
        overlap_start = src_min
        log.info(f"Full rebuild: data range {src_min} → {src_max}")
    else:
        overlap_start = max(src_min, watermark - 86400)            # last 1 day
        warmup_start = max(src_min, overlap_start - 35 * 86400)    # +35d warm-up
        log.info(f"Incremental: watermark={watermark}, overlap_start={overlap_start}, warmup_start={warmup_start}, src_max={src_max}")

    # Read 5m + 1m source
    log.info("Reading 1m → 5m bucketed source...")
    df_5m = read_1m_bucketed_to_5m(conn, warmup_start, src_max)
    log.info(f"5m bars: {len(df_5m):,}")
    if df_5m.empty:
        log.warning("No 5m data to process; aborting.")
        return

    log.info("Reading raw 1m source for 1m-TF indicators...")
    df_1m = read_1m_bucketed_to_1m(conn, warmup_start, src_max)
    log.info(f"1m bars: {len(df_1m):,}")

    log.info("Computing per-TF + cross-TF indicators...")
    enriched = compute_pipeline(df_5m, df_1m)
    log.info(f"Enriched columns: {len(enriched.columns)}")

    # Slice to rows ≥ overlap_start (drop warm-up)
    if watermark is not None:
        new_df = enriched[enriched["timestamp_unix"] >= overlap_start].copy()
    else:
        new_df = enriched
    log.info(f"Rows to write: {len(new_df):,}")

    if new_df.empty:
        log.warning("Nothing to write after warm-up slice; done.")
        return

    log.info(f"Writing → {OUTPUT_PATH}")
    write_output(conn, new_df, overwrite_from_unix=overlap_start if watermark is not None else None)

    out_size = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)
    elapsed = _time.time() - t0
    log.info(f"Done in {elapsed:.1f}s. Output {out_size:.1f} MB.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Spot enrichment pipeline (Module 1).")
    p.add_argument("--rebuild", action="store_true", help="Full overwrite from beginning of data.")
    p.add_argument("--through", type=str, default=None,
                   help="Cap end date (YYYY-MM-DD, IST midnight inclusive).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    through_unix = None
    if args.through:
        # Interpret as IST end-of-day (23:59:59 IST = 18:29:59 UTC)
        ts = pd.Timestamp(args.through + " 23:59:59")  # naive; treated as IST
        # convert IST → UTC: subtract 5h30m
        ts_utc = ts - pd.Timedelta(hours=5, minutes=30)
        through_unix = int(ts_utc.timestamp())
    try:
        run_pipeline(rebuild=args.rebuild, through_unix=through_unix)
        return 0
    except Exception as e:
        log.exception(f"Pipeline failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
