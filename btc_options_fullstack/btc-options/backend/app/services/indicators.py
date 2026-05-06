"""Technical indicator computation for the historical/backtest charts.

Public API: ``compute_indicators(df, configs)`` — takes an OHLCV DataFrame and
a list of indicator configs, returns a dict keyed by stable indicator IDs.

All indicators are hand-rolled in pandas/numpy (no pandas-ta) to keep the
dependency footprint minimal and avoid the known pandas-ta + numpy 2.x
incompatibility (numpy.NaN was removed). Each indicator is ~5 lines.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Stable indicator IDs ──────────────────────────────────────────────────────

def indicator_id(cfg: dict) -> str:
    """Build a stable string ID like 'rsi_14', 'ema_20', 'macd_12_26_9'."""
    t = cfg.get("type", "")
    p = cfg.get("params", {}) or {}
    if t in ("sma", "ema", "rsi", "atr"):
        return f"{t}_{int(p.get('period', 0))}"
    if t == "bbands":
        return f"bbands_{int(p.get('period', 20))}_{int(p.get('stdev', 2))}"
    if t == "macd":
        return f"macd_{int(p.get('fast', 12))}_{int(p.get('slow', 26))}_{int(p.get('signal', 9))}"
    if t == "vwap":
        return "vwap"
    return f"{t}_{p}"


# ── Per-indicator handlers ────────────────────────────────────────────────────

def _sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


def _ema(close: pd.Series, period: int) -> pd.Series:
    # Standard EMA (Wilder/regular). adjust=False for the recursive form.
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _macd(close: pd.Series, fast: int, slow: int, signal: int):
    fast_ema = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    slow_ema = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = fast_ema - slow_ema
    sig_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def _bbands(close: pd.Series, period: int, stdev: float):
    mid = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + stdev * std
    lower = mid - stdev * std
    return upper, mid, lower


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Wilder's ATR."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _vwap(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series) -> pd.Series:
    """Cumulative VWAP using typical-price × volume.

    NOTE: classic intraday VWAP resets each session; here we compute over the
    requested window (caller decides). For intraday use, request a single-day
    window. Returns NaN if volume is all-zero/missing.
    """
    if volume is None or volume.fillna(0).sum() == 0:
        return pd.Series(np.nan, index=close.index)
    tp = (high + low + close) / 3.0
    cum_pv = (tp * volume).cumsum()
    cum_v = volume.cumsum()
    return cum_pv / cum_v.replace(0.0, np.nan)


# ── Public entry point ────────────────────────────────────────────────────────

def compute_indicators(
    df: pd.DataFrame,
    configs: list[dict],
) -> dict[str, list[dict[str, Any]]]:
    """Compute requested indicators on ``df``.

    Args:
        df: must have columns ``time`` (unix sec int), ``close``; optionally
            ``open``, ``high``, ``low``, ``volume``. Sorted ascending by time.
        configs: e.g. [{"type": "rsi", "params": {"period": 14}}, ...].

    Returns:
        {indicator_id: [{time, ...values}, ...]} — one list of points per id.
        Single-value indicators yield ``{time, value}``; MACD yields
        ``{time, macd, signal, hist}``; BBands yields ``{time, upper, mid, lower}``.
        NaN bars (warm-up) are dropped from each indicator's series.
    """
    out: dict[str, list[dict]] = {}
    if df.empty or not configs:
        return out

    close = df["close"].astype(float)
    high  = df["high"].astype(float)  if "high"   in df.columns else close
    low   = df["low"].astype(float)   if "low"    in df.columns else close
    vol   = df["volume"].astype(float) if "volume" in df.columns else None
    times = df["time"].astype(int).values

    for cfg in configs:
        t = (cfg.get("type") or "").lower()
        p = cfg.get("params", {}) or {}
        try:
            iid = indicator_id(cfg)
            if t == "sma":
                series = _sma(close, int(p.get("period", 20)))
                out[iid] = _to_value_points(times, series)
            elif t == "ema":
                series = _ema(close, int(p.get("period", 20)))
                out[iid] = _to_value_points(times, series)
            elif t == "rsi":
                series = _rsi(close, int(p.get("period", 14)))
                out[iid] = _to_value_points(times, series)
            elif t == "atr":
                series = _atr(high, low, close, int(p.get("period", 14)))
                out[iid] = _to_value_points(times, series)
            elif t == "vwap":
                series = _vwap(close, high, low, vol) if vol is not None else pd.Series(np.nan, index=close.index)
                out[iid] = _to_value_points(times, series)
            elif t == "macd":
                m, s, h = _macd(close,
                                int(p.get("fast", 12)),
                                int(p.get("slow", 26)),
                                int(p.get("signal", 9)))
                out[iid] = _to_macd_points(times, m, s, h)
            elif t == "bbands":
                u, mid, lo = _bbands(close,
                                     int(p.get("period", 20)),
                                     float(p.get("stdev", 2)))
                out[iid] = _to_bbands_points(times, u, mid, lo)
            else:
                logger.warning(f"Unknown indicator type: {t}")
        except Exception as e:
            logger.warning(f"Indicator {cfg} failed: {e}")
    return out


# ── Helpers: Series → list of points (drop NaN warm-up bars) ──────────────────

def _to_value_points(times, s: pd.Series) -> list[dict]:
    arr = s.values
    return [
        {"time": int(times[i]), "value": float(arr[i])}
        for i in range(len(arr))
        if not np.isnan(arr[i])
    ]


def _to_macd_points(times, m: pd.Series, s: pd.Series, h: pd.Series) -> list[dict]:
    ma, sa, ha = m.values, s.values, h.values
    out = []
    for i in range(len(ma)):
        if np.isnan(ma[i]) and np.isnan(sa[i]) and np.isnan(ha[i]):
            continue
        out.append({
            "time": int(times[i]),
            "macd":   None if np.isnan(ma[i]) else float(ma[i]),
            "signal": None if np.isnan(sa[i]) else float(sa[i]),
            "hist":   None if np.isnan(ha[i]) else float(ha[i]),
        })
    return out


def _to_bbands_points(times, u: pd.Series, m: pd.Series, lo: pd.Series) -> list[dict]:
    ua, ma, la = u.values, m.values, lo.values
    out = []
    for i in range(len(ua)):
        if np.isnan(ua[i]) and np.isnan(ma[i]) and np.isnan(la[i]):
            continue
        out.append({
            "time":  int(times[i]),
            "upper": None if np.isnan(ua[i]) else float(ua[i]),
            "mid":   None if np.isnan(ma[i]) else float(ma[i]),
            "lower": None if np.isnan(la[i]) else float(la[i]),
        })
    return out
